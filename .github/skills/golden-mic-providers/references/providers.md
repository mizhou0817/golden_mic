# 各服务实现细节

2026-10-21 按工作区代码整理。下面写的默认值都不是秘密（URL、模型名、超时）。凭证只写键名，不写值。行号会变，以类名和函数名为准。

## Seed ASR：`backend/providers/asr.py`

- **类**：`VolcengineASRProvider`（asr.py:95）。工厂是 `create_asr_provider`，`ASR_PROVIDER` 可选 volcengine、doubao（asr.py:296-300）。
- **传输**：WebSocket（`websockets.asyncio.client.connect`）。测试时可以注入 `connect_factory`（asr.py:113、asr.py:221-227）。
- **端点**：`VOLCENGINE_ASR_BASE_URL` + `/` + `VOLCENGINE_ASR_ENDPOINT_PATH`，默认拼成 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`。resource 默认 `volc.seedasr.sauc.duration`（config.py:68-71）。
- **请求头**：
  - `X-Api-App-Key` 放 `VOLCENGINE_APP_ID`，`X-Api-Access-Key` 放 `VOLCENGINE_ACCESS_TOKEN`；
  - 另有 `X-Api-Resource-Id`、`X-Api-Connect-Id`（asr.py:127-136、asr.py:179-184）；
  - 只有一组凭证，不轮换。
- **请求**：
  - `model_name=bigmodel`，zh-CN，16 kHz、16 bit、单声道，开启 ITN 和标点，`result_type=full`；
  - nonstream 模式下追加 `enable_speaker_info` 和 `ssd_version=200`（asr.py:190-218）；
  - 音频按 6400 字节分块发送（asr.py:254-270）。
- **超时与重试**：
  - `ASR_TIMEOUT_SECONDS=60`；`ASR_MAX_RETRIES=2`，即最多 3 次，退避 1 秒 × 2^n（asr.py:18-19、asr.py:161-172）。只有旧流水线（`pipeline.py`）照用；V2 的预转写、补跑、录音转写、本人录音都把它设为 0（`max_retries = 0`）；
  - 响应上限 16 MiB（asr.py:21、asr.py:226）；
  - 接收超时为 max(60, 字节数 / 32000 + 60)（asr.py:235-238）。
- **错误类**：`ASRProviderError`、`ASRConfigurationError`。
- **必填校验**：`VOLCENGINE_ASR_BASE_URL`、`VOLCENGINE_ASR_ENDPOINT_PATH`、`VOLCENGINE_APP_ID`、`VOLCENGINE_ACCESS_TOKEN`、`VOLCENGINE_ASR_RESOURCE_ID`；URL 必须是 ws 或 wss（asr.py:138-153）。`VOLCENGINE_ASR_CLUSTER_ID` 不会发给服务端，只进缓存签名。

## Seed TTS：`backend/providers/tts.py`

- **`VolcengineTTSProvider`**（tts.py:241）
  - **默认值**：HTTP POST 到 `https://openspeech.bytedance.com/api/v3/tts/unidirectional`，resource `seed-tts-2.0`，音色 `zh_male_m191_uranus_bigtts`（config.py:59-63）。
  - **响应解析**：按行解析 JSON（兼容 `data:` 前缀）；音频是 base64 分块，`code=20000000` 表示结束（tts.py:411-458）。
  - **请求内容**：单句文本、speaker、mp3（48 kHz、160 kbps）、`enable_subtitle`（返回词时间）、语速、响度、`additions={context_texts, section_id}`（tts.py:342-367）。`context_texts` 是一段固定的播音指令，只在 resource 为 `seed-tts-2.0` 时发送（tts.py:19-25、tts.py:301-315）。
  - **鉴权**：先轮流使用 `VOLCENGINE_TTS_API_KEYS`（放在 `X-Api-Key`），最后才用 `VOLCENGINE_APP_ID`（`X-Api-App-Id`）加 `VOLCENGINE_ACCESS_TOKEN`（`X-Api-Access-Key`）（tts.py:329-333、tts.py:400-409）。
  - **重试**：尝试次数 = max(retries + 1, 凭证数)。401、403、429、5xx、额度不足、并发超限都会重试（tts.py:371-372、tts.py:573-579）。
  - **其他限制**：连接数上限 20；在 `tts_pipeline` 里逐句串行合成。
- **其他实现**：
  - `EdgeTTSProvider`（tts.py:69）：用 `edge_tts`，只校验 `TTS_VOICE`；
  - `OpenAITTSProvider`（tts.py:138）：POST `{TTS_BASE_URL}/audio/speech`。
