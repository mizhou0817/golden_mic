import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import StudioAssets, { EMPTY_STUDIO_ASSETS, StudioLutPicker, importStudioAsset } from "./StudioAssets";
import type { StudioAssetsState } from "./StudioAssets";
import StudioProxy, { EMPTY_STUDIO_PROXIES, PROXY_EXPLANATION, canPreviewProxy, createStudioProxy, proxyProfileReadout, proxySourceProblem } from "./StudioProxy";
import type { StudioPreview, StudioProxiesState } from "./StudioProxy";
import {
  createStudioApi, DEFAULT_EXPORT, FORMATS, GROUPS, formatSupport, isActiveJob,
  makeClip, makeTrack, optionsForFormat, parseProjectImport, projectWarnings, starterProject, uid,
  ANIMATED_PROPERTIES, DEFAULT_SHORTCUTS, canonicalChord, effectiveShortcuts,
  copyColorParameters, applicableColorParameters, normalizeClip, TRANSITION_KINDS,
  isImageSource, isImageSourceId, imageDisplayLimit, makeSourceClip, IMAGE_CLIP_FIELDS, studioAssetsAvailable,
  isProxyJob, studioProxiesAvailable, upsertStudioJob, STUDIO_MAX_JOBS,
  isSequenceClip, sequenceFullEnd, sequenceRenderStats,
} from "../lib/studioApi";
import {
  activeProject, projectScope, allProjectTracks, projectClipCount, isEmptySequenceProject, editActiveProject,
  selectProjectSequence, createProjectSequence, insertNestedSequence, proposeSequenceDurationBindings,
  removeProjectGroup, captureSequenceClipboard, pasteSequenceClipboard, sequenceContentLock,
} from "../lib/studioSequences";
import type { SequenceClipboard } from "../lib/studioSequences";
import {
  buildTransitionProposal, buildRemoveTransitionProposal, transitionBoundaries,
  buildMulticamProject, isMulticamVideoSource, parseMulticamCuts,
} from "../lib/studioCompositions";
import type {
  CompositionContext, CompositionProposal, MulticamRecipe, TransitionBoundary,
} from "../lib/studioCompositions";
import {
  TIMELINE_FPS, EDIT_MODE_LABELS, secondsToFrame, frameToSeconds, formatTimecode, parseFrameInput,
  quantizeTime, collectSnapTargets, proposeTimelineTime, canSplitAt, clipEditProblem, buildDeleteAction, buildTrimAction, updateSourceMarks, validateSourceRange,
} from "../lib/timelineEditing";
import type { EditMode, TimeProposal } from "../lib/timelineEditing";
import type {
  AuditRecord, ExportFormat, ExportOptions, ProjectEnvelope, SourceCatalog, StudioAction,
  StudioCapabilities, StudioClip, StudioJob, StudioProject, StudioRequest, StudioSource,
  StudioTool, StudioTrack, ToolGroup, TrackColor, TrackType,
  AnimatedProperty, KeyPoint, ShortcutAction, AssetMetadata, WorkspaceMetadata, TimelineFPS,
  ColorParameters, RGBChannel, CurvePoint, TransitionKind, StudioAssetKind,
} from "../lib/studioApi";

export interface StudioProps {
  taskId: string;
  accessToken?: string;
  onBack: () => void;
  onError: (msg: string) => void;
  request: StudioRequest;
}

