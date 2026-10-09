import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Actual helper + Workspace validator/handlers compiled in memory. These are
// deterministic storage/lifecycle contracts, NOT mounted ReactDOM/E2E proof.
// Only synthetic capabilities; no token/content dumps, disk writes, real
// localStorage, network, backend, data/env, providers, or shared build output.
const read = relative => readFileSync(new URL(relative, import.meta.url), 'utf8');
const source = read('../src/components/Workspace.tsx');
const ast = ts.createSourceFile('Workspace.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
const nodes = name => {
  const found = [];
  const visit = node => {
    if ((ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node)) && node.name?.getText(ast) === name) found.push(node);
    ts.forEachChild(node, visit);
  };
  visit(ast); assert.equal(found.length, 1, `Unique actual declaration: ${name}`); return found[0];
};
const declaration = name => {
  const node = nodes(name);
  return `${ts.isVariableDeclaration(node) ? 'const ' : ''}${printer.printNode(ts.EmitHint.Unspecified, node, ast)}${ts.isVariableDeclaration(node) ? ';' : ''}`;
};
const blocked = () => { throw new Error('Forbidden test side effect'); };
function load(code, deps = {}, globals = {}) {
  const compiled = ts.transpileModule(code, { fileName: 'actual.tsx', reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
  assert.equal(compiled.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  const module = { exports: {} };
  new vm.Script(compiled.outputText).runInContext(vm.createContext({ module, exports: module.exports,
    URL, URLSearchParams, AbortController, Headers, fetch: blocked, localStorage: { getItem: blocked, setItem: blocked },
    console: { log: blocked, error: blocked, warn: blocked }, ...globals,
    require(name) { assert.ok(Object.hasOwn(deps, name), 'Known dependency'); return deps[name]; },
  }), { timeout: 1000 });
  return module.exports;
}
const modes = load(read('../src/lib/productionModes.ts'), { '../../../backend/mode_rules.json': JSON.parse(read('../../backend/mode_rules.json')) });
const media = load(read('../src/lib/mediaInput.ts'));
const uploads = load(read('../src/lib/uploadSessions.ts'), { './appApi': { createAppRequest: blocked }, './productionModes': modes, './mediaInput': media });
const helper = load(read('../src/lib/submissionRecovery.ts'));
const persistence = load(read('../src/lib/v2Persistence.ts'));
const declarations = ['MAX_DRAFT_LENGTH', 'MAX_CORE_LENGTH', 'MAX_SCRIPT_LENGTH', 'isObject', 'invalidDraft', 'draftText', 'draftNumber',
  'readMediaMetadata', 'validateDraft', 'parseJson', 'coreArchive', 'writeDraftCache', 'readDraftCache', 'hasDraftContent', 'restoredDraftNote'];
function validators(storage) {
  return load(`${declarations.map(declaration).join('\n')}\nexport { validateDraft, writeDraftCache, readDraftCache, hasDraftContent, restoredDraftNote };`, {}, {
    ...modes, ...uploads, ...media, v2Persistence: persistence.createV2Persistence(() => storage),
    localStorage: storage, DRAFT_KEY: 'gm-core-v1', LEGACY_DRAFT_KEY: 'golden-mic.draft.v2', defaultPreferences: modes.defaultModePreferences,
  });
}
function store() {
  const data = new Map(), reads = [], writes = [];
  return { data, reads, writes, getItem(key) { reads.push(key); return data.get(key) ?? null; },
    setItem(key, value) { writes.push(key); data.set(key, value); }, removeItem(key) { data.delete(key); } };
}
const key = id => helper.RECOVERY_PREFIX + id;
const token = 'synthetic_only_not_a_real_capability_000000000000';
function draft(mode = 'mixed') {
  const file = { name: 'fixture.mp4', size: 3, lastModified: 1, type: 'video/mp4', kind: 'video', duration: 10, note: 'fixture', trim_start: 1, trim_end: 8 };
  file.id = media.fileIdentity(file);
  const script = mode === 'mixed' ? 'Title\nNarration。\n同期：Quote。' : 'Title\nContent。';
  return { legacyRestore: true, script, mode, step: 3, files: [file], preferences: modes.defaultModePreferences(mode),
    elements: {}, sentenceChecks: {}, voice: 'ai', ownVoice: null, sentenceKinds: {}, speakers: [],
    sentences: modes.parseModeScript(script, mode), uploadIds: ['upload-one'], uploadTokens: { 'upload-one': token },
    uploadBindings: [{ file_id: file.id, upload_id: 'upload-one', sha256: 'a'.repeat(64), chunk_size: 8 * 1024 * 1024, put_url: '/api/uploads/upload-one/chunks' }] };
}
function fixture() {
  const storage = store(), valid = validators(storage); let time = 1_800_000_000_000;
  const create = () => helper.createSubmissionRecovery(valid.validateDraft, () => storage, () => time);
  return { storage, valid, create, now: () => time, advance: delta => { time += delta; }, recovery: create() };
}
const same = (a, b) => assert.ok(JSON.stringify(a) === JSON.stringify(b), 'Projected values match (contents not printed)');

// Execute the actual hash-navigation handler and departure guard, without a
// browser. A rejected same-document navigation must NOT open the target task.
function hashNavigation(confirm) {
  const route = nodes('routeHandler');
  assert.ok(ts.isVariableDeclaration(route));
  const handlers = [];
  const visit = node => {
    if (ts.isBinaryExpression(node) && node.left.getText(ast) === 'routeHandler.current'
        && node.operatorToken.kind === ts.SyntaxKind.EqualsToken) handlers.push(node.right);
    ts.forEachChild(node, visit);
  };
  visit(ast); assert.equal(handlers.length, 1);
  const state = { confirmations: [], opened: [], restored: [], errors: [] };
  const location = { hash: '#work=task-A' };
  const api = load(`${['validId', 'readRoute', 'canLeave'].map(declaration).join('\n')}
    export const handle = ${printer.printNode(ts.EmitHint.Expression, handlers[0], ast)};`, {}, {
    location, window: { confirm(message) { state.confirmations.push(message); return confirm; } },
    uploadController: { current: null }, actionLock: { current: false }, intentRef: { current: null },
    pageRef: { current: 'create' }, draftRef: { current: { script: 'Unrelated saved manuscript' } },
    draftDirty: { current: false }, hasDraftContent: value => !!value.script,
    activeHash: { current: '#create' }, setError: message => state.errors.push(message),
    writeHash(hash, replace) { state.restored.push({ hash, replace }); location.hash = hash; },
    beginOpen(...args) { state.opened.push(args); }, navigate: blocked,
    document: { getElementById: blocked },
  });
  api.handle(false);
  return { ...state, location };
}

test('same-document task hash with a saved draft and dismissed confirmation restores creator route', () => {
  const result = hashNavigation(false);
  assert.equal(result.confirmations.length, 1); assert.match(result.confirmations[0], /^离开创作页？/);
  assert.equal(result.opened.length, 0); assert.equal(result.location.hash, '#create');
  same(result.restored, [{ hash: '#create', replace: true }]); assert.equal(result.errors.length, 0);
});

test('explicitly accepted draft departure opens the requested task once with confirmed hash intent', () => {
  const result = hashNavigation(true);
  assert.equal(result.confirmations.length, 1); assert.equal(result.restored.length, 0);
  same(result.opened, [['task-A', 'result', { confirmed: true, fromHash: true }]]);
  assert.equal(result.location.hash, '#work=task-A'); assert.equal(result.errors.length, 0);
});

for (const mode of ['voiceover', 'mixed', 'original']) test(`refresh simulation restores exact ${mode} submission only by task ID`, () => {
  const f = fixture(), input = draft(mode);
  if (mode !== 'voiceover') input.sentences.at(-1).source_hint = { upload_id: 'upload-one', seg_id: 'segment-one' };
  assert.equal(f.recovery.save('task-A', input), true);
  const reads = f.storage.reads.length, reloaded = f.create();
  assert.equal(f.storage.reads.length, reads, 'Construction performs no recovery read');
  const restored = reloaded.read('task-A'); assert.equal(restored.status, 'saved');
  same(restored.draft, f.valid.validateDraft(input));
  assert.equal(reloaded.read('task-B').status, 'missing');
});

test('independent tabs and task keys cannot clobber another snapshot or main/history caches', () => {
  const f = fixture();
  f.storage.data.set('gm-core-v1', '{"draft":null,"history":["archival"],"tasks":{}}');
  f.storage.data.set('golden-mic.history.v1', 'historical receipt bytes');
  const before = [...f.storage.data];
  assert.equal(f.recovery.save('task-A', draft('original')), true);
  assert.equal(f.create().save('task-B', draft('voiceover')), true);
  assert.equal(f.create().read('task-A').draft.mode, 'original');
  assert.equal(f.create().read('task-B').draft.mode, 'voiceover');
  for (const [k, value] of before) assert.equal(f.storage.data.get(k), value);
  assert.ok(f.storage.writes.every(k => k.startsWith(helper.RECOVERY_PREFIX)));
});

test('snapshots are detached on save and on every read, including memory fallback', () => {
  const f = fixture(), input = draft(); f.recovery.save('task-A', input);
  input.script = 'changed'; input.files[0].note = 'changed'; input.uploadTokens['upload-one'] = 'changed';
  const first = f.recovery.read('task-A'); first.draft.script = 'changed again'; first.draft.files.length = 0;
  same(f.recovery.read('task-A').draft, f.valid.validateDraft(draft()));
});

test('existing strict whitelist drops unknown fields, file bodies, URLs, paths and report material', () => {
  const f = fixture(), input = draft();
  input.report = { rows: ['invented'] }; input.access_token = token; input.files[0].file = { bytes: [1, 2, 3] };
  input.files[0].url = 'blob:fixture'; input.files[0].path = 'C:/private'; input.preferences.unknown = true;
  input.uploadBindings[0].preview_url = 'https://invalid.example';
  assert.equal(f.recovery.save('task-A', input), true);
  const restored = f.create().read('task-A').draft;
  same(restored, f.valid.validateDraft(draft()));
  assert.ok(!f.storage.data.get(key('task-A')).includes('blob:fixture'));
});

for (const [label, mutate] of [
  ['invalid mode', d => { d.mode = 'other'; }], ['missing capability', d => { d.uploadTokens = {}; }],
  ['extra capability', d => { d.uploadTokens.other = token; }], ['foreign binding', d => { d.uploadBindings[0].file_id = '["other.mp4",3,1]'; }],
  ['unsafe put path', d => { d.uploadBindings[0].put_url = 'https://invalid.example/chunks'; }],
  ['foreign source hint', d => { d.sentences[1].source_hint = { upload_id: 'other', seg_id: 'one' }; }],
  ['stale sentence text', d => { d.sentences[0].text = 'stale'; }], ['invalid trim', d => { d.files[0].trim_start = Infinity; }],
  ['oversize manuscript', d => { d.script = 'x'.repeat(100001); }], ['duplicate upload', d => { d.uploadIds.push('upload-one'); }],
]) test(`strict validation refuses ${label} before any write`, () => {
  const f = fixture(), input = draft(); mutate(input);
  assert.equal(f.recovery.save('task-A', input), false); assert.equal(f.storage.writes.length, 0);
  assert.equal(f.recovery.read('task-A').status, 'missing');
});

for (const [label, mutate] of [
  ['bad JSON', () => '{'], ['null', () => 'null'], ['array', () => '[]'],
  ['oversize envelope', () => ' '.repeat(helper.RECOVERY_MAX_LENGTH + 1)],
  ['unsupported version', v => JSON.stringify({ ...v, version: 2 })],
  ['cross-task envelope', v => JSON.stringify({ ...v, taskId: 'task-B' })],
  ['future time', v => JSON.stringify({ ...v, savedAt: v.savedAt + 1, expiresAt: v.expiresAt + 1 })],
  ['extended expiry', v => JSON.stringify({ ...v, expiresAt: v.expiresAt + 1 })],
  ['bad draft', v => JSON.stringify({ ...v, draft: {} })],
]) test(`malformed ${label} is refused without overwriting raw cache`, () => {
  const f = fixture(); f.recovery.save('task-A', draft());
  const raw = mutate(JSON.parse(f.storage.data.get(key('task-A')))); f.storage.data.set(key('task-A'), raw);
  const writes = f.storage.writes.length;
  assert.equal(f.create().read('task-A').status, 'invalid');
  assert.equal(f.storage.writes.length, writes); assert.ok(f.storage.data.get(key('task-A')) === raw);
});

test('invalid task IDs cannot address sibling storage keys', () => {
  const f = fixture();
  for (const id of ['', '../gm-core-v1', 'a/b', 'x'.repeat(129)]) {
    assert.equal(f.recovery.save(id, draft()), false); assert.equal(f.recovery.read(id).status, 'invalid'); assert.equal(f.recovery.forget(id), false);
  }
  assert.equal(f.storage.reads.length, 0); assert.equal(f.storage.writes.length, 0);
});

test('TTL boundary refuses both memory and refreshed records; reads never extend or sweep TTL', () => {
  const f = fixture(); f.recovery.save('task-A', draft()); f.advance(helper.RECOVERY_TTL_MS - 1);
  assert.equal(f.create().read('task-A').status, 'saved'); f.advance(1);
  assert.equal(f.create().read('task-A').status, 'expired'); assert.equal(f.recovery.read('task-A').status, 'expired');
  assert.equal(f.storage.writes.length, 1); assert.ok(f.storage.data.has(key('task-A')));
});

for (const kind of ['access', 'read', 'quota', 'readback']) test(`storage ${kind} failure preserves memory without throwing or logging capabilities`, () => {
  const f = fixture(); let broken = true;
  const wrapped = { ...f.storage,
    getItem(k) { if (broken && kind === 'read') throw new Error(token); if (broken && kind === 'readback') return null; return f.storage.getItem(k); },
    setItem(k, v) { if (broken && kind === 'quota') throw new Error(token); if (broken && kind === 'readback') return; f.storage.setItem(k, v); },
  };
  const getStorage = () => { if (broken && kind === 'access') throw new Error(token); return wrapped; };
  const r = helper.createSubmissionRecovery(f.valid.validateDraft, getStorage);
  assert.equal(r.save('task-A', draft()), false); assert.equal(r.pendingCount(), 1); assert.equal(r.read('task-A').status, 'memory');
  const reloaded = helper.createSubmissionRecovery(f.valid.validateDraft, getStorage);
  assert.ok(['missing', 'unavailable'].includes(reloaded.read('task-A').status));
  broken = false; assert.equal(r.retry(), 0); assert.equal(reloaded.read('task-A').status, 'saved');
});

test('write succeeded but readback threw: local retry confirms same immutable record', () => {
  const f = fixture(); let wrote = false, broken = true;
  const wrapped = { ...f.storage, setItem(k, v) { f.storage.setItem(k, v); wrote = true; },
    getItem(k) { if (wrote && broken) throw new Error(token); return f.storage.getItem(k); } };
  const r = helper.createSubmissionRecovery(f.valid.validateDraft, () => wrapped, f.now);
  assert.equal(r.save('task-A', draft()), false); broken = false; assert.equal(r.retry(), 0);
  assert.equal(f.create().read('task-A').status, 'saved');
});

test('conflicting existing key is never overwritten by save or retry', () => {
  const f = fixture(); f.storage.data.set(key('task-A'), '{unreadable');
  assert.equal(f.recovery.save('task-A', draft()), false); assert.equal(f.recovery.retry(), 1);
  assert.equal(f.storage.data.get(key('task-A')), '{unreadable'); assert.equal(f.storage.writes.length, 0);
});

test('explicit forget is task-scoped; removal failure is reported and retryable', () => {
  const f = fixture(); f.recovery.save('task-A', draft()); f.recovery.save('task-B', draft());
  const remove = f.storage.removeItem; f.storage.removeItem = blocked;
  assert.equal(f.recovery.forget('task-A'), false); f.storage.removeItem = remove;
  assert.equal(f.recovery.forget('task-A'), true); assert.equal(f.create().read('task-A').status, 'missing');
  assert.equal(f.create().read('task-B').status, 'saved');
});

// Execute the actual Workspace callbacks with explicit refs/setters instead of
// duplicating their branching logic. Discarding this harness simulates reload;
// the next one shares only the synthetic storage, not refs or recovery memory.
function workspace(f, { id = 'task-A', status = 'failed', errorKind, currentDraft = null, confirm = true, upload, clearPending = false, taskToken } = {}) {
  const events = [], refs = { draft: currentDraft }, timers = [];
  const globals = { ...f.valid, submissionRecovery: f.create(), serviceReady: true, window: { confirm: () => { events.push('confirm'); return confirm; } },
    actionLock: { current: false }, intentRef: { current: null }, pageRef: { current: 'processing' },
    selectedRef: { current: { id, token: taskToken, scope: new AbortController(), request: blocked } }, task: { task_id: id, status, errorKind },
    draftRef: { get current() { return refs.draft; }, set current(v) { refs.draft = v; } },
    draftDirty: { current: false }, draftBlocked: { current: false }, draftNeedsClear: clearPending,
    draftSuppressed: { current: false }, draftSaved: { current: '' }, draftTimer: { current: null },
    uploadUncertain: false, uploadController: { current: null }, alive: { current: true }, generation: { current: 1 }, wizardGeneration: { current: 1 },
    setTimeout(callback) { timers.push(callback); return timers.length; }, clearTimeout() {},
    cancelReads() { events.push('cancelReads'); }, unmountWizard() { events.push('unmountWizard'); },
    mountWizard(seed) { refs.seed = seed; events.push('mountWizard'); }, showPage(page) { globals.pageRef.current = page; },
    writeHash(hash) { refs.hash = hash; }, saveDraft() { events.push('saveDraft'); }, clearDraftCache() { events.push('clearDraftCache'); },
    refreshHistory() { events.push('refreshHistory'); }, beginOpen() { events.push('beginOpen'); },
    parseReceipt(value) { if (!value?.task_id) throw new Error('Invalid receipt'); return value; },
    async uploadTask(value) { events.push('create'); return upload ? upload(value) : { task_id: id, access_token: token }; },
    rememberReceipt() { events.push('rememberReceipt'); return {}; }, errorText: () => 'Synthetic failure',
  };
  for (const name of ['select', 'setTask', 'setUpload', 'setMissing', 'setDialog', 'setDraftPending', 'setDraftStatus', 'setUploading', 'setNow',
    'setUploadUncertain', 'setPendingRecoveryCount', 'setRecoveryProblem', 'setNotice', 'setError']) globals[name] = value => { refs[name] = value; };
  const handlers = load(`${declaration('editFailedDraft')}\n${declaration('submit')}\nexport { editFailedDraft, submit };`, {}, globals);
  return { ...handlers, globals, refs, events, timers };
}
function submission(d) { return { script: d.script, mode: d.mode, preferences: d.preferences, sentences: d.sentences, sentenceKinds: d.sentenceKinds,
  speakers: d.speakers, uploadIds: d.uploadIds, uploadTokens: d.uploadTokens, files: [], asset_options: d.files.map(({ note, trim_start, trim_end }) => ({ note, trim_start, trim_end })) }; }

test('actual submit waits for accepted receipt, then persists exact input before clearing main draft', async () => {
  const f = fixture(), input = draft(); let release;
  const pending = new Promise(resolve => { release = resolve; });
  const w = workspace(f, { currentDraft: input, upload: () => pending }); w.globals.pageRef.current = 'create';
  const value = submission(input); value.script = 'Submitted title\nNarration。\n同期：Quote。'; value.asset_options[0].note = 'submitted';
  const result = w.submit(value); assert.equal(f.storage.writes.length, 0);
  release({ task_id: 'task-A', access_token: token }); await result;
  assert.equal(w.events.filter(e => e === 'create').length, 1);
  const restored = f.create().read('task-A'); assert.equal(restored.status, 'saved');
  assert.equal(restored.draft.script, value.script); assert.equal(restored.draft.files[0].note, 'submitted');
  assert.equal(w.refs.draft, null); assert.equal(w.refs.setUploadUncertain, false);
});

test('rejected creation never saves a recovery snapshot or discards current draft', async () => {
  const f = fixture(), input = draft(); const w = workspace(f, { currentDraft: input, upload: async () => { throw new Error('not accepted'); } });
  w.globals.pageRef.current = 'create'; await assert.rejects(w.submit(submission(input)));
  assert.equal(f.storage.writes.length, 0); assert.equal(w.refs.draft, input); assert.equal(w.refs.setUploadUncertain, true);
});

test('a clear refusal (e.g. the hourly start limit) shows its reason and does not claim a work may exist', async () => {
  const f = fixture(), input = draft();
  const refusal = Object.assign(new Error('这一小时内开始制作的次数已经用完（每小时最多 10 次）。约 5 分钟后可以再点“开始制作”。'), { status: 429 });
  const w = workspace(f, { currentDraft: input, upload: async () => { throw refusal; } });
  w.globals.pageRef.current = 'create'; await assert.rejects(w.submit(submission(input)));
  assert.equal(w.refs.setUploadUncertain, false, 'nothing was created, so no "the server may have created a work" banner');
  assert.equal(f.storage.writes.length, 0); assert.equal(w.refs.draft, input);
});

test('accepted creation plus quota failure stays successful; refresh cannot claim durable recovery', async () => {
  const f = fixture(), input = draft(); f.storage.setItem = blocked;
  const w = workspace(f, { currentDraft: input }); w.globals.pageRef.current = 'create'; await w.submit(submission(input));
  assert.equal(w.refs.setUploadUncertain, false); assert.equal(w.refs.setPendingRecoveryCount, 1); assert.ok(w.refs.setRecoveryProblem);
  assert.equal(w.events.filter(e => e === 'create').length, 1); assert.equal(f.create().read('task-A').status, 'missing');
  assert.equal(w.globals.submissionRecovery.read('task-A').status, 'memory');
});

// Intentional v2 boundary: old browser snapshots remain readable evidence, not
// authority to clone a committed task. No fabricated recover-draft receipt.
for (const mode of ['voiceover', 'mixed', 'original']) test(`legacy ${mode} snapshot cannot authorize failed-task recovery without its task capability`, async () => {
  const f = fixture(); f.recovery.save('task-A', draft(mode));
  const w = workspace(f); assert.equal(w.refs.seed, undefined); assert.equal(w.refs.draft, null);
  const raw = f.storage.data.get(key('task-A')), writes = f.storage.writes.length;
  await w.editFailedDraft(); assert.equal(w.refs.seed, undefined); assert.equal(w.refs.draft, null);
  assert.equal(w.refs.setError, '缺少原任务的访问凭据，无法恢复。现有草稿保留；不会从报告或旧浏览器快照补造原稿。');
  assert.equal(w.events.includes('create'), false); assert.equal(w.events.includes('saveDraft'), false);
  assert.equal(f.storage.writes.length, writes); assert.equal(f.storage.data.get(key('task-A')), raw);
  same(f.create().read('task-A').draft.uploadBindings, draft(mode).uploadBindings);
});

test('quote_missing does not promote a legacy source hint into recovery authority', async () => {
  const f = fixture(), input = draft('original'); input.sentences[0].source_hint = { upload_id: 'upload-one', seg_id: 'one' };
  f.recovery.save('task-A', input); const w = workspace(f, { errorKind: 'quote_missing' }); await w.editFailedDraft();
  assert.equal(w.refs.seed, undefined); assert.equal(w.refs.hash, undefined);
  assert.equal(w.refs.setError, '缺少原任务的访问凭据，无法恢复。现有草稿保留；不会从报告或旧浏览器快照补造原稿。');
  same(f.create().read('task-A').draft.sentences, f.valid.validateDraft(input).sentences);
});

test('a different task cannot recover the last submission or replace an unrelated draft', () => {
  const f = fixture(), other = draft('voiceover'); f.recovery.save('task-A', draft('mixed'));
  const w = workspace(f, { id: 'task-B', currentDraft: other }); w.editFailedDraft();
  assert.equal(w.refs.draft, other); assert.equal(w.refs.seed, undefined); assert.ok(w.refs.setError);
});

for (const conflict of ['content', 'dirty', 'blocked', 'clearPending']) test(`actual ${conflict} draft conflict requires confirmation and cancel is inert`, () => {
  const f = fixture(); f.recovery.save('task-A', draft());
  const w = workspace(f, { taskToken: token, currentDraft: conflict === 'content' ? draft('voiceover') : null, confirm: false, clearPending: conflict === 'clearPending' });
  if (conflict === 'dirty') w.globals.draftDirty.current = true;
  if (conflict === 'blocked') w.globals.draftBlocked.current = true;
  const previous = w.refs.draft; w.editFailedDraft(); assert.equal(w.refs.draft, previous);
  assert.deepEqual(w.events, ['confirm']); assert.equal(w.refs.seed, undefined);
});

test('local submitted snapshot cannot replace an unrelated editor draft without server recovery authority', async () => {
  const f = fixture(); f.recovery.save('task-A', draft()); const w = workspace(f, { currentDraft: draft('voiceover') });
  const previous = w.refs.draft, raw = f.storage.data.get(key('task-A'));
  await w.editFailedDraft(); assert.equal(w.refs.seed, undefined); assert.equal(w.refs.draft, previous);
  assert.equal(w.events.includes('confirm'), false);
  assert.equal(w.refs.setError, '缺少原任务的访问凭据，无法恢复。现有草稿保留；不会从报告或旧浏览器快照补造原稿。');
  assert.equal(f.storage.data.get(key('task-A')), raw);
  assert.equal(f.create().read('task-A').draft.step, 3);
});

for (const state of ['queued', 'running', 'done']) test(`actual return-to-edit refuses ${state} task without reading snapshot`, () => {
  const f = fixture(); f.recovery.save('task-A', draft()); const w = workspace(f, { status: state });
  const before = f.storage.reads.length; w.editFailedDraft(); assert.equal(f.storage.reads.length, before); assert.equal(w.refs.seed, undefined);
});

test('actual return-to-edit refuses mismatched selection, pending navigation and aborted scope', () => {
  for (const mutate of [w => { w.globals.task.task_id = 'task-B'; }, w => { w.globals.intentRef.current = {}; },
    w => { w.globals.selectedRef.current.scope.abort(); }, w => { w.globals.pageRef.current = 'history'; }]) {
    const f = fixture(); f.recovery.save('task-A', draft()); const w = workspace(f); mutate(w);
    const before = f.storage.reads.length; w.editFailedDraft(); assert.equal(f.storage.reads.length, before); assert.equal(w.refs.seed, undefined);
  }
});

for (const failure of ['expired', 'invalid', 'unavailable']) test(`actual ${failure} recovery preserves current draft and exposes only generic reason`, () => {
  const f = fixture(); f.recovery.save('task-A', draft());
  if (failure === 'expired') f.advance(helper.RECOVERY_TTL_MS);
  if (failure === 'invalid') f.storage.data.set(key('task-A'), token);
  if (failure === 'unavailable') f.storage.getItem = () => { throw new Error(token); };
  const current = draft('voiceover'), w = workspace(f, { currentDraft: current }); w.editFailedDraft();
  assert.equal(w.refs.draft, current); assert.equal(w.refs.seed, undefined); assert.ok(w.refs.setError && !w.refs.setError.includes(token));
});

test('main draft v3 migrates to v2 with exact legacy archive; old keys and recovery snapshots remain untouched', () => {
  const f = fixture(), archive = { tasks: { legacy: { status: 'done', receipt: 'old' } }, checked: {}, pend: {}, history: ['old'], quotaUsed: 1 };
  const legacyRaw = JSON.stringify(archive);
  f.storage.data.set('gm-core-v1', legacyRaw); f.recovery.save('task-A', draft());
  const recoveryRaw = f.storage.data.get(key('task-A'));
  f.valid.writeDraftCache(draft(), Date.now()); let core = JSON.parse(f.storage.data.get('gm-modes-v1'));
  assert.equal(core.schemaVersion, 1); assert.equal(core.archive.legacy['gm-core-v1'], legacyRaw);
  assert.equal(f.storage.data.get('gm-core-v1'), legacyRaw);
  assert.equal(core.draftVersion, 3); assert.equal(f.valid.readDraftCache().value.mode, 'mixed');
  f.valid.writeDraftCache(null, null); core = JSON.parse(f.storage.data.get('gm-modes-v1'));
  assert.equal(core.draft, null); assert.equal(f.valid.readDraftCache().value, null);
  assert.equal(core.archive.legacy['gm-core-v1'], legacyRaw); assert.equal(f.storage.data.get('gm-core-v1'), legacyRaw);
  assert.ok(f.storage.data.get(key('task-A')) === recoveryRaw);
});

test('expired pending memory cannot be repersisted to reset its retention clock', () => {
  const f = fixture(), original = f.storage.setItem; f.storage.setItem = blocked;
  assert.equal(f.recovery.save('task-A', draft()), false); f.advance(helper.RECOVERY_TTL_MS);
  f.storage.setItem = original; assert.equal(f.recovery.retry(), 1); assert.equal(f.storage.writes.length, 0);
  assert.equal(f.recovery.read('task-A').status, 'expired');
});

test('whole narration recording restores metadata only; no File or recording is recreated', () => {
  const f = fixture(), input = draft(); input.voice = 'self'; input.preferences.voice = 'mine';
  input.ownVoice = { ...input.files[0], name: 'voice.wav', type: 'audio/wav', kind: 'audio', file: { bytes: [1] } };
  f.recovery.save('task-A', input); const restored = f.create().read('task-A').draft;
  assert.equal(restored.voice, 'self'); assert.equal(restored.ownVoice.kind, 'audio');
  assert.equal(Object.hasOwn(restored.ownVoice, 'file'), false);
  assert.ok(f.valid.restoredDraftNote(restored).includes('整篇配音录音未存入浏览器，仍需重选或重录'));
});

// Use the actual existing wizard read path (named observeUpload in this tree),
// with its real pollUploadStatus/getUploadStatus and only the HTTP seam mocked.
const wizardSource = read('../src/components/CreateWizard.tsx');
const wizardAst = ts.createSourceFile('CreateWizard.tsx', wizardSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function wizardFunction(name) {
  const found = [];
  const visit = node => { if (ts.isFunctionDeclaration(node) && node.name?.text === name) found.push(node); ts.forEachChild(node, visit); };
  visit(wizardAst); assert.equal(found.length, 1);
  return printer.printNode(ts.EmitHint.Unspecified, found[0], wizardAst);
}
for (const failure of [401, 403, 404, 410]) test(`restored upload ${failure} uses existing read-only validation, never reupload or ASR`, async () => {
  // Legacy draft migration is distinct from committed-task recover-draft.
  const f = fixture(); f.storage.data.set('gm-core-v1', JSON.stringify({ draftVersion: 3, draft: draft() }));
  const restored = f.valid.readDraftCache(); assert.equal(restored.blocked, false); assert.equal(restored.migrate, true);
  const seed = restored.value, calls = [], state = { errors: {}, uploads: {} }, polling = { current: new Map() };
  const realUploads = load(read('../src/lib/uploadSessions.ts'), { './productionModes': modes, './mediaInput': media,
    './appApi': { createAppRequest: () => async (path, init) => {
      calls.push({ path, method: init?.method ?? 'GET' });
      assert.ok(init.headers['X-Upload-Token'] === token, 'Uses bound capability without printing it');
      throw new Error(`Synthetic HTTP ${failure}`);
    } },
  });
  const methods = load(['sessionFor', 'observeUpload'].map(wizardFunction).join('\n') + '\nexport { observeUpload };', {}, {
    ...realUploads, mounted: { current: true }, bindingsRef: { current: seed.uploadBindings }, tokensRef: { current: seed.uploadTokens },
    filesRef: { current: seed.files }, uploadsRef: { current: {} }, polling, draftAccessRef: { current: undefined },
    setUploads(value) { state.uploads = value; }, setUploadErrors(update) { state.errors = update(state.errors); },
    publishSnapshot: blocked, invalidatePreview() {}, errorMessage: error => error.message,
  });
  methods.observeUpload(seed.files[0].id);
  for (let i = 0; i < 20; i++) await Promise.resolve();
  assert.deepEqual(calls, [{ path: '/api/uploads/upload-one', method: 'GET' }]);
  assert.equal(Object.keys(state.errors).length, 1); assert.equal(polling.current.size, 0);
  assert.equal(Object.keys(state.uploads).length, 0); assert.ok(!seed.files[0].file);
  same(seed.uploadBindings, draft().uploadBindings);
});

test('the accepted-work banner is exactly the one the status effect clears', () => {
  const match = /const RECEIVED_NOTICE = '([^']+)';/.exec(source);
  assert.ok(match, 'RECEIVED_NOTICE is declared');
  assert.ok(source.includes(`setNotice('${match[1]}')`), 'submit shows the same text, so the banner cannot stay forever');
  assert.match(source, /setNotice\(current => current === RECEIVED_NOTICE \? '' : current\)/);
});
