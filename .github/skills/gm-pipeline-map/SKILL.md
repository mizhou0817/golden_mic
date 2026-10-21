---
name: gm-pipeline-map
description: 金话筒 V2 制作流水线地图（pipeline map）。列出十个阶段对应的后端模块、调用的 AI 服务（Seed ASR、Kimi、doubao-embedding-vision、Seed TTS、Seedream/Seedance）、关键配置和测试，以及新增检查码、新增服务商、新增导出格式时要同步改的文件。用户问"这一步在哪段代码""哪个阶段调用了 Kimi/火山""改这里会影响什么""加一个检查码/服务商/导出格式要改哪些文件"，或排查某个阶段失败、太慢、重复收费时使用。只读参考，不用于真实调用服务或修改配置。
---

# 金话筒 V2 流水线地图

## 目的
定位每个阶段的代码、服务和配置，评估改动的影响面。所有外部服务调用都可能收费，排查时只读代码、跑合成测试，不触发真实调用。

## 适用 / 不适用
- 适用：定位阶段代码，追踪服务调用，做改动影响分析，规划新增检查码、服务商或导出格式。
- 不适用：
  - 运行制作或调用真实服务，需用户另行授权。
  - 验证步骤，见 gm-verify。
  - 渲染参数，见 gm-ffmpeg-render。
  - 检查码含义和红线，见 gm-news-integrity。

## 总体结构
- **入口**：V2 草稿创建时固定 `mode_contract=True`（`drafts.py`），`pipeline.run_pipeline` 再转交给编排器 `mode_pipeline.run_mode_pipeline`。`pipeline.py` 的其余部分是旧流水线，只为兼容保留。
- **阶段编号和名称**：编号是整数 1–10。界面名称按模式取自 `backend/mode_rules.json` 的 `modes.<voiceover|mixed|original>.stages`，前端读的是同一个文件（`productionModes.ts`、`Processing.tsx`）。`models.py` 里的 `PIPELINE_STAGE_NAMES` 是旧名称，只出现在日志里。
- **改后重算**：`mode_pipeline.edit_mode_workspace` 按 `mode_rules.json` 的 `plan_stages` 只重跑受影响的阶段。
- **模式代号**：代码里 voiceover = A · AI 配音，mixed = B · 旁白 + 原声，original = C · 只用原声。界面上的模式名不带"A · / B · / C ·"前缀。

## 十个阶段

