import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { File, Blob } from 'node:buffer';
import ts from 'typescript';

const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const original = process.env.GM_F06_BASELINE;
const source = name => original ? readFileSync(path.join(original, name + '.before'), 'utf8') : read('../src/lib/' + name);
const mediaSource = source('mediaInput.ts'), uploadSource = source('uploadSessions.ts');
const compile = code => ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText;
function load(code, deps = {}, globals = {}) {
  const exports = {};
  vm.runInNewContext(compile(code), { exports, require: key => { assert.ok(key in deps, key); return deps[key]; },
    URL, URLSearchParams, Headers, Response, Blob, File, AbortController, DOMException,
    Uint8Array, Uint32Array, DataView, setTimeout, clearTimeout, ...globals });
  return exports;
}
const media = load(mediaSource);
const limits = { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 };
const id = 'up_' + '1'.repeat(32), task = 'synthetic_task', token = 'x'.repeat(43);
const scope = { draftTaskId: task, draftTaskToken: token, server_file_id: id };
const base = `/api/tasks/${task}/files/${id}`;
const file = () => new File([Uint8Array.of(1, 2, 3)], 'fixture.mkv', { lastModified: 1 });
const sha = '039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81';
const session = () => ({ ...scope, file_id: media.fileIdentity(file()), upload_id: id, access_token: token, sha256: sha,
  chunk_size: 8388608, put_url: base + '/chunks/{index}' });
const snapshot = patch => ({ id, name: 'fixture.mkv', bytes: 3, sec: 1, width: 64, height: 48, fps: 25,
  status: 'uploading', metadata_only: true, probe_ok: true, can_materialize: false, has_speech: null,
  transcript: { segments: [], speakers: [] }, chunks: [0], progress: 40, asr_confidence: null,
  thumb_url: null, wave_url: null, ...patch });
const item = patch => ({ id: media.fileIdentity(file()), file: file(), name: 'fixture.mkv', size: 3, lastModified: 1,
  kind: 'video', type: '', duration: null, note: 'keep', trim_start: 0, trim_end: null, status: 'loading', ...patch });
const modes = load(read('../src/lib/productionModes.ts'), { '../../../backend/mode_rules.json': JSON.parse(read('../../backend/mode_rules.json')) });
const upload = (respond = () => { throw Error('unexpected request'); }) => load(uploadSource,
  { './mediaInput': media, './productionModes': modes, './appApi': { createAppRequest: auth => (url, init) => respond(url, init ?? {}, auth) } });
const sig = () => new AbortController().signal;

