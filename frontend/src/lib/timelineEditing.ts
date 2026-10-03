/** Pure editing proposals. No DOM, clock, requests, media execution or project
 * mutation. The server remains authoritative for revisions, locks and geometry. */
import type { SourceMark, StudioAction, StudioClip, StudioProject, TimelineFPS } from "./studioApi";

export const TIMELINE_FPS: readonly TimelineFPS[] = [24, 25, 30, 60];
export type EditMode = "ordinary" | "ripple" | "roll" | "slip" | "slide";
export const EDIT_MODE_LABELS: Record<EditMode, string> = {
  ordinary: "普通修剪", ripple: "波纹修剪", roll: "滚动修剪", slip: "滑移源范围", slide: "滑动片段",
};

function checkFPS(fps: TimelineFPS): void {
  if (!TIMELINE_FPS.includes(fps)) throw new Error("时间线帧率须为 24 / 25 / 30 / 60。");
}

/** Non-negative round-half-up. Compare against the actual half-frame time to
 * avoid e.g. 1.025 * 60 = 61.49999999999999 rounding a decimal tie down.
 * Values immediately below the boundary are NOT promoted with an epsilon. */
export function secondsToFrame(seconds: number, fps: TimelineFPS): number {
  checkFPS(fps);
  if (!Number.isFinite(seconds) || seconds < 0 || !Number.isSafeInteger(Math.ceil(seconds * fps))) {
    throw new Error("时间须为非负有限秒数。");
  }
  const lower = Math.floor(seconds * fps);
  const frame = seconds >= (lower + 0.5) / fps ? lower + 1 : lower;
  return frame === 0 ? 0 : frame;
}

export function frameToSeconds(frame: number, fps: TimelineFPS): number {
  checkFPS(fps);
  if (!Number.isSafeInteger(frame) || frame < 0) throw new Error("请输入非负整数帧。");
  return frame / fps;
}

export function quantizeTime(seconds: number, fps: TimelineFPS): number {
  return frameToSeconds(secondsToFrame(seconds, fps), fps);
}

export function formatTimecode(seconds: number, fps: TimelineFPS): string {
  const frame = secondsToFrame(seconds, fps), wholeSeconds = Math.floor(frame / fps);
  return [Math.floor(wholeSeconds / 3600), Math.floor(wholeSeconds / 60) % 60, wholeSeconds % 60, frame % fps]
    .map(n => String(n).padStart(2, "0")).join(":");
}

/** Absolute integer frame count OR non-drop HH:MM:SS:FF; never parseFloat partials. */
export function parseFrameInput(input: string, fps: TimelineFPS, maxSeconds = 120): number {
  checkFPS(fps);
  const text = input.trim();
  let frame: number;
  if (/^\d+$/.test(text)) frame = Number(text);
  else {
    const match = /^(\d{2,}):([0-5]\d):([0-5]\d):(\d{2})$/.exec(text);
    if (!match || Number(match[4]) >= fps) throw new Error(`请输入整数帧或 HH:MM:SS:FF（FF < ${fps}，非丢帧）。`);
    frame = ((Number(match[1]) * 60 + Number(match[2])) * 60 + Number(match[3])) * fps + Number(match[4]);
  }
  const seconds = frameToSeconds(frame, fps);
  if (!Number.isFinite(maxSeconds) || maxSeconds < 0 || seconds > maxSeconds) throw new Error(`时间不得超过 ${maxSeconds} 秒。`);
  return seconds;
}

export interface SnapTarget {
  id: string; kind: "origin" | "marker" | "clip-start" | "clip-end"; time: number; label: string;
}
export interface TimeProposal { requested: number; seconds: number; frame: number; target: SnapTarget | null }

export function collectSnapTargets(project: StudioProject, excludeClipId?: string): SnapTarget[] {
  return [
    { id: "origin", kind: "origin", time: 0, label: "时间线零点" },
    ...project.markers.map(m => ({ id: m.id, kind: "marker" as const, time: m.time, label: `标记「${m.label || m.id}」` })),
    ...project.tracks.flatMap(track => track.clips.filter(c => c.id !== excludeClipId).flatMap(clip => [
      { id: `${clip.id}:start`, kind: "clip-start" as const, time: clip.start, label: `${track.name || track.id} / ${clip.id} 起点` },
      { id: `${clip.id}:end`, kind: "clip-end" as const, time: clip.start + clip.duration, label: `${track.name || track.id} / ${clip.id} 终点` },
    ])),
  ];
}

/** Candidates and submitted values share the workspace frame grid. Distance is
 * measured in frames; ties: earlier frame, origin, marker, start, end, stable ID.
 * Legacy off-grid targets are NOT moved; their original time stays in target.time
 * so the UI can disclose the quantization instead of claiming exact adjacency. */
