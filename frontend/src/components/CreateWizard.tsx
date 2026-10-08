import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import SharedIcon from "./ui/Icon";
import { deferredRemoval, draftTaskOnce, readDraftAccess, readDraftTask } from "../lib/draftTasks";
import type { DraftAccess } from "../lib/draftTasks";
import type { PublicLimits } from "../types";
import type { ScriptAssistContext } from "../lib/scriptAssist";
import {
  AUDIO_ACCEPT, ELEMENT_RULES, MEDIA_ACCEPT, copyText, countSpokenChars, estimateSufficiency,
  fileIdentity, formatBytes, formatDuration, mediaKind, mediaLimits, probeMedia, canServerProbe,
  selectionIssues, serializeMedia, shotAdvice, validateFileSelection, validateTrim,
} from "../lib/mediaInput";
import type { ElementKey, MediaMetadata, MediaSelection } from "../lib/mediaInput";
import {
  MATCH_OK, MODE_CARDS, MODE_LABELS, MODE_LIMITS, PACING_CPM, TITLE_PLACEHOLDER,
  applySpeakerHints, countPreviewJumpCuts, defaultModePreferences, isProductionMode, matchState, parseModeScript,
  quoteGate, quoteTextEvidence, readModePreferences, readSentenceInputs, readSpeakers,
  scriptFromSentences, selectTranscriptSentence, sentencesMatchScript,
} from "../lib/productionModes";
import type { ModePreferences, ProductionMode, SentenceInput, SentenceKind, Speaker, TranscriptSegment, UploadSnapshot, MatchPreview } from "../lib/productionModes";
import {
  MATCH_DEBOUNCE_MS, deleteUploadFile, pollUploadStatus, readUploadBindings, readUploadTokens,
  requestMatchPreview, transferComplete, uploadFile, uploadMediaUrl, uploadUsable, completeUpload, reconcileServerProbe, serverTrimEnd, getUploadStatus,
} from "../lib/uploadSessions";
import type { ProbedUploadSnapshot, UploadBinding, UploadSession } from "../lib/uploadSessions";
import "../styles/modes.css";

export type { MediaSelection } from "../lib/mediaInput";

export interface Draft {
  draftTaskId?: string;
  draftTaskToken?: string;
  /** Explicit migration marker; never inferred for a newly created task. */
  legacyRestore?: boolean;
  script: string;
  preferences: ModePreferences;
  /** Original creation input, independent of mode/voice; absent legacy values stay absent. */
  source_voice_preferred?: boolean;
  step: 1 | 2 | 3;
  /** Metadata only. A bound upload is rechecked with its capability on refresh. */
  files: MediaMetadata[];
  elements: Partial<Record<ElementKey, { confirmed: boolean; evidence: string }>>;
  /** Keys include the sentence index and text; a script edit clears stale checks. */
  sentenceChecks: Record<string, boolean>;
  voice?: "ai" | "self";
  ownVoice?: MediaMetadata | null;
  mode?: ProductionMode;
  sentenceKinds?: Record<number, SentenceKind>;
  speakers?: Speaker[];
  uploadIds?: string[];
  uploadTokens?: Record<string, string>;
  sentences?: SentenceInput[];
  /** Stable fileIdentity → upload_id; no transcript, preview URL or file bytes. */
  uploadBindings?: UploadBinding[];
}

export interface CreateSubmission {
  draftTaskId?: string;
  draftTaskToken?: string;
  legacyRestore?: boolean;
  script: string;
  /** Retained for multipart/whole-recording compatibility; restored uploads may have no File. */
  files: File[];
  preferences: ModePreferences;
  source_voice_preferred?: boolean;
  /** Same order as uploadIds when present, otherwise files. Image trim_end is null. */
  asset_options: Array<{ note: string; trim_start: number; trim_end: number | null }>;
  ownVoice?: File;
  mode?: ProductionMode;
  sentenceKinds?: Record<number, SentenceKind>;
  speakers?: Speaker[];
  uploadIds?: string[];
  uploadTokens?: Record<string, string>;
  sentences?: SentenceInput[];
}

export interface CreateWizardProps {
  limits: PublicLimits;
  intro?: ReactNode;
  initialDraft?: Draft | null;
  /** Server-configured capability; unavailable capabilities cannot be enabled in a draft. */
  generativeAllowed: boolean;
  busy: boolean;
  serviceReady?: boolean;
  onDraft: (draft: Draft) => void;
  onSubmit: (value: CreateSubmission) => Promise<void>;
  onError: (message: string) => void;
  /** Optional AI writing helper, rendered under the manuscript. The wizard stays the owner of the text. */
  scriptAssistant?: (context: ScriptAssistContext) => ReactNode;
  /** Files to queue once on mount, exactly as if the user had picked them (the sample walkthrough). */
  initialFiles?: File[];
}

export function defaultPreferences(mode: ProductionMode = "voiceover"): ModePreferences { return defaultModePreferences(mode); }

const effects = [
  ["background_music", "背景音乐", "轻轻铺在播音下面，不抢人声。"],
  ["motion_effects", "画面慢慢推近", "轻运镜，不改变素材里的新闻事实。"],
  ["news_graphics", "新闻台标和片尾板", "增加新闻包装，与旁白字幕分开控制。"],
  ["transitions", "开头结尾淡入淡出", "仅处理首尾，不是镜头间花式转场。"],
  ["color_consistency", "颜色统一", "轻量调整，实际效果请看成片。"],
] as const;
// Handoff names, mapped to the shell's existing SVG exports (no emoji fallback).
function Icon({ name }: { name: "mic" | "mic-bubble" | "bubble" | "cloud-up" | "pencil" | "arrow" | "check" | "clapper-mic" }) {
  const names = { mic: "mic", "mic-bubble": "mixed", bubble: "original", "cloud-up": "upload",
    pencil: "pencil", arrow: "arrow", check: "check", "clapper-mic": "intro" } as const;
  return <SharedIcon name={names[name]} />;
}
const sentenceKey = (text: string, index: number) => `${index}:${text}`;
const errorMessage = (_error: unknown) => "操作未完成。请核对当前状态后重试；不会自动重复提交。";
const errorStatus = (error: unknown): number | undefined => {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : undefined;
};
/** Capacity refusals (quota, rate, disk): every further file would hit the same wall, so the queue pauses. */
const isCapacityFailure = (error: unknown) => errorStatus(error) === 429 || errorStatus(error) === 507 || errorStatus(error) === 413;
/** The real, safe reason for a failed upload; never raw server text beyond the fixed sentences decoded upstream. */
function uploadErrorText(error: unknown): string {
  const status = errorStatus(error), text = (error as { message?: unknown } | null)?.message, message = typeof text === "string" ? text : "";
  if (status === 429) return message && message.length <= 200 ? `${message}请先移除不需要的素材，或在“我的作品”里删掉旧草稿/作品后重试。` : errorMessage(error);
  if (status === 413) return "素材总量超过了服务器允许的上限，请移除一部分素材后重试。";
  if (status === 507) return "服务器磁盘空间不足，暂时无法接收更多素材，请稍后再试或先移除一部分素材。";
  return errorMessage(error);
}
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
type FileJob = { file?: File; itemId: string; controller: AbortController; probe: boolean; continuation?: () => boolean };

function modeDraftSeed(draft: Draft | null | undefined, generativeAllowed: boolean) {
  const mode = isProductionMode(draft?.mode) ? draft.mode : "voiceover";
  try {
    const access = readDraftAccess(draft ?? {});
    const preferences = readModePreferences(draft?.preferences, mode);
    preferences.generative_fill = mode !== "original" && generativeAllowed && preferences.generative_fill;
    const bindings = readUploadBindings(draft?.uploadBindings);
    if (bindings.some(binding => binding.draftTaskId !== access?.draftTaskId)) throw new Error("素材不属于这个草稿。");
    if (bindings.length && !access && draft?.legacyRestore !== true) throw new Error("旧草稿需要明确恢复后才能继续。");
    const ids = bindings.map(binding => binding.upload_id);
    const tokens = readUploadTokens(draft?.uploadTokens, ids);
    if (access && ids.some(id => tokens[id] !== access.draftTaskToken)) throw new Error("素材访问凭据不属于这个草稿。");
    if (bindings.some(binding => !draft?.files.some(file => file.id === binding.file_id))) throw new Error("上传绑定与文件清单不一致。");
    if (draft?.uploadIds && (draft.uploadIds.length !== ids.length || draft.uploadIds.some(uploadId => !ids.includes(uploadId)))) throw new Error("上传清单缺少稳定的文件身份绑定。");
    const kinds = draft?.sentenceKinds ?? {};
    let parsed: SentenceInput[] | null;
    try { parsed = parseModeScript(draft?.script ?? "", mode); } catch { parsed = null; }
    // An unfinished oversized sentence is an editing error, not evidence that
    // valid upload capabilities should be discarded or paid ASR repeated.
    const sentences = parsed === null ? null : draft?.sentences ? readSentenceInputs(draft.sentences, mode) : parseModeScript(draft?.script ?? "", mode, kinds);
    if (sentences && !sentencesMatchScript(draft?.script ?? "", sentences, mode)) throw new Error("草稿句子与稿件不一致。");
    const overrides: Record<number, SentenceKind> = {};
    for (const [idx, kind] of Object.entries(sentences ? kinds : {})) {
      if (!/^(0|[1-9]\d*)$/.test(idx) || !sentences?.some(sentence => sentence.idx === Number(idx) && sentence.kind === kind)) throw new Error("句子类型标记失效。");
      overrides[Number(idx)] = kind;
    }
    return { mode, preferences, bindings, tokens, sentences, overrides, access, speakers: readSpeakers(draft?.speakers ?? []),
      problem: parsed === null ? "稿件有超长句，请拆句后继续；已有上传凭据保留，不会重新上传或转写。" : "" };
  } catch {
    return { mode, preferences: defaultPreferences(mode), bindings: [] as UploadBinding[], tokens: {} as Record<string, string>, access: undefined,
      sentences: null, overrides: {} as Record<number, SentenceKind>, speakers: [] as Speaker[], problem: "模式草稿或上传凭据无效。保留稿件和文件清单，未把缓存当作已上传素材；请核对并重新选择。" };
  }
}

function Chips<T extends string>({ label, value, options, onChange, disabled = false, description }: {
  label: string; value: T; options: readonly (readonly [T, string])[]; onChange: (value: T) => void; disabled?: boolean;
  description?: string;
}) {
  const descriptionId = useId();
  return <div className="gm-option-group"><h3>{label}</h3><div className="gm-chips" role="group" aria-label={label} aria-describedby={description ? descriptionId : undefined}>
    {options.map(([key, text]) => <button key={key} type="button" className="gm-chip" aria-pressed={value === key}
      disabled={disabled} onClick={() => onChange(key)}>{text}</button>)}
  </div>{description && <p id={descriptionId} className="gm-option-description gm-muted gm-small">{description}</p>}</div>;
}

/** Uncontrolled draft seed. Remount with a new key when opening another draft.
 * No storage writes here: Workspace owns persistence. Mount only reads existing
 * upload status; new uploads/paid transcription require an explicit file action.
 */
