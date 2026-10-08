import assert from 'node:assert/strict';
import { Blob, File } from 'node:buffer';
import { createHash } from 'node:crypto';
import { getEventListeners } from 'node:events';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import postcss from 'postcss';

// Compile the actual TS/TSX only in memory. No source substitution, filesystem
// writes, real server, provider, browser storage or microphone. The upload tests
// inject ONLY the shared request boundary. Component tests use a deterministic
// hook/event adapter, NOT ReactDOM/browser/layout/CSRF acceptance evidence.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const rules = JSON.parse(read('../../backend/mode_rules.json'));
const sources = {
  modes: read('../src/lib/productionModes.ts'), uploads: read('../src/lib/uploadSessions.ts'),
  media: read('../src/lib/mediaInput.ts'), wizard: read('../src/components/CreateWizard.tsx'),
  icon: read('../src/components/ui/Icon.tsx'), draftTasks: read('../src/lib/draftTasks.ts'),
};
const compiled = Object.fromEntries(Object.entries(sources).map(([name, source]) => {
  const result = ts.transpileModule(source, { fileName: `${name}.${['wizard', 'icon'].includes(name) ? 'tsx' : 'ts'}`, reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
  assert.equal(result.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0, `${name} syntax`);
  return [name, new vm.Script(result.outputText, { filename: `actual-${name}.js` })];
}));
const plain = value => JSON.parse(JSON.stringify(value));
const signal = () => new AbortController().signal;
const abortError = error => error?.name === 'AbortError';
const forbidden = what => () => assert.fail(`Forbidden test side effect: ${what}`);
const nativeTimers = { setTimeout, clearTimeout };
function load(name, dependencies = {}, globals = {}) {
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, ...nativeTimers, Uint8Array, Uint32Array, DataView,
    Headers, URL, URLSearchParams, Blob, File, AbortController, DOMException, Error, console,
    fetch: forbidden('fetch'), localStorage: { getItem: forbidden('storage read'), setItem: forbidden('storage write') },
    WebSocket: forbidden('websocket'), EventSource: forbidden('event stream'), ...globals,
    require: name => {
      assert.ok(Object.hasOwn(dependencies, name), `Unexpected runtime dependency: ${name}`);
      return dependencies[name];
    } });
  compiled[name].runInContext(context, { timeout: 1000 });
  return module.exports;
}
const modes = load('modes', { '../../../backend/mode_rules.json': rules });
const media = load('media');
const UPLOAD = 'synthetic-upload_1', OTHER = 'synthetic-upload_2';
const TOKEN = 'synthetic_upload_capability_'.padEnd(48, 'x');
const root = `/api/uploads/${UPLOAD}`;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const row = (text = '欢迎大家前来参观', patch = {}) => ({ idx: 0, text, kind: 'quote', terminal_punctuation: '', ...patch });
const segment = (text = '欢迎大家前来参观', patch = {}) => ({ id: 'seg1', start: 1, end: 5, speaker_id: 'sp1', text, words: [], confidence: null, snr_db: null, ...patch });
const take = (patch = {}) => ({ take_id: 'take1', upload_id: UPLOAD, start: 1, end: 5, speaker_id: 'sp1',
  asr_text: '欢迎大家前来参观', score: 1, snr_db: null, words: [], precision: 'segment', matched_text: '欢迎大家前来参观', segment_ids: ['seg1'], ...patch });
const match = (patch = {}) => ({ idx: 0, kind: 'quote', score: 1, source: take(), alt_takes: [],
  upload_id: UPLOAD, start: 1, end: 5, speaker_id: 'sp1', asr_text: '欢迎大家前来参观', ...patch });
const snapshot = (patch = {}) => ({ id: UPLOAD, name: 'synthetic.mp4', bytes: 3, sec: 10, status: 'ready', has_speech: true,
  thumb_url: null, wave_url: null, asr_confidence: null, transcript: [segment()], progress: 100, chunks: [0], ...patch });
