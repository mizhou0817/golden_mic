import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { CaptionStyle, EditingPacing } from '../types';
import {
  canConfirmCheck, createWorkbenchApi, DEFAULT_EXPORT, diffWorkbenchPreferences, exportSupport,
  hasWorkbenchPreferenceChanges, isQuoteRow, normalizeExport, protectedMedia, readWorkbenchPreferences,
  serverAllowsExport, SIMPLE_EXPORT_FORMATS, unresolvedChecks, unresolvedSignature, validateEditBatch, validateRecording, recordingLimit, rowStart,
  workbenchErrorMessage,
} from '../lib/workbenchApi';
import type {
  EditBatch, ExportJob, ExportOptions, Report, Row,
  QuoteRange, SentenceEdit, WorkbenchCheck, WorkbenchChecks, WorkbenchContext, WorkbenchPreferenceChanges, WorkbenchRequest,
} from '../lib/workbenchApi';
import { MODE_LABELS } from '../lib/productionModes';
import type { QuoteTake, Speaker } from '../lib/productionModes';
import QuoteEditor, { currentQuoteSource, quoteRangeProblem } from './QuoteEditor';
import { pendingWriter, readPending } from '../lib/pendingEdits';
import { goneReason } from '../lib/appApi';
import { qualitySummary } from '../lib/qualitySummary';
import type { PendingDraft as Draft, PendingSentence } from '../lib/pendingEdits';
import Icon from './ui/Icon';
import './ResultWorkbench.css';

export interface ResultWorkbenchProps {
  taskId: string; accessToken?: string; title?: string;
  request: WorkbenchRequest; onProcessing: () => void; onChanged: () => void;
  onDelete: () => void; onNew: () => void; onStudio?: () => void;
  onError: (msg: string) => void; onDuplicate?: () => void;
  onRename?: () => void; isOwner?: boolean; sample?: Report;
  onChecksChange?: (checks: WorkbenchChecks | null) => void;
  onUnavailable?: (error: unknown) => void;
  onMutationStateChange?: (busy: boolean) => void;
}
// Files remain memory-only until an explicit upload yields a validated receipt.
const drafts = new Map<string, Draft>();
const exportJobs = new Map<string, ExportJob>();
/** Optional explicit reset; ordinary task navigation does not require a host call. */
export function clearWorkbenchMemory(): void { drafts.clear(); exportJobs.clear(); }
const emptyDraft = (): Draft => ({ base: null, sentences: {}, deleted: [],
  pendTrim: {}, pendTake: {}, pendToNarration: [], pendSpeakers: {} });
const message = workbenchErrorMessage;

/** Why the last edit did not become a new version; the previous version is always kept. */
export function operationErrorText(code: string): string {
  const keep = '之前的版本没有受影响，待修改内容已保留。';
  if (code.startsWith('needs_change: ')) {
    return `上次修改没做成：${code.slice('needs_change: '.length).trim()} ${keep}按提示调整后再点“应用修改”即可。`;
  }
  if (code === 'operation_timed_out') return `上次修改处理超时，没有生成新版本。${keep}可以直接再点一次“应用修改”重试。`;
  return `上次修改没做成（处理过程中出错），没有生成新版本。${keep}可以直接再点一次“应用修改”重试；多次失败时，试试减少一次修改的句子数量。`;
}
// Translate browser recording failures only; API and file-validation errors keep
// their own actionable messages instead of being mislabeled as permission errors.
function microphoneErrorMessage(failure: unknown): string {
  const name = failure && typeof failure === 'object' && 'name' in failure ? failure.name : undefined;
  const fallback = '也可点击“或上传音频”选择已有音频。';
  switch (name) {
    case 'NotAllowedError': return `麦克风权限未允许或被拒绝。请检查浏览器网站权限及系统隐私设置后重试。${fallback}`;
    case 'NotFoundError': return `未找到可用的麦克风。请连接或启用麦克风，并检查系统输入设备。${fallback}`;
    case 'NotReadableError': return `无法读取麦克风，设备可能被其他应用占用或发生故障。请关闭占用麦克风的应用并检查设备。${fallback}`;
    case 'SecurityError': return `当前页面的安全策略禁止使用麦克风。请使用 HTTPS 或本机安全连接，并检查浏览器安全设置；受管设备请联系管理员。${fallback}`;
    case 'AbortError': return `麦克风启动被中断，请重试。${fallback}`;
    default: return `无法启动麦克风录音。请检查设备、权限及浏览器录音支持后重试。${fallback}`;
  }
}
const activeJob = (job: ExportJob | null) => job?.state === 'queued' || job?.state === 'running';
const time = (value?: number | null) => typeof value === 'number' && Number.isFinite(value)
  ? `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toFixed(2).padStart(5, '0')}` : '时间未提供';
const confidence = (v?: number | null) => typeof v === 'number' && Number.isFinite(v)
  ? `${Math.round(v * 100)}%（模型匹配，非事实核实）` : '未提供置信度';
const audioLabel = (r: Row) => ({ tts: 'AI 配音', sync: '现场原声', recording: '本人录音' }[r.audio_source ?? r.audio_kind]);
const PACING_LABELS: Record<EditingPacing, string> = { slow: '舒缓', normal: '正常', fast: '紧凑' };
const CAPTION_LABELS: Record<CaptionStyle, string> = { news: '新闻标准', big: '大字清晰', none: '不显示旁白字幕' };
const hasEdit = (d: Draft) => !!(Object.keys(d.sentences).length || d.deleted.length || hasWorkbenchPreferenceChanges(d)
  || Object.keys(d.pendTrim ?? {}).length || Object.keys(d.pendTake ?? {}).length || (d.pendToNarration ?? []).length || Object.keys(d.pendSpeakers ?? {}).length);
const hasUnsent = (d: Draft) => d.submitted === undefined && hasEdit(d);
const rowHasEdit = (d: Draft, id: number) => !!(d.sentences[id] || d.pendTrim?.[id] || d.pendTake?.[id] || d.pendToNarration?.includes(id));
function withoutRowEdits(draft: Draft, id: number): Draft {
  const sentences = { ...draft.sentences }, pendTrim = { ...draft.pendTrim }, pendTake = { ...draft.pendTake };
  delete sentences[id]; delete pendTrim[id]; delete pendTake[id];
  return { ...draft, sentences, pendTrim, pendTake, pendToNarration: (draft.pendToNarration ?? []).filter(value => value !== id) };
}
const checkActionLabel = (check: WorkbenchCheck): string => ({ listen: '去听', trim: '去剪短', speaker: '去填', edit: '去改字', replace: '去换', view: '去看' }[check.action]
  ?? (['去听', '去剪短', '去填', '去改字', '去换', '去看'].includes(check.action) ? check.action : '去看'));
const exportStateLabel = (state: ExportJob['state']) => ({ queued: '排队中', running: '制作中', succeeded: '已完成', failed: '未完成', cancelled: '已取消', interrupted: '已中断' }[state]);
const DEV_NOTES = import.meta.env.DEV && import.meta.env.VITE_DEV_NOTES === 'true';

/** Changing task or credentials cancels old requests; drafts remain task-local. */
export function ResultWorkbench(props: ResultWorkbenchProps) {
  return <ResultWorkbenchEditor key={JSON.stringify([props.taskId, props.accessToken ?? null, props.isOwner ?? true, !!props.sample])} {...props} />;
}
export default ResultWorkbench;

