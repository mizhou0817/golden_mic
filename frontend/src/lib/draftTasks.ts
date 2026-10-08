import { createAppRequest } from "./appApi";
import type { ProductionMode } from "./productionModes";

/** Capabilities are persisted by the parent, never in a URL or a global cache. */
export interface DraftAccess { draftTaskId: string; draftTaskToken: string }
export interface DraftReceipt extends DraftAccess { expires_at: string; status: "draft" }
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
export function readDraftAccess(value: { draftTaskId?: unknown; draftTaskToken?: unknown }): DraftAccess | undefined {
  if (value.draftTaskId === undefined && value.draftTaskToken === undefined) return undefined;
  if (typeof value.draftTaskId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value.draftTaskId)
    || typeof value.draftTaskToken !== "string" || !/^[A-Za-z0-9_-]{32,256}$/.test(value.draftTaskToken)) {
    throw new Error("草稿访问凭据不完整，请重新打开已保存的草稿。");
  }
  return { draftTaskId: value.draftTaskId, draftTaskToken: value.draftTaskToken };
}
export function parseDraftReceipt(value: unknown): DraftReceipt {
  if (!object(value) || value.status !== "draft" || value.id !== value.task_id
    || typeof value.expires_at !== "string" || !Number.isFinite(Date.parse(value.expires_at))) throw new Error("未能确认草稿，请先核对保存状态，不要重复添加。");
  const access = readDraftAccess({ draftTaskId: value.task_id, draftTaskToken: value.access_token });
  if (!access) throw new Error("草稿缺少访问凭据。");
  return { ...access, status: "draft", expires_at: value.expires_at };
}
export async function createDraftTask(mode: ProductionMode, signal: AbortSignal): Promise<DraftReceipt> {
  if (!["voiceover", "mixed", "original"].includes(mode)) throw new Error("请选择制作方式。");
  return parseDraftReceipt(await createAppRequest({ signal })<unknown>("/api/tasks", {
    method: "POST", body: JSON.stringify({ mode }),
  }));
}
/** One promise for all picker/drop actions. An ambiguous POST is never replayed. */
const REFUSED = new Set([409, 429, 503, 507]);
export function draftTaskOnce(existing?: DraftAccess) {
  let pending: Promise<DraftAccess> | undefined = existing ? Promise.resolve(readDraftAccess(existing)!) : undefined;
  // An explicit refusal (busy, limit, draining, no space: nothing was created) is forgotten, so the next
  // upload or retry can try again. An ambiguous failure (lost reply) is never replayed: it may have committed.
  return (mode: ProductionMode, signal: AbortSignal): Promise<DraftAccess> => pending ??= createDraftTask(mode, signal)
    .catch(error => { if (REFUSED.has((error as { status?: unknown } | null)?.status as number)) pending = undefined; throw error; });
}
/** Refresh is read-only: no create, complete, align, start or provider work. */
export async function readDraftTask(access: DraftAccess, signal: AbortSignal): Promise<void> {
  readDraftAccess(access);
  const root = `/api/tasks/${access.draftTaskId}`;
  const request = createAppRequest({ taskId: access.draftTaskId, token: access.draftTaskToken, signal });
  const value = await request<unknown>(root);
  if (signal.aborted) throw new DOMException("操作已取消", "AbortError");
  if (!object(value) || (value.task_id ?? value.id) !== access.draftTaskId || value.status !== "draft") {
    throw new Error("这个草稿已开始制作或不可继续编辑，请到我的作品查看。");
  }
  await request<unknown>(`${root}/files`);
}

export const REMOVE_UNDO_MS = 5000;
/** Cancellation (including unmount) never commits a destructive request. */
export function deferredRemoval(commit: () => void, schedule = setTimeout, cancel = clearTimeout): () => void {
  const timer = schedule(commit, REMOVE_UNDO_MS);
  return () => cancel(timer);
}