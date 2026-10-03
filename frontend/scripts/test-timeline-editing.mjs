import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Node-only isolated client
// units, NOT React/Playwright, server geometry, FFmpeg or real-media evidence.
// Transpile the ACTUAL modules; no copied editing algorithm, process execution,
// socket, provider, browser storage, or production file writes. VM has no fetch.
function loadModule(relativePath) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    fileName: relativePath, reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, sourceMap: false },
    // The only substitution is Vite's build-time import.meta.env, not transport
    // or editing logic. No source/output URL is opened by these units.
    transformers: { before: [context => root => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
        && ts.isMetaProperty(node.expression) && node.expression.keywordToken === ts.SyntaxKind.ImportKeyword
        ? ts.factory.createObjectLiteralExpression([])
        : ts.visitEachChild(node, visit, context);
      return ts.visitNode(root, visit);
    }] },
  });
  assert.equal(compiled.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length ?? 0, 0,
    'Actual module must transpile without syntax diagnostics.');
  const module = { exports: {} };
  const context = vm.createContext({ module, exports: module.exports, TextEncoder, Headers, crypto: { randomUUID },
    require() { throw new Error('Unexpected runtime dependency in isolated editing units.'); } });
  new vm.Script(compiled.outputText, { filename: relativePath }).runInContext(context, { timeout: 1000 });
  return module.exports;
}

const timeline = loadModule('../src/lib/timelineEditing.ts');
const api = loadModule('../src/lib/studioApi.ts');
const plain = value => JSON.parse(JSON.stringify(value)); // Cross-realm object comparison, not numeric rounding.
const clip = (patch = {}) => api.makeClip({ id: 'clip-a', source_id: 'upload_real_id', start: 0, trim: 0.125, duration: 3, ...patch });
const projectWith = (clips = [clip()], type = 'video', workspace = {}) => api.normalizeProject({
  schema_version: 1, name: 'Pure client unit', tracks: [{ id: 'track-a', type, clips }], markers: [],
  workspace: { ...api.makeWorkspace(), ...workspace },
});
const target = (id, time, kind = 'marker') => ({ id, time, kind, label: id });

test('workspace FPS half-up rounding, decimal ties and frame idempotence', () => {
  assert.equal(timeline.secondsToFrame(1.05, 30), 32);
  assert.equal(timeline.secondsToFrame(1.025, 60), 62);
  assert.equal(timeline.secondsToFrame(1.025 - Number.EPSILON, 60), 61);
  assert.equal(timeline.secondsToFrame(0.02, 25), 1);
  assert.equal(timeline.secondsToFrame(0.0625, 24), 2);
  for (const fps of [24, 25, 30, 60]) for (let frame = 0; frame <= fps * 120; frame++) {
    const seconds = timeline.frameToSeconds(frame, fps);
    assert.equal(timeline.secondsToFrame(seconds, fps), frame);
    assert.equal(timeline.quantizeTime(seconds, fps), seconds);
  }
  for (const seconds of [-1, NaN, Infinity, 1e300]) assert.throws(() => timeline.secondsToFrame(seconds, 30));
  assert.throws(() => timeline.frameToSeconds(0.5, 30));
  assert.throws(() => timeline.secondsToFrame(1, 120));
});

test('frame input accepts strict integer frames or non-drop timecode at workspace FPS', () => {
  for (const fps of [24, 25, 30, 60]) {
    assert.equal(timeline.parseFrameInput(String(fps + 3), fps), (fps + 3) / fps);
    assert.equal(timeline.parseFrameInput('00:00:01:03', fps), (fps + 3) / fps);
    assert.equal(timeline.formatTimecode((fps + 3) / fps, fps), '00:00:01:03');
    assert.equal(timeline.parseFrameInput('00:02:00:00', fps), 120);
    assert.throws(() => timeline.parseFrameInput(`00:00:01:${fps}`, fps));
  }
  for (const value of ['', '-1', '1.5', '1e2', '0x20', '20frames', '00:60:00:00', '00:00:60:00', '00:00:01;02', '00:02:00:01']) {
    assert.throws(() => timeline.parseFrameInput(value, 30), value);
  }
  assert.equal(timeline.parseFrameInput(' 00030 ', 30), 1);
  assert.equal(timeline.formatTimecode(1, 24), '00:00:01:00');
  assert.notEqual(timeline.parseFrameInput('30', 24), timeline.parseFrameInput('30', 60));
});

