import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test, { after } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

// In-memory source contracts only. No server, sockets, browser storage, media,
// providers, user tasks, build output or test evidence files are touched.
// SSR is not a claim of mounted React/pointer/keyboard/media acceptance.
const read = relative => readFileSync(new URL(relative, import.meta.url), 'utf8');
const apiSource = read('../src/lib/workbenchApi.ts');
const quoteSource = read('../src/components/QuoteEditor.tsx');
const resultSource = read('../src/components/ResultWorkbench.tsx');
const cssSource = read('../src/components/ResultWorkbench.css');
const plain = value => JSON.parse(JSON.stringify(value));
const task = 'a'.repeat(32), root = `/api/tasks/${task}`;
let networkAttempts = 0;
function forbiddenNetwork() { networkAttempts++; throw new Error('Unexpected network access'); }
after(() => assert.equal(networkAttempts, 0, 'All transport remains synthetic; no VM network attempts'));

function load(source, fileName, bindings = {}, modules = {}) {
  const compiled = ts.transpileModule(source, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    transformers: { before: [context => root => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
        && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
        ? ts.factory.createIdentifier('__testViteEnv') : ts.visitEachChild(node, visit, context);
      return ts.visitNode(root, visit);
    }] },
  });
  assert.equal(compiled.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, URL, FormData, AbortController, __testViteEnv: { DEV: false }, ...bindings,
    fetch: forbiddenNetwork, WebSocket: forbiddenNetwork, XMLHttpRequest: forbiddenNetwork,
    require(name) {
      if (Object.hasOwn(modules, name)) return modules[name];
      throw new Error(`Unexpected dependency: ${name}`);
    } });
  new vm.Script(compiled.outputText, { filename: fileName }).runInContext(context, { timeout: 1000 });
  return module.exports;
}
const api = load(apiSource, 'workbenchApi.ts');
const appApi = load(read('../src/lib/appApi.ts'), 'appApi.ts');
const modes = load(read('../src/lib/productionModes.ts'), 'productionModes.ts', {}, {
  '../../../backend/mode_rules.json': { default: JSON.parse(read('../../backend/mode_rules.json')) },
});
const quality = load(read('../src/lib/qualitySummary.ts'), 'qualitySummary.ts', {}, {
  './productionModes': modes, './workbenchApi': api,
});
const quote = load(quoteSource, 'QuoteEditor.tsx', {}, { react: React, 'react/jsx-runtime': jsxRuntime, '../lib/workbenchApi': api });
const resultAst = ts.createSourceFile('ResultWorkbench.tsx', resultSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
function expression(name) {
  const nodes = [];
  const walk = node => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === name) nodes.push(node.initializer);
    ts.forEachChild(node, walk);
  };
  walk(resultAst); assert.equal(nodes.length, 1, `Actual ${name} declaration must be unique`);
  return printer.printNode(ts.EmitHint.Expression, nodes[0], resultAst);
}
const draftFunctions = ['emptyDraft', 'hasEdit', 'hasUnsent', 'rowHasEdit'].map(name => `const ${name} = ${expression(name)};`).join('\n');
const withoutRow = resultAst.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'withoutRowEdits');
assert.ok(withoutRow);
const draft = load(`${draftFunctions}\n${printer.printNode(ts.EmitHint.Unspecified, withoutRow, resultAst)}
export { emptyDraft, hasEdit, hasUnsent, rowHasEdit, withoutRowEdits };
function gates(input) {
  const { owner, context, historical, state, processing, disabled, dirty, checksLoading, checksError, checks, revision } = input;
  const exportReady = ${expression('exportReady')};
  const canExport = ${expression('canExport')};
  const openChecks = unresolvedChecks(checks, revision);
  const canExportAnyway = ${expression('canExportAnyway')};
  return { canExport, canExportAnyway, openChecks };
}
export const canExport = input => gates(input).canExport;
export const canExportAnyway = input => gates(input).canExportAnyway;`, 'result-predicates.ts', { hasWorkbenchPreferenceChanges: api.hasWorkbenchPreferenceChanges, serverAllowsExport: api.serverAllowsExport, unresolvedChecks: api.unresolvedChecks });

function take(overrides = {}) {
  return { take_id: 'take-one', upload_id: 'upload-one', start: 10, end: 15, speaker_id: 'speaker-one',
    asr_text: '今天，我们来到这里。', score: .9, snr_db: null, precision: 'word',
    words: [{ w: '今天', s: 10.12, e: 10.9 }, { w: '我们', s: 11, e: 11.7 },
      { w: '来到', s: 12, e: 12.6 }, { w: '这里', s: 13, e: 14.8 }], ...overrides };
}
const person = () => ({ id: 'speaker-one', name: '', title: '', auto_label: '说话人一', appearances: 1, seconds: 5 });
const row = (overrides = {}) => ({ sentence_id: 0, sentence: '今天，我们来到这里。', shot_id: 2, thumb_url: null,
  description: 'Synthetic test source', duration: 5, confidence: .9, is_fallback: false,
  audio_kind: 'sync', spoken_text: '今天，我们来到这里。', visual_beats: [], replacement_instruction: null,
  kind: 'quote', source: take(), ...overrides });
