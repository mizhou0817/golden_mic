import assert from 'node:assert/strict';
import { Blob, File } from 'node:buffer';
import { getEventListeners } from 'node:events';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

// Actual appApi, transpiled only in memory. The sole source substitution is
// Vite's import.meta.env binding, by AST rather than textual replacement.
// All storage/fetch/XHR/clock inputs are explicitly synthetic and in memory.
// No server, socket, browser, provider, real credentials or filesystem writes.
// These contracts do NOT claim browser cookie/CORS/redirect or backend coverage.
const source = readFileSync(new URL('../src/lib/appApi.ts', import.meta.url), 'utf8');
let envReplacements = 0;
const compiled = ts.transpileModule(source, {
  fileName: 'appApi.ts', reportDiagnostics: true,
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, sourceMap: false },
  transformers: { before: [context => root => {
    const visit = node => {
      if (ts.isPropertyAccessExpression(node) && node.name.text === 'env'
        && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword) {
        envReplacements++;
        return ts.factory.createIdentifier('__testViteEnv');
      }
      return ts.visitEachChild(node, visit, context);
    };
    return ts.visitNode(root, visit);
  }] },
});
assert.equal(compiled.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0,
  'The actual appApi must transpile without syntax diagnostics.');
assert.ok(envReplacements > 0, 'The harness must bind the actual runtime import.meta.env expression.');
const script = new vm.Script(compiled.outputText, { filename: 'actual-appApi.ts' });
const ORIGIN = 'https://node-only.invalid';
const NOW = Date.parse('2026-09-27T12:00:00.000Z');
const HISTORY_KEY = 'golden-mic.history.v1';
const TASK = 'synthetic-task_1';
const ROOT = `/api/tasks/${TASK}`;
const TOKEN = 'synthetic_task_capability_'.padEnd(48, 't');
const OTHER_TOKEN = 'different_task_capability_'.padEnd(48, 'u');
const CSRF = 'synthetic_browser_csrf_'.padEnd(48, 'c');
const NEXT_CSRF = 'replacement_browser_csrf_'.padEnd(48, 'n');
const plain = value => JSON.parse(JSON.stringify(value));
const json = (value, status = 200, headers = {}) => new Response(value === undefined ? null : JSON.stringify(value),
  { status, headers: { 'Content-Type': 'application/json', ...headers } });
const development = () => ({ access_mode: 'development', csrf_token: null, expires_at: null });
const anonymous = (expires = NOW + 600_000, csrf_token = CSRF) => ({ access_mode: 'anonymous', csrf_token,
  expires_at: new Date(expires).toISOString() });
const historyWork = (patch = {}) => ({ taskId: TASK, accessToken: TOKEN, title: 'Synthetic title',
  createdAt: '2026-09-27T10:00:00.000Z', status: 'done', revision: 4, ...patch });
const receipt = (patch = {}) => ({ task_id: TASK, access_token: TOKEN, status: 'queued', revision: 0, ...patch });
const isAbort = error => error?.name === 'AbortError';
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function memoryStorage(raw = null) {
  const state = { raw, reads: 0, writes: 0, readError: null, writeError: null, readbackError: null, ignoreWrites: false };
  return { state, area: {
    getItem(key) {
      assert.equal(key, HISTORY_KEY); state.reads++;
      if (state.readError) throw state.readError;
      if (state.writes && state.readbackError) throw state.readbackError;
      return state.raw;
    },
    setItem(key, value) {
      assert.equal(key, HISTORY_KEY); assert.equal(typeof value, 'string'); state.writes++;
      if (state.writeError) throw state.writeError;
      if (!state.ignoreWrites) state.raw = value;
    },
    removeItem() { assert.fail('History must never be cleared as an error-recovery side effect.'); },
    clear() { assert.fail('Unrelated browser storage must never be cleared.'); },
  } };
}
function harness({ respond, env = {}, store = memoryStorage(), now = NOW } = {}) {
  const calls = [], xhrs = [], timers = new Map(), clock = { now }, sent = deferred();
  let nextTimer = 0;
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock.now])); }
    static now() { return clock.now; }
  }
  // This is only an XHR event/input adapter. Upload assembly, headers, receipt
  // decoding, error policy, progress and cancellation all run in actual appApi.
  class MemoryXHR {
    constructor() {
      this.upload = { onprogress: null, onload: null };
      this.onload = this.onerror = this.ontimeout = this.onabort = null;
      this.headers = new Headers(); this.responseHeaders = new Headers();
      this.responseText = ''; this.responseURL = ''; this.status = 0;
      this.sends = 0; this.aborts = 0; xhrs.push(this);
    }
    open(method, path) { this.method = method; this.path = path; }
    setRequestHeader(name, value) { this.headers.append(name, value); }
    getResponseHeader(name) { return this.responseHeaders.get(name); }
    send(body) { this.body = body; this.sends++; sent.resolve(this); }
    abort() { this.aborts++; this.onabort?.(); }
    respond({ payload = receipt(), text, status = 201, headers = {}, url = `${ORIGIN}/api/tasks` } = {}) {
      this.status = status; this.responseURL = url; this.responseHeaders = new Headers(headers);
      this.responseText = text ?? JSON.stringify(payload); this.onload?.();
    }
  }
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, __testViteEnv: Object.freeze({ ...env }),
    URL, URLSearchParams, Headers, Response, FormData, Blob, File, AbortController, DOMException, Date: ClockDate,
    location: Object.freeze({ origin: ORIGIN }), localStorage: store.area, XMLHttpRequest: MemoryXHR,
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async (path, init) => {
      calls.push({ path, init });
      assert.equal(typeof respond, 'function', `Unexpected synthetic request: ${path}`);
      const response = await respond(path, init);
      assert.ok(response instanceof Response, 'Every fake response must be explicit; never fall through to host fetch.');
      return response;
    },
    require(name) { assert.fail(`Unexpected runtime dependency: ${name}`); },
    WebSocket() { assert.fail('No websocket transport in Node contracts.'); },
    EventSource() { assert.fail('No event-stream transport in Node contracts.'); },
  });
  script.runInContext(context, { timeout: 1000 });
  return { api: module.exports, calls, xhrs, timers, clock, store, sent: sent.promise,
    fireTimer(id) {
      const timer = timers.get(id); assert.ok(timer, 'Only an actual scheduled timer can fire.');
      timers.delete(id); timer.callback();
    } };
}
const request = (h, access = {}) => h.api.createAppRequest({ taskId: TASK, token: TOKEN,
  signal: new AbortController().signal, ...access });
const onlySession = (session = development()) => path => { assert.equal(path, '/api/session'); return json(session); };
const methods = h => h.calls.map(({ path, init }) => [path, init.method ?? 'GET']);
const idle = h => assert.equal(h.timers.size, 0, 'Every settled request clears its deadline.');
const noAbortListeners = signal => assert.equal(getEventListeners(signal, 'abort').length, 0, 'Caller abort listeners are detached.');
function pendingFetch(signal) {
  return new Promise((resolve, reject) => {
    const abort = () => { signal.removeEventListener('abort', abort); reject(signal.reason); };
    if (signal.aborted) abort(); else signal.addEventListener('abort', abort, { once: true });
  });
}
function submission(patch = {}) {
  // These contracts deliberately exercise restored legacy multipart submissions.
  return { legacyRestore: true, script: 'Synthetic headline\n\nSynthetic narration.',
    files: [new File([Uint8Array.of(1, 2, 3)], 'synthetic-video.mp4', { type: 'video/mp4' }),
      new File([Uint8Array.of(4, 5)], 'synthetic-image.png', { type: 'image/png' })],
    preferences: { pacing: 'normal', tone: 'neutral', caption_style: 'none', enhance_speech: false,
      background_music: false, music_mood: 'auto', motion_effects: true, transitions: false, news_graphics: false,
      color_consistency: false, generative_fill: false, custom_instructions: '' },
    asset_options: [{ note: 'First source', trim_start: 0.123456789, trim_end: 1.987654321 },
      { note: 'Still source', trim_start: 0, trim_end: null }], ...patch };
}
function assertXhrFinished(xhr, signal) {
  for (const key of ['onload', 'onerror', 'ontimeout', 'onabort']) assert.equal(xhr[key], null, key);
  assert.equal(xhr.upload.onprogress, null); assert.equal(xhr.upload.onload, null);
  noAbortListeners(signal);
}

test('empty browser history is read-only and does not bootstrap a session or touch the network', () => {
  const h = harness(); assert.equal(h.api.HISTORY_KEY, HISTORY_KEY);
  assert.deepEqual(plain(h.api.readHistory()), []);
  assert.equal(h.store.state.raw, null); assert.equal(h.store.state.reads, 1); assert.equal(h.store.state.writes, 0);
  assert.equal(h.calls.length, 0); idle(h);
});