- **工厂**：`create_tts_provider(settings, target_chars_per_minute=)`。`TTS_PROVIDER` 可选 volcengine、doubao、edge、openai、openai-compatible（tts.py:462-499）。
- **错误类**：`TTSProviderError`、`TTSConfigurationError`。

## 画面理解：`backend/providers/vision.py`

- **默认实现 `KimiVisionProvider`**（vision.py:417）：POST `{KIMI_BASE_URL}/chat/completions`，带 `reasoning_effort` 和 `response_format=json_object`。默认值：`https://api.moonshot.cn/v1`、`kimi-k3`、`low`（config.py:28-31）。
- **`VolcengineVisionProvider`**（vision.py:241）：POST `{base}/responses`，`thinking` 设为 disabled。只有在 `VISION_PROVIDER=volcengine` 时才使用，凭证是 `VOLCENGINE_VISION_API_KEYS`（多 key 轮换，尝试次数 = max(3, key 数)）。
- **`VisionProvider`**（vision.py:59）：OpenAI 兼容接口，用 `VISION_BASE_URL`、`VISION_API_KEY`、`VISION_MODEL`。
- **发出的数据**：
  - 每个镜头一张 5 帧拼图（JPEG，base64）；失败时退回中间帧（vision_pipeline.py:61-74、vision_pipeline.py:177-198）；
  - 实体核验另外发 `visual_beat` 和 `target_entities`。
- **超时、重试、并发**：60 秒、2 次重试；并发 `MAX_VISION_CONCURRENCY=4`（vision_pipeline.py:23）。
- **工厂**：`VisionProvider.from_settings`，`VISION_PROVIDER` 可选 kimi、moonshot、volcengine、doubao、ark、openai、openai-compatible（vision.py:535-559）。

## 向量：`backend/providers/embedding.py`、`embedding_concurrency.py`

- **`VolcengineMultimodalEmbeddingProvider`**（embedding.py:212）：POST `{base}/embeddings/multimodal`。默认 `doubao-embedding-vision-251215`，2048 维（只允许 1024 或 2048）。
- **凭证**：`VOLCENGINE_VISION_API_KEYS`，多 key 轮换。没有单独的 Embedding Key（embedding.py:686）。
- **文本向量**：每条文本发一次请求，并发 `EMBED_MAX_CONCURRENCY=8`。**没有缓存。**
- **视频向量**：
  - 发送镜头 mp4 的 base64（超过 50 MB 拒绝）加元数据；
  - 同一账号共用一个自适应并发控制器：遇 429 并发减半，并按 Retry-After 冷却；连续 3 次瞬时错误也减半（embedding_concurrency.py:137-163）；
  - 有总时间预算（config.py:110-114）；
  - 缓存在 `video_embeddings.json`（matching.py:661-688）。
- **可重试的状态码**：401、403、408、409、425、429、≥500（embedding.py:657-663）。
- **`EmbeddingProvider`**（embedding.py:77）：OpenAI 兼容的 `/embeddings`，用 `EMBED_*`。
- **工厂**：`EmbeddingProvider.from_settings(settings, task_dir=)`，`EMBEDDING_PROVIDER` 可选 volcengine、doubao、ark、openai、openai-compatible。

## 文本模型：`backend/providers/llm.py`

- **`LLMProvider`**（llm.py:130）：统一 POST `{base}/chat/completions`。
- **`from_settings` 按 `LLM_PROVIDER` 选择**（llm.py:162-192）：
  - kimi / moonshot：用 `KIMI_*`，不发送 temperature；
  - volcengine：用 `VOLCENGINE_LLM_*`，`thinking` 设为 disabled；
  - openai：用 `LLM_*`。
- **`for_script_segmentation`** 固定使用 Kimi（llm.py:194-201）。V2 阶段 5 不调用它，旧流水线和预检仍在用。
- **各方法发送的内容**：
  - `segment_script`：全文；
  - `rerank`：句子、视觉节拍、候选镜头的描述、关键词、OCR；
  - `plan_pronunciations`：全文加含数字的句子；
  - `classify_music_mood`：稿件前 4000 字；
  - `complete_text`：system 与 user 文本。
- **超时与重试**：120 秒、2 次重试；`complete_text` 只在 httpx 错误时重试 1 次；`classify_music_mood` 不重试。
- **AI 写作接口**：`/api/script/assist` 和 `/api/script/effects` 的并发槽 2 个；每个客户端每小时 30 次，全站每小时 300 次（main.py:730-748）。

