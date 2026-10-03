/** Pure whole-document sequence edits. No IO, implicit duration repair, random
 * IDs or media simulation. The caller confirms and saves the COMPLETE project
 * with the actual server revision. Locks / authorization remain server-owned. */
import {
  STUDIO_ACTIVE_VIEW, STUDIO_SEQUENCE_LIMITS, isSequenceClip, makeClip, normalizeClip,
  normalizeProject, sequenceFullEnd, sequenceRenderStats,
} from "./studioApi";
import type { StudioClip, StudioMarker, StudioProject, StudioTrack, TrackType } from "./studioApi";

const detached = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
export interface ProjectScope { id: string | null; name: string; tracks: StudioTrack[]; markers: StudioMarker[] }
export type ActiveProject = StudioProject & {
  readonly [STUDIO_ACTIVE_VIEW]: { readonly scopeId: string | null; readonly basis: string };
};

/** References for reading only. Root tracks NEVER mean the active child. */
export function projectScopes(project: StudioProject): ProjectScope[] {
  return [{ id: null, name: "主时间线", tracks: project.tracks, markers: project.markers }, ...(project.sequences ?? [])];
}
export function projectScope(project: StudioProject, id: string | null = project.active_sequence_id ?? null): ProjectScope {
  const scope = projectScopes(project).find(scope => scope.id === id);
  if (!scope) throw new Error("活动 / 目标序列不存在，未回退到主时间线。");
  return scope;
}
export function allProjectTracks(project: StudioProject): StudioTrack[] {
  return projectScopes(project).flatMap(scope => scope.tracks);
}
export function projectClipCount(project: StudioProject): number {
  return allProjectTracks(project).reduce((total, track) => total + track.clips.length, 0);
}
export function isEmptySequenceProject(project: StudioProject): boolean {
  return (project.active_sequence_id ?? null) === null && !(project.sequences ?? []).length
    && project.tracks.length === 0 && project.markers.length === 0;
}

/** A locked parent protects referenced track CONTENT transitively, even when
 * its own lane is hidden/inactive. Metadata/markers remain global/local data.
 * This UX guard is not authorization; the server compares saved snapshots. */
export function sequenceContentLock(project: StudioProject, id: string | null = project.active_sequence_id ?? null): string | null {
  if (id === null) return null;
  const scopes = projectScopes(project);
  const contains = (target: string, seen: Set<string>): boolean => {
    if (target === id) return true;
    if (seen.has(target)) return false;
    seen.add(target);
    return scopes.find(scope => scope.id === target)?.tracks.some(track => track.clips.some(clip =>
      isSequenceClip(clip) && contains(clip.sequence_id, seen))) ?? false;
  };
  for (const scope of scopes) for (const track of scope.tracks) {
    if (track.locked && track.clips.some(clip => isSequenceClip(clip) && contains(clip.sequence_id, new Set()))) {
      return `父轨道「${track.name || track.id}」已锁定，先到「${scope.name}」解锁嵌套引用后再编辑子轨。`;
    }
  }
  return null;
}

/** Detached working snapshot for old track/marker-only editing helpers. It is
 * intentionally not a valid save document, even when the active scope is main. */
export function activeProject(project: StudioProject): ActiveProject {
  const scope = projectScope(project), working = detached(project);
  working.tracks = detached(scope.tracks); working.markers = detached(scope.markers);
  Object.defineProperty(working, STUDIO_ACTIVE_VIEW, { value: { scopeId: scope.id, basis: JSON.stringify(project) } });
  return working as ActiveProject;
}

/** Map ONLY active tracks/markers back; globals may be edited, other sequences
 * and root geometry may not be substituted through a working snapshot. Do not
 * normalize here: temporarily invalid form input must remain visible in drafts. */
