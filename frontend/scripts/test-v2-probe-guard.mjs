import assert from 'node:assert/strict';
import { test as nodeTest } from 'node:test';
import vm from 'node:vm';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { File, Blob } from 'node:buffer';
import ts from 'typescript';

// Offline, memory-only wire recording; complete actual TS upload/transport and
// actual V2Run class. No host, provider, build, environment or user-data reads.
// All credentials below are STATIC synthetic data. Never print requests,
// source text, exception payloads, credentials, DOM or capability-bearing URLs.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = name => readFileSync(resolve(root, name), 'utf8');
const TASK = 'a'.repeat(32), OTHER = 'b'.repeat(32), TOKEN = 'STATIC_SYNTHETIC_ONLY_'.padEnd(43, 'x');
const fid = i => 'up_' + i.toString(16).padStart(32, '0');
const base = i => `/api/tasks/${TASK}/files/${fid(i)}`;
const plain = value => JSON.parse(JSON.stringify(value));
const check = (condition, message = 'STATIC invariant') => { if (!condition) throw Error(message); };
function test(name, fn) {
  nodeTest(name, async () => { try { await fn(); } catch { throw Error('STATIC probe-guard regression failed: ' + name); } });
}
function compile(source, filename = 'fixture.ts') {
  const result = ts.transpileModule(source, { fileName: filename, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    transformers: { before: [context => tree => {
      const visit = n => ts.isPropertyAccessExpression(n) && ts.isMetaProperty(n.expression) && n.name.text === 'env'
        ? ts.factory.createObjectLiteralExpression() : ts.visitEachChild(n, visit, context);
      return ts.visitNode(tree, visit);
    }] } });
  check(!result.diagnostics?.some(d => d.category === ts.DiagnosticCategory.Error));
  return result.outputText;
}
const support = read('e2e/v2/support.ts');
const ast = ts.createSourceFile('support.ts', support, ts.ScriptTarget.Latest, true);
const runClass = ast.statements.find(n => ts.isClassDeclaration(n) && n.name.text === 'V2Run');
check(runClass);
function run(design = false) {
  const exports = {}, evidence = [];
  vm.runInNewContext(compile(runClass.getText(ast)), { exports, env: { V2_DESIGN_ONLY: design ? '1' : '0' },
    WRITE: new Set(['POST', 'PUT', 'PATCH', 'DELETE']), ID: /^[a-f0-9]{32}$/,
    invariant: check, URL, BASE_URL: 'https://synthetic.invalid', pageErrorFacts: () => ({ static: true }),
    bound: () => true });
  const instance = new exports.V2Run({ instanceId: 'STATIC' }, {}, {});
  instance.captureTask({ id: TASK, task_id: TASK, access_token: TOKEN, status: 'draft' });
  instance.captureTask({ id: OTHER, task_id: OTHER, access_token: TOKEN, status: 'draft' });
  instance.evidence = (_name, value) => evidence.push(value);
  instance.status = async () => ({ status: 'draft', revision: 0 });
  instance.identity = async () => ({ deniedEgress: 0, providerCalls: {}, uploadASRCalls: {} });
  return { instance, evidence };
}
function receipt(i, bytes = 3) {
  return { id: fid(i), file_id: fid(i), up_id: fid(i), upload_id: fid(i), token: TOKEN, access_token: TOKEN,
    status: 'uploading', bytes, chunk_size: 8388608, put_url: base(i) + '/chunks/{index}' };
}
function snapshot(i, patch = {}) {
  return { id: fid(i), file_id: fid(i), name: `fixture-${i}.mkv`, bytes: 3, sec: 1, width: 64, height: 48, fps: 25,
    status: 'uploading', probe_ok: true, metadata_only: true, can_materialize: false, chunks: [0],
    has_speech: null, transcript: { segments: [], speakers: [] }, progress: 40,
    asr_confidence: null, thumb_url: null, wave_url: null, ...patch };
}
function admit(m, i = 1, bytes = 3) {
  m.admitFiles(1); assert.equal(m.authorize('POST', `/api/tasks/${TASK}/files`), true);
  m.captureFile(TASK, receipt(i, bytes));
}
function probe(m, i = 1) {
  assert.equal(m.authorize('PUT', base(i) + '/chunks/0'), true);
  assert.equal(m.authorize('POST', base(i) + '/probe', {}), true);
  m.captureProbe(base(i) + '/probe', snapshot(i));
}

