import assert from 'node:assert/strict';
import test from 'node:test';
import { loader, fixture, harness, flat, text } from './test-v2-recording-affordance.mjs';

const load = loader();
const { qualitySummary } = load('src/lib/qualitySummary.ts');
const { parseReport } = load('src/lib/workbenchApi.ts');
const plain = v => JSON.parse(JSON.stringify(v));
const saved = { target_cpm: 290, min_cpm: 253.75, max_cpm: 326.25 };
function context(target = saved) {
  const ctx = fixture('mixed');
  ctx.preferences.pacing = 'slow';
  ctx.report.quality = { blocking_issue_count: 0, warning_count: 0, issues: [], metrics: {
    narration_rate_target: target, narration_speaking_rate_min_cpm: 201,
    narration_speaking_rate_max_cpm: 299, narration_overall_speaking_rate_cpm: 255,
  } };
  return ctx;
}
test('saved QC target/window wins over current preferences and measured CPM, including historical/cache-only reads', () => {
  for (const historical of [false, true]) for (const withContext of [false, true]) {
    const ctx = context(), report = parseReport(ctx.report, ctx.task_id), before = JSON.stringify(ctx);
    const s = qualitySummary(report, withContext ? ctx : null, historical);
    assert.match(s.rate, /目标：290 字\/分钟/);
    assert.match(s.rate, /配置窗口：253.75–326.25 字\/分钟/);
    assert.match(s.rate, /所看版本.*保存.*质量检查/);
    assert.doesNotMatch(s.rate, /230|201|299|255|当前已保存偏好/);
    assert.equal(JSON.stringify(ctx), before);
  }
});
test('actual parser keeps only the three public target numbers and never mutates the input', () => {
  const ctx = context({ ...saved, provider: 'private-sentinel', path: 'private-sentinel' });
  const before = JSON.stringify(ctx);
  const parsed = parseReport(ctx.report, ctx.task_id);
  assert.deepEqual(plain(parsed.quality.metrics.narration_rate_target), saved);
  assert.equal(JSON.stringify(ctx), before);
});
test('malformed optional target is unknown, not coerced or a reason to lose an old report', () => {
  const invalid = [null, [], '290', true, {}, { target_cpm: 290 },
    ...['target_cpm', 'min_cpm', 'max_cpm'].flatMap(key => [undefined, null, '290', true, -1, 0, NaN, Infinity]
      .map(value => ({ ...saved, [key]: value }))),
    { ...saved, min_cpm: 291 }, { ...saved, max_cpm: 289 }, { ...saved, min_cpm: 400 }];
  for (const target of invalid) {
    const ctx = context(target), parsed = parseReport(ctx.report, ctx.task_id);
    assert.equal(parsed.quality.metrics.narration_rate_target, undefined);
    for (const report of [ctx.report, parsed]) {
      const s = qualitySummary(report, ctx, true);
      assert.match(s.rate, /目标：未提供.*配置窗口：未提供/);
      assert.doesNotMatch(s.rate, /253.75|326.25|230/);
    }
  }
});
test('zero tolerance is valid; bounds are not rounded to whole CPM', () => {
  const ctx = context({ target_cpm: 265, min_cpm: 265, max_cpm: 265 });
  assert.match(qualitySummary(ctx.report, null, true).rate, /265–265/);
});
test('historical missing contract has an honest reason and never borrows latest preferences', () => {
  const ctx = context(); delete ctx.report.quality.metrics.narration_rate_target;
  assert.match(qualitySummary(ctx.report, ctx, true).rate, /此版本未保存目标语速窗口/);
  assert.doesNotMatch(qualitySummary(ctx.report, ctx, true).rate, /230|265|290|201|299/);
});
test('C, recording-only and mismatched metric mode never advertise a saved TTS target', () => {
  const ctx = context(); ctx.report.mode = 'original';
  assert.equal(qualitySummary(ctx.report, ctx, true).rate, '仅原声，不适用旁白目标语速');
  ctx.report.mode = 'mixed'; ctx.report.rows[0].audio_source = 'recording';
  assert.match(qualitySummary(ctx.report, ctx, true).rate, /无 AI 旁白/);
  delete ctx.report.rows[0].audio_source; ctx.report.quality.metrics.mode = 'original';
  assert.match(qualitySummary(ctx.report, ctx, true).rate, /配置窗口：未提供/);
});
test('normal collapsed workbench details display persisted target/window without changing server gate or callbacks', async () => {
  const h = harness(context()); h.render(); await h.settle();
  const details = flat(h.tree()).find(n => n.type === 'details' && text(n).includes('质量详情'));
  assert.ok(details); assert.ok(!details.props.open);
  assert.match(text(details), /253.75–326.25/); assert.equal(h.probe().canExport, true);
  assert.equal(h.calls.filter(c => (c.init.method ?? 'GET') !== 'GET').length, 0);
  h.stop();
});