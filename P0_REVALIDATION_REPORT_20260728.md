# Golden Mic P0 二次全面复核报告

复核日期：2026-07-28  
范围：腾讯云 Linux 单机生产部署前 P0、部署供应链、运行可靠性及目标环境放行条件

## 结论

**仓库侧 P0 实现已达到“可进入目标 Linux/腾讯云实机验收”状态，但当前仍不能判定为“已可公网正式上线”。**

原因不是核心业务代码缺失，而是以下事实仍未在目标环境完成：

1. 当前工作目录不是 Git 仓库，缺少不可变 commit/tag、受保护分支、CI 结果与发布制品之间的可追溯链。
2. Linux CI 工作流已经补齐，但尚无本会话可核验的远端成功运行记录。
3. 目标 CVM/CBS、真实 Nginx、systemd、TLS、Basic Auth/OIDC、发布、服务宕机回滚、脚本中断恢复和日志泄漏检查尚未实机演练。
4. 腾讯云安全组、固定出口、证书续期、CLS/云监控，以及火山引擎凭证轮换、最小权限、费用预算和告警属于账号级外部动作，仓库无法代为完成。
5. 中国大陆公网域名的 ICP、隐私告知、第三方数据处理和保留/删除策略仍需业务与法务确认。

因此，当前准确状态是：

- **代码/配置/部署资产：P0 已补齐并通过 Windows 自动化验证。**
- **Linux 构建/静态验证：已编码进 CI，等待真实 CI 结果。**
- **腾讯云实机与账号级控制：未完成，不得勾选上线放行。**

## 本次复核新发现并已修复

### 1. 发布中断可能永久保持 drain

原发布脚本在开启 drain 后若收到 SIGINT/SIGTERM 或中途失败，旧服务可能继续拒绝新任务。

已修复：

- 发布脚本增加 EXIT/INT/TERM 事务恢复；
- 软链切换前失败会取消旧服务 drain；
- 软链切换后失败会恢复旧软链并重启；
- 首次发布失败会停止新服务并删除 current；
- 未提交的 release 目录会被清理。

### 2. 服务宕机时无法执行显式回滚

原回滚脚本无条件调用 drain，当前服务不可达时会立即退出。

已修复：

- 服务健康时才 drain；
- 服务 active 但不健康时执行紧急回滚；
- 服务已停止时直接切换并启动目标 release；
- 回滚失败或脚本中断自动恢复原 release；
- 回滚前验证目标 release 的 Python 环境、前端、锁文件、vendored wheel 和部署资产。

### 3. root 解压归档存在供应链与路径风险

已修复：

- 发布强制要求 SHA-256 与 Minisign Ed25519 签名；
- 公钥固定在 `/etc/golden-mic/release.pub`，检查 owner/mode；
- 归档先复制到 root-only 暂存文件，消除上传目录 TOCTOU；
- 解压前拒绝绝对路径、`..`、反斜杠、重复成员、symlink/hardlink/device、setuid/setgid、秘密目录和压缩炸弹；
- vendored SceneDetect wheel、字体、前端清单、锁文件、SBOM 和部署资产均为归档必需成员；
- venv 由非特权 `goldenmic` 用户安装，再固化为 root 只读。

### 4. 未知 Host 仍先进入应用

已修复：Nginx 增加 80/443 `default_server` sinkhole，未知 HTTP Host 返回 444，未知 TLS SNI 使用 `ssl_reject_handshake`，不进入认证和 FastAPI。

### 5. 普通 requirements 缺少哈希

已修复：

- `requirements.txt` 明确标注仅开发兼容；
- 生产只允许 `uv sync --frozen`；
- 新增与 `uv.lock` 同步的 `requirements-production.lock`，可使用 `pip --require-hashes`；
- 已在全新 Windows venv 实际完成哈希安装、唯一 headless OpenCV 和 `pip check`；
- 已交叉 dry-run 验证 Linux x86_64/Python 3.12 会解析 43 个生产包。

### 6. 字体/前端/SBOM 仅存在但缺少更强绑定

已修复：

- Noto Sans SC 字体和 OFL 均固定审计 SHA-256；
- 前端 dist 通过 `ASSET_MANIFEST.sha256` 强制校验；
- release 构建前检查字体、前端、uv lock、哈希 requirements 和 SBOM 同步；
- 新增确定性 CycloneDX 1.5 SBOM，serial/timestamp 从 `uv.lock` 和固定 epoch 生成；
- 发布包连续构建保持字节级一致。

### 7. 媒体命令可能永久卡住任务槽

已修复：

- 所有异步 FFmpeg/ffprobe 命令增加生产硬超时；
- Linux 使用独立进程组，取消/超时先 SIGTERM，10 秒后 SIGKILL；
- TTS 响度分析也统一走同一超时/取消路径；
- FFmpeg 自动添加 `-hide_banner -nostats`，降低 stderr 和 task.log 放大；
- 阶段 10 同步质量扫描移出事件循环，并给同步子进程增加 600 秒超时。

### 8. TTL 可能删除长时间运行任务

已修复：