export function replaceActiveProject(project: StudioProject, working: ActiveProject): StudioProject {
  const origin = working[STUDIO_ACTIVE_VIEW];
  if (!origin || origin.basis !== JSON.stringify(project) || origin.scopeId !== (project.active_sequence_id ?? null)
    || (working.active_sequence_id ?? null) !== origin.scopeId
    || JSON.stringify(working.sequences) !== JSON.stringify(project.sequences)) {
    throw new Error("活动编辑依据已过期或试图替换其他序列；未覆盖完整工程。");
  }
  const oldScope = projectScope(project), lock = sequenceContentLock(project);
  if (lock && JSON.stringify(oldScope.tracks) !== JSON.stringify(working.tracks)) throw new Error(lock);
  oldScope.tracks.forEach((track, index) => {
    if (track.locked && JSON.stringify(track) !== JSON.stringify(working.tracks[index])) {
      throw new Error("锁定轨道内容 / 顺序不能通过草稿更新；请先用服务器轨道动作解锁。");
    }
  });
  const next = detached(working); // The non-JSON view marker deliberately does not survive.
  if (origin.scopeId !== null) {
    next.tracks = detached(project.tracks); next.markers = detached(project.markers);
    next.sequences = (project.sequences ?? []).map(sequence => sequence.id === origin.scopeId
      ? { ...detached(sequence), tracks: detached(working.tracks), markers: detached(working.markers) } : detached(sequence));
  }
  return next;
}
export function editActiveProject(project: StudioProject, change: (working: StudioProject) => void): StudioProject {
  const working = activeProject(project);
  change(working);
  return replaceActiveProject(project, working);
}

export function selectProjectSequence(project: StudioProject, id: string | null): StudioProject {
  const next = normalizeProject(project);
  projectScope(next, id); // Never silently redirect a missing child to root.
  next.active_sequence_id = id;
  return next;
}
export function createProjectSequence(project: StudioProject, id: string, name: string): StudioProject {
  const next = normalizeProject(project);
  if (next.sequences.length >= STUDIO_SEQUENCE_LIMITS.sequences) throw new Error("最多 4 个子序列；不含主时间线。");
  if (!name.trim()) throw new Error("请输入新序列名称，不会自动从素材推断。");
  next.sequences.push({ id, name, tracks: [], markers: [] });
  next.active_sequence_id = id;
  return normalizeProject(next);
}

export function assertSequenceRenderBudget(project: StudioProject): void {
  const stats = sequenceRenderStats(project);
  if (stats.tracks > 8 || stats.clips > 64 || stats.inputs > 16) {
    throw new Error(`当前序列展开后 ${stats.tracks} 轨 / ${stats.clips} 片段 / ${stats.inputs} 解码输入，超过 8 / 64 / 16；重复嵌套与图片逐次计数，不会删除内容来凑额度。`);
  }
}

/** A new dedicated parent lane, never a mixed source/nested clip lane. All IDs
 * come from the caller so identical proposals can be confirmed without drift. */
export function insertNestedSequence(project: StudioProject, options: {
  sequenceId: string; start: number; trackId: string; clipId: string; type?: "video" | "overlay";
}): StudioProject {
  if (![options.trackId, options.clipId].every(id => typeof id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(id))) {
    throw new Error("完整嵌套提案须明确提供新轨道 / 片段 ID，不会隐式分配或替换。");
  }
  const base = normalizeProject(project), scope = projectScope(base);
  const child = base.sequences.find(sequence => sequence.id === options.sequenceId);
  if (!child || child.id === scope.id) throw new Error("请选择另一个存在的子序列，不能引用自身或主时间线。");
  if (scope.tracks.length >= 8) throw new Error("活动序列已到 8 轨上限；完整嵌套需一条独立新轨，不会混入旧片段。");
  if (scope.tracks.some(track => track.solo && !track.hidden)) throw new Error("活动序列有可见独奏轨，新嵌套轨将不生效；请先明确取消独奏。");
  const duration = sequenceFullEnd(child);
  if (!(duration > 0)) throw new Error("不能插入空子序列；需有大于 0 秒的完整生效内容。");
  if (!Number.isFinite(options.start) || options.start < 0 || options.start + duration > 120) throw new Error("完整子序列与插入起点合计须在 0–120 秒内；不裁剪或补帧。");
  const track: StudioTrack = { id: options.trackId, type: options.type ?? "overlay", name: [...`嵌套 · ${child.name}`].slice(0, 80).join(""),
    color: "purple", locked: false, hidden: false, solo: false, group_id: null,
    clips: [makeClip({ id: options.clipId, sequence_id: child.id, source_id: null, start: options.start, duration })] };
  const next = normalizeProject(editActiveProject(base, working => { working.tracks.push(track); }));
  assertSequenceRenderBudget(next);
  return next;
}

