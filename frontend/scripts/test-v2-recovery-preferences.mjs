import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

// Compile production declarations and their real dependencies in memory.
// Only browser state/storage and HTTP responses are fixtures: no backend,
// provider, service, build, disk cache, or duplicated preference implementation.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = readFileSync(resolve(root, 'src/components/Workspace.tsx'), 'utf8');
const ast = ts.createSourceFile('Workspace.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
function declaration(name) {
  const found = [];
  function visit(node) {
    if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText(ast) === name) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.equal(found.length, 1, `production declaration ${name}`);
  return ((ts.isVariableDeclaration(found[0]) ? 'const ' : '')
    + printer.printNode(ts.EmitHint.Unspecified, found[0], ast)).replace(/^export /, '') + ';';
}
function compile(code) {
  return ts.transpileModule(code, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
    // Bind Vite's environment, not the API implementation, for the VM.
    transformers: { before: [context => tree => {
      function visit(node) {
        if (ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression) && node.name.text === 'env') {
          return ts.factory.createObjectLiteralExpression();
        }
        return ts.visitEachChild(node, visit, context);
      }
      return ts.visitNode(tree, visit);
    }] },
  }).outputText;
}
const declarations = ['isObject', 'validId', 'pathFor', 'invalidDraft', 'draftText', 'draftNumber',
  'readMediaMetadata', 'validateDraft', 'writeDraftCache', 'editFailedDraft'].map(declaration).join('\n');
const plain = value => JSON.parse(JSON.stringify(value));
const OLD_TOKEN = 'O'.repeat(43), NEW_TOKEN = 'N'.repeat(43);
const absent = Symbol('legacy absent');

function harness(preference = absent) {
  const storage = new Map(), requests = [], mounts = [], errors = [], receipts = [];
  let response;
  const globals = {
    exports: {},
    URL, Headers, Response, FormData, Blob, AbortController, DOMException, setTimeout, clearTimeout,
    location: { origin: 'http://recovery.test' },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    fetch: async (url, init) => {
      requests.push({ url, init });
      if (url === '/api/session') return Response.json({ access_mode: 'development', csrf_token: null, expires_at: null });
      assert.equal(url, '/api/tasks/failed-task/recover-draft');
      assert.equal(init.method, 'POST');
      assert.equal(init.headers.get('X-Task-Token'), OLD_TOKEN);
      assert.deepEqual(JSON.parse(init.body), { step: 2 });
      return Response.json(response);
    },
  };
  const modules = new Map();
  function load(path) {
    path = resolve(root, path);
    if (modules.has(path)) return modules.get(path);
    if (path.endsWith('.json')) return JSON.parse(readFileSync(path, 'utf8'));
    const module = { exports: {} };
    modules.set(path, module.exports);
    vm.runInNewContext(compile(readFileSync(path, 'utf8')), { ...globals, module, exports: module.exports,
      require(name) {
        assert.ok(name.startsWith('.'), `unexpected dependency ${name}`);
        const base = resolve(dirname(path), name);
        const dependency = [base, base + '.ts', base + '.json'].find(existsSync);
        assert.ok(dependency, `missing dependency ${name}`);
        return load(dependency);
      },
    }, { filename: path });
    return module.exports;
  }
  const modes = load('src/lib/productionModes.ts');
  const api = load('src/lib/appApi.ts');
  const persistence = load('src/lib/v2Persistence.ts');
  response = {
    task_id: 'new-draft', status: 'draft', accepted: false, access_token: NEW_TOKEN, step: 2,
    recovery: { source_task_id: 'failed-task', task_id: 'new-draft' }, created_at: '2026-09-29T00:00:00Z',
    script: 'Headline\nOriginal manuscript.', mode: 'mixed', preferences: modes.defaultModePreferences('mixed'),
    sentences: modes.parseModeScript('Headline\nOriginal manuscript.', 'mixed'), speakers: [],
    files: [{ file_id: 'up_new', up_id: 'up_new', name: 'source.mp4', bytes: 19, size: 19,
      lastModified: 1790640000000, is_image: false, sec: 3, note: 'original', in_sec: .25, out_sec: 2.75,
      width: 64, height: 64, status: 'ready', sha256: 'a'.repeat(64), chunksize: 8 * 1024 * 1024,
      token: NEW_TOKEN, access_token: NEW_TOKEN }],
    ...(preference === absent ? {} : { source_voice_preferred: preference }),
  };
  const scope = new AbortController(), ref = current => ({ current });
  const target = { id: 'failed-task', token: OLD_TOKEN, scope,
    request: api.createAppRequest({ taskId: 'failed-task', token: OLD_TOKEN, signal: scope.signal }) };
  const old = { script: 'existing draft' };
  Object.assign(globals, modes, load('src/lib/mediaInput.ts'), load('src/lib/uploadSessions.ts'), persistence, {
    MAX_SCRIPT_LENGTH: 100000, MAX_DRAFT_LENGTH: 512 * 1024,
    selectedRef: ref(target), actionLock: ref(false), intentRef: ref(null), pageRef: ref('processing'),
    task: { task_id: target.id, status: 'failed', lifecycle_v2: true }, generation: ref(1), alive: ref(true),
    draftRef: ref(old), draftDirty: ref(false), draftBlocked: ref(false), draftNeedsClear: false,
    draftSuppressed: ref(false), draftSaved: ref('old'), draftTimer: ref(null),
    hasDraftContent: draft => !!draft?.script, window: { confirm: () => true },
    mutate: async action => { globals.actionLock.current = true; try { await action(); } finally { globals.actionLock.current = false; } },
    retainWork: value => receipts.push(plain(value)),
    cancelReads() {}, unmountWizard() {}, select() {}, setTask() {}, setUpload() {}, setMissing() {}, setDialog() {},
    setDraftPending() {}, setDraftProblem() {}, setDraftNeedsClear() {}, setDraftSavedAt() {},
    mountWizard: value => mounts.push(plain(value)), showPage: value => { globals.pageRef.current = value; }, writeHash() {},
    setDraftStatus() {}, restoredDraftNote: () => '', setNotice() {}, setError: value => { if (value) errors.push(value); },
    errorText: api.errorText, refreshHistory() {},
  });
  vm.runInNewContext(compile(declarations + '\nthis.edit = editFailedDraft; this.validate = validateDraft;'), globals);
  return { globals, old, response, requests, mounts, errors, receipts, storage, persistence,
    edit: globals.edit, validate: globals.validate };
}

