import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Node-only contracts. Same actual-module
// transpilation pattern as test-timeline-editing.mjs. No processes, network,
// providers, storage or production writes. These are client/schema proposals,
// not React, server-history, ffprobe, FFmpeg or rendered-pixel acceptance.
function loadModule(relativePath, dependencies = {}) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    fileName: relativePath, reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, sourceMap: false },
    transformers: { before: [context => root => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
        && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
        ? ts.factory.createObjectLiteralExpression([])
        : ts.visitEachChild(node, visit, context);
      return ts.visitNode(root, visit);
    }] },
  });
  assert.equal(compiled.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, TextEncoder, Headers,
    crypto: { randomUUID() { throw new Error('Pure proposals must not allocate random IDs.'); } },
    require(name) {
      if (!Object.hasOwn(dependencies, name)) throw new Error(`Unexpected runtime dependency: ${name}`);
      return dependencies[name];
    },
  });
  new vm.Script(compiled.outputText, { filename: relativePath }).runInContext(context, { timeout: 1000 });
  return module.exports;
}
const api = loadModule('../src/lib/studioApi.ts');
const timeline = loadModule('../src/lib/timelineEditing.ts');
const compositions = loadModule('../src/lib/studioCompositions.ts', { './studioApi': api, './timelineEditing': timeline });
const plain = value => JSON.parse(JSON.stringify(value));
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-11, `${actual} != ${expected}`);
function frozen(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(frozen); Object.freeze(value); }
  return value;
}
const source = (id, patch = {}) => ({ id, name: `${id}.mp4`, bytes: 1000, url: '/not-opened', burned_subtitles: false,
  duration: 240, has_video: true, has_audio: true, ...patch });
const sources = ['cam_a', 'cam_b', 'cam_c', 'cam_d'].map(id => source(id));
sources.push(source('master', { name: 'master.wav', has_video: false }));
const context = (patch = {}) => ({ sources, expectedRevision: 7, ...patch });
const clip = (id, start = 0, duration = 4, patch = {}) => api.makeClip({ id, source_id: 'cam_a', start, duration,
  trim: 0.123456789, fit: 'cover', ...patch });
const track = (id, clips = [], patch = {}) => ({ id, type: 'video', name: id, locked: false, hidden: false,
  solo: false, color: 'gold', group_id: null, clips, ...patch });
const project = (tracks = []) => api.normalizeProject({ schema_version: 1, name: 'Composition client unit', tracks,
  groups: [{ id: 'group-real', name: 'Retained' }], markers: [{ id: 'marker-real', time: 9.0125, label: 'Unmoved' }],
  assets: [{ source_id: 'cam_a', rating: 4, tags: ['kept'] }], workspace: { ...api.makeWorkspace(), timeline_fps: 25,
    time_display: 'frames', snap_enabled: false, ripple_enabled: true, layout: 'editing', timeline_zoom: 1.75,
    shortcuts: { undo: 'Alt+Z', split: 'X' }, source_marks: [{ source_id: 'cam_a', in_point: 0.123456789, out_point: 4.012345678 }] } });
const timelineProject = () => project([track('v1', [clip('a'), clip('b', 4), clip('c', 8)])]);
const boundary = { trackId: 'v1', leftClipId: 'a', rightClipId: 'b' };
const transition = (patch = {}) => ({ kind: 'dissolve', duration: 0.5, easing: 'linear', audio: true, ...patch });
const add = (p = timelineProject(), patch = {}, ctx = context()) => compositions.buildTransitionProposal(p, boundary, transition(patch), ctx);
const gridRecipe = (patch = {}) => ({ mode: 'grid', placement: 'append', duration: 6,
  angles: [{ source_id: 'cam_a', sync_in: 0.123456789 }, { source_id: 'cam_b', sync_in: 1.125 }],
  cuts: [], master: { source_id: 'master', sync_in: 0.75 }, ...patch });
const switchRecipe = (patch = {}) => gridRecipe({ mode: 'switch',
  cuts: [{ start: 0, angle: 2 }, { start: 1.5, angle: 1 }, { start: 3.125, angle: 2 }], ...patch });
const compose = (recipe = gridRecipe(), p = project(), ctx = context()) => compositions.buildMulticamProject(p, recipe, ctx);

test('legacy defaults and strict transition fields survive import without dropping any known field', () => {
  const old = api.parseProjectImport(JSON.stringify({ schema_version: 1, tracks: [{ id: 'v', type: 'video',
    clips: [{ id: 'old', source_id: 'cam_a', start: 0.0125, trim: 0.123456789, duration: 2.013 }] }] }));
  assert.equal(old.tracks[0].clips[0].transition_in, null);
  assert.equal(old.tracks[0].clips[0].start, 0.0125); assert.equal(old.tracks[0].clips[0].trim, 0.123456789);
  assert.equal(old.tracks[0].clips[0].duration, 2.013);
  assert.equal(api.normalizeTransition(undefined), null); assert.equal(api.normalizeTransition(null), null);
  assert.deepEqual(plain(api.normalizeTransition({ left_clip_id: 'a', kind: 'dissolve', duration: 0.5 })),
    { left_clip_id: 'a', kind: 'dissolve', duration: 0.5, easing: 'linear', audio: true });
  const p = add().project, raw = JSON.stringify(p);
  assert.deepEqual(plain(api.parseProjectImport(raw)), plain(p));
  assert.equal(JSON.stringify(p), raw);
  for (const bad of [{ kind: 'custom' }, { duration: 0 }, { duration: -1 }, { duration: NaN }, { duration: Infinity },
    { duration: '0.5' }, { duration: true }, { duration: 1.20001 }, { easing: 'expression' }, { easing: null },
    { audio: 'true' }, { audio: 1 }, { audio: null }, { left_clip_id: '../source' }, { extra_filter: 'not allowed' }]) {
    assert.throws(() => api.normalizeTransition({ ...transition(), left_clip_id: 'a', ...bad }));
  }
  for (const bad of [false, [], 'wipe_left']) assert.throws(() => api.normalizeTransition(bad));
});

