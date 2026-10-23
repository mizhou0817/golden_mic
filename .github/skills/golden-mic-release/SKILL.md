---
name: golden-mic-release
description: 说明金话筒的发布与回滚流程（只讲步骤和风险，不执行）：版本号与 release ID；在干净 checkout 里构建前端和最小发布包（build_release.py）；锁文件与 SBOM 核对；归档校验与 Minisign 签名；systemd 部署脚本 deploy_release.sh；排空；发布后检查；显式回滚 rollback_release.sh；文档所说"当前线上"的 Docker 路径的差异与风险；Linux CI。维护者问"怎么发布""怎么回滚""怎么排空""发布包里有什么""SBOM 怎么核对"时手动调用。它不部署、不构建、不签名、不连服务器，这些由有权限的维护者在授权窗口执行。关键词：发布 release、部署 deploy、回滚 rollback、排空 drain、SBOM、签名 minisign、build_release、Docker、systemd、CI。
disable-model-invocation: true
argument-hint: "[打包|签名|部署|排空|回滚|Docker|CI]"
---

# 金话筒发布与回滚（只讲流程）

这个 skill 只解释流程、前提和风险，**不执行**任何部署、构建、签名、排空或回滚命令。下面的命令由有权限的维护者在授权窗口里，在隔离构建机或目标机上运行。

权威说明是 [deploy/README.md](../../../deploy/README.md) 和各脚本。本文和它们不一致时，以脚本为准，并把差异告诉用户。

## 何时使用 / 不要使用

- 使用：
  - 维护者准备发布，要一份按顺序的检查清单；
  - 了解排空、回滚或 Docker 更新会影响什么；
  - 核对发布包里有什么、SBOM 和锁文件怎么核对；
  - 发布失败后，弄清脚本做到了哪一步。
- 不要使用：
  - 日常开发验证：用 golden-mic-verify；
  - 排查单个失败任务：用 golden-mic-task-debug；
  - 修改 `deploy/` 脚本或 CI：先经用户批准，并在 Linux 隔离环境验证。

## 红线（附出处）

1. **不执行任何部署操作。** 包括：
   - 不运行 `deploy/` 下的脚本，不 ssh、不连服务器；
   - 不调用 `/api/admin/drain`，不启动、停止或重启服务。

   出处：`.github/copilot-instructions.md` 红线 3；deploy/README.md 开头"本文命令均为后续授权操作说明"。
2. **不改 `deploy/`、CI、锁文件和 `sbom.cdx.json`**（红线 4）。注意两个会写文件的命令：
   - `deploy/generate_sbom.py` 不带 `--output` 时，会覆盖仓库根的 `sbom.cdx.json`；
   - `uv lock` 会改写 `uv.lock`。
3. **不在这个工作树里构建发布包：**
   - `build_release.py` 只拒绝把输出放在源码树里、覆盖已有的输出或校验和，缺文件时停止；它不检查工作树是否干净，“干净 checkout”是 deploy/README.md 第 4 节对操作者的要求；
   - `npm run build` 会替换 `frontend/dist/`，而本机服务正在用这个目录。
4. **不新建 `frontend/dist-canary-*`。** 2026-10-21 已按维护者要求全部删除，不要重建。
   - 受保护的验证运行（`tests/run_v2_validation.py`，golden-mic-verify 要求的）每次会在 `canary_test/artifacts/` 下新建它自己的证据目录（被 git 忽略），这是正常的；不要手工重建 2026-10-21 删掉的旧证据。
5. **不生成、不保存、不转述签名私钥。** 也不读真实的 EnvironmentFile、`goldmic.env` 或 `.env`。

## 版本和标识（附出处）

- **没有统一的版本号：**
  - `pyproject.toml` 是 1.0.0；
  - `backend/main.py` 里 FastAPI 的 `version="1.0.0"` 是写死的；
  - `frontend/package.json` 是 0.1.0；
  - 没有 git tag，也没有检查版本是否同步的测试。
