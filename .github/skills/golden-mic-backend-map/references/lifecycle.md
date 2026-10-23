# V2 任务生命周期与数据保留

2026-10-21 按工作区代码核对。行号会变，以函数名为准。

## 状态

- `TaskState`：draft、queued、running、done、failed、cancelled（`models.py:25-31`）。交接文档里的 `uploading`，在后端对应 draft 状态加上文件级的上传状态。
- `StageState`：pending、running、done、failed（`models.py:34-38`）。
- 上传会话：
  - status：uploading、probed、processing、ready、asr_failed、failed、interrupted；
  - phase：upload、queued、probe、waveform、asr、done、error（`uploads.py:320-321`）。

## 主流程

1. **建草稿**：`POST /api/tasks`，JSON 里不带 script 和 upload_ids（`main.py:661-674`）→ `DraftService.create`（`drafts.py:500-528`）。
   - 状态为 draft，`expires_at` = 现在 + 24 小时。
   - 每人最多 20 个草稿，全站最多 1000 个，超出返回 429；磁盘不足返回 507。
2. **编辑**：`PATCH /api/tasks/{id}(/draft)` → `patch`、`editorial`（`drafts.py:848-981`）。
   - 标题不超过 40 字，不带句末标点，不能是占位符；A、B 模式全文至少 20 字。
   - A 模式只能有旁白，C 模式只能有原声。C 模式强制 `voice=ai`、`pacing=normal`、`generative_fill=False`。
3. **分块上传**：
   - `POST /{id}/files` → `add_file`：文件数不超过 min(20, `MAX_FILES`)，受总量预算约束。
   - `PUT …/chunks/{i}`：每块 8 MiB。
   - `POST …/complete` → `UploadStore.complete`：合并分块，并调度 `_preprocess`（`uploads.py:846-879`）。
   - 每次操作都会调用 `touch`：草稿续期 24 小时；上传会话最长到创建后 72 小时（`drafts.py:487-498`）。
4. **预处理和预转写（收费）**：`_preprocess` 依次做 probe → 波形和静音区间 → ASR（`uploads.py:1085-1136`）。失败时状态为 `asr_failed`，要用户显式调用 `pretranscribe` 并传 `{"retry": true}`。
5. **对齐预览**：`POST /{id}/align` → `DraftService.align` → `production_modes.align_quotes`（`drafts.py:1088-1106`）。只预览，不提交。
6. **显式开始**：`POST /{id}/start` → `_start_locked`（`drafts.py:1108-1230`）。
   - 重新对齐一次：缺原话返回 422 `quote_missing`；C 模式原话不连续返回 422 `quote_unverified`。
   - `ensure_start_capacity`：V2 等待队列最多 50 个（`task_manager.py:668-676`）。
   - 磁盘预留，并写入 `pretranscripts.json`。
   - `AdmissionLedger.accept`：SQLite 账本 `_v2/admission.sqlite3`，统计最近 3600 秒内全站、owner、IP 的次数，超限返回 429 加 Retry-After（`admission.py:40-125`）。**已接收的记录不退额度。**
   - 状态改为 queued → `TaskManager.start_task`。入队失败则状态为 failed，返回 409 `accepted_start_uncertain`。
7. **执行**：单 worker。`start_stage` 把状态置为 running。
   - 成功 → done，并保存"初版"快照；
   - 失败 → failed，`error_kind` 取异常上的属性，没有时推断为 shortage 或 transient；
   - 取消 → cancelled。

   最后都会释放磁盘预留（`task_manager.py:702-857`）。队列位置是排队列表里的序号 + 1（`task_manager.py:435`）。
8. **重试**：`POST /api/tasks/{id}/retry` → `DraftService.retry`（`drafts.py:1348-1376`）。
   - 只接受已接收且失败的任务，输入不可变。
   - `retry_verified_mode` 先核验阶段回执，再从第一个没完成或没验证的阶段继续。
   - 不新增准入记录。
   - 输入绑定里有全部 settings 的摘要，改过任何配置都会返回 409 `retry_inputs_changed`。
