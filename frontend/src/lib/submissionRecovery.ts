import type { Draft } from '../components/CreateWizard';

/** Browser-only capabilities, with the same trust boundary as gm-core-v1.
 * Never log/export these records, put them in URLs, or derive them from reports.
 * Separate per-task keys avoid cross-tab read/modify/write loss of other tasks.
 * No index, history migration, automatic recovery, network call or media bytes.
 * TTL is local retention, NOT a promise that an upload remains usable. Expired
 * records are refused (not swept on mount); browser quota bounds total storage.
 */
export const RECOVERY_PREFIX = 'golden-mic.submission-recovery.v1.';
export const RECOVERY_TTL_MS = 30 * 24 * 60 * 60 * 1000;
export const RECOVERY_MAX_LENGTH = 512 * 1024 + 1024;
type StorageAccess = () => Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
type Snapshot = { version: 1; taskId: string; savedAt: number; expiresAt: number; draft: Draft };
export type RecoveryResult = { status: 'saved' | 'memory'; draft: Draft }
  | { status: 'missing' | 'expired' | 'invalid' | 'unavailable'; draft: null };
const validId = (id: string) => /^[A-Za-z0-9_-]{1,128}$/.test(id);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const invalid = (): never => { throw new Error('提交快照无法安全读取或保存。'); };

/** Construction is inert. Reads happen ONLY at explicit return-to-edit; writes
 * ONLY after an accepted creation receipt or explicit local save retry.
 * Memory fallback is also task-bound, validated and subject to the same TTL.
 */
export function createSubmissionRecovery(validateDraft: (value: unknown) => Draft,
  storage: StorageAccess = () => localStorage, now: () => number = Date.now) {
  const memory = new Map<string, string>();
  const pending = new Set<string>();
  function parse(raw: string, taskId: string): Snapshot {
    if (raw.length > RECOVERY_MAX_LENGTH) return invalid();
    const value: unknown = JSON.parse(raw);
    if (!object(value) || value.version !== 1 || value.taskId !== taskId
        || typeof value.savedAt !== 'number' || !Number.isSafeInteger(value.savedAt) || value.savedAt <= 0 || value.savedAt > now()
        || typeof value.expiresAt !== 'number' || !Number.isSafeInteger(value.expiresAt)
        || value.expiresAt !== value.savedAt + RECOVERY_TTL_MS) return invalid();
    return { version: 1, taskId, savedAt: value.savedAt, expiresAt: value.expiresAt, draft: validateDraft(value.draft) };
  }
  function persist(taskId: string, encoded: string): boolean {
    try {
      const target = storage(), key = RECOVERY_PREFIX + taskId;
      const previous = target.getItem(key);
      // An accepted task ID is immutable. Never replace a conflicting/corrupt
      // record to hide a collision, even on an explicit retry.
      if (previous !== null && previous !== encoded) return false;
      target.setItem(key, encoded);
      if (target.getItem(key) !== encoded) return false;
      pending.delete(taskId); return true;
    } catch { return false; }
  }
  return {
    save(taskId: string, draft: Draft): boolean {
      // Catch validation/serialization/storage exceptions without ever exposing
      // their text (a storage adapter could include capabilities in an error).
      if (!validId(taskId)) return false;
      try {
        const savedAt = now();
        const snapshot: Snapshot = { version: 1, taskId, savedAt, expiresAt: savedAt + RECOVERY_TTL_MS, draft: validateDraft(draft) };
        const encoded = JSON.stringify(snapshot);
        parse(encoded, taskId);
        if (memory.has(taskId) && memory.get(taskId) !== encoded) return false;
        memory.set(taskId, encoded); pending.add(taskId);
        return persist(taskId, encoded);
      } catch { return false; }
    },
    read(taskId: string): RecoveryResult {
      if (!validId(taskId)) return { status: 'invalid', draft: null };
      let raw: string | null = memory.get(taskId) ?? null;
      const fromMemory = raw !== null;
      if (raw === null) {
        try { raw = storage().getItem(RECOVERY_PREFIX + taskId); }
        catch { return { status: 'unavailable', draft: null }; }
      }
      if (raw === null) return { status: 'missing', draft: null };
      try {
        const snapshot = parse(raw, taskId);
        if (snapshot.expiresAt <= now()) return { status: 'expired', draft: null };
        return { status: fromMemory && pending.has(taskId) ? 'memory' : 'saved', draft: snapshot.draft };
      } catch { return { status: 'invalid', draft: null }; }
    },
    retry(): number {
      for (const taskId of pending) {
        const encoded = memory.get(taskId);
        if (!encoded) continue;
        try { if (parse(encoded, taskId).expiresAt > now()) persist(taskId, encoded); }
        catch { /* Retain the failure without exposing content. */ }
      }
      return pending.size;
    },
    forget(taskId: string): boolean {
      if (!validId(taskId)) return false;
      memory.delete(taskId); pending.delete(taskId);
      try {
        const target = storage(), key = RECOVERY_PREFIX + taskId;
        target.removeItem(key); return target.getItem(key) === null;
      } catch { return false; }
    },
    pendingCount: () => pending.size,
  };
}