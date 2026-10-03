import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import postcss from 'postcss';

// Actual production TS/TSX compiled in memory; synthetic HTTP/storage/hooks
// only. No hosts, builds, real network, disk writes, media or provider calls.
// Native DOM focus/layout is not claimed by these deterministic hook tests.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = p => readFileSync(resolve(root, p), 'utf8');
const plain = v => JSON.parse(JSON.stringify(v));
const ref = current => ({ current });
const block = () => { throw Error('Unapproved side effect'); };
const TOKEN = 'T'.repeat(43), NEW_TOKEN = 'N'.repeat(43);
const expiry = '2026-10-02T20:13:14.123456+00:00';
function compile(code) {
  return ts.transpileModule(code, { fileName: 'actual.tsx', reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    transformers: { before: [context => tree => {
      const visit = node => ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression) && node.name.text === 'env'
        ? ts.factory.createObjectLiteralExpression() : ts.visitEachChild(node, visit, context);
      return ts.visitNode(tree, visit);
    }] },
  }).outputText;
}
function loader(globals = {}) {
  const cache = new Map();
  function load(p) {
    p = resolve(root, p); if (cache.has(p)) return cache.get(p);
    if (p.endsWith('.json')) return JSON.parse(readFileSync(p, 'utf8'));
    const module = { exports: {} }; cache.set(p, module.exports);
    vm.runInNewContext(compile(readFileSync(p, 'utf8')), { module, exports: module.exports,
      URL, URLSearchParams, Headers, Response, FormData, Blob, AbortController, DOMException,
      setTimeout, clearTimeout, fetch: block, localStorage: { getItem: block, setItem: block },
      location: { origin: 'http://metadata.test' }, ...globals,
      require(name) {
        assert.ok(name.startsWith('.'), `unexpected dependency ${name}`);
        const full = resolve(dirname(p), name), target = [full, full + '.ts', full + '.json'].find(existsSync);
        assert.ok(target); return load(target);
      },
    }, { filename: p });
    return module.exports;
  }
  return load;
}
const workspace = read('src/components/Workspace.tsx');
const ast = ts.createSourceFile('Workspace.tsx', workspace, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
function decl(name, source = ast) {
  const found = [];
  function visit(node) {
    if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText(source) === name) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(source); assert.equal(found.length, 1, `unique declaration ${name}`);
  return ((ts.isVariableDeclaration(found[0]) ? 'const ' : '') + printer.printNode(ts.EmitHint.Unspecified, found[0], source)).replace(/^export /, '') + ';';
}
function evaluate(names, globals, exports = names) {
  const context = vm.createContext({ exports: {}, AbortController, clearTimeout, ...globals });
  vm.runInContext(compile(names.map(n => decl(n)).join('\n') + `\nthis.actual={${exports.join(',')}};`), context);
  return { context, ...context.actual };
}
const baseline = () => ({ task_id: 'source', title: 'Original', revision: 7, metadata_revision: 2 });
const status = (patch = {}) => ({ ...baseline(), status: 'done', mode: 'mixed', progress: 100, stages: [],
  version_count: 3, expires_at: expiry, ...patch });
function transport(handler) {
  const calls = [], data = new Map();
  const load = loader({ localStorage: { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, v) },
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (url === '/api/session') return Response.json({ access_mode: 'anonymous', csrf_token: 'C'.repeat(43), expires_at: '2099-01-01T00:00:00Z' });
      return handler(url, init);
    } });
  const api = load('src/lib/appApi.ts'), scope = new AbortController();
  return { api, load, calls, data, scope, request: api.createAppRequest({ taskId: 'source', token: TOKEN, signal: scope.signal }) };
}
const receipt = title => ({ ...baseline(), title, metadata_revision: 3, access_token: 'evil', expires_at: 'fake', unknown: true });

