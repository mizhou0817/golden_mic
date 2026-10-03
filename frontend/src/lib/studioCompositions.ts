/** Pure, bounded proposals for the EXISTING Project renderer. No requests,
 * clocks, random IDs, media execution, implicit retiming or nested recipe model.
 * The caller previews, confirms and saves with expected_revision; the server
 * still authorizes sources / locks and probes actual streams / EOF. */
import { isFullFrameTransitionClip, makeClip, normalizeProject, normalizeTransition } from "./studioApi";
import type { ClipTransition, StudioCapabilities, StudioClip, StudioProject, StudioSource, StudioTrack } from "./studioApi";
import { validateSourceRange } from "./timelineEditing";

const EPSILON = 1e-12;
export const COMPOSITION_LIMITS = { tracks: 8, clips: 64, inputs: 16, cuts: 12, duration: 120, requestBytes: 262144 } as const;
export type CompositionLimits = Partial<Pick<StudioCapabilities["limits"],
  "tracks" | "clips" | "duration_seconds" | "request_bytes" | "source_marks" | "history_mutations">>;
export interface CompositionContext {
  sources: readonly StudioSource[];
  durations?: Readonly<Record<string, number>>;
  limits?: CompositionLimits;
  expectedRevision?: number;
}
export interface CompositionStats { tracks: number; clips: number; inputs: number; end: number; activeEnd: number; requestBytes: number }
export interface CompositionProposal {
  kind: "transition-add" | "transition-remove" | "multicam-grid" | "multicam-switch";
  project: StudioProject;
  stats: CompositionStats;
  beforeEnd: number; beforeActiveEnd: number;
  warnings: string[];
  focus: { trackId: string; clipId: string };
}
export interface TransitionBoundary { trackId: string; leftClipId: string; rightClipId: string }
export interface TransitionProposal extends CompositionProposal, TransitionBoundary {
  shiftSeconds: number;
  movements: { clipId: string; before: number; after: number }[];
}
export interface MulticamAngle { source_id: string; sync_in: number }
/** Angle is one-based to match the manual form. Starts are explicit seconds. */
export interface MulticamCut { start: number; angle: number }
export interface MulticamRecipe {
  mode: "grid" | "switch";
  placement: "append" | "replace";
  duration: number;
  angles: MulticamAngle[];
  cuts: MulticamCut[];
  master: MulticamAngle;
}
export interface MulticamProposal extends CompositionProposal {
  placement: MulticamRecipe["placement"];
  generatedTrackIds: string[];
  reads: { clipId: string; sourceId: string; start: number; trim: number; duration: number; end: number; role: string }[];
}

const endOf = (clip: StudioClip) => clip.start + clip.duration;
const orderedClips = (track: StudioTrack) => [...track.clips].sort((a, b) => a.start - b.start);
const projectEnd = (project: StudioProject) => Math.max(0, ...project.tracks.flatMap(track => track.clips.map(endOf)));
const activeEnd = (project: StudioProject) => {
  const solo = project.tracks.some(track => track.solo && !track.hidden);
  return Math.max(0, ...project.tracks.filter(track => !track.hidden && (!solo || track.solo)).flatMap(track => track.clips.map(endOf)));
};
const overlap = (a: StudioClip, b: StudioClip) => Math.max(0, Math.min(endOf(a), endOf(b)) - Math.max(a.start, b.start));
const mediaTrack = (track: StudioTrack) => ["video", "overlay", "audio"].includes(track.type);

function limit(context: CompositionContext, key: keyof CompositionLimits, hard: number): number {
  const value = context.limits?.[key] ?? hard;
  if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`服务器资源限制 ${key} 无效。`);
  return Math.min(hard, value);
}

function sourceMap(context: CompositionContext): Map<string, StudioSource> {
  const sources = new Map<string, StudioSource>();
  for (const source of context.sources) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(source.id) || sources.has(source.id)) throw new Error("素材目录包含无效 / 重复源 ID。");
    if ((source.has_audio !== undefined && typeof source.has_audio !== "boolean")
      || (source.has_video !== undefined && typeof source.has_video !== "boolean")) throw new Error("素材音视频元数据无效。");
    sources.set(source.id, source);
  }
  return sources;
}

function knownDuration(source: StudioSource, context: CompositionContext): number | undefined {
  const values = [source.duration, context.durations?.[source.id]].filter((value): value is number => value !== undefined);
  if (values.some(value => !Number.isFinite(value) || value <= 0 || value > 3600)) throw new Error("已知源时长无效或超过 3600 秒。");
  // When browser / catalog measurements differ, never silently choose the longer one.
  return values.length ? Math.min(...values) : undefined;
}

