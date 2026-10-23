---
name: golden-mic-providers
description: 用于修改或排查金话筒调用的收费 AI 服务：Kimi（画面理解、精排、读音、AI 写作）、火山 Embedding（文本和视频向量）、Seed ASR（转写）、Seed TTS（配音）、Seedream/Seedance（生成补画面），以及本地语音模型。说明调用位置、环境变量键名（不含值）、各服务的鉴权差异、超时与重试、计费与隐私规则、缓存、测试里怎样模拟。改 backend/providers/、asr_pipeline.py、tts_pipeline.py、uploads.py 的转写、matching.py、vision_pipeline.py，或排查"转写失败""配音失败""重复收费""换 Key"时使用。不要用于真实调用服务或填写凭证；只改 FFmpeg 渲染或界面时不要使用。关键词：转写 ASR、配音 TTS、画面理解 vision、向量 embedding、Kimi、火山 volcengine、豆包、Seed、计费 billing、模拟 mock。
---

# 金话筒外部 AI 服务

这些服务全部收费，而且会把素材和稿件发给第三方。改代码、排查问题、写测试时都**不调用真实服务**；接口细节以官方文档为准，不凭记忆写字段。

## 何时使用 / 不要使用

- 使用：
  - 改 `backend/providers/*.py` 或调用它们的代码：`uploads._transcribe`、`mode_pipeline`、`matching`、`vision_pipeline`、`tts_pipeline`、`workbench` 的录音转写、`/api/script/*`；
  - 排查转写或配音失败、重复收费、Key 轮换；
  - 为调用服务的代码写测试。
- 不要使用：
  - 真实调用服务、填写或检查凭证；
  - 只改 FFmpeg 参数（用 golden-mic-media）；
  - 只改接口约定（用 golden-mic-api-change）。

## 红线（附出处）

- 不调用收费服务，不提交真实生成任务（`.github/copilot-instructions.md` 红线 1）。选好文件、上传完成后就会开始收费转写，早于"开始制作"（README.md:13、`docs/USER_GUIDE.md:49`）。
- 不读取、打印 `.env` 的值。`.env.example` 只看键名，键名清单见 [references/env-keys.md](references/env-keys.md)。
- 凭证在 `Settings` 里是普通 `str`，只有 `anonymous_session_secret` 是 `SecretStr`（`config.py:15`）。所以**不要打印 `Settings` 对象或 `model_dump()`**。异常和日志保存前要经过 `storage.sanitize_sensitive_text`（`storage.py:98-113`）。
- 不把带 token 的媒体 URL 写进日志或文件。

## 服务一览

默认值来自 `backend/config.py`。完整细节（端点、并发、错误类、工厂）见 [references/providers.md](references/providers.md)。

| 服务 | 默认实现 | 凭证键名（不含值） | 超时与重试 | 发出的数据 | 什么时候调用 |
|---|---|---|---|---|---|
| Seed ASR 转写 | `ASR_PROVIDER=volcengine`，`asr.py::VolcengineASRProvider`，WebSocket | `VOLCENGINE_APP_ID`、`VOLCENGINE_ACCESS_TOKEN`，另加 `VOLCENGINE_ASR_RESOURCE_ID` | 60 秒；provider 默认 `max_retries=2`（最多 3 次），退避 1 秒 × 2^n，只有旧流水线用；V2 的每个调用点都设成 0（一次操作只发一次请求） | 整段 16 kHz 单声道 PCM | 上传完成后的预转写（`uploads._transcribe`）；阶段 2 对失败素材补跑一次（`max_retries=0`）；结果页的录音 |
| Seed TTS 配音 | `TTS_PROVIDER=volcengine`，`tts.py::VolcengineTTSProvider`，HTTP 流式 | `VOLCENGINE_TTS_API_KEYS`（逗号分隔，轮换使用）；后备 `VOLCENGINE_APP_ID` + `VOLCENGINE_ACCESS_TOKEN` | 60 秒；尝试次数 = max(3, 凭证数)；401、403、429、5xx、额度不足、并发超限都会重试 | 单句文本，加一段固定的播音指令 | 阶段 7 逐句串行；结果页改字、调语速后重新配音 |
| Kimi 画面理解 | `VISION_PROVIDER=kimi`，`vision.py::KimiVisionProvider` | `KIMI_API_KEY` | 60 秒、2 次重试；并发 4 | 每个镜头一张 5 帧拼图（JPEG） | 阶段 4（没有可用空镜时跳过）；阶段 6 的实体核验（`ENTITY_VERIFICATION_ENABLED`：代码默认和生产模板为 true，开发模板为 false；最多 `ENTITY_VERIFICATION_MAX_SHOTS` 个镜头）也用这个视觉服务，发拼图和目标实体 |
| Kimi 文本 | `LLM_PROVIDER=kimi`，`llm.py::LLMProvider` | `KIMI_API_KEY` | 120 秒、2 次重试；`complete_text` 只重试 1 次；`classify_music_mood` 不重试 | 精排：句子和候选镜头的描述；读音：全文和含数字的句子；AI 写作：system 与 user 文本 | 阶段 6 精排，阶段 7 读音规划，`/api/script/assist` 和 `/api/script/effects` |
| 火山 Embedding | `EMBEDDING_PROVIDER=volcengine`，`embedding.py::VolcengineMultimodalEmbeddingProvider` | `VOLCENGINE_VISION_API_KEYS`（没有单独的 Embedding Key；多 key 轮换） | 60 秒；尝试次数 = max(3, key 数)；文本向量并发 8；视频向量并发自适应，遇 429 减半，并按 Retry-After 冷却 | 句子文本；镜头 mp4（base64，≤ 50 MB） | 阶段 6；结果页改字、换画面 |
| Seedream / Seedance 生成补画面 | 默认关闭（`GENERATIVE_FILL_ENABLED=false`），`generative.py::GenerativeFillProvider` | 优先 `SEEDANCE_2_0_API_KEY`，否则用 `VOLCENGINE_GEN_API_KEYS` 的第一个 | 300 秒；没有重试；视频任务每 5 秒轮询一次 | 只发文本提示 | 阶段 6。要同时满足：任务偏好开了 `generative_fill`、不是 C 模式、配置齐全 |
| 本地语音 | 默认关闭，`local_speech.py` | 模型路径 `LOCAL_*`，不是凭证 | — | 不出本机 | 说话人特征、强制对齐（可选） |