export interface DurationBindingChange {
  scopeId: string | null; trackId: string; clipId: string; sequenceId: string; before: number; after: number;
}
export interface DurationBindingProposal { project: StudioProject; changes: DurationBindingChange[] }
/** Explicit transaction proposal only. Recompute bottom-up, update EVERY
 * reference (also inactive / hidden), then validate the COMPLETE graph. Nothing
 * is applied, persisted, truncated or moved until the caller confirms. */
export function proposeSequenceDurationBindings(project: StudioProject): DurationBindingProposal {
  const next = detached(project), changes: DurationBindingChange[] = [];
  const byId = new Map((next.sequences ?? []).map(sequence => [sequence.id, sequence]));
  const complete = new Set<string>(), visiting = new Set<string>();
  const update = (scope: ProjectScope, depth: number): void => {
    for (const track of scope.tracks) for (const clip of track.clips) {
      if (!isSequenceClip(clip)) continue;
      const child = byId.get(clip.sequence_id);
      if (!child) throw new Error("嵌套引用不存在，不能更新时长绑定。");
      if (depth >= 2 || visiting.has(child.id)) throw new Error("嵌套循环或超过两层，不能更新时长绑定。");
      if (!complete.has(child.id)) {
        visiting.add(child.id); update(child, depth + 1); visiting.delete(child.id); complete.add(child.id);
      }
      const duration = sequenceFullEnd(child);
      if (!(duration > 0)) throw new Error("子序列已为空；请先恢复内容，或在父序列明确删除引用后再编辑，不能保存零时长嵌套。");
      if (Math.abs(clip.duration - duration) > 1e-12) {
        const inheritedLock = sequenceContentLock(next, scope.id);
        if (inheritedLock) throw new Error(inheritedLock);
        if (track.locked) throw new Error(`父轨道「${track.name || track.id}」已锁定，不能更新嵌套时长；请先解锁。`);
        changes.push({ scopeId: scope.id, trackId: track.id, clipId: clip.id, sequenceId: child.id, before: clip.duration, after: duration });
        clip.duration = duration;
      }
    }
  };
  update(projectScope(next, null), 0);
  for (const sequence of next.sequences ?? []) {
    visiting.add(sequence.id); update(sequence, 0); visiting.delete(sequence.id); complete.add(sequence.id);
  }
  const validated = normalizeProject(next);
  assertSequenceRenderBudget(validated);
  return { project: validated, changes };
}

/** Global group membership, unlike timeline edits, spans every sequence. */
export function removeProjectGroup(project: StudioProject, id: string): StudioProject {
  if (allProjectTracks(project).some(track => track.group_id === id && track.locked)) throw new Error("组内仍有锁定轨道（可能在其他序列），不能删除组。");
  for (const scope of projectScopes(project)) {
    const lock = sequenceContentLock(project, scope.id);
    if (lock && scope.tracks.some(track => track.group_id === id)) throw new Error(lock);
  }
  const next = detached(project);
  next.groups = next.groups.filter(group => group.id !== id);
  allProjectTracks(next).forEach(track => { if (track.group_id === id) track.group_id = null; });
  return next;
}

