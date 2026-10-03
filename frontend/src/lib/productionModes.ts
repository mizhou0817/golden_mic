import rules from "../../../backend/mode_rules.json";
import type { EditingPreferences } from "../types";
import type { MediaMetadata } from "./mediaInput";

export type ProductionMode = "voiceover" | "mixed" | "original";
export type SentenceKind = "narration" | "quote";
export type Kind = SentenceKind;
export type TerminalPunctuation = "" | "，" | "," | "。" | "." | ";" | "；" | "!" | "?" | "！" | "？";
export interface SentenceInput {
  idx: number;
  text: string;
  kind: SentenceKind;
  speaker_hint?: string;
  source_hint?: { upload_id: string; seg_id: string } | null;
  /** Actual source ending; absent legacy metadata is unknown, never guessed. */
  terminal_punctuation?: TerminalPunctuation;
}
export interface Speaker {
  id: string;
  name: string;
  title: string;
  auto_label: string;
  appearances: number;
  seconds: number;
}
/** Absolute ORIGINAL-upload seconds. Never synthesize word clocks from text. */
export interface QuoteWord { w: string; s: number; e: number }
export interface TranscriptSegment {
  id: string;
  start: number;
  end: number;
  speaker_id: string;
  text: string;
  words: QuoteWord[];
  confidence?: number | null;
  snr_db?: number | null;
}
export interface QuoteTake {
  take_id: string;
  upload_id: string;
  start: number;
  end: number;
  speaker_id: string;
  asr_text: string;
  score: number;
  snr_db: number | null;
  words: QuoteWord[];
  precision: "word" | "segment";
  matched_text?: string;
  segment_ids?: string[];
}
export interface UploadSnapshot {
  id: string;
  name: string;
  bytes: number;
  sec: number | null;
  status: "uploading" | "probing" | "transcribing" | "ready" | "failed";
  /** null means not established; do not relabel unknown audio as silence. */
  has_speech: boolean | null;
  thumb_url: string | null;
  wave_url: string | null;
  asr_confidence: number | null;
  transcript: TranscriptSegment[];
  /** Server progress, 0–100; during transfer, acknowledged bytes only. */
  progress: number;
  /** Zero-based, server-acknowledged chunk indices, not a guessed count. */
  chunks: number[];
  error?: string;
  /** Explicit server evidence allows ASR-only failure to remain usable B-roll. */
  probe_ok?: boolean;
  failure_kind?: "asr" | "media" | "interrupted";
  can_materialize?: boolean;
  sha256?: string;
  speakers?: Speaker[];
}
export interface MatchPreview {
  idx: number;
  kind: SentenceKind;
  score: number;
  source: QuoteTake | null;
  alt_takes: QuoteTake[];
  upload_id: string | null;
  start: number | null;
  end: number | null;
  speaker_id: string | null;
  asr_text: string | null;
}
/** Structural alias remains compatible while the shared preferences grow. */
export type ModePreferences = Omit<EditingPreferences, "quote_caption"> & {
  voice?: "ai" | "mine";
  lower_third?: boolean;
  jump_cut_cover?: "broll" | "zoom" | "hard";
  quote_caption?: "spoken" | "asr" | "none";
};

export const PRODUCTION_MODES = ["voiceover", "mixed", "original"] as const;
export const MODE_RULES = rules;
export const RULES_VERSION = rules.rules_version;
export const MODE_LIMITS = rules.limits;
export const MATCH_OK = rules.limits.match_ok;
export const MATCH_LOW = rules.limits.match_low;
export const PACING_CPM = rules.pacing_cpm;
export const TITLE_PLACEHOLDER = "（写一个标题）";
export const LEGACY_CORE_KEY = "gm-core-v1";
export const MODE_LABELS: Readonly<Record<ProductionMode, string>> = Object.freeze({
  voiceover: rules.modes.voiceover.name, mixed: rules.modes.mixed.name, original: rules.modes.original.name,
});
export const MODE_CARDS = PRODUCTION_MODES.map(mode => Object.freeze({
  mode, name: MODE_LABELS[mode], description: rules.modes[mode].description,
  icon: ({ voiceover: "♪", mixed: "♪＋话", original: "话" } as const)[mode],
  fit: ({ voiceover: "消息、活动报道、无人说话的素材", mixed: "带采访的新闻、有受访者发言", original: "人物专访、演讲、街访集锦" } as const)[mode],
}));
export interface ModeStage { n: number; name: string; description: string }
export const STAGE_DEFINITIONS: Readonly<Record<ProductionMode, readonly ModeStage[]>> = Object.freeze({
  voiceover: rules.modes.voiceover.stages, mixed: rules.modes.mixed.stages, original: rules.modes.original.stages,
});
export const STAGE_LABELS: Readonly<Record<ProductionMode, readonly string[]>> = Object.freeze({
  voiceover: STAGE_DEFINITIONS.voiceover.map(stage => stage.name),
  mixed: STAGE_DEFINITIONS.mixed.map(stage => stage.name),
  original: STAGE_DEFINITIONS.original.map(stage => stage.name),
});
const weights = (mode: ProductionMode) => {
  const raw = rules.stage_weights_raw[mode], total = raw.reduce((sum, value) => sum + value, 0);
  return Object.freeze(raw.map(value => value / total));
};
/** Relative work weights, NOT a duration/ETA prediction. */
export const STAGE_WEIGHTS = Object.freeze({ voiceover: weights("voiceover"), mixed: weights("mixed"), original: weights("original") });
export const isProductionMode = (value: unknown): value is ProductionMode => PRODUCTION_MODES.some(mode => value === mode);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const text = (value: unknown, maximum: number): value is string => typeof value === "string" && Array.from(value).length <= maximum;
const finite = (value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number => typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
const opaqueId = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value);
function invalid(): never { throw new Error("制作模式或草稿格式无效，未覆盖原记录。"); }

