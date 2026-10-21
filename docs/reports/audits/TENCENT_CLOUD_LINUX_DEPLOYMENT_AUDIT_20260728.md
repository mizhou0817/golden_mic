# Golden Mic 腾讯云 Linux 部署前全面审计

审计日期：2026-07-28  
目标：腾讯云单台 Linux CVM 上的受保护生产部署  
结论等级：**仓库内 P0 技术项已完成；腾讯云账号、域名、证书、密钥轮换与预算等外部 P0 项完成前仍不得开放公网。**

## 0. P0 实施更新（2026-07-28）

> 二次复核已发现并修复发布/回滚 drain 异常、归档签名与安全解压、媒体超时、TTL、优雅停机、Nginx 默认 Host、生产哈希锁、SBOM 等问题。最新状态与剩余上线阻断项以 `P0_REVALIDATION_REPORT_20260728.md` 为准；本文件后续旧数字只保留为历史审计记录。

| P0 | 仓库实施状态 | 仍需目标环境完成 |
| --- | --- | --- |
| P0-1 字体 | 已内置官方 Noto Sans SC、OFL、SHA-256；启动预检强制验证；发布制品已包含 | Linux CI/目标机中文烧字复验 |
| P0-2 认证与 HTTPS | 已提供 Nginx TLS 1.2/1.3、Basic Auth、认证用户注入、Origin 防护、Host 白名单、按用户+IP 配额、文档关闭、无 query 日志 | 安全组、真实证书、交互创建密码、WAF/CLB、Provider 预算和 ICP/合规 |
| P0-3 资源护栏 | 已实现总 body/总文件量、并发上传、单/总时长、宽高、帧率、磁盘余量及完整任务生命周期 8 倍空间预留 | 真实 CBS 容量压测和告警 |
| P0-4 Linux 资产 | 已提供 live/ready/drain、完整 preflight、systemd、Nginx、主机安装、版本化发布、自动回滚、显式回滚、Linux CI 和确定性最小归档 | 在目标 Linux 执行 `nginx -t`、systemd/发布/回滚演练 |
| P0-5 依赖 | 已生成 `uv.lock`；只安装 `opencv-python-headless==5.0.0.93`；SceneDetect wheel 经过上游 SHA-256 验证并重建 RECORD | Linux CI 冻结安装复验 |
| P0-6 配置与凭证 | 已清除当前 `.env` 的 12 个 MediaKit 遗留键；提供完整 production 模板、权限和占位/TLS/遗留键硬校验 | 在云控制台轮换/吊销真实凭证，并将新值写入受保护 EnvironmentFile/Secrets Manager |

全部外部操作已列入 `deploy/TENCENT_CLOUD_P0_EXTERNAL_CHECKLIST.md`；生产实施步骤见 `deploy/README.md`。仓库无法代替有权限的操作者执行腾讯云安全组、证书、域名备案、Provider 密钥轮换或费用预算操作。

## 1. 执行结论

项目核心流水线已经具备较好的功能完整性、任务级访问令牌、输入白名单、Provider 超时重试、原子状态写入、质量门禁和完整测试。当前代码可在单进程、单 worker、本地磁盘模式下运行。

基线审计发现了以下六类阻断问题；上述 P0 实施已完成仓库侧修复：

1. Linux 必需的 Noto Sans SC 字体没有随项目提供，现有 Windows 测试依赖系统字体回退；Linux 渲染会失败。
2. 匿名任务创建会直接产生第三方 AI 费用，应用自身没有用户认证、用户配额或 CSRF 防护，不能裸露公网。
3. 默认上传上限允许单请求理论上传约 48.828 GiB，且没有总上传量、总时长、最大源分辨率和磁盘余量保护。
4. 没有 Nginx/systemd/健康检查/优雅排空/部署回滚等生产运行资产。
5. Python 依赖同时安装 `opencv-python` 与 `opencv-python-headless`，实际导入版本不是显式固定的 headless 版本；Linux 构建不够确定。
6. 当前环境配置仍有已移除 MediaKit 的遗留变量，关键生产参数大量依赖代码默认值，密钥管理和配置漂移需要清理。