/** Unknown video metadata is not a claim of a video stream; render probes it.
 * Known audio-only uploads should not be offered as camera angles. */
export function isMulticamVideoSource(source: StudioSource): boolean {
  return source.is_image !== true && !/^image_[0-9a-f]{24}$/.test(source.id)
    && source.has_video !== false && (source.has_video === true
    || (source.id !== "narration" && !/\.(mp3|wav|m4a|aac|flac|ogg|opus)$/i.test(source.name)));
}

/** Includes hidden / non-solo clips in budgets so later enabling a track cannot
 * turn a proposed bounded composition into >16 decoder inputs. Counts clips,
 * NOT distinct source IDs: repeated cuts each cost a renderer input. */
function inspectProject(project: StudioProject, context: CompositionContext): { stats: CompositionStats; warnings: string[] } {
  const sources = sourceMap(context), clips = project.tracks.flatMap(track => track.clips);
  const inputs = project.tracks.filter(mediaTrack).reduce((total, track) => total + track.clips.length, 0);
  const end = projectEnd(project), visibleEnd = activeEnd(project);
  if (project.tracks.length > limit(context, "tracks", 8) || clips.length > limit(context, "clips", 64)) throw new Error("提案超过轨道 / 片段额度（最多 8 轨 / 64 片段），不会自动删除旧轨道。");
  if (inputs > 16) throw new Error("提案超过 16 个解码输入（每个媒体片段均计数，包含隐藏轨），请减少切点或明确选择替换。");
  if (end > limit(context, "duration_seconds", 120)) throw new Error("提案工程总时长超限（最多 120 秒），不会裁掉尾部。");
  if (project.workspace.source_marks.length > limit(context, "source_marks", 200)) throw new Error("源入出点标记数量超限。");
  const unprobed = new Set<string>();
  for (const track of project.tracks) for (const clip of track.clips) {
    if (!mediaTrack(track)) continue;
    const source = clip.source_id ? sources.get(clip.source_id) : undefined;
    if (!source) throw new Error("提案只能引用当前任务已授权目录中的源 ID。");
    if (source.is_image === true || /^image_[0-9a-f]{24}$/.test(source.id)) {
      if (track.type === "audio" || clip.trim !== 0 || clip.speed !== 1 || clip.reverse || clip.freeze || !clip.mute) {
        throw new Error("静态贴纸须使用显示时长、零入点、原速和静音；不能当作有源时钟的音视频。");
      }
      continue;
    }
    if (track.type === "audio" && source.has_audio === false) throw new Error("所选主音频 / 音频轨源已知没有音轨。");
    if (track.type !== "audio" && !isMulticamVideoSource(source)) throw new Error("视频 / 叠加轨源已知没有视频流。");
    const known = knownDuration(source, context);
    if (known === undefined) unprobed.add(source.id);
    if (clip.freeze) {
      if (known !== undefined && clip.trim >= known) throw new Error("定格入点必须小于真实源时长。");
    } else validateSourceRange(clip.trim, clip.trim + clip.duration * clip.speed, known);
  }
  for (const entry of [...project.workspace.source_marks, ...project.assets]) {
    if (!sources.has(entry.source_id)) throw new Error("工程素材元数据 / 源标记引用了目录之外的源 ID。");
  }
  const revision = context.expectedRevision ?? 0;
  if (!Number.isSafeInteger(revision) || revision < 0 || revision >= limit(context, "history_mutations", 100)) throw new Error("当前修订无效或服务器历史变更额度已用完。");
  const requestBytes = new TextEncoder().encode(JSON.stringify({ expected_revision: revision, project })).length;
  if (requestBytes > limit(context, "request_bytes", 262144)) throw new Error("提案超过工程请求字节额度（最多 256 KiB）。");
  return { stats: { tracks: project.tracks.length, clips: clips.length, inputs, end, activeEnd: visibleEnd, requestBytes },
    warnings: unprobed.size ? [`${unprobed.size} 个源的 EOF 尚未实测；保存不是媒体验证，渲染器会探测并拒绝越界，不补帧。`] : [] };
}

/** New boundaries must be chronological immediate neighbors. Existing declared
 * pairs remain removable even if an unrelated legacy clip ends before their
 * overlap. Full-frame / locks / budgets are checked by the actual builder. */
