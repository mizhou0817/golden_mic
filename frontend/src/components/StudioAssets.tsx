import { useEffect, useRef, useState } from "react";
import { STUDIO_ASSET_LIMITS, isImageSource, validateStudioAssetFile } from "../lib/studioApi";
import type {
  ProjectEnvelope, SourceCatalog, StudioApi, StudioAssetCatalog, StudioAssetKind, StudioAssetLimits,
  StudioAssetReceipt, StudioImageAsset, StudioLutAsset,
} from "../lib/studioApi";

export interface StudioAssetsState {
  status: "unavailable" | "loading" | "ready" | "error";
  catalog: StudioAssetCatalog | null;
  message: string;
}
export const EMPTY_STUDIO_ASSETS: StudioAssetsState = { status: "unavailable", catalog: null, message: "" };
export type StudioAssetImportResult =
  | { kind: "image"; receipt: StudioAssetReceipt<StudioImageAsset> }
  | { kind: "lut"; receipt: StudioAssetReceipt<StudioLutAsset> };

/** The parent MUST run this inside its shared operation mutex. The save receipt,
 * not a predicted +1 or the displayed revision, supplies expected_revision.
 * This function never retries, installs a project, creates clips or plays media. */
export async function importStudioAsset({ api, kind, file, limits, onDirtySave, isCurrent }: {
  api: Pick<StudioApi, "importImage" | "importLut">; kind: StudioAssetKind; file: File;
  limits: Readonly<StudioAssetLimits>; onDirtySave: () => Promise<Pick<ProjectEnvelope, "revision">>;
  isCurrent: () => boolean;
}): Promise<StudioAssetImportResult> {
  const check = () => { if (!isCurrent()) throw new Error("编辑器已切换，未采用旧素材导入结果；请重新读取当前任务目录。"); };
  check(); validateStudioAssetFile(kind, file, limits);
  const current = await onDirtySave();
  check();
  const result: StudioAssetImportResult = kind === "image"
    ? { kind, receipt: await api.importImage(current.revision, file) }
    : { kind, receipt: await api.importLut(current.revision, file) };
  check();
  return result;
}

/** A real ID selector shared by the panel and clip inspector. Unknown bindings
 * remain visible and can be cleared; failed loading never silently selects none. */
export function StudioLutPicker({ value, state, available, disabled, onChange, label = "片段 LUT" }: {
  value: string | null; state: StudioAssetsState; available: boolean; disabled: boolean;
  onChange: (id: string | null) => void; label?: string;
}) {
  const rows = state.catalog?.luts ?? [];
  const known = rows.some(row => row.id === value);
  return <div className="gm-studio-lut-picker" data-lut-id={value ?? ""}>
    <label className="gm-studio-field"><span>{label}</span>
      <select aria-label={label} value={value ?? ""} data-lut-id={value ?? ""}
        disabled={disabled || !available || state.status !== "ready"}
        onChange={event => {
          const id = event.target.value;
          if (!id || rows.some(row => row.id === id)) onChange(id || null);
        }}>
        <option value="">不使用 LUT</option>
        {value && !known && <option value={value}>未知 / 尚未加载的 LUT · {value}</option>}
        {rows.map(row => <option key={row.id} value={row.id} data-lut-id={row.id}>{row.name} · {row.size}³ · {row.id}</option>)}
      </select>
    </label>
    <button type="button" disabled={disabled || value === null} onClick={() => onChange(null)}>清除 LUT 绑定</button>
    {value && (!known || state.status !== "ready") && <p role="alert" className="gm-studio-warning">
      当前 LUT「{value}」{state.status === "ready" ? "未在此任务素材目录中找到" : "尚不能由当前目录确认"}。
      已保留原 ID，不会当作无 LUT 或替换成预设；请刷新目录、重新绑定或明确清除后再渲染。
    </p>}
    {state.status === "error" && <p className="gm-studio-warning">LUT 目录读取失败：{state.message} 可在本地素材面板手动刷新。</p>}
    {!available && <p className="gm-studio-note">服务器未声明 LUT 能力；不加载或伪造 LUT 选项。已有绑定仍保留，可明确清除。</p>}
    <p className="gm-studio-note">只绑定本任务已导入的真实 LUT ID，不把源视频当作 LUT。修改是草稿参数，保存并真实渲染后才有画面效果。</p>
  </div>;
}