本项目在保持当前架构时只能部署为：

- 单机；
- 单 Uvicorn worker；
- 本地持久数据盘；
- 有计划停机更新；
- 不承诺正在处理任务的重启续跑；
- 不具备多实例高可用。

## 2. 实际验证结果

### 2.1 后端

| 检查 | 结果 |
| --- | --- |
| Python 当前解释器 | 3.11.9 venv |
| `compileall backend tests` | 通过 |
| P0 实施后完整测试 | 145 项通过，54.228 秒 |
| `pip check` | 通过 |
| 全新 Python 3.11 环境安装与完整测试 | 134 项通过 |
| 全新 Python 3.12 环境安装与完整测试 | 134 项通过，约 33 秒 |
| `pip-audit -r requirements.txt` | 0 个已知漏洞 |
| 冻结 uv 环境 | 46 个解析包；唯一 OpenCV 为 headless 5.0.0.93；SceneDetect 0.7.1 |
| 完整 preflight | 前端 SHA、字体/OFL、数据目录、磁盘、FFmpeg 能力、Provider 结构全部通过 |
| Pylance 导入解析 | 无 unresolved import |
| Provider/FFmpeg 本地配置预检 | 通过；该预检不调用真实云接口 |

测试运行中存在两个非阻断弃用告警：

- FastAPI TestClient 的 `httpx` 兼容层弃用告警；
- SceneDetect `get_seconds()` 弃用告警。

修复后 Pylance 严格模式共报告 762 条诊断，其中后端 265 条。多数来自第三方库类型信息不足、Pydantic 动态字段和测试 JSON 类型未知，不等同于运行失败；但其中确实发现了 `ReportRow` 未声明 `overlay_kind`/`overlay_text`、导致报告字段被 Pydantic 静默丢弃的问题。本次审计已修复并增加回归断言。后续应建立可执行的 Python 静态检查基线，而不是长期忽略全部诊断。

### 2.2 前端

| 检查 | 结果 |
| --- | --- |
| `npm ci` | 通过 |
| TypeScript `strict` typecheck | 通过 |
| Vite 生产构建 | 通过，1773 个模块 |
| JS 产物 | 176.95 kB，gzip 57.13 kB |
| CSS 产物 | 15.05 kB，gzip 4.17 kB |
| `npm audit --omit=dev` | 0 个已知漏洞 |
| `npm audit` | 0 个已知漏洞 |

### 2.3 媒体环境

当前 Windows FFmpeg 8.1.2 已验证包含：

- `libx264`；
- AAC；
- `libass`/`ass`；
- `loudnorm`；
- `ebur128`；
- `silencedetect`。

这只能证明当前机器可用，不能证明目标 Linux 镜像的 FFmpeg 功能一致。目标服务器必须重复执行编码器、滤镜和真实烧字冒烟测试。

### 2.4 服务冒烟

临时单 worker Uvicorn 服务可正常启动：

- `/` 返回 200；
- `/openapi.json` 返回 200；
- `/docs` 返回 200；
- `/healthz` 返回 404，说明尚无健康检查端点；
- HTML/API 响应没有 CSP 或 HSTS；HSTS 应由 HTTPS 入口配置。

此前本机端口 8000 启动退出码 1 的直接原因是已有 Python 进程 PID 54420 正在监听 `0.0.0.0:8000`，不是本次代码导入失败。

P0 实施后另用隔离数据目录启动严格 `APP_ENV=production` 冒烟：live/ready 为 200；`/docs` 和 `/openapi.json` 为 404；未认证 API 为 401；未知 Host 为 400；drain 后 ready 为 503，取消 drain 后恢复 200；6,000,000,000 字节声明请求在正文解析前返回 413；HSTS 与 CSP 均存在。该测试使用不会发起真实云请求的结构占位配置，只验证启动与 HTTP 安全路径。

当时发布归档的文件数和 SHA-256 已被后续二次复核修改取代；当前制品信息见 `P0_REVALIDATION_REPORT_20260728.md` 和同目录 `.sha256` 文件。