- **线上看不到版本号。** 生产关闭了 OpenAPI 文档（`config.py`），`/health/live`、`/health/ready` 也不带版本。用 release ID 识别发布：
  - `deploy_release.sh` 默认用 `date -u +%Y%m%d-%H%M%S`，只允许字母、数字、`.`、`_`、`-`；
  - 归档名是 `golden-mic-<release ID>.tar.gz`；
  - 部署成功时打印 `release=… target=… previous=…`。

## 两条上线路径

| | systemd（deploy/README.md 第 1–9 节） | Docker（deploy/README.md"共用服务器上的 Docker 部署"；目前的线上实例用它，没做过分层验收） |
|---|---|---|
| 依赖安装 | 目标机 `uv sync --frozen --no-dev` | 镜像里 `pip install --require-hashes -r requirements-production.lock` |
| 监听 | uvicorn `127.0.0.1:8000`，带 `--proxy-headers --forwarded-allow-ips 127.0.0.1` | 容器内 `0.0.0.0:8000`，映射到宿主 `127.0.0.1:18765`；没有代理头参数，生产模式下应用直接读 nginx 覆盖过的 `X-Forwarded-For` 计算每个 IP 的额度 |
| 来源校验 | SHA-256、Minisign、归档验证器 | 没有：同步构建上下文后直接 `docker build` |
| 启动前检查 | `ExecStartPre` 跑环境校验和完整预检 | 容器启动时跑完整预检，生产模式下不通过就启动失败；`update.sh` 再等 `healthy` 和 `/health/ready`，不通过自动换回原镜像 |
| 排空 | 部署脚本先排空，默认最多等 7200 秒 | `deploy/docker/update.sh` 先排空，默认最多等 7200 秒，超时取消排空、不切换；容器已坏时可加 `--force` 跳过 |
| 停止上限 | uvicorn 7100 秒、systemd 7200 秒 | uvicorn `--timeout-graceful-shutdown 7100`、`stop_grace_period: 7200s` |
| 回滚 | `rollback_release.sh` | `update.sh --rollback`（换回 `goldmic:previous`，同样先排空） |

Docker 路径的风险：直接 `docker compose up -d`、`docker restart` 或主机重启都不会先排空。停止时应用最多等 `SHUTDOWN_GRACE_SECONDS`（模板 6900 秒），到时仍在排队或运行的任务被中止；重启后这些任务改为 failed，不会续跑，已付费的部分可能白费。所以更新一律用 `update.sh`。

`update.sh` 的其他行为（`deploy/docker/update.sh`）：

- **环境变量**：`GM_ROOT`（默认 `/opt/goldmic`，里面要有 `docker-compose.yml` 和 `build/`）、`GM_DRAIN_TIMEOUT`（默认 7200）、`GM_HEALTH_TIMEOUT`（默认 300）。`docker-compose.yml` 的 `stop_grace_period` 不是 7200s 时只警告，不阻止。
- **镜像标签**：先构建 `goldmic:next`；切换成功后，`goldmic:previous` 是切换前**正在运行的容器所用的镜像**（手工构建过 `goldmic:latest` 时，它和 `latest` 指向的可能不同），`latest` 是 `next`。回滚时切换前运行的镜像另存为 `goldmic:rolled-back`。
- **一次只能跑一个**：用 `flock` 锁 `$GM_ROOT/.update.lock`，拿不到锁就以退出码 2 退出。
- **中断或失败**：排空开始之后脚本失败或被中断（Ctrl-C、SSH 断线），而旧容器还在运行，就取消排空；容器还没被换掉时被中断，或 `docker compose up -d` 在换掉容器之前就失败，两个标签也标回原样。排空请求和每次轮询各连续失败 5 次才放弃；排空请求一直没有回应时什么都不改就退出（退出码 1），提示改用 `--force`。
- **`--force`**：容器已经坏了（崩溃循环、卡住、不回应排空请求）时跳过排空和等待，直接切换；旧容器里正在做的事会丢，compose 最多等 30 秒让它停下。要先确认里面没有值得保留的作品。
- **排空最长约 2 小时**：要在 tmux 或 screen 里运行。

