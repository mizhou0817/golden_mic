> **已退役 · 2026-09-27：本文仅保留独立云作业/课堂授权的历史契约与验收记录。** 独立云作业路由、CloudTools UI 及其 32 个 `CLOUD_*` 配置键已退出当前产品/示例模板；下文“已挂载”“当前”、旧源码链接与测试数只描述当时版本，不是可启用的现行入口，也不授权复跑一次性调用。
>
> 当前范围见 [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)。核心生成的 Kimi/火山 Embedding、ASR、TTS 和受控生成式补拍并未因此移除。原始付费回执、账本、日志与媒体保留不动；未知远端受理/费用仍需独立核对，不得删账或清预约来解锁。本次未运行服务、套件或付费请求。

# 历史云端作业 API：第一阶段契约与本地验收（2026-09-23）

以[路由与校验](../backend/cloud_jobs.py#L1)、[预算账本](../backend/cloud_store.py#L1)、[Kimi 适配器](../backend/providers/cloud_editorial.py#L1)、[腾讯适配器](../backend/providers/cloud_media.py#L1)、[默认配置](../backend/config.py#L129-L162)、[CloudTools 面板](../frontend/src/components/CloudTools.tsx#L1)及[同标签页提交回执](../frontend/src/lib/cloudReceipt.ts#L1)为准。[主应用已挂载路由](../backend/main.py#L904)，仅支持单应用 worker；第一阶段六种受限云作业不等于真实账号、媒体质量或原型剩余 58 项能力验收。运维、官方参考与后续范围见 [CLOUD_CAPABILITY_PLAN_20260923.md](CLOUD_CAPABILITY_PLAN_20260923.md)。

## 1. 最终本地证据与授权范围

**第一阶段代码与本地回归已完成收口，腾讯真实验收仍阻塞。** 当前结论以 [CLOUD_ACCEPTANCE_20260923.md](CLOUD_ACCEPTANCE_20260923.md)及机器可读汇总 [../canary_test/artifacts/prototype-refactor-20260923/cloud-validation.json](../canary_test/artifacts/prototype-refactor-20260923/cloud-validation.json)为准；不是等待全量结果，也不是把不同轮次的通过项拼接为最终结果。

| 验证层次 | 当前证据与适用范围 |
| --- | --- |
| 最终后端全量 | **795 项：793 通过、2 跳过，389.889 秒**；[../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-final.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-final.log) |
| 最终页面回归 | **32/32 通过，242.14308 秒**；单 Edge worker、0 retries；[../canary_test/artifacts/cloud-pages-verified-20260923/summary.json](../canary_test/artifacts/cloud-pages-verified-20260923/summary.json) |
| 最终完整深层回归 | **42/42 通过，239.026573 秒**；新进程/新种子的完整一轮，单 Edge worker、0 retries；**31 项真实本地后端用例（含默认关闭云面板）＋11 项启用态合成云契约用例**；[../canary_test/artifacts/round2/cloud-verified-20260923/summary.json](../canary_test/artifacts/round2/cloud-verified-20260923/summary.json) |
| 云/保护门禁/部署定向 | **287/287 通过，129.719 秒**；包含在最终全量内，不重复相加；[../canary_test/artifacts/prototype-refactor-20260923/cloud-enabled-contract.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-enabled-contract.log) |
| 类型、构建与一致性 | 应用及两套 E2E TypeScript、Vite 构建、`compileall`、`pip check`、环境验证、离线 `uv lock --check` 通过；独立冻结锁/SBOM 导出哈希一致；[../frontend/dist/ASSET_MANIFEST.sha256](../frontend/dist/ASSET_MANIFEST.sha256)的 **4 个资源哈希全部匹配** |
| 依赖审计 | Python **76 包、0 已知漏洞、0 跳过**；npm 全部依赖 **0 已知漏洞**；[../canary_test/artifacts/prototype-refactor-20260923/cloud-pip-audit.json](../canary_test/artifacts/prototype-refactor-20260923/cloud-pip-audit.json)、[../canary_test/artifacts/prototype-refactor-20260923/cloud-npm-audit.json](../canary_test/artifacts/prototype-refactor-20260923/cloud-npm-audit.json) |

后端两项跳过为 Windows 符号链接权限及需显式开启的历史真实 TTS 离线回放，不是缺 FFmpeg。教师统计区的普通 `div` 已补 `role=group`，使 `aria-label` 可命名，R2-23 验证实际“班级统计”组；最新教师 320px/20px axe 为 **0 violations，仅剩 `color-contrast` 的 11 个节点 incomplete**，云作用域为 **0 violations、0 incomplete**。其它页面待人工测量项仍保留，不外推为全页、读屏或硬件认证。

11 项启用态浏览器用例的**全部云响应均为明确标记的合成契约，云 POST 未送达真实后端**。唯一额外例外是 R2-CE-02 的撤审场景：一次浏览器侧当前作品 GET 返回合成的 `can_export=false`，没有真实课堂撤审/写入，也不证明后端撤权鉴权。用例前后均绕过浏览器拦截，用真实后端 GET 严格确认云仍关闭、六项操作均为 `cloud_disabled`、任务列表为空；真实隔离 scratch 作品的成片字节、报告、工程及作品/审批状态不变，没有恢复写入。其它真实后端用例保持原有断言；R2-30 的直接报价请求只验证真实 403 拒绝。无需启用或运行真实云服务，这些用例不证明云鉴权、计费或处理效果。

### 保留的中间记录（不覆盖失败）

- 首轮 **41/42，约 239.971 秒**：R2-CE-06 丢失回执用例只预期一次外壳确认，未改动的初始 Studio 工程仍“未保存”，须先确认丢弃、再确认离开。仅修正测试导航辅助逻辑，未改生产确认逻辑；当轮 31 项真实后端用例全部通过。原失败保留于 [../canary_test/artifacts/round2/cloud-enabled-20260923/summary.json](../canary_test/artifacts/round2/cloud-enabled-20260923/summary.json)，随后 **11/11、约 51.243 秒**的筛选复测保留于 [../canary_test/artifacts/round2/cloud-enabled-receipt-retest/summary.json](../canary_test/artifacts/round2/cloud-enabled-receipt-retest/summary.json)，不冒充完整回归。
- 中间第二轮 **41/42**：R2-13 在 `browserContext.newPage` 时 Edge 意外关闭，**尚未到业务断言**；未因此改生产代码或削弱断言。失败保留于 [../canary_test/artifacts/round2/cloud-final-20260923/summary.json](../canary_test/artifacts/round2/cloud-final-20260923/summary.json)；最终结果来自上述新进程/新种子的完整 42/42，不是跨轮相加。
- 旧全量 **785 项（783 通过、2 跳过），532.420 秒**及旧定向 **249 项通过，109.750 秒**均早于最新修复：[../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-full.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-full.log)、[../canary_test/artifacts/prototype-refactor-20260923/cloud-contract-final.log](../canary_test/artifacts/prototype-refactor-20260923/cloud-contract-final.log)。旧核心 517 项（515 通过、2 跳过）及页面 32/深层 30 项仍仅是[云接入前历史基线](PROTOTYPE_REFACTOR_20260923.md#L45)。

### 真实调用、数据与服务边界

- **唯一一次获准的 Kimi `reference_narration` smoke 已成功**：[真实报告](../canary_test/artifacts/cloud-live-20260923/report.json#L1)。9.266 秒；实际 usage 为输入 377、输出 175、合计 552 tokens；预约 **100 分仍为 `held_for_reconciliation`，实际账单未知**。输入仅为自编、无可识别人物的合成纸片形状解说文字。只证明该参考解说适配器，不证明字幕翻译、云端前端端到端或腾讯能力；不得重跑一次性驱动或追加 Kimi 调用。
- 腾讯真实验收仍因凭据、私有桶、地域、模板及字幕审核语种、费率与预算未配置而阻塞。用户许可为本次**一次性不超过 50 元**，不是循环生产预算或新增购买/资源创建许可。
- **实际本地与生产环境文件未修改**；新增云作业及两个作业供应商开关默认关闭，三项预算默认 0。配置示例中的字段镜像不代表真实环境已启用。
- 最终本轮所有自有临时服务 **8766/8769/8770 已停止，8765–8770 监听数为 0**；259 个允许读取的原始源文件哈希在关闭时复核全部一致。未启动/修改日常 8000 服务；TEMP、失败证据和唯一付费回执保留，历史完整 live 原片的 4 项 QC 阻断不变。此处引用既有最终证据，不表示当前有测试主机运行。

## 2. 能力与输入边界

| `kind` | 供应商与实际输入 | 衍生产物与限制 |
| --- | --- | --- |
| `reference_narration` | Kimi；当前已提交流水线报告的正文文字 | 参考解说文字 JSON，附未核实警示；不是 TTS、事实核查或已批准新闻 |
| `subtitle_translation` | Kimi；报告正文按 `cue_id` 序列化，时间取同版本已提交时序 | 文字 JSON；ID 的类型、值、数量、顺序不变；模型只翻译文字，原时间在服务端回接 |
| `video_super_resolution` | 腾讯 MPS + 私有 COS；当前已提交原成片 | MP4；宽、高分别严格 2 倍，保持源 FPS |
| `video_interpolation` | 同上 | MP4；保持源尺寸，校验已审核目标 FPS |
| `audio_denoise` | 同上，必须有音轨 | MP4；保持尺寸/FPS，必须保留音轨；不是独立音频上传接口 |
| `smart_subtitles` | 同上，必须有音轨 | 单一语言 ASR 或 ASR 翻译的 SRT/VTT；不支持 OCR、纯字幕文件翻译、多语言/不明确或仅 URL 结果 |

只读取**当前流水线已提交版本**，不用 Studio 草稿、任意客户端文字、文件路径、URL 或回调。媒体上传的是整段已提交成片，不是自动选出的片段。能力查询及报价不调用供应商；媒体报价会在本地哈希和探测，接受提交后才可能上传/计费。

Kimi 固定 `kimi-k3`、`reasoning_effort=low`，仅允许已批准的 `https://api.moonshot.cn/v1` 端点（兼容 completions 路径、443 和末尾斜线），须明确批准 `cn`；不自动认可其它域名/跨境。所有文字仍是不可信纯文本，不执行 HTML/代码，不访问其中链接。

## 3. API、角色与版本绑定

公共前缀：`/api/tasks/{task_id}/cloud`。每个接口都要求真实课堂会话和私有作品权限：本班成员教师或学生作者；已发布作品的其他同学、旧任务令牌均不能绕过。POST 还须通过 Origin 与 `X-Classroom-CSRF` 校验。

| 方法/后缀 | 权限与响应 |
| --- | --- |
| GET `/capabilities` | 私有读取；`enabled`、6 个 `operations[{kind,available,provider,reason,target_languages}]`、布尔 `can_spend`/`can_cancel`、报价有效期 600 秒、文字输出上限 65,536 字节及费用/隐私警示 |
| POST `/quotes` | 仅教师，当前空闲 `done` 版本且 `can_export=true`；返回报价 ID、绑定版本/操作/语言、供应商、实际获准配置的 `region`、预约金额/计量单位/费率版本/过期时间及 `price_kind=reservation_ceiling` |
| POST `/jobs` | 同上，重新核验源和授权并原子预约；新作业 HTTP 202，有效幂等重放 HTTP 200 |
| GET `/jobs`、GET `/jobs/{job_id}` | 私有读取；列表为 `{jobs:[...]}`，包含实际状态、`reserved_fen`、`money_status`、取消意图和输出可用标志 |
| POST `/jobs/{job_id}/cancel` | 仅具有私有作品访问权限的本班教师；记录取消意图，独立于总/供应商开关、当前 `can_export` 及 drain；不承诺远端停止/退款 |
| GET `/jobs/{job_id}/output` | 私有读取且当前 `can_export=true`；须成功、同流水线版本，重新核对源快照和输出大小/SHA-256 后才提供附件 |

- `can_cancel` 在私有访问校验通过后对本班教师为 `true`、学生作者为 `false`；不依赖 `enabled`、供应商可用性、`can_spend`、导出批准或 drain。它不是绕过课堂身份/私有权限的授权，也不是远端必能取消的保证。
- `operations[].target_languages` 来自服务端审核策略：可用的两个 Kimi 操作为 `zh-CN/en-US/ja-JP`；可用的 `smart_subtitles` 只返回审核模板的单一输出语言；其它三种媒体操作为 `[]`（语言不适用），策略未通过的操作也返回 `[]`。配置可用不表示已在线核验云端模板。
- 报价体仅含 `kind`、严格整数 `expected_revision`（0 至 2⁶³−1）及 `target_language`（`zh-CN/en-US/ja-JP`，**API 缺省仍为 `en-US`**）；两个 Kimi 操作均接受三种语言。当前 UI 对参考解说、字幕翻译和智能字幕默认选择并显式发送 **`zh-CN`**。
- 智能字幕的 `Settings.cloud_mps_subtitles_language` 为 `Literal["", "zh-CN", "en-US", "ja-JP"] = ""`，已镜像到[本地配置示例](../.env.example#L142-L144)及[生产配置示例](../deploy/golden-mic.env.production.example#L186-L188)。空值使该操作不可用（`template_language_required`）；报价请求与本地审核语种不符时以 **422 `language_not_supported`** 在源哈希/探测、报价账本访问及上传之前拒绝。提交、执行和恢复还会重验保存的语种与配置绑定；此本地声明不能排除操作者误填或远端模板漂移。
- 提交体仅含 `quote_id`、`idempotency_key`（各 32 位小写十六进制）、严格整数 `max_cost_fen`（1 至 2⁶³−1）、`rights_confirmed=true`、`no_identifiable_people=true`。两项确认是操作者声明，**不是自动身份/权利检测**。接受金额须不低于服务器报价，实际只预约报价额；客户端不能定价，当前 UI 原样提交报价金额。
- POST 请求体最多 **8 KiB**，拒绝重复 JSON 键及 NaN/Infinity 常量；报价/提交模型另拒绝未知字段、非有限数值及错误类型，取消接口不接收操作参数。常见错误：401/403 身份或配置门禁，404 私有对象不可用，409 版本/预算/幂等/任务冲突，413 大小超限，422 输入不合规，429 容量不足，503 drain/账本不可用。
- 绑定包含任务、流水线 revision、教师/班级、操作/选项、报告与**成片 SHA-256（文字作业也绑定成片）**、可用的提交清单哈希；文字另有文本哈希，翻译另有时序哈希，媒体另有探测属性。配置指纹 **schema 3** 含预算、费率、批准地域列表、实际 `region`、当前操作的 `target_languages`、端点/模型、模板 ID 和处理限制，不含秘密或仅控制准入的 `cloud_max_active_jobs`。地域或审核语种变化会使旧绑定失效，不能沿用旧报价。
- 新报价有效 **10 分钟**；源、批准或配置变化需重新核验，不能用新版批准开放旧版输出。`available` 只是配置检查，`can_spend` 也不是余额/容量预约或真实云账号连通证明。`output_available` 不替代下载时的完整性检查。
- 报价**必须返回 `region` 与 `price_kind=reservation_ceiling`**：Kimi 为 `cn`；腾讯为 `cloud_approved_regions` 明确批准的实际 `cloud_tencent_region`。前端拒绝缺失/非法地域或错误价格类型，不自行补值。显示的地域是获准服务端配置，**不是独立验证的数据驻留证明**。
- 响应使用 `no-store`/`nosniff`；不公开远端 ID、COS 键、签名 URL、原始供应商响应或秘密。

## 4. 预算、幂等与准入

| 配置 | 实际语义/范围 |
| --- | --- |
| `cloud_budget_id` + `cloud_budget_fen` | 具名一次性总额度；默认空/0，总额配置范围 0–5,000,000 分 |
| `cloud_daily_budget_fen` | 同预算 ID 下按作业创建时间计的 **UTC 自然日**额度；默认 0，范围 0–5,000,000 分 |
| `cloud_task_budget_fen` | 同预算 ID 下单任务跨日累计额度；默认 0，范围 0–100,000 分 |
| `cloud_max_active_jobs` | 同账本跨教师、班级、预算 ID 的活动作业准入上限 **1–8，默认 1**；每任务最多 1 个活动云作业 |

新增提交要求三个额度均为正，具名预算、总/供应商开关、隐私批准、无可识别人物素材、地域、凭据及当前操作审核费率全部通过；腾讯还需当前操作模板。缺项直接拒绝，不模拟排队。上述配置最大值**不是本次获准金额**，本次许可仍不超过 5,000 分。未实现月度或每班预算，也不会自动续期。

- `get_cloud_store()` 将账本定位在 `settings.data_dir.parent` 下，独立于可删除的单任务文件夹；SQLite `WAL`/`FULL` 保存报价、远端 ID、预约及状态事件。预算检查、全局/单任务准入和预约在同一 `BEGIN IMMEDIATE` 事务中完成；媒体/网络 I/O 不放入该事务。
- 同预算 ID 首次额度不可调高，临时调低可限制新预约；不得通过换 ID、删作品或重启扩大授权。只排除 `released`，失败、未知、已成功乃至 `settled` 仍按预约额计入累计占用。UTC 换日只影响日额，不重置一次性/任务累计额。
- `cloud_rates_json` 的键只能是已实现操作；每项严格包含 `unit`、`max_fen`、`transfer_fen`。文本仅 `request`；媒体可为 `request` 或 `minute`。`max_fen` 为 1–5,000,000 分，`transfer_fen` 为 0–5,000,000 分。按分钟时本地单位数为源秒数除以 60 向上取整；预约额为单位数乘 `max_fen` 再加一次传输额度，**不是供应商真实计费算法**。
- Kimi 另检查保守本地许可下限：以源 UTF-8 字节数 ×6＋8192 作为输入 allowance，按未命中输入＋1 小时缓存写入费率，加全部允许输出 tokens 的费率，向上取整到分；审核的 `max_fen` 不得低于它。默认输入/输出/缓存写入分别为每百万 2000/10000/4000 分；三项 `Settings` 费率均为每百万 1–1,000,000 整数分，仍须人工审核，不是实测 tokenizer 或保证账单上界。
- 所有报价标记 `price_kind=reservation_ceiling`：**预约既非实际账单，也非有保证的账单封顶**。报价面板与提交确认均明确警示“**实际账单可能超过预留额**”。COS/传输、其它入口或凭据被别处使用的费用、账单延迟均不能靠本账本全局封顶；实际 usage 也不等于已结账金额。
- 幂等唯一键为 `(actor_id, idempotency_key, budget_id)`；同键换报价返回 409。当前权限/源/配置仍通过时，同键同报价重放只读原作业，不再预约或调用；可以跨报价到期、取消及准入并发下调，但不能绕过总开关/授权检查。幂等不是按素材去重，更换键可能产生新费用。
- 活动计数包括 `submission_unknown`，以及已尝试或有远端 ID 的 `interrupted`；占槽不因换预算 ID 消失。超全局上限返回 429 `capacity_exceeded`，同任务已有活动作业返回 409 `task_busy`。成功但待对账只占金额，不继续占活动槽。

## 5. 硬限制与输出验证

| 项目 | 已实现限制 |
| --- | --- |
| 通用 | 云作业超时默认 600 秒，可配 30–3600；媒体下载上限默认 **128 MiB**，可配 **1–512 MiB** |
| 源绑定 | 成片非空且 ≤5 GiB；提交报告/时序各 ≤8 MiB，报告 1–256 行、每行 ≤2000 字符；账本源快照 ≤64 KiB |
| Kimi | 源 ≤32,768 字符；输出文字 ≤16,384 字符，最终 JSON ≤64 KiB；HTTP 响应 ≤2 MiB；输出 tokens 1–2048（默认 2048），单次请求 1–60 秒（默认 60） |
| Kimi 翻译时序 | 按报告 ID 匹配已提交时序，严格顺序、不重叠、结束 ≤3600 秒；模型不得增加时间字段。不能把腾讯的 120 秒限制误套为所有文字作业上限 |
| 腾讯输入 | MP4、单一视频流、宽高为 ≥2 的偶数；**≤120 秒、长边 ≤1920 且面积 ≤1920×1080、0＜FPS≤60、仅 SDR 范围**；降噪/智能字幕须有音轨 |
| 腾讯视频输出 | MP4、单一视频流、偶数尺寸；**长边 ≤3840 且面积 ≤3840×2160（4K 范围）**、FPS≤60；超分宽高各严格 2×，其它操作尺寸不变 |
| FPS | 超分/降噪与源 FPS 差 ≤0.02；补帧目标按 `Settings` 为 **24–60 的整数，默认 60**，源 FPS 不得高于目标；输出不得低于源，且与目标差 ≤0.02；不是任意 120fps |
| 时长/音轨 | 视频输出与源时长差 ≤较大者（0.25 秒或源时长的 2%），且探测时长 ≤122.4 秒；源有音轨则不得丢失。音轨存在不证明音画同步或音质 |
| 腾讯字幕 | UTF-8 SRT/VTT、≤1 MiB、1–4096 条、每条 ≤2000 字符；时间递增且不重叠，结束 ≤源时长＋0.5 秒；拒绝 HTML/ASS 控制内容 |

显式 HDR/广色域标签及 HDR side data 会被拒绝；缺少色彩标签**不是像素级 SDR 证明**。视频结果记录 `validation=stream_properties_only`、`quality_measured=false`，只验证容器/流属性及哈希，不声称实测超分细节、光流质量、降噪效果或硬件性能。全部产物保持 `unreviewed`，不覆盖原片、不自动回灌 Studio/已审核成片、不自动批准或发布。

补帧目标是服务端配置，不是客户端自由参数；路由内部的防御检查接受 1–60，正常 `Settings` 仍将可配置范围收紧为 24–60。

## 6. 状态、不确定性与清理

作业状态：`reserved`、`preparing`、`submitting`、`submitted`、`running`、`downloading`、`succeeded`、`failed`、`cancel_requested`、`cancelled`、`submission_unknown`、`interrupted`。金额状态独立：`reserved`、`held_for_reconciliation`、`released`、`settled`。

- COS 上传本身可能计费，上传前即保守记录 `attempted`；Kimi 请求前也记录尝试。仅能证明未尝试、无远端 ID 的作业可由预提交释放逻辑退还预约；成功、失败、拒绝、超时或取消都不证明免费。缺失/非法 usage 保留为未知，不补零。
- 不自动重试付费创建。腾讯发送 `SessionId=job_id` 且禁用 SDK 重试，但本地幂等不依赖供应商去重期限。未知受理不重提；Kimi 没有可自动恢复的远端任务轮询。
- 重启先恢复账本：预提交作业标中断；只有有持久远端 ID、源/配置仍一致的腾讯作业可恢复轮询/下载，绝不重新创建。配置变化、无 ID 或结果不明需人工核对。**没有对账结算/人工修复的 UI 或公开运维 API，也没有自动对账、未知任务匹配或已尝试费用的自动释放**；账本有 `settled` 状态不代表已有结算产品。
- 取消 API 记录意图；腾讯仅在 `WAITING` 尝试 Abort，接受 Abort 也不证明已取消，`PROCESSING` 可能继续计费。Kimi 停止本地请求不等于远端停止。取消不会自动解除未知受理保护。
- 活动作业及有远端风险的中断记录阻止媒体修改、复制、删除/TTL；终态账本保留，删作品不能抹账。服务端再次提供已落盘输出时仍须当前课堂授权；无法撤回用户已经下载的副本。
- 腾讯只在安全终态尝试清理该作业随机前缀下的**当前对象**；中断下载/未知受理保留远端数据供核对。清理最多 20 页/10,000 个对象；失败或关闭异常可记录 `cleanup_pending`，**尚无自动重试清理队列/修复按钮**。
- `cleanup()` 不删除历史 COS 版本、删除标记、遗留分片、复制/备份、供应商内部副本或本地下载。遇到版本删除标记不能宣称物理删除成功；必须另行审核生命周期和人工处置，也不能由桶地域保证完整处理地域。

## 7. 前端与适配器边界

### 7.1 面板、语言与取消

- `CloudTools` 仅在课堂 Studio 挂载，使用当前已通过导出门禁的 **pipeline revision**，不是工程 revision。读取以 5–30 秒退避，30 秒过期；隐藏页面使查询失效，回到页面/获得焦点重新核验，旧状态不能冒充当前权限。只读刷新不保存/重载工程，不自动报价、提交或编造 ETA。
- 明确选择 → 服务器报价 → 两项默认未勾选声明 → 再次确认费用并刷新能力/任务 → 保存提交回执 → 提交。目标语言默认简体中文，作用于 `reference_narration`、`subtitle_translation` 和 `smart_subtitles`；所选语言不在该可用操作的 `target_languages` 中时，其报价/提交不可用，不上传素材。报价与确认框都展示服务器返回的供应商、实际获准配置地域和“实际账单可能超过预留额”。
- 取消使用独立、新鲜的 `can_cancel`，不依赖 `canSpend`、非空已批准 revision、总/供应商开关或导出门禁。原生确认框确认后，**先重新读取 capabilities 与 jobs**，再次确认教师权限及该作业仍可取消、未已请求取消，再发取消 POST；读失败、权限或状态变化即不发。学生不能取消；取消结果未知只查询，不自动重发，也不解除未知付费提交保护。
- 文字只下载 JSON，不渲染模型 HTML；下载仍由后端最终鉴权。没有管理员恢复/对账界面。

### 7.2 同标签页最小提交回执

- [回执实现](../frontend/src/lib/cloudReceipt.ts#L1)使用 **`sessionStorage`**，键为 `golden-mic.cloud-attempt.v1:${actorScope}:${taskId}`，其中 `actorScope` 为 `${role}:${id}`。[App 传入真实课堂会话身份](../frontend/src/App.tsx#L446)，经 [Studio 转交](../frontend/src/components/Studio.tsx#L882)；`id` 是 `identity.id` 的不透明标识，不是显示名、客户端杜撰身份或登录令牌。不同身份和任务分别隔离，缺失/非法作用域不允许付费。
- 单份回执序列化长度 **≤4096**，仅含 `schema=1`、不透明 `quoteId`、幂等 `key`、`revision`、`state`，以及仅 `accepted` 状态允许的 `jobId`。不保存源文字/媒体、密钥、会话令牌、CSRF 或个人资料；它不是授权、工程草稿或账单。
- 每份报价最多生成一个安全随机幂等键。完成费用确认与最新门禁核对后，**同步写入 `sending` 并读回验证，才允许该次唯一的 POST `/jobs`**。读取/写入/读回失败、格式错误、超长或不合法作用域均 fail closed：停止新报价与付费提交，仍可只读查询；不暴露原始存储内容或异常。
- 有效且绑定相符的受理响应才能记为 `accepted` 并保存 `jobId`；明确拒绝记为 `rejected`，响应不确定记为 `unknown`，中途卸载可能保留 `sending`。**`sending`/`unknown` 不随报价过期而重置**，在同标签页组件卸载/重挂载、刷新，以及退出后同一身份重新登录时仍保留。列表出现相似甚至已完成作业不能证明报价/幂等绑定，也不能据此解除保护。
- 最小回执保留最后一次尝试；仅已明确 `accepted`/`rejected` 的记录可在**下一份新报价开始提交时**替换，不用原报价换键重发。晚到响应不能覆盖其它尝试或降级已确定状态。恢复界面只查询，不自动恢复付费请求、不自动结算，也无回执对账/修复端点。
- **保护范围仅同一标签页**，不提供跨标签页协调、完整浏览器重启后的持久保护或清除浏览器存储后的保护。关闭标签页、清存储、换页/身份或生成新键都不是安全重试方法；不得手动清回执来重新下单。服务端授权、预算、幂等仍是独立防线，但不按素材阻止使用新键再次消费。

### 7.3 供应商适配器

- `KimiEditorialProvider.generate(kind,text,target_language)` 返回 `text/source_text/language/usage`，翻译另有 `cues`。只发一个有界 chat 请求，无付费重试；其安全错误区分 `attempted` 与 `uncertain`。
- `TencentMediaProvider` 懒加载，提供 `upload/submit/inspect/abort/download/cleanup/aclose`；限定同一私有 COS 桶/地域和服务端随机前缀，无任意 URL 下载。`FINISH` 仍须校验工作流、子任务成功、模板 ID、输出存储/键；SDK 在线程中运行，取消会等线程收尾，可能超过本地截止时间。模板由操作者审核固定，适配器不创建/修改模板；ID 指纹不能原子锁定云端模板内容。
- 智能字幕的[远端模板在线核验](../backend/providers/cloud_media.py#L509-L578)仍发生在 **COS 上传之后、`ProcessMedia` 之前**，检查实际模板模式、单语输出及请求语言。已知本地语种不匹配可以在报价前拒绝，但操作者误填审核语种或云端模板漂移仍可能直到上传后才发现；**不能宣称错误配置/漂移不可能，也不能保证未上传或零费用**。