import type { ShellPreferences } from '../model';

export const V2_KEY = 'gm-modes-v1';
const MAX_BYTES = 4 * 1024 * 1024;
const LEGACY_KEYS = ['gm-core-v1', 'golden-mic.draft.v2', 'golden-mic.font-size'] as const;
type Store = Pick<Storage, 'getItem' | 'setItem'>;
type Envelope = Record<string, unknown>;
const object = (v: unknown): v is Envelope => !!v && typeof v === 'object' && !Array.isArray(v);
const conflict = () => new Error('浏览器缓存已被其他页面修改或格式不受支持；未覆盖原记录，请保留本页文字并重新核对。');

/** Each writer owns ONLY its slots. pend/checked/receipts are opaque siblings.
 * Reads never write. Migration archives the exact legacy strings and retains
 * the old keys. No archived tasks/capabilities become live history.
 * Synchronous compare/readback detects observed conflicts; localStorage does
 * not provide atomic cross-tab CAS. Never retry a failed write automatically.
 */
export function createV2Persistence(storage: () => Store = () => localStorage) {
  let draftBaseline: string | undefined;
  const preferenceBaselines = new Map<keyof ShellPreferences, string>();
  const fingerprint = (root: Envelope | null) => JSON.stringify([root?.draftVersion, root?.draft, root?.draftSavedAt]);
  function read(): { raw: string | null; root: Envelope | null } {
    const raw = storage().getItem(V2_KEY);
    if (raw === null) return { raw, root: null };
    if (raw.length > MAX_BYTES) throw conflict();
    const root: unknown = JSON.parse(raw);
    // The result writer can be the first writer. Adopt only its known pending
    // envelope, not an arbitrary unversioned prototype/draft as live v2 state.
    if (!object(root) || (root.schemaVersion !== 1 && !(root.schemaVersion === undefined
      && Object.keys(root).every(key => key === 'pend' || key === 'checked')
      && (root.pend === undefined || object(root.pend)) && (root.checked === undefined || object(root.checked))))) throw conflict();
    return { raw, root };
  }
  function fresh(): Envelope {
    const legacy: Record<string, string> = {};
    for (const key of LEGACY_KEYS) {
      const raw = storage().getItem(key);
      if (raw !== null) {
        if (raw.length > 2 * 1024 * 1024) throw conflict();
        legacy[key] = raw;
      }
    }
    return { schemaVersion: 1, archive: { version: 1, legacy } };
  }
  function commit(previous: string | null, root: Envelope) {
    const encoded = JSON.stringify(root);
    if (encoded.length > MAX_BYTES || storage().getItem(V2_KEY) !== previous) throw conflict();
    storage().setItem(V2_KEY, encoded);
    if (storage().getItem(V2_KEY) !== encoded) throw conflict();
  }
  return {
    readDraft(): Envelope | null {
      const { root } = read();
      draftBaseline = fingerprint(root);
      if (!root || !Object.prototype.hasOwnProperty.call(root, 'draft')) return null;
      if (root.draftVersion !== 3) throw conflict();
      return { draftVersion: root.draftVersion, draft: root.draft, draftSavedAt: root.draftSavedAt };
    },
    writeDraft(draft: unknown, savedAt: number | null) {
      if (draft !== null && (!object(draft) || !Number.isSafeInteger(savedAt) || savedAt === null || savedAt <= 0)
          || draft === null && savedAt !== null) throw conflict();
      const { raw, root } = read();
      if (draftBaseline !== undefined && draftBaseline !== fingerprint(root)) throw conflict();
      const next = { ...(root?.schemaVersion === 1 ? root : { ...fresh(), ...root }), draftVersion: 3, draft, draftSavedAt: savedAt };
      commit(raw, next); draftBaseline = fingerprint(next);
    },
    readPreferences(): ShellPreferences {
      const { root } = read();
      if (root?.uiVersion !== undefined && root.uiVersion !== 1) throw conflict();
      for (const key of ['introDismissed', 'bigFont'] as const) if (root?.[key] !== undefined && typeof root[key] !== 'boolean') throw conflict();
      if (root?.histSort !== undefined && root.histSort !== 'time' && root.histSort !== 'title') throw conflict();
      for (const key of ['introDismissed', 'bigFont', 'histSort'] as const) preferenceBaselines.set(key, JSON.stringify(root?.[key]) ?? 'undefined');
      return { introDismissed: root?.introDismissed === true,
        bigFont: typeof root?.bigFont === 'boolean' ? root.bigFont : storage().getItem('golden-mic.font-size') === '20',
        histSort: root?.histSort === 'title' ? 'title' : 'time' };
    },
    writePreference<K extends keyof ShellPreferences>(key: K, value: ShellPreferences[K]) {
      if (!['introDismissed', 'bigFont', 'histSort'].includes(key)
          || (key === 'histSort' ? value !== 'time' && value !== 'title' : typeof value !== 'boolean')) throw conflict();
      const { raw, root } = read();
      if (root?.uiVersion !== undefined && root.uiVersion !== 1) throw conflict();
      const before = JSON.stringify(root?.[key]) ?? 'undefined';
      if (preferenceBaselines.has(key) && preferenceBaselines.get(key) !== before) throw conflict();
      commit(raw, { ...(root?.schemaVersion === 1 ? root : { ...fresh(), ...root }), uiVersion: 1, [key]: value });
      preferenceBaselines.set(key, JSON.stringify(value));
    },
  };
}

export const v2Persistence = createV2Persistence();