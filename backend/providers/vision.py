import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from ..config import Settings
from ..models import VisualEntityVerification, VisionAnnotation


VISION_SYSTEM_PROMPT = """你是新闻视频素材编目员。输入通常是同一镜头按时间排列的多帧联系表，每格左上角标有相对时间。必须综合观察全部帧，而不是只描述一格。只输出严格的 JSON（无任何多余文字、无 markdown 代码块）：
{
    "description": "一句话客观概括整段镜头（谁/在哪/在做什么），中文，不超过40字",
  "scene_type": "indoor 或 outdoor 或 unknown",
  "subjects": ["画面主体，如：医护人员、市民、救护车"],
  "actions": ["正在发生的动作"],
  "keywords": ["3到6个检索关键词"],
    "ocr_texts": ["画面中清晰可辨的招牌、商品名、地名；没有则为空数组"],
    "entities": ["可由画面直接确认的商品、菜品、车辆、机构、地点或人物类别；无法确认则不要猜测"],
  "quality": {"sharp": 0到1, "bright": 0到1}
}
规则：不得仅凭模糊外观臆测品牌、菜名或机构；不确定时使用上位类别。"""
VISION_USER_PROMPT = "请按时间顺序综合观察这组视频镜头帧，并严格按系统要求输出 JSON。"
ENTITY_VERIFICATION_SYSTEM_PROMPT = """你是新闻视频事实核验员。输入是一组同一镜头按时间排列的帧，以及一个目标视觉节拍和实体列表。
只根据画面中可见的物体、人物、动作、文字和场景判断该镜头是否能直接支持目标实体；不得因主题相近而猜测具体商品、菜品、机构或地点。
只输出严格 JSON：
{
    "verified": true或false,
    "confidence": 0到1,
    "matched_entities": ["画面可支持的目标实体"],
    "evidence": "不超过120字的可见证据；不支持时说明缺失证据",
    "preferred_relative_time": 最能体现证据的T+秒数，无法判断则为null
}
只有 confidence >= 0.75 且存在明确可见证据时才可 verified=true。"""
VISION_TIMEOUT_SECONDS = 60.0
VISION_MAX_RETRIES = 2


class VisionProviderError(RuntimeError):
    """Raised after a Vision request or response cannot be processed."""


class VisionConfigurationError(VisionProviderError):
    """Raised when required Vision environment variables are missing."""


def _validate_vision_annotation(content: str) -> VisionAnnotation:
    payload = parse_vision_json(content)
    description = payload.get("description")
    if isinstance(description, str):
        payload["description"] = description.strip()[:40]
    return VisionAnnotation.model_validate(payload)


class VisionProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = VISION_TIMEOUT_SECONDS,
        max_retries: int = VISION_MAX_RETRIES,
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
    def from_settings(cls, settings: Settings) -> "VisionProvider":
        return create_vision_provider(settings)

    async def __aenter__(self) -> "VisionProvider":
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
                ("VISION_BASE_URL", self.base_url),
                ("VISION_API_KEY", self.api_key),
                ("VISION_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise VisionConfigurationError(f"Vision 配置缺失：{', '.join(missing)}。请检查 .env。")
        if not self.base_url.startswith(("http://", "https://")):
            raise VisionConfigurationError("VISION_BASE_URL 必须是 http:// 或 https:// 地址。")

    async def annotate_image(self, image_path: Path) -> VisionAnnotation:
        self.validate_configuration()
        try:
            image_bytes = await asyncio.to_thread(image_path.read_bytes)
        except OSError as exc:
            raise VisionProviderError(f"无法读取镜头关键帧：{image_path.name}；{exc}") from exc
        if not image_bytes:
            raise VisionProviderError(f"镜头关键帧为空：{image_path.name}")

        image_data = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_USER_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                        },
                    ],
                },
            ],
            "temperature": 0,
        }

        last_error: Exception | None = None
        total_attempts = self.max_retries + 1
        for attempt in range(total_attempts):
            try:
                return await self._request_once(payload)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, VisionProviderError, ValueError, TypeError, ValidationError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise VisionProviderError(f"Vision 调用在 {total_attempts} 次尝试后失败：{detail}") from last_error

    async def verify_visual_entities(
        self,
        image_path: Path,
        visual_beat: str,
        entities: list[str],
    ) -> VisualEntityVerification:
        self.validate_configuration()
        try:
            image_bytes = await asyncio.to_thread(image_path.read_bytes)
        except OSError as exc:
            raise VisionProviderError(f"无法读取实体复核联系表：{image_path.name}；{exc}") from exc
        if not image_bytes:
            raise VisionProviderError(f"实体复核联系表为空：{image_path.name}")
        image_data = base64.b64encode(image_bytes).decode("ascii")
        request_text = json.dumps(
            {"visual_beat": visual_beat, "target_entities": entities},
            ensure_ascii=False,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": ENTITY_VERIFICATION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                        },
                    ],
                },
            ],
            "temperature": 0,
        }
        last_error: Exception | None = None
        total_attempts = self.max_retries + 1
        for attempt in range(total_attempts):
            try:
                endpoint = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
                response = await self._client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                content = _extract_message_content(response.json())
                return VisualEntityVerification.model_validate(parse_vision_json(content))
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, VisionProviderError, ValueError, TypeError, ValidationError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))
        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise VisionProviderError(f"实体画面复核在 {total_attempts} 次尝试后失败：{detail}") from last_error

    async def _request_once(self, payload: dict[str, Any]) -> VisionAnnotation:
        endpoint = self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
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
            raise VisionProviderError(f"Vision API HTTP {exc.response.status_code}{suffix}") from exc

        try:
            response_payload = response.json()
        except json.JSONDecodeError as exc:
            raise VisionProviderError("Vision API 响应不是有效 JSON。") from exc

        content = _extract_message_content(response_payload)
        return _validate_vision_annotation(content)


