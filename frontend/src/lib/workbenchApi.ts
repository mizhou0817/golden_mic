import type { CaptionStyle, EditingPacing, EditingPreferences, QualitySummary, ReportBeatRow, ReportRow, TaskState } from '../types';
import type { ModePreferences, ProductionMode, QuoteTake, QuoteWord, SentenceKind, Speaker } from './productionModes';

/** No session bootstrap, token storage, or automatic write retries. The host owns authentication. */
export type WorkbenchRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
export interface Beat extends Omit<ReportBeatRow, 'confidence'> {
  confidence?: number | null;
  media_origin?: 'source' | 'generated';
}
export interface QuoteRange { start: number; end: number }
export interface JumpCut { after_row: number; cover: 'broll' | 'zoom' | 'hard'; shot: number | null; downgraded: boolean }
export interface Row extends Omit<ReportRow, 'confidence' | 'visual_beats' | 'kind' | 'source' | 'alt_takes' | 'trim' | 'to_narration' | 'jumpcut_before'> {
  confidence?: number | null;
  visual_beats: Beat[];
  audio_source?: 'tts' | 'sync' | 'recording';
  media_origin?: 'source' | 'generated';
  start?: number;
  end?: number;
  kind?: SentenceKind;
  source?: QuoteTake | null;
  alt_takes?: QuoteTake[];
  trim?: QuoteRange | null;
  to_narration?: boolean;
  jumpcut_before?: boolean;
}
export interface Report {
  task_id: string; rows: Row[]; quality: QualitySummary | null;
  mode?: ProductionMode; speakers?: Speaker[]; jumpcuts?: JumpCut[];
  revision?: number;
  replace_fails?: Record<string, { kind: 'none' | 'used' | 'abstract'; text: string }>;
}
/** Optional persisted report.quality.metrics contract, not measured CPM. */
export interface NarrationRateTarget { target_cpm: number; min_cpm: number; max_cpm: number }
export function parseNarrationRateTarget(value: unknown): NarrationRateTarget | undefined {
  if (!object(value) || !finite(value.target_cpm) || !finite(value.min_cpm) || !finite(value.max_cpm)
    || value.min_cpm <= 0 || value.min_cpm > value.target_cpm || value.target_cpm > value.max_cpm) return undefined;
  return { target_cpm: value.target_cpm, min_cpm: value.min_cpm, max_cpm: value.max_cpm };
}
export interface Timing {
  sentence_id: number; text: string; start: number; end: number; duration: number;
  audio_kind: 'tts' | 'sync'; audio_source: 'tts' | 'sync' | 'recording';
  recording_id?: string; transcript_verified?: boolean; uploaded_revision?: number;
  speaking_rate_cpm?: number | null; integrated_lufs?: number | null; gap_after?: number;
}
export interface Shot {
  shot_id: number; source_index: number; source_scene_index: number;
  start: number; end: number; duration: number; status: string;
  media_origin: 'source' | 'generated'; description: string | null;
  scene_type?: string; subjects?: string[]; actions?: string[]; keywords?: string[];
  ocr_texts?: string[]; entities?: string[]; source_transcript?: string;
  quality?: unknown; unused: boolean; used_by_sentence_id: number | null;
  selectable: boolean; thumb_url: string;
}
export interface Version { revision: number; label: string; created_at: string }
export interface WorkbenchContext {
  task_id: string; revision: number; status: TaskState; script: string;
  preferences: ModePreferences; last_operation_error: string | null;
  mode?: ProductionMode; speakers?: Speaker[]; jumpcuts?: JumpCut[];
  /** Explicit render evidence only. Unknown never means a clean, unburned video. */
  lower_thirds_burned?: boolean;
  report: Report; timings: Timing[];
  current_shots: { sentence_id: number; shot_ids: number[] }[];
  shots: Shot[]; versions: Version[];
}
export interface SentenceEdit {
  sentence_id: number; text?: string; shot_id?: number; instruction?: string; recording_id?: string;
}
export type WorkbenchPreferences = Required<Pick<EditingPreferences, 'pacing' | 'caption_style' | 'enhance_speech'>>;
export type WorkbenchPreferenceChanges = Partial<WorkbenchPreferences>;
export interface EditBatch extends WorkbenchPreferenceChanges {
  expected_revision: number; keep_sentence_ids: number[]; edits: SentenceEdit[];
  quote_trims?: (QuoteRange & { id: number })[];
  quote_takes?: { id: number; take_id: string }[];
  to_narration?: number[];
  speakers?: Speaker[];
}
export interface WorkbenchCheck {
  key: string; code: string; level: 0 | 1 | 2; message: string;
  sentence_id: number | null; action: string; checked: boolean; confirmable?: boolean;
}
export interface WorkbenchChecks {
  revision: number; blocking_count: number; pending_count: number; passed: boolean; checks: WorkbenchCheck[];
}
export interface QuoteWave { interval_ms: 20; samples: number[]; start: number; end: number }
export interface ApplyStep { kind: 'remix' | 'recompose' | 'replace'; stages: number[]; state: 'pending' | 'running' | 'succeeded' | 'failed'; row_id?: number }
export interface EditReceipt { task_id: string; revision: number; current_revision: number; status: 'queued'; operation_id?: string; plan?: { steps: ApplyStep[] } }
export interface RecordingReceipt {
  recording_id: string; sentence_id: number; revision: number; duration: number; size: number;
  audio_source: 'recording'; transcript_verified: boolean;
  speaking_rate_cpm?: number | null; words?: RecordingWord[];
}
export interface RecordingWord { text: string; start: number; end: number }
export const MAX_RECORDING_BYTES = 20 * 1024 * 1024;
export const EXPORT_FORMATS = ['mp4', 'mov', 'mkv', 'avi', 'gif', 'mp3', 'wav', 'png', 'srt', 'ass'] as const;
export const SIMPLE_EXPORT_FORMATS = ['mp4', 'gif', 'mp3', 'srt', 'png'] as const;
export type ExportFormat = typeof EXPORT_FORMATS[number];
export interface ExportOptions {
  format: ExportFormat; resolution: 360 | 720 | 1080; fps: 24 | 25 | 30 | 60;
  aspect: '16:9' | '9:16' | '1:1'; subtitles: 'standard' | 'large' | 'none';
  video_bitrate_kbps: number; audio_bitrate_kbps: 96 | 128 | 192 | 256 | 320;
  frame_time?: number | null;
}
export const DEFAULT_EXPORT: ExportOptions = {
  format: 'mp4', resolution: 1080, fps: 30, aspect: '16:9', subtitles: 'standard',
  video_bitrate_kbps: 4000, audio_bitrate_kbps: 192,
};
export interface ExportJob {
  id: string; revision: number; pipeline_revision: number;
  export_id?: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
  output_id: string | null; error: string | null; qc: string; disclosure: boolean;
  options: ExportOptions;
  result?: { file: string; bytes: number; duration: number; png_semantics?: string; gif_fps_semantics?: string };
}
export function exportSupport(format: ExportFormat) {
  return {
    geometry: !['mp3', 'wav', 'srt', 'ass'].includes(format),
    fps: ['mp4', 'mov', 'mkv', 'avi', 'gif'].includes(format),
    videoBitrate: ['mp4', 'mov', 'mkv', 'avi'].includes(format),
    audioBitrate: ['mp4', 'mov', 'mkv', 'avi', 'mp3'].includes(format),
    subtitles: ['mp3', 'wav', 'srt'].includes(format) ? ['standard']
      : format === 'ass' ? ['standard', 'large'] : ['standard', 'large', 'none'],
  };
}
export function normalizeExport(options: ExportOptions): ExportOptions {
  const support = exportSupport(options.format);
  return { ...options,
    ...(options.format !== 'png' ? { frame_time: null } : {}),
    ...(!support.geometry ? { resolution: 1080 as const, aspect: '16:9' as const } : {}),
    ...(!support.fps ? { fps: 30 as const } : {}),
    ...(!support.videoBitrate ? { video_bitrate_kbps: 4000 } : {}),
    ...(!support.audioBitrate ? { audio_bitrate_kbps: 192 as const } : {}),
    subtitles: support.subtitles.includes(options.subtitles) ? options.subtitles : 'standard',
  };
}
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const hexId = (v: unknown): v is string => typeof v === 'string' && /^[a-f0-9]{32}$/.test(v);
const text = (v: unknown, max: number): v is string => typeof v === 'string' && v.length <= max && !/[\u0000-\u001f\u007f]/.test(v);
const modeValue = (v: unknown): v is ProductionMode => v === 'voiceover' || v === 'mixed' || v === 'original';
const rangeValue = (v: unknown): v is QuoteRange => object(v) && finite(v.start) && finite(v.end) && v.start >= 0 && v.end > v.start;

