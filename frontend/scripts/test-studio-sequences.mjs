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

// Node-only contracts. Actual TS modules and
// actual Studio editor statements are transpiled in memory. No subprocess,
// real network, browser, provider, media execution, storage or file writes.
// SSR checks markup/control boundaries only, not lifecycle, layout or FFmpeg.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const forbidden = label => () => assert.fail(`Unexpected side effect: ${label}`);
function load(source, fileName, dependencies = {}, bindings = {}) {
  const parsed = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true,
    fileName.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  assert.equal(parsed.parseDiagnostics.length, 0, `${fileName}: actual source must parse`);
  const compiled = ts.transpileModule(source, { fileName, reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, sourceMap: false },
    transformers: { before: [context => root => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
        && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
        ? ts.factory.createObjectLiteralExpression([]) : ts.visitEachChild(node, visit, context);
      return ts.visitNode(root, visit);
    }] },
  });
  assert.equal(compiled.diagnostics?.filter(item => item.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, TextEncoder, Headers, Blob, File, URL, AbortController,
    crypto: { randomUUID }, fetch: forbidden('real fetch'), ...bindings,
    require(name) { assert.ok(Object.hasOwn(dependencies, name), `Unlisted runtime dependency: ${name}`); return dependencies[name]; },
  });
  new vm.Script(compiled.outputText, { filename: fileName }).runInContext(context, { timeout: 1000 });
  return module.exports;
}
const apiSource = read('../src/lib/studioApi.ts'), sequencesSource = read('../src/lib/studioSequences.ts');
const studioSource = read('../src/components/Studio.tsx');
const api = load(apiSource, 'studioApi.ts');
const sequences = load(sequencesSource, 'studioSequences.ts', { './studioApi': api });
const timeline = load(read('../src/lib/timelineEditing.ts'), 'timelineEditing.ts');
const compositions = load(read('../src/lib/studioCompositions.ts'), 'studioCompositions.ts', { './studioApi': api, './timelineEditing': timeline });
const assets = load(read('../src/components/StudioAssets.tsx'), 'StudioAssets.tsx', { react: React, 'react/jsx-runtime': jsxRuntime, '../lib/studioApi': api });
const proxy = load(read('../src/components/StudioProxy.tsx'), 'StudioProxy.tsx', { react: React, 'react/jsx-runtime': jsxRuntime, '../lib/studioApi': api });
const studioDependencies = { react: React, 'react/jsx-runtime': jsxRuntime, '../lib/studioApi': api, '../lib/studioSequences': sequences,
  '../lib/timelineEditing': timeline, '../lib/studioCompositions': compositions, './StudioAssets': assets, './StudioProxy': proxy };
const TASK = 'synthetic-sequence-task', ROOT = `/api/tasks/${TASK}/studio`;
const IMAGE = `image_${'a'.repeat(24)}`, LUT = `lut_${'b'.repeat(24)}`;
const clip = (id, patch = {}) => api.makeClip({ id, source_id: 'original', duration: 3, ...patch });
const lane = (id, clips = [], type = 'video', patch = {}) => ({ id, type, name: id, clips, ...patch });
const sequence = (id, tracks = [], markers = []) => ({ id, name: id, tracks, markers });
const nested = (id, sequence_id, duration = 3, start = 0) => clip(id, { source_id: null, sequence_id, duration, start });
const normalize = (patch = {}) => api.normalizeProject({ schema_version: 1, name: 'Complete document', tracks: [], ...patch });
const project = () => normalize({
  tracks: [lane('root-track', [clip('root-clip', { start: 0.017, duration: 2.013, trim: 0.123456789 })])],
  markers: [{ id: 'root-marker', time: 0.731, label: 'Main only' }], groups: [{ id: 'group', name: 'Shared' }],
  sequences: [sequence('A', [lane('a-track', [clip('a-clip')])], [{ id: 'a-marker', time: 1.013, label: 'A only' }]),
    sequence('B', [lane('b-track', [clip('b-clip', { duration: 5 })])])],
  assets: [{ source_id: 'original', rating: 4, tags: ['retained'] }],
  workspace: { ...plain(api.makeWorkspace()), timeline_fps: 25, time_display: 'frames', timeline_zoom: 1.75,
    source_marks: [{ source_id: 'original', in_point: 0.123456789, out_point: 8.987654321 }] },
});
const nestedProject = () => normalize({ tracks: [lane('parent', [nested('parent-a', 'A', 5)])],
  sequences: [sequence('A', [lane('a-parent', [nested('a-b', 'B', 3, 2)])]), sequence('B', [lane('b-track', [clip('b-leaf')])])] });
const source = (patch = {}) => ({ id: 'original', name: 'original.mp4', bytes: 1234, url: '/ignored',
  burned_subtitles: false, has_video: true, has_audio: true, duration: 120, ...patch });
const catalog = (rows = [source()]) => ({ sources: rows, shots: [], report: { rows: [], quality: {} } });
const envelope = (p, revision = 7) => ({ revision, project: p, can_undo: true, can_redo: false });
function capabilities() {
  const ids = ['tlPick', 'nest', 'camNest', 'edit', 'trimMode', 'addTrack', 'marker', 'undo', 'tc', 'snap', 'inout', 'ripple', 'trCustom'];
  return { schema_version: 1, tools: ids.map(id => ({ id, group: id === 'camNest' ? 'cam' : id === 'trCustom' ? 'fx' : 'timeline', label: id,
    available: true, classification: 'partial', reason: 'Bounded real saved sequences' })), tool_count: ids.length,
    limits: { tracks: 8, clips: 64, sequences: 4, nesting_depth: 2, expanded_tracks: 8, expanded_clips: 64, decoder_inputs: 16,
      duration_seconds: 120, output_bytes: 134217728, task_output_bytes: 536870912, jobs_per_task: 20, history_mutations: 100,
      threads: 2, global_jobs: 2, max_resolution: 1080, request_bytes: 262144 },
    render_available: true, quality: 'Unreviewed derivative', unsupported: [], project_schema: {}, action_schema: {}, export_schema: {} };
}

