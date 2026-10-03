import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Actual estimator and wizard estimate/JSX declarations, compiled in memory.
// Synthetic receipts only; no network, media, timers, storage or server work.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const denied = () => assert.fail('Unexpected estimate side effect');
const compile = (source, fileName = 'estimate.tsx') => {
  const result = ts.transpileModule(source, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
  return result.outputText;
};
const run = (source, globals = {}) => {
  const module = { exports: {} };
  const context = { module, exports: module.exports, fetch: denied, XMLHttpRequest: denied,
    setTimeout: denied, setInterval: denied, localStorage: { getItem: denied, setItem: denied },
    navigator: {}, require: denied, ...globals };
  vm.runInNewContext(compile(source), context, { timeout: 1000 });
  return module.exports;
};
const media = run(read('../src/lib/mediaInput.ts'));
const wizardSource = read('../src/components/CreateWizard.tsx');
const wizardAst = ts.createSourceFile('CreateWizard.tsx', wizardSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declarations = new Map(); let sufficiencySection;
function visit(node) {
  if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)) declarations.set(node.name.text, node);
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(wizardAst) === 'section'
    && node.openingElement.attributes.properties.some(p => ts.isJsxAttribute(p) && p.name.text === 'className'
      && p.initializer?.text === 'gm-sufficiency')) sufficiencySection = node.getText(wizardAst);
  ts.forEachChild(node, visit);
}
visit(wizardAst);
assert.ok(declarations.has('estimate')); assert.ok(sufficiencySection);
// Before the patch there is no speechByFile declaration: run the original
// estimate as-is so baseline failures are policy assertions, not loader errors.
const estimateCode = wizardSource.slice(
  (declarations.get('speechByFile') ?? declarations.get('estimate')).parent.parent.getStart(wizardAst),
  declarations.get('estimate').parent.parent.end);
const TASK = 'synthetic_d06', UP = 'up_d06', FID = 'file_d06', TOKEN = 'synthetic_d06_token'.padEnd(48, 'x');
const meta = (patch = {}) => {
  const item = { name: 'source.mp4', size: 3, lastModified: 123, type: 'video/mp4', kind: 'video',
    duration: 60, trim_start: 0, trim_end: 60, status: 'ready', note: '', ...patch };
  return { ...item, id: media.fileIdentity(item) };
};
const binding = item => ({ file_id: item.id, upload_id: UP, sha256: 'a'.repeat(64), chunk_size: 8388608,
  draftTaskId: TASK, server_file_id: FID, put_url: `/api/tasks/${TASK}/files/${FID}/chunks` });
const receipt = item => ({ id: UP, name: item.name, bytes: item.size, sec: item.duration, status: 'ready',
  has_speech: true, probe_ok: true, can_materialize: true, sha256: 'a'.repeat(64), transcript: [],
  thumb_url: null, wave_url: null, asr_confidence: null, chunks: [0], progress: 100 });
function state(item = meta()) {
  return { files: [item], bindings: [binding(item)], uploads: { [UP]: receipt(item) },
    uploadTokens: { [UP]: TOKEN }, draftAccess: { draftTaskId: TASK, draftTaskToken: TOKEN },
    draftChecking: false, draftReadError: '', uploadErrors: {},
    narrationRows: Array.from({ length: 10 }, () => ({ text: 'a' })), cpm: 255 };
}
const plain = value => JSON.parse(JSON.stringify(value));
function wizardEstimate(s) {
  const before = plain(s);
  const result = run(`${estimateCode}\nexports.result = { estimate, speechByFile: typeof speechByFile === 'undefined' ? undefined : speechByFile };`,
    { ...media, ...s, bindingFor: id => s.bindings.find(b => b.file_id === id),
      uploadFor: id => s.uploads[s.bindings.find(b => b.file_id === id)?.upload_id] }).result;
  assert.deepEqual(plain(s), before, 'estimate must not clear/mutate selections, receipts, or pending state');
  return result;
}
const estimate = (items, speech = new Map(), sentences = 10, chars = 10) => media.estimateSufficiency(items, sentences, chars, 255, speech);

