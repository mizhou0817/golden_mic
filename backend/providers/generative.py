"""Gated generative B-roll/image fill via Volcengine Ark (Seedream image, Seedance 2.5 video).

This provider is inert unless BOTH the operator enables it (``GENERATIVE_FILL_ENABLED`` and
``VOLCENGINE_GEN_API_KEYS``) and a task opts in (``preferences.generative_fill``). It is the
prepared integration point for filling missing visuals when no uploaded footage matches a beat.
Downloads are restricted to trusted Volcengine hosts, forbid redirects, and cap size.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from ..media import MediaProcessingError, run_logged_command
from ..models import AnnotatedShot, EDLItem, GeneratedMediaDisclosureManifest, MatchCandidate, MatchPlanItem, Sentence, VisionQuality, is_generated_media_path
from ..storage import write_text_log
from ..storage import write_json_atomic


IMAGE_ENDPOINT = "images/generations"
VIDEO_TASK_ENDPOINT = "contents/generations/tasks"
ALLOWED_MEDIA_HOST_SUFFIXES = (".volces.com", ".volccdn.com", ".byteimg.com", ".bytedance.com")
MAX_MEDIA_BYTES = 64 * 1024 * 1024
VIDEO_POLL_INTERVAL_SECONDS = 5.0
_VIDEO_WIDTH = 1920
_VIDEO_HEIGHT = 1080


class GenerativeFillError(RuntimeError):
    """Raised when generative fill media cannot be produced."""


class GenerativeFillConfigurationError(GenerativeFillError):
    """Raised when generative fill is requested without valid configuration."""


def generative_fill_configured(settings: Settings) -> bool:
    """True only when the operator has enabled generative fill and provided a key."""
    if not settings.generative_fill_enabled:
        return False
    if settings.seedance_2_0_api_key.strip():
        return True
    return any(key.strip() for key in settings.volcengine_gen_api_keys.split(","))


def filter_generated_media_disclosure(
    task_dir: Path,
    edl: list[EDLItem],
    *,
    output_path: Path | None = None,
) -> Path | None:
    source_path = task_dir / "generated_media_disclosure.json"
    generated_shot_ids = {
        clip.shot_id
        for item in edl
        for clip in item.clips
        if clip.media_origin == "generated" or is_generated_media_path(clip.src)
    }
    if not source_path.is_file():
        if generated_shot_ids:
            raise GenerativeFillError("成片包含 AI 生成画面，但缺少生成媒体披露清单。")
        return None
    try:
        payload = GeneratedMediaDisclosureManifest.model_validate_json(
            source_path.read_text(encoding="utf-8")
        )
        items = [
            item.model_dump(mode="json")
            for item in payload.items
            if item.shot_id in generated_shot_ids
        ]
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise GenerativeFillError("无法读取生成媒体披露清单。") from exc
    disclosed_ids = {int(item["shot_id"]) for item in items}
    missing_ids = sorted(generated_shot_ids - disclosed_ids)
    if missing_ids:
        raise GenerativeFillError(
            "成片 AI 生成镜头缺少披露记录："
            + "、".join(str(shot_id) for shot_id in missing_ids)
        )
    destination = output_path or source_path
    write_json_atomic(
        destination,
        {
            "schema_version": 1,
            "policy": "generated_visuals_are_disclosed_and_not_evidence",
            "items": items,
        },
    )
    return destination


class GenerativeFillProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        image_model: str,
        video_model: str,
        mode: str = "image",
        duration: int = 5,
        ratio: str = "16:9",
        resolution: str = "1080p",
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.image_model = image_model.strip()
        self.video_model = video_model.strip()
        self.mode = mode.strip().lower()
        self.duration = duration
        self.ratio = ratio.strip() or "16:9"
        self.resolution = resolution.strip() or "1080p"
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "GenerativeFillProvider":
        provider = settings.generative_fill_provider.strip().lower()
        if provider not in {"volcengine", "doubao", "ark"}:
            raise GenerativeFillConfigurationError(
                "GENERATIVE_FILL_PROVIDER 目前仅支持 volcengine、doubao 或 ark。"
            )
        first_key = next(
            (key.strip() for key in settings.volcengine_gen_api_keys.split(",") if key.strip()),
            "",
        )
        return cls(
            base_url=settings.volcengine_gen_base_url,
            api_key=settings.seedance_2_0_api_key.strip() or first_key,
            image_model=settings.volcengine_gen_image_model,
            video_model=settings.volcengine_gen_video_model,
            mode=settings.generative_fill_mode,
            duration=settings.generative_fill_duration_seconds,
            resolution=settings.generative_fill_resolution,
            timeout=settings.generative_fill_timeout_seconds,
        )

    async def __aenter__(self) -> "GenerativeFillProvider":
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
                ("VOLCENGINE_GEN_BASE_URL", self.base_url),
                ("VOLCENGINE_GEN_API_KEYS", self.api_key),
            )
            if not value
        ]
        if self.mode == "video" and not self.video_model:
            missing.append("VOLCENGINE_GEN_VIDEO_MODEL")
        if self.mode != "video" and not self.image_model:
            missing.append("VOLCENGINE_GEN_IMAGE_MODEL")
        if missing:
            raise GenerativeFillConfigurationError(
                f"生成式补拍配置缺失：{', '.join(missing)}。请检查 .env。"
            )
        if not self.base_url.startswith("https://"):
            raise GenerativeFillConfigurationError("VOLCENGINE_GEN_BASE_URL 必须是 https:// 地址。")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def generate_image(self, prompt: str, *, size: str = "1280x720") -> bytes:
        self.validate_configuration()
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise GenerativeFillError("生成式补图缺少提示词。")
        payload = {
            "model": self.image_model,
            "prompt": clean_prompt,
            "size": size,
            "response_format": "b64_json",
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/{IMAGE_ENDPOINT}",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip().replace("\n", " ")[:300]
            raise GenerativeFillError(f"生成式补图 HTTP {exc.response.status_code}：{detail}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise GenerativeFillError(f"生成式补图请求失败：{exc}") from exc
        return await self._extract_image_bytes(body)

    async def _extract_image_bytes(self, body: Any) -> bytes:
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise GenerativeFillError("生成式补图响应缺少 data。")
        entry = data[0]
        encoded = entry.get("b64_json")
        if isinstance(encoded, str) and encoded:
            try:
                return base64.b64decode(encoded)
            except (ValueError, TypeError) as exc:
                raise GenerativeFillError("生成式补图 b64_json 解码失败。") from exc
        url = entry.get("url")
        if isinstance(url, str) and url:
            return await self._download_media(url)
        raise GenerativeFillError("生成式补图响应既无 b64_json 也无 url。")

    async def generate_video(self, prompt: str) -> bytes:
        self.validate_configuration()
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise GenerativeFillError("生成式补拍缺少提示词。")
        payload = {
            "model": self.video_model,
            "content": [{"type": "text", "text": clean_prompt}],
            "generate_audio": False,
            "resolution": self.resolution,
            "ratio": self.ratio,
            "duration": self.duration,
            "watermark": False,
            "output_format": "mp4",
        }
        try:
            response = await self._client.post(
                f"{self.base_url}/{VIDEO_TASK_ENDPOINT}",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip().replace("\n", " ")[:300]
            raise GenerativeFillError(f"生成式补拍提交 HTTP {exc.response.status_code}：{detail}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise GenerativeFillError(f"生成式补拍提交失败：{exc}") from exc
        task_id = body.get("id") if isinstance(body, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise GenerativeFillError("生成式补拍未返回任务 ID。")
        video_url = await self._poll_video_task(task_id)
        return await self._download_media(video_url)

    async def _poll_video_task(self, task_id: str) -> str:
        deadline = asyncio.get_event_loop().time() + self.timeout
        endpoint = f"{self.base_url}/{VIDEO_TASK_ENDPOINT}/{task_id}"
        while True:
            try:
                response = await self._client.get(endpoint, headers=self._headers())
                response.raise_for_status()
                body = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise GenerativeFillError(f"生成式补拍轮询失败：{exc}") from exc
            status = str(body.get("status", "")).lower() if isinstance(body, dict) else ""
            if status in {"succeeded", "success", "done"}:
                content = body.get("content") if isinstance(body, dict) else None
                url = content.get("video_url") if isinstance(content, dict) else None
                if isinstance(url, str) and url:
                    return url
                raise GenerativeFillError("生成式补拍完成但缺少 video_url。")
            if status in {"failed", "error", "cancelled"}:
                raise GenerativeFillError(f"生成式补拍任务失败：status={status}。")
            if asyncio.get_event_loop().time() >= deadline:
                raise GenerativeFillError("生成式补拍任务超时。")
            await asyncio.sleep(VIDEO_POLL_INTERVAL_SECONDS)

    async def _download_media(self, url: str) -> bytes:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host.endswith(ALLOWED_MEDIA_HOST_SUFFIXES):
            raise GenerativeFillError("生成式补拍返回的媒体地址不在可信主机白名单内。")
        try:
            async with self._client.stream("GET", url) as response:
                response.raise_for_status()
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_MEDIA_BYTES:
                    raise GenerativeFillError("生成式补拍媒体超过大小上限。")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_MEDIA_BYTES:
                        raise GenerativeFillError("生成式补拍媒体超过大小上限。")
                return bytes(chunks)
        except httpx.HTTPError as exc:
            raise GenerativeFillError(f"生成式补拍媒体下载失败：{exc}") from exc


async def synthesize_fill_clip(
    provider: GenerativeFillProvider,
    task_dir: Path,
    prompt: str,
    *,
    duration: float,
    output_path: Path,
    mode: str | None = None,
) -> Path:
    """Generate media for ``prompt`` and normalize it to a 1080p30 MP4 clip."""
    if duration <= 0:
        raise GenerativeFillError("生成式补拍片段时长必须大于 0。")
    resolved_mode = (mode or provider.mode or "image").lower()
    generated_dir = task_dir / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    normalize_filter = (
        f"scale={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={_VIDEO_WIDTH}:{_VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )
    if resolved_mode == "video":
        raw_path = generated_dir / f"{output_path.stem}_raw.mp4"
        raw_path.write_bytes(await provider.generate_video(prompt))
        command = [
            "ffmpeg", "-y", "-i", str(raw_path), "-t", f"{duration:.6f}",
            "-vf", normalize_filter, "-map", "0:v:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(output_path),
        ]
    else:
        raw_path = generated_dir / f"{output_path.stem}_raw.png"
        raw_path.write_bytes(await provider.generate_image(prompt))
        command = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(raw_path), "-t", f"{duration:.6f}",
            "-vf", normalize_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(output_path),
        ]
    await run_logged_command(command, task_dir, f"生成式补拍片段（{resolved_mode}）")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise GenerativeFillError("生成式补拍片段渲染失败。")
    return output_path


def _build_fill_prompt(sentence_text: str, beat_text: str) -> str:
    focus = (beat_text or sentence_text).strip()
    return (
        "电视新闻纪实风格的真实现场画面，横向 16:9 构图，自然光，无任何文字、字幕或水印。"
        f"画面内容：{focus}。新闻语境：{sentence_text.strip()}"
    )


async def apply_generative_fill(
    task_dir: Path,
    sentences: list[Sentence],
    shots: list[AnnotatedShot],
    match_plan: list[MatchPlanItem],
    provider: GenerativeFillProvider,
    *,
    max_clips: int,
    mode: str = "image",
    clip_duration: float = 5.0,
) -> tuple[list[AnnotatedShot], list[MatchPlanItem], int]:
    """Generate disclosed illustration media for fallback beats.

    Returns (new_synthetic_shots, updated_match_plan, filled_count). Any per-item failure
    is logged and leaves that item's original fallback untouched.
    """
    if max_clips <= 0:
        return [], match_plan, 0
    provider.validate_configuration()
    sentence_by_id = {sentence.sentence_id: sentence for sentence in sentences}
    used_shot_ids = {shot.shot_id for shot in shots}
    next_shot_id = (max(used_shot_ids) + 1) if used_shot_ids else 0
    updated_plan = list(match_plan)
    synthetic_shots: list[AnnotatedShot] = []
    disclosures: list[dict[str, object]] = []
    filled = 0
    for index, item in enumerate(match_plan):
        if filled >= max_clips:
            break
        if not item.is_fallback:
            continue
        sentence = sentence_by_id.get(item.sentence_id)
        source_beats = item.beat_matches or []
        if not source_beats:
            write_text_log(
                task_dir,
                f"生成式补拍跳过 sentence={item.sentence_id}：缺少视觉节拍，保留原兜底",
            )
            continue
        rewired_beats = list(source_beats)
        generated_candidates: list[MatchCandidate] = []
        for beat_index, beat in enumerate(source_beats):
            if filled >= max_clips or not beat.is_fallback:
                continue
            # Generated media must never masquerade as direct evidence for a
            # concrete entity, date, or organization claim.
            if beat.requires_entity_coverage or beat.intent_type in {"entity", "date", "organization"}:
                write_text_log(
                    task_dir,
                    f"生成式补拍跳过事实性节拍 sentence={item.sentence_id} beat={beat.beat_id}",
                )
                continue
            prompt = _build_fill_prompt(
                sentence.text if sentence else item.text,
                beat.text,
            )
            output_path = task_dir / "generated" / f"fill_shot_{next_shot_id}.mp4"
            thumb_path = task_dir / "thumbs" / f"shot_{next_shot_id}.jpg"
            try:
                await synthesize_fill_clip(
                    provider,
                    task_dir,
                    prompt,
                    duration=clip_duration,
                    output_path=output_path,
                    mode=mode,
                )
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                await run_logged_command(
                    [
                        "ffmpeg", "-y", "-ss", f"{clip_duration / 2:.3f}", "-i", str(output_path),
                        "-frames:v", "1", "-q:v", "3", "-update", "1", str(thumb_path),
                    ],
                    task_dir,
                    f"生成式补拍缩略图 shot {next_shot_id}",
                )
            except (GenerativeFillError, MediaProcessingError) as exc:
                write_text_log(
                    task_dir,
                    f"生成式补图失败，保留原兜底 sentence={item.sentence_id} beat={beat.beat_id}：{exc}",
                )
                continue
            synthetic = AnnotatedShot(
                shot_id=next_shot_id,
                source_index=next_shot_id,
                source_scene_index=0,
                source_name=f"generated_fill_{next_shot_id}",
                norm_path=output_path.relative_to(task_dir).as_posix(),
                thumb_path=f"thumbs/shot_{next_shot_id}.jpg",
                start=0.0,
                end=clip_duration,
                duration=clip_duration,
                status="available",
                media_origin="generated",
                description=f"AI生成示意画面：{beat.text}"[:40],
                scene_type="unknown",
                keywords=["AI生成", "示意画面", "非现场实拍"],
                quality=VisionQuality(sharp=0.6, bright=0.6),
            )
            candidate = MatchCandidate(
                shot_id=next_shot_id,
                similarity=0.0,
                combined_score=0.0,
                verification_evidence="AI生成示意画面，不代表新闻现场实拍或事实证明。",
            )
            synthetic_shots.append(synthetic)
            generated_candidates.append(candidate)
            rewired_beats[beat_index] = beat.model_copy(
                update={
                    "shot_id": next_shot_id,
                    # Preserve fallback semantics: generated illustration is a
                    # disclosed fallback, not a semantic evidence success.
                    "is_fallback": True,
                    "confidence": 0.0,
                    "candidates": [candidate],
                }
            )
            disclosures.append(
                {
                    "sentence_id": item.sentence_id,
                    "beat_id": beat.beat_id,
                    "shot_id": next_shot_id,
                    "mode": mode,
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "disclosure_text": "AI生成示意画面",
                }
            )
            used_shot_ids.add(next_shot_id)
            next_shot_id += 1
            filled += 1
        if generated_candidates:
            primary_beat = rewired_beats[0]
            updated_plan[index] = item.model_copy(
                update={
                    "shot_id": primary_beat.shot_id,
                    "confidence": 0.0,
                    "is_fallback": True,
                    "candidates": primary_beat.candidates,
                    "beat_matches": rewired_beats,
                }
            )
    if filled:
        generated_ids = [
            beat.shot_id
            for item in updated_plan
            for beat in item.beat_matches
            if beat.shot_id in used_shot_ids and beat.shot_id >= (synthetic_shots[0].shot_id if synthetic_shots else 0)
        ]
        if len(generated_ids) != len(set(generated_ids)):
            raise GenerativeFillError("生成式补拍产生了重复镜头，已拒绝提交。")
        write_json_atomic(
            task_dir / "generated_media_disclosure.json",
            {
                "schema_version": 1,
                "policy": "generated_visuals_are_disclosed_and_not_evidence",
                "items": disclosures,
            },
        )
        write_text_log(task_dir, f"生成式补图完成，共补 {filled} 个兜底镜头")
    return synthetic_shots, updated_plan, filled