function file(bytes = Uint8Array.of(1, 2, 3), patch = {}) { return new File([bytes], patch.name ?? 'synthetic.mp4', { type: 'video/mp4', lastModified: patch.lastModified ?? 123 }); }
const binding = (patch = {}) => ({ file_id: media.fileIdentity(file()), upload_id: UPLOAD, sha256: sha(Uint8Array.of(1, 2, 3)), chunk_size: 8 * 1024 * 1024, put_url: `${root}/chunks`, ...patch });
const session = (patch = {}) => ({ ...binding(), access_token: TOKEN, ...patch });
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
const microtasks = async () => { for (let i = 0; i < 24; i++) await Promise.resolve(); };
function clock() {
  const timers = new Map(); let next = 1;
  return { timers,
    setTimeout(callback, delay) { const id = next++; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fire(delay) { const entry = [...timers].find(([, item]) => item.delay === delay); assert.ok(entry, `Expected a real ${delay}ms timer`); timers.delete(entry[0]); entry[1].callback(); },
  };
}
function uploadsHarness(respond = forbidden('unexpected request'), timers = nativeTimers) {
  const calls = [], scopes = [];
  const api = load('uploads', { './productionModes': modes, './mediaInput': media,
    './appApi': { createAppRequest(access) {
      assert.deepEqual(Object.keys(access), ['signal']); assert.ok(access.signal instanceof AbortSignal); scopes.push(access);
      return async (path, init = {}) => { calls.push({ path, init, signal: access.signal }); return respond(path, init, access.signal); };
    } } }, timers);
  return { api, calls, scopes };
}

test('quote ranking preserves server evidence order, unchanged containment scores and manual choices', () => {
  const { api } = uploadsHarness();
  const text = '买了点腊肉，还有礼盒，准备给老人送去';
  const full = take({ asr_text: text, matched_text: text, snr_db: 8 });
  const fragment = take({ take_id: 'fragment', asr_text: '腊', matched_text: '腊', snr_db: 40, start: 7, end: 9 });
  assert.equal(modes.similarity(text, full.matched_text), 1);
  assert.equal(modes.similarity(text, fragment.matched_text), 1);
  assert.equal(modes.MODE_RULES.notes.quote_ranking, rules.notes.quote_ranking);
  const sentences = [row(text)];
  const value = { matches: [match({ source: full, asr_text: text, alt_takes: [fragment] })] };
  const before = JSON.stringify(value);
  const parsed = api.parseMatchPreview(value, sentences, [UPLOAD]);
  assert.deepEqual(plain(parsed), value.matches);
  assert.equal(modes.quoteGate(sentences, parsed, 'original'), '');
  assert.equal(modes.quoteGate(sentences, parsed, 'mixed'), '');
  assert.equal(JSON.stringify(value), before);
  // The parser must not secretly re-rank an explicitly selected source.
  const manual = api.parseMatchPreview({ matches: [match({ source: fragment, start: 7, end: 9,
    asr_text: '腊', alt_takes: [full] })] }, sentences, [UPLOAD]);
  assert.equal(manual[0].source.take_id, 'fragment');
  assert.notEqual(modes.quoteGate(sentences, manual, 'original'), '');
  for (const [query, actual] of [['我们来了', '我们，然后来了'], ['欢迎参观', '欢迎大家参观']]) {
    const source = take({ asr_text: actual, matched_text: actual });
    const result = api.parseMatchPreview({ matches: [match({ source, asr_text: actual })] }, [row(query)], [UPLOAD]);
    assert.notEqual(modes.quoteGate([row(query)], result, 'original'), '');
  }
});

test('shared JSON is the single source of mode names, thresholds, pacing and ten stages', () => {
  assert.deepEqual(plain(modes.PACING_CPM), { slow: 230, normal: 265, fast: 290 });
  assert.equal(modes.MATCH_LOW, .6); assert.equal(modes.MATCH_OK, .85);
  assert.deepEqual(plain(modes.MODE_RULES), rules);
  for (const mode of ['voiceover', 'mixed', 'original']) {
    assert.equal(modes.MODE_LABELS[mode], rules.modes[mode].name);
    assert.equal(modes.MODE_CARDS.find(card => card.mode === mode).description, rules.modes[mode].description);
    assert.equal(modes.STAGE_LABELS[mode].length, 10);
    assert.deepEqual(plain(modes.STAGE_DEFINITIONS[mode]), rules.modes[mode].stages);
    assert.ok(Math.abs(modes.STAGE_WEIGHTS[mode].reduce((sum, weight) => sum + weight, 0) - 1) < 1e-12);
  }
  assert.equal(modes.MODE_LIMITS.lower_third_repeat_gap_seconds, 60);
});

test('mode cards retain the exact descriptions and fit text from the handoff', () => {
  assert.deepEqual(plain(modes.MODE_CARDS.map(({ description, fit }) => [description, fit])), [
    ['稿子全由 AI 读，素材只出画面', '消息、活动报道、无人说话的素材'],
    ['旁白 AI 读，人说话的地方直接用现场原声', '带采访的新闻、有受访者发言'],
    ['稿子就是采访原话，直接剪出来', '人物专访、演讲、街访集锦'],
  ]);
});

test('five-element hints recognize general news people and locations without rewriting input', () => {
  const rule = key => media.ELEMENT_RULES.find(item => item.key === key).rule;
  for (const word of ['市民', '居民', '游客', '商户', '工作人员', '负责人', '先生', '女士', '企业', '记者', '志愿者', '商家']) assert.ok(rule('who').test(word), word);
  for (const word of ['95号', '区', '广场', '街道', '市场', '展馆', '社区', '公园', '会场']) assert.ok(rule('place').test(word), word);
  for (const word of ['同学', '学生', '老师', '校长']) assert.equal(rule('who').test(word), false, word);
  for (const word of ['学校', '校园', '操场', '图书馆', '礼堂']) assert.equal(rule('place').test(word), false, word);
  assert.ok(rule('time').test('今天')); assert.ok(rule('what').test('举办')); assert.ok(rule('why').test('为了'));
});

test('parser matches Python title, BOM, CRLF and first-nonempty-line rules', () => {
  assert.deepEqual(plain(modes.parseModeScript('\ufeff\r\n \r\n标题！\r\n\r\n正文，第二句。', 'voiceover')),
    [row('正文', { kind: 'narration', speaker_hint: '', terminal_punctuation: '，' }), row('第二句', { idx: 1, kind: 'narration', speaker_hint: '', terminal_punctuation: '。' })]);
  for (const value of ['', '\n \n', '只有标题', '\n只有标题\n\n']) assert.equal(modes.parseModeScript(value, 'mixed').length, 0);
  assert.deepEqual(plain(modes.parseModeScript('标题\u0085正文。\u2028下一句', 'original').map(row => row.text)), ['正文', '下一句']);
});

test('narration splits commas/semicolons but keeps decimals, brackets, enumeration and quoted names', () => {
  const texts = modes.parseModeScript('标题\n苹果、梨,很好；开门;你好!结束?下一句.最后。票价3.14元，活动叫“高新，年味”。', 'voiceover').map(row => row.text);
  assert.deepEqual(plain(texts), ['苹果、梨', '很好', '开门', '你好', '结束', '下一句', '最后', '票价3.14元', '活动叫“高新，年味”']);
  assert.deepEqual(plain(modes.parseModeScript('标题\n活动（内容，地点）；《节目，专题》。', 'voiceover').map(row => row.text)), ['活动（内容，地点）', '《节目，专题》']);
});

test('mixed recognizes every required prefix while ordinary colons stay narration', () => {
  const result = modes.parseModeScript('标题\n旁白：先说背景，活动开幕。\n同期：今天很热闹，欢迎大家。\n【同期】我们来了！\n同期 王红（主办方）：大家好。谢谢！\n李大姐（摊主）：是自己做的，用本地猪肉。', 'mixed');
  assert.deepEqual(plain(result.map(row => row.kind)), ['narration', 'narration', 'quote', 'quote', 'quote', 'quote', 'quote']);
  assert.equal(result[2].text, '今天很热闹，欢迎大家');
  assert.equal(result[4].speaker_hint, '王红（主办方）'); assert.equal(result[6].speaker_hint, '李大姐（摊主）');
  assert.deepEqual(plain(modes.parseModeScript('标题\n活动时间：上午九点。\n同期增长很快。', 'mixed').map(row => row.kind)), ['narration', 'narration']);
});

test('original preserves commas; a manual mixed override never changes segmentation', () => {
  assert.deepEqual(plain(modes.parseModeScript('标题\n买了腊肉，还有礼盒\n自己做的，用本地猪肉。欢迎！', 'original').map(row => row.text)), ['买了腊肉，还有礼盒', '自己做的，用本地猪肉', '欢迎']);
  const source = '标题\n同期：长句，仍是同句。\n一段旁白。';
  assert.deepEqual(plain(modes.parseModeScript(source, 'mixed', { 0: 'narration', 1: 'quote' }).map(row => [row.text, row.kind])), [['长句，仍是同句', 'narration'], ['一段旁白', 'quote']]);
});

test('parser rejects invalid modes, override indices/kinds and oversize sentences', () => {
  for (const [mode, marks] of [['voiceover', { 0: 'quote' }], ['original', { 0: 'narration' }], ['mixed', { 3: 'quote' }],
    ['mixed', { true: 'quote' }], ['mixed', { '00': 'quote' }], ['mixed', { 0: 'grade' }], ['invalid', {}]]) {
    assert.throws(() => modes.parseModeScript('标题\n内容', mode, marks));
  }
  assert.throws(() => modes.parseModeScript('标题\n' + '字'.repeat(2001), 'mixed'));
  assert.equal(modes.parseModeScript('标题\n' + '字'.repeat(2000), 'mixed')[0].text.length, 2000);
});

test('normalization follows Python Chinese/financial numeric and date fixtures without losing signs', () => {
  const cases = { '两百': '200', '兩百': '200', '一千零二十': '1020', '十': '10', '十一': '11', '二十三': '23',
    '一万零三': '10003', '二亿三千万': '230000000', '壹佰贰拾叁': '123', '貳佰參拾肆': '234', '伍佰陆拾柒': '567',
    '捌仟玖佰': '8900', '三点五': '3.5', '负三点五': '-3.5', '一月三十一号': '1月31日', '１月３１日': '1月31日',
    '二〇二六年九月二十八日': '2026年9月28日', '鲁班路九十五号': '鲁班路95号', '一 月 三 十 一 号': '1月31日', '两\n百零\t三': '203', '－ ３．５': '-3.5' };
  for (const [input, expected] of Object.entries(cases)) assert.equal(modes.normalizeText(input), expected, input);
  assert.notEqual(modes.normalizeText('3.5元'), modes.normalizeText('35元'));
  assert.notEqual(modes.normalizeText('负三度'), modes.normalizeText('三度'));
  assert.notEqual(modes.normalizeText('95号'), modes.normalizeText('95日'));
});

test('normalization keeps idioms and word-internal fillers, while stripping boundary fillers', () => {
  assert.equal(modes.normalizeText('嗯，啊，呃，那个，就是说，就是，然后我们欢迎大家'), '我们欢迎大家');
  assert.equal(modes.normalizeText('嗯…就是希望大家来。'), '希望大家来');
  assert.equal(modes.normalizeText('🙂嗯我们欢迎大家'), '我们欢迎大家');
  for (const input of ['那个人', '然后果很严重', '呃逆', '阿姨', '就是事实', '我们一直在一起', '千万别忘记']) assert.equal(modes.normalizeText(input), input);
});

test('NFKC, combining accents, Jamo and locale-independent casefold are stable', () => {
  assert.equal(modes.normalizeText(' ＡＢＣ，‘采访’！？\n'), 'abc采访');
  assert.equal(modes.normalizeText('Café'), modes.normalizeText('Cafe\u0301'));
  assert.equal(modes.normalizeText('\u1100\u1161'), modes.normalizeText('가'));
  assert.equal(modes.normalizeText('Straße ẞς'), 'strassessσ');
  assert.equal(modes.normalizeText('ı'), 'ı');
});

test('original text evidence rejects inserted words and noncontiguous deletion, including interior fillers', () => {
  assert.equal(modes.quoteTextEvidence('今天欢迎大家', '前文今天欢迎大家后文').contiguous, true);
  assert.equal(modes.quoteTextEvidence('我们欢迎大家', '我们今天欢迎大家').unverified, true);
  assert.equal(modes.quoteTextEvidence('我们，嗯，欢迎大家', '我们欢迎大家').unverified, true);
  // The recognizer heard '那边比较旺一点' this time: the typed line is blocked in 只用原声, and taking the
  // transcript's exact words (what “改成素材里的原话” does) passes the same literal check.
  assert.equal(modes.quoteTextEvidence('那边比较方便', '那边比较旺一点。').unverified, true);
  assert.equal(modes.quoteTextEvidence('那边比较旺一点', '那边比较旺一点。').unverified, false);
  assert.equal(modes.quoteTextEvidence('我们欢迎大家', '我们，嗯，欢迎大家').unverified, true);
  assert.equal(modes.quoteTextEvidence('1月31日', '一月三十一号').exact, true);
  assert.equal(modes.quoteTextEvidence('', '欢迎大家').unverified, true);
});

test('quote thresholds and original truth gate do not fabricate a score or audio', () => {
  assert.equal(modes.matchState(undefined), 'pending');
  assert.equal(modes.matchState(match({ score: .5999 })), 'missing');
  assert.equal(modes.matchState(match({ score: .6 })), 'low');
  assert.equal(modes.matchState(match({ score: .85 })), 'ok');
  assert.equal(modes.matchState(match({ source: null, score: 1 })), 'missing');
  assert.match(modes.quoteGate([row()], [], 'mixed'), /等待/);
  assert.match(modes.quoteGate([row()], [match({ source: null, score: .2 })], 'mixed'), /改成旁白/);
  assert.equal(modes.quoteGate([row()], [match()], 'original'), '');
  assert.match(modes.quoteGate([row('欢迎参观')], [match()], 'original'), /中间删词/);
  assert.equal(modes.quoteGate([row('欢迎参观')], [match()], 'mixed'), '');
  assert.equal(modes.quoteGate([row()], [], 'voiceover'), '');
});

test('selecting transcripts appends in user order and removes only the exact source identity', () => {
  let rows = modes.selectTranscriptSentence([], UPLOAD, segment(), true);
  rows = modes.selectTranscriptSentence(rows, OTHER, segment(), true);
  assert.equal(rows.length, 2); assert.equal(rows[0].source_hint.upload_id, UPLOAD); assert.equal(rows[1].source_hint.upload_id, OTHER);
  rows = modes.selectTranscriptSentence(rows, UPLOAD, segment(), true); assert.equal(rows.length, 2);
  rows = modes.selectTranscriptSentence(rows, UPLOAD, segment(), false);
  assert.equal(rows.length, 1); assert.equal(rows[0].idx, 0); assert.equal(rows[0].source_hint.upload_id, OTHER);
  assert.ok(modes.scriptFromSentences('', rows, 'original').startsWith('（写一个标题）\n'));
});

test('preview jump cuts reset across narration and use supplied file/time evidence only', () => {
  const rows = [row(), row('第二句', { idx: 1 }), row('旁白', { idx: 2, kind: 'narration' }), row('第三句', { idx: 3 })];
  const ms = [match(), match({ idx: 1, source: take({ upload_id: OTHER, start: 6, end: 9 }) }), match({ idx: 3, source: take() })];
  assert.equal(modes.countPreviewJumpCuts(rows, ms), 1);
  assert.equal(modes.countPreviewJumpCuts(rows.slice(0, 2), [match(), match({ idx: 1, source: take({ start: 5.5, end: 8 }) })]), 0);
});

test('mode preferences are whitelisted; original defaults disable music, motion and generation', () => {
  const original = modes.defaultModePreferences('original');
  assert.equal(original.background_music, false); assert.equal(original.motion_effects, false); assert.equal(original.generative_fill, false);
  const value = modes.readModePreferences({ grade: 'retired', authorization: TOKEN, quote_caption: 'spoken', voice: 'mine', generative_fill: true }, 'original');
  assert.equal(value.quote_caption, 'spoken'); assert.equal(value.voice, 'ai'); assert.equal(value.generative_fill, false);
  assert.ok(!('grade' in value)); assert.ok(!('authorization' in value));
  for (const bad of [{ background_music: 'yes' }, { pacing: 'turbo' }, { lower_third: 1 }, { custom_instructions: 'x'.repeat(501) }]) assert.throws(() => modes.readModePreferences(bad));
});

test('all modes default speech enhancement on but preserve an explicit saved opt-out', () => {
  for (const mode of ['voiceover', 'mixed', 'original']) {
    assert.equal(modes.defaultModePreferences(mode).enhance_speech, true);
    assert.equal(modes.readModePreferences(undefined, mode).enhance_speech, true);
    assert.equal(modes.readModePreferences({}, mode).enhance_speech, true);
    const saved = { enhance_speech: false }, before = JSON.stringify(saved);
    assert.equal(modes.readModePreferences(saved, mode).enhance_speech, false);
    assert.equal(JSON.stringify(saved), before);
    assert.throws(() => modes.readModePreferences({ enhance_speech: 'true' }, mode));
  }
});

const legacy = () => ({ tasks: { 'old-1': { id: 'old-1', title: '旧标题', script: '旧标题\n旧稿件', status: 'held', revision: 2, createdAt: 123,
  grade: 'retired', access_token: TOKEN, arbitrary: { secret: 'discard' } } }, history: ['old-1'],
  draft: { script: '标题\n同期：现场原话', step: 2, mode: 'mixed', prefs: { grade: 'retired', quote_caption: 'spoken' },
    files: [{ name: '旧文件.mp4', mb: 12, sec: 10, thumb: 'data:secret', secret: 'discard' }], typeMarks: { '现场原话': 'quote' },
    speakerNames: { sp1: { name: '张先生', role: '主办方', secret: 'discard' } } },
  pend: { 'old-1': { secret: 'discard' } }, checked: { invented_check: true }, bigFont: true });
test('legacy core is bounded/read-only, maps held to queued and never imports secrets, grade or simulated media', () => {
  assert.equal(modes.restoreLegacyCore(null), null); const input = legacy(), before = JSON.stringify(input);
  const result = modes.restoreLegacyCore(before); assert.equal(result.tasks['old-1'].status, 'queued');
  assert.equal(result.draft.mode, 'mixed'); assert.equal(result.draft.speakers[0].title, '主办方');
  assert.equal(result.draft.preferences.quote_caption, 'spoken'); assert.deepEqual(plain(result.legacyFileNames), ['旧文件.mp4']);
  assert.equal(result.draft.files.length, 0); assert.equal(JSON.stringify(input), before);
  const encoded = JSON.stringify(result);
  for (const forbidden of ['grade', TOKEN, 'data:secret', 'discard', 'invented_check', 'pend', 'access_token']) assert.ok(!encoded.includes(forbidden));
});

test('legacy malformed roots, overlarge histories/strings and prototype-pollution IDs fail closed', () => {
  for (const raw of ['{broken', 'null', '[]', '{}', 'x'.repeat(2 * 1024 * 1024 + 1), JSON.stringify({ ...legacy(), history: ['missing'] }),
    JSON.stringify({ ...legacy(), history: Array(101).fill('old-1') }), JSON.stringify({ ...legacy(), draft: { ...legacy().draft, script: 'x'.repeat(8001) } })]) {
    assert.throws(() => modes.restoreLegacyCore(raw));
  }
  const payload = JSON.parse('{"tasks":{"__proto__":{"id":"__proto__","title":"x","status":"held"}},"history":["__proto__"]}');
  const value = modes.restoreLegacyCore(JSON.stringify(payload));
  assert.equal(Object.getPrototypeOf(value.tasks), null); assert.equal(value.tasks.__proto__.status, 'queued');
  assert.equal({}.polluted, undefined);
});

test('sentence/speaker projections reject duplicate IDs and discard unknown nested data', () => {
  assert.throws(() => modes.readSentenceInputs([row(), row()]));
  assert.throws(() => modes.readSentenceInputs([row()], 'voiceover'));
  const projected = modes.readSentenceInputs([row('内容', { access_token: TOKEN, source_hint: { upload_id: UPLOAD, seg_id: 'seg1', secret: TOKEN } })]);
  assert.ok(!JSON.stringify(projected).includes(TOKEN));
  const speaker = { id: 'sp1', name: '', title: '', auto_label: '', appearances: 1, seconds: 4, secret: TOKEN };
  assert.ok(!JSON.stringify(modes.readSpeakers([speaker])).includes(TOKEN));
  assert.throws(() => modes.readSpeakers([speaker, speaker]));
});

test('speaker projection preserves legacy colon IDs as bounded identities without path conversion', () => {
  const speaker = { id: `${UPLOAD}:speaker1`, name: '', title: '', auto_label: '说话人 1', appearances: 1, seconds: 4 };
  const before = JSON.stringify(speaker);
  assert.deepEqual(plain(modes.readSpeakers([speaker])), [speaker]);
  assert.equal(JSON.stringify(speaker), before);
  assert.throws(() => modes.readSpeakers([speaker, { ...speaker }]));
  for (const id of ['', ' ', 'x'.repeat(129), null, 1]) assert.throws(() => modes.readSpeakers([{ ...speaker, id }]));
  assert.equal(modes.readSpeakers([{ ...speaker, id: 'x'.repeat(128) }])[0].id.length, 128);
});

test('draft sentences cannot inject content or erase decimal meanings during restoration', () => {
  assert.equal(modes.sentencesMatchScript('标题\n同期：欢迎大家，今天见面。', [row('欢迎大家，今天见面', { kind: 'narration' })], 'mixed'), true);
  assert.equal(modes.sentencesMatchScript('标题\n票价3.5元。', [row('票价35元', { kind: 'narration' })], 'voiceover'), false);
  assert.equal(modes.sentencesMatchScript('标题\n现场没有这句话。', [row('伪造的话', { kind: 'narration' })], 'voiceover'), false);
});

test('speaker hints only name actually matched clusters and conflicting hints stay unresolved', () => {
  const speaker = { id: 'sp1', name: '', title: '', auto_label: '说话人 1', appearances: 1, seconds: 4 };
  const sentences = [row('欢迎大家前来参观', { speaker_hint: '王红（主办方）' })];
  assert.equal(modes.applySpeakerHints([speaker], sentences, [match()])[0].name, '王红');
  assert.equal(modes.applySpeakerHints([speaker], sentences, [match({ source: null })])[0].name, '');
  assert.equal(modes.applySpeakerHints([speaker], [...sentences, row('别的话', { idx: 1, speaker_hint: '李姐（商家）' })], [match(), match({ idx: 1 })])[0].name, '');
  assert.equal(modes.applySpeakerHints([{ ...speaker, name: '已核实姓名' }], sentences, [match()])[0].name, '已核实姓名');
});

test('incremental SHA-256 matches standard empty/abc/million-a vectors and is finalized once', () => {
  const { api } = uploadsHarness();
  for (const bytes of [Buffer.alloc(0), Buffer.from('abc'), Buffer.alloc(1_000_000, 97)]) {
    const digest = new api.IncrementalSha256();
    for (let offset = 0; offset < bytes.length; offset += 137) digest.update(bytes.subarray(offset, offset + 137));
    assert.equal(digest.hex(), sha(bytes)); assert.equal(digest.hex(), sha(bytes));
    assert.throws(() => digest.update(Uint8Array.of(1)));
  }
});

test('SHA padding and arbitrary byte partition boundaries equal independent Node crypto', () => {
  const { api } = uploadsHarness();
  for (const length of [1, 55, 56, 63, 64, 65, 119, 120, 127, 128, 129, 513, 4097]) {
    const bytes = Uint8Array.from({ length }, (_, i) => (i * 67 + 13) % 256);
    for (const stride of [1, 3, 64, 1000]) {
      const digest = new api.IncrementalSha256();
      for (let i = 0; i < length; i += stride) digest.update(bytes.subarray(i, i + stride));
      assert.equal(digest.hex(), sha(bytes), `${length}/${stride}`);
    }
  }
});

test('file hashing uses only bounded slices and never calls whole-file arrayBuffer', async () => {
  const { api } = uploadsHarness(), bytes = Buffer.alloc(8 * 1024 * 1024 + 317, 91), reads = [], progress = [];
  const source = { size: bytes.length, arrayBuffer: forbidden('whole file read'), slice(start, end) {
    reads.push([start, end]); assert.ok(end - start <= api.UPLOAD_CHUNK_BYTES); return new Blob([bytes.subarray(start, end)]);
  } };
  assert.equal(await api.hashFileIncrementally(source, signal(), (loaded, total) => progress.push([loaded, total])), sha(bytes));
  assert.equal(reads.length, 2); assert.equal(progress.at(-1)[0], bytes.length); assert.ok(progress.every((row, i) => !i || row[0] > progress[i - 1][0]));
});

test('500 MiB virtual file hashes with one bounded slice in flight and an independent incremental oracle', async () => {
  const immediate = { setTimeout(callback, delay) { assert.equal(delay, 0); const handle = { cancelled: false }; queueMicrotask(() => { if (!handle.cancelled) callback(); }); return handle; }, clearTimeout(handle) { handle.cancelled = true; } };
  const { api } = uploadsHarness(undefined, immediate), block = Buffer.alloc(8 * 1024 * 1024, 137), expected = createHash('sha256');
  const total = 500 * 1024 * 1024; let reads = 0, inFlight = 0, maximum = 0, reported = 0;
  for (let offset = 0; offset < total; offset += block.length) expected.update(block.subarray(0, Math.min(block.length, total - offset)));
  const source = { size: total, arrayBuffer: forbidden('500 MiB whole-file allocation'), slice(start, end) {
    assert.ok(end - start <= block.length); assert.equal(inFlight, 0); reads++;
    return { async arrayBuffer() { inFlight++; maximum = Math.max(maximum, inFlight); const result = block.buffer.slice(block.byteOffset, block.byteOffset + end - start); inFlight--; return result; } };
  } };
  assert.equal(await api.hashFileIncrementally(source, signal(), bytes => { reported = bytes; }), expected.digest('hex'));
  assert.equal(reads, 63); assert.equal(maximum, 1); assert.equal(reported, total);
});

test('hash cancellation after a bounded read prevents progress and any upload request', async () => {
  const { api, calls } = uploadsHarness(), controller = new AbortController(), pending = deferred(); let slices = 0, reported = 0;
  const result = api.hashFileIncrementally({ size: 3, slice() { slices++; return { arrayBuffer: () => pending.promise }; } }, controller.signal, () => reported++);
  controller.abort(); pending.resolve(Uint8Array.of(1, 2, 3).buffer);
  await assert.rejects(result, abortError); assert.equal(slices, 1); assert.equal(reported, 0); assert.equal(calls.length, 0);
});

test('upload binding/token projection validates exact identity and discards unrelated secrets', () => {
  const { api } = uploadsHarness();
  assert.deepEqual(plain(api.readUploadBindings([{ ...binding(), secret: TOKEN }])), [binding()]);
  assert.deepEqual(plain(api.readUploadTokens({ [UPLOAD]: TOKEN, discarded: 'secret' }, [UPLOAD])), { [UPLOAD]: TOKEN });
  for (const change of [{ sha256: 'not-a-hash' }, { chunk_size: 16 * 1024 * 1024 }, { file_id: '[]' }, { put_url: 'https://outside.invalid/put' },
    { put_url: `/api/uploads/${OTHER}/chunks` }, { put_url: `${root}/chunks?token=${TOKEN}` }]) assert.throws(() => api.readUploadBindings([binding(change)]));
  assert.throws(() => api.readUploadTokens({}, [UPLOAD])); assert.throws(() => api.readUploadBindings([binding(), binding()]));
});

test('private thumbnail URLs are minted only for exact owned media paths and are not persisted in bindings', () => {
  const { api } = uploadsHarness();
  assert.equal(api.uploadMediaUrl(snapshot({ thumb_url: `${root}/thumb` }), TOKEN, 'thumb'), `${root}/thumb?token=${TOKEN}`);
  assert.equal(api.uploadMediaUrl(snapshot({ thumb_url: `${root}/thumb?token=other` }), TOKEN, 'thumb'), null);
  assert.equal(api.uploadMediaUrl(snapshot({ thumb_url: `/api/uploads/${OTHER}/thumb` }), TOKEN, 'thumb'), null);
  assert.equal(api.uploadMediaUrl(snapshot(), TOKEN, 'thumb'), null);
  assert.ok(!JSON.stringify(api.readUploadBindings([session()])).includes(TOKEN));
});

test('upload snapshots require numeric clocks and acknowledged chunks, not fabricated success', () => {
  const { api } = uploadsHarness();
  assert.equal(api.uploadUsable(api.parseUploadSnapshot(snapshot(), UPLOAD)), true);
  assert.equal(api.uploadUsable(api.parseUploadSnapshot(snapshot({ chunks: [] }), UPLOAD)), false);
  assert.equal(api.uploadUsable(api.parseUploadSnapshot(snapshot({ status: 'failed', error_code: 'ASR_FAILED', probe_ok: true, has_speech: false, transcript: [] }), UPLOAD)), true);
  assert.equal(api.uploadUsable(api.parseUploadSnapshot(snapshot({ status: 'failed', probe_ok: true, has_speech: false, transcript: [] }), UPLOAD)), false);
  assert.equal(api.uploadUsable(api.parseUploadSnapshot(snapshot({ status: 'failed', has_speech: false, transcript: [] }), UPLOAD)), false);
  for (const change of [{ id: OTHER }, { progress: 101 }, { asr_confidence: NaN }, { chunks: [0, 0] }, { chunks: [1] }, { sec: 0 },
    { transcript: [segment('', { start: 5, end: 1 })] }, { thumb_url: 'https://evil.invalid/image' }, { thumb_url: `${root}/../other` }]) {
    assert.throws(() => api.parseUploadSnapshot(snapshot(change), UPLOAD));
  }
});

test('UploadStore envelope/status/unknown speech/error-null contract is explicitly projected', () => {
  const { api } = uploadsHarness();
  const pending = snapshot({ status: 'processing', phase: 'asr', error: null, has_speech: null,
    transcript: { segments: [segment()], speakers: [], precision: 'segment', ignored: TOKEN },
    probe_ok: true, can_materialize: false, received_bytes: 3, chunk_size: 8 * 1024 * 1024, total_chunks: 1 });
  const parsed = api.parseUploadSnapshot(pending); assert.equal(parsed.status, 'transcribing'); assert.equal(parsed.has_speech, null);
  assert.equal(parsed.transcript.length, 1); assert.ok(!JSON.stringify(parsed).includes(TOKEN));
  assert.equal(api.uploadUsable(parsed), false);
  const failed = api.parseUploadSnapshot({ ...pending, status: 'asr_failed', phase: 'error', can_materialize: true });
  assert.equal(failed.failure_kind, 'asr'); assert.equal(api.uploadUsable(failed), true);
  assert.equal(api.uploadUsable(api.parseUploadSnapshot({ ...pending, status: 'failed', phase: 'error', can_materialize: false })), false);
  const transferring = api.parseUploadSnapshot({ ...pending, status: 'uploading', phase: 'upload', progress: 40 });
  assert.equal(transferring.progress, 100);
  assert.throws(() => api.parseUploadSnapshot({ ...pending, received_bytes: 4 }));
  assert.throws(() => api.parseUploadSnapshot({ ...pending, status: 'processing', phase: 'unknown' }));
});

test('ready upload recovery is one GET with X-Upload-Token and no File, POST or hashing', async () => {
  const { api, calls } = uploadsHarness(() => snapshot()); const snapshots = [];
  const result = await api.uploadFile(undefined, signal(), { onSession: forbidden('new session'), onSnapshot: row => snapshots.push(row), onHashProgress: forbidden('rehash ready upload') }, session());
  assert.equal(result.status, 'ready'); assert.equal(snapshots.length, 1); assert.equal(calls.length, 1);
  assert.equal(calls[0].path, root); assert.equal(new Headers(calls[0].init.headers).get('X-Upload-Token'), TOKEN);
});

test('ready recovery rejects an ID/name/size/hash mismatch without a mutation', async () => {
  for (const change of [{ name: 'wrong.mp4' }, { bytes: 4 }, { sha256: '0'.repeat(64) }]) {
    const h = uploadsHarness(() => snapshot(change));
    await assert.rejects(h.api.uploadFile(undefined, signal(), { onSession() {}, onSnapshot() {} }, session()));
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0].init.method, undefined);
  }
});

