/** Isolated Studio v1 contract. JSON and raw asset bodies use the host's CSRF/session client.
 * The host must mount create_studio_router and authorize cookie sessions or legacy
 * X-Task-Token / ?token= requests (including Range media requests).
 * /export exports pipeline artifacts; /render renders the saved Studio timeline.
 * /proxies creates a bounded RAW-source preview, never a timeline source / output.
 * Jobs have states, NOT percentage progress. There is no historical snapshot GET.
 */
export type StudioRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
export type TrackType = "video" | "audio" | "text" | "overlay" | "adjustment";
export type TrackColor = "gold" | "cyan" | "red" | "purple" | "gray";
export type ToolGroup = "timeline" | "clip" | "cam" | "audio" | "ai" | "fx" | "text" | "mask" | "export" | "tools";
export interface Crop { x: number; y: number; width: number; height: number }
export interface KeyPoint { time: number; value: number; easing: "linear" | "ease_in" | "ease_out" | "ease_in_out" }
export type TransitionKind = "dissolve" | "wipe_left" | "wipe_right" | "wipe_up" | "wipe_down";
export interface ClipTransition {
  left_clip_id: string; kind: TransitionKind; duration: number; easing: KeyPoint["easing"]; audio: boolean;
}
export const TRANSITION_KINDS: readonly TransitionKind[] = ["dissolve", "wipe_left", "wipe_right", "wipe_up", "wipe_down"];
export type AnimatedProperty = "x" | "y" | "scale" | "opacity";
export type Keyframes = Record<AnimatedProperty, KeyPoint[]>;
export interface CurvePoint { x: number; y: number }
export type RGBChannel = "red" | "green" | "blue";
export type RGBCurves = Partial<Record<RGBChannel, CurvePoint[]>>;
export interface ShapeMask { type: "rectangle" | "ellipse"; x: number; y: number; width: number; height: number; feather: number; invert: boolean }
export type ShortcutAction = "play_pause" | "split" | "delete" | "undo" | "redo" | "marker";
export type TimelineFPS = 24 | 25 | 30 | 60;
export interface SourceMark { source_id: string; in_point: number; out_point: number }
export interface WorkspaceMetadata {
  layout: "default" | "editing" | "audio" | "captions"; timeline_zoom: number;
  timeline_fps: TimelineFPS; time_display: "seconds" | "frames";
  snap_enabled: boolean; ripple_enabled: boolean; source_marks: SourceMark[];
  shortcuts: Partial<Record<ShortcutAction, string>>;
}
export interface AssetMetadata { source_id: string; rating: number; tags: string[] }
export const ANIMATED_PROPERTIES: AnimatedProperty[] = ["x", "y", "scale", "opacity"];
export const DEFAULT_SHORTCUTS: Record<ShortcutAction, string> = {
  play_pause: "Space", split: "S", delete: "Delete", undo: "Ctrl+Z", redo: "Ctrl+Shift+Z", marker: "M",
};
export function canonicalChord(chord: string): string | null {
  if (!/^(?:(?:Ctrl|Alt|Shift)\+){0,3}(?:[A-Z0-9]|Space|Delete|Left|Right)$/.test(chord)) return null;
  const parts = chord.split("+");
  if (new Set(parts).size !== parts.length) return null;
  return [...["Ctrl", "Alt", "Shift"].filter(m => parts.includes(m)), parts[parts.length - 1]].join("+");
}
/** An explicit binding takes precedence over a colliding default, never fires two actions. */
export function effectiveShortcuts(custom: WorkspaceMetadata["shortcuts"]): Partial<Record<ShortcutAction, string>> {
  const bindings: Partial<Record<ShortcutAction, string>> = {};
  const assigned = new Set(Object.values(custom).map(c => canonicalChord(c)));
  for (const action of Object.keys(DEFAULT_SHORTCUTS) as ShortcutAction[]) {
    if (custom[action]) bindings[action] = canonicalChord(custom[action]) ?? undefined;
    else if (!assigned.has(DEFAULT_SHORTCUTS[action])) bindings[action] = DEFAULT_SHORTCUTS[action];
  }
  return bindings;
}
export interface StudioClip {
  id: string; source_id: string | null; start: number; trim: number; duration: number;
  /** Full-child reference, not a source URL or a second source clock. */
  sequence_id: string | null;
  speed: number; reverse: boolean; freeze: boolean; rotation: 0 | 90 | 180 | 270; mirror: boolean; flip: boolean;
  crop: Crop; scale: number; x: number; y: number; opacity: number;
  fit: "contain" | "cover"; keyframes: Keyframes; mask: ShapeMask | null;
  volume: number; pan: number; mute: boolean;
  brightness: number; contrast: number; saturation: number; sharpen: number; noise: number;
  temperature: number; hue: number; shadows: number; highlights: number; fade_amount: number;
  color_preset: "none" | "warm" | "cool" | "cinema" | "mono"; rgb_curves: RGBCurves;
  /** Task-local imported asset, not a source ID, preset label or filter expression. */
  lut_id: string | null;
  fade_in: number; fade_out: number; audio_effect: "none" | "compressor" | "limiter" | "denoise" | "invert" | "normalize" | "reverb";
  bass_db: number; treble_db: number;
  text: string; subtitle: boolean; font_size: number; color: string;
  bold: boolean; outline: number; shadow: number; background: boolean;
  text_animation: "none" | "typewriter" | "fade" | "pop";
  text_template: "custom" | "news" | "outline" | "gold" | "note";
  chroma_color: string | null; chroma_similarity: number; chroma_blend: number;
  /** Incoming boundary on the RIGHT clip. Missing legacy values normalize to null. */
  transition_in?: ClipTransition | null;
}
export type ColorParameters = Pick<StudioClip, "brightness" | "contrast" | "saturation" | "temperature" | "hue"
  | "shadows" | "highlights" | "fade_amount" | "color_preset" | "rgb_curves" | "lut_id" | "sharpen" | "noise">;
export const COLOR_FIELDS = ["brightness", "contrast", "saturation", "temperature", "hue", "shadows", "highlights",
  "fade_amount", "color_preset", "rgb_curves", "lut_id"] as const;
/** Detached typed data only; the caller must check LUT membership in this task.
 * Adjustment tracks cannot accept texture fields. */
export function copyColorParameters(clip: StudioClip): ColorParameters {
  const { brightness, contrast, saturation, temperature, hue, shadows, highlights, fade_amount, color_preset, rgb_curves, lut_id, sharpen, noise } = clip;
  return { brightness, contrast, saturation, temperature, hue, shadows, highlights, fade_amount, color_preset, lut_id,
    rgb_curves: normalizeRGBCurves(rgb_curves), sharpen, noise };
}
export function applicableColorParameters(color: ColorParameters, type: TrackType): Partial<StudioClip> {
  if (!["video", "overlay", "adjustment"].includes(type)) throw new Error("此轨道不支持调色。");
  const { sharpen, noise, ...shared } = color;
  const detached = { ...shared, rgb_curves: normalizeRGBCurves(shared.rgb_curves) };
  return type === "adjustment" ? detached : { ...detached, sharpen, noise };
}
export interface StudioTrack {
  id: string; type: TrackType; name: string; locked: boolean; hidden: boolean;
  solo: boolean; color: TrackColor; group_id: string | null; clips: StudioClip[];
}
export interface StudioMarker { id: string; time: number; label: string }
export interface StudioSequence { id: string; name: string; tracks: StudioTrack[]; markers: StudioMarker[] }
export interface StudioProject {
  schema_version: 1; name: string; tracks: StudioTrack[];
  markers: StudioMarker[];
  /** Root tracks / markers always remain the main timeline, even while editing a child. */
  sequences: StudioSequence[]; active_sequence_id: string | null;
  groups: { id: string; name: string }[]; assets: AssetMetadata[]; workspace: WorkspaceMetadata;
}
/** Non-JSON view marker. A working timeline must be mapped back before saving. */
export const STUDIO_ACTIVE_VIEW = Symbol("Studio active timeline view, not a persisted project");
export const STUDIO_SEQUENCE_LIMITS = Object.freeze({ sequences: 4, tracks: 8, totalTracks: 32, markers: 100, clips: 64, depth: 2, inputs: 16, duration: 120 });
export function isSequenceClip(clip: StudioClip): clip is StudioClip & { sequence_id: string } {
  return typeof clip.sequence_id === "string";
}
export interface ProjectEnvelope { revision: number; project: StudioProject; can_undo: boolean; can_redo: boolean }
export interface StudioTool {
  id: string; group: ToolGroup; label: string; available: boolean;
  classification: "unsupported" | "partial" | "metadata" | "renderer"; reason: string;
}
export interface StudioCapabilities {
  schema_version: number; tools: StudioTool[]; tool_count: number;
  limits: { tracks: number; clips: number; duration_seconds: number; output_bytes: number;
    task_output_bytes: number; jobs_per_task: number; history_mutations: number;
    threads: number; global_jobs: number; max_resolution: number; request_bytes: number;
    source_marks?: number; rgb_knots_per_channel?: number; subtitle_bytes?: number;
    sequences?: number; total_tracks?: number; markers_per_timeline?: number;
    nesting_depth?: number; expanded_tracks?: number; expanded_clips?: number; decoder_inputs?: number;
    proxy_duration_seconds?: number; proxy_width?: number; proxy_height?: number; proxy_fps?: number };
  export_schema: Record<string, unknown>; project_schema: Record<string, unknown>;
  action_schema: Record<string, unknown>; subtitle_import_schema?: Record<string, unknown>; proxy_request_schema?: Record<string, unknown>;
  unsupported: string[]; render_available: boolean; quality: string;
}
export interface StudioSource {
  id: string; name: string; bytes: number; url: string; burned_subtitles: boolean;
  /** Optional verified catalog metadata, never inferred from a successful video load.
   * Older catalogs omit these; the renderer must then probe stream presence / EOF.
   * For an image, duration is its maximum DISPLAY duration (120s), NOT media EOF. */
  duration?: number; has_video?: boolean; has_audio?: boolean;
  is_image?: boolean; width?: number; height?: number;
}
export interface SourceCatalog {
  sources: StudioSource[];
  shots: { shot_id?: number; start?: number; end?: number; duration?: number; description?: string;
    media_origin?: string; source_id: string | null }[];
  report: { rows: { sentence_id?: number; sentence?: string; duration?: number; confidence?: number;
    is_fallback?: boolean; audio_kind?: string }[]; quality: unknown };
}
export type StudioAssetKind = "image" | "lut";
export interface StudioImageAsset { id: string; name: string; bytes: number; width: number; height: number; url: string }
export interface StudioLutAsset { id: string; name: string; bytes: number; size: number }
export interface StudioAssetLimits {
  image_bytes: number; lut_bytes: number; assets: number; dimension: number; pixels: number; lut_size: number;
}
export interface StudioAssetCatalog { images: StudioImageAsset[]; luts: StudioLutAsset[]; limits: StudioAssetLimits }
export interface StudioAssetReceipt<T extends StudioImageAsset | StudioLutAsset> {
  asset: T; project_revision: number; deduplicated: boolean;
}
export const STUDIO_ASSET_LIMITS: Readonly<StudioAssetLimits> = Object.freeze({
  image_bytes: 8388608, lut_bytes: 2097152, assets: 64, dimension: 4096, pixels: 8847360, lut_size: 33,
});
export const isImageSourceId = (id: string | null): boolean => typeof id === "string" && /^image_[a-f0-9]{24}$/.test(id);
export const isImageSource = (source: Pick<StudioSource, "id" | "is_image">): boolean => source.is_image === true || isImageSourceId(source.id);
/** Old capability fixtures do not have this endpoint. Availability is opt-in. */
export const studioAssetsAvailable = (caps: Pick<StudioCapabilities, "tools"> | null): boolean =>
  !!caps?.tools.some(tool => (tool.id === "stickerCustom" || tool.id === "lut") && tool.available === true);
