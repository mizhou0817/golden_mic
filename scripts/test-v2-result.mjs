import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
const require = createRequire(new URL('../frontend/package.json', import.meta.url));
const ts = require('typescript'), React = require('react'), { renderToStaticMarkup } = require('react-dom/server');
const read = path => readFileSync(new URL('../' + path, import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
let networkAttempts = 0;
function forbiddenNetwork() { networkAttempts++; throw Error('Unexpected network access'); }
after(() => assert.equal(networkAttempts, 0, 'All transport remains synthetic; no VM network attempts'));
function load(path, modules = {}, extra = {}) {
  const source = read(path);
  const result = ts.transpileModule(source, { fileName: path, reportDiagnostics: true, compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  }, transformers: { before: [context => root => {
    const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
      && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
      ? ts.factory.createIdentifier('__testViteEnv') : ts.visitEachChild(node, visit, context);
    return ts.visitNode(root, visit);
  }] } });
  assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  const module = { exports: {} };
  const sandbox = { module, exports: module.exports, URL, FormData, File, AbortController, setTimeout, clearTimeout,
    __testViteEnv: { DEV: false }, ...extra,
    fetch: forbiddenNetwork, WebSocket: forbiddenNetwork, XMLHttpRequest: forbiddenNetwork,
    require(name) {
      if (Object.hasOwn(modules, name)) return modules[name];
      if (name === 'react' || name === 'react/jsx-runtime') return require(name);
      throw Error('Unexpected dependency: ' + name);
    } };
  vm.runInNewContext(result.outputText, sandbox, { filename: path, timeout: 2000 });
  return module.exports;
}
const api = load('frontend/src/lib/workbenchApi.ts');
const appApi = load('frontend/src/lib/appApi.ts');
const modes = load('frontend/src/lib/productionModes.ts', {
  '../../../backend/mode_rules.json': { default: JSON.parse(read('backend/mode_rules.json')) },
});
const quality = load('frontend/src/lib/qualitySummary.ts', { './productionModes': modes, './workbenchApi': api });
const icon = load('frontend/src/components/ui/Icon.tsx', { './Primitives.module.css': { default: {} } });
const pend = load('frontend/src/lib/pendingEdits.ts', { './workbenchApi': api });
const quote = load('frontend/src/components/QuoteEditor.tsx', { '../lib/workbenchApi': api });
const resultModules = { '../lib/workbenchApi': api, '../lib/pendingEdits': pend,
  '../lib/productionModes': modes, '../lib/appApi': appApi, '../lib/qualitySummary': quality,
  './QuoteEditor': quote, './ui/Icon': icon, './ResultWorkbench.css': {} };
const TASK = 'a'.repeat(32), JOB = 'b'.repeat(32), RECORD = 'c'.repeat(32);
const store = (initial = {}) => { let raw = JSON.stringify(initial); return { getItem: () => raw, setItem: (_, v) => { raw = v; }, value: () => JSON.parse(raw) }; };
const draft = (base = 2) => ({ ...pend.emptyPending(), base, sentences: { 1: { text: 'changed' } } });
const batch = () => ({ expected_revision: 2, keep_sentence_ids: [1, 2], edits: [{ sentence_id: 1, text: 'changed', instruction: 'a person interviewing' }] });
const gate = () => ({ revision: 2, passed: true, blocking_count: 0, pending_count: 0, checks: [] });
const steps = () => [{ kind: 'remix', stages: [7, 8, 9, 10], state: 'pending' }, { kind: 'replace', row_id: 1, stages: [6, 9, 10], state: 'pending' }];
const applyReceipt = () => ({ task_id: TASK, operation_id: JOB, revision: 4, current_revision: 2, status: 'queued', steps: steps() });

test('pending projection excludes unknown tokens, URLs, files, stack and take metadata', () => {
  const d = draft(); d.token = 'private'; d.stack = 'Traceback'; d.sentences[1].file = new File(['audio'], 'private.wav');
  d.sentences[2] = { text: 'https://secret.test?token=private', instruction: 'Bearer secret' };
  d.pendTake[3] = { take_id: 'take-1', upload_id: 'private', asr_text: 'private', url: 'blob:private' };
  d.pendTake[4] = { take_id: 'blob:private' };
  const projected = pend.projectPending(d), serialized = JSON.stringify(projected);
  assert.equal(/private|https:|Bearer|stack|file|upload_id|asr_text/.test(serialized), false);
  assert.deepEqual(plain(projected.pendTake), { 3: { take_id: 'take-1' } });
});
test('recordings persist only validated identities bound to row and revision', () => {
  const d = draft(); d.sentences[1].recording = { recording_id: RECORD, sentence_id: 1, revision: 2, duration: 3, size: 200,
    audio_source: 'recording', transcript_verified: false, url: 'private' };
  assert.equal(pend.projectPending(d).sentences[1].recording.recording_id, RECORD);
  for (const patch of [{ revision: 1 }, { sentence_id: 2 }, { recording_id: 'blob:bad' }, { size: 0 }, { duration: Infinity }, { transcript_verified: 'true' }]) {
    const copy = structuredClone(d); Object.assign(copy.sentences[1].recording, patch);
    assert.equal(pend.projectPending(copy).sentences[1].recording, undefined);
  }
});
test('latest-read merge and discard preserve Workspace, draft, checked, legacy and other tasks', () => {
  const storage = store({ schema: 2, workspace: { value: 1 }, draft: { content: 'keep' }, checked: { key: true }, pend: { legacyTask: { legacy: true } } });
  pend.writePending(TASK, draft(), storage);
  const concurrent = storage.value(); concurrent.workspace.value = 2; concurrent.checked.new = true;
  storage.setItem('', JSON.stringify(concurrent));
  pend.writePending('other', draft(9), storage); pend.writePending(TASK, null, storage);
  const root = storage.value(); assert.equal(root.workspace.value, 2); assert.deepEqual(root.checked, { key: true, new: true });
  assert.deepEqual(root.pend.legacyTask, { legacy: true }); assert.deepEqual(root.draft, { content: 'keep' });
  assert.equal(pend.readPending(TASK, storage), null); assert.equal(pend.readPending('other', storage).base, 9);
});
test('malformed legacy storage is not overwritten', () => {
  for (const root of [[], { pend: [] }, { pend: { resultV2: 4 } }]) {
    const storage = store(root), before = storage.getItem();
    assert.throws(() => pend.writePending(TASK, draft(), storage)); assert.equal(storage.getItem(), before);
  }
});
test('restore keeps old revision, does not rebase or perform network work', () => {
  const storage = store(); pend.writePending(TASK, draft(1), storage);
  assert.equal(pend.readPending(TASK, storage).base, 1);
  assert.equal(pend.projectPending({ ...draft(), base: null }), null);
});
test('unavailable storage cannot crash initial render and failed debounce remains retryable', () => {
  assert.equal(pend.readPending(TASK), null, 'VM intentionally has no localStorage.');
  const storage = store(); let callback, unavailable = true;
  const guarded = { getItem: storage.getItem, setItem(key, value) { if (unavailable) throw Error('quota'); storage.setItem(key, value); } };
  const writer = pend.pendingWriter(TASK, guarded, { set(fn) { callback = fn; return 1; }, clear() {} });
  writer.schedule(draft()); assert.doesNotThrow(() => callback()); assert.throws(() => writer.flush());
  unavailable = false; writer.flush(); assert.equal(pend.readPending(TASK, storage).base, 2);
  assert.throws(() => pend.writePending('__proto__', draft(), storage));
});
test('300ms debounce flushes latest draft and explicit flush cancels timer', () => {
  const storage = store(); let callback, delay, clears = 0;
  const writer = pend.pendingWriter(TASK, storage, { set(fn, ms) { callback = fn; delay = ms; return 1; }, clear() { clears++; } });
  writer.schedule(draft()); writer.schedule(draft(4)); assert.equal(delay, 300); assert.equal(pend.readPending(TASK, storage), null);
  callback(); assert.equal(pend.readPending(TASK, storage).base, 4);
  writer.schedule(null); writer.flush(); assert.equal(pend.readPending(TASK, storage), null); assert.ok(clears >= 2);
});
test('v2 apply uses strict row maps and preserves deletion/trim/take/voice/speaker semantics', () => {
  const b = { expected_revision: 2, keep_sentence_ids: [1, 2, 3, 4], edits: [{ sentence_id: 1, text: 'new', recording_id: RECORD, instruction: 'interview' }],
    quote_trims: [{ id: 2, start: 10, end: 12 }], quote_takes: [{ id: 3, take_id: 'take-3' }], to_narration: [4],
    speakers: [{ id: 'spk1', name: 'Name', title: 'Role', auto_label: '', appearances: 1, seconds: 3 }], pacing: 'slow' };
  assert.deepEqual(plain(api.applyBody(b, [1, 2, 3, 4, 5], 'mixed')), { expected_revision: 2, deleted: [5], edits: { 1: 'new' },
    voices: { 1: RECORD }, pacing: 'slow', trims: { 2: { t0: 10, t1: 12 } }, takes: { 3: 'take-3' }, to_narration: [4],
    speakers: { spk1: { name: 'Name', role: 'Role' } }, replaces: { 1: { instruction: 'interview' } } });
});
test('apply rejects direct shot IDs, stale rows, original narration and conflicting quote edits', () => {
  assert.throws(() => api.applyBody({ ...batch(), edits: [{ sentence_id: 1, shot_id: 2 }] }, [1, 2], 'voiceover'));
  assert.throws(() => api.applyBody(batch(), [1], 'voiceover'));
  assert.throws(() => api.applyBody(batch(), [1, 2], 'original'));
  assert.throws(() => api.applyBody({ ...batch(), quote_trims: [{ id: 1, start: 0, end: 2 }] }, [1, 2], 'mixed'));
});
test('apply single POST adopts real plan; failed writes never retry or fallback', async () => {
  const calls = [], client = api.createWorkbenchApi(TASK, async (path, init) => {
    calls.push([path, init]); return applyReceipt();
  });
  const receipt = await client.apply(batch(), [1, 2], 'voiceover'); assert.equal(receipt.revision, 4);
  assert.deepEqual(plain(receipt.plan.steps), steps());
  assert.equal(calls.length, 1); assert.ok(calls[0][0].endsWith('/apply'));
  let count = 0; const failed = api.createWorkbenchApi(TASK, async () => { count++; throw Error('network'); });
  await assert.rejects(failed.apply(batch(), [1, 2], 'voiceover')); assert.equal(count, 1);
});
test('hard blockers cannot be confirmed; generated disclosure is the sole explicit exception', () => {
  for (const code of ['MATCH_FALLBACK', 'QUOTE_NOT_FOUND', 'FREEZE_PAD_EXCESSIVE']) assert.equal(api.canConfirmCheck({ level: 0, code, confirmable: true }), false);
  assert.equal(api.canConfirmCheck({ level: 0, code: 'generated_media', confirmable: true }), true);
  assert.equal(api.serverAllowsExport(gate(), 3), false);
  assert.throws(() => api.parseChecks({ ...gate(), checks: [{ key: 'x', code: 'MATCH_FALLBACK', level: 0, checked: true, message: 'blocked' }] }));
});
test('check confirmation uses PUT and binds revision', async () => {
  const checks = { ...gate(), checks: [{ key: 'fact', code: 'FACT_CHECK', level: 1, checked: false, message: 'verify', sentence_id: 1, action: 'view' }] };
  let call; const client = api.createWorkbenchApi(TASK, async (path, init) => { call = { path, init }; return { ...checks, checks: [{ ...checks.checks[0], checked: true }] }; });
  await client.confirmCheck(checks, 'fact', true); assert.equal(call.init.method, 'PUT'); assert.equal(JSON.parse(call.init.body).expected_revision, 2);
});
test('all five exports use POST /exports and real revision-bound job identity', async () => {
  assert.deepEqual(plain(api.SIMPLE_EXPORT_FORMATS), ['mp4', 'gif', 'mp3', 'srt', 'png']);
  for (const format of api.SIMPLE_EXPORT_FORMATS) {
    let call; const client = api.createWorkbenchApi(TASK, async (path, init) => { call = { path, init }; return { export_id: JOB, revision: 2, state: 'queued' }; });
    const job = await client.export({ ...api.DEFAULT_EXPORT, format, frame_time: 1.234 }, 2);
    assert.equal(job.id, JOB); assert.equal(job.pipeline_revision, 2); assert.ok(call.path.endsWith('/exports')); assert.equal(call.init.method, 'POST');
    const body = JSON.parse(call.init.body); assert.equal(body.expected_revision, 2); assert.equal(body.sub, 'std');
    assert.equal(body.frame_seconds, format === 'png' ? 1.234 : undefined);
  }
});
test('invalid PNG time and mismatched export revision fail closed without retry', async () => {
  assert.throws(() => api.simpleExportBody({ ...api.DEFAULT_EXPORT, format: 'png', frame_time: null }));
  let count = 0; const client = api.createWorkbenchApi(TASK, async () => { count++; return { export_id: JOB, revision: 3, state: 'queued' }; });
  await assert.rejects(client.export(api.DEFAULT_EXPORT, 2)); assert.equal(count, 1);
});
test('successful receipt requires same output ID and valid actual file facts', () => {
  const job = { export_id: JOB, revision: 2, state: 'succeeded', output_id: JOB,
    options: { fmt: 'png', res: '1080p', aspect: '16:9', sub: 'standard', frame_seconds: 1.25 }, result: { bytes: 1200, duration: 2 } };
  assert.equal(api.parseJob(job).output_id, JOB);
  assert.throws(() => api.parseJob({ ...job, output_id: RECORD }));
  assert.throws(() => api.parseJob({ ...job, result: { bytes: -1, duration: 2 } }));
  assert.throws(() => api.parseJob({ ...job, result: undefined }));
});
test('status and cancellation cannot replace a receipt with another revision', async () => {
  let calls = 0;
  const client = api.createWorkbenchApi(TASK, async () => { calls++; return { export_id: JOB, revision: 3, state: 'cancelled',
    options: { fmt: 'mp4', res: '1080p', aspect: '16:9', sub: 'standard' } }; });
  await assert.rejects(client.job(JOB, undefined, 2));
  await assert.rejects(client.cancelExport(JOB, undefined, 2));
  assert.equal(calls, 2, 'One request per explicit operation; no mutation retry.');
});
test('completed-operation identity survives refresh without pending edits or raw failures', () => {
  const storage = store(), d = { ...pend.emptyPending(), base: 3, lastOperation: { id: JOB, revision: 3, stack: 'private' } };
  pend.writePending(TASK, d, storage);
  assert.deepEqual(plain(pend.readPending(TASK, storage).lastOperation), { id: JOB, revision: 3 });
  assert.equal(pend.readPending(TASK, storage).submitted, undefined);
  assert.equal(pend.projectPending({ ...d, lastOperation: { id: JOB, revision: 4 } }).lastOperation, undefined);
});
test('recording staging has real multipart fields and validated revision-bound receipt', async () => {
  let call; const client = api.createWorkbenchApi(TASK, async (path, init) => { call = { path, init }; return { recording_id: RECORD, sentence_id: 1, revision: 2,
    duration: 2, size: 5, transcript_verified: false, audio_source: 'recording' }; });
  const result = await client.upload(1, 2, new File(['audio'], 'voice.webm')); assert.equal(result.recording_id, RECORD);
  assert.ok(call.path.endsWith('/recordings')); assert.equal(call.init.body.get('rowid'), '1'); assert.equal(call.init.body.get('expected_revision'), '2');
  assert.equal(call.init.body.get('audio').size, 5); assert.equal(api.recordingLimit(4), 11); assert.equal(api.recordingLimit(80), 119);
});
test('restore sends rev and expected_revision exactly once', async () => {
  let call; const client = api.createWorkbenchApi(TASK, async (path, init) => { call = { path, init }; return { task_id: TASK, revision: 3, current_revision: 2, status: 'queued' }; });
  await client.restore(0, 2); assert.ok(call.path.endsWith('/restore')); assert.deepEqual(JSON.parse(call.init.body), { rev: 0, expected_revision: 2 });
});
test('row starts use absolute final-film clocks, preserving authored gaps', () => {
  assert.equal(api.rowStart({ sentence_id: 2, duration: 4, start: 9 }, [{ sentence_id: 2, start: 12.5 }]), 12.5);
  assert.equal(api.rowStart({ sentence_id: 2, duration: 4 }, []), null);
});
const take = { take_id: 'take', upload_id: 'upload', speaker_id: 's1', start: 10, end: 13,
  precision: 'word', asr_text: 'abc', words: [{ w: 'a', s: 10, e: 11 }, { w: 'b', s: 11.1, e: 12 }, { w: 'c', s: 12.2, e: 13 }], score: 1 };
test('word trim is immutable, contiguous, bounded and at least one second', () => {
  const source = structuredClone(take), before = JSON.stringify(source);
  assert.equal(quote.quoteRangeProblem(source, { start: 11.1, end: 13 }), '');
  assert.notEqual(quote.quoteRangeProblem(source, { start: 11.2, end: 13 }), '');
  assert.notEqual(quote.quoteRangeProblem(source, { start: 12.2, end: 13 }), '');
  assert.equal(JSON.stringify(source), before);
  assert.equal(quote.quoteTrimEvidence({ ...source, precision: 'segment' }).words.length, 0);
});
test('two .2s nudges snap real boundaries, cannot exceed saved source interval', () => {
  const range = { start: 11.1, end: 13 }, next = quote.nudgeQuoteRange(take, range, 'start', -1);
  assert.equal(next.start, 10); assert.equal(range.start, 11.1);
  assert.equal(quote.nudgeQuoteRange(take, next, 'start', -1), next);
  assert.equal(quote.nudgeQuoteRange(take, range, 'end', 1), range);
});
test('real operation receipt exposes only recognized none/used/abstract failures', async () => {
  const client = api.createWorkbenchApi(TASK, async () => ({ operation_id: JOB, revision: 3, state: 'succeeded', steps: steps(), replace_failures: { 1: 'none', 2: 'used', 3: 'abstract', 4: 'stack private' } }));
  const result = await client.operation(JOB, 3);
  assert.deepEqual(Object.keys(result.failures), ['1', '2', '3']); assert.equal(result.complete, false);
  await assert.rejects(client.operation(JOB, 4));
});
test('read-only sample SSR has real video, no management/Studio/PDF or duplicate burned overlays', () => {
  const component = load('frontend/src/components/ResultWorkbench.tsx', resultModules);
  const sample = { task_id: TASK, mode: 'voiceover', quality: null, rows: [{ sentence_id: 1, sentence: 'sample sentence', duration: 3,
    shot_id: 1, is_fallback: false, audio_kind: 'tts', visual_beats: [] }] };
  const html = renderToStaticMarkup(React.createElement(component.default, { taskId: TASK, sample, request: () => { throw Error('No sample requests'); },
    onProcessing() {}, onChanged() {}, onDelete() {}, onNew() {}, onError() {} }));
  assert.match(html, /<video/); assert.match(html, /type="range"/); assert.match(html, /gm-rw-watermark/);
  assert.doesNotMatch(html, /专业剪辑台|打印|PDF|直接选镜|gm-rw-lower-third/);
  assert.ok(html.indexOf('gm-rw-qc-summary') < html.indexOf('gm-rw-card gm-rw-selected'));
  assert.doesNotMatch(html, />删除<|>复制一份<|>我来读<|<dialog|gm-rw-cell-dot/);
});
test('source contracts: explicit recording countdown, flush lifecycle, real controls and responsive layout', async () => {
  const source = read('frontend/src/components/ResultWorkbench.tsx'), css = read('frontend/src/components/ResultWorkbench.css');
  assert.match(source, /count\(3\)/); assert.match(source, /recordingLimit\(row.duration\) \* 1000/);
  assert.match(source, /visibilitychange/); assert.match(source, /writer.flush\(\)/);
  // Handoff sentence panel: show three candidates by default, then the exact remainder.
  // Exercise the complete editor rather than requiring one spelling of its condition.
  for (const count of [0, 3, 4, 5]) {
    const h = editorHarness();
    try {
      h.state.ctx.shots = Array.from({ length: count }, (_, i) => ({ shot_id: i + 10,
        description: `candidate ${i}`, media_origin: 'source', selectable: true, unused: true,
        used_by_sentence_id: null, duration: 3, start: 0, end: 3, source_index: i, source_scene_index: 0 }));
      h.render(); await h.settle();
      const controls = () => {
        const panel = flat(h.tree()).find(n => n?.props?.className === 'gm-rw-candidates');
        assert.ok(panel, 'Candidate panel is rendered');
        return flat(panel).filter(n => n?.type === 'button');
      };
      const choices = () => controls().filter(n => Object.hasOwn(n.props, 'aria-pressed'));
      const more = () => controls().find(n => Object.hasOwn(n.props, 'aria-expanded'));
      const expected = h.state.ctx.shots.map(s => String(s.shot_id));
      assert.deepEqual(choices().map(n => n.key), expected.slice(0, 3));
      if (count <= 3) { assert.equal(more(), undefined); continue; }
      assert.equal(renderedText(more()), `还有 ${count - 3} 个镜头 ▾`);
      assert.equal(more().props.type, 'button'); assert.equal(more().props['aria-expanded'], false);
      more().props.onClick(); await h.settle();
      assert.deepEqual(choices().map(n => n.key), expected);
      assert.equal(more().props['aria-expanded'], true); assert.equal(renderedText(more()), '收起 ▴');
      more().props.onClick(); await h.settle();
      assert.deepEqual(choices().map(n => n.key), expected.slice(0, 3));
      assert.equal(more().props['aria-expanded'], false);
      assert.equal(renderedText(more()), `还有 ${count - 3} 个镜头 ▾`);
      assert.equal(h.calls.filter(c => c.init?.method && c.init.method !== 'GET').length, 0);
    } finally { h.stop(); }
  }
  assert.match(source, /先勾选确认，再导出/); assert.match(source, /开始导出/); assert.match(source, /export-unresolved/);
  assert.match(css, /max-width: 1000px/); assert.match(css, /height: 6.5rem/); assert.match(css, /scroll-snap-type: x proximity/);
  assert.match(css, /var\(--teal, #237f4a\)/); assert.match(css, /var\(--gold, #f2b632\)/);
});

test('multi-step apply rejects arbitrary future revisions, mismatched stages, rows and current base', async () => {
  for (const patch of [ { revision: 3 }, { revision: 5 }, { current_revision: 1 }, { operation_id: 'bad' },
    { steps: [] }, { steps: [steps()[0]] }, { steps: [steps()[1], steps()[0]] },
    { steps: [steps()[0], { ...steps()[1], row_id: 2 }] },
    { steps: [{ ...steps()[0], stages: [7, 9, 10] }, steps()[1]] },
    { steps: [{ ...steps()[0], kind: 'unknown' }, steps()[1]] },
    { steps: [{ ...steps()[0], state: 'succeeded' }, steps()[1]] } ]) {
    let calls = 0;
    const client = api.createWorkbenchApi(TASK, async () => { calls++; return { ...applyReceipt(), ...patch }; });
    await assert.rejects(client.apply(batch(), [1, 2], 'voiceover')); assert.equal(calls, 1);
  }
  const client = api.createWorkbenchApi(TASK, async () => ({ ...applyReceipt(), steps: [{ ...steps()[0], kind: 'recompose' }, steps()[1]] }));
  assert.equal((await client.apply(batch(), [1, 2], 'voiceover')).revision, 4);
});
test('speaker-only, conversion, replace-only and explicit recomposition have exact known stage contracts', async () => {
  const cases = [
    [{ ...batch(), edits: [], speakers: [{ id: 's1', name: 'N', title: 'R', auto_label: '', appearances: 1, seconds: 3 }] }, [{ kind: 'remix', stages: [8, 9, 10], state: 'pending' }], 'mixed'],
    [{ ...batch(), edits: [], to_narration: [1] }, [{ kind: 'recompose', stages: [6, 7, 8, 9, 10], state: 'pending' }], 'mixed'],
    [{ ...batch(), edits: [{ sentence_id: 1, instruction: 'person' }] }, [steps()[1]], 'voiceover'],
    [{ ...batch(), edits: [] }, [{ kind: 'remix', stages: [9, 10], state: 'pending' }], 'voiceover'],
    [{ ...batch(), edits: [] }, [{ kind: 'recompose', stages: [9, 10], state: 'pending' }], 'voiceover'],
  ];
  for (const [body, plan, mode] of cases) {
    const client = api.createWorkbenchApi(TASK, async () => ({ ...applyReceipt(), revision: 3, steps: plan }));
    assert.equal((await client.apply(body, [1, 2], mode)).revision, 3);
  }
  const client = api.createWorkbenchApi(TASK, async () => ({ ...applyReceipt(), revision: 3, steps: [] }));
  await assert.rejects(client.apply({ ...batch(), edits: [] }, [1, 2], 'voiceover'));
});
test('legacy edit, remix and restore retain strict +1 even with a valid multi-step plan', async () => {
  for (const method of ['edit', 'remix', 'restore']) {
    let revision = 4;
    const client = api.createWorkbenchApi(TASK, async () => ({ ...applyReceipt(), revision }));
    const invoke = () => method === 'restore' ? client.restore(0, 2) : method === 'remix' ? client.remix(batch(), 'voiceover') : client.edit(batch());
    await assert.rejects(invoke()); revision = 3; assert.equal((await invoke()).revision, 3);
  }
});
test('pending storage retains exact validated multi-step target; no arbitrary future revision', () => {
  const storage = store(), d = { ...draft(), submitted: 4, operationId: JOB, submittedPlan: steps() };
  pend.writePending(TASK, d, storage); assert.equal(pend.readPending(TASK, storage).submitted, 4);
  assert.deepEqual(plain(pend.readPending(TASK, storage).submittedPlan), steps());
  for (const patch of [{ submitted: 5 }, { submittedPlan: undefined }, { submittedPlan: [] }, { operationId: undefined }]) {
    assert.equal(pend.projectPending({ ...d, ...patch }).submitted, undefined);
  }
});
test('pending writer detects same-slot conflicts, unknown schemas and synchronous readback races', () => {
  const storage = store(); pend.writePending(TASK, draft(), storage);
  const writer = pend.pendingWriter(TASK, storage, { set: () => 1, clear() {} }); writer.schedule(draft(3));
  pend.writePending(TASK, draft(8), storage); const latest = storage.getItem();
  assert.throws(() => writer.flush()); assert.equal(storage.getItem(), latest);
  for (const root of [{ schemaVersion: 2, pend: {} }, { pend: { resultV2: { [TASK]: { schema: 3, task_id: TASK } } } }]) {
    const s = store(root), raw = s.getItem(); assert.throws(() => pend.writePending(TASK, draft(), s)); assert.equal(s.getItem(), raw);
  }
  const racing = store(); let reads = 0;
  const r = { getItem() { if (++reads === 3) racing.setItem('', JSON.stringify({ checked: { foreign: true } })); return racing.getItem(); }, setItem: racing.setItem };
  assert.throws(() => pend.writePending(TASK, draft(), r)); assert.equal(racing.value().checked.foreign, true);
});
test('actual shell persistence interleaves with pending writer without dropping either owned slot', () => {
  const shell = load('frontend/src/lib/v2Persistence.ts'), storage = store();
  // Separate keys are essential: legacy reads are not aliases of gm-modes-v1.
  const adapter = { getItem: key => key === pend.PENDING_KEY ? storage.getItem() : null, setItem: storage.setItem };
  adapter.setItem('', JSON.stringify({ pend: {}, checked: { a: true } }));
  const persistence = shell.createV2Persistence(() => adapter), writer = pend.pendingWriter(TASK, adapter, { set: () => 1, clear() {} });
  persistence.readDraft(); persistence.readPreferences(); writer.schedule(draft());
  persistence.writeDraft({ script: 'kept' }, 1); writer.flush(); persistence.writePreference('histSort', 'title');
  writer.schedule(draft(3)); writer.flush(); persistence.writeDraft({ script: 'new' }, 2);
  assert.equal(storage.value().draft.script, 'new'); assert.equal(storage.value().histSort, 'title');
  assert.equal(storage.value().checked.a, true); assert.equal(pend.readPending(TASK, adapter).base, 3);
});
test('both canonical and legacy subtitle values parse, PNG clock exact, private job metadata discarded', () => {
  for (const [sub, expected] of [['std', 'standard'], ['big', 'large'], ['standard', 'standard'], ['large', 'large'], ['none', 'none']]) {
    const raw = { export_id: JOB, revision: 2, state: 'running', source_sha256: { private: 'secret' }, publication: { private: true },
      options: { fmt: 'png', res: '1080p', aspect: '16:9', sub, frame_seconds: 1.23456789, expected_revision: 2 } };
    const job = api.parseJob(raw); assert.equal(job.options.subtitles, expected); assert.equal(job.options.frame_time, 1.23456789);
    assert.doesNotMatch(JSON.stringify(job), /source_sha256|publication|secret|private/);
    assert.equal(api.simpleExportBody(job.options).frame_seconds, 1.23456789);
    assert.throws(() => api.parseJob({ ...raw, options: { ...raw.options, frame_seconds: Infinity } }));
  }
  assert.equal(api.simpleExportBody({ ...api.DEFAULT_EXPORT, subtitles: 'large' }).sub, 'big');
});
test('verified recording receipt preserves server CPM and bounded word clocks without fabricating evidence', async () => {
  const raw = { recording_id: RECORD, sentence_id: 1, revision: 2, duration: 2, size: 5, audio_source: 'recording', transcript_verified: true,
    speaking_rate_cpm: 247.1, words: [{ text: 'real', start: .2, end: 1.4 }], private: 'not stored' };
  const client = api.createWorkbenchApi(TASK, async () => raw), receipt = await client.upload(1, 2, new File(['audio'], 'voice.wav'));
  assert.equal(receipt.transcript_verified, true); assert.equal(receipt.speaking_rate_cpm, 247.1);
  const projected = pend.projectPending({ ...draft(), sentences: { 1: { recording: receipt } } });
  assert.deepEqual(plain(projected.sentences[1].recording), { ...plain(receipt) }); assert.equal(receipt.private, undefined);
  const minimal = api.parseRecordingReceipt({ ...raw, words: undefined, speaking_rate_cpm: undefined }, 1, 2);
  assert.equal('words' in minimal, false); assert.equal('speaking_rate_cpm' in minimal, false);
  for (const patch of [{ speaking_rate_cpm: Infinity }, { speaking_rate_cpm: 0 }, { words: [{ text: 'a', start: 0, end: 2.1 }] },
    { words: [{ text: 'a', start: -.1, end: 1 }] }, { words: [{ text: 'a', start: 0, end: 1 }, { text: 'b', start: .5, end: 2 }] }]) {
    assert.throws(() => api.parseRecordingReceipt({ ...raw, ...patch }, 1, 2));
  }
});
test('public errors never echo speech engine/state codes or raw paths', () => {
  for (const message of ['ASR processing_failed', '录音 ASR 校验失败', 'TTS_FAILED', 'Traceback private', 'failed', '录音 C:\\private\\file']) {
    const visible = api.workbenchErrorMessage({ message }); assert.doesNotMatch(visible, /ASR|TTS|processing_failed|Traceback|private|failed/);
  }
  assert.match(api.workbenchErrorMessage({ status: 409, message: 'private' }), /已有变化/);
});

// In-memory hook runner executes the actual editor and API boundary. HTTP and
// visibility are explicitly controlled fixtures, not live-server/browser proof.
function hooks() {
  let cursor = 0, dirty = true; const slots = [], effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    Fragment: React.Fragment,
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, next => { const value = typeof next === 'function' ? next(slots[i].value) : next; if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; } }]; },
    useRef(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { current: initial }; return slots[i]; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: fn(), deps }; return slots[i].value; },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps, cleanup: old?.cleanup };
      effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); } },
  };
  return { react, begin() { cursor = 0; dirty = false; }, dirty: () => dirty,
    effects() { for (const fn of effects.splice(0)) fn(); }, stop() { for (const s of slots) s?.cleanup?.(); } };
}
const flat = tree => tree == null || typeof tree === 'boolean' ? [] : Array.isArray(tree) ? tree.flatMap(flat)
  : typeof tree !== 'object' ? [tree] : [tree, ...flat(tree.props?.children)];
