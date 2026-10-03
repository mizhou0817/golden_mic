import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import type { ProductionMode, QuoteTake, QuoteWord, Speaker } from '../lib/productionModes';
import type { QuoteRange, QuoteWave, Row, createWorkbenchApi } from '../lib/workbenchApi';
import { SPEAKER_FIELD_LIMITS, speakerFieldProblem, workbenchErrorMessage } from '../lib/workbenchApi';

const clock = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;
const failureText = workbenchErrorMessage;
// Literal evidence only: never normalize numbers/fillers into different words.
// Numeric signs and decimal points remain significant, as in the server check.
function literalWords(value: string): string {
  const chars = Array.from(value.normalize('NFKC').toLowerCase());
  const digit = (v: string | undefined) => !!v && /^[\p{Nd}零〇一二两兩三四五六七八九十百千万萬亿億壹贰貳叁參肆伍陆陸柒捌玖拾佰仟]$/u.test(v);
  return chars.filter((c, i) => (c === '.' && digit(chars[i - 1]) && digit(chars[i + 1]))
    || ((c === '+' || c === '-') && digit(chars[i + 1])) || !/[\p{P}\p{S}\p{Z}\p{C}\s]/u.test(c)).join('');
}

export function quoteTrimEvidence(source: QuoteTake | null | undefined): { words: readonly QuoteWord[]; reason: string } {
  const no = (reason: string) => ({ words: [], reason });
  if (!source) return no('没有找到可核对的原声来源，不能剪短。可以换一段或删除这句。');
  if (source.precision !== 'word') return no('这段只有整段时间，没有可靠的逐词时间，不能精确剪短。可以换一段或删除这句。');
  if (!Number.isFinite(source.start) || !Number.isFinite(source.end) || source.start < 0 || source.end <= source.start
    || !Array.isArray(source.words) || !source.words.length) return no('逐词时间不完整，不能精确剪短。可以换一段或删除这句。');
  let previous = source.start;
  const text: string[] = [];
  for (const word of source.words) {
    if (!word || typeof word.w !== 'string' || !Number.isFinite(word.s) || !Number.isFinite(word.e)
      || word.s < previous || word.e <= word.s || word.e > source.end) {
      return no('逐词时间有缺失、重叠或超出原声范围，不能精确剪短。可以换一段或删除这句。');
    }
    const key = literalWords(word.w);
    if (!key) return no('逐词文字和原话未完整对应，不能精确剪短。可以换一段或删除这句。');
    text.push(key); previous = word.e;
  }
  if (text.join('') !== literalWords(source.asr_text)) return no('逐词文字没有覆盖全部原话，不能精确剪短。可以换一段或删除这句。');
  return { words: source.words, reason: '' };
}

/** Reports may retain the immutable original take plus a saved trim. The next
 * edit is bounded by that saved interval, not the wider original interview.
 * spoken_text is the server's actual current transcript; never rebuild it by
 * concatenating display words or apply the trim to final-film clocks. */
export function currentQuoteSource(row: Row): QuoteTake | null {
  const source = row.source, trim = row.trim;
  if (!source) return null;
  if (!trim || trim.start === source.start && trim.end === source.end) return source;
  if (!Number.isFinite(trim.start) || !Number.isFinite(trim.end) || trim.start < source.start
    || trim.end > source.end || trim.end <= trim.start) return null;
  const evidence = quoteTrimEvidence(source), spoken = typeof row.spoken_text === 'string' ? row.spoken_text : '';
  const words = evidence.words.filter(word => word.s >= trim.start && word.e <= trim.end);
  const split = evidence.words.some(word => word.s < trim.start && trim.start < word.e || word.s < trim.end && trim.end < word.e);
  return { ...source, ...trim, asr_text: spoken, matched_text: spoken, words,
    precision: evidence.reason || split ? 'segment' : source.precision };
}

