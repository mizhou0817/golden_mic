# 三模式实现与验收边界（2026-09-28）

> **FINAL：本轮 coding todos 已完成，本地 synthetic 有界验收通过；尚未部署日常 8000 或生产。** 完成结论只覆盖本轮实现和已验证范围，不表示整个产品/全部生产能力已签收。API 详见 [THREE_MODE_API_20260928.md](THREE_MODE_API_20260928.md)，最终证据见 [THREE_MODE_VALIDATION_20260928.md](THREE_MODE_VALIDATION_20260928.md)，总入口见 [README](../README.md)。

## 1. 依据与证据分层

设计依据是 [三模式 UI 设计交接文档](../金话筒%20·%20三模式%20UI%20设计交接文档（GPT-6%20Astra）.md) 与 [三模式独立原型](../金话筒%20·%20三模式（独立版）.html)。原型中的模拟转写、计时、声音、示例人物、预检速度和“永远保留”等文字不是后端事实。冲突时本文明确列出当前代码语义，而不修改原始设计文件。

| 层次 | 当前可说的结论 | 不能推导的结论 |
| --- | --- | --- |
| 源码实现 | 已有上传、三模式流水线、原声编辑、发布检查、简单导出及只读演示的实际调用链。 | 文件存在不等于运行通过，更不等于新闻可直接发布。 |
| 本地合成验收 | 最新 safe full 后端 1118 项：1116 pass、2 skip、0 failure/error、887.556619 秒；前端 459 pass、3 项 typecheck 通过；最终 L browser 8/8、262.979523 秒、0 retry。 | J 7 pass / 1 fail、K 6 pass / 1 fail / 1 未运行仍是失败历史；L 不覆盖真人质量、任意长片/负载或整个生产环境。 |
| 真实人声/模型质量 | 当前文档没有新增可据以签收的真人语音、真实 ASR 词时/分说话人质量或正式新闻试听证据。 | 合成音调、人工文本/词时、假 Provider 即使用真实 FFmpeg，也不是真人声学对齐验收。 |
| 生产/性能 | 三模式未部署日常 `http://127.0.0.1:8000`；生产、长片/负载/稳定性与性能 SLA 未验收。 | 2026-09-27 的已部署与已验证结果不继承到三模式。 |

准确 run 来源、计数及边界见[最终验收记录](THREE_MODE_VALIDATION_20260928.md)。后端证据为 `workspace-validation-20260928-132643-628340`；前端 459/3 项 typecheck 来自主集成反馈，不充当本次重跑。2026-09-28T13:53:56.623Z 的独立只读复核确认前端 source 31、build 4、backend 106、harness 绑定 source 56、test 11 的散列全部匹配；集合可能重叠，不相加为唯一文件数。最终独立 [L 构建清单](../frontend/dist-canary-modes-20260928-l/ASSET_MANIFEST.sha256) SHA-256 为 `27bb22d96b0eab93b1a6fab91a6cbff5ed1f3df10d4cf4e4f2b57191ce23a7fd`。L 主机已正常完成 lifespan shutdown，8786 无监听；缺失字段与退出回调限制见下表。本次仅更新四份现有文档，不启动服务、构建、测试或 Provider，不触碰实际环境与用户数据。

## 2. 当前实现映射