- TTL 仅清理 done/failed/cancelled；
- 使用 processing_completed_at/updated_at，而非创建时间；
- queued/running 内存任务和 orphan state 都不会被 TTL 删除。

### 9. 服务收到 SIGTERM 会立即取消任务

已修复：

- shutdown 先进入 drain；
- 最多等待 `SHUTDOWN_GRACE_SECONDS`；
- production 建议 6900 秒，单媒体命令 6800 秒；
- Uvicorn/systemd 外层分别为 7100/7200 秒；
- 超时后才取消残余任务。

### 10. 频控可因历史 IP 填满或通过换 IP 绕过

已修复：

- 每次请求清理过期 identity；
- 认证用户同时按 user 全局和 user+IP 双层计数；
- 同一账号切换 IP 不能绕过小时配额。

### 11. 生产容量基线仍可被配置意外放宽

已修复：production Settings 硬限制：

- 总上传不超过 5120 MB；
- 源文件不超过 20；
- 总素材不超过 3600 秒；
- 最大 7680×4320、120 fps；
- 上传并发 1、处理并发 1、排队上限 5；
- 数据盘安全余量至少 50 GiB；
- 任务磁盘预留倍数至少 6，模板采用 8；
- 媒体超时不得超过 shutdown 宽限。

### 12. 生产环境模板与 Settings 可能漂移

已修复：

- production 模板精确覆盖 107 个 Settings 字段；
- 新增无 shell 求值的 EnvironmentFile 解析/执行器；
- 部署、systemd 与目标验收都验证无 missing/unknown 字段；
- production Settings 拒绝未知 Provider、示例域名、非 TLS URL、缺失启用 Provider 凭证、占位凭证、相对路径、通配 Host、公开 docs、warn QC 和旧 MediaKit 键。

### 13. 多 worker/多实例可能绕过单进程架构约束

已修复：production 生命周期在 CBS 上获取排他实例锁；第二个 worker 或实例启动失败。systemd 仍明确固定 `--workers 1`。

### 14. 目标验收步骤分散且容易漏项

已修复：新增 `deploy/validate_target_host.sh`，统一检查 CBS mount、Nginx、systemd、EnvironmentFile 权限/schema、唯一 OpenCV、production preflight、live/ready、docs 关闭、8000 loopback 和 Nginx 日志无 token。

## P0 逐项完成度

| P0 | 仓库侧 | Windows 自动化 | Linux CI 编码 | 目标腾讯云 |
| --- | --- | --- | --- | --- |
| P0-1 字体/OFL | 完成 | 内置字体真实烧录通过 | 完整测试会执行同一烧录 | 未执行目标机烧录 |
| P0-2 HTTPS/认证/配额 | 完成 | 严格 production HTTP 合约通过 | Nginx 真实 `-t`、production preflight 已加入 | 证书、认证、WAF/CLB、预算未执行 |
| P0-3 上传/媒体/磁盘 | 完成 | body/流/时长/分辨率/FPS/预留测试通过 | production preflight 已加入 | CBS 容量和真实压测未执行 |
| P0-4 Linux 运行资产 | 完成 | Bash 语法、归档、release 确定性通过 | shellcheck、systemd-analyze、nginx -t 已加入 | install/deploy/rollback/drain 未实机演练 |
| P0-5 依赖锁 | 完成 | uv/pip hash 安装、pip check、Linux dry-run 通过 | frozen sync、唯一 OpenCV、锁同步已加入 | 真实 Linux 安装待 CI/目标机 |
| P0-6 环境/凭证 | 模板与校验完成 | `.env` 旧键已移除，未输出值 | strict production schema/preflight 已加入 | 凭证轮换、Secrets Manager/权限未执行 |

## 当前可复核验证结果

- 完整 Python 测试：**157 项通过，64.355 秒**。
- 聚焦复审测试：严格 production、归档攻击防护、内置字体烧录、媒体超时、TTL、停机、频控和环境 schema 均通过。
- `pip check`：通过。
- `pip-audit -r requirements.txt`：0 个已知漏洞。
- 当前 venv：仅 `opencv-python-headless==5.0.0.93`，无 `opencv-python`，SceneDetect 0.7.1。
- `requirements-production.lock`：全新 venv `--require-hashes` 安装和检查通过。
- Linux x86_64/Python 3.12 哈希依赖 dry-run：43 个包可解析。
- 最终发布包：`dist/golden-mic-p0-20260728.tar.gz`，66 个文件，无 `.env`/data/eval_sample/node_modules/.venv，连续构建字节一致，SHA-256 `5519B8FD7051B2EA6475AD769E7156234FC9035D136A55E30822F6279787AAD0`。
- 该归档当前没有生产 `.minisig`；这是刻意保留的外部步骤，必须由隔离签名机上的真实生产私钥生成，不能在仓库或对话中伪造。
- 开发 `.env`：32 个键，MediaKit 遗留键 0；未读取/输出值。
- 前端锁：已恢复为 npm 操作前快照，SHA-256 `9E0060BCFF4A66AB67B8054D73C5D46667C7C5BFEA766CDA9F9D3BDC8C76FEBB`；本次按用户要求未继续执行 npm。
- 硬编码秘密扫描：backend/frontend/src/deploy 未发现有效秘密字面量。

