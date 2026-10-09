import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { CreateWizard, defaultPreferences } from './CreateWizard';
import ScriptAssistant from './ScriptAssistant';
import type { CreateSubmission, Draft } from './CreateWizard';
import Processing from './Processing';
import ResultWorkbench from './ResultWorkbench';
import {
  AppApiError, HISTORY_KEY, assertSameOriginConfiguration, createAppRequest, errorText,
  forgetHistory, getLocalHistory, getPublicLimits, getWorkspaceConfig, parseReceipt,
  parseTask, publicRequest, readHistory, rememberWork, taskMedia, uploadTask,
  goneReason, renameTaskMetadata, validMetadataTitle,
} from '../lib/appApi';
import type { AppRequest, GoneReason, HistoryWork, Receipt, TaskMetadata, UploadProgress } from '../lib/appApi';
import { ELEMENT_RULES, fileIdentity } from '../lib/mediaInput';
import type { MediaMetadata } from '../lib/mediaInput';
import { isProductionMode, LEGACY_CORE_KEY, MODE_LABELS, parseModeScript, readModePreferences, readSentenceInputs, readSpeakers, restoreLegacyCore, sentencesMatchScript } from '../lib/productionModes';
import type { ProductionMode, SentenceKind } from '../lib/productionModes';
import { readUploadBindings, readUploadTokens, validUploadId } from '../lib/uploadSessions';
import { createSubmissionRecovery } from '../lib/submissionRecovery';
import Icon from './ui/Icon';
import IntroCard from './ui/IntroCard';
import SampleView, { type SampleTry } from './ui/SampleView';
import './ui/tokens.css';
import { v2Persistence } from '../lib/v2Persistence';
import { historyChecksLabel, parseHistoryChecks } from '../lib/historyChecks';
import type { HistoryChecksSummary } from '../lib/historyChecks';
import type { WorkbenchChecks } from '../lib/workbenchApi';
import { DRAFT_SAVE_DELAY_MS, V2_HISTORY_LIMIT } from '../model';
import type { DraftTaskBinding, HistorySort, ShellPreferences, WorkspaceScreen } from '../model';
import type { PublicLimits, TaskStatusResponse } from '../types';

const DRAFT_KEY = LEGACY_CORE_KEY;
const LEGACY_DRAFT_KEY = 'golden-mic.draft.v2';
const MAX_DRAFT_LENGTH = 512 * 1024;
const MAX_CORE_LENGTH = 2 * 1024 * 1024;
const MAX_SCRIPT_LENGTH = 100_000;
type Page = WorkspaceScreen;
/** submit() shows this right after the server accepts a work; it is cleared once the real status has been read. */
const RECEIVED_NOTICE = '服务器已确认接收作品。正在核验实际制作状态；请勿重复提交。';
type Route = { page: 'create' | 'gone'; invalid?: boolean; drawer?: boolean } | { page: 'work'; id: string } | { page: 'sample' };
type WorkspaceConfig = { local_history: boolean; generative_fill_available: boolean };
type Selection = { id: string; token?: string; title: string; mode?: ProductionMode; scope: AbortController; request: AppRequest };
type OpenIntent = { id: string; target: 'result'; serial: number; receipt?: HistoryWork };
type DialogSpec = { title: string; text: string; confirm: string; danger?: boolean; action: () => Promise<void> };
type RenameSpec = { baseline: TaskMetadata; save: (baseline: TaskMetadata, title: string) => Promise<void>; read: () => Promise<TaskMetadata> };
const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const validId = (value: string) => /^[A-Za-z0-9_-]{1,128}$/.test(value);
const pathFor = (id: string) => `/api/tasks/${encodeURIComponent(id)}`;
const hashFor = (id: string) => `/tasks/${encodeURIComponent(id)}`;
const statusLabel = (status: string) => ({ queued: '等待制作', running: '制作中', done: '已完成', failed: '制作未完成', cancelled: '已取消', gone: '已到期或已清理' }[status] ?? '状态待确认');
const dateLabel = (value?: string) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('zh-CN') : '时间未提供';
const timestamp = (value?: string) => value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;
const versionLabel = (item: HistoryWork) => item.version_count !== undefined && item.version_count >= 1
  ? `改了 ${item.version_count - 1} 次` : item.revision !== undefined ? `版本 ${item.revision}` : '版本未提供';
const savedTime = (value: number) => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
const modeLabel = (value: unknown) => MODE_LABELS[isProductionMode(value) ? value : 'voiceover'];

function readRoute(hash: string, pathname = '/'): Route {
  // Legacy hash links take precedence when explicitly supplied. Neither path
  // nor hash can grant a capability; all GETs use browser receipts/local policy.
  if (!hash && pathname === '/samples/default') return { page: 'sample' };
  if (!hash && pathname !== '/') {
    const match = /^\/tasks\/([A-Za-z0-9_-]{1,128})\/?$/.exec(pathname);
    return match ? { page: 'work', id: match[1] } : { page: 'gone', invalid: true };
  }
  if (!hash || hash === '#create') return { page: 'create' };
  if (hash === '#history') return { page: 'create', drawer: true };
  const params = new URLSearchParams(hash.slice(1));
  const ids = [...params.getAll('work'), ...params.getAll('show')];
  // Both link spellings open the same workbench; a URL never supplies a token.
  if (ids.length === 1 && validId(ids[0])) return { page: 'work', id: ids[0] };
  return { page: 'gone', invalid: true };
}

function invalidDraft(): never { throw new Error('草稿格式无效或超过本地缓存上限。'); }
function draftText(value: unknown, maximum: number): string {
  if (typeof value !== 'string' || value.length > maximum) return invalidDraft();
  return value;
}
function draftNumber(value: unknown, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) return invalidDraft();
  return value;
}
function readMediaMetadata(value: unknown, voice = false): MediaMetadata {
  if (!isObject(value)) return invalidDraft();
  const name = draftText(value.name, 512);
  if (!name || /[\x00-\x1f/\\]/.test(name)) return invalidDraft();
  const size = draftNumber(value.size); const lastModified = draftNumber(value.lastModified);
  if (!Number.isSafeInteger(size) || !Number.isSafeInteger(lastModified)) return invalidDraft();
  const kind = value.kind;
  if (voice ? kind !== 'audio' : kind !== 'video' && kind !== 'image') return invalidDraft();
  if (kind !== 'audio' && kind !== 'video' && kind !== 'image') return invalidDraft();
  const type = draftText(value.type, 256);
  if (/[\x00-\x1f]/.test(type)) return invalidDraft();
  // Explicit projection drops File/Blob, preview URLs, paths and unknown fields.
  // Reconstruct the browser file identity instead of trusting an arbitrary URL/id.
  const result: MediaMetadata = {
    id: fileIdentity({ name, size, lastModified }), name, size, lastModified, type, kind,
    duration: value.duration === null ? null : draftNumber(value.duration, 0, 604_800),
    note: draftText(value.note, 512),
    // A draft may contain a temporarily invalid trim; submission validates it.
    trim_start: draftNumber(value.trim_start, -604_800, 604_800),
    trim_end: value.trim_end === null ? null : draftNumber(value.trim_end, -604_800, 604_800),
  };
  for (const key of ['width', 'height'] as const) {
    if (value[key] !== undefined) {
      const number = draftNumber(value[key], 1, 100_000);
      if (!Number.isSafeInteger(number)) return invalidDraft();
      result[key] = number;
    }
  }
  return result;
}

/** Restore only bounded, typed metadata. Never spread untrusted preferences. */
export function validateDraft(value: unknown): Draft & DraftTaskBinding {
  if (!isObject(value) || ![1, 2, 3].includes(Number(value.step)) || typeof value.step !== 'number'
      || !Array.isArray(value.files) || value.files.length > 100) return invalidDraft();
  if (value.mode !== undefined && !isProductionMode(value.mode)) return invalidDraft();
  const mode = value.mode ?? 'voiceover';
  const preferences = readModePreferences(value.preferences, mode);
  const files = value.files.map(item => readMediaMetadata(item));
  if (new Set(files.map(item => item.id)).size !== files.length) return invalidDraft();
  const elements: Draft['elements'] = {};
  if (value.elements !== undefined) {
    if (!isObject(value.elements)) return invalidDraft();
    for (const { key } of ELEMENT_RULES) {
      const item = value.elements[key];
      if (item === undefined) continue;
      if (!isObject(item) || typeof item.confirmed !== 'boolean') return invalidDraft();
      elements[key] = { confirmed: item.confirmed, evidence: draftText(item.evidence, MAX_SCRIPT_LENGTH + 16) };
    }
  }
  const sentenceChecks: Record<string, boolean> = {};
  if (value.sentenceChecks !== undefined) {
    if (!isObject(value.sentenceChecks) || Object.keys(value.sentenceChecks).length > 2048) return invalidDraft();
    for (const [key, checked] of Object.entries(value.sentenceChecks)) {
      if (!/^\d{1,6}:/.test(key) || key.length > MAX_SCRIPT_LENGTH + 16 || typeof checked !== 'boolean') return invalidDraft();
      sentenceChecks[key] = checked;
    }
  }
  if (value.voice !== undefined && value.voice !== 'ai' && value.voice !== 'self') return invalidDraft();
  const sentenceKinds: Record<number, SentenceKind> = {};
  if (value.sentenceKinds !== undefined) {
    if (!isObject(value.sentenceKinds) || Object.keys(value.sentenceKinds).length > 2048) return invalidDraft();
    for (const [key, kind] of Object.entries(value.sentenceKinds)) {
      if (!/^(0|[1-9]\d{0,5})$/.test(key) || Number(key) > 100_000 || kind !== 'narration' && kind !== 'quote'
          || mode === 'voiceover' && kind !== 'narration' || mode === 'original' && kind !== 'quote') return invalidDraft();
      sentenceKinds[Number(key)] = kind;
    }
  }
  const uploadBindings = readUploadBindings(value.uploadBindings);
  if (uploadBindings.some(binding => !files.some(file => file.id === binding.file_id))) return invalidDraft();
  const uploadIds = value.uploadIds === undefined ? uploadBindings.map(binding => binding.upload_id) : value.uploadIds;
  if (!Array.isArray(uploadIds) || uploadIds.length > 100 || !uploadIds.every(validUploadId)
      || new Set(uploadIds).size !== uploadIds.length || uploadIds.length !== uploadBindings.length
      || uploadBindings.some(binding => !uploadIds.includes(binding.upload_id))) return invalidDraft();
  // A broken old binding must fail closed with its raw cache intact, not get
  // "migrated" by throwing away already-issued upload capabilities.
  if (isObject(value.uploadTokens) && Object.keys(value.uploadTokens).some(id => !uploadIds.includes(id))) return invalidDraft();
  const uploadTokens = readUploadTokens(value.uploadTokens, uploadIds);
  if (value.source_voice_preferred !== undefined && typeof value.source_voice_preferred !== 'boolean') return invalidDraft();
  const result: Draft & DraftTaskBinding = {
    script: draftText(value.script, MAX_SCRIPT_LENGTH), step: value.step as Draft['step'], preferences, files, elements, sentenceChecks,
    // Preserve explicit false as well as true; absent legacy drafts keep their existing defaults.
    ...(value.source_voice_preferred === undefined ? {} : { source_voice_preferred: value.source_voice_preferred }),
    voice: value.voice ?? (preferences.voice === 'mine' ? 'self' : 'ai'), ownVoice: value.ownVoice == null ? null : readMediaMetadata(value.ownVoice, true),
    mode, sentenceKinds, speakers: readSpeakers(value.speakers ?? []), uploadIds: [...uploadIds], uploadTokens, uploadBindings,
  };
  if (value.draftTaskId !== undefined || value.draftTaskToken !== undefined) {
    if (typeof value.draftTaskId !== 'string' || !validId(value.draftTaskId)
        || typeof value.draftTaskToken !== 'string' || !/^[A-Za-z0-9_-]{32,256}$/.test(value.draftTaskToken)) return invalidDraft();
    result.draftTaskId = value.draftTaskId; result.draftTaskToken = value.draftTaskToken;
  }
  if (uploadBindings.some(binding => binding.draftTaskId !== result.draftTaskId)) return invalidDraft();
  if (value.legacyRestore !== undefined) {
    if (typeof value.legacyRestore !== 'boolean' || value.legacyRestore && result.draftTaskId) return invalidDraft();
    result.legacyRestore = value.legacyRestore;
  }
  // An unfinished/oversized manuscript is still a draft. A parser error must
  // not discard otherwise-valid upload receipts. For parseable text, catch a
  // stale sentence/type plan HERE rather than let the wizard's invalid-seed
  // fallback silently drop the upload bindings on its next emission.
  if (value.sentences !== undefined) result.sentences = readSentenceInputs(value.sentences, mode);
  if (result.sentences?.some(sentence => sentence.source_hint && !uploadIds.includes(sentence.source_hint.upload_id))) return invalidDraft();
  let parsed: ReturnType<typeof parseModeScript> | null;
  try { parsed = parseModeScript(result.script, mode); } catch { parsed = null; }
  if (parsed !== null) {
    if (result.sentences && !sentencesMatchScript(result.script, result.sentences, mode)) return invalidDraft();
    const rows = result.sentences ?? parsed;
    for (const [key, kind] of Object.entries(sentenceKinds)) {
      const row = rows.find(sentence => sentence.idx === Number(key));
      if (!row || result.sentences && row.kind !== kind) return invalidDraft();
    }
  }
  if (JSON.stringify(result).length > MAX_DRAFT_LENGTH) return invalidDraft();
  return result;
}

