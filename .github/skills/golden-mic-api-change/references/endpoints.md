# 现有 V2 接口

2026-10-21 按工作区代码整理。行号会随代码变化，以函数名为准。

鉴权缩写：

- **P**：`_authorize_private`，即 `_authorize_task`（`main.py:878`）加上 `service().check`（`drafts.py:1444-1447`）。
- **W**：workbench 的 `access()`（`workbench.py:1031-1038`）。写请求还要经过 `_authorize_workbench_mutation`。
- **E**：V2 导出。先把作用域改写成 `/studio/export`，再授权（`v2_editing.py:500-510`）。

所有写请求还受中间件约束：排空、Origin、CSRF、请求体上限，见 [status-codes.md](status-codes.md)。

| 方法 | 路径 | handler（位置） | 鉴权 | 成功 |
|---|---|---|---|---|
| POST | `/api/tasks`（JSON 不带 script 和 upload_ids 时建草稿） | `create_task`（`main.py:653`） | 草稿 owner；生产环境要有会话 | 201 |
| GET | `/api/tasks/{id}`、`/status` | `get_task`（`main.py:931-932`） | `_authorize_task` | 200；ETag 命中返回 304 |
| POST | `/api/tasks/{id}/retry` | `retry_task_v2`（`main.py:1332`） | 写 | 202 |
| PATCH | `/api/tasks/{id}/metadata` | `patch_task_metadata`（`main.py:897`） | 写 | 200 |
| GET/POST/PUT | `/api/tasks/{id}/checks` | `task_checks`、`check_confirmation`（`main.py:1391-1397`） | `_authorize_task` | 200 |
| DELETE | `/api/tasks/{id}` | `delete_task`（`main.py:1084`） | 写 | 204 |
| GET | `/api/tasks/{id}/draft` | `draft`（`drafts.py:1449`） | P | 200 |
| POST | `/api/tasks/{id}/recover-draft` | `recover_draft`（`drafts.py:1454`） | P，且必须恰好一个 `X-Task-Token` | 201 |
| PATCH | `/api/tasks/{id}/draft`、`/api/tasks/{id}` | `patch`（`drafts.py:1467`） | P 写 | 200 |
| GET/POST | `/api/tasks/{id}/files` | `files`、`add_file`（`drafts.py:1473`、`drafts.py:1478`） | P | 200 / 201 |
| PUT | `…/files/{fid}/chunks/{index}` | `chunk`（`drafts.py:1489`） | P 写 | 200 |
| POST | `…/files/{fid}/complete` | `complete`（`drafts.py:1496`） | P 写 | 202 |
| POST | `…/files/{fid}/probe` | `probe`（`drafts.py:1501`） | P 写 | 200 |
| PATCH/DELETE | `…/files/{fid}` | `file_patch`、`file_delete`（`drafts.py:1508`、`drafts.py:1513`） | P 写 | 200 / 204 |
| POST | `…/files/{fid}/pretranscribe`、`/api/tasks/{id}/pretranscribe` | `pretranscribe`、`task_pretranscribe`（`drafts.py:1519`、`drafts.py:1524`） | P 写 | 200 |
| GET | `…/files/{fid}/thumb`、`/wave` | `media`（`drafts.py:1533`） | P | 200 |
| POST | `/api/tasks/{id}/align` | `align`（`drafts.py:1542`） | P 写 | 200 |
| POST | `/api/tasks/{id}/start` | `start`（`drafts.py:1547`） | P 写 | 202 |
| GET | `/api/tasks/{id}/context`（同一个 `context` 还挂在 `/api/tasks/{id}/workbench/context`，`workbenchApi.ts` 现在调用的是这一条） | `context`（`workbench.py:1286`） | W | 200 |
| GET | `…/versions`、`/versions/{rev}`、`/versions/{rev}/video`、`/versions/{rev}/report` | `workbench.py:1321-1350`、`workbench.py:1397` | W | 200 |
| POST | `/api/tasks/{id}/apply`（别名 `/addop`、`/run`） | `apply`（`workbench.py:1364-1366`） | W 写 | 202 |
| GET | `/api/tasks/{id}/operations/{op}` | `operation`（`workbench.py:1384`） | W | 200 |
| POST | `/api/tasks/{id}/restore` | `restore_v2`（`workbench.py:1451`） | W 写 | 202 |
| POST | `/api/tasks/{id}/recordings` | `recordings_v2`（`workbench.py:1460`） | W 写 | 201 |
| GET | `/api/tasks/{id}/quotes/{row}/takes` | `main.py:1411-1420` | 读 | 200 |
| POST/GET/DELETE | `/api/tasks/{id}/exports`、`/exports/{eid}`、`/exports/{eid}/file` | `v2_editing.py:562`、`v2_editing.py:701`、`v2_editing.py:712`、`v2_editing.py:728` | E | 202 / 200 / 200 / 200 或 206 |
| GET | `/api/config/limits` | `public_limits`（`main.py:363-397`） | 公开 | 200 |