/** ReferenceLocalCollection contains only server-returned, task-local images.
 * No fabricated stock catalog, data URLs, browser persistence or external media. */
export function ReferenceLocalCollection({ api, catalog, images, busy, selectedId, onSelectImage }: {
  api: Pick<StudioApi, "sourceUrl">; catalog: SourceCatalog; images: readonly StudioImageAsset[];
  busy: boolean; selectedId: string; onSelectImage: (id: string) => void;
}) {
  return <section className="gm-studio-local-collection" aria-label="本任务已导入贴纸" data-asset-collection="ReferenceLocalCollection">
    <h3>本地收藏 · 已导入图片 / 贴纸</h3>
    {!images.length && <p className="gm-studio-note">尚未导入图片。这里只列出本任务真实文件，没有预装贴纸库。</p>}
    <div className="gm-studio-asset-grid">{images.map(image => {
      const source = catalog.sources.find(item => item.id === image.id && isImageSource(item));
      return <article key={image.id} className="gm-studio-asset-card" data-image-id={image.id}>
        <img src={api.sourceUrl(image.id)} alt={`原始图片 · ${image.name}`} loading="lazy" decoding="async" referrerPolicy="no-referrer" />
        <div><strong>{image.name}</strong><small>{image.width} × {image.height} · {(image.bytes / 1048576).toFixed(2)} MiB · PNG</small><small>{image.id}</small></div>
        <button type="button" disabled={busy || !source} aria-pressed={selectedId === image.id}
          onClick={() => { if (source) onSelectImage(image.id); }}>选择图片（不自动加入时间线）</button>
        {!source && <p className="gm-studio-note">源目录尚未包含此图片，请刷新素材目录；未创建任何片段。</p>}
      </article>;
    })}</div>
  </section>;
}

export interface StudioAssetsProps {
  api: Pick<StudioApi, "sourceUrl">; catalog: SourceCatalog; state: StudioAssetsState;
  savedRevision: number; busy: boolean; dirty: boolean; imageAvailable: boolean; lutAvailable: boolean;
  selectedSourceId: string; selectedLutId: string | null; canUseLut: boolean; canAnimateImage: boolean;
  onImport: (kind: StudioAssetKind, file: File) => Promise<boolean>;
  onCatalogRefresh: () => void; onSelectImage: (id: string) => void; onUseLut: (id: string | null) => void;
  onAnimateImage: () => void;
}

/** Files stay only in this mounted form's memory. All I/O and catalog state are
 * owned by Studio, using the SAME mutex / saveDraft flow as timeline operations. */
