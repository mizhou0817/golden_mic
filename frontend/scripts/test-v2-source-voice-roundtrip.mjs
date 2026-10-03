import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

// Actual frontend TS/TSX, deterministic hooks, memory-only storage, synthetic
// HTTP. No backend execution, services, provider calls, media or disk writes.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = path => readFileSync(resolve(root, path), 'utf8');
const absent = Symbol('absent');
const TASK = 'source_voice_draft', TOKEN = 'synthetic_source_voice_'.padEnd(48, 'x');
const UP = 'up_synthetic', FID = 'file_synthetic', path = `/api/tasks/${TASK}`;
const access = { draftTaskId: TASK, draftTaskToken: TOKEN };
const plain = value => JSON.parse(JSON.stringify(value));
const denied = () => assert.fail('Unexpected external side effect');
function compile(code, fileName = 'fixture.ts') {
  const result = ts.transpileModule(code, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    transformers: { before: [context => tree => {
      const visit = node => ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression) && node.name.text === 'env'
        ? ts.factory.createObjectLiteralExpression() : ts.visitEachChild(node, visit, context);
      return ts.visitNode(tree, visit);
    }] } });
  assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
  return result.outputText;
}
const workspace = ts.createSourceFile('Workspace.tsx', read('src/components/Workspace.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
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
const validation = ['isObject', 'validId', 'invalidDraft', 'draftText', 'draftNumber', 'readMediaMetadata', 'validateDraft', 'writeDraftCache']
  .map(declaration).join('\n');

function harness(value = absent) {
  const requests = [], storage = new Map(), modules = new Map();
  let serverValue = value, started, timerId = 0;
  const timers = new Map();
  const globals = { URL, URLSearchParams, Headers, Response, FormData, Blob, AbortController, DOMException,
    Uint8Array, Uint32Array, DataView,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    location: { origin: 'https://synthetic.invalid' }, navigator: {}, XMLHttpRequest: denied,
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, text) => storage.set(key, text) },
    fetch: async (url, init = {}) => {
      requests.push({ url, init });
      if (url === '/api/session') return Response.json({ access_mode: 'development', csrf_token: null, expires_at: null });
      assert.equal(init.headers.get('X-Task-Token'), TOKEN);
      assert.equal(init.credentials, 'include');
      if (url === path && init.method === 'PATCH') {
        const body = JSON.parse(init.body);
        if (Object.hasOwn(body, 'source_voice_preferred')) serverValue = body.source_voice_preferred;
        return Response.json(snapshot());
      }
      if (url === path && (!init.method || init.method === 'GET')) return Response.json(snapshot());
      if (url === `${path}/files`) return Response.json({ files: [] });
      if (url === `${path}/files/${FID}`) return Response.json({ id: UP, name: 'source.mp4', bytes: 3, sec: 10,
        probe_ok: true, width: 64, height: 64, fps: 25, metadata_only: false, can_materialize: true,
        status: 'ready', has_speech: false, thumb_url: null, wave_url: null, asr_confidence: null,
        transcript: [], progress: 100, chunks: [0], sha256: 'a'.repeat(64) });
      if (url === `${path}/start`) {
        assert.equal(init.method, 'POST'); started = JSON.parse(init.body);
        return Response.json({ id: TASK, task_id: TASK, status: 'queued', accepted: true });
      }
      assert.fail(`Unexpected request ${url}`);
    } };
  function load(relative, overrides = {}) {
    const filename = resolve(root, relative);
    if (modules.has(filename) && !Object.keys(overrides).length) return modules.get(filename);
    if (filename.endsWith('.json')) return JSON.parse(readFileSync(filename, 'utf8'));
    const module = { exports: {} };
    modules.set(filename, module.exports);
    vm.runInNewContext(compile(readFileSync(filename, 'utf8'), filename), { ...globals, module, exports: module.exports,
      require(name) {
        if (Object.hasOwn(overrides, name)) return overrides[name];
        assert.ok(name.startsWith('.'), `Unbound dependency ${name}`);
        const base = resolve(dirname(filename), name);
        const dependency = [base, base + '.ts', base + '.json'].find(existsSync);
        assert.ok(dependency, name); return load(dependency);
      }, window: { confirm: () => true }, performance: { now: () => 0 },
    }, { filename, timeout: 1000 });
    return module.exports;
  }
  const modes = load('src/lib/productionModes.ts'), media = load('src/lib/mediaInput.ts');
  const api = load('src/lib/appApi.ts'), uploads = load('src/lib/uploadSessions.ts'), persistence = load('src/lib/v2Persistence.ts');
  const meta = { name: 'source.mp4', size: 3, lastModified: 123, type: 'video/mp4', kind: 'video', duration: 10,
    note: 'keep', trim_start: 0, trim_end: 10 };
  meta.id = media.fileIdentity(meta);
  function snapshot() {
    return { task_id: TASK, id: TASK, status: 'draft', script: 'Headline\nA complete synthetic news story.', mode: 'voiceover',
      preferences: modes.defaultModePreferences(), sentences: modes.parseModeScript('Headline\nA complete synthetic news story.', 'voiceover'),
      ...(serverValue === absent ? {} : { source_voice_preferred: serverValue }) };
  }
  const scope = { ...globals, exports: {}, ...modes, ...media, ...uploads, ...persistence, MAX_SCRIPT_LENGTH: 100000, MAX_DRAFT_LENGTH: 512 * 1024 };
  vm.runInNewContext(compile(validation + '\nthis.validate = validateDraft; this.save = writeDraftCache;'), scope);
  const get = api.createAppRequest({ taskId: TASK, token: TOKEN, signal: new AbortController().signal });
  async function seed() {
    const raw = await get(path);
    // Server editorial fields + existing client upload metadata/capability.
    // The task GET is NOT a Wizard Draft and must not be cast as one.
    return scope.validate({ ...raw, ...access, step: 2, files: [meta], elements: {}, sentenceChecks: {},
      uploadIds: [UP], uploadTokens: { [UP]: TOKEN }, uploadBindings: [{ file_id: meta.id, upload_id: UP,
        sha256: 'a'.repeat(64), chunk_size: 8388608, put_url: `${path}/files/${FID}/chunks`, draftTaskId: TASK, server_file_id: FID }] });
  }
  function mount(initialDraft) {
    const hooks = [], effects = [], drafts = [], submissions = [];
    let cursor = 0, dirty = true, tree;
    const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
    const react = { useId: () => 'source-voice-test',
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
    const wizard = load('src/components/CreateWizard.tsx', { react,
      'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' }, '../styles/modes.css': {},
      './ui/Icon': { __esModule: true, default: () => null } });
    const props = { initialDraft, limits: { max_files: 20, max_upload_bytes: 500 * 1024 ** 2,
      max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 }, generativeAllowed: false, busy: false,
      onDraft(d) { drafts.push(d); }, onError: denied,
      async onSubmit(d) { submissions.push(d); await api.uploadTask(d, new AbortController().signal, () => {}); } };
    function flush() { let guard = 0; do { if (dirty) { cursor = 0; dirty = false; tree = wizard.CreateWizard(props); }
      while (effects.length) effects.shift()(); assert.ok(++guard < 30); } while (dirty); }
    function nodes() { const out = []; function visit(n) { if (Array.isArray(n)) n.forEach(visit);
      else if (n && typeof n === 'object') { out.push(n); visit(n.props?.children); } } visit(tree); return out; }
    const text = n => n == null || typeof n === 'boolean' ? '' : Array.isArray(n) ? n.map(text).join('')
      : typeof n === 'object' ? text(n.props?.children) : String(n);
    function click(label) { const n = nodes().find(n => n.type === 'button' && text(n).trim() === label);
      assert.ok(n, label); assert.ok(!n.props.disabled, label); n.props.onClick(); flush(); }
    async function settle() { for (let i = 0; i < 6; i++) { for (let j = 0; j < 40; j++) await Promise.resolve(); flush(); } }
    flush(); return { drafts, submissions, click, settle, nodes, flush,
      unmount() { for (const hook of hooks) hook?.cleanup?.(); } };
  }
  return { api, modes, get, seed, mount, requests, scope, persistence, get started() { return started; } };
}
function check(value, expected) {
  assert.equal(Object.hasOwn(value, 'source_voice_preferred'), expected !== absent);
  assert.equal(value.source_voice_preferred, expected === absent ? undefined : expected);
  if (value.preferences) assert.equal(Object.hasOwn(value.preferences, 'source_voice_preferred'), false);
}