test('snapping uses a bounded six-frame proposal, and disabled snapping still quantizes at', () => {
  const marks = [target('zero', 0, 'origin'), target('one', 1), target('two', 2, 'clip-start')];
  const chosen = timeline.proposeTimelineTime(1.18, 30, true, marks);
  assert.equal(chosen.seconds, 1); assert.equal(chosen.frame, 30); assert.equal(chosen.target.id, 'one');
  assert.equal(chosen.requested, 1.18);
  assert.equal(timeline.proposeTimelineTime(1.2, 30, true, marks).target.id, 'one');
  assert.equal(timeline.proposeTimelineTime(1 + 7 / 30, 30, true, marks).target, null);
  assert.equal(timeline.proposeTimelineTime(1.18, 30, false, marks).seconds, 35 / 30);
  assert.equal(timeline.proposeTimelineTime(1.18, 30, false, marks).target, null);
  assert.throws(() => timeline.proposeTimelineTime(121, 30, true, marks));
  assert.throws(() => timeline.proposeTimelineTime(3, 30, true, marks, { max: 2 }));
});

test('snap ties are deterministic regardless of catalog, marker or clip order', () => {
  const equalDistance = [target('later', 1.1), target('earlier', 0.9)];
  assert.equal(timeline.proposeTimelineTime(1, 30, true, equalDistance).target.id, 'earlier');
  assert.equal(timeline.proposeTimelineTime(1, 30, true, [...equalDistance].reverse()).target.id, 'earlier');
  const equalFrame = [target('clip', 1, 'clip-start'), target('z-marker', 1), target('a-marker', 1)];
  assert.equal(timeline.proposeTimelineTime(1, 30, true, equalFrame).target.id, 'a-marker');
  assert.equal(timeline.proposeTimelineTime(1, 30, true, [...equalFrame].reverse()).target.id, 'a-marker');
  assert.equal(timeline.proposeTimelineTime(0, 30, true, [target('m', 0), target('o', 0, 'origin')]).target.id, 'o');
});

test('snap collects only actual geometry, excludes the edited clip and never rewrites off-grid endpoints', () => {
  const project = projectWith([clip(), clip({ id: 'clip-b', start: 5.0125, duration: 2.013 })]);
  project.markers.push({ id: 'real-marker', time: 4, label: 'marker' });
  const before = JSON.stringify(project), targets = timeline.collectSnapTargets(project, 'clip-a');
  assert.deepEqual(plain(targets.map(t => t.id)), ['origin', 'real-marker', 'clip-b:start', 'clip-b:end']);
  const proposal = timeline.proposeTimelineTime(5.08, 30, true, targets);
  assert.equal(proposal.target.id, 'clip-b:start'); assert.equal(proposal.target.time, 5.0125); assert.equal(proposal.seconds, 5);
  assert.equal(JSON.stringify(project), before);
  const splitProposal = timeline.proposeTimelineTime(0.12, 30, true, [target('edge', 0), target('valid', 0.2)], { min: 1 / 30, max: 2 });
  assert.equal(splitProposal.target.id, 'valid');
});

test('all five trim modes and ripple-delete build exact backend shapes without mutating envelopes', () => {
  const source = clip({ reverse: true, speed: 1.25, fade_in: 0.3,
    keyframes: { x: [{ time: 0, value: 0, easing: 'ease_in_out' }, { time: 2, value: 0.2, easing: 'linear' }], y: [], scale: [], opacity: [] } });
  const before = JSON.stringify(source), values = { trim: 0.123456789, duration: 1.05, at: 2.05 };
  assert.deepEqual(plain(timeline.buildTrimAction('ordinary', source, values, 30)), { op: 'trim', clip_id: source.id, trim: values.trim, duration: 1.05 });
  assert.deepEqual(plain(timeline.buildTrimAction('ripple', source, values, 30)), { op: 'ripple_trim', clip_id: source.id, trim: values.trim, duration: 32 / 30 });
  assert.deepEqual(plain(timeline.buildTrimAction('slip', source, { ...values, at: NaN, duration: NaN }, 30)), { op: 'slip', clip_id: source.id, trim: values.trim });
  for (const mode of ['roll', 'slide']) assert.deepEqual(plain(timeline.buildTrimAction(mode, source, { ...values, trim: NaN, duration: NaN }, 30)), { op: mode, clip_id: source.id, at: 62 / 30 });
  assert.deepEqual(plain(timeline.buildDeleteAction(source, true)), { op: 'ripple_delete', clip_id: source.id });
  assert.deepEqual(plain(timeline.buildDeleteAction(source, false)), { op: 'delete', clip_id: source.id });
  assert.equal(JSON.stringify(source), before, 'Client proposes opcodes; it must not imitate server roll/slide/envelope geometry.');
  assert.throws(() => timeline.buildTrimAction('ripple', source, { ...values, duration: 0.001 }, 30));
  assert.throws(() => timeline.buildTrimAction('slip', clip({ source_id: null, text: 'text' }), values, 30));
  assert.throws(() => timeline.buildTrimAction('ordinary', clip({ source_id: null }), values, 30));
  assert.throws(() => timeline.buildTrimAction('roll', source, { ...values, at: 121 }, 30));
});