| # | 界面名称（A / B / C） | 主要代码 | 外部服务 | 关键配置 | 主要测试 |
|---|---|---|---|---|---|
| 1 | 检查素材 | `pipeline._run_upload_validation`；上传时由 `uploads.py` 探测，`media_input.prepare_media_inputs` 把照片转成 3 秒视频并处理裁剪 | 无（本地 ffprobe） | `MAX_FILES`、`MAX_UPLOAD_MB`、`MAX_TOTAL_UPLOAD_MB`、`MAX_SOURCE_DURATION_SECONDS_PER_FILE`、`MAX_TOTAL_SOURCE_DURATION_SECONDS` | test_m1_media、test_media_input、test_upload_sessions、test_v2_drafts |
| 2 | 听素材里的声音 / 听采访 / 听采访 | 转写在**上传预处理时就已发生**（`uploads._transcribe`）。阶段内：`mode_pipeline._retry_failed_asr` 对失败项最多再收费重试一次；`analyze_task_speakers`；`media.normalize_assets` | Seed ASR；可选的本地说话人模型（离线） | `ASR_PROVIDER`、`VOLCENGINE_ASR_*`、`ASR_CACHE_*`、`SYNC_SOUND_VAD_*`、`LOCAL_SPEECH_*` | test_asr、test_v2_speech、test_v2_speaker_vectors、test_v2_quote_ranking |
| 3 | 切分镜头 / 切分空镜 / 找空镜 | `pipeline._run_scene_detection` 调 `media.detect_shots`（PySceneDetect）；空镜池分层在 `mode_pipeline` | 无 | `MAX_SHOTS` | test_m1_media、test_v2_broll_fallback |
| 4 | 看懂每个画面 | `pipeline._run_vision_annotation` 调 `vision_pipeline.annotate_shots`；空镜池为空时跳过 | Kimi（默认 `VISION_PROVIDER=kimi`） | `VISION_PROVIDER`、`KIMI_*`、`VOLCENGINE_VISION_*` | test_m2_vision |
| 5 | 给稿子分句 / 分旁白和原声 / 对稿子 | `mode_pipeline._write_script` 只读取客户端已确认的句子。分句规则在 `production_modes.parse_sentences`，草稿阶段就已运行。**V2 不调用 LLM 分句**；`script_segmentation.py` 的 Kimi 分句只用于旧流水线 | 无 | `MAX_SENTENCES`；`mode_rules.json` 的 `limits` | test_v2_text_rules、test_production_modes |
| 6 | 给每句话找画面 / 配画面、对原话 / 找原话 | 原话：`_align_quotes_offloop`，本地匹配，低于阈值报错。旁白：`_match_narration` 调 `matching.build_match_plan`（向量、视频向量、LLM 精排、`assignment.solve_capacity_assignment`）。可选 `_generated_narration` | doubao-embedding-vision；Kimi 精排和实体核验；可选 Seedream/Seedance。C 模式没有旁白，不会创建这些服务 | `EMBEDDING_PROVIDER`、`VOLCENGINE_EMBEDDING_*`、`VOLCENGINE_VISION_API_KEYS`（向量服务共用这把 Key）、`RETRIEVAL_*`、`VIDEO_EMBEDDING_*`、`ENTITY_VERIFICATION_*`、`GENERATIVE_FILL_*` | test_m3_matching、test_assignment、test_v2_unique_shots、test_v2_footage_chain |
| 7 | 配音 / 配音 / 整理原声 | `_assemble_audio`。原声：`_prepare_quote_audio`（`tts_pipeline.extract_mode_pcm`、`normalize_mode_unit`）。本人录音：`_recorded_narration`。TTS：`synthesize_narration`，稿中有数字时先由 Kimi 做读音计划（`pronunciation.py`）。最后 `concatenate_narration` | Seed TTS 2.0；Kimi（读音）；Seed ASR（本人录音）。C 模式不调用 TTS | `TTS_PROVIDER`、`VOLCENGINE_TTS_*`、`VOLCENGINE_VOICE_TYPE`、`TTS_NEWS_RATE_TOLERANCE`。V2 的响度写死在代码里，`TTS_TARGET_*` 只影响旧流程 | test_m4_tts、test_v2_own_voice、test_v2_short_audio_unit |
| 8 | 做字幕 | `graphics.generate_mode_subtitles`、`validate_mode_subtitle_artifacts`。`subtitles.py` 只提供字体常量和旧版 ASS | 无 | 偏好项 `caption_style`、`quote_caption` | test_v2_media、test_v2_subtitle_font |
| 9 | 合成视频 | `_render_mode` → `build_mode_edl` → `rendering.render_mode_video`；`music.prepare_music_bed` | 无（本地 FFmpeg） | `MEDIA_COMMAND_TIMEOUT_SECONDS`、`MUSIC_LIBRARY_DIR`、`MUSIC_BED_TARGET_LUFS` | test_m5_rendering、test_motion_rendering、test_v2_media |
| 10 | 质量检查 | `_complete_mode`：音源核验、`quality.generate_quality_report`、`production_modes.evaluate_mode_checks`、`quality.enforce_quality_gate`。导出前另有 `publication.publication_gate` | 无（ffmpeg ebur128、silencedetect） | `QUALITY_GATE_MODE`、`QUALITY_MIN_MATCH_CONFIDENCE`、`TTS_MAX_LOUDNESS_SPREAD_LU` | test_quality、test_publication、test_v2_export_unresolved |

整条流水线的测试是 `tests/test_mode_pipeline.py`，运行方式见 gm-verify。

## 计费与重复调用的风险点
- 用户选好文件、上传完成后就会排队预处理，`uploads._transcribe` 会调用收费的 Seed ASR。同一素材有 ASR 缓存（`ASR_CACHE_*`）。
- 阶段 2 对转写失败的素材最多再收费重试一次。
- 服务商的超时和重试次数写在 `backend/providers/*.py` 的常量里：ASR、TTS、视觉、向量都是 60 秒、2 次重试，LLM 是 120 秒、2 次重试。改动时要计算最坏情况下的调用次数。
- 新增的 TTS 设置必须同步进配音缓存签名，否则会错误复用旧音频，见下方改动清单。
- 排查问题用合成素材和受保护测试，不要用真实凭证重跑制作。

## 改动清单
新增检查码、新增 AI 服务商、新增导出格式时要同步修改的文件，见 [references/change-checklists.md](references/change-checklists.md)。

## 来源
- [docs/DEVELOPMENT.md](../../../docs/DEVELOPMENT.md) 第 2 节代码地图（那里没列 graphics、tts_pipeline、matching、audio_filters、music）
- [backend/mode_pipeline.py](../../../backend/mode_pipeline.py)、[backend/pipeline.py](../../../backend/pipeline.py)、[backend/mode_rules.json](../../../backend/mode_rules.json)、[backend/config.py](../../../backend/config.py)、[backend/providers/](../../../backend/providers/)
- 2026-10-21 按提交 af1e4e7 读代码核对。行号会变，以函数名为准。