class VolcengineVisionProvider(VisionProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_keys: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = VISION_TIMEOUT_SECONDS,
        max_retries: int = VISION_MAX_RETRIES,
        backoff_base: float = 1.0,
    ) -> None:
        self.api_keys = tuple(key.strip() for key in api_keys.split(",") if key.strip())
        super().__init__(
            base_url=base_url,
            api_key=self.api_keys[0] if self.api_keys else "",
            model=model,
            client=client,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
        )

    def validate_configuration(self) -> None:
        missing = [
            name
            for name, value in (
                ("VOLCENGINE_VISION_BASE_URL", self.base_url),
                ("VOLCENGINE_VISION_API_KEYS", self.api_keys),
                ("VOLCENGINE_VISION_MODEL", self.model),
            )
            if not value
        ]
        if missing:
            raise VisionConfigurationError(f"火山引擎 Vision 配置缺失：{', '.join(missing)}。请检查 .env。")
        if not self.base_url.startswith(("http://", "https://")):
            raise VisionConfigurationError("VOLCENGINE_VISION_BASE_URL 必须是 http:// 或 https:// 地址。")

    async def annotate_image(self, image_path: Path) -> VisionAnnotation:
        self.validate_configuration()
        try:
            image_bytes = await asyncio.to_thread(image_path.read_bytes)
        except OSError as exc:
            raise VisionProviderError(f"无法读取镜头关键帧：{image_path.name}；{exc}") from exc
        if not image_bytes:
            raise VisionProviderError(f"镜头关键帧为空：{image_path.name}")

        image_data = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "model": self.model,
            "thinking": {"type": "disabled"},
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": f"data:image/jpeg;base64,{image_data}",
                        },
                        {
                            "type": "input_text",
                            "text": f"{VISION_SYSTEM_PROMPT}\n\n{VISION_USER_PROMPT}",
                        },
                    ],
                }
            ],
        }

        last_error: Exception | None = None
        total_attempts = max(self.max_retries + 1, len(self.api_keys))
        for attempt in range(total_attempts):
            try:
                return await self._request_responses_once(payload, self.api_keys[attempt % len(self.api_keys)])
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, VisionProviderError, ValueError, TypeError, ValidationError) as exc:
                last_error = exc
                if attempt >= total_attempts - 1:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))

        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise VisionProviderError(f"火山引擎 Vision 调用在 {total_attempts} 次尝试后失败：{detail}") from last_error

    async def verify_visual_entities(
        self,
        image_path: Path,
        visual_beat: str,
        entities: list[str],
    ) -> VisualEntityVerification:
        self.validate_configuration()
        try:
            image_bytes = await asyncio.to_thread(image_path.read_bytes)
        except OSError as exc:
            raise VisionProviderError(f"无法读取实体复核联系表：{image_path.name}；{exc}") from exc
        if not image_bytes:
            raise VisionProviderError(f"实体复核联系表为空：{image_path.name}")
        image_data = base64.b64encode(image_bytes).decode("ascii")
        request_text = json.dumps(
            {"visual_beat": visual_beat, "target_entities": entities},
            ensure_ascii=False,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "thinking": {"type": "disabled"},
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{image_data}"},
                        {
                            "type": "input_text",
                            "text": f"{ENTITY_VERIFICATION_SYSTEM_PROMPT}\n\n{request_text}",
                        },
                    ],
                }
            ],
        }
        last_error: Exception | None = None
        total_attempts = max(self.max_retries + 1, len(self.api_keys))
        for attempt in range(total_attempts):
            try:
                endpoint = self.base_url if self.base_url.endswith("/responses") else f"{self.base_url}/responses"
                response = await self._client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_keys[attempt % len(self.api_keys)]}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                content = _extract_responses_output_text(response.json())
                return VisualEntityVerification.model_validate(parse_vision_json(content))
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, VisionProviderError, ValueError, TypeError, ValidationError) as exc:
                last_error = exc
                if attempt >= total_attempts - 1:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))
        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise VisionProviderError(f"实体画面复核在 {total_attempts} 次尝试后失败：{detail}") from last_error

    async def _request_responses_once(
        self,
        payload: dict[str, Any],
        api_key: str,
    ) -> VisionAnnotation:
        endpoint = self.base_url if self.base_url.endswith("/responses") else f"{self.base_url}/responses"
        try:
            response = await self._client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text.strip().replace("\n", " ")[:300]
            suffix = f"：{body}" if body else ""
            raise VisionProviderError(f"火山引擎 Vision API HTTP {exc.response.status_code}{suffix}") from exc

        try:
            response_payload = response.json()
        except json.JSONDecodeError as exc:
            raise VisionProviderError("火山引擎 Vision API 响应不是有效 JSON。") from exc

        content = _extract_responses_output_text(response_payload)
        return _validate_vision_annotation(content)


