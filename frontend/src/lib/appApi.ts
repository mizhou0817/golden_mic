import type { CreateSubmission } from '../components/CreateWizard';
import type { PublicLimits, TaskStatusResponse } from '../types';
import type { ProductionMode } from './productionModes';

export type AppRequest = <T>(path: string, init?: RequestInit) => Promise<T>;
export type Access = { taskId?: string; token?: string; signal: AbortSignal };
export class AppApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly retryAfterSeconds?: number,
    public readonly code?: 'task_gone' | 'stale_metadata') { super(message); }
}
export const errorText = (error: unknown) => error instanceof Error ? error.message : '请求未完成，请重试。';
export const HISTORY_KEY = 'golden-mic.history.v1';
export interface HistoryWork { taskId: string; accessToken?: string; title: string; createdAt?: string; status: string; revision?: number; mode?: ProductionMode;
  metadata_revision?: number; version_count?: number; expires_at?: string | null }
export interface TaskMetadata { task_id: string; title: string; revision: number; metadata_revision: number }
export type GoneReason = 'expired' | 'inaccessible' | 'network';
export const goneReason = (failure: unknown): GoneReason => failure instanceof AppApiError
  ? failure.status === 410 && failure.code === 'task_gone' ? 'expired'
    : [401, 403, 404, 410].includes(failure.status) ? 'inaccessible' : 'network'
  : 'network';
export interface Receipt { task_id: string; access_token: string; revision?: number; status?: string }
export interface UploadProgress { loaded: number; total: number | null; startedAt: number; finishedAt: number | null }
export interface WorkspaceConfig { local_history: boolean; generative_fill_available: boolean }
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const isProductionMode = (value: unknown): value is ProductionMode => value === 'voiceover' || value === 'mixed' || value === 'original';
export const validTaskId = (value: string) => /^[A-Za-z0-9_-]{1,128}$/.test(value);
const validToken = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{32,256}$/.test(value);
const validStatus = (value: unknown): value is string => typeof value === 'string' && ['draft', 'uploading', 'queued', 'running', 'done', 'failed', 'cancelled', 'gone'].includes(value);
const validRevision = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const validDate = (value: unknown): value is string => typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value));
const validExpiry = (value: unknown) => value === null || validDate(value)
  && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value);
export const validMetadataTitle = (value: unknown): value is string => typeof value === 'string'
  && Array.from(value).length >= 1 && Array.from(value).length <= 40 && !!value.trim()
  && !/[\p{Cc}\p{Cs}\u2028\u2029]/u.test(value);
function metadataFields(value: Record<string, unknown>) {
  if (['metadata_revision', 'version_count'].some(key => value[key] !== undefined && !validRevision(value[key]))
      || value.expires_at !== undefined && !validExpiry(value.expires_at)) throw new Error('作品元数据格式不正确。');
  return { ...(value.metadata_revision === undefined ? {} : { metadata_revision: value.metadata_revision as number }),
    ...(value.version_count === undefined ? {} : { version_count: value.version_count as number }),
    ...(value.expires_at === undefined ? {} : { expires_at: value.expires_at as string | null }) };
}
/** Project, never spread an untrusted receipt or accept coerced CAS counters. */
export function parseTaskMetadata(value: unknown): TaskMetadata {
  if (!object(value) || typeof value.task_id !== 'string' || !validTaskId(value.task_id)
      || !validMetadataTitle(value.title) || !validRevision(value.revision) || !validRevision(value.metadata_revision)) {
    throw new Error('重命名回执未确认，请只读核对作品名称，不要重复提交。');
  }
  return { task_id: value.task_id, title: value.title, revision: value.revision, metadata_revision: value.metadata_revision };
}
export async function renameTaskMetadata(request: AppRequest, baseline: TaskMetadata, title: string): Promise<TaskMetadata> {
  if (!validTaskId(baseline.task_id) || !validRevision(baseline.revision) || !validRevision(baseline.metadata_revision)
      || !validMetadataTitle(title)) throw new Error('作品名称须为 1–40 个字符，不能只有空白或含控制字符。');
  const receipt = parseTaskMetadata(await request<unknown>(`/api/tasks/${encodeURIComponent(baseline.task_id)}/metadata`, {
    method: 'PATCH', body: JSON.stringify({ expected_revision: baseline.revision,
      expected_metadata_revision: baseline.metadata_revision, title }),
  }));
  if (receipt.task_id !== baseline.task_id || receipt.title !== title || receipt.revision !== baseline.revision
      || receipt.metadata_revision !== baseline.metadata_revision + 1) throw new Error('重命名回执与本次操作不一致，请只读核对。');
  return receipt;
}
const MAX_HISTORY_BYTES = 2 * 1024 * 1024;
const MAX_HISTORY_ITEMS = 1000;