## 生成补画面：`backend/providers/generative.py`

- **`GenerativeFillProvider`**（generative.py:101）：
  - 图片走 Ark 的 `images/generations`（Seedream）；
  - 视频走 `contents/generations/tasks`（Seedance），提交后每 5 秒轮询一次。
- **默认值**：模式 video、1080p、5 秒、最多 2 段、超时 300 秒，**默认关闭**（config.py:122-133）。
- **凭证**：优先 `SEEDANCE_2_0_API_KEY`，否则用 `VOLCENGINE_GEN_API_KEYS` 的第一个；同一个 key 同时用于图片和视频。
- **发出的数据**：只发文本提示（generative.py:350-355）。
- **下载**：只接受 https，且主机属于 `.volces.com`、`.volccdn.com`、`.byteimg.com`、`.bytedance.com`；禁止重定向；单个文件不超过 64 MB。
- **触发条件**：`generative_fill_configured`、任务偏好 `generative_fill`、不是 original 模式，三者同时满足（mode_pipeline.py:982-991）。

## 本地语音：`backend/providers/local_speech.py`

- **说话人特征**：sherpa-onnx 的 `SpeakerEmbeddingExtractor`，读 `LOCAL_SPEAKER_MODEL_PATH`。
- **强制对齐**：transformers 的 CTC 模型，读 `LOCAL_ALIGNMENT_MODEL_PATH`。设置 `local_files_only=True`、`trust_remote_code=False`。
- **默认关闭**，从不下载模型。`LOCAL_SPEECH_REQUIRED=true` 时，缺模型会抛出 `SpeechUnavailable`。
- **就绪检查**：`local_speech_readiness`，检查运行库、模型路径和许可确认（`LOCAL_SPEECH_LICENSE_REVIEWED`）。
- CTC 模型尚未交付（`docs/CONFIGURATION.md:68`）。

## 各阶段的调用点（V2）

| 时机 | 调用 | 出处 |
|---|---|---|
| 上传完成后、开始制作之前 | Seed ASR 预转写 | `uploads._preprocess` → `_transcribe`（uploads.py:1039-1136） |
| 阶段 2 | 对失败的素材补跑一次 ASR，`max_retries=0`；本地说话人分析（可选） | `mode_pipeline._retry_failed_asr`（mode_pipeline.py:388-428） |
| 阶段 4 | Kimi 画面理解（没有空镜时跳过） | mode_pipeline.py:1880-1896 |
| 阶段 5 | 不调用模型 | mode_pipeline.py:1902 |
| 阶段 6 | 原话本地对齐，不调用模型；旁白匹配调用向量、LLM 精排、实体核验（走视觉服务）；可选生成补画面 | `_match_narration`、`_generated_narration`（mode_pipeline.py:939-1007） |
| 阶段 7 | 读音规划（Kimi），然后逐句 TTS；本人录音走 ASR，`max_retries=0` | `_assemble_audio`、`_recorded_narration`（mode_pipeline.py:1149-1337） |
| 阶段 8–10 | 不调用服务 | — |
| 结果页修改 | 重新匹配、生成、配音 | `edit_mode_workspace`（mode_pipeline.py:2018-2261） |
| 结果页上传录音 | ASR | `workbench._ingest_recording`（workbench.py:937-1017） |
| AI 写作、选效果 | Kimi `complete_text` | main.py:762-826 |

## `errorKind`

- **赋值来源**：
  - `quote_missing`：`QuoteMissingError`；
  - `qc`：`ModeQualityError`，或阶段回执被拒；
  - `shortage`：`ShotShortageError`；另外消息里同时有"镜头"和"不足"时也归为 shortage；
  - `speech_prerequisite`：`local_speech.SpeechUnavailable`（本机语音组件没准备好）。凡是带 `error_kind` 属性的异常，`task_manager` 都原样采用（task_manager.py:832-851）；
  - `transient`：没有 `error_kind`、消息也不是镜头不足的其余异常；
  - `network`：已声明，但后端没有赋值处。
- **前端怎样处理**：errorKind 为空、`network` 或 `transient` 时显示"再试一次"；`qc`、`shortage`、`quote_missing` 时只显示"回去改稿子"；`speech_prerequisite` 不显示"再试一次"，提示联系维护人员（非 A 模式还可以改用 AI 配音继续）。V2 下的 shortage 可以选择"重复使用画面"后继续（Processing.tsx:81-108）。