test('PATCH is exact CAS + title, authenticated, same-origin, one write; receipt drops unknown fields', async () => {
  const h = transport((url, init) => {
    assert.equal(url, '/api/tasks/source/metadata'); assert.equal(init.method, 'PATCH');
    assert.equal(init.headers.get('X-Task-Token'), TOKEN); assert.equal(init.headers.get('X-CSRF-Token'), 'C'.repeat(43));
    assert.equal(init.credentials, 'include'); assert.equal(init.redirect, 'error'); assert.equal(init.cache, 'no-store');
    assert.deepEqual(JSON.parse(init.body), { expected_revision: 7, expected_metadata_revision: 2, title: '  New😀  ' });
    return Response.json(receipt('  New😀  '));
  });
  assert.deepEqual(plain(await h.api.renameTaskMetadata(h.request, baseline(), '  New😀  ')), { ...baseline(), title: '  New😀  ', metadata_revision: 3 });
  assert.equal(h.calls.length, 2);
});
for (const title of ['', ' ', '\nBad', '\u007f', '\u0085', '\u2028', '\u2029', '\ud800', 'x'.repeat(41)]) {
  test(`reject invalid title ${JSON.stringify(title)} before auth/write`, async () => {
    const h = transport(block); await assert.rejects(h.api.renameTaskMetadata(h.request, baseline(), title)); assert.equal(h.calls.length, 0);
  });
}
test('40 Unicode codepoints including surrogate pairs and exact surrounding spaces accepted', () => {
  const { api } = transport(block); assert.equal(api.validMetadataTitle('😀'.repeat(40)), true);
  assert.equal(api.validMetadataTitle('😀'.repeat(41)), false); assert.equal(api.validMetadataTitle(' A '), true);
});
for (const patch of [{ revision: '7' }, { revision: true }, { metadata_revision: -1 }, { metadata_revision: 1.5 }, { metadata_revision: null }]) {
  test(`strict receipt counters ${JSON.stringify(patch)}`, () => assert.throws(() => transport(block).api.parseTaskMetadata({ ...baseline(), ...patch })));
}
for (const patch of [{ task_id: 'other' }, { title: 'other' }, { revision: 8 }, { metadata_revision: 2 }]) {
  test(`mismatched receipt is unknown, never retried ${JSON.stringify(patch)}`, async () => {
    const h = transport(() => Response.json({ ...receipt('New'), ...patch }));
    await assert.rejects(h.api.renameTaskMetadata(h.request, baseline(), 'New'));
    assert.equal(h.calls.filter(c => c.init.method === 'PATCH').length, 1);
  });
}
for (const [http, code, expected] of [[410, 'task_gone', 'expired'], [410, undefined, 'inaccessible'], [404, 'task_gone', 'inaccessible'], [401, undefined, 'inaccessible'], [403, undefined, 'inaccessible'], [409, 'stale_metadata', 'network']]) {
  test(`HTTP ${http}/${code} has exact gone classification and no automatic retry`, async () => {
    const h = transport(() => Response.json({ detail: { code } }, { status: http }));
    let failure; try { await h.api.renameTaskMetadata(h.request, baseline(), 'New'); } catch (e) { failure = e; }
    assert.ok(failure); assert.equal(failure.code, code); assert.equal(h.api.goneReason(failure), expected);
    assert.equal(h.calls.filter(c => c.init.method === 'PATCH').length, 1);
  });
}
test('cancelled request cannot bootstrap or mutate', async () => {
  const h = transport(block); h.scope.abort(); await assert.rejects(h.api.renameTaskMetadata(h.request, baseline(), 'New')); assert.equal(h.calls.length, 0);
});
test('history roundtrip retains capability, metadata CAS, real count and exact ISO, never extends TTL', () => {
  const h = transport(block), item = { taskId: 'source', accessToken: TOKEN, title: 'Original', createdAt: '2026-09-29T00:00:00Z',
    status: 'done', revision: 7, metadata_revision: 2, version_count: 3, expires_at: expiry, mode: 'mixed' };
  h.api.rememberWork(item); h.api.rememberWork({ ...h.api.readHistory()[0], title: 'New', metadata_revision: 3 });
  assert.deepEqual(plain(h.api.readHistory()[0]), { ...item, title: 'New', metadata_revision: 3 });
});
test('sparse owner tombstones do not invent created_at, mode, revision, count or expiry', async () => {
  const h = transport(() => Response.json({ tasks: [{ id: 'gone', task_id: 'gone', status: 'gone', title: '未命名视频' }], total: 1 }));
  assert.deepEqual(plain(await h.api.getLocalHistory()), [{ taskId: 'gone', status: 'gone', title: '未命名视频' }]);
});
test('legacy absence and explicit null expiry remain distinct without fabricated count', () => {
  const { api } = transport(block);
  const legacy = api.parseTask({ task_id: 'legacy', status: 'done', revision: 42, progress: 100, stages: [] });
  assert.equal(Object.hasOwn(legacy, 'version_count'), false); assert.equal(Object.hasOwn(legacy, 'expires_at'), false);
  assert.equal(api.parseTask(status({ expires_at: null })).expires_at, null);
});
for (const patch of [{ version_count: true }, { version_count: -1 }, { metadata_revision: '0' }, { expires_at: '2026-10-02' }, { expires_at: 123 }]) {
  test(`reject malformed optional status metadata ${JSON.stringify(patch)}`, () => assert.throws(() => transport(block).api.parseTask(status(patch))));
}
test('sidebar counts only authoritative published entries, otherwise labels revision or missing', () => {
  const { versionLabel } = evaluate(['versionLabel'], {});
  assert.equal(versionLabel({ revision: 19, version_count: 3 }), '改了 2 次');
  assert.equal(versionLabel({ revision: 19, version_count: 1 }), '改了 0 次');
  assert.equal(versionLabel({ revision: 19, version_count: 0 }), '版本 19');
  assert.equal(versionLabel({ revision: 19 }), '版本 19'); assert.equal(versionLabel({}), '版本未提供');
});

