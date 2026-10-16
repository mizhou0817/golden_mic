// AI help for the manuscript: one text-in/text-out call. It never touches a task or a draft.
import { createAppRequest } from "./appApi";
import type { ProductionMode } from "./productionModes";

export type AssistAction = "write" | "edit";
export type AssistLength = "short" | "normal" | "long";
export interface AssistRequest {
  action: AssistAction; mode: Exclude<ProductionMode, "original">;
  brief?: string; text?: string; instruction?: string; length?: AssistLength;
}
export interface AssistResult { text: string; warnings: string[] }

/** What the writing page hands to the assistant; the page stays the owner of the manuscript. */
export interface ScriptAssistContext {
  mode: ProductionMode; script: string; disabled: boolean; maxScript: number;
  /** Replace the manuscript with this text (goes through the page's normal edit path). */
  onApply: (text: string) => void;
}

export const ASSIST_LIMITS = { brief: 2000, text: 3000, instruction: 300 } as const;
export const ASSIST_LENGTH_LABELS: Record<AssistLength, string> = { short: "短（约 150 字）", normal: "中（约 300 字）", long: "长（约 500 字）" };
export const QUICK_EDITS = [
  { label: "润色，更通顺", instruction: "润色：让语句更通顺、更适合口播，不改变任何事实。" },
  { label: "精简一些", instruction: "精简：删去重复和可有可无的话，保留全部事实，篇幅缩短约三分之一。" },
  { label: "更适合口播", instruction: "改成口播风格：多用短句，一句一个意思，数字写法便于朗读。" },
  { label: "语气更庄重", instruction: "语气更庄重、克制，适合严肃的新闻报道，不夸张。" },
] as const;

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

export function parseAssistResult(value: unknown): AssistResult {
  if (!isObject(value) || typeof value.text !== "string" || !value.text.trim() || value.text.length > ASSIST_LIMITS.text
    || !Array.isArray(value.warnings) || value.warnings.length > 8
    || !value.warnings.every(item => typeof item === "string" && item.length > 0 && item.length <= 400)) {
    throw new Error("AI 返回的内容格式不对，稿子没有被改动。请重试。");
  }
  return { text: value.text, warnings: value.warnings as string[] };
}

export function validateAssistRequest(body: AssistRequest): void {
  if (body.action === "write" && !(body.brief ?? "").trim()) throw new Error("请先写几句要点：时间、地点、人物、发生了什么。");
  if (body.action === "edit" && !(body.text ?? "").trim()) throw new Error("稿子是空的，请先写点内容，或让 AI 写一稿。");
  if ((body.brief ?? "").length > ASSIST_LIMITS.brief) throw new Error(`要点最多 ${ASSIST_LIMITS.brief} 字。`);
  if ((body.text ?? "").length > ASSIST_LIMITS.text) throw new Error(`稿子最多 ${ASSIST_LIMITS.text} 字，请先精简。`);
  if ((body.instruction ?? "").length > ASSIST_LIMITS.instruction) throw new Error(`修改要求最多 ${ASSIST_LIMITS.instruction} 字。`);
}

export async function requestScriptAssist(body: AssistRequest, signal: AbortSignal): Promise<AssistResult> {
  validateAssistRequest(body);
  const value = await createAppRequest({ signal })<unknown>("/api/script/assist", { method: "POST", body: JSON.stringify(body), signal });
  return parseAssistResult(value);
}
