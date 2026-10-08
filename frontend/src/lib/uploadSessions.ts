import { createAppRequest } from "./appApi";
import { fileIdentity, mediaLimits } from "./mediaInput";
import type { MediaSelection } from "./mediaInput";
import type { PublicLimits } from "../types";
import { MODE_LIMITS, readSentenceInputs, readSpeakers } from "./productionModes";
import type { MatchPreview, QuoteTake, QuoteWord, SentenceInput, TranscriptSegment, UploadSnapshot } from "./productionModes";
import type { DraftAccess } from "./draftTasks";

export const UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024;
export const UPLOAD_POLL_MS = 2000;
export const MATCH_DEBOUNCE_MS = 600;
/** Persist this small binding alongside file metadata; tokens live separately. */
export interface UploadBinding {
  file_id: string;
  upload_id: string;
  sha256: string;
  chunk_size: number;
  put_url: string;
  draftTaskId?: string;
  server_file_id?: string;
}
export interface UploadSession extends UploadBinding { access_token: string; draftTaskToken?: string }
type UploadScope = Partial<DraftAccess> & { server_file_id?: string };
export type ProbedUploadSnapshot = UploadSnapshot & { width?: number; height?: number; fps?: number; metadata_only?: boolean };
export interface UploadCallbacks {
  onSession: (session: UploadSession) => void;
  onSnapshot: (snapshot: UploadSnapshot) => void;
  onHashProgress?: (bytes: number, total: number) => void;
}
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const number = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number => typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
const string = (value: unknown, max: number): value is string => typeof value === "string" && value.length <= max;
export const validUploadId = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
export const validUploadToken = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{32,256}$/.test(value);
const digestPattern = /^[a-f0-9]{64}$/;
const fail = (): never => { throw new Error("上传或原话预览响应格式无效；未确认成功，请核对后重试。"); };
const aborted = (signal: AbortSignal) => { if (signal.aborted) throw new DOMException("操作已取消", "AbortError"); };
const root = (id: string, scope?: UploadScope) => {
  if (!validUploadId(id)) return fail();
  if (!scope?.draftTaskId) {
    if (scope?.draftTaskToken !== undefined || scope?.server_file_id !== undefined) return fail();
    return `/api/uploads/${encodeURIComponent(id)}`;
  }
  if (!validUploadId(scope.draftTaskId) || !validUploadId(scope.server_file_id)) return fail();
  return `/api/tasks/${scope.draftTaskId}/files/${scope.server_file_id}`;
};
function requestFor(signal: AbortSignal, scope?: UploadScope) {
  if (!scope?.draftTaskId) {
    if (scope?.draftTaskToken !== undefined || scope?.server_file_id !== undefined) return fail();
    return createAppRequest({ signal });
  }
  if (!validUploadId(scope.draftTaskId) || !validUploadToken(scope.draftTaskToken)) return fail();
  return createAppRequest({ signal, taskId: scope.draftTaskId, token: scope.draftTaskToken });
}
function headers(id: string, token: string): HeadersInit {
  root(id);
  if (!validUploadToken(token)) return fail();
  return { "X-Upload-Token": token };
}