const editorRoot = ts.createSourceFile('Studio.tsx', studioSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
assert.equal(editorRoot.parseDiagnostics.length, 0);
const editorNode = editorRoot.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'StudioEditor');
assert.ok(editorNode?.body);
const printer = ts.createPrinter();
function editorStatements(names, bindings = {}) {
  const text = names.map(name => {
    const matches = editorNode.body.statements.filter(node => ts.isFunctionDeclaration(node) ? node.name?.text === name
      : ts.isVariableStatement(node) && node.declarationList.declarations.some(declaration => ts.isIdentifier(declaration.name) && declaration.name.text === name));
    assert.equal(matches.length, 1, `Exactly one actual editor declaration: ${name}`);
    return printer.printNode(ts.EmitHint.Unspecified, matches[0], editorRoot);
  }).join('\n');
  return load(`${text}\nexport { ${names.join(', ')} };`, 'actual-editor-statements.ts', {}, { ...api, ...sequences, ...timeline, ...bindings });
}
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
  const text = printer.printFile(transformed.transformed[0]); transformed.dispose();
  return load(text, 'Studio.tsx', studioDependencies, { __testSeeds: seeds });
}
function renderStudio(p = project(), patch = {}) {
  const seeded = seedEditor({ loading: false, project: p, saved: envelope(p), caps: capabilities(), catalog: catalog(),
    sourceId: 'original', durations: { original: 120 }, trackId: sequences.projectScope(p).tracks[0]?.id ?? '', ...patch });
  return renderToStaticMarkup(React.createElement(seeded.default, { taskId: TASK, onBack: forbidden('back'), onError: forbidden('error'), request: forbidden('SSR network') }));
}
function operationHarness(p, extra = {}) {
  const calls = [], receipts = [], errors = [], notices = [], pending = [];
  const mounted = { current: true }, mutex = { current: false };
  const saved = envelope(p), dirty = extra.dirty ?? false;
  const client = api.createStudioApi(TASK, async (path, init) => {
    calls.push({ path, init });
    if (extra.respond) return extra.respond(path, init);
    if (path === `${ROOT}/audit`) return { records: [] };
    const body = JSON.parse(init.body);
    if (path === `${ROOT}/project`) return envelope(body.project, calls.filter(row => row.path === path).length === 1 ? 41 : 59);
    throw new Error(`Unlisted in-memory request: ${path}`);
  });
  const bindings = { project: p, saved, dirty, warnings: api.projectWarnings(p, {}, catalog().sources), frozen: false,
    supports: () => true, caps: capabilities(), catalog: catalog(), durations: {}, assetState: { status: 'unavailable', catalog: null },
    mounted, mutex, taskId: TASK, activeSequenceLock: sequences.sequenceContentLock(p), api: client,
    setBusy() {}, setError() {}, setNotice: text => notices.push(text), setAudit() {}, sourceRangeError: '',
    reportError: error => errors.push(error), remember: value => receipts.push(value), setNewSequenceName() {}, setNestedSequenceId() {}, ...extra.bindings };
  const base = editorStatements(['operation', 'saveDraft', 'refreshAudit'], bindings);
  const operation = (label, run) => { const result = base.operation(label, run); pending.push(result); return result; };
  const handlers = editorStatements(['switchSequence', 'addSequence', 'confirmDurationBindings', 'insertSequence', 'action'], {
    ...bindings, ...base, operation,
    newSequenceName: 'New sequence', nestedChild: p.sequences[0], nestedPosition: { value: { seconds: 4, target: null } },
    durationBindings: { proposal: extra.bindingProposal ?? null, error: '' }, pickClip() {},
    window: { confirm: extra.confirm ?? (() => true) }, ...extra.bindings,
  });
  return { ...handlers, base, calls, receipts, errors, notices, pending, mutex, mounted };
}

test('legacy additive defaults keep every authored field and precise source clock', () => {
  const raw = { schema_version: 1, tracks: [lane('legacy', [{ id: 'legacy-clip', source_id: 'original', start: 0.017, trim: 0.123456789, duration: 2.013 }])],
    markers: [{ id: 'legacy-marker', time: 3.731, label: 'Keep' }] };
  const before = JSON.stringify(raw), p = api.parseProjectImport(before);
  assert.equal(p.active_sequence_id, null); assert.deepEqual(plain(p.sequences), []);
  assert.equal(p.tracks[0].clips[0].sequence_id, null); assert.equal(p.tracks[0].clips[0].trim, 0.123456789);
  assert.equal(p.tracks[0].clips[0].start, 0.017); assert.equal(p.tracks[0].clips[0].duration, 2.013);
  assert.deepEqual(plain(p.markers), raw.markers); assert.equal(JSON.stringify(raw), before);
  assert.deepEqual(plain(api.normalizeProject(p)), plain(p));
});

test('main plus four independent scopes round-trip without activating or copying root tracks', () => {
  const p = project(); p.sequences.push(sequence('C'), sequence('D')); p.active_sequence_id = 'B';
  const before = JSON.stringify(p), next = api.parseProjectImport(JSON.stringify({ project: p, revision: 91 }));
  assert.deepEqual(plain(next), plain(p)); assert.equal(JSON.stringify(p), before);
  assert.equal(next.tracks[0].id, 'root-track'); assert.equal(next.sequences[1].tracks[0].id, 'b-track');
  next.sequences[1].tracks[0].clips[0].brightness = 0.5;
  assert.equal(p.sequences[1].tracks[0].clips[0].brightness, 0);
  assert.throws(() => normalize({ ...p, sequences: [...p.sequences, sequence('E')] }));
});

test('malformed known fields and unknown sequence/clip fields fail without dropping content', () => {
  const base = project();
  for (const patch of [{ sequences: null }, { sequences: {} }, { active_sequence_id: '' }, { active_sequence_id: 'missing' },
    { active_sequence_id: 1 }, { unknown_sequence_field: true }]) {
    const p = { ...base, ...patch }, before = JSON.stringify(p);
    assert.throws(() => api.normalizeProject(p)); assert.equal(JSON.stringify(p), before);
  }
  for (const s of [{ id: 'X', tracks: [] }, { ...sequence('X'), name: 'x'.repeat(81) }, { ...sequence('X'), markers: null },
    { ...sequence('X'), foreign: [] }, { ...sequence('X'), tracks: Array.from({ length: 9 }, (_, i) => lane(`x${i}`)) }]) {
    assert.throws(() => normalize({ sequences: [s] }));
  }
  for (const sequence_id of ['', 1, false, [], {}, '../other', 'x'.repeat(65)]) assert.throws(() => api.normalizeClip(clip('bad', { sequence_id })));
});

test('sequence/tracks/clips/markers/groups share one global ID namespace', () => {
  for (const mutate of [p => { p.sequences[0].id = p.tracks[0].id; }, p => { p.sequences[0].tracks[0].id = p.tracks[0].id; },
    p => { p.sequences[0].tracks[0].clips[0].id = p.markers[0].id; }, p => { p.sequences[0].markers[0].id = p.groups[0].id; },
    p => { p.groups[0].id = p.sequences[1].id; }, p => { p.sequences[1].tracks[0].clips[0].id = p.sequences[0].tracks[0].clips[0].id; }]) {
    const p = project(); mutate(p); assert.throws(() => api.normalizeProject(p), /ID/);
  }
});

test('all-scope clip cap includes hidden clips and parent descriptors; marker caps are per scope', () => {
  const leaf = lane('leaf-track', Array.from({ length: 63 }, (_, i) => clip(`leaf-${i}`, { duration: 1 })));
  const p = normalize({ tracks: [lane('parent', [nested('ref', 'A', 1)])], sequences: [sequence('A', [leaf])] });
  assert.equal(sequences.projectClipCount(p), 64);
  p.sequences[0].tracks[0].clips.push(clip('too-many', { duration: 1 })); p.sequences[0].tracks[0].hidden = true;
  assert.throws(() => api.normalizeProject(p), /64/);
  const markers = prefix => Array.from({ length: 100 }, (_, i) => ({ id: `${prefix}-${i}`, time: i, label: '' }));
  const marked = normalize({ markers: markers('root'), sequences: [sequence('A', [], markers('a'))] });
  assert.equal(marked.markers.length + marked.sequences[0].markers.length, 200);
  marked.sequences[0].markers.push({ id: 'extra', time: 110, label: '' }); assert.throws(() => api.normalizeProject(marked));
});

