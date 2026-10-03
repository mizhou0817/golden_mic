import asyncio
import base64
import hashlib
import json
import math
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..storage import sanitize_sensitive_text
from .embedding_concurrency import (
    AdaptiveConcurrencyController,
    get_video_embedding_controller,
)


EMBED_TIMEOUT_SECONDS = 60.0
EMBED_MAX_RETRIES = 2
EMBED_MAX_CONCURRENCY = 8
VOLCENGINE_STS_INSTRUCTIONS = (
    "Target_modality: text.\n"
    "Instruction:Retrieve semantically similar text\n"
    "Query:"
)
VOLCENGINE_QUERY_INSTRUCTIONS = (
    "Target_modality: text.\n"
    "Instruction:根据新闻稿中的视觉实体、人物、动作和场景，检索最匹配的视频镜头元数据\n"
    "Query:"
)
VOLCENGINE_CORPUS_INSTRUCTIONS = "Instruction:Compress the text into one word.\nQuery:"
VOLCENGINE_VIDEO_QUERY_INSTRUCTIONS = (
    "Target_modality: text and video.\n"
    "Instruction:根据新闻稿中的视觉实体、人物、动作和场景，检索最匹配的文本与视频组合镜头\n"
    "Query:"
)
VOLCENGINE_VIDEO_CORPUS_INSTRUCTIONS = "Instruction:Compress the text and video into one word.\nQuery:"


class EmbeddingProviderError(RuntimeError):
    """Raised after an embedding request or response cannot be processed."""


class EmbeddingConfigurationError(EmbeddingProviderError):
    """Raised when required embedding environment variables are missing."""


class EmbeddingHTTPError(EmbeddingProviderError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None,
        retry_after_seconds: float | None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class EmbeddingResponse:
    vector: list[float]
    status_code: int
    request_id: str | None


class EmbeddingProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = EMBED_TIMEOUT_SECONDS,
        max_retries: int = EMBED_MAX_RETRIES,
        backoff_base: float = 1.0,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.max_retries = max(0, max_retries)
        self.backoff_base = max(0.0, backoff_base)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        task_dir: Path | None = None,
    ) -> "EmbeddingProvider":
        return create_embedding_provider(settings, task_dir=task_dir)

    async def __aenter__(self) -> "EmbeddingProvider":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("EMBED_BASE_URL", self.base_url),
                ("EMBED_API_KEY", self.api_key),
                ("EMBED_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise EmbeddingConfigurationError(f"Embedding 配置缺失：{', '.join(missing)}。请检查 .env。")
        if not self.base_url.startswith(("http://", "https://")):
            raise EmbeddingConfigurationError("EMBED_BASE_URL 必须是 http:// 或 https:// 地址。")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.validate_configuration()
        clean_texts = [text.strip() for text in texts]
        if not clean_texts or any(not text for text in clean_texts):
            raise EmbeddingProviderError("Embedding 输入不能为空。")

        payload = {"model": self.model, "input": clean_texts, "encoding_format": "float"}
        last_error: Exception | None = None
        total_attempts = self.max_retries + 1
        for attempt in range(total_attempts):
            try:
                return await self._request_once(payload, len(clean_texts))
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, EmbeddingProviderError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) if last_error else "未知错误"
        raise EmbeddingProviderError(f"Embedding 调用在 {total_attempts} 次尝试后失败：{detail}") from last_error

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self.embed(texts)

    async def embed_corpus(self, texts: Sequence[str]) -> list[list[float]]:
        return await self.embed(texts)

    async def _request_once(self, payload: dict[str, Any], expected_count: int) -> list[list[float]]:
        endpoint = self.base_url if self.base_url.endswith("/embeddings") else f"{self.base_url}/embeddings"
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
        except httpx.HTTPStatusError as exc:
            body = exc.response.text.strip().replace("\n", " ")[:300]
            suffix = f"：{body}" if body else ""
            raise EmbeddingProviderError(f"Embedding API HTTP {exc.response.status_code}{suffix}") from exc

        try:
            response_payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise EmbeddingProviderError("Embedding API 响应不是有效 JSON。") from exc
        if not isinstance(response_payload, dict) or not isinstance(response_payload.get("data"), list):
            raise EmbeddingProviderError("Embedding API 响应缺少 data 数组。")

        indexed_vectors: dict[int, list[float]] = {}
        vector_size: int | None = None
        for fallback_index, item in enumerate(response_payload["data"]):
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise EmbeddingProviderError("Embedding API data 项格式无效。")
            index = item.get("index", fallback_index)
            if not isinstance(index, int) or index < 0 or index >= expected_count or index in indexed_vectors:
                raise EmbeddingProviderError("Embedding API 返回了无效或重复的 index。")
            try:
                vector = [float(value) for value in item["embedding"]]
            except (TypeError, ValueError) as exc:
                raise EmbeddingProviderError("Embedding 向量包含非数字值。") from exc
            if not vector or any(not math.isfinite(value) for value in vector):
                raise EmbeddingProviderError("Embedding 向量为空或包含非有限值。")
            if vector_size is None:
                vector_size = len(vector)
            elif len(vector) != vector_size:
                raise EmbeddingProviderError("Embedding 向量维度不一致。")
            indexed_vectors[index] = vector

        if len(indexed_vectors) != expected_count:
            raise EmbeddingProviderError(f"Embedding 返回数量不匹配：期望 {expected_count}，实际 {len(indexed_vectors)}。")
        return [indexed_vectors[index] for index in range(expected_count)]


class VolcengineMultimodalEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_keys: str,
        model: str,
        dimensions: int = 2048,
        client: httpx.AsyncClient | None = None,
        timeout: float = EMBED_TIMEOUT_SECONDS,
        max_retries: int = EMBED_MAX_RETRIES,
        backoff_base: float = 1.0,
        max_concurrency: int = EMBED_MAX_CONCURRENCY,
        video_fps: float = 0.5,
        video_max_tokens: int = 10240,
        video_concurrency: int = 4,
        video_global_concurrency: int = 4,
        video_global_max_concurrency: int = 8,
        video_min_concurrency: int = 2,
        video_adaptive_concurrency: bool = False,
        video_adaptive_success_window: int = 20,
        video_total_timeout_seconds: float = 300.0,
        video_connect_timeout_seconds: float = 10.0,
        video_write_timeout_seconds: float = 120.0,
        video_read_timeout_seconds: float = 120.0,
        video_pool_timeout_seconds: float = 30.0,
        task_dir: Path | None = None,
    ) -> None:
        self.api_keys = tuple(key.strip() for key in api_keys.split(",") if key.strip())
        self.dimensions = dimensions
        self.max_concurrency = max(1, max_concurrency)
        self.video_fps = max(0.2, min(5.0, video_fps))
        self.video_max_tokens = max(10240, min(204800, video_max_tokens))
        self.video_concurrency = max(1, min(8, video_concurrency))
        self.video_global_concurrency = max(1, min(8, video_global_concurrency))
        self.video_global_max_concurrency = max(
            self.video_global_concurrency,
            min(8, video_global_max_concurrency),
        )
        self.video_min_concurrency = max(
            1,
            min(self.video_global_concurrency, video_min_concurrency),
        )
        self.video_adaptive_concurrency = video_adaptive_concurrency
        self.video_adaptive_success_window = max(5, video_adaptive_success_window)
        self.video_total_timeout_seconds = max(30.0, video_total_timeout_seconds)
        self.video_timeout = httpx.Timeout(
            connect=max(1.0, video_connect_timeout_seconds),
            write=max(10.0, video_write_timeout_seconds),
            read=max(10.0, video_read_timeout_seconds),
            pool=max(1.0, video_pool_timeout_seconds),
        )
        self.task_dir = task_dir
        self.task_id = task_dir.name if task_dir is not None else f"unscoped-{id(self)}"
        super().__init__(
            base_url=base_url,
            api_key=self.api_keys[0] if self.api_keys else "",
            model=model,
            client=client,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
        )
        account_material = "\0".join((self.base_url, *sorted(self.api_keys)))
        self._video_account_key = hashlib.sha256(account_material.encode("utf-8")).hexdigest()
        self._metrics_lock = asyncio.Lock()

    def validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("VOLCENGINE_EMBEDDING_BASE_URL", self.base_url),
                ("VOLCENGINE_VISION_API_KEYS", self.api_keys),
                ("VOLCENGINE_EMBEDDING_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise EmbeddingConfigurationError(
                f"火山引擎 Embedding 配置缺失：{', '.join(missing)}。请检查 .env。"
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise EmbeddingConfigurationError("VOLCENGINE_EMBEDDING_BASE_URL 必须是 http:// 或 https:// 地址。")
        if self.dimensions not in {1024, 2048}:
            raise EmbeddingConfigurationError("VOLCENGINE_EMBEDDING_DIMENSIONS 仅支持 1024 或 2048。")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed_with_instructions(texts, VOLCENGINE_STS_INSTRUCTIONS)

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed_with_instructions(texts, VOLCENGINE_QUERY_INSTRUCTIONS)

    async def embed_corpus(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed_with_instructions(texts, VOLCENGINE_CORPUS_INSTRUCTIONS)

    async def embed_video_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed_with_instructions(texts, VOLCENGINE_VIDEO_QUERY_INSTRUCTIONS)

    async def embed_video_corpus(
        self,
        items: Sequence[tuple[Path, str]],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[list[float] | None]:
        self.validate_configuration()
        semaphore = asyncio.Semaphore(self.video_concurrency)
        progress_lock = asyncio.Lock()
        results: list[list[float] | None] = [None] * len(items)
        completed = 0

        async def embed_one(index: int, video_path: Path, metadata_text: str) -> None:
            nonlocal completed
            async with semaphore:
                try:
                    result = await self._embed_video_one_with_retries(video_path, metadata_text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._record_video_metric(
                        {
                            "event": "video_embedding_final_failure",
                            "shot": video_path.stem,
                            "error_type": type(exc).__name__,
                            "error": _safe_error_detail(exc),
                        }
                    )
                    result = None
            results[index] = result
            async with progress_lock:
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, len(items))

        ordered = sorted(
            enumerate(items),
            key=lambda item: _safe_file_size(item[1][0]),
            reverse=True,
        )
        await asyncio.gather(
            *(embed_one(index, path, text) for index, (path, text) in ordered)
        )
        return results

    async def _embed_with_instructions(
        self,
        texts: Sequence[str],
        instructions: str,
    ) -> list[list[float]]:
        self.validate_configuration()
        clean_texts = [text.strip() for text in texts]
        if not clean_texts or any(not text for text in clean_texts):
            raise EmbeddingProviderError("Embedding 输入不能为空。")

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def embed_one(text: str) -> list[float]:
            async with semaphore:
                return await self._embed_one_with_retries(text, instructions)

        vectors = await asyncio.gather(*(embed_one(text) for text in clean_texts))
        vector_size = len(vectors[0])
        if vector_size != self.dimensions or any(len(vector) != vector_size for vector in vectors):
            raise EmbeddingProviderError(
                f"火山引擎 Embedding 向量维度不匹配：期望 {self.dimensions}，实际 {vector_size}。"
            )
        return vectors

    async def _embed_one_with_retries(self, text: str, instructions: str) -> list[float]:
        payload = {
            "model": self.model,
            "encoding_format": "float",
            "dimensions": self.dimensions,
            "instructions": instructions,
            "input": [{"type": "text", "text": text}],
        }
        last_error: Exception | None = None
        total_attempts = max(self.max_retries + 1, len(self.api_keys))
        for attempt in range(total_attempts):
            try:
                api_key = self.api_keys[attempt % len(self.api_keys)]
                return await self._request_multimodal_once(payload, api_key)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, EmbeddingProviderError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt >= total_attempts - 1:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise EmbeddingProviderError(
            f"火山引擎 Embedding 调用在 {total_attempts} 次尝试后失败：{detail}"
        ) from last_error

    async def _embed_video_one_with_retries(self, video_path: Path, metadata_text: str) -> list[float]:
        if not video_path.is_file() or video_path.stat().st_size <= 0:
            raise EmbeddingProviderError(f"视频向量素材不存在或为空：{video_path.name}")
        if video_path.stat().st_size > 50 * 1024 * 1024:
            raise EmbeddingProviderError(f"视频向量素材超过 50MB：{video_path.name}")
        upload_bytes = video_path.stat().st_size
        video_data = base64.b64encode(await asyncio.to_thread(video_path.read_bytes)).decode("ascii")
        payload = {
            "model": self.model,
            "encoding_format": "float",
            "dimensions": self.dimensions,
            "instructions": VOLCENGINE_VIDEO_CORPUS_INSTRUCTIONS,
            "input": [
                {
                    "type": "video_url",
                    "video_url": {
                        "url": f"data:video/mp4;base64,{video_data}",
                        "fps": self.video_fps,
                        "max_video_tokens": self.video_max_tokens,
                    },
                },
                {"type": "text", "text": metadata_text},
            ],
        }
        last_error: Exception | None = None
        total_attempts = max(self.max_retries + 1, len(self.api_keys))
        total_started = time.monotonic()
        controller = self._get_video_controller()
        for attempt in range(total_attempts):
            if time.monotonic() - total_started >= self.video_total_timeout_seconds:
                break
            api_key = self.api_keys[attempt % len(self.api_keys)]
            lease = None
            provider_latency = 0.0
            provider_started: float | None = None
            try:
                async with controller.slot(self.task_id) as lease:
                    provider_started = time.monotonic()
                    response = await self._request_multimodal_response(
                        payload,
                        api_key,
                        timeout=self.video_timeout,
                    )
                    provider_latency = time.monotonic() - provider_started
                await controller.report_success(provider_latency)
                snapshot = await controller.snapshot()
                await self._record_video_metric(
                    {
                        "event": "video_embedding_attempt",
                        "shot": video_path.stem,
                        "attempt": attempt + 1,
                        "attempts_allowed": total_attempts,
                        "result": "success",
                        "queue_wait_ms": round(lease.queue_wait_seconds * 1000, 3),
                        "provider_latency_ms": round(provider_latency * 1000, 3),
                        "total_elapsed_ms": round((time.monotonic() - total_started) * 1000, 3),
                        "upload_bytes": upload_bytes,
                        "status_code": response.status_code,
                        "request_id": response.request_id,
                        **snapshot,
                    }
                )
                return response.vector
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, EmbeddingProviderError, ValueError, TypeError) as exc:
                last_error = exc
                status_code = exc.status_code if isinstance(exc, EmbeddingHTTPError) else None
                request_id = exc.request_id if isinstance(exc, EmbeddingHTTPError) else None
                retry_after = (
                    exc.retry_after_seconds
                    if isinstance(exc, EmbeddingHTTPError)
                    else None
                )
                if status_code == 429:
                    await controller.report_rate_limit(retry_after)
                elif _is_transient_concurrency_error(exc):
                    await controller.report_transient_failure()
                if provider_started is not None:
                    provider_latency = time.monotonic() - provider_started
                snapshot = await controller.snapshot()
                await self._record_video_metric(
                    {
                        "event": "video_embedding_attempt",
                        "shot": video_path.stem,
                        "attempt": attempt + 1,
                        "attempts_allowed": total_attempts,
                        "result": "error",
                        "queue_wait_ms": round(
                            lease.queue_wait_seconds * 1000,
                            3,
                        )
                        if lease is not None
                        else None,
                        "provider_latency_ms": round(provider_latency * 1000, 3),
                        "total_elapsed_ms": round((time.monotonic() - total_started) * 1000, 3),
                        "upload_bytes": upload_bytes,
                        "status_code": status_code,
                        "request_id": request_id,
                        "retry_after_seconds": retry_after,
                        "error_type": type(exc).__name__,
                        "error": _safe_error_detail(exc),
                        **snapshot,
                    }
                )
                if attempt >= total_attempts - 1 or not _is_retryable_video_error(exc):
                    break
                base_delay = (
                    retry_after
                    if retry_after is not None
                    else self.backoff_base * (2**attempt)
                )
                delay = max(0.0, base_delay)
                if retry_after is None:
                    delay *= random.uniform(0.8, 1.2)
                if (
                    time.monotonic() - total_started + delay
                    >= self.video_total_timeout_seconds
                ):
                    break
                await asyncio.sleep(delay)
        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise EmbeddingProviderError(
            f"火山引擎视频 Embedding 调用失败或超过总时间预算：{detail}"
        ) from last_error

    def _get_video_controller(self) -> AdaptiveConcurrencyController:
        return get_video_embedding_controller(
            self._video_account_key,
            initial_limit=self.video_global_concurrency,
            minimum_limit=self.video_min_concurrency,
            maximum_limit=self.video_global_max_concurrency,
            adaptive=self.video_adaptive_concurrency,
            success_window=self.video_adaptive_success_window,
        )

    async def _record_video_metric(self, payload: dict[str, Any]) -> None:
        if self.task_dir is None:
            return
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": self.task_id,
            **payload,
        }
        async with self._metrics_lock:
            with (self.task_dir / "video_embedding_metrics.ndjson").open(
                "a",
                encoding="utf-8",
            ) as output:
                output.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                output.write("\n")

    async def _request_multimodal_once(
        self,
        payload: dict[str, Any],
        api_key: str,
    ) -> list[float]:
        response = await self._request_multimodal_response(payload, api_key)
        return response.vector

    async def _request_multimodal_response(
        self,
        payload: dict[str, Any],
        api_key: str,
        *,
        timeout: httpx.Timeout | None = None,
    ) -> EmbeddingResponse:
        endpoint = (
            self.base_url
            if self.base_url.endswith("/embeddings/multimodal")
            else f"{self.base_url}/embeddings/multimodal"
        )
        request_options: dict[str, Any] = {
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            "json": payload,
        }
        if timeout is not None:
            request_options["timeout"] = timeout
        response = await self._client.post(endpoint, **request_options)
        request_id = _response_request_id(response)
        if response.status_code >= 400:
            body = response.text.strip().replace("\n", " ")[:300]
            suffix = f"：{body}" if body else ""
            raise EmbeddingHTTPError(
                f"火山引擎 Embedding API HTTP {response.status_code}{suffix}",
                status_code=response.status_code,
                request_id=request_id,
                retry_after_seconds=_parse_retry_after(response),
            )

        try:
            response_payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise EmbeddingProviderError("火山引擎 Embedding API 响应不是有效 JSON。") from exc
        if not isinstance(response_payload, dict) or not isinstance(response_payload.get("data"), dict):
            raise EmbeddingProviderError("火山引擎 Embedding API 响应缺少 data 对象。")
        embedding = response_payload["data"].get("embedding")
        if not isinstance(embedding, list):
            raise EmbeddingProviderError("火山引擎 Embedding API 响应缺少 data.embedding。")
        try:
            vector = [float(value) for value in embedding]
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderError("火山引擎 Embedding 向量包含非数字值。") from exc
        if not vector or any(not math.isfinite(value) for value in vector):
            raise EmbeddingProviderError("火山引擎 Embedding 向量为空或包含非有限值。")
        return EmbeddingResponse(
            vector=vector,
            status_code=response.status_code,
            request_id=request_id,
        )


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _response_request_id(response: httpx.Response) -> str | None:
    for name in (
        "x-request-id",
        "x-tt-logid",
        "x-trace-id",
        "trace-id",
        "request-id",
    ):
        value = response.headers.get(name)
        if value:
            return value[:200]
    return None


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _is_retryable_video_error(error: Exception) -> bool:
    if isinstance(error, EmbeddingHTTPError):
        return error.status_code in {401, 403, 408, 409, 425, 429} or error.status_code >= 500
    return isinstance(
        error,
        (httpx.HTTPError, EmbeddingProviderError, ValueError, TypeError),
    )


def _is_transient_concurrency_error(error: Exception) -> bool:
    if isinstance(error, EmbeddingHTTPError):
        return error.status_code >= 500
    return isinstance(error, (httpx.TimeoutException, httpx.NetworkError))


def _safe_error_detail(error: Exception) -> str:
    detail = sanitize_sensitive_text(str(error).strip() or type(error).__name__)
    return detail[:500]


def create_embedding_provider(
    settings: Settings,
    *,
    task_dir: Path | None = None,
) -> EmbeddingProvider:
    provider_name = settings.embedding_provider.strip().lower()
    if provider_name in {"volcengine", "doubao", "ark"}:
        return VolcengineMultimodalEmbeddingProvider(
            base_url=settings.volcengine_embedding_base_url,
            api_keys=settings.volcengine_vision_api_keys,
            model=settings.volcengine_embedding_model,
            dimensions=settings.volcengine_embedding_dimensions,
            video_fps=settings.video_embedding_fps,
            video_max_tokens=settings.video_embedding_max_video_tokens,
            video_concurrency=settings.video_embedding_concurrency,
            video_global_concurrency=settings.video_embedding_global_concurrency,
            video_global_max_concurrency=settings.video_embedding_global_max_concurrency,
            video_min_concurrency=settings.video_embedding_min_concurrency,
            video_adaptive_concurrency=settings.video_embedding_adaptive_concurrency,
            video_adaptive_success_window=settings.video_embedding_adaptive_success_window,
            video_total_timeout_seconds=settings.video_embedding_total_timeout_seconds,
            video_connect_timeout_seconds=settings.video_embedding_connect_timeout_seconds,
            video_write_timeout_seconds=settings.video_embedding_write_timeout_seconds,
            video_read_timeout_seconds=settings.video_embedding_read_timeout_seconds,
            video_pool_timeout_seconds=settings.video_embedding_pool_timeout_seconds,
            task_dir=task_dir,
        )
    if provider_name in {"openai", "openai-compatible"}:
        return EmbeddingProvider(
            base_url=settings.embed_base_url,
            api_key=settings.embed_api_key,
            model=settings.embed_model,
        )
    raise EmbeddingConfigurationError(
        "EMBEDDING_PROVIDER 仅支持 volcengine、doubao、ark、openai 或 openai-compatible。"
    )