// SHA-256 compression defined by FIPS 180-4. Only a 64-byte remainder and one
// 64-word schedule are retained; no dependency, full-file buffer or WebCrypto
// digest fallback. Length is counted in bytes and written as 64-bit bit length.
const SHA_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);
const rotate = (value: number, bits: number) => value >>> bits | value << (32 - bits);
export class IncrementalSha256 {
  private state = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  private remainder = new Uint8Array(64);
  private schedule = new Uint32Array(64);
  private used = 0;
  private bytes = 0;
  private finished = false;
  private compress(input: Uint8Array, offset: number) {
    const w = this.schedule;
    for (let i = 0; i < 16; i++) {
      const at = offset + i * 4;
      w[i] = input[at] << 24 | input[at + 1] << 16 | input[at + 2] << 8 | input[at + 3];
    }
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15], b = w[i - 2];
      w[i] = (rotate(a, 7) ^ rotate(a, 18) ^ a >>> 3) + w[i - 16] + (rotate(b, 17) ^ rotate(b, 19) ^ b >>> 10) + w[i - 7];
    }
    const state = this.state;
    let a = state[0], b = state[1], c = state[2], d = state[3], e = state[4], f = state[5], g = state[6], h = state[7];
    for (let i = 0; i < 64; i++) {
      const first = (h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) + ((e & f) ^ (~e & g)) + SHA_K[i] + w[i]) >>> 0;
      const second = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      h = g; g = f; f = e; e = (d + first) >>> 0; d = c; c = b; b = a; a = (first + second) >>> 0;
    }
    state[0] = (state[0] + a) >>> 0; state[1] = (state[1] + b) >>> 0;
    state[2] = (state[2] + c) >>> 0; state[3] = (state[3] + d) >>> 0;
    state[4] = (state[4] + e) >>> 0; state[5] = (state[5] + f) >>> 0;
    state[6] = (state[6] + g) >>> 0; state[7] = (state[7] + h) >>> 0;
  }
  update(input: Uint8Array): this {
    if (this.finished) throw new Error("摘要已经结束。");
    if (!Number.isSafeInteger(this.bytes + input.length) || this.bytes + input.length > Number.MAX_SAFE_INTEGER / 8) throw new Error("文件长度超出摘要上限。");
    this.bytes += input.length;
    let offset = 0;
    if (this.used) {
      const count = Math.min(64 - this.used, input.length);
      this.remainder.set(input.subarray(0, count), this.used); this.used += count; offset = count;
      if (this.used === 64) { this.compress(this.remainder, 0); this.used = 0; }
    }
    while (offset + 64 <= input.length) { this.compress(input, offset); offset += 64; }
    if (offset < input.length) { this.remainder.set(input.subarray(offset), 0); this.used = input.length - offset; }
    return this;
  }
  hex(): string {
    if (!this.finished) {
      const padding = new Uint8Array(this.used < 56 ? 64 : 128);
      padding.set(this.remainder.subarray(0, this.used)); padding[this.used] = 0x80;
      const view = new DataView(padding.buffer), bits = this.bytes * 8;
      view.setUint32(padding.length - 8, Math.floor(bits / 0x100000000)); view.setUint32(padding.length - 4, bits >>> 0);
      for (let i = 0; i < padding.length; i += 64) this.compress(padding, i);
      this.finished = true;
    }
    return Array.from(this.state, word => word.toString(16).padStart(8, "0")).join("");
  }
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  aborted(signal);
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener("abort", abort); reject(new DOMException("操作已取消", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
/** At most 8 MiB read at once, yielding every 256 KiB so typing remains possible.
 * The caller bounds concurrent files. A 500 MiB file is never materialized whole.
 */
export async function hashFileIncrementally(file: Blob, signal: AbortSignal, progress?: (bytes: number, total: number) => void): Promise<string> {
  const digest = new IncrementalSha256();
  aborted(signal);
  for (let offset = 0; offset < file.size; offset += UPLOAD_CHUNK_BYTES) {
    aborted(signal);
    const bytes = new Uint8Array(await file.slice(offset, Math.min(file.size, offset + UPLOAD_CHUNK_BYTES)).arrayBuffer());
    aborted(signal);
    for (let part = 0; part < bytes.length; part += 256 * 1024) {
      digest.update(bytes.subarray(part, part + 256 * 1024));
      progress?.(offset + Math.min(bytes.length, part + 256 * 1024), file.size);
      await wait(0, signal);
    }
  }
  aborted(signal);
  return digest.hex();
}

function putPath(id: string, value: unknown, scope?: UploadScope): string {
  // Never send a capability to an arbitrary URL supplied by a response/cache.
  const base = `${root(id, scope)}/chunks`;
  if (value !== base && value !== `${base}/{index}`) return fail();
  return value;
}
export function readUploadBindings(value: unknown): UploadBinding[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 100) return fail();
  const files = new Set<string>(), uploads = new Set<string>();
  return value.map(item => {
    if (!object(item) || !string(item.file_id, 2048) || !validUploadId(item.upload_id)
      || !string(item.sha256, 64) || !digestPattern.test(item.sha256) || item.chunk_size !== UPLOAD_CHUNK_BYTES
      || files.has(item.file_id) || uploads.has(item.upload_id)) return fail();
    let identity: unknown;
    try { identity = JSON.parse(item.file_id); } catch { return fail(); }
    if (!Array.isArray(identity) || identity.length !== 3 || !string(identity[0], 512) || !identity[0]
      || /[\x00-\x1f/\\]/.test(identity[0]) || !number(identity[1], 1) || !Number.isSafeInteger(identity[1])
      || !number(identity[2]) || !Number.isSafeInteger(identity[2]) || JSON.stringify(identity) !== item.file_id) return fail();
    if (item.draftTaskId !== undefined && !validUploadId(item.draftTaskId)
      || item.server_file_id !== undefined && (!validUploadId(item.server_file_id) || !item.draftTaskId)) return fail();
    const scoped = item.draftTaskId ? { draftTaskId: item.draftTaskId as string, server_file_id: item.server_file_id as string | undefined } : {};
    const put_url = putPath(item.upload_id, item.put_url, scoped);
    files.add(item.file_id); uploads.add(item.upload_id);
    return { file_id: item.file_id, upload_id: item.upload_id, sha256: item.sha256, chunk_size: item.chunk_size, put_url, ...scoped };
  });
}
export function readUploadTokens(value: unknown, ids: readonly string[]): Record<string, string> {
  if (value === undefined && !ids.length) return {};
  if (!object(value) || Object.keys(value).length > 100 || ids.length > 100 || new Set(ids).size !== ids.length) return fail();
  const result: Record<string, string> = Object.create(null);
  for (const id of ids) {
    if (!validUploadId(id) || !Object.prototype.hasOwnProperty.call(value, id) || !validUploadToken(value[id])) return fail();
    result[id] = value[id];
  }
  return result;
}
function previewUrl(value: unknown, id: string, scope?: UploadScope): string | null {
  if (value === null || value === undefined) return null;
  if (!string(value, 2048) || !value.startsWith(root(id, scope) + "/") || /[\\\x00-\x20#]/.test(value)) return fail();
  const parsed = new URL(value, "https://upload-validation.invalid");
  if (parsed.origin !== "https://upload-validation.invalid" || !parsed.pathname.startsWith(root(id, scope) + "/") || parsed.search) return fail();
  return value;
}
/** Only private preview GETs allow a query capability (HTML img has no header
 * facility). Never persist this URL or accept a server-provided query token.
 */
export function uploadMediaUrl(snapshot: UploadSnapshot, token: string, kind: "thumb" | "wave", scope?: UploadScope): string | null {
  const value = kind === "thumb" ? snapshot.thumb_url : snapshot.wave_url;
  if (value === null) return null;
  headers(snapshot.id, token);
  if (value !== `${root(snapshot.id, scope)}/${kind}`) return null;
  if (scope?.draftTaskId && !validUploadToken(scope.draftTaskToken)) return fail();
  return `${value}?${new URLSearchParams({ token: scope?.draftTaskToken ?? token })}`;
}
function words(value: unknown, start: number, end: number): QuoteWord[] {
  if (!Array.isArray(value) || value.length > 20_000) return fail();
  let previous = start;
  return value.map(item => {
    if (!object(item) || !string(item.w, 2000) || !item.w.trim() || !number(item.s, previous, end) || !number(item.e, item.s, end) || item.e <= item.s) return fail();
    previous = item.e;
    return { w: item.w, s: item.s, e: item.e };
  });
}
export function parseUploadSnapshot(value: unknown, expectedId?: string, scope?: UploadScope): ProbedUploadSnapshot {
  if (!object(value)) return fail();
  // UploadStore exposes its durable phase/status plus a transcript envelope;
  // keep a stable UI shape without treating unknown speech or any probe-only
  // media failure as permission to submit. No provider work is triggered here.
  const serverStatus = value.status;
  const status = serverStatus === "processing" ? value.phase === "asr" ? "transcribing"
    : ["queued", "probe", "waveform"].includes(String(value.phase)) ? "probing" : null
    : serverStatus === "asr_failed" || serverStatus === "interrupted" ? "failed" : serverStatus;
  const envelope = object(value.transcript) ? value.transcript : null;
  const segments = envelope ? envelope.segments : value.transcript;
  if (!validUploadId(value.id) || expectedId !== undefined && value.id !== expectedId
    || !string(value.name, 512) || !value.name || /[\x00-\x1f/\\]/.test(value.name)
    || !number(value.bytes, 1) || !Number.isSafeInteger(value.bytes)
    || value.sec !== null && !number(value.sec, 0, 604_800)
    || typeof status !== "string" || !["uploading", "probing", "transcribing", "ready", "failed"].includes(status)
    || value.has_speech !== null && typeof value.has_speech !== "boolean" || value.asr_confidence !== null && !number(value.asr_confidence, 0, 1)
    || !number(value.progress, 0, 100) || !Array.isArray(value.chunks) || value.chunks.length > 10_000
    || !value.chunks.every(item => number(item, 0, Math.ceil(value.bytes as number / UPLOAD_CHUNK_BYTES) - 1) && Number.isSafeInteger(item))
    || new Set(value.chunks).size !== value.chunks.length || !Array.isArray(segments) || segments.length > 10_000) return fail();
  const ids = new Set<string>();
  const duration = value.sec as number | null;
  let transcriptChars = 0, transcriptWords = 0;
  const transcript: TranscriptSegment[] = segments.map(item => {
    if (!object(item) || !string(item.id, 128) || !item.id.trim() || ids.has(item.id)
      || !number(item.start, 0, duration ?? 604_800) || !number(item.end, item.start, duration ?? 604_800) || item.end <= item.start
      || !string(item.text, 16_000) || !string(item.speaker_id, 128)
      || item.confidence != null && !number(item.confidence, 0, 1) || item.snr_db != null && !number(item.snr_db, -1000, 1000)) return fail();
    ids.add(item.id);
    transcriptChars += item.text.length;
    const cues = words(item.words ?? [], item.start, item.end); transcriptWords += cues.length;
    if (transcriptChars > 200_000 || transcriptWords > 30_000) return fail();
    return { id: item.id, start: item.start, end: item.end, text: item.text, speaker_id: item.speaker_id,
      words: cues, confidence: item.confidence ?? null, snr_db: item.snr_db ?? null } as TranscriptSegment;
  });
  if (value.error != null && !string(value.error, 2000) || value.probe_ok !== undefined && typeof value.probe_ok !== "boolean"
    || value.can_materialize !== undefined && typeof value.can_materialize !== "boolean"
    || value.sha256 !== undefined && (!string(value.sha256, 64) || !digestPattern.test(value.sha256))) return fail();
  if ((status === "ready" || value.probe_ok === true) && !number(value.sec, Number.MIN_VALUE, 604_800)) return fail();
  if (value.metadata_only !== undefined && typeof value.metadata_only !== "boolean") return fail();
  for (const key of ["width", "height", "fps"] as const) {
    if (value[key] != null && (!number(value[key], Number.MIN_VALUE) || key !== "fps" && !Number.isSafeInteger(value[key]))) return fail();
  }
  if (scope?.draftTaskId && (value.probe_ok === true || status === "ready")
    && (value.probe_ok !== true || !number(value.width, 1) || !number(value.height, 1) || !number(value.fps, Number.MIN_VALUE))) return fail();
  if (value.metadata_only === true && (status !== "uploading" || value.probe_ok !== true || value.can_materialize !== false)) return fail();
  const receivedBytes = (value.chunks as number[]).reduce((sum, index) => sum + Math.min(UPLOAD_CHUNK_BYTES, (value.bytes as number) - index * UPLOAD_CHUNK_BYTES), 0);
  if (value.received_bytes !== undefined && value.received_bytes !== receivedBytes
    || value.chunk_size !== undefined && value.chunk_size !== UPLOAD_CHUNK_BYTES
    || value.total_chunks !== undefined && value.total_chunks !== Math.ceil(value.bytes / UPLOAD_CHUNK_BYTES)) return fail();
  const failureKind = status !== "failed" ? undefined : serverStatus === "asr_failed" || value.error_code === "ASR_FAILED" ? "asr"
    : serverStatus === "interrupted" ? "interrupted" : "media";
  const people = value.speakers ?? envelope?.speakers;
  return { id: value.id, name: value.name, bytes: value.bytes, sec: value.sec as number | null,
    ...(value.width != null ? { width: value.width as number } : {}),
    ...(value.height != null ? { height: value.height as number } : {}),
    ...(value.fps != null ? { fps: value.fps as number } : {}),
    ...(value.metadata_only !== undefined ? { metadata_only: value.metadata_only as boolean } : {}),
    status: status as UploadSnapshot["status"], has_speech: value.has_speech as boolean | null,
    thumb_url: previewUrl(value.thumb_url, value.id, scope), wave_url: previewUrl(value.wave_url, value.id, scope),
    asr_confidence: value.asr_confidence as number | null, transcript,
    progress: status === "uploading" ? receivedBytes / value.bytes * 100 : value.progress, chunks: [...value.chunks] as number[],
    ...(value.error != null ? { error: value.error as string } : {}),
    ...(value.probe_ok !== undefined ? { probe_ok: value.probe_ok as boolean } : {}),
    ...(failureKind ? { failure_kind: failureKind } : {}),
    ...(value.can_materialize !== undefined ? { can_materialize: value.can_materialize as boolean } : {}),
    ...(value.sha256 !== undefined ? { sha256: value.sha256 as string } : {}),
    ...(people !== undefined ? { speakers: readSpeakers(people) } : {}) };
}
function parseTake(value: unknown, allowed: ReadonlySet<string>): QuoteTake {
  if (!object(value) || !string(value.take_id, 128) || !value.take_id || !validUploadId(value.upload_id) || !allowed.has(value.upload_id)
    || !number(value.start, 0, 604_800) || !number(value.end, value.start, 604_800) || value.end <= value.start
    || !string(value.speaker_id, 128) || !string(value.asr_text, 32_000) || !number(value.score, 0, 1)
    || value.snr_db != null && !number(value.snr_db, -1000, 1000) || value.precision !== "word" && value.precision !== "segment"
    || value.matched_text !== undefined && !string(value.matched_text, 32_000)
    || value.segment_ids !== undefined && (!Array.isArray(value.segment_ids) || value.segment_ids.length > 1000 || !value.segment_ids.every(id => string(id, 128) && !!id))) return fail();
  const cues = words(value.words ?? [], value.start, value.end);
  if (value.precision === "word" && !cues.length) return fail();
  return { take_id: value.take_id, upload_id: value.upload_id, start: value.start, end: value.end,
    speaker_id: value.speaker_id, asr_text: value.asr_text, score: value.score, snr_db: (value.snr_db ?? null) as number | null,
    words: cues, precision: value.precision,
    ...(value.matched_text !== undefined ? { matched_text: value.matched_text as string } : {}),
    ...(value.segment_ids !== undefined ? { segment_ids: [...value.segment_ids] as string[] } : {}) };
}
export function parseMatchPreview(value: unknown, sentences: readonly SentenceInput[], uploadIds: readonly string[]): MatchPreview[] {
  if (!object(value) || !Array.isArray(value.matches) || value.matches.length !== sentences.length) return fail();
  const expected = new Map(sentences.map(sentence => [sentence.idx, sentence.kind])), seen = new Set<number>(), allowed = new Set(uploadIds);
  const matches = value.matches.map(item => {
    if (!object(item) || !number(item.idx) || !Number.isSafeInteger(item.idx) || !expected.has(item.idx) || seen.has(item.idx)
      || item.kind !== expected.get(item.idx) || !number(item.score, 0, 1) || !Array.isArray(item.alt_takes)
      || item.alt_takes.length > MODE_LIMITS.alternative_takes) return fail();
    seen.add(item.idx);
    const source = item.source === null ? null : parseTake(item.source, allowed);
    if (source && (source.score !== item.score || source.score < MODE_LIMITS.match_low) || item.kind === "narration" && source) return fail();
    const result: MatchPreview = { idx: item.idx, kind: item.kind as SentenceInput["kind"], score: item.score, source,
      alt_takes: item.alt_takes.map(take => parseTake(take, allowed)), upload_id: source?.upload_id ?? null,
      start: source?.start ?? null, end: source?.end ?? null, speaker_id: source?.speaker_id ?? null, asr_text: source?.asr_text ?? null };
    for (const key of ["upload_id", "start", "end", "speaker_id", "asr_text"] as const) {
      if (item[key] !== undefined && item[key] !== result[key]) return fail();
    }
    return result;
  });
  const byId = new Map(matches.map(match => [match.idx, match]));
  return sentences.map(sentence => byId.get(sentence.idx)!);
}

export async function createUploadSession(file: File, sha256: string, signal: AbortSignal, scope?: DraftAccess): Promise<UploadSession> {
  aborted(signal);
  if (!digestPattern.test(sha256)) return fail();
  const request = requestFor(signal, scope);
  const value = await request<unknown>(scope ? `/api/tasks/${scope.draftTaskId}/files` : "/api/uploads", { method: "POST",
    body: JSON.stringify(scope ? { name: file.name, size: file.size, sha256, content_type: file.type || "application/octet-stream" }
      : { name: file.name, bytes: file.size, sha256 }) });
  aborted(signal);
  if (!object(value) || value.chunk_size !== UPLOAD_CHUNK_BYTES) return fail();
  const uploadId = scope ? value.upload_id ?? value.up_id : value.upload_id;
  const token = scope ? value.access_token ?? value.token : value.access_token;
  if (!validUploadId(uploadId) || !validUploadToken(token)) return fail();
  // A scoped receipt can expose only the task capability, never the underlying
  // UploadStore secret. Accept old spellings and additive aliases, not conflicts.
  if (scope && (token !== scope.draftTaskToken || value.token !== undefined && value.token !== token
    || value.up_id !== undefined && value.up_id !== uploadId || value.id !== undefined && value.id !== uploadId)) return fail();
  if (scope && !validUploadId(value.file_id)) return fail();
  const binding = scope ? { ...scope, server_file_id: value.file_id as string } : {};
  return { file_id: fileIdentity(file), upload_id: uploadId, access_token: token,
    sha256, chunk_size: value.chunk_size, ...binding,
    put_url: putPath(uploadId, value.put_url, binding) };
}
export async function getUploadStatus(uploadId: string, token: string, signal: AbortSignal, scope?: UploadScope): Promise<UploadSnapshot> {
  const value = await requestFor(signal, scope)<unknown>(root(uploadId, scope), { headers: headers(uploadId, token) });
  aborted(signal);
  return parseUploadSnapshot(value, uploadId, scope);
}
export const transferComplete = (snapshot: UploadSnapshot): boolean => snapshot.chunks.length === Math.ceil(snapshot.bytes / UPLOAD_CHUNK_BYTES)
  && Array.from({ length: Math.ceil(snapshot.bytes / UPLOAD_CHUNK_BYTES) }, (_, index) => index).every(index => snapshot.chunks.includes(index));
export const uploadUsable = (snapshot: UploadSnapshot | undefined): boolean => !!snapshot && transferComplete(snapshot)
  && snapshot.can_materialize !== false && snapshot.probe_ok !== false
  && (snapshot.status === "ready" || snapshot.status === "failed" && snapshot.failure_kind === "asr" && snapshot.probe_ok === true);

/** Only an authorized, identity-checked server response can replace metadata.
 * Preserve authored notes/ranges; do not silently shorten a range or source.
 */
/** The server's measured duration replaces the browser's (Safari reads e.g. 8.0667 where FFmpeg says 8.07).
 * An untouched full-clip trim follows it; a trim the user set is kept as is (and flagged if it no longer fits). */
export function serverTrimEnd(item: Pick<MediaSelection, "duration" | "trim_end">, sec: number): number {
  const untouched = item.trim_end == null || item.duration != null && Math.abs(item.trim_end - item.duration) < 1e-6;
  return untouched ? sec : item.trim_end!;
}

export function reconcileServerProbe(item: MediaSelection, snapshot: ProbedUploadSnapshot, limits: PublicLimits): Partial<MediaSelection> {
  if (snapshot.probe_ok !== true) return { status: snapshot.status === "failed" ? "error" : "loading" };
  const cap = mediaLimits(limits);
  if (!number(snapshot.sec, Number.MIN_VALUE, cap.maxDuration)
    || !number(snapshot.width, 1, 7680)
    || !number(snapshot.height, 1, 4320)
    || !number(snapshot.fps, Number.MIN_VALUE, 120)) return fail();
  return { duration: snapshot.sec, width: snapshot.width, height: snapshot.height,
    trim_end: item.kind === "image" ? null : serverTrimEnd(item, snapshot.sec),
    status: snapshot.status === "ready" && uploadUsable(snapshot) ? "ready" : snapshot.status === "failed" ? "error" : "loading" };
}

export async function probeUpload(session: UploadSession, signal: AbortSignal): Promise<ProbedUploadSnapshot> {
  if (!session.draftTaskId) return fail();
  const value = await requestFor(signal, session)<unknown>(`${root(session.upload_id, session)}/probe`,
    { method: "POST", headers: headers(session.upload_id, session.access_token), body: "{}" });
  aborted(signal);
  return parseUploadSnapshot(value, session.upload_id, session);
}

/** Read-only, bounded cadence. Stops on failure; never restarts ASR or replays POST. */
export async function pollUploadStatus(uploadId: string, token: string, signal: AbortSignal, onSnapshot: (snapshot: UploadSnapshot) => void, scope?: UploadScope): Promise<UploadSnapshot> {
  for (;;) {
    const snapshot = await getUploadStatus(uploadId, token, signal, scope);
    aborted(signal); onSnapshot(snapshot);
    if (["ready", "failed", "uploading"].includes(snapshot.status)) return snapshot;
    await wait(UPLOAD_POLL_MS, signal);
  }
}
/** Explicit action; callers must not automatically retry an ambiguous completion. */
export async function completeUpload(uploadId: string, token: string, signal: AbortSignal, scope?: UploadScope): Promise<UploadSnapshot> {
  await requestFor(signal, scope)<unknown>(`${root(uploadId, scope)}/complete`, { method: "POST", headers: headers(uploadId, token), body: "{}" });
  aborted(signal);
  return getUploadStatus(uploadId, token, signal, scope);
}

export async function deleteUploadFile(session: UploadSession, signal: AbortSignal): Promise<void> {
  await requestFor(signal, session)<unknown>(root(session.upload_id, session), { method: "DELETE", headers: headers(session.upload_id, session.access_token) });
}

/** One transfer at a time in CreateWizard. The receipt is published BEFORE PUT.
 * Retry always GETs chunk acknowledgements and checks the full incremental hash;
 * a ready/processing upload needs no File at all, and no ASR is restarted here.
 */
export async function uploadFile(file: File | undefined, signal: AbortSignal, callbacks: UploadCallbacks,
  resume?: UploadSession, scope?: DraftAccess, metadataOnly = false): Promise<UploadSnapshot> {
  aborted(signal);
  let session = resume, snapshot: UploadSnapshot | undefined;
  if (session) {
    if (scope && (session.draftTaskId !== scope.draftTaskId || session.draftTaskToken !== scope.draftTaskToken)) return fail();
    readUploadBindings([session]); headers(session.upload_id, session.access_token);
    snapshot = await getUploadStatus(session.upload_id, session.access_token, signal, session);
    const identity = JSON.parse(session.file_id) as [string, number, number];
    if (snapshot.name !== identity[0] || snapshot.bytes !== identity[1]) throw new Error("上传状态与原文件身份不一致。");
    if (snapshot.sha256 !== undefined && snapshot.sha256 !== session.sha256) throw new Error("已上传素材的摘要不符，未继续上传。");
    if (snapshot.status !== "uploading" && file) {
      if (fileIdentity(file) !== session.file_id || await hashFileIncrementally(file, signal, callbacks.onHashProgress) !== session.sha256) {
        throw new Error("重新选择的文件内容与已上传素材不一致，未采用这份文件。");
      }
    }
    aborted(signal); callbacks.onSnapshot(snapshot);
    if (snapshot.status !== "uploading" || metadataOnly && (snapshot as ProbedUploadSnapshot).metadata_only === true) return snapshot;
  }
  if (!snapshot || !transferComplete(snapshot)) {
    if (!file) throw new Error("上传尚未完成，请重新选择同一个原文件以继续；已完成的文件无需重选。");
    if (session && fileIdentity(file) !== session.file_id) throw new Error("所选文件不是这个上传会话的原文件。");
    const sha256 = await hashFileIncrementally(file, signal, callbacks.onHashProgress);
    aborted(signal);
    if (session && sha256 !== session.sha256) throw new Error("文件内容已变化，不能续传到原上传会话。请移除后重新选择。");
    if (!session) {
      session = await createUploadSession(file, sha256, signal, scope);
      callbacks.onSession(session);
      aborted(signal);
      snapshot = await getUploadStatus(session.upload_id, session.access_token, signal, session);
      if (snapshot.bytes !== file.size || snapshot.name !== file.name || snapshot.sha256 !== undefined && snapshot.sha256 !== sha256) throw new Error("上传会话与所选文件不一致。");
      callbacks.onSnapshot(snapshot);
      if (snapshot.status !== "uploading") return snapshot;
    }
    const active = session, chunks = new Set(snapshot!.chunks), count = Math.ceil(file.size / UPLOAD_CHUNK_BYTES);
    if (snapshot!.bytes !== file.size || snapshot!.name !== file.name) throw new Error("上传会话与所选文件不一致。");
    for (let index = 0; index < count; index++) {
      if (chunks.has(index)) continue;
      aborted(signal);
      const start = index * UPLOAD_CHUNK_BYTES, end = Math.min(file.size, start + UPLOAD_CHUNK_BYTES);
      const base = putPath(active.upload_id, active.put_url, active);
      const path = base.endsWith("/{index}") ? base.replace("{index}", String(index)) : `${base}/${index}`;
      await requestFor(signal, active)<unknown>(path, { method: "PUT", headers: { ...headers(active.upload_id, active.access_token),
        "Content-Type": "application/octet-stream", "Content-Range": `bytes ${start}-${end - 1}/${file.size}` }, body: file.slice(start, end) });
      aborted(signal);
      // A successful response acknowledges this one chunk, not all file bytes.
      chunks.add(index);
      const loaded = [...chunks].reduce((sum, chunk) => sum + Math.min(UPLOAD_CHUNK_BYTES, file!.size - chunk * UPLOAD_CHUNK_BYTES), 0);
      snapshot = { ...snapshot!, chunks: [...chunks].sort((a, b) => a - b), progress: loaded / file.size * 100 };
      callbacks.onSnapshot(snapshot);
    }
  }
  if (!session) return fail();
  // Reconcile server chunk state before the only completion POST.
  snapshot = await getUploadStatus(session.upload_id, session.access_token, signal, session);
  const [expectedName, expectedBytes] = JSON.parse(session.file_id) as [string, number, number];
  if (snapshot.name !== expectedName || snapshot.bytes !== expectedBytes) throw new Error("服务器上传身份发生变化，未请求完成上传。");
  aborted(signal); callbacks.onSnapshot(snapshot);
  if (snapshot.status !== "uploading") return snapshot;
  if (!transferComplete(snapshot)) throw new Error("服务器还未确认全部分片，请核对状态后继续。");
  snapshot = metadataOnly ? await probeUpload(session, signal) : await completeUpload(session.upload_id, session.access_token, signal, session);
  if (snapshot.name !== expectedName || snapshot.bytes !== expectedBytes) throw new Error("服务器上传身份发生变化，请重新核对状态。");
  aborted(signal); callbacks.onSnapshot(snapshot);
  return snapshot;
}

export async function requestMatchPreview(sentences: readonly SentenceInput[], uploadIds: readonly string[],
  uploadTokens: Readonly<Record<string, string>>, signal: AbortSignal, scope?: DraftAccess,
  editorial?: { mode: "voiceover" | "mixed" | "original"; script: string }): Promise<MatchPreview[]> {
  aborted(signal);
  // Scoped previews always carry the exact local script and selected mode.
  // A-mode quote rows are evidence-only projections; /align must not persist
  // them or turn the draft into C. Legacy preview needs only sentence inputs.
  if (scope && (!editorial || !["voiceover", "mixed", "original"].includes(editorial.mode) || typeof editorial.script !== "string")) return fail();
  const safeSentences = readSentenceInputs(sentences), tokens = readUploadTokens(uploadTokens, uploadIds);
  for (const sentence of safeSentences) if (sentence.source_hint && !uploadIds.includes(sentence.source_hint.upload_id)) {
    throw new Error("原话引用的素材已移除，请重新选择出处。");
  }
  const value = await requestFor(signal, scope)<unknown>(scope ? `/api/tasks/${scope.draftTaskId}/align` : "/api/match/preview", { method: "POST",
    body: JSON.stringify({ ...editorial, sentences: safeSentences, ...(scope ? {} : { upload_ids: uploadIds, upload_tokens: tokens }) }) });
  aborted(signal);
  return parseMatchPreview(value, safeSentences, uploadIds);
}