export function imageDisplayLimit(source: StudioSource): number {
  if (!isImageSource(source)) throw new Error("此素材不是静态图片。");
  return source.duration === undefined ? 120 : numeric(source.duration, Number.MIN_VALUE, 120, "图片最大显示时长");
}
/** Only used for an explicit new clip / source selection, never to repair imports.
 * Visual transforms, masks, alpha, keyframes, LUT and authored timing are retained. */
export const IMAGE_CLIP_FIELDS: Readonly<Pick<StudioClip, "trim" | "speed" | "reverse" | "freeze" | "mute"
  | "volume" | "pan" | "audio_effect" | "bass_db" | "treble_db">> = Object.freeze({
  trim: 0, speed: 1, reverse: false, freeze: false, mute: true,
  volume: 1, pan: 0, audio_effect: "none", bass_db: 0, treble_db: 0,
});
export function makeSourceClip(source: StudioSource, patch: Partial<StudioClip> & Pick<StudioClip, "duration">): StudioClip {
  if (isImageSource(source) && patch.duration > imageDisplayLimit(source)) throw new Error("图片显示时长超过支持范围。");
  return normalizeClip(makeClip({ ...patch, source_id: source.id, ...(isImageSource(source) ? IMAGE_CLIP_FIELDS : {}) }));
}
export type ExportFormat = "mp4" | "mov" | "mkv" | "avi" | "gif" | "mp3" | "wav" | "png" | "srt" | "ass";
export interface ExportOptions {
  format: ExportFormat; resolution: 360 | 720 | 1080; fps: 24 | 25 | 30 | 60;
  aspect: "16:9" | "9:16" | "1:1"; subtitles: "standard" | "large" | "none";
  video_bitrate_kbps: number; audio_bitrate_kbps: 96 | 128 | 192 | 256 | 320;
  frame_time: number | null;
}
export interface StudioJob {
  id: string; revision: number;
  /** Captured source revision; older immutable render/export records may omit it. */
  pipeline_revision?: number;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "interrupted";
  kind: "render" | "export" | "proxy"; created_at: number; finished_at?: number; options: ExportOptions;
  source_id?: string; cache_key?: string; cached?: boolean;
  output_id: string | null; error: string | null; qc: string; disclosure: boolean;
  /** Old jobs may omit both. A terminal failure can still own quota-counted residue. */
  cleanup_pending?: boolean; cleanup_error?: string | null;
  export_semantics?: { picture: string; audio: string; framing: string; warnings: string[] };
  result?: { file: string; bytes: number; duration: number; applied: ExportOptions;
    probe?: unknown; disclosure?: unknown; png_semantics?: string; gif_fps_semantics?: string;
    frame_time?: number | null; gif_duration_limit?: number | null };
}
export type StudioProxyJob = StudioJob & { kind: "proxy"; source_id: string; cache_key: string; pipeline_revision: number };
/** Fixed server target, NOT a claim about measured output or a configurable export preset. */
export const STUDIO_PROXY_PROFILE = Object.freeze({ max_source_seconds: 120, width: 640, height: 360, resolution: 360, fps: 30,
  format: "mp4", aspect: "16:9", subtitles: "standard", video_bitrate_kbps: 1000, audio_bitrate_kbps: 128,
  video_codec: "h264", audio_codec: "aac" });
export const STUDIO_MAX_JOBS = 20;
/** RAW means the unchanged catalog file, not the current edited timeline.
 * Existing final/normalized video sources are legal; proxy/image/recording IDs are not. */
export const isRawProxySourceId = (id: unknown): id is string => typeof id === "string"
  && /^(?:final|video_only|(?:upload|norm)_[a-f0-9]{20})$/.test(id);
/** Unavailable/older fixtures must never receive the new auxiliary endpoint. */
export const studioProxiesAvailable = (caps: Pick<StudioCapabilities, "tools"> | null): boolean =>
  !!caps?.tools.some(tool => tool.id === "proxy" && tool.available === true);
/** Only use with jobs already validated by this module. Source membership is checked separately. */
export const isProxyJob = (job: StudioJob): job is StudioProxyJob => job.kind === "proxy";
export interface AuditRecord { id: string; time: number; op: string; revision?: number; job_id?: string; [key: string]: unknown }
export type StudioAction =
  | { op: "undo" | "redo" | "clear_markers" }
  | { op: "marker"; new_id: string; at: number; label?: string }
  | { op: "track"; track_id: string; locked?: boolean; hidden?: boolean; solo?: boolean; color?: TrackColor }
  | { op: "split"; clip_id: string; at: number; new_id: string }
  | { op: "delete" | "ripple_delete"; clip_id: string }
  | { op: "duplicate"; clip_id: string; new_id: string; at: number; track_id?: string }
  | { op: "move"; clip_id: string; at: number; track_id?: string }
  | { op: "trim" | "ripple_trim"; clip_id: string; trim: number; duration: number }
  | { op: "slip"; clip_id: string; trim: number }
  | { op: "roll" | "slide"; clip_id: string; at: number };

