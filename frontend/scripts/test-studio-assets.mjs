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

// Node-only contracts. Actual modules are transpiled in
// memory. No subprocess, real fetch, browser, provider, storage or file writes.
// React server rendering proves escaped/static markup only: NOT lifecycle,
// layout/320px accessibility, server revision races, FFmpeg or pixel acceptance.
const sourceText = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const studioSource = sourceText('../src/components/Studio.tsx');
function load(source, fileName, dependencies = {}, bindings = {}, env = {}) {
  const compiled = ts.transpileModule(source, {
    fileName, reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, sourceMap: false },
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
    `Actual ${fileName} must transpile without syntax diagnostics.`);
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, TextEncoder, Headers, Blob, File, URL, AbortController,
    crypto: { randomUUID }, ...bindings,
    require(name) {
      if (!Object.hasOwn(dependencies, name)) throw new Error(`Unexpected runtime dependency: ${name}`);
      return dependencies[name];
    },
  });
  new vm.Script(compiled.outputText, { filename: fileName }).runInContext(context, { timeout: 1000 });
  return module.exports;
}
const apiSource = sourceText('../src/lib/studioApi.ts');
const api = load(apiSource, 'studioApi.ts');
const assetsSource = sourceText('../src/components/StudioAssets.tsx');
const assets = load(assetsSource, 'StudioAssets.tsx', { '../lib/studioApi': api, react: React, 'react/jsx-runtime': jsxRuntime });
const plain = value => JSON.parse(JSON.stringify(value));
const TASK = 'unit-task', ROOT = `/api/tasks/${TASK}/studio`;
const IMAGE = `image_${'a'.repeat(24)}`, LUT = `lut_${'b'.repeat(24)}`;
const limits = () => ({ image_bytes: 8388608, lut_bytes: 2097152, assets: 64, dimension: 4096, pixels: 8847360, lut_size: 33 });
const image = (patch = {}) => ({ id: IMAGE, name: 'server-generated.png', bytes: 1728, width: 640, height: 360,
  url: 'https://untrusted.invalid/not-a-preview', ...patch });
const lut = (patch = {}) => ({ id: LUT, name: 'server-generated.cube', bytes: 768, size: 2, ...patch });
const collection = (patch = {}) => ({ images: [image()], luts: [lut()], limits: limits(), ...patch });
const source = (patch = {}) => ({ ...image(), burned_subtitles: false, is_image: true, has_video: true, has_audio: false,
  duration: 120, ...patch });
const video = (patch = {}) => ({ id: 'original', name: 'source.mp4', url: '/ignored', bytes: 1234,
  burned_subtitles: false, duration: 20, has_video: true, has_audio: true, ...patch });
const sources = (rows = [video(), source()]) => ({ sources: rows, shots: [], report: { rows: [], quality: { blockers: 1 } } });
const ready = (catalog = collection()) => ({ status: 'ready', catalog, message: '' });
const receipt = (asset = image(), revision = 7, deduplicated = false) => ({ asset, project_revision: revision, deduplicated });
const file = (kind = 'image', patch = {}) => new File([new Uint8Array([0, 1, 2, 255])],
  patch.name ?? (kind === 'image' ? 'local-only.png' : 'local-only.cube'), { type: patch.type ?? (kind === 'image' ? 'image/png' : 'text/plain') });
const clip = (patch = {}) => api.makeClip({ id: 'clip', source_id: 'original', duration: 3, ...patch });
const project = (type = 'video', clips = [clip()]) => api.normalizeProject({ schema_version: 1, tracks: [{ id: 'track', type, clips }] });
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

// Extract actual editor statements unchanged; only export visibility is added.
// Do not copy the mutex / upload algorithm into a test-side imitation.
function editorStatements(names, bindings) {
  const root = ts.createSourceFile('Studio.tsx', studioSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const editor = root.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'StudioEditor');
  assert.ok(editor?.body);
  const printer = ts.createPrinter();
  const statements = names.map(name => {
    const matches = editor.body.statements.filter(node => ts.isFunctionDeclaration(node) ? node.name?.text === name
      : ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration => ts.isIdentifier(declaration.name) && declaration.name.text === name));
    assert.equal(matches.length, 1, `Find actual ${name}, never a copied implementation.`);
    return printer.printNode(ts.EmitHint.Unspecified, matches[0], root);
  }).join('\n') + `\nexport { ${names.join(', ')} };`;
  return load(statements, 'studio-actual-statements.ts', {}, bindings);
}

test('hard bounds and omitted legacy clip fields receive only documented defaults', () => {
  assert.deepEqual(plain(api.STUDIO_ASSET_LIMITS), limits());
  assert.equal(Object.isFrozen(api.STUDIO_ASSET_LIMITS), true);
  const p = api.parseProjectImport(JSON.stringify({ schema_version: 1, tracks: [{ id: 'v', type: 'video', clips: [
    { id: 'old', source_id: 'original', start: 0.017, trim: 0.123456789, duration: 2.013 },
  ] }] }));
  const c = p.tracks[0].clips[0];
  assert.equal(c.lut_id, null); assert.equal(c.transition_in, null);
  assert.equal(c.start, 0.017); assert.equal(c.trim, 0.123456789); assert.equal(c.duration, 2.013);
  assert.deepEqual(plain(p.workspace), plain(api.makeWorkspace()));
  for (const type of ['video', 'overlay', 'adjustment']) {
    assert.equal(project(type, [clip({ source_id: type === 'adjustment' ? null : 'original' })]).tracks[0].clips[0].lut_id, null);
  }
});

