import type { PublicLimits } from "../types";

export const MEDIA_ACCEPT = ".mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.gif,video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,image/jpeg,image/png,image/gif";
export const AUDIO_ACCEPT = ".mp3,.wav,.m4a,.ogg,.webm,audio/mpeg,audio/wav,audio/mp4,audio/ogg,audio/webm";

/** Optional runtime extensions; the shared PublicLimits contract remains unchanged. */
export type MediaLimits = PublicLimits & {
  max_source_duration_seconds_per_file?: number;
  max_total_source_duration_seconds?: number;
  max_duration_seconds_per_file?: number;
  max_total_duration_seconds?: number;
};

/** JSON-safe only: never put File, Blob, data URLs or object URLs in a draft. */
export interface MediaMetadata {
  id: string;
  name: string;
  size: number;
  lastModified: number;
  type: string;
  kind: "video" | "image" | "audio";
  duration: number | null;
  width?: number;
  height?: number;
  note: string;
  trim_start: number;
  trim_end: number | null;
}

export interface MediaSelection extends MediaMetadata {
  file?: File;
  thumbnail?: string;
  status: "reselect" | "loading" | "ready" | "error";
  error?: string;
}

const positive = (value: number | undefined, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) && value > 0 ? value : fallback;

export function mediaLimits(limits: MediaLimits) {
  return {
    maxFiles: Math.min(20, Math.floor(positive(limits.max_files, 20))),
    maxBytes: Math.min(500 * 1024 ** 2, positive(limits.max_upload_bytes, 500 * 1024 ** 2)),
    maxTotalBytes: positive(limits.max_total_upload_bytes, 5 * 1024 ** 3),
    maxScript: Math.floor(positive(limits.max_script_length, 8000)),
    maxDuration: Math.min(1800, positive(limits.max_video_duration_seconds ?? limits.max_source_duration_seconds_per_file ?? limits.max_duration_seconds_per_file, 1800)),
    maxTotalDuration: Math.min(3600, positive(limits.max_total_video_duration_seconds ?? limits.max_total_source_duration_seconds ?? limits.max_total_duration_seconds, 3600)),
  };
}

export function fileIdentity(file: Pick<File, "name" | "size" | "lastModified">): string {
  return JSON.stringify([file.name, file.size, file.lastModified]);
}

export function mediaKind(name: string): "image" | "video" | null {
  if (/\.(jpe?g|png|gif)$/i.test(name)) return "image";
  if (/\.(mp4|mov|avi|mkv)$/i.test(name)) return "video";
  return null;
}

