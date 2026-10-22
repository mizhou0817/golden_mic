# 限额与常量

2026-10-21 按工作区代码核对。单位以代码为准：后端用的是 MiB 和 GiB。

## `backend/mode_rules.json`（前后端共用的规则）

- **文本**：`sentence_max_chars` 2000、`script_soft` 3000、`script_max` 8000、`title_max` 40。其中 `title_max` 在后端没有被读取，标题上限写死在 `drafts.py:894`。
- **匹配**：
  - `match_ok` 0.85、`match_low` 0.6；
  - `alternative_takes` 5、`alternative_match_min` 0.5；
  - `source_hint_radius` 5.0、`cross_segment_max_gap` 2.0。
- **原声**：
  - 时长 `quote_min/warn/max` 1.0 / 20 / 30 秒；
  - `quote_snr_min_db` 18；`quote_text_difference` 0.1。
- **切点**：
  - `cut_head` 0.12、`cut_tail` 0.2、`cut_snap` 0.3、`quote_gap` 0.15、`jump_continuity` 0.5；
  - `original_hard_cuts_warn_above` 3，后端和前端都没有使用。
- **人名条**：`lower_third_seconds` 2.5、`repeat_gap` 60。
- **其他**：`pacing_cpm` 230 / 265 / 290 字每分钟；`stage_weights_raw` 加载时归一化（`production_modes.py:51-54`）。
- **检查码**（`checks`、`safety_checks`）：含义和级别见 gm-news-integrity。`QUOTE_TOO_LONG` 在规则里是 1 级，但超过 30 秒时代码会改成 0 级（`production_modes.py:1368-1369`）。

## `backend/config.py` 的默认值

- **上传与素材**：
  - `MAX_UPLOAD_MB` 500、`MAX_FILES` 20、`MAX_TOTAL_UPLOAD_MB` 5120；
  - `MAX_SHOTS` 120、`MAX_SENTENCES` 200；
  - 单文件和总时长 1800 / 3600 秒；
  - 分辨率不超过 7680×4320，帧率不超过 120 fps。
- **并发**：
  - `MAX_CONCURRENT_UPLOADS` 2、`MAX_CONCURRENT_TASKS` 1、`MAX_PENDING_TASKS` 5；
  - `v2_max_waiting_tasks` 固定为 50（`config.py:161-165`）。
- **时长与超时**：`SHUTDOWN_GRACE_SECONDS` 7500、`MEDIA_COMMAND_TIMEOUT_SECONDS` 7200、`GENERATIVE_FILL_TIMEOUT_SECONDS` 300。
- **额度**：匿名会话、IP、全站每小时 2 / 5 / 10 次（`config.py:17-19`）；旧接口 `TASK_RATE_LIMIT_PER_HOUR` 5；会话有效期 86400 秒。
- **保留与磁盘**：`TASK_TTL_HOURS` 0、`MIN_FREE_DISK_GB` 5、`TASK_DISK_RESERVATION_MULTIPLIER` 8、`ASR_CACHE_TTL_HOURS` 168。
- **质量与音频**：
  - `QUALITY_GATE_MODE` 为 warn，`QUALITY_MIN_MATCH_CONFIDENCE` 0.5；
  - TTS 目标语速 255 字每分钟只是兜底值，模式流水线实际传入 normal = 265（`models.py:220-221`），容差 0.08；
  - 旧流程的 TTS 响度 −20 / 5 / −2.0；
  - `MUSIC_BED_TARGET_LUFS` −32，`MUSIC_DUCK_RATIO` 8（只用于旧流程）。
- **生产环境的硬性要求**（`config.py:201-238`）：
  - 总上传不超过 5120 MB，文件不超过 20 个，总时长不超过 3600 秒；
  - 上传和任务并发都不超过 1，待处理不超过 5；
  - 剩余空间至少 50 GiB，预留倍数至少 6；
  - `TASK_TTL_HOURS` 大于 0。

## 其他模块里的常量

- **`uploads.py:70-104`**：
  - 分块 8 MiB，单文件 500 MiB；
  - 图片不超过 50 MiB 或 2000 万像素，`PHOTO_DURATION` 3.0 秒；
  - 每个任务最多 20 个文件、5 GiB；每个访客最多 100 个文件、20 GiB；
  - 全站最多 500 个上传会话；
  - 上传创建每小时 40 次（全站 1000 次）；
  - 分块超时 = 120 秒 + 大小 ÷ 8 KB/s。
- **`workbench.py:59-62`**：单条录音不超过 20 MiB，合计不超过 200 MiB、100 条；单次操作超时 1800 秒。
- **`production_modes.py:48-50`**：转写上限 3600 秒、20 万字。
- **`quality.py:26-27`**：单句画面不超过 6.5 秒，语速容差 ±8%。

## `GET /api/config/limits`

字段来源见 golden-mic-api-change 的 `references/endpoints.md`。其中一部分是写死的：`max_script_length`、匹配阈值、原声时长、保留时长、提交额度等。运营改了配置，这些字段不会跟着变。

## 和交接文档 §10 的差异

交接文档里的这些项和代码一致：20 个文件、30 分钟 / 60 分钟、120 个镜头、6.5 秒、原声 1 / 20 / 30 秒、语速 ±8%、稿件 3000 / 8000 字、图片定格 3 秒。

不一致的地方：

1. **单位**：文档写 MB、GB，后端用 MiB、GiB。
2. **标题长度**：后端是 40 字（`drafts.py:894`）；交接文档里另有一处写 32 字；`script_assist.py` 的上限是 30 字，提示词里又写"不超过 20 字"。
3. **提交额度**：除了文档写的"每小时 2 次"，后端还有 IP 5 次、全站 10 次，旧接口另有 5 次。limits 接口返回的 2 / 5 / 10 是写死的。
4. **保留时长**：V2 写死 72 小时，修改会重置倒计时，磁盘不足时可能提前删除。交接文档 §1 还写着"稿子和素材永远保留"，与此不符。
5. **格式**：后端多接受 `.jpeg`。图片另有 50 MiB、2000 万像素、GIF 最多 300 帧的限制。
6. **后端有、文档没写的限制**：最多 200 句、单句不超过 2000 字、分辨率和帧率上限、每个访客 100 个文件 / 20 GiB、每人最多 20 个草稿、V2 等待队列最多 50 个。
7. **文档里有、后端没有的**：原型 LIMITS 里的 `syncMax` 15 对应的是旧的 `SYNC_SOUND_MAX_DURATION_SECONDS`；`uploadMbps` 60 在后端找不到。