export const DEFAULT_EXPORT: ExportOptions = {
  format: "mp4", resolution: 1080, fps: 30, aspect: "16:9", subtitles: "standard",
  video_bitrate_kbps: 4000, audio_bitrate_kbps: 192, frame_time: null,
};
export const FORMATS: ExportFormat[] = ["mp4", "mov", "mkv", "avi", "gif", "mp3", "wav", "png", "srt", "ass"];
export const GROUPS: [ToolGroup, string][] = [
  ["timeline", "时间线与轨道"], ["clip", "片段编辑"], ["cam", "多机位"], ["audio", "音频台"],
  ["ai", "AI 工具"], ["fx", "特效与调色"], ["text", "字幕与贴纸"], ["mask", "画中画与蒙版"],
  ["export", "专业导出与分享"], ["tools", "素材与工具"],
];
export const uid = (prefix: string) => `${prefix}_${crypto.randomUUID().replace(/-/g, "")}`;
export function makeClip(patch: Partial<StudioClip> & Pick<StudioClip, "duration">): StudioClip {
  return { id: patch.id ?? uid("clip"), source_id: null, sequence_id: null, start: 0, trim: 0, speed: 1, reverse: false, freeze: false,
    rotation: 0, mirror: false, flip: false, crop: { x: 0, y: 0, width: 1, height: 1 },
    scale: 1, x: 0, y: 0, opacity: 1, fit: "contain", keyframes: { x: [], y: [], scale: [], opacity: [] }, mask: null,
    volume: 1, pan: 0, mute: false, bass_db: 0, treble_db: 0,
    brightness: 0, contrast: 1, saturation: 1, sharpen: 0, noise: 0, fade_in: 0, fade_out: 0,
    temperature: 0, hue: 0, shadows: 0, highlights: 0, fade_amount: 0, color_preset: "none", rgb_curves: {}, lut_id: null,
    audio_effect: "none", text: "", subtitle: true, font_size: 42, color: "FFFFFF", bold: false, outline: 2, shadow: 0, background: false,
    text_animation: "none", text_template: "custom",
    chroma_color: null, chroma_similarity: 0.1, chroma_blend: 0, transition_in: null, ...patch };
}
export function makeTrack(type: TrackType, name: string, color: TrackColor = "gray"): StudioTrack {
  return { id: uid("track"), type, name, color, group_id: null, locked: false, hidden: false, solo: false, clips: [] };
}
export function makeWorkspace(): WorkspaceMetadata {
  return { layout: "default", timeline_zoom: 1, timeline_fps: 30, time_display: "seconds",
    snap_enabled: true, ripple_enabled: false, source_marks: [], shortcuts: {} };
}
/** Empty editing lanes, never fabricated source clips or inferred subtitle timing. */
export function starterProject(): StudioProject {
  return { schema_version: 1, name: "Studio", markers: [], groups: [], assets: [], sequences: [], active_sequence_id: null,
    workspace: makeWorkspace(), tracks: [
    makeTrack("video", "视频 V1", "gold"), makeTrack("overlay", "画中画 V2", "red"),
    makeTrack("text", "文字 T1", "gold"), makeTrack("overlay", "贴纸 S1 · 素材叠加", "purple"),
    makeTrack("adjustment", "调整图层", "gray"), makeTrack("audio", "配音 A1", "cyan"),
    makeTrack("audio", "音乐 A2", "cyan"),
  ] };
}
export function formatSupport(format: ExportFormat) {
  return {
    geometry: !["mp3", "wav", "srt", "ass"].includes(format),
    fps: ["mp4", "mov", "mkv", "avi", "gif"].includes(format),
    videoBitrate: ["mp4", "mov", "mkv", "avi"].includes(format),
    audioBitrate: ["mp4", "mov", "mkv", "avi", "mp3"].includes(format),
    subtitleStyles: format === "srt" || format === "mp3" || format === "wav"
      ? ["standard"] as const : format === "ass" ? ["standard", "large"] as const : ["standard", "large", "none"] as const,
  };
}
/** Reset inapplicable fields explicitly when changing container, as required by the API. */
export function optionsForFormat(options: ExportOptions, format: ExportFormat): ExportOptions {
  const support = formatSupport(format);
  return { ...options, format,
    frame_time: format === "png" ? options.frame_time : null,
    ...(!support.geometry ? { resolution: 1080 as const, aspect: "16:9" as const } : {}),
    ...(!support.fps ? { fps: 30 as const } : {}),
    ...(!support.videoBitrate ? { video_bitrate_kbps: 4000 } : {}),
    ...(!support.audioBitrate ? { audio_bitrate_kbps: 192 as const } : {}),
    subtitles: (support.subtitleStyles as readonly string[]).includes(options.subtitles) ? options.subtitles : "standard",
  };
}
export const isActiveJob = (job: StudioJob) => job.state === "queued" || job.state === "running";
export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function invalid(field: string): never { throw new Error(`Studio ${field} 无效或响应不兼容。`); }
const object = (value: unknown, keys: readonly string[], field: string): Record<string, unknown> => {
  if (!isRecord(value) || Object.keys(value).some(key => !keys.includes(key))) return invalid(field);
  return value;
};
const list = (value: unknown, max: number, field: string): unknown[] => {
  if (!Array.isArray(value) || value.length > max) return invalid(field);
  return value;
};
const numeric = (value: unknown, min: number, max: number, field: string, integer = false): number => {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max
    || (integer && !Number.isSafeInteger(value))) return invalid(field);
  return value;
};
const boolean = (value: unknown, field: string): boolean => typeof value === "boolean" ? value : invalid(field);
const textValue = (value: unknown, max: number, field: string, min = 0): string => {
  if (typeof value !== "string" || [...value].length < min || [...value].length > max) return invalid(field);
  return value;
};
const identifier = (value: unknown, field: string): string =>
  typeof value === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(value) ? value : invalid(field);
const choice = <T extends string | number>(value: unknown, choices: readonly T[], field: string): T =>
  choices.includes(value as T) ? value as T : invalid(field);
const missing = (value: unknown, fallback: unknown): unknown => value === undefined ? fallback : value;
const EASINGS: KeyPoint["easing"][] = ["linear", "ease_in", "ease_out", "ease_in_out"];

/** Validate only local file metadata here. The server decodes images / parses
 * .cube data; extensions and MIME are NOT proof of valid or safe contents. */
export function validateStudioAssetFile(kind: StudioAssetKind, file: Pick<File, "name" | "size" | "type">,
  limits: Readonly<StudioAssetLimits> = STUDIO_ASSET_LIMITS): "image/png" | "image/jpeg" | "text/plain" {
  if (kind !== "image" && kind !== "lut") return invalid("素材种类");
  const limit = Math.min(STUDIO_ASSET_LIMITS[`${kind}_bytes`], limits[`${kind}_bytes`]);
  numeric(limit, 1, Number.MAX_SAFE_INTEGER, "素材字节额度", true);
  if (!Number.isSafeInteger(file.size) || file.size <= 0 || file.size > limit) {
    throw new Error(`${kind === "image" ? "图片" : "LUT"}文件须为 1–${limit} 字节，未上传。`);
  }
  if (typeof file.name !== "string" || typeof file.type !== "string") return invalid("本地文件元数据");
  const mime = file.type.toLowerCase();
  if (kind === "image") {
    const expected = /\.png$/i.test(file.name) ? "image/png" : /\.jpe?g$/i.test(file.name) ? "image/jpeg" : null;
    if (!expected || (mime !== "" && mime !== expected)) throw new Error("仅接受扩展名与 MIME 一致的 PNG / JPEG 图片；不支持 SVG、GIF 或外部地址。");
    return expected;
  }
  if (!/\.cube$/i.test(file.name) || !["", "text/plain", "application/octet-stream"].includes(mime)) {
    throw new Error("仅接受本地 .cube 文本 LUT；不支持 HTML、其他 LUT 格式或外部地址。");
  }
  return "text/plain";
}

function normalizeAssetLimits(value: unknown): StudioAssetLimits {
  const raw = object(value, Object.keys(STUDIO_ASSET_LIMITS), "assets.limits");
  const result = { ...STUDIO_ASSET_LIMITS };
  for (const key of Object.keys(result) as (keyof StudioAssetLimits)[]) {
    result[key] = numeric(raw[key], key === "lut_size" ? 2 : 1, STUDIO_ASSET_LIMITS[key], `assets.limits.${key}`, true);
  }
  return result;
}
function normalizeImageAsset(value: unknown, limits: Readonly<StudioAssetLimits>): StudioImageAsset {
  const raw = object(value, ["id", "name", "bytes", "width", "height", "url"], "image asset");
  const id = identifier(raw.id, "image.id");
  if (!isImageSourceId(id)) return invalid("image.id");
  const width = numeric(raw.width, 1, limits.dimension, "image.width", true);
  const height = numeric(raw.height, 1, limits.dimension, "image.height", true);
  if (width * height > limits.pixels) return invalid("图片像素上限");
  return { id, name: textValue(raw.name, 255, "image.name", 1),
    bytes: numeric(raw.bytes, 1, limits.image_bytes, "image.bytes", true), width, height,
    // Display-only metadata. Never use this value as a request / img URL.
    url: textValue(raw.url, 2048, "image.url", 1) };
}
function normalizeLutAsset(value: unknown, limits: Readonly<StudioAssetLimits>): StudioLutAsset {
  const raw = object(value, ["id", "name", "bytes", "size"], "lut asset");
  return { id: identifier(raw.id, "lut.id"), name: textValue(raw.name, 255, "lut.name", 1),
    bytes: numeric(raw.bytes, 1, limits.lut_bytes, "lut.bytes", true), size: numeric(raw.size, 2, limits.lut_size, "lut.size", true) };
}
export function normalizeStudioAssets(value: unknown): StudioAssetCatalog {
  const raw = object(value, ["images", "luts", "limits"], "assets");
  const limits = normalizeAssetLimits(raw.limits);
  const images = list(raw.images, limits.assets, "assets.images").map(item => normalizeImageAsset(item, limits));
  const luts = list(raw.luts, limits.assets, "assets.luts").map(item => normalizeLutAsset(item, limits));
  const ids = [...images, ...luts].map(asset => asset.id);
  if (ids.length > limits.assets || new Set(ids).size !== ids.length) return invalid("素材总数 / 重复 ID");
  return { images, luts, limits };
}
function assetReceipt<T extends StudioImageAsset | StudioLutAsset>(value: unknown, revision: number,
  read: (value: unknown, limits: Readonly<StudioAssetLimits>) => T): StudioAssetReceipt<T> {
  const raw = object(value, ["asset", "project_revision", "deduplicated"], "asset import");
  const project_revision = numeric(raw.project_revision, 0, Number.MAX_SAFE_INTEGER, "project_revision", true);
  if (project_revision !== revision) throw new Error("素材导入回执修订不一致；导入不能推进工程修订。请刷新目录确认，勿重复上传。");
  return { asset: read(raw.asset, STUDIO_ASSET_LIMITS), project_revision, deduplicated: boolean(raw.deduplicated, "deduplicated") };
}