/** Never surface provider names, machine state codes, paths or raw server errors. */
export function workbenchErrorMessage(error: unknown): string {
  const status = object(error) ? error.status : undefined;
  if (status === 409) return '作品或检查结果已有变化。请刷新后重新核对；刚才的操作未确认成功，待修改内容仍保留。';
  if (status === 401 || status === 403) return '当前无法访问这条作品，请重新打开作品并核对访问权限。';
  if (status === 413) return '提交内容超过限制，请缩短文字或录音后再试。';
  const value = object(error) && typeof error.message === 'string' ? error.message : '';
  return value && /[\u3400-\u9fff]/u.test(value) && !/[\u0000-\u001f]|ASR|TTS|[A-Za-z]+_[A-Za-z_]+|Traceback|https?:|[A-Z]:\\|Bearer/i.test(value)
    ? value : '操作未确认完成，请刷新核对作品状态；修改仍保留，不要重复提交。';
}

/** A bounded, known pipeline plan, not permission to accept any future revision. */
export function parseApplySteps(value: unknown, pendingOnly = false): ApplyStep[] {
  if (!Array.isArray(value) || !value.length || value.length > 201) throw new Error('修改计划不完整，请刷新核对；不要重复提交。');
  const steps: ApplyStep[] = value.map((step, index) => {
    if (!object(step) || !['remix', 'recompose', 'replace'].includes(String(step.kind))
      || !Array.isArray(step.stages) || !['pending', 'running', 'succeeded', 'failed'].includes(String(step.state))
      || pendingOnly && step.state !== 'pending') throw new Error('修改计划不兼容，请刷新核对；不要重复提交。');
    const stages = JSON.stringify(step.stages);
    if (step.kind === 'replace' ? !integer(step.row_id) || stages !== '[6,9,10]'
      : index !== 0 || step.row_id !== undefined || !['[6,7,8,9,10]', '[7,8,9,10]', '[8,9,10]', '[9,10]'].includes(stages)) {
      throw new Error('修改步骤不匹配，请刷新核对；不要重复提交。');
    }
    return { kind: step.kind as ApplyStep['kind'], stages: [...step.stages] as number[], state: step.state as ApplyStep['state'],
      ...(step.kind === 'replace' ? { row_id: step.row_id as number } : {}) };
  });
  const rows = steps.filter(s => s.kind === 'replace').map(s => s.row_id!);
  if (new Set(rows).size !== rows.length || rows.some((id, i) => i > 0 && id <= rows[i - 1])) throw new Error('修改步骤重复或顺序不匹配。');
  return steps;
}

export function parseRecordingReceipt(v: unknown, sentenceId: number, revision: number): RecordingReceipt {
  if (!object(v) || !hexId(v.recording_id) || v.sentence_id !== sentenceId || v.revision !== revision
    || !finite(v.duration) || v.duration <= 0 || v.duration > 120 || typeof v.transcript_verified !== 'boolean'
    || !integer(v.size) || v.size <= 0 || v.size > MAX_RECORDING_BYTES || v.audio_source !== 'recording'
    || v.speaking_rate_cpm != null && (!finite(v.speaking_rate_cpm) || v.speaking_rate_cpm <= 0)
    || v.words !== undefined && (!Array.isArray(v.words) || v.words.length > 20_000)) throw new Error('录音回执不兼容，未应用录音。');
  let end = 0;
  const words = v.words === undefined ? undefined : (v.words as unknown[]).map(w => {
    if (!object(w) || !text(w.text, 2000) || !w.text.trim() || !finite(w.start) || !finite(w.end)
      || w.start < end || w.end <= w.start || w.end > (v.duration as number)) throw new Error('录音词时间不完整，未应用录音。');
    end = w.end; return { text: w.text, start: w.start, end: w.end };
  });
  return { recording_id: v.recording_id, sentence_id: sentenceId, revision, duration: v.duration, size: v.size,
    audio_source: 'recording', transcript_verified: v.transcript_verified,
    ...(v.speaking_rate_cpm !== undefined ? { speaking_rate_cpm: v.speaking_rate_cpm as number | null } : {}),
    ...(words !== undefined ? { words } : {}) };
}

