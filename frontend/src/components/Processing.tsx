import type { UploadProgress } from '../lib/appApi';
import { describeFailure, redactFailureText } from '../lib/failureDetail';
import { isProductionMode, MODE_LABELS, STAGE_DEFINITIONS } from '../lib/productionModes';
import type { ProductionMode } from '../lib/productionModes';
import type { StageState, TaskStatusResponse } from '../types';
import styles from './Processing.module.css';

type DisplayState = StageState | 'unknown';
const stateLabels: Record<DisplayState, string> = { pending: '等待', running: '进行中', done: '已完成', failed: '未完成', unknown: '尚未收到阶段信息' };
const measured = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const percent = (value: number) => Math.min(100, Math.max(0, value));
const seconds = (value: number) => value < 60 ? `${value.toFixed(1)} 秒` : `${Math.floor(Math.round(value) / 60)} 分 ${Math.round(value) % 60} 秒`;
const bytes = (value: number) => `${(value / 1048576).toFixed(1)} 兆字节`;
export const RECOVERY_UNAVAILABLE = '暂不能把已提交作品恢复为可编辑的新草稿。原任务和当前草稿都没有改动；请保留原任务，使用原始稿件和素材新建作品。';
const operations: Record<string, string> = {
  initial: '初版', delete: '删句', edit: '改字', voice: '录音', pacing: '调整语速', trim: '剪短原声',
  take: '换一段原声', to_narration: '改成旁白', speakers: '修改说话人', render: '合成视频', replace_shot: '换画面',
};
const operationLabel = (op: string) => Object.prototype.hasOwnProperty.call(operations, op) ? operations[op] : '应用修改';

function Microphone({ active }: { active: boolean }) {
  return <span className={styles.microphone} aria-hidden="true" data-animated={active}>
    <svg width="40" height="40" viewBox="0 0 40 40" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="14.5" y="6" width="11" height="17" rx="5.5" fill="currentColor" />
      <path d="M10 18a10 10 0 0 0 20 0M20 28v5M15 33h10" />
      <g className={styles.wave}><path d="M6 12v10" /></g><g className={styles.wave}><path d="M3 14v6" /></g>
      <g className={styles.wave}><path d="M34 12v10" /></g><g className={styles.wave}><path d="M37 14v6" /></g>
    </svg>
  </span>;
}

