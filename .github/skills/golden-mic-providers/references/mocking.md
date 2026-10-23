# 测试里怎样模拟外部服务

原则：

- 测试不联网，也不构造真实的服务商客户端；
- 补丁打在"使用处"；
- 用哨兵证明"不会调用"，用计数证明"只调用一次"。

## 受保护入口提供的兜底

`tests/run_v2_validation.py` 在导入应用前做这些事（`tests/run_core_validation.py:63-88`、`tests/run_core_validation.py:245-325`）：

- **清空环境变量，再写入合成环境**：`APP_ENV=test`、`PYTHON_DOTENV_DISABLED=1`、`HF_HUB_OFFLINE`；所有 base URL 设为 `https://safe.test/v1`，ASR 设为 `wss://safe.test/asr`。
- **屏蔽 dotenv**：pydantic-settings 的 dotenv 读取和 `load_dotenv` 都返回空，真实的 `.env` 文件被隐藏。
- **拦截网络**：拒绝外部 socket 和 DNS；httpx、aiohttp 只放行回环地址；websocket 一律拒绝。

这层拦截只是兜底。测试本身仍要按下面的方式打补丁。

## 补丁位置（按使用处）

| 要替换的东西 | 补丁目标 |
|---|---|
| 预转写的 ASR | `backend.uploads.create_asr_provider`（uploads.py:64），或 `patch.object(UploadStore, "_transcribe", ...)` |
| 制作流程里的 ASR、TTS | `backend.mode_pipeline.create_asr_provider`、`backend.mode_pipeline.create_tts_provider`（mode_pipeline.py:37-41） |
| 开始制作时的媒体准备 | `backend.media_input.create_asr_provider`（media_input.py:30） |
| 画面理解 | `backend.providers.vision.VisionProvider.from_settings`（类方法，打在类上） |
| workbench 里的服务 | `backend.workbench.EmbeddingProvider.from_settings`、`backend.workbench.LLMProvider.from_settings`、`backend.workbench.create_tts_provider` |
| AI 写作 | `patch.object(LLMProvider, "validate_configuration" / "complete_text", AsyncMock(...))`（tests/test_v2_script_assist.py:148-155） |
| 视频向量的全局并发控制器 | 测试前后调用 `reset_video_embedding_controllers()`（tests/test_embedding_concurrency.py:15-21） |

`workbench.py:993` 在函数内部导入 ASR（`from .providers.asr import create_asr_provider`），所以要打在 `backend.providers.asr.create_asr_provider`；`backend.workbench` 里没有这个名字，patch 它会报 AttributeError。仓库里还没有用例走这条路径，这个写法是读代码得出的，写第一个用例时先验证。

## 现成的假实现

受保护入口只运行 `tests/test_v2_*.py`，加 `--legacy` 时再运行 `test_upload_sessions`、`test_mode_workbench` 等 8 个旧模块。下面和上表引用的 `test_workbench.py`、`test_mode_pipeline.py`、`test_asr.py`、`test_m4_tts.py`、`test_embedding_concurrency.py` 不在入口里，只当写法范例读；新测试写进 `tests/test_v2_*.py`。

- `FakeTTS`、`FakeEmbedding`、`FakeLLM`：tests/test_workbench.py:45-86
- `FakeVoice`（用 ffmpeg 生成正弦音调）、`FakeVision`、`FakeContext`、`fake_match`，以及辅助方法 `providers()`：tests/test_mode_pipeline.py:103-158、tests/test_mode_pipeline.py:480-495
- `FakeProvider(ASRProvider)`，会记录音频的 sha：tests/test_upload_sessions.py:116
- `FakeWebSocket`、`FakeConnectFactory`，用来注入 ASR 的 `connect_factory`：tests/test_asr.py:39-74
- `GeneratedAudioProvider`、`AlwaysFailProvider`：tests/test_m4_tts.py:1094-1123
- 回环假服务（`/v1/embeddings`、`/v1/audio/speech` 返回音调、`/v1/chat/completions`）：tests/workspace_fixture.py:143-228
- 验收服务器：`make_settings` 清空所有凭证，并把 URL 都指向回环地址（tests/mode_acceptance_server.py:426-456）

## 可以照抄的写法

```python
from contextlib import ExitStack
from unittest.mock import patch

from backend.config import Settings

settings = Settings(_env_file=None, app_env="test", data_dir=root / "tasks",
                    asr_cache_dir=root / "cache",
                    volcengine_app_id="", volcengine_access_token="",
                    volcengine_asr_base_url="wss://invalid.test/no-network")
with ExitStack() as stack:
    stack.enter_context(patch("backend.mode_pipeline.create_tts_provider", return_value=FakeVoice()))
    stack.enter_context(patch("backend.providers.vision.VisionProvider.from_settings",
                              return_value=FakeVision()))
    asr_factory = stack.enter_context(patch("backend.mode_pipeline.create_asr_provider",
                                            side_effect=AssertionError("must not bill")))
    stack.enter_context(patch("httpx.AsyncHTTPTransport.handle_async_request",
                              side_effect=AssertionError("offline")))
    ...  # 运行被测代码
```

- **证明不会调用**：工厂打成 `side_effect=AssertionError(...)`。参照 tests/test_mode_pipeline.py:483-489、tests/test_mode_workbench.py:239-243。
- **证明只收费一次**：断言 `factory.call_count == 1`、`transcribe.await_count == 1`、`max_retries == 0`，并确认秘密没有写进磁盘。参照 tests/test_mode_pipeline.py:625-644。
- **HTTP 层**：注入 `client=httpx.AsyncClient(transport=httpx.MockTransport(handler))`，并把 `backoff_base` 设为 0，就能断言请求体和请求头，而不用联网。参照 tests/test_m4_tts.py:133-141。断言请求头时，只检查头是否存在或是否等于测试里的假值，不要打印。

## 不要这样做

- 不在测试里设置真实的 Key，也不读 `.env`。
- 不用 `unittest discover` 或 pytest 直接跑测试。用 golden-mic-verify 的入口。
- 不靠"测试环境没配 Key，所以会失败"来证明不会调用：那样测试依赖本机配置，结论也不可靠。
