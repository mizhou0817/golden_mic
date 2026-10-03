import asyncio
import base64
import binascii
import json
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, cast

import edge_tts
import httpx

from ..config import Settings
from ..models import TTSWordTiming


TTS_TIMEOUT_SECONDS = 60.0
TTS_MAX_RETRIES = 2
NEWS_CONTEXT_INSTRUCTION_TEMPLATE = (
    "请始终由同一位播音员，以专业、客观、沉稳、清晰的中文电视新闻旁白风格朗读；"
    "全篇保持相同音色、音高、情绪强度、话筒距离和音量，不要切换声线，不要使用广告腔、"
    "聊天腔或夸张情绪；平均语速控制在每分钟约{target_chars_per_minute}个中文字符，"
    "语流连贯、重音克制，短分句之间不要重新起调；数字读音已依据整篇新闻稿语义审校，"
    "请准确朗读年份、编号、型号、金额和数量。"
)
NEWS_CONTEXT_INSTRUCTION = NEWS_CONTEXT_INSTRUCTION_TEMPLATE.format(
    target_chars_per_minute=255,
)


class TTSProviderError(RuntimeError):
    """Raised after a TTS request cannot produce a valid audio file."""


class TTSConfigurationError(TTSProviderError):
    """Raised when TTS provider settings are missing or invalid."""


class TTSProvider(ABC):
    @property
    def supports_word_timings(self) -> bool:
        return False

    def synthesis_profile(self) -> dict[str, object]:
        return {"provider": type(self).__name__}

    async def __aenter__(self) -> "TTSProvider":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        return None

    def set_document_context(self, full_text: str) -> None:
        """Configure optional document-level context before sentence synthesis."""
        return None

    @abstractmethod
    def validate_configuration(self) -> None:
        """Validate provider settings before processing any sentence."""

    @abstractmethod
    async def synthesize(self, text: str, output_path: Path) -> list[TTSWordTiming] | None:
        """Synthesize one sentence to an MP3 file."""


class EdgeTTSProvider(TTSProvider):
    def __init__(
        self,
        *,
        voice: str,
        timeout: float = TTS_TIMEOUT_SECONDS,
        max_retries: int = TTS_MAX_RETRIES,
        backoff_base: float = 1.0,
        target_chars_per_minute: int = 255,
        speech_rate: int = 0,
        loudness_rate: int = 0,
    ) -> None:
        self.voice = voice.strip()
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.0, backoff_base)
        self.target_chars_per_minute = target_chars_per_minute
        self.speech_rate = speech_rate
        self.loudness_rate = loudness_rate

    def validate_configuration(self) -> None:
        if not self.voice:
            raise TTSConfigurationError("TTS_VOICE 不能为空。")

    def synthesis_profile(self) -> dict[str, object]:
        return {
            "provider": "edge",
            "voice": self.voice,
            "speech_rate": self.speech_rate,
            "loudness_rate": self.loudness_rate,
        }

    async def synthesize(self, text: str, output_path: Path) -> list[TTSWordTiming]:
        self.validate_configuration()
        clean_text = text.strip()
        if not clean_text:
            raise TTSProviderError("TTS 输入文本不能为空。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.mp3")
        last_error: Exception | None = None
        total_attempts = self.max_retries + 1

        for attempt in range(total_attempts):
            temporary_path.unlink(missing_ok=True)
            try:
                communicate = edge_tts.Communicate(
                    clean_text,
                    self.voice,
                    rate=f"{self.speech_rate:+d}%",
                    volume=f"{self.loudness_rate:+d}%",
                )
                await asyncio.wait_for(communicate.save(str(temporary_path)), timeout=self.timeout)
                _validate_audio_file(temporary_path)
                temporary_path.replace(output_path)
                return []
            except asyncio.CancelledError:
                temporary_path.unlink(missing_ok=True)
                raise
            except Exception as exc:
                last_error = exc
                temporary_path.unlink(missing_ok=True)
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) if last_error else "未知错误"
        raise TTSProviderError(f"Edge TTS 在 {total_attempts} 次尝试后失败：{detail}") from last_error