test('explicit transition shifts only right and same-track successors, retaining list order and all authored fields', () => {
  const rich = clip('a', 0, 4, { speed: 1.25, reverse: true, rotation: 90, mirror: true, flip: true,
    crop: { x: 0.1, y: 0.2, width: 0.8, height: 0.7 }, brightness: 0.1, contrast: 1.2, saturation: 0.8,
    temperature: 0.4, hue: -70, shadows: 0.2, highlights: -0.2, fade_amount: 0.3, color_preset: 'cinema',
    rgb_curves: { red: [{ x: 0, y: 1 }, { x: 0.4, y: 0.6 }, { x: 1, y: 0 }] }, sharpen: 0.2, noise: 3,
    volume: 0.7, pan: -0.2, audio_effect: 'reverb', bass_db: 2, treble_db: -3 });
  const tail = clip('c', 8, 4, { mask: { type: 'ellipse', x: 0.4, y: 0.6, width: 0.7, height: 0.8, feather: 0.1, invert: true },
    keyframes: { x: [{ time: 0, value: 0, easing: 'ease_in_out' }, { time: 2, value: 0.2, easing: 'ease_out' }], y: [], scale: [], opacity: [] } });
  const p = project([track('v1', [tail, rich, clip('b', 4)], { group_id: 'group-real' }),
    track('audio', [api.makeClip({ id: 'music', source_id: 'master', duration: 22 })], { type: 'audio', locked: true }),
    track('text', [api.makeClip({ id: 'title', duration: 3, text: 'Retained', subtitle: false, text_animation: 'pop',
      text_template: 'gold', color: '123456', font_size: 46, bold: true, outline: 3, shadow: 2, background: true })], { type: 'text' })]);
  const original = JSON.stringify(p); frozen(p);
  const proposed = add(p), expected = plain(p);
  expected.tracks[0].clips.find(c => c.id === 'b').start = 3.5;
  expected.tracks[0].clips.find(c => c.id === 'b').transition_in = { left_clip_id: 'a', ...transition() };
  expected.tracks[0].clips.find(c => c.id === 'c').start = 7.5;
  assert.deepEqual(plain(proposed.project), expected);
  assert.deepEqual(plain(proposed.movements), [{ clipId: 'b', before: 4, after: 3.5 }, { clipId: 'c', before: 8, after: 7.5 }]);
  assert.equal(proposed.shiftSeconds, -0.5); assert.equal(proposed.stats.end, 22); assert.equal(proposed.beforeEnd, 22);
  assert.equal(JSON.stringify(p), original);
  proposed.project.tracks[0].clips[1].rgb_curves.red[1].y = 0.01;
  assert.equal(p.tracks[0].clips[1].rgb_curves.red[1].y, 0.6, 'Proposal is independently detached.');
});

test('all five transition kinds, four easing modes and both audio policies serialize without fake clip fades', () => {
  for (const kind of api.TRANSITION_KINDS) for (const easing of ['linear', 'ease_in', 'ease_out', 'ease_in_out']) for (const audio of [false, true]) {
    const p = add(timelineProject(), { kind, easing, audio }).project, [left, right] = p.tracks[0].clips;
    assert.deepEqual(plain(right.transition_in), { left_clip_id: 'a', kind, duration: 0.5, easing, audio });
    for (const c of [left, right]) { assert.equal(c.fade_in, 0); assert.equal(c.fade_out, 0); assert.equal(c.mute, false); assert.equal(c.volume, 1); }
    assert.equal(right.trim, 0.123456789); assert.equal(right.duration, 4);
  }
});

test('add and remove are inverse geometry proposals; server history is not fabricated by helpers', () => {
  const p = frozen(timelineProject()), proposed = add(p), before = JSON.stringify(proposed.project);
  const removed = compositions.buildRemoveTransitionProposal(frozen(proposed.project), boundary, context());
  assert.deepEqual(plain(removed.project), plain(p)); assert.equal(removed.shiftSeconds, 0.5);
  assert.equal(removed.project.tracks[0].clips[1].transition_in, null);
  assert.equal(JSON.stringify(proposed.project), before);
  assert.equal('revision' in removed, false); assert.equal('can_undo' in removed, false);
  assert.throws(() => compositions.buildRemoveTransitionProposal(p, boundary, context()));
  assert.throws(() => add(proposed.project), /无缝|入转场/);
});

test('minimum is the workspace frame; duration is positive <=1.2 and no more than half of EITHER end', () => {
  for (const fps of [24, 25, 30, 60]) {
    const p = timelineProject(); p.workspace.timeline_fps = fps;
    near(add(p, { duration: 1 / fps }).project.tracks[0].clips[1].start, 4 - 1 / fps);
    assert.throws(() => add(p, { duration: 0.99 / fps }), /一工作区帧/);
  }
  assert.equal(add(timelineProject(), { duration: 1.2 }).project.tracks[0].clips[1].transition_in.duration, 1.2);
  for (const [leftDuration, rightDuration] of [[1, 4], [4, 1]]) {
    const p = project([track('v1', [clip('a', 0, leftDuration), clip('b', leftDuration, rightDuration)])]);
    assert.throws(() => add(p, { duration: 0.6 }), /一半/);
    assert.doesNotThrow(() => add(p, { duration: 0.5 }));
  }
  for (const duration of [0, -1, NaN, Infinity, 1.20001, '0.5']) assert.throws(() => add(timelineProject(), { duration }));
});