function parseHistoryItem(value: unknown, tokenRequired: boolean): HistoryWork {
  if (!object(value) || typeof value.taskId !== 'string' || !validTaskId(value.taskId)
    || typeof value.title !== 'string' || value.title.length > 512
    || (value.status === 'gone' ? value.createdAt !== undefined && !validDate(value.createdAt) : !validDate(value.createdAt))
    || !validStatus(value.status) || (value.status === 'gone' ? value.revision !== undefined && !validRevision(value.revision) : !validRevision(value.revision))
    || (tokenRequired && !validToken(value.accessToken))) throw new Error('作品历史格式不正确，未改动浏览器记录。');
  return { taskId: value.taskId, title: value.title, status: value.status,
    ...(value.createdAt === undefined ? {} : { createdAt: value.createdAt as string }),
    ...(value.revision === undefined ? {} : { revision: value.revision as number }), ...metadataFields(value),
    ...(isProductionMode(value.mode) ? { mode: value.mode } : {}),
    ...(tokenRequired ? { accessToken: value.accessToken as string } : {}) };
}
/** Browser-held task capabilities, not accounts. Old valid receipts remain usable. */
export function readHistory(includeDrafts = false): HistoryWork[] {
  const raw = localStorage.getItem(HISTORY_KEY) ?? '[]';
  if (raw.length > MAX_HISTORY_BYTES) throw new Error('浏览器历史过大，未覆盖原记录。');
  const data: unknown = JSON.parse(raw);
  if (!Array.isArray(data) || data.length > MAX_HISTORY_ITEMS) throw new Error('浏览器历史格式或数量不正确。');
  const rows = new Map<string, HistoryWork>();
  for (const value of data) {
    const row = parseHistoryItem(value, true);
    const previous = rows.get(row.taskId);
    if (previous && previous.accessToken !== row.accessToken) throw new Error('同一作品包含冲突凭据，请保留原记录并核对。');
    if (!previous || (previous.revision ?? -1) < (row.revision ?? -1)
      || previous.revision === row.revision && (previous.metadata_revision ?? -1) < (row.metadata_revision ?? -1)) rows.set(row.taskId, row);
  }
  return [...rows.values()].filter(row => includeDrafts || row.status !== 'draft' && row.status !== 'uploading');
}
function writeHistory(rows: HistoryWork[]): void {
  const encoded = JSON.stringify(rows);
  if (rows.length > MAX_HISTORY_ITEMS || encoded.length > MAX_HISTORY_BYTES) throw new Error('浏览器历史容量不足；不会自动丢弃旧作品凭据。');
  localStorage.setItem(HISTORY_KEY, encoded);
  if (localStorage.getItem(HISTORY_KEY) !== encoded) throw new Error('浏览器尚未确认保存作品记录。');
}
/** Callers keep the receipt in memory FIRST; storage failure must not replay a POST. */
export function rememberWork(item: HistoryWork): void {
  const safe = parseHistoryItem(item, true);
  // Hide drafts in the work list without evicting their stored capabilities.
  const current = readHistory(true);
  writeHistory([safe, ...current.filter(row => row.taskId !== safe.taskId)]);
}
export function forgetHistory(taskId: string): void {
  if (!validTaskId(taskId)) throw new Error('作品编号不正确。');
  writeHistory(readHistory(true).filter(row => row.taskId !== taskId));
}