class OpenAITTSProvider(TTSProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        voice: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = TTS_TIMEOUT_SECONDS,
        max_retries: int = TTS_MAX_RETRIES,
        backoff_base: float = 1.0,
        target_chars_per_minute: int = 255,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.voice = voice.strip()
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.0, backoff_base)
        self.target_chars_per_minute = target_chars_per_minute
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def synthesis_profile(self) -> dict[str, object]:
        return {
            "provider": "openai-compatible",
            "model": self.model,
            "voice": self.voice,
        }

    def validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("TTS_BASE_URL", self.base_url),
                ("TTS_API_KEY", self.api_key),
                ("TTS_MODEL", self.model),
                ("TTS_VOICE", self.voice),
            )
            if not value
        ]
        if missing:
            raise TTSConfigurationError(f"OpenAI TTS 配置缺失：{', '.join(missing)}。请检查 .env。")
        if not self.base_url.startswith(("http://", "https://")):
            raise TTSConfigurationError("TTS_BASE_URL 必须是 http:// 或 https:// 地址。")

    async def synthesize(self, text: str, output_path: Path) -> list[TTSWordTiming]:
        self.validate_configuration()
        clean_text = text.strip()
        if not clean_text:
            raise TTSProviderError("TTS 输入文本不能为空。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.mp3")
        payload = {
            "model": self.model,
            "input": clean_text,
            "voice": self.voice,
            "response_format": "mp3",
        }
        endpoint = self.base_url if self.base_url.endswith("/audio/speech") else f"{self.base_url}/audio/speech"
        last_error: Exception | None = None
        total_attempts = self.max_retries + 1

        for attempt in range(total_attempts):
            temporary_path.unlink(missing_ok=True)
            try:
                response = await self._client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                if not response.content:
                    raise TTSProviderError("OpenAI TTS API 返回了空音频。")
                await asyncio.to_thread(temporary_path.write_bytes, response.content)
                _validate_audio_file(temporary_path)
                temporary_path.replace(output_path)
                return []
            except asyncio.CancelledError:
                temporary_path.unlink(missing_ok=True)
                raise
            except Exception as exc:
                last_error = _normalize_http_error(exc, "OpenAI TTS")
                temporary_path.unlink(missing_ok=True)
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) if last_error else "未知错误"
        raise TTSProviderError(f"OpenAI TTS 在 {total_attempts} 次尝试后失败：{detail}") from last_error