test('both endpoints require existing full-frame cover; no parameter is auto-fixed', () => {
  const invalid = [{ scale: 0.5 }, { x: 0.1 }, { y: -0.1 }, { opacity: 0.9 }, { fit: 'contain' },
    { mask: { type: 'rectangle', x: 0.5, y: 0.5, width: 1, height: 1, feather: 0, invert: false } },
    { chroma_color: '00FF00' }, { fade_in: 0.1 }, { fade_out: 0.1 }, { freeze: true, mute: true },
    { keyframes: { x: [], y: [], scale: [], opacity: [{ time: 0, value: 1, easing: 'linear' }, { time: 2, value: 0.5, easing: 'ease_out' }] } }];
  for (const index of [0, 1]) for (const patch of invalid) {
    const p = timelineProject(); Object.assign(p.tracks[0].clips[index], patch); const original = JSON.stringify(p);
    assert.throws(() => add(frozen(p)), /全帧|转场/); assert.equal(JSON.stringify(p), original);
  }
});

test('same-track, contiguous new boundary, locked-track, stale IDs and third-overlap checks fail closed', () => {
  const locked = timelineProject(); locked.tracks[0].locked = true;
  assert.throws(() => add(locked), /锁定/);
  const transitioned = add().project; transitioned.tracks[0].locked = true;
  assert.throws(() => compositions.buildRemoveTransitionProposal(transitioned, boundary, context()), /锁定/);
  for (const delta of [0.01, -0.01, 5e-10]) {
    const p = timelineProject(); p.tracks[0].clips[1].start += delta;
    assert.throws(() => add(p), /无缝/);
  }
  for (const patch of [{ trackId: 'missing' }, { leftClipId: 'missing' }, { rightClipId: 'c' }, { leftClipId: 'b', rightClipId: 'a' }]) {
    assert.throws(() => compositions.buildTransitionProposal(timelineProject(), { ...boundary, ...patch }, transition(), context()));
  }
  const p = timelineProject(), other = p.tracks[0].clips.splice(1, 1)[0]; p.tracks.push(track('v2', [other]));
  assert.throws(() => add(p));
  const third = add().project; third.tracks[0].clips.push(clip('third', 3.75, 0.1));
  assert.throws(() => api.normalizeProject(third), /第三/);
  const conflict = project([track('v1', [clip('a'), clip('b', 4), clip('third', 4.1, 1)])]);
  assert.throws(() => add(conflict), /重叠|第三/);
});

test('snapshot validator rejects incorrect links/overlap/order and inapplicable track types, not legacy unrelated overlaps', () => {
  const valid = add().project;
  for (const edit of [p => { p.tracks[0].clips[1].start += 5e-10; },
    p => { p.tracks[0].clips[1].transition_in.left_clip_id = 'b'; },
    p => { p.tracks[0].clips[1].transition_in.left_clip_id = 'missing'; },
    p => { p.tracks[0].clips[1].start = 0; }, p => { p.tracks[0].clips[1].duration = 0.25; }]) {
    const broken = plain(valid); edit(broken); assert.throws(() => api.normalizeProject(broken));
  }
  const audio = project([track('sound', [api.makeClip({ id: 'a', source_id: 'master', duration: 4 }),
    api.makeClip({ id: 'b', source_id: 'master', start: 3.5, duration: 4 })], { type: 'audio' })]);
  audio.tracks[0].clips[1].transition_in = { left_clip_id: 'a', ...transition() };
  assert.throws(() => api.normalizeProject(audio));
  // A short legacy overlap BEFORE the envelope is legal in the actual backend.
  const legacy = plain(valid); legacy.tracks[0].clips.splice(1, 0, clip('small-legacy', 1, 0.25));
  assert.doesNotThrow(() => api.normalizeProject(legacy));
  assert.ok(compositions.transitionBoundaries(legacy).some(b => b.leftClipId === 'a' && b.rightClipId === 'b'));
  const removed = compositions.buildRemoveTransitionProposal(legacy, boundary, context());
  assert.equal(removed.project.tracks[0].clips.find(c => c.id === 'small-legacy').start, 1);
  assert.equal(removed.project.tracks[0].clips.find(c => c.id === 'b').start, 4);
  const independent = timelineProject(); independent.tracks[0].clips[1].start = 1;
  assert.doesNotThrow(() => api.normalizeProject(independent), 'Ordinary independent overlap is not converted to a transition.');
});

test('a middle clip can have valid transitions at both ends, with envelopes meeting at its midpoint', () => {
  const p = project([track('v1', [clip('a', 0, 2), clip('b', 2, 2), clip('c', 4, 2)])]);
  const first = add(p, { duration: 1 }), secondBoundary = { trackId: 'v1', leftClipId: 'b', rightClipId: 'c' };
  const second = compositions.buildTransitionProposal(first.project, secondBoundary, transition({ duration: 1, kind: 'wipe_up', audio: false }), context());
  assert.deepEqual(plain(second.project.tracks[0].clips.map(c => c.start)), [0, 1, 2]);
  assert.equal(second.project.tracks[0].clips[1].transition_in.left_clip_id, 'a');
  assert.equal(second.project.tracks[0].clips[2].transition_in.left_clip_id, 'b');
  const removeFirst = compositions.buildRemoveTransitionProposal(second.project, boundary, context());
  assert.equal(removeFirst.project.tracks[0].clips[2].transition_in.kind, 'wipe_up');
  assert.deepEqual(plain(compositions.buildRemoveTransitionProposal(removeFirst.project, secondBoundary, context()).project), plain(p));
});