test('full project roundtrip retains transitions, LUT, all color, workspace and precise source/time fields', () => {
  const left = clip({ id: 'left', start: 0.0125, duration: 4, trim: 0.123456789, fit: 'cover', lut_id: LUT,
    temperature: 0.4, hue: -75, shadows: 0.1, highlights: -0.2, fade_amount: 0.3, color_preset: 'cinema',
    rgb_curves: { red: [{ x: 0, y: 1 }, { x: 0.4, y: 0.6 }, { x: 1, y: 0 }] }, sharpen: 0.2, noise: 2 });
  const right = clip({ id: 'right', start: 3.5125, duration: 4, fit: 'cover', lut_id: LUT,
    transition_in: { left_clip_id: 'left', kind: 'wipe_left', duration: 0.5, easing: 'ease_in_out', audio: false } });
  const p = project('video', [left, right]);
  p.workspace = { ...api.makeWorkspace(), layout: 'editing', timeline_zoom: 1.75, timeline_fps: 25,
    time_display: 'frames', snap_enabled: false, ripple_enabled: true, shortcuts: { split: 'X', undo: 'Alt+Z' },
    source_marks: [{ source_id: 'original', in_point: 0.123456789, out_point: 4.987654321 }] };
  p.groups.push({ id: 'group', name: 'Kept' }); p.tracks[0].group_id = 'group';
  p.markers.push({ id: 'marker', time: 9.0125, label: 'Unmoved' });
  p.assets.push({ source_id: 'original', rating: 4, tags: ['retained'] });
  const before = JSON.stringify(p), normalized = api.parseProjectImport(before);
  assert.deepEqual(plain(normalized), plain(p)); assert.equal(JSON.stringify(p), before);
  normalized.tracks[0].clips[0].rgb_curves.red[1].y = 0;
  assert.equal(p.tracks[0].clips[0].rgb_curves.red[1].y, 0.6);
});

test('LUT IDs are typed opaque local identifiers; malformed values and unknown clip fields fail closed', () => {
  for (const lut_id of [null, LUT, 'unknown_old_lut']) assert.equal(api.normalizeClip(clip({ lut_id })).lut_id, lut_id);
  for (const lut_id of ['', false, 1, [], {}, 'x'.repeat(65), '../look.cube', 'https://invalid/look', 'lut3d=file=x']) {
    assert.throws(() => api.normalizeClip(clip({ lut_id })));
  }
  for (const extra of [{ filter: 'lut3d=fake' }, { unknown_field: true }, { lut: LUT }]) {
    assert.throws(() => api.normalizeClip({ ...clip(), ...extra }));
  }
  const p = project(); p.workspace.unknown_preference = true;
  assert.throws(() => api.parseProjectImport(JSON.stringify(p)), 'Unknown workspace preferences cannot be silently dropped.');
});

test('LUT is allowed on video/overlay/adjustment only and is not a source substitution', () => {
  for (const type of ['video', 'overlay', 'adjustment']) {
    const c = clip({ source_id: type === 'adjustment' ? null : 'original', lut_id: LUT });
    assert.equal(project(type, [c]).tracks[0].clips[0].lut_id, LUT);
    assert.equal(c.source_id, type === 'adjustment' ? null : 'original');
  }
  assert.throws(() => project('audio', [clip({ lut_id: LUT })]));
  assert.throws(() => project('text', [clip({ source_id: null, text: 'Title', lut_id: LUT })]));
});

test('color copies detach RGB curves and carry real LUT bindings to every applicable visual track', () => {
  const c = clip({ lut_id: LUT, sharpen: 0.4, noise: 4, temperature: -0.3, color_preset: 'warm',
    rgb_curves: { blue: [{ x: 0, y: 0.1 }, { x: 1, y: 0.9 }] } });
  const copied = api.copyColorParameters(c);
  assert.equal(copied.lut_id, LUT); copied.rgb_curves.blue[0].y = 0.2;
  assert.equal(c.rgb_curves.blue[0].y, 0.1);
  for (const type of ['video', 'overlay', 'adjustment']) {
    const applied = api.applicableColorParameters(copied, type);
    assert.equal(applied.lut_id, LUT); assert.equal(applied.temperature, -0.3);
    assert.equal(Object.hasOwn(applied, 'source_id'), false);
    assert.equal(Object.hasOwn(applied, 'sharpen'), type !== 'adjustment');
    assert.equal(Object.hasOwn(applied, 'noise'), type !== 'adjustment');
  }
  for (const type of ['audio', 'text']) assert.throws(() => api.applicableColorParameters(copied, type));
});

test('source normalization preserves safe optional image metadata, legacy omissions and additive catalog data', () => {
  const legacy = video(); for (const key of ['duration', 'has_video', 'has_audio']) delete legacy[key];
  const value = sources([legacy, source({ probe_note: 'metadata remains data' })]), snapshot = JSON.stringify(value);
  const normalized = api.normalizeSourceCatalog(value);
  assert.deepEqual(plain(normalized), value); assert.equal(JSON.stringify(value), snapshot);
  assert.equal(Object.hasOwn(normalized.sources[0], 'is_image'), false);
  assert.equal(normalized.sources[1].width, 640); assert.equal(normalized.sources[1].height, 360);
  assert.equal(normalized.sources[1].is_image, true); assert.equal(normalized.sources[1].duration, 120);
});

test('malformed optional source metadata fails rather than becoming invented video/image timing', () => {
  for (const patch of [{ is_image: 'true' }, { is_image: null }, { is_image: false }, { id: 'not-an-image', is_image: true },
    { has_audio: true }, { width: 0 }, { height: -1 }, { width: 4097 }, { width: 4096, height: 4096 },
    { width: null }, { height: 1.5 }, { duration: 121 }, { duration: null }, { duration: NaN }, { duration: Infinity }]) {
    assert.throws(() => api.normalizeSourceCatalog(sources([source(patch)])));
  }
  assert.throws(() => api.normalizeSourceCatalog(sources([source(), source()])));
  for (const patch of [{ has_video: null }, { has_audio: 'yes' }, { duration: 3601 }, { width: '1920' }]) {
    assert.throws(() => api.normalizeSourceCatalog(sources([video(patch)])));
  }
});