/** Indices select a contiguous set of real words, never a text reconstruction. */
export function quoteRangeForWords(source: QuoteTake, anchor: number, focus: number): QuoteRange | null {
  const { words, reason } = quoteTrimEvidence(source);
  if (reason || !Number.isInteger(anchor) || !Number.isInteger(focus)
    || Math.min(anchor, focus) < 0 || Math.max(anchor, focus) >= words.length) return null;
  const first = Math.min(anchor, focus), last = Math.max(anchor, focus);
  return { start: first === 0 ? source.start : words[first].s, end: last === words.length - 1 ? source.end : words[last].e };
}
export function quoteRangeProblem(source: QuoteTake | null | undefined, range: QuoteRange): string {
  const { words, reason } = quoteTrimEvidence(source);
  if (reason || !source) return reason;
  if (!Number.isFinite(range.start) || !Number.isFinite(range.end) || range.start < source.start || range.end > source.end
    || range.end <= range.start) return '只能保留原来范围内的一段连续原话，不能向外扩展。';
  if (!(range.start === source.start || words.some(w => w.s === range.start))
    || !(range.end === source.end || words.some(w => w.e === range.end))
    || words.some(w => w.s < range.start && range.start < w.e || w.s < range.end && range.end < w.e)) {
    return '起止位置必须落在真实词边界，不能剪开一个词。';
  }
  if (!words.some(w => w.s >= range.start && w.e <= range.end)) return '请至少保留一个完整的词。';
  // Compare endpoints, avoiding 2.3 - 1.3 becoming 0.9999999999999998.
  // No rounded clock is sent back to the server.
  if (range.end < range.start + 1) return '原声至少保留 1 秒，请多选几个完整的词。';
  return '';
}
/** About .2s in the requested direction, snapped to real legal boundaries. */
export function nudgeQuoteRange(source: QuoteTake, range: QuoteRange, edge: 'start' | 'end', direction: -1 | 1): QuoteRange {
  const { words, reason } = quoteTrimEvidence(source);
  if (reason || !Number.isFinite(range.start) || !Number.isFinite(range.end)
    || range.start < source.start || range.end > source.end || range.end <= range.start) return range;
  const fixed = edge === 'start' ? 'end' : 'start';
  if (fixed === 'start' ? range.start !== source.start && !words.some(w => w.s === range.start)
    : range.end !== source.end && !words.some(w => w.e === range.end)) return range;
  const points = new Set(edge === 'start' ? [source.start, ...words.map(w => w.s)] : [...words.map(w => w.e), source.end]);
  const target = range[edge] + direction * .2;
  // Ordered complete word evidence was checked once above. Every candidate is
  // now a real boundary; do not re-validate the whole transcript for each point.
  const valid = [...points].filter(point => direction * (point - range[edge]) > 0
    && (edge === 'start' ? range.end >= point + 1 : point >= range.start + 1));
  valid.sort((a, b) => Math.abs(a - target) - Math.abs(b - target) || Math.abs(a - range[edge]) - Math.abs(b - range[edge]));
  return valid.length ? { ...range, [edge]: valid[0] } : range;
}

/** Peak-preserving reduction of actual samples, including truly zero silence. */
export function quoteWaveBins(wave: QuoteWave, maximum = 240): { start: number; end: number; amplitude: number }[] {
  if (!Number.isInteger(maximum) || maximum < 1) return [];
  const step = Math.max(1, Math.ceil(wave.samples.length / maximum)), result = [];
  for (let index = 0; index < wave.samples.length; index += step) {
    let amplitude = 0;
    for (let j = index; j < Math.min(index + step, wave.samples.length); j++) amplitude = Math.max(amplitude, Math.abs(wave.samples[j]));
    const start = wave.start + index * wave.interval_ms / 1000;
    if (start >= wave.end) break;
    result.push({ start, end: Math.min(wave.end, wave.start + Math.min(index + step, wave.samples.length) * wave.interval_ms / 1000), amplitude });
  }
  return result;
}

