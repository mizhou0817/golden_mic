# Golden Mic 全面审计报告

审计日期：2026-07-23

> 后续变更：AI MediaKit 云端画质增强已从当前版本中移除。阶段 2 现在只执行同期声识别与本地 FFmpeg 格式适配；下文涉及 MediaKit 的内容仅保留为本次审计时的历史记录，不代表当前运行行为。

## 结论

项目的核心十阶段流程、AI MediaKit、Vision、Embedding、LLM、TTS、ASR、同期声替换、字幕、渲染和快速重剪均可运行。审计期间发现并修复了多项会导致生产失败、安全暴露或费用失控的问题。修复后后端完整测试、前端生产构建、Python 依赖一致性检查、Python 漏洞扫描和 npm 漏洞扫描均通过。

本项目仍定位为单机、单进程、无账号系统应用。它适合本机或受保护的内网环境，不应在没有反向代理认证的情况下直接暴露到公网。

## 已修复的高优先级问题

### 1. 任务资源越权访问

原先仅凭 task ID 即可查询、下载、重剪或删除任务。现已为每个任务生成 256 位随机访问令牌：

- 状态、报告、重剪和删除使用 `X-Task-Token`。
- 视频和缩略图使用媒体 URL 中的 `token`。
- 缺少令牌或令牌不匹配统一返回 404。
- 令牌不写入任务 manifest 或日志。

相关文件：

- `backend/models.py`
- `backend/task_manager.py`
- `backend/main.py`
- `frontend/src/api.ts`
- `frontend/src/App.tsx`

### 2. MediaKit SSRF、重定向和无限下载风险

现已对 MediaKit 预签名上传地址和增强结果下载地址执行：

- 仅允许可信火山域名后缀。
- DNS 解析结果必须全部为公网 IP。
- 禁止自动重定向。
- 配置最大下载字节数。
- 校验 `Content-Length` 与实际下载大小。
- 临时文件失败时删除。

相关文件：`backend/providers/mediakit.py`

### 3. 云端计费前缺少输入与配置预检

原先可能在 MediaKit 已产生费用后，才发现 Vision、Embedding、LLM 或 TTS 配置缺失。现已在任务进入第一阶段前检查：

- FFmpeg 与 ffprobe。
- MediaKit、Vision、Embedding、LLM、TTS、ASR 配置。
- 上传文件必须含真实视频流。
- MediaKit 输入分辨率和 SDR 限制。
- MediaKit 增强结果与原片时长偏差。

相关文件：

- `backend/pipeline.py`
- `backend/media.py`

### 4. 生产 LLM 配置缺失

此前 `.env` 没有 LLM 配置，生产任务会在镜头精排阶段失败。现在默认使用已验证可用的 Doubao Seed 2.1 Pro Chat Completions，并支持切换回 OpenAI 兼容 LLM。

相关文件：

- `backend/config.py`
- `backend/providers/llm.py`
- `.env.example`
- `README.md`

### 5. 敏感数据可能进入日志或前端错误

现已增加全局脱敏：

- Bearer Token。
- API Key、Access Token、App Key、Authorization。
- 签名 URL 的 `auth_key`、`signature`、`token` 等参数。
- 任务失败信息在返回前脱敏。

相关文件：

- `backend/storage.py`
- `backend/task_manager.py`

### 6. 已知依赖漏洞

初次 `pip-audit` 检测到 15 个公开漏洞，涉及旧版 `python-multipart`、`click` 和 `starlette`。现已升级并固定兼容版本：

- FastAPI 0.139.2
- Starlette 1.3.1
- Uvicorn 0.51.0
- Click 8.3.3
- python-multipart 0.0.32
- pydantic-settings 2.14.2
- SceneDetect 0.7.1

修复后：

- `pip-audit -r requirements.txt`：0 个已知漏洞。
- `npm audit`：0 个已知漏洞。

### 7. 费用和资源耗尽护栏不足

新增：

- `MAX_PENDING_TASKS`：限制全局排队和运行任务数。
- `MAX_SENTENCES`：限制稿件分句数，防止上千次 Embedding/TTS 调用。
- IP 频控来源字典最大基数。
- 429 响应携带 `Retry-After`。
- MediaKit 控制面重试和指数退避。
- 大目录删除移出事件循环。

### 8. 关键 JSON 可能半写

MediaKit manifest、ASR transcript、match plan 和 upload manifest 改为临时文件写入后原子替换。

相关文件：`backend/storage.py` 及各管线模块。

### 9. 前端网络和可访问性不足

已增加：

- API 请求超时。
- 上传请求独立长超时。
- 网络失败、429 和 5xx 的用户可读提示。
- 上传区域键盘操作、`role` 和 `tabIndex`。
- 明确的按钮类型。

## 仍然存在的风险和不足

### Critical：匿名创建任务仍可造成云费用滥用

任务访问令牌只能保护已创建任务，不能识别创建者。分布式攻击者仍可使用多个 IP 创建 MediaKit、Vision、Embedding、LLM、TTS 和 ASR 任务。

建议：