test('nested identity requires XOR source and exact defaults except typed inherited mute', () => {
  const p = nestedProject(), c = p.tracks[0].clips[0];
  assert.equal(api.isSequenceClip(c), true); assert.equal(c.source_id, null); assert.equal(c.fit, 'contain');
  const defaults = api.makeClip({ id: c.id, duration: c.duration });
  for (const key of Object.keys(defaults).filter(key => !['id', 'start', 'duration', 'sequence_id'].includes(key))) {
    assert.deepEqual(plain(c[key]), plain(defaults[key]));
  }
  const bad = [{ source_id: 'original' }, { trim: 0.01 }, { speed: 1.5 }, { reverse: true }, { freeze: true }, { fit: 'cover' },
    { scale: 0.5 }, { x: 0.1 }, { opacity: 0.5 }, { mute: 'true' }, { volume: 0.7 }, { audio_effect: 'reverb' },
    { brightness: 0.1 }, { lut_id: LUT }, { text: 'not a label' }, { fade_in: 0.1 }, { mask: { type: 'ellipse' } },
    { keyframes: { x: [{ time: 0, value: 0 }, { time: 1, value: 0.2 }] } }];
  for (const patch of bad) { const value = { ...c, ...patch }, before = JSON.stringify(value); assert.throws(() => api.normalizeClip(value)); assert.equal(JSON.stringify(value), before); }
  assert.equal(api.normalizeClip({ ...c, mute: true }).mute, true);
  for (const type of ['audio', 'text', 'adjustment']) assert.throws(() => normalize({ tracks: [lane('bad', [c], type)], sequences: p.sequences }));
  assert.throws(() => normalize({ tracks: [lane('v', [clip('empty', { source_id: null })])] }));
});

test('real child full end includes its leading gap, visible solo policy, and excludes markers/hidden tails', () => {
  const child = sequence('A', [lane('visible', [clip('v', { start: 2, duration: 3 })]), lane('hidden', [clip('h', { duration: 100 })], 'video', { hidden: true })],
    [{ id: 'far-marker', time: 110, label: '' }]);
  assert.equal(api.sequenceFullEnd(child), 5);
  const p = normalize({ tracks: [lane('parent', [nested('ref', 'A', 5, 7)])], sequences: [child] });
  assert.equal(api.sequenceRenderStats(p).duration, 12);
  p.sequences[0].tracks.push(api.normalizeProject({ schema_version: 1, tracks: [lane('solo', [clip('s', { duration: 2 })], 'video', { solo: true })] }).tracks[0]);
  assert.equal(api.sequenceFullEnd(p.sequences[0]), 2); assert.throws(() => api.normalizeProject(p), /duration/);
  p.tracks[0].clips[0].duration = 2; assert.doesNotThrow(() => api.normalizeProject(p));
});

test('missing child, stale duration and empty child references are errors, never repaired or trimmed', () => {
  for (const change of [p => { p.tracks[0].clips[0].sequence_id = 'missing'; }, p => { p.tracks[0].clips[0].duration = 4; },
    p => { p.sequences[1].tracks[0].hidden = true; }, p => { p.tracks[0].clips[0].start = 119; }]) {
    const p = nestedProject(); change(p); const before = JSON.stringify(p);
    assert.throws(() => api.normalizeProject(p)); assert.ok(api.projectWarnings(p).length); assert.equal(JSON.stringify(p), before);
  }
});

test('depth two works; cycles and third edges fail even in inactive/hidden subgraphs', () => {
  assert.doesNotThrow(() => api.normalizeProject(nestedProject()));
  const cyclic = normalize({ sequences: [sequence('A', [lane('a', [clip('a-leaf')])]), sequence('B', [lane('b', [clip('b-leaf')])])] });
  cyclic.sequences[0].tracks.push(lane('a-ref', [nested('a-b', 'B')], 'overlay', { hidden: true }));
  cyclic.sequences[1].tracks.push(lane('b-ref', [nested('b-a', 'A')], 'overlay', { hidden: true }));
  assert.throws(() => api.normalizeProject(cyclic), /循环/);
  const p = nestedProject(); p.sequences.push(sequence('C', [lane('c', [clip('c-leaf')])]));
  p.sequences[1].tracks[0].clips = [nested('b-c', 'C')]; assert.throws(() => api.normalizeProject(p), /两层/);
  p.tracks = []; assert.doesNotThrow(() => api.normalizeProject(p), 'A may itself be a render root with two edges A→B→C.');
  p.sequences.push(sequence('D', [lane('d', [clip('d-leaf')])]));
  p.sequences[2].tracks[0].clips = [nested('c-d', 'D')]; assert.throws(() => api.normalizeProject(p), /两层/);
});

test('nested same-track overlap rejects, adjacency is legal; ordinary independent overlaps stay legal', () => {
  const p = normalize({ sequences: [sequence('A', [lane('a', [clip('leaf')])])], tracks: [lane('parent', [nested('ref', 'A'), clip('after', { start: 3 })])] });
  p.tracks[0].clips[1].start = 2.9; assert.throws(() => api.normalizeProject(p), /重叠/);
  assert.doesNotThrow(() => normalize({ tracks: [lane('ordinary', [clip('one'), clip('two')])] }));
});

test('active view edits track/marker fields while full root and other sequences remain unchanged', () => {
  const p = sequences.selectProjectSequence(project(), 'A'), before = JSON.stringify(p);
  const next = sequences.editActiveProject(p, working => {
    working.tracks[0].clips[0].brightness = 0.4;
    working.markers.push({ id: 'new-marker', time: 2.313, label: 'Child' });
    working.workspace.timeline_fps = 60; working.name = 'Global name';
  });
  assert.equal(JSON.stringify(p), before); assert.deepEqual(plain(next.tracks), plain(p.tracks));
  assert.deepEqual(plain(next.markers), plain(p.markers)); assert.deepEqual(plain(next.sequences[1]), plain(p.sequences[1]));
  assert.equal(next.sequences[0].tracks[0].clips[0].brightness, 0.4); assert.equal(next.sequences[0].markers.length, 2);
  assert.equal(next.name, 'Global name'); assert.equal(next.workspace.timeline_fps, 60);
  assert.deepEqual(plain(next.workspace.source_marks), plain(p.workspace.source_marks)); assert.doesNotThrow(() => api.normalizeProject(next));
});

test('child transition geometry, LUT, masks, keyframes and exact global metadata survive save/import mapping', () => {
  const p = project();
  p.sequences[0].tracks = [lane('transition-track', [clip('left', { duration: 4, fit: 'cover', lut_id: LUT }),
    clip('right', { start: 3.5, duration: 4, fit: 'cover', transition_in: { left_clip_id: 'left', kind: 'wipe_left', duration: 0.5, easing: 'ease_out', audio: false } })]),
  lane('effects-track', [clip('effects', { trim: 0.123456789, duration: 2.013, scale: 0.5,
    mask: { type: 'ellipse', x: 0.5, y: 0.5, width: 0.8, height: 0.8, feather: 0.1, invert: false },
    keyframes: { x: [{ time: 0, value: 0, easing: 'linear' }, { time: 1.017, value: 0.2, easing: 'ease_in_out' }], y: [], scale: [], opacity: [] },
    rgb_curves: { red: [{ x: 0, y: 0.1 }, { x: 1, y: 0.9 }] } })], 'overlay')];
  const selected = sequences.selectProjectSequence(p, 'A');
  const edited = sequences.editActiveProject(selected, working => { working.markers[0].label = 'Still child-only'; });
  const imported = api.parseProjectImport(JSON.stringify({ project: edited, revision: 11 }));
  assert.deepEqual(plain(imported.sequences[0].tracks), plain(api.normalizeProject(p).sequences[0].tracks));
  assert.deepEqual(plain(imported.tracks), plain(p.tracks)); assert.deepEqual(plain(imported.workspace), plain(p.workspace));
  assert.equal(imported.sequences[0].tracks[0].clips[1].transition_in.left_clip_id, 'left');
});

