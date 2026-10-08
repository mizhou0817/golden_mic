# 金话筒 V2：Linux 单机部署维护指南

本页按当前源码说明部署流程，**不是当前在线版本或生产验收报告**。[2026-10-03 本地开发交付记录](../docs/V2_PRODUCT_DELIVERY_20261003.md)仅证明该日期、该源码与受限环境中的结果；其中 TEMP 未签名包不是可直接上线的生产制品，后写文档也不属于那个包。不得从历史测试数量、构建模块数或本机服务记录推断目标机状态。

当前入口是 V2 A/B/C 创建、处理、结果工作台和作品历史；不新增 Studio UI，不恢复课堂、账号登录或独立云作业。开发防护基线见 [DEVELOPMENT.md](../docs/DEVELOPMENT.md)，配置与故障处理见 [CONFIGURATION.md](../docs/CONFIGURATION.md)、[TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)、[RUNBOOK.md](../docs/RUNBOOK.md)。新发布包按显式白名单包含当前全部 docs Markdown 和模型决策 JSON，包括索引、六篇入门/维护指南与最新交付页；不追补旧包。

**上线前仍须完成独立验证：** 环境包装器的调用协议见第 7 节：选项在环境文件之前，显式 `--` 后才是目标命令及其参数。修复后的回归和 Linux CI 必须重跑、单独报告，静态/参数回归不等于目标机发布验收。必需本地语音包和真实生产验收仍未完成。下面展示脚本的设计流程，不是可忽略阻塞直接复制运行的批准。

**本文命令均为后续授权操作说明。** 本次文档修订只读取文档与相关源码，未执行命令、安装、应用构建、产品回归、预检、服务、网络请求或部署，未读取实际环境、数据库、模型、媒体或本地验收产物。Linux/Windows 受保护 V2 入口的前提与逐项平台排除见开发指南；Windows 结果不能被改名当成 Linux 验收，也不能用无防护全量 discovery 代替。

GitHub `blob/main` / `tree/main` 链接指向**完整公开源码**，不是最小包内文件，也不证明已部署版本；`main` 会变化。包内 README/docs/deploy/backend/vendor/冻结依赖等保持相对链接。源码 commit、构建绑定和实际归档成员需在每次新发布时核验。

## 1. 架构、权限与持久状态

- [systemd 服务](systemd/golden-mic.service)固定单 Uvicorn worker，监听 `127.0.0.1:8000`，启用 `--no-access-log`，只信任来自 `127.0.0.1` 的代理头。Nginx 是唯一公网入口，HTTP 跳转 HTTPS；安全组不开放 8000。
- [站点模板](nginx/golden-mic.conf.template)直接服务 `/assets/`，其余页面/API 代理到应用；不要添加可把 API 错误伪装成首页的通用 SPA fallback。默认 Host/SNI 拒绝；`/health/live` 是最小公开存活探测，`/health/ready` 经 Nginx 仅本机可达，`/api/admin/` 经 Nginx 返回 404。
- 生产始终为**签名匿名会话 + CSRF + Origin + 单任务 capability**。`GET /api/session` 建立安全匿名 cookie；任务写入还要求 `X-CSRF-Token` 和允许的 Origin。会话不是任意作品访问权；没有 Basic Auth、应用账号或可切换登录模式。
- 私有 API 使用 `X-Task-Token`，媒体可使用 `token` 查询参数。[任务授权](../backend/main.py)允许单个且相同的 header/query 配对，拒绝同一通道重复或两通道冲突。**V2 任务及其文件 API 即使在本地回环也必须有有效任务令牌**；浏览器历史视图不能授予其他浏览器的草稿/上传权限。
- legacy 的受信任本地访问是另一条兼容规则：非生产、直接 loopback 客户端与 Host、无转发头，写入另需精确 Origin；显式错误令牌不能回退。不得把这条规则推广到 V2 或公网。生产 `local_history:false`，全局 `GET /api/tasks?offset&limit` 即使从生产回环请求也返回 404；按匿名所有者作用域的历史也不是全局索引。
- 单 worker 仍然必要：运行任务、锁和调度有进程内状态，生产数据盘另有排他实例锁。但**不是所有准入/额度都在内存**：[V2 admission](../backend/admission.py)使用独立 SQLite accepted-start 账本与 tombstone，不使用旧课堂数据库。accepted start 不因后续失败退款；RetrySame 复用已接受记录，不新增接受额度，不延长小时窗口。这不是 Provider 账单系统，重试仍可能有实际费用，必须显式授权。
- 模板小时额度为会话 2、IP 5、全站 10；V2 持久计数与 legacy 兼容计数共同约束准入。重启不应成为绕过预算的手段，仍需 Provider/WAF 外部预算和告警。恢复持久任务记录不意味着自动续跑付费生成；中断处理按恢复规则失败关闭。

## 2. 上线前置条件与依赖

