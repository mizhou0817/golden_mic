import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Complete production components/API transpiled in memory with deterministic
// hooks + synthetic transport. No real storage/network/browser/build/host.
// Probe exposes existing closures only; native ReactDOM scheduling is not claimed.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = p => readFileSync(resolve(root, p), 'utf8');
const plain = v => JSON.parse(JSON.stringify(v));
const forbidden = () => { throw Error('Forbidden side effect'); };
const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const jsx = (type, props) => ({ type, props: props ?? {} });
const flat = t => t == null || typeof t === 'boolean' ? [] : Array.isArray(t) ? t.flatMap(flat)
  : typeof t !== 'object' ? [t] : [t, ...flat(t.props?.children)];
const text = t => flat(t).filter(v => typeof v === 'string' || typeof v === 'number').join('');
function hooks() {
  let cursor = 0, dirty = true; const slots = [], pending = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    Fragment: 'fragment',
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, n => { const v = typeof n === 'function' ? n(slots[i].value) : n; if (!Object.is(v, slots[i].value)) { slots[i].value = v; dirty = true; } }]; },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: fn(), deps }; return slots[i].value; },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); }, useId() { return react.useMemo(() => 'history-id', []); },
    useEffect(effect, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps, effect }; pending.push(() => { old?.cleanup?.(); slots[i].cleanup = effect(); }); } },
  };
  return { react, begin() { cursor = 0; dirty = false; }, dirty: () => dirty,
    effects() { for (const e of pending.splice(0)) e(); },
    replay() { for (const s of slots) s?.cleanup?.(); for (const s of slots) if (s?.effect) s.cleanup = s.effect(); },
    stop() { for (const s of slots) s?.cleanup?.(); } };
}
function loader(globals = {}, stubs = {}, probe = '') {
  const cache = new Map();
  function load(p) {
    p = resolve(root, p); if (p in stubs) return stubs[p]; if (cache.has(p)) return cache.get(p);
    if (p.endsWith('.css')) return {}; if (p.endsWith('.json')) return JSON.parse(readFileSync(p, 'utf8'));
    let source = readFileSync(p, 'utf8').replaceAll('import.meta.env', '({})');
    if (probe && /components[\\/](Workspace|ResultWorkbench)\.tsx$/.test(p)) {
      const ast = ts.createSourceFile(p, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const component = ast.statements.find(n => ts.isFunctionDeclaration(n) && ['Workspace', 'ResultWorkbenchEditor'].includes(n.name?.text));
      const ret = component.body.statements.find(ts.isReturnStatement);
      source = source.slice(0, ret.getStart(ast)) + `globalThis.__probe = {${probe}};\n` + source.slice(ret.getStart(ast));
      if (component.name.text === 'ResultWorkbenchEditor') source += '\nexport { ResultWorkbenchEditor };';
    }
    const compiled = ts.transpileModule(source, { fileName: p, reportDiagnostics: true,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
    assert.equal(compiled.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
    const module = { exports: {} }; cache.set(p, module.exports);
    vm.runInNewContext(compiled.outputText, { module, exports: module.exports, URL, URLSearchParams, Headers, Response, FormData, Blob, File,
      AbortController, DOMException, setTimeout, clearTimeout, fetch: forbidden, ...globals,
      require(name) {
        if (name in stubs) return stubs[name]; assert.ok(name.startsWith('.'), name);
        const base = resolve(dirname(p), name), target = [base, base + '.ts', base + '.tsx', base + '.json'].find(existsSync);
        assert.ok(target, name); return load(target);
      },
      set __probe(value) { globals.probe = value; },
    }, { filename: p });
    return module.exports;
  }
  return load;
}
const api = loader()('src/lib/workbenchApi.ts');
const summary = loader()('src/lib/historyChecks.ts');
const unknown = '发布前检查尚未读取';
const gate = (patch = {}) => ({ revision: 2, blocking_count: 0, pending_count: 0, passed: true, checks: [], ...patch });
const issue = (patch = {}) => ({ key: 'listen:1', code: 'listen', level: 1, message: 'Listen once', sentence_id: null, action: '', checked: false, ...patch });
test('server counts/passed only; blocking priority; no row-derived authoritative floor or invented permission', () => {
  for (const [g, label] of [[gate(), '检查通过'], [gate({ passed: false, blocking_count: 7, pending_count: 9 }), '待修改 7 处'],
    [gate({ passed: false, pending_count: 3 }), '待确认 3 项'], [gate({ passed: false }), unknown],
    [gate({ passed: false, pending_count: 1, checks: [issue(), issue({ key: 'second' }), issue({ key: 'third', level: 0 })] }), '待确认 1 项']]) {
    const before = JSON.stringify(g), s = summary.parseHistoryChecks(g, 2);
    assert.equal(summary.historyChecksLabel(s), label); assert.equal(JSON.stringify(g), before);
    if (s) assert.deepEqual(Object.keys(s).sort(), ['blocking_count', 'passed', 'pending_count', 'revision']);
  }
  assert.equal(api.serverAllowsExport(gate({ passed: false }), 2), false);
});
for (const key of ['revision', 'blocking_count', 'pending_count']) for (const value of [-1, 0.5, NaN, Infinity, true, '0', null, undefined, Number.MAX_SAFE_INTEGER + 1]) {
  test(`strict ${key} rejects ${String(value)}`, () => assert.equal(summary.parseHistoryChecks(gate({ [key]: value }), 2), null));
}
test('revision/state/row consistency, full checks required; report cannot stand in for checks', () => {
  for (const g of [gate({ revision: 3 }), gate({ passed: 'true' }), gate({ blocking_count: 1 }), gate({ pending_count: 1 }),
    gate({ checks: [issue()] }), gate({ checks: [issue({ level: 0, checked: true })] }), gate({ checks: undefined }),
    gate({ checks: [issue({ checked: true }), issue({ checked: true })] }), { revision: 2, quality: { blocking_issue_count: 0 } }]) {
    assert.equal(summary.parseHistoryChecks(g, 2), null);
  }
  assert.equal(summary.parseHistoryChecks(gate(), true), null);
});

function runtime() {
  const h = hooks(), timers = new Map(), events = new Map(), storage = new Map(), writes = []; let seq = 0;
  const target = { addEventListener(n, f) { if (!events.has(n)) events.set(n, new Set()); events.get(n).add(f); }, removeEventListener(n, f) { events.get(n)?.delete(f); } };
  const globals = { localStorage: { getItem: k => storage.get(k) ?? null, setItem(k, v) { storage.set(k, v); writes.push(k); } },
    location: { origin: 'http://offline.test', pathname: '/', hash: '', href: 'http://offline.test/' },
    document: { ...target, hidden: false, documentElement: { style: {} }, getElementById: () => null },
    window: { ...target, confirm: () => true, matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }),
      history: { pushState() {}, replaceState() {} } },
    setTimeout(fn, delay) { const id = ++seq; timers.set(id, { fn, delay }); return id; }, clearTimeout(id) { timers.delete(id); },
    setInterval(fn, delay) { const id = ++seq; timers.set(id, { fn, delay }); return id; }, clearInterval(id) { timers.delete(id); },
  };
  const stubs = { react: h.react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' } };
  function mount(component, props = {}) {
    let tree;
    const render = () => { h.begin(); tree = component(props); h.effects(); return tree; };
    const settle = async () => { for (let i = 0; i < 12; i++) { await tick(); if (h.dirty()) render(); } };
    return { render, settle, tree: () => tree, props, probe: () => globals.probe };
  }
  return { ...h, globals, stubs, mount, timers, events, storage, writes };
}
const TOKEN = 'T'.repeat(43);
const historyRow = (id = 'A', patch = {}) => ({ taskId: id, title: id, accessToken: TOKEN, status: 'done', revision: 2, ...patch });
const taskStatus = (id, patch = {}) => ({ task_id: id, title: id, status: 'done', revision: 2, stages: [], progress: 100, ...patch });
function workspaceHarness() {
  const r = runtime(), calls = []; let rows = Array.from({ length: 100 }, (_, i) => historyRow(i ? `other-${i}` : 'A'));
  let handler = async (path) => taskStatus(path.split('/').at(-1));
  const app = { ...loader()('src/lib/appApi.ts'), readHistory: () => rows, rememberWork: forbidden,
    assertSameOriginConfiguration() {}, getPublicLimits: async () => ({ max_files: 20 }),
    getWorkspaceConfig: async () => ({ local_history: false, generative_fill_available: false }),
    publicRequest: async () => ({ status: 'ready' }), getLocalHistory: forbidden,
    createAppRequest(access) { return (path, init) => { calls.push({ path, access, init }); return handler(path, access, init); }; } };
  const modes = loader()('src/lib/productionModes.ts');
  Object.assign(r.stubs, { [resolve(root, 'src/lib/appApi.ts')]: app,
    [resolve(root, 'src/components/CreateWizard.tsx')]: { CreateWizard: 'Wizard', defaultPreferences: modes.defaultModePreferences },
    [resolve(root, 'src/components/ResultWorkbench.tsx')]: { __esModule: true, default: 'Workbench' },
    [resolve(root, 'src/components/Processing.tsx')]: { __esModule: true, default: 'Processing' },
    [resolve(root, 'src/components/ui/SampleView.tsx')]: { __esModule: true, default: 'Sample' } });
  const component = loader(r.globals, r.stubs, 'beginOpen, refreshHistory, processingStarted, navigate, selectedRef, onChecksChange, selectedChecks')('src/components/Workspace.tsx').default;
  const m = r.mount(component);
  return { ...r, ...m, calls, async open(id) { m.probe().beginOpen(id, 'result', { confirmed: true }); await m.settle(); },
    handler(fn) { handler = fn; }, async rows(next) { rows = next; m.probe().refreshHistory(); await m.settle(); }, getRows: () => rows };
}
test('actual Workspace: 100 rows make no gate GET; only selected done gets ephemeral label; no persistence', async () => {
  const h = workspaceHarness(); h.render(); await h.settle(); assert.equal(h.calls.length, 0);
  await h.open('A'); const cb = h.probe().onChecksChange; cb(gate()); await h.settle();
  const sidebar = flat(h.tree()).find(n => n?.props?.className === 'shell-sidebar');
  assert.equal((text(sidebar).match(/检查通过/g) ?? []).length, 1);
  assert.equal((text(sidebar).match(/发布前检查尚未读取/g) ?? []).length, 99);
  assert.deepEqual(h.calls.map(c => c.path), ['/api/tasks/A']); assert.equal(h.writes.length, 0);
  h.probe().navigate('create', false, true); await h.settle(); cb(gate()); await h.settle();
  assert.equal(h.probe().selectedChecks, null); h.stop();
});
test('actual Workspace: stale A to B, old A to new A, abort and old cleanup cannot erase new result', async () => {
  const h = workspaceHarness(); h.render(); await h.settle(); await h.open('A');
  const oldA = h.probe().onChecksChange, oldScope = h.probe().selectedRef.current;
  await h.open('other-1'); const b = h.probe().onChecksChange; b(gate({ passed: false, pending_count: 4 })); await h.settle();
  oldA(gate()); oldA(null); await h.settle(); assert.equal(h.probe().selectedChecks.pending_count, 4); assert.equal(oldScope.scope.signal.aborted, true);
  await h.open('A'); const newA = h.probe().onChecksChange; newA(gate()); await h.settle();
  oldA(null); oldA(gate({ passed: false, blocking_count: 8 })); await h.settle(); assert.equal(h.probe().selectedChecks.passed, true);
  h.probe().selectedRef.current.scope.abort(); await h.settle(); newA(gate()); await h.settle(); assert.equal(h.probe().selectedChecks, null); h.stop();
});
for (const [name, patch] of [['revision', { revision: 3 }], ['processing', { status: 'running' }], ['credential', { accessToken: 'N'.repeat(43) }],
  ['expiry', { expires_at: '2000-01-01T00:00:00Z' }], ['gone', { status: 'gone' }]]) {
  test(`actual Workspace: ${name} change invalidates and old callback stays fenced`, async () => {
    const h = workspaceHarness(); h.render(); await h.settle(); await h.open('A'); const cb = h.probe().onChecksChange;
    cb(gate()); await h.settle(); assert.ok(h.probe().selectedChecks);
    await h.rows(h.getRows().map(row => row.taskId === 'A' ? { ...row, ...patch } : row));
    cb(gate()); await h.settle(); assert.equal(h.probe().selectedChecks, null); h.stop();
  });
}
test('actual Workspace: expiry timer, processing entry, StrictMode cleanup invalidate observations without requests', async () => {
  const h = workspaceHarness(); h.render(); await h.settle();
  await h.rows(h.getRows().map(row => row.taskId === 'A' ? { ...row, expires_at: new Date(Date.now() + 10000).toISOString() } : row));
  await h.open('A'); const cb = h.probe().onChecksChange; cb(gate()); await h.settle();
  const timer = [...h.timers.values()].find(t => t.delay > 0 && t.delay <= 10000); assert.ok(timer); timer.fn(); await h.settle(); assert.equal(h.probe().selectedChecks, null);
  h.probe().processingStarted(); await h.settle(); cb(gate()); await h.settle(); assert.equal(h.probe().selectedChecks, null);
  h.replay(); await h.settle(); cb(gate()); await h.settle(); assert.equal(h.probe().selectedChecks, null); h.stop();
});

const context = (id = 'A') => ({ task_id: id, revision: 2, status: 'done', script: '', preferences: { pacing: 'normal' },
  report: { task_id: id, rows: [] }, timings: [], shots: [], current_shots: [], versions: [] });
function workbenchHarness(callback = () => {}) {
  const r = runtime(), calls = []; let handler = async path => path.endsWith('/context') ? context() : gate();
  const props = { taskId: 'A', accessToken: TOKEN, onChecksChange: callback, onProcessing() {}, onChanged() {}, onDelete() {}, onNew() {}, onError() {},
    request: async (path, init) => { calls.push({ path, init }); return handler(path, init); } };
  const component = loader(r.globals, r.stubs, 'reload, refreshChecks, confirmCheck, submitExport, canExport, canConfirmCheck, checks, invalidateChecks')('src/components/ResultWorkbench.tsx').ResultWorkbenchEditor;
  return { ...r, ...r.mount(component, props), calls, handler(fn) { handler = fn; } };
}
test('actual workbench existing GET relays exactly, optional callback changes do not reload editor', async () => {
  const values = [], h = workbenchHarness(v => values.push(plain(v))); h.render(); await h.settle();
  assert.equal(h.probe().canExport, true); assert.deepEqual(values.at(-1), gate());
  const count = h.calls.length; h.props.onChecksChange = undefined; h.render(); await h.settle(); assert.equal(h.calls.length, count);
  h.props.onChecksChange = v => values.push(plain(v)); h.render(); await h.settle(); assert.equal(h.calls.length, count);
  const held = deferred(); h.handler(() => held.promise); const work = h.probe().refreshChecks(2); assert.equal(values.at(-1), null);
  held.reject(Error('offline')); await work; await h.settle(); assert.equal(values.at(-1), null); assert.equal(h.probe().canExport, false); h.stop();
});
test('actual workbench held old request, newer read, old finally and StrictMode aborts cannot overwrite', async () => {
  const values = [], h = workbenchHarness(v => values.push(plain(v))); h.render(); await h.settle();
  const held = deferred(); h.handler(() => held.promise); const old = h.probe().refreshChecks(2), oldSignal = h.calls.at(-1).init.signal;
  h.handler(async () => gate({ passed: false, pending_count: 5 })); await h.probe().refreshChecks(2); await h.settle();
  held.resolve(gate()); await old; await h.settle(); assert.equal(oldSignal.aborted, true); assert.equal(values.at(-1).pending_count, 5);
  const strictHeld = deferred(); h.handler(path => path.endsWith('/context') ? Promise.resolve(context()) : strictHeld.promise);
  const pending = h.probe().refreshChecks(2), signal = h.calls.at(-1).init.signal; h.replay();
  assert.equal(signal.aborted, true); strictHeld.resolve(gate()); await pending; await h.settle();
  assert.equal(values.at(-1).passed, true); h.stop(); assert.equal(values.at(-1), null);
});
test('actual workbench confirmation invalidates immediately and relays server ACK, no count derivation', async () => {
  const values = [], h = workbenchHarness(v => values.push(plain(v)));
  h.handler(async path => path.endsWith('/context') ? context() : gate({ passed: false, pending_count: 9, checks: [issue()] }));
  h.render(); await h.settle(); const held = deferred(); h.handler(() => held.promise);
  h.probe().confirmCheck(h.probe().checks.checks[0], true); assert.equal(values.at(-1), null);
  assert.equal(h.calls.at(-1).init.method, 'PUT');
  held.resolve(gate({ checks: [issue({ checked: true })] })); await h.settle();
  assert.equal(values.at(-1).passed, true); assert.equal(h.probe().canExport, true); h.stop();
});
test('actual workbench historical/reload failure/mismatched revision are unknown; no export permission', async () => {
  const values = [], h = workbenchHarness(v => values.push(plain(v))); h.render(); await h.settle();
  h.handler(async path => path.endsWith('/context') ? context() : { task_id: 'A', revision: 1, rows: [] });
  await h.probe().reload(1); await h.settle(); assert.equal(values.at(-1), null); assert.equal(h.probe().canExport, false);
  h.handler(async () => { throw Error('failure'); }); await h.probe().reload(); await h.settle(); assert.equal(values.at(-1), null);
  h.handler(async () => gate({ revision: 3 })); await h.probe().refreshChecks(2); await h.settle(); assert.equal(values.at(-1), null); h.stop();
});
test('actual workbench export retains real POST gate, refresh invalidation and failure invalidation', async () => {
  const values = [], h = workbenchHarness(v => values.push(plain(v))); h.render(); await h.settle();
  const held = deferred(); h.handler((path, init) => init?.method === 'POST' ? held.promise : Promise.resolve(gate()));
  h.probe().submitExport(); assert.equal(values.at(-1), null); await h.settle(); assert.equal(values.at(-1).passed, true);
  assert.equal(h.calls.filter(c => c.init?.method === 'POST').length, 1);
  held.reject(Error('export failure')); await h.settle(); assert.equal(values.at(-1), null); assert.equal(h.probe().canExport, false); h.stop();
});
test('permission 410 proof classification is unchanged by display observation', () => {
  const app = loader()('src/lib/appApi.ts');
  for (const [status, code, expected] of [[410, 'task_gone', 'expired'], [410, undefined, 'inaccessible'], [404, 'task_gone', 'inaccessible'], [403, undefined, 'inaccessible']]) {
    assert.equal(app.goneReason(new app.AppApiError('synthetic', status, undefined, code)), expected);
  }
});

test('actual components together: held A response after B/new A cannot label or clear the new selection', async () => {
  const shell = workspaceHarness(); shell.render(); await shell.settle(); await shell.open('A');
  const oldCallback = shell.probe().onChecksChange, work = workbenchHarness(oldCallback);
  const held = deferred(); work.handler(path => path.endsWith('/context') ? Promise.resolve(context()) : held.promise);
  work.render(); await work.settle();
  await shell.open('other-1'); shell.probe().onChecksChange(gate({ passed: false, pending_count: 3 })); await shell.settle();
  await shell.open('A'); shell.probe().onChecksChange(gate({ passed: false, blocking_count: 6 })); await shell.settle();
  held.resolve(gate()); await work.settle(); await shell.settle(); assert.equal(shell.probe().selectedChecks.blocking_count, 6);
  work.stop(); await shell.settle(); assert.equal(shell.probe().selectedChecks.blocking_count, 6); shell.stop();
});
test('context read captures its original callback before await, no adoption by a later callback', async () => {
  const oldValues = [], newValues = [], h = workbenchHarness(v => oldValues.push(plain(v))), held = deferred();
  h.handler(path => path.endsWith('/context') ? held.promise : Promise.resolve(gate())); h.render();
  h.props.onChecksChange = v => newValues.push(v); h.render(); held.resolve(context()); await h.settle();
  assert.equal(oldValues.at(-1).passed, true); assert.equal(newValues.length, 0); h.stop();
});
test('old export failure cannot invalidate a newer successful checks refresh', async () => {
  const values = [], h = workbenchHarness(v => values.push(plain(v))); h.render(); await h.settle();
  const held = deferred(); h.handler((path, init) => init?.method === 'POST' ? held.promise : Promise.resolve(gate()));
  h.probe().submitExport(); await h.settle(); await h.probe().refreshChecks(2); await h.settle();
  held.reject(Error('old export failure')); await h.settle(); assert.equal(values.at(-1).passed, true); h.stop();
});
test('failed and stale export prechecks never POST or relay a passing summary', async () => {
  for (const reply of [gate({ revision: 3 }), gate({ passed: false, blocking_count: 4 })]) {
    const values = [], h = workbenchHarness(v => values.push(plain(v))); h.render(); await h.settle();
    h.handler(async () => reply); h.probe().submitExport(); await h.settle();
    assert.equal(values.at(-1), null); assert.equal(h.calls.filter(c => c.init?.method === 'POST').length, 0); h.stop();
  }
});

test('held export failure relays only to its captured callback, never a newer parent scope', async () => {
  const oldValues = [], nextValues = [], h = workbenchHarness(v => oldValues.push(plain(v))); h.render(); await h.settle();
  const held = deferred(); h.handler((path, init) => init?.method === 'POST' ? held.promise : Promise.resolve(gate()));
  h.probe().submitExport(); await h.settle(); h.props.onChecksChange = v => nextValues.push(v); h.render();
  held.reject(Error('old scope export failure')); await h.settle();
  assert.equal(oldValues.at(-1), null); assert.equal(nextValues.length, 0); h.stop();
});
test('actual Workspace held task GET: StrictMode abort and navigation fence reject late A completion', async () => {
  const h = workspaceHarness(); h.render(); await h.settle(); const a = deferred();
  h.handler((path) => path.endsWith('/A') ? a.promise : Promise.resolve(taskStatus('other-1')));
  h.probe().beginOpen('A', 'result', { confirmed: true }); await h.settle();
  const old = h.calls.at(-1); h.replay(); await h.settle(); assert.equal(old.access.signal.aborted, true);
  await h.open('other-1'); h.probe().onChecksChange(gate()); await h.settle();
  a.resolve(taskStatus('A')); await h.settle(); assert.equal(h.probe().selectedRef.current.id, 'other-1'); assert.equal(h.probe().selectedChecks.passed, true);
  await h.rows(h.getRows().filter(row => row.taskId !== 'other-1')); assert.equal(h.probe().selectedChecks, null); h.stop();
});