## 鉴权差异

- **ASR 的授权不能代替 TTS 的授权。** TTS 可以用 `VOLCENGINE_APP_ID` + `VOLCENGINE_ACCESS_TOKEN` 作后备鉴权，但有 ASR 权限不等于有 TTS 权限。方舟（Ark）的 Key 也不能代替 ASR 授权（README.md:65-66、`docs/CONFIGURATION.md:32-33`）。
- **TTS 怎样选凭证。** 每次尝试按"尝试序号 % 凭证数"选一个：先轮遍各个 API Key，最后才用 APP_ID 组合（`tts.py:400-409`）。
- **视觉默认用 Kimi（`KIMI_API_KEY`）。** `VOLCENGINE_VISION_API_KEYS` 默认其实是给 Embedding 用的。只有设了 `VISION_PROVIDER=volcengine`，视觉才用它。
- **LLM 选 volcengine 时**，`VOLCENGINE_LLM_API_KEYS` 为空就借用 `VOLCENGINE_VISION_API_KEYS` 的第一个 key，而且不轮换（`llm.py:175-176`）。
- **`VOLCENGINE_APP_KEY` 没有被任何 provider 使用**，只出现在生产环境的占位符检查里。
- **生产环境的额外要求**（`config.py:266-326`、`config.py:368-372`）：
  - 进程环境变量 `APP_ENV=production` 时不读 dotenv；
  - URL 必须是 https，ASR 必须是 wss；
  - 拒绝模板里的占位凭证。

## 计费与重复计费

- **预转写失败不会自动重试。** 要用户显式调用 `pretranscribe` 并传 `{"retry": true}`，这会再收一次费。旧的预上传创建路径里（V2 开始制作要求素材全部就绪，不会走到这里），阶段 2 对失败的素材最多补跑一次，并且 `max_retries=0`（`mode_pipeline.py:388-428`）。
- **会命中缓存、不重复收费的情况：**
  - **预转写**：内存 LRU，最多 16 条、4 MiB，另外复用同一 owner 72 小时内已就绪的清单。键里有音频 sha256 和 provider 配置的摘要（`uploads.py:1012-1082`）。**V2 不用 `ASR_CACHE_DIR`**，那个磁盘缓存只给旧流水线用。
  - **阶段回执** `ModeStageCache`：覆盖阶段 2、3、4、6、7、9。回执状态为 `started` 时拒绝自动重跑（`mode_pipeline.py:118-191`）。
  - **逐句 TTS 音频**：缓存键是文本、语速目标、容差和 `voice_signature`。**新增 TTS 设置必须加进 `voice_signature`**，否则会错误复用旧音频（`mode_pipeline.py:1061-1068`、`mode_pipeline.py:1278-1313`）。
  - **视频向量**：`video_embeddings.json`（`matching.py:661-688`）。
  - **文本向量没有缓存**，每次运行阶段 6 或改稿都会重新计算并收费。