function renameHarness(handler) {
  const h = transport(handler), published = [], notices = [], errors = [], cache = [];
  const target = { id: 'source', token: TOKEN, title: 'Original', scope: h.scope, request: h.request };
  const cached = { taskId: 'source', accessToken: TOKEN, title: 'Original', status: 'done', revision: 7,
    metadata_revision: 2, version_count: 3, expires_at: expiry, createdAt: '2026-09-29T00:00:00Z' };
  h.api.rememberWork(cached);
  let spec;
  const globals = { ...h.api, selectedRef: ref(target), actionLock: ref(false), intentRef: ref(null), pageRef: ref('result'),
    task: status(), alive: ref(true), generation: ref(1), selectedReadController: ref(null),
    setRename: s => { spec = s; }, setTask: v => published.push(plain(v)), setNotice: v => notices.push(v),
    observeTask(next) { const item = { ...h.api.readHistory()[0], title: next.title, metadata_revision: next.metadata_revision };
      h.api.rememberWork(item); cache.push(item); },
    setError: v => errors.push(v), refreshSelected: () => errors.push('read-requested'),
    mutate: async fn => { globals.actionLock.current = true; try { return await fn(); } finally { globals.actionLock.current = false; } },
  };
  const actual = evaluate(['pathFor', 'openRename'], globals); actual.openRename();
  return { ...h, globals, published, notices, errors, cache, actual, spec: () => spec, target };
}
test('rename ACK publishes exact server title, retains capability and TTL, no GET/write replay', async () => {
  const h = renameHarness(() => Response.json(receipt('Renamed'))); await h.spec().save(baseline(), 'Renamed');
  assert.equal(h.published.length, 1); assert.equal(h.published[0].title, 'Renamed'); assert.equal(h.target.title, 'Renamed');
  assert.equal(h.published[0].expires_at, expiry); assert.equal(h.published[0].version_count, 3);
  assert.equal(h.api.readHistory()[0].accessToken, TOKEN); assert.equal(h.api.readHistory()[0].expires_at, expiry);
  assert.equal(h.calls.filter(c => c.url === '/api/tasks/source').length, 0);
});
for (const reason of ['lost', 'invalid', 'CAS']) test(`rename ${reason} receipt reconciles with GET only and never pretends requested title saved`, async () => {
  const h = renameHarness((url, init) => {
    if (init.method === 'PATCH') {
      if (reason === 'lost') throw Error('lost');
      return reason === 'CAS' ? Response.json({ detail: { code: 'stale_metadata' } }, { status: 409 }) : Response.json({ task_id: 'source' });
    }
    assert.equal(url, '/api/tasks/source'); assert.equal(init.headers.get('X-Task-Token'), TOKEN);
    return Response.json(status({ title: 'Server wins', metadata_revision: 8 }));
  });
  await assert.rejects(h.spec().save(baseline(), 'Requested'));
  assert.equal(h.published.length, 1); assert.equal(h.published[0].title, 'Server wins'); assert.equal(h.notices.length, 0);
  assert.equal(h.calls.filter(c => c.init.method === 'PATCH').length, 1); assert.equal(h.calls.filter(c => c.url === '/api/tasks/source').length, 1);
  assert.equal(h.api.readHistory()[0].accessToken, TOKEN);
});
test('failed reconciliation retains original title/capability with no success notice', async () => {
  const h = renameHarness(() => { throw Error('offline'); }); await assert.rejects(h.spec().save(baseline(), 'Requested'));
  assert.equal(h.published.length, 0); assert.equal(h.notices.length, 0); assert.equal(h.api.readHistory()[0].title, 'Original');
});
test('held rename then stale navigation cannot publish, select or overwrite cache', async () => {
  let release; const h = renameHarness(() => new Promise(resolve => { release = resolve; }));
  const work = h.spec().save(baseline(), 'New'); for (let i = 0; i < 30 && !release; i++) await Promise.resolve();
  assert.ok(release); h.globals.generation.current++; h.globals.pageRef.current = 'create';
  release(Response.json(receipt('New'))); await work;
  assert.equal(h.published.length, 0); assert.equal(h.api.readHistory()[0].title, 'Original'); assert.equal(h.notices.length, 0);
});
test('held PATCH blocks a second rename invocation', async () => {
  let release; const h = renameHarness(() => new Promise(resolve => { release = resolve; }));
  const first = h.spec().save(baseline(), 'New'); await assert.rejects(h.spec().save(baseline(), 'Again'));
  for (let i = 0; i < 30 && !release; i++) await Promise.resolve(); release(Response.json(receipt('New'))); await first;
  assert.equal(h.calls.filter(c => c.init.method === 'PATCH').length, 1);
});