test('history projects only task capability/title/date/status/revision and detaches discarded account fields', () => {
  const row = historyWork(), input = { ...row, identity: { role: 'teacher' }, class_id: 'retired',
    authorization: 'not-a-task-capability', injected: { script: 'must not persist on next write' } };
  const raw = JSON.stringify([input]), h = harness({ store: memoryStorage(raw) });
  const result = h.api.readHistory(); assert.deepEqual(plain(result), [row]);
  result[0].title = 'Changed in memory'; assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 0);
  assert.deepEqual(plain(h.api.readHistory()), [row]);
});

test('history rejects every malformed row without salvaging a prefix or rewriting the original record', () => {
  const patches = [{ taskId: '' }, { taskId: '../outside' }, { taskId: 'x'.repeat(129) }, { title: null }, { title: 'x'.repeat(513) },
    { createdAt: 'not a date' }, { createdAt: 'x'.repeat(65) }, { status: 'succeeded' }, { status: ['done'] },
    { revision: -1 }, { revision: 0.5 }, { revision: '4' }, { revision: Number.MAX_SAFE_INTEGER + 1 },
    { accessToken: '' }, { accessToken: null }, { accessToken: 't'.repeat(31) }, { accessToken: 't'.repeat(257) }];
  for (const invalid of [null, [], false, 'row', ...patches.map(patch => historyWork(patch))]) {
    const raw = JSON.stringify([historyWork({ taskId: 'valid-first' }), invalid]);
    const h = harness({ store: memoryStorage(raw) });
    assert.throws(() => h.api.readHistory()); assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 0);
  }
});

test('history enforces both byte and row-count limits without eviction or storage clearing', () => {
  for (const raw of [' '.repeat(2 * 1024 * 1024 + 1), JSON.stringify(Array.from({ length: 1001 }, (_, i) => historyWork({ taskId: `task-${i}` })))]) {
    const h = harness({ store: memoryStorage(raw) });
    assert.throws(() => h.api.readHistory()); assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 0);
  }
});

test('same-capability history duplicates retain the highest revision and deterministic first row on a tie', () => {
  const low = historyWork({ revision: 1 }), high = historyWork({ title: 'Highest', revision: 9 });
  const other = historyWork({ taskId: 'other-task', accessToken: OTHER_TOKEN });
  for (const rows of [[low, other, high, low], [high, other, low, { ...high, title: 'Later tie' }]]) {
    const raw = JSON.stringify(rows), h = harness({ store: memoryStorage(raw) });
    assert.deepEqual(plain(h.api.readHistory()), [high, other]); assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 0);
  }
});

test('conflicting duplicate capabilities fail closed regardless of revision/order, including remember/forget', () => {
  for (const revisions of [[1, 2], [2, 1], [4, 4]]) {
    const rows = [historyWork({ revision: revisions[0] }), historyWork({ revision: revisions[1], accessToken: OTHER_TOKEN })];
    const raw = JSON.stringify(rows), h = harness({ store: memoryStorage(raw) });
    assert.throws(() => h.api.readHistory(), /冲突/);
    assert.throws(() => h.api.rememberWork(historyWork({ taskId: 'new-task' })), /冲突/);
    assert.throws(() => h.api.forgetHistory(TASK), /冲突/);
    assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 0); assert.equal(h.calls.length, 0);
  }
});

test('rememberWork writes a projected receipt first, removes only its prior row, and verifies persistence', () => {
  const old = historyWork({ revision: 1 }), peer = historyWork({ taskId: 'peer', accessToken: OTHER_TOKEN });
  const h = harness({ store: memoryStorage(JSON.stringify([peer, old])) });
  const item = Object.freeze({ ...historyWork(), identity: 'retired', extra: { secret: 'synthetic discarded field' } });
  h.api.rememberWork(item);
  assert.deepEqual(JSON.parse(h.store.state.raw), [historyWork(), peer]);
  assert.equal(h.store.state.writes, 1); assert.equal(h.store.state.reads, 2);
  assert.equal(item.identity, 'retired'); assert.equal(h.calls.length, 0);
});

test('malformed JSON and nonarray storage remain untouched on read, remember and forget', () => {
  for (const raw of ['', '{broken', '{}', 'null', 'false', '"not an array"']) {
    const h = harness({ store: memoryStorage(raw) });
    assert.throws(() => h.api.readHistory()); assert.throws(() => h.api.rememberWork(historyWork()));
    assert.throws(() => h.api.forgetHistory(TASK));
    assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 0);
  }
});

test('denied storage reads propagate without replacing the history or inventing a successful save', () => {
  const h = harness({ store: memoryStorage(JSON.stringify([historyWork()])) }), error = new Error('Synthetic storage denied');
  h.store.state.readError = error;
  for (const read of [() => h.api.readHistory(), () => h.api.rememberWork(historyWork()), () => h.api.forgetHistory(TASK)]) {
    assert.throws(read, caught => caught === error);
  }
  assert.equal(h.store.state.writes, 0); assert.equal(h.calls.length, 0);
});

test('storage quota failures preserve the old record and the caller-owned capability without a POST retry', () => {
  const raw = JSON.stringify([historyWork()]);
  for (const operation of ['remember', 'forget']) {
    const h = harness({ store: memoryStorage(raw) }), error = new DOMException('Synthetic full storage', 'QuotaExceededError');
    h.store.state.writeError = error;
    const item = Object.freeze(historyWork({ taskId: 'new-task' }));
    assert.throws(() => operation === 'remember' ? h.api.rememberWork(item) : h.api.forgetHistory(TASK), caught => caught === error);
    assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 1);
    assert.equal(item.accessToken, TOKEN); assert.equal(h.calls.length, 0);
  }
});

test('silent write loss is an explicit unconfirmed persistence failure, never successful history recovery', () => {
  const raw = JSON.stringify([historyWork()]), h = harness({ store: memoryStorage(raw) });
  h.store.state.ignoreWrites = true;
  assert.throws(() => h.api.rememberWork(historyWork({ taskId: 'not-stored' })), /确认保存/);
  assert.throws(() => h.api.forgetHistory(TASK), /确认保存/);
  assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 2); assert.equal(h.calls.length, 0);
});

test('a failed readback after an actual write is reported without a second write or invented rollback', () => {
  const h = harness(), failure = new Error('Synthetic readback denied');
  h.store.state.readbackError = failure;
  assert.throws(() => h.api.rememberWork(historyWork()), caught => caught === failure);
  assert.equal(h.store.state.writes, 1); assert.deepEqual(JSON.parse(h.store.state.raw), [historyWork()]);
  h.store.state.readbackError = null; assert.deepEqual(plain(h.api.readHistory()), [historyWork()]);
  assert.equal(h.calls.length, 0);
});

test('1000 valid capabilities may be updated but a new task cannot silently evict an old one', () => {
  const rows = Array.from({ length: 1000 }, (_, i) => historyWork({ taskId: `task-${i}` }));
  const raw = JSON.stringify(rows), h = harness({ store: memoryStorage(raw) });
  assert.equal(h.api.readHistory().length, 1000);
  assert.throws(() => h.api.rememberWork(historyWork()), /容量/);
  assert.equal(h.store.state.raw, raw); assert.equal(h.store.state.writes, 0);
  const changed = historyWork({ taskId: 'task-500', revision: 5 }); h.api.rememberWork(changed);
  const next = h.api.readHistory(); assert.equal(next.length, 1000); assert.deepEqual(plain(next[0]), changed);
  assert.deepEqual(new Set(next.map(row => row.taskId)), new Set(rows.map(row => row.taskId)));
});

test('forgetHistory removes only an exact task and rejects malformed task IDs before touching storage', () => {
  const peers = [historyWork(), historyWork({ taskId: `${TASK}-suffix`, accessToken: OTHER_TOKEN })];
  const h = harness({ store: memoryStorage(JSON.stringify(peers)) });
  for (const taskId of ['', '../other', 'a/b', 'x'.repeat(129)]) assert.throws(() => h.api.forgetHistory(taskId));
  assert.equal(h.store.state.reads, 0); assert.equal(h.store.state.writes, 0);
  h.api.forgetHistory(TASK); assert.deepEqual(plain(h.api.readHistory()), [peers[1]]); assert.equal(h.calls.length, 0);
});

test('invalid new history entries cannot trigger a storage write or overwrite existing capabilities', () => {
  const h = harness({ store: memoryStorage(JSON.stringify([historyWork()])) });
  for (const patch of [{ accessToken: undefined }, { accessToken: '' }, { revision: Infinity }, { status: 'accepted' }]) {
    assert.throws(() => h.api.rememberWork(historyWork(patch)));
  }
  assert.equal(h.store.state.reads, 0); assert.equal(h.store.state.writes, 0);
});

