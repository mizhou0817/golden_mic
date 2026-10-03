import { useId } from "react";
import { STUDIO_MAX_JOBS, STUDIO_PROXY_PROFILE, isActiveJob, isImageSource, isProxyJob, isRawProxySourceId, isRecord } from "../lib/studioApi";
import type { ProjectEnvelope, StudioApi, StudioJob, StudioProxyJob, StudioSource } from "../lib/studioApi";

export type StudioPreview = { kind: "source" | "job" | "proxy"; id: string };
export interface StudioProxiesState {
  status: "unavailable" | "loading" | "ready" | "error";
  message: string;
}
export const EMPTY_STUDIO_PROXIES: StudioProxiesState = { status: "unavailable", message: "" };
export const PROXY_EXPLANATION = "低分辨率源代理，不包含时间线效果；导出使用原片";
const STATES: Record<StudioJob["state"], string> = {
  queued: "排队中", running: "代理转码中", succeeded: "已完成", failed: "失败", cancelled: "已取消", interrupted: "服务重启中断",
};

/** Client preflight only. The server must authorize this catalog member, probe
 * RAW video/duration, and check source hash + pipeline revision on every read.
 * Browser proxy metadata is NEVER passed as originalDuration. */
export function proxySourceProblem(source: StudioSource | undefined, originalDuration?: number): string | null {
  if (!source) return "请先从源素材库选择本任务的原目录视频。";
  if (!isRawProxySourceId(source.id) || isImageSource(source)) {
    return "只支持原目录视频（原始上传 / 标准化视频 / 原成片）；图片、配音 / 录音和 Studio 输出不能作为源代理输入。";
  }
  if (source.has_video === false || /\.(mp3|wav|m4a|aac|ogg|flac|opus)$/i.test(source.name)) {
    return "源代理需要视频画面；纯音频 / 录音保持原有播放与编辑方式。";
  }
  if ([source.duration, originalDuration].some(duration => duration !== undefined
    && (!Number.isFinite(duration) || duration <= 0 || duration > STUDIO_PROXY_PROFILE.max_source_seconds))) {
    return "原始源文件须为 0–120 秒（不含 0）；选择较短入出点不会缩短代理输入，不自动截断长源。";
  }
  return null;
}

/** Deliberately independent of export approval. Only an already validated,
 * succeeded proxy of an existing RAW source can reach the private media route. */
export function canPreviewProxy(job: StudioJob, sources: readonly StudioSource[]): job is StudioProxyJob {
  return isProxyJob(job) && job.state === "succeeded"
    && proxySourceProblem(sources.find(source => source.id === job.source_id)) === null;
}

/** The caller owns the SAME operation mutex as save/import/render. No retries,
 * extra render options, source substitutions, project writes or implicit playback. */
export async function createStudioProxy({ api, source, originalDuration, onDirtySave, isCurrent }: {
  api: Pick<StudioApi, "createProxy">; source: StudioSource; originalDuration?: number;
  onDirtySave: () => Promise<Pick<ProjectEnvelope, "revision">>; isCurrent: () => boolean;
}): Promise<StudioProxyJob> {
  const check = () => { if (!isCurrent()) throw new Error("编辑器已切换；请重新读取代理状态，未自动重发创建请求。"); };
  check();
  const problem = proxySourceProblem(source, originalDuration);
  if (problem) throw new Error(problem);
  const current = await onDirtySave();
  check();
  const job = await api.createProxy(current.revision, source.id);
  check();
  return job;
}

/** Read only reported measurements; a target profile is not a successful probe.
 * Never put result.duration or these stream measurements in the source EOF map. */