test('removal cannot move successors beyond 120 seconds; lower duration, bytes, sources and history caps are checked', () => {
  const p = add().project; p.tracks[0].clips.push(clip('late', 119, 1));
  const snapshot = JSON.stringify(p);
  assert.throws(() => compositions.buildRemoveTransitionProposal(p, boundary, context()));
  assert.equal(JSON.stringify(p), snapshot);
  assert.throws(() => add(timelineProject(), {}, context({ limits: { duration_seconds: 8 } })), /时长/);
  assert.throws(() => add(timelineProject(), {}, context({ limits: { request_bytes: 100 } })), /字节/);
  assert.throws(() => add(timelineProject(), {}, context({ expectedRevision: 100 })), /修订|历史/);
  assert.throws(() => add(timelineProject(), {}, context({ limits: { history_mutations: 7 } })), /修订|历史/);
  assert.doesNotThrow(() => add(timelineProject(), {}, context({ expectedRevision: 99 })));
  assert.throws(() => add(timelineProject(), {}, context({ sources: sources.filter(s => s.id !== 'cam_a') })), /源 ID/);
  assert.throws(() => add(timelineProject(), {}, context({ durations: { cam_a: 3 } })), /源出点/);
});

test('2/3/4-camera grids generate ordinary exact quadrant overlays plus one continuous master audio clip', () => {
  for (const count of [2, 3, 4]) {
    const recipe = gridRecipe({ angles: sources.slice(0, count).map((s, i) => ({ source_id: s.id, sync_in: 0.125 + i })) });
    const p = frozen(project()), original = JSON.stringify(p); frozen(recipe);
    const result = compose(recipe, p), generated = result.project.tracks;
    const positions = [[-0.25, -0.25], [0.25, -0.25], [-0.25, 0.25], [0.25, 0.25]];
    assert.equal(generated.length, count + 1); assert.equal(result.stats.inputs, count + 1);
    generated.slice(0, count).forEach((lane, index) => {
      const c = lane.clips[0]; assert.equal(lane.type, 'overlay'); assert.equal(c.scale, 0.5);
      assert.deepEqual([c.x, c.y], positions[index]); assert.equal(c.fit, 'cover'); assert.equal(c.opacity, 1);
      assert.equal(c.start, 0); assert.equal(c.duration, 6); assert.equal(c.trim, recipe.angles[index].sync_in);
      assert.equal(c.mute, true); assert.equal(c.transition_in, null);
    });
    const sound = generated.at(-1); assert.equal(sound.type, 'audio'); assert.equal(sound.clips.length, 1);
    assert.equal(sound.clips[0].source_id, 'master'); assert.equal(sound.clips[0].trim, 0.75);
    assert.equal(sound.clips[0].duration, 6); assert.equal(sound.clips[0].start, 0); assert.equal(sound.clips[0].mute, false);
    assert.deepEqual(plain(result.project.workspace), plain(p.workspace)); assert.equal(result.stats.end, 6);
    assert.equal(JSON.stringify(p), original); assert.deepEqual(plain(compose(recipe, p).project), plain(result.project));
    assert.equal('multicam' in result.project, false); assert.equal('recipe' in result.project, false);
  }
});

test('manual switch cuts retain exact source seconds with continuous selected-camera or dedicated master sound', () => {
  const recipe = switchRecipe({ duration: 6.2 }), p = project(); p.workspace.timeline_fps = 60;
  const result = compose(recipe, p), [video, audio] = result.project.tracks;
  assert.equal(video.type, 'video'); assert.equal(audio.type, 'audio'); assert.equal(result.stats.inputs, 4);
  video.clips.forEach((c, i) => {
    const cut = recipe.cuts[i], angle = recipe.angles[cut.angle - 1];
    assert.equal(c.start, cut.start); near(c.duration, (recipe.cuts[i + 1]?.start ?? recipe.duration) - cut.start);
    near(c.trim, angle.sync_in + cut.start); assert.equal(c.source_id, angle.source_id);
    assert.equal(c.mute, true); assert.equal(c.scale, 1); assert.equal(c.fit, 'cover'); assert.equal(c.transition_in, null);
  });
  assert.equal(result.project.workspace.timeline_fps, 60); assert.equal(video.clips[2].start, 3.125, 'Not silently frame-quantized.');
  assert.equal(audio.clips.length, 1); assert.equal(audio.clips[0].duration, 6.2); assert.equal(audio.clips[0].trim, 0.75);
  const cameraMaster = compose(switchRecipe({ master: recipe.angles[1] }));
  assert.equal(cameraMaster.project.tracks[1].clips[0].source_id, 'cam_b');
  assert.equal(cameraMaster.project.tracks[1].clips[0].trim, 1.125);
  assert.ok(cameraMaster.project.tracks[0].clips.every(c => c.mute));
});

test('sync marks are optional explicit input, never silently substituted, shortened, rewritten or quantized', () => {
  const p = project(); p.workspace.source_marks[0] = { source_id: 'cam_a', in_point: 3.012345678, out_point: 4.2 };
  const manual = compose(gridRecipe(), p);
  assert.equal(manual.project.tracks[0].clips[0].trim, 0.123456789);
  const marked = compose(gridRecipe({ angles: [{ source_id: 'cam_a', sync_in: p.workspace.source_marks[0].in_point },
    { source_id: 'cam_b', sync_in: 1 }] }), p);
  assert.equal(marked.project.tracks[0].clips[0].trim, 3.012345678); assert.equal(marked.project.tracks[0].clips[0].duration, 6);
  assert.deepEqual(plain(marked.project.workspace), plain(p.workspace));
  marked.project.workspace.source_marks[0].in_point = 2;
  assert.equal(p.workspace.source_marks[0].in_point, 3.012345678);
});