const jsx = (type, props) => ({ type, props: props ?? {} });
const flat = tree => tree == null || typeof tree === 'boolean' ? [] : Array.isArray(tree) ? tree.flatMap(flat)
  : typeof tree !== 'object' ? [tree] : [tree, ...flat(tree.props?.children)];
const text = tree => flat(tree).filter(v => typeof v === 'string' || typeof v === 'number').join('');
function component(name, props) {
  let cursor = 0; const slots = [], cleanups = [], effects = []; let tree;
  const globals = { ...transport(block).api, require: () => ({ jsx, jsxs: jsx }),
    useState(value) { const i = cursor++; if (!(i in slots)) slots[i] = typeof value === 'function' ? value() : value;
      return [slots[i], v => { slots[i] = typeof v === 'function' ? v(slots[i]) : v; }]; },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; }, useId: () => 'test-dialog',
    useEffect(fn) { const i = cursor++; if (!(i in slots)) { slots[i] = true; effects.push(fn); } },
    HTMLElement: class {}, document: { activeElement: null }, MODE_LABELS: { voiceover: 'AI配音', mixed: '配音+原声', original: '只用原声' } };
  const actual = evaluate([name], globals)[name];
  function render() { cursor = 0; tree = actual(props); effects.splice(0).forEach(fn => cleanups.push(fn())); return tree; }
  render(); return { render, tree: () => tree, nodes: () => flat(tree), stop: () => cleanups.forEach(fn => fn?.()) };
}
test('native rename dialog has linked label/copy; cancel sends zero writes', () => {
  let closes = 0; const h = component('RenameDialog', { spec: { baseline: baseline(), save: block, read: block }, onClose: () => closes++ });
  assert.equal(h.tree().type, 'dialog'); assert.equal(h.tree().props['aria-labelledby'], 'test-dialog-title');
  assert.ok(text(h.tree()).includes('不改变成片中已烧录')); assert.equal(h.nodes().find(n => n.type === 'label').props.htmlFor, 'test-dialog-name');
  h.tree().props.onCancel({ preventDefault() {} }); assert.equal(closes, 1); h.stop();
});
test('dialog holds double submit/Escape, unknown locks writes until explicit GET then rerename uses new CAS', async () => {
  let release, closes = 0, reads = 0; const saves = [];
  const h = component('RenameDialog', { spec: { baseline: baseline(),
    save: async (base, title) => { saves.push({ base: plain(base), title }); if (saves.length === 1) await new Promise((_, reject) => { release = reject; }); },
    read: async () => { reads++; return { ...baseline(), title: 'Other', metadata_revision: 9 }; } }, onClose: () => closes++ });
  const input = () => h.nodes().find(n => n.type === 'input'); const form = () => h.nodes().find(n => n.type === 'form');
  const submit = () => form().props.onSubmit({ preventDefault() {} });
  input().props.onChange({ target: { value: 'New' } }); h.render(); submit(); submit(); h.tree().props.onCancel({ preventDefault() {} });
  assert.equal(saves.length, 1); assert.equal(closes, 0); release(Error('unknown'));
  for (let i = 0; i < 8; i++) await Promise.resolve(); h.render(); submit(); assert.equal(saves.length, 1);
  h.nodes().find(n => n.type === 'button' && text(n) === '只读核对名称').props.onClick();
  for (let i = 0; i < 8; i++) await Promise.resolve(); h.render(); assert.equal(reads, 1);
  input().props.onChange({ target: { value: 'Again' } }); h.render(); submit();
  for (let i = 0; i < 8; i++) await Promise.resolve();
  assert.equal(saves.length, 2); assert.equal(saves[1].base.metadata_revision, 9); assert.equal(closes, 1); h.stop();
});
test('duplicate choice is native accessible fieldset/radios with keep default and explicit A/B/C', () => {
  const choices = [], h = component('DuplicateDialog', { onChoose: v => choices.push(v), onClose() {} });
  assert.equal(h.tree().type, 'dialog'); assert.equal(h.nodes().filter(n => n.type === 'input').length, 4);
  assert.ok(text(h.tree()).includes('保持当前方式')); assert.ok(text(h.tree()).includes('改为 A'));
  h.nodes().find(n => n.type === 'button' && text(n) === '继续').props.onClick(); assert.equal(choices[0], undefined);
  h.nodes().filter(n => n.type === 'input')[3].props.onChange(); h.render();
  h.nodes().find(n => n.type === 'button' && text(n) === '继续').props.onClick(); assert.equal(choices[1], 'original'); h.stop();
});

