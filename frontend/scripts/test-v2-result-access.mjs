import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import ts from 'typescript';
import nodeTest from 'node:test';
const test = process.argv[1]?.endsWith('test-v2-result-access.mjs') ? nodeTest : () => {};

// Reuse only the unchanged history suite's deterministic hook/loader utilities,
// not its test registration or mocked API. Full TSX and real appApi decoder run
// in memory; all HTTP is synthetic, storage/timers are maps, no host or build.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = readFileSync(resolve(root, 'scripts/test-v2-history-checks.mjs'), 'utf8');
const ast = ts.createSourceFile('harness.mjs', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = new Set(['plain', 'forbidden', 'tick', 'deferred', 'jsx', 'flat', 'text', 'hooks', 'loader', 'runtime']);
const shared = ast.statements.filter(n => ts.isFunctionDeclaration(n) ? names.has(n.name?.text)
  : ts.isVariableStatement(n) && n.declarationList.declarations.every(d => names.has(d.name.getText(ast))));
const env = { root, assert, existsSync, readFileSync, resolve, dirname, vm, ts, URL, URLSearchParams, Headers, Response, FormData, Blob, File,
  AbortController, DOMException, setTimeout, clearTimeout };
vm.createContext(env); vm.runInContext(shared.map(n => n.getText(ast)).join('\n') + '\nglobalThis.helpers = {runtime, loader, flat, text, deferred, tick};', env);
const { runtime, loader, flat, text, deferred } = env.helpers;
export const TOKEN = 'T'.repeat(43);
export const gate = () => ({ revision: 2, blocking_count: 0, pending_count: 0, passed: true, checks: [] });
export const status = (id = 'A', patch = {}) => ({ task_id: id, title: id, status: 'done', revision: 2, stages: [], progress: 100, ...patch });
const context = (id = 'A') => ({ ...status(id), script: '', preferences: { pacing: 'normal' },
  report: { task_id: id, rows: [] }, timings: [], shots: [], current_shots: [], versions: [] });
const response = (body, code = 200) => new Response(JSON.stringify(body), { status: code });
export function harness(kind = 'workbench', patch = {}) {
  const r = runtime(), calls = [];
  const history = ['A', 'B'].map(id => ({ taskId: id, title: id, status: patch.status ?? 'done', revision: 2,
    createdAt: '2026-09-29T00:00:00Z', accessToken: TOKEN }));
  r.storage.set('golden-mic.history.v1', JSON.stringify(history));
  let handler = async path => path.endsWith('/context') ? response(context()) : path.endsWith('/checks') ? response(gate()) : response(status(path.split('/').at(-1), patch));
  r.globals.fetch = async (path, init) => {
    calls.push({ path, init });
    if (path === '/api/config/workspace') return response({ local_history: false, generative_fill_available: false });
    if (path === '/api/config/availability') return response({ status: 'ready' });
    if (path === '/api/session') return response({ access_mode: 'development', csrf_token: null, expires_at: null });
    if (path === '/api/config/limits') return response({ max_files: 20, max_upload_bytes: 100, max_total_upload_bytes: 1000, max_script_length: 8000 });
    assert.ok(path.startsWith('/api/tasks/'), path);
    return handler(path, init);
  };
  if (kind === 'workspace') Object.assign(r.stubs, {
    [resolve(root, 'src/components/CreateWizard.tsx')]: { CreateWizard: 'Wizard', defaultPreferences: loader()('src/lib/productionModes.ts').defaultModePreferences },
    [resolve(root, 'src/components/ResultWorkbench.tsx')]: { __esModule: true, default: 'Workbench' },
    [resolve(root, 'src/components/ui/SampleView.tsx')]: { __esModule: true, default: 'Sample' },
  });
  const probe = kind === 'workspace' ? 'beginOpen, refreshSelected, selectedRef, onChecksChange, selectedChecks, page, task, missingReason, browserRows, generation, deleteSelected, dialog, editFailedDraft, retryOriginal'
    : 'reload, refreshChecks, submitExport, context, report, checks, error, checksError, draftRef, drafts, updateDraft, writer, run';
  const load = loader(r.globals, r.stubs, probe), app = load('src/lib/appApi.ts');
  const scope = new AbortController();
  const props = { taskId: 'A', accessToken: TOKEN, request: app.createAppRequest({ taskId: 'A', token: TOKEN, signal: scope.signal }),
    onProcessing() {}, onChanged() {}, onDelete() {}, onNew() {}, onError() {} };
  const component = kind === 'workspace' ? load('src/components/Workspace.tsx').default : load('src/components/ResultWorkbench.tsx').ResultWorkbenchEditor;
  const m = r.mount(component, props);
  return { ...r, ...m, app, load, calls, scope, handler(fn) { handler = fn; },
    deny(code, detail) { handler = async () => response({ detail }, code); },
    taskCalls: () => calls.filter(c => c.path.startsWith('/api/tasks/')),
    async open(id) { m.probe().beginOpen(id, 'result', { confirmed: true }); await m.settle(); } };
}
export { flat, text, response };

for (const [code, detail, reason] of [[401, '', 'inaccessible'], [403, '', 'inaccessible'], [404, { code: 'task_gone' }, 'inaccessible'],
  [410, '', 'inaccessible'], [410, { code: 'other' }, 'inaccessible'], [410, { code: 'task_gone' }, 'expired']]) {
  for (const route of ['reload', 'checks', 'visibility', 'operation', 'export-precheck']) test(`actual decoder ${code}/${JSON.stringify(detail)} via ${route}`, async () => {
    const h = harness(), failures = []; h.props.onUnavailable = e => failures.push(e);
    h.render(); await h.settle();
    if (route === 'operation') {
      h.probe().updateDraft(d => ({ ...d, base: 2, submitted: 3, operationId: 'a'.repeat(32) })); await h.settle();
      h.handler(async path => path.endsWith('/context') ? response(context()) : response({ detail }, code));
    } else h.deny(code, detail);
    const before = h.taskCalls().length;
    if (route === 'checks') await h.probe().refreshChecks(2);
    else if (route === 'export-precheck') h.probe().submitExport();
    else if (route === 'visibility') { for (const f of [...h.events.get('visibilitychange')]) f(); }
    else await h.probe().reload();
    await h.settle();
    assert.equal(failures.length, 1); assert.ok(failures[0] instanceof h.app.AppApiError);
    assert.equal(h.app.goneReason(failures[0]), reason);
    assert.equal(h.probe().context, null); assert.equal(h.probe().report, null); assert.equal(h.probe().checks, null);
    assert.equal(h.taskCalls().length - before, route === 'operation' ? 2 : 1);
    assert.ok(h.taskCalls().every(c => !c.init.method || c.init.method === 'GET')); h.stop();
  });
}
for (const mode of ['network', 'generic-status', '409', '500']) test(`${mode} remains a panel error, no host-gone or purge`, async () => {
  const h = harness(), failures = []; h.props.onUnavailable = e => failures.push(e); h.render(); await h.settle();
  h.probe().updateDraft(d => ({ ...d, base: 2, sentences: { 1: { text: 'pending' } } }));
  if (mode === 'network' || mode === 'generic-status') h.handler(async () => { throw Object.assign(Error('offline'), mode === 'generic-status' ? { status: 410, code: 'task_gone' } : {}); });
  else h.deny(Number(mode), { code: 'task_gone' });
  await h.probe().refreshChecks(2); await h.settle(); assert.ok(h.probe().checksError); assert.ok(h.probe().report);
  await h.probe().reload(); await h.settle(); assert.ok(h.probe().error); assert.equal(failures.length, 0);
  assert.equal(h.probe().draftRef.current.sentences[1].text, 'pending'); h.stop();
});
test('parent callback clears only current read projections and summary, keeps capability/history and scope', async () => {
  const h = harness('workspace'); h.render(); await h.settle(); await h.open('A');
  h.probe().onChecksChange(gate()); await h.settle(); assert.ok(h.probe().selectedChecks);
  const child = flat(h.tree()).find(n => n.type === 'Workbench'), selection = h.probe().selectedRef.current;
  const before = h.storage.get('golden-mic.history.v1'), count = h.calls.length;
  assert.equal(typeof child.props.onUnavailable, 'function');
  child.props.onUnavailable(new h.app.AppApiError('gone', 410, undefined, 'task_gone')); await h.settle();
  assert.equal(h.probe().page, 'gone'); assert.equal(h.probe().task, null); assert.equal(h.probe().selectedChecks, null);
  assert.equal(h.probe().missingReason, 'expired'); assert.equal(selection.scope.signal.aborted, false);
  assert.equal(h.storage.get('golden-mic.history.v1'), before); assert.equal(h.calls.length, count); h.stop();
});
test('held old A after B and same A new scope, StrictMode replay and selected abort are fenced', async () => {
  const h = harness('workspace'); h.render(); h.replay(); await h.settle(); await h.open('A');
  const old = flat(h.tree()).find(n => n.type === 'Workbench').props.onUnavailable;
  await h.open('B'); old?.(new h.app.AppApiError('gone', 410, undefined, 'task_gone')); await h.settle(); assert.equal(h.probe().page, 'result');
  await h.open('A'); old?.(new h.app.AppApiError('gone', 404)); await h.settle(); assert.equal(h.probe().page, 'result');
  const current = flat(h.tree()).find(n => n.type === 'Workbench').props.onUnavailable;
  h.probe().selectedRef.current.scope.abort(); current?.(new h.app.AppApiError('gone', 410, undefined, 'task_gone'));
  await h.settle(); assert.equal(h.probe().page, 'result'); assert.equal(h.probe().selectedRef.current.id, 'A'); h.stop();
});
for (const [code, reason] of [[404, 'inaccessible'], [410, 'expired'], [409, null]]) test(`parent refreshSelected classifies actual ${code}`, async () => {
  const h = harness('workspace'); h.render(); await h.settle(); await h.open('A'); h.deny(code, { code: 'task_gone' });
  const before = h.taskCalls().length; h.probe().refreshSelected(); await h.settle();
  assert.equal(h.probe().page, reason ? 'gone' : 'result'); if (reason) assert.equal(h.probe().missingReason, reason);
  assert.equal(h.taskCalls().length - before, 1); assert.equal(h.probe().browserRows[0].accessToken, TOKEN); h.stop();
});
test('held read captures callback before await; StrictMode old rejection cannot evict new editor', async () => {
  const h = harness(), old = [], next = [], held = deferred(); h.props.onUnavailable = e => old.push(e);
  h.handler(() => held.promise); h.render(); h.props.onUnavailable = e => next.push(e); h.render();
  held.resolve(response({}, 404)); await h.settle(); assert.equal(old.length, 1); assert.equal(next.length, 0); h.stop();
  const s = harness(), stale = deferred(), errors = []; s.props.onUnavailable = e => errors.push(e);
  s.handler(() => stale.promise); s.render();
  s.handler(async path => response(path.endsWith('/context') ? context() : gate())); s.replay(); await s.settle();
  stale.resolve(response({ detail: { code: 'task_gone' } }, 410)); await s.settle(); assert.equal(errors.length, 0); assert.ok(s.probe().report); s.stop();
});
test('access loss waits for mutation receipt; unmount flushes durable pending and retains memory', async () => {
  const h = harness(), held = deferred(), lost = []; h.props.onUnavailable = e => { lost.push(e); h.stop(); };
  h.render(); await h.settle(); h.probe().updateDraft(d => ({ ...d, base: 2, sentences: { 1: { text: 'keep me' } } })); await h.settle();
  const work = h.probe().run('held receipt', async () => { await held.promise; h.probe().updateDraft(d => ({ ...d, submitted: 3, operationId: 'a'.repeat(32) })); });
  h.deny(404); await h.probe().refreshChecks(2); await h.settle(); assert.equal(lost.length, 0); assert.equal(h.scope.signal.aborted, false);
  held.resolve(); await work; await h.settle(); assert.equal(lost.length, 1);
  const durable = h.load('src/lib/pendingEdits.ts').readPending('A'); assert.equal(durable.sentences[1].text, 'keep me'); assert.equal(durable.operationId, 'a'.repeat(32));
  assert.equal(h.probe().draftRef.current.operationId, 'a'.repeat(32)); assert.ok(h.taskCalls().every(c => (c.init.method ?? 'GET') === 'GET'));
  assert.equal(h.probe().drafts.get('A').operationId, 'a'.repeat(32));
});

test('parent refresh while editor write is held waits for exact decoded receipt, never aborts/retries', async () => {
  const h = harness('workspace'); h.render(); await h.settle(); await h.open('A');
  const child = flat(h.tree()).find(n => n.type === 'Workbench'), selection = h.probe().selectedRef.current, held = deferred();
  assert.equal(typeof child.props.onMutationStateChange, 'function');
  child.props.onMutationStateChange(true);
  h.handler(async (path, init) => init.method === 'POST' ? held.promise : response({ detail: { code: 'task_gone' } }, 410));
  const write = selection.request('/api/tasks/A/apply', { method: 'POST', body: '{}' }); await h.settle();
  h.probe().refreshSelected(); await h.settle(); assert.equal(h.probe().page, 'result');
  assert.equal(selection.scope.signal.aborted, false); assert.equal(h.taskCalls().find(c => c.init.method === 'POST').init.signal.aborted, false);
  const receipt = { task_id: 'A', revision: 3, operation_id: 'a'.repeat(32) }; held.resolve(response(receipt));
  assert.deepEqual(JSON.parse(JSON.stringify(await write)), receipt);
  child.props.onMutationStateChange(false); await h.settle(); assert.equal(h.probe().page, 'gone');
  assert.equal(h.probe().missingReason, 'expired'); assert.equal(h.taskCalls().filter(c => c.init.method === 'POST').length, 1); h.stop();
});
test('actual held old status error after B or new A cannot purge or overwrite current scope', async () => {
  for (const id of ['B', 'A']) {
    const h = harness('workspace'); h.render(); h.replay(); await h.settle(); await h.open('A');
    const held = deferred(); h.handler(() => held.promise); h.probe().refreshSelected(); await h.settle();
    h.handler(async path => response(status(path.split('/').at(-1)))); await h.open(id);
    h.probe().onChecksChange(gate()); await h.settle(); const count = h.calls.length;
    held.resolve(response({ detail: { code: 'task_gone' } }, 410)); await h.settle();
    assert.equal(h.probe().page, 'result'); assert.equal(h.probe().selectedRef.current.id, id); assert.equal(h.probe().selectedChecks.passed, true);
    assert.equal(h.calls.length, count); assert.equal(h.probe().browserRows.length, 2); h.stop();
  }
});
test('bare operation last-receipt access loss retains lastOperation and generic network never expires', async () => {
  const h = harness(), failures = []; h.props.onUnavailable = e => failures.push(e); h.render(); await h.settle();
  h.probe().updateDraft(d => ({ ...d, base: 2, lastOperation: { id: 'b'.repeat(32), revision: 2 } })); await h.settle();
  h.handler(async path => path.endsWith('/context') ? response(context()) : response({}, 404));
  await h.probe().reload(); await h.settle(); assert.equal(failures.length, 1); assert.equal(h.probe().draftRef.current.lastOperation.id, 'b'.repeat(32)); h.stop();
});
test('optional unavailable callback and changing host props do not add GETs; held checks notify original scope only', async () => {
  const h = harness(); h.render(); await h.settle(); const initial = h.calls.length, old = [], next = [];
  h.props.onUnavailable = e => old.push(e); h.render(); await h.settle(); assert.equal(h.calls.length, initial);
  const held = deferred(); h.handler(() => held.promise); const pending = h.probe().refreshChecks(2);
  h.props.onUnavailable = e => next.push(e); h.render(); held.resolve(response({}, 403)); await pending; await h.settle();
  assert.equal(old.length, 1); assert.equal(next.length, 0); assert.equal(h.calls.length, initial + 1);
  h.props.onUnavailable = undefined; h.render(); h.deny(404); await h.probe().reload(); await h.settle(); assert.ok(h.probe().error); h.stop();
});
test('parent network refresh retains current result and history, while aborted selected scope ignores held error', async () => {
  const h = harness('workspace'); h.render(); await h.settle(); await h.open('A'); const before = h.storage.get('golden-mic.history.v1');
  h.handler(async () => { throw Error('offline'); }); h.probe().refreshSelected(); await h.settle(); assert.equal(h.probe().page, 'result'); assert.ok(h.probe().task);
  const held = deferred(); h.handler(() => held.promise); h.probe().refreshSelected(); await h.settle(); h.probe().selectedRef.current.scope.abort();
  held.resolve(response({ detail: { code: 'task_gone' } }, 410)); await h.settle(); assert.equal(h.probe().page, 'result');
  assert.equal(h.storage.get('golden-mic.history.v1'), before); h.stop();
});