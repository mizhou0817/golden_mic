import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import postcss from 'postcss';

// Actual owned TS/TSX, transpiled only in memory. No services, network, real
// storage, media, provider calls, generated files, or shared dist. Hook tests
// are deterministic lifecycle contracts, NOT ReactDOM/browser/layout evidence.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = path => readFileSync(resolve(root, path), 'utf8');
const forbidden = () => { throw Error('Forbidden side effect'); };
const plain = value => JSON.parse(JSON.stringify(value));
function store(seed = {}) {
  const data = new Map(Object.entries(seed)), writes = [];
  return { data, writes, getItem: key => data.get(key) ?? null,
    setItem(key, value) { writes.push(key); data.set(key, value); } };
}
function loader(globals = {}, stubs = {}) {
  const cache = new Map();
  const load = path => {
    path = resolve(root, path);
    if (Object.hasOwn(stubs, path)) return stubs[path];
    if (cache.has(path)) return cache.get(path).exports;
    if (path.endsWith('.css')) return { default: new Proxy({}, { get: (_, key) => key }) };
    if (path.endsWith('.json')) return JSON.parse(readFileSync(path, 'utf8'));
    const result = ts.transpileModule(readFileSync(path, 'utf8'), { fileName: path, reportDiagnostics: true,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
    assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0, 'Syntax');
    const module = { exports: {} }; cache.set(path, module);
    new vm.Script(result.outputText, { filename: path }).runInNewContext({ module, exports: module.exports,
      URL, URLSearchParams, AbortController, DOMException, Headers, console, setTimeout, clearTimeout, setInterval, clearInterval,
      fetch: forbidden, localStorage: { getItem: forbidden, setItem: forbidden }, ...globals,
      require(name) {
        if (Object.hasOwn(stubs, name)) return stubs[name];
        assert.ok(name.startsWith('.'), `Unmocked dependency ${name}`);
        const full = resolve(dirname(path), name);
        const target = [full, `${full}.ts`, `${full}.tsx`].find(p => existsSync(p));
        assert.ok(target, `Dependency exists: ${name}`); return load(target);
      },
    }, { timeout: 10_000 });
    return module.exports;
  };
  return load;
}
const persistence = loader()('src/lib/v2Persistence.ts');
const KEY = persistence.V2_KEY;
const fresh = storage => persistence.createV2Persistence(() => storage);
const envelope = (patch = {}) => JSON.stringify({ schemaVersion: 1, ...patch });

test('read-only initialization preserves all three legacy keys and creates no v2 key', () => {
  const storage = store({ 'gm-core-v1': '{"draft":null}', 'golden-mic.draft.v2': '{"script":"older"}', 'golden-mic.font-size': '20' });
  const p = fresh(storage); assert.equal(p.readDraft(), null); assert.equal(p.readPreferences().bigFont, true);
  assert.equal(storage.writes.length, 0); assert.equal(storage.data.has(KEY), false);
});
test('first deliberate save archives exact legacy strings and leaves originals byte-equal', () => {
  const legacy = { 'gm-core-v1': '\ufeff {"tasks":{"x":{"status":"held"}},"pend":{"x":1}} ', 'golden-mic.draft.v2': '{"draft":"old"}', 'golden-mic.font-size': '20' };
  const storage = store(legacy), p = fresh(storage); p.readDraft(); p.writeDraft({ script: 'new' }, 1);
  const root = JSON.parse(storage.getItem(KEY)); assert.deepEqual(root.archive.legacy, legacy);
  for (const [key, value] of Object.entries(legacy)) assert.equal(storage.getItem(key), value);
  assert.equal(root.tasks, undefined); assert.equal(root.pend, undefined); assert.equal(root.draftVersion, 3);
});
test('latest-read draft merge retains another writer pending/checked/receipts/archive', () => {
  const storage = store({ [KEY]: envelope({ draftVersion: 3, draft: null, draftSavedAt: null }) }), p = fresh(storage);
  p.readDraft(); const latest = { ...JSON.parse(storage.getItem(KEY)), pend: { resultV2: { task: { schema: 2, draft: { base: 4 } } } }, checked: { a: true }, receipt: { id: 'opaque' }, archive: { keep: true } };
  storage.data.set(KEY, JSON.stringify(latest)); p.writeDraft({ script: 'edited' }, 2);
  const result = JSON.parse(storage.getItem(KEY)); for (const key of ['pend', 'checked', 'receipt', 'archive']) assert.deepEqual(result[key], latest[key]);
});
test('result-first envelope can be adopted without dropping pending slot', () => {
  const storage = store({ [KEY]: JSON.stringify({ pend: { resultV2: { x: { draft: { base: 1 } } } } }) }), p = fresh(storage);
  p.readDraft(); p.writeDraft(null, null); assert.equal(JSON.parse(storage.getItem(KEY)).pend.resultV2.x.draft.base, 1);
  assert.equal(JSON.parse(storage.getItem(KEY)).schemaVersion, 1);
});
test('same-slot cross-tab conflict fails closed with latest value intact', () => {
  const storage = store(), a = fresh(storage), b = fresh(storage); a.readDraft(); b.readDraft();
  b.writeDraft({ script: 'other tab' }, 1); const raw = storage.getItem(KEY);
  assert.throws(() => a.writeDraft({ script: 'stale' }, 2)); assert.equal(storage.getItem(KEY), raw);
});
test('preference slots merge independently but reject a changed same-slot baseline', () => {
  const storage = store(), a = fresh(storage), b = fresh(storage); a.readPreferences(); b.readPreferences();
  a.writePreference('bigFont', true); b.writePreference('histSort', 'title');
  assert.equal(JSON.parse(storage.getItem(KEY)).bigFont, true);
  assert.throws(() => b.writePreference('bigFont', false));
  a.writePreference('introDismissed', true); assert.equal(JSON.parse(storage.getItem(KEY)).histSort, 'title');
});
for (const raw of ['invalid', '[]', envelope({ schemaVersion: 7 }), '{"draft":{"script":"unversioned"}}']) test('malformed/unsupported envelope never overwritten: ' + raw.slice(0, 24), () => {
  const storage = store({ [KEY]: raw }), p = fresh(storage); assert.throws(() => p.readDraft());
  assert.throws(() => p.writeDraft(null, null)); assert.equal(storage.getItem(KEY), raw); assert.equal(storage.writes.length, 0);
});
test('quota error and readback mismatch are not success', () => {
  const p = fresh({ getItem: () => null, setItem: () => { throw Error('quota'); } }); p.readDraft(); assert.throws(() => p.writeDraft(null, null));
  const q = fresh({ getItem: () => null, setItem() {} }); q.readDraft(); assert.throws(() => q.writeDraft(null, null));
});
test('pre-write race is detected before setItem', () => {
  const storage = store(), p = fresh(storage); p.readDraft(); let n = 0;
  const q = fresh({ getItem(key) { if (key === KEY && ++n === 2) return envelope({ pend: { concurrent: true } }); return storage.getItem(key); }, setItem: storage.setItem });
  assert.throws(() => q.writeDraft(null, null)); assert.equal(storage.writes.length, 0);
});

const jsx = (type, props) => ({ type, props: props ?? {} });
const jsxRuntime = { jsx, jsxs: jsx, Fragment: 'fragment' };
const apiPath = resolve(root, 'src/lib/appApi.ts');
const wizardPath = resolve(root, 'src/components/CreateWizard.tsx');
const workspaceSource = read('src/components/Workspace.tsx');
const ast = ts.createSourceFile('Workspace.tsx', workspaceSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
function declaration(name) {
  const found = [];
  function visit(node) { if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText(ast) === name) found.push(node); ts.forEachChild(node, visit); }
  visit(ast); assert.equal(found.length, 1);
  return (ts.isVariableDeclaration(found[0]) ? 'const ' : '') + printer.printNode(ts.EmitHint.Unspecified, found[0], ast) + ';';
}
const routes = {};
vm.runInNewContext(ts.transpileModule(`${declaration('validId')} ${declaration('readRoute')} ${declaration('hashFor')} this.routes = { readRoute, hashFor };`,
  { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText, { URLSearchParams, ...routes, get routes() { return routes; }, set routes(v) { Object.assign(routes, v); } });
for (const [hash, pathname, expected] of [
  ['', '/', { page: 'create' }], ['', '/tasks/task-A', { page: 'work', id: 'task-A' }],
  ['#work=task-A', '/', { page: 'work', id: 'task-A' }], ['#show=task-A', '/', { page: 'work', id: 'task-A' }],
  ['#history', '/', { page: 'create', drawer: true }], ['', '/samples/default', { page: 'sample' }],
  ['#work=a&show=b', '/', { page: 'gone', invalid: true }], ['', '/tasks/a%2Fb', { page: 'gone', invalid: true }],
]) test(`route ${pathname}${hash}`, () => assert.deepEqual(plain(routes.readRoute(hash, pathname)), expected));
test('canonical work links contain only the task path', () => assert.equal(routes.hashFor('task-A'), '/tasks/task-A'));

function hooks() {
  let cursor = 0, dirty = true; const slots = [], effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => { const v = typeof next === 'function' ? next(slots[i].value) : next; if (!Object.is(v, slots[i].value)) { slots[i].value = v; dirty = true; } }]; },
    useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i]; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: fn(), deps }; return slots[i].value; },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); }, useId() { return react.useMemo(() => 'id', []); },
    useEffect(effect, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps, effect, cleanup: old?.cleanup }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); } },
  };
  return { react, begin() { cursor = 0; dirty = false; }, dirty: () => dirty,
    effects() { for (const effect of effects.splice(0)) effect(); },
    strictReplay() { for (const slot of slots) if (slot?.effect) slot.cleanup?.(); for (const slot of slots) if (slot?.effect) slot.cleanup = slot.effect(); },
    stop() { for (const slot of slots) slot?.cleanup?.(); },
  };
}
const flat = tree => tree == null || typeof tree === 'boolean' ? [] : Array.isArray(tree) ? tree.flatMap(flat) : typeof tree !== 'object' ? [tree] : [tree, ...flat(tree.props?.children)];
const text = tree => flat(tree).filter(v => typeof v === 'string' || typeof v === 'number').join('');
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function harness(pathname = '/', seed = {}) {
  const h = hooks(), storage = store(seed), calls = [], pending = [], listeners = new Map(), timers = new Map(); let timer = 0;
  const location = { pathname, hash: '', search: '', origin: 'http://localhost', href: 'http://localhost' + pathname };
  const history = { pushState(_, __, path) { const u = new URL(path, location.origin); Object.assign(location, { pathname: u.pathname, hash: u.hash, href: u.href }); }, replaceState(...args) { this.pushState(...args); } };
  class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
  const appApi = { AppApiError: ApiError, HISTORY_KEY: 'history', assertSameOriginConfiguration() {}, readHistory: () => [],
    getPublicLimits: async () => ({ max_files: 20 }), getWorkspaceConfig: async () => ({ local_history: true, generative_fill_available: false }),
    publicRequest: async path => { calls.push(['public', path]); return { status: 'ready' }; },
    getLocalHistory: async () => [], errorText: e => e.message, taskMedia: () => '/api/tasks/a/video', parseTask: v => v,
    createAppRequest(access) { return (path, init) => { calls.push(['task', path, access, init]); return new Promise((resolve, reject) => pending.push({ resolve, reject, access })); }; },
    rememberWork: forbidden, forgetHistory: forbidden, uploadTask: forbidden, parseReceipt: v => v,
  };
  const modes = loader()('src/lib/productionModes.ts');
  const globals = { localStorage: storage, location, document: { hidden: false, documentElement: { style: { fontSize: '' } }, getElementById: () => null, querySelector: () => null, addEventListener() {}, removeEventListener() {} },
    window: { history, confirm: () => true, matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
      addEventListener(name, fn, capture) { if (!capture) listeners.set(name, fn); }, removeEventListener(name) { listeners.delete(name); } },
    setTimeout(fn, delay) { const id = ++timer; timers.set(id, { fn, delay }); return id; }, clearTimeout(id) { timers.delete(id); },
  };
  const load = loader(globals, { react: h.react, 'react/jsx-runtime': jsxRuntime, [apiPath]: appApi,
    [wizardPath]: { CreateWizard: 'Wizard', defaultPreferences: modes.defaultModePreferences },
    [resolve(root, 'src/components/Processing.tsx')]: { __esModule: true, default: 'Processing' },
    [resolve(root, 'src/components/ResultWorkbench.tsx')]: { __esModule: true, default: 'Result' },
    [resolve(root, 'src/components/ui/SampleView.tsx')]: { __esModule: true, default: 'Sample' },
  });
  const api = load('src/components/Workspace.tsx'); let tree;
  function render() { h.begin(); tree = api.default(); h.effects(); return tree; }
  async function settle() { for (let i = 0; i < 8; i++) { await tick(); if (h.dirty()) render(); } return tree; }
  return { ...h, api, storage, calls, pending, timers, location, listeners, render, settle, tree: () => tree, modes };
}
test('real shell has introduction, sidebar history, sample action, no Studio or history-page entry', async () => {
  const h = harness(); h.render(); await h.settle();
  const output = text(h.tree()); assert.ok(output.includes('我的作品')); assert.ok(output.includes('看一条范例作品'));
  assert.ok(!output.includes('专业剪辑台')); assert.ok(!flat(h.tree()).some(n => n?.props?.className === 'shell-history-panel'));
  assert.equal(h.storage.writes.length, 0); h.stop();
});
test('deep-link StrictMode replay never mounts wizard or writes draft; initial GET still resolves', async () => {
  const h = harness('/tasks/task-A'); h.render(); h.strictReplay(); await h.settle();
  assert.ok(!flat(h.tree()).some(n => n?.type === 'Wizard')); assert.equal(h.storage.writes.length, 0);
  assert.equal(h.pending.length, 1); const request = h.pending[0];
  request.resolve({ task_id: 'task-A', status: 'done', revision: 0, mode: 'voiceover' }); await h.settle();
  assert.ok(flat(h.tree()).some(n => n?.type === 'Result')); assert.equal(h.location.pathname, '/tasks/task-A'); h.stop();
});
test('native Back aborts held task read and stale completion cannot reopen it', async () => {
  const h = harness('/tasks/task-A'); h.render(); await h.settle(); const held = h.pending[0];
  h.location.pathname = '/'; h.location.href = 'http://localhost/'; h.listeners.get('popstate')(); await h.settle();
  assert.equal(held.access.signal.aborted, true); held.resolve({ task_id: 'task-A', status: 'done', revision: 0 }); await h.settle();
  assert.ok(flat(h.tree()).some(n => n?.type === 'Wizard')); assert.ok(!flat(h.tree()).some(n => n?.type === 'Result')); h.stop();
});
test('actual draft validator retains paired issued capability, rejects incomplete and cross-task bindings', () => {
  const h = harness();
  const draft = { script: 'Title\nBody。', step: 1, files: [], elements: {}, sentenceChecks: {}, preferences: h.modes.defaultModePreferences('voiceover'), draftTaskId: 'task-A', draftTaskToken: 'synthetic_'.padEnd(48, 'x') };
  const safe = h.api.validateDraft(draft); assert.equal(safe.draftTaskId, 'task-A'); assert.equal(safe.draftTaskToken, draft.draftTaskToken);
  assert.throws(() => h.api.validateDraft({ ...draft, draftTaskToken: undefined }));
  assert.throws(() => h.api.validateDraft({ ...draft, draftTaskToken: 'https://unsafe' }));
  assert.throws(() => h.api.validateDraft({ ...draft, legacyRestore: true }));
});
test('actual cache signatures migrate without old-key writes and null tombstone prevents resurrection', () => {
  const modes = loader()('src/lib/productionModes.ts');
  const draft = { script: 'Title\nBody。', step: 1, files: [], elements: {}, sentenceChecks: {}, preferences: modes.defaultModePreferences('voiceover') };
  const old = JSON.stringify({ draftVersion: 3, draft, draftSavedAt: 1, tasks: { old: { status: 'held' } } });
  const h = harness('/', { 'gm-core-v1': old }); const cache = h.api.readDraftCache();
  assert.equal(cache.migrate, true); assert.equal(cache.value.legacyRestore, true); assert.equal(h.storage.writes.length, 0);
  h.api.writeDraftCache(cache.value, 2); assert.equal(h.storage.getItem('gm-core-v1'), old);
  h.api.writeDraftCache(null, null); assert.equal(h.api.readDraftCache().value, null); assert.equal(h.storage.getItem('gm-core-v1'), old);
});
test('actual writer uses 300ms only after a user draft change, not mount normalization', async () => {
  const h = harness(); h.render(); await h.settle(); let wizard = flat(h.tree()).find(n => n?.type === 'Wizard');
  const draft = { script: '', step: 1, files: [], elements: {}, sentenceChecks: {}, preferences: h.modes.defaultModePreferences('voiceover') };
  wizard.props.onDraft(draft); assert.equal(h.timers.size, 0);
  wizard.props.onDraft({ ...draft, script: 'Title\nBody。' });
  assert.equal([...h.timers.values()].filter(t => t.delay === 300).length, 1);
  [...h.timers.values()].find(t => t.delay === 300).fn(); assert.equal(JSON.parse(h.storage.getItem(KEY)).draft.script, 'Title\nBody。'); h.stop();
});
test('header order and font copy match handoff; collapsed post-form manual save still flushes', async () => {
  const h = harness(); h.render(); await h.settle();
  const nodes = () => flat(h.tree());
  const header = nodes().find(n => n?.type === 'header');
  const classes = flat(header).map(n => n?.props?.className);
  assert.ok(classes.indexOf('shell-brand') < classes.indexOf('shell-font-toggle'));
  assert.ok(classes.indexOf('shell-font-toggle') < classes.indexOf('shell-help'));
  assert.ok(classes.indexOf('shell-help') < classes.indexOf('shell-header-actions'));
  assert.ok(text(header).includes('服务正常')); assert.ok(!text(header).includes('制作服务就绪'));
  let toggle = nodes().find(n => n?.props?.className === 'shell-font-toggle');
  assert.equal(text(toggle), '大字'); toggle.props.onClick(); await h.settle();
  toggle = nodes().find(n => n?.props?.className === 'shell-font-toggle');
  assert.equal(text(toggle), '常规字号'); assert.equal(toggle.props['aria-pressed'], true);
  const wizard = nodes().find(n => n?.type === 'Wizard'); assert.ok(wizard.props.intro);
  const detail = nodes().find(n => n?.props?.className === 'shell-draft-details');
  assert.equal(detail.type, 'details'); assert.equal(detail.props.open, undefined);
  assert.ok(nodes().indexOf(detail) > nodes().indexOf(wizard));
  const draft = { script: '', step: 1, files: [], elements: {}, sentenceChecks: {}, preferences: h.modes.defaultModePreferences() };
  wizard.props.onDraft(draft); wizard.props.onDraft({ ...draft, script: 'Title\nManual save body。' });
  const manual = flat(detail).find(n => n?.type === 'button' && text(n) === '保存草稿');
  manual.props.onClick(); await h.settle();
  assert.equal(JSON.parse(h.storage.getItem(KEY)).draft.script, 'Title\nManual save body。'); h.stop();
});
test('owned palette, six animations, exact SVG paths and 17px mobile baseline', () => {
  const css = postcss.parse(read('src/components/ui/tokens.css'));
  const animations = []; css.walkAtRules('keyframes', r => animations.push(r.params));
  assert.deepEqual(animations.sort(), ['gm-blink', 'gm-check', 'gm-pop', 'gm-rise', 'gm-spin', 'gm-wave']);
  for (const [name, value] of Object.entries({ ink: '#4a3626', muted: '#75645a', line: '#dccfbb', teal: '#237f4a', red: '#c94a2c', gold: '#f2b632', paper: '#f9f3e6', green: '#e7f3ea' })) {
    let found = false; css.walkDecls(`--${name}`, d => { assert.equal(d.value, value); found = true; }); assert.ok(found);
  }
  const styles = read('src/styles.css'); assert.match(styles, /max-width:600px\)\{:root\{font-size:17px/); assert.match(styles, /solid var\(--gold\)/);
  const icon = read('src/components/ui/Icon.tsx'); assert.match(icon, /M5 12\.5l4\.5 4\.5L19 7/); assert.match(icon, /aria-hidden="true"/); assert.ok(!icon.includes('lucide'));
});
test('sample view issues one GET boundary, shows 503 honestly, aborts on cleanup', async () => {
  const h = hooks(), calls = []; let reject;
  class ApiError extends Error { constructor(status) { super('unavailable'); this.status = status; } }
  const load = loader({}, { react: h.react, 'react/jsx-runtime': jsxRuntime, [apiPath]: { AppApiError: ApiError, publicRequest(path, signal) { calls.push({ path, signal }); return new Promise((_, no) => { reject = no; }); } } });
  const { default: Sample } = load('src/components/ui/SampleView.tsx');
  h.begin(); Sample({ onBack() {} }); h.effects(); assert.equal(calls.length, 1); assert.equal(calls[0].path, '/api/samples/default');
  reject(new ApiError(503)); await tick(); h.begin(); const tree = Sample({ onBack() {} }); h.effects(); assert.ok(text(tree).includes('不会用演示数据代替真实成片'));
  h.stop(); assert.equal(calls[0].signal.aborted, true);
});
test('sample parser rejects writable data and token/external media URLs', () => {
  const load = loader({}, { react: {}, 'react/jsx-runtime': jsxRuntime, [apiPath]: {},
    [resolve(root, 'src/lib/workbenchApi.ts')]: { parseReport(value, id) { assert.equal(value.task_id, id); return value; } } });
  const { parseSample } = load('src/components/ui/SampleView.tsx');
  const value = { read_only: true, task_id: 'sample-A', title: 'Verified sample', report: { task_id: 'sample-A', rows: [] }, video_url: '/api/samples/default/video?revision=0' };
  assert.equal(parseSample(value).video, value.video_url);
  for (const video_url of ['https://example.org/video', '/api/tasks/a/video?access_token=bad', '//example.org/a', '/api/samples/default/video?token=bad']) assert.throws(() => parseSample({ ...value, video_url }));
  assert.throws(() => parseSample({ ...value, read_only: false }));
});