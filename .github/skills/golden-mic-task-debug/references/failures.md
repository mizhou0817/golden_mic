# 各接口的失败返回

只列排查时常见的情况。完整的状态码约定见 golden-mic-api-change 的 [status-codes.md](../../golden-mic-api-change/references/status-codes.md)。2026-10-22 按代码核对，行号会变，以函数名为准。

## 通用

| 状态码 | 含义 | 出处 |
|---|---|---|
| 403 | Origin 不对，或生产环境缺匿名会话（响应头 `X-Anonymous-Session: required`）。前端不重放 | `main.py` 中间件 |
| 404 | 没有任务，或凭据不对。不区分这两种，不暴露任务是否存在 | `main.py::_authorize_task` |
| 410 `task_gone` | 草稿或作品已到期；或已清理，且墓碑能证明。要凭访问令牌或同一会话，墓碑保留 30 天，之后返回 404。手动删除不写墓碑，直接返回 404 | `main.py::_authorize_task`、`admission.py` |
| 410 `draft_expired` | 少见：请求通过授权后、处理过程中（等待操作锁或接收分块时）草稿恰好到期 | `drafts.py::DraftService.check` |
| 408 | 请求体 30 秒没传完 | `main._bounded_json`、`drafts.bounded_json`、`task_operations` |
| 503（`Retry-After: 60`） | 排空中。拦 `/api/tasks*`、`/api/uploads*`、`/api/match*`、`/api/script*` 的 POST/PUT/PATCH | `main.py` 中间件 |
| 507（`Retry-After: 300`） | 磁盘不足（`InsufficientDiskSpaceError`）。直接抛 `HTTPException(507)` 的地方不带 Retry-After | `main.py` 中间件 |

## 开始制作 `POST /api/tasks/{id}/start`

| 返回 | `detail` | 说明 |
|---|---|---|
| 202 | `status: queued`、`queue` | 已接收，进入单 worker 队列 |
| 422 | `quote_missing`（带 `badRows`） | B、C 模式有原声句没找到来源 |
| 422 | `quote_unverified`（带 `badRows`） | C 模式原话在素材里不连续 |
| 422 | `files_not_ready`（带 `file_id`、`status`） | 还有文件没处理完 |
| 429 `start_budget`（`Retry-After` ≥ 60 秒） | 每小时开始额度用完：会话 2、IP 5、全站 10 | 已接收的不退额度（`admission.py`） |
| 409 | `already_accepted` / `accepted_start_uncertain` | 已经开始过，或开始的结果不确定。先读状态，不要重发 |
| 503 | 队列满（50 个）、排空中，或本机语音前置条件不满足 | 排空的 503 由中间件返回，带 `Retry-After: 60`；队列满和语音前置条件不满足的不带 |
| 507 | 磁盘预留不足 | |

## RetrySame `POST /api/tasks/{id}/retry`

返回 202，带 `status: queued` 和 `resume_from`（从第几阶段重跑）。被拒绝时不会开始，也不收费：

| 返回 | 触发条件（`drafts.py::retry_verified_mode`、`DraftService.retry`） |
|---|---|
| 409（纯文本） | 任务不是 failed，或没有被准入账本接收 |
| 422 | body 不是 `{}`、`{"expected_revision": 当前版本}`，也不是再加 `"allow_shot_reuse": true` 的形式 |
| 409 `retry_same_unavailable` | 没有重试绑定、不是 V2 规则、没有任何一个阶段缓存能核验，或者重试钩子没有安装（`supported: false`） |
| 409 `shot_reuse_not_applicable` | 带了 `allow_shot_reuse` 却不是 shortage 失败，或是 C 模式 |
| 409 `retry_inputs_changed` | 稿子、模式、偏好、句子、说话人、全部配置、素材字节，或 17 个实现文件的散列变了。重新部署也会触发 |
| 409 `retry_cache_missing` | 阶段 4、6、7 已经开始，但缓存文件不存在 |
| 409 `retry_provider_uncertain` | 某阶段回执停在 `started`，收费调用的结果不确定；或某个文件的任务内转写状态是 started、cancelled、failed（阶段 2） |
| 409 `retry_cache_invalid` | 缓存签名对不上，或读缓存出错 |

阶段 6 的回执是 `retry_safe` 时，表示收费调用已经结束、之后在本地失败，可以直接重跑。

## 回去修改 `POST /api/tasks/{id}/recover-draft`

返回 201，带新草稿和新的访问令牌。新草稿不会自动开始。

| 返回 | `detail.code` | 说明 |
|---|---|---|
| 409 | `recovery_requires_terminal_task`、`recovery_requires_accepted_task` | 原任务还没结束，或从没被接收 |
| 409 | `recovery_source_unverified` | 原任务的输入快照或素材核验不过 |
| 409 | `recovery_legacy_unsupported` | 旧流程的任务 |
| 409 | `shot_reuse_not_applicable` | 不是 shortage 却要求重复用镜头，或者是 C 模式 |
| 429 | `recovery_quota` | 文件名额不够（每人最多保存的文件数、合计 20 GiB） |

## 预转写重试 `POST /api/tasks/{id}/files/{file_id}/pretranscribe`

- body 必须是 `{"retry": true}`，否则返回 422 "file_id and retry:true are required"。
- 只对上传状态为 `asr_failed` 或 `interrupted` 的文件有效。会重新调用 Seed ASR 并计费。

## 修改 `POST /api/tasks/{id}/apply`

- **请求阶段的错误**（`workbench.py` 的 `apply`、`ready`、`enqueue`）：
  - 409 `stale_revision`（带 `current_revision`）；
  - 409 有导出或中断的发布在进行；
  - 409 "Revision limit reached"（版本满 100 个）；
  - 429 "Pending task capacity reached"；
  - 422 请求不合法，或 `compile_plan`、回执写入被拒（`RevisionError`）。
- **后台执行失败时**：作品保持 done，`error_stage="workbench"`，上一版保留。`error_message` 是下列之一：
  - `operation_failed`
  - `operation_cancelled`
  - `operation_timed_out`（上限 1800 秒）
  - `validation_failed: …`
  - `needs_change: …`
- **到哪里看**：堆栈末尾 4000 字符写进 `task.log`，回执在 `v2_operations/<operation_id>.json`。
- **回执上限**：最多 200 份。达到上限后，下一次 apply 返回 422。

## 导出 `POST /api/tasks/{id}/exports`

| 返回 | 说明 |
|---|---|
| 409 | 任务过期、正忙或需要恢复；同一作品已有导出在进行；导出期间来源或发布状态变了 |
| 422 | 不是 V2 制作模式的任务；时间线或当前帧不合法；AI 生成画面缺少有效的来源记录（`generated_media_disclosure.json`）。2026-10-23 起不再因为"会丢掉 AI 标注"拒绝 MP3、SRT |
| 429 | 全站同时导出已满（2 个），或这个作品的导出已有 20 个。不带 Retry-After |
| 503 | 排空中 |
| 507 | 导出磁盘预留不足 |

- **导出失败时**：`v2_exports/<export_id>/job.json` 的 `state` 是 `failed` 或 `cancelled`，`error` 一律是 `export_not_published`，不给具体原因（`v2_editing.py`）。
- **服务重启后**：还在 queued、running 的导出显示为 `interrupted` / `not_resumed`，不会续跑。
- **超时**：`min(MEDIA_COMMAND_TIMEOUT_SECONDS, max(120, 4 × 成片时长))` 秒（`studio_render.py`）。
