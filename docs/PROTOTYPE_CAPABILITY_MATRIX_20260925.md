> **历史矩阵 · 2026-09-27 范围变更：课堂/账号、独立云作业及旧 Canary/Round 2 入口已退役。** 下文 113 行分类、限额、C25/C26 测试数与源码链接保持当日记录，不表示当前全部工具已验证或实现。现行无登录范围与验证要求见 [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)；原型文件与原始证据不改写，历史原生崩溃风险不因清理而消失。

# C25 当前原型能力矩阵（2026-09-25）

> **C26 当前快照 · 2026-09-26：受限回归已结束，High 原生运行时风险未解决。** 后端 **1,148 tests：1,145 PASS、3 SKIP，623.067s，exit 0**；7 套 Node **260 PASS**；最终独立构建页面 **37/37**、深层 **69/69**（58 项真实本地后端 + 11 项明确模拟的启用态云契约，**非腾讯 live**）。第 8 节记录当前证据、原生崩溃和深层正常关闭证明缺口。**113 行、58 partial / 8 metadata / 11 renderer / 36 unsupported、全部限额与缺失子项不变**；绿色重跑不是所有 bug 已修或生产通过。

> **C25 历史快照 · 2026-09-26：有界实现与回归闭环完成。** 当轮后端 **1,121 tests：1,118 PASS、3 SKIP，937.139s，exit 0**；7 套 Node **238 PASS**；同一独立构建页面 **37/37**、深层 **63/63**（52 项真实本地后端 + 11 项明确模拟的启用态云契约，非腾讯 live）。全部 **113 个唯一需求 ID** 及缺失子项保留，历史失败见第 7 节；不是 113 项全部实现，也不是 77 个完整工具或生产上线。

## 1. 范围、原型版本与计数口径

- 唯一原型目录来源：[../金话筒新闻视频生成/金话筒 · 原型.dc.html](../金话筒新闻视频生成/金话筒%20·%20原型.dc.html) 的 `PRO_TABS` / **`PRO_DEF`**，不是旧课堂规格、旧 UI 清单或历史记忆中的支持数量。
- 2026-09-26 直接核对的暖色原型 SHA-256 仍为 `17b53e815e6f94847b383a2b7fb6cb2acf916cf50890f8563a982999b631a251`，与需求基线一致；见 [PROTOTYPE_IMPLEMENTATION_20260925.md](PROTOTYPE_IMPLEMENTATION_20260925.md)。它不是前端构建散列凭证。
- 原型为奶油纸色 `#f7efe2`、棕色 `#4a3626`、橄榄绿 `#557538`、金色 `#e2a83c`；当前全局令牌见 [../frontend/src/styles.css](../frontend/src/styles.css)。最终布局证据限已测视口/字体，不代表逐像素一致或全部响应式情境通过。
- 下表按原型顺序列出 **113 个不同 ID，恰好 113 行**：时间线 11、片段 14、多机位 5、音频 11、AI 11、特效 13、文字 15、蒙版 12、导出 10、工具 11。**`expHdr` 已包含在序号 096，不是漏项，也不另加为第 114 项。** 主流程说明、云作业种类和同一工具的子选项不增加 ID 数。
- 当前 `capabilities()` 与原型的 **113 个唯一 ID 集合精确相同，无缺项/多项**；分类为 **58 partial、8 metadata、11 renderer、36 unsupported**，不是完成率。`tlPick/nest/camNest/proxy` 均为受限 partial；不沿用旧报告“58 项未支持”，也不能把非 unsupported 的 77 行称为完整工具。

### 不可直接移植的原型行为

原型实现说明明确标注 AI / 后端为前端模拟：`SIM`、`tick()`、`runTask()`、样例 `ROWS` / `SHOT`、假播放、演示费用、`pro` 状态赋值和演示成功提示都不是生产实现。`localStorage` 演示任务不构成真实媒体备份或授权凭证；恢复旧演示存档时补出的同意信息不能移植为真实同意记录。

原型中的“无限轨道”“无限撤销”、固定容量与 72 小时清理文案也不是无上限服务承诺，必须服从实际服务端限额及作品期限。原型 §8 将班级、登录、名册等状态列为历史兼容内容；当前产品仍保留真实课堂与账号扩展及其安全边界，**不能因此删掉认证、CSRF、所有权或发布门禁，也不能以旧课堂功能充抵这 113 个工具**。

## 2. 源码索引与状态含义

入口列简称指现存实现；函数、字段和 UI 区域用于定位契约，不代表另有未展示的 API。

| 简称 | 现存源码 / 职责 |
| --- | --- |
| UI | [../frontend/src/components/Studio.tsx](../frontend/src/components/Studio.tsx)：`StudioEditor`、`toolControls()`、`openTool()`、`ClipInspector`、`TransitionEditor`、`ManualMulticamEditor` |
| API | [../frontend/src/lib/studioApi.ts](../frontend/src/lib/studioApi.ts)：真实请求、类型及导入校验、素材与代理契约 |
| TL | [../frontend/src/lib/timelineEditing.ts](../frontend/src/lib/timelineEditing.ts)：帧网格、吸附、源标记、修剪动作提案 |
| COMP | [../frontend/src/lib/studioCompositions.ts](../frontend/src/lib/studioCompositions.ts)：显式转场位移提案、手动多机位编译为普通轨道 |
| SEQ | [../frontend/src/lib/studioSequences.ts](../frontend/src/lib/studioSequences.ts)、[../backend/studio_sequences.py](../backend/studio_sequences.py)：完整工程/活动序列分离、全图校验、时长绑定、跨序列复制与有界展开 |
| AS-UI | [../frontend/src/components/StudioAssets.tsx](../frontend/src/components/StudioAssets.tsx)：图片 / LUT 导入、任务收藏、真实 LUT ID 绑定 |
| PX-UI | [../frontend/src/components/StudioProxy.tsx](../frontend/src/components/StudioProxy.tsx)：手动源代理创建、原片/代理切换、只读恢复与真实保存回执 |
| B | [../backend/studio.py](../backend/studio.py)：`PROTOTYPE_TOOLS`、`SUPPORTED`、`capabilities()`、工程 / 动作 / 资产 / 作业路由 |
| R | [../backend/studio_render.py](../backend/studio_render.py)：`Project`、`Clip`、`Track`、`ExportOptions`、`render_project()`、字幕及滤镜实现 |
| AS | [../backend/studio_assets.py](../backend/studio_assets.py)：`canonical_cube()`、`prepare_image()`、`publish()`、`verified_asset()`、容量与路径约束 |
| PX | [../backend/studio_proxy.py](../backend/studio_proxy.py)：固定 RAW profile v2、绑定 / 散列 / 结果校验与传输租约；B 挂载真实作业/媒体路由 |
| DSP | [../backend/audio_filters.py](../backend/audio_filters.py)：Studio 与流水线共享 afftdn 固定预热/flush/时钟补偿，不含高通 |
| W-UI / W | [../frontend/src/components/ResultWorkbench.tsx](../frontend/src/components/ResultWorkbench.tsx)、[../frontend/src/lib/workbenchApi.ts](../frontend/src/lib/workbenchApi.ts)、[../backend/workbench.py](../backend/workbench.py)：按句待办、录音、合并提交、版本恢复 |
| PIPE | [../backend/pipeline.py](../backend/pipeline.py)、[../backend/tts_pipeline.py](../backend/tts_pipeline.py)、[../backend/subtitles.py](../backend/subtitles.py)：普通成片流水线、原声处理、正文字幕样式 |
| CLOUD | [../frontend/src/components/CloudTools.tsx](../frontend/src/components/CloudTools.tsx)、[../backend/cloud_jobs.py](../backend/cloud_jobs.py)、[../backend/cloud_store.py](../backend/cloud_store.py)：六种独立云作业、私有门禁与费用账本 |
| PROVIDER | [../backend/providers/cloud_editorial.py](../backend/providers/cloud_editorial.py)、[../backend/providers/cloud_media.py](../backend/providers/cloud_media.py)：Kimi 文字与腾讯 MPS / 私有 COS 适配器 |
| HOST | [../backend/main.py](../backend/main.py)、[../backend/config.py](../backend/config.py)：路由挂载、授权与默认关闭的云配置 |

### 两个维度必须分开

- **实现状态**：`已有子集/渲染/元数据` 表示本行具体实现存在，不等于整个复合工具完成；`本地未实现` 表示该原型动作仍无生产闭环；`外部阻塞` 表示受限云适配路径的账号、配置、预算或同意条件未满足。一个 partial ID 仍可能同时有本地缺口和外部条件。**C25 回归闭环完成只覆盖本轮受限实现，不取消缺失子项或改写历史失败。**
- **Studio 分类**：保留 B 中的 `renderer`、`partial`、`metadata`、`unsupported` 口径。`renderer` 仅表示所述字段有渲染路径，**不是整个复合工具所有选项都已满足**；`partial` 必须逐项看边界；`metadata` 不直接改变媒体，但 UI 可以真实消费它，例如分组折叠、快捷键和吸附；`unsupported` 不会因为有相近功能或云导航按钮而自动升级。
- B 的 `edit`、`marker`、`frameCrop`、`expCs` 的 reason 与序列 `clone` 边界已同步现有控件、实际 BT.709 转换及缺口；`voiceClone` 仍未实现。本文仍按**控件 → 请求 / 字段 → 实际执行分支**写边界，不能从 `available` 推导复合工具全支持。
- “定位实际控件”只滚动 / 聚焦区域；“查看云端能力（需配置）”只导航。选择模板 / 参数只形成草稿；保存成功也不等于渲染成功。源播放器不展示未渲染的时间线效果。
- **C25 历史证据按轮次绑定**：当轮独立构建最终深层 63/63 含 C25S-01–04、C25P-01–03、C25A-01–04、P25-01–09 及新增 C25D-01，覆盖 profile v2、序列/DSP 与冷深链修复。旧专项仍各自保留，不把多轮 PASS 相加或直接转移给其他构建；C26 当前整轮另见第 8 节。

## 3. 当前完整主流程，不计入 113 个工具 ID