test('a processing session is observed, never completed or transcribed again on resume', async () => {
  const h = uploadsHarness(() => snapshot({ status: 'transcribing' }));
  const result = await h.api.uploadFile(undefined, signal(), { onSession() {}, onSnapshot() {} }, session());
  assert.equal(result.status, 'transcribing'); assert.equal(h.calls.length, 1);
});

test('missing chunks require the original File; metadata alone cannot complete them', async () => {
  const h = uploadsHarness(() => snapshot({ status: 'uploading', chunks: [], sec: null, transcript: [], progress: 0 }));
  await assert.rejects(h.api.uploadFile(undefined, signal(), { onSession() {}, onSnapshot() {} }, session()), /重新选择/);
  assert.equal(h.calls.length, 1);
});

test('fully acknowledged upload can complete without a File after explicit user action', async () => {
  let completed = false; const h = uploadsHarness((path, init) => {
    if (init.method === 'POST') { assert.equal(path, `${root}/complete`); completed = true; return {}; }
    assert.equal(path, root); return snapshot({ status: completed ? 'transcribing' : 'uploading' });
  });
  const result = await h.api.uploadFile(undefined, signal(), { onSession() {}, onSnapshot() {} }, session());
  assert.equal(result.status, 'transcribing'); assert.equal(h.calls.filter(call => call.init.method === 'POST').length, 1);
  assert.ok(h.calls.every(call => call.init.method !== 'PUT'));
});