function parseJson(raw: string, maximum = MAX_CORE_LENGTH): unknown {
  if (raw.length > maximum) return invalidDraft();
  return JSON.parse(raw.replace(/^\ufeff/, '')) as unknown;
}

/** Retain bounded archival blocks, but NEVER mount their tasks, import their
 * capabilities, resume their queue or apply their checked/pend state. The live
 * capability history remains independently owned by golden-mic.history.v1.
 * Root keys are explicit: no spread of a stored object into live state/storage.
 */
function coreArchive(raw: string | null): Record<string, unknown> {
  if (raw === null) return {};
  const value = parseJson(raw);
  if (!isObject(value)) return invalidDraft();
  const archive: Record<string, unknown> = {};
  for (const key of ['tasks', 'checked', 'pend'] as const) {
    const block = value[key];
    if (block === undefined) continue;
    if (!isObject(block) || Object.keys(block).length > (key === 'checked' ? 10_000 : 1000)) return invalidDraft();
    // Parsed JSON is inert. Preserve the WHOLE known archival block, including
    // old receipt metadata; do not project it into a lossy new history schema.
    archive[key] = block;
    if (key === 'tasks') for (const item of Object.values(block)) {
      if (!isObject(item)) return invalidDraft();
      if (item.status === 'held') item.status = 'queued';
    }
  }
  if (value.history !== undefined) {
    if (!Array.isArray(value.history) || value.history.length > 1000) return invalidDraft();
    archive.history = value.history;
  }
  for (const key of ['quotaUsed', 'quotaResetAt'] as const) {
    if (value[key] !== undefined) archive[key] = draftNumber(value[key]);
  }
  if (value.bigFont !== undefined) {
    if (typeof value.bigFont !== 'boolean') return invalidDraft();
    archive.bigFont = value.bigFont;
  }
  return archive;
}

/** Update only the managed draft slot, reading the latest archival siblings.
 * Even explicit replacement cannot erase unreadable history to make space.
 */
export function writeDraftCache(value: Draft | null, savedAt: number | null): void {
  v2Persistence.writeDraft(value === null ? null : validateDraft(value), savedAt);
}

type DraftCache = { value: Draft | null; problem: string; blocked: boolean; savedAt: number | null; migrate: boolean; legacyFiles: number };
export function readDraftCache(): DraftCache {
  const empty: DraftCache = { value: null, problem: '', blocked: false, savedAt: null, migrate: false, legacyFiles: 0 };
  let raw: string | null; let legacy = false; let migrated = false;
  try {
    const managed = v2Persistence.readDraft();
    raw = managed ? JSON.stringify(managed) : localStorage.getItem(DRAFT_KEY);
    migrated = managed === null && raw !== null;
    if (raw === null) { legacy = true; raw = localStorage.getItem(LEGACY_DRAFT_KEY); }
  } catch { return { ...empty, problem: '无法读取浏览器草稿缓存。本页仍可创作；内容暂存内存，刷新会丢失。', blocked: true }; }
  if (raw === null) return empty;
  try {
    let wrapper = parseJson(raw, legacy ? MAX_DRAFT_LENGTH : MAX_CORE_LENGTH);
    if (!isObject(wrapper)) return invalidDraft();
    if (!legacy && wrapper.draftVersion !== undefined && wrapper.draftVersion !== 3) return invalidDraft();
    if (!legacy && !Object.prototype.hasOwnProperty.call(wrapper, 'draft')) {
      // An old history-only envelope is not a draft tombstone. Its archival
      // blocks remain in gm-core while the independent v2 draft can migrate.
      raw = localStorage.getItem(LEGACY_DRAFT_KEY);
      if (raw === null) return empty;
      legacy = true; wrapper = parseJson(raw, MAX_DRAFT_LENGTH);
      if (!isObject(wrapper)) return invalidDraft();
    }
    // A gm-core null is an intentional tombstone. Never resurrect an older v2
    // draft after submission/new-blank, and never overwrite either key on mount.
    const value = legacy && !Object.prototype.hasOwnProperty.call(wrapper, 'draft') ? wrapper : wrapper.draft;
    if (value == null) return empty;
    if (!isObject(value)) return invalidDraft();
    const modern = ['preferences', 'uploadTokens', 'uploadIds', 'uploadBindings', 'sentenceKinds', 'sentences', 'ownVoice'].some(key => Object.prototype.hasOwnProperty.call(value, key));
    const prototype = !modern && ('prefs' in value || 'typeMarks' in value || 'speakerNames' in value
      || 'elemMarks' in value || 'sentMarks' in value || Array.isArray(value.files) && value.files.some(file => isObject(file) && !('size' in file)));
    if (prototype) {
      // Use the existing prototype migration, not a guessed File/byte identity.
      const restored = restoreLegacyCore(JSON.stringify(legacy ? { tasks: {}, history: [], draft: value } : wrapper));
      return { ...empty, value: restored?.draft ? validateDraft(restored.draft) : null, migrate: true, legacyFiles: restored?.legacyFileNames.length ?? 0 };
    }
    const savedAt = !legacy && typeof wrapper.draftSavedAt === 'number' && Number.isSafeInteger(wrapper.draftSavedAt)
      && wrapper.draftSavedAt > 0 && wrapper.draftSavedAt <= Date.now() ? wrapper.draftSavedAt : null;
    const restored = validateDraft(value);
    if ((legacy || migrated) && !restored.draftTaskId) restored.legacyRestore = true;
    return { ...empty, value: restored, savedAt, migrate: legacy || migrated };
  } catch {
    return { ...empty, problem: '浏览器草稿缓存损坏或格式不受支持，原缓存及上传凭据未覆盖。可继续写稿；明确保存只替换草稿，不会清空历史。', blocked: true };
  }
}
const hasDraftContent = (draft: Draft | null) => !!draft && (!!draft.script.trim() || !!draft.files.length || !!draft.ownVoice
  || draft.voice === 'self' || draft.mode !== undefined && draft.mode !== 'voiceover' || !!draft.speakers?.length
  || JSON.stringify(draft.preferences) !== JSON.stringify(defaultPreferences(draft.mode)));
const restoredDraftNote = (draft: Draft | null) => draft
  ? `稿件、模式和素材清单已保留。${draft.uploadBindings?.length ? '已完成上传的素材核对通过后无需重选；未传完的文件仍需重选。' : '尚未上传的素材需重新选择。'}${draft.voice === 'self' || draft.ownVoice ? '整篇配音录音未存入浏览器，仍需重选或重录。' : ''}`
  : '草稿保存在此浏览器，包含稿件、模式、素材清单和上传凭据，不保存媒体文件。';

/** Keep ordinary Tab inside our open modal instead of visiting browser chrome.
 * Recompute each key: busy/uncertain states change the available controls. */
function containDialogTab(event: React.KeyboardEvent<HTMLDialogElement>) {
  const dialog = event.currentTarget;
  // macOS Safari only tabs onto buttons/links with Option+Tab; elsewhere Alt+Tab belongs to the OS.
  const optionTab = event.altKey && typeof navigator !== 'undefined' && /^(Mac|iPhone|iPad)/.test(navigator.platform);
  if (event.key !== 'Tab' || event.defaultPrevented || event.ctrlKey || (event.altKey && !optionTab) || event.metaKey
      || !dialog.open || !dialog.matches(':modal')) return;
  const available = Array.from(dialog.querySelectorAll<HTMLElement>('input, button, select, textarea, a[href], [tabindex]'))
    .filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && !element.closest('[inert]')
      && element.checkVisibility({ visibilityProperty: true }) && element.getClientRects().length > 0);
  const stops = available.filter(element => {
    const radio = element as HTMLInputElement;
    if (radio.type !== 'radio' || !radio.name) return true;
    const group = available.filter(candidate => {
      const other = candidate as HTMLInputElement;
      return other.type === 'radio' && other.name === radio.name && other.form === radio.form;
    });
    return element === (group.find(candidate => (candidate as HTMLInputElement).checked) ?? group[0]);
  });
  const index = stops.indexOf(dialog.ownerDocument.activeElement as HTMLElement);
  if (!stops.length || index < 0 || (event.shiftKey ? index === 0 : index === stops.length - 1)) {
    event.preventDefault();
    (event.shiftKey ? stops[stops.length - 1] ?? dialog : stops[0] ?? dialog).focus({ preventScroll: true });
  }
}