for (const design of [false, true]) {
  const label = design ? 'design' : 'default';
  test(`${label}: exact owned phases each once, independent remaining records`, () => {
    const { instance: m } = run(design);
    for (let i = 1; i <= 7; i++) admit(m, i);
    for (let i = 1; i <= 7; i++) {
      assert.equal(m.authorize('POST', base(i) + '/complete', {}), false);
      probe(m, i);
    }
    for (let i = 1; i <= 7; i++) {
      assert.equal(m.authorize('POST', base(i) + '/complete', {}), true);
      for (const phase of ['probe', 'complete', 'pretranscribe']) assert.equal(m.authorize('POST', base(i) + '/' + phase, {}), false);
      assert.equal(m.authorize('PUT', base(i) + '/chunks/0'), false);
      assert.equal(m.authorize('GET', base(i)), true);
    }
  });
  test(`${label}: unknown and cross-task files never acquire POST or PUT authority`, () => {
    const { instance: m } = run(design); admit(m);
    for (const path of [base(2), base(1).replace(TASK, OTHER), `/api/tasks/${TASK}/nested/files/${fid(1)}`, `/api/uploads/${fid(1)}`]) {
      for (const phase of ['probe', 'complete', 'pretranscribe', 'start', 'align', 'draft'])
        for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) assert.equal(m.authorize(method, path + '/' + phase, {}), false);
      assert.equal(m.authorize('PUT', path + '/chunks/0'), false);
    }
  });
  test(`${label}: wrong methods bodies aliases and chunk indices cannot spend valid slots`, () => {
    const { instance: m } = run(design); admit(m);
    for (const method of ['POST', 'PATCH', 'DELETE']) assert.equal(m.authorize(method, base(1) + '/chunks/0'), false);
    for (const index of ['00', '-1', '1', '0/extra', '9007199254740992']) assert.equal(m.authorize('PUT', base(1) + '/chunks/' + index), false);
    assert.equal(m.authorize('PUT', base(1) + '/chunks/0'), true);
    for (const body of [undefined, null, [], 'STATIC', { retry: true }, { sec: 1 }])
      assert.equal(m.authorize('POST', base(1) + '/probe', body), false);
    for (const method of ['PUT', 'PATCH', 'DELETE']) assert.equal(m.authorize(method, base(1) + '/probe', {}), false);
    for (const suffix of ['/probe/', '/probe/complete', '/complete/align', '/chunks/0/complete'])
      assert.equal(m.authorize('POST', base(1) + suffix, {}), false);
    assert.equal(m.authorize('POST', base(1) + '/probe', {}), true);
  });
  test(`${label}: failed or ambiguous probe never refunds or permits completion`, () => {
    const { instance: m } = run(design); admit(m);
    assert.equal(m.authorize('PUT', base(1) + '/chunks/0'), true);
    assert.equal(m.authorize('POST', base(1) + '/probe', {}), true);
    for (const patch of [{ id: fid(2) }, { bytes: 4 }, { width: null }, { fps: 0 }, { sec: Infinity },
      { probe_ok: false }, { metadata_only: false }, { can_materialize: true }, { chunks: [] }, { status: 'ready' }]) {
      assert.throws(() => m.captureProbe(base(1) + '/probe', snapshot(1, patch)));
      assert.equal(m.authorize('POST', base(1) + '/complete', {}), false);
    }
    assert.equal(m.authorize('POST', base(1) + '/probe', {}), false);
    assert.throws(() => m.captureFile(TASK, receipt(1)));
  });
  test(`${label}: bounded chunks require every index and attempted complete cannot replay`, () => {
    const { instance: m } = run(design); admit(m, 1, 8388609);
    assert.equal(m.authorize('PUT', base(1) + '/chunks/1'), true);
    assert.equal(m.authorize('POST', base(1) + '/probe', {}), false);
    assert.equal(m.authorize('PUT', base(1) + '/chunks/0'), true);
    assert.equal(m.authorize('POST', base(1) + '/probe', {}), true);
    m.captureProbe(base(1) + '/probe', snapshot(1, { bytes: 8388609, chunks: [0, 1] }));
    assert.equal(m.authorize('POST', base(1) + '/complete', {}), true);
    assert.equal(m.authorize('POST', base(1) + '/complete', {}), false);
    assert.throws(() => m.captureProbe(base(1) + '/probe', snapshot(1, { bytes: 8388609, chunks: [0, 1] })));
  });
}
test('unsolicited receipts and recovered ready files grant no upload phase budgets', () => {
  const { instance: m } = run(true);
  assert.throws(() => m.captureFile(TASK, receipt(1)));
  m.captureFile(TASK, { ...receipt(1), status: 'ready' }, true);
  for (const phase of ['probe', 'complete', 'pretranscribe']) assert.equal(m.authorize('POST', base(1) + '/' + phase, {}), false);
  assert.equal(m.authorize('PUT', base(1) + '/chunks/0'), false);
  assert.throws(() => m.captureFile(TASK, { ...receipt(1), status: 'ready' }, true));
});
test('partial phase attempts survive thrown actions in sanitized safety evidence', async () => {
  const { instance: m, evidence } = run(); admit(m, 1); admit(m, 2); probe(m, 1); probe(m, 2);
  assert.equal(m.authorize('POST', base(1) + '/complete', {}), true);
  // A thrown transport action leaves its one attempt spent, not forgotten.
  await m.finish();
  assert.deepEqual(plain(evidence[0].uploadPhaseAttempts).map(({ chunks, probe, probeConfirmed, complete }) =>
    ({ chunks, probe, probeConfirmed, complete })), [
    { chunks: 1, probe: 1, probeConfirmed: true, complete: 1 },
    { chunks: 1, probe: 1, probeConfirmed: true, complete: 0 }]);
  assert.equal(JSON.stringify(evidence).includes(TOKEN), false);
  assert.equal(evidence[0].rawBodiesHeadersCookiesURLsDOMRecorded, false);
});