| 部分 | 实际源码 / 规格 | 已实现内容与边界 |
| --- | --- | --- |
| 共享模式规则 | [mode_rules.json](../backend/mode_rules.json)、[production_modes.py](../backend/production_modes.py)、[productionModes.ts](../frontend/src/lib/productionModes.ts) | 三种句型约束、确定性分句、文本匹配、真实 take、trim、跳切及质量项。前后端共读版本化常量，不用原型随机结果代替。 |
| 上传/预处理 | [uploads.py](../backend/uploads.py)、[uploadSessions.ts](../frontend/src/lib/uploadSessions.ts)、[media_input.py](../backend/media_input.py) | 8 MiB 分块、独立 capability、散列/媒体检查、私有缩略图/波形、ASR/断点状态与有界缓存。GET/恢复不发起自动计费重试。 |
| 创建/预检/状态 | [main.py](../backend/main.py)、[models.py](../backend/models.py)、[task_manager.py](../backend/task_manager.py) | JSON 引用已上传素材；确认句子入任务；匹配预检、十阶段权重、错误状态、72 小时终态保留、1 执行/5 pending。legacy multipart 留在单独分支。 |
| 三模式媒体链 | [mode_pipeline.py](../backend/mode_pipeline.py)、[pipeline.py](../backend/pipeline.py) | 真源时钟、原声 PCM 截取、旁白单独 TTS、自录对齐、跳切画面、片段缓存、QC/报告及来源清单。无缺源时假装成功。 |
| 逐句编辑/修订 | [workbench.py](../backend/workbench.py)、[revisions.py](../backend/revisions.py)、[QuoteEditor.tsx](../frontend/src/components/QuoteEditor.tsx)、[ResultWorkbench.tsx](../frontend/src/components/ResultWorkbench.tsx) | 先记下再批量应用；原声只连续缩短/换 take/删除，B 可显式转旁白；临时修订生成后提交，失败保留前一成功版本。 |
| 字幕/图文 | [graphics.py](../backend/graphics.py) | ASR 原话字幕、真实词时或明确段级降级、54/72 字号、标题、人名条和强制生成内容披露，均有规范元数据绑定。 |
| 发布/导出 | [publication.py](../backend/publication.py)、[studio.py](../backend/studio.py)、[studio_render.py](../backend/studio_render.py) | 服务端版本绑定确认、正式导出门禁、MP4/无 BGM MP3/前 6 秒 GIF；三模式 simple 固定计划独立时长预算 min(600, config)，128 MiB 与高码率拒绝保留；legacy Studio 120 秒不变。 |
| 页面/旧存档 | [Workspace.tsx](../frontend/src/components/Workspace.tsx)、[submissionRecovery.ts](../frontend/src/lib/submissionRecovery.ts)、[CreateWizard.tsx](../frontend/src/components/CreateWizard.tsx)、[Processing.tsx](../frontend/src/components/Processing.tsx)、[StudioDemo.tsx](../frontend/src/components/StudioDemo.tsx) | 记者三步向导、真实状态、草稿/上传凭据恢复、task 绑定的同浏览器 30 天失败提交快照与 legacy 保留；专业剪辑入口只读。 |

## 3. 不能混淆的模式与来源语义

1. **A 全旁白，默认全 AI，不自动同期声。** 识别到素材里有人说同样的话，只建议切换 B；A 的正式句子仍为 `narration`。显式自主录音是独立用户选择，不能把旧流水线“高置信度同期声替换 TTS”描述套到 A。
2. **B 由用户明确 `quote`。** 类型 chip/行首标记只帮助构造已确认清单；原声找不到不能静默用 AI 念，转旁白必须是单独显式操作。C 全 `quote`，**从不调用 TTS**，不能通过参数/录音编辑绕过。
3. **保留 original client accepted sentences。** 正式流水线消费客户端确认的文字、顺序、类型与 source hint，不用模型重写稿件或重分 C 选句。创建入口校验稿件与清单相符，只有缺清单时做确定性解析。
4. take 的 `start/end`、词 `s/e` 是原始上传绝对秒数；prepared 素材对应 `norm_time = original_source_time - prepared_start`。源媒体散列、音轨起点、规范化偏移、实测 PCM 区间与输出时间线分开存证，不能把最终片段零起点冒充原素材时间。
5. 未提供/不可信的词时必须降级 `precision:segment`，整段展示；不得均分文本生成词时间。声学正确性仍依赖真实上游证据，结构校验不证明“识别到的是这个人”。说话人 ID 是录音内聚类，跨文件不自动合并实名。
6. 规范化编辑距离只用于匹配；C 还检查真实 ASR 的连续子串，不靠高分接受增字/同音替换/中间删词。合法 trim 不扩张、不切词内、至少 1 秒；显式确认后当前稿句更新到实际保留文本，原稿留在旧修订。
7. FFmpeg/PCM 实际时长决定后续时间线，音频短缺/范围越界拒绝；不补静音或截尾硬凑理论时长。合成测试里的人工词时只能证明代码如何处理那组输入，不能证明实际 ASR 准确。

