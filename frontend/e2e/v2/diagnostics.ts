/** Static classifications and asset coordinates only. Never retain raw errors,
 * messages, function names, query strings, source text, tokens or DOM. */
export function pageErrorFacts(error: { name?: string; message?: string; stack?: string }) {
  const names = ['Error', 'TypeError', 'ReferenceError', 'RangeError', 'SyntaxError', 'DOMException'];
  const name = names.find(value => value === error.name) ?? 'other';
  const category = error.message === 'Illegal invocation' ? 'illegal-invocation' : 'other';
  const frames = [...(error.stack ?? '').matchAll(/(?:^|[\s(])http:\/\/127\.0\.0\.1:8787\/assets\/([A-Za-z0-9_-]{1,100}\.js):(\d{1,8}):(\d{1,8})(?=$|[\s)])/gm)]
    .slice(0, 8).map(match => ({ asset: match[1], line: Number(match[2]), column: Number(match[3]) }));
  return { name, category, frames };
}