import assert from 'node:assert/strict';
import nodeTest from 'node:test';
import { request } from 'node:http';
import { connect } from 'node:net';
import { realpathSync, mkdtempSync, writeFileSync, readFileSync, unlinkSync, linkSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validateOrigin, matchesDownload, downloadGuard, startDownloadTransport, responseHeaders, cleanupDownload, inspectOwnedFile, digest } from './final-download-fixture.mjs';

const direct = !!process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
// Each execution gets a fresh safe receipt; no raw assertion/HTTP error escapes.
const evidence = direct ? mkdtempSync(join(realpathSync(tmpdir()), 'gm-final-download-units-')) : null;
const outcomes = [];
const test = (name, fn) => { if (direct) nodeTest(name, async () => {
  let passed = false;
  try { await fn(); passed = true; } catch { throw Error('fixture-unit-failed-raw-error-suppressed'); }
  finally { outcomes.push({ stage: name, passed }); }
}); };
if (direct) nodeTest.after(() => {
  const hashes = Object.fromEntries(['final-download-fixture.mjs', 'test-final-download-fixture.mjs'].map(file =>
    [file, digest(readFileSync(new URL(file, import.meta.url)))]));
  writeFileSync(join(evidence, 'receipt.json'), JSON.stringify({ outcomes, hashes, rawArtifacts: false }, null, 2), { flag: 'wx' });
  console.log(JSON.stringify({ evidence, passed: outcomes.filter(x => x.passed).length, failed: outcomes.filter(x => !x.passed).length }));
});
const binding = { origin: 'http://127.0.0.1:49152', task: 'final-fixture-a', job: 'e'.repeat(32), token: 'a'.repeat(43), revision: 2 };
const target = b => `${b.origin}/api/tasks/${b.task}/exports/${b.job}/file?token=${b.token}&revision=${b.revision}`;
const valid = b => ({ url: target(b), method: 'GET', rawHeaders: ['Host', new URL(b.origin).host] });
const bytes = Buffer.from('1\r\n00:00:00,000 --> 00:00:01,000\r\nSynthetic.\r\n\r\n');