1. **创作外壳与访问**： [../frontend/src/App.tsx](../frontend/src/App.tsx) 已有新闻创作 / 个人作品主导航、最近作品、抽屉及结果到 Studio 的入口；老师/学生认证后均默认 create，work/show hash 深链先进入只读 history；主导航和抽屿名为“工作室导航”。课堂 / 范例仍为独立区域，新建仍需真实课堂 / 班级上下文。旧令牌模式保留旧作品查看、修订和重试，不是免登录课堂创建。
2. **三步创作**： [../frontend/src/components/CreateWizard.tsx](../frontend/src/components/CreateWizard.tsx) 与 [../frontend/src/lib/appApi.ts](../frontend/src/lib/appApi.ts) 发送标题优先的 `script_format=headline_first`、真实文件、`asset_options`、制作偏好及可选 `own_voice`；[../backend/media_input.py](../backend/media_input.py) 处理真实素材范围 / 照片 / 整篇录音。浏览器五要素与够不够估算是辅助提示，不是事实核查或镜头匹配保证；刷新后需重新选择文件。
3. **正文字幕与原声增强**：`EditingPreferences.caption_style` 为 `news / big / none`；仅派生正文烧录表现，保留规范字幕证据，`none` 不抹标题/强制披露/源烧录文字。`enhance_speech` 默认 **false**，只对同期声 / 录音单元执行 70 Hz 高通加共享补偿 `afftdn`，不处理 TTS。DSP 固定 1,200 样本预热、1,200 EOF flush、移除 2,400 启动样本；Studio 共用后半链但**没有高通**。不是 AI 修复、分离或音色美化。
4. **真实制作状态**： [../frontend/src/components/Processing.tsx](../frontend/src/components/Processing.tsx) 使用服务端十阶段、上传字节、队列及耗时；小学分组为 1–5 / 6 / 7–10。缺少状态不编造完成百分比 / ETA，网络失败不伪称服务器停止；原任务重试可能重新运行有费用的流水线。
5. **按句结果工作台**：W-UI 从真实报告 / 上下文读故事板、来源、检查与版本；改字、直接选镜头 / 指令换画面、删句、单句录音及语速 / 字幕 / 降噪待办经 `POST /api/tasks/{task_id}/workbench/edit` 合并提交。不是照抄原型的多次模拟成功；文本重合成 / 语义换镜仍可能依赖已有模型服务。录音是实际 `MediaRecorder` 或文件上传，需要硬件权限，不是定时生成假音频；上传回执不等于口述事实已验证。
6. **版本与期限**：W 的 `/versions`、`/restore` 支持真实版本路径；Studio 工程修订与流水线成片修订是两套编号。当前不可访问页将 404 明确解释为“不可访问或已到期”，不凭 404 断言数据已被删除；原型 `gone` 与演示 TTL 不替代服务端期限。
7. **导出与审批**：Studio `/render` 渲染当前已保存活动序列及其完整嵌套；根主时间线不因选中子序列被替换。`/export` 仍独立导出流水线原成片，**不使用 Studio 草稿或活动选择**。标准输出可沿用已烧录成片；无字幕 / 大字分支依赖干净画面及最终混音，不能承诺完全保留原烧录图文。衍生输出不覆盖原成片 / 报告 / QC，也不会自动取得发布批准。

**冷深链保护已验证**：自动保存及向导挂载仅在真实 create 且未打开作品时发生；从真实创作页显式 `openWork()` 仍沿编辑链 flush 一次，GET 本身不触发草稿 POST。C25D-01 挂起真实私有 GET、收到非空第 3 步草稿，在挂起期间/打开后各推进 1,500 ms，浏览器写请求 0、整份草稿严格不变；P25-08 原“恰好一次 workbench POST”断言未改，最终通过。

**其余制作偏好也有明确边界**：[../backend/models.py](../backend/models.py) 将 `slow/normal/fast` 映射为 230/265/290 字/分；PIPE 的 `_segment_render_options()` / `_build_finish_options()` 接入画面推近、轻量颜色统一、配乐及基调、新闻图文、整片首尾 0.5/0.6 秒淡化和字幕样式；自动配乐分类可能使用已有 LLM，自定义要求传入匹配的 `editing_brief`。这些不等于自由曲线、全套转场库或自动事实核查。生成示意补图另有 [../backend/providers/generative.py](../backend/providers/generative.py) `apply_generative_fill()`，默认上限 2 段，需制作偏好、课堂许可与供应商配置共同允许；排除需实体覆盖及实体 / 日期 / 机构类型的节拍，保留兜底语义与强制披露。它不是物体消除、人物抠像或不受限生成。普通流水线的 Kimi / 火山能力与第 6 节**独立云作业六种操作**不是同一功能清单，均不能据本文推断新增付费调用已发生。

## 4. 三组状态汇总（不作完成数统计）

### A. 已有实现与已完成证据

时间线网格/五种修剪、有限关键帧、手工 SDR、文字模板/动画、正文字幕/原声增强、有限转场、2–4 机位手动组合、PNG/JPEG 与真实 3D LUT、手动 RAW profile v2 代理已有实际路径。**主 + 最多 4 子序列、完整 identity 嵌套、跨序列复制**不是全缺失；相关子集已纳入当前后端全量、7 套 Node 和最终同构建浏览器整轮。历史序列/代理/转场专项 132 PASS、序列 Node 51 PASS 仍是独立记录，不与终轮相加；实现限制见逐行矩阵。

**C26 补记：上述本地子集继续可用，不提升任何一行分类。** 本轮修正导航等待/跳转 hash、完整工程机位 ID、异步等待后写授权、终态/残留清理和 305px 布局，并纠正已有实际 SDR BT.709 转换的文案；没有新增 HDR、自动同步、独立嵌套合成组或完整批量导出。第 5 节全部 **113 行原样保留**，局部回归通过不能抵扣 B/C 组的缺失条件。

### B. 本地仍未实现，不能归咎于缺少云账号

包括独立嵌套合成组、父引用任意范围/变速/效果、自动波形 / 源时间码同步、9/16 机位、自动机位配方嵌套、多麦同步录音、录音美化 / 独立变调、完整音色库与批量 TTS 工具、曲线变速、贝塞尔 / 旋转 / 滤镜关键帧、区域调色、完整专业色轮 / HSL / 色阶面板、文字蒙版、自动运动 / 贴纸 / 蒙版跟踪、复杂形状与钢笔蒙版、高级混合模式、抠像边缘细化 / 溢色消除、画笔抠像、稳定器，以及序列帧、Alpha、HDR、超过本地上限的导出、批量导出队列、外部工程 / 文件夹 / Live Photo 导入、专用批量提取转文字和团队同步等。多时间线/完整嵌套本身不再属于全缺失项。

另外，`aiCut` 的 Studio 推荐 / 批量动作、`aiColor` 的自动匹配 / LUT 生成、`aiFrame` 的主体构图、`aiTransition` 的智能选转场均未实现；普通流水线、手工预设、居中裁切或溶解转场不能替它们“算完成”。部分已支持 ID 的未完成子项也属于本组，不能只统计整行 `unsupported`。

### C. 外部账号、预算、权利与同意条件

现有 CLOUD **仅六种操作**：`reference_narration`、`subtitle_translation`、`video_super_resolution`、`video_interpolation`、`audio_denoise`、`smart_subtitles`。入口与默认门禁详见第 6 节。腾讯真实凭据、既有私有 COS 桶、获准地域、模板 / 字幕语种、审核费率与预算尚缺，不能称为可用腾讯服务。

音色克隆、人物抠像 / 消除、美颜美体、对口型、自动社交平台发布等还需要独立权利 / 人物同意 / 平台账号与接口实现；**它们当前也有本地代码缺口，并非补密钥即可启用，更不属于现有六种云操作**。境外供应商须逐家单独同意，既有一次性测试许可不是持续生产支出、购买资源或上传学生可识别人物素材的许可。

## 5. `PRO_DEF` 逐项矩阵（001–113）

“†”表示原型设有 `gate:true`；只记录规格，不代表当前产品已实现对应专用同意流程。行状态描述**实现**，当前闭环证据、历史失败与未验证的外部范围集中在第 7 节；不逐行宣称整个复合工具已完成。

### 5.1 时间线与轨道（11 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 001 | `addTrack` | 无限轨道：视频、音频、文字、贴纸、调整图层 | 已有子集 / `partial` | UI `addEmptyTrack()`；R `Project/Sequence.tracks` | 五类 `video/audio/text/overlay/adjustment`；贴纸放视频/叠加轨 | 每时间线≤8 轨，全工程≤32 存储轨/64 片段；调整轨须另建区间，不是无限资源 |
| 002 | `trkGroup` | 同类折组：关 / 开 | 已有元数据 / `metadata` | UI 分组视图；R `TrackGroup`、`Track.group_id` | 全工程保存组名/归属，当前活动序列筛选、折叠/展开；“删除组并取消全部序列归属”仍守业务锁，最终相关用例通过；不改合成顺序 | ≤8 组，折叠是编辑器视图；没有组级渲染、组嵌套或联动特效；有界序列是另一实体 |
| 003 | `trkColor` | 颜色标记：金 / 青 / 红 / 紫 / 灰 | 已有元数据 / `metadata` | UI「轨道颜色」→ B `Action(op=track)` | 五个颜色 ID 保存为时间线标识，显示映射暖色主题 | 仅标记，不调色；活动轨操作且锁定须先解锁，不承诺原型逐像素色值 |
| 004 | `proxy` | 关 / 自动 / 手动导入；低清编辑、导出回原片 | 已有子集 / `partial`；当前 C25P-01–03 PASS | PX-UI/API；B `/proxies`、`/proxies/{id}/media`；PX 绑定/散列/租约 | 手动完整目录视频 RAW 预览，固定 profile v2：640×360/30 fps/H.264/AAC；源与输出重散列、成片修订绑定、缓存、私有 Range、取消/只读恢复；导出仍解析原源 | 自动批量/任意代理导入未实现，无时间线效果预览；仅同 profile 版本的旧空 schema 可兼容升级，仍校验旧模板/键/当前散列，不复用 v1 或编辑/嵌套模板 |
| 005 | `tc` | 时间码：秒 / 帧，精确到帧 | 已有子集 / `partial` | UI `TimelineField`；TL `formatTimecode()` / `parseFrameInput()` | 工作区 24/25/30/60 fps，整数帧/非丢帧 `HH:MM:SS:FF`、跳转和 ±1 帧；相关动作量化 | 独立于源/导出 FPS，不解析源 SMPTE/丢帧码；切换显示不重写秒数，源播放非实时合成 |
| 006 | `marker` | 添加 / 下一个 / 清空；备注、跳转 | 已有元数据 / `metadata` | UI `seekTimeline()`；B `marker/clear_markers` | 活动时间线真实保存时间/备注，逐项/下一个/标尺跳转 | 每时间线≤100 点，备注≤120 字符；不改变媒体，reason 已同步有限导航能力 |
| 007 | `inout` | 设入点 / 设出点 / 清除 | 已有元数据 / `metadata` | UI `markCurrentSource()`；TL `updateSourceMarks()`；R `SourceMark` | 读取真实原源 `currentTime` 或源秒，工程共享保存/撤销，显式插入才生片段 | ≤200 个授权源；不在保存时测 EOF；图片只有显示时长，精确记点须切回原片，不用代理时长覆盖原 EOF |
| 008 | `snap` | 磁性吸附：关 / 开 | 已有元数据 / `metadata` | UI `SnapReadout`；TL `collectSnapTargets()` / `proposeTimelineTime()` | 活动序列插入/移动/复制/分割提案在六帧内吸附零点、标记、边界，展示实际提交位置 | 非所有修剪数值都吸附；后端只量化，不搜索目标；R2-20 改用范围外 1.3s，P25-03 独立验证吸附 |
| 009 | `ripple` | 波纹编辑：关 / 开，删除后拼接 | 已有子集 / `partial` | UI `deleteClip()` / `applyTrim()`；B `_timeline_edit()` | 显式 `ripple_delete/ripple_trim` 移动同轨旧终点后的片段 | 其他间隙/轨/标记不联动，重叠/锁定拒绝；API 嵌套可 ripple_delete，但当前父嵌套 UI 不提供波纹操作 |
| 010 | `tlPick` | 时间线 1 / 2 / 新建；跨线复制粘贴 | 已有子集 / `partial`；当前 C25S 相关子集 PASS | UI 多序列面板；SEQ `createProjectSequence()` / `selectProjectSequence()` / `pasteSequenceClipboard()`；B `/project` | 主 + 最多 4 命名子序列；根 tracks/markers 永远为主，保存 active_sequence_id（null=主）有修订/撤销；同任务跨序列复制完整参数/嵌套引用 | 跨序列 CUT 禁止、同序列 cut 是原子 move；无无限序列/多窗口/自动克隆 UI |
| 011 | `nest` | 嵌套插入 / 打开嵌套 | 已有子集 / `partial`；当前 C25S 相关子集 PASS | UI 插入/打开/时长绑定；SEQ；R `Clip.sequence_id` | 视频/叠加用 sequence_id 替代 source_id；完整生效子时长、start/mute，其余默认；全图无环且≤2 引用边；可打开真实子编辑 | 非独立组：空隙透父下层、子调整作用累计下层、文字高于所有媒体；无父 trim/speed/FX；父锁传递，改长须同次更新全部引用；UI 父 mute 暂不编辑 |