const timing = (id, start, end) => ({ sentence_id: id, start, end, duration: end - start, text: 'fixture', audio_kind: 'sync', audio_source: 'sync' });
const check = (overrides = {}) => ({ key: 'literal-signature', code: 'QUOTE_MATCH_LOW', level: 1, message: '请听一下是不是这句。',
  sentence_id: 0, action: '去听', checked: false, ...overrides });
const gate = (overrides = {}) => ({ revision: 4, blocking_count: 0, pending_count: 1, passed: false, checks: [check()], ...overrides });
const passed = () => gate({ pending_count: 0, passed: true, checks: [check({ checked: true })] });
const editReceipt = () => ({ task_id: task, revision: 5, current_revision: 4, status: 'queued' });
const batch = overrides => ({ expected_revision: 4, keep_sentence_ids: [0, 1, 2, 3], edits: [], ...overrides });
function context(overrides = {}) {
  return { task_id: task, revision: 4, status: 'done', script: 'Test title\nTest body', preferences: { pacing: 'normal' }, last_operation_error: null,
    report: { task_id: task, rows: [row()], quality: null }, timings: [timing(0, 2, 7)], current_shots: [{ sentence_id: 0, shot_ids: [2] }],
    shots: [{ shot_id: 2, source_index: 0, source_scene_index: 0, start: 10, end: 15, duration: 5,
      status: 'available', media_origin: 'source', description: 'fixture', unused: false, used_by_sentence_id: 0, selectable: true, thumb_url: '' }],
    versions: [{ revision: 4, label: 'Fixture', created_at: '2026-09-28T00:00:00Z' }], ...overrides };
}
function job(overrides = {}) {
  return { id: 'b'.repeat(32), revision: 7, pipeline_revision: 4, state: 'queued', output_id: null, error: null,
    qc: 'fixture', disclosure: false, options: { ...api.DEFAULT_EXPORT }, ...overrides };
}

test('complete literal word evidence is read without mutating source or inventing clocks', () => {
  const source = take(), before = JSON.stringify(source), evidence = quote.quoteTrimEvidence(source);
  assert.equal(evidence.reason, ''); assert.equal(evidence.words, source.words);
  assert.equal(JSON.stringify(source), before);
  assert.deepEqual(plain(quote.quoteRangeForWords(source, 0, 3)), { start: 10, end: 15 });
  assert.deepEqual(plain(quote.quoteRangeForWords(source, 3, 1)), { start: 11, end: 15 });
  assert.deepEqual(plain(quote.quoteRangeForWords(source, 1, 2)), { start: 11, end: 12.6 });
});

test('segment or partial evidence disables only precise editing with an actionable reason', () => {
  for (const source of [null, take({ precision: 'segment' }), take({ words: [] }), take({ words: take().words.slice(1) }),
    take({ asr_text: '今天我们没有来到这里' }), take({ words: [...take().words].reverse() }),
    take({ words: [{ ...take().words[0], e: 11.2 }, ...take().words.slice(1)] }),
    take({ words: [{ ...take().words[0], s: 9 }, ...take().words.slice(1)] }),
    take({ words: take().words.map((w, i) => i === 3 ? { ...w, e: 16 } : w) })]) {
    const evidence = quote.quoteTrimEvidence(source);
    assert.notEqual(evidence.reason, ''); assert.equal(evidence.words.length, 0);
    assert.match(evidence.reason, /换一段|删除/);
  }
});

test('numeric and filler differences cannot become false literal timing evidence', () => {
  const words = [{ w: '我们', s: 0, e: 1 }, { w: '来了', s: 1, e: 2 }];
  assert.notEqual(quote.quoteTrimEvidence(take({ start: 0, end: 2, words, asr_text: '嗯我们来了' })).reason, '');
  for (const [actual, word] of [['-1.5', '1.5'], ['1.5', '15'], ['一百', '100'], ['-一', '一']]) {
    assert.notEqual(quote.quoteTrimEvidence(take({ start: 0, end: 2, asr_text: actual, words: [{ w: word, s: 0, e: 2 }] })).reason, '');
  }
});

test('all selected ranges are contiguous, inside original source, and use actual endpoint values', () => {
  for (let count = 1; count <= 24; count++) {
    const words = Array.from({ length: count }, (_, i) => ({ w: `词${i}`, s: 100 + i * 1.3 + .12, e: 100 + i * 1.3 + 1.1 }));
    const source = take({ start: 100, end: 100 + count * 1.3, asr_text: words.map(w => w.w).join('，'), words });
    for (let a = 0; a < count; a++) for (let b = 0; b < count; b++) {
      const selected = quote.quoteRangeForWords(source, a, b);
      assert.ok(selected); assert.ok(selected.start >= source.start && selected.end <= source.end);
      const retained = words.filter(w => w.s >= selected.start && w.e <= selected.end);
      assert.deepEqual(retained, words.slice(Math.min(a, b), Math.max(a, b) + 1));
    }
  }
  for (const [a, b] of [[-1, 0], [0, 10], [NaN, 1], [.5, 2]]) assert.equal(quote.quoteRangeForWords(take(), a, b), null);
});