test('stale/wrong-scope/copied working views cannot be mistaken for complete save documents', async () => {
  const p = sequences.selectProjectSequence(project(), 'A'), working = sequences.activeProject(p);
  let calls = 0; const client = api.createStudioApi(TASK, async () => { calls++; return envelope(p); });
  assert.throws(() => api.normalizeProject(working), /工作视图/); await assert.rejects(client.save(7, working)); assert.equal(calls, 0);
  assert.throws(() => sequences.replaceActiveProject(sequences.selectProjectSequence(p, 'B'), working));
  assert.throws(() => sequences.replaceActiveProject({ ...p, name: 'Newer' }, working));
  assert.throws(() => sequences.replaceActiveProject(p, plain(working)), 'JSON cloning must not forge the view basis.');
  working.sequences[1].tracks = []; assert.throws(() => sequences.replaceActiveProject(p, working));
});

test('temporary invalid child input remains in its own draft, never normalizes away root content', () => {
  const p = sequences.selectProjectSequence(project(), 'A');
  const draft = sequences.editActiveProject(p, working => { working.tracks[0].clips[0].duration = -1; });
  assert.equal(draft.sequences[0].tracks[0].clips[0].duration, -1); assert.deepEqual(plain(draft.tracks), plain(p.tracks));
  assert.throws(() => api.normalizeProject(draft)); assert.ok(api.projectWarnings(draft).length);
});

test('repeated switching and child edits do not cumulatively copy root tracks or rewrite precise globals', () => {
  let p = project(); const root = plain(p.tracks), marks = plain(p.workspace.source_marks);
  for (let i = 0; i < 12; i++) {
    const id = i % 2 ? 'B' : 'A'; p = sequences.selectProjectSequence(p, id);
    p = sequences.editActiveProject(p, working => { working.tracks[0].clips[0].start = (i + 1) / 25; });
    p = api.parseProjectImport(JSON.stringify(p));
    assert.deepEqual(plain(p.tracks), root); assert.deepEqual(plain(p.workspace.source_marks), marks);
    assert.equal(new Set(sequences.allProjectTracks(p).map(track => track.id)).size, 3);
  }
  p = sequences.selectProjectSequence(p, null); assert.equal(sequences.projectScope(p).tracks[0].id, 'root-track');
  assert.equal(sequences.projectClipCount(p), 3);
});

test('starter lanes are only for a wholly empty initial main project; switching never fabricates them', () => {
  assert.equal(sequences.isEmptySequenceProject(normalize()), true);
  for (const p of [project(), normalize({ sequences: [sequence('A')] }), normalize({ sequences: [sequence('A')], active_sequence_id: 'A' }),
    normalize({ markers: [{ id: 'm', time: 0, label: '' }] })]) assert.equal(sequences.isEmptySequenceProject(p), false);
  const p = sequences.createProjectSequence(project(), 'C', 'Blank');
  assert.equal(p.active_sequence_id, 'C'); assert.deepEqual(plain(sequences.projectScope(p).tracks), []);
  assert.deepEqual(plain(p.tracks), plain(project().tracks));
});

test('nested insertion is deterministic and uses a new dedicated lane with complete identity', () => {
  const p = project(), options = { sequenceId: 'A', start: 6 / 25, trackId: 'nest-track', clipId: 'nest-clip' };
  const before = JSON.stringify(p), next = sequences.insertNestedSequence(p, options);
  assert.equal(JSON.stringify(p), before); assert.deepEqual(plain(next.tracks[0]), plain(p.tracks[0]));
  const track = next.tracks[1], c = track.clips[0];
  assert.equal(track.type, 'overlay'); assert.equal(track.clips.length, 1); assert.equal(c.start, 6 / 25); assert.equal(c.duration, 3);
  assert.equal(c.source_id, null); assert.equal(c.sequence_id, 'A'); assert.equal(c.fit, 'contain');
  assert.deepEqual(plain(sequences.insertNestedSequence(p, options)), plain(next));
  assert.deepEqual(plain(next.sequences), plain(p.sequences));
  for (const patch of [{ start: -1 }, { start: 118 }, { sequenceId: 'missing' }, { trackId: 'a-track' }, { clipId: 'a-clip' }, { clipId: undefined }]) {
    assert.throws(() => sequences.insertNestedSequence(p, { ...options, ...patch }));
  }
  assert.throws(() => sequences.insertNestedSequence(sequences.selectProjectSequence(p, 'A'), options));
});

test('insertion respects active-scope tracks, solo, empty-child and inherited-lock boundaries', () => {
  const options = { sequenceId: 'B', start: 0, trackId: 'nest-track', clipId: 'nest-clip' };
  const p = sequences.selectProjectSequence(project(), 'A');
  const next = sequences.insertNestedSequence(p, options);
  assert.equal(next.sequences[0].tracks.length, 2); assert.deepEqual(plain(next.tracks), plain(p.tracks));
  p.sequences[0].tracks[0].solo = true; assert.throws(() => sequences.insertNestedSequence(p, options), /独奏/);
  p.sequences[0].tracks[0].solo = false; p.sequences[1].tracks = []; assert.throws(() => sequences.insertNestedSequence(p, options), /空/);
  const full = project(); full.tracks = Array.from({ length: 8 }, (_, i) => lane(`full-${i}`)); assert.throws(() => sequences.insertNestedSequence(full, options), /8/);
});

test('workspace FPS changes insertion proposals only, not child duration or source marks', () => {
  for (const fps of [24, 25, 30, 60]) {
    const p = project(); p.workspace.timeline_fps = fps;
    const proposed = timeline.proposeTimelineTime(1.025, fps, false, timeline.collectSnapTargets(sequences.activeProject(p)));
    const next = sequences.insertNestedSequence(p, { sequenceId: 'A', start: proposed.seconds, trackId: 'nested-lane', clipId: 'nested' });
    assert.equal(next.tracks[1].clips[0].start, proposed.frame / fps); assert.equal(next.tracks[1].clips[0].duration, 3);
    assert.deepEqual(plain(next.workspace.source_marks), plain(p.workspace.source_marks));
  }
  const p = sequences.selectProjectSequence(project(), 'A'), targets = timeline.collectSnapTargets(sequences.activeProject(p));
  assert.ok(targets.some(item => item.id === 'a-marker')); assert.equal(targets.some(item => item.id === 'root-marker'), false);
});