test('parseReceipt requires a real nonempty capability and projects only the actual receipt fields', () => {
  const h = harness();
  for (const status of ['queued', 'running', 'done', 'failed', 'cancelled']) {
    const value = receipt({ status, revision: Number.MAX_SAFE_INTEGER, identity: { role: 'retired' }, title: 'not in receipt' });
    assert.deepEqual(plain(h.api.parseReceipt(value)), { task_id: TASK, access_token: TOKEN, status, revision: Number.MAX_SAFE_INTEGER });
  }
  const minimal = { task_id: TASK, access_token: TOKEN };
  assert.deepEqual(plain(h.api.parseReceipt(minimal)), minimal);
  assert.equal(Object.hasOwn(h.api.parseReceipt(minimal), 'revision'), false);
});

test('receipt task IDs and capabilities enforce exact inclusive length and URL-safe character boundaries', () => {
  const h = harness();
  for (const size of [32, 256]) assert.equal(h.api.parseReceipt(receipt({ access_token: '_-aZ09'.repeat(43).slice(0, size) })).access_token.length, size);
  for (const size of [1, 128]) assert.equal(h.api.parseReceipt(receipt({ task_id: 'T'.repeat(size) })).task_id.length, size);
  for (const task_id of ['', 'a'.repeat(129), '../x', 'x/y', 'x?token=t', 'x#fragment', 'space task', '\\host', ['task']]) {
    assert.throws(() => h.api.parseReceipt(receipt({ task_id })));
  }
  for (const access_token of [undefined, null, '', 't'.repeat(31), 't'.repeat(257), `${TOKEN}=`, `${TOKEN}/`, `${TOKEN} `, 1, [], {}]) {
    assert.throws(() => h.api.parseReceipt(receipt({ access_token })));
  }
});

test('receipt parsing rejects mistyped optional fields and nonobjects without filling invented defaults', () => {
  const h = harness();
  for (const value of [null, [], false, 'receipt', {}, receipt({ revision: -1 }), receipt({ revision: 0.5 }),
    receipt({ revision: '1' }), receipt({ revision: null }), receipt({ revision: NaN }), receipt({ revision: Infinity }),
    receipt({ status: null }), receipt({ status: 'succeeded' }), receipt({ status: ['queued'] })]) {
    assert.throws(() => h.api.parseReceipt(value));
  }
});

test('task status validates task/progress/revision/stage structure without declaring unknown states done', () => {
  const h = harness(), value = { task_id: TASK, status: 'running', progress: 50, revision: 4,
    stages: [{ number: 1, name: 'Synthetic validation', status: 'done' }] };
  assert.deepEqual(plain(h.api.parseTask(value)), value);
  for (const patch of [{ task_id: '../other' }, { status: 'succeeded' }, { progress: -1 }, { progress: 101 },
    { progress: NaN }, { progress: Infinity }, { revision: 1.5 }, { stages: null },
    { stages: [{ number: 1, name: 'Stage', status: 'unknown' }] }]) assert.throws(() => h.api.parseTask({ ...value, ...patch }));
});

test('local history paginates read-only and strips every supplied token/account field from its projection', async () => {
  const raws = Array.from({ length: 201 }, (_, i) => ({ task_id: `local-${i}`, title: `Local ${i}`, created_at: '2026-09-27T10:00:00Z',
    revision: i, status: 'done', access_token: TOKEN, accessToken: OTHER_TOKEN, identity: { role: 'retired' } }));
  const h = harness({ respond: (path, init) => {
    assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); assert.equal(init.redirect, 'error');
    assert.equal(new Headers(init.headers).has('X-Task-Token'), false);
    const url = new URL(path, ORIGIN); assert.equal(url.pathname, '/api/tasks'); assert.equal(url.searchParams.get('limit'), '200');
    const offset = Number(url.searchParams.get('offset')); return json({ total: 201, tasks: raws.slice(offset, offset + 200) });
  } });
  const rows = await h.api.getLocalHistory();
  assert.deepEqual(plain(rows), raws.map(({ task_id, title, created_at, status, revision }) => ({ taskId: task_id, title, createdAt: created_at, status, revision })));
  assert.deepEqual(methods(h), [['/api/tasks?offset=0&limit=200', 'GET'], ['/api/tasks?offset=200&limit=200', 'GET']]);
  assert.equal(h.store.state.reads, 0); assert.equal(h.store.state.writes, 0); idle(h);
});

test('local history refuses malformed pages, rows and empty intermediate pages rather than returning a partial success', async () => {
  const row = { task_id: TASK, title: 'Local', created_at: '2026-09-27T10:00:00Z', status: 'done', revision: 4 };
  for (const value of [null, [], { total: -1, tasks: [] }, { total: '1', tasks: [row] }, { total: 0.5, tasks: [] },
    { total: 201, tasks: Array(201).fill(row) }, { total: 1, tasks: [] }, { total: 1, tasks: [null] },
    { total: 1, tasks: [{ ...row, task_id: '../foreign' }] }]) {
    const h = harness({ respond: () => json(value) }); await assert.rejects(h.api.getLocalHistory());
    assert.equal(h.calls.length, 1); assert.equal(h.store.state.writes, 0); idle(h);
  }
});

test('local history has a finite 100-page read budget even if totals never converge', async () => {
  const h = harness({ respond: path => {
    const offset = Number(new URL(path, ORIGIN).searchParams.get('offset'));
    return json({ total: 100_000, tasks: [{ task_id: `row-${offset}`, title: 'Local', created_at: '2026-09-27T10:00:00Z', status: 'done', revision: 0 }] });
  } });
  await assert.rejects(h.api.getLocalHistory(), /安全读取上限/); assert.equal(h.calls.length, 100); idle(h);
});

test('workspace configuration projects booleans and never treats malformed capability data as available', async () => {
  for (const local_history of [false, true]) for (const generative_fill_available of [false, true]) {
    const value = { local_history, generative_fill_available }, h = harness({ respond: path => {
      assert.equal(path, '/api/config/workspace'); return json({ ...value, identity: 'retired' });
    } });
    assert.deepEqual(plain(await h.api.getWorkspaceConfig()), value); idle(h);
  }
  for (const value of [null, {}, { local_history: 'true', generative_fill_available: false }, { local_history: true, generative_fill_available: 1 }]) {
    const h = harness({ respond: () => json(value) }); await assert.rejects(h.api.getWorkspaceConfig()); idle(h);
  }
});

test('public upload limits come from valid positive server bounds, not client-invented fallback values', async () => {
  const value = { max_files: 100, max_upload_bytes: 1024, max_total_upload_bytes: 8192, max_script_length: 2000,
    max_video_duration_seconds: 120.5, max_total_video_duration_seconds: 600, allowed_extensions: ['.mp4', '.PNG'] };
  const good = harness({ respond: path => { assert.equal(path, '/api/config/limits'); return json(value); } });
  assert.deepEqual(plain(await good.api.getPublicLimits()), value); idle(good);
  for (const patch of [{ max_files: 0 }, { max_files: 1.5 }, { max_upload_bytes: '1024' }, { max_total_upload_bytes: -1 },
    { max_script_length: null }, { max_video_duration_seconds: 0 }, { max_total_video_duration_seconds: null },
    { allowed_extensions: [] }, { allowed_extensions: ['mp4'] }, { allowed_extensions: ['.mp4', '.js/path'] }]) {
    const h = harness({ respond: () => json({ ...value, ...patch }) }); await assert.rejects(h.api.getPublicLimits()); idle(h);
  }
});

test('same-origin paths allow only the API namespace or exact readiness endpoint after canonicalization', () => {
  const h = harness();
  for (const path of ['/api/tasks', '/api/session', '/api/config/limits?x=1', '/health/ready']) assert.equal(h.api.sameOriginPath(path), path);
  assert.equal(h.api.sameOriginPath('/api/temporary/../tasks'), '/api/tasks');
  for (const path of ['/', '/api', '/apix/tasks', '/health/ready/extra', '/health/ready/', '/assets/app.js', '/api/../../outside']) {
    assert.throws(() => h.api.sameOriginPath(path));
  }
  assert.equal(h.calls.length, 0);
});

test('same-origin transport rejects absolute/network paths, userinfo, fragments, backslashes and controls', () => {
  const h = harness();
  for (const path of [`${ORIGIN}/api/tasks`, 'https://outside.invalid/api/tasks', '//outside.invalid/api/tasks',
    '//user:pass@node-only.invalid/api/tasks', 'api/tasks', '\\node-only.invalid\\api\\tasks', '/api\\tasks',
    '/api/tasks#fragment', '/api/tasks?q=has space', '/api/ta\tsks', '/api/tasks\n', '/api/tasks\0']) {
    assert.throws(() => h.api.sameOriginPath(path));
  }
});