function recoveryHarness(mode = 'mixed') {
  const mounts = [], receipts = [], errors = []; let release;
  const h = transport((url, init) => {
    assert.equal(url, '/api/tasks/source/recover-draft'); assert.equal(init.method, 'POST');
    assert.equal(init.headers.get('X-Task-Token'), TOKEN); assert.deepEqual(JSON.parse(init.body), { step: 1, mode });
    return new Promise(resolve => { release = resolve; });
  });
  const modes = h.load('src/lib/productionModes.ts');
  const response = { task_id: 'cloned', access_token: NEW_TOKEN, status: 'draft', accepted: false, step: 1,
    recovery: { source_task_id: 'source', task_id: 'cloned' }, mode, script: 'Headline\nBody.',
    preferences: modes.defaultModePreferences(mode), sentences: modes.parseModeScript('Headline\nBody.', mode), speakers: [], source_voice_preferred: false,
    created_at: '2026-09-29T00:00:00Z', files: [{ file_id: 'up_clone', up_id: 'up_clone', status: 'ready', token: NEW_TOKEN, access_token: NEW_TOKEN,
      name: 'source.mp4', bytes: 19, size: 19, lastModified: 1, is_image: false, sec: 3, note: '', in_sec: 0, out_sec: 3,
      width: 64, height: 64, sha256: 'a'.repeat(64), chunksize: 8 * 1024 * 1024 }] };
  const target = { id: 'source', token: TOKEN, scope: h.scope, request: h.request };
  const globals = { ...modes, ...h.load('src/lib/mediaInput.ts'), ...h.load('src/lib/uploadSessions.ts'), ...h.api,
    ...h.load('src/lib/v2Persistence.ts'), MAX_SCRIPT_LENGTH: 100000, MAX_DRAFT_LENGTH: 512 * 1024,
    selectedRef: ref(target), actionLock: ref(false), intentRef: ref(null), pageRef: ref('result'), task: status(), generation: ref(1), alive: ref(true),
    draftRef: ref(null), draftDirty: ref(false), draftBlocked: ref(false), draftNeedsClear: false, draftSuppressed: ref(false), draftSaved: ref(''), draftTimer: ref(null),
    hasDraftContent: () => false, window: { confirm: () => true },
    mutate: async fn => { globals.actionLock.current = true; try { await fn(); } finally { globals.actionLock.current = false; } },
    retainWork: v => receipts.push(plain(v)), mountWizard: v => mounts.push(plain(v)),
    cancelReads() {}, unmountWizard() {}, select() {}, setTask() {}, setUpload() {}, setMissing() {}, setDialog() {}, setError: e => { if (e) errors.push(e); },
    setDraftPending() {}, setDraftProblem() {}, setDraftNeedsClear() {}, setDraftSavedAt() {}, showPage() {}, writeHash() {},
    setDraftStatus() {}, restoredDraftNote: () => '', setNotice() {}, refreshHistory() {},
  };
  const actual = evaluate(['isObject', 'validId', 'pathFor', 'invalidDraft', 'draftText', 'draftNumber', 'readMediaMetadata', 'validateDraft', 'writeDraftCache', 'editFailedDraft'], globals);
  return { ...h, globals, response, mounts, receipts, errors, edit: actual.editFailedDraft, release: () => release(Response.json(response)) };
}
for (const mode of ['voiceover', 'mixed', 'original']) test(`completed source clones ${mode} via one recovery POST, step1 strict validation and cache`, async () => {
  const h = recoveryHarness(mode), work = h.edit(1, mode);
  for (let i = 0; i < 40; i++) await Promise.resolve(); h.release(); await work;
  assert.deepEqual(h.errors, []); assert.equal(h.mounts.length, 1); assert.equal(h.mounts[0].step, 1); assert.equal(h.mounts[0].mode, mode);
  assert.equal(h.mounts[0].draftTaskToken, NEW_TOKEN); assert.equal(h.mounts[0].source_voice_preferred, false);
  assert.equal(h.receipts.length, 1); assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 1);
  assert.ok(!h.calls.some(c => /start|align|pretranscribe/.test(c.url)));
});
test('held recovery retains trusted new capability but cannot replace draft after navigation', async () => {
  const h = recoveryHarness(), work = h.edit(1, 'mixed'); for (let i = 0; i < 40; i++) await Promise.resolve();
  h.globals.generation.current++; h.globals.pageRef.current = 'create'; h.release(); await work;
  assert.equal(h.mounts.length, 0); assert.equal(h.receipts.length, 1); assert.equal(h.receipts[0].accessToken, NEW_TOKEN); assert.equal(h.data.size, 0);
});
test('mode mismatched recovery response never replaces draft or imports credentials', async () => {
  const h = recoveryHarness(), work = h.edit(1, 'mixed'); for (let i = 0; i < 40; i++) await Promise.resolve();
  h.response.mode = 'original'; h.release(); await work; assert.equal(h.mounts.length, 0); assert.equal(h.receipts.length, 0); assert.equal(h.errors.length, 1);
});
test('held recovery blocks repeated click, keeping exactly one mutation', async () => {
  const h = recoveryHarness(), work = h.edit(1, 'mixed'); await h.edit(1, 'mixed');
  for (let i = 0; i < 40; i++) await Promise.resolve(); h.release(); await work;
  assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 1); assert.equal(h.mounts.length, 1);
});
test('keep-current copy still uses exact duplicate endpoint/CAS and durable receipt', async () => {
  const h = transport((url, init) => init.method === 'POST'
    ? Response.json({ task_id: 'copy', access_token: NEW_TOKEN, revision: 7, status: 'done' }) : Response.json(status()));
  let dialog; const retained = [], opens = [];
  const target = { id: 'source', title: 'Original', token: TOKEN, scope: h.scope, request: h.request };
  const globals = { ...h.api, selectedRef: ref(target), actionLock: ref(false), generation: ref(1), alive: ref(true), intentRef: ref(null), pageRef: ref('result'),
    works: [], setDialog: value => { dialog = value; }, mutate: async fn => fn(),
    rememberReceipt: (r, title) => { const item = { taskId: r.task_id, accessToken: r.access_token, title }; retained.push(item); return item; },
    refreshHistory() {}, beginOpen: (...args) => opens.push(args), setNotice() {} };
  evaluate(['pathFor', 'duplicateCurrent'], globals).duplicateCurrent(); assert.ok(dialog); assert.equal(h.calls.length, 0);
  await dialog.action(); const writes = h.calls.filter(c => c.init.method === 'POST');
  assert.equal(writes.length, 1); assert.equal(writes[0].url, '/api/tasks/source/duplicate');
  assert.deepEqual(JSON.parse(writes[0].init.body), { expected_revision: 7 });
  assert.equal(retained[0].accessToken, NEW_TOKEN); assert.equal(opens.length, 1);
});
test('keep-current held status read cannot POST after stale navigation', async () => {
  let release, dialog; const h = transport(() => new Promise(resolve => { release = resolve; }));
  const target = { id: 'source', title: 'Original', scope: h.scope, request: h.request };
  const globals = { ...h.api, selectedRef: ref(target), actionLock: ref(false), generation: ref(1), alive: ref(true), intentRef: ref(null), pageRef: ref('result'),
    works: [], setDialog: v => { dialog = v; }, mutate: async fn => fn(), rememberReceipt: block, refreshHistory: block, beginOpen: block, setNotice: block };
  evaluate(['pathFor', 'duplicateCurrent'], globals).duplicateCurrent(); const pending = dialog.action();
  for (let i = 0; i < 30 && !release; i++) await Promise.resolve(); globals.generation.current++;
  release(Response.json(status())); await pending; assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 0);
});
test('reconcile then deliberate rerename retains latest actual version count and exact expiry', async () => {
  const latestExpiry = '2026-10-01T09:00:00.000001+00:00'; let writes = 0;
  const h = renameHarness((url, init) => {
    if (init.method !== 'PATCH') return Response.json(status({ revision: 9, metadata_revision: 10, version_count: 4, expires_at: latestExpiry }));
    if (++writes === 1) throw Error('lost');
    assert.deepEqual(JSON.parse(init.body), { expected_revision: 9, expected_metadata_revision: 10, title: 'Again' });
    return Response.json({ task_id: 'source', revision: 9, metadata_revision: 11, title: 'Again' });
  });
  await assert.rejects(h.spec().save(baseline(), 'First'));
  const reconciled = await h.spec().read(); await h.spec().save(reconciled, 'Again');
  assert.equal(h.published.at(-1).version_count, 4); assert.equal(h.published.at(-1).expires_at, latestExpiry);
});
test('own-voice notice uses owner CURRENT context preference and actual AI narration, never historical/sample/recorded rows', () => {
  const source = ts.createSourceFile('Result.tsx', read('src/components/ResultWorkbench.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const code = compile(decl('ownVoiceNotice', source) + '\nthis.result=ownVoiceNotice;');
  const base = { owner: true, historical: false, context: { preferences: { voice: 'mine' } }, mode: 'mixed', rows: [{ sentence_id: 1, audio_kind: 'tts' }], currentTimings: new Map(), isQuoteRow: r => r.kind === 'quote' };
  function check(patch) { const g = { ...base, ...patch }; vm.runInNewContext(code, g); return g.result; }
  assert.equal(check({}), true);
  for (const patch of [{ owner: false }, { historical: true }, { context: null }, { context: { preferences: { voice: 'ai' } } },
    { mode: 'original' }, { rows: [] }, { rows: [{ kind: 'quote', audio_kind: 'tts' }] },
    { currentTimings: new Map([[1, { audio_source: 'recording' }]]) }]) assert.ok(!check(patch));
});
test('owned CSS parses and native dialogs use shrinkable input/fieldset, not overflow masking', () => {
  for (const file of ['src/styles.css', 'src/components/ResultWorkbench.css']) assert.doesNotThrow(() => postcss.parse(read(file)));
  assert.match(read('src/styles.css'), /\.shell-rename input\{[^}]*min-width:0/);
  assert.match(read('src/styles.css'), /\.shell-duplicate-choices\{[^}]*min-inline-size:0/);
});