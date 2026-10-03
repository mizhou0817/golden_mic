import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

// Actual Workspace recovery/validation/cache and complete CreateWizard TSX.
// Synthetic read-only HTTP after the one explicit recovery; no host, media,
// providers, real storage, filesystem writes or unbound module imports.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = name => readFileSync(resolve(root, name), 'utf8');
const SCRIPT = '\u5408\u6210\u8272\u5361\u914d\u97f3\u9a8c\u6536\u6837\u7247\n\u8272\u5757\u753b\u9762\u7f13\u7f13\u79fb\u52a8\u3002\n\u6d4b\u8bd5\u56fe\u6848\u6e05\u6670\u53ef\u89c1\u3002';
// Independent documented start contract: title + blank separator + full body.
// Never derived from the observed UI value or scriptFromSentences.
const SAVED_SCRIPT = SCRIPT.replace('\n', '\n\n');
const LABEL = '\u65b0\u95fb\u7a3f\uff08\u7b2c\u4e00\u884c\u6807\u9898\uff0c\u4e0b\u4e00\u884c\u6b63\u6587\uff09';
const plain = value => JSON.parse(JSON.stringify(value));
const escaped = value => JSON.stringify(value).replace(/[\u007f-\uffff]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
const denied = () => assert.fail('Unexpected external effect');
const absent = Symbol('absent');
const workspace = ts.createSourceFile('Workspace.tsx', source('src/components/Workspace.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
function declaration(name) {
  const found = [];
  function visit(node) {
    if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText(workspace) === name) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(workspace); assert.equal(found.length, 1, name);
  return (ts.isVariableDeclaration(found[0]) ? 'const ' : '')
    + printer.printNode(ts.EmitHint.Unspecified, found[0], workspace).replace(/^export /, '') + ';';
}
const recoveryCode = ['isObject', 'validId', 'pathFor', 'invalidDraft', 'draftText', 'draftNumber', 'readMediaMetadata',
  'validateDraft', 'writeDraftCache', 'editFailedDraft'].map(declaration).join('\n');
function compile(code, fileName = 'recovery.tsx') {
  const result = ts.transpileModule(code, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    transformers: { before: [context => tree => {
      const visit = node => ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression) && node.name.text === 'env'
        ? ts.factory.createObjectLiteralExpression() : ts.visitEachChild(node, visit, context);
      return ts.visitNode(tree, visit);
    }] } });
  assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
  return result.outputText;
}
function deferred() { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; }

function harness({ script = SAVED_SCRIPT, voice = false, mode = 'original', suffix = 'one' } = {}) {
  const task = `recovered_${suffix}`, sourceTask = `source_${suffix}`, token = `synthetic_new_${suffix}_`.padEnd(48, 'x');
  const oldToken = `synthetic_old_${suffix}_`.padEnd(48, 'y'), path = `/api/tasks/${task}`;
  const storage = new Map(), modules = new Map(), requests = [], errors = [], mounts = [], timers = new Map(), reads = [];
  let response, timerSerial = 0;
  const globals = { exports: {}, URL, URLSearchParams, Headers, Response, FormData, Blob, AbortController, DOMException,
    Uint8Array, Uint32Array, DataView, location: { origin: 'https://synthetic.invalid' }, navigator: {}, XMLHttpRequest: denied,
    setTimeout(fn, delay) { const id = ++timerSerial; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); }, setInterval: denied, clearInterval: denied,
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    fetch: async (url, init = {}) => {
      const method = init.method ?? 'GET'; requests.push({ url, method, token: init.headers?.get('X-Task-Token') });
      if (url === '/api/session') { assert.equal(method, 'GET'); return Response.json({ access_mode: 'development', csrf_token: null, expires_at: null }); }
      if (url === `/api/tasks/${sourceTask}/recover-draft`) {
        assert.equal(method, 'POST'); assert.equal(init.headers.get('X-Task-Token'), oldToken);
        assert.deepEqual(JSON.parse(init.body), { step: 1, mode });
        return Response.json(response, { status: 201 });
      }
      // Independent write/ASR/generation boundary, including align/chunk/complete.
      assert.equal(method, 'GET', 'hydration must not mutate or start processing');
      assert.equal(init.headers.get('X-Task-Token'), token, 'read uses recovered task capability');
      assert.ok(url === path || url.startsWith(`${path}/files`), 'no old/foreign task read');
      if (url === path) return Response.json(response);
      if (url === `${path}/files`) return Response.json({ files: response.files });
      const f = response.files.find(f => url === `${path}/files/${f.file_id}`);
      assert.ok(f, 'exact recovered file path');
      const held = deferred(); reads.push({ held, file: f });
      await held.promise;
      return Response.json({ id: f.up_id, name: f.name, bytes: f.bytes, sec: f.sec, status: 'ready',
        probe_ok: true, width: f.width, height: f.height, fps: f.fps, metadata_only: false, can_materialize: true,
        has_speech: false, transcript: [], thumb_url: null, wave_url: null, asr_confidence: null,
        progress: 100, chunks: [0], sha256: f.sha256 });
    } };
  function load(relative, overrides = {}) {
    const filename = resolve(root, relative);
    if (modules.has(filename) && !Object.keys(overrides).length) return modules.get(filename);
    if (filename.endsWith('.json')) return JSON.parse(readFileSync(filename, 'utf8'));
    const module = { exports: {} }; modules.set(filename, module.exports);
    vm.runInNewContext(compile(readFileSync(filename, 'utf8'), filename), { ...globals, module, exports: module.exports,
      window: { confirm: () => true }, performance: { now: () => 0 }, require(name) {
        if (Object.hasOwn(overrides, name)) return overrides[name];
        assert.ok(name.startsWith('.'), `unbound module ${name}`);
        const base = resolve(dirname(filename), name), dependency = [base, base + '.ts', base + '.json'].find(existsSync);
        assert.ok(dependency, name); return load(dependency);
      } }, { filename, timeout: 1000 });
    return module.exports;
  }
  const modes = load('src/lib/productionModes.ts'), api = load('src/lib/appApi.ts');
  const persistence = load('src/lib/v2Persistence.ts');
  response = { task_id: task, id: task, status: 'draft', accepted: false, access_token: token, step: 1, mode, script,
    preferences: modes.defaultModePreferences(mode), sentences: modes.parseModeScript(script, mode), speakers: [],
    recovery: { source_task_id: sourceTask, task_id: task },
    ...(voice === absent ? {} : { source_voice_preferred: voice }),
    files: Array.from({ length: 4 }, (_, i) => ({ file_id: `file_${suffix}_${i}`, up_id: `up_${suffix}_${i}`,
      name: `broll-${i}.mp4`, bytes: 3, size: 3, lastModified: 123 + i, is_image: false, sec: 10,
      note: '', in_sec: 0, out_sec: 10, width: 64, height: 64, status: 'ready', sha256: 'a'.repeat(64),
      fps: 25, probe_ok: true, metadata_only: false, can_materialize: true,
      chunksize: 8388608, token, access_token: token })) };
  const before = plain(response), ref = current => ({ current }), scope = new AbortController();
  const target = { id: sourceTask, token: oldToken, scope,
    request: api.createAppRequest({ taskId: sourceTask, token: oldToken, signal: scope.signal }) };
  Object.assign(globals, modes, load('src/lib/mediaInput.ts'), load('src/lib/uploadSessions.ts'), persistence, {
    MAX_SCRIPT_LENGTH: 100000, MAX_DRAFT_LENGTH: 512 * 1024,
    selectedRef: ref(target), actionLock: ref(false), intentRef: ref(null), pageRef: ref('result'),
    task: { task_id: sourceTask, status: 'done' }, generation: ref(1), alive: ref(true), draftRef: ref(null),
    draftDirty: ref(false), draftBlocked: ref(false), draftNeedsClear: false, draftSuppressed: ref(false), draftSaved: ref(''), draftTimer: ref(null),
    hasDraftContent: draft => !!draft?.script, window: { confirm: () => true }, mutate: action => action(),
    retainWork() {}, cancelReads() {}, unmountWizard() {}, select() {}, setTask() {}, setUpload() {}, setMissing() {}, setDialog() {},
    setDraftPending() {}, setDraftProblem() {}, setDraftNeedsClear() {}, setDraftSavedAt() {},
    mountWizard: seed => mounts.push(seed), showPage: page => { globals.pageRef.current = page; }, writeHash() {},
    setDraftStatus() {}, restoredDraftNote: () => '', setNotice() {}, setError: message => { if (message) errors.push(message); },
    errorText: api.errorText, refreshHistory() {},
  });
  vm.runInNewContext(compile(recoveryCode + '\nthis.recover = editFailedDraft; this.validate = validateDraft; this.save = writeDraftCache;'), globals);

  function mount(initialDraft) {
    const hooks = [], effects = [], drafts = []; let cursor = 0, dirty = true, tree;
    const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
    const react = { useId: () => `wizard-${suffix}`,
      useState(seed) { const at = cursor++; hooks[at] ??= { value: typeof seed === 'function' ? seed() : seed };
        return [hooks[at].value, next => { const v = typeof next === 'function' ? next(hooks[at].value) : next;
          if (!Object.is(v, hooks[at].value)) { hooks[at].value = v; dirty = true; } }]; },
      useRef(seed) { const at = cursor++; return hooks[at] ??= { current: seed }; },
      useMemo(fn, deps) { const at = cursor++; if (!same(hooks[at]?.deps, deps)) hooks[at] = { deps, value: fn() }; return hooks[at].value; },
      useEffect(fn, deps) { const at = cursor++; if (!same(hooks[at]?.deps, deps)) {
        hooks[at] = { deps, cleanup: hooks[at]?.cleanup }; effects.push(() => { hooks[at].cleanup?.(); hooks[at].cleanup = fn(); });
      } },
    };
    const jsx = (type, props = {}) => typeof type === 'function' ? type(props) : { type, props };
    const wizard = load('src/components/CreateWizard.tsx', { react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' },
      '../styles/modes.css': {}, './ui/Icon': { __esModule: true, default: () => null } });
    const props = { initialDraft, limits: { max_files: 20, max_upload_bytes: 500 * 1024 ** 2,
      max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 }, generativeAllowed: false, busy: false,
      onDraft(d) { drafts.push(plain(d)); globals.save(d, 123); }, onSubmit: denied, onError: denied };
    function render() { cursor = 0; dirty = false; tree = wizard.CreateWizard(props); }
    function flush() { let guard = 0; do { if (dirty) render(); while (effects.length) effects.shift()(); assert.ok(++guard < 30); } while (dirty); }
    function nodes() { const out = []; function visit(n) { if (Array.isArray(n)) n.forEach(visit);
      else if (n && typeof n === 'object') { out.push(n); visit(n.props?.children); } } visit(tree); return out; }
    const text = n => n == null || typeof n === 'boolean' ? '' : Array.isArray(n) ? n.map(text).join('')
      : typeof n === 'object' ? text(n.props?.children) : String(n);
    function scriptBox() {
      const labels = nodes().filter(n => n.type === 'label' && text(n) === LABEL); assert.equal(labels.length, 1);
      const boxes = nodes().filter(n => n.type === 'textarea' && n.props.id === labels[0].props.htmlFor); assert.equal(boxes.length, 1);
      return boxes[0];
    }
    async function settle() { for (let i = 0; i < 8; i++) { for (let j = 0; j < 40; j++) await Promise.resolve(); flush(); } }
    render(); const firstValue = scriptBox().props.value; flush();
    return { drafts, firstValue, scriptBox, settle, nodes,
      unmount() { for (const hook of hooks) hook?.cleanup?.(); } };
  }
  return { globals, response, before, requests, errors, mounts, reads, timers, persistence, mount, task, token,
    async recover() { await globals.recover(1, mode); assert.deepEqual(errors, []); assert.equal(mounts.length, 1); return mounts[0]; },
    releaseReads() { for (const read of reads) read.held.release(); },
    advanceHydrationDebounce() {
      for (const [id, timer] of [...timers]) if (timer.delay <= 1000) { timers.delete(id); timer.fn(); }
    },
    checkBoundary() {
      assert.deepEqual(plain(response), before, 'synthetic server snapshot remains byte-equivalent');
      assert.deepEqual(requests.filter(r => r.method !== 'GET').map(r => r.url), [`/api/tasks/${sourceTask}/recover-draft`]);
      assert.ok(requests.every(r => !/\/(start|align|complete|chunks)(?:\/|$)/.test(r.url)), 'no ASR/write/generation route');
    } };
}