## 4. 容量、质量与规格差异

### 容量

- 上传块 **8 MiB**，不是整文件 cap。视频最多 500 MiB/单个、5 GiB/owner；图片另限 **50 MiB、20 MP**，GIF 首帧 3 秒，并做全动画结构安全检查。
- 新上传/新任务最多 **20 文件**；任务运行并发 **1**，pending **5（含运行）**；单服务 worker 约束不能靠开多个 uvicorn worker 绕过。预处理/媒体内部有自己的有界并发。
- 上传从创建起 **72 小时**；新三模式终态按保留参考时间固定 **72 小时**，保护忙碌任务与旧 `local_only`。开发模板的 `TASK_TTL_HOURS=0` 刻意保留用于 legacy 普通任务，不关闭新模式 TTL，不保证新稿/素材永久可找回。
- [开发模板](../.env.example)已为 **20 文件 / 1 执行 / 5 pending**，保留 legacy TTL 0；**不能为了三模式而改成 TTL 72，否则会给 legacy 普通任务开启自动清理**。[生产模板](../deploy/golden-mic.env.production.example)的 20/1/5/72 是另行审核的部署策略。本次不修改模板、实际环境或数据。

### 质量与人工确认

- 当前噪声阈值是 **15 dB**，不是交接文档表格的 18 dB；估计 SNR 未知时保持未知。
- quoteCaption 的 API 名是 `quote_caption`，取 **`asr/none`**；原型 `spoken` 仅作为旧偏好兼容输入。原声字幕来自实际发声文本，不拿用户稿件替代。
- `<0.6` 未找到、`0.6–<0.85` 待确认、`≥0.85` 文本较匹配；`<1 秒` 阻断、`>20 秒` 警告、`>30 秒` 阻断。以上数字来自共享规则，不代表真实人声质量已达标。
- `FACT_CHECK` 明确要求人工核对数字、人名、日期与称谓。发布确认绑定版本/报告证据；普通真实性错误不可勾选豁免，生成示意画面知情勾选不替代披露或事实核验。
- 当前 C 跳切警告是“自动空镜降级或硬切超过 3 处”；不是 B/C 所有非空镜切点一律警告。无可用空镜可降级 zoom，但不虚构空镜或宣称已消除视觉跳变。
- 人名条首次最多 **2.5 秒**，受实际出场范围约束，距上次显示结束至少 **60 秒**才重显；不用其他历史规格的 30 秒。

## 5. 最新导出实现与未完成限制

- **std=54、big=72（1080p 模板字号）**；三模式不是旧字幕的 1.5 倍版本。真实词时间决定字幕段落；只有段级证据时不制造逐词动态字幕。
- 制作偏好 `caption_style:none` 只隐藏旁白，原声由 `quote_caption` 控制。**导出 `sub:none` 同时关闭两类正文字幕，但保留标题（最多前 2.5 秒）、已启用人名条/包装、AI 示意披露**。std/big 不能擅自恢复用户关闭的原声字幕。
- **MP3 只含逐句人声与真实间隙，无 BGM**；从绑定的音频单元重新组装，不从混音成片抽取。不能把这项三模式实现宣称为所有 legacy 导出的行为。
- **三模式 simple MP4/MP3 长片已独立于 legacy Studio 120 秒限制**：服务端固定 `SimpleModeProject` 的预算为 `min(600, settings.max_total_source_duration_seconds)` 秒，不接收用户任意轨道/特效/嵌套工程。**128 MiB** 输出 cap、高码率预估拒绝与完整时长校验仍有效，不保证任意高码率长片可导出；MP3 无损人声中间文件也有预算。公共 Studio `Project/Clip`、任意编辑/嵌套及 legacy simple 仍是 120 秒。
- **GIF 仅前 6 秒、无声**，但入口先检查完整源成片在新模式时长预算内。独立预算与服务端契约是源码能力，不代替当前长片/性能/完整浏览器验收。
- 主 `/export` adapter 已修正内部 `path/raw_path` 到 Studio export，避免后置重授权 self-gate；外层权限、QC、修订检查保留，旧 URL 不变。海报/缩略图 cache 固定读取 committed 源，JPG/日志移到任务级 `public_previews`，键加入源相对位置；不再写不可变 revision，见 [main.py](../backend/main.py)、[public_media.py](../backend/public_media.py)。
- **样片水印缺口仍在**：未经发布确认的 preview 只能视为样片，UI 提示不是媒体防护。inline raw preview 不强制烧录水印，授权用户能取得其字节；正式 download/export 门禁不能被描述为 DRM，也不是完整的水印样片下载机制。