export function sameOriginPath(path: string): string {
  const url = new URL(path, location.origin);
  if (!path.startsWith('/') || path.startsWith('//') || /[\\\x00-\x20]/.test(path) || url.hash || url.username || url.password || url.origin !== location.origin
    || !(url.pathname.startsWith('/api/') || url.pathname === '/health/ready')) throw new Error('只允许本站接口。');
  return url.pathname + url.search;
}
export function assertSameOriginConfiguration(): void {
  const configured = new URL(import.meta.env.VITE_API_BASE_URL || '/', location.origin);
  if (configured.origin !== location.origin || configured.pathname !== '/' || configured.username || configured.password || configured.search || configured.hash) {
    throw new Error('工作区需要同源 /api 代理；请移除跨域或子路径 VITE_API_BASE_URL 配置。');
  }
}
const KNOWN_LIMIT_MESSAGES = new Set([
  '你保存的素材已达上限（最多 100 个文件，合计不超过 20 GiB）。', '新建上传过于频繁，请稍后重试。', '全站上传名额已满，请稍后重试。',
  'AI 写作用得太频繁了，请稍后再试，或先自己动手改一改。', 'AI 正在为别人写稿，请几秒后再试。',
]);
async function decode<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown;
  try { payload = text ? JSON.parse(text) : undefined; } catch { /* Do not display proxy HTML. */ }
  if (!response.ok) {
    const detail = object(payload) ? payload.detail : undefined;
    const fallback: Record<number, string> = { 401: '作品访问凭据无效。', 403: '安全凭据无效或请求来源不受信任，请刷新页面后核对状态。',
      404: '作品或接口不存在，或没有访问权限。', 410: '作品暂不可访问，请核对访问凭据。', 409: '当前版本或任务状态不允许此操作，请刷新。', 429: '操作过于频繁，请稍后再试。' };
    const code = object(detail) && (detail.code === 'task_gone' || detail.code === 'stale_metadata') ? detail.code : undefined;
    // Machine codes the server returns for refused recovery/retry: say what it means, never just "refresh".
    const coded: Record<string, string> = {
      recovery_source_unverified: '这个作品没有保存完整、可核验的原始稿件和素材记录，无法恢复为草稿。请用原稿和素材新建作品。',
      recovery_legacy_unsupported: '这是旧版本制作的作品，不支持恢复为草稿。请用原稿和素材新建作品。',
      recovery_requires_terminal_task: '作品还在制作中，等它结束（成功或失败）后才能回去修改。',
      recovery_requires_accepted_task: '这个作品还没有被服务器正式接收，不能恢复。请用原稿和素材新建作品。',
      retry_same_unavailable: '当前不能从失败处继续制作。可以回去修改稿子或素材后重新提交。',
      retry_inputs_changed: '失败之后程序或素材记录发生了变化，这个作品不能直接续作。请点“回去删减稿子”或“回去多传素材”恢复为草稿后重新提交。',
      retry_cache_missing: '已完成步骤的缓存不完整，不能安全续作。请点“回去删减稿子”或“回去多传素材”恢复为草稿后重新提交。',
      retry_cache_invalid: '已完成步骤的缓存校验没通过，不能安全续作。请点“回去删减稿子”或“回去多传素材”恢复为草稿后重新提交。',
      retry_provider_uncertain: '上次有一步是否已向 AI 服务提交无法确认，为避免重复收费，不会自动续作。请点“回去删减稿子”或“回去多传素材”恢复为草稿后重新提交。',
      shot_reuse_not_applicable: '这次失败不是“镜头不足”，或当前制作方式不支持重复使用画面。',
    };
    // recovery_quota is a fixed, non-sensitive sentence from the server; unlike other 429s it is not a rate limit.
    const quotaMessage = object(detail) && (detail.code === 'recovery_quota' || detail.code === 'recovery_draft_limit' || detail.code === 'start_budget') && typeof detail.message === 'string' && detail.message.length <= 300 ? detail.message : undefined;
    // The server's own fixed upload-limit sentences (never free text): shown so people know what to free up.
    const limitMessage = response.status === 429 && typeof detail === 'string' && KNOWN_LIMIT_MESSAGES.has(detail) ? detail : undefined;
    const codedMessage = quotaMessage ?? limitMessage ?? (object(detail) && typeof detail.code === 'string' ? coded[detail.code] : undefined);
    const retryAfter = parseRetryAfter(response.headers.get('Retry-After'));
    // Never reflect private quota diagnostics or automatically replay a write.
    const message = response.status === 429 && !quotaMessage && !limitMessage ? `${fallback[429]}${retryAfter === undefined ? '' : ` 请在 ${retryAfter} 秒后手动重试。`}`
      : codedMessage ? codedMessage : typeof detail === 'string' ? detail : object(detail) && typeof detail.message === 'string' ? detail.message
        : fallback[response.status] ?? `请求失败（${response.status}）。`;
    throw new AppApiError(response.status === 410 && code === 'task_gone' ? '作品已到期或已清理。' : message, response.status, retryAfter, code);
  }
  if (text && payload === undefined) throw new AppApiError('接口返回了无法解析的数据。', 502);
  return payload as T;
}
export function parseRetryAfter(value: string | null, now = Date.now()): number | undefined {
  if (!value || value.length > 128) return undefined;
  const text = value.trim();
  const seconds = /^\d+$/.test(text) ? Number(text)
    : /^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(text)
      ? Math.ceil((Date.parse(text) - now) / 1000) : NaN;
  return Number.isSafeInteger(seconds) && seconds >= 0 && seconds <= 604800 ? seconds : undefined;
}
export async function publicRequest<T>(path: string, signal?: AbortSignal): Promise<T> {
  assertSameOriginConfiguration();
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, 30_000);
  try { return await decode<T>(await fetch(sameOriginPath(path), { credentials: 'include', cache: 'no-store', redirect: 'error', signal: controller.signal })); }
  finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

