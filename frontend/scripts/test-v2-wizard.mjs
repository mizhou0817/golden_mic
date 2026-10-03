import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { Blob, File } from 'node:buffer';
import ts from 'typescript';

// Actual TS/TSX compiled in memory. All network, storage, mic and media decoding
// boundaries are blocked or explicitly synthetic. No files written or services.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const rules = JSON.parse(read('../../backend/mode_rules.json'));
const paths = { api: '../src/lib/appApi.ts', draft: '../src/lib/draftTasks.ts', upload: '../src/lib/uploadSessions.ts',
  media: '../src/lib/mediaInput.ts', modes: '../src/lib/productionModes.ts', wizard: '../src/components/CreateWizard.tsx' };
const scripts = Object.fromEntries(Object.entries(paths).map(([name, path]) => {
  const result = ts.transpileModule(read(path), { fileName: path, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    transformers: { before: [context => root => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env' && ts.isMetaProperty(node.expression)
        ? ts.factory.createIdentifier('__viteEnv') : ts.visitEachChild(node, visit, context);
      return ts.visitNode(root, visit);
    }] } });
  assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
  return [name, new vm.Script(result.outputText)];
}));
const denied = () => assert.fail('Unexpected external side effect');
function load(name, deps = {}, globals = {}) {
  const module = { exports: {} };
  scripts[name].runInNewContext({ module, exports: module.exports, __viteEnv: {},
    URL, URLSearchParams, Headers, Response, FormData, Blob, File, AbortController, DOMException,
    Uint8Array, Uint32Array, DataView, setTimeout, clearTimeout, location: { origin: 'https://synthetic.invalid' },
    fetch: denied, XMLHttpRequest: denied, localStorage: { getItem: denied, setItem: denied }, navigator: {},
    require: key => { assert.ok(Object.hasOwn(deps, key), `Unbound import ${key}`); return deps[key]; }, ...globals,
  }, { timeout: 1000 });
  return module.exports;
}
const modes = load('modes', { '../../../backend/mode_rules.json': rules });
const media = load('media');
const TASK = 'synthetic_draft', UP = 'up_00000000000000000000000000000001', FID = 'file_synthetic';
const TOKEN = 'synthetic_task_token_'.padEnd(48, 'x'), UTOKEN = 'synthetic_upload_token_'.padEnd(48, 'y');
const access = { draftTaskId: TASK, draftTaskToken: TOKEN };
const root = `/api/tasks/${TASK}`, fileRoot = `${root}/files/${FID}`;
const receipt = () => ({ id: TASK, task_id: TASK, access_token: TOKEN, status: 'draft', expires_at: '2027-01-01T00:00:00Z' });
const sig = () => new AbortController().signal;
const plain = value => JSON.parse(JSON.stringify(value));
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
const ticks = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
function clock() {
  let now = 0, serial = 0; const timers = new Map();
  return { timers, setTimeout(fn, delay) { const id = ++serial; timers.set(id, { fn, at: now + delay, delay }); return id; },
    clearTimeout(id) { timers.delete(id); }, advance(ms) { now += ms; for (const [id, item] of [...timers]) if (item.at <= now) { timers.delete(id); item.fn(); } } };
}
function boundary(respond) { const calls = []; return { calls, createAppRequest(scope) { return async (path, init = {}) => {
  calls.push({ scope, path, init }); return respond(path, init, scope);
}; } }; }
const file = () => new File([Uint8Array.of(1, 2, 3)], 'synthetic.mp4', { lastModified: 123, type: 'video/mp4' });
const sha = '039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81';
const binding = () => ({ file_id: media.fileIdentity(file()), upload_id: UP, sha256: sha, chunk_size: 8388608,
  put_url: `${fileRoot}/chunks`, draftTaskId: TASK, server_file_id: FID });
const session = () => ({ ...binding(), draftTaskToken: TOKEN, access_token: TOKEN });
const metadata = () => ({ id: media.fileIdentity(file()), name: 'synthetic.mp4', size: 3, lastModified: 123,
  type: 'video/mp4', kind: 'video', duration: 10, note: '', trim_start: 0, trim_end: 10 });
const snapshot = patch => ({ id: UP, name: 'synthetic.mp4', bytes: 3, sec: 10, status: 'ready', has_speech: false,
  // Measured fields exposed by UploadStore._public and DraftStore.file_view.
  probe_ok: true, width: 1920, height: 1080, fps: 30, metadata_only: false, can_materialize: true, phase: 'done',
  thumb_url: null, wave_url: null, asr_confidence: null, transcript: [], progress: 100, chunks: [0], sha256: sha, ...patch });
const uploadModule = api => load('upload', { './appApi': api, './mediaInput': media, './productionModes': modes });