### 2.5 当前资源与配置事实

在隐藏全部凭证值的前提下，读取到的有效参数为：

- `MAX_UPLOAD_MB=500`；
- `MAX_FILES=100`；
- 理论单请求文件总量：约 48.828 GiB；
- `MAX_CONCURRENT_TASKS=2`；
- `MAX_PENDING_TASKS=20`；
- `ASR_CONCURRENCY=4`，与示例推荐值 2 不一致；
- `TASK_TTL_HOURS=0`，不自动清理；
- `QUALITY_GATE_MODE=warn`；
- CORS 仍使用 localhost 默认来源。

本地数据现状：

- 84 个任务目录；
- `data/` 约 2.03 GiB；
- `eval_sample/` 约 1.70 GiB；
- `.venv/` 约 0.31 GiB；
- 最大单任务约 0.49 GiB，其中规格化视频、原始视频、Embedding 代理、片段、`video_only.mp4` 和 `final.mp4` 会同时存在。

部署包必须排除本地任务、评测样例、虚拟环境和 `node_modules`，只携带源码、锁文件、已构建前端和获许可字体。

## 3. P0：部署前必须完成

### P0-1：随部署制品提供 Noto Sans SC 字体和许可证

**状态：仓库侧已完成。** 官方字体、OFL、来源和 SHA-256 已随制品提供，preflight 会校验字体签名、体积和许可证；目标 Linux 烧字复验仍属于上线前外部验收。

**证据**

- `backend/assets/fonts/` 当前只有 `.gitkeep`；
- `backend/rendering.py` 在项目字体缺失时只搜索 Windows 字体目录；
- Linux 不会命中该回退，字幕烧录将抛出 `RenderingError`。

**必须修改**

1. 将明确版本的 `NotoSansSC*.ttf`、`.otf` 或 `.ttc` 放入 `backend/assets/fonts/`；文件名必须以规范化后的 `notosanssc` 开头。
2. 一并保存字体版本、SHA-256 和 SIL Open Font License 文本。
3. 在 Linux CI/目标机执行带中文、标题、双行字幕的真实烧录测试。
4. 启动预检/就绪检查必须验证字体文件，而不是等到阶段 9 才失败。

**验收**：目标机不依赖系统字体也能通过 `RenderingSmokeTest`，成片抽帧能看到正确中文字体。

### P0-2：公网入口必须有身份认证、HTTPS 和费用配额

**状态：仓库侧已完成，账号级操作待目标环境执行。** Nginx 模板强制 TLS/Basic Auth 并覆盖 `X-Authenticated-User`；应用 production 模式要求认证用户和可信 Origin，按认证用户+可信 IP 限额，并关闭 API 文档。安全组、真实证书、预算、凭证权限与合规必须按外部清单人工完成。

**证据**

- `POST /api/tasks` 匿名可用；
- 每任务令牌只能保护已创建任务，不能限制创建者；
- 每个任务会触发 Vision、Embedding、LLM、TTS、ASR 等计费调用；
- 当前只按内存 IP 计数，每小时 5 次，不能抵御多 IP 攻击，也不是用户配额；
- Basic Auth 若单独使用，还应防止已认证浏览器被跨站表单触发昂贵任务。

**必须修改**

1. 安全组仅开放 80/443，禁止公网访问 8000。
2. 入口使用腾讯云 CLB/WAF 或本机 Nginx，配置 TLS 1.2/1.3 和自动证书续期。
3. 优先使用 OIDC/企业身份代理；内网试运行至少使用 VPN 或 Basic Auth。
4. 对状态修改请求校验可信 `Origin`，或使用带 SameSite/CSRF 防护的认证代理。
5. 在代理层增加按认证用户和 IP 的上传速率、并发、频率限制。
6. 在火山引擎侧限制 API Key 权限、来源 IP、模型范围和预算告警。
7. `/docs` 与 `/openapi.json` 应关闭或置于同一认证后。
8. 若使用中国大陆公网域名，确认 ICP 备案、隐私告知和第三方数据处理合规要求。