/** Validate source identity, but let incomplete word evidence disable trimming
 * rather than making deletion/replacement of an otherwise valid quote impossible. */
export function parseQuoteTake(value: unknown): QuoteTake {
  if (!object(value) || !text(value.take_id, 200) || !value.take_id.trim()
    || !text(value.upload_id, 128) || !/^[A-Za-z0-9_-]+$/.test(value.upload_id)
    || !rangeValue(value) || value.end > 604_800 || !text(value.speaker_id, 128)
    || !text(value.asr_text, 32_000) || !finite(value.score) || value.score < 0 || value.score > 1
    || value.snr_db != null && !finite(value.snr_db)
    || value.precision !== 'word' && value.precision !== 'segment'
    || value.matched_text !== undefined && !text(value.matched_text, 32_000)
    || value.segment_ids !== undefined && (!Array.isArray(value.segment_ids) || value.segment_ids.length > 1000
      || !value.segment_ids.every(id => text(id, 128) && !!id.trim()))) {
    throw new Error('原声片段资料不完整，请刷新后重新选择；未应用替换。');
  }
  const validWords = Array.isArray(value.words) && value.words.length <= 20_000 && value.words.every(w => object(w)
    && text(w.w, 2000) && !!w.w.trim() && finite(w.s) && finite(w.e));
  // Never partially keep a malformed word list or create clocks from the text.
  const words: QuoteWord[] = validWords ? (value.words as QuoteWord[]).map(w => ({ w: w.w, s: w.s, e: w.e })) : [];
  return { take_id: value.take_id, upload_id: value.upload_id, start: value.start, end: value.end,
    speaker_id: value.speaker_id, asr_text: value.asr_text, score: value.score, snr_db: (value.snr_db ?? null) as number | null,
    words, precision: value.precision,
    ...(value.matched_text !== undefined ? { matched_text: value.matched_text as string } : {}),
    ...(value.segment_ids !== undefined ? { segment_ids: [...value.segment_ids] as string[] } : {}) };
}
function parseSpeakers(value: unknown): Speaker[] {
  if (!Array.isArray(value) || value.length > 1000 || !value.every(p => object(p) && text(p.id, 128) && !!p.id.trim()
    && text(p.name, 80) && text(p.title, 160) && text(p.auto_label, 160) && integer(p.appearances) && finite(p.seconds) && p.seconds >= 0)
    || new Set(value.map(p => p.id)).size !== value.length) throw new Error('说话人资料不完整，请刷新后再填写。');
  return value.map(p => ({ id: p.id, name: p.name, title: p.title, auto_label: p.auto_label, appearances: p.appearances, seconds: p.seconds }));
}
function parseJumpCuts(value: unknown): JumpCut[] {
  if (!Array.isArray(value) || value.length > 2048 || !value.every(c => object(c) && integer(c.after_row)
    && ['broll', 'zoom', 'hard'].includes(String(c.cover)) && (c.shot == null || integer(c.shot))
    && typeof c.downgraded === 'boolean' && (c.cover !== 'broll' || integer(c.shot)))
    || new Set(value.map(c => c.after_row)).size !== value.length) throw new Error('跳切处理资料不完整，请刷新后核对。');
  return value.map(c => ({ after_row: c.after_row, cover: c.cover, shot: c.shot ?? null, downgraded: c.downgraded }));
}
export function isQuoteRow(row: Row, mode: ProductionMode = 'voiceover'): boolean {
  return row.kind === 'quote' || row.kind === undefined && !row.to_narration && (!!row.source || mode === 'original');
}

export function canConfirmCheck(check: WorkbenchCheck): boolean {
  return check.level === 1 && check.confirmable !== false
    || check.level === 0 && check.code === 'generated_media' && check.confirmable === true;
}
export function parseChecks(value: unknown): WorkbenchChecks {
  if (!object(value) || !integer(value.revision) || !integer(value.blocking_count) || !integer(value.pending_count)
    || typeof value.passed !== 'boolean' || !Array.isArray(value.checks) || value.checks.length > 10_000
    || !value.checks.every(c => object(c) && text(c.key, 2048) && !!c.key.trim() && text(c.code, 160) && !!c.code
      && [0, 1, 2].includes(c.level as number) && text(c.message, 8000) && !!c.message.trim()
      && (c.sentence_id == null || integer(c.sentence_id)) && (c.action == null || text(c.action, 100))
      && typeof c.checked === 'boolean' && (c.confirmable === undefined || typeof c.confirmable === 'boolean'))
    || new Set(value.checks.map(c => c.key)).size !== value.checks.length
    || value.passed && (value.blocking_count !== 0 || value.pending_count !== 0)) {
    throw new Error('未读到有效的发布前检查，暂时不能导出。请重新读取检查结果。');
  }
  const result = { ...value, checks: value.checks.map(c => ({ ...c, sentence_id: c.sentence_id ?? null, action: c.action ?? '' })) } as unknown as WorkbenchChecks;
  if (result.passed && result.checks.some(c => c.level === 0 && !canConfirmCheck(c) || c.level < 2 && !c.checked)) {
    throw new Error('检查结果前后不一致，暂时不能导出。请刷新核对。');
  }
  return result;
}
/** Permission is a server result, never reconstructed from local issue counts. */
export function serverAllowsExport(checks: WorkbenchChecks | null, revision: number): boolean {
  return checks?.revision === revision && checks.passed === true && checks.blocking_count === 0 && checks.pending_count === 0;
}
export function confirmationKeys(checks: WorkbenchChecks, key: string, checked: boolean): string[] {
  const check = checks.checks.find(c => c.key === key);
  if (!check || !canConfirmCheck(check)) throw new Error('这项检查不能直接勾选，请先处理对应的问题。');
  return checks.checks.filter(c => canConfirmCheck(c) && (c.key === key ? checked : c.checked)).map(c => c.key);
}
export function parseQuoteWave(value: unknown): QuoteWave {
  if (!object(value) || value.interval_ms !== 20 || !rangeValue(value) || !Array.isArray(value.samples)
    || !value.samples.length || value.samples.length > 300_000 || !value.samples.every(s => finite(s) && Math.abs(s) <= 1)
    || Math.abs(value.samples.length - Math.ceil((value.end - value.start) * 1000 / 20)) > 1) {
    throw new Error('没有读到与原声时间对应的真实波形；仍可按可靠的词时间选段。');
  }
  return { interval_ms: 20, samples: [...value.samples] as number[], start: value.start, end: value.end };
}