test('image recognition uses metadata/canonical IDs, not filename or a fake playback clock', () => {
  assert.equal(api.isImageSource(source()), true);
  assert.equal(api.isImageSource(source({ is_image: undefined, duration: undefined })), true);
  assert.equal(api.isImageSource(video({ name: 'misleading.png' })), false);
  assert.equal(api.imageDisplayLimit(source()), 120);
  assert.equal(api.imageDisplayLimit(source({ duration: undefined })), 120);
  assert.throws(() => api.imageDisplayLimit(video()));
  assert.throws(() => api.imageDisplayLimit(source({ duration: 121 })));
});

test('explicit image creation forces valid still timing/audio while retaining authored visual fields', () => {
  const patch = { id: 'still', duration: 12.013, start: 0.017, trim: 9, speed: 2, reverse: true, freeze: true, mute: false,
    volume: 2, pan: 1, audio_effect: 'reverb', bass_db: 4, treble_db: -3, lut_id: LUT,
    scale: 0.5, opacity: 0.7, x: 0.1, rotation: 90,
    mask: { type: 'ellipse', x: 0.5, y: 0.5, width: 0.8, height: 0.8, feather: 0.1, invert: false },
    keyframes: { x: [{ time: 0, value: 0, easing: 'ease_in' }, { time: 10, value: 0.5, easing: 'linear' }], y: [], scale: [], opacity: [] } };
  const before = JSON.stringify(patch), c = api.makeSourceClip(source(), patch);
  for (const [key, value] of Object.entries(api.IMAGE_CLIP_FIELDS)) assert.equal(c[key], value);
  for (const key of ['lut_id', 'scale', 'opacity', 'x', 'rotation', 'mask', 'keyframes', 'start', 'duration']) {
    assert.deepEqual(plain(c[key]), patch[key]);
  }
  assert.equal(c.source_id, IMAGE); assert.equal(JSON.stringify(patch), before);
  assert.doesNotThrow(() => project('overlay', [c]));
  assert.equal(api.makeSourceClip(source(), { id: 'full', duration: 120 }).duration, 120);
  assert.throws(() => api.makeSourceClip(source(), { id: 'long', duration: 120.001 }));
});

test('existing/imported invalid still clips are rejected, not silently repaired', () => {
  const valid = api.makeSourceClip(source(), { id: 'still', duration: 3 });
  for (const patch of [{ trim: 0.1 }, { speed: 1.25 }, { reverse: true }, { freeze: true }, { mute: false },
    { volume: 0.5 }, { pan: 0.2 }, { audio_effect: 'reverb' }, { bass_db: 1 }, { treble_db: -1 }]) {
    const bad = { ...valid, ...patch }, before = JSON.stringify(bad);
    assert.throws(() => api.normalizeClip(bad)); assert.equal(JSON.stringify(bad), before);
  }
  assert.throws(() => project('audio', [valid]));
  assert.throws(() => project('adjustment', [valid]));
});

test('image display duration ignores stale media-duration cache, while real video EOF warnings remain', () => {
  const p = project('overlay', [api.makeSourceClip(source(), { id: 'still', duration: 120 })]);
  assert.deepEqual(plain(api.projectWarnings(p, { [IMAGE]: 0.04 }, [source()])), []);
  assert.ok(api.projectWarnings(project('video', [clip({ duration: 3 })]), { original: 1 }, [video()]).some(message => message.includes('素材')));
  assert.ok(api.projectWarnings(p, {}, [source({ duration: 60 })]).some(message => message.includes('显示时长')));
});

test('asset catalog validates and detaches exact response rows without using server names/URLs as markup', () => {
  const value = collection({ images: [image({ name: '<img src=x onerror=bad>.png' })], luts: [lut({ name: '<script>bad</script>.cube' })] });
  const snapshot = JSON.stringify(value), parsed = api.normalizeStudioAssets(value);
  assert.deepEqual(plain(parsed), value); assert.equal(JSON.stringify(value), snapshot);
  parsed.images[0].name = 'local change'; assert.notEqual(value.images[0].name, 'local change');
});

test('asset schema rejects unknown fields, missing limits, invalid dimensions/grid sizes and duplicate IDs', () => {
  for (const value of [null, [], { ...collection(), extra: true }, { ...collection(), limits: undefined },
    collection({ limits: { ...limits(), unknown: 1 } }), collection({ images: [image({ extra: true })] }),
    collection({ luts: [lut({ filter: 'script' })] }), collection({ images: [image(), image()] }),
    collection({ luts: [lut({ id: IMAGE })] })]) assert.throws(() => api.normalizeStudioAssets(value));
  for (const patch of [{ id: 'image_short' }, { bytes: 0 }, { bytes: 8388609 }, { width: 4097 },
    { width: 4096, height: 4096 }, { height: NaN }, { url: null }, { name: 4 }]) {
    assert.throws(() => api.normalizeStudioAssets(collection({ images: [image(patch)] })));
  }
  for (const patch of [{ id: '../lut' }, { bytes: 2097153 }, { size: 1 }, { size: 34 }, { size: 2.5 }, { size: '33' }]) {
    assert.throws(() => api.normalizeStudioAssets(collection({ luts: [lut(patch)] })));
  }
  for (const key of Object.keys(limits())) {
    assert.throws(() => api.normalizeStudioAssets(collection({ limits: { ...limits(), [key]: limits()[key] + 1 } })));
  }
});

test('64 combined assets is bounded, including mixed lists and server-lowered limits', () => {
  const luts = Array.from({ length: 64 }, (_, index) => lut({ id: `lut_${index}` }));
  assert.equal(api.normalizeStudioAssets(collection({ images: [], luts })).luts.length, 64);
  assert.throws(() => api.normalizeStudioAssets(collection({ luts })));
  assert.throws(() => api.normalizeStudioAssets(collection({ limits: { ...limits(), assets: 1 } })));
  assert.equal(api.normalizeStudioAssets(collection({ images: [], luts: [lut({ size: 33 })] })).luts[0].size, 33);
});