1. 公网部署必须放在 VPN、Zero Trust、Basic Auth 或 OIDC 反向代理后。
2. 生产版增加用户系统、配额和账单预算。
3. 在云侧为 API Key 设置项目、IP、模型和费用上限。

### High：任务状态和访问令牌仅保存在内存

进程重启后：

- 用户无法恢复任务状态和访问令牌。
- 正在运行的 MediaKit 远端任务可能继续产生费用。
- 不支持多 worker 或多实例。

建议迁移到 SQLite/PostgreSQL + Redis 队列，持久化任务、访问令牌哈希、远端 task ID 和阶段检查点。

### High：取消本地任务不能保证取消远端 MediaKit 任务

当前取消会关闭本地协程和清理目录，但已提交的远端增强任务可能继续执行。现有实现会在提交后记录远端 task ID，便于人工排查。

建议确认 MediaKit 是否提供当前任务类型可用的取消接口；若有，应在取消和关机流程中调用。若无，应在提交前提示费用不可撤销，并设置云侧预算。

### High：频控仍是单进程、按直连 IP 的内存实现

反向代理后可能把所有用户识别为同一 IP；多实例时各实例计数独立。

建议：

- 使用 Redis 滑动窗口。
- 明确信任代理列表后再解析 Forwarded/X-Forwarded-For。
- 优先按登录用户和租户限流。

### High：媒体处理未做操作系统级沙箱

FFmpeg、ffprobe、SceneDetect 和 OpenCV 会解析不可信视频。即使做了格式与尺寸预检，底层解码器漏洞仍可能被恶意媒体触发。

建议在独立低权限容器或作业节点中运行媒体命令，启用只读根文件系统、seccomp、CPU/内存/磁盘/进程数限制和无外网策略。

### Medium：媒体令牌位于查询参数

视频和图片标签无法方便地发送自定义请求头，因此当前令牌位于 URL 查询参数。已设置 `no-referrer` 和 `no-store`，但反向代理访问日志仍可能记录完整 URL。

建议生产版改为：

- HttpOnly SameSite Cookie；或
- 短时签名媒体 URL；并
- 配置代理日志隐藏查询参数。

### Medium：长视频延迟和成本不可预测

- MediaKit 生成式增强平均 RTF 很高。
- Seed ASR 按约 200ms 音频节奏发送，长视频接近实时耗时。
- 当前仅有超时和并发上限，没有费用预估或审批。

建议在提交前探测总时长，展示预计耗时和费用，并支持“仅本地处理”“仅增强低清素材”等策略。

### Medium：全 Vision 失败时任务仍会终止

单镜头失败可降级，但全部 Vision 失败会停止任务。这保护了匹配质量，但降低了可用性。

建议提供显式的低质量降级模式，例如按时间线均匀分配镜头，并在报告中标记“无语义匹配”。

### Medium：前端没有自动化测试和运行时响应校验

当前前端只有 TypeScript 编译与生产构建，没有 Vitest/React Testing Library，也没有使用 Zod 验证后端响应。

建议补充：

- 轮询取消与报告重试测试。
- 任务令牌丢失测试。
- 上传超时、429、5xx 测试。
- Remix 状态测试。
- API 响应运行时 schema 校验。

### Medium：可观测性不足

当前只有任务文本日志，没有结构化日志、指标和 tracing。

建议增加：

- JSON 日志和 request/task/remote-task correlation ID。
- 阶段耗时、失败率、API 延迟、重试次数和费用指标。
- OpenTelemetry 和错误聚合。

### Low：依赖更新仍需持续维护

安全漏洞已经清零，但部分依赖仍有新的大版本。不要直接升级 React、Tailwind、OpenCV 等大版本；应在独立分支逐项升级并运行完整媒体回归。

## 排除的误报

审计过程中核实以下项目不是当前真实漏洞：

- Embedding 实际会校验所有向量维度及配置维度。
- 同期声 padding 不会跨句累计漂移；每句音频和画面使用同一选择范围。
- Remix 的状态检查和状态修改之间没有 `await`，单事件循环内不会出现所描述的检查后竞态。
- task.log 没有对外读取端点。
- 当前 MediaKit 多 Key 会在 401/403/429 和 5xx 时轮换，并新增了重试。

## 验证记录

- Python 编译：通过。
- 后端完整测试：53 项通过，包含十阶段 E2E、渲染、Range、CORS、取消、Remix、MediaKit、ASR 和安全测试。
- 前端 `npm run build`：通过。
- `pip check`：通过。
- `pip-audit -r requirements.txt`：0 个已知漏洞。
- `npm audit`：0 个已知漏洞。
- VS Code Problems：0 个错误。

## 建议执行顺序

1. 公网入口增加认证和用户配额。
2. 持久化任务与远端 task ID，并接入真正的队列。
3. 将媒体解析和渲染移入沙箱作业容器。
4. 增加远端任务取消/费用预算。
5. 增加前端测试、结构化日志、指标和 tracing。
6. 最后再做性能优化和大版本依赖升级。

## 凭证处置

本次会话中多个真实 API Key、App ID 和 Access Token 曾以明文出现。即使 `.env` 已被 `.gitignore` 排除，也建议完成验收后立即轮换全部凭证，并在各云控制台检查近期调用和费用。