// v2_editing.Person; mode_rules currently has no display-name/role limits.
// Keep submission rules separate from permissive historical report reads.
export const SPEAKER_FIELD_LIMITS = { name: 8, title: 12 } as const;
export function speakerFieldProblem(value: unknown, field: keyof typeof SPEAKER_FIELD_LIMITS, personNumber: number): string {
  const label = `说话人 ${personNumber} 的${field === 'name' ? '姓名' : '身份'}`;
  if (typeof value !== 'string') return `${label}须为文字，请重新填写；也可以留空。`;
  // ApplyRequest rejects ord(c) < 32, not all Unicode control categories.
  if (/[\u0000-\u001f]/.test(value)) return `${label}不能包含换行或控制字符，请删除这些字符后再应用。`;
  // Python len counts code points, unlike JS length/native input maxlength.
  if (Array.from(value).length > SPEAKER_FIELD_LIMITS[field]) {
    return `${label}最多 ${SPEAKER_FIELD_LIMITS[field]} 字，请缩短后再应用；待修改内容仍保留。`;
  }
  return '';
}
function validateSpeakerSubmission(value: unknown): void {
  if (!Array.isArray(value) || value.length > 100 || !value.every(p => object(p) && text(p.id, 128) && !!p.id.trim()
    && text(p.auto_label, 160) && integer(p.appearances) && finite(p.seconds) && p.seconds >= 0)
    || new Set(value.map(p => p.id)).size !== value.length) throw new Error('说话人资料不完整或超过 100 位，请核对后再应用。');
  value.forEach((person, index) => {
    for (const field of ['name', 'title'] as const) {
      const problem = speakerFieldProblem(person[field], field, index + 1);
      if (problem) throw new Error(problem);
    }
  });
}

/** The UI and batch boundary both reject conflicting quote/narration operations. */
export function validateEditBatch(batch: EditBatch, mode?: ProductionMode): void {
  const trims = batch.quote_trims ?? [], takes = batch.quote_takes ?? [], conversions = batch.to_narration ?? [];
  const ids = [...batch.edits.map(e => e.sentence_id), ...trims.map(t => t.id), ...takes.map(t => t.id), ...conversions];
  if (!integer(batch.expected_revision) || !batch.keep_sentence_ids.length || batch.keep_sentence_ids.length > 200
    || !batch.keep_sentence_ids.every(integer) || new Set(batch.keep_sentence_ids).size !== batch.keep_sentence_ids.length
    || !ids.every(id => integer(id) && batch.keep_sentence_ids.includes(id)) || new Set(ids).size !== ids.length
    || !trims.every(rangeValue) || !takes.every(t => text(t.take_id, 200) && !!t.take_id.trim())) {
    throw new Error('同一句只能选择一种修改，删除的句子不能同时修改；请检查待应用列表。');
  }
  if (mode === 'original' && (batch.edits.length || conversions.length || batch.pacing !== undefined)) {
    throw new Error('只用原声模式不能改字、录音、换画面、转旁白或调整播音速度。');
  }
  if (mode === 'voiceover' && (trims.length || takes.length || conversions.length || batch.speakers !== undefined)) {
    throw new Error('这条作品是配音模式，不能提交原声片段修改。');
  }
  if (batch.speakers !== undefined) validateSpeakerSubmission(batch.speakers);
  if (batch.edits.some(e => e.text !== undefined && (!text(e.text, 2000) || !e.text.trim())
      || e.instruction !== undefined && (!text(e.instruction, 500) || !e.instruction.trim())
      || e.recording_id !== undefined && !hexId(e.recording_id))) throw new Error('文字、画面要求或说话人资料超过限制，请缩短后再提交。');
}
export function simpleExportBody(options: ExportOptions) {
  const normalized = normalizeExport(options);
  if (!(SIMPLE_EXPORT_FORMATS as readonly string[]).includes(normalized.format)) throw new Error('请选择视频、动图、音频、字幕或封面图。');
  if (normalized.format === 'png' && (!finite(options.frame_time) || options.frame_time < 0)) throw new Error('请先在播放器选择要导出的画面。');
  return { fmt: normalized.format, aspect: normalized.aspect, res: `${normalized.resolution}p`,
    sub: normalized.subtitles === 'standard' ? 'std' : normalized.subtitles === 'large' ? 'big' : 'none',
    ...(normalized.format === 'png' ? { frame_seconds: options.frame_time } : {}) };
}

export interface ApplyBody {
  expected_revision: number; deleted: number[];
  edits: Record<string, string>; voices: Record<string, string>;
  pacing?: EditingPacing; trims: Record<string, { t0: number; t1: number }>;
  takes: Record<string, string>; to_narration: number[];
  speakers: Record<string, { name: string; role: string }>; replaces: Record<string, { instruction: string }>;
}
export function applyBody(batch: EditBatch, allRowIds: readonly number[], mode: ProductionMode): ApplyBody {
  validateEditBatch(batch, mode);
  if (batch.keep_sentence_ids.some(id => !allRowIds.includes(id))) throw new Error('句子版本不匹配，请刷新。');
  if (batch.edits.some(e => e.shot_id !== undefined)) throw new Error('请按画面描述选择候选，不提交镜头编号。');
  if (batch.caption_style !== undefined || batch.enhance_speech !== undefined) throw new Error('旧偏好草稿不适用于这次修改，请撤销后重新选择。');
  return { expected_revision: batch.expected_revision, deleted: allRowIds.filter(id => !batch.keep_sentence_ids.includes(id)),
    edits: Object.fromEntries(batch.edits.filter(e => e.text !== undefined).map(e => [String(e.sentence_id), e.text!])),
    voices: Object.fromEntries(batch.edits.filter(e => e.recording_id !== undefined).map(e => [String(e.sentence_id), e.recording_id!])),
    ...(batch.pacing !== undefined ? { pacing: batch.pacing } : {}),
    trims: Object.fromEntries((batch.quote_trims ?? []).map(t => [String(t.id), { t0: t.start, t1: t.end }])),
    takes: Object.fromEntries((batch.quote_takes ?? []).map(t => [String(t.id), t.take_id])),
    to_narration: batch.to_narration ?? [], speakers: Object.fromEntries((batch.speakers ?? []).map(p => [p.id, { name: p.name, role: p.title }])),
    replaces: Object.fromEntries(batch.edits.filter(e => e.instruction !== undefined).map(e => [String(e.sentence_id), { instruction: e.instruction! }])) };
}
export function recordingLimit(duration: number): number { return Math.min(119, Math.max(1, duration * 2 + 3)); }
export function rowStart(row: Row, timings: readonly Timing[]): number | null {
  const timing = timings.find(t => t.sentence_id === row.sentence_id);
  const start = timing?.start ?? row.start;
  return finite(start) && start >= 0 ? start : null;
}

