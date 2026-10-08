import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import React from 'react';
import * as runtime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import postcss from 'postcss';

// Actual TSX + dependencies, compiled only in memory. React SSR proves markup;
// the deterministic hook/event adapter proves state/request behavior. It does
// NOT emulate native keyboard dispatch, DOM mounting, layout or browser focus.
// No server, build, storage, microphone, media decoding or provider work.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = path => readFileSync(resolve(root, path), 'utf8');
const deny = () => assert.fail('Unexpected external effect');
const plain = value => JSON.parse(JSON.stringify(value));
const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
const WIZARD = 'src/components/CreateWizard.tsx';
function loader({ react = React, jsx = runtime, seeds = {}, globals = {}, request = deny } = {}) {
  const cache = new Map();
  function load(input) {
    const path = resolve(root, input);
    if (cache.has(path)) return cache.get(path).exports;
    if (path.endsWith('.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (path.endsWith('.json')) return JSON.parse(readFileSync(path, 'utf8'));
    // Only the HTTP boundary is synthetic; draft/upload parsing and gating stay real.
    if (path === resolve(root, 'src/lib/appApi.ts')) return { createAppRequest: scope => (url, init = {}) => request(url, init, scope) };
    const output = ts.transpileModule(readFileSync(path, 'utf8'), {
      fileName: path, reportDiagnostics: true,
      compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      transformers: { before: [context => source => {
        const visit = node => {
          if (ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression) && node.name.text === 'env') return ts.factory.createIdentifier('__viteEnv');
          if (path === resolve(root, WIZARD) && ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name)
            && node.initializer && ts.isCallExpression(node.initializer) && node.initializer.expression.getText(source) === 'useState') {
            const name = node.name.elements[0]?.name?.getText(source);
            if (Object.hasOwn(seeds, name)) return ts.factory.updateVariableDeclaration(node, node.name, node.exclamationToken, node.type,
              ts.factory.updateCallExpression(node.initializer, node.initializer.expression, node.initializer.typeArguments,
                [ts.factory.createElementAccessExpression(ts.factory.createIdentifier('__seeds'), ts.factory.createStringLiteral(name))]));
          }
          return ts.visitEachChild(node, visit, context);
        };
        return ts.visitNode(source, visit);
      }] },
    });
    assert.equal(output.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
    const module = { exports: {} }; cache.set(path, module);
    new vm.Script(output.outputText, { filename: path }).runInNewContext({
      module, exports: module.exports, __seeds: seeds, __viteEnv: {}, URL, URLSearchParams, Headers, AbortController, DOMException,
      fetch: deny, XMLHttpRequest: deny, setTimeout: deny, clearTimeout() {}, setInterval: deny,
      localStorage: { getItem: deny, setItem: deny }, navigator: {}, window: { confirm: () => true },
      location: { origin: 'https://synthetic.invalid' }, performance: { now: () => 0 }, ...globals,
      require(name) {
        if (name === 'react') return react;
        if (name === 'react/jsx-runtime') return jsx;
        assert.ok(name.startsWith('.'), `Unbound import ${name}`);
        const full = resolve(dirname(path), name), target = [full, `${full}.ts`, `${full}.tsx`].find(existsSync);
        assert.ok(target, name); return load(target);
      },
    }, { timeout: 5000 });
    return module.exports;
  }
  return load;
}
const modes = loader()('src/lib/productionModes.ts');
const limits = { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 };
const TASK = 'progressive_synthetic', TOKEN = 'synthetic_only_'.padEnd(48, 'x');
const UP = 'up_00000000000000000000000000000001', UP2 = 'up_00000000000000000000000000000002';
const access = { draftTaskId: TASK, draftTaskToken: TOKEN }, taskRoot = `/api/tasks/${TASK}`;
const metadata = (n = 1) => ({ id: JSON.stringify([`synthetic_${n}.mp4`, 3, 123]), name: `synthetic_${n}.mp4`, size: 3, lastModified: 123,
  type: 'video/mp4', kind: 'video', duration: 60, note: '保留备注', trim_start: 0, trim_end: 60 });
const binding = (n = 1) => ({ file_id: metadata(n).id, upload_id: n === 1 ? UP : UP2, sha256: 'a'.repeat(64),
  chunk_size: 8388608, put_url: `${taskRoot}/files/server_${n}/chunks`, draftTaskId: TASK, server_file_id: `server_${n}` });
const segment = (id, text = '今天社区活动正式开始', start = 1) => ({ id, text, start, end: start + 2, speaker_id: '', words: [] });
const transcript = [segment('same'), segment('second', '居民在广场参加活动', 4)];
const snapshot = (n = 1, patch = {}) => ({ id: n === 1 ? UP : UP2, name: metadata(n).name, bytes: 3, sec: 60,
  // UploadStore._public / DraftStore.file_view: scoped ready requires measured
  // server metadata, not the browser draft's cached duration or dimensions.
  probe_ok: true, width: 1920, height: 1080, fps: 30, metadata_only: false, can_materialize: true, phase: 'done',
  status: 'ready', has_speech: true, thumb_url: null, wave_url: null, asr_confidence: null,
  transcript, progress: 100, chunks: [0], sha256: 'a'.repeat(64), ...patch });
const draft = (patch = {}) => ({ ...access, mode: 'original', script: '社区新闻', step: 2,
  preferences: modes.defaultModePreferences('original'), files: [metadata()], uploadBindings: [binding()],
  uploadIds: [UP], uploadTokens: { [UP]: TOKEN }, elements: {}, sentenceChecks: {}, ...patch });
const props = initialDraft => ({ limits, initialDraft, generativeAllowed: false, busy: false,
  serviceReady: true, onDraft: deny, onSubmit: deny, onError: deny });
function markup(initialDraft, seeds = {}) {
  return renderToStaticMarkup(React.createElement(loader({ seeds })(WIZARD).CreateWizard, props(initialDraft)));
}
const text = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(text).join('')
  : typeof node === 'object' ? text(node.props?.children) : String(node);
function nodes(node, ancestors = []) {
  if (Array.isArray(node)) return node.flatMap(n => nodes(n, ancestors));
  if (!node || typeof node !== 'object') return [];
  return [{ node, ancestors }, ...nodes(node.props?.children, [...ancestors, node])];
}
function harness(initialDraft = draft(), options = {}) {
  const hooks = [], effects = [], drafts = [], requests = [], submissions = [], timers = new Map();
  let cursor = 0, dirty = true, tree, serial = 0, time = 0, focuses = 0;
  const clock = {
    setTimeout(fn, delay) { const id = ++serial; timers.set(id, { fn, at: time + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    advance(ms) { time += ms; for (const [id, item] of [...timers]) if (item.at <= time) { timers.delete(id); item.fn(); } },
  };
  const react = {
    useId: () => 'synthetic-progressive',
    useState(seed) { const at = cursor++; hooks[at] ??= { value: typeof seed === 'function' ? seed() : seed };
      return [hooks[at].value, next => { const value = typeof next === 'function' ? next(hooks[at].value) : next;
        if (!Object.is(value, hooks[at].value)) { hooks[at].value = value; dirty = true; } }]; },
    useRef(value) { return hooks[cursor++] ??= { current: value }; },
    useMemo(fn, deps) { const at = cursor++; if (!same(hooks[at]?.deps, deps)) hooks[at] = { value: fn(), deps }; return hooks[at].value; },
    useEffect(fn, deps) { const at = cursor++; if (!same(hooks[at]?.deps, deps)) {
      const old = hooks[at]; hooks[at] = { deps, cleanup: old?.cleanup };
      effects.push(() => { hooks[at].cleanup?.(); hooks[at].cleanup = fn(); });
    } },
  };
  const jsx = (type, properties = {}) => {
    if (typeof type === 'function') return type(properties);
    if (type === 'h1' && properties.ref) properties.ref.current = { focus: () => focuses++ };
    return { type, props: properties };
  };
  const request = async (path, init, scope) => {
    requests.push({ path, init, scope });
    if (options.respond) return options.respond(path, init, scope);
    if (path === taskRoot) return { task_id: TASK, status: 'draft' };
    if (path === `${taskRoot}/files`) return { files: [] };
    if (path === `${taskRoot}/files/server_1`) return snapshot();
    if (path === `${taskRoot}/files/server_2`) return snapshot(2);
    if (path === `${taskRoot}/align`) return { matches: JSON.parse(init.body).sentences.map(s => ({ idx: s.idx, kind: s.kind,
      score: 1, source: { take_id: 'synthetic_take', upload_id: s.source_hint?.upload_id ?? UP, start: 1, end: 3,
        speaker_id: '', asr_text: options.asr ?? s.text, score: 1, snr_db: null, words: [], precision: 'segment' }, alt_takes: [] })) };
    return deny();
  };
  const load = loader({ react, jsx: { jsx, jsxs: jsx, Fragment: 'fragment' }, request, globals: clock, seeds: options.seeds });
  const component = load(WIZARD).CreateWizard;
  const properties = { ...props(initialDraft), serviceReady: options.ready ?? true, onDraft: d => drafts.push(plain(d)),
    onSubmit: async d => submissions.push(plain(d)), onError() {} };
  function flush() {
    let guard = 0;
    do { if (dirty) { cursor = 0; dirty = false; tree = component(properties); }
      while (effects.length) effects.shift()(); assert.ok(++guard < 40, 'render loop');
    } while (dirty);
  }
  function all(node = tree) { return nodes(node).map(item => item.node); }
  function button(label) { const n = all().find(n => n.type === 'button' && (text(n).trim() === label || n.props['aria-label'] === label)); assert.ok(n, label); return n; }
  const picker = () => all().find(n => n.type === 'details' && n.props.className === 'gm-transcript-picker');
  flush();
  return { clock, drafts, requests, submissions, all, button, picker, flush, get tree() { return tree; }, get focuses() { return focuses; },
    click(label) { const n = button(label); assert.equal(!!n.props.disabled, false, label); n.props.onClick(); flush(); },
    fold(open) { assert.ok(picker()); picker().props.onToggle({ currentTarget: { open } }); flush(); },
    pick(index, checked) { const n = all(picker()).filter(n => n.type === 'input' && n.props.type === 'checkbox')[index];
      assert.ok(n); n.props.onChange({ target: { checked } }); flush(); },
    edit(value) { all().find(n => n.type === 'textarea' && n.props.className === 'gm-script').props.onChange({ target: { value } }); flush(); },
    async settle() { for (let round = 0; round < 4; round++) { for (let tick = 0; tick < 40; tick++) await Promise.resolve(); flush(); } },
    unmount() { for (const hook of hooks) hook?.cleanup?.(); },
  };
}

test('SSR native summary is first child, initially open; folded rows stay rendered and no keyboard override', () => {
  const initial = draft({ step: 1 }), state = { uploads: { [UP]: snapshot() } };
  const open = markup(initial, state), closed = markup(initial, { ...state, pickOpen: false });
  assert.match(open, /<details class="gm-transcript-picker" open=""><summary class="gm-pick-summary">/);
  assert.match(closed, /<details class="gm-transcript-picker"><summary class="gm-pick-summary">/);
  assert.match(closed, /已挑 0 句 \/ 可选 2 句/);
  assert.equal((closed.match(/type="checkbox"/g) ?? []).length, 2);
  const summary = closed.match(/<summary class="gm-pick-summary">([\s\S]*?)<\/summary>/)[1];
  assert.doesNotMatch(summary, /<button|<input|tabindex|role=/);
  // Native Enter/Space disclosure is delegated to HTML, not simulated here.
  const w = harness(initial, { seeds: state });
  const node = w.picker(), first = node.props.children[0];
  assert.equal(first.type, 'summary');
  for (const key of ['onKeyDown', 'onKeyUp', 'onClick', 'role', 'tabIndex']) assert.equal(first.props[key], undefined);
  assert.equal(node.props.onKeyDown, undefined); w.unmount();
});
test('SSR summary counts source identities across all rows, not the eight-row preview or repeated text', () => {
  const ten = Array.from({ length: 10 }, (_, i) => segment(`s${i}`, '同一句原话', i * 3));
  const selected = modes.selectTranscriptSentence([], UP, ten[9], true);
  const html = markup(draft({ step: 1, script: modes.scriptFromSentences('标题', selected, 'original'), sentences: selected }),
    { uploads: { [UP]: snapshot(1, { transcript: ten }) } });
  assert.match(html, /已挑 1 句 \/ 可选 10 句/); assert.match(html, /再看 2 句转写/);
  assert.equal((html.match(/type="checkbox"/g) ?? []).length, 8);
});
test('VM empty C and title-only C can discover upload, but not skip to effects', async () => {
  for (const script of ['', '新闻标题']) {
    const w = harness(draft({ script, step: 1, files: [], uploadBindings: [], uploadIds: [], uploadTokens: {} }));
    await w.settle(); assert.equal(w.picker(), undefined);
    w.click('下一步：传素材'); assert.equal(w.button('下一步：选效果').props['aria-disabled'], true);
    assert.equal(w.all().filter(n => n.type === 'input' && n.props.type === 'file').length, 1);
    w.click('从转写挑句子'); assert.equal(w.focuses, 2);
    assert.ok(w.requests.every(c => !c.init.method)); w.unmount();
  }
});
test('VM fold/reopen retains checked source hints, click order, file notes, capability and no extra requests', async () => {
  const w = harness(); await w.settle();
  assert.equal(w.picker().props.open, true); w.pick(1, true); w.pick(0, true);
  const before = plain(w.drafts.at(-1)), requests = w.requests.length;
  w.fold(false); w.fold(true);
  assert.deepEqual(w.drafts.at(-1), before); assert.equal(w.requests.length, requests);
  assert.deepEqual(before.sentences.map(s => s.source_hint), [{ upload_id: UP, seg_id: 'second' }, { upload_id: UP, seg_id: 'same' }]);
  assert.ok(w.all(w.picker()).filter(n => n.type === 'input').every(n => n.props.checked));
  assert.ok(text(w.picker()).includes('已挑 2 句 / 可选 2 句'));
  assert.equal(before.files[0].note, '保留备注'); assert.equal(before.draftTaskToken, TOKEN);
  w.pick(1, false); assert.equal(w.drafts.at(-1).sentences[0].source_hint.seg_id, 'same'); w.unmount();
});
test('VM identical text/segment IDs in two uploads count twice; deselection is source-specific', async () => {
  const w = harness(draft({ files: [metadata(), metadata(2)], uploadBindings: [binding(), binding(2)],
    uploadIds: [UP, UP2], uploadTokens: { [UP]: TOKEN, [UP2]: TOKEN } }));
  await w.settle(); w.pick(0, true); w.pick(2, true); w.pick(0, false);
  assert.ok(text(w.picker()).includes('已挑 1 句 / 可选 4 句'));
  assert.deepEqual(w.drafts.at(-1).sentences.map(s => s.source_hint), [{ upload_id: UP2, seg_id: 'same' }]); w.unmount();
});
test('VM fold state survives step navigation; explicit discovery reopens without refreshing or starting ASR', async () => {
  const w = harness(); await w.settle(); const count = w.requests.length;
  w.fold(false); w.click('← 上一步'); assert.equal(w.picker().props.open, false);
  w.click('下一步：传素材'); assert.equal(w.picker().props.open, false);
  w.click('从转写挑句子'); assert.equal(w.picker().props.open, true);
  assert.equal(w.requests.length, count); assert.ok(w.requests.every(c => !c.init.method)); w.unmount();
});
test('VM restored checked source rows do GET-only verification, no complete/align/start or repeat transcription', async () => {
  const selected = modes.selectTranscriptSentence([], UP, transcript[0], true);
  const w = harness(draft({ sentences: selected, script: modes.scriptFromSentences('标题', selected, 'original') }));
  await w.settle(); w.fold(false); w.clock.advance(10000); await w.settle(); w.fold(true);
  assert.deepEqual(w.requests.map(c => c.path), [taskRoot, `${taskRoot}/files`, `${taskRoot}/files/server_1`]);
  assert.ok(w.requests.every(c => !c.init.method && c.scope.token === TOKEN));
  assert.ok(w.all(w.picker()).find(n => n.type === 'input').props.checked);
  assert.equal(w.button('下一步：选效果').props['aria-disabled'], true); w.unmount();
});
test('VM folding during pending upload retains read signal and mounted upload controls', async () => {
  let finish; const pending = new Promise(resolve => { finish = resolve; });
  const w = harness(draft({ files: [metadata(), metadata(2)], uploadBindings: [binding(), binding(2)],
    uploadIds: [UP, UP2], uploadTokens: { [UP]: TOKEN, [UP2]: TOKEN } }), { respond(path) {
    if (path === taskRoot) return { task_id: TASK, status: 'draft' };
    if (path === `${taskRoot}/files`) return { files: [] };
    if (path.endsWith('server_1')) return snapshot();
    if (path.endsWith('server_2')) return pending;
    return deny();
  } });
  await w.settle(); const request = w.requests.find(c => c.path.endsWith('server_2'));
  w.fold(false); w.fold(true); assert.equal(request.scope.signal.aborted, false);
  assert.equal(w.all().filter(n => n.props?.className === 'gm-file-picker').length, 1);
  finish(snapshot(2)); await w.settle(); assert.ok(text(w.picker()).includes('可选 4 句'));
  assert.equal(w.requests.length, 4);
  // Completed polls leave the ownership map; only pending reads need aborting.
  assert.equal(request.scope.signal.aborted, false); w.unmount();
});
for (const asr of ['今天社区活动正式开始', '今年我们还请了舞狮队']) test(`VM unsafe C remains blocked after folding, score=1: ${asr}`, async () => {
  const unsafe = asr === '今年我们还请了舞狮队' ? '今年我们请舞狮队' : asr + '并且完全免费';
  const w = harness(draft({ script: `标题\n${unsafe}` }), { asr }); await w.settle();
  w.fold(false); w.click('重新核对'); w.clock.advance(600); await w.settle();
  assert.equal(w.button('下一步：选效果').props['aria-disabled'], true);
  w.button('下一步：选效果').props.onClick(); w.flush();
  assert.equal(w.all().some(n => n.props?.className === 'gm-submit'), false);
  w.fold(true); assert.equal(w.submissions.length, 0);
  assert.equal(w.requests.filter(c => c.init.method).length, 1); // alignment only
  assert.equal(w.requests.at(-1).path, `${taskRoot}/align`); w.unmount();
});
test('VM real source selection keeps match debounce unchanged and permits explicitly verified C', async () => {
  const w = harness(); await w.settle(); w.pick(0, true); w.fold(false);
  w.clock.advance(599); await w.settle(); assert.equal(w.requests.filter(c => c.init.method).length, 0);
  w.clock.advance(1); await w.settle(); assert.equal(w.button('下一步：选效果').props['aria-disabled'], false);
  w.click('下一步：选效果'); w.click('开始制作'); await w.settle();
  assert.equal(w.submissions.length, 1); assert.equal(w.submissions[0].mode, 'original');
  assert.deepEqual(w.submissions[0].sentences[0].source_hint, { upload_id: UP, seg_id: 'same' }); w.unmount();
});
test('VM A/B/C effects remain visible, no invented advanced fold; choices survive back/forward', async () => {
  for (const mode of ['voiceover', 'mixed', 'original']) {
    const w = harness(draft({ mode, script: '社区新闻\n今天社区居民在广场共同参加现场活动', preferences: modes.defaultModePreferences(mode) }));
    await w.settle(); if (mode === 'original') { w.click('重新核对'); w.clock.advance(600); await w.settle(); }
    w.click('下一步：选效果');
    const visibleControls = nodes(w.tree).filter(({ node }) => node.props.role === 'switch' || node.props.role === 'group');
    assert.ok(visibleControls.length >= 8);
    assert.ok(visibleControls.every(({ ancestors }) => !ancestors.some(n => n.type === 'details')));
    for (const name of ['背景音乐', '画面慢慢推近', '新闻台标和片尾板', '开头结尾淡入淡出', '颜色统一', '现场原声降噪']) assert.ok(w.button(name));
    w.click('大字清晰'); w.click('背景音乐'); const before = plain(w.drafts.at(-1).preferences);
    w.click('← 上一步'); w.click('下一步：选效果'); assert.deepEqual(w.drafts.at(-1).preferences, before);
    assert.equal(w.button('大字清晰').props['aria-pressed'], true);
    if (mode === 'original') assert.equal(w.all().some(n => n.type === 'button' && text(n) === '我自己读'), false);
    else { assert.ok(w.button('我自己读')); assert.ok(w.button('快一点')); }
    w.unmount();
  }
});
test('VM M0 own voice stays mine while independent source_voice_preferred remains false/true/absent', async () => {
  for (const preferred of [undefined, false, true]) {
    const w = harness(draft({ mode: 'voiceover', script: '社区新闻\n今天社区居民在广场共同参加现场活动',
      preferences: modes.defaultModePreferences(), ...(preferred === undefined ? {} : { source_voice_preferred: preferred }) }));
    await w.settle(); w.click('下一步：选效果'); w.click('我自己读');
    assert.ok(text(w.tree).includes('先用 AI 播音生成，再替换需要自己读的句子'));
    assert.equal(w.all().some(n => n.props.className === 'gm-voice-panel'), false);
    w.click('开始制作'); await w.settle();
    assert.equal(w.submissions[0].preferences.voice, 'mine');
    assert.equal(w.submissions[0].source_voice_preferred, preferred);
    assert.equal(Object.hasOwn(w.submissions[0], 'source_voice_preferred'), preferred !== undefined);
    assert.equal(Object.hasOwn(w.submissions[0], 'ownVoice'), false); w.unmount();
  }
});
test('VM stale ready UI cannot start after explicit GET refresh fails', async () => {
  let fail = false;
  const w = harness(draft({ mode: 'voiceover', script: '社区新闻\n今天社区居民在广场共同参加现场活动', preferences: modes.defaultModePreferences() }), {
    async respond(path) { if (path === taskRoot) return { task_id: TASK, status: 'draft' };
      if (path === `${taskRoot}/files`) return { files: [] };
      if (fail) throw Error('synthetic unavailable'); return snapshot(); },
  });
  await w.settle(); w.click('下一步：选效果'); assert.equal(w.button('开始制作').props.disabled, false);
  w.click('← 上一步'); fail = true; w.click('重新读取状态'); await w.settle();
  assert.equal(w.button('下一步：选效果').props['aria-disabled'], true); assert.equal(w.submissions.length, 0);
  assert.ok(w.requests.every(c => !c.init.method)); w.unmount();
});
test('scoped incomplete server authority never unlocks restored transcription or adds writes', async () => {
  for (const key of ['probe_ok', 'width', 'height', 'fps']) {
    const invalid = snapshot(); delete invalid[key];
    const w = harness(draft({ mode: 'voiceover', preferences: modes.defaultModePreferences(),
      script: '社区新闻\n今天社区居民在广场共同参加现场活动' }), { respond(path) {
      if (path === taskRoot) return { task_id: TASK, status: 'draft' };
      if (path === `${taskRoot}/files`) return { files: [] };
      assert.equal(path, `${taskRoot}/files/server_1`); return invalid;
    } });
    try {
      await w.settle(); w.clock.advance(10000); await w.settle();
      assert.equal(w.picker(), undefined, key);
      assert.equal(w.button('下一步：选效果').props['aria-disabled'], true, key);
      assert.equal(w.submissions.length, 0);
      assert.deepEqual(w.requests.map(c => c.path), [taskRoot, `${taskRoot}/files`, `${taskRoot}/files/server_1`]);
      assert.ok(w.requests.every(c => !c.init.method && c.scope.token === TOKEN));
      assert.equal(w.drafts.at(-1).uploadBindings[0].server_file_id, 'server_1');
    } finally { w.unmount(); }
  }
});
test('SSR processing internal revision is version N, not number of user modifications or revision+1', () => {
  const Processing = loader()('src/components/Processing.tsx').default;
  for (const revision of [0, 1, 2, 17]) {
    const html = renderToStaticMarkup(React.createElement(Processing, { task: { status: 'running', revision, mode: 'voiceover',
      stages: [], progress: 42, current_stage: 9, plan: { idx: 1, steps: [{ op: 'edit' }, { op: 'render' }] } },
      upload: null, uploading: false, now: 0, busy: false, title: '测试', onCancel: deny, onRetry: deny, onRefresh: deny }));
    assert.ok(html.includes(revision === 0 ? '>初版</span>' : `>版本 ${revision}</span>`));
    assert.doesNotMatch(html, /第 \d+ 次修改/); assert.match(html, /第 2\/2 步/); assert.match(html, /aria-valuenow="42"/);
  }
});
test('CSS native disclosure contract preserves marker, gold focus and wrapping; media rules stay exact', () => {
  const css = postcss.parse(read('src/styles/modes.css')); let summaryHeight, focus;
  css.walkRules(rule => {
    if (rule.selector === '.gm-create-wizard .gm-transcript-picker > .gm-pick-summary') {
      rule.walkDecls('min-height', d => { summaryHeight = d.value; });
      rule.walkDecls(d => assert.ok(!['display', 'list-style'].includes(d.prop), 'keep native marker'));
    }
    if (rule.selector.includes('summary):focus-visible')) rule.walkDecls('outline', d => { focus = d.value; });
  });
  assert.equal(summaryHeight, '2.75rem'); assert.equal(focus, '.1875rem solid var(--gm-gold)');
  const rules = JSON.parse(read('../backend/mode_rules.json'));
  assert.equal(rules.limits.cut_snap_seconds, .3); assert.equal(rules.limits.quote_snr_min_db, 18);
  assert.equal(rules.limits.match_ok, .85); assert.equal(rules.limits.match_low, .6);
  assert.deepEqual(rules.pacing_cpm, { slow: 230, normal: 265, fast: 290 });
  assert.equal(loader()('src/lib/uploadSessions.ts').MATCH_DEBOUNCE_MS, 600);
});