test('trim validation refuses expansion, word splits, invalid numbers and sub-second clips', () => {
  const source = take();
  for (const range of [{ start: 9, end: 15 }, { start: 10, end: 16 }, { start: 10.5, end: 15 },
    { start: 11, end: 11.7 }, { start: 13, end: 12 }, { start: NaN, end: 15 }, { start: 10, end: Infinity }]) {
    assert.notEqual(quote.quoteRangeProblem(source, range), '');
  }
  assert.equal(quote.quoteRangeProblem(source, { start: 11, end: 12.6 }), '');
  const decimal = take({ start: 1.3, end: 2.3, asr_text: '好', words: [{ w: '好', s: 1.3, e: 2.3 }] });
  assert.equal(quote.quoteRangeProblem(decimal, { start: 1.3, end: 2.3 }), '', 'Exactly one second remains valid without rounding the payload');
});

test('micro-adjustments always snap to real endpoints and never expand the source or cut a word', () => {
  const source = take(), original = JSON.stringify(source);
  for (const edge of ['start', 'end']) for (const direction of [-1, 1]) {
    let selected = { start: 11, end: 14.8 };
    for (let step = 0; step < 20; step++) {
      const next = quote.nudgeQuoteRange(source, selected, edge, direction);
      assert.equal(quote.quoteRangeProblem(source, next), '');
      assert.ok(direction * (next[edge] - selected[edge]) >= 0);
      assert.equal(next[edge === 'start' ? 'end' : 'start'], selected[edge === 'start' ? 'end' : 'start']);
      selected = next;
    }
  }
  assert.equal(JSON.stringify(source), original);
});

test('malformed word lists keep a valid quote removable rather than synthesizing times', () => {
  for (const words of [undefined, null, ['not a cue'], [{ w: '今天', s: '10', e: 11 }]]) {
    const parsed = api.parseQuoteTake(take({ words }));
    assert.equal(parsed.words.length, 0);
    assert.notEqual(quote.quoteTrimEvidence(parsed).reason, '');
  }
  const parsed = api.parseReport({ task_id: task, rows: [row({ source: take({ precision: 'segment', words: [] }) })], quality: null }, task);
  assert.equal(parsed.rows[0].sentence_id, 0); assert.equal(parsed.rows[0].source.precision, 'segment');
});

test('invalid source identities and non-finite source clocks are never accepted', () => {
  for (const patch of [{ upload_id: '../outside' }, { take_id: '' }, { start: -1 }, { end: Infinity },
    { score: NaN }, { score: 1.1 }, { precision: 'estimated' }]) assert.throws(() => api.parseQuoteTake(take(patch)));
});

test('wave parsing requires real 20ms bounded normalized data and matching coverage', () => {
  const wave = { interval_ms: 20, start: 10, end: 11, samples: Array(50).fill(0) };
  assert.deepEqual(plain(api.parseQuoteWave(wave)), wave);
  for (const patch of [{ interval_ms: 10 }, { samples: [] }, { samples: Array(20).fill(.3) },
    { samples: Array(50).fill(NaN) }, { samples: Array(50).fill(1.1) }, { end: 9 }]) assert.throws(() => api.parseQuoteWave({ ...wave, ...patch }));
});

test('wave reduction preserves real silence and peak amplitude, not decorative bars', () => {
  const wave = { interval_ms: 20, start: 10, end: 30, samples: Array(1000).fill(0) };
  const silent = quote.quoteWaveBins(wave, 100);
  assert.equal(silent.length, 100); assert.ok(silent.every(bin => bin.amplitude === 0));
  wave.samples[431] = -.8;
  const reduced = quote.quoteWaveBins(wave, 100);
  assert.equal(Math.max(...reduced.map(bin => bin.amplitude)), .8);
  assert.equal(reduced[43].start, 18.6); assert.equal(reduced.at(-1).end, 30);
});

test('legacy context defaults mode without inferring quotes from old sync audio', () => {
  const legacyRow = row(); delete legacyRow.kind; delete legacyRow.source;
  const parsed = api.parseContext(context({ report: { task_id: task, rows: [legacyRow], quality: null } }), task);
  assert.equal(parsed.mode, 'voiceover'); assert.equal(parsed.speakers.length, 0);
  assert.equal(api.isQuoteRow(parsed.report.rows[0], parsed.mode), false);
  assert.equal(api.isQuoteRow({ ...legacyRow, kind: 'quote' }, 'mixed'), true);
});