## 表里没有的接口

上表只列 V2 制作、修改、导出的主线。下面这些路由也在用，改它们时先看对应装饰器：

- **主应用（`main.py`）**：
  - `GET /api/tasks`（我的作品列表）、`GET /api/session`（匿名会话）、`GET /api/config/availability`、`GET /api/config/workspace`；
  - `POST /api/match/preview`（原话预检）、`POST /api/script/assist`（AI 写稿或润色，调用收费的 LLM）、`POST /api/script/effects`（AI 自动选效果；没有可用的模型、模型忙或排空期间，只按稿子里的关键词回答，永远有结果）；
  - 取成片和素材：`GET /api/tasks/{id}/report`、`/video`、`/thumbs/{shot_id}.jpg`、`/poster`、`/waves/{row}.json`（别名 `/waveform/{row}`）、`/quote/{row}/takes`（`/quotes/…` 的别名）；
  - 旧接口：`POST /api/tasks/{id}/remix`、`/replace-shot`、`/export`（V2 用 `/exports`）；
  - 范例：`GET /api/samples/default`、`/api/samples/default/video`、`/api/samples/default/{kind}/{name}`；
  - 维护：`/api/admin/drain`（POST 开始、GET 查看、DELETE 取消，只接受本机回环）。
- **`workbench.py`**：`POST /api/tasks/{id}/edit`（旧的单步修改，字幕样式和人声增强只能走它）。
- **`task_operations.py`**：`POST /api/tasks/{id}/duplicate`（复制一份作品）。
- **`uploads.py`**：`/api/uploads*`（上传会话，分块、完成、探测）。

## 重点接口的请求和响应

### `POST /api/tasks/{id}/align`：只预览，不提交编辑
- **请求**：JSON，上限 256 KiB。允许的键：`script, mode, prefs/preferences, type_marks, speaker_names, sentences, speakers, quality_gate_mode, asset_options, source_voice_preferred`（`drafts.py:849-851`）。可以只传 `sentences`。
- **响应**：`{matches, match_ok: 0.85, match_low: 0.6, local_speech, rules_version}`（`drafts.py:1105`）。
  - `matches[]`：`idx, kind, source (QuoteTake 或 null), alt_takes, score, upload_id, start, end, speaker_id, asr_text`（`production_modes.py:1169-1173`、`production_modes.py:1215-1230`）。
  - `alt_takes`：最多 5 条，必须是同一说话人，分数至少 0.5（`mode_rules.json` 的 `alternative_takes`、`alternative_match_min`）。
  - `QuoteTake`：`take_id, upload_id, start, end, speaker_id, asr_text, score, snr_db, words, precision, matched_text, segment_ids`（`production_modes.py:147-159`）。时间是原始上传文件里的绝对秒数。
  - 状态判定：没有来源或分数 < 0.6 为 `missing`，< 0.85 为 `low`，其余为 `ok`（`production_modes.py:496-500`）。
- **错误**：422 无法安全对齐；409 任务已接收或不是草稿；503 必需的本地语音不可用。

### `POST …/pretranscribe`：只用来重试失败的转写
- 首次转写在 complete 之后自动进行：probe → 波形和静音区间 → ASR（`uploads.py:1085-1136`）。
- 文件级端点：请求体必须严格等于 `{"retry": true}`。任务级端点：`{file_id, retry: true}`。
- 只允许状态为 `asr_failed` 或 `interrupted`、且没有正在运行的上传，否则返回 409（`drafts.py:107-119`）。**会再次调用收费的 ASR。**

### `POST /api/tasks/{id}/start`
- **回执**：202 `{id, task_id, status: "queued", accepted: true, access_token, queue}`（`drafts.py:1228-1230`）。
- **错误**：
  - 422 `quote_missing` 或 `quote_unverified`；
  - 503 等待队列已满（上限 50）；
  - 429 `start_budget`；
  - 409 `already_accepted` 或 `accepted_start_uncertain`。
- 已接收的准入记录不会退还额度（`admission.py:1-10`）。