test('only catalog sources, finite explicit sync seconds and known video/audio metadata are accepted', () => {
  for (const source_id of ['', 'unknown', '../cam_a', 'https://invalid.example/media']) {
    assert.throws(() => compose(gridRecipe({ angles: [{ source_id, sync_in: 0 }, { source_id: 'cam_b', sync_in: 0 }] })));
    assert.throws(() => compose(gridRecipe({ master: { source_id, sync_in: 0 } })));
  }
  for (const sync_in of [-1, NaN, Infinity, 3600, '0', undefined]) {
    assert.throws(() => compose(gridRecipe({ angles: [{ source_id: 'cam_a', sync_in }, { source_id: 'cam_b', sync_in: 0 }] })));
    assert.throws(() => compose(gridRecipe({ master: { source_id: 'master', sync_in } })));
  }
  assert.throws(() => compose(gridRecipe(), project(), context({ sources: [...sources, sources[0]] })), /重复/);
  assert.throws(() => compose(gridRecipe(), project(), context({ sources: sources.map(s => s.id === 'master' ? { ...s, has_audio: false } : s) })), /没有音频/);
  assert.throws(() => compose(gridRecipe(), project(), context({ sources: sources.map(s => s.id === 'cam_b' ? { ...s, has_video: false } : s) })), /没有视频/);
  assert.equal(compositions.isMulticamVideoSource(source('sound', { name: 'sound.wav', has_video: undefined })), false);
  const noMetadata = sources.map(s => { const result = { ...s }; delete result.has_audio; delete result.duration; return result; });
  const proposed = compose(gridRecipe(), project(), context({ sources: noMetadata }));
  assert.ok(proposed.warnings.some(w => w.includes('ffprobe'))); assert.ok(proposed.warnings.some(w => w.includes('EOF')));
  assert.equal(proposed.project.tracks.at(-1).type, 'audio', 'Unknown master still uses a server-probed audio track, never a muted fake success.');
});

test('actual cut/grid/master source read bounds are enforced; an angle need not cover unused switch spans', () => {
  assert.throws(() => compose(gridRecipe(), project(), context({ durations: { cam_a: 4 } })), /源出点/);
  assert.throws(() => compose(gridRecipe(), project(), context({ durations: { master: 4 } })), /源出点/);
  for (const duration of [NaN, Infinity, -1, 0, 3601]) {
    assert.throws(() => compose(gridRecipe(), project(), context({ durations: { cam_a: duration } })));
  }
  const recipe = switchRecipe({ duration: 10, angles: [{ source_id: 'cam_a', sync_in: 0 }, { source_id: 'cam_b', sync_in: 0 }],
    cuts: [{ start: 0, angle: 1 }, { start: 1, angle: 2 }], master: { source_id: 'master', sync_in: 0 } });
  const result = compose(recipe, project(), context({ durations: { cam_a: 2, cam_b: 10, master: 10 } }));
  assert.deepEqual(plain(result.reads.map(r => [r.trim, r.end])), [[0, 1], [1, 10], [0, 10]]);
  assert.throws(() => compose(recipe, project(), context({ durations: { cam_b: 9.9 } })));
  const unused = switchRecipe({ angles: [{ source_id: 'cam_a', sync_in: 240 }, { source_id: 'cam_b', sync_in: 0 }], cuts: [{ start: 0, angle: 2 }] });
  assert.throws(() => compose(unused), /同步入点/);
  const unknown = sources.map(s => { const value = { ...s }; delete value.duration; return value; });
  assert.throws(() => compose(gridRecipe({ master: { source_id: 'master', sync_in: 3599 } }), project(), context({ sources: unknown })), /3600/);
});

test('cut text is strict and ordered cuts must be unique, start at zero and remain inside duration', () => {
  assert.deepEqual(plain(compositions.parseMulticamCuts('0,1\r\n1.25 2\n3，1')), [{ start: 0, angle: 1 }, { start: 1.25, angle: 2 }, { start: 3, angle: 1 }]);
  for (const input of ['', '0,0', '0,5', '0,1,2', '-1,1', '1e2,1', '0x10,1', '0,1.5', '0,1\ninvalid', 'x'.repeat(4097)]) {
    assert.throws(() => compositions.parseMulticamCuts(input));
  }
  for (const cuts of [[], [{ start: 1, angle: 1 }], [{ start: 0, angle: 1 }, { start: 0, angle: 2 }],
    [{ start: 0, angle: 1 }, { start: 2, angle: 2 }, { start: 1, angle: 1 }], [{ start: 0, angle: 1 }, { start: 6, angle: 2 }],
    [{ start: 0, angle: 1 }, { start: NaN, angle: 2 }], [{ start: -1, angle: 1 }], [{ start: 0, angle: 3 }], [{ start: 0, angle: 1.5 }]]) {
    assert.throws(() => compose(switchRecipe({ cuts })));
  }
  assert.throws(() => compose(gridRecipe({ cuts: [{ start: 0, angle: 1 }] })), /不使用切换清单/);
});