export default function StudioAssets({ api, catalog, state, savedRevision, busy, dirty, imageAvailable, lutAvailable,
  selectedSourceId, selectedLutId, canUseLut, canAnimateImage, onImport, onCatalogRefresh, onSelectImage, onUseLut, onAnimateImage }: StudioAssetsProps) {
  const [files, setFiles] = useState<Partial<Record<StudioAssetKind, File>>>({});
  const [fileError, setFileError] = useState("");
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const limits = state.catalog?.limits ?? STUDIO_ASSET_LIMITS;
  const ready = state.status === "ready";
  const enabled = imageAvailable || lutAvailable;
  const upload = async (kind: StudioAssetKind) => {
    const file = files[kind];
    if (!file || busy || !ready || !(kind === "image" ? imageAvailable : lutAvailable)) return;
    try {
      validateStudioAssetFile(kind, file, limits); setFileError("");
      if (await onImport(kind, file) && live.current) setFiles(old => ({ ...old, [kind]: undefined }));
    } catch (error) { if (live.current) setFileError(error instanceof Error ? error.message : "未能导入素材；请刷新目录确认后再提交。"); }
  };
  return <section className="gm-studio-panel gm-studio-assets" data-studio-section="assets" tabIndex={-1} aria-label="本地图片与 LUT 素材" aria-busy={busy || state.status === "loading"}>
    <style>{ASSET_CSS}</style>
    <div className="gm-studio-section-title"><div><span className="gm-studio-eyebrow">TASK-LOCAL COLLECTION</span><h2>本地图片 / LUT</h2></div>
      <button type="button" disabled={busy || state.status === "loading" || !enabled} onClick={onCatalogRefresh}>刷新素材目录</button>
    </div>
    <p className="gm-studio-note">仅导入此任务的私有素材；不是发布、导出或云端生成。导入前{dirty ? "将先保存当前草稿，并使用保存回执的真实修订" : `核对已保存工程 r${savedRevision}`}。
      导入本身不推进工程修订；文件名由服务器生成，不发送本地文件名。原成片、报告、QC 与发布门禁均不改变。</p>
    {!enabled && <p className="gm-studio-warning">当前服务器未声明 stickerCustom / lut 能力，素材端点未加载，上传保持禁用。</p>}
    {state.status === "loading" && <p role="status">正在读取服务器素材目录…</p>}
    {state.status === "error" && <p role="alert" className="gm-studio-warning">{state.message} 未自动重试上传。请先刷新目录确认已有素材。</p>}
    {fileError && <p role="alert" className="gm-studio-warning">{fileError}</p>}
    <div className="gm-studio-asset-forms">{(["image", "lut"] as const).map(kind => <form key={kind} onSubmit={event => { event.preventDefault(); void upload(kind); }}>
      <fieldset disabled={busy || !ready || !(kind === "image" ? imageAvailable : lutAvailable)}>
        <legend>{kind === "image" ? "导入图片 / 自定义贴纸" : "导入 .cube LUT"}</legend>
        <label className="gm-studio-field"><span>{kind === "image" ? "本地 PNG / JPEG 文件" : "本地 .cube 文本文件"}</span>
          <input type="file" aria-label={kind === "image" ? "本地 PNG / JPEG 文件" : "本地 .cube 文本文件"} data-asset-upload={kind}
            accept={kind === "image" ? ".png,.jpg,.jpeg,image/png,image/jpeg" : ".cube,text/plain"}
            onChange={event => {
              const file = event.currentTarget.files?.[0]; event.currentTarget.value = "";
              setFiles(old => ({ ...old, [kind]: undefined })); setFileError("");
              if (!file) return;
              try { validateStudioAssetFile(kind, file, limits); setFiles(old => ({ ...old, [kind]: file })); }
              catch (error) { setFileError(error instanceof Error ? error.message : "文件不符合要求，未上传。"); }
            }} />
        </label>
        <p className="gm-studio-note">{kind === "image" ? `PNG / JPEG ≤${limits.image_bytes / 1048576} MiB；单边 ≤${limits.dimension}px，总像素 ≤${limits.pixels}。服务器解码后保存为标准 PNG。`
          : `.cube ≤${limits.lut_bytes / 1048576} MiB；3D 尺寸上限 ${limits.lut_size}。内容由服务器严格解析；不是预设、滤镜脚本或在线地址。`}</p>
        <p className="gm-studio-note">{files[kind] ? `待导入：${files[kind]!.name} · ${files[kind]!.size} 字节（仅本地显示）` : "尚未选择文件"}</p>
        <button type="submit" className="gm-studio-primary" disabled={!files[kind]}>保存草稿并导入{kind === "image" ? "图片" : " LUT"}</button>
      </fieldset>
    </form>)}</div>
    <p className="gm-studio-note">已读取 {state.catalog ? state.catalog.images.length + state.catalog.luts.length : "—"} / {limits.assets} 项（图片 + LUT 合计）。
      重复文件是否去重及剩余额度由服务器确认。只有成功回执才更新目录；超时 / 冲突不会自动重发 POST。</p>
    <ReferenceLocalCollection api={api} catalog={catalog} images={state.catalog?.images ?? []} busy={busy || !ready}
      selectedId={selectedSourceId} onSelectImage={onSelectImage} />
    <p className="gm-studio-note">图片只有显示时长，没有源播放时钟。选择图片后，请明确选择视频 / 叠加目标轨、插入位置与显示时长，再点击加入；导入 / 选择本身不添加片段。</p>
    <div className="gm-studio-row"><button type="button" disabled={busy || !canAnimateImage} onClick={onAnimateImage}>编辑已选图片片段的关键帧</button>
      <button type="button" disabled title="自动跟踪未实现">自动跟踪（未支持）</button></div>
    <p className="gm-studio-note">图片支持普通变换、Alpha、形状蒙版和 x / y / scale / opacity 关键帧；不自动生成动画、不自动播放，无实时效果预览。保存并真实渲染后检查结果。</p>
    <section className="gm-studio-asset-luts" aria-label="已导入 LUT 与片段绑定"><h3>真实 LUT · 当前任务</h3>
      {!state.catalog?.luts.length && <p className="gm-studio-note">尚无已读取的 LUT。图片 / 视频源不作为 LUT 选项。</p>}
      <ul>{state.catalog?.luts.map(lut => <li key={lut.id} data-lut-id={lut.id}><strong>{lut.name}</strong> · {lut.size}³ · {lut.bytes} 字节<small>{lut.id}</small></li>)}</ul>
      {!canUseLut && <p className="gm-studio-note">先选择未锁定的视频、叠加或调整轨片段，再绑定 LUT；导入 LUT 不会自动改变任何片段。</p>}
      <StudioLutPicker value={selectedLutId} state={state} available={lutAvailable} disabled={busy || !canUseLut}
        label="素材面板 · 当前片段 LUT" onChange={onUseLut} />
    </section>
  </section>;
}