// Historical color IDs remain stable in saved projects; the visible accents use the current theme.
const COLORS: Record<TrackColor, string> = { gold: "var(--gm-gold)", cyan: "var(--gm-olive)", red: "var(--gm-red)", purple: "var(--gm-ink)", gray: "var(--gm-muted)" };
const TYPES: [TrackType, string][] = [["video", "视频"], ["audio", "音频"], ["text", "文字"], ["overlay", "素材叠加"], ["adjustment", "调整图层"]];
const STATE_NAMES: Record<StudioJob["state"], string> = { queued: "排队中", running: "编码中", succeeded: "已完成", failed: "失败", cancelled: "已取消", interrupted: "服务重启中断" };
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const mb = (bytes: number) => `${(bytes / 1048576).toFixed(2)} MiB`;
const displayableTime = (time: number) => Number.isFinite(time) && time >= 0 && time <= 3600;
const audioSource = (source: StudioSource) => !isImageSource(source) && (source.id === "narration" || /\.(mp3|wav|m4a)$/i.test(source.name));
const activeTracks = (project: StudioProject) => {
  const solo = project.tracks.some(t => t.solo && !t.hidden);
  return project.tracks.filter(t => !t.hidden && (!solo || t.solo));
};
// Older servers can still return retired catalog entries. They have no UI or
// navigation handlers here; retained entries keep the server's availability.
const RETIRED_TOOL_IDS = new Set(["aiNarr", "aiTrans", "aiFix", "interp", "aiSub", "aiAudio", "sharePlat", "shareHd"]);
const canReadOutput = (job: StudioJob) => job.kind !== "proxy" && job.state === "succeeded" && !!job.output_id;
const EDIT_EFFECTS: Record<EditMode, string> = {
  ordinary: "只改变选中片段的源入点和输出时长；其他片段不移动。",
  ripple: "改变选中片段的源入点 / 时长；同轨从旧终点开始的后续片段平移时长差，原有其他间隙保留。",
  roll: "以选中片段为左侧，改变它与下一相邻片段的绝对边界；二者外侧端点不动，源范围同步调整。",
  slip: "只改变源入点；时间线起点、输出时长与其他片段不变，不改变播放速度。",
  slide: "将选中片段移动到新起点，保持它的时长 / 源范围；修剪前后相邻片段，三者外侧端点不动。",
};
interface ProposalResult { value: TimeProposal | null; error: string }
const attemptProposal = (run: () => TimeProposal): ProposalResult => {
  try { return { value: run(), error: "" }; } catch (e) { return { value: null, error: e instanceof Error ? e.message : "无效位置" }; }
};
function downloadJson(value: unknown, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = name; document.body.append(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="gm-studio-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
function NumberField({ label, value, onChange, min, max, step = 0.01, hint, disabled = false }: {
  label: string; value: number; onChange: (value: number) => void; min: number; max: number;
  step?: number; hint?: string; disabled?: boolean;
}) {
  return <Field label={label} hint={hint}><input aria-label={label} type="number" inputMode="decimal" value={value}
    min={min} max={max} step={step} disabled={disabled} onChange={event => {
      const n = event.currentTarget.valueAsNumber;
      if (Number.isFinite(n)) onChange(n);
    }} /></Field>;
}
function Check({ label, checked, onChange, disabled = false }: {
  label: string; checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean;
}) {
  return <label className="gm-studio-check"><input type="checkbox" aria-label={label} checked={checked}
    disabled={disabled} onChange={event => onChange(event.target.checked)} />{label}</label>;
}

/** Frame mode edits integer workspace frames. Merely changing the display never
 * quantizes legacy saved times or source seconds. Existing second-mode labels stay stable. */
function TimelineField({ label, value, onChange, fps, display, min = 0, max = 120, disabled = false }: {
  label: string; value: number; onChange: (value: number) => void; fps: TimelineFPS;
  display: WorkspaceMetadata["time_display"]; min?: number; max?: number; disabled?: boolean;
}) {
  const frames = display === "frames";
  const valid = displayableTime(value);
  return <NumberField label={frames ? label.replace("（秒）", "（帧）") : label} value={frames && valid ? secondsToFrame(value, fps) : value}
    min={frames ? Math.ceil(min * fps) : min} max={frames ? Math.floor(max * fps) : max} step={frames ? 1 : 1 / fps} disabled={disabled}
    hint={!valid ? "无效时间，显示原输入待修正；未应用帧转换。" : frames ? `${formatTimecode(value, fps)} · ${fps} fps；存储 ${value}s（切换显示不改原值）` : undefined}
    onChange={n => { if (!frames || (Number.isSafeInteger(n) && n >= 0)) onChange(frames ? frameToSeconds(n, fps) : n); }} />;
}

function SnapReadout({ label, result, fps, enabled, kind }: {
  label: string; result: ProposalResult; fps: TimelineFPS; enabled: boolean; kind: string;
}) {
  const proposal = result.value;
  return <p className="gm-studio-note gm-studio-snap-readout" data-edit-proposal={kind}
    data-snap-target={proposal?.target?.id ?? ""} data-frame={proposal?.frame} data-seconds={proposal?.seconds}>
    <strong>{label}：</strong>{!proposal ? result.error : <>
      {proposal.target ? `吸附 → ${proposal.target.label}（原 ${proposal.target.time}s）` : enabled ? "6 帧内无目标" : "磁性关闭"}
      {`；将提交 ${formatTimecode(proposal.seconds, fps)} / ${proposal.frame} 帧 / ${proposal.seconds}s。`}
      {proposal.target && Math.abs(proposal.seconds - proposal.target.time) > 1e-12 && "目标不在帧网格上，提交为最近帧；不会移动目标或保证无缝相接。"}
    </>}
  </p>;
}

type CompositionCommit = (proposal: CompositionProposal,
  rebuild: (current: StudioProject, revision: number) => CompositionProposal, confirmation: string) => void;

/** Keyed task boundary prevents delayed responses from changing a different task. */
export default function Studio(props: StudioProps) {
  return <StudioEditor key={JSON.stringify([props.taskId, props.accessToken ?? null])} {...props} />;
}

function StudioEditor({ taskId, accessToken, onBack, onError, request }: StudioProps) {
  const prefix = useId();
  const root = useRef<HTMLDivElement>(null);
  const requestRef = useRef(request); requestRef.current = request;
  const errorRef = useRef(onError); errorRef.current = onError;
  const api = useMemo(() => createStudioApi(taskId, <T,>(path: string, init?: RequestInit) => requestRef.current<T>(path, init), accessToken), [taskId, accessToken]);
  // Known API routes with the supplied token, or no token for a server-guarded
  // local workspace. Catalog URLs never provide credentials or authorization.
  const sourceUrl = api.sourceUrl;
  const outputUrl = api.outputUrl;
  const mounted = useRef(true);
  const mutex = useRef(false);
  const [caps, setCaps] = useState<StudioCapabilities | null>(null);
  const [catalog, setCatalog] = useState<SourceCatalog | null>(null);
  const [assetState, setAssetState] = useState<StudioAssetsState>(EMPTY_STUDIO_ASSETS);
  const [saved, setSaved] = useState<ProjectEnvelope | null>(null);
  const [project, setProject] = useState<StudioProject | null>(null);
  const [snapshots, setSnapshots] = useState<ProjectEnvelope[]>([]);
  const [audit, setAudit] = useState<AuditRecord[]>([]);
  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const jobsRef = useRef<StudioJob[]>([]);
  const [proxyState, setProxyState] = useState<StudioProxiesState>(EMPTY_STUDIO_PROXIES);
  const proxyReadSequence = useRef(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pollError, setPollError] = useState("");
  const [reload, setReload] = useState(0);
  const [tab, setTab] = useState<ToolGroup>("timeline");
  const [clipId, setClipId] = useState("");
  const [trackId, setTrackId] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [search, setSearch] = useState("");
  const [preview, setPreview] = useState<StudioPreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const previewSeek = useRef<{ kind: "source" | "proxy"; id: string; time: number } | null>(null);
  const [sourceTime, setSourceTime] = useState(0);
  const [durations, setDurations] = useState<Record<string, number>>({});
  const [playhead, setPlayhead] = useState(0);
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>([]);
  const [groupFilter, setGroupFilter] = useState("");
  const [newGroupName, setNewGroupName] = useState("");
  const [tagDraft, setTagDraft] = useState("");
  const [srtText, setSrtText] = useState("");
  const [inPoint, setInPoint] = useState(0);
  const [outPoint, setOutPoint] = useState(3);
  const [spanDuration, setSpanDuration] = useState(3);
  const [imageDuration, setImageDuration] = useState(3);
  const [insertAt, setInsertAt] = useState(0);
  const [newType, setNewType] = useState<TrackType>("video");
  const [newTrackName, setNewTrackName] = useState("");
  const [markerLabel, setMarkerLabel] = useState("");
  const [moveTrack, setMoveTrack] = useState("");
  const [moveAt, setMoveAt] = useState(0);
  const [advancedMode, setAdvancedMode] = useState<"roll" | "slip" | "slide" | null>(null);
  const [trimInput, setTrimInput] = useState(0);
  const [durationInput, setDurationInput] = useState(1);
  const [boundaryInput, setBoundaryInput] = useState(0);
  const [timecodeInput, setTimecodeInput] = useState("");
  const [sourceRangeError, setSourceRangeError] = useState("");
  const initialRange = useRef<string | null>(null);
  const [clipClipboard, setClipClipboard] = useState<SequenceClipboard | null>(null);
  const [options, setOptions] = useState<ExportOptions>({ ...DEFAULT_EXPORT });
  const [exportTarget, setExportTarget] = useState<"render" | "export">("render");
  const [importText, setImportText] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [colorClipboard, setColorClipboard] = useState<ColorParameters | null>(null);
  const [newSequenceName, setNewSequenceName] = useState("");
  const [nestedSequenceId, setNestedSequenceId] = useState("");
  const [nestedAt, setNestedAt] = useState(0);
  // `project` ALWAYS owns the complete persisted document. Only track/marker
  // readers and edit callbacks use this detached, non-saveable active view.
  const workingProject = useMemo(() => project ? activeProject(project) : null, [project]);
  const clipCount = project ? projectClipCount(project) : 0;
  const activeSequenceLock = project ? sequenceContentLock(project) : null;
  const dirty = !!project && !!saved && JSON.stringify(project) !== JSON.stringify(saved.project);
  const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const selection = workingProject?.tracks.flatMap(track => track.clips.map(clip => ({ track, clip }))).find(item => item.clip.id === clipId);
  const selectedTrack = workingProject?.tracks.find(t => t.id === trackId);
  const selectionIsSequence = !!selection && isSequenceClip(selection.clip);
  const insertingSpan = selectedTrack?.type === "text" || selectedTrack?.type === "adjustment";
  const source = catalog?.sources.find(s => s.id === sourceId);
  const sourceIsImage = !!source && isImageSource(source);
  const selectionIsImage = isImageSourceId(selection?.clip.source_id ?? null)
    || !!catalog?.sources.some(s => s.id === selection?.clip.source_id && isImageSource(s));
  const insertionDuration = insertingSpan ? spanDuration : sourceIsImage ? imageDuration : outPoint - inPoint;
  const previewSource = preview?.kind === "source" ? catalog?.sources.find(s => s.id === preview.id) : undefined;
  const previewJob = preview?.kind === "job" ? jobs.find(j => j.id === preview.id && canReadOutput(j)) : undefined;
  const selectedProxyRecord = preview?.kind === "proxy" ? jobs.filter(isProxyJob).find(job => job.id === preview.id) : undefined;
  const previewProxy = selectedProxyRecord && studioProxiesAvailable(caps) && proxyState.status === "ready"
    && canPreviewProxy(selectedProxyRecord, catalog?.sources ?? []) ? selectedProxyRecord : undefined;
  const availableProxies = studioProxiesAvailable(caps) ? jobs.filter(isProxyJob).filter(job => canPreviewProxy(job, catalog?.sources ?? [])) : [];
  const warnings = useMemo(() => [...(project ? projectWarnings(project, durations, catalog?.sources, assetState.status === "ready" ? assetState.catalog?.luts : undefined) : []), ...(sourceRangeError ? [sourceRangeError] : [])], [project, durations, catalog, assetState, sourceRangeError]);
  const active = useMemo(() => workingProject ? activeTracks(workingProject) : [], [workingProject]);
  const renderStats = useMemo(() => { try { return project ? sequenceRenderStats(project) : null; } catch { return null; } }, [project]);
  const durationBindings = useMemo(() => {
    try { return { proposal: project ? proposeSequenceDurationBindings(project) : null, error: "" }; }
    catch (e) { return { proposal: null, error: e instanceof Error ? e.message : "时长绑定提案无效。" }; }
  }, [project]);
  const duration = Math.max(0, ...active.flatMap(t => t.clips.map(c => c.start + c.duration)));
  const hasBurnedFinal = renderStats?.visualSourceIds.includes("final") ?? active.some(t => ["video", "overlay"].includes(t.type) && t.clips.some(c => c.source_id === "final"));
  const zoom = project?.workspace.timeline_zoom ?? 1;
  const layout = project?.workspace.layout ?? "default";
  const timelineFPS = project?.workspace.timeline_fps ?? 30;
  const timeDisplay = project?.workspace.time_display ?? "seconds";
  const snapEnabled = project?.workspace.snap_enabled ?? true;
  const rippleEnabled = project?.workspace.ripple_enabled ?? false;
  const editMode: EditMode = advancedMode ?? (rippleEnabled ? "ripple" : "ordinary");
  const asset = project?.assets.find(a => a.source_id === sourceId);
  const sourceMark = project?.workspace.source_marks.find(mark => mark.source_id === sourceId);
  const shortcuts = effectiveShortcuts(project?.workspace.shortcuts ?? {});
  const running = jobs.some(isActiveJob);
  const frozen = loading || !!busy;
  const tools = useMemo(() => (caps?.tools ?? []).filter(tool => !RETIRED_TOOL_IDS.has(tool.id))
    .map(tool => tool.id === "draftOps" ? { ...tool, label: "工程快照 / 本地备份" } : tool), [caps]);
  const toolGroups = GROUPS.filter(([group]) => tools.some(tool => tool.group === group))
    .map(([group, label]): [ToolGroup, string] => [group, group === "export" ? "专业导出" : label]);
  const activeToolGroup = toolGroups.some(([group]) => group === tab) ? tab : toolGroups[0]?.[0];
  const capabilities = new Map(tools.map(t => [t.id, t]));
  const supports = (id: string) => !!capabilities.get(id)?.available;
  const snapActive = snapEnabled && supports("snap");
  const compositionMainOnly = (project?.active_sequence_id ?? null) === null
    && !project?.tracks.some(track => track.clips.some(isSequenceClip));
  const targets = useMemo(() => workingProject ? collectSnapTargets(workingProject, clipId) : [], [workingProject, clipId]);
  const insertTargets = useMemo(() => workingProject ? collectSnapTargets(workingProject) : [], [workingProject]);
  const nestedChild = project?.sequences.find(sequence => sequence.id === nestedSequenceId);
  const nestedPosition = attemptProposal(() => proposeTimelineTime(nestedAt, timelineFPS, snapActive, insertTargets,
    { max: Math.max(0, 120 - (nestedChild ? sequenceFullEnd(nestedChild) : 0)) }));
  const moveProposal = attemptProposal(() => proposeTimelineTime(moveAt, timelineFPS, snapActive, targets,
    { max: Math.max(0, 120 - (selection?.clip.duration ?? 0)) }));
  const insertProposal = attemptProposal(() => proposeTimelineTime(insertAt, timelineFPS, snapActive, insertTargets,
    { max: Math.max(0, 120 - Math.max(0, insertionDuration)) }));
  const splitProposal = attemptProposal(() => proposeTimelineTime(playhead, timelineFPS, snapActive, targets,
    selection ? { min: selection.clip.start + 1 / timelineFPS, max: selection.clip.start + selection.clip.duration - 1 / timelineFPS } : {}));
  const canSplit = !!selection && !activeSequenceLock && !selectionIsSequence && !selection.track.locked && !!splitProposal.value && canSplitAt(selection.clip, splitProposal.value.seconds, timelineFPS);
  const canMove = !!workingProject && !activeSequenceLock && !!selection && !!moveProposal.value
    && clipEditProblem(workingProject, selection.clip.id, moveTrack || selection.track.id) === null;
  const canDelete = !!selection && !activeSequenceLock && !selection.track.locked && supports("edit") && (selectionIsSequence || !rippleEnabled || supports("ripple"));
  const displayTime = (time: number) => !displayableTime(time) ? "无效时间（待修正）" : timeDisplay === "frames"
    ? `${formatTimecode(time, timelineFPS)} · ${secondsToFrame(time, timelineFPS)}f` : `${Number(time.toFixed(6))}s`;
  const trimProposal = (() => {
    if (!selection) return { payload: null, error: "请先选择片段。" };
    if (selectionIsSequence) return { payload: null, error: "完整嵌套只支持父级移动 / 普通删除；范围与效果须打开子序列编辑。" };
    if (selectionIsImage && editMode === "slip") return { payload: null, error: "静态图片没有可滑移的源时间；请选择显示时长修剪。" };
    try { return { payload: buildTrimAction(editMode, selection.clip, { trim: selectionIsImage ? 0 : trimInput, duration: durationInput, at: boundaryInput }, timelineFPS), error: "" }; }
    catch (e) { return { payload: null, error: e instanceof Error ? e.message : "修剪参数无效。" }; }
  })();
  const compositionContext: CompositionContext = {
    sources: catalog?.sources ?? [], durations, limits: caps?.limits,
    // A dirty draft is saved first, then the confirmed composition consumes one
    // more revision. The helpers preflight the final save's history / byte caps.
    expectedRevision: (saved?.revision ?? 0) + (dirty ? 1 : 0),
  };

  const reportError = (e: unknown) => {
    const message = e instanceof Error ? e.message : String(e);
    if (mounted.current) { setError(message); errorRef.current(message); }
  };
  const remember = (next: ProjectEnvelope) => {
    if (!mounted.current) return;
    setSaved(next); setProject(clone(next.project));
    const scope = projectScope(next.project);
    setTrackId(old => scope.tracks.some(track => track.id === old) ? old : scope.tracks[0]?.id ?? "");
    setClipId(old => scope.tracks.some(track => track.clips.some(clip => clip.id === old)) ? old : "");
    setMoveTrack(old => scope.tracks.some(track => track.id === old) ? old : "");
    setSnapshots(old => [next, ...old.filter(s => s.revision !== next.revision)].slice(0, 100));
  };
  const upsertJob = (job: StudioJob) => {
    if (!mounted.current) return;
    // Validate job IDs and merge synchronously so response errors reach the
    // operation/poll catch, not a later React state-updater exception.
    const next = upsertStudioJob(jobsRef.current, job);
    jobsRef.current = next; setJobs(next);
  };
  const refreshAudit = async () => { const records = await api.audit(); if (mounted.current) setAudit(records); };
  const refreshProxyJobs = async (capabilities = caps, signal?: AbortSignal) => {
    if (!studioProxiesAvailable(capabilities)) throw new Error("服务器未声明源代理能力；未请求代理端点。");
    const sequence = ++proxyReadSequence.current;
    if (mounted.current) setProxyState({ status: "loading", message: "" });
    try {
      const proxies = await api.proxies(signal);
      if (!mounted.current || signal?.aborted || sequence !== proxyReadSequence.current) return null;
      // This GET is the full bounded proxy list. Keep ordinary job history;
      // remove absent proxy records without inventing cancelled/failed states.
      let next = jobsRef.current.filter(job => job.kind !== "proxy" || proxies.some(row => row.id === job.id));
      for (const job of proxies) next = upsertStudioJob(next, job);
      jobsRef.current = next; setJobs(next); setProxyState({ status: "ready", message: "" });
      return proxies;
    } catch (e) {
      if (mounted.current && !signal?.aborted && sequence === proxyReadSequence.current) {
        setProxyState({ status: "error", message: e instanceof Error ? e.message : "代理状态读取失败，保留上次记录。" });
      }
      throw e;
    }
  };

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    setLoading(true); setError(""); setAssetState(EMPTY_STUDIO_ASSETS);
    proxyReadSequence.current++; setProxyState(EMPTY_STUDIO_PROXIES);
    void (async () => {
      try {
        const [c, p, s] = await Promise.all([api.capabilities(controller.signal), api.project(controller.signal), api.sources(controller.signal)]);
        if (!live) return;
        setCaps(c); setCatalog(s); remember(p);
        if (p.revision === 0 && isEmptySequenceProject(p.project)) {
          const initial = { ...p.project, tracks: starterProject().tracks }; setProject(initial); setTrackId(initial.tracks[0].id);
        } else setTrackId(projectScope(p.project).tracks[0]?.id ?? "");
        const first = s.sources.find(item => item.id === "final") ?? s.sources[0];
        if (first) {
          initialRange.current = isImageSource(first) || p.project.workspace.source_marks.some(mark => mark.source_id === first.id) ? null : first.id;
          setSourceId(first.id); setPreview({ kind: "source", id: first.id });
        }
        setLoading(false);
        // Optional asset reads require an explicit server capability, just like
        // original sources. Unavailable fixtures never receive /assets.
        if (studioAssetsAvailable(c)) {
          setAssetState({ status: "loading", catalog: null, message: "" });
          void api.assets(controller.signal).then(assets => {
            if (live) setAssetState({ status: "ready", catalog: assets, message: "" });
          }).catch(e => {
            if (live) setAssetState({ status: "error", catalog: null,
              message: e instanceof Error ? e.message : "素材目录暂不可用。" });
          });
        }
        // Read persisted proxies on reopen ONLY when capability is available.
        // Auxiliary source reads never submit a new job.
        if (studioProxiesAvailable(c)) {
          void refreshProxyJobs(c, controller.signal).catch(() => { /* The card keeps a manual GET refresh action. */ });
        }
        // Audit is auxiliary; a failure must not hide an otherwise loaded project.
        try {
          const records = await api.audit(controller.signal);
          if (!live) return;
          setAudit(records);
          const ids = [...new Set(records.map(r => r.job_id).filter((id): id is string => !!id && /^[a-f0-9]{32}$/.test(id)))];
          const results = await Promise.allSettled(ids.slice(-20).map(id => api.job(id, controller.signal)));
          if (!live) return;
          results.forEach(result => { if (result.status === "fulfilled") upsertJob(result.value); });
          if (results.some(result => result.status === "rejected")) setPollError("部分历史任务暂时无法读取；可刷新审计重试。");
        } catch (e) { if (live) setPollError(`审计暂不可用：${e instanceof Error ? e.message : String(e)}`); }
      } catch (e) { if (live) { reportError(e); setLoading(false); } }
    })();
    return () => { live = false; controller.abort(); };
  }, [api, reload]);

  useEffect(() => {
    const beforeLeave = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || mutex.current) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", beforeLeave);
    return () => window.removeEventListener("beforeunload", beforeLeave);
  }, []);

  const pollingIds = jobs.filter(isActiveJob).map(j => j.id).sort().join(",");
  useEffect(() => {
    if (!pollingIds) return;
    let live = true; let inFlight = false; let timer: number | undefined;
    const controller = new AbortController();
    const tick = async () => {
      if (!live || inFlight || document.hidden) return;
      inFlight = true;
      let failed = false;
      for (const id of pollingIds.split(",")) {
        try {
          const job = await api.job(id, controller.signal);
          if (!live) return;
          upsertJob(job);
          if (!isActiveJob(job)) {
            if (job.state === "failed" || job.state === "interrupted") reportError(new Error(job.error ?? STATE_NAMES[job.state]));
            try { await refreshAudit(); } catch { /* Job polling remains independent of audit. */ }
          }
        } catch (e) {
          if (!live) return;
          failed = true; setPollError(`状态查询失败，保留上次状态并重试：${e instanceof Error ? e.message : String(e)}`);
        }
      }
      if (!live) return;
      if (!failed) setPollError("");
      inFlight = false;
      timer = window.setTimeout(() => void tick(), failed ? 6000 : 1800);
    };
    const visibility = () => { window.clearTimeout(timer); if (!document.hidden) void tick(); };
    document.addEventListener("visibilitychange", visibility);
    void tick();
    return () => { live = false; controller.abort(); window.clearTimeout(timer); document.removeEventListener("visibilitychange", visibility); };
  }, [api, pollingIds]);

  useEffect(() => {
    setPreviewError(""); setSourceTime(0);
    if (previewSeek.current && (previewSeek.current.kind !== preview?.kind || previewSeek.current.id !== preview.id)) previewSeek.current = null;
  }, [preview?.kind, preview?.id]);

  // Server save/import/undo receipts restore both workspace settings and source marks.
  // Changing FPS never reinterprets source seconds or rewrites existing clip geometry.
  useEffect(() => {
    const mark = saved?.project.workspace.source_marks.find(item => item.source_id === sourceId);
    setInPoint(sourceIsImage ? 0 : mark?.in_point ?? 0);
    setOutPoint(sourceIsImage ? 0 : mark?.out_point ?? Math.min(3, durations[sourceId] ?? 3));
    initialRange.current = sourceIsImage || mark ? null : sourceId;
    setSourceRangeError("");
  }, [saved]);
  useEffect(() => {
    if (!selection) return;
    setTrimInput(selection.clip.trim); setDurationInput(selection.clip.duration);
    setBoundaryInput(advancedMode === "slide" ? selection.clip.start : selection.clip.start + selection.clip.duration);
  }, [clipId, selection?.clip.start, selection?.clip.trim, selection?.clip.duration, advancedMode]);
  useEffect(() => {
    // Source selection/marks and task clipboard survive a timeline switch.
    // A previous sequence's rendered player must not drive the new playhead.
    setPlayhead(0); setMoveAt(0); setInsertAt(0); setNestedAt(0); setTimecodeInput("");
    setAdvancedMode(null); setGroupFilter(""); setCollapsedGroups([]);
    setPreview(current => current?.kind === "job" ? null : current);
  }, [project?.active_sequence_id]);
  useEffect(() => {
    if (nestedSequenceId && !project?.sequences.some(sequence => sequence.id === nestedSequenceId)) setNestedSequenceId("");
  }, [project?.sequences, nestedSequenceId]);
  useEffect(() => {
    if (catalog && sourceId && !catalog.sources.some(source => source.id === sourceId)) {
      setSourceId(""); setTagDraft(""); setSourceRangeError(""); initialRange.current = null;
      // Do not pick another source or rewrite persisted marks as a fallback.
      setPreview(current => current?.kind === "source" && current.id === sourceId ? null : current);
    }
  }, [catalog, sourceId]);

  async function operation(label: string, run: () => Promise<void>): Promise<boolean> {
    if (mutex.current || !mounted.current) return false;
    mutex.current = true; setBusy(label); setError(""); setNotice("");
    try { await run(); return true; }
    catch (e) { reportError(e); return false; }
    finally { mutex.current = false; if (mounted.current) setBusy(""); }
  }
  async function saveDraft(): Promise<ProjectEnvelope> {
    if (!project || !saved) throw new Error("工程尚未加载。");
    if (warnings.length) throw new Error(warnings.join("\n"));
    if (!dirty) return saved;
    const bytes = new TextEncoder().encode(JSON.stringify({ expected_revision: saved.revision, project })).length;
    if (bytes > (caps?.limits.request_bytes ?? 262144)) throw new Error("工程超过 256 KiB 请求限制。");
    const next = await api.save(saved.revision, project);
    remember(next);
    return next;
  }
  const save = () => void operation("保存工程", async () => {
    const next = await saveDraft(); setNotice(`已保存快照 r${next.revision}；未渲染的修改不会出现在播放器中。`);
    try { await refreshAudit(); } catch { setNotice(`已保存 r${next.revision}；审计刷新失败，可稍后重试。`); }
  });
  const switchSequence = (id: string | null) => {
    if (frozen || mutex.current || !project || !saved || !supports("tlPick") || id === project.active_sequence_id) return;
    void operation("保存草稿并切换活动序列", async () => {
      projectScope(project, id);
      // A saved but over-budget render root must still allow navigating to its
      // child to fix it. Dirty drafts always go through ordinary save checks.
      const current = dirty ? await saveDraft() : saved;
      if (!mounted.current) return;
      const next = await api.save(current.revision, selectProjectSequence(current.project, id));
      remember(next);
      if (!mounted.current) return;
      setNotice(`活动序列已由服务器保存：${projectScope(next.project).name} · r${next.revision}。根轨、其他序列与源标记均保留；渲染仅使用这个活动序列。`);
      try { await refreshAudit(); } catch { /* Real save receipt, never retry a selection POST. */ }
    });
  };
  const addSequence = () => {
    if (frozen || mutex.current || !project || !supports("tlPick") || !newSequenceName.trim()) return;
    const id = uid("sequence"), name = newSequenceName.trim();
    void operation("保存草稿并新建序列", async () => {
      createProjectSequence(project, id, name);
      const current = await saveDraft();
      if (!mounted.current) return;
      const next = await api.save(current.revision, createProjectSequence(current.project, id, name));
      remember(next);
      if (!mounted.current) return;
      setNewSequenceName(""); setNestedSequenceId("");
      setNotice(`已新建并打开空序列「${name}」· r${next.revision}。请明确添加轨道与片段；未复制主时间线，也未编造媒体。`);
      try { await refreshAudit(); } catch { /* Creation is already committed. */ }
    });
  };
  const insertSequence = () => {
    if (frozen || mutex.current || !project || !nestedChild || !nestedPosition.value || !supports("nest")) return;
    const options = { sequenceId: nestedChild.id, start: nestedPosition.value.seconds, trackId: uid("track"), clipId: uid("clip") };
    let proposed: StudioProject;
    try {
      proposed = insertNestedSequence(project, options);
      const problems = projectWarnings(proposed, durations, catalog?.sources, assetState.status === "ready" ? assetState.catalog?.luts : undefined);
      if (problems.length) throw new Error(problems.join("\n"));
      if (!window.confirm(`在「${projectScope(project).name}」新增独立叠加轨，插入完整「${nestedChild.name}」？\n起点 ${options.start}s；子序列范围 0–${sequenceFullEnd(nestedChild)}s，时长严格绑定。\n父级仅移动 / 普通删除；不能裁剪、变速、缩放或施加效果 / 音量。子文字始终高于全部媒体。\n${dirty ? "先保存当前草稿，再保存嵌套提案。" : "提交一次真实工程保存。"}不会自动渲染。`)) return;
    } catch (e) { reportError(e); return; }
    void operation("插入完整有界嵌套", async () => {
      const current = await saveDraft();
      if (!mounted.current) return;
      const fresh = insertNestedSequence(current.project, options);
      if (JSON.stringify(fresh) !== JSON.stringify(proposed)) throw new Error("保存回执改变了嵌套提案依据；请重新确认，尚未插入。");
      const next = await api.save(current.revision, fresh);
      remember(next);
      if (!mounted.current) return;
      const track = projectScope(next.project).tracks.find(track => track.id === options.trackId);
      const clip = track?.clips.find(clip => clip.id === options.clipId);
      if (track && clip) pickClip(track, clip);
      setNotice(`完整嵌套已保存 r${next.revision}；双击片段或“打开所选嵌套”编辑子序列。未自动缩放、重定时或渲染。`);
      try { await refreshAudit(); } catch { /* Keep actual committed revision. */ }
    });
  };
  const confirmDurationBindings = () => {
    if (frozen || mutex.current || !project || !saved || !supports("nest")) return;
    const shown = durationBindings.proposal;
    if (!shown?.changes.length) return;
    try {
      if (sourceRangeError) throw new Error(sourceRangeError);
      const problems = projectWarnings(shown.project, durations, catalog?.sources, assetState.status === "ready" ? assetState.catalog?.luts : undefined);
      if (problems.length) throw new Error(problems.join("\n"));
      if (!window.confirm(`确认将子序列草稿与全部 ${shown.changes.length} 处父时长绑定作为同一次保存？\n${shown.changes.map(change => `${change.clipId}：${change.before}s → ${change.after}s`).join("\n")}\n包括未激活 / 隐藏父引用；不移动任何起点、邻片或标记，不缩放任何源范围 / 关键帧。锁定 / 越界会拒绝。`)) return;
    } catch (e) { reportError(e); return; }
    void operation("明确确认全部嵌套时长绑定并保存", async () => {
      const fresh = proposeSequenceDurationBindings(project);
      if (JSON.stringify(fresh) !== JSON.stringify(shown)) throw new Error("时长绑定提案已变更，请重新确认。");
      // Do not call saveDraft: the pending child edit is deliberately invalid
      // until ALL parent bindings are explicitly committed in this transaction.
      const next = await api.save(saved.revision, fresh.project);
      remember(next);
      if (!mounted.current) return;
      setNotice(`子草稿与 ${fresh.changes.length} 处父时长绑定已原子保存 r${next.revision}；可用服务器撤销，其他起点不动。`);
      try { await refreshAudit(); } catch { /* Never replay a confirmed transaction. */ }
    });
  };
  const refreshProxies = () => void operation("读取代理状态（不创建）", async () => {
    await refreshProxyJobs();
    if (mounted.current) { setPreviewError(""); setNotice("已刷新真实代理记录；未创建代理、未切换监看质量，也未更改工程。"); }
  });
  const startProxy = (): Promise<boolean> => operation("保存草稿并创建 / 复用源代理", async () => {
    if (!studioProxiesAvailable(caps) || proxyState.status !== "ready" || !source) throw new Error("请先选择原目录视频并刷新代理状态。");
    if (jobsRef.current.some(isActiveJob)) throw new Error("已有 Studio 作业运行，请等待或在后台作业列表中取消。");
    if (jobsRef.current.length >= STUDIO_MAX_JOBS && !jobsRef.current.some(job => isProxyJob(job)
      && job.source_id === source.id && job.state === "succeeded")) throw new Error("已到达共同作业上限；请刷新状态核对已有缓存。");
    const problem = proxySourceProblem(source, durations[source.id]);
    if (problem) throw new Error(problem);
    try {
      const job = await createStudioProxy({ api, source, originalDuration: durations[source.id], onDirtySave: saveDraft,
        isCurrent: () => mounted.current });
      if (!mounted.current) return;
      upsertJob(job);
      // A proxy receipt is NOT a project receipt. No remember(), source/trim
      // changes, options mutations, implicit preview or output URL.
      setNotice(`${job.cached === true ? "服务器已确认复用已有源代理" : "源代理作业已确认"} · ${STATE_NAMES[job.state]} · 捕获 r${job.revision}。代理本身未推进工程修订；请手动选择预览质量。`);
      try { await refreshAudit(); } catch { /* Poll this real job ID; never replay POST. */ }
    } catch (e) {
      if (!mounted.current) return;
      // A rejected/unknown receipt can follow an accepted POST. Reconcile with
      // ONE read, never recreate. Require explicit refresh before another POST.
      try { await refreshProxyJobs(); } catch { /* Preserve the original uncertainty below. */ }
      if (!mounted.current) return;
      const message = `创建 / 复用未确认：${e instanceof Error ? e.message : "响应未知"} 已尝试只读查询；请点击“刷新代理状态”核对，未自动重新提交。`;
      setProxyState({ status: "error", message });
      throw new Error(message);
    }
  });
  const cancelJob = (job: StudioJob): Promise<boolean> => operation("取消后台作业", async () => {
    try { upsertJob(await api.cancel(job.id)); }
    catch (e) {
      // Cancellation is a real common DELETE, also for proxies. Uncertainty
      // permits a GET only, not another DELETE / POST or a guessed state.
      if (mounted.current) { try { upsertJob(await api.job(job.id)); } catch { /* Manual refresh remains available. */ } }
      if (mounted.current && job.kind === "proxy") setProxyState({ status: "error", message: "取消结果未确认；已尝试读取真实状态，请刷新代理状态，未重复取消或创建。" });
      throw e;
    }
    if (!mounted.current) return;
    try { await refreshAudit(); } catch { setNotice("已读取取消回执；审计刷新失败，可稍后手动刷新。"); }
  });
  const refreshAssetCatalog = async () => {
    if (!studioAssetsAvailable(caps)) throw new Error("服务器未声明素材导入能力；未请求素材端点。");
    const [assets, sources] = await Promise.all([api.assets(), api.sources()]);
    if (!mounted.current) throw new Error("编辑器已关闭，未采用旧素材目录。");
    setAssetState({ status: "ready", catalog: assets, message: "" }); setCatalog(sources);
    return { assets, sources };
  };
  const refreshAssets = () => void operation("刷新图片 / LUT 与源目录", async () => {
    if (!studioAssetsAvailable(caps)) return;
    setAssetState(old => ({ ...old, status: "loading", message: "" }));
    try { await refreshAssetCatalog(); }
    catch (e) {
      if (mounted.current) setAssetState(old => ({ ...old, status: "error", message: e instanceof Error ? e.message : "素材目录刷新失败。" }));
      throw e;
    }
  });
  const uploadAsset = (kind: StudioAssetKind, file: File): Promise<boolean> => operation("保存草稿并导入本地素材", async () => {
    if (!supports(kind === "image" ? "stickerCustom" : "lut") || assetState.status !== "ready" || !assetState.catalog) {
      throw new Error("请先确认服务器素材能力并成功读取当前目录。");
    }
    const result = await importStudioAsset({ api, kind, file, limits: assetState.catalog.limits,
      onDirtySave: saveDraft, isCurrent: () => mounted.current });
    if (!mounted.current) return;
    // This is an asset receipt, NOT a project mutation. Do not remember(),
    // increment revision, invent an undo snapshot or auto-insert a clip / LUT.
    setAssetState(old => {
      if (!old.catalog) return old;
      const next = result.kind === "image"
        ? { ...old.catalog, images: [...old.catalog.images.filter(row => row.id !== result.receipt.asset.id), result.receipt.asset] }
        : { ...old.catalog, luts: [...old.catalog.luts.filter(row => row.id !== result.receipt.asset.id), result.receipt.asset] };
      return { status: "loading", catalog: next, message: "" };
    });
    const receipt = result.receipt;
    setNotice(`${receipt.deduplicated ? "服务器确认已有相同素材，已去重" : "素材已导入"}：${receipt.asset.name} · 工程仍为 r${receipt.project_revision}。未添加片段或应用 LUT。`);
    try {
      const fresh = await refreshAssetCatalog();
      if (!mounted.current) return;
      if (result.kind === "image") {
        const image = fresh.sources.sources.find(row => row.id === receipt.asset.id && isImageSource(row));
        if (!image) throw new Error("图片导入已确认，但源目录尚未返回该图片；请刷新目录，勿重复上传。");
        chooseSource(image.id, fresh.sources);
      }
    } catch (e) {
      if (mounted.current) setAssetState(old => ({ ...old, status: "error",
        message: `导入已由服务器确认，但目录刷新未完成：${e instanceof Error ? e.message : "请手动刷新。"}` }));
    }
    if (mounted.current) { try { await refreshAudit(); } catch { /* Asset receipt remains authoritative; never retry POST. */ } }
  });
  const commitComposition: CompositionCommit = (shown, rebuild, confirmation) => {
    if (frozen || mutex.current || !project || !saved) return;
    try {
      if (!compositionMainOnly) throw new Error("现有转场 / 多机位构建器仅支持不含嵌套引用的主时间线；子序列控件已停用，未将子轨写入主轨。");
      const available = shown.kind === "transition-add" || shown.kind === "transition-remove"
        ? supports("trLib") && supports("trDur")
        : supports("camN") && supports("camAlign") && (shown.kind !== "multicam-switch" || supports("camBatch"));
      if (!available) throw new Error("服务器未声明此合成能力，未提交工程变更。");
      const check = rebuild(project, saved.revision + (dirty ? 1 : 0));
      if (JSON.stringify(check.project) !== JSON.stringify(shown.project)) throw new Error("工程或表单已变更，请重新检查提案后确认。");
      if (!window.confirm(`${confirmation}\n${dirty ? "将先保存当前草稿，再提交此提案（两次服务器历史变更）。" : "将以当前服务器修订提交一次工程保存。"}\n只更新 Studio 工程，不覆盖原成片 / 原素材 / 报告 / QC；效果需另行渲染。继续？`)) return;
    } catch (e) { reportError(e); return; }
    void operation("保存已确认的合成提案", async () => {
      const current = await saveDraft();
      if (!mounted.current) return;
      // Rebuild against the ACTUAL save receipt, not a stale revision or a
      // guessed server mutation. If normalization changed the plan, re-confirm.
      const fresh = rebuild(current.project, current.revision);
      if (JSON.stringify(fresh.project) !== JSON.stringify(shown.project)) throw new Error("保存回执改变了提案依据；当前草稿已保存，但合成未提交，请重新预览并确认。");
      const problems = projectWarnings(fresh.project, durations, catalog?.sources, assetState.status === "ready" ? assetState.catalog?.luts : undefined);
      if (problems.length) throw new Error(problems.join("\n"));
      const next = await api.save(current.revision, fresh.project);
      remember(next);
      if (!mounted.current) return;
      setTrackId(fresh.focus.trackId); setClipId(fresh.focus.clipId);
      setNotice(`合成提案已保存为真实快照 r${next.revision}；可用服务器撤销 / 重做，尚未渲染。原文件与质检未改变。`);
      try { await refreshAudit(); } catch { /* Only the successful save receipt installs this project. */ }
    });
  };
  const action = (payload: StudioAction) => void operation("应用编辑", async () => {
    if (activeSequenceLock && ("clip_id" in payload || payload.op === "track")) throw new Error(activeSequenceLock);
    if (project && "clip_id" in payload) {
      const working = activeProject(project);
      const nested = working.tracks.flatMap(track => track.clips).find(clip => clip.id === payload.clip_id);
      if (nested && isSequenceClip(nested) && payload.op !== "move" && payload.op !== "delete") throw new Error("完整嵌套父片段仅可移动 / 普通删除；不能分割、波纹、修剪或应用源效果。");
      const problem = clipEditProblem(working, payload.clip_id, "track_id" in payload ? payload.track_id : undefined);
      if (problem) throw new Error(problem);
    }
    const current = await saveDraft();
    if (!mounted.current) return;
    if (project && payload.op !== "undo" && payload.op !== "redo" && current.project.active_sequence_id !== project.active_sequence_id) {
      throw new Error("保存回执改变了活动序列，请重新确认动作目标；未对其他序列执行动作。");
    }
    const receiptLock = sequenceContentLock(current.project);
    if (receiptLock && ("clip_id" in payload || payload.op === "track")) throw new Error(receiptLock);
    if ("clip_id" in payload) {
      const problem = clipEditProblem(activeProject(current.project), payload.clip_id, "track_id" in payload ? payload.track_id : undefined);
      if (problem) throw new Error(problem);
    }
    const next = await api.action(current.revision, payload); remember(next);
    if (!mounted.current) return;
    setNotice(`已应用 ${payload.op} · r${next.revision}`);
    try { await refreshAudit(); } catch { /* Mutation success remains authoritative. */ }
  });
  const edit = (change: (p: StudioProject) => void) => {
    if (mutex.current || !project) return false;
    try { setProject(editActiveProject(project, change)); return true; }
    catch (e) { reportError(e); return false; }
  };
  const updateClip = (patch: Partial<StudioClip>) => {
    if (frozen || mutex.current || !selection || selection.track.locked) return;
    if (activeSequenceLock) { reportError(new Error(activeSequenceLock)); return; }
    if (selectionIsSequence && Object.keys(patch).some(key => key !== "start")) {
      reportError(new Error("完整嵌套父片段只能改变起点；其他编辑请打开子序列，改变时长后须明确更新全部父绑定。")); return;
    }
    const nextId = patch.source_id === undefined ? selection.clip.source_id : patch.source_id;
    const nextSource = catalog?.sources.find(s => s.id === nextId);
    const still = isImageSourceId(nextId) || (!!nextSource && isImageSource(nextSource));
    if (still && !["video", "overlay"].includes(selection.track.type)) { reportError(new Error("图片只能加入视频 / 叠加轨。")); return; }
    // A source change is an explicit user choice. Explain the required reset
    // in the inspector; never silently repair imported / persisted clips.
    const applied = still && patch.source_id !== undefined && patch.source_id !== selection.clip.source_id
      ? { ...patch, ...IMAGE_CLIP_FIELDS } : patch;
    if (still && Object.keys(IMAGE_CLIP_FIELDS).some(key => {
      const field = key as keyof typeof IMAGE_CLIP_FIELDS;
      return applied[field] !== undefined && applied[field] !== IMAGE_CLIP_FIELDS[field];
    })) { reportError(new Error("图片没有源时钟或音轨：保持 trim=0、1×、静音、不倒放 / 定格；请编辑显示时长。")); return; }
    edit(p => { const target = p.tracks.flatMap(t => t.clips).find(c => c.id === clipId); if (target) Object.assign(target, applied); });
  };
  const useLut = (id: string | null) => {
    if (frozen || !selection || selection.clip.sequence_id != null || selection.track.locked || !["video", "overlay", "adjustment"].includes(selection.track.type)) return;
    if (id !== null && (!supports("lut") || assetState.status !== "ready" || !assetState.catalog?.luts.some(row => row.id === id))) {
      reportError(new Error("LUT 必须来自此任务已加载的真实素材目录。未改变原绑定。")); return;
    }
    updateClip({ lut_id: id });
  };
  const pickClip = (track: StudioTrack, clip: StudioClip) => {
    setTrackId(track.id); setClipId(clip.id); setMoveTrack(track.id); setMoveAt(clip.start); setPlayhead(Math.max(0, Math.min(120, clip.start)));
    setTrimInput(clip.trim); setDurationInput(clip.duration);
    setBoundaryInput(advancedMode === "slide" ? clip.start : clip.start + clip.duration);
  };
  const chooseSource = (id: string, currentCatalog = catalog) => {
    const chosen = currentCatalog?.sources.find(s => s.id === id);
    if (!chosen) return;
    const still = isImageSource(chosen);
    const mark = project?.workspace.source_marks.find(item => item.source_id === id);
    initialRange.current = still || mark ? null : id;
    setSourceId(id); setTagDraft(""); setPreview({ kind: "source", id });
    setInPoint(still ? 0 : mark?.in_point ?? 0); setOutPoint(still ? 0 : mark?.out_point ?? Math.min(3, durations[id] ?? 3)); setSourceRangeError("");
    if (still) setImageDuration(Math.min(3, imageDisplayLimit(chosen)));
  };
  const preserveSourcePosition = (next: { kind: "source" | "proxy"; id: string }, originalId: string) => {
    if (next.kind === preview?.kind && next.id === preview.id) { previewSeek.current = null; return; }
    const previousSource = preview?.kind === "source" ? preview.id : preview?.kind === "proxy"
      ? jobsRef.current.find(job => job.id === preview.id && isProxyJob(job))?.source_id : undefined;
    const media = mediaRef.current;
    previewSeek.current = previousSource === originalId && !!media?.isConnected && media.readyState >= 1
      && Number.isFinite(media.currentTime) && media.currentTime >= 0 ? { ...next, time: media.currentTime } : null;
  };
  const chooseProxyPreview = (id: string) => {
    if (frozen || mutex.current || !studioProxiesAvailable(caps) || proxyState.status !== "ready") return;
    const job = jobsRef.current.find(row => row.id === id);
    if (!job || !canPreviewProxy(job, catalog?.sources ?? [])) {
      reportError(new Error("该源代理尚未完成、源已不在当前目录或状态无效；请刷新，不会改用导出地址或自动重建。")); return;
    }
    const next = { kind: "proxy" as const, id: job.id };
    preserveSourcePosition(next, job.source_id);
    if (sourceId !== job.source_id) chooseSource(job.source_id);
    setPreview(next); setNotice(`${PROXY_EXPLANATION}。请切回原片精确记点；原 source_id 与源范围不变。`);
  };
  const chooseOriginalPreview = () => {
    if (frozen || mutex.current || !source) return;
    const next = { kind: "source" as const, id: source.id };
    preserveSourcePosition(next, source.id);
    // Do not call chooseSource for the same source: keep even unsaved candidate
    // in/out fields. Only the monitor quality changes, not source authority.
    setPreview(next); setNotice("已选择原片；加载后尝试保留原源秒位置，请在原片上精确记点。入出点未改变。");
  };
  const updateWorkspace = (patch: Partial<WorkspaceMetadata>) => edit(p => { Object.assign(p.workspace, patch); });
  const chooseEditMode = (mode: EditMode) => {
    if (frozen) return;
    if (mode === "ordinary" || mode === "ripple") {
      setAdvancedMode(null); updateWorkspace({ ripple_enabled: mode === "ripple" });
    } else setAdvancedMode(mode);
  };
  const setSourceRange = (nextIn: number, nextOut: number) => {
    if (frozen || sourceIsImage) return;
    initialRange.current = null;
    setInPoint(nextIn); setOutPoint(nextOut); setSourceRangeError("");
    if (!source || !project || !catalog || !supports("inout")) return;
    try {
      const marks = updateSourceMarks(project.workspace.source_marks, source.id, { in_point: nextIn, out_point: nextOut },
        catalog.sources.map(s => s.id), durations[source.id]);
      updateWorkspace({ source_marks: marks });
    } catch (e) { setSourceRangeError(`${e instanceof Error ? e.message : "无效源范围"} 待选范围未写入工程，保留上次有效标记。`); }
  };
  const markCurrentSource = (edge: "in" | "out") => {
    if (preview?.kind === "proxy") {
      reportError(new Error("低分辨率源代理不能用于当前帧精确记点；请切回原片设置源入出点。")); return;
    }
    const media = mediaRef.current;
    if (frozen || !source || sourceIsImage || preview?.kind !== "source" || previewSource?.id !== source.id || !media?.isConnected || media.readyState < 1
      || !Number.isFinite(media.duration) || !Number.isFinite(media.currentTime)) {
      reportError(new Error("请先载入当前选中源素材的真实播放元数据；不能用时间线或导出预览时间作源入出点。")); return;
    }
    // Read currentTime at the click, not a throttled timeupdate snapshot. No invented frame times.
    setSourceRange(edge === "in" ? media.currentTime : inPoint, edge === "out" ? media.currentTime : outPoint);
  };
  const clearSourceRange = () => {
    if (frozen || !source || !project || !catalog) return;
    try {
      updateWorkspace({ source_marks: updateSourceMarks(project.workspace.source_marks, source.id, null, catalog.sources.map(s => s.id)) });
      initialRange.current = sourceIsImage ? null : source.id;
      setInPoint(0); setOutPoint(sourceIsImage ? 0 : Math.min(3, durations[source.id] ?? 3)); setSourceRangeError("");
      setNotice("已清除该素材的持久化入出点（待保存）；表单恢复初始候选范围，未自动创建片段。 ");
    } catch (e) { reportError(e); }
  };
  const updateAsset = (patch: Partial<AssetMetadata>) => {
    if (!source) return;
    if (!asset && (project?.assets.length ?? 0) >= 200) { reportError(new Error("最多 200 条素材元数据。")); return; }
    edit(p => {
      let entry = p.assets.find(a => a.source_id === source.id);
      if (!entry) { entry = { source_id: source.id, rating: 0, tags: [] }; p.assets.push(entry); }
      Object.assign(entry, patch);
    });
  };
  const togglePlayback = () => {
    const media = mediaRef.current;
    if (!media || !media.isConnected || !preview || (previewSource && isImageSource(previewSource))
      || (preview.kind === "job" && ["png", "gif", "srt", "ass"].includes(previewJob?.options.format ?? ""))) return;
    if (media.paused) void media.play().catch(reportError); else media.pause();
  };
  const seekTimeline = (time: number) => {
    if (!Number.isFinite(time)) return;
    const next = quantizeTime(Math.max(0, Math.min(120, time)), timelineFPS); setPlayhead(next);
    // Only a completed timeline render uses timeline time. Source playback is separate.
    if (previewJob?.kind === "render" && mediaRef.current && Number.isFinite(mediaRef.current.duration)) {
      mediaRef.current.currentTime = Math.min(next, mediaRef.current.duration);
    }
  };
  function addClip() {
    if (frozen || !project || !selectedTrack || selectedTrack.locked) return;
    if (!insertProposal.value) { reportError(new Error(insertProposal.error)); return; }
    try { if (!insertingSpan && !sourceIsImage) validateSourceRange(inPoint, outPoint, source ? durations[source.id] : undefined); }
    catch (e) { reportError(e); return; }
    const at = insertProposal.value.seconds;
    let clip: StudioClip;
    if (selectedTrack.type === "text") clip = makeClip({ start: at, duration: spanDuration, text: "请输入文字" });
    else if (selectedTrack.type === "adjustment") clip = makeClip({ start: at, duration: spanDuration });
    else {
      if (!source) { reportError(new Error("请先选择真实素材。")); return; }
      if (audioSource(source) && selectedTrack.type !== "audio") { reportError(new Error("纯音频素材只能加入音频轨。")); return; }
      if (sourceIsImage && !["video", "overlay"].includes(selectedTrack.type)) { reportError(new Error("静态图片只能加入视频 / 叠加轨，请明确选择目标轨道。")); return; }
      try { clip = makeSourceClip(source, { start: at, trim: sourceIsImage ? 0 : inPoint, duration: sourceIsImage ? imageDuration : outPoint - inPoint }); }
      catch (e) { reportError(e); return; }
    }
    if (clip.duration <= 0 || clip.start < 0 || clip.start + clip.duration > 120 || (!insertingSpan && inPoint < 0)) { reportError(new Error("输出时长须为正数；媒体出点必须大于入点，片段必须位于 0–120 秒。")); return; }
    if (!sourceIsImage && clip.source_id && durations[clip.source_id] !== undefined && outPoint > durations[clip.source_id] + 0.001) { reportError(new Error("出点超出素材时长。")); return; }
    if ([...project.tracks, ...(project.sequences ?? []).flatMap(sequence => sequence.tracks)].flatMap(t => t.clips).length >= 64) { reportError(new Error("主时间线与所有子序列合计最多 64 个片段。")); return; }
    if (edit(p => { p.tracks.find(t => t.id === selectedTrack.id)?.clips.push(clip); }) === false) return;
    pickClip(selectedTrack, clip);
    setNotice(`已在 ${displayTime(at)} 加入草稿${insertProposal.value.target ? `；吸附目标：${insertProposal.value.target.label}` : ""}。保存后进入历史；源播放器不显示时间线效果。`);
  }
  const splitClip = () => {
    if (frozen || !supports("edit") || !canSplit || !selection || !splitProposal.value) return;
    action({ op: "split", clip_id: selection.clip.id, at: splitProposal.value.seconds, new_id: uid("clip") });
  };
  const deleteClip = () => {
    if (frozen || !canDelete || !selection) return;
    if (selectionIsSequence) { action({ op: "delete", clip_id: selection.clip.id }); return; }
    if (rippleEnabled && !window.confirm(`波纹删除「${selection.track.name}」的选中片段？只将本轨从旧终点起的后续片段前移 ${selection.clip.duration}s，其他轨道 / 标记不动；受影响的重叠会被拒绝。`)) return;
    action(buildDeleteAction(selection.clip, rippleEnabled));
  };
  const moveOrDuplicate = (op: "move" | "duplicate") => {
    if (frozen || !supports("edit") || !selection || !canMove || !moveProposal.value) return;
    if (op === "duplicate" && (selectionIsSequence || clipCount >= 64)) return;
    const payload = { clip_id: selection.clip.id, at: moveProposal.value.seconds, ...(moveTrack ? { track_id: moveTrack } : {}) };
    action(op === "duplicate" ? { op, ...payload, new_id: uid("clip") } : { op, ...payload });
  };
  const applyTrim = () => {
    if (frozen || !selection || selectionIsSequence || selection.track.locked || !supports("trimMode") || (editMode === "ripple" && !supports("ripple"))) return;
    try {
      const payload = trimProposal.payload;
      if (!payload) throw new Error(trimProposal.error);
      if (!selectionIsImage && ["ordinary", "ripple", "slip"].includes(editMode) && selection.clip.source_id) {
        const output = "duration" in payload ? payload.duration : selection.clip.duration;
        const known = durations[selection.clip.source_id];
        if (selection.clip.freeze) {
          if (known !== undefined && trimInput >= known) throw new Error("定格取帧入点须小于源时长。");
        } else validateSourceRange(trimInput, trimInput + output * selection.clip.speed, known);
      }
      const constraints = editMode === "ordinary"
        ? "普通修剪不自动解决同轨重叠；服务器仍验证锁、时长和包络，素材 EOF 在渲染时确认。"
        : "服务器拒绝受影响的重叠、锁定、越界或放不下的淡化 / 关键帧；滚动 / 滑动还要求相邻无缝，不自动缩短包络。";
      if (!window.confirm(`${EDIT_MODE_LABELS[editMode]} · 仅轨道「${selection.track.name}」\n${selectionIsImage ? "图片仅编辑时间线显示范围，trim 始终为 0；滚动 / 滑动还会修剪相邻片段，非图片邻片仍按各自源范围验证。" : EDIT_EFFECTS[editMode]}\n${constraints}\n继续？`)) return;
      action(payload);
    } catch (e) { reportError(e); }
  };
  const copyClip = (cut: boolean) => {
    if (frozen || !project || !selection || selection.track.locked || !supports("edit")) return;
    // Normalize nested key ordering as well as values, so a save receipt does
    // not falsely invalidate a cut merely by reordering mask/curve JSON keys.
    try { setClipClipboard(captureSequenceClipboard(project, taskId, selection.clip.id, cut)); }
    catch (e) { reportError(e); return; }
    setNotice(cut ? "已暂存剪切；仅在同一序列粘贴时提交原子 move，不先删除、不波纹，不支持跨序列剪切。原片段变更 / 锁定后须重新剪切。" : "已复制完整片段快照到本编辑器内存；允许本任务跨序列复制到同类型未锁定轨道，嵌套引用保留。未读取系统剪贴板。");
  };
  const pasteClip = () => {
    if (frozen || !clipClipboard || !selectedTrack || selectedTrack.locked || !supports("edit")) return;
    void operation("粘贴片段", async () => {
      if (!project || !catalog || selectedTrack.type !== clipClipboard.type) throw new Error("粘贴目标须为同类型未锁定轨道。");
      if (clipClipboard.cut && clipClipboard.sequenceId !== project.active_sequence_id) throw new Error("不支持跨序列剪切；请返回来源序列粘贴或改用复制，原片段未删除。");
      const proposal = proposeTimelineTime(insertAt, timelineFPS, snapActive,
        collectSnapTargets(activeProject(project), clipClipboard.cut ? clipClipboard.clip.id : undefined), { max: 120 - clipClipboard.clip.duration });
      const pastedId = clipClipboard.cut ? clipClipboard.clip.id : uid("clip");
      const pasteOptions = { taskId, trackId: selectedTrack.id, start: proposal.seconds, newClipId: pastedId,
        sourceIds: catalog.sources.map(source => source.id), lutIds: assetState.status === "ready" ? assetState.catalog?.luts.map(lut => lut.id) : undefined };
      pasteSequenceClipboard(project, clipClipboard, pasteOptions);
      if (!window.confirm(`将${clipClipboard.cut ? "剪切" : "复制"}片段粘贴到「${selectedTrack.name}」${displayTime(proposal.seconds)}？${proposal.target ? `\n吸附：${proposal.target.label}（原 ${proposal.target.time}s），提交 ${proposal.seconds}s。` : "\n未吸附，仅按工作区帧网格定位。"}\n不影响其他片段，不自动波纹。`)) return;
      const current = await saveDraft();
      if (!mounted.current) return;
      if (current.project.active_sequence_id !== project.active_sequence_id) throw new Error("保存回执改变了活动序列；请重新选择粘贴目标，未移动片段。");
      const nextProject = pasteSequenceClipboard(current.project, clipClipboard, pasteOptions);
      let next: ProjectEnvelope;
      if (clipClipboard.cut) {
        next = await api.action(current.revision, { op: "move", clip_id: pastedId, at: proposal.seconds, track_id: selectedTrack.id });
      } else {
        const problems = projectWarnings(nextProject, durations, catalog.sources, assetState.status === "ready" ? assetState.catalog?.luts : undefined);
        if (problems.length) throw new Error(problems.join("\n"));
        next = await api.save(current.revision, nextProject);
      }
      remember(next);
      if (!mounted.current) return;
      if (clipClipboard.cut) setClipClipboard(null);
      const target = projectScope(next.project).tracks.find(t => t.id === selectedTrack.id), pasted = target?.clips.find(c => c.id === pastedId);
      if (target && pasted) pickClip(target, pasted);
      setNotice(`已粘贴并保存 r${next.revision}；可用服务器撤销恢复。`);
      try { await refreshAudit(); } catch { /* Commit receipt remains authoritative. */ }
    });
  };
  function reorderTrack(direction: -1 | 1) {
    if (!workingProject || !selectedTrack) return;
    const index = workingProject.tracks.findIndex(t => t.id === trackId), other = workingProject.tracks[index + direction];
    if (!other || selectedTrack.locked || other.locked) return;
    edit(p => { [p.tracks[index], p.tracks[index + direction]] = [p.tracks[index + direction], p.tracks[index]]; });
  }
  const startJob = (kind: "render" | "export", previewOnly = false) => void operation(previewOnly ? "提交时间线预览" : "提交导出", async () => {
    if (!caps?.render_available) throw new Error("服务器 FFmpeg / ffprobe 不可用。");
    if (running) throw new Error("每个任务同时只允许一个 Studio 作业。");
    const current = await saveDraft();
    const renderOptions = previewOnly ? optionsForFormat(options, "mp4") : options;
    if (renderOptions.format === "png" && renderOptions.frame_time !== null) {
      const end = kind === "render" ? duration : durations.final;
      if (!Number.isFinite(renderOptions.frame_time) || renderOptions.frame_time < 0 || renderOptions.frame_time > 120
        || (end !== undefined && renderOptions.frame_time >= end)) throw new Error("PNG 取帧时间必须在输出时长内（不含终点）。");
    }
    if (kind === "render" && hasBurnedFinal && renderOptions.subtitles !== "standard") throw new Error("时间线引用已烧字幕的 final：只能选标准字幕，或换成干净画面与配音。");
    if (!mounted.current) return;
    // Submit the actual save receipt revision. Task state, QC, resource limits
    // and media access remain server checks; never retry an uncertain POST.
    const job = await api.submit(kind, current.revision, renderOptions);
    if (!mounted.current) return;
    upsertJob(job); setNotice(`${kind === "render" ? "时间线" : "原成片独立"}作业已提交。服务器只返回状态，不提供百分比。完成后可选择预览或下载。`);
    try { await refreshAudit(); } catch { /* Poll the returned ID even when audit is unavailable. */ }
  });
  const showHistory = () => { setHistoryOpen(true); window.setTimeout(() => root.current?.querySelector<HTMLElement>("[data-studio-section='history']")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0); };
  const focusSection = (section: string) => {
    const element = root.current?.querySelector<HTMLElement>(`[data-studio-section='${section}']`)
      ?? root.current?.querySelector<HTMLElement>("[data-studio-section='inspector']");
    element?.scrollIntoView({ behavior: "smooth", block: "center" }); element?.focus({ preventScroll: true });
  };
  const toolTarget: Record<string, string> = {
    addTrack: "tracks", trkColor: "tracks", marker: "markers", edit: "actions", undo: "actions", trimMode: "trim-modes",
    proxy: "proxy", tlPick: "sequences", nest: "sequences", camNest: "sequences",
    tc: "timecontrols", inout: "source", snap: "timeline", ripple: "timeline",
    reverse: "timing", mirror: "transform", rotate: "transform", frameCrop: "crop", speed: "timing", adjLayer: "tracks",
    mixVol: "audio", mixPan: "audio", mixMute: "audio", fxAudio: "audio", wave: "audio",
    trLib: "transitions", trDur: "transitions", trCustom: "transitions", camN: "multicam", camAlign: "multicam", camBatch: "multicam",
    basicColor: "color", colorVal: "color", proColor: "color", colorCopy: "color", textAlpha: "text", textAnim: "text",
    stickerCustom: "assets", stickerLib: "assets", stickerAnim: "assets", lut: "assets",
    fancy: "text", subTpl: "text", perChar: "text",
    subBatch: "text", subOps: "actions", subShift: "timing", subIO: "subtitles", pip: "transform", pipLayer: "tracks",
    blend: "transform", chroma: "chroma", expRes: "export", expFps: "export", expBr: "export", expCs: "export",
    expFmt2: "export", expBatch: "jobs", expOpt: "export", libAuto: "library",
    importPro: "backup", splitMerge: "actions", draftOps: "history", quick: "timing",
    freeze: "timing", kf: "keyframes", kfCurve: "keyframes", kfParam: "keyframes",
    maskType: "mask", maskFeather: "mask", maskInv: "mask", textFx: "text",
    trkGroup: "groups", libRate: "asset-metadata", hotkey: "workspace", layout: "workspace",
  };
  const openTool = (tool: StudioTool) => {
    const target = toolTarget[tool.id];
    if (!target || !tool.available) return;
    if (target === "history") { showHistory(); return; }
    focusSection(["timing", "transform", "crop", "audio", "color", "text", "fades", "chroma", "keyframes", "mask"].includes(target) && !selection ? "inspector" : target);
    if (["timing", "transform", "crop", "audio", "color", "text", "fades", "chroma", "keyframes", "mask"].includes(target)) {
      setNotice("在片段检查器编辑真实参数；先选中适用类型的片段。未适用的参数不会显示为可操作开关。");
    }
  };
  const copyColor = () => {
    if (frozen || !selection || selectionIsSequence || selection.track.locked || !supports("colorCopy") || !["video", "overlay", "adjustment"].includes(selection.track.type)) return;
    try { setColorClipboard(copyColorParameters(normalizeClip(selection.clip))); setNotice("已复制完整调色参数（含预设 / RGB 曲线 / LUT ID）；仅限本任务，LUT 须由当前素材目录确认后才能粘贴。 "); }
    catch (e) { reportError(e); }
  };
  const pasteColor = () => {
    if (frozen || selectionIsSequence || !supports("colorCopy")) return;
    if (colorClipboard?.lut_id && (!supports("lut") || assetState.status !== "ready"
      || !assetState.catalog?.luts.some(row => row.id === colorClipboard.lut_id))) {
      reportError(new Error("剪贴板 LUT 尚未在此任务真实素材目录中确认，未粘贴任何调色参数。请刷新目录或重新复制。")); return;
    }
    if (colorClipboard && selection && !selection.track.locked && ["video", "overlay", "adjustment"].includes(selection.track.type)) {
      updateClip(applicableColorParameters(colorClipboard, selection.track.type));
    }
  };
  const addEmptyTrack = (type: TrackType, name: string) => {
    if (frozen || activeSequenceLock || !workingProject || !supports("addTrack") || workingProject.tracks.length >= (caps?.limits.tracks ?? 8)) return;
    const track = makeTrack(type, name || `${TYPES.find(([key]) => key === type)?.[1]} ${workingProject.tracks.length + 1}`);
    if (edit(p => { p.tracks.push(track); })) setTrackId(track.id);
  };
  /** Compact prototype palette. These are actual controls, not success-state
   * demonstrations; detailed numeric forms remain available at stable anchors. */
  const toolControls = (tool: StudioTool): ReactNode => {
    if (!tool.available || !project || !workingProject || !saved) return null;
    const clip = selection?.clip, track = selection?.track;
    const editable = !!clip && !track?.locked && !activeSequenceLock && !selectionIsSequence;
    const visual = editable && (track?.type === "video" || track?.type === "overlay");
    const media = (visual || (editable && track?.type === "audio")) && !selectionIsImage;
    const color = visual || (editable && track?.type === "adjustment");
    const text = editable && track?.type === "text";
    switch (tool.id) {
      case "addTrack": return <div className="gm-studio-row">{TYPES.map(([type, label]) => <button key={type} type="button" disabled={!!activeSequenceLock || workingProject.tracks.length >= caps!.limits.tracks} onClick={() => addEmptyTrack(type, "")}>＋ {label}</button>)}</div>;
      case "adjLayer": return <button type="button" disabled={!!activeSequenceLock || workingProject.tracks.length >= caps!.limits.tracks} onClick={() => addEmptyTrack("adjustment", "调整图层")}>添加调整轨（不自动创建区间）</button>;
      case "tlPick": case "nest": case "camNest": return <><button type="button" onClick={() => focusSection("sequences")}>打开多序列 / 完整嵌套面板</button><p className="gm-studio-note">主时间线 + 最多 4 个子序列；深度最多主 → A → B。不是无限轨道或自动多机位配方；已有机位须先成为普通子序列内容。</p></>;
      case "tc": return <div className="gm-studio-fields">
        <Field label="工具 · 时间线帧率"><select aria-label="工具 · 时间线帧率" value={timelineFPS} onChange={e => { updateWorkspace({ timeline_fps: Number(e.target.value) as TimelineFPS }); setTimecodeInput(""); }}>{TIMELINE_FPS.map(fps => <option key={fps} value={fps}>{fps} fps</option>)}</select></Field>
        <Field label="工具 · 时间显示"><select aria-label="工具 · 时间显示" value={timeDisplay} onChange={e => updateWorkspace({ time_display: e.target.value as WorkspaceMetadata["time_display"] })}><option value="seconds">秒</option><option value="frames">帧 / 时间码</option></select></Field>
      </div>;
      case "snap": return <Check label="工具 · 磁性吸附（6 帧内）" checked={snapEnabled} onChange={snap_enabled => updateWorkspace({ snap_enabled })} />;
      case "ripple": return <Check label="工具 · 波纹编辑（仅选中轨）" checked={rippleEnabled} onChange={ripple_enabled => { setAdvancedMode(null); updateWorkspace({ ripple_enabled }); }} />;
      case "inout": return <><div className="gm-studio-row">
        <button type="button" disabled={sourceIsImage || preview?.kind !== "source" || previewSource?.id !== sourceId || durations[sourceId] === undefined} onClick={() => markCurrentSource("in")}>源当前帧 → 入点</button>
        <button type="button" disabled={sourceIsImage || preview?.kind !== "source" || previewSource?.id !== sourceId || durations[sourceId] === undefined} onClick={() => markCurrentSource("out")}>源当前帧 → 出点</button>
        <button type="button" disabled={!source || (!sourceMark && !sourceRangeError)} onClick={clearSourceRange}>清除已记入出点</button>
      </div><p className="gm-studio-note">{sourceIsImage ? "静态图片没有源当前帧 / 入出点；请明确填写显示时长。" : <>{source ? `${source.name} · ${source.id} · 源范围 ${inPoint}s → ${outPoint}s` : "请先选择真实源素材。"}
        {preview?.kind === "proxy" ? "；正在播放源代理，请切回原片精确记点。" : sourceRangeError || "；以播放器当前源秒数记点，待保存，不按时间线 / 导出 FPS 量化。"}</>}</p></>;
      case "proxy": return <button type="button" onClick={() => focusSection("proxy")}>打开源代理卡片（不自动创建）</button>;
      case "trimMode": return <Field label="工具 · 修剪模式"><select aria-label="工具 · 修剪模式" value={editMode} onChange={e => chooseEditMode(e.target.value as EditMode)}>
        {(Object.keys(EDIT_MODE_LABELS) as EditMode[]).map(mode => <option key={mode} value={mode} disabled={(mode === "ripple" && !supports("ripple")) || (mode === "slip" && selectionIsImage)}>{EDIT_MODE_LABELS[mode]}</option>)}
      </select></Field>;
      case "edit": case "splitMerge": case "subOps": return <><div className="gm-studio-row">
        <button type="button" disabled={!canSplit} onClick={splitClip}>分割选中片段</button>
        <button type="button" disabled={!canDelete} onClick={deleteClip}>{rippleEnabled && !selectionIsSequence ? "波纹删除所选" : "删除所选（留空隙）"}</button>
        <button type="button" disabled={!editable} onClick={() => copyClip(false)}>复制所选参数与范围</button>
      </div><SnapReadout label="工具分割提案" result={splitProposal} fps={timelineFPS} enabled={snapActive} kind={`palette-${tool.id}`} /></>;
      case "undo": return <div className="gm-studio-row">
        <button type="button" disabled={!dirty && !saved.can_undo} onClick={() => action({ op: "undo" })}>撤销一步{dirty ? "（先存草稿）" : "（服务器）"}</button>
        <button type="button" disabled={dirty || !saved.can_redo} onClick={() => action({ op: "redo" })}>重做一步（服务器）</button>
      </div>;
      case "marker": return <button type="button" disabled={workingProject.markers.length >= 100} onClick={() => action({ op: "marker", new_id: uid("marker"), at: playhead, label: markerLabel })}>添加播放头标记</button>;
      case "trkColor": return <Field label="工具 · 轨道颜色"><select aria-label="工具 · 轨道颜色" disabled={!selectedTrack || selectedTrack.locked} value={selectedTrack?.color ?? "gray"}
        onChange={e => { if (selectedTrack) action({ op: "track", track_id: selectedTrack.id, color: e.target.value as TrackColor }); }}>{Object.keys(COLORS).map(key => <option key={key} value={key}>{key}</option>)}</select></Field>;
      case "trkGroup": return <Field label="工具 · 轨道组"><select aria-label="工具 · 轨道组" disabled={!selectedTrack || selectedTrack.locked} value={selectedTrack?.group_id ?? ""} onChange={e => {
        const groupId = e.target.value || null; if (selectedTrack && !selectedTrack.locked) edit(p => { const t = p.tracks.find(t => t.id === selectedTrack.id); if (t) t.group_id = groupId; });
      }}><option value="">未分组</option>{project.groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></Field>;
      case "speed": return <NumberField label="工具 · 恒定速度" value={clip?.speed ?? 1} min={0.5} max={2} step={0.05} disabled={!media || clip?.freeze} onChange={speed => updateClip({ speed })} />;
      case "reverse": return <Check label="工具 · 倒放" checked={clip?.reverse ?? false} disabled={!media || clip?.freeze} onChange={reverse => updateClip({ reverse })} />;
      case "mirror": return <Check label="工具 · 水平镜像" checked={clip?.mirror ?? false} disabled={!visual} onChange={mirror => updateClip({ mirror })} />;
      case "freeze": return <Check label="工具 · 显式定格" checked={clip?.freeze ?? false} disabled={!visual || selectionIsImage} onChange={freeze => updateClip(freeze ? { freeze, mute: true, speed: 1, reverse: false } : { freeze })} />;
      case "rotate": return <Field label="工具 · 旋转"><select aria-label="工具 · 旋转" value={clip?.rotation ?? 0} disabled={!visual} onChange={e => updateClip({ rotation: Number(e.target.value) as StudioClip["rotation"] })}>{[0, 90, 180, 270].map(n => <option key={n} value={n}>{n}°</option>)}</select></Field>;
      case "mixVol": return <NumberField label="工具 · 音量" value={clip?.volume ?? 1} min={0} max={2} disabled={!media} onChange={volume => updateClip({ volume })} />;
      case "mixPan": return <NumberField label="工具 · 立体声平衡" value={clip?.pan ?? 0} min={-1} max={1} disabled={!media} onChange={pan => updateClip({ pan })} />;
      case "mixMute": return <Check label="工具 · 片段静音" checked={clip?.mute ?? false} disabled={!media || clip?.freeze} onChange={mute => updateClip({ mute })} />;
      case "basicColor": return <NumberField label="工具 · 亮度" value={clip?.brightness ?? 0} min={-1} max={1} disabled={!color} onChange={brightness => updateClip({ brightness })} />;
      case "colorVal": return <Field label="工具 · 调色预设"><select aria-label="工具 · 调色预设" value={clip?.color_preset ?? "none"} disabled={!color} onChange={e => updateClip({ color_preset: e.target.value as StudioClip["color_preset"] })}>
        <option value="none">无预设</option><option value="warm">暖色</option><option value="cool">冷色</option><option value="cinema">电影色调</option><option value="mono">黑白</option>
      </select></Field>;
      case "proColor": return <button type="button" disabled={!color || !clip || !Object.keys(clip.rgb_curves).length} onClick={() => updateClip({ rgb_curves: {} })}>清除 RGB 曲线（其他调色保留）</button>;
      case "colorCopy": return <div className="gm-studio-row"><button type="button" disabled={!color} onClick={copyColor}>复制完整调色</button><button type="button" disabled={!color || !colorClipboard} onClick={pasteColor}>粘贴完整调色</button></div>;
      case "stickerCustom": case "stickerLib": case "stickerAnim": case "lut": return <button type="button" onClick={() => focusSection("assets")}>打开本地图片 / LUT 面板（不提交处理）</button>;
      case "textAnim": return <Field label="工具 · 文字动画"><select aria-label="工具 · 文字动画" value={clip?.text_animation ?? "none"} disabled={!text} onChange={e => updateClip({ text_animation: e.target.value as StudioClip["text_animation"] })}>
        <option value="none">无动画</option><option value="typewriter">分阶段打字机</option><option value="fade">淡入淡出</option><option value="pop">弹入</option>
      </select></Field>;
      case "perChar": return <Check label="工具 · 有界打字机揭示（非语音时间戳）" checked={clip?.text_animation === "typewriter"} disabled={!text} onChange={on => updateClip({ text_animation: on ? "typewriter" : "none" })} />;
      case "fancy": case "subTpl": return <Field label={`工具 · ${tool.label}`}><select aria-label={`工具 · ${tool.label}`} disabled={!text} value={clip?.text_template ?? "custom"} onChange={e => updateClip({ text_template: e.target.value as StudioClip["text_template"] })}>
        <option value="custom">自定义原样式</option><option value="news">新闻</option><option value="outline">描边</option><option value="gold">金色</option><option value="note">便笺</option>
      </select></Field>;
      case "textAlpha": return <NumberField label="工具 · 文字透明度" value={clip?.opacity ?? 1} min={0} max={1} disabled={!text} onChange={opacity => updateClip({ opacity })} />;
      case "layout": return <Field label="工具 · 布局"><select aria-label="工具 · 布局" value={layout} onChange={e => updateWorkspace({ layout: e.target.value as WorkspaceMetadata["layout"] })}>
        <option value="default">默认</option><option value="editing">剪辑</option><option value="audio">音频</option><option value="captions">字幕</option>
      </select></Field>;
      case "expFps": return <Field label="工具 · 导出帧率"><select aria-label="工具 · 导出帧率" value={options.fps} disabled={!formatSupport(options.format).fps} onChange={e => setOptions(o => ({ ...o, fps: Number(e.target.value) as ExportOptions["fps"] }))}>{TIMELINE_FPS.map(fps => <option key={fps} value={fps}>{fps} fps</option>)}</select></Field>;
      case "expRes": return <Field label="工具 · 导出分辨率"><select aria-label="工具 · 导出分辨率" value={options.resolution} disabled={!formatSupport(options.format).geometry} onChange={e => setOptions(o => ({ ...o, resolution: Number(e.target.value) as ExportOptions["resolution"] }))}>{[360, 720, 1080].map(n => <option key={n} value={n}>{n}p</option>)}</select></Field>;
      case "expFmt2": return <Field label="工具 · 输出格式"><select aria-label="工具 · 输出格式" value={options.format} onChange={e => setOptions(o => optionsForFormat(o, e.target.value as ExportFormat))}>{FORMATS.map(format => <option key={format} value={format}>{format.toUpperCase()}</option>)}</select></Field>;
      default: return null;
    }
  };

  // Scoped to this editor; typing, native controls, IME and held keys never edit the timeline.
  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat || event.isComposing || event.metaKey || frozen || !project || !saved) return;
      const target = event.target;
      if (!(target instanceof HTMLElement) || !root.current?.contains(target)
        || target.closest("input,textarea,select,button,a,video,audio,summary,[contenteditable]:not([contenteditable='false']),[role='textbox']")) return;
      const key = event.code === "Space" ? "Space" : event.key === "ArrowLeft" ? "Left" : event.key === "ArrowRight" ? "Right"
        : event.key === "Delete" ? "Delete" : /^[a-z0-9]$/i.test(event.key) ? event.key.toUpperCase() : "";
      const chord = [...(event.ctrlKey ? ["Ctrl"] : []), ...(event.altKey ? ["Alt"] : []), ...(event.shiftKey ? ["Shift"] : []), key].join("+");
      const command = (Object.keys(shortcuts) as ShortcutAction[]).find(a => shortcuts[a] === chord);
      if (!command) {
        if (!event.ctrlKey && !event.altKey && !event.shiftKey && (key === "Left" || key === "Right")) {
          event.preventDefault(); seekTimeline(playhead + (key === "Right" ? 1 : -1) / timelineFPS);
        }
        return;
      }
      event.preventDefault();
      if (command === "play_pause") togglePlayback();
      else if (command === "undo" && supports("undo") && (dirty || saved.can_undo)) action({ op: "undo" });
      else if (command === "redo" && supports("undo") && !dirty && saved.can_redo) action({ op: "redo" });
      else if (command === "marker" && supports("marker") && workingProject && workingProject.markers.length < 100) action({ op: "marker", new_id: uid("marker"), at: playhead, label: markerLabel });
      else if (command === "split") splitClip();
      else if (command === "delete") deleteClip();
    };
    const editor = root.current;
    editor?.addEventListener("keydown", handle);
    return () => editor?.removeEventListener("keydown", handle);
  });

  const previewUrl = previewProxy ? api.proxyUrl(previewProxy.id)
    : previewSource ? sourceUrl(previewSource.id) : previewJob?.output_id ? outputUrl(previewJob.output_id) : "";
  useEffect(() => {
    if (preview?.kind !== "proxy" && preview?.kind !== "job") return;
    const media = mediaRef.current;
    if (!media || !previewUrl) return;
    // Mirror cleanup if React replays this effect without replacing the element.
    if (media.getAttribute("src") !== previewUrl) { media.setAttribute("src", previewUrl); media.load(); }
    return () => {
      // A removed / invalidated proxy or a switched output stops buffered media.
      // Proxy validity still requires the current source catalog to match.
      media.pause(); media.removeAttribute("src"); media.load();
    };
  }, [preview?.kind, previewUrl]);
  const previewFormat = previewJob?.options.format;
  const previewAudio = previewSource ? audioSource(previewSource) : previewFormat === "mp3" || previewFormat === "wav";
  const previewImage = !!previewSource && isImageSource(previewSource) || previewFormat === "png" || previewFormat === "gif";
  const previewText = previewFormat === "srt" || previewFormat === "ass";
  const readMediaTime = (media = mediaRef.current) => {
    if (!media?.isConnected || media !== mediaRef.current || (previewSource && isImageSource(previewSource))) return;
    const currentTime = media.currentTime;
    if (!Number.isFinite(currentTime) || currentTime < 0) return;
    if (previewSource || previewProxy) setSourceTime(currentTime);
    else if (previewJob?.kind === "render") setPlayhead(Math.min(120, currentTime));
  };
  const loadedMetadata = (media = mediaRef.current) => {
    if (!media?.isConnected || media !== mediaRef.current) return;
    const value = media.duration;
    if (preview?.kind === "source" && previewSource && !isImageSource(previewSource) && value !== undefined && Number.isFinite(value) && value > 0) {
      setDurations(old => ({ ...old, [previewSource.id]: value }));
      if (initialRange.current === previewSource.id && previewSource.id === sourceId) {
        // Only bound the untouched convenience range to actual metadata. Never
        // shorten an authored/persisted mark or fabricate a compensating out point.
        setOutPoint(Math.min(3, value)); initialRange.current = null;
      }
    }
    const seek = previewSeek.current;
    if (media?.isConnected && seek && seek.kind === preview?.kind && seek.id === preview.id && value !== undefined && Number.isFinite(value) && value > 0) {
      previewSeek.current = null;
      if (seek.time <= value) {
        try { media.currentTime = seek.time; setSourceTime(media.currentTime); }
        catch { setNotice("浏览器未能恢复源播放位置；请手动定位。工程入出点未改变。"); }
      } else setNotice("原播放位置超出此预览文件的实测时长，未强行裁剪或补帧；请在原片重新定位。工程入出点未改变。");
    }
  };
  const mediaFailure = (element?: HTMLMediaElement | HTMLImageElement) => {
    if (element && (!element.isConnected || ("readyState" in element && element !== mediaRef.current))) return;
    if (preview?.kind === "proxy") {
      const message = "源代理播放失败（源变化、成片修订、权限、格式或网络）；请刷新代理状态或明确切回原片。不自动重建，不使用导出地址。";
      setPreviewError(message); setProxyState({ status: "error", message });
    } else setPreviewError("浏览器无法播放此文件（格式、权限或网络）。可使用受保护下载；不会把素材当作时间线效果预览。");
  };
  const previewLabel = preview?.kind === "proxy" ? `低分辨率源代理 · ${catalog?.sources.find(s => s.id === selectedProxyRecord?.source_id)?.name ?? "状态待核对"}`
    : previewSource ? `素材预览 · ${previewSource.id === "final" ? "流水线成片（烧录字幕）" : previewSource.id === "video_only" ? "干净画面（无配音 / 字幕）" : previewSource.name}`
    : previewJob ? `${previewJob.kind === "render" ? "已完成时间线预览" : "原成片独立导出预览（不是当前时间线）"} · r${previewJob.revision}` : "选择素材或已完成的输出";

  return <div className="gm-studio" data-layout={layout} data-timeline-fps={timelineFPS} data-time-display={timeDisplay}
    data-snap-enabled={snapActive} data-ripple-enabled={rippleEnabled} data-active-sequence={project?.active_sequence_id ?? "main"} ref={root} tabIndex={-1} role="region" aria-label="专业剪辑台">
    <style>{STUDIO_CSS}</style>
    <header className="gm-studio-header">
      <button type="button" disabled={!!busy} onClick={() => { if (!dirty || window.confirm("尚有未保存的编辑，确定丢弃并返回？")) onBack(); }}>← 回结果页</button>
      <div className="gm-studio-title"><span className="gm-studio-eyebrow">GOLDEN MIC / LOCAL STUDIO</span><h1>专业剪辑台</h1></div>
      <span className={`gm-studio-badge ${dirty ? "gm-studio-gold" : ""}`}>{saved ? `r${saved.revision} · ${dirty ? "未保存" : "已保存"}` : "连接中"}</span>
      <button type="button" className="gm-studio-primary" disabled={frozen || !dirty || warnings.length > 0} onClick={save}>{busy || "保存工程"}</button>
      <button type="button" disabled={loading || !caps} onClick={() => focusSection("palette")}>专业工具面板 ↓</button>
      <button type="button" disabled={frozen || !project || !caps?.render_available || running || duration <= 0 || warnings.length > 0} onClick={() => startJob("render", true)}>渲染时间线预览 · MP4</button>
    </header>
    <p className="gm-studio-subtitle">奶油白工作台 · 本地真实编辑 · 不覆盖原成片 / 报告 / QC。衍生输出不代表已通过新的流水线质检；任务状态、修订、QC 与作业限制仍由服务器验证。</p>
    {error && <div role="alert" className="gm-studio-alert"><strong>操作未完成</strong><p>{error}</p><p>修订冲突时不会覆盖本地草稿。可先备份本地草稿，再重新加载服务器版本。</p>
      <button type="button" disabled={frozen} onClick={() => { if (!dirty || window.confirm("重新加载将丢弃本地编辑。已备份并继续？")) setReload(n => n + 1); }}>重新加载服务器版本</button>
      {project && <button type="button" onClick={() => downloadJson({ revision: saved?.revision, project }, "studio-unsaved-draft.json")}>备份本地草稿</button>}
    </div>}
    <div role="status" aria-live="polite" className="gm-studio-status">{loading ? "正在读取工程、素材和能力清单…" : busy || notice}</div>
    {!loading && project && workingProject && saved && catalog && caps && <>
      <section className="gm-studio-panel" data-studio-section="sequences" tabIndex={-1} aria-label="多序列与完整有界嵌套">
        <div className="gm-studio-section-title"><h2>多序列 / 完整嵌套（有界）</h2><span>当前：{projectScope(project).name} · 主时间线 + {project.sequences.length} / 4 子序列</span></div>
        {caps.limits.total_tracks !== undefined && <p className="gm-studio-note">服务器额外声明的全工程存储轨道额度：{allProjectTracks(project).length} / {caps.limits.total_tracks}（每个序列仍最多 8 轨）。超出时服务器拒绝，不自动删除。</p>}
        <p className="gm-studio-note">主轨与所有子序列一起保存 / 备份 / 撤销；编辑、标记、字幕导入和渲染只作用于已保存的活动序列。切换会先保存当前草稿，再以回执修订保存活动 ID，失败不会偷偷切回主轨。</p>
        <fieldset disabled={frozen || !supports("tlPick")}><div className="gm-studio-fields">
          <Field label="活动序列"><select aria-label="活动序列" value={project.active_sequence_id ?? ""} onChange={e => switchSequence(e.target.value || null)}>
            <option value="">主时间线 · {sequenceFullEnd(project)}s</option>{project.sequences.map(sequence => <option key={sequence.id} value={sequence.id}>{sequence.name || sequence.id} · {sequenceFullEnd(sequence)}s</option>)}
          </select></Field>
          <Field label="新序列名称"><input aria-label="新序列名称" maxLength={80} value={newSequenceName} onChange={e => setNewSequenceName(e.target.value)} /></Field>
        </div><button type="button" disabled={!newSequenceName.trim() || project.sequences.length >= 4} onClick={addSequence}>新建空序列并打开（服务器保存）</button></fieldset>
        {!supports("tlPick") && <p className="gm-studio-warning">服务器未声明 tlPick 多序列能力；仅显示已有完整工程，不提交新序列或切换。</p>}
        {activeSequenceLock && <p className="gm-studio-warning" role="status">{activeSequenceLock} 仍可切换 / 读取；源入出点与工作区设置独立保留。</p>}
        <fieldset disabled={frozen || !!activeSequenceLock || !supports("nest")}><legend>插入完整子序列 · 新建独立叠加轨</legend><div className="gm-studio-fields">
          <Field label="嵌套子序列"><select aria-label="嵌套子序列" value={nestedSequenceId} onChange={e => setNestedSequenceId(e.target.value)}>
            <option value="">请选择非空子序列</option>{project.sequences.map(sequence => <option key={sequence.id} value={sequence.id} disabled={sequence.id === project.active_sequence_id || sequenceFullEnd(sequence) <= 0}>{sequence.name || sequence.id} · {sequenceFullEnd(sequence)}s{sequence.id === project.active_sequence_id ? "（当前序列，不可自引）" : sequenceFullEnd(sequence) <= 0 ? "（空，不可嵌套）" : ""}</option>)}
          </select></Field>
          <TimelineField label="嵌套插入起点（秒）" value={nestedAt} fps={timelineFPS} display={timeDisplay} onChange={setNestedAt} />
        </div><SnapReadout label="完整嵌套位置提案" result={nestedPosition} fps={timelineFPS} enabled={snapActive} kind="nested-insert" />
          <div className="gm-studio-row"><button type="button" onClick={() => setNestedAt(playhead)}>嵌套起点使用播放头</button>
            <button type="button" disabled={!nestedChild || nestedChild.id === project.active_sequence_id || sequenceFullEnd(nestedChild) <= 0 || !nestedPosition.value || workingProject.tracks.length >= 8 || clipCount >= 64} onClick={insertSequence}>插入完整嵌套（有界）</button></div>
        </fieldset>
        <button type="button" disabled={frozen || !selectionIsSequence || !selection || !supports("tlPick")} onClick={() => { if (selection && isSequenceClip(selection.clip)) switchSequence(selection.clip.sequence_id); }}>打开所选嵌套</button>
        <p className="gm-studio-warning">每序列最多 8 轨 / 100 标记；主 + 全部子序列合计 ≤64 片段，全部 ID 全局唯一。深度最多主 → A → B；未激活 / 隐藏引用也禁止循环。父嵌套完整范围 0 → 子实际生效终点，所有其他参数固定默认（含 contain）；仅移动 / 普通删除，不支持父级范围、变速、缩放、效果或音频编辑。同轨嵌套不能重叠；子透明间隙透出父级下层，子调整轨影响累计下层画面，子文字始终在全部媒体之上，并非独立预合成画布。空子序列不可引用。</p>
        <p className="gm-studio-note" data-sequence-render-budget>{renderStats ? `当前展开：${renderStats.tracks} / 8 生效层 · ${renderStats.clips} / 64 展开片段 · ${renderStats.inputs} / 16 解码输入（含 ${renderStats.imageInputs} 张图片）· ${renderStats.lutBindings} 处 LUT 绑定。` : "当前草稿未通过完整图验证；不能推断渲染额度。"}重复引用逐次展开并计入额度；空可见轨也占展开层，可在轨道管理明确删除空轨；LUT 是受验证滤镜资产，不冒充解码输入。原素材播放器不显示嵌套效果，需保存后真实渲染。</p>
        <p className="gm-studio-note">子序列的服务器移动 / 删除 / 修剪 / 隐藏 / 独奏 / SRT 导入若改变被引用终点，将直接拒绝且不改工程。可先用检查器编辑子片段的起点 / 时长，再确认下方全部父绑定；不能形成有效提案时，先恢复草稿并在父级明确删除引用。不会自动缩放、波纹或重写父时长。</p>
        {!!durationBindings.proposal?.changes.length && <div data-duration-bindings>
          <p className="gm-studio-warning">子序列生效终点已改变，普通保存 / 切换被阻止。以下父时长仅为提案，绝未自动写入：</p>
          <ul>{durationBindings.proposal.changes.map(change => <li key={change.clipId}>{change.scopeId ?? "主时间线"} / {change.clipId}：{change.before}s → {change.after}s</li>)}</ul>
          <button type="button" disabled={frozen || !supports("nest")} onClick={confirmDurationBindings}>确认全部父时长绑定并保存（单次事务）</button>
        </div>}
        {durationBindings.error && <p className="gm-studio-warning">不能生成父时长更新提案：{durationBindings.error} 未丢弃草稿；可修正子终点，或恢复已保存草稿后先在父级移除引用。</p>}
        {dirty && <button type="button" disabled={frozen} onClick={() => { if (window.confirm("放弃全部未保存编辑并恢复最近服务器快照？不会修改服务器，也不会自动更新父绑定。")) remember(saved); }}>放弃草稿，恢复已保存完整工程</button>}
        <p className="gm-studio-note">tlPick / nest / camNest 仅代表上述有限序列功能；不是无限时间线、自动机位同步或任意嵌套效果。最多两个实际引用边（如主 → A → B）；任一未激活序列也独立校验。现有转场 / 手动多机位构建器仅在无嵌套引用的主时间线开放，子序列编辑时明确停用；已有子序列内部参数与转场仍完整保存。没有多窗口并行编辑或自动序列克隆。</p>
      </section>
      <div className="gm-studio-workbench">
        <section className="gm-studio-panel gm-studio-library" data-studio-section="library" tabIndex={-1}>
          <div className="gm-studio-section-title"><h2>源素材库</h2><span>{catalog.sources.length} 个真实文件</span></div>
          <Field label="搜索素材"><input type="search" aria-label="搜索素材" value={search} onChange={e => setSearch(e.target.value)} placeholder="文件名 / 素材 ID / 标签" /></Field>
          <div className="gm-studio-source-list">
            {catalog.sources.filter(s => `${s.name} ${s.id} ${project.assets.find(a => a.source_id === s.id)?.tags.join(" ") ?? ""}`.toLowerCase().includes(search.toLowerCase())).map(s => <button type="button" key={s.id}
              className={`gm-studio-source ${sourceId === s.id ? "is-selected" : ""}`} aria-pressed={sourceId === s.id} onClick={() => chooseSource(s.id)}>
              <span className="gm-studio-source-icon" aria-hidden="true">{isImageSource(s) ? "▧" : audioSource(s) ? "♫" : "▶"}</span><span><strong>{s.name}</strong><small>{isImageSource(s) ? "导入静态图片 · 无音轨 / 源时钟" : s.burned_subtitles ? "原成片 · 字幕已烧录" : s.id === "video_only" ? "干净画面 · 无配音" : s.id === "narration" ? "真实配音" : s.id.startsWith("norm_") ? "标准化素材" : "原始上传"} · {mb(s.bytes)}</small></span>
            </button>)}
            {!catalog.sources.length && <p>此任务没有可用素材，不能创建媒体片段。</p>}
          </div>
          <StudioProxy source={source} jobs={jobs} state={proxyState} preview={preview} available={supports("proxy")}
            capabilityReason={capabilities.get("proxy")?.reason} busy={frozen} dirty={dirty} savedRevision={saved.revision}
            originalDuration={source ? durations[source.id] : undefined} draftProblem={warnings[0]}
            onCreate={() => { void startProxy(); }} onRefresh={refreshProxies} onOriginal={chooseOriginalPreview} onPreview={chooseProxyPreview} />
          <fieldset disabled={frozen || !supports("libRate")} data-studio-section="asset-metadata" tabIndex={-1}>
            <legend>素材标签 / 评分（工程元数据）</legend>
            {source ? <>
              <Field label="素材评分"><select aria-label="素材评分" value={asset?.rating ?? 0} onChange={e => updateAsset({ rating: Number(e.target.value) })}>{[0, 1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n === 0 ? "未评分" : `${n} 星`}</option>)}</select></Field>
              <div className="gm-studio-row">{asset?.tags.map((tag, i) => <button type="button" key={`${tag}-${i}`} aria-label={`移除标签 ${tag}`} onClick={() => updateAsset({ tags: asset.tags.filter((_, index) => index !== i) })}>{tag} ×</button>)}</div>
              <Field label="新素材标签"><input aria-label="新素材标签" maxLength={40} value={tagDraft} onChange={e => setTagDraft(e.target.value)} /></Field>
              <button type="button" disabled={!tagDraft.trim() || (asset?.tags.length ?? 0) >= 16} onClick={() => { updateAsset({ tags: [...(asset?.tags ?? []), tagDraft.trim()] }); setTagDraft(""); }}>添加标签</button>
              <p className="gm-studio-note">最多 16 个标签，每个 1–40 字符；评分、标签随工程保存，不更改源文件。</p>
            </> : <p>请先选择素材。</p>}
          </fieldset>
          <details><summary>镜头元数据（{catalog.shots.length}）</summary><div className="gm-studio-scroll">
            {catalog.shots.map((shot, index) => <div className="gm-studio-shot" key={`${shot.shot_id ?? index}-${index}`}>
              <strong>镜头 {shot.shot_id ?? index + 1}</strong><p>{shot.description || "无描述"}</p><small>{shot.media_origin === "generated" ? "AI 生成素材 · 强制披露" : "来源元数据"} · {shot.start ?? "?"}–{shot.end ?? "?"}s</small>
              <button type="button" disabled={!shot.source_id || !catalog.sources.some(s => s.id === shot.source_id)} onClick={() => {
                if (!shot.source_id) return; chooseSource(shot.source_id);
                if (shot.start !== undefined && shot.end !== undefined) { initialRange.current = null; setInPoint(shot.start); setOutPoint(shot.end); }
              }}>选择对应素材与范围</button>
            </div>)}
          </div></details>
          <details><summary>原匹配报告 / QC（只读）</summary><div className="gm-studio-scroll">
            {catalog.report.rows.map((row, i) => <p key={i}><strong>句 {row.sentence_id ?? i + 1}</strong> {row.sentence} <small>{row.duration ?? "?"}s · {row.audio_kind} {row.is_fallback ? "· 备选画面" : ""}</small></p>)}
            <pre>{JSON.stringify(catalog.report.quality, null, 2)}</pre>
          </div></details>
        </section>

        <section className="gm-studio-panel gm-studio-preview" data-studio-section="preview" tabIndex={-1} data-preview-kind={preview?.kind === "proxy" ? "proxy" : previewSource ? "source" : previewJob ? previewJob.kind : "unavailable"}>
          <div className="gm-studio-section-title"><h2>监看</h2><span className="gm-studio-badge">{preview?.kind === "proxy" ? "SOURCE PROXY" : previewSource ? "SOURCE" : previewJob ? "RENDERED" : "EMPTY"}</span></div>
          <p className="gm-studio-preview-label">{previewLabel}</p>
          <div className="gm-studio-screen">
            {!previewUrl ? <p>{preview?.kind === "proxy" ? "源代理尚不可播放，请刷新真实状态或手动切回原片；不会自动换片或重建。" : preview?.kind === "job" ? "所选作业尚无可读取的已完成输出，请刷新真实状态。记录与工程均保留。" : "请选择源素材。时间线需完成渲染后才能预览。"}</p> : previewImage ? <img src={previewUrl} alt={`${previewLabel}${previewFormat === "png" ? ` · 取帧 ${previewJob?.result?.frame_time ?? previewJob?.options.frame_time ?? 0}s` : ""}`} onError={event => mediaFailure(event.currentTarget)} />
              : previewText ? <p>字幕是文本文件，请使用下载链接检查真实时间码。</p>
                : previewAudio ? <audio key={previewUrl} ref={element => { mediaRef.current = element; }} src={previewUrl} controls preload="metadata" crossOrigin="use-credentials"
                  aria-label={previewLabel} onLoadedMetadata={event => loadedMetadata(event.currentTarget)} onTimeUpdate={event => readMediaTime(event.currentTarget)} onError={event => mediaFailure(event.currentTarget)} />
                  : <video key={previewUrl} ref={element => { mediaRef.current = element; }} src={previewUrl} controls playsInline preload="metadata" crossOrigin="use-credentials"
                    aria-label={previewLabel} onLoadedMetadata={event => loadedMetadata(event.currentTarget)} onTimeUpdate={event => readMediaTime(event.currentTarget)} onError={event => mediaFailure(event.currentTarget)} />}
          </div>
          {previewError && <p role="alert" className="gm-studio-warning">{previewError}</p>}
          <p className="gm-studio-note">{preview?.kind === "proxy" ? `${PROXY_EXPLANATION}。代理仅用于当前源素材监看，不是时间线输出；请切回原片精确记点。`
            : previewSource ? isImageSource(previewSource) ? "这是服务器标准化后的原始静态图片，没有播放时钟；不是时间线效果预览。不显示当前变换、蒙版、关键帧、调色或 LUT 效果，请保存并真实渲染。" : "这是原始素材播放，不包含本次时间线裁剪、调色、叠加或静音效果。" : previewJob ? `此输出捕获 r${previewJob.revision}，${previewJob.kind === "render" && !dirty && previewJob.revision === saved.revision ? "对应当前已保存修订" : "不代表当前未渲染编辑"}。${previewJob.qc}` : "无实时合成或伪造预览。"}</p>
          {selectedProxyRecord && <p className="gm-studio-note" data-proxy-profile>{proxyProfileReadout(selectedProxyRecord)}</p>}
          {preview?.kind === "proxy" && <div className="gm-studio-row">
            <button type="button" disabled={frozen || !supports("proxy") || proxyState.status === "loading"} onClick={refreshProxies}>刷新当前源代理状态</button>
            <button type="button" disabled={frozen || !source} onClick={chooseOriginalPreview}>切回原片以精确记点</button>
          </div>}
          <div className="gm-studio-row">
            <Field label="选择预览"><select aria-label="选择预览" value={preview ? `${preview.kind}:${preview.id}` : ""} onChange={e => {
              const [kind, id] = e.target.value.split(":");
              if (kind === "source") { if (id === sourceId) chooseOriginalPreview(); else chooseSource(id); }
              else if (kind === "proxy") chooseProxyPreview(id);
              else if (kind === "job" && jobs.some(job => job.id === id && canReadOutput(job))) setPreview({ kind, id });
            }}><option value="" disabled>请选择</option>{catalog.sources.map(s => <option key={s.id} value={`source:${s.id}`}>素材 · {s.name}</option>)}
              {preview?.kind === "proxy" && !availableProxies.some(job => job.id === preview.id) && <option value={`proxy:${preview.id}`} disabled>所选源代理不可用 · 请刷新状态</option>}
              {availableProxies.map(job => <option key={job.id} value={`proxy:${job.id}`} disabled={proxyState.status !== "ready"}>源代理 · {job.source_id} · {job.options.resolution}p / {job.options.fps} FPS · {job.id.slice(0, 6)}</option>)}
              {jobs.filter(canReadOutput).map(j => <option key={j.id} value={`job:${j.id}`}>{j.kind === "render" ? "时间线" : "原成片独立导出"} r{j.revision} · {j.options.format} · {j.id.slice(0, 6)}</option>)}
            </select></Field>
            {previewUrl && preview?.kind !== "proxy" && <a href={previewUrl} download rel="noreferrer">下载当前{previewSource ? "源素材" : "已完成输出"}</a>}
          </div>
          <fieldset disabled={frozen} className="gm-studio-insert" data-studio-section="source" tabIndex={-1}>
            <legend>素材 → 时间线</legend>
            <p className="gm-studio-note">{source ? `选中：${source.name} · ${sourceIsImage ? `静态图片；最大显示时长 ${imageDisplayLimit(source)} 秒（不是源 EOF）` : durations[source.id] !== undefined ? `${durations[source.id].toFixed(3)} 秒（浏览器读取）` : "时长待播放元数据确认，服务端会再次验证"}` : "文字 / 调整轨无需源素材"}</p>
            <div className="gm-studio-fields">
              {sourceIsImage ? <div data-image-display-duration><TimelineField label="图片显示时长（秒）" min={0.001} max={source ? imageDisplayLimit(source) : 120}
                fps={timelineFPS} display={timeDisplay} value={imageDuration} onChange={setImageDuration} disabled={insertingSpan} /></div> : <>
                <NumberField label="源入点（秒）" min={0} max={3600} step={0.001} value={inPoint} onChange={n => setSourceRange(n, outPoint)} />
                <NumberField label="源出点（秒）" min={0} max={3600} step={0.001} value={outPoint} onChange={n => setSourceRange(inPoint, n)} />
              </>}
              <TimelineField label="插入时间（秒）" fps={timelineFPS} display={timeDisplay} value={insertAt} onChange={setInsertAt} />
            </div>
            <div className="gm-studio-row">
              <button type="button" data-source-mark="in" disabled={sourceIsImage || preview?.kind !== "source" || !supports("inout") || !previewSource || previewSource.id !== sourceId || durations[sourceId] === undefined} onClick={() => markCurrentSource("in")}>当前源帧设入点</button>
              <button type="button" data-source-mark="out" disabled={sourceIsImage || preview?.kind !== "source" || !supports("inout") || !previewSource || previewSource.id !== sourceId || durations[sourceId] === undefined} onClick={() => markCurrentSource("out")}>当前源帧设出点</button>
              <button type="button" data-source-mark="clear" disabled={!source || !supports("inout") || (!sourceMark && !sourceRangeError)} onClick={clearSourceRange}>清除源入出点</button>
              <button type="button" onClick={() => setInsertAt(playhead)}>插入到播放头</button>
            </div>
            <p className="gm-studio-note" data-source-marks-for={sourceId}>{sourceIsImage ? <>
              静态图片没有源播放位置 / 当前帧入出点。显示时长是时间线停留时长；trim 固定 0、速度固定 1×、静音、不倒放 / 定格。
              {sourceMark && "旧工程源标记已保留但不会用于图片插入，可明确清除；未偷偷改写工程。"}
            </> : <>{preview?.kind === "proxy" ? `代理播放位置 ${sourceTime.toFixed(6)}s（仅监看）；请切回原片精确记点。` : previewSource?.id === sourceId ? `源播放位置 ${sourceTime.toFixed(6)}s；` : ""}
              {sourceMark ? `工程标记：${sourceMark.in_point}s → ${sourceMark.out_point}s（随保存 / 撤销恢复）` : "此素材没有工程入出点标记；初始范围仅为候选，并非实测的镜头 / 语音时间。请根据已读取源时长修改，超出 EOF 不会自动补帧。"}
              源时间始终以实际秒数保存，不按时间线 / 导出帧率量化；最多 200 个真实 source_id。保存标记不裁剪素材，插入与渲染仍验证 EOF。
            </>}</p>
            {sourceRangeError && <p role="alert" className="gm-studio-warning">{sourceRangeError}</p>}
            <SnapReadout label="插入位置提案" result={insertProposal} fps={timelineFPS} enabled={snapActive} kind="insert" />
            <Field label="目标轨道"><select aria-label="目标轨道" value={trackId} onChange={e => setTrackId(e.target.value)}><option value="">选择轨道</option>{workingProject.tracks.map(t => <option key={t.id} value={t.id} disabled={t.locked || (sourceIsImage && t.type === "audio")}>{t.name} · {t.type}{t.locked ? "（锁定）" : sourceIsImage && t.type === "audio" ? "（图片无音轨）" : ""}</option>)}</select></Field>
            {insertingSpan && <TimelineField label="文字 / 调整区间时长（秒）" fps={timelineFPS} display={timeDisplay} value={spanDuration} min={0.001} onChange={setSpanDuration} />}
            <button type="button" className="gm-studio-primary" disabled={!!activeSequenceLock || !selectedTrack || selectedTrack.locked || (sourceIsImage && selectedTrack.type === "audio") || !insertProposal.value || insertionDuration <= 0 || !!sourceRangeError || clipCount >= caps.limits.clips} onClick={addClip}>＋ {selectedTrack?.type === "text" ? "添加文字片段" : selectedTrack?.type === "adjustment" ? "添加调色区间" : sourceIsImage ? "按显示时长加入图片" : "加入所选素材范围"}</button>
            <p className="gm-studio-note">视频 / 音频新片段速度为 1×；输出时长 = 出点 − 入点。静态图片使用单独显示时长，不消耗源媒体时间。文字与调整轨使用单独的区间时长，与源文件入出点无关。只有点击加入才创建草稿片段。</p>
          </fieldset>
        </section>
      </div>

      <StudioAssets api={{ sourceUrl }} catalog={catalog} state={assetState} savedRevision={saved.revision} busy={frozen} dirty={dirty}
        imageAvailable={supports("stickerCustom")} lutAvailable={supports("lut")} selectedSourceId={sourceId}
        selectedLutId={selection?.clip.lut_id ?? null} canUseLut={!!selection && !activeSequenceLock && !selectionIsSequence && !selection.track.locked && ["video", "overlay", "adjustment"].includes(selection.track.type)}
        canAnimateImage={selectionIsImage && !!selection && !selection.track.locked && supports("kf")}
        onImport={uploadAsset} onCatalogRefresh={refreshAssets} onUseLut={useLut}
        onSelectImage={id => { chooseSource(id); focusSection("source"); }} onAnimateImage={() => focusSection("keyframes")} />

      <section className="gm-studio-panel" data-studio-section="workspace" tabIndex={-1}>
        <h2>工作区 / 快捷键</h2>
        <fieldset disabled={frozen}><div className="gm-studio-fields">
          <Field label="工作区布局"><select aria-label="工作区布局" disabled={!supports("layout")} value={layout} onChange={e => updateWorkspace({ layout: e.target.value as WorkspaceMetadata["layout"] })}>
            <option value="default">默认 · 素材与监看</option><option value="editing">剪辑 · 宽监看与检查器</option><option value="audio">音频 · 优先音频检查器</option><option value="captions">字幕 · 优先文字检查器</option>
          </select></Field>
          <NumberField label="工作区时间线缩放" value={zoom} min={0.25} max={8} step={0.25} disabled={!supports("layout")} onChange={n => { if (n >= 0.25 && n <= 8) updateWorkspace({ timeline_zoom: n }); }} />
        </div>
        <p className="gm-studio-note">布局调整面板宽度与检查器顺序，不改变轨道合成顺序。快捷键仅在工作台非输入区域生效；播放键控制当前真实素材或已完成媒体，不模拟实时合成。</p>
        <div className="gm-studio-fields">{(Object.keys(DEFAULT_SHORTCUTS) as ShortcutAction[]).map(command => <Field key={command}
          label={`快捷键 · ${{ play_pause: "播放 / 暂停", split: "分割", delete: "删除", undo: "撤销", redo: "重做", marker: "标记" }[command]}`}
          hint={`当前生效：${shortcuts[command] ?? "被自定义绑定占用"}；默认 ${DEFAULT_SHORTCUTS[command]}`}>
          <input aria-label={`快捷键 ${command}`} disabled={!supports("hotkey")} value={project.workspace.shortcuts[command] ?? ""} placeholder="点击后按组合键；Backspace 恢复默认" readOnly onKeyDown={e => {
            if (e.key === "Tab" || e.nativeEvent.isComposing) return;
            e.preventDefault(); e.stopPropagation();
            if (e.key === "Backspace") {
              const next = { ...project.workspace.shortcuts }; delete next[command]; updateWorkspace({ shortcuts: next }); return;
            }
            if (e.metaKey || ["Control", "Alt", "Shift"].includes(e.key)) return;
            const key = e.code === "Space" ? "Space" : e.key === "ArrowLeft" ? "Left" : e.key === "ArrowRight" ? "Right" : e.key === "Delete" ? "Delete" : e.key.toUpperCase();
            const chord = canonicalChord([...(e.ctrlKey ? ["Ctrl"] : []), ...(e.altKey ? ["Alt"] : []), ...(e.shiftKey ? ["Shift"] : []), key].join("+"));
            if (!chord) { reportError(new Error("快捷键仅支持 Ctrl/Alt/Shift + 字母、数字、Space、Delete、Left、Right。")); return; }
            if (Object.entries(project.workspace.shortcuts).some(([actionName, value]) => actionName !== command && canonicalChord(value) === chord)) {
              reportError(new Error("该组合键已分配给其他动作。")); return;
            }
            updateWorkspace({ shortcuts: { ...project.workspace.shortcuts, [command]: chord } });
          }} />
        </Field>)}</div>
        </fieldset>
        <button type="button" onClick={() => root.current?.querySelector<HTMLElement>("[data-studio-section='timeline']")?.focus()}>聚焦时间线以使用快捷键</button>
      </section>

      <section className="gm-studio-timeline" data-studio-section="timeline" tabIndex={0} aria-label="多轨时间线">
        <div className="gm-studio-row gm-studio-timeline-heading"><strong className="gm-studio-timecode">{formatTimecode(playhead, timelineFPS)}</strong><span>{projectScope(project).name} · {workingProject.tracks.length} / {caps.limits.tracks} 轨 · 本序列 {workingProject.tracks.flatMap(t => t.clips).length} 段 / 全工程 {clipCount} / {caps.limits.clips} 段 · 生效时长 {displayTime(duration)}</span>
          <button type="button" onClick={() => focusSection("sequences")}>活动序列 / 完整嵌套</button>
          <button type="button" disabled={frozen || !supports("proxy")} onClick={() => focusSection("proxy")}>源代理 / 原片监看（不改变时间线）</button>
          <label>缩放 <input aria-label="时间线缩放" type="range" min={0.25} max={8} step={0.25} disabled={frozen || !supports("layout")} value={zoom} onChange={e => updateWorkspace({ timeline_zoom: Number(e.target.value) })} /></label>
        </div>
        <fieldset data-studio-section="timecontrols" tabIndex={-1} disabled={frozen} className="gm-studio-timecontrols">
          <legend>时间码 / 编辑网格</legend>
          <div className="gm-studio-fields">
            <Field label="时间线帧率" hint="与导出帧率独立；切换不会重定时已有片段"><select aria-label="时间线帧率" disabled={!supports("tc")} value={timelineFPS}
              onChange={e => { updateWorkspace({ timeline_fps: Number(e.target.value) as TimelineFPS }); setTimecodeInput(""); }}>
              {TIMELINE_FPS.map(fps => <option key={fps} value={fps}>{fps} fps</option>)}
            </select></Field>
            <Field label="时间显示"><select aria-label="时间显示" disabled={!supports("tc")} value={timeDisplay} onChange={e => updateWorkspace({ time_display: e.target.value as WorkspaceMetadata["time_display"] })}>
              <option value="seconds">秒</option><option value="frames">整数帧 / 时间码</option>
            </select></Field>
            <Field label="跳转时间码 / 帧号" hint={`HH:MM:SS:FF 或整数帧；FF < ${timelineFPS}，非丢帧`}><input aria-label="跳转时间码 / 帧号" disabled={!supports("tc")} value={timecodeInput}
              placeholder={formatTimecode(playhead, timelineFPS)} onChange={e => setTimecodeInput(e.target.value)} onKeyDown={e => {
                if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
                e.preventDefault(); try { seekTimeline(parseFrameInput(timecodeInput, timelineFPS)); setTimecodeInput(""); } catch (error) { reportError(error); }
              }} /></Field>
            <button type="button" disabled={!supports("tc") || !timecodeInput.trim()} onClick={() => {
              try { seekTimeline(parseFrameInput(timecodeInput, timelineFPS)); setTimecodeInput(""); } catch (error) { reportError(error); }
            }}>跳转到时间码</button>
          </div>
          <div className="gm-studio-row">
            <Check label="磁性吸附（6 帧内）" checked={snapEnabled} disabled={!supports("snap")} onChange={snap_enabled => updateWorkspace({ snap_enabled })} />
            <Check label="波纹编辑（仅选中轨）" checked={rippleEnabled} disabled={!supports("ripple")} onChange={ripple_enabled => { setAdvancedMode(null); updateWorkspace({ ripple_enabled }); }} />
          </div>
          <p className="gm-studio-note">设置随工程快照保存 / 撤销。磁性只为插入起点、移动 / 复制起点及分割提案选择最近零点、标记或其他片段边界；目标和实际提交值均在下方显示。等距时先取较早帧，再按零点 / 标记 / 起点 / 终点与 ID 排序。不自动调整源入出点、邻片段或既有间隙。</p>
        </fieldset>
        <div className="gm-studio-row" data-studio-section="actions" tabIndex={-1}>
          <button type="button" disabled={frozen || !supports("undo") || (!dirty && !saved.can_undo)} onClick={() => action({ op: "undo" })}>撤销{dirty ? "（先存草稿）" : ""}</button>
          <button type="button" disabled={frozen || dirty || !saved.can_redo || !supports("undo")} title={dirty ? "保存新编辑会清空重做历史" : "恢复服务器下一快照"} onClick={() => action({ op: "redo" })}>重做</button>
          <button type="button" disabled={frozen || !canSplit || !supports("edit")}
            data-action="split" title="按下方分割提案提交；两边至少一帧，先清除淡入淡出、关键帧和文字动画" onClick={splitClip}>在播放头分割</button>
          <button type="button" disabled={frozen || !canDelete} data-action={rippleEnabled && !selectionIsSequence ? "ripple_delete" : "delete"} onClick={deleteClip}>{rippleEnabled && !selectionIsSequence ? "波纹删除片段（仅本轨）" : "删除片段（不波纹）"}</button>
          <button type="button" disabled={frozen || selectionIsSequence || !canMove || !supports("edit") || clipCount >= caps.limits.clips} data-action="duplicate" onClick={() => moveOrDuplicate("duplicate")}>复制到指定位置</button>
          <button type="button" disabled={frozen || !canMove || !supports("edit")} data-action="move" onClick={() => moveOrDuplicate("move")}>移动到指定位置</button>
        </div>
        <SnapReadout label="分割位置提案" result={splitProposal} fps={timelineFPS} enabled={snapActive} kind="split" />
        <div className="gm-studio-row gm-studio-move">
          <TimelineField label="移动 / 复制到（秒）" fps={timelineFPS} display={timeDisplay} value={moveAt} onChange={setMoveAt} disabled={frozen} />
          <Field label="移动 / 复制目标轨"><select aria-label="移动 / 复制目标轨" disabled={frozen} value={moveTrack} onChange={e => setMoveTrack(e.target.value)}><option value="">原轨道</option>{workingProject.tracks.filter(t => t.type === selection?.track.type).map(t => <option disabled={t.locked} key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
          <button type="button" disabled={frozen} onClick={() => setMoveAt(playhead)}>使用播放头位置</button>
          <span>先保存草稿再执行服务器动作；拆分保留倒放 / 变速源范围，静态图片两段 trim 均保持 0。</span>
        </div>
        <SnapReadout label="移动 / 复制位置提案" result={moveProposal} fps={timelineFPS} enabled={snapActive} kind="move" />
        <fieldset disabled={frozen} className="gm-studio-clipboard" data-studio-section="clipboard">
          <legend>任务内剪贴板</legend>
          <div className="gm-studio-row">
            <button type="button" disabled={!selection || !!activeSequenceLock || selection.track.locked || !supports("edit")} onClick={() => copyClip(false)}>复制片段到剪贴板</button>
            <button type="button" disabled={!selection || !!activeSequenceLock || selection.track.locked || !supports("edit")} onClick={() => copyClip(true)}>剪切片段（粘贴时移动）</button>
            <button type="button" disabled={!clipClipboard || !!activeSequenceLock || !selectedTrack || selectedTrack.locked || selectedTrack.type !== clipClipboard.type || (clipClipboard.cut && clipClipboard.sequenceId !== project.active_sequence_id) || !supports("edit")}
              onClick={pasteClip}>粘贴到目标轨 / 插入时间</button>
            <button type="button" disabled={!clipClipboard} onClick={() => setClipClipboard(null)}>清空片段剪贴板</button>
          </div>
          <p className="gm-studio-note">{clipClipboard ? `${clipClipboard.cut ? "待剪切" : "已复制"} · ${clipClipboard.clip.id} · ${clipClipboard.type} · ${displayTime(clipClipboard.clip.duration)}；` : "尚无片段；"}
            粘贴使用“素材 → 时间线”的活动目标轨道与插入时间，提交前确认吸附位置。复制可跨本任务序列，保留完整参数 / 嵌套引用并分配新片段 ID；剪切仅限原序列的单次 move，不先删除、不波纹。跨序列剪切明确停用，请返回来源序列或重新复制。仅内存、仅此任务，离开后清空；不复制媒体文件。
          </p>
        </fieldset>
        {selectionIsImage && <p className="gm-studio-warning">静态图片只编辑时间线显示范围，没有源修剪 / 滑移时钟。分割 / 修剪后图片 trim 仍为 0；关键帧 / 淡化不自动缩放或删除，包络放不下时须明确修正。</p>}
        {selectionIsSequence && <p className="gm-studio-warning">选中完整嵌套：父级只允许移动 / 普通删除。分割、波纹删除、修剪、滑移与效果不可用；双击打开子序列。任务内复制保留引用并重新验证循环 / 深度 / 时长与资源额度。</p>}
        <fieldset data-studio-section="trim-modes" data-edit-mode={editMode} tabIndex={-1} disabled={frozen || !!activeSequenceLock || !selection || selectionIsSequence || selection.track.locked || !supports("trimMode")} className="gm-studio-trim-modes">
          <legend>修剪模式 · {selection?.track.name || "请先选择片段"}</legend>
          <Field label="修剪模式"><select aria-label="修剪模式" value={editMode} onChange={e => chooseEditMode(e.target.value as EditMode)}>
            {(Object.keys(EDIT_MODE_LABELS) as EditMode[]).map(mode => <option key={mode} value={mode} disabled={(mode === "ripple" && !supports("ripple")) || (mode === "slip" && (!selection?.clip.source_id || selectionIsImage))}>{EDIT_MODE_LABELS[mode]}</option>)}
          </select></Field>
          <p className="gm-studio-note">{selectionIsImage ? "图片只改变时间线显示范围，不推进源时间；滑移不适用。" : EDIT_EFFECTS[editMode]} 此模式表单不会预先改写片段。其他轨道、标记和源入出点不联动；滚动需要两段无缝相邻，滑动需要三段无缝相邻。波纹 / 滚动 / 滑移 / 滑动会拒绝受影响的重叠；普通修剪保留独立合成重叠语义。所有模式均验证锁、越界和包络，不偷偷重映射关键帧。若子序列终点变更导致父时长不匹配，服务器拒绝该动作；可通过检查器草稿加“确认全部父时长绑定”单次保存，不自动更新。视频 / 音频源 EOF 仍在渲染时最终验证。</p>
          <div className="gm-studio-fields">
            {!selectionIsImage && ["ordinary", "ripple", "slip"].includes(editMode) && <NumberField label="修剪源入点（秒）" value={trimInput} min={0} max={3600} step={0.001} disabled={!selection?.clip.source_id} onChange={setTrimInput} hint="源秒数，不按时间线 FPS 量化" />}
            {["ordinary", "ripple"].includes(editMode) && <TimelineField label={selectionIsImage ? "修剪图片显示时长（秒）" : "修剪输出时长（秒）"} value={durationInput} fps={timelineFPS} display={timeDisplay} min={editMode === "ripple" ? 1 / timelineFPS : 0.001} onChange={setDurationInput} />}
            {(editMode === "roll" || editMode === "slide") && <TimelineField label={editMode === "roll" ? "与下一片段的新边界（秒）" : "滑动新起点（秒）"}
              value={boundaryInput} fps={timelineFPS} display={timeDisplay} onChange={setBoundaryInput} />}
          </div>
          <p className="gm-studio-note" data-edit-submit="trim">实际动作：{editMode === "ordinary" ? "trim" : editMode === "ripple" ? "ripple_trim" : editMode}
            {trimProposal.error && ` · ${trimProposal.error}`}
            {trimProposal.payload && "duration" in trimProposal.payload && ` · 提交时长 ${trimProposal.payload.duration}s`}
            {trimProposal.payload && "at" in trimProposal.payload && ` · 提交 at=${trimProposal.payload.at}s / ${formatTimecode(trimProposal.payload.at, timelineFPS)}`}
            。不对滚动 / 滑动隐式磁性吸附；每次确认明确作用轨道。
          </p>
          <button type="button" data-action={editMode === "ordinary" ? "trim" : editMode === "ripple" ? "ripple_trim" : editMode}
            disabled={!trimProposal.payload || (editMode === "ripple" && !supports("ripple")) || (editMode === "slip" && (!selection?.clip.source_id || selectionIsImage))} onClick={applyTrim}>应用{EDIT_MODE_LABELS[editMode]}（服务器）</button>
        </fieldset>
        <div className="gm-studio-row">
          <Field label="轨道组视图"><select aria-label="轨道组视图" value={project.groups.some(g => g.id === groupFilter) || groupFilter === "*" ? groupFilter : ""} onChange={e => setGroupFilter(e.target.value)}>
            <option value="">全部轨道（原合成顺序）</option><option value="*">未分组</option>{project.groups.map(g => <option key={g.id} value={g.id}>{g.name || g.id}</option>)}
          </select></Field>
          {project.groups.map(g => <button type="button" key={g.id} aria-expanded={!collapsedGroups.includes(g.id)} onClick={() => setCollapsedGroups(old => old.includes(g.id) ? old.filter(id => id !== g.id) : [...old, g.id])}>
            {collapsedGroups.includes(g.id) ? "展开" : "折叠"} {g.name || g.id}（本序列 {workingProject.tracks.filter(t => t.group_id === g.id).length} 轨）
          </button>)}
        </div>
        <div className="gm-studio-timeline-scroll" tabIndex={0} role="region" aria-label="时间线轨道（可横向滚动）">
          <div style={{ minWidth: 880 * zoom }}>
            <div className="gm-studio-track-row"><div className="gm-studio-track-name">{timeDisplay === "frames" ? "帧 / 时间码" : "秒"} · {timelineFPS} fps</div><div className="gm-studio-ruler" role="slider" tabIndex={0} aria-label="时间线播放头" aria-valuemin={0} aria-valuemax={120} aria-valuenow={playhead} aria-valuetext={displayTime(playhead)}
              onClick={e => { const rect = e.currentTarget.getBoundingClientRect(); seekTimeline((e.clientX - rect.left) / rect.width * 120); }}>
              {Array.from({ length: 13 }, (_, i) => <span key={i} style={{ left: `${i / 12 * 100}%`, whiteSpace: 'nowrap', transform: i === 12 ? 'translateX(-100%)' : undefined }}>{timeDisplay === "frames" ? `${i * 10 * timelineFPS}f` : `${i * 10}s`}</span>)}
              {workingProject.markers.map(m => <button type="button" key={m.id} className="gm-studio-marker" style={{ left: `${m.time / 120 * 100}%` }} aria-label={`跳到标记 ${m.label || m.time}`} title={`${m.label} · ${displayTime(m.time)}`} onClick={e => { e.stopPropagation(); seekTimeline(m.time); }}>◆</button>)}
              <i className="gm-studio-playhead" style={{ left: `${playhead / 120 * 100}%` }} />
            </div></div>
            {workingProject.tracks.filter(t => (!t.group_id || !collapsedGroups.includes(t.group_id))
              && (groupFilter === "*" ? t.group_id === null : !project.groups.some(g => g.id === groupFilter) || t.group_id === groupFilter)).map(track => <div className="gm-studio-track-row" key={track.id}>
              <div className="gm-studio-track-name" style={{ borderLeft: `5px solid ${COLORS[track.color]}` }}>
                <button type="button" className="gm-studio-track-label" aria-pressed={trackId === track.id} onClick={() => setTrackId(track.id)}>{track.name || track.type}</button>
                {track.group_id && <small>{project.groups.find(g => g.id === track.group_id)?.name}</small>}
                <div className="gm-studio-row">
                  {(["locked", "hidden", "solo"] as const).map((flag, i) => <button type="button" key={flag} disabled={frozen || !!activeSequenceLock || (track.locked && flag !== "locked")} aria-label={`${track.name}${["锁定", "隐藏", "独奏"][i]}`} aria-pressed={track[flag]} onClick={() => action({ op: "track", track_id: track.id, [flag]: !track[flag] })}>{["锁", "隐", "独"][i]}</button>)}
                </div>
              </div>
              <div className={`gm-studio-lane ${track.hidden || (active.some(t => t.solo) && !track.solo) ? "is-muted" : ""}`}>
                {!track.clips.length && <span className="gm-studio-empty-lane">空轨 · 从素材库加入{track.type === "text" ? "文字" : track.type === "adjustment" ? "调色区间" : "片段"}</span>}
                {track.clips.map((clip, i) => <button type="button" key={clip.id} className={`gm-studio-clip ${clipId === clip.id ? "is-selected" : ""}`}
                  data-clip-id={clip.id} data-track-id={track.id} data-start={clip.start} data-duration={clip.duration} data-sequence-id={clip.sequence_id ?? undefined}
                  style={{ left: `${clip.start / 120 * 100}%`, width: `${clip.duration / 120 * 100}%`, backgroundColor: COLORS[track.color], color: track.color === 'gold' ? 'var(--gm-ink)' : 'var(--gm-surface)', top: 5 + i % 2 * 9 }}
                  title={`${isSequenceClip(clip) ? `完整嵌套 · ${project.sequences.find(sequence => sequence.id === clip.sequence_id)?.name || clip.sequence_id} · 双击打开` : clip.text || catalog.sources.find(s => s.id === clip.source_id)?.name || track.name} · ${displayTime(clip.start)} + ${displayTime(clip.duration)}`}
                  aria-label={`${track.name}片段 ${i + 1}，${clip.start} 秒，时长 ${clip.duration} 秒${isSequenceClip(clip) ? "，完整嵌套，双击打开" : ""}`} aria-pressed={clipId === clip.id} onClick={() => pickClip(track, clip)}
                  onDoubleClick={() => { if (isSequenceClip(clip)) switchSequence(clip.sequence_id); }}>{isSequenceClip(clip) ? `嵌套 · ${project.sequences.find(sequence => sequence.id === clip.sequence_id)?.name || clip.sequence_id}` : clip.text || catalog.sources.find(s => s.id === clip.source_id)?.name || "调整"}</button>)}
                <i className="gm-studio-playhead" style={{ left: `${playhead / 120 * 100}%` }} />
              </div>
            </div>)}
          </div>
        </div>
        <div className="gm-studio-row">
          <button type="button" aria-label="播放头前一帧" onClick={() => seekTimeline(playhead - 1 / timelineFPS)}>−1 帧</button>
          <input aria-label="播放头位置秒" type="range" min={0} max={120} step={1 / timelineFPS} value={playhead} onChange={e => seekTimeline(Number(e.target.value))} />
          <button type="button" aria-label="播放头后一帧" onClick={() => seekTimeline(playhead + 1 / timelineFPS)}>+1 帧</button>
          <TimelineField label="播放头（秒）" fps={timelineFPS} display={timeDisplay} value={playhead} onChange={seekTimeline} />
        </div>
        <p className="gm-studio-note">位置尺固定 0–120 秒；左右键 / ±1 帧按工作区 {timelineFPS} fps 步进，不使用导出 {options.fps} fps。不在输入框、原生控件或 IME 输入时拦截编辑快捷键。播放头只会联动已完成的时间线输出，不模拟实时效果。轨道按列表顺序合成，后面的媒体轨在上层；文字始终在媒体之上。</p>
      </section>

      <section className="gm-studio-capabilities" data-studio-section="palette" tabIndex={-1} aria-label="编辑能力清单">
        <div className="gm-studio-section-title"><h2>专业工具 · 能力清单</h2><span>{tools.filter(t => t.classification === "renderer").length} 项渲染支持 · {tools.filter(t => t.classification === "partial").length} 项有限支持 · {tools.filter(t => t.classification === "metadata").length} 项仅工程设置 · {tools.filter(t => !t.available).length} 项未接入 / {tools.length} 项编辑工具</span></div>
        <p className="gm-studio-note">仅列出当前编辑范围内的工具；可用性与分类来自服务器，有限支持不代表完整实现。</p>
        <div role="tablist" aria-label="专业工具分类" className="gm-studio-tabs">{toolGroups.map(([key, text], i) => <button type="button" key={key} id={`${prefix}-tab-${key}`} role="tab" aria-selected={activeToolGroup === key}
          aria-controls={`${prefix}-panel-${key}`} tabIndex={activeToolGroup === key ? 0 : -1} onClick={() => setTab(key)} onKeyDown={e => {
            let next = i;
            if (e.key === "ArrowRight") next = (i + 1) % toolGroups.length; else if (e.key === "ArrowLeft") next = (i + toolGroups.length - 1) % toolGroups.length;
            else if (e.key === "Home") next = 0; else if (e.key === "End") next = toolGroups.length - 1; else return;
            e.preventDefault(); setTab(toolGroups[next][0]); document.getElementById(`${prefix}-tab-${toolGroups[next][0]}`)?.focus();
          }}>{text} <small>{tools.filter(t => t.group === key).length}</small></button>)}</div>
        {toolGroups.map(([group]) => <div key={group} id={`${prefix}-panel-${group}`} role="tabpanel" aria-labelledby={`${prefix}-tab-${group}`} hidden={activeToolGroup !== group} tabIndex={activeToolGroup === group ? 0 : -1} className="gm-studio-panel">
          {activeToolGroup === group && (group === "fx" || group === "cam") && !compositionMainOnly && <p className="gm-studio-warning">此转场 / 多机位构建器目前只支持无嵌套引用的主时间线。当前为子序列或含嵌套的主时间线，构建器明确停用；不会把活动轨当成根轨保存。请使用多序列面板与普通片段编辑。</p>}
          {activeToolGroup === group && group === "fx" && compositionMainOnly && <TransitionEditor project={project} context={compositionContext}
            disabled={frozen} supports={supports} selectedClipId={clipId} onCommit={commitComposition} />}
          {activeToolGroup === group && group === "cam" && compositionMainOnly && <ManualMulticamEditor project={project} context={compositionContext}
            disabled={frozen} supports={supports} onCommit={commitComposition}
            onPreview={id => { chooseSource(id); focusSection("preview"); }} />}
          {activeToolGroup === group && tools.filter(t => t.group === group).map(tool => {
            return <div key={tool.id} data-tool-id={tool.id} data-local-available={tool.available} className={`gm-studio-tool ${!tool.available ? "is-unsupported" : ""}`}>
              <div><strong>{tool.label}</strong><small>{tool.id}</small><span className={`gm-studio-badge ${tool.available ? "" : "gm-studio-muted"}`}>
                {tool.classification === "partial" ? "有限支持" : tool.classification === "metadata" ? "工程设置" : tool.available ? "本地渲染器" : "本地未支持"}</span></div>
              <div className="gm-studio-tool-controls">
                <fieldset disabled={frozen} aria-label={`${tool.label}实际控件`}>{toolControls(tool)}</fieldset>
                <p>{tool.available ? tool.reason : "当前后端未接入此能力，不会提交处理或模拟成功。"}</p>
                {tool.id === "trCustom" && <p className="gm-studio-note">本界面仅有单边界种类 / 时长 / 缓动 / 音频参数；没有批量按钮、任意自定义滤镜或模板。</p>}
                {tool.id === "camAlign" && <p className="gm-studio-note">仅手工填写各素材同步入点；声波 / 波形同步与自动时间码对齐未实现。</p>}
                {!tool.available && <details><summary>本地能力限制</summary><p>{tool.reason}</p></details>}
                <button type="button" disabled={!tool.available || !toolTarget[tool.id]}
                  aria-label={`${tool.label}：${tool.available ? "定位实际控件" : "未实现"}`} title={tool.reason} onClick={() => openTool(tool)}>
                  {tool.available ? "定位实际控件 ↗" : "不可用"}
                </button>
              </div>
            </div>;
          })}
        </div>)}
        <p className="gm-studio-note">上方选择器 / 按钮操作真实参数或提交服务器动作；“定位实际控件”仅导航。先选中适用片段，锁定时不可修改；修改先保存、再渲染才生效。主 + 最多 4 子序列，每个 ≤8 轨，全部合计 ≤64 片段；活动序列嵌套展开仍 ≤8 轨 / 64 片段 / 16 解码输入 / 120 秒 / 1080p SDR；每任务 100 次历史变更，不是无限轨道 / 撤销，不提供 8K / HDR。有限转场与手动多机位仅在服务器声明能力且无嵌套引用的主时间线开放；无假 AI、实时合成、自动机位同步、光流或跟踪。只渲染当前分类控件，其他分类切换后可访问；审计 / 备份 / 配置均保留。</p>
      </section>

      <div className="gm-studio-edit-grid">
        <section className="gm-studio-panel" data-studio-section="tracks" tabIndex={-1}>
          <h2>轨道管理</h2>
          <fieldset disabled={frozen || !!activeSequenceLock}><div className="gm-studio-fields">
            <Field label="新轨道类型"><select aria-label="新轨道类型" value={newType} onChange={e => setNewType(e.target.value as TrackType)}>{TYPES.map(([v, text]) => <option key={v} value={v}>{text}</option>)}</select></Field>
            <Field label="新轨道名称"><input aria-label="新轨道名称" maxLength={80} value={newTrackName} onChange={e => setNewTrackName(e.target.value)} placeholder="自定义名称" /></Field>
          </div><button type="button" disabled={workingProject.tracks.length >= caps.limits.tracks || !supports("addTrack")} onClick={() => {
            const track = makeTrack(newType, newTrackName.trim() || `${TYPES.find(([type]) => type === newType)?.[1]} ${workingProject.tracks.length + 1}`);
            if (edit(p => { p.tracks.push(track); })) { setTrackId(track.id); setNewTrackName(""); }
          }}>＋ 添加轨道（最多 {caps.limits.tracks}）</button></fieldset>
          {selectedTrack && <fieldset disabled={frozen || !!activeSequenceLock}>
            <legend>选中轨道 · {selectedTrack.name}</legend>
            <Field label="轨道名称"><input aria-label="轨道名称" maxLength={80} value={selectedTrack.name} disabled={selectedTrack.locked} onChange={e => { const name = e.target.value; edit(p => { const t = p.tracks.find(t => t.id === trackId); if (t) t.name = name; }); }} /></Field>
            <Field label="轨道颜色（仅标记，不改变像素）"><select aria-label="轨道颜色" value={selectedTrack.color} disabled={selectedTrack.locked || !supports("trkColor")} onChange={e => action({ op: "track", track_id: trackId, color: e.target.value as TrackColor })}>{Object.keys(COLORS).map(c => <option key={c} value={c}>{c}</option>)}</select></Field>
            <Field label="所属轨道组"><select aria-label="所属轨道组" value={selectedTrack.group_id ?? ""} disabled={selectedTrack.locked || !supports("trkGroup")} onChange={e => { const groupId = e.target.value || null; edit(p => { const t = p.tracks.find(t => t.id === trackId); if (t) t.group_id = groupId; }); }}>
              <option value="">未分组</option>{project.groups.map(g => <option key={g.id} value={g.id}>{g.name || g.id}</option>)}
            </select></Field>
            <div className="gm-studio-row"><button type="button" disabled={selectedTrack.locked || workingProject.tracks.findIndex(t => t.id === trackId) === 0 || workingProject.tracks[workingProject.tracks.findIndex(t => t.id === trackId) - 1]?.locked} onClick={() => reorderTrack(-1)}>向列表前移（下层）</button>
              <button type="button" disabled={selectedTrack.locked || workingProject.tracks.findIndex(t => t.id === trackId) === workingProject.tracks.length - 1 || workingProject.tracks[workingProject.tracks.findIndex(t => t.id === trackId) + 1]?.locked} onClick={() => reorderTrack(1)}>向列表后移（上层）</button></div>
            <button type="button" disabled={selectedTrack.locked || selectedTrack.clips.length > 0} onClick={() => {
              if (selectedTrack.clips.length || selectedTrack.locked) return;
              if (edit(p => { p.tracks = p.tracks.filter(track => track.id !== selectedTrack.id); })) {
                setTrackId(""); setMoveTrack(""); setNotice("已从活动序列草稿移除选中空轨；其他序列与片段保留，保存后进入历史。");
              }
            }}>删除选中空轨（草稿）</button>
            <p className="gm-studio-note">锁定轨道不能编辑或重排，需先用“锁”按钮解锁。隐藏不输出；存在可见独奏轨时仅输出独奏轨。调整轨只影响前面的媒体。</p>
          </fieldset>}
          <fieldset disabled={frozen || !supports("trkGroup")} data-studio-section="groups" tabIndex={-1}>
            <legend>轨道分组 · {project.groups.length} / 8</legend>
            <Field label="新组名称"><input aria-label="新组名称" maxLength={80} value={newGroupName} onChange={e => setNewGroupName(e.target.value)} /></Field>
            <button type="button" disabled={project.groups.length >= 8 || !newGroupName.trim()} onClick={() => { const group = { id: uid("group"), name: newGroupName.trim() }; edit(p => { p.groups.push(group); }); setNewGroupName(""); }}>添加组</button>
            {project.groups.map(g => <div key={g.id}>
              <Field label={`组名 ${g.id}`}><input aria-label={`组名 ${g.id}`} maxLength={80} value={g.name} onChange={e => { const name = e.target.value; edit(p => { const group = p.groups.find(item => item.id === g.id); if (group) group.name = name; }); }} /></Field>
              <button type="button" disabled={allProjectTracks(project).some(t => t.group_id === g.id && t.locked)} onClick={() => {
                if (mutex.current) return;
                try { setProject(removeProjectGroup(project, g.id)); if (groupFilter === g.id) setGroupFilter(""); }
                catch (e) { reportError(e); }
              }}>删除组并取消全部序列归属</button>
            </div>)}
            <p className="gm-studio-note">组是全工程元数据；本视图只筛选 / 折叠活动序列轨道。不重排轨道、不影响输出、不创建嵌套。组 ID 与全部序列、轨道、片段、标记共用唯一命名空间；任一序列中有锁定组员轨时不能删除组。</p>
          </fieldset>
          <fieldset disabled={frozen} data-studio-section="markers" tabIndex={-1}>
            <legend>本序列标记点 · {workingProject.markers.length} / 100</legend>
            <Field label="标记备注"><input aria-label="标记备注" maxLength={120} value={markerLabel} onChange={e => setMarkerLabel(e.target.value)} /></Field>
            <div className="gm-studio-row"><button type="button" disabled={!supports("marker") || workingProject.markers.length >= 100} onClick={() => action({ op: "marker", new_id: uid("marker"), at: playhead, label: markerLabel })}>在播放头添加</button>
              <button type="button" disabled={!workingProject.markers.length} onClick={() => { const sorted = [...workingProject.markers].sort((a, b) => a.time - b.time); seekTimeline((sorted.find(m => m.time > playhead + 0.001) ?? sorted[0]).time); }}>下一个标记</button>
              <button type="button" disabled={!workingProject.markers.length || !supports("marker")} onClick={() => action({ op: "clear_markers" })}>清空标记</button></div>
            <div className="gm-studio-row">{workingProject.markers.map(m => <button type="button" key={m.id} onClick={() => seekTimeline(m.time)}>{m.label || "标记"} · {m.time.toFixed(2)}s</button>)}</div>
            <p className="gm-studio-note">标记仅保存编辑备注，不改变输出画面。</p>
          </fieldset>
        </section>

        <section className="gm-studio-panel gm-studio-inspector" data-studio-section="inspector" tabIndex={-1} aria-label="选中片段检查器">
          <div className="gm-studio-section-title"><h2>片段检查器</h2><span>{selection ? `${selection.track.name} · ${selection.clip.id.slice(-6)}` : "未选中片段"}</span></div>
          <Field label="选择片段"><select aria-label="选择片段" value={clipId} onChange={e => { const item = workingProject.tracks.flatMap(track => track.clips.map(clip => ({ track, clip }))).find(item => item.clip.id === e.target.value); if (item) pickClip(item.track, item.clip); else setClipId(""); }}><option value="">请选择时间线片段</option>{workingProject.tracks.flatMap(t => t.clips.map((c, i) => <option key={c.id} value={c.id}>{t.name} #{i + 1} · {isSequenceClip(c) ? `完整嵌套 ${c.sequence_id}` : c.text || c.source_id || "调整"}</option>))}</select></Field>
          {selection && selectionIsSequence ? <div data-studio-section="nested-inspector"><h3>完整嵌套 · {selection.clip.sequence_id}</h3>
            <p className="gm-studio-warning">父片段范围 0 → {selection.clip.duration}s，严格绑定子实际生效终点。只能通过时间线移动 / 普通删除；此处不提供源、范围、缩放、颜色或音频控件。子文字始终高于全部媒体。</p>
            <button type="button" disabled={frozen || !supports("tlPick")} onClick={() => switchSequence(selection.clip.sequence_id)}>进入此子序列编辑</button>
          </div> : selection ? <ClipInspector clip={selection.clip} track={selection.track} sources={catalog.sources} disabled={frozen || !!activeSequenceLock || selection.track.locked}
            assetState={assetState} onUseLut={useLut}
            fps={timelineFPS} timeDisplay={timeDisplay} update={updateClip} supports={supports} colorClipboard={colorClipboard} copyColor={copyColor} pasteColor={pasteColor} />
            : <p className="gm-studio-empty">从时间线选择片段。只有适用于该轨道类型的真实参数才会显示；源文件不会被修改。</p>}
        </section>
      </div>

      <section className="gm-studio-panel" data-studio-section="subtitles" tabIndex={-1}>
        <h2>SRT 字幕导入</h2>
        <p className="gm-studio-note">读取本地 UTF-8 文本并原样发送给服务器验证，不在客户端编造字幕片段。仅纯文本 SRT，1–64 条、≤64,000 字符、≤256 KiB 请求；时间码严格递增且不重叠，结束 ≤120 秒，每条 ≤500 字符。HTML / ASS 不支持。</p>
        <fieldset disabled={frozen || !!activeSequenceLock || !supports("subIO")}>
          <Field label="选择 SRT 文件"><input aria-label="选择 SRT 文件" type="file" accept=".srt,application/x-subrip,text/plain" onChange={e => {
            const file = e.target.files?.[0]; e.target.value = ""; if (!file) return;
            void operation("读取本地 SRT", async () => {
              if (file.size > caps.limits.request_bytes - 256) throw new Error("SRT 文件超出请求大小限制。");
              const text = await file.text();
              if (!text.trim() || [...text].length > 64000) throw new Error("SRT 必须为 1–64,000 字符。");
              if (mounted.current) setSrtText(text);
            });
          }} /></Field>
          <Field label="SRT 原始文本"><textarea aria-label="SRT 原始文本" rows={6} maxLength={128000} value={srtText} onChange={e => setSrtText(e.target.value)} /></Field>
          <button type="button" disabled={!srtText.trim() || [...srtText].length > 64000 || workingProject.tracks.length >= caps.limits.tracks || clipCount >= caps.limits.clips}
            onClick={() => void operation("服务器验证并导入 SRT", async () => {
              const newTrackId = uid("srt_track");
              if (new TextEncoder().encode(JSON.stringify({ expected_revision: saved.revision, track_id: newTrackId, srt: srtText })).length > caps.limits.request_bytes) throw new Error("SRT JSON 请求超过大小限制。");
              const current = await saveDraft();
              const next = await api.importSubtitles(current.revision, newTrackId, srtText);
              remember(next); setTrackId(newTrackId); setClipId(projectScope(next.project).tracks.find(t => t.id === newTrackId)?.clips[0]?.id ?? "");
              setSrtText(""); setNotice(`服务器验证通过，SRT 已加入新文字轨 · r${next.revision}`);
              try { await refreshAudit(); } catch { /* Import already committed. */ }
            })}>保存草稿并导入新字幕轨</button>
          <p className="gm-studio-note">始终在活动序列新建轨道，保留其他序列与已有 / 锁定轨道；每序列最多 8 轨，主 + 全部子序列合计 64 片段，剩余额度由服务器验证。若改变被引用子序列终点，服务器会拒绝：先在父级移除引用后导入，不自动更新绑定。失败保留 SRT 文本，不显示成功。导入后可通过下方渲染 / 导出选择 SRT 或 ASS。</p>
        </fieldset>
      </section>

      <section className="gm-studio-panel" data-studio-section="export" tabIndex={-1}>
        <div className="gm-studio-section-title"><h2>渲染 / 导出</h2><span className="gm-studio-badge gm-studio-gold">SDR · BT.709 · ≤1080p</span></div>
        {!caps.render_available && <p role="alert" className="gm-studio-warning">服务器未找到 FFmpeg / ffprobe，不能提交媒体作业。</p>}
        <fieldset disabled={frozen}><div className="gm-studio-fields">
          <Field label="导出内容"><select aria-label="导出内容" value={exportTarget} onChange={e => setExportTarget(e.target.value as "render" | "export")}><option value="render">当前活动序列「{projectScope(project).name}」· POST /render</option><option value="export">流水线原成片独立导出 · POST /export</option></select></Field>
          <Field label="输出格式"><select aria-label="输出格式" value={options.format} onChange={e => { const format = e.target.value as ExportFormat; setOptions(old => ({ ...optionsForFormat(old, format), frame_time: format === "png" ? Number(playhead.toFixed(6)) : null })); }}>{FORMATS.map(f => <option key={f} value={f}>{f.toUpperCase()}{f === "png" ? "（指定单帧）" : f === "gif" ? "（前 ≤6 秒）" : ""}</option>)}</select></Field>
          {options.format === "png" && <div><NumberField label="PNG 取帧时间（秒）" min={0} max={120} step={1 / 30} value={options.frame_time ?? 0} onChange={n => setOptions(o => ({ ...o, frame_time: n }))} hint="数值秒；必须小于输出时长，服务器向下取整到 30 fps 帧网格" />
            <button type="button" onClick={() => setOptions(o => ({ ...o, frame_time: Number(playhead.toFixed(6)) }))}>使用当前播放头</button></div>}
          <Field label="分辨率" hint={!formatSupport(options.format).geometry ? "此格式不适用，固定默认值" : "不提供 2K / 4K / 8K"}><select aria-label="导出分辨率" disabled={!formatSupport(options.format).geometry} value={options.resolution} onChange={e => setOptions(o => ({ ...o, resolution: Number(e.target.value) as ExportOptions["resolution"] }))}>{[360, 720, 1080].map(n => <option value={n} key={n}>{n}p</option>)}</select></Field>
          <Field label="画幅" hint={exportTarget === "export" ? "原成片独立导出居中裁切填满，边缘包装可能被裁掉" : "时间线遵循各片段 contain / cover；默认 contain"}><select aria-label="导出画幅" disabled={!formatSupport(options.format).geometry} value={options.aspect} onChange={e => setOptions(o => ({ ...o, aspect: e.target.value as ExportOptions["aspect"] }))}>{["16:9", "9:16", "1:1"].map(v => <option value={v} key={v}>{v}</option>)}</select></Field>
          <Field label="帧率" hint={!formatSupport(options.format).fps ? "此格式不适用，固定默认 30" : "GIF 的帧延迟量化到厘秒"}><select aria-label="导出帧率" disabled={!formatSupport(options.format).fps} value={options.fps} onChange={e => setOptions(o => ({ ...o, fps: Number(e.target.value) as ExportOptions["fps"] }))}>{[24, 25, 30, 60].map(n => <option value={n} key={n}>{n} fps</option>)}</select></Field>
          <Field label="字幕" hint="none 仅隐藏字幕文字，不隐藏普通文字与强制 AI 披露"><select aria-label="导出字幕" value={options.subtitles} onChange={e => setOptions(o => ({ ...o, subtitles: e.target.value as ExportOptions["subtitles"] }))}>{formatSupport(options.format).subtitleStyles.map(s => <option value={s} key={s}>{s === "standard" ? "标准" : s === "large" ? "大字 · 1.5×" : "无字幕"}</option>)}</select></Field>
          <NumberField label="视频目标码率（kbps）" value={options.video_bitrate_kbps} min={250} max={12000} step={1} disabled={!formatSupport(options.format).videoBitrate} onChange={n => setOptions(o => ({ ...o, video_bitrate_kbps: n }))} hint="编码目标，非精确文件码率；不适用格式固定默认 4000" />
          <Field label="音频码率" hint="立体声 48 kHz；无音频 / 无损格式不适用"><select aria-label="导出音频码率" disabled={!formatSupport(options.format).audioBitrate} value={options.audio_bitrate_kbps} onChange={e => setOptions(o => ({ ...o, audio_bitrate_kbps: Number(e.target.value) as ExportOptions["audio_bitrate_kbps"] }))}>{[96, 128, 192, 256, 320].map(n => <option value={n} key={n}>{n} kbps</option>)}</select></Field>
        </div></fieldset>
        <div className="gm-studio-warning">
          {exportTarget === "render" ? <p>渲染已保存的当前时间线。{hasBurnedFinal ? "当前使用 final，字幕已烧录，不能去除或放大；必须选择标准字幕。" : "干净画面不包含配音、字幕或音乐，需明确添加相应轨道。"}</p>
            : <p>独立导出不使用 Studio 时间线：标准视觉输出使用 final，保留混音、音乐、标题、字幕、包装与收尾效果，但居中裁切可能裁掉边缘。无字幕 / 大字使用干净画面 + final 最终混音（不是 narration），保留音乐但无法保留可选烧录图文或收尾淡化；标题仅按已验证元数据近似重建，非原样还原。缺少所需素材时失败，不降级假成功。</p>}
          <p>MP3 / WAV 只含音频；GIF / PNG 不含音频；GIF 仅前 min(时长, 6) 秒；PNG 按指定时间取单帧，默认 0 秒。SRT / ASS 需要真实字幕片段与时间。生成内容的强制披露不能关闭；QC 阻断或丢失披露的请求由服务器拒绝。</p>
        </div>
        {!!warnings.length && <ul role="alert" className="gm-studio-warning">{warnings.map(w => <li key={w}>{w}</li>)}</ul>}
        <div className="gm-studio-row"><button type="button" className="gm-studio-primary" disabled={frozen || running || !caps.render_available || warnings.length > 0 || (exportTarget === "render" && (duration <= 0 || (hasBurnedFinal && options.subtitles !== "standard"))) || options.video_bitrate_kbps < 250 || options.video_bitrate_kbps > 12000 || !Number.isInteger(options.video_bitrate_kbps)} onClick={() => startJob(exportTarget)}>{dirty ? "保存并提交" : "提交"}{exportTarget === "render" ? "时间线渲染" : "原成片独立导出"} · {options.format.toUpperCase()}</button>
          <span>每任务同时 1 个作业，后台 CPU 编码，无 GPU / 批处理队列。</span></div>
        <details><summary>资源边界与格式语义</summary><p>主 + 最多 4 子序列；每序列最多 {caps.limits.duration_seconds}s / {caps.limits.tracks} 轨 / 100 标记；全部序列合计最多 {caps.limits.clips} 片段。活动序列嵌套展开仍 ≤8 生效层 / 64 片段 / 16 解码输入（图片 / 重复引用计数），32 个全高清等效源帧；LUT 绑定验证但不另占解码输入；倒放缓冲 ≤128 MiB；输出 &lt;{mb(caps.limits.output_bytes)}，每任务 {mb(caps.limits.task_output_bytes)} / {caps.limits.jobs_per_task} 作业（失败 / 取消也计数）；{caps.limits.history_mutations} 次历史变更。最多 300s 作业时限。不是无限轨道或无限撤销。</p>
          <p>MP4 / MOV / MKV：H.264 + AAC；AVI：MPEG-4 Part 2 + MP3；WAV：PCM 16-bit。SDR 视频实际转换为 BT.709 有限范围 YUV 并设置色彩标签；不是 HDR 色彩转换。HDR 源被拒绝。</p>
          <pre>{JSON.stringify(caps.limits, null, 2)}</pre></details>
      </section>

      <section className="gm-studio-panel" data-studio-section="jobs" tabIndex={-1}>
        <div className="gm-studio-section-title"><h2>后台作业与输出</h2><span>{jobs.length} 个已知作业</span></div>
        <p className="gm-studio-note">后台完成后手动选择预览；不会跳回源视频假装展示修改。页面隐藏时暂停查询，重新可见立即刷新。进度条为不定进度，服务器不提供完成百分比。</p>
        {pollError && <p role="alert" className="gm-studio-warning">{pollError}</p>}
        {!jobs.length && <p>尚无可读取的 Studio 作业。</p>}
        <div className="gm-studio-job-list">{jobs.map(job => <article key={job.id} className="gm-studio-job" data-job-kind={job.kind}>
          <div><strong>{isProxyJob(job) ? `源代理 · ${job.source_id}` : job.kind === "render" ? "时间线渲染" : "原成片独立导出"} · r{job.revision} · {job.options.format.toUpperCase()}</strong><small>{new Date(job.created_at * 1000).toLocaleString()} · {job.id.slice(0, 8)}</small>
            {isProxyJob(job) && <p className="gm-studio-note">{PROXY_EXPLANATION}。{job.cached === true ? "服务器已确认复用此记录；" : ""}不是导出作业。</p>}</div>
          <div role="status"><span className={`gm-studio-badge ${job.state === "succeeded" ? "" : "gm-studio-gold"}`}>{STATE_NAMES[job.state]}</span>
            {isActiveJob(job) && <progress aria-label={`${job.id.slice(0, 8)} 编码进度（服务器未提供百分比）`} />}
            {job.error && <p className="gm-studio-warning">{job.error}</p>}
            {job.cleanup_pending === true && <p className="gm-studio-warning" data-job-cleanup="pending">作业已结束，但残留输出尚未清理，仍占用 Studio 存储额度；没有可下载结果。可读取状态重试清理，不会重新编码。</p>}
            {job.export_semantics?.warnings.map((warning, i) => <p key={i} className="gm-studio-warning">{warning}</p>)}
            {job.options.format === "png" && job.result && <small>实际取帧：{job.result.frame_time ?? job.options.frame_time ?? 0}s；duration 为源时间线时长，非图片播放时长。</small>}
            {job.result && <small>{mb(job.result.bytes)} · {job.result.duration.toFixed(3)}s · {job.qc}</small>}
          </div>
          <div className="gm-studio-row">{isActiveJob(job) && <button type="button" disabled={frozen} onClick={() => { void cancelJob(job); }}>取消并清理</button>}
            {job.cleanup_pending === true && <button type="button" disabled={frozen} onClick={() => void operation("读取任务状态并重试残留清理", async () => {
              const latest = await api.job(job.id);
              if (!mounted.current) return;
              upsertJob(latest);
              setNotice(latest.cleanup_pending === true ? "残留清理尚未完成，文件仍计入额度；未重新提交编码任务。" : "服务器已确认残留输出清理完成；原作业结果与工程不变。");
            })}>读取状态并重试清理</button>}
            {isProxyJob(job) && job.state === "succeeded" && <><button type="button" disabled={frozen || !supports("proxy") || proxyState.status !== "ready" || !canPreviewProxy(job, catalog.sources)}
              onClick={() => { chooseProxyPreview(job.id); focusSection("preview"); }}>查看源代理（不含时间线效果）</button>
              <p className="gm-studio-note">仅走私有源代理预览地址；源哈希与成片修订由服务器再次核验。不能作为导出输出。</p></>}
            {job.kind !== "proxy" && job.state === "succeeded" && job.output_id && <><button type="button" disabled={!canReadOutput(job)} onClick={() => { if (canReadOutput(job)) { setPreview({ kind: "job", id: job.id }); focusSection("library"); } }}>查看已完成输出</button>
              {canReadOutput(job) && <a href={outputUrl(job.output_id)} download={`studio-r${job.revision}.${job.options.format}`} rel="noreferrer">下载 {job.options.format.toUpperCase()}</a>}
              <p className="gm-studio-note">已完成输出保持不变，捕获工程 r{job.revision}{job.pipeline_revision !== undefined ? ` / 成片 v${job.pipeline_revision}` : '；旧记录未提供成片版本'}。不代表当前未渲染的编辑；媒体读取仍由服务器校验。</p>
            </>}
          </div>
          <details><summary>捕获参数 / 实测信息</summary>{isProxyJob(job) && <p>{proxyProfileReadout(job)}</p>}<pre>{JSON.stringify({ kind: job.kind, source_id: job.source_id, cache_key: job.cache_key, cached: job.cached, revision: job.revision, pipeline_revision: job.pipeline_revision, options: job.options, export_semantics: job.export_semantics, result: job.result, cleanup_pending: job.cleanup_pending, cleanup_error: job.cleanup_error, disclosure: job.disclosure, qc: job.qc }, null, 2)}</pre></details>
        </article>)}</div>
      </section>

      <section className="gm-studio-panel" data-studio-section="backup" tabIndex={-1}>
        <h2>工程 JSON · 备份 / 导入</h2>
        <div className="gm-studio-row"><button type="button" disabled={frozen} onClick={() => void operation("导出服务器工程", async () => { const backup = await api.backup(); if (mounted.current) downloadJson(backup, `studio-r${backup.revision}.json`); })}>下载已保存工程（服务器）</button>
          <button type="button" onClick={() => downloadJson({ revision: saved.revision, project }, "studio-local-draft.json")}>下载本地草稿（含未保存编辑）</button>
          <button type="button" onClick={showHistory}>快照与审计</button></div>
        <p className="gm-studio-note">服务器备份不包含未保存编辑。JSON 包含完整主轨 / 主标记、全部 sequences / 子轨 / 子标记、active_sequence_id、嵌套 sequence_id、关键帧、蒙版、调色 / RGB 曲线 / lut_id、文字模板 / 动画、分组、素材标签 / 评分、时间线 FPS / 吸附 / 波纹设置、源入出点及 transition_in 转场，无源媒体 / 图片 / LUT 文件；手动多机位仍为普通片段，不是假嵌套配方。只能引用此任务素材。旧工程仅补缺省字段，已有内容与精确秒数保留；未知字段 / 无效引用整体拒绝，不默默丢弃。服务器继续验证源 ID、全部序列锁与修订。SRT 使用上方专用导入；不支持外部工程、任意路径或 ASS 脚本。</p>
        <Field label="工程名称"><input aria-label="工程名称" maxLength={120} value={project.name} disabled={frozen} onChange={e => { const name = e.target.value; edit(p => { p.name = name; }); }} /></Field>
        <Field label="选择工程 JSON"><input aria-label="选择工程 JSON" type="file" accept=".json,application/json" disabled={frozen} onChange={e => {
          const file = e.target.files?.[0]; e.target.value = ""; if (!file) return;
          void operation("读取本地 JSON", async () => {
            if (file.size > caps.limits.request_bytes - 128) throw new Error("工程文件超出 256 KiB 限制。");
            const text = await file.text(); parseProjectImport(text, caps.limits.request_bytes); if (mounted.current) setImportText(text);
          });
        }} /></Field>
        <Field label="工程 JSON 内容"><textarea aria-label="工程 JSON 内容" rows={5} value={importText} disabled={frozen} onChange={e => setImportText(e.target.value)} placeholder="粘贴 Studio 工程或备份；提交后由服务器验证字段、范围、锁与源 ID" /></Field>
        <button type="button" disabled={frozen || !importText.trim()} onClick={() => {
          if (!window.confirm("用导入工程替换当前草稿？服务器将验证锁、素材和修订号，失败时不会替换。")) return;
          void operation("验证并导入工程", async () => {
            const value = parseProjectImport(importText, caps.limits.request_bytes);
            const next = await api.importProject(saved.revision, value); remember(next); setClipId(""); setTrackId(projectScope(next.project).tracks[0]?.id ?? "");
            setImportText(""); setNotice(`服务器验证通过，已导入 r${next.revision}。`); try { await refreshAudit(); } catch { /* Import already committed. */ }
          });
        }}>由 API 验证并导入（替换草稿）</button>
      </section>

      <section className="gm-studio-panel" data-studio-section="history" tabIndex={-1}>
        <div className="gm-studio-section-title"><h2>快照 / 审计</h2><button type="button" aria-expanded={historyOpen} onClick={() => setHistoryOpen(v => !v)}>{historyOpen ? "收起" : "展开"}</button></div>
        {historyOpen && <><p className="gm-studio-note">服务器保存历史，撤销 / 重做可恢复；API 不提供任意历史快照读取。以下 JSON 快照仅为本次打开期间实际读取的版本。审计不是签名合规日志。</p>
          <div className="gm-studio-row"><button type="button" disabled={frozen} onClick={() => void operation("刷新审计", async () => {
            if (!mounted.current) return;
            const records = await api.audit(); if (!mounted.current) return; setAudit(records);
            for (const id of [...new Set(records.map(r => r.job_id).filter((id): id is string => !!id))].slice(-20)) upsertJob(await api.job(id));
            setPollError("");
          })}>刷新审计与任务</button>{snapshots.map(snapshot => <button type="button" key={snapshot.revision} onClick={() => downloadJson(snapshot, `studio-r${snapshot.revision}.json`)}>下载已读取快照 r{snapshot.revision}</button>)}</div>
          <div className="gm-studio-audit"><table><thead><tr><th>时间</th><th>操作</th><th>修订 / 作业</th><th>详情</th></tr></thead><tbody>{[...audit].reverse().map(record => <tr key={record.id}><td data-label="时间">{new Date(record.time * 1000).toLocaleString()}</td><td data-label="操作">{record.op}</td><td data-label="修订 / 作业">{record.revision !== undefined ? `r${record.revision}` : record.job_id?.slice(0, 8) ?? "—"}</td><td data-label="详情"><details><summary>查看记录</summary><pre>{JSON.stringify(record, null, 2)}</pre></details></td></tr>)}</tbody></table></div>
          <details><summary>当前服务器工程快照 r{saved.revision}（不含本地草稿）</summary><pre>{JSON.stringify(saved.project, null, 2)}</pre></details>
        </>}
      </section>
      <footer className="gm-studio-note">{caps.quality} · 未支持的能力保持禁用，具体范围以各项说明与服务器校验为准。</footer>
    </>}
  </div>;
}

const TRANSITION_LABELS: Record<TransitionKind, string> = {
  dissolve: "交叉溶解", wipe_left: "向左擦除", wipe_right: "向右擦除", wipe_up: "向上擦除", wipe_down: "向下擦除",
};
const EASING_LABELS: Record<KeyPoint["easing"], string> = {
  linear: "线性", ease_in: "缓入", ease_out: "缓出", ease_in_out: "缓入缓出",
};
function requiredSeconds(value: string, label: string): number {
  const text = value.trim();
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(text) || !Number.isFinite(Number(text))) throw new Error(`请明确填写${label}（非负有限秒数），空白不是 0。`);
  return Number(text);
}
function compositionAttempt<T extends CompositionProposal>(run: () => T): { proposal: T | null; error: string } {
  try { return { proposal: run(), error: "" }; }
  catch (e) { return { proposal: null, error: e instanceof Error ? e.message : "提案无效。" }; }
}
function CompositionSummary({ proposal }: { proposal: CompositionProposal }) {
  return <div className="gm-studio-composition-summary" data-composition-proposal={proposal.kind}>
    <p className="gm-studio-note">仅参数 / 几何提案，尚未保存或渲染。提案后：{proposal.stats.tracks} 轨 / {proposal.stats.clips} 片段 / {proposal.stats.inputs} 解码输入（含隐藏轨） / 请求 {proposal.stats.requestBytes} 字节。</p>
    <p className="gm-studio-note">全工程终点 {proposal.beforeEnd}s → {proposal.stats.end}s；当前可见 / 独奏生效终点 {proposal.beforeActiveEnd}s → {proposal.stats.activeEnd}s。其他轨道可能使工程终点不变。</p>
    <ul className="gm-studio-warning">{proposal.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
  </div>;
}

function TransitionEditor({ project, context, disabled, supports, selectedClipId, onCommit }: {
  project: StudioProject; context: CompositionContext; disabled: boolean; supports: (id: string) => boolean;
  selectedClipId: string; onCommit: CompositionCommit;
}) {
  const [leftId, setLeftId] = useState("");
  const [kind, setKind] = useState<TransitionKind>("dissolve");
  const [seconds, setSeconds] = useState("0.5");
  const [easing, setEasing] = useState<KeyPoint["easing"]>("linear");
  const [audio, setAudio] = useState(false);
  const available = supports("trLib") && supports("trDur");
  const boundaries = transitionBoundaries(project);
  const selectedIncoming = project.tracks.flatMap(lane => lane.clips).find(clip => clip.id === selectedClipId)?.transition_in;
  const chosenLeft = leftId || selectedIncoming?.left_clip_id || (boundaries.some(item => item.leftClipId === selectedClipId) ? selectedClipId : "");
  const boundary = boundaries.find(item => item.leftClipId === chosenLeft);
  const track = project.tracks.find(item => item.id === boundary?.trackId);
  const left = track?.clips.find(clip => clip.id === boundary?.leftClipId);
  const right = track?.clips.find(clip => clip.id === boundary?.rightClipId);
  const selectedBoundary = (): TransitionBoundary => {
    if (!boundary) throw new Error("请选择左片段及同轨无缝相接的右片段；已有入转场的相邻边界可移除。");
    return boundary;
  };
  const buildAdd = (current: StudioProject, revision = context.expectedRevision ?? 0) => buildTransitionProposal(current,
    selectedBoundary(), { kind, duration: requiredSeconds(seconds, "转场时长"), easing, audio }, { ...context, expectedRevision: revision });
  const buildRemove = (current: StudioProject, revision = context.expectedRevision ?? 0) => buildRemoveTransitionProposal(current,
    selectedBoundary(), { ...context, expectedRevision: revision });
  const adding = compositionAttempt(() => buildAdd(project)), removing = compositionAttempt(() => buildRemove(project));
  const shown = right?.transition_in ? removing : adding;
  const proposal = shown.proposal;
  const describe = (edge: TransitionBoundary) => {
    const lane = project.tracks.find(item => item.id === edge.trackId)!;
    const clip = lane.clips.find(item => item.id === edge.leftClipId)!;
    return `${lane.name || lane.id}${lane.locked ? "（锁定）" : ""} · ${clip.id} · ${clip.start}s → ${clip.start + clip.duration}s`;
  };
  return <section data-studio-section="transitions" tabIndex={-1} className="gm-studio-composition" aria-label="真实片段边界转场">
    <h3>片段边界转场 · 真实时间线</h3>
    {!available && <p className="gm-studio-warning">当前后端尚未声明 trLib / trDur，不能提交转场。不是片段淡化或模拟效果。</p>}
    <fieldset disabled={disabled || !available}>
      <div className="gm-studio-fields">
        <Field label="转场左片段"><select aria-label="转场左片段" data-transition-left value={chosenLeft} onChange={e => setLeftId(e.target.value)}>
          <option value="">选择左片段</option>{boundaries.map(edge => <option key={edge.leftClipId} value={edge.leftClipId}>{describe(edge)}</option>)}
        </select></Field>
        <Field label="转场右片段（同轨紧邻）"><select aria-label="转场右片段（同轨紧邻）" data-transition-right value={right?.id ?? ""}
          onChange={() => { /* Immediate neighbor is determined by the selected left clip, never an arbitrary pair. */ }}>
          {!right && <option value="">先选择左片段</option>}{right && <option value={right.id}>{right.id} · {right.start}s → {right.start + right.duration}s</option>}
        </select></Field>
        <Field label="转场类型"><select aria-label="转场类型" data-transition-kind={kind} value={kind} onChange={e => setKind(e.target.value as TransitionKind)}>
          {TRANSITION_KINDS.map(value => <option key={value} value={value}>{TRANSITION_LABELS[value]}</option>)}
          <option value="custom" disabled>自定义滤镜 / 模板（未支持）</option>
        </select></Field>
        <Field label="转场时长（秒）" hint={`至少 1 / ${project.workspace.timeline_fps} 秒且 ≤1.2 秒，并且 ≤左右各自时长的一半；不自动量化或修剪`}>
          <input type="number" inputMode="decimal" aria-label="转场时长（秒）" data-transition-duration={seconds} value={seconds}
            min={1 / project.workspace.timeline_fps} max={1.2} step="any" onChange={e => setSeconds(e.target.value)} />
        </Field>
        <Field label="转场画面缓动"><select aria-label="转场画面缓动" data-transition-easing value={easing} onChange={e => setEasing(e.target.value as KeyPoint["easing"])}>
          {(Object.keys(EASING_LABELS) as KeyPoint["easing"][]).map(value => <option key={value} value={value}>{EASING_LABELS[value]}</option>)}
        </select></Field>
      </div>
      <Check label="转场音频：左线性淡出 / 右线性淡入" checked={audio} onChange={setAudio} />
      <p className="gm-studio-note">未勾选音频时保留原重叠混音，不自动静音。勾选后声音仍为线性包络，不跟随画面缓动。两端必须原已为全帧 cover / scale 1 / x、y 0 / opacity 1，无蒙版、色度键、关键帧、淡化或定格；不自动更改上述参数。参与转场的源按不透明全帧 RGB 渲染，不保留源内 Alpha。</p>
      {right?.transition_in && <p className="gm-studio-note">已保存 / 草稿边界：{TRANSITION_LABELS[right.transition_in.kind]}，{right.transition_in.duration}s，{EASING_LABELS[right.transition_in.easing]}，音频{right.transition_in.audio ? "线性交叉淡化" : "保持重叠混音"}。修改种类 / 时长前请先移除并确认反向平移。</p>}
      {left && right && <p className="gm-studio-note">现有左终点 {left.start + left.duration}s / 右起点 {right.start}s。{track?.locked && "本轨已锁定，先解锁。"}</p>}
      {shown.error && <p className="gm-studio-warning" data-transition-problem>{shown.error}</p>}
      {proposal && <>
        <p className="gm-studio-warning" data-transition-shift={proposal.shiftSeconds}><strong>明确移动：</strong>右片段及本轨全部后续片段将{proposal.shiftSeconds < 0 ? "提前" : "后移"} {Math.abs(proposal.shiftSeconds)} 秒（共 {proposal.movements.length} 段）。其他轨道 / 标记不动，工程终点可能改变，也可能因其他轨道而不变。</p>
        <div className="gm-studio-composition-scroll" role="region" tabIndex={0} aria-label="转场移动提案"><table>
          <thead><tr><th>片段</th><th>原起点</th><th>提案起点</th></tr></thead>
          <tbody>{proposal.movements.map(move => <tr key={move.clipId} data-transition-move={move.clipId}><td>{move.clipId}</td><td>{move.before}s</td><td>{move.after}s</td></tr>)}</tbody>
        </table></div>
        <CompositionSummary proposal={proposal} />
      </>}
      <div className="gm-studio-row">
        <button type="button" data-transition-apply disabled={!adding.proposal || !!right?.transition_in} onClick={() => {
          const next = adding.proposal;
          if (next) onCommit(next, buildAdd, `确认添加${TRANSITION_LABELS[kind]}转场？\n右片段及「${track?.name || next.trackId}」全部后续片段提前 ${Math.abs(next.shiftSeconds)} 秒，共 ${next.movements.length} 段。\n其他轨道不动，工程终点可能不变。`);
        }}>确认添加转场并保存</button>
        <button type="button" data-transition-remove disabled={!removing.proposal} onClick={() => {
          const next = removing.proposal;
          if (next) onCommit(next, buildRemove, `确认移除此转场？\n右片段及「${track?.name || next.trackId}」全部后续片段后移 ${next.shiftSeconds} 秒，共 ${next.movements.length} 段，恢复无缝硬切。\n其他轨道不动；超过 120 秒 / 存在冲突的提案已被拒绝。`);
        }}>确认移除转场并反向平移</button>
      </div>
    </fieldset>
    <p className="gm-studio-note">只支持明确的单边界提案；批量 / 自定义转场未实现。实际移动在工程快照中可见，渲染器不替你改几何。先确认保存，再用现有“渲染时间线预览”；源播放器不会显示转场。</p>
  </section>;
}

interface MulticamAngleDraft { source_id: string; syncIn: string }
function ManualMulticamEditor({ project, context, disabled, supports, onCommit, onPreview }: {
  project: StudioProject; context: CompositionContext; disabled: boolean; supports: (id: string) => boolean;
  onCommit: CompositionCommit; onPreview: (sourceId: string) => void;
}) {
  const [mode, setMode] = useState<MulticamRecipe["mode"]>("grid");
  const [placement, setPlacement] = useState<MulticamRecipe["placement"]>("append");
  const [seconds, setSeconds] = useState("");
  const [angles, setAngles] = useState<MulticamAngleDraft[]>([{ source_id: "", syncIn: "" }, { source_id: "", syncIn: "" }]);
  const [masterChoice, setMasterChoice] = useState("");
  const [master, setMaster] = useState<MulticamAngleDraft>({ source_id: "", syncIn: "" });
  const [cutList, setCutList] = useState("");
  const baseAvailable = supports("camN") && supports("camAlign");
  const available = baseAvailable && (mode === "grid" || supports("camBatch"));
  const videos = context.sources.filter(source => !isImageSource(source) && isMulticamVideoSource(source));
  const changeAngle = (index: number, patch: Partial<MulticamAngleDraft>) => setAngles(old => old.map((angle, i) => i === index ? { ...angle, ...patch } : angle));
  const markFor = (id: string) => project.workspace.source_marks.find(mark => mark.source_id === id);
  const recipe = (): MulticamRecipe => {
    const parsed = angles.map((angle, index) => ({ source_id: angle.source_id, sync_in: requiredSeconds(angle.syncIn, `机位 ${index + 1} 的同步入点`) }));
    const audio = masterChoice === "dedicated" ? { source_id: master.source_id, sync_in: requiredSeconds(master.syncIn, "主音轨同步入点") }
      : /^angle:[1-4]$/.test(masterChoice) ? parsed[Number(masterChoice.slice(6)) - 1] : undefined;
    if (!audio) throw new Error("请明确选择一条连续主音轨（来自某机位或其他目录素材）。");
    return { mode, placement, duration: requiredSeconds(seconds, "共同输出时长"), angles: parsed, master: audio,
      cuts: mode === "switch" ? parseMulticamCuts(cutList) : [] };
  };
  const build = (current: StudioProject, revision = context.expectedRevision ?? 0) => buildMulticamProject(current, recipe(), { ...context, expectedRevision: revision });
  const result = compositionAttempt(() => build(project));
  const sourceSummary = (id: string) => {
    const source = context.sources.find(item => item.id === id);
    if (!source) return "请选择真实源素材；不会按文件名推断同步点。";
    const duration = context.durations?.[id] ?? source.duration;
    return `${source.name} · ${duration === undefined ? "源时长待探测" : `已知源时长 ${duration}s`} · ${source.has_audio === undefined ? "音轨待服务器探测" : source.has_audio ? "目录声明含音轨" : "目录声明无音轨"}`;
  };
  const sourceOptions = (sources: readonly StudioSource[]) => <><option value="">请选择当前任务素材</option>{sources.map(source => <option key={source.id} value={source.id}>{source.name} · {source.id}</option>)}</>;
  return <section data-studio-section="multicam" data-multicam-mode={mode} tabIndex={-1} className="gm-studio-composition" aria-label="手动多机位组合">
    <h3>手动多机位 · 普通轨道 / 片段</h3>
    {!baseAvailable && <p className="gm-studio-warning">当前后端尚未声明 camN / camAlign 的手动有限支持，不能提交组合。此表单不调用多机位专用 API。</p>}
    <p className="gm-studio-note">共同时间线从 0 开始；每个源的“同步入点”由你明确指定，可主动复制当前工程源标记（含未保存标记）。无波形 / 声波分析、自动同步或嵌套；表单只是提案，最终仅保存普通 video / overlay / audio 片段。</p>
    <fieldset disabled={disabled || !baseAvailable}>
      <div className="gm-studio-fields">
        <Field label="多机位输出方式"><select aria-label="多机位输出方式" value={mode} onChange={e => setMode(e.target.value as MulticamRecipe["mode"])}>
          <option value="grid">2×2 网格（普通叠加轨）</option><option value="switch" disabled={!supports("camBatch")}>手动切换清单（硬切）{!supports("camBatch") ? " · 后端未声明" : ""}</option>
          <option value="nested" disabled>嵌套多机位（未支持）</option>
        </select></Field>
        <Field label="机位数量"><select aria-label="机位数量" data-multicam-count value={angles.length} onChange={e => {
          const count = Number(e.target.value);
          setAngles(old => Array.from({ length: count }, (_, index) => old[index] ?? { source_id: "", syncIn: "" }));
          if (masterChoice.startsWith("angle:") && Number(masterChoice.slice(6)) > count) setMasterChoice("");
        }}>
          {[2, 3, 4].map(count => <option key={count} value={count}>{count} 机位</option>)}<option value="9" disabled>9 机位（未支持）</option><option value="16" disabled>16 机位（未支持）</option>
        </select></Field>
        <Field label="共同输出时长（秒）" hint="必须明确输入，大于 0 且 ≤120；不会自动取最长 / 最短源时长">
          <input aria-label="共同输出时长（秒）" data-multicam-duration type="number" inputMode="decimal" min={0} max={120} step="any" value={seconds} onChange={e => setSeconds(e.target.value)} />
        </Field>
        <Field label="多机位写入方式"><select aria-label="多机位写入方式" data-multicam-placement value={placement} onChange={e => setPlacement(e.target.value as MulticamRecipe["placement"])}>
          <option value="append">优先追加新轨（保留现有工程）</option><option value="replace" disabled={project.tracks.some(track => track.locked)}>明确替换 Studio 轨道{project.tracks.some(track => track.locked) ? " · 先解锁全部轨道" : ""}</option>
        </select></Field>
      </div>
      <p className="gm-studio-note">9 / 16 机位、波形 / 声波同步、自动时间码对齐、机位嵌套均未支持。手动切换最多 12 段；总计 ≤16 解码输入 / 8 轨 / 64 片段 / 120 秒，源 EOF 与音视频流仍由主渲染器验证。</p>
      {angles.map((angle, index) => <fieldset key={index} className="gm-studio-multicam-angle" data-multicam-angle={index + 1}>
        <legend>机位 {index + 1}{mode === "grid" ? ` · ${["左上", "右上", "左下", "右下"][index]}` : ""}</legend>
        <div className="gm-studio-fields">
          <Field label={`机位 ${index + 1} 源素材`}><select aria-label={`机位 ${index + 1} 源素材`} value={angle.source_id} onChange={e => changeAngle(index, { source_id: e.target.value, syncIn: "" })}>{sourceOptions(videos)}</select></Field>
          <Field label={`机位 ${index + 1} 同步入点（源秒）`}><input type="number" inputMode="decimal" aria-label={`机位 ${index + 1} 同步入点（源秒）`} min={0} max={3600} step="any" value={angle.syncIn} onChange={e => changeAngle(index, { syncIn: e.target.value })} /></Field>
        </div>
        <p className="gm-studio-note">{sourceSummary(angle.source_id)}{markFor(angle.source_id) && `；源标记 ${markFor(angle.source_id)!.in_point}s → ${markFor(angle.source_id)!.out_point}s。`}</p>
        <div className="gm-studio-row">
          <button type="button" data-multicam-source-preview={index + 1} disabled={!videos.some(source => source.id === angle.source_id)} onClick={() => onPreview(angle.source_id)}>预览机位 {index + 1} 真实源</button>
          <button type="button" disabled={!markFor(angle.source_id)} onClick={() => { const mark = markFor(angle.source_id); if (mark) changeAngle(index, { syncIn: String(mark.in_point) }); }}>使用工程源标记入点</button>
        </div>
      </fieldset>)}
      <Field label="连续主音轨"><select aria-label="连续主音轨" data-multicam-master value={masterChoice} onChange={e => setMasterChoice(e.target.value)}>
        <option value="">明确选择主音频</option>{angles.map((_, index) => <option key={index} value={`angle:${index + 1}`}>机位 {index + 1} 的声音（生成连续独立音轨）</option>)}
        <option value="dedicated">其他目录素材作为主音轨（独立同步入点）</option>
      </select></Field>
      {masterChoice === "dedicated" && <fieldset><legend>独立主音源</legend><div className="gm-studio-fields">
        <Field label="主音轨源素材"><select aria-label="主音轨源素材" value={master.source_id} onChange={e => setMaster({ source_id: e.target.value, syncIn: "" })}>{sourceOptions(context.sources.filter(source => !isImageSource(source) && source.has_audio !== false))}</select></Field>
        <Field label="主音轨同步入点（源秒）"><input aria-label="主音轨同步入点（源秒）" type="number" inputMode="decimal" min={0} max={3600} step="any" value={master.syncIn} onChange={e => setMaster(old => ({ ...old, syncIn: e.target.value }))} /></Field>
      </div><p className="gm-studio-note">{sourceSummary(master.source_id)}</p><div className="gm-studio-row">
        <button type="button" disabled={!context.sources.some(source => source.id === master.source_id)} onClick={() => onPreview(master.source_id)}>预览真实主音源</button>
        <button type="button" disabled={!markFor(master.source_id)} onClick={() => { const mark = markFor(master.source_id); if (mark) setMaster(old => ({ ...old, syncIn: String(mark.in_point) })); }}>使用主音源标记入点</button>
      </div></fieldset>}
      {mode === "switch" ? <Field label="手动切换清单" hint="每行：起始秒数,机位编号；第一段 0 秒，严格递增、不重复且小于共同输出时长；最多 12 段。不自动排序或改点。">
        <textarea aria-label="手动切换清单" data-multicam-cuts rows={5} maxLength={4096} value={cutList} onChange={e => setCutList(e.target.value)} placeholder={"0,1\n3,2\n6,1"} />
      </Field> : <p className="gm-studio-note">网格使用每机位独立 overlay 轨：scale=0.5、x/y=±0.25、fit=cover，从左上逐格放置；2 / 3 机位不拉伸填满空位。生成画面片段全部静音，主声音独立连续。</p>}
      <p className="gm-studio-note">“预览真实源”使用本任务受保护的源 URL，在上方监看播放器播放原文件；各机位未联动、不代表同步或合成效果。源标记只在点击时提供入点，不偷偷改写标记或按其出点截短；实际读取范围见下方提案。共同起点固定 0 秒，保留工作区 {project.workspace.timeline_fps} fps / 时间显示 / 吸附等设置；不改源秒数。</p>
      {placement === "replace" && <p className="gm-studio-warning"><strong>将替换当前 Studio 工程的 {project.tracks.length} 条轨道 / {project.tracks.reduce((sum, track) => sum + track.clips.length, 0)} 个片段。</strong>不修改任何原文件 / 原成片。所有旧轨必须解锁，绑定转场须先显式移除并保存；确认后通过真实工程保存更新，服务器撤销可恢复已保存旧布局。绝不因超额自动改成替换。</p>}
      {result.error && <p className="gm-studio-warning" data-multicam-problem>{result.error}</p>}
      {result.proposal && <>
        <div className="gm-studio-composition-scroll" role="region" tabIndex={0} aria-label="多机位实际源读取提案"><table>
          <thead><tr><th>片段角色 / 源</th><th>时间线范围</th><th>源读取范围</th></tr></thead><tbody>{result.proposal.reads.map(read => <tr key={read.clipId} data-multicam-read={read.clipId}>
            <td>{read.role}<small>{read.sourceId}</small></td><td>{read.start}s → {read.start + read.duration}s</td><td>{read.trim}s → {read.end}s</td>
          </tr>)}</tbody>
        </table></div>
        <CompositionSummary proposal={result.proposal} />
      </>}
      <button type="button" data-multicam-apply disabled={!available || !result.proposal} onClick={() => {
        const next = result.proposal;
        if (next) onCommit(next, build, `${placement === "replace" ? `确认丢弃当前 Studio 轨道布局，并用 ${next.generatedTrackIds.length} 条生成轨道替换？所有原轨必须解锁。` : `确认追加 ${next.generatedTrackIds.length} 条普通轨道？旧画面 / 文字 / 音频保留并按原合成规则叠加 / 混音。`}\n手动${mode === "grid" ? "2×2 网格" : "硬切清单"}，0 → ${recipe().duration} 秒，所有机位画面静音 + 一条连续主音轨。\n未进行波形分析或自动同步；缺少音轨 / 超出 EOF 时实际渲染会失败。`);
      }}>{placement === "replace" ? "确认替换 Studio 轨道并保存" : "确认追加普通轨道并保存"}</button>
    </fieldset>
  </section>;
}

function ClipInspector({ clip, track, sources, disabled, update, supports, colorClipboard, copyColor, pasteColor, fps, timeDisplay, assetState, onUseLut }: {
  clip: StudioClip; track: StudioTrack; sources: StudioSource[]; disabled: boolean;
  fps: TimelineFPS; timeDisplay: WorkspaceMetadata["time_display"];
  update: (patch: Partial<StudioClip>) => void; supports: (id: string) => boolean;
  colorClipboard: unknown; copyColor: () => void; pasteColor: () => void;
  assetState: StudioAssetsState; onUseLut: (id: string | null) => void;
}) {
  const visual = track.type === "video" || track.type === "overlay";
  const media = visual || track.type === "audio";
  const text = track.type === "text";
  const adjustment = track.type === "adjustment";
  const source = sources.find(item => item.id === clip.source_id);
  const still = isImageSourceId(clip.source_id) || (!!source && isImageSource(source));
  const audio = media && !still;
  const number = (key: keyof StudioClip, label: string, min: number, max: number, step = 0.01, hint?: string) =>
    <NumberField key={key} label={label} value={clip[key] as number} min={min} max={max} step={step} hint={hint} onChange={n => update({ [key]: n })} />;
  return <fieldset disabled={disabled} className="gm-studio-inspector-body">
    {track.locked && <p className="gm-studio-warning">轨道已锁定，先解锁再编辑。</p>}
    {track.clips.some(item => item.transition_in && (item.id === clip.id || item.transition_in.left_clip_id === clip.id))
      && <p className="gm-studio-warning">此片段绑定了边界转场。请先在 FX 边界编辑器显式移除并保存，再删除 / 分割 / 移动端点或改变全帧条件。不会自动清除转场、缩短包络或丢弃复制参数。</p>}
    <section data-studio-section="timing" tabIndex={-1}><h3>位置 / 时长 / 源范围</h3>
      <div className="gm-studio-fields"><TimelineField label="起始时间（秒）" value={clip.start} fps={fps} display={timeDisplay} onChange={start => update({ start })} />
        <TimelineField label={still ? "图片显示时长（秒）" : "输出时长（秒）"} value={clip.duration} fps={fps} display={timeDisplay} min={0.001}
          max={still && source ? imageDisplayLimit(source) : clip.freeze ? 10 : 120} onChange={duration => update({ duration })} /></div>
      <p className="gm-studio-note">此处为普通草稿数值编辑，不自动波纹 / 吸附；使用时间线的“修剪模式”表单提交支持的时间线动作，图片不适用源滑移。帧模式按工作区 FPS 输入；仅切换显示不会改写原有非整帧时刻。</p>
      {media && <><Field label="片段源素材"><select aria-label="片段源素材" value={clip.source_id ?? ""} onChange={e => update({ source_id: e.target.value })}>{sources.filter(s => track.type === "audio" ? !isImageSource(s) : !audioSource(s)).map(s => <option key={s.id} value={s.id}>{s.name}{isImageSource(s) ? " · 静态图片" : s.burned_subtitles ? " · 烧录字幕" : ""}</option>)}</select></Field>
        <p className="gm-studio-note">明确换成图片时，源入点归零、速度设为 1×、静音并关闭倒放 / 定格，音频参数恢复默认；保留显示时长、变换、蒙版、关键帧、调色 / LUT 与转场绑定。超出约束仍须手动修正，不自动删除参数。</p>
        {still ? <p className="gm-studio-note" data-image-timing="display-duration">静态图片只按上方显示时长停留：trim=0、speed=1、mute=true、reverse=false、freeze=false。
          图片没有源媒体 EOF、播放时钟或音轨；最大显示时长 {source ? imageDisplayLimit(source) : 120}s 不是文件长度。需要动画时编辑普通变换 / Alpha 关键帧，再保存渲染。</p> : <>
        <div className="gm-studio-fields">{number("trim", "源入点 trim（秒）", 0, 3600)}{supports("speed") && <NumberField label="恒定速度" min={0.5} max={2} step={0.05} value={clip.speed} disabled={clip.freeze} onChange={speed => update({ speed })} hint="音频 atempo 保持音高；定格固定 1×" />}</div>
        {supports("reverse") && <Check label="倒放（同时倒放已有音频）" checked={clip.reverse} disabled={clip.freeze} onChange={reverse => update({ reverse })} />}
        {visual && supports("freeze") && <Check label="显式定格（取 trim 帧，静音，最长 10 秒）" checked={clip.freeze} onChange={freeze => update(freeze ? { freeze, mute: true, speed: 1, reverse: false } : { freeze })} />}
        <p className="gm-studio-note">{clip.freeze ? `固定读取源 ${clip.trim.toFixed(3)}s 的一帧；必须小于源时长。开启时设为静音 / 1× / 不倒放，关闭后静音保留；超 10 秒需手动缩短，关键帧与淡化不被偷偷删除。` : `实际源范围 [${clip.trim.toFixed(3)}, ${(clip.trim + clip.duration * clip.speed).toFixed(3)}]s；不自动补帧。倒放缓冲上限 128 MiB。`}</p></>}</>}
    </section>
    {(visual || text) && <section data-studio-section="transform" tabIndex={-1}><h3>变换 / 普通 Alpha 合成</h3><div className="gm-studio-fields">
      {visual && number("scale", "画面缩放", 0.05, 1)}{number("x", "水平位置 x", -1, 1)}{number("y", "垂直位置 y", -1, 1)}{number("opacity", "不透明度", 0, 1)}
      {visual && <Field label="画面适配"><select aria-label="画面适配" value={clip.fit} onChange={e => update({ fit: e.target.value as StudioClip["fit"] })}><option value="contain">contain · 完整显示（留边）</option><option value="cover">cover · 居中裁切填满</option></select></Field>}
      {visual && supports("rotate") && <Field label="旋转"><select aria-label="旋转" value={clip.rotation} onChange={e => update({ rotation: Number(e.target.value) as StudioClip["rotation"] })}>{[0, 90, 180, 270].map(n => <option key={n} value={n}>{n}°</option>)}</select></Field>}
    </div>{visual && supports("mirror") && <div className="gm-studio-row"><Check label="水平镜像" checked={clip.mirror} onChange={mirror => update({ mirror })} /><Check label="垂直翻转" checked={clip.flip} onChange={flip => update({ flip })} /></div>}
      <p className="gm-studio-note">{text ? "文字坐标为中心归一化位置；字幕 (0,0) 特例为底部居中，普通文字为居中。" : "先裁剪再旋转、contain 适配或 cover 居中裁切。x/y 为相对中心的整幅画布偏移；缩放 ≤1。非空关键帧替代对应静态值；仅普通 Alpha 合成。"}</p>
    </section>}
    {visual && supports("kf") && <section data-studio-section="keyframes" tabIndex={-1}><h3>片段局部关键帧</h3>
      <p className="gm-studio-note">各属性 2–8 点，首点 0 秒、严格递增、不超过输出时长。左侧点控制到下一点的缓动；支持线性、二次缓入 / 缓出与三次平滑缓入缓出（3u² − 2u³）；末点后保持。空列表使用静态值。仅 x / y / scale / opacity，分割前须清除。</p>
      {ANIMATED_PROPERTIES.map(property => <KeyframeRows key={`${clip.id}-${property}`} property={property} points={clip.keyframes[property]} duration={clip.duration} staticValue={clip[property]}
        onChange={points => update({ keyframes: { ...clip.keyframes, [property]: points } })} />)}
    </section>}
    {visual && supports("maskType") && <section data-studio-section="mask" tabIndex={-1}><h3>静态形状蒙版</h3>
      <Field label="蒙版形状"><select aria-label="蒙版形状" value={clip.mask?.type ?? "none"} onChange={e => update({ mask: e.target.value === "none" ? null
        : { x: 0.5, y: 0.5, width: 1, height: 1, feather: 0, invert: false, ...clip.mask, type: e.target.value as "rectangle" | "ellipse" } })}>
        <option value="none">无蒙版</option><option value="rectangle">矩形</option><option value="ellipse">椭圆</option>
      </select></Field>
      {clip.mask && <><div className="gm-studio-fields">{(["x", "y", "width", "height", "feather"] as const).map(key => <NumberField key={key}
        label={`蒙版 ${{ x: "中心 x", y: "中心 y", width: "宽度", height: "高度", feather: "羽化" }[key]}`} value={clip.mask![key]}
        min={key === "width" || key === "height" ? 0.001 : 0} max={key === "feather" ? 0.5 : 1} step={0.01}
        disabled={key === "feather" && !supports("maskFeather")} onChange={value => { if (clip.mask) update({ mask: { ...clip.mask, [key]: value } }); }} />)}</div>
        <Check label="反转蒙版" checked={clip.mask.invert} disabled={!supports("maskInv")} onChange={invert => { if (clip.mask) update({ mask: { ...clip.mask, invert } }); }} />
      </>}
      <p className="gm-studio-note">中心 / 宽高相对于变换后画面（缩放动画为固定画布），在时间线位移前计算。羽化为向内 0–0.5 线性过渡，反转仍保留源 Alpha；无跟踪或贝塞尔。</p>
    </section>}
    {visual && supports("frameCrop") && <section data-studio-section="crop" tabIndex={-1}><h3>矩形裁剪 · 源归一化范围</h3><div className="gm-studio-fields">
      {(["x", "y", "width", "height"] as const).map(key => <NumberField key={key} label={`裁剪 ${key}`} min={key === "x" || key === "y" ? 0 : 0.001} max={1} value={clip.crop[key]} onChange={n => update({ crop: { ...clip.crop, [key]: n } })} />)}
    </div><p className="gm-studio-note">x + width ≤1，y + height ≤1；正宽高。不是形状蒙版。</p></section>}
    {(visual || adjustment) && supports("basicColor") && <section data-studio-section="color" tabIndex={-1}><h3>{adjustment ? "限时调整 · 影响前面媒体轨" : "基础调色 / 纹理"}</h3><div className="gm-studio-fields">
      {number("brightness", "亮度", -1, 1)}{number("contrast", "对比度", 0, 2)}{number("saturation", "饱和度", 0, 3)}
      {visual && number("sharpen", "锐化", 0, 1)}{visual && number("noise", "固定种子时域噪点", 0, 20, 1)}
      {supports("proColor") && <>{number("temperature", "色温（冷 −1 / 暖 +1）", -1, 1)}{number("hue", "色相（度）", -180, 180, 1)}
        {number("shadows", "阴影调整", -1, 1)}{number("highlights", "高光调整", -1, 1)}{number("fade_amount", "褪色量（非时间淡化）", 0, 1)}
        <Field label="调色预设"><select aria-label="调色预设" value={clip.color_preset} onChange={e => update({ color_preset: e.target.value as StudioClip["color_preset"] })}>
          <option value="none">无预设</option><option value="warm">暖色 warm</option><option value="cool">冷色 cool</option><option value="cinema">电影色调 cinema</option><option value="mono">黑白 mono</option>
        </select></Field>
      </>}
    </div>
      {supports("proColor") && <><p className="gm-studio-note">真实本地 FFmpeg SDR 调色顺序：基础 EQ → 预设 → 色温 / 阴影 / 高光 → 色相 → 褪色 → RGB 曲线。预设不是导入 LUT；褪色是抬黑 / 降白，不是时间淡入淡出。源播放器不呈现这些效果，请保存并渲染检查。</p>
        {(["red", "green", "blue"] as RGBChannel[]).map(channel => <RGBCurveRows key={`${clip.id}-${channel}`} channel={channel} points={clip.rgb_curves[channel]}
          onChange={points => { const rgb_curves = { ...clip.rgb_curves }; if (points) rgb_curves[channel] = points; else delete rgb_curves[channel]; update({ rgb_curves }); }} />)}
      </>}
      <div className="gm-studio-row"><button type="button" disabled={!supports("colorCopy")} onClick={copyColor}>复制调色参数</button><button type="button" disabled={!colorClipboard || !supports("colorCopy")} onClick={pasteColor}>粘贴适用调色参数</button></div>
      <p className="gm-studio-note">复制包含全部调色字段、预设、RGB 曲线与真实 LUT ID；LUT 仅能粘贴到此任务目录确认的资产。调整轨不带锐化 / 噪点，其他适用参数均保留。无任意滤镜字符串或 AI 配色。</p>
    </section>}
    {(visual || adjustment) && (supports("lut") || clip.lut_id !== null) && <section data-studio-section="lut-binding" tabIndex={-1}>
      <h3>导入 LUT · 真实资产绑定</h3><StudioLutPicker value={clip.lut_id} state={assetState} available={supports("lut")}
        disabled={disabled} onChange={onUseLut} />
    </section>}
    {audio && <section data-studio-section="audio" tabIndex={-1}><h3>真实音频处理</h3>
      {audio ? <><p className="gm-studio-note">{clip.source_id === "video_only" ? "video_only 没有音轨：增益必须为 1、平衡为 0、效果为无。请添加 narration 音频轨；保留这些控件以便重置换源前的音频参数。" : "服务器会验证音轨；无音频源使用非默认增益 / 平衡 / 滤镜会失败。平衡不是空间声像。"}</p><div className="gm-studio-fields">
        {supports("mixVol") && number("volume", "音量（0–2 倍）", 0, 2)}{supports("mixPan") && number("pan", "立体声平衡（左 −1 / 右 +1）", -1, 1)}
        {supports("fxAudio") && <><Field label="音频效果"><select aria-label="音频效果" value={clip.audio_effect} onChange={e => update({ audio_effect: e.target.value as StudioClip["audio_effect"] })}>{[["none", "无"], ["compressor", "压缩器"], ["limiter", "限制器"], ["denoise", "降噪 afftdn"], ["invert", "相位反转"], ["normalize", "单遍响度归一化 −16 LUFS"], ["reverb", "固定 60/120 ms 回声混响"]].map(([v, label]) => <option value={v} key={v}>{label}</option>)}</select></Field>
          {number("bass_db", "低频 EQ（dB / 100 Hz）", -12, 12, 0.5)}{number("treble_db", "高频 EQ（dB / 3000 Hz）", -12, 12, 0.5)}
        </>}
      </div>{supports("mixMute") && <Check label="片段静音（不隐藏画面）" checked={clip.mute} disabled={clip.freeze} onChange={mute => update({ mute })} />}
        <p className="gm-studio-note">归一化为每片段单遍 loudnorm（−16 LUFS / −1.5 dBTP / LRA 11），不保证混音后精确响度。混响不是卷积，尾音在片段结束处裁掉；EQ 在所选效果之后。无音轨源 EQ 须为 0。</p>
      </> : <p className="gm-studio-note">video_only 是无配音的干净画面，请从素材库添加 narration 音频轨。</p>}
    </section>}
    {(media || text) && <section data-studio-section="fades" tabIndex={-1}><h3>淡入 / 淡出</h3><div className="gm-studio-fields">
      {number("fade_in", "淡入（秒）", 0, clip.duration)}{number("fade_out", "淡出（秒）", 0, clip.duration)}
    </div><p className="gm-studio-note">总长 ≤输出时长。视觉为 Alpha，音频为增益，文字为 ASS 淡化；不是相邻片段交叉溶解。分割前需设为 0。</p></section>}
    {text && <section data-studio-section="text" tabIndex={-1}><h3>文字 / 字幕</h3>
      <Field label="文字内容"><textarea aria-label="文字内容" maxLength={500} rows={4} value={clip.text} onChange={e => update({ text: e.target.value })} /></Field>
      <Check label="作为字幕（可随导出选项隐藏 / 放大）" checked={clip.subtitle} onChange={subtitle => update({ subtitle })} />
      <div className="gm-studio-fields">{number("font_size", "字号（1080 高度基准）", 12, 160, 1)}
        <Field label="文字颜色" hint={clip.text_template !== "custom" ? "保留的自定义颜色；当前模板在渲染时覆盖" : undefined}><input type="color" aria-label="文字颜色" value={`#${clip.color}`} onChange={e => update({ color: e.target.value.slice(1).toUpperCase() })} /></Field>
      </div><p className="gm-studio-note">固定 Noto Sans SC；纯文字（最多 500 字符），不接受 ASS 指令。位置与透明度在变换区，时间偏移在起始时间，淡化在淡入 / 淡出区。</p>
      {supports("textFx") && <><Check label="文字加粗" checked={clip.bold} onChange={bold => update({ bold })} />
        <Check label="半透明文字背景框" checked={clip.background} onChange={background => update({ background })} />
        <div className="gm-studio-fields">{number("outline", "文字描边", 0, 8)}{number("shadow", "文字阴影", 0, 8)}</div>
      </>}
      <div className="gm-studio-fields">
        {(supports("fancy") || supports("subTpl")) && <Field label="文字样式模板"><select aria-label="文字样式模板" value={clip.text_template} onChange={e => update({ text_template: e.target.value as StudioClip["text_template"] })}>
          <option value="custom">custom · 自定义原样式</option><option value="news">news · 白色粗体 / 描边 3 / 阴影 1</option>
          <option value="outline">outline · 白色 / 描边 5</option><option value="gold">gold · 金色粗体 / 描边 2 / 阴影 2</option><option value="note">note · 浅黄 / 半透明背景框</option>
        </select></Field>}
        {supports("textAnim") && <Field label="文字动画"><select aria-label="文字动画" value={clip.text_animation} onChange={e => update({ text_animation: e.target.value as StudioClip["text_animation"] })}>
          <option value="none">none · 无动画</option><option value="typewriter">typewriter · 分阶段打字机</option><option value="fade">fade · 淡入淡出</option><option value="pop">pop · 70% → 100% 弹入</option>
        </select></Field>}
      </div>
      <p className="gm-studio-note" data-text-template={clip.text_template}>命名模板仅覆盖渲染时的颜色、粗体、描边、阴影、背景；不会改写上方保存的自定义值，切回 custom 恢复。字号、位置、透明度与文本不被模板替换；源素材预览不含模板 / 动画，需保存并渲染。</p>
      <p className="gm-studio-note" data-text-animation={clip.text_animation}>动画使用真实 ASS：打字机最多 64 个揭示阶段，长文本可能成组出现，并非 ASR 逐字时间戳。fade 在未设显式淡化时使用每侧 min(0.3 秒, 时长 × 20%)；pop 为开头缩放弹入。SRT 仍是普通文本，不保留样式 / 动画。字幕文档上限 256 KiB，过短动画或超限由渲染器拒绝。分割前须清除文字动画。</p>
    </section>}
    {visual && supports("chroma") && <section data-studio-section="chroma" tabIndex={-1}><h3>色度抠像 · 非 AI</h3>
      <Check label="启用色度键" checked={clip.chroma_color !== null} onChange={enabled => update(enabled ? { chroma_color: "00FF00" } : { chroma_color: null, chroma_similarity: 0.1, chroma_blend: 0 })} />
      <fieldset disabled={clip.chroma_color === null}><div className="gm-studio-fields"><Field label="抠像颜色"><input type="color" aria-label="抠像颜色" value={`#${clip.chroma_color ?? "00FF00"}`} onChange={e => update({ chroma_color: e.target.value.slice(1).toUpperCase() })} /></Field>
        {number("chroma_similarity", "色度相似度", 0.01, 1)}{number("chroma_blend", "色度混合", 0, 1)}
      </div></fieldset><p className="gm-studio-note">关闭时清除色度键并恢复其依赖参数，避免保存无意义的开关状态。</p>
    </section>}
  </fieldset>;
}

function RGBCurveRows({ channel, points, onChange }: {
  channel: RGBChannel; points: CurvePoint[] | undefined; onChange: (points: CurvePoint[] | undefined) => void;
}) {
  const label = { red: "红", green: "绿", blue: "蓝" }[channel];
  const invalid = !!points && (points.length < 2 || points.length > 8 || points[0]?.x !== 0 || points[points.length - 1]?.x !== 1
    || points.some((p, i) => !Number.isFinite(p.x) || !Number.isFinite(p.y) || p.x < 0 || p.x > 1 || p.y < 0 || p.y > 1 || (i > 0 && p.x <= points[i - 1].x)));
  const replace = (index: number, patch: Partial<CurvePoint>) => onChange(points?.map((p, i) => i === index ? { ...p, ...patch } : p));
  const add = () => {
    if (!points) { onChange([{ x: 0, y: 0 }, { x: 1, y: 1 }]); return; }
    if (invalid || points.length >= 8) return;
    let index = 1;
    for (let i = 2; i < points.length; i++) if (points[i].x - points[i - 1].x > points[index].x - points[index - 1].x) index = i;
    const left = points[index - 1], right = points[index], x = (left.x + right.x) / 2;
    if (!(x > left.x && x < right.x)) return;
    onChange([...points.slice(0, index), { x, y: (left.y + right.y) / 2 }, ...points.slice(index)]);
  };
  return <fieldset className="gm-studio-curves" data-rgb-channel={channel}><legend>{label}通道 RGB 曲线 · {points?.length ?? 0} / 8</legend>
    {points && <svg viewBox="0 0 100 100" className="gm-studio-curve-plot" role="img" aria-label={`${label}通道控制点示意（非 FFmpeg 插值或效果预览）`}>
      <path d="M0 100L100 0 M0 50H100 M50 0V100" fill="none" stroke="var(--gm-line)" strokeWidth="1" />
      <polyline fill="none" stroke="var(--gm-olive)" strokeWidth="2" points={points.map(p => `${Math.max(0, Math.min(1, p.x)) * 100},${(1 - Math.max(0, Math.min(1, p.y))) * 100}`).join(" ")} />
      {points.map((p, i) => <circle key={i} cx={Math.max(0, Math.min(1, p.x)) * 100} cy={(1 - Math.max(0, Math.min(1, p.y))) * 100} r="2.5" fill="var(--gm-red)" />)}
    </svg>}
    {points?.map((point, index) => <div className="gm-studio-keyframe-row" key={index}>
      <NumberField label={`${channel} 第 ${index + 1} 点 x`} value={point.x} min={0} max={1} step={0.01} disabled={index === 0 || index === points.length - 1} onChange={x => replace(index, { x })} />
      <NumberField label={`${channel} 第 ${index + 1} 点 y`} value={point.y} min={0} max={1} step={0.01} onChange={y => replace(index, { y })} />
      <button type="button" aria-label={`删除 ${channel} 第 ${index + 1} 点`} disabled={index === 0 || index === points.length - 1 || points.length <= 2} onClick={() => onChange(points.filter((_, i) => i !== index))}>删除控制点</button>
    </div>)}
    {invalid && <p role="alert" className="gm-studio-warning">RGB 曲线须为 2–8 点，x 严格递增，首尾 x 为 0 / 1，x / y 均在 0–1；修正后才能保存。</p>}
    <div className="gm-studio-row"><button type="button" disabled={invalid || (points?.length ?? 0) >= 8} onClick={add}>{points ? `插入 ${label} 通道点` : `启用 ${label} 通道曲线`}</button>
      <button type="button" disabled={!points} onClick={() => onChange(undefined)}>清除 {label} 通道曲线</button></div>
    <p className="gm-studio-note">缺省通道为恒等；y 无需递增，允许反相。图中直线只连接输入点，不是 FFmpeg 三次曲线或像素预览；新增点使用邻点中值，可能改变实际曲线。清除只移除本通道，其他调色不变。</p>
  </fieldset>;
}

function KeyframeRows({ property, points, duration, staticValue, onChange }: {
  property: AnimatedProperty; points: KeyPoint[]; duration: number; staticValue: number; onChange: (points: KeyPoint[]) => void;
}) {
  const min = property === "scale" ? 0.05 : property === "opacity" ? 0 : -1;
  const labels = { x: "水平位置", y: "垂直位置", scale: "缩放", opacity: "透明度" };
  const replace = (index: number, patch: Partial<KeyPoint>) => onChange(points.map((p, i) => i === index ? { ...p, ...patch } : p));
  const invalid = points.length > 0 && (points.length < 2 || points.length > 8 || points[0].time !== 0 || points.some((p, i) =>
    p.time > duration || p.time < 0 || p.value < min || p.value > 1 || (i > 0 && p.time <= points[i - 1].time)));
  const add = () => {
    if (!points.length) {
      onChange([{ time: 0, value: staticValue, easing: "linear" }, { time: duration, value: staticValue, easing: "linear" }]); return;
    }
    // Insert into the largest interval, retaining all existing points and local times.
    let index = 1;
    for (let i = 2; i < points.length; i++) if (points[i].time - points[i - 1].time > points[index].time - points[index - 1].time) index = i;
    const left = points[index - 1], right = points[index];
    const time = (left.time + right.time) / 2;
    if (!(time > left.time && time < right.time)) return;
    const fraction = left.easing === "ease_in" ? 0.25 : left.easing === "ease_out" ? 0.75 : 0.5;
    onChange([...points.slice(0, index), { time, value: left.value + (right.value - left.value) * fraction, easing: left.easing }, ...points.slice(index)]);
  };
  return <fieldset className="gm-studio-keyframes"><legend>{labels[property]} · {points.length} / 8</legend>
    {points.map((point, index) => <div key={index} className="gm-studio-keyframe-row">
      <NumberField label={`${property} 第 ${index + 1} 点时间（局部秒）`} value={point.time} min={0} max={duration} step={0.001} disabled={index === 0} onChange={time => replace(index, { time })} />
      <NumberField label={`${property} 第 ${index + 1} 点值`} value={point.value} min={min} max={1} step={0.01} onChange={value => replace(index, { value })} />
      <Field label={`${property} 第 ${index + 1} 点缓动`}><select aria-label={`${property} 第 ${index + 1} 点缓动`} value={point.easing} onChange={e => replace(index, { easing: e.target.value as KeyPoint["easing"] })}>
        <option value="linear">线性</option><option value="ease_in">二次缓入</option><option value="ease_out">二次缓出</option><option value="ease_in_out">三次平滑缓入缓出</option>
      </select></Field>
      <button type="button" aria-label={`删除 ${property} 第 ${index + 1} 点`} disabled={index === 0 || points.length <= 2} onClick={() => onChange(points.filter((_, i) => i !== index))}>删除点</button>
    </div>)}
    {invalid && <p role="alert" className="gm-studio-warning">首点须为 0，时间严格递增且 ≤{duration}s，值在 {min}–1；修正后才能保存。</p>}
    <div className="gm-studio-row"><button type="button" disabled={points.length >= 8 || duration <= 0 || duration > 120 || !Number.isFinite(duration) || invalid} onClick={add}>{points.length ? "插入关键帧" : "启用两点关键帧"}</button>
      <button type="button" disabled={!points.length} onClick={() => onChange([])}>清除 {property} 动画</button></div>
    <p className="gm-studio-note">插入点保持已有时间和值，在最大区间的缓动中点取值；拆成两个缓动区间可能改变运动曲线，并非无损重采样。</p>
  </fieldset>;
}

// Component-owned styling; prototype palette and geometry, with real controls intact.
const STUDIO_CSS = `
.gm-studio{--studio-dark-control:color-mix(in srgb,var(--gm-paper) 12%,var(--gm-ink));--studio-dark-line:color-mix(in srgb,var(--gm-paper) 44%,var(--gm-ink));background:transparent;color:var(--gm-ink);padding:0;border-radius:18px;font:1rem/1.55 "Noto Sans SC","Microsoft YaHei",sans-serif;max-width:1200px;margin:auto;isolation:isolate;min-width:0;overflow-wrap:anywhere}
.gm-studio *{box-sizing:border-box}
.gm-studio :is(h1,h2,h3,p){margin:0}.gm-studio h1{font-size:1.5rem;font-weight:900;letter-spacing:.03em}.gm-studio h2{font-size:1.125rem;font-weight:800}.gm-studio h3{font-size:.875rem;font-weight:800;margin-bottom:10px}.gm-studio p{margin:7px 0}.gm-studio small{font-size:.75rem;display:block;color:var(--gm-muted);overflow-wrap:anywhere}
.gm-studio :is(button,input,select,textarea){font:inherit}
.gm-studio button,.gm-studio a[download]{border:2px solid var(--gm-line);background:var(--gm-surface);color:var(--gm-ink);border-radius:10px;min-height:36px;padding:6px 12px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:5px;transition:background .15s,border-color .15s}
.gm-studio button:hover:not(:disabled),.gm-studio a[download]:hover{border-color:var(--gm-olive);background:var(--gm-green)}
.gm-studio button:disabled{opacity:1;color:var(--gm-muted);background:var(--gm-cream);cursor:not-allowed}
.gm-studio button.gm-studio-primary:not(:disabled){background:var(--gm-olive);border-color:var(--gm-olive);color:var(--gm-surface)}
.gm-studio button.gm-studio-primary:hover:not(:disabled){background:var(--gm-ink);border-color:var(--gm-ink)}
.gm-studio :focus-visible{outline:3px solid var(--gm-olive);outline-offset:3px}
.gm-studio input:not([type=checkbox]):not([type=range]),.gm-studio select,.gm-studio textarea{width:100%;min-height:38px;border:2px solid var(--gm-line);border-radius:8px;background:var(--gm-surface);color:var(--gm-ink);padding:7px 9px;min-width:0}
.gm-studio input[type=color]{padding:3px;height:40px}.gm-studio input[type=checkbox]{accent-color:var(--gm-olive);width:18px;height:18px;flex:none}.gm-studio input[type=range]{accent-color:var(--gm-gold);max-width:100%;min-width:100px}
.gm-studio :is(input,select,textarea):disabled{background:var(--gm-cream);color:var(--gm-muted)}.gm-studio textarea{resize:vertical}.gm-studio fieldset{border:0;margin:0;padding:0;min-width:0;min-inline-size:0;max-width:100%}.gm-studio legend{font-weight:800;margin:12px 0 8px}.gm-studio details{margin-top:12px}.gm-studio summary{cursor:pointer;font-weight:750}.gm-studio pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.75rem;background:var(--gm-paper);color:var(--gm-ink);padding:12px;border-radius:8px;max-width:100%}.gm-studio ul{padding-left:22px;list-style:disc}
.gm-studio .gm-studio-header,.gm-studio .gm-studio-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.gm-studio .gm-studio-header{gap:12px}.gm-studio .gm-studio-title{flex:1;min-width:180px}.gm-studio .gm-studio-eyebrow{font-size:.625rem;font-weight:800;letter-spacing:.18em;color:var(--gm-olive)}.gm-studio .gm-studio-subtitle,.gm-studio .gm-studio-note{font-size:.75rem;color:var(--gm-muted)}.gm-studio .gm-studio-subtitle{margin:10px 0}
.gm-studio .gm-studio-badge{font-size:.6875rem;font-weight:800;border-radius:99px;display:inline-block;padding:3px 9px;background:var(--gm-green);color:var(--gm-olive);border:1px solid var(--gm-olive);white-space:normal}.gm-studio .gm-studio-gold{background:var(--gm-note);border-color:var(--gm-gold);color:var(--gm-ink)}.gm-studio .gm-studio-muted{background:var(--gm-cream);border-color:var(--gm-line);color:var(--gm-muted)}.gm-studio .gm-studio-status{min-height:24px;color:var(--gm-olive);font-size:.8125rem}
.gm-studio .gm-studio-alert{padding:16px;background:var(--gm-paper);color:var(--gm-ink);border:2px solid var(--gm-red);border-radius:12px;white-space:pre-wrap}.gm-studio .gm-studio-alert button{margin-right:8px}.gm-studio .gm-studio-warning{color:var(--gm-ink);background:var(--gm-note);border-left:3px solid var(--gm-red);padding:9px 12px;font-size:.75rem;border-radius:5px;overflow-wrap:anywhere}
.gm-studio .gm-studio-panel{background:var(--gm-surface);border:2px solid var(--gm-line);border-radius:16px;padding:16px;min-width:0}.gm-studio>section,.gm-studio>.gm-studio-edit-grid{margin-top:16px}.gm-studio .gm-studio-section-title{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:12px}.gm-studio .gm-studio-section-title>span{font-size:.75rem;color:var(--gm-muted)}
.gm-studio .gm-studio-workbench,.gm-studio .gm-studio-edit-grid{display:grid;grid-template-columns:minmax(240px,.8fr) minmax(0,1.8fr);gap:14px}
.gm-studio .gm-studio-field{display:flex;flex-direction:column;gap:4px;min-width:0;margin:7px 0;font-size:.75rem;font-weight:650}.gm-studio .gm-studio-field small{font-weight:400;font-size:.6875rem}.gm-studio .gm-studio-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 12px}.gm-studio .gm-studio-check{display:flex;align-items:center;gap:7px;font-size:.8125rem;margin:9px 0;min-height:28px}
.gm-studio .gm-studio-source-list{max-height:360px;overflow:auto;display:flex;flex-direction:column;gap:8px}.gm-studio button.gm-studio-source{justify-content:flex-start;width:100%;text-align:left;padding:10px;min-width:0;flex-shrink:0}.gm-studio .gm-studio-source.is-selected{border-color:var(--gm-olive);background:var(--gm-green)}.gm-studio .gm-studio-source-icon{display:grid;place-items:center;width:32px;height:32px;flex:none;border-radius:8px;background:var(--gm-ink);color:var(--gm-gold)}.gm-studio .gm-studio-source strong{display:block;font-size:.8125rem;overflow-wrap:anywhere}.gm-studio .gm-studio-source>span:last-child{min-width:0;flex:1}.gm-studio .gm-studio-scroll{max-height:300px;overflow:auto}.gm-studio .gm-studio-shot{border-top:1px solid var(--gm-line);padding:10px 0}
.gm-studio .gm-studio-preview-label{font-weight:750;font-size:.8125rem}.gm-studio .gm-studio-screen{display:grid;place-items:center;aspect-ratio:16/9;background:var(--gm-ink);color:var(--gm-surface);border:2px solid var(--gm-ink);border-radius:16px;overflow:hidden;box-shadow:4px 4px 0 var(--gm-ink)}.gm-studio .gm-studio-screen video,.gm-studio .gm-studio-screen img{width:100%;height:100%;object-fit:contain}.gm-studio .gm-studio-screen audio{width:100%}.gm-studio .gm-studio-screen>p{padding:20px;text-align:center}.gm-studio .gm-studio-insert{margin-top:16px}.gm-studio .gm-studio-insert>.gm-studio-fields{grid-template-columns:repeat(3,minmax(0,1fr))}.gm-studio .gm-studio-preview>.gm-studio-row>.gm-studio-field{flex:1 1 14rem}
.gm-studio .gm-studio-timeline{background:var(--gm-ink);color:var(--gm-paper);border:2px solid var(--gm-ink);border-radius:16px;padding:14px;box-shadow:4px 4px 0 var(--gm-ink)}
.gm-studio .gm-studio-timeline button{background:var(--studio-dark-control);border-color:var(--studio-dark-line);color:var(--gm-surface);font-size:.75rem;min-height:32px}.gm-studio .gm-studio-timeline button:hover:not(:disabled){background:var(--gm-paper);color:var(--gm-ink);border-color:var(--gm-gold)}.gm-studio .gm-studio-timeline button:disabled{color:var(--gm-line);background:var(--gm-ink);border-color:var(--studio-dark-line)}.gm-studio .gm-studio-timeline .gm-studio-note,.gm-studio .gm-studio-timeline small{color:var(--gm-line)}.gm-studio .gm-studio-timeline :focus-visible{outline-color:var(--gm-gold)}
.gm-studio .gm-studio-timeline-heading{margin-bottom:12px;font-size:.75rem;justify-content:space-between}.gm-studio .gm-studio-timeline-heading label{display:flex;align-items:center;gap:10px}.gm-studio .gm-studio-timecode{font:700 1.25rem ui-monospace,monospace;color:var(--gm-gold)}
.gm-studio .gm-studio-timecontrols,.gm-studio .gm-studio-trim-modes,.gm-studio .gm-studio-clipboard{border-top:1px solid var(--studio-dark-line);padding:8px 0;margin:8px 0}.gm-studio .gm-studio-timecontrols>.gm-studio-fields{align-items:end}.gm-studio .gm-studio-snap-readout{border-left:3px solid var(--gm-gold);padding:5px 10px}.gm-studio .gm-studio-snap-readout[data-snap-target]:not([data-snap-target=""]){font-weight:650}
.gm-studio .gm-studio-timeline-scroll{overflow-x:auto;padding:8px 0 16px;margin:10px 0;max-width:100%}.gm-studio .gm-studio-track-row{display:grid;grid-template-columns:176px minmax(0,1fr);gap:8px;margin:5px 0}.gm-studio .gm-studio-track-name{border-radius:5px;padding:3px 8px;position:sticky;left:0;background:var(--gm-ink);z-index:3;display:flex;flex-direction:column;justify-content:center;gap:2px;min-height:48px;font-size:.6875rem}.gm-studio .gm-studio-track-name .gm-studio-row{gap:4px}.gm-studio .gm-studio-track-name .gm-studio-row button{min-height:24px;padding:1px 8px;font-size:.6875rem}.gm-studio .gm-studio-track-name button[aria-pressed=true]{background:var(--gm-gold);color:var(--gm-ink);border-color:var(--gm-gold)}
.gm-studio .gm-studio-timeline button.gm-studio-track-label{padding:0 3px;justify-content:flex-start;text-align:left;min-height:22px;border:0;background:transparent;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.gm-studio .gm-studio-timeline button.gm-studio-track-label[aria-pressed=true]{background:var(--gm-gold);color:var(--gm-ink)}
.gm-studio .gm-studio-lane{position:relative;height:60px;border-radius:6px;background:repeating-linear-gradient(90deg,rgba(255,255,255,.05) 0,rgba(255,255,255,.05) calc(8.333% - 1px),rgba(255,255,255,.15) calc(8.333% - 1px),rgba(255,255,255,.15) 8.333%)}.gm-studio .gm-studio-lane.is-muted{background:var(--studio-dark-control);outline:1px dashed var(--studio-dark-line)}.gm-studio .gm-studio-lane.is-muted .gm-studio-clip{text-decoration:line-through}.gm-studio .gm-studio-empty-lane{font-size:.6875rem;display:block;padding:17px;color:var(--gm-line)}
.gm-studio button.gm-studio-clip{position:absolute;height:35px;min-height:35px;min-width:6px;padding:3px;font-size:.625rem;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left;border-radius:5px;border:2px solid var(--studio-dark-line)}.gm-studio button.gm-studio-clip.is-selected{border-color:var(--gm-surface);box-shadow:0 0 0 2px var(--gm-gold);z-index:2}
.gm-studio .gm-studio-ruler{position:relative;min-height:48px;cursor:crosshair;border-bottom:1px solid var(--studio-dark-line)}.gm-studio .gm-studio-ruler>span{position:absolute;top:5px;font-size:.6875rem;color:var(--gm-line)}.gm-studio .gm-studio-ruler button.gm-studio-marker{position:absolute;top:24px;transform:translateX(-50%);padding:0;min-width:22px;min-height:22px;border:0;background:transparent;color:var(--gm-gold)}.gm-studio .gm-studio-playhead{position:absolute;top:0;bottom:0;width:2px;background:var(--gm-surface);pointer-events:none;z-index:2}.gm-studio .gm-studio-move{margin:12px 0;font-size:.75rem}.gm-studio .gm-studio-move .gm-studio-field{flex:1 1 160px}
.gm-studio .gm-studio-inspector-body{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.gm-studio .gm-studio-inspector-body>section{border:1px solid var(--gm-line);border-radius:10px;padding:12px;background:var(--gm-surface)}.gm-studio .gm-studio-inspector-body>section:first-of-type,.gm-studio .gm-studio-inspector-body>p{grid-column:1/-1}.gm-studio .gm-studio-empty{padding:32px 16px;border:2px dashed var(--gm-line);text-align:center;color:var(--gm-muted)}
.gm-studio .gm-studio-keyframe-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;align-items:end;border-bottom:1px solid var(--gm-line);padding:6px 0}.gm-studio .gm-studio-curves{border-top:1px dashed var(--gm-line);margin:8px 0}.gm-studio .gm-studio-curve-plot{display:block;width:100px;height:100px;max-width:100%;border:1px solid var(--gm-line);background:var(--gm-paper);margin:4px}
.gm-studio .gm-studio-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.gm-studio .gm-studio-tabs button{border-radius:99px;font-size:.75rem;min-height:36px;padding:5px 11px}.gm-studio .gm-studio-tabs button[aria-selected=true]{background:var(--gm-ink);color:var(--gm-surface);border-color:var(--gm-ink)}.gm-studio .gm-studio-tabs button[aria-selected=true] small{color:var(--gm-gold)}
.gm-studio .gm-studio-composition{min-width:0;border:2px solid var(--gm-olive);border-radius:12px;padding:12px;margin:12px 0;background:var(--gm-surface)}.gm-studio .gm-studio-composition-scroll{max-width:100%;overflow-x:auto;margin:10px 0}.gm-studio .gm-studio-composition-scroll table{min-width:30rem;margin:0}.gm-studio .gm-studio-composition .gm-studio-multicam-angle{border-top:1px solid var(--gm-line);padding:4px 0 10px}.gm-studio .gm-studio-composition-summary{margin:10px 0}.gm-studio .gm-studio-composition-summary ul{padding-left:28px}
.gm-studio .gm-studio-capabilities [role=tabpanel]{padding:6px 16px 10px;max-height:34rem;overflow:auto;scroll-padding:12px}.gm-studio .gm-studio-tool{display:grid;grid-template-columns:170px minmax(0,1fr);gap:6px 14px;align-items:start;padding:10px 0;border-bottom:1px solid var(--gm-line)}.gm-studio .gm-studio-tool:last-child{border-bottom:0}.gm-studio .gm-studio-tool p{font-size:.75rem;overflow-wrap:anywhere}.gm-studio .gm-studio-tool strong{font-size:.875rem}.gm-studio .gm-studio-tool small{font:.6875rem ui-monospace,monospace;margin:3px 0 6px}.gm-studio .gm-studio-tool.is-unsupported strong{color:var(--gm-muted)}.gm-studio .gm-studio-tool-controls{min-width:0}.gm-studio .gm-studio-tool-controls button{border-radius:99px;font-size:.75rem;min-height:34px;padding:5px 11px}.gm-studio .gm-studio-tool-controls>.gm-studio-note{font-size:.6875rem}.gm-studio .gm-studio-tool-controls details{margin:4px 0;font-size:.75rem}.gm-studio [hidden]{display:none!important}
.gm-studio .gm-studio-job-list{display:flex;flex-direction:column;gap:12px}.gm-studio .gm-studio-job{border:2px solid var(--gm-line);border-radius:10px;padding:14px;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:10px}.gm-studio .gm-studio-job>details{grid-column:1/-1;margin:0}.gm-studio .gm-studio-job progress{display:block;max-width:100%;height:8px;margin:10px 0;accent-color:var(--gm-olive)}.gm-studio .gm-studio-audit{overflow:auto}.gm-studio table{width:100%;border-collapse:collapse;margin-top:14px;text-align:left;font-size:.75rem}.gm-studio th,.gm-studio td{border-bottom:1px solid var(--gm-line);padding:10px;vertical-align:top}.gm-studio th{background:var(--gm-paper)}.gm-studio td pre{max-width:480px}.gm-studio footer{padding:18px 0 0;overflow-wrap:anywhere}
.gm-studio[data-layout=editing] .gm-studio-workbench,.gm-studio[data-layout=editing] .gm-studio-edit-grid{grid-template-columns:minmax(200px,.5fr) minmax(0,2fr)}
.gm-studio[data-layout=audio] .gm-studio-inspector-body>[data-studio-section=audio],.gm-studio[data-layout=captions] .gm-studio-inspector-body>[data-studio-section=text]{order:-2;grid-column:1/-1;border-color:var(--gm-olive);background:var(--gm-green)}
.gm-studio[data-layout=audio] .gm-studio-inspector-body>[data-studio-section=fades],.gm-studio[data-layout=captions] .gm-studio-inspector-body>[data-studio-section=timing]{order:-1;grid-column:1/-1}
/* Reset intrinsic minima; retain named scroll regions instead of concealing overflowing controls. */
.gm-studio legend,.gm-studio :is(input,select,textarea){max-width:100%}.gm-studio :is(.gm-studio-workbench,.gm-studio-edit-grid,.gm-studio-inspector-body,.gm-studio-fields,.gm-studio-keyframe-row,.gm-studio-tool)>*{min-width:0}.gm-studio .gm-studio-row>*{min-width:0;max-width:100%}
@media(min-width:1200px){.gm-studio [data-studio-section=export]>fieldset>.gm-studio-fields{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:1180px){.gm-studio .gm-studio-inspector-body{grid-template-columns:minmax(0,1fr)}.gm-studio .gm-studio-job{grid-template-columns:repeat(2,minmax(0,1fr))}.gm-studio .gm-studio-job>.gm-studio-row{grid-column:1/-1}.gm-studio .gm-studio-workbench,.gm-studio .gm-studio-edit-grid{grid-template-columns:minmax(210px,.75fr) minmax(0,1.3fr)}}
@media(max-width:1000px){.gm-studio .gm-studio-workbench,.gm-studio .gm-studio-edit-grid,.gm-studio[data-layout=editing] .gm-studio-workbench,.gm-studio[data-layout=editing] .gm-studio-edit-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:760px){.gm-studio{border-radius:14px}.gm-studio .gm-studio-header{gap:8px}.gm-studio .gm-studio-panel{padding:13px}.gm-studio .gm-studio-source-list{max-height:220px}.gm-studio .gm-studio-insert>.gm-studio-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.gm-studio .gm-studio-tool{grid-template-columns:minmax(0,1fr);gap:6px;padding:12px 0}.gm-studio .gm-studio-tool-controls>button{justify-self:start}.gm-studio .gm-studio-tabs{flex-wrap:nowrap;overflow:auto;padding:3px 2px 10px}.gm-studio .gm-studio-tabs button{white-space:nowrap;flex-shrink:0}.gm-studio .gm-studio-job{grid-template-columns:minmax(0,1fr)}.gm-studio .gm-studio-timeline{padding:12px}.gm-studio .gm-studio-audit thead{display:none}.gm-studio .gm-studio-audit table,.gm-studio .gm-studio-audit tbody,.gm-studio .gm-studio-audit tr,.gm-studio .gm-studio-audit td{display:block}.gm-studio .gm-studio-audit tr{border:2px solid var(--gm-line);border-radius:10px;margin:12px 0;overflow:hidden}.gm-studio .gm-studio-audit td{display:grid;grid-template-columns:88px minmax(0,1fr);gap:8px}.gm-studio .gm-studio-audit td:before{content:attr(data-label);font-weight:800}.gm-studio .gm-studio-audit td pre{max-width:100%}.gm-studio button,.gm-studio a[download]{min-height:40px}.gm-studio .gm-studio-ruler button.gm-studio-marker{min-height:22px}}
@media(max-width:480px){.gm-studio .gm-studio-fields,.gm-studio .gm-studio-insert>.gm-studio-fields,.gm-studio .gm-studio-keyframe-row{grid-template-columns:minmax(0,1fr)}}
@media(prefers-reduced-motion:reduce){.gm-studio *{transition:none!important;scroll-behavior:auto!important}}
`;