test('mode, speakers and server jumpcuts remain attached without inferring missing coverage', () => {
  const parsed = api.parseContext(context({ mode: 'original', speakers: [person()], jumpcuts: [{ after_row: 0, cover: 'zoom', shot: null, downgraded: true }], lower_thirds_burned: true }), task);
  assert.equal(parsed.mode, 'original'); assert.equal(parsed.jumpcuts[0].downgraded, true); assert.equal(parsed.lower_thirds_burned, true);
  assert.throws(() => api.parseContext(context({ mode: 'original', jumpcuts: [{ after_row: 0, cover: 'broll', shot: null, downgraded: false }] }), task));
  assert.throws(() => api.parseContext(context({ mode: 'mixed', report: { task_id: task, mode: 'original', rows: [row()], quality: null } }), task));
});

test('server checks must be well-formed; unknown or contradictory results fail closed', () => {
  for (const value of [null, {}, gate({ passed: 'true' }), gate({ passed: true }),
    gate({ checks: [check(), check()] }), gate({ checks: [check({ level: 3 })] })]) {
    assert.throws(() => api.parseChecks(value));
  }
  assert.equal(api.parseChecks(passed()).passed, true);
  assert.throws(() => api.parseChecks(gate({ pending_count: 0, passed: true })));
  assert.throws(() => api.parseChecks(passedWith([check({ level: 0, checked: true, confirmable: true })])));
});
function passedWith(checks) { return gate({ pending_count: 0, passed: true, checks }); }

test('only explicit generated-media consent is confirmable at level zero', () => {
  for (const code of ['FREEZE_PAD_EXCESSIVE', 'QUOTE_NOT_FOUND', 'unknown']) {
    assert.equal(api.canConfirmCheck(check({ code, level: 0, confirmable: true })), false);
    assert.throws(() => api.confirmationKeys(gate({ checks: [check({ code, level: 0, confirmable: true })] }), 'literal-signature', true));
  }
  assert.equal(api.canConfirmCheck(check({ code: 'generated_media', level: 0 })), false);
  assert.equal(api.canConfirmCheck(check({ code: 'generated_media', level: 0, confirmable: true })), true);
  assert.equal(api.canConfirmCheck(check({ level: 2, confirmable: true })), false);
  assert.equal(api.canConfirmCheck(check({ level: 1, confirmable: false })), false);
});

test('confirmation keys preserve other actual confirmations but exclude blockers and notices', () => {
  const checks = gate({ checks: [check(), check({ key: 'other', checked: true }),
    check({ key: 'note', level: 2, checked: true }), check({ key: 'hard', level: 0, checked: true })] });
  assert.deepEqual(plain(api.confirmationKeys(checks, 'literal-signature', true)), ['literal-signature', 'other']);
  assert.deepEqual(plain(api.confirmationKeys(checks, 'other', false)), []);
  assert.throws(() => api.confirmationKeys(checks, 'stale-signature', true));
});

test('actual export gate refuses unsaved, loading, historical, failed or unconfirmed state', () => {
  const ready = { owner: true, context: {}, historical: false, state: 'done', processing: false, disabled: false, dirty: false,
    checksLoading: false, checksError: '', checks: passed(), revision: 4 };
  assert.equal(draft.canExport(ready), true);
  for (const patch of [{ owner: false }, { context: null }, { historical: true }, { state: 'failed' }, { processing: true }, { disabled: true },
    { dirty: true }, { checksLoading: true }, { checksError: 'offline' }, { checks: null }, { checks: gate() },
    { revision: 5 }, { checks: gate({ pending_count: 0, checks: [] }) }]) {
    assert.equal(draft.canExport({ ...ready, ...patch }), false, JSON.stringify(patch));
  }
});

test('open checks no longer block exporting, but only with a fresh server-read list and no other blocker', () => {
  const ready = { owner: true, context: {}, historical: false, state: 'done', processing: false, disabled: false, dirty: false,
    checksLoading: false, checksError: '', checks: gate(), revision: 4 };
  assert.equal(draft.canExport(ready), false, 'the strict gate itself is unchanged');
  assert.equal(draft.canExportAnyway(ready), true, 'unconfirmed warnings: export is possible after acknowledging them');
  const blocking = gate({ blocking_count: 1, pending_count: 0, checks: [check({ level: 0, code: 'MATCH_FALLBACK' })] });
  assert.equal(draft.canExportAnyway({ ...ready, checks: blocking }), true, 'even a hard error can be exported past, once acknowledged');
  assert.equal(draft.canExportAnyway({ ...ready, checks: passed() }), false, 'nothing open: the normal path applies');
  // Everything that is not a check result still blocks exactly as before.
  for (const patch of [{ owner: false }, { context: null }, { historical: true }, { state: 'failed' }, { processing: true }, { disabled: true },
    { dirty: true }, { checksLoading: true }, { checksError: 'offline' }, { checks: null }, { revision: 5 }]) {
    assert.equal(draft.canExportAnyway({ ...ready, ...patch }), false, JSON.stringify(patch));
  }
});