export interface SpeakerCue { speaker_id: string; sentence_id: number; start: number; end: number }
/** Absolute, current final-film clocks only. Never use source clocks, summed
 * durations or the selected sentence to pretend the video is playing. */
export function speakerCues(rows: readonly Row[], timings: readonly Timing[], mode: ProductionMode): SpeakerCue[] {
  const byId = new Map(rows.map(row => [row.sentence_id, row]));
  const lastEnd = new Map<string, number>(), cues: SpeakerCue[] = [];
  for (const timing of [...timings].sort((a, b) => a.start - b.start)) {
    const row = byId.get(timing.sentence_id), id = row?.source?.speaker_id;
    if (!row || !isQuoteRow(row, mode) || !id || timing.audio_source !== 'sync'
      || !finite(timing.start) || !finite(timing.end) || timing.start < 0 || timing.end <= timing.start) continue;
    const previousEnd = lastEnd.get(id);
    if (previousEnd === undefined || timing.start - previousEnd >= 60) {
      cues.push({ speaker_id: id, sentence_id: row.sentence_id, start: timing.start, end: Math.min(timing.end, timing.start + 2.5) });
    }
    lastEnd.set(id, Math.max(previousEnd ?? 0, timing.end));
  }
  return cues;
}

/** Only omitted legacy options have defaults; malformed persisted values must not look saved. */
export function readWorkbenchPreferences(value: unknown): WorkbenchPreferences {
  if (!object(value) || typeof value.pacing !== 'string' || !['slow', 'normal', 'fast'].includes(value.pacing)
    || (value.caption_style !== undefined && (typeof value.caption_style !== 'string' || !['news', 'big', 'none'].includes(value.caption_style)))
    || (value.enhance_speech !== undefined && typeof value.enhance_speech !== 'boolean')) {
    throw new Error('没有读到有效的语速、字幕样式和降噪开关，请刷新作品后再修改。');
  }
  return { pacing: value.pacing as EditingPacing, caption_style: (value.caption_style ?? 'news') as CaptionStyle,
    enhance_speech: value.enhance_speech ?? false };
}

/** The three editable keys only. false is an explicit edit, not an absent value. */
export function diffWorkbenchPreferences(saved: Pick<EditingPreferences, 'pacing' | 'caption_style' | 'enhance_speech'>, draft: WorkbenchPreferenceChanges): WorkbenchPreferenceChanges {
  const current = readWorkbenchPreferences(saved);
  const changes: WorkbenchPreferenceChanges = {};
  if (draft.pacing !== undefined && draft.pacing !== current.pacing) changes.pacing = draft.pacing;
  if (draft.caption_style !== undefined && draft.caption_style !== current.caption_style) changes.caption_style = draft.caption_style;
  if (draft.enhance_speech !== undefined && draft.enhance_speech !== current.enhance_speech) changes.enhance_speech = draft.enhance_speech;
  return changes;
}
export function hasWorkbenchPreferenceChanges(changes: WorkbenchPreferenceChanges): boolean {
  return changes.pacing !== undefined || changes.caption_style !== undefined || changes.enhance_speech !== undefined;
}

