---
name: golden-mic-task-debug
description: 用于排查金话筒 V2 任务为什么失败、卡住或报错：读任务状态（status、errorKind、badRows、error_stage、error_message）；分清失败类型（transient、shortage、qc、quote_missing，代码里另有 speech_prerequisite，network 只在前端出现）；找日志（任务目录的 task.log、服务输出、journalctl、docker logs）和中间文件；判断哪种重试会再次收费（预转写重试、RetrySame、回去修改 recover-draft、修改后重算）；用合成素材和模拟服务离线复现。用户说"任务失败了""卡住了""为什么报 409/410/422/429/503/507""能不能重试""重试要不要钱""日志在哪"时使用。不要用它真实重跑收费任务（先征得用户同意），也不要用于部署和回滚（看 golden-mic-release）或设计新接口（看 golden-mic-api-change）。关键词：排查 debug、失败 failed、errorKind、日志 log、task.log、重试 retry、RetrySame、recover-draft、计费 billing、任务目录。
---

# 金话筒失败任务排查

先读状态和日志，再下结论。任何会再次收费的操作（重试、重新开始、修改后重算），先告诉用户会调用哪些收费服务，得到同意再做。

## 何时使用 / 不要使用

- 使用：
  - 任务停在某个阶段，或界面显示"处理失败""状态需要确认"；
  - 接口返回 409、410、422、429、503、507，需要知道原因；
  - 用户问能不能重试、重试要不要钱、日志在哪；
  - 改失败处理或重试相关的代码之前，先弄清现有行为。
- 不要使用：
  - 真实重跑任务、真实上传素材：要用户另行同意（见红线）；
  - 部署、排空、回滚：看 golden-mic-release；
  - 设计新接口或新错误码：看 golden-mic-api-change；
  - 服务商接口和模拟写法：看 golden-mic-providers；FFmpeg 参数：看 golden-mic-media。

## 红线（附出处）

- **不要为了"试一下"去点重试、调接口或提交任务。** 预转写重试、RetrySame、恢复后重新开始、修改后重算都可能再次收费（见"重试与计费"）。出处：`.github/copilot-instructions.md` 红线 1。
- **`task_state.json` 当作机密。** 它的 `draft_context.files[].capability` 是上传凭据原文：`drafts.py` 建文件时写入，`task_manager.py::_persist_record` 落盘。
  - 只用脚本取需要的字段（写法见 [references/task-files.md](references/task-files.md)）；
  - 不整份打印、复制，也不贴进对话或 issue；
  - `admission.sqlite3` 也不整表导出。
- **不读 `.env`。** 日志里出现带 `token`、`signature` 的 URL，转述前先遮掉。
  - `task.log` 写入时已遮掉 Bearer、api_key 和 URL 签名参数（`storage.py::sanitize_sensitive_text`）；
  - 服务输出不保证遮掉。
- **不重启正在服务的实例来"清状态"。** 重启时 queued、running 的任务一律改为 failed，不会续跑（`task_manager.py` 启动恢复）。
- **不改、不删 `data/` 下的文件**，包括任务目录、账本和 `service-logs`。

## 状态怎么读（附出处）

- **接口**：`GET /api/tasks/{id}`（或 `/status`），必须带 `X-Task-Token`。
  - 凭据不对返回 404。
  - 到期或已清理，且能凭令牌或同一会话证明时，返回 410 `task_gone`。墓碑保留 30 天，之后返回 404。出处：`main.py::_authorize_task`、`admission.py`。
- **主要字段**（`TaskManager.status_response`、`DraftService.view`）：
  - `status`：draft、queued、running、done、failed、cancelled；
  - `current_stage`、`stage_name`、`progress`。进度已按阶段权重折算。
  - `queue`：排队时从 1 开始的位置；
  - `errorKind`、`badRows`（从 1 开始的句号）、`error_stage`、`error_message`。消息已脱敏，不含堆栈。
  - `retry_same_supported`：只表示服务端装了 RetrySame，不代表这次一定能重试；
  - `plan`：最近一次 apply 的原始回执，失败的也算（`v2_editing.py::current_plan`）。