### `POST /api/tasks/{id}/apply`：一次提交多处修改，生成新版本
- **请求**：`ApplyRequest`，strict，不允许多余字段（`v2_editing.py:166-193`）：
  - `expected_revision`；
  - `deleted[]`；
  - `edits{row: text}`；
  - `voices{row: recording_id}`；
  - `pacing`；
  - `trims{row: {t0, t1}}`：剪短原声。只能缩短，至少 1 秒，不能切进词里（`production_modes.py:1234-1267`）；
  - `takes`：换段。只能选当前 `alt_takes` 里的；
  - `to_narration[]`：转旁白。仅 B 模式，会从阶段 6 开始重跑；
  - `speakers{id: {name ≤ 8, role ≤ 12}}`：改说话人。从阶段 8 开始重跑；
  - `replaces{row: {instruction ≤ 500}}`：换画面。
- **回执**：202 `{task_id, revision, current_revision, status: "queued", operation_id, steps, plan, visible_version}`（`workbench.py:1272-1275`）。
- **阶段计划**由 `v2_editing.compile_plan` 计算（`v2_editing.py:211-259`），不读 `mode_rules.json` 的 `plan_stages`：
  - 有 `to_narration` 时从阶段 6 跑到 10；
  - 只改说话人时从阶段 8 开始；
  - 其他修改从阶段 7 开始；
  - 空 apply 只跑 [9, 10]；
  - 每个换画面跑 [6, 9, 10]。
- 版本最多 100 个，否则返回 409；待处理已满返回 429。失败时保留上一版，状态回到 done（`workbench.py:1094-1101`、`workbench.py:1222-1231`）。

### `POST /api/tasks/{id}/recordings`
- multipart，字段为 `rowid` 或 `row_id`、`expected_revision`、`audio`。
- 格式 webm、ogg、wav、mp3、m4a；不超过 20 MiB、120 秒，单音轨。上传后会做 ASR（收费）。
- 原声句不接受录音，返回 422（`workbench.py:937-1017`、`workbench.py:1417-1422`）。

### `POST /api/tasks/{id}/restore`
- 请求体 `{rev, expected_revision}`。
- 做法是复制旧版本的快照，发布为新版本"Restore r{n}"，不重新渲染（`workbench.py:1118-1121`、`workbench.py:1451-1458`）。
- `expected_revision` 必填：缺少返回 422，和当前版本不一致返回 409 `stale_revision`（`v2_editing.py::V2RestoreRequest`）。

### `POST /api/tasks/{id}/exports`
- 请求体 `V2ExportOptions {fmt, aspect, res, sub, frame_seconds, expected_revision, acknowledge_unresolved}`（`studio_render.py:476-483`）。
- 支持的格式：mp4、gif、mp3、srt、png；画幅 16:9、9:16、1:1；分辨率 360p、720p、1080p。
- 每个任务最多 20 个导出作业，全站同时最多 2 个（`v2_editing.py:578-582`）。

### `GET /api/config/limits`
- 由设置计算的字段：`max_files`、`max_upload_bytes`、`max_total_upload_bytes`（设置值和 `uploads.MAX_WORK_FILES`、`MAX_FILE_BYTES`、`MAX_WORK_BYTES` 里小的那个）、`max_video_duration_seconds`、`max_total_video_duration_seconds`、`max_shots`、`maxPending`；`max_concurrent_tasks`（设置值和字面量 1 里小的）、`max_pending_tasks` 与 `legacy_max_pending_tasks`（设置值和字面量 5 里小的）。
- 运行时状态：`maintenance`（`task_manager.is_draining`）、`retry_same_supported`（重试钩子是否安装）；`allowed_extensions` 取模块常量 `ALLOWED_EXTENSIONS`。
- 这个接口不需要登录，所有字段都是公开信息，包括每小时的提交额度（`browser_submissions_per_hour`、`ip_submissions_per_hour`、`global_submissions_per_hour`）。2026-10-22 起它们取自真实生效的设置，不再是模型默认值 2 / 5 / 10。
- 其余字段都取自执行处（2026-10-22 起，`main.py::public_limits`）：
  - `mode_rules.json`：`max_script_length`（8000）、`script_soft_max`、原声时长 30/20/1 秒、匹配阈值 0.85/0.6、语速 230/265/290；
  - `Settings`：`rate_tolerance`（`TTS_NEWS_RATE_TOLERANCE`），每小时提交 `browser_submissions_per_hour` / `ip_…` / `global_…`；
  - 模块常量：`chunk_size`（`uploads.CHUNK_SIZE`，8 MiB）、`max_visual_sec`（`quality.MAX_VISUAL_CLIP_SECONDS`，6.5）、`retention_hours` 72 和 `draft_ttl_hours` 24（`task_metadata`）、`tombstone_days` 30（`admission.TOMBSTONE_SECONDS`）。
- 运营改了配置，界面读到的就是新值。
