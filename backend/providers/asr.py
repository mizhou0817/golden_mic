import asyncio
import gzip
import json
import math
import struct
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import connect

from ..config import Settings


ASR_TIMEOUT_SECONDS = 60.0
ASR_MAX_RETRIES = 2
ASR_AUDIO_CHUNK_BYTES = 6_400
ASR_MAX_RESPONSE_BYTES = 16 * 1024 * 1024

_MESSAGE_TYPE_FULL_CLIENT_REQUEST = 0x1
_MESSAGE_TYPE_AUDIO_ONLY_REQUEST = 0x2
_MESSAGE_TYPE_FULL_SERVER_RESPONSE = 0x9
_MESSAGE_TYPE_ERROR = 0xF
_FLAG_LAST_PACKET = 0x2
_SERIALIZATION_JSON = 0x1
_COMPRESSION_GZIP = 0x1


class ASRProviderError(RuntimeError):
    """Raised after an ASR request or response cannot be processed."""


class ASRConfigurationError(ASRProviderError):
    """Raised when required ASR environment variables are missing."""


class ASRWord(BaseModel):
    """Provider milliseconds, never interpolated from utterance boundaries.

    The service can return zero-duration words. Preserve that evidence here;
    editorial callers must reject it for word trimming rather than repair it.
    """

    text: str
    start_time_ms: int = Field(ge=0)
    end_time_ms: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    speaker_id: str | None = None

    model_config = ConfigDict(extra="ignore")


class ASRUtterance(BaseModel):
    text: str
    start_time_ms: int = Field(ge=0)
    end_time_ms: int = Field(ge=0)
    definite: bool = True
    speaker_id: str | None = None
    words: list[ASRWord] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    model_config = ConfigDict(extra="ignore")


class ASRTranscript(BaseModel):
    text: str
    duration_ms: int = Field(default=0, ge=0)
    utterances: list[ASRUtterance] = Field(default_factory=list)
    words: list[ASRWord] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class ASRProvider(ABC):
    async def __aenter__(self) -> "ASRProvider":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        return None

    @abstractmethod
    def validate_configuration(self) -> None:
        """Validate provider settings before transcribing audio."""

    @abstractmethod
    async def transcribe(self, audio_path: Path) -> ASRTranscript:
        """Transcribe one local audio file with utterance timestamps."""


