import { useEffect, useRef, useState } from 'react';
import { AppApiError, publicRequest } from '../../lib/appApi';
import { parseReport } from '../../lib/workbenchApi';
import type { Report } from '../../lib/workbenchApi';
import styles from './Primitives.module.css';
import './uiSampleView.css';

export interface SampleData { title: string; report: Report; video: string | null }
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
/** Public sample data cannot promote a task into an owned selection or import a
 * capability. Media is restricted to the dedicated same-origin sample route. */
export function parseSample(value: unknown): SampleData {
  if (!object(value) || value.read_only !== true || typeof value.task_id !== 'string'
      || !/^[A-Za-z0-9_-]{1,128}$/.test(value.task_id) || typeof value.title !== 'string'
      || !value.title.trim() || value.title.length > 512 || !object(value.report)
      || !Array.isArray(value.report.rows) || value.report.rows.length > 2048) throw new Error('范例数据格式不受支持；没有加载可编辑作品。');
  const report = parseReport(value.report, value.task_id);
  // The packaged API strips thumbnails. Accept legacy task-relative references
  // only when bound to this report; never fetch private task media from a sample.
  const thumbnail = (url: unknown) => url == null || typeof url === 'string'
    && new RegExp(`^/api/tasks/${value.task_id}/thumbs/[0-9]+\\.jpg$`).test(url);
  let previousEnd = 0;
  for (const row of report.rows) {
    if (!thumbnail(row.thumb_url) || row.visual_beats.some(beat => !thumbnail(beat.thumb_url))
      || typeof row.description !== 'string' || row.visual_beats.some(beat => typeof beat.description !== 'string')
      || row.audio_source !== undefined && !['tts', 'sync', 'recording'].includes(row.audio_source)) {
      throw new Error('范例画面资料不完整或地址不匹配。');
    }
    if (row.start !== undefined || row.end !== undefined) {
      if (typeof row.start !== 'number' || !Number.isFinite(row.start) || row.start < previousEnd
        || typeof row.end !== 'number' || !Number.isFinite(row.end) || row.end <= row.start || row.end > 604800) {
        throw new Error('范例成片时间不完整或顺序不匹配。');
      }
      previousEnd = row.end;
    }
  }
  let video: string | null = null;
  if (value.video_url !== undefined && value.video_url !== null) {
    if (typeof value.video_url !== 'string' || !/^\/api\/samples\/default\/video(?:\?revision=\d+)?$/.test(value.video_url)) throw new Error('范例媒体地址不受支持。');
    video = value.video_url;
    const revision = /\?revision=(\d+)$/.exec(video)?.[1];
    if (revision !== undefined && (!Number.isSafeInteger(Number(revision))
      || report.revision !== undefined && report.revision !== Number(revision))) {
      throw new Error('范例媒体版本与报告不匹配。');
    }
  }
  return { title: value.title, report, video };
}