- **"再试一次"的限制。** "再试一次"（`retry_verified_mode`）会核对一个输入绑定，绑定里包含全部 settings 的摘要。所以换 Key 或改任何配置，都会让它返回 409 `retry_inputs_changed`。另外，用户同意重复使用画面后，阶段 6 会重跑，再次收费（`drafts.py:310-368`、`drafts.py:1361-1369`）。
- **改动前先算最坏调用次数**：重试次数 × 凭证数 × 句数（或镜头数）。不要新增自动重试收费 POST 的逻辑，因为没有幂等键。

## 隐私

- **会发给第三方的数据**：音频（ASR）、关键帧拼图（视觉）、镜头片段（视频向量）、稿件和句子（LLM、TTS、向量），以及 AI 写作的输入。"本地运行不等于完全离线"（README.md:13）。
- 新增发送内容时，要在文档里写明发什么、发给谁；**不扩大发送范围**，例如不要把原始 ASR 全文并进画面理解的请求。

## 测试里怎样模拟

详见 [references/mocking.md](references/mocking.md)。要点：

- **按使用处打补丁**，例如 `backend.uploads.create_asr_provider`、`backend.mode_pipeline.create_tts_provider`、`backend.mode_pipeline.create_asr_provider`。类方法打在类上，例如 `backend.providers.vision.VisionProvider.from_settings`。例外：`workbench.py` 的录音转写在函数内部才导入 `create_asr_provider`，要打在 `backend.providers.asr.create_asr_provider`（见文末“维护者决定保留”第 3 条）。
- **证明没有调用**：工厂打桩成 `side_effect=AssertionError("must not bill")`，一旦被构造测试就失败。**证明只收费一次**：断言 `factory.call_count == 1`、`max_retries == 0`。
- **现成的假实现**：
  - `FakeTTS`、`FakeEmbedding`、`FakeLLM`（`tests/test_workbench.py`）；
  - `FakeVoice`、`FakeVision`（`tests/test_mode_pipeline.py`）；
  - `FakeProvider(ASRProvider)`（`tests/test_upload_sessions.py`）；
  - HTTP 层用 `httpx.MockTransport`（`tests/test_m4_tts.py:133-141`）；
  - 这些范例文件大多不在受保护入口里：`test_upload_sessions` 只在 `--legacy` 里，`test_workbench`、`test_mode_pipeline`、`test_m4_tts`、`test_asr` 都不在。把它们当写法范例读，新测试写进 `tests/test_v2_*.py`。
- **配置**：用 `Settings(_env_file=None, ...)`，凭证留空，base URL 指向 `invalid.test`。

## 步骤（改服务调用相关代码时）

1. **查官方文档**（见下方入口），确认字段、错误码、限流和计费方式。
2. **定位代码**：用上表和 [references/providers.md](references/providers.md)。
3. **改代码**时同步检查：
   - 超时、重试、并发、错误分类（`error_kind`）；
   - 是否会增加调用次数；
   - 新增设置走 golden-mic-api-change 的配置同步清单，TTS 设置还要加进 `voice_signature`；
   - 新增服务商按 golden-mic-backend-map 的改动清单。
4. **先写测试**：用模拟实现，断言调用次数。新测试放进 `tests/test_v2_*.py`，golden-mic-verify 的命令 B 才会运行它。
5. **验证**：用 golden-mic-verify。

## 示例：给 TTS 增加一个请求参数（假设）

1. 查豆包语音 TTS 文档，确认参数名和取值。
2. 在 `config.py` 加设置，同步 `.env.example` 和生产模板（改 `deploy/` 先经批准），并更新 `docs/CONFIGURATION.md`。
3. 在 `tts.py` 构造请求体的地方加字段；把新设置加进 `voice_signature`（`mode_pipeline.py`）。
4. 测试：写在 `tests/test_v2_*.py` 里，用 `httpx.MockTransport` 断言请求体里有新字段（写法参考 `tests/test_m4_tts.py`，那个文件不在受保护入口里）；另写一个缓存键随新设置变化的用例。
5. 交付说明里写明：改这个设置会让旧任务的"再试一次"返回 409 `retry_inputs_changed`。

## 验证

