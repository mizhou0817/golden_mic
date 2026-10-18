// "AI 自动选效果": one call that returns a complete effects choice for the manuscript.
import { createAppRequest } from "./appApi";
import type { ModePreferences, ProductionMode } from "./productionModes";

export type EffectsChoice = Partial<Pick<ModePreferences, "tone" | "pacing" | "background_music" | "music_mood" | "motion_effects"
  | "transitions" | "news_graphics" | "color_consistency" | "caption_style" | "enhance_speech" | "lower_third" | "jump_cut_cover">>;
export interface EffectsSuggestion { preferences: EffectsChoice; reason: string; source: "ai" | "rules" }

const CHOICES: Record<string, readonly string[]> = {
  tone: ["solemn", "neutral", "energetic"], pacing: ["slow", "normal", "fast"],
  music_mood: ["auto", "solemn", "neutral", "uplifting", "tense"], caption_style: ["news", "big", "none"],
  jump_cut_cover: ["broll", "zoom", "hard"],
};
const SWITCHES = ["background_music", "motion_effects", "transitions", "news_graphics", "color_consistency", "enhance_speech", "lower_third"];

/** Whitelist only: an unknown key or value is dropped, never applied. */
export function parseEffectsSuggestion(value: unknown): EffectsSuggestion {
  const object = (item: unknown): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item);
  if (!object(value) || !object(value.preferences)) throw new Error("AI 没有给出可用的效果，请稍后重试。");
  const preferences: Record<string, unknown> = {};
  for (const [key, allowed] of Object.entries(CHOICES)) {
    const choice = value.preferences[key];
    if (typeof choice === "string" && allowed.includes(choice)) preferences[key] = choice;
  }
  for (const key of SWITCHES) if (typeof value.preferences[key] === "boolean") preferences[key] = value.preferences[key];
  if (!Object.keys(preferences).length) throw new Error("AI 没有给出可用的效果，请稍后重试。");
  const reason = typeof value.reason === "string" && value.reason.length <= 30 ? value.reason : "";
  return { preferences: preferences as EffectsChoice, reason, source: value.source === "ai" ? "ai" : "rules" };
}

export async function requestEffectsSuggestion(mode: ProductionMode, script: string, signal: AbortSignal): Promise<EffectsSuggestion> {
  if (!script.trim()) throw new Error("请先写好稿子，再让 AI 选效果。");
  const value = await createAppRequest({ signal })<unknown>("/api/script/effects", { method: "POST", body: JSON.stringify({ mode, script }), signal });
  return parseEffectsSuggestion(value);
}

const TONE = { solemn: "庄重", neutral: "中性", energetic: "明快" } as const;
const PACE = { slow: "慢一点", normal: "正常语速", fast: "快一点" } as const;
const MOOD = { auto: "自动", solemn: "庄重", neutral: "平和", uplifting: "昂扬", tense: "紧张" } as const;
const CAPTION = { news: "新闻标准字幕", big: "大字字幕", none: "不要字幕" } as const;

/** One short line naming what was picked, e.g. "明快 · 正常语速 · 背景音乐（昂扬）· 新闻标准字幕". */
export function describeEffects(preferences: ModePreferences, mode: ProductionMode): string {
  return [TONE[preferences.tone], mode !== "original" ? PACE[preferences.pacing] : "",
    preferences.background_music ? `背景音乐（${MOOD[preferences.music_mood]}）` : "无背景音乐",
    CAPTION[preferences.caption_style ?? "news"], preferences.motion_effects ? "画面推近" : ""].filter(Boolean).join(" · ");
}