test('split refuses sub-frame remainders, fades, visual keyframes and text reveal envelopes', () => {
  assert.equal(timeline.canSplitAt(clip(), 1, 30), true);
  assert.equal(timeline.canSplitAt(clip(), 0.01, 30), false);
  assert.equal(timeline.canSplitAt(clip(), 2.99, 30), false);
  assert.equal(timeline.canSplitAt(clip({ fade_out: 0.1 }), 1, 30), false);
  assert.equal(timeline.canSplitAt(clip({ text_animation: 'typewriter' }), 1, 30), false);
  assert.equal(timeline.canSplitAt(clip({ keyframes: { x: [{ time: 0, value: 0, easing: 'linear' }, { time: 2, value: 1, easing: 'ease_in_out' }], y: [], scale: [], opacity: [] } }), 1, 30), false);
});

test('local action guard rejects locked source/destination, missing clips and unlike track types', () => {
  const project = projectWith();
  project.tracks.push({ id: 'track-b', type: 'video', name: 'Target', locked: false, hidden: false, solo: false, color: 'gold', group_id: null, clips: [] });
  assert.equal(timeline.clipEditProblem(project, 'clip-a'), null);
  assert.equal(timeline.clipEditProblem(project, 'clip-a', 'track-b'), null);
  project.tracks[0].locked = true;
  assert.match(timeline.clipEditProblem(project, 'clip-a'), /锁定/);
  project.tracks[0].locked = false; project.tracks[1].locked = true;
  assert.match(timeline.clipEditProblem(project, 'clip-a', 'track-b'), /锁定/);
  project.tracks[1].locked = false; project.tracks[1].type = 'audio';
  assert.match(timeline.clipEditProblem(project, 'clip-a', 'track-b'), /类型/);
  assert.match(timeline.clipEditProblem(project, 'missing'), /不存在/);
  assert.match(timeline.clipEditProblem(project, 'clip-a', 'missing'), /存在/);
});

test('in/out retains precise caller-provided source seconds, authorizes real IDs and clears without inventing timing', () => {
  const ids = ['upload_real_id', 'video_only'];
  const sourceSeconds = { in_point: 0.123456789, out_point: 2.987654321 };
  const before = [{ source_id: 'video_only', in_point: 1, out_point: 2 }], snapshot = JSON.stringify(before);
  const next = timeline.updateSourceMarks(before, ids[0], sourceSeconds, ids, 3);
  assert.deepEqual(plain(next[1]), { source_id: ids[0], ...sourceSeconds });
  assert.equal(timeline.updateSourceMarks([], ids[0], { ...sourceSeconds, source_id: 'unknown_source' }, ids)[0].source_id, ids[0]);
  assert.equal(JSON.stringify(before), snapshot);
  assert.deepEqual(plain(timeline.updateSourceMarks(next, ids[0], null, ids)), before);
  assert.deepEqual(plain(timeline.updateSourceMarks([], ids[0], null, ids)), []);
  assert.throws(() => timeline.updateSourceMarks(next, 'unknown_source', sourceSeconds, ids));
  assert.throws(() => timeline.updateSourceMarks(next, ids[0], { in_point: 3, out_point: 2 }, ids));
  assert.throws(() => timeline.updateSourceMarks(next, ids[0], { in_point: 0, out_point: 4 }, ids, 3));
  assert.throws(() => timeline.updateSourceMarks(next, ids[0], { in_point: NaN, out_point: 2 }, ids));
  assert.throws(() => timeline.validateSourceRange(0, 3601));
  const full = Array.from({ length: 200 }, (_, i) => ({ source_id: `source_${i}`, in_point: 0, out_point: 1 }));
  assert.throws(() => timeline.updateSourceMarks(full, ids[0], sourceSeconds, [...ids, ...full.map(m => m.source_id)]));
  assert.equal(timeline.updateSourceMarks(full, 'source_0', { in_point: 0.1, out_point: 0.9 }, full.map(m => m.source_id)).length, 200);
});