for (const value of [true, false]) test(`recovery preserves explicit ${value} through real API, validation, cache and reload`, async () => {
  const h = harness(value), before = plain(h.response);
  await h.edit(2);
  assert.deepEqual(h.errors, []);
  assert.equal(h.mounts.length, 1);
  const seed = h.mounts[0];
  assert.equal(seed.source_voice_preferred, value);
  assert.equal(Object.hasOwn(seed, 'source_voice_preferred'), true);
  assert.deepEqual(plain(h.globals.draftRef.current), seed);
  const stored = JSON.parse(h.storage.get(h.persistence.V2_KEY)).draft;
  assert.deepEqual(stored, seed);
  const reloaded = plain(h.validate(h.persistence.v2Persistence.readDraft().draft));
  assert.deepEqual(reloaded, seed);
  assert.deepEqual(reloaded.preferences, before.preferences);
  assert.equal(reloaded.files[0].note, 'original');
  assert.equal(reloaded.files[0].trim_start, .25);
  assert.equal(reloaded.files[0].trim_end, 2.75);
  assert.equal(reloaded.uploadBindings[0].server_file_id, 'up_new');
  assert.equal(reloaded.draftTaskToken, NEW_TOKEN);
  assert.equal(h.receipts.length, 1);
  assert.deepEqual(plain(h.response), before, 'recovery receipt was not mutated');
  assert.equal(h.requests.filter(item => item.init.method === 'POST').length, 1);
});

test('legacy absent stays absent across recovery/cache/reload so existing default semantics apply', async () => {
  const h = harness(); await h.edit(2);
  assert.deepEqual(h.errors, []);
  assert.equal(h.mounts.length, 1);
  for (const draft of [h.mounts[0], h.persistence.v2Persistence.readDraft().draft,
    h.validate(h.persistence.v2Persistence.readDraft().draft)]) {
    assert.equal(Object.hasOwn(draft, 'source_voice_preferred'), false);
  }
});

for (const value of [null, 0, 1, 'true', 'false', {}, []]) test(`invalid preference ${JSON.stringify(value)} is rejected before replacing the draft`, async () => {
  const h = harness(value); await h.edit(2);
  assert.equal(h.errors.length, 1);
  assert.equal(h.mounts.length, 0);
  assert.equal(h.storage.size, 0);
  assert.equal(h.globals.draftRef.current, h.old);
  assert.equal(h.requests.filter(item => item.init.method === 'POST').length, 1);
  assert.throws(() => h.validate({ script: 'Headline\nBody.', mode: 'mixed', step: 2, files: [], source_voice_preferred: value }));
});