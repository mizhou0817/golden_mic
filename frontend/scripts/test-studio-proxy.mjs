import assert from 'node:assert/strict';
import { Blob, File } from 'node:buffer';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

// Node-only contracts. Transpile ACTUAL modules and
// editor statements in memory. All HTTP is an in-memory transport double; no
// subprocess, real fetch, media execution, provider, browser or file writes.
// Seeded React SSR proves render/control boundaries, NOT effects, real layout,
// accessibility audit, FFmpeg output, integrity/authorization or browser seeks.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const apiSource = read('../src/lib/studioApi.ts');
const proxySource = read('../src/components/StudioProxy.tsx');
const studioSource = read('../src/components/Studio.tsx');
const plain = value => JSON.parse(JSON.stringify(value));
const forbidden = label => () => assert.fail(`Unexpected side effect: ${label}`);
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function load(source, fileName, dependencies = {}, bindings = {}, env = {}) {
  const compiled = ts.transpileModule(source, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, sourceMap: false },
    transformers: { before: [context => root => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
        && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
        ? ts.factory.createObjectLiteralExpression(Object.entries(env).map(([key, value]) =>
          ts.factory.createPropertyAssignment(key, ts.factory.createStringLiteral(value))))
        : ts.visitEachChild(node, visit, context);
      return ts.visitNode(root, visit);
    }] },
  });
  assert.equal(compiled.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0,
    `${fileName}: actual module must transpile without syntax diagnostics`);
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, TextEncoder, Headers, Blob, File, URL, AbortController,
    crypto: { randomUUID }, fetch: forbidden('real network'), ...bindings,
    require(name) { assert.ok(Object.hasOwn(dependencies, name), `Unlisted dependency: ${name}`); return dependencies[name]; },
  });
  new vm.Script(compiled.outputText, { filename: fileName }).runInContext(context, { timeout: 1000 });
  return module.exports;
}
const api = load(apiSource, 'studioApi.ts');
const proxy = load(proxySource, 'StudioProxy.tsx', { react: React, 'react/jsx-runtime': jsxRuntime, '../lib/studioApi': api });
const timeline = load(read('../src/lib/timelineEditing.ts'), 'timelineEditing.ts');
const compositions = load(read('../src/lib/studioCompositions.ts'), 'studioCompositions.ts', { './studioApi': api, './timelineEditing': timeline });
const sequences = load(read('../src/lib/studioSequences.ts'), 'studioSequences.ts', { './studioApi': api });
const assets = load(read('../src/components/StudioAssets.tsx'), 'StudioAssets.tsx', { react: React, 'react/jsx-runtime': jsxRuntime, '../lib/studioApi': api });
const studioDependencies = { react: React, 'react/jsx-runtime': jsxRuntime, '../lib/studioApi': api,
  '../lib/timelineEditing': timeline, '../lib/studioCompositions': compositions, '../lib/studioSequences': sequences, './StudioAssets': assets,
  './StudioProxy': proxy };
const TASK = 'unit-task', ROOT = `/api/tasks/${TASK}/studio`;
const SOURCE = `upload_${'a'.repeat(20)}`, OTHER = `upload_${'b'.repeat(20)}`, NORM = `norm_${'c'.repeat(20)}`;
const JOB = 'a'.repeat(32), RENDER = 'b'.repeat(32), EXPORT = 'd'.repeat(32), CACHE = 'c'.repeat(64);
const OPTIONS = { ...plain(api.DEFAULT_EXPORT), resolution: 360, video_bitrate_kbps: 1000, audio_bitrate_kbps: 128 };
// Keep overrides raw for rejection cases; positive catalogs use the actual response validator.
const source = (patch = {}) => ({ id: SOURCE, name: 'original.mp4', url: 'https://untrusted.invalid/not-a-source-route',
  bytes: 4096, burned_subtitles: false, duration: 8.012345, has_video: true, has_audio: true, is_image: false, ...patch });
const catalog = (rows = [source()]) => api.normalizeSourceCatalog({ sources: rows, shots: [], report: { rows: [], quality: { blockers: 1 } } });
const measured = () => ({ file: 'output.mp4', bytes: 2048, duration: 8.012345, applied: { ...OPTIONS },
  probe: { streams: [{ codec_type: 'video', codec_name: 'h264', width: 640, height: 360, avg_frame_rate: '30/1' },
    { codec_type: 'audio', codec_name: 'aac', sample_rate: '48000', channels: 2 }], format: { duration: '8.033333', size: '2048' } } });
function proxyJob(patch = {}) {
  const state = patch.state ?? 'queued', id = patch.id ?? JOB;
  return { id, kind: 'proxy', source_id: SOURCE, cache_key: CACHE, revision: 7, pipeline_revision: 2,
    state, created_at: 1000, options: { ...OPTIONS }, output_id: state === 'succeeded' ? id : null, error: null,
    qc: 'raw source preview only; not an edited or pipeline-approved export', disclosure: false,
    ...(state === 'succeeded' ? { finished_at: 1001, result: measured() } : {}), ...patch };
}
function outputJob(patch = {}) {
  const state = patch.state ?? 'succeeded', id = patch.id ?? RENDER;
  return { id, kind: 'render', revision: 7, pipeline_revision: 2, state, created_at: 999,
    options: { ...OPTIONS }, output_id: state === 'succeeded' ? id : null, error: null,
    qc: 'unreviewed derivative; not pipeline-approved', disclosure: false,
    ...(state === 'succeeded' ? { finished_at: 1001, result: measured() } : {}), ...patch };
}
function project() {
  return api.normalizeProject({ schema_version: 1, name: 'Unsaved editing remains original', tracks: [{ id: 'track', type: 'video', name: 'V1', clips: [
    { id: 'clip', source_id: SOURCE, trim: 0.123456789, start: 0.017, duration: 3, speed: 1.25, brightness: 0.4 },
  ] }], workspace: { ...plain(api.makeWorkspace()), source_marks: [{ source_id: SOURCE, in_point: 0.123456789, out_point: 4.987654321 }] } });
}
const envelope = (revision = 7, p = project()) => ({ revision, project: p, can_undo: true, can_redo: false });
const capabilities = (available = true) => ({ schema_version: 1,
  tools: [{ id: 'proxy', label: '代理剪辑', group: 'timeline', available, classification: available ? 'partial' : 'unsupported', reason: 'Bounded private RAW source' },
    { id: 'inout', label: '源入出点', group: 'timeline', available: true, classification: 'metadata', reason: 'Original clock' }],
  tool_count: 2, render_available: true, quality: 'unreviewed', unsupported: [],
  limits: { tracks: 8, clips: 64, duration_seconds: 120, output_bytes: 268435456, task_output_bytes: 2147483648,
    jobs_per_task: 20, history_mutations: 100, threads: 2, global_jobs: 2, max_resolution: 1080, request_bytes: 262144,
    proxy_duration_seconds: 120, proxy_width: 640, proxy_height: 360, proxy_fps: 30 },
  export_schema: {}, project_schema: {}, action_schema: {}, proxy_request_schema: {} });
const ready = () => ({ status: 'ready', message: '' });
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));

const editorRoot = ts.createSourceFile('Studio.tsx', studioSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
assert.equal(editorRoot.parseDiagnostics.length, 0, 'Actual Studio source must parse before AST extraction or printing.');
const editorNode = editorRoot.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'StudioEditor');
assert.ok(editorNode?.body);
const printer = ts.createPrinter();
// Only export visibility is added. No copied mutex/save/preview algorithms.
function editorStatements(names, bindings = {}, statements = editorNode.body.statements) {
  const text = names.map(name => {
    const matches = statements.filter(node => ts.isFunctionDeclaration(node) ? node.name?.text === name
      : ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration => ts.isIdentifier(declaration.name) && declaration.name.text === name));
    assert.equal(matches.length, 1, `Exactly one actual declaration: ${name}`);
    return printer.printNode(ts.EmitHint.Unspecified, matches[0], editorRoot);
  }).join('\n');
  return load(`${text}\nexport { ${names.join(', ')} };`, 'actual-editor-statements.ts', {}, bindings);
}
function editorPollingEffect(bindings) {
  const nodes = editorNode.body.statements.filter(node => ts.isExpressionStatement(node) && ts.isCallExpression(node.expression)
    && node.expression.expression.getText(editorRoot) === 'useEffect' && node.getText(editorRoot).includes('pollingIds.split'));
  assert.equal(nodes.length, 1);
  const callback = printer.printNode(ts.EmitHint.Expression, nodes[0].expression.arguments[0], editorRoot);
  return load(`export const effect = ${callback};`, 'actual-editor-poll.ts', {}, bindings).effect;
}

test('fixed profile is bounded and capability opt-in does not reinterpret older fixtures', () => {
  assert.equal(Object.isFrozen(api.STUDIO_PROXY_PROFILE), true);
  assert.equal(api.STUDIO_PROXY_PROFILE.max_source_seconds, 120); assert.equal(api.STUDIO_MAX_JOBS, 20);
  assert.equal(api.STUDIO_PROXY_PROFILE.width, 640); assert.equal(api.STUDIO_PROXY_PROFILE.height, 360);
  assert.equal(api.STUDIO_PROXY_PROFILE.fps, 30);
  for (const caps of [null, { tools: [] }, capabilities(false), { tools: [{ id: 'render', available: true }] },
    { tools: [{ id: 'proxy', available: 'true' }] }]) assert.equal(api.studioProxiesAvailable(caps), false);
  assert.equal(api.studioProxiesAvailable(capabilities()), true);
});

test('RAW means unchanged original catalog video, not a new proxy source, recording or arbitrary URL', () => {
  for (const id of [SOURCE, NORM, 'final', 'video_only']) {
    assert.equal(api.isRawProxySourceId(id), true); assert.equal(proxy.proxySourceProblem(source({ id })), null);
  }
  for (const id of ['narration', 'recording_1', `image_${'a'.repeat(24)}`, 'lut_1', `proxy_${JOB}`, JOB,
    '../source', '/sources/final', 'https://untrusted.invalid/video', `upload_${'a'.repeat(19)}`, null, { toString: () => SOURCE }]) {
    assert.equal(api.isRawProxySourceId(id), false); assert.ok(proxy.proxySourceProblem(source({ id })));
  }
  assert.ok(proxy.proxySourceProblem(source({ has_video: false })));
  assert.ok(proxy.proxySourceProblem(source({ is_image: true })));
  for (const name of ['recording.webm.wav', 'narration.m4a', 'sound.MP3', 'sound.flac']) assert.ok(proxy.proxySourceProblem(source({ name })));
});

test('original duration bound is checked before save; unknown streams/EOF are left to backend probing, never guessed', () => {
  for (const duration of [0, -1, 120.000001, Infinity, NaN]) {
    assert.ok(proxy.proxySourceProblem(source({ duration })));
    assert.ok(proxy.proxySourceProblem(source({ duration: undefined }), duration));
  }
  assert.equal(proxy.proxySourceProblem(source({ duration: 120 })), null);
  assert.ok(proxy.proxySourceProblem(source({ duration: 8 }), 121), 'A conflicting original metadata reading must not be ignored.');
  assert.equal(proxy.proxySourceProblem(source({ duration: undefined, has_video: undefined })), null);
  assert.ok(proxy.proxySourceProblem(undefined));
});