export function CreateWizard({ limits, intro, initialDraft, generativeAllowed, busy, serviceReady = true, onDraft, onSubmit, onError, scriptAssistant, initialFiles }: CreateWizardProps) {
  const id = useId();
  const [seed] = useState(() => modeDraftSeed(initialDraft, generativeAllowed));
  const [mode, setMode] = useState<ProductionMode>(seed.mode);
  const [fileAdv, setFileAdv] = useState<Record<string, boolean>>({});
  const [sentListOpen, setSentListOpen] = useState(false);
  // Handoff Appendix B starts pickOpen=true, including blank C discovery.
  // Folding is presentation only: keep uploads, source hints and reads alive.
  const [pickOpen, setPickOpen] = useState(true);
  const [draftAccess, setDraftAccess] = useState<DraftAccess | undefined>(seed.access);
  const draftAccessRef = useRef(draftAccess);
  const [ensureDraft] = useState(() => draftTaskOnce(seed.access));
  const draftController = useRef<AbortController | null>(null);
  const [alignmentEnabled, setAlignmentEnabled] = useState(!initialDraft);
  const [draftReadError, setDraftReadError] = useState("");
  const [draftChecking, setDraftChecking] = useState(!!seed.access);
  const [removing, setRemoving] = useState<Record<string, string>>({});
  const [deleting, setDeleting] = useState<Record<string, boolean>>({});
  const removalCancels = useRef(new Map<string, () => void>());
  const [script, setScript] = useState(initialDraft?.script ?? "");
  const [step, setStep] = useState<1 | 2 | 3>(() => initialDraft?.step === 3 ? 2 : initialDraft?.step ?? 1);
  const [preferences, setPreferences] = useState<ModePreferences>(seed.preferences);
  const [sourceVoicePreferred] = useState<boolean | undefined>(initialDraft?.source_voice_preferred);
  const [confirmedSentences, setConfirmedSentences] = useState<SentenceInput[] | null>(seed.sentences);
  const [sentenceKinds, setSentenceKinds] = useState<Record<number, SentenceKind>>(seed.overrides);
  const [speakers, setSpeakers] = useState<Speaker[]>(seed.speakers);
  const speakerHintsApplied = useRef(new Set(seed.speakers.filter(person => person.name || person.title).map(person => person.id)));
  const [bindings, setBindings] = useState<UploadBinding[]>(seed.bindings);
  const [uploadTokens, setUploadTokens] = useState<Record<string, string>>(seed.tokens);
  const [uploads, setUploads] = useState<Record<string, UploadSnapshot>>({});
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({});
  // Why the upload queue was paused (capacity refusal); cleared as soon as an upload goes through.
  const [uploadBlock, setUploadBlock] = useState("");
  const [hashProgress, setHashProgress] = useState<Record<string, { bytes: number; total: number }>>({});
  const [expandedTranscripts, setExpandedTranscripts] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<{ key: string; rows: MatchPreview[]; error: string; pending: boolean }>({ key: "", rows: [], error: "", pending: false });
  const [previewRetry, setPreviewRetry] = useState(0);
  const [suggested, setSuggested] = useState({ voiceover: false, original: false });
  const [suggestion, setSuggestion] = useState<{ mode: "voiceover" | "original"; count: number } | null>(null);
  const [files, setFiles] = useState<MediaSelection[]>(() => (initialDraft?.files ?? []).map((item) => ({
    ...serializeMedia(item), status: "reselect",
  })));
  const [elements, setElements] = useState<Draft["elements"]>(initialDraft?.elements ?? {});
  const [sentenceChecks, setSentenceChecks] = useState<Draft["sentenceChecks"]>(initialDraft?.sentenceChecks ?? {});
  const [voice, setVoice] = useState<"ai" | "self">(seed.mode === "original" ? "ai" : initialDraft?.voice ?? (seed.preferences.voice === "mine" ? "self" : "ai"));
  const [ownVoice, setOwnVoice] = useState<File | undefined>();
  const legacyWholeVoice = initialDraft?.legacyRestore === true && !seed.access;
  const [voiceMeta, setVoiceMeta] = useState<MediaMetadata | null>(legacyWholeVoice ? initialDraft?.ownVoice ?? null : null);
  const [voiceUrl, setVoiceUrl] = useState("");
  const [voiceLoading, setVoiceLoading] = useState(false);
  const [recording, setRecording] = useState(false);
  const [requestingMic, setRequestingMic] = useState(false);
  const [recordSeconds, setRecordSeconds] = useState(0);
  const [recordFinishing, setRecordFinishing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [localError, setLocalError] = useState(seed.problem);
  const [manualCopy, setManualCopy] = useState(false);
  const [shotListOpen, setShotListOpen] = useState(false);
  const [elementDetailsOpen, setElementDetailsOpen] = useState(false);
  const filesRef = useRef(files);
  const bindingsRef = useRef(bindings);
  const tokensRef = useRef(uploadTokens);
  const uploadsRef = useRef(uploads);
  const mounted = useRef(true);
  const operations = useRef(new Map<string, AbortController>());
  const polling = useRef(new Map<string, AbortController>());
  const jobs = useRef<FileJob[]>([]);
  const transferRunning = useRef(false);
  const previewController = useRef<AbortController | null>(null);
  const previewGeneration = useRef(0);
  const livePreviewKey = useRef("");
  const urls = useRef(new Set<string>());
  const voiceController = useRef<AbortController | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const recordingTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const recordingGeneration = useRef(0);
  const submitLock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const scriptBox = useRef<HTMLTextAreaElement>(null);
  const previousStep = useRef(step);
  const [nextTried, setNextTried] = useState(false);
  const callbacks = useRef({ onDraft, onError });
  callbacks.current = { onDraft, onError };
  const cap = mediaLimits(limits);
  const analysis = useMemo(() => {
    // The prototype checks the actual first line and only its END punctuation.
    // Keep the body separate even when the title needs fixing; never count it as narration.
    const lines = script.replace(/\r\n?/g, "\n").split("\n");
    const first = lines[0].replace(/^\ufeff/u, "").trim();
    const titleError = first === TITLE_PLACEHOLDER ? "请把占位标题改成真实标题，再继续制作。" : !first ? "第一行必须填写标题，正文从下一行开始。"
      : Array.from(first).length > 40 ? "标题超过 40 字，请精简第一行。"
        : /[。！？.!?；;]$/.test(first) ? "标题末尾不能有句末标点（。！？.!?；;），请去掉后继续。" : "";
    const fixedFirst = first.replace(/[。！？.!?；;]+$/, "");
    const body = lines.slice(1).join("\n").trim();
    let rows: SentenceInput[] = [], parseError = "";
    try { rows = confirmedSentences ?? parseModeScript(script, mode); } catch (error) { parseError = errorMessage(error); }
    const sentences = rows.map(sentence => sentence.text);
    return { title: titleError ? null : first, titleError, body, sentences, rows, parseError,
      titleFixable: first !== fixedFirst && !!fixedFirst && Array.from(fixedFirst).length <= 40,
      fixedScript: [fixedFirst, ...lines.slice(1)].join("\n"),
      longSentences: sentences.map((text, index) => ({ index, chars: countSpokenChars(text) })).filter(item => item.chars > 30),
      spokenChars: countSpokenChars(body) };
  }, [script, mode, confirmedSentences]);
  const cpm = PACING_CPM[preferences.pacing];
  const charCount = Array.from(script.trim()).length;
  const scriptValid = !!analysis.title && (mode === "original" || charCount >= 20) && charCount <= cap.maxScript && analysis.rows.length > 0 && !analysis.parseError;
  const scriptProblem = charCount > cap.maxScript ? `稿件不能超过 ${cap.maxScript} 字。`
    : analysis.parseError || (mode !== "original" && charCount < 20 ? "稿件至少写 20 字，第一行标题，下一行正文。"
      : analysis.titleError || (!analysis.rows.length ? "请在标题下一行填写正文。" : ""));
  const blankOriginal = mode === "original" && (!script.trim() || !!analysis.title && !analysis.body);
  const narrationRows = analysis.rows.filter(sentence => sentence.kind === "narration");
  const quoteRows = analysis.rows.filter(sentence => sentence.kind === "quote");
  const bindingFor = (itemId: string) => bindings.find(binding => binding.file_id === itemId);
  const uploadFor = (itemId: string) => { const binding = bindingFor(itemId); return binding ? uploads[binding.upload_id] : undefined; };
  const orderedUploadIds = files.flatMap(item => { const binding = bindingFor(item.id); return binding ? [binding.upload_id] : []; });
  const readyUploadIds = orderedUploadIds.filter(uploadId => uploadUsable(uploads[uploadId]));
  const fileUsable = (itemId: string) => uploadUsable(uploadFor(itemId)) && (!draftAccess || uploadFor(itemId)?.status === "ready");
  const querySentences = mode === "voiceover" ? analysis.rows.map(sentence => ({ ...sentence, kind: "quote" as const })) : analysis.rows;
  const previewKey = JSON.stringify([mode, script, querySentences, readyUploadIds, files.map(item => [item.id, uploadFor(item.id)?.status ?? "pending"])]);
  livePreviewKey.current = previewKey;
  const matches = preview.key === previewKey && !preview.pending && !preview.error ? preview.rows : [];
  const outsideTrim = quoteRows.some(sentence => {
    const source = matches.find(match => match.idx === sentence.idx)?.source;
    if (!source) return false;
    const file = files.find(item => bindingFor(item.id)?.upload_id === source.upload_id);
    return !file || file.kind !== "video" || source.start < file.trim_start || file.trim_end === null || source.end > file.trim_end;
  });
  const matchingProblem = draftReadError || (draftChecking ? "正在核对草稿，请稍候。" : "")
    || (Object.keys(removing).length ? "请先撤销移除或等待 5 秒，再继续。" : "")
    || quoteGate(analysis.rows, matches, mode) || (outsideTrim ? "有原话出处超出所选素材裁剪范围，请调整“只用”区间或重新选择原话。" : "");
  const noSpeech = mode === "original" && files.length > 0 && files.every(item => uploadUsable(uploadFor(item.id)))
    && !readyUploadIds.some(uploadId => uploads[uploadId]?.status === "ready" && uploads[uploadId].has_speech && uploads[uploadId].transcript.some(segment => segment.text.trim()));
  const localFiles = files.filter(item => !!item.file);
  const issues = localFiles.length ? selectionIssues(localFiles, limits) : [];
  if (!files.length) issues.push("至少选择 1 个真实视频或照片。");
  if (files.length > cap.maxFiles) issues.push(`最多 ${cap.maxFiles} 个素材。`);
  if (files.some(item => item.duration == null || !Number.isFinite(item.duration) || item.duration <= 0 || item.status !== "ready")) issues.push("所有素材须完成有效的服务端校验。");
  if (files.reduce((sum, item) => sum + (item.duration ?? 0), 0) > cap.maxTotalDuration) issues.push("素材原始总时长超过限制；裁剪不能绕过限制。");
  if (files.reduce((sum, item) => sum + item.size, 0) > cap.maxTotalBytes) issues.push("素材总大小超过限制。");
  if (files.some(item => item.size > cap.maxBytes || item.duration != null && item.duration > cap.maxDuration)) issues.push("有素材超过单文件大小或原始时长限制。");
  if (files.some(item => !item.file && !bindingFor(item.id))) issues.push("未上传的草稿文件需要重新选择，或移除不用的条目。");
  if (files.some(item => !!uploadErrors[item.id])) issues.push("有上传状态尚未核实，请查看文件行并重试读取或续传。");
  if (files.some(item => !fileUsable(item.id))) issues.push("素材仍在上传或预处理，完成后才能进入选效果；可以继续编辑稿件。");
  if (files.some(item => item.status === "ready" && validateTrim(item))) issues.push("请修正素材裁剪时间。");
  if (files.some(item => Array.from(item.note).length > 20)) issues.push("每个素材备注最多 20 字。");
  if (files.some(item => item.kind === "image" && item.size > 50 * 1024 ** 2)) issues.push("照片不能超过 50 MiB。");
  if (mode === "original" && files.some(item => uploadFor(item.id)?.status === "failed")) issues.push("原声模式不能使用转写失败的素材，请补传可听清人声的素材或移除失败项。");
  if (noSpeech) issues.push("素材中没有可用人声，不能用“只用原声”制作。请补传采访素材。");
  const locked = busy || submitting;
  const audioBusy = recording || requestingMic || voiceLoading || recordFinishing;
  // Read only existing, validated task-scoped receipts. Recompute on each render
  // so a receipt/error change cannot leave a files-only memo with stale speech.
  const speechByFile = new Map<string, boolean | null>();
  if (draftAccess && !draftChecking && !draftReadError) {
    for (const item of files) {
      const binding = bindingFor(item.id), snapshot = uploadFor(item.id);
      if (item.kind !== "video" || item.id !== fileIdentity(item) || item.file && fileIdentity(item.file) !== item.id
        || uploadErrors[item.id] || !binding || binding.draftTaskId !== draftAccess.draftTaskId || !binding.server_file_id
        || uploadTokens[binding.upload_id] !== draftAccess.draftTaskToken || !snapshot || snapshot.status !== "ready"
        || snapshot.probe_ok !== true || snapshot.id !== binding.upload_id || snapshot.name !== item.name || snapshot.bytes !== item.size
        || snapshot.sec !== item.duration || snapshot.sha256 !== undefined && snapshot.sha256 !== binding.sha256) continue;
      speechByFile.set(item.id, snapshot.has_speech);
    }
  }
  const estimate = estimateSufficiency(files, narrationRows.length, narrationRows.reduce((sum, row) => sum + countSpokenChars(row.text), 0), cpm, speechByFile);
  // A chip is a manual whole-manuscript self-check, never inferred NLP evidence.
  // Keep the existing evidence field and invalidate it on every manuscript edit.
  const wholeScriptEvidence = `script:${script}`;
  const confirmedElements = new Set(ELEMENT_RULES.filter(({ key }) => elements[key]?.confirmed
    && (elements[key]?.evidence === wholeScriptEvidence
      || analysis.sentences.some((text, index) => sentenceKey(text, index) === elements[key]?.evidence))).map(({ key }) => key));
  const captionStyle = preferences.caption_style ?? "news";
  const captionNote = captionStyle === "none" ? "只隐藏旁白字幕；标题、字幕审计和必需的 AI 生成画面标注仍保留。"
    : captionStyle === "big" ? "旁白字幕字号为新闻标准的 1.5 倍，适合投影和手机；请在成片检查遮挡。"
      : "白字黑边、居中下方，沿用新闻标准旁白字幕。";
  const shotText = analysis.sentences.map((text, index) => `${index + 1}. ${text}\n${shotAdvice(text)}`).join("\n\n");
  const fileBytes = files.reduce((sum, item) => sum + item.size, 0);
  const sourceSeconds = files.reduce((sum, item) => sum + (item.duration ?? 0), 0);
  const usingOwnVoice = mode !== "original" && voice === "self" && narrationRows.length > 0;
  const usingWholeVoice = legacyWholeVoice && usingOwnVoice;
  const voiceValid = !usingWholeVoice || (!!ownVoice && !!voiceMeta && voiceMeta.duration != null && voiceMeta.duration <= cap.maxDuration && ownVoice.size <= cap.maxBytes);
  const totalBytesValid = fileBytes + (usingWholeVoice ? ownVoice?.size ?? 0 : 0) <= cap.maxTotalBytes;
  const totalDurationValid = sourceSeconds + (usingWholeVoice ? voiceMeta?.duration ?? 0 : 0) <= cap.maxTotalDuration;
  if (!totalBytesValid) issues.push("素材和录音合计超过上传总量限制。");
  if (!totalDurationValid) issues.push("素材和录音合计超过总时长限制。");
  const mediaValid = issues.length === 0;
  const canSubmit = serviceReady && (!!draftAccess || initialDraft?.legacyRestore === true) && scriptValid && mediaValid && !matchingProblem && voiceValid && !audioBusy && !locked && preferences.custom_instructions.length <= 500;
  const quoteSeconds = quoteRows.reduce((sum, sentence) => { const source = matches.find(match => match.idx === sentence.idx)?.source; return sum + (source ? source.end - source.start : 0); }, 0);
  const quotesComplete = quoteRows.every(sentence => !!matches.find(match => match.idx === sentence.idx)?.source);
  const summarySeconds = (usingWholeVoice && voiceMeta ? voiceMeta.duration ?? 0 : estimate.narrationSeconds) + quoteSeconds;
  const summaryLine = `${MODE_LABELS[mode]} · 旁白 ${narrationRows.length} 句 / 原声 ${quoteRows.length} 段 · ${quotesComplete ? `预计 ${formatDuration(summarySeconds)}` : "原声时长待匹配"}${mode === "voiceover" ? "" : ` · 说话人 ${speakers.length} 位`}`;
  const jumpCuts = countPreviewJumpCuts(analysis.rows, matches);
  const recordSupported = typeof MediaRecorder !== "undefined" && typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

  const releaseUrl = (url?: string) => { if (url) { URL.revokeObjectURL(url); urls.current.delete(url); } };
  const publishFiles = (next: MediaSelection[]) => { filesRef.current = next; setFiles(next); };
  const reportError = (message: string) => { if (mounted.current) { setLocalError(message); callbacks.current.onError(message); } };
  const stopTracks = () => {
    stream.current?.getTracks().forEach((track) => track.stop()); stream.current = null;
    if (recordingTimer.current) clearInterval(recordingTimer.current);
    recordingTimer.current = null;
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      draftController.current?.abort();
      removalCancels.current.forEach(cancel => cancel()); removalCancels.current.clear();
      operations.current.forEach((controller) => controller.abort()); operations.current.clear();
      polling.current.forEach((controller) => controller.abort()); polling.current.clear();
      jobs.current.forEach(job => job.controller.abort()); jobs.current = [];
      previewController.current?.abort();
      voiceController.current?.abort();
      recordingGeneration.current++;
      if (recorder.current) {
        recorder.current.ondataavailable = null; recorder.current.onstop = null; recorder.current.onerror = null;
        if (recorder.current.state !== "inactive") recorder.current.stop();
      }
      stopTracks();
      urls.current.forEach((url) => URL.revokeObjectURL(url)); urls.current.clear();
    };
  }, []);

  useEffect(() => {
    if (!generativeAllowed || mode === "original") setPreferences((current) => current.generative_fill ? { ...current, generative_fill: false } : current);
  }, [generativeAllowed, mode]);

  useEffect(() => {
    // A reload never creates a session, sends a chunk or starts transcription.
    // StrictMode's second mount owns new controllers; old reads stay fenced.
    const controller = new AbortController();
    const restore = async () => {
      try {
        if (seed.access) await readDraftTask(seed.access, controller.signal);
        if (controller.signal.aborted) return;
        setDraftChecking(false);
        seed.bindings.forEach(binding => observeUpload(binding.file_id));
      } catch (error) {
        if (!controller.signal.aborted) { setDraftChecking(false); setDraftReadError(errorMessage(error)); }
      }
    };
    void restore();
    return () => { controller.abort(); polling.current.forEach(controller => controller.abort()); polling.current.clear(); };
  }, [seed]);

  useEffect(() => {
    const controller = new AbortController(), serial = ++previewGeneration.current;
    previewController.current?.abort(); previewController.current = controller;
    const current = () => mounted.current && !controller.signal.aborted && serial === previewGeneration.current && livePreviewKey.current === previewKey;
    const eligible = alignmentEnabled && !draftChecking && !draftReadError && !Object.keys(removing).length
      && querySentences.length > 0 && files.length > 0 && files.every(item => fileUsable(item.id));
    setPreview({ key: previewKey, rows: [], error: "", pending: eligible });
    const timer = eligible ? setTimeout(() => {
      void requestMatchPreview(querySentences, readyUploadIds, tokensRef.current, controller.signal, draftAccessRef.current,
        { mode, script }).then(rows => {
        if (current()) setPreview({ key: previewKey, rows, error: "", pending: false });
      }).catch(error => {
        if (current()) setPreview({ key: previewKey, rows: [], error: errorMessage(error), pending: false });
      });
    }, MATCH_DEBOUNCE_MS) : undefined;
    return () => { clearTimeout(timer); controller.abort(); if (previewController.current === controller) previewController.current = null; };
    // This serialized key binds mode + exact sentences/source hints + ready IDs.
    // A response cannot certify a different edit, even before cleanup runs.
  }, [previewKey, previewRetry, alignmentEnabled, draftChecking, draftReadError, removing]);

  useEffect(() => {
    if (preview.key !== previewKey || preview.pending || preview.error) return;
    if (mode === "voiceover" && !suggested.voiceover) {
      const count = preview.rows.filter(row => row.source && row.score >= MATCH_OK).length;
      if (count) { setSuggested(value => ({ ...value, voiceover: true })); setSuggestion({ mode, count }); }
    } else if (mode === "original" && !suggested.original) {
      const count = preview.rows.filter(row => matchState(row) === "missing").length;
      if (count) { setSuggested(value => ({ ...value, original: true })); setSuggestion({ mode, count }); }
    }
  }, [mode, preview, previewKey, suggested]);

  const detectedSpeakers = useMemo(() => {
    const found = new Map<string, Speaker>();
    for (const upload of Object.values(uploads)) {
      for (const segment of upload.transcript) {
        if (!segment.speaker_id || !segment.text.trim()) continue;
        const person = found.get(segment.speaker_id) ?? { id: segment.speaker_id, name: "", title: "", auto_label: `说话人 ${found.size + 1}`, appearances: 0, seconds: 0 };
        found.set(person.id, { ...person, appearances: person.appearances + 1, seconds: person.seconds + segment.end - segment.start });
      }
      for (const person of upload.speakers ?? []) found.set(person.id, person);
    }
    return [...found.values()];
  }, [uploads]);
  useEffect(() => {
    if (!detectedSpeakers.length) return;
    setSpeakers(previous => {
      const byId = new Map(previous.map(person => [person.id, person]));
      for (const person of detectedSpeakers) {
        const old = byId.get(person.id);
        byId.set(person.id, { ...person, name: old?.name ?? person.name, title: old?.title ?? person.title });
      }
      const next = [...byId.values()];
      return JSON.stringify(previous) === JSON.stringify(next) ? previous : next;
    });
  }, [detectedSpeakers]);

  useEffect(() => {
    if (mode === "voiceover" || !matches.length) return;
    const hinted = applySpeakerHints(speakers.filter(person => !speakerHintsApplied.current.has(person.id)), analysis.rows, matches);
    const byId = new Map(hinted.map(person => [person.id, person]));
    const next = speakers.map(person => {
      const hint = byId.get(person.id);
      return hint && (hint.name || hint.title) ? hint : person;
    });
    if (JSON.stringify(next) !== JSON.stringify(speakers)) {
      next.forEach((person, index) => { if (person !== speakers[index]) speakerHintsApplied.current.add(person.id); });
      setSpeakers(next);
    }
  }, [preview, detectedSpeakers, mode, speakers]);

  useEffect(() => {
    callbacks.current.onDraft({ script, step, preferences: { ...preferences, voice: usingOwnVoice ? "mine" : "ai", generative_fill: mode !== "original" && generativeAllowed && preferences.generative_fill } as ModePreferences,
      ...(sourceVoicePreferred === undefined ? {} : { source_voice_preferred: sourceVoicePreferred }),
      files: files.map(serializeMedia), elements, sentenceChecks, voice, ownVoice: voiceMeta ? serializeMedia(voiceMeta) : null,
        mode, sentenceKinds, speakers, sentences: analysis.rows, uploadIds: orderedUploadIds, uploadTokens, ...draftAccess,
        ...(initialDraft?.legacyRestore === true ? { legacyRestore: true } : {}),
        uploadBindings: bindings.map(({ file_id, upload_id, sha256, chunk_size, put_url, draftTaskId, server_file_id }) => ({ file_id, upload_id, sha256, chunk_size, put_url, draftTaskId, server_file_id })) });
      }, [script, step, preferences, sourceVoicePreferred, generativeAllowed, files, elements, sentenceChecks, voice, voiceMeta, mode, sentenceKinds, speakers, bindings, uploadTokens, confirmedSentences, draftAccess]);

  useEffect(() => {
    if (previousStep.current !== step) { heading.current?.focus(); previousStep.current = step; setNextTried(false); }
  }, [step]);

  function canGoToStep(target: 1 | 2 | 3) {
    // Backward navigation never requires fixing the current step first.
    return !locked && !audioBusy && (target <= step || (target === 2 ? scriptValid || blankOriginal : step === 2 && scriptValid && mediaValid && !matchingProblem));
  }

  // Why "下一步" cannot proceed yet. Empty when the user may continue.
  const nextBlockReason = step >= 3 ? ""
    : locked ? "正在提交，请稍候。"
    : audioBusy ? "声音正在录制或处理，请稍候再继续。"
    : step === 1 ? (scriptValid || blankOriginal ? "" : scriptProblem || "请先写好稿件：第一行标题，下一行正文。")
    : scriptValid && mediaValid && !matchingProblem ? "" : scriptProblem || issues[0] || matchingProblem || "素材还没准备好，请检查后再继续。";

  function goNext() {
    if (nextBlockReason) {
      // Never leave a dead button: say what is missing and move to it.
      setNextTried(true);
      if (step === 1 && !locked && !audioBusy) scriptBox.current?.focus();
      return;
    }
    goToStep(step === 1 ? 2 : 3);
  }

  function goToStep(target: 1 | 2 | 3) {
    if (!canGoToStep(target) || target === step) return;
    if (step === 2 && target === 3 && mode !== "original" && narrationRows.length > 0 && !estimate.enough && !window.confirm(
      "素材可能不够。数量和时长仅作估算，不是镜头检测或内容匹配；实际制作仍可能失败，建议补拍或精简稿件。\n仍要继续到选效果吗？这里只切换步骤，不会提交制作任务。",
    )) return;
    setStep(target);
  }

  function changeScript(value: string) {
    setAlignmentEnabled(true);
    invalidatePreview();
    try {
      const next = parseModeScript(value, mode);
      const retainedKinds: Record<number, SentenceKind> = {};
      // Changing only a title/prefix must not re-split a chip-confirmed row.
      const currentBody = script.replace(/\r\n?/g, "\n").split("\n").slice(1).join("\n");
      const nextBody = value.replace(/\r\n?/g, "\n").split("\n").slice(1).join("\n");
      const merged = currentBody === nextBody ? analysis.rows : next.map(sentence => {
        const old = analysis.rows[sentence.idx];
        // Only an identical row at the same index can retain a manual kind/hint.
        if (old?.text !== sentence.text) return sentence;
        const overridden = mode === "mixed" && Object.prototype.hasOwnProperty.call(sentenceKinds, sentence.idx);
        if (overridden) retainedKinds[sentence.idx] = sentenceKinds[sentence.idx];
        return { ...sentence, kind: overridden ? sentenceKinds[sentence.idx] : sentence.kind,
          ...(old.source_hint ? { source_hint: old.source_hint } : {}),
          speaker_hint: sentence.speaker_hint || (old.source_hint ? old.speaker_hint : "") };
      });
      setConfirmedSentences(merged); setSentenceKinds(currentBody === nextBody ? sentenceKinds : retainedKinds);
    } catch { setConfirmedSentences(null); setSentenceKinds({}); }
    setScript(value); setElements({}); setSentenceChecks({});
    // An existing whole-document recording no longer certifies the edited manuscript.
    clearVoice();
  }

  function invalidatePreview() {
    previewGeneration.current++; previewController.current?.abort();
    setPreview({ key: "", rows: [], error: "", pending: false });
  }

  function chooseMode(nextMode: ProductionMode, useSuggestion = false) {
    if (locked || audioBusy || nextMode === mode) return;
    setAlignmentEnabled(true);
    invalidatePreview(); setSuggestion(null);
    // Mode changes never re-split source-selected rows or turn B's editorial
    // prefixes into spoken A text. Preserve the entire confirmed body and hints.
    const next = analysis.rows.map(sentence => {
      const match = matches.find(row => row.idx === sentence.idx);
      return { ...sentence,
        kind: nextMode === "voiceover" ? "narration" as const : nextMode === "original" ? "quote" as const
          : useSuggestion && match ? mode === "voiceover" ? match.source && match.score >= MATCH_OK ? "quote" as const : "narration" as const
            : matchState(match) === "missing" ? "narration" as const : "quote" as const : sentence.kind };
    });
    // Text and files stay intact; only confirmed types and mode defaults change.
    setMode(nextMode); setConfirmedSentences(analysis.parseError ? null : next);
    setSentenceKinds(analysis.parseError ? {} : Object.fromEntries(next.map(row => [row.idx, row.kind])));
    // Preserve source-selected quotes when moving C→B without interpreting
    // them as unmarked narration again on the next draft restoration.
    if (!analysis.parseError && (mode === "original" && nextMode === "mixed" || !sentencesMatchScript(script, next, nextMode))) {
      setScript(scriptFromSentences(script.split(/\r?\n/u)[0], next, nextMode));
    }
    setPreferences(previous => ({ ...previous, background_music: nextMode !== "original", motion_effects: nextMode !== "original",
      generative_fill: nextMode !== "original" && generativeAllowed && previous.generative_fill }));
    // Changing which rows are narrated invalidates a whole-document recording.
    clearVoice();
    if (nextMode === "original") setVoice("ai");
    setSentenceChecks({}); setElements({});
  }

  function commitSentences(rows: SentenceInput[]) {
    setAlignmentEnabled(true);
    invalidatePreview();
    const next = rows.map((row, idx) => ({ ...row, idx }));
    const title = script.replace(/^\ufeff/u, "").split(/\r?\n/u)[0]?.trim() || TITLE_PLACEHOLDER;
    setScript(scriptFromSentences(title, next, mode)); setConfirmedSentences(next);
    setSentenceKinds(Object.fromEntries(next.map(row => [row.idx, row.kind])));
    setSentenceChecks({}); setElements({}); clearVoice();
  }
  function changeKind(idx: number, kind: SentenceKind) {
    if (mode !== "mixed") return;
    setAlignmentEnabled(true);
    if (kind === "narration" && matchState(matches.find(row => row.idx === idx)) === "missing"
      && !window.confirm("这句原话在素材里没找到。改成旁白后将由配音朗读，不再使用现场声音，确定吗？")) return;
    invalidatePreview();
    const next = analysis.rows.map(sentence => sentence.idx === idx ? { ...sentence, kind } : sentence);
    setConfirmedSentences(next); setSentenceKinds(current => ({ ...current, [idx]: kind }));
    setSentenceChecks({}); setElements({}); clearVoice();
  }
  function pickTranscript(uploadId: string, segment: TranscriptSegment, selected: boolean) {
    if (mode !== "original") return;
    try { commitSentences(selectTranscriptSentence(analysis.rows, uploadId, segment, selected)); }
    catch (error) { reportError(errorMessage(error)); }
  }

  function updateFile(itemId: string, patch: Partial<MediaSelection>) {
    publishFiles(filesRef.current.map((item) => item.id === itemId ? { ...item, ...patch } : item));
  }

  function removeFile(itemId: string) {
    const item = filesRef.current.find(file => file.id === itemId);
    if (!item || operations.current.has(itemId) || removalCancels.current.has(itemId)) return;
    invalidatePreview();
    setRemoving(current => ({ ...current, [itemId]: item.name }));
    const cancel = deferredRemoval(() => {
      removalCancels.current.delete(itemId);
      const session = sessionFor(itemId);
      const controller = new AbortController(); operations.current.set(itemId, controller);
      setDeleting(current => ({ ...current, [itemId]: true }));
      void (async () => {
        try {
          if (session) await deleteUploadFile(session, controller.signal);
          if (mounted.current && !controller.signal.aborted) finalizeRemoval(itemId);
        } catch (error) {
          // Keep the durable binding if deletion is uncertain; a later GET can
          // reconcile it. Never start with a silently retained server file.
          if (mounted.current && !controller.signal.aborted) {
            setUploadErrors(current => ({ ...current, [itemId]: "移除尚未确认，请重新读取状态。" }));
            reportError(errorMessage(error));
          }
        } finally {
          if (operations.current.get(itemId) === controller) operations.current.delete(itemId);
          if (mounted.current) {
            setRemoving(current => { const next = { ...current }; delete next[itemId]; return next; });
            setDeleting(current => { const next = { ...current }; delete next[itemId]; return next; });
          }
        }
      })();
    });
    removalCancels.current.set(itemId, cancel);
  }
  function undoRemoval(itemId: string) {
    if (!removalCancels.current.has(itemId)) return;
    removalCancels.current.get(itemId)?.(); removalCancels.current.delete(itemId);
    setRemoving(current => { const next = { ...current }; delete next[itemId]; return next; });
    setNotice("已撤销移除，素材与备注已保留。");
  }
  function finalizeRemoval(itemId: string) {
    operations.current.get(itemId)?.abort(); operations.current.delete(itemId);
    polling.current.get(itemId)?.abort(); polling.current.delete(itemId);
    invalidatePreview();
    const removed = bindingsRef.current.find(binding => binding.file_id === itemId);
    const nextBindings = bindingsRef.current.filter(binding => binding.file_id !== itemId);
    bindingsRef.current = nextBindings; setBindings(nextBindings);
    if (removed) {
      const nextTokens = { ...tokensRef.current }, nextUploads = { ...uploadsRef.current };
      const removedSpeakers = new Set([
        ...(nextUploads[removed.upload_id]?.transcript.map(segment => segment.speaker_id) ?? []),
        ...(nextUploads[removed.upload_id]?.speakers?.map(person => person.id) ?? []),
      ]);
      delete nextTokens[removed.upload_id]; delete nextUploads[removed.upload_id];
      tokensRef.current = nextTokens; setUploadTokens(nextTokens); uploadsRef.current = nextUploads; setUploads(nextUploads);
      setConfirmedSentences(previous => previous?.map(sentence => sentence.source_hint?.upload_id === removed.upload_id
        ? { idx: sentence.idx, text: sentence.text, kind: sentence.kind, speaker_hint: sentence.speaker_hint } : sentence) ?? null);
      const remainingSpeakers = new Set(Object.values(nextUploads).flatMap(upload => [
        ...upload.transcript.map(segment => segment.speaker_id), ...(upload.speakers?.map(person => person.id) ?? []),
      ]));
      setSpeakers(previous => previous.filter(person => !removedSpeakers.has(person.id) || remainingSpeakers.has(person.id)));
    }
    setUploadErrors(previous => { const next = { ...previous }; delete next[itemId]; return next; });
    setHashProgress(previous => { const next = { ...previous }; delete next[itemId]; return next; });
    releaseUrl(filesRef.current.find((item) => item.id === itemId)?.thumbnail);
    publishFiles(filesRef.current.filter((item) => item.id !== itemId));
    setSentenceChecks({});
    if (removed) setNotice("已从本次素材清单移除；此前的处理费用不会撤销。");
  }

  function rememberSession(session: UploadSession) {
    const binding: UploadBinding = { file_id: session.file_id, upload_id: session.upload_id, sha256: session.sha256,
      chunk_size: session.chunk_size, put_url: session.put_url, draftTaskId: session.draftTaskId, server_file_id: session.server_file_id };
    const next = [...bindingsRef.current.filter(item => item.file_id !== binding.file_id), binding];
    bindingsRef.current = next; setBindings(next);
    const tokens = { ...tokensRef.current, [session.upload_id]: session.access_token };
    tokensRef.current = tokens; setUploadTokens(tokens);
  }
  function sessionFor(itemId: string): UploadSession | undefined {
    const binding = bindingsRef.current.find(item => item.file_id === itemId);
    return binding && tokensRef.current[binding.upload_id] ? { ...binding, access_token: tokensRef.current[binding.upload_id],
      ...(binding.draftTaskId ? { draftTaskToken: draftAccessRef.current?.draftTaskToken } : {}) } : undefined;
  }
  function publishSnapshot(itemId: string, snapshot: UploadSnapshot) {
    const item = filesRef.current.find(file => file.id === itemId), session = sessionFor(itemId);
    if (!item || !session) return;
    if (snapshot.id !== session.upload_id || snapshot.name !== item.name || snapshot.bytes !== item.size
      || snapshot.sha256 !== undefined && snapshot.sha256 !== session.sha256) throw new Error("上传状态与文件身份不一致，未采用这个响应。");
    const next = { ...uploadsRef.current, [snapshot.id]: snapshot }; uploadsRef.current = next; setUploads(next);
    setUploadErrors(previous => { const next = { ...previous }; delete next[itemId]; return next; });
    if (session.draftTaskId) updateFile(itemId, reconcileServerProbe(item, snapshot, limits));
    else if (snapshot.sec != null && (snapshot.status === "ready" || snapshot.probe_ok)) updateFile(itemId, {
      duration: snapshot.sec, trim_end: item.kind === "image" ? null : serverTrimEnd(item, snapshot.sec), status: "ready",
    });
    else if (!item.file) updateFile(itemId, { status: snapshot.status === "uploading" ? "reselect" : snapshot.status === "failed" ? "error" : "loading" });
  }
  function observeUpload(itemId: string) {
    const session = sessionFor(itemId);
    if (!session || !mounted.current) return;
    polling.current.get(itemId)?.abort();
    if (uploadsRef.current[session.upload_id]?.status === "ready") {
      const next = { ...uploadsRef.current }; delete next[session.upload_id]; uploadsRef.current = next; setUploads(next);
      invalidatePreview();
    }
    const controller = new AbortController(); polling.current.set(itemId, controller);
    const current = () => mounted.current && !controller.signal.aborted && polling.current.get(itemId) === controller && filesRef.current.some(item => item.id === itemId);
    void pollUploadStatus(session.upload_id, session.access_token, controller.signal, snapshot => {
      if (current()) publishSnapshot(itemId, snapshot);
    }, session).catch(error => {
      if (current()) {
        const next = { ...uploadsRef.current }; delete next[session.upload_id]; uploadsRef.current = next; setUploads(next);
        setUploadErrors(previous => ({ ...previous, [itemId]: errorMessage(error) })); invalidatePreview();
      }
    }).finally(() => { if (polling.current.get(itemId) === controller) polling.current.delete(itemId); });
  }

  async function drainFileJobs() {
    if (transferRunning.current) return;
    transferRunning.current = true;
    try {
      while (jobs.current.length && mounted.current) {
        const job = jobs.current.shift()!;
        const { itemId, controller, file } = job;
        const current = () => mounted.current && !controller.signal.aborted && operations.current.get(itemId) === controller
          && filesRef.current.some(item => item.id === itemId) && (!job.continuation || job.continuation());
        if (!current()) {
          if (operations.current.get(itemId) === controller) operations.current.delete(itemId);
          continue;
        }
        let probeDone = !job.probe;
        try {
          // An explicit continuation must reconcile every bound peer first:
          // a lost completion reply may already have started server work.
          if (job.continuation) {
            for (const item of filesRef.current) {
              if (!current()) break;
              const session = sessionFor(item.id);
              if (!session) continue;
              const state = await getUploadStatus(session.upload_id, session.access_token, controller.signal, session);
              if (!current()) break;
              publishSnapshot(item.id, state);
            }
            if (!current()) continue;
          }
          if (file && job.probe) {
            const item = filesRef.current.find(item => item.id === itemId)!;
            let metadata;
            try { metadata = await probeMedia(file, item.kind, controller.signal, cap.maxDuration); }
            catch (error) {
              if (!canServerProbe(error, item.kind) || initialDraft?.legacyRestore === true || !current()) throw error;
              updateFile(itemId, { duration: null, width: undefined, height: undefined, status: "loading" });
              setNotice("浏览器无法读取此视频，正上传原文件供服务器校验；整批校验通过前不会启动预转写。");
            }
            if (metadata) {
              if (!current()) { if (metadata.thumbnail) URL.revokeObjectURL(metadata.thumbnail); continue; }
              if (metadata.thumbnail) urls.current.add(metadata.thumbnail);
              releaseUrl(item.thumbnail);
              updateFile(itemId, { ...metadata, trim_end: item.kind === "image" ? null : item.trim_end ?? metadata.duration, status: "ready" });
              probeDone = true;
              if (metadata.duration > cap.maxDuration || filesRef.current.reduce((sum, item) => sum + (item.duration ?? 0), 0) > cap.maxTotalDuration) {
                throw new Error("素材原始时长超过限制，未开始上传。请移除或在本地剪短后重选。");
              }
            }
            probeDone = true;
          }
          const selected = filesRef.current.find(item => item.id === itemId)!;
          if (selected.size > cap.maxBytes || selected.duration != null && selected.duration > cap.maxDuration
            || filesRef.current.reduce((sum, item) => sum + item.size, 0) > cap.maxTotalBytes
            || filesRef.current.reduce((sum, item) => sum + (item.duration ?? 0), 0) > cap.maxTotalDuration) {
            throw new Error("素材大小或原始时长超限，未开始上传。请移除或在本地剪短后重选。");
          }
          let access = draftAccessRef.current;
          if (!access && initialDraft?.legacyRestore !== true) {
            draftController.current ??= new AbortController();
            access = await ensureDraft(mode, draftController.current.signal);
            if (!mounted.current) continue;
            draftAccessRef.current = access; setDraftAccess(access);
          }
          if (!current()) continue;
          const snapshot = await uploadFile(file, controller.signal, {
            onSession: session => { if (current()) rememberSession(session); },
            onSnapshot: snapshot => { if (current()) publishSnapshot(itemId, snapshot); },
            onHashProgress: (bytes, total) => { if (current()) setHashProgress(previous => ({ ...previous, [itemId]: { bytes, total } })); },
          }, sessionFor(itemId), access, !!access);
          if (current()) setUploadBlock("");
          if (current() && (snapshot.status === "probing" || snapshot.status === "transcribing")) observeUpload(itemId);
          // Only an explicit transfer/retry may continue preprocessing. Refresh
          // merely observes. All current local files must be bound and probed;
          // the server independently validates its entire attached selection.
          if (current() && access && !jobs.current.length) {
            const identityOf = () => JSON.stringify([draftAccessRef.current, filesRef.current.map(item =>
              [item.id, item.name, item.size, sessionFor(item.id)])]);
            const identity = identityOf();
            const validSelection = () => {
              const selection = filesRef.current;
              return selection.length > 0 && selection.length <= cap.maxFiles
                && selection.every(item => {
                  const session = sessionFor(item.id);
                  const state: ProbedUploadSnapshot | undefined = session && uploadsRef.current[session.upload_id];
                  return session?.draftTaskId === access.draftTaskId && state?.probe_ok === true && transferComplete(state)
                    && (["uploading", "probing", "transcribing", "ready"].includes(state.status)
                      || state.status === "failed" && state.failure_kind === "asr" && state.can_materialize === true)
                    && Number.isFinite(state.sec) && state.sec! > 0 && state.sec! <= cap.maxDuration
                    && Number.isSafeInteger(state.width) && state.width! > 0 && state.width! <= 7680
                    && Number.isSafeInteger(state.height) && state.height! > 0 && state.height! <= 4320
                    && Number.isFinite(state.fps) && state.fps! > 0 && state.fps! <= 120
                    && item.duration === state.sec && item.duration != null && Number.isFinite(item.duration) && item.duration > 0 && item.duration <= cap.maxDuration
                    && Number.isSafeInteger(item.size) && item.size > 0 && item.size <= cap.maxBytes;
                })
                && selection.reduce((sum, item) => sum + item.duration!, 0) <= cap.maxTotalDuration
                && selection.reduce((sum, item) => sum + item.size, 0) <= cap.maxTotalBytes;
            };
            for (const item of filesRef.current) {
              if (!current() || jobs.current.length || identityOf() !== identity || !validSelection()) break;
              const session = sessionFor(item.id);
              const state: ProbedUploadSnapshot | undefined = session && uploadsRef.current[session.upload_id];
              // Retained ASR failures are bounded peers, never retry targets.
              if (!session || state?.status !== "uploading" || state.metadata_only !== true) continue;
              const completed = await completeUpload(session.upload_id, session.access_token, controller.signal, session);
              if (!current() || identityOf() !== identity) break;
              publishSnapshot(session.file_id, completed);
              if (["probing", "transcribing"].includes(completed.status)) observeUpload(session.file_id);
            }
          }
        } catch (error) {
          if (current()) {
            const reason = uploadErrorText(error);
            if (!probeDone) updateFile(itemId, { status: "error", error: reason });
            setUploadErrors(previous => ({ ...previous, [itemId]: reason }));
            if (isCapacityFailure(error)) {
              // Every queued file would be refused for the same reason: stop here instead of failing them one by one.
              const dropped = jobs.current.splice(0);
              for (const waiting of dropped) if (operations.current.get(waiting.itemId) === waiting.controller) operations.current.delete(waiting.itemId);
              if (dropped.length) setUploadErrors(previous => {
                const next = { ...previous };
                for (const waiting of dropped) next[waiting.itemId] = `没有开始上传：${reason}`;
                return next;
              });
              setUploadBlock(reason);
            }
          }
        } finally {
          if (operations.current.get(itemId) === controller) operations.current.delete(itemId);
          if (mounted.current) setHashProgress(previous => { const next = { ...previous }; delete next[itemId]; return next; });
        }
      }
    } finally { transferRunning.current = false; }
  }
  function retryFile(itemId: string) {
    if (locked || operations.current.has(itemId) || removalCancels.current.size) return;
    const item = filesRef.current.find(item => item.id === itemId);
    if (!item) return;
    const session = sessionFor(itemId), snapshot = session && uploadsRef.current[session.upload_id];
    if (session && snapshot?.status !== "uploading") { observeUpload(itemId); return; }
    if (!item.file && !snapshot?.chunks.length) { observeUpload(itemId); return; }
    polling.current.get(itemId)?.abort(); polling.current.delete(itemId);
    const controller = new AbortController(); operations.current.set(itemId, controller);
    setUploadErrors(previous => { const next = { ...previous }; delete next[itemId]; return next; });
    const identityOf = () => JSON.stringify([draftAccessRef.current, filesRef.current.map(item =>
      [item.id, item.name, item.size, sessionFor(item.id)])]);
    const identity = identityOf();
    jobs.current.push({ itemId, file: item.file, controller, probe: item.status !== "ready" && !!item.file,
      continuation: () => !removalCancels.current.size && identityOf() === identity });
    void drainFileJobs();
  }

  // Files that did not get through, and files that are fully uploaded but still waiting for the explicit "continue".
  const failedFiles = files.filter(item => !removing[item.id] && (uploadErrors[item.id] || item.status === "error"));
  // Only while the queue is idle: during a normal batch the completion step runs by itself at the end.
  const queueIdle = operations.current.size === 0 && jobs.current.length === 0 && !transferRunning.current;
  const waitingToFinish = !queueIdle ? [] : files.filter(item => {
    const state = uploadFor(item.id);
    return !removing[item.id] && state?.status === "uploading" && transferComplete(state);
  });
  function retryFailed() {
    if (locked || removalCancels.current.size) return;
    setUploadBlock("");
    // Files with no server session yet are queued exactly like a fresh selection (no "list unchanged" fence:
    // the first upload binds a session and would otherwise silently cancel every later retry).
    const fresh = failedFiles.filter(item => item.file && !sessionFor(item.id) && !operations.current.has(item.id));
    if (fresh.length) {
      setUploadErrors(previous => { const next = { ...previous }; for (const item of fresh) delete next[item.id]; return next; });
      for (const item of fresh) {
        const controller = new AbortController(); operations.current.set(item.id, controller);
        jobs.current.push({ itemId: item.id, file: item.file, controller, probe: item.status !== "ready" });
      }
      void drainFileJobs();
      return;
    }
    // Everything left already has a server session: one explicit continuation reconciles every peer first.
    const resumable = failedFiles.find(item => !operations.current.has(item.id));
    if (resumable) retryFile(resumable.id);
  }
  function removeFailed() {
    for (const item of failedFiles) removeFile(item.id);
    setUploadBlock("");
  }
  function continueUploaded() {
    const first = waitingToFinish[0];
    if (first) retryFile(first.id);
  }

  const pendingInitialFiles = useRef(initialFiles);
  useEffect(() => {
    const pending = pendingInitialFiles.current;
    pendingInitialFiles.current = undefined;
    if (pending?.length) void addFiles(pending);
    // Once per mount: a remount with a new key is how another draft is opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function addFiles(incoming: File[]) {
    if (locked || draftChecking || draftReadError) return;
    setAlignmentEnabled(true);
    setLocalError("");
    const errors: string[] = [];
    const accepted: FileJob[] = [];
    let next = [...filesRef.current];
    for (const file of incoming) {
      if (removalCancels.current.has(fileIdentity(file))) { errors.push(`${file.name}：请先撤销移除或等待移除完成。`); continue; }
      const problem = validateFileSelection(file, next, limits);
      if (problem) { errors.push(`${file.name}：${problem}`); continue; }
      const itemId = fileIdentity(file);
      const previous = next.find((item) => item.id === itemId);
      if (next.reduce((sum, item) => sum + item.size, 0) - (previous?.size ?? 0) + file.size > cap.maxTotalBytes) {
        errors.push(`${file.name}：加上已上传素材后总量超限。`); continue;
      }
      const extension = /\.[^.]+$/.exec(file.name)?.[0].toLowerCase();
      if (limits.allowed_extensions && (!extension || !limits.allowed_extensions.includes(extension))) {
        errors.push(`${file.name}：服务器当前不允许这个格式。`); continue;
      }
      const item: MediaSelection = { id: itemId, file, name: file.name, size: file.size, lastModified: file.lastModified,
        type: file.type, kind: mediaKind(file.name)!, duration: null, note: previous?.note ?? "",
        trim_start: previous?.trim_start ?? 0, trim_end: previous?.trim_end ?? null, status: "loading" };
      const controller = new AbortController();
      operations.current.get(itemId)?.abort(); polling.current.get(itemId)?.abort(); polling.current.delete(itemId);
      operations.current.set(itemId, controller);
      next = previous ? next.map((old) => old.id === itemId ? item : old) : [...next, item];
      accepted.push({ file, itemId, controller, probe: true });
    }
    if (accepted.length) {
      publishFiles(next); setSentenceChecks({}); invalidatePreview();
      const nextUploads = { ...uploadsRef.current };
      for (const job of accepted) { const session = sessionFor(job.itemId); if (session) delete nextUploads[session.upload_id]; }
      uploadsRef.current = nextUploads; setUploads(nextUploads);
    }
    if (errors.length) reportError(errors.join("\n"));
    // Across repeated picker actions, only one decoder/hash/transfer is active.
    // Transcription polling is independent and never disables manuscript editing.
    jobs.current.push(...accepted); void drainFileJobs();
  }

  function clearVoice() {
    voiceController.current?.abort(); voiceController.current = null;
    releaseUrl(voiceUrl); setVoiceUrl(""); setOwnVoice(undefined); setVoiceMeta(null); setVoiceLoading(false); setNotice("");
  }

  function acceptVoice(file: File, duration: number) {
    if (!file.size || file.size > cap.maxBytes || !Number.isFinite(duration) || duration <= 0 || duration > cap.maxDuration) {
      reportError(`录音无效或超过限制（${formatBytes(cap.maxBytes)} / ${formatDuration(cap.maxDuration)}），请重新录制。`); return false;
    }
    const url = URL.createObjectURL(file); urls.current.add(url);
    releaseUrl(voiceUrl); setVoiceUrl(url); setOwnVoice(file);
    setVoiceMeta({ id: fileIdentity(file), name: file.name, size: file.size, lastModified: file.lastModified, type: file.type,
      kind: "audio", duration, note: "整篇播音", trim_start: 0, trim_end: duration });
    return true;
  }

  async function uploadVoice(file: File) {
    if (locked || audioBusy) return;
    clearVoice();
    if ((!/^audio\//.test(file.type) && !/\.(mp3|wav|m4a|ogg|webm|aac)$/i.test(file.name)) || !file.size || file.size > cap.maxBytes) {
      reportError(`请选择有效音频文件，单文件不超过 ${formatBytes(cap.maxBytes)}。`); return;
    }
    const controller = new AbortController(); voiceController.current = controller; setVoiceLoading(true);
    try {
      const metadata = await probeMedia(file, "audio", controller.signal);
      if (mounted.current && !controller.signal.aborted) acceptVoice(file, metadata.duration);
    } catch (error) { if (!controller.signal.aborted) reportError(errorMessage(error)); }
    finally { if (mounted.current && voiceController.current === controller) { voiceController.current = null; setVoiceLoading(false); } }
  }

  function cancelRecording() {
    recordingGeneration.current++;
    const active = recorder.current; recorder.current = null;
    if (active) {
      active.ondataavailable = null; active.onstop = null; active.onerror = null;
      if (active.state !== "inactive") active.stop();
    }
    stopTracks(); setRecording(false); setRequestingMic(false); setRecordFinishing(false); setRecordSeconds(0);
    setNotice("录音已取消，没有保存或上传。");
  }

  function finishRecording() {
    if (!recorder.current || recorder.current.state === "inactive") return;
    setRecordFinishing(true); setRecording(false);
    recorder.current.stop(); stopTracks();
  }

  async function startRecording() {
    if (!recordSupported || locked || audioBusy) return;
    clearVoice(); setRequestingMic(true); setLocalError("");
    const generation = ++recordingGeneration.current;
    try {
      const input = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current || generation !== recordingGeneration.current) { input.getTracks().forEach((track) => track.stop()); return; }
      stream.current = input;
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find((type) => MediaRecorder.isTypeSupported(type));
      const active = new MediaRecorder(input, mime ? { mimeType: mime } : undefined);
      recorder.current = active;
      const chunks: Blob[] = [];
      let byteCount = 0;
      const started = performance.now();
      active.ondataavailable = (event) => {
        if (!event.data.size || generation !== recordingGeneration.current) return;
        byteCount += event.data.size; chunks.push(event.data);
        if (byteCount > cap.maxBytes) { cancelRecording(); reportError("录音超过文件大小上限，已取消。请缩短稿件后重录。"); }
      };
      active.onerror = () => { cancelRecording(); reportError("麦克风录音中断，没有生成完整录音。请检查设备后重试。"); };
      active.onstop = () => {
        stopTracks(); recorder.current = null;
        if (!mounted.current || generation !== recordingGeneration.current) return;
        setRecording(false); setRecordFinishing(false);
        const type = active.mimeType || chunks[0]?.type || "audio/webm";
        const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        const file = new File(chunks, `我的整篇播音-${Date.now()}.${extension}`, { type });
        if (acceptVoice(file, (performance.now() - started) / 1000)) {
          setNotice("录音已保存在本页，请试听确认整篇内容。尚未上传或完成配音对齐。");
        }
      };
      active.start(1000); setRecording(true); setRequestingMic(false); setRecordSeconds(0);
      recordingTimer.current = setInterval(() => {
        const seconds = (performance.now() - started) / 1000;
        setRecordSeconds(Math.floor(seconds));
        if (seconds >= cap.maxDuration - 1) { cancelRecording(); reportError("录音达到时长上限，已取消。请缩短稿件后重新录制。"); }
      }, 250);
    } catch (error) {
      if (generation !== recordingGeneration.current || !mounted.current) return;
      stopTracks(); setRequestingMic(false); setRecording(false);
      reportError(error instanceof DOMException && error.name === "NotAllowedError"
        ? "麦克风权限未获允许。可在浏览器设置中允许，或直接选择整篇录音文件。"
        : `无法开始录音：${errorMessage(error)} 可改用音频文件上传。`);
    }
  }

  async function submit() {
    if (!canSubmit || submitLock.current) return;
    submitLock.current = true; setSubmitting(true); setLocalError("");
    try {
      await onSubmit({ script: script.trim(), files: files.flatMap(item => item.file ? [item.file] : []),
        preferences: { ...preferences, voice: usingOwnVoice ? "mine" : "ai", generative_fill: mode !== "original" && generativeAllowed && preferences.generative_fill } as ModePreferences,
        ...(sourceVoicePreferred === undefined ? {} : { source_voice_preferred: sourceVoicePreferred }),
        asset_options: files.map((item) => ({ note: item.note.trim(), trim_start: item.kind === "image" ? 0 : item.trim_start, trim_end: item.kind === "image" ? null : item.trim_end })),
        mode, sentenceKinds, speakers, sentences: analysis.rows, uploadIds: orderedUploadIds,
        uploadTokens: readUploadTokens(tokensRef.current, orderedUploadIds),
        ...draftAccessRef.current,
        ...(initialDraft?.legacyRestore === true ? { legacyRestore: true } : {}),
        ...(usingWholeVoice ? { ownVoice } : {}),
      });
    } catch (error) { reportError(errorMessage(error)); }
    finally { submitLock.current = false; if (mounted.current) setSubmitting(false); }
  }

  const blockedReason = !serviceReady ? "制作服务未就绪；仍可写稿、选择素材并保存草稿。" : !scriptValid ? scriptProblem
    : issues[0] || matchingProblem || (audioBusy ? "请先完成或取消录音 / 音频读取。" : !voiceValid ? "请重新选择或录制有效的整篇配音。"
      : !totalBytesValid ? "素材和录音合计超过上传总量限制。" : !totalDurationValid ? "素材和录音合计超过总时长限制。"
        : preferences.custom_instructions.length > 500 ? "剪辑要求不能超过 500 字。" : "");

  function transcriptList(upload: UploadSnapshot, compact = true) {
    const RowContainer = mode === "original" ? "label" : "div";
    const row = (segment: TranscriptSegment) => <RowContainer className="gm-transcript-line" key={segment.id}>
      {mode === "original" ? <input type="checkbox" aria-label={`选入原话：${segment.text}`}
        checked={analysis.rows.some(sentence => sentence.source_hint?.upload_id === upload.id && sentence.source_hint.seg_id === segment.id)}
        onChange={event => pickTranscript(upload.id, segment, event.target.checked)} /> : <span aria-hidden="true">·</span>}
      <span className="gm-clock">{clock(segment.start)}–{clock(segment.end)}</span>
      <span className="gm-person-badge">{speakers.find(person => person.id === segment.speaker_id)?.name || speakers.find(person => person.id === segment.speaker_id)?.auto_label || "受访者"}</span>
      <span>{segment.text}</span>
    </RowContainer>;
    return <div className="gm-transcript">{upload.transcript.slice(0, compact ? 8 : undefined).map(row)}
      {compact && upload.transcript.length > 8 && <details open={!!expandedTranscripts[upload.id]} onToggle={event => {
        const open = event.currentTarget.open; setExpandedTranscripts(current => current[upload.id] === open ? current : { ...current, [upload.id]: open });
      }}><summary>再看 {upload.transcript.length - 8} 句转写</summary>{expandedTranscripts[upload.id] && upload.transcript.slice(8).map(row)}</details>}
    </div>;
  }
  const pickGroups = files.flatMap(file => {
    const upload = uploadFor(file.id);
    if (upload?.status !== "ready") return [];
    const transcript = upload.transcript.filter(segment => segment.text.trim());
    return transcript.length ? [{ file, upload: { ...upload, transcript } }] : [];
  });
  const pickAvailable = pickGroups.reduce((count, { upload }) => count + upload.transcript.length, 0);
  const pickedCount = pickGroups.reduce((count, { upload }) => count + upload.transcript.filter(segment =>
    analysis.rows.some(sentence => sentence.source_hint?.upload_id === upload.id && sentence.source_hint.seg_id === segment.id)).length, 0);
  const transcriptPicker = mode === "original" && pickAvailable > 0
    ? <details className="gm-transcript-picker" open={pickOpen} onToggle={event => setPickOpen(event.currentTarget.open)}>
      <summary className="gm-pick-summary"><strong>从转写挑句子</strong>{" "}<span className="gm-muted gm-small">已挑 {pickedCount} 句 / 可选 {pickAvailable} 句</span>{" "}<span className="gm-pick-action">{pickOpen ? "收起" : "展开"}</span></summary>
      <p className="gm-muted gm-small">勾选按点击顺序追加到稿末；取消只移除这个出处的句子。首行仍需填写真实标题。转写可能听错，请结合原素材核对。</p>
      {pickGroups.map(({ file, upload }) => <details key={file.id} open><summary>{file.name} · {upload.transcript.length} 句</summary>{transcriptList(upload)}</details>)}
    </details> : null;
  const speakerEditor = mode !== "voiceover" && speakers.length > 0 ? <section className="gm-speakers"><h2>说话人</h2>
    <p className="gm-muted gm-small">请核实姓名与身份；未填写时人名条显示“受访者”。自动分人不代表身份已认证。</p>
    {speakers.map((person, index) => <div className="gm-speaker-row" data-unnamed={!person.name.trim()} key={person.id}>
      <span className="gm-speaker-number" aria-hidden="true">{index + 1}</span>
      <label><span className="gm-sr-only">{person.auto_label || `说话人 ${index + 1}`}的姓名</span><input value={person.name} maxLength={8} placeholder="姓名"
        onChange={event => { speakerHintsApplied.current.add(person.id); setSpeakers(current => current.map(item => item.id === person.id ? { ...item, name: event.target.value } : item)); }} /></label>
      <label><span className="gm-sr-only">{person.auto_label || `说话人 ${index + 1}`}的身份</span><input value={person.title} maxLength={12} placeholder="身份，如：主办方"
        onChange={event => { speakerHintsApplied.current.add(person.id); setSpeakers(current => current.map(item => item.id === person.id ? { ...item, title: event.target.value } : item)); }} /></label>
      <span className="gm-muted gm-small">出现 {person.appearances} 次 · {formatDuration(person.seconds)}</span>
    </div>)}
  </section> : null;
  const matchCards = mode !== "voiceover" ? <section className="gm-match-card"><div className="gm-row"><h2>{mode === "mixed" ? "原话都拍到了吗？" : "原话对上了吗？"}</h2>
    <span className="gm-muted gm-small">{quoteRows.length} 句原话 · {quoteRows.filter(sentence => matchState(matches.find(match => match.idx === sentence.idx)) === "ok").length} 句对上</span>
    {mode === "original" && <button type="button" className="gm-small-button" onClick={() => { setPickOpen(true); goToStep(1); }}>从转写挑句子</button>}
    <button type="button" className="gm-small-button gm-push" disabled={preview.pending || readyUploadIds.length !== files.length || !files.length}
      onClick={() => { setAlignmentEnabled(true); invalidatePreview(); setPreviewRetry(value => value + 1); }}>重新核对</button></div>
    <p className="gm-muted gm-small">85% 起为“对上了”，60%–85% 为“不太确定”，低于 60% 或无出处不能继续。分数来自服务器文本匹配，不是声音质量或事实认证。</p>
    {preview.error && preview.key === previewKey && <p className="gm-error" role="alert">原话核对失败：{preview.error} 不会重复转写素材；可点“重新核对”。</p>}
    {!quoteRows.length && <p className="gm-muted">{mode === "mixed" ? "暂无原声句，可回写稿把现场原话标成“同期”。" : "先上传采访，从转写里挑选至少一句原话。"}</p>}
    {quoteRows.map(sentence => {
      const match = matches.find(row => row.idx === sentence.idx), state = matchState(match), source = match?.source;
      return <article key={sentence.idx} className="gm-match-row" data-state={state}>
        <b className="gm-clock">{sentence.idx + 1}</b><p>{sentence.text}</p>
        <p className="gm-muted gm-small">{source ? `${uploads[source.upload_id]?.name ?? "已上传素材"} · ${clock(source.start)}–${clock(source.end)} · ${speakers.find(person => person.id === source.speaker_id)?.name || "受访者"}` : state === "pending" ? "等待转写和原话核对" : "没有可用的原声出处"}</p>
        <span className="gm-match-state">{({ pending: "○ 待核对", missing: "✕ 没找到", low: "○ 不太确定", ok: "✓ 对上了" })[state]}{match && <small className="gm-clock">{(match.score * 100).toFixed(1)}%</small>}</span>
        {source && <details className="gm-match-detail"><summary>核对实际说的话{source.precision === "segment" ? " · 仅有整段时间" : " · 有词时间"}</summary>
          <p>{source.asr_text}</p><p className="gm-muted gm-small">{state === "low" ? "请在原素材中试听；低分待确认项仍会进入成片检查。" : "文字对上不等于口型、切点或新闻事实已核验。"}
            {source.precision === "segment" ? " 没有完整词时间，不能承诺按字裁剪；实际区间可能包括前后文。" : " 按字剪短只能选连续词段，不能拼接中间删词。"}</p>
        </details>}
        {state === "missing" && <div className="gm-match-fixes">{mode === "mixed" && <button type="button" className="gm-small-button" onClick={() => changeKind(sentence.idx, "narration")}>改成旁白</button>}
          <button type="button" className="gm-small-button" onClick={() => commitSentences(analysis.rows.filter(row => row.idx !== sentence.idx))}>删掉这句</button>
          {mode === "original" && <button type="button" className="gm-small-button" onClick={() => { setPickOpen(true); goToStep(1); }}>从转写里挑</button>}
          <span className="gm-muted gm-small">也可以补传确实说过这句话的素材。</span></div>}
      </article>;
    })}
    {mode === "original" && quotesComplete && quoteRows.length > 0 && <p className="gm-jump-preview">按当前出处预计 {jumpCuts} 处跳切。第 3 步可选遮盖方式；是否有可用空镜由正式镜头分析确认。</p>}
    {matchingProblem && <p className="gm-danger-text gm-small">{matchingProblem}</p>}
  </section> : null;

  return <section className="gm-create-wizard" aria-label="新建新闻作品" aria-busy={locked}>
    <nav className="gm-steps" aria-label="制作步骤">{(["写稿", "传素材", "选效果"] as const).map((label, index) => {
      const number = (index + 1) as 1 | 2 | 3;
      return <button key={label} type="button" aria-current={step === number ? "step" : undefined} data-complete={number < step}
        disabled={!canGoToStep(number)} onClick={() => goToStep(number)}>
        <span className="gm-step-number">{number < step ? <Icon name="check" /> : number}</span><span>{label}</span></button>;
    })}</nav>
    {step === 1 && intro}
    {localError && <div className="gm-error" role="alert">{localError}</div>}
    <div className="gm-sr-only" role="status" aria-live="polite">{notice}</div>
    <fieldset className="gm-wizard-fieldset" disabled={locked}>
      <legend className="gm-sr-only">填写新闻稿、素材与效果</legend>
      <div className="gm-wizard-card">
        <span className="gm-step-badge">第 {step} 步</span>
        <header className="gm-card-heading"><h1 ref={heading} tabIndex={-1}>{["写稿", "传素材", "选效果"][step - 1]}</h1>
          <p>{step === 1 ? mode === "mixed" ? "第一行是标题；人说的话写成「同期 姓名（身份）：…」。" : mode === "original" ? "第一行是标题；下面每行一句原话。" : "第一行是标题。"
            : step === 2 ? mode === "voiceover" ? "为稿件内容准备真实、相关的画面。" : "上传真实采访，对上原话和说话人。" : "按制作需求选择字幕和画面效果。"}</p></header>

        {step === 1 && <>
          <div id={`${id}-modes`} className="gm-mode-cards" role="group" aria-label="制作模式">{MODE_CARDS.map(card => <button type="button" key={card.mode} aria-pressed={mode === card.mode}
            className="gm-mode-card" disabled={audioBusy} onClick={() => chooseMode(card.mode)}>
            <span className="gm-mode-card-title"><span className="gm-mode-icon" aria-hidden="true"><Icon name={card.mode === "voiceover" ? "mic" : card.mode === "mixed" ? "mic-bubble" : "bubble"} /></span><strong>{card.name}</strong></span>
            {card.mode === "voiceover" && <span className="gm-mode-stamp">最简单，先用这个</span>}
            <span className="gm-mode-description">{card.description}</span><span className="gm-mode-fit">适合：{card.fit}</span>
            {mode === card.mode && <span className="gm-mode-check" aria-hidden="true"><Icon name="check" /></span>}
          </button>)}</div>
          {transcriptPicker}
          <label className="gm-sr-only" htmlFor={`${id}-script`}>新闻稿（第一行标题，下一行正文）</label>
          <textarea id={`${id}-script`} ref={scriptBox} className="gm-script" value={script} onChange={(event) => changeScript(event.target.value)}
            placeholder={mode === "original" ? "把从采访里挑出来的原话贴到这里，一句一行。还没有稿？先去第 2 步传素材，转写完回来挑句子" : mode === "mixed" ? "直接粘贴稿子到这里，或用下面的 AI 助手写旁白；受访者的话写成「同期 王红（市集主办方）：…」" : "直接粘贴新闻稿到这里，或自己写；也可以用下面的 AI 写作助手帮你写一稿……"} aria-describedby={`${id}-script-hint ${id}-gate`} aria-invalid={charCount > 0 && !scriptValid} />
          <div className="gm-row gm-script-status" id={`${id}-script-hint`} aria-live="polite">
            <strong className={analysis.title ? "gm-teal-text" : charCount ? "gm-danger-text" : "gm-muted"}>{analysis.title ? `标题：${analysis.title}` : charCount ? analysis.titleError : "第一行会当作标题"}</strong>
            {analysis.titleFixable && <button type="button" className="gm-small-button" onClick={() => changeScript(analysis.fixedScript)}>去掉首行句末标点，当标题</button>}
            <span className={charCount > cap.maxScript ? "gm-danger-text gm-push" : "gm-muted gm-push"}>{charCount} / {MODE_LIMITS.script_soft_chars} 字</span>
          </div>
          {scriptAssistant?.({ mode, script, disabled: locked || audioBusy, maxScript: cap.maxScript, onApply: changeScript })}
          {charCount > MODE_LIMITS.script_soft_chars && <p className="gm-warning">稿件超过 {MODE_LIMITS.script_soft_chars} 字的建议长度，建议精简；服务器硬上限 {cap.maxScript} 字。</p>}
          {analysis.longSentences.length > 0 && <div className="gm-warning">有 {analysis.longSentences.length} 句超过 30 字：{analysis.longSentences.slice(0, 6).map((item) => `第 ${item.index + 1} 句 ${item.chars} 字`).join("、")}。建议在意思完整的地方拆句。</div>}
          {mode !== "original" && script.trim() && <section className="gm-elements"><div className="gm-element-summary"><h2>五要素</h2>
            {ELEMENT_RULES.map(({ key, label }) => <button key={key} type="button" className="gm-element-chip" data-confirmed={confirmedElements.has(key)}
              aria-pressed={confirmedElements.has(key)} onClick={() => setElements(current => ({ ...current, [key]: {
                confirmed: !confirmedElements.has(key), evidence: analysis.sentences.some((text, index) => sentenceKey(text, index) === current[key]?.evidence)
                  ? current[key]!.evidence : wholeScriptEvidence,
              } }))}>
              <span aria-hidden="true">{confirmedElements.has(key) ? "✓" : "○"}</span>{label}<span className="gm-sr-only">：{confirmedElements.has(key) ? "已自行核对，点击取消" : "待自行核对，点击确认"}</span>
            </button>)}<span className="gm-muted gm-small">{confirmedElements.size} / 5 已自查 · 不是事实认证</span></div>
            <details id={`${id}-element-details`} className="gm-evidence-details" open={elementDetailsOpen} onToggle={event => setElementDetailsOpen(event.currentTarget.open)}>
              <summary>稿件依据与逐项自查 <span className="gm-muted gm-small">{elementDetailsOpen ? "可收起" : "展开选择依据与核对"}</span></summary>
              <p className="gm-muted gm-small">可选辅助，不影响提交。点击要素表示你已自行核对整篇稿件，也可选一句作为依据。关键词提示可能漏检或误判，不会自动补写或认证事实。</p>
              {ELEMENT_RULES.map(({ key, label, rule }) => {
                const mark = elements[key] ?? { confirmed: false, evidence: "" };
                const suggested = rule.test(analysis.body);
                return <div className="gm-evidence-row" key={key}>
                  <b>{label}</b><span className="gm-muted gm-small">{suggested ? "可能已提到" : "未检出，可手动选"}</span>
                  <select aria-label={`${label}的稿件依据`} value={mark.evidence} onChange={(event) => setElements((current) => ({ ...current, [key]: { evidence: event.target.value, confirmed: false } }))}>
                    <option value="">选择一句作为依据</option><option value={wholeScriptEvidence}>整篇稿件（手动自查）</option>{analysis.sentences.map((text, index) => <option key={index} value={sentenceKey(text, index)}>{index + 1}. {text}</option>)}
                  </select>
                  <label className="gm-inline-check"><input type="checkbox" checked={mark.confirmed} disabled={!mark.evidence}
                    onChange={(event) => setElements((current) => ({ ...current, [key]: { ...mark, confirmed: event.target.checked } }))} />我已核对</label>
                </div>;
              })}
            </details>
          </section>}
          {mode === "voiceover" && analysis.sentences.length > 0 && <section className="gm-shot-plan">
            <div className="gm-row"><h2>拍摄清单 · {analysis.sentences.length} 条</h2><span className="gm-muted gm-small">可选 · 按稿件准备对应画面</span>
              <button className="gm-small-button gm-push" type="button" aria-expanded={shotListOpen} onClick={() => setShotListOpen(!shotListOpen)}>{shotListOpen ? "收起" : "展开"}</button>
              <button className="gm-small-button" type="button" onClick={async () => {
                const copied = await copyText(shotText); if (!mounted.current) return;
                setManualCopy(!copied); setNotice(copied ? "拍摄清单已复制，可粘贴发送到手机。" : "自动复制不可用，请选择下方文字手动复制。");
              }}>复制清单</button></div>
            {shotListOpen && <ol className="gm-shot-list">{analysis.sentences.map((text, index) => <li key={index}><p>{text}</p><strong>{shotAdvice(text)}</strong></li>)}</ol>}
            {manualCopy && <label className="gm-field-label">自动复制不可用，请全选后复制<textarea readOnly value={shotText} onFocus={(event) => event.currentTarget.select()} /></label>}
          </section>}
          {mode !== "voiceover" && analysis.rows.length > 0 && <section className="gm-sentence-plan"><h2>句子清单</h2>
            <p className="gm-muted gm-small">{summaryLine}</p>
            {analysis.rows.map(sentence => {
              const match = matches.find(row => row.idx === sentence.idx), state = matchState(match);
              const sourceText = sentence.source_hint ? uploads[sentence.source_hint.upload_id]?.transcript.find(segment => segment.id === sentence.source_hint?.seg_id)?.text : match?.source?.asr_text;
              const unverified = mode === "original" && (state === "missing" || sourceText !== undefined && quoteTextEvidence(sentence.text, sourceText).unverified);
              return <article key={sentence.idx} className="gm-sentence-row" data-kind={sentence.kind}>
                <b className="gm-clock">{sentence.idx + 1}</b><div><p className={unverified ? "gm-unverified-quote" : ""}>{sentence.text}</p>
                  {sentence.speaker_hint && <span className="gm-muted gm-small">{sentence.speaker_hint}</span>}
                  {unverified && <p className="gm-danger-text gm-small">红线内容未能在这个出处中核实为连续原话，可能有新增词或中间删词。请从转写重新选择，不能任意删词后拼出新的发言。</p>}
                  {sentence.kind === "quote" && countSpokenChars(sentence.text) > 60 && <p className="gm-warning">这句原声超过 60 字，建议在完整意思处断成两句；实际时长以转写出处为准。</p>}
                </div>
                {mode === "mixed" ? <button type="button" className="gm-kind-chip" data-kind={sentence.kind} aria-pressed={sentence.kind === "quote"}
                  aria-label={`第 ${sentence.idx + 1} 句：${sentence.kind === "quote" ? "原声，改成旁白" : "旁白，改成原声"}`}
                  onClick={() => changeKind(sentence.idx, sentence.kind === "quote" ? "narration" : "quote")}>{sentence.kind === "quote" ? "原声" : "旁白"}</button>
                  : <span className="gm-match-state" data-state={state}>{({ pending: "○ 待核对", missing: "✕ 没找到", low: "○ 不太确定", ok: "✓ 对上了" })[state]}</span>}
              </article>;
            })}
            {mode === "mixed" && (narrationRows.length === 0 || quoteRows.length === 0) && <p className="gm-warning">{quoteRows.length === 0 ? "目前全是旁白，也可以使用“AI 配音”模式。" : "目前全是原声，也可以使用“只用原声”模式。"} 这只是建议，不会自动切换。</p>}
          </section>}
          <details className="gm-tip"><summary><Icon name="pencil" /> <b>小贴士</b><span>{mode === "voiceover" ? "导语一句说清：何时、何地、何人、何事、为何。" : mode === "mixed" ? "旁白由 AI 读，「同期」开头的句子会从素材里剪出原声——说过的话不能改，不确定原话就先去传素材。" : "这个模式不配音：稿子里的每句都要是素材里真说过的话。"}</span><span className="gm-tip-more">更多</span><span className="gm-tip-less">收起</span></summary><ul><li>导语一句说清：何时、何地、何人、何事、为何。</li><li>一句话别超过 30 个字——太长会读得赶，画面也撑不住。</li><li>写了几样东西，就要拍到几样：“腊肉、糕点、礼盒”要三个镜头。</li><li>数字写阿拉伯数字，制作后请核对实际读音。</li></ul></details>
        </>}

        {step === 2 && <>
          <div className="gm-mobile-tip">手机上传大视频可能较慢，建议电脑传素材，手机看片和提意见。</div>
          <div className="gm-cloud-notice" id={`${id}-upload-disclosure`}><p><b>选择文件即开始上传和转写。</b>可能调用第三方 AI 并产生费用；请仅上传已获授权、不含敏感隐私的素材。</p><details><summary>隐私与处理说明</summary><p>选中或拖入文件后，本页会先检查格式和时长，再分片上传并请求转写。音频、文本或抽帧可能发送到已配置的第三方 AI 服务，不必等到“开始制作”。请核实必要的肖像与声音授权。</p></details></div>
          <label className={`gm-dropzone${locked ? " gm-is-disabled" : ""}`} onDragOver={(event) => event.preventDefault()} onDrop={(event) => {
            event.preventDefault(); if (!locked) void addFiles(Array.from(event.dataTransfer.files));
          }}>
            <input className="gm-file-picker" type="file" multiple accept={MEDIA_ACCEPT} disabled={locked} aria-label="选择视频或照片" aria-describedby={`${id}-upload-disclosure`} onChange={(event) => {
              void addFiles(Array.from(event.target.files ?? [])); event.target.value = "";
            }} />
            <strong><Icon name="cloud-up" /> 把视频或照片拖进来，或点击选择</strong>
            <span>mp4 · mov · avi · mkv · jpg · png · gif（图片按 3 秒估算）</span>
          </label>
          <p className="gm-muted gm-small">最多 {cap.maxFiles} 个 · 单文件 {formatBytes(cap.maxBytes)} / {formatDuration(cap.maxDuration)} · 总量 {formatBytes(cap.maxTotalBytes)} / {formatDuration(cap.maxTotalDuration)}。格式支持不等于浏览器能解码，服务端还会复核。</p>
          {files.some((item) => item.status === "reselect") && <div className="gm-warning">已恢复文件清单。带上传凭据的素材正在核实服务器状态；上传完整后无需重选。未上传或缺少分片的素材需重选同一个原文件，不能只凭文件名认作已上传。</div>}
          {(failedFiles.length > 0 || (waitingToFinish.length > 0 && !locked)) && <div className={failedFiles.length ? "gm-warning gm-upload-summary" : "gm-cloud-notice gm-upload-summary"} role={failedFiles.length ? "alert" : "status"} data-testid="upload-summary">
            {failedFiles.length > 0 ? <>
              <p><b>有 {failedFiles.length} 个素材没有传成。</b>{uploadBlock ? ` 原因：${uploadBlock}` : " 每个素材下面写着具体原因。"}</p>
              <p className="gm-small">没传成的素材会挡住“下一步”。先处理原因，再重试；也可以直接移除它们，用已经传好的素材继续。</p>
              <div className="gm-row"><button type="button" className="gm-small-button" data-control="upload-retry-failed" disabled={locked} onClick={retryFailed}>重试这些素材</button>
                <button type="button" className="gm-small-button" data-control="upload-remove-failed" disabled={locked} onClick={removeFailed}>移除这些素材</button></div>
            </> : <>
              <p><b>{waitingToFinish.length} 个素材已经传完，等你点一下继续处理。</b></p>
              <div className="gm-row"><button type="button" className="gm-small-button" data-control="upload-continue" onClick={continueUploaded}>继续处理已传好的素材</button></div>
            </>}
          </div>}
          <div className="gm-file-list">{files.filter(item => !removing[item.id]).map((item) => {
            const trimError = item.status === "ready" ? validateTrim(item) : null;
            const durationError = item.duration != null && item.duration > cap.maxDuration;
            const upload = uploadFor(item.id), uploadError = uploadErrors[item.id], hash = hashProgress[item.id];
            const thumbnail = item.thumbnail || (upload && uploadTokens[upload.id] ? uploadMediaUrl(upload, uploadTokens[upload.id], "thumb", sessionFor(item.id)) : null);
            const stateText = !upload ? hash ? `正在计算文件摘要 · ${formatBytes(hash.bytes)} / ${formatBytes(hash.total)}` : item.status === "reselect" ? "等待核实或重选原文件" : "等待上传"
              : upload.status === "uploading" ? `已确认上传 ${upload.progress.toFixed(1)}% · ${upload.chunks.length} 个分片`
                : upload.status === "probing" ? "正在检查素材…" : upload.status === "transcribing" ? "正在听采访并转写…"
                  : upload.status === "failed" ? uploadUsable(upload) ? "转写失败，画面已通过检查；不能充当原话出处" : "素材预处理失败"
                    : upload.has_speech === true ? `有人说话 · 转写 ${upload.transcript.length} 句` : upload.has_speech === null ? "已处理 · 人声情况未确认" : "已处理 · 没有可用人声";
            return <article className={`gm-media-item${item.status === "error" || trimError || durationError ? " gm-media-invalid" : ""}`} key={item.id}>
              <div className="gm-media-thumb">{thumbnail ? <img src={thumbnail} alt={`${item.name} 首帧预览`} referrerPolicy="no-referrer" /> : <span>{item.kind === "image" ? "照片" : "视频"}<small>{item.status === "ready" ? "无预览" : "待校验"}</small></span>}</div>
              <div className="gm-media-info"><h3 title={item.name}>{item.name}</h3>
                <p className="gm-muted gm-small">{formatBytes(item.size)} · {item.status === "reselect" ? "原文件不在本页" : item.status === "loading" ? "正在读取时长和首帧…" : item.status === "error" ? item.error : `${formatDuration(item.duration ?? 0)}${item.width && item.height ? ` · ${item.width} × ${item.height}` : ""}${item.kind === "image" ? " · 图片按 3 秒估算" : ""}`}</p>
                <p className="gm-upload-state" role="status">{stateText}</p>
                {upload?.status === "uploading" && <progress max={100} value={upload.progress} aria-label={`${item.name} 已确认上传进度`} />}
                {upload?.error && <p className="gm-danger-text gm-small">这段素材暂时无法处理，请检查文件或换一份素材。</p>}
                {uploadError && <p className="gm-danger-text gm-small" role="alert">{uploadError}</p>}
                {(uploadError || upload) && <button type="button" className="gm-small-button" disabled={operations.current.has(item.id)} onClick={() => retryFile(item.id)}>
                  {upload?.status === "uploading" && (item.file || transferComplete(upload)) ? "核对分片并继续" : bindingFor(item.id) ? "重新读取状态" : "重新校验并上传"}</button>}
                {upload?.status === "uploading" && !item.file && !transferComplete(upload) && <p className="gm-warning">缺少文件分片，请在上方重新选择同一个原文件。</p>}
                <button type="button" className="gm-small-button gm-file-adv" aria-expanded={!!fileAdv[item.id]} onClick={() => setFileAdv(current => ({ ...current, [item.id]: !current[item.id] }))}>{fileAdv[item.id] ? "收起 ▴" : "备注 / 只用片段 ▾"}</button>
                {fileAdv[item.id] && <div className="gm-media-edit"><label className="gm-note-label"><span className="gm-sr-only">{item.name} 的备注</span><input value={item.note} placeholder="备注 / 标签（最多 20 字）" onChange={(event) => updateFile(item.id, { note: Array.from(event.target.value).slice(0, 20).join("") })} /></label>
                  {item.kind === "video" && item.status === "ready" && <div className="gm-trim"><span>只用</span>
                    <input aria-label={`${item.name} 裁剪开始秒数`} type="number" min={0} max={item.duration ?? undefined} step="1" value={Number.isFinite(item.trim_start) ? item.trim_start : ""} onChange={(event) => updateFile(item.id, { trim_start: event.target.value === "" ? NaN : Number(event.target.value) })} />
                    <span>–</span><input aria-label={`${item.name} 裁剪结束秒数`} type="number" min={0} max={item.duration ?? undefined} step="1" value={item.trim_end ?? ""} onChange={(event) => updateFile(item.id, { trim_end: event.target.value === "" ? null : Number(event.target.value) })} /><span>秒</span><button type="button" className="gm-small-button" onClick={() => updateFile(item.id, { trim_start: 0, trim_end: item.duration })}>使用完整片段</button></div>}
                  </div>}
                {(trimError || durationError) && <p className="gm-danger-text gm-small">{durationError ? "原始时长超限，请移除并在本地剪短后重新选择。" : trimError}</p>}
                {upload && upload.transcript.length > 0 && <details className="gm-file-transcript"><summary>查看转写 · {upload.transcript.length} 段</summary>{transcriptList(upload)}</details>}
              </div><button className="gm-remove" type="button" disabled={operations.current.has(item.id)} title={operations.current.has(item.id) ? "请等待本次传输完成后移除" : undefined} aria-label={`移除 ${item.name}`} onClick={() => removeFile(item.id)}>×</button>
            </article>;
          })}</div>
          {Object.entries(removing).map(([itemId, name]) => <div className="gm-removal-toast" role="status" key={itemId}>已移除「{name}」<button type="button" className="gm-small-button" disabled={!!deleting[itemId]} onClick={() => undoRemoval(itemId)}>撤销</button><span className="gm-small">{deleting[itemId] ? "正在确认移除…" : "5 秒内可撤销"}</span></div>)}
          <div className="gm-capacity"><b>{files.length} / {cap.maxFiles} 个素材 · {readyUploadIds.length} 个已预处理</b><progress max={cap.maxTotalBytes} value={fileBytes} aria-label="素材占用总量" /><span>{formatBytes(fileBytes)} / {formatBytes(cap.maxTotalBytes)} · {formatDuration(sourceSeconds)}</span></div>
          {mode === "voiceover" && readyUploadIds.some(uploadId => uploads[uploadId].has_speech) && <div className="gm-warning">这个模式不用素材人声作旁白，只用画面。想用现场原话？<button type="button" className="gm-small-button" onClick={() => chooseMode("mixed")}>切到“旁白 + 原声”</button></div>}
          {mode === "original" && <>{transcriptPicker}<button type="button" className="gm-small-button" onClick={() => goToStep(1)}>← 回写稿检查标题和原话</button></>}
          {matchCards}{speakerEditor}
          {noSpeech && <p className="gm-error" role="alert">这批素材没有可用人声。“只用原声”不能制作，请上传采访素材。</p>}
          {mode !== "original" && !!files.length && <section className="gm-sufficiency"><div className="gm-row"><h2>拍摄充足度 <span>仅作估算</span></h2><strong className={estimate.enough ? "gm-teal-text" : "gm-danger-text"}>{estimate.enough ? "数量 / 时长初估够用" : "建议再补拍"}</strong></div>
            <progress max={100} value={estimate.percent} aria-label="拍摄充足度估算" />
            <p className="gm-small">旁白约 {formatDuration(estimate.narrationSeconds)}，素材可用约 {formatDuration(estimate.seconds)}；普通视频按每 12 秒约 1 个镜头、逐个向下取整且至少 1 个，照片和已确认有人声的视频各按 1 个；初估 {estimate.possibleShots} 个，旁白 {narrationRows.length} 句。{mode === "mixed" && `另有原声 ${quoteRows.length} 段${quotesComplete ? `，约 ${formatDuration(quoteSeconds)}` : "，时长待匹配"}。`}</p>
            {estimate.unknownSpeechVideos > 0 && <p className="gm-muted gm-small">{estimate.unknownSpeechVideos} 个视频人声状态未确认，暂按普通视频估算，不代表无人声；确认后估算可能降低。</p>}
            <p className="gm-muted gm-small">这不是镜头检测或内容匹配；一个长视频不一定有多个不同镜头。实际可用镜头须由服务端分析，可能仍需补拍，不能保证制作成功。</p>
            <h3>每句话都拍到了吗？ <span className="gm-muted gm-small">可选自查，不是 AI 已确认</span></h3>
            <div className="gm-sentence-checks">{narrationRows.slice(0, sentListOpen ? 60 : 12).map(({ text, idx: index }) => <button type="button" className="gm-check-row" key={sentenceKey(text, index)}
              onClick={() => setSentenceChecks(current => { const next = { ...current }, key = sentenceKey(text, index); if (!(key in next)) next[key] = true; else if (next[key]) next[key] = false; else delete next[key]; return next; })}>
              <span><b>{index + 1}.</b> {text}</span><strong>{sentenceChecks[sentenceKey(text, index)] === true ? "拍到了 ✓" : sentenceChecks[sentenceKey(text, index)] === false ? "没拍到 ✕" : "点一下确认"}</strong>
            </button>)}</div>
            {narrationRows.length > 12 && <button type="button" className="gm-small-button" aria-expanded={sentListOpen} onClick={() => setSentListOpen(value => !value)}>{sentListOpen ? "收起" : `展开全部 ${Math.min(60, narrationRows.length)} 句`}</button>}
            {narrationRows.length > 60 && <p className="gm-muted gm-small">这里仅列前 60 句，完整稿件仍会参与制作。</p>}
          </section>}
          <p className="gm-muted gm-small">请选择有权使用的素材；涉及可识别的人物或声音时，请自行核实必要授权并保护隐私。本页不会自动核验素材权利。</p>
          <details className="gm-tip"><summary><Icon name="clapper-mic" /> 小贴士 · 多拍一点不同的画面</summary><ul><li>远景、中景、特写都拍一点，每个场景录 10–20 秒。</li><li>采访先征得同意，让对方把一句话说完；原声是否采用以实际匹配结果为准。</li><li>视频无法读取时，可用“兼容性最好”模式重新拍摄，或转换格式。</li><li>新增或移除文件后，请重新检查素材与稿件的对应关系。</li></ul></details>
        </>}

        {step === 3 && <>
          <div className="gm-preference-groups">
            {mode !== "original" && <><Chips label={mode === "mixed" ? "旁白谁来读" : "谁来配音"} value={voice} options={[["ai", "AI 播音"], ["self", "我自己读"]]} disabled={audioBusy}
              description={voice === "self" ? legacyWholeVoice ? "旧草稿整篇录音恢复：请重新选择原录音。" : "做好后逐句录音。先用 AI 播音生成，再替换需要自己读的句子。" : mode === "mixed" ? "AI 只读旁白句；原声句使用已匹配的现场声音。" : "AI 朗读全部正文；素材人声不会自动代替旁白。"} onChange={(value) => { clearVoice(); setVoice(value); }} />
              <Chips label="播音速度" value={preferences.pacing} options={[["slow", "慢一点"], ["normal", "正常"], ["fast", "快一点"]]} description={`AI 目标 ${cpm} 字 / 分，实际以成片为准；自录音不承诺自动变速。`} onChange={(pacing) => setPreferences((current) => ({ ...current, pacing }))} /></>}
            <Chips label="整体感觉" value={preferences.tone} options={[["solemn", "庄重"], ["neutral", "中性"], ["energetic", "明快"]]} onChange={(tone) => setPreferences((current) => ({ ...current, tone }))} />
            <Chips label="字幕样式" value={captionStyle} options={[["news", "新闻标准"], ["big", "大字清晰"], ["none", "不要字幕"]]} description={captionNote + (mode !== "voiceover" ? " 原声段字幕另行控制，按实际说的话显示。" : "")} onChange={(caption_style) => setPreferences((current) => ({ ...current, caption_style }))} />
            {mode !== "voiceover" && <><Chips label="跳切怎么处理" value={preferences.jump_cut_cover ?? "broll"} options={[["broll", "自动插空镜"], ["zoom", "轻微推近"], ["hard", "直接硬切"]]}
              description="当前还未完成镜头分析，不能承诺有可用空镜；无已分配空镜时由服务器降级为轻微推近并提示。" onChange={jump_cut_cover => setPreferences(current => ({ ...current, jump_cut_cover }))} />
              <Chips label="原声段的字幕" value={preferences.quote_caption === "asr" ? "spoken" : preferences.quote_caption ?? "spoken"} options={[["spoken", "按实际说的话"], ["none", "不显示原声字幕"]]}
                description="不把改写的稿子冒充采访原话；隐藏显示不删除转写和审计。" onChange={quote_caption => setPreferences(current => ({ ...current, quote_caption }))} /></>}
          </div>
          <p className="gm-caption-policy">字幕选择不替代新闻事实核对或素材授权，不改变质量检查与字幕审计，也不能关闭必需的“AI生成示意画面”标注。</p>
          {usingWholeVoice && <section className="gm-voice-panel"><h2>我的整篇播音</h2><p>{mode === "mixed" ? "请只连贯读完所有旁白句，不读原声句和标题。" : "请连贯读完正文，不读标题。"}试听确认没有漏句、杂音或截尾。</p>
            <div className="gm-row"><label className="gm-audio-picker">选择整篇音频<input type="file" accept={AUDIO_ACCEPT} disabled={audioBusy || locked} onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadVoice(file); event.target.value = ""; }} /></label>
              {!recording && !requestingMic && <button type="button" className="gm-button" disabled={!recordSupported || audioBusy} onClick={() => void startRecording()}>● 开始录音</button>}
              {recording && <button type="button" className="gm-button gm-primary" onClick={finishRecording}>■ 停止并试听</button>}
              {(recording || requestingMic || recordFinishing) && <button type="button" className="gm-button" onClick={cancelRecording}>取消录音</button>}
            </div>
            {!recordSupported && <p className="gm-warning">当前浏览器或连接不支持麦克风录音。没有生成录音，请选择音频文件，或改用 AI 播音。</p>}
            <p className="gm-small" role="status">{requestingMic ? "等待麦克风权限…" : recording ? `正在录音 · ${formatDuration(recordSeconds)}` : recordFinishing ? "正在整理录音…" : voiceLoading ? "正在校验音频…" : voiceMeta && !ownVoice ? `草稿记录了「${voiceMeta.name}」，请重新选择，尚无可提交音频。` : ""}</p>
            {(recording || requestingMic) && <div className="gm-read-script" aria-label="录音提词">{narrationRows.map(row => row.text).join("\n")}</div>}
            {ownVoice && voiceUrl && <><p className="gm-small">{ownVoice.name} · {formatDuration(voiceMeta?.duration ?? 0)} · {formatBytes(ownVoice.size)}</p>
              <audio controls src={voiceUrl} onError={() => { clearVoice(); reportError("录音无法播放，不能确认录制成功。请换用兼容音频或 AI 播音。"); }} />
              <div className="gm-row"><a className="gm-small-button" href={voiceUrl} download={ownVoice.name}>下载这份录音</a><button type="button" className="gm-small-button" onClick={clearVoice}>移除录音</button></div></>}
            <p className="gm-muted gm-small">录音目前仅在本页。提交会把真实音频交给制作服务；不代表已上传或完成对齐。服务不支持时须明确报错，不能静默替换成 AI 配音。刷新需重新选择音频。</p>
          </section>}
          {/* Handoff A-3 renders this grid directly, not behind an advanced toggle. */}
          <div className="gm-effect-grid">{effects.map(([key, label, hint]) => <section key={key} className="gm-effect-card" data-active={preferences[key]}>
            <button type="button" className="gm-switch-button" role="switch" aria-checked={preferences[key]} aria-describedby={`${id}-${key}-hint`} onClick={() => setPreferences((current) => ({ ...current, [key]: !current[key] }))}><strong>{label}</strong><span className="gm-switch-track" aria-hidden="true"><span /></span></button>
            <p id={`${id}-${key}-hint`} className="gm-muted gm-small">{hint}{mode === "mixed" && key === "background_music" ? " 原声段落音乐自动压低。" : mode === "mixed" && key === "motion_effects" ? " 此开关只作用于旁白画面；跳切推近由单独选项控制。" : ""}</p>
            {key === "background_music" && preferences.background_music && <Chips label="音乐情绪" value={preferences.music_mood} options={[["auto", "自动"], ["solemn", "庄重"], ["neutral", "平和"], ["uplifting", "昂扬"], ["tense", "紧张"]]} onChange={(music_mood) => setPreferences((current) => ({ ...current, music_mood }))} />}
          </section>)}
            <section className="gm-effect-card" data-active={preferences.enhance_speech ?? false}>
              <button type="button" className="gm-switch-button" role="switch" aria-checked={preferences.enhance_speech ?? false} aria-describedby={`${id}-speech-hint`} onClick={() => setPreferences((current) => ({ ...current, enhance_speech: !current.enhance_speech }))}><strong>现场原声降噪</strong><span className="gm-switch-track" aria-hidden="true"><span /></span></button>
              <p id={`${id}-speech-hint`} className="gm-muted gm-small">仅处理现场原声和自录配音，不处理 AI 播音；尝试减轻噪声，不保证消除风声、杂音或恢复人声。</p>
            </section>
            {mode !== "voiceover" && <section className="gm-effect-card" data-active={preferences.lower_third ?? true}>
              <button type="button" className="gm-switch-button" role="switch" aria-checked={preferences.lower_third ?? true} aria-describedby={`${id}-lower-third-hint`}
                onClick={() => setPreferences(current => ({ ...current, lower_third: !(current.lower_third ?? true) }))}><strong>显示人名条</strong><span className="gm-switch-track" aria-hidden="true"><span /></span></button>
              <p id={`${id}-lower-third-hint`} className="gm-muted gm-small">首次显示 {MODE_LIMITS.lower_third_seconds} 秒，间隔至少 {MODE_LIMITS.lower_third_repeat_gap_seconds} 秒再次显示。请核实下方姓名与身份。</p>
            </section>}
            {mode !== "original" && <section className="gm-effect-card gm-generative" data-active={generativeAllowed && preferences.generative_fill}>
              <button type="button" className="gm-switch-button" role="switch" aria-checked={generativeAllowed && preferences.generative_fill} aria-describedby={`${id}-generative-hint`} disabled={!generativeAllowed} onClick={() => setPreferences((current) => ({ ...current, generative_fill: !current.generative_fill }))}><strong>AI 示意画面补充</strong><span className="gm-switch-track" aria-hidden="true"><span /></span></button>
              <p id={`${id}-generative-hint`} className="gm-muted gm-small">{generativeAllowed ? "服务器已配置此能力，可选且默认关闭。" : "服务器尚未配置此能力，不能开启。"} 仅可用于非事实性的示意画面，须保留“AI生成示意画面”标注，不能代替真实新闻证据；可能产生额外费用，实际可用性与质量要求仍由服务器检查。</p>
            </section>}
          </div>
          {speakerEditor}
          <details className="gm-preference-details"><summary>效果说明与处理范围</summary><dl>
            <dt>字幕</dt><dd>新闻标准沿用原字幕样式；大字清晰将旁白字号放大到 1.5 倍；不要字幕只省略旁白显示，不删除标题、审计或强制披露。</dd>
            <dt>现场原声降噪</dt><dd>尝试减轻现场原声和自录配音里的噪声，不改变 AI 播音；请试听成片，不承诺修复失真或缺失的语音。</dd>
            <dt>画面与声音</dt><dd>轻运镜、颜色调整、首尾淡入淡出和背景音乐只表达制作偏好，不构成事实核验，也不代表素材已通过质量检查。</dd>
          </dl></details>
          <label className="gm-field-label gm-custom-label" htmlFor={`${id}-custom`}>想对 AI 剪辑师说的话 <span className="gm-muted gm-small">选填 · {preferences.custom_instructions.length} / 500 字</span></label>
          <textarea id={`${id}-custom`} maxLength={500} rows={3} value={preferences.custom_instructions} placeholder="想对 AI 剪辑师说的话（选填），例如：多用有人笑的画面" onChange={(event) => setPreferences((current) => ({ ...current, custom_instructions: event.target.value }))} />
          <details className="gm-preference-details"><summary>本次提交清单 · {files.length} 个素材{usingWholeVoice && ownVoice ? " + 1 份整篇录音" : ""}</summary>
            <p className="gm-muted gm-small">素材需全部完成上传与预处理才能制作，将复用已有转写，不因点击制作而重复上传。{legacyWholeVoice ? "旧草稿整篇录音提交时另行上传。" : "选择我自己读：做好后逐句录音。"}</p>
            <ul>{files.map(item => <li key={item.id}>{item.name} · {formatBytes(item.size)} · {uploadUsable(uploadFor(item.id)) ? "服务器已接收" : "仍待处理"}{item.note ? ` · 备注：${item.note}` : ""}</li>)}</ul>
            {usingWholeVoice && <p className="gm-small">整篇录音：{ownVoice?.name ?? "尚未选择可提交录音"}</p>}
          </details>
          <p className="gm-mode-summary">{summaryLine}{mode === "original" && quotesComplete ? ` · 预计 ${jumpCuts} 处跳切` : ""}</p>
          <div className="gm-cloud-notice"><p><b>提交前，请知晓：</b>制作可能调用第三方 AI 并产生费用；请确认素材授权、不含敏感隐私，成片仍须人工核对。</p><details><summary>隐私与处理说明</summary><p>开始制作会提交稿件、所选录音及素材引用；文本、音频、抽帧或视频片段可能发送到配置的第三方 AI 服务处理。请核实必要的肖像与声音授权，并核对新闻事实、画面匹配与 AI 示意画面标注。</p></details></div>
        </>}
        {suggestion?.mode === mode && <aside className="gm-tip gm-mode-suggestion" role="status"><b>✎ 建议</b><p>{mode === "voiceover" ? `有 ${suggestion.count} 句话和素材里的原话对上了，想直接用原声？` : `有 ${suggestion.count} 句在素材里没找到，这几句让 AI 读？`}</p>
          <div className="gm-row"><button type="button" className="gm-small-button" onClick={() => chooseMode("mixed", true)}>切到“旁白 + 原声”</button><button type="button" className="gm-small-button" onClick={() => setSuggestion(null)}>保持当前模式</button></div>
        </aside>}
      </div>
      <footer className="gm-wizard-actions">
        {step > 1 && <button className="gm-button" type="button" disabled={!canGoToStep(step === 3 ? 2 : 1)} onClick={() => goToStep(step === 3 ? 2 : 1)}>← 上一步</button>}
        <p className="gm-action-hint" id={`${id}-gate`}>{step === 1 ? blankOriginal ? "可以先传采访素材，再从转写里挑选原话。" : scriptProblem || "稿件格式已检查，可以准备对应素材。" : step === 2 ? scriptProblem || issues[0] || matchingProblem || "素材已预处理，接下来选择效果；正式制作仍会复核。" : blockedReason || "先制作、再检查。生成完成不代表新闻事实已核验。"}</p>
        {nextTried && nextBlockReason && <p role="alert" className="gm-error gm-action-error" id={`${id}-gate-error`}>还不能进入下一步：{nextBlockReason}</p>}
        {step < 3 ? <button type="button" className="gm-button gm-primary" aria-describedby={`${id}-gate`} aria-disabled={Boolean(nextBlockReason)} onClick={goNext}>下一步：{step === 1 ? "传素材" : "选效果"} <Icon name="arrow" /></button>
          : <button type="button" className="gm-button gm-primary gm-submit" aria-describedby={`${id}-gate`} disabled={!canSubmit} onClick={() => void submit()}>{locked ? "正在提交…" : "开始制作"}</button>}
      </footer>
    </fieldset>
    {notice && <p className="gm-muted gm-small gm-visible-notice">{notice}</p>}
  </section>;
}

export default CreateWizard;