export function proxyProfileReadout(job: StudioProxyJob): string {
  const target = `服务器参数：${job.options.format.toUpperCase()} · ${job.options.resolution}p · ${job.options.fps} FPS · ${job.options.aspect} · 视频 ${job.options.video_bitrate_kbps} kbps / 音频 ${job.options.audio_bitrate_kbps} kbps；固定目标 H.264 / AAC`;
  const probe = job.result?.probe;
  if (!isRecord(probe) || !Array.isArray(probe.streams)) return `${target}。尚无可读取的媒体实测信息。`;
  const video = probe.streams.find((stream: unknown) => isRecord(stream) && stream.codec_type === "video");
  const audio = probe.streams.find((stream: unknown) => isRecord(stream) && stream.codec_type === "audio");
  const measured: string[] = [];
  if (isRecord(video)) {
    if (typeof video.width === "number" && Number.isSafeInteger(video.width) && video.width > 0
      && typeof video.height === "number" && Number.isSafeInteger(video.height) && video.height > 0) measured.push(`${video.width} × ${video.height}`);
    if (typeof video.codec_name === "string" && video.codec_name.length <= 40) measured.push(video.codec_name);
    const rate = video.avg_frame_rate;
    if (typeof rate === "string" && /^\d+(?:\/\d+)?$/.test(rate)) {
      const [n, d = "1"] = rate.split("/"), fps = Number(n) / Number(d);
      if (Number.isFinite(fps) && fps > 0 && fps <= 240) measured.push(`${Number(fps.toFixed(6))} FPS`);
    }
  }
  if (isRecord(audio) && typeof audio.codec_name === "string" && audio.codec_name.length <= 40) measured.push(`音频 ${audio.codec_name}`);
  const rawDuration = isRecord(probe.format) ? probe.format.duration : undefined;
  const duration = typeof rawDuration === "number" ? rawDuration
    : typeof rawDuration === "string" && /^\d+(?:\.\d+)?$/.test(rawDuration) ? Number(rawDuration) : NaN;
  if (Number.isFinite(duration) && duration > 0) measured.push(`代理容器 ${duration.toFixed(6)}s（不是原片 EOF）`);
  return `${target}。${measured.length ? `服务器实测：${measured.join(" · ")}` : "尚无可读取的媒体实测信息"}。`;
}

export interface StudioProxyProps {
  source: StudioSource | undefined; jobs: readonly StudioJob[]; state: StudioProxiesState;
  preview: StudioPreview | null; available: boolean; capabilityReason?: string;
  busy: boolean; dirty: boolean; savedRevision: number; originalDuration?: number; draftProblem?: string;
  onCreate: () => void; onRefresh: () => void; onOriginal: () => void; onPreview: (jobId: string) => void;
}

/** Controlled library card. Rendering / reopening / changing the selected source
 * never POSTs or silently switches the monitor to a cached low-resolution file. */