const renderedText = tree => flat(tree).filter(n => typeof n === 'string' || typeof n === 'number').join('');
const contextFixture = () => ({ task_id: TASK, revision: 2, status: 'done', script: 'two sentences', preferences: { pacing: 'normal' }, last_operation_error: null,
  mode: 'voiceover', report: { task_id: TASK, revision: 2, quality: null, rows: [1, 2].map(id => ({ sentence_id: id, sentence: 'sentence ' + id, duration: 3,
    shot_id: id, is_fallback: false, audio_kind: 'tts', visual_beats: [] })) },
  timings: [1, 2].map(id => ({ sentence_id: id, start: (id - 1) * 3, end: id * 3, duration: 3, audio_kind: 'tts', audio_source: 'tts' })),
  current_shots: [], shots: [], versions: [{ revision: 0, label: 'initial', created_at: '' }, { revision: 2, label: 'apply', created_at: '' }] });
function editorHarness(seed) {
  const h = hooks(), storage = store(), listeners = new Map(), timers = new Map(), calls = []; let clock = 0, tree;
  if (seed) pend.writePending(TASK, seed, storage);
  const state = { ctx: contextFixture(), checks: gate(), operation: { operation_id: JOB, revision: 4, state: 'succeeded',
    steps: steps().map(s => ({ ...s, state: 'succeeded' })), replace_failures: {} }, changed: 0, processing: 0 };
  const doc = { hidden: false, addEventListener(k, fn) { if (!listeners.has(k)) listeners.set(k, new Set()); listeners.get(k).add(fn); },
    removeEventListener(k, fn) { listeners.get(k)?.delete(fn); }, getElementById: () => null };
  const globals = { localStorage: storage, document: doc, window: { addEventListener() {}, removeEventListener() {}, confirm: () => true,
    matchMedia: () => ({ matches: false }) }, setTimeout(fn, delay) { const id = ++clock; timers.set(id, { fn, delay }); return id; }, clearTimeout(id) { timers.delete(id); } };
  const pendingApi = load('frontend/src/lib/pendingEdits.ts', { './workbenchApi': api }, globals);
  const component = load('frontend/src/components/ResultWorkbench.tsx', {
    ...resultModules, react: h.react, '../lib/pendingEdits': pendingApi,
  }, globals);
  const props = { taskId: TASK, onChanged() { state.changed++; }, onProcessing() { state.processing++; }, onDelete() {}, onNew() {}, onError() {},
    async request(path, init) {
      calls.push({ path, init });
      if (path.endsWith('/workbench/context')) return structuredClone(state.ctx);
      if (path.endsWith('/checks')) return structuredClone(state.checks);
      if (path.endsWith('/apply')) return applyReceipt();
      if (path.endsWith('/exports') && init?.method === 'POST') return structuredClone(state.exportReply ?? { export_id: JOB, revision: state.ctx.revision, state: 'queued', output_id: null,
        options: JSON.parse(init.body), error: null });
      if (path.endsWith('/restore')) return { task_id: TASK, current_revision: state.ctx.revision, revision: state.ctx.revision + 1, status: 'queued' };
      if (path.includes('/operations/')) return structuredClone(state.operation);
      if (path.endsWith('/versions/0')) return { revision: 0, report: { ...structuredClone(state.ctx.report), revision: 0 } };
      throw Error('Unexpected route');
    } };
  const render = () => { h.begin(); const outer = component.default(props); tree = outer.type(outer.props); h.effects(); };
  const settle = async () => { for (let i = 0; i < 50; i++) { await Promise.resolve(); if (h.dirty()) render(); } };
  return { state, storage, calls, render, settle, stop: h.stop, tree: () => tree,
    button(label) { const b = flat(tree).find(n => n?.type === 'button' && renderedText(n) === label); assert.ok(b, 'Missing button ' + label); return b; },
    visibility(hidden) { doc.hidden = hidden; for (const fn of [...listeners.get('visibilitychange') ?? []]) fn(); } };
}
test('editor multi-op commit keeps submission through intermediate revision, refreshes real gate and acknowledges only final revision', async () => {
  const h = editorHarness({ ...draft(), sentences: { 1: { text: 'changed', instruction: 'person' } } });
  try {
    h.render(); await h.settle(); h.button('应用修改').props.onClick(); await h.settle();
    assert.equal(h.state.processing, 1); assert.equal(pend.readPending(TASK, h.storage).submitted, 4);
    h.state.ctx.revision = 3; h.state.ctx.report.revision = 3; h.state.checks.revision = 3;
    h.visibility(true); const reads = h.calls.length; await h.settle(); assert.equal(h.calls.length, reads);
    h.visibility(false); await h.settle();
    assert.equal(pend.readPending(TASK, h.storage).submitted, 4); assert.doesNotMatch(renderedText(h.tree()), /已清除本次提交/);
    h.state.ctx.revision = 4; h.state.ctx.report.revision = 4; h.state.checks = { ...gate(), revision: 4, passed: false, blocking_count: 1,
      checks: [{ key: 'new', code: 'UNSEEN_HARD_BLOCK', level: 0, message: '新发现的问题需要处理', sentence_id: 1, checked: false, confirmable: true }] };
    h.visibility(true); h.visibility(false); await h.settle(); h.visibility(true);
    assert.equal(pend.readPending(TASK, h.storage).submitted, undefined); assert.deepEqual(plain(pend.readPending(TASK, h.storage).sentences), {});
    assert.match(renderedText(h.tree()), /新发现的问题需要处理/); assert.equal(h.button('先勾选确认，再导出').props.disabled, true);
    assert.equal(h.calls.filter(c => c.path.endsWith('/apply')).length, 1);
    assert.equal(h.calls.filter(c => c.init?.method === 'PUT' || c.path.endsWith('/exports')).length, 0);
  } finally { h.stop(); }
});
test('partial commit and fatal rollback preserve pending edits, and reload never reapplies them', async () => {
  for (const partial of [true, false]) {
    const h = editorHarness({ ...draft(), submitted: 4, operationId: JOB, submittedPlan: steps() });
    try {
      h.state.ctx.revision = partial ? 4 : 2; h.state.ctx.report.revision = h.state.ctx.revision;
      h.state.ctx.last_operation_error = partial ? null : 'processing_failed'; h.state.checks.revision = h.state.ctx.revision;
      h.state.operation.state = partial ? 'succeeded' : 'failed'; h.state.operation.steps[1].state = 'failed'; h.state.operation.replace_failures = { 1: 'used' };
      h.render(); await h.settle(); h.visibility(true);
      const saved = pend.readPending(TASK, h.storage); assert.equal(saved.base, 2); assert.equal(saved.sentences[1].text, 'changed'); assert.equal(saved.submitted, undefined);
      assert.match(renderedText(h.tree()), /合适的镜头已经用过/); assert.doesNotMatch(renderedText(h.tree()), /processing_failed|已清除本次提交/);
      if (partial) { assert.match(renderedText(h.tree()), /禁止自动覆盖/); assert.equal(h.button('应用修改').props.disabled, true); }
      assert.equal(h.calls.filter(c => c.init?.method === 'POST').length, 0);
    } finally { h.stop(); }
  }
});
test('context revision 2 is a version, historical wrapper is observed, restore receipt is base+1 and durable', async () => {
  const h = editorHarness();
  try {
    h.render(); await h.settle(); assert.ok(h.button('版本 2')); assert.doesNotMatch(renderedText(h.tree()), /第 2 次修改/);
    h.button('初版').props.onClick(); await h.settle(); h.button('恢复这个版本').props.onClick(); await h.settle();
    const saved = pend.readPending(TASK, h.storage); assert.equal(saved.base, 2); assert.equal(saved.submitted, 3); assert.equal(saved.operationId, undefined);
    assert.deepEqual(JSON.parse(h.calls.find(c => c.path.endsWith('/restore')).init.body), { rev: 0, expected_revision: 2 });
    assert.ok(h.calls.some(c => c.path.endsWith('/versions/0')));
  } finally { h.stop(); }
});
test('context accepts verified recording CPM and real relative word clocks; rejects malformed evidence', () => {
  const ctx = contextFixture(); Object.assign(ctx.timings[0], { audio_source: 'recording', audio_kind: 'sync', transcript_verified: true,
    speaking_rate_cpm: 241.25, words: [{ text: 'sentence', start: .1, end: 2.5 }] });
  const result = api.parseContext(ctx, TASK); assert.equal(result.timings[0].speaking_rate_cpm, 241.25);
  assert.equal(result.timings[0].transcript_verified, true);
  for (const patch of [{ speaking_rate_cpm: NaN }, { end: Infinity }, { start: -.1 }, { duration: 0 },
    { words: [{ text: 'sentence', start: 0, end: 3.1 }] }, { words: [{ text: 'sentence', start: NaN, end: 1 }] }]) {
    const copy = structuredClone(ctx); Object.assign(copy.timings[0], patch); assert.throws(() => api.parseContext(copy, TASK));
  }
});
test('stale pending edit does not hide or confirm an unseen server hard blocker after visibility refresh', async () => {
  const h = editorHarness(draft(1));
  try {
    h.render(); await h.settle();
    h.state.checks = { ...gate(), passed: false, blocking_count: 1, checks: [
      { key: 'new', code: 'NEW_UNKNOWN_HARD_BLOCK', level: 0, checked: false, confirmable: true, message: '新发现的阻止发布问题', sentence_id: 2, action: 'view' }] };
    h.visibility(true); h.visibility(false); await h.settle();
    assert.match(renderedText(h.tree()), /新发现的阻止发布问题/); assert.match(renderedText(h.tree()), /禁止自动覆盖/);
    // Unsent edits still block exporting outright, whatever the open checks say.
    assert.equal(h.button('暂时不能导出').props.disabled, true);
    const summary = flat(h.tree()).find(n => n?.type === 'aside' && n.props['aria-labelledby'] === 'gm-rw-qc-summary-title');
    assert.equal(flat(summary).filter(n => n?.type === 'input' && n.props.type === 'checkbox').length, 0);
    assert.equal(h.calls.filter(c => c.init?.method === 'PUT' || c.init?.method === 'POST').length, 0);
  } finally { h.stop(); }
});
const openGate = (extra = []) => ({ ...gate(), passed: false, blocking_count: 1, pending_count: 1, checks: [
  { key: 'err', code: 'MATCH_FALLBACK', level: 0, message: '该播音单元使用了无语义保证的兜底镜头。', sentence_id: 2, checked: false, confirmable: false, action: 'view' },
  { key: 'warn', code: 'LOW_MATCH_CONFIDENCE', level: 1, message: '匹配置信度偏低。', sentence_id: 0, checked: false, confirmable: true, action: 'view' }, ...extra] });