test('same-duration child edits need no parent rewrite; end changes remain invalid until explicit binding proposal', () => {
  const p = sequences.selectProjectSequence(nestedProject(), 'B');
  const color = sequences.editActiveProject(p, working => { working.tracks[0].clips[0].brightness = 0.5; });
  assert.doesNotThrow(() => api.normalizeProject(color)); assert.equal(sequences.proposeSequenceDurationBindings(color).changes.length, 0);
  const changed = sequences.editActiveProject(color, working => { working.tracks[0].clips[0].duration = 4; });
  const before = JSON.stringify(changed); assert.throws(() => api.normalizeProject(changed));
  const proposal = sequences.proposeSequenceDurationBindings(changed);
  assert.equal(JSON.stringify(changed), before); assert.equal(proposal.changes.length, 2);
  assert.equal(proposal.project.tracks[0].clips[0].duration, 6); assert.equal(proposal.project.sequences[0].tracks[0].clips[0].duration, 4);
  assert.equal(proposal.project.sequences[0].tracks[0].clips[0].start, 2); assert.equal(proposal.project.sequences[1].tracks[0].clips[0].brightness, 0.5);
  assert.equal(sequences.proposeSequenceDurationBindings(proposal.project).changes.length, 0);
});

test('duration proposal updates every repeated/inactive/hidden reference, never child source time or parent start', () => {
  const p = nestedProject(); p.tracks.push(lane('hidden-parent', [nested('hidden-ref', 'B', 3, 15)], 'overlay', { hidden: true }));
  p.sequences.push(sequence('C', [lane('inactive-parent', [nested('inactive-ref', 'B', 3, 10)])]));
  let next = sequences.selectProjectSequence(api.normalizeProject(p), 'B');
  for (const value of [4, 2, 5]) {
    const draft = sequences.editActiveProject(next, working => { working.tracks[0].clips[0].duration = value; });
    const proposal = sequences.proposeSequenceDurationBindings(draft); assert.equal(proposal.changes.length, 4);
    for (const scope of sequences.projectScopes(proposal.project)) for (const track of scope.tracks) for (const c of track.clips) {
      if (c.sequence_id === 'B') assert.equal(c.duration, value);
    }
    assert.equal(proposal.project.tracks[1].clips[0].start, 15); assert.equal(proposal.project.sequences[2].tracks[0].clips[0].start, 10);
    assert.equal(proposal.project.sequences[1].tracks[0].clips[0].trim, 0); next = proposal.project;
  }
});

test('binding proposals refuse locked parents, empty children, overflow and overlap, retaining the input draft', () => {
  for (const mutate of [p => { p.tracks[0].locked = true; }, p => { p.sequences[1].tracks = []; },
    p => { p.tracks[0].clips[0].start = 115; }, p => { p.sequences[0].tracks[0].clips.push(clip('next', { start: 5, duration: 1 })); }]) {
    const p = nestedProject(); p.sequences[1].tracks[0].clips[0].duration = 4; mutate(p);
    const before = JSON.stringify(p); assert.throws(() => sequences.proposeSequenceDurationBindings(p)); assert.equal(JSON.stringify(p), before);
  }
});

test('locked tracks cannot move containers, reorder, unlock or change through active draft mapping', () => {
  const p = sequences.selectProjectSequence(project(), 'A'); p.sequences[0].tracks[0].locked = true;
  for (const change of [working => { working.tracks[0].clips[0].brightness = 0.4; }, working => { working.tracks = []; },
    working => { working.tracks[0].locked = false; }, working => { working.tracks.unshift(lane('new')); }]) {
    assert.throws(() => sequences.editActiveProject(p, change), /锁定/);
  }
  assert.doesNotThrow(() => sequences.editActiveProject(p, working => { working.workspace.timeline_zoom = 2; }));
});

test('hidden locked parent protects grandchild content but not source marks or local marker metadata', () => {
  const p = nestedProject(); p.tracks[0].locked = true; p.tracks[0].hidden = true;
  const active = sequences.selectProjectSequence(p, 'B'); assert.match(sequences.sequenceContentLock(active), /父轨道/);
  assert.throws(() => sequences.editActiveProject(active, working => { working.tracks[0].clips[0].brightness = 0.5; }), /父轨道/);
  assert.doesNotThrow(() => sequences.editActiveProject(active, working => { working.workspace.timeline_fps = 25; working.markers.push({ id: 'm', time: 1, label: '' }); }));
  assert.throws(() => sequences.captureSequenceClipboard(active, TASK, 'b-leaf', false), /父轨道/);
});

test('group removal updates all scopes, and an inactive locked member prevents partial removal', () => {
  const p = project(); p.tracks[0].group_id = 'group'; p.sequences[1].tracks[0].group_id = 'group';
  const next = sequences.removeProjectGroup(p, 'group'); assert.equal(next.groups.length, 0);
  assert.equal(sequences.allProjectTracks(next).some(track => track.group_id), false); assert.equal(p.groups.length, 1);
  p.sequences[1].tracks[0].locked = true; assert.throws(() => sequences.removeProjectGroup(p, 'group'));
});

test('cross-sequence copy preserves complete parameters and creates a globally new ID; original stays', () => {
  const p = project(); p.tracks[0].clips[0].lut_id = LUT;
  p.tracks[0].clips[0].keyframes.x = [{ time: 0, value: 0, easing: 'linear' }, { time: 1, value: 0.1, easing: 'ease_in' }];
  const clipboard = sequences.captureSequenceClipboard(p, TASK, 'root-clip', false);
  const selected = sequences.selectProjectSequence(p, 'A'), before = JSON.stringify(selected);
  const next = sequences.pasteSequenceClipboard(selected, clipboard, { taskId: TASK, trackId: 'a-track', start: 0.4, newClipId: 'copy', sourceIds: ['original'], lutIds: [LUT] });
  const pasted = next.sequences[0].tracks[0].clips[1];
  assert.deepEqual(plain(pasted), { ...plain(clipboard.clip), id: 'copy', start: 0.4 });
  assert.deepEqual(plain(next.tracks), plain(p.tracks)); assert.equal(JSON.stringify(selected), before); assert.equal(sequences.projectClipCount(next), 4);
  pasted.keyframes.x[1].value = 0.7; assert.equal(clipboard.clip.keyframes.x[1].value, 0.1);
});

test('nested copy keeps child reference, refuses cycles or stale child duration instead of repairing clipboard', () => {
  const p = nestedProject(), clipboard = sequences.captureSequenceClipboard(p, TASK, 'parent-a', false);
  const next = sequences.pasteSequenceClipboard(p, clipboard, { taskId: TASK, trackId: 'parent', start: 6, newClipId: 'copy' });
  assert.equal(next.tracks[0].clips[1].sequence_id, 'A'); assert.equal(next.tracks[0].clips[1].source_id, null);
  const active = sequences.selectProjectSequence(p, 'A');
  assert.throws(() => sequences.pasteSequenceClipboard(active, clipboard, { taskId: TASK, trackId: 'a-parent', start: 6, newClipId: 'cycle' }));
  const changed = plain(p); changed.sequences[1].tracks[0].clips[0].duration = 4;
  const rebound = sequences.proposeSequenceDurationBindings(changed).project;
  assert.throws(() => sequences.pasteSequenceClipboard(rebound, clipboard, { taskId: TASK, trackId: 'parent', start: 8, newClipId: 'stale-copy' }), /duration/);
});

