> **历史计划 · 2026-09-27 退役标识：下述独立云作业/课堂集成不再是当前产品路线或可执行授权。** 当日验收与一次性费用许可只作历史记录，不因保留本文延续为新调用许可；独立 `CLOUD_*` 模板键已移除。当前范围见 [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)。原始回执/账本/日志保留，未知费用不清零；核心 Kimi/火山生成能力不在此次退役范围。

# 原型剩余能力：云接入第一阶段本地验收与后续计划（2026-09-23）

**第一阶段六种受限云作业代码与最终本地回归已完成；真实验收仍仅一次 Kimi 参考解说 smoke，腾讯仍未就绪，不是原型 58 项高级缺口完成。** 最终证据见 [CLOUD_ACCEPTANCE_20260923.md](CLOUD_ACCEPTANCE_20260923.md)及 [../canary_test/artifacts/prototype-refactor-20260923/cloud-validation.json](../canary_test/artifacts/prototype-refactor-20260923/cloud-validation.json)。具体契约以 [CLOUD_API.md](CLOUD_API.md)及其链接的后端、配置和 `CloudTools` 实现为准。

## 1. 已批准范围与最终本地状态

- 已批准组合：**现有 Kimi/火山服务＋腾讯 MPS/COS**，保留本地 FFmpeg 与课堂审核。境外能力必须逐供应商另行确认；当前未接 Azure/阿里，不做人物音色克隆、对口型或自动社交发布。
- 本次付费许可仅为**一次性 ≤50 元（5000 分）**，仅自有/合成且无可识别人物的内容；不是月度或持续生产支出许可，也不授权购买套餐、创建云资源或上传学生人物素材。
- **实际本地与生产环境文件未修改**；新增云作业总开关、Kimi/腾讯作业开关默认关闭，三项预算默认 0。配置示例中的新增字段不代表真实环境已启用。