test('proxy POST has exactly expected_revision/source_id and protected same-origin transport; no editing parameters', async () => {
  const calls = [], signal = new AbortController().signal;
  const client = api.createStudioApi(TASK, async (path, init) => { calls.push({ path, init }); return proxyJob(); }, 'synthetic-token');
  const result = await client.createProxy(7, SOURCE, signal);
  assert.equal(calls.length, 1); assert.equal(calls[0].path, `${ROOT}/proxies`);
  const init = calls[0].init, headers = new Headers(init.headers);
  assert.equal(init.method, 'POST'); assert.equal(typeof init.body, 'string');
  assert.deepEqual(JSON.parse(init.body), { expected_revision: 7, source_id: SOURCE });
  assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store');
  assert.equal(init.mode, 'same-origin'); assert.equal(init.redirect, 'error'); assert.equal(init.signal, signal);
  assert.equal(headers.get('Content-Type'), 'application/json'); assert.equal(headers.get('X-Task-Token'), 'synthetic-token');
  assert.equal(headers.has('X-Classroom-CSRF'), false, 'Host transport owns CSRF, not this wrapper.');
  assert.equal(result.kind, 'proxy'); assert.equal(result.state, 'queued'); assert.equal(result.output_id, null);
});

test('cached 200 receipt is the SAME historical job; never rewrite its captured revision to the current project', async () => {
  let calls = 0;
  const value = proxyJob({ state: 'succeeded', cached: true, revision: 3 });
  const client = api.createStudioApi(TASK, async () => { calls++; return value; });
  const result = await client.createProxy(41, SOURCE);
  assert.equal(calls, 1); assert.equal(result.id, value.id); assert.equal(result.revision, 3); assert.equal(result.cached, true);
  assert.equal(result.cache_key, CACHE); assert.equal(Object.hasOwn(result, 'project'), false);
});

test('invalid revisions/source identifiers fail before transport and submission endpoints cannot be injected', async () => {
  let calls = 0; const client = api.createStudioApi(TASK, async () => { calls++; return proxyJob(); });
  for (const revision of [-1, 1.5, NaN, Infinity, '7', null, Number.MAX_SAFE_INTEGER + 1]) await assert.rejects(client.createProxy(revision, SOURCE));
  for (const id of ['../x', 'narration', JOB, `proxy_${JOB}`, 'https://host.invalid/video', null]) await assert.rejects(client.createProxy(7, id));
  await assert.rejects(client.submit('proxy', 7, OPTIONS));
  await assert.rejects(client.submit('../proxies', 7, OPTIONS));
  assert.equal(calls, 0);
});

test('mismatched proxy receipts fail without retry, fallback render/export or guessed job identity', async () => {
  const bad = [proxyJob({ source_id: OTHER }), proxyJob({ revision: 8 }), proxyJob({ revision: 6 }),
    proxyJob({ state: 'succeeded', revision: 8, cached: true }), proxyJob({ cached: true }),
    proxyJob({ cached: 'true' }), proxyJob({ cached: null }), outputJob()];
  for (const value of bad) {
    let calls = 0; const client = api.createStudioApi(TASK, async () => { calls++; return value; });
    await assert.rejects(client.createProxy(7, SOURCE)); assert.equal(calls, 1);
  }
});

test('unknown/mistyped states and kinds never become success, progress or a retryable synthetic job', async () => {
  const bad = [{ state: 'done' }, { state: 'encoding' }, { state: ['succeeded'] }, { state: null },
    { state: { toString: () => 'succeeded' } }, { kind: 'preview' }, { kind: ['proxy'] }, { kind: null },
    { id: { toString: () => JOB } }, { revision: '7' }, { revision: -1 }, { pipeline_revision: undefined },
    { pipeline_revision: NaN }, { created_at: Infinity }, { created_at: '1000' }, { finished_at: NaN },
    { source_id: 'narration' }, { source_id: '../x' }, { cache_key: '../cache' }, { cache_key: 'a'.repeat(63) },
    { error: {} }, { qc: null }, { disclosure: 'false' }, { output_id: 'https://unsafe.invalid/file' }];
  for (const patch of bad) {
    let calls = 0; const client = api.createStudioApi(TASK, async () => { calls++; return proxyJob(patch); });
    await assert.rejects(client.job(JOB)); assert.equal(calls, 1);
  }
});

test('fixed profile mismatches and malformed result fields are rejected, not repaired with defaults', async () => {
  for (const patch of [{ format: 'gif' }, { resolution: 720 }, { fps: 24 }, { aspect: '1:1' }, { subtitles: 'none' },
    { video_bitrate_kbps: 4000 }, { audio_bitrate_kbps: 192 }, { frame_time: 0 }, { fps: '30' }, { filter: 'arbitrary-effect' }]) {
    const client = api.createStudioApi(TASK, async () => proxyJob({ options: { ...OPTIONS, ...patch } }));
    await assert.rejects(client.job(JOB));
  }
  for (const patch of [{ bytes: '2048' }, { bytes: 0 }, { duration: NaN }, { duration: -1 }, { duration: 120.01 },
    { applied: null }, { applied: { ...OPTIONS, fps: 60 } }, { file: null }]) {
    const client = api.createStudioApi(TASK, async () => proxyJob({ state: 'succeeded', result: { ...measured(), ...patch } }));
    await assert.rejects(client.job(JOB));
  }
  const result = await api.createStudioApi(TASK, async () => proxyJob({ options: { ...OPTIONS, frame_time: undefined } })).job(JOB);
  assert.equal(result.options.frame_time, null, 'Only the already documented omitted legacy frame_time default is hydrated.');
});

test('all six real job states work for mixed jobs; proxy output identity is not export authority', async () => {
  for (const state of ['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']) {
    for (const value of [proxyJob({ state }), outputJob({ state }), outputJob({ state, kind: 'export', id: EXPORT })]) {
      const client = api.createStudioApi(TASK, async () => value);
      const result = await client.job(value.id);
      assert.equal(result.state, state); assert.equal(result.kind, value.kind);
      assert.equal(api.isActiveJob(result), ['queued', 'running'].includes(state));
    }
  }
  const client = api.createStudioApi(TASK, async () => outputJob({ source_id: SOURCE, cache_key: CACHE }));
  await assert.rejects(client.job(RENDER));
  for (const patch of [{ output_id: null }, { output_id: RENDER }, { result: undefined }]) {
    await assert.rejects(api.createStudioApi(TASK, async () => proxyJob({ state: 'succeeded', ...patch })).job(JOB));
  }
  const wrong = api.createStudioApi(TASK, async () => proxyJob());
  await assert.rejects(wrong.submit('render', 7, OPTIONS));
  await assert.rejects(wrong.submit('export', 7, OPTIONS));
});

test('bounded GET returns persisted proxies only and validates the complete list rather than skipping bad rows', async () => {
  const calls = [], signal = new AbortController().signal;
  const rows = Array.from({ length: 20 }, (_, i) => proxyJob({ id: i.toString(16).padStart(32, '0') }));
  const client = api.createStudioApi(TASK, async (path, init) => { calls.push({ path, init }); return { proxies: rows }; });
  assert.equal((await client.proxies(signal)).length, 20);
  assert.equal(calls.length, 1); assert.equal(calls[0].path, `${ROOT}/proxies`); assert.equal(calls[0].init.method, 'GET');
  assert.equal(calls[0].init.body, undefined); assert.equal(calls[0].init.signal, signal);
  assert.equal(calls[0].init.credentials, 'include'); assert.equal(calls[0].init.mode, 'same-origin'); assert.equal(calls[0].init.redirect, 'error');
  assert.deepEqual(plain(api.normalizeStudioProxies({ proxies: [] })), []);
  for (const value of [null, [], { jobs: rows }, { proxies: 'none' }, { proxies: rows, invented: true },
    { proxies: [...rows, proxyJob()] }, { proxies: [proxyJob(), proxyJob()] }, { proxies: [outputJob()] },
    { proxies: [proxyJob(), proxyJob({ id: RENDER, state: 'unknown' })] }]) assert.throws(() => api.normalizeStudioProxies(value));
});

test('job polling and cancellation use common known routes once and reject mismatched receipt IDs', async () => {
  const calls = [];
  const client = api.createStudioApi(TASK, async (path, init) => {
    calls.push({ path, init }); return proxyJob({ state: init.method === 'DELETE' ? 'cancelled' : 'running' });
  });
  assert.equal((await client.job(JOB)).state, 'running'); assert.equal((await client.cancel(JOB)).state, 'cancelled');
  assert.deepEqual(calls.map(row => [row.path, row.init.method ?? 'GET']), [[`${ROOT}/jobs/${JOB}`, 'GET'], [`${ROOT}/jobs/${JOB}`, 'DELETE']]);
  assert.equal(calls[1].init.body, undefined); assert.equal(calls[1].init.credentials, 'include');
  const wrong = api.createStudioApi(TASK, async () => proxyJob({ id: RENDER }));
  await assert.rejects(wrong.job(JOB)); await assert.rejects(wrong.cancel(JOB));
  for (const id of ['../outside', 'https://outside.invalid', { toString: () => JOB }]) {
    await assert.rejects(client.job(id)); await assert.rejects(client.cancel(id));
  }
  assert.equal(calls.length, 2);
});

test('401/403/409/422/429/500 and uncertain transport errors never replay a proxy POST', async () => {
  for (const status of [401, 403, 409, 422, 429, 500, undefined]) {
    const failure = Object.assign(new Error('Synthetic failure'), { status }); let calls = 0;
    const client = api.createStudioApi(TASK, async () => { calls++; throw failure; });
    await assert.rejects(client.createProxy(7, SOURCE), error => error === failure); assert.equal(calls, 1);
  }
});

test('proxy media URLs are always known same-origin paths, never Vite/catalog/cache/file URLs or export endpoints', () => {
  const withBase = load(apiSource, 'studioApi.ts', {}, {}, { VITE_API_BASE_URL: 'https://external.invalid' });
  const client = withBase.createStudioApi(TASK, forbidden('URL construction must not fetch'), 'synthetic token/only');
  assert.equal(client.proxyUrl(JOB), `${ROOT}/proxies/${JOB}/media?token=synthetic%20token%2Fonly`);
  const local = withBase.createStudioApi(TASK, forbidden('local URL construction must not fetch'));
  assert.equal(local.proxyUrl(JOB), `${ROOT}/proxies/${JOB}/media`, 'A local workspace URL has no invented task token.');
  assert.equal(client.sourceUrl(SOURCE), `${ROOT}/sources/${SOURCE}?token=synthetic%20token%2Fonly`, 'The explicit original quality choice is same-origin too.');
  assert.equal(client.outputUrl(JOB), `${ROOT}/outputs/${JOB}?token=synthetic%20token%2Fonly`, 'Exports must not send a task token to an external Vite base URL.');
  assert.equal(local.outputUrl(JOB), `${ROOT}/outputs/${JOB}`, 'Local output URLs have no account mode or invented capability.');
  for (const id of [SOURCE, CACHE, '../outside', '//host.invalid', 'https://host.invalid/file', 'a'.repeat(31), { toString: () => JOB }]) assert.throws(() => client.proxyUrl(id));
});

