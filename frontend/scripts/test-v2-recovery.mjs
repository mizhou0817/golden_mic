import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

// In-memory execution of the actual Workspace handler, validator and upload
// parser. No DOM/browser claim, real storage, network, build or provider access.
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
  visit(ast); assert.equal(found.length, 1, name);
  return ((ts.isVariableDeclaration(found[0]) ? 'const ' : '') + printer.printNode(ts.EmitHint.Unspecified, found[0], ast)).replace(/^export /, '') + ';';
}
const compile = code => ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
const forbidden = () => { throw Error('Forbidden network or storage'); };
const modules = new Map();
function load(path) {
  path = resolve(root, path);
  if (modules.has(path)) return modules.get(path);
  if (path.endsWith('.json')) return JSON.parse(readFileSync(path, 'utf8'));
  const module = { exports: {} };
  vm.runInNewContext(compile(readFileSync(path, 'utf8').replaceAll('import.meta.env', '({})')), { module, exports: module.exports,
    Error, URL, URLSearchParams, Headers, AbortController, DOMException, fetch: forbidden, localStorage: { getItem: forbidden, setItem: forbidden },
    require(name) {
      assert.ok(name.startsWith('.'), name);
      const base = resolve(dirname(path), name);
      return load([base, base + '.ts', base + '.json'].find(existsSync));
    } });
  modules.set(path, module.exports); return module.exports;
}
const modes = load('src/lib/productionModes.ts');
const uploads = load('src/lib/uploadSessions.ts');
const media = load('src/lib/mediaInput.ts');
const { errorText, AppApiError } = load('src/lib/appApi.ts');
const plain = value => JSON.parse(JSON.stringify(value));
const declarations = ['isObject', 'validId', 'pathFor', 'invalidDraft', 'draftText', 'draftNumber', 'readMediaMetadata', 'validateDraft', 'mutate', 'editFailedDraft'].map(declaration).join('\n');
function receipt() {
  return { task_id: 'new-draft', status: 'draft', accepted: false, access_token: 'N'.repeat(43), step: 2,
    recovery: { source_task_id: 'failed-task', task_id: 'new-draft' }, created_at: '2026-09-29T00:00:00Z',
    script: 'Headline\nOriginal manuscript.', mode: 'mixed', preferences: modes.defaultModePreferences('mixed'),
    sentences: modes.parseModeScript('Headline\nOriginal manuscript.', 'mixed'), speakers: [],
    files: [{ file_id: 'up_new', up_id: 'up_new', name: 'source.mp4', bytes: 19, size: 19,
      lastModified: 1790640000000, is_image: false, sec: 3, note: 'original', in_sec: .25, out_sec: 2.75,
      width: 64, height: 64, fps: 25, status: 'ready', sha256: 'a'.repeat(64), chunksize: 8 * 1024 * 1024,
      token: 'N'.repeat(43), access_token: 'N'.repeat(43) }] };
}
function harness({ response = receipt(), confirm = true, failWrite = false, token = 'O'.repeat(43), held = false } = {}) {
  const calls = [], receipts = [], writes = [], errors = [], mounts = [], old = { script: 'existing draft' };
  let release;
  const scope = new AbortController();
  const target = { id: 'failed-task', token, scope, request: async (url, init) => {
    calls.push({ url, init, token });
    if (held) await new Promise(resolve => { release = resolve; });
    if (response instanceof Error) throw response;
    return response;
  } };
  const ref = current => ({ current });
  const globals = { ...modes, ...uploads, ...media, exports: {},
    MAX_SCRIPT_LENGTH: 100000, MAX_DRAFT_LENGTH: 512 * 1024, console, Error,
    selectedRef: ref(target), actionLock: ref(false), intentRef: ref(null), pageRef: ref('processing'),
    task: { task_id: target.id, status: 'failed', lifecycle_v2: true }, generation: ref(1), alive: ref(true),
    draftRef: ref(old), draftDirty: ref(false), draftBlocked: ref(false), draftNeedsClear: false,
    draftSuppressed: ref(false), draftSaved: ref('old'), draftTimer: ref(null),
    hasDraftContent: draft => !!draft?.script, window: { confirm: () => { calls.push('confirm'); return confirm; } },
    setBusy() {}, submissionRecovery: { read: forbidden },
    retainWork: value => receipts.push(plain(value)), writeDraftCache: (value, time) => {
      if (failWrite) throw Error('storage blocked'); writes.push({ value: plain(value), time });
    }, cancelReads() {}, unmountWizard() {}, select() {}, setTask() {}, setUpload() {}, setMissing() {}, setDialog() {},
    setDraftPending() {}, setDraftProblem() {}, setDraftNeedsClear() {}, setDraftSavedAt() {}, clearTimeout() {},
    mountWizard: value => mounts.push(plain(value)), showPage: value => { globals.pageRef.current = value; }, writeHash() {},
    setDraftStatus() {}, restoredDraftNote: () => '', setNotice() {}, setError: value => errors.push(value),
    errorText, refreshHistory: () => calls.push('history-read'),
  };
  vm.runInNewContext(compile(declarations + '\nthis.edit = editFailedDraft; this.validate = validateDraft;'), globals);
  return { ...globals, globals, calls, receipts, writes, errors, mounts, old, edit: globals.edit,
    release: () => release() };
}
test('one POST with original capability, independent persisted draft, actual-size fileIdentity, no browser File', async () => {
  const h = harness(); await h.edit(2);
  assert.equal(h.calls[0], 'confirm');
  const posts = h.calls.filter(x => x.init);
  assert.equal(posts.length, 1); assert.equal(posts[0].url, '/api/tasks/failed-task/recover-draft');
  assert.deepEqual(JSON.parse(posts[0].init.body), { step: 2 }); assert.equal(posts[0].token, 'O'.repeat(43));
  const draft = h.mounts[0]; assert.ok(draft, h.errors.join('\n'));
  assert.equal(h.writes.length, 1); assert.deepEqual(h.writes[0].value, draft);
  assert.equal(h.receipts[0].accessToken, 'N'.repeat(43)); assert.equal(h.receipts[0].status, 'draft');
  assert.equal(draft.files[0].id, JSON.stringify(['source.mp4', 19, 1790640000000]));
  assert.equal(draft.files[0].size, 19); assert.equal(draft.files[0].file, undefined);
  assert.equal(draft.legacyRestore, undefined); assert.equal(draft.uploadBindings[0].server_file_id, 'up_new');
  assert.equal(draft.uploadTokens.up_new, draft.draftTaskToken);
  assert.deepEqual(draft.preferences, plain(modes.defaultModePreferences('mixed')));
  assert.deepEqual(plain(h.validate(draft)), draft);
});
const consented = (patch = {}, overrides = {}) => {
  const response = { ...receipt(), step: 1, shot_reuse_accepted: true, ...patch }; const h = harness({ response, ...overrides });
  h.globals.task.errorKind = 'shortage'; h.globals.task.mode = 'voiceover'; return h;
};
test('recovery can carry an explicit, confirmed consent to repeated footage into the new draft', async () => {
  const h = consented(); await h.edit(1, undefined, { allowShotReuse: true });
  assert.equal(h.calls.filter(x => x === 'confirm').length, 2, 'asks about repeated footage, then about replacing the draft');
  const posts = h.calls.filter(x => x.init); assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].init.body), { step: 1, allow_shot_reuse: true });
  assert.equal(h.mounts.length, 1); assert.deepEqual(h.errors.filter(Boolean), []);
});
test('an ordinary recovery never carries consent', async () => {
  const h = consented({ shot_reuse_accepted: false }); await h.edit(1);
  assert.deepEqual(JSON.parse(h.calls.find(x => x.init).init.body), { step: 1 });
});
test('repeated-footage recovery is refused locally unless it is a shortage in a mode that has b-roll', async () => {
  for (const [errorKind, mode] of [['transient', 'voiceover'], ['qc', 'mixed'], ['shortage', 'original'], [undefined, 'voiceover']]) {
    const h = consented(); h.globals.task.errorKind = errorKind; h.globals.task.mode = mode;
    await h.edit(1, undefined, { allowShotReuse: true });
    assert.equal(h.calls.length, 0, `${errorKind}/${mode}`); assert.equal(h.mounts.length, 0);
  }
});
test('declining the repeated-footage question sends nothing and keeps the draft', async () => {
  const h = consented({}, { confirm: false }); await h.edit(1, undefined, { allowShotReuse: true });
  assert.deepEqual(h.calls, ['confirm']); assert.equal(h.draftRef.current, h.old); assert.equal(h.mounts.length, 0);
});
test('a recovery receipt that does not confirm the consent is rejected, nothing is mounted', async () => {
  const h = consented({ shot_reuse_accepted: undefined }); await h.edit(1, undefined, { allowShotReuse: true });
  assert.equal(h.calls.filter(x => x.init).length, 1); assert.equal(h.mounts.length, 0); assert.equal(h.writes.length, 0);
  assert.match(h.errors.at(-1), /恢复回执无效/);
});
test('replacement declined means no POST or draft write', async () => {
  const h = harness({ confirm: false }); await h.edit(2);
  assert.deepEqual(h.calls, ['confirm']); assert.equal(h.writes.length, 0); assert.equal(h.draftRef.current, h.old);
});
test('missing original capability does not exploit localhost or local recovery', async () => {
  const h = harness({ token: undefined }); h.selectedRef.current.token = undefined; await h.edit(2);
  assert.equal(h.calls.length, 0); assert.equal(h.draftRef.current, h.old); assert.equal(h.mounts.length, 0);
  assert.equal(h.receipts.length, 0); assert.equal(h.writes.length, 0); assert.match(h.errors.at(-1), /缺少原任务的访问凭据/);
});
test('script recovery preserves requested step and independent task binding', async () => {
  const response = receipt(); response.step = 1; const h = harness({ response }); await h.edit(1);
  assert.equal(h.calls.filter(x => x.init).length, 1);
  assert.deepEqual(JSON.parse(h.calls.find(x => x.init).init.body), { step: 1 });
  assert.equal(h.mounts.length, 1); assert.equal(h.mounts[0].step, 1);
  assert.equal(h.mounts[0].draftTaskId, 'new-draft'); assert.equal(h.mounts[0].draftTaskToken, 'N'.repeat(43));
  assert.deepEqual(h.writes[0].value, h.mounts[0]); assert.equal(h.globals.pageRef.current, 'create');
});
for (const status of [401, 403, 404, 410]) test(`recovery auth ${status}: no tokenless fallback, upload, start, or mutation retry`, async () => {
  const h = harness({ response: new AppApiError(`denied-${status}`, status) }); await h.edit(2);
  const requests = h.calls.filter(x => x.init);
  assert.equal(requests.length, 1); assert.equal(requests[0].init.method, 'POST');
  assert.equal(requests[0].url, '/api/tasks/failed-task/recover-draft'); assert.equal(requests[0].token, 'O'.repeat(43));
  assert.equal(h.receipts.length, 0); assert.equal(h.writes.length, 0); assert.equal(h.mounts.length, 0);
  assert.equal(h.draftRef.current, h.old); assert.deepEqual(h.calls.filter(x => typeof x === 'string'), ['confirm', 'history-read']);
  assert.ok(h.errors.at(-1).startsWith(`denied-${status}`)); assert.match(h.errors.at(-1), /不会自动重试/);
});
for (const reason of ['transport ambiguous', 'source expired', 'unsupported legacy']) test(reason + ': one attempt, existing UI draft untouched', async () => {
  const h = harness({ response: Error(reason) }); await h.edit(2);
  assert.equal(h.calls.filter(x => x.init).length, 1); assert.equal(h.draftRef.current, h.old);
  assert.equal(h.writes.length, 0); assert.equal(h.mounts.length, 0); assert.ok(h.calls.includes('history-read'));
});
test('storage rejection retains new receipt but preserves existing UI draft', async () => {
  const h = harness({ failWrite: true }); await h.edit(2);
  assert.equal(h.receipts.length, 1); assert.equal(h.draftRef.current, h.old); assert.equal(h.mounts.length, 0);
  assert.match(h.errors.at(-1), /storage blocked.*现有草稿未替换/);
  assert.equal(h.calls.filter(x => x.init).length, 1); assert.equal(h.writes.length, 0);
});
for (const mutate of [r => { r.task_id = 'failed-task'; }, r => { r.files[0].bytes = 22; },
  r => { r.files[0].sha256 = 'invalid'; }, r => { r.files[0].token = 'O'.repeat(43); },
  r => { r.files[0].name = '../foreign.mp4'; }, r => { r.files[0].status = 'failed'; },
  r => { r.preferences = modes.defaultModePreferences; }, r => { r.recovery.source_task_id = 'foreign-task'; },
  r => { r.recovery.task_id = 'foreign-draft'; }, r => { r.step = 1; }]) {
  test('strict malformed recovery binding fails before replacing draft: ' + mutate.toString(), async () => {
    const response = receipt(); mutate(response); const h = harness({ response }); await h.edit(2);
    assert.equal(h.writes.length, 0); assert.equal(h.mounts.length, 0); assert.equal(h.draftRef.current, h.old);
    assert.equal(h.receipts.length, 0); assert.equal(h.calls.filter(x => x.init).length, 1);
    assert.match(h.errors.at(-1), /现有草稿未替换.*不会自动重试/);
  });
}
const fences = {
  selection: h => { h.selectedRef.current = { ...h.selectedRef.current }; },
  generation: h => { h.generation.current++; },
  intent: h => { h.intentRef.current = {}; },
  abort: h => { h.selectedRef.current.scope.abort(); },
  page: h => { h.pageRef.current = 'create'; },
  alive: h => { h.alive.current = false; },
};
for (const [name, fence] of Object.entries(fences)) test(`recovery response fenced by ${name}: retain independent receipt only, never replace current draft`, async () => {
  const h = harness({ held: true }); const action = h.edit(2); fence(h); h.release(); await action;
  assert.equal(h.calls.filter(x => x.init).length, 1); assert.equal(h.receipts.length, 1);
  assert.equal(h.receipts[0].taskId, 'new-draft'); assert.equal(h.receipts[0].accessToken, 'N'.repeat(43));
  assert.equal(h.writes.length, 0); assert.equal(h.mounts.length, 0); assert.equal(h.draftRef.current, h.old);
  assert.deepEqual(h.errors, ['']); assert.ok(!h.calls.includes('history-read'));
});
test('double-click is fenced while one POST is held; superseded response retains only receipt', async () => {
  const h = harness({ held: true }); const first = h.edit(2); await Promise.resolve();
  await h.edit(2); assert.equal(h.calls.filter(x => x.init).length, 1);
  h.generation.current++; h.release(); await first;
  assert.equal(h.receipts.length, 1); assert.equal(h.mounts.length, 0); assert.equal(h.draftRef.current, h.old);
});
test('scoped ready snapshot parses as complete server file without reselection or transfer', () => {
  const file = receipt().files[0];
  const snapshot = uploads.parseUploadSnapshot({ ...file, id: file.up_id, progress: 100,
    chunks: [0], received_bytes: 19, chunk_size: 8 * 1024 * 1024, total_chunks: 1,
    has_speech: false, asr_confidence: null, transcript: { segments: [], speakers: [] },
    probe_ok: true, can_materialize: true, thumb_url: null, wave_url: null }, file.up_id,
    { draftTaskId: 'new-draft', server_file_id: file.file_id });
  assert.equal(snapshot.status, 'ready'); assert.equal(snapshot.bytes, 19); assert.equal(snapshot.can_materialize, true);
});