### 5.2 片段编辑（14 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 012 | `edit` | 分割 / 剪切 / 复制 / 粘贴 / 删除 | 已有子集 / `partial` | UI `splitClip/copyClip/pasteClip`；SEQ；B `apply_action()` | 活动序列真实动作；内存复制完整参数可跨本任务序列，新 ID；同序列剪切单次 move | 跨序列剪切禁用；同类型未锁轨，离开清空，不读系统剪贴板；分割须移除不适合包络/转场，父嵌套不可分割 |
| 013 | `undo` | 撤销 / 重做；无限步 | 已有子集 / `partial` | UI；B `action_project()` / `commit()` | 完整工程历史含根/子内容、活动选择与锁；新编辑清 redo，脏草稿可先存 | ≤100 次历史变更，undo/redo 也消耗修订；非无限、无任意历史快照 GET |
| 014 | `trimMode` | 普通 / 波纹 / 滚动 / 滑移 / 滑动 | 已有子集 / `partial` | UI；TL `buildTrimAction()`；B `_timeline_edit()` | 五种显式动作；roll 保留两片段外端点，slide 保留三片段外端点，slip 仅改源入点 | 同轨无缝邻居/锁/重叠/一帧/包络校验；不自动改关键帧，渲染测 EOF；父嵌套不能修剪 |
| 015 | `freeze` | 关 / 开；3 秒图片定格 | 已有子集 / `partial` | UI；R `Clip.freeze` | 从 trim 取一帧按明确时长持有，静音/1×/不倒放 | ≤10s，可手设 3s；非独立图片导出/音频冻结；静态贴纸不使用此开关 |
| 016 | `reverse` | 关 / 开 / 开·保留音频 | 已有渲染 / `renderer` | UI；R `reverse/areverse` | 视频与已有未静音音频倒放，静音另设 | 非独立三档；倒放缓冲≤128 MiB，图片/定格/父嵌套不适用 |
| 017 | `mirror` | 镜像：关 / 开 | 已有渲染 / `renderer` | UI；R `Clip.mirror/flip` | 视频/叠加图片实际 hflip/vflip | 保存渲染后见效，非自动构图/镜面蒙版；父嵌套引用不可变换 |
| 018 | `rotate` | 0° / 90° / 180° / 自由 | 已有子集 / `partial` | UI；R `Clip.rotation` | 0/90/180/270 度 | 无自由角度/旋转动画；270 度不抵“自由” |
| 019 | `frameCrop` | 帧精确裁剪：关 / 开 | 已有子集 / `partial` | UI `TimelineField/ClipInspector`；R `Crop`、trim/duration | 帧输入/步进与归一化矩形裁剪实际渲染 | 非原片逐帧选择/无损切割或实时效果预览；源秒与输出量化独立 |
| 020 | `speed` | 0.5× / 1× / 2×；蒙太奇 / 闪白 / 慢动作 / 自定义曲线 | 已有子集 / `partial` | UI；R `Clip.speed` | 0.5–2× 恒速/保音高；**1× 不运行 atempo**，修复原 none 路径丢 55 样本 | 无速度模板/曲线/光流；图片/定格/父嵌套固定 1×，非 1× 不凭公式保证 PCM 等长 |
| 021 | `interp` | 关 / 普通 / 光流法补帧 Pro | 外部阻塞 / `unsupported` | UI 仅云导航 → CLOUD `video_interpolation`；R `ExportOptions.fps` 是另一功能 | 本地无智能补帧；受限腾讯适配器有目标 FPS 与输出流校验代码 | 腾讯条件未满足；普通 FPS 重采样不能冒充光流，光流质量没有本轮验证，也无本地三档控制 |
| 022 | `kf` | 添加 / 删除关键帧；位置 / 缩放 / 旋转 / 透明度 / 滤镜 | 已有子集 / `partial` | UI `KeyframeRows`；R `Keyframes` | 普通视频/叠加 x/y/scale/opacity，每属性 2–8 点，局部时间 | 无旋转/滤镜/音频关键帧；分割前清除，不自动重映射；父引用不接受关键帧 |
| 023 | `kfCurve` | 线性 / 缓入 / 缓出 / 缓入缓出 / 贝塞尔 | 已有子集 / `partial` | UI；R `keyframe_expression()` | 线性、二次缓入/出、三次 smoothstep，左点控制下一段 | 无贝塞尔手柄/任意表达式/跟踪 |
| 024 | `kfParam` | 位置 / 缩放 / 旋转 / 透明度 / 滤镜强度 | 已有子集 / `partial` | UI `ANIMATED_PROPERTIES`；R `Keyframes` | x/y/scale/opacity 非空曲线替代静态值 | 无旋转/滤镜强度，属性与工程图均有上限 |
| 025 | `adjLayer` | 新建调整图层；统一作用下方轨道 | 已有子集 / `partial` | UI 调色区间；R adjustment；SEQ | 有界基础色彩/预设/RGB/LUT 作用于累计下层；嵌套子调整也作用父级累计下层 | 非独立组，不裁在子画布；无任意 FX/音频/锐化/噪点；文字最后烧录不受影响 |

### 5.3 多机位（5 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 026 | `camN` | 4 / 9 / 16 机位 | 已有子集 / `partial` | UI `ManualMulticamEditor`；COMP | 2–4 目录视频，2×2 网格或硬切，普通轨道+连续主音轨 | 9/16 未实现；构建器只在无嵌套主线；活动展开≤8 层/64 片段/16 输入，非实时同步多播放器 |
| 027 | `camAlign` | 声音 / 时间码 / 入点对齐 | 已有子集 / `partial` | UI 同步入点；COMP `MulticamRecipe` | 显式 sync_in/共同输出时长，可主动复制源标记入点，编译 trim/start | 无波形/源时间码解析/自动同步；不暗缩至标记出点，渲染测 EOF/音轨 |
| 028 | `camWave` | 波形对齐：关 / 开 | 本地未实现 / `unsupported` | B `capabilities()`；COMP 仅手动秒数配方 | 连续主音轨只是用户选择，不是波形对齐结果 | 缺波形显示、音频相关匹配与偏移求解；不是腾讯账号阻塞 |
| 029 | `camBatch` | 批量切换 / 调整顺序 | 已有子集 / `partial` | UI 切换清单；COMP `parseMulticamCuts()` | 从 0 起递增≤12 段切点/机位，静音视频+连续主音轨 | 无 AI 切机/自动调序；追加保留旧混音，替换先解锁/移转场并确认；无完整批量队列 |
| 030 | `camNest` | 机位嵌套：关 / 开 | 已有子集 / `partial`；当前 C25S 相关子集 PASS | UI 多序列面板；SEQ；R `Sequence/Clip.sequence_id` | 普通机位轨可保存为子序列、完整 identity 引用并打开二级编辑；时长/锁/图/资源与 nest 相同 | 无自动机位配方嵌套/自动同步/独立组效果；机位构建器在子序列与嵌套主线停用，不能称专用多机位引擎 |

