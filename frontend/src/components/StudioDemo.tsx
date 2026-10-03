import { useState } from 'react';
import { MODE_LABELS } from '../lib/productionModes';
import type { ProductionMode } from '../lib/productionModes';
import type { Row } from '../lib/workbenchApi';

export interface StudioDemoProps {
  title?: string;
  mode: ProductionMode;
  revision: number;
  rows: readonly Row[];
  loading?: boolean;
  error?: string;
  onBack: () => void;
  onRefresh: () => void;
}

const measured = (value: number | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const clock = (seconds: number) => {
  const hundredths = Math.round(seconds * 100);
  return `${Math.floor(hundredths / 6000)}:${(hundredths % 6000 / 100).toFixed(2).padStart(5, '0')}`;
};
const isQuote = (row: Row, mode: ProductionMode) => row.kind === 'quote'
  || row.kind === undefined && !row.to_narration && (!!row.source || mode === 'original');
const audioLabel = (row: Row, mode: ProductionMode) => isQuote(row, mode) ? '原声'
  : row.audio_source === 'recording' ? '我的配音' : row.audio_kind === 'sync' ? '现场声音' : '旁白';

/** Presentation only. No requests, media loading, timers, storage, project API,
 * autosave, exports or generated playback. Even selection stays in this view.
 * The host supplies a read-only report, never an editable Studio project.
 */
export default function StudioDemo({ title, mode, revision, rows, loading, error, onBack, onRefresh }: StudioDemoProps) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = rows.find(row => row.sentence_id === selectedId) ?? rows[0];
  const selectedIndex = selected ? rows.indexOf(selected) : -1;
  const total = rows.reduce((sum, row) => sum + (measured(row.duration) ? row.duration : 0), 0);
  const quotes = rows.filter(row => isQuote(row, mode)).length;
  const ready = !loading && !error;
  return <section className="shell-studio-demo" aria-labelledby="studio-demo-title">
    <header className="shell-demo-heading"><div><span className="shell-eyebrow">只读演示</span><h1 id="studio-demo-title">专业剪辑台</h1><p>{title || '当前作品'}</p></div>
      <span className="shell-demo-badge">演示·不会修改成片</span>
    </header>
    <div className="shell-notice" role="note">这里只展示当前作品的模式、句子与时间轨示意。点选句子仅改变本页视图，不保存工程、不重新制作、不导出，也不调用专业剪辑服务。实际修改请返回结果页。</div>
    <div className="shell-actions"><span className="shell-mode-badge" data-mode={mode}>{MODE_LABELS[mode]}</span><span>{revision === 0 ? '初版' : `第 ${revision} 次修改`}</span>
      <button type="button" onClick={onBack}>← 返回结果页</button><button type="button" onClick={onRefresh} disabled={loading}>重新读取句子</button>
    </div>
    {loading && <p className="shell-panel" role="status">正在只读获取已保存的句子报告，不会创建剪辑工程。</p>}
    {error && <p className="shell-error" role="alert">{error} 未显示旧版本的句子，请重新读取或返回结果页。</p>}
    {ready && !rows.length && <div className="shell-panel shell-empty"><span className="shell-empty-mark" aria-hidden="true">☷</span><p>还没有可显示的句子资料。不会用示例内容冒充这条作品。</p></div>}
    {ready && rows.length > 0 && <>
      <div className="shell-demo-summary">{rows.length} 句 · 原声 {quotes} 段 · 旁白或其他声音 {rows.length - quotes} 段 · 报告句长合计 {clock(total)}</div>
      <div className="shell-demo-columns">
        <section className="shell-panel shell-demo-sentences" aria-labelledby="studio-demo-sentences"><h2 id="studio-demo-sentences">句子清单</h2>
          <ol>{rows.map((row, index) => <li key={row.sentence_id}><button type="button" aria-pressed={selected?.sentence_id === row.sentence_id} onClick={() => setSelectedId(row.sentence_id)}>
            <span className="shell-demo-row-number">{index + 1}</span><span className="shell-demo-row-text">{row.sentence}</span>
            <span className="shell-demo-row-meta"><span className="shell-audio-badge" data-quote={isQuote(row, mode)}>{audioLabel(row, mode)}</span><span>{clock(row.duration)}</span></span>
          </button></li>)}</ol>
        </section>
        {selected && <section className="shell-panel shell-demo-inspector" aria-labelledby="studio-demo-selected"><h2 id="studio-demo-selected">第 {selectedIndex + 1} 句</h2>
          <span className="shell-audio-badge" data-quote={isQuote(selected, mode)}>{audioLabel(selected, mode)}</span><p className="shell-demo-quote">{selected.sentence}</p>
          <dl><dt>句长</dt><dd>{clock(selected.duration)}</dd><dt>成片时间</dt><dd>{measured(selected.start) && measured(selected.end) && selected.end > selected.start
            ? `${clock(selected.start)}–${clock(selected.end)}` : '报告未提供，不由句长推算'}</dd>
          {selected.source && <><dt>原素材区间</dt><dd>{clock(selected.source.start)}–{clock(selected.source.end)}（不是成片时间）</dd><dt>实际原话</dt><dd>{selected.source.asr_text}</dd></>}</dl>
          <p className="shell-demo-note">{isQuote(selected, mode) ? '原声只能依据真实出处处理；这里不改字、不剪短，也不合成新声音。' : '这里不能改稿、换镜头或录音；请在结果页记下修改后统一应用。'}</p>
        </section>}
      </div>
      <section className="shell-panel shell-demo-timeline" aria-labelledby="studio-demo-timeline"><h2 id="studio-demo-timeline">句子时间轨 · 示意</h2>
        <p id="studio-demo-clock-note">宽度按报告中的句长示意，并保留最小点击宽度；不包含句间间隙，不是成片的精确剪辑时间线。没有模拟波形或播放进度。</p>
        <div className="shell-demo-track-region" role="region" aria-label="只读句子时间轨，可横向滚动" aria-describedby="studio-demo-clock-note" tabIndex={0}>
          <ol className="shell-demo-track">{rows.map((row, index) => <li key={row.sentence_id} data-quote={isQuote(row, mode)} style={{ flexGrow: row.duration, flexBasis: total > 0 ? `${row.duration / total * 100}%` : `${100 / rows.length}%` }}>
            <button type="button" aria-label={`查看第 ${index + 1} 句，${audioLabel(row, mode)}，${clock(row.duration)}`} aria-pressed={selected?.sentence_id === row.sentence_id} onClick={() => setSelectedId(row.sentence_id)}>
              <strong>{index + 1} · {audioLabel(row, mode)}</strong><span>{row.sentence}</span><small>{clock(row.duration)}</small>
            </button>
          </li>)}</ol>
        </div>
      </section>
    </>}
  </section>;
}