**验收**：未认证请求在读取大请求体前即被拒绝；单用户配额、费用预算和告警可验证。

### P0-3：增加总上传量、媒体时长、分辨率和磁盘护栏

**状态：已完成。** 请求在 multipart 解析前校验 `Content-Length`，上传流和文件合计再次校验；ffprobe 在任何云调用前校验单/总时长、宽高和帧率；数据盘按请求体 8 倍预留中间媒体空间并保持到原始任务结束，取消、失败或完成后释放。

**证据**

- 应用只限制每个文件 500 MiB 和文件数 100；
- 理论单请求可达约 48.828 GiB；
- FastAPI 在进入路由函数前已解析 multipart，`UploadFile` 大文件会先写临时目录；之后应用再复制到任务目录；
- 若 Nginx 也启用请求缓冲，可能同时占用 Nginx 临时文件、Python multipart 临时文件和任务原始文件；
- 源媒体只验证存在正尺寸视频流，没有最大宽高、总时长、帧率或解码复杂度限制；
- 没有提交前磁盘余量检查，也没有每任务磁盘配额。

**必须修改**

新增并强制执行：

- `MAX_TOTAL_UPLOAD_MB`；
- `MAX_SOURCE_DURATION_SECONDS_PER_FILE`；
- `MAX_TOTAL_SOURCE_DURATION_SECONDS`；
- `MAX_SOURCE_WIDTH`/`MAX_SOURCE_HEIGHT`；
- `MIN_FREE_DISK_GB`；
- 可选的每用户每日素材分钟数与任务数。

同时：

1. Nginx `client_max_body_size` 必须与应用总量上限一致。
2. 将 `TMPDIR`、Nginx `client_body_temp_path` 和 `DATA_DIR` 放到独立腾讯云 CBS 数据盘，而非根盘。
3. 上传前/创建任务前预留容量；任务失败、取消和解析失败后回收临时文件。
4. 初始生产值建议为总上传 2–5 GiB、最多 20 个文件、总素材 60 分钟；经压测后再放宽。
5. 若坚持保留 100×500 MiB 上限，单并发任务应预留至少数百 GiB，可用磁盘需按临时副本与 1080p 中间文件重新压测，不能使用普通 50 GiB 根盘。

**验收**：超总量、超时长、超分辨率或低磁盘请求在任何云 API 调用前失败；磁盘压测不会写满根分区。

### P0-4：补齐 Linux 生产运行资产

**状态：仓库侧已完成。** `deploy/` 已包含 Nginx、systemd、生产环境模板、主机初始化、确定性最小制品、preflight、排空发布、自动回滚和显式回滚；`.github/workflows/linux-ci.yml` 提供目标 Linux 静态与完整回归。仍需在目标 CVM 执行实机验收。

当前仓库没有：

- Dockerfile/Compose；
- systemd unit；
- Nginx 配置；
- 健康检查端点；
- 部署/回滚脚本；
- CI 工作流；
- 生产预检脚本。

至少需要创建并评审：

1. `systemd` 服务：专用低权限用户、固定工作目录、单 worker、自动重启、合理的停止超时、`KillMode=control-group`、文件句柄和资源限制。
2. Nginx：TLS、认证、总请求大小、上传超时、真实客户端 IP、Range、静态缓存和无查询字符串日志。
3. `/health/live`：仅证明进程/事件循环存活。
4. `/health/ready`：验证未处于 drain、前端产物、数据盘可写与余量、FFmpeg/ffprobe、所需编码器/滤镜、字体和配置结构。
5. `preflight`：在服务切换流量前执行，不应通过真实昂贵生成任务才能发现配置错误。
6. 版本化发布目录与 `current` 软链接，实现快速回滚；数据目录必须独立于发布目录。

### P0-5：解决 OpenCV 双包和依赖锁定问题

**状态：已完成。** 当前 venv 和冻结 uv 环境均只含 `opencv-python-headless==5.0.0.93`，不含 `opencv-python`；`cv2==5.0.0`，SceneDetect 0.7.1。生产归档只部署已构建并通过 SHA-256 清单校验的 `frontend/dist`，不依赖服务器 npm 构建。