export function parseReport(value: unknown, taskId: string): Report {
  if (!object(value) || value.task_id !== taskId || !Array.isArray(value.rows)
    || !value.rows.every(r => object(r) && integer(r.sentence_id) && typeof r.sentence === 'string'
      && finite(r.duration) && r.duration >= 0 && (r.shot_id === null || integer(r.shot_id))
      && (r.confidence == null || (finite(r.confidence) && r.confidence >= 0 && r.confidence <= 1))
      && typeof r.is_fallback === 'boolean' && ['tts', 'sync'].includes(String(r.audio_kind))
      && Array.isArray(r.visual_beats) && r.visual_beats.every(b => object(b) && integer(b.beat_id)
        && integer(b.shot_id) && typeof b.text === 'string'
        && (b.confidence == null || (finite(b.confidence) && b.confidence >= 0 && b.confidence <= 1))))) {
    throw new Error('这条作品的句子或画面报告不完整，请刷新后重新读取。');
  }
  if (new Set(value.rows.map(r => r.sentence_id)).size !== value.rows.length) throw new Error('报告包含重复句子标识。');
  const q = value.quality;
  if (q != null && (!object(q) || !integer(q.blocking_issue_count) || !integer(q.warning_count)
    || !object(q.metrics) || !Array.isArray(q.issues) || !q.issues.every(i => object(i)
      && typeof i.code === 'string' && typeof i.message === 'string' && ['error', 'warning'].includes(String(i.severity))))) {
    throw new Error('质量报告响应不完整，不能推断检查通过。');
  }
  if (value.mode !== undefined && !modeValue(value.mode)) throw new Error('作品模式无法识别，请刷新报告。');
  if (value.revision !== undefined && !integer(value.revision)) throw new Error('作品版本无效，请刷新报告。');
  const rows = value.rows.map(r => {
    if (r.kind !== undefined && r.kind !== 'quote' && r.kind !== 'narration'
      || r.trim != null && !rangeValue(r.trim)
      || r.to_narration !== undefined && typeof r.to_narration !== 'boolean'
      || r.jumpcut_before !== undefined && typeof r.jumpcut_before !== 'boolean'
      || r.alt_takes !== undefined && (!Array.isArray(r.alt_takes) || r.alt_takes.length > 100)) {
      throw new Error('原声句资料不完整，请刷新报告后再编辑。');
    }
    return { ...r, ...(r.source != null ? { source: parseQuoteTake(r.source) } : {}),
      ...(r.alt_takes !== undefined ? { alt_takes: r.alt_takes.map(parseQuoteTake) } : {}) };
  });
  // Invalid optional display evidence stays unknown; do not reject an otherwise
  // usable historical report or pass arbitrary nested diagnostic fields through.
  let quality = q ?? null;
  if (object(q) && object(q.metrics)) {
    const { narration_rate_target, ...metrics } = q.metrics;
    const target = parseNarrationRateTarget(narration_rate_target);
    quality = { ...q, metrics: { ...metrics, ...(target ? { narration_rate_target: target } : {}) } };
  }
  return { ...value, rows, quality,
    ...(value.speakers !== undefined ? { speakers: parseSpeakers(value.speakers) } : {}),
    ...(value.jumpcuts !== undefined ? { jumpcuts: parseJumpCuts(value.jumpcuts) } : {}) } as unknown as Report;
}
export function parseContext(value: unknown, taskId: string): WorkbenchContext {
  if (!object(value) || value.task_id !== taskId || !integer(value.revision)
    || !['queued', 'running', 'done', 'failed', 'cancelled'].includes(String(value.status))
    || typeof value.script !== 'string' || !object(value.preferences)
    || !['slow', 'normal', 'fast'].includes(String(value.preferences.pacing))
    || !Array.isArray(value.timings) || !value.timings.every(t => object(t) && integer(t.sentence_id)
      && finite(t.start) && t.start >= 0 && finite(t.end) && t.end <= 604800 && t.end > t.start && finite(t.duration) && t.duration > 0
      && (t.speaking_rate_cpm == null || finite(t.speaking_rate_cpm) && t.speaking_rate_cpm > 0)
      && (t.integrated_lufs == null || finite(t.integrated_lufs))
      && (t.gap_after === undefined || finite(t.gap_after) && t.gap_after >= 0)
      && ['tts', 'sync', 'recording'].includes(String(t.audio_source))
      && ['tts', 'sync'].includes(String(t.audio_kind))
      && (t.audio_source === 'recording' ? t.audio_kind === 'sync' : t.audio_source === t.audio_kind)
      && (t.transcript_verified === undefined || (t.audio_source === 'recording' && typeof t.transcript_verified === 'boolean'))
      && (t.recording_id === undefined || (t.audio_source === 'recording' && hexId(t.recording_id)))
      && (t.uploaded_revision === undefined || integer(t.uploaded_revision)))
    || !Array.isArray(value.current_shots) || !value.current_shots.every(s => object(s)
      && integer(s.sentence_id) && Array.isArray(s.shot_ids) && s.shot_ids.every(integer))
    || !Array.isArray(value.shots) || !value.shots.every(s => object(s) && integer(s.shot_id)
      && ['source', 'generated'].includes(String(s.media_origin)) && typeof s.selectable === 'boolean'
      && finite(s.duration) && finite(s.start) && finite(s.end)
      && integer(s.source_index) && integer(s.source_scene_index)
      && typeof s.unused === 'boolean' && (s.used_by_sentence_id === null || integer(s.used_by_sentence_id)))
    || !Array.isArray(value.versions) || !value.versions.every(v => object(v) && integer(v.revision)
      && typeof v.label === 'string' && typeof v.created_at === 'string')) {
    throw new Error('作品编辑资料不完整，暂时不能编辑。请刷新状态，仍失败时联系维护人员。');
  }
  readWorkbenchPreferences(value.preferences);
  for (const timing of value.timings) {
    if (timing.words === undefined) continue;
    if (!Array.isArray(timing.words) || timing.words.length > 20_000) throw new Error('句子词时间不完整，请刷新核对。');
    let end = 0;
    for (const word of timing.words) {
      if (!object(word) || !text(word.text, 2000) || !word.text.trim() || !finite(word.start) || !finite(word.end)
        || word.start < end || word.end <= word.start || word.end > timing.duration) throw new Error('句子词时间不完整，请刷新核对。');
      end = word.end;
    }
  }
  const report = parseReport(value.report, taskId), mode = value.mode ?? report.mode ?? 'voiceover';
  if (!modeValue(mode) || value.mode !== undefined && report.mode !== undefined && value.mode !== report.mode
    || report.revision !== undefined && report.revision !== value.revision
    || value.lower_thirds_burned !== undefined && typeof value.lower_thirds_burned !== 'boolean'
    || value.preferences.lower_third !== undefined && typeof value.preferences.lower_third !== 'boolean') {
    throw new Error('作品模式或人名条资料不一致，请刷新后核对。');
  }
  return { ...value, mode, report, speakers: value.speakers === undefined ? report.speakers ?? [] : parseSpeakers(value.speakers),
    jumpcuts: value.jumpcuts === undefined ? report.jumpcuts ?? [] : parseJumpCuts(value.jumpcuts) } as unknown as WorkbenchContext;
}
export function parseJob(value: unknown, submittedOptions?: ExportOptions): ExportJob {
  // v2's receipt is intentionally small. It still supplies the real identity,
  // revision and state; options may only come from that exact explicit POST.
  if (object(value) && value.id === undefined && hexId(value.export_id)) {
    const opts = object(value.options) ? value.options : null;
    if (!integer(value.revision) || !['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'].includes(String(value.state))
      || value.state === 'succeeded' && (value.output_id !== value.export_id || !object(value.result))
      || value.output_id != null && value.output_id !== value.export_id
      || value.result != null && (!object(value.result) || !integer(value.result.bytes) || value.result.bytes <= 0
        || !finite(value.result.duration) || value.result.duration < 0)
      || opts && (!(SIMPLE_EXPORT_FORMATS as readonly unknown[]).includes(opts.fmt)
        || !['16:9', '9:16', '1:1'].includes(String(opts.aspect)) || !['360p', '720p', '1080p'].includes(String(opts.res))
        || !['std', 'big', 'standard', 'large', 'none'].includes(String(opts.sub))
        || opts.expected_revision !== undefined && opts.expected_revision !== value.revision
        || opts.fmt === 'png' && (!finite(opts.frame_seconds) || opts.frame_seconds < 0 || opts.frame_seconds > 3600)
        || opts.fmt !== 'png' && opts.frame_seconds != null)
      || !opts && !submittedOptions) throw new Error('导出回执不完整，请查询状态，不要重复提交。');
    const options = opts ? { ...DEFAULT_EXPORT, format: opts.fmt as ExportFormat, aspect: opts.aspect as ExportOptions['aspect'],
      resolution: Number(String(opts.res).slice(0, -1)) as ExportOptions['resolution'],
      subtitles: (opts.sub === 'std' ? 'standard' : opts.sub === 'big' ? 'large' : opts.sub) as ExportOptions['subtitles'],
      ...(opts.fmt === 'png' ? { frame_time: opts.frame_seconds as number } : {}) } : normalizeExport(submittedOptions!);
    return { id: value.export_id, export_id: value.export_id, revision: value.revision, pipeline_revision: value.revision,
      state: value.state as ExportJob['state'], output_id: value.output_id as string ?? null,
      error: value.error ? '导出没有完成，请核对状态后再操作。' : null, qc: '', disclosure: false, options,
      ...(object(value.result) ? { result: { file: '', bytes: value.result.bytes as number, duration: value.result.duration as number } } : {}) };
  }
  if (!object(value) || !hexId(value.id) || !integer(value.pipeline_revision) || !integer(value.revision)
    || value.export_id !== undefined && !hexId(value.export_id)
    || value.error != null && !text(value.error, 8000) || value.output_id != null && !hexId(value.output_id)
    || !object(value.options) || !EXPORT_FORMATS.includes(value.options.format as ExportFormat)
    || typeof value.qc !== 'string' || typeof value.disclosure !== 'boolean'
    || !['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'].includes(String(value.state))
    || (value.state === 'succeeded' && value.output_id !== value.id)
    || (value.result != null && (!object(value.result) || !integer(value.result.bytes)
      || !finite(value.result.duration) || value.result.duration < 0))) throw new Error('导出任务响应不兼容，不能提供下载。');
  const legacy = value as unknown as ExportJob;
  return { id: legacy.id, ...(legacy.export_id ? { export_id: legacy.export_id } : {}), revision: legacy.revision,
    pipeline_revision: legacy.pipeline_revision, state: legacy.state, output_id: legacy.output_id ?? null,
    error: legacy.error ? '导出没有完成，请核对状态后再操作。' : null, qc: '', disclosure: legacy.disclosure,
    options: { format: legacy.options.format, resolution: legacy.options.resolution, fps: legacy.options.fps,
      aspect: legacy.options.aspect, subtitles: legacy.options.subtitles, video_bitrate_kbps: legacy.options.video_bitrate_kbps,
      audio_bitrate_kbps: legacy.options.audio_bitrate_kbps, ...(legacy.options.frame_time != null ? { frame_time: legacy.options.frame_time } : {}) },
    ...(legacy.result ? { result: { file: '', bytes: legacy.result.bytes, duration: legacy.result.duration } } : {}) };
}
export function validateRecording(file: File): void {
  if (!file.size || file.size > MAX_RECORDING_BYTES) throw new Error('录音必须非空且不超过 20 MiB。');
  if (!/\.(webm|ogg|wav|mp3|m4a)$/i.test(file.name)) throw new Error('请选择 WebM、Ogg、WAV、MP3 或 M4A 音频。');
}