test('old/unavailable capability fixtures do not opt in to the asset endpoint', () => {
  for (const caps of [null, { tools: [] }, { tools: [{ id: 'stickerCustom', available: false }, { id: 'lut', available: false }] },
    { tools: [{ id: 'stickerLib', available: true }, { id: 'stickerAnim', available: true }] }]) assert.equal(api.studioAssetsAvailable(caps), false);
  for (const id of ['stickerCustom', 'lut']) assert.equal(api.studioAssetsAvailable({ tools: [{ id, available: true }] }), true);
});

test('image file preflight enforces bytes, extension and MIME before upload, including exact boundaries', () => {
  for (const [name, type, expected] of [['x.png', 'image/png', 'image/png'], ['x.JPG', 'image/jpeg', 'image/jpeg'],
    ['x.jpeg', '', 'image/jpeg'], ['x.PNG', '', 'image/png']]) {
    assert.equal(api.validateStudioAssetFile('image', { name, type, size: 8388608 }), expected);
  }
  for (const patch of [{ size: 0 }, { size: 8388609 }, { size: -1 }, { size: 1.5 }, { size: Infinity }, { size: NaN },
    { name: 'x.svg' }, { name: 'x.gif' }, { name: 'x.png.exe' }, { name: 'x.jpg', type: 'image/png' },
    { type: 'text/html' }, { type: 'image/svg+xml' }, { type: 'image/gif' }]) {
    assert.throws(() => api.validateStudioAssetFile('image', { name: 'x.png', type: 'image/png', size: 12, ...patch }));
  }
});

test('LUT file preflight accepts only bounded .cube with text/unknown MIME and always sends text/plain', () => {
  for (const type of ['text/plain', '', 'application/octet-stream']) {
    assert.equal(api.validateStudioAssetFile('lut', { name: 'local.CUBE', type, size: 2097152 }), 'text/plain');
  }
  for (const patch of [{ name: 'x.txt' }, { name: 'x.cube.html' }, { type: 'text/html' }, { type: 'application/json' },
    { size: 0 }, { size: 2097153 }, { size: '12' }]) {
    assert.throws(() => api.validateStudioAssetFile('lut', { name: 'x.cube', type: 'text/plain', size: 12, ...patch }));
  }
});

test('local file limits can be reduced by server but never increased beyond client hard caps', () => {
  assert.throws(() => api.validateStudioAssetFile('image', { name: 'x.png', type: 'image/png', size: 17 }, { ...limits(), image_bytes: 16 }));
  assert.throws(() => api.validateStudioAssetFile('image', { name: 'x.png', type: 'image/png', size: 8388609 }, { ...limits(), image_bytes: 99999999 }));
  assert.throws(() => api.validateStudioAssetFile('lut', file('lut'), { ...limits(), lut_bytes: NaN }));
});

test('actual assets GET delegates protected same-origin transport and returns validated data once', async () => {
  const calls = [], signal = new AbortController().signal;
  const client = api.createStudioApi(TASK, async (path, init) => { calls.push({ path, init }); return collection(); });
  assert.deepEqual(plain(await client.assets(signal)), collection());
  assert.equal(calls.length, 1); assert.equal(calls[0].path, `${ROOT}/assets`);
  const init = calls[0].init;
  assert.equal(init.signal, signal); assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store');
  assert.equal(init.mode, 'same-origin'); assert.equal(init.redirect, 'error'); assert.equal(init.body, undefined);
});

test('PNG/JPEG/LUT POST uses the SAME raw File/Blob, revision query, explicit MIME and no filename header', async () => {
  const calls = [], signal = new AbortController().signal;
  const client = api.createStudioApi(TASK, async (path, init) => {
    calls.push({ path, init }); return receipt(path.includes('/assets/lut') ? lut() : image());
  }, 'synthetic-task-token');
  for (const [kind, upload, mime] of [['image', file(), 'image/png'],
    ['image', file('image', { name: 'private-name.jpg', type: 'image/jpeg' }), 'image/jpeg'], ['lut', file('lut'), 'text/plain']]) {
    upload.text = () => { throw new Error('Never read/re-encode raw contents.'); };
    upload.arrayBuffer = () => { throw new Error('Never read/re-encode raw contents.'); };
    const result = await (kind === 'image' ? client.importImage(7, upload, signal) : client.importLut(7, upload, signal));
    const { path, init } = calls.at(-1), headers = new Headers(init.headers);
    assert.equal(path, `${ROOT}/assets/${kind}?expected_revision=7`);
    assert.equal(init.method, 'POST'); assert.equal(init.body, upload); assert.ok(init.body instanceof Blob);
    assert.equal(init.signal, signal); assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store');
    assert.equal(init.mode, 'same-origin'); assert.equal(init.redirect, 'error'); assert.equal(headers.get('Content-Type'), mime);
    assert.deepEqual([...headers.keys()].sort(), ['content-type', 'x-task-token']);
    assert.equal(headers.has('X-Classroom-CSRF'), false, 'Host transport, not the Studio wrapper, supplies session CSRF.');
    assert.equal(result.project_revision, 7); assert.equal(result.asset.name, kind === 'image' ? 'server-generated.png' : 'server-generated.cube');
    assert.equal(result.deduplicated, false);
  }
  assert.equal(calls.length, 3);
});

test('invalid revisions/files never reach the import transport', async () => {
  let calls = 0; const client = api.createStudioApi(TASK, async () => { calls++; return receipt(); });
  for (const revision of [-1, 1.5, NaN, Infinity, '7', null]) {
    await assert.rejects(client.importImage(revision, file()));
    await assert.rejects(client.importLut(revision, file('lut')));
  }
  await assert.rejects(client.importImage(7, file('image', { type: 'text/html' })));
  await assert.rejects(client.importLut(7, file('lut', { name: 'not-cube.txt' })));
  assert.equal(calls, 0);
});