async function wire({ design = false, failure, malformed = false } = {}) {
  const { instance: m } = run(design), modules = new Map(), records = new Map(), calls = [];
  let responseHandler, routeHandler;
  const page = { on(name, fn) { if (name === 'response') responseHandler = fn; } };
  await m.guard({ route(_pattern, fn) { routeHandler = fn; }, routeWebSocket() {}, pages: () => [page], on() {} });
  const signal = new AbortController();
  const scope = { draftTaskId: TASK, draftTaskToken: TOKEN };
  async function fetch(url, init = {}) {
    const method = init.method ?? 'GET', pathname = new URL(url, 'https://synthetic.invalid').pathname;
    const request = { url: () => 'https://synthetic.invalid' + pathname, method: () => method,
      headers: () => Object.fromEntries(init.headers ?? []), postDataJSON: () => JSON.parse(init.body) };
    let allowed = false;
    await routeHandler({ request: () => request, continue: async () => { allowed = true; }, abort: async () => {} });
    check(allowed, 'STATIC blocked wire');
    calls.push({ path: pathname, method });
    if (pathname === '/api/session') return Response.json({ access_mode: 'development', csrf_token: null, expires_at: null });
    check(init.headers.get('X-Task-Token') === TOKEN);
    let value, status = 200;
    if (pathname === `/api/tasks/${TASK}/files`) {
      const body = JSON.parse(init.body), i = records.size + 1;
      records.set(i, snapshot(i, { name: body.name, bytes: body.size, sha256: body.sha256, sec: null, width: null, height: null, fps: null,
        chunks: [], probe_ok: false, metadata_only: false }));
      value = receipt(i, body.size); status = 201;
    } else {
      const i = [...records.keys()].find(i => pathname === base(i) || pathname.startsWith(base(i) + '/'));
      check(i); const state = records.get(i);
      if (method === 'PUT') { state.chunks = [0]; value = {}; }
      else if (pathname.endsWith('/probe')) {
        if (failure === 'probe') { status = 503; value = { detail: 'STATIC failure' }; }
        else {
          Object.assign(state, { sec: 1, width: 64, height: 48, fps: 25, probe_ok: true, metadata_only: true });
          value = { ...state, ...(malformed ? { fps: null } : {}) };
        }
      } else if (pathname.endsWith('/complete')) {
        Object.assign(state, { status: 'ready', metadata_only: false, can_materialize: true });
        if (failure === 'complete') throw Error('STATIC ambiguous completion');
        value = state;
      } else { check(method === 'GET'); value = state; }
    }
    const detached = plain(value);
    responseHandler({ url: request.url, request: () => request, status: () => status,
      headers: () => ({ 'x-v2-acceptance': 'STATIC' }), json: async () => detached });
    return Response.json(detached, { status });
  }
  const globals = { URL, URLSearchParams, Headers, Response, FormData, File, Blob, AbortController, DOMException,
    Uint8Array, Uint32Array, DataView, setTimeout, clearTimeout, location: { origin: 'https://synthetic.invalid' }, fetch };
  function load(relative) {
    const filename = resolve(root, relative);
    if (modules.has(filename)) return modules.get(filename);
    if (filename.endsWith('.json')) return JSON.parse(readFileSync(filename, 'utf8'));
    const module = { exports: {} }; modules.set(filename, module.exports);
    vm.runInNewContext(compile(readFileSync(filename, 'utf8'), filename), { ...globals, module, exports: module.exports,
      require(name) { check(name.startsWith('.')); const target = resolve(dirname(filename), name);
        const found = [target, target + '.ts', target + '.json'].find(existsSync); check(found); return load(found); } });
    return module.exports;
  }
  const uploads = load('src/lib/uploadSessions.ts'), media = load('src/lib/mediaInput.ts');
  const sessions = [], states = [];
  async function transfer(i) {
    m.admitFiles(1);
    return uploads.uploadFile(new File([Uint8Array.of(1, 2, 3)], `fixture-${i}.mkv`, { lastModified: i }), signal.signal,
      { onSession: s => sessions.push(s), onSnapshot: s => states.push(s) }, undefined, scope, true);
  }
  return { m, uploads, media, records, calls, sessions, states, signal, scope, transfer };
}
for (const design of [false, true]) test(`actual TS wire ${design ? 'design' : 'default'}: all seven records probe then complete once; reload GET-only`, async () => {
  const h = await wire({ design });
  for (let i = 1; i <= 7; i++) { const s = await h.transfer(i); assert.equal(s.metadata_only, true); }
  assert.equal(h.calls.some(c => c.path.endsWith('/complete')), false);
  for (const session of h.sessions) {
    const s = await h.uploads.completeUpload(session.upload_id, TOKEN, h.signal.signal, session);
    assert.equal(h.uploads.uploadUsable(s), true);
  }
  const before = h.calls.length;
  for (const session of h.sessions) for (let n = 0; n < 2; n++) {
    await h.uploads.uploadFile(undefined, h.signal.signal, { onSession() { check(false); }, onSnapshot() {} }, session, h.scope, true);
    await h.uploads.pollUploadStatus(session.upload_id, TOKEN, h.signal.signal, () => {}, session);
  }
  assert.ok(h.calls.slice(before).every(c => c.method === 'GET'));
  for (let i = 1; i <= 7; i++) for (const phase of ['probe', 'complete'])
    assert.equal(h.calls.filter(c => c.path === base(i) + '/' + phase).length, 1);
  assert.ok(h.calls.every(c => !/pretranscribe|start|apply|exports|recording/.test(c.path)));
});
for (const failure of ['probe', 'complete']) test(`actual TS wire: ${failure} exception stops without automatic write retry`, async () => {
  const h = await wire({ failure });
  if (failure === 'probe') await assert.rejects(h.transfer(1));
  else {
    await h.transfer(1);
    const s = h.sessions[0];
    await assert.rejects(h.uploads.completeUpload(s.upload_id, TOKEN, h.signal.signal, s));
  }
  const before = h.calls.length, s = h.sessions[0];
  await h.uploads.pollUploadStatus(s.upload_id, TOKEN, h.signal.signal, () => {}, s);
  assert.ok(h.calls.slice(before).every(c => c.method === 'GET'));
  assert.equal(h.calls.filter(c => c.path.endsWith('/' + failure)).length, 1);
  assert.equal(h.m.authorize('POST', base(1) + '/' + failure, {}), false);
  assert.equal(h.calls.some(c => /pretranscribe|start|recording/.test(c.path)), false);
});
test('actual TS wire rejects missing authoritative fps without completing or exposing capability', async () => {
  const h = await wire({ malformed: true }); await assert.rejects(h.transfer(1));
  assert.equal(h.calls.some(c => c.path.endsWith('/complete')), false);
  assert.equal(h.m.authorize('POST', base(1) + '/complete', {}), false);
  assert.ok(h.calls.every(c => !c.path.includes(TOKEN)));
});
test('actual scoped parser strict replay; unscoped legacy omission remains accepted', async () => {
  const h = await wire(), scope = { ...h.scope, server_file_id: fid(1) };
  const value = snapshot(1, { status: 'ready', metadata_only: false, can_materialize: true });
  for (const key of ['width', 'height', 'fps', 'probe_ok']) {
    const missing = { ...value }; delete missing[key];
    assert.throws(() => h.uploads.parseUploadSnapshot(missing, fid(1), scope));
    assert.equal(h.uploads.parseUploadSnapshot(missing, fid(1)).status, 'ready');
  }
  for (const key of ['width', 'height', 'fps']) for (const bad of [0, -1, NaN, Infinity, '25'])
    assert.throws(() => h.uploads.parseUploadSnapshot({ ...value, [key]: bad }, fid(1), scope));
});