/** Refuse foreign URLs before appending credentials, including server-provided thumbnail URLs. */
export function protectedMedia(taskId: string, path: string, token?: string, revision?: number): string {
  const root = `/api/tasks/${encodeURIComponent(taskId)}/`;
  const url = new URL(path, 'https://workbench.invalid');
  if (url.origin !== 'https://workbench.invalid' || !url.pathname.startsWith(root)
    || path.includes('\\') || path.includes('://') || path.startsWith('//')) throw new Error('无效的作品媒体地址。');
  // Never preserve arbitrary credentials supplied by a response.
  url.search = '';
  if (token) url.searchParams.set('token', token);
  if (revision !== undefined) url.searchParams.set('revision', String(revision));
  return url.pathname + url.search;
}
export function createWorkbenchApi(taskId: string, request: WorkbenchRequest) {
  const root = `/api/tasks/${encodeURIComponent(taskId)}`;
  const get = (path: string, signal?: AbortSignal) => request<unknown>(root + path, { signal, cache: 'no-store' });
  const post = (path: string, body: unknown, signal?: AbortSignal) => request<unknown>(root + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  const receipt = (v: unknown, expected: number, count = 1): EditReceipt => {
    if (!object(v) || v.task_id !== taskId || v.status !== 'queued' || !integer(v.revision)
      || !integer(expected) || v.current_revision !== expected || v.revision !== expected + count) throw new Error('未确认本次提交成功，请刷新核对任务状态；不要重复提交。');
    return { task_id: taskId, revision: v.revision, current_revision: expected, status: 'queued' };
  };
  return {
    context: async (signal?: AbortSignal) => parseContext(await get('/workbench/context', signal), taskId),
    operation: async (id: string, expectedRevision: number, signal?: AbortSignal, expectedSteps?: ApplyStep[]) => {
      if (!hexId(id)) throw new Error('无效修改回执。');
      const value = await get(`/operations/${id}`, signal);
      if (!object(value) || value.operation_id !== id || value.revision !== expectedRevision || !object(value.replace_failures)
        || !['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted'].includes(String(value.state))) throw new Error('修改结果版本不匹配，请刷新核对。');
      const steps = parseApplySteps(value.steps);
      if (expectedSteps && (steps.length !== expectedSteps.length || steps.some((s, i) => s.kind !== expectedSteps[i].kind
        || s.row_id !== expectedSteps[i].row_id || JSON.stringify(s.stages) !== JSON.stringify(expectedSteps[i].stages)))) throw new Error('修改结果与提交计划不匹配，请刷新核对。');
      const failures: NonNullable<Report['replace_fails']> = {};
      for (const [row, reason] of Object.entries(value.replace_failures)) {
        if (/^(0|[1-9]\d*)$/.test(row) && ['none', 'used', 'abstract'].includes(String(reason))) failures[row] = { kind: reason as 'none' | 'used' | 'abstract', text: '' };
      }
      return { failures, state: value.state as ExportJob['state'], steps,
        complete: value.state === 'succeeded' && steps.every(s => s.state === 'succeeded') && Object.keys(value.replace_failures).length === 0 };
    },
    report: async (signal?: AbortSignal) => parseReport(await get('/report', signal), taskId),
    version: async (revision: number, signal?: AbortSignal) => {
      const value = await get(`/versions/${revision}`, signal);
      if (!object(value) || value.revision !== revision) throw new Error('历史报告版本不匹配。');
      return parseReport(value.report, taskId);
    },
    // Kept for older callers. The mode-aware result page uses one remix below.
    edit: async (batch: EditBatch, signal?: AbortSignal) => {
      validateEditBatch(batch);
      return receipt(await post('/workbench/edit', batch, signal), batch.expected_revision);
    },
    remix: async (batch: EditBatch, mode: ProductionMode, signal?: AbortSignal) => {
      validateEditBatch(batch, mode);
      return receipt(await post('/remix', batch, signal), batch.expected_revision);
    },
    apply: async (batch: EditBatch, allRowIds: readonly number[], mode: ProductionMode, signal?: AbortSignal) => {
      const body = applyBody(batch, allRowIds, mode);
      const hasChanges = !!(body.deleted.length || Object.keys(body.edits).length || Object.keys(body.voices).length || body.pacing !== undefined
        || Object.keys(body.trims).length || Object.keys(body.takes).length || body.to_narration.length || Object.keys(body.speakers).length);
      const replaces = Object.keys(body.replaces).map(Number).sort((a, b) => a - b);
      const recompose = !hasChanges && !replaces.length, remix = hasChanges || recompose;
      const raw = await post('/apply', body, signal);
      if (!object(raw) || !hexId(raw.operation_id)) throw new Error('修改回执不完整，请刷新状态；不要重复提交。');
      const steps = parseApplySteps(object(raw.plan) ? raw.plan.steps : raw.steps, true);
      const firstStage = recompose ? 9 : body.to_narration.length ? 6 : Object.keys(body.speakers).length && !body.deleted.length
        && !Object.keys(body.edits).length && !Object.keys(body.voices).length && !Object.keys(body.trims).length
        && !Object.keys(body.takes).length && body.pacing === undefined ? 8 : 7;
      if (steps.length !== Number(remix) + replaces.length || remix && (steps[0].kind === 'replace'
        || JSON.stringify(steps[0].stages) !== JSON.stringify(Array.from({ length: 11 - firstStage }, (_, i) => firstStage + i)))
        || replaces.some((row, i) => steps[i + Number(remix)].kind !== 'replace' || steps[i + Number(remix)].row_id !== row)) {
        throw new Error('修改计划与本次提交不一致，请刷新状态；不要重复提交。');
      }
      if (raw.steps !== undefined && raw.plan !== undefined && JSON.stringify(parseApplySteps(raw.steps, true)) !== JSON.stringify(steps)) throw new Error('修改计划前后不一致。');
      return { ...receipt(raw, batch.expected_revision, steps.length), operation_id: raw.operation_id, plan: { steps } };
    },
    checks: async (signal?: AbortSignal) => parseChecks(await get('/checks', signal)),
    confirmCheck: async (checks: WorkbenchChecks, key: string, checked: boolean, signal?: AbortSignal) => {
      const checked_keys = confirmationKeys(checks, key, checked);
      const next = parseChecks(await request<unknown>(root + '/checks', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_revision: checks.revision, checked_keys }), signal }));
      if (next.revision !== checks.revision) throw new Error('作品已有新修改，请刷新后重新核对，刚才的勾选未被确认。');
      if (next.checks.find(c => c.key === key)?.checked !== checked) throw new Error('服务器没有确认这次勾选，请重新读取检查结果。');
      return next;
    },
    takes: async (rowId: number, signal?: AbortSignal) => {
      if (!integer(rowId)) throw new Error('请先选择一句原声。');
      const value = await get(`/quotes/${rowId}/takes`, signal);
      if (!object(value) || !Array.isArray(value.takes) || value.takes.length > 100) throw new Error('其他原声片段读取失败，请重试。');
      const takes = value.takes.map(parseQuoteTake);
      if (new Set(takes.map(t => t.take_id)).size !== takes.length) throw new Error('候选片段重复，请刷新后重新选择。');
      return takes;
    },
    wave: async (rowId: number, signal?: AbortSignal) => {
      if (!integer(rowId)) throw new Error('请先选择一句原声。');
      return parseQuoteWave(await get(`/waves/${rowId}.json`, signal));
    },
    restore: async (revision: number, expected_revision: number, signal?: AbortSignal) =>
      receipt(await post('/restore', { rev: revision, expected_revision }, signal), expected_revision),
    upload: async (sentenceId: number, revision: number, file: File, signal?: AbortSignal): Promise<RecordingReceipt> => {
      validateRecording(file);
      const body = new FormData();
      body.set('rowid', String(sentenceId)); body.set('expected_revision', String(revision)); body.set('audio', file);
      const v = await request<unknown>(root + '/recordings', { method: 'POST', body, signal });
      return parseRecordingReceipt(v, sentenceId, revision);
    },
    export: async (options: ExportOptions, expected_revision: number, signal?: AbortSignal) => {
      if (!integer(expected_revision)) throw new Error('导出必须绑定当前版本。');
      const job = parseJob(await post('/exports', { ...simpleExportBody(options), expected_revision }, signal), options);
      if (job.pipeline_revision !== expected_revision) throw new Error('导出回执版本不匹配，请查询状态，不要重复提交。');
      return job;
    },
    job: async (id: string, signal?: AbortSignal, expectedRevision?: number) => {
      if (!hexId(id)) throw new Error('无效导出任务标识。');
      const next = parseJob(await get(`/exports/${id}`, signal));
      if (next.id !== id && next.export_id !== id) throw new Error('导出状态与本次任务不符，未替换已有回执。');
      if (expectedRevision !== undefined && next.pipeline_revision !== expectedRevision) throw new Error('导出状态版本不匹配，未替换已有回执。');
      return next;
    },
    cancelExport: async (id: string, signal?: AbortSignal, expectedRevision?: number) => {
      if (!hexId(id)) throw new Error('无效导出任务标识。');
      const next = parseJob(await request<unknown>(root + `/exports/${id}`, { method: 'DELETE', signal }));
      if (next.id !== id) throw new Error('取消回执与本次导出不符，请重新查询状态，不要重复提交。');
      if (expectedRevision !== undefined && next.pipeline_revision !== expectedRevision) throw new Error('取消回执版本不匹配，请重新查询状态，不要重复提交。');
      return next;
    },
  };
}