class VolcengineTTSProvider(TTSProvider):
    @property
    def supports_word_timings(self) -> bool:
        return True

    def __init__(
        self,
        *,
        base_url: str,
        api_keys: str,
        voice_type: str,
        resource_id: str,
        model: str = "",
        app_id: str = "",
        access_token: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = TTS_TIMEOUT_SECONDS,
        max_retries: int = TTS_MAX_RETRIES,
        backoff_base: float = 1.0,
        target_chars_per_minute: int = 255,
        speech_rate: int = 0,
        loudness_rate: int = 0,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_keys = tuple(key.strip() for key in api_keys.split(",") if key.strip())
        self.voice_type = voice_type.strip()
        self.resource_id = resource_id.strip()
        self.model = model.strip()
        self.app_id = app_id.strip()
        self.access_token = access_token.strip()
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.0, backoff_base)
        self.target_chars_per_minute = target_chars_per_minute
        self.speech_rate = speech_rate
        self.loudness_rate = loudness_rate
        self.section_id = ""
        self.context_texts: list[str] = []
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def synthesis_profile(self) -> dict[str, object]:
        return {
            "provider": "volcengine",
            "voice": self.voice_type,
            "resource_id": self.resource_id,
            "model": self.model,
            "sample_rate": 48_000,
            "bit_rate": 160_000,
            "speech_rate": self.speech_rate,
            "loudness_rate": self.loudness_rate,
            "section_id": self.section_id,
        }

    def set_document_context(self, full_text: str) -> None:
        if not full_text.strip():
            self.section_id = ""
            self.context_texts = []
            return
        self.section_id = str(uuid.uuid4())
        self.context_texts = (
            [
                NEWS_CONTEXT_INSTRUCTION_TEMPLATE.format(
                    target_chars_per_minute=self.target_chars_per_minute,
                )
            ]
            if self.resource_id == "seed-tts-2.0"
            else []
        )

    def validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("VOLCENGINE_TTS_BASE_URL", self.base_url),
                ("VOLCENGINE_VOICE_TYPE", self.voice_type),
                ("VOLCENGINE_TTS_RESOURCE_ID", self.resource_id),
            )
            if not value
        ]
        if missing:
            raise TTSConfigurationError(f"火山引擎 TTS 配置缺失：{', '.join(missing)}。请检查 .env。")
        if not self.api_keys and not (self.app_id and self.access_token):
            raise TTSConfigurationError(
                "火山引擎 TTS 鉴权配置缺失：请设置 VOLCENGINE_TTS_API_KEYS，"
                "或同时设置 VOLCENGINE_APP_ID 和 VOLCENGINE_ACCESS_TOKEN。"
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise TTSConfigurationError("VOLCENGINE_TTS_BASE_URL 必须是 http:// 或 https:// 地址。")

    async def synthesize(self, text: str, output_path: Path) -> list[TTSWordTiming]:
        self.validate_configuration()
        clean_text = text.strip()
        if not clean_text:
            raise TTSProviderError("TTS 输入文本不能为空。")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.mp3")
        payload: dict[str, object] = {
            "req_params": {
                "text": clean_text,
                "speaker": self.voice_type,
                "audio_params": {
                    "format": "mp3",
                    "sample_rate": 48_000,
                    "bit_rate": 160_000,
                    "enable_subtitle": True,
                    "speech_rate": self.speech_rate,
                    "loudness_rate": self.loudness_rate,
                },
            }
        }
        req_params = payload["req_params"]
        assert isinstance(req_params, dict)
        additions: dict[str, object] = {}
        if self.context_texts:
            additions["context_texts"] = list(self.context_texts)
        if self.section_id:
            additions["section_id"] = self.section_id
        if additions:
            req_params["additions"] = json.dumps(additions, ensure_ascii=False)
        if self.model:
            req_params["model"] = self.model

        last_error: Exception | None = None
        credential_count = len(self.api_keys) + int(bool(self.app_id and self.access_token))
        total_attempts = max(self.max_retries + 1, credential_count)
        attempted = 0
        for attempt in range(total_attempts):
            attempted = attempt + 1
            temporary_path.unlink(missing_ok=True)
            try:
                headers = self._request_headers(attempt)
                word_timings = await self._stream_audio(payload, headers, temporary_path)
                _validate_audio_file(temporary_path)
                temporary_path.replace(output_path)
                return word_timings
            except asyncio.CancelledError:
                temporary_path.unlink(missing_ok=True)
                raise
            except Exception as exc:
                last_error = _normalize_http_error(exc, "火山引擎 TTS")
                temporary_path.unlink(missing_ok=True)
                if attempt >= total_attempts - 1 or not _is_retryable_volcengine_error(last_error):
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) if last_error else "未知错误"
        raise TTSProviderError(f"火山引擎 TTS 在 {attempted} 次尝试后失败：{detail}") from last_error

    def _request_headers(self, attempt: int) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }
        credential_count = len(self.api_keys) + int(bool(self.app_id and self.access_token))
        credential_index = attempt % credential_count
        if credential_index < len(self.api_keys):
            headers["X-Api-Key"] = self.api_keys[credential_index]
        else:
            headers["X-Api-App-Id"] = self.app_id
            headers["X-Api-Access-Key"] = self.access_token
        return headers

    async def _stream_audio(
        self,
        payload: dict[str, object],
        headers: dict[str, str],
        temporary_path: Path,
    ) -> list[TTSWordTiming]:
        audio_bytes = 0
        word_timings: list[TTSWordTiming] = []
        with temporary_path.open("wb") as audio_file:
            async with self._client.stream("POST", self.base_url, headers=headers, json=payload) as response:
                if response.is_error:
                    await response.aread()
                    response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise TTSProviderError("火山引擎 TTS API 返回了无效的流式 JSON。") from exc
                    if not isinstance(event, dict):
                        raise TTSProviderError("火山引擎 TTS API 返回了无效的流式事件。")
                    typed_event = cast(dict[str, Any], event)

                    code = typed_event.get("code")
                    data = typed_event.get("data")
                    event_words = _word_timings_from_event(typed_event)
                    if event_words:
                        word_timings = _merge_word_timings(word_timings, event_words)
                    if code == 0 and isinstance(data, str) and data:
                        try:
                            chunk = base64.b64decode(data, validate=True)
                        except (binascii.Error, ValueError) as exc:
                            raise TTSProviderError("火山引擎 TTS API 返回了无效的 Base64 音频。") from exc
                        audio_file.write(chunk)
                        audio_bytes += len(chunk)
                        continue
                    if code == 20000000:
                        break
                    if code not in (None, 0):
                        message = str(typed_event.get("message") or "未知错误").strip()
                        raise TTSProviderError(f"火山引擎 TTS 流式响应错误 code={code}：{message}")

        if audio_bytes == 0:
            raise TTSProviderError("火山引擎 TTS API 返回了空音频。")
        return word_timings


