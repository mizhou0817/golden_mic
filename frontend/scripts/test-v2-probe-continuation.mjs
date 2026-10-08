import assert from 'node:assert/strict';
import { test as nodeTest } from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import ts from 'typescript';

// Actual source-extracted queue/retry/publication + actual upload library.
// Synthetic in-memory HTTP only; no acceptance-budget changes, disk output,
// browser, service, models or environment reads. Failures never dump payloads.
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const source = read('../src/components/CreateWizard.tsx');
const ast = ts.createSourceFile('CreateWizard.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const plain = x => JSON.parse(JSON.stringify(x));
const TASK = 'synthetic_continuation', TOKEN = 'static_synthetic_continuation_'.padEnd(43, 'x');
const access = { draftTaskId: TASK, draftTaskToken: TOKEN };
const limits = { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 };
function test(name, fn) {
  nodeTest(name, async () => { try { await fn(); } catch { throw Error('STATIC continuation assertion: ' + name); } });
}
function declaration(name) {
  const found = [];
  const walk = n => { if (ts.isFunctionDeclaration(n) && n.name?.text === name) found.push(n); ts.forEachChild(n, walk); };
  walk(ast); assert.equal(found.length, 1); return found[0].getText(ast);
}
const compile = code => ts.transpileModule(code, { fileName: 'fixture.tsx', compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
} }).outputText;
function load(code, deps = {}) {
  const exports = {};
  vm.runInNewContext(compile(code), { exports, require: p => { assert.ok(p in deps); return deps[p]; },
    URL, URLSearchParams, Headers, AbortController, DOMException, Uint8Array, Uint32Array, DataView, setTimeout, clearTimeout });
  return exports;
}
const media = load(read('../src/lib/mediaInput.ts'));
const modes = load(read('../src/lib/productionModes.ts'), { '../../../backend/mode_rules.json': JSON.parse(read('../../backend/mode_rules.json')) });
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
async function fixture({ firstFailed = true, earlyInvalid = false } = {}) {
  const calls = [], states = new Map(), sessions = new Map(), reads = [], observations = [];
  const files = [1, 2, 3].map(i => ({ id: JSON.stringify([`synthetic-${i}.mkv`, 3, i]), name: `synthetic-${i}.mkv`,
    size: 3, lastModified: i, type: '', kind: 'video', duration: 10, note: `note-${i}`, trim_start: 1, trim_end: 8, status: 'loading' }));
  for (const [i, f] of files.entries()) {
    const id = `up_${i + 1}`;
    sessions.set(f.id, { file_id: f.id, upload_id: id, server_file_id: id, ...access,
      access_token: TOKEN, sha256: 'a'.repeat(64), chunk_size: 8388608, put_url: `/api/tasks/${TASK}/files/${id}/chunks/{index}` });
    states.set(id, { id, name: f.name, bytes: 3, sec: 10, width: 64, height: 48, fps: 25,
      status: firstFailed && i === 0 ? 'asr_failed' : 'uploading', probe_ok: true,
      metadata_only: !(firstFailed && i === 0), can_materialize: firstFailed && i === 0,
      chunks: [0], has_speech: null, transcript: [], asr_confidence: null, thumb_url: null, wave_url: null, progress: 100 });
  }
  let held, hook, uncertain, errors = {}, readGate;
  const u = load(read('../src/lib/uploadSessions.ts'), { './mediaInput': media, './productionModes': modes,
    './appApi': { createAppRequest: auth => async (url, init = {}) => {
      assert.equal(auth.taskId, TASK); assert.equal(auth.token, TOKEN);
      if (auth.signal.aborted) throw new DOMException('STATIC abort', 'AbortError');
      const match = /^\/api\/tasks\/synthetic_continuation\/files\/(up_[123])(\/complete)?$/.exec(url);
      assert.ok(match, 'No other endpoints permitted');
      const [, id, phase] = match, method = init.method ?? 'GET';
      calls.push({ id, method, phase: phase ?? '', body: init.body });
      if (!phase) {
        assert.equal(method, 'GET'); reads.push(id);
        if (readGate) { const gate = readGate; readGate = undefined; gate.enter.resolve(); await gate.exit.promise; }
        return plain(states.get(id));
      }
      assert.equal(method, 'POST'); assert.equal(init.body, '{}');
      if (uncertain?.id === id) {
        const failure = uncertain; uncertain = undefined;
        if (failure.committed) Object.assign(states.get(id), { status: 'ready', metadata_only: false, can_materialize: true });
        throw Error('STATIC uncertain');
      }
      if (held) { const gate = held; held = undefined; gate.enter.resolve(); await gate.exit.promise; }
      Object.assign(states.get(id), { status: 'ready', metadata_only: false, can_materialize: true });
      hook?.(id);
      return {};
    } } });
  const context = { exports: {}, ...u, limits, cap: media.mediaLimits(limits), initialDraft: {}, locked: false,
    filesRef: { current: files }, uploadsRef: { current: {} }, jobs: { current: [] }, operations: { current: new Map() },
    polling: { current: new Map() }, removalCancels: { current: new Map() }, mounted: { current: true }, transferRunning: { current: false },
    draftAccessRef: { current: access }, draftController: { current: null }, AbortController,
    sessionFor: id => sessions.get(id), setHashProgress() {}, setUploads() {}, setDraftAccess() {},
    setUploadErrors(fn) { errors = fn(errors); }, errorMessage: () => 'STATIC failed', uploadErrorText: () => 'STATIC failed', isCapacityFailure: () => false, setUploadBlock() {},
    updateFile(id, patch) { const f = context.filesRef.current.find(f => f.id === id); assert.ok(f); Object.assign(f, patch); },
    observeUpload(id) { observations.push(id); }, canServerProbe: media.canServerProbe,
    urls: { current: new Set() }, URL, releaseUrl() {}, setNotice() {},
    async probeMedia() { throw new media.BrowserProbeError('invalid', 'STATIC invalid'); },
  };
  vm.runInNewContext(compile(['publishSnapshot', 'drainFileJobs', 'retryFile'].map(declaration).join('\n')
    + '\nthis.drain = drainFileJobs; this.retry = retryFile; this.publish = publishSnapshot;'), context);
  for (const f of files) { const s = sessions.get(f.id); context.publish(f.id, u.parseUploadSnapshot(states.get(s.upload_id), s.upload_id, s)); }
  if (earlyInvalid) {
    const f = files[2], controller = new AbortController();
    context.operations.current.set(f.id, controller);
    context.jobs.current.push({ itemId: f.id, file: {}, controller, probe: true });
    await context.drain();
    assert.equal(Object.keys(errors).length, 1); assert.equal(calls.length, 0);
  }
  async function settle() {
    for (let n = 0; n < 300 && context.transferRunning.current; n++) await Promise.resolve();
    assert.equal(context.transferRunning.current, false);
  }
  function retry(index = 2) { context.retry(files[index].id); }
  function hold(kind) { const gate = { enter: deferred(), exit: deferred() }; if (kind === 'read') readGate = gate; else held = gate; return gate; }
  return { context, files, sessions, states, calls, reads, observations, u, retry, settle, hold,
    errors: () => errors, posts: () => calls.filter(c => c.method === 'POST'),
    hook: fn => { hook = fn; }, uncertain: (id, committed) => { uncertain = { id, committed }; },
    refresh() { for (const f of context.filesRef.current) { const s = sessions.get(f.id); context.publish(f.id, u.parseUploadSnapshot(states.get(s.upload_id), s.upload_id, s)); } } };
}