- **一直停在 running。** 没有 ETA，也没有全局看门狗，多半是某个外部调用或 FFmpeg 命令还没到超时。
  - FFmpeg 的上限是 `MEDIA_COMMAND_TIMEOUT_SECONDS`：代码默认 7200 秒，生产模板 6800 秒（`config.py`、`deploy/golden-mic.env.production.example`）。
  - 上传后的预转写另有时限：`max(5, min(MEDIA_COMMAND_TIMEOUT_SECONDS, 0.5 × 时长))` 秒（`uploads.py`）。
- **修改失败不改成 failed。** apply 失败时作品保持 done，并记录：
  - `error_stage="workbench"`；
  - 错误码 `operation_failed`、`operation_cancelled`、`operation_timed_out`、`validation_failed: …` 或 `needs_change: …`；
  - 上一版保留（`workbench.py`）。

## 失败类型 errorKind

| errorKind | 写入位置 | 常见原因 | 界面给的出路（`Processing.tsx`） |
|---|---|---|---|
| `transient` | `task_manager.py` 兜底：异常没带 `error_kind` 时；`drafts.py` 还在两处直接写：重启时发现已接收却仍是草稿的记录，以及入队失败的 `accepted_start_uncertain` | 服务商报错或超时；FFmpeg 失败（消息为"{步骤}失败：stderr 最后一行"）；其他没归类的异常 | "重试"（RetrySame，要求 `retry_same_supported`） |
| `shortage` | `matching.py`（不同镜头不够）；消息同时含"镜头"和"不足"时也归为它 | 素材里的不同镜头不够配稿 | "回去删减稿子"；A、B 模式可以同意重复使用镜头后继续 |
| `qc` | `mode_pipeline.py` | 阻断模式下的质检问题；声音或字幕证据不全；阶段回执完整性校验失败 | "回去改稿子" |
| `quote_missing` | `mode_pipeline.py` | 原话在素材里没找到，`badRows` 给出句号 | "回去改第 N 句" |
| `speech_prerequisite` | `providers/local_speech.py` | 本机说话人识别或对齐的前置条件缺失（运行库、模型或许可确认），是服务器配置问题 | 说明原因，不给重试；B、C 模式可以改用 AI 配音；要维护者补齐本地语音包 |
| 空 | 启动恢复（`task_manager.py`，只改成 failed，不赋值） | 服务重启时任务还在排队或运行 | 前端按可重试处理 |
| `network` | 后端从不写入 | 为兼容旧客户端保留在类型里，文案是"网络连接中断" | — |

各接口的状态码和 `detail.code`，见 [references/failures.md](references/failures.md)。

## 日志在哪

| 环境 | 服务输出 | 任务日志 |
|---|---|---|
| 本机（VS Code 任务 "Golden Mic: Start local server"） | `data\service-logs\local-<时间>.stdout.log`、`.stderr.log`；uvicorn 用 `--no-access-log`，没有访问日志 | `<DATA_DIR>\<task_id>\task.log`，`DATA_DIR` 默认 `data/tasks` |
| systemd 生产 | `journalctl -u golden-mic.service --no-pager -n 100`；nginx 访问日志 `/var/log/nginx/golden-mic.access.log` | `/srv/golden-mic-data/tasks/<task_id>/task.log` |
| Docker | `docker logs goldmic`（json-file，单个 20 MB、保留 5 个） | 宿主机 `/opt/goldmic/data` 挂载为容器内 `/data`。镜像预建 `/data/tasks`，实际以 `goldmic.env` 的 `DATA_DIR` 为准 |

