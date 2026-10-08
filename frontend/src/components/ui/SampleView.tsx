import { useEffect, useRef, useState } from 'react';
import { AppApiError, publicRequest } from '../../lib/appApi';
import { parseReport } from '../../lib/workbenchApi';
import type { Report } from '../../lib/workbenchApi';
import styles from './Primitives.module.css';
import './uiSampleView.css';

export type SampleMode = 'voiceover' | 'mixed' | 'original';
export interface SampleSource { index: number; name: string; duration: number; usedBy: number[]; video: string; thumb: string }
/** How the sample was made: the same inputs a user gives in the real wizard. */
export interface SampleWalkthrough { script: string; mode: SampleMode; settings: { label: string; value: string }[]; sources: SampleSource[] }
export interface SampleData { title: string; report: Report; video: string | null; walkthrough: SampleWalkthrough | null }
export const SAMPLE_MODE_LABEL: Record<SampleMode, string> = { voiceover: 'AI 配音', mixed: '旁白 + 原声', original: '原声' };
const SAMPLE_MEDIA = /^\/api\/samples\/default\/sources\/(\d{2})\.(mp4|jpg)$/;
const text = (v: unknown, max: number): v is string => typeof v === 'string' && !!v.trim() && v.length <= max;

function parseWalkthrough(value: unknown, sentenceIds: Set<number>): SampleWalkthrough | null {
  if (value === undefined || value === null) return null;
  if (!object(value) || !text(value.script, 4000) || !['voiceover', 'mixed', 'original'].includes(value.mode as string)
    || !Array.isArray(value.settings) || value.settings.length > 12 || !Array.isArray(value.sources)
    || !value.sources.length || value.sources.length > 20) throw new Error('范例制作步骤格式不受支持。');
  const settings = value.settings.map(item => {
    if (!object(item) || !text(item.label, 20) || !text(item.value, 80)) throw new Error('范例效果设置格式不受支持。');
    return { label: item.label, value: item.value };
  });
  const sources = value.sources.map((item, position) => {
    const number = String(position + 1).padStart(2, '0');
    if (!object(item) || item.index !== position + 1 || !text(item.name, 80) || typeof item.duration !== 'number'
      || !Number.isFinite(item.duration) || item.duration <= 0 || !Array.isArray(item.used_by)
      || item.used_by.some(id => typeof id !== 'number' || !sentenceIds.has(id))
      || typeof item.video_url !== 'string' || SAMPLE_MEDIA.exec(item.video_url)?.[1] !== number || !item.video_url.endsWith('.mp4')
      || typeof item.thumb_url !== 'string' || SAMPLE_MEDIA.exec(item.thumb_url)?.[1] !== number || !item.thumb_url.endsWith('.jpg')) {
      throw new Error('范例素材地址不受支持。');
    }
    return { index: position + 1, name: item.name, duration: item.duration, usedBy: item.used_by as number[], video: item.video_url, thumb: item.thumb_url };
  });
  return { script: value.script, mode: value.mode as SampleMode, settings, sources };
}
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
    && (new RegExp(`^/api/tasks/${value.task_id}/thumbs/[0-9]+\\.jpg$`).test(url) || /^\/api\/samples\/default\/thumbs\/[0-9]{1,4}\.jpg$/.test(url));
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
  const walkthrough = parseWalkthrough(value.walkthrough, new Set(report.rows.map(row => row.sentence_id)));
  return { title: value.title, report, video, walkthrough };
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
            {item.thumb_url && <img src={item.thumb_url} alt="" loading="lazy" className="gm-sample-thumb" />}
            <strong>第 {index + 1} 句</strong><span>{item.sentence}</span>
            <small>{item.start !== undefined && item.end !== undefined ? `${clock(item.start)} – ${clock(item.end)}` : '成片时间未提供'}</small>
          </button></li>)}</ol>
        {!data.report.rows.length && <p>报告尚未提供句子。</p>}
      </section>
      <section id="gm-sample-sentence" aria-label="所选句子，只读" className="gm-sample-detail">
        <h2>所选句子 · 只读</h2>
        {row ? <>{row.thumb_url && <img src={row.thumb_url} alt={`第 ${data.report.rows.indexOf(row) + 1} 句用的画面`} className="gm-sample-picture" />}
          <p>{row.sentence}</p><dl>
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

const seconds = (value: number) => `${value.toFixed(1)} 秒`;
const sentenceList = (ids: number[], rows: Report['rows']) => ids.map(id => rows.findIndex(row => row.sentence_id === id) + 1)
  .filter(n => n > 0).map(n => `第 ${n} 句`).join('、');

export interface SampleTry { script: string; mode: SampleMode; files: File[] }

/** The same five steps as a real production, filled with what this sample actually used. */
export function SampleSteps({ data }: { data: SampleData & { walkthrough: SampleWalkthrough } }) {
  const { walkthrough, report } = data;
  const lines = walkthrough.script.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const total = walkthrough.sources.reduce((sum, item) => sum + item.duration, 0);
  return <ol className="gm-sample-steps">
    <li><h2><span aria-hidden="true">1</span>写稿</h2>
      <p>选的是「{SAMPLE_MODE_LABEL[walkthrough.mode]}」。第一行是标题，之后每行一句；这条范例共 {lines.length - 1} 句。</p>
      <div className="gm-sample-script" aria-label="范例稿件">{lines.map((line, index) => index === 0
        ? <p key={index} className="gm-sample-script-title">{line}</p>
        : <p key={index}><b>{index}</b>{line}</p>)}</div>
    </li>
    <li><h2><span aria-hidden="true">2</span>传素材</h2>
      <p>传了 {walkthrough.sources.length} 段手机拍的视频，一共约 {Math.round(total)} 秒。系统会自己看懂每段拍了什么。</p>
      <ul className="gm-sample-sources">{walkthrough.sources.map(item => <li key={item.index}>
        <img src={item.thumb} alt="" loading="lazy" />
        <span>{item.name}</span>
        <small>{seconds(item.duration)} · {item.usedBy.length ? `用在${sentenceList(item.usedBy, report.rows)}` : '这次没用上'}</small>
      </li>)}</ul>
    </li>
    <li><h2><span aria-hidden="true">3</span>选效果</h2>
      {walkthrough.settings.length ? <dl className="gm-sample-settings">{walkthrough.settings.map(item => <div key={item.label}>
        <dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl> : <p>全部用默认效果。</p>}
    </li>
    <li><h2><span aria-hidden="true">4</span>点“开始制作”，等它自动做完</h2>
      <p>依次：检查素材 → 看懂画面 → 给每句话找画面 → AI 配音 → 配字幕 → 合成视频 → 质量检查。做的时候可以离开页面，回来在“我的作品”里找。</p>
    </li>
    <li><h2><span aria-hidden="true">5</span>看成片，逐句核对</h2>
      <p>点下面任意一句，视频会跳到那里；可以看到这句用了哪段画面、为什么选它。自己的作品在这一步还能改字、换画面、导出。</p>
    </li>
  </ol>;
}

export default function SampleView({ onBack, onTry }: { onBack: () => void; onTry?: (value: SampleTry) => void }) {
  const [retry, setRetry] = useState(0);
  const [data, setData] = useState<SampleData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [preparing, setPreparing] = useState('');
  const tryController = useRef<AbortController | null>(null);
  useEffect(() => () => tryController.current?.abort(), []);
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
  const walkthrough = data?.walkthrough ?? null;
  async function tryIt() {
    if (!walkthrough || !onTry || preparing) return;
    const controller = new AbortController(); tryController.current = controller;
    setError('');
    try {
      const files: File[] = [];
      for (const item of walkthrough.sources) {
        setPreparing(`正在取范例素材 ${item.index}/${walkthrough.sources.length}…`);
        const response = await fetch(item.video, { signal: controller.signal, credentials: 'same-origin', cache: 'no-store' });
        if (!response.ok) throw new Error('missing');
        files.push(new File([await response.blob()], item.name, { type: 'video/mp4' }));
      }
      if (!controller.signal.aborted) onTry({ script: walkthrough.script, mode: walkthrough.mode, files });
    } catch {
      if (!controller.signal.aborted) setError('范例素材没能取回来，请稍后再试；也可以直接“返回写稿”用自己的素材。');
    } finally {
      if (!controller.signal.aborted) setPreparing('');
    }
  }
  return <section className={styles.sample} aria-label="范例作品，只读">
    <p className={styles.readonly}>范例作品 · 只读</p><h1>{data?.title ?? '看一条范例作品'}</h1>
    {walkthrough
      ? <p>这条成片是用下面这份稿子和 {walkthrough.sources.length} 段素材，按和你自己做时完全一样的 5 步做出来的。看完可以点“用这份范例自己做一遍”，亲手走一遍。</p>
      : <p>这里只查看服务器提供的范例；不保存检查勾选、不改稿、不复制或导出，也不写入你的作品历史。</p>}
    {loading && <p role="status">正在读取已核验范例…</p>}
    {error && <p role="alert">{error}</p>}
    {walkthrough && onTry && <div className={styles.actions}><button type="button" className={styles.button} data-primary="true"
      disabled={!!preparing} onClick={() => void tryIt()}>用这份范例自己做一遍</button></div>}
    {data && walkthrough && <SampleSteps data={{ ...data, walkthrough }} />}
    {data && <SampleReview key={retry} data={data} />}
    {preparing && <p role="status">{preparing}</p>}
    <div className={styles.actions}>
      {walkthrough && onTry && <button type="button" className={styles.button} data-primary="true" disabled={!!preparing} onClick={() => void tryIt()}>用这份范例自己做一遍</button>}
      <button type="button" className={styles.button} data-primary={walkthrough && onTry ? undefined : 'true'} onClick={onBack}>返回写稿</button>
      <button type="button" className={styles.button} disabled={loading} onClick={() => setRetry(value => value + 1)}>重新读取范例</button></div>
    {walkthrough && onTry && <p className="gm-sample-note">“自己做一遍”会把这份稿子和 {walkthrough.sources.length} 段素材放进一份新草稿，从第 1 步开始，真实上传和制作，会占用一次制作次数。</p>}
  </section>;
}