test('export request carries the acknowledgement only when asked; the receipt reports what was open', () => {
  const options = { ...api.DEFAULT_EXPORT };
  assert.equal('acknowledge_unresolved' in api.simpleExportBody(options), false);
  assert.equal('acknowledge_unresolved' in api.simpleExportBody(options, false), false);
  assert.equal(api.simpleExportBody(options, true).acknowledge_unresolved, true);
  const id = 'a'.repeat(32);
  const receipt = { export_id: id, revision: 4, state: 'queued', output_id: null, error: null,
    options: { fmt: 'mp4', aspect: '16:9', res: '1080p', sub: 'std', expected_revision: 4 } };
  assert.equal(api.parseJob(receipt, options).unresolved, undefined);
  assert.deepEqual(plain(api.parseJob({ ...receipt, unresolved: { blocking: 1, pending: 2, qc_blockers: true } }, options).unresolved),
    { blocking: 1, pending: 2, qc_blockers: true });
  for (const bad of [{ blocking: '1', pending: 0, qc_blockers: false }, { blocking: 1, pending: 0 }, { blocking: -1, pending: 0, qc_blockers: false }, 'x', null]) {
    assert.equal(api.parseJob({ ...receipt, unresolved: bad }, options).unresolved, undefined, JSON.stringify(bad));
  }
});

test('the acknowledgement only ever covers the exact list of open checks that was shown', () => {
  const a = check({ key: 'a' }), b = check({ key: 'b' }), c = check({ key: 'c', checked: true }), info = check({ key: 'i', level: 2 });
  const open = api.unresolvedChecks(gate({ checks: [b, a, c, info] }), 4);
  assert.deepEqual(plain(open.map(item => item.key)), ['b', 'a'], 'confirmed and informational items are not open');
  assert.equal(api.unresolvedSignature(open), 'a|b');
  assert.equal(api.unresolvedSignature(api.unresolvedChecks(gate({ checks: [a, b] }), 4)), 'a|b', 'order does not matter');
  assert.notEqual(api.unresolvedSignature(api.unresolvedChecks(gate({ checks: [a, b, check({ key: 'new' })] }), 4)), 'a|b', 'a new open check invalidates it');
  assert.deepEqual(plain(api.unresolvedChecks(gate(), 5)), [], 'another revision has no usable list');
  assert.deepEqual(plain(api.unresolvedChecks(null, 4)), []);
});

test('each new quote/speaker draft participates in actual unsaved navigation and submission guards', () => {
  for (const patch of [{ pendTrim: { 0: { start: 11, end: 15 } } }, { pendTake: { 0: take() } }, { pendToNarration: [0] },
    { pendSpeakers: { 'speaker-one': { name: '记者', title: '采访' } } }]) {
    const current = { ...draft.emptyDraft(), ...patch, base: 4 };
    assert.equal(draft.hasEdit(current), true); assert.equal(draft.hasUnsent(current), true);
    assert.equal(draft.hasUnsent({ ...current, submitted: 5 }), false);
    assert.equal(draft.hasUnsent({ ...current, submitted: undefined }), true);
  }
  assert.equal(draft.hasEdit(draft.emptyDraft()), false);
});

test('deleting or reverting a row atomically removes conflicting edits but keeps other rows and speakers', () => {
  const current = { ...draft.emptyDraft(), sentences: { 0: { text: 'old' }, 1: { text: 'other' } },
    pendTrim: { 0: { start: 11, end: 15 } }, pendTake: { 0: take() }, pendToNarration: [0, 2],
    pendSpeakers: { 'speaker-one': { name: '记者', title: '采访' } } };
  const before = JSON.stringify(current), next = draft.withoutRowEdits(current, 0);
  assert.equal(draft.rowHasEdit(next, 0), false); assert.equal(draft.rowHasEdit(next, 1), true);
  assert.equal(draft.rowHasEdit(next, 2), true); assert.deepEqual(plain(next.pendSpeakers), current.pendSpeakers);
  assert.equal(JSON.stringify(current), before);
});

test('batch validation forbids all quote edit/take/trim/conversion overlaps and deleted-row edits', () => {
  const operations = [{ edits: [{ sentence_id: 0, text: 'new' }] }, { quote_trims: [{ id: 0, start: 11, end: 15 }] },
    { quote_takes: [{ id: 0, take_id: 'take-two' }] }, { to_narration: [0] }];
  for (let a = 0; a < operations.length; a++) for (let b = a + 1; b < operations.length; b++) {
    assert.throws(() => api.validateEditBatch(batch({ ...operations[a], ...operations[b] }), 'mixed'));
  }
  for (const operation of operations) assert.throws(() => api.validateEditBatch(batch({ ...operation, keep_sentence_ids: [1, 2] }), 'mixed'));
  assert.throws(() => api.validateEditBatch(batch({ keep_sentence_ids: [] }), 'mixed'));
});

