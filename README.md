# 金话筒 · AI 新闻视频（V2）

面向中文新闻制作的**单机 Web 应用**：输入稿件、选择自己的视频或照片，由 AI 辅助配画面与声音，再逐句检查、修改并导出 MP4。无需应用账号或登录。

当前入口是 **Workspace 工作区：创建 → 处理 → 结果工作台**，配有作品历史和只读范例。当前主界面没有 Studio 专业时间线、课堂、教师审批或独立云作业入口；保留的旧接口不代表新界面提供这些能力。

| 制作模式 | 适合什么内容 | 声音规则 |
| --- | --- | --- |
| **A · AI 配音** | 第一次体验、常规旁白新闻 | 初版使用 AI 旁白，不自动把素材声音变成同期声 |
| **B · 旁白 + 原声** | 解说与采访交替 | 显式选择原声句（`quote`），核对真实出处，其余为旁白 |
| **C · 只用原声** | 采访摘编、现场发言 | 只使用素材中的真实原话，不调用 TTS 合成配音 |

> **先了解费用与隐私：选择文件后，上传和预转写就可能调用收费 ASR，早于点击“开始制作”。** 正式生成、重试和部分修改也可能计费。稿件、抽取音频、关键帧或候选视频片段可能发送给配置的第三方服务；本地运行不等于完全离线。先取得素材、人物声音与第三方处理授权，再选文件。

## 开始前准备

- **平台与 Python：** Windows AMD64 或 Linux x86_64，CPython **3.11 / 3.12**。下面以 Windows PowerShell 5.1、Python 3.11 为例；不承诺 ARM/macOS 兼容。
- **前端工具：** Node.js **20.19+（20.x）或 22.12+**，配套 npm；命令行中需可找到 Node 和 npm。
- **媒体工具：** FFmpeg 和 ffprobe 均须在当前终端 `PATH`；FFmpeg 需要 `libx264`、AAC、`ass`（libass）、`loudnorm`、`ebur128`、`silencedetect`、`perspective`。捆绑字幕字体也会被预检校验。
- **机器容量：** 建议至少 4 核 CPU、8 GiB 内存并留足本地磁盘。中间媒体可远大于原文件；这是起步建议，不是大素材可运行或速度保证。
- **服务凭证：** 默认使用 Kimi、火山 Embedding、Seed ASR/TTS。需要相应服务权限、余额、网络和费用授权；下面的安装与预检不提交付费生成。

尚未安装这些工具、使用 Linux 或已有工作区？先看 [docs/QUICKSTART.md](docs/QUICKSTART.md)。
**没有凭证、只想看界面：** 请走快速开始的[仅查看界面](docs/QUICKSTART.md#view-only)分支，不填假 Key、不关闭功能或校验来伪造就绪。

## Windows 最短启动路径（仅全新本地开发副本）

从你已获得并确认可信的**完整源码**开始；ZIP 解压也可以，无需假定存在 Git 仓库。用 VS Code 打开项目根目录（含本页、backend、frontend），再打开 PowerShell 终端。

以下按编号逐段执行，任何一步失败就停止。只用于**自己拥有的新工作区**，不用于正在服务的目录、历史验收目录或生产升级。已有环境和作品不要删除、覆盖或套用重建流程，按运行维护指南处理。

### 1. 创建独立 Python 环境，安装开发依赖

无需激活脚本，也无需修改 PowerShell 执行策略；始终直接使用项目解释器。发现已有虚拟环境时，下面主动停止，先确认版本和用途，而不是覆盖重建。

```powershell
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath 'requirements.txt')) { throw '请先进入完整源码的项目根目录。' }
if (Test-Path -LiteralPath '.venv') { throw '已有虚拟环境：停止首次安装，先按快速开始核对复用条件。' }
py -3.11 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败。' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败。' }
```

[requirements.txt](requirements.txt)仅用于这里的开发安装，不能替代生产冻结依赖流程。pip/npm 安装可能联网下载依赖；不等于调用新闻生成服务。

### 2. 安装前端依赖，准备本地配置

仍在同一个根目录终端执行；只在本地配置尚不存在时从模板复制，绝不覆盖已有凭证。

```powershell
$ErrorActionPreference = 'Stop'
npm.cmd --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
```

由你在本机编辑刚复制的配置，保留开发模板的其他默认项。**不要把实际内容、Key 或 token 粘贴到聊天、截图、日志或前端代码。**

| 默认服务 | 需要填写的字段 | 注意 |
| --- | --- | --- |
| Kimi 画面与文本处理 | `KIMI_API_KEY` | 保留模板的 Kimi 服务和模型配置 |
| 火山 Embedding 检索 | `VOLCENGINE_VISION_API_KEYS` | Embedding 共用这个字段，没有单独的 Embedding Key 字段 |
| Seed ASR 语音识别 | `VOLCENGINE_APP_ID`、`VOLCENGINE_ACCESS_TOKEN` | 需要 ASR 资源权限，Ark Key 不能替代 |
| Seed TTS 配音 | `VOLCENGINE_TTS_API_KEYS` | 支持以可用的 APP_ID + ACCESS_TOKEN 组合后备鉴权；ASR 授权不等于 TTS 授权 |

字段细节与可选项见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md)。缺少凭证就停在这里，改走“仅查看界面”分支，不填写占位密钥。