## 6. 失败恢复与兼容边界

### 失败任务返回编辑

`submissionRecovery` 在已接受创建回执后保存按 task ID 绑定的原始提交快照，**同浏览器/同源保留 30 天，只有明确点击“返回编辑”才读取**。持久化成功后刷新/同浏览器新标签可用，不是跨设备同步，不新增 server creation context API。已有另一份草稿时先确认替换；成功提交后普通创作草稿清空，不等于删掉这份独立快照，不能从报告或另一份草稿补造。

恢复的只是原稿、模式、选项与素材清单/凭据，随后对上传做**只读复核**；不自动重传、complete、重复 ASR 或制作，不修改原任务。**30 天快照不延长 72 小时媒体/任务保留**；媒体缺失/过期或 capability 失效时不能恢复可用媒体，须明确重选。快照过期/损坏、清 storage 后无持久快照、禁止存储或旧任务从未保存时均无可靠恢复；未传完文件/整篇录音始终须重选。存储失败提示后最多只有同生命周期、同 TTL 的内存回退，不能承诺刷新或清 storage 后可恢复，也不因此重复 POST。

这与未提交草稿的恢复是两回事：草稿可保存模式、确认句子、说话人、偏好、上传 ID/capability，刷新后只读核对有效上传；文件本体与录音并不因此持久化到浏览器。

**L 的恢复 fixture 显式预置快照，只覆盖主动读取/只读恢复路径，不是产品 auto-save hook E2E。** 真实保存 hook 由单元测试另行覆盖；不能将二者合称自动保存端到端闭环。

### legacy、只读演示与旧 API

- `gm-core-v1` 中 legacy tasks/history/checked/pend 等历史内容保留；新草稿按白名单读取/保存，未知或损坏内容不假装成功迁移。旧文件名摘要不是已上传文件，不能凭名称复原媒体或启动任务。
- 旧 multipart、工作台修订与后台 Studio API 保留。当前主界面入口使用 **只读 `StudioDemo`**：读取报告展示句子，时间轨为句长示意，不含间隙，不生成假波形/播放进度，不保存工程/渲染/导出。旧 [STUDIO_API.md](STUDIO_API.md) 描述的是保留 API 能力，不代表目前主界面仍开放整个旧专业编辑台。
- `replace-shot` 的 `sentence_id + instruction` 两字段旧契约按设计保留，不把缺少 `expected_revision` 声称为缺陷；显式修订控制使用工作台批量接口。
- legacy 清理复核确认**无 active classroom**，无教师、账号审核或排队放行；旧任务兼容、后台 Studio、replace-shot 两字段契约仍保留。旧 `local_only` 状态/数据库和用户媒体不迁移、不清空、不自动放行；历史费用风险记录仍不能删除来解锁编辑。
- 历史报告与链接继续保留：[2026-09-27 核心工作区验收](CORE_WORKSPACE_VALIDATION_20260927.md)、[C26](../canary_test/CANARY_C26_20260926.md)、[原型能力矩阵](PROTOTYPE_CAPABILITY_MATRIX_20260925.md)、[历史证据索引](../canary_test/README.md)。这些不证明本轮三模式通过，也不恢复退役产品入口。

