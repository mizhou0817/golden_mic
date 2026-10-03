import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import vm from 'node:vm';
import { createHash } from 'node:crypto';
import ts from 'typescript';
import { inside, ROOT, tempRoot, unlinked, writeJSON, makeDirectory, parentPath } from '../e2e/modes/io.mjs';

// Execute the actual whole helper, substituting only its imported dependencies.
// No HTTP, host, browser, subprocess, media probe, model, env file or product data.
const source = fs.readFileSync(new URL('../e2e/v2/media.mjs', import.meta.url), 'utf8');
const support = fs.readFileSync(new URL('../e2e/v2/support.ts', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('support.ts', support, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const v2Class = parsed.statements.find(n => ts.isClassDeclaration(n) && n.name.text === 'V2Run');
const evidenceMethod = v2Class.members.find(n => n.name?.getText(parsed) === 'evidence');
const exportFunction = parsed.statements.find(n => ts.isFunctionDeclaration(n) && n.name.text === 'exportFile');
let diagnosticCallback;
function findCallback(node) {
  if (ts.isCallExpression(node) && node.arguments.length === 9
    && node.expression.getText(parsed).includes('rangeDownload')) diagnosticCallback = node.arguments[8].getText(parsed);
  ts.forEachChild(node, findCallback);
}
findCallback(exportFunction);
const compile = text => ts.transpileModule(text, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
} }).outputText;
const compiled = compile(source);
const MiB = 1024 ** 2;
const privateText = 'PRIVATE_SENTINEL_token=never_record_this';
const instance = 'synthetic-instance';
const contract = 'Exact bounded media Range contract failed';
const keys = ['stage', 'offset', 'status', 'identityMatches', 'rangeStart', 'rangeEnd', 'rangeTotal',
  'length', 'expectedLength', 'bodyLength', 'errorClass'];
const clean = value => JSON.parse(JSON.stringify(value));

function privateError(name = 'Error') {
  const error = new Error(privateText);
  error.name = name; error.stack = privateText; error.url = privateText; error.body = privateText;
  return error;
}

function fixture(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-range-diagnostics-'));
  const format = options.format ?? 'mp4';
  const target = path.join(directory, `download-${'a'.repeat(32)}.${format}`);
  const state = { gets: 0, bodies: 0, disposes: 0, closes: 0, writes: 0, probes: 0, bytesWritten: 0, requests: [], evidence: [] };
  const descriptors = new Set();
  t.after(() => {
    for (const fd of descriptors) fs.closeSync(fd);
    // Exact synthetic fixture tree only; never touch retained acceptance output.
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const fsMock = {
    openSync(file, flags) {
      assert.equal(file, target); assert.equal(flags, 'wx');
      if (options.openError) throw options.openError;
      const fd = fs.openSync(file, flags); descriptors.add(fd); return fd;
    },
    writeSync(fd, bytes, offset, length) {
      assert.ok(descriptors.has(fd)); state.writes++;
      if (options.writeError) throw options.writeError;
      if (options.noProgress) return 0;
      const count = fs.writeSync(fd, bytes, offset, Math.min(length, options.writeLimit ?? Infinity));
      state.bytesWritten += count; return count;
    },
    closeSync(fd) {
      assert.ok(descriptors.has(fd)); state.closes++;
      fs.closeSync(fd); descriptors.delete(fd);
      if (options.closeError) throw options.closeError;
    },
    readFileSync(file, encoding) {
      assert.equal(file, target); assert.equal(encoding, 'utf8');
      if (options.readError) throw options.readError;
      return fs.readFileSync(file, encoding);
    },
  };
  const module = { exports: {} };
  const dependencies = {
    'node:fs': fsMock, 'node:path': path, 'node:crypto': { createHash },
    '../modes/io.mjs': { inside, ROOT, tempRoot,
      unlinked(file) { assert.equal(file, directory); if (options.linkError) throw options.linkError; return unlinked(file); },
      probeMedia(file, tool) {
        assert.equal(file, target); assert.equal(tool, 'synthetic-ffprobe-never-executed');
        assert.equal(descriptors.size, 0); state.probes++;
        if (options.probeError) throw options.probeError;
        return { fixture: true }; // Explicit test double, never a real-media acceptance claim.
      },
    },
  };
  vm.runInNewContext(compiled, { module, exports: module.exports, Buffer,
    require(name) { assert.ok(Object.hasOwn(dependencies, name)); return dependencies[name]; },
  }, { timeout: 1000 });
  const total = options.total ?? 64;
  const api = { async get(url, request) {
    state.gets++; assert.equal(url, `https://offline.invalid/file?${privateText}`);
    assert.equal(request.maxRedirects, 0); assert.equal(request.maxRetries, 0);
    assert.equal(request.headers['X-Task-Token'], privateText);
    const range = /^bytes=(\d+)-(\d+)$/.exec(request.headers.Range); assert.ok(range);
    const start = Number(range[1]), end = Number(range[2]);
    assert.ok(end - start + 1 <= MiB);
    state.requests.push([start, end]);
    if (options.transportError && state.gets === (options.failAt ?? 1)) throw options.transportError;
    const responseTotal = options.changedTotal && state.gets === 2 ? total + 1 : total;
    const actualEnd = Math.min(end, responseTotal - 1);
    const headers = {
      'content-range': `bytes ${start}-${actualEnd}/${responseTotal}`,
      'content-length': String(actualEnd - start + 1), 'x-v2-acceptance': instance,
      'set-cookie': privateText, location: privateText, ...options.headers,
    };
    return {
      status() { return options.status ?? 206; },
      headers() { if (options.metadataError) throw options.metadataError; return headers; },
      async body() {
        state.bodies++;
        if (options.bodyError) throw options.bodyError;
        const bytes = Buffer.alloc(options.bodyLength ?? actualEnd - start + 1, 0x5a);
        if (!start && bytes.length >= 8) {
          if (options.content !== undefined) Buffer.from(options.content).copy(bytes);
          else if (format === 'mp4') bytes.write('ftyp', 4, 'ascii');
          else if (format === 'png') Buffer.from('89504e470d0a1a0a', 'hex').copy(bytes);
          else if (format === 'gif') bytes.write('GIF89a', 0, 'ascii');
          else if (format === 'srt') bytes.write('1\n00:00:00,000 --> 00:00:01,000\nsynthetic\n', 0, 'ascii');
        }
        return bytes;
      },
      async dispose() { state.disposes++; if (options.disposeError) throw options.disposeError; },
    };
  } };
  const invoke = callback => module.exports.rangeDownload(api, `https://offline.invalid/file?${privateText}`,
    { 'X-Task-Token': privateText }, options.target ?? target, 'synthetic-ffprobe-never-executed', instance,
    format, options.duration ?? 60, callback);
  return { target, directory, state, descriptors, invoke,
    run: () => invoke(facts => state.evidence.push(clean(facts))) };
}

async function fails(f, stage, { message, error, bytes = 0, gets = 1, disposes = gets, closes = 1, bodies = 0 } = {}) {
  let caught;
  try { await f.run(); } catch (value) { caught = value; }
  assert.ok(caught, 'must fail, not recover or retry');
  if (error) assert.equal(caught, error, 'same error object');
  if (message) assert.equal(caught.message, message);
  assert.equal(f.state.evidence.length, 1);
  const facts = f.state.evidence[0];
  assert.deepEqual(Object.keys(facts), keys); assert.equal(facts.stage, stage);
  assert.equal(JSON.stringify(facts).includes(privateText), false);
  assert.ok(Object.values(facts).every(v => v === null || typeof v === 'boolean' || typeof v === 'string'
    || typeof v === 'number' && Number.isSafeInteger(v)));
  assert.equal(f.state.gets, gets); assert.equal(f.state.disposes, disposes);
  assert.equal(f.state.closes, closes); assert.equal(f.state.bodies, bodies);
  assert.equal(f.descriptors.size, 0);
  assert.equal(fs.existsSync(f.target) ? fs.statSync(f.target).size : 0, bytes);
  return facts;
}

test('request transport failure retains empty file, closes fd, never retries and records only static error class', async t => {
  const error = privateError('TimeoutError'); const f = fixture(t, { transportError: error });
  const facts = await fails(f, 'request-transport', { error, disposes: 0 });
  assert.equal(fs.existsSync(f.target), true);
  assert.deepEqual(facts, { stage: 'request-transport', offset: 0, status: null, identityMatches: null,
    rangeStart: null, rangeEnd: null, rangeTotal: null, length: null, expectedLength: null,
    bodyLength: null, errorClass: 'TimeoutError' });
});

for (const [label, options, stage] of [
  ['status 200', { status: 200 }, 'response-206'],
  ['redirect', { status: 302, headers: { location: privateText } }, 'response-206'],
  ['unauthorized', { status: 403 }, 'response-206'],
  ['marker mismatch', { headers: { 'x-v2-acceptance': privateText } }, 'response-marker'],
  ['marker absent', { headers: { 'x-v2-acceptance': undefined } }, 'response-marker'],
  ['range missing', { headers: { 'content-range': undefined } }, 'content-range-parse'],
  ['range secret', { headers: { 'content-range': privateText } }, 'content-range-parse'],
  ['range wildcard', { headers: { 'content-range': 'bytes 0-63/*' } }, 'content-range-parse'],
  ['range suffix', { headers: { 'content-range': 'bytes 0-63/64 extra' } }, 'content-range-parse'],
  ['range start', { headers: { 'content-range': 'bytes 1-63/64' } }, 'content-range-bounds'],
  ['range end', { headers: { 'content-range': 'bytes 0-62/64' } }, 'content-range-bounds'],
  ['zero total retains original bounds precedence', { headers: { 'content-range': 'bytes 0-0/0' } }, 'content-range-bounds'],
  ['over 1MiB chunk', { total: 2 * MiB, headers: { 'content-range': `bytes 0-${MiB}/${2 * MiB}` } }, 'content-range-bounds'],
  ['over 128MiB total', { duration: 3600, total: 128 * MiB + 1 }, 'size-budget'],
  ['unsafe integer total', { headers: { 'content-range': 'bytes 0-1048575/9007199254740992' } }, 'size-budget'],
  ['infinite parsed total', { headers: { 'content-range': `bytes 0-1048575/${'9'.repeat(400)}` } }, 'size-budget'],
]) test(`closed Range gate: ${label}`, async t => {
  const f = fixture(t, options); const facts = await fails(f, stage, { message: contract });
  assert.equal(facts.status, options.status ?? 206);
  assert.equal(facts.identityMatches, !label.startsWith('marker'));
  if (label === 'range start') assert.deepEqual([facts.rangeStart, facts.rangeEnd, facts.rangeTotal], [1, 63, 64]);
  if (label.includes('integer') || label.includes('infinite')) assert.equal(facts.rangeTotal, null);
});

for (const [format, duration, cap] of [
  ['mp4', 1.25, Math.ceil(1.25 * 4192 * 125 * 1.25) + 4 * MiB],
  ['mp3', 1.25, Math.ceil(1.25 * 192 * 125 * 1.1) + MiB],
  ['gif', 60, 64 * MiB], ['png', 60, 16 * MiB], ['srt', 60, 256 * 1024],
]) test(`${format} independent product cap rejects cap+1 before reading body`, async t => {
  const f = fixture(t, { format, duration, total: cap + 1 });
  const facts = await fails(f, 'size-budget', { message: contract }); assert.equal(facts.rangeTotal, cap + 1);
});

for (const length of [undefined, 'bad-private', '63', '65', String(8 * MiB)]) {
  test(`content length gate ${length === undefined ? 'absent' : length}`, async t => {
    const f = fixture(t, { headers: { 'content-length': length } });
    const facts = await fails(f, 'content-length', { message: 'Range length rejected' });
    assert.equal(facts.expectedLength, 64); assert.equal(facts.length, Number.isSafeInteger(Number(length)) ? Number(length) : null);
  });
}

test('body read transport error disposes response and closes retained zero-byte file', async t => {
  const error = privateError('AbortError');
  await fails(fixture(t, { bodyError: error }), 'body-read', { error, bodies: 1 });
});
for (const length of [0, 63, 65]) test(`payload mismatch ${length} does not write`, async t => {
  const facts = await fails(fixture(t, { bodyLength: length }), 'body-length', { message: 'Range payload mismatch', bodies: 1 });
  assert.equal(facts.bodyLength, length);
});

test('changed total fails on second response, retaining exactly one chunk', async t => {
  const facts = await fails(fixture(t, { total: MiB + 64, changedTotal: true }), 'content-range-total',
    { message: contract, bytes: MiB, gets: 2, bodies: 1 });
  assert.equal(facts.offset, MiB); assert.equal(facts.rangeTotal, MiB + 65);
});
test('second request transport failure clears previous response facts and retains first chunk', async t => {
  const error = privateError();
  const facts = await fails(fixture(t, { total: MiB + 64, failAt: 2, transportError: error }), 'request-transport',
    { error, bytes: MiB, gets: 2, disposes: 1, bodies: 1 });
  assert.equal(facts.offset, MiB);
  for (const key of keys.filter(k => !['stage', 'offset', 'errorClass'].includes(k))) assert.equal(facts[key], null);
});

for (const [option, stage, bodies, bytes] of [
  ['metadataError', 'response-metadata', 0, 0], ['writeError', 'file-write', 1, 0],
  ['disposeError', 'response-dispose', 1, 64], ['closeError', 'file-close', 1, 64], ['probeError', 'probe', 1, 64],
]) test(`${stage} preserves original error object and cleanup`, async t => {
  const error = privateError();
  await fails(fixture(t, { [option]: error }), stage, { error, bodies, bytes });
});
test('write with no progress is not retried', async t => {
  const f = fixture(t, { noProgress: true });
  await fails(f, 'file-write', { message: 'Export write made no progress', bodies: 1 }); assert.equal(f.state.writes, 1);
});
test('dispose error retains original finally precedence over status failure', async t => {
  const error = privateError();
  await fails(fixture(t, { status: 403, disposeError: error }), 'response-dispose', { error });
});
test('close error retains original finally precedence over response failure', async t => {
  const error = privateError();
  await fails(fixture(t, { status: 403, closeError: error }), 'file-close', { error });
});

for (const duration of [NaN, Infinity, -1, 3600.1, '60']) test(`invalid duration ${String(duration)} has no IO`, async t => {
  await fails(fixture(t, { duration }), 'duration', { message: 'Export duration budget', gets: 0, closes: 0 });
});
test('out of TEMP target refused without IO', async t => {
  await fails(fixture(t, { target: path.join(ROOT, `download-${'a'.repeat(32)}.mp4`) }), 'target',
    { message: 'Owned TEMP export target required', gets: 0, closes: 0 });
});
for (const [option, stage] of [['linkError', 'target-unlinked'], ['openError', 'file-open']]) {
  test(`${stage} fails before requests`, async t => {
    const error = privateError(); await fails(fixture(t, { [option]: error }), stage, { error, gets: 0, closes: 0 });
  });
}
test('exclusive existing target stays byte-identical', async t => {
  const f = fixture(t); fs.writeFileSync(f.target, 'existing', { flag: 'wx' });
  await fails(f, 'file-open', { gets: 0, closes: 0, bytes: 8 }); assert.equal(fs.readFileSync(f.target, 'utf8'), 'existing');
});
for (const format of ['mp4', 'gif', 'png']) test(`${format} bad signature stays separate from probe`, async t => {
  const f = fixture(t, { format, content: 'Z'.repeat(64) });
  await fails(f, 'signature', { message: 'Export signature mismatch', bytes: 64, bodies: 1 }); assert.equal(f.state.probes, 0);
});
test('SRT read failure is separate from cue validation', async t => {
  const error = privateError();
  await fails(fixture(t, { format: 'srt', readError: error }), 'file-read', { error, bytes: 64, bodies: 1 });
});
test('SRT cue failure never probes or removes file', async t => {
  const f = fixture(t, { format: 'srt', content: 'Z'.repeat(64) });
  await fails(f, 'srt-cues', { message: 'Real SRT cues required', bytes: 64, bodies: 1 }); assert.equal(f.state.probes, 0);
});

for (const mode of ['throws', 'rejects', 'unknown-class', 'hostile-name', 'absent']) test(`diagnostic sink ${mode} cannot mask original failure`, async t => {
  const error = privateError(mode === 'unknown-class' ? privateText : 'Error');
  if (mode === 'hostile-name') Object.defineProperty(error, 'name', { get() { throw privateError(); } });
  const f = fixture(t, { bodyError: error }); let calls = 0;
  const callback = mode === 'absent' ? undefined : facts => {
    calls++; assert.equal(JSON.stringify(facts).includes(privateText), false);
    if (mode === 'unknown-class') assert.equal(facts.errorClass, 'other');
    if (mode === 'throws') throw privateError();
    if (mode === 'rejects') return Promise.reject(privateError());
  };
  let caught; try { await f.invoke(callback); } catch (value) { caught = value; }
  assert.equal(caught, error); assert.equal(calls, ['absent', 'hostile-name'].includes(mode) ? 0 : 1);
  assert.equal(f.state.disposes, 1); assert.equal(f.state.closes, 1); assert.equal(f.state.gets, 1);
  assert.equal(fs.statSync(f.target).size, 0);
});

test('actual exportFile callback persists actual V2Run evidence projection; exclusive evidence failure cannot mask rejection', async t => {
  assert.ok(diagnosticCallback, 'actual exportFile must pass its ninth argument');
  const error = privateError('TypeError'), f = fixture(t, { bodyError: error });
  const module = { exports: {} };
  vm.runInNewContext(compile(`export class EvidenceOnly { ${evidenceMethod.getText(parsed)} }`), {
    module, exports: module.exports, makeDirectory, parentPath, writeJSON,
    invariant(condition) { assert.ok(condition); },
  }, { timeout: 1000 });
  const m = new module.exports.EvidenceOnly();
  m.info = { outputPath(name) { assert.equal(name, 'download-mp4-failure.json'); return path.join(f.directory, name); } };
  const callback = vm.runInNewContext(`(${diagnosticCallback})`, { m, format: 'mp4' }, { timeout: 1000 });
  let caught; try { await f.invoke(callback); } catch (value) { caught = value; }
  assert.equal(caught, error);
  const evidence = path.join(f.directory, 'download-mp4-failure.json');
  const before = fs.readFileSync(evidence, 'utf8'), facts = JSON.parse(before);
  assert.deepEqual(Object.keys(facts), keys); assert.equal(facts.stage, 'body-read');
  assert.equal(facts.errorClass, 'TypeError'); assert.equal(before.includes(privateText), false);
  assert.equal(fs.statSync(f.target).size, 0); assert.equal(f.state.disposes, 1); assert.equal(f.state.closes, 1);
  // Separate invocation, not an automatic retry: preexisting download AND evidence
  // both reject exclusive writes. The file-open error must win over sink EEXIST.
  try { await f.invoke(callback); } catch (value) { caught = value; }
  assert.equal(caught.code, 'EEXIST'); assert.equal(fs.readFileSync(evidence, 'utf8'), before);
  assert.equal(f.state.gets, 1);
});

for (const format of ['mp4', 'gif', 'png', 'mp3', 'srt']) test(`${format} success keeps result and emits no diagnostics`, async t => {
  const f = fixture(t, { format, writeLimit: 7 }); const result = clean(await f.run());
  assert.deepEqual(result, { bytes: 64, sha256: createHash('sha256').update(fs.readFileSync(f.target)).digest('hex'),
    chunks: 1, format, media: format === 'srt' ? null : { fixture: true } });
  assert.equal(f.state.evidence.length, 0); assert.equal(f.state.gets, 1); assert.equal(f.state.disposes, 1);
  assert.equal(f.state.closes, 1); assert.equal(f.state.writes, 10); assert.equal(f.descriptors.size, 0);
});

test('Q-sized 31MiB-class MP4 succeeds identically with/without callback: bounded chunks, exact EOF, hash and cleanup', async t => {
  // Synthetic data with Q-class size/duration, NOT a replay of Q media or HTTP.
  const total = 31_589_023, duration = 59.566667;
  const a = fixture(t, { total, duration }), b = fixture(t, { total, duration });
  const actual = clean(await a.run()), without = clean(await b.invoke());
  const expectedHash = createHash('sha256');
  for (let offset = 0; offset < total; offset += MiB) {
    const bytes = Buffer.alloc(Math.min(MiB, total - offset), 0x5a);
    if (!offset) bytes.write('ftyp', 4, 'ascii'); expectedHash.update(bytes);
  }
  assert.deepEqual(actual, { bytes: total, sha256: expectedHash.digest('hex'), chunks: 31, format: 'mp4', media: { fixture: true } });
  assert.deepEqual(actual, without); assert.deepEqual(a.state.requests, b.state.requests);
  for (const f of [a, b]) {
    assert.equal(f.state.evidence.length, 0); assert.equal(f.state.gets, 31); assert.equal(f.state.disposes, 31);
    assert.equal(f.state.bodies, 31); assert.equal(f.state.closes, 1); assert.equal(f.state.probes, 1);
    assert.equal(fs.statSync(f.target).size, total); assert.equal(f.descriptors.size, 0);
    assert.deepEqual(f.state.requests.at(-1), [30 * MiB, total - 1]);
  }
});