| 验证/就绪项目 | 实际结论与证据 |
| --- | --- |
| 最终后端全量 | **795 项：793 通过、2 跳过，389.889 秒**；[../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-final.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-final.log) |
| 最终页面回归 | **32/32 通过，242.14308 秒**；单 Edge worker、0 retries；[../canary_test/artifacts/cloud-pages-verified-20260923/summary.json](../canary_test/artifacts/cloud-pages-verified-20260923/summary.json) |
| 最终完整深层回归 | **42/42 通过，239.026573 秒**；新进程/新种子，单 Edge worker、0 retries；**31 项真实本地后端用例（含默认关闭云面板）＋11 项启用态合成云契约**；[../canary_test/artifacts/round2/cloud-verified-20260923/summary.json](../canary_test/artifacts/round2/cloud-verified-20260923/summary.json) |
| 云/保护门禁/部署定向 | **287/287 通过，129.719 秒**；包含在全量内，不重复相加；[../canary_test/artifacts/prototype-refactor-20260923/cloud-enabled-contract.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-enabled-contract.log) |
| 类型、构建与一致性 | 应用及两套 E2E TypeScript、Vite 构建、`compileall`、`pip check`、环境验证、离线 `uv lock --check` 通过；独立冻结锁/SBOM 导出哈希一致；[../frontend/dist/ASSET_MANIFEST.sha256](../frontend/dist/ASSET_MANIFEST.sha256)的 **4 个资源哈希全部匹配** |
| 依赖审计 | Python **76 包、0 已知漏洞、0 跳过**；npm 全部依赖 **0 已知漏洞**；[../canary_test/artifacts/prototype-refactor-20260923/cloud-pip-audit.json](../canary_test/artifacts/prototype-refactor-20260923/cloud-pip-audit.json)、[../canary_test/artifacts/prototype-refactor-20260923/cloud-npm-audit.json](../canary_test/artifacts/prototype-refactor-20260923/cloud-npm-audit.json) |
| 唯一一次 Kimi smoke | **成功，9.266 秒**；[真实报告](../canary_test/artifacts/cloud-live-20260923/report.json#L1)。实际 input 377 / output 175 / total 552 tokens；**100 分预约仍待对账，实际账单未知** |
| smoke 素材/证明范围 | 仅自编的合成纸片形状参考解说文字，不含可识别人物；只验证 `reference_narration` 适配器，不证明字幕翻译、云前端端到端或腾讯能力。一次性驱动已使用，**不得重跑或追加 Kimi 调用** |
| 腾讯真实验收 | **阻塞**：凭据、既有私有桶、地域、模板 ID 及字幕审核语种、审核费率和预算尚未配置；不宣称账号已配置、MPS 已开通或实际地域已验证 |
| 服务与原始数据 | 最终所有自有临时服务 **8766/8769/8770 已停止，8765–8770 监听数为 0**；关闭时复核 **259 个允许读取的原始文件哈希全部一致**；实际环境未修改，TEMP/失败证据/唯一付费回执保留 |

最终本地验证已收口，不再等待完整回归，也不以定向结果或跨轮拼接代替。后端两项跳过仍为 Windows 符号链接权限和需显式开启的历史真实 TTS 离线回放。日常 8000 服务未启动/修改，历史完整 live 原片的 4 项 QC 阻断不变；上表是保留的关闭证据，不是仍在运行的测试服务。

教师统计区已给带 `aria-label` 的普通 `div` 增加 `role=group`，R2-23 检查真实可访问的“班级统计”组。最新教师 320px/20px axe 为 **0 violations，仅剩 `color-contrast` 的 11 个节点 incomplete**；云作用域为 **0 violations、0 incomplete**。其它页面的待审项仍保留，不能等同于全页、读屏、像素一致性或硬件验收。

11 项启用态用例的**全部云响应均为浏览器拦截的合成契约，云 POST 未送达真实后端**。唯一额外例外是 R2-CE-02 撤审场景的一次浏览器当前作品 GET 覆盖为 `can_export=false`，没有真实课堂撤审/写入或后端撤权证明。用例前后以绕过浏览器拦截的真实 GET 严格确认后端云关闭、六项操作均为 `cloud_disabled`、作业列表为空；真实隔离 scratch 的成片字节、报告、工程及作品/审批状态不变，没有恢复写入。R2-30 则仍以真实 API 验证默认关闭及报价 403 拒绝，其余真实用例不变。无需运行真实云服务，不能把合成 UI 契约与31项真实后端证明或真实云鉴权/计费/效果混为一谈。

### 中间与历史记录仍保留

- **首轮 41/42，约 239.971 秒**：R2-CE-06 丢失回执测试漏处理初始未保存工程的丢弃确认，需先确认丢弃、再确认离开；仅修正导航辅助逻辑，未改生产确认流程。当轮31项真实后端用例全过。失败与随后 **11/11、约51.243秒**的定向复测分别保留：[../canary_test/artifacts/round2/cloud-enabled-20260923/summary.json](../canary_test/artifacts/round2/cloud-enabled-20260923/summary.json)、[../canary_test/artifacts/round2/cloud-enabled-receipt-retest/summary.json](../canary_test/artifacts/round2/cloud-enabled-receipt-retest/summary.json)。
- **中间第二轮 41/42**：R2-13 在 `browserContext.newPage` 时 Edge 意外关闭，**尚未到业务断言**；未因此修改生产代码或削弱断言。[../canary_test/artifacts/round2/cloud-final-20260923/summary.json](../canary_test/artifacts/round2/cloud-final-20260923/summary.json)仍为失败记录；最终42/42来自新进程/新种子的完整复验，不是跨轮相加。
- **旧785项（783通过、2跳过），532.420秒**及**旧249项定向通过，109.750秒**早于最新修复，不是最终结果：[../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-full.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-full.log)、[../canary_test/artifacts/prototype-refactor-20260923/cloud-contract-final.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-contract-final.log)。[原517/32/30记录](PROTOTYPE_REFACTOR_20260923.md#L45)也仍仅为云接入前基线，其中后端515通过/2跳过。

## 2. 第一阶段：已实现与仍待完成

| 层次 | 已实现 | 尚未证明/尚未实现 |
| --- | --- | --- |
| 云作业基础 | 主应用挂载 API；私有课堂角色/CSRF；新费用/下载要求当前 `can_export`，教师取消独立授权；源哈希与流水线 revision 绑定；含地域的报价、审核语言列表、原子预算/准入、持久幂等/状态、受保护下载 | 多进程/分布式调度、自动对账、运维恢复 UI/公开 API、自动重试清理队列 |
| Kimi 文本 | `reference_narration`、`subtitle_translation`；一次有界请求、不付费重试；翻译保留 cue ID/原时间，输出 `unreviewed` | 仅参考解说有上述一次真实证据；翻译实测、参考稿事实质量、云前端真实端到端仍待验收；没有新增 ASR/TTS 批量接口 |
| 腾讯媒体 | `video_super_resolution`、`video_interpolation`、`audio_denoise`、`smart_subtitles` 的私有 COS/MPS 适配、轮询、受限下载与校验 | 账号/IAM/地域/模板/费率核验及每种真实效果/清理验收均未完成；未实现 OCR、擦除、抠像或稳定器 |
| 云端面板 | 课堂 Studio 独立面板；中文默认目标语言、按审核列表限制操作、地域/超预约账单警示、两项权利隐私声明、独立教师取消；先保存同标签页最小回执再提交，未知提交不重发 | 不自动读取 Studio 草稿、不套用结果、不批准/发布；无跨标签页/清除存储后的回执保护、自动付费恢复或回执对账端点；配置可用与合成用例通过不等于真实验收 |

现有火山 ASR/TTS、生成媒体与本次六种云作业不是同一个入口；本云预算账本不自动覆盖旧流水线或同一凭据在其它系统的消费。云面板与 [STUDIO_API.md](STUDIO_API.md)的本地能力目录分别披露，不以新面板把旧工具改标为已支持。

## 3. 当前约束（不是供应商能力宣传）

- **授权/绑定**：只有具有私有作品权限的本班教师可报价、提交及取消；学生作者可私有查询，满足当前导出门禁才可下载。提交/下载绑定当前已提交流水线版本，不是 Studio 工程版本；文字作业也绑定报告和成片哈希，翻译另绑定时序。旧令牌/他人已发布作品不能绕过。
- **独立取消**：能力返回布尔 `can_cancel`；私有访问校验通过的本班教师为 `true`、学生作者为 `false`，不依赖总/供应商开关、`can_spend`、当前 `can_export` 或 drain。UI 不再用 `canSpend` 阻断取消；原生确认后先刷新 capabilities/jobs，再核对教师权限和该作业可取消状态，变化或读失败即不发取消 POST。权限独立不等于远端能停止或退款。
- **门禁**：总/供应商开关、非零具名一次性＋UTC 每日＋单任务累计额度、隐私/地域批准、凭据、版本化审核费率缺一则拒绝；腾讯还需当前操作模板。**没有月度/每班预算实现**。同预算 ID 不得调高初始额度，删除作品不抹账，换 ID 不是扩额许可。
- **报价地域/指纹**：报价必须返回 `region` 及 `price_kind=reservation_ceiling`；Kimi 为 `cn`，腾讯为批准列表中的实际 `cloud_tencent_region`，前端拒绝缺失/非法字段，不推断地域。非秘密配置指纹为 **schema 3**，包含实际 `region` 和操作的 `target_languages`；地域/审核语言变化使旧绑定失效。该地域仍是审核配置，不是独立数据驻留证明。
- **预约非账单**：服务器报价 10 分钟有效，绑定源、教师/班级、操作和非秘密配置；金额来自审核的 `unit/max_fen/transfer_fen`，不是前端定价。报价和确认框均明示“**实际账单可能超过预留额**”。Kimi 还检查 UTF-8/JSON allowance、最大输出及一小时缓存写入的保守下限；均不是实测 tokens 或保证账单封顶。尝试后的成功/失败/取消仍保留额度；包括 `settled` 在内，除 `released` 外均按预约额计占用。
- **准入/幂等**：全账本活动上限 **1–8，默认 1**，每任务最多一个；跨教师/预算 ID 计数，与预算预约同事务。未知受理及有远端风险的中断占槽。服务端持久键为教师＋幂等键＋预算 ID，同键同报价重放在当前权限/源/配置仍通过时不再付费；不是按素材去重，新键仍可能产生新费用。
- **同标签页回执**：[最小回执实现](../frontend/src/lib/cloudReceipt.ts#L1)在 `sessionStorage` 使用 `golden-mic.cloud-attempt.v1:${actorScope}:${taskId}`；`actorScope=${role}:${id}` 由 App/Studio 传入真实课堂会话的角色及不透明 `identity.id`，不是显示名或登录令牌。序列化长度 **≤4096**，仅存 `schema`、不透明 `quoteId/key`、`revision/state` 和仅已接受时的 `jobId`，不存秘密、源文稿/媒体或个人资料。**同步写入 `sending` 并读回一致后，才发该次唯一的 POST `/jobs`**；格式错误、作用域错误或存储读写/读回失败均停止新报价及付费提交。`sending/unknown` 在同标签页重挂载、刷新、退出后同身份重登仍保留，不因报价过期重置；只有明确 `accepted/rejected` 才能在下一份新报价开始提交时被替换。
- **回执保护上限**：仅同标签页，不保证跨标签页、完整浏览器重启或清除存储后的保护；绝不能手动清回执、关页/换页/身份或换键重试付费。未知回执只查询，不根据相似/已完成作业自动匹配，不自动恢复付费、结算或释放，也没有回执对账端点。详见 [CLOUD_API.md](CLOUD_API.md)。
- **腾讯输入/输出**：仅整段已提交 MP4，输入 **≤120 秒、SDR、长边≤1920 且面积≤1920×1080、0＜FPS≤60**；输出最大 **长边3840/面积3840×2160（4K 范围）**。超分宽高各严格 **2×**，其它视频操作尺寸不变；不是放开通用 4K/8K/HDR 导出。
- **帧率/时长/音轨**：补帧目标按 `Settings` 为 **24–60 整数，默认60**；目标/输出均不得低于源 FPS，输出与目标差≤0.02fps，超分/降噪与源差≤0.02fps。时长差≤较大者（0.25秒或2%），输出≤122.4秒；源音轨不能丢，降噪/智能字幕必须有音轨。
- **大小/时限**：媒体输出默认128 MiB（可配1–512 MiB），作业默认600秒（30–3600）；Kimi 源≤32,768字符、输出≤16,384字符/64 KiB JSON、最多2048输出tokens/60秒单次请求；智能字幕≤1 MiB。
- **语言/模板**：UI 对参考解说和两个字幕操作默认 `zh-CN`，并按各操作返回的 `target_languages` 限制所选语言能否报价/提交；两个可用 Kimi 操作支持 `zh-CN/en-US/ja-JP`，智能字幕仅支持审核的单一模板输出语言，其它媒体操作不适用语言（空列表）。`Settings.cloud_mps_subtitles_language: Literal["", "zh-CN", "en-US", "ja-JP"] = ""` 已镜像到[本地示例](../.env.example#L142-L144)和[生产示例](../deploy/golden-mic.env.production.example#L186-L188)；空值关闭智能字幕。报价语种与本地审核值不符时返回 **422**，在源读取/探测、报价账本访问和上传前拒绝；API 未显式传目标语言时仍默认 `en-US`，不能与 UI 默认混淆。
- **远端模板风险**：本地审核声明不是远端事实。智能字幕适配器仍在 **COS 上传后、`ProcessMedia` 前**在线检查模板；操作者误填或模板漂移可能此时才发现。不能声称错误配置/漂移已不可能，也不能把此类拒绝当作未上传或零费用。
- **质量/真实性**：媒体只验证容器/流属性，明确 `quality_measured=false`；更大尺寸/更高 FPS 不等于实测超分/光流效果，音轨存在不等于降噪或同步合格，缺 HDR 标签也不是色彩测量。不声称硬件质量验收。所有结果 `unreviewed`，不覆盖、自动应用或发布。

## 4. 腾讯操作者安全核对清单（尚未执行，不含秘密）

仅供有权限的操作者核对**既有账号、既有私有 COS 桶和既有已审核模板**。本清单不是账号已配置的声明，也不是运行指令；**不购买、不新建桶/实例/模板或其它资源**。现有条件不足即保持阻塞，另行申请授权。凭据只由操作者经安全秘密管理注入，禁止贴入聊天、文档、仓库、日志或测试证据。

- [ ] **账号/服务**：确认既有账号归属、负责人、MPS/COS 使用授权和计费主体；不要把“部署在腾讯云”当成处理服务已开通。记录不含秘密的审批编号/负责人/日期，不导出账号凭据。
- [ ] **既有私有桶**：核对 `cloud_tencent_bucket` 与受限 `cloud_tencent_prefix`，无公开读写、公共 CDN 或不受控复制。适配器在该前缀下按随机作业 ID 放输入/输出，上传 ACL 为 private；必须独立审核桶策略，字段校验不证明整桶私密。
- [ ] **真实地域**：核对 `cloud_tencent_region` 与 `cloud_approved_regions`，确认桶、MPS 所选能力/模板实际处理地域、跨境条件、内部副本保留及子处理方。**不能由桶名、桶地域、SDK region 或同属腾讯账号推断完整数据驻留保证**；未确认则保持关闭。
- [ ] **固定模板 ID 与字幕语种**：分别核对 `cloud_mps_super_resolution_template`、`cloud_mps_interpolation_template`、`cloud_mps_audio_denoise_template`、`cloud_mps_subtitles_template`，仅开放已有正整数 ID 的获准操作。前三类须实际配置 MP4、对应增强和上述尺寸/FPS/音轨限制；字幕模板必须是单语 ASR 或 ASR 翻译、SRT/VTT、无嵌入字幕，实际输出语种与审核的 `cloud_mps_subtitles_language` 及请求一致，未审核保持空值。不能用 OCR/纯字幕翻译模板冒充；不得原地改已审核模板，ID/schema 3 指纹也不能锁定其云端内容。在线复核在上传后，不能据此承诺误填/漂移时未传素材或零费用。
- [ ] **审核费率/额度**：按账号、地域、日期、模板的实际计费组合核对增强＋基础转码、字幕、COS 存储/请求/传输及失败/取消计费，登记 `cloud_rate_version` 与逐操作 `cloud_rates_json`。确认具名 `cloud_budget_id` 及总/UTC 日/任务额度，默认保持0，任何未来配置不得把本次≤5000分许可改为自动续费。未审核不填猜测单价；公开价只供参考，不作为账单保证。
- [ ] **最小 IAM**：使用既有受限身份/获准临时会话，不使用主账号全权密钥。按腾讯 CAM 支持的资源/条件范围限制 MPS `ProcessMedia`、`DescribeTaskDetail`、`ManageTask`，字幕另需 `DescribeSmartSubtitleTemplates`；COS 仅指定桶/前缀的写、读、列举、删除。另审核 MPS 访问 COS 的服务授权；确需宽资源范围的动作单独评审，不授整账号管理员或整桶公开权限。
- [ ] **秘密与网络**：确认服务端 HTTPS、证书验证、无重定向/环境代理绕路及 SDK 日志不泄漏签名/响应；秘密只检查“已配置/未配置”，不显示值。`cloud_tencent_secret_id`、`cloud_tencent_secret_key` 和可选会话 token 不进入报价、客户端或证据。
- [ ] **生命周期/清理**：只对获准前缀审核保留期；覆盖当前与非当前版本、删除标记、遗留分片、复制/备份及供应商内部副本，不能破坏既有桶其它用途。应用 `cleanup()` **只删当前对象，不删版本/分片/副本**；`cleanup_pending` 需人工跟踪，未实现自动清理队列。保留期/真实物理删除未核实前不得承诺已清除。
- [ ] **告警/停止责任**：在既有账号范围核对费用阈值、异常调用/并发、存储/外网流量、失败/超时/未知提交及清理失败告警与接收人；告警不是硬停费。默认活动准入1，增至最多8须另评容量；关闭新提交不会自动中止远端处理，预先指定人工核对任务和账单的负责人。
- [ ] **放行仍需明确指令**：检查完成不自动运行 smoke；本次 Kimi 一次性驱动已使用且不可重跑。腾讯仍需单独确认具体操作、合规素材、审核模板/地域、预约金额及验收计划后才考虑真实调用；不得触碰现有回执/证据来重置“一次性”。

以上各项仍待操作者核对，不是腾讯控制台、IAM、生命周期、预算或资源配置已验收的记录。一般生产部署另见[外部运维清单](../deploy/TENCENT_CLOUD_P0_EXTERNAL_CHECKLIST.md#L1)，该历史清单同样不能替代当前放行证据。

## 5. 不确定性、人工对账与恢复边界

1. `submission_unknown` 不自动重提；腾讯 `SessionId` 不是本地幂等替代品。同标签页最小回执的 `sending/unknown` 不因重挂载、刷新、退出后同身份重登或报价到期消失，不能清存储来重试。服务端重启只对已持久化远端 ID 且源/配置匹配的腾讯作业恢复轮询/下载，**不重新创建付费任务**；Kimi 不重发请求。缺 ID、配置变化或监控中断需人工核对。
2. 腾讯 Abort 只尝试 `WAITING`，接受不证明已取消；`PROCESSING` 可能继续计费。取消 Kimi 本地请求同样不能证明远端停止。已尝试的额度保留，唯一成功 smoke 的100分也不能按 usage自行当作已结账/释放。
3. 活动/有远端风险的记录保护原作品，阻止编辑、复制和删除；不通过删作品、清账本、换预算 ID 或新幂等键“恢复”。账本 `settled` 只是状态，**没有对账 UI、公开修复 API、自动核账/已尝试费用释放或未知任务自动匹配**。负责人须在受控流程中核对任务、费用和残留数据，修复产品仍待建设。
4. 安全终态可尝试前缀当前对象清理；未知受理/中断下载保留数据便于恢复。没有 `cleanup_pending` 也不等于所有历史版本/供应商副本已删除，当前清理不是版本删除或全链路擦除承诺。
5. 只传已提交报告文字或整段成片，不传课堂数据库/任务令牌/工作目录；隐私声明不自动识别人脸或人物。学生真人素材、克隆/脸部改写、境外处理须另行目的、地域、保留期和授权审查，不能扩展本次无可识别人物素材许可。

## 6. 本地收口、后续真实验收与原型缺口

| 阶段 | 当前结论与下一步（后续工作不自动执行） |
| --- | --- |
| 本地收口（已完成） | 最终 **795 项后端（793通过/2跳过）、32/32页面、完整42/42深层**以 [CLOUD_ACCEPTANCE_20260923.md](CLOUD_ACCEPTANCE_20260923.md)为准；31真实后端＋11合成云契约分开解释。旧785/249、CE-06辅助逻辑失败、R2-13浏览器提前关闭和定向复测均保留，不覆盖失败、不跨轮拼接，也不外推为全产品/真实腾讯通过 |
| 云产品实测 | 在新授权下，分别验收字幕文字翻译、课堂云面板全流程及腾讯四种操作；使用既有批准桶的隔离前缀，核对真实输出、费用、撤权、取消/中断/恢复与清理；本次不追加调用 |
| 运维产品 | 补人工对账/恢复的受控接口、审计和 UI，完善清理重试及告警；必须避免重复受理/重复扣费，不能先靠改库解除保护 |
| 媒体质量 | 对实际输出检查细节、运动伪影、语音可懂度、音画同步、字幕语义/时序与色彩；属性探测不能替代人工及客观测量，更不能伪造硬件质量结论 |
| 仍未接入 | 擦除、抠像、稳定器、火山 ASR/TTS 批量作业等需独立契约/授权/验证；不能把接口名或模型建议标为已渲染功能 |
| 本地/专项编辑 | 吸附/波纹/代理/嵌套、曲线/LUT/转场、字幕动画/贴纸、蒙版跟踪、多机位、HDR/高分辨率、协作与平台发布仍需独立实现与验收；人物克隆/对口型继续延期 |

历史[原型目录边界](PROTOTYPE_REFACTOR_20260923.md#L83)为113项：11渲染、38有限支持、6工程元数据、58未接入。六种独立云作业并不等价于补齐58项，也没有开放无限轨道、8K/120fps、自动审核或自动发布。只有逐项有真实产物和相应验收，才可更新具体能力结论。

## 7. 官方参考：接口、费用与隐私

以下为操作者核对接口、计费及隐私条件的官方入口，不构成实时价格、账号开通、实际处理地域/驻留、保留期或已完成合规审查的证明；本文实现边界与测试证据也不等于供应商能力保证。

- [腾讯 MPS ProcessMedia](https://cloud.tencent.com/document/product/862/37578)：媒体处理提交接口参考。
- [腾讯 MPS ManageTask](https://cloud.tencent.com/document/product/862/40947)：任务管理/取消接口参考；取消仍须遵守上述不确定性与费用边界。
- [腾讯 MPS 智能字幕](https://cloud.tencent.com/document/product/862/117004)：字幕能力与模板配置参考，不代表当前账号模板已核验。
- [腾讯 MPS 计费说明](https://cloud.tencent.com/document/product/862/36180)：人工审核费率的官方入口；还须核对实际模板组合及 COS/传输等独立费用。
- [Kimi 模型计费](https://platform.kimi.com/docs/pricing/chat)：文字调用费率参考；usage、本地预约和实际账单须分别记录。
- [腾讯云隐私政策](https://cloud.tencent.com/document/product/301/11470)：隐私条款入口；具体目的、地域、保留、删除与子处理方仍须按实际服务和授权单独审核。