const TASK_TOKEN = 't'.repeat(48);
function appTransport(respond, token = TASK_TOKEN) {
  const host = load(read('../src/lib/appApi.ts'), 'appApi.ts', {}, { FormData, Response, DOMException, setTimeout, clearTimeout,
    location: { origin: 'https://node-only.invalid' },
    fetch: async (path, init) => {
      const result = await respond(path, init);
      return new Response(JSON.stringify(result.payload), { status: result.status ?? 200,
        headers: { 'Content-Type': 'application/json', ...result.headers } });
    } });
  return host.createAppRequest({ taskId: TASK, token, signal: new AbortController().signal });
}
const session = () => ({ access_mode: 'anonymous', csrf_token: 'c'.repeat(48),
  expires_at: new Date(Date.now() + 600_000).toISOString() });
test('actual appApi owns anonymous CSRF and selected-capability authorization for proxy writes', async () => {
  const calls = [];
  const request = appTransport((path, init) => {
    calls.push({ path, method: init.method ?? 'GET' });
    if (path === '/api/session') return { payload: session() };
    assert.equal(path, `${ROOT}/proxies`);
    assert.deepEqual(JSON.parse(init.body), { expected_revision: 7, source_id: SOURCE });
    const headers = new Headers(init.headers);
    assert.equal(headers.get('X-CSRF-Token'), session().csrf_token); assert.equal(headers.get('X-Task-Token'), TASK_TOKEN);
    assert.equal(headers.has('X-Classroom-CSRF'), false); assert.equal(headers.has('Authorization'), false);
    assert.equal(headers.get('Content-Type'), 'application/json'); assert.equal(headers.get('Accept'), 'application/json');
    assert.equal(init.credentials, 'include'); assert.equal(init.redirect, 'error'); assert.equal(init.mode, 'same-origin');
    assert.equal(init.cache, 'no-store');
    return { payload: proxyJob(), status: 202 };
  });
  const client = api.createStudioApi(TASK, request, 'injected-wrapper-token'.padEnd(48, '_'));
  assert.equal((await client.createProxy(7, SOURCE)).kind, 'proxy');
  assert.deepEqual(calls.map(row => row.method), ['GET', 'POST']);
});

test('actual appApi never replays a rejected proxy POST or eagerly bootstraps after its 403', async () => {
  const calls = [];
  const request = appTransport((path, init) => {
    calls.push({ path, method: init.method ?? 'GET' });
    if (path === '/api/session') return { payload: session() };
    return { status: 403, payload: { detail: 'Invalid anonymous CSRF' }, headers: { 'X-Anonymous-Session': 'required' } };
  });
  const client = api.createStudioApi(TASK, request);
  await assert.rejects(client.createProxy(7, SOURCE), error => error.status === 403);
  assert.deepEqual(calls.map(row => row.method), ['GET', 'POST']);
  assert.equal(calls.filter(row => row.path === `${ROOT}/proxies`).length, 1);
});

test('coordinator waits for actual save revision rather than predicting +1; original project/timing remain authoritative', async () => {
  const hold = deferred(), p = project(), before = JSON.stringify(p), calls = [];
  const client = api.createStudioApi(TASK, async (path, init) => {
    calls.push({ path, body: JSON.parse(init.body) });
    if (path === `${ROOT}/project`) { await hold.promise; return envelope(41, p); }
    assert.equal(path, `${ROOT}/proxies`); assert.equal(JSON.parse(init.body).expected_revision, 41);
    return proxyJob({ revision: 41 });
  });
  const pending = proxy.createStudioProxy({ api: client, source: source(), originalDuration: 8.012345,
    onDirtySave: () => client.save(7, p), isCurrent: () => true });
  assert.equal(calls.length, 1); assert.equal(calls[0].body.expected_revision, 7);
  hold.resolve(); const result = await pending;
  assert.equal(result.revision, 41); assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].body, { expected_revision: 41, source_id: SOURCE });
  assert.equal(JSON.stringify(p), before); assert.equal(p.tracks[0].clips[0].source_id, SOURCE);
  assert.equal(p.tracks[0].clips[0].trim, 0.123456789); assert.equal(p.workspace.source_marks[0].out_point, 4.987654321);
});

test('coordinator preflights before saving and save failure never starts encoding', async () => {
  let saves = 0, posts = 0; const conflict = new Error('Synthetic revision conflict');
  const args = { api: { createProxy: async () => { posts++; return proxyJob(); } }, isCurrent: () => true,
    onDirtySave: async () => { saves++; throw conflict; } };
  await assert.rejects(proxy.createStudioProxy({ ...args, source: source({ duration: 121 }) }));
  assert.equal(saves, 0); assert.equal(posts, 0);
  await assert.rejects(proxy.createStudioProxy({ ...args, source: source() }), error => error === conflict);
  assert.equal(saves, 1); assert.equal(posts, 0);
});

test('coordinator fences task/editor changes before save, after save and after POST without resubmission', async () => {
  for (const phase of ['before', 'save', 'post']) {
    let current = phase !== 'before', saves = 0, posts = 0;
    await assert.rejects(proxy.createStudioProxy({ source: source(), isCurrent: () => current,
      api: { createProxy: async () => { posts++; if (phase === 'post') current = false; return proxyJob(); } },
      onDirtySave: async () => { saves++; if (phase === 'save') current = false; return { revision: 7 }; } }));
    assert.equal(saves, phase === 'before' ? 0 : 1); assert.equal(posts, phase === 'post' ? 1 : 0);
  }
});

test('common upsert keeps proxy identity, bounded mixed history and terminal cancellation under late polling', () => {
  let jobs = [outputJob(), outputJob({ id: EXPORT, kind: 'export' })];
  jobs = api.upsertStudioJob(jobs, proxyJob({ state: 'running' }));
  assert.equal(jobs.length, 3); assert.equal(jobs[0].kind, 'proxy');
  jobs = api.upsertStudioJob(jobs, proxyJob({ state: 'queued' })); assert.equal(jobs[0].state, 'running');
  jobs = api.upsertStudioJob(jobs, proxyJob({ state: 'cancelled' }));
  for (const state of ['queued', 'running', 'succeeded']) {
    jobs = api.upsertStudioJob(jobs, proxyJob({ state })); assert.equal(jobs[0].state, 'cancelled');
  }
  for (const patch of [{ source_id: OTHER }, { cache_key: 'e'.repeat(64) }, { kind: 'render' }, { revision: 8 }, { pipeline_revision: 3 }]) {
    assert.throws(() => api.upsertStudioJob(jobs, proxyJob(patch)));
  }
  const many = Array.from({ length: 20 }, (_, index) => outputJob({ id: index.toString(16).padStart(32, '0'), created_at: index }));
  assert.equal(api.upsertStudioJob(many, proxyJob()).length, 20);
  const invalidated = api.upsertStudioJob([proxyJob({ state: 'succeeded' })], proxyJob({ state: 'failed', error: 'source changed' }));
  assert.equal(invalidated[0].state, 'failed');
  assert.equal(api.upsertStudioJob(invalidated, proxyJob({ state: 'succeeded' }))[0].state, 'failed');
});

function editorHarness({ respond, dirty = false, initialJobs = [], sourceValue = source(), available = true, saveRevision = 41 } = {}) {
  const calls = [], errors = [], notices = [], receipts = [], p = project(), state = ready();
  const mounted = { current: true }, mutex = { current: false }, jobsRef = { current: initialJobs }, proxyReadSequence = { current: 0 };
  const client = api.createStudioApi(TASK, async (path, init) => {
    calls.push({ path, method: init.method ?? 'GET', init });
    if (respond) return respond(path, init);
    if (path === `${ROOT}/project`) return envelope(saveRevision, p);
    if (path === `${ROOT}/proxies` && init.method === 'POST') return proxyJob({ revision: dirty ? saveRevision : 7 });
    if (path === `${ROOT}/proxies`) return { proxies: [] };
    if (path === `${ROOT}/audit`) return { records: [] };
    throw new Error(`Unexpected in-memory route: ${path}`);
  });
  const bindings = { ...api, ...proxy, api: client, caps: capabilities(available), project: p, saved: envelope(7, p), dirty, warnings: [],
    durations: { [SOURCE]: 8.012345 }, source: sourceValue, running: initialJobs.some(api.isActiveJob), proxyState: state,
    mounted, mutex, jobsRef, proxyReadSequence, setJobs() {}, setBusy() {}, setError() {}, setAudit() {},
    setProxyState: next => Object.assign(state, typeof next === 'function' ? next(state) : next),
    setPreview: forbidden('implicit preview'), setCatalog: forbidden('proxy catalog source insertion'),
    setOptions: forbidden('proxy changes export options'), outputUrl: forbidden('proxy output URL'),
    setNotice: message => notices.push(message), reportError: error => errors.push(error), remember: value => receipts.push(value),
    STATE_NAMES: { queued: '排队中', running: '编码中', succeeded: '已完成', failed: '失败', cancelled: '已取消', interrupted: '服务重启中断' } };
  const editor = editorStatements(['operation', 'saveDraft', 'upsertJob', 'refreshAudit', 'refreshProxyJobs', 'startProxy', 'cancelJob'], bindings);
  return { editor, calls, errors, notices, receipts, project: p, state, mounted, mutex, jobsRef, client };
}

test('actual editor uses its common mutex and saveDraft; proxy receipt never installs a project or selects media', async () => {
  const h = editorHarness({ dirty: true }), before = JSON.stringify(h.project);
  assert.equal(await h.editor.startProxy(), true);
  assert.deepEqual(h.calls.map(row => [row.method, row.path]), [['POST', `${ROOT}/project`], ['POST', `${ROOT}/proxies`], ['GET', `${ROOT}/audit`]]);
  assert.equal(JSON.parse(h.calls[1].init.body).expected_revision, 41);
  assert.equal(h.receipts.length, 1); assert.equal(h.receipts[0].revision, 41, 'Only the real draft save installs a project.');
  assert.equal(h.jobsRef.current[0].kind, 'proxy'); assert.equal(h.jobsRef.current[0].source_id, SOURCE);
  assert.equal(JSON.stringify(h.project), before); assert.equal(h.errors.length, 0); assert.equal(h.mutex.current, false);
});

test('actual shared mutex excludes overlapping proxy and other editor operations, then releases normally', async () => {
  const hold = deferred();
  const h = editorHarness({ respond: async (path, init) => {
    if (path === `${ROOT}/proxies` && init.method === 'POST') { await hold.promise; return proxyJob(); }
    if (path === `${ROOT}/audit`) return { records: [] };
    throw new Error('No other requests expected');
  } });
  const first = h.editor.startProxy();
  assert.equal(h.mutex.current, true); assert.equal(await h.editor.startProxy(), false);
  assert.equal(await h.editor.operation('other write', forbidden('mutex admitted another mutation')), false);
  hold.resolve(); assert.equal(await first, true); assert.equal(h.mutex.current, false);
  assert.equal(h.calls.filter(row => row.method === 'POST').length, 1);
});