test('cut never deletes origin across sequences; same-scope move preserves ID, ordering and other clips', () => {
  const p = project(); p.tracks[0].clips.push(clip('other', { start: 8 }));
  const clipboard = sequences.captureSequenceClipboard(p, TASK, 'root-clip', true), before = JSON.stringify(p);
  const target = sequences.selectProjectSequence(p, 'A');
  assert.throws(() => sequences.pasteSequenceClipboard(target, clipboard, { taskId: TASK, trackId: 'a-track', start: 4 }), /跨序列剪切/);
  const next = sequences.pasteSequenceClipboard(p, clipboard, { taskId: TASK, trackId: 'root-track', start: 4 });
  assert.equal(JSON.stringify(p), before); assert.equal(next.tracks[0].clips[0].id, 'root-clip'); assert.equal(next.tracks[0].clips[0].start, 4);
  assert.equal(next.tracks[0].clips[1].id, 'other'); assert.equal(next.tracks[0].clips[1].start, 8); assert.equal(sequences.projectClipCount(next), 4);
});

test('clipboard checks task, both-scope locks, target type, source/LUT membership, stale cut and global IDs', () => {
  const p = project(), clipboard = sequences.captureSequenceClipboard(p, TASK, 'root-clip', false);
  const selected = sequences.selectProjectSequence(p, 'A');
  const options = { taskId: TASK, trackId: 'a-track', start: 4, newClipId: 'copy' };
  for (const patch of [{ taskId: 'other-task' }, { trackId: 'root-track' }, { newClipId: 'a-marker' }, { sourceIds: [] }, { start: 120 }]) {
    assert.throws(() => sequences.pasteSequenceClipboard(selected, clipboard, { ...options, ...patch }));
  }
  for (const which of ['origin', 'target']) { const locked = plain(selected); (which === 'origin' ? locked.tracks[0] : locked.sequences[0].tracks[0]).locked = true; assert.throws(() => sequences.pasteSequenceClipboard(locked, clipboard, options)); }
  const withLut = { ...clipboard, clip: { ...clipboard.clip, lut_id: LUT } }; assert.throws(() => sequences.pasteSequenceClipboard(selected, withLut, { ...options, lutIds: [] }));
  const cut = sequences.captureSequenceClipboard(p, TASK, 'root-clip', true); p.tracks[0].clips[0].trim = 0.4;
  assert.throws(() => sequences.pasteSequenceClipboard(p, cut, { taskId: TASK, trackId: 'root-track', start: 4 }), /变更/);
});

test('expanded resource counts charge repeated child images and LUT bindings but not LUT decoders', () => {
  const image = source({ id: IMAGE, name: 'image.png', is_image: true, has_audio: false });
  const child = sequence('A', [lane('image-track', [api.makeSourceClip(image, { id: 'image-clip', duration: 3, lut_id: LUT })], 'overlay'),
    lane('text-track', [clip('text', { source_id: null, text: 'Always above media' })], 'text'), lane('empty', [])]);
  const p = normalize({ tracks: [lane('parent', [nested('ref1', 'A', 3, 0), nested('ref2', 'A', 3, 4)])], sequences: [child] });
  const stats = api.sequenceRenderStats(p);
  assert.equal(stats.tracks, 4); assert.equal(stats.clips, 4); assert.equal(stats.inputs, 2); assert.equal(stats.imageInputs, 2);
  assert.equal(stats.lutBindings, 2); assert.equal(stats.nestedText, true); assert.deepEqual(plain(stats.sourceIds), [IMAGE]); assert.deepEqual(plain(stats.lutIds), [LUT]);
  assert.deepEqual(plain(api.projectWarnings(p, { [IMAGE]: 0.001 }, [image], [{ id: LUT }])), []);
  assert.ok(api.projectWarnings(p, {}, [image], []).some(message => message.includes('LUT')));
});

test('expanded layers, expanded clips and decoder inputs have independent fail-closed limits', () => {
  const repeated = (leaves, type = 'video', lanes = 1) => normalize({ tracks: [lane('parent', [nested('one', 'A', 1), nested('two', 'A', 1, 2)])],
    sequences: [sequence('A', Array.from({ length: lanes }, (_, i) => lane(`child-${i}`,
      Array.from({ length: leaves }, (_, j) => clip(`leaf-${i}-${j}`, { duration: 1, ...(type === 'text' ? { source_id: null, text: 'Text' } : {}) })), type)))] });
  const decoder = repeated(9); assert.equal(api.sequenceRenderStats(decoder).inputs, 18); assert.throws(() => sequences.assertSequenceRenderBudget(decoder));
  const layers = repeated(1, 'text', 5); assert.equal(api.sequenceRenderStats(layers).tracks, 10); assert.throws(() => sequences.assertSequenceRenderBudget(layers));
  const clips = repeated(33, 'text'); assert.equal(api.sequenceRenderStats(clips).clips, 66); assert.throws(() => sequences.assertSequenceRenderBudget(clips));
  const edge = repeated(8); assert.equal(api.sequenceRenderStats(edge).inputs, 16); assert.doesNotThrow(() => sequences.assertSequenceRenderBudget(edge));
});

test('active render resource prediction resolves solo locally and inactive invalid source/LUT references still warn', () => {
  const p = project(); p.tracks[0].clips[0].source_id = 'unknown-inactive'; p.sequences[1].tracks[0].clips[0].lut_id = LUT;
  p.active_sequence_id = 'A'; const stats = api.sequenceRenderStats(p); assert.equal(stats.inputs, 1); assert.deepEqual(plain(stats.sourceIds), ['original']);
  const warnings = api.projectWarnings(p, {}, [source()], []); assert.ok(warnings.some(message => message.includes('source_id'))); assert.ok(warnings.some(message => message.includes('LUT')));
  const nestedP = nestedProject(); nestedP.sequences[1].tracks.push(lane('solo', [clip('solo-clip', { duration: 3 })], 'video', { solo: true }));
  assert.equal(api.sequenceRenderStats(nestedP).inputs, 1);
  const audioFinal = normalize({ tracks: [lane('a', [clip('f', { source_id: 'final' })], 'audio')] });
  assert.deepEqual(plain(api.sequenceRenderStats(audioFinal).visualSourceIds), []);
});

test('API project/save/import/backup/undo receipts preserve the entire schema and actual revisions', async () => {
  const p = sequences.selectProjectSequence(project(), 'A'), calls = [];
  const client = api.createStudioApi(TASK, async (path, init) => { calls.push({ path, init }); return envelope(p, 43); });
  for (const result of [await client.project(), await client.backup(), await client.save(7, p), await client.importProject(8, p), await client.action(9, { op: 'undo' })]) {
    assert.equal(result.revision, 43); assert.deepEqual(plain(result.project), plain(p));
  }
  for (const call of calls.filter(row => row.path === `${ROOT}/project/import` || (row.path === `${ROOT}/project` && row.init.method === 'POST'))) {
    const body = JSON.parse(call.init.body); assert.deepEqual(body.project, plain(p)); assert.equal(body.project.tracks[0].id, 'root-track');
  }
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { expected_revision: 9, op: 'undo' });
});

test('unknown/imported sequence content is rejected before transport rather than silently stripped', async () => {
  let calls = 0; const client = api.createStudioApi(TASK, async () => { calls++; return envelope(project()); });
  const bad = project(); bad.sequences[0].tracks[0].clips[0].unknown = true;
  await assert.rejects(client.save(7, bad)); await assert.rejects(client.importProject(7, bad)); assert.equal(calls, 0);
  assert.throws(() => api.parseProjectImport(JSON.stringify(bad)));
  const incompatible = api.createStudioApi(TASK, async () => envelope({ ...project(), active_sequence_id: 'missing' }));
  await assert.rejects(incompatible.project());
});