test('asset receipts cannot advance revision or invent deduplication, and bad receipts are never retried', async () => {
  let value = receipt(), calls = 0;
  const client = api.createStudioApi(TASK, async () => { calls++; return value; });
  for (const bad of [receipt(image(), 8), receipt(image(), 6), receipt(image(), '7'), receipt(image(), 7, 'true'),
    { ...receipt(), extra: true }, { asset: image(), project_revision: 7 }, receipt(image({ id: 'not_image' }))]) {
    value = bad; const before = calls;
    await assert.rejects(client.importImage(7, file())); assert.equal(calls, before + 1);
  }
  value = receipt(image(), 7, true); assert.equal((await client.importImage(7, file())).deduplicated, true);
});

test('401/403/409 and uncertain failures propagate without automatic POST retry or fabricated receipt', async () => {
  for (const status of [401, 403, 409, 500]) {
    let calls = 0; const failure = Object.assign(new Error('Synthetic rejection'), { status });
    const client = api.createStudioApi(TASK, async () => { calls++; throw failure; });
    await assert.rejects(client.importImage(7, file()), error => error === failure);
    await assert.rejects(client.importLut(7, file('lut')), error => error === failure);
    assert.equal(calls, 2, 'Only the two explicitly requested imports ran.');
  }
});

test('image URLs always use known same-origin source routes, including an external Vite base configuration', () => {
  const withBase = load(apiSource, 'studioApi.ts', {}, {}, { VITE_API_BASE_URL: 'https://not-for-images.invalid' });
  const client = withBase.createStudioApi(TASK, () => { throw new Error('Constructing a URL must not request it.'); }, 'synthetic token/only');
  assert.equal(client.sourceUrl(IMAGE), `${ROOT}/sources/${IMAGE}?token=synthetic%20token%2Fonly`);
  assert.throws(() => client.sourceUrl('../outside'));
  assert.throws(() => client.sourceUrl('https://outside.invalid/file'));
});

test('import coordinator waits for the actual save receipt and uses its revision without assuming +1', async () => {
  const hold = deferred(), calls = [], p = project();
  const client = api.createStudioApi(TASK, async (path, init) => {
    calls.push({ path, init });
    if (path === `${ROOT}/project`) { await hold.promise; return { revision: 41, project: p, can_undo: true, can_redo: false }; }
    assert.equal(path, `${ROOT}/assets/image?expected_revision=41`); return receipt(image(), 41);
  });
  const imported = assets.importStudioAsset({ api: client, kind: 'image', file: file(), limits: limits(),
    onDirtySave: () => client.save(7, p), isCurrent: () => true });
  assert.equal(calls.length, 1); assert.equal(calls[0].path, `${ROOT}/project`);
  assert.equal(JSON.parse(calls[0].init.body).expected_revision, 7);
  hold.resolve(); const result = await imported;
  assert.equal(result.kind, 'image'); assert.equal(result.receipt.project_revision, 41); assert.equal(calls.length, 2);
});

test('coordinator validates before saving; save conflicts do not upload or discard draft data', async () => {
  let saves = 0, posts = 0;
  const client = api.createStudioApi(TASK, async () => { posts++; return receipt(); });
  const failure = new Error('Synthetic save conflict');
  const args = { api: client, kind: 'image', limits: limits(), isCurrent: () => true,
    onDirtySave: async () => { saves++; throw failure; } };
  await assert.rejects(assets.importStudioAsset({ ...args, file: file('image', { name: 'x.svg' }) }));
  assert.equal(saves, 0); assert.equal(posts, 0);
  await assert.rejects(assets.importStudioAsset({ ...args, file: file() }), error => error === failure);
  assert.equal(saves, 1); assert.equal(posts, 0);
});

test('coordinator fences an old editor before save, after save and after upload without replay', async () => {
  for (const closeAt of ['before', 'save', 'upload']) {
    let current = closeAt !== 'before', saves = 0, posts = 0;
    const client = api.createStudioApi(TASK, async () => { posts++; if (closeAt === 'upload') current = false; return receipt(); });
    await assert.rejects(assets.importStudioAsset({ api: client, kind: 'image', file: file(), limits: limits(), isCurrent: () => current,
      onDirtySave: async () => { saves++; if (closeAt === 'save') current = false; return { revision: 7 }; } }));
    assert.equal(saves, closeAt === 'before' ? 0 : 1); assert.equal(posts, closeAt === 'upload' ? 1 : 0);
  }
});

test('actual editor operation mutex excludes overlapping imports and releases after success/error', async () => {
  const mutex = { current: false }, mounted = { current: true }, labels = [], errors = [], hold = deferred();
  const editor = editorStatements(['operation'], { mutex, mounted, setBusy: label => labels.push(label),
    setError() {}, setNotice() {}, reportError: error => errors.push(error) });
  let firstRuns = 0, secondRuns = 0;
  const first = editor.operation('first', async () => { firstRuns++; await hold.promise; });
  assert.equal(mutex.current, true);
  assert.equal(await editor.operation('second', async () => { secondRuns++; }), false);
  assert.equal(firstRuns, 1); assert.equal(secondRuns, 0);
  hold.resolve(); assert.equal(await first, true); assert.equal(mutex.current, false);
  assert.deepEqual(labels, ['first', '']);
  assert.equal(await editor.operation('failed', async () => { throw new Error('unit failure'); }), false);
  assert.equal(errors.length, 1); assert.equal(mutex.current, false);
  mounted.current = false; assert.equal(await editor.operation('unmounted', async () => { secondRuns++; }), false);
  assert.equal(secondRuns, 0);
});