/** Native modal: inert background, focus containment, Escape, no repeat on failure. */
function ConfirmDialog({ spec, onClose, onError }: { spec: DialogSpec; onClose: () => void; onError: (text: string) => void }) {
  const id = useId(); const node = useRef<HTMLDialogElement>(null); const lock = useRef(false); const alive = useRef(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  useEffect(() => {
    alive.current = true;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    node.current?.showModal();
    return () => {
      alive.current = false; node.current?.close();
      if (previous?.isConnected) previous.focus(); else document.getElementById('main-content')?.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => { if (busy) node.current?.focus({ preventScroll: true }); }, [busy]);
  return <dialog ref={node} tabIndex={-1} onKeyDown={event => containDialogTab(event)} className="shell-dialog" aria-labelledby={`${id}-title`} aria-describedby={`${id}-text`}
    onCancel={event => { event.preventDefault(); if (!lock.current) onClose(); }}>
    <h2 id={`${id}-title`}>{spec.title}</h2><p id={`${id}-text`}>{spec.text}</p>
    {error && <p className="shell-error" role="alert">{error} 请返回核对真实状态；不会自动重复操作。</p>}
    <div className="shell-actions"><button type="button" autoFocus disabled={busy} onClick={onClose}>返回</button>
      <button type="button" className={spec.danger ? 'shell-danger' : 'shell-primary'} disabled={busy || !!error} onClick={() => {
        if (lock.current) return;
        lock.current = true; setBusy(true);
        void spec.action().then(() => { if (alive.current) onClose(); }).catch(failure => {
          if (alive.current) { const text = errorText(failure); setError(text); onError(text); }
        }).finally(() => { lock.current = false; if (alive.current) setBusy(false); });
      }}>{busy ? '正在等待服务器确认…' : spec.confirm}</button></div>
  </dialog>;
}

/** Native, focus-contained metadata editor. A failed write locks submission
 * until an explicit read; closing or Escape never retries a mutation. */
function RenameDialog({ spec, onClose }: { spec: RenameSpec; onClose: () => void }) {
  const id = useId(); const node = useRef<HTMLDialogElement>(null); const lock = useRef(false); const alive = useRef(false);
  const [baseline, setBaseline] = useState(spec.baseline); const [name, setName] = useState(spec.baseline.title);
  const [busy, setBusy] = useState(false); const [uncertain, setUncertain] = useState(false); const [message, setMessage] = useState('');
  useEffect(() => {
    alive.current = true; const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    node.current?.showModal();
    return () => { alive.current = false; node.current?.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  const run = async (readOnly: boolean) => {
    if (lock.current || !readOnly && (uncertain || !validMetadataTitle(name))) return;
    lock.current = true; setBusy(true);
    try {
      if (readOnly) {
        const next = await spec.read();
        if (alive.current) { setBaseline(next); setName(next.title); setUncertain(false); setMessage('已读取服务器名称；若仍需修改，请重新输入并明确保存。'); }
      } else { await spec.save(baseline, name); if (alive.current) onClose(); }
    } catch (failure) { if (alive.current) { setUncertain(true); setMessage(`${errorText(failure)} 不会自动重复保存，请只读核对名称。`); } }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  };
  useEffect(() => { if (busy) node.current?.focus({ preventScroll: true }); }, [busy]);
  return <dialog ref={node} tabIndex={-1} onKeyDown={event => containDialogTab(event)} className="shell-dialog shell-rename" aria-labelledby={`${id}-title`} aria-describedby={`${id}-help`}
    onCancel={event => { event.preventDefault(); if (!lock.current) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); void run(false); }}>
      <h2 id={`${id}-title`}>重命名作品</h2><p id={`${id}-help`}>只修改作品列表和结果页的名称，不改变成片中已烧录的新闻标题、字幕或保留期限。</p>
      <label htmlFor={`${id}-name`}>作品名称（1–40 个字符）</label>
      <input id={`${id}-name`} autoFocus value={name} disabled={busy || uncertain} onChange={event => setName(event.target.value)} aria-describedby={`${id}-count`} />
      <small id={`${id}-count`}>{Array.from(name).length} / 40；不能只有空白或含控制字符。</small>
      {message && <p role="status">{message}</p>}
      <div className="shell-actions"><button type="button" disabled={busy} onClick={onClose}>返回</button>
        {uncertain && <button type="button" disabled={busy} onClick={() => void run(true)}>只读核对名称</button>}
        <button className="shell-primary" type="submit" disabled={busy || uncertain || !validMetadataTitle(name) || name === baseline.title}>{busy ? '正在等待服务器确认…' : '保存名称'}</button></div>
    </form>
  </dialog>;
}

function DuplicateDialog({ onClose, onChoose }: { onClose: () => void; onChoose: (mode?: ProductionMode) => void }) {
  const id = useId(); const node = useRef<HTMLDialogElement>(null); const [choice, setChoice] = useState<ProductionMode | 'keep'>('keep');
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null; node.current?.showModal();
    return () => { node.current?.close(); if (previous?.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={node} tabIndex={-1} onKeyDown={event => containDialogTab(event)} className="shell-dialog" aria-labelledby={`${id}-title`} onCancel={event => { event.preventDefault(); onClose(); }}>
    <h2 id={`${id}-title`}>复制一份</h2><p>保持当前方式会复制已保存成片；改为另一种方式会复制独立草稿，返回第一步确认，不会开始制作。原作品和待修改内容不变。</p>
    <fieldset className="shell-duplicate-choices"><legend>副本的制作方式</legend>
      <label><input type="radio" name={id} autoFocus checked={choice === 'keep'} onChange={() => setChoice('keep')} />保持当前方式</label>
      {(['voiceover', 'mixed', 'original'] as const).map((mode, index) => <label key={mode}><input type="radio" name={id} checked={choice === mode} onChange={() => setChoice(mode)} />改为 {['A', 'B', 'C'][index]} · {MODE_LABELS[mode]}</label>)}
    </fieldset><div className="shell-actions"><button type="button" onClick={onClose}>返回</button><button type="button" className="shell-primary" onClick={() => onChoose(choice === 'keep' ? undefined : choice)}>继续</button></div>
  </dialog>;
}

export default function Workspace() {
  const [initial] = useState(() => {
    let browser: HistoryWork[] = []; let historyProblem = ''; let font: 16 | 20 = 16; let fontProblem = '';
    let preferences: ShellPreferences = { introDismissed: false, bigFont: false, histSort: 'time' };
    try { browser = readHistory(); } catch { historyProblem = '浏览器历史无法读取，未改动原记录。新回执会先留在本标签页。'; }
    try { preferences = v2Persistence.readPreferences(); font = preferences.bigFont ? 20 : 16; } catch { fontProblem = '界面偏好无法读取，原缓存未覆盖；调整暂留在本标签页。'; }
    return { browser, historyProblem, font, fontProblem, preferences, draft: readDraftCache(), route: readRoute(location.hash, location.pathname) };
  });
  const [font, setFont] = useState<16 | 20>(initial.font); const [fontProblem, setFontProblem] = useState(initial.fontProblem);
  const [page, setPageState] = useState<Page>(initial.route.page === 'create' ? 'create' : initial.route.page === 'sample' ? 'result' : 'gone'); const pageRef = useRef(page);
  const [sample, setSample] = useState(initial.route.page === 'sample');
  const [introDismissed, setIntroDismissed] = useState(initial.preferences.introDismissed);
  const [wizardStep, setWizardStep] = useState(initial.draft.value?.step ?? 1);
  const showPage = useCallback((next: Page) => { pageRef.current = next; setPageState(next); }, []);
  const generation = useRef(1);
  const [intent, setIntentState] = useState<OpenIntent | null>(initial.route.page === 'work'
    ? { id: initial.route.id, target: 'result', serial: 1 } : null);
  const intentRef = useRef(intent);
  const setIntent = useCallback((next: OpenIntent | null) => { intentRef.current = next; setIntentState(next); }, []);
  const [selection, setSelectionState] = useState<Selection | null>(null); const selectedRef = useRef<Selection | null>(null);
  const select = useCallback((next: Selection | null) => { selectedRef.current = next; setSelectionState(next); }, []);
  const [task, setTask] = useState<TaskStatusResponse | null>(null);
  const [error, setError] = useState('invalid' in initial.route && initial.route.invalid ? '作品链接格式不正确，请从左侧作品列表重新打开。' : '');
  const [notice, setNotice] = useState(''); const [missing, setMissing] = useState<string | null>(null);
  // The accepted work's real status has now been read: the "verifying" banner has done its job.
  useEffect(() => { if (task?.status) setNotice(current => current === RECEIVED_NOTICE ? '' : current); }, [task?.task_id, task?.status]);
  const [missingReason, setMissingReason] = useState<GoneReason>('inaccessible');
  const [rename, setRename] = useState<RenameSpec | null>(null); const [duplicateChoice, setDuplicateChoice] = useState(false);
  const [limits, setLimits] = useState<PublicLimits | null>(null); const [limitsError, setLimitsError] = useState('');
  const [config, setConfig] = useState<WorkspaceConfig | null>(null); const configRef = useRef(config);
  const [configResolved, setConfigResolved] = useState(false); const [configError, setConfigError] = useState('');
  const [healthReady, setHealthReady] = useState(false); const [healthError, setHealthError] = useState('');
  const [healthBusy, setHealthBusy] = useState(true); const [healthRetry, setHealthRetry] = useState(0);
  const [browserRows, setBrowserRowsState] = useState(initial.browser); const browserRef = useRef(browserRows);
  const [localRows, setLocalRowsState] = useState<HistoryWork[]>([]); const localRef = useRef(localRows);
  const setBrowserRows = useCallback((rows: HistoryWork[]) => { browserRef.current = rows; setBrowserRowsState(rows); }, []);
  const setLocalRows = useCallback((rows: HistoryWork[]) => { localRef.current = rows; setLocalRowsState(rows); }, []);
  const [historyProblem, setHistoryProblem] = useState(initial.historyProblem); const [historyError, setHistoryError] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false); const [historyRetry, setHistoryRetry] = useState(0);
  const [historyCheckedAt, setHistoryCheckedAt] = useState<number | null>(null);
  const pendingHistory = useRef(new Map<string, HistoryWork>()); const [pendingHistoryCount, setPendingHistoryCount] = useState(0);
  const pendingRemoval = useRef(new Set<string>()); const [pendingRemovalCount, setPendingRemovalCount] = useState(0);
  const forgotten = useRef(new Set<string>()); const deleted = useRef(new Set<string>());
  const observations = useRef(new Map<string, { serial: number; fields: Partial<HistoryWork> }>()); const observationSerial = useRef(0);
  const [sort, setSort] = useState<HistorySort>(initial.preferences.histSort);
  const draftRef = useRef<Draft | null>(initial.draft.value); const draftSaved = useRef(initial.draft.value ? JSON.stringify(initial.draft.value) : '');
  const draftDirty = useRef(false); const draftBlocked = useRef(initial.draft.blocked); const draftSuppressed = useRef(false);
  const draftMigration = useRef(initial.draft.migrate);
  const [draftPending, setDraftPending] = useState(false); const [draftSavedAt, setDraftSavedAt] = useState<number | null>(initial.draft.savedAt);
  const [draftSavedAutomatically, setDraftSavedAutomatically] = useState(false);
  const draftTimer = useRef<ReturnType<typeof setTimeout>>(); const [draftProblem, setDraftProblem] = useState(initial.draft.problem);
  const [draftStatus, setDraftStatus] = useState(restoredDraftNote(initial.draft.value)
    + (initial.draft.legacyFiles ? `旧版 ${initial.draft.legacyFiles} 个文件只有名称摘要，无法据此确认上传；请重新选择。` : ''));
  const [draftNeedsClear, setDraftNeedsClear] = useState(false);
  const [wizardSeed, setWizardSeed] = useState<Draft | null>(initial.draft.value);
  const [wizardFiles, setWizardFiles] = useState<File[] | undefined>(undefined);
  const [wizardMounted, setWizardMounted] = useState(initial.route.page === 'create'); const wizardActive = useRef(wizardMounted);
  const [wizardKey, setWizardKey] = useState(0); const wizardGeneration = useRef(0); const firstDraftEmission = useRef(true);
  const [upload, setUpload] = useState<UploadProgress | null>(null); const [uploading, setUploading] = useState(false);
  const uploadController = useRef<AbortController | null>(null); const [uploadUncertain, setUploadUncertain] = useState(false);
  const [busy, setBusy] = useState(false); const actionLock = useRef(false); const [taskRetry, setTaskRetry] = useState(0);
  const [submissionRecovery] = useState(() => createSubmissionRecovery(validateDraft));
  const [recoveryProblem, setRecoveryProblem] = useState('');
  const [pendingRecoveryCount, setPendingRecoveryCount] = useState(0);
  const [dialog, setDialog] = useState<DialogSpec | null>(null); const [drawer, setDrawer] = useState('drawer' in initial.route && initial.route.drawer === true);
  const drawerRef = useRef<HTMLDialogElement>(null); const [now, setNow] = useState(Date.now());
  const alive = useRef(true); const openingController = useRef<AbortController | null>(null);
  const selectedReadController = useRef<AbortController | null>(null); const historyController = useRef<AbortController | null>(null);
  const historyGeneration = useRef(0); const activeHash = useRef(location.pathname + location.hash);
  const serviceReady = healthReady && !!limits && !limitsError && configResolved && !!config;

  useEffect(() => {
    alive.current = true;
    return () => {
      // Abort the old effect instance without invalidating the initial route's
      // serial: StrictMode deliberately runs this cleanup once during mounting.
      alive.current = false; clearTimeout(draftTimer.current);
      openingController.current?.abort(); selectedReadController.current?.abort(); selectedRef.current?.scope.abort();
      uploadController.current?.abort(); historyController.current?.abort();
    };
  }, []);
  useEffect(() => {
    const previous = document.documentElement.style.fontSize;
    document.documentElement.style.fontSize = font === 20 ? '20px' : '';
    return () => { document.documentElement.style.fontSize = previous; };
  }, [font]);
  function toggleFont() {
    const next = font === 20 ? 16 : 20; setFont(next);
    try { v2Persistence.writePreference('bigFont', next === 20); setFontProblem(''); }
    catch { setFontProblem('字号已调整，但浏览器未保存偏好；刷新后可能恢复默认字号。'); }
  }

  // Public, non-generating reads are independent: unavailable generation must
  // never gate an existing work's status, report, media or local editor.
  useEffect(() => {
    const controller = new AbortController(); const current = () => alive.current && !controller.signal.aborted;
    setHealthBusy(true); setHealthReady(false); setHealthError(''); setLimitsError(''); setConfigError('');
    try { assertSameOriginConfiguration(); }
    catch (failure) {
      setHealthError(errorText(failure)); configRef.current = null; setConfig(null); setConfigResolved(true); setHealthBusy(false);
      return () => controller.abort();
    }
    const limitsRead = getPublicLimits(controller.signal).then(value => { if (current()) setLimits(value); })
      .catch(() => { if (current()) setLimitsError('上传限制读取失败；新制作暂停，已有作品仍可打开。'); });
    const configRead = getWorkspaceConfig(controller.signal).then(value => {
      if (!current()) return;
      configRef.current = value; setConfig(value); setConfigResolved(true);
    }).catch(() => {
      if (!current()) return;
      configRef.current = null; setConfig(null); setConfigResolved(true);
      setConfigError('工作区配置读取失败；暂仅显示本浏览器的历史记录，不读取服务器全部任务。');
    });
    const availabilityRead = publicRequest<unknown>('/api/config/availability', controller.signal).then(value => {
      if (!current()) return;
      const ready = isObject(value) && value.status === 'ready'; setHealthReady(ready);
      if (!ready) setHealthError('制作服务尚未就绪。可以写稿和查看已有作品，不会自动发起制作。');
    }).catch(() => { if (current()) setHealthError('制作服务连接失败。已有作品仍可尝试读取；不会自动重试制作。'); });
    void Promise.all([limitsRead, configRead, availabilityRead]).finally(() => { if (current()) setHealthBusy(false); });
    return () => controller.abort();
  }, [healthRetry]);

  const reloadBrowserHistory = useCallback(() => {
    try {
      const rows = new Map(readHistory().filter(item => !forgotten.current.has(item.taskId)).map(item => [item.taskId, item]));
      pendingHistory.current.forEach((item, id) => { if (!forgotten.current.has(id)) rows.set(id, item); });
      setBrowserRows([...rows.values()]); setHistoryProblem('');
    } catch { setHistoryProblem('浏览器历史读取失败；本标签页已持有的记录和访问凭据仍保留，未覆盖缓存。'); }
  }, [setBrowserRows]);
  const refreshHistory = useCallback(() => {
    reloadBrowserHistory(); historyGeneration.current++; historyController.current?.abort(); setHistoryRetry(value => value + 1);
  }, [reloadBrowserHistory]);
  const retainWork = useCallback((item: HistoryWork) => {
    // Memory FIRST. A storage exception after a committed POST must not turn
    // success into a failed submission, discard its capability, or resubmit it.
    forgotten.current.delete(item.taskId);
    setBrowserRows([item, ...browserRef.current.filter(row => row.taskId !== item.taskId)]);
    pendingHistory.current.set(item.taskId, item);
    try { rememberWork(item); pendingHistory.current.delete(item.taskId); }
    catch { /* The visible pending-receipt warning is independent of page errors. */ }
    setPendingHistoryCount(pendingHistory.current.size);
  }, [setBrowserRows]);
  function retryHistorySave() {
    for (const [id, item] of pendingHistory.current) {
      try { rememberWork(item); pendingHistory.current.delete(id); }
      catch { break; }
    }
    setPendingHistoryCount(pendingHistory.current.size);
    if (!pendingHistory.current.size) { setHistoryProblem(''); setNotice('已确认保存浏览器历史；没有重新提交任何制作任务。'); }
  }
  function retryHistoryRemoval() {
    for (const id of pendingRemoval.current) {
      try {
        forgetHistory(id);
        if (!submissionRecovery.forget(id)) break;
        pendingRemoval.current.delete(id);
      }
      catch { break; }
    }
    setPendingRemovalCount(pendingRemoval.current.size);
    if (!pendingRemoval.current.size) setNotice('浏览器旧记录已移除，没有再次删除服务器作品。');
  }
  const observeTask = useCallback((next: TaskStatusResponse) => {
    const mode = isProductionMode(next.mode) ? next.mode : undefined;
    const fields: Partial<HistoryWork> = { status: next.status, revision: next.revision,
      ...(mode === undefined ? {} : { mode }), ...(next.title === undefined ? {} : { title: next.title }),
      ...(next.metadata_revision === undefined ? {} : { metadata_revision: next.metadata_revision }),
      ...(next.version_count === undefined ? {} : { version_count: next.version_count }),
      ...(next.expires_at === undefined ? {} : { expires_at: next.expires_at }) };
    observations.current.set(next.task_id, { serial: ++observationSerial.current, fields });
    const cached = browserRef.current.find(item => item.taskId === next.task_id);
    if (cached?.accessToken && JSON.stringify(cached) !== JSON.stringify({ ...cached, ...fields })) {
      retainWork({ ...cached, ...fields });
    }
    if (localRef.current.some(item => item.taskId === next.task_id)) {
      setLocalRows(localRef.current.map(item => item.taskId === next.task_id ? { ...item, ...fields } : item));
    }
  }, [retainWork, setLocalRows]);
  useEffect(() => {
    const changed = (event: StorageEvent) => { if (event.key === HISTORY_KEY || event.key === null) reloadBrowserHistory(); };
    window.addEventListener('storage', changed); return () => window.removeEventListener('storage', changed);
  }, [reloadBrowserHistory]);
  useEffect(() => {
    const serial = ++historyGeneration.current; const controller = new AbortController(); historyController.current = controller;
    const current = () => alive.current && !controller.signal.aborted && serial === historyGeneration.current;
    if (!config?.local_history) {
      setLocalRows([]); setHistoryLoading(false); setHistoryError(''); setHistoryCheckedAt(null);
      return () => controller.abort();
    }
    setHistoryLoading(true); setHistoryError(''); const startedObservation = observationSerial.current;
    void getLocalHistory(controller.signal).then(rows => {
      if (!current()) return;
      setLocalRows(rows.filter(item => !deleted.current.has(item.taskId)).map(item => {
        const observed = observations.current.get(item.taskId);
        return { ...item, accessToken: undefined,
          ...(observed && observed.serial > startedObservation ? observed.fields : {}) };
      }));
      setHistoryCheckedAt(Date.now());
    }).catch(failure => { if (current()) setHistoryError(`本机历史更新失败，保留上次列表：${errorText(failure)}`); })
      .finally(() => { if (current()) setHistoryLoading(false); });
    return () => { controller.abort(); if (historyController.current === controller) historyController.current = null; };
  }, [config?.local_history, historyRetry, setLocalRows]);
  const works = useMemo(() => {
    const merged = new Map(browserRows.filter(item => !deleted.current.has(item.taskId)).map(item => [item.taskId, item]));
    // Server snapshots override cached title/status/revision, but never import a
    // credential. Browser capabilities are kept separately for durable receipts.
    if (config?.local_history) localRows.forEach(item => {
      if (!deleted.current.has(item.taskId)) merged.set(item.taskId, item.status === 'gone' ? item : { ...merged.get(item.taskId), ...item,
        mode: item.mode ?? merged.get(item.taskId)?.mode, accessToken: undefined });
    });
    return [...merged.values()];
  }, [browserRows, localRows, config?.local_history]);

  // Only the mounted selection may relay its existing authorized checks read.
  // This identity is deliberately not HistoryWork, a cache, or export authority.
  const checksRow = works.find(item => item.taskId === selection?.id);
  const checksToken = browserRows.find(item => item.taskId === selection?.id)?.accessToken;
  const checksScope = useMemo(() => selection && page === 'result' && !sample && !intent
    && task?.task_id === selection.id && task.status === 'done'
    && checksRow?.status === 'done' && checksRow.revision === task.revision
    && checksToken === selection.token && !selection.scope.signal.aborted
    ? { selection, serial: generation.current, revision: task.revision, expires: checksRow.expires_at } : null,
  [selection, page, sample, intent, task?.task_id, task?.status, task?.revision,
    checksRow?.status, checksRow?.revision, checksRow?.expires_at, checksToken]);
  const checksScopeRef = useRef(checksScope); checksScopeRef.current = checksScope;
  const [historyChecks, setHistoryChecks] = useState<{ scope: NonNullable<typeof checksScope>; summary: HistoryChecksSummary } | null>(null);
  const onChecksChange = useMemo(() => {
    const scope = checksScope;
    return (value: WorkbenchChecks | null) => {
      if (!scope || !alive.current || checksScopeRef.current !== scope
          || selectedRef.current !== scope.selection || generation.current !== scope.serial
          || scope.selection.scope.signal.aborted || pageRef.current !== 'result') return;
      const summary = scope.expires && Date.parse(scope.expires) <= Date.now()
        ? null : parseHistoryChecks(value, scope.revision);
      setHistoryChecks(summary ? { scope, summary } : null);
    };
  }, [checksScope]);
  useEffect(() => {
    checksScopeRef.current = checksScope;
    // Clear rather than retain old selection/version observations for later visits.
    setHistoryChecks(null);
    const scope = checksScope;
    const clear = () => {
      if (checksScopeRef.current === scope) { checksScopeRef.current = null; setHistoryChecks(null); }
    };
    scope?.selection.scope.signal.addEventListener('abort', clear, { once: true });
    const remaining = scope?.expires ? Date.parse(scope.expires) - Date.now() : null;
    const timer = remaining === null ? undefined : setTimeout(clear, Math.max(0, Math.min(remaining, 2_147_483_647)));
    return () => { clearTimeout(timer); scope?.selection.scope.signal.removeEventListener('abort', clear); clear(); };
  }, [checksScope]);
  const selectedChecks = historyChecks?.scope === checksScope && checksScopeRef.current === checksScope
    && checksScope && !checksScope.selection.scope.signal.aborted
    && (!checksScope.expires || Date.parse(checksScope.expires) > Date.now()) ? historyChecks.summary : null;

  const resultMutation = useRef<{ selection: Selection; notify: (() => void) | null } | null>(null);
  const onUnavailable = useMemo(() => {
    const target = selection, serial = generation.current;
    const notify = (failure: unknown) => {
      const reason = goneReason(failure);
      if (reason === 'network' || !target || !alive.current || selectedRef.current !== target
          || generation.current !== serial || target.scope.signal.aborted || intentRef.current
          || pageRef.current !== 'result' && pageRef.current !== 'processing') return;
      if (resultMutation.current?.selection === target) {
        resultMutation.current.notify = () => notify(failure); return;
      }
      // Clear read projections, not capabilities or pending edits. In particular,
      // do not abort the shared request scope while a write still owns a receipt.
      checksScopeRef.current = null; setHistoryChecks(null); setTask(null);
      setMissing(target.id); setMissingReason(reason); setError(''); showPage('gone');
    };
    return notify;
  }, [selection, showPage]);
  const onMutationStateChange = useMemo(() => {
    const target = selection, serial = generation.current;
    return (active: boolean) => {
      if (!target || selectedRef.current !== target || generation.current !== serial || target.scope.signal.aborted) return;
      if (active) resultMutation.current = { selection: target, notify: null };
      else if (resultMutation.current?.selection === target) {
        const notify = resultMutation.current.notify; resultMutation.current = null; notify?.();
      }
    };
  }, [selection]);

  const saveDraft = useCallback((force = false): boolean => {
    clearTimeout(draftTimer.current);
    if (draftSuppressed.current || !draftRef.current) return false;
    if (draftBlocked.current && !force) { setDraftStatus('草稿在本标签页内，原缓存尚未替换。请明确重试保存。'); return false; }
    try {
      const safe = validateDraft(draftRef.current), encoded = JSON.stringify(safe);
      if (encoded !== draftSaved.current || draftMigration.current || draftBlocked.current) {
        const savedAt = Date.now();
        writeDraftCache(safe, savedAt);
        // A timestamp means an actual successful write AND readback, never a
        // render time or a failed/quota-exceeded autosave attempt.
        setDraftSavedAt(savedAt); setDraftSavedAutomatically(!force); draftMigration.current = false;
      }
      draftSaved.current = encoded; draftDirty.current = false; draftBlocked.current = false; setDraftPending(false);
      setDraftProblem(''); setDraftNeedsClear(false); setDraftStatus(`已保存到此浏览器 · ${restoredDraftNote(safe)}`);
      return true;
    } catch {
      draftDirty.current = true; setDraftPending(true);
      setDraftProblem('草稿未能安全保存到浏览器（格式、容量或存储权限限制）。没有丢弃旧历史或上传凭据；稿件仍在本标签页内，请勿刷新，可重试保存或先备份文字。');
      setDraftStatus('尚未保存 · 当前内容仅在本标签页内'); return false;
    }
  }, []);
  function receiveDraft(value: Draft, serial: number) {
    if (serial !== wizardGeneration.current || !wizardActive.current || draftSuppressed.current || pageRef.current !== 'create' || intentRef.current) return;
    const previous = draftRef.current;
    let next: Draft;
    try { next = validateDraft(value); }
    catch {
      draftRef.current = value; draftDirty.current = true; setDraftPending(true); clearTimeout(draftTimer.current);
      setDraftProblem('草稿或上传凭据尚未形成有效绑定，原缓存未覆盖。请核对素材上传状态；当前文字仍留在本页。'); return;
    }
    draftRef.current = next; setWizardStep(next.step);
    // Mount-time normalization (including restored step 3 -> 2) is not a user
    // edit. In particular, an initial work/show link never mounts this writer.
    if (firstDraftEmission.current) { firstDraftEmission.current = false; return; }
    if (JSON.stringify(previous) === JSON.stringify(next)) return;
    draftDirty.current = true; setDraftPending(true); setDraftStatus('草稿有修改 · 等待保存到此浏览器…');
    clearTimeout(draftTimer.current); draftTimer.current = setTimeout(() => { saveDraft(); }, DRAFT_SAVE_DELAY_MS);
  }
  function retryDraftSave() {
    if (draftBlocked.current && !window.confirm('原草稿未能读取。用本页稿件、模式及上传凭据替换草稿部分？历史记录不会清空；若历史也无法安全读取，本次保存将停止。不会上传文件或开始制作。')) return;
    if (!draftRef.current) { setNotice('尚无可保存的稿件，请先进入创作页。'); return; }
    saveDraft(true);
  }
  function clearDraftCache() {
    try {
      writeDraftCache(null, null);
      draftBlocked.current = false; draftMigration.current = false; setDraftPending(false); setDraftSavedAt(null); setDraftProblem(''); setDraftNeedsClear(false);
    } catch {
      setDraftNeedsClear(true);
      setDraftProblem('旧草稿缓存未能清除。本标签页不会恢复或重新保存已提交稿件；刷新后旧缓存可能再次出现，请勿重复制作。');
    }
  }
  function unmountWizard() { wizardActive.current = false; setWizardMounted(false); clearTimeout(draftTimer.current); }
  function mountWizard(seed: Draft | null, files?: File[]) {
    // Every genuine remount gets its own callback generation, including the
    // first blank creator opened after a successfully submitted draft.
    wizardGeneration.current++; setWizardKey(wizardGeneration.current); setWizardFiles(files);
    firstDraftEmission.current = true; setWizardSeed(seed); setWizardStep(seed?.step ?? 1); wizardActive.current = true; setWizardMounted(true);
  }
  const cancelReads = useCallback(() => {
    generation.current++; openingController.current?.abort(); openingController.current = null;
    selectedReadController.current?.abort(); selectedReadController.current = null;
    selectedRef.current?.scope.abort(); setIntent(null); setRename(null); setDuplicateChoice(false);
  }, [setIntent]);
  function writeHash(hash: string, replace = false) {
    // Canonical links have no query/hash/token, including when arriving via an
    // old URL containing unrelated query parameters.
    const url = hash.startsWith('/') ? hash : '/';
    if (replace || location.pathname + location.hash === url) window.history.replaceState(null, '', url);
    else window.history.pushState(null, '', url);
    activeHash.current = url;
  }
  function canLeave(editorConfirmed = false, discardDraft = false): boolean {
    if (uploadController.current || actionLock.current) { setError('请等待当前操作完成，或先停止上传。服务器是否已接收需单独核对。'); return false; }
    if (intentRef.current) return true;
    if (!editorConfirmed && pageRef.current === 'result' && !sample
        && !window.confirm('切换页面？已确认保存的待修改内容会保留在此浏览器；未保存的文字、尚未上传的录音或文件可能丢失。进行中的保存或导出不等于已完成，请先核对状态。不会自动撤销已保存的修改。')) return false;
    if (!discardDraft && pageRef.current === 'create' && (hasDraftContent(draftRef.current) || draftDirty.current)
        && !window.confirm('离开创作页？稿件、模式与上传凭据会保留。已完成上传的素材核对后无需重选；未传完的文件和整篇录音仍需重选或重录。已启动的服务器处理不会自动取消。')) return false;
    return true;
  }
  function leaveCreator() { if (pageRef.current === 'create' && draftDirty.current && !draftSuppressed.current) saveDraft(); }
  function navigate(next: 'create' | 'gone', fromHash = false, confirmed = false) {
    if (!intentRef.current && pageRef.current === next) { setDrawer(false); if (fromHash) activeHash.current = location.pathname + location.hash; return; }
    if (!confirmed && !canLeave()) { if (fromHash) writeHash(activeHash.current, true); return; }
    leaveCreator(); cancelReads(); unmountWizard(); setSample(false); setUpload(null); setMissing(null); setError(''); setDrawer(false); setDialog(null);
    showPage(next);
    if (next === 'create') {
      // After a successful upload, only an explicit create navigation enables a
      // new draft writer. No old callback or rerender can resurrect that draft.
      draftSuppressed.current = false; mountWizard(draftRef.current);
      setDraftStatus(restoredDraftNote(draftRef.current));
    }
    if (fromHash) activeHash.current = location.pathname + location.hash; else writeHash('/');
  }
  function beginOpen(id: string, target: 'result' = 'result', options: { confirmed?: boolean; fromHash?: boolean; receipt?: HistoryWork } = {}) {
    if (!validId(id)) { setError('作品编号格式不正确。'); return; }
    if (!options.confirmed && !canLeave()) { if (options.fromHash) writeHash(activeHash.current, true); return; }
    leaveCreator(); cancelReads(); unmountWizard(); setSample(false); select(null); setTask(null); setUpload(null); setMissing(null); setError(''); setDrawer(false); setDialog(null);
    showPage('gone');
    const next: OpenIntent = { id, target, serial: generation.current, receipt: options.receipt }; setIntent(next);
    if (options.fromHash) activeHash.current = location.pathname + location.hash; else writeHash(hashFor(id));
  }
  useEffect(() => {
    if (!intent || !configResolved) return;
    const controller = new AbortController(); openingController.current = controller;
    const current = () => alive.current && !controller.signal.aborted && generation.current === intent.serial && intentRef.current?.serial === intent.serial;
    void (async () => {
      try {
        const cached = intent.receipt ?? browserRef.current.find(item => item.taskId === intent.id);
        const local = configRef.current?.local_history === true;
        // A v2 task requires its capability even on loopback. Preserve an
        // explicit receipt; a rejected token is NEVER retried tokenlessly.
        // Tokenless local reads remain available only for legacy records.
        const token = cached?.accessToken;
        if (!local && !token) throw new AppApiError('此浏览器没有该作品的访问凭据。请在创建作品的原浏览器打开；链接本身不包含凭据。', 403);
        const get = createAppRequest({ taskId: intent.id, token, signal: controller.signal });
        const next = parseTask(await get<unknown>(pathFor(intent.id)));
        if (next.task_id !== intent.id) throw new Error('服务器返回的作品编号不一致。');
        if (!current()) return;
        const scope = new AbortController();
        const title = next.title ?? localRef.current.find(item => item.taskId === intent.id)?.title ?? cached?.title ?? '新闻作品';
        select({ id: intent.id, token, title, mode: next.mode ?? cached?.mode, scope, request: createAppRequest({ taskId: intent.id, token, signal: scope.signal }) });
        setTask(next); observeTask(next); showPage(next.status === 'done' ? intent.target : 'processing');
        setIntent(null); writeHash(hashFor(intent.id), true);
      } catch (failure) {
        if (!current()) return;
        setError(errorText(failure));
        setMissing(intent.id); setMissingReason(goneReason(failure));
        setIntent(null); showPage('gone');
      } finally { if (openingController.current === controller) openingController.current = null; }
    })();
    return () => controller.abort();
  }, [intent, configResolved, observeTask, select, setIntent, showPage]);

  const routeHandler = useRef<(nativeHistory: boolean) => void>(() => undefined);
  routeHandler.current = nativeHistory => {
    if (location.hash === '#main-content') { writeHash(activeHash.current, true); document.getElementById('main-content')?.focus(); return; }
    if (location.pathname + location.hash === activeHash.current) return;
    const route = readRoute(location.hash, location.pathname);
    // The result editor already guards native popstate. Sidebar/hash edits still
    // use the conservative host guard; the sample has no mutable editor.
    const editorConfirmed = nativeHistory && pageRef.current === 'result';
    if (!canLeave(editorConfirmed)) { writeHash(activeHash.current, true); return; }
    if (route.page === 'work') beginOpen(route.id, 'result', { confirmed: true, fromHash: true });
    else if (route.page === 'sample') openSample(true);
    else {
      navigate(route.page, true, true);
      if (route.drawer && !window.matchMedia('(min-width: 901px)').matches) setDrawer(true);
      if (route.invalid) setError('链接格式不正确，请从作品历史重新打开作品。');
    }
  };
  useEffect(() => {
    // Observe popstate before the mounted workbench guard. If that guard cancels
    // propagation and goes forward, its corresponding hashchange must NOT reopen
    // the rejected destination behind it. Accepted popstate is handled once.
    const observed = new Set<string>();
    const capture = () => { if (observed.size > 16) observed.clear(); observed.add(location.href); };
    const pop = () => routeHandler.current(true);
    const hash = (event: HashChangeEvent) => { if (observed.delete(event.newURL)) return; routeHandler.current(false); };
    window.addEventListener('popstate', capture, true); window.addEventListener('popstate', pop); window.addEventListener('hashchange', hash);
    return () => { window.removeEventListener('popstate', capture, true); window.removeEventListener('popstate', pop); window.removeEventListener('hashchange', hash); };
  }, []);

  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (uploadController.current || actionLock.current || draftDirty.current || pendingHistory.current.size > 0 || submissionRecovery.pendingCount() > 0
          || pageRef.current === 'create' && hasDraftContent(draftRef.current)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', unload); return () => window.removeEventListener('beforeunload', unload);
  }, []);
  useEffect(() => {
    if (page !== 'processing') return;
    const timer = setInterval(() => { if (!document.hidden) setNow(Date.now()); }, 1000);
    return () => clearInterval(timer);
  }, [page]);
  useEffect(() => {
    if (!selection || page !== 'processing' || uploading || intent) return;
    const controller = new AbortController(); const serial = generation.current;
    let stopped = false; let terminal = false; let inFlight = false; let failures = 0; let timer: ReturnType<typeof setTimeout>;
    const current = () => !stopped && !controller.signal.aborted && alive.current && selectedRef.current === selection && serial === generation.current;
    const poll = async () => {
      if (!current() || inFlight || document.hidden || terminal) return;
      inFlight = true;
      try {
        const next = parseTask(await selection.request<unknown>(pathFor(selection.id), { signal: controller.signal }));
        if (next.task_id !== selection.id) throw new Error('任务编号不一致。');
        if (!current()) return;
        setTask(next); observeTask(next); setError(''); setMissing(null); failures = 0;
        terminal = ['done', 'failed', 'cancelled'].includes(next.status);
        if (next.status === 'done') showPage('result');
        if (terminal) refreshHistory();
      } catch (failure) {
        if (!current()) return;
        setError(errorText(failure)); failures++;
        if (failure instanceof AppApiError && [401, 403, 404, 410].includes(failure.status)) { setMissing(selection.id); setMissingReason(goneReason(failure)); terminal = true; showPage('gone'); }
      } finally {
        inFlight = false;
        if (current() && !terminal && !document.hidden) timer = setTimeout(poll, Math.min(10_000, 1000 * 2 ** failures));
      }
    };
    const visible = () => { clearTimeout(timer); if (!document.hidden) { setNow(Date.now()); void poll(); } };
    document.addEventListener('visibilitychange', visible); void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
  }, [selection, page, uploading, intent, taskRetry, observeTask, refreshHistory, showPage]);

  function refreshSelected() {
    const target = selectedRef.current;
    refreshHistory();
    if (!target || target.scope.signal.aborted) return;
    selectedReadController.current?.abort(); const controller = new AbortController(); selectedReadController.current = controller;
    const serial = generation.current;
    const notify = onUnavailable;
    const current = () => alive.current && !controller.signal.aborted && !target.scope.signal.aborted
      && selectedRef.current === target && serial === generation.current
      && (pageRef.current === 'result' || pageRef.current === 'processing');
    void target.request<unknown>(pathFor(target.id), { signal: controller.signal }).then(value => {
      const next = parseTask(value); if (next.task_id !== target.id) throw new Error('任务编号不一致。');
      if (!current()) return;
      setTask(next); observeTask(next); setError('');
      if (next.status !== 'done' && pageRef.current === 'result') showPage('processing');
    }).catch(failure => {
      if (!current()) return;
      if (goneReason(failure) !== 'network') notify(failure);
      else setError(`作品修改后状态尚未确认：${errorText(failure)}`);
    })
      .finally(() => { if (selectedReadController.current === controller) selectedReadController.current = null; });
  }
  function processingStarted() {
    selectedReadController.current?.abort(); setTask(null); setError(''); showPage('processing'); setTaskRetry(value => value + 1); refreshHistory();
  }
  async function editFailedDraft(step: 1 | 2 = 1, mode?: ProductionMode, options: { allowShotReuse?: boolean } = {}) {
    const reuse = options.allowShotReuse === true;
    const target = selectedRef.current;
    // A failed task on the processing page may also switch mode (e.g. fall back to AI voiceover).
    const sourcePage = mode === undefined || task?.status === 'failed' ? 'processing' : 'result';
    if (!target || actionLock.current || intentRef.current || pageRef.current !== sourcePage
        || target.scope.signal.aborted || !task || task.task_id !== target.id
        || task.status !== (sourcePage === 'processing' ? 'failed' : 'done') || mode !== undefined && (!isProductionMode(mode) || step !== 1)) return;
    const serial = generation.current;
    const isCurrent = () => alive.current && selectedRef.current === target && serial === generation.current
      && !target.scope.signal.aborted && !intentRef.current && pageRef.current === sourcePage;
    // A local/report reconstruction is not proof of original committed inputs.
    // The server alone decides narrow old-v2 compatibility; no legacy fallback.
    if (!target.token) {
      setError('缺少原任务的访问凭据，无法恢复。现有草稿保留；不会从报告或旧浏览器快照补造原稿。');
      return;
    }
    if (reuse && (task.errorKind !== 'shortage' || task.mode === 'original')) return;
    if (reuse && !window.confirm('允许画面重复出现吗？新草稿会记下这个选择：素材里互不相同的画面不够时，同一段素材会在不同时刻重复出现（不是定格补帧）。原作品不变，需要你确认后才会重新提交制作（可能产生费用）。')) return;
    if ((hasDraftContent(draftRef.current) || draftDirty.current || draftBlocked.current || draftNeedsClear)
        && !window.confirm('用这条任务提交时的原稿和素材清单替换当前创作草稿？另一份未提交草稿将被替换，服务器作品不变。')) return;
    if (!isCurrent()) return;
    try {
      await mutate(async () => {
        if (!isCurrent()) return;
        // Exactly one POST. A lost/invalid receipt is ambiguous, never a retry.
        const value = await target.request<unknown>(`${pathFor(target.id)}/recover-draft`, {
          method: 'POST', body: JSON.stringify({ step, ...(mode === undefined ? {} : { mode }), ...(reuse ? { allow_shot_reuse: true } : {}) }), signal: target.scope.signal,
        });
        if (!isObject(value) || value.task_id === target.id || value.status !== 'draft' || value.accepted !== false
            || !isObject(value.recovery) || value.recovery.source_task_id !== target.id
            || value.recovery.task_id !== value.task_id || value.step !== step || !Array.isArray(value.files)
            || !value.files.length || value.files.length > 20
            || mode !== undefined && value.mode !== mode || reuse && value.shot_reuse_accepted !== true) throw new Error('恢复回执无效。');
        const metadata = value.files.map(file => {
          if (!isObject(file) || file.status !== 'ready' || file.token !== value.access_token
              || file.access_token !== value.access_token || file.bytes !== file.size
              || typeof file.is_image !== 'boolean') throw new Error('恢复素材未确认。');
          return readMediaMetadata({ name: file.name, size: file.bytes, lastModified: file.lastModified,
            type: '', kind: file.is_image ? 'image' : 'video', duration: file.sec,
            note: file.note, trim_start: file.in_sec, trim_end: file.out_sec,
            width: file.width, height: file.height });
        });
        const bindings = value.files.map((file, index) => {
          if (!isObject(file) || !validUploadId(file.up_id) || !validUploadId(file.file_id)) throw new Error('恢复上传凭据无效。');
          return { file_id: fileIdentity(metadata[index]), upload_id: file.up_id, sha256: file.sha256,
            chunk_size: file.chunksize, draftTaskId: value.task_id, server_file_id: file.file_id,
            put_url: `${pathFor(String(value.task_id))}/files/${file.file_id}/chunks/{index}` };
        });
        const seed = validateDraft({ script: value.script, mode: value.mode, preferences: value.preferences,
          source_voice_preferred: value.source_voice_preferred,
          sentences: value.sentences, speakers: value.speakers, sentenceKinds: {}, files: metadata,
          elements: {}, sentenceChecks: {}, ownVoice: null, step,
          draftTaskId: value.task_id, draftTaskToken: value.access_token,
          uploadIds: bindings.map(file => file.upload_id), uploadBindings: bindings,
          uploadTokens: Object.fromEntries(bindings.map(file => [file.upload_id, value.access_token])) });
        // Retain the independent receipt even if navigation superseded the POST.
        retainWork({ taskId: seed.draftTaskId!, accessToken: seed.draftTaskToken!, title: seed.script.split(/\r?\n/)[0],
          createdAt: typeof value.created_at === 'string' ? value.created_at : new Date().toISOString(),
          status: 'draft', revision: 0, mode: seed.mode });
        if (!isCurrent()) return;
        // Strict validation + durable write happen BEFORE replacing UI state.
        const savedAt = Date.now(); writeDraftCache(seed, savedAt);
        cancelReads(); unmountWizard(); select(null); setTask(null); setUpload(null); setMissing(null); setError(''); setDialog(null);
        draftSuppressed.current = false; draftRef.current = seed; draftSaved.current = JSON.stringify(seed);
        draftDirty.current = false; draftBlocked.current = false; setDraftPending(false); setDraftProblem(''); setDraftNeedsClear(false);
        setDraftSavedAt(savedAt); clearTimeout(draftTimer.current);
        mountWizard(seed); showPage('create'); writeHash('#create'); setDraftStatus(restoredDraftNote(seed));
        setNotice(`已保存独立恢复草稿，原任务保持不变。原稿、素材和已核验转录已复制，无需重选完整素材；未重复 ASR、扣制作次数或开始制作。${reuse ? '已记下“允许画面重复出现”：镜头不够时同一素材会在不同时刻重复使用。' : ''}请重新确认编辑后再提交。`);
      });
    } catch (failure) {
      if (isCurrent()) {
        setError(`${errorText(failure)} 现有草稿未替换。恢复结果未确认时请先核对草稿/历史，不会自动重试；旧任务或已过期、未核验素材不能从本地快照补造。`);
        refreshHistory();
      }
    }
  }
  function newBlank(editorConfirmed = false) {
    if (!canLeave(editorConfirmed, true)) return;
    if ((hasDraftContent(draftRef.current) || draftDirty.current || draftBlocked.current || draftNeedsClear)
        && !window.confirm('新建空白将清空当前创作草稿及其浏览器缓存，并释放已选择的素材和录音。已有服务器作品不会被删除。继续？')) return;
    cancelReads(); unmountWizard(); setSample(false); draftSuppressed.current = true; draftRef.current = null; draftSaved.current = ''; draftDirty.current = false;
    clearDraftCache(); select(null); setTask(null); setUpload(null); setError(''); setMissing(null); setDrawer(false); setDialog(null);
    draftSuppressed.current = false; showPage('create'); mountWizard(null); writeHash('#create');
    setDraftStatus('空白稿件 · 填写后保存稿件、模式与上传凭据，不保存文件本体。');
  }
  function rememberReceipt(receipt: Receipt, title: string, acceptedStatus: 'queued' | 'done', mode: ProductionMode = 'voiceover'): HistoryWork {
    // Creation's 202 receipt may omit status/revision. Store its accepted state,
    // never an invalid history enum, and immediately GET the actual task state.
    const item: HistoryWork = { taskId: receipt.task_id, accessToken: receipt.access_token, title,
      createdAt: new Date().toISOString(), status: receipt.status ?? acceptedStatus, revision: receipt.revision ?? 0, mode };
    retainWork(item); return item;
  }
  async function submit(value: CreateSubmission) {
    if (actionLock.current || !serviceReady || pageRef.current !== 'create' || intentRef.current) throw new Error('制作服务尚未就绪，或当前已有操作。稿件和素材仍保留在本页。');
    const question = uploadUncertain
      ? '上次上传没有取得完整回执，服务器可能已创建作品。确认已核对作品历史，仍要再次提交并承担可能的重复制作费用？'
      : '开始上传并制作新闻视频？服务器会使用已配置的 AI 服务，可能产生费用。请确认稿件、素材使用权与所选效果；不会自动核实新闻事实。';
    if (!window.confirm(question)) return;
    // Freeze the accepted input, not a later onDraft emission. File identities
    // come from the validated draft; content/options come from this submission.
    const submittedDraft = draftRef.current ? validateDraft({ ...draftRef.current,
      script: value.script, mode: value.mode, preferences: value.preferences, sentences: value.sentences,
      sentenceKinds: value.sentenceKinds, speakers: value.speakers, uploadIds: value.uploadIds,
      uploadTokens: value.uploadTokens,
      files: draftRef.current.files.map((file, index) => ({ ...file, ...value.asset_options[index] })),
      voice: value.ownVoice ? 'self' : 'ai', ownVoice: value.ownVoice ? draftRef.current.ownVoice : null,
    }) : null;
    if (draftDirty.current) saveDraft();
    cancelReads(); select(null); setTask(null); setUpload(null); setError(''); setMissing(null); setNotice('');
    clearTimeout(draftTimer.current); draftSuppressed.current = true; actionLock.current = true;
    const controller = new AbortController(); uploadController.current = controller;
    setUploading(true); setNow(Date.now()); showPage('processing'); const serial = generation.current;
    try {
      const receipt = parseReceipt(await uploadTask(value, controller.signal, progress => {
        if (alive.current && serial === generation.current) setUpload(progress);
      }));
      // An acknowledged creation remains successful even if cache/status refresh fails.
      const title = Array.from(value.script.trim().split(/\r?\n/)[0] || '未命名新闻').slice(0, 80).join('');
      const item = rememberReceipt(receipt, title, 'queued', value.mode);
      // Only AFTER the accepted creation receipt. Storage failure must never
      // reach the submission catch, imply rejection, or trigger another POST.
      const recoverySaved = submittedDraft !== null && submissionRecovery.save(receipt.task_id, submittedDraft);
      if (!alive.current || serial !== generation.current) return;
      setPendingRecoveryCount(submissionRecovery.pendingCount());
      setRecoveryProblem(recoverySaved ? '' : '服务器已接收作品，但提交快照未确认保存到浏览器。请勿因缓存失败重复制作；刷新后可能无法返回原稿编辑。');
      unmountWizard(); wizardGeneration.current++; draftRef.current = null; draftSaved.current = ''; draftDirty.current = false;
      clearDraftCache(); setUploadUncertain(false); setDraftStatus('已提交的创作草稿已从本标签页清空。');
      refreshHistory(); beginOpen(receipt.task_id, 'result', { confirmed: true, receipt: item });
      setNotice('服务器已确认接收作品。正在核验实际制作状态；请勿重复提交。');
    } catch (failure) {
      if (alive.current && serial === generation.current) {
        // A clear refusal (4xx, e.g. the hourly start limit) created nothing; only an unknown outcome
        // (network failure, timeout, server error) may have created a work and needs the history check.
        const status = (failure as { status?: unknown } | null)?.status;
        const refused = typeof status === 'number' && (status >= 400 && status < 500 || status === 507);
        draftSuppressed.current = false; setUploadUncertain(!refused); showPage('create');
        setError(refused ? `${errorText(failure)} 本页稿件和素材都保留着，没有创建作品。`
          : `${errorText(failure)} 本页稿件和已选择文件仍保留；请先核对作品历史，不会自动重新上传。`); refreshHistory();
      }
      throw failure;
    } finally {
      if (uploadController.current === controller) { uploadController.current = null; actionLock.current = false; }
      if (alive.current) setUploading(false);
    }
  }
  async function mutate(action: () => Promise<void>) {
    if (actionLock.current) throw new Error('已有操作正在等待服务器确认。');
    actionLock.current = true; setBusy(true); setError('');
    try { await action(); }
    finally { actionLock.current = false; if (alive.current) setBusy(false); }
  }
  // allowShotReuse: the user explicitly accepts repeated footage after a "not enough distinct shots" failure.
  function retryOriginal(options: { allowShotReuse?: boolean } = {}) {
    const reuse = options.allowShotReuse === true;
    const target = selectedRef.current;
    if (!target || actionLock.current || intentRef.current || pageRef.current !== 'processing'
        || target.scope.signal.aborted || !task || task.task_id !== target.id || task.status !== 'failed') return;
    const v2 = task.lifecycle_v2 === true;
    if (v2 ? task.retry_same_supported !== true : task.revision !== 0) return;
    if (reuse ? !v2 || task.errorKind !== 'shortage' || task.mode === 'original'
      : task.errorKind && !['network', 'transient'].includes(task.errorKind)) return;
    if (!serviceReady) { setError('制作服务尚未就绪，请先重新检查服务。不会提交重试。'); return; }
    const serial = generation.current;
    const isCurrent = () => alive.current && selectedRef.current === target && serial === generation.current
      && !target.scope.signal.aborted && !intentRef.current && pageRef.current === 'processing';
    setDialog({ title: reuse ? '接受重复使用画面吗？' : v2 ? '继续这次制作？' : '用原素材重新制作？',
      text: reuse ? '你的素材里互不相同的画面不够，没法让每一句话都配上不同的镜头。选择“接受并继续”后，同一段素材会在不同时刻重复出现（同一场景的不同片段，不是定格补帧）；素材实在不够时会循环使用这些真实片段、优先用最相关的画面，保证能做完，借用的画面会在检查里标出，做完后可以在结果页换画面。已完成的前几步不会重做，只从“给每句话找画面”继续，可能产生少量 AI 费用。不想重复画面，请取消，回去删减稿子或多传素材。'
        : v2 ? '先核验已完成步骤的缓存，再从未完成处继续。同一作品只提交一次重试，不重新上传，不新增提交次数；无法安全续作时会停止。'
        : '将使用服务器保存的原稿和原素材，从第一步重新运行，不是断点续作。AI 服务可能再次收费；只会在你确认后提交一次。', confirm: reuse ? '接受并继续' : v2 ? '再试一次' : '确认重新制作',
      action: () => { if (!isCurrent()) return Promise.resolve(); return mutate(async () => {
        let current: TaskStatusResponse;
        try { current = parseTask(await target.request<unknown>(pathFor(target.id))); }
        catch { if (isCurrent()) setError('暂时无法确认任务状态。请先重新读取状态，没有提交重试。'); return; }
        if (!isCurrent()) return;
        if (current.task_id !== target.id || current.status !== 'failed' || current.revision !== task.revision || (v2 ? current.lifecycle_v2 !== true || current.retry_same_supported !== true : current.lifecycle_v2 === true || current.revision !== 0)
            || (reuse ? current.errorKind !== 'shortage' : current.errorKind && !['network', 'transient'].includes(current.errorKind))) throw new Error('任务状态已经变化，请返回并重新读取状态。');
        let accepted = false;
        try {
          const receipt = await target.request<unknown>(`${pathFor(target.id)}/retry`, { method: 'POST', body: JSON.stringify({ expected_revision: current.revision, ...(reuse ? { allow_shot_reuse: true } : {}) }) });
          accepted = isObject(receipt) && receipt.task_id === target.id && (receipt.id === undefined || receipt.id === target.id)
            && receipt.status === 'queued' && (receipt.accepted === undefined || receipt.accepted === true)
            && (v2 ? receipt.revision === undefined || receipt.revision === current.revision : receipt.revision === current.revision);
        } catch { /* A lost receipt is not permission for a second POST. Read only. */ }
        if (!isCurrent()) return;
        try {
          const next = parseTask(await target.request<unknown>(pathFor(target.id)));
          if (!isCurrent()) return;
          if (next.task_id !== target.id) throw new Error('任务编号不一致。');
          setTask(next); observeTask(next);
          if (['queued', 'running', 'done'].includes(next.status)) {
            setNotice(accepted ? '已取得重试回执，正在读取实际进度。' : '已重新确认服务器状态，没有重复提交重试。');
            if (next.status === 'done') showPage('result');
          } else setError('这次重试尚未确认开始。请先重新读取状态；只有再次明确确认才会重试，不会重新提交作品。');
        } catch { if (isCurrent()) setError('重试结果尚未确认。请先重新读取状态，不要重复提交。'); }
        if (isCurrent()) { setTaskRetry(value => value + 1); refreshHistory(); }
      }); } });
  }
  function openRename() {
    const target = selectedRef.current;
    if (!target || actionLock.current || intentRef.current || pageRef.current !== 'result' || target.scope.signal.aborted
        || !task || task.task_id !== target.id || task.status !== 'done') return;
    if (task.title === undefined || task.metadata_revision === undefined) {
      setError('服务器尚未提供可用于重命名的名称和版本，请先重新读取作品。'); refreshSelected(); return;
    }
    selectedReadController.current?.abort();
    let snapshot = task;
    const serial = generation.current;
    const current = () => alive.current && serial === generation.current && selectedRef.current === target
      && !target.scope.signal.aborted && !intentRef.current && pageRef.current === 'result';
    const publish = (next: TaskStatusResponse) => {
      if (!current()) return;
      snapshot = next;
      if (next.title !== undefined) target.title = next.title;
      setTask(next); observeTask(next);
    };
    const read = async (): Promise<TaskMetadata> => {
      if (!current()) throw new Error('页面已切换，本次操作已停止。');
      const next = parseTask(await target.request<unknown>(pathFor(target.id)));
      if (!current()) throw new Error('页面已切换，本次操作已停止。');
      if (next.task_id !== target.id || next.title === undefined || next.metadata_revision === undefined) throw new Error('服务器名称尚未确认。');
      publish(next);
      if (next.status !== 'done') throw new Error('作品状态已变化，请返回并重新读取作品。');
      return { task_id: next.task_id, title: next.title, revision: next.revision, metadata_revision: next.metadata_revision };
    };
    setRename({ baseline: { task_id: task.task_id, title: task.title, revision: task.revision, metadata_revision: task.metadata_revision }, read,
      save: async (baseline, name) => {
        if (!current() || actionLock.current) throw new Error('页面或任务状态已变化，请重新核对。');
        await mutate(async () => {
          try {
            const receipt = await renameTaskMetadata(target.request, baseline, name);
            // The four-field receipt is display-only: retain exact expiry/count,
            // task capability, creation time and the existing result editor.
            if (current()) {
              selectedReadController.current?.abort();
              publish({ ...snapshot, ...receipt }); setNotice('作品名称已由服务器确认保存；成片标题和保留期限未改变。');
            }
          } catch (failure) {
            if (current()) {
              try { await read(); } catch { /* Unknown remains unknown; never fake a saved title. */ }
            }
            throw failure;
          }
        });
      } });
  }
  function duplicate() {
    if (!selectedRef.current || actionLock.current || intentRef.current || pageRef.current !== 'result') return;
    setDuplicateChoice(true);
  }
  function duplicateCurrent() {
    const target = selectedRef.current; if (!target || actionLock.current) return;
    const serial = generation.current;
    const isCurrent = () => alive.current && selectedRef.current === target && generation.current === serial
      && !target.scope.signal.aborted && !intentRef.current && pageRef.current === 'result';
    const title = works.find(item => item.taskId === target.id)?.title ?? target.title;
    setDialog({ title: '创建独立副本？', text: '复制当前已保存版本及必要媒体，会占用额外磁盘。未保存的结果修改和专业剪辑台草稿不包含在内；复制本身不重新调用 AI 制作。', confirm: '确认复制',
      action: () => mutate(async () => {
        if (!isCurrent()) return;
        const current = parseTask(await target.request<unknown>(pathFor(target.id)));
        if (!isCurrent()) return;
        if (current.task_id !== target.id || current.status !== 'done') throw new Error('作品尚未完成或状态已变化，请重新读取后再复制。');
        const receipt = parseReceipt(await target.request<unknown>(`${pathFor(target.id)}/duplicate`, { method: 'POST', body: JSON.stringify({ expected_revision: current.revision }) }));
        if (receipt.task_id === target.id) throw new Error('副本回执没有新的作品编号，请核对历史，勿重复复制。');
        const item = rememberReceipt(receipt, `${Array.from(title).slice(0, 70).join('')}（副本）`, 'done', current.mode ?? target.mode);
        if (!isCurrent()) return;
        refreshHistory(); beginOpen(item.taskId, 'result', { confirmed: true, receipt: item }); setNotice('已取得独立副本回执，正在读取副本的真实状态。');
      }) });
  }
  function deleteSelected() {
    if (uploadController.current) {
      setDialog({ title: '停止上传？', text: '会中断本次传输，表单和已选择文件仍保留。若服务器已经接收作品，停止上传不等于撤销制作；请刷新历史确认，不要直接重复提交。', confirm: '停止上传', danger: true,
        action: async () => { uploadController.current?.abort(); } }); return;
    }
    const target = selectedRef.current; if (!target || actionLock.current) return;
    const title = works.find(item => item.taskId === target.id)?.title ?? target.title;
    setDialog({ title: '取消并永久删除作品？', text: `将永久删除《${title}》的全部媒体、历史版本和编辑工程，无法撤销。正在制作的任务也会取消；已经发生的服务费用不会因此撤回。`, confirm: '永久删除', danger: true,
      action: () => mutate(async () => {
        await target.request(pathFor(target.id), { method: 'DELETE' });
        if (!alive.current) return;
        // Only an acknowledged DELETE removes history. A 404, network error or
        // cleanup failure is not evidence that deletion completed.
        deleted.current.add(target.id); forgotten.current.add(target.id); pendingHistory.current.delete(target.id); observations.current.delete(target.id);
        setPendingHistoryCount(pendingHistory.current.size);
        setBrowserRows(browserRef.current.filter(item => item.taskId !== target.id)); setLocalRows(localRef.current.filter(item => item.taskId !== target.id));
        try {
          forgetHistory(target.id);
          if (!submissionRecovery.forget(target.id)) throw new Error('浏览器提交快照尚未移除。');
          pendingRemoval.current.delete(target.id);
        }
        catch { pendingRemoval.current.add(target.id); }
        setPendingRecoveryCount(submissionRecovery.pendingCount());
        setPendingRemovalCount(pendingRemoval.current.size);
        cancelReads(); select(null); setTask(null); setUpload(null); setMissing(null); setError(''); navigate('create', false, true); writeHash('/', true);
        setNotice('服务器已确认删除作品。'); refreshHistory();
      }) });
  }
  function forgetMissing() {
    const id = missing; if (!id || !browserRef.current.some(item => item.taskId === id)) return;
    if (!window.confirm('仅移除此浏览器历史中的记录和访问凭据？不会删除服务器媒体。移除后可能无法从此浏览器再次访问。')) return;
    try {
      forgetHistory(id); forgotten.current.add(id); pendingHistory.current.delete(id); setPendingHistoryCount(pendingHistory.current.size);
      if (!submissionRecovery.forget(id)) pendingRemoval.current.add(id);
      setPendingRemovalCount(pendingRemoval.current.size); setPendingRecoveryCount(submissionRecovery.pendingCount());
      setBrowserRows(browserRef.current.filter(item => item.taskId !== id)); cancelReads(); select(null); setTask(null); setMissing(null); navigate('create', false, true); writeHash('/', true);
      setNotice('已移除浏览器历史标记；服务器媒体未改动，本机列表仍以服务器返回为准。');
    } catch { setHistoryProblem('浏览器历史未能更新，记录和凭据仍保留；没有删除服务器作品。'); }
  }

  function preference<K extends keyof ShellPreferences>(key: K, value: ShellPreferences[K]) {
    try { v2Persistence.writePreference(key, value); }
    catch { setNotice('界面偏好未能安全保存；原缓存未覆盖，本次调整仅在此标签页有效。'); }
  }
  function dismissIntro() {
    setIntroDismissed(true); preference('introDismissed', true);
    document.querySelector<HTMLTextAreaElement>('.shell-wizard-host textarea')?.focus();
  }
  function showHelp() {
    if (pageRef.current !== 'create' || intentRef.current) {
      if (!canLeave()) return;
      if (draftRef.current) draftRef.current = { ...draftRef.current, step: 1 };
      navigate('create', false, true);
    } else {
      // Use the existing wizard's step action, not a remount that loses Files.
      document.querySelector<HTMLButtonElement>('.shell-wizard-host .gm-steps button')?.click();
      setWizardStep(1);
    }
    setDrawer(false); setIntroDismissed(false); preference('introDismissed', false);
  }
  function trySample(value: SampleTry) {
    // Same reset as "新建空白", then the sample's script, mode and real clips go into a fresh draft.
    if ((hasDraftContent(draftRef.current) || draftDirty.current || draftBlocked.current || draftNeedsClear)
        && !window.confirm('用范例自己做一遍会替换当前的创作草稿，并释放已选择的素材和录音。已有服务器作品不会被删除。继续？')) return;
    cancelReads(); unmountWizard(); setSample(false); draftSuppressed.current = true; draftRef.current = null; draftSaved.current = ''; draftDirty.current = false;
    clearDraftCache(); select(null); setTask(null); setUpload(null); setError(''); setMissing(null); setDrawer(false); setDialog(null);
    draftSuppressed.current = false; showPage('create');
    mountWizard({ script: value.script, mode: value.mode, step: 1, preferences: defaultPreferences(value.mode),
      files: [], elements: {}, sentenceChecks: {} }, value.files);
    writeHash('#create');
    setDraftStatus(`已放入范例稿件和 ${value.files.length} 段范例素材，素材正在后台上传。核对稿件后点“下一步：传素材”，之后和平时一样操作。`);
  }
  function openSample(confirmed = false) {
    if (!confirmed && !canLeave()) return;
    leaveCreator(); cancelReads(); unmountWizard(); select(null); setTask(null);
    setUpload(null); setMissing(null); setError(''); setDrawer(false); setDialog(null);
    setSample(true); showPage('result'); writeHash('/samples/default');
  }

  useEffect(() => {
    const node = drawerRef.current;
    if (!drawer) { node?.close(); return; }
    if (window.matchMedia('(min-width: 901px)').matches) { setDrawer(false); return; }
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    node?.showModal(); return () => { node?.close(); if (previous?.isConnected) previous.focus(); };
  }, [drawer]);
  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 901px)'); const changed = () => { if (desktop.matches) setDrawer(false); };
    desktop.addEventListener('change', changed); return () => desktop.removeEventListener('change', changed);
  }, []);
  useEffect(() => { document.getElementById('main-content')?.focus({ preventScroll: true }); }, [page]);

  const title = selection ? task?.task_id === selection.id && task.title !== undefined ? task.title
    : works.find(item => item.taskId === selection.id)?.title ?? selection.title : undefined;
  const modeCandidate = task?.mode ?? selection?.mode ?? draftRef.current?.mode;
  const activeMode = isProductionMode(modeCandidate) ? modeCandidate : 'voiceover';
  const compareWorks = (a: HistoryWork, b: HistoryWork) => sort === 'title' ? a.title.localeCompare(b.title, 'zh-CN')
    : timestamp(b.createdAt) - timestamp(a.createdAt);
  // Bound presentation, never evict older capabilities just to meet a UI cap.
  const recent = [...works].sort(compareWorks).slice(0, V2_HISTORY_LIMIT);
  const sourceDescription = config?.local_history
    ? '本机工作区：显示服务器允许读取的作品，并合并此浏览器保存的记录。打开作品使用原有访问凭据，不会在拒绝后更换凭据。'
    : '浏览器历史：仅列出此浏览器持有访问凭据的作品。清除浏览器数据可能使作品无法找回；不会读取其他人的任务列表。';
  const navigation = <>
    <button type="button" className="shell-new-work" disabled={busy || uploading} onClick={() => newBlank()}><span aria-hidden="true">＋</span>新作品</button>
    <nav className="shell-navigation" aria-label="工作区导航">
      {page !== 'create' && hasDraftContent(draftRef.current) && <button type="button" className="shell-nav-link" disabled={busy || uploading} onClick={() => navigate('create')}><Icon name="pencil" size={18} /><span>继续写稿</span></button>}
      <section className="shell-recent" aria-label="我的作品"><div className="shell-recent-heading"><h2>我的作品</h2><button type="button" aria-label={`作品排序：${sort === 'title' ? '按标题' : '按时间'}，点击切换`} onClick={() => { const next = sort === 'title' ? 'time' : 'title'; setSort(next); preference('histSort', next); }}>{sort === 'title' ? '按标题 ↕' : '按时间 ↕'}</button></div>
        {recent.map(item => <button type="button" key={item.taskId} disabled={busy || uploading} onClick={() => beginOpen(item.taskId)} aria-current={selection?.id === item.taskId ? 'true' : undefined}>
          <strong>{item.title || '未命名新闻'}</strong><small className="shell-recent-meta"><span className="shell-work-status" data-status={item.status}>{statusLabel(item.status)}</span>{item.mode && <span className="shell-mode-badge" data-mode={item.mode}>{modeLabel(item.mode)}</span>}<span>{versionLabel(item)}</span></small>
          <small>{item.createdAt ? <time dateTime={item.createdAt}>{dateLabel(item.createdAt)}</time> : '创建时间未提供'}</small>
          <small className="shell-work-expiry">{item.expires_at ? <>保留至 <time dateTime={item.expires_at} title={item.expires_at}>{dateLabel(item.expires_at)}</time></> : '到期时间未提供'}</small>
          {item.status === 'done' && <small>{historyChecksLabel(item.taskId === selection?.id ? selectedChecks : null)}</small>}
        </button>)}
        {!recent.length && <p className="shell-sidebar-empty">{historyLoading || !configResolved ? '正在读取作品记录…' : '还没有可显示的作品，从一条真实新闻开始。'}</p>}
        {historyError && <p className="shell-sidebar-empty">列表更新失败，当前显示上次记录。打开时会核验真实状态。</p>}
        {works.length > V2_HISTORY_LIMIT && <p className="shell-sidebar-empty">显示排序前 100 条；较早的访问凭据未删除。</p>}
      </section>
    </nav>
    <button type="button" className="shell-sample-button" disabled={busy || uploading} onClick={() => openSample()}>看一条范例作品</button>
    <button type="button" className="shell-drawer-help shell-nav-link" disabled={busy || uploading} onClick={showHelp}>怎么用</button>
    <details className="shell-receipt-note"><summary>保留期限与作品回执</summary><p>{sourceDescription}</p><p>媒体保留期限以服务器为准；未提供到期时间时不推测倒计时。访问凭据不放进分享链接；清除浏览器数据可能无法找回作品。</p><p>提交快照可在此浏览器恢复 30 天，不保存媒体文件，不会自动重新制作。</p>{historyCheckedAt !== null && <p>列表读取于 {savedTime(historyCheckedAt)}</p>}<button type="button" disabled={historyLoading} onClick={refreshHistory}>刷新作品列表</button>{pendingHistoryCount > 0 && <button type="button" onClick={retryHistorySave}>仅重试保存回执</button>}</details>
    <p className="shell-retention">新作品的保留期限以服务器返回的到期时间为准；做好后请及时下载留档。</p>
  </>;
  return <div className="shell-layout">
    <a className="shell-skip" href="#main-content" onClick={event => {
      const main = document.getElementById('main-content'); if (!main) return;
      event.preventDefault(); main.focus({ preventScroll: true }); main.scrollIntoView({ block: 'start' });
    }}>跳到主要内容</a>
    <header className="shell-header">
      <button type="button" className="shell-menu" aria-label="打开导航菜单" aria-expanded={drawer} aria-controls="workspace-navigation" onClick={() => setDrawer(true)}><span aria-hidden="true">☰</span></button>
      <button type="button" className="shell-brand" aria-label="金话筒 · 创作工作台" onClick={() => navigate('create')}><span className="shell-microphone"><Icon name="mic" size={20} color="var(--ink)" /></span><strong>金话筒</strong></button>
      <button type="button" className="shell-font-toggle" aria-pressed={font === 20} title={font === 20 ? '恢复常规字号' : '切换为 20 像素大字'} onClick={toggleFont}>{font === 20 ? '常规字号' : '大字'}</button>
      <button type="button" className="shell-help" disabled={busy || uploading} onClick={showHelp}>怎么用</button>
      <div className="shell-header-actions"><span className="shell-ready" data-ready={serviceReady} title="最近一次服务器检查结果，不是实时监控。">{healthBusy ? '正在检查制作服务' : serviceReady ? '服务正常' : '新制作暂不可用'}</span>
        <span className="shell-saved" role="status">{draftProblem ? '草稿尚未确认保存' : draftPending ? '草稿有修改 · 待保存' : draftSavedAt !== null ? <>✓ {draftSavedAutomatically ? '已自动保存' : '已保存'} <time dateTime={new Date(draftSavedAt).toISOString()}>{savedTime(draftSavedAt)}</time></> : '草稿随写随存'}</span>
      </div>
    </header>
    <aside className="shell-sidebar">{navigation}</aside>
    <dialog ref={drawerRef} id="workspace-navigation" className="shell-drawer" aria-label="工作区导航菜单" onCancel={event => { event.preventDefault(); setDrawer(false); }}>
      <button type="button" className="shell-drawer-close" aria-label="关闭导航菜单" onClick={() => setDrawer(false)}><span aria-hidden="true">×</span></button>{navigation}
    </dialog>
    <main id="main-content" className="shell-main" data-page={page} tabIndex={-1}>
      {(!serviceReady || healthError || limitsError || configError) && <div className="shell-notice" role="status">
        {[healthError, limitsError, configError].filter(Boolean).join(' ') || '正在读取制作服务配置；已有作品不受此检查阻挡。'}
        <button type="button" disabled={healthBusy} onClick={() => setHealthRetry(value => value + 1)}>重新检查服务</button>
      </div>}
      {historyProblem && <div className="shell-notice" role="alert">{historyProblem}<button type="button" onClick={refreshHistory}>重新读取历史</button></div>}
      {pendingHistoryCount > 0 && <div className="shell-notice shell-cache-warning" role="alert"><strong>{pendingHistoryCount} 条作品记录尚未确认写入浏览器。</strong> 访问凭据已保留在本标签页；请勿刷新或重新提交制作。<button type="button" onClick={retryHistorySave}>仅重试保存记录</button></div>}
      {pendingRemovalCount > 0 && <div className="shell-notice" role="alert">{pendingRemovalCount} 条浏览器旧记录或提交快照未能移除。本页已隐藏这些记录；刷新后可能再次出现。此提示不代表服务器媒体已删除。<button type="button" onClick={retryHistoryRemoval}>仅重试移除旧记录</button></div>}
      {(recoveryProblem || pendingRecoveryCount > 0) && <div className="shell-notice" role="alert">{recoveryProblem || '有提交快照尚未确认保存；请勿刷新。'}{pendingRecoveryCount > 0 && <><span> {pendingRecoveryCount} 条快照仅确认保留在本标签页。</span><button type="button" onClick={() => {
        const remaining = submissionRecovery.retry(); setPendingRecoveryCount(remaining);
        setRecoveryProblem(remaining ? '提交快照仍未确认保存；原缓存未覆盖，请勿刷新。' : '');
        if (!remaining) setNotice('提交快照已确认保存到此浏览器；未上传、未转写、未重新制作。');
      }}>仅重试保存提交快照</button></>}</div>}
      {draftProblem && <div className="shell-notice" role="alert">{draftProblem}{draftNeedsClear
        ? <button type="button" onClick={clearDraftCache}>仅重试清除旧草稿</button>
        : <button type="button" disabled={uploading || busy} onClick={retryDraftSave}>重试保存草稿</button>}</div>}
      {fontProblem && <p className="shell-notice" role="status">{fontProblem}</p>}
      {uploadUncertain && <div className="shell-notice" role="alert">上次上传未取得完整确认，服务器可能已创建作品。请先核对历史，再决定是否再次制作。<button type="button" onClick={refreshHistory}>刷新历史记录</button></div>}
      {error && page !== 'processing' && <div className="shell-error" role="alert">{error}<button type="button" aria-label="关闭错误提示" onClick={() => setError('')}><span aria-hidden="true">×</span></button></div>}
      {notice && <div className="shell-notice" role="status">{notice}<button type="button" aria-label="关闭状态提示" onClick={() => setNotice('')}><span aria-hidden="true">×</span></button></div>}
      {missing && <section className="shell-panel shell-gone"><h1>{missingReason === 'expired' ? '作品已到期或已清理' : missingReason === 'network' ? '暂时无法读取作品' : '作品不存在或暂不可访问'}</h1><p>{missingReason === 'expired' ? '服务器已确认此作品不可再用。原访问记录仍保留，可新建作品；不会自动重新制作。' : missingReason === 'network' ? '连接或响应尚未确认。记录和访问凭据仍保留，可只读重试；未认定作品到期或删除。' : '请在持有原访问凭据的浏览器核对。权限拒绝或 404 不等于作品到期或删除，不会更换凭据自动重试。'}</p><div className="shell-actions">
        <button type="button" disabled={busy || uploading} onClick={() => beginOpen(missing)}>重新读取作品</button>
        {browserRows.some(item => item.taskId === missing) && <button type="button" disabled={busy || uploading} onClick={forgetMissing}>仅移除浏览器标记</button>}
        <button type="button" disabled={busy || uploading} onClick={() => newBlank()}>新作品</button>
      </div></section>}
      {intent && <section className="shell-panel shell-opening" role="status"><h1>正在读取作品</h1><p>{configResolved ? '正在核验访问权限与服务器真实状态，尚未判定成片已完成。' : '正在确认工作区历史来源，不会启动创作或写入草稿。'}</p><div className="shell-actions"><button type="button" onClick={() => navigate('create')}>返回写稿</button><button type="button" onClick={() => newBlank()}>新建空白</button></div></section>}
      {!intent && page === 'gone' && !missing && <section className="shell-panel shell-gone"><h1>暂未打开作品</h1><p>请从左侧「我的作品」重新选择，或继续写稿。未确认作品已清理，不会删除原记录。</p><button type="button" onClick={() => navigate('create')}>返回写稿</button></section>}
      {!intent && page === 'create' && <>
        {(!limits || !configResolved) && <p className="shell-panel" role="status">正在等待有效的上传限制和工作区配置。可先从作品历史打开已有作品；表单不会使用猜测的上传限制。</p>}
      </>}
      {/* Keep this exact subtree mounted only across create -> upload -> failed
          submit. Moving it inside a page conditional would discard real Files. */}
      {wizardMounted && limits && configResolved && <div className="shell-wizard-host" hidden={page !== 'create' || !!intent}>
        <CreateWizard key={wizardKey} limits={limits} initialDraft={wizardSeed} initialFiles={wizardFiles} generativeAllowed={config?.generative_fill_available === true}
          scriptAssistant={context => <ScriptAssistant {...context} />}
          intro={!introDismissed && wizardStep === 1 ? <IntroCard onStart={dismissIntro} onSample={() => openSample()} /> : undefined}
          busy={uploading || busy} serviceReady={serviceReady} onDraft={value => receiveDraft(value, wizardKey)} onSubmit={submit} onError={setError} />
      </div>}
      {!intent && page === 'create' && <details className="shell-draft-details"><summary>草稿操作</summary>
        <div className="shell-actions shell-draft-actions" data-state={draftProblem ? 'error' : draftPending ? 'pending' : draftSavedAt !== null ? 'saved' : 'ready'}><div className="shell-draft-copy" role="status">{draftStatus}{draftSavedAt !== null && <small>最近确认保存：<time dateTime={new Date(draftSavedAt).toISOString()}>{savedTime(draftSavedAt)}</time></small>}</div><button type="button" disabled={busy || uploading} onClick={retryDraftSave}>保存草稿</button><button type="button" disabled={busy || uploading} onClick={() => newBlank()}>新建空白</button></div>
      </details>}
      {!intent && page === 'processing' && <Processing task={task} upload={upload} uploading={uploading} error={error} now={now} title={title}
        mode={activeMode} busy={busy} onCancel={deleteSelected} onRetry={retryOriginal} onEdit={editFailedDraft} onNew={() => newBlank(true)}
        onRefresh={() => { setTaskRetry(value => value + 1); refreshHistory(); }} />}
      {!intent && page === 'result' && sample && <SampleView onBack={() => navigate('create')} onTry={trySample} />}
      {!intent && page === 'result' && selection && task?.status === 'done' && <>
        <div className="shell-actions shell-result-tools"><span>{title}</span><button type="button" onClick={() => {
          const url = new URL(hashFor(selection.id), location.origin); const serial = generation.current;
          void navigator.clipboard?.writeText(url.href).then(() => {
            if (alive.current && generation.current === serial) setNotice('已复制不含令牌的作品链接。仍需在持有凭据的浏览器或服务器允许的本机工作区打开。');
          }).catch(() => { if (alive.current && generation.current === serial) setNotice('无法写入剪贴板，请复制浏览器地址栏中的作品链接。'); });
          if (!navigator.clipboard) setNotice('此浏览器不支持剪贴板写入，请复制地址栏中的作品链接。');
        }}>复制作品链接</button></div>
        <ResultWorkbench taskId={selection.id} accessToken={selection.token} title={title} request={selection.request} onProcessing={processingStarted} onChanged={refreshSelected}
          onChecksChange={onChecksChange} onUnavailable={onUnavailable} onMutationStateChange={onMutationStateChange} onDelete={deleteSelected} onNew={() => newBlank(true)} onError={setError} onDuplicate={duplicate} onRename={openRename} />
        <details className="shell-panel shell-media-fallback"><summary>报告暂不可用？直接下载成片</summary><p>只请求当前作品的已保存成片。服务器仍会校验访问权限；不会重新制作。</p><a href={taskMedia(selection.id, selection.token, task.revision, true)} download="新闻成片.mp4" referrerPolicy="no-referrer">下载当前成片</a></details>
      </>}
    </main>
    {dialog && <ConfirmDialog spec={dialog} onClose={() => setDialog(null)} onError={setError} />}
    {rename && <RenameDialog spec={rename} onClose={() => setRename(null)} />}
    {duplicateChoice && <DuplicateDialog onClose={() => setDuplicateChoice(false)} onChoose={mode => {
      setDuplicateChoice(false); if (mode === undefined) duplicateCurrent(); else void editFailedDraft(1, mode);
    }} />}
  </div>;
}