async function hydrated(options) {
  const h = harness(options), seed = await h.recover(), w = h.mount(seed);
  await w.settle(); assert.equal(h.reads.length, 4, 'four independent held file reads');
  assert.equal(w.firstValue, seed.script); assert.equal(w.scriptBox().props.value, seed.script);
  h.releaseReads(); await w.settle();
  h.advanceHydrationDebounce(); await w.settle(); return { h, seed, w };
}

// Explicit diagnostic reproduces the OLD acceptance comparison unchanged.
// Its failure is NOT a product regression test to weaken into a pass.
if (process.argv.includes('--compare-original-input')) {
  const { h, w } = await hydrated({});
  try {
    console.log(escaped({ input: SCRIPT, recovered: h.response.script, rendered: w.scriptBox().props.value }));
    assert.equal(w.scriptBox().props.value, SCRIPT);
  } finally { w.unmount(); h.checkBoundary(); }
} else {
  for (const voice of [true, false, absent]) test(`recovered C preserves full saved text and independent source voice ${String(voice)}`, async () => {
    const { h, seed, w } = await hydrated({ voice });
    try {
      assert.notEqual(SAVED_SCRIPT, SCRIPT, 'start-time blank separator is a real exact-string difference');
      assert.equal(w.scriptBox().props.value, SAVED_SCRIPT);
      assert.equal(w.nodes().filter(n => n.type === 'input' && String(n.props['aria-label']).startsWith('\u9009\u5165\u539f\u8bdd')).length, 0);
      assert.equal(seed.sentences.length, 2); assert.ok(seed.sentences.every(s => s.kind === 'quote'));
      assert.ok(w.drafts.length > 0);
      for (const d of [seed, ...w.drafts, h.persistence.v2Persistence.readDraft().draft]) {
        assert.equal(d.script, SAVED_SCRIPT); assert.equal(d.draftTaskId, h.task); assert.equal(d.draftTaskToken, h.token);
        assert.equal(d.uploadBindings.length, 4); assert.equal(d.source_voice_preferred, voice === absent ? undefined : voice);
        assert.equal(Object.hasOwn(d, 'source_voice_preferred'), voice !== absent);
        assert.ok(d.uploadBindings.every(b => b.draftTaskId === h.task && d.uploadTokens[b.upload_id] === h.token));
      }
      const reload = h.mount(h.globals.validate(h.persistence.v2Persistence.readDraft().draft));
      try { await reload.settle(); h.releaseReads(); await reload.settle(); assert.equal(reload.scriptBox().props.value, SAVED_SCRIPT); }
      finally { reload.unmount(); }
    } finally { w.unmount(); h.checkBoundary(); }
  });
  for (const [name, script] of [['single-LF', SCRIPT], ['blank-LF', SAVED_SCRIPT], ['CRLF', SAVED_SCRIPT.replaceAll('\n', '\r\n')]]) {
    test(`hydration never normalizes ${name} recovered text even with empty manual selection`, async () => {
      const { h, w } = await hydrated({ script });
      try { assert.equal(w.scriptBox().props.value, script); assert.ok(w.drafts.every(d => d.script === script)); }
      finally { w.unmount(); h.checkBoundary(); }
    });
  }
  test('two recovered draft scopes hydrate out of order without script/capability cross-talk', async () => {
    const a = harness({ suffix: 'alpha', voice: true }), b = harness({ suffix: 'beta', script: SCRIPT, voice: false });
    const wa = a.mount(await a.recover()), wb = b.mount(await b.recover());
    try {
      await Promise.all([wa.settle(), wb.settle()]); assert.equal(a.reads.length, 4); assert.equal(b.reads.length, 4);
      b.releaseReads(); await wb.settle(); assert.equal(wb.scriptBox().props.value, SCRIPT);
      assert.equal(wa.scriptBox().props.value, SAVED_SCRIPT);
      a.releaseReads(); await wa.settle(); assert.equal(wa.scriptBox().props.value, SAVED_SCRIPT);
      assert.equal(wb.scriptBox().props.value, SCRIPT);
      assert.equal(a.persistence.v2Persistence.readDraft().draft.draftTaskId, a.task);
      assert.equal(b.persistence.v2Persistence.readDraft().draft.draftTaskId, b.task);
    } finally { wa.unmount(); wb.unmount(); a.checkBoundary(); b.checkBoundary(); }
  });
}