function ResultWorkbenchEditor(props: ResultWorkbenchProps) {
  const { taskId, accessToken } = props;
  const owner = props.isOwner !== false && !props.sample;
  const callbacks = useRef(props); callbacks.current = props;
  // Host callbacks are often inline; they must not restart loads/pollers per render.
  const request = useCallback<WorkbenchRequest>((path, init) => callbacks.current.request(path, init), []);
  const api = useMemo(() => createWorkbenchApi(taskId, request), [taskId, request]);
  const root = `/api/tasks/${encodeURIComponent(taskId)}`;
  const [context, setContext] = useState<WorkbenchContext | null>(null);
  const [report, setReport] = useState<Report | null>(props.sample ?? null);
  const [version, setVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(!props.sample);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>(() => owner ? drafts.get(taskId) ?? readPending(taskId) ?? emptyDraft() : emptyDraft());
  const writer = useMemo(() => pendingWriter(taskId), [taskId]);
  const [candidateMore, setCandidateMore] = useState(false);
  const [editText, setEditText] = useState<string | null>(null);
  const [instruction, setInstruction] = useState('');
  const [countdown, setCountdown] = useState<number | null>(null);
  const [replaceFailures, setReplaceFailures] = useState<NonNullable<Report['replace_fails']>>({});
  const draftRef = useRef(draft); draftRef.current = draft;
  const [exportOpen, setExportOpen] = useState(false);
  // Signature of the open checks the user has read and accepted for this export; stale as soon as the list changes.
  const [acceptedOpen, setAcceptedOpen] = useState('');
  const [options, setOptions] = useState<ExportOptions>({ ...DEFAULT_EXPORT });
  const [job, setJob] = useState<ExportJob | null>(() => owner ? exportJobs.get(taskId) ?? null : null);
  const [pollError, setPollError] = useState(false);
  const [pollRetry, setPollRetry] = useState(0);
  const [checks, setChecks] = useState<WorkbenchChecks | null>(null);
  const [checksLoading, setChecksLoading] = useState(!props.sample);
  const [checksError, setChecksError] = useState('');
  const checksSequence = useRef(0);
  const checksController = useRef<AbortController | null>(null);
  const editorPanel = useRef<HTMLElement>(null);
  const storyboardPanel = useRef<HTMLElement>(null);
  const [checkFocus, setCheckFocus] = useState<{ sentence: number; action: string } | null>(null);
  const [recording, setRecording] = useState(false);
  const [micStarting, setMicStarting] = useState(false);
  const [playback, setPlayback] = useState<{
    source: string; position: number; duration: number | null; playing: boolean;
  } | null>(null);
  const mounted = useRef(false);
  const lifecycle = useRef(new AbortController());
  const loadSequence = useRef(0);
  const lock = useRef(false);
  const unavailable = useRef<(() => void) | null>(null);
  const recordingSession = useRef<{ stop: () => void; cancel: () => void } | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const cells = useRef(new Map<number, HTMLButtonElement>());
  const exportDialog = useRef<HTMLDialogElement>(null);
  const rememberExport = useCallback((next: ExportJob) => {
    if (!mounted.current) return;
    // Store the real receipt before a host callback can navigate away.
    exportJobs.set(taskId, next); setJob(next);
  }, [taskId]);

  const updateDraft = useCallback((update: (old: Draft) => Draft) => {
    const next = update(draftRef.current);
    draftRef.current = next;
    // Synchronous write: a host navigation in this same event cannot lose a draft.
    if (hasUnsent(next) || next.submitted !== undefined || next.lastOperation) drafts.set(taskId, next); else drafts.delete(taskId);
    if (owner) writer.schedule(hasEdit(next) || next.submitted !== undefined || next.lastOperation ? next : null);
    setDraft(next);
  }, [taskId, owner, writer]);
  const fail = useCallback((e: unknown) => {
    if (!mounted.current) return;
    const text = message(e); setError(text); callbacks.current.onError(text);
  }, []);
  useEffect(() => {
    mounted.current = true;
    lifecycle.current = new AbortController();
    const relay = callbacks.current.onChecksChange;
    relay?.(null);
    const flush = () => { try { writer.flush(); } catch { if (mounted.current) setNotice('本机保存失败；修改仍在本标签页，请不要关闭页面。'); } };
    const visibility = () => { if (document.hidden) flush(); };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', flush);
    return () => {
      flush(); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('pagehide', flush);
      mounted.current = false; lifecycle.current.abort(); loadSequence.current++;
      checksController.current?.abort(); checksSequence.current++;
      relay?.(null);
      recordingSession.current?.cancel();
    };
  }, [writer]);

  const invalidateChecks = useCallback((relay = callbacks.current.onChecksChange) => {
    checksController.current?.abort(); ++checksSequence.current;
    relay?.(null);
    setChecks(null); setChecksError(''); setChecksLoading(false);
  }, []);
  const readUnavailable = useCallback((failure: unknown, notify: ResultWorkbenchProps['onUnavailable'], relay: ResultWorkbenchProps['onChecksChange']) => {
    // Only decoded access failures, never generic errors or revision conflicts.
    if (!mounted.current || goneReason(failure) === 'network') return;
    setContext(null); setReport(null); invalidateChecks(relay); setError(message(failure));
    // A read must not unmount/abort a concurrent write before its receipt is saved.
    if (lock.current) unavailable.current = () => notify?.(failure);
    else notify?.(failure);
  }, [invalidateChecks]);
  const refreshChecks = useCallback(async (expected: number, relay = callbacks.current.onChecksChange, notify = callbacks.current.onUnavailable) => {
    checksController.current?.abort();
    const controller = new AbortController(), sequence = ++checksSequence.current;
    checksController.current = controller;
    relay?.(null);
    setChecks(null); setChecksLoading(true); setChecksError('');
    try {
      const next = await api.checks(controller.signal);
      if (!mounted.current || controller.signal.aborted || sequence !== checksSequence.current) return;
      if (next.revision !== expected) throw new Error('作品已有新版本，请刷新整条作品后重新核对检查结果。');
      setChecks(next); relay?.(next);
    } catch (e) {
      if (mounted.current && !controller.signal.aborted && sequence === checksSequence.current) {
        setChecksError(message(e)); readUnavailable(e, notify, relay);
      }
    } finally {
      if (mounted.current && sequence === checksSequence.current) setChecksLoading(false);
    }
  }, [api, readUnavailable]);

  const reload = useCallback(async (target: number | null = null) => {
    if (callbacks.current.sample) { setReport(callbacks.current.sample); setLoading(false); setChecksLoading(false); return; }
    const relay = callbacks.current.onChecksChange;
    const notify = callbacks.current.onUnavailable;
    const signal = lifecycle.current.signal;
    const seq = ++loadSequence.current;
    invalidateChecks();
    setLoading(true); setError('');
    try {
      const ctx = await api.context(signal);
      if (!mounted.current || signal.aborted || seq !== loadSequence.current) return;
      setContext(ctx);
      const old = target !== null && target !== ctx.revision;
      if (old) {
        const historical = await api.version(target, lifecycle.current.signal);
        if (!mounted.current || seq !== loadSequence.current) return;
        setReport(historical); setVersion(target);
      } else { setReport(ctx.report); setVersion(null); }
      if (draftRef.current.submitted !== undefined && ['done', 'failed', 'cancelled'].includes(ctx.status)) {
        let complete = true;
        let awaitingCommit = false;
        if (draftRef.current.operationId) {
          const operation = await api.operation(draftRef.current.operationId, draftRef.current.submitted, lifecycle.current.signal, draftRef.current.submittedPlan);
          if (!mounted.current || seq !== loadSequence.current) return;
          setReplaceFailures(operation.failures); complete = operation.complete;
          awaitingCommit = operation.state === 'queued' || operation.state === 'running'
            || operation.state === 'succeeded' && ctx.revision < draftRef.current.submitted && !ctx.last_operation_error;
        }
        if (awaitingCommit) {
          setNotice('修改尚未全部提交为新版本，请等待完成后刷新；待修改内容仍保留。');
        } else if (ctx.status === 'done' && ctx.revision === draftRef.current.submitted && !ctx.last_operation_error && complete) {
          const operationId = draftRef.current.operationId;
          updateDraft(() => ({ ...emptyDraft(), base: ctx.revision,
            ...(operationId ? { lastOperation: { id: operationId, revision: ctx.revision } } : {}) }));
          setNotice('新版本已完成，已清除本次提交的修改。');
        } else {
          updateDraft(d => ({ ...d, submitted: undefined,
            ...(d.operationId && ctx.revision === d.submitted ? { lastOperation: { id: d.operationId, revision: ctx.revision } } : {}) }));
          setNotice('部分修改未完成或版本与预期不一致，草稿已保留。请核对服务器状态，不要直接重复提交。');
        }
      } else if (!old && draftRef.current.lastOperation?.revision === ctx.revision) {
        const last = draftRef.current.lastOperation;
        const operation = await api.operation(last.id, last.revision, lifecycle.current.signal);
        if (!mounted.current || seq !== loadSequence.current) return;
        setReplaceFailures(operation.failures);
      }
      if (ctx.last_operation_error) setError(operationErrorText(ctx.last_operation_error));
      if (!old && ctx.status === 'done') await refreshChecks(ctx.revision, relay, notify);
    } catch (e) {
      if (mounted.current && !signal.aborted && seq === loadSequence.current) {
        // Never leave a previous report editable under a newly loaded revision.
        setContext(null); setReport(null);
        // Loading failures belong to this reloadable panel. Duplicating them in
        // the outer shell leaves a stale error there after a successful retry.
        setError(message(e));
        readUnavailable(e, notify, relay);
      }
    } finally { if (mounted.current && seq === loadSequence.current) setLoading(false); }
  }, [api, updateDraft, invalidateChecks, refreshChecks, readUnavailable]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => {
    const visible = () => {
      if (!document.hidden && !lock.current && !recordingSession.current) void reload();
    };
    document.addEventListener('visibilitychange', visible);
    return () => document.removeEventListener('visibilitychange', visible);
  }, [reload]);

  const revision = context?.revision ?? report?.revision ?? 0;
  const mode = (version === null ? context?.mode : report?.mode) ?? report?.mode ?? context?.mode ?? 'voiceover';
  const historical = version !== null && version !== revision;
  const state = context?.status;
  const processing = state === 'queued' || state === 'running' || draft.submitted !== undefined;
  const exporting = activeJob(job);
  const staleDraft = hasEdit(draft) && draft.base !== revision;
  const disabled = !!busy || loading || recording || micStarting || exporting || processing;
  const editable = owner && !!context && state === 'done' && !historical && !disabled && !staleDraft;
  const dirty = hasUnsent(draft) || recording || micStarting || editText !== null || !!instruction.trim();
  const exportReady = owner && !!context && !historical && state === 'done' && !processing && !disabled && !dirty
    && !checksLoading && !checksError && checks?.revision === revision;
  const canExport = exportReady && serverAllowsExport(checks, revision);
  // Open checks no longer block exporting; they must be shown and explicitly accepted first.
  const openChecks = useMemo(() => unresolvedChecks(checks, revision), [checks, revision]);
  const openSignature = unresolvedSignature(openChecks);
  const canExportAnyway = exportReady && !canExport && openChecks.length > 0;
  const acknowledged = canExportAnyway && acceptedOpen === openSignature;
  // A file exported with open checks stays downloadable while the work is otherwise ready.
  const canDownload = canExport || (exportReady && !!job?.unresolved);
  const rows = report?.rows ?? [];
  const row = rows.find(r => r.sentence_id === selected) ?? rows[0];
  const rowIsQuote = !!row && isQuoteRow(row, mode);
  const speakers = context?.speakers ?? report?.speakers ?? [];
  const jumpcuts = historical ? report?.jumpcuts ?? [] : context?.jumpcuts ?? [];
  // Always read the current server snapshot, never wizard defaults or a historical report.
  const savedPreferences = context ? readWorkbenchPreferences(context.preferences) : null;
  const preferenceDiffs = [
    draft.pacing !== undefined ? `整片语速：${savedPreferences ? PACING_LABELS[savedPreferences.pacing] : '当前值未读取'} → ${PACING_LABELS[draft.pacing]}` : null,
    draft.caption_style !== undefined ? `字幕样式：${savedPreferences ? CAPTION_LABELS[savedPreferences.caption_style] : '当前值未读取'} → ${CAPTION_LABELS[draft.caption_style]}` : null,
    draft.enhance_speech !== undefined ? `原声／录音降噪：${savedPreferences ? savedPreferences.enhance_speech ? '开启' : '关闭' : '当前值未读取'} → ${draft.enhance_speech ? '开启' : '关闭'}` : null,
  ].filter((text): text is string => text !== null);
  const quality = report?.quality;
  const qualityDetails = qualitySummary(report, context, historical);
  const pending = row ? draft.sentences[row.sentence_id] ?? {} : {};
  const timing = row ? ((!historical && context?.timings.find(t => t.sentence_id === row.sentence_id)) || (
    typeof row.start === 'number' && typeof row.end === 'number'
      ? { start: row.start, end: row.end, duration: row.duration, speaking_rate_cpm: null, audio_source: row.audio_source ?? row.audio_kind } : undefined
  )) : undefined;
  const media = (path: string, rev = version ?? revision) => protectedMedia(taskId, path, accessToken, rev);
  const videoPath = historical ? `${root}/versions/${version}/video` : `${root}/video`;

  // Presentation only: never sum sentence durations, seek on a playback event,
  // or change the selected editor while someone is typing or recording.
  const currentTimings = useMemo(() => new Map((historical ? [] : context?.timings ?? [])
    .map(t => [t.sentence_id, t])), [context, historical]);
  const ownVoiceNotice = owner && !historical && context?.preferences.voice === 'mine' && mode !== 'original'
    && rows.some(r => !isQuoteRow(r, mode) && (currentTimings.get(r.sentence_id)?.audio_source ?? r.audio_source ?? r.audio_kind) === 'tts');
  const visiblePlayback = report && playback?.source === videoPath + revision ? playback : null;
  const playbackRow = visiblePlayback ? rows.find(r => {
    const t = currentTimings.get(r.sentence_id) ?? r;
    return typeof t.start === 'number' && Number.isFinite(t.start)
      && typeof t.end === 'number' && Number.isFinite(t.end) && t.end > t.start
      && visiblePlayback.position >= t.start && visiblePlayback.position < t.end;
  }) : undefined;
  const syncPlayback = (player: HTMLVideoElement) => {
    if (!mounted.current || player !== video.current) return;
    if (!player.readyState || !Number.isFinite(player.currentTime)) { setPlayback(null); return; }
    setPlayback({ source: videoPath + revision, position: player.currentTime,
      duration: Number.isFinite(player.duration) && player.duration > 0 ? player.duration : null,
      playing: !player.paused && !player.ended });
  };
  const candidates = (context?.shots ?? []).filter(s => s.selectable && s.unused && s.media_origin === 'source');
  const visibleCandidates = candidateMore ? candidates : candidates.slice(0, 3);
  const replacementFailure = row ? replaceFailures[row.sentence_id] ?? report?.replace_fails?.[row.sentence_id] : undefined;
  const togglePlay = () => {
    const player = video.current;
    if (!player) return;
    if (!player.paused) player.pause();
    else void player.play().catch(() => setNotice('视频暂不可用，请刷新状态。'));
  };

  useEffect(() => {
    if (!dirty && !exporting && !processing) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    let returning = false;
    const pop = (event: PopStateEvent) => {
      if (returning) { returning = false; return; }
      if (!window.confirm('还有待应用修改或正在执行的任务。修改会保留，但未上传的录音关闭标签页后会丢失。仍要离开？')) {
        event.stopImmediatePropagation(); returning = true; window.history.forward();
      }
    };
    window.addEventListener('beforeunload', unload);
    window.addEventListener('popstate', pop, true);
    return () => { window.removeEventListener('beforeunload', unload); window.removeEventListener('popstate', pop, true); };
  }, [dirty, exporting, processing]);

  // Only poll actual active jobs; pause requests while the tab is hidden.
  useEffect(() => {
    if (!exporting || !job || pollError) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const relay = callbacks.current.onChecksChange, sequence = checksSequence.current;
    const notify = callbacks.current.onUnavailable;
    const poll = async () => {
      if (cancelled) return;
      if (document.hidden) { timer = setTimeout(poll, 3000); return; }
      try {
        const next = await api.job(job.export_id ?? job.id, controller.signal, job.pipeline_revision);
        if (cancelled || !mounted.current) return;
        if (!activeJob(next)) {
          if (next.state !== 'succeeded' && sequence === checksSequence.current) relay?.(null);
          rememberExport(next); callbacks.current.onChanged();
          return;
        }
        rememberExport(next);
        timer = setTimeout(poll, 2000);
      } catch (e) { if (!cancelled && mounted.current) { if (sequence === checksSequence.current) relay?.(null); setPollError(true); fail(e); readUnavailable(e, notify, relay); } }
    };
    timer = setTimeout(poll, 1500);
    return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
  }, [api, exporting, job?.id, job?.export_id, pollRetry, pollError, rememberExport, fail, readUnavailable]);

  const run = async (label: string, operation: () => Promise<void>) => {
    if (lock.current || !mounted.current) return;
    ++loadSequence.current;
    const mutationState = callbacks.current.onMutationStateChange;
    lock.current = true; setBusy(label); setError(''); setNotice('');
    mutationState?.(true);
    try { await operation(); }
    catch (e) { fail(e); }
    finally {
      lock.current = false;
      if (mounted.current) setBusy('');
      const notify = unavailable.current; unavailable.current = null;
      if (mounted.current) notify?.();
      mutationState?.(false);
    }
  };
  const patchSentence = (sentenceId: number, patch: Partial<PendingSentence>) => {
    if (!owner) return;
    if (context?.report.rows.some(r => r.sentence_id === sentenceId && isQuoteRow(r, mode))) return;
    updateDraft(d => {
      const sentences = { ...d.sentences }, next = { ...sentences[sentenceId], ...patch };
      const saved = context?.report.rows.find(r => r.sentence_id === sentenceId);
      if (next.text === saved?.sentence) delete next.text;
      for (const key of Object.keys(next) as (keyof PendingSentence)[]) if (next[key] === undefined) delete next[key];
      if (Object.keys(next).length) sentences[sentenceId] = next; else delete sentences[sentenceId];
      return { ...d, base: hasEdit(d) ? d.base ?? revision : revision, sentences };
    });
  };
  const rememberQuote = (id: number, kind: 'trim' | 'take' | 'narration', value?: QuoteRange | QuoteTake | boolean) => {
    if (!editable || !context || draftRef.current.deleted.includes(id)) return;
    const target = context.report.rows.find(r => r.sentence_id === id);
    if (!target || !isQuoteRow(target, mode) || kind === 'narration' && mode !== 'mixed') return;
    if (value && kind === 'trim') {
      const issue = quoteRangeProblem(currentQuoteSource(target), value as QuoteRange);
      if (issue) { fail(new Error(issue)); return; }
    }
    const d = draftRef.current;
    if (value && (d.sentences[id] || kind !== 'trim' && d.pendTrim[id] || kind !== 'take' && d.pendTake[id]
      || kind !== 'narration' && d.pendToNarration.includes(id))) {
      setNotice('这句已有其他待应用修改，请先撤销，再选择剪短、换段或转旁白。'); return;
    }
    updateDraft(current => {
      const next = { ...current, base: hasEdit(current) ? current.base ?? revision : revision,
        pendTrim: { ...current.pendTrim }, pendTake: { ...current.pendTake }, pendToNarration: [...current.pendToNarration] };
      if (kind === 'trim') { if (value) next.pendTrim[id] = value as QuoteRange; else delete next.pendTrim[id]; }
      if (kind === 'take') { if (value) next.pendTake[id] = value as QuoteTake; else delete next.pendTake[id]; }
      if (kind === 'narration') next.pendToNarration = value ? [...new Set([...next.pendToNarration, id])] : next.pendToNarration.filter(n => n !== id);
      return next;
    });
    setNotice(value ? '已记下，点击“应用修改”后才会改动成片。' : '已撤销这项待应用修改。');
  };
  const patchSpeaker = (id: string, patch: Partial<Pick<Speaker, 'name' | 'title'>>) => {
    const person = speakers.find(p => p.id === id);
    if (!editable || !person || mode === 'voiceover') return;
    updateDraft(d => {
      const previous = Object.prototype.hasOwnProperty.call(d.pendSpeakers, id) ? d.pendSpeakers[id] : { name: person.name, title: person.title };
      const next = { ...previous, ...patch }, pendSpeakers = { ...d.pendSpeakers, [id]: next };
      if (next.name === person.name && next.title === person.title) delete pendSpeakers[id];
      return { ...d, base: hasEdit(d) ? d.base ?? revision : revision, pendSpeakers };
    });
  };
  const toggleDeleted = (id: number, remove: boolean) => {
    if (!editable || !rows.some(r => r.sentence_id === id)) return;
    const current = draftRef.current;
    if (remove && !current.deleted.includes(id) && rows.length - current.deleted.length <= 1) { setNotice('至少保留一句。'); return; }
    if (remove && rowHasEdit(current, id) && !window.confirm('这句已有待应用修改。标记删除会撤销这句的修改，继续吗？')) return;
    updateDraft(d => ({ ...(remove ? withoutRowEdits(d, id) : d), base: hasEdit(d) ? d.base ?? revision : revision,
      deleted: remove ? [...new Set([...d.deleted, id])] : d.deleted.filter(value => value !== id) }));
  };
  const patchPreferences = (patch: WorkbenchPreferenceChanges) => {
    if (!editable || !context || mode === 'original' && patch.pacing !== undefined) return;
    updateDraft(d => {
      const changes = diffWorkbenchPreferences(context.preferences, { ...d, ...patch });
      // Returning to a saved value removes that draft key, including false → true.
      return { ...d, base: hasEdit(d) ? d.base ?? revision : revision,
        pacing: changes.pacing, caption_style: changes.caption_style, enhance_speech: changes.enhance_speech };
    });
  };
  const selectRow = (id: number, focus = false) => {
    if (recording || micStarting) return false;
    if (id !== row?.sentence_id && (editText !== null || instruction.trim())) {
      setNotice('请先记下或取消这句的文字／换画面输入，再切换句子。'); return false;
    }
    setSelected(id);
    if (id !== row?.sentence_id) { setCandidateMore(false); setEditText(null); setInstruction(''); }
    if (focus) { cells.current.get(id)?.focus(); cells.current.get(id)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
    if (window.matchMedia('(max-width: 1000px)').matches) editorPanel.current?.scrollIntoView({ block: 'start' });
    const r = rows.find(item => item.sentence_id === id);
    const start = r ? rowStart(r, historical ? [] : context?.timings ?? []) : null;
    if (start !== null && video.current) video.current.currentTime = start;
    return true;
  };
  const stripKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if ((event.key === 'Delete' || event.key === 'Backspace') && !event.altKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault(); toggleDeleted(rows[index].sentence_id, !draftRef.current.deleted.includes(rows[index].sentence_id)); return;
    }
    let next = index;
    if (event.key === 'ArrowLeft') next = Math.max(0, index - 1);
    else if (event.key === 'ArrowRight') next = Math.min(rows.length - 1, index + 1);
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = rows.length - 1;
    else return;
    event.preventDefault(); selectRow(rows[next].sentence_id, true);
  };
  const listenTo = (id: number) => {
    const t = context?.timings.find(item => item.sentence_id === id);
    if (!t || !video.current || historical) { setNotice('没有读到这句的成片位置，请刷新后再试听。'); return; }
    if (!selectRow(id)) return;
    void video.current.play().catch(() => { if (mounted.current) setNotice('请点播放器里的播放按钮试听这句。'); });
  };
  const goToCheck = (check: WorkbenchCheck) => {
    const action = checkActionLabel(check);
    if (check.sentence_id != null && rows.some(r => r.sentence_id === check.sentence_id)) {
      if (!selectRow(check.sentence_id)) return;
      setCheckFocus({ sentence: check.sentence_id, action });
      if (action === '去听') listenTo(check.sentence_id);
    } else {
      const panel = document.getElementById('gm-rw-qc-summary-title');
      panel?.focus({ preventScroll: true }); panel?.scrollIntoView({ block: 'start' });
    }
  };
  useEffect(() => {
    if (!checkFocus || row?.sentence_id !== checkFocus.sentence) return;
    const panel = editorPanel.current;
    const target = checkFocus.action === '去填' ? panel?.querySelector<HTMLElement>('[data-quote-speaker-name]')
      : checkFocus.action === '去剪短' ? panel?.querySelector<HTMLElement>('[data-quote-word]:not(:disabled)')
        : checkFocus.action === '去改字' ? panel?.querySelector<HTMLElement>('textarea') : panel;
    (target ?? panel)?.focus({ preventScroll: true }); panel?.scrollIntoView({ block: 'start' });
    setCheckFocus(null);
  }, [checkFocus, row?.sentence_id]);
  const confirmCheck = (check: WorkbenchCheck, checked: boolean) => {
    if (!owner || !checks || !canConfirmCheck(check) || disabled || dirty || historical || checksLoading || checks.revision !== revision) return;
    const snapshot = checks;
    const relay = callbacks.current.onChecksChange;
    void run('保存核对结果', async () => {
      invalidateChecks(); setChecksLoading(true);
      const sequence = ++checksSequence.current;
      try {
        const next = await api.confirmCheck(snapshot, check.key, checked, lifecycle.current.signal);
        if (!mounted.current || sequence !== checksSequence.current) return;
        setChecks(next); relay?.(next); setNotice(checked ? '已保存这一项核对。' : '已撤销这一项核对。');
      } catch (error) {
        if (mounted.current && sequence === checksSequence.current) setChecksError(message(error));
        throw error;
      } finally { if (mounted.current && sequence === checksSequence.current) setChecksLoading(false); }
    });
  };
  const nextCheck = checks?.checks.find(c => c.level === 0 && !canConfirmCheck(c))
    ?? checks?.checks.find(c => c.level < 2 && !c.checked);
  const gateLabel = props.sample ? '范例作品' : historical ? '历史版本 · 只读' : checksLoading ? '正在核对'
    : !checks || checksError ? '检查未读取' : checks.blocking_count ? `待处理 ${checks.blocking_count} 处`
      : checks.pending_count ? `待确认 ${checks.pending_count} 项` : checks.passed ? '检查通过' : '检查尚未通过';
  const nextAction = processing ? '等待制作完成，再回来检查这条作品'
    : historical ? '正在回看历史版本；恢复为新版本后可以继续编辑'
      : dirty ? '修改已记下；应用修改后再检查和导出'
        : checksLoading || loading ? '正在读取这条作品的发布前检查'
          : checksError || !checks ? '先重新读取检查结果，确认前不能导出'
            : nextCheck ? nextCheck.message : checks.passed ? '检查通过，可以导出作品' : '检查尚未通过，请刷新核对';
  const navigate = (callback: () => void) => {
    if (busy || recording || micStarting || exporting) { setNotice('请先结束当前操作。'); return; }
    if (dirty && !window.confirm('还有待应用修改。修改会保留，但未上传的录音关闭标签页后会丢失。仍要离开？')) return;
    callback();
  };
  const clearAll = () => {
    if (!window.confirm('撤销全部未保存的文字、画面、录音、原声剪短、换段、说话人和制作偏好？已保存的版本不会被撤销。')) return;
    updateDraft(() => emptyDraft());
    setEditText(null); setInstruction('');
    try { writer.flush(); setNotice('已撤销全部未保存内容。'); } catch (e) { fail(e); }
  };
  const submitBatch = () => {
    if (!editable || !context || !hasEdit(draft)) return;
    if (editText !== null || instruction.trim()) { setNotice('请先记下修改，或点“算了”。'); return; }
    void run('提交合并修改', async () => {
      const current = draftRef.current;
      const keep = context.report.rows.filter(r => !current.deleted.includes(r.sentence_id));
      if (!keep.length) throw new Error('至少保留一句。');
      if (current.base !== context.revision) throw new Error('草稿基于旧版本。请保留内容核对后撤销或重新编辑，不能静默覆盖。');
      let length = 0;
      for (const r of keep) {
        const p = current.sentences[r.sentence_id];
        if (isQuoteRow(r, mode) && p) throw new Error('原声句不能改字、录音或换画面；请剪短、换一段，或单独记下转旁白。');
        const text = (p?.text ?? r.sentence).trim(); length += text.length;
        if (!text || text.length > 2000 || /[\u0000-\u001f]/.test(text)) throw new Error('每句须为 1–2000 字且不能包含换行或控制字符。');
        if (p?.instruction !== undefined && (!p.instruction.trim() || p.instruction.trim().length > 500)) throw new Error('换镜指令须为 1–500 字，或清除该修改。');
      }
      if (length > 8000) throw new Error('修改后的配音正文不能超过 8000 字。');
      const edits: SentenceEdit[] = [];
      const used = new Set<number>();
      for (const r of keep) {
        const p = draftRef.current.sentences[r.sentence_id];
        if (!p) continue;
        const edit: SentenceEdit = { sentence_id: r.sentence_id };
        if (p.text !== undefined && p.text.trim() !== r.sentence) edit.text = p.text.trim();
        if (p.shot_id !== undefined) {
          if (used.has(p.shot_id)) throw new Error('两个句子不能选择同一镜头。');
          used.add(p.shot_id); edit.shot_id = p.shot_id;
        } else if (p.instruction !== undefined) edit.instruction = p.instruction.trim();
        if (p.file && !p.recording) throw new Error('请先点击“保存这段录音”；应用修改不会自动上传录音。');
        if (p.recording) {
          if (p.recording.revision !== context.revision || p.recording.sentence_id !== r.sentence_id) throw new Error('录音来自其他版本，请重新录制。');
          edit.recording_id = p.recording.recording_id;
        }
        if (Object.keys(edit).length > 1) edits.push(edit);
      }
      const preferences = diffWorkbenchPreferences(context.preferences, current);
      const quote_trims = Object.entries(current.pendTrim).map(([id, range]) => ({ id: Number(id), ...range }));
      const quote_takes = Object.entries(current.pendTake).map(([id, take]) => ({ id: Number(id), take_id: take.take_id }));
      for (const trim of quote_trims) {
        const target = keep.find(r => r.sentence_id === trim.id);
        const source = target ? currentQuoteSource(target) : null;
        const issue = quoteRangeProblem(source, trim);
        if (issue) throw new Error(issue);
      }
      const batch: EditBatch = { expected_revision: context.revision, keep_sentence_ids: keep.map(r => r.sentence_id), edits, ...preferences,
        ...(quote_trims.length ? { quote_trims } : {}), ...(quote_takes.length ? { quote_takes } : {}),
        ...(current.pendToNarration.length ? { to_narration: [...current.pendToNarration] } : {}),
        ...(Object.keys(current.pendSpeakers).length ? { speakers: speakers.map(person => ({ ...person,
          ...(Object.prototype.hasOwnProperty.call(current.pendSpeakers, person.id) ? current.pendSpeakers[person.id] : {}) })) } : {}) };
      validateEditBatch(batch, mode);
      if (!edits.length && !quote_trims.length && !quote_takes.length && !batch.to_narration?.length && !batch.speakers
        && keep.length === context.report.rows.length && !hasWorkbenchPreferenceChanges(preferences)) throw new Error('没有实际改动，请撤销未变化的编辑。');
      invalidateChecks();
      const receipt = await api.apply(batch, context.report.rows.map(r => r.sentence_id), mode, lifecycle.current.signal);
      if (!mounted.current) return;
      updateDraft(d => ({ ...d, submitted: receipt.revision, operationId: receipt.operation_id, submittedPlan: receipt.plan?.steps }));
      try { writer.flush(); } catch { setNotice('提交已受理，但本机保存失败；请不要关闭本标签页。'); }
      setContext(c => c ? { ...c, status: 'queued' } : c);
      if (mounted.current) { callbacks.current.onChanged(); callbacks.current.onProcessing(); }
    });
  };
  const restore = () => {
    if (!owner || !context || state !== 'done' || !historical || disabled || dirty) return;
    if (!window.confirm(`将版本 ${version} 恢复为一个新版本？服务器会重新检查成片质量，已有历史版本保留。`)) return;
    void run('恢复历史版本', async () => {
      invalidateChecks();
      const receipt = await api.restore(version!, context.revision, lifecycle.current.signal);
      if (!mounted.current) return;
      updateDraft(() => ({ ...emptyDraft(), base: context.revision, submitted: receipt.revision }));
      try { writer.flush(); } catch { setNotice('恢复已受理，但本机保存失败；请不要关闭本标签页。'); }
      setContext(c => c ? { ...c, status: 'queued' } : c);
      if (mounted.current) { callbacks.current.onChanged(); callbacks.current.onProcessing(); }
    });
  };

  const stageRecording = async (sentenceId: number, file: File) => {
    validateRecording(file);
    patchSentence(sentenceId, { file, recording: undefined });
    await run('保存录音', async () => {
      const receipt = await api.upload(sentenceId, revision, file, lifecycle.current.signal);
      if (mounted.current) { patchSentence(sentenceId, { recording: receipt, file: undefined }); setNotice('录音已记下，应用修改后才会替换配音。'); }
    });
  };
  const chooseFile = (file: File | undefined) => {
    if (!file || !row || !editable || rowIsQuote || mode === 'original' || draft.deleted.includes(row.sentence_id)) return;
    try { void stageRecording(row.sentence_id, file).catch(fail); }
    catch (e) { fail(e); }
  };
  const startRecording = async () => {
    if (!editable || !row || rowIsQuote || mode === 'original' || draft.deleted.includes(row.sentence_id) || lock.current || recordingSession.current) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      fail(new Error('此浏览器/连接不支持麦克风录音，请使用 HTTPS 或本机安全连接，也可上传音频。')); return;
    }
    const sentenceId = row.sentence_id;
    // Each request owns its resources. getUserMedia cannot be aborted: a late
    // grant after cancellation must stop its own tracks, not a newer recording.
    let tracks: MediaStream | null = null;
    let rec: MediaRecorder | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let countdownTimer: ReturnType<typeof setTimeout> | undefined;
    let finished = false;
    let bytes = 0;
    const chunks: BlobPart[] = [];
    const isCurrent = () => !finished && mounted.current && recordingSession.current === session;
    const stopTracks = () => {
      clearTimeout(timer); timer = undefined;
      clearTimeout(countdownTimer); countdownTimer = undefined;
      tracks?.getTracks().forEach(track => track.stop()); tracks = null;
    };
    const finish = (failure?: Error) => {
      if (finished) return;
      finished = true;
      if (rec) { rec.ondataavailable = null; rec.onerror = null; rec.onstop = null; }
      try { if (rec && rec.state !== 'inactive') rec.stop(); }
      catch { /* Cancellation must still release tracks if recorder.stop fails. */ }
      finally { stopTracks(); chunks.length = 0; }
      if (recordingSession.current !== session) return;
      recordingSession.current = null; lock.current = false;
      if (mounted.current) {
        setRecording(false); setMicStarting(false); setCountdown(null);
        if (failure) fail(failure);
      }
    };
    const session = {
      cancel: () => finish(),
      stop: () => {
        if (!isCurrent() || !rec) return;
        try { if (rec.state !== 'inactive') rec.stop(); }
        catch (e) { finish(new Error(microphoneErrorMessage(e))); }
        finally { stopTracks(); }
      },
    };
    recordingSession.current = session;
    setMicStarting(true); lock.current = true; setError(''); setNotice('');
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      if (!isCurrent()) { acquired.getTracks().forEach(track => track.stop()); return; }
      tracks = acquired;
      const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => MediaRecorder.isTypeSupported(t));
      const activeRecorder = new MediaRecorder(acquired, mimeType ? { mimeType } : undefined);
      rec = activeRecorder;
      activeRecorder.ondataavailable = event => {
        if (!isCurrent() || !event.data.size) return;
        bytes += event.data.size;
        if (bytes > 20 * 1024 * 1024) {
          finish(new Error('录音超过 20 MiB，已停止。请选择更短的录音或上传音频。')); return;
        }
        chunks.push(event.data);
      };
      activeRecorder.onerror = () => finish(new Error('录音失败，请检查麦克风设备，也可点击“或上传音频”选择已有音频。'));
      activeRecorder.onstop = () => {
        if (!isCurrent()) return;
        let file: File | undefined;
        try {
          const type = activeRecorder.mimeType;
          const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'm4a' : type.includes('webm') ? 'webm' : '';
          if (!ext) throw new Error('浏览器录音容器不受支持，请上传 WebM/Ogg/WAV/MP3/M4A 音频。');
          file = new File(chunks, `sentence-${sentenceId}.${ext}`, { type });
          validateRecording(file);
        }
        catch (e) { file = undefined; fail(e); }
        finally { finish(); }
        if (file?.size && mounted.current) void stageRecording(sentenceId, file).catch(fail);
      };
      const count = (remaining: number) => {
        if (!isCurrent()) return;
        setCountdown(remaining);
        if (remaining > 0) { countdownTimer = setTimeout(() => count(remaining - 1), 1000); return; }
        try {
          activeRecorder.start(250); setCountdown(null); setMicStarting(false); setRecording(true);
          timer = setTimeout(session.stop, recordingLimit(row.duration) * 1000);
        } catch (e) { finish(new Error(microphoneErrorMessage(e))); }
      };
      count(3);
    } catch (e) { if (isCurrent()) finish(new Error(microphoneErrorMessage(e))); }
  };
  const stopRecording = () => recordingSession.current?.stop();

  const openExport = () => {
    if (!canExport && !canExportAnyway) { setNotice('请先应用或撤销待修改内容，并等服务器读完发布前检查，再导出。'); return; }
    setExportOpen(true);
  };
  useEffect(() => {
    if (exportOpen) exportDialog.current?.showModal(); else { exportDialog.current?.close(); setAcceptedOpen(''); }
  }, [exportOpen]);
  const submitExport = () => {
    if (!(canExport || acknowledged) || disabled || dirty) return;
    const acknowledgedAtClick = acceptedOpen;
    const relay = callbacks.current.onChecksChange;
    const notify = callbacks.current.onUnavailable;
    let openAtExport = false;
    void run('准备导出任务', async () => {
      // Refresh the server gate before the single explicit export POST. No fallback.
      invalidateChecks(); setChecksLoading(true);
      const sequence = ++checksSequence.current;
      try {
        const current = await api.checks(lifecycle.current.signal);
        if (!mounted.current || sequence !== checksSequence.current) return;
        setChecks(current); relay?.(current);
        const stillOpen = unresolvedChecks(current, revision);
        if (current.revision !== revision) throw new Error('作品已变化。请先刷新，再重新核对检查。');
        // Exporting past open checks is allowed, but only for exactly the list the user was shown.
        // The acknowledgement must name a non-empty list identical to the one the server reports now.
        if (!serverAllowsExport(current, revision) && (!stillOpen.length || !acknowledgedAtClick || unresolvedSignature(stillOpen) !== acknowledgedAtClick)) {
          throw new Error('未通过的检查在你确认之后发生了变化。请重新查看列表并再次确认。');
        }
        openAtExport = !serverAllowsExport(current, revision);
      } catch (e) {
        if (mounted.current && sequence === checksSequence.current) { relay?.(null); setChecksError(message(e)); readUnavailable(e, notify, relay); }
        throw e;
      } finally { if (mounted.current && sequence === checksSequence.current) setChecksLoading(false); }
      if (!mounted.current) return;
      try {
        const frame = video.current;
        if (options.format === 'png' && (!frame || !frame.readyState || !Number.isFinite(frame.duration))) throw new Error('请先在播放器选择要导出的画面。');
        const captured = options.format === 'png' ? { ...options, frame_time: Math.max(0, Math.min(frame!.currentTime, frame!.duration - 1 / 30)) } : options;
        const next = await api.export(captured, revision, lifecycle.current.signal, openAtExport);
        if (mounted.current) { rememberExport(next); setPollError(false); callbacks.current.onChanged(); }
      } catch (e) {
        if (mounted.current && sequence === checksSequence.current) { invalidateChecks(relay); setChecksError(`${message(e)} 导出没有确认成功，请重新读取检查和任务状态，不要直接重复提交。`); }
        throw e;
      }
    });
  };
  const shotOrigin = (shotId: number | null, declared?: string) =>
    declared ?? (!historical ? context?.shots.find(s => s.shot_id === shotId)?.media_origin : undefined);
  const originLabel = (shotId: number | null, declared?: string) => {
    const origin = shotOrigin(shotId, declared);
    return origin === 'generated' ? 'AI 生成示意画面' : origin === 'source' ? '实拍来源素材' : '来源类型未提供';
  };
  const rowAudioLabel = (r: Row) => audioLabel({ ...r, audio_source: currentTimings.get(r.sentence_id)?.audio_source ?? r.audio_source });
  const rowBadges = (r: Row): { label: string; tone: 'neutral' | 'olive' | 'warning' | 'danger' }[] => {
    const badges: ReturnType<typeof rowBadges> = [{ label: isQuoteRow(r, mode) ? '现场原声' : rowAudioLabel(r), tone: isQuoteRow(r, mode) ? 'olive' : 'neutral' }];
    const origins = r.visual_beats.length ? r.visual_beats.map(b => shotOrigin(b.shot_id, b.media_origin)) : [shotOrigin(r.shot_id, r.media_origin)];
    if (origins.includes('generated')) badges.push({ label: 'AI 生成示意画面', tone: 'warning' });
    if (r.is_fallback) badges.push({ label: '兜底 · 待核查', tone: 'warning' });
    const serverChecks = !historical && checks?.revision === revision ? checks.checks.filter(check => check.sentence_id === r.sentence_id) : null;
    const blocked = serverChecks ? serverChecks.some(check => check.level === 0 && !canConfirmCheck(check))
      : quality?.issues.some(issue => issue.sentence_id === r.sentence_id && issue.severity === 'error');
    const unconfirmed = serverChecks ? serverChecks.some(check => check.level < 2 && !check.checked && canConfirmCheck(check))
      : quality?.issues.some(issue => issue.sentence_id === r.sentence_id && issue.severity === 'warning');
    if (blocked) badges.push({ label: '需要处理', tone: 'danger' });
    if (unconfirmed) badges.push({ label: '需要核对', tone: 'warning' });
    if (r.replacement_instruction) badges.push({ label: '有换镜记录', tone: 'neutral' });
    if (draft.sentences[r.sentence_id]) badges.push({ label: draft.submitted ? '修改已提交' : '待修改', tone: 'olive' });
    if (draft.pendTrim[r.sentence_id]) badges.push({ label: '待剪短', tone: 'olive' });
    if (draft.pendTake[r.sentence_id]) badges.push({ label: '待换段', tone: 'olive' });
    if (draft.pendToNarration.includes(r.sentence_id)) badges.push({ label: '待转旁白', tone: 'warning' });
    if (draft.deleted.includes(r.sentence_id)) badges.push({ label: draft.submitted ? '删除已提交' : '待删除', tone: 'danger' });
    return badges;
  };
  return <section className="gm-rw" aria-label="作品结果工作台" aria-busy={loading || !!busy} tabIndex={-1} onKeyDown={e => {
    if (e.target instanceof HTMLElement && e.target.closest('input,textarea,select,button,a,[contenteditable=true]')) return;
    if (e.altKey || e.ctrlKey || e.metaKey || exportOpen) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (row && ['ArrowLeft', 'ArrowRight', 'Delete', 'Backspace'].includes(e.key)) stripKey(e as unknown as KeyboardEvent<HTMLButtonElement>, rows.indexOf(row));
  }}>
    <div className="gm-rw-screen">
      <header className="gm-rw-header">
        <div className="gm-rw-title">
          <h1>{props.title?.trim() || '新闻作品'}</h1>
          <div className="gm-rw-header-badges">
            <span className="gm-rw-badge" data-tone={canExport ? 'olive' : 'warning'}>{gateLabel}</span>
            <span className="gm-rw-badge">{MODE_LABELS[mode]}</span>
          </div>
          <div className="gm-rw-versions"><span>{props.sample ? '范例作品 · 只读' : owner ? '我的作品' : '只读作品'}</span>
            {(context?.versions ?? []).map(v => <button key={v.revision} aria-pressed={(version ?? revision) === v.revision}
              disabled={disabled || dirty} onClick={() => { setSelected(null); void reload(v.revision); }}>{v.revision === 0 ? '初版' : `版本 ${v.revision}`}</button>)}
            {owner && historical && <button disabled={disabled || dirty || state !== 'done'} onClick={restore}>恢复这个版本</button>}
          </div>
        </div>
        <div className="gm-rw-actions">
          {owner && props.onRename && <button disabled={disabled || historical} onClick={props.onRename}>重命名</button>}
          {owner && props.onDuplicate && <button disabled={disabled || historical} onClick={() => navigate(props.onDuplicate!)}>复制一份</button>}
          {owner && <button className="gm-rw-danger" disabled={disabled} onClick={() => {
            if (window.confirm('永久删除作品及其媒体？此操作不可撤销。')) navigate(props.onDelete);
          }}>删除</button>}
          {owner && <button className="gm-rw-primary" disabled={disabled || historical} onClick={() => setExportOpen(true)}>导出 / 分享</button>}
        </div>
      </header>
      {error && <div className="gm-rw-alert" role="alert">{error}<p>没有自动重复提交。刷新会保留草稿，请核对作品的最新状态后再操作。</p><button disabled={!!busy || loading || recording || micStarting} onClick={() => void reload(version)}>刷新作品状态</button></div>}
      {notice && <div className="gm-rw-notice" role="status">{notice}</div>}
      {ownVoiceNotice && <p className="gm-rw-own-voice" role="status">你选择了自己配音。初版先用 AI 配音；选中旁白句子，点「我来读」逐句替换。尚未录制的句子仍是 AI 配音，不会自动开启麦克风。</p>}
      {(busy || loading) && <p role="status">{busy || '正在读取真实作品与报告…'}</p>}
      <div className="gm-rw-next"><b aria-hidden="true"><Icon name="arrow" size={18} /></b><div><small>现在该做什么</small><strong>{props.sample ? '这是一条范例作品，点故事板看看每句话是怎么做的。' : nextAction}</strong></div>
        {processing && <button disabled={!!busy} onClick={props.onProcessing}>查看处理状态</button>}
        {processing && <button disabled={!!busy || loading} onClick={() => void reload()}>刷新作品状态</button>}
        {!processing && !historical && !dirty && !loading && !checksLoading && (checksError || !checks)
          && <button disabled={disabled || !context} onClick={() => void refreshChecks(revision)}>重新读取检查</button>}
        {!processing && !historical && !dirty && !checksError && nextCheck && <button disabled={disabled || checksLoading} onClick={() => goToCheck(nextCheck)}>{nextCheck.action ? checkActionLabel(nextCheck) : '去核对'}</button>}
        {!processing && dirty && <button disabled={!editable || !hasEdit(draft)} onClick={submitBatch}>应用修改</button>}
        {(canExport || canExportAnyway) && <button onClick={openExport}>{canExport ? '开始导出' : '导出（有未通过的检查）'}</button>}
      </div>

        {/* Responsive grid retains a single instance of every control. */}
      <div className={`gm-rw-preview-grid${row ? ' has-selection' : ''}`}>
        <div className="gm-rw-preview-stack">
          <section className="gm-rw-preview" aria-label="成片预览">
            {report ? <div className="gm-rw-video">
              <video ref={video} key={videoPath + revision} playsInline preload="metadata" aria-label="新闻作品成片预览"
                src={media(videoPath)} poster={!historical ? media(`${root}/poster`) : undefined}
                onLoadStart={() => setPlayback(null)}
                onLoadedMetadata={e => syncPlayback(e.currentTarget)} onDurationChange={e => syncPlayback(e.currentTarget)}
                onPlay={e => syncPlayback(e.currentTarget)} onPause={e => syncPlayback(e.currentTarget)}
                onTimeUpdate={e => syncPlayback(e.currentTarget)} onSeeked={e => syncPlayback(e.currentTarget)}
                onEnded={e => syncPlayback(e.currentTarget)} onEmptied={() => setPlayback(null)}
                onError={() => { setPlayback(null); setNotice('视频暂不可用或权限已变化，请刷新状态；未替换为演示视频。'); }} />
              {(props.sample || !historical && !serverAllowsExport(checks, revision)) && <div className="gm-rw-watermark"><span>样片 · {gateLabel}</span></div>}
              <button className="gm-rw-play" aria-label={visiblePlayback?.playing ? '暂停' : '播放'} onClick={togglePlay}>{visiblePlayback?.playing ? 'Ⅱ' : '▶'}</button>
              <div className="gm-rw-controls"><span>{time(visiblePlayback?.position ?? 0)} / {time(visiblePlayback?.duration)}</span>
                <input type="range" aria-label="进度" aria-valuetext={time(visiblePlayback?.position ?? 0)} min={0} max={visiblePlayback?.duration ?? 0} step={0.01}
                  disabled={!visiblePlayback?.duration} value={visiblePlayback?.position ?? 0} onChange={e => { if (video.current) video.current.currentTime = Number(e.target.value); }} />
              </div>
            </div> : <div className="gm-rw-preview-empty">{loading ? '正在载入预览…' : '当前无可用预览，请检查作品状态与权限。'}</div>}
          </section>

          <section ref={storyboardPanel} className="gm-rw-card gm-rw-storyboard">
            <div className="gm-rw-section-head"><h2>故事板</h2><span>一格一句话，宽=时长 · 点一格就能改它 · 第 <b>{row ? rows.indexOf(row) + 1 : 0}</b> / {rows.length} 句</span></div>
            <div className="gm-rw-overview" aria-hidden="true">{rows.map(r => <span key={r.sentence_id} style={{ flex: Math.max(.1, r.duration) }} className={r.sentence_id === row?.sentence_id ? 'is-selected' : ''} />)}</div>
            {!rows.length && <p>暂无可用句子报告，未生成示例内容。</p>}
            <div className="gm-rw-strip" role="group" aria-label="句子选择">
              {rows.map((r, index) => <Fragment key={r.sentence_id}>
                {index > 0 && (r.jumpcut_before || jumpcuts.some(cut => cut.after_row === rows[index - 1].sentence_id)) && <span className="gm-rw-jumpcut" role="img" aria-label={(() => {
                  const cut = jumpcuts.find(c => c.after_row === rows[index - 1].sentence_id);
                  return cut ? `跳切：${({ broll: '空镜遮盖', zoom: '轻微推近', hard: '直接硬切' } as const)[cut.cover]}${cut.downgraded ? '，没有可用空镜，已改用轻微推近' : ''}` : '此处有跳切，未提供遮盖方式';
                })()} />}
                <button ref={el => { if (el) cells.current.set(r.sentence_id, el); else cells.current.delete(r.sentence_id); }}
                className={`gm-rw-cell ${isQuoteRow(r, mode) ? 'is-quote' : ''} ${row?.sentence_id === r.sentence_id ? 'is-selected' : ''} ${draft.deleted.includes(r.sentence_id) ? 'is-deleted' : ''} ${r.is_fallback ? 'is-fallback' : ''} ${visiblePlayback?.playing && playbackRow?.sentence_id === r.sentence_id ? 'is-playing' : ''}`}
                style={{ flexGrow: Math.max(r.duration, 0.1), flexBasis: 0 }}
                aria-pressed={row?.sentence_id === r.sentence_id} tabIndex={row?.sentence_id === r.sentence_id ? 0 : -1}
                onKeyDown={e => stripKey(e, index)} onClick={() => selectRow(r.sentence_id)}>
                <span className="gm-rw-cell-media">
                  {r.shot_id !== null ? <img loading="lazy" alt="" src={media(`${root}/thumbs/${r.shot_id}.jpg`)} /> : <span className="gm-rw-thumb-placeholder">无镜头缩略图</span>}
                  {visiblePlayback?.playing && playbackRow?.sentence_id === r.sentence_id && <span className="gm-rw-cell-playing">播放中</span>}
                </span>
                <span className="gm-rw-cell-number">{index + 1}</span>
                <span className="gm-rw-cell-text">{r.sentence}</span>
                {(r.is_fallback || isQuoteRow(r, mode) || r.audio_source === 'recording' || r.media_origin === 'generated'
                  || rowBadges(r).some(b => b.tone === 'warning' || b.tone === 'danger')) && <span className="gm-rw-cell-dot" data-tone={r.is_fallback ? 'danger' : isQuoteRow(r, mode) || r.audio_source === 'recording' ? 'voice' : r.media_origin === 'generated' ? 'generated' : 'uncertain'} aria-label={rowBadges(r).map(b => b.label).join(' · ')} />}
                {(rowHasEdit(draft, r.sentence_id) || draft.deleted.includes(r.sentence_id)) && <span className="gm-rw-cell-tag">{draft.deleted.includes(r.sentence_id) ? '删' : draft.pendTrim[r.sentence_id] ? '剪短' : draft.pendTake[r.sentence_id] ? '换段' : draft.pendToNarration.includes(r.sentence_id) ? '转旁白' : '待改'}</span>}
              </button></Fragment>)}
            </div>
          </section>
        </div>

        <aside className="gm-rw-card gm-rw-qc-summary" aria-labelledby="gm-rw-qc-summary-title">
          <div className="gm-rw-section-head"><h2 id="gm-rw-qc-summary-title" tabIndex={-1}>发布前检查</h2><span>{gateLabel}</span></div>
          {historical ? <p>历史版本只供回看，不能用当前版本的核对结果替它放行。</p> : props.sample ? <p>范例仅供查看，不代替当前作品的发布前检查。</p> : <>
            {checksLoading && <p role="status">正在读取检查结果…</p>}
            {checksError && <p className="gm-rw-alert" role="alert">{checksError}</p>}
            {!checks && !checksLoading && !checksError && <p>还没有读取检查结果，暂时不能导出。</p>}
            {checks && <><p className="gm-rw-check-summary">{checks.checks.filter(c => c.checked || c.level === 2).length} / {checks.checks.length} 完成</p>
              <progress max={Math.max(1, checks.checks.length)} value={checks.checks.filter(c => c.checked || c.level === 2).length} aria-label="发布前检查完成进度" />
              <div className="gm-rw-check-list">{checks.checks.map(check => <div key={check.key} className={`gm-rw-check-item ${check.level === 0 && !canConfirmCheck(check) ? 'is-blocking' : ''}`}>
                {canConfirmCheck(check) ? <label className="gm-rw-confirm"><input type="checkbox" checked={check.checked}
                  disabled={!owner || disabled || dirty || checksLoading || checks.revision !== revision} onChange={e => confirmCheck(check, e.target.checked)} />
                  <span>{check.message}</span></label> : <div className="gm-rw-check-readonly"><b aria-hidden="true">{check.level === 0 ? '!' : '·'}</b><span>{check.message}</span></div>}
                {(check.action || check.sentence_id !== null) && <button type="button" disabled={loading || recording || micStarting || !!busy} onClick={() => goToCheck(check)}>{checkActionLabel(check)}</button>}
                {DEV_NOTES && <small>{check.code}</small>}
              </div>)}</div>
            </>}
            {(!checks || checksError) && <button type="button" disabled={disabled || checksLoading || !context} onClick={() => void refreshChecks(revision)}>重新读取检查</button>}
            {dirty && <p className="gm-rw-muted">请先应用或撤销待修改内容，再保存核对结果。</p>}
          </>}
          <details className="gm-rw-metrics">
            <summary>质量详情</summary>
            <p className="gm-rw-muted">{qualityDetails.scope}</p>
            <dl>{qualityDetails.items.map(item => <Fragment key={item.key}><dt>{item.label}</dt><dd>{item.value}</dd></Fragment>)}</dl>
            <p>{qualityDetails.rate}</p>
            <p className="gm-rw-muted">以上为当前所看报告及已保存配置，不含待应用修改；不代表实测语速或发布前检查通过。</p>
          </details>
          <div className="gm-rw-metrics">
            {owner && !historical && savedPreferences && mode !== 'original' && <label>播音速度<select disabled={!editable} value={draft.pacing ?? savedPreferences.pacing} onChange={e => patchPreferences({ pacing: e.target.value as EditingPacing })}>
              <option value="slow">慢一点</option><option value="normal">正常</option><option value="fast">快一点</option></select></label>}
          </div>
        </aside>

        {row && <section ref={editorPanel} tabIndex={-1} className="gm-rw-card gm-rw-selected" aria-label="选中句子详情">
          <div className="gm-rw-selected-head">
            <h2>第 {rows.indexOf(row) + 1} 句 <small>/ {rows.length}</small></h2>
            <div className="gm-rw-editor-nav"><button type="button" aria-label="上一句" disabled={rows.indexOf(row) === 0 || recording || micStarting} onClick={() => selectRow(rows[rows.indexOf(row) - 1].sentence_id)}>←</button>
              <button type="button" aria-label="下一句" disabled={rows.indexOf(row) === rows.length - 1 || recording || micStarting} onClick={() => selectRow(rows[rows.indexOf(row) + 1].sentence_id)}>→</button></div>
            <div className="gm-rw-header-badges">
              {rowBadges(row).map(b => <span className="gm-rw-badge" data-tone={b.tone} key={b.label}>{b.label === '兜底 · 待核查' ? '兜底画面，不代表匹配通过' : b.label}</span>)}
            </div>
          </div>
          <div className="gm-rw-editor-grid">
            <div className="gm-rw-sentence-summary">
              {editText === null ? <p className="gm-rw-script">{pending.text ?? row.sentence}</p> : <><textarea aria-label="改这句话" maxLength={2000} value={editText} onChange={e => setEditText(e.target.value)} />
                <div className="gm-rw-actions"><button disabled={!editable || !editText.trim()} onClick={() => { patchSentence(row.sentence_id, { text: editText }); setEditText(null); }}>记下修改</button><button onClick={() => setEditText(null)}>算了</button></div></>}
              {DEV_NOTES && <div className="gm-rw-properties">
                <p className="gm-rw-muted">{timing ? `${time(timing.start)} – ${time(timing.end)} · 实测 ${timing.duration.toFixed(3)} 秒` : '未提供精确起止时间；未累加句时长伪造切点。'}</p>
                {mode !== 'original' && !rowIsQuote && timing?.speaking_rate_cpm != null && timing.audio_source === 'tts' && <p className="gm-rw-muted">实测语速 {timing.speaking_rate_cpm.toFixed(1)} 字/分钟</p>}
                <p className="gm-rw-muted">匹配置信度：{confidence(row.confidence)}</p>
              </div>}
              <p className="gm-rw-muted">{rowIsQuote ? '只能剪短，不能改字' : `${row.duration.toFixed(1)} 秒 · ${rowAudioLabel(row)}`}</p>
              {replacementFailure && <div role="alert" className="gm-rw-alert"><b>上次换画面没成功：</b>{replacementFailure.kind === 'used' ? '合适的镜头已经用过了——删掉这句，或回去多传素材。' : replacementFailure.kind === 'abstract' ? '这句太抽象，AI 找不到能直接表现它的画面。换成具体的人、物或动作。' : replacementFailure.kind === 'processing_failed' ? '换画面时处理出错了，原来的画面保持不变。可以再记一次换画面并点“应用修改”重试。' : '素材里没找到合适画面——换个说法，或回去多传素材。'}</div>}
              {timing?.audio_source === 'recording' && <p className="gm-rw-warning">{'transcript_verified' in timing && timing.transcript_verified === true ? '此句录音已通过转写稿件核对（不代表新闻事实核实）' : 'transcript_verified' in timing && timing.transcript_verified === false ? '此句录音未经转写核验，请试听并核对稿件。' : '此版本未提供录音转写核验状态，请试听核对。'}</p>}
              {row.replacement_instruction && <p className="gm-rw-muted">上次换镜指令：{row.replacement_instruction}</p>}
            </div>
            <div className="gm-rw-beats">
              {(row.visual_beats.length ? row.visual_beats : [{ beat_id: -1, shot_id: row.shot_id, text: row.sentence, description: row.description, confidence: row.confidence, media_origin: row.media_origin }]).map(beat => <figure key={beat.beat_id}>
                {beat.shot_id !== null ? <img loading="lazy" src={media(`${root}/thumbs/${beat.shot_id}.jpg`)} alt={beat.description || '选中句子的实际镜头'} /> : <div className="gm-rw-thumb-placeholder">无镜头缩略图</div>}
                <figcaption><b>{originLabel(beat.shot_id, beat.media_origin)}</b><p>{beat.text}</p><p>{beat.description || '未提供画面描述'}</p>{DEV_NOTES && <small>镜头 {beat.shot_id ?? '未提供'} · {confidence(beat.confidence)}</small>}</figcaption>
              </figure>)}
            </div>
            <div className="gm-rw-sentence-fields">
              {owner && !historical && context && rowIsQuote && <QuoteEditor key={`${revision}:${row.sentence_id}`} row={row} revision={revision} mode={mode} api={api}
                disabled={!editable || draft.deleted.includes(row.sentence_id)} pendingTrim={draft.pendTrim[row.sentence_id]} pendingTake={draft.pendTake[row.sentence_id]}
                toNarration={draft.pendToNarration.includes(row.sentence_id)} speakers={speakers} pendingSpeakers={draft.pendSpeakers}
                onTrim={range => rememberQuote(row.sentence_id, 'trim', range)} onTake={take => rememberQuote(row.sentence_id, 'take', take)}
                onToNarration={selected => rememberQuote(row.sentence_id, 'narration', selected)} onSpeaker={patchSpeaker} onListen={() => listenTo(row.sentence_id)} />}
              {owner && !historical && context && !rowIsQuote && mode !== 'original' && <fieldset disabled={!editable || draft.deleted.includes(row.sentence_id)}>
                <div className="gm-rw-edit-group">
                  <h3>① 改这句话</h3>
                  <div className="gm-rw-actions"><button type="button" onClick={() => setEditText(pending.text ?? row.sentence)}><Icon name="pencil" size={15} /> 改字</button>
                    <button type="button" onClick={() => void startRecording()}>我来读</button></div>
                  {editable && !draft.deleted.includes(row.sentence_id) && <label className="gm-rw-file">或上传音频<input type="file" accept=".webm,.ogg,.wav,.mp3,.m4a" onChange={e => { chooseFile(e.target.files?.[0]); e.target.value = ''; }} /></label>}
                  {(pending.file || pending.recording) && <div className="gm-rw-recording-file">{pending.recording ? '已记下录音' : '录音尚未上传，关闭标签页后会丢失'}
                    {pending.file && !pending.recording && <button type="button" onClick={() => void stageRecording(row.sentence_id, pending.file!).catch(fail)}>保存这段录音</button>}
                    <button type="button" onClick={() => patchSentence(row.sentence_id, { file: undefined, recording: undefined })}>移除录音</button></div>}
                </div>
                <div className="gm-rw-edit-group">
                  <h3>② 换个画面 <small>· 只改画面，旁白字幕不变 · 还有 {candidates.length} 个没用过的镜头</small></h3>
                  <div className="gm-rw-replace"><input aria-label="想要什么画面" maxLength={500} placeholder="想要什么画面？例如：人多热闹 / 汽车" value={instruction} onChange={e => setInstruction(e.target.value)} />
                    <button type="button" className="gm-rw-primary" disabled={!instruction.trim()} onClick={() => { patchSentence(row.sentence_id, { instruction: instruction.trim(), shot_id: undefined }); setInstruction(''); }}>记下换画面</button></div>
                  <p className="gm-rw-muted">也可以直接选一个：</p>
                  <div className="gm-rw-candidates">{visibleCandidates.map(s => <button key={s.shot_id} type="button" aria-pressed={pending.instruction === s.description}
                    disabled={!s.description}
                    onClick={() => patchSentence(row.sentence_id, { shot_id: undefined, instruction: pending.instruction === s.description ? undefined : s.description ?? undefined })}>
                    <img src={media(`${root}/thumbs/${s.shot_id}.jpg`)} alt="" /><span>{s.description || '未提供画面描述'}</span></button>)}
                    {candidates.length > 3 && <button type="button" aria-expanded={candidateMore} onClick={() => setCandidateMore(v => !v)}>{candidateMore ? '收起 ▴' : `还有 ${candidates.length - 3} 个镜头 ▾`}</button>}
                  </div>
                  {!candidates.length && <p className="gm-rw-danger">没用过的镜头都用完了——删掉这句，或回去多传素材。</p>}
                  {(pending.instruction || pending.shot_id !== undefined) && <p className="gm-rw-muted">已记下换画面，应用后才会改动成片。</p>}
                </div>
              </fieldset>}
              {(recording || micStarting) && <div className="gm-rw-recording" role="status">{countdown !== null ? `准备录音 · ${countdown}` : micStarting ? '等待麦克风权限…' : `正在录音，最长 ${recordingLimit(row.duration).toFixed(1)} 秒`}
                {recording && <button type="button" onClick={stopRecording}>停止并保留录音</button>}
                <button type="button" onClick={() => { recordingSession.current?.cancel(); setNotice('已取消本次录音，未保存本次录音内容。原有待修改内容保留，可点击“或上传音频”继续。'); }}>{countdown !== null ? '取消准备录音' : micStarting ? '取消等待麦克风' : '取消并丢弃录音'}</button>
              </div>}
            </div>
            <aside className="gm-rw-sentence-tools">
              {owner && !historical && context && <>
                <div className="gm-rw-delete-group">
                  <h3>③ 不要这句 <small>· 其他句子照原样，至少留一句</small></h3>
                  <label className="gm-rw-check"><input type="checkbox" checked={draft.deleted.includes(row.sentence_id)}
                    disabled={!editable || (!draft.deleted.includes(row.sentence_id) && rows.length - draft.deleted.length <= 1)}
                    onChange={e => toggleDeleted(row.sentence_id, e.target.checked)} />删掉第 {rows.indexOf(row) + 1} 句</label>
                  {/\d|主办|先生|女士|主任|书记|公司|协会/.test(row.sentence) && <p className="gm-rw-danger">这句有数字、人名或称谓，删掉后新闻会缺信息。</p>}
                </div>
                <div className="gm-rw-sentence-revert">
                  <button disabled={!editable || !rowHasEdit(draft, row.sentence_id)} onClick={() => updateDraft(d => withoutRowEdits(d, row.sentence_id))}>撤销这句的待应用修改</button>
                </div>
              </>}
            </aside>
          </div>
        </section>}

      </div>

      {(hasEdit(draft) || dirty || draft.submitted) && <div className="gm-rw-pending" role="region" aria-label="待保存修改">
        <div><b>{draft.submitted ? `已提交，等待版本 ${draft.submitted}` : '修改已记下，尚未应用'}</b><p>{Object.keys(draft.sentences).length} 句编辑 · {draft.deleted.length} 句删除
          {Object.keys(draft.pendTrim).length > 0 ? ` · 剪短 ${Object.keys(draft.pendTrim).length} 句` : ''}
          {Object.keys(draft.pendTake).length > 0 ? ` · 换 ${Object.keys(draft.pendTake).length} 段原声` : ''}
          {draft.pendToNarration.length > 0 ? ` · ${draft.pendToNarration.length} 句转旁白` : ''}
          {Object.keys(draft.pendSpeakers).length > 0 ? ` · 修改 ${Object.keys(draft.pendSpeakers).length} 位说话人` : ''}
          {preferenceDiffs.length > 0 ? ` · ${preferenceDiffs.length} 项整片偏好调整` : ''}</p>
          {preferenceDiffs.length > 0 && <div role="group" aria-label="整片偏好修改差异"><p>当前已保存 → {draft.submitted ? '本次提交' : '草稿目标'}</p>
            <ul className="gm-rw-preference-diff">{preferenceDiffs.map(text => <li key={text}>{text}</li>)}</ul>
          </div>}
          <small>全部修改一次应用，生成新版本后重新检查；原声不会被改写或替成录音。</small>
          {staleDraft && <p>草稿基于 v{draft.base}，当前为 v{revision}。已保留草稿，禁止自动覆盖。</p>}
        </div>
        <button disabled={!!busy || recording || micStarting || processing || exporting} onClick={clearAll}>全部撤销</button>
        <button className="gm-rw-gold" disabled={!editable || !hasEdit(draft)} onClick={submitBatch}>应用修改 → 生成新版本</button>
      </div>}

      {owner && <dialog ref={exportDialog} className="gm-rw-export" aria-labelledby="gm-rw-export-title" onCancel={() => setExportOpen(false)}>
        <div className="gm-rw-section-head"><h2 id="gm-rw-export-title">导出 / 分享</h2><button onClick={() => setExportOpen(false)}>关闭</button></div>
        <p>生成独立文件，不覆盖成片。待修改内容需要先应用或撤销；发布前检查没通过时仍可导出，但会先告诉你哪些没通过，由你确认。</p>
        <p className="gm-rw-muted">竖屏与正方形会居中裁切，请留意人物是否完整。标准字幕保留原成片；改变字幕样式后的包装效果请检查导出文件。</p>
        <fieldset disabled={!!busy || exporting || loading || checksLoading}><legend>导出选项</legend>
          <div className="gm-rw-format"><h3>导出什么</h3>{SIMPLE_EXPORT_FORMATS.map(f => <button key={f} type="button" aria-pressed={options.format === f} onClick={() => setOptions(o => normalizeExport({ ...o, format: f }))}>{({ mp4: '视频 MP4', gif: '动图 GIF', mp3: '音频 MP3', srt: '字幕 SRT', png: '封面图' } as const)[f]}</button>)}</div>
          {options.format === 'mp4' && <><label>清晰度<select value={options.resolution} onChange={e => setOptions(o => ({ ...o, resolution: Number(e.target.value) as ExportOptions['resolution'] }))}>{[720, 1080].map(n => <option value={n} key={n}>{n}p</option>)}</select></label>
            <label>画幅<select value={options.aspect} onChange={e => setOptions(o => ({ ...o, aspect: e.target.value as ExportOptions['aspect'] }))}>{['16:9', '9:16', '1:1'].map(a => <option key={a}>{a}</option>)}</select></label></>}
          {options.format === 'mp4' && <label>字幕<select value={options.subtitles} onChange={e => setOptions(o => ({ ...o, subtitles: e.target.value as ExportOptions['subtitles'] }))}>{exportSupport(options.format).subtitles.map(s => <option value={s} key={s}>{({ standard: '标准', large: '大字', none: '无字幕' } as Record<string, string>)[s]}</option>)}</select></label>}
        </fieldset>
        {options.format === 'gif' && <p>GIF 只取前 6 秒，没有声音，适合发群里做预告。</p>}
        {options.format === 'mp3' && <p>{mode === 'original' ? '只导出现场原声，可以当广播稿。' : '只导出配音和现场原声，可以当广播稿。'}不含背景音乐。</p>}
        {options.format === 'srt' && <p>字幕文件可以导入其他剪辑软件。</p>}
        {options.format === 'png' && <p>导出当前画面当封面。</p>}
        {checksError && <p className="gm-rw-alert" role="alert">{checksError}</p>}
        {canExportAnyway && <section className="gm-rw-unresolved" data-testid="export-unresolved" aria-labelledby="gm-rw-unresolved-title">
          <h3 id="gm-rw-unresolved-title">有 {openChecks.length} 项检查没有通过</h3>
          <p>仍然可以导出，但这份文件可能带着下面的问题，请先看一遍：</p>
          <ul>{openChecks.slice(0, 20).map(check => <li key={check.key} data-level={check.level}>
            <b>{check.level === 0 ? '错误' : '待确认'}</b>
            {check.sentence_id != null ? `第 ${check.sentence_id + 1} 句：` : ''}{check.message}</li>)}</ul>
          {openChecks.length > 20 && <p className="gm-rw-muted">还有 {openChecks.length - 20} 项未列出。</p>}
          <label><input type="checkbox" data-control="export-accept-unresolved" checked={acknowledged} disabled={!!busy || exporting}
            onChange={event => setAcceptedOpen(event.target.checked ? openSignature : '')} /> 我已看过以上未通过的检查，仍要导出</label>
        </section>}
        {!canExport && !canExportAnyway && !checksError && <p className="gm-rw-warning">{dirty ? '还有待应用修改，请先应用或撤销。' : '作品正在处理中，或检查结果还没读完，暂时不能导出。'}</p>}
        <button className="gm-rw-primary" disabled={!(canExport || acknowledged)} onClick={submitExport}>{canExport ? '开始导出' : canExportAnyway ? (acknowledged ? '仍然导出（带未通过的检查）' : '先勾选确认，再导出') : '暂时不能导出'}</button>
        {job && <div aria-live="polite"><h3>导出任务：{exportStateLabel(job.state)}</h3>{DEV_NOTES && <p>{job.qc}</p>}
          {exporting && <><progress aria-label="导出任务正在处理，未提供百分比" /><p>正在排队或编码；没有可用的百分比进度。</p>
            <button disabled={!!busy} onClick={() => void run('取消导出', async () => {
              const next = await api.cancelExport(job.id, lifecycle.current.signal, job.pipeline_revision);
              if (mounted.current) { rememberExport(next); callbacks.current.onChanged(); }
            })}>取消导出</button></>}
          {pollError && <button onClick={() => { setPollError(false); setPollRetry(n => n + 1); }}>恢复状态查询（不重新提交）</button>}
          {job.error && <p className="gm-rw-danger">{job.error}</p>}
          {job.unresolved && <p className="gm-rw-warning" data-testid="export-unresolved-note">这份文件是在检查未通过时导出的：错误 {job.unresolved.blocking} 项、待确认 {job.unresolved.pending} 项{job.unresolved.qc_blockers ? '，另有制作质检的阻断项' : ''}。发布前请自己再核对。</p>}
          {job.state === 'succeeded' && job.output_id && canDownload && job.pipeline_revision === revision
            && <a className="gm-rw-download" href={media(`${root}/exports/${job.export_id ?? job.id}/file`, job.pipeline_revision)} download>下载已完成的 {job.options.format.toUpperCase()} 文件（第 {job.pipeline_revision} 版）</a>}
          {job.state === 'succeeded' && (!canDownload || job.pipeline_revision !== revision) && <p>这份文件的版本或检查需要重新核对，暂不提供下载入口。</p>}
          {job.result && <p>{job.result.bytes.toLocaleString()} 字节 · 实测 {job.result.duration.toFixed(3)} 秒</p>}
          {job.disclosure && <p>衍生画面包含整段 AI 生成内容披露。</p>}
          {context && job.pipeline_revision !== revision && <p>这个导出捕获成片 v{job.pipeline_revision}，不是当前 v{revision}；已完成文件保持不变，新修改需要另行导出。</p>}
        </div>}
      </dialog>}
      {owner && job && !exportOpen && <div className="gm-rw-notice">导出任务：{exportStateLabel(job.state)} <button onClick={() => setExportOpen(true)}>查看导出状态</button></div>}
    </div>

  </section>;
}