## 打包（隔离的干净 checkout，维护者执行）

按 deploy/README.md 第 4 节，由维护者在 Linux 上执行。先设好 `GM_SOURCE`（可信的干净 checkout）、`GM_OUTPUT_ROOT`（源码树之外、已存在）和新的 `GM_RELEASE_ID`。**要运行那里的整块命令**，不要只摘几行：它以 `set -euo pipefail` 开头，检查三个变量都已设置、路径是绝对路径、输出目录不在源码树里、输出文件和校验和都不存在；缺了这些，前一条失败后后一条仍会继续，变量没设时 `--output` 会变成 `/golden-mic-.tar.gz`。块里的步骤依次是：

1. `uv lock --check`、`uv sync --frozen --no-dev`、`deploy/verify_python_environment.py`；
2. 确认 `frontend/dist` 不存在也不是链接；
3. 在 `frontend/` 里 `npm ci`，然后 `npm run build`；
4. 回到源码根目录运行 `deploy/build_release.py --output "$GM_ARCHIVE"`，`GM_ARCHIVE` 是块里算出的 `$GM_OUTPUT_ROOT/golden-mic-$GM_RELEASE_ID.tar.gz`。

- **`npm run build` 的内容**：先做类型检查，再构建，并写入 `ASSET_MANIFEST.sha256` 和 `MODE_BUILD_BINDING.json`。Node 要求 `^20.19.0` 或 `>=22.12.0`（docs/QUICKSTART.md），CI 用 24.18.0。
- **`build_release.py` 的检查**：
  - 打包前检查前端绑定、字体和 dist；
  - `uv lock --check`；
  - 把 `uv export` 的结果和 `requirements-production.lock` 逐字节比较；
  - 把新生成的 SBOM 和 `sbom.cdx.json` 比较；
  - tar 用固定时间戳，结果可复现；
  - 生成同名 `.sha256`，**不签名**。
- **构建失败时**：保留证据，换一个新的输出路径。不要重新生成锁文件或 SBOM 来"修好"比较。

发布包里有什么、没有什么，以及 CI 的步骤，见 [references/package-and-ci.md](references/package-and-ci.md)。

## 签名与校验

按 deploy/README.md 第 5 节：

- **签名**在隔离的签名机上，用组织保管的 Minisign 私钥手工完成。不要在部署时临时生成一把密钥来自签。
- **目标机上的检查**：先核对 `.sha256`，再用 `/etc/golden-mic/release.pub` 验签，然后用可信源码里的 `deploy/verify_release_archive.py` 检查归档。

## 部署（systemd）

按 deploy/README.md 第 7 节，由维护者运行 `deploy_release.sh`（本 skill 不运行），参数为 `--archive`、`--checksum`、`--signature`、`--release-id`、`--drain-timeout`（默认 7200）。脚本的顺序：

1. 校验 SHA-256 和签名，检查环境文件权限，用验证器检查 tar，再解压到新的 release 目录。
2. 用服务身份 `uv sync` 安装依赖，校验 Python 环境和环境文件，跑完整预检。这一步在排空之前。
3. 服务在运行时先排空：每 5 秒读一次 `active_tasks` 加 `upload_jobs`，直到归零或超时。超时就取消排空、中止发布，不强杀任务。
4. 切换 `current` 链接，daemon-reload，重启服务。readiness 最多检查 60 次，每次间隔 2 秒，通过后 reload nginx。
5. 新版本没通过 readiness 时，输出 `journalctl` 的最后 100 行，切回旧链接并重启旧服务；首次发布没有旧版本时，停止服务并删除 `current`。其他步骤失败或被中断时，由 trap 尝试恢复。**脚本报"restored"不代表旧服务已经健康**，要再检查一次。

## 排空