由操作者完成 CVM、独立 CBS 挂载、DNS、TLS、密钥轮换、预算、合规和监控审批。建议容量起点为 8 vCPU / 16 GiB / 200 GiB，但这不是压测承诺。安全组仅向目标用户开放 80/443，22 仅固定运维 IP；设置磁盘/inode、CPU/内存、服务重启、5xx 和费用告警，独立备份数据与受限配置。

- Linux 锁目标是 **x86_64、CPython 3.11 或 3.12**；不是 ARM 或任意 Python 的兼容声明。[pyproject.toml](../pyproject.toml)要求 uv `>=0.11.11,<0.12`；重现当前导出/SBOM 的逐字节比较应使用 **uv 0.11.11** 和相同 `SOURCE_DATE_EPOCH` 策略，不要把可接受的版本范围误当作输出字节稳定保证。
- 目标机预先安装 Python/venv、同一套真实 FFmpeg/ffprobe、Nginx、Minisign、uv、curl、tar 和脚本所列 Linux 工具；安装器只配置主机，不安装这些依赖。Ubuntu 24.04/Python 3.12 是可选部署基线，不是已验收结论。构建机另外需要与 [frontend/package-lock.json](https://github.com/mizhou0817/golden_mic/blob/main/frontend/package-lock.json)兼容的 Node/npm。
- 基础环境只使用 [uv.lock](../uv.lock)冻结安装：`uv sync --frozen --no-dev`。[requirements-production.lock](../requirements-production.lock)是同锁的带哈希备用导出；[requirements.txt](../requirements.txt)不是生产部署脚本的安装输入。不得重写锁、生产导出或 [sbom.cdx.json](../sbom.cdx.json)只为让比较变绿。
- [环境验证器](verify_python_environment.py)使用安装元数据逐项检查依赖，并检查 `opencv-python-headless==5.0.0.93`、SceneDetect 0.7.1 与 `cv2` 版本，拒绝 GUI OpenCV。**uv 新建 venv 不保证含 pip**，不要在未确认安装 pip 时附加 `python -m pip check`；脚本实际执行的是此验证器，不以 pip 为前提。
- FFmpeg 必需 `libx264`、AAC、`ass`、`loudnorm`、`ebur128`、`silencedetect`、`perspective`；字体及 OFL 摘要同样会校验。缺能力必须修复供应链/环境，不能跳过预检。
- npm/uv 依赖获取可能联网；构建器内部还调用 `uv lock --check`、冻结导出和 SBOM 生成。必须事先批准下载来源/网络范围，或备齐可信缓存并禁用网络。`--frozen` 不等于离线，Python 层拦截也不等于 OS 沙箱。

## 3. 先解除真实前置阻塞，不能伪造 ready

[生产模板](golden-mic.env.production.example)为保守的 `QUALITY_GATE_MODE=block`；当前 [Settings](../backend/config.py)也接受 `warn`，**不能再声称 production 校验器必定拒绝 warn**。[发布门禁](../backend/publication.py)独立检查当前修订的报告、QC、确认和来源：真实错误不能被勾选豁免，缺失/损坏 QC 失败关闭。调整质量模式不等于获得发布权。

模板的 `LOCAL_SPEECH_REQUIRED=true` 是生产要求；Settings 默认仍为 false。模板同时保留 `LOCAL_SPEECH_LICENSE_REVIEWED=false` 和空清单，表示尚未完成前置条件，不是可直接启动的成品配置。必需生产模式检查：

1. `sherpa_onnx`、`torch`、`transformers` 的可发现性与配置路径/许可条件；这些不是当前基础 uv 依赖自动提供的完整 Linux 语音栈。Windows 专用声纹锁不能装到 Linux。Linux 原生依赖、权重与兼容性需要另行审核的隔离供应链方案，部署脚本没有自动补装步骤。
2. `LOCAL_SPEECH_BUNDLE_MANIFEST_PATH` 必须显式提供绝对路径；空值不是工作目录，不会自动发现清单。`LOCAL_SPEAKER_MODEL_PATH` 和 `LOCAL_ALIGNMENT_MODEL_PATH` 必须精确绑定同一外置 bundle 的 speaker/model.onnx 与 ctc 子目录；清单可在包根的 installed-manifest.json 或外置位置。
3. [模型包验证器](verify_local_speech_bundle.py)检查规范绝对路径、无软/硬链接、允许清单、schema、大小、SHA-256，以及受限的标准 Wav2Vec2ForCTC safetensors/tokenizer 布局；[运行时桥接](../backend/local_speech_bundle.py)每次所需预检重新校验，而不是复用一张旧回执。
4. 运行时许可证、声纹权重许可证、CTC 权重/词表/语言使用权分别审核。仅在真实审核后确认相应声明；**禁止把许可标志改 true、把 required 改 false 或伪造 manifest 来“修好 preflight”**。哈希只是完整性，不证明来源真实性、转换等价、推理兼容或语音质量；`inference_verified:false` 不能被读作推理通过。

模型安装到发布目录和 HTTP 服务目录之外、服务身份可只读访问的私有路径，例如模板指定的数据盘外模型根；确保每层父目录可遍历，不能依赖 root 的访问权限。安装器不创建/下载这个 bundle，也不安装已批准公开范例。[模型决策](../docs/V2_MODEL_SELECTION_20261002.md)和[日期交付记录](../docs/V2_PRODUCT_DELIVERY_20261003.md)中的 CTC 未交付、完整语音包未就绪、公开范例未安装仍是明确边界；不能因为准备/验证工具存在而声称制品已附带可用模型或样片。

## 4. 在独立干净 checkout 构建

使用可追溯、经审批的不可变版本，**自己拥有的独立干净 checkout**，不借用正在编辑/运行服务的工作树或旧构建。工作树和构建进程中不得有实际 dotenv、Provider 凭证、用户数据/媒体或生产挂载；临时目录、缓存和出站规则在隔离构建环境中预先配置。构建期间所有输入必须冻结，包括本页等会打包的文档。

使用含本轮 Git 属性换行规则的干净克隆：shell、[uv.lock](../uv.lock)、[requirements-production.lock](../requirements-production.lock)、[sbom.cdx.json](../sbom.cdx.json)固定 LF，其余源码及前端绑定输入保留提交原始字节，不自动转换已有 CRLF。不要在旧工作树批量 renormalize 或重新签署历史绑定来“修复”CRLF 差异；在新副本重建并比较，详见[开发指南](../docs/DEVELOPMENT.md)。

以下 Bash 示例要求操作者先设置 `GM_SOURCE`（可信 checkout 的规范绝对路径）、`GM_OUTPUT_ROOT`（已存在且在源码树之外的自有绝对输出目录）和唯一 `GM_RELEASE_ID`（字母/数字/点/下划线/连字符）。未设置会停止；不要把示例域名或尖括号字串当真实参数。不覆盖旧输出、失败证据或 release ID。

```bash
set -euo pipefail
: "${GM_SOURCE:?set an absolute clean checkout path}"
: "${GM_OUTPUT_ROOT:?set an absolute output directory outside source}"
: "${GM_RELEASE_ID:?set a new approved release ID}"
[[ "$GM_SOURCE" = /* && "$GM_OUTPUT_ROOT" = /* ]]
[[ "$GM_RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]]
[[ "$GM_RELEASE_ID" != . && "$GM_RELEASE_ID" != .. ]]
GM_SOURCE=$(readlink -f -- "$GM_SOURCE")
GM_OUTPUT_ROOT=$(readlink -f -- "$GM_OUTPUT_ROOT")
test -d "$GM_SOURCE"
test -d "$GM_OUTPUT_ROOT"
case "$GM_OUTPUT_ROOT/" in "$GM_SOURCE/"*) exit 1 ;; esac
GM_ARCHIVE="$GM_OUTPUT_ROOT/golden-mic-$GM_RELEASE_ID.tar.gz"
for GM_PATH in "$GM_ARCHIVE" "$GM_ARCHIVE.sha256" "$GM_ARCHIVE.minisig"; do
  test ! -e "$GM_PATH"
  test ! -L "$GM_PATH"
done
cd "$GM_SOURCE"
uv lock --check
uv sync --frozen --no-dev
.venv/bin/python deploy/verify_python_environment.py
test ! -e frontend/dist
test ! -L frontend/dist
cd frontend
npm ci
npm run build
cd "$GM_SOURCE"
.venv/bin/python deploy/build_release.py --output "$GM_ARCHIVE"
```

这不是测试命令清单。先完成 [DEVELOPMENT.md](../docs/DEVELOPMENT.md)说明的受保护 V2 回归及经审核的 Linux 隔离 CI，再批准生产构建。Linux 回归要求已安装的真实配对 ffmpeg/ffprobe，不下载模型；平台限定排除逐项另列，不隐式跳过。新修复的实际结果另行报告，不在本文宣称 CI 绿色。不要在真实工作树运行无防护全套测试或导入启动钩子。

构建细节以 [build_release.py](build_release.py)、完整源码中的[前端构建入口](https://github.com/mizhou0817/golden_mic/blob/main/frontend/scripts/write-manifest.mjs)和[绑定校验器](frontend_binding.py)为准：

- 必须在 frontend 工作目录执行 `npm ci` 后 `npm run build`。它先类型检查，再在同进程 Vite 构建前后绑定源码/锁/配置/模式规则和资产；禁用 Vite dotenv/public 复制。不要单独跑 Vite 后补签资产摘要，也不要用 manifest-only 调用给陈旧源码重新签绑定。
- `--frontend-dir` 可选；默认选择标准 dist。只接受已有的规范直接子目录 `frontend/dist` 或 `frontend/dist-canary-安全标签`（标签首位字母/数字，后续仅字母/数字/下划线/连字符，总长不超过 64），也可用对应规范绝对路径；不接受别名、点路径、链接、任意外部目录或尾斜杠。独立构建可在 frontend 中用 `npm run build -- --out-dir "$GM_FRONTEND_LEAF"`，其中变量须预设为新的合法 `dist-canary-` 叶名；打包时加 `--frontend-dir "frontend/$GM_FRONTEND_LEAF"`。这是替代标准 dist 的分支，不要复用旧标签。
- `--output` 现在必填。本指南强制传源码树外的绝对路径；构建器还拒绝树内输出（包括根目录 dist）、路径链接、已有归档或校验和。失败后保留证据，换新输出，不删除旧包重试。
- 打包器先检查绑定，再导入 runtime readiness、验证字体/前端，执行 uv 检查、临时冻结导出与 SBOM 字节比较；打包后再核对绑定和实际 tar。它**不是无 I/O 的静态格式化工具**，会读源码/字体/锁、创建临时文件与归档、启动子进程；runtime 导入可能受环境影响，不能接触实际环境。完整 `backend.preflight` 还会创建目录和写读删除磁盘探针、调用 FFmpeg，并按 required 策略读取模型包；不要把它作为本次文档检查。
- 成功生成归档和同名 `.sha256` 校验和，**不会生成 Minisign 签名**。冻结导出/SBOM 不一致时保留错误并核对工具版本、输入和审核流程，不能重锁或改清单掩盖差异。

### 包内边界

[build_release.py](build_release.py)的 `INCLUDED_FILES` / `INCLUDED_DIRECTORIES` 是唯一打包范围权威，不能由本页扩大白名单。新包递归包含 backend、选定前端资产（映射为标准 dist）、vendor、deploy；另外显式包含根 README、冻结依赖/SBOM、**当前全部 docs/*.md** 和 [docs/v2-model-selection-20261002.json](../docs/v2-model-selection-20261002.json)。文档按文件名逐项列入，不是递归打包整个 docs 或在打包时通配吸收任意新文件。

显式新增文档包含 [docs/README.md](../docs/README.md)、[docs/QUICKSTART.md](../docs/QUICKSTART.md)、[docs/USER_GUIDE.md](../docs/USER_GUIDE.md)、[docs/CONFIGURATION.md](../docs/CONFIGURATION.md)、[docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md)、[docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md)及[最新交付页](../docs/V2_PRODUCT_DELIVERY_20261003.md)；[docs/RUNBOOK.md](../docs/RUNBOOK.md)继续包含。后续新增文件仍须同步审核白名单与归档验证，不以本页代替实际 tar 成员核验。

不包含 tests、前端源码/开发配置/e2e、VS Code 配置、node_modules、开发 venv、实际 dotenv、任务数据、评测媒体、canary 本地证据或外置模型包；根开发 dotenv 示例也不在白名单。部署生产示例模板则随 deploy 目录打包，不能与实际秘密混淆。所有新增指南和最新交付页只随**重新构建的新包**发布，不追补旧日期报告中的归档、计数或哈希。最小归档不是可直接重新构建前端的完整源码 checkout；发布时保留独立同版源码与审计材料，不复制真实数据填补文档链接。

## 5. 独立签名与可信引导

SHA-256 可检查传输完整性，**不能单独建立发布者真实性**。由受保护渠道核验公钥指纹/来源，签名人员独立核对审批、源码、归档摘要和构建证据。私钥只放隔离签名机，不进入仓库、生产主机、聊天、CI 日志或归档；加密私钥口令只在本机交互输入。

在签名机预设归档、公钥、私钥的绝对路径以及同一 release ID。此处使用已由组织安全生成并保管的密钥，不在部署时临时生成一把自我信任的密钥：

```bash
set -euo pipefail
: "${GM_ARCHIVE:?set the approved archive path}"
: "${GM_RELEASE_PUBLIC_KEY:?set the independently trusted public key path}"
: "${GM_SIGNING_KEY:?set the private key path on the signer only}"
: "${GM_RELEASE_ID:?set the approved release ID}"
(cd "$(dirname -- "$GM_ARCHIVE")"; sha256sum --check "$(basename -- "$GM_ARCHIVE").sha256")
test ! -e "$GM_ARCHIVE.minisig"
test ! -L "$GM_ARCHIVE.minisig"
minisign -Sm "$GM_ARCHIVE" -s "$GM_SIGNING_KEY" \
  -x "$GM_ARCHIVE.minisig" -t "Golden Mic production release $GM_RELEASE_ID"
minisign -Vm "$GM_ARCHIVE" -x "$GM_ARCHIVE.minisig" -p "$GM_RELEASE_PUBLIC_KEY"
```

目标机首次安装器和 tar 校验器自身必须来自**已验证可信源码或已验签且经可信校验器检查的归档**。不能先以 root 解压未经验证的 tar，再运行其中的脚本去“证明它可信”。源码获取、可信公钥分发、签名归档传输属于独立受控步骤；本页不下载或部署日期记录中的 TEMP 未签名包。

用独立可信源码中的验证器做手工复核，仅验证、不解压、不部署；先在目标机设置 `GM_BOOTSTRAP_SOURCE`、`GM_ARCHIVE`、`GM_RELEASE_PUBLIC_KEY`：

```bash
set -euo pipefail
: "${GM_BOOTSTRAP_SOURCE:?set the independently verified source path}"
: "${GM_ARCHIVE:?set the transferred signed archive path}"
: "${GM_RELEASE_PUBLIC_KEY:?set the independently trusted public key path}"
(cd "$(dirname -- "$GM_ARCHIVE")"; sha256sum --check "$(basename -- "$GM_ARCHIVE").sha256")
minisign -Vm "$GM_ARCHIVE" -x "$GM_ARCHIVE.minisig" -p "$GM_RELEASE_PUBLIC_KEY"
python3 "$GM_BOOTSTRAP_SOURCE/deploy/verify_release_archive.py" "$GM_ARCHIVE"
```

[归档验证器](verify_release_archive.py)拒绝越界/非规范路径、重复成员、链接/设备、setuid/setgid、禁止目录和超限包，并验证必需成员；当前构建器要求 frontend binding，独立验证器仍接受满足当前必需成员的旧 receipt-free schema，已有 receipt 从不忽略。验证通过不代表模型/语音/目标机已验收，也不授权部署。

## 6. TLS、主机初始化与配置迁移

脚本**不申请或续期证书**；ACME challenge 目录不是证书自动化。先安装有效证书与私钥、配置续期/告警，确保 Nginx 可读，且独立 CBS 已挂载到 `/srv/golden-mic-data`。替换真实域名与绝对证书路径；`news.example.com`、`CHANGE_ME` 等均不能作为生产值。

确认初始化窗口后，从可信引导源运行。`GM_DOMAIN` 为真实 DNS 名，其他变量为已验证存在的绝对路径：

```bash
set -euo pipefail
: "${GM_BOOTSTRAP_SOURCE:?set verified bootstrap source}"
: "${GM_DOMAIN:?set the real production domain}"
: "${GM_CERTIFICATE:?set the installed certificate path}"
: "${GM_CERTIFICATE_KEY:?set the installed TLS private key path}"
: "${GM_RELEASE_PUBLIC_KEY:?set the trusted release public key path}"
sudo bash "$GM_BOOTSTRAP_SOURCE/deploy/install_host.sh" \
  --domain "$GM_DOMAIN" \
  --certificate "$GM_CERTIFICATE" \
  --certificate-key "$GM_CERTIFICATE_KEY" \
  --release-public-key "$GM_RELEASE_PUBLIC_KEY"
```

[安装器](install_host.sh)检查 root、命令、域名、非空证书/公钥、公钥格式和挂载点；公钥格式检查不替代来源认证。随后创建非登录 OS 身份 `goldenmic`、release/config/data/cache/tmp 目录；仅在不存在时安装生产环境模板，复制受信公钥；先在临时 Nginx 配置中校验，再安装共享代理/站点/systemd，删除默认站点链接，运行 `nginx -t`、daemon-reload、enable 和 Nginx reload（失败则 restart）。它启用 golden-mic 服务但**不完成应用发布启动**；不是只读操作，也不保证出错后完整撤销主机配置。

由授权操作者在受控终端维护系统 EnvironmentFile（`/etc/golden-mic/golden-mic.env`）。要求 `root:goldenmic 0640`；发布公钥为 `root:root 0644` 或 `0444`。安装器保留已存在的配置，不代替逐版本迁移：

- 对照该 release 的 [Settings](../backend/config.py)与[生产模板](golden-mic.env.production.example)覆盖全部字段。[精确 schema 验证器](validate_environment_file.py)拒绝缺失、未知、重复键，再用 `Settings(_env_file=None)` 验证 production 值；不要直接复制开发 dotenv 或靠默认值补字段。
- 移除 `PUBLIC_ACCESS_MODE`、退役 `CLOUD_*`、`VIDEO_PROCESSING_PROVIDER`、`VOLCENGINE_MEDIAKIT_*` 旧键；不要发明新开关绕过。旧 `--access-mode` / `--htpasswd-file` 已不支持。清理旧 Nginx/include/CDN 登录挑战，不移除 TLS、Host、Origin、IP 转发和限流保护。
- 替换全部占位凭据/域名，未启用的可选凭据留空。设置真实 `ALLOWED_HOSTS`（保留维护所需 loopback Host）、HTTPS `FRONTEND_ORIGINS`，直接在服务器安全生成至少 32 随机字节的匿名签名密钥；不在聊天、shell history 或日志中传秘密。Provider 凭据经轮换、最小权限、预算和出站限制审批后注入。
- 保留 Kimi/火山 Embedding、Seed ASR/TTS 的实际启用配置；`GENERATIVE_FILL_ENABLED=false` 不因部署而开启。云 Provider 可接收音频/关键帧等请求材料，本地部署不代表所有内容永不出机。对补拍或真实生成必须另行授权。
- DATA_DIR、ASR_CACHE_DIR 和 TMPDIR 使用可写绝对路径，并按预检要求位于同一数据文件系统；systemd 仅允许该数据盘写入，HOME 为不可用目录。模型路径另按上一节只读权限配置。数据、缓存、模型和生产配置都不能放入可公开静态目录。
- [代理配置](nginx/golden-mic-proxy.conf)覆盖 `X-Forwarded-For` 为 `$remote_addr`。若增加 CLB/CDN，另行审核精确可信 real-IP 网段；不能信任公网提交的转发头，也不能把所有用户无意折叠成一个代理 IP 额度。
- 旧课堂持久状态的 `local_only` 不变，副本继承隔离；不公开旧任务、不铸造恢复令牌、不自动放行，不清空或迁移旧数据库。备份/保留策略需数据所有者审批，本页没有真实历史数据数量或完整散列承诺。

生产还校验 API 文档关闭、Origin 检查开启、非零 TTL、显式 Host/HTTPS Origin、绝对数据路径、磁盘余量、并发/容量约束及 Provider 配置。[run_with_environment.py](run_with_environment.py)解析环境文件而不执行 shell；它继承调用环境再覆盖，**不是秘密隔离沙箱**。发布脚本和 systemd 都会读取实际配置并做完整预检，只能在授权维护窗口运行。

## 7. 发布顺序与失败恢复

预设 `GM_BOOTSTRAP_SOURCE`、已签名的 `GM_ARCHIVE` 和从未使用的 `GM_RELEASE_ID`。同名失败证据也不复用；保留独立发布登记。`--release-id` 可选但此处显式给出；`--drain-timeout` 必须为正整数，默认 7200 秒。

```bash
set -euo pipefail
: "${GM_BOOTSTRAP_SOURCE:?set verified deployment scripts}"
: "${GM_ARCHIVE:?set the signed archive path}"
: "${GM_RELEASE_ID:?set a new approved release ID}"
[[ "$GM_RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]]
[[ "$GM_RELEASE_ID" != . && "$GM_RELEASE_ID" != .. ]]
test ! -e "/opt/golden-mic/releases/$GM_RELEASE_ID"
test ! -L "/opt/golden-mic/releases/$GM_RELEASE_ID"
sudo bash "$GM_BOOTSTRAP_SOURCE/deploy/deploy_release.sh" \
  --archive "$GM_ARCHIVE" --checksum "$GM_ARCHIVE.sha256" \
  --signature "$GM_ARCHIVE.minisig" --release-id "$GM_RELEASE_ID" \
  --drain-timeout 7200
```

[deploy_release.sh](deploy_release.sh)当前安排的顺序如下，不应缩写成“先停旧服务再安装”；完整可执行性仍须经过 Linux 隔离验证，并遵循下述包装器调用协议：

1. 校验参数/工具/CBS 挂载；把归档复制到 root-only 临时文件；先校验 SHA-256，再用已安装公钥验证 Minisign。
2. 检查系统环境文件占位符/权限和公钥属主/权限；拒绝已存在目标，创建新 release 目录；使用**调用脚本旁的可信归档验证器**检查 tar，之后才解压，检查必要文件。
3. 以 `goldenmic` 身份在新 release 创建 venv，使用目标机 `python3` 与 `uv sync --frozen --no-dev --python ... --project ...` 安装。此阶段可能联网，不会安装缺失的外置语音模型栈。
4. 以服务身份执行新 release 的 Python 环境验证、精确 EnvironmentFile 验证和带目标 `PYTHONPATH`、数据盘 TMPDIR 的完整 preflight；之后将 release 树收回 `root:root` 并去掉 group/other 写权限。**这些步骤在排空旧服务之前**，但会进行数据盘探针和所需模型读取，不是零 I/O。
5. 记录旧 current 链接；仅当服务 active 时 POST drain，每 5 秒读取 `active_tasks`，直到归零或超时。活动数还含导出/任务操作/删除，不只是 pipeline running。
6. 用 `ln -sfn` 更新 current，daemon-reload、restart；systemd 的 `ExecStartPre` 再验证环境与完整 preflight。最多 60 次、间隔 2 秒检查 readiness，成功后 reload Nginx，才提交发布成功。脚本未实现整个发布/数据的事务原子性，不能把链接切换描述为断电安全事务。
7. 排空超时会尝试 DELETE drain 并中止，不主动强杀任务。失败/中断 trap 尝试恢复旧链接/服务、取消未切换旧服务的 drain，并清理本次新 release；首次发布无旧版本时停止服务并移除 current。自动恢复中的部分命令是 best-effort，**脚本报“restored”不证明旧服务已健康**，须再次验证。不会以删除真实任务数据解决失败。

**环境包装器调用协议：** [run_with_environment.py](run_with_environment.py)的选项 `--cwd` / `--set` 必须放在环境文件位置参数**之前**；环境文件后必须有显式 `--`，其后是目标可执行程序及全部目标参数。目标参数即使名为 `--cwd` 或 `--set` 也属于目标命令，不能再被包装器解释。[部署调用](deploy_release.sh)与[目标机检查调用](validate_target_host.sh)应使用同一协议。缺少分隔符或旧顺序应修正调用，不绕过包装器、环境 schema 或完整 preflight。此规则的静态/合成参数回归只验证解析、转发与拒绝边界；不代表已读取真实 EnvironmentFile、运行目标 preflight 或完成 Linux 发布/回滚。修复后的实际验证另行报告，本文不新增通过计数。

### 共用服务器上的 Docker 部署（宝塔 / 已有其他项目）

当前线上（101.33.210.33，https://goldmic.misuntech.com）用这种方式，和其他项目完全隔离，全部放在 `/opt/goldmic/`：

| 路径 | 内容 |
| --- | --- |
| `build/` | 镜像构建上下文：`backend/`、`frontend/dist/`、`deploy/`、`vendor/`、锁文件、[Dockerfile](docker/Dockerfile) |
| `data/` | 作品、ASR 缓存、临时文件（同一文件系统；容器内 `/data`，uid 990） |
| `samples/` | 已审核范例包（只读挂载到 `backend/assets/samples`） |
| `goldmic.env` | 生产配置与密钥（root，0600；生产模式只读进程环境，不读 `.env`） |
| `docker-compose.yml` | [模板](docker/docker-compose.yml)：容器 `goldmic`，`restart: always`，只监听 `127.0.0.1:18765` |
| `nginx-proxy.conf` | 即 [golden-mic-proxy.conf](nginx/golden-mic-proxy.conf) |

nginx：宝塔的 `/www/server/panel/vhost/nginx/goldmic.misuntech.com.conf`（来自 [goldmic.nginx.conf](docker/goldmic.nginx.conf)），证书由 certbot（webroot `/opt/goldmic/acme`）签发并自动续期。

更新版本：本机 `npm --prefix frontend run build` 后，把上表的构建上下文同步到 `/opt/goldmic/build/`，然后在服务器
`cd /opt/goldmic && docker build -t goldmic:latest build && docker compose up -d`。数据和配置不受影响。
生产模式要求 HTTPS（`__Host-` 安全 Cookie 与 https Origin），不能用纯 http + IP 访问。

### 范例成片（“先看一条范例成片”）

范例不在发布包里（`verify_release_archive.py` 拒绝 `backend/assets/samples`），在主机上单独放一次即可：

1. 本机准备（已做好的范例在开发机 `backend/assets/samples/`；素材与稿件在 `examples/迎春市集范例/`）。重新制作时：用这份稿件和素材在应用里真实做一遍，然后
   `python deploy/export_sample_input.py --task-dir data/tasks/<作品id> --media-dir examples/迎春市集范例/素材 --script examples/迎春市集范例/稿件.txt --output /tmp/sample-input`，
   再 `python deploy/prepare_sample_bundle.py --source /tmp/sample-input --output /tmp/sample-bundle --rights-reviewed`（`--rights-reviewed` 表示你已确认素材可公开展示）。
2. 把 `registry.json` 和 `default/` 拷到服务器 `/opt/golden-mic/samples/`（root 所有，0755/0644，约 32 MB）。
3. 之后每次 `deploy_release.sh` 都会把它复制进新版本的 `backend/assets/samples/`；服务端每次读取都会重新核对每个文件的 SHA-256，不匹配时页面显示“暂时没有可供查看的已核验范例”，不会用其他数据代替。

### 手工排空、停止与取消排空

仅在目标主机直接回环执行，不经 Nginx，不增加公共管理接口：

```bash
curl --fail --silent --show-error --request POST http://127.0.0.1:8000/api/admin/drain
curl --fail --silent --show-error http://127.0.0.1:8000/api/admin/drain
```

确认 `active_tasks=0` 后才停止服务；未归零则继续观察或取消维护，不能直接 kill，也不要为了排空删除用户任务：

```bash
sudo systemctl stop golden-mic.service
```

若取消维护、旧服务仍在运行，以相同回环端点解除 drain 并检查 readiness：

```bash
curl --fail --silent --show-error --request DELETE http://127.0.0.1:8000/api/admin/drain
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
```

### 显式回滚

预先确认旧 release 完整、venv 可执行、共享环境仍与**旧版本 Settings**兼容；回滚脚本不恢复环境、模型、数据、Nginx 配置或数据库快照，也不重验旧归档签名。不要为了让旧版本启动而删除新数据。

```bash
set -euo pipefail
: "${GM_BOOTSTRAP_SOURCE:?set verified rollback scripts}"
: "${GM_PREVIOUS_RELEASE_ID:?set an existing verified release ID}"
[[ "$GM_PREVIOUS_RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]]
[[ "$GM_PREVIOUS_RELEASE_ID" != . && "$GM_PREVIOUS_RELEASE_ID" != .. ]]
sudo bash "$GM_BOOTSTRAP_SOURCE/deploy/rollback_release.sh" "$GM_PREVIOUS_RELEASE_ID"
```

[rollback_release.sh](rollback_release.sh)只接受一个位置参数，无 `--drain-timeout`；先检查旧 release 必需文件、Python 可执行性与依赖，再要求 current 有效（否则用部署流程恢复）。目标已 current 则退出成功。当前服务 active 且 live 时排空最多 1440 次、每次 5 秒；超时不切换，trap 尝试解除 drain。active 但 live 不通会进入紧急回滚，不等排空。切换后 restart、最多 60 次 readiness 探测，通过后 reload Nginx；失败 trap 尝试回到回滚前 current。紧急路径、超时与恢复健康必须在隔离目标环境演练，不能凭静态阅读声称通过。

## 8. 验收分层：基础设施不等于真实语音

只有实际授权的目标机才能执行下列检查；本页未执行：

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
sudo systemctl status golden-mic.service --no-pager
sudo nginx -t
sudo ss -lntp
sudo bash /opt/golden-mic/current/deploy/validate_target_host.sh
```

[目标机验证器](validate_target_host.sh)检查挂载、Nginx/systemd、权限、Python 依赖、该 release 环境 schema 和完整预检；读取本机健康/工作区配置，验证生产无全局列表、无正文创建请求被拒、文档接口关闭、日志无指定敏感查询模式、8000 的 loopback 绑定。它不提交媒体或付费生成，但**会读取实际 EnvironmentFile/日志、执行磁盘写读探针/原生程序、按 required 配置读取模型**；不是只读离线检查，也不证明公网 TLS/所有网卡绑定/所有日志链路均安全。

进一步分开记录，不能用一层结果替代另一层：

1. **目标基础设施与安全**：外网 TLS/证书链/续期；仅 loopback Uvicorn；首次页面无登录挑战；安全匿名会话；缺/错 CSRF、Origin、task token 负向测试；V2 本机无令牌仍拒绝；公网 admin 不可达；API 文档关闭；Nginx/CDN/WAF/追踪日志不泄露查询令牌。模板 access log 使用 `$uri`、Uvicorn 禁用 access log，不等于全链路已验收。
2. **隔离核心流程**：在无真实凭据/数据、Provider 被阻断的独立环境，用合成媒体验证 V2 A/B/C 创建→保存凭据→刷新/历史→结果工作台→检查/确认/导出、鉴权 Range 206、版本与源文件一致性、排空/重启/回滚、资源满载拒绝及清理失败保护。不增加 Studio UI 验收范围，不挪用日期报告的次数当本次结果。
3. **模型与真实语音**：单独审批语料的权利/隐私、模型来源/许可证/安全转换、Linux native runtime、说话人区分/CTC 对齐、真实 ASR/TTS、录音设备、听审和性能；明确调用次数/金额/时长/素材范围与停止条件后才能测试。预检、模型摘要、合成 ASR/音调 TTS、历史声纹探针均不能代替这层。

现有源码提供样例准备和模型包验证工具，**没有已安装公开样片或完整可用语音包的保证**；无范例包的 503 应保留真实含义。Linux CI、真实 TLS/负载/排空回滚演练、持续运行与断电恢复仍需独立证据；历史原生崩溃风险不会因一次绿色核心测试被宣布根治。

## 9. 当前资源与停止上限

以下是[生产模板](golden-mic.env.production.example)、[Settings](../backend/config.py)、[任务容量实现](../backend/task_manager.py)与[服务单元](systemd/golden-mic.service)的当前值，不是压测容量承诺：

| 项目 | 当前约束 |
|---|---|
| 上传 | 单文件 500 MiB；文件总量 5120 MiB；每任务最多 20 个源文件 |
| HTTP body | 应用总文件量另加每文件 1 MiB multipart 余量；Nginx `client_max_body_size 5128m`，两个边界不同，先命中的限制生效 |
| 素材 | 单文件 1800 秒、总计 3600 秒；最大 7680×4320、120 fps |
| 磁盘 | 至少 50 GiB 安全余量；预留倍数模板 8，production 校验不允许低于 6；不同操作还受各自空间预算约束 |
| 并发 | 生产模板 1 个大上传、1 个处理任务；单 worker 不代表没有并发 I/O |
| **V2 队列** | `v2_max_waiting_tasks` 为固定属性 **50 个 waiting/queued**，不含 running；不是新增可配置环境键，额度/磁盘限制可能更早拒绝 |
| legacy 队列 | `MAX_PENDING_TASKS=5` 对应 queued + running；与 V2 waiting 50 是不同准入分支 |
| 保留 | 生产模板任务 TTL 72 小时；V2 done/failed/cancelled 按 72 小时终态保留，删除/过期后的 capability-bound tombstone 保留 30 天；不是通过删除账本重置额度 |
| 媒体与停止 | 单条媒体命令 6800 秒；应用 shutdown grace 6900 秒；Uvicorn graceful 7100 秒；systemd stop 7200 秒 |
| 发布等待 | 发布 drain 默认 7200 秒；回滚固定最多 1440 × 5 秒；readiness 最多 60 次、间隔 2 秒 |

正常维护优先显式 drain，超过停止上限可能终止剩余进程，不能承诺无限等待或自动恢复未完成付费任务。调整限制必须同步审核应用、Nginx、数据盘、cgroup、Provider 配额与费用预算，不能用删真实数据、解除发布门禁或放宽模型许可来获得“可用”状态。