test('new upload publishes its receipt before PUT, verifies bytes, and completes exactly once', async () => {
  let receiptPublished = false, acknowledged = false, completed = false; const source = file(), history = [];
  const h = uploadsHarness(async (path, init) => {
    if (path === '/api/uploads') { assert.deepEqual(JSON.parse(init.body), { name: source.name, bytes: source.size, sha256: sha(Uint8Array.of(1, 2, 3)) });
      return { upload_id: UPLOAD, access_token: TOKEN, chunk_size: 8 * 1024 * 1024, put_url: `${root}/chunks/{index}` }; }
    assert.equal(new Headers(init.headers).get('X-Upload-Token'), TOKEN);
    if (init.method === 'PUT') {
      assert.equal(receiptPublished, true); assert.equal(path, `${root}/chunks/0`);
      assert.equal(new Headers(init.headers).get('Content-Range'), 'bytes 0-2/3');
      assert.deepEqual(new Uint8Array(await init.body.arrayBuffer()), Uint8Array.of(1, 2, 3)); acknowledged = true; return {};
    }
    if (init.method === 'POST') { assert.equal(path, `${root}/complete`); assert.equal(acknowledged, true); completed = true; return {}; }
    return snapshot({ status: completed ? 'probing' : 'uploading', chunks: acknowledged ? [0] : [], progress: acknowledged ? 100 : 0, sec: null, transcript: [] });
  });
  const result = await h.api.uploadFile(source, signal(), { onSession(receipt) { receiptPublished = true; assert.equal(receipt.file_id, media.fileIdentity(source)); }, onSnapshot: row => history.push(row) });
  assert.equal(result.status, 'probing'); assert.equal(h.calls.filter(call => call.path === '/api/uploads').length, 1);
  assert.equal(h.calls.filter(call => call.init.method === 'PUT').length, 1); assert.equal(h.calls.filter(call => call.path.endsWith('/complete')).length, 1);
  assert.equal(history.at(-1).status, 'probing');
});