test('actual switch handler saves complete draft first, then uses returned revision without optimistic selection', async () => {
  const p = project(), before = JSON.stringify(p), h = operationHarness(p, { dirty: true });
  h.switchSequence('A'); await Promise.all(h.pending);
  const saves = h.calls.filter(row => row.path === `${ROOT}/project`); assert.equal(saves.length, 2);
  assert.equal(JSON.parse(saves[0].init.body).expected_revision, 7); assert.equal(JSON.parse(saves[1].init.body).expected_revision, 41);
  assert.equal(JSON.parse(saves[0].init.body).project.active_sequence_id, null); assert.equal(JSON.parse(saves[1].init.body).project.active_sequence_id, 'A');
  assert.equal(h.receipts.length, 2); assert.equal(h.receipts[1].revision, 59); assert.equal(h.errors.length, 0); assert.equal(JSON.stringify(p), before);
  assert.deepEqual(JSON.parse(saves[1].init.body).project.tracks, plain(p.tracks));
});

test('actual switch conflict, disappeared target, and unmounted editor never retry or switch locally', async () => {
  const p = project();
  for (const target of ['A', 'missing']) {
    const h = operationHarness(p, { dirty: true, respond: () => { throw new Error('Synthetic conflict'); } });
    h.switchSequence(target); await Promise.all(h.pending);
    assert.equal(h.calls.length, target === 'A' ? 1 : 0); assert.equal(h.receipts.length, 0); assert.equal(h.errors.length, 1); assert.equal(p.active_sequence_id, null);
  }
  const h = operationHarness(p, { bindings: { saveDraft: async () => envelope(p, 41) } });
  h.mounted.current = false; h.switchSequence('A'); await Promise.all(h.pending); assert.equal(h.calls.length, 0);
});

test('actual common mutex excludes overlapping sequence switches without extra history mutations', async () => {
  let release; const held = new Promise(resolve => { release = resolve; });
  const p = project(), h = operationHarness(p, { respond: async (path, init) => {
    if (path === `${ROOT}/audit`) return { records: [] };
    await held; return envelope(JSON.parse(init.body).project, 43);
  } });
  h.switchSequence('A'); assert.equal(h.mutex.current, true); h.switchSequence('B');
  release(); await Promise.all(h.pending); assert.equal(h.calls.filter(row => row.path === `${ROOT}/project`).length, 1);
  assert.equal(h.receipts.at(-1).project.active_sequence_id, 'A');
});

test('saved over-budget main can navigate to its child for repair without resaving or dropping main', async () => {
  const p = normalize({ tracks: [lane('parent', [nested('one', 'A', 1), nested('two', 'A', 1, 2)])],
    sequences: [sequence('A', Array.from({ length: 5 }, (_, i) => lane(`child-${i}`, [clip(`leaf-${i}`, { duration: 1 })])))] });
  assert.equal(api.sequenceRenderStats(p).tracks, 10); assert.ok(api.projectWarnings(p).length);
  const h = operationHarness(p); h.switchSequence('A'); await Promise.all(h.pending);
  assert.equal(h.errors.length, 0); assert.equal(h.calls.filter(row => row.path === `${ROOT}/project`).length, 1);
  assert.deepEqual(plain(h.receipts[0].project.tracks), plain(p.tracks)); assert.equal(h.receipts[0].project.active_sequence_id, 'A');
});

test('actual create handler saves a real empty sequence and no cloned root clips', async () => {
  const p = project(), h = operationHarness(p); h.addSequence(); await Promise.all(h.pending);
  assert.equal(h.errors.length, 0); const next = h.receipts.at(-1).project;
  assert.equal(next.sequences.length, 3); assert.equal(sequences.projectScope(next).tracks.length, 0);
  assert.deepEqual(plain(next.tracks), plain(p.tracks)); assert.equal(sequences.projectClipCount(next), sequences.projectClipCount(p));
});

test('actual nested insertion requires confirmation, saves real full document and preserves position', async () => {
  const p = project(), cancelled = operationHarness(p, { confirm: () => false });
  cancelled.insertSequence(); await Promise.all(cancelled.pending); assert.equal(cancelled.calls.length, 0);
  const h = operationHarness(p); h.insertSequence(); await Promise.all(h.pending);
  assert.equal(h.errors.length, 0); const next = h.receipts.at(-1).project;
  assert.equal(next.tracks.length, 2); assert.equal(next.tracks[1].clips[0].sequence_id, 'A'); assert.equal(next.tracks[1].clips[0].start, 4);
  assert.deepEqual(plain(next.sequences), plain(p.sequences));
});

test('actual explicit binding handler bypasses invalid draft save and commits child plus all parents once', async () => {
  const p = sequences.selectProjectSequence(nestedProject(), 'B');
  const draft = sequences.editActiveProject(p, working => { working.tracks[0].clips[0].duration = 4; });
  const proposal = sequences.proposeSequenceDurationBindings(draft), before = JSON.stringify(draft);
  const cancelled = operationHarness(draft, { bindingProposal: proposal, confirm: () => false });
  cancelled.confirmDurationBindings(); await Promise.all(cancelled.pending); assert.equal(cancelled.calls.length, 0);
  const h = operationHarness(draft, { bindingProposal: proposal, dirty: true });
  h.confirmDurationBindings(); await Promise.all(h.pending); assert.equal(h.errors.length, 0);
  const saves = h.calls.filter(row => row.path === `${ROOT}/project`); assert.equal(saves.length, 1);
  assert.equal(JSON.parse(saves[0].init.body).expected_revision, 7); assert.deepEqual(JSON.parse(saves[0].init.body).project, plain(proposal.project));
  assert.equal(JSON.stringify(draft), before); assert.equal(h.receipts.length, 1);
});

test('actual nested action guard allows only move/delete and checks the active clip scope', async () => {
  const p = nestedProject(), h = operationHarness(p);
  h.action({ op: 'split', clip_id: 'parent-a', at: 1, new_id: 'split' }); await Promise.all(h.pending);
  assert.equal(h.calls.length, 0); assert.equal(h.errors.length, 1);
  const active = sequences.selectProjectSequence(project(), 'A'), other = operationHarness(active);
  other.action({ op: 'delete', clip_id: 'root-clip' }); await Promise.all(other.pending);
  assert.equal(other.calls.length, 0); assert.equal(other.errors.length, 1);
});

test('actual editor draft mapper and save retain both roots while mapping child edits back', async () => {
  const p = sequences.selectProjectSequence(project(), 'A'), updates = [], errors = [];
  const editor = editorStatements(['edit'], { project: p, mutex: { current: false }, setProject: value => updates.push(value), reportError: error => errors.push(error) });
  assert.equal(editor.edit(working => { working.tracks[0].clips[0].brightness = 0.7; }), true);
  assert.deepEqual(plain(updates[0].tracks), plain(p.tracks)); assert.equal(updates[0].sequences[0].tracks[0].clips[0].brightness, 0.7); assert.equal(errors.length, 0);
  const h = operationHarness(updates[0], { dirty: true }); await h.base.saveDraft();
  assert.deepEqual(JSON.parse(h.calls[0].init.body).project, plain(updates[0]));
});