test('legacy project normalization adds all defaults without inventing marks or retiming source/clip data', () => {
  const old = { schema_version: 1, tracks: [{ id: 'old-track', type: 'video', clips: [{ id: 'old-clip', source_id: 'upload_real_id', duration: 2.013, start: 0.017, trim: 0.123456789 }] }], markers: [] };
  const project = api.parseProjectImport(JSON.stringify({ revision: 7, project: old }));
  assert.deepEqual(plain(project.workspace), { layout: 'default', timeline_zoom: 1, shortcuts: {}, timeline_fps: 30,
    time_display: 'seconds', snap_enabled: true, ripple_enabled: false, source_marks: [] });
  const stored = project.tracks[0].clips[0];
  assert.equal(stored.start, 0.017); assert.equal(stored.duration, 2.013); assert.equal(stored.trim, 0.123456789);
  for (const key of ['temperature', 'hue', 'shadows', 'highlights', 'fade_amount']) assert.equal(stored[key], 0);
  assert.equal(stored.color_preset, 'none'); assert.deepEqual(plain(stored.rgb_curves), {});
  assert.equal(stored.text_animation, 'none'); assert.equal(stored.text_template, 'custom');
  project.workspace.timeline_fps = 60;
  assert.equal(api.normalizeProject(project).tracks[0].clips[0].trim, 0.123456789);
});

test('workspace import rejects wrong types, unknown fields, invalid source marks, duplicates and invalid FPS', () => {
  for (const bad of [{ timeline_fps: 29.97 }, { timeline_fps: '30' }, { time_display: 'drop_frame' }, { snap_enabled: 'true' },
    { ripple_enabled: 1 }, { source_marks: null }, { unknown_setting: true }, { timeline_zoom: Infinity },
    { source_marks: [{ source_id: 'source', in_point: 2, out_point: 2 }] },
    { source_marks: [{ source_id: '../path', in_point: 0, out_point: 1 }] },
    { source_marks: [{ source_id: 'source', in_point: 0, out_point: 1, command: 'not accepted' }] },
    { source_marks: [{ source_id: 'same', in_point: 0, out_point: 1 }, { source_id: 'same', in_point: 1, out_point: 2 }] },
    { shortcuts: { undo: 'Ctrl+Z', redo: 'Ctrl+Z' } }]) {
    assert.throws(() => api.normalizeWorkspace({ ...api.makeWorkspace(), ...bad }));
  }
  assert.throws(() => api.normalizeWorkspace({ source_marks: Array.from({ length: 201 }, (_, i) => ({ source_id: `s_${i}`, in_point: 0, out_point: 1 })) }));
});