**证据**

- `requirements.txt` 同时声明 SceneDetect 与 `opencv-python-headless==4.11.0.86`；
- SceneDetect 0.7.1 的硬依赖是 `opencv-python`；
- 全新环境实际安装了 `opencv-python 5.0.0.93` 和 `opencv-python-headless 4.11.0.86`；
- `import cv2` 实际返回 5.0.0，而不是显式声明的 4.11 headless 版本；
- 两个分发包写入同一 `cv2` 命名空间，安装顺序可能影响结果。

**必须修改**

1. 选择唯一 OpenCV 分发包；Linux 服务优先 headless。
2. 使用支持依赖覆盖的锁定方式，或将项目打包并明确 SceneDetect 的依赖处理，避免两个 OpenCV 包共存。
3. 生成针对 Linux x86_64/Python 3.11 或 3.12 的完整传递依赖锁和哈希。
4. 在 Linux 干净镜像中断言只安装一个 OpenCV 分发包，并运行 134 项测试。
5. 前端锁文件当前 134 个 `resolved` URL 指向 Microsoft `ms-feed-*.pkgs.visualstudio.com`；生产构建应使用组织明确允许的公网/国内镜像，或在 CI 构建后只部署 `dist/`。

**验收**：干净 Linux 环境中 `pip check` 通过、只有一个 OpenCV 分发包、`cv2.__version__` 与锁文件一致。

### P0-6：重建生产环境配置并轮换凭证

**状态：仓库配置已完成，云凭证轮换待有权限操作者执行。** 当前开发 `.env` 的 12 个遗留 MediaKit 键已安全删除；production 模板显式列出安全、存储、并发、Provider 和质量参数，应用与发布脚本拒绝占位凭证、非 TLS URL、相对数据目录、通配 Host、零 TTL、公开文档、warn 质量模式和 MediaKit 遗留键。

实际环境键集合仍包含 12 个已经从代码移除的 MediaKit 变量，代码因 `extra="ignore"` 会静默忽略它们。与此同时，ASR 并发、任务并发、视频 Embedding、VAD、质量门禁和 CORS 等大量参数未显式写入，依赖代码默认值。

**必须修改**

1. 不复制当前开发环境文件到服务器；基于最新模板新建生产配置。
2. 删除全部 MediaKit 遗留变量；若其中仍有有效凭证，立即在云控制台轮换/吊销。
3. 生产值必须显式填写，不依赖默认值，尤其是数据路径、TTL、并发、上传限制、质量门禁和来源域名。
4. 凭证存入腾讯云 Secrets Manager，或 `/etc/golden-mic/golden-mic.env`，权限建议 root:service 640；禁止进入镜像、发布包、日志、备份和 shell history。
5. 服务运行用户对源码只读，仅对数据盘和指定临时目录可写。
6. 上线前再次轮换历史上曾以明文使用过的 Provider 凭证，并检查近期账单与调用记录。

## 4. P1：首轮生产前强烈建议完成

### P1-1：为 FFmpeg/ffprobe 增加超时、进程组取消和资源限制

`backend/media.py::run_logged_command()` 没有命令超时；取消时只调用 `terminate()` 后无限等待。`backend/tts_pipeline.py::_run_capture_stderr()` 没有取消清理。异常媒体或 FFmpeg 卡死可能永久占用任务槽；进程退出时还可能留下子进程。

建议：

- 每类媒体命令按输入时长设置硬超时和总任务预算；
- Linux 下为每个命令创建独立进程组；取消先发 SIGTERM，超时后 SIGKILL；
- systemd 使用 `KillMode=control-group`；
- 设置 FFmpeg `-threads` 或 systemd CPUQuota，避免两个任务各自占满全部核心；
- 对 stderr 和 task.log 做大小上限/轮转。

### P1-2：把质量检测移出事件循环

`backend/quality.py` 在异步流水线阶段 10 中直接调用同步 `subprocess.run()`：