test('actual cached reuse leaves project revision, original trims and export options untouched', async () => {
  const h = editorHarness({ respond: (path, init) => path === `${ROOT}/audit` ? { records: [] }
    : init.method === 'POST' ? proxyJob({ state: 'succeeded', cached: true, revision: 3 }) : { proxies: [] } });
  const before = JSON.stringify(h.project);
  assert.equal(await h.editor.startProxy(), true); assert.equal(h.receipts.length, 0);
  assert.equal(h.jobsRef.current[0].revision, 3); assert.equal(h.jobsRef.current[0].cached, true);
  assert.equal(h.calls.filter(row => row.method === 'POST').length, 1); assert.equal(JSON.stringify(h.project), before);
  assert.ok(h.notices.some(message => message.includes('复用')));
});

test('actual draft-save conflict preserves local editing and does not POST a proxy', async () => {
  const h = editorHarness({ dirty: true, respond: (path, init) => {
    if (path === `${ROOT}/project` && init.method === 'POST') throw new Error('Synthetic revision conflict');
    assert.equal(path, `${ROOT}/proxies`); assert.equal(init.method, 'GET'); return { proxies: [] };
  } });
  const before = JSON.stringify(h.project);
  assert.equal(await h.editor.startProxy(), false); assert.equal(h.receipts.length, 0); assert.equal(h.jobsRef.current.length, 0);
  assert.equal(h.calls.filter(row => row.method === 'POST' && row.path === `${ROOT}/proxies`).length, 0);
  assert.equal(JSON.stringify(h.project), before); assert.equal(h.mutex.current, false);
});

test('actual proxy admission consults live common jobs, including a render admitted after its event closure', async () => {
  const h = editorHarness();
  h.jobsRef.current = [outputJob({ state: 'running' })];
  assert.equal(await h.editor.startProxy(), false); assert.equal(h.calls.length, 0); assert.equal(h.mutex.current, false);
});

test('unknown/rejected create response triggers one GET reconciliation, preserves real jobs and requires manual refresh', async () => {
  for (const failure of ['unknown', 'network']) {
    const h = editorHarness({ respond: (path, init) => {
      assert.equal(path, `${ROOT}/proxies`);
      if (init.method === 'POST') {
        if (failure === 'network') throw new Error('Uncertain synthetic connection');
        return proxyJob({ state: 'done' });
      }
      return { proxies: [proxyJob({ state: 'running' })] };
    } });
    const before = JSON.stringify(h.project);
    assert.equal(await h.editor.startProxy(), false);
    assert.deepEqual(h.calls.map(row => row.method), ['POST', 'GET']);
    assert.equal(h.jobsRef.current[0].state, 'running'); assert.equal(h.state.status, 'error');
    assert.match(h.state.message, /刷新代理状态/); assert.equal(h.receipts.length, 0); assert.equal(JSON.stringify(h.project), before);
    assert.equal(await h.editor.startProxy(), false, 'An unresolved response cannot silently start another proxy.');
    assert.equal(h.calls.length, 2);
    await h.editor.refreshProxyJobs(); assert.equal(h.state.status, 'ready');
    assert.deepEqual(h.calls.map(row => row.method), ['POST', 'GET', 'GET']);
  }
});

test('unknown create plus failed reconciliation leaves no invented job, source or success state', async () => {
  const h = editorHarness({ respond: (_path, init) => {
    if (init.method === 'POST') return { state: 'unknown' };
    throw new Error('Synthetic read failure');
  } });
  assert.equal(await h.editor.startProxy(), false);
  assert.deepEqual(h.calls.map(row => row.method), ['POST', 'GET']); assert.equal(h.jobsRef.current.length, 0);
  assert.equal(h.state.status, 'error'); assert.equal(h.errors.length, 1); assert.equal(h.mutex.current, false);
});

test('persistent reopen reads proxy jobs without audit dependence or automatic POST', async () => {
  const respond = () => ({ proxies: [proxyJob({ state: 'succeeded' })] });
  for (let reopen = 0; reopen < 2; reopen++) {
    const h = editorHarness({ respond, initialJobs: [outputJob()] });
    await h.editor.refreshProxyJobs();
    assert.equal(h.state.status, 'ready'); assert.equal(h.jobsRef.current.length, 2);
    assert.equal(h.jobsRef.current.find(job => job.id === JOB).kind, 'proxy');
    assert.deepEqual(h.calls.map(row => [row.method, row.path]), [['GET', `${ROOT}/proxies`]]);
  }
  const old = editorHarness({ available: false });
  await assert.rejects(old.editor.refreshProxyJobs()); assert.equal(old.calls.length, 0);
});

test('read generations and abort ignore late proxy snapshots without rewriting another mounted state', async () => {
  const first = deferred(), second = deferred(); let reads = 0;
  const h = editorHarness({ respond: () => (++reads === 1 ? first.promise : second.promise) });
  const old = h.editor.refreshProxyJobs(), latest = h.editor.refreshProxyJobs();
  second.resolve({ proxies: [proxyJob({ state: 'cancelled' })] }); await latest;
  first.resolve({ proxies: [proxyJob({ state: 'running' })] }); await old;
  assert.equal(h.jobsRef.current[0].state, 'cancelled'); assert.equal(h.state.status, 'ready');
  const hold = deferred(), aborted = editorHarness({ respond: () => hold.promise }), controller = new AbortController();
  const pending = aborted.editor.refreshProxyJobs(capabilities(), controller.signal);
  controller.abort(); hold.resolve({ proxies: [proxyJob()] });
  assert.equal(await pending, null); assert.equal(aborted.jobsRef.current.length, 0);
});

test('completed proxy list refresh removes absent proxies but preserves real render/export history without replacement states', async () => {
  const h = editorHarness({ initialJobs: [proxyJob({ state: 'succeeded' }), outputJob(), outputJob({ id: EXPORT, kind: 'export' })],
    respond: () => ({ proxies: [] }) });
  await h.editor.refreshProxyJobs();
  assert.equal(h.jobsRef.current.length, 2); assert.equal(h.jobsRef.current.some(job => job.kind === 'proxy'), false);
  assert.deepEqual(h.jobsRef.current.map(job => job.state), ['succeeded', 'succeeded']);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].method, 'GET');
});

test('actual common cancellation uses one DELETE; uncertain cancellation uses GET and no fabricated cancelled state', async () => {
  for (const uncertain of [false, true]) {
    const h = editorHarness({ initialJobs: [proxyJob({ state: 'running' })], respond: (path, init) => {
      if (path === `${ROOT}/audit`) return { records: [] };
      assert.equal(path, `${ROOT}/jobs/${JOB}`);
      if (uncertain && init.method === 'DELETE') throw new Error('Unknown cancel response');
      return proxyJob({ state: uncertain ? 'running' : 'cancelled' });
    } });
    assert.equal(await h.editor.cancelJob(h.jobsRef.current[0]), !uncertain);
    assert.equal(h.calls.filter(row => row.method === 'DELETE').length, 1); assert.equal(h.calls.some(row => row.method === 'POST'), false);
    assert.equal(h.jobsRef.current[0].state, uncertain ? 'running' : 'cancelled');
    if (uncertain) { assert.equal(h.calls[1].method, 'GET'); assert.equal(h.state.status, 'error'); }
  }
});

test('actual common poll retains real proxy/derivative states and refreshes audit without account reads', async () => {
  for (const job of [proxyJob({ state: 'succeeded' }), outputJob(), proxyJob({ state: 'interrupted', error: 'server restarted' })]) {
    const done = deferred(), seen = [], errors = []; let auditReads = 0;
    const effect = editorPollingEffect({ pollingIds: job.id, api: { job: async id => { assert.equal(id, job.id); return job; } },
      mounted: { current: true }, document: { hidden: false, addEventListener() {}, removeEventListener() {} },
      window: { setTimeout() { done.resolve(); return 1; }, clearTimeout() {} },
      upsertJob: value => seen.push(value), isActiveJob: api.isActiveJob,
      refreshAudit: async () => { auditReads++; }, reportError: error => errors.push(error), setPollError() {}, STATE_NAMES: { interrupted: '服务重启中断' } });
    const cleanup = effect(); await done.promise; cleanup();
    assert.equal(seen.length, 1); assert.equal(seen[0].kind, job.kind);
    assert.equal(auditReads, 1); assert.equal(errors.length, job.state === 'interrupted' ? 1 : 0);
  }
});

test('actual source/proxy choice preserves original authority and captured source seconds, never resets same-source trims', () => {
  const p = project(), before = JSON.stringify(p), previews = [], seek = { current: null }, media = { isConnected: true, readyState: 1, currentTime: 2.123456789 };
  const binding = { frozen: false, mutex: { current: false }, caps: capabilities(), proxyState: ready(), catalog: catalog(),
    sourceId: SOURCE, source: source(), preview: { kind: 'source', id: SOURCE }, jobsRef: { current: [proxyJob({ state: 'succeeded' })] },
    mediaRef: { current: media }, previewSeek: seek, ...proxy, ...api,
    setPreview: value => previews.push(plain(value)), setNotice() {}, reportError: forbidden('valid preview selection rejected'),
    chooseSource: forbidden('same-source trims reset'), setProject: forbidden('preview changed project'), project: p };
  const editor = editorStatements(['preserveSourcePosition', 'chooseProxyPreview'], binding);
  editor.chooseProxyPreview(JOB);
  assert.deepEqual(previews, [{ kind: 'proxy', id: JOB }]); assert.equal(seek.current.time, media.currentTime);
  assert.equal(JSON.stringify(p), before);
  const original = editorStatements(['preserveSourcePosition', 'chooseOriginalPreview'], { ...binding, preview: { kind: 'proxy', id: JOB } });
  original.chooseOriginalPreview(); assert.deepEqual(previews.at(-1), { kind: 'source', id: SOURCE });
  assert.equal(seek.current.time, media.currentTime); assert.equal(JSON.stringify(p), before);
});

test('proxy selector rejects unfinished/foreign-source jobs without changing preview or invoking output gates', () => {
  for (const job of [proxyJob({ state: 'queued' }), proxyJob({ state: 'failed' }), proxyJob({ state: 'succeeded', source_id: OTHER }), outputJob()]) {
    const errors = [];
    const editor = editorStatements(['chooseProxyPreview'], { frozen: false, mutex: { current: false }, caps: capabilities(), proxyState: ready(),
      jobsRef: { current: [job] }, catalog: catalog(), studioProxiesAvailable: api.studioProxiesAvailable, canPreviewProxy: proxy.canPreviewProxy,
      reportError: error => errors.push(error), setPreview: forbidden('unavailable preview'), preserveSourcePosition: forbidden('unavailable seek'),
      canReadOutput: forbidden('source preview called output gate') });
    editor.chooseProxyPreview(job.id); assert.equal(errors.length, 1);
  }
});

test('current-frame marking is ORIGINAL ONLY; proxy/frame-rate approximations never write source in/out', () => {
  for (const kind of ['proxy', 'job', 'source']) {
    const marks = [], errors = [], currentTime = 2.123456789;
    const editor = editorStatements(['markCurrentSource'], { preview: { kind, id: kind === 'source' ? SOURCE : JOB },
      source: source(), sourceIsImage: false, previewSource: kind === 'source' ? source() : undefined, frozen: false,
      mediaRef: { current: { isConnected: true, readyState: 1, duration: 8.012345, currentTime } },
      inPoint: 0.123456789, outPoint: 4.987654321, reportError: error => errors.push(error), setSourceRange: (...range) => marks.push(range) });
    editor.markCurrentSource('in'); editor.markCurrentSource('out');
    if (kind === 'source') assert.deepEqual(marks, [[currentTime, 4.987654321], [0.123456789, currentTime]]);
    else { assert.equal(marks.length, 0); assert.equal(errors.length, 2); }
  }
});