export function transitionBoundaries(project: StudioProject): TransitionBoundary[] {
  return project.tracks.filter(track => track.type === "video" || track.type === "overlay").flatMap(track => {
    const ordered = orderedClips(track);
    return ordered.slice(1).flatMap((right, index) => {
      const left = right.transition_in ? ordered.find(clip => clip.id === right.transition_in?.left_clip_id) : ordered[index];
      return left && (Math.abs(endOf(left) - right.start) <= EPSILON || right.transition_in?.left_clip_id === left.id)
        ? [{ trackId: track.id, leftClipId: left.id, rightClipId: right.id }] : [];
    });
  });
}

function shiftBoundary(project: StudioProject, boundary: TransitionBoundary, value: Omit<ClipTransition, "left_clip_id"> | null,
  context: CompositionContext): TransitionProposal {
  // Hydrates legacy defaults and deep-detaches all nested schema fields, without
  // ever changing the caller's project or changing source time / duration / FX.
  const next = normalizeProject(project), track = next.tracks.find(item => item.id === boundary.trackId);
  if (!track || (track.type !== "video" && track.type !== "overlay")) throw new Error("转场两端必须在同一视频 / 叠加轨。");
  if (track.locked) throw new Error("转场轨道已锁定，不能前移 / 后移任何片段。");
  const ordered = orderedClips(track), leftIndex = ordered.findIndex(clip => clip.id === boundary.leftClipId);
  const rightIndex = ordered.findIndex(clip => clip.id === boundary.rightClipId);
  const left = ordered[leftIndex], right = ordered[rightIndex];
  if (!left || !right || rightIndex <= leftIndex || (value !== null && rightIndex !== leftIndex + 1)) throw new Error("添加须选择同轨按时间排序的相邻左 / 右片段；移除须选择原声明端点。");
  if (!isFullFrameTransitionClip(left) || !isFullFrameTransitionClip(right)) {
    throw new Error("两端须已为全帧 cover、scale=1、x/y=0、opacity=1，且无蒙版、色度键、关键帧、淡化或定格；不会自动改参数。");
  }
  const transition = value === null ? right.transition_in : normalizeTransition({ ...value, left_clip_id: left.id });
  if (!transition || transition.left_clip_id !== left.id) throw new Error("此边界没有可移除的入转场。");
  if (value !== null && (right.transition_in || Math.abs(endOf(left) - right.start) > EPSILON)) throw new Error("添加转场要求原边界无缝相接且没有入转场；请先移除已有转场。");
  const shiftSeconds = (value === null ? 1 : -1) * transition.duration;
  const moving = new Set(ordered.slice(rightIndex).map(clip => clip.id));
  const original = new Map(track.clips.map(clip => [clip.id, { ...clip }]));
  const movements = ordered.slice(rightIndex).map(clip => ({ clipId: clip.id, before: clip.start, after: clip.start + shiftSeconds }));
  for (const clip of track.clips) if (moving.has(clip.id)) clip.start += shiftSeconds;
  right.transition_in = value === null ? null : transition;
  // Crossing the shifted/unshifted boundary may not create/change an unrelated
  // overlap. Existing overlaps wholly among successors remain exactly intact.
  for (const fixed of track.clips.filter(clip => !moving.has(clip.id))) for (const moved of track.clips.filter(clip => moving.has(clip.id))) {
    if (fixed.id === left.id && moved.id === right.id) continue;
    const after = overlap(fixed, moved), before = overlap(original.get(fixed.id)!, original.get(moved.id)!);
    if (after > EPSILON && Math.abs(after - before) > EPSILON) throw new Error("此移动会与其他同轨片段产生冲突重叠，未生成提案。");
  }
  // Also revalidates other transitions on BOTH sides of a middle clip after a
  // shift. Removal must leave a contiguous hard cut, not a new gap or overlap.
  if (value === null && Math.abs(endOf(left) - right.start) > EPSILON) throw new Error("无法精确反向恢复此转场边界。");
  const composedProject = normalizeProject(next), checked = inspectProject(composedProject, context);
  return { kind: value === null ? "transition-remove" : "transition-add", project: composedProject, ...boundary,
    shiftSeconds, movements, beforeEnd: projectEnd(project), beforeActiveEnd: activeEnd(project), ...checked,
    focus: { trackId: track.id, clipId: right.id }, warnings: [...checked.warnings,
      "仅右片段及该轨按时间排序的所有后续片段移动；其他轨道、标记、源入点、时长与参数不动。工程终点可能因其他轨道而不变。",
      "含转场的轨道由渲染器按时间排序合成；移除该轨最后一个转场后恢复原片段列表顺序，其他普通重叠的层次可能变化。无实时效果预览，需保存后渲染。"] };
}

