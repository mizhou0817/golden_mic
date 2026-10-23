# 十个制作阶段：代码、服务、配置、测试

2026-10-21 按提交 af1e4e7 加工作区的代码核对。行号会变，以函数名为准。

## 总体结构

- **入口**：V2 草稿创建时固定 `mode_contract=True`（`drafts.py`）。`pipeline.run_pipeline` 再转给编排器 `mode_pipeline.run_mode_pipeline`（`pipeline.py:124-130`）。`pipeline.py` 的其余部分是旧流水线，只为兼容保留。
- **阶段编号**：整数 1–10。界面上的名称按模式取自 `backend/mode_rules.json` 的 `modes.<voiceover|mixed|original>.stages`。前端读的是同一个文件（`productionModes.ts`、`Processing.tsx`）；V2 任务也用这些名称和权重覆盖阶段（`task_manager.py:108-111`）。`models.py` 里的 `PIPELINE_STAGE_NAMES` 是旧名称，只出现在日志里。
- **改后重算**：由 `v2_editing.compile_plan` 计算要跑哪些阶段，再由 `mode_pipeline.edit_mode_workspace` 执行。见 SKILL.md 的"重做规则"。
- **单 worker**：`asyncio.Semaphore(min(MAX_CONCURRENT_TASKS, 1))`（`task_manager.py:124`）。apply 的执行和制作共用这把锁。

## 阶段表

| # | 界面名称（A / B / C） | 主要代码 | 外部服务 | 关键配置 | 主要测试 |
|---|---|---|---|---|---|
| 1 | 检查素材 | `pipeline._run_upload_validation`；上传时由 `uploads.py` 先探测；`media_input.prepare_media_inputs` 把照片转成 3 秒视频并处理裁剪 | 无（本地 ffprobe） | `MAX_FILES`、`MAX_UPLOAD_MB`、`MAX_TOTAL_UPLOAD_MB`、`MAX_SOURCE_DURATION_SECONDS_PER_FILE`、`MAX_TOTAL_SOURCE_DURATION_SECONDS` | test_m1_media、test_media_input、test_upload_sessions、test_v2_drafts |
| 2 | 听素材里的声音 / 听采访 / 听采访 | 转写在**上传预处理时**就发生了（`uploads._transcribe`）。阶段内：`mode_pipeline._retry_failed_asr` 对失败的素材补跑一次（`max_retries=0`），`analyze_task_speakers`（本地，可选），`media.normalize_assets` | Seed ASR（只补跑失败的）；可选的本地说话人模型 | `ASR_PROVIDER`、`VOLCENGINE_ASR_*`、`LOCAL_SPEECH_*` | test_asr、test_v2_speech、test_v2_speaker_vectors、test_v2_quote_ranking |
| 3 | 切分镜头 / 切分空镜 / 找空镜 | `pipeline._run_scene_detection` 调 `media.detect_shots`（PySceneDetect）；空镜池的分层在 `mode_pipeline` 里 | 无 | `MAX_SHOTS` | test_m1_media、test_v2_broll_fallback |
| 4 | 看懂每个画面 | `pipeline._run_vision_annotation` 调 `vision_pipeline.annotate_shots`；空镜池为空时跳过 | Kimi（默认 `VISION_PROVIDER=kimi`） | `VISION_PROVIDER`、`KIMI_*`、`VOLCENGINE_VISION_*` | test_m2_vision |
| 5 | 给稿子分句 / 分旁白和原声 / 对稿子 | `mode_pipeline._write_script` 只读取客户端确认过的句子。分句规则在 `production_modes.parse_sentences`，草稿阶段就已经运行。**V2 不调用 LLM 分句**；`script_segmentation.py` 的 Kimi 分句只用于旧流水线 | 无 | `MAX_SENTENCES`；`mode_rules.json` 的 `limits` | test_v2_text_rules、test_production_modes |
| 6 | 给每句话找画面 / 配画面、对原话 / 找原话 | 原话：`_align_quotes_offloop`，本地匹配，缺原话时在创建任何 provider 之前抛出 `QuoteMissingError`。旁白：`_match_narration` 调 `matching.build_match_plan`（向量、视频向量、LLM 精排、`assignment.solve_capacity_assignment`）。可选 `_generated_narration`。镜头不足抛出 `ShotShortageError` | doubao-embedding-vision；Kimi 精排和实体核验；可选的 Seedream/Seedance。C 模式没有旁白，不会创建这些服务 | `EMBEDDING_PROVIDER`、`VOLCENGINE_EMBEDDING_*`、`VOLCENGINE_VISION_API_KEYS`（向量服务也用这把 Key）、`RETRIEVAL_*`、`VIDEO_EMBEDDING_*`、`ENTITY_VERIFICATION_*`、`GENERATIVE_FILL_*` | test_m3_matching、test_assignment、test_v2_unique_shots、test_v2_footage_chain |
| 7 | 配音 / 配音 / 整理原声 | `_assemble_audio`。原声：`_prepare_quote_audio`（`tts_pipeline.extract_mode_pcm`、`normalize_mode_unit`）。本人录音：`_recorded_narration`（ASR，`max_retries=0`）。TTS：稿件含数字时，先由 Kimi 做读音规划（`pronunciation.py` 只负责抽取和应用），再 `synthesize_narration`；最后 `concatenate_narration` | Seed TTS 2.0；Kimi（读音）；Seed ASR（本人录音）。C 模式不调用 TTS | `TTS_PROVIDER`、`VOLCENGINE_TTS_*`、`VOLCENGINE_VOICE_TYPE`、`TTS_NEWS_RATE_TOLERANCE`。V2 的响度写死在代码里，`TTS_TARGET_*` 只影响旧流程 | test_m4_tts、test_v2_own_voice、test_v2_short_audio_unit |
| 8 | 做字幕 | `graphics.generate_mode_subtitles`、`generate_mode_graphics`（人名条；AI 标识只在旧版本里）、`validate_mode_subtitle_artifacts`。`subtitles.py` 只提供字体常量和旧版 ASS | 无 | 偏好项 `caption_style`、`quote_caption` | test_v2_media、test_v2_subtitle_font |
| 9 | 合成视频 | `_render_mode` → `build_mode_edl`（含跳切遮盖）→ `rendering.render_mode_video`；`music.prepare_music_bed` | 无（本地 FFmpeg） | `MEDIA_COMMAND_TIMEOUT_SECONDS`、`MUSIC_LIBRARY_DIR`、`MUSIC_BED_TARGET_LUFS` | test_m5_rendering、test_motion_rendering、test_v2_media |
| 10 | 质量检查 | `_complete_mode`：音源核验、`quality.generate_quality_report`、`production_modes.evaluate_mode_checks`、`quality.enforce_quality_gate`。导出前另有 `publication.publication_gate` | 无（ffmpeg ebur128、silencedetect） | `QUALITY_GATE_MODE`（默认 warn）、`QUALITY_MIN_MATCH_CONFIDENCE`、`TTS_MAX_LOUDNESS_SPREAD_LU` | test_quality、test_publication、test_v2_export_unresolved |

