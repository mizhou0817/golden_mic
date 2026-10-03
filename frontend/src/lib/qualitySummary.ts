import { PACING_CPM } from './productionModes';
import { isQuoteRow, parseNarrationRateTarget } from './workbenchApi';
import type { Report, WorkbenchContext } from './workbenchApi';

const missing = '未提供';
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

/** Display-only observations of the displayed report. Never a publish gate,
 * timing measurement, source-footage authenticity claim, or draft projection. */
export function qualitySummary(report: Report | null, context: WorkbenchContext | null, historical: boolean) {
  const aligned = !!report && !!context && !historical && report.task_id === context.task_id
    && (report.revision === context.revision || report.revision === undefined && report === context.report)
    && (report.mode === undefined || context.mode === undefined || report.mode === context.mode);
  const mode = report?.mode ?? (aligned ? context?.mode : undefined);
  const metrics = report?.quality?.metrics;
  // Mode reports count narration only in backend.quality; legacy reports count
  // their complete match plan. Do not mix those with visible row/shot-ID counts.
  const compatible = object(metrics) && (metrics.mode === undefined || metrics.mode === mode);
  const metric = (key: string) => compatible && count(metrics[key]) ? String(metrics[key]) : missing;
  const seen = report?.rows.flatMap(row => row.visual_beats.length
    ? row.visual_beats.map(beat => beat.shot_id) : [row.shot_id]);
  const seenCount = seen && seen.every(count) ? String(new Set(seen).size) : missing;
  const scope = mode ? '质量报告统计仅旁白；不含原声句，镜头标识可能包含生成画面。'
    : '旧版报告统计范围为该报告的匹配单元；镜头标识可能包含生成画面。';
  const items = [
    { key: 'sentences', label: '当前所看报告 · 全部句子', value: report ? String(report.rows.length) : missing },
    { key: 'seenShots', label: '当前所看报告 · 不同镜头标识（含生成，非素材文件数）', value: seenCount },
    { key: 'narration', label: mode ? '质量报告 · 旁白单元' : '质量报告 · 匹配单元', value: metric('narration_unit_count') },
    { key: 'visuals', label: '质量报告 · 视觉节拍', value: metric('visual_beat_count') },
    { key: 'shots', label: '质量报告 · 不同视觉镜头标识（含生成）', value: metric('distinct_visual_shot_count') },
    { key: 'uncertain', label: '质量报告 · 低置信度单元（非事实核实）', value: metric('low_confidence_count') },
    { key: 'fallback', label: '质量报告 · 兜底单元', value: metric('fallback_count') },
  ];
  const hasTts = report?.rows.some(row => !isQuoteRow(row, mode)
    && ((aligned ? context?.timings.find(t => t.sentence_id === row.sentence_id)?.audio_source : undefined)
      ?? row.audio_source ?? row.audio_kind) === 'tts');
  const pacing = aligned ? context?.preferences.pacing : undefined;
  const target = pacing === 'slow' || pacing === 'normal' || pacing === 'fast' ? PACING_CPM[pacing] : null;
  const saved = compatible ? parseNarrationRateTarget(metrics.narration_rate_target) : undefined;
  const cpm = (value: number) => String(Number(value.toFixed(6)));
  // Persisted QC evidence belongs to the displayed version, even when cached or
  // historical. The current preference fallback is labelled as such, never a
  // historical window, measurement, or guarantee about a cached TTS provider.
  const rate = mode === 'original' ? '仅原声，不适用旁白目标语速'
    : report && !hasTts ? '此报告无 AI 旁白，不适用旁白目标语速'
      : saved ? `AI 旁白目标：${cpm(saved.target_cpm)} 字/分钟；配置窗口：${cpm(saved.min_cpm)}–${cpm(saved.max_cpm)} 字/分钟（所看版本保存的质量检查目标，非实测语速）。`
        : `AI 旁白目标：${target === null ? missing : `${target} 字/分钟（当前已保存偏好）`}；配置窗口：${missing}。${report ? '此版本未保存目标语速窗口，不能据当前配置回算。' : ''}`;
  return { items, scope, rate };
}