### 5.4 音频台（11 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 031 | `mixVol` | 音量：0 / 25 / 50 / 75 / 100 | 已有渲染 / `renderer` | UI「音量 0–2 倍」；R `Clip.volume` | 可表达百分比范围并允许到 2×增益，混音后有限幅 | 须真实音轨，倍率不是 LUFS/专业总线；父嵌套无增益字段 |
| 032 | `mixPan` | 左 / 左中 / 中 / 右中 / 右 | 已有渲染 / `renderer` | UI「立体声平衡」；R `Clip.pan` | −1 至 +1，通过通道衰减实现 | 非空间/多声道 panner，须有音轨；父嵌套无平衡字段 |
| 033 | `mixMute` | 正常 / 静音 / 独奏 | 已有渲染 / `renderer` | UI 片段静音、轨道隐/独；R/SEQ visible_tracks | 普通片段静音不藏画面；每条时间线独立解析 hidden/solo，父 identity mute 传至子音频 | solo 可同时影响画面，非仅音频总线；锁定先解锁，父嵌套 mute 可经 API 保存但 UI 不提供编辑 |
| 034 | `fxAudio` | 无 / 压缩 / 均衡 / 混响 / 限制器 | 已有子集 / `partial` | UI；R audio_effect/bass_db/treble_db；DSP | 固定压缩/限幅、低高频 EQ、回声混响、相位反转、单遍响度；denoise 共享 1200 预热+1200 flush−2400 启动样本补偿；1× 旁路 atempo | 无插件链/多段 EQ/卷积混响；尾声受片段边界约束；Studio **无高通**，PIPE 另加录音专用 70 Hz 高通/回执；不称 AI 修复 |
| 035 | `recMulti` | 多麦克风录音：关 / 开 | 本地未实现 / `unsupported` | B `capabilities()`；W-UI `startRecording()` 是单录音入口 | 现有录音为单路浏览器输入 / 上传，不是多麦采集 | 缺设备多选、并行采集、时钟同步、轨道映射与硬件验证 |
| 036 | `recFx` | 录音处理：降噪 / 美化 / 变速 / 变调 | 本地未实现 / `unsupported`；有相关能力 | B 无专用动作；W `enhance_speech`；UI「音频效果 / 恒定速度」为替代入口 | 原声降噪、普通素材音效 / 不变调变速存在，但没有 `recFx` 整套录音处理控件 | 美化、独立音高变调及录音处理闭环未实现；不能把附近的音频字段算成四个子项全支持 |
| 037 | `voiceLib` | 50+ 音色：新闻男 / 女、少年、粤语、英语 | 本地未实现 / `unsupported`；外部条件另需 | B 无音色库入口；[../backend/providers/tts.py](../backend/providers/tts.py) `create_tts_provider()` | 普通流水线使用服务端配置音色，不是可浏览 / 试听的 50+ 库 | 缺目录、授权语种 / 音色映射、试听及用户选择；供应商能提供音色不等于当前 UI 已接入 |
| 038 | `voiceClone` | 音色克隆 Pro：10–30 秒上传生成 † | 本地未实现 / `unsupported`；需独立同意 | B `capabilities()`；UI 音频台禁用卡 | 自录配音不训练/克隆声音；R2-20 定向复验实际确认 available=false 与按钮 disabled | 缺适配器、训练/生成、可撤销同意和声音权利治理；六云操作/现有许可均不覆盖 |
| 039 | `ttsBatch` | 批量生成 / 导出配音 | 本地未实现 / `unsupported`；有相关能力 | PIPE `_run_tts_synthesis()`；UI「MP3 / WAV 导出」不是本工具 | 正常稿件逐单元 TTS 与已有音轨导出存在，不等于任意批量配音任务 | 缺 Studio 批量 TTS 清单、分项回执和批量导出；真实合成仍需既有供应商凭据与费用许可 |
| 040 | `wave` | 淡入淡出 / 增益标准化 / 变速不变调 / 相位反转 | 已有子集 / `partial` | UI；R 音频链 | 有界淡化/增益、非 1× atempo、invert、单遍 −16 LUFS；1× 不过 WSOLA | 无波形编辑器/实测双遍母带保证，混音后不保精确 LUFS；非 camWave 自动对齐 |
| 041 | `aiAudio` | 人声分离 / 降噪 / AI 消音 / 修复 / 音效 | 外部阻塞 / `unsupported`；本地其余缺失 | UI 仅云导航 → CLOUD `audio_denoise`；普通降噪另见 `fxAudio` | 仅受限腾讯降噪适配路径；本地 `afftdn` 明确是传统滤镜 | 腾讯条件未满足；分离、AI 消音、修复、生成音效均未接入，不可包装成本地 AI 成功 |

### 5.5 AI 工具（11 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 042 | `aiCut` | 推荐方案 / 批量处理 | 本地未实现 / `unsupported`；有独立主流程 | B 无 Studio AI 剪辑动作；PIPE `run_pipeline()` | 主流程有稿件驱动自动成片，但 Studio 按钮没有方案生成 / 批量应用实现 | 缺方案预览、用户确认、批量任务与回写；不能以已有自动成片替代该工具所有选项 |
| 043 | `aiNarr` | 生成参考解说词，仅参考 † | 外部阻塞 / `unsupported` | UI 仅云导航 → CLOUD `reference_narration` → PROVIDER Kimi | 读取已提交稿件，生成带未经核实警示的文字衍生结果 | 总 / Kimi 开关、教师、预算、隐私及当前导出门禁缺一不可；非自动播音 / 自动套用或发布，不含 Studio 草稿 |
| 044 | `aiKey` | 抠当前画面 / 多人 / 批量 / 导出透明 † | 本地未实现 / `unsupported`；需人物授权 | B `capabilities()`；R 仅另有传统 `chroma` | 无人物分割模型、AI 抠像任务或透明媒体导出路径 | 色度键不是 AI 抠像；多人 / 批量 / Alpha 均缺，现有六云操作不含此项 |
| 045 | `aiErase` | 涂抹消除 / 大区域填充 / 逐帧消除 † | 本地未实现 / `unsupported`；需权利审查 | B 无消除动作；PIPE 生成示意补图是不同功能 | 无涂抹选区、视频修补或逐帧一致性实现 | 生成 B-roll 不等于消除原画面物体；新闻真实性、人物及素材权利条件另需 |
| 046 | `aiFix` | 高清修复、2K/4K/8K 超分、HDR↔SDR、去抖 | 外部阻塞 / `unsupported`；本地其余缺失 | UI 仅云导航 → CLOUD `video_super_resolution` | 腾讯子集仅审核模板下的 2× SDR 尺寸提升 / 输出流检查，非所有修复模式 | 腾讯未就绪；8K、HDR 变换、稳定 / 去抖及细节修复质量未完成；本地尺寸放大不能称 AI 修复 |
| 047 | `aiLip` | 普通话 / 英语 / 粤语对口型 † | 本地未实现 / `unsupported`；需独立人物同意 | B `capabilities()`；CLOUD 不含对口型 | 没有口型模型、音画驱动或对应任务路由 | 缺生产实现、人物 / 声音使用权与风险审查；普通 TTS 不等于对口型 |
| 048 | `aiTrans` | 英 / 日 / 法；保留原声 / 口音 / 批量 † | 外部阻塞 / `unsupported`；仅文字子集 | UI 仅云导航 → CLOUD `subtitle_translation` → PROVIDER Kimi | 配置后的中/英/日字幕文字翻译保 cue 身份、时码在模型请求外；R2-20 实际通过默认关闭云面板导航且作业为空 | 导航不启用服务/提升本地分类；无法语/整片译配/保声口音/唇形/批量/自动套用，仍须付费门禁与人工核对 |
| 049 | `aiColor` | 一键调色 / 批量 / 生成 LUT | 本地未实现 / `unsupported` | B 无 AI 调色；R `color_filters()` / AS `canonical_cube()` 为手工功能 | 手工预设、RGB 曲线及导入 LUT 真实存在，但不分析内容或生成 LUT | 缺智能匹配、批量自动调色和 LUT 生成；本地确定性滤镜不能改标 AI |
| 050 | `aiFrame` | 自动 / 抖音 9:16 / 小红书 3:4 / 微信 1:1 | 本地未实现 / `unsupported` | B 无智能构图；R `ExportOptions.aspect` / `Clip.crop/fit` 为手工入口 | 有 16:9、9:16、1:1 手工画幅 / 居中裁切 | 无主体识别 / 跟随重构图、3:4 或平台优化；裁切可能丢边缘内容 |
| 051 | `aiSub` | 一键包装 / 重点花字 / 双语字幕 | 外部阻塞 / `unsupported`；本地其余缺失 | UI 仅云导航 → CLOUD `smart_subtitles`；另有本地文字模板 | 腾讯路径仅审核单语 ASR / ASR 翻译字幕；本地模板为手工选择 | 不自动关键词花字或双语排版，不自动套用；腾讯模板 / 语种未配置，现有 SRT / ASS 导出不是 AI 包装 |
| 052 | `aiTransition` | AI 智能转场：关 / 开 | 本地未实现 / `unsupported` | B `capabilities()`；UI `TransitionEditor` 为独立手工工具 | 五种有限本地边界转场存在，但没有智能挑选 / 分析 | 缺内容驱动选择与自动应用；不能把溶解或首尾淡化宣传为 AI 转场 |

### 5.6 特效与调色（13 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 053 | `trLib` | 转场库：基础 / 运镜 / 特效 / 电影 / 故障 | 已有子集 / `partial` | UI `TransitionEditor`；COMP；R `TransitionIn` | 同轨两普通视频/叠加的溶解及四向擦除，合法子转场在展开中保留 | 非完整五类库；端点 cover、scale=1、x/y=0、opacity=1，无蒙版/chroma/关键帧/淡化/定格/父嵌套；源内 Alpha 转不透明 RGB；UI 构建器限无嵌套主线 |
| 054 | `trDur` | 0.3 / 0.5 / 0.8 / 1.2 秒 | 已有子集 / `partial` | UI；R `valid_transitions/transition_frames` | >0、≤1.2s、≥一工作区帧且≤两端各半，重叠严格等于 duration | UI 明示右段/同轨后续前移提案并确认；renderer 不移位/修剪，第三段不可占重叠 |
| 055 | `trCustom` | 自定义 / 批量添加 | 已有子集 / `partial` | UI `commitComposition()`；COMP；B 保存 | 五类型/四视觉缓动/音频交叉淡化；API 可明确保存多个合法边界 | 无批量按钮/任意滤镜模板，子/嵌套主线构建器停用；UI audio 初始 false、API 省略 true，音频总线性；false 留原混音，删除/分割先移绑定 |
| 056 | `beauty` | 磨皮 / 美白 / 瘦脸 / 大眼；0/20/40/60/80 † | 本地未实现 / `unsupported`；需人物同意 | B `capabilities()`；无人物专用效果字段 | 普通亮度 / 色彩不是美颜或脸部几何处理 | 缺检测、局部效果、多人规则、同意及真实性提示；云六操作不覆盖 |
| 057 | `body` | 美体：0/20/40/60 † | 本地未实现 / `unsupported`；需人物同意 | B `capabilities()` | 无人体几何变形 / 保护区域实现 | 缺模型与专用授权；不能用画面缩放替代 |
| 058 | `faceMode` | 人像：单人 / 多人 | 本地未实现 / `unsupported` | B `capabilities()`；R 无人物选择实体 | 未分析 / 选择画面人物 | 缺人脸 / 人体检测、人物选择与多目标效果绑定 |
| 059 | `faceSticker` | 无 / 人像贴纸 / 人像滤镜 | 本地未实现 / `unsupported`；有普通贴纸 | AS-UI 为静态图片；B 此 ID 未接入 | 已导入图片可手动定位，不会识别人脸或贴合面部 | 缺人脸锚点、追踪与人物滤镜；不能以自定义贴纸抵扣 |
| 060 | `basicColor` | 亮度 / 对比 / 饱和 / 色温 / 色调 / 锐化 / 阴影 / 高光 / 褪色 | 已有子集 / `partial` | UI `ClipInspector`；R `color_filters()` | 手工 SDR；warm/cool/temperature 用实际改灰色像素的有界 RGB 曲线；视频锐化/固定噪点、调整轨适用色彩 | 非 AI/HDR；hue 用角度，非统一百分制，保存渲染才见效；不对父嵌套直接调色 |
| 061 | `colorVal` | −50 / −25 / 0 / +25 / +50 | 已有子集 / `partial` | UI 数值/预设；R `Clip` | 独立有限区间，none/warm/cool/cinema/mono | 没有通用 ±50 同时驱动各项；预设非 LUT 导入/AI |
| 062 | `proColor` | 色轮 / HSL / RGB 曲线 / 色阶 / 白平衡 | 已有子集 / `partial` | UI `RGBCurveRows`；R | 每个提供的 RGB 通道 2–8 点，x 递增且端点 0/1；手工色温/色相/阴影/高光 | 无完整色轮/分色 HSL/色阶/自动白平衡；曲线图非效果预览，无 HDR 匹配 |
| 063 | `zoneColor` | 分区调色：关 / 开 | 本地未实现 / `unsupported` | B `capabilities()`；R 只有全片段 / 下层合成调色 | 形状蒙版控制图层 Alpha，并非分区调色选择器 | 缺颜色区域选择、区域参数与局部跟踪；不能把两类控件拼称已实现 |
| 064 | `lut` | 导入 LUT | 已有子集 / `partial`；当前 C25A 像素 PASS | AS-UI；B `/assets/lut`；AS `canonical_cube()`；R | 真实 3D .cube，2–33、单位域、有限 RGB、≤2 MiB（精确 main POST 已对齐）；四面体插值在类型化调色后，普通视频/叠加/调整绑定 | 拒绝 1D/非单位域/路径/include/HDR，无 AI LUT 生成；未知 ID 保留且拒用，导入不自动应用；未激活子引用同样验证 |
| 065 | `colorCopy` | 复制 / 粘贴调色 | 已有子集 / `partial` | UI `copyColor/pasteColor`；API | 内存复制适用色彩/预设/RGB/LUT，到当前未锁定普通片段 | 仅本任务、LUT 须在目录，调整轨不带锐化/噪点；非系统剪贴板/媒体打包/父引用效果 |

