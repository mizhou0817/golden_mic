import assert from 'node:assert/strict';
import { launchBrowser } from './browser.mjs';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';
import nodeTest from 'node:test';
const test = process.argv[1]?.endsWith('test-v2-recording-affordance.mjs') ? nodeTest : () => {};
export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
// Reuse declarations only, never register the unrelated suite. Full production
// TSX/API + deterministic hook/clock doubles; these are NOT real-mic tests.
const source = readFileSync(resolve(root, 'scripts/test-v2-history-checks.mjs'), 'utf8');
const ast = ts.createSourceFile('helpers.mjs', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const names = new Set(['forbidden', 'tick', 'deferred', 'jsx', 'flat', 'text', 'hooks', 'loader', 'runtime']);
const shared = ast.statements.filter(n => ts.isFunctionDeclaration(n) ? names.has(n.name?.text)
  : ts.isVariableStatement(n) && n.declarationList.declarations.every(d => names.has(d.name.getText(ast))));
const env = { root, assert, existsSync, readFileSync, resolve, dirname, vm, ts, URL, URLSearchParams, Headers, Response, FormData, Blob, File,
  AbortController, DOMException, setTimeout, clearTimeout };
vm.createContext(env); vm.runInContext(shared.map(n => n.getText(ast)).join('\n') + '\nglobalThis.helpers={runtime,loader,flat,text,deferred};', env);
export const { runtime, loader, flat, text, deferred } = env.helpers;
export const rowFixture = (patch = {}) => ({ sentence_id: 1, sentence: 'Synthetic narration', duration: 3, shot_id: 4,
  description: 'Synthetic source', thumb_url: '', confidence: .6, is_fallback: false, audio_kind: 'tts', visual_beats: [], kind: 'narration', ...patch });
export const fixture = (mode = 'voiceover') => ({ task_id: 'recording-fixture', revision: 2, status: 'done', script: '', mode,
  preferences: { pacing: 'normal' }, report: { task_id: 'recording-fixture', revision: 2, mode, quality: null,
    rows: [rowFixture(mode === 'original' ? { kind: 'quote', audio_kind: 'sync' } : {})] },
  timings: [], shots: [], current_shots: [], versions: [] });
export function harness(ctx = fixture(), propPatch = {}) {
  const r = runtime(), calls = [], permissions = [], recorders = [];
  r.globals.window.isSecureContext = true;
  r.globals.navigator = { mediaDevices: { getUserMedia() { const p = deferred(); permissions.push(p); return p.promise; } } };
  r.globals.MediaRecorder = class {
    static isTypeSupported() { return true; }
    constructor(stream) { this.stream = stream; this.state = 'inactive'; this.mimeType = 'audio/webm'; recorders.push(this); }
    start() { this.state = 'recording'; }
    stop() { this.state = 'inactive'; this.onstop?.(); }
  };
  const load = loader(r.globals, r.stubs, 'startRecording, chooseFile, reload, recordingSession, recording, micStarting, countdown, lock, draftRef, updateDraft, editable, error, notice, canExport');
  const props = { taskId: ctx.task_id, onProcessing() {}, onChanged() {}, onDelete() {}, onNew() {}, onError() {},
    request: async (path, init = {}) => { calls.push({ path, init });
      if ((init.method ?? 'GET') !== 'GET') throw Error('Unexpected write');
      if (path.endsWith('/context')) return ctx;
      if (path.includes('/versions/')) return { ...ctx.report, revision: 1 };
      if (path.endsWith('/checks')) return { revision: 2, checks: [], blocking_count: 0, pending_count: 0, passed: true };
      throw Error('Unexpected read'); }, ...propPatch };
  const m = r.mount(load('src/components/ResultWorkbench.tsx').ResultWorkbenchEditor, props);
  return { ...r, ...m, load, calls, permissions, recorders,
    upload: () => flat(m.tree()).find(n => n.type === 'input' && n.props.type === 'file'),
    button: label => flat(m.tree()).find(n => n.type === 'button' && text(n) === label),
    async advance() { const found = [...r.timers].find(([, t]) => t.delay === 1000); assert.ok(found); r.timers.delete(found[0]); found[1].fn(); await m.settle(); },
  };
}
const stream = () => { const track = { stops: 0, stop() { this.stops++; } }; return { track, getTracks: () => [track] }; };
const pending = () => ({ base: 2, sentences: { 1: { text: 'Exact pending text', instruction: 'Exact pending picture' } }, deleted: [],
  pendTrim: { 8: { start: .3, end: .8 } }, pendTake: { 9: 'take-kept' }, pendToNarration: [7], pendSpeakers: {}, pacing: 'slow' });
const plain = v => JSON.parse(JSON.stringify(v));
test('upload is visible before any error for editable A/B narration; inactive-only', async () => {
  for (const mode of ['voiceover', 'mixed']) { const h = harness(fixture(mode)); h.render(); await h.settle();
    assert.ok(h.upload()); assert.match(h.upload().props.accept, /\.wav/); assert.equal(h.probe().error, '');
    h.button('我来读').props.onClick(); await h.settle(); assert.equal(h.upload(), undefined);
    h.button('取消等待麦克风').props.onClick(); await h.settle(); assert.ok(h.upload()); h.stop(); }
});
test('quote/C/history/sample/nonowner/deleted/stale views expose no enabled recording upload', async () => {
  for (const kind of ['quote', 'C', 'history', 'sample', 'nonowner', 'deleted', 'stale']) {
    const ctx = fixture(kind === 'C' ? 'original' : 'mixed'); if (kind === 'quote') ctx.report.rows[0].kind = 'quote';
    const h = harness(ctx, kind === 'sample' ? { sample: ctx.report } : kind === 'nonowner' ? { isOwner: false } : {});
    h.render(); await h.settle();
    if (kind === 'history') await h.probe().reload(1);
    if (kind === 'deleted' || kind === 'stale') h.probe().updateDraft(() => ({ ...pending(), ...(kind === 'deleted' ? { deleted: [1] } : { base: 1 }) }));
    await h.settle(); assert.equal(h.upload(), undefined, kind); void h.probe().startRecording(); assert.equal(h.permissions.length, 0, kind); h.stop();
  }
});
for (const phase of ['permission', 'countdown', 'recording']) test(`actual cancel callback at ${phase}: reset, no POST, exact pending untouched`, async () => {
  const h = harness(); h.render(); await h.settle(); h.probe().updateDraft(() => pending()); await h.settle();
  const before = plain(h.probe().draftRef.current); h.button('我来读').props.onClick(); await h.settle();
  const s = stream(); if (phase !== 'permission') { h.permissions[0].resolve(s); await h.settle(); }
  if (phase === 'recording') for (let i = 0; i < 3; i++) await h.advance();
  const cancel = h.button(phase === 'permission' ? '取消等待麦克风' : phase === 'countdown' ? '取消准备录音' : '取消并丢弃录音'); assert.ok(cancel);
  const recorder = h.recorders[0], lateData = recorder?.ondataavailable, lateStop = recorder?.onstop;
  if (recorder && phase === 'recording') recorder.ondataavailable({ data: new Blob(['synthetic-not-real-speech']) });
  cancel.props.onClick(); await h.settle();
  if (phase === 'permission') { h.permissions[0].resolve(s); await h.settle(); }
  lateData?.({ data: new Blob(['late']) }); lateStop?.(); await h.settle();
  assert.equal(s.track.stops, 1); assert.equal(h.probe().recording, false); assert.equal(h.probe().micStarting, false);
  assert.equal(h.probe().countdown, null); assert.equal(h.probe().lock.current, false); assert.equal(h.probe().recordingSession.current, null);
  assert.ok(h.upload()); assert.deepEqual(plain(h.probe().draftRef.current), before);
  assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 0); assert.ok(![...h.timers.values()].some(t => t.delay === 1000)); h.stop();
});
test('late old permission grant/rejection cannot reset a new recording generation', async () => {
  for (const rejects of [false, true]) { const h = harness(); h.render(); await h.settle();
    h.probe().startRecording(); await h.settle(); h.button('取消等待麦克风').props.onClick(); await h.settle();
    h.probe().startRecording(); await h.settle(); const current = h.probe().recordingSession.current, next = stream(), old = stream();
    h.permissions[1].resolve(next); await h.settle();
    if (rejects) h.permissions[0].reject(new DOMException('Synthetic denied', 'NotAllowedError')); else h.permissions[0].resolve(old);
    await h.settle(); assert.equal(h.probe().recordingSession.current, current); assert.equal(h.probe().countdown, 3);
    assert.equal(h.probe().error, ''); assert.equal(old.track.stops, rejects ? 0 : 1); assert.equal(next.track.stops, 0);
    h.stop(); assert.equal(next.track.stops, 1); }
});
test('unmount during permission releases late tracks; empty recording/file never posts', async () => {
  const h = harness(); h.render(); await h.settle(); const work = h.probe().startRecording(); h.stop(); const s = stream(); h.permissions[0].resolve(s); await work;
  assert.equal(s.track.stops, 1); assert.equal(h.calls.filter(c => c.init.method === 'POST').length, 0);
  const e = harness(); e.render(); await e.settle(); e.probe().chooseFile(new File([], 'empty.wav')); await e.settle();
  e.probe().startRecording(); await e.settle(); const emptyStream = stream(); e.permissions[0].resolve(emptyStream); await e.settle();
  for (let i = 0; i < 3; i++) await e.advance(); e.button('停止并保留录音').props.onClick(); await e.settle();
  assert.equal(e.calls.filter(c => c.init.method === 'POST').length, 0); assert.ok(e.probe().error); assert.ok(e.upload()); e.stop();
});
test('actual file onChange uploads exactly once and keeps independent pending fields and exact receipt', async () => {
  const h = harness(), writes = [], original = h.props.request;
  const receipt = { recording_id: 'b'.repeat(32), sentence_id: 1, revision: 2, duration: 2.7, size: 4,
    audio_source: 'recording', transcript_verified: false };
  h.props.request = async (path, init) => {
    if (init?.method !== 'POST') return original(path, init);
    writes.push({ path, body: init.body }); return receipt;
  };
  h.render(); await h.settle(); h.probe().updateDraft(() => pending()); await h.settle();
  const file = new File(['test'], 'synthetic.wav', { type: 'audio/wav' }), target = { files: [file], value: 'selected' };
  h.upload().props.onChange({ target }); await h.settle();
  assert.equal(target.value, ''); assert.equal(writes.length, 1); assert.match(writes[0].path, /\/recordings$/);
  assert.equal(writes[0].body.get('audio').size, 4); assert.equal(writes[0].body.get('rowid'), '1'); assert.equal(writes[0].body.get('expected_revision'), '2');
  const actual = h.probe().draftRef.current, expected = pending();
  expected.sentences[1].recording = receipt;
  assert.deepEqual(plain(actual), expected); assert.equal(actual.sentences[1].file, undefined); h.stop();
});
test('recorder error callback releases tracks and preserves pending receipt/file exactly', async () => {
  const h = harness(); h.render(); await h.settle();
  const file = new File(['prior'], 'prior.wav'), receipt = { recording_id: 'b'.repeat(32), sentence_id: 1, revision: 2, duration: 1, size: 5, audio_source: 'recording', transcript_verified: false };
  const d = pending(); Object.assign(d.sentences[1], { file, recording: receipt }); h.probe().updateDraft(() => d); await h.settle();
  h.probe().startRecording(); await h.settle(); const s = stream(); h.permissions[0].resolve(s); await h.settle();
  for (let i = 0; i < 3; i++) await h.advance(); h.recorders[0].onerror(); await h.settle();
  assert.equal(s.track.stops, 1); assert.equal(h.probe().draftRef.current, d); assert.equal(d.sentences[1].file, file);
  assert.equal(d.sentences[1].recording, receipt); assert.ok(h.probe().error); assert.ok(h.upload()); h.stop();
});
for (const name of ['NotAllowedError', 'NotFoundError', 'NotReadableError', 'SecurityError', 'AbortError', 'UnknownError']) test(`synthetic permission error ${name} restores upload without pending mutation`, async () => {
  const h = harness(); h.render(); await h.settle(); h.probe().updateDraft(() => pending()); await h.settle(); const before = plain(h.probe().draftRef.current);
  h.probe().startRecording(); await h.settle(); h.permissions[0].reject(new DOMException('Synthetic only', name)); await h.settle();
  assert.ok(h.probe().error); assert.ok(h.upload()); assert.equal(h.probe().lock.current, false); assert.deepEqual(plain(h.probe().draftRef.current), before); h.stop();
});

