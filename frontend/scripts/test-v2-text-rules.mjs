import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

// Actual shared module compiled in memory. No build, app, storage, server or providers.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const cases = JSON.parse(read('../../tests/cases/text-rules.json'));
const rules = JSON.parse(read('../../backend/mode_rules.json'));
const compiled = ts.transpileModule(read('../src/lib/productionModes.ts'), {
  reportDiagnostics: true, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
});
assert.equal(compiled.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
const module = { exports: {} };
vm.runInNewContext(compiled.outputText, { module, exports: module.exports, require(name) {
  assert.equal(name, '../../../backend/mode_rules.json'); return rules;
} }, { timeout: 1000 });
const pm = module.exports, plain = value => JSON.parse(JSON.stringify(value));
test('shared rules compile under strict ES2020 with the exact optional terminal union', () => {
  const path = fileURLToPath(new URL('../src/lib/productionModes.ts', import.meta.url));
  const options = { noEmit: true, strict: true, skipLibCheck: true, target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
    lib: ['lib.es2020.d.ts', 'lib.dom.d.ts', 'lib.dom.iterable.d.ts'],
    resolveJsonModule: true, esModuleInterop: true, types: [] };
  const program = ts.createProgram([path], options), errors = ts.getPreEmitDiagnostics(program);
  assert.deepEqual(errors.map(d => ts.flattenDiagnosticMessageText(d.messageText, '\n')), []);
  const checker = program.getTypeChecker(), source = program.getSourceFile(path);
  const sentence = source.statements.find(s => ts.isInterfaceDeclaration(s) && s.name.text === 'SentenceInput');
  const terminal = sentence.members.find(m => m.name?.getText(source) === 'terminal_punctuation');
  assert.ok(terminal.questionToken);
  const type = checker.getTypeAtLocation(terminal);
  assert.deepEqual(type.types.filter(t => t.isStringLiteral()).map(t => t.value).sort(), ['', '，', ',', '。', '.', ';', '；', '!', '?', '！', '？'].sort());
  assert.ok(type.types.some(t => t.flags & ts.TypeFlags.Undefined));
});
const handlers = {
  terminal: c => pm.parseModeScript(c.script, c.mode, c.overrides ?? {}).map(s => [s.text, s.terminal_punctuation]),
  normalization: c => pm.normalizeText(c.input), similarity: c => pm.similarity(c.left, c.right),
  split: c => pm.splitText(c.text, c.kind), parseLine: c => pm.parseLine(c.input),
  fillers: c => pm.stripLeadingFillers(c.input), facts: c => pm.factsOf(c.input),
  status: c => pm.scoreStatus(c.score, c.has_source ?? true), caption: c => pm.canonicalQuoteCaption(c.input),
  plan: c => pm.recommendPlan(c.operations, c.mode), samples: c => pm.sentenceNeeds(pm.parseModeScript(c.script, c.mode)),
};
for (const [group, handler] of Object.entries(handlers)) for (const [index, c] of cases[group].entries()) {
  test(`${group} shared golden ${index + 1}`, () => {
    if (c.error) assert.throws(() => handler(c));
    else assert.deepEqual(plain(handler(c)), c.expected);
  });
}
for (const c of cases.checks) test(`shared check ${c.code}`, () => {
  const definition = pm.checkDefinition(c.code);
  for (const [key, value] of Object.entries(c)) if (key !== 'code') assert.equal(definition[key], value);
});
test('v2 contract, spoken canonical, legacy input and no shared mutation', () => {
  assert.equal(pm.RULES_VERSION, 2); assert.equal(Object.keys(rules.checks).length, 17);
  assert.equal(pm.RULES_VERSION, cases.rules_version);
  for (const name of ['parseModeScript', 'splitText', 'parseLine', 'normalizeText', 'similarity', 'factsOf', 'sentenceNeeds', 'recommendPlan', 'checkDefinition']) assert.equal(typeof pm[name], 'function');
  assert.equal(pm.MODE_LIMITS.quote_snr_min_db, 18); assert.equal(pm.MODE_LIMITS.alternative_takes, 5);
  const legacy = { quote_caption: 'asr' };
  assert.equal(pm.readModePreferences(legacy).quote_caption, 'spoken');
  assert.deepEqual(legacy, { quote_caption: 'asr' });
  assert.equal(pm.defaultModePreferences().quote_caption, 'spoken');
  const definition = pm.checkDefinition('QUOTE_AUDIO_NOISY'); definition.message = 'changed';
  assert.notEqual(pm.checkDefinition('QUOTE_AUDIO_NOISY').message, 'changed');
});
test('manual sentence map changes kinds without splitting or losing text', () => {
  const script = '标题\n甲，乙。\n同期：丙，丁。';
  const rows = pm.parseModeScript(script, 'mixed', { 0: 'quote', 2: 'narration' });
  assert.deepEqual(plain(rows.map(r => r.text)), ['甲', '乙', '丙，丁']);
  assert.deepEqual(plain(rows.map(r => r.kind)), ['quote', 'narration', 'narration']);
  assert.deepEqual(plain(rows.map(r => r.terminal_punctuation)), ['，', '。', '。']);
  assert.equal(pm.sentencesMatchScript(script, rows, 'mixed'), true);
});
test('terminal metadata is strict, legacy unknown, read and script roundtrip preserve source hints', () => {
  const legacy = { idx: 0, text: '原文。', kind: 'quote', source_hint: { upload_id: 'u', seg_id: 's' }, speaker_hint: '王红（主办方）' };
  const before = JSON.stringify(legacy);
  assert.equal(pm.readSentenceInputs([legacy])[0].terminal_punctuation, '');
  for (const mark of ['', '，', ',', '。', '.', ';', '；', '!', '?', '！', '？']) {
    const row = { ...legacy, terminal_punctuation: mark };
    assert.deepEqual(plain(pm.readSentenceInputs([row])), [row]);
  }
  for (const mark of [undefined, null, 0, false, '。！', ' ', 'x', '、', '…', '）']) {
    assert.throws(() => pm.readSentenceInputs([{ ...legacy, terminal_punctuation: mark }]));
  }
  assert.equal(JSON.stringify(legacy), before);
  for (const c of cases.terminal.filter(c => !c.overrides)) {
    const rows = pm.parseModeScript(c.script, c.mode);
    const rebuilt = pm.scriptFromSentences('标题', rows, c.mode);
    assert.deepEqual(plain(pm.parseModeScript(rebuilt, c.mode)), plain(rows));
  }
});
test('T1 keeps sixteen rows and source quotes; B keeps numeric claims and exact endings', () => {
  for (const c of cases.samples) assert.deepEqual(plain(pm.parseModeScript(c.script, c.mode).map(s => s.terminal_punctuation)), cases.sample_terminal[c.mode]);
  const a = pm.parseModeScript(cases.samples[0].script, 'voiceover');
  assert.equal(a.length, 16); assert.ok(a[1].text.includes('“金马贺岁，高新同驰”'));
  const b = pm.parseModeScript(cases.samples[1].script, 'mixed');
  assert.deepEqual(plain(b.filter(s => s.kind === 'quote').map(s => s.idx)), [3, 7, 8, 12, 15]);
  assert.ok(b[8].text.includes('两百多斤')); assert.ok(pm.factsOf(b[8].text).includes('数字'));
  assert.ok(b[15].text.includes('1月31号')); assert.equal(b[15].speaker_hint, '王红（市集主办方）');
});
test('reconcileManual keeps new source endings while carrying manual kinds and source hints', () => {
  const old = pm.parseModeScript('标题\n甲，乙。', 'mixed');
  old[0].source_hint = { upload_id: 'u', seg_id: 's' }; old[0].speaker_hint = 'S1';
  const parsed = pm.parseModeScript('标题\n甲。乙，', 'mixed'), before = JSON.stringify({ old, parsed });
  const rows = pm.reconcileManual(parsed, old, { 0: 'quote' }, 'mixed');
  assert.deepEqual(plain(rows.map(s => [s.text, s.kind, s.terminal_punctuation])), [['甲', 'quote', '。'], ['乙', 'narration', '，']]);
  assert.deepEqual(plain(rows[0].source_hint), { upload_id: 'u', seg_id: 's' }); assert.equal(rows[0].speaker_hint, 'S1');
  assert.deepEqual(plain(pm.readSentenceInputs(rows)), plain(rows));
  const changed = pm.reconcileManual(pm.parseModeScript('标题\n改文。乙，', 'mixed'), old, { 0: 'quote' }, 'mixed');
  assert.equal(changed[0].kind, 'narration'); assert.equal(changed[0].source_hint, undefined);
  assert.throws(() => pm.reconcileManual(parsed, old, { 0: 'bad' }, 'mixed'));
  assert.equal(JSON.stringify({ old, parsed }), before);
});
test('C sample has three factual rows without invented clocks or total duration', () => {
  const sample = cases.samples.find(c => c.mode === 'original');
  const rows = pm.parseModeScript(sample.script, sample.mode);
  assert.deepEqual(plain(rows.filter(row => pm.factsOf(row.text).length).map(row => row.idx)), [2, 4, 7]);
  assert.equal(rows.some(row => 'duration' in row), false);
});
test('C selection strips only known leading fillers, preserves raw ASR and exact source hint', () => {
  const segment = { id: 's', text: '嗯…就是希望大家过年都能吃上一口家乡味', speaker_id: 'S1', start: 30, end: 38, words: [] };
  const before = JSON.stringify(segment), rows = pm.selectTranscriptSentence([], 'upload', segment, true);
  assert.equal(rows[0].text, '希望大家过年都能吃上一口家乡味');
  assert.deepEqual(plain(rows[0].source_hint), { upload_id: 'upload', seg_id: 's' });
  assert.equal(JSON.stringify(segment), before);
  assert.equal(pm.selectTranscriptSentence(rows, 'upload', segment, true).length, 1);
  assert.equal(pm.selectTranscriptSentence(rows, 'upload', segment, false).length, 0);
});
test('high fuzzy score never bypasses C continuity; raw filler deletion remains blocked', () => {
  assert.equal(pm.quoteTextEvidence('嗯，啊', '嗯，啊').contiguous, false);
  for (const [text, actual] of [['abcd', 'cdab'], ['我们来了', '我们，然后来了']]) {
    assert.equal(pm.quoteTextEvidence(text, actual).contiguous, false);
    const source = { asr_text: actual };
    assert.notEqual(pm.quoteGate([{ idx: 0, kind: 'quote', text }], [{ idx: 0, score: 1, source }], 'original'), '');
  }
});
test('preview clocks retain reversals and exact .5 second boundary', () => {
  const sentences = [{idx: 0, kind: 'quote'}, {idx: 1, kind: 'quote'}];
  const count = start => pm.countPreviewJumpCuts(sentences, [{idx: 0, source: {upload_id: 'u', start: 0, end: 2}}, {idx: 1, source: {upload_id: 'u', start, end: 4}}]);
  assert.equal(count(2.5), 0); assert.equal(count(2.500001), 1); assert.equal(count(1.9), 1);
});