# 核心工作区契约 · 2026-09-27

**当前产品范围：无登录的 AI 新闻生成 + 逐句工作台 + 有界专业剪辑。** 本文与 [部署指南](../deploy/README.md)、[WORKBENCH_API.md](WORKBENCH_API.md)、[STUDIO_API.md](STUDIO_API.md)、[MEDIA_INPUT_API.md](MEDIA_INPUT_API.md) 为本次范围调整的当前契约。旧课堂、账号、队列、独立云作业及原型验收文档只描述历史版本；不能用其登录/批准步骤启动当前产品。

**本地集成与日常 8000 部署已完成。** 后端 766 项（764 通过 / 2 跳过，0 失败/错误）、前端 286/286、浏览器 14/14（0 重试）及 56 个无溢出布局样本，应用/E2E 类型和构建通过。精确结果、旧失败、证据来源与未执行范围见 [CORE_WORKSPACE_VALIDATION_20260927.md](CORE_WORKSPACE_VALIDATION_20260927.md)。合成浏览器没有新 live Provider/付费调用；Linux/目标机、硬件、完整 WCAG 与新模型质量未验收，C26 原生崩溃风险未解决。文档收尾不重跑测试/构建，不改机器证据。

## 1. 保留与移除

保留文稿、视频/照片、素材裁剪/备注、AI 配音/整篇自主录音、十阶段生成、逐句报告、MP4 预览/下载、历史修订、工作台、Studio、显式失败重试及本地独立副本。

移除登录/注册/首次教师初始化、学生/教师身份、班级/名册/作业、作品墙、课堂草稿与审核/发布/放行流程；独立云编辑/MPS 作业的路由与 UI 也退出产品。没有隐藏 Basic Auth、账号批准替代物或新访问模式开关。旧 cookie、教师身份头、班级 ID 不授予核心任务访问权；创建时不再接收 `classroom` 字段。

核心 Kimi/火山引擎调用仍然存在。移除“独立云作业”不等于生成完全离线、不再计费或可移除 Embedding/ASR/TTS 凭据。非事实性 `generative_fill` 仍保留，运营开关及任务默认均为 **false**。

本轮范围清理没有替换或简化核心新闻生成算法；源时序、不可变修订、字幕/音频处理、真实 QC 与生成内容披露保留。访问/费用保护不属于被删除的账户系统，用户仍须主动提交或明确确认可能计费的编辑/重试。

## 2. 两种部署边界，不是两种登录模式

### 自有单机开发工作区

运行开发服务并绑定 `127.0.0.1`，只供该机器的数据所有者使用。免任务令牌的本机历史/编辑权限必须同时满足：

1. 非 production；实际连接客户端为 IP loopback，URL Host 为 `localhost` 或 loopback IP，且只有一个 Host 头。
2. 没有 `Forwarded`、`X-Real-IP` 或任意 `X-Forwarded-*` 头；不能仅凭代理后的客户端地址或伪造 loopback Host 获得权限。
3. 提供 `Sec-Fetch-Site` 时仅接受单个 `same-origin` 或 `none`。读取可不带 Origin；带 Origin 必须精确等于当前基础 Origin。
4. **免令牌写入必须带精确同源 Origin**，即使开发环境的普通 `ENFORCE_ORIGIN_CHECK=false`。关闭该开关不是放宽本机写入的办法。

存在的错误、空或相互冲突的任务令牌不能回退为本机权限。本机模式不是多用户隔离：能访问这台机器的可信工作区就能访问其本机任务。不得绑定 `0.0.0.0`，不得通过 LAN、隧道或公网代理暴露开发工作区。推荐使用同源的已构建前端；开发代理若改写 Host/Origin 或添加转发头，不保证本机免令牌权限，不能通过移除安全检查“修复”。

### 生产，无登录但仍有访问控制