test('original mode cannot submit narration, shot swaps, recordings, conversion or pacing', () => {
  for (const patch of [{ edits: [{ sentence_id: 0, text: 'new' }] }, { edits: [{ sentence_id: 0, shot_id: 10 }] },
    { edits: [{ sentence_id: 0, recording_id: 'b'.repeat(32) }] }, { to_narration: [0] }, { pacing: 'slow' }]) {
    assert.throws(() => api.validateEditBatch(batch(patch), 'original'));
  }
  api.validateEditBatch(batch({ quote_trims: [{ id: 0, start: 11, end: 15 }], speakers: [person()], enhance_speech: false }), 'original');
});

test('one mode-aware remix sends only flat server-owned selections and explicit revision', async () => {
  const calls = [], controller = new AbortController(), client = api.createWorkbenchApi(task, async (path, init) => {
    calls.push({ path, method: init.method, body: JSON.parse(init.body) });
    assert.equal(init.signal, controller.signal); return editReceipt();
  });
  const current = batch({ edits: [{ sentence_id: 3, text: 'new narration' }], quote_trims: [{ id: 0, start: 11, end: 15 }],
    quote_takes: [{ id: 1, take_id: 'take-two' }], to_narration: [2], speakers: [person()], caption_style: 'none', enhance_speech: false });
  const before = JSON.stringify(current);
  assert.equal((await client.remix(current, 'mixed', controller.signal)).revision, 5);
  assert.deepEqual(calls, [{ path: `${root}/remix`, method: 'POST', body: current }]);
  assert.equal(JSON.stringify(current), before);
  assert.equal(Object.hasOwn(calls[0].body, 'preferences'), false);
});

test('a lost or failed remix never retries writes or changes the retained caller draft', async () => {
  for (const status of [409, 500, 503]) {
    let calls = 0; const error = Object.assign(new Error('fixture failure'), { status });
    const client = api.createWorkbenchApi(task, async () => { calls++; throw error; });
    const current = batch({ quote_trims: [{ id: 0, start: 11, end: 15 }] }), before = JSON.stringify(current);
    await assert.rejects(client.remix(current, 'original'), e => e === error);
    assert.equal(calls, 1); assert.equal(JSON.stringify(current), before);
  }
});

test('receipt needs the current and next expected revision before a pending draft is considered submitted', async () => {
  for (const patch of [{ revision: 4 }, { revision: 6 }, { current_revision: 3 }, { task_id: 'other' }, { status: 'done' }]) {
    let calls = 0; const client = api.createWorkbenchApi(task, async () => { calls++; return { ...editReceipt(), ...patch }; });
    await assert.rejects(client.remix(batch(), 'mixed')); assert.equal(calls, 1);
  }
});

test('explicit confirmations PUT only expected_revision and checked_keys', async () => {
  const calls = [], controller = new AbortController(), client = api.createWorkbenchApi(task, async (path, init) => {
    calls.push({ path, method: init.method ?? 'GET', body: init.body && JSON.parse(init.body) });
    assert.equal(init.signal, controller.signal);
    if (!init.method) { assert.equal(init.cache, 'no-store'); return gate(); }
    return passed();
  });
  const previous = await client.checks(controller.signal);
  assert.equal(previous.passed, false);
  const next = await client.confirmCheck(previous, 'literal-signature', true, controller.signal);
  assert.equal(next.passed, true);
  assert.deepEqual(calls[1], { path: `${root}/checks`, method: 'PUT', body: { expected_revision: 4, checked_keys: ['literal-signature'] } });
  assert.equal(previous.checks[0].checked, false, 'No optimistic mutation of server confirmation');
});

test('failed checks or unacknowledged confirmations never fabricate pass or retry', async () => {
  for (const response of [gate(), { ...passed(), revision: 5 }, null]) {
    let attempts = 0; const client = api.createWorkbenchApi(task, async () => { attempts++; return response; });
    await assert.rejects(client.confirmCheck(gate(), 'literal-signature', true)); assert.equal(attempts, 1);
  }
  let attempts = 0; const client = api.createWorkbenchApi(task, async () => { attempts++; throw new Error('offline'); });
  await assert.rejects(client.checks()); assert.equal(attempts, 1);
});

test('hard blockers cannot issue even one confirmation request', async () => {
  let attempts = 0; const client = api.createWorkbenchApi(task, async () => { attempts++; return passed(); });
  await assert.rejects(client.confirmCheck(gate({ checks: [check({ level: 0, code: 'QUOTE_NOT_FOUND', confirmable: true })] }), 'literal-signature', true));
  assert.equal(attempts, 0);
});

test('quote candidate and waveform APIs use read-only task-scoped paths and abort signal', async () => {
  const calls = [], controller = new AbortController(), wave = { interval_ms: 20, samples: Array(250).fill(0), start: 10, end: 15 };
  const client = api.createWorkbenchApi(task, async (path, init) => {
    calls.push(path); assert.equal(init.method, undefined); assert.equal(init.cache, 'no-store'); assert.equal(init.signal, controller.signal);
    return path.endsWith('/takes') ? { takes: [take()] } : wave;
  });
  assert.equal((await client.takes(0, controller.signal))[0].take_id, 'take-one');
  assert.deepEqual(plain(await client.wave(0, controller.signal)), wave);
  assert.deepEqual(calls, [`${root}/quotes/0/takes`, `${root}/waves/0.json`]);
  await assert.rejects(client.takes('../outside')); assert.equal(calls.length, 2);
});