## 部署前仍必须完成（阻断上线）

### A. 版本与发布治理

- 创建私有 Git 仓库并提交当前代码。
- main 启用评审、强制 CI、禁止强推。
- 用不可变 commit/tag 对应 release-id、SBOM、SHA-256 和 `.minisig`。
- 生产 Minisign 私钥在隔离签名机交互生成并加密保存；服务器只安装公钥。
- 复核 CI 固定 SHA 的第三方 Actions 后运行 Linux CI，保存成功记录。

### B. 腾讯云基础设施

- 创建/确认独立 CBS 并挂载到 `/srv/golden-mic-data`；脚本和 systemd 已强制 mountpoint。
- 安全组禁止公网 8000，SSH 仅固定运维 IP，公网仅 80/443。
- 配置固定 EIP/NAT、DNS、真实 TLS 证书和自动续期告警。
- 视暴露范围配置 CLB/WAF、DDoS 和登录失败防护。
- 配置 CLS/云监控：CPU、内存、OOM、systemd restart、CBS 容量/inode、Nginx 4xx/5xx、队列和 Provider 429/延迟。

### C. 身份、费用和第三方 Provider

- Basic Auth 仅适合受控试运行；使用 `htpasswd -B -C 12` 交互创建。
- 正式多用户应改为 OIDC/Zero Trust，并保持可信 `X-Authenticated-User` 覆盖语义。
- 轮换所有历史 Provider 凭证，生产使用最小权限。
- 限制固定出口 IP（产品支持时），设置日/月预算、模型配额、并发和异常费用告警。
- 用无敏感小样执行真实 Vision/Embedding/LLM/TTS/ASR 连通和 429 演练。

### D. 目标 Linux 实机验收

- 运行 `install_host.sh`、首次 `deploy_release.sh`、升级发布和 `rollback_release.sh`。
- 演练：健康服务排空发布、服务已宕机回滚、发布脚本 SIGINT/SIGTERM 中断恢复、错误签名/错误 SHA/恶意 tar 拒绝。
- 运行 `validate_target_host.sh`，确认 8000 仅 loopback、docs 关闭、preflight 通过、日志无 token。
- 在真实 HTTPS 入口验证未知 Host/SNI 拒绝、Basic Auth/OIDC、Origin、HSTS/CSP、Range 206 和不含 query 的 CLS/WAF/CLB/Nginx 日志。
- 使用真实素材压测 5 GiB/20 文件/60 分钟边界，确认 8 倍磁盘预留与 50 GiB 安全余量足够。

### E. 合规与数据生命周期

- 完成 ICP（适用时）、隐私告知和第三方数据处理披露。
- 明确原始视频、文稿、关键帧和音频的地域、保留 72 小时、删除和备份策略。
- 若使用 COS，启用加密、最小权限和生命周期删除；禁止把 `.env`、任务令牌或无期限原始素材纳入普通备份。

## P1：建议首轮生产前完成

1. 将同步质量扫描完全改为异步子进程或受控 worker；本次已移出事件循环，但线程取消不能强杀已经运行的同步扫描，只能依赖 600 秒 subprocess timeout。
2. 增加结构化 JSON 日志、request/task correlation ID、Prometheus/OpenTelemetry 或腾讯云指标上报。
3. 增加数据库/Redis 队列后再考虑高可用、多实例和零中断升级；当前架构仍是单机单 worker。
4. 将媒体解析/渲染迁入低权限沙箱作业容器；当前 systemd 已有限权和 cgroup 限制，但不是 per-job seccomp 容器。
5. 将任务完成后的 `norm/`、embedding clips、重复视频和中间音频按重剪依赖清单分级清理，降低长期 CBS 成本。
6. 增加前端运行时 schema 校验和自动化测试；当前本机 npm 不可用，前端制品以 SHA-256 清单锁定，但未来前端变更必须在独立可信构建机重新构建、审计和更新清单。
7. 替换 FastAPI TestClient 的 `httpx` 兼容层弃用路径（当前依赖提示迁移 `httpx2`），避免未来测试框架升级阻断。
8. 为 Basic Auth 增加 fail2ban/WAF 登录失败限速；或直接切换企业身份代理。
9. 对第三方 Provider 做数据出境/地域与跨云网络延迟、带宽和可用性基准测试。

## 最终放行规则

只有以下三列都完成，才可把 P0 判定为“上线完成”：

1. **仓库实现通过**：代码、测试、锁、签名、SBOM、release 制品通过。
2. **目标 Linux 实测通过**：CI 和 CVM 的 Nginx/systemd/preflight/render/deploy/rollback/HTTPS/log 验收通过。
3. **云账号/合规完成**：安全组、证书、身份、密钥轮换、费用预算、监控、备案和隐私审批完成。

当前只完成第 1 列；第 2 列已提供自动化但未执行完毕；第 3 列必须由有权限操作者完成。