### 5.7 字幕与贴纸（15 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 066 | `fancy` | 无 / 新闻标题 / 描边黑 / 渐变金 / 手写 | 已有子集 / `partial` | UI 模板；R `_TEXT_TEMPLATES/subtitle_document` | custom/news/outline/gold/note；只覆盖渲染颜色/粗体/描边/阴影/背景，保留自定义值/字号/位置/透明度 | gold 纯色非渐变，无手写字体/完整花字库 |
| 067 | `textFx` | 描边 / 阴影 / 发光 / 渐变 / 背景（多选） | 已有子集 / `partial` | UI；R bold/outline/shadow/background | 固定字体粗体/描边/阴影/半透明背景框进 ASS | 无发光/渐变/任意插件或 ASS 指令输入 |
| 068 | `textAlpha` | 100 / 80 / 60 / 40 | 已有渲染 / `renderer` | UI；R `Clip.opacity` | 0–1 静态值转 ASS Alpha | 非透明视频导出/文字关键帧通道 |
| 069 | `textAnim` | 无 / 打字机 / 渐入 / 弹跳 / 旋转 | 已有子集 / `partial` | UI；R `text_animation` | none/typewriter/fade/pop；fade 明示或默认有界，pop 开头 70%→100% | 无旋转/持续弹跳，局部时间非词时，SRT 不带效果 |
| 070 | `perChar` | 逐字动画：关 / 开 | 已有子集 / `partial` | UI；R `MAX_TYPEWRITER_STEPS` | ≤64 阶段，长文本可成组，ASS 有字节上限 | 非 ASR 词时/卡拉 OK，过短/超限拒绝，不称精确语音同步 |
| 071 | `textMask` | 文字蒙版：关 / 开 | 本地未实现 / `unsupported` | B `capabilities()`；R 文字只烧录 ASS | 没有以文字字形裁切媒体的 Alpha 蒙版 | 文字透明度 / 描边不是文字蒙版；本地需实现 |
| 072 | `subTpl` | 保存模板 / 应用模板 | 已有子集 / `partial` | UI `text_template`；R | 四内置+custom，随工程保存/应用 | 无独立命名用户模板库/外部模板导入，工程保存非完整保存模板功能 |
| 073 | `subBatch` | 批量：字体 / 颜色 / 大小 / 位置 | 已有子集 / `partial` | UI 片段检查器；B 保存；R | 单片段或完整工程 JSON 明确写多片段，固定 Noto Sans SC | 无批量选择/一键全改 UI/任意字体，数组可写不等于原型批量完成 |
| 074 | `subOps` | 字幕分割 / 合并 / 删除 | 已有子集 / `partial` | UI split/delete；B actions | 文字分割/删除/复制，文本/时序可手工保存 | 无语义合并按钮，分割不智能分配文本，先处理动画/淡化/关键帧 |
| 075 | `subShift` | −1 / −0.5 / 0 / +0.5 / +1 秒 | 已有渲染 / `renderer` | UI 移动；B move；R start | 选中字幕开始秒数/网格移动 | 非整轨偏移预设；非负且≤120s，at 是绝对时间 |
| 076 | `subIO` | 导入 SRT、ASS/SSA；导出 ASS；透明字幕视频 | 已有子集 / `partial` | UI；B `/subtitles/import`；R/SEQ | SRT 1–64 cue、64,000 字符、请求≤256 KiB，添至保存的活动时间线新轨；展开实际时码 SRT/ASS 导出 | 无 ASS/SSA 导入/Alpha 视频，拒脚本，不覆盖旧轨；共享 ID/轨/片段限额，输出≤256 KiB |
| 077 | `subLang` | 普通话 / 粤语 / 四川话 / 英语 / 批量识别 | 本地未实现 / `unsupported`；外部条件另需 | B 无此语种选择动作；PIPE ASR 与 CLOUD `smart_subtitles` 是独立路径 | 普通流水线有 ASR，云字幕只用审核模板的单一输出语言 | 无所列方言切换 / 批量识别 UI；不能把模型可能识别方言或字幕翻译算为已实现 |
| 078 | `stickerLib` | 表情 / 装饰 / 文字 / 节日 / 动物 / 收藏 | 已有子集 / `partial` | AS-UI `ReferenceLocalCollection`；B assets | 任务内实际图片集合/受保护缩略与选择 | 非内置分类/外部搜索库；图片+LUT≤64，选择不自动插入 |
| 079 | `stickerCustom` | 导入图片 | 已有子集 / `partial`；当前 C25A Alpha PASS | AS-UI；B `/assets/image`；AS/API | PNG/JPEG≤8 MiB、≤4096/边、≤8,847,360 像素；主应用精确 POST 限额已对齐；去元数据/规范 RGBA PNG 保 Alpha，明确显示时长 | 拒 SVG/GIF/APNG/动画/多图；忽略 EXIF 方向，非 ICC 管理；无源时钟/音轨，不以 120s 当 EOF；Alpha 导入非透明导出 |
| 080 | `stickerAnim` | 关键帧 / 自动跟踪（多选） | 已有子集 / `partial` | AS-UI→UI `KeyframeRows`；R | 静态图片 x/y/scale/opacity、形状蒙版和原 Alpha | 无人物/物体跟踪/动画图导入/实时预览；转场端点不能带这些包络 |

### 5.8 画中画与蒙版（12 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 081 | `pip` | 添加画中画 / 嵌套 | 已有子集 / `partial` | UI 叠加/变换；R overlay；SEQ | 普通视频/图片位置、scale、opacity、有限关键帧；可在子序列编辑这些片段并完整嵌套 | 父引用 identity-only，不能缩放整个嵌套/独立预合成画布；须添加实际片段，不是目录点击即效果 |
| 082 | `pipLayer` | 上移 / 下移 / 置顶 / 置底 | 已有渲染 / `renderer` | UI `reorderTrack()`；R/SEQ | 重排活动轨，后媒体高于前媒体；子层插入父轨层位，锁定轨不可重排 | 无独立置顶/底按钮；所有子/主文字都在媒体之上，非任意 z-order/独立组裁切 |
| 083 | `blend` | 正常 / 变暗 / 变亮 / 叠加 / 滤色 / 柔光 | 已有子集 / `partial` | UI；R overlay | 正常 Alpha，嵌套透明/空隙露父下层 | 无变暗/变亮/叠加/滤色/柔光；降 opacity 非这些模式 |
| 084 | `maskType` | 无 / 线性 / 圆 / 矩形 / 心 / 星 / 镜面 / 渐变 / 文字 / 钢笔 | 已有子集 / `partial` | UI；R `ShapeMask/alpha_expression` | 无/矩形/椭圆，圆须按像素画幅调整归一化比例 | 无其余形状/线性/渐变/镜面/文字/钢笔；归一化宽高等值不保像素圆 |
| 085 | `maskFeather` | 羽化：0 / 10 / 25 / 50 | 已有渲染 / `renderer` | UI；R `ShapeMask.feather` | 矩形/椭圆向内 0–0.5 归一化边缘渐变 | 非像素半径/所有形状支持，须先有蒙版 |
| 086 | `maskInv` | 反转：关 / 开 | 已有渲染 / `renderer` | UI；R `ShapeMask.invert` | 反转覆盖率，继续乘源 Alpha | 不恢复源透明像素，无动态跟踪 |
| 087 | `maskTrack` | 蒙版跟踪 / 关键帧（多选） | 本地未实现 / `unsupported` | B `capabilities()`；R `ShapeMask` 为静态 | 图层位置关键帧不等于蒙版形状 / 控制点关键帧 | 缺蒙版时序、目标跟踪及校正 UI；本地需实现 |
| 088 | `bezier` | 钢笔贝塞尔：关 / 开 | 本地未实现 / `unsupported` | B `capabilities()`；R 无路径模型 | 现有数值 RGB 曲线和缓动均不是钢笔路径 | 缺路径 / 控制柄、填充与蒙版渲染 |
| 089 | `chroma` | 取色 / 相似度 / 平滑度 † | 已有渲染 / `renderer` | UI「色度抠像·非 AI」；R chroma | 手工选色/相似度/边缘混合进本地 chromakey | 无像素吸管/AI 人物识别/Pro 修边/despill/透明导出；私有权限不变 |
| 090 | `chromaPro` | 边缘细化 / 溢色消除 † | 本地未实现 / `unsupported` | B `capabilities()`；R 仅普通色度键参数 | 普通 `chroma_blend` 不构成完整 Pro 修边 / despill | 缺专用滤镜与控件，不因普通抠像可用而算完成 |
| 091 | `brushKey` | 自定义抠像：画笔 / 橡皮擦 † | 本地未实现 / `unsupported` | B `capabilities()`；R 无手绘遮罩资产 | 无画笔采样 / 橡皮擦 / 逐帧遮罩 | 缺笔刷 UI、遮罩持久化与渲染；人物使用权另需 |
| 092 | `track` | 运动跟踪：点 / 面 / 物体 / 手动修正 | 本地未实现 / `unsupported` | B `capabilities()`；R 仅手工图层关键帧 | 没有跟踪分析或轨迹回写 | 手动填关键帧不等于识别 / 跟踪 / 修正流程；缺本地算法与 UI |