- 全片时长探测；
- 静音扫描；
- 全片 EBU R128；
- 每个 TTS 句子的 EBU R128。

长片会在阶段 10 阻塞 FastAPI 事件循环，期间状态轮询、取消、下载和其他任务 API 都可能停顿。应改为异步子进程或 `asyncio.to_thread()`，增加并发上限与超时；更优方案是复用已有响度测量，避免对成片逐句重复解码。

### P1-3：修正清理策略和磁盘生命周期

当前 `TASK_TTL_HOURS=0` 永不自动清理。若改为非零，现有清理逻辑按任务 `created_at` 判断，并会删除满足年龄条件的运行中任务；不适合很短 TTL。

建议：

- 只自动清理 `done`/`failed`/`cancelled`；
- 按 `processing_completed_at` 或最近访问时间计算保留期；
- 运行中任务永不由 TTL 清理；
- 设置高/低水位磁盘清理；
- 原始素材、中间文件、最终成片采用不同保留期；
- 默认生产 TTL 建议 24–72 小时，并明确用户删除能力和隐私告知。

### P1-4：增加 drain 和可控重启

当前收到停机时会取消后台任务；启动后会把遗留 `queued`/`running` 标为失败，不支持断点续跑。

上线流程必须：

1. 进入 drain，立即拒绝新建/重剪/换镜；
2. readiness 返回失败，入口停止分配新流量；
3. 等待活动任务完成或达到运维超时；
4. 备份状态并停止服务；
5. 启动新版本、预检、切流；
6. 失败时回滚代码，但不回滚/覆盖数据目录。

如果业务要求无损更新、横向扩容或高可用，必须迁移到 PostgreSQL/Redis + 外部 worker 队列；不能通过增加 Uvicorn worker 解决。

### P1-5：修正代理后的真实 IP 与限流实现

`_client_ip()` 只使用 `request.client.host`。若 Uvicorn 不可信任代理头，所有用户会被识别为 Nginx 的 127.0.0.1，五次后全站新建任务被限流；若无条件信任外部 `X-Forwarded-For`，客户端又可伪造 IP 绕过限制。

要求：

- Uvicorn 只监听 `127.0.0.1:8000`；
- 只信任本机 Nginx/指定 CLB 地址；
- Nginx 覆盖而不是透传客户端提供的转发头；
- 代理层执行主限流，应用内限流作为第二层；
- 将限流键改为认证用户 + 可信客户端 IP；
- 定期删除过期 IP 键。当前字典达到 10,000 个不同来源后不会自动释放空键，新来源会持续收到 429，直到重启。

### P1-6：可观测性与告警

当前主要是每任务文本日志和少量 `print()`，缺少统一结构化日志、指标和 tracing。

至少增加：

- 不含查询字符串的 request ID、task ID、revision、stage；
- 队列长度、活动任务、阶段耗时、失败率；
- FFmpeg 退出码/超时/OOM；
- Provider P50/P95、重试、429、费用估算；
- CPU、内存、根盘/数据盘/tmp 使用率、inode；
- 腾讯云 CLS 收集与云监控告警；
- 日志保留和脱敏回归测试。

### P1-7：安全响应头与 API 表面

应用已有 `nosniff`、`DENY`、`no-referrer`、Permissions-Policy 和 API `no-store`，是正面项。仍建议在 HTTPS 入口增加：

- Content-Security-Policy；
- Strict-Transport-Security；
- `Cross-Origin-Opener-Policy`；
- 合理的静态资源缓存策略；
- Host allowlist/default server 拒绝未知 Host；
- 禁止缓存 `/api/`、尤其是带任务令牌的媒体 URL。

媒体令牌位于查询参数，因此 Uvicorn 已改为示例使用 `--no-access-log`；Nginx/CLB/WAF/CLS 日志也必须去掉 query string。长期建议改为短时签名 URL 或 HttpOnly SameSite Cookie。

## 5. P2：后续质量与性能优化