class KimiVisionProvider(VisionProvider):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        reasoning_effort: str = "low",
        client: httpx.AsyncClient | None = None,
        timeout: float = VISION_TIMEOUT_SECONDS,
        max_retries: int = VISION_MAX_RETRIES,
        backoff_base: float = 1.0,
    ) -> None:
        self.reasoning_effort = reasoning_effort
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            model=model,
            client=client,
            timeout=timeout,
            max_retries=max_retries,
            backoff_base=backoff_base,
        )

    async def annotate_image(self, image_path: Path) -> VisionAnnotation:
        content = await self._request_visual_json(
            image_path,
            VISION_SYSTEM_PROMPT,
            VISION_USER_PROMPT,
            operation="Kimi Vision",
        )
        return _validate_vision_annotation(content)

    async def verify_visual_entities(
        self,
        image_path: Path,
        visual_beat: str,
        entities: list[str],
    ) -> VisualEntityVerification:
        request_text = json.dumps(
            {"visual_beat": visual_beat, "target_entities": entities},
            ensure_ascii=False,
        )
        content = await self._request_visual_json(
            image_path,
            ENTITY_VERIFICATION_SYSTEM_PROMPT,
            request_text,
            operation="Kimi 实体画面复核",
        )
        return VisualEntityVerification.model_validate(parse_vision_json(content))

    async def _request_visual_json(
        self,
        image_path: Path,
        system_prompt: str,
        user_text: str,
        *,
        operation: str,
    ) -> str:
        self.validate_configuration()
        try:
            image_bytes = await asyncio.to_thread(image_path.read_bytes)
        except OSError as exc:
            raise VisionProviderError(f"无法读取镜头联系表：{image_path.name}；{exc}") from exc
        if not image_bytes:
            raise VisionProviderError(f"镜头联系表为空：{image_path.name}")
        image_data = base64.b64encode(image_bytes).decode("ascii")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                        },
                        {"type": "text", "text": user_text},
                    ],
                },
            ],
            "reasoning_effort": self.reasoning_effort,
            "response_format": {"type": "json_object"},
        }
        endpoint = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
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
                return _extract_message_content(response.json())
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, VisionProviderError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self.backoff_base * (2**attempt))
        detail = str(last_error) or type(last_error).__name__ if last_error else "未知错误"
        raise VisionProviderError(
            f"{operation} 调用在 {self.max_retries + 1} 次尝试后失败：{detail}"
        ) from last_error


def create_vision_provider(settings: Settings) -> VisionProvider:
    provider_name = settings.vision_provider.strip().lower()
    if provider_name in {"kimi", "moonshot"}:
        return KimiVisionProvider(
            base_url=settings.kimi_base_url,
            api_key=settings.kimi_api_key,
            model=settings.kimi_model,
            reasoning_effort=settings.kimi_reasoning_effort,
        )
    if provider_name in {"volcengine", "doubao", "ark"}:
        return VolcengineVisionProvider(
            base_url=settings.volcengine_vision_base_url,
            api_keys=settings.volcengine_vision_api_keys,
            model=settings.volcengine_vision_model,
        )
    if provider_name in {"openai", "openai-compatible"}:
        return VisionProvider(
            base_url=settings.vision_base_url,
            api_key=settings.vision_api_key,
            model=settings.vision_model,
        )
    raise VisionConfigurationError(
        "VISION_PROVIDER 仅支持 kimi、moonshot、volcengine、doubao、ark、openai 或 openai-compatible。"
    )


def parse_vision_json(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    cleaned = re.sub(r"^\s*```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise VisionProviderError(f"Vision JSON 解析失败：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise VisionProviderError("Vision 响应必须是 JSON 对象。")
    return payload


def _extract_message_content(payload: Any) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise VisionProviderError("Vision API 响应缺少 choices[0].message.content。") from exc

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        combined = "".join(text_parts)
        if combined:
            return combined
    raise VisionProviderError("Vision API 返回了不支持的 message.content 格式。")


def _extract_responses_output_text(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("output_text"), str):
        output_text = payload["output_text"].strip()
        if output_text:
            return output_text

    if not isinstance(payload, dict) or not isinstance(payload.get("output"), list):
        raise VisionProviderError("火山引擎 Vision API 响应缺少 output。")

    text_parts: list[str] = []
    for output_item in payload["output"]:
        if not isinstance(output_item, dict):
            continue
        content = output_item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"} and isinstance(part.get("text"), str):
                text_parts.append(part["text"])

    combined = "".join(text_parts).strip()
    if combined:
        return combined
    raise VisionProviderError("火山引擎 Vision API 响应缺少 output_text。")