整条流水线的测试是 `tests/test_mode_pipeline.py`。它不是 `test_v2_*` 文件，受保护入口（带不带 `--legacy` 都一样）不会跑它；只能按 golden-mic-verify 里"单独跑某个测试模块"的做法运行，要先征得用户同意。

## 计费与重复调用的风险点

- **预转写**：上传完成后就排队预处理，`uploads._transcribe` 调用收费的 Seed ASR，早于"开始制作"。预转写失败不会自动重试。
- **缓存**：V2 的预转写缓存在内存里（最多 16 条），另外复用同一 owner 72 小时内已就绪的清单；**不用 `ASR_CACHE_DIR`**，那个磁盘缓存只给旧流水线用（`uploads.py:1012-1082`）。
- **阶段 2** 对转写失败的素材最多补跑一次，并且 `max_retries=0`（`mode_pipeline.py:388-428`）。这只会在旧的预上传创建路径（`main.py::_create_preuploaded_task`）里发生；V2 开始制作要求所有素材已就绪，否则返回 422 `files_not_ready`（`drafts.py:1012-1015`）。
- **超时和重试不是统一的**：
  - ASR：60 秒、2 次重试只用于旧流水线；V2 的预转写、补跑、录音转写、本人录音都是 0 次（一次操作一次收费请求）；
  - TTS、Embedding、火山视觉：尝试次数 = max(3, 凭证数)；
  - LLM：120 秒、2 次重试，其中 `complete_text` 只重试 1 次，`classify_music_mood` 不重试；
  - 生成补画面：300 秒、不重试。

  改动时要计算最坏情况下的调用次数。细节见 golden-mic-providers。
- **阶段回执**（`ModeStageCache`）覆盖阶段 2、3、4、6、7、9，签名规则见 [change-checklists.md](change-checklists.md) 第 2 节。回执状态为 `started` 时拒绝自动重跑。
- **配音缓存**：新增的 TTS 设置必须加进配音缓存签名（voice signature），否则会错误复用旧音频。
- **排查**：用合成素材和受保护测试，不要用真实凭证重跑制作。
