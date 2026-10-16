// Plain, redacted description of why a task failed. The raw server text is never
// rendered directly: local paths, long identifiers/tokens and key-like values are
// removed first, control characters stripped and the length capped.
const MAX_DETAIL = 400;
const PATH = /(?:[A-Za-z]:\\|\/)(?:[^\s'"<>|:*?\\/]+[\\/])+([^\s'"<>|:*?\\/]*)/g;
const SECRET = /\b(?:bearer|authorization|api[_-]?key|access[_-]?token|token|secret|password|sk-[A-Za-z0-9]*)\b\s*[:=]?\s*\S+/gi;
const LONG_ID = /[A-Za-z0-9_-]{24,}/g;

export function redactFailureText(value: unknown): string {
  if (typeof value !== 'string') return '';
  const text = value
    .replace(/[\p{Cc}\p{Cs}\u2028\u2029]+/gu, ' ')
    .replace(SECRET, '[已隐藏]')
    .replace(PATH, (_match, name: string) => name ? `…/${name.replace(LONG_ID, '…')}` : '…')
    .replace(LONG_ID, '…')
    .replace(/\s+/g, ' ').trim();
  return text.length > MAX_DETAIL ? `${text.slice(0, MAX_DETAIL)}…` : text;
}

export function describeFailure(task: { error_stage?: string | null; error_message?: string | null } | null | undefined) {
  const stage = typeof task?.error_stage === 'string' ? redactFailureText(task.error_stage).slice(0, 60) : '';
  const detail = redactFailureText(task?.error_message);
  return { stage, detail };
}