1. **版本控制与 CI**：当前目录不是 Git 仓库，无法可靠审计版本、生成差异或回滚。应建立私有仓库、受保护分支、版本标签和 SBOM。
2. **Linux CI**：在目标基础镜像运行后端 134 项测试、前端构建、pip/npm audit、FFmpeg 能力检查和中文烧字测试。
3. **Python 静态检查**：建立 pyright/Pylance 可执行配置，先处理真实参数/schema 问题，再逐步降低 unknown 噪声。
4. **前端自动化测试**：补充上传超时、轮询取消、令牌丢失、历史恢复、429/5xx、重剪/换镜状态和运行时响应 schema 校验。
5. **前端生产制品**：CI 构建 `dist/`，服务器不安装 Node/npm；为 HTML 设 no-cache，为带 hash 资源设一年 immutable。
6. **媒体下载优化**：可在应用鉴权后使用 Nginx `X-Accel-Redirect` 发送大文件，减少 Python 进程传输开销；必须保持任务令牌鉴权和 no-store。
7. **磁盘优化**：任务完成后按策略删除 `norm/`、`embedding_clips/`、临时视频和中间音频，只保留重剪真正需要的产物；需要先确认重剪/换镜依赖清单。
8. **跨云网络**：腾讯云到火山引擎会产生跨云延迟和公网流量；在目标地域实测 DNS、TLS、HTTPS、WebSocket、P95 和费用，并配置固定 EIP/NAT。
9. **灾备**：代码与数据分离。若要备份，只备份业务需要的成片/报告/状态，使用加密 COS 和生命周期规则；原始素材是否备份必须服从隐私策略。
10. **依赖更新**：修复 TestClient 与 SceneDetect 弃用告警；定期重建锁文件和 SBOM，不在生产机上临时升级。

## 6. 推荐的腾讯云单机拓扑

```text
用户
  -> 腾讯云 DNS / HTTPS
  -> 可选 WAF 或 CLB
  -> OIDC/VPN/认证入口
  -> Nginx :443
       -> /assets 静态文件
       -> /api -> 127.0.0.1:8000
  -> systemd: Uvicorn 单 worker
  -> 独立 CBS 数据盘: tasks / cache / tmp / nginx-body
  -> 火山引擎 HTTPS/WSS Provider
```

### 推荐起步规格

在增加 2–5 GiB 总上传上限、`MAX_CONCURRENT_TASKS=1` 后：

- x86_64 CVM；
- 8 vCPU / 16 GiB 内存起步；
- 200 GiB 高性能云硬盘起步；
- 独立数据盘，监控 70%/85% 水位；
- 固定公网出口，入站仅 80/443；
- Debian 12 + Python 3.11，或 Ubuntu 24.04 + Python 3.12（3.12 已在本次 Windows 干净环境通过完整测试，但仍需 Linux CI）。

若保留并发 2、长素材或接近当前理论上传上限，应至少重新进行 CPU、内存、临时盘和数据盘容量压测；不能直接套用上述规格。

## 7. 建议生产参数基线

以下是试运行建议，不含任何凭证：

```dotenv
DATA_DIR=/srv/golden-mic-data/tasks
ASR_CACHE_DIR=/srv/golden-mic-data/cache/asr
TASK_TTL_HOURS=72

MAX_UPLOAD_MB=500
MAX_FILES=20
MAX_TOTAL_UPLOAD_MB=5120
MAX_TOTAL_SOURCE_DURATION_SECONDS=3600
MIN_FREE_DISK_GB=50

MAX_CONCURRENT_TASKS=1
MAX_PENDING_TASKS=5
ASR_CONCURRENCY=2
VIDEO_EMBEDDING_CONCURRENCY=4
VIDEO_EMBEDDING_GLOBAL_CONCURRENCY=4
VIDEO_EMBEDDING_ADAPTIVE_CONCURRENCY=false

QUALITY_GATE_MODE=block
FRONTEND_ORIGINS=https://your-production-domain.example
```

注意：`MAX_TOTAL_UPLOAD_MB`、`MAX_TOTAL_SOURCE_DURATION_SECONDS` 和 `MIN_FREE_DISK_GB` 当前尚未实现，必须先增加代码和测试，不能仅写入环境文件。