export function formatBytes(bytes: number): string {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GiB` : `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

export function formatDuration(seconds: number): string {
  const rounded = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(rounded / 60)} 分 ${rounded % 60} 秒`;
}

export function validateFileSelection(file: File, selected: MediaSelection[], limits: PublicLimits): string | null {
  const cap = mediaLimits(limits);
  if (!mediaKind(file.name)) return "仅支持 mp4、mov、avi、mkv、jpg、png、gif 文件。";
  if (file.size <= 0) return "文件为空，请重新导出。";
  if (mediaKind(file.name) === "image" && file.size > 50 * 1024 ** 2) return "照片不能超过 50 MiB。";
  if (file.size > cap.maxBytes) return `单文件不能超过 ${formatBytes(cap.maxBytes)}。`;
  if (selected.some((item) => item.file && item.id === fileIdentity(file))) return "这个文件已选择，请不要重复添加。";
  const replacing = selected.some((item) => !item.file && item.id === fileIdentity(file));
  if (!replacing && selected.length >= cap.maxFiles) return `最多选择 ${cap.maxFiles} 个素材；请先移除不需要的文件。`;
  const actualBytes = selected.filter((item) => item.file).reduce((sum, item) => sum + item.size, 0);
  if (actualBytes + file.size > cap.maxTotalBytes) return `素材总量不能超过 ${formatBytes(cap.maxTotalBytes)}。`;
  return null;
}

export function validateTrim(item: Pick<MediaMetadata, "kind" | "duration" | "trim_start" | "trim_end">): string | null {
  if (item.kind === "image") return null;
  const { duration, trim_start: start, trim_end: end } = item;
  if (duration == null || !Number.isFinite(duration) || duration <= 0) return "尚未读到有效时长。";
  if (!Number.isInteger(start) || end == null || !Number.isInteger(end) && end !== duration || start < 0 || end <= start || end > duration) {
    return "裁剪请填整秒：0 ≤ 开始 < 结束 ≤ 原始时长。";
  }
  return null;
}

export function serializeMedia(item: MediaMetadata): MediaMetadata {
  return {
    id: item.id, name: item.name, size: item.size, lastModified: item.lastModified,
    type: item.type, kind: item.kind, duration: item.duration,
    width: item.width, height: item.height, note: item.note,
    trim_start: Number.isFinite(item.trim_start) ? item.trim_start : 0,
    trim_end: item.trim_end != null && Number.isFinite(item.trim_end) ? item.trim_end : null,
  };
}

export function selectionIssues(items: MediaSelection[], limits: PublicLimits): string[] {
  const cap = mediaLimits(limits);
  const issues: string[] = [];
  if (!items.length) issues.push("至少选择 1 个真实视频或照片。");
  if (items.length > cap.maxFiles) issues.push(`最多 ${cap.maxFiles} 个素材。`);
  if (items.some((item) => !item.file || item.status === "reselect")) issues.push("草稿中的文件需要重新选择，未重新选择的可移除。");
  if (items.some((item) => item.status === "loading")) issues.push("正在读取文件信息，请稍候。");
  if (items.some((item) => item.status === "error")) issues.push("请移除或重新选择无法读取的文件。");
  if (items.some((item) => item.size > cap.maxBytes)) issues.push("有文件超过单文件大小限制。");
  if (items.some(item => item.kind === "image" && item.size > 50 * 1024 ** 2)) issues.push("照片不能超过 50 MiB。");
  if (items.reduce((sum, item) => sum + (item.file ? item.size : 0), 0) > cap.maxTotalBytes) issues.push("文件总量超出限制。");
  if (items.some((item) => item.duration != null && item.duration > cap.maxDuration)) issues.push("有文件超过单文件原始时长限制，裁剪不能绕过上传限制。");
  if (items.reduce((sum, item) => sum + (item.duration ?? 0), 0) > cap.maxTotalDuration) issues.push("素材原始总时长超过限制。");
  if (items.some((item) => item.status === "ready" && validateTrim(item))) issues.push("请修正素材裁剪时间。");
  if (items.some((item) => Array.from(item.note).length > 20)) issues.push("每个素材的备注最多 20 字。");
  return issues;
}

export function estimateSufficiency(items: MediaSelection[], sentenceCount: number, spokenChars: number, cpm: number,
  speechByFile: ReadonlyMap<string, boolean | null> = new Map()) {
  const ready = items.filter((item) => item.status === "ready" && !validateTrim(item));
  const seconds = ready.reduce((sum, item) => sum + (item.kind === "image" ? 3 : Math.max(0, (item.trim_end ?? 0) - item.trim_start)), 0);
  // A planning estimate, NOT scene detection or a promise of one usable shot per file.
  // Speech evidence is supplied separately by the caller's current upload binding,
  // never inferred from local file metadata. Unknown uses the ordinary estimate.
  const possibleShots = ready.reduce((sum, item) => sum + (item.kind === "image" || item.kind === "video" && speechByFile.get(item.id) === true
    ? 1 : Math.max(1, Math.floor(((item.trim_end ?? 0) - item.trim_start) / 12))), 0);
  const unknownSpeechVideos = ready.filter(item => item.kind === "video"
    && speechByFile.get(item.id) !== true && speechByFile.get(item.id) !== false).length;
  const narrationSeconds = spokenChars / cpm * 60;
  const ratio = Math.min(possibleShots / Math.max(1, sentenceCount), seconds / Math.max(1, narrationSeconds));
  return { seconds, possibleShots, unknownSpeechVideos, narrationSeconds, percent: Math.min(100, Math.round(ratio * 100)), enough: ratio >= 1 };
}

export const ELEMENT_RULES = [
  { key: "time", label: "何时", rule: /(\d+月\d+日|\d{4}年|今天|昨天|上午|下午|近日|日前)/ },
  { key: "place", label: "何地", rule: /(\d+号|区|广场|街道|市场|展馆|社区|公园|会场)/ },
  { key: "who", label: "何人", rule: /(市民|居民|游客|商户|工作人员|负责人|先生|女士|企业|记者|志愿者|商家)/ },
  { key: "what", label: "何事", rule: /(举办|开展|举行|开幕|进行|参加|启动|发布|比赛)/ },
  { key: "why", label: "为何", rule: /(为了|旨在|目的|以便|因为|由于|让.{1,10}(感受|了解|体验))/ },
] as const;
export type ElementKey = typeof ELEMENT_RULES[number]["key"];

export const countSpokenChars = (text: string) => Array.from(text.replace(/[\s，。、“”；：！？,.!?‘’「」『』（）()—…]/g, "")).length;

export function shotAdvice(text: string): string {
  if (/采访|说|表示|介绍/.test(text)) return "建议：征得同意后，拍说话的人和相关现场；保留完整一句原声。";
  if (/\d+月|\d+日|主办|承办|\d+号/.test(text)) return "建议：拍清日期、地址或主办方标识，另找可靠来源核实。";
  if (/、/.test(text)) return "建议：列举的每样物品各拍一个清楚的特写，不用无关画面代替。";
  return "建议：拍与这句话对应的真实行动或物品，远景交代环境，再补特写。";
}

interface ProbeResult { duration: number; width?: number; height?: number; thumbnail?: string }

export class BrowserProbeError extends Error {
  constructor(public readonly code: "unsupported" | "timeout" | "invalid", message: string) {
    super(message);
    this.name = "BrowserProbeError";
  }
}

export function canServerProbe(error: unknown, kind: MediaMetadata["kind"]): boolean {
  return kind === "video" && error instanceof BrowserProbeError
    && (error.code === "unsupported" || error.code === "timeout");
}

/** The caller owns returned thumbnail URLs. Source URLs are always released here. */
export async function probeMedia(file: File, kind: "image" | "video" | "audio", signal?: AbortSignal, maxDuration = 1800): Promise<ProbeResult> {
  if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
  const source = URL.createObjectURL(file);
  let image: HTMLImageElement | undefined;
  let media: HTMLMediaElement | undefined;
  let thumbnail: string | undefined;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort: (() => void) | undefined;
  try {
    return await new Promise<ProbeResult>((resolve, reject) => {
      let settled = false;
      const finish = (value?: ProbeResult, error?: Error) => {
        if (settled) return;
        settled = true;
        if (error) reject(error); else resolve(value!);
      };
      abort = () => finish(undefined, new DOMException("Cancelled", "AbortError"));
      signal?.addEventListener("abort", abort, { once: true });
      timer = setTimeout(() => finish(undefined, new BrowserProbeError("timeout", "浏览器读取超时，尚未完成素材校验。")), 20000);
      const fail = () => finish(undefined, new BrowserProbeError("unsupported", "浏览器无法解码这个文件，尚未完成素材校验。"));
      const invalid = () => finish(undefined, new BrowserProbeError("invalid", "文件时长或画面尺寸无效。"));
      const capture = (element: CanvasImageSource, width: number, height: number, duration: number) => {
        if (settled || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) { if (!settled) invalid(); return; }
        try {
          const canvas = document.createElement("canvas");
          canvas.width = 240;
          canvas.height = Math.max(1, Math.round(240 * height / width));
          // Limit unusually tall input before allocating the drawing buffer.
          canvas.height = Math.min(canvas.height, 320);
          const context = canvas.getContext("2d");
          if (!context) { finish({ duration, width, height }); return; }
          context.drawImage(element, 0, 0, canvas.width, canvas.height);
          canvas.toBlob((blob) => {
            if (settled || disposed) return;
            thumbnail = blob ? URL.createObjectURL(blob) : undefined;
            finish({ duration, width, height, thumbnail });
          }, "image/jpeg", 0.8);
        } catch {
          // A failed thumbnail is not a failed duration probe; show an honest placeholder.
          finish({ duration, width, height });
        }
      };
      if (kind === "image") {
        image = new Image();
        image.onload = () => capture(image!, image!.naturalWidth, image!.naturalHeight, 3);
        image.onerror = fail;
        image.src = source;
      } else {
        media = document.createElement(kind === "video" ? "video" : "audio");
        media.preload = "auto";
        media.onloadedmetadata = () => {
          if (!Number.isFinite(media!.duration) || media!.duration <= 0 || media!.duration > maxDuration) { invalid(); return; }
          if (kind === "audio") finish({ duration: media!.duration });
        };
        if (kind === "video") {
          const video = media as HTMLVideoElement;
          video.muted = true;
          video.playsInline = true;
          video.onloadeddata = () => {
            if (Number.isFinite(video.duration) && video.duration > 0 && video.duration <= maxDuration) capture(video, video.videoWidth, video.videoHeight, video.duration);
            else invalid();
          };
        }
        media.onerror = fail;
        media.src = source;
        media.load();
      }
    });
  } finally {
    disposed = true;
    clearTimeout(timer);
    if (abort) signal?.removeEventListener("abort", abort);
    if (image) { image.onload = null; image.onerror = null; image.src = ""; }
    if (media) {
      media.onloadedmetadata = null; media.onloadeddata = null; media.onerror = null;
      media.pause(); media.removeAttribute("src"); media.load();
    }
    URL.revokeObjectURL(source);
  }
}

/** Returns false when the UI should offer a selectable manual-copy textarea. */
export async function copyText(text: string): Promise<boolean> {
  try { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true; } } catch { /* Try legacy copy. */ }
  const input = document.createElement("textarea");
  const previous = document.activeElement;
  input.value = text;
  input.style.cssText = "position:fixed;left:-9999px;top:0";
  document.body.appendChild(input);
  try { input.select(); return document.execCommand("copy"); }
  catch { return false; }
  finally { input.remove(); if (previous instanceof HTMLElement) previous.focus(); }
}