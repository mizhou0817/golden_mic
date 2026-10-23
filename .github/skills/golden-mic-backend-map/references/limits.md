# 限额与常量

2026-10-21 按工作区代码核对。单位以代码为准：后端用的是 MiB 和 GiB。

## `backend/mode_rules.json`（前后端共用的规则）

- **文本**：`sentence_max_chars` 2000、`script_soft_chars` 3000、`script_max_chars` 8000、`title_max_chars` 40。后端经 `production_modes.TITLE_MAX_CHARS` 读取 `title_max_chars`（草稿校验和 AI 写稿都用它）。
- **匹配**：
  - `match_ok` 0.85、`match_low` 0.6；
  - `alternative_takes` 5、`alternative_match_min` 0.5；
  - `source_hint_radius_seconds` 5.0、`cross_segment_max_gap_seconds` 2.0。
- **原声**：
  - 时长 `quote_min_seconds` / `quote_warn_seconds` / `quote_max_seconds` 1.0 / 20 / 30 秒；
  - `quote_snr_min_db` 18；`quote_text_difference` 0.1。
- **切点**：
  - `cut_head_seconds` 0.12、`cut_tail_seconds` 0.2、`cut_snap_seconds` 0.3、`quote_gap_seconds` 0.15、`jump_continuity_seconds` 0.5；
- **人名条**：`lower_third_seconds` 2.5、`lower_third_repeat_gap_seconds` 60。
- **其他**：`pacing_cpm` 230 / 265 / 290 字每分钟；`stage_weights_raw` 加载时归一化（`production_modes.py:55-58`）。
- **检查码**（`checks`、`safety_checks`）：含义和级别见 gm-news-integrity。`QUOTE_TOO_LONG` 在规则里是 1 级，但超过 30 秒时代码会改成 0 级（`production_modes.py:1380-1381`）。

## `backend/config.py` 的默认值

- **上传与素材**：
  - `MAX_UPLOAD_MB` 500、`MAX_FILES` 20、`MAX_TOTAL_UPLOAD_MB` 5120；
  - `MAX_SHOTS` 120、`MAX_SENTENCES` 200；
  - 单文件和总时长 1800 / 3600 秒；
  - 分辨率不超过 7680×4320，帧率不超过 120 fps。
- **并发**：
  - `MAX_CONCURRENT_UPLOADS` 2、`MAX_CONCURRENT_TASKS` 1、`MAX_PENDING_TASKS` 5；
  - `v2_max_waiting_tasks` 固定为 50（`config.py:161-165`）。
- **时长与超时**：`SHUTDOWN_GRACE_SECONDS` 7500（生产模板 6900）、`MEDIA_COMMAND_TIMEOUT_SECONDS` 7200、`GENERATIVE_FILL_TIMEOUT_SECONDS` 300。
- **额度**：匿名会话、IP、全站每小时 2 / 5 / 10 次（`config.py:17-19`；生产模板 10 / 30 / 60）；旧接口 `TASK_RATE_LIMIT_PER_HOUR` 5；会话有效期 86400 秒。
- **保留与磁盘**：`TASK_TTL_HOURS` 0、`MIN_FREE_DISK_GB` 5、`TASK_DISK_RESERVATION_MULTIPLIER` 8、`ASR_CACHE_TTL_HOURS` 168。
- **质量与音频**：
  - `QUALITY_GATE_MODE` 为 warn，`QUALITY_MIN_MATCH_CONFIDENCE` 0.5；
  - TTS 目标语速 255 字每分钟只是兜底值，模式流水线实际传入 normal = 265（`models.py:223-224`），容差 0.08；
  - 旧流程的 TTS 响度 −20 / 5 / −2.0；
  - `MUSIC_BED_TARGET_LUFS` −32，`MUSIC_DUCK_RATIO` 8（只用于旧流程和没有 V2 媒体合同的旧三模式作品；V2 作品固定压低 18 dB）。
- **生产环境的硬性要求**（`config.py:201-238`）：
  - 总上传不超过 5120 MB，文件不超过 20 个，总时长不超过 3600 秒；
  - 上传和任务并发都不超过 1，待处理不超过 5；
  - 剩余空间至少 50 GiB，预留倍数至少 6；
  - `TASK_TTL_HOURS` 大于 0。

## 其他模块里的常量

- **`uploads.py:71-105`**：
  - 分块 8 MiB，单文件 500 MiB；
  - 图片不超过 50 MiB 或 2000 万像素，`PHOTO_DURATION` 3.0 秒；
  - 每个任务最多 20 个文件、5 GiB；每个访客最多 100 个文件、20 GiB；
  - 全站最多 500 个上传会话；
  - 上传创建每小时 40 次（全站 1000 次）；
  - 分块超时 = 120 秒 + 大小 ÷ 8 KB/s。
- **`workbench.py:59-62`**：单条录音不超过 20 MiB，合计不超过 200 MiB、100 条；单次操作超时 1800 秒。
- **`production_modes.py:52-54`**：转写上限 3600 秒、20 万字。
- **`quality.py:22-27`**：新闻语速范围 240–280 字每分钟（`MIN/MAX_NEWS_SPEAKING_RATE_CPM`）、定格不超过 0.3 秒、单句画面不超过 6.5 秒（`MAX_VISUAL_CLIP_SECONDS`，第 27 行）。语速容差 ±8% 不在这里，是 `Settings.tts_news_rate_tolerance`（`config.py:52`）。

## `GET /api/config/limits`

字段来源见 golden-mic-api-change 的 `references/endpoints.md`。2026-10-22 起每个字段都取自执行处，运营改了配置，界面读到的就是新值。仍是字面量的只有并发 1 和待处理 5 这两个上限（`main.py::public_limits`）。

## 和交接文档 §10 的关系

2026-10-22 已按代码更正交接文档 §10 和 §1：单位改为 MiB / GiB；标题 40 字；提交额度写全会话 / IP / 全站 2 / 5 / 10 次；写明保留期（24 小时 / 72 小时、修改重新计时、磁盘不足时提前清理），删掉"稿子和素材永远保留"；格式加上 `.jpeg` 和图片限制；补上后端另有的限制（200 句、单句 2000 字、每人 20 个草稿、等待队列 50 个）。没写进 §10 的细节：分辨率和帧率上限、每个访客 100 个文件 / 20 GiB、GIF 最多 300 帧、旧接口另有每小时 5 次。

仍不一致的（只做记录；标题长度已在 2026-10-22 统一为 40 字，AI 写稿也按它）：

1. **原型 `LIMITS`**：附录 B 原型源码里的 `syncMax` 15 对应旧的 `SYNC_SOUND_MAX_DURATION_SECONDS`，`uploadMbps` 60 在后端找不到；原型源码保持原样。