test('all typed color parameters and RGB inversion survive copies, saves, and adjustment applicability', () => {
  const value = clip({ temperature: 0.6, hue: -50, shadows: -0.4, highlights: 0.3, fade_amount: 0.5, color_preset: 'cinema',
    sharpen: 0.25, noise: 3, rgb_curves: { red: [{ x: 0, y: 1 }, { x: 0.4, y: 0.7 }, { x: 1, y: 0 }] } });
  const normalized = projectWith([value]), copied = api.copyColorParameters(normalized.tracks[0].clips[0]);
  assert.equal(copied.temperature, 0.6); assert.equal(copied.hue, -50); assert.equal(copied.shadows, -0.4);
  assert.equal(copied.highlights, 0.3); assert.equal(copied.fade_amount, 0.5); assert.equal(copied.color_preset, 'cinema');
  assert.deepEqual(plain(copied.rgb_curves), plain(value.rgb_curves));
  const adjustment = api.applicableColorParameters(copied, 'adjustment');
  assert.equal('sharpen' in adjustment, false); assert.equal('noise' in adjustment, false);
  assert.deepEqual(plain(adjustment.rgb_curves), plain(copied.rgb_curves));
  assert.equal(projectWith([api.makeClip({ id: 'adjustment', duration: 2, ...adjustment })], 'adjustment').tracks[0].clips[0].hue, -50);
  copied.rgb_curves.red[1].y = 0.2;
  assert.equal(value.rgb_curves.red[1].y, 0.7); assert.equal(adjustment.rgb_curves.red[1].y, 0.7);
  assert.throws(() => api.applicableColorParameters(copied, 'audio'));
  const reordered = clip({ mask: { invert: false, feather: 0.1, height: 0.8, width: 0.7, y: 0.5, x: 0.5, type: 'ellipse' } });
  const canonical = api.normalizeClip(reordered);
  assert.equal(JSON.stringify(canonical), JSON.stringify(projectWith([reordered]).tracks[0].clips[0]),
    'Cut snapshot and save receipt must compare identically despite input JSON member ordering.');
});

test('RGB normalization rejects malformed channels/points but not creative decreasing y', () => {
  const ends = [{ x: 0, y: 0 }, { x: 1, y: 1 }];
  for (const invalid of [null, [], { alpha: ends }, { red: [] }, { red: [ends[0]] }, { red: [{ x: 0.1, y: 0 }, ends[1]] },
    { red: [ends[0], { x: 0.5, y: 0.4 }, { x: 0.5, y: 0.7 }, ends[1]] }, { red: [{ x: 0, y: 2 }, ends[1]] },
    { red: [{ x: 0, y: '0' }, ends[1]] }, { red: [{ x: 0, y: NaN }, ends[1]] },
    { red: [{ x: 0, y: 0, filter: 'no raw strings' }, ends[1]] },
    { red: Array.from({ length: 9 }, (_, i) => ({ x: i / 8, y: i / 8 })) }]) assert.throws(() => api.normalizeRGBCurves(invalid));
  assert.deepEqual(plain(api.normalizeRGBCurves({ blue: [{ x: 0, y: 1 }, { x: 1, y: 0 }] })), { blue: [{ x: 0, y: 1 }, { x: 1, y: 0 }] });
});

test('strict clip import rejects unknown/wrong fields and inapplicable effects', () => {
  const badPatches = [{ temperature: 1.1 }, { temperature: '0.5' }, { temp: 0.5 }, { hue: 181 }, { shadows: -1.1 },
    { highlights: Infinity }, { fade_amount: -0.1 }, { color_preset: 'lut' }, { rgb_curves: null },
    { text_animation: 'karaoke' }, { text_template: 'external' }, { filter: 'arbitrary' }, { reverse: 'false' },
    { crop: { x: 0, y: 0, width: 1, height: 1, expression: 'no' } }];
  for (const patch of badPatches) assert.throws(() => projectWith([clip(patch)]));
  assert.throws(() => projectWith([clip({ temperature: 0.5 })], 'audio'));
  assert.throws(() => projectWith([api.makeClip({ id: 'a', duration: 1, sharpen: 0.2 })], 'adjustment'));
  assert.throws(() => api.normalizeProject({ ...projectWith(), accidental_field: true }));
});

test('text templates retain authored style and text animations serialize without fake word timings', () => {
  for (const text_template of ['custom', 'news', 'outline', 'gold', 'note']) for (const text_animation of ['none', 'typewriter', 'fade', 'pop']) {
    const authored = api.makeClip({ id: 'text-a', duration: 3, text: 'literal <b>text</b>', color: '123456', bold: false,
      outline: 0.5, shadow: 0.25, background: true, text_template, text_animation });
    const result = api.parseProjectImport(JSON.stringify(projectWith([authored], 'text'))).tracks[0].clips[0];
    for (const key of ['color', 'bold', 'outline', 'shadow', 'background', 'text_template', 'text_animation', 'text']) assert.equal(result[key], authored[key]);
    assert.equal('word_timings' in result, false);
    const custom = { ...result, text_template: 'custom' };
    assert.equal(projectWith([custom], 'text').tracks[0].clips[0].color, '123456');
  }
  const eased = clip({ keyframes: { x: [{ time: 0, value: 0, easing: 'ease_in_out' }, { time: 2, value: 1, easing: 'linear' }], y: [], scale: [], opacity: [] } });
  assert.equal(projectWith([eased]).tracks[0].clips[0].keyframes.x[0].easing, 'ease_in_out');
  eased.keyframes.x[0].easing = 'expression';
  assert.throws(() => projectWith([eased]));
});