- `APP_ENV=production`，Nginx HTTPS 为唯一公网入口，Uvicorn 仅监听 `127.0.0.1:8000`、单 worker、关闭访问日志。
- `GET /api/session` 自动建立签名匿名会话。生产 cookie 为 `__Host-golden_mic_session`，带 Secure、HttpOnly、SameSite=Strict、Path=/；响应包含 `access_mode:"anonymous"`、`csrf_token`、`expires_at`。`access_mode` 只是响应描述，不是配置开关；开发响应为 `development` 和两个 null。
- 所有任务写入在应用边界要求合法 cookie、`X-CSRF-Token` 与允许的 HTTPS Origin；这是防跨站/费用保护，**不是身份认证，也不替代单任务 capability**。会话刷新后不可自动重放 POST。
- 新任务与副本返回非空 `access_token`。私有 API 使用 `X-Task-Token`；受保护媒体支持 `token` 查询参数。服务端只持久化令牌哈希；状态、报告、本机列表、工程备份不返回/恢复明文令牌。
- 生产始终 `local_history:false`，**任何生产请求（包括直接 loopback）都不能获取全局任务索引**。Cookie 不能列出或授权其他任务；没有账号找回接口。浏览器保存的任务凭据丢失后，不能靠刷新会话重新枚举服务器任务。
- 浏览器历史使用 `golden-mic.history.v1` 保存任务凭据，是敏感能力存储，不是云账户同步。不要共享浏览器配置、带令牌 URL 或未脱敏截图；服务端 TTL/显式删除后，历史条目不保证仍可打开。仅移除浏览器条目不等于删除服务器数据。

实际入口与权限实现在 [backend/main.py](../backend/main.py)、[backend/anonymous_access.py](../backend/anonymous_access.py)、[backend/task_operations.py](../backend/task_operations.py) 和 [frontend/src/lib/appApi.ts](../frontend/src/lib/appApi.ts)。

## 3. 核心 API

| 方法 / 路径 | 契约 |
|---|---|
| GET `/api/config/workspace` | `{local_history:boolean,generative_fill_available:boolean}`；仅能力提示，不含秘密、目录或任务列表，也不代表 Provider 实测成功 |
| GET `/api/config/limits` | 文件数、单文件/总字节数、稿件长度、源时长和允许后缀；UI 必须使用实例返回值 |
| GET `/api/config/availability` | 浏览器安全的 `ready` / `not_ready`，不公开运维诊断 |
| GET `/api/session` | 匿名安全会话 bootstrap；不登录、不创建任务、不调用 Provider |
| GET `/api/tasks?offset=0&limit=200` | **仅可信本机开发请求**；`offset≥0`，`1≤limit≤200`；返回 `{tasks,total}`，每项含 `task_id,title,status,revision,created_at,updated_at`，无令牌/路径。生产或不可信请求 404 |
| POST `/api/tasks` | multipart：`script`、`files`，可选 `preferences`、`asset_options` JSON、`own_voice` 文件、`script_format`；202 `{task_id,access_token}`，令牌必须非空 |
| GET `/api/tasks/{id}` | 状态、当前修订、阶段/总耗时、进度及安全错误 |
| GET `/api/tasks/{id}/video` | 当前已提交成片，支持 Range；`download=true` 为附件 |
| GET `/api/tasks/{id}/report` | 当前已提交逐句报告与 QC |
| GET `/api/tasks/{id}/thumbs/{shot_id}.jpg`、`/poster` | 受同一任务权限约束的缩略图/预览 |
| POST `/api/tasks/{id}/retry` | 严格 JSON `{expected_revision}`；符合条件的失败初版完整重跑；202 `{task_id,status:"queued",revision:0}`，不返回新令牌 |
| POST `/api/tasks/{id}/duplicate` | 严格 JSON `{expected_revision}`；完成版本的本地独立复制；201 `{task_id,access_token,status:"done",revision:0}`，**新 ID、新非空令牌** |
| POST `/api/tasks/{id}/remix` | `keep_sentence_ids`；保持原顺序、至少一句，异步删句重剪 |
| POST `/api/tasks/{id}/replace-shot` | `sentence_id` + `instruction`；异步文字换镜，可涉及模型调用 |
| GET/POST `/api/tasks/{id}/workbench/...` | 批量编辑、录音、不可变版本与恢复，见工作台契约 |
| GET/POST/DELETE `/api/tasks/{id}/studio/...` | 工程、源、图片/LUT、RAW 代理、渲染/导出与作业取消，见剪辑台契约 |
| DELETE `/api/tasks/{id}` | 显式取消/删除，成功 204；受副本/媒体作业保护，清理失败不能当成功 |

创建校验与媒体含义见 [MEDIA_INPUT_API.md](MEDIA_INPUT_API.md)：向导显式 `headline_first`，默认 API `auto`；标题不播报；整篇录音必须真实 ASR 对齐而不是任意“配稿”。接收上传后即进入普通任务准入/排队，**不等待老师放行**；接收回执不意味着生成成功。