test('resume skips acknowledged chunks after verifying the whole incremental hash', async () => {
  const bytes = Buffer.alloc(8 * 1024 * 1024 + 31, 19), source = file(bytes); let acknowledged = false, completed = false;
  const h = uploadsHarness((path, init) => {
    if (init.method === 'PUT') { assert.equal(path, `${root}/chunks/1`); assert.equal(init.body.size, 31); acknowledged = true; return {}; }
    if (init.method === 'POST') { assert.equal(path, `${root}/complete`); completed = true; return {}; }
    return snapshot({ bytes: bytes.length, status: completed ? 'ready' : 'uploading', chunks: acknowledged ? [0, 1] : [0], progress: 90 });
  });
  await h.api.uploadFile(source, signal(), { onSession: forbidden('duplicate session'), onSnapshot() {} }, session({ file_id: media.fileIdentity(source), sha256: sha(bytes) }));
  assert.equal(h.calls.filter(call => call.init.method === 'PUT').length, 1); assert.ok(completed);
});

test('same name/size/mtime but different bytes cannot resume or reuse a chunk session', async () => {
  const h = uploadsHarness(() => snapshot({ status: 'uploading', chunks: [], progress: 0 }));
  await assert.rejects(h.api.uploadFile(file(Uint8Array.of(4, 5, 6)), signal(), { onSession() {}, onSnapshot() {} }, session()), /内容已变化/);
  assert.equal(h.calls.length, 1);
});

test('reselecting a ready upload verifies content before emitting any ready state', async () => {
  const h = uploadsHarness(() => snapshot()); let emitted = 0;
  await assert.rejects(h.api.uploadFile(file(Uint8Array.of(4, 5, 6)), signal(), { onSession() {}, onSnapshot() { emitted++; } }, session()), /内容/);
  assert.equal(emitted, 0); assert.equal(h.calls.length, 1);
});

test('ambiguous PUT or completion failure is never automatically replayed', async () => {
  for (const failingMethod of ['PUT', 'POST']) {
    let acknowledged = failingMethod === 'POST'; const failure = new Error('Synthetic disconnect');
    const h = uploadsHarness((path, init) => {
      if (init.method === failingMethod) throw failure;
      if (init.method === 'PUT') { acknowledged = true; return {}; }
      return snapshot({ status: 'uploading', chunks: acknowledged ? [0] : [], progress: acknowledged ? 100 : 0 });
    });
    await assert.rejects(h.api.uploadFile(file(), signal(), { onSession() {}, onSnapshot() {} }, session()), error => error === failure);
    assert.equal(h.calls.filter(call => call.init.method === failingMethod).length, 1);
  }
});

test('polling waits exactly 2s, releases abort listeners and never overlaps GETs', async () => {
  const timer = clock(), first = deferred(); let reads = 0, snapshots = 0;
  const h = uploadsHarness(() => ++reads === 1 ? first.promise : snapshot(), timer), controller = new AbortController();
  const pending = h.api.pollUploadStatus(UPLOAD, TOKEN, controller.signal, () => snapshots++);
  assert.equal(reads, 1); assert.equal(timer.timers.size, 0);
  first.resolve(snapshot({ status: 'transcribing' })); await microtasks();
  assert.equal(timer.timers.size, 1); assert.equal([...timer.timers.values()][0].delay, 2000);
  timer.fire(2000); await pending; assert.equal(reads, 2); assert.equal(snapshots, 2);
  assert.equal(timer.timers.size, 0); assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
});