test('2–4 cameras, <=12 cuts, 16 actual clip inputs, 8 tracks, 64 clips, <=120 seconds and request budgets', () => {
  for (const count of [0, 1, 5, 9, 16]) assert.throws(() => compose(gridRecipe({ angles: Array.from({ length: count }, () => ({ source_id: 'cam_a', sync_in: 0 })) })));
  for (const duration of [0, -1, 120.1, NaN, Infinity, '6']) assert.throws(() => compose(gridRecipe({ duration })));
  const twelve = switchRecipe({ cuts: Array.from({ length: 12 }, (_, i) => ({ start: i * 0.5, angle: i % 2 + 1 })) });
  assert.equal(compose(twelve).stats.inputs, 13);
  assert.throws(() => compose(switchRecipe({ cuts: Array.from({ length: 13 }, (_, i) => ({ start: i * 0.4, angle: 1 })) })), /12/);
  const fourInputs = project([track('existing', Array.from({ length: 4 }, (_, i) => clip(`old-${i}`, i, 1)), { hidden: true })]);
  assert.throws(() => compose(twelve, fourInputs), /16 个解码输入/);
  assert.equal(compose(twelve, project([track('old', [clip('old-1'), clip('old-2'), clip('old-3')])])).stats.inputs, 16);
  assert.throws(() => compose(gridRecipe(), project(Array.from({ length: 7 }, (_, i) => track(`empty-${i}`)))));
  const textClips = Array.from({ length: 52 }, (_, i) => api.makeClip({ id: `text-${i}`, duration: 1, text: 'retained' }));
  assert.throws(() => compose(twelve, project([track('text', textClips, { type: 'text' })])));
  assert.doesNotThrow(() => compose(gridRecipe({ duration: 120, angles: [{ source_id: 'cam_a', sync_in: 0 }, { source_id: 'cam_b', sync_in: 0 }], master: { source_id: 'master', sync_in: 0 } })));
  const roundingTail = project([track('tail', [clip('tail-clip', 119.0000000000001, 1)])]);
  assert.throws(() => compose(gridRecipe(), roundingTail), /总时长超限/, 'Do not trim or tolerate an active end above the render limit.');
  for (const limits of [{ tracks: 2 }, { clips: 2 }, { duration_seconds: 5 }, { request_bytes: 100 }, { history_mutations: 7 }]) {
    assert.throws(() => compose(gridRecipe(), project(), context({ limits })));
  }
});

test('append retains even locked tracks and their indices; replacement is explicit and preserves workspace metadata', () => {
  const p = project([track('existing', [clip('old')], { locked: true, hidden: true })]), snapshot = JSON.stringify(p);
  const appended = compose(gridRecipe(), frozen(p));
  assert.deepEqual(plain(appended.project.tracks[0]), plain(p.tracks[0])); assert.equal(appended.placement, 'append');
  assert.throws(() => compose(gridRecipe({ placement: 'replace' }), p), /解锁/);
  const unlocked = plain(p); unlocked.tracks[0].locked = false;
  const replaced = compose(gridRecipe({ placement: 'replace' }), unlocked);
  assert.equal(replaced.project.tracks.some(t => t.id === 'existing'), false);
  for (const key of ['schema_version', 'name', 'workspace', 'markers', 'groups', 'assets']) assert.deepEqual(plain(replaced.project[key]), plain(p[key]));
  assert.equal(JSON.stringify(p), snapshot);
  assert.throws(() => compose(gridRecipe({ placement: 'replace' }), add().project), /绑定转场/);
  const solo = plain(unlocked); solo.tracks[0].hidden = false; solo.tracks[0].solo = true;
  assert.throws(() => compose(gridRecipe(), solo), /独奏/);
  assert.doesNotThrow(() => compose(gridRecipe({ placement: 'replace' }), solo));
});

test('generated IDs are independent, deterministic and collision-free across clips/tracks/groups/markers', () => {
  const p = project([track('mc_track_2', [clip('mc_clip_2')])]);
  p.groups.push({ id: 'mc_track_1', name: 'occupied' }); p.markers.push({ id: 'mc_clip_1', time: 1, label: 'occupied' });
  const result = compose(gridRecipe(), p), ids = [...result.project.groups.map(g => g.id), ...result.project.markers.map(m => m.id),
    ...result.project.tracks.flatMap(t => [t.id, ...t.clips.map(c => c.id)])];
  assert.equal(ids.length, new Set(ids).size); assert.equal(result.generatedTrackIds[0], 'mc_track_3');
  assert.deepEqual(plain(compose(gridRecipe(), p).project), plain(result.project));
  assert.equal(result.project.tracks[1].clips[0].id, 'mc_clip_3');
});

test('full clip copies retain transition/color/keyframe/mask fields; cut/save receipt member ordering remains canonical', () => {
  const p = add().project, right = p.tracks[0].clips[1];
  const copied = api.normalizeClip({ ...right, transition_in: { audio: true, easing: 'linear', duration: 0.5, kind: 'dissolve', left_clip_id: 'a' } });
  assert.equal(JSON.stringify(copied), JSON.stringify(right));
  assert.deepEqual(plain(copied.transition_in), plain(right.transition_in));
  const pasted = plain(p); pasted.tracks[0].clips.push({ ...plain(copied), id: 'paste', start: 20 });
  assert.throws(() => api.normalizeProject(pasted), 'Cannot silently drop the copied binding to make a bad paste succeed.');
  const rich = clip('rich', 0, 3, { color_preset: 'warm', temperature: 0.4, rgb_curves: { blue: [{ x: 0, y: 1 }, { x: 1, y: 0 }] },
    keyframes: { x: [], y: [], scale: [{ time: 0, value: 1, easing: 'ease_in' }, { time: 2, value: 0.5, easing: 'ease_out' }], opacity: [] },
    mask: { type: 'ellipse', x: 0.5, y: 0.5, width: 0.8, height: 0.8, feather: 0.1, invert: false } });
  const normalized = api.normalizeClip(rich); assert.deepEqual(plain(normalized), plain(rich));
  normalized.rgb_curves.blue[0].y = 0; assert.equal(rich.rgb_curves.blue[0].y, 1);
  assert.equal(timeline.clipEditProblem(project([track('v', [rich])]), rich.id), null);
  assert.equal(timeline.buildDeleteAction(rich, false).op, 'delete');
  assert.deepEqual(plain(timeline.buildTrimAction('slip', rich, { trim: 0.25, duration: 3, at: 0 }, 25)), { op: 'slip', clip_id: 'rich', trim: 0.25 });
});