export function buildTransitionProposal(project: StudioProject, boundary: TransitionBoundary,
  transition: Omit<ClipTransition, "left_clip_id">, context: CompositionContext): TransitionProposal {
  return shiftBoundary(project, boundary, transition, context);
}
export function buildRemoveTransitionProposal(project: StudioProject, boundary: TransitionBoundary, context: CompositionContext): TransitionProposal {
  return shiftBoundary(project, boundary, null, context);
}

export function parseMulticamCuts(text: string): MulticamCut[] {
  if (text.length > 4096) throw new Error("切换清单最多 4096 字符 / 12 段。");
  const lines = text.trim().split(/\r?\n/).filter(line => line.trim());
  if (!lines.length || lines.length > 12) throw new Error("请填写 1–12 段切换，每行：起始秒数,机位编号。");
  return lines.map((line, index) => {
    const parts = line.trim().split(/[\s,，]+/);
    if (parts.length !== 2 || !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(parts[0]) || !/^[1-4]$/.test(parts[1])) {
      throw new Error(`切换清单第 ${index + 1} 行须为非负秒数与 1–4 的机位编号。`);
    }
    return { start: Number(parts[0]), angle: Number(parts[1]) };
  });
}

/** Deterministic, project-local IDs: re-proposing against an unchanged save
 * receipt yields the SAME candidate. Never borrows or overwrites existing IDs. */
function idAllocator(project: StudioProject): (kind: string) => string {
  // The namespace belongs to the complete snapshot, not only the currently
  // edited root. Inactive children survive both append and root replacement.
  const used = new Set([...project.groups.map(item => item.id), ...project.sequences.map(item => item.id),
    ...[project, ...project.sequences].flatMap(scope => [...scope.markers.map(item => item.id),
      ...scope.tracks.flatMap(track => [track.id, ...track.clips.map(clip => clip.id)])])]);
  return kind => {
    let index = 1;
    while (used.has(`mc_${kind}_${index}`)) index++;
    const id = `mc_${kind}_${index}`; used.add(id); return id;
  };
}