export interface ProcessingProps {
  task: TaskStatusResponse | null; upload: UploadProgress | null; uploading: boolean;
  error?: string; now: number;
  title?: string; mode?: ProductionMode;
  busy: boolean; onCancel: () => void;
  /** Explicit same-task retry only; the host rechecks support/state before POST. */
  onRetry: (options?: { allowShotReuse?: boolean }) => void;
  onEdit?: (step?: 1 | 2, mode?: ProductionMode, options?: { allowShotReuse?: boolean }) => void; onRefresh: () => void; onNew?: () => void;
}
export default function Processing({ task, upload, uploading, error, now, title, mode: requestedMode, busy, onCancel, onRetry, onEdit, onRefresh, onNew }: ProcessingProps) {
  const mode = isProductionMode(task?.mode) ? task.mode : isProductionMode(requestedMode) ? requestedMode : 'voiceover';
  const stages = STAGE_DEFINITIONS[mode];
  const failed = !uploading && (task?.status === 'failed' || task?.status === 'cancelled');
  const cancelled = !uploading && task?.status === 'cancelled';
  const queued = !uploading && task?.status === 'queued';
  const running = !uploading && task?.status === 'running' && !error;
  const kind = failed && !cancelled ? task?.errorKind : null;
  // A known failure plus an error string means the last ACTION failed (e.g. recovery); only without a known failure is the connection in doubt.
  const connectionUncertain = !!error && !failed;
  const badRows = [...new Set((Array.isArray(task?.badRows) ? task.badRows : []).filter(row => Number.isSafeInteger(row) && row > 0 && row <= 100_000))].slice(0, 2048);
  const uploadPercent = upload && measured(upload.total) && upload.total > 0 && measured(upload.loaded)
    ? percent(upload.loaded / upload.total * 100) : undefined;
  const taskPercent = task && measured(task.progress) ? percent(task.progress) : undefined;
  const progress = uploading ? uploadPercent : taskPercent;
  // Overall percentage already includes server weights. No local stage sums,
  // countdown, elapsed-based progress, or prototype ETA.
  const currentNumber = !uploading ? task?.current_stage ?? task?.current : null;
  const currentDefinition = stages.find(stage => stage.n === currentNumber);
  // TaskManager.queue is one-based within the waiting queue, not a count ahead.
  const queue = queued && Number.isSafeInteger(task.queue) && (task.queue ?? 0) > 0 ? task.queue : undefined;
  const plan = task?.plan;
  const planIndex = plan && Number.isSafeInteger(plan.idx) && plan.idx >= 0 && plan.idx < plan.steps.length ? plan.idx : null;
  const operation = planIndex !== null && plan ? operationLabel(plan.steps[planIndex].op) : '应用修改';
  const headline = connectionUncertain ? '状态需要确认' : uploading ? upload?.finishedAt != null ? '素材已传完，等待服务器确认' : '正在上传素材'
    : queued ? queue !== undefined ? `队列前面还有 ${queue - 1} 个作品` : '已提交，等待服务器处理'
      : cancelled ? '制作已取消' : failed ? kind === 'quote_missing' ? badRows.length ? `有 ${badRows.length} 句原话在素材里没找到` : '有原话在素材里没找到'
        : currentDefinition ? `停在了“${currentDefinition.name}”` : '这次没做成'
        : task?.status === 'done' ? '做好了' : running && currentDefinition
          ? `${plan && planIndex !== null && plan.steps.length > 1 ? `第 ${planIndex + 1}/${plan.steps.length} 步 · ${operation}：` : task?.revision ? '正在重新制作：' : 'AI 剪辑师正在：'}${currentDefinition.name}`
          : '正在读取制作状态';
  const doing = connectionUncertain ? '下方保留最后收到的阶段记录；连接失败不代表服务器制作已停止。'
    : uploading ? '上传完成前请别关这个页面；传输结束后还要等待服务器确认。'
      : queued ? '素材已提交。可以先去写下一篇，做好了会在“我的作品”里。'
        : failed ? '' : task?.status === 'done' ? '请核对成片，再决定是否导出。' : currentDefinition?.description ?? '等待服务器返回实际进度。';
  const retryableKind = !kind || kind === 'network' || kind === 'transient';
  const retryable = task?.status === 'failed' && retryableKind && (task.lifecycle_v2 ? task.retry_same_supported === true : task.revision === 0);
  const editPair = kind === 'qc' || kind === 'shortage' || kind === 'quote_missing';
  const editLabel = kind === 'shortage' ? '回去删减稿子' : badRows.length ? `回去改第 ${badRows.join('、')} 句` : '回去改稿子';
  const reason = kind === 'quote_missing' ? badRows.length ? `有 ${badRows.length} 句原话在素材里没找到。请核对第 ${badRows.join('、')} 句原话。` : '素材里没有找到稿子中的原话。'
    : kind === 'shortage' ? '不同的画面不够，没法给每句话配上不重复的镜头。'
      : kind === 'qc' ? '这次的画面、声音或字幕没有通过质量检查。'
        : kind === 'network' ? '制作时连接中断了。' : kind === 'transient' ? '制作服务暂时没能完成这一步。'
          : task?.status === 'cancelled' ? '制作已取消。' : '制作没有完成，请先核对状态。';
  const advice = cancelled ? '已取消的作品暂不支持恢复为新草稿。可用原始稿件和素材新建作品；不会自动重试或重新提交。'
    : kind === 'quote_missing' ? `把那几句改成真说过的话，或者补传含这些原话的视频。${mode === 'mixed' ? '也可以明确改成旁白。' : '不会用配音补上没找到的原话。'}`
    : kind === 'shortage' ? '删减稿子，或者多传几个不同场景的视频。'
      : kind === 'qc' ? '新闻里不能用不相关的画面充数。把那句改成拍到的东西，或者删掉它。'
        : task?.lifecycle_v2 ? retryable ? '再试一次会先核验已完成的步骤，从未完成处继续；不重新上传，也不新增提交次数。' : '当前不能安全续作；不会自动重试或重新提交。'
          : retryable ? '重新制作会从第一步运行，可能再次产生费用；确认后才会提交。' : '不会自动重试或替你改稿。';
  // Say where it stopped and why, in plain words; the raw server text stays one click away.
  const failure = failed && !cancelled ? describeFailure(task) : { stage: '', detail: '' };
  const stoppedAt = failure.stage || (failed && !cancelled ? currentDefinition?.name ?? '' : '');
  const technical = failure.detail;
  const cause = !failed || cancelled || kind === 'quote_missing' || kind === 'shortage' || kind === 'qc' ? ''
    : kind === 'network' ? '这是网络连接中断造成的，不是稿子或素材的问题。'
      : '这是制作服务这一步出了错，多数不是稿子或素材的问题，往往再试一次就能解决；反复出现时请把下面的技术详情告诉维护人员。';
  // Not enough distinct shots: the user may accept repeated footage (same scene, different moments)
  // and continue the same task from the matching step. Never automatic; the host asks first.
  const canReuseShots = failed && !cancelled && kind === 'shortage' && task?.lifecycle_v2 === true && mode !== 'original';
  const canResumeWithReuse = canReuseShots && task?.retry_same_supported === true;
  // Only offered where original sound could be what failed; AI voiceover needs no source speech.
  const canFallBackToVoiceover = failed && !cancelled && mode !== 'voiceover' && !!onEdit;
  const stageLabel = (state: DisplayState) => state === 'running' && failed ? '已停止 · 上次进行到此'
    : state === 'running' && error ? '上次状态：进行中，等待重新连接'
      : state === 'running' && !running ? '阶段状态待刷新' : stateLabels[state];
  return <section className={styles.processing} data-screen-label="制作中" data-testid="v2-processing" data-state={uploading ? 'uploading' : task?.status ?? 'unknown'} data-connection={error ? 'uncertain' : 'current'} aria-labelledby="processing-title">
    <header className={styles.heading}><h1 id="processing-title" title={title}>{title || '新闻视频制作'}</h1>
      {/* Internal revisions can advance more than once per apply; not an edit count. */}
      {task && <span className={styles.version}>{task.revision === 0 ? '初版' : `版本 ${task.revision}`}</span>}
    </header>
    <div className={styles.progressCard} data-state={failed ? 'failed' : uploading ? 'uploading' : task?.status ?? 'unknown'}>
      <div className={styles.progressCopy}>
        <div className={styles.figure}><Microphone active={(running || uploading) && !error} /><strong className={styles.big} data-testid="processing-big">{queued ? '排队' : progress === undefined ? '—' : `${Math.round(progress)}%`}</strong></div>
        <div className={styles.copy} role="status"><h2>{headline}</h2><p>{doing}</p>
          {!error && (running || queued) && plan && planIndex !== null && planIndex < plan.steps.length - 1 && <p className={styles.next} data-testid="processing-plan">接下来：{plan.steps.slice(planIndex + 1).map(step => operationLabel(step.op)).join(' → ')}</p>}
          {import.meta.env.DEV && <p className={styles.technical} data-testid="processing-dev">阶段 {currentNumber ?? '—'}/10 · 服务器进度 {taskPercent ?? '—'} · 内部修订 {task?.revision ?? '—'}</p>}
        </div>
      </div>
      <div className={styles.progress} role="progressbar" aria-label={uploading ? '上传阶段进度' : error ? '上次收到的制作进度' : '服务器制作进度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-valuetext={progress === undefined ? '尚未收到进度' : undefined}><div style={{ width: `${progress ?? 0}%` }} /></div>
      {!error && (running || queued) && <p className={styles.reassurance}>通常需要几分钟。可以先去做别的——做好后会出现在左边「我的作品」里。</p>}
    </div>
    {(failed || error) && <div className={styles.failure} role="alert" data-error-kind={kind ?? (error ? 'connection' : 'unknown')}>
      <h2>{cancelled ? '制作已取消' : failed ? '这次没做成' : '状态连接需要确认'}</h2>
      {failed && <><p>{reason}</p>
        {stoppedAt && !cancelled && <p className={styles.cause} data-testid="processing-cause">停在了“{stoppedAt}”这一步。{cause}</p>}
        <p className={styles.advice}>{advice}</p>
        {technical && <details className={styles.details} data-testid="processing-technical"><summary>技术详情（反馈问题时请附上）</summary><pre>{stoppedAt ? `阶段：${stoppedAt}\n` : ''}原因：{technical}</pre></details>}</>}
      {error && (error === RECOVERY_UNAVAILABLE ? <p>{RECOVERY_UNAVAILABLE}</p>
        : failed ? <p className={styles.actionError} data-testid="processing-action-error">刚才的操作没成功：{redactFailureText(error)}</p>
          : <p>暂时无法确认最新状态。下方是最后收到的记录，请先重新读取；不会自动重试制作。</p>)}
      {canReuseShots && <div className={styles.alternative} data-testid="processing-reuse">
        <h3>替代方案：允许画面重复出现</h3>
        <p>素材里互不相同的画面不够，没法让每句话都配上不同的镜头。可以让同一段素材在不同时刻重复出现（同一场景的不同片段，不是定格补帧）；素材实在不够时，会循环使用这些真实片段，并优先用最相关的画面，保证能做完。从这一步继续，前面已完成的步骤不重做。</p>
        <p>点击后会先让你确认，确认后才会继续（可能产生少量费用）。不想重复画面，请用下面的按钮回去删减稿子或多传素材。</p>
        <div className={styles.actions}>
          {canResumeWithReuse && <button type="button" className={styles.primary} data-control="processing-reuse-shots" onClick={() => onRetry({ allowShotReuse: true })} disabled={busy || connectionUncertain}>接受重复画面并继续…</button>}
          {onEdit && <button type="button" data-control="processing-reuse-restart" onClick={() => onEdit(1, undefined, { allowShotReuse: true })} disabled={busy}>用同样的稿子和素材重新开始，并允许重复画面…</button>}
        </div>
        <p className={styles.advice}>“接受并继续”会从“给每句话找画面”接着做，前面的步骤不重做；若程序已更新、这个作品不能续作，请用“重新开始”，会先保存成一份新草稿，由你确认后再提交。</p>
      </div>}
      {canFallBackToVoiceover && <div className={styles.alternative} data-testid="processing-alternative">
        <h3>临时替代方案：改用 AI 配音</h3>
        <p>如果问题出在素材原声上，可以用同一份稿子和素材改成“AI 配音”：由 AI 读稿，素材只出画面，不依赖原声。</p>
        <p>点击后只会把作品恢复成一份新草稿，由你确认后才会重新提交制作（可能再次产生费用），原作品不变。不想用，可选择下面其他按钮。</p>
        <button type="button" data-control="processing-switch-voiceover" onClick={() => onEdit?.(1, 'voiceover')} disabled={busy}>使用替代方案：改用 AI 配音继续</button>
      </div>}
      <div className={styles.actions}>
        {error && <button type="button" data-control="processing-refresh" onClick={onRefresh} disabled={busy}>重新读取状态</button>}
        {failed && editPair && onEdit && <><button type="button" className={styles.primary} data-control="processing-edit-script" onClick={() => onEdit(1)} disabled={busy}>{editLabel}</button><button type="button" data-control="processing-edit-materials" onClick={() => onEdit(2)} disabled={busy}>回去多传素材</button></>}
        {failed && !cancelled && !editPair && <>{retryable && <button type="button" className={styles.primary} data-control="processing-retry" onClick={() => onRetry()} disabled={busy || connectionUncertain}>{task?.lifecycle_v2 ? '再试一次' : '用原素材重新制作'}</button>}{onEdit && <button type="button" data-control="processing-edit-script" onClick={() => onEdit(1)} disabled={busy}>回去改稿子</button>}</>}
        {cancelled && onNew && <button type="button" className={styles.primary} data-control="processing-new" onClick={onNew} disabled={busy}>新作品</button>}
        {failed && <button type="button" className={styles.danger} data-control="processing-delete" onClick={onCancel} disabled={busy}>删除这个作品</button>}
      </div>
    </div>}
    {upload && <div className={styles.upload} data-testid="processing-upload"><span className={styles.mark} aria-hidden="true">{upload.finishedAt != null ? '✓' : '↑'}</span><div><strong>上传素材</strong>
      <p>{measured(upload.loaded) ? bytes(upload.loaded) : '已传大小待确认'}{measured(upload.total) ? ` / ${bytes(upload.total)}` : ''} · {upload.finishedAt != null ? '传输结束，接收状态以服务器为准' : '正在传输'}</p>
      <progress aria-label="素材整体上传进度" max={100} value={uploadPercent} />
    </div><span className={styles.time}>{measured(upload.startedAt) && measured(upload.finishedAt ?? now) ? seconds(Math.max(0, ((upload.finishedAt ?? now) - upload.startedAt) / 1000)) : ''}</span></div>}
    <ol className={styles.stages} aria-label={`${MODE_LABELS[mode]}的十步制作阶段`} data-testid="processing-stages">{stages.map(({ n, name }) => {
        const stage = !uploading ? task?.stages.find(item => item.number === n) : undefined;
        const state: DisplayState = stage?.status ?? (uploading ? 'pending' : 'unknown');
        return <li key={n} data-stage={n} data-state={state} data-animated={state === 'running' && running} aria-current={currentNumber === n ? 'step' : undefined}>
          <span className={styles.mark} aria-hidden="true"><span>{state === 'done' ? '✓' : state === 'failed' ? '✕' : state === 'running' ? running ? '◠' : 'Ⅱ' : n}</span></span>
          <div className={styles.stageCopy}><span>{name}</span><span className={styles.srOnly}> · {stageLabel(state)}</span>
            {import.meta.env.DEV && <p className={styles.technical}>阶段 {n} · {stageLabel(state)}{measured(stage?.weight) && stage.weight <= 1 ? ` · 服务端权重 ${stage.weight}` : ''}</p>}</div>
          <span className={styles.time}>{state === 'done' && measured(stage?.elapsed_seconds) ? seconds(stage.elapsed_seconds) : ''}</span>
        </li>;
      })}</ol>
    {!failed && task?.status !== 'done' && <footer className={styles.footer}><p>{uploading ? '停止上传不等于撤销服务器已接收的作品；停止后请核对“我的作品”。' : '可以先去做别的，回来在“我的作品”里找它。取消并删除无法撤销。'}</p>
      <button type="button" className={styles.danger} data-control="processing-cancel" onClick={onCancel} disabled={busy}>{uploading ? '停止上传' : '取消并删除'}</button></footer>}
  </section>;
}