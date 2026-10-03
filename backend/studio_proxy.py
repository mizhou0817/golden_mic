"""Fixed RAW-source preview profile and bounded, cancellation-drained verification.

No catalog, queue, cache directory, provider or pipeline writes live here. The
Studio router owns authorization, durable jobs and the shared resource leases.
Changing the profile/disclosure implementation requires a version bump.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import threading
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypeVar

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import Field

from .studio_assets import contained, drain, is_image_id
from .studio_render import MAX_BYTES, Clip, ExportOptions, ID, Project, RenderError, StrictModel, Track

DURATION_SECONDS = 120
WIDTH, HEIGHT, FPS = 640, 360, 30
HASH_CHUNK_BYTES = 1024 * 1024
HASH_SECONDS = 60
POLL_SECONDS = 0.25
DISCLOSURE_VERSION = "studio-full-frame-disclosure-v1"
VIDEO_CONTAINERS = frozenset({".mp4", ".mov", ".mkv", ".avi"})
METADATA = ("report.json", "quality_report.json", "generated_media_disclosure.json",
            "edl.json", "shots_annotated.json", "timings.json", "script_structure.json", "subtitle_manifest.json")
T = TypeVar("T")
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", strict=True)]
Stamp = Annotated[list[Annotated[int, Field(strict=True)]], Field(min_length=5, max_length=5)]


def options() -> ExportOptions:
    return ExportOptions(format="mp4", resolution=360, aspect="16:9", fps=30,
                         video_bitrate_kbps=1000, audio_bitrate_kbps=128, subtitles="standard")


def project(source_id: str, duration: float, has_audio: bool) -> Project:
    return Project(tracks=[Track(id="proxy_video", type="video", clips=[
        Clip(id="proxy_source", source_id=source_id, trim=0, start=0, duration=duration,
             fit="contain", mute=not has_audio),
    ])])


def profile() -> dict[str, Any]:
    # Include every default editing control, not just the bitrate/resolution.
    # IDs/duration/audio availability are replaced only by verified source facts.
    return {"version": 2, "scope": "raw_source_preview", "duration_limit": DURATION_SECONDS,
            "options": options().model_dump(), "project_template": project("source", 1, True).model_dump(),
            "audio_absent": "muted_source_with_renderer_silent_bed", "video_codec": "h264", "audio_codec": "aac",
            "disclosure_version": DISCLOSURE_VERSION}


def cache_key(source_sha256: str, pipeline_revision: int, disclosure: bool) -> str:
    document = {"source_sha256": source_sha256, "pipeline_revision": pipeline_revision,
                "profile": profile(), "disclosure": disclosure}
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def source_facts(info: dict[str, Any]) -> tuple[float, bool]:
    """The existing forced-demuxer/SDR probe must have run before this check."""
    duration = float(info["duration"])
    streams = info["streams"]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if (info.get("is_image") or video is None or video.get("disposition", {}).get("attached_pic")
            or not math.isfinite(duration) or not 0 < duration <= DURATION_SECONDS):
        raise RenderError("proxy requires a video source of at most 120 seconds; no stills, audio-only sources or truncation")
    return duration, any(s.get("codec_type") == "audio" for s in streams)


def _stamp(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def file_stamp(root: Path, path: Path) -> list[int]:
    safe = contained(root, path)
    try:
        info = safe.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RenderError("proxy verification requires an unlinked regular file")
        return _stamp(info)
    except OSError as exc:
        raise RenderError("proxy file is unavailable") from exc


def metadata_stamp(root: Path) -> dict[str, list[int] | None]:
    result: dict[str, list[int] | None] = {}
    for name in METADATA:
        path = contained(root, root / name, exists=False)
        result[name] = file_stamp(root, path) if path.exists() else None
    return result


@dataclass(frozen=True)
class FileDigest:
    sha256: str
    fingerprint: list[int]


def _hash_file(root: Path, path: Path, stop: threading.Event, max_bytes: int | None) -> FileDigest:
    """Read a fixed, pre-statted byte count; never chase a growing file to EOF."""
    before = file_stamp(root, path)
    if not 0 < before[2] or (max_bytes is not None and before[2] >= max_bytes):
        raise RenderError("proxy file is empty or exceeds its byte limit")
    deadline = time.monotonic() + HASH_SECONDS
    digest = hashlib.sha256()
    try:
        with contained(root, path).open("rb") as source:
            if _stamp(os.fstat(source.fileno())) != before:
                raise RenderError("proxy file changed while opening")
            remaining = before[2]
            while remaining:
                if stop.is_set() or time.monotonic() >= deadline:
                    raise RenderError("proxy file verification stopped or timed out")
                chunk = source.read(min(HASH_CHUNK_BYTES, remaining))
                if not chunk:
                    raise RenderError("proxy file changed while hashing")
                digest.update(chunk)
                remaining -= len(chunk)
            if _stamp(os.fstat(source.fileno())) != before:
                raise RenderError("proxy file changed while hashing")
        if file_stamp(root, path) != before:
            raise RenderError("proxy file changed while hashing")
    except OSError as exc:
        raise RenderError("proxy file verification failed") from exc
    return FileDigest(digest.hexdigest(), before)


async def hash_file(root: Path, path: Path, *, max_bytes: int | None = None) -> FileDigest:
    stop = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(_hash_file, root, path, stop, max_bytes))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stop.set()
        try:
            await drain(worker)
        except (Exception, asyncio.CancelledError):
            pass
        raise


async def guarded(operation: Coroutine[Any, Any, T], check: Callable[[], None], *, cancel: bool = True) -> T:
    """Watch storage/source invariants, cancel once, then drain before lease release.

    A probe is allowed to finish its bounded command on cancellation so a PID
    acquisition cannot be orphaned. Renderer cancellation uses its existing
    subprocess termination path; repeated caller cancellation cannot interrupt it.
    """
    worker = asyncio.create_task(operation)
    try:
        while not worker.done():
            await asyncio.wait({worker}, timeout=POLL_SECONDS)
            check()
        check()
        return worker.result()
    except BaseException:
        if cancel and not worker.done():
            worker.cancel()
        try:
            await drain(worker)
        except (Exception, asyncio.CancelledError):
            pass
        raise


class Binding(StrictModel):
    source_id: ID
    source_path: str = Field(min_length=1, max_length=2048)
    source_sha256: Digest
    source_fingerprint: Stamp
    pipeline_revision: int = Field(strict=True, ge=0)
    duration: float = Field(gt=0, le=DURATION_SECONDS)
    has_audio: bool = Field(strict=True)
    disclosure: bool = Field(strict=True)
    profile: dict[str, Any]
    metadata: dict[str, Stamp | None]

    @property
    def key(self) -> str:
        return cache_key(self.source_sha256, self.pipeline_revision, self.disclosure)


def validate_snapshot(job: dict[str, Any], snapshot: dict[str, Any]) -> Binding:
    """A relabelled edited render/export can never inherit this private route."""
    binding = Binding.model_validate(snapshot["proxy"])
    if (job.get("kind") != "proxy" or job.get("source_id") != binding.source_id
            or is_image_id(binding.source_id) or binding.profile != profile()
            or type(job.get("pipeline_revision")) is not int or job["pipeline_revision"] != binding.pipeline_revision
            or type(job.get("disclosure")) is not bool or job["disclosure"] != binding.disclosure
            or job.get("cache_key") != binding.key or job.get("options") != options().model_dump()
            or snapshot.get("options") != options().model_dump()
            or snapshot.get("project") != project(binding.source_id, binding.duration, binding.has_audio).model_dump()
            or set(binding.metadata) != set(METADATA)):
        raise RenderError("proxy snapshot is not the immutable raw-source profile")
    return binding


def validate_result(result: dict[str, Any], binding: Binding) -> None:
    streams = result["probe"]["streams"]
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(video) != 1 or len(audio) != 1 or len(streams) != 2:
        raise RenderError("proxy output streams do not match the fixed profile")
    n, d = video[0]["avg_frame_rate"].split("/")
    numerator, denominator = float(n), float(d)
    measured_duration = float(result["probe"]["format"]["duration"])
    if (video[0].get("codec_name") != "h264" or audio[0].get("codec_name") != "aac"
            or str(audio[0].get("sample_rate")) != "48000" or audio[0].get("channels") != 2
            or any(video[0].get(key) != "bt709" for key in ("color_space", "color_transfer", "color_primaries"))
            or (video[0].get("width"), video[0].get("height")) != (WIDTH, HEIGHT)
            or not math.isfinite(numerator) or not math.isfinite(denominator)
            or denominator <= 0 or abs(numerator / denominator - FPS) > 0.01
            or not math.isfinite(measured_duration) or abs(measured_duration - binding.duration) > 0.15
            or result.get("duration") != binding.duration or result.get("file") != "output.mp4"
            or type(result.get("bytes")) is not int or not 0 < result["bytes"] < MAX_BYTES
            or result.get("applied") != options().model_dump() or result.get("disclosure") != binding.disclosure):
        raise RenderError("proxy output does not match the fixed profile")


class VerifiedFileResponse(FileResponse):
    """Keep the verification lease through actual FileResponse/Range delivery."""

    def __init__(self, path: Path, check: Callable[[], None], release: Callable[[], Awaitable[None]]):
        super().__init__(path, media_type="video/mp4",
                         headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        self._proxy_check = check
        self._proxy_release = release

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            try:
                self._proxy_check()  # Also before malformed/multipart Range handling.
            except (RenderError, OSError) as exc:
                raise HTTPException(409, "proxy file changed before media delivery") from exc
            # A path-send extension may finish ASGI before the server opens the
            # file. Stream here so our source/output lease covers actual delivery.
            scope = {**scope, "extensions": {key: value for key, value in scope.get("extensions", {}).items()
                                             if key != "http.response.pathsend"}}
            await super().__call__(scope, receive, send)
        finally:
            await self._proxy_release()