const exportPosts = h => h.calls.filter(c => c.path.endsWith('/exports') && c.init?.method === 'POST');
const primary = (h, label) => flat(h.tree()).find(n => n?.type === 'button' && n.props.className === 'gm-rw-primary' && renderedText(n) === label);
const acceptBox = h => flat(h.tree()).find(n => n?.type === 'input' && n.props['data-control'] === 'export-accept-unresolved');
test('export with open checks: they are listed first, the button stays off until accepted, then exactly one acknowledged POST', async () => {
  const h = editorHarness(); h.state.checks = openGate();
  try {
    h.render(); await h.settle();
    const text = renderedText(h.tree());
    assert.match(text, /有 2 项检查没有通过/); assert.match(text, /无语义保证的兜底镜头/); assert.match(text, /匹配置信度偏低/);
    assert.match(text, /第 3 句：/); assert.match(text, /错误/); assert.match(text, /待确认/);
    assert.equal(h.button('先勾选确认，再导出').props.disabled, true);
    const before = h.calls.length; h.button('先勾选确认，再导出').props.onClick(); await h.settle();
    assert.equal(h.calls.length, before, 'a click before accepting sends nothing, not even a read');
    assert.equal(acceptBox(h).props.checked, false);
    acceptBox(h).props.onChange({ target: { checked: true } }); await h.settle();
    assert.equal(h.button('仍然导出（带未通过的检查）').props.disabled, false);
    h.button('仍然导出（带未通过的检查）').props.onClick(); await h.settle();
    const posts = exportPosts(h); assert.equal(posts.length, 1);
    assert.deepEqual(plain(JSON.parse(posts[0].init.body)), { fmt: 'mp4', aspect: '16:9', res: '1080p', sub: 'std', acknowledge_unresolved: true, expected_revision: h.state.ctx.revision });
    assert.ok(h.calls.findIndex(c => c.path.endsWith('/checks')) < h.calls.findIndex(c => c.path.endsWith('/exports')), 'the gate is re-read right before the POST');
  } finally { h.stop(); }
});
test('the acknowledgement does not survive a change in the open checks', async () => {
  const h = editorHarness(); h.state.checks = openGate();
  try {
    h.render(); await h.settle(); acceptBox(h).props.onChange({ target: { checked: true } }); await h.settle();
    // A new problem appears on the server between the user's tick and the click.
    h.state.checks = openGate([{ key: 'late', code: 'NEW_ERROR', level: 0, message: '新冒出来的问题。', sentence_id: 1, checked: false, confirmable: false, action: 'view' }]);
    h.button('仍然导出（带未通过的检查）').props.onClick(); await h.settle();
    assert.equal(exportPosts(h).length, 0, 'nothing is exported for a list the user never saw');
    assert.match(renderedText(h.tree()), /发生了变化|重新查看/);
  } finally { h.stop(); }
});
test('a fully passing gate exports exactly as before, with no acknowledgement flag; an inconsistent not-passed gate with no listed check never exports', async () => {
  const pass = editorHarness();
  try {
    pass.render(); await pass.settle();
    assert.equal(primary(pass, '开始导出').props.disabled, false); assert.doesNotMatch(renderedText(pass.tree()), /项检查没有通过/);
    primary(pass, '开始导出').props.onClick(); await pass.settle();
    assert.equal(exportPosts(pass).length, 1); assert.equal('acknowledge_unresolved' in JSON.parse(exportPosts(pass)[0].init.body), false);
  } finally { pass.stop(); }
  const odd = editorHarness();
  try {
    odd.render(); await odd.settle();
    odd.state.checks = { ...gate(), passed: false, blocking_count: 4, pending_count: 0, checks: [] };
    primary(odd, '开始导出').props.onClick(); await odd.settle();
    assert.equal(exportPosts(odd).length, 0);
  } finally { odd.stop(); }
});
test('a job exported with open checks says so and stays downloadable while the work is otherwise ready', async () => {
  const h = editorHarness(); h.state.checks = openGate();
  h.state.exportReply = { export_id: JOB, revision: h.state.ctx.revision, state: 'succeeded', output_id: JOB, error: null,
    options: { fmt: 'mp4', aspect: '16:9', res: '1080p', sub: 'std', expected_revision: h.state.ctx.revision, acknowledge_unresolved: true },
    result: { bytes: 1234, duration: 3.5 }, unresolved: { blocking: 1, pending: 1, qc_blockers: true } };
  try {
    h.render(); await h.settle(); acceptBox(h).props.onChange({ target: { checked: true } }); await h.settle();
    h.button('仍然导出（带未通过的检查）').props.onClick(); await h.settle();
    const text = renderedText(h.tree());
    assert.match(text, /这份文件是在检查未通过时导出的：错误 1 项、待确认 1 项，另有制作质检的阻断项/);
    const link = flat(h.tree()).find(n => n?.type === 'a' && String(n.props.href).includes(`/exports/${JOB}/file`));
    assert.ok(link, 'download link is offered for an acknowledged export');
  } finally { h.stop(); }
});
test('apply boundary enforces server row text, instruction, speaker role and identity limits before POST', async () => {
  let posts = 0; const client = api.createWorkbenchApi(TASK, async () => { posts++; return applyReceipt(); });
  for (const edits of [[{ sentence_id: 1, text: 'a'.repeat(2001) }], [{ sentence_id: 1, instruction: 'a'.repeat(501) }], [{ sentence_id: 1, recording_id: 'bad' }]]) {
    await assert.rejects(client.apply({ ...batch(), edits }, [1, 2], 'voiceover'));
  }
  await assert.rejects(client.apply({ ...batch(), edits: [], speakers: [{ id: 's1', name: 'N', title: 'a'.repeat(121), auto_label: '', appearances: 1, seconds: 3 }] }, [1, 2], 'mixed'));
  assert.equal(posts, 0);
});