export function buildMulticamProject(project: StudioProject, recipe: MulticamRecipe, context: CompositionContext): MulticamProposal {
  const base = normalizeProject(project), sources = sourceMap(context);
  if (!["grid", "switch"].includes(recipe.mode) || !["append", "replace"].includes(recipe.placement)) throw new Error("未知多机位模式 / 写入方式。");
  if (!Number.isFinite(recipe.duration) || recipe.duration <= 0 || recipe.duration > limit(context, "duration_seconds", 120)) throw new Error("请明确填写共同输出时长：大于 0 且不超过 120 秒；共同起点固定为 0。");
  if (!Array.isArray(recipe.angles) || recipe.angles.length < 2 || recipe.angles.length > 4) throw new Error("手动多机位仅支持 2–4 个机位；9 / 16 机位未支持。");
  if (recipe.placement === "replace" && base.tracks.some(track => track.locked)) throw new Error("替换要求原工程所有轨道均已解锁；不会悄悄清除锁定轨道。");
  if (recipe.placement === "replace" && base.tracks.some(track => track.clips.some(clip => clip.transition_in))) throw new Error("原工程仍有绑定转场；请先在 FX 面板显式移除并保存，再替换轨道，不能连同端点悄悄删除绑定。");
  if (recipe.placement === "append" && base.tracks.some(track => track.solo && !track.hidden)) throw new Error("原工程存在可见独奏轨，新轨将不生效；请先明确取消独奏或选择替换。");
  const checkAngle = (angle: MulticamAngle, audio: boolean) => {
    const source = sources.get(angle.source_id);
    if (!source) throw new Error("机位 / 主音轨必须选择当前已授权目录中的素材。");
    if (!Number.isFinite(angle.sync_in) || angle.sync_in < 0 || angle.sync_in >= 3600) throw new Error("每个素材须明确填写 0–3600 秒内的同步入点，不会猜测或自动同步。");
    if (audio ? source.has_audio === false : !isMulticamVideoSource(source)) throw new Error(audio ? "所选主音轨源已知没有音频流。" : "机位源已知没有视频流。");
    const duration = knownDuration(source, context);
    if (duration !== undefined && angle.sync_in >= duration) throw new Error("同步入点须小于已知源时长，即使此机位暂未出现在切换清单中。");
    return source;
  };
  recipe.angles.forEach(angle => checkAngle(angle, false));
  const master = checkAngle(recipe.master, true);
  if (!Array.isArray(recipe.cuts) || recipe.cuts.length > 12) throw new Error("最多 12 段手动切换。");
  if (recipe.mode === "switch") {
    if (!recipe.cuts.length || recipe.cuts[0].start !== 0 || recipe.cuts.some((cut, index) =>
      !Number.isFinite(cut.start) || cut.start < 0 || cut.start >= recipe.duration
      || !Number.isSafeInteger(cut.angle) || cut.angle < 1 || cut.angle > recipe.angles.length
      || (index > 0 && cut.start <= recipe.cuts[index - 1].start))) {
      throw new Error("切换清单须 1–12 段、首段从 0 开始、起点严格递增且小于共同输出时长，机位编号须存在。");
    }
  } else if (recipe.cuts.length) throw new Error("2×2 网格不使用切换清单；不会静默忽略已提交的切点。");
  const nextId = idAllocator(base), generated: StudioTrack[] = [];
  const newTrack = (type: "video" | "overlay" | "audio", name: string): StudioTrack => {
    const track: StudioTrack = { id: nextId("track"), type, name, locked: false, hidden: false, solo: false,
      color: type === "audio" ? "cyan" : "gold", group_id: null, clips: [] };
    generated.push(track); return track;
  };
  const reads: MulticamProposal["reads"] = [];
  const add = (track: StudioTrack, angle: MulticamAngle, start: number, duration: number, role: string, geometry: Partial<StudioClip> = {}) => {
    const clip = makeClip({ id: nextId("clip"), source_id: angle.source_id, start, trim: angle.sync_in + start, duration,
      ...(track.type === "audio" ? {} : { mute: true, fit: "cover" as const }), ...geometry });
    const readEnd = clip.trim + clip.duration;
    validateSourceRange(clip.trim, readEnd, knownDuration(sources.get(angle.source_id)!, context));
    track.clips.push(clip);
    reads.push({ clipId: clip.id, sourceId: angle.source_id, start, trim: clip.trim, duration, end: readEnd, role });
  };
  if (recipe.mode === "grid") {
    const positions = [[-0.25, -0.25], [0.25, -0.25], [-0.25, 0.25], [0.25, 0.25]] as const;
    recipe.angles.forEach((angle, index) => add(newTrack("overlay", `手动网格 · 机位 ${index + 1}`), angle, 0, recipe.duration,
      `机位 ${index + 1}`, { scale: 0.5, x: positions[index][0], y: positions[index][1] }));
  } else {
    const video = newTrack("video", "手动机位切换 · 硬切");
    recipe.cuts.forEach((cut, index) => add(video, recipe.angles[cut.angle - 1], cut.start,
      (recipe.cuts[index + 1]?.start ?? recipe.duration) - cut.start, `切换 ${index + 1} · 机位 ${cut.angle}`));
  }
  // ALWAYS a continuous ordinary audio clip, even when the user picked a camera
  // as master. Audio-track probing rejects silent sources; do not infer audio
  // availability from video playback or an extension. No cut-by-cut audio seams.
  add(newTrack("audio", "手动多机位 · 连续主音轨"), recipe.master, 0, recipe.duration, "连续主音轨");
  const composedProject = normalizeProject({ ...base, tracks: recipe.placement === "append" ? [...base.tracks, ...generated] : generated });
  const checked = inspectProject(composedProject, context);
  return { kind: recipe.mode === "grid" ? "multicam-grid" : "multicam-switch", project: composedProject, placement: recipe.placement,
    generatedTrackIds: generated.map(track => track.id), reads, beforeEnd: projectEnd(base), beforeActiveEnd: activeEnd(base), ...checked,
    focus: { trackId: generated[0].id, clipId: generated[0].clips[0].id }, warnings: [...checked.warnings,
      ...(master.has_audio === undefined ? ["主音轨的音频流未由目录确认；将由现有渲染器 ffprobe 验证，缺少音轨时失败，不伪造成功或自动换源。"] : []),
      recipe.placement === "append" ? "保留原轨道并从 0 秒追加上层普通轨道：旧音频仍会混音，旧文字仍在媒体上方；网格空位会透出旧画面。"
        : "替换仅限当前 Studio 轨道，不更改原素材 / 原成片 / 报告 / QC；保留工程名称、标记、分组、素材元数据及全部工作区设置。",
      "所有生成的视频片段静音；只增加一条连续主音轨。同步入点完全由用户指定，无波形分析、自动同步或嵌套工程。",
      "源帧解码内存、编码大小、磁盘 / 作业额度及作品访问权限仍在实际渲染提交时独立验证。"] };
}