test('actual upload handler refreshes assets/sources and selects only a returned image; it does not add clips/LUT', async () => {
  let state = ready(collection({ images: [], luts: [] }));
  const chosen = [], notices = [], catalogs = [], calls = [], errors = [], saved = project();
  const before = JSON.stringify(saved);
  const client = api.createStudioApi(TASK, async (path, init) => {
    calls.push({ path, method: init.method ?? 'GET' });
    if (path === `${ROOT}/assets/image?expected_revision=41`) return receipt(image(), 41);
    if (path === `${ROOT}/assets`) return collection();
    if (path === `${ROOT}/sources`) return sources();
    throw new Error('Unexpected request; no auto-save, render or project mutation after asset import.');
  });
  const editor = editorStatements(['operation', 'refreshAssetCatalog', 'uploadAsset'], {
    api: client, assetState: state, mutex: { current: false }, mounted: { current: true },
    caps: { tools: [{ id: 'stickerCustom', available: true }] }, studioAssetsAvailable: api.studioAssetsAvailable,
    isImageSource: api.isImageSource,
    supports: id => id === 'stickerCustom', importStudioAsset: assets.importStudioAsset,
    saveDraft: async () => ({ revision: 41, project: saved }),
    setBusy() {}, setError() {}, setNotice: message => notices.push(message), reportError: error => errors.push(error),
    setAssetState: update => { state = typeof update === 'function' ? update(state) : update; },
    setCatalog: value => catalogs.push(value), chooseSource: (id, catalog) => chosen.push({ id, catalog }), refreshAudit: async () => {},
  });
  assert.equal(await editor.uploadAsset('image', file()), true);
  assert.equal(errors.length, 0); assert.equal(state.status, 'ready');
  assert.equal(state.catalog.images[0].id, IMAGE); assert.equal(catalogs.length, 1);
  assert.equal(chosen.length, 1); assert.equal(chosen[0].id, IMAGE);
  assert.ok(chosen[0].catalog.sources.some(item => item.id === IMAGE && item.is_image));
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(JSON.stringify(saved), before); assert.ok(notices.some(message => message.includes('r41')));
});

test('actual upload failures never install guessed rows or select a nonexistent image; confirmed receipts survive refresh failure', async () => {
  for (const phase of ['post', 'refresh', 'missing-source']) {
    let state = ready(collection({ images: [], luts: [] }));
    const selected = [], errors = [], calls = [];
    const client = api.createStudioApi(TASK, async path => {
      calls.push(path);
      if (path.includes('/assets/image?')) {
        if (phase === 'post') throw new Error('Synthetic upload failure');
        return receipt();
      }
      if (path === `${ROOT}/assets`) {
        if (phase === 'refresh') throw new Error('Synthetic GET failure');
        return collection();
      }
      if (path === `${ROOT}/sources`) return sources(phase === 'missing-source' ? [video()] : [video(), source()]);
      throw new Error('Unexpected request.');
    });
    const editor = editorStatements(['operation', 'refreshAssetCatalog', 'uploadAsset'], {
      api: client, assetState: state, mutex: { current: false }, mounted: { current: true },
      caps: { tools: [{ id: 'stickerCustom', available: true }] }, studioAssetsAvailable: api.studioAssetsAvailable,
      isImageSource: api.isImageSource, supports: () => true, importStudioAsset: assets.importStudioAsset,
      saveDraft: async () => ({ revision: 7 }), setBusy() {}, setError() {}, setNotice() {}, reportError: error => errors.push(error),
      setAssetState: update => { state = typeof update === 'function' ? update(state) : update; },
      setCatalog() {}, chooseSource: id => selected.push(id), refreshAudit: async () => {},
    });
    assert.equal(await editor.uploadAsset('image', file()), phase !== 'post');
    assert.equal(selected.length, 0); assert.equal(calls.filter(path => path.includes('/assets/image?')).length, 1);
    assert.equal(state.catalog.images.length, phase === 'post' ? 0 : 1);
    assert.equal(state.status, phase === 'post' ? 'ready' : 'error');
    assert.equal(errors.length, phase === 'post' ? 1 : 0);
  }
});

test('actual LUT handler imports and refreshes without automatically binding a clip or selecting a source', async () => {
  let state = ready(collection({ images: [], luts: [] }));
  const projectSnapshot = project(), before = JSON.stringify(projectSnapshot), calls = [];
  const client = api.createStudioApi(TASK, async path => {
    calls.push(path);
    if (path.includes('/assets/lut?')) return receipt(lut(), 7, true);
    if (path === `${ROOT}/assets`) return collection({ images: [] });
    if (path === `${ROOT}/sources`) return sources([video()]);
    throw new Error('No automatic render/project mutation.');
  });
  const editor = editorStatements(['operation', 'refreshAssetCatalog', 'uploadAsset'], {
    api: client, assetState: state, mutex: { current: false }, mounted: { current: true },
    caps: { tools: [{ id: 'lut', available: true }] }, studioAssetsAvailable: api.studioAssetsAvailable,
    isImageSource: api.isImageSource, supports: id => id === 'lut', importStudioAsset: assets.importStudioAsset,
    saveDraft: async () => ({ revision: 7, project: projectSnapshot }), setBusy() {}, setError() {}, setNotice() {},
    reportError: error => assert.fail(String(error)),
    setAssetState: update => { state = typeof update === 'function' ? update(state) : update; }, setCatalog() {},
    chooseSource() { assert.fail('LUTs are not media sources.'); }, refreshAudit: async () => {},
  });
  assert.equal(await editor.uploadAsset('lut', file('lut')), true);
  assert.equal(state.status, 'ready'); assert.equal(state.catalog.luts[0].id, LUT);
  assert.equal(projectSnapshot.tracks[0].clips[0].lut_id, null); assert.equal(JSON.stringify(projectSnapshot), before);
  assert.equal(calls.filter(path => path.includes('/assets/lut?')).length, 1);
});

test('actual clip LUT update rejects foreign/unloaded IDs and uses the existing typed update path, including clear', () => {
  const p = project(), updates = [], errors = [], state = ready();
  const selection = { track: p.tracks[0], clip: p.tracks[0].clips[0] };
  const editor = editorStatements(['useLut'], { frozen: false, selection, supports: () => true,
    assetState: state, updateClip: patch => updates.push(plain(patch)), reportError: error => errors.push(error) });
  editor.useLut(LUT); editor.useLut('not-from-this-task'); editor.useLut(null);
  assert.deepEqual(updates, [{ lut_id: LUT }, { lut_id: null }]); assert.equal(errors.length, 1);
  state.status = 'error'; editor.useLut(LUT); assert.equal(updates.length, 2); assert.equal(errors.length, 2);
  selection.track.locked = true; editor.useLut(null); assert.equal(updates.length, 2);
  selection.track.locked = false; selection.track.type = 'audio'; editor.useLut(LUT); assert.equal(updates.length, 2);
});