### 明确成本与重试

- 创建和 `/retry` 都可能调用已配置 Provider，必须由用户明确发起。重试使用同一任务，不是从失败阶段继续；只允许空闲失败初版、`revision=0`、请求 `expected_revision=0`、无已完成报告/成片/修订、原素材完整且安全。
- `expected_revision` 必须是非负 JSON 整数（不是布尔、字符串）；未知字段拒绝。旧修订、不完整原素材、忙碌、排空、未核对的旧远端作业等必须拒绝，不创建旁路。
- 创建/重试共享请求频控。生产默认每匿名会话/出口 IP/全站每小时 2/5/10 次；它是单进程内存计数，**不是可靠日预算/持久计费账本**。编辑的 LLM/TTS 与媒体作业也不能因此视为免费；保持 Provider 预算、WAF 与资源护栏。
- 网络中断、响应丢失、历史存储失败不是重发许可。先读状态核对；生产如果没有收到/保存 capability，没有全局查找兜底。不要轮换会话、重启服务或循环重试以绕过费用边界。

### 独立副本不是重新生成

复制只读取当前完成版本的允许列表媒体/元数据，预留磁盘后进行本地复制和修订基线建立，不调用 Provider、不重跑 QC 来宣称质量改善，也不覆盖原任务。复制受 8 GiB（包含初始快照）上限、源/修订/路径一致性、忙碌/排空/残留远端风险检查约束。副本保留真实 QC/生成内容披露，并继承源任务的 `local_only` 标志；新 ID/token 不会使旧本机作品公开。不携带旧账号/批准或复用原 capability；不承诺复制全部 Studio 工程、导出作业或旧历史账本。

## 4. 旧任务保留与重启

- 兼容性从持久任务状态读取：旧 `classroom_task:true`、`queue_hold:true` 或已有 `local_only:true` 作为 `local_only`。不通过课堂 DB 查询归属，不初始化或迁移 DB，也不将旧任务自动转换成公共 capability 任务。
- 旧任务只向可信本机开发工作区开放，生产令牌访问也不绕过 `local_only`。历史成片/源文件保留；普通 TTL 清理跳过这些记录和无法安全分类的旧状态。数据所有者可在本机显式编辑/重试/复制/删除，但这不是隐式公开或自动启动。
- 重启时所有未完成的 queued/running 任务标记失败，包括旧未放行上传；未完成副本不发布。读取历史、启动服务、刷新页面均不开始付费生成。
- Studio 未完成作业在恢复读取时标记 interrupted，不自动重新编码。已成功的衍生输出仍是受保护的不可变历史输出，不依赖教师批准；新渲染/导出继续检查真实 QC、当前源/修订和作业条件。RAW 代理有独立当前源/修订/散列/作业保护，不要求导出 QC，但不能充当导出旁路。
- 旧独立云账本仅可能被只读风险检查使用；未决/未知远端受理继续阻止危险修改/删除。移除 UI 不取消远端计费、不清空预约、不代为对账；不要删除账本来解锁。
- 保留原数据库及备份、WAL/SHM、失败证据和已有费用回执。它们不属于当前应用账户系统，但仍是敏感历史数据；不能据“无登录”将数据目录挂为静态站点。

