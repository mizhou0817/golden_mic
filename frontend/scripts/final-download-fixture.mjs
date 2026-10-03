import { createServer } from 'node:http';
import { lstatSync, readFileSync, existsSync, realpathSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { createHash } from 'node:crypto';

const check = (value, kind) => { if (!value) throw Error(kind); };
export const digest = bytes => createHash('sha256').update(bytes).digest('hex');

// Only a caller-supplied canonical ephemeral loopback authority is eligible.
// This is NOT an authorization rule for product requests or arbitrary origins.
export function validateOrigin(origin) {
  try {
    const u = new URL(origin);
    return u.protocol === 'http:' && u.hostname === '127.0.0.1' && Number(u.port) >= 1024
      && Number(u.port) <= 65535 && u.origin === origin && !u.username && !u.password && !u.search && !u.hash;
  } catch { return false; }
}

export function headerMap(raw) {
  if (!Array.isArray(raw) || raw.length % 2 || raw.length > 64) return null;
  const h = Object.create(null);
  for (let i = 0; i < raw.length; i += 2) {
    if (typeof raw[i] !== 'string' || typeof raw[i + 1] !== 'string') return null;
    const key = raw[i].toLowerCase(), value = raw[i + 1];
    if (!/^[a-z0-9-]+$/.test(key) || Object.hasOwn(h, key) || value.length > 1024 || /[^\x20-\x7e]/.test(value)) return null;
    h[key] = value;
  }
  return h;
}

const browserHeaders = new Set(['host', 'connection', 'proxy-connection', 'user-agent', 'accept', 'accept-encoding',
  'accept-language', 'referer', 'origin', 'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
  'sec-fetch-dest', 'sec-fetch-mode', 'sec-fetch-site', 'sec-fetch-user', 'upgrade-insecure-requests', 'priority']);

export function matchesDownload({ url, method, rawHeaders }, binding) {
  try {
    if (!binding || !validateOrigin(binding.origin) || !/^[a-z0-9-]{1,64}$/.test(binding.task)
      || !/^[a-f0-9]{32}$/.test(binding.job) || !/^[A-Za-z0-9_-]{43}$/.test(binding.token) || binding.revision !== 2) return false;
    const h = headerMap(rawHeaders), u = new URL(url);
    if (!h || method !== 'GET' || h.host !== new URL(binding.origin).host || Object.keys(h).some(k => !browserHeaders.has(k))) return false;
    if (h.origin && h.origin !== binding.origin || h.referer && h.referer !== `${binding.origin}/tasks/${binding.task}`) return false;
    if (['connection', 'proxy-connection'].some(k => h[k] && !['close', 'keep-alive'].includes(h[k].toLowerCase()))) return false;
    const target = `${binding.origin}/api/tasks/${binding.task}/exports/${binding.job}/file?token=${binding.token}&revision=2`;
    // Exact raw spelling also rejects normalization, userinfo, fragments,
    // duplicated parameters, encoded paths, extra query and alternate authority.
    return url === target && u.origin === binding.origin;
  } catch { return false; }
}

export function downloadGuard(binding) {
  check(matchesDownload({ url: `${binding.origin}/api/tasks/${binding.task}/exports/${binding.job}/file?token=${binding.token}&revision=2`,
    method: 'GET', rawHeaders: ['Host', new URL(binding.origin).host] }, binding), 'binding-invalid');
  let armed = false, armedOnce = false, gets = 0;
  return {
    arm({ posts, revision }) { check(!armedOnce && posts === 1 && revision === binding.revision, 'arm-budget'); armedOnce = true; armed = true; },
    accept(request) {
      if (!matchesDownload(request, binding) || !armed || gets !== 0) return false;
      armed = false; gets++; return true;
    },
    get gets() { return gets; },
  };
}

export function responseHeaders(bytes) {
  check(Buffer.isBuffer(bytes) && bytes.length > 0 && bytes.length <= 4096, 'srt-size');
  return Object.freeze({ 'content-type': 'application/x-subrip', 'content-length': String(bytes.length),
    'content-disposition': 'attachment; filename="synthetic-final.srt"', 'cache-control': 'no-store',
    'x-final-fixture': 'synthetic-only', 'x-content-type-options': 'nosniff', connection: 'close' });
}

/** Self-owned rejecting HTTP proxy; never constructs an upstream client/socket.
 * All UI requests stay in Playwright memory. Only one native absolute-form GET
 * is answered here. CONNECT/upgrade/unknown HTTP receive a closed rejection,
 * never a redirect, tunnel, DNS lookup, fetch, route.continue or forwarding.
 */
export async function startDownloadTransport({ task, job, token, revision, bytes }) {
  const payload = Buffer.from(bytes), headers = responseHeaders(payload), sockets = new Set();
  const counters = { gets: 0, denied: 0, connectDenied: 0, upgradeDenied: 0, clientErrors: 0, outboundConnections: 0 };
  let guard, origin, closed = false, observed = null;
  const deny = response => { response.sendDate = false; response.writeHead(403, { connection: 'close', 'content-length': '0' }); response.end(); };
  const server = createServer({ maxHeaderSize: 8192, requestTimeout: 3000, headersTimeout: 3000 }, (request, response) => {
    if (!guard?.accept({ url: request.url, method: request.method, rawHeaders: request.rawHeaders })) {
      counters.denied++; deny(response); return;
    }
    counters.gets++; response.sendDate = false;
    response.writeHead(200, headers); response.end(payload);
    observed = { status: 200, headers: { ...headers }, browser_header_observation: false, observation: 'transport-response' };
  });
  server.on('connection', socket => { sockets.add(socket); socket.setTimeout(3000, () => socket.destroy()); socket.on('close', () => sockets.delete(socket)); });
  const rejectSocket = socket => socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n');
  server.on('connect', (_request, socket) => { counters.connectDenied++; rejectSocket(socket); });
  server.on('upgrade', (_request, socket) => { counters.upgradeDenied++; rejectSocket(socket); });
  server.on('clientError', (_error, socket) => { counters.clientErrors++; socket.destroy(); });
  async function close() {
    if (closed) return;
    const socketClosures = [...sockets].map(socket => new Promise(ok => socket.once('close', ok)));
    const stopped = new Promise((ok, reject) => server.close(error => error ? reject(Error('transport-close')) : ok()));
    for (const socket of sockets) socket.destroy();
    await Promise.all([stopped, ...socketClosures]);
    closed = true;
  }
  try {
    await new Promise((ok, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', ok); });
    origin = `http://127.0.0.1:${server.address().port}`;
    guard = downloadGuard({ origin, task, job, token, revision });
  } catch { if (server.listening) await close(); throw Error('transport-start'); }
  return {
    origin, arm: receipt => guard.arm(receipt), close,
    get observation() { return observed; },
    get counters() { return { ...counters }; },
    get cleanup() { return { closed, listening: server.listening, sockets: sockets.size }; },
  };
}

export function inspectOwnedFile(file, directory, expected) {
  check(typeof file === 'string' && dirname(file) === directory && realpathSync(directory) === resolve(directory), 'download-owner');
  const parent = lstatSync(directory), stat = lstatSync(file);
  check(parent.isDirectory() && !parent.isSymbolicLink() && stat.isFile() && !stat.isSymbolicLink()
    && stat.nlink === 1 && stat.size === expected.length && stat.size <= 4096, 'download-file');
  const bytes = readFileSync(file);
  check(bytes.equals(expected) && digest(bytes) === digest(expected), 'download-bytes');
  return { bytes: bytes.length, sha256: digest(bytes) };
}

// Failed Playwright artifacts can reject delete() with the original cancellation.
// Do not misreport that expected outcome as context-close failure, or skip closing
// the context/browser. The caller independently verifies directory removal.
export async function cleanupDownload(download) {
  if (!download) return { kind: 'none', deleteRejected: false };
  const failure = await download.failure();
  if (failure === null) {
    const file = await download.path(); await download.delete();
    check(!file || !existsSync(file), 'download-delete');
    return { kind: 'success', deleteRejected: false };
  }
  let deleteRejected = false;
  try { await download.delete(); } catch { deleteRejected = true; }
  return { kind: 'failed-download', deleteRejected };
}