test('explicit VM module map fails closed and keeps actual typed gone classification', () => {
  const rulesPath = '../../../backend/mode_rules.json';
  for (const modules of [{}, Object.create({ [rulesPath]: { default: JSON.parse(read('backend/mode_rules.json')) } })]) {
    assert.throws(() => load('frontend/src/lib/productionModes.ts', modules),
      e => e.message === 'Unexpected dependency: ' + rulesPath);
  }
  const missingCss = { ...resultModules }; delete missingCss['./ResultWorkbench.css'];
  assert.throws(() => load('frontend/src/components/ResultWorkbench.tsx', missingCss), /Unexpected dependency: \.\/ResultWorkbench\.css/);
  assert.throws(() => load('frontend/src/lib/qualitySummary.ts', { './workbenchApi': api }), /Unexpected dependency: \.\/productionModes/);
  for (const status of [401, 403, 404, 410]) assert.equal(appApi.goneReason(new appApi.AppApiError('synthetic', status)), 'inaccessible');
  assert.equal(appApi.goneReason(new appApi.AppApiError('synthetic', 410, undefined, 'task_gone')), 'expired');
  assert.equal(appApi.goneReason(new appApi.AppApiError('synthetic', 409)), 'network');
  assert.equal(appApi.goneReason({ status: 410, code: 'task_gone' }), 'network');
  assert.equal(networkAttempts, 0);
});