export interface QuoteEditorProps {
  row: Row; revision: number; mode: ProductionMode; disabled: boolean;
  api: Pick<ReturnType<typeof createWorkbenchApi>, 'takes' | 'wave'>;
  pendingTrim?: QuoteRange; pendingTake?: Pick<QuoteTake, 'take_id'>; toNarration: boolean;
  speakers: readonly Speaker[]; pendingSpeakers: Readonly<Record<string, Pick<Speaker, 'name' | 'title'>>>;
  onTrim: (range: QuoteRange | undefined) => void; onTake: (take: QuoteTake | undefined) => void;
  onToNarration: (selected: boolean) => void; onSpeaker: (id: string, patch: Partial<Pick<Speaker, 'name' | 'title'>>) => void;
  onListen: () => void;
}

export default function QuoteEditor(props: QuoteEditorProps) {
  const { row, revision, api, disabled, pendingTrim, pendingTake, toNarration } = props;
  const source = useMemo(() => currentQuoteSource(row), [row]), id = useId();
  const evidence = useMemo(() => quoteTrimEvidence(source), [source]);
  const [selection, setSelection] = useState<QuoteRange | null>(() => pendingTrim ?? (source ? { start: source.start, end: source.end } : null));
  const [takes, setTakes] = useState<QuoteTake[] | null>(null);
  const [takeError, setTakeError] = useState('');
  const [wave, setWave] = useState<QuoteWave | null>(null);
  const [waveError, setWaveError] = useState('');
  const [retry, setRetry] = useState(0);
  const [anchor, setAnchor] = useState<number | null>(null);
  const wordGroup = useRef<HTMLDivElement>(null);
  const buttons = useRef(new Map<number, HTMLButtonElement>());
  const drag = useRef<{ pointer: number; anchor: number; previous: QuoteRange | null; previousAnchor: number | null; moved: boolean } | null>(null);
  const range = selection ?? (source ? { start: source.start, end: source.end } : null);
  const conflict = !!pendingTake || toNarration;
  const trimDisabled = disabled || conflict || !!evidence.reason;
  const problem = range ? quoteRangeProblem(source, range) : evidence.reason;
  const changed = !!source && !!range && (range.start !== source.start || range.end !== source.end);
  const queued = !!pendingTrim && !!range && pendingTrim.start === range.start && pendingTrim.end === range.end;
  const bins = useMemo(() => wave ? quoteWaveBins(wave) : [], [wave]);
  const chosenTake = pendingTake && takes?.find(t => t.take_id === pendingTake.take_id);
  const person = props.speakers.find(p => p.id === (chosenTake || source)?.speaker_id);
  const personDraft = person && Object.prototype.hasOwnProperty.call(props.pendingSpeakers, person.id) ? props.pendingSpeakers[person.id] : undefined;
  const personNumber = person ? props.speakers.indexOf(person) + 1 : 0;
  const nameProblem = person ? speakerFieldProblem(personDraft?.name ?? person.name, 'name', personNumber) : '';
  const titleProblem = person ? speakerFieldProblem(personDraft?.title ?? person.title, 'title', personNumber) : '';

  useEffect(() => {
    setSelection(pendingTrim ?? (source ? { start: source.start, end: source.end } : null));
    setAnchor(null); drag.current = null;
  }, [row.sentence_id, revision, source, pendingTrim]);
  useEffect(() => {
    const controller = new AbortController();
    setTakes(null); setTakeError(''); setWave(null); setWaveError('');
    void api.takes(row.sentence_id, controller.signal).then(value => {
      if (!controller.signal.aborted) setTakes(value.filter(t => t.take_id !== source?.take_id));
    }).catch(error => { if (!controller.signal.aborted) setTakeError(failureText(error)); });
    if (source) void api.wave(row.sentence_id, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.end <= source.start || value.start >= source.end) throw new Error('波形与这段原声的时间不对应，未显示波形。');
      setWave(value);
    }).catch(error => { if (!controller.signal.aborted) setWaveError(failureText(error)); });
    return () => controller.abort();
  }, [api, row.sentence_id, revision, source, retry]);

  const chooseWords = (first: number, last: number) => {
    if (!source || trimDisabled) return;
    const next = quoteRangeForWords(source, first, last);
    if (next) setSelection(next);
  };
  const beginDrag = (event: PointerEvent<HTMLButtonElement>, index: number) => {
    if (trimDisabled || !event.isPrimary || event.button !== 0) return;
    event.preventDefault(); event.currentTarget.focus();
    drag.current = { pointer: event.pointerId, anchor: index, previous: selection, previousAnchor: anchor, moved: false };
    chooseWords(anchor ?? index, index);
    wordGroup.current?.setPointerCapture(event.pointerId);
  };
  const moveDrag = (event: PointerEvent<HTMLDivElement>) => {
    const active = drag.current;
    if (!active || active.pointer !== event.pointerId || trimDisabled) return;
    const target = event.currentTarget.ownerDocument.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLButtonElement>('[data-quote-word]');
    if (target && wordGroup.current?.contains(target)) {
      const index = Number(target.dataset.quoteWord);
      if (index !== active.anchor) active.moved = true;
      if (active.moved) { chooseWords(active.anchor, index); setAnchor(null); }
    }
  };
  const nudge = (edge: 'start' | 'end', direction: -1 | 1) => {
    if (source && range && !trimDisabled) { setSelection(nudgeQuoteRange(source, range, edge, direction)); setAnchor(null); }
  };
  const rangeKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (trimDisabled || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === '[' || event.key === '{') { event.preventDefault(); event.stopPropagation(); nudge('start', event.shiftKey ? -1 : 1); }
    if (event.key === ']' || event.key === '}') { event.preventDefault(); event.stopPropagation(); nudge('end', event.shiftKey ? 1 : -1); }
  };
  const wordKeys = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.altKey || event.ctrlKey || event.metaKey || trimDisabled) return;
    let next: number;
    if (event.key === 'ArrowLeft') next = Math.max(0, index - 1);
    else if (event.key === 'ArrowRight') next = Math.min(evidence.words.length - 1, index + 1);
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = evidence.words.length - 1;
    else return;
    event.preventDefault(); event.stopPropagation();
    if (event.shiftKey) { const first = anchor ?? index; setAnchor(first); chooseWords(first, next); }
    buttons.current.get(next)?.focus();
  };

  return <div className="gm-quote-editor" role="group" aria-label="原声句编辑">
    <section className="gm-rw-edit-group" aria-labelledby={`${id}-trim`}>
      <h3 id={`${id}-trim`}>① 剪短这句 <small>· 点要保留的词 · 只能剪短，不能改字</small></h3>
      <p className="gm-rw-muted" id={`${id}-help`}>只能剪短，不能改字或重录。拖过要保留的词，或依次点起点和终点；中间的词会一起保留。</p>
      {source && <p className="gm-quote-source">实际原话：{source.asr_text || '当前保留片段的转写尚未提供'}<br /><small>原素材 {clock(source.start)}–{clock(source.end)} · 相似程度 {Math.round(source.score * 100)}%</small></p>}
      {source && <button type="button" disabled={disabled} onClick={props.onListen}>听这句原声</button>}
      {evidence.reason ? <p className="gm-rw-warning" role="status">{evidence.reason}</p> : <>
        <div ref={wordGroup} className="gm-quote-words" role="group" aria-label="选择要保留的连续原话" aria-describedby={`${id}-help ${id}-keys`}
          onKeyDown={rangeKeys} onPointerMove={moveDrag} onPointerUp={event => {
            if (drag.current?.pointer === event.pointerId) {
              moveDrag(event);
              const active = drag.current;
              setAnchor(active.moved || active.previousAnchor !== null ? null : active.anchor);
              drag.current = null;
            }
          }} onPointerCancel={() => {
            if (drag.current) { setSelection(drag.current.previous); setAnchor(drag.current.previousAnchor); }
            drag.current = null;
          }}
          onLostPointerCapture={() => { drag.current = null; }}>
          {evidence.words.map((word, index) => <button type="button" key={`${index}-${word.s}`} data-quote-word={index}
            ref={element => { if (element) buttons.current.set(index, element); else buttons.current.delete(index); }}
            disabled={trimDisabled} aria-pressed={!!range && word.s >= range.start && word.e <= range.end}
            aria-label={`${word.w}，原素材 ${clock(word.s)} 到 ${clock(word.e)}`}
            onPointerDown={event => beginDrag(event, index)} onKeyDown={event => wordKeys(event, index)}
            onClick={event => {
              // Pointer drag/tap is already handled above. Keyboard/assistive
              // clicks use the same explicit first/last pair.
              if (event.detail !== 0 || trimDisabled) return;
              chooseWords(anchor ?? index, index); setAnchor(anchor === null ? index : null);
            }}>{word.w}</button>)}
        </div>
        <p className="gm-rw-muted" id={`${id}-keys`}>键盘：方向键移到词，按住 Shift 扩选；[ 收起开头、] 收起结尾，Shift 配合方括号恢复到原范围内。每次约 0.2 秒，吸附真实词边界。</p>
        {anchor !== null && <p role="status" className="gm-rw-muted">已选起点，再点一个词确定终点。</p>}
      </>}
      {wave && <figure className="gm-quote-wave">
        <svg viewBox={`0 0 ${Math.max(bins.length, 1)} 80`} preserveAspectRatio="none" role="img" aria-label="原声真实采样波形，已缩略">
          {bins.map((bin, index) => <rect key={index} x={index} y={40 - bin.amplitude * 40} width="0.75" height={bin.amplitude * 80}
            className={range && bin.start >= range.start && bin.end <= range.end ? 'is-kept' : ''} />)}
        </svg>
        <figcaption>原声真实波形 · {clock(wave.start)}–{clock(wave.end)}；波形仅供参考，剪切按词边界。</figcaption>
      </figure>}
      {source && !wave && !waveError && <p className="gm-rw-muted" role="status">正在读取原声波形…</p>}
      {waveError && <p className="gm-rw-warning" role="alert">{waveError}</p>}
      {range && <p className="gm-quote-range" aria-live="polite">保留 {clock(range.start)}–{clock(range.end)} · {(range.end - range.start).toFixed(2)} 秒</p>}
      {conflict && <p className="gm-rw-warning">已记下{toNarration ? '改成旁白' : '换一段'}。要剪短，请先撤销该项；不能同时提交。</p>}
      {range && !evidence.reason && <>
        <div className="gm-quote-nudges" role="group" aria-label="按真实词边界微调原声">
          {(['start', 'end'] as const).map(edge => { const direction = edge === 'start' ? -1 : 1; return <button type="button" key={edge} disabled={trimDisabled || !source || nudgeQuoteRange(source, range, edge, direction) === range}
              aria-label={`${edge === 'start' ? '起点' : '终点'}${direction === -1 ? '提前' : '延后'}约零点二秒，吸附词边界`}
              onClick={() => nudge(edge, direction)}>{direction === -1 ? '◁ 0.2s' : '0.2s ▷'}</button>; })}
        </div>
        {problem && <p role="status" className="gm-rw-warning">{problem}</p>}
        <div className="gm-rw-actions"><button type="button" className="gm-rw-primary" disabled={trimDisabled || !!problem || !changed || queued}
          onClick={() => { if (source && range && !quoteRangeProblem(source, range)) props.onTrim(range); }}>记下剪短</button>
          <button type="button" disabled={disabled} onClick={() => { props.onTrim(undefined); setSelection(source ? { start: source.start, end: source.end } : null); setAnchor(null); }}>算了</button>
        </div>
      </>}
      {pendingTrim && <p role="status" className="gm-rw-preference-saved">已记下剪短，尚未改动成片。<button type="button" disabled={disabled} onClick={() => props.onTrim(undefined)}>撤销剪短</button></p>}
    </section>

    <section className="gm-rw-edit-group" aria-labelledby={`${id}-takes`}>
      <h3 id={`${id}-takes`}>② 换一段</h3>
      <p className="gm-rw-muted">下面是素材里实际找到的其他片段；采用后按这一段真正说的话出字幕。</p>
      {takes === null && !takeError && <p role="status">正在找其他原声片段…</p>}
      {takeError && <p role="alert" className="gm-rw-warning">{takeError} 未替换为示例片段。</p>}
      {takes?.length === 0 && <p className="gm-rw-muted">没有找到其他可换的原声片段。</p>}
      {pendingTrim && <p className="gm-rw-warning">已记下剪短；换段或转旁白前，请先撤销剪短。</p>}
      <div className="gm-quote-takes">{takes?.map(take => <button type="button" key={take.take_id} aria-pressed={pendingTake?.take_id === take.take_id}
        disabled={disabled || !!pendingTrim || toNarration} onClick={() => props.onTake(pendingTake?.take_id === take.take_id ? undefined : take)}>
        <strong>{take.asr_text}</strong><span>{clock(take.start)}–{clock(take.end)} · {(take.end - take.start).toFixed(2)} 秒 · 相似程度 {Math.round(take.score * 100)}%</span>
        <small>{props.speakers.find(p => p.id === take.speaker_id)?.name || '受访者'}{take.precision === 'segment' ? ' · 只有整段时间，换段后也不能按词剪短' : ''}</small>
      </button>)}</div>
      {pendingTake && <p className="gm-rw-preference-saved" role="status">已记下换段{chosenTake ? `：${chosenTake.asr_text}` : '，正在核对片段'}<button type="button" disabled={disabled} onClick={() => props.onTake(undefined)}>撤销换段</button></p>}
      {(takeError || waveError) && <button type="button" disabled={disabled} onClick={() => setRetry(value => value + 1)}>重新读取原声资料</button>}
      {props.mode === 'mixed' && <button type="button" className="gm-quote-convert" aria-pressed={toNarration}
        disabled={disabled || !!pendingTrim || !!pendingTake} onClick={() => props.onToNarration(!toNarration)}>{toNarration ? '撤销改成旁白' : '改成旁白（AI 读）'}</button>}
      {toNarration && <p role="status" className="gm-rw-warning">本次只记下转为旁白。应用完成后，才能再改字、录音或换画面；不会把配音当成受访者原话。</p>}
    </section>

    {person ? <section className="gm-rw-edit-group gm-quote-person" aria-labelledby={`${id}-speaker`}>
      <h3 id={`${id}-speaker`}>说话人</h3>
      {/* Do not truncate pasted/restored drafts or apply a UTF-16 maxlength. */}
      <div className="gm-quote-speaker-fields"><label>姓名<input data-quote-speaker-name type="text" disabled={disabled}
        aria-invalid={!!nameProblem} aria-describedby={`${id}-name-help`}
        placeholder="未填写时显示受访者" value={personDraft?.name ?? person.name} onChange={e => props.onSpeaker(person.id, { name: e.target.value })} /></label>
        <label>身份<input type="text" disabled={disabled} placeholder="例如：市集主办方" value={personDraft?.title ?? person.title}
          aria-invalid={!!titleProblem} aria-describedby={`${id}-title-help`}
          onChange={e => props.onSpeaker(person.id, { title: e.target.value })} /></label></div>
      <p id={`${id}-name-help`} className={nameProblem ? 'gm-rw-warning' : 'gm-rw-muted'} aria-live="polite">
        {nameProblem || `姓名最多 ${SPEAKER_FIELD_LIMITS.name} 字，可以留空。`}</p>
      <p id={`${id}-title-help`} className={titleProblem ? 'gm-rw-warning' : 'gm-rw-muted'} aria-live="polite">
        {titleProblem || `身份最多 ${SPEAKER_FIELD_LIMITS.title} 字，可以留空。`}</p>
      <p className="gm-rw-muted">{person.appearances} 次出现 · {person.seconds.toFixed(1)} 秒。名字和身份只记入待应用，不会立即改动片中人名条。</p>
    </section> : <p className="gm-rw-muted">{source?.speaker_id ? '尚未读到这位说话人的资料，请刷新后填写；不会猜测人物身份。' : '未提供说话人身份，不能编造人名条。'}</p>}
  </div>;
}