test('draft creation shares one promise across additions and never replays an ambiguous failure', async () => {
  const held = deferred(), api = boundary(() => held.promise), drafts = load('draft', { './appApi': api });
  const once = drafts.draftTaskOnce(), a = once('voiceover', sig()), b = once('mixed', sig());
  assert.equal(a, b); assert.equal(api.calls.length, 1); assert.deepEqual(JSON.parse(api.calls[0].init.body), { mode: 'voiceover' });
  held.resolve(receipt()); assert.deepEqual(plain(await a), { ...access, status: 'draft', expires_at: receipt().expires_at });
  assert.equal(once('original', sig()), a);
  const broken = boundary(() => Promise.reject(Error('synthetic uncertain commit')));
  const retry = load('draft', { './appApi': broken }).draftTaskOnce();
  await assert.rejects(retry('voiceover', sig())); await assert.rejects(retry('voiceover', sig())); assert.equal(broken.calls.length, 1);
});
test('draft capabilities reject partial and mismatched receipts', () => {
  const d = load('draft', { './appApi': boundary(denied) });
  assert.throws(() => d.readDraftAccess({ draftTaskId: TASK }));
  assert.throws(() => d.parseDraftReceipt({ ...receipt(), id: 'foreign' }));
  assert.throws(() => d.parseDraftReceipt({ ...receipt(), status: 'queued' }));
  assert.throws(() => d.readDraftAccess({ ...access, draftTaskId: '../outside' }));
});
test('refresh reads existing task and file list with task capability, never POST', async () => {
  const api = boundary(path => path === root ? receipt() : { files: [] });
  await load('draft', { './appApi': api }).readDraftTask(access, sig());
  assert.deepEqual(api.calls.map(c => c.path), [root, `${root}/files`]);
  for (const call of api.calls) { assert.equal(call.init.method, undefined); assert.equal(call.scope.token, TOKEN); }
});
test('five-second removal never deletes early; undo cancels only its own timer', () => {
  const c = clock(), d = load('draft', { './appApi': boundary(denied) }); let count = 0;
  const undo = d.deferredRemoval(() => count++, c.setTimeout, c.clearTimeout);
  c.advance(4999); assert.equal(count, 0); undo(); c.advance(1); assert.equal(count, 0);
  d.deferredRemoval(() => count++, c.setTimeout, c.clearTimeout); c.advance(5000); assert.equal(count, 1);
});
test('actual upload uses only task-bound init, GET, chunk, complete and DELETE routes', async () => {
  let chunks = [], done = false;
  const uploading = () => snapshot({ chunks, progress: chunks.length ? 100 : 0, status: 'uploading',
    sec: null, width: null, height: null, fps: null, probe_ok: false, can_materialize: false, phase: 'upload' });
  const api = boundary((path, init) => {
    if (path === `${root}/files`) {
      assert.deepEqual(JSON.parse(init.body), { name: 'synthetic.mp4', size: 3, sha256: sha, content_type: 'video/mp4' });
      // Actual DraftStore receipt: file view plus taskcap, NOT UploadStore cap.
      return { ...uploading(), up_id: UP, file_id: FID, token: TOKEN,
        chunk_size: 8388608, put_url: `${fileRoot}/chunks/{index}` };
    }
    if (init.method === 'PUT') { assert.equal(path, `${fileRoot}/chunks/0`); assert.equal(init.body.size, 3); chunks = [0]; return {}; }
    if (init.method === 'DELETE') return {};
    if (path.endsWith('/complete')) { assert.deepEqual(chunks, [0]); assert.equal(init.body, '{}'); done = true; return {}; }
    assert.equal(path, fileRoot); return done ? snapshot() : uploading();
  });
  const uploads = uploadModule(api); let saved;
  const result = await uploads.uploadFile(file(), sig(), { onSession: s => { saved = s; }, onSnapshot() {} }, undefined, access);
  assert.equal(result.status, 'ready'); assert.equal(saved.server_file_id, FID);
  assert.equal(saved.access_token, TOKEN);
  assert.equal(saved.file_id, media.fileIdentity(file())); assert.equal(saved.sha256, sha);
  await uploads.deleteUploadFile(saved, sig());
  assert.ok(api.calls.every(c => c.scope.taskId === TASK && c.scope.token === TOKEN && c.path.startsWith(root + '/')));
  assert.equal(api.calls.filter(c => c.path.endsWith('/complete')).length, 1);
  assert.deepEqual(api.calls.map(c => [c.init.method ?? 'GET', c.path]), [
    ['POST', `${root}/files`], ['GET', fileRoot], ['PUT', `${fileRoot}/chunks/0`], ['GET', fileRoot],
    ['POST', `${fileRoot}/complete`], ['GET', fileRoot], ['DELETE', fileRoot],
  ]);
});
test('explicit scoped metadata transfer probes once and only explicit complete grants preprocessing readiness', async () => {
  let chunks = [], probed = false, done = false, saved;
  const state = () => snapshot({ chunks, progress: chunks.length ? 100 : 0, status: done ? 'ready' : 'uploading',
    sec: probed ? 10 : null, width: probed ? 1920 : null, height: probed ? 1080 : null, fps: probed ? 30 : null,
    probe_ok: probed, metadata_only: probed && !done, can_materialize: done, phase: done ? 'done' : probed ? 'probe' : 'upload' });
  const api = boundary((path, init) => {
    if (path === `${root}/files`) {
      assert.equal(init.method, 'POST');
      assert.deepEqual(JSON.parse(init.body), { name: 'synthetic.mp4', size: 3, sha256: sha, content_type: 'video/mp4' });
      return { ...state(), up_id: UP, file_id: FID, token: TOKEN, chunk_size: 8388608, put_url: `${fileRoot}/chunks/{index}` };
    }
    if (path === `${fileRoot}/chunks/0`) {
      assert.equal(init.method, 'PUT'); assert.equal(init.body.size, 3); chunks = [0]; return {};
    }
    if (path === `${fileRoot}/probe`) {
      assert.equal(init.method, 'POST'); assert.equal(init.body, '{}'); assert.deepEqual(chunks, [0]);
      assert.equal(probed, false); assert.equal(done, false); probed = true; return state();
    }
    if (path === `${fileRoot}/complete`) {
      assert.equal(init.method, 'POST'); assert.equal(init.body, '{}'); assert.equal(probed, true);
      assert.equal(done, false); done = true; return {};
    }
    assert.equal(path, fileRoot); assert.equal(init.method, undefined); return state();
  });
  const u = uploadModule(api), callbacks = { onSession: s => { saved = s; }, onSnapshot() {} };
  const measured = await u.uploadFile(file(), sig(), callbacks, undefined, access, true);
  assert.equal(measured.status, 'uploading'); assert.equal(measured.metadata_only, true);
  assert.equal(u.uploadUsable(measured), false); assert.equal(done, false);
  assert.deepEqual(api.calls.map(c => [c.init.method ?? 'GET', c.path]), [
    ['POST', `${root}/files`], ['GET', fileRoot], ['PUT', `${fileRoot}/chunks/0`], ['GET', fileRoot], ['POST', `${fileRoot}/probe`],
  ]);
  const refreshed = await u.pollUploadStatus(UP, TOKEN, sig(), () => {}, saved);
  assert.equal(refreshed.metadata_only, true); assert.equal(done, false);
  assert.equal(api.calls.length, 6); assert.equal(api.calls[5].path, fileRoot); assert.equal(api.calls[5].init.method, undefined);
  const ready = await u.completeUpload(UP, TOKEN, sig(), saved);
  assert.equal(ready.status, 'ready'); assert.equal(ready.metadata_only, false); assert.equal(u.uploadUsable(ready), true);
  assert.deepEqual(api.calls.slice(6).map(c => [c.init.method ?? 'GET', c.path]), [['POST', `${fileRoot}/complete`], ['GET', fileRoot]]);
  assert.equal(api.calls.length, 8);
  assert.ok(api.calls.every(c => c.scope.taskId === TASK && c.scope.token === TOKEN));
});
test('scoped ready and probe-only fixtures reject incomplete authority without requests or legacy promotion', () => {
  const api = boundary(denied), u = uploadModule(api);
  for (const key of ['probe_ok', 'width', 'height', 'fps']) {
    const invalid = snapshot(); delete invalid[key];
    assert.throws(() => u.parseUploadSnapshot(invalid, UP, session()), key);
  }
  for (const patch of [{ probe_ok: false }, { fps: 0 }, { fps: Infinity }, { width: 1.5 }, { metadata_only: true },
    { status: 'uploading', metadata_only: true, can_materialize: true }]) {
    assert.throws(() => u.parseUploadSnapshot(snapshot(patch), UP, session()));
  }
  const unscoped = snapshot(); for (const key of ['probe_ok', 'width', 'height', 'fps']) delete unscoped[key];
  const before = plain(unscoped), parsed = u.parseUploadSnapshot(unscoped, UP);
  assert.equal(parsed.status, 'ready'); assert.equal(parsed.probe_ok, undefined); assert.equal(parsed.fps, undefined);
  assert.deepEqual(plain(unscoped), before); assert.equal(api.calls.length, 0);
});
test('bound restore reads only and rejects cross-task chunk destinations', async () => {
  const api = boundary(() => snapshot()), u = uploadModule(api);
  await u.uploadFile(undefined, sig(), { onSession: denied, onSnapshot() {} }, session());
  assert.equal(api.calls.length, 1); assert.equal(api.calls[0].init.method, undefined);
  assert.throws(() => u.readUploadBindings([{ ...binding(), put_url: '/api/tasks/foreign/files/file/chunks' }]));
  assert.throws(() => u.readUploadBindings([{ ...binding(), draftTaskId: undefined }]));
});
test('task file preview accepts only its own exact path and uses task—not upload—capability', () => {
  const u = uploadModule(boundary(denied));
  const s = u.parseUploadSnapshot(snapshot({ thumb_url: `${fileRoot}/thumb` }), UP, session());
  assert.equal(u.uploadMediaUrl(s, UTOKEN, 'thumb', session()), `${fileRoot}/thumb?token=${TOKEN}`);
  assert.throws(() => u.parseUploadSnapshot(snapshot({ thumb_url: `${fileRoot}/thumb?token=injected` }), UP, session()));
  assert.throws(() => u.parseUploadSnapshot(snapshot({ thumb_url: '/api/tasks/foreign/files/foreign/thumb' }), UP, session()));
});
test('all modes share task alignment results, with 600ms debounce and stale cancellation', async () => {
  const held = deferred(), api = boundary(() => held.promise), u = uploadModule(api), c = new AbortController();
  const sentence = { idx: 0, text: 'synthetic words', kind: 'quote' };
  const pending = u.requestMatchPreview([sentence], [UP], { [UP]: TOKEN }, c.signal, access,
    { mode: 'voiceover', script: 'Synthetic title\nsynthetic words' });
  assert.equal(api.calls[0].path, `${root}/align`); assert.equal(u.MATCH_DEBOUNCE_MS, 600);
  c.abort(); held.resolve({ matches: [{ idx: 0, kind: 'quote', score: 0, source: null, alt_takes: [] }] });
  await assert.rejects(pending, e => e.name === 'AbortError');
});
test('v2 selection is capped at 20 / 500MiB, images retain 50MiB and complete source EOF is not truncated', () => {
  const limits = { max_files: 100, max_upload_bytes: 1024 ** 3, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 };
  assert.equal(media.mediaLimits(limits).maxFiles, 20); assert.equal(media.mediaLimits(limits).maxBytes, 500 * 1024 ** 2);
  assert.ok(media.validateFileSelection({ name: 'image.png', size: 50 * 1024 ** 2 + 1 }, [], limits));
  assert.equal(media.validateTrim({ kind: 'video', duration: 10.4, trim_start: 0, trim_end: 10.4 }), null);
  assert.ok(media.validateTrim({ kind: 'video', duration: 10.4, trim_start: .5, trim_end: 10 }));
});

