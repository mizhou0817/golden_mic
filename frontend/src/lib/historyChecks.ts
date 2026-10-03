import { parseChecks } from './workbenchApi';

/** Display-only projection of an authorized gate response, never permission. */
export type HistoryChecksSummary = Readonly<{
  revision: number; blocking_count: number; pending_count: number; passed: boolean;
}>;
export const UNKNOWN_HISTORY_CHECKS = '发布前检查尚未读取';

export function parseHistoryChecks(value: unknown, revision: number): HistoryChecksSummary | null {
  if (!Number.isSafeInteger(revision) || revision < 0) return null;
  try {
    const checks = parseChecks(value);
    if (checks.revision !== revision
        || !checks.passed && checks.blocking_count === 0 && checks.pending_count === 0) return null;
    // Counts are authoritative. Do not infer a floor from report/check rows,
    // or manufacture passed=true from zero counts.
    return { revision: checks.revision, blocking_count: checks.blocking_count,
      pending_count: checks.pending_count, passed: checks.passed };
  } catch { return null; }
}

export function historyChecksLabel(summary: HistoryChecksSummary | null): string {
  return !summary ? UNKNOWN_HISTORY_CHECKS
    : summary.blocking_count > 0 ? `待修改 ${summary.blocking_count} 处`
      : summary.pending_count > 0 ? `待确认 ${summary.pending_count} 项`
        : summary.passed ? '检查通过' : UNKNOWN_HISTORY_CHECKS;
}