const wizardSource = read('src/components/CreateWizard.tsx');
const wizardAst = ts.createSourceFile('CreateWizard.tsx', wizardSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function declaration(name) {
  const found = [];
  const visit = n => { if (ts.isFunctionDeclaration(n) && n.name?.text === name) found.push(n); ts.forEachChild(n, visit); };
  visit(wizardAst); check(found.length === 1); return found[0].getText(wizardAst);
}
async function queue(scenario) {
  const h = await wire();
  const files = [1, 2, 3].map(i => {
    const file = new File([Uint8Array.of(1, 2, 3)], `fixture-${i}.mkv`, { lastModified: i });
    return { id: h.media.fileIdentity(file), file, name: file.name, size: 3, kind: 'video', duration: null,
      status: 'loading', note: 'STATIC', trim_start: 0, trim_end: null };
  });
  const controllers = files.map(() => new AbortController()), sessions = new Map(), uploadsRef = { current: {} };
  const operations = { current: new Map(files.map((f, i) => [f.id, controllers[i]])) };
  const jobs = { current: files.map((f, i) => ({ itemId: f.id, file: f.file, controller: controllers[i], probe: true })) };
  const filesRef = { current: files }, completed = [], observed = [], blocks = []; let errors = {}, failOnce = true;
  const limits = { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 };
  const context = { exports: {}, filesRef, uploadsRef, operations, jobs, mounted: { current: true }, transferRunning: { current: false },
    initialDraft: {}, draftAccessRef: { current: h.scope }, draftController: { current: null }, limits, cap: h.media.mediaLimits(limits),
    urls: { current: new Set() }, URL, releaseUrl() {}, setNotice() {}, setHashProgress() {}, setDraftAccess() {}, setUploads() {},
    setUploadErrors(fn) { errors = fn(errors); }, errorMessage: () => 'STATIC action failed', uploadErrorText: () => scenario === 'capacity' ? 'REASON' : 'STATIC action failed',
    isCapacityFailure: error => scenario === 'capacity' && error?.status === 429, setUploadBlock(value) { blocks.push(value); },
    sessionFor: key => sessions.get(key), rememberSession(s) { sessions.set(s.file_id, s); },
    updateFile(key, patch) { Object.assign(filesRef.current.find(f => f.id === key), patch); },
    reconcileServerProbe: h.uploads.reconcileServerProbe, transferComplete: h.uploads.transferComplete,
    canServerProbe: h.media.canServerProbe,
    async probeMedia(file) {
      if (scenario === 'last-invalid' && file.name === 'fixture-3.mkv' && failOnce) {
        failOnce = false; throw new h.media.BrowserProbeError('invalid', 'STATIC invalid');
      }
      throw new h.media.BrowserProbeError('unsupported', 'STATIC unsupported');
    },
    async uploadFile(file, signal, callbacks, resume, scope, metadataOnly) {
      if (scenario === 'capacity' && file.name === 'fixture-2.mkv') throw Object.assign(Error('quota'), { status: 429 });
      if (!resume) h.m.admitFiles(1);
      return h.uploads.uploadFile(file, signal, callbacks, resume, scope, metadataOnly);
    },
    async completeUpload(id, token, signal, scope) {
      if (['complete-failure', 'failed-peer'].includes(scenario) && completed.length === 1 && failOnce) {
        failOnce = false; throw Error('STATIC transport stopped before dispatch');
      }
      const result = await h.uploads.completeUpload(id, token, signal, scope);
      completed.push(id);
      if (scenario === 'cancel-completing') controllers[2].abort();
      if (scenario === 'cancel-peer') controllers[0].abort();
      if (scenario === 'failed-peer' && completed.length === 1) return { ...result, status: 'failed', failure_kind: 'asr' };
      return result;
    },
    observeUpload(key) { observed.push(key); },
  };
  vm.runInNewContext(compile(declaration('publishSnapshot') + '\n' + declaration('drainFileJobs')
    + '\nthis.drain = drainFileJobs;'), context);
  await context.drain();
  async function manual() {
    const f = files.at(-1), controller = new AbortController();
    operations.current.set(f.id, controller);
    jobs.current.push({ itemId: f.id, file: f.file, controller, probe: false });
    await context.drain();
  }
  return { h, context, files, completed, observed, blocks, errors: () => errors, manual, jobs, operations };
}
function wizardConsts(...names) {
  return names.map(name => {
    const found = [];
    const visit = n => { if (ts.isVariableStatement(n) && n.declarationList.declarations.some(d => d.name.getText(wizardAst) === name)) found.push(n); ts.forEachChild(n, visit); };
    visit(wizardAst); check(found.length === 1); return found[0].getText(wizardAst);
  }).join('\n');
}
test('upload failures are explained in plain words by status; nothing raw is echoed and only capacity refusals pause the queue', () => {
  const sandbox = {}; vm.runInNewContext(compile(wizardConsts('errorMessage', 'errorStatus', 'isCapacityFailure') + '\n' + declaration('uploadErrorText')
    + '\nthis.text = uploadErrorText; this.capacity = isCapacityFailure; this.generic = errorMessage;'), sandbox);
  const err = (status, message = '') => Object.assign(new Error(message), { status });
  assert.match(sandbox.text(err(429, '你保存的素材已达上限（最多 100 个文件，合计不超过 20 GiB）。')), /你保存的素材已达上限.*请先移除不需要的素材.*我的作品/s);
  assert.match(sandbox.text(err(413, 'Task upload budget exceeded')), /素材总量超过了服务器允许的上限/);
  assert.doesNotMatch(sandbox.text(err(413, 'Task upload budget exceeded')), /Task upload budget/);
  assert.match(sandbox.text(err(507, 'Insufficient shared upload/task storage')), /服务器磁盘空间不足/);
  assert.doesNotMatch(sandbox.text(err(507, 'Insufficient shared upload/task storage')), /Insufficient/);
  assert.equal(sandbox.text(err(429, 'x'.repeat(300))), sandbox.generic(), 'an oversized message is never shown');
  for (const other of [err(500, 'boom'), err(404, 'x'), new Error('network down'), 'text', null, undefined]) assert.equal(sandbox.text(other), sandbox.generic());
  for (const capacity of [429, 507, 413]) assert.equal(sandbox.capacity(err(capacity)), true, String(capacity));
  for (const other of [err(500), err(401), err(404), err(409), err(422), new Error('x'), null, undefined, 'x', {}]) assert.equal(sandbox.capacity(other), false);
});
test('a capacity refusal pauses the queue: later files are not attempted one by one, each says why, and the reason is kept', async () => {
  const q = await queue('capacity');
  const [first, second, third] = q.files;
  assert.equal(q.jobs.current.length, 0, 'nothing stays queued behind a refusal');
  assert.equal(q.operations.current.has(second.id), false); assert.equal(q.operations.current.has(third.id), false, 'the dropped job no longer holds its slot');
  assert.equal(q.errors()[first.id], undefined, 'the file before the refusal went through');
  assert.equal(q.errors()[second.id], 'REASON');
  assert.equal(q.errors()[third.id], '没有开始上传：REASON');
  assert.deepEqual(plain(q.blocks), ['', 'REASON'], 'the pause reason is published; the earlier success had cleared any older one');
  assert.equal(q.h.calls.some(c => c.path.includes('fixture-3')), false, 'the third file never reached the server');
  assert.equal(q.context.uploadsRef.current && Object.keys(q.context.uploadsRef.current).length, 1);
  // Explicit retry of a dropped file works as before (it is just a new job).
  q.operations.current.set(third.id, new AbortController());
  q.jobs.current.push({ itemId: third.id, file: third.file, controller: q.operations.current.get(third.id), probe: false });
  await q.context.drain();
  assert.equal(q.jobs.current.length, 0);
});
test('actual wizard queue plus actual TS upload: last invalid file leaves metadata-only peers; explicit continuation progresses all', async () => {
  const q = await queue('last-invalid');
  assert.equal(q.completed.length, 0);
  assert.equal(q.h.calls.filter(c => c.path.endsWith('/probe')).length, 2);
  assert.equal(Object.keys(q.errors()).length, 1);
  assert.ok(Object.values(q.context.uploadsRef.current).every(s => s.metadata_only && s.status === 'uploading'));
  await q.manual();
  assert.equal(q.completed.length, 3);
  assert.equal(q.h.calls.filter(c => c.path.endsWith('/probe')).length, 3);
  assert.equal(q.h.calls.filter(c => c.path.endsWith('/complete')).length, 3);
});
test('actual wizard queue: completion error preserves remaining metadata-only peers for explicit continuation without re-probe', async () => {
  const q = await queue('complete-failure');
  assert.equal(q.completed.length, 1); assert.equal(Object.keys(q.errors()).length, 1);
  await q.manual();
  assert.equal(q.completed.length, 3);
  assert.equal(q.h.calls.filter(c => c.path.endsWith('/probe')).length, 3);
  assert.equal(q.h.calls.filter(c => c.path.endsWith('/complete')).length, 3);
});
test('actual wizard queue: abort after first complete prevents later peer dispatch, does not undo accepted server work', async () => {
  const q = await queue('cancel-completing');
  assert.equal(q.completed.length, 1);
  assert.equal(q.h.calls.filter(c => c.path.endsWith('/complete')).length, 1);
  assert.equal(q.observed.length, 0);
  assert.equal(q.h.records.get(1).status, 'ready');
  assert.equal(q.context.uploadsRef.current[fid(1)].status, 'uploading');
  assert.equal(q.context.transferRunning.current, false);
});
test('fixed product boundary: bounded ASR-failed peer permits explicit continuation without retrying ASR', async () => {
  const q = await queue('failed-peer');
  assert.equal(q.completed.length, 1);
  assert.equal(q.context.uploadsRef.current[fid(1)].status, 'failed');
  assert.equal(q.context.uploadsRef.current[fid(2)].metadata_only, true);
  const before = q.h.calls.length;
  await q.manual();
  assert.equal(q.completed.length, 3);
  assert.equal(q.context.uploadsRef.current[fid(1)].status, 'failed');
  assert.equal(q.h.calls.filter(c => c.path === base(1) + '/complete').length, 1);
  assert.equal(q.h.calls.slice(before).filter(c => c.method === 'POST').length, 2);
  assert.equal(q.h.calls.some(c => c.path.endsWith('/pretranscribe')), false);
});
test('documented cancellation boundary: peer old controller is not the batch completion signal', async () => {
  const q = await queue('cancel-peer');
  assert.equal(q.completed.length, 3);
  assert.equal(q.h.calls.filter(c => c.path.endsWith('/complete')).length, 3);
});
test('static UI exposes explicit uploading continuation and disables only active file operations', () => {
  const button = [];
  const visit = n => {
    if (ts.isJsxElement(n) && n.openingElement.tagName.getText(wizardAst) === 'button'
      && n.getText(wizardAst).includes('retryFile(item.id)')) button.push(n.getText(wizardAst));
    ts.forEachChild(n, visit);
  };
  visit(wizardAst); check(button.length === 1);
  assert.ok(button[0].includes('disabled={operations.current.has(item.id)}'));
  assert.ok(button[0].includes('upload?.status === "uploading" && (item.file || transferComplete(upload))'));
  assert.ok(button[0].includes('\u6838\u5bf9\u5206\u7247\u5e76\u7ee7\u7eed'));
});