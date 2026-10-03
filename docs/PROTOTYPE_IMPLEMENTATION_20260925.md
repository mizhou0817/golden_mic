> **历史快照 · 2026-09-27 范围变更：课堂/账号、独立云作业及旧 Canary/Round 2 入口已退役。** 下文“当前”与 C25/C26 数量仅指各自当日源码/运行，不是现行套件或无登录验收结果；当前契约见 [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)。历史通过、失败、风险和能力缺口照录，原始证据/媒体/原型/隔离构建不改写。

# 2026-09-25 当前原型实现对照

> **C26 当前快照 · 2026-09-26：受限回归已结束；High 原生运行时风险未解决。** 后端 **1,148 tests：1,145 PASS、3 SKIP，623.067s，exit 0**；7 套 Node **260 PASS**；最终独立构建页面 **37/37**、深层 **69/69**（58 项真实本地后端 + 11 项明确模拟的启用态云契约，**非腾讯实测**）。最终绿色不证明历史原生崩溃根因已修复，也没有深层主机正常关闭证明。第 8 节记录当前结果、失败和证据缺口；另见 [C26 审查报告](../canary_test/CANARY_C26_20260926.md)。

> **C25 历史快照 · 2026-09-26：有界实现与回归闭环完成。** 当轮源码后端 **1,121 tests：1,118 PASS、3 SKIP，937.139s，exit 0**；7 套 Node **238 PASS**；同一独立构建页面 **37/37**、深层 **63/63**（52 项真实本地后端 + 11 项明确模拟的启用态云契约，非腾讯实测）。历史失败保留，第 6 节列出逐轮证据；总报告见 [C25 验收报告](../canary_test/CANARY_20260926.md)。这是受限实现的收口，**不是全部 113 工具实现或生产上线**。

## 1. 权威版本与范围