test('real speaker module preserves legacy reads but validates 8/12 code points before apply transport', async () => {
  const person = patch => ({ id: 's1', name: '', title: '', auto_label: '', appearances: 1, seconds: 3, ...patch });
  const legacy = person({ name: 'x'.repeat(80), title: 'x'.repeat(160) });
  const ctx = { ...contextFixture(), mode: 'mixed', speakers: [legacy] };
  assert.deepEqual(plain(api.parseContext(ctx, TASK).speakers), [legacy]);
  assert.deepEqual(plain(api.parseReport({ ...ctx.report, speakers: [legacy] }, TASK).speakers), [legacy]);
  for (const patch of [{ name: 'x'.repeat(81) }, { title: 'x'.repeat(161) }]) {
    assert.throws(() => api.parseContext({ ...ctx, speakers: [person(patch)] }, TASK));
  }
  const calls = [], client = api.createWorkbenchApi(TASK, async (path, init) => {
    calls.push({ path, init }); return { ...applyReceipt(), revision: 3,
      steps: [{ kind: 'remix', stages: [8, 9, 10], state: 'pending' }] };
  });
  const make = speaker => ({ ...batch(), edits: [], speakers: [speaker] });
  for (const [field, limit] of [['name', 8], ['title', 12]]) {
    for (const char of ['x', '\u{20000}']) {
      const bad = make(person({ [field]: char.repeat(limit + 1) })), before = JSON.stringify(bad), posts = calls.length;
      await assert.rejects(client.apply(bad, [1, 2], 'mixed'));
      assert.equal(calls.length, posts); assert.equal(JSON.stringify(bad), before);
      await client.apply(make(person({ [field]: char.repeat(limit) })), [1, 2], 'mixed');
      assert.equal(calls.length, posts + 1);
      assert.equal(JSON.parse(calls.at(-1).init.body).speakers.s1[field === 'title' ? 'role' : 'name'], char.repeat(limit));
    }
    for (let code = 0; code < 32; code++) {
      const posts = calls.length;
      await assert.rejects(client.apply(make(person({ [field]: `a${String.fromCharCode(code)}` })), [1, 2], 'mixed'));
      assert.equal(calls.length, posts);
    }
  }
  const posts = calls.length; await assert.rejects(client.apply(make(legacy), [1, 2], 'mixed')); assert.equal(calls.length, posts);
  assert.equal(networkAttempts, 0);
});