test('simple export mapping exposes only options actually transmitted to the new endpoint', async () => {
  const calls = [], client = api.createWorkbenchApi(task, async (path, init) => { calls.push({ path, body: JSON.parse(init.body) }); return job(); });
  for (const subtitles of ['standard', 'large', 'none']) {
    await client.export({ ...api.DEFAULT_EXPORT, resolution: 720, aspect: '9:16', subtitles }, 4);
    assert.deepEqual(calls.at(-1), { path: `${root}/exports`, body: { fmt: 'mp4', aspect: '9:16', res: '720p', sub: { standard: 'std', large: 'big', none: 'none' }[subtitles], expected_revision: 4 } });
  }
  assert.deepEqual(plain(api.simpleExportBody({ ...api.DEFAULT_EXPORT, format: 'mp3', resolution: 360, aspect: '1:1', subtitles: 'none' })),
    { fmt: 'mp3', aspect: '16:9', res: '1080p', sub: 'std' });
  // PNG is now supported only with an explicit frame clock; no invented frame.
  await assert.rejects(client.export({ ...api.DEFAULT_EXPORT, format: 'png' }, 4)); assert.equal(calls.length, 3);
});

test('legacy export receipts retain identity while polling and cancellation use exports routes', async () => {
  const calls = [], id = 'b'.repeat(32), exportId = 'c'.repeat(32);
  const client = api.createWorkbenchApi(task, async (path, init) => { calls.push({ path, method: init.method ?? 'GET' }); return job({ export_id: exportId }); });
  const current = await client.job(exportId); assert.equal(current.id, id);
  await client.cancelExport(current.id);
  assert.deepEqual(calls, [{ path: `${root}/exports/${exportId}`, method: 'GET' }, { path: `${root}/exports/${id}`, method: 'DELETE' }]);
  const wrong = api.createWorkbenchApi(task, async () => job()); await assert.rejects(wrong.job(exportId));
});

test('failed export request is not retried through the old export adapter', async () => {
  const paths = [], client = api.createWorkbenchApi(task, async path => { paths.push(path); throw new Error('fixture 503'); });
  await assert.rejects(client.export(api.DEFAULT_EXPORT, 4)); assert.deepEqual(paths, [`${root}/exports`]);
});

test('speaker cues use final-film clocks, last appearance gap and a maximum of 2.5 seconds', () => {
  const rows = [0, 1, 2, 3].map(id => row({ sentence_id: id }));
  const times = [timing(0, 2, 7), timing(1, 20, 23), timing(2, 82.99, 85), timing(3, 145, 150)];
  const cues = quoteOnlyCues(rows, times);
  assert.deepEqual(cues, [{ speaker_id: 'speaker-one', sentence_id: 0, start: 2, end: 4.5 }, { speaker_id: 'speaker-one', sentence_id: 3, start: 145, end: 147.5 }]);
  assert.equal(cues.some(c => c.start === rows[0].source.start), false, 'Source time 10 is not a final-film cue');
  assert.equal(api.speakerCues(rows, [], 'original').length, 0, 'Missing final timings cannot be replaced by summed durations');
});
function quoteOnlyCues(rows, timings) { return plain(api.speakerCues(rows, timings, 'original')); }

test('short quotes, narration and custom recordings cannot show invented speaker overlays', () => {
  const rows = [row(), row({ sentence_id: 1, kind: 'narration' }), row({ sentence_id: 2 })];
  const times = [timing(0, 2, 3.2), timing(1, 80, 90), { ...timing(2, 100, 103), audio_source: 'recording' }];
  assert.deepEqual(quoteOnlyCues(rows, times), [{ speaker_id: 'speaker-one', sentence_id: 0, start: 2, end: 3.2 }]);
  // v2 relies on burned server overlays; it must not render another DOM cue.
  const overlayIdentifiers = [];
  const visit = node => { if (ts.isIdentifier(node) && ['showLowerThird', 'activeSpeakerCue', 'speakerCues'].includes(node.text)) overlayIdentifiers.push(node.text); ts.forEachChild(node, visit); };
  visit(resultAst); assert.deepEqual(overlayIdentifiers, []);
});

function renderQuote(mode, source = take(), overrides = {}) {
  const never = () => { throw new Error('No API call is allowed during SSR'); };
  return renderToStaticMarkup(React.createElement(quote.default, {
    row: row({ source }), revision: 4, mode, disabled: false, api: { takes: never, wave: never },
    toNarration: false, speakers: [person()], pendingSpeakers: {}, onTrim: never, onTake: never, onToNarration: never,
    onSpeaker: never, onListen: never, ...overrides,
  }));
}