test('source identity receipt (before and after runs are distinct, no baseline masking)', () => {
  console.log(JSON.stringify({ continuationWizardSHA256: createHash('sha256').update(source).digest('hex'), syntheticOnly: true }));
});
test('last-file failure then actual retry continues metadata-only peers without File and retains failed ASR', async () => {
  const h = await fixture({ earlyInvalid: true }); const authored = h.files.map(({ id, note, trim_start, trim_end }) => ({ id, note, trim_start, trim_end }));
  h.retry(); await h.settle();
  assert.deepEqual(h.posts().map(p => p.id), ['up_2', 'up_3']);
  assert.equal(h.context.uploadsRef.current.up_1.status, 'failed');
  assert.equal(h.context.uploadsRef.current.up_1.failure_kind, 'asr');
  assert.deepEqual(h.files.map(({ id, note, trim_start, trim_end }) => ({ id, note, trim_start, trim_end })), authored);
  assert.ok(h.files.every(f => !f.file));
});
test('ordinary no-failure queue continues all peers once and subsequent ready retry is read-only', async () => {
  const h = await fixture({ firstFailed: false }); h.retry(); await h.settle();
  assert.deepEqual(h.posts().map(p => p.id), ['up_1', 'up_2', 'up_3']);
  h.retry(); await h.settle(); assert.equal(h.posts().length, 3);
});
test('uploading without authoritative metadata-only phase never receives complete', async () => {
  const h = await fixture(); h.context.uploadsRef.current.up_2.metadata_only = false;
  // Reconciliation is authoritative: remove the phase from its actual GET too.
  h.states.get('up_2').metadata_only = false;
  // Retry another file: no /probe permission or invented phase is available.
  h.retry(); await h.settle(); assert.deepEqual(h.posts().map(p => p.id), ['up_3']);
});
for (const committed of [false, true]) test(`uncertain completion committed=${committed}: next explicit attempt GETs peer first, no automatic replay`, async () => {
  const h = await fixture(); h.uncertain('up_2', committed); h.retry(); await h.settle();
  assert.deepEqual(h.posts().map(p => p.id), ['up_2']);
  const before = h.calls.length; await h.settle(); assert.equal(h.calls.length, before);
  h.retry(); await h.settle();
  assert.deepEqual(h.posts().map(p => p.id), committed ? ['up_2', 'up_3'] : ['up_2', 'up_2', 'up_3']);
  const after = h.calls.slice(before); assert.equal(after[0].method, 'GET');
  assert.ok(after.findIndex(c => c.id === 'up_2' && c.method === 'GET') < after.findIndex(c => c.method === 'POST'));
});
for (const status of ['ready', 'probing', 'transcribing']) test(`already ${status} peer never receives another complete`, async () => {
  const h = await fixture(); Object.assign(h.states.get('up_2'), { status, metadata_only: false, can_materialize: true }); h.refresh();
  h.retry(); await h.settle(); assert.deepEqual(h.posts().map(p => p.id), ['up_3']);
});
for (const [label, patch] of [
  ['media failure', { status: 'failed' }], ['interrupted failure', { status: 'interrupted' }],
  ['unknown probe', { probe_ok: false }], ['not materializable', { can_materialize: false }],
  ['unknown duration', { sec: null, probe_ok: false }], ['invalid fps', { fps: 0 }],
]) test(`${label} failed peer blocks all completion`, async () => {
  const h = await fixture(); Object.assign(h.states.get('up_1'), patch);
  h.retry(); await h.settle(); assert.equal(h.posts().length, 0);
});
for (const mutation of ['bytes', 'total-bytes', 'duration', 'total-duration', 'unknown-duration']) test(`before every complete revalidates full current ${mutation}, never trimmed duration`, async () => {
  const h = await fixture(); h.hook(id => {
    if (id !== 'up_2') return;
    if (mutation === 'bytes') h.files[0].size = h.context.cap.maxBytes + 1;
    if (mutation === 'total-bytes') h.context.cap.maxTotalBytes = 8;
    if (mutation === 'duration') h.files[0].duration = 1801;
    if (mutation === 'total-duration') { h.files[0].duration = 1795; h.context.cap.maxTotalDuration = 1800; }
    if (mutation === 'unknown-duration') h.files[0].duration = null;
  });
  h.retry(); await h.settle(); assert.deepEqual(h.posts().map(p => p.id), ['up_2']);
});
for (const mutation of ['abort', 'generation', 'unmount', 'remove', 'filter-pending', 'binding', 'task', 'reorder']) test(`held complete fences ${mutation} before adopting response or dispatching later peers`, async () => {
  const h = await fixture(), gate = h.hold(); h.retry(); await gate.enter.promise;
  const controller = h.context.operations.current.get(h.files[2].id);
  if (mutation === 'abort') controller.abort();
  if (mutation === 'generation') h.context.operations.current.set(h.files[2].id, new AbortController());
  if (mutation === 'unmount') h.context.mounted.current = false;
  if (mutation === 'remove') h.context.filesRef.current = h.files.slice(1);
  if (mutation === 'filter-pending') h.context.removalCancels.current.set(h.files[0].id, () => {});
  if (mutation === 'binding') h.sessions.get(h.files[0].id).server_file_id = 'foreign';
  if (mutation === 'task') h.context.draftAccessRef.current = { ...access, draftTaskToken: 'different' };
  if (mutation === 'reorder') h.context.filesRef.current = [...h.files].reverse();
  gate.exit.resolve(); await h.settle();
  assert.deepEqual(h.posts().map(p => p.id), ['up_2']);
  assert.equal(h.context.uploadsRef.current.up_2.status, 'uploading');
  if (mutation === 'generation') assert.notEqual(h.context.operations.current.get(h.files[2].id), controller);
});
test('held reconciliation plus pending removal sends no complete, duplicate explicit click cannot enqueue', async () => {
  const h = await fixture(), gate = h.hold('read'); h.retry(); await gate.enter.promise;
  h.retry(); assert.equal(h.context.jobs.current.length, 0);
  h.context.removalCancels.current.set(h.files[0].id, () => {}); gate.exit.resolve(); await h.settle();
  assert.equal(h.posts().length, 0);
});
for (const mutation of ['abort', 'generation', 'binding']) test(`held GET fences ${mutation} without publication or any POST`, async () => {
  const h = await fixture(), gate = h.hold('read'); h.retry(); await gate.enter.promise;
  const original = h.context.uploadsRef.current.up_1;
  if (mutation === 'abort') h.context.operations.current.get(h.files[2].id).abort();
  if (mutation === 'generation') h.context.operations.current.set(h.files[2].id, new AbortController());
  if (mutation === 'binding') h.sessions.get(h.files[0].id).sha256 = 'b'.repeat(64);
  gate.exit.resolve(); await h.settle();
  assert.equal(h.posts().length, 0); assert.equal(h.calls.length, 1);
  assert.equal(h.context.uploadsRef.current.up_1, original);
});
test('pending removal at entry refuses retry without even a GET', async () => {
  const h = await fixture(); h.context.removalCancels.current.set(h.files[0].id, () => {});
  h.retry(); await h.settle(); assert.equal(h.calls.length, 0);
});
test('queued continuation invalidated before drain releases only its owned controller without API', async () => {
  const h = await fixture(); h.context.transferRunning.current = true; h.retry();
  assert.equal(h.context.jobs.current.length, 1);
  h.context.removalCancels.current.set(h.files[0].id, () => {});
  h.context.transferRunning.current = false; await h.context.drain();
  assert.equal(h.calls.length, 0); assert.equal(h.context.operations.current.size, 0);
});
test('failed-ASR own retry is observation only; refresh never grants POST', async () => {
  const h = await fixture(); h.retry(0); await h.settle(); h.refresh(); await h.settle();
  assert.equal(h.calls.length, 0); assert.deepEqual(h.observations, [h.files[0].id]);
});
test('actual continuation control stays visible without File and has truthful label; source/submit gates unchanged', () => {
  const buttons = [], effects = [];
  const walk = n => {
    if (ts.isJsxElement(n) && n.openingElement.tagName.getText(ast) === 'button' && n.getText(ast).includes('retryFile(item.id)')) buttons.push(n);
    if (ts.isCallExpression(n) && n.expression.getText(ast) === 'useEffect') effects.push(n.getText(ast));
    ts.forEachChild(n, walk);
  }; walk(ast); assert.equal(buttons.length, 1);
  const jsx = (type, props) => ({ type, props });
  for (const file of [undefined, {}]) {
    const button = vm.runInNewContext(compile('const button = (' + buttons[0].getText(ast) + ');button;'), {
      exports: {}, require: () => ({ jsx, jsxs: jsx }), item: { id: 'static', file }, upload: { status: 'uploading' },
      operations: { current: new Map() }, transferComplete: () => true, bindingFor: () => ({}), retryFile() {},
    });
    assert.equal(button.props.children, '\u6838\u5bf9\u5206\u7247\u5e76\u7ee7\u7eed'); assert.equal(button.props.disabled, false);
  }
  assert.ok(effects.every(text => !/completeUpload\(|retryFile\(|drainFileJobs\(/.test(text)));
  assert.ok(source.includes('(!draftAccess || uploadFor(itemId)?.status === "ready")'));
  assert.ok(source.includes('files.filter(item => !removing[item.id])'));
  assert.ok(!declaration('retryFile').includes('pretranscribe'));
});