/** Preserve legacy and additive metadata; malformed KNOWN metadata fails closed.
 * Catalog URLs are never followed: previews use the API's known source route. */
export function normalizeSourceCatalog(value: unknown): SourceCatalog {
  if (!isRecord(value) || !Array.isArray(value.sources) || !Array.isArray(value.shots) || !isRecord(value.report)
    || !Array.isArray(value.report.rows)) return invalid("素材响应");
  for (const item of value.sources) {
    if (!isRecord(item)) return invalid("source");
    const id = identifier(item.id, "source.id"), image = item.is_image === true || isImageSourceId(id);
    textValue(item.name, 1024, "source.name"); numeric(item.bytes, 0, Number.MAX_SAFE_INTEGER, "source.bytes", true);
    if (item.url !== undefined) textValue(item.url, 2048, "source.url");
    boolean(item.burned_subtitles, "source.burned_subtitles");
    for (const key of ["has_video", "has_audio", "is_image"]) if (item[key] !== undefined) boolean(item[key], `source.${key}`);
    if (item.duration !== undefined) numeric(item.duration, Number.MIN_VALUE, image ? 120 : 3600, "source.duration");
    for (const key of ["width", "height"]) {
      if (item[key] !== undefined) numeric(item[key], 1, image ? STUDIO_ASSET_LIMITS.dimension : Number.MAX_SAFE_INTEGER, `source.${key}`, true);
    }
    if (image && (!isImageSourceId(id) || item.is_image === false || item.has_audio === true
      || (typeof item.width === "number" && typeof item.height === "number" && item.width * item.height > STUDIO_ASSET_LIMITS.pixels))) {
      return invalid("静态图片元数据");
    }
  }
  if (new Set(value.sources.map(item => (item as StudioSource).id)).size !== value.sources.length) return invalid("重复源 ID");
  return value as unknown as SourceCatalog;
}

export function normalizeTransition(value: unknown): ClipTransition | null {
  if (value === undefined || value === null) return null;
  const raw = object(value, ["left_clip_id", "kind", "duration", "easing", "audio"], "transition_in");
  return {
    left_clip_id: identifier(raw.left_clip_id, "transition_in.left_clip_id"),
    kind: choice(raw.kind, TRANSITION_KINDS, "transition_in.kind"),
    duration: numeric(raw.duration, Number.MIN_VALUE, 1.2, "transition_in.duration"),
    easing: choice(missing(raw.easing, "linear"), EASINGS, "transition_in.easing"),
    audio: boolean(missing(raw.audio, true), "transition_in.audio"),
  };
}

/** No implicit repair: both ends must ALREADY use the renderer's full-frame mode.
 * Crop / rotation / color / speed are retained; they do not change this canvas contract. */
export function isFullFrameTransitionClip(clip: StudioClip): boolean {
  return !isSequenceClip(clip) && clip.scale === 1 && clip.x === 0 && clip.y === 0 && clip.opacity === 1 && clip.fit === "cover"
    && clip.mask === null && clip.chroma_color === null && !clip.freeze
    && clip.fade_in === 0 && clip.fade_out === 0 && ANIMATED_PROPERTIES.every(key => clip.keyframes[key].length === 0);
}

/** Validate persisted geometry, never shift it. Only declared boundaries are
 * constrained; ordinary independent alpha overlaps keep their existing semantics. */
function validateTransitions(tracks: StudioTrack[], fps: TimelineFPS): void {
  const epsilon = 1e-12; // Same serialization-noise tolerance as the renderer, not a frame / gap tolerance.
  for (const track of tracks) {
    const byId = new Map(track.clips.map(clip => [clip.id, clip]));
    for (const right of track.clips) {
      const transition = right.transition_in;
      if (!transition) continue;
      if (track.type !== "video" && track.type !== "overlay") invalid("转场仅适用于同轨视频 / 叠加片段");
      const left = byId.get(transition.left_clip_id);
      if (!left || left.id === right.id) invalid("转场左端须为同轨另一个视频 / 叠加片段");
      const leftEnd = left.start + left.duration, rightEnd = right.start + right.duration;
      if (!(left.start < right.start && leftEnd < rightEnd)) invalid("转场两端起点 / 终点必须严格递增");
      if (transition.duration + epsilon < 1 / fps || transition.duration > Math.min(left.duration, right.duration) / 2 + epsilon) {
        invalid("转场须至少一工作区帧，且不超过两端各自时长的一半");
      }
      if (Math.abs(leftEnd - right.start - transition.duration) > epsilon) invalid("转场重叠必须恰好等于声明时长；渲染器不会移动片段");
      if (!isFullFrameTransitionClip(left) || !isFullFrameTransitionClip(right)) {
        invalid("转场两端须已为全帧 cover（scale=1、x/y=0、opacity=1），且无蒙版、色度键、关键帧、淡化或定格");
      }
      if (track.clips.some(other => other.id !== left.id && other.id !== right.id
        && Math.min(other.start + other.duration, leftEnd) - Math.max(other.start, right.start) > epsilon)) {
        invalid("转场重叠区间不能有第三个同轨片段");
      }
    }
  }
}

/** Missing channels mean identity; null, empty arrays and unknown keys are errors. */
export function normalizeRGBCurves(value: unknown): RGBCurves {
  const raw = object(value, ["red", "green", "blue"], "rgb_curves"), result: RGBCurves = {};
  for (const channel of Object.keys(raw) as RGBChannel[]) {
    const points = list(raw[channel], 8, `rgb_curves.${channel}`).map(item => {
      const point = object(item, ["x", "y"], "RGB 曲线点");
      return { x: numeric(point.x, 0, 1, "RGB x"), y: numeric(point.y, 0, 1, "RGB y") };
    });
    if (points.length < 2 || points[0].x !== 0 || points[points.length - 1].x !== 1
      || points.some((point, i) => i > 0 && point.x <= points[i - 1].x)) invalid("RGB 曲线须 2–8 点、x 严格递增且端点为 0 / 1");
    result[channel] = points;
  }
  return result;
}

export function normalizeWorkspace(value: unknown): WorkspaceMetadata {
  const defaults = makeWorkspace();
  const raw = object(missing(value, {}), Object.keys(defaults), "workspace");
  const data = { ...defaults, ...raw };
  const shortcuts = object(data.shortcuts, Object.keys(DEFAULT_SHORTCUTS), "shortcuts");
  const chords = Object.values(shortcuts).map(c => typeof c === "string" ? canonicalChord(c) : null);
  if (chords.some(c => c === null) || new Set(chords).size !== chords.length) invalid("快捷键格式 / 重复绑定");
  const marks = list(data.source_marks, 200, "source_marks").map(item => {
    const mark = object(item, ["source_id", "in_point", "out_point"], "source_marks");
    const in_point = numeric(mark.in_point, 0, 3600, "源入点"), out_point = numeric(mark.out_point, 0, 3600, "源出点");
    if (out_point <= in_point) invalid("源出点须大于入点");
    return { source_id: identifier(mark.source_id, "source_id"), in_point, out_point };
  });
  if (new Set(marks.map(mark => mark.source_id)).size !== marks.length) invalid("重复的源入出点 ID");
  return {
    layout: choice(data.layout, ["default", "editing", "audio", "captions"], "layout"),
    timeline_zoom: numeric(data.timeline_zoom, 0.25, 8, "timeline_zoom"),
    timeline_fps: choice(data.timeline_fps, [24, 25, 30, 60], "timeline_fps"),
    time_display: choice(data.time_display, ["seconds", "frames"], "time_display"),
    snap_enabled: boolean(data.snap_enabled, "snap_enabled"), ripple_enabled: boolean(data.ripple_enabled, "ripple_enabled"),
    source_marks: marks, shortcuts: { ...shortcuts } as WorkspaceMetadata["shortcuts"],
  };
}