for (const [duration, shots] of [[1, 1], [11, 1], [12, 1], [23, 1], [24, 2], [35, 2], [36, 3], [60, 5]]) {
  test(`ordinary video ${duration}s uses floor(duration/12), minimum one`, () => {
    const item = meta({ duration, trim_end: duration });
    assert.equal(estimate([item], new Map([[item.id, false]])).possibleShots, shots);
  });
}
test('each file is floored separately; image is one shot and three seconds', () => {
  const a = meta({ duration: 23, trim_end: 23 }), b = meta({ name: 'other.mp4', duration: 23, trim_end: 23 });
  const image = meta({ name: 'photo.png', kind: 'image', duration: 3, trim_end: null });
  const result = estimate([a, b, image], new Map([[a.id, false], [b.id, false]]));
  assert.equal(result.possibleShots, 3); assert.equal(result.seconds, 49);
});
test('known speech counts one shot even in a long video; local ad-hoc flag is ignored', () => {
  const item = meta({ has_speech: true });
  assert.equal(estimate([item], new Map([[item.id, true]])).possibleShots, 1);
  assert.equal(estimate([item]).possibleShots, 5);
  assert.equal(estimate([item]).unknownSpeechVideos, 1);
});
test('strict true only, unknown remains distinct from known false', () => {
  const item = meta();
  for (const value of [null, undefined, 'true', 1]) {
    const result = estimate([item], new Map([[item.id, value]]));
    assert.equal(result.possibleShots, 5); assert.equal(result.unknownSpeechVideos, 1);
  }
  const result = estimate([item], new Map([[item.id, false]]));
  assert.equal(result.possibleShots, 5); assert.equal(result.unknownSpeechVideos, 0);
});
test('trimmed interval, narration duration, ratio, rounding and cap retain their contracts', () => {
  const item = meta({ trim_start: 12, trim_end: 36 });
  const result = estimate([item], new Map([[item.id, false]]), 4, 51);
  assert.equal(result.possibleShots, 2); assert.equal(result.seconds, 24);
  assert.equal(result.narrationSeconds, 12); assert.equal(result.percent, 50); assert.equal(result.enough, false);
  assert.equal(estimate([item], new Map([[item.id, true]]), 4, 51).percent, 25);
  assert.equal(estimate([item], new Map([[item.id, false]]), 1, 510).percent, 20);
  assert.equal(estimate([item], new Map([[item.id, false]]), 1, 1).percent, 100);
  assert.equal(estimate([item], new Map([[item.id, false]]), 1, 1).enough, true);
});
test('invalid trims and nonready files supply neither shots nor unknown-speech counts', () => {
  const items = [meta({ status: 'loading' }), meta({ status: 'error' }), meta({ status: 'reselect' }),
    meta({ trim_start: 10, trim_end: 10 }), meta({ trim_end: 61 }), meta({ duration: null }), meta({ trim_start: 0.5 })];
  const result = estimate(items);
  assert.equal(result.possibleShots, 0); assert.equal(result.seconds, 0); assert.equal(result.unknownSpeechVideos, 0);
  assert.equal(result.enough, false); assert.equal(result.percent, 0);
});
test('wizard uses current task-scoped ready speech receipt, not a local file flag', () => {
  const s = state(meta({ has_speech: false }));
  const result = wizardEstimate(s);
  assert.equal(result.estimate.possibleShots, 1); assert.equal(result.estimate.unknownSpeechVideos, 0);
  assert.equal(result.speechByFile.get(s.files[0].id), true);
});
test('wizard re-evaluates receipt changes without files changing or triggering work', () => {
  const s = state(), files = s.files;
  s.uploads = {}; assert.equal(wizardEstimate(s).estimate.unknownSpeechVideos, 1);
  s.uploads = { [UP]: receipt(files[0]) }; assert.equal(wizardEstimate(s).estimate.possibleShots, 1);
  s.uploads = { [UP]: { ...receipt(files[0]), has_speech: false } };
  assert.equal(wizardEstimate(s).estimate.possibleShots, 5); assert.equal(wizardEstimate(s).estimate.unknownSpeechVideos, 0);
  s.uploads = { [UP]: { ...receipt(files[0]), has_speech: null } };
  assert.equal(wizardEstimate(s).estimate.unknownSpeechVideos, 1); assert.equal(s.files, files);
});
const invalid = {
  'missing binding': s => { s.bindings = []; },
  'removed selection': s => { s.files = [meta({ name: 'new.mp4' })]; },
  'changed lastModified': s => { s.files = [meta({ lastModified: 124 })]; },
  'forged stable id': s => { s.files[0].lastModified++; },
  'different attached File identity': s => { s.files[0].file = { name: 'different.mp4', size: 3, lastModified: 123 }; },
  'foreign task': s => { s.bindings[0].draftTaskId = 'foreign'; },
  'unscoped legacy receipt': s => { delete s.bindings[0].draftTaskId; delete s.bindings[0].server_file_id; },
  'missing server file id': s => { delete s.bindings[0].server_file_id; },
  'missing access': s => { s.draftAccess = undefined; },
  'wrong capability': s => { s.uploadTokens[UP] = 'other'.padEnd(48, 'x'); },
  'missing capability': s => { s.uploadTokens = {}; },
  'wrong upload id': s => { s.uploads[UP].id = 'foreign'; },
  'wrong receipt name': s => { s.uploads[UP].name = 'other.mp4'; },
  'wrong receipt bytes': s => { s.uploads[UP].bytes++; },
  'wrong receipt hash': s => { s.uploads[UP].sha256 = 'b'.repeat(64); },
  'wrong receipt duration': s => { s.uploads[UP].sec++; },
  'probe not confirmed': s => { s.uploads[UP].probe_ok = false; },
  'failed ASR with stale speech': s => { Object.assign(s.uploads[UP], { status: 'failed', failure_kind: 'asr', can_materialize: true }); },
  'transcribing': s => { s.uploads[UP].status = 'transcribing'; },
  'probe-only': s => { s.uploads[UP].status = 'uploading'; },
  'draft refresh pending': s => { s.draftChecking = true; },
  'observed expired draft': s => { s.draftReadError = 'expired'; },
  'observed expired upload/read failure': s => { s.uploadErrors[s.files[0].id] = 'expired'; },
};
for (const [name, mutate] of Object.entries(invalid)) test(`${name} cannot supply known speech`, () => {
  const s = state(); mutate(s);
  const result = wizardEstimate(s);
  assert.equal(result.estimate.possibleShots, 5);
  assert.equal(result.estimate.unknownSpeechVideos, 1);
  assert.notEqual(result.speechByFile?.get(s.files[0].id), true);
});
test('optional public digest may be absent; validated binding still supplies speech', () => {
  const s = state(); delete s.uploads[UP].sha256;
  assert.equal(wizardEstimate(s).estimate.possibleShots, 1);
});
test('receipt association survives reorder and does not leak to an unrelated file', () => {
  const s = state(), other = meta({ name: 'other.mp4' });
  s.files = [other, s.files[0]];
  assert.equal(wizardEstimate(s).estimate.possibleShots, 6);
  assert.equal(wizardEstimate(s).estimate.unknownSpeechVideos, 1);
  s.files.reverse(); assert.equal(wizardEstimate(s).estimate.possibleShots, 6);
});
test('unchanged sufficiency JSX renders new policy and truthful conditional unknown note', () => {
  const jsx = (type, props) => ({ type, props });
  const text = value => value == null || typeof value === 'boolean' ? '' : Array.isArray(value) ? value.map(text).join('')
    : typeof value === 'object' ? text(value.props?.children) : String(value);
  const render = (s, mode = 'voiceover') => text(run(`exports.tree = (${sufficiencySection});`, {
    require: name => { assert.equal(name, 'react/jsx-runtime'); return { jsx, jsxs: jsx }; },
    ...media, ...s, estimate: wizardEstimate(s).estimate, mode, quoteRows: [{ text: 'quote' }], quotesComplete: false, quoteSeconds: 0,
    sentListOpen: false, sentenceChecks: {}, sentenceKey: (text, index) => `${index}:${text}`,
    setSentenceChecks: denied, setSentListOpen: denied,
  }).tree);
  const s = state(), known = render(s);
  assert.match(known, /12 秒/); assert.match(known, /照片.*已确认有人声.*各.*1/);
  assert.doesNotMatch(known, /每 6 秒|人声状态未确认/);
  s.uploads = {}; const unknown = render(s, 'mixed');
  assert.match(unknown, /1 个视频人声状态未确认/); assert.match(unknown, /暂按普通视频估算/);
  assert.match(unknown, /不是镜头检测或内容匹配/); assert.match(unknown, /不能保证制作成功/);
  assert.match(unknown, /另有原声 1 段/);
});