def create_tts_provider(
    settings: Settings,
    *,
    target_chars_per_minute: int | None = None,
) -> TTSProvider:
    provider_name = settings.tts_provider.strip().lower()
    resolved_target = target_chars_per_minute or settings.tts_news_target_chars_per_minute
    if provider_name == "edge":
        return EdgeTTSProvider(
            voice=settings.tts_voice,
            target_chars_per_minute=resolved_target,
            speech_rate=settings.tts_provider_speech_rate,
            loudness_rate=settings.tts_provider_loudness_rate,
        )
    if provider_name in {"openai", "openai-compatible"}:
        return OpenAITTSProvider(
            base_url=settings.tts_base_url,
            api_key=settings.tts_api_key,
            model=settings.tts_model,
            voice=settings.tts_voice,
            target_chars_per_minute=resolved_target,
        )
    if provider_name in {"volcengine", "doubao"}:
        return VolcengineTTSProvider(
            base_url=settings.volcengine_tts_base_url,
            api_keys=settings.volcengine_tts_api_keys,
            voice_type=settings.volcengine_voice_type,
            resource_id=settings.volcengine_tts_resource_id,
            model=settings.volcengine_tts_model,
            app_id=settings.volcengine_app_id,
            access_token=settings.volcengine_access_token,
            target_chars_per_minute=resolved_target,
            speech_rate=settings.tts_provider_speech_rate,
            loudness_rate=settings.tts_provider_loudness_rate,
        )
    raise TTSConfigurationError(
        "TTS_PROVIDER 仅支持 volcengine、doubao、edge、openai 或 openai-compatible。"
    )


def _validate_audio_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise TTSProviderError("TTS 未生成有效音频文件。")


def _word_timings_from_event(event: dict[str, Any]) -> list[TTSWordTiming]:
    sentence = event.get("sentence")
    if not isinstance(sentence, dict):
        return []
    typed_sentence = cast(dict[str, Any], sentence)
    raw_words = typed_sentence.get("words")
    if not isinstance(raw_words, list):
        return []
    words = cast(list[object], raw_words)
    result: list[TTSWordTiming] = []
    for item in words:
        if not isinstance(item, dict):
            continue
        typed_item = cast(dict[str, Any], item)
        text = str(typed_item.get("word") or typed_item.get("text") or "").strip()
        start = typed_item.get("startTime", typed_item.get("start_time"))
        end = typed_item.get("endTime", typed_item.get("end_time"))
        confidence = typed_item.get("confidence")
        if not isinstance(start, (str, int, float)) or not isinstance(end, (str, int, float)):
            continue
        if confidence is not None and not isinstance(confidence, (str, int, float)):
            confidence = None
        try:
            start_value = float(start)
            end_value = float(end)
            confidence_value = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            continue
        if not text or start_value < 0 or end_value <= start_value:
            continue
        if confidence_value is not None and not 0.0 <= confidence_value <= 1.0:
            confidence_value = None
        result.append(
            TTSWordTiming(
                text=text,
                start=start_value,
                end=end_value,
                confidence=confidence_value,
            )
        )
    return result


def _merge_word_timings(
    existing: list[TTSWordTiming],
    incoming: list[TTSWordTiming],
) -> list[TTSWordTiming]:
    merged: list[TTSWordTiming] = []
    seen: set[tuple[str, int, int]] = set()
    for word in sorted([*existing, *incoming], key=lambda item: (item.start, item.end, item.text)):
        key = (word.text, round(word.start * 1000), round(word.end * 1000))
        if key in seen:
            continue
        seen.add(key)
        merged.append(word)
    return merged


def _normalize_http_error(exc: Exception, provider_name: str) -> Exception:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip().replace("\n", " ")[:300]
        suffix = f"：{body}" if body else ""
        return TTSProviderError(f"{provider_name} API HTTP {exc.response.status_code}{suffix}")
    return exc


def _is_retryable_volcengine_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    if "http 401" in detail or "http 403" in detail or "http 429" in detail:
        return True
    if "http 5" in detail or "quota exceeded" in detail or "concurrency" in detail:
        return True
    return not isinstance(exc, TTSProviderError)