test('Vite env is replaced only in the VM and accepts empty/root or exact same-origin root configuration', () => {
  for (const value of [undefined, '', '/', ORIGIN, `${ORIGIN}/`]) {
    const h = harness({ env: value === undefined ? {} : { VITE_API_BASE_URL: value } });
    assert.doesNotThrow(() => h.api.assertSameOriginConfiguration());
  }
  assert.ok(source.includes('import.meta.env.VITE_API_BASE_URL'), 'No test-specific production semantics are required.');
});

test('foreign/subpath/credential/query/fragment configuration fails before every transport, including upload', async () => {
  for (const value of ['https://outside.invalid', '/subpath', `${ORIGIN}/api/`, 'https://user:pass@node-only.invalid/',
    `${ORIGIN}/?token=x`, `${ORIGIN}/#fragment`, 'http://node-only.invalid/']) {
    const h = harness({ env: { VITE_API_BASE_URL: value } });
    assert.throws(() => h.api.assertSameOriginConfiguration());
    await assert.rejects(h.api.publicRequest('/api/config/limits'));
    await assert.rejects(request(h)(ROOT));
    await assert.rejects(h.api.uploadTask(submission(), new AbortController().signal, () => assert.fail('No upload progress before admission.')));
    assert.equal(h.calls.length, 0); assert.equal(h.xhrs.length, 0); idle(h);
  }
});

test('selected-task reads permit exact root, query and child boundaries with one receipt capability', async () => {
  const h = harness({ respond: () => json({ ok: true }) }), call = request(h);
  for (const path of [ROOT, `${ROOT}/`, `${ROOT}?revision=4`, `${ROOT}/studio/project`, `${ROOT}/workbench/context`]) {
    assert.deepEqual(plain(await call(path)), { ok: true });
    assert.equal(h.calls.at(-1).path, path); assert.equal(h.calls.at(-1).init.headers.get('X-Task-Token'), TOKEN);
  }
  assert.equal(h.calls.length, 5); assert.equal(h.store.state.reads, 0); idle(h);
});

test('missing/invalid selected task or supplied capability fails before bootstrap and task fetch', async () => {
  const h = harness();
  for (const access of [{ taskId: undefined }, { taskId: '' }, { taskId: '../other' }, { taskId: 't'.repeat(129) },
    { token: '' }, { token: null }, { token: 't'.repeat(31) }, { token: 't'.repeat(257) }, { token: `${TOKEN}/` }]) {
    await assert.rejects(request(h, access)(ROOT, { method: 'POST', body: '{}' })); idle(h);
  }
  assert.equal(h.calls.length, 0);
});

test('selected-task boundaries reject sibling/prefix collisions, encoded traversal and all non-task endpoints', async () => {
  const h = harness(), call = request(h);
  for (const path of ['/api/tasks', '/api/session', '/api/config/workspace', '/api/classroom/session', '/health/ready',
    '/api/tasks/other', `${ROOT}-suffix`, `${ROOT}_suffix/project`, `${ROOT}%2Fsibling`,
    `${ROOT}/../other`, `${ROOT}/%2e%2e/other`, `${ROOT}/%2E%2E/other`, `${ROOT}/../../tasks/other`,
    `${ROOT}/studio/../../../other`, `${ROOT}#fragment`, `https://outside.invalid${ROOT}`]) {
    await assert.rejects(call(path, { method: 'POST', body: '{}' })); idle(h);
  }
  assert.equal(h.calls.length, 0);
});

test('caller query token injection is rejected even when empty, repeated or percent-encoded', async () => {
  const h = harness(), call = request(h);
  for (const query of ['token=', `token=${TOKEN}`, `x=1&token=${OTHER_TOKEN}`, 'token=x&token=y', '%74oken=x', 'to%6ben=x']) {
    await assert.rejects(call(`${ROOT}?${query}`, { method: 'POST', body: '{}' }), /额外/);
  }
  assert.equal(h.calls.length, 0); idle(h);
});

test('task transport overwrites unsafe fetch options and strips injected credentials without mutating caller headers', async () => {
  const h = harness({ respond: path => path === '/api/session' ? json(anonymous()) : json({ ok: true }) });
  const headers = new Headers({ Authorization: 'Bearer synthetic-injected', 'X-Classroom-CSRF': 'retired',
    'X-CSRF-Token': 'injected', 'X-Task-Token': OTHER_TOKEN, Accept: 'text/html', 'X-Editor-Revision': '4' });
  const before = [...headers], access = new AbortController(), caller = new AbortController();
  const init = { method: 'POST', body: '{"revision":4}', headers, mode: 'cors', credentials: 'omit',
    redirect: 'follow', cache: 'force-cache', signal: caller.signal };
  await request(h, { signal: access.signal })(`${ROOT}/workbench/edit`, init);
  const sent = h.calls.at(-1).init;
  assert.equal(sent.headers.get('X-Task-Token'), TOKEN); assert.equal(sent.headers.get('X-CSRF-Token'), CSRF);
  assert.equal(sent.headers.has('Authorization'), false); assert.equal(sent.headers.has('X-Classroom-CSRF'), false);
  assert.equal(sent.headers.get('Accept'), 'application/json'); assert.equal(sent.headers.get('Content-Type'), 'application/json');
  assert.equal(sent.headers.get('X-Editor-Revision'), '4'); assert.equal(sent.body, init.body);
  assert.equal(sent.credentials, 'include'); assert.equal(sent.mode, 'same-origin'); assert.equal(sent.redirect, 'error'); assert.equal(sent.cache, 'no-store');
  assert.notEqual(sent.signal, access.signal); assert.notEqual(sent.signal, caller.signal);
  assert.deepEqual([...headers], before); assert.equal(init.credentials, 'omit');
  noAbortListeners(access.signal); noAbortListeners(caller.signal); idle(h);
});

test('token-free local task access never borrows caller headers or storage capabilities', async () => {
  const h = harness({ store: memoryStorage(JSON.stringify([historyWork()])),
    respond: path => path === '/api/session' ? json(development()) : json({ ok: true }) });
  const call = request(h, { token: undefined });
  for (const method of ['GET', 'POST', 'DELETE']) {
    await call(ROOT, { method, headers: { Authorization: 'Basic synthetic', 'X-Task-Token': TOKEN,
      'X-Classroom-CSRF': 'retired', 'X-CSRF-Token': CSRF } });
    const headers = h.calls.at(-1).init.headers;
    for (const name of ['Authorization', 'X-Task-Token', 'X-Classroom-CSRF', 'X-CSRF-Token']) assert.equal(headers.has(name), false);
  }
  assert.equal(h.calls.filter(row => row.path === '/api/session').length, 1); assert.equal(h.store.state.reads, 0); idle(h);
});

test('GET/HEAD/OPTIONS are read-only, strip caller CSRF and do not create an anonymous session', async () => {
  const h = harness({ respond: () => json({ ok: true }) }), call = request(h);
  for (const method of ['GET', 'HEAD', 'OPTIONS', 'get']) {
    await call(ROOT, { method, headers: { 'X-CSRF-Token': 'injected', 'X-Classroom-CSRF': 'retired' } });
    assert.equal(h.calls.at(-1).init.headers.has('X-CSRF-Token'), false);
    assert.equal(h.calls.at(-1).init.headers.has('X-Classroom-CSRF'), false);
  }
  assert.equal(h.calls.length, 4); assert.ok(h.calls.every(row => row.path === ROOT)); idle(h);
});

test('JSON/raw File/FormData bodies keep identity and appropriate content type without copying asset bytes', async () => {
  const h = harness({ respond: path => path === '/api/session' ? json(development()) : json({ ok: true }) }), call = request(h);
  const raw = new File([Uint8Array.of(1, 2)], 'synthetic.png', { type: 'image/png' }), form = new FormData(); form.append('file', raw);
  for (const [body, contentType, expected] of [['{}', undefined, 'application/json'], ['text', 'text/plain', 'text/plain'],
    [raw, 'image/png', 'image/png'], [form, 'application/json', null]]) {
    await call(ROOT, { method: 'POST', body, headers: contentType ? { 'Content-Type': contentType } : {} });
    assert.equal(h.calls.at(-1).init.body, body); assert.equal(h.calls.at(-1).init.headers.get('Content-Type'), expected);
  }
  assert.equal(h.calls.filter(row => row.path === '/api/session').length, 1); idle(h);
});

test('successful JSON and empty 204 responses decode without inventing payloads', async () => {
  for (const payload of [{ ok: true }, [1, 2], null, undefined]) {
    const h = harness({ respond: () => json(payload, payload === undefined ? 204 : 200) });
    const result = await request(h)(ROOT);
    if (payload === undefined) assert.equal(result, undefined); else assert.deepEqual(plain(result), payload);
    idle(h);
  }
});