test('cancelling a poll drops even a transport response that ignores abort', async () => {
  const read = deferred(), h = uploadsHarness(() => read.promise), controller = new AbortController(); let snapshots = 0;
  const pending = h.api.pollUploadStatus(UPLOAD, TOKEN, controller.signal, () => snapshots++);
  controller.abort(); read.resolve(snapshot()); await assert.rejects(pending, abortError);
  assert.equal(snapshots, 0); assert.equal(h.calls.length, 1);
});

test('cancelling the 2s poll delay clears its timer/listener and starts no extra GET', async () => {
  const timer = clock(), h = uploadsHarness(() => snapshot({ status: 'probing' }), timer), controller = new AbortController();
  const pending = h.api.pollUploadStatus(UPLOAD, TOKEN, controller.signal, () => {}); await microtasks();
  controller.abort(); await assert.rejects(pending, abortError); assert.equal(h.calls.length, 1);
  assert.equal(timer.timers.size, 0); assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
});

test('preview sends exact confirmed sentences and upload capabilities, not server/provider calls', async () => {
  const h = uploadsHarness((path, init) => { assert.equal(path, '/api/match/preview'); assert.equal(init.method, 'POST');
    assert.deepEqual(JSON.parse(init.body), { sentences: [row()], upload_ids: [UPLOAD], upload_tokens: { [UPLOAD]: TOKEN } });
    return { matches: [match()] }; });
  const result = await h.api.requestMatchPreview([row()], [UPLOAD], { [UPLOAD]: TOKEN }, signal());
  assert.equal(result[0].score, 1); assert.equal(result[0].source.precision, 'segment'); assert.equal(result[0].source.words.length, 0);
  assert.equal(h.calls.length, 1);
});

test('preview parser fails on foreign sources, flat-field conflicts, missing rows and fabricated word precision', () => {
  const { api } = uploadsHarness();
  for (const rows of [[], [match(), match()], [match({ source: take({ upload_id: OTHER }) })], [match({ start: 20 })],
    [match({ source: take({ precision: 'word' }) })], [match({ source: take({ words: [{ w: '词', s: 2, e: 8 }] }) })]]) {
    assert.throws(() => api.parseMatchPreview({ matches: rows }, [row()], [UPLOAD]));
  }
});

test('preview cancellation and removed source hints do not accept or start stale work', async () => {
  const response = deferred(), h = uploadsHarness(() => response.promise), controller = new AbortController();
  const pending = h.api.requestMatchPreview([row()], [UPLOAD], { [UPLOAD]: TOKEN }, controller.signal);
  controller.abort(); response.resolve({ matches: [match()] }); await assert.rejects(pending, abortError);
  await assert.rejects(h.api.requestMatchPreview([row('内容', { source_hint: { upload_id: OTHER, seg_id: 'seg1' } })], [UPLOAD], { [UPLOAD]: TOKEN }, signal()));
  assert.equal(h.calls.length, 1);
});

// A deterministic hook adapter runs actual component branches/effects. It does
// not simulate DOM geometry, browser decoding, React scheduling or native mic.
function wizardHarness({ draft, uploadApi = {}, mediaApi = {}, props = {} } = {}) {
  const hooks = [], effects = [], timers = clock(), drafts = [], submits = []; let cursor = 0, tree, dirty = false, live = true;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const react = {
    useId: () => 'synthetic-wizard',
    useState(initial) { const at = cursor++; if (!(at in hooks)) hooks[at] = { value: typeof initial === 'function' ? initial() : initial };
      return [hooks[at].value, next => { const value = typeof next === 'function' ? next(hooks[at].value) : next; if (!Object.is(value, hooks[at].value)) { hooks[at].value = value; dirty = true; } }]; },
    useRef(initial) { const at = cursor++; if (!(at in hooks)) hooks[at] = { current: initial }; return hooks[at]; },
    useMemo(factory, dependencies) { const at = cursor++; if (!hooks[at] || !same(hooks[at].dependencies, dependencies)) hooks[at] = { dependencies, value: factory() }; return hooks[at].value; },
    useEffect(effect, dependencies) { const at = cursor++; if (!hooks[at] || !same(hooks[at].dependencies, dependencies)) {
      const previous = hooks[at]; hooks[at] = { dependencies, cleanup: previous?.cleanup, effect }; effects.push(() => { hooks[at].cleanup?.(); hooks[at].cleanup = effect(); });
    } },
  };
  const jsx = (type, props) => typeof type === 'function' ? type(props) : { type, props: props ?? {} };
  const jsxModule = { jsx, jsxs: jsx, Fragment: 'fragment' };
  // Execute the real imported helpers; an unexpected draft POST is forbidden.
  const icon = load('icon', { 'react/jsx-runtime': jsxModule, './Primitives.module.css': {} });
  const draftTasks = load('draftTasks', { './appApi': { createAppRequest: forbidden('draft transport') } }, timers);
  const api = load('wizard', { react, 'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: 'fragment' }, '../styles/modes.css': {},
    './ui/Icon': icon, '../lib/draftTasks': draftTasks,
    '../lib/productionModes': modes, '../lib/mediaInput': { ...media, ...mediaApi },
    '../lib/uploadSessions': { ...uploadsHarness().api, ...uploadApi } }, { ...timers,
    window: { confirm: () => true }, navigator: {}, performance: { now: () => 100 }, setInterval: forbidden('recording timer'), clearInterval() {} });
  const inputs = { limits: { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 },
    initialDraft: draft, generativeAllowed: true, busy: false, onDraft: value => drafts.push(value),
    onSubmit: async value => { submits.push(value); }, onError() {}, ...props };
  function render() { assert.ok(live); cursor = 0; dirty = false; tree = api.CreateWizard(inputs); }
  function flush() { let count = 0; do { if (dirty || !tree) render(); while (effects.length) effects.shift()(); assert.ok(++count < 25, 'No render/effect loop'); } while (dirty); }
  function nodes(node = tree) {
    const result = [];
    const visit = item => {
      if (Array.isArray(item)) item.forEach(visit);
      else if (item && typeof item === 'object') { result.push(item); visit(item.props?.children); }
    };
    visit(node); return result;
  }
  function content(node) { if (node === undefined || node === null || typeof node === 'boolean') return ''; return Array.isArray(node) ? node.map(content).join('') : typeof node === 'object' ? content(node.props?.children) : String(node); }
  function button(label) {
    // The trailing arrow is now a real decorative SVG, not button text.
    const text = ({ '下一步：传素材 →': '下一步：传素材', '下一步：选效果 →': '下一步：选效果' })[label] ?? label;
    const found = nodes().find(node => node.type === 'button' && (content(node).trim() === text || node.props['aria-label'] === label));
    assert.ok(found, `Button not found: ${label}`); return found;
  }
  flush();
  return { api, drafts, submits, timers, nodes, content, button, flush, get tree() { return tree; },
    click(label) { const node = button(label); assert.equal(!!node.props.disabled, false, `Enabled button: ${label}`); node.props.onClick(); flush(); },
    script(value) { const node = nodes().find(node => node.type === 'textarea' && node.props.className === 'gm-script'); assert.ok(node); node.props.onChange({ target: { value } }); flush(); },
    async settle() { await microtasks(); flush(); await microtasks(); flush(); },
    replayEffects() { for (const hook of hooks) hook?.cleanup?.(); for (const hook of hooks) if (hook?.effect) hook.cleanup = hook.effect(); flush(); },
    unmount() { live = false; for (const hook of hooks) hook?.cleanup?.(); },
  };
}
// Historical unscoped uploads/picker contracts, not newly created v2 drafts.
const draft = (patch = {}) => ({ legacyRestore: true, script: '真实新闻标题\n今天活动正式开幕，欢迎大家前来参观。', step: 1, preferences: modes.defaultModePreferences(), files: [], elements: {}, sentenceChecks: {}, ...patch });
const uploadedDraft = (patch = {}) => draft({ script: '采访标题\n欢迎大家前来参观', step: 2, mode: 'original', preferences: modes.defaultModePreferences('original'),
  files: [{ id: media.fileIdentity(file()), name: 'synthetic.mp4', size: 3, lastModified: 123, type: 'video/mp4', kind: 'video', duration: 10, trim_start: 0, trim_end: 10, note: '' }],
  uploadBindings: [binding()], uploadIds: [UPLOAD], uploadTokens: { [UPLOAD]: TOKEN }, ...patch });
const readyPoll = async (id, token, signal, emit) => { emit(snapshot()); return snapshot(); };