class VolcengineASRProvider(ASRProvider):
    def __init__(
        self,
        *,
        base_url: str,
        endpoint_path: str,
        app_id: str,
        access_token: str,
        resource_id: str,
        cluster_id: str = "",
        timeout: float = ASR_TIMEOUT_SECONDS,
        max_retries: int = ASR_MAX_RETRIES,
        backoff_base: float = 1.0,
        audio_chunk_bytes: int = ASR_AUDIO_CHUNK_BYTES,
        enable_nonstream: bool = True,
        enable_speaker_info: bool = True,
        connect_factory: Callable[..., Any] = connect,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.endpoint_path = endpoint_path.strip().strip("/")
        self.app_id = app_id.strip()
        self.access_token = access_token.strip()
        self.resource_id = resource_id.strip()
        self.cluster_id = cluster_id.strip()
        self.timeout = max(1.0, timeout)
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.0, backoff_base)
        self.audio_chunk_bytes = max(1, audio_chunk_bytes)
        self.enable_nonstream = enable_nonstream
        self.enable_speaker_info = enable_speaker_info
        self._connect_factory = connect_factory

    @classmethod
    def from_settings(cls, settings: Settings) -> "VolcengineASRProvider":
        return cls(
            base_url=settings.volcengine_asr_base_url,
            endpoint_path=settings.volcengine_asr_endpoint_path,
            app_id=settings.volcengine_app_id,
            access_token=settings.volcengine_access_token,
            resource_id=settings.volcengine_asr_resource_id,
            cluster_id=settings.volcengine_asr_cluster_id,
        )

    def validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("VOLCENGINE_ASR_BASE_URL", self.base_url),
                ("VOLCENGINE_ASR_ENDPOINT_PATH", self.endpoint_path),
                ("VOLCENGINE_APP_ID", self.app_id),
                ("VOLCENGINE_ACCESS_TOKEN", self.access_token),
                ("VOLCENGINE_ASR_RESOURCE_ID", self.resource_id),
            )
            if not value
        ]
        if missing:
            raise ASRConfigurationError(f"火山引擎 ASR 配置缺失：{', '.join(missing)}。请检查 .env。")
        if not self.base_url.startswith(("ws://", "wss://")):
            raise ASRConfigurationError("VOLCENGINE_ASR_BASE_URL 必须是 ws:// 或 wss:// 地址。")

    async def transcribe(self, audio_path: Path) -> ASRTranscript:
        self.validate_configuration()
        if not audio_path.is_file() or audio_path.stat().st_size <= 0:
            raise ASRProviderError(f"ASR 音频不存在或为空：{audio_path.name}")
        audio_format = _audio_format_for_path(audio_path)

        last_error: Exception | None = None
        total_attempts = self.max_retries + 1
        for attempt in range(total_attempts):
            try:
                return await self._transcribe_once(audio_path, audio_format)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise ASRProviderError(f"火山引擎 ASR 在 {total_attempts} 次尝试后失败：{detail}") from last_error

    async def _transcribe_once(self, audio_path: Path, audio_format: str) -> ASRTranscript:
        connect_id = str(uuid.uuid4())
        headers = {
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": connect_id,
        }
        endpoint = (
            self.base_url
            if self.base_url.endswith(f"/{self.endpoint_path}")
            else f"{self.base_url}/{self.endpoint_path}"
        )
        request_payload = {
            "user": {"uid": connect_id},
            "audio": {
                "format": audio_format,
                "codec": "raw",
                "rate": 16_000,
                "bits": 16,
                "channel": 1,
                "language": "zh-CN",
            },
            "request": {
                "model_name": "bigmodel",
                "enable_nonstream": self.enable_nonstream,
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "show_utterances": True,
                "result_type": "full",
            },
        }
        # Official streaming API (verified 2026-09-28): show_utterances already
        # requests words; there is no documented enable_words switch. ASR 2.0
        # speaker clustering on bigmodel_async requires nonstream + SSD 200.
        # https://docs.volcengine.com/docs/6561/1354869
        if self.enable_speaker_info and self.enable_nonstream:
            request_payload["request"].update({
                "enable_speaker_info": True,
                "ssd_version": "200",
            })

        try:
            async with self._connect_factory(
                endpoint,
                additional_headers=headers,
                open_timeout=self.timeout,
                close_timeout=10,
                max_size=ASR_MAX_RESPONSE_BYTES,
            ) as websocket:
                await websocket.send(_build_full_client_request(request_payload))
                initial_message = await asyncio.wait_for(websocket.recv(), timeout=self.timeout)
                initial_frame = _parse_server_frame(initial_message)
                latest = _transcript_from_payload(initial_frame.payload)
                if initial_frame.final:
                    return latest

                response_timeout = max(
                    self.timeout,
                    audio_path.stat().st_size / 32_000.0 + self.timeout,
                )
                receiver = asyncio.create_task(
                    self._receive_final_transcript(websocket, latest, response_timeout)
                )
                try:
                    await self._send_audio(websocket, audio_path, audio_format)
                    return await receiver
                except BaseException:
                    receiver.cancel()
                    await asyncio.gather(receiver, return_exceptions=True)
                    raise
        except ASRProviderError:
            raise
        except Exception as exc:
            raise ASRProviderError(f"火山引擎 ASR WebSocket 调用失败：{exc}") from exc

    async def _send_audio(self, websocket: Any, audio_path: Path, audio_format: str) -> None:
        with audio_path.open("rb") as audio_file:
            chunk = audio_file.read(self.audio_chunk_bytes)
            while chunk:
                next_chunk = audio_file.read(self.audio_chunk_bytes)
                await websocket.send(_build_audio_request(chunk, final=not next_chunk))
                if next_chunk:
                    # File pretranscription must not sleep through the entire
                    # recording. Yield to the receiver without a realtime delay.
                    # This is not an upstream latency/RTF guarantee.
                    delay = (
                        len(chunk) / 32_000.0
                        if not self.enable_nonstream and audio_format in {"wav", "pcm"}
                        else 0.0
                    )
                    await asyncio.sleep(delay)
                chunk = next_chunk

    async def _receive_final_transcript(
        self,
        websocket: Any,
        latest: ASRTranscript,
        response_timeout: float | None = None,
    ) -> ASRTranscript:
        while True:
            message = await asyncio.wait_for(
                websocket.recv(),
                timeout=response_timeout or self.timeout,
            )
            frame = _parse_server_frame(message)
            transcript = _transcript_from_payload(frame.payload)
            if transcript.text or transcript.utterances or transcript.words or transcript.duration_ms:
                latest = transcript
            if frame.final:
                return latest