test('proxy metadata may restore monitor position but never changes original EOF/candidate range; replaced media events are ignored', () => {
  for (const kind of ['proxy', 'source']) {
    let durations = { [SOURCE]: 8.012345 }; const ranges = [], positions = [], notices = [];
    const media = { isConnected: true, duration: 8.033333, currentTime: 0 }, seek = { current: { kind, id: kind === 'source' ? SOURCE : JOB, time: 2.123456789 } };
    const initialRange = { current: SOURCE };
    const editor = editorStatements(['loadedMetadata'], { preview: { kind, id: kind === 'source' ? SOURCE : JOB }, previewSource: kind === 'source' ? source() : undefined,
      sourceId: SOURCE, initialRange, previewSeek: seek, mediaRef: { current: media }, isImageSource: api.isImageSource,
      setDurations: update => { durations = update(durations); }, setOutPoint: value => ranges.push(value),
      setSourceTime: value => positions.push(value), setNotice: message => notices.push(message) });
    editor.loadedMetadata({ isConnected: false, duration: 99, currentTime: 0 }); assert.equal(durations[SOURCE], 8.012345);
    editor.loadedMetadata(media);
    assert.equal(media.currentTime, 2.123456789); assert.equal(seek.current, null);
    assert.deepEqual(positions, [2.123456789]); assert.equal(notices.length, 0);
    assert.equal(durations[SOURCE], kind === 'source' ? 8.033333 : 8.012345);
    assert.deepEqual(ranges, kind === 'source' ? [3] : []); assert.equal(initialRange.current, kind === 'source' ? null : SOURCE);
  }
});

test('out-of-range quality-switch seek is not clamped or converted into authored source timing', () => {
  const media = { isConnected: true, duration: 8, currentTime: 0 }, notices = [];
  const editor = editorStatements(['loadedMetadata'], { preview: { kind: 'proxy', id: JOB }, previewSource: undefined,
    previewSeek: { current: { kind: 'proxy', id: JOB, time: 8.012345 } }, mediaRef: { current: media },
    setDurations: forbidden('proxy EOF overwrite'), setSourceTime: forbidden('invented seek'), setOutPoint: forbidden('clamped source end'),
    setNotice: message => notices.push(message) });
  editor.loadedMetadata(); assert.equal(media.currentTime, 0); assert.equal(notices.length, 1);
});

test('numeric source ranges and inserted clips continue to use original IDs and exact seconds even when a proxy is displayed', () => {
  const p = project(), selectedTrack = p.tracks[0], ranges = [], inPoint = 0.123456789, outPoint = 4.987654321;
  const editor = editorStatements(['addClip'], { frozen: false, project: p, selectedTrack,
    insertProposal: { value: { seconds: 5, target: null } }, insertingSpan: false, sourceIsImage: false, source: source(), inPoint, outPoint,
    durations: { [SOURCE]: 8.012345 }, preview: { kind: 'proxy', id: JOB }, makeClip: api.makeClip, makeSourceClip: api.makeSourceClip,
    validateSourceRange: (...args) => { ranges.push(args); return timeline.validateSourceRange(...args); }, audioSource: () => false,
    edit: change => change(p), pickClip() {}, displayTime: String, setNotice() {}, reportError: forbidden('valid range rejected') });
  editor.addClip(); const inserted = selectedTrack.clips.at(-1);
  assert.equal(inserted.source_id, SOURCE); assert.equal(inserted.trim, inPoint); assert.equal(inserted.duration, outPoint - inPoint);
  assert.deepEqual(ranges, [[inPoint, outPoint, 8.012345]]); assert.equal(p.workspace.source_marks[0].source_id, SOURCE);
  assert.equal(p.tracks.flatMap(track => track.clips).some(clip => clip.source_id === JOB), false);
});

test('proxy/source clocks do not seek the timeline, and only completed nonproxy jobs expose an output', () => {
  const job = proxyJob({ state: 'succeeded' });
  const output = editorStatements(['canReadOutput'], {}, editorRoot.statements);
  assert.equal(output.canReadOutput(job), false);
  for (const kind of ['render', 'export']) {
    assert.equal(output.canReadOutput(outputJob({ kind })), true);
    assert.equal(output.canReadOutput(outputJob({ kind, output_id: null })), false);
    for (const state of ['queued', 'running', 'failed', 'cancelled', 'interrupted']) {
      assert.equal(output.canReadOutput(outputJob({ kind, state })), false);
    }
    assert.equal(output.canReadOutput(outputJob({ kind, pipeline_revision: undefined })), true);
    assert.equal(output.canReadOutput(outputJob({ kind, pipeline_revision: 1 })), true);
  }
  assert.equal(proxy.canPreviewProxy(job, [source()]), true);
  const playheads = [], media = { isConnected: true, currentTime: 2.123456789, duration: 8.012345 };
  const editor = editorStatements(['readMediaTime', 'seekTimeline'], { previewSource: undefined, previewProxy: job, previewJob: undefined,
    mediaRef: { current: media }, isImageSource: api.isImageSource,
    setSourceTime() {}, setPlayhead: value => playheads.push(value), timelineFPS: 25, quantizeTime: timeline.quantizeTime });
  editor.readMediaTime(); assert.equal(playheads.length, 0);
  editor.seekTimeline(4); assert.deepEqual(playheads, [4]); assert.equal(media.currentTime, 2.123456789);
});

test('actual preview URL branch uses proxy id/route rather than output_id, result.file or source.url', () => {
  for (const token of [undefined, TASK_TOKEN]) {
    const client = api.createStudioApi(TASK, forbidden('preview URL fetch'), token);
    const editor = editorStatements(['previewUrl'], { previewProxy: proxyJob({ state: 'succeeded' }), previewSource: undefined, previewJob: undefined,
      api: client, sourceUrl: forbidden('proxy replaced with original URL'), outputUrl: forbidden('proxy entered output route') });
    assert.equal(editor.previewUrl, `${ROOT}/proxies/${JOB}/media${token ? `?token=${token}` : ''}`);
  }
});

test('proxy media failure asks for a read-only refresh without inventing a failed job or auto-recreating it', () => {
  const errors = [], states = [], media = { isConnected: true, readyState: 1 };
  const editor = editorStatements(['mediaFailure'], { preview: { kind: 'proxy', id: JOB }, mediaRef: { current: media },
    setPreviewError: value => errors.push(value), setProxyState: value => states.push(value),
    upsertJob: forbidden('fabricated failure state'), setPreview: forbidden('automatic original fallback'),
    startProxy: forbidden('automatic retry'), api: { createProxy: forbidden('automatic POST'), proxies: forbidden('hidden GET loop') } });
  editor.mediaFailure({ isConnected: false, readyState: 1 }); assert.equal(errors.length, 0);
  editor.mediaFailure(media); assert.equal(errors.length, 1); assert.equal(states[0].status, 'error');
  assert.match(states[0].message, /刷新代理状态/); assert.match(states[0].message, /不自动重建/);
});

test('profile readout uses measured stream/container metadata, not a guessed target or result source-duration field', () => {
  const readout = proxy.proxyProfileReadout(proxyJob({ state: 'succeeded' }));
  assert.match(readout, /640 × 360/); assert.match(readout, /30 FPS/); assert.match(readout, /h264/); assert.match(readout, /aac/);
  assert.match(readout, /8\.033333s/); assert.equal(readout.includes('8.012345s'), false);
  const pending = proxy.proxyProfileReadout(proxyJob()); assert.match(pending, /尚无可读取的媒体实测信息/);
  assert.equal(pending.includes('服务器实测：'), false);
});

test('card render is inert, escaped, explicitly selected and accessible; no success URL or percentage is invented', () => {
  const html = render(proxy.default, { source: source({ name: '<script>not markup</script>.mp4' }), jobs: [proxyJob({ state: 'succeeded' })],
    state: ready(), preview: { kind: 'source', id: SOURCE }, available: true, busy: false, dirty: true, savedRevision: 7,
    onCreate: forbidden('create on render'), onRefresh: forbidden('GET on render'), onOriginal: forbidden('auto original'), onPreview: forbidden('auto proxy') });
  assert.match(html, /data-studio-section="proxy"/); assert.match(html, /aria-labelledby=/); assert.match(html, /aria-describedby=/);
  assert.match(html, /aria-live="polite"/); assert.match(html, /value="original" selected=""/);
  assert.match(html, /&lt;script&gt;not markup&lt;\/script&gt;\.mp4/); assert.equal(html.includes('<script>'), false);
  assert.match(html, /保存草稿并核验 \/ 复用源代理/); assert.match(html, /切回原片/);
  assert.equal(/<(video|audio|img)|href=|autoplay/i.test(html), false);
  assert.equal(/localStorage|sessionStorage|dangerouslySetInnerHTML|fetch\(/.test(proxySource), false);
  assert.match(proxySource, /min-inline-size:0/); assert.match(proxySource, /min-block-size:44px/);
});

test('actual card change handler only selects known completed proxies; arbitrary values and unavailable state are inert', () => {
  // Only useId is replaced for direct tree inspection; production event code is unchanged.
  const direct = load(proxySource, 'StudioProxy.tsx', { react: { useId: () => 'unit-card' }, 'react/jsx-runtime': jsxRuntime, '../lib/studioApi': api });
  const findSelect = node => {
    if (!node || typeof node !== 'object') return undefined;
    if (node.type === 'select') return node;
    return React.Children.toArray(node.props?.children).map(findSelect).find(Boolean);
  };
  const choices = [], props = { source: source(), jobs: [proxyJob({ state: 'succeeded' }), proxyJob({ id: RENDER, state: 'running' })],
    state: ready(), preview: { kind: 'source', id: SOURCE }, available: true, busy: false, dirty: false, savedRevision: 7,
    onCreate: forbidden('selection created a proxy'), onRefresh: forbidden('selection queried state'), onOriginal: () => choices.push('original'),
    onPreview: id => choices.push(id) };
  const selector = findSelect(direct.default(props)); assert.ok(selector);
  for (const value of ['https://unsafe.invalid/video', RENDER, 'unknown', JOB, 'original']) selector.props.onChange({ target: { value } });
  assert.deepEqual(choices, [JOB, 'original']);
  const unavailable = findSelect(direct.default({ ...props, state: { status: 'error', message: 'unknown response' } }));
  unavailable.props.onChange({ target: { value: JOB } }); assert.deepEqual(choices, [JOB, 'original']);
});

test('unavailable, failed-read, active and full-quota cards cannot create while keeping original preview recovery visible', () => {
  const base = { source: source(), jobs: [], state: ready(), preview: { kind: 'source', id: SOURCE }, available: true,
    busy: false, dirty: false, savedRevision: 7, onCreate() {}, onRefresh() {}, onOriginal() {}, onPreview() {} };
  for (const patch of [{ available: false, state: proxy.EMPTY_STUDIO_PROXIES }, { state: { status: 'error', message: 'Refresh first' } },
    { state: { status: 'loading', message: '' } }, { jobs: [proxyJob({ state: 'running' })] }, { source: source({ duration: 121 }) },
    { jobs: Array.from({ length: 20 }, (_, i) => outputJob({ id: i.toString(16).padStart(32, '0') })) }]) {
    const html = render(proxy.default, { ...base, ...patch });
    assert.match(html, /<button[^>]*data-proxy-create[^>]*disabled=""/);
    assert.match(html, /<option value="original"/); assert.match(html, /刷新代理状态/);
  }
});

// Seed only useState INITIAL VALUES in the actual module for loaded SSR. All
// render branches, handler declarations and imported modules stay unchanged.
function seedEditor(seeds) {
  const transformed = ts.transform(editorRoot, [context => root => {
    const visit = node => {
      if (ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name) && node.initializer && ts.isCallExpression(node.initializer)
        && ts.isIdentifier(node.initializer.expression) && node.initializer.expression.text === 'useState') {
        const binding = node.name.elements[0];
        if (ts.isBindingElement(binding) && ts.isIdentifier(binding.name) && Object.hasOwn(seeds, binding.name.text)) {
          return ts.factory.updateVariableDeclaration(node, node.name, node.exclamationToken, node.type,
            ts.factory.updateCallExpression(node.initializer, node.initializer.expression, node.initializer.typeArguments,
              [ts.factory.createElementAccessExpression(ts.factory.createIdentifier('__testSeeds'), ts.factory.createStringLiteral(binding.name.text))]));
        }
      }
      return ts.visitEachChild(node, visit, context);
    };
    return ts.visitNode(root, visit);
  }]);
  const source = printer.printFile(transformed.transformed[0]); transformed.dispose();
  return load(source, 'Studio.tsx', studioDependencies, { __testSeeds: seeds });
}
const studioProps = () => ({ taskId: TASK, onBack: forbidden('back on render'), onError: forbidden('error on render'), request: forbidden('request on render') });
function loadedStudio(patch = {}, props = {}) {
  const p = project();
  const seeds = { loading: false, caps: capabilities(), catalog: catalog(), saved: envelope(7, p), project: p,
    jobs: [proxyJob({ state: 'succeeded' }), outputJob()], proxyState: ready(), sourceId: SOURCE,
    preview: { kind: 'source', id: SOURCE }, durations: { [SOURCE]: 8.012345 }, trackId: 'track', clipId: 'clip', ...patch };
  return render(seedEditor(seeds).default, { ...studioProps(), ...props });
}