export function normalizeClip(value: unknown): StudioClip {
  if (!isRecord(value)) return invalid("clip");
  const defaults = makeClip({ id: identifier(value.id, "clip.id"), duration: numeric(value.duration, Number.MIN_VALUE, 120, "duration") });
  const raw = object(value, Object.keys(defaults), "clip 字段（不接受滤镜字符串或未知字段）");
  const c: Record<string, unknown> = { ...defaults, ...raw };
  const ranges: [string, number, number, boolean?][] = [
    ["start", 0, 120], ["trim", 0, 3600], ["duration", Number.MIN_VALUE, 120], ["speed", 0.5, 2], ["scale", 0.05, 1],
    ["x", -1, 1], ["y", -1, 1], ["opacity", 0, 1], ["volume", 0, 2], ["pan", -1, 1],
    ["brightness", -1, 1], ["contrast", 0, 2], ["saturation", 0, 3], ["temperature", -1, 1], ["hue", -180, 180],
    ["shadows", -1, 1], ["highlights", -1, 1], ["fade_amount", 0, 1], ["sharpen", 0, 1], ["noise", 0, 20, true],
    ["fade_in", 0, 120], ["fade_out", 0, 120], ["bass_db", -12, 12], ["treble_db", -12, 12],
    ["font_size", 12, 160, true], ["outline", 0, 8], ["shadow", 0, 8], ["chroma_similarity", 0.01, 1], ["chroma_blend", 0, 1],
  ];
  for (const [key, min, max, integer] of ranges) numeric(c[key], min, max, key, integer);
  for (const key of ["reverse", "freeze", "mirror", "flip", "mute", "subtitle", "bold", "background"]) boolean(c[key], key);
  if (c.source_id !== null) identifier(c.source_id, "source_id");
  if (c.sequence_id !== null) identifier(c.sequence_id, "sequence_id");
  if (c.lut_id !== null) identifier(c.lut_id, "lut_id");
  choice(c.rotation, [0, 90, 180, 270], "rotation"); choice(c.fit, ["contain", "cover"], "fit");
  choice(c.audio_effect, ["none", "compressor", "limiter", "denoise", "invert", "normalize", "reverb"], "audio_effect");
  choice(c.color_preset, ["none", "warm", "cool", "cinema", "mono"], "color_preset");
  choice(c.text_animation, ["none", "typewriter", "fade", "pop"], "text_animation");
  choice(c.text_template, ["custom", "news", "outline", "gold", "note"], "text_template");
  const text = textValue(c.text, 500, "text");
  if (/[\x00-\x08\x0b-\x1f]/.test(text)) invalid("文字控制字符");
  if (typeof c.color !== "string" || !/^[\dA-Fa-f]{6}$/.test(c.color)
    || (c.chroma_color !== null && (typeof c.chroma_color !== "string" || !/^[\dA-Fa-f]{6}$/.test(c.chroma_color)))) invalid("颜色");
  const rawCrop = object(c.crop, ["x", "y", "width", "height"], "crop");
  const crop = {
    x: numeric(missing(rawCrop.x, 0), 0, 1, "crop.x"), y: numeric(missing(rawCrop.y, 0), 0, 1, "crop.y"),
    width: numeric(missing(rawCrop.width, 1), Number.MIN_VALUE, 1, "crop.width"),
    height: numeric(missing(rawCrop.height, 1), Number.MIN_VALUE, 1, "crop.height"),
  };
  if (crop.x >= 1 || crop.y >= 1 || crop.x + crop.width > 1.000001 || crop.y + crop.height > 1.000001) invalid("裁剪矩形越界");
  const frames = object(c.keyframes, ANIMATED_PROPERTIES, "keyframes"), keyframes: Keyframes = { x: [], y: [], scale: [], opacity: [] };
  for (const key of ANIMATED_PROPERTIES) {
    const low = key === "scale" ? 0.05 : key === "opacity" ? 0 : -1;
    const points = list(missing(frames[key], []), 8, `keyframes.${key}`).map(item => {
      const point = object(item, ["time", "value", "easing"], "关键帧点");
      return { time: numeric(point.time, 0, defaults.duration, "关键帧时间"), value: numeric(point.value, low, 1, "关键帧值"),
        easing: choice(missing(point.easing, "linear"), EASINGS, "easing") };
    });
    if (points.length && (points.length < 2 || points[0].time !== 0 || points.some((p, i) => i > 0 && p.time <= points[i - 1].time))) invalid("关键帧时间须从 0 开始严格递增");
    keyframes[key] = points;
  }
  let mask: ShapeMask | null = null;
  if (c.mask !== null) {
    const m = object(c.mask, ["type", "x", "y", "width", "height", "feather", "invert"], "mask");
    mask = { type: choice(m.type, ["rectangle", "ellipse"], "mask.type"),
      x: numeric(missing(m.x, 0.5), 0, 1, "mask.x"), y: numeric(missing(m.y, 0.5), 0, 1, "mask.y"),
      width: numeric(missing(m.width, 1), Number.MIN_VALUE, 1, "mask.width"), height: numeric(missing(m.height, 1), Number.MIN_VALUE, 1, "mask.height"),
      feather: numeric(missing(m.feather, 0), 0, 0.5, "mask.feather"), invert: boolean(missing(m.invert, false), "mask.invert") };
  }
  // Every scalar and nested member above has been checked; no unknown JSON is trusted.
  const clip = { ...c, crop, keyframes, mask, rgb_curves: normalizeRGBCurves(c.rgb_curves),
    transition_in: normalizeTransition(c.transition_in) } as unknown as StudioClip;
  if (clip.start + clip.duration > 120.000001 || clip.fade_in + clip.fade_out > clip.duration) invalid("片段时长 / 淡化包络");
  if (isSequenceClip(clip)) {
    // Exact default visual identity, including fit=contain. A typed parent
    // mute is inherited by every child audio leaf, without altering its clock.
    const identityFields = new Set(["id", "start", "duration", "sequence_id", "mute"]);
    for (const key of Object.keys(clip) as (keyof StudioClip)[]) {
      if (!identityFields.has(key) && JSON.stringify(clip[key]) !== JSON.stringify(defaults[key])) {
        invalid(`完整嵌套的 ${key} 必须为默认值；仅可移动 / 删除父片段或打开子序列编辑`);
      }
    }
  }
  if (clip.freeze && (clip.duration > 10 || clip.speed !== 1 || clip.reverse || !clip.mute)) invalid("定格要求静音、1×、不倒放且 ≤10 秒");
  if (isImageSourceId(clip.source_id) && Object.keys(IMAGE_CLIP_FIELDS).some(key => {
    const field = key as keyof typeof IMAGE_CLIP_FIELDS;
    return clip[field] !== IMAGE_CLIP_FIELDS[field];
  })) {
    invalid("静态图片须 trim=0、1×、静音、不倒放 / 定格且音频参数默认；duration 是显示时长");
  }
  if (clip.chroma_color === null && (clip.chroma_similarity !== 0.1 || clip.chroma_blend !== 0)) invalid("色度参数依赖");
  return clip;
}

/** Full schema-1 normalization for both API receipts and imports. Only ABSENT
 * additive fields receive defaults. Unknown keys fail, rather than being lost.
 * Authorizing source IDs, checking locks/revisions and probing EOF remain server work. */
