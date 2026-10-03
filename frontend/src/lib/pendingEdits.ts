import { parseApplySteps, parseRecordingReceipt } from './workbenchApi';
import type { ApplyStep, QuoteRange, RecordingReceipt, WorkbenchPreferenceChanges } from './workbenchApi';

export const PENDING_KEY = 'gm-modes-v1';
export const PENDING_SLOT = 'resultV2';
export interface PendingSentence {
  text?: string; instruction?: string; shot_id?: number;
  /** Deliberately memory-only, including the filename and blob. */
  file?: File; recording?: RecordingReceipt;
}
export interface PendingDraft extends WorkbenchPreferenceChanges {
  base: number | null; sentences: Record<number, PendingSentence>; deleted: number[];
  pendTrim: Record<number, QuoteRange>; pendTake: Record<number, { take_id: string }>;
  pendToNarration: number[]; pendSpeakers: Record<string, { name: string; title: string }>;
  submitted?: number; operationId?: string; submittedPlan?: ApplyStep[];
  lastOperation?: { id: string; revision: number };
}
export const emptyPending = (): PendingDraft => ({ base: null, sentences: {}, deleted: [],
  pendTrim: {}, pendTake: {}, pendToNarration: [], pendSpeakers: {} });
type Store = Pick<Storage, 'getItem' | 'setItem'>;
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const revision = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0;
const rowKey = (s: string) => /^(0|[1-9]\d{0,5})$/.test(s);
const identity = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(v)
  && !['__proto__', 'constructor', 'prototype'].includes(v);
const safeText = (v: unknown, max: number): v is string => typeof v === 'string' && v.length <= max
  && !/[\u0000-\u001f\u007f]|(?:https?|blob|data|file):|(?:[A-Z]:\\|\\\\)|(?:access[_-]?token|api[_-]?key|authorization)\s*[:=]|Bearer\s|Traceback|\bat\s+\S+\s*\([^)]*:\d+/i.test(v);
const ids = (v: unknown): number[] => Array.isArray(v) ? [...new Set(v.filter(n => revision(n) && n < 100000))].slice(0, 200) : [];

/** An allowlist projection, not JSON.stringify(draft). Unknown properties, media,
 * credentials, filenames and errors never enter this managed storage slot. */
export function projectPending(value: unknown): PendingDraft | null {
  if (!object(value) || !revision(value.base)) return null;
  const out = { ...emptyPending(), base: value.base };
  if (object(value.sentences)) for (const [id, item] of Object.entries(value.sentences).slice(0, 200)) {
    if (!rowKey(id) || !object(item)) continue;
    const sentence: PendingSentence = {};
    if (safeText(item.text, 2000)) sentence.text = item.text;
    if (safeText(item.instruction, 500)) sentence.instruction = item.instruction;
    if (revision(item.shot_id)) sentence.shot_id = item.shot_id;
    const r = item.recording;
    if (r !== undefined) try { sentence.recording = parseRecordingReceipt(r, Number(id), value.base); } catch { /* Invalid evidence is never restored. */ }
    if (Object.keys(sentence).length) out.sentences[Number(id)] = sentence;
  }
  out.deleted = ids(value.deleted); out.pendToNarration = ids(value.pendToNarration);
  if (object(value.pendTrim)) for (const [id, r] of Object.entries(value.pendTrim).slice(0, 200)) {
    if (rowKey(id) && object(r) && typeof r.start === 'number' && typeof r.end === 'number'
      && Number.isFinite(r.start) && Number.isFinite(r.end) && r.start >= 0 && r.end >= r.start + 1 && r.end <= 604800)
      out.pendTrim[Number(id)] = { start: r.start, end: r.end };
  }
  if (object(value.pendTake)) for (const [id, t] of Object.entries(value.pendTake).slice(0, 200)) {
    if (rowKey(id) && object(t) && safeText(t.take_id, 200) && /^[A-Za-z0-9_.:-]{1,200}$/.test(t.take_id))
      out.pendTake[Number(id)] = { take_id: t.take_id };
  }
  if (object(value.pendSpeakers)) for (const [id, s] of Object.entries(value.pendSpeakers).slice(0, 200)) {
    if (identity(id) && object(s) && safeText(s.name, 80) && safeText(s.title, 120)) out.pendSpeakers[id] = { name: s.name, title: s.title };
  }
  if (['slow', 'normal', 'fast'].includes(String(value.pacing))) out.pacing = value.pacing as PendingDraft['pacing'];
  if (['news', 'big', 'none'].includes(String(value.caption_style))) out.caption_style = value.caption_style as PendingDraft['caption_style'];
  if (typeof value.enhance_speech === 'boolean') out.enhance_speech = value.enhance_speech;
  if (typeof value.operationId === 'string' && /^[a-f0-9]{32}$/.test(value.operationId)) out.operationId = value.operationId;
  if (value.submittedPlan !== undefined && out.operationId) try { out.submittedPlan = parseApplySteps(value.submittedPlan, true); } catch { /* Never accept an arbitrary revision jump. */ }
  if (revision(value.submitted) && value.submitted === value.base + (out.submittedPlan?.length ?? 1)) out.submitted = value.submitted;
  if (object(value.lastOperation) && typeof value.lastOperation.id === 'string' && /^[a-f0-9]{32}$/.test(value.lastOperation.id)
    && revision(value.lastOperation.revision) && value.lastOperation.revision <= value.base)
    out.lastOperation = { id: value.lastOperation.id, revision: value.lastOperation.revision };
  return out;
}
function readRoot(storage: Store): Record<string, unknown> {
  const raw = storage.getItem(PENDING_KEY);
  if (raw === null) return {};
  const root: unknown = JSON.parse(raw);
  if (raw.length > 4 * 1024 * 1024 || !object(root) || root.schemaVersion !== undefined && root.schemaVersion !== 1) throw new Error('本机保存内容格式不兼容，未覆盖原内容。');
  return root;
}
export function readPending(taskId: string, storage?: Store): PendingDraft | null {
  try {
    const root = readRoot(storage ?? localStorage), pend = root.pend;
    if (!object(pend) || !object(pend[PENDING_SLOT])) return null;
    const slot = pend[PENDING_SLOT][taskId];
    return object(slot) && slot.schema === 2 && slot.task_id === taskId ? projectPending(slot.draft) : null;
  } catch { return null; }
}
/** Read latest on EVERY write. Own only pend.resultV2[taskId]; even legacy pend
 * entries and foreign draft/checked/workspace slots remain byte-value intact. */