const clock = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toFixed(2).padStart(5, '0')}`;

/** No workbench API, persistence, edit dialogs or export authority is created. */
export function SampleReview({ data }: { data: SampleData }) {
  const [selected, setSelected] = useState(data.report.rows[0]?.sentence_id ?? null);
  const [mediaError, setMediaError] = useState(false);
  const [notice, setNotice] = useState('');
  const video = useRef<HTMLVideoElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const row = data.report.rows.find(item => item.sentence_id === selected);
  const seek = (start: number) => {
    const player = video.current;
    if (!player || mediaError) return;
    if (player.readyState === 0) { pendingSeek.current = start; return; }
    pendingSeek.current = null;
    if (!Number.isFinite(player.duration) || start >= player.duration) {
      setNotice('这句的时间超出可播放媒体，未跳转；请重新读取范例。'); return;
    }
    player.currentTime = start;
    setNotice('');
  };
  const followPlayback = () => {
    const position = video.current?.currentTime;
    if (position === undefined) return;
    const active = data.report.rows.find(item => item.start !== undefined && item.end !== undefined
      && position >= item.start && position < item.end);
    if (active) setSelected(active.sentence_id);
  };
  return <div className="gm-sample-review">
    {data.video ? <video ref={video} controls controlsList="nodownload noremoteplayback" disablePictureInPicture
      preload="metadata" src={data.video} aria-label="范例成片"
      onTimeUpdate={followPlayback} onSeeked={followPlayback}
      onLoadedMetadata={() => { if (pendingSeek.current !== null) seek(pendingSeek.current); }}
      onError={() => { setMediaError(true); pendingSeek.current = null; }} />
      : <p>服务器未提供可播放的范例媒体；以下仅为只读句子报告。</p>}
    {mediaError && <p role="alert">范例媒体缺失或无法播放；仍可查看报告，但不能核对成片画面。未使用替代媒体。</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="gm-sample-panels">
      <section aria-label="范例分镜"><h2>逐句看范例</h2>
        <p>选择句子查看画面依据；有成片时间时可定位播放。不提供修改或导出。</p>
        <ol className="gm-sample-storyboard">{data.report.rows.map((item, index) => <li key={item.sentence_id}>
          <button type="button" aria-pressed={selected === item.sentence_id} aria-controls="gm-sample-sentence"
            onClick={() => { setSelected(item.sentence_id); setNotice(''); if (item.start !== undefined) seek(item.start); }}>
            <strong>第 {index + 1} 句</strong><span>{item.sentence}</span>
            <small>{item.start !== undefined && item.end !== undefined ? `${clock(item.start)} – ${clock(item.end)}` : '成片时间未提供'}</small>
          </button></li>)}</ol>
        {!data.report.rows.length && <p>报告尚未提供句子。</p>}
      </section>
      <section id="gm-sample-sentence" aria-label="所选句子，只读" className="gm-sample-detail">
        <h2>所选句子 · 只读</h2>
        {row ? <><p>{row.sentence}</p><dl>
          <dt>声音</dt><dd>{{ tts: 'AI 配音', sync: '现场原声', recording: '本人录音' }[row.audio_source ?? row.audio_kind]}</dd>
          <dt>成片时间</dt><dd>{row.start !== undefined && row.end !== undefined ? `${clock(row.start)} – ${clock(row.end)}` : '未提供；不根据句长推算'}</dd>
          <dt>画面描述</dt><dd>{row.description || '未提供'}</dd>
          <dt>匹配依据</dt><dd>{row.confidence == null ? '未提供置信度' : `${Math.round(row.confidence * 100)}%（模型匹配，非事实核实）`}{row.is_fallback ? ' · 备用画面' : ''}</dd>
        </dl>
          {row.visual_beats.length > 0 && <><h3>这一句的分镜</h3><ol>{row.visual_beats.map(beat => <li key={beat.beat_id}>
            <p>{beat.text}</p><p>{beat.description || '未提供画面描述'}</p>
          </li>)}</ol></>}
        </> : <p>请选择报告中的句子。</p>}
      </section>
    </div>
  </div>;
}

export default function SampleView({ onBack }: { onBack: () => void }) {
  const [retry, setRetry] = useState(0);
  const [data, setData] = useState<SampleData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController(); let current = true;
    setLoading(true); setError(''); setData(null);
    void publicRequest<unknown>('/api/samples/default', controller.signal).then(parseSample).then(value => {
      if (current && !controller.signal.aborted) setData(value);
    }).catch(failure => {
      if (current && !controller.signal.aborted) setError(failure instanceof AppApiError && [404, 503].includes(failure.status)
        ? '暂时没有可供查看的已核验范例。不会用演示数据代替真实成片。'
        : '范例未能安全读取，请稍后重试。未创建作品或调用制作服务。');
    }).finally(() => { if (current && !controller.signal.aborted) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [retry]);
  return <section className={styles.sample} aria-label="范例作品，只读">
    <p className={styles.readonly}>范例作品 · 只读</p><h1>{data?.title ?? '看一条范例作品'}</h1>
    <p>这里只查看服务器提供的范例；不保存检查勾选、不改稿、不复制或导出，也不写入你的作品历史。</p>
    {loading && <p role="status">正在读取已核验范例…</p>}
    {error && <p role="alert">{error}</p>}
    {data && <SampleReview key={retry} data={data} />}
    <div className={styles.actions}><button type="button" className={styles.button} data-primary="true" onClick={onBack}>返回写稿</button>
      <button type="button" className={styles.button} disabled={loading} onClick={() => setRetry(value => value + 1)}>重新读取范例</button></div>
  </section>;
}