test('upload-limit 429s show the server\'s own fixed sentence; any other 429 text stays the generic rate-limit message', async () => {
  for (const known of ['当前会话最多保留 20 个文件，合计不超过 5 GiB。', '新建上传过于频繁，请稍后重试。', '全站上传名额已满，请稍后重试。']) {
    const h = harness({ respond: () => json({ detail: known }, 429) });
    await assert.rejects(request(h)(ROOT), error => { assert.equal(error.status, 429); assert.equal(error.message, known); return true; });
  }
  for (const detail of ['Traceback (most recent call last): secret=abc', '当前会话最多保留 21 个文件，合计不超过 5 GiB。', { message: '新建上传过于频繁，请稍后重试。' }]) {
    const h = harness({ respond: () => json({ detail }, 429) });
    await assert.rejects(request(h)(ROOT), error => { assert.ok(error.message.includes('频繁')); assert.ok(!error.message.includes('Traceback')); assert.ok(!error.message.includes('21')); return true; });
  }
  // The same sentence under another status is not special-cased.
  const h = harness({ respond: () => json({ detail: '新建上传过于频繁，请稍后重试。' }, 409) });
  await assert.rejects(request(h)(ROOT), error => { assert.equal(error.status, 409); return true; });
});
test('recovery refusals say what they mean; only the fixed recovery_quota sentence overrides the generic 429', async () => {
  for (const [status, detail, expected] of [
    [409, { code: 'recovery_source_unverified' }, '没有保存完整'], [409, { code: 'recovery_legacy_unsupported' }, '旧版本'],
    [409, { code: 'recovery_requires_terminal_task' }, '还在制作中'], [409, { code: 'recovery_requires_accepted_task' }, '没有被服务器正式接收'],
    [409, { code: 'retry_same_unavailable', message: 'raw server prose' }, '不能从失败处继续制作'],
    [409, { code: 'retry_inputs_changed' }, '不能直接续作'], [409, { code: 'retry_cache_missing', stage: 6 }, '缓存不完整'],
    [409, { code: 'retry_cache_invalid' }, '缓存校验没通过'], [409, { code: 'retry_provider_uncertain', stage: 4 }, '避免重复收费'],
    [409, { code: 'shot_reuse_not_applicable' }, '不是“镜头不足”'],
    [429, { code: 'recovery_draft_limit', message: '未提交的草稿已达上限（每个会话最多 20 份）。' }, '草稿已达上限'],
    [429, { code: 'recovery_quota', message: '恢复需要 17 个文件名额，当前会话最多保留 20 个文件。' }, '恢复需要 17 个文件名额']]) {
    const h = harness({ respond: () => json({ detail }, status) });
    await assert.rejects(request(h)(ROOT), error => { assert.equal(error.status, status); assert.ok(error.message.includes(expected), error.message); return true; });
  }
  // An ordinary 429 (or a quota code with an oversized/untyped message) stays the generic rate-limit text.
  for (const detail of [{}, { code: 'recovery_quota', message: 'x'.repeat(301) }, { code: 'recovery_quota' }, { message: '全站上传名额已满' }]) {
    const h = harness({ respond: () => json({ detail }, 429) });
    await assert.rejects(request(h)(ROOT), error => { assert.ok(error.message.includes('频繁')); assert.ok(!error.message.includes('xxxx')); return true; });
  }
});

test('HTTP error details/status survive decoding, with bounded generic fallbacks instead of proxy HTML', async () => {
  for (const [status, payload, expected] of [[409, { detail: 'Synthetic revision conflict' }, 'Synthetic revision conflict'],
    [422, { detail: { message: 'Synthetic validation' } }, 'Synthetic validation'], [401, {}, '凭据'], [403, {}, '安全'],
    [404, {}, '不存在'], [410, {}, '暂不可访问'], [410, { detail: { code: 'task_gone' } }, '到期'],
    [410, { detail: { code: 'unknown' } }, '暂不可访问'], [429, {}, '频繁'], [500, {}, '500']]) {
    const h = harness({ respond: () => json(payload, status) });
    await assert.rejects(request(h)(ROOT), error => {
      assert.ok(error instanceof h.api.AppApiError);
      assert.equal(error.status, status); assert.ok(error.message.includes(expected));
      if (status === 410) {
        const provenGone = payload.detail?.code === 'task_gone';
        assert.equal(error.code, provenGone ? 'task_gone' : undefined);
        assert.equal(error.message.includes('到期'), provenGone);
      }
      return true;
    });
    assert.equal(h.calls.length, 1); idle(h);
  }
  const h = harness({ respond: () => new Response('<html>synthetic-proxy-secret</html>', { status: 502 }) });
  await assert.rejects(request(h)(ROOT), error => error.status === 502 && !error.message.includes('synthetic-proxy-secret')); idle(h);
});

test('malformed successful JSON is an explicit 502 and does not display arbitrary proxy markup', async () => {
  const h = harness({ respond: () => new Response('<script>synthetic markup</script>', { status: 200 }) });
  await assert.rejects(request(h)(ROOT), error => error.status === 502 && !error.message.includes('<script>'));
  assert.equal(h.calls.length, 1); idle(h);
});

test('concurrent pending anonymous writes share exactly one bootstrap and wait for its real CSRF receipt', async () => {
  const hold = deferred(), h = harness({ respond: path => path === '/api/session' ? hold.promise : json({ ok: true }) });
  const first = request(h)(`${ROOT}/studio/project`, { method: 'POST', body: '{}' });
  const second = request(h, { taskId: 'other-task', token: OTHER_TOKEN })('/api/tasks/other-task/studio/project', { method: 'POST', body: '{}' });
  const third = request(h)(`${ROOT}/workbench/edit`, { method: 'POST', body: '{}' });
  assert.deepEqual(methods(h), [['/api/session', 'GET']], 'Pending expiry must not cause replacement bootstraps.');
  hold.resolve(json(anonymous())); await Promise.all([first, second, third]);
  assert.equal(h.calls.length, 4);
  for (const row of h.calls.slice(1)) {
    assert.equal(row.init.headers.get('X-CSRF-Token'), CSRF);
    assert.equal(row.init.headers.get('X-Task-Token'), row.path.startsWith('/api/tasks/other-task/') ? OTHER_TOKEN : TOKEN);
  }
  assert.equal(h.store.state.reads, 0); assert.equal(h.store.state.writes, 0); idle(h);
});

test('anonymous expiry reuses cache until the exact 60-second margin, then coalesces one held renewal', async () => {
  const renew = deferred(); let reads = 0;
  const h = harness({ respond: path => path === '/api/session' ? ++reads === 1 ? json(anonymous(NOW + 120_000)) : renew.promise : json({ ok: true }) });
  const call = request(h), write = () => call(ROOT, { method: 'POST' });
  await write(); h.clock.now = NOW + 59_999; await write(); assert.equal(reads, 1);
  h.clock.now = NOW + 60_000; const first = write(), second = write();
  assert.equal(reads, 2); assert.equal(h.calls.filter(row => row.path === ROOT).length, 2);
  renew.resolve(json(anonymous(NOW + 600_000, NEXT_CSRF))); await Promise.all([first, second]);
  assert.deepEqual(h.calls.filter(row => row.path === ROOT).map(row => row.init.headers.get('X-CSRF-Token')), [CSRF, CSRF, NEXT_CSRF, NEXT_CSRF]);
  idle(h);
});

test('development mode caches only null CSRF and does not invent expiry or anonymous credentials', async () => {
  const h = harness({ respond: path => path === '/api/session' ? json(development()) : json({ ok: true }) }), call = request(h, { token: undefined });
  await call(ROOT, { method: 'POST' }); h.clock.now += 365 * 24 * 60 * 60_000; await call(ROOT, { method: 'DELETE' });
  assert.equal(h.calls.filter(row => row.path === '/api/session').length, 1);
  assert.ok(h.calls.filter(row => row.path === ROOT).every(row => !row.init.headers.has('X-CSRF-Token') && !row.init.headers.has('X-Task-Token')));
  idle(h);
});

test('invalid/expired anonymous sessions and invalid development CSRF never authorize a task mutation', async () => {
  for (const value of [null, {}, { ...anonymous(), access_mode: 'authenticated' }, { ...anonymous(), access_mode: 'classroom' },
    anonymous(NOW), anonymous(NOW - 1), { ...anonymous(), expires_at: 'not-a-date' }, { ...anonymous(), expires_at: null },
    { ...anonymous(), csrf_token: '' }, { ...anonymous(), csrf_token: 'c'.repeat(31) }, { ...anonymous(), csrf_token: null },
    { ...development(), csrf_token: CSRF }, { ...development(), expires_at: anonymous().expires_at }]) {
    const h = harness({ respond: () => json(value) });
    await assert.rejects(request(h)(ROOT, { method: 'POST' }));
    assert.deepEqual(methods(h), [['/api/session', 'GET']]); idle(h);
  }
});