const ASSET_CSS = `
.gm-studio .gm-studio-assets{background:var(--gm-surface);border-color:var(--gm-line);min-inline-size:0}
.gm-studio .gm-studio-asset-forms{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:12px 0}
.gm-studio .gm-studio-asset-forms>form{min-inline-size:0;max-inline-size:100%;border:1px solid var(--gm-line);border-radius:12px;padding:12px;background:var(--gm-paper)}
.gm-studio .gm-studio-assets :is(form,fieldset,label,input,select,button){min-inline-size:0;max-inline-size:100%;overflow-wrap:anywhere}
.gm-studio .gm-studio-assets input[type=file]{inline-size:100%;white-space:normal;padding:6px}
.gm-studio .gm-studio-assets input[type=file]::file-selector-button{font:inherit;color:var(--gm-ink);background:var(--gm-cream);border:1px solid var(--gm-line);border-radius:6px;padding:6px;max-inline-size:100%;white-space:normal}
.gm-studio .gm-studio-asset-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,15rem),1fr));gap:12px;margin:10px 0}
.gm-studio .gm-studio-asset-card{min-inline-size:0;border:1px solid var(--gm-line);border-radius:12px;padding:10px;display:flex;flex-direction:column;align-items:stretch;gap:8px;background:var(--gm-paper)}
.gm-studio .gm-studio-asset-card img{display:block;inline-size:100%;max-inline-size:100%;block-size:9rem;object-fit:contain;border:1px solid var(--gm-line);border-radius:8px;background:var(--gm-cream)}
.gm-studio .gm-studio-asset-card strong{font-size:.875rem;overflow-wrap:anywhere}.gm-studio .gm-studio-asset-card button{margin-top:auto;white-space:normal}
.gm-studio .gm-studio-asset-luts{border-top:1px solid var(--gm-line);margin-top:16px;padding-top:12px}.gm-studio .gm-studio-asset-luts li{margin:6px 0;overflow-wrap:anywhere}
.gm-studio .gm-studio-lut-picker{min-inline-size:0;max-inline-size:100%}.gm-studio .gm-studio-lut-picker select{max-inline-size:100%;min-inline-size:0}
@media(max-width:600px){.gm-studio .gm-studio-asset-forms{grid-template-columns:minmax(0,1fr)}.gm-studio .gm-studio-assets button,.gm-studio .gm-studio-lut-picker button{min-block-size:44px;white-space:normal}}
`;