function apiHarness(respond) {
  const calls = [];
  const api = load('api', {}, { fetch: async (path, init = {}) => {
    calls.push({ path, init });
    const data = path === '/api/session' ? { access_mode: 'development', csrf_token: null, expires_at: null } : await respond(path, init);
    if (data instanceof Response) return data;
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
  } });
  return { api, calls };
}
const submission = patch => ({ ...access, mode: 'voiceover', script: 'Synthetic title\nSynthetic complete body.', files: [],
  preferences: modes.defaultModePreferences(), sentences: [{ idx: 0, text: 'Synthetic complete body', kind: 'narration' }],
  uploadIds: [UP], uploadTokens: { [UP]: TOKEN }, asset_options: [{ note: '', trim_start: 0, trim_end: 10 }], ...patch });
test('actual request strips injected task token and retains only scoped file upload capability', async () => {
  const h = apiHarness(() => ({}));
  await h.api.createAppRequest({ taskId: TASK, token: TOKEN, signal: sig() })(fileRoot, {
    headers: { 'X-Task-Token': 'injected', 'X-Upload-Token': UTOKEN },
  });
  assert.equal(h.calls[0].init.headers.get('X-Task-Token'), TOKEN);
  assert.equal(h.calls[0].init.headers.get('X-Upload-Token'), UTOKEN);
  await assert.rejects(h.api.createAppRequest({ taskId: TASK, token: TOKEN, signal: sig() })('/api/tasks/foreign/files'));
});
test('v2 start sends full preferences and normalizes receipt, no fresh task or legacy fallback', async () => {
  const h = apiHarness((path, init) => {
    assert.equal(path, `${root}/start`); assert.equal(init.headers.get('X-Task-Token'), TOKEN);
    const body = JSON.parse(init.body);
    assert.deepEqual(body.preferences, plain(modes.defaultModePreferences()));
    assert.deepEqual(Object.keys(body).sort(), ['mode', 'script', 'sentences', 'type_marks', 'speakers', 'preferences', 'asset_options'].sort());
    return { id: TASK, task_id: TASK, access_token: TOKEN, status: 'queued', queue: 1 };
  });
  const result = await h.api.uploadTask(submission(), sig(), () => {});
  assert.equal(result.task_id, TASK); assert.equal(result.status, 'queued');
  assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 1);
  await assert.rejects(h.api.uploadTask(submission({ draftTaskId: undefined, draftTaskToken: undefined }), sig(), denied));
  assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 1);
});
test('self-voice start is JSON without whole recording; original mode and new whole recordings fail closed', async () => {
  const ownVoice = new File(['synthetic'], 'voice.wav', { type: 'audio/wav' });
  const h = apiHarness((path, init) => {
    assert.equal(path, `${root}/start`); assert.equal(typeof init.body, 'string');
    assert.equal(JSON.parse(init.body).preferences.voice, 'mine');
    assert.equal(init.headers.get('Content-Type'), 'application/json');
    return { id: TASK, task_id: TASK, access_token: TOKEN, status: 'queued', queue: 1 };
  });
  await h.api.uploadTask(submission({ preferences: { ...modes.defaultModePreferences(), voice: 'mine' } }), sig(), () => {});
  await assert.rejects(h.api.uploadTask(submission({ ownVoice }), sig(), denied));
  await assert.rejects(h.api.uploadTask(submission({ mode: 'original', ownVoice }), sig(), denied));
});