## 7. 最终本地验收与未闭合边界

| 项目 | 当前状态 / 后续需要 |
| --- | --- |
| 完整浏览器流程 | L `modes-verified-20260928-l` 8/8、262.979523 秒、0 retry、globalErrors=0；J/K 原失败记录保留，不拼接绿项。C 八句十阶段/trim R1/正式导出通过，短 A/B 通过；完整 B 样例只预检，不渲染全部 16 句。 |
| 布局、安全与目视 | 23 layout=21 regular+2 missing-quote，320/900px、20px 字体，零 overflow；8 safety reports 的 pageErrors/blockedRequests/unexpectedDialogs/cleanupErrors 均 0。run 内 41 JSON、无 raw artifacts。父代理实际结果纸感 UI 截图已目视检查，仅当前 viewport，不是全页像素一致或完整 WCAG。 |
| 测试准确数 | 后端 1118=1116 pass+2 skip，0 failure/error，887.556619 秒，106 source 不变；前端主集成反馈 459 pass，产品/workspace E2E/modes E2E 三项 typecheck 通过。精确来源与反馈证据限制见[最终验收记录](THREE_MODE_VALIDATION_20260928.md)。 |
| 匹配 SLA | **200 ms SLA 未达**。此前“135 项纯测试、约 398 ms”的记录仅为历史信息，不是这次新基准；不据此宣称当前性能或并发达标。预转写 ≤素材时长×0.3 同样没有当前验收依据。 |
| 真实人声质量 | 真实采访 ASR/词边界/说话人、噪声估计、断句剪切自然度、旁白/原声响度与主观试听未形成当前完整验收。 |
| 导出完整性 | L C 初版 60.066667 秒，trim R1 后正式输出 59.566667 秒、31,579,719 字节、1080p30 H.264/AAC 48 kHz；确认前 409、确认 10 项后可下载，R0 62/62 SHA 不变，无 TTS/重复 ASR。不能推导任意时长/码率或真人新闻质量；128 MiB/高码率拒绝保留。 |
| L 停机 | `serverState=stopped`，`shutdownComplete/lifespanShutdownComplete/fakeProviderStopped/inputHashesUnchanged=true`，8786 无监听。`errors/sourceHashesUnchanged` 字段实际缺失，投影 null 不是 clean/源码不变证明；源码以独立散列复核为据。 |
| 部署与稳定性 | 日常 8000 未更新；未调用真实付费 Provider、未触碰用户 data。L 停机仍有 **3 次 WinError 10054 callback**；正常 lifespan 退出不等于日志无错误，不证明历史 Python 原生 crash 根治，也不补写 C26 退出缺口。生产/Linux/长时负载/备份恢复未验。 |
| 其他产品边界 | 同浏览器失败快照不等于服务端/跨设备恢复；raw preview 强制水印仍缺。完整 WCAG、麦克风硬件、真人新闻事实及无断电数据损失承诺均未验收。 |

可检查的当前测试源码包括 [test_production_modes.py](../tests/test_production_modes.py)、[test_mode_api.py](../tests/test_mode_api.py)、[test_mode_pipeline.py](../tests/test_mode_pipeline.py)、[test_mode_workbench.py](../tests/test_mode_workbench.py)、[test_mode_exports.py](../tests/test_mode_exports.py) 和 [acceptance.modes.spec.ts](../frontend/e2e/modes/acceptance.modes.spec.ts)。源码说明覆盖意图，成绩以最终证据为准；合成音调/人工词时边界在测试中已有注明。

**本轮收尾完成：本地合成通过范围已绑定最终 source/build/host，coding todos 已完成，J/K 失败证据保留。** raw preview 水印、性能 SLA、真实 ASR/音频、生产 Linux 与稳定性仍是明确缺口；不作整个生产系统无条件完成声明，不以清理用户 data、复用旧绿色记录或重复付费任务替代验收。