- **后端没有 logging 配置**，没有请求 ID。服务输出里只有少量 `print`：启动时恢复了几个任务、跳过的坏状态文件、TTL 清理、磁盘不足时的提前清理（`Low disk: freed …`）。
- **`task.log` 每行是 `[本地时间 ISO] 消息`**（`storage.py::write_text_log`）。内容包括：
  - 阶段开始和结束；
  - 执行的命令行（`media.py`）；
  - FFmpeg 失败时的原始 stderr；
  - apply 失败的堆栈（末尾 4000 字符）；
  - 首版制作失败的堆栈（"失败详情："，末尾 4000 字符）；
  - 磁盘不足时提前清理的记录：触发清理的草稿记总数，被清掉素材的草稿各记一行。
- 写入前都经过 `sanitize_sensitive_text`，令牌、密钥和带签名的 URL 参数会被打码。

任务目录里各文件能看出什么，见 [references/task-files.md](references/task-files.md)。

## 重试与计费

| 操作 | 接口 | 什么时候能用 | 会再次收费的服务 |
|---|---|---|---|
| 预转写重试 | `POST /api/tasks/{id}/files/{file_id}/pretranscribe`，body `{"retry": true}` | 该文件的上传状态是 `asr_failed` 或 `interrupted` | Seed ASR，整段重新计费 |
| RetrySame | `POST /api/tasks/{id}/retry`，body 为 `{}`、`{"expected_revision": 当前版本}`，或再加 `"allow_shot_reuse": true`（仅 shortage） | 失败任务已被准入账本接收；输入、配置和 17 个后端实现文件的散列都没变；阶段缓存能核验 | 从第一个未完成或未核验的阶段重跑，已完成且核验过的阶段不重跑。重跑到阶段 4、6、7 时，会再次调用 Kimi、火山 Embedding、Seed TTS。不经过每小时开始额度 |
| 回去修改 | `POST /api/tasks/{id}/recover-draft` | 失败的任务 | 新建一个草稿（201），不会自动开始。开始后整条流水线重跑，Kimi、Embedding、TTS 都会再次收费，并占用每小时开始额度 |
| 修改后重算 | `POST /api/tasks/{id}/apply` | done 的作品 | 按重做规则：换画面重跑 6、9、10（Embedding、Kimi）；改字、录音、调语速等重跑 7–10（TTS）。规则见 golden-mic-backend-map |
| 导出 | `POST /api/tasks/{id}/exports` | done 的作品 | 不调用外部服务，只占本机 FFmpeg |

- **一次操作只发一次识别请求**：V2 的预转写、录音转写、本人录音都关掉了 ASR 的内部重试（`max_retries = 0`），失败直接报告，要不要再花钱由用户决定。
- **RetrySame 失败时不收费。** 拒绝时返回 409，不会开始，也不收费（`drafts.py::retry_verified_mode`）：
  - 有阶段的回执停在 `started`（收费调用的结果不确定）时，返回 `retry_provider_uncertain`；
  - 配置或代码改过（包括重新部署），返回 `retry_inputs_changed`；
  - 其他 409 码见 [references/failures.md](references/failures.md)。
- **中途失败会丢掉已付费的部分。** 画面理解、Embedding、TTS 在阶段结束时才写缓存，中途失败时已付费的那部分结果用不上。
- **重启会丢掉内存状态**：ASR 内存缓存、导出状态和限流计数。

## 步骤

1. **确认环境和任务 ID**：本机、systemd 还是 Docker。不要让用户把访问令牌原文发给你。能登录服务器时，直接读任务目录（只取字段）。
2. **读状态字段**：`status`、`stage_name`、`error_stage`、`error_kind`、`bad_rows`、`error_message`。
3. **读 `task.log` 末尾**（80 行左右），对照 `error_stage` 找到失败的那一步。FFmpeg 失败时看 command 行和后面的 stderr。
4. **判断类型**：按 errorKind 表区分是稿子或素材的问题，还是服务问题。需要更多证据时，看对应的中间文件。
5. **给用户结论**：原因、证据（文件和行）、可选的出路，以及每条出路会不会再次收费、调用哪些服务。然后等用户决定。
6. **要修代码时**：先离线复现（合成素材加模拟服务），写测试，再改代码。验证用 golden-mic-verify。