// Deterministic hooks/events/ref adapter: real component code, not DOM/layout.
function wizard(draft, { respond = denied, uploadOverrides = {}, ready = true, confirm = () => true, onSubmit = denied } = {}) {
  const c = clock(), hooks = [], effects = [], drafts = [], requests = boundary(respond); let cursor = 0, dirty = true, tree, focuses = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = { useId: () => 'test-wizard',
    useState(seed) { const at = cursor++; hooks[at] ??= { value: typeof seed === 'function' ? seed() : seed }; return [hooks[at].value, next => {
      const value = typeof next === 'function' ? next(hooks[at].value) : next; if (!Object.is(value, hooks[at].value)) { hooks[at].value = value; dirty = true; }
    }]; },
    useRef(seed) { const at = cursor++; return hooks[at] ??= { current: seed }; },
    useMemo(fn, deps) { const at = cursor++; if (!same(hooks[at]?.deps, deps)) hooks[at] = { deps, value: fn() }; return hooks[at].value; },
    useEffect(fn, deps) { const at = cursor++; if (!same(hooks[at]?.deps, deps)) {
      const old = hooks[at]; hooks[at] = { deps, cleanup: old?.cleanup, fn }; effects.push(() => { hooks[at].cleanup?.(); hooks[at].cleanup = fn(); });
    } },
  };
  const jsx = (type, props = {}) => { if (typeof type === 'function') return type(props);
    if (type === 'h1' && props.ref) props.ref.current = { focus: () => focuses++ }; return { type, props }; };
  const d = load('draft', { './appApi': requests }, c);
  const api = load('wizard', { react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
    './ui/Icon': { default: ({ name }) => ({ type: 'svg', props: { 'data-icon': name } }), __esModule: true },
    '../styles/modes.css': {}, '../lib/draftTasks': d, '../lib/mediaInput': media, '../lib/productionModes': modes,
    '../lib/uploadSessions': { ...uploadModule(requests), ...uploadOverrides },
  }, { ...c, window: { confirm }, performance: { now: () => 0 } });
  const props = { limits: { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 },
    initialDraft: draft, generativeAllowed: true, busy: false, serviceReady: ready, onDraft: d => drafts.push(d), onSubmit, onError() {} };
  function flush() { let guard = 0; do { if (dirty) { cursor = 0; dirty = false; tree = api.CreateWizard(props); }
    while (effects.length) effects.shift()(); assert.ok(++guard < 30); } while (dirty); }
  function nodes(node = tree) { const out = []; const visit = n => { if (Array.isArray(n)) n.forEach(visit); else if (n && typeof n === 'object') { out.push(n); visit(n.props?.children); } }; visit(node); return out; }
  const text = n => n == null || typeof n === 'boolean' ? '' : Array.isArray(n) ? n.map(text).join('') : typeof n === 'object' ? text(n.props?.children) : String(n);
  const button = label => { const n = nodes().find(n => n.type === 'button' && (text(n).trim() === label || n.props['aria-label'] === label)); assert.ok(n, label); return n; };
  flush();
  return { c, drafts, requests, nodes, text, button, flush, get focuses() { return focuses; }, get tree() { return tree; },
    click(label) { const n = button(label); assert.ok(!n.props.disabled, label); n.props.onClick(); flush(); },
    edit(script) { nodes().find(n => n.type === 'textarea' && n.props.className === 'gm-script').props.onChange({ target: { value: script } }); flush(); },
    async settle() { await ticks(); flush(); await ticks(); flush(); },
    unmount() { for (const hook of hooks) hook?.cleanup?.(); },
  };
}
const draft = patch => ({ script: '新闻标题\n今天社区活动正式开始，大家一起来了解现场的最新消息。', mode: 'voiceover', step: 1,
  preferences: modes.defaultModePreferences(), files: [], elements: {}, sentenceChecks: {}, ...patch });