test('real API wrapper uses current revisions and validates full save/import/undo receipts (in-memory transport only)', async () => {
  const proposed = add().project, calls = [];
  let receipt = { revision: 41, project: proposed, can_undo: true, can_redo: false };
  const transport = api.createStudioApi('unit-task', async (path, init) => {
    calls.push({ path, method: init?.method, body: init?.body ? JSON.parse(init.body) : null });
    assert.equal(init.credentials, 'include'); assert.equal(init.cache, 'no-store'); return plain(receipt);
  });
  const saved = await transport.save(7, proposed);
  assert.equal(saved.revision, 41, 'Return the receipt revision, never assume request revision + 1.');
  assert.deepEqual(calls.at(-1).body, { expected_revision: 7, project: plain(proposed) });
  assert.deepEqual(plain(saved.project), plain(proposed));
  await transport.importProject(41, compose().project); assert.equal(calls.at(-1).path, '/api/tasks/unit-task/studio/project/import');
  for (const action of [{ op: 'undo' }, { op: 'redo' }, { op: 'move', clip_id: 'c', at: 15, track_id: 'v1' },
    { op: 'duplicate', clip_id: 'c', new_id: 'copy', at: 15, track_id: 'v1' }]) {
    await transport.action(41, action); assert.deepEqual(calls.at(-1).body, { expected_revision: 41, ...action });
  }
  const before = calls.length, invalid = plain(proposed); invalid.tracks[0].clips[1].transition_in.kind = 'fake-fx';
  await assert.rejects(transport.save(41, invalid)); assert.equal(calls.length, before);
  receipt = { ...receipt, project: invalid }; await assert.rejects(transport.project());
  assert.equal(transport.sourceUrl('cam_a'), '/api/tasks/unit-task/studio/sources/cam_a');
  assert.equal(calls.some(call => /multicam|transitions/.test(call.path)), false, 'No invented composition API.');
});

test('catalog adapter preserves optional trusted metadata and rejects malformed values without opening URLs', async () => {
  let value = { sources: sources.map(s => ({ ...s, url: 'https://invalid.example/not-opened' })), shots: [], report: { rows: [], quality: null } };
  const transport = api.createStudioApi('unit-task', async () => plain(value));
  assert.deepEqual(plain(await transport.sources()), value);
  for (const patch of [{ duration: '12' }, { duration: 0 }, { duration: -1 }, { duration: null }, { has_audio: 'true' }, { has_video: 1 }]) {
    value = { ...value, sources: [{ ...sources[0], ...patch }] }; await assert.rejects(transport.sources());
  }
  const legacy = { ...sources[0] }; delete legacy.duration; delete legacy.has_audio; delete legacy.has_video;
  value = { ...value, sources: [legacy] }; assert.deepEqual(plain((await transport.sources()).sources[0]), legacy);
});