test('session access_mode must be a string, not a coerced array that bypasses anonymous CSRF validation', async () => {
  for (const access_mode of [['anonymous'], ['development']]) {
    const h = harness({ respond: path => path === '/api/session' ? json({ access_mode, csrf_token: null, expires_at: null }) : json({ ok: true }) });
    await assert.rejects(request(h)(ROOT, { method: 'POST' }), 'A nonstring mode must not become a token-free development session.');
    assert.deepEqual(methods(h), [['/api/session', 'GET']]); idle(h);
  }
});

test('a rejected bootstrap has no automatic retry; the next explicit mutation can start a new bootstrap', async () => {
  let reads = 0; const failure = new Error('Synthetic bootstrap connection failure');
  const h = harness({ respond: path => { if (path !== '/api/session') return json({ ok: true }); if (++reads === 1) throw failure; return json(anonymous()); } });
  const call = request(h); await assert.rejects(call(ROOT, { method: 'POST' }), error => error === failure);
  assert.deepEqual(methods(h), [['/api/session', 'GET']]); idle(h);
  await call(ROOT, { method: 'POST' });
  assert.deepEqual(methods(h), [['/api/session', 'GET'], ['/api/session', 'GET'], [ROOT, 'POST']]); idle(h);
});

test('POST 403 with anonymous-required invalidates only future bootstrap state, never replays the POST', async () => {
  let sessions = 0, posts = 0;
  const h = harness({ respond: path => path === '/api/session' ? json(anonymous(NOW + 600_000, ++sessions === 1 ? CSRF : NEXT_CSRF))
    : ++posts === 1 ? json({ detail: 'Synthetic expired CSRF' }, 403, { 'X-Anonymous-Session': 'required' }) : json({ accepted: true }) });
  const call = request(h); await assert.rejects(call(ROOT, { method: 'POST', body: '{"intent":1}' }), error => error.status === 403);
  assert.deepEqual(methods(h), [['/api/session', 'GET'], [ROOT, 'POST']]); idle(h);
  await call(ROOT, { method: 'POST', body: '{"intent":2}' });
  assert.deepEqual(methods(h), [['/api/session', 'GET'], [ROOT, 'POST'], ['/api/session', 'GET'], [ROOT, 'POST']]);
  assert.equal(h.calls.at(-1).init.headers.get('X-CSRF-Token'), NEXT_CSRF); assert.equal(posts, 2); idle(h);
});

test('ordinary forbidden POST does not clear a valid CSRF cache or fabricate recovery requests', async () => {
  const h = harness({ respond: path => path === '/api/session' ? json(anonymous()) : json({ detail: 'Synthetic access denial' }, 403) }), call = request(h);
  await assert.rejects(call(ROOT, { method: 'POST' }), error => error.status === 403);
  assert.deepEqual(methods(h), [['/api/session', 'GET'], [ROOT, 'POST']]);
  await assert.rejects(call(ROOT, { method: 'POST' }), error => error.status === 403);
  assert.deepEqual(methods(h), [['/api/session', 'GET'], [ROOT, 'POST'], [ROOT, 'POST']]); idle(h);
});

test('cleared-session epochs fence old pending receipts without letting their cleanup discard a newer bootstrap', async () => {
  const old = deferred(), latest = deferred(); let reads = 0;
  const h = harness({ respond: path => path === '/api/session' ? (++reads === 1 ? old.promise : latest.promise) : json({ ok: true }) }), call = request(h);
  const obsolete = assert.rejects(call(ROOT, { method: 'POST' }), isAbort);
  h.api.clearBrowserSession(); const current = call(ROOT, { method: 'POST' }); assert.equal(reads, 2);
  old.resolve(json(anonymous())); await obsolete;
  const peer = call(ROOT, { method: 'POST' }); assert.equal(reads, 2, 'Old finally/catch cannot replace the new pending promise.');
  latest.resolve(json(anonymous(NOW + 600_000, NEXT_CSRF))); await Promise.all([current, peer]);
  assert.equal(h.calls.filter(row => row.path === ROOT).length, 2);
  assert.ok(h.calls.filter(row => row.path === ROOT).every(row => row.init.headers.get('X-CSRF-Token') === NEXT_CSRF)); idle(h);
});

for (const scope of ['access', 'request']) {
  test(`${scope} cancellation rejects only that pending caller while another caller retains the shared bootstrap`, { timeout: 5000 }, async () => {
    const hold = deferred(), h = harness({ respond: path => path === '/api/session' ? hold.promise : json({ ok: true }) });
    const stopped = new AbortController(), surviving = new AbortController();
    const first = request(h, scope === 'access' ? { signal: stopped.signal } : {})(ROOT,
      { method: 'POST', ...(scope === 'request' ? { signal: stopped.signal } : {}) });
    const rejected = assert.rejects(first, isAbort);
    const second = request(h, { signal: surviving.signal })(ROOT, { method: 'POST' });
    const bootstrapSignal = h.calls[0].init.signal; stopped.abort();
    try {
      await rejected; assert.equal(bootstrapSignal.aborted, false); assert.equal(surviving.signal.aborted, false);
      assert.deepEqual(methods(h), [['/api/session', 'GET']], 'The cancelled caller settles before bootstrap is released.');
    } finally { hold.resolve(json(anonymous())); await second; }
    assert.equal(h.calls.filter(row => row.path === ROOT).length, 1);
    noAbortListeners(stopped.signal); noAbortListeners(surviving.signal); idle(h);
  });
}

for (const scope of ['access', 'request', 'upload']) {
  test(`already-aborted ${scope} never starts session bootstrap, task fetch or XHR`, async () => {
    const h = harness(), controller = new AbortController(); controller.abort();
    const pending = scope === 'upload' ? h.api.uploadTask(submission(), controller.signal, () => assert.fail('Cancelled upload progress'))
      : request(h, scope === 'access' ? { signal: controller.signal } : {})(ROOT, { method: 'POST', ...(scope === 'request' ? { signal: controller.signal } : {}) });
    await assert.rejects(pending, isAbort); assert.equal(h.calls.length, 0); assert.equal(h.xhrs.length, 0);
    noAbortListeners(controller.signal); idle(h);
  });
}

test('cancelling every waiter does not poison a shared pending bootstrap for a later explicit caller', async () => {
  const hold = deferred(), h = harness({ respond: path => path === '/api/session' ? hold.promise : json({ ok: true }) });
  const controllers = [new AbortController(), new AbortController()];
  const rejected = controllers.map(controller => assert.rejects(request(h, { signal: controller.signal })(ROOT, { method: 'POST' }), isAbort));
  controllers.forEach(controller => controller.abort()); await Promise.all(rejected);
  assert.equal(h.calls[0].init.signal.aborted, false);
  const later = request(h)(ROOT, { method: 'POST' }); assert.equal(h.calls.length, 1);
  hold.resolve(json(anonymous())); await later; assert.equal(h.calls.length, 2); idle(h);
});

test('task fetch cancellation composes access and per-request signals and removes both listeners', async () => {
  for (const scope of ['access', 'request']) {
    const h = harness({ respond: (_path, init) => pendingFetch(init.signal) }), access = new AbortController(), caller = new AbortController();
    const pending = request(h, { signal: access.signal })(ROOT, { signal: caller.signal });
    const rejected = assert.rejects(pending, isAbort); (scope === 'access' ? access : caller).abort(); await rejected;
    assert.equal(h.calls[0].init.signal.aborted, true); assert.equal(h.calls.length, 1);
    noAbortListeners(access.signal); noAbortListeners(caller.signal); idle(h);
  }
});

test('a caller deadline during bootstrap cancels only its wait, not the shared security-session fetch', async () => {
  const hold = deferred(), h = harness({ respond: path => path === '/api/session' ? hold.promise : json({ ok: true }) });
  const first = request(h)(ROOT, { method: 'POST' }), rejected = assert.rejects(first, isAbort);
  const ownDeadline = [...h.timers.keys()][0]; assert.equal(h.timers.get(ownDeadline).delay, 30_000);
  const second = request(h)(ROOT, { method: 'POST' }); h.fireTimer(ownDeadline); await rejected;
  assert.equal(h.calls[0].init.signal.aborted, false);
  hold.resolve(json(anonymous())); await second; assert.equal(h.calls.filter(row => row.path === ROOT).length, 1); idle(h);
});

test('public request deadlines and caller cancellation abort their own fetch and clean up listeners', async () => {
  for (const reason of ['caller', 'deadline']) {
    const h = harness({ respond: (_path, init) => pendingFetch(init.signal) }), controller = new AbortController();
    const rejected = assert.rejects(h.api.publicRequest('/api/config/limits', controller.signal), isAbort);
    assert.equal(h.timers.size, 1); const [id, timer] = [...h.timers][0]; assert.equal(timer.delay, 30_000);
    if (reason === 'caller') controller.abort(); else h.fireTimer(id);
    await rejected; assert.equal(h.calls[0].init.signal.aborted, true); noAbortListeners(controller.signal); idle(h);
  }
});