export async function getWorkspaceConfig(signal?: AbortSignal): Promise<WorkspaceConfig> {
  const value = await publicRequest<unknown>('/api/config/workspace', signal);
  if (!object(value) || typeof value.local_history !== 'boolean' || typeof value.generative_fill_available !== 'boolean') {
    throw new Error('工作区配置格式不正确。');
  }
  return { local_history: value.local_history, generative_fill_available: value.generative_fill_available };
}

/** The server exposes this index ONLY to direct local development connections. */
export async function getLocalHistory(signal?: AbortSignal): Promise<HistoryWork[]> {
  const items = new Map<string, HistoryWork>();
  let offset = 0;
  for (let page = 0; page < 100; page++) {
    const value = await publicRequest<unknown>(`/api/tasks?offset=${offset}&limit=200`, signal);
    if (!object(value) || !Array.isArray(value.tasks) || value.tasks.length > 200
      || typeof value.total !== 'number' || !Number.isSafeInteger(value.total) || value.total < 0) throw new Error('本机作品列表格式不正确。');
    for (const raw of value.tasks) {
      if (!object(raw)) throw new Error('本机作品记录格式不正确。');
      const item = parseHistoryItem({ taskId: raw.task_id, title: raw.title, createdAt: raw.created_at, status: raw.status,
        revision: raw.revision, mode: raw.mode, ...metadataFields(raw) }, false);
      if (item.status !== 'draft' && item.status !== 'uploading') items.set(item.taskId, item);
    }
    offset += value.tasks.length;
    if (offset >= value.total) return [...items.values()];
    if (!value.tasks.length) throw new Error('本机列表在读取期间发生变化，请刷新后重试。');
  }
  throw new Error('本机作品列表超过安全读取上限，请清理或分批归档。');
}