test('actual image source selection resets only the candidate form, not project marks, tracks or playback', () => {
  const p = project(), before = JSON.stringify(p), selected = [], previews = [], ins = [], outs = [], holds = [];
  const initialRange = { current: 'previous' };
  const editor = editorStatements(['chooseSource'], { catalog: sources(), project: p, durations: { [IMAGE]: 0.04 }, initialRange,
    isImageSource: api.isImageSource, imageDisplayLimit: api.imageDisplayLimit,
    setSourceId: id => selected.push(id), setTagDraft() {}, setPreview: preview => previews.push(plain(preview)),
    setInPoint: value => ins.push(value), setOutPoint: value => outs.push(value), setSourceRangeError() {},
    setImageDuration: value => holds.push(value) });
  editor.chooseSource('not-in-catalog'); assert.equal(selected.length, 0);
  editor.chooseSource(IMAGE);
  assert.deepEqual(selected, [IMAGE]); assert.deepEqual(previews, [{ kind: 'source', id: IMAGE }]);
  assert.deepEqual(ins, [0]); assert.deepEqual(outs, [0]); assert.deepEqual(holds, [3]);
  assert.equal(initialRange.current, null); assert.equal(JSON.stringify(p), before);
});

test('actual addClip creates a valid still only in an explicitly selected visual target and ignores fake EOF', () => {
  const p = project('overlay', []), selectedTrack = p.tracks[0], picked = [], errors = [];
  const editor = editorStatements(['addClip'], { frozen: false, project: p, selectedTrack,
    insertProposal: { value: { seconds: 2, target: null }, error: '' }, insertingSpan: false,
    sourceIsImage: true, source: source(), inPoint: 999, outPoint: 1000, imageDuration: 12,
    durations: { [IMAGE]: 0.04 }, makeSourceClip: api.makeSourceClip, makeClip: api.makeClip,
    audioSource: () => false, edit: change => change(p), pickClip: (track, c) => picked.push({ track, c }),
    reportError: error => errors.push(error), displayTime: String, setNotice() {},
    validateSourceRange() { assert.fail('A static image has no source media range to validate.'); } });
  editor.addClip(); assert.equal(errors.length, 0); assert.equal(picked.length, 1);
  const inserted = selectedTrack.clips[0];
  assert.equal(inserted.source_id, IMAGE); assert.equal(inserted.start, 2); assert.equal(inserted.duration, 12);
  assert.equal(inserted.trim, 0); assert.equal(inserted.mute, true); assert.equal(inserted.speed, 1); assert.equal(inserted.freeze, false);
  assert.doesNotThrow(() => api.normalizeProject(p));
  selectedTrack.type = 'audio'; editor.addClip(); assert.equal(errors.length, 1); assert.equal(selectedTrack.clips.length, 1);
});

const TASK_TOKEN = 't'.repeat(48);
function appTransport(respond, token = TASK_TOKEN) {
  const host = load(sourceText('../src/lib/appApi.ts'), 'appApi.ts', {}, {
    FormData, Response, DOMException, setTimeout, clearTimeout,
    location: { origin: 'https://node-only.invalid' },
    // Deliberately NOT the host fetch: every path is handled in-memory.
    fetch: async (url, init) => {
      const result = await respond(url, init);
      return new Response(JSON.stringify(result.payload), { status: result.status ?? 200,
        headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...result.headers } });
    },
  });
  return host.createAppRequest({ taskId: TASK, token, signal: new AbortController().signal });
}
const session = () => ({ access_mode: 'anonymous', csrf_token: 'c'.repeat(48),
  expires_at: new Date(Date.now() + 600_000).toISOString() });

test('raw upload uses actual appApi anonymous CSRF and the selected task capability, not wrapper-injected credentials', async () => {
  const calls = [], upload = file();
  const request = appTransport((path, init) => {
    calls.push({ path, method: init.method ?? 'GET' });
    if (path === '/api/session') return { payload: session() };
    assert.equal(path, `${ROOT}/assets/image?expected_revision=7`);
    const headers = new Headers(init.headers);
    assert.equal(headers.get('X-CSRF-Token'), session().csrf_token);
    assert.equal(headers.get('X-Task-Token'), TASK_TOKEN);
    assert.equal(headers.has('X-Classroom-CSRF'), false); assert.equal(headers.has('Authorization'), false);
    assert.equal(headers.get('Accept'), 'application/json');
    assert.equal(headers.get('Content-Type'), 'image/png'); assert.equal(init.body, upload);
    assert.equal(init.credentials, 'include'); assert.equal(init.redirect, 'error'); assert.equal(init.mode, 'same-origin');
    assert.equal(init.cache, 'no-store');
    return { payload: receipt() };
  });
  const client = api.createStudioApi(TASK, request, 'injected-wrapper-token'.padEnd(48, '_'));
  assert.equal((await client.importImage(7, upload)).project_revision, 7);
  assert.deepEqual(calls.map(call => call.method), ['GET', 'POST']);
});

test('actual appApi invalidates rejected anonymous CSRF without replaying or automatically refreshing an asset POST', async () => {
  const calls = [];
  const request = appTransport((path, init) => {
    calls.push({ path, method: init.method ?? 'GET' });
    if (path === '/api/session') return { payload: session() };
    assert.equal(path, `${ROOT}/assets/lut?expected_revision=7`);
    assert.equal(new Headers(init.headers).get('Content-Type'), 'text/plain');
    return { status: 403, payload: { detail: 'Invalid anonymous CSRF' }, headers: { 'X-Anonymous-Session': 'required' } };
  });
  const client = api.createStudioApi(TASK, request);
  await assert.rejects(client.importLut(7, file('lut')), error => error.status === 403);
  assert.deepEqual(calls.map(call => call.method), ['GET', 'POST']);
  assert.equal(calls.filter(call => call.path.startsWith(`${ROOT}/assets/`)).length, 1);
});