### 3. 构建同版前端

此操作会重新生成并替换前端 dist 构建目录的内容，**仅在自己的新开发副本中有意执行**；不要覆盖其他服务正在使用的构建。

```powershell
$ErrorActionPreference = 'Stop'
npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败，停止启动。' }
```

### 4. 先预检，再启动单进程服务

预检检查配置、本地工具、字体、构建与存储等前提，会做本地目录/磁盘探测，但**不提交新闻生成、不验证真实服务密钥权限或语音质量**。必须退出成功且报告 `ready: true` 才继续制作路径。

```powershell
$ErrorActionPreference = 'Stop'
$gmReady = $false
& .\.venv\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖校验失败。' }
& .\.venv\Scripts\python.exe -m backend.preflight
if ($LASTEXITCODE -ne 0) { throw '预检未就绪：停止，按故障排查修复后再试。' }
$gmReady = $true
```

同一终端接着执行；8000 已占用时先确认已有服务，不强杀或重复启动。

```powershell
$ErrorActionPreference = 'Stop'
if ($gmReady -ne $true) { throw '请先在本终端完成预检。' }
if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) { throw '8000 已占用：请先核对已有服务。' }
& .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
if ($LASTEXITCODE -ne 0) { throw '服务异常退出，请查看故障排查。' }
```

打开 <http://127.0.0.1:8000>。前端和 API 由同一服务提供，不需要另开 Vite。这个终端保持前台运行；**仅在没有上传、生成、修改或导出作业时按 Ctrl+C 停止**，忙碌时先按运行维护指南排空。

### 5. 确认服务状态

- <http://127.0.0.1:8000/health/live>：只证明进程存活，不证明可以制作。
- <http://127.0.0.1:8000/api/config/availability>：浏览器安全的 `ready/not_ready` 状态，不包含完整运维细节。
- 完整 `/health/ready` 属于受限运维检查，不能公开暴露。`ready` 不证明第三方权限、额度或真实成片质量已验收。
- 手动开发服务可以在部分 `not_ready` 状态下启动以查看界面，**不等于可以制作**；上述正常制作流程仍在预检失败时停止，查看方式另见快速开始。