test('wizard defaults to voiceover with three Chinese cards and keeps optional element self-checks', () => {
  const h = wizardHarness({ draft: draft() });
  assert.equal(h.drafts.at(-1).mode, 'voiceover'); assert.ok(h.nodes().some(node => node.props?.className === 'gm-elements'));
  assert.equal(h.nodes().some(node => String(node.props?.className ?? '').includes('gm-mode-more')), false, 'no hidden-mode toggle');
  const cards = h.nodes().filter(node => node.props?.className === 'gm-mode-card'); assert.equal(cards.length, 3);
  assert.equal(cards.filter(card => card.props['aria-pressed']).length, 1); assert.equal(cards[0].props['aria-pressed'], true);
  assert.ok(h.content(h.tree).includes('不是事实认证')); h.unmount();
});

test('blank original mode can enter uploads without weakening the normal-script minimum', () => {
  const a = wizardHarness(); assert.equal(a.button('下一步：传素材 →').props['aria-disabled'], true); a.unmount();
  const c = wizardHarness({ draft: draft({ script: '', mode: 'original', preferences: modes.defaultModePreferences('original') }) });
  assert.equal(c.button('下一步：传素材 →').props['aria-disabled'], false); c.click('下一步：传素材 →');
  assert.ok(c.content(c.tree).includes('选择文件即开始上传和转写')); assert.equal(c.button('下一步：选效果 →').props['aria-disabled'], true); c.unmount();
});

test('next step is never a dead button: a blocked click says what is missing, a valid one proceeds', () => {
  const h = wizardHarness();
  const next = () => h.button('下一步：传素材 →');
  // Blocked: keeps a real, focusable button (no disabled attribute) that reports why.
  assert.equal(next().props.disabled, undefined); assert.equal(next().props['aria-disabled'], true);
  assert.ok(!h.content(h.tree).includes('还不能进入下一步'), 'no scolding before the user tries');
  next().props.onClick(); h.flush();
  assert.ok(h.content(h.tree).includes('还不能进入下一步：稿件至少写 20 字'));
  assert.ok(!h.content(h.tree).includes('选择文件即开始上传和转写'), 'blocked click must not advance');
  assert.equal(h.nodes().find(node => node.props?.role === 'alert' && String(node.props.className).includes('gm-action-error')) !== undefined, true);
  // Fixing the script clears the message and the same button now proceeds.
  h.script('真实新闻标题\n今天活动正式开幕，欢迎大家前来参观。');
  assert.equal(next().props['aria-disabled'], false); assert.ok(!h.content(h.tree).includes('还不能进入下一步'));
  next().props.onClick(); h.flush();
  assert.ok(h.content(h.tree).includes('选择文件即开始上传和转写')); h.unmount();
});

test('manual mixed chips preserve text and segmentation and invalidate element checks', () => {
  const h = wizardHarness({ draft: draft({ mode: 'mixed', script: '采访新闻标题\n同期：很长的现场原声，仍然是一个完整句子。' }) });
  assert.equal(h.drafts.at(-1).sentences.length, 1);
  h.click('第 1 句：原声，改成旁白');
  assert.equal(h.drafts.at(-1).sentences.length, 1); assert.equal(h.drafts.at(-1).sentences[0].kind, 'narration');
  assert.ok(h.drafts.at(-1).script.includes('同期：')); assert.deepEqual(plain(h.drafts.at(-1).sentenceChecks), {}); h.unmount();
});

test('title-only edits and draft reload preserve manually confirmed boundaries', () => {
  const h = wizardHarness({ draft: draft({ mode: 'mixed', script: '采访新闻标题\n同期：很长的现场原声，仍然是一个完整句子。' }) });
  h.click('第 1 句：原声，改成旁白'); h.script('修改后的采访新闻标题\n同期：很长的现场原声，仍然是一个完整句子。');
  const saved = plain(h.drafts.at(-1)); assert.equal(saved.sentences.length, 1); h.unmount();
  const restored = wizardHarness({ draft: saved }); assert.equal(restored.drafts.at(-1).sentences.length, 1);
  assert.equal(restored.drafts.at(-1).sentences[0].kind, 'narration'); restored.unmount();
});

test('editing a prefix updates inferred kinds while an explicit user chip retains priority', () => {
  const h = wizardHarness({ draft: draft({ mode: 'mixed', script: '采访新闻标题\n旁白：欢迎大家前来参观今天的活动。' }) });
  h.script('采访新闻标题\n同期：欢迎大家前来参观今天的活动。'); assert.equal(h.drafts.at(-1).sentences[0].kind, 'quote');
  h.click('第 1 句：原声，改成旁白');
  h.script('采访新闻标题修改\n同期：欢迎大家前来参观今天的活动。'); assert.equal(h.drafts.at(-1).sentences[0].kind, 'narration'); h.unmount();
});

test('restored ready uploads need no reselect; C effects hide voice, pacing and generation', async () => {
  const h = wizardHarness({ draft: uploadedDraft(), uploadApi: { pollUploadStatus: readyPoll, requestMatchPreview: async () => [match()] } });
  assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true);
  await h.settle(); assert.equal(h.timers.timers.size, 0, 'Restoration is read-only until explicit alignment');
  h.click('重新核对'); h.timers.fire(600); await h.settle(); h.click('下一步：选效果 →');
  const body = h.content(h.tree);
  assert.ok(!body.includes('谁来配音')); assert.ok(!body.includes('播音速度')); assert.ok(!body.includes('AI 示意画面补充'));
  assert.ok(body.includes('跳切怎么处理')); assert.ok(body.includes('原声段的字幕'));
  h.click('开始制作'); await h.settle();
  assert.equal(h.submits.length, 1); assert.equal(h.submits[0].files.length, 0); assert.deepEqual(plain(h.submits[0].uploadIds), [UPLOAD]);
  assert.equal(h.submits[0].preferences.background_music, false); assert.equal(h.submits[0].preferences.motion_effects, false); h.unmount();
});

test('restoring invalid mode sentences/tokens retains script but never queries a forged upload binding', () => {
  const value = uploadedDraft({ sentences: [row('并不在稿子中的内容')] }); let reads = 0;
  const h = wizardHarness({ draft: value, uploadApi: { pollUploadStatus: () => { reads++; return Promise.resolve(snapshot()); } } });
  assert.equal(h.drafts.at(-1).script, value.script); assert.equal(reads, 0); assert.equal(h.drafts.at(-1).uploadIds.length, 0);
  assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true); h.unmount();
});

test('an unfinished oversize sentence does not discard valid uploads or trigger another paid transcription', async () => {
  const value = uploadedDraft({ script: '采访标题\n' + '字'.repeat(2001), sentences: [] }); let reads = 0;
  const h = wizardHarness({ draft: value, uploadApi: { pollUploadStatus: async (...args) => { reads++; return readyPoll(...args); } } });
  await h.settle(); assert.equal(reads, 1); assert.deepEqual(plain(h.drafts.at(-1).uploadIds), [UPLOAD]);
  assert.equal(h.drafts.at(-1).script, value.script); assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true); h.unmount();
});

test('effect replay cancels old restored-upload reads without losing the replacement read', async () => {
  const reads = [], h = wizardHarness({ draft: uploadedDraft(), uploadApi: {
    pollUploadStatus(id, token, signal, emit) { const response = deferred(); reads.push({ signal, emit, response }); return response.promise; },
    requestMatchPreview: async () => [match()],
  } });
  h.replayEffects(); assert.equal(reads.length, 2); assert.equal(reads[0].signal.aborted, true);
  reads[0].emit(snapshot()); reads[0].response.resolve(snapshot()); await h.settle();
  assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true);
  reads[1].emit(snapshot()); reads[1].response.resolve(snapshot()); await h.settle(); h.click('重新核对'); h.timers.fire(600); await h.settle();
  assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], false); h.unmount();
});

test('transcript picks preserve source hints and upload-scoped speaker IDs through mode switch/reload', async () => {
  const realSegment = segment('欢迎大家前来参观', { speaker_id: `${UPLOAD}:speaker1` });
  const h = wizardHarness({ draft: uploadedDraft({ script: '' }), uploadApi: {
    pollUploadStatus: async (id, token, signal, emit) => { const value = snapshot({ transcript: [realSegment] }); emit(value); return value; },
  } });
  await h.settle();
  const checkbox = h.nodes().find(node => node.type === 'input' && node.props['aria-label'] === '选入原话：欢迎大家前来参观');
  checkbox.props.onChange({ target: { checked: true } }); h.flush();
  assert.ok(h.drafts.at(-1).script.startsWith('（写一个标题）'));
  assert.equal(h.drafts.at(-1).sentences[0].source_hint.upload_id, UPLOAD);
  h.click('← 上一步');
  const card = h.nodes().find(node => node.props.className === 'gm-mode-card' && h.content(node).includes('旁白 + 原声')); card.props.onClick(); h.flush();
  const saved = plain(h.drafts.at(-1)); h.unmount();
  assert.equal(saved.sentences[0].kind, 'quote'); assert.ok(!saved.script.includes(':speaker1'));
  const restored = wizardHarness({ draft: saved, uploadApi: { pollUploadStatus: readyPoll } });
  assert.equal(restored.drafts.at(-1).sentences[0].source_hint.upload_id, UPLOAD); restored.unmount();
});