export function defaultModePreferences(mode: ProductionMode = "voiceover"): ModePreferences {
  return { pacing: "normal", tone: "neutral", background_music: mode !== "original", music_mood: "auto",
    motion_effects: mode !== "original", transitions: true, news_graphics: true, color_consistency: true,
    caption_style: "news", enhance_speech: true, generative_fill: false, custom_instructions: "",
    voice: "ai", lower_third: true, jump_cut_cover: "broll", quote_caption: "spoken" };
}
/** Explicit whitelist; never spread stored preferences or retired fields. */
export function readModePreferences(value: unknown, mode: ProductionMode = "voiceover"): ModePreferences {
  if (value !== undefined && !object(value)) return invalid();
  const result = defaultModePreferences(mode), input = value ?? {};
  const choices = { pacing: ["slow", "normal", "fast"], tone: ["solemn", "neutral", "energetic"],
    music_mood: ["auto", "solemn", "neutral", "uplifting", "tense"], caption_style: ["news", "big", "none"],
    voice: ["ai", "mine"], jump_cut_cover: ["broll", "zoom", "hard"], quote_caption: ["spoken", "none"] } as const;
  for (const key of Object.keys(choices) as Array<keyof typeof choices>) {
    if (!own(input, key)) continue;
    // Accept legacy input, but new drafts/renders use the canonical v2 spelling.
    const choice = key === "quote_caption" && input[key] === "asr" ? "spoken" : input[key];
    if (typeof choice !== "string" || !(choices[key] as readonly string[]).includes(choice)) return invalid();
    Object.assign(result, { [key]: choice });
  }
  for (const key of ["background_music", "motion_effects", "transitions", "news_graphics", "color_consistency", "generative_fill", "enhance_speech", "lower_third"] as const) {
    if (!own(input, key)) continue;
    if (typeof input[key] !== "boolean") return invalid();
    result[key] = input[key];
  }
  if (own(input, "custom_instructions")) {
    if (!text(input.custom_instructions, 500)) return invalid();
    result.custom_instructions = input.custom_instructions;
  }
  if (mode === "original") { result.generative_fill = false; result.voice = "ai"; }
  return result;
}

