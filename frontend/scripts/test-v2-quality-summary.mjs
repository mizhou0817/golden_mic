import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { root, loader, harness, fixture, rowFixture, flat, text } from './test-v2-recording-affordance.mjs';

const lib = resolve(root, 'src/lib/qualitySummary.ts');
const summary = (...args) => { assert.ok(existsSync(lib), 'quality summary implementation is required'); return loader()('src/lib/qualitySummary.ts').qualitySummary(...args); };
const values = s => Object.fromEntries(s.items.map(i => [i.key, i.value]));
const metrics = () => ({ narration_unit_count: 2, visual_beat_count: 3, distinct_visual_shot_count: 2, low_confidence_count: 1, fallback_count: 1 });
function reportFixture(mode = 'voiceover') {
  const ctx = fixture(mode);
  ctx.report.rows = [rowFixture({ visual_beats: [4, 5].map((id, beat_id) => ({ beat_id, shot_id: id, text: 'Synthetic beat', description: '', confidence: .6 })), media_origin: 'generated' }),
    rowFixture({ sentence_id: 8, shot_id: 5, is_fallback: true }),
    rowFixture({ sentence_id: 9, kind: 'quote', audio_kind: 'sync', shot_id: 7 })];
  ctx.report.quality = { blocking_issue_count: 12, warning_count: 19, issues: [], metrics: metrics() }; return ctx;
}
test('normal UI has collapsed quality details, not DEV-only, and preserves server gate', async () => {
  const ctx = reportFixture('mixed'), h = harness(ctx); h.render(); await h.settle();
  const details = flat(h.tree()).find(n => n.type === 'details' && text(n).includes('质量详情'));
  assert.ok(details); assert.ok(!details.props.open); assert.match(text(details), /未提供/);
  assert.equal(h.probe().canExport, true, 'quality counts cannot invent a check gate');
  assert.match(text(details), /仅旁白/); h.stop();
});
for (const mode of ['voiceover', 'mixed', 'original']) test(`mode ${mode}: stats scope, distinct IDs include generated, no real-shot/quality claim`, () => {
  const ctx = reportFixture(mode), before = JSON.stringify(ctx), s = summary(ctx.report, ctx, false);
  const v = values(s); assert.equal(v.sentences, '3'); assert.equal(v.visuals, '3'); assert.equal(v.shots, '2');
  assert.equal(v.uncertain, '1'); assert.equal(v.fallback, '1'); assert.equal(v.seenShots, '3');
  assert.match(s.scope, /仅旁白/); assert.match(s.items.find(i => i.key === 'seenShots').label, /含生成/);
  assert.doesNotMatch(JSON.stringify(s), /实测|检查通过|真实镜头/);
  if (mode === 'original') { assert.equal(s.rate, '仅原声，不适用旁白目标语速'); assert.ok(!s.rate.includes('265')); }
  else { assert.match(s.rate, /265/); assert.match(s.rate, /配置窗口：未提供/); }
  assert.equal(JSON.stringify(ctx), before);
});
for (const key of ['narration_unit_count', 'visual_beat_count', 'distinct_visual_shot_count', 'low_confidence_count', 'fallback_count']) test(`strict authoritative ${key}: invalid/missing not zero`, () => {
  const output = { narration_unit_count: 'narration', visual_beat_count: 'visuals', distinct_visual_shot_count: 'shots', low_confidence_count: 'uncertain', fallback_count: 'fallback' }[key];
  for (const invalid of [undefined, null, '2', true, -1, .5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    const ctx = reportFixture(); ctx.report.quality.metrics[key] = invalid;
    assert.equal(values(summary(ctx.report, ctx, false))[output], '未提供');
  }
  const ctx = reportFixture(); ctx.report.quality.metrics[key] = 0; assert.equal(values(summary(ctx.report, ctx, false))[output], '0');
});
test('missing report/quality stays explicitly unknown; rows never replace authoritative uncertainty counts', () => {
  const ctx = fixture(); let s = summary(ctx.report, ctx, false); assert.equal(values(s).sentences, '1');
  for (const key of ['narration', 'visuals', 'shots', 'uncertain', 'fallback']) assert.equal(values(s)[key], '未提供');
  s = summary(null, null, false); for (const i of s.items) assert.equal(i.value, '未提供'); assert.match(s.rate, /未提供/);
  ctx.report.rows[0].shot_id = null; assert.equal(values(summary(ctx.report, ctx, false)).seenShots, '未提供');
});
test('configured pacing from matching current context only; pending/latest preferences cannot leak into historical report', () => {
  for (const [pacing, target] of [['slow', 230], ['normal', 265], ['fast', 290]]) {
    const ctx = reportFixture('mixed'); ctx.preferences.pacing = pacing;
    assert.match(summary(ctx.report, ctx, false).rate, new RegExp(String(target)));
    const old = { ...ctx.report, revision: 1 }; assert.doesNotMatch(summary(old, ctx, true).rate, /230|265|290/);
    assert.match(summary(old, ctx, true).rate, /未提供/);
    assert.match(summary(old, ctx, false).rate, /未提供/);
  }
  for (const invalid of [undefined, null, 'turbo', true, 265]) { const ctx = reportFixture(); ctx.preferences.pacing = invalid; assert.match(summary(ctx.report, ctx, false).rate, /目标：未提供/); }
  const ctx = reportFixture();
  assert.match(summary({ ...ctx.report, task_id: 'other' }, ctx, false).rate, /目标：未提供/);
  assert.match(summary({ ...ctx.report, mode: 'mixed' }, ctx, false).rate, /目标：未提供/);
  assert.match(summary(ctx.report, null, true).rate, /目标：未提供/);
});
test('A/B non-TTS narration and C never advertise an irrelevant TTS target', () => {
  for (const mode of ['voiceover', 'mixed', 'original']) {
    const ctx = fixture(mode); ctx.report.rows[0].audio_kind = 'sync'; ctx.report.rows[0].audio_source = 'recording';
    const s = summary(ctx.report, ctx, false); assert.doesNotMatch(s.rate, /265|窗口/);
  }
});
test('no invented window from min/max measured rates, defaults, uncontracted target keys or stats', () => {
  const ctx = reportFixture(); Object.assign(ctx.report.quality.metrics, { narration_speaking_rate_min_cpm: 201,
    narration_speaking_rate_max_cpm: 299, narration_overall_speaking_rate_cpm: 255,
    target_chars_per_minute: 500, rate_tolerance: .25, minimum_speaking_rate_cpm: 200, maximum_speaking_rate_cpm: 300 });
  const s = summary(ctx.report, ctx, false); assert.match(s.rate, /265/); assert.match(s.rate, /配置窗口：未提供/);
  assert.doesNotMatch(s.rate, /201|299|255|500|200|300/);
});
test('legacy quality is not mislabeled as mode-filtered; metadata mismatch fails closed', () => {
  const ctx = reportFixture(); delete ctx.report.mode;
  assert.match(summary(ctx.report, null, true).scope, /旧版报告/);
  ctx.report.quality.metrics.mode = 'original'; assert.equal(values(summary(ctx.report, null, true)).visuals, '未提供');
});
test('read-only backend contract: mode-filtered stats; target window not serialized', () => {
  const backend = readFileSync(resolve(root, '../backend/quality.py'), 'utf8');
  assert.match(backend, /match_plan = \[item for item in all_plan if item.kind == "narration"\]/);
  const output = backend.slice(backend.indexOf('"schema_version": 2'));
  assert.doesNotMatch(output, /"(?:minimum_speaking_rate_cpm|maximum_speaking_rate_cpm|rate_tolerance|target_chars_per_minute)"\s*:/);
});