class _ServerFrame(BaseModel):
    payload: dict[str, Any]
    final: bool = False


def create_asr_provider(settings: Settings) -> ASRProvider:
    provider_name = settings.asr_provider.strip().lower()
    if provider_name in {"volcengine", "doubao"}:
        return VolcengineASRProvider.from_settings(settings)
    raise ASRConfigurationError("ASR_PROVIDER 仅支持 volcengine 或 doubao。")


def _audio_format_for_path(audio_path: Path) -> str:
    suffix = audio_path.suffix.lower()
    formats = {
        ".mp3": "mp3",
        ".wav": "wav",
        ".wave": "wav",
        ".ogg": "ogg",
        ".pcm": "pcm",
        ".raw": "pcm",
    }
    try:
        return formats[suffix]
    except KeyError as exc:
        raise ASRProviderError("ASR 音频格式仅支持 mp3、wav、ogg 或 16kHz PCM。") from exc


def _build_full_client_request(payload: dict[str, Any]) -> bytes:
    encoded = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    header = _build_header(
        message_type=_MESSAGE_TYPE_FULL_CLIENT_REQUEST,
        flags=0,
        serialization=_SERIALIZATION_JSON,
        compression=_COMPRESSION_GZIP,
    )
    return header + struct.pack(">I", len(encoded)) + encoded


def _build_audio_request(audio: bytes, *, final: bool) -> bytes:
    encoded = gzip.compress(audio)
    header = _build_header(
        message_type=_MESSAGE_TYPE_AUDIO_ONLY_REQUEST,
        flags=_FLAG_LAST_PACKET if final else 0,
        serialization=0,
        compression=_COMPRESSION_GZIP,
    )
    return header + struct.pack(">I", len(encoded)) + encoded


def _build_header(*, message_type: int, flags: int, serialization: int, compression: int) -> bytes:
    return bytes(
        [
            0x11,
            ((message_type & 0x0F) << 4) | (flags & 0x0F),
            ((serialization & 0x0F) << 4) | (compression & 0x0F),
            0x00,
        ]
    )


def _parse_server_frame(message: bytes | str) -> _ServerFrame:
    if not isinstance(message, bytes):
        raise ASRProviderError("火山引擎 ASR 返回了非二进制 WebSocket 消息。")
    if len(message) < 8:
        raise ASRProviderError("火山引擎 ASR 返回的二进制帧过短。")

    header_size = (message[0] & 0x0F) * 4
    message_type = message[1] >> 4
    flags = message[1] & 0x0F
    compression = message[2] & 0x0F
    if header_size < 4 or len(message) < header_size + 4:
        raise ASRProviderError("火山引擎 ASR 返回了无效的帧头。")
    offset = header_size
    sequence: int | None = None

    if message_type == _MESSAGE_TYPE_FULL_SERVER_RESPONSE:
        if flags & 0x1:
            if len(message) < offset + 8:
                raise ASRProviderError("火山引擎 ASR 响应缺少 sequence 或 payload size。")
            sequence = struct.unpack_from(">i", message, offset)[0]
            offset += 4
        payload_bytes, offset = _read_payload(message, offset)
        payload = _decode_payload(payload_bytes, compression)
        return _ServerFrame(payload=payload, final=bool(flags & _FLAG_LAST_PACKET) or (sequence or 0) < 0)

    if message_type == _MESSAGE_TYPE_ERROR:
        if len(message) < offset + 8:
            raise ASRProviderError("火山引擎 ASR 错误帧格式无效。")
        error_code = struct.unpack_from(">I", message, offset)[0]
        payload_bytes, offset = _read_payload(message, offset + 4)
        payload = _decode_payload(payload_bytes, compression)
        message_text = str(payload.get("message") or payload.get("error") or payload)
        raise ASRProviderError(f"火山引擎 ASR 错误 code={error_code}：{message_text}")

    raise ASRProviderError(f"火山引擎 ASR 返回了不支持的消息类型：{message_type}")