test('actual remember receipt refreshes active selection and retains full snapshots, not a child-only history', () => {
  const p = sequences.selectProjectSequence(project(), 'A'), receipt = envelope(p, 43);
  const state = { project: null, saved: null, trackId: 'root-track', clipId: 'root-clip', moveTrack: 'root-track', snapshots: [] };
  const setter = key => value => { state[key] = typeof value === 'function' ? value(state[key]) : value; };
  const editor = editorStatements(['remember'], { mounted: { current: true }, clone: plain,
    setProject: setter('project'), setSaved: setter('saved'), setTrackId: setter('trackId'), setClipId: setter('clipId'),
    setMoveTrack: setter('moveTrack'), setSnapshots: setter('snapshots') });
  editor.remember(receipt);
  assert.equal(state.trackId, 'a-track'); assert.equal(state.clipId, ''); assert.equal(state.moveTrack, '');
  assert.deepEqual(state.project, plain(p)); assert.deepEqual(plain(state.snapshots[0]), plain(receipt));
  state.project.sequences[0].tracks[0].clips[0].brightness = 0.9;
  assert.equal(receipt.project.sequences[0].tracks[0].clips[0].brightness, 0);
});

test('actual active action POST uses the saved child selection and receipt revision, never root substitution', async () => {
  const p = sequences.selectProjectSequence(project(), 'A'), after = sequences.editActiveProject(p, working => { working.tracks[0].clips[0].start = 1; });
  const h = operationHarness(p, { dirty: true, respond: (path, init) => {
    if (path === `${ROOT}/audit`) return { records: [] };
    return path === `${ROOT}/project` ? envelope(JSON.parse(init.body).project, 41) : envelope(after, 59);
  } });
  h.action({ op: 'move', clip_id: 'a-clip', at: 1, track_id: 'a-track' }); await Promise.all(h.pending);
  assert.equal(h.errors.length, 0);
  const action = h.calls.find(row => row.path === `${ROOT}/actions`);
  assert.deepEqual(JSON.parse(action.init.body), { expected_revision: 41, op: 'move', clip_id: 'a-clip', at: 1, track_id: 'a-track' });
  assert.deepEqual(plain(h.receipts.at(-1).project.tracks), plain(p.tracks)); assert.equal(h.receipts.at(-1).project.active_sequence_id, 'A');
});

test('actual clipboard handler rejects cross-sequence cut before save/POST; copy saves full document', async () => {
  const original = project(), p = sequences.selectProjectSequence(original, 'A');
  for (const cut of [true, false]) {
    const h = operationHarness(p), pending = [], clipboard = sequences.captureSequenceClipboard(original, TASK, 'root-clip', cut);
    const editor = editorStatements(['pasteClip'], { project: p, catalog: catalog(), clipClipboard: clipboard,
      frozen: false, selectedTrack: sequences.projectScope(p).tracks[0], supports: () => true,
      insertAt: 4, timelineFPS: 25, snapActive: false, durations: {}, taskId: TASK,
      assetState: { status: 'ready', catalog: { luts: [] } }, mounted: h.mounted,
      api: api.createStudioApi(TASK, async (path, init) => { h.calls.push({ path, init }); return envelope(JSON.parse(init.body).project, 59); }),
      operation: (label, run) => { const result = h.base.operation(label, run); pending.push(result); return result; },
      saveDraft: async () => envelope(p, 41), remember: value => h.receipts.push(value), displayTime: String,
      window: { confirm: () => true }, setClipClipboard() {}, pickClip() {}, setNotice() {}, refreshAudit: async () => {} });
    editor.pasteClip(); await Promise.all(pending);
    if (cut) { assert.equal(h.calls.length, 0); assert.equal(h.errors.length, 1); }
    else {
      assert.equal(h.errors.length, 0); assert.equal(h.calls.length, 1);
      const body = JSON.parse(h.calls[0].init.body); assert.equal(body.expected_revision, 41); assert.deepEqual(body.project.tracks, plain(p.tracks));
      assert.equal(body.project.sequences[0].tracks[0].clips.length, 2); assert.equal(body.project.active_sequence_id, 'A');
    }
  }
});

test('loaded actual Studio shows only active track/marker controls while backups keep full data', () => {
  const p = sequences.selectProjectSequence(project(), 'A'), html = renderStudio(p, { clipId: 'a-clip' });
  assert.match(html, /data-active-sequence="A"/); assert.match(html, /data-studio-section="sequences"/);
  assert.match(html, /data-clip-id="a-clip"/); assert.equal(html.includes('data-clip-id="root-clip"'), false); assert.equal(html.includes('data-clip-id="b-clip"'), false);
  assert.match(html, /跳到标记 A only/); assert.equal(html.includes('跳到标记 Main only'), false);
  assert.match(html, /插入完整嵌套（有界）/); assert.match(html, /新建空序列并打开/); assert.match(html, /全部 sequences/);
  assert.equal(/autoplay|<iframe/.test(html), false);
});

test('loaded nested inspector has open/move/delete but no source/trim/FX/audio masquerading as parent edits', () => {
  const html = renderStudio(nestedProject(), { clipId: 'parent-a', trackId: 'parent' });
  assert.match(html, /data-studio-section="nested-inspector"/); assert.match(html, /打开所选嵌套/);
  assert.match(html, /data-sequence-id="A"/); assert.match(html, /双击打开/);
  for (const label of ['片段源素材', '输出时长（秒）', '画面缩放', '音量（0–2 倍）']) assert.equal(html.includes(`aria-label="${label}"`), false);
  assert.match(html, /disabled=""[^>]*data-action="split"/); assert.match(html, /data-action="delete"/);
});

test('main-only composition restriction is visible and child builders are not mounted', () => {
  const p = sequences.selectProjectSequence(project(), 'A');
  for (const tab of ['fx', 'cam']) {
    const html = renderStudio(p, { tab }); assert.match(html, /构建器明确停用/);
    assert.equal(html.includes('data-multicam-apply'), false); assert.equal(html.includes('data-transition-apply'), false);
  }
});

test('invalid duration binding has explicit confirmation UI and no implicit default repair', () => {
  const p = sequences.selectProjectSequence(nestedProject(), 'B'), saved = envelope(p);
  const draft = sequences.editActiveProject(p, working => { working.tracks[0].clips[0].duration = 4; });
  const html = renderStudio(draft, { saved }); assert.match(html, /data-duration-bindings/);
  assert.match(html, /确认全部父时长绑定并保存（单次事务）/); assert.match(html, /绝未自动写入/);
  assert.match(html, /放弃草稿，恢复已保存完整工程/);
});

test('new helpers have no hidden IO/timers/random IDs and stable integration declarations remain extractable', () => {
  assert.doesNotMatch(sequencesSource, /\b(?:fetch|XMLHttpRequest|WebSocket|setTimeout|setInterval)\s*\(/);
  assert.doesNotMatch(sequencesSource, /\b(?:Date\.now|Math\.random|crypto\.randomUUID)\s*\(/);
  for (const name of ['operation', 'saveDraft', 'uploadAsset', 'refreshAssetCatalog', 'useLut', 'chooseSource', 'addClip', 'startProxy', 'cancelJob']) {
    assert.equal(typeof editorStatements([name], { operation() {} })[name], 'function');
  }
  for (const section of ['sequences', 'timeline', 'clipboard', 'source', 'proxy', 'assets', 'backup', 'history']) {
    assert.ok(studioSource.includes(`data-studio-section="${section}"`) || (section === 'proxy' && studioSource.includes('<StudioProxy')) || (section === 'assets' && studioSource.includes('<StudioAssets')));
  }
});