export function normalizeProject(value: unknown): StudioProject {
  if (isRecord(value) && STUDIO_ACTIVE_VIEW in value) invalid("活动时间线只是工作视图，须映射回完整工程后保存");
  const raw = object(value, ["schema_version", "name", "tracks", "markers", "groups", "assets", "workspace", "sequences", "active_sequence_id"], "project");
  if (raw.schema_version !== 1) return invalid("schema_version");
  const ids = new Set<string>();
  const id = (v: unknown): string => { const key = identifier(v, "工程 ID"); if (ids.has(key)) invalid("重复工程 ID"); ids.add(key); return key; };
  const groups = list(missing(raw.groups, []), 8, "groups").map(value => {
    const g = object(value, ["id", "name"], "group"); return { id: id(g.id), name: textValue(g.name, 80, "group.name") };
  });
  const readTracks = (value: unknown): StudioTrack[] => list(value, 8, "tracks").map(value => {
    const t = object(value, ["id", "type", "name", "locked", "hidden", "solo", "color", "group_id", "clips"], "track");
    const type = choice<TrackType>(t.type, ["video", "audio", "text", "overlay", "adjustment"], "track.type");
    const trackId = id(t.id), group_id = t.group_id === undefined || t.group_id === null ? null : identifier(t.group_id, "group_id");
    if (group_id !== null && !groups.some(g => g.id === group_id)) invalid("不存在的轨道组");
    const clips = list(missing(t.clips, []), 64, "clips").map(item => { const clip = normalizeClip(item); id(clip.id); return clip; });
    const shared = ["id", "start", "duration"];
    const audio = ["source_id", "trim", "speed", "reverse", "volume", "pan", "mute", "fade_in", "fade_out", "audio_effect", "bass_db", "treble_db"];
    const allowed = new Set([...shared, ...(type === "adjustment" ? COLOR_FIELDS : type === "audio" ? audio : type === "text"
      ? ["text", "subtitle", "font_size", "color", "x", "y", "opacity", "fade_in", "fade_out", "bold", "outline", "shadow", "background", "text_animation", "text_template"]
      : [...audio, ...COLOR_FIELDS, "sequence_id", "freeze", "rotation", "mirror", "flip", "crop", "scale", "x", "y", "opacity", "fit", "keyframes", "mask", "sharpen", "noise", "chroma_color", "chroma_similarity", "chroma_blend", "transition_in"])]);
    for (const clip of clips) {
      if ((type === "video" || type === "overlay") && (!!clip.source_id === isSequenceClip(clip))) invalid("视频 / 叠加片段须且仅须有一个 source_id 或 sequence_id");
      if (type === "audio" && !clip.source_id) invalid("音频片段需要 source_id");
      if (isImageSourceId(clip.source_id) && type !== "video" && type !== "overlay") invalid("静态图片只能用于视频 / 叠加轨");
      if (type === "text" && !clip.text.trim()) invalid("文字片段不能为空");
      const defaults = makeClip({ id: clip.id, duration: clip.duration });
      for (const key of Object.keys(clip) as (keyof StudioClip)[]) {
        if (!allowed.has(key) && JSON.stringify(clip[key]) !== JSON.stringify(defaults[key])) invalid(`${key} 不适用于 ${type} 轨道`);
      }
    }
    for (let left = 0; left < clips.length; left++) for (let right = left + 1; right < clips.length; right++) {
      if ((isSequenceClip(clips[left]) || isSequenceClip(clips[right]))
        && Math.min(clips[left].start + clips[left].duration, clips[right].start + clips[right].duration)
          - Math.max(clips[left].start, clips[right].start) > 1e-12) invalid("完整嵌套不能与同轨其他片段重叠；请使用独立轨道");
    }
    return { id: trackId, type, name: textValue(missing(t.name, ""), 80, "track.name"), group_id, clips,
      locked: boolean(missing(t.locked, false), "locked"), hidden: boolean(missing(t.hidden, false), "hidden"), solo: boolean(missing(t.solo, false), "solo"),
      color: choice<TrackColor>(missing(t.color, "gray"), ["gold", "cyan", "red", "purple", "gray"], "track.color") };
  });
  const readMarkers = (value: unknown): StudioMarker[] => list(missing(value, []), 100, "markers").map(value => {
    const m = object(value, ["id", "time", "label"], "marker");
    return { id: id(m.id), time: numeric(m.time, 0, 120, "marker.time"), label: textValue(missing(m.label, ""), 120, "marker.label") };
  });
  const tracks = readTracks(raw.tracks), markers = readMarkers(raw.markers);
  const sequences = list(missing(raw.sequences, []), 4, "sequences").map(value => {
    const s = object(value, ["id", "name", "tracks", "markers"], "sequence");
    return { id: id(s.id), name: textValue(s.name, 80, "sequence.name"),
      tracks: readTracks(missing(s.tracks, [])), markers: readMarkers(s.markers) };
  });
  const active_sequence_id = raw.active_sequence_id === undefined || raw.active_sequence_id === null ? null : identifier(raw.active_sequence_id, "active_sequence_id");
  if (active_sequence_id !== null && !sequences.some(s => s.id === active_sequence_id)) invalid("活动序列不存在");
  const allTracks = [...tracks, ...sequences.flatMap(s => s.tracks)];
  if (allTracks.length > STUDIO_SEQUENCE_LIMITS.totalTracks) invalid("主时间线与所有序列合计最多 32 轨");
  if (allTracks.reduce((sum, track) => sum + track.clips.length, 0) > 64) invalid("主时间线与所有序列合计最多 64 片段");
  const assets = list(missing(raw.assets, []), 200, "assets").map(value => {
    const a = object(value, ["source_id", "rating", "tags"], "asset");
    return { source_id: identifier(a.source_id, "asset.source_id"), rating: numeric(missing(a.rating, 0), 0, 5, "rating", true),
      tags: list(missing(a.tags, []), 16, "tags").map(t => textValue(t, 40, "tag", 1)) };
  });
  if (new Set(assets.map(a => a.source_id)).size !== assets.length) invalid("重复素材元数据");
  const workspace = normalizeWorkspace(raw.workspace);
  validateTransitions(allTracks, workspace.timeline_fps);
  const project: StudioProject = { schema_version: 1, name: textValue(missing(raw.name, "Studio"), 120, "project.name"), tracks, markers, groups, assets,
    workspace, sequences, active_sequence_id };
  validateSequenceGraph(project);
  return project;
}

/** Full output from local zero through the last visible / solo clip. Markers,
 * hidden tracks and non-solo tracks do not extend the rendered child clock. */
export function sequenceFullEnd(scope: Pick<StudioProject, "tracks">): number {
  const solo = scope.tracks.some(track => track.solo && !track.hidden);
  return Math.max(0, ...scope.tracks.filter(track => !track.hidden && (!solo || track.solo))
    .flatMap(track => track.clips.map(clip => clip.start + clip.duration)));
}

function validateSequenceGraph(project: StudioProject): void {
  const byId = new Map(project.sequences.map(sequence => [sequence.id, sequence]));
  const visit = (tracks: StudioTrack[], path: string[], depth: number): void => {
    // ALL references count, including hidden / non-solo and inactive sequences.
    for (const track of tracks) for (const clip of track.clips) {
      if (!isSequenceClip(clip)) continue;
      const child = byId.get(clip.sequence_id);
      if (!child) invalid("嵌套引用的子序列不存在");
      if (path.includes(child.id)) invalid("嵌套序列不能循环引用（包括未激活序列）");
      if (depth >= 2) invalid("嵌套最多两层：主时间线 → A → B");
      const end = sequenceFullEnd(child);
      if (!(end > 0) || Math.abs(clip.duration - end) > 1e-12) {
        invalid(`嵌套 ${clip.id} 的 duration 须等于子序列 ${child.id} 的完整生效终点 ${end}s；请明确确认统一更新父时长，不能自动裁剪 / 补帧`);
      }
      visit(child.tracks, [...path, child.id], depth + 1);
    }
  };
  visit(project.tracks, [], 0);
  // A named timeline may itself be the render root. Check every disconnected
  // graph too: no path may contain more than two actual reference edges.
  for (const sequence of project.sequences) visit(sequence.tracks, [sequence.id], 0);
}

export interface SequenceRenderStats {
  tracks: number; clips: number; inputs: number; imageInputs: number; lutBindings: number;
  sourceIds: string[]; visualSourceIds: string[]; lutIds: string[]; nestedText: boolean; duration: number;
}
/** Resource prediction, NOT a preview. Count each repeated child occurrence
 * and each image decoder; a LUT file is a filter binding, NOT another decoder.
 * Ordinary clips keep one layer, followed by child layers. Empty visible lanes
 * consume storage limits, not render layers. Nests cannot overlap on one lane. */
export function sequenceRenderStats(project: StudioProject): SequenceRenderStats {
  const checked = normalizeProject(project);
  const byId = new Map(checked.sequences.map(sequence => [sequence.id, sequence]));
  const scope = checked.active_sequence_id === null ? checked : byId.get(checked.active_sequence_id)!;
  const stats: SequenceRenderStats = { tracks: 0, clips: 0, inputs: 0, imageInputs: 0, lutBindings: 0,
    sourceIds: [], visualSourceIds: [], lutIds: [], nestedText: false, duration: sequenceFullEnd(scope) };
  const sources = new Set<string>(), visualSources = new Set<string>(), luts = new Set<string>();
  const visit = (tracks: StudioTrack[], depth: number) => {
    const solo = tracks.some(track => track.solo && !track.hidden);
    for (const track of tracks.filter(track => !track.hidden && (!solo || track.solo))) {
      const ordinary = track.clips.filter(clip => !isSequenceClip(clip)), nested = track.clips.filter(isSequenceClip);
      if (ordinary.length) stats.tracks++;
      for (const clip of ordinary) {
        stats.clips++;
        if (["video", "overlay", "audio"].includes(track.type)) {
          stats.inputs++; sources.add(clip.source_id!);
          if (track.type !== "audio") visualSources.add(clip.source_id!);
          if (isImageSourceId(clip.source_id)) stats.imageInputs++;
        }
        if (clip.lut_id) { stats.lutBindings++; luts.add(clip.lut_id); }
        if (depth > 0 && track.type === "text") stats.nestedText = true;
      }
      for (const clip of nested) visit(byId.get(clip.sequence_id)!.tracks, depth + 1);
    }
  };
  visit(scope.tracks, 0);
  stats.sourceIds = [...sources]; stats.visualSourceIds = [...visualSources]; stats.lutIds = [...luts];
  return stats;
}