test('multipart task requests get a bounded 30-minute deadline rather than the JSON 30-second deadline', async () => {
  const reached = deferred(), h = harness({ respond: (path, init) => {
    if (path === '/api/session') return json(development());
    reached.resolve(init); return pendingFetch(init.signal);
  } });
  const form = new FormData(); form.append('payload', new Blob(['synthetic']));
  const pending = request(h)(ROOT, { method: 'POST', body: form }), rejected = assert.rejects(pending, isAbort);
  const sent = await reached.promise; assert.equal(sent.body, form);
  assert.equal(h.timers.size, 1, 'The bootstrap finished before exercising the task transport deadline.');
  const entry = [...h.timers][0]; assert.equal(entry[1].delay, 30 * 60_000);
  h.fireTimer(entry[0]); await rejected;
  assert.equal(sent.signal.aborted, true); assert.equal(h.calls.filter(row => row.path === ROOT).length, 1); idle(h);
});

test('task media uses exact selected capability/revision or a truly token-free local path, without network calls', () => {
  const h = harness();
  assert.equal(h.api.taskMedia(TASK), `${ROOT}/video?revision=0`);
  assert.equal(h.api.taskMedia(TASK, TOKEN, 4, true), `${ROOT}/video?revision=4&token=${TOKEN}&download=true`);
  assert.equal(h.api.taskMedia(TASK, undefined, 9, true), `${ROOT}/video?revision=9&download=true`);
  assert.equal(h.calls.length, 0); assert.equal(h.store.state.reads, 0);
});