test('scoped recovered ready authority rejects missing probe metadata without upgrading unscoped legacy receipts', () => {
  const file = receipt().files[0], scope = { draftTaskId: 'new-draft', server_file_id: file.file_id };
  const valid = { ...file, id: file.up_id, progress: 100, chunks: [0], received_bytes: 19,
    chunk_size: 8 * 1024 * 1024, total_chunks: 1, has_speech: false, asr_confidence: null,
    transcript: { segments: [], speakers: [] }, probe_ok: true, can_materialize: true,
    metadata_only: false, phase: 'done', thumb_url: null, wave_url: null };
  for (const key of ['probe_ok', 'width', 'height', 'fps']) {
    const incomplete = { ...valid }; delete incomplete[key];
    assert.throws(() => uploads.parseUploadSnapshot(incomplete, file.up_id, scope), key);
  }
  const legacy = { ...valid }; for (const key of ['probe_ok', 'width', 'height', 'fps']) delete legacy[key];
  const before = plain(legacy), parsed = uploads.parseUploadSnapshot(legacy, file.up_id);
  assert.equal(parsed.status, 'ready');
  for (const key of ['probe_ok', 'width', 'height', 'fps', 'draftTaskId', 'server_file_id']) assert.equal(parsed[key], undefined);
  assert.deepEqual(plain(legacy), before);
  assert.throws(() => uploads.parseUploadSnapshot(legacy, file.up_id, scope));
});