const slotFingerprint = (root: Record<string, unknown>, taskId: string) => JSON.stringify(object(root.pend) && object(root.pend[PENDING_SLOT]) ? root.pend[PENDING_SLOT][taskId] : null) ?? 'null';
export function writePending(taskId: string, draft: PendingDraft | null, storage: Store = localStorage, baseline?: string): string {
  if (!identity(taskId)) throw new Error('无效作品标识，未保存修改。');
  const before = storage.getItem(PENDING_KEY);
  const root = readRoot(storage);
  if (baseline !== undefined && slotFingerprint(root, taskId) !== baseline) throw new Error('本机修改已被其他页面更新，未覆盖；请保留文字并重新核对。');
  if (root.pend !== undefined && !object(root.pend)) throw new Error('旧待应用内容格式不兼容，未覆盖原内容。');
  const pend = { ...(root.pend as Record<string, unknown> | undefined) };
  if (pend[PENDING_SLOT] !== undefined && !object(pend[PENDING_SLOT])) throw new Error('待应用内容格式不兼容，未覆盖原内容。');
  const slot = { ...(pend[PENDING_SLOT] as Record<string, unknown> | undefined) };
  if (slot[taskId] !== undefined && (!object(slot[taskId]) || slot[taskId].schema !== 2 || slot[taskId].task_id !== taskId)) throw new Error('本机修改版本不兼容，未覆盖原内容。');
  const projected = draft && projectPending(draft);
  if (draft && !projected) throw new Error('修改没有绑定作品版本，未保存。');
  if (projected) slot[taskId] = { schema: 2, task_id: taskId, draft: projected };
  else delete slot[taskId];
  pend[PENDING_SLOT] = slot;
  const encoded = JSON.stringify({ ...root, pend });
  if (encoded.length > 4 * 1024 * 1024 || storage.getItem(PENDING_KEY) !== before) throw new Error('本机保存内容已变化，未覆盖原内容。');
  storage.setItem(PENDING_KEY, encoded);
  if (storage.getItem(PENDING_KEY) !== encoded) throw new Error('本机保存未确认，请保留本页修改。');
  return slotFingerprint({ ...root, pend }, taskId);
}
/** Injectable scheduler keeps debounce/flush tests independent of wall clocks. */
export function pendingWriter(taskId: string, storage?: Store, scheduler = {
  // Browser timer functions reject an arbitrary object as their receiver.
  // Call the globals directly; injected schedulers keep their own method `this`.
  set: (callback: () => void, delay: number) => setTimeout(callback, delay),
  clear: (timer: ReturnType<typeof setTimeout>) => clearTimeout(timer),
}) {
  let timer: ReturnType<typeof setTimeout> | undefined, queued: PendingDraft | null | undefined;
  let baseline: string | undefined;
  try { baseline = slotFingerprint(readRoot(storage ?? localStorage), taskId); } catch { /* Explicit flush reports unavailable storage. */ }
  const flush = () => {
    if (timer !== undefined) scheduler.clear(timer);
    timer = undefined;
    if (queued === undefined) return;
    baseline = writePending(taskId, queued, storage, baseline); queued = undefined;
  };
  return { flush, schedule(draft: PendingDraft | null) {
    queued = draft;
    if (timer !== undefined) scheduler.clear(timer);
    timer = scheduler.set(() => { try { flush(); } catch { /* Explicit flush reports storage failure; retain queued content. */ } }, 300);
  } };
}