export default function StudioProxy({ source, jobs, state, preview, available, capabilityReason, busy, dirty,
  savedRevision, originalDuration, draftProblem, onCreate, onRefresh, onOriginal, onPreview }: StudioProxyProps) {
  const id = useId();
  const rows = jobs.filter(isProxyJob).filter(job => job.source_id === source?.id);
  const completed = source ? rows.filter(job => canPreviewProxy(job, [source])) : [];
  const selected = preview?.kind === "proxy" ? rows.find(job => job.id === preview.id) : undefined;
  const originalSelected = preview?.kind === "source" && preview.id === source?.id;
  const choice = originalSelected ? "original" : selected?.id ?? "";
  const problem = proxySourceProblem(source, originalDuration);
  const active = jobs.some(isActiveJob);
  const full = jobs.length >= STUDIO_MAX_JOBS && completed.length === 0;
  const canCreate = available && !busy && state.status === "ready" && !active && !problem && !draftProblem && !full;
  const latest = selected ?? rows[0];
  return <section className="gm-studio-proxy" data-studio-section="proxy" tabIndex={-1}
    aria-labelledby={`${id}-title`} aria-busy={busy || state.status === "loading"} data-proxy-source={source?.id ?? ""}>
    <style>{PROXY_CSS}</style>
    <div className="gm-studio-section-title"><div><span className="gm-studio-eyebrow">RAW SOURCE / PRIVATE PREVIEW</span><h3 id={`${id}-title`}>低分辨率源代理</h3></div>
      <span className="gm-studio-badge gm-studio-gold">≤120s · 360p · 30 FPS</span></div>
    <p id={`${id}-help`} className="gm-studio-note">{PROXY_EXPLANATION}。不是时间线合成，也不代表发布 / 导出获准。</p>
    <p className="gm-studio-note">{source ? `${source.name} · ${source.id}` : "尚未选择素材"}</p>
    {!available && <p className="gm-studio-warning">服务器未声明可用的源代理能力，未请求代理端点。{capabilityReason}</p>}
    {problem && <p className="gm-studio-note">{problem}</p>}
    {state.status === "loading" && <p role="status">正在读取真实代理记录；不会自动创建或切换预览…</p>}
    {state.status === "error" && <p role="alert" className="gm-studio-warning">{state.message} 请先刷新代理状态；不会自动重发 POST。</p>}
    {state.status === "ready" && <p role="status" aria-live="polite">已读取该源 {rows.length} 个代理作业，{completed.length} 个完成记录。媒体读取仍由服务器核验源文件与成片修订。</p>}
    <label className="gm-studio-field"><span>此源的监看质量（手动选择）</span>
      <select aria-label="此源的监看质量" aria-describedby={`${id}-help ${id}-clock`} value={choice} disabled={busy || !source}
        onChange={event => {
          const value = event.target.value;
          if (value === "original") onOriginal();
          else if (available && state.status === "ready" && completed.some(job => job.id === value)) onPreview(value);
        }}>
        <option value="" disabled>请选择原片或已完成代理</option>
        <option value="original">原片 · 精确源入出点</option>
        {selected && !completed.some(job => job.id === selected.id) && <option value={selected.id} disabled>所选代理已不可用 · {STATES[selected.state]}</option>}
        {completed.map(job => <option key={job.id} value={job.id} disabled={!available || state.status !== "ready"}>
          源代理 · {job.options.resolution}p / {job.options.fps} FPS · {job.id.slice(0, 8)}{job.cached === true ? " · 服务器确认复用" : ""}
        </option>)}
      </select>
    </label>
    <div className="gm-studio-row">
      <button type="button" data-proxy-create className="gm-studio-primary" disabled={!canCreate} onClick={() => { if (canCreate) onCreate(); }}>
        {dirty ? "保存草稿并" : ""}{completed.length ? "核验 / 复用源代理" : "创建 / 复用源代理"}
      </button>
      <button type="button" data-proxy-refresh disabled={busy || !available || state.status === "loading"} onClick={onRefresh}>刷新代理状态</button>
      <button type="button" data-proxy-original disabled={busy || !source || originalSelected} onClick={onOriginal}>切回原片（精确记点）</button>
    </div>
    <p className="gm-studio-note">{dirty ? "提交前先保存草稿，使用真实保存回执的 expected_revision" : `提交前核对已保存工程 r${savedRevision}`}；代理本身不推进工程修订。
      新建 / 复用仅在点击后提交一次；完成后仍需手动选择。最多共 {STUDIO_MAX_JOBS} 个作业，失败 / 取消也计数。</p>
    {active && <p className="gm-studio-note">已有 Studio 作业排队 / 运行，暂不提交；可在“后台作业与输出”查看或取消真实作业。</p>}
    {full && <p className="gm-studio-note">已到达共同作业上限，未创建新代理。刷新可核对已有缓存，不自动清理作业。</p>}
    {draftProblem && <p className="gm-studio-warning">须先修正工程才能保存并提交：{draftProblem}</p>}
    {latest && <div className="gm-studio-proxy-receipt" data-proxy-job={latest.id}>
      <strong>真实作业：{STATES[latest.state]} · {latest.id.slice(0, 8)}</strong>
      {latest.error && <p className="gm-studio-warning">{latest.error}</p>}
      <p className="gm-studio-note">{proxyProfileReadout(latest)}</p>
      <small>捕获工程 r{latest.revision} / 成片 v{latest.pipeline_revision} · 缓存 {latest.cache_key}</small>
    </div>}
    <p id={`${id}-clock`} className="gm-studio-note">代理播放期间不能以当前帧记入出点，请切回原片精确记点。切换仅尝试保留源秒位置，不修改入出点；
      编码帧 / 容器时长可能略有差异，代理时长不会写入原片时长表。时间线始终使用原 source_id 和原始秒范围。</p>
    <p className="gm-studio-note">RAW 指原目录文件，不应用当前时间线裁剪、速度、调色、LUT、转场或混音；原文件已烧录的内容仍保留。
      与原片相同的私有读取授权，不是导出审批的替代路径。源文件变化、修订变化、失败或重启中断时保留真实状态，不假装可用、不自动重建。</p>
  </section>;
}

const PROXY_CSS = `
.gm-studio .gm-studio-proxy{margin-top:14px;padding:12px;border:2px solid var(--gm-gold);border-radius:12px;background:var(--gm-paper);color:var(--gm-ink);min-inline-size:0;max-inline-size:100%;overflow-wrap:anywhere}
.gm-studio .gm-studio-proxy :is(div,label,select,button,p,small){min-inline-size:0;max-inline-size:100%}.gm-studio .gm-studio-proxy .gm-studio-section-title{margin-bottom:8px}.gm-studio .gm-studio-proxy h3{margin:0;font-size:1rem}
.gm-studio .gm-studio-proxy select{inline-size:100%}.gm-studio .gm-studio-proxy .gm-studio-row{align-items:stretch}.gm-studio .gm-studio-proxy button{white-space:normal;min-block-size:44px;flex:1 1 10rem}.gm-studio .gm-studio-proxy-receipt{border-top:1px solid var(--gm-line);margin-top:10px;padding-top:10px;font-size:.8125rem}
@media(max-width:480px){.gm-studio .gm-studio-proxy .gm-studio-row{flex-direction:column}.gm-studio .gm-studio-proxy button{flex-basis:auto;inline-size:100%}}
`;