- **调用方式**：`POST`、`GET`、`DELETE /api/admin/drain`（`main.py`）只能在目标机上从回环地址调用，经 nginx 访问返回 404。
- **只在内存里**：排空标记不落盘，服务重启后清除。
- **拦截范围**：排空期间拦 `/api/tasks*`、`/api/uploads*`、`/api/match*`、`/api/script/assist` 的 POST、PUT、PATCH，返回 503 并带 `Retry-After: 60`；AI 写稿停用，不再调用收费的 LLM；`/api/script/effects` 照常回答，但只按关键词判断、不调用模型。DELETE 不拦。
- **快照里数什么**：`active_tasks` 是 queued、running、Studio 任务、进行中的修改操作和删除（`task_manager.py::operational_snapshot`）；`upload_jobs` 是传完后还在后台做收费预转写的素材（`UploadStore.active_jobs`，数 `uploads.py::_preprocess` 的任务，2026-10-22 起快照里才有这一项）。切换或重启会取消这些预转写（`UploadStore.close`），已付的转写费用白费，所以三个脚本（`update.sh`、`deploy_release.sh`、`rollback_release.sh`）都等两项相加归零，旧版本的服务没有这一项，按 0 算。还在传输中的分块两项都不数；排空后新的分块请求会被拒绝，所以先通知使用者停止上传。手工用 `curl` 排空时要自己看 `upload_jobs`。
- **停止上限**（生产模板）：单条媒体命令 6800 秒，应用 shutdown grace 6900 秒，uvicorn 7100 秒，systemd 7200 秒；Docker 相同（`--timeout-graceful-shutdown 7100`、`stop_grace_period: 7200s`）。
  - 代码里 shutdown grace 的默认值是 7500 秒。环境文件不设这个键时，它比 uvicorn 和 systemd 的上限还长。
- **重启的后果**：queued、running 的任务改为 failed，不续跑，所以要等 `active_tasks` 和 `upload_jobs` 都归零再停止服务。

## 回滚

按 deploy/README.md"显式回滚"：`rollback_release.sh <旧 release ID>`，只接受这一个参数。

- **它做什么：**
  - 检查旧 release 的文件、Python 和依赖。
  - 要求当前的 `current` 链接有效。目标已经是 current 时，直接成功退出。
  - 服务在线时先排空（`active_tasks` 加 `upload_jobs` 归零为止），最多 1440 × 5 秒，超时就不切换。服务 active 但 live 不通时，走紧急回滚，不等排空。
  - 切换后重启，readiness 最多检查 60 次，通过后 reload nginx。失败时尝试回到回滚前的版本。
- **它不做**：daemon-reload、重新验签、恢复环境文件、模型、数据、nginx 配置或数据库快照。
- **风险：**
  - 新版本往环境文件里加了键时，旧版本的精确 schema 校验会因"未知键"失败，服务起不来（推断，未演练）。
  - `task_state.json` 只有 version 1，没有迁移脚本，降级是否兼容没有验证。
  - `graphics.ass` 的代际标记：2026-10-22 起完成的作品是 `; template: GM-GFX-DISCLOSURE-V2-20261022`，2026-10-23 起是 `…V3-20261023`，再往后不带 AI 标识的是 `GM-GFX-NO-AI-LABEL-V4-20261023`。回滚到某一代出现之前的版本后，旧代码重新生成不出这一代的 `graphics.ass`，这些作品的导出会因为 `graphics binding changed` 返回 422。回滚前要有取舍：重新制作这些作品，或者先不回滚。
  - 部署或回滚改动了 `mode_pipeline.py`、`production_modes.py`、`mode_rules.json`、`media.py`、`graphics.py` 等文件时（完整名单见 `drafts._retry_binding`），改动前失败的任务的"再试一次"会返回 409 `retry_inputs_changed`，只能重新提交。
  - 仓库里没有备份工具；备份方法见 [docs/RUNBOOK.md](../../../docs/RUNBOOK.md) 第 4 节。

## 发布后检查

deploy/README.md 第 8 节。以下命令由维护者在目标机上运行，本 skill 不运行：