/** The same-origin server remains the authority for upload limits, not a UI constant. */
export async function getPublicLimits(signal?: AbortSignal): Promise<PublicLimits> {
  const value = await publicRequest<unknown>('/api/config/limits', signal);
  const positive = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v > 0;
  if (!object(value) || !['max_files', 'max_upload_bytes', 'max_total_upload_bytes', 'max_script_length'].every(key => positive(value[key]))
    || !['max_video_duration_seconds', 'max_total_video_duration_seconds'].every(key => value[key] === undefined
      || typeof value[key] === 'number' && Number.isFinite(value[key]) && value[key] > 0)
    || (value.allowed_extensions !== undefined && (!Array.isArray(value.allowed_extensions)
      || !value.allowed_extensions.length || !value.allowed_extensions.every(extension => typeof extension === 'string' && /^\.[a-z0-9]+$/i.test(extension))))) {
    throw new Error('服务器返回了无效的上传限制，请重新连接。');
  }
  // Optional v2 defaults remain server facts: validate supplied values, never
  // manufacture omitted quota/capability permissions from UI constants.
  const positiveFields = ['quote_max_sec', 'quote_warn_sec', 'quote_min_sec', 'max_visual_sec'];
  const integerFields = ['script_soft_max', 'chunk_size', 'max_shots', 'retention_hours', 'draft_ttl_hours', 'rules_version',
    'browser_submissions_per_hour', 'ip_submissions_per_hour', 'global_submissions_per_hour', 'max_concurrent_tasks', 'max_pending_tasks'];
  if (positiveFields.some(key => value[key] !== undefined && !(typeof value[key] === 'number' && Number.isFinite(value[key]) && value[key] > 0))
    || integerFields.some(key => value[key] !== undefined && !positive(value[key]))
    || ['match_ok', 'match_low', 'rate_tolerance'].some(key => value[key] !== undefined && !(typeof value[key] === 'number' && Number.isFinite(value[key]) && value[key] >= 0 && value[key] <= 1))
    || ['maintenance', 'retry_same_supported'].some(key => value[key] !== undefined && typeof value[key] !== 'boolean')
    || value.quality_gate_mode !== undefined && value.quality_gate_mode !== 'warn' && value.quality_gate_mode !== 'block'
    || value.pacing_cpm !== undefined && (!object(value.pacing_cpm) || !['slow', 'normal', 'fast'].every(key => positive((value.pacing_cpm as Record<string, unknown>)[key])))
    || typeof value.match_low === 'number' && typeof value.match_ok === 'number' && value.match_low > value.match_ok
    || typeof value.quote_min_sec === 'number' && typeof value.quote_max_sec === 'number' && value.quote_min_sec > value.quote_max_sec) {
    throw new Error('服务器返回了无效的制作限制，请重新连接。');
  }
  return value as unknown as PublicLimits;
}

let browserSession: Promise<{ csrf_token: string | null }> | null = null;
let sessionExpires = 0;
let sessionEpoch = 0;
export function clearBrowserSession() { sessionEpoch++; browserSession = null; sessionExpires = 0; }
async function browserHeaders(): Promise<Headers> {
  assertSameOriginConfiguration();
  if (!browserSession || Date.now() >= sessionExpires - 60_000) {
    const epoch = ++sessionEpoch;
    // Mark the in-flight read as reusable. A second caller must not replace its
    // promise just because its expiry has not been received yet.
    sessionExpires = Number.POSITIVE_INFINITY;
    browserSession = (async () => {
      // Anonymous CSRF protection needs no login and never supplies task access.
      const value = await publicRequest<unknown>('/api/session');
      if (!object(value) || typeof value.access_mode !== 'string' || !['development', 'anonymous'].includes(value.access_mode)) throw new Error('浏览器安全会话格式不正确。');
      if (value.access_mode === 'anonymous' ? !validToken(value.csrf_token) || !validDate(value.expires_at) || Date.parse(value.expires_at) <= Date.now()
        : value.csrf_token !== null || value.expires_at !== null) throw new Error('浏览器安全会话已失效或格式不正确。');
      if (epoch !== sessionEpoch) throw new DOMException('安全会话已更新', 'AbortError');
      sessionExpires = typeof value.expires_at === 'string' ? Date.parse(value.expires_at) : Number.POSITIVE_INFINITY;
      return { csrf_token: value.csrf_token as string | null };
    })().catch(error => { if (epoch === sessionEpoch) { browserSession = null; sessionExpires = 0; } throw error; });
  }
  const session = await browserSession;
  return new Headers(session.csrf_token ? { 'X-CSRF-Token': session.csrf_token } : {});
}