test('Studio JSX retains editing/output controls and declares actual composition anchors (static only)', () => {
  const sourceText = readFileSync(new URL('../src/components/Studio.tsx', import.meta.url), 'utf8');
  const parsed = ts.transpileModule(sourceText, { fileName: 'Studio.tsx', reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve } });
  assert.equal(parsed.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  for (const section of ['transitions', 'multicam', 'timecontrols', 'source', 'trim-modes', 'clipboard', 'palette', 'export', 'history']) {
    assert.ok(sourceText.includes(`data-studio-section="${section}"`), section);
  }
  for (const attribute of ['data-transition-kind', 'data-transition-duration', 'data-transition-apply', 'data-transition-remove',
    'data-transition-left', 'data-transition-right', 'data-transition-shift', 'data-multicam-count', 'data-multicam-duration',
    'data-multicam-master', 'data-multicam-cuts', 'data-multicam-placement', 'data-multicam-apply', 'data-multicam-source-preview']) assert.ok(sourceText.includes(attribute));
  assert.ok(sourceText.includes('api.save(current.revision, fresh.project)'));
  assert.ok(sourceText.includes('const current = await saveDraft();'));
  assert.ok(sourceText.includes('if (!mounted.current) return;'));
  assert.ok(sourceText.includes('window.confirm(`${confirmation}'));
  assert.ok(sourceText.includes('rebuild(current.project, current.revision)'));
  assert.ok(sourceText.includes('JSON.stringify(fresh.project) !== JSON.stringify(shown.project)'));
  for (const original of ['copyClip(false)', 'copyClip(true)', 'onClick={pasteClip}', 'buildDeleteAction(selection.clip, rippleEnabled)',
    'buildTrimAction(editMode', 'collectSnapTargets', 'job.kind !== "proxy" && job.state === "succeeded"',
    'props.accessToken ?? null', '当前源帧设入点', '当前源帧设出点']) assert.ok(sourceText.includes(original), original);
  assert.ok(sourceText.includes('chooseSource(id); focusSection("preview")'));
  assert.ok(sourceText.includes('previewSource ? sourceUrl(previewSource.id)'));
  assert.ok(sourceText.includes('value="9" disabled')); assert.ok(sourceText.includes('value="16" disabled'));
  assert.ok(sourceText.includes('声波 / 波形同步与自动时间码对齐未实现'));
  assert.ok(sourceText.includes('没有批量按钮、任意自定义滤镜或模板'));
  const helpers = readFileSync(new URL('../src/lib/studioCompositions.ts', import.meta.url), 'utf8');
  assert.doesNotMatch(helpers, /\b(?:fetch|XMLHttpRequest|WebSocket|setTimeout)\s*\(/);
  assert.doesNotMatch(helpers, /\b(?:Date\.now|Math\.random|crypto\.randomUUID)\s*\(/);
});

// C26: inactive, unreferenced timelines still share the whole project's ID
// namespace. These are valid MAIN proposals, not new active/nested guard rules.
function compositionGraphIds(p) {
  return [...p.groups.map(group => group.id), ...p.sequences.map(sequence => sequence.id),
    ...[p, ...p.sequences].flatMap(scope => [...scope.markers.map(marker => marker.id),
      ...scope.tracks.flatMap(lane => [lane.id, ...lane.clips.map(item => item.id)])])];
}
function wholeGraphCollisionProject() {
  const base = project([track('mc_track_2', [clip('mc_clip_2', 0, 2)], { group_id: 'group-real' })]);
  return api.normalizeProject({ ...base, active_sequence_id: null,
    groups: [...base.groups, { id: 'mc_track_1', name: 'Root group reservation' }],
    markers: [...base.markers, { id: 'mc_clip_1', time: 0.5, label: 'Root marker reservation' }],
    sequences: [
      { id: 'mc_track_3', name: 'Inactive A', tracks: [track('mc_track_4', [
        clip('mc_clip_3', 0, 2, { source_id: 'cam_b',
          rgb_curves: { red: [{ x: 0, y: 0.1 }, { x: 1, y: 0.9 }] } }),
      ], { group_id: 'mc_track_1' })], markers: [{ id: 'mc_clip_4', time: 0.75, label: 'Child A marker' }] },
      // Cross-kind names in a SECOND inactive child catch first-child-only or
      // per-kind scans: sequence/track IDs can occupy the clip prefix and vice versa.
      { id: 'mc_clip_5', name: 'Inactive B', tracks: [track('mc_clip_6', [
        clip('mc_track_5', 0, 1, { source_id: 'cam_c' }),
      ])], markers: [{ id: 'mc_track_6', time: 0.25, label: 'Child B marker' }] },
    ],
  });
}

for (const placement of ['append', 'replace']) {
  test(`C26-MC-unit-${placement} · multicam reserves whole-graph IDs deterministically without mutating inputs`, () => {
    const p = frozen(wholeGraphCollisionProject());
    const recipe = frozen(gridRecipe({ placement, duration: 1 }));
    const ctx = frozen(plain(context())); // Do not freeze shared fixtures used by other cases.
    const snapshots = [p, recipe, ctx].map(value => JSON.stringify(value));
    const originalIds = compositionGraphIds(p), reserved = new Set(originalIds);
    assert.equal(reserved.size, originalIds.length, 'The input is valid before the builder is called.');
    assert.equal(p.active_sequence_id, null);
    assert.ok(p.tracks.every(lane => lane.clips.every(item => item.sequence_id === null)));
    for (const prefix of ['track', 'clip']) for (let number = 1; number <= 6; number++) {
      assert.ok(reserved.has(`mc_${prefix}_${number}`), 'Reserve roots, groups, both children and both ID prefixes.');
    }

    const proposed = compose(recipe, p, ctx), repeated = compose(recipe, p, ctx);
    assert.deepEqual(plain(repeated), plain(proposed), 'The complete proposal, not just its IDs, is deterministic.');
    const repeatedSnapshot = JSON.stringify(repeated);
    const ids = compositionGraphIds(proposed.project);
    assert.equal(new Set(ids).size, ids.length, 'Uniqueness includes every inactive sequence, track, clip and marker.');
    assert.equal(proposed.placement, placement);
    assert.equal(proposed.generatedTrackIds.length, 3);
    const generated = proposed.project.tracks.filter(lane => proposed.generatedTrackIds.includes(lane.id));
    assert.equal(generated.length, 3);
    const generatedIds = generated.flatMap(lane => [lane.id, ...lane.clips.map(item => item.id)]);
    assert.equal(generatedIds.length, 6);
    assert.equal(new Set(generatedIds).size, generatedIds.length);
    assert.ok(generatedIds.every(id => !reserved.has(id)),
      'Even replace must allocate against the original complete snapshot, not recycle removed root IDs.');
    if (placement === 'append') {
      assert.deepEqual(plain(proposed.project.tracks.slice(0, p.tracks.length)), plain(p.tracks));
      assert.equal(proposed.project.tracks.length, p.tracks.length + 3);
    } else {
      assert.equal(proposed.project.tracks.length, 3);
      assert.ok(proposed.project.tracks.every(lane => !p.tracks.some(old => old.id === lane.id)));
    }
    for (const key of ['schema_version', 'name', 'workspace', 'markers', 'groups', 'assets', 'sequences', 'active_sequence_id']) {
      assert.deepEqual(plain(proposed.project[key]), plain(p[key]), `${key} is retained in ${placement}.`);
    }
    assert.deepEqual([p, recipe, ctx].map(value => JSON.stringify(value)), snapshots);

    // A returned proposal must not alias either frozen inputs or a prior result.
    proposed.project.sequences[0].tracks[0].clips[0].rgb_curves.red[0].y = 0.4;
    proposed.project.sequences[0].markers[0].label = 'Changed only in this proposal';
    proposed.project.groups[0].name = 'Detached group';
    proposed.project.workspace.source_marks[0].in_point = 0.25;
    generated[0].clips[0].volume = 0.5;
    assert.deepEqual([p, recipe, ctx].map(value => JSON.stringify(value)), snapshots);
    assert.equal(JSON.stringify(repeated), repeatedSnapshot);
    assert.deepEqual(plain(compose(recipe, p, ctx)), plain(repeated));
  });
}