test('LUT picker uses actual IDs/null, keeps unknown bindings visible and disables selection while unloaded', () => {
  const applied = [], props = { value: LUT, state: ready(), available: true, disabled: false, onChange: id => applied.push(id) };
  const tree = assets.StudioLutPicker(props);
  const findSelect = node => {
    if (!node || typeof node !== 'object') return undefined;
    if (node.type === 'select') return node;
    return React.Children.toArray(node.props?.children).map(findSelect).find(Boolean);
  };
  const selector = findSelect(tree); assert.ok(selector);
  selector.props.onChange({ target: { value: LUT } }); selector.props.onChange({ target: { value: '' } });
  selector.props.onChange({ target: { value: 'unknown-injected-id' } });
  assert.deepEqual(applied, [LUT, null]);
  const unknown = render(assets.StudioLutPicker, { ...props, value: 'unbound_old_lut' });
  assert.match(unknown, /unbound_old_lut/); assert.match(unknown, /未知/); assert.match(unknown, /role="alert"/);
  const escaped = render(assets.StudioLutPicker, { ...props, state: ready(collection({ luts: [lut({ name: '<script>not markup</script>.cube' })] })) });
  assert.match(escaped, /&lt;script&gt;not markup&lt;\/script&gt;\.cube/); assert.equal(escaped.includes('<script>'), false);
  for (const status of ['loading', 'error', 'unavailable']) {
    const output = render(assets.StudioLutPicker, { ...props, state: { status, catalog: null, message: 'not loaded' } });
    assert.match(output, /<select[^>]*disabled/); assert.match(output, new RegExp(`data-lut-id="${LUT}"`));
  }
});

test('local collection escapes server text, ignores catalog URLs and has no video/autoplay/effect preview', () => {
  const name = '<img src=x onerror=bad>.png', client = api.createStudioApi(TASK, () => { throw new Error('No I/O during static render.'); });
  const html = render(assets.ReferenceLocalCollection, { api: client, catalog: sources(), images: [image({ name })], busy: false,
    selectedId: '', onSelectImage() { throw new Error('Never select/import during render.'); } });
  assert.match(html, /&lt;img src=x onerror=bad&gt;\.png/);
  assert.equal(html.includes('https://untrusted.invalid'), false); assert.equal(html.includes('<img src=x'), false);
  assert.match(html, new RegExp(`src="${ROOT}/sources/${IMAGE}"`));
  assert.equal(/<video|<audio|autoplay|data:image|blob:/i.test(html), false);
  assert.match(html, /ReferenceLocalCollection/); assert.match(html, /不自动加入时间线/);
});

test('asset panel is controlled/read-only on render, with upload anchors and disabled truthful fallback/tracking', () => {
  let actions = 0;
  const props = { api: { sourceUrl: id => `${ROOT}/sources/${id}` }, catalog: sources(), state: ready(), savedRevision: 7,
    busy: false, dirty: false, imageAvailable: true, lutAvailable: true, selectedSourceId: '', selectedLutId: null,
    canUseLut: false, canAnimateImage: false, onImport: async () => { actions++; return true; },
    onCatalogRefresh() { actions++; }, onSelectImage() { actions++; }, onUseLut() { actions++; }, onAnimateImage() { actions++; } };
  const html = render(assets.default, props);
  assert.match(html, /data-studio-section="assets"/); assert.match(html, /data-asset-upload="image"/); assert.match(html, /data-asset-upload="lut"/);
  assert.match(html, /<button[^>]*disabled[^>]*>自动跟踪（未支持）/);
  assert.match(html, /没有预装贴纸库|真实文件|服务器生成/); assert.equal(actions, 0);
  const fallback = render(assets.default, { ...props, state: assets.EMPTY_STUDIO_ASSETS, imageAvailable: false, lutAvailable: false });
  assert.equal((fallback.match(/<fieldset disabled/g) ?? []).length, 2);
  assert.match(fallback, /素材端点未加载/); assert.equal(actions, 0);
  assert.equal(/dangerouslySetInnerHTML|localStorage|sessionStorage|readAsDataURL/.test(assetsSource), false);
});

test('owned Studio integration retains capability bounds and routes assets/stills to real controls (static source checks)', () => {
  const compiled = ts.transpileModule(studioSource, { fileName: 'Studio.tsx', reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve } });
  assert.equal(compiled.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  assert.match(studioSource, /if \(studioAssetsAvailable\(c\)\)[\s\S]*?api\.assets\(controller\.signal\)/);
  for (const id of ['stickerCustom', 'stickerLib', 'stickerAnim', 'lut']) assert.match(studioSource, new RegExp(`${id}: "assets"`));
  assert.match(studioSource, /data-source-mark="in" disabled=\{sourceIsImage/);
  assert.match(studioSource, /data-source-mark="out" disabled=\{sourceIsImage/);
  assert.match(studioSource, /previewImage \? <img/); assert.match(studioSource, /data-image-display-duration/);
  assert.match(studioSource, /!isImageSource\(source\) && isMulticamVideoSource\(source\)/);
  assert.match(studioSource, /createStudioApi\(taskId,[\s\S]*?accessToken\)/);
  assert.doesNotMatch(studioSource, /(?:from|import)\s+["'][^"']*(?:classroomApi|CloudTools)/);
  assert.match(studioSource, /assetState\.catalog\?\.luts\.some\(row => row\.id === colorClipboard\.lut_id\)/);
  const upload = editorStatements(['uploadAsset'], { operation: () => {}, supports: () => true });
  assert.equal(typeof upload.uploadAsset, 'function', 'The static declaration must remain available without side effects.');
});