function decoder(event, duration = 1) {
  let removed = 0, revoked = 0;
  const element = { duration, videoWidth: 64, videoHeight: 48, pause() {}, removeAttribute() { removed++; },
    load() { if (this.src) queueMicrotask(() => { this.src = ''; this[event]?.(); }); } };
  const api = load(mediaSource, {}, { URL: { createObjectURL: () => 'blob:synthetic', revokeObjectURL: () => revoked++ },
    document: { createElement: () => element },
    setTimeout: event === 'timeout' ? fn => { queueMicrotask(fn); return 1; } : setTimeout, clearTimeout });
  return { api, element, receipt: () => ({ removed, revoked }) };
}
for (const event of ['onerror', 'timeout']) test(`typed ${event} allows video only, never image/audio/general errors`, async () => {
  const d = decoder(event);
  await assert.rejects(d.api.probeMedia(file(), 'video'), error => {
    assert.equal(error.code, event === 'onerror' ? 'unsupported' : 'timeout');
    assert.equal(d.api.canServerProbe(error, 'video'), true);
    assert.equal(d.api.canServerProbe(error, 'image'), false);
    assert.equal(d.api.canServerProbe(error, 'audio'), false);
    return true;
  });
  assert.deepEqual(d.receipt(), { removed: 1, revoked: 1 });
  assert.equal(d.api.canServerProbe(Error('unknown'), 'video'), false);
});
for (const value of [NaN, Infinity, 0, -1, 1800.001]) test(`invalid/measured-over-limit duration ${value} never falls back`, async () => {
  const d = decoder('onloadedmetadata', value);
  await assert.rejects(d.api.probeMedia(file(), 'video'), e => e.code === 'invalid' && !d.api.canServerProbe(e, 'video'));
});
test('abort remains abort, releases object URL, never fallback', async () => {
  const d = decoder('none'), c = new AbortController();
  const pending = d.api.probeMedia(file(), 'video', c.signal); c.abort();
  await assert.rejects(pending, e => e.name === 'AbortError' && !d.api.canServerProbe(e, 'video'));
  assert.deepEqual(d.receipt(), { removed: 1, revoked: 1 });
});
test('hard duration ceilings cannot be expanded by public limits', () => {
  const cap = media.mediaLimits({ ...limits, max_video_duration_seconds: 9999, max_total_video_duration_seconds: 99999 });
  assert.equal(cap.maxDuration, 1800); assert.equal(cap.maxTotalDuration, 3600);
});
test('server metadata remains non-ready until actual ready, preserves notes/ranges', () => {
  const u = upload(), input = item({ trim_start: 1, trim_end: 8, note: 'keep' });
  const probed = u.parseUploadSnapshot(snapshot({ sec: 10 }), id, scope);
  const changed = { ...input, ...u.reconcileServerProbe(input, probed, limits) };
  assert.equal(changed.status, 'loading'); assert.equal(changed.duration, 10);
  assert.equal(changed.note, 'keep'); assert.equal(changed.trim_start, 1); assert.equal(changed.trim_end, 8);
  assert.equal(u.uploadUsable(probed), false);
  const ready = u.parseUploadSnapshot(snapshot({ sec: 10, status: 'ready', metadata_only: false, can_materialize: true }), id, scope);
  assert.equal(u.reconcileServerProbe(input, ready, limits).status, 'ready');
  const invalidTrim = { ...input, ...u.reconcileServerProbe(input, { ...ready, sec: 2 }, limits) };
  assert.equal(invalidTrim.trim_end, 8); assert.ok(media.validateTrim(invalidTrim));
});
test('strict task parser rejects missing/zero/nonfinite dimensions fps and false probe', () => {
  const u = upload();
  for (const key of ['width', 'height', 'fps', 'sec']) for (const value of [undefined, null, 0, -1, Infinity, NaN, '1']) {
    assert.throws(() => u.parseUploadSnapshot(snapshot({ [key]: value }), id, scope), `${key}:${value}`);
  }
  assert.throws(() => u.parseUploadSnapshot(snapshot({ status: 'ready', probe_ok: false, metadata_only: false }), id, scope));
  assert.throws(() => u.parseUploadSnapshot(snapshot({ width: 1.2 }), id, scope));
  assert.throws(() => u.reconcileServerProbe(item(), { ...snapshot(), sec: 1801 }, limits));
  assert.throws(() => u.reconcileServerProbe(item(), { ...snapshot(), fps: 121 }, limits));
});
test('bound chunks -> probe only; no complete, start, render or ASR endpoint', async () => {
  let chunks = [], probed = false; const calls = []; let saved;
  const u = upload((url, init, auth) => {
    calls.push({ url, method: init.method }); assert.equal(auth.token, token); assert.equal(auth.taskId, task);
    if (url === `/api/tasks/${task}/files`) return { upload_id: id, file_id: id, token, chunk_size: 8388608, put_url: base + '/chunks/{index}' };
    if (init.method === 'PUT') { chunks = [0]; return {}; }
    if (url === base + '/probe') { assert.equal(init.body, '{}'); probed = true; return snapshot(); }
    assert.equal(url, base);
    return snapshot({ chunks, sec: probed ? 1 : null, width: null, height: null, fps: null,
      probe_ok: false, metadata_only: false });
  });
  const result = await u.uploadFile(file(), sig(), { onSession: s => saved = s, onSnapshot() {} }, undefined,
    { draftTaskId: task, draftTaskToken: token }, true);
  assert.equal(result.metadata_only, true); assert.equal(result.status, 'uploading');
  assert.equal(saved.file_id, media.fileIdentity(file())); assert.equal(saved.sha256, sha);
  assert.equal(calls.filter(c => c.url.endsWith('/probe')).length, 1);
  assert.ok(calls.every(c => !/complete|start|render|pretranscribe/.test(c.url)));
});
test('refresh and repeated resume of metadata-only session are GET-only', async () => {
  const calls = [], u = upload((url, init) => { calls.push(init.method ?? 'GET'); return snapshot(); });
  for (let i = 0; i < 3; i++) {
    await u.pollUploadStatus(id, token, sig(), () => {}, session());
    await u.uploadFile(undefined, sig(), { onSession() { assert.fail(); }, onSnapshot() {} }, session(), scope, true);
  }
  assert.deepEqual(calls, Array(6).fill('GET'));
});
test('task probe rejects foreign paths, response identity and unscoped calls', async () => {
  const u = upload(() => snapshot({ id: 'foreign' }));
  await assert.rejects(u.probeUpload(session(), sig()));
  await assert.rejects(u.probeUpload({ ...session(), draftTaskId: undefined }, sig()));
  assert.throws(() => u.parseUploadSnapshot(snapshot({ thumb_url: '/api/tasks/foreign/files/x/thumb' }), id, scope));
});