for (const value of [true, false, absent]) test(`GET -> wizard -> onDraft -> cache/reload -> submit -> start preserves ${String(value)}`, async () => {
  const h = harness(value), seed = await h.seed(), before = plain(seed);
  const w = h.mount(seed); await w.settle();
  check(w.drafts.at(-1), value);
  assert.ok(h.requests.every(r => !r.init.method || r.init.method === 'GET'), 'mount is read-only');
  w.click('下一步：选效果'); w.click('我自己读');
  check(w.drafts.at(-1), value); assert.equal(w.drafts.at(-1).preferences.voice, 'mine');
  h.scope.save(w.drafts.at(-1), Date.now());
  const restored = h.scope.validate(h.persistence.v2Persistence.readDraft().draft);
  check(restored, value); w.unmount();
  const again = h.mount(restored); await again.settle();
  check(again.drafts.at(-1), value); again.click('下一步：选效果'); again.click('开始制作'); await again.settle();
  assert.equal(again.submissions.length, 1); check(again.submissions[0], value); check(h.started, value);
  assert.equal(h.started.preferences.voice, 'mine');
  assert.equal(h.started.script, seed.script); assert.deepEqual(h.started.asset_options, [{ note: 'keep', trim_start: 0, trim_end: 10 }]);
  assert.deepEqual(plain(seed), before, 'original seed unchanged');
  assert.equal(h.requests.filter(r => r.url.endsWith('/start')).length, 1);
  assert.ok(h.requests.every(r => !r.init.method || ['GET', 'POST'].includes(r.init.method)), 'no invented draft PATCH on autosave');
  again.unmount();
});