const restored = patch => draft({ ...access, step: 2, files: [metadata()], uploadBindings: [binding()], uploadIds: [UP], uploadTokens: { [UP]: TOKEN }, ...patch });
test('actual wizard starts with A only, unfolds three modes, step change focuses heading', () => {
  const w = wizard(draft());
  assert.equal(w.nodes().filter(n => n.props?.className === 'gm-mode-card').length, 1);
  w.click('更多制作方式：旁白 + 原声 / 只用原声 ▾');
  assert.equal(w.nodes().filter(n => n.props?.className === 'gm-mode-card').length, 3);
  assert.equal(w.focuses, 0); w.click('下一步：传素材'); assert.equal(w.focuses, 1);
  w.click('← 上一步'); assert.equal(w.focuses, 2); w.unmount();
});
test('element chips toggle manual whole-script self-checks without opening evidence; edits clear them', () => {
  const w = wizard(draft());
  const chip = () => w.nodes().find(n => n.props?.className === 'gm-element-chip');
  const details = () => w.nodes().find(n => n.props?.className === 'gm-evidence-details');
  assert.equal(details().props.open, false); assert.equal(chip().props['aria-pressed'], false);
  chip().props.onClick(); w.flush();
  assert.equal(chip().props['aria-pressed'], true); assert.equal(details().props.open, false);
  const key = media.ELEMENT_RULES[0].key;
  assert.deepEqual(plain(w.drafts.at(-1).elements[key]), { confirmed: true, evidence: `script:${draft().script}` });
  chip().props.onClick(); w.flush(); assert.equal(chip().props['aria-pressed'], false);
  const select = w.nodes().find(n => n.type === 'select' && n.props['aria-label']?.endsWith('的稿件依据'));
  const evidence = `0:${w.drafts.at(-1).sentences[0].text}`;
  select.props.onChange({ target: { value: evidence } }); w.flush();
  chip().props.onClick(); w.flush(); assert.equal(w.drafts.at(-1).elements[key].evidence, evidence);
  assert.equal(chip().props['aria-pressed'], true);
  w.edit(draft().script + '新的现场消息。');
  assert.equal(chip().props['aria-pressed'], false); assert.deepEqual(plain(w.drafts.at(-1).elements), {});
  assert.equal(w.requests.calls.length, 0); w.unmount();
});
test('original empty/title-only onboarding is allowed; malformed title and short A remain blocked', () => {
  for (const script of ['', '新闻标题']) {
    const w = wizard(draft({ mode: 'original', script, preferences: modes.defaultModePreferences('original') }));
    assert.equal(w.button('下一步：传素材').props.disabled, false); w.unmount();
  }
  for (const [mode, script] of [['voiceover', '标题\n太短'], ['original', '标题！']]) {
    const w = wizard(draft({ mode, script })); assert.equal(w.button('下一步：传素材').props.disabled, true); w.unmount();
  }
});
test('restored task emits capability and only reads; file advanced controls fold and undo prevents DELETE', async () => {
  const w = wizard(restored(), { respond: path => path === root ? receipt() : path === fileRoot ? snapshot() : { files: [] } });
  await w.settle(); w.c.advance(600); await w.settle();
  assert.equal(w.drafts.at(-1).draftTaskId, TASK); assert.equal(w.drafts.at(-1).draftTaskToken, TOKEN);
  assert.ok(w.requests.calls.every(c => !c.init.method));
  assert.equal(w.nodes().some(n => n.props?.className === 'gm-media-edit'), false);
  w.click('备注 / 只用片段 ▾'); assert.ok(w.nodes().some(n => n.props?.className === 'gm-media-edit'));
  w.click('移除 synthetic.mp4'); assert.ok(w.text(w.tree).includes('已移除'));
  w.c.advance(4999); await w.settle(); assert.ok(w.requests.calls.every(c => c.init.method !== 'DELETE'));
  w.click('撤销'); w.c.advance(1); await w.settle(); assert.ok(w.requests.calls.every(c => c.init.method !== 'DELETE'));
  w.click('移除 synthetic.mp4'); w.c.advance(5000); await w.settle();
  assert.equal(w.requests.calls.filter(c => c.init.method === 'DELETE').length, 1);
  assert.equal(w.drafts.at(-1).files.length, 0); w.unmount();
});
test('unmount cancels pending removal without destructive cleanup and stage warnings cannot bypass hard upload gate', async () => {
  const w = wizard(restored(), { respond: path => path === root ? receipt() : path === fileRoot ? snapshot({ status: 'uploading', chunks: [], progress: 0 }) : { files: [] } });
  await w.settle(); assert.equal(w.button('下一步：选效果').props.disabled, true);
  w.click('移除 synthetic.mp4'); w.unmount(); w.c.advance(5000); await ticks();
  assert.ok(w.requests.calls.every(c => c.init.method !== 'DELETE'));
});
test('sentence self-check list shows 12 then at most 60, preserving complete draft body', async () => {
  const script = '新闻标题\n' + Array.from({ length: 70 }, (_, i) => `这是第${i}条现场新闻。`).join('\n');
  const w = wizard(restored({ script }), { respond: path => path === root ? receipt() : path === fileRoot ? snapshot() : { files: [] } });
  await w.settle(); assert.equal(w.nodes().filter(n => n.props?.className === 'gm-check-row').length, 12);
  w.click('展开全部 60 句'); assert.equal(w.nodes().filter(n => n.props?.className === 'gm-check-row').length, 60);
  assert.equal(w.drafts.at(-1).sentences.length, 70); w.unmount();
});
test('wizard keeps start blocked and binding recoverable while DELETE is pending or fails', async () => {
  const held = deferred();
  const w = wizard(restored(), { respond: (path, init) => init.method === 'DELETE' ? held.promise
    : path === root ? receipt() : path === fileRoot ? snapshot() : { files: [] } });
  await w.settle(); w.click('移除 synthetic.mp4'); w.c.advance(5000); await w.settle();
  assert.equal(w.button('下一步：选效果').props.disabled, true);
  assert.equal(w.button('撤销').props.disabled, true);
  assert.equal(w.drafts.at(-1).uploadBindings.length, 1);
  held.reject(Error('synthetic ambiguous DELETE')); await w.settle();
  assert.equal(w.drafts.at(-1).uploadBindings.length, 1);
  assert.equal(w.button('下一步：选效果').props.disabled, true); w.unmount();
});
test('actual wizard edit waits 600ms, sends complete editorial context and never starts provider work', async () => {
  const w = wizard(restored(), { respond: (path, init) => path.endsWith('/align')
    ? { matches: JSON.parse(init.body).sentences.map(s => ({ idx: s.idx, kind: s.kind, score: 0, source: null, alt_takes: [] })) }
    : path === root ? receipt() : path === fileRoot ? snapshot() : { files: [] } });
  await w.settle(); w.click('← 上一步'); w.edit('新闻标题\n今天大家在现场观看节目，也了解了活动的详细信息。');
  w.c.advance(599); await w.settle(); assert.equal(w.requests.calls.filter(c => c.path.endsWith('/align')).length, 0);
  w.c.advance(1); await w.settle();
  const calls = w.requests.calls.filter(c => c.init.method === 'POST'); assert.equal(calls.length, 1);
  assert.equal(calls[0].path, `${root}/align`); assert.equal(JSON.parse(calls[0].init.body).script, w.drafts.at(-1).script);
  assert.equal(JSON.parse(calls[0].init.body).mode, 'voiceover');
  assert.ok(JSON.parse(calls[0].init.body).sentences.every(s => s.kind === 'quote'));
  assert.equal('upload_tokens' in JSON.parse(calls[0].init.body), false); w.unmount();
});
test('B missing-source conversion requires confirmation and never silently changes quote type', async () => {
  let accepts = false, confirmations = 0;
  const w = wizard(restored({ mode: 'mixed', script: '新闻标题\n同期：今天的现场活动吸引了很多人前来了解。' }), {
    confirm() { confirmations++; return accepts; }, respond: (path, init) => path.endsWith('/align')
      ? { matches: JSON.parse(init.body).sentences.map(s => ({ idx: s.idx, kind: s.kind, score: 0, source: null, alt_takes: [] })) }
      : path === root ? receipt() : path === fileRoot ? snapshot() : { files: [] },
  });
  await w.settle(); w.click('重新核对'); w.c.advance(600); await w.settle();
  w.click('改成旁白'); assert.equal(confirmations, 1); assert.equal(w.drafts.at(-1).sentences[0].kind, 'quote');
  accepts = true; w.click('改成旁白'); assert.equal(confirmations, 2); assert.equal(w.drafts.at(-1).sentences[0].kind, 'narration'); w.unmount();
});
test('C unsaid content remains blocked after real match result, even with a high score', async () => {
  const actual = '今天活动非常热闹', source = { take_id: 'synthetic_take', upload_id: UP, start: 1, end: 4,
    speaker_id: '', asr_text: actual, score: .95, snr_db: null, words: [], precision: 'segment' };
  const w = wizard(restored({ mode: 'original', script: '新闻标题\n今天活动非常热闹并且完全免费。' }), {
    respond: (path, init) => path.endsWith('/align') ? { matches: JSON.parse(init.body).sentences.map(s => ({
      idx: s.idx, kind: s.kind, score: .95, source, alt_takes: [],
    })) } : path === root ? receipt() : path === fileRoot ? snapshot({ has_speech: true,
      transcript: [{ id: 'seg', start: 1, end: 4, speaker_id: '', text: actual, words: [] }] }) : { files: [] },
  });
  await w.settle(); w.click('重新核对'); w.c.advance(600); await w.settle();
  assert.equal(w.button('下一步：选效果').props.disabled, true);
  w.click('← 上一步'); assert.ok(w.nodes().some(n => n.props?.className === 'gm-unverified-quote')); w.unmount();
});
test('transcript displays eight lines then an explicit remainder fold; notes preserve twenty Unicode codepoints', async () => {
  const transcript = Array.from({ length: 10 }, (_, i) => ({ id: `s${i}`, start: i, end: i + 1, text: `Synthetic ${i}`, speaker_id: '', words: [] }));
  const w = wizard(restored(), { respond: path => path === root ? receipt() : path === fileRoot ? snapshot({ has_speech: true, transcript }) : { files: [] } });
  await w.settle(); assert.equal(w.nodes().filter(n => n.props?.className === 'gm-transcript-line').length, 8);
  const detail = w.nodes().find(n => n.type === 'details' && w.text(n).includes('再看 2 句转写') && n.props.onToggle);
  assert.ok(detail); detail.props.onToggle({ currentTarget: { open: true } }); w.flush();
  assert.equal(w.nodes().filter(n => n.props?.className === 'gm-transcript-line').length, 10);
  w.click('备注 / 只用片段 ▾');
  const note = w.nodes().find(n => n.type === 'input' && n.props.placeholder === '备注 / 标签（最多 20 字）');
  note.props.onChange({ target: { value: '𠮷'.repeat(21) } }); w.flush();
  assert.equal(Array.from(w.drafts.at(-1).files[0].note).length, 20); w.unmount();
});