// Native browser execution is explicit; no physical microphone permission is
// granted. Only browser-denied getUserMedia and labelled injected failures /
// AudioContext synthetic tracks. Production MediaRecorder remains untouched.
if (process.env.GM_RECORDING_BROWSER === '1') test('offline Edge: native denied permission, mocked device errors, synthetic-stream countdown/record/cancel', async () => {
  const require = createRequire(import.meta.url), { chromium } = require('playwright');
  let browser, context, stage = 'launch', pageErrors = 0, unexpected = 0;
  try {
    const factories = {}, paths = {};
    function collect(file) {
      file = resolve(root, file); const key = file.replaceAll('\\', '/'); if (factories[key]) return key;
      const src = readFileSync(file, 'utf8').replaceAll('import.meta.env', '({})');
      factories[key] = '';
      const tree = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const deps = {};
      for (const n of tree.statements) if (ts.isImportDeclaration(n) && !n.importClause?.isTypeOnly) {
        const name = n.moduleSpecifier.text; if (name === 'react' || name.endsWith('.css')) continue;
        assert.ok(name.startsWith('.')); const base = resolve(dirname(file), name);
        const child = [base, base + '.ts', base + '.tsx', base + '.json'].find(existsSync); assert.ok(child);
        if (child.endsWith('.json')) { const id = child.replaceAll('\\', '/'); factories[id] = `module.exports=${readFileSync(child, 'utf8')};`; deps[name] = id; }
        else deps[name] = collect(child);
      }
      paths[key] = deps;
      factories[key] = ts.transpileModule(src, { fileName: file, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true } }).outputText;
      return key;
    }
    const entry = collect('src/components/ResultWorkbench.tsx');
    stage = 'edge-launch';
    browser = await launchBrowser({ chromiumOnly: true, headless: true, args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] });
    context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
    await context.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.origin === 'https://recording.invalid' && u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><div id="root"></div>' });
      if (u.origin === 'https://recording.invalid' && /^\/api\/tasks\/recording-fixture\/(video|poster|thumbs\/4.jpg)$/.test(u.pathname)) return route.abort();
      unexpected++; return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(6000); page.on('pageerror', () => pageErrors++);
    stage = 'deny-browser-permission';
    const cdp = await context.newCDPSession(page);
    const { targetInfo } = await cdp.send('Target.getTargetInfo');
    assert.ok(targetInfo.browserContextId);
    await cdp.send('Browser.setPermission', { permission: { name: 'microphone' }, setting: 'denied', origin: 'https://recording.invalid', browserContextId: targetInfo.browserContextId });
    stage = 'load-offline-page';
    await page.goto('https://recording.invalid/');
    await page.addStyleTag({ content: ['src/components/ui/tokens.css', 'src/components/ResultWorkbench.css'].map(p => readFileSync(resolve(root, p), 'utf8')).join('\n') });
    await page.addScriptTag({ path: require.resolve('react').replace(/index\.js$/, 'umd/react.development.js') });
    await page.addScriptTag({ path: require.resolve('react-dom').replace(/index\.js$/, 'umd/react-dom.development.js') });
    stage = 'mount-actual-component';
    await page.addScriptTag({ content: `const factories={${Object.entries(factories).map(([k, v]) => `${JSON.stringify(k)}:function(module,exports,require){${v}\n}`).join(',')}}, paths=${JSON.stringify(paths)},cache={};
      function load(key){if(cache[key])return cache[key].exports;const m=cache[key]={exports:{}};factories[key](m,m.exports,name=>name==='react'?React:name.endsWith('.css')?{}:load(paths[key][name]));return m.exports;}
      const Component=load(${JSON.stringify(entry)}).default, ctx=${JSON.stringify(fixture())};
      window.posts=0;window.micCalls=0;window.tracks=[];window.audioContexts=[];window.permissionMode='native-denied';window.nativeMic=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia=async constraints=>{window.micCalls++;if(window.permissionMode==='native-denied')return window.nativeMic(constraints);
        if(window.permissionMode==='held')return new Promise(resolve=>window.releaseMic=resolve);
        if(window.permissionMode!=='synthetic')throw new DOMException('Labelled synthetic device error',window.permissionMode);
        const audio=new AudioContext(),dest=audio.createMediaStreamDestination();window.audioContexts.push(audio);window.tracks.push(...dest.stream.getTracks());return dest.stream;};
      window.request=async(path,init={})=>{if((init.method||'GET')!=='GET'){window.posts++;throw Error('No writes permitted');}
        if(path.endsWith('/context'))return ctx;if(path.endsWith('/checks'))return {revision:2,checks:[],blocking_count:0,pending_count:0,passed:true};throw Error('Unexpected synthetic request');};
      const root=ReactDOM.createRoot(document.getElementById('root'));ReactDOM.flushSync(()=>root.render(React.createElement(Component,{taskId:ctx.task_id,request:window.request,onProcessing(){},onChanged(){},onDelete(){},onNew(){},onError(){}})));
    ` });
    const upload = page.getByLabel('或上传音频'), start = page.getByRole('button', { name: '我来读', exact: true });
    await upload.waitFor(); stage = 'native-denied';
    assert.equal(await page.evaluate(async () => (await navigator.permissions.query({ name: 'microphone' })).state), 'denied');
    stage = 'native-denied-click'; await start.click();
    stage = 'native-denied-message'; await page.getByRole('alert').filter({ hasText: '麦克风权限未允许或被拒绝' }).waitFor(); await upload.waitFor();
    for (const name of ['NotFoundError', 'NotReadableError', 'SecurityError', 'AbortError']) {
      stage = 'mock-' + name; await page.evaluate(n => { window.permissionMode = n; }, name); await start.click(); await upload.waitFor();
    }
    stage = 'held-permission'; await page.evaluate(() => { window.permissionMode = 'held'; }); await start.click();
    await page.getByRole('button', { name: '取消等待麦克风', exact: true }).click(); await upload.waitFor();
    await page.evaluate(() => { const audio = new AudioContext(), dest = audio.createMediaStreamDestination(); window.audioContexts.push(audio); window.tracks.push(...dest.stream.getTracks()); window.releaseMic(dest.stream); });
    await page.waitForFunction(() => window.tracks.every(t => t.readyState === 'ended'));
    stage = 'synthetic-countdown'; await page.evaluate(() => { window.permissionMode = 'synthetic'; }); await start.click();
    await page.getByRole('button', { name: '取消准备录音', exact: true }).click(); await upload.waitFor();
    stage = 'synthetic-native-recorder'; await start.click(); await page.getByRole('button', { name: '停止并保留录音', exact: true }).waitFor();
    await page.getByRole('button', { name: '取消并丢弃录音', exact: true }).click(); await upload.waitFor();
    const receipt = await page.evaluate(async () => { await Promise.all(window.audioContexts.map(a => a.close()));return { posts:window.posts, micCalls:window.micCalls, tracks:window.tracks.length, stopped:window.tracks.every(t=>t.readyState==='ended') }; });
    assert.deepEqual(receipt, { posts: 0, micCalls: 8, tracks: 3, stopped: true }); assert.equal(unexpected, 0); assert.equal(pageErrors, 0);
    console.log(JSON.stringify({ recordingBrowser: browser.version(), ...receipt, nativePermissionDenied: true, syntheticStreamsOnly: true, realMicQuality: false, unexpected, pageErrors, rawArtifacts: false }));
  } catch {
    const page = context?.pages()[0];
    const safe = page ? await page.evaluate(() => ({ micCalls: window.micCalls ?? 0, posts: window.posts ?? 0,
      deniedMessage: document.body.textContent.includes('麦克风权限未允许或被拒绝'),
      unsupportedMessage: document.body.textContent.includes('不支持麦克风录音'),
      genericError: document.body.textContent.includes('操作未确认完成'),
      alerts: document.querySelectorAll('[role=alert]').length,
      uploadInputs: document.querySelectorAll('input[type=file]').length })).catch(() => null) : null;
    throw Error(`Offline recording browser failed at ${stage}; safe=${JSON.stringify(safe)}; no raw DOM/error retained`);
  }
  finally { await context?.close(); await browser?.close(); }
});