export function proposeTimelineTime(requested: number, fps: TimelineFPS, enabled: boolean, targets: readonly SnapTarget[],
  { min = 0, max = 120, thresholdFrames = 6 }: { min?: number; max?: number; thresholdFrames?: number } = {}): TimeProposal {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min < 0 || max < min || max > 120
    || !Number.isInteger(thresholdFrames) || thresholdFrames < 0 || !Number.isFinite(requested) || requested < 0 || requested > 120
    || requested < min - 1e-12 || requested > max + 1e-12) {
    throw new Error("提交位置须在允许的时间线范围内。");
  }
  const frame = secondsToFrame(requested, fps), seconds = frameToSeconds(frame, fps);
  if (seconds < min - 1e-12 || seconds > max + 1e-12) throw new Error("取整后的位置超出允许范围。");
  if (!enabled) return { requested, seconds, frame, target: null };
  const priority = { origin: 0, marker: 1, "clip-start": 2, "clip-end": 3 };
  const candidates = targets.filter(t => Number.isFinite(t.time) && t.time >= 0 && t.time <= 120).map(target => {
    const targetFrame = secondsToFrame(target.time, fps);
    return { target, frame: targetFrame, distance: Math.abs(targetFrame - frame) };
  }).filter(c => c.distance <= thresholdFrames && c.frame / fps >= min - 1e-12 && c.frame / fps <= max + 1e-12);
  candidates.sort((a, b) => a.distance - b.distance || a.frame - b.frame || priority[a.target.kind] - priority[b.target.kind]
    || (a.target.id < b.target.id ? -1 : a.target.id > b.target.id ? 1 : 0));
  const nearest = candidates[0];
  return nearest ? { requested, seconds: frameToSeconds(nearest.frame, fps), frame: nearest.frame, target: nearest.target }
    : { requested, seconds, frame, target: null };
}

export function canSplitAt(clip: StudioClip, at: number, fps: TimelineFPS): boolean {
  return Number.isFinite(at) && at - clip.start >= 1 / fps - 1e-12
    && clip.start + clip.duration - at >= 1 / fps - 1e-12
    && clip.fade_in === 0 && clip.fade_out === 0 && clip.text_animation === "none"
    && Object.values(clip.keyframes).every(points => points.length === 0);
}

/** A stale selection or changed clipboard origin must never bypass local locks.
 * Caller still submits expected_revision; this check is not server authorization. */
export function clipEditProblem(project: StudioProject, clipId: string, targetTrackId?: string): string | null {
  const source = project.tracks.find(track => track.clips.some(clip => clip.id === clipId));
  if (!source) return "片段已不存在，请重新选择。";
  if (source.locked) return "来源轨道已锁定，请先解锁。";
  if (targetTrackId !== undefined) {
    const target = project.tracks.find(track => track.id === targetTrackId);
    if (!target || target.type !== source.type) return "目标轨道必须存在且类型相同。";
    if (target.locked) return "目标轨道已锁定，请先解锁。";
  }
  return null;
}

export function buildDeleteAction(clip: StudioClip, ripple: boolean): StudioAction {
  return { op: ripple ? "ripple_delete" : "delete", clip_id: clip.id };
}

/** Explicit, minimal API shapes; irrelevant form values are never serialized. */
export function buildTrimAction(mode: EditMode, clip: StudioClip,
  values: { trim: number; duration: number; at: number }, fps: TimelineFPS): StudioAction {
  checkFPS(fps);
  if (mode === "roll" || mode === "slide") {
    if (!Number.isFinite(values.at) || values.at < 0 || values.at > 120) throw new Error("边界 / 新起点须在 0–120 秒内。");
    return { op: mode, clip_id: clip.id, at: quantizeTime(values.at, fps) };
  }
  if (!["ordinary", "ripple", "slip"].includes(mode)) throw new Error("未知修剪模式。");
  if (!Number.isFinite(values.trim) || values.trim < 0 || values.trim > 3600) throw new Error("源入点须在 0–3600 秒内。");
  if (mode === "slip") {
    if (!clip.source_id) throw new Error("滑移仅适用于真实媒体片段。");
    return { op: "slip", clip_id: clip.id, trim: values.trim };
  }
  if (!Number.isFinite(values.duration) || values.duration <= 0 || values.duration > 120) throw new Error("修剪时长须大于 0 且不超过 120 秒。");
  if (!clip.source_id && values.trim !== 0) throw new Error("文字 / 调整区间的源入点必须为 0。");
  const duration = mode === "ripple" ? quantizeTime(values.duration, fps) : values.duration;
  if (mode === "ripple" && duration < 1 / fps) throw new Error("波纹修剪须保留至少一帧。");
  return { op: mode === "ripple" ? "ripple_trim" : "trim", clip_id: clip.id, trim: values.trim, duration };
}

export function validateSourceRange(inPoint: number, outPoint: number, knownDuration?: number): void {
  if (!Number.isFinite(inPoint) || !Number.isFinite(outPoint) || inPoint < 0 || outPoint > 3600 || outPoint <= inPoint) {
    throw new Error("源范围须满足 0 ≤ 入点 < 出点 ≤ 3600 秒；不会自动交换或编造出点。");
  }
  if (knownDuration !== undefined && (!Number.isFinite(knownDuration) || knownDuration <= 0 || outPoint > knownDuration + 1e-9)) {
    throw new Error("源出点超过浏览器读取的真实素材时长。");
  }
}

/** Source times remain exact source seconds, independently of timeline/export FPS.
 * Clear removes the entry; it never writes a fabricated 0–3 second mark. */
export function updateSourceMarks(marks: readonly SourceMark[], sourceId: string,
  range: { in_point: number; out_point: number } | null, authorizedIds: readonly string[], knownDuration?: number): SourceMark[] {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(sourceId) || !authorizedIds.includes(sourceId)) throw new Error("入出点必须引用当前任务的真实源 ID。");
  if (range) validateSourceRange(range.in_point, range.out_point, knownDuration);
  const existing = marks.some(m => m.source_id === sourceId);
  if (range && !existing && marks.length >= 200) throw new Error("每个工程最多保存 200 个素材入出点。");
  if (!range) return marks.filter(m => m.source_id !== sourceId).map(m => ({ ...m }));
  const mark = { source_id: sourceId, in_point: range.in_point, out_point: range.out_point };
  return existing ? marks.map(m => m.source_id === sourceId ? mark : { ...m })
    : [...marks.map(m => ({ ...m })), mark];
}