// Python str.strip/splitlines, including NEL and record separators. BOM is only
// stripped at the start of the document, as in backend.parse_sentences.
const whitespace = /[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u;
const trim = (value: string) => value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, "");
const speakerPrefix = /^([^\s（）():：，。！？,!?]{1,40})\s*[（(]([^（）()\n]{1,80})[）)]\s*[:：]\s*(.*)$/u;
const namedSync = /^([^\s（）():：，。！？,!?]{1,40})(?:\s*[（(]([^（）()\n]{1,80})[）)])?\s*[:：]\s*(.*)$/u;
const syncPrefix = /^(?:【同期】\s*[:：]?\s*|同期(?:\s*[:：]\s*|\s+|$))(.*)$/u;
export function parseLine(line: string): { kind: SentenceKind; content: string; hint: string } {
  line = trim(line);
  const narration = /^(?:旁白|记者)\s*[:：]\s*(.*)$/u.exec(line);
  if (narration) return { kind: "narration", content: narration[1], hint: "" };
  const sync = syncPrefix.exec(line), content = sync ? sync[1] : line;
  const named = (sync ? namedSync : speakerPrefix).exec(content);
  return named ? { kind: "quote", content: named[3], hint: named[1] + (named[2] ? `（${trim(named[2])}）` : "") }
    : { kind: sync ? "quote" : "narration", content, hint: "" };
}
const terminalMarks: readonly string[] = ["", "，", ",", "。", ".", ";", "；", "!", "?", "！", "？"];
const isTerminalPunctuation = (value: unknown): value is TerminalPunctuation => typeof value === "string" && terminalMarks.includes(value);
function sourceTerminal(value: string): TerminalPunctuation {
  const ending = trim(trim(value).replace(/[”’"'）)\]}】》」』]+$/u, ""));
  const characters = Array.from(ending), last = characters[characters.length - 1];
  return isTerminalPunctuation(last) ? last : "";
}
function splitTextParts(content: string, kind: SentenceKind): { text: string; terminal_punctuation: TerminalPunctuation }[] {
  if (kind !== "narration" && kind !== "quote") return invalid();
  let value = trim(content);
  if (value.length >= 2 && ["“”", '\"\"', "‘’"].includes(value[0] + value[value.length - 1])) value = value.slice(1, -1);
  const chars = Array.from(value), delimiters = kind === "narration" ? "，,。.;；!?！？" : "。.!?！？";
  const pairs: Record<string, string> = { "（": "）", "(": ")", "【": "】", "[": "]", "{": "}", "《": "》", "“": "”", "‘": "’" };
  const stack: string[] = [], output: { text: string; terminal_punctuation: TerminalPunctuation }[] = [];
  let start = 0;
  chars.forEach((character, index) => {
    if (character === '"' && chars[index - 1] !== "\\") {
      if (stack[stack.length - 1] === character) stack.pop(); else stack.push(character);
    } else if (own(pairs, character)) stack.push(pairs[character]);
    else if (stack.length && character === stack[stack.length - 1]) stack.pop();
    // Python isdigit also accepts superscript/circled digits after NFKC.
    const digit = (character: string | undefined) => !!character && /^\p{Nd}$/u.test(character.normalize("NFKC"));
    const decimal = character === "." && digit(chars[index - 1]) && digit(chars[index + 1]);
    if (delimiters.includes(character) && !stack.length && !decimal) {
      const part = trim(chars.slice(start, index).join(""));
      if (part) output.push({ text: part, terminal_punctuation: character as TerminalPunctuation });
      else if (output.length) output[output.length - 1].terminal_punctuation = character as TerminalPunctuation;
      start = index + 1;
    }
  });
  const last = trim(chars.slice(start).join(""));
  if (last) output.push({ text: last, terminal_punctuation: sourceTerminal(last) });
  return output;
}

export function splitText(content: string, kind: SentenceKind = "narration"): string[] {
  return splitTextParts(content, kind).map(part => part.text);
}

/** Same rules as Python parse_sentences. Overrides change kind, NOT splitting. */
export function parseModeScript(script: string, mode: ProductionMode = "voiceover", overrides: Readonly<Record<number, SentenceKind>> = {}): SentenceInput[] {
  if (typeof script !== "string" || !isProductionMode(mode) || !object(overrides)) return invalid();
  if (Array.from(script).length > MODE_LIMITS.script_max_chars) return invalid();
  const lines = script.replace(/^\ufeff+/u, "").split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/u).map(trim).filter(Boolean);
  const result: SentenceInput[] = [];
  for (const line of lines.slice(1)) {
    const parsed = parseLine(line);
    const kind = mode === "voiceover" ? "narration" : mode === "original" ? "quote" : parsed.kind;
    const content = mode === "voiceover" ? line : parsed.content, hint = mode === "voiceover" ? "" : parsed.hint;
    for (const part of splitTextParts(content, kind)) {
      if (Array.from(part.text).length > MODE_LIMITS.sentence_max_chars) throw new Error(`单句不能超过 ${MODE_LIMITS.sentence_max_chars} 字，请分句。`);
      result.push({ idx: result.length, ...part, kind, speaker_hint: hint });
    }
  }
  for (const [key, kind] of Object.entries(overrides)) {
    const idx = Number(key);
    if (!/^(0|[1-9]\d*)$/.test(key) || !Number.isSafeInteger(idx) || idx >= result.length
      || !(rules.modes[mode].allowed_kinds as readonly string[]).includes(kind)) return invalid();
    result[idx] = { ...result[idx], kind };
  }
  return result;
}

/** Reconcile re-parsed rows, retaining manual kinds/hints only at an identical
 * index/text. Source punctuation always comes from the NEW parse, never a stale
 * confirmed row. Title-only edits may retain the existing rows unchanged.
 */
export function reconcileManual(
  parsed: readonly SentenceInput[], previous: readonly SentenceInput[],
  kinds: Readonly<Record<number, SentenceKind>>, mode: ProductionMode,
): SentenceInput[] {
  if (!isProductionMode(mode)) return invalid();
  return parsed.map(sentence => {
    const old = previous[sentence.idx];
    if (!old || old.idx !== sentence.idx || old.text !== sentence.text) return { ...sentence };
    const kind = mode === "mixed" && own(kinds, sentence.idx.toString()) ? kinds[sentence.idx] : sentence.kind;
    if (!(rules.modes[mode].allowed_kinds as readonly string[]).includes(kind)) return invalid();
    return { ...sentence, kind,
      ...(old.source_hint ? { source_hint: { ...old.source_hint } } : {}),
      speaker_hint: sentence.speaker_hint || (old.source_hint ? old.speaker_hint : "") };
  });
}

const boundary = (character: string) => whitespace.test(character) || /[\p{P}\p{Z}\p{S}]/u.test(character);
const fillers = ["就是说", "那个", "就是", "然后", "嗯", "啊", "呃"];
const discourseNext = ["我们", "我", "你", "他们", "他", "她", "大家", "想", "希望", "要", "再", "今天", "这个", "其实"];
const digitMap: Record<string, string> = {};
["零〇", "一壹", "二两兩贰貳", "三叁參", "四肆", "五伍", "六陆陸", "七柒", "八捌", "九玖"].forEach((glyphs, digit) => {
  for (const glyph of glyphs) digitMap[glyph] = String(digit);
});
const smallUnits: Record<string, number> = { "十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000 };
const numberPattern = /[负負+-]?(?:[0-9零〇一二两兩三四五六七八九十百千万萬亿億壹贰貳叁參肆伍陆陸柒捌玖拾佰仟]+)(?:[.点點][0-9零〇一二两兩三四五六七八九]+)?/uy;
function integer(token: string): string {
  token = token.replace(/萬/g, "万").replace(/億/g, "亿");
  for (const [unit, factor] of [["亿", BigInt(100_000_000)], ["万", BigInt(10_000)]] as const) {
    const split = token.lastIndexOf(unit);
    if (split >= 0) return (BigInt(integer(token.slice(0, split) || "一")) * factor + BigInt(integer(token.slice(split + 1) || "零"))).toString();
  }
  if (!Array.from(token).some(character => own(smallUnits, character))) return Array.from(token, c => digitMap[c] ?? c).join("");
  let total = BigInt(0), previous = 10_000, digits = "";
  for (const character of token) {
    if (!own(smallUnits, character)) { digits += digitMap[character] ?? character; continue; }
    const factor = smallUnits[character];
    if (factor >= previous) throw new Error("不是明确的中文数值。");
    total += BigInt(digits || "1") * BigInt(factor); digits = ""; previous = factor;
  }
  return (total + BigInt(digits || "0")).toString();
}
function numberText(token: string, following: string): string {
  if (token === "一" && ["起", "直", "样", "致", "般", "旦", "定", "些", "切", "律", "再", "并", "共", "味", "体"].some(part => following.startsWith(part))) return token;
  if (token === "千万" && ["别", "不", "要", "记"].some(part => following.startsWith(part))) return token;
  let sign = "";
  if ("负負+-".includes(token[0])) { sign = token[0] === "+" ? "+" : "-"; token = token.slice(1); }
  try {
    const [whole, fraction] = token.split(/[.点點]/u);
    return sign + integer(whole) + (fraction === undefined ? "" : "." + integer(fraction));
  } catch { return sign + token; }
}
function casefold(value: string): string {
  return Array.from(value.normalize("NFKC"), character => {
    // Case folding is locale-independent: preserve dotless i, fold Cherokee
    // towards uppercase, expand sharp s and Greek combining forms.
    const code = character.codePointAt(0)!;
    if (character === "ı") return character;
    if (code >= 0x13a0 && code <= 0x13ff || code >= 0xab70 && code <= 0xabbf) return character.toUpperCase();
    return character.toUpperCase().toLowerCase().replace(/ß/g, "ss");
  }).join("");
}
function normalizeLexical(value: string, removeFillers: boolean): string {
  if (typeof value !== "string") return invalid();
  const raw = casefold(value);
  let retained = "", index = 0, afterFiller = false, previousCharacter = "";
  while (index < raw.length) {
    const atBoundary = index === 0 || afterFiller || boundary(previousCharacter);
    let removed = false;
    if (removeFillers && atBoundary) for (const filler of fillers) {
      if (!raw.startsWith(filler, index)) continue;
      const following = raw.slice(index + filler.length);
      if (!following || boundary(following[0]) || [...discourseNext, ...fillers].some(part => following.startsWith(part))) {
        index += filler.length; afterFiller = removed = true; break;
      }
    }
    if (removed) continue;
    afterFiller = false;
    const character = String.fromCodePoint(raw.codePointAt(index)!);
    if (!whitespace.test(character)) retained += character;
    previousCharacter = character;
    index += character.length;
  }
  let result = ""; index = 0;
  while (index < retained.length) {
    numberPattern.lastIndex = index;
    const numeric = numberPattern.exec(retained);
    if (numeric) { result += numberText(numeric[0], retained.slice(numberPattern.lastIndex)); index = numberPattern.lastIndex; continue; }
    const character = String.fromCodePoint(retained.codePointAt(index)!);
    if (!boundary(character) && !/\p{C}/u.test(character)) result += character;
    index += character.length;
  }
  return result.replace(/[0-9]+月[0-9]+号/g, date => date.slice(0, -1) + "日");
}
/** Comparison only; never replace displayed transcript or manufacture word timing. */
export function normalizeText(value: string): string { return normalizeLexical(value, true); }

/** Normative §10.3; full precision, multiset (not set) intersection. */
export function similarity(left: string, right: string): number {
  const a = Array.from(normalizeText(left)), b = Array.from(normalizeText(right));
  if (!a.length || !b.length) return 0;
  if (Math.min(a.length, b.length) === 1) return +(a.join("").includes(b.join("")) || b.join("").includes(a.join("")));
  const counts = new Map<string, number>();
  for (let i = 1; i < a.length; i++) { const gram = a[i - 1] + a[i]; counts.set(gram, (counts.get(gram) ?? 0) + 1); }
  let intersection = 0;
  for (let i = 1; i < b.length; i++) {
    const gram = b[i - 1] + b[i], count = counts.get(gram) ?? 0;
    if (count) { intersection++; counts.set(gram, count - 1); }
  }
  return Math.max(2 * intersection / (a.length + b.length - 2), intersection / Math.min(a.length - 1, b.length - 1));
}

export function scoreStatus(score: number, hasSource = true): "missing" | "low" | "ok" {
  if (!finite(score, 0, 1)) return invalid();
  return !hasSource || score < MATCH_LOW ? "missing" : score < MATCH_OK ? "low" : "ok";
}

/** Editorial prompts only: no inferred person identity or verified fact claim. */
export function factsOf(value: string): string[] {
  const normalized = normalizeText(value.replace(/一下/g, "片刻").replace(/一口/g, "口")), facts: string[] = [];
  if (/[0-9]+月[0-9]+日|[0-9]{4}年|[0-9]+[时分秒]/u.test(normalized)) facts.push("时间");
  if (/[0-9]+号|区|广场|信息港/u.test(normalized)) facts.push("地点");
  if (/主办|协办|承办/u.test(normalized)) facts.push("主办方");
  if (/[0-9]/u.test(normalized)) facts.push("数字");
  if (/先生|女士|大姐|主任|经理|教授|院士|记者|负责人|发言人/u.test(normalized)) facts.push("称谓");
  return facts;
}

/** Wizard estimate only, not an allocation or an invented render clock. */
export function sentenceNeeds(sentences: readonly SentenceInput[]) {
  const narration = sentences.filter(sentence => sentence.kind === "narration");
  const extra = narration.filter(sentence => sentence.text.includes("、")).length;
  return { narration: narration.length, quotes: sentences.length - narration.length, list_extra: extra, needed_shots: narration.length + extra };
}

/** Only apply to wizard selections; raw transcript and words remain untouched. */
export function stripLeadingFillers(value: string): string {
  let result = trim(value);
  while (result) {
    const filler = fillers.find(part => {
      if (!result.startsWith(part)) return false;
      const following = result.slice(part.length);
      return !following || boundary(following[0]) || [...discourseNext, ...fillers].some(next => following.startsWith(next));
    });
    if (!filler) break;
    result = result.slice(filler.length).replace(/^[ \t\r\n，,。.…!?！？;；、]+/u, "");
  }
  return result;
}

export function canonicalQuoteCaption(value: string): "spoken" | "none" {
  if (value !== "spoken" && value !== "asr" && value !== "none") return invalid();
  return value === "asr" ? "spoken" : value;
}

export function checkDefinition(code: string) {
  const templates: Record<string, { severity: string; level: number; message: string; action: string; action_kind: string; acknowledgeable?: boolean; modes?: string[] }> = { ...rules.checks, ...rules.safety_checks };
  if (!own(templates, code)) return invalid();
  return { ...templates[code] };
}

/** Pure recommendation; workbench owns planning, validation and execution. */
export function recommendPlan(operations: readonly string[], mode: ProductionMode): { rules_version: number; run: number[] } {
  if (!isProductionMode(mode)) return invalid();
  const stages = new Set<number>();
  for (const operation of operations) {
    if (!own(rules.plan_stages, operation) || mode === "original" && ["pacing", "voice", "to_narration"].includes(operation)
      || operation === "to_narration" && mode !== "mixed") return invalid();
    for (const n of rules.plan_stages[operation as keyof typeof rules.plan_stages]) stages.add(n);
  }
  return { rules_version: RULES_VERSION, run: [...stages].sort((a, b) => a - b) };
}

export function readSentenceInputs(value: unknown, mode?: ProductionMode): SentenceInput[] {
  if (!Array.isArray(value) || value.length > 2048) return invalid();
  const seen = new Set<number>();
  return value.map(item => {
    if (!object(item) || !finite(item.idx, 0, 100_000) || !Number.isSafeInteger(item.idx) || seen.has(item.idx)
      || !text(item.text, MODE_LIMITS.sentence_max_chars) || !item.text.trim()
      || item.kind !== "narration" && item.kind !== "quote"
      || mode && !(rules.modes[mode].allowed_kinds as readonly string[]).includes(item.kind)) return invalid();
    seen.add(item.idx);
    if (own(item, "terminal_punctuation") && !isTerminalPunctuation(item.terminal_punctuation)) return invalid();
    const result: SentenceInput = { idx: item.idx, text: item.text, kind: item.kind,
      terminal_punctuation: isTerminalPunctuation(item.terminal_punctuation) ? item.terminal_punctuation : "" };
    if (item.speaker_hint !== undefined) {
      if (!text(item.speaker_hint, 256)) return invalid();
      result.speaker_hint = item.speaker_hint;
    }
    if (item.source_hint != null) {
      if (!object(item.source_hint) || !opaqueId(item.source_hint.upload_id) || !text(item.source_hint.seg_id, 128) || !item.source_hint.seg_id.trim()) return invalid();
      result.source_hint = { upload_id: item.source_hint.upload_id, seg_id: item.source_hint.seg_id };
    }
    return result;
  });
}
/** Speaker IDs are bounded identity strings, not paths or upload IDs.
 * Preserve legacy colon-containing IDs verbatim; never apply opaqueId/path rules.
 */
export function readSpeakers(value: unknown): Speaker[] {
  if (!Array.isArray(value) || value.length > 1000) return invalid();
  const seen = new Set<string>();
  return value.map(item => {
    if (!object(item) || !text(item.id, 128) || !item.id.trim() || seen.has(item.id)
      || !text(item.name, 80) || !text(item.title, 160) || !text(item.auto_label, 160)
      || !finite(item.appearances, 0, 100_000) || !Number.isSafeInteger(item.appearances) || !finite(item.seconds, 0, 604_800)) return invalid();
    seen.add(item.id);
    return { id: item.id, name: item.name, title: item.title, auto_label: item.auto_label, appearances: item.appearances, seconds: item.seconds };
  });
}

/** Apply script-provided names only to an actual matched speaker cluster.
 * Conflicting hints stay unnamed; do not invent a cross-recording identity.
 */
export function applySpeakerHints(people: readonly Speaker[], sentences: readonly SentenceInput[], matches: readonly MatchPreview[]): Speaker[] {
  const hints = new Map<string, { name: string; title: string } | null>();
  for (const sentence of sentences) {
    const source = sentence.kind === "quote" ? matches.find(match => match.idx === sentence.idx)?.source : null;
    const hint = sentence.speaker_hint && /^([^\s（）():：，。！？,!?]{1,40})(?:[（(]([^（）()]{1,80})[）)])?$/u.exec(sentence.speaker_hint);
    if (!source?.speaker_id || !hint || sentence.speaker_hint === source.speaker_id) continue;
    const person = { name: hint[1].trim(), title: hint[2]?.trim() ?? "" }, previous = hints.get(source.speaker_id);
    if (!hints.has(source.speaker_id)) hints.set(source.speaker_id, person);
    else if (!previous || previous.name !== person.name || previous.title !== person.title) hints.set(source.speaker_id, null);
  }
  return people.map(person => {
    const hint = hints.get(person.id);
    return hint && !person.name && !person.title ? { ...person, name: hint.name, title: hint.title } : person;
  });
}

/** Draft reconciliation without rewriting a manual type override's boundaries.
 * Compare canonical literal bodies, not normalization that removes fillers or
 * changes numeric values. Stale/injected stored sentences never replace text.
 */
export function sentencesMatchScript(script: string, sentences: readonly SentenceInput[], mode: ProductionMode): boolean {
  let parsed: SentenceInput[];
  try { parsed = parseModeScript(script, mode); } catch { return false; }
  const literal = (rows: readonly SentenceInput[]) => rows.map(row => {
    const characters = Array.from(row.text);
    return characters.filter((character, index) => {
      if (character === ".") return /^\p{Nd}$/u.test(characters[index - 1] ?? "") && /^\p{Nd}$/u.test(characters[index + 1] ?? "");
      return !/[\s，,。;；!?！？]/u.test(character);
    }).join("");
  }).join("");
  return sentences.every((sentence, index) => sentence.idx === index)
    && literal(parsed) === literal(sentences);
}

export const matchState = (value: Pick<MatchPreview, "score" | "source"> | undefined): "pending" | "missing" | "low" | "ok" =>
  !value ? "pending" : scoreStatus(value.score, !!value.source);

/** Deliberately NOT a subsequence test. Internal deletions cannot become one cut. */
export function quoteTextEvidence(value: string, actualText: string): { contiguous: boolean; exact: boolean; unverified: boolean } {
  // Do not excuse an inserted/interior-deleted filler just because alignment
  // ignores discourse fillers. Numeric/date formatting remains equivalent.
  const query = normalizeLexical(value, false), actual = normalizeLexical(actualText, false);
  const contiguous = !!normalizeText(value) && !!query && actual.includes(query);
  return { contiguous, exact: contiguous && query === actual, unverified: !contiguous };
}
export function quoteGate(sentences: readonly SentenceInput[], matches: readonly MatchPreview[], mode: ProductionMode): string {
  if (mode === "voiceover") return "";
  const quotes = sentences.filter(sentence => sentence.kind === "quote"), byId = new Map(matches.map(match => [match.idx, match]));
  if (mode === "original" && !quotes.length) return "请先从转写里选择至少一句原话。";
  if (quotes.some(sentence => !byId.has(sentence.idx))) return "正在核对原话；请等待匹配完成，失败时可手动重试。";
  const missing = quotes.filter(sentence => matchState(byId.get(sentence.idx)) === "missing");
  if (missing.length) return `有 ${missing.length} 句原话没找到，请${mode === "mixed" ? "改成旁白、" : ""}删除或补传含这句话的素材。`;
  if (mode === "original" && quotes.some(sentence => quoteTextEvidence(sentence.text, byId.get(sentence.idx)!.source!.asr_text).unverified)) {
    return "原话有未核实的增改或中间删词；请重新选取连续原话，不能拼出素材里没有说过的话。";
  }
  return "";
}
/** A clock-derived preview only; no claim that a B-roll shot is available. */
export function countPreviewJumpCuts(sentences: readonly SentenceInput[], matches: readonly MatchPreview[]): number {
  const byId = new Map(matches.map(match => [match.idx, match]));
  let previous: QuoteTake | null = null, count = 0;
  for (const sentence of sentences) {
    const source = sentence.kind === "quote" ? byId.get(sentence.idx)?.source ?? null : null;
    if (previous && source && (source.upload_id !== previous.upload_id || source.start < previous.end || source.start > previous.end + MODE_LIMITS.jump_continuity_seconds)) count++;
    previous = source;
  }
  return count;
}
export function scriptFromSentences(title: string, sentences: readonly SentenceInput[], mode: ProductionMode): string {
  const printableHint = (hint: string | undefined) => hint && /^[^\s（）():：，。！？,!?]{1,40}(?:（[^（）()\n]{1,80}）)?$/u.test(hint) ? ` ${hint}` : "";
  return [title || TITLE_PLACEHOLDER, ...sentences.map(sentence => {
    const terminal = sentence.terminal_punctuation ?? "";
    const body = sentence.text + (terminal && sourceTerminal(sentence.text) !== terminal ? terminal : "");
    return mode !== "mixed" ? body : sentence.kind === "quote"
      ? `同期${printableHint(sentence.speaker_hint)}：${body}` : `旁白：${body}`;
  })].join("\n");
}
/** Append in explicit selection order; uncheck by source identity, not text equality. */
export function selectTranscriptSentence(sentences: readonly SentenceInput[], uploadId: string, segment: TranscriptSegment, selected: boolean): SentenceInput[] {
  const existing = sentences.filter(sentence => sentence.source_hint?.upload_id === uploadId && sentence.source_hint.seg_id === segment.id);
  let next = [...sentences];
  if (!selected) next = next.filter(sentence => !existing.includes(sentence));
  else if (!existing.length) next.push(...parseModeScript(`${TITLE_PLACEHOLDER}\n${stripLeadingFillers(segment.text)}`, "original").map(sentence => ({
    ...sentence, source_hint: { upload_id: uploadId, seg_id: segment.id }, speaker_hint: segment.speaker_id,
  })));
  return next.map((sentence, idx) => ({ ...sentence, idx }));
}

export interface LegacyCoreTask {
  id: string; title: string; script: string; mode: ProductionMode;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | "uploading";
  revision: number; createdAt: number;
}
export interface LegacyCoreDraft {
  script: string; step: 1 | 2 | 3; mode: ProductionMode; preferences: ModePreferences;
  files: MediaMetadata[]; elements: {}; sentenceChecks: Record<string, boolean>;
  voice: "ai" | "self"; ownVoice: null;
  sentenceKinds: Record<number, SentenceKind>; speakers: Speaker[]; sentences: SentenceInput[];
}
export interface LegacyCore {
  tasks: Record<string, LegacyCoreTask>; history: string[]; draft: LegacyCoreDraft | null;
  bigFont: boolean; legacyFileNames: string[];
}
/** Read-only projection of gm-core-v1. No storage, expiry deletion, task restart,
 * server capabilities, simulated rows, checks or pending mutations are imported.
 * Old mb/sec file summaries cannot establish byte identity: retain names for a
 * warning, never manufacture a ready upload or an exact-size File from them.
 */
export function restoreLegacyCore(raw: string | null): LegacyCore | null {
  if (raw === null) return null;
  if (typeof raw !== "string" || raw.length > 2 * 1024 * 1024) return invalid();
  const value: unknown = JSON.parse(raw);
  if (!object(value) || !object(value.tasks) || !Array.isArray(value.history)
    || Object.keys(value.tasks).length > 100 || value.history.length > 100) return invalid();
  const tasks: Record<string, LegacyCoreTask> = Object.create(null);
  for (const [id, item] of Object.entries(value.tasks)) {
    if (!opaqueId(id) || !object(item) || item.id !== id || !text(item.title, 512) || !text(item.script ?? "", MODE_LIMITS.script_max_chars)) return invalid();
    const status = item.status === "held" ? "queued" : item.status;
    if (typeof status !== "string" || !["queued", "running", "done", "failed", "cancelled", "uploading"].includes(status)
      || !finite(item.revision ?? 0) || !Number.isSafeInteger(item.revision ?? 0) || !finite(item.createdAt ?? 0)
      || item.mode !== undefined && !isProductionMode(item.mode)) return invalid();
    tasks[id] = { id, title: item.title, script: (item.script ?? "") as string, mode: item.mode ?? "voiceover",
      status: status as LegacyCoreTask["status"], revision: (item.revision ?? 0) as number, createdAt: (item.createdAt ?? 0) as number };
  }
  if (!value.history.every(id => opaqueId(id) && own(tasks, id)) || new Set(value.history).size !== value.history.length) return invalid();
  const result: LegacyCore = { tasks, history: [...value.history] as string[], draft: null, bigFont: value.bigFont === true, legacyFileNames: [] };
  if (value.draft == null) return result;
  const draft = value.draft;
  if (!object(draft) || !text(draft.script ?? "", MODE_LIMITS.script_max_chars) || ![1, 2, 3].includes(draft.step as number)
    || draft.mode !== undefined && !isProductionMode(draft.mode) || !Array.isArray(draft.files ?? []) || ((draft.files ?? []) as unknown[]).length > 100) return invalid();
  const mode = draft.mode ?? "voiceover", script = (draft.script ?? "") as string;
  const preferences = readModePreferences(draft.preferences ?? draft.prefs, mode);
  const parsed = parseModeScript(script, mode), kinds: Record<number, SentenceKind> = {};
  if (draft.typeMarks !== undefined) {
    if (!object(draft.typeMarks) || Object.keys(draft.typeMarks).length > 2048) return invalid();
    for (const sentence of parsed) {
      const mark = own(draft.typeMarks, sentence.text) ? draft.typeMarks[sentence.text] : undefined;
      if (mark === undefined) continue;
      if (mark !== "narration" && mark !== "quote") return invalid();
      if ((rules.modes[mode].allowed_kinds as readonly string[]).includes(mark)) kinds[sentence.idx] = mark;
    }
  }
  const speakers: Speaker[] = [];
  if (draft.speakerNames !== undefined) {
    if (!object(draft.speakerNames) || Object.keys(draft.speakerNames).length > 1000) return invalid();
    for (const [id, person] of Object.entries(draft.speakerNames)) {
      if (!text(id, 128) || !id.trim() || !object(person) || !text(person.name ?? "", 80) || !text(person.title ?? person.role ?? "", 160)) return invalid();
      speakers.push({ id, name: (person.name ?? "") as string, title: (person.title ?? person.role ?? "") as string,
        auto_label: "", appearances: 0, seconds: 0 });
    }
  }
  for (const file of (draft.files ?? []) as unknown[]) {
    if (!object(file) || !text(file.name, 512) || !file.name || /[\x00-\x1f/\\]/.test(file.name)) return invalid();
    result.legacyFileNames.push(file.name);
  }
  result.draft = { script, step: draft.step as 1 | 2 | 3, mode, preferences, files: [], elements: {}, sentenceChecks: {},
    voice: preferences.voice === "mine" ? "self" : "ai", ownVoice: null, sentenceKinds: kinds, speakers,
    sentences: parseModeScript(script, mode, kinds) };
  return result;
}