本次受控重启前后，46 个任务状态与旧 DB 共 47 个文件无变化；服务恢复 41 done / 5 failed，既有 15 句、61.8 秒作品只读打开且编辑入口可用，没有在用户任务上编辑或生成。这是有限文件/入口验证，不是所有历史媒体散列验证；OneDrive 阻断的额外报告读取没有被强制 hydrate 或覆盖。详见[数据保护记录](CORE_WORKSPACE_VALIDATION_20260927.md#5-日常-8000-部署与旧数据保护)。

## 5. 工作台与 Studio 仍有真实门禁

没有账号审批，不等于取消授权/正确性检查：任务 capability 或严格本机 authority、生产 CSRF/Origin、当前记录/源/修订、忙碌/锁、drain、磁盘/大小/时限、路径及生成内容披露继续生效。异步读取正文、探测、复制后必须重查相应条件。

工作台以 `expected_revision` 进行批量提交/恢复，失败保留上次成功版；逐句录音不证明台词/身份，整篇录音的 ASR 对齐也不证明说话人身份。Studio 输出独立于流水线成片/报告，不自动获得语义 QC 通过；存在真实 QC 阻断时不能靠移除审批绕过。RAW 源预览与衍生导出不同，预览不表示允许导出。

编辑仍有限：主时间线 + 最多 4 子序列，每条至多 8 轨，全工程至多 32 存储轨/64 片段；活动嵌套展开最多 8 层/64 实例/16 解码输入/120 秒/1080p。不是完整 NLE、HDR/4K、自动多机位或全部原型工具。

没有进程/文件的跨资源断电事务或分布式调度保证。历史 [C26 审查](../canary_test/CANARY_C26_20260926.md)的 Python 原生崩溃高风险与深层主机正常退出证据缺口，不因本次范围缩减消失。

## 6. 配置与部署迁移

- [开发模板](../.env.example)只用于本机开发；[生产模板](../deploy/golden-mic.env.production.example)由受信任操作者渲染到受限 EnvironmentFile。秘密不进仓库、命令参数、聊天或日志；本次不读取/改写真实环境文件。
- `PUBLIC_ACCESS_MODE` 已删除，不是弃用后仍可选择的配置项。安装器没有 `--access-mode` / `--htpasswd-file`，不创建应用用户；`goldenmic` 仅是 systemd 所需的非交互 OS 身份。
- 保留 TLS/Host/Origin、安全响应头、覆盖的真实客户端 IP、无查询日志、上传/媒体/队列/磁盘资源护栏、签名发布、排空和回滚。所有上游日志也须脱敏；任务 token 出现在媒体 URL 中。
- 核心 `KIMI_*`、火山 Embedding/ASR/TTS 及可选生成式补拍配置保留。`generative_fill_available` 只报告开关/配置可用；补拍仍需任务选择和符合非事实性 fallback 条件，不绕过披露/QC。
- **退役示例配置已同步并通过精确 schema 检查：** `Settings` 与两份示例模板已移除独立云作业的 32 个 `CLOUD_*` 键和 `PUBLIC_ACCESS_MODE`。9 项部署测试已通过并计入后端总数；实际生产文件的旧键仍须由操作者按同版 schema 清理并校验。没有读取/改写实际生产凭据文件，不声明生产迁移通过；不能恢复旧字段/路由或放宽校验。
- Tencent 3 个直接依赖与 7 个专属传递依赖已退出声明/锁/导出/SBOM；当前 44 个外部包名、45 个 SBOM 组件，保留版本/制品哈希未变，用户环境 76 个 distribution 未卸载。离线锁、`pip check` 和安装环境验证通过，详见[依赖记录](CORE_WORKSPACE_VALIDATION_20260927.md#6-依赖配置与制品边界)。

## 7. 本次验证结果与未执行范围

| 已完成的本地验证 | 明确不覆盖 |
|---|---|
| 后端完整 766 项：764 通过 / 2 跳过，435.199335 秒；94 源文件零漂移、0 套件违规、0 残留自有 HTTP listener | opt-in 历史音频回放、Windows 无权限 symlink 项仍跳过；不是实网 Provider 验收 |
| 能力/本机权限、匿名生产 CSRF/Origin、旧数据/重启、重试/副本、工作台/Studio 媒体和修订保护回归 | 不是公网代理/TLS、并发压测或断电事务保证 |
| 前端 286/286，8.5351768 秒；应用/E2E 类型、1788 modules/4 assets 构建及当前清单一致性 | 最终发布归档由主集成另验，尚不声明通过或签名完成 |
| 浏览器 14/14，69.495374 秒，0 重试：真实十阶段、四次逐句修订、Studio 1.5 秒/360p 下载、同源负向、草稿与 56 个布局样本 | 假模型/音调 TTS；不含 ASR、自主录音、直接视频 Embedding、新语音质量、物理麦克风或完整 WCAG |
| 9 项部署契约（含精确环境 schema）与本机 8000 空闲排空重启、live/ready 200、47 文件不变 | Linux CI/ShellCheck/实际 Nginx/systemd/目标机、TLS/日志链、安全组、备份恢复、回滚/故障演练未验收 |

原始结果、两项 skip、前期失败与最终 TEMP 正常退出范围见[验收汇总](CORE_WORKSPACE_VALIDATION_20260927.md)。当前本地完成不修复 C26 原生崩溃 HIGH 或其历史退出证据缺口；后续真实生成/硬件/生产验收仍需独立安排，任何付费请求必须重新明确授权，不能自动补跑。