export interface SequenceClipboard {
  taskId: string; sequenceId: string | null; trackId: string; type: TrackType; cut: boolean; clip: StudioClip;
}
export function captureSequenceClipboard(project: StudioProject, taskId: string, clipId: string, cut: boolean): SequenceClipboard {
  const lock = sequenceContentLock(project);
  if (lock) throw new Error(lock);
  const scope = projectScope(project), track = scope.tracks.find(track => track.clips.some(clip => clip.id === clipId));
  const clip = track?.clips.find(clip => clip.id === clipId);
  if (!clip || !track || track.locked) throw new Error("剪贴来源须为活动序列中存在且未锁定的片段。");
  return { taskId, sequenceId: scope.id, trackId: track.id, type: track.type, cut, clip: normalizeClip(clip) };
}
/** Copies keep complete parameters and nested references with one fresh clip
 * ID. Cross-sequence CUT is deliberately blocked: /actions targets active only.
 * A cut proposal may be checked here, then submitted as one server move action. */
export function pasteSequenceClipboard(project: StudioProject, clipboard: SequenceClipboard, options: {
  taskId: string; trackId: string; start: number; newClipId?: string; sourceIds?: readonly string[]; lutIds?: readonly string[];
}): StudioProject {
  if (clipboard.taskId !== options.taskId) throw new Error("剪贴板仅限原任务，不可跨任务粘贴。");
  const base = normalizeProject(project), scope = projectScope(base);
  const lock = sequenceContentLock(base) ?? sequenceContentLock(base, clipboard.sequenceId);
  if (lock) throw new Error(lock);
  if (clipboard.cut && clipboard.sequenceId !== scope.id) throw new Error("不支持跨序列剪切；请返回来源序列粘贴，或重新复制。未删除原片段。");
  const origin = projectScopes(base).find(item => item.id === clipboard.sequenceId)?.tracks.find(track => track.id === clipboard.trackId);
  const liveOrigin = allProjectTracks(base).find(track => track.clips.some(clip => clip.id === clipboard.clip.id));
  if (origin?.locked || liveOrigin?.locked) throw new Error("剪贴来源轨道已锁定（含其他序列），请解锁并重新复制。");
  const target = scope.tracks.find(track => track.id === options.trackId);
  if (!target || target.locked || target.type !== clipboard.type) throw new Error("粘贴目标须为活动序列中同类型未锁定轨道。");
  const clip = normalizeClip(clipboard.clip);
  if (clip.source_id && options.sourceIds && !options.sourceIds.includes(clip.source_id)) throw new Error("剪贴板的 source_id 已不在当前任务目录中。");
  if (clip.lut_id && options.lutIds && !options.lutIds.includes(clip.lut_id)) throw new Error("剪贴板的 LUT 已不在当前任务目录中。");
  let id = options.newClipId;
  if (clipboard.cut) {
    const current = origin?.clips.find(item => item.id === clip.id);
    if (!current || JSON.stringify(normalizeClip(current)) !== JSON.stringify(clip)) throw new Error("剪切后原片段已变更 / 删除；请重新剪切，未移动任何片段。");
    id = current.id;
  }
  if (!id || (!clipboard.cut && id === clip.id)) throw new Error("复制粘贴必须提供新的全局片段 ID。");
  const pastedId = id;
  const next = normalizeProject(editActiveProject(base, working => {
    if (clipboard.cut && origin?.id === target.id) {
      const current = working.tracks.find(track => track.id === target.id)!.clips.find(item => item.id === clip.id)!;
      current.start = options.start; // Preserve same-track compositing order.
    } else {
      if (clipboard.cut) working.tracks.forEach(track => { track.clips = track.clips.filter(item => item.id !== clip.id); });
      working.tracks.find(track => track.id === target.id)!.clips.push({ ...detached(clip), id: pastedId, start: options.start });
    }
  }));
  assertSequenceRenderBudget(next);
  return next;
}