- `curl --fail http://127.0.0.1:8000/health/live`
- `curl --fail http://127.0.0.1:8000/health/ready`
- `sudo systemctl status golden-mic.service --no-pager`
- `sudo nginx -t`
- `sudo ss -lntp`
- `sudo bash /opt/golden-mic/current/deploy/validate_target_host.sh`

这些通过了，只说明基础设施没问题，不等于真实语音和模型已经验收。三层验收要分开记录。

## 步骤（调用本 skill 时）

1. 按参数（打包、签名、部署、排空、回滚、Docker、CI）找到对应小节和 deploy/README.md 的章节。
2. 列出前提：干净 checkout、授权窗口、release ID、旧版本 ID、排空状态，以及本文的风险。
3. 给出按顺序的清单和命令原文。**自己不运行**，标明每一步由谁、在哪台机器上执行。
4. 列出相关的"仍未消除的不一致"，提醒维护者先做决定。

## 示例

用户：`/golden-mic-release 回滚`

1. 先确认前提：
   - 要回滚到的 release ID；
   - `/opt/golden-mic/releases/<ID>` 完整，venv 可执行；
   - 环境文件和旧版本的 Settings 兼容；
   - 新版本没有写入旧版本读不了的数据。
2. 给出 deploy/README.md 的回滚命令原文，并说明：
   - 会先排空，最多约 2 小时；
   - 超时不切换；
   - 不会恢复环境文件和数据。
3. 列出回滚后要做的检查（见"发布后检查"）。
4. 告诉用户：以上都没有执行，要由维护者在目标机上运行。

## 验证

- **回答里的每条命令和数值都能在出处找到**：deploy/README.md、脚本或代码。找不到的写"未核实"。
- **不声称已经部署、构建、签名或回滚。** 这个 skill 不执行这些操作。

## 仍未消除的不一致（只记录，这里不修）

2026-10-22 已处理：README 和 deploy/README.md 对线上状态的说法统一为"有线上实例，但没做过分层验收"；Docker 路径补上排空、回滚、上线后检查和 7200 秒停止上限（`deploy/docker/update.sh`、`docker-compose.yml`、`Dockerfile`）；deploy/README.md 的打包范围、墓碑、质量门禁模板值和额度已按代码更正。之前记的"Docker 下按 IP 的额度会合并"不成立（生产模式下应用直接读 nginx 覆盖过的 `X-Forwarded-For`），已删除。维护者决定保留、只做记录的：

1. **Docker 路径仍没有签名和归档校验**，依赖用 pip 安装（带哈希的锁），前端在本机工作树里构建。
2. **版本号不一致**：后端 1.0.0，前端 0.1.0，线上看不到版本。
3. **停止上限的默认值偏长。** 代码默认 shutdown grace 7500 秒，比 uvicorn 7100 秒、systemd 7200 秒还长；生产模板设为 6900 秒，环境文件要设这个键。
4. **没有依赖漏洞审计**：没有 pip-audit、npm audit，也没有 SPDX。

## 参考

- [references/package-and-ci.md](references/package-and-ci.md)：发布包内容、归档验证器的限制、SBOM、CI 步骤
- 来源：
  - [deploy/README.md](../../../deploy/README.md)、`deploy/build_release.py`、`deploy/verify_release_archive.py`、`deploy/generate_sbom.py`；
  - `deploy/deploy_release.sh`、`deploy/rollback_release.sh`、`deploy/install_host.sh`、`deploy/validate_target_host.sh`；
  - `deploy/systemd/golden-mic.service`、`deploy/docker/Dockerfile`、`deploy/docker/docker-compose.yml`；
  - `backend/main.py`、`backend/config.py`、`backend/task_manager.py`；
  - `.github/workflows/linux-ci.yml`、[docs/RUNBOOK.md](../../../docs/RUNBOOK.md)、[README.md](../../../README.md)。
- 2026-10-22 按工作区代码核对。行号会变，以函数名和章节名为准。