test('canonical supplied loopback origin only', () => {
  assert.equal(validateOrigin(binding.origin), true);
  for (const value of [undefined, '', 'https://example.com', 'http://localhost:49152', 'http://127.0.0.1:800',
    binding.origin + '/', binding.origin + '/else', binding.origin + '?x', binding.origin + '#x', 'http://x@127.0.0.1:49152', 'http://127.1:49152'])
    assert.equal(validateOrigin(value), false);
  assert.equal(matchesDownload(valid(binding), undefined), false);
});
test('exact method authority Host and raw target binding', () => {
  const good = valid(binding); assert.equal(matchesDownload(good, binding), true);
  const bad = [
    ...['POST', 'HEAD', 'CONNECT', 'OPTIONS'].map(method => ({ ...good, method })),
    ...['http://127.0.0.1:8000', 'http://else.invalid', 'https://127.0.0.1:49152', 'http://x@127.0.0.1:49152'].map(origin => ({ ...good, url: target({ ...binding, origin }) })),
    ...[[], ['Host', 'else.invalid'], ['Host', '127.0.0.1:8000'], [...good.rawHeaders, 'host', new URL(binding.origin).host]].map(rawHeaders => ({ ...good, rawHeaders })),
    { ...good, url: good.url.replace(binding.task, 'final-fixture-b') }, { ...good, url: good.url.replace(binding.job, 'f'.repeat(32)) },
    { ...good, url: good.url.replace('/file?', '/other?') }, { ...good, url: good.url.replace('/file?', '/%66ile?') },
    { ...good, url: good.url.replace('/file?', '/x/../file?') }, { ...good, url: good.url + '#fragment' },
    { ...good, url: new URL(good.url).pathname + new URL(good.url).search },
  ];
  for (const value of bad) assert.equal(matchesDownload(value, binding), false);
});
test('token revision extra query and range remain strict', () => {
  const good = valid(binding);
  for (const url of [good.url.replace(binding.token, 'b'.repeat(43)), good.url.replace('&revision=2', ''),
    good.url.replace('revision=2', 'revision=3'), good.url.replace('revision=2', 'revision=02'),
    good.url + '&token=' + binding.token, good.url + '&revision=2', good.url + '&extra=1', good.url.replace('token=', 'other=')])
    assert.equal(matchesDownload({ ...good, url }, binding), false);
  for (const value of ['bytes=0-', 'bytes=0-4', 'bytes=1-'])
    assert.equal(matchesDownload({ ...good, rawHeaders: [...good.rawHeaders, 'Range', value] }, binding), false);
  assert.equal(matchesDownload(good, { ...binding, revision: 3 }), false);
});
test('unknown sensitive duplicate malformed and oversized headers denied', () => {
  const good = valid(binding);
  for (const [key, value] of [['Cookie', 'no'], ['Authorization', 'no'], ['X-Task-Token', binding.token], ['X-Csrf-Token', 'no'],
    ['Proxy-Authorization', 'no'], ['Forwarded', 'no'], ['X-Forwarded-Host', 'no'], ['Content-Length', '0'], ['Transfer-Encoding', 'chunked'],
    ['Location', '/redirect'], ['Upgrade', 'websocket'], ['X-Unknown', 'no'], ['Origin', 'http://else.invalid'],
    ['Referer', binding.origin + '/tasks/other'], ['Connection', 'upgrade'], ['User-Agent', 'a'.repeat(1025)], ['Accept', 'bad\r\nheader']])
    assert.equal(matchesDownload({ ...good, rawHeaders: [...good.rawHeaders, key, value] }, binding), false);
  for (const rawHeaders of [['Host'], [...good.rawHeaders, 'Accept', '*/*', 'accept', '*/*'], [...good.rawHeaders, 'bad key', 'value']])
    assert.equal(matchesDownload({ ...good, rawHeaders }, binding), false);
  assert.equal(matchesDownload({ ...good, rawHeaders: [...good.rawHeaders, 'Referer', binding.origin + '/tasks/' + binding.task, 'Origin', binding.origin] }, binding), true);
});
test('one shot arm requires export receipt current revision and no replenishment', () => {
  const guard = downloadGuard(binding), good = valid(binding);
  assert.equal(guard.accept(good), false);
  for (const receipt of [{ posts: 0, revision: 2 }, { posts: 2, revision: 2 }, { posts: 1, revision: 3 }]) assert.throws(() => guard.arm(receipt));
  guard.arm({ posts: 1, revision: 2 });
  assert.equal(guard.accept({ ...good, method: 'POST' }), false);
  assert.equal(guard.accept(good), true); assert.equal(guard.accept(good), false); assert.equal(guard.gets, 1);
  assert.throws(() => guard.arm({ posts: 1, revision: 2 }));
});
test('response headers explicit safe projection and 4KiB cap', () => {
  assert.equal(responseHeaders(bytes)['content-length'], String(bytes.length));
  assert.equal(responseHeaders(Buffer.alloc(4096))['content-length'], '4096');
  assert.throws(() => responseHeaders(Buffer.alloc(4097))); assert.throws(() => responseHeaders(Buffer.alloc(0)));
  assert.equal(Object.keys(responseHeaders(bytes)).length, 7);
  assert.equal(responseHeaders(bytes).location, undefined);
});

