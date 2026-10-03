# 快速开始：从源码到本机工作区

[项目首页](../README.md) · [文档目录](README.md) · [使用指南](USER_GUIDE.md) · [配置说明](CONFIGURATION.md) · [故障排查](TROUBLESHOOTING.md)

目标是先在自己的电脑打开同版前后端，再决定是否进行获准的真实制作。**安装依赖、能打开界面、预检 ready、实际生成成功是四件不同的事。** 下载速度、机器性能与服务开通时间不固定，本指南不承诺“几分钟生成完”。

## 1. 选择你的路径

| 你手上有什么 | 走哪条路径 |
|---|---|
| 完整源码、已开通的 AI 服务、获准的素材与预算 | 完成环境准备，按第 4 节启动，再制作第一条视频 |
| 完整源码，但没有服务凭证 | 安装、构建后走[仅查看界面](#view-only)；不选文件、不生成 |
| 已有本地环境或已有任务 | 不重复创建环境、不覆盖配置，先看[已有工作区](#existing-workspace) |
| 只有最小生产发布包 | 不按源码安装；包不含完整前端源码和测试，交给操作者按[部署指南](../deploy/README.md)处理 |

先从维护者取得**可信的完整源码**并解压到自己的本地目录，例如非同步的短路径。公开源码为 [golden_mic](https://github.com/mizhou0817/golden_mic)；没有 Git 元数据的 ZIP 也可用于本地开发，但不满足可追溯生产发布要求。确保 [pyproject.toml](../pyproject.toml)、[requirements.txt](../requirements.txt)、[frontend/package.json](https://github.com/mizhou0817/golden_mic/blob/main/frontend/package.json)、[vendor/](../vendor/)和[字体目录](../backend/assets/fonts/)完整，不只复制单个 Python 文件。

GitHub 源码链接指向可变化的 `main`，不是最小包内文件或版本证明。建议从含换行规则的新版本做干净克隆；shell、冻结锁/SBOM 固定 LF，其余源码及前端绑定输入保留提交的原始字节。不要对旧工作树批量重新规范化换行；复现要求见[开发指南](DEVELOPMENT.md)。

建议使用桌面版 Edge/Chrome。浏览器端录音需要 HTTPS 或受信任的 localhost 上下文和真实麦克风权限；普通 MP4 制作不需要麦克风。无需注册应用账户。

## 2. 安装系统工具

| 工具 | 当前要求 |
|---|---|
| Python | CPython 3.11 或 3.12；不使用 3.13+ |
| Node.js | `^20.19.0` 或 `>=22.12.0`，配套 npm 10+；版本要求来自锁定的前端工具链 |
| FFmpeg / ffprobe | 同一套安装，均在启动终端 PATH；需 H.264/AAC、libass、响度/静音检测与 perspective 滤镜 |
| 系统 | 基础锁覆盖 Windows AMD64、Linux x86_64；macOS/ARM 不在当前冻结支持矩阵 |
| 资源 | 起步建议 4 核/8 GiB；开发预检至少 5 GiB 磁盘余量，处理中另需大量空间，不是最大素材容量保证 |

### Windows（推荐首次体验）

如果尚未安装，下面命令会安装系统软件，可能出现许可或管理员提示。已有满足版本的工具不必重复安装：

```powershell
winget install --id Python.Python.3.11 --exact
winget install --id OpenJS.NodeJS.LTS --exact
winget install --id Gyan.FFmpeg --exact
```

安装后**新开一个 PowerShell**，逐项确认可执行：

```powershell
py -3.11 --version
node --version
npm.cmd --version
ffmpeg -version
ffprobe -version
```

若 FFmpeg 已由 WinGet 安装但 PATH 未生效，可在当前 PowerShell 动态定位，不要复制旧版本目录：

```powershell
$ErrorActionPreference = 'Stop'
$gmFFmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $gmFFmpeg) {
    $gmFFmpegPath = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter 'Gyan.FFmpeg*' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -File -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
    if (-not $gmFFmpegPath) { throw '未找到 WinGet FFmpeg，请先安装。' }
    $env:Path = (Split-Path -Parent $gmFFmpegPath) + ';' + $env:Path
}
Get-Command ffmpeg.exe, ffprobe.exe
```

这只修改当前终端 PATH，新终端需要重新确认。**本项目不要求放宽 PowerShell 执行策略**：使用 `npm.cmd` 和虚拟环境的 Python 可执行文件即可。

### Linux x86_64

以 Ubuntu 24.04 为例，可由操作者安装 Python 3.12/venv 与 FFmpeg：

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip ffmpeg
```

另从批准的软件源安装符合上表版本的 Node.js/npm。**不要假定发行版默认的 nodejs 包足够新。** 其他发行版使用对应包管理器，确认 Python 版本和 FFmpeg 功能，而非机械复制 Ubuntu 包名。

```bash
python3.12 --version
node --version
npm --version
ffmpeg -version
ffprobe -version
```

macOS/ARM 的依赖和原生模型兼容尚未验收；不要把 `brew install` 或 WSL 能启动当作本项目全部测试通过。受保护 V2 回归面向 Linux/Windows，须具备已安装的真实配对 FFmpeg/ffprobe，不下载模型；平台限定排除与重跑要求见[开发指南](DEVELOPMENT.md)。

## 3. 在新源码副本安装依赖

所有命令以**项目根目录**为起点。运行前先确认这个目录不在为别人提供服务、没有要保留的虚拟环境。安装可能联网下载依赖，但不执行新闻生成。

### Windows PowerShell

完整分步路径见[首页](../README.md)。以下是同一安装步骤，**不是要在首页命令之后再执行一次**：

```powershell
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath 'pyproject.toml')) { throw '当前目录不是完整项目根目录。' }
if (Test-Path -LiteralPath '.venv') { throw '已有环境，请先阅读复用说明。' }
py -3.11 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '创建环境失败。' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装 Python 依赖失败。' }
npm.cmd --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw '安装前端依赖失败。' }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
```

如使用已安装的 Python 3.12，仅把环境创建命令的 `-3.11` 改为 `-3.12`，后续仍使用项目解释器。无需激活，不在全局 Python 安装项目包。

### Linux Bash

```bash
set -euo pipefail
test -f pyproject.toml
if [ -e .venv ]; then
  printf '%s\n' 'Existing environment: review reuse instructions first.' >&2
  exit 1