## 8. Nginx/systemd 必须满足的关键点

### Nginx

- 只向本机 `127.0.0.1:8000` 反向代理；
- 覆盖 `X-Forwarded-For` 为可信远端地址；
- 认证与限流发生在大请求体进入应用前；
- 设置总 body 大小、上传超时和数据盘临时目录；
- access log 使用 `$uri` 等不含 `$args` 的字段，禁止 `$request`/`$request_uri` 泄露 token；
- 透传 `Range`/`If-Range`，不缓存 `/api/`；
- HTML no-cache，hash 静态资源 immutable；
- TLS/HSTS/CSP/Host 校验；
- 关闭目录浏览与不必要方法。

### systemd

- 专用 `goldenmic` 用户，无登录 shell；
- `WorkingDirectory` 固定到发布目录；
- `EnvironmentFile` 位于 `/etc/golden-mic/`；
- `ExecStart` 使用 venv 的绝对 Python 路径；
- `--workers 1 --no-access-log --host 127.0.0.1`；
- `Restart=on-failure`，但部署前先 drain；
- `KillMode=control-group`；
- `NoNewPrivileges=true`、受限写目录、合理 `LimitNOFILE`；
- `TMPDIR` 指向数据盘；
- 停止超时覆盖最长允许任务，超时后杀死整个 cgroup。

## 9. 推荐实施顺序

1. 立即备份并轮换密钥，清理 MediaKit 遗留变量。
2. 增加字体及许可证，建立 Linux 烧字测试。
3. 增加总上传/时长/分辨率/磁盘护栏。
4. 解决 OpenCV 双包并生成 Linux 锁文件。
5. 新增 health/readiness/drain/preflight。
6. 把质量扫描和媒体命令改为可超时、可取消、非阻塞实现。
7. 创建 Nginx、systemd、部署与回滚资产。
8. 在 Linux CI 运行完整检查并生成版本化制品/SBOM。
9. 创建腾讯云 CVM、CBS、安全组、TLS、认证、监控和预算告警。
10. 先用 `MAX_CONCURRENT_TASKS=1` 做真实素材灰度压测。
11. 验证重启中断语义、删除、TTL、Range、磁盘高水位和 Provider 429。
12. 完成隐私/数据处理告知后再开放正式用户。

## 10. 上线放行标准

只有全部满足后才建议切正式流量：

- [ ] Linux 干净环境 134 项后端测试通过；
- [ ] 前端 typecheck/build/audit 通过；
- [ ] pip audit、pip check 通过；
- [ ] 仅有一个 OpenCV 分发包；
- [ ] Noto Sans SC 随制品提供，中文烧字实测通过；
- [ ] 未认证、超配额和超 body 请求在上传前被拒绝；
- [ ] 数据盘/tmp 空间和高水位保护实测通过；
- [ ] `/health/live`、`/health/ready` 和 drain 实测通过；
- [ ] 8000 不对公网开放；
- [ ] HTTPS、认证、CSRF/Origin、Host、CSP/HSTS 配置通过；
- [ ] 所有访问日志确认不记录任务 token/query string；
- [ ] SIGTERM/取消能在超时后清理 FFmpeg 进程组；
- [ ] 更新流程不会误中断未排空任务；
- [ ] Provider 真实连通、配额、429、预算和告警验证通过；
- [ ] 监控 CPU、内存、磁盘、队列、任务失败和第三方延迟；
- [ ] 回滚演练通过，数据目录不随代码回滚；
- [ ] 隐私告知、数据保留和删除策略完成评审。

## 11. 本次审计已直接修复

1. `ReportRow` 增加 `overlay_kind` 和 `overlay_text`，避免报告/API 静默丢弃事实文字叠层字段。
2. 报告生成测试增加上述字段的持久化断言。
3. README 生产启动命令增加 `--no-access-log`，并明确反向代理日志不得记录查询参数。

其余 P0/P1 项涉及生产策略、容量、域名、认证方式、服务器路径和运维基础设施，应按本报告单独实施并在目标 Linux 环境验收。