- 权威需求来源是[当前指定原型](../金话筒新闻视频生成/金话筒%20·%20原型.dc.html#L474-L524)；实际契约以生产源码为准，验收以对应轮次的完整结果为准。2026-09-26 直接核对的原型 SHA-256 仍为 `17b53e815e6f94847b383a2b7fb6cb2acf916cf50890f8563a982999b631a251`，与需求基线一致；它不是前端构建散列。
- `PRO_DEF` 的目录分母为 **113 个唯一工具 ID**，含 `expHdr`；组数为 11/14/5/11/11/13/15/12/10/11。当前 `capabilities()` 与原型 ID 集合**完全相同，无缺项/多项**：**58 partial、8 metadata、11 renderer、36 unsupported**。这是分类，不是 77 个完整工具或完成率。逐项需求、部分实现和缺失子项见 [PROTOTYPE_CAPABILITY_MATRIX_20260925.md](PROTOTYPE_CAPABILITY_MATRIX_20260925.md)，不沿用历史未支持总数。
- 主目标是当前暖色新闻创作原型，不是旧课堂管理界面：奶油纸色 `#f7efe2`、棕色 `#4a3626`、橄榄绿 `#557538`、金色 `#e2a83c`，60px 顶栏、260px 作品侧栏、28px 纸网格；创作/处理/专业剪辑的原型宽度分别为 960/760/1200px。当前令牌见 [../frontend/src/styles.css](../frontend/src/styles.css)。最终布局证据限已测视口/字体，不代表逐像素一致或所有响应式情境通过。
- 原型 `create/processing/result/pro/gone` 是主流程。所有已认证老师/学生默认进入 **create**，不再默认把老师送到管理页；作品/展示 hash 深链则先进入只读 history，避免临时创作页误保存。主导航及移动抽屿名为“**工作室导航**”。课堂、账号、作品墙、教师管理作为真实扩展保留；认证、`X-Classroom-CSRF`、所有权、预算和发布门禁不变。新建仍依赖课堂身份/班级上下文，**不是免登录创建**。
- 不复制 `SIM`、计时器推进、假播放、演示费用、自动同意或本地样例任务作为真实结果；不把原型的无限轨道/撤销、固定期限和容量文案当成服务端无上限承诺。

## 2. 实际生产路径

| 模块 | 当前源码 / 接线 | 当前实现与边界（最终证据见第 6 节） |
|---|---|---|
| 外壳与作品入口 | [../frontend/src/App.tsx](../frontend/src/App.tsx)、[../frontend/src/styles.css](../frontend/src/styles.css) | 新闻创作/个人作品主导航、最近作品与移动抽屉；课堂/范例独立区域。旧令牌模式保留旧作品查看/修订/重试，不能新建课堂作品 |
| 三步创作 | [../frontend/src/components/CreateWizard.tsx](../frontend/src/components/CreateWizard.tsx)、[../frontend/src/lib/appApi.ts](../frontend/src/lib/appApi.ts)、[../backend/main.py](../backend/main.py) | 真实文件、`script_format:headline_first`、`preferences`、`asset_options`、可选 `own_voice`；正文字幕与原声降噪已接线，文件刷新后需重选 |
| 制作中 | [../frontend/src/components/Processing.tsx](../frontend/src/components/Processing.tsx) | 消费真实十阶段/上传字节/队列/耗时；小学分组 1–5 / 6 / 7–10，不虚构 ETA 或将断网当作服务端已停止 |
| 结果与按句待办 | [../frontend/src/components/ResultWorkbench.tsx](../frontend/src/components/ResultWorkbench.tsx)、[../frontend/src/lib/workbenchApi.ts](../frontend/src/lib/workbenchApi.ts)、[../backend/workbench.py](../backend/workbench.py) | 视频/故事板/选句面板、改字/换画面/删句/录音及偏好合并提交；真实版本恢复和发布检查。不照抄原型旧 remix/replace-shot 模拟映射 |
| Studio 工程与时间线 | [../backend/studio.py](../backend/studio.py)、[../backend/studio_render.py](../backend/studio_render.py)、[../backend/studio_sequences.py](../backend/studio_sequences.py)、[../frontend/src/components/Studio.tsx](../frontend/src/components/Studio.tsx)、[../frontend/src/lib/studioSequences.ts](../frontend/src/lib/studioSequences.ts)、[../frontend/src/lib/timelineEditing.ts](../frontend/src/lib/timelineEditing.ts) | 主时间线 + 最多 4 子序列、完整时长 identity 嵌套、活动序列动作、帧网格/五种修剪、同任务跨序列复制、持久修订/撤销；无实时合成预览 |
| 转场/手动机位 | [../frontend/src/lib/studioCompositions.ts](../frontend/src/lib/studioCompositions.ts)、[../backend/studio_render.py](../backend/studio_render.py) | 显式边界转场和 2–4 机位编译为普通轨道；构建器仅在不含嵌套的主时间线开放，普通机位轨可作为子序列内容 |
| 私有图片/LUT | [../backend/studio_assets.py](../backend/studio_assets.py)、[../frontend/src/components/StudioAssets.tsx](../frontend/src/components/StudioAssets.tsx)、[../backend/main.py](../backend/main.py) | PNG/JPEG 规范化、真实 3D LUT；精确 POST 的主应用限额已对齐 8 MiB/2 MiB，真实大 PNG/LUT 挂载路径专项通过 |
| 源代理 | [../backend/studio_proxy.py](../backend/studio_proxy.py)、[../backend/studio.py](../backend/studio.py)、[../frontend/src/components/StudioProxy.tsx](../frontend/src/components/StudioProxy.tsx)、[../frontend/src/lib/studioApi.ts](../frontend/src/lib/studioApi.ts) | 手动创建/缓存/私有读取已接通；当前 profile v2、序列/DSP 集成及 C25P-01–03 已在最终同构建深层轮次通过，不含自动/外部代理导入 |
| 共享音频与 SDR 输出 | [../backend/audio_filters.py](../backend/audio_filters.py)、[../backend/tts_pipeline.py](../backend/tts_pipeline.py)、[../backend/studio_render.py](../backend/studio_render.py) | 两条链共享固定 afftdn 时钟补偿；Studio 无高通、1× 不运行 atempo；视频输出实际转换 BT.709 limited-range YUV 后设置标签 |

所有 Studio 路由、字段、响应状态、错误、资源和导出语义的完整参考见 [STUDIO_API.md](STUDIO_API.md)。源码可保存、能产生实际媒体、单项测试通过、统一浏览器验收通过是不同结论。

## 3. `CaptionStyle` 与 `enhance_speech`：主流程偏好，不是 Studio 导出参数

定义来自 [../backend/models.py](../backend/models.py#L162-L191)，执行经 [../backend/pipeline.py](../backend/pipeline.py)、[../backend/workbench.py](../backend/workbench.py)、[../backend/tts_pipeline.py](../backend/tts_pipeline.py)、[../backend/subtitles.py](../backend/subtitles.py)、[../backend/rendering.py](../backend/rendering.py)。前端真实共享类型见 [../frontend/src/types.ts](../frontend/src/types.ts)，工作台变更比较见 [../frontend/src/lib/workbenchApi.ts](../frontend/src/lib/workbenchApi.ts)。

| 字段 | 创建/旧数据默认 | 工作台编辑契约 |
|---|---|---|
| `caption_style` | `CaptionStyle = news / big / none`，省略为 `news` | 可仅更新该偏好；不是 `standard / large / none` 的 Studio `subtitles` 枚举 |
| `enhance_speech` | 严格 JSON boolean，省略为 **false**；不是原型演示的默认开启 | `false` 是明确关闭；省略/null 为不改变，不可用字符串或 0/1 冒充布尔 |
| `pacing` | `normal`；230/265/290 字/分对应 slow/normal/fast | 原有音频按新旧目标比进行不变调变速，新 TTS 用新目标；不是把未验证录音标成已测语速 |

- 创建仍是 `POST /api/tasks` 的 multipart `preferences` JSON；成功 **202**，偏好 JSON/未知字段/非法值是 **400**。新建向导对既有音乐/运镜/首尾淡化/图文/颜色统一开关显式设 true，但新字幕/降噪仍为 `news`/false；不把 UI 新建默认误记成服务端旧任务默认。
- 修改是 `POST /api/tasks/{task_id}/workbench/edit`：`{expected_revision,keep_sentence_ids,edits?:[],pacing?,caption_style?,enhance_speech?}`。`expected_revision` 是**成片修订**；保留 ID 须非空、唯一且属于当前句子，空 edits 加真正偏好变化可独立提交。成功 **202** 回执含 `task_id,revision,current_revision,status:"queued"`；无变化/非法值/未知字段 **422**，旧修订 **409**。不是独立偏好 PATCH 或 Studio 工程修订。
- 只有缺失的旧前端字段补默认；存在但损坏的值不能伪装成已保存。接受的工作台编辑走已有预约、权限复核、确认失效、新版本、QC 与失败回滚；失败接受任务仍需重新确认发布，不静默恢复旧批准。

### 正文字幕

`subtitle_burn_in_artifact()` 在 **news/big/none 全部模式**先校验规范 ASS 和绑定清单。`news` 使用原文件；`big` 将 `NewsDialogue2024` 与 `NewsSentence2024` 两种正文样式放大 **1.5×**，保留时码，必要时安全换行/调整容纳区域，无法安全容纳则报错，不偷偷缩字号或裁掉字形。`none` 只移除正文事件。

**`none` 不移除标题、独立新闻包装、强制生成内容披露或源画面已有烧录文字。** 派生 ASS 使用独立临时文件并记录规范字幕 SHA-256，不改规范字幕/清单、词时刻、ASR 证据；退出渲染上下文时清理。仅改字幕样式不会重建或重复降噪旁白，仍须经过最终渲染/QC。

Studio `subtitles:standard` 保留已有 final，即使它原本选择 big/none；`large/none` 才按干净画面与最终混音重建。Studio `text_template:news` 也是独立片段模板，不是流水线字幕标准。原片已烧录内容不能由导出开关逆向删除。

### 当前录音 DSP：共享固定时钟补偿

只对 `SentenceTiming.audio_kind == "sync"` 的同期声、整篇学生录音拆分单元、单句录音应用；当前 TTS 即使有旧录音元数据也跳过。入口是组装旁白的 `concatenate_narration()`，不对最终混音/TTS 整轨降噪，不覆盖可重用逐句源音频。

在与参考支路**相同的 48 kHz stereo 格式转换之后**，流水线固定链如下。第 2–5 步由 [../backend/audio_filters.py](../backend/audio_filters.py) 的 `AFFTDN_CLOCK_FILTER` 共享给流水线和 Studio，**不是两份独立参数副本**；70 Hz 高通只在流水线原声增强中，Studio 没有该高通。

| 顺序 | 实际参数 | 含义 |
|---|---|---|
| 1 | `highpass=f=70:p=2` | 70 Hz 双极高通 |
| 2 | `adelay=1200S:all=1` | 1,200 samples 静音预热，避免起始 overlap-add 增益损失 |
| 3 | `apad=pad_len=1200` | 1,200 samples EOF flush，送出降噪器内部尾部 |
| 4 | `afftdn=nr=12:nf=-50:nt=w:rf=-38:tn=0:tr=0:om=o:ad=0.5:gs=0` | 固定白噪声底、12 dB 降噪参数、输出处理音频；`tn=0` 不等于禁止每频点增益自适应 |
| 5 | `atrim=start_sample=2400,asetpts=PTS-STARTPTS` | 只移除 2,400 个合成启动样本（预热 + 算法延迟），恢复局部时钟 |

1,200 samples 在 48 kHz 为 25 ms；这里的固定预热/flush 不是为凑目标时长任意补静音。**没有尾端裁切、按目标时长补齐、静音删除或观测失败后修补长度。** 检查的是同转换参考与处理 PCM 的实际帧字节、规格和非空内容，样本数差绝对值必须 **≤1**；源 SHA-256 必须未变。失败拒绝使用，不改源/词时刻去“满足”断言。

当前 profile 算法名为 `local_highpass_afftdn_clock_compensated_v2`，记录 requested/applied、录音单元数、`tts_filtered:false`、1,200 延迟/预热、单元源/处理散列、PCM 数量和 `sample_tolerance:1`。全 TTS 时 requested=true 也可以 applied=false；关闭时移除当前处理回执。重剪从原始单元再组装，纯换画面复用已组装旁白，不反复处理已降噪混音。工作台的 `speech_enhancement_applied` 是处理回执，不是 `transcript_verified`；单句录音仍未验证转写。

高通/降噪可能改变相位和幅度。样本数、词时码不变本身不能证明所有音节/起音/尾音无损；测试另有相同声道参考、奇数 PCM、非零首尾和独立载波时钟检查。该功能是**本地 DSP，不是 AI 修复、分离、美化或音色克隆**。Studio 的 `audio_effect:denoise` 在变速后恢复到 48 kHz 再执行同一补偿链，之后才应用用户淡化/转场/时间线偏移；它不使用流水线的录音专用范围、高通或处理档案。

Studio **speed=1 时完全旁路 `atempo`**，包括 `audio_effect:none`。真实 96,037-sample PCM 曾在 `atempo=1` 下丢失 55 个样本，后续静音底掩盖了尾部缺口；修复是跳过无必要 WSOLA，而不是补静音伪装等长。这改变了原空效果路径，RAW 代理固定 profile 已升为 **version 2**。非 1× 仍按实际媒体时钟验证，不能从恒速公式推导精确 PCM 长度。上述本地 DSP 不新增供应商调用；原有语义换镜/TTS/整篇录音 ASR/自动配乐分类仍可能使用既有付费服务。

## 4. Studio 序列、效果、转场与手动机位

### 主时间线、活动选择与完整嵌套

- `Project.sequences` 默认 `[]`、最多 **4 个命名子序列**；`active_sequence_id` 默认 null 表示主时间线。根 `tracks/markers` 始终是主时间线，不能把活动子轨当根轨写回。`Sequence` 包含 `id,name,tracks,markers`，工程共用 groups/assets/workspace；活动选择须正常保存，消耗修订并参与撤销/重做。
- 每条时间线≤8 轨/100 标记；**主 + 全部子序列合计≤32 存储轨/64 片段**，嵌套描述也算片段。序列/轨/片段/标记/组 ID 全局唯一。隐藏、独奏排除、未激活序列都查源/LUT、循环和深度；从任何时间线最多两条引用边（如主→A→B），不是只检查当前画面。
- 视频/叠加片段可用 `sequence_id` 替代 `source_id`，两者互斥。父引用只允许 `id,sequence_id,start,duration,mute` 非默认；trim=0、speed=1、fit=contain，其他变换/特效/音频字段保持中性。duration 必须严格等于子序列 visible/solo 生效全长，>0、≤120 秒；子时长改变须同次保存明确更新所有受影响祖先引用，不自动裁切/延长。空子序列不可嵌套，同轨嵌套区间不可与其他片段重叠。
- 每容器独立解析 visible/solo，再把子轨插入父轨层位。只累加祖先 start/mute，保留子片段源 trim、局部包络及内部转场。**不是独立预合成组**：透明/空隙露出父级下层，子调整轨作用于累计下层，所有子文字仍在所有媒体之上。一次最终混音/披露，无中间媒体。
- 活动展开另限≤8 个**非空**层/64 叶片段实例/16 解码输入；重复引用按实例逐次计算渲染资源，不按唯一 source ID 合并，也不新增磁盘/作业配额。所有声明的源/LUT（含未激活内容）在快照验证/监视，实际解码由展开叶项确定；图片/LUT 散列在发布前复核。
- 锁定父引用会**传递保护全部后代轨内容与顺序**，即使父级隐藏/未激活；先去对应时间线单独解锁，不允许导入绕过。删除被引用子序列前，先单独保存移除所有引用。撤销/重做恢复完整合法工程和活动选择。
- `/actions` 的片段/轨/标记操作及 SRT 导入仅作用于**已保存活动序列**；`/render` 渲染其快照，简单流水线 `/export` 不变。API 父片段动作仅 move/duplicate/delete/ripple_delete，identity mute 可保存；UI 更窄：移动/普通删除/受验证复制，不提供父级 mute/效果编辑。普通子片段的参数仍可编辑。
- UI 已有新建/切换/打开子序列、完整嵌套、全祖先时长绑定确认和**同任务跨序列复制**；跨序列 CUT 明确禁止，同序列剪切只发一次原子 move。现有转场/机位构建器在子序列及含嵌套的主时间线禁用，避免错写根轨；内部已保存合法转场仍可渲染。没有自动序列克隆、多窗口编辑或自动生成机位嵌套配方。

### 兼容与显式时序

- 工程继续 `schema_version:1`，缺失的新字段采用中性默认并在读取/导入/撤销时补齐：`sequences:[]`、`active_sequence_id:null`、`sequence_id:null`；工作区 30 FPS、秒显示、snap=true、ripple=false、空源标记；新调色数值为 0、preset=none、RGB 曲线空、`lut_id:null`、文字 custom/none、`transition_in:null`。已保存秒数不因切显示/FPS 自动改写；未知字段和枚举报错，不静默丢弃。
- 工作区 24/25/30/60 FPS；整数帧与非丢帧 `HH:MM:SS:FF` 输入转换为秒。后端只对动作 split/move/duplicate/roll/slide 的 `at` 及 ripple_trim 的 duration 按 half-up 取整，不重取整邻居、源 trim 或标记。源 FPS、工作区 FPS、导出 FPS 相互独立。
- 源入出点是最多 200 个授权 ID 的 `0≤in_point<out_point≤3600` 源秒；保存不测 EOF、不自动插片段。客户端六帧范围吸附给出实际提交位置，后端不偷偷搜索目标；图片只有显示时长，代理不能用于精确原片记点。
- 普通 trim/delete 不波纹。`ripple_delete/ripple_trim` 只位移同轨从旧终点开始的后续片段，保留其他间隙、他轨、标记；`slip` 只改媒体源 trim；`roll` 调整连续两片段共同切点，`slide` 移动连续三片段的中间项并改两邻居。保持相应外端点/源范围，拒绝受影响重叠、锁定、少于一帧或容纳不了的淡化/关键帧；实际 EOF 仍由渲染核验。

### 类型化效果

- 视觉位置为 `(W-w)/2+W*x`、`(H-h)/2+H*y`，文字则是 `W*(0.5+x/2)`、`H*(0.5+y/2)`；不能混用坐标语义。先源裁切/旋转/镜像/fit，再颜色、动画、Alpha/蒙版及合成。普通片段保留列表层序，**含转场的轨道按起点稳定排序**；移除最后一个转场会恢复列表层序。文字最终在媒体上方，披露最后。
- 色彩顺序为基础 EQ → none/warm/cool/cinema/mono 预设 → temperature → shadows/highlights → hue → fade_amount → RGB 曲线 → 真实 LUT。色温/阴影/高光 −1..1、色相 −180..180°、褪色 0..1；每已提供 RGB 通道 2–8 点、x 递增且端点 0/1，y 不要求单调。warm/cool/色温当前用真正影响灰色像素的有界 RGB 曲线，不再用未起作用的中间调 `colorbalance` 代替效果证据。
- 编码视频末端现在先执行 `scale=out_color_matrix=bt709:out_range=tv` 的**实际矩阵/范围转换**，再 YUV420P 与 BT.709 frame/encoder 标签。此前只加标签会把 360p 的 BT.601 样本错标成 BT.709，lime 样例 green≈212 而非正确≈250；修复针对样本转换，不是调大颜色或放宽像素断言。PNG 仍走 RGB、GIF 走调色板；没有据此实现 HDR/ICC 色彩管理。
- `x/y/scale/opacity` 关键帧每属性 2–8 点，局部输出秒；四缓动为 linear、二次 ease_in/ease_out、三次 smoothstep ease_in_out。无任意表达式、旋转/滤镜/音频关键帧、运动跟踪或贝塞尔路径。
- 文字模板 custom/news/outline/gold/note 只覆盖渲染颜色/粗体/描边/阴影/背景，保留自定义字段、字号、位置、透明度；gold 为纯色不是渐变。none/typewriter/fade/pop 动画局部定时，打字机≤64 阶段、长字串成组揭示，非 ASR 逐词字幕。ASS/SRT≤256 KiB；SRT 不携带这些样式。

### 转场与机位不是演示状态

`transition_in` 在右片段上，含必填 `left_clip_id,kind,duration`，easing 默认 linear，**audio 默认 true**。仅 dissolve 与四方向 wipe；同轨两端起点/终点严格递增，**实际重叠恰好等于 duration**（仅 1e-12 序列化误差），时长 >0、≤1.2 秒、≥一工作区帧且≤两端各自一半，无第三片段占用重叠。两端预先满足全帧 cover、scale=1、x/y=0、opacity=1、无蒙版/chroma/关键帧/淡化/定格；不自动修正这些参数。

视觉只给入画施加覆盖率，参与者源 RGB 不透明，**此处不保留内在 Alpha**；擦除名称表示移动边方向。音频 true 在 tempo/effects 后、时间线 delay 前，对已有未静音音轨做互补**线性**淡化，独立于视觉缓动；false 保留原重叠混音并提示可能更响。不会用 acrossfade 缩短时间线。UI 初始不勾音频，不能与 API 省略 audio 的默认 true 混淆。

客户端先展示位移提案：从无缝边界将右段和同轨后续段前移 d，再确认保存；删除转场提案反向恢复。**这个显式提案可改变工程终点，渲染器不自动移动/裁切任何端点。** 删除/分割/跨轨移走绑定端点前须先明确移除转场；撤销/重做恢复完整合法快照。

手动多机位只有 **2–4 源、2×2 网格或≤12 段硬切清单**。显式 `sync_in` 编译为真实 `trim/start`；所有机位画面静音，另有一条连续主音轨。网格用 scale=0.5、中心偏移 ±0.25；切换时源 trim 为 sync_in+该切点。追加从 0 秒开始并保留旧轨/混音，替换需先解锁、移除原转场且明确确认；保留工程元数据。普通机位轨可以存入子序列后完整嵌套并二级编辑，因此 `camNest` 已是 partial；**没有自动机位配方转嵌套 UI**，构建器只在无嵌套主时间线开放。波形/源时间码自动同步、9/16 机位与实时多播放器仍缺。最终仍受活动展开 8 层/64 片段/16 解码输入/120 秒及 EOF/音轨/导出门禁约束。

## 5. 图片、LUT、代理及权限边界

以下均以 `/api/tasks/{task_id}/studio` 为前缀；完整错误表见 [STUDIO_API.md](STUDIO_API.md)。

| 路由 | 当前请求/成功回执 | 不可扩大为 |
|---|---|---|
| GET `/assets` | **200** `{images,luts,limits}`，读取校验后的任务内集合 | 全局素材库、外部贴纸库或 LUT 文件下载 |
| POST `/assets/image?expected_revision=n` | raw `image/png` 或 `image/jpeg`；**200** `{asset,project_revision,deduplicated}` | multipart/路径导入，或导入后自动插片段 |
| POST `/assets/lut?expected_revision=n` | raw UTF-8 `text/plain`；同样 **200**，不推进修订 | 预设冒充真实 LUT、自动绑定、AI 生成 LUT |
| GET `/sources/{source_id}` / `/preview/{source_id}` | **200/206** 原目录媒体/规范化图片；后一条是原源别名 | 新代理接口或时间线效果预览 |
| POST `/proxies` | 仅 `{expected_revision,source_id}`；新 job **202**，核验复用同一成功 job **200** 且 `cached:true` | 任意代理文件导入、后台自动全量生成、工程修订更新 |
| GET `/proxies` | **200** `{proxies:[job]}`；只读真实记录，未完成遗留 job 可标中断 | 自动转码、实际字节完整性已核验或自动重建 |
| GET `/proxies/{job_id}/media` | **200/206** 通过当前源/修订/内容核验后的私有 MP4；失效 **409** | 获准导出的时间线输出 |
| GET/DELETE `/jobs/{job_id}` | **200** 真实状态/取消并等待终态持久化；物理清理可仍 pending，授权 GET 仅重试一次清理；所有三种 job 共用 | 删除历史、伪进度、自动重新编码或保证物理删除成功 |
| GET `/outputs/{output_id}` | **200/206** 成功 render/export 附件，课堂批准及当前成片修订另查；proxy 被拒 **409** | proxy 的备用下载/绕过门禁 |

### 原始文件与索引

- 图片处理器：raw/规范 PNG≤8 MiB、单边≤4096、总像素≤8,847,360，拒绝 SVG/GIF/APNG/多图 JPEG。解码前去 EXIF/ICC/私有元数据并查几何，限界 probe 后重编码单帧 RGBA PNG，忽略 EXIF 方向。**普通合成保留源 Alpha；最终输出 PNG 为 RGB，不是透明输出。** `subtitles:none` 不会移除图片、Alpha 蒙版、普通叠字或强制披露。
- LUT 处理器：≤2 MiB、UTF-8、`LUT_3D_SIZE` 2–33、单位 domain、恰好 size³ 个有限 [0,1] RGB 行，red-fast 顺序；规范化九位有效数字/half-up。拒绝 1D、非单位域、路径/include/任意指令。`lut3d` 四面体插值在类型化调色之后执行，真正改变像素；**不是 AI**。
- 只有原子发布的严格 `AssetIndex` 能让规范文件进入目录；ID 是 `image_`/`lut_`+24 位内容散列前缀，全文 SHA-256/尺寸/格式重新校验，孤儿文件不算素材。索引/数据损坏拒绝，路径组件拒绝符号链接/junction，限界读取查文件身份/单链接/实际字节。取消须先等待 decoder/磁盘预约清理，不删除已换归属的目录。
- 图片仅视频/叠加轨，trim=0、speed=1、mute=true、无 reverse/freeze、其他音频控制默认；duration 为**手写显示时长**，不是目录 120 秒“实测 EOF”。LUT ID 不属于 source catalog。导入不改变工程修订/审批，不消耗渲染作业名额；最多 64 图片+LUT 合计，去重仍须权限/修订校验。备份 JSON 只含 ID/参数，不能跨任务携带或恢复实际媒体。
- **主应用限额已对齐：** [../backend/main.py](../backend/main.py) 只对精确 `POST /api/tasks/[0-9a-f]{32}/studio/assets/image` 和 `/lut` 分别采用 8 MiB/2 MiB。其他 JSON 仍≤256 KiB，录音另有 21 MiB；其他方法/路径别名不继承资产额度。无 `Content-Length` 仍 411，非法/超限仍 413；处理器继续计实际流字节及长度一致性。真实 main 大 PNG/LUT 已在 6-test、15.309s 专项通过，**不再是未解决的 256 KiB 接线矛盾**，也不放宽认证、CSRF、所有权或内容验证。

### 代理是私有 RAW 预览，不能变成输出或源 ID

- RAW 指**当前目录文件**，包括原上传/标准化视频/干净画面/final，不是仅指相机原始文件。输入为整个 >0 且≤120 秒的视频，不能借短入出点截取长源；拒绝图片、纯音频、封面流、Studio 输出/任意路径。固定 **profile version 2**：contain 640×360/30 FPS/H.264 1000 kbps/AAC 128 kbps/48 kHz stereo/BT.709，无音频源使用已有静音底，不制造语音。不应用当前时间线调色/LUT/转场/混音；原文件已经烧录的内容仍在，必要披露另烧全片。
- 新建前、编码后实际流式散列源；完成时散列输出。cache key 由**源 SHA-256、成片修订、完整固定 profile 与 disclosure/版本**组成，另核验 source_id/路径；不以 Studio 工程修订作 RAW 内容缓存键。命中返回旧 job、不新增编码/历史/审计，20-job 上限仍可复用有效结果。
- 新增空序列字段的兼容分支只接受**同一 profile 版本**下完全匹配的旧 RAW 模板/键及全部当前散列验证，不改旧回执，不接受混合/编辑/嵌套模板；旧 version 1 音频不能冒充修正后的 version 2。
- 每次 cache/媒体 Range 都校验 job/snapshot/manifest/Profile、当前身份/目录/成片状态及元数据，再读取实际源和输出 SHA-256；只看已存散列、大小/mtime 不够。≤1 MiB 分块/60 秒限界散列，取消先排空后台线程。重新授权在异步散列后执行，缓存回执还在释放预约后重查。读取预约持续到真实 Range 传输结束，不能把重命名后的编辑输出当 RAW 文件。
- 与原片相同的私有作者/教师或未归属旧任务令牌授权；**不要求课堂 can_export 或原 QC 零阻断**，但仍查当前 done 成片、源、披露和后台排他。这不是导出旁路，proxy 既不进入 `/outputs`，也不进入 source catalog；导出一直按原 `source_id` 解析。
- 前端挂载只 GET 列表，点击后先保存草稿并使用实际保存回执，再提交一次；完成不自动切换。未知提交/取消只读核对，不自动 POST/DELETE 重试。原片/代理切换尝试保留源秒，不改 trim/入出点；代理容器时长不写原片 EOF 表，当前帧精确记点需切回原片。列表成功记录可能已过期，必须以媒体路由核验为准。

### 共享限额与最小门禁状态

Studio 共享 **每任务 512 MiB、20 个 render/export/proxy job、100 次工程变更**；媒体操作每任务一个、进程全局两个，导入、代理准备/散列/传输也占相同活动额度。子序列不获得独立作业/磁盘/历史额度。资产临时区/日志/历史/输出计入同一磁盘额度；导入/代理还接已有 host 磁盘预约，不能当作分布式锁或压力测试结论。单输出<128 MiB、ASS/SRT≤256 KiB、活动展开最多 16 解码输入/32 全高清等效帧、最高本地1080p/120秒，更多限制见 API。

`StudioProps` 只要求 `taskId,request,onBack,onError`，另有 `accessToken?,classroom?,cloudActorScope?`；App 显式 `classroom:!legacy`。门禁只存严格类型的 task ID/整数成片 revision/status/boolean can_export/reasons，不复制整份 Work，不以真假值强转授权。GET 使用 same-origin/include/no-store/redirect-error、代次/取消校验；可见页 5 秒轮询，10 秒新鲜度从请求开始计算，隐藏/失效/失败即撤回衍生输出但不丢草稿。保存后、render/export 提交前再次获取同修订门禁；服务端每次仍独立授权。源代理不依赖也不修改这份导出门禁。

## 6. C25 分轮验证状态

> **本节为 C25 已完成历史。** 下文“当前/最终”均指该轮源码、构建及核验时点，不是 C26；计数、失败和完成结论原样保留。C26 当前快照单列第 8 节。

下列结果按**已经结束的具体轮次**记录；失败基线保留，专项复验不回写基线、不与其他轮次相加。当前最终结果已对齐源码与独立构建；不能用最终绿色记录重写此前失败。

### 浏览器：历史基线、定向复验与最终整轮

| 轮次 / 原始证据 | 实际结果 | 覆盖及处置 |
|---|---|---|
| [p25-pages-baseline](../canary_test/artifacts/p25-pages-baseline/summary.json) | **32/37 PASS，5 FAIL；341,438.722 ms** | 10 为真实文字对比度约 4.4:1；11 为标签定位，20 为队列页面位置变化，26/27 为嵌套 status 定位。修复后的最终结果单列，本轮仍为 FAIL |
| [p25-features-baseline](../canary_test/artifacts/round2/p25-features-baseline/summary.json) | **15/16 PASS，1 FAIL；310,734.838 ms** | 图片/LUT、帧/修剪、调色/文字、转场、手动机位、偏好与 C25P-01–03 代理通过。唯一失败 P25-01 是 closed-details 的旧几何盒被误算成可见溢出 |
| [p25-deep-baseline](../canary_test/artifacts/round2/p25-deep-baseline/summary.json) | **41/42 PASS，1 FAIL；278,939.176 ms** | 唯一失败 R2-20；42 项含 11 项明确模拟的启用态云 UI 契约，非腾讯实测 |
| [p25-layout-retest](../canary_test/artifacts/round2/p25-layout-retest/summary.json) | **1/1 PASS；21,120.336 ms** | survey 按 `checkVisibility()` 排除关闭 details 的旧几何，保留独立页面宽度断言 |
| [p25-layout-edit-verified](../canary_test/artifacts/round2/p25-layout-edit-verified/summary.json) | **2/2 PASS；21,547.939 ms** | 加入真正展开镜头/报告元数据的窄屏检查，并通过 R2-20 的真实编辑、撤销/重做和不可用/云导航边界 |
| [p25-pages-complete-20260926](../canary_test/artifacts/p25-pages-complete-20260926/summary.json) | **36/37 PASS，1 FAIL；275,607.424 ms** | 中断前较后整轮；唯一失败为旧分组删除按钮定位 |
| [p25-groups-final-retest](../canary_test/artifacts/p25-groups-final-retest/summary.json) | **FAIL：page crash** | 独立分组复验崩溃，不能归为已证实的分组业务根因 |
| [p25-groups-after-crash](../canary_test/artifacts/p25-groups-after-crash/summary.json) | **1/1 PASS；33,801.536 ms** | 随后单项通过，不改写上一轮崩溃 |
| [p25-deep-complete-20260926](../canary_test/artifacts/round2/p25-deep-complete-20260926/summary.json) | **60/62 PASS，2 FAIL；756,039.077 ms** | C25A-03 page crash；P25-08 出现额外草稿 POST，后者是实际 App 缺陷 |
| [p25-sequences-browser-retest](../canary_test/artifacts/round2/p25-sequences-browser-retest/summary.json) | **4/4 PASS；117,789.323 ms** | 序列专项，不能代替最终整轮 |
| [p25-pages-closure-20260926](../canary_test/artifacts/p25-pages-closure-20260926/summary.json) | **runner FAIL，0 用例执行；118,433.971 ms** | 页面种子尚未就绪时失败；不是 37 项业务失败，不由脱敏通用错误推断内部异常 |
| [p25-deep-closure-20260926](../canary_test/artifacts/round2/p25-deep-closure-20260926/summary.json) | **61/63 PASS，2 FAIL；848,642.735 ms** | R2-28、P25-07 的 `spawn ffprobe ENOENT` 来自测试 shell PATH，非生产编码故障 |
| [p25-media-path-retest-20260926](../canary_test/artifacts/round2/p25-media-path-retest-20260926/summary.json) | **2/2 PASS；64,263.202 ms** | 修正测试 PATH 后两项均通过；随后重新播种并完整执行 63 项，不拼接为整轮通过 |
| [p25-pages-final-20260926](../canary_test/artifacts/p25-pages-final-20260926/summary.json) | **37/37 PASS；322,225.950 ms** | 当前源码独立构建的最终页面整轮 |
| [p25-deep-final-20260926](../canary_test/artifacts/round2/p25-deep-final-20260926/summary.json) | **63/63 PASS；855,637.336 ms** | 52 项真实本地后端 + 11 项明确模拟的启用态云契约；含 C25S-01–04、C25P-01–03、C25A-01–04、P25-01–09、新增 C25D-01 |

最初五轮早于最新序列/DSP 修复；当前验收依据是表尾两个最终整轮，均为 **Edge、1 worker、0 自动重试**。最终两轮未发生崩溃，**不证明历史 page crash 的根因已修复**。63 项中的模拟云契约不是腾讯 live 或供应商质量验证；也没有逐项证明 113 个复合工具全部完成。

关键失败处置：

- **页面 10 的对比度是真缺陷，不是测试误报。** [../frontend/src/components/CreateWizard.tsx](../frontend/src/components/CreateWizard.tsx) 将 danger 文本设为 `#963e22`，已进入最终独立构建及页面整轮；没有降低 axe 规则，人工检查边界见下文。
- **页面 11/20/26/27 是旧测试定位/流程假设。** 测试已适配当前标签、嵌套状态与队列页面；老师/学生都默认 create，导航为“工作室导航”，不能为满足旧测试恢复老师默认管理页或改变授权。
- **分组定位改用当前“删除组并取消全部序列归属”。** 测试保留业务锁和全工程归属边界，不回退生产行为来迁就旧名称；历史 36/37 与崩溃记录仍保留。
- **P25-01 是可见性测量问题。** Edge 在响应式变化后可给关闭 details 的后代保留非零旧盒；修正使用原生 `checkVisibility()`，不是放宽屏宽。随后显式展开元数据也通过，避免把真实可见溢出一并过滤。
- **R2-20 原 1.05 秒落在六帧吸附范围，实际合法吸回 1 秒。** 移动测试改为范围外且在 30 FPS 网格上的 **1.3 秒**，独立 P25-03 继续覆盖吸附。复验还实际确认 `voiceClone` 不可用且按钮 disabled，`aiTrans` 仅导航到真实默认关闭云面板、作业仍空；不是跳过 AI 边界或新增云调用。
- **代理不是“仅类型/路由待接”。** 当前 profile v2 的 C25P-01–03 在最终整轮再次通过：创建 202、解码、缓存 200、只读重开/源记点、导出门禁与私有权限区分、旧修订拒绝及 320px/20px。兼容仍仅同版本旧空 schema，不接受旧 v1、编辑/嵌套模板或外部代理导入。
- **冷深链误保存已修复，不能通过削弱断言掩盖。** [../frontend/src/App.tsx](../frontend/src/App.tsx) 对 work/show hash 初始使用只读 history；仅真实 create 且未处于打开作品过程中才自动保存/挂载向导。真正从创作页显式 `openWork()` 仍沿编辑链 flush 一次，读取 GET 本身不触发草稿 POST。C25D-01 挂起真实私有 GET，收到非空第 3 步草稿后，在挂起期间及打开后各推进时钟 **1,500 ms**；浏览器写请求 **0**、完整草稿严格不变。P25-08 原有“恰好一次 workbench POST”断言未放宽，最终通过。

### 后端：历史基线、专项与当前全回归

| 轮次 / 原始证据 | 实际结果 | 当前结论 |
|---|---|---|
| [完整后端基线](../canary_test/artifacts/prototype-20260925/backend-full-baseline.log) | **1,056 tests；522.099s；1 failure、3 skipped** | 唯一失败是旧 `setparams` 空转场命令精确相等断言，已修正；原日志仍为 FAIL，不是完整通过记录 |
| [主应用限额与标签专项](../canary_test/artifacts/prototype-20260925/host-and-tags-verified.log) | **6 tests PASS；15.309s** | 包含实际 main 大 PNG/LUT，经新的精确路径 8 MiB/2 MiB 准入；不再挂在 256 KiB 旧上限 |
| [音频/偏好/转场复验](../canary_test/artifacts/prototype-20260925/audio-clock-integration-retest.log) | **96 tests PASS；40.811s** | 共享 DSP/1× 音频等专项；发生在后续序列整合之前，不能代替其后的完整回归 |
| [序列/代理/转场专项](../canary_test/artifacts/prototype-20260925/sequences-final.log) | **132 tests PASS；62.466s** | 序列图/动作/锁、真实媒体/像素/音频、代理与转场组合的已结束记录；不是全部后端模块 |
| [中断前完整后端](../canary_test/artifacts/prototype-20260925/backend-final.log) | **1,121 tests；1,118 PASS、3 SKIP；582.356s；OK** | 已结束的历史记录；其后源码有修改，不替代当前回归 |
| [当前最终完整后端](../canary_test/artifacts/p25-closure-20260926/backend.log) | **1,121 tests；1,118 PASS、3 SKIP；937.139s；exit 0** | 当前源码全量结束；包含序列、共享 DSP、1× 旁路与实际 BT.709 转换，不把 skip 写成 pass |

早期失败证据仍保留，包括[非零起音/颜色断言](../canary_test/artifacts/prototype-20260925/features-clock-color-verified.log)、[旧 LUT 目录断言](../canary_test/artifacts/prototype-20260925/assets-integration-baseline.log)、[音频整合初跑](../canary_test/artifacts/prototype-20260925/audio-clock-integration.log)及[序列中间轮](../canary_test/artifacts/prototype-20260925/sequences-verified.log)。文件名含 verified 也不能覆盖其中的实际失败；当前状态由上表最后的当前源码全量结果说明。

### 前端与最终构建

- 历史[序列客户端复验](../canary_test/artifacts/prototype-20260925/sequences-client-retest.log) **51 PASS、0 FAIL；1,191.0777 ms**；较早[时间线 17](../canary_test/artifacts/prototype-20260925/timeline-unit.log)、[组合 23](../canary_test/artifacts/prototype-20260925/compositions-baseline.log)、[工作台偏好 12](../canary_test/artifacts/prototype-20260925/workbench-preferences.log)、[图片/LUT 40](../canary_test/artifacts/prototype-20260925/assets-client-baseline.log)、[代理 50](../canary_test/artifacts/prototype-20260925/proxy-client-retest.log) 均保留，不与当前轮次重复相加。
- **当前 7 套 Node 共 238 PASS**，逐套独立日志如下；它们是单元/隔离契约，不是额外浏览器用例：

| 当前 Node 套件 / 原始日志 | PASS |
|---|---|
| [会话](../canary_test/artifacts/p25-closure-20260926/test-classroom-session.log) | 45 |
| [时间线](../canary_test/artifacts/p25-closure-20260926/test-timeline-editing.log) | 17 |
| [工作台偏好](../canary_test/artifacts/p25-closure-20260926/test-workbench-preferences.log) | 12 |
| [组合](../canary_test/artifacts/p25-closure-20260926/test-studio-compositions.log) | 23 |
| [素材](../canary_test/artifacts/p25-closure-20260926/test-studio-assets.log) | 40 |
| [代理](../canary_test/artifacts/p25-closure-20260926/test-studio-proxy.log) | 50 |
| [序列](../canary_test/artifacts/p25-closure-20260926/test-studio-sequences.log) | 51 |

- 应用及两套 E2E TypeScript 均 **exit 0**。当前 Vite 构建实际 **1,793 modules、4m 1s**；[独立构建 manifest](../frontend/dist-canary-p25-closure-20260926/ASSET_MANIFEST.sha256) 含 **4 个资源**，不是共享构建。
- 4 个资源在 **8768/8769/8770** 的实际 HTTP 字节/散列均一致，见[页面 HTTP 核验](../canary_test/artifacts/p25-closure-20260926/http-pages-final.json)、[深层 HTTP 核验](../canary_test/artifacts/p25-closure-20260926/http-deep-final.json)。上表两个最终浏览器整轮使用这一构建，不沿用旧构建结论。
- [环境检查](../canary_test/artifacts/p25-closure-20260926/environment-checks.log)：compile、pip check、环境验证器、离线 uv 检查均通过；[Python 审计](../canary_test/artifacts/p25-closure-20260926/python-audit.json) **76 包、0 漏洞、0 跳过**，[npm 审计](../canary_test/artifacts/p25-closure-20260926/npm-audit.json) **0 漏洞**。后端测试的 3 SKIP 与依赖审计的 0 跳过是不同口径。

### 可访问性、完整性与服务清理

- 使用[浏览器与完整性已核验记录](../canary_test/artifacts/p25-closure-20260926/browser-and-integrity-verified.json)，不使用路径编码受损的旧版。**163 份 axe：0 violations**；仍有 color-contrast **1,764**、video-caption **45** 个 incomplete **节点出现次数**，不是唯一缺陷数，也不是 WCAG 全部通过。
- **31 份布局报告、150 个样本**：文档水平溢出/可见元素溢出均 0；**100 份用例 observations**：problems/errors 均 0。只涵盖记录的视口与场景，不代替人工屏幕阅读器/键盘/像素级验收。
- 页面原始文件 **564** 个散列全部一致；深层原始文件 **259** 个在关闭后同样全部一致，两套清单**分别统计，不相加**。受检 **84 份源码、共享前端 4 个资源及历史付费回执**散列未变，结论绑定验收基线至核验时点。
- [关闭记录](../canary_test/artifacts/p25-closure-20260926/shutdown.json) 确认本轮所属 **8768/8769/8770 已停止，errors=[]**，TEMP 和失败证据保留。恢复时 8000、8766–8771 已无监听；没有停止用户进程，**不宣称保留了此前旧 PID**。共享前端未被覆盖，**本轮修复未部署到共享构建或旧服务**。
- 参数字符串、实际解码 PCM/像素/ASS 与 UI 流程分别提供证据；合成波形不证明人物听感或 ASR 等价。最终无崩溃不证明旧崩溃根因消失；离线通过不等于腾讯 live、生产/Linux/负载、实物打印或麦克风音质验收。

## 7. 剩余产品缺口与最终收口

> **本节收口表及结论属于 C25 历史快照。** 所列产品缺口、限额、费用和外部条件在 C26 仍保留；当前回归与 High 运行时风险见第 8 节，不能用本节历史完成结论替代。

**已有实现不回退为“未实现”：** `tlPick/nest/camNest` 是有界 partial，RAW profile v2 代理及序列相关行为已纳入当前最终深层整轮；主应用资产限额已对齐并通过真实请求专项。[WORKBENCH_API.md](WORKBENCH_API.md) 已纳入两个偏好字段，逐 ID 边界统一见 [PROTOTYPE_CAPABILITY_MATRIX_20260925.md](PROTOTYPE_CAPABILITY_MATRIX_20260925.md)。

| 收口项 | 已完成事实与保留边界 |
|---|---|
| 完整后端与前端契约 | 当前 1,121 tests（1,118 PASS/3 SKIP）、937.139s、exit 0；7 套 Node 238 PASS。旧完整基线失败/中断前通过各自保留，不当当前结果 |
| 同构建浏览器 | 三套类型检查及独立 Vite/4 资源 HTTP 核验通过，最终页面 37/37、深层 63/63；含序列、profile v2、冷深链与原 P25-08 严格断言。11 项模拟云契约不是腾讯实测 |
| 能力目录一致性 | 113 唯一 ID 与原型精确匹配：58 partial/8 metadata/11 renderer/36 unsupported；`edit`、`marker`、`frameCrop`、`expCs` 的 reason 与序列 `clone` 边界已同步，不再称滞后，也不据 available 称复合工具全支持 |
| UI/API 差异 | 转场 UI audio 初始 false，API 省略默认 true；父嵌套 API 接受 identity mute，UI 暂无可编辑父音频面板；向导与省略字段的服务端默认需分别解释 |
| 完整性与部署 | 两套原始文件清单、受检源码/共享资源/付费回执散列不变；仅关闭所属验收服务，修复未部署到共享前端或旧服务；生产与硬件仍未验收 |

本地仍缺**独立嵌套合成组与父级任意范围/变速/效果**、9/16 机位及自动波形/源时间码同步、多麦同步、录音美化/独立变调、完整音色库/批量 TTS、速度曲线、旋转/滤镜关键帧、区域调色/完整色轮 HSL、文字蒙版、人物/物体/蒙版跟踪、复杂/钢笔蒙版、高级混合/修边、稳定器、透明/HDR/>1080p/序列帧输出、完整批量导出、外部工程/文件夹/Live Photo、团队同步和社交 OAuth 等。部分已支持 ID 仍有缺失子项，不能只统计 unsupported 整行，也不再把“多时间线/嵌套”整体列缺。

独立云操作仍仅 `reference_narration,subtitle_translation,video_super_resolution,video_interpolation,audio_denoise,smart_subtitles` 六种，见 [CLOUD_API.md](CLOUD_API.md)；导航不提交云任务、不升级本地分类。腾讯凭据、私有桶/获准地域、审核模板/语种、费率预算条件仍缺。克隆、人物编辑、对口型、社交发布另缺生产实现与独立权利/人物/平台同意；一次性测试许可不是持续预算或上传可识别学生素材的许可。**本轮新增付费调用 0；历史 Kimi 100 分预约仍为 HELD、实际账单未知**，不重跑、重置或扩额。

当前结论是**C25 有界实现及当前源码/独立构建回归闭环完成，历史失败与剩余子功能缺口明确保留**。证据来自隔离 TEMP 验收，不等于原始历史成片的质量问题已修复、全部 113 工具实现、生产上线或硬件验收。

## 8. C26 当前快照（2026-09-26）

**本节记录已有证据，不重跑测试、不拼接轮次，也不覆盖 C25 历史。受限回归通过与 High 原生运行时风险未解决同时成立。**

### 已结束的最终整轮与构建绑定

| C26 证据 | 实际结果 / 范围 |
|---|---|
| [完整后端](../canary_test/artifacts/c26-audit-20260926/backend-full.log) | **1,148 tests：1,145 PASS、3 SKIP，623.067s，exit 0**；包含 24 项 Studio 审查契约和 3 项真实主应用授权场景 |
| Node：[会话](../canary_test/artifacts/c26-audit-20260926/test-classroom-session-final.log)、[时间线](../canary_test/artifacts/c26-audit-20260926/test-timeline-editing-final.log)、[偏好](../canary_test/artifacts/c26-audit-20260926/test-workbench-preferences-final.log)、[组合](../canary_test/artifacts/c26-audit-20260926/test-studio-compositions-final.log)、[素材](../canary_test/artifacts/c26-audit-20260926/test-studio-assets-final.log)、[代理/作业](../canary_test/artifacts/c26-audit-20260926/test-studio-proxy-final.log)、[序列](../canary_test/artifacts/c26-audit-20260926/test-studio-sequences-final.log) | **45/17/12/25/40/70/51 PASS，共 260**；单元/隔离契约，不计为浏览器或媒体用例 |
| [最终独立构建清单](../frontend/dist-canary-c26-final-20260926/ASSET_MANIFEST.sha256) | 应用与两套 E2E 类型检查通过；Vite **1,793 modules、7.20s、4 个资源**；不覆盖共享前端 |
| [页面 HTTP 核验](../canary_test/artifacts/c26-audit-20260926/http-final.json) / [最终深层 HTTP 核验](../canary_test/artifacts/c26-audit-20260926/http-verified.json) | 最终页面 8768 与新深层 8769/8770 的实际 HTTP 字节/散列均匹配该独立构建；深层使用后一个核验的运行身份，不沿用已崩溃主机 |
| [最终页面](../canary_test/artifacts/c26-pages-final-20260926/summary.json) | **37/37 PASS，309646.872ms** |
| [最终深层](../canary_test/artifacts/round2/c26-deep-verified-20260926/summary.json) | **69/69 PASS，843674.39ms**；**58 项真实本地后端 + 11 项明确模拟的启用态云契约，非腾讯实测** |

两个最终浏览器整轮均为 **Edge、1 worker、0 自动重试**。完整后端结束后，生产代码**仅再改窄屏 CSS**；代理切回监看精度是后续**测试侧**改动，不是后端行为变更。[最终来源记录](../canary_test/artifacts/c26-audit-20260926/final-source.json)明确 `backendSourceUnchanged:true`、`changesAfterBackendRun` 仅列样式文件。最终深层开启 `-X faulthandler` 只是诊断，不改变 Python 版本或应用实现。

### C26 修复的实际范围

- **作品打开与键盘导航：** [../frontend/src/App.tsx](../frontend/src/App.tsx) 的 `cancelWorkOpening()` 在相关导航统一取消代次、AbortController 和打开状态，包括重选当前页、新建空白、hash Back、返回编辑和放映。过期私有 GET 不再覆盖新导航；保留原确认/草稿 flush 语义，不宣称全站内部导航都已改成浏览器历史栈。“跳到主要内容”仍可 Tab→Enter，只聚焦/滚动 main，保留作品 hash，刷新仍定位原作品；不增加写请求。
- **完整工程 ID：** [../frontend/src/lib/studioCompositions.ts](../frontend/src/lib/studioCompositions.ts#L209-L223) 的分配器收集根与**全部子序列（含未激活）**的轨/片段/标记，以及序列/组 ID。追加和替换都避让全局命名空间并保留子工程/元数据；不是扩大机位数、嵌套效果或构建器开放范围。
- **等待后的写授权：** [../backend/studio.py](../backend/studio.py) 对普通工程保存/导入、动作（含 undo/redo）、SRT 导入在 body 后重新执行主机 `write=True` 授权；render/export 在异步准备结束、持久作业准入**紧前**再次执行。随后仍检查当前 record/root、done 状态、成片及预期工程修订。3 个真实 main 场景证明教师撤销、作者取消核对和注销在等待后生效，不以模拟授权响应代替；私有预览与导出门禁保持分离。
- **真实终态与残留清理：** `cleanup_pending` / `cleanup_error` 是可省略的成对字段；出现时前者必须是真 boolean，pending=true 仅用于 failed/cancelled/interrupted，后者是非空、**≤256 字符**的泛化提示，清理成功为 false/null。失败终态 `output_id:null`、无 `result`；`rmtree` 失败不把已取消/中断改成 running 或成功，元数据可写时照实持久化。残留继续计入原 Studio 配额。授权 job GET 每次只尝试一次清理，不重新编码/调用供应商/建 job，不改原错误、结束时间或工程历史；UI 显示残留警告及“读取状态并重试清理”，迟到 pending 响应不能复活已确认清理/终态。
- **发布与根身份：** 输出 manifest 写入成功后才赋 succeeded/result/output ID。已移除、链接或被替换的任务根不被重建、写入或删除以伪造成功回执；负向契约覆盖清单/终态存储错误和根替换。持久化失败仍报错，job 与 audit 的分别原子写入**不是断电事务**。
- **布局与格式文案：** `body` 的 `min-width:0` 修正 320px 窗口减 15px 常驻滚动条后只有 **305px** 可用宽度的问题，不隐藏溢出或缩小字号；匿名登录/结果/Studio ×16/20px 共 **6 个新样本**通过。集成浏览器观察到真实 305px；headless 回归明确只是可用宽度模型，不模拟原生 OS 滚动条。Studio 格式文案改为已有的**实际 BT.709 矩阵/有限范围转换加标签**，不是只改标签，也没有新增 HDR/Rec.2020/ICC 支持。

普通 render/export 源仍采用**大小/mtime 指纹**，导入图片/LUT 与 RAW 代理有各自的实际散列契约。不要把 C26 根身份负向验证扩大为所有普通源全文重散列、对特权并发文件写入者的 OS 沙箱或媒体 DRM；已交付的字节不能撤回。

### 失败基线与专项复验（均保留，不拼成最终整轮）

| C26 轮次 / 证据 | 实际结果与处置 |
|---|---|
| [后端复现基线](../canary_test/artifacts/c26-audit-20260926/backend-repro-baseline.log) → [扩展清理回归](../canary_test/artifacts/c26-audit-20260926/backend-cleanup-extended.log) | **20 tests / 20 failures，2.282s → 24 tests PASS，4.015s**；旧授权写入、终态残留和 GET 500 为真实失败，后者扩充了持久化/根身份负向场景 |
| [真实主应用授权基线](../canary_test/artifacts/c26-audit-20260926/host-auth-repro-baseline.log) → [授权/清理复验](../canary_test/artifacts/c26-audit-20260926/backend-auth-cleanup-retest.log) | **3 tests / 3 failures，22.275s → 3 个主应用场景通过**；后一个合并专项共 **23 tests PASS，21.800s**，不把 23 都称为主应用测试 |
| [组合基线](../canary_test/artifacts/c26-audit-20260926/composition-repro-baseline.log) → [组合复验](../canary_test/artifacts/c26-audit-20260926/composition-retest.log) | 原 23 PASS、新增 append/replace 两项失败 → **25 PASS**；最终 Node 260 已含这些用例 |
| [新增 UI 基线](../canary_test/artifacts/round2/c26-ui-repro-baseline-20260926/summary.json) → [五项复验](../canary_test/artifacts/round2/c26-ui-retest-20260926/summary.json) | **0/3 → 5/5**；返回、跳转主区、重选、新建、机位真实保存/撤销/重开 |
| [305px 基线](../canary_test/artifacts/round2/c26-scrollbar-baseline-20260926/summary.json) → [复验](../canary_test/artifacts/round2/c26-scrollbar-retest-20260926/summary.json) | **0/1 → 1/1**；一项用例含六个宽度/字号场景 |
| [第一次完整深层](../canary_test/artifacts/round2/c26-deep-final-20260926/summary.json) → [代理定向复验](../canary_test/artifacts/round2/c26-proxy-clock-retest-20260926/summary.json) | **68/69，806547.705ms → 1/1，34712.213ms**；唯一失败是原生监看读回差 1 微秒，不是精确持久标记被改写；定向通过不替代最终 69/69 |
| [后续 closure 深层](../canary_test/artifacts/round2/c26-deep-closure-20260926/summary.json) | **61/69，744784.912ms**；C25S-02 功能断言完成后清理传输失败，另 7 项后续连接失败，原因是 8769 原生主机崩溃；不是 8 个已证实业务缺陷，也不能拼接为通过 |

[独立真实媒体实验](../canary_test/artifacts/c26-audit-20260926/native-seek-precision.json)在真实样例字节上、不经 App 假播放或合成媒体事件，复现 Edge 将 `currentTime=2.007592` 读回 **2.007591**。只将代理**切回监看**断言由小于 0.5 微秒调整为**1 个原生微秒 tick + 浮点噪声**；所有持久源标记、trim/入出点、字节、授权和缓存精确断言不变。此前 68/69 与后续原生崩溃是两个不同失败，不能混为“计时已修所以崩溃已修”。

### High 未解决运行时风险与关闭证据缺口

**High：Windows Python 原生崩溃根因仍未定位/修复。** [原生事件证据](../canary_test/artifacts/c26-audit-20260926/native-host-crash.json)记录 2026-09-26T14:23:26Z、Python 3.11.9 / [python311.dll](../canary_test/artifacts/c26-audit-20260926/native-host-crash.json#L29-L33)、`c0000005`、偏移 **0x205cbe**。随后完整 69/69 使用 `-X faulthandler` **仅增加诊断**；没有升级 Python、收集内存/认证 dump，亦无根因修复证据。不得据绿色重跑宣称所有 bug 已修、宿主稳定或生产通过。

崩溃轮绕过 shutdown/finally，原 manifest 仍为 `startup_complete`；[崩溃后独立源核验](../canary_test/artifacts/c26-audit-20260926/crash-source-recheck.json)确认 **259 文件全匹配**，没有重写原清单或冒称正常退出。

最终[关闭核验](../canary_test/artifacts/c26-audit-20260926/shutdown-verified.json)记录本轮所属 **8768/8769/8770 全停、监听为空、验收进程 0**，未停止用户进程，全部 TEMP/失败记录保留。但**仅 page 主机正常写入 stopped**；两个重定向诊断 deep 终端 Ctrl-C 后进程虽退出，manifest 的 finally 未完成，仍为 **startup_complete、stoppedAt 缺失**。`errors:[]` **不能推出 deep 正常关闭**，`deepOrderlyShutdownRecorded:false` 明确保留。[最终 deep 独立核验](../canary_test/artifacts/c26-audit-20260926/final-deep-source-recheck.json)另证 **259 原文件散列全匹配**，同时明确不改写原 manifest、不声称 orderly shutdown。文件未变、进程已停、正常 lifespan 完成是三种不同结论。

### 最终完整性、可访问性与未扩大的能力

- [最终浏览器/完整性 verified 记录](../canary_test/artifacts/c26-audit-20260926/browser-integrity-verified.json)：页面 **564 原文件 SHA 全匹配**；最终 **84 源码/依赖声明**自最终来源基线至核验时点未变，不是说 C26 相对 C25 未改源码。共享前端 **4 资源**、此前 C25 独立构建、当前 C26 构建及唯一付费回执均未变。page 564 与 deep 259 分别核验，不相加为唯一文件数；本轮修复未部署到共享构建或旧服务。
- **163 份 axe、0 violations**；incomplete 为对比度 **1,764**、字幕 **45** 个节点**出现次数**，不是唯一缺陷数或 WCAG 全部通过。**31 份布局报告 / 150 矩阵样本，0 溢出**；另有上述 **6 个 305px 新样本**，不冒称已包含在 150 内。**106 份 observations，problems/errors 均 0**，不替代人工键盘/读屏/人物听感或全视口验收。
- 只读对照[原型实际目录](../金话筒新闻视频生成/金话筒%20·%20原型.dc.html#L585-L597)与[当前注册表及分类分支](../backend/studio.py#L161-L264)：**113 唯一 ID 精确对应，58 partial / 8 metadata / 11 renderer / 36 unsupported**。矩阵 **001–113 全行及缺失子项不改**；已有本地子集继续可用，不提升分类，不扩大序列/机位/素材/作业/磁盘/导出限额。
- **新增付费调用 0**；历史 Kimi **100 分 HELD、实际账单未知**不重试/重置/扩额。腾讯凭据、私有桶、获准地域、模板/语种、审核费率与预算仍需补齐；11 项模拟启用云契约不改变这些条件。全部 partial 子功能缺口、unsupported、人物/声音权利、生产 Linux/负载、实物麦克风/打印及原历史成片 QC 问题仍保留。

**当前结论：C26 指定源码/独立构建的受限回归已结束，但 High 原生运行时风险和 deep 正常关闭证明缺口未解决；不是全部 113 工具完成、所有缺陷清零或生产准入。**