**VS Code 可选方式：** 完成安装与配置后，可依次运行 **Golden Mic: Build local frontend**、**Golden Mic: Start local server**，替代手动构建/启动，不要两种方式同时开服务。[.vscode/tasks.json](https://github.com/mizhou0817/golden_mic/blob/main/.vscode/tasks.json)中的启动任务检查端口、查找 FFmpeg、验证依赖和 preflight；空凭证不会因此通过。它在后台启动，停止方式见 [docs/RUNBOOK.md](docs/RUNBOOK.md)，不能把关闭终端当成已经停机。

## 第一条视频：建议从 A 模式开始

1. 打开工作区，选择 **A · AI 配音**。
2. 稿件**首行是标题**：1–40 字，末尾不加句末标点；空一行后填写非空正文。A/B 整份稿件至少 **20 字**、最多 **8000 字**（不是正文单独的限额），标题不播报。首次建议短稿，不用长稿测试安装。
3. 至少选择 **1 个真实视频或照片素材**；建议准备 4 段自有短镜头，内容与稿件一致。这是体验建议，不保证任意稿件都有足够画面。**选文件前**确认上传、语音转写与第三方处理授权和预算。
4. 等待上传/预转写，检查文稿、素材与提示，再明确点击开始制作。A 不会擅自把原声建议变成同期声；若以后用 B/C，须核对原话、说话人、出处和使用许可。
5. 在处理页查看实际进度；上传完成不等于生成成功。出错先查状态，不连续点击重试；重试可能再次收费。
6. 进入结果工作台，逐句核对文字、画面、声音、人名、数字和日期；有修改先**统一应用修改**，等待新版本完成，再处理**发布检查**。
7. 检查通过后导出并下载 **MP4**，完整观看确认。质量错误不能靠勾选豁免；预览可播放不等于允许发布。

**想用自己的声音？** 当前 V2 先生成 AI 配音初版，再在结果工作台按句录音或上传替换；不是创建时自动用整篇自录代替初版。录音权限、对齐和修改步骤见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。

## 作品、隐私与安全

- 历史入口用于重新打开作品，不是账号云同步。V2 任务/文件访问依赖任务凭据，本机也不能省略；不要清浏览器存储来“修复”任务，更不要分享带 token 的完整媒体 URL。
- 草稿空闲到期、作品/上传保留时间不同：V2 草稿通常为 **24 小时空闲**，终态作品 **72 小时**，暂存上传自创建起最长 **72 小时**。以服务端状态为准，及时保存获准导出的成片；浏览器缓存不是备份。
- 只监听 `127.0.0.1`，保持前端与 API **同源**、**一个 worker**。不要公开数据目录、配置或模型，不向公网开放开发端口；无登录不等于作品公开。
- 不在收费作业期间使用 `--reload`、重启或升级。重启不是自动续跑或退款；取消/失败也不保证退费。不要靠关校验、改额度、换会话或删账本绕过限制。

## 当前交付边界

以下数字仅摘自 **2026-10-03** 的[日期交付记录](docs/V2_PRODUCT_DELIVERY_20261003.md)，**不是本文重新运行的测试，也不证明你现有服务的版本或状态**：

| 记录范围 | 当日结果 |
| --- | --- |
| 后端 V2 + 选定 legacy（非全部历史测试） | 812/812 |
| 前端全 glob，dialog/final/recording 原生 opt-in 全开 | 1268/1268 |
| 独立结果工作台入口（不与上行相加） | 43/43 |
| TypeScript 四套检查 | 4/4 |
| 当前核心 / 独立设计浏览器 | 6/6、4/4 |

- 上述合成素材、模拟 Provider 与真实 FFmpeg 的验证不代表真实语音质量、事实正确性或固定处理速度；不承诺旧作品已被修复或任意输入都能成功。
- **CTC 模型尚未交付**；开发默认可选本地语音能力不阻塞普通 A 云配音路径，但不能据此宣布本地声纹/对齐已可用，更不能伪造许可确认或绕过生产模型门禁。
- **只读范例需要已批准且安装的范例包**；缺包时 503/不可用是正常边界，不承诺开箱即有样片，也不拿用户作品充当公开范例。
- **公网生产尚未批准/验收。** Linux、TLS、负载、签名、排空/回滚等仍需独立验证。环境包装器须将选项放在环境文件之前，并以显式 `--` 分隔目标命令及其参数，见 [deploy/README.md](deploy/README.md)。本轮修复后的回归/CI 需要重跑并单独报告；存在脚本或未签名包不等于可直接上线。

## 文档导航

| 你要做什么 | 从这里开始 |
| --- | --- |
| 安装、首次启动、无凭证只看界面 | [docs/QUICKSTART.md](docs/QUICKSTART.md) |
| A/B/C、录音替换、修改、历史、导出 | [docs/USER_GUIDE.md](docs/USER_GUIDE.md) |
| 填凭证、理解可选模型与限制 | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| 处理预检、上传、生成或导出错误 | [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |
| 日常启动/停止、备份与安全维护 | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| 修改代码、隔离测试与构建 | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| 查 API、当前契约与日期证据 | [docs/README.md](docs/README.md) |
| 评估 Linux 生产部署与未解除条件 | [deploy/README.md](deploy/README.md) |

## 仓库速览

GitHub `blob/main` / `tree/main` 链接指向**完整源码**，不是最小包内文件，也不是部署版本证明；`main` 会变化。新发布包按显式白名单包含当前全部 docs Markdown 与模型决策 JSON，旧包不追补。干净克隆的换行与构建要求见[开发指南](docs/DEVELOPMENT.md)。

| 目录 | 内容 |
| --- | --- |
| [frontend/](https://github.com/mizhou0817/golden_mic/tree/main/frontend) | 完整源码中的 React / TypeScript 工作区界面；最小包仅含已构建资产 |
| [backend/](backend/) | Python / FastAPI、任务与媒体处理 |
| [tests/](https://github.com/mizhou0817/golden_mic/tree/main/tests) | 完整源码中的测试与受保护验证入口；先读开发指南，勿直接对真实数据运行 |
| [docs/](docs/) | 使用文档、接口与日期交付记录 |
| [deploy/](deploy/) | 发布、校验和运维脚本，不代表生产已就绪 |
| [canary_test/](https://github.com/mizhou0817/golden_mic/tree/main/canary_test) | 完整源码中的历史验收记录；本地 artifacts 未发布，不作为当前在线状态 |

旧报告按日期保留；当前使用方式以本页和文档导航为起点，不把历史课堂、Studio 或旧测试数量当作 V2 的当前承诺。