fi
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix frontend ci
if [ ! -e .env ]; then cp .env.example .env; fi
```

[开发依赖入口](../requirements.txt)使用随源码提供的 SceneDetect headless wheel；不要另装 GUI OpenCV，也不要删除 vendor 目录。此 pip 路径仅供开发，生产必须使用[冻结发布流程](../deploy/README.md)。

### 填配置

在 VS Code 文件树中打开刚复制的本地配置，由你自己填写服务凭证。模板是完整源码中的[.env.example](https://github.com/mizhou0817/golden_mic/blob/main/.env.example)，不在最小生产包内；**不要直接把凭证填进模板、聊天、前端变量或提交到版本库**。

默认需要 `KIMI_API_KEY`、`VOLCENGINE_VISION_API_KEYS`（Embedding 共用）、`VOLCENGINE_APP_ID` + `VOLCENGINE_ACCESS_TOKEN`（ASR），以及 TTS 凭证/获授权的后备组合。服务开通、资源权限和计费账户彼此独立；完整说明见[配置指南](CONFIGURATION.md)。

第一次保留 `APP_ENV=development`、`LOCAL_SPEECH_REQUIRED=false`、`LOCAL_SPEECH_LICENSE_REVIEWED=false` 和生成补画面关闭等开发默认值。没有本地模型不意味着需要立刻下载；CTC/声纹供应链与许可不属于普通 A 模式安装步骤。不要填假 Key 使存在性检查通过。

## 4. 有凭证的正常启动

### Windows

在[首页](../README.md)按“构建 → pip check → preflight → Uvicorn”执行，或完成安装配置后使用 VS Code **终端 → 运行任务**：

1. **Golden Mic: Build local frontend**：替换自己工作区的标准静态构建。
2. **Golden Mic: Start local server**：检查 8000、定位 FFmpeg、检查依赖和预检，通过才后台启动。

两种启动方式二选一。任务是 Windows PowerShell 命令，不是 Linux task；后台任务打印的启动信息包含日志位置与 launcher PID。它是启动回执，不是持久健康保证。不要根据旧回执 PID 停进程。

### Linux

以下用于自己的本地开发副本，不是 systemd 生产部署。先由操作者确认 8000 无其他服务，再逐步执行：

```bash
set -euo pipefail
npm --prefix frontend run build
.venv/bin/python -m pip check
.venv/bin/python -m backend.preflight
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