test('scoped receipt aliases retain taskcap and server file identity; missing scope never falls back', async () => {
  for (const aliases of [false, true]) {
    const api = boundary(() => ({ ...snapshot(), up_id: UP, file_id: FID, token: TOKEN,
      chunk_size: 8388608, put_url: `${fileRoot}/chunks/{index}`,
      ...(aliases ? { upload_id: UP, access_token: TOKEN } : {}) }));
    const u = uploadModule(api), result = await u.createUploadSession(file(), sha, sig(), access);
    assert.equal(result.access_token, TOKEN); assert.equal(result.server_file_id, FID);
    assert.equal(u.readUploadBindings([result])[0].server_file_id, FID);
    const before = api.calls.length;
    await assert.rejects(u.getUploadStatus(UP, TOKEN, sig(), { draftTaskToken: TOKEN, server_file_id: FID }));
    await assert.rejects(u.uploadFile(undefined, sig(), { onSession: denied, onSnapshot: denied }, { ...session(), draftTaskId: undefined }));
    assert.equal(api.calls.length, before);
  }
  const bad = uploadModule(boundary(() => ({ upload_id: UP, up_id: UP, file_id: FID, access_token: UTOKEN,
    token: TOKEN, chunk_size: 8388608, put_url: `${fileRoot}/chunks/{index}` })));
  await assert.rejects(bad.createUploadSession(file(), sha, sig(), access));
});