// Separate opt-in case: real React, MediaRecorder/Opus and native clocks. Only
// getUserMedia's source is replaced, BEFORE mounting. The authorized request
// boundary is a task/revision-bound in-page stub, NOT backend/auth/ASR coverage.
// No fixtures, microphone input, audio files, downloads or runner artifacts.
if (process.env.GM_RECORDING_BROWSER === '1') test('offline Edge: native recording stop/save receipt then discard retains typed pending', { timeout: 45000 }, async () => {
  const require = createRequire(import.meta.url), { chromium } = require('playwright');
  let browser, context, page, stage = 'collect', unexpected = 0, pageErrors = 0, downloads = 0, dialogs = 0;
  let crashed = false;
  try {
    const factories = {}, paths = {};
    function collect(file) {
      file = resolve(root, file); const key = file.replaceAll('\\', '/'); if (factories[key]) return key;
      const src = readFileSync(file, 'utf8').replaceAll('import.meta.env', '({})');
      factories[key] = '';
      const tree = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX), deps = {};
      for (const n of tree.statements) if (ts.isImportDeclaration(n) && !n.importClause?.isTypeOnly) {
        const name = n.moduleSpecifier.text; if (name === 'react' || name.endsWith('.css')) continue;
        assert.ok(name.startsWith('.')); const base = resolve(dirname(file), name);
        const child = [base, base + '.ts', base + '.tsx', base + '.json'].find(existsSync); assert.ok(child);
        if (child.endsWith('.json')) {
          const id = child.replaceAll('\\', '/'); factories[id] = `module.exports=${readFileSync(child, 'utf8')};`; deps[name] = id;
        } else deps[name] = collect(child);
      }
      paths[key] = deps;
      factories[key] = ts.transpileModule(src, { fileName: file, compilerOptions: {
        module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true,
      } }).outputText;
      return key;
    }
    const entry = collect('src/components/ResultWorkbench.tsx'), apiEntry = collect('src/lib/workbenchApi.ts');
    const pendingEntry = collect('src/lib/pendingEdits.ts');
    stage = 'launch';
    browser = await launchBrowser({ chromiumOnly: true, headless: true,
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] });
    context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
    await context.route('**/*', route => {
      const request = route.request(), u = new URL(request.url());
      if (request.method() === 'GET' && u.href === 'https://recording-save.invalid/')
        return route.fulfill({ contentType: 'text/html', body: '<!doctype html><div id="root"></div>' });
      if (request.method() === 'GET' && u.origin === 'https://recording-save.invalid' && !u.search.replace(/^\?revision=2$/, '')
        && /^\/api\/tasks\/recording-fixture\/(video|poster|thumbs\/4.jpg)$/.test(u.pathname)) return route.abort();
      unexpected++; return route.abort();
    });
    // Also refuse WebSockets, which do not pass through ordinary HTTP routing.
    await context.routeWebSocket('**/*', socket => { unexpected++; socket.close(); });
    page = await context.newPage(); page.setDefaultTimeout(8000);
    page.on('pageerror', () => pageErrors++); page.on('crash', () => { crashed = true; });
    page.on('download', download => { downloads++; void download.cancel(); });
    page.on('dialog', dialog => { dialogs++; void dialog.dismiss(); });
    const cdp = await context.newCDPSession(page), { targetInfo } = await cdp.send('Target.getTargetInfo');
    assert.ok(targetInfo.browserContextId);
    await cdp.send('Browser.setPermission', { permission: { name: 'microphone' }, setting: 'denied',
      origin: 'https://recording-save.invalid', browserContextId: targetInfo.browserContextId });
    await page.goto('https://recording-save.invalid/');
    await page.addStyleTag({ content: ['src/components/ui/tokens.css', 'src/components/ResultWorkbench.css']
      .map(p => readFileSync(resolve(root, p), 'utf8')).join('\n') });
    await page.addScriptTag({ path: require.resolve('react').replace(/index\.js$/, 'umd/react.development.js') });
    await page.addScriptTag({ path: require.resolve('react-dom').replace(/index\.js$/, 'umd/react-dom.development.js') });
    await page.addScriptTag({ content: `window.recordingModules=(()=>{const factories={${Object.entries(factories)
      .map(([k, v]) => `${JSON.stringify(k)}:function(module,exports,require){${v}\n}`).join(',')}},paths=${JSON.stringify(paths)},cache={};
      function load(key){if(cache[key])return cache[key].exports;const m=cache[key]={exports:{}};factories[key](m,m.exports,name=>name==='react'?React:name.endsWith('.css')?{}:load(paths[key][name]));return m.exports;}
      return {component:load(${JSON.stringify(entry)}).default,api:load(${JSON.stringify(apiEntry)}),pending:load(${JSON.stringify(pendingEntry)})};})();` });
    stage = 'install-synthetic-source-before-mount';
    await page.evaluate(ctx => {
      const check = (ok, code) => { if (!ok) throw Error(code); };
      const { component, api, pending } = window.recordingModules;
      const state = window.recordingCase = { resources: [], posts: 0, reads: [], rejected: 0, callbacks: 0,
        micCalls: 0, validations: 0, nativeRecorder: MediaRecorder, receipt: null, upload: null };
      check(MediaRecorder.isTypeSupported('audio/webm;codecs=opus'), 'native_webm_required');
      navigator.mediaDevices.getUserMedia = async constraints => {
        state.micCalls++;
        check(JSON.stringify(constraints) === JSON.stringify({ audio: true, video: false }), 'audio_only');
        const audio = new AudioContext(), oscillator = audio.createOscillator(), gain = audio.createGain();
        const destination = audio.createMediaStreamDestination(), track = destination.stream.getAudioTracks()[0];
        const resource = { audio, oscillator, gain, destination, track, trackStops: 0, oscillatorStops: 0, closes: 0 };
        state.resources.push(resource);
        const nativeStop = track.stop.bind(track);
        track.stop = () => { resource.trackStops++; nativeStop(); };
        oscillator.frequency.value = 440; gain.gain.value = .2;
        oscillator.connect(gain); gain.connect(destination); // Never connect to speakers.
        oscillator.start(); await audio.resume(); check(audio.state === 'running', 'audio_running');
        return destination.stream;
      };
      // Valid independent typed fields, not the unit fixture's deliberately short trim/string take.
      pending.writePending(ctx.task_id, { base: 2, sentences: {}, deleted: [], pendTrim: { 8: { start: .3, end: 1.8 } },
        pendTake: { 9: { take_id: 'take-kept' } }, pendToNarration: [7], pendSpeakers: { speaker: { name: 'Synthetic', title: 'Fixture' } },
        pacing: 'slow', caption_style: 'big', enhance_speech: false });
      state.readPending = () => pending.readPending(ctx.task_id);
      const request = async (path, init = {}) => {
        const method = init.method ?? 'GET';
        if (method === 'GET' && path === `/api/tasks/${ctx.task_id}/workbench/context`) { state.reads.push('context'); return structuredClone(ctx); }
        if (method === 'GET' && path === `/api/tasks/${ctx.task_id}/checks`) {
          state.reads.push('checks'); return { revision: 2, checks: [], blocking_count: 0, pending_count: 0, passed: true };
        }
        if (method !== 'POST' || path !== `/api/tasks/${ctx.task_id}/recordings`) { state.rejected++; throw Error('unapproved_request'); }
        state.posts++; check(state.posts === 1, 'one_upload_only');
        check(init.signal instanceof AbortSignal && !init.signal.aborted, 'live_request');
        check(init.headers === undefined && !path.includes('?'), 'no_plaintext_capability');
        check(init.body instanceof FormData, 'actual_formdata');
        check(JSON.stringify([...init.body.keys()].sort()) === JSON.stringify(['audio', 'expected_revision', 'rowid']), 'exact_form_fields');
        check(init.body.get('rowid') === '1' && init.body.get('expected_revision') === '2', 'task_row_revision_authorized');
        const file = init.body.get('audio');
        check(file instanceof File && file.name === 'sentence-1.webm' && file.type === 'audio/webm;codecs=opus', 'native_file');
        api.validateRecording(file); state.validations++;
        check(file.size > 0 && file.size <= 20 * 1024 * 1024, 'bounded_nonempty');
        const bytes = new Uint8Array(await file.arrayBuffer());
        check(bytes[0] === 0x1a && bytes[1] === 0x45 && bytes[2] === 0xdf && bytes[3] === 0xa3, 'webm_ebml');
        // Native multipart serialization/parse, entirely in-memory: no fetch.
        const wire = new Request('https://recording-save.invalid' + path, { method, body: init.body });
        check(/^multipart\/form-data; boundary=/.test(wire.headers.get('content-type')), 'native_multipart');
        const roundtrip = await wire.formData(), transported = roundtrip.get('audio');
        check(JSON.stringify([...roundtrip.keys()].sort()) === JSON.stringify(['audio', 'expected_revision', 'rowid'])
          && roundtrip.get('rowid') === '1' && roundtrip.get('expected_revision') === '2', 'wire_fields');
        const transportedBytes = new Uint8Array(await transported.arrayBuffer());
        check(transported.name === file.name && transported.type === file.type && transportedBytes.length === bytes.length
          && bytes.every((b, i) => b === transportedBytes[i]), 'wire_exact_audio_bytes');
        const decoded = await state.resources[0].audio.decodeAudioData(bytes.slice().buffer);
        check(decoded.duration >= .2 && decoded.duration <= api.recordingLimit(ctx.report.rows[0].duration), 'decoded_duration_cap');
        check(decoded.length > 0 && decoded.numberOfChannels > 0 && decoded.getChannelData(0).some(v => Math.abs(v) > .01), 'real_non_silent_frames');
        state.receipt = { recording_id: 'b'.repeat(32), sentence_id: 1, revision: 2, duration: decoded.duration,
          size: file.size, audio_source: 'recording', transcript_verified: false };
        check(JSON.stringify(api.parseRecordingReceipt(state.receipt, 1, 2)) === JSON.stringify(state.receipt), 'valid_receipt');
        state.upload = { size: file.size, mime: file.type, duration: decoded.duration, frames: decoded.length,
          sampleRate: decoded.sampleRate, multipartExact: true };
        await new Promise(resolve => { state.releaseReceipt = resolve; });
        return structuredClone(state.receipt);
      };
      state.root = ReactDOM.createRoot(document.getElementById('root'));
      const callback = () => { state.callbacks++; };
      ReactDOM.flushSync(() => state.root.render(React.createElement(component, { taskId: ctx.task_id, request,
        onProcessing: callback, onChanged: callback, onDelete: callback, onNew: callback, onError: callback })));
    }, fixture());
    const upload = page.getByLabel('或上传音频'), start = page.getByRole('button', { name: '我来读', exact: true });
    await upload.waitFor();
    assert.equal(await page.evaluate(async () => (await navigator.permissions.query({ name: 'microphone' })).state), 'denied');
    stage = 'type-pending';
    await page.getByRole('button', { name: '改字', exact: true }).click();
    await page.getByLabel('改这句话', { exact: true }).fill('Exact typed synthetic narration');
    await page.getByRole('button', { name: '记下修改', exact: true }).click();
    await page.getByLabel('想要什么画面', { exact: true }).fill('Exact typed synthetic picture');
    await page.getByRole('button', { name: '记下换画面', exact: true }).click();
    await page.waitForFunction(() => window.recordingCase.readPending()?.sentences[1]?.instruction === 'Exact typed synthetic picture');
    const before = await page.evaluate(() => window.recordingCase.readPending());
    assert.deepEqual(before.sentences, { 1: { text: 'Exact typed synthetic narration', instruction: 'Exact typed synthetic picture' } });
    // No page.clock / fake recorder / synthetic timer advancement. Each countdown
    // is the component's native 3 seconds; oscillator ended is a native audio-clock
    // fence providing another .8 seconds of actual encoded samples after start.
    const realFrames = () => page.evaluate(() => new Promise(resolve => {
      const r = window.recordingCase.resources.at(-1);
      r.oscillator.onended = () => resolve(); r.oscillatorStops++; r.oscillator.stop(r.audio.currentTime + .8);
    }));
    stage = 'native-countdown-and-save';
    await start.click(); await page.getByRole('button', { name: '停止并保留录音', exact: true }).waitFor();
    await realFrames(); await page.getByRole('button', { name: '停止并保留录音', exact: true }).click();
    await page.waitForFunction(() => typeof window.recordingCase.releaseReceipt === 'function');
    assert.equal(await page.getByText('录音已记下，应用修改后才会替换配音。', { exact: true }).count(), 0);
    assert.deepEqual(await page.evaluate(() => window.recordingCase.readPending()), before);
    stage = 'valid-receipt-pending-only';
    await page.evaluate(() => window.recordingCase.releaseReceipt());
    await page.getByText('录音已记下，应用修改后才会替换配音。', { exact: true }).waitFor();
    await page.waitForFunction(() => !!window.recordingCase.readPending()?.sentences[1]?.recording);
    const saved = await page.evaluate(() => ({ pending: window.recordingCase.readPending(), receipt: window.recordingCase.receipt }));
    const expected = structuredClone(before); expected.sentences[1].recording = saved.receipt;
    assert.deepEqual(saved.pending, expected); await upload.waitFor();
    stage = 'native-countdown-and-discard';
    await start.click(); await page.getByRole('button', { name: '停止并保留录音', exact: true }).waitFor();
    await realFrames(); await page.getByRole('button', { name: '取消并丢弃录音', exact: true }).click(); await upload.waitFor();
    await page.getByText('已取消本次录音，未保存本次录音内容。原有待修改内容保留，可点击“或上传音频”继续。', { exact: true }).waitFor();
    stage = 'unmount-and-cleanup';
    const result = await page.evaluate(async () => {
      const s = window.recordingCase;
      // Unmount flushes the real pending writer; an additional render cannot hide
      // late recording uploads. Native close promises drain each audio context.
      ReactDOM.flushSync(() => s.root.unmount());
      s.unmounted = true;
      for (const r of s.resources) { r.oscillator.disconnect(); r.gain.disconnect(); r.destination.disconnect(); r.closes++; await r.audio.close(); }
      return { pending: s.readPending(), posts: s.posts, rejected: s.rejected, callbacks: s.callbacks, reads: s.reads,
        micCalls: s.micCalls, validations: s.validations, nativeRecorderUnchanged: MediaRecorder === s.nativeRecorder,
        resources: s.resources.map(r => ({ trackStops: r.trackStops, oscillatorStops: r.oscillatorStops, closes: r.closes,
          trackState: r.track.readyState, audioState: r.audio.state })), upload: s.upload,
        privateMediaNotPersisted: !/sentence-1\.webm|blob:|data:|access_token|authorization|"file"/i.test(localStorage.getItem('gm-modes-v1')) };
    });
    assert.deepEqual(result.pending, expected);
    assert.equal(result.posts, 1); assert.equal(result.rejected, 0); assert.equal(result.callbacks, 0);
    assert.deepEqual(result.reads, ['context', 'checks']); assert.equal(result.micCalls, 2); assert.equal(result.validations, 1);
    assert.equal(result.nativeRecorderUnchanged, true); assert.equal(result.privateMediaNotPersisted, true);
    assert.deepEqual(result.resources, Array.from({ length: 2 }, () => ({ trackStops: 1, oscillatorStops: 1, closes: 1,
      trackState: 'ended', audioState: 'closed' })));
    assert.equal(unexpected, 0); assert.equal(pageErrors, 0); assert.equal(downloads, 0); assert.equal(dialogs, 0); assert.equal(crashed, false);
    console.log(JSON.stringify({ recordingSaveBrowser: browser.version(), posts: result.posts, applyOrStart: 0, readRequests: result.reads.length,
      micCalls: result.micCalls, releasedResources: result.resources.length, ...result.upload, pendingPreserved: true,
      nativeRecorderUnchanged: true, permissionDenied: true, syntheticOscillatorOnly: true, realMicQuality: false,
      backendAuthOrASR: false, ffmpegUsed: false, unexpected, pageErrors, downloads, dialogs, rawArtifacts: false }));
  } catch {
    throw Error(`Offline recording save failed at ${stage}; nativePageCrash=${crashed}; no raw DOM/error/audio retained`);
  } finally {
    // Failure cleanup too: never stop an already-ended track/oscillator twice.
    try { await page?.evaluate(async () => {
      const s = window.recordingCase; if (!s) return;
      s.releaseReceipt?.();
      if (!s.unmounted && s.root) { ReactDOM.flushSync(() => s.root.unmount()); s.unmounted = true; }
      for (const r of s.resources) {
        if (!r.oscillatorStops) { r.oscillatorStops++; r.oscillator.stop(); }
        if (r.track.readyState !== 'ended') r.track.stop();
        if (r.audio.state !== 'closed') { r.oscillator.disconnect(); r.gain.disconnect(); r.destination.disconnect(); r.closes++; await r.audio.close(); }
      }
    }); } finally { try { await context?.close(); } finally { await browser?.close(); } }
  }
});