for (const value of [true, false, absent]) test(`generic PATCH/GET preserves ${String(value)} without deriving it from mode preferences`, async () => {
  const h = harness(value === absent ? true : !value);
  // There is no production draft-PATCH serializer/autosave; exercise the actual
  // generic transport with the backend's top-level partial-update contract.
  const body = value === absent ? {} : { source_voice_preferred: value };
  const reply = await h.get(path, { method: 'PATCH', body: JSON.stringify(body) });
  check(reply, value === absent ? true : value);
  check(await h.get(path), value === absent ? true : value);
  assert.deepEqual(JSON.parse(h.requests.find(r => r.init.method === 'PATCH').init.body), body);
});

for (const mode of ['voiceover', 'mixed', 'original']) for (const value of [true, false, absent]) {
  test(`${mode} seed keeps independent ${String(value)} through draft emission`, async () => {
    const h = harness(value), seed = await h.seed();
    const script = 'Headline\nA complete synthetic news story.';
    const w = h.mount({ ...seed, mode, script, sentences: h.modes.parseModeScript(script, mode), preferences: h.modes.defaultModePreferences(mode) });
    await w.settle(); check(w.drafts.at(-1), value); w.unmount();
  });
}

for (const value of [null, 0, 1, 'true', 'false', {}, []]) test(`invalid ${JSON.stringify(value)} is rejected before any start request`, async () => {
  const h = harness();
  await assert.rejects(h.api.uploadTask({ ...access, source_voice_preferred: value }, new AbortController().signal, denied));
  assert.equal(h.requests.length, 0);
});

test('fresh wizard does not invent a source-voice value or a new control', () => {
  const h = harness(), w = h.mount(null); check(w.drafts.at(-1), absent);
  assert.equal(h.requests.length, 0);
  assert.equal(w.nodes().some(n => n.props?.name === 'source_voice_preferred'), false); w.unmount();
});