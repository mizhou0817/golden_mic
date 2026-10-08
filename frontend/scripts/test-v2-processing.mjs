import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import postcss from 'postcss';

// In-memory compilation/SSR and actual extracted Workspace hooks. No services,
// browser, storage, uploads, provider calls, emitted build, or filesystem writes.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const read = path => readFileSync(resolve(root, path), 'utf8');
const forbidden = () => { throw Error('Unexpected external effect'); };
function loader(dev = false) {
  const cache = new Map();
  function load(path) {
    path = resolve(root, path);
    if (cache.has(path)) return cache.get(path).exports;
    if (path.endsWith('.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (path.endsWith('.json')) return JSON.parse(readFileSync(path, 'utf8'));
    const source = readFileSync(path, 'utf8').replaceAll('import.meta.env.DEV', String(dev)).replaceAll('import.meta.env', '({})');
    const result = ts.transpileModule(source, { fileName: path, reportDiagnostics: true,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } });
    assert.equal(result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
    const module = { exports: {} }; cache.set(path, module);
    vm.runInNewContext(result.outputText, { module, exports: module.exports, console, Error, URL, URLSearchParams, Headers,
      AbortController, DOMException, fetch: forbidden, localStorage: { getItem: forbidden, setItem: forbidden },
      require(name) {
        if (['react', 'react/jsx-runtime'].includes(name)) return require(name);
        assert.ok(name.startsWith('.'), `Unexpected import ${name}`);
        const full = resolve(dirname(path), name), target = [full, `${full}.ts`, `${full}.tsx`].find(existsSync);
        assert.ok(target, `Missing import ${name}`); return load(target);
      },
    }, { filename: path, timeout: 10_000 });
    return module.exports;
  }
  return load;
}
const load = loader();
const { default: Processing, RECOVERY_UNAVAILABLE } = load('src/components/Processing.tsx');
const { parseTask, errorText, AppApiError } = load('src/lib/appApi.ts');
const rules = JSON.parse(read('../backend/mode_rules.json'));
const makeTask = (patch = {}) => ({ task_id: 'owned-task', lifecycle_v2: true, retry_same_supported: true,
  mode: 'voiceover', status: 'running', revision: 0, progress: 37.25, current_stage: 6, stage_name: 'private',
  message: 'TTS ASR /api/private Traceback secret', error_stage: null, error_message: null,
  processing_started_at: null, processing_completed_at: null, total_elapsed_seconds: null,
  stages: rules.modes.voiceover.stages.map(s => ({ number: s.n, name: 'ASR private', message: 'TTS /api/private Traceback secret',
    status: s.n < 6 ? 'done' : s.n === 6 ? 'running' : 'pending', started_at: null, completed_at: null,
    elapsed_seconds: s.n === 1 ? 65.2 : s.n === 6 ? 9999 : null, weight: .01, fraction: 1 })), ...patch });
const noop = () => {};
const props = patch => ({ task: makeTask(), upload: null, uploading: false, now: 5000, title: '现场报道', busy: false,
  onCancel: noop, onRetry: noop, onEdit: noop, onRefresh: noop, ...patch });
const render = patch => renderToStaticMarkup(React.createElement(Processing, props(patch)));
const controls = html => [...html.matchAll(/data-control="([^"]+)"/g)].map(m => m[1]);
function nodes(element) {
  if (!element || typeof element !== 'object') return [];
  const children = React.Children.toArray(element.props?.children);
  return [element, ...children.flatMap(nodes)];
}

test('A-4 title/version, 64px microphone with exactly four wave groups and ten simple rows', () => {
  const html = render(); assert.match(html, /<h1[^>]*>现场报道<\/h1>/); assert.match(html, />初版<\/span>/);
  assert.equal((html.match(/<g /g) ?? []).length, 4); assert.equal((html.match(/data-stage="/g) ?? []).length, 10);
  assert.match(html, /data-screen-label="制作中"/); assert.match(html, /通常需要几分钟。可以先去做别的——做好后会出现在左边「我的作品」里。/);
  assert.deepEqual(controls(html), ['processing-cancel']);
});
for (const mode of ['voiceover', 'mixed', 'original']) test(`${mode}: shared v2 labels and current doing, no raw diagnostics`, () => {
  const html = render({ task: makeTask({ mode, current_stage: 7 }) });
  for (const stage of rules.modes[mode].stages) assert.ok(html.includes(stage.name));
  assert.ok(html.includes(rules.modes[mode].stages[6].description));
  assert.doesNotMatch(html, /TTS|ASR|\/api\/|Traceback|secret|processing-dev|本步进度|占总进度|十步制作进度/);
  assert.match(html, /aria-valuenow="37.25"/); assert.match(html, />37%<\/strong>/);
});
test('progress comes only from server, not weights or wall clock', () => {
  const first = render({ now: 0 }), later = render({ now: 99999999 }); assert.equal(first, later);
  assert.match(first, /width:37.25%/); assert.doesNotMatch(first, /9999|166 分/);
  assert.match(first, /1 分 5 秒/);
});
test('missing stage snapshots are unknown, never fabricated completion', () => {
  const html = render({ task: makeTask({ stages: [], current_stage: null, progress: NaN }) });
  assert.equal((html.match(/data-state="unknown"/g) ?? []).length, 10);
  assert.doesNotMatch(html, /aria-valuenow|>0%|已完成/); assert.match(html, />—<\/strong>/);
});
for (const [queue, expected] of [[1, '队列前面还有 0 个作品'], [4, '队列前面还有 3 个作品'], [0, '已提交，等待服务器处理'], [undefined, '已提交，等待服务器处理']]) test(`queue ${queue}: truthful one-based server queue, no ETA`, () => {
  const html = render({ task: makeTask({ status: 'queued', queue }) });
  assert.match(html, />排队<\/strong>/); assert.ok(html.includes(expected));
  assert.doesNotMatch(html, /预计|马上开始|每个约|取消不占|退回/);
});
test('network uncertainty retains stages, removes live animation and raw error', () => {
  const html = render({ error: 'HTTP 500 /api/tasks/secret TTS Traceback' });
  assert.match(html, /连接失败不代表服务器制作已停止/); assert.match(html, /上次状态：进行中/);
  assert.match(html, /data-stage="1" data-state="done"/); assert.match(html, /data-stage="6" data-state="running" data-animated="false"/);
  assert.doesNotMatch(html, /HTTP|secret|Traceback|TTS/); assert.ok(controls(html).includes('processing-refresh'));
});
for (const kind of ['qc', 'shortage', 'quote_missing']) test(`${kind}: two step-aware edits, no retry`, () => {
  const calls = [], task = makeTask({ status: 'failed', errorKind: kind, badRows: [2, 2, 4, -1, NaN] });
  const elements = nodes(Processing(props({ task, onEdit: step => calls.push(step) })));
  for (const key of ['processing-edit-script', 'processing-edit-materials']) elements.find(n => n.props?.['data-control'] === key).props.onClick();
  assert.deepEqual(calls, [1, 2]); const html = render({ task });
  // A resumable shortage additionally offers the explicit, confirm-first repeated-footage option; never an ordinary retry.
  assert.deepEqual(controls(html), [...(kind === 'shortage' ? ['processing-reuse-shots', 'processing-reuse-restart'] : []), 'processing-edit-script', 'processing-edit-materials', 'processing-delete']);
  assert.doesNotMatch(html, /processing-retry/);
  assert.doesNotMatch(html, /重新提交（算|回去改第 2、2|NaN/);
});
for (const kind of ['network', 'transient']) test(`${kind}: explicit supported same-task retry only`, () => {
  const html = render({ task: makeTask({ status: 'failed', errorKind: kind, revision: 3 }) });
  assert.match(html, />再试一次<\/button>/); assert.match(html, /不重新上传，也不新增提交次数/);
  assert.doesNotMatch(html, /从第一步|重新提交（算/);
});
test('support missing/false hides v2 retry; legacy paid confirmation remains possible', () => {
  for (const retry_same_supported of [undefined, false]) assert.ok(!controls(render({ task: makeTask({ status: 'failed', retry_same_supported }) })).includes('processing-retry'));
  assert.match(render({ task: makeTask({ status: 'failed', lifecycle_v2: false }) }), /可能再次产生费用/);
  assert.ok(!controls(render({ task: makeTask({ status: 'failed', lifecycle_v2: false, revision: 2 }) })).includes('processing-retry'));
});
test('busy disables every button and optional editing adds no fake controls', () => {
  const tree = nodes(Processing(props({ busy: true, task: makeTask({ status: 'failed', errorKind: 'qc' }) })));
  assert.ok(tree.filter(n => n.type === 'button').every(n => n.props.disabled));
  assert.deepEqual(controls(render({ onEdit: undefined, task: makeTask({ status: 'failed', errorKind: 'qc' }) })), ['processing-delete']);
});
test('honest unavailable recovery is visible, other raw errors are not echoed', () => {
  assert.ok(render({ error: RECOVERY_UNAVAILABLE }).includes(RECOVERY_UNAVAILABLE));
});
test('failed task says where it stopped and why: plain cause plus redacted technical detail', () => {
  const task = makeTask({ status: 'failed', errorKind: 'transient', current_stage: 2, error_stage: '听素材里的声音',
    error_message: "'/Users/me/Documents/golden_mic/data/tasks/505ee5c12c544208830841ecea123878/raw/1b075c83912adc7b5d493c3093ebe0e0.mp4' is not in the subpath of 'data/tasks/505ee5c12c544208830841ecea123878' api_key=sk-SECRET123 done" });
  const html = render({ task });
  assert.match(html, /停在了“听素材里的声音”这一步。/); assert.match(html, /data-testid="processing-cause"/);
  assert.match(html, /data-testid="processing-technical"/); assert.match(html, /技术详情/);
  assert.match(html, /is not in the subpath of/);
  assert.doesNotMatch(html, /\/Users\/me|505ee5c12c544208830841ecea123878|SECRET123|sk-/);
  // No stage/message at all still gives an honest, non-empty statement and no empty details block.
  const bare = render({ task: makeTask({ status: 'failed', errorKind: 'transient', current_stage: null, stages: [] }) });
  assert.doesNotMatch(bare, /data-testid="processing-technical"/); assert.match(bare, /这次没做成|制作服务暂时没能完成这一步/);
});
test('an action error on a known failure is shown as such, never relabelled as a lost connection', () => {
  const task = makeTask({ status: 'failed', errorKind: 'transient' });
  const html = render({ task, error: '这个作品没有保存完整、可核验的原始稿件和素材记录，无法恢复为草稿。' });
  assert.match(html, /data-testid="processing-action-error"/); assert.match(html, /刚才的操作没成功：这个作品没有保存完整/);
  assert.doesNotMatch(html, /暂时无法确认最新状态|状态需要确认/);
  assert.match(html, /重新读取状态/); assert.match(html, /data-control="processing-edit-script"/);
  const retry = nodes(Processing(props({ task, error: 'x' }))).find(n => n.props?.['data-control'] === 'processing-retry');
  assert.equal(retry.props.disabled, false, 'known failure + action error must not block an explicit retry');
  // Without a known failure the connection really is in doubt.
  const lost = render({ task: makeTask(), error: 'Failed to fetch' });
  assert.match(lost, /状态需要确认/); assert.match(lost, /暂时无法确认最新状态/); assert.doesNotMatch(lost, /刚才的操作没成功/);
});
test('failed mixed/original task offers an optional AI-voiceover fallback; the user decides', () => {
  for (const mode of ['mixed', 'original']) {
    const calls = [];
    const task = makeTask({ status: 'failed', errorKind: 'transient', mode });
    const html = render({ task });
    assert.match(html, /data-testid="processing-alternative"/); assert.match(html, /临时替代方案：改用 AI 配音/);
    assert.match(html, /由你确认后才会重新提交/); assert.match(html, /data-control="processing-switch-voiceover"/);
    // Choosing it is a click on one explicit button that asks the host to recover the draft in voiceover mode.
    const button = nodes(Processing(props({ task, onEdit: (...args) => calls.push(args) }))).find(n => n.props?.['data-control'] === 'processing-switch-voiceover');
    button.props.onClick(); assert.deepEqual(calls, [[1, 'voiceover']]);
    // The ordinary choices stay next to it, so declining the fallback is just another button.
    assert.match(html, /data-control="processing-edit-script"/); assert.match(html, /data-control="processing-delete"/);
  }
  assert.doesNotMatch(render({ task: makeTask({ status: 'failed', errorKind: 'transient', mode: 'voiceover' }) }), /processing-alternative|processing-switch-voiceover/);
  assert.doesNotMatch(render({ task: makeTask({ status: 'cancelled', mode: 'mixed' }) }), /processing-alternative/);
});
test('failure text redaction removes paths, long identifiers, key-like values and control characters', () => {
  const { redactFailureText, describeFailure } = load('src/lib/failureDetail.ts');
  const redacted = redactFailureText("open C:\\Users\\me\\data\\clip.mp4 failed; /var/x/y/z.mp4 token: abcdef0123456789abcdef0123456789 bad\u0000byte");
  assert.doesNotMatch(redacted, /Users|\/var\/x|abcdef0123456789|\u0000/); assert.match(redacted, /clip\.mp4/); assert.match(redacted, /z\.mp4/);
  assert.equal(redactFailureText({}), ''); assert.equal(redactFailureText(undefined), '');
  assert.ok(redactFailureText('字'.repeat(5000)).length <= 401);
  assert.deepEqual({ ...describeFailure(null) }, { stage: '', detail: '' });
  assert.equal(describeFailure({ error_stage: '切分镜头', error_message: 'ok' }).stage, '切分镜头');
});
test('upload byte progress remains distinct from production, finished transfer is not acceptance', () => {
  const html = render({ uploading: true, task: null, upload: { loaded: 80, total: 100, startedAt: 0, finishedAt: 2500 } });
  assert.match(html, />80%<\/strong>/); assert.match(html, /等待服务器确认/); assert.match(html, /2.5 秒/);
  assert.equal((html.match(/data-state="pending"/g) ?? []).length, 10);
  assert.doesNotMatch(html, />上传完成<|已完成|data-state="done"/);
});
test('multi-operation plan uses trusted Chinese labels, never arbitrary server op text', () => {
  const html = render({ task: makeTask({ plan: { idx: 0, steps: [{ op: 'edit', run: [7] }, { op: 'constructor', run: [9] }, { op: 'replace_shot', run: [6, 9] }] } }) });
  assert.match(html, /第 1\/3 步 · 改字/); assert.match(html, /接下来：应用修改 → 换画面/); assert.doesNotMatch(html, /constructor/);
});
test('dev-only static diagnostics contain no server messages or paths', () => {
  const Dev = loader(true)('src/components/Processing.tsx').default;
  const html = renderToStaticMarkup(React.createElement(Dev, props()));
  assert.match(html, /processing-dev/); assert.match(html, /服务端权重/); assert.doesNotMatch(html, /TTS|ASR|Traceback|secret|\/api\//);
});
test('CSS module owns exact A-4 dimensions and reduced-motion/narrow reflow; no global processing selectors', () => {
  const css = read('src/components/Processing.module.css'); postcss.parse(css);
  for (const pattern of [/max-width:760px/, /width:64px; height:64px/, /font-size:3.25rem/, /grid-template-columns:34px minmax\(0,1fr\) auto/, /prefers-reduced-motion/, /max-width:600px/]) assert.match(css, pattern);
  const source = read('src/components/Processing.tsx');
  assert.doesNotMatch(source, /shell-|styles\.css|task\?\.message|stage\?\.message|task\?\.error_message|task\.error_message/);
  // Failure text is only shown after redaction (paths, long ids, key-like values, control chars, length cap).
  assert.match(source, /import \{ describeFailure, redactFailureText \} from '\.\.\/lib\/failureDetail'/);
});

// Execute the actual hooks with task-scoped in-memory transport. Uses the real
// parseTask validator; no guessed receipt parser or reimplementation of hooks.
const workspace = read('src/components/Workspace.tsx');
const ast = ts.createSourceFile('Workspace.tsx', workspace, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const printer = ts.createPrinter();
function declaration(name) {
  const found = [];
  function visit(n) { if (ts.isFunctionDeclaration(n) && n.name?.getText(ast) === name) found.push(n); ts.forEachChild(n, visit); }
  visit(ast); assert.equal(found.length, 1, name); return printer.printNode(ts.EmitHint.Unspecified, found[0], ast);
}
function harness({ taskPatch = {}, reply, saved = { status: 'missing', draft: null } } = {}) {
  const calls = [], state = { dialog: null, error: '', task: makeTask({ status: 'failed', errorKind: 'transient', ...taskPatch }), notices: [], drafts: [], reads: 0, snapshotReads: 0, historyReads: 0 };
  const target = { id: 'owned-task', token: 'only-in-memory', scope: new AbortController(),
    async request(path, init = {}) { calls.push({ path, method: init.method ?? 'GET', body: init.body });
      return reply ? reply(path, init, state, env) : init.method === 'POST' ? { task_id: target.id, id: target.id, status: 'queued', resume_from: 6 } : ++state.reads === 1 ? state.task : makeTask({ status: 'queued' });
    } };
  const env = { console, Promise, Error, errorText, parseTask, task: state.task, serviceReady: true,
    isObject: value => value !== null && typeof value === 'object' && !Array.isArray(value), pathFor: id => `/api/tasks/${id}`,
    selectedRef: { current: target }, actionLock: { current: false }, intentRef: { current: null }, pageRef: { current: 'processing' },
    alive: { current: true }, generation: { current: 1 }, draftRef: { current: null }, draftDirty: { current: false },
    draftBlocked: { current: false }, draftNeedsClear: false, draftSuppressed: { current: false }, draftTimer: { current: null }, DRAFT_SAVE_DELAY_MS: 300,
    submissionRecovery: { read: () => { state.snapshotReads++; return saved; } }, setDialog: value => { state.dialog = value; }, setError: value => { state.error = value; },
    setBusy: noop, setNotice: value => state.notices.push(value), setTask: value => { state.task = value; }, observeTask: noop,
    showPage: page => { env.pageRef.current = page; }, setTaskRetry: noop, refreshHistory: () => { state.historyReads++; },
    validateDraft: forbidden, writeDraftCache: forbidden, retainWork: forbidden, hasDraftContent: () => false, window: { confirm: () => true },
    cancelReads: noop, unmountWizard: noop, select: noop, setUpload: noop, setMissing: noop, setDraftPending: noop,
    mountWizard: value => state.drafts.push(value), writeHash: noop, setDraftStatus: noop, restoredDraftNote: () => '',
    clearTimeout: noop, setTimeout: () => 1, saveDraft: forbidden,
  };
  vm.createContext(env);
  vm.runInContext(ts.transpileModule([declaration('mutate'), declaration('retryOriginal'), declaration('editFailedDraft')].join('\n'),
    { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } }).outputText, env);
  return { env, state, calls, target, async retry(options) { env.retryOriginal(options); if (state.dialog) await state.dialog.action(); } };
}
test('v2 retry accepts actual revision-less cache-resume receipt; one POST surrounded by GETs', async () => {
  const h = harness(); await h.retry(); assert.deepEqual(h.calls.map(c => c.method), ['GET', 'POST', 'GET']);
  assert.equal(h.calls[1].path, '/api/tasks/owned-task/retry'); assert.deepEqual(JSON.parse(h.calls[1].body), { expected_revision: 0 });
  assert.equal(h.state.task.status, 'queued'); assert.equal(h.state.error, ''); assert.match(h.state.dialog.text, /不新增提交次数/);
});
test('accepting repeated footage asks first, then sends exactly one retry that carries the explicit consent', async () => {
  const h = harness({ taskPatch: { errorKind: 'shortage', mode: 'mixed' } });
  h.env.retryOriginal({ allowShotReuse: true });
  assert.equal(h.calls.length, 0, 'opening the dialog sends nothing');
  assert.equal(h.state.dialog.title, '接受重复使用画面吗？'); assert.equal(h.state.dialog.confirm, '接受并继续');
  assert.match(h.state.dialog.text, /不是定格补帧/); assert.match(h.state.dialog.text, /取消，回去删减稿子或多传素材/);
  await h.state.dialog.action();
  assert.deepEqual(h.calls.map(c => c.method), ['GET', 'POST', 'GET']);
  assert.deepEqual(JSON.parse(h.calls[1].body), { expected_revision: 0, allow_shot_reuse: true });
  assert.equal(h.state.task.status, 'queued');
});
test('declining the repeated-footage dialog changes nothing; ordinary retry never carries consent', async () => {
  const declined = harness({ taskPatch: { errorKind: 'shortage' } });
  declined.env.retryOriginal({ allowShotReuse: true }); assert.ok(declined.state.dialog); assert.equal(declined.calls.length, 0);
  const plain = harness({ taskPatch: { errorKind: 'transient' } }); await plain.retry();
  assert.deepEqual(JSON.parse(plain.calls[1].body), { expected_revision: 0 });
  // A shortage is not an ordinary retry: without the explicit consent path nothing is offered.
  const ordinary = harness({ taskPatch: { errorKind: 'shortage' } }); await ordinary.retry();
  assert.equal(ordinary.state.dialog, null); assert.equal(ordinary.calls.length, 0);
});
for (const [name, taskPatch] of [['a non-shortage failure', { errorKind: 'transient' }], ['original-sound mode', { errorKind: 'shortage', mode: 'original' }],
  ['a task that cannot resume', { errorKind: 'shortage', retry_same_supported: false }], ['a legacy task', { errorKind: 'shortage', lifecycle_v2: false }]]) {
  test(`repeated footage is not offered for ${name}`, async () => {
    const h = harness({ taskPatch }); await h.retry({ allowShotReuse: true });
    assert.equal(h.state.dialog, null); assert.equal(h.calls.length, 0);
  });
}
test('repeated-footage option renders only for a resumable shortage, and its button carries the consent', () => {
  const calls = [];
  const shortage = makeTask({ status: 'failed', errorKind: 'shortage', mode: 'voiceover' });
  const html = render({ task: shortage });
  assert.match(html, /data-testid="processing-reuse"/); assert.match(html, /替代方案：允许画面重复出现/); assert.match(html, /data-control="processing-reuse-shots"/);
  assert.match(html, /data-control="processing-edit-script"/, 'declining still has the normal edit choices');
  const button = nodes(Processing(props({ task: shortage, onRetry: (...args) => calls.push(args) }))).find(n => n.props?.['data-control'] === 'processing-reuse-shots');
  button.props.onClick(); assert.equal(JSON.stringify(calls), JSON.stringify([[{ allowShotReuse: true }]]));
  for (const patch of [{ mode: 'original' }, { lifecycle_v2: false }, { errorKind: 'transient' }, { errorKind: 'qc' }, { status: 'cancelled' }]) {
    assert.doesNotMatch(render({ task: makeTask({ status: 'failed', errorKind: 'shortage', ...patch }) }), /processing-reuse/, JSON.stringify(patch));
  }
  // A task that cannot resume (e.g. the program was updated) still offers the restart-with-consent path, not the resume button.
  const stale = render({ task: makeTask({ status: 'failed', errorKind: 'shortage', retry_same_supported: false }) });
  assert.doesNotMatch(stale, /processing-reuse-shots/); assert.match(stale, /data-control="processing-reuse-restart"/);
  const edits = [];
  const restart = nodes(Processing(props({ task: shortage, onEdit: (...args) => edits.push(args) }))).find(n => n.props?.['data-control'] === 'processing-reuse-restart');
  restart.props.onClick(); assert.equal(JSON.stringify(edits), JSON.stringify([[1, null, { allowShotReuse: true }]]));
  assert.doesNotMatch(render({ task: shortage, onEdit: undefined }), /processing-reuse-restart/);
  // The ordinary retry button must not forward the click event as an options object.
  const retry = nodes(Processing(props({ task: makeTask({ status: 'failed', errorKind: 'transient' }), onRetry: (...args) => calls.push(args) }))).find(n => n.props?.['data-control'] === 'processing-retry');
  calls.length = 0; retry.props.onClick({ type: 'click' }); assert.equal(JSON.stringify(calls), JSON.stringify([[]]));
});
for (const malformed of [false, true]) test(`ambiguous ${malformed ? 'malformed receipt' : 'lost response'} does one read-only reconciliation`, async () => {
  const h = harness({ reply(path, init, state) {
    if (init.method === 'POST') { if (malformed) return { task_id: 'other', status: 'queued' }; throw Error('lost'); }
    return ++state.reads === 1 ? state.task : makeTask({ status: 'running' });
  } });
  await h.retry(); assert.deepEqual(h.calls.map(c => c.method), ['GET', 'POST', 'GET']); assert.equal(h.state.task.status, 'running');
});
test('failed reconciliation retains last stages and uncertainty without resubmitting', async () => {
  const h = harness({ reply(path, init, state) { if (init.method === 'POST' || state.reads++) throw Error('lost'); return state.task; } });
  await h.retry(); assert.equal(h.calls.filter(c => c.method === 'POST').length, 1); assert.match(h.state.error, /尚未确认/);
  assert.equal(h.state.task.status, 'failed'); assert.equal(h.state.task.stages.length, 10);
});
for (const status of [401, 403, 404, 410]) test(`retry auth ${status}: preflight blocks POST; denied mutation reconciles once without resubmission`, async () => {
  const preflight = harness({ reply: () => { throw new AppApiError('denied', status); } });
  await preflight.retry(); assert.deepEqual(preflight.calls.map(c => c.method), ['GET']);
  assert.match(preflight.state.error, /没有提交重试/);
  const mutation = harness({ reply(path, init, state) {
    if (init.method === 'POST' || state.reads++) throw new AppApiError('denied', status);
    return state.task;
  } });
  await mutation.retry(); assert.deepEqual(mutation.calls.map(c => c.method), ['GET', 'POST', 'GET']);
  assert.match(mutation.state.error, /尚未确认/); assert.equal(mutation.state.task.status, 'failed');
});
test('v2 support is required initially and rechecked immediately before POST', async () => {
  const initial = harness({ taskPatch: { retry_same_supported: false } }); await initial.retry(); assert.equal(initial.calls.length, 0);
  const changed = harness({ reply: () => makeTask({ status: 'failed', retry_same_supported: false }) });
  await assert.rejects(changed.retry()); assert.equal(changed.calls.length, 1);
});
for (const fence of ['selection', 'generation', 'intent', 'abort', 'page', 'alive']) test(`stale retry dialog is fenced by ${fence}`, async () => {
  const h = harness(); h.env.retryOriginal();
  if (fence === 'selection') h.env.selectedRef.current = { ...h.target };
  if (fence === 'generation') h.env.generation.current++;
  if (fence === 'intent') h.env.intentRef.current = {};
  if (fence === 'abort') h.target.scope.abort();
  if (fence === 'page') h.env.pageRef.current = 'create';
  if (fence === 'alive') h.env.alive.current = false;
  await h.state.dialog.action(); assert.equal(h.calls.length, 0);
});
test('navigation while preflight GET is pending prevents retry POST', async () => {
  let release; const pending = new Promise(resolve => { release = resolve; });
  const h = harness({ reply: () => pending }); const action = h.retry(); h.env.generation.current++;
  release(makeTask({ status: 'failed' })); await action; assert.equal(h.calls.length, 1);
});
test('navigation after POST prevents old receipt/GET from mutating new screen', async () => {
  const h = harness({ reply(path, init, state, env) { if (init.method === 'POST') { env.generation.current++; return { task_id: 'owned-task', status: 'queued' }; } return state.task; } });
  await h.retry(); assert.equal(h.calls.length, 2); assert.equal(h.state.notices.length, 0); assert.equal(h.state.task.status, 'failed');
});
test('legacy retry retains paid restart confirmation and revision receipt contract', async () => {
  const h = harness({ taskPatch: { lifecycle_v2: false }, reply(path, init, state) { return init.method === 'POST' ? { task_id: 'owned-task', status: 'queued', revision: 0 } : ++state.reads === 1 ? state.task : makeTask({ lifecycle_v2: false, status: 'queued' }); } });
  await h.retry(); assert.match(h.state.dialog.text, /从第一步.*可能再次收费/); assert.equal(h.calls.filter(c => c.method === 'POST').length, 1);
});
function assertUnrecovered(h, step) {
  assert.deepEqual(h.calls.map(c => [c.path, c.method, JSON.parse(c.body)]), [['/api/tasks/owned-task/recover-draft', 'POST', { step }]]);
  assert.equal(h.state.drafts.length, 0); assert.equal(h.env.draftRef.current, null);
  assert.equal(h.env.selectedRef.current, h.target); assert.equal(h.env.pageRef.current, 'processing');
  assert.equal(h.state.snapshotReads, 0); assert.equal(h.state.historyReads, 1);
  assert.match(h.state.error, /现有草稿未替换.*不会自动重试/);
}
test('accepted browser snapshot cannot replace a valid independent server recovery receipt', async () => {
  const h = harness({ saved: { status: 'ready', draft: { draftTaskId: 'owned-task', draftTaskToken: 'bound-token' } } });
  await h.env.editFailedDraft(2); assertUnrecovered(h, 2); assert.match(h.state.error, /恢复回执无效/);
});
test('old accepted draft response cannot authorize editing the submitted task', async () => {
  const h = harness({ reply: () => ({ task_id: 'owned-task', accepted: true, script: 'actual original', files: [{ file_id: 'actual-id' }] }) });
  await h.env.editFailedDraft(1); assertUnrecovered(h, 1); assert.match(h.state.error, /恢复回执无效/);
});
test('failed or wrong-task recovery POST cannot fabricate a draft or retry', async () => {
  for (const reply of [() => { throw Error('offline'); }, () => ({ task_id: 'wrong', accepted: true })]) {
    const h = harness({ reply }); await h.env.editFailedDraft(2); assertUnrecovered(h, 2);
    assert.match(h.state.error, /offline|恢复回执无效/);
  }
});
test('stale recovery POST does not report against another selection', async () => {
  let release; const pending = new Promise(resolve => { release = resolve; }); const h = harness({ reply: () => pending });
  const action = h.env.editFailedDraft(2); h.env.generation.current++; release({ task_id: 'owned-task', accepted: true });
  await action; assert.equal(h.state.error, ''); assert.equal(h.state.drafts.length, 0);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].method, 'POST'); assert.equal(h.state.historyReads, 0);
});
for (const step of [1, 2]) test(`unsupported legacy recovery at step ${step} cannot fall back to unscoped browser input`, async () => {
  const h = harness({ taskPatch: { lifecycle_v2: false }, saved: { status: 'ready', draft: { script: 'original', files: [], step: 3 } },
    reply: () => { throw new AppApiError('recovery_legacy_unsupported', 409); } });
  await h.env.editFailedDraft(step); assertUnrecovered(h, step); assert.match(h.state.error, /recovery_legacy_unsupported/);
});