## 离线复现

- **只用合成素材和模拟服务**，不连真实服务。现成的写法：
  - `tests/test_v2_drafts.py::DraftTests`：V2 草稿全流程的夹具。例如：
    - `test_start_once_then_explicit_retry_capability`；
    - `test_retry_real_cache_manager_queue_preserves_stages_quota`；
    - `test_expiry_only_v2_and_owned_tombstone`；
    - 一组 `test_recovery_*`。
  - `tests/test_mode_pipeline.py` 的 `FakeVoice`、`FakeVision`、`FakeContext`：十阶段流水线的假服务；
  - `tests/test_upload_sessions.py::FakeProvider`：用 `error=` 参数模拟 ASR 失败；
  - `tests/test_m1_media.py`：用 `configure_media_command_timeout(0.05)` 模拟 FFmpeg 超时；
  - `tests/test_v2_task_metadata.py`：410 和 404 的判定。
- **复现写成新的 `tests/test_v2_*.py` 用例**，用 golden-mic-verify 的受保护入口运行。
- **单独跑某个模块**要先征得用户同意，并在不含 `.env` 和 `data/` 的副本里运行（做法见 golden-mic-verify）。

## 示例

用户："任务在第 7 步失败了，能直接重试吗？"

1. 读 `task_state.json` 的字段（下面的值是假设的）：
   - `status=failed`；
   - `error_stage=配音`；
   - `error_kind=transient`；
   - `error_message` 是 TTS 请求超时。
2. 看 `mode_stage_cache/stage-7.json`：`state` 是 `started`，不是 `complete`。所以 RetrySame 会返回 409 `retry_provider_uncertain`，不会开始，也不会收费。
3. 回复用户：
   - 这次不能 RetrySame；
   - 可以"回去修改"建一个新草稿，再开始制作。这会再次调用 Kimi、火山 Embedding、Seed TTS，并占用每小时开始额度。
   - 问用户是否接受。

## 验证

- **每条原因都要有证据**：状态字段、`task.log` 的行，或中间文件的字段。拿不到证据的写"未核实"。
- **说"可以重试"之前**，按"重试与计费"表核对条件，尤其是 RetrySame 的 409 条件。
- **改了代码的**，用 golden-mic-verify 汇报。

## 仍未消除的不一致（只记录，这里不修）

2026-10-22 已修复：`speech_prerequisite` 进了接口类型和前端校验，失败卡说明原因、不给重试；前端按回执的 `steps[].kind / state` 显示"第 n/m 步"；首版失败的堆栈写进 `task.log`；磁盘不足时被清掉素材的草稿各记一行，服务输出也有一行。之前记的"qc、shortage 的失败卡也说再试一次"不成立（这两类不显示那句话），已删除。剩下的：

1. **`network` 后端从不写入。** 为兼容旧客户端和测试夹具保留在类型里（维护者决定）。
2. **没有请求 ID。** 服务输出和 `task.log` 只能按任务 ID 和时间对照。

## 参考

- [references/failures.md](references/failures.md)：各接口的状态码、`detail.code` 和 RetrySame 的 409 码
- [references/task-files.md](references/task-files.md)：任务目录里的文件，以及安全读取的写法
- 来源：
  - `backend/task_manager.py`、`drafts.py`、`workbench.py`、`v2_editing.py`、`uploads.py`、`mode_pipeline.py`、`matching.py`、`storage.py`、`main.py`、`admission.py`、`providers/local_speech.py`；
  - `frontend/src/components/Processing.tsx`、`Workspace.tsx`、`frontend/src/types.ts`；
  - `.vscode/tasks.json`、`deploy/deploy_release.sh`、`deploy/nginx/golden-mic.conf.template`、`deploy/docker/docker-compose.yml`。
- 2026-10-22 按工作区代码核对。行号会变，以函数名为准。