test('automatic file upload starts after a real picker action and serializes probes across actions', async () => {
  const probes = [], transfers = [];
  const h = wizardHarness({ draft: draft({ step: 2 }), mediaApi: { probeMedia(file, kind, signal) { const result = deferred(); probes.push({ file, kind, signal, result }); return result.promise; } },
    uploadApi: { uploadFile: async (file, signal) => { transfers.push(file.name); return snapshot(); } } });
  const pick = source => { const input = h.nodes().find(node => node.type === 'input' && node.props.className === 'gm-file-picker'); input.props.onChange({ target: { files: [source], value: '' } }); h.flush(); };
  assert.equal(probes.length, 0); assert.equal(transfers.length, 0);
  pick(file()); pick(file(Uint8Array.of(7), { name: 'second.mp4' })); assert.equal(probes.length, 1);
  probes[0].result.resolve({ duration: 10, width: 1280, height: 720 }); await h.settle();
  assert.deepEqual(transfers, ['synthetic.mp4']); assert.equal(probes.length, 2);
  probes[1].result.resolve({ duration: 5, width: 1280, height: 720 }); await h.settle();
  assert.deepEqual(transfers, ['synthetic.mp4', 'second.mp4']); h.unmount();
});

test('active probe removal is disabled; unmount cancels its late response without starting upload', async () => {
  const pending = deferred(); let transfer = 0, probeSignal;
  const h = wizardHarness({ draft: draft({ step: 2 }), mediaApi: { probeMedia(file, kind, signal) { probeSignal = signal; return pending.promise; } },
    uploadApi: { uploadFile: async () => { transfer++; return snapshot(); } } });
  h.nodes().find(node => node.type === 'input' && node.props.className === 'gm-file-picker').props.onChange({ target: { files: [file()], value: '' } }); h.flush();
  assert.equal(h.button('移除 synthetic.mp4').props.disabled, true);
  const before = JSON.stringify(h.drafts); h.unmount(); assert.equal(probeSignal.aborted, true);
  pending.resolve({ duration: 10 }); await microtasks();
  assert.equal(JSON.stringify(h.drafts), before); assert.equal(transfer, 0);
});

test('mixed missing quote blocks the effect step until explicitly converted, not silently voiced', async () => {
  const h = wizardHarness({ draft: uploadedDraft({ mode: 'mixed', script: '采访新闻标题\n同期：这是一段素材里面没有说过的原话。' }),
    uploadApi: { pollUploadStatus: readyPoll, requestMatchPreview: async () => [match({ source: null, score: .2 })] } });
  await h.settle(); h.click('重新核对'); h.timers.fire(600); await h.settle();
  assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true); assert.ok(h.content(h.tree).includes('没找到'));
  h.click('改成旁白'); assert.equal(h.drafts.at(-1).sentences[0].kind, 'narration');
  assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], false); h.unmount();
});

test('original cannot pass with no speech or with a matched take outside the selected source trim', async () => {
  for (const noSpeech of [true, false]) {
    const value = uploadedDraft(); if (!noSpeech) value.files[0].trim_start = 2;
    const h = wizardHarness({ draft: value, uploadApi: {
      pollUploadStatus: async (id, token, signal, emit) => { const s = snapshot(noSpeech ? { has_speech: false, transcript: [] } : {}); emit(s); return s; },
      requestMatchPreview: async () => [match()],
    } });
    await h.settle(); h.click('重新核对'); h.timers.fire(600); await h.settle(); assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true); h.unmount();
  }
});

test('preview debounce cancels old controller and late responses cannot certify an edited manuscript', async () => {
  const requests = [], h = wizardHarness({ draft: uploadedDraft(), uploadApi: { pollUploadStatus: readyPoll,
    requestMatchPreview(sentences, ids, tokens, signal) { const response = deferred(); requests.push({ response, signal }); return response.promise; } } });
  await h.settle(); assert.equal(requests.length, 0); h.click('重新核对'); h.timers.fire(600); await h.settle(); assert.equal(requests.length, 1);
  h.click('← 上一步'); h.script('采访标题改过\n欢迎大家前来参观'); assert.equal(requests[0].signal.aborted, true);
  h.timers.fire(600); await h.settle(); assert.equal(requests.length, 2);
  requests[1].response.resolve([match({ source: null, score: .1 })]); await h.settle();
  requests[0].response.resolve([match()]); await h.settle();
  h.click('下一步：传素材 →'); assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true);
  assert.ok(h.content(h.tree).includes('没找到')); h.unmount();
});

test('mode suggestion is once per wizard session and never auto-switches', async () => {
  const h = wizardHarness({ draft: uploadedDraft({ mode: 'voiceover', script: '活动新闻标题\n欢迎大家前来参观今天开幕的新活动。' }), uploadApi: {
    pollUploadStatus: readyPoll, requestMatchPreview: async () => [match()],
  } });
  // A restores read-only and has no quote recheck button; a real manuscript
  // edit explicitly enables its evidence-only suggestion request.
  await h.settle(); h.click('← 上一步'); h.script('活动新闻标题\n欢迎大家前来参观今天开幕的新活动。');
  h.timers.fire(600); await h.settle(); h.click('下一步：传素材 →');
  assert.equal(h.drafts.at(-1).mode, 'voiceover'); assert.ok(h.nodes().some(node => node.props?.className === 'gm-tip gm-mode-suggestion'));
  h.click('保持当前模式'); h.click('← 上一步'); h.script('新的活动新闻标题\n欢迎大家前来参观今天开幕的新活动。');
  h.timers.fire(600); await h.settle(); assert.ok(!h.nodes().some(node => node.props?.className === 'gm-tip gm-mode-suggestion')); h.unmount();
});

test('server-status refresh failure revokes previously ready eligibility', async () => {
  let fail = false;
  const h = wizardHarness({ draft: uploadedDraft(), uploadApi: { pollUploadStatus: async (...args) => { if (fail) throw new Error('Synthetic missing upload'); return readyPoll(...args); }, requestMatchPreview: async () => [match()] } });
  await h.settle(); h.click('重新核对'); h.timers.fire(600); await h.settle(); assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], false);
  fail = true;
  h.click('重新读取状态'); await h.settle();
  assert.equal(h.button('下一步：选效果 →').props['aria-disabled'], true); h.unmount();
});

test('paper CSS consumes the authoritative v2 palette, rem geometry and shared SVG icons without remote assets', () => {
  const css = read('../src/styles/modes.css'), root = postcss.parse(css);
  const palette = postcss.parse(read('../src/components/ui/tokens.css'));
  const tokens = Object.fromEntries(palette.nodes.find(node => node.selector === ':root, .shell-layout').nodes.map(node => [node.prop, node.value]));
  const expected = { ink: '#4a3626', muted: '#75645a', line: '#dccfbb', teal: '#237f4a', red: '#c94a2c', gold: '#f2b632', paper: '#f9f3e6', wash: '#f4ecdf', green: '#e7f3ea', redwash: '#fbeee6', yellow: '#fdf2d8', track: '#f0e7d8' };
  for (const [name, value] of Object.entries(expected)) {
    assert.equal(tokens[`--${name}`], value); assert.equal(tokens[`--gm-${name}`], `var(--${name})`);
  }
  assert.equal(root.nodes.filter(node => node.type === 'atrule' && node.name === 'import').length, 1);
  assert.equal(root.nodes.find(node => node.type === 'atrule' && node.name === 'import').params, '"../components/ui/tokens.css"');
  root.walkDecls(/^--gm-/, () => assert.fail('Wizard must not override shared palette'));
  root.walkDecls(declaration => {
    if (declaration.parent.selector === '.gm-create-wizard' && declaration.prop === 'max-width') assert.equal(declaration.value, '960px');
    else assert.ok(!/\dpx\b/.test(declaration.value), `Use rem for ${declaration.prop}`);
  });
  for (const width of [600, 900, 1000]) assert.ok(css.includes(`max-width:${width}px`));
  assert.ok(!/@font-face|url\(/.test(css)); assert.ok(!/lucide|wizardCss|<style>/.test(sources.wizard));
  assert.match(sources.wizard, /import SharedIcon from "\.\/ui\/Icon"/);
  assert.match(sources.wizard, /import "\.\.\/styles\/modes\.css"/);
});