9. **修改 → 新版本**：`POST /api/tasks/{id}/apply`。
   - `compile_plan` 算出要跑的阶段；
   - `enqueue` 计算新 revision = 现有最大值 + 步数，一次 apply 只产生一个快照（`workbench.py:1222-1231`）；
   - `execute` 在临时目录里运行，超时 1800 秒；
   - 失败时 `restore_error` 保留上一版，状态回到 done；
   - 回执写入 `v2_operations`；满 200 份后不会自动清理，下一次 apply 返回 422（`v2_editing.py:265-267`）。
10. **版本与回滚**：
    - `GET /{id}/versions` 等查看接口；
    - `POST /{id}/restore` 复制旧快照，发布为新版本"Restore r{n}"，不重新渲染（`workbench.py:1118-1121`）；
    - 版本最多 100 个、8 GiB（`revisions.py:42-43`）。
11. **删除**：`DELETE /api/tasks/{id}` → `DraftService.delete` → `cancel_and_delete`：置为 cancelled、取消 worker、删除目录、释放预留（`task_manager.py:453-533`）。
12. **重启恢复**（**不自动续跑**）：
    - 先用 `recover_v2_publication` 回滚未完成的发布；
    - `restore_tasks` 把 queued 和 running 一律改为 failed；done 但缺成片或报告的，也改为 failed（`task_manager.py:253-274`）；
    - 已接收但仍是 draft 的记录，改为 failed、transient（`drafts.py:403-411`）；
    - 中断的 apply 只在 `GET /{id}/operations/{op}` 里显示 interrupted（`workbench.py:1392-1393`），状态接口里的 `plan` 保持原始回执；中断的导出读出时显示 interrupted 或 not_resumed（`v2_editing.py:549-550`）。

## 数据保留与清理

| 数据 | 保留规则 | 到期后 | 出处 |
|---|---|---|---|
| 草稿 | 闲置 24 小时到期，每次操作续期 | 410 `task_gone`（由 `_authorize_task` 返回，`main.py:866-868`）；`draft_expired` 只在请求通过授权后、处理过程中（等待操作锁或接收分块时）草稿恰好到期才出现 | `drafts.py:4-7`、`drafts.py:481-490` |
| V2 终态作品（done、failed、cancelled） | `processing_completed_at` 加 72 小时；没有时依次用 `updated_at`、`created_at`。**每次成功修改或恢复都会重置倒计时** | 410 `task_gone`；墓碑保留 30 天，期间凭访问令牌或同一会话访问返回 410，否则 404（`admission.py:150-164`） | `drafts.py:1412-1415`、`task_metadata.py:52-59`、`admission.py:127-164` |
| 手动删除的作品 | 立即删除，不写墓碑 | 404 | `drafts.py:1378-1394` |
| 上传会话 | 从创建起最长 72 小时 | 由 `cleanup_owned` 删除 | `uploads.py:92`、`uploads.py:1241-1255` |
| 上传 ASR 缓存 | min(72 小时, `ASR_CACHE_TTL_HOURS`) | — | `uploads.py:1051` |
| 导出文件 | 没有单独的 TTL，随任务删除；每个任务最多 20 个导出作业 | — | `v2_editing.py:578-582` |
| 旧任务 | `TASK_TTL_HOURS`，默认 0 表示不清理；生产环境必须大于 0 | — | `task_manager.py:547-605`、`config.py:134` |

- **清理调度**：`cleanup_loop` 和 `_cleanup_uploads_loop`，都是每 3600 秒运行一次（`main.py:173-218`）。
- **磁盘预留**：预留量 = 请求字节 × `TASK_DISK_RESERVATION_MULTIPLIER`（默认 8，生产至少 6），并且要求剩余空间减去已预留后不少于 `MIN_FREE_DISK_GB`（默认 5，生产至少 50）（`operations.py:63-85`）。
- **磁盘不足时可能提前删除**：`free_space_for` 按时间顺序清理，可能早于 72 小时（`drafts.py:601-660`）：
  1. 先删已完成作品的上传副本；
  2. 再删其他草稿的素材；
  3. 最后删最早的已完成作品。
- `backend/storage.py` 里没有保留逻辑，只有建目录、保存、日志和原子写入。