def _read_payload(message: bytes, offset: int) -> tuple[bytes, int]:
    if len(message) < offset + 4:
        raise ASRProviderError("火山引擎 ASR 响应缺少 payload size。")
    payload_size = struct.unpack_from(">I", message, offset)[0]
    offset += 4
    if len(message) != offset + payload_size:
        raise ASRProviderError("火山引擎 ASR 响应 payload 长度不匹配。")
    return message[offset : offset + payload_size], offset + payload_size


def _decode_payload(payload: bytes, compression: int) -> dict[str, Any]:
    if compression == _COMPRESSION_GZIP:
        try:
            payload = gzip.decompress(payload)
        except gzip.BadGzipFile as exc:
            raise ASRProviderError("火山引擎 ASR 响应 Gzip 解压失败。") from exc
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ASRProviderError("火山引擎 ASR 响应不是有效 JSON。") from exc
    if not isinstance(decoded, dict):
        raise ASRProviderError("火山引擎 ASR 响应必须是 JSON 对象。")
    return decoded


def _provider_confidence(value: Any) -> float | None:
    # Missing confidence is unknown, not zero, a percentage, or a word average.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return float(value)


def _provider_speaker(item: dict[str, Any]) -> str | None:
    extra = item.get("extra")
    speaker = item.get("speaker_id", item.get("speaker"))
    if speaker is None and isinstance(extra, dict):
        speaker = extra.get("speaker_id")
    if type(speaker) is int:
        return str(speaker) if speaker >= 0 else None
    if isinstance(speaker, str) and speaker.strip() and len(speaker) <= 128:
        if not any(ord(character) < 32 for character in speaker):
            return speaker
    return None


def _provider_times(item: dict[str, Any]) -> tuple[int, int] | None:
    start = item.get("start_time", item.get("start_time_ms"))
    end = item.get("end_time", item.get("end_time_ms"))
    if type(start) is not int or type(end) is not int or not 0 <= start <= end:
        return None
    return start, end


def _provider_words(value: Any) -> list[ASRWord]:
    words: list[ASRWord] = []
    if not isinstance(value, list):
        return words
    for item in value:
        if not isinstance(item, dict):
            continue
        times = _provider_times(item)
        text = item.get("text", item.get("word"))
        if times is None or not isinstance(text, str) or not text.strip():
            continue
        words.append(ASRWord(
            text=text, start_time_ms=times[0], end_time_ms=times[1],
            confidence=_provider_confidence(item.get("confidence")),
            speaker_id=_provider_speaker(item),
        ))
    return words


def _transcript_from_payload(payload: dict[str, Any]) -> ASRTranscript:
    result: Any = payload.get("result")
    if isinstance(result, list):
        result = result[-1] if result else {}
    if not isinstance(result, dict):
        result = {}

    utterances: list[ASRUtterance] = []
    raw_utterances = result.get("utterances")
    if isinstance(raw_utterances, list):
        for item in raw_utterances:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            times = _provider_times(item)
            if not isinstance(text, str) or times is None:
                continue
            utterances.append(
                ASRUtterance(
                    text=text.strip(),
                    start_time_ms=times[0],
                    end_time_ms=times[1],
                    definite=item.get("definite", True) is True,
                    speaker_id=_provider_speaker(item),
                    words=_provider_words(item.get("words")),
                    confidence=_provider_confidence(item.get("confidence")),
                )
            )

    audio_info = payload.get("audio_info")
    duration = audio_info.get("duration", 0) if isinstance(audio_info, dict) else 0
    if type(duration) is not int:
        duration = 0
    return ASRTranscript(
        text=result["text"].strip() if isinstance(result.get("text"), str) else "",
        duration_ms=max(0, duration),
        utterances=utterances,
        words=_provider_words(result.get("words")),
        confidence=_provider_confidence(result.get("confidence")),
    )