test('new wizard self voice needs no microphone or whole recording and submits mine only after preprocessing', async () => {
  const submitted = [], respond = path => path === root ? receipt() : path === fileRoot ? snapshot() : { files: [] };
  const w = wizard(restored(), { respond, onSubmit: async value => submitted.push(value) });
  await w.settle(); w.click('下一步：选效果'); w.click('我自己读');
  assert.ok(w.text(w.tree).includes('做好后逐句录音'));
  assert.equal(w.nodes().some(n => n.props?.className === 'gm-voice-panel'), false);
  assert.equal(w.drafts.at(-1).preferences.voice, 'mine');
  assert.equal(w.button('开始制作').props.disabled, false);
  w.click('开始制作'); await w.settle();
  assert.equal(submitted.length, 1); assert.equal(submitted[0].preferences.voice, 'mine');
  assert.equal('ownVoice' in submitted[0], false); w.unmount();
  for (const patch of [{ status: 'processing', phase: 'asr' }, { status: 'asr_failed', probe_ok: true, can_materialize: true }]) {
    const blocked = wizard(restored({ voice: 'self' }), { respond: path => path === root ? receipt() : path === fileRoot ? snapshot(patch) : { files: [] } });
    await blocked.settle(); assert.equal(blocked.button('下一步：选效果').props.disabled, true); blocked.unmount();
  }
});

