import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Actual complete TS/TSX, deterministic hooks and in-memory transport/storage.
// No Python imports, network, hosts, builds, user data or native-browser claim.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = p => readFileSync(resolve(root, p), 'utf8');
const plain = v => JSON.parse(JSON.stringify(v));
const forbidden = () => { throw Error('Unexpected side effect'); };
const jsx = (type, props) => ({ type, props: props ?? {} });
const flat = t => t == null || typeof t === 'boolean' ? [] : Array.isArray(t) ? t.flatMap(flat)
  : typeof t !== 'object' ? [t] : [t, ...flat(t.props?.children)];
const text = t => flat(t).filter(v => typeof v === 'string' || typeof v === 'number').join('');
function hooks() {
  let cursor = 0, dirty = true; const slots = [], effects = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
  const react = {
    Fragment: 'fragment',
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, n => { const value = typeof n === 'function' ? n(slots[i].value) : n; if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; } }]; },
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) slots[i] = { value: fn(), deps }; return slots[i].value; },
    useCallback(fn, deps) { return react.useMemo(() => fn, deps); }, useId() { return react.useMemo(() => 'speaker-fixture', []); },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || !same(slots[i].deps, deps)) { const old = slots[i]; slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); }); } },
  };
  return { react, begin() { cursor = 0; dirty = false; }, dirty: () => dirty,
    effects() { for (const fn of effects.splice(0)) fn(); }, stop() { for (const s of slots) s?.cleanup?.(); } };
}
function loader(globals = {}, react = {}, probe = false) {
  const cache = new Map();
  function load(p) {
    p = resolve(root, p); if (cache.has(p)) return cache.get(p);
    if (p.endsWith('.css')) return {};
    if (p.endsWith('.json')) return JSON.parse(readFileSync(p, 'utf8'));
    let source = readFileSync(p, 'utf8').replaceAll('import.meta.env', '({})');
    if (probe && p.endsWith('ResultWorkbench.tsx')) {
      const ast = ts.createSourceFile(p, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const fn = ast.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'ResultWorkbenchEditor');
      const ret = fn.body.statements.find(ts.isReturnStatement);
      source = source.slice(0, ret.getStart(ast)) + 'globalThis.__probe = { draft, writer, patchSpeaker, patchSentence, submitBatch, error, editable };\n' + source.slice(ret.getStart(ast));
      source += '\nexport { ResultWorkbenchEditor };';
    }
    const compiled = ts.transpileModule(source, { fileName: p, reportDiagnostics: true,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
    assert.equal(compiled.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
    const module = { exports: {} }; cache.set(p, module.exports);
    vm.runInNewContext(compiled.outputText, { module, exports: module.exports, URL, URLSearchParams, Headers, FormData, Blob, File,
      AbortController, DOMException, fetch: forbidden, setTimeout: forbidden, setInterval: forbidden, ...globals,
      set __probe(value) { globals.probe = value; },
      require(name) {
        if (name === 'react') return react;
        if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
        assert.ok(name.startsWith('.'), name);
        const base = resolve(dirname(p), name), target = [base, base + '.ts', base + '.tsx', base + '.json'].find(existsSync);
        assert.ok(target, name); return load(target);
      },
    }, { filename: p });
    return module.exports;
  }
  return load;
}
const api = loader()('src/lib/workbenchApi.ts');
const task = 'a'.repeat(32), opaque = 's'.repeat(128);
const person = (patch = {}) => ({ id: opaque, name: '', title: '', auto_label: '说话人一', appearances: 1, seconds: 4, ...patch });
const batch = people => ({ expected_revision: 2, keep_sentence_ids: [0, 1], edits: [{ sentence_id: 1, text: 'New narration' }], speakers: people });
const source = { take_id: 'take-one', upload_id: 'upload-one', speaker_id: opaque, start: 0, end: 4,
  asr_text: '原话', score: .9, precision: 'segment', words: [], snr_db: null };
const rows = [0, 1].map(id => ({ sentence_id: id, sentence: id ? 'Narration' : '原话', shot_id: id, thumb_url: null,
  description: 'Synthetic', duration: 4, confidence: .9, is_fallback: false, audio_kind: id ? 'tts' : 'sync',
  visual_beats: [], replacement_instruction: null, kind: id ? 'narration' : 'quote', ...(id ? {} : { source }) }));
const context = () => ({ task_id: task, revision: 2, status: 'done', script: '', preferences: { pacing: 'normal' }, mode: 'mixed',
  speakers: [person()], report: { task_id: task, rows, quality: null }, timings: [], shots: [], current_shots: [], versions: [], last_operation_error: null });
const receipt = () => ({ task_id: task, revision: 3, current_revision: 2, status: 'queued', operation_id: 'b'.repeat(32),
  plan: { steps: [{ kind: 'remix', stages: [7, 8, 9, 10], state: 'pending' }] } });
function runtime() {
  const h = hooks(), storage = new Map(), timers = new Map(); let serial = 0;
  const events = { addEventListener() {}, removeEventListener() {} };
  const globals = { localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v) },
    document: { ...events, hidden: false }, window: { ...events, confirm: () => true },
    setTimeout(fn) { const id = ++serial; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
    setInterval: forbidden, clearInterval: forbidden };
  return { ...h, globals, storage, load: loader(globals, h.react, true),
    mount(component, props) { let tree;
      const render = () => { h.begin(); tree = component(props); h.effects(); return tree; };
      return { render, tree: () => tree, async settle() { for (let i = 0; i < 20; i++) { await Promise.resolve(); if (h.dirty()) render(); } } };
    } };
}
function quoteHarness(patch = {}) {
  const r = runtime(), props = { row: rows[0], revision: 2, mode: 'mixed', disabled: false, toNarration: false,
    speakers: [person()], pendingSpeakers: { [opaque]: { name: '', title: '', ...patch } },
    api: { takes: async () => [], wave: async () => ({ start: 0, end: 4, interval_ms: 20, samples: [] }) },
    onSpeaker(id, change) { props.pendingSpeakers[id] = { ...props.pendingSpeakers[id], ...change }; },
    onTrim: forbidden, onTake: forbidden, onToNarration: forbidden, onListen: forbidden };
  const m = r.mount(r.load('src/components/QuoteEditor.tsx').default, props);
  return { ...r, ...m, props, input(field) { return flat(m.tree()).filter(n => n?.type === 'input')[field === 'name' ? 0 : 1]; } };
}
test('source contract: v2 Person 8/12, blank allowed, ApplyRequest rejects ord < 32; wizard agrees on nominal limits', () => {
  const backend = read('../backend/v2_editing.py');
  assert.match(backend, /class Person\(StrictRequest\):\s+name: str = Field\(max_length=8\)\s+role: str = Field\(max_length=12\)/);
  assert.match(backend, /any\(ord\(c\) < 32 for c in value\)/);
  assert.match(read('src/components/CreateWizard.tsx'), /value=\{person.name\} maxLength=\{8\}/);
  assert.match(read('src/components/CreateWizard.tsx'), /value=\{person.title\} maxLength=\{12\}/);
});
for (const [field, limit, label] of [['name', 8, '姓名'], ['title', 12, '身份']]) {
  for (const [kind, char] of [['BMP', '字'], ['supplementary', '\u{20000}'], ['emoji', '\u{1f600}']]) {
    test(`${field} ${kind}: ${limit} accepted, ${limit + 1} rejected before any POST`, async () => {
      const good = batch([person({ [field]: char.repeat(limit) })]), bad = batch([person(), person({ id: 'second', [field]: char.repeat(limit + 1) })]);
      api.validateEditBatch(good, 'mixed');
      const calls = [], client = api.createWorkbenchApi(task, async (path, init) => { calls.push({ path, init }); return receipt(); });
      const before = JSON.stringify(bad);
      await assert.rejects(client.apply(bad, [0, 1], 'mixed'), e => {
        assert.match(e.message, /说话人\s*2/); assert.ok(e.message.includes(label)); assert.ok(e.message.includes(String(limit)));
        assert.match(e.message, /缩短|删除/); assert.equal(api.workbenchErrorMessage(e), e.message); return true;
      });
      assert.equal(calls.length, 0); assert.equal(JSON.stringify(bad), before);
      await client.apply(good, [0, 1], 'mixed'); assert.equal(calls.length, 1);
      assert.equal(JSON.parse(calls[0].init.body).speakers[opaque][field === 'title' ? 'role' : 'name'], char.repeat(limit));
    });
  }
  test(`${field}: actual input marks invalid, preserves paste and incremental deletion, supplementary boundary valid`, () => {
    const h = quoteHarness({ [field]: '字'.repeat(limit + 2) }); h.render();
    assert.equal(h.input(field).props['aria-invalid'], true);
    const described = h.input(field).props['aria-describedby']; assert.ok(described);
    const message = flat(h.tree()).find(n => n?.props?.id === described); assert.ok(text(message).includes(label)); assert.match(text(message), /缩短|删除/);
    assert.equal(h.input(field).props.maxLength, undefined, 'native UTF-16 maxlength must not truncate or block code points');
    for (const value of ['字'.repeat(limit + 1), '\u{20000}'.repeat(limit), '\u{20000}'.repeat(limit + 1), '']) {
      h.input(field).props.onChange({ target: { value } }); h.render();
      assert.equal(h.input(field).props.value, value); assert.equal(h.props.pendingSpeakers[opaque][field], value);
      assert.equal(h.input(field).props['aria-invalid'], Array.from(value).length > limit);
    }
    h.stop();
  });
  test(`${field}: blank and ordinary whitespace allowed; combining sequences count code points, not graphemes`, () => {
    for (const value of ['', ' '.repeat(limit), '\u00a0', '\u2003', '\u007f', '\u0085', 'e\u0301'.repeat(limit / 2)]) {
      api.validateEditBatch(batch([person({ [field]: value })]), 'mixed');
    }
    assert.throws(() => api.validateEditBatch(batch([person({ [field]: 'e\u0301'.repeat(limit / 2) + 'x' })]), 'mixed'));
  });
  test(`${field}: every C0 control is rejected with specific actionable input and API messages`, () => {
    for (let i = 0; i < 32; i++) {
      const value = `A${String.fromCharCode(i)}`;
      assert.throws(() => api.validateEditBatch(batch([person({ [field]: value })]), 'mixed'), e => e.message.includes(label) && /控制字符/.test(e.message) && /删除|移除/.test(e.message));
      const h = quoteHarness({ [field]: value }); h.render(); assert.equal(h.input(field).props['aria-invalid'], true); h.stop();
    }
  });
}
test('historical reads keep 80/160 limits and opaque 128 ID; do not tighten read parser', () => {
  const legacy = person({ name: '字'.repeat(80), title: '字'.repeat(160) });
  const report = api.parseReport({ task_id: task, rows, quality: null, speakers: [legacy] }, task);
  assert.deepEqual(plain(report.speakers), [legacy]);
  assert.equal(api.parseContext({ ...context(), speakers: [legacy] }, task).speakers[0].id, opaque);
  for (const patch of [{ name: 'x'.repeat(81) }, { title: 'x'.repeat(161) }, { id: 'x'.repeat(129) }]) {
    assert.throws(() => api.parseReport({ task_id: task, rows, quality: null, speakers: [person(patch)] }, task));
  }
  assert.equal(api.applyBody(batch([person()]), [0, 1], 'mixed').speakers[opaque].name, '');
});
test('speaker shape/count/identity checks retained separately from display validation', () => {
  for (const people of [null, {}, [person(), person()], [person({ id: '' })], [person({ id: 'x'.repeat(129) })],
    [person({ name: null })], [person({ title: 12 })], [person({ seconds: -1 })], [person({ appearances: .5 })],
    Array.from({ length: 101 }, (_, i) => person({ id: `s${i}` }))]) assert.throws(() => api.validateEditBatch(batch(people), 'mixed'));
  api.validateEditBatch(batch(Array.from({ length: 100 }, (_, i) => person({ id: `s${i}` }))), 'mixed');
});
for (const entry of ['edit', 'remix', 'apply']) test(`${entry}: invalid speaker rejects mixed batch before transport`, async () => {
  let posts = 0; const client = api.createWorkbenchApi(task, async () => { posts++; return receipt(); });
  const value = batch([person({ title: 'x'.repeat(13) })]), before = JSON.stringify(value);
  await assert.rejects(entry === 'edit' ? client.edit(value) : entry === 'remix' ? client.remix(value, 'mixed') : client.apply(value, [0, 1], 'mixed'));
  assert.equal(posts, 0); assert.equal(JSON.stringify(value), before);
});
for (const cached of [false, true]) test(`actual Workbench ${cached ? 'cached' : 'new'} invalid speakers retain all edits, zero POST; explicit correction succeeds`, async () => {
  const r = runtime(), calls = [], errors = [], pending = r.load('src/lib/pendingEdits.ts');
  if (cached) pending.writePending(task, { ...pending.emptyPending(), base: 2, sentences: { 1: { text: 'New narration' } },
    pendSpeakers: { [opaque]: { name: '字'.repeat(9), title: '字'.repeat(13) } } }, r.globals.localStorage);
  const props = { taskId: task, request: async (path, init) => { calls.push({ path, init });
    if (init?.method === 'POST') return receipt();
    if (path.endsWith('/context')) return context();
    if (path.endsWith('/checks')) return { revision: 2, passed: true, blocking_count: 0, pending_count: 0, checks: [] };
    throw Error('Unexpected request'); }, onProcessing() {}, onChanged() {}, onDelete() {}, onNew() {}, onError: e => errors.push(e) };
  const m = r.mount(r.load('src/components/ResultWorkbench.tsx').ResultWorkbenchEditor, props); m.render(); await m.settle();
  assert.equal(r.globals.probe.editable, true);
  if (!cached) { r.globals.probe.patchSentence(1, { text: 'New narration' }); r.globals.probe.patchSpeaker(opaque, { name: '字'.repeat(9), title: '字'.repeat(13) }); await m.settle(); }
  const before = JSON.stringify(r.globals.probe.draft);
  r.globals.probe.submitBatch(); await m.settle();
  assert.equal(calls.filter(c => c.init?.method === 'POST').length, 0);
  assert.equal(JSON.stringify(r.globals.probe.draft), before); assert.match(errors.at(-1), /姓名.*8/);
  r.globals.probe.writer.flush(); assert.equal(JSON.stringify(pending.readPending(task, r.globals.localStorage)), before);
  r.globals.probe.patchSpeaker(opaque, { name: '\u{20000}'.repeat(8) }); await m.settle(); r.globals.probe.submitBatch(); await m.settle();
  assert.equal(calls.filter(c => c.init?.method === 'POST').length, 0); assert.match(errors.at(-1), /身份.*12/);
  r.globals.probe.patchSpeaker(opaque, { title: '\u{20000}'.repeat(12) }); await m.settle(); r.globals.probe.submitBatch(); await m.settle();
  const posts = calls.filter(c => c.init?.method === 'POST'); assert.equal(posts.length, 1);
  const body = JSON.parse(posts[0].init.body); assert.deepEqual(body.edits, { 1: 'New narration' });
  assert.deepEqual(body.speakers[opaque], { name: '\u{20000}'.repeat(8), role: '\u{20000}'.repeat(12) });
  assert.equal(r.globals.probe.draft.submitted, 3); r.stop();
});