async function get(transport, url, options = {}) {
  const u = new URL(transport.origin);
  return await new Promise((ok, reject) => {
    // The test client connects ONLY to its owned loopback proxy, never URL host.
    const req = request({ hostname: '127.0.0.1', port: u.port, path: url, method: options.method ?? 'GET',
      headers: { Host: u.host, ...options.headers }, agent: false }, res => {
      const chunks = []; res.on('data', chunk => chunks.push(chunk));
      res.on('end', () => ok({ status: res.statusCode, headers: res.headers, bytes: Buffer.concat(chunks) })); res.on('error', reject);
    }); req.setTimeout(2000, () => req.destroy(Error('owned-client-timeout'))); req.on('error', reject); req.end();
  });
}
async function raw(transport, line) {
  return await new Promise((ok, reject) => {
    const socket = connect({ host: '127.0.0.1', port: new URL(transport.origin).port }); let data = '';
    socket.setTimeout(2000, () => socket.destroy(Error('owned-socket-timeout')));
    socket.on('connect', () => socket.write(line)); socket.on('data', chunk => { data += chunk; });
    socket.on('end', () => ok(data)); socket.on('error', reject);
  });
}
test('real rejecting transport never redirects CONNECT upgrades or forwards and closes sockets', async () => {
  const transport = await startDownloadTransport({ ...binding, bytes });
  const bound = { ...binding, origin: transport.origin };
  try {
    assert.equal((await get(transport, target(bound))).status, 403);
    for (const url of ['http://upstream.invalid/redirect', transport.origin + '/redirect', 'http://127.0.0.1:8000/private']) {
      const response = await get(transport, url); assert.equal(response.status, 403); assert.equal(response.headers.location, undefined); assert.equal(response.bytes.length, 0);
    }
    assert.match(await raw(transport, 'CONNECT upstream.invalid:443 HTTP/1.1\r\nHost: upstream.invalid:443\r\n\r\n'), /^HTTP\/1.1 403/);
    assert.match(await raw(transport, 'GET / HTTP/1.1\r\nHost: invalid\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n'), /^HTTP\/1.1 403/);
    transport.arm({ posts: 1, revision: 2 });
    assert.equal((await get(transport, target(bound), { headers: { Range: 'bytes=0-' } })).status, 403);
    const response = await get(transport, target(bound));
    assert.equal(response.status, 200); assert.deepEqual(response.bytes, bytes);
    assert.deepEqual(response.headers, responseHeaders(bytes));
    assert.deepEqual(transport.observation, { status: 200, headers: responseHeaders(bytes), browser_header_observation: false, observation: 'transport-response' });
    assert.equal((await get(transport, target(bound))).status, 403);
    assert.deepEqual(transport.counters, { gets: 1, denied: 6, connectDenied: 1, upgradeDenied: 1, clientErrors: 0, outboundConnections: 0 });
  } finally { await transport.close(); }
  assert.deepEqual(transport.cleanup, { closed: true, listening: false, sockets: 0 });
});
test('owned regular file exact size bytes hash removal and hardlink denial', async () => {
  const dir = join(evidence, 'owned-files'); mkdirSync(dir);
  const file = join(dir, 'synthetic.srt'), alias = join(dir, 'alias.srt'); writeFileSync(file, bytes, { flag: 'wx' });
  try {
    assert.deepEqual(inspectOwnedFile(file, dir, bytes), { bytes: bytes.length, sha256: digest(bytes) });
    assert.throws(() => inspectOwnedFile(file, dir, Buffer.from('wrong')));
    assert.throws(() => inspectOwnedFile(file, realpathSync(tmpdir()), bytes));
    linkSync(file, alias); assert.throws(() => inspectOwnedFile(file, dir, bytes)); unlinkSync(alias);
    assert.deepEqual(await cleanupDownload({ failure: async () => null, path: async () => file, delete: async () => unlinkSync(file) }), { kind: 'success', deleteRejected: false });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
test('canceled delete rejection is distinct from context cleanup and success failures propagate', async () => {
  let removed = 0;
  assert.deepEqual(await cleanupDownload({ failure: async () => 'canceled', delete: async () => { removed++; throw Error('canceled'); } }), { kind: 'failed-download', deleteRejected: true });
  assert.equal(removed, 1);
  assert.deepEqual(await cleanupDownload(null), { kind: 'none', deleteRejected: false });
  await assert.rejects(cleanupDownload({ failure: async () => null, path: async () => null, delete: async () => { throw Error('unexpected'); } }));
});