### 5.9 专业导出与分享（10 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 093 | `expRes` | 360P / 720P / 1080P / 2K / 4K / 8K | 已有子集 / `partial` | UI；R resolution/dimensions | 本地 360/720/1080，横/竖/方几何 | 拒 2K/4K/8K，腾讯独立超分非本地 4K 选项 |
| 094 | `expFps` | 24 / 25 / 30 / 60 / 120 | 已有子集 / `partial` | UI；R fps | 24/25/30/60；GIF 厘秒量化，PNG 单帧 | 无 120；独立于时间线 FPS，重采样非光流；非视频不适用 |
| 095 | `expBr` | 低 / 中 / 高 / 自定义 | 已有子集 / `partial` | UI；R `ExportOptions` | 视频 250–12000 kbps，压缩音频 96/128/192/256/320 | 无低中高一键档；目标非精确 CBR/大小保证，仍受估算/实测上限 |
| 096 | `expHdr` | 关 / HDR10 / HLG | 本地未实现 / `unsupported` | B 含此 ID；R `ExportOptions` / `probe()` | 当前只有 SDR；没有可打开的 HDR 模式，HDR 源被拒绝 | HDR10 / HLG 编码、10-bit 与色彩管理均未实现；“关”对应默认 SDR 不能使整项成为已支持 |
| 097 | `expCs` | Rec.709 / Rec.2020 | 已有子集 / `partial` | UI SDR·BT.709；R 视频最终链 | **实际** scale out_color_matrix=bt709/out_range=tv → YUV420P → frame/encoder 标签，修复仅标签时 lime green≈212 而非≈250；当前回归及 reason 已同步 | 非 Rec.2020/HDR/ICC 管理，PNG/GIF 走独立分支；实际 SDR 转换不等于完整色彩管理 |
| 098 | `expFmt2` | MP4/MOV/AVI/MKV/GIF/MP3/WAV；PNG/TGA 序列、Alpha | 已有子集 / `partial` | UI；R `render_project()` | MP4/MOV/MKV/AVI/GIF/MP3/WAV/**单帧 PNG**/SRT/ASS；GIF 前≤6s；活动展开导出 | 非原型所有格式：无 PNG/TGA 序列/Alpha 视频，PNG RGB；导入 Alpha 非透明输出；强制披露可禁纯音频/字幕 |
| 099 | `expBatch` | 加入队列 / 查看队列 | 已有子集 / `partial` | UI 后台作业；B 状态/取消 | 单次异步真实状态/取消/历史，每任务一个活动媒体操作 | 无批量排队/参数/调序，不接并行第二个；子序列不另分配额度 |
| 100 | `expOpt` | 智能编码 / 快速 / 后台 / 硬件加速 / 代理导出 / 导出前预览 | 已有子集 / `partial` | UI `startJob()`；R；PX | 保存后后台 CPU 编码/真实 MP4 预览；手动 RAW 代理仅源播放，timeline 导出仍用原源；profile v2 的 C25P-01–03 最终 PASS | 无智能流复制/快速模式/GPU/代理替代最终输出/实时预览 |
| 101 | `sharePlat` | 抖音 / 快手 / 小红书 / 微信 / 微博 † | 本地未实现 / `unsupported`；需外部账号同意 | B `capabilities()`；HOST / 课堂发布为内部功能 | 内部作品链接 / 班级发布不是社交平台上传 | 缺各平台 OAuth、发布 API、账号授权 / 撤销和教师同意流程；六云操作不含社交发布 |
| 102 | `shareHd` | 高清链接 / 导出工程 / 分享草稿 | 已有子集 / `partial` | UI JSON/下载；B project/export、outputs | 私有授权下载、完整工程 JSON（含序列/选择），App 访问链接不带 token 且仍需权限 | 无公开高清链接/便携媒体包/草稿协作；JSON 不含原片/图片/LUT，异任务/异机不能直接恢复 |

### 5.10 素材与工具（11 项）

| 序号 | ID | 原型功能 / 子选项（缩写） | 状态 / Studio 分类 | 实际入口 / 路径或函数 | 当前真实实现边界 | 缺失 / 条件 |
| --- | --- | --- | --- | --- | --- | --- |
| 103 | `libAuto` | 本地库：按类型 / 日期 / 评分 / 搜索 | 已有子集 / `partial` | UI 源库；B catalog/get_sources；AS-UI | 任务上传/norm/final/干净画面/旁白/规范图片及只读镜头报告，文件名/ID/标签本地搜索 | 无全局库/扫描/完整类型日期评分排序；代理/LUT 不是可插入 source ID，子序列引用另用 sequence_id |
| 104 | `libRate` | ★ 至 ★★★★★ | 已有元数据 / `metadata` | UI `updateAsset()`；R `AssetMetadata` | 授权源 0–5 星/≤16 标签，0 未评分，全工程共享保存 | ≤200 条，不改源/自动评级，无按评分排序 |
| 105 | `importPro` | 文件夹 / 工程文件 / Live Photo / 序列帧 | 已有子集 / `partial` | UI/API import；B `/project/import`；AS-UI | 本项目完整安全 JSON 含根/子/活动选择；专门 PNG/JPEG/单位域 3D .cube，媒体只用授权 ID | 无文件夹/其他软件工程/Live Photo/序列帧/任意路径 URL；全图锁/引用校验不能绕过，不含便携媒体包 |
| 106 | `hotkey` | 自定义 / 导入预设 / 导出预设 | 已有元数据 / `metadata` | UI workspace；API `canonicalChord/effectiveShortcuts` | 播放/分割/删/undo/redo/marker 六动作绑定，输入/IME/云面板不触发编辑 | 无独立预设导入导出 UI，只随工程携带；非任意命令键表，浏览器/OS 冲突仍属验收边界 |
| 107 | `stab` | 关 / 一键 / 云台 / 行走 | 本地未实现 / `unsupported` | B `capabilities()`；R 无稳定分析分支 | 普通裁切 / 镜像 / 缩放不估计抖动 | 缺运动估计、稳定轨迹、边界裁切及模式控件；腾讯现有六操作也不含稳定器 |
| 108 | `wm` | 自有素材去水印：智能 / 批量 † | 本地未实现 / `unsupported`；需权利限定 | B `capabilities()`；无水印消除任务 | 无智能修补 / 去水印实现；裁剪不是该工具 | 缺限定自有素材的流程、区域处理及批量实现；不能移除强制 AI 生成披露 |
| 109 | `splitMerge` | 批量分割 / 合并 | 已有子集 / `partial` | UI 单片段/顺序编排；B actions；R | 普通单片段分割，相邻硬切实际渲染 | 无批量任务/文件合并工具/一键合并；重叠是合成非一概 concat，父嵌套不可分割 |
| 110 | `draftOps` | 本地备份 / 云同步 / 团队云空间 / 云端恢复 | 已有子集 / `partial` | UI JSON/草稿/审计；B | 完整工程本地 JSON、服务器保存/撤销/重做，草稿可含未保存参数 | 无跨端/团队/外部云恢复，不打包媒体，服务器持久化非团队云空间 |
| 111 | `extract` | 批量提取 / 转文字导出 | 本地未实现 / `unsupported`；有相关产物 | B 无专用动作；PIPE ASR、UI 音频 / 字幕导出为相关路径 | 流水线产生语音证据、可导出已有音频或字幕，但无任意素材批量提取工作台 | 缺批量选择、提取队列与转文字提交 / 导出契约；ASR 原始转写不能用成片字幕冒充 |
| 112 | `quick` | 倒放 / 拼接 / 裁剪16:9 / 裁剪9:16 / 旋转裁剪 | 已有子集 / `partial` | UI 时长/变换/crop/aspect；R | 组合倒放/相邻片段/矩形裁剪/离散旋转/横竖输出 | 非一键批量宏，无自由角；须选源/编排/保存/渲染，缓冲/EOF 限制不绕过 |
| 113 | `layout` | 默认 / 剪辑优先 / 调色优先 / 保存工作区 | 已有元数据 / `metadata`；最终相关布局 PASS | UI data-layout；R workspace | default/editing/audio/captions，面板宽度/顺序、zoom 0.25–8 随工程保存；含序列的最终整轮及 320/20、展开元数据复验通过 | 无独立调色优先/自由停靠/多套命名工作区，不改媒体顺序；checkVisibility 修的是关闭 details 测量，结论限已测视口/字体 |

## 6. 六种云操作的真实范围与门禁

HOST 已挂载 `create_cloud_router()`，UI 中 `aiNarr/aiTrans/aiFix/interp/aiSub/aiAudio` 可在真实课堂上下文导航到 CLOUD 面板。**导航不提交云任务，不修改本地 `unsupported` 分类，更不意味着腾讯 / Kimi 已经启用。** 路由前缀为 `/api/tasks/{task_id}/cloud`，报价与作业分别经 `/quotes` 和 `/jobs`；只处理已提交的当前流水线修订，不包含 Studio 草稿。

| 现有操作（不是新增原型 ID） | 代码中的输入 / 输出边界 | 不可扩大的结论 |
| --- | --- | --- |
| `reference_narration` | Kimi 处理已提交稿件，返回带警示的未审核参考文字 | 不自动合成语音 / 套用稿件 / 核实事实 / 发布 |
| `subtitle_translation` | Kimi 处理 cue ID 与纯文本，保持身份 / 顺序；中 / 英 / 日；原时码留在模型请求之外 | 不等于整片翻译、译配、保口音、法语或双语自动包装 |
| `video_super_resolution` | 腾讯审核模板；输入≤120 秒、SDR≤1080p，结果按 2×尺寸及流信息验证 | 不是本地 4K 导出、8K、HDR 转换或细节质量验收 |
| `video_interpolation` | 腾讯审核目标 FPS，不得降帧，目标≤60；校验输出 FPS / 时长等 | 不是本地光流实现或运动质量已验证 |
| `audio_denoise` | 腾讯处理含音轨的当前成片，要求输出保有音频和规定几何 / 时长 | 不含分离、消音、声音修复 / 生成音效，也未量化听感改善 |
| `smart_subtitles` | 腾讯单语 ASR / ASR 翻译模板，输出 SRT/VTT；请求语种须与审核模板语种相符 | 不含多人字幕包装 / 方言选择器 / 重点花字，不自动回写或批准 |