// Actual component functions, not a replacement reconciler or upload loop.
const wizardSource = original ? readFileSync(path.join(original, 'CreateWizard.tsx.before'), 'utf8') : read('../src/components/CreateWizard.tsx');
const wizardAst = ts.createSourceFile('CreateWizard.tsx', wizardSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function declaration(name) {
  let found;
  const walk = n => { if (ts.isFunctionDeclaration(n) && n.name?.text === name) { assert.equal(found, undefined); found = n; } ts.forEachChild(n, walk); };
  walk(wizardAst); assert.ok(found, name); return found.getText(wizardAst);
}
test('actual wizard publication checks identity and adopts authoritative metadata without transcript edits', () => {
  const u = upload(), file = item(), filesRef = { current: [file] }, uploadsRef = { current: {} };
  let updates = 0;
  const context = { filesRef, uploadsRef, sessionFor: () => session(), limits, reconcileServerProbe: u.reconcileServerProbe,
    setUploads() {}, setUploadErrors() {}, updateFile(key, patch) { assert.equal(key, file.id); Object.assign(file, patch); updates++; } };
  const publish = vm.runInNewContext(compile(declaration('publishSnapshot') + ';publishSnapshot'), context);
  publish(file.id, u.parseUploadSnapshot(snapshot(), id, scope));
  assert.equal(file.duration, 1); assert.equal(file.status, 'loading'); assert.equal(file.note, 'keep');
  assert.throws(() => publish(file.id, snapshot({ name: 'foreign.mkv' }))); assert.equal(updates, 1);
  publish(file.id, u.parseUploadSnapshot(snapshot({ status: 'ready', metadata_only: false, can_materialize: true }), id, scope));
  assert.equal(file.status, 'ready');
});
test('actual wizard submit issues include restored-file aggregate and unknown duration', () => {
  const start = wizardSource.indexOf('  const localFiles ='), end = wizardSource.indexOf('  const locked =', start);
  assert.ok(start >= 0 && end > start);
  const evaluate = files => vm.runInNewContext(compile(wizardSource.slice(start, end) + ';issues'), {
    files, limits, cap: media.mediaLimits(limits), selectionIssues: media.selectionIssues, validateTrim: media.validateTrim,
    bindingFor: () => ({}), fileUsable: () => true, uploadErrors: {}, mode: 'voiceover', noSpeech: false,
  });
  const ready = duration => item({ file: undefined, status: 'ready', duration, trim_end: duration });
  assert.equal(evaluate([ready(1800), ready(1800)]).length, 0);
  assert.ok(evaluate([ready(1201), ready(1201), ready(1201)]).length);
  assert.ok(evaluate([ready(null)]).length);
});
for (const scenario of ['valid', 'aggregate', 'unknown', 'abort', 'invalid', 'image']) test(`actual wizard queue ${scenario}: batch probes precede all completion`, async () => {
  const u = upload(), controller = new AbortController(), second = item({ id: 'second', name: 'second.mkv', kind: scenario === 'image' ? 'image' : 'video' });
  const first = item({ kind: scenario === 'image' ? 'image' : 'video' });
  const filesRef = { current: [first, second] }, uploadsRef = { current: {} }, calls = [];
  const sessions = new Map(), operations = { current: new Map([[first.id, controller], [second.id, controller]]) };
  const jobs = { current: [first, second].map(f => ({ file: f.file, itemId: f.id, controller, probe: true })) };
  let errors = {};
  const context = {
    transferRunning: { current: false }, jobs, mounted: { current: true }, filesRef, uploadsRef, operations,
    cap: media.mediaLimits(limits), limits, initialDraft: {}, draftAccessRef: { current: scope }, draftController: { current: null },
    canServerProbe: media.canServerProbe, urls: { current: new Set() }, releaseUrl() {}, setNotice() {},
    setHashProgress() {}, setUploadErrors(fn) { errors = fn(errors); }, setDraftAccess() {},
    errorMessage: e => e.message, URL, transferComplete: u.transferComplete,
    updateFile(key, values) { Object.assign(filesRef.current.find(f => f.id === key), values); },
    sessionFor: key => sessions.get(key), rememberSession(s) { sessions.set(s.file_id, s); },
    async probeMedia() {
      if (scenario === 'abort') { controller.abort(); throw new DOMException('Cancelled', 'AbortError'); }
      throw new media.BrowserProbeError(scenario === 'invalid' ? 'invalid' : 'unsupported', 'synthetic browser');
    },
    async uploadFile(file, signal, cb, resume, access, metadataOnly) {
      assert.equal(metadataOnly, true); assert.equal(access, scope);
      const selected = calls.length === 0 ? first : second;
      calls.push('probe');
      const s = { ...session(), file_id: selected.id, upload_id: selected.id === first.id ? id : 'second_upload' };
      cb.onSession(s);
      const unknown = scenario === 'unknown' && selected === second;
      const state = { ...snapshot(), id: s.upload_id, sec: unknown ? null : scenario === 'aggregate' ? 1800 : 1,
        probe_ok: !unknown };
      cb.onSnapshot(state);
      if (scenario === 'aggregate' && selected === second) {
        // Both individually legal; restored peer makes 3601 original seconds.
        filesRef.current.push(item({ id: 'restored', file: undefined, duration: 1 }));
        sessions.set('restored', { ...session(), file_id: 'restored', upload_id: 'restored' });
        uploadsRef.current.restored = snapshot();
      }
      return state;
    },
    publishSnapshot(key, state) {
      uploadsRef.current[state.id] = state;
      if (state.probe_ok) Object.assign(filesRef.current.find(f => f.id === key), u.reconcileServerProbe(filesRef.current.find(f => f.id === key), state, limits));
    },
    async completeUpload(key) { calls.push('complete'); assert.equal(calls.filter(x => x === 'probe').length, 2);
      return { ...snapshot(), id: key, status: 'probing', metadata_only: false }; },
    observeUpload() { calls.push('observe'); },
  };
  const drain = vm.runInNewContext(compile(declaration('drainFileJobs') + ';drainFileJobs'), context);
  await drain();
  if (scenario === 'valid') assert.deepEqual(calls, ['probe', 'probe', 'complete', 'observe', 'complete', 'observe']);
  else assert.equal(calls.includes('complete'), false);
  if (['abort', 'invalid', 'image'].includes(scenario)) assert.equal(calls.length, 0);
  assert.equal(context.transferRunning.current, false);
  assert.equal(first.note, 'keep'); assert.equal(second.note, 'keep');
});
if (process.env.F06_BRIDGE) test('actual main ASGI receipt passes actual frontend parser and reconciler', () => {
  const bridge = JSON.parse(readFileSync(process.env.F06_BRIDGE, 'utf8'));
  const u = upload(), parsed = u.parseUploadSnapshot(bridge.snapshot, bridge.snapshot.id, bridge.scope);
  assert.equal(parsed.sec, 1); assert.equal(parsed.fps, 25);
  assert.equal(u.reconcileServerProbe(item(), parsed, limits).status, 'loading');
});
if (process.env.GM_F06_BROWSER === '1') test('offline native Edge decodes actual FFV1 fixture as typed unsupported, no network', async () => {
  let browser, requests = 0, stage = 0;
  try {
    const { chromium } = createRequire(import.meta.url)('playwright');
    stage = 1;
    browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] });
    const context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
    await context.route('**/*', route => { requests++; return route.abort(); });
    const page = await context.newPage(); await page.setContent('<html><body></body></html>');
    stage = 2;
    await page.addScriptTag({ content: 'var exports={};' + compile(mediaSource) });
    const bytes = [...readFileSync(path.join(process.env.GM_F06_TEMP, 'ffv1.mkv'))];
    stage = 3;
    const result = await page.evaluate(async data => {
      try { await exports.probeMedia(new File([Uint8Array.from(data)], 'fixture.mkv', { type: 'video/x-matroska' }), 'video'); return { accepted: true }; }
      catch (e) { return { accepted: false, code: e.code, fallback: exports.canServerProbe(e, 'video') }; }
    }, bytes);
    stage = 4;
    console.log(JSON.stringify({ f06Decoder: result, requests, browser: browser.version() }));
    assert.deepEqual(result, { accepted: false, code: 'unsupported', fallback: true }); assert.equal(requests, 0);
    await context.close();
  } catch { assert.fail(`F06 offline decoder assertion failed at stage ${stage}; no raw browser artifacts`); }
  finally { await browser?.close(); }
});