import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Node-only client contracts. Actual TypeScript
// is transpiled in memory; no server, browser, provider, socket or file writes.
// These are NOT React lifecycle, accessibility, rendering or media acceptance.
const apiSource = readFileSync(new URL('../src/lib/workbenchApi.ts', import.meta.url), 'utf8');
const componentSource = readFileSync(new URL('../src/components/ResultWorkbench.tsx', import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));

function load(source, fileName, bindings = {}) {
  const compiled = ts.transpileModule(source, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, sourceMap: false } });
  assert.equal(compiled.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0,
    'Actual source must transpile without syntax diagnostics.');
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, URL, FormData, ...bindings,
    require() { throw new Error('Unexpected runtime dependency in isolated workbench units.'); } });
  new vm.Script(compiled.outputText, { filename: fileName }).runInContext(context, { timeout: 1000 });
  return module.exports;
}

const api = load(apiSource, 'workbenchApi.ts');

// Select the actual pure draft expressions by AST, unchanged. Only their export
// visibility is added; do not copy the dirty algorithm or mock React hooks.
function loadDraftPredicates() {
  const root = ts.createSourceFile('ResultWorkbench.tsx', componentSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const names = ['emptyDraft', 'hasEdit', 'hasUnsent'];
  const declarations = names.map(name => {
    const found = root.statements.filter(ts.isVariableStatement).flatMap(statement => [...statement.declarationList.declarations])
      .filter(declaration => ts.isIdentifier(declaration.name) && declaration.name.text === name);
    assert.equal(found.length, 1, `Find the actual ${name} expression, not a test-side replacement.`);
    return ts.factory.createVariableStatement(undefined, ts.factory.createVariableDeclarationList(found, ts.NodeFlags.Const));
  });
  const printer = ts.createPrinter();
  const source = declarations.map(node => printer.printNode(ts.EmitHint.Unspecified, node, root)).join('\n')
    + `\nexport { ${names.join(', ')} };`;
  return load(source, 'workbench-draft-predicates.ts', { hasWorkbenchPreferenceChanges: api.hasWorkbenchPreferenceChanges });
}
const draftApi = loadDraftPredicates();
const TASK = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const ROOT = `/api/tasks/${TASK}/workbench`;
const preferences = (overrides = {}) => ({ pacing: 'normal', tone: 'neutral', caption_style: 'news', enhance_speech: false,
  background_music: false, music_mood: 'auto', motion_effects: false, transitions: false,
  news_graphics: true, color_consistency: false, generative_fill: false, custom_instructions: '', ...overrides });
function contextFixture({ revision = 4, prefs = preferences(), audioSource = 'tts' } = {}) {
  const audioKind = audioSource === 'recording' ? 'sync' : audioSource;
  return { task_id: TASK, revision, status: 'done', script: '隔离测试标题\n这是一句测试稿件。', preferences: prefs,
    last_operation_error: null,
    report: { task_id: TASK, quality: { blocking_issue_count: 1, warning_count: 0, metrics: {},
      issues: [{ severity: 'error', code: 'UNIT_TEST_BLOCKER', message: 'Isolated fixture only', sentence_id: 1 }] },
    rows: [{ sentence_id: 1, sentence: '这是一句测试稿件。', shot_id: 7, duration: 2, confidence: 0.8,
      is_fallback: false, audio_kind: audioKind, audio_source: audioSource, visual_beats: [],
      description: 'Isolated fixture', thumb_url: null, spoken_text: null, replacement_instruction: null }] },
    timings: [{ sentence_id: 1, text: '这是一句测试稿件。', start: 0, end: 2, duration: 2,
      audio_kind: audioKind, audio_source: audioSource,
      ...(audioSource === 'recording' ? { recording_id: 'b'.repeat(32), transcript_verified: false, uploaded_revision: revision } : {}) }],
    current_shots: [{ sentence_id: 1, shot_ids: [7] }],
    shots: [{ shot_id: 7, source_index: 0, source_scene_index: 0, start: 0, end: 2, duration: 2,
      status: 'available', media_origin: 'source', description: 'Isolated source', unused: false,
      used_by_sentence_id: 1, selectable: true, thumb_url: `/api/tasks/${TASK}/thumbs/7.jpg` }],
    versions: [{ revision, label: 'Isolated version', created_at: '2026-09-25T00:00:00Z' }] };
}

test('omitted legacy preferences have documented defaults without rewriting the persisted snapshot', () => {
  const legacy = preferences(); delete legacy.caption_style; delete legacy.enhance_speech;
  Object.freeze(legacy);
  const ctx = api.parseContext(contextFixture({ prefs: legacy }), TASK);
  assert.deepEqual(plain(api.readWorkbenchPreferences(ctx.preferences)), { pacing: 'normal', caption_style: 'news', enhance_speech: false });
  assert.equal(Object.hasOwn(ctx.preferences, 'caption_style'), false);
  assert.equal(Object.hasOwn(ctx.preferences, 'enhance_speech'), false);
  assert.deepEqual(plain(api.diffWorkbenchPreferences(legacy, { caption_style: 'news', enhance_speech: false })), {});
});

test('all caption transitions send only a genuinely different caption_style, including none', () => {
  for (const saved of ['news', 'big', 'none']) for (const selected of ['news', 'big', 'none']) {
    const diff = api.diffWorkbenchPreferences(preferences({ caption_style: saved }), { caption_style: selected });
    assert.deepEqual(plain(diff), saved === selected ? {} : { caption_style: selected });
    assert.equal(api.hasWorkbenchPreferenceChanges(diff), saved !== selected);
  }
});

test('all pacing transitions omit unchanged pacing', () => {
  for (const saved of ['slow', 'normal', 'fast']) for (const selected of ['slow', 'normal', 'fast']) {
    const diff = api.diffWorkbenchPreferences(preferences({ pacing: saved }), { pacing: selected });
    assert.deepEqual(plain(diff), saved === selected ? {} : { pacing: selected });
  }
});

test('explicit false is a real change and survives dirty detection and JSON serialization', () => {
  const diff = api.diffWorkbenchPreferences(preferences({ enhance_speech: true }), { enhance_speech: false });
  assert.equal(Object.hasOwn(diff, 'enhance_speech'), true);
  assert.equal(diff.enhance_speech, false);
  assert.equal(api.hasWorkbenchPreferenceChanges(diff), true);
  assert.equal(JSON.stringify(diff), '{"enhance_speech":false}');
  for (const saved of [false, true]) for (const selected of [false, true]) {
    assert.deepEqual(plain(api.diffWorkbenchPreferences(preferences({ enhance_speech: saved }), { enhance_speech: selected })),
      saved === selected ? {} : { enhance_speech: selected });
  }
  assert.equal(api.hasWorkbenchPreferenceChanges({ pacing: undefined, caption_style: undefined, enhance_speech: undefined }), false);
});

test('diff whitelists editable keys, does not mutate drafts and clears selections returned to saved values', () => {
  const saved = Object.freeze(preferences({ enhance_speech: true }));
  const draft = Object.freeze({ pacing: 'slow', caption_style: 'none', enhance_speech: false,
    background_music: true, news_graphics: false, generative_fill: true, sentences: { 1: { text: '未提交的稿件' } } });
  const before = JSON.stringify({ saved, draft });
  assert.deepEqual(plain(api.diffWorkbenchPreferences(saved, draft)), { pacing: 'slow', caption_style: 'none', enhance_speech: false });
  assert.deepEqual(plain(api.diffWorkbenchPreferences(saved, { ...draft, pacing: 'normal', caption_style: 'news', enhance_speech: true })), {});
  assert.equal(JSON.stringify({ saved, draft }), before);
  assert.deepEqual(plain(api.diffWorkbenchPreferences(saved, {})), {});
});

test('actual component draft predicates include caption-only and false-only edits in unsaved guards', () => {
  for (const patch of [{ caption_style: 'none' }, { enhance_speech: false }, { enhance_speech: true }, { pacing: 'slow' }]) {
    const draft = { ...draftApi.emptyDraft(), base: 4, ...patch };
    assert.equal(draftApi.hasEdit(draft), true);
    assert.equal(draftApi.hasUnsent(draft), true);
    assert.equal(draftApi.hasUnsent({ ...draft, submitted: 5 }), false, 'Accepted changes use the existing processing guard.');
    assert.equal(draftApi.hasUnsent({ ...draft, submitted: undefined }), true, 'An unconfirmed/failed submission keeps the real editing draft.');
  }
  const reverted = { ...draftApi.emptyDraft(), base: 4, pacing: undefined, caption_style: undefined, enhance_speech: undefined };
  assert.equal(draftApi.hasEdit(reverted), false);
  assert.equal(draftApi.hasUnsent(reverted), false);
  assert.equal(draftApi.hasUnsent(draftApi.emptyDraft()), false, 'Clear-all uses the real empty draft factory.');
  assert.equal(draftApi.hasUnsent({ ...reverted, sentences: { 1: { text: '句子修改仍保留' } } }), true);
});

test('malformed persisted preferences fail closed rather than displaying invented defaults', () => {
  for (const patch of [{ caption_style: null }, { caption_style: '' }, { caption_style: 'large' }, { caption_style: 1 },
    { enhance_speech: null }, { enhance_speech: 'false' }, { enhance_speech: 0 }, { enhance_speech: {} },
    { pacing: 'invalid' }, { pacing: null }]) {
    const prefs = preferences(patch);
    assert.throws(() => api.readWorkbenchPreferences(prefs));
    assert.throws(() => api.parseContext(contextFixture({ prefs }), TASK));
  }
  for (const prefs of [null, [], false, {}]) assert.throws(() => api.readWorkbenchPreferences(prefs));
});

test('real edit wrapper sends flat changed keys with the expected revision, retaining false and sentence edits', async () => {
  const calls = [], controller = new AbortController();
  const saved = preferences({ enhance_speech: true });
  const client = api.createWorkbenchApi(TASK, async (path, init) => {
    calls.push({ path, body: JSON.parse(init.body) });
    assert.equal(init.method, 'POST'); assert.equal(init.headers['Content-Type'], 'application/json');
    assert.equal(init.signal, controller.signal);
    return { task_id: TASK, status: 'queued', current_revision: 4, revision: 5 };
  });
  const proposals = [{ caption_style: 'none' }, { caption_style: 'big' }, { enhance_speech: false },
    { pacing: 'fast', caption_style: 'none', enhance_speech: false },
    { pacing: 'normal', caption_style: 'news', enhance_speech: true }];
  const expected = [{ caption_style: 'none' }, { caption_style: 'big' }, { enhance_speech: false },
    { pacing: 'fast', caption_style: 'none', enhance_speech: false }, {}];
  for (let index = 0; index < proposals.length; index++) {
    // The last case is an actual sentence-only edit with unchanged preferences.
    const edits = index === proposals.length - 1 ? [{ sentence_id: 1, text: '新的句子', recording_id: 'b'.repeat(32) }] : [];
    const base = { expected_revision: 4, keep_sentence_ids: [1], edits };
    const receipt = await client.edit({ ...base, ...api.diffWorkbenchPreferences(saved, proposals[index]) }, controller.signal);
    assert.equal(receipt.revision, 5);
    assert.deepEqual(calls.at(-1), { path: `${ROOT}/edit`, body: { ...base, ...expected[index] } });
    assert.equal(Object.hasOwn(calls.at(-1).body, 'preferences'), false, 'Do not send the complete wizard preferences.');
  }
  assert.equal(calls.length, proposals.length, 'One explicit transport invocation per edit; no bootstrap or extra writes.');
});

test('a transport rejection preserves the caller draft and is never automatically retried', async () => {
  let attempts = 0;
  const failure = Object.assign(new Error('Isolated revision conflict'), { status: 409 });
  const batch = { expected_revision: 4, keep_sentence_ids: [1], edits: [], enhance_speech: false };
  const before = JSON.stringify(batch);
  const client = api.createWorkbenchApi(TASK, async () => { attempts++; throw failure; });
  await assert.rejects(client.edit(batch), caught => caught === failure);
  assert.equal(attempts, 1); assert.equal(JSON.stringify(batch), before);
});

test('reload after a restore receipt reads the new persisted preferences, not the original snapshot or wizard defaults', async () => {
  const calls = [], controller = new AbortController();
  let current = contextFixture({ prefs: preferences({ caption_style: 'big', enhance_speech: true }) });
  const client = api.createWorkbenchApi(TASK, async (path, init) => {
    calls.push({ path, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(init.body) : null });
    assert.equal(init.signal, controller.signal);
    if (path === `${ROOT}/context`) { assert.equal(init.cache, 'no-store'); return plain(current); }
    assert.equal(path, `/api/tasks/${TASK}/restore`);
    return { task_id: TASK, status: 'queued', current_revision: 4, revision: 5 };
  });
  const first = await client.context(controller.signal);
  assert.deepEqual(plain(api.readWorkbenchPreferences(first.preferences)), { pacing: 'normal', caption_style: 'big', enhance_speech: true });
  await client.restore(1, 4, controller.signal);
  assert.deepEqual(calls.at(-1), { path: `/api/tasks/${TASK}/restore`, method: 'POST', body: { rev: 1, expected_revision: 4 } });
  // Explicit synthetic GET response: not a simulation of backend restoration.
  current = contextFixture({ revision: 5, prefs: preferences({ pacing: 'slow', caption_style: 'none', enhance_speech: false }) });
  const next = await client.context(controller.signal);
  assert.equal(next.revision, 5);
  assert.deepEqual(plain(api.readWorkbenchPreferences(next.preferences)), { pacing: 'slow', caption_style: 'none', enhance_speech: false });
  assert.deepEqual(plain(api.diffWorkbenchPreferences(next.preferences, { caption_style: 'none', enhance_speech: false })), {});
  assert.equal(first.preferences.caption_style, 'big'); assert.equal(first.preferences.enhance_speech, true);
  assert.equal(next.report.quality.blocking_issue_count, 1, 'Changing display preferences must not imply QC success.');
  assert.equal(calls.length, 3);
});

test('preference parsing preserves validated audio-source distinctions and does not bypass recording contracts', () => {
  for (const audioSource of ['tts', 'sync', 'recording']) {
    const fixture = contextFixture({ audioSource, prefs: preferences({ caption_style: 'none', enhance_speech: true }) });
    const before = JSON.stringify(fixture);
    const parsed = api.parseContext(fixture, TASK);
    assert.equal(parsed.timings[0].audio_source, audioSource);
    assert.equal(JSON.stringify(fixture), before);
    assert.deepEqual(plain(parsed.report.quality), fixture.report.quality);
  }
  const invalid = contextFixture({ audioSource: 'recording' }); invalid.timings[0].audio_kind = 'tts';
  assert.throws(() => api.parseContext(invalid, TASK));
  assert.throws(() => api.parseContext(contextFixture(), 'another-task'));
});

test('ResultWorkbench JSX syntax is valid (static check only, not mounted UI evidence)', () => {
  const compiled = ts.transpileModule(componentSource, { fileName: 'ResultWorkbench.tsx', reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve } });
  assert.equal(compiled.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
});