实际条件与限制：

- **默认关闭**：HOST 的 `cloud_jobs_enabled=False`、`cloud_kimi_enabled=False`、`cloud_tencent_enabled=False`、`cloud_privacy_approved=False`，总 / 每日 / 单任务预算默认 0，腾讯凭据 / 地域 / 桶 / 模板为空或 0；实际部署仍须核对自身配置，离线 UI 通过不代表线上已启用。
- **仅私有门禁**：真实课堂身份、作品所有权 / 班级成员关系和 CSRF 仍必须通过；旧令牌任务不能调用这组云作业。只有有权限的教师可报价 / 提交付费 / 请求取消；创建与结果读取绑定当前已完成、允许导出的成片修订。公开作品页不是绕过门禁的入口。
- **配置不是同意**：需批准处理地域、具名一次性预算及审核费率、对应供应商开关 / 凭据；腾讯另需既有私有 COS 桶、受限前缀、审核模板和字幕语言。桶字段符合格式不能证明整桶私有或真实数据驻留。
- **当前外部阻塞明确保留**：真实腾讯凭据与预算等材料尚未齐备；SDK/mock/UI 条目不证明付费服务可用。C25 本轮新增付费请求 **0**；历史唯一 Kimi 请求的 **100 分预约仍为 HELD、实际账单未知**，回执散列未变，不能重试/重置预约来获得绿色记录。
- **权利与人物**：提交要求 `rights_confirmed`、`no_identifiable_people` 两项明确声明；它们是操作者声明，不是自动人物检测。历史一次性≤50元许可不授权持续支出、扩额 / 换预算 ID、购买云资源或上传学生可识别人物内容；境外服务需逐供应商另行同意。
- **账本与不确定性**：CLOUD 有持久预约、幂等、活动额度和未知提交保护；预约不是实际账单或远端费用硬封顶。未知 / 中断不能自动重提、清回执或假定退款；取消申请不证明远端停止，腾讯只在等待态可尝试中止。结果均为未审核衍生物，不自动应用 / 发布。

## 7. 共用边界、历史轮次与当前闭环

### 本地媒体与工程边界

- 主 + 最多 4 个子序列，每条≤8 轨/100 标记，**全工程≤32 存储轨/64 片段（含父嵌套描述）**，全局唯一 ID；从任意时间线最多两条引用边，无环/缺引用检查包含隐藏、独奏排除、未激活内容。active_sequence_id=null 为主，根 tracks/markers 永远不代表活动子序列。
- 活动展开≤8 个非空层/64 叶片段实例/16 解码输入（重复引用按次计），≤32 全高清等效源帧/120 秒/1080p SDR；空/隐藏/排除轨不增加展开渲染成本，但仍占存储轨限额。工作区/视频 FPS 24/25/30/60 独立，倒放≤128 MiB；另受线程/命令图/时限/EOF 约束。
- 父引用只允许完整时长 identity/start/mute，其他控制默认；改子时长必须同次明确更新所有受影响父/祖先绑定，锁定父轨传递保护子轨内容/顺序。展开非独立组：空隙透下层，子调整作用累计下层，所有文字在所有媒体上方；一次最终混音/披露，无中间文件。
- 片段/轨/标记动作及 SRT 导入仅作用于已保存活动时间线；返回/保存仍是完整工程，选择消耗修订/参与撤销。简单流水线 export 不随活动选择改变。UI 在子/嵌套主线停用旧转场/机位构建器；跨序列复制可用、跨序列 CUT 不可用，不把不支持的父音频编辑伪装成可用。
- 输出<128 MiB；每任务**共享**512 MiB 存储、20 作业、100 历史变更，子序列不另获预算。导入/代理准备/散列/传输共用每任务一个、全局两个媒体槽与对应租约；不是分布式锁或压力测试保证。
- 工程 JSON≤256 KiB。HOST 仅精确 **POST** image/lut 路径分别≤8 MiB/2 MiB（真实 main 大请求专项通过）；非资产 JSON/其他方法或别名不升级。仍须 Content-Length（缺 411，非法/超限 413）与处理器实际流字节检查；认证/CSRF/Origin/所有权不变。
- 源只能用当前任务授权 ID，无任意路径/URL。所有声明的源/LUT 含未激活子内容都验证/监视，活动展开确定实际解码；图片/LUT 规范化/索引/全文散列在渲染发布时复核。RAW 代理不进入 source catalog，导出不用代理代替原源；这些边界不是对本机管理员的 OS 沙箱。
- 图片导入成功不改变工程修订、不自动插片段；LUT 导入成功不自动绑定。图片时长是用户创作的停留时长，不是媒体 EOF；目录中 120 秒字段不可拿来当“实测源长度”。选择静态源不制造音轨 / 播放时钟。
- RAW 固定 profile **v2**；旧空 schema 兼容只限同版本旧 RAW 模板/键与全部当前散列校验，不改旧回执、不接受旧 v1、混合/编辑/嵌套模板。整源 >0 且≤120 秒、缓存/媒体重授权、源与输出重散列、私有 Range/取消租约及共享额度不因兼容分支放宽。
- 时间线无实时合成。播放原片、原始图片、低清源代理与已完成渲染是不同模式；保存与数字曲线图都不能当成效果证据。原片自带烧录字幕不能通过一个字幕开关消除。
- 工程备份只保存 ID、参数与有限状态，不携带原片、图片、LUT 文件，也没有通用跨任务重链接 / 跨机器素材包；**不可移植性必须明确告知，不能称完整便携工程**。

### C25 历史轮次（保留原结果）

本小节及下面 C25 历史终轮的“当前/最终”均指当轮版本，不是 C26；原计数、失败与完成结论保留，C26 另列第 8 节。

| 原始证据 | 实际结果 | 对矩阵的含义 |
|---|---|---|
| [页面基线](../canary_test/artifacts/p25-pages-baseline/summary.json) | **32/37；341,438.722 ms；FAIL** | 10 对比度约 4.4:1 是真缺陷，danger 文本改为 #963e22 后已纳入最终构建；11 标签、20 队列位置、26/27 嵌套 status 的测试已更新，不改本轮失败结果 |
| [新功能基线](../canary_test/artifacts/round2/p25-features-baseline/summary.json) | **15/16；310,734.838 ms；FAIL** | C25A-01–04 图片/LUT、P25-02–09 时间线/效果/偏好、C25P-01–03 代理当轮 PASS；P25-01 关闭 details 旧盒测量失败 |
| [深层基线](../canary_test/artifacts/round2/p25-deep-baseline/summary.json) | **41/42；278,939.176 ms；FAIL** | 剩 R2-20；其中 11 项启用态云 UI 是明确模拟，不是收费服务实测 |
| [布局复验](../canary_test/artifacts/round2/p25-layout-retest/summary.json) | **1/1；21,120.336 ms；PASS** | 用 checkVisibility 排除关闭 details 旧盒，页面宽度断言仍独立 |
| [布局与编辑复验](../canary_test/artifacts/round2/p25-layout-edit-verified/summary.json) | **2/2；21,547.939 ms；PASS** | 真正展开镜头/报告元数据仍不过界；R2-20 用 1.3s（原 1.05s 合法吸回 1s），实际编辑/undo/redo、voiceClone 禁用、aiTrans 仅云导航通过 |
| [完整后端基线](../canary_test/artifacts/prototype-20260925/backend-full-baseline.log) | **1,056 tests；522.099s；1 failure、3 skipped** | 旧 setparams 精确断言已修，原日志仍为失败 |
| [主应用专项](../canary_test/artifacts/prototype-20260925/host-and-tags-verified.log) | **6 tests；15.309s；PASS** | 真实 main 大 PNG/LUT 验证精确路径额度，非仅隔离 router |
| [音频/偏好/转场](../canary_test/artifacts/prototype-20260925/audio-clock-integration-retest.log) | **96 tests；40.811s；PASS** | 序列整合之前的专项，不当最新整套后端 |
| [序列/代理/转场](../canary_test/artifacts/prototype-20260925/sequences-final.log) / [序列 Node](../canary_test/artifacts/prototype-20260925/sequences-client-retest.log) | **132 tests；62.466s；PASS** / **51 PASS；1,191.0777 ms** | 有界序列已有实际专项，不再标全缺失；仍非全产品/最终浏览器 |
| [中断前完整后端](../canary_test/artifacts/prototype-20260925/backend-final.log) | **1,121 tests；1,118 PASS、3 SKIP；582.356s；OK** | 后续源码有修改，不能替代当前全量 |
| [较后页面](../canary_test/artifacts/p25-pages-complete-20260926/summary.json) | **36/37；275,607.424 ms；FAIL** | 唯一失败为旧分组按钮定位；现用“删除组并取消全部序列归属”，仍测试业务锁 |
| [分组崩溃轮](../canary_test/artifacts/p25-groups-final-retest/summary.json) / [随后分组复验](../canary_test/artifacts/p25-groups-after-crash/summary.json) | **page crash，FAIL** / **1/1 PASS；33,801.536 ms** | 两轮独立，后者不能证明前者崩溃根因已修复 |
| [较后深层](../canary_test/artifacts/round2/p25-deep-complete-20260926/summary.json) | **60/62；756,039.077 ms；FAIL** | C25A-03 page crash、P25-08 额外草稿 POST；不能全归为测试误报 |
| [序列浏览器复验](../canary_test/artifacts/round2/p25-sequences-browser-retest/summary.json) | **4/4；117,789.323 ms；PASS** | 不替代最终整轮 |
| [closure 页面首轮](../canary_test/artifacts/p25-pages-closure-20260926/summary.json) | **runner FAIL，0 用例执行；118,433.971 ms** | 种子未就绪时失败，不是 37 项业务失败；脱敏错误不证明具体内部异常 |
| [closure 深层首轮](../canary_test/artifacts/round2/p25-deep-closure-20260926/summary.json) | **61/63；848,642.735 ms；FAIL** | R2-28、P25-07 的 `spawn ffprobe ENOENT` 来自测试 shell PATH，非生产编码缺陷 |
| [媒体 PATH 复验](../canary_test/artifacts/round2/p25-media-path-retest-20260926/summary.json) | **2/2；64,263.202 ms；PASS** | 两项均通过后使用新种子完整重跑 63 项，不拼接两轮计数 |

### C25 历史终轮（非 C26 当前结果）