function envelope(value: unknown): ProjectEnvelope {
  if (!isRecord(value) || typeof value.revision !== "number" || !Number.isSafeInteger(value.revision) || value.revision < 0
    || typeof value.can_undo !== "boolean" || typeof value.can_redo !== "boolean") invalid("工程响应");
  return { revision: value.revision, project: normalizeProject(value.project), can_undo: value.can_undo, can_redo: value.can_redo };
}
function jobOptions(value: unknown): ExportOptions {
  if (!isRecord(value)) return invalid("任务参数");
  return {
    format: choice(value.format, FORMATS, "任务格式"),
    resolution: choice(value.resolution, [360, 720, 1080], "任务分辨率"),
    fps: choice(value.fps, [24, 25, 30, 60], "任务帧率"),
    aspect: choice(value.aspect, ["16:9", "9:16", "1:1"], "任务画幅"),
    subtitles: choice(value.subtitles, ["standard", "large", "none"], "任务字幕参数"),
    video_bitrate_kbps: numeric(value.video_bitrate_kbps, 250, 12000, "任务视频码率", true),
    audio_bitrate_kbps: choice(value.audio_bitrate_kbps, [96, 128, 192, 256, 320], "任务音频码率"),
    frame_time: value.frame_time === undefined || value.frame_time === null ? null : numeric(value.frame_time, 0, 120, "任务取帧时间"),
  };
}
function jobResponse(value: unknown): StudioJob {
  if (!isRecord(value) || typeof value.id !== "string" || !/^[a-f0-9]{32}$/.test(value.id)) {
    throw new Error("Studio 任务响应不兼容；请刷新任务状态，勿重复提交。");
  }
  // No String(value), truthy booleans, guessed states, progress or success URLs.
  const kind = choice(value.kind, ["render", "export", "proxy"], "任务种类");
  const state = choice(value.state, ["queued", "running", "succeeded", "failed", "cancelled", "interrupted"], "任务状态");
  numeric(value.revision, 0, Number.MAX_SAFE_INTEGER, "任务工程修订", true);
  if (value.pipeline_revision !== undefined || kind === "proxy") numeric(value.pipeline_revision, 0, Number.MAX_SAFE_INTEGER, "任务成片修订", true);
  numeric(value.created_at, 0, Number.MAX_SAFE_INTEGER, "任务创建时间");
  if (value.finished_at !== undefined) numeric(value.finished_at, 0, Number.MAX_SAFE_INTEGER, "任务结束时间");
  if (value.output_id !== null && value.output_id !== value.id) invalid("任务输出标识");
  if ((state !== "succeeded" && value.output_id !== null)
    || (state === "succeeded" && value.output_id !== value.id)) invalid("任务输出状态");
  if (value.error !== null) textValue(value.error, 4000, "任务错误");
  if (value.cleanup_pending !== undefined || value.cleanup_error !== undefined) {
    const pending = boolean(value.cleanup_pending, "任务残留清理状态");
    if (pending) {
      if (!["failed", "cancelled", "interrupted"].includes(state) || value.result !== undefined) invalid("任务残留清理与输出状态");
      if (!textValue(value.cleanup_error, 256, "任务残留清理提示", 1).trim()) invalid("任务残留清理提示");
    } else if (value.cleanup_error !== null) invalid("已清理任务的残留提示");
  }
  textValue(value.qc, 4000, "任务 QC 说明"); boolean(value.disclosure, "任务披露状态");
  const options = jobOptions(value.options);
  if (kind === "proxy") {
    object(value.options, Object.keys(DEFAULT_EXPORT), "代理固定参数字段");
    if (!isRawProxySourceId(value.source_id)) invalid("代理仅接受原目录视频源 ID");
    if (typeof value.cache_key !== "string" || !/^[a-f0-9]{64}$/.test(value.cache_key)) invalid("代理缓存标识");
    if (value.cached !== undefined) boolean(value.cached, "代理缓存回执");
    if (value.cached === true && state !== "succeeded") invalid("未完成的代理不能宣称缓存命中");
    if (state === "succeeded" && !isRecord(value.result)) invalid("已完成代理缺少结果记录");
    if (options.format !== STUDIO_PROXY_PROFILE.format || options.resolution !== STUDIO_PROXY_PROFILE.resolution
      || options.fps !== STUDIO_PROXY_PROFILE.fps || options.aspect !== STUDIO_PROXY_PROFILE.aspect
      || options.subtitles !== STUDIO_PROXY_PROFILE.subtitles || options.video_bitrate_kbps !== STUDIO_PROXY_PROFILE.video_bitrate_kbps
      || options.audio_bitrate_kbps !== STUDIO_PROXY_PROFILE.audio_bitrate_kbps || options.frame_time !== null) invalid("代理固定参数");
  } else if (value.source_id !== undefined || value.cache_key !== undefined || value.cached !== undefined) {
    invalid("源代理元数据不能冒充渲染 / 导出");
  }
  const semantics = value.export_semantics;
  if (semantics !== undefined && (!isRecord(semantics)
    || !["picture", "audio", "framing"].every(key => typeof semantics[key] === "string")
    || !Array.isArray(semantics.warnings) || !semantics.warnings.every(w => typeof w === "string"))) {
    throw new Error("Studio 导出语义响应不兼容。");
  }
  if (value.result !== undefined) {
    if (!isRecord(value.result)) invalid("任务结果");
    textValue(value.result.file, 255, "结果文件名", 1); // Display-only, NEVER a media URL.
    numeric(value.result.bytes, 1, Number.MAX_SAFE_INTEGER, "结果字节数", true);
    numeric(value.result.duration, Number.MIN_VALUE, kind === "proxy" ? STUDIO_PROXY_PROFILE.max_source_seconds : 3600, "结果时长");
    const applied = jobOptions(value.result.applied);
    if (kind === "proxy" && JSON.stringify(applied) !== JSON.stringify(options)) invalid("代理实际应用参数不一致");
    if (value.result.frame_time !== undefined && value.result.frame_time !== null) numeric(value.result.frame_time, 0, 120, "结果取帧时间");
  }
  return { ...value, options } as unknown as StudioJob;
}
function proxyResponse(value: unknown): StudioProxyJob {
  const job = jobResponse(value);
  if (!isProxyJob(job)) return invalid("源代理响应种类");
  return job;
}
/** A bounded read of persisted jobs, not source-catalog entries. Reject the
 * whole incompatible response; do not turn unknown rows into pending/success. */
export function normalizeStudioProxies(value: unknown): StudioProxyJob[] {
  const raw = object(value, ["proxies"], "代理列表");
  const jobs = list(raw.proxies, STUDIO_MAX_JOBS, "代理列表").map(proxyResponse);
  if (new Set(jobs.map(job => job.id)).size !== jobs.length) invalid("重复代理任务 ID");
  return jobs;
}
/** Common render/export/proxy history. Late GETs cannot resurrect a cancelled
 * job; a succeeded proxy MAY become failed if source/hash/revision is invalidated. */
export function upsertStudioJob(jobs: readonly StudioJob[], job: StudioJob): StudioJob[] {
  const previous = jobs.find(row => row.id === job.id);
  if (previous) {
    if (previous.kind !== job.kind || previous.revision !== job.revision || previous.pipeline_revision !== job.pipeline_revision
      || previous.source_id !== job.source_id || previous.cache_key !== job.cache_key) {
      throw new Error("同一作业的类型 / 源 / 修订 / 缓存标识发生变化，未采用不兼容状态；请刷新确认。");
    }
    if (previous.state === "running" && job.state === "queued") return [...jobs];
    if (!isActiveJob(previous) && (isActiveJob(job) || (previous.state !== "succeeded" && job.state === "succeeded"))) return [...jobs];
    if (!isActiveJob(previous) && previous.cleanup_pending === false && job.cleanup_pending === true) return [...jobs];
  }
  return [job, ...jobs.filter(row => row.id !== job.id)].sort((a, b) => b.created_at - a.created_at || a.id.localeCompare(b.id)).slice(0, STUDIO_MAX_JOBS);
}