/** A caller's cancellation must not cancel another caller's CSRF bootstrap. */
function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) {
    // Observe the shared read even if this caller was cancelled before attaching.
    void promise.catch(() => undefined);
    return Promise.reject(new DOMException('已取消', 'AbortError'));
  }
  return new Promise<T>((resolve, reject) => {
    const abort = () => { signal.removeEventListener('abort', abort); reject(new DOMException('已取消', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
    promise.then(value => { signal.removeEventListener('abort', abort); resolve(value); },
      error => { signal.removeEventListener('abort', abort); reject(error); });
  });
}

export function createAppRequest(access: Access): AppRequest {
  return async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    assertSameOriginConfiguration();
    const url = sameOriginPath(path);
    const controller = new AbortController();
    const abort = () => controller.abort();
    const signals = [access.signal, init.signal].filter((s): s is AbortSignal => !!s);
    signals.forEach(s => { if (s.aborted) abort(); s.addEventListener('abort', abort, { once: true }); });
    // AI writing is one slow model call; everything else keeps the short JSON timeout.
    const timer = setTimeout(abort, init.body instanceof FormData ? 30 * 60_000 : init.body instanceof Blob ? 120_000 : url === '/api/script/assist' ? 170_000 : 30_000);
    try {
      if (controller.signal.aborted) throw new DOMException('已取消', 'AbortError');
      const uploadScope = access.taskId === undefined && access.token === undefined && (
        url === '/api/tasks' && init.method?.toUpperCase() === 'POST' ||
        url === '/api/uploads' || /^\/api\/uploads\/up_[0-9a-f]{32}(?:\/chunks\/(?:0|[1-9]\d*)|\/complete)?$/.test(url)
        || url === '/api/match/preview' || url === '/api/script/assist');
      if (!uploadScope && (!access.taskId || !validTaskId(access.taskId) || access.token !== undefined && !validToken(access.token)
        || !url.startsWith(`/api/tasks/${encodeURIComponent(access.taskId)}`)
        || !['', '/', '?'].includes(url.charAt(`/api/tasks/${encodeURIComponent(access.taskId)}`.length)))) throw new Error('只能访问当前选中的作品。');
      // All task credentials come from the selected receipt, never an arbitrary
      // caller query/header. A token-free URL is authorized only by the server.
      if (new URL(url, location.origin).searchParams.has('token')) throw new Error('请求地址不能携带额外的作品凭据。');
      const headers = new Headers(init.headers);
      headers.delete('Authorization'); headers.delete('X-Classroom-CSRF'); headers.delete('X-CSRF-Token'); headers.delete('X-Task-Token');
      if (!uploadScope && !/^\/api\/tasks\/[A-Za-z0-9_-]+\/files(?:\/|$)/.test(url)) headers.delete('X-Upload-Token');
      if (access.token !== undefined) headers.set('X-Task-Token', access.token);
      headers.set('Accept', 'application/json');
      if (!['GET', 'HEAD', 'OPTIONS'].includes((init.method ?? 'GET').toUpperCase())) {
        (await abortable(browserHeaders(), controller.signal)).forEach((value, key) => headers.set(key, value));
      }
      if (init.body instanceof FormData) headers.delete('Content-Type');
      else if (typeof init.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
      if (controller.signal.aborted) throw new DOMException('已取消', 'AbortError');
      // Mutations are never replayed: an interrupted response may already have committed.
      const response = await fetch(url, { ...init, headers, mode: 'same-origin', credentials: 'include', redirect: 'error', cache: 'no-store', signal: controller.signal });
      if (response.status === 403 && response.headers.get('X-Anonymous-Session') === 'required') clearBrowserSession();
      return await decode<T>(response);
    } finally { clearTimeout(timer); signals.forEach(s => s.removeEventListener('abort', abort)); }
  };
}
export function parseTask(value: unknown): TaskStatusResponse {
  if (!object(value) || typeof value.task_id !== 'string' || !validTaskId(value.task_id)
    || value.title !== undefined && (typeof value.title !== 'string' || value.title.length > 512 || /[\p{Cc}\p{Cs}\u2028\u2029]/u.test(value.title))
    || !validStatus(value.status) || value.mode !== undefined && !isProductionMode(value.mode)
    // Legacy tasks may omit these fields. Supplied metadata must be typed;
    // never infer quote failures from prose or silently discard invalid rows.
    || value.errorKind !== undefined && value.errorKind !== null && (typeof value.errorKind !== 'string'
      || !['network', 'transient', 'shortage', 'qc', 'quote_missing'].includes(value.errorKind))
    || value.badRows !== undefined && (!Array.isArray(value.badRows) || value.badRows.length > 2048
      || !value.badRows.every(row => typeof row === 'number' && Number.isSafeInteger(row) && row > 0 && row <= 100_000))
    || typeof value.progress !== 'number' || value.progress < 0 || value.progress > 100 || !Number.isFinite(value.progress)
    || !validRevision(value.revision) || !Array.isArray(value.stages)
    || !value.stages.every(s => object(s) && Number.isInteger(s.number) && typeof s.name === 'string'
      && typeof s.status === 'string' && ['pending', 'running', 'done', 'failed'].includes(s.status))) throw new Error('任务状态响应格式不正确。');
  metadataFields(value);
  return value as unknown as TaskStatusResponse;
}
export function parseReceipt(value: unknown): Receipt {
  if (!object(value) || typeof value.task_id !== 'string' || !validTaskId(value.task_id)
    || !validToken(value.access_token) || value.revision !== undefined && !validRevision(value.revision)
    || value.status !== undefined && !validStatus(value.status)) throw new Error('任务回执格式不正确，请先刷新作品列表，勿重复上传。');
  return { task_id: value.task_id, access_token: value.access_token,
    ...(value.revision !== undefined ? { revision: value.revision as number } : {}),
    ...(value.status !== undefined ? { status: value.status as string } : {}) };
}
export function taskMedia(taskId: string, token?: string, revision = 0, download = false): string {
  if (!validTaskId(taskId) || !validRevision(revision) || token !== undefined && !validToken(token)) throw new Error('成片访问参数不正确。');
  const query = new URLSearchParams({ revision: String(revision) });
  if (token !== undefined) query.set('token', token);
  if (download) query.set('download', 'true');
  return `/api/tasks/${encodeURIComponent(taskId)}/video?${query}`;
}

/** Bytes are aggregate multipart transfer bytes, not invented per-file progress. */
export async function uploadTask(value: CreateSubmission, signal: AbortSignal,
  progress: (value: UploadProgress) => void): Promise<Receipt> {
  assertSameOriginConfiguration();
  if (signal.aborted) throw new DOMException('上传已取消', 'AbortError');
  if (value.source_voice_preferred !== undefined && typeof value.source_voice_preferred !== 'boolean') {
    throw new Error('原声偏好必须为布尔值，请重新打开草稿。');
  }
  const { draftTaskId, draftTaskToken } = value;
  if (draftTaskId !== undefined || draftTaskToken !== undefined) {
    if (!draftTaskId || !validTaskId(draftTaskId) || !validToken(draftTaskToken)) throw new Error('草稿访问凭据不完整，请重新打开草稿。');
    if (!value.uploadIds?.length || value.uploadIds.length !== value.asset_options.length) throw new Error('请等待全部素材上传完成后再制作。');
    if (value.mode === 'original' && (value.ownVoice || (value.preferences as { voice?: string }).voice === 'mine' || value.preferences.generative_fill)) {
      throw new Error('只用原声不能使用整篇配音或生成画面。');
    }
    const startedAt = Date.now();
    if (value.ownVoice) throw new Error('新草稿请先制作，再逐句录音；整篇录音只用于旧草稿恢复。');
    const payload = { mode: value.mode ?? 'voiceover', script: value.script,
      sentences: value.sentences ?? [], type_marks: value.sentenceKinds ?? {},
      ...(value.source_voice_preferred === undefined ? {} : { source_voice_preferred: value.source_voice_preferred }),
      speakers: value.speakers ?? [], preferences: value.preferences, asset_options: value.asset_options };
    const body = JSON.stringify(payload);
    progress({ loaded: 0, total: null, startedAt, finishedAt: null });
    const raw = await createAppRequest({ taskId: draftTaskId, token: draftTaskToken, signal })<unknown>(`/api/tasks/${draftTaskId}/start`, { method: 'POST', body });
    if (!object(raw) || (raw.id ?? raw.task_id) !== draftTaskId || raw.task_id !== draftTaskId
      || !['queued', 'running'].includes(String(raw.status))) throw new Error('未确认制作回执，请先到我的作品核对，不要重复提交。');
    // /start does not mint a new capability. Preserve older servers' omitted echo.
    if (raw.access_token !== undefined && raw.access_token !== draftTaskToken) throw new Error('制作回执凭据不一致，请核对草稿状态，不要重复提交。');
    const receipt = parseReceipt({ ...raw, access_token: raw.access_token ?? draftTaskToken });
    progress({ loaded: 0, total: null, startedAt, finishedAt: Date.now() });
    return receipt;
  }
  // Only an explicitly restored pre-v2 draft may use the old creation route.
  if (value.legacyRestore !== true) throw new Error('请先添加素材并保存草稿，再开始制作。');
  const headers = await abortable(browserHeaders(), signal);
  if (signal.aborted) throw new DOMException('上传已取消', 'AbortError');
  if (value.uploadIds?.length && !value.ownVoice) {
    const startedAt = Date.now();
    const body = JSON.stringify({ mode: value.mode ?? 'voiceover', script: value.script,
      sentences: value.sentences ?? [], upload_ids: value.uploadIds, upload_tokens: value.uploadTokens ?? {},
      speakers: value.speakers ?? [], preferences: value.preferences, asset_options: value.asset_options });
    headers.set('Content-Type', 'application/json'); headers.set('Accept', 'application/json');
    progress({ loaded: 0, total: null, startedAt, finishedAt: null });
    const response = await fetch('/api/tasks', { method: 'POST', headers, body, signal,
      mode: 'same-origin', credentials: 'include', redirect: 'error', cache: 'no-store' });
    if (response.status === 403 && response.headers.get('X-Anonymous-Session') === 'required') clearBrowserSession();
    const receipt = parseReceipt(await decode<unknown>(response));
    progress({ loaded: 0, total: null, startedAt, finishedAt: Date.now() });
    return receipt;
  }
  const form = new FormData();
  form.append('script', value.script);
  form.append('script_format', 'headline_first');
  form.append('preferences', JSON.stringify(value.preferences));
  form.append('asset_options', JSON.stringify(value.asset_options));
  if (value.uploadIds?.length) {
    form.append('mode', value.mode ?? 'voiceover');
    form.append('upload_ids', JSON.stringify(value.uploadIds));
    form.append('upload_tokens', JSON.stringify(value.uploadTokens ?? {}));
    form.append('sentences', JSON.stringify(value.sentences ?? []));
    form.append('speakers', JSON.stringify(value.speakers ?? []));
  } else value.files.forEach(file => form.append('files', file));
  if (value.ownVoice) form.append('own_voice', value.ownVoice);
  return await new Promise<Receipt>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const startedAt = Date.now();
    let last: UploadProgress = { loaded: 0, total: null, startedAt, finishedAt: null };
    const abort = () => xhr.abort();
    const finish = () => { signal.removeEventListener('abort', abort); xhr.onload = xhr.onerror = xhr.ontimeout = xhr.onabort = null; xhr.upload.onprogress = xhr.upload.onload = null; };
    xhr.open('POST', '/api/tasks'); xhr.withCredentials = true; xhr.timeout = 30 * 60_000;
    headers.forEach((header, key) => xhr.setRequestHeader(key, header));
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.upload.onprogress = event => { last = { ...last, loaded: event.loaded, total: event.lengthComputable ? event.total : null }; progress(last); };
    xhr.upload.onload = () => { last = { ...last, finishedAt: Date.now() }; progress(last); };
    xhr.onload = () => {
      if (xhr.status === 403 && xhr.getResponseHeader('X-Anonymous-Session') === 'required') clearBrowserSession();
      if (xhr.responseURL && new URL(xhr.responseURL, location.origin).href !== new URL('/api/tasks', location.origin).href) {
        finish(); reject(new Error('上传发生了意外跳转，请先核对作品历史，不会重复提交。')); return;
      }
      const response = new Response(xhr.responseText || null, { status: xhr.status || 502,
        headers: { 'Retry-After': xhr.getResponseHeader('Retry-After') ?? '' } });
      finish(); void decode<unknown>(response).then(v => resolve(parseReceipt(v))).catch(reject);
    };
    xhr.onerror = () => { finish(); reject(new Error('上传连接中断；服务器可能已接收，请先刷新作品列表再决定是否重试。')); };
    xhr.ontimeout = () => { finish(); reject(new Error('上传超时；请先刷新作品列表，避免重复提交。')); };
    xhr.onabort = () => { finish(); reject(new DOMException('上传已取消；如数据已传完，请刷新作品列表确认。', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) { finish(); reject(new DOMException('上传已取消', 'AbortError')); return; }
    progress(last); xhr.send(form);
  });
}