test('task media rejects malformed identifiers, capabilities and unsafe revisions instead of encoding credentials into an unsafe path', () => {
  const h = harness();
  for (const taskId of ['', '../other', 'task/other', 't'.repeat(129)]) assert.throws(() => h.api.taskMedia(taskId, TOKEN));
  for (const token of ['', null, 't'.repeat(31), 't'.repeat(257), `${TOKEN}?`]) assert.throws(() => h.api.taskMedia(TASK, token));
  for (const revision of [-1, 1.5, NaN, Infinity, '4', Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => h.api.taskMedia(TASK, TOKEN, revision));
});

test('uploadTask sends exact multipart files/preferences/precise asset options and no classroom fields, returning a nonempty capability', async () => {
  const h = harness({ respond: onlySession(anonymous()) }), controller = new AbortController();
  const ownVoice = new File([Uint8Array.of(9, 8, 7)], 'synthetic-voice.wav', { type: 'audio/wav' });
  const value = submission({ ownVoice, class_id: 'retired', classroom: { student_id: 'retired' }, identity: 'retired', access_token: OTHER_TOKEN });
  const before = JSON.stringify({ preferences: value.preferences, asset_options: value.asset_options });
  const pending = h.api.uploadTask(value, controller.signal, () => {}), xhr = await h.sent;
  assert.equal(xhr.method, 'POST'); assert.equal(xhr.path, '/api/tasks'); assert.equal(xhr.withCredentials, true); assert.equal(xhr.timeout, 30 * 60_000);
  assert.equal(xhr.sends, 1); assert.ok(xhr.body instanceof FormData);
  assert.deepEqual([...xhr.body.keys()], ['script', 'script_format', 'preferences', 'asset_options', 'files', 'files', 'own_voice']);
  assert.equal(xhr.body.get('script'), value.script); assert.equal(xhr.body.get('script_format'), 'headline_first');
  assert.deepEqual(JSON.parse(xhr.body.get('preferences')), value.preferences);
  assert.deepEqual(JSON.parse(xhr.body.get('asset_options')), value.asset_options);
  const files = xhr.body.getAll('files'); assert.equal(files[0], value.files[0]); assert.equal(files[1], value.files[1]); assert.equal(xhr.body.get('own_voice'), ownVoice);
  assert.deepEqual([...xhr.headers], [['accept', 'application/json'], ['x-csrf-token', CSRF]]);
  assert.equal(xhr.headers.has('Content-Type'), false, 'The browser, not appApi, supplies the multipart boundary.');
  // Native serialization is local bytes only, not a network request or fake server.
  const encoded = new Request(`${ORIGIN}/api/tasks`, { method: 'POST', body: xhr.body });
  assert.match(encoded.headers.get('Content-Type'), /^multipart\/form-data; boundary=/);
  const wire = await encoded.text(); assert.doesNotMatch(wire, /name="(?:classroom|class_id|student_id|identity|access_token)"/);
  xhr.respond({ payload: receipt({ identity: 'discarded', classroom: 'discarded' }) });
  const result = await pending; assert.deepEqual(plain(result), receipt()); assert.ok(result.access_token.length >= 32);
  assert.equal(before, JSON.stringify({ preferences: value.preferences, asset_options: value.asset_options }));
  assert.equal(h.store.state.writes, 0); assert.deepEqual(methods(h), [['/api/session', 'GET']]); assertXhrFinished(xhr, controller.signal); idle(h);
});

test('upload without own voice omits that part and development does not invent task/session credentials', async () => {
  const h = harness({ respond: onlySession() }), controller = new AbortController();
  const pending = h.api.uploadTask(submission(), controller.signal, () => {}), xhr = await h.sent;
  assert.equal(xhr.body.has('own_voice'), false); assert.deepEqual([...xhr.headers], [['accept', 'application/json']]);
  xhr.respond(); assert.equal((await pending).access_token, TOKEN); assertXhrFinished(xhr, controller.signal); idle(h);
});

test('upload rejects absent/empty/malformed returned capabilities without retry, storage write or fabricated success', async () => {
  for (const access_token of [undefined, null, '', 't'.repeat(31), 't'.repeat(257), `${TOKEN}/`]) {
    const h = harness({ respond: onlySession() }), controller = new AbortController();
    const rejected = assert.rejects(h.api.uploadTask(submission(), controller.signal, () => {}), /回执/), xhr = await h.sent;
    xhr.respond({ payload: receipt({ access_token }) }); await rejected;
    assert.equal(h.xhrs.length, 1); assert.equal(xhr.sends, 1); assert.equal(h.calls.length, 1); assert.equal(h.store.state.writes, 0);
    assertXhrFinished(xhr, controller.signal); idle(h);
  }
});

test('upload waits on the same pending CSRF bootstrap as task writes and can cancel without cancelling its peer', { timeout: 5000 }, async () => {
  const hold = deferred(), h = harness({ respond: path => path === '/api/session' ? hold.promise : json({ ok: true }) });
  const controller = new AbortController(), progress = [];
  const rejected = assert.rejects(h.api.uploadTask(submission(), controller.signal, item => progress.push(item)), isAbort);
  const peer = request(h)(ROOT, { method: 'POST' }); assert.equal(h.calls.length, 1); controller.abort();
  try {
    await rejected; assert.equal(h.calls[0].init.signal.aborted, false); assert.equal(h.xhrs.length, 0); assert.equal(progress.length, 0);
  } finally { hold.resolve(json(anonymous())); await peer; }
  assert.equal(h.calls.filter(row => row.path === ROOT).length, 1); noAbortListeners(controller.signal); idle(h);
});

test('upload 403 invalidates anonymous cache but neither replays XHR nor automatically refreshes; next explicit caller bootstraps', async () => {
  let reads = 0;
  const h = harness({ respond: path => path === '/api/session' ? json(anonymous(NOW + 600_000, ++reads === 1 ? CSRF : NEXT_CSRF)) : json({ ok: true }) });
  const controller = new AbortController(), rejected = assert.rejects(h.api.uploadTask(submission(), controller.signal, () => {}), error => error.status === 403);
  const xhr = await h.sent; xhr.respond({ status: 403, payload: { detail: 'Synthetic expired session' }, headers: { 'X-Anonymous-Session': 'required' } }); await rejected;
  assert.equal(reads, 1); assert.equal(h.xhrs.length, 1); assert.equal(xhr.sends, 1);
  await request(h)(ROOT, { method: 'POST' }); assert.equal(reads, 2); assert.equal(h.calls.at(-1).init.headers.get('X-CSRF-Token'), NEXT_CSRF);
  assertXhrFinished(xhr, controller.signal); idle(h);
});

for (const event of ['onerror', 'ontimeout']) {
  test(`upload ${event} reports an uncertain outcome without replaying bytes or losing caller data`, async () => {
    const h = harness({ respond: onlySession() }), controller = new AbortController(), value = submission();
    const rejected = assert.rejects(h.api.uploadTask(value, controller.signal, () => {}), /作品列表/), xhr = await h.sent;
    xhr[event](); await rejected;
    assert.equal(h.xhrs.length, 1); assert.equal(xhr.sends, 1); assert.equal(xhr.body.getAll('files')[0], value.files[0]);
    assert.equal(h.store.state.writes, 0); assertXhrFinished(xhr, controller.signal); idle(h);
  });
}

test('upload rejects unexpected response redirects across origin, path or query and never resubmits', async () => {
  for (const url of ['https://outside.invalid/api/tasks', `${ORIGIN}/api/tasks/other`, `${ORIGIN}/api/tasks?redirected=1`]) {
    const h = harness({ respond: onlySession() }), controller = new AbortController();
    const rejected = assert.rejects(h.api.uploadTask(submission(), controller.signal, () => {}), /跳转/), xhr = await h.sent;
    xhr.respond({ url }); await rejected;
    assert.equal(h.xhrs.length, 1); assert.equal(xhr.sends, 1); assertXhrFinished(xhr, controller.signal); idle(h);
  }
});

test('upload progress is aggregate multipart bytes with actual event clocks, not invented per-file completion', async () => {
  const h = harness({ respond: onlySession() }), controller = new AbortController(), observations = [];
  const pending = h.api.uploadTask(submission(), controller.signal, item => observations.push(plain(item))), xhr = await h.sent;
  assert.deepEqual(observations, [{ loaded: 0, total: null, startedAt: NOW, finishedAt: null }]);
  xhr.upload.onprogress({ loaded: 17, total: 999, lengthComputable: false });
  h.clock.now += 5; xhr.upload.onprogress({ loaded: 123, total: 234, lengthComputable: true });
  h.clock.now += 8; xhr.upload.onload();
  assert.deepEqual(observations, [
    { loaded: 0, total: null, startedAt: NOW, finishedAt: null },
    { loaded: 17, total: null, startedAt: NOW, finishedAt: null },
    { loaded: 123, total: 234, startedAt: NOW, finishedAt: null },
    { loaded: 123, total: 234, startedAt: NOW, finishedAt: NOW + 13 },
  ]);
  xhr.respond(); await pending; assert.equal(observations.length, 4, 'A server receipt must not fabricate a final byte count.');
  assertXhrFinished(xhr, controller.signal); idle(h);
});

test('in-flight upload aborts only its XHR and removes all upload/response handlers before rejecting', async () => {
  const h = harness({ respond: onlySession() }), controller = new AbortController();
  const rejected = assert.rejects(h.api.uploadTask(submission(), controller.signal, () => {}), isAbort), xhr = await h.sent;
  controller.abort(); await rejected;
  assert.equal(xhr.aborts, 1); assert.equal(xhr.sends, 1); assert.equal(h.xhrs.length, 1); assertXhrFinished(xhr, controller.signal); idle(h);
});

test('malformed upload JSON and incompatible receipts fail once and never masquerade as accepted work', async () => {
  for (const response of [{ text: '<html>synthetic proxy error</html>' }, { payload: { task_id: TASK, access_token: TOKEN, revision: -1 } },
    { payload: { task_id: TASK, access_token: TOKEN, status: 'accepted' } }, { payload: null }]) {
    const h = harness({ respond: onlySession() }), controller = new AbortController();
    const rejected = assert.rejects(h.api.uploadTask(submission(), controller.signal, () => {})), xhr = await h.sent;
    xhr.respond(response); await rejected;
    assert.equal(h.xhrs.length, 1); assert.equal(xhr.sends, 1); assert.equal(h.store.state.writes, 0); assertXhrFinished(xhr, controller.signal); idle(h);
  }
});

const failedTask = (patch = {}) => ({ task_id: TASK, mode: 'original', status: 'failed', progress: 55,
  revision: 0, stages: [{ number: 6, name: 'Synthetic match', status: 'failed' }],
  current_stage: 6, error_message: 'Synthetic missing quote', errorKind: 'quote_missing', badRows: [1], ...patch });

test('task GET and fresh-client reload preserve quote failure metadata without session or mutation', async () => {
  for (const mode of ['original', 'mixed']) {
    const value = failedTask({ mode });
    for (let read = 0; read < 2; read++) {
      const h = harness({ respond: (path, init) => {
        assert.equal(path, ROOT); assert.equal(init.method ?? 'GET', 'GET'); return json(value);
      } });
      assert.deepEqual(plain(h.api.parseTask(await request(h)(ROOT))), value);
      assert.deepEqual(methods(h), [[ROOT, 'GET']]); assert.equal(h.store.state.writes, 0); idle(h);
    }
  }
});

test('task failure metadata rejects malformed values rather than coercing or silently filtering', () => {
  const h = harness();
  for (const errorKind of ['', 'unknown', 1, false, [], ['quote_missing'], {}]) {
    assert.throws(() => h.api.parseTask(failedTask({ errorKind })));
  }
  for (const badRows of [null, false, 1, '1', {}, [0], [-1], [1.5], ['1'], [true], [null], [NaN],
    [Infinity], [100_001], [Number.MAX_SAFE_INTEGER + 1], Array(2049).fill(1)]) {
    assert.throws(() => h.api.parseTask(failedTask({ badRows })));
  }
});

test('legacy task metadata may be absent and valid modern metadata is preserved without invented defaults', () => {
  const h = harness(), legacy = failedTask(); delete legacy.mode; delete legacy.errorKind; delete legacy.badRows;
  assert.deepEqual(plain(h.api.parseTask(legacy)), legacy);
  for (const errorKind of [null, 'network', 'transient', 'shortage', 'qc', 'quote_missing']) {
    for (const badRows of [[], [1], [100_000], [2, 1, 2], Array(2048).fill(1)]) {
      const value = failedTask({ errorKind, badRows });
      assert.deepEqual(plain(h.api.parseTask(value)), value);
    }
  }
  for (const patch of [{ errorKind: undefined }, { badRows: undefined }]) {
    assert.doesNotThrow(() => h.api.parseTask(failedTask(patch)));
  }
});

// Actual Processing + mode definitions, React SSR only: no DOM, host or browser.
function processingRenderer() {
  const load = (relative, dependencies) => {
    const result = ts.transpileModule(readFileSync(new URL(relative, import.meta.url), 'utf8'), {
      fileName: relative, reportDiagnostics: true,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      transformers: { before: [context => root => {
        const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
          && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
          ? ts.factory.createIdentifier('__testViteEnv') : ts.visitEachChild(node, visit, context);
        return ts.visitNode(root, visit);
      }] },
    });
    assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
    const module = { exports: {} };
    new vm.Script(result.outputText).runInNewContext({ module, exports: module.exports, __testViteEnv: { DEV: false },
      require(name) { assert.ok(Object.hasOwn(dependencies, name), `Known SSR dependency: ${name}`); return dependencies[name]; },
    }, { timeout: 1000 });
    return module.exports;
  };
  const modes = load('../src/lib/productionModes.ts', {
    '../../../backend/mode_rules.json': JSON.parse(readFileSync(new URL('../../backend/mode_rules.json', import.meta.url), 'utf8')),
  });
  const failureDetail = load('../src/lib/failureDetail.ts', {});
  const Processing = load('../src/components/Processing.tsx', {
    'react/jsx-runtime': jsxRuntime, '../lib/productionModes': modes, '../lib/failureDetail': failureDetail,
    './Processing.module.css': { default: {} },
  }).default;
  const blocked = () => assert.fail('SSR never invokes task actions');
  return task => renderToStaticMarkup(jsxRuntime.jsx(Processing, { task, upload: null, uploading: false,
    now: NOW, busy: false, onCancel: blocked, onRetry: blocked, onEdit: blocked, onRefresh: blocked }));
}

test('parsed quote failure renders exact heading and hides retry on initial read and fresh reload', () => {
  const render = processingRenderer();
  for (const mode of ['original', 'mixed']) for (let read = 0; read < 2; read++) {
    const html = render(harness().api.parseTask(failedTask({ mode })));
    assert.ok(html.includes('<h2>有 1 句原话在素材里没找到</h2>'));
    assert.ok(html.includes('请核对第 1 句原话。'));
    assert.ok(html.includes('回去改第 1 句'));
    assert.ok(html.includes('回去多传素材'));
    assert.ok(!html.includes('用原素材重新制作'));
  }
});

test('legacy failure retains generic retry while missing row metadata never invents a sentence count', () => {
  const render = processingRenderer(), legacy = failedTask(); delete legacy.errorKind; delete legacy.badRows;
  const html = render(harness().api.parseTask(legacy));
  assert.ok(html.includes('<h2>这次没做成</h2>')); assert.ok(html.includes('用原素材重新制作'));
  const quote = render(harness().api.parseTask(failedTask({ badRows: undefined })));
  assert.ok(quote.includes('<h2>有原话在素材里没找到</h2>'));
  assert.ok(!quote.includes('用原素材重新制作'));
});