- 测试里没有真实 Key，也不联网：服务商工厂被打桩，"证明不会调用"的用例用 `AssertionError` 哨兵，"只收费一次"的用例断言 `call_count == 1`、`max_retries == 0`。
- 改了超时、重试或并发的，汇报改前 → 改后，以及最坏调用次数（重试次数 × 凭证数 × 句数或镜头数）的变化。
- 改了 TTS 设置的，确认 `voice_signature` 带上了它，并说明旧任务的"再试一次"会返回 `retry_inputs_changed`。
- 用 golden-mic-verify 的命令 B（外部服务全部模拟），不另外调用真实服务来确认。没跑的写"未运行"和原因。

## 官方文档入口（2026-10-21 核实）

- **火山方舟**（Embedding、Chat/Responses、Seedream、Seedance）：<https://ark.volcengine.com/llms.txt>。每页加 `.md` 就是 Markdown 版。常用页面：
  - <https://ark.volcengine.com/region:cn-beijing/docs/multimodal-vectorization-api.md>
  - <https://ark.volcengine.com/region:cn-beijing/docs/chat-api.md>
  - <https://ark.volcengine.com/region:cn-beijing/docs/image-generation-api.md>
  - <https://ark.volcengine.com/region:cn-beijing/docs/create-video-generation-task-api.md>
- **Kimi**：<https://platform.kimi.com/docs/llms.txt>，页面同样加 `.md`。常用页面：
  - <https://platform.kimi.com/docs/api/chat.md>
  - <https://platform.kimi.com/docs/guide/use-kimi-vision-model.md>
  - <https://platform.kimi.com/docs/guide/response_format.md>
  - <https://platform.kimi.com/docs/pricing/limits.md>
  - <https://platform.kimi.com/docs/api/errors.md>

  OpenAPI：<https://platform.kimi.com/docs/openapi.json>。
- **豆包语音**（Seed ASR、Seed TTS）：页面在 `docs.volcengine.com/docs/6561/…` 下。这些页面由前端渲染，抓取工具读不到正文，要在浏览器里查看。代码注释引用的流式识别页：<https://docs.volcengine.com/docs/6561/1354869>（`asr.py:213`）。

## 仍未消除的不一致（只记录）

2026-10-22 先按代码更正了文档（`docs/CONFIGURATION.md`、`docs/TROUBLESHOOTING.md`、交接文档 §4.2），随后改了代码：

- V2 的预转写（`uploads.py::_transcribe`）、录音转写（`workbench.py`）、本人录音（`media_input.py`、`mode_pipeline.py`）都把 ASR 的内部重试关掉（`max_retries = 0`）：一次操作只发一次收费请求，失败直接报告。旧流水线（`pipeline.py`）仍按 `ASR_MAX_RETRIES` 重试。`tests/test_v2_code_doc_agreement.py` 检查每个 V2 调用点。
- `speech_prerequisite` 进了 `models.py` 的 Literal、前端类型和 `parseTask` 白名单。
- 报错里的键名就是要补的键名：Kimi 写 `KIMI_*`，火山方舟写 `VOLCENGINE_*`；画面理解全部失败时按实际服务商列键名（`config_keys`）。
- `GenerativeFillProvider` 构造函数的默认 mode 改为 video，和配置一致。

维护者决定保留、只做记录的：

1. **补画面配置的命名。** 只配 `SEEDANCE_2_0_API_KEY` 就算已配置（`generative.py:49-51`），同一个 key 也用于 Seedream 图片；键名带"2_0"，默认模型却是 seedance-2-5（`config.py:127`）。改键名会让现有环境文件过不了精确 schema 校验，所以不改。
2. **`network`。** `errorKind` 的 `network` 后端从不写入，为兼容旧客户端和测试夹具保留。
3. **录音转写的模拟写法。** `workbench.py` 在函数内部 `from .providers.asr import create_asr_provider`，测试要 patch `backend.providers.asr.create_asr_provider`（调用时才取属性）；`backend.workbench` 里没有这个名字，patch `backend.workbench.create_asr_provider` 会直接报 AttributeError。仓库里还没有用例走这条路径，这个写法是读代码得出的，写第一个用例时先验证。

## 参考

- 细节：[references/providers.md](references/providers.md)、[references/mocking.md](references/mocking.md)、[references/env-keys.md](references/env-keys.md)
- 来源：`backend/providers/*.py`、`backend/config.py`、`backend/uploads.py`、`backend/mode_pipeline.py`、`backend/matching.py`、`backend/vision_pipeline.py`、`backend/tts_pipeline.py`、`backend/drafts.py`、`backend/workbench.py`、`backend/storage.py`、README.md、`docs/CONFIGURATION.md`、`docs/USER_GUIDE.md`、`tests/`。2026-10-21 按工作区代码核对，行号会变，以函数名为准。