任一步非零退出即停止。构建自动完成类型检查与源输入/资产绑定，**不用手工补写清单**。preflight 可能创建/检查自己的数据和缓存目录、探测磁盘/媒体工具；不提交新闻生成，也不验证真实凭证权限。正常启动还会恢复任务并执行到期清理，不能对别人数据目录试跑。

### 成功时应看到什么

打开 <http://127.0.0.1:8000>，看到 **新作品 / 我的作品** 工作区，无登录或首次教师初始化。首选此单服务地址，前端与 API 同源。

- <http://127.0.0.1:8000/health/live> 返回 `{"status":"live"}`：仅进程存活。
- <http://127.0.0.1:8000/api/config/availability> 正文为 `{"status":"ready"}`：启动前提和动态容量可接单，不是实际模型效果证明。
- 初次没有作品是正常状态；**看一条范例作品** 可能返回未安装/503，这不妨碍拥有权限和素材的正常创建。

第一次实际制作按[使用指南](USER_GUIDE.md)，只用短稿和获准素材，先批准可能的 ASR/生成费用。提交文件本身就可能触发第三方处理，不用正式“开始”按钮作费用分界。

<a id="view-only"></a>
## 5. 无凭证：仅查看开发界面

这是**显式不就绪的界面浏览路径，不是离线生成模式**。只用于自己的全新开发副本；保留空凭证和开发设置，不在运行中的工作区改数据路径。不要选择任何文件、上传录音、调用生成/重试，也不要使用测试 Provider 冒充产品配置。

先完成第 2–3 节安装，构建前端，然后直接启动开发 Uvicorn。开发应用即使 `provider_configuration` 不通过，也可提供界面；**不要使用会严格拒绝预检失败的 VS Code 启动任务**：

```powershell
$ErrorActionPreference = 'Stop'
npm.cmd --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败。' }
if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) { throw '8000 已被占用。' }
& .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Linux 对应命令（先确认端口空闲）：

```bash
set -euo pipefail
npm --prefix frontend run build
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

预期界面提示 **新制作暂不可用**，就绪接口 `not_ready`、完整 ready 503。可阅读页面和编辑本地文稿；不能据此声称可以完成视频。仍会做本地启动检查/恢复/清理，并非无 I/O 沙箱。日后填好真实配置，应空闲停止再重启，不是刷新网页就生效。

<a id="existing-workspace"></a>
## 6. 已有工作区：复用而非覆盖

- 先确认是否有运行中服务及上传/生成/导出。**不要重复 venv、复制模板覆盖秘密、运行 npm ci 清理正被使用的依赖，或覆盖共享静态构建。**
- 仅在确认环境属于本项目且没有维护冲突时，检查其 Python 版本和依赖；不要在全局解释器上执行。解释器须为 3.11/3.12。

```powershell
& .\.venv\Scripts\python.exe --version
& .\.venv\Scripts\python.exe -m pip check
```

- 源码/依赖升级、配置变化应安排维护窗口，先按[运行维护](RUNBOOK.md)排空、备份、停止自己的实例，再安装和构建。建议新的干净源码副本，不删除旧数据或历史证据。
- 前台手动服务空闲时 Ctrl+C 并等待退出；后台 VS Code 任务不会随关闭终端自动停止。归属不明、停止方式不明时不要按端口杀进程，由操作者确认进程身份再正常停止。

## 7. 下一步

- 会启动后：[制作第一条视频、原声和结果编辑](USER_GUIDE.md)。
- 配置或 ready 不正确：[配置说明](CONFIGURATION.md)和[故障排查](TROUBLESHOOTING.md)。
- 要改代码/跑测试：[开发指南](DEVELOPMENT.md)，不是对真实数据运行全量 unittest。
- 要让其他人通过公网使用：[部署指南](../deploy/README.md)，先解除已列阻塞，不能将上述开发命令绑定公网。