test('actual main Studio module still loads and renders without executing requests during initial render', () => {
  const studio = load(studioSource, 'Studio.tsx', studioDependencies);
  const html = render(studio.default, studioProps());
  assert.match(html, /专业剪辑台/); assert.match(html, /正在读取工程、素材和能力清单/);
});

test('loaded main defaults to original despite a cached proxy; catalog, render job and proxy job are not conflated', () => {
  const html = loadedStudio({ jobs: [proxyJob({ state: 'succeeded', cached: true }), outputJob()] });
  assert.match(html, /data-preview-kind="source"/); assert.match(html, new RegExp(`src="${ROOT}/sources/${SOURCE}"`));
  assert.equal(html.includes('https://untrusted.invalid'), false);
  assert.match(html, /data-job-kind="proxy"/); assert.match(html, /data-job-kind="render"/);
  assert.match(html, /源代理 · upload_/); assert.match(html, /时间线渲染/);
  assert.equal((html.match(/class="gm-studio-source(?: is-selected)?"/g) ?? []).length, 1, 'A proxy is never a second catalog source.');
  assert.match(html, /proxy:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/);
  assert.equal(html.includes(`value="job:${JOB}"`), false, 'A proxy is not an export preview choice.');
});

test('loaded main renders selected proxy media via private route without advertising it as a derivative download', () => {
  const html = loadedStudio({ preview: { kind: 'proxy', id: JOB } });
  assert.match(html, /data-preview-kind="proxy"/); assert.match(html, new RegExp(`src="${ROOT}/proxies/${JOB}/media"`));
  assert.match(html, /低分辨率源代理，不包含时间线效果；导出使用原片/); assert.match(html, /data-proxy-profile/);
  assert.match(html, /data-source-mark="in" disabled=""/); assert.match(html, /data-source-mark="out" disabled=""/);
  assert.equal(new RegExp(`href="${ROOT}/proxies/`).test(html), false, 'A source proxy is not an export.');
  assert.match(html, new RegExp(`href="${ROOT}/outputs/${RENDER}"`), 'The separate completed render remains available.');
  assert.doesNotMatch(studioJobArticles(html).find(article => article.includes('data-job-kind="proxy"')), /<a\b|查看已完成输出/);
  assert.match(html, /查看源代理（不含时间线效果）/); assert.equal(/autoplay/i.test(html), false);
  const target = /<select[^>]*aria-label="导出内容"[\s\S]*?<\/select>/.exec(html)?.[0];
  assert.ok(target); assert.equal(target.includes('proxy'), false);
});

test('failed/cancelled/reboot proxy states remain visible and cannot silently switch to original or output URLs', () => {
  for (const [state, label] of [['failed', '失败'], ['cancelled', '已取消'], ['interrupted', '服务重启中断']]) {
    const html = loadedStudio({ preview: { kind: 'proxy', id: JOB }, jobs: [proxyJob({ state, error: 'Synthetic terminal state' })] });
    assert.match(html, new RegExp(label)); assert.match(html, /源代理尚不可播放/);
    assert.equal(/<video|<audio/.test(html), false); assert.equal(new RegExp(`(?:src|href)="${ROOT}/(?:sources|outputs|proxies)/`).test(html), false);
    assert.match(html, /切回原片以精确记点/); assert.match(html, /刷新当前源代理状态/);
  }
});