test('static original quote panel has no text editing, audio upload, recording, shot swap or conversion', () => {
  const html = renderQuote('original');
  assert.doesNotMatch(html, /<textarea|type="file"|type="number"|录制我的配音|直接选镜|改成旁白（AI 读）/);
  assert.match(html, /data-quote-word/); assert.match(html, /只能剪短，不能改字或重录/);
  assert.match(html, /data-quote-speaker-name/);
});

test('static mixed quote panel has a separate pending conversion action, not a narration editor', () => {
  const html = renderQuote('mixed', take(), { toNarration: true });
  assert.match(html, /撤销改成旁白/); assert.match(html, /本次只记下转为旁白/);
  assert.doesNotMatch(html, /<textarea|录制我的配音|直接选镜/);
});

test('incomplete words remain visible as actual transcript, without a fake waveform or precise buttons', () => {
  const html = renderQuote('original', take({ words: [] }));
  assert.match(html, /今天，我们来到这里/); assert.match(html, /不能精确剪短/);
  assert.doesNotMatch(html, /data-quote-word=|<svg|记下剪短/);
  assert.match(html, /换一段/); assert.match(html, /听这句原声/);
});

test('server speaker identifiers never inherit an invented name from object prototypes', () => {
  const source = take({ speaker_id: 'constructor' });
  const html = renderQuote('mixed', source, { speakers: [{ ...person(), id: 'constructor' }] });
  assert.doesNotMatch(html, /value="Object"/);
});

test('actual editor and UI styles stay syntactically valid without full build or output writes', () => {
  const pending = load(read('../src/lib/pendingEdits.ts'), 'pendingEdits.ts', {}, { './workbenchApi': api });
  const icon = load(read('../src/components/ui/Icon.tsx'), 'Icon.tsx', {}, { 'react/jsx-runtime': jsxRuntime, './Primitives.module.css': { default: {} } });
  load(resultSource, 'ResultWorkbench.tsx', {}, {
    react: React, 'react/jsx-runtime': jsxRuntime, '../lib/workbenchApi': api,
    '../lib/productionModes': modes, '../lib/appApi': appApi, '../lib/qualitySummary': quality,
    './QuoteEditor': { __esModule: true, ...quote }, '../styles/modes.css': {}, './ResultWorkbench.css': {},
    '../lib/pendingEdits': pending, './ui/Icon': icon,
  });
  assert.match(cssSource, /minmax\(0, 1\.6fr\) minmax\(320px, 1fr\)/);
  assert.match(cssSource, /max-width: 1440px/);
  assert.doesNotMatch(quoteSource, /Math\.random|Intl\.Segmenter|localStorage|sessionStorage/);
  assert.doesNotMatch(resultSource, /from ['"]lucide/);
});

test('a saved trim bounds the next edit and uses the actual current spoken transcript', () => {
  const saved = row({ trim: { start: 11, end: 14.8 }, spoken_text: '我们来到这里。' }), before = JSON.stringify(saved);
  const current = quote.currentQuoteSource(saved);
  assert.equal(current.start, 11); assert.equal(current.end, 14.8);
  assert.equal(current.asr_text, saved.spoken_text); assert.equal(quote.quoteTrimEvidence(current).reason, '');
  assert.deepEqual(plain(current.words), saved.source.words.slice(1));
  assert.deepEqual(plain(quote.quoteRangeForWords(current, 0, 2)), saved.trim);
  assert.notEqual(quote.quoteRangeProblem(current, { start: 10, end: 15 }), '');
  assert.equal(quote.nudgeQuoteRange(current, saved.trim, 'start', -1), saved.trim);
  assert.equal(JSON.stringify(saved), before, 'Immutable original evidence is retained');
});

test('saved trims with missing transcript, split words or invalid bounds never regain precision', () => {
  for (const saved of [row({ trim: { start: 11, end: 14.8 }, spoken_text: null }),
    row({ trim: { start: 11.2, end: 14.8 }, spoken_text: '来到这里' }), row({ trim: { start: 9, end: 15 } })]) {
    assert.notEqual(quote.quoteTrimEvidence(quote.currentQuoteSource(saved)).reason, '');
  }
});

test('explicit VM bindings reject unknown and inherited imports instead of supplying missing logic', () => {
  for (const dependency of ['../lib/not-registered', 'toString', './unknown.css', 'node:http']) {
    assert.throws(() => load(`import value from ${JSON.stringify(dependency)}; export default value;`,
      'unknown-import.ts'), e => e.message === `Unexpected dependency: ${dependency}`);
  }
  assert.throws(() => load(read('../src/lib/qualitySummary.ts'), 'qualitySummary.ts', {}, { './workbenchApi': api }),
    /Unexpected dependency: \.\/productionModes/);
  assert.equal(appApi.goneReason(new appApi.AppApiError('synthetic', 410, undefined, 'task_gone')), 'expired');
  assert.equal(appApi.goneReason(new appApi.AppApiError('synthetic', 410)), 'inaccessible');
  assert.equal(appApi.goneReason({ status: 410, code: 'task_gone' }), 'network');
  assert.equal(networkAttempts, 0);
});