export function createStudioApi(taskId: string, request: StudioRequest, accessToken?: string) {
  const root = `/api/tasks/${encodeURIComponent(taskId)}/studio`;
  const headers = (contentType: string): Headers => {
    const result = new Headers({ "Content-Type": contentType });
    if (accessToken) result.set("X-Task-Token", accessToken);
    return result;
  };
  const call = <T,>(path: string, init: RequestInit = {}, contentType = "application/json") => request<T>(root + path, {
    credentials: "include", cache: "no-store", ...init, headers: headers(contentType),
  });
  const post = <T,>(path: string, body: unknown, init: RequestInit = {}) => {
    const json = JSON.stringify(body);
    if (new TextEncoder().encode(json).length > 262144) throw new Error("Studio 请求超过 256 KiB 限制。");
    return call<T>(path, { ...init, method: "POST", body: json });
  };
  // Never attach credentials to a catalog-supplied external URL. Use known routes.
  const media = (path: string) => {
    const url = `${root}${path}`;
    return accessToken ? `${url}?token=${encodeURIComponent(accessToken)}` : url;
  };
  const jobId = (id: string) => {
    if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id)) throw new Error("无效的 Studio 任务标识。");
    return id;
  };
  return {
    async capabilities(signal?: AbortSignal): Promise<StudioCapabilities> {
      const value = await call<unknown>("/capabilities", { signal });
      if (!isRecord(value) || value.schema_version !== 1 || !Array.isArray(value.tools)
        || value.tool_count !== value.tools.length || !isRecord(value.limits)
        || !value.tools.every(t => isRecord(t) && typeof t.id === "string" && typeof t.label === "string"
          && typeof t.reason === "string" && typeof t.available === "boolean" && GROUPS.some(([g]) => g === t.group)
          && ["unsupported", "partial", "metadata", "renderer"].includes(String(t.classification))
          && t.available === (t.classification !== "unsupported"))
        || typeof value.render_available !== "boolean" || typeof value.quality !== "string"
        || !Array.isArray(value.unsupported) || !value.unsupported.every(item => typeof item === "string")
        || !isRecord(value.project_schema) || !isRecord(value.action_schema) || !isRecord(value.export_schema)
        || new Set(value.tools.map(t => (t as StudioTool).id)).size !== value.tools.length) {
        throw new Error("Studio 能力清单不兼容；未启用未经声明的功能。");
      }
      for (const key of ["tracks", "clips", "duration_seconds", "output_bytes", "task_output_bytes", "jobs_per_task", "history_mutations",
        "threads", "global_jobs", "max_resolution", "request_bytes"]) numeric(value.limits[key], 1, Number.MAX_SAFE_INTEGER, `limits.${key}`, true);
      for (const key of ["source_marks", "rgb_knots_per_channel", "subtitle_bytes", "proxy_duration_seconds", "proxy_width", "proxy_height", "proxy_fps",
        "sequences", "total_tracks", "markers_per_timeline", "nesting_depth", "expanded_tracks", "expanded_clips", "decoder_inputs"]) {
        if (value.limits[key] !== undefined) numeric(value.limits[key], 1, Number.MAX_SAFE_INTEGER, `limits.${key}`, true);
      }
      if (value.proxy_request_schema !== undefined && !isRecord(value.proxy_request_schema)) invalid("proxy_request_schema");
      return value as unknown as StudioCapabilities;
    },
    async project(signal?: AbortSignal) { return envelope(await call<unknown>("/project", { signal })); },
    async backup() { return envelope(await call<unknown>("/project/export")); },
    async save(revision: number, project: StudioProject) {
      return envelope(await post<unknown>("/project", { expected_revision: revision, project: normalizeProject(project) }));
    },
    async importProject(revision: number, project: unknown) {
      return envelope(await post<unknown>("/project/import", { expected_revision: revision, project: normalizeProject(project) }));
    },
    async importSubtitles(revision: number, trackId: string, srt: string) {
      if (!srt.trim() || [...srt].length > 64000) throw new Error("SRT 必须为 1–64,000 字符，时间码和内容由服务器验证。");
      return envelope(await post<unknown>("/subtitles/import", { expected_revision: revision, track_id: trackId, srt }));
    },
    async action(revision: number, action: StudioAction) {
      return envelope(await post<unknown>("/actions", { expected_revision: revision, ...action }));
    },
    async sources(signal?: AbortSignal): Promise<SourceCatalog> {
      return normalizeSourceCatalog(await call<unknown>("/sources", { signal }));
    },
    async proxies(signal?: AbortSignal): Promise<StudioProxyJob[]> {
      return normalizeStudioProxies(await call<unknown>("/proxies", { method: "GET", signal, mode: "same-origin", redirect: "error" }));
    },
    async createProxy(revision: number, sourceId: string, signal?: AbortSignal): Promise<StudioProxyJob> {
      numeric(revision, 0, Number.MAX_SAFE_INTEGER, "expected_revision", true);
      if (!isRawProxySourceId(sourceId)) throw new Error("代理仅接受此任务原目录视频的 source_id，不接受图片、录音、Studio 输出或地址。");
      const job = proxyResponse(await post<unknown>("/proxies", { expected_revision: revision, source_id: sourceId },
        { signal, mode: "same-origin", redirect: "error" }));
      // A cached response is the SAME historical job, not a new revision. Never
      // guess +1, relabel the job, or retry a POST after an uncertain response.
      if (job.source_id !== sourceId || job.revision > revision || (job.cached !== true && job.revision !== revision)) {
        throw new Error("代理回执的源或捕获修订不一致；请查询代理状态，勿重复创建。");
      }
      return job;
    },
    async assets(signal?: AbortSignal): Promise<StudioAssetCatalog> {
      return normalizeStudioAssets(await call<unknown>("/assets", { signal, mode: "same-origin", redirect: "error" }));
    },
    async importImage(revision: number, file: File, signal?: AbortSignal): Promise<StudioAssetReceipt<StudioImageAsset>> {
      numeric(revision, 0, Number.MAX_SAFE_INTEGER, "expected_revision", true);
      const contentType = validateStudioAssetFile("image", file);
      const value = await call<unknown>(`/assets/image?expected_revision=${revision}`, {
        method: "POST", body: file, signal, mode: "same-origin", redirect: "error",
      }, contentType);
      return assetReceipt(value, revision, normalizeImageAsset);
    },
    async importLut(revision: number, file: File, signal?: AbortSignal): Promise<StudioAssetReceipt<StudioLutAsset>> {
      numeric(revision, 0, Number.MAX_SAFE_INTEGER, "expected_revision", true);
      const contentType = validateStudioAssetFile("lut", file);
      const value = await call<unknown>(`/assets/lut?expected_revision=${revision}`, {
        method: "POST", body: file, signal, mode: "same-origin", redirect: "error",
      }, contentType);
      return assetReceipt(value, revision, normalizeLutAsset);
    },
    async audit(signal?: AbortSignal): Promise<AuditRecord[]> {
      const value = await call<unknown>("/audit", { signal });
      if (!isRecord(value) || !Array.isArray(value.records) || !value.records.every(r => isRecord(r)
        && typeof r.id === "string" && typeof r.time === "number" && typeof r.op === "string")) {
        throw new Error("Studio 审计响应不兼容。");
      }
      return value.records as AuditRecord[];
    },
    async submit(kind: "render" | "export", revision: number, options: ExportOptions) {
      choice(kind, ["render", "export"], "导出提交种类");
      const job = jobResponse(await post<unknown>(`/${kind}`, { expected_revision: revision, options }));
      if (job.kind !== kind || job.revision !== revision) invalid("渲染 / 导出回执类型或修订");
      return job;
    },
    async job(id: string, signal?: AbortSignal) {
      const job = jobResponse(await call<unknown>(`/jobs/${jobId(id)}`, { signal }));
      if (job.id !== id) invalid("查询作业 ID 不一致");
      return job;
    },
    async cancel(id: string) {
      const job = jobResponse(await call<unknown>(`/jobs/${jobId(id)}`, { method: "DELETE" }));
      if (job.id !== id) invalid("取消作业 ID 不一致");
      return job;
    },
    // Original quality selection, like proxy/image previews, stays same-origin.
    sourceUrl: (id: string) => media(`/sources/${encodeURIComponent(identifier(id, "source.id"))}`),
    proxyUrl: (id: string) => media(`/proxies/${jobId(id)}/media`),
    outputUrl: (id: string) => media(`/outputs/${jobId(id)}`),
  };
}
export type StudioApi = ReturnType<typeof createStudioApi>;

/** Validate and hydrate the actual schema, without installing imported content.
 * The server still authorizes references, locks and revisions before commit. */
export function parseProjectImport(text: string, maxBytes = 256 * 1024): StudioProject {
  if (new TextEncoder().encode(text).length > maxBytes - 128) throw new Error("工程 JSON 超出请求大小限制。");
  const value: unknown = JSON.parse(text);
  const project = isRecord(value) && "project" in value ? value.project : value;
  if (!isRecord(project) || project.schema_version !== 1 || !Array.isArray(project.tracks)) {
    throw new Error("请选择 schema_version: 1 的 Studio 工程或工程备份。");
  }
  return normalizeProject(project);
}

export function projectWarnings(project: StudioProject, durations: Record<string, number> = {}, sources?: readonly StudioSource[], luts?: readonly StudioLutAsset[]): string[] {
  const issues: string[] = [];
  let checked: StudioProject;
  try { checked = normalizeProject(project); }
  catch (e) { return [e instanceof Error ? e.message : "工程字段无效；未丢弃任何内容。"]; }
  const stats = sequenceRenderStats(checked);
  if (stats.tracks > 8 || stats.clips > 64 || stats.inputs > 16) issues.push(`当前序列展开后为 ${stats.tracks} 轨 / ${stats.clips} 片段 / ${stats.inputs} 解码输入（图片与重复引用逐次计数）；上限 8 / 64 / 16。`);
  // Validate even inactive references. Passing no catalog means "not loaded",
  // not permission to fabricate membership; an explicitly empty catalog is final.
  for (const entry of [...checked.assets, ...checked.workspace.source_marks]) {
    if (sources && !sources.some(source => source.id === entry.source_id)) issues.push("素材元数据 / 源入出点引用了当前目录之外的 source_id。");
  }
  for (const track of [...checked.tracks, ...checked.sequences.flatMap(sequence => sequence.tracks)]) {
    for (const clip of track.clips) {
      if (isSequenceClip(clip)) continue;
      if (clip.lut_id && luts && !luts.some(lut => lut.id === clip.lut_id)) issues.push(`${track.name}：LUT 不在此任务已读取的真实素材目录中。`);
      const source = sources?.find(item => item.id === clip.source_id);
      if (clip.source_id && sources && !source) issues.push(`${track.name}：片段 source_id 不在此任务当前素材目录中。`);
      if (isImageSourceId(clip.source_id) || (source && isImageSource(source))) {
        if (clip.trim !== 0 || clip.speed !== 1 || clip.reverse || clip.freeze || !clip.mute || !["video", "overlay"].includes(track.type)) {
          issues.push(`${track.name}：静态图片仅限视频 / 叠加轨，须静音、trim=0、1×、不倒放 / 定格。`);
        }
        if (clip.duration > (source?.duration ?? 120)) issues.push(`${track.name}：图片显示时长超过支持范围；不是源媒体 EOF。`);
        continue; // Still images have no playback clock, even if an old duration map contains their IDs.
      }
      const known = clip.source_id ? [durations[clip.source_id], source?.duration].filter((value): value is number => value !== undefined) : [];
      const duration = known.length ? Math.min(...known) : undefined;
      if (duration !== undefined && (clip.freeze ? clip.trim >= duration : clip.trim + clip.duration * clip.speed > duration + 0.001)) issues.push(`${track.name}：${clip.freeze ? "定格取帧入点必须小于源时长" : "入点 + 输出时长 × 速度超出素材，禁止自动补帧"}。`);
    }
  }
  return [...new Set(issues)];
}