| 当前证据 | 已结束结果与边界 |
|---|---|
| [完整后端](../canary_test/artifacts/p25-closure-20260926/backend.log) | **1,121 tests；1,118 PASS、3 SKIP；937.139s；exit 0**，不是中断前 582.356s 那轮 |
| Node：[会话](../canary_test/artifacts/p25-closure-20260926/test-classroom-session.log)、[时间线](../canary_test/artifacts/p25-closure-20260926/test-timeline-editing.log)、[偏好](../canary_test/artifacts/p25-closure-20260926/test-workbench-preferences.log)、[组合](../canary_test/artifacts/p25-closure-20260926/test-studio-compositions.log)、[素材](../canary_test/artifacts/p25-closure-20260926/test-studio-assets.log)、[代理](../canary_test/artifacts/p25-closure-20260926/test-studio-proxy.log)、[序列](../canary_test/artifacts/p25-closure-20260926/test-studio-sequences.log) | 分别 **45/17/12/23/40/50/51 PASS，共 238**；单元/隔离契约，不计为浏览器用例 |
| [独立构建 manifest](../frontend/dist-canary-p25-closure-20260926/ASSET_MANIFEST.sha256) | 应用及两套 E2E 类型检查 exit 0；Vite **1,793 modules、4m 1s、4 个资源** |
| [页面 HTTP](../canary_test/artifacts/p25-closure-20260926/http-pages-final.json) / [深层 HTTP](../canary_test/artifacts/p25-closure-20260926/http-deep-final.json) | 4 个资源在 **8768/8769/8770** 的实际字节/散列全部与独立构建一致，非仅信任种子声明 |
| [最终页面](../canary_test/artifacts/p25-pages-final-20260926/summary.json) | **37/37 PASS；322,225.950 ms** |
| [最终深层](../canary_test/artifacts/round2/p25-deep-final-20260926/summary.json) | **63/63 PASS；855,637.336 ms**；52 项真实本地后端 + 11 项明确模拟的启用态云契约，含 C25S 4 项、C25P 3 项、C25A 4 项、P25-01–09 与 C25D-01；非腾讯 live |
| [环境检查](../canary_test/artifacts/p25-closure-20260926/environment-checks.log)、[Python 审计](../canary_test/artifacts/p25-closure-20260926/python-audit.json)、[npm 审计](../canary_test/artifacts/p25-closure-20260926/npm-audit.json) | compile、pip check、环境验证器、离线 uv 均通过；Python **76 包、0 漏洞、0 跳过**，npm **0 漏洞**；审计 0 跳过与后端测试 3 SKIP 不混用 |

最终两个浏览器整轮均 **Edge、1 worker、0 自动重试**，无 page crash；这不证明历史崩溃根因已修复。C25D-01 确定性检查冷深链零写入/完整草稿不变，P25-08 恰好一次 workbench POST 原断言仍通过；不以空草稿或放宽写入计数换取绿色结果。

- [浏览器与完整性已核验记录](../canary_test/artifacts/p25-closure-20260926/browser-and-integrity-verified.json)：使用路径序列化修正后的 verified 版，不使用编码受损旧版。**163 份 axe、0 violations**；incomplete 为 color-contrast **1,764**、video-caption **45** 个节点出现次数，**不是唯一缺陷数或 WCAG 通过**。
- **31 份布局报告/150 个样本**：文档与可见元素溢出均 0；**100 份用例 observations** 的 problems/errors 均 0。自动化与合成媒体不证明全情境视觉一致、人物听感、ASR 等价或新闻事实核实。
- 页面原始文件 **564** 个、深层原始文件 **259** 个散列**各自全部匹配，不相加**；受检 **84 份源码、共享前端 4 个资源及历史付费回执**散列未变，结论绑定验收基线与核验时点。
- [关闭记录](../canary_test/artifacts/p25-closure-20260926/shutdown.json)：本轮所属 **8768/8769/8770 已停止，errors=[]**，TEMP/失败证据保留。恢复时 8000、8766–8771 已无监听，没有停止用户进程，**不声称保留此前旧 PID**。修复**未部署到共享前端或旧服务**；腾讯批准配置、生产/Linux/负载、实物打印与麦克风音质仍未验证。

**C25 历史结论：有界 C25 实现与当轮回归闭环完成；不是全部 113 工具实现或 77 个完整工具。** 第 5 节所有缺失子项、第 6 节账号/预算/权利条件继续保留。详细分轮处置见 [PROTOTYPE_IMPLEMENTATION_20260925.md](PROTOTYPE_IMPLEMENTATION_20260925.md#6-c25-分轮验证状态)，总报告见 [C25 验收报告](../canary_test/CANARY_20260926.md)；不把离线验收写成共享服务升级或生产上线。

## 8. C26 当前快照（2026-09-26）

只读核对[原型实际目录](../金话筒新闻视频生成/金话筒%20·%20原型.dc.html#L585-L597)的 `PRO_DEF` 与[实际目录/注册表/分类分支](../backend/studio.py#L161-L264)，**113 个唯一 ID 集合一致；58 partial、8 metadata、11 renderer、36 unsupported**。`expHdr` 仍在第 096 行且 unsupported；`available` 不表示复合工具完成。本节只补充当前证据，**不改 001–113 行、分类或限额**。

| 当前 C26 证据 | 已结束结果 |
|---|---|
| [完整后端](../canary_test/artifacts/c26-audit-20260926/backend-full.log) | **1,148 tests：1,145 PASS、3 SKIP，623.067s，exit 0** |
| 七套 Node，逐套日志见[实现快照](PROTOTYPE_IMPLEMENTATION_20260925.md#8-c26-当前快照2026-09-26) | **260 PASS = 45/17/12/25/40/70/51**（会话/时间线/偏好/组合/素材/代理与作业/序列） |
| [最终构建](../frontend/dist-canary-c26-final-20260926/ASSET_MANIFEST.sha256)、[页面 HTTP](../canary_test/artifacts/c26-audit-20260926/http-final.json)、[最终深层 HTTP](../canary_test/artifacts/c26-audit-20260926/http-verified.json) | 三套类型检查通过；**1,793 modules、7.20s、4 资源**，实际 HTTP 字节/散列匹配，不覆盖共享构建 |
| [最终页面](../canary_test/artifacts/c26-pages-final-20260926/summary.json) | **37/37 PASS，309646.872ms** |
| [最终深层](../canary_test/artifacts/round2/c26-deep-verified-20260926/summary.json) | **69/69 PASS，843674.39ms**；**58 真实本地 + 11 明确模拟启用态云契约，非腾讯实测** |

最终浏览器均 Edge、1 worker、0 自动重试。[最终来源](../canary_test/artifacts/c26-audit-20260926/final-source.json)证明完整后端之后后端未变；生产仅再改窄屏 CSS，代理精度调整仅在测试侧。C26 的 body 后/持久作业准入前重新授权、可选 boolean/≤256 字符泛化提示的 cleanup 字段对、manifest-before-success、根身份负向契约和 UI 单次 GET 清理重试见 [STUDIO_API.md](STUDIO_API.md)。残留仍计原配额，失败/取消/中断不得携带成功输出；不会借重试重新编码或复活旧 pending 状态。普通源仍是大小/mtime 指纹及受信特权本机写入者边界，不声称所有源全文散列或媒体 DRM。

失败证据没有改写：后端 **20/20 失败 → 扩展 24 PASS**、真实 main **3/3 失败 → 3 场景通过**、UI **0/3 → 5/5**、305px **0/1 → 1/1**。第一次深层 **68/69** 的一微秒监看差经 **1/1** 定向复验，再独立执行最终整轮；[真实媒体实验](../canary_test/artifacts/c26-audit-20260926/native-seek-precision.json)复现 `currentTime` **2.007592 → 2.007591**，只放宽切回监看到一原生微秒 tick 加浮点噪声，所有持久源标记精确断言不变。各轮原始链接见[实现快照](PROTOTYPE_IMPLEMENTATION_20260925.md#8-c26-当前快照2026-09-26)，不得累加为更多测试。

**High 未解决运行时风险：** 后续 [closure 深层](../canary_test/artifacts/round2/c26-deep-closure-20260926/summary.json)为 **61/69**，C25S-02 清理与后续 7 项连接失败来自原生主机退出，不是八个已证实业务缺陷。[Windows 原生崩溃证据](../canary_test/artifacts/c26-audit-20260926/native-host-crash.json)为 Python 3.11.9 / [python311.dll](../canary_test/artifacts/c26-audit-20260926/native-host-crash.json#L29-L33)、`c0000005`、offset **0x205cbe**。最终 69/69 的 `-X faulthandler` **只是诊断，不是根因修复**；没有 Python 升级或内存/认证 dump，不能宣称所有 bug 已修或生产通过。[崩溃后独立核验](../canary_test/artifacts/c26-audit-20260926/crash-source-recheck.json)的 **259 原文件全匹配**不等于正常退出。

[关闭核验](../canary_test/artifacts/c26-audit-20260926/shutdown-verified.json)确认最终所属 **8768/8769/8770 已停、验收进程 0**，但**仅 page 记录正常 stopped**；两个重定向诊断 deep 终端 Ctrl-C 未写完 manifest finally，仍为 `startup_complete`、无停止时间。`errors:[]` 不能证明 orderly shutdown，证据明确 `deepOrderlyShutdownRecorded:false`。[最终 deep 独立重查](../canary_test/artifacts/c26-audit-20260926/final-deep-source-recheck.json)确认 **259 SHA 全匹配**，没有重写原清单。TEMP/失败记录保留，没有停止用户进程。

[浏览器/完整性 verified 记录](../canary_test/artifacts/c26-audit-20260926/browser-integrity-verified.json)另记页面 **564 SHA 全匹配**、最终 **84 源码/依赖声明**与共享 **4 资源**/此前 C25 构建/当前构建/历史付费回执未变；不同原文件清单不相加。**163 axe、0 violations**，但对比度 **1,764** 与字幕 **45** 为 incomplete 节点出现次数，不是 WCAG 全通过。**31 布局报告 / 150 矩阵样本 0 溢出**，另有匿名登录/结果/Studio ×16/20px 的 **6 个 305px 可用宽度样本**；**106 observations，0 problems/errors**。305px 是 320px 窗口扣参考 15px gutter 的模型，不冒充 headless 原生滚动条验证。

**本地已有子集继续可用，所有 partial 子功能缺口与 unsupported 仍保留。** 新增付费调用 **0**，历史 Kimi **100 分 HELD、账单未知**不重试/重置/扩额；腾讯配置、私有桶、地域、模板/语种、审核费率预算与人物/声音权利条件不变。没有共享服务升级、生产/Linux/负载或硬件通过结论；C26 报告见 [../canary_test/CANARY_C26_20260926.md](../canary_test/CANARY_C26_20260926.md)。