test('real API wrapper serializes explicit new actions and preserves normalized receipt fields (in-memory transport only)', async () => {
  const returned = projectWith([clip({ temperature: 0.3, color_preset: 'warm', rgb_curves: { red: [{ x: 0, y: 0.1 }, { x: 1, y: 0.9 }] } })], 'video',
    { timeline_fps: 25, time_display: 'frames', snap_enabled: false, ripple_enabled: true, source_marks: [{ source_id: 'upload_real_id', in_point: 0.125, out_point: 3 }] });
  const calls = [], receipt = { revision: 8, project: returned, can_undo: true, can_redo: false };
  const transport = api.createStudioApi('unit-task', async (path, init) => {
    calls.push({ path, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(init.body) : null });
    assert.equal(init?.credentials, 'include');
    assert.equal(new Headers(init?.headers).has('Authorization'), false);
    return plain(receipt); // Not a server geometry simulation, only schema/serialization.
  });
  const proposed = [timeline.buildDeleteAction(returned.tracks[0].clips[0], true), ...['ordinary', 'ripple', 'slip', 'roll', 'slide'].map(mode =>
    timeline.buildTrimAction(mode, returned.tracks[0].clips[0], { trim: 0.125, duration: 2, at: 1 }, 25))];
  for (const action of proposed) {
    const next = await transport.action(7, action);
    assert.deepEqual(plain(next.project), plain(returned));
    assert.deepEqual(calls.at(-1), { path: '/api/tasks/unit-task/studio/actions', method: 'POST', body: { expected_revision: 7, ...plain(action) } });
  }
  await transport.save(7, returned);
  assert.deepEqual(calls.at(-1).body.project, plain(returned));
  await transport.importProject(7, returned);
  assert.deepEqual(calls.at(-1).body.project.workspace, plain(returned.workspace));
  const count = calls.length;
  await assert.rejects(transport.save(7, { ...returned, workspace: { ...returned.workspace, snap_enabled: 'true' } }), /snap_enabled/);
  await assert.rejects(transport.importProject(7, { ...returned, arbitrary_filter: 'forbidden' }), /project/);
  assert.equal(calls.length, count, 'Invalid typed drafts must not reach even the in-memory transport.');
  receipt.project = { ...returned, workspace: { ...returned.workspace, timeline_fps: 120 } };
  await assert.rejects(transport.project(), /timeline_fps/);
  receipt.project = plain(returned); receipt.project.tracks[0].clips[0].rgb_curves = { red: [] };
  await assert.rejects(transport.project(), /RGB/);
});

test('Studio JSX parses and retains declared E2E/control anchors (static source check only)', () => {
  const source = readFileSync(new URL('../src/components/Studio.tsx', import.meta.url), 'utf8');
  const parsed = ts.transpileModule(source, { fileName: 'Studio.tsx', reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve } });
  assert.equal(parsed.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error).length ?? 0, 0);
  for (const anchor of ['timecontrols', 'source', 'trim-modes', 'inspector', 'palette', 'export', 'jobs', 'history']) {
    assert.ok(source.includes(`data-studio-section="${anchor}"`), anchor);
  }
  for (const attribute of ['data-timeline-fps', 'data-time-display', 'data-snap-target', 'data-action', 'data-source-mark',
    'data-rgb-channel', 'data-text-template', 'data-text-animation', 'data-preview-kind']) assert.ok(source.includes(attribute));
  assert.ok(source.includes('当前源帧设入点')); assert.ok(source.includes('当前源帧设出点'));
  assert.ok(source.includes('media.currentTime')); assert.ok(source.includes('buildTrimAction(editMode'));
  assert.ok(source.includes('buildDeleteAction(selection.clip, rippleEnabled)'));
  assert.equal(source.includes('data-cloud-navigation'), false, 'Retired cloud/account navigation must not return.');
  assert.ok(source.includes('这是原始素材播放，不包含本次时间线裁剪、调色、叠加或静音效果'));
});