test('main integration retains asset declarations and output-kind boundaries without account dependencies', () => {
  for (const name of ['operation', 'uploadAsset', 'refreshAssetCatalog', 'useLut', 'chooseSource', 'addClip']) {
    assert.equal(typeof editorStatements([name], { operation() {} })[name], 'function');
  }
  assert.match(studioSource, /if \(studioProxiesAvailable\(c\)\)[\s\S]*?refreshProxyJobs\(c, controller\.signal\)/);
  assert.match(studioSource, /if \(studioAssetsAvailable\(c\)\)[\s\S]*?api\.assets\(controller\.signal\)/);
  assert.doesNotMatch(studioSource, /(?:from|import)\s+["'][^"']*(?:classroomApi|CloudTools)/);
  assert.match(studioSource, /job\.kind !== "proxy" && job\.state === "succeeded"/);
  assert.match(studioSource, /proxy: "proxy"/); assert.match(studioSource, /<StudioProxy source=\{source\}/);
  assert.match(studioSource, /previewImage \? <img/); assert.match(studioSource, /data-source-mark="in" disabled=\{sourceIsImage/);
  assert.equal(/navigator\.mediaDevices|MediaRecorder/.test(proxySource), false, 'Recording flow remains out of scope.');
});

// Append-only cleanup contracts. Only export visibility changes in this second
// in-memory load: every assertion below uses the ACTUAL job response validator.
const cleanupContract = load(`${apiSource}\nexport { jobResponse };`, 'studioApi.ts');
const CLEANUP_FAILURE_STATES = ['failed', 'cancelled', 'interrupted'];
const CLEANUP_JOB_STATES = ['queued', 'running', 'succeeded', ...CLEANUP_FAILURE_STATES];
const cleanupVariants = patch => [proxyJob(patch), outputJob(patch), outputJob({ kind: 'export', id: EXPORT, ...patch })];
const pendingCleanup = { cleanup_pending: true, cleanup_error: 'Synthetic residue still occupies quota' };
const clearedCleanup = { cleanup_pending: false, cleanup_error: null };

test('cleanup contract preserves legacy omissions for every job state and kind without inventing cleared metadata', () => {
  for (const state of CLEANUP_JOB_STATES) {
    for (const value of cleanupVariants({ state })) {
      const before = plain(value), result = cleanupContract.jobResponse(value);
      assert.equal(Object.hasOwn(result, 'cleanup_pending'), false, `${value.kind}/${state}`);
      assert.equal(Object.hasOwn(result, 'cleanup_error'), false);
      assert.deepEqual(plain(result), before); assert.deepEqual(plain(value), before);
      if (value.kind !== 'proxy') {
        const older = { ...value }; delete older.pipeline_revision;
        const legacy = cleanupContract.jobResponse(older);
        assert.equal(Object.hasOwn(legacy, 'pipeline_revision'), false);
        assert.equal(Object.hasOwn(legacy, 'cleanup_pending'), false);
        assert.equal(Object.hasOwn(legacy, 'cleanup_error'), false);
        assert.deepEqual(plain(legacy), plain(older));
      }
    }
  }
});

test('explicit false/null cleanup is valid in all six states and preserves successful outputs and processing errors', () => {
  for (const state of CLEANUP_JOB_STATES) {
    for (const value of cleanupVariants({ state, ...clearedCleanup,
      error: CLEANUP_FAILURE_STATES.includes(state) ? 'Original processing failure' : null })) {
      const before = plain(value), result = cleanupContract.jobResponse(value);
      assert.equal(result.cleanup_pending, false); assert.equal(result.cleanup_error, null);
      assert.equal(Object.hasOwn(result, 'cleanup_pending'), true); assert.equal(Object.hasOwn(result, 'cleanup_error'), true);
      assert.equal(result.output_id, state === 'succeeded' ? value.id : null);
      assert.equal(Object.hasOwn(result, 'result'), state === 'succeeded');
      assert.equal(api.isActiveJob(result), state === 'queued' || state === 'running');
      assert.deepEqual(plain(result), before); assert.deepEqual(plain(value), before);
    }
  }
});

test('pending cleanup accepts only terminal-failure receipts with distinct nonblank 1–256-code-point errors', () => {
  for (const state of CLEANUP_FAILURE_STATES) {
    for (const cleanup_error of ['清', 'x'.repeat(256), '\u{1F9F9}'.repeat(256), ` ${'x'.repeat(254)} `]) {
      for (const value of cleanupVariants({ state, cleanup_pending: true, cleanup_error, error: null })) {
        const result = cleanupContract.jobResponse(value);
        assert.equal(result.state, state); assert.equal(result.cleanup_pending, true);
        assert.equal(result.cleanup_error, cleanup_error, 'Validate without truncating, trimming or replacing the supplied message.');
        assert.equal(result.error, null, 'Cleanup metadata must not fabricate the original processing error.');
        assert.equal(result.output_id, null); assert.equal(Object.hasOwn(result, 'result'), false);
        assert.equal(api.isActiveJob(result), false);
      }
    }
  }
});

test('queued/running/succeeded cannot claim pending cleanup even when no result is supplied', () => {
  for (const state of ['queued', 'running', 'succeeded']) {
    for (const value of cleanupVariants({ state })) {
      assert.doesNotThrow(() => cleanupContract.jobResponse(value));
      const invalid = { ...value, ...pendingCleanup }; delete invalid.result;
      assert.throws(() => cleanupContract.jobResponse(invalid), /任务残留清理与输出状态/, `${value.kind}/${state}`);
    }
  }
});

test('cleanup pairs reject missing peers, nonboolean pending and every nonnull cleared error across states and kinds', () => {
  const invalid = [
    { cleanup_pending: true }, { cleanup_pending: false }, { cleanup_error: null }, { cleanup_error: 'Residue' },
    { cleanup_pending: undefined, cleanup_error: null }, { cleanup_pending: undefined, cleanup_error: 'Residue' },
    { cleanup_pending: true, cleanup_error: undefined },
    ...[null, 0, 1, 'true', 'false', [], {}, { valueOf: () => true }].map(cleanup_pending => ({ cleanup_pending, cleanup_error: null })),
    ...[undefined, '', ' ', 'Residue', false, true, 0, 1, [], {}, ['Residue']].map(cleanup_error => ({ cleanup_pending: false, cleanup_error })),
  ];
  for (const state of CLEANUP_JOB_STATES) {
    for (const value of cleanupVariants({ state })) {
      for (const [index, pair] of invalid.entries()) {
        assert.throws(() => cleanupContract.jobResponse({ ...value, ...pair }), /Studio .*无效或响应不兼容/, `${value.kind}/${state}/pair ${index}`);
      }
    }
  }
});

test('pending cleanup rejects blank, overlong and nonstring errors without coercion in each terminal failure', () => {
  const invalid = [undefined, null, '', ' \t\r\n', '\u00a0\u3000', 'x'.repeat(257), '\u{1F9F9}'.repeat(257),
    false, true, 0, 1, [], {}, ['Residue'], { toString: () => 'Residue' }];
  for (const state of CLEANUP_FAILURE_STATES) {
    for (const value of cleanupVariants({ state, ...pendingCleanup })) {
      for (const [index, cleanup_error] of invalid.entries()) {
        assert.throws(() => cleanupContract.jobResponse({ ...value, cleanup_error }), /任务残留清理提示/, `${value.kind}/${state}/error ${index}`);
      }
    }
  }
});

test('pending terminal cleanup forbids both result payloads and output IDs independently', () => {
  for (const state of CLEANUP_FAILURE_STATES) {
    for (const value of cleanupVariants({ state, ...pendingCleanup })) {
      assert.doesNotThrow(() => cleanupContract.jobResponse(value));
      for (const result of [measured(), null, {}, [], 'output.mp4']) {
        assert.throws(() => cleanupContract.jobResponse({ ...value, result }), /任务残留清理与输出状态/, `${value.kind}/${state}`);
      }
      assert.throws(() => cleanupContract.jobResponse({ ...value, output_id: value.id }), /任务输出状态/);
      assert.throws(() => cleanupContract.jobResponse({ ...value, output_id: 'https://untrusted.invalid/output' }), /任务输出标识/);
      assert.equal(value.output_id, null); assert.equal(Object.hasOwn(value, 'result'), false);
    }
  }
});

test('persisted proxy list validates cleanup on every row and never silently drops an incompatible receipt', () => {
  const pending = proxyJob({ state: 'interrupted', ...pendingCleanup });
  const cleared = proxyJob({ id: RENDER, state: 'cancelled', ...clearedCleanup });
  const input = { proxies: [pending, cleared] }, before = plain(input);
  const result = api.normalizeStudioProxies(input);
  assert.deepEqual(plain(result), before.proxies);
  for (const patch of [{ cleanup_pending: undefined }, { cleanup_error: 'Not null' }]) {
    assert.throws(() => api.normalizeStudioProxies({ proxies: [pending, { ...cleared, ...patch }] }), /Studio /);
  }
  assert.deepEqual(plain(input), before);
});

test('upsert accepts cleanup completion but rejects late pending receipts over every known cleared terminal state', () => {
  for (const state of [...CLEANUP_FAILURE_STATES, 'succeeded']) {
    for (const value of cleanupVariants({ state, ...clearedCleanup })) {
      const cleared = cleanupContract.jobResponse(value);
      const peer = cleanupContract.jobResponse(outputJob({ id: '7'.repeat(32), created_at: 2000 }));
      const previous = state === 'succeeded' ? cleared : cleanupContract.jobResponse({ ...value, ...pendingCleanup });
      const history = Object.freeze([peer, previous]), before = plain(history);
      const jobs = api.upsertStudioJob(history, cleared);
      assert.equal(jobs.find(job => job.id === cleared.id), cleared, 'The actual clearing receipt must replace pending metadata.');
      for (const lateState of CLEANUP_FAILURE_STATES) {
        const raw = { ...value, state: lateState, output_id: null, ...pendingCleanup, error: 'Late stale error must not replace the known receipt' };
        delete raw.result;
        const late = cleanupContract.jobResponse(raw), merged = api.upsertStudioJob(jobs, late);
        assert.deepEqual(plain(merged), plain(jobs), `${value.kind}/${state} must ignore late ${lateState}/pending`);
        assert.equal(merged.find(job => job.id === cleared.id), cleared);
        assert.equal(merged.find(job => job.id === peer.id), peer);
        assert.equal(merged.filter(job => job.id === cleared.id).length, 1);
      }
      assert.deepEqual(plain(history), before, 'Neither the original history nor its receipts may be mutated.');
    }
  }
});

test('upsert distinguishes unknown legacy cleanup and active false from confirmed terminal clearing, scoped to job ID', () => {
  for (const state of CLEANUP_JOB_STATES.filter(state => state !== 'succeeded')) {
    for (const value of cleanupVariants({ state, ...(state === 'queued' || state === 'running' ? clearedCleanup : {}) })) {
      const previous = cleanupContract.jobResponse(value);
      const pending = cleanupContract.jobResponse({ ...value, state: CLEANUP_FAILURE_STATES.includes(state) ? state : 'failed', ...pendingCleanup });
      const jobs = api.upsertStudioJob([previous], pending);
      assert.equal(jobs[0], pending, `${value.kind}/${state}: omission or an active false is not confirmed terminal cleanup.`);
      const updated = cleanupContract.jobResponse({ ...pending, cleanup_error: 'A newer pending-cleanup explanation' });
      assert.equal(api.upsertStudioJob(jobs, updated)[0], updated, 'A pending receipt can still receive a newer pending explanation.');
    }
  }
  const cleared = cleanupContract.jobResponse(proxyJob({ state: 'failed', ...clearedCleanup }));
  const other = cleanupContract.jobResponse(proxyJob({ id: '8'.repeat(32), state: 'failed', ...pendingCleanup }));
  const jobs = api.upsertStudioJob([cleared], other);
  assert.equal(jobs.length, 2); assert.equal(jobs.find(job => job.id === cleared.id), cleared);
  assert.equal(jobs.find(job => job.id === other.id), other, 'Another ID must not inherit the first job’s cleared flag.');
});

test('late cleanup still validates kind/source/revision/cache identity before the cleared-state early return', () => {
  for (const value of cleanupVariants({ state: 'failed', ...clearedCleanup })) {
    const previous = cleanupContract.jobResponse(value), history = Object.freeze([previous]), before = plain(history);
    const changes = [{ ...value, revision: 8 }, { ...value, pipeline_revision: 3 },
      outputJob({ id: value.id, kind: value.kind === 'render' ? 'export' : 'render', state: 'failed' })];
    if (value.kind === 'proxy') changes.push({ ...value, source_id: OTHER }, { ...value, cache_key: 'e'.repeat(64) });
    else { const omitted = { ...value }; delete omitted.pipeline_revision; changes.push(omitted); }
    for (const changed of changes) {
      const incompatible = cleanupContract.jobResponse({ ...changed, ...pendingCleanup });
      assert.throws(() => api.upsertStudioJob(history, incompatible), /同一作业的类型 \/ 源 \/ 修订 \/ 缓存标识发生变化/);
      assert.deepEqual(plain(history), before); assert.equal(history[0], previous);
    }
  }
});

const studioJobArticles = html => [...html.matchAll(/<article\b[^>]*data-job-kind="(?:proxy|render|export)"[^>]*>[\s\S]*?<\/article>/g)].map(match => match[0]);

test('loaded Studio SSR shows terminal cleanup quota/no-output warnings and escaped errors, never progress or download links', () => {
  for (const [state, label] of [['failed', '失败'], ['cancelled', '已取消'], ['interrupted', '服务重启中断']]) {
    const jobs = cleanupVariants({ state, ...pendingCleanup, error: '<script>processing & failure</script>',
      cleanup_error: '<script>cleanup & pending</script>' }).map(cleanupContract.jobResponse);
    const html = loadedStudio({ jobs, preview: null }), articles = studioJobArticles(html);
    assert.equal(articles.length, 3); assert.doesNotMatch(html, /<script\b/i);
    for (const [index, article] of articles.entries()) {
      assert.match(article, new RegExp(`data-job-kind="${jobs[index].kind}"`)); assert.ok(article.includes(label));
      const warning = /<p\b[^>]*class="gm-studio-warning"[^>]*data-job-cleanup="pending"[^>]*>([\s\S]*?)<\/p>/.exec(article)?.[1];
      assert.ok(warning); assert.match(warning, /作业已结束/); assert.match(warning, /仍占用 Studio 存储额度/);
      assert.match(warning, /没有可下载结果/); assert.match(warning, /读取状态重试清理/); assert.match(warning, /不会重新编码/);
      const button = /<button\b[^>]*>读取状态并重试清理<\/button>/.exec(article)?.[0];
      assert.ok(button); assert.doesNotMatch(button, /\bdisabled=/);
      assert.match(article, /&lt;script&gt;processing &amp; failure&lt;\/script&gt;/);
      assert.match(article, /<pre>[\s\S]*&quot;cleanup_pending&quot;: true[\s\S]*&quot;cleanup_error&quot;: &quot;&lt;script&gt;cleanup &amp; pending&lt;\/script&gt;&quot;/);
      assert.doesNotMatch(article, /<progress\b|role="progressbar"|<a\b|\bhref=|取消并清理|查看已完成输出|查看源代理（不含时间线效果）/);
    }
  }
});

test('loaded Studio SSR disables cleanup retry while frozen and does not expose jobs during initial loading', () => {
  const jobs = cleanupVariants({ state: 'failed', ...pendingCleanup }).map((job, index) =>
    cleanupContract.jobResponse({ ...job, state: CLEANUP_FAILURE_STATES[index] }));
  for (const busy of ['', 'Synthetic operation in flight']) {
    const articles = studioJobArticles(loadedStudio({ jobs, busy, preview: null }));
    assert.equal(articles.length, jobs.length);
    for (const article of articles) {
      const button = /<button\b[^>]*>读取状态并重试清理<\/button>/.exec(article)?.[0];
      assert.ok(button); assert.equal(/\bdisabled=""/.test(button), !!busy);
      assert.match(article, /data-job-cleanup="pending"/); assert.doesNotMatch(article, /<progress\b|<a\b/);
    }
  }
  const loading = loadedStudio({ jobs, loading: true, preview: null });
  assert.match(loading, /正在读取工程、素材和能力清单/); assert.equal(studioJobArticles(loading).length, 0);
});

test('loaded Studio legacy/cleared histories show indeterminate progress and poll IDs only for queued/running jobs', () => {
  for (const cleanup of [{}, clearedCleanup]) {
    const jobs = CLEANUP_JOB_STATES.flatMap(state => cleanupVariants({ state, ...cleanup })).map((job, index) => {
      const id = (index + 1).toString(16).padStart(32, '0');
      return cleanupContract.jobResponse({ ...job, id, output_id: job.state === 'succeeded' ? id : null });
    });
    // Evaluate the real ID declaration only. No polling effect/timer is run by SSR.
    const ids = editorStatements(['pollingIds'], { jobs, isActiveJob: api.isActiveJob }).pollingIds;
    assert.equal(ids, jobs.filter(job => job.state === 'queued' || job.state === 'running').map(job => job.id).sort().join(','));
    const articles = studioJobArticles(loadedStudio({ jobs, preview: null })); assert.equal(articles.length, jobs.length);
    for (const [index, article] of articles.entries()) {
      const active = jobs[index].state === 'queued' || jobs[index].state === 'running';
      const progress = article.match(/<progress\b[^>]*>/g) ?? [];
      assert.equal(progress.length, active ? 1 : 0, `${jobs[index].kind}/${jobs[index].state}`);
      if (active) { assert.match(progress[0], /服务器未提供百分比/); assert.doesNotMatch(progress[0], /\bvalue=/); }
      assert.equal(/>取消并清理<\/button>/.test(article), active);
      assert.doesNotMatch(article, /data-job-cleanup=|读取状态并重试清理/);
    }
  }
});

test('loaded Studio exposes only completed nonproxy outputs with explicit capability or token-free local routes', () => {
  const jobs = [proxyJob({ state: 'succeeded', ...clearedCleanup }), outputJob(clearedCleanup),
    outputJob({ id: EXPORT, kind: 'export', ...clearedCleanup }), outputJob({ id: 'e'.repeat(32), kind: 'export' }),
    outputJob({ id: 'f'.repeat(32), pipeline_revision: 1, ...clearedCleanup }), outputJob({ id: '1'.repeat(32), pipeline_revision: undefined })]
    .map(cleanupContract.jobResponse);
  for (const accessToken of [undefined, TASK_TOKEN]) {
    const articles = studioJobArticles(loadedStudio({ jobs, preview: null }, { accessToken })); assert.equal(articles.length, jobs.length);
    for (const [index, article] of articles.entries()) {
      const readable = index !== 0, job = jobs[index];
      if (readable) {
        const query = accessToken ? `\\?token=${accessToken}` : '';
        assert.match(article, new RegExp(`<a\\b[^>]*href="${ROOT}/outputs/${job.id}${query}"[^>]*download="studio-r7\\.mp4"[^>]*>下载 MP4</a>`));
      } else assert.doesNotMatch(article, /<a\b|\bhref=/);
      if (job.kind !== 'proxy') {
        const button = /<button\b[^>]*>查看已完成输出<\/button>/.exec(article)?.[0];
        assert.ok(button); assert.equal(/\bdisabled=""/.test(button), !readable);
      }
      assert.doesNotMatch(article, /<progress\b|data-job-cleanup=|读取状态并重试清理/);
    }
  }
});

test('actual rendered export help states BT.709 limited-range conversion plus labels and rejects HDR sources', () => {
  // Rendered product copy only: this is not a pixel, codec or FFmpeg assertion.
  for (const exportTarget of ['render', 'export']) {
    const html = loadedStudio({ jobs: [], preview: null, exportTarget });
    const section = /<section\b[^>]*data-studio-section="export"[^>]*>[\s\S]*?<\/section>/.exec(html)?.[0];
    assert.ok(section);
    const help = /<details><summary>资源边界与格式语义<\/summary>[\s\S]*?<\/details>/.exec(section)?.[0];
    assert.ok(help);
    assert.match(help, /SDR 视频实际转换为 BT\.709 有限范围 YUV 并设置色彩标签；不是 HDR 色彩转换。HDR 源被拒绝。/);
  }
});

// Extract this one actual inline button handler; reuse the existing editor
// harness and its ACTUAL operation/upsert declarations. Capture the promise
// discarded by `void`, without rewriting the handler or executing SSR effects.
function cleanupRetryClick(h, job) {
  const buttons = [];
  const visit = node => {
    if (ts.isJsxElement(node) && node.openingElement.tagName.getText(editorRoot) === 'button'
      && node.children.some(child => ts.isJsxText(child) && child.text.trim() === '读取状态并重试清理')) buttons.push(node);
    ts.forEachChild(node, visit);
  };
  visit(editorNode.body); assert.equal(buttons.length, 1, 'Exactly one actual cleanup retry button.');
  const attributes = buttons[0].openingElement.attributes.properties;
  const onClick = attributes.find(node => ts.isJsxAttribute(node) && node.name.getText(editorRoot) === 'onClick');
  const disabled = attributes.find(node => ts.isJsxAttribute(node) && node.name.getText(editorRoot) === 'disabled');
  assert.ok(onClick?.initializer && ts.isJsxExpression(onClick.initializer) && onClick.initializer.expression && ts.isArrowFunction(onClick.initializer.expression));
  assert.ok(disabled?.initializer && ts.isJsxExpression(disabled.initializer));
  assert.equal(disabled.initializer.expression?.getText(editorRoot), 'frozen');
  let completion;
  const click = load(`export const click = ${printer.printNode(ts.EmitHint.Expression, onClick.initializer.expression, editorRoot)};`,
    'actual-cleanup-retry.ts', {}, { api: h.client, job, mounted: h.mounted, upsertJob: h.editor.upsertJob,
      setNotice: message => h.notices.push(message),
      operation: (...args) => { completion = h.editor.operation(...args); return completion; } }).click;
  return () => {
    completion = undefined; assert.equal(click(), undefined);
    assert.equal(typeof completion?.then, 'function', 'The actual button must delegate to the common operation.');
    return completion;
  };
}

test('actual cleanup button uses one job GET and upsert for pending/cleared responses, never saves or reencodes the draft', async () => {
  for (const value of cleanupVariants({ state: 'failed', ...pendingCleanup })) {
    for (const cleanup of [pendingCleanup, clearedCleanup]) {
      const previous = cleanupContract.jobResponse(value), latest = { ...value, ...cleanup };
      const h = editorHarness({ dirty: true, initialJobs: [previous], respond: () => latest }), before = JSON.stringify(h.project);
      assert.equal(await cleanupRetryClick(h, previous)(), true);
      assert.deepEqual(h.calls.map(row => [row.method, row.path]), [['GET', `${ROOT}/jobs/${previous.id}`]]);
      assert.equal(h.calls[0].init.body, undefined); assert.equal(h.calls[0].init.credentials, 'include'); assert.equal(h.calls[0].init.cache, 'no-store');
      assert.deepEqual(plain(h.jobsRef.current), [plain(latest)]);
      assert.equal(h.jobsRef.current[0].output_id, null); assert.equal(Object.hasOwn(h.jobsRef.current[0], 'result'), false);
      assert.match(h.notices.at(-1), cleanup.cleanup_pending ? /仍计入额度；未重新提交编码任务/ : /已确认残留输出清理完成；原作业结果与工程不变/);
      assert.equal(h.receipts.length, 0); assert.equal(JSON.stringify(h.project), before);
      assert.equal(h.errors.length, 0); assert.equal(h.mutex.current, false);
    }
  }
});

test('actual cleanup retry shares the existing operation mutex and cannot issue overlapping GETs or another editor write', async () => {
  const previous = cleanupContract.jobResponse(proxyJob({ state: 'cancelled', ...pendingCleanup }));
  const latest = { ...previous, ...clearedCleanup }, hold = deferred();
  const h = editorHarness({ dirty: true, initialJobs: [previous], respond: () => hold.promise }), before = JSON.stringify(h.project);
  const click = cleanupRetryClick(h, previous), first = click();
  try {
    assert.equal(h.mutex.current, true); assert.equal(h.jobsRef.current[0], previous);
    assert.equal(await click(), false);
    assert.equal(await h.editor.operation('competing write', forbidden('write during cleanup GET')), false);
    assert.equal(h.calls.length, 1); assert.equal(h.receipts.length, 0);
  } finally { hold.resolve(latest); await first; }
  assert.equal(await first, true); assert.equal(h.mutex.current, false); assert.equal(h.errors.length, 0);
  assert.deepEqual(h.calls.map(row => [row.method, row.path]), [['GET', `${ROOT}/jobs/${JOB}`]]);
  assert.equal(h.jobsRef.current[0].cleanup_pending, false); assert.equal(JSON.stringify(h.project), before);
});

test('actual cleanup handler respects the mounted fence before click and after a held GET without adopting state or changing drafts', async () => {
  for (const phase of ['before', 'pending']) {
    const previous = cleanupContract.jobResponse(outputJob({ state: 'interrupted', ...pendingCleanup }));
    const initialJobs = [previous], hold = deferred();
    const h = editorHarness({ dirty: true, initialJobs, respond: () => hold.promise }), before = JSON.stringify(h.project);
    if (phase === 'before') h.mounted.current = false;
    const completion = cleanupRetryClick(h, previous)(), notices = [...h.notices];
    // Explicit fence input, NOT a simulated claim that React ran unmount effects.
    h.mounted.current = false; hold.resolve({ ...previous, ...clearedCleanup });
    assert.equal(await completion, phase === 'pending');
    assert.deepEqual(h.calls.map(row => [row.method, row.path]), phase === 'pending' ? [['GET', `${ROOT}/jobs/${RENDER}`]] : []);
    assert.equal(h.jobsRef.current, initialJobs); assert.equal(h.jobsRef.current[0], previous);
    assert.deepEqual(h.notices, notices); assert.equal(h.receipts.length, 0); assert.equal(h.errors.length, 0);
    assert.equal(JSON.stringify(h.project), before); assert.equal(h.mutex.current, false);
  }
});

test('actual cleanup GET transport/contract/identity failures preserve pending jobs and unsaved drafts without retry or fake success', async () => {
  const previous = cleanupContract.jobResponse(proxyJob({ state: 'failed', ...pendingCleanup }));
  const cases = [
    ...[403, 409, 500, undefined].map(status => ({ failure: Object.assign(new Error('Synthetic cleanup GET failure'), { status }) })),
    { value: { ...previous, cleanup_pending: false }, message: /已清理任务的残留提示/ },
    { value: { ...previous, id: RENDER }, message: /查询作业 ID 不一致/ },
    { value: { ...previous, revision: 8 }, message: /同一作业的类型 \/ 源 \/ 修订 \/ 缓存标识发生变化/ },
  ];
  for (const scenario of cases) {
    const initialJobs = [previous, outputJob()], snapshot = plain(initialJobs);
    const h = editorHarness({ dirty: true, initialJobs, respond: () => { if (scenario.failure) throw scenario.failure; return scenario.value; } });
    const before = JSON.stringify(h.project);
    assert.equal(await cleanupRetryClick(h, previous)(), false);
    assert.deepEqual(h.calls.map(row => [row.method, row.path]), [['GET', `${ROOT}/jobs/${JOB}`]]);
    assert.equal(h.calls[0].init.body, undefined); assert.equal(h.errors.length, 1);
    if (scenario.failure) assert.equal(h.errors[0], scenario.failure);
    else assert.match(String(h.errors[0]), scenario.message);
    assert.equal(h.jobsRef.current, initialJobs); assert.deepEqual(plain(h.jobsRef.current), snapshot);
    assert.deepEqual(h.notices.filter(Boolean), []); assert.equal(h.receipts.length, 0);
    assert.equal(JSON.stringify(h.project), before); assert.equal(h.mutex.current, false);
  }
});