test('actual quality summary keeps saved rate evidence and precise metric labels in the rendered editor', async () => {
  const h = editorHarness();
  try {
    h.state.ctx.preferences.pacing = 'slow';
    h.state.ctx.report.quality = { blocking_issue_count: 0, warning_count: 0, issues: [], metrics: {
      narration_unit_count: 2, visual_beat_count: 4, distinct_visual_shot_count: 3, low_confidence_count: 1, fallback_count: 0,
      narration_rate_target: { target_cpm: 290, min_cpm: 253.75, max_cpm: 326.25 },
    } };
    h.render(); await h.settle();
    const details = flat(h.tree()).find(n => n?.type === 'details' && renderedText(n).includes('质量详情'));
    assert.ok(details); assert.ok(!details.props.open);
    const pairs = flat(details).filter(n => n?.type === 'dt' || n?.type === 'dd').map(renderedText);
    assert.deepEqual(pairs, ['当前所看报告 · 全部句子', '2', '当前所看报告 · 不同镜头标识（含生成，非素材文件数）', '2',
      '质量报告 · 旁白单元', '2', '质量报告 · 视觉节拍', '4', '质量报告 · 不同视觉镜头标识（含生成）', '3',
      '质量报告 · 低置信度单元（非事实核实）', '1', '质量报告 · 兜底单元', '0']);
    assert.match(renderedText(details), /目标：290 字\/分钟；配置窗口：253.75–326.25 字\/分钟/);
    assert.doesNotMatch(renderedText(details), /当前已保存偏好/);
    assert.equal(h.calls.filter(c => c.init?.method && c.init.method !== 'GET').length, 0);
    const parsed = api.parseContext(h.state.ctx, TASK);
    delete parsed.report.quality.metrics.narration_rate_target;
    assert.ok(quality.qualitySummary(parsed.report, parsed, false).rate.includes(`${modes.PACING_CPM.slow} 字/分钟（当前已保存偏好）`));
    assert.match(quality.qualitySummary(parsed.report, parsed, true).rate, /目标：未提供.*配置窗口：未提供/);
    assert.equal(networkAttempts, 0);
  } finally { h.stop(); }
});