test('C source-selected full body and B-to-A mode changes round-trip without losing text or source hints', async () => {
  const body = '第一段原话，保持完整。第二段原话也要完整保留。';
  const segment = { id: 'seg_full', start: 1, end: 8, speaker_id: '', text: body, words: [] };
  const w = wizard(restored({ mode: 'original', script: '新闻标题', preferences: modes.defaultModePreferences('original') }), {
    respond: path => path === root ? receipt() : path === fileRoot ? snapshot({ has_speech: true, transcript: [segment] }) : { files: [] },
  });
  await w.settle();
  const select = w.nodes().find(n => n.type === 'input' && n.props['aria-label'] === `选入原话：${body}`);
  assert.ok(select); select.props.onChange({ target: { checked: true } }); w.flush();
  const selected = plain(w.drafts.at(-1));
  assert.equal(selected.sentences.length, 2);
  assert.ok(selected.script.includes('第一段原话，保持完整')); assert.ok(selected.script.includes('第二段原话也要完整保留'));
  assert.ok(modes.sentencesMatchScript(selected.script, selected.sentences, 'original'));
  assert.ok(selected.sentences.every(s => s.source_hint.upload_id === UP && s.source_hint.seg_id === segment.id));
  w.unmount();
  const restoredAgain = wizard(selected, { respond: path => path === root ? receipt() : path === fileRoot ? snapshot({ has_speech: true, transcript: [segment] }) : { files: [] } });
  await restoredAgain.settle(); restoredAgain.click('← 上一步');
  const modeButton = mode => restoredAgain.nodes().find(n => n.props?.className === 'gm-mode-card' && restoredAgain.text(n).includes(modes.MODE_LABELS[mode]));
  modeButton('mixed').props.onClick(); restoredAgain.flush();
  let current = restoredAgain.drafts.at(-1);
  assert.equal(current.mode, 'mixed'); assert.ok(modes.sentencesMatchScript(current.script, current.sentences, current.mode));
  modeButton('voiceover').props.onClick(); restoredAgain.flush(); current = restoredAgain.drafts.at(-1);
  assert.equal(current.mode, 'voiceover'); assert.ok(modes.sentencesMatchScript(current.script, current.sentences, current.mode));
  assert.deepEqual(current.sentences.map(s => s.text), selected.sentences.map(s => s.text));
  assert.ok(current.sentences.every(s => s.kind === 'narration' && s.source_hint.seg_id === segment.id));
  assert.equal(current.uploadBindings[0].server_file_id, FID); restoredAgain.unmount();
});

test('start keeps omitted capability; quota/403/timeout never replay; task states and server defaults are typed', async () => {
  const kept = apiHarness(() => ({ id: TASK, task_id: TASK, status: 'queued', queue: 1 }));
  assert.equal((await kept.api.uploadTask(submission(), sig(), () => {})).access_token, TOKEN);
  for (const failure of ['quota', 'forbidden', 'timeout']) {
    const h = apiHarness(() => {
      if (failure === 'timeout') throw new DOMException('Synthetic timeout', 'AbortError');
      return new Response(JSON.stringify({ detail: { message: 'PRIVATE_QUOTA_DETAIL' } }), { status: failure === 'quota' ? 429 : 403,
        headers: { 'Retry-After': '3600', 'X-Anonymous-Session': 'required' } });
    });
    await assert.rejects(h.api.uploadTask(submission(), sig(), () => {}), error => failure !== 'quota'
      || error.status === 429 && error.retryAfterSeconds === 3600 && !error.message.includes('PRIVATE_QUOTA_DETAIL'));
    assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 1);
  }
  const api = kept.api;
  for (const raw of ['-1', 'Infinity', '1e3', '999999999999999999', '1.5', '2026-01-01']) assert.equal(api.parseRetryAfter(raw), undefined);
  assert.equal(api.parseRetryAfter('Tue, 29 Sep 2026 00:01:00 GMT', Date.parse('2026-09-29T00:00:00Z')), 60);
  for (const status of ['draft', 'uploading']) assert.equal(api.parseTask({ task_id: TASK, status, progress: 0, revision: 0, stages: [] }).status, status);
  const rows = ['draft', 'uploading', 'done'].map((status, i) => ({ task_id: `task${i}`, title: 'Synthetic', created_at: '2026-09-29T00:00:00Z', revision: 0, status }));
  assert.equal((await apiHarness(() => ({ tasks: rows, total: rows.length })).api.getLocalHistory()).length, 1);
  let stored = JSON.stringify(rows.map(row => ({ taskId: row.task_id, title: row.title, createdAt: row.created_at,
    revision: row.revision, status: row.status, accessToken: TOKEN })));
  const historyApi = load('api', {}, { localStorage: { getItem: () => stored, setItem: (_key, value) => { stored = value; } } });
  assert.equal(historyApi.readHistory().length, 1);
  historyApi.rememberWork({ taskId: 'newwork', title: 'New', createdAt: '2026-09-29T00:00:00Z', status: 'queued', revision: 0, accessToken: TOKEN });
  assert.equal(JSON.parse(stored).length, 4); assert.equal(historyApi.readHistory().length, 2);
  historyApi.forgetHistory('newwork'); assert.equal(JSON.parse(stored).length, 3);
  const limits = { max_files: 20, max_upload_bytes: 500, max_total_upload_bytes: 1000, max_script_length: 8000,
    pacing_cpm: { slow: 230, normal: 265, fast: 290 }, maintenance: false, retry_same_supported: false, match_low: .6, match_ok: .85 };
  assert.deepEqual(plain(await apiHarness(() => limits).api.getPublicLimits()), limits);
  for (const patch of [{ maintenance: 'false' }, { pacing_cpm: { slow: 230, normal: '265', fast: 290 } }, { match_ok: .5 }]) {
    await assert.rejects(apiHarness(() => ({ ...limits, ...patch })).api.getPublicLimits());
  }
});