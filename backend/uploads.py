"""Private, resumable source uploads; no task submission or application imports.

Integration contract (the parent application owns Origin/CSRF/drain middleware):
  POST /api/uploads {name, bytes, sha256, content_type?}
  PUT  /api/uploads/{id}/chunks/{index}  (zero based, exact Content-Length)
  GET  /api/uploads/{id}; POST /api/uploads/{id}/complete; DELETE .../{id}
  GET  /api/uploads/{id}/thumb and /wave
Every existing-upload endpoint requires X-Upload-Token, including loopback.
Only media GETs also accept ?token=. Returned media URLs contain no credential.

UploadStore(settings) owns settings.data_dir / '_uploads', a single-instance
lock, durable manifests, bounded admission budgets and its own preprocess jobs.
Creation/complete/put_chunk/materialize/close/cleanup_owned are async; read,
authorize and snapshots are synchronous. complete schedules preprocessing and
is idempotent, including ASR failures (no implicit second billable request).
After an interrupted ASR, the task pipeline may explicitly retry using the raw
copy. GET/restart never starts an ASR call. Ready manifests are the durable cache;
cross-upload reuse is restricted to the same owner AND provider/request scope.
New uploads expire 72 hours after creation; existing persisted expiry is kept.
JPEG/PNG/GIF keep their original bytes and represent a 3-second still, with no
audio measurements. The task's prepare_media_inputs owns any derived video.
Photos retain media_input's stricter 50 MiB / 20M-pixel safety ceiling, rather
than the general 500 MiB file limit, including its per-frame GIF checks.

transcript is an object containing strict production_modes segments and separate
precision/word-confidence metadata. Unknown timing/confidence/speakers remain
unknown. Speaker cluster IDs are upload-scoped, not cross-recording identities.
SNR, when measurable, is explicitly an estimate from decoded PCM and ASR spans.
The timeouts are resource ceilings, not an ASR speed or acoustic-quality claim.
"""
from __future__ import annotations

import array
import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import stat
import sys
import threading
import time
import wave
import weakref
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import AsyncIterable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal, TypeVar, TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .instance_lock import InstanceLock, InstanceLockError
from .models import UploadedAsset
from .production_modes import QuoteTake, Speaker, TranscriptSegment, Word
from .providers.asr import ASRTranscript, create_asr_provider
from .speech_analysis import adjacent_snr, speech_present

if TYPE_CHECKING:
    from .config import Settings


CHUNK_SIZE = 8_388_608
MAX_FILE_BYTES = 500 * 1024**2
# Cheap admission/public mirrors of media_input's limits. Import its native
# decoder only inside an owned blocking worker; tests pin these ceilings to it.
MAX_PHOTO_BYTES = 50 * 1024**2
MAX_PHOTO_PIXELS = 20_000_000
PHOTO_DURATION = 3.0
MAX_FILE_SECONDS = 1800.0
MAX_TASK_SECONDS = 3600.0
MAX_OWNER_BYTES = 5 * 1024**3
MAX_OWNER_FILES = 20
MAX_SESSIONS = 100
OWNER_CREATIONS_PER_HOUR = 40
GLOBAL_CREATIONS_PER_HOUR = 200
UPLOAD_TTL_SECONDS = 72 * 3600
MAX_PREPROCESS = 4
IO_BLOCK = 128 * 1024
SAMPLE_RATE = 16_000
WAVE_INTERVAL_MS = 20
WAVE_SAMPLES = SAMPLE_RATE * WAVE_INTERVAL_MS // 1000
MAX_PCM_BYTES = int(MAX_FILE_SECONDS * SAMPLE_RATE * 2)
MAX_JSON_BYTES = 8 * 1024**2
MAX_TRANSCRIPT_MEMORY_BYTES = 16 * 1024**2
MAX_THUMB_BYTES = 2 * 1024**2
QUIET_RMS = 10 ** (-50 / 20)
CHUNK_TIMEOUT_SECONDS = 120.0
_ID = re.compile(r"up_[0-9a-f]{32}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_ROOT_MARKER = b"golden-mic-uploads-v1\n"
_FORMATS = {
    ".mp4": ("mov", "video/mp4"),
    ".mov": ("mov", "video/quicktime"),
    ".mkv": ("matroska,webm", "video/x-matroska"),
    ".avi": ("avi", "video/x-msvideo"),
    # Only a safely decoded, canonical PNG is passed to the image2 demuxer.
    ".jpg": ("image2", "image/jpeg"), ".jpeg": ("image2", "image/jpeg"),
    ".png": ("image2", "image/png"), ".gif": ("image2", "image/gif"),
}
_PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif"})
_PHOTO_ERROR = (
    "图片无法安全预处理；仅支持 JPEG、PNG、GIF，图片安全上限为 50 MiB、2000 万像素，"
    "还须满足配置的宽高及 3 秒时长限制；GIF 限 300 帧、累计 1 亿画布像素且每帧不得越界。"
    "通用单文件 500 MiB 上限不适用于图片。"
)
_PRIVATE_HEADERS = {
    "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
_PREPROCESS_SLOTS: weakref.WeakKeyDictionary[Any, asyncio.Semaphore] = weakref.WeakKeyDictionary()
_MATERIALIZE_SLOTS: weakref.WeakKeyDictionary[Any, asyncio.Semaphore] = weakref.WeakKeyDictionary()
_T = TypeVar("_T")


def _error(code: int, message: str) -> HTTPException:
    return HTTPException(code, message, headers=_PRIVATE_HEADERS)


def _not_found() -> HTTPException:
    return _error(404, "上传不存在或无权访问。")


def _link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _safe_path(path: Path, *, exists: bool = True, directory: bool = False) -> Path:
    """Check BEFORE resolve: symlinks, junctions/reparse points must not disappear."""
    path = Path(os.path.abspath(path))
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == path and exists:
                raise _error(409, "上传文件不完整或已不可用。") from None
            continue
        if _link(info):
            raise _error(409, "上传存储不允许链接或重解析路径。")
        if candidate != path and not stat.S_ISDIR(info.st_mode):
            raise _error(409, "上传存储路径无效。")
        if candidate == path:
            valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
            if not valid or (not directory and info.st_nlink != 1):
                raise _error(409, "上传存储文件类型无效。")
    return path


def _open_file(path: Path, *, exclusive: bool = False) -> BinaryIO:
    path = _safe_path(path, exists=not exclusive)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL if exclusive else os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        before, opened = path.lstat(), os.fstat(descriptor)
        _safe_path(path)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise _error(409, "上传存储在访问期间发生变化。")
        return os.fdopen(descriptor, "wb" if exclusive else "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _read_bytes(path: Path, limit: int) -> bytes:
    with _open_file(path) as source:
        if os.fstat(source.fileno()).st_size > limit:
            raise _error(409, "上传元数据超出安全限制。")
        data = source.read(limit + 1)
    if len(data) > limit:
        raise _error(409, "上传元数据超出安全限制。")
    return data


def _atomic_json(path: Path, value: Any) -> None:
    content = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(content) > MAX_JSON_BYTES:
        raise _error(413, "上传分析结果超出安全限制。")
    _safe_path(path, exists=False)
    temporary = path.with_name(f"write-{secrets.token_hex(16)}.tmp")
    try:
        with _open_file(temporary, exclusive=True) as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        _safe_path(path, exists=False)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            _safe_path(temporary).unlink()


async def _drain(task: asyncio.Task[Any]) -> bool:
    """Finish owned cleanup even when the caller is cancelled more than once."""
    current = asyncio.current_task()
    cancelling = current.cancelling() if current else 0
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = interrupted or bool(current and current.cancelling() > cancelling)
            continue
        except BaseException:
            break
    if not task.cancelled():
        task.exception()
    return interrupted


async def _blocking(function: Callable[..., _T], *args: Any) -> _T:
    stop = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(function, *args, stop))
    try:
        return await asyncio.shield(task)
    except BaseException:
        stop.set()
        await _drain(task)
        raise


def _check_stop(stop: threading.Event) -> None:
    if stop.is_set():
        raise InterruptedError("Upload operation cancelled")


def _digest_file(path: Path, limit: int, stop: threading.Event) -> tuple[str, int]:
    digest, count = hashlib.sha256(), 0
    with _open_file(path) as source:
        before = os.fstat(source.fileno())
        while data := source.read(IO_BLOCK):
            _check_stop(stop)
            count += len(data)
            if count > limit:
                raise _error(409, "上传文件大小与声明不一致。")
            digest.update(data)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise _error(409, "上传文件在读取期间发生变化。")
    return digest.hexdigest(), count


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _CreateUpload(_Strict):
    name: str = Field(min_length=1, max_length=255)
    bytes: int = Field(gt=0, le=MAX_FILE_BYTES, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    content_type: str = Field(default="application/octet-stream", max_length=100)


class _Chunk(_Strict):
    size: int = Field(gt=0, le=CHUNK_SIZE, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Probe(_Strict):
    sec: float = Field(gt=0, le=MAX_FILE_SECONDS)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float = Field(gt=0)
    has_audio: bool
    is_image: bool = False
    audio_offset: float = 0.0
    format_name: str


class _Transcript(_Strict):
    text: str = ""
    segments: list[TranscriptSegment] = Field(default_factory=list)
    speakers: list[Speaker] = Field(default_factory=list)
    precision: Literal["word", "segment", "unavailable"] = "unavailable"
    segment_precision: dict[str, Literal["word", "segment"]] = Field(default_factory=dict)
    word_trim_allowed: bool = False
    word_confidences: dict[str, list[float | None]] = Field(default_factory=dict)
    word_speaker_ids: dict[str, list[str | None]] = Field(default_factory=dict)
    observed_words: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    snr_method: str = "estimated_pcm_rms_asr_spans_v1"
    snr_is_estimate: bool = True


class _Manifest(_Strict):
    version: Literal[1] = 1
    id: str = Field(pattern=r"^up_[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: str
    created_at: float
    expires_at: float
    status: Literal["uploading", "probed", "processing", "ready", "asr_failed", "failed", "interrupted"] = "uploading"
    phase: Literal["upload", "queued", "probe", "waveform", "asr", "done", "error"] = "upload"
    progress: int = Field(default=0, ge=0, le=100)
    chunks: dict[str, _Chunk] = Field(default_factory=dict)
    probe: _Probe | None = None
    has_speech: bool | None = None
    speech_evidence: str = "unknown"
    speech_intervals: list[tuple[float, float]] = Field(default_factory=list)
    transcript: _Transcript = Field(default_factory=_Transcript)
    silences: list[tuple[float, float]] = Field(default_factory=list)
    asr_result: ASRTranscript | None = None
    asr_attempted: bool = False
    asr_cache_key: str | None = None
    asr_cached_at: float | None = None
    asr_cache_hit: bool = False
    thumb: bool = False
    wave: bool = False
    error: str | None = None
    error_code: str | None = None


class _BudgetEntry(_Strict):
    owner: str = Field(pattern=r"^[0-9a-f]{64}$")
    at: float


class _Budgets(_Strict):
    version: Literal[1] = 1
    entries: list[_BudgetEntry] = Field(default_factory=list, max_length=GLOBAL_CREATIONS_PER_HOUR)


class _MaterializedAsset(UploadedAsset):
    # A proper subclass, not an unvalidated model_copy(extra=...) workaround.
    # Keeps this module usable while the parent adds the same field to the base.
    upload_id: str = Field(pattern=r"^up_[0-9a-f]{32}$")


@dataclass
class _Wave:
    rms: list[float]
    counts: list[int]
    samples: int
    silences: list[tuple[float, float]]
    has_speech: bool | None
    evidence: str
    speech_intervals: tuple[tuple[float, float], ...] = ()


def _validate_name(name: str, content_type: str) -> str:
    if not name.strip() or len(name) > 255 or any(ord(c) < 32 or ord(c) == 127 or c in '\\/:<>"|?*' for c in name):
        raise _error(422, "文件名无效。")
    suffix = Path(name).suffix.lower()
    if suffix not in _FORMATS:
        raise _error(415, "仅支持 MP4、MOV、AVI、MKV 视频及 JPG、JPEG、PNG、GIF 图片；不支持 WebM、M4V、WMV。")
    if content_type not in {"application/octet-stream", _FORMATS[suffix][1]}:
        raise _error(415, "文件媒体类型与扩展名不符。")
    return suffix


def _decode_upload_photo(path: Path, settings: Settings, output: Path | None, stop: threading.Event) -> tuple[int, int]:
    """Reuse the production header/decode guards off-thread, never create video.

    Any canonical PNG is owned by the caller and is removed only after this
    worker has drained, including cancellation during native decoding/encoding.
    """
    _check_stop(stop)
    path = _safe_path(path)
    before = path.stat()
    from .media_input import _decode_photo

    if output is not None:
        # Reserve an exclusive private temporary file, not a source replacement.
        with _open_file(output, exclusive=True):
            pass
    dimensions = _decode_photo(path, settings, output)
    after = _safe_path(path).stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise _error(409, "上传图片在解码期间发生变化。")
    if output is not None:
        _safe_path(output)
    _check_stop(stop)
    return dimensions


def _slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _PREPROCESS_SLOTS:
        _PREPROCESS_SLOTS[loop] = asyncio.Semaphore(MAX_PREPROCESS)
    return _PREPROCESS_SLOTS[loop]


class UploadStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(os.path.abspath(settings.data_dir)) / "_uploads"
        self.max_bytes = min(MAX_FILE_BYTES, int(settings.upload_limit_bytes))
        self.max_seconds = min(MAX_FILE_SECONDS, float(settings.max_source_duration_seconds_per_file))
        self.max_total_bytes = min(MAX_OWNER_BYTES, int(settings.total_upload_limit_bytes))
        self.max_total_seconds = min(MAX_TASK_SECONDS, float(settings.max_total_source_duration_seconds))
        self.max_files = min(MAX_OWNER_FILES, int(settings.max_files))
        self._records: dict[str, _Manifest] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._admission = asyncio.Lock()
        self._writers = asyncio.Semaphore(4)
        self._operations: dict[asyncio.Task[Any], int] = {}
        self._cache: OrderedDict[str, tuple[float, ASRTranscript, int]] = OrderedDict()
        self._cache_locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self._closed = False
        # Synchronous admission check, immediately before a paid request. The
        # task adapter supplies ownership/batch policy; legacy uploads retain it.
        self.before_asr: Callable[[_Manifest], None] = lambda record: None
        self._close_task: asyncio.Task[None] | None = None
        self._orphans = 0
        self._budgets = _Budgets()
        self._instance = InstanceLock(self.root / ".lock")
        try:
            _safe_path(self.root, exists=False, directory=True)
            self.root.mkdir(parents=True, exist_ok=True)
            marker = self.root / ".uploads-v1"
            if not marker.exists():
                if any(self.root.iterdir()):
                    raise _error(503, "上传目录未被本服务初始化，拒绝使用已有目录。")
                with _open_file(marker, exclusive=True) as target:
                    target.write(_ROOT_MARKER)
                    target.flush()
                    os.fsync(target.fileno())
            if _read_bytes(marker, 64) != _ROOT_MARKER:
                raise _error(503, "上传目录所有权标记无效。")
            _safe_path(self.root / ".lock", exists=False)
            self._instance.acquire()
            self._restore()
        except HTTPException:
            self._instance.release()
            raise
        except (OSError, ValueError, InstanceLockError):
            self._instance.release()
            raise _error(503, "上传存储不可用或正由另一个服务使用。") from None

    @property
    def reserved_bytes(self) -> int:
        return sum(self._reservation(record.size) for record in self._records.values()) + self._orphans * self._reservation(MAX_FILE_BYTES)

    @staticmethod
    def _reservation(size: int) -> int:
        # Includes chunks + assembly, one retry temp, PCM and bounded metadata.
        # Intentionally conservative: never overcommit already received uploads.
        return 2 * size + CHUNK_SIZE + MAX_PCM_BYTES + 2 * MAX_JSON_BYTES + MAX_THUMB_BYTES

    def _space(self, additional: int = 0) -> None:
        try:
            free = shutil.disk_usage(_safe_path(self.root, directory=True)).free
        except OSError:
            raise _error(507, "无法确认上传存储剩余空间。") from None
        if free < self.settings.minimum_free_disk_bytes + self.reserved_bytes + additional:
            raise _error(507, "上传存储空间不足，请稍后重试。")

    def _available(self) -> None:
        if self._closed:
            raise _error(503, "上传服务正在停止，请稍后重试。")

    @asynccontextmanager
    async def _operation(self):
        self._available()
        task = asyncio.current_task()
        assert task is not None
        self._operations[task] = self._operations.get(task, 0) + 1
        try:
            yield
        finally:
            count = self._operations[task] - 1
            if count:
                self._operations[task] = count
            else:
                del self._operations[task]

    def _directory(self, record: _Manifest) -> Path:
        _safe_path(self.root, directory=True)
        if _read_bytes(self.root / ".uploads-v1", 64) != _ROOT_MARKER:
            raise _error(503, "上传存储所有权标记无效。")
        try:
            return _safe_path(self.root / record.id, directory=True)
        except OSError:
            raise _error(503, "上传存储暂时不可用。") from None

    def _source(self, record: _Manifest) -> Path:
        return _safe_path(self._directory(record) / ("source" + Path(record.name).suffix.lower()))

    def _save(self, record: _Manifest) -> None:
        try:
            _atomic_json(self._directory(record) / "manifest.json", record.model_dump(mode="json"))
        except OSError:
            raise _error(507, "无法安全保存上传状态。") from None

    def _transcript_size(self, record: _Manifest) -> int:
        result_bytes = len(record.asr_result.model_dump_json().encode("utf-8")) if record.asr_result else 0
        return result_bytes + len(record.transcript.model_dump_json().encode("utf-8"))

    def _transcript_budget(self, record: _Manifest, result: ASRTranscript, transcript: _Transcript) -> None:
        size = len(result.model_dump_json().encode("utf-8")) + len(transcript.model_dump_json().encode("utf-8"))
        if size + sum(self._transcript_size(other) for other in self._records.values() if other is not record) > MAX_TRANSCRIPT_MEMORY_BYTES:
            raise _error(503, "上传分析缓存已满，可在正式任务中重试识别。")

    def _restore(self) -> None:
        budgets = self.root / "budgets.json"
        if budgets.exists():
            self._budgets = _Budgets.model_validate_json(_read_bytes(budgets, 64 * 1024))
        total_manifest_bytes = 0
        children = []
        for child in self.root.iterdir():
            if _ID.fullmatch(child.name):
                children.append(child)
                if len(children) > MAX_SESSIONS:
                    raise _error(503, "已有上传数量超出安全限制。")
        total_transcript_bytes = 0
        for child in children:
            try:
                _safe_path(child, directory=True)
                total_manifest_bytes += _safe_path(child / "manifest.json").stat().st_size
                if total_manifest_bytes > 64 * 1024**2:
                    raise _error(503, "上传分析元数据超过内存预算。")
                record = _Manifest.model_validate_json(_read_bytes(child / "manifest.json", MAX_JSON_BYTES))
                _validate_name(record.name, record.content_type)
                if record.id != child.name or not record.created_at < record.expires_at <= record.created_at + UPLOAD_TTL_SECONDS:
                    raise ValueError("Invalid upload manifest identity/expiry")
                if len(record.chunks) > math.ceil(record.size / CHUNK_SIZE):
                    raise ValueError("Too many chunks")
                for key, chunk in list(record.chunks.items()):
                    if not key.isascii() or not key.isdecimal() or str(int(key)) != key:
                        raise ValueError("Invalid chunk index")
                    expected = self._expected(record, int(key))
                    part = child / f"chunk-{int(key):05d}.part"
                    if chunk.size != expected:
                        raise ValueError("Invalid chunk size")
                    if not part.exists() or _safe_path(part).stat().st_size != expected:
                        del record.chunks[key]
                if record.status == "processing":
                    record.status = "asr_failed" if record.asr_attempted and record.probe else "interrupted"
                    record.phase, record.error_code = "error", "INTERRUPTED"
                    record.error = "上传预处理已中断；可在正式任务中重试识别。" if record.asr_attempted else "上传预处理已中断，请重新完成上传。"
                    self._save(record)
                transcript_bytes = self._transcript_size(record)
                if total_transcript_bytes + transcript_bytes > MAX_TRANSCRIPT_MEMORY_BYTES:
                    raise _error(503, "上传分析缓存超过内存预算。")
                total_transcript_bytes += transcript_bytes
                self._records[record.id] = record
                self._locks[record.id] = asyncio.Lock()
            except (OSError, ValueError, HTTPException):
                # Unknown/corrupt directories are not ours to delete. Count a
                # worst-case reservation and exclude them from all reads/jobs.
                self._orphans += 1

    async def create(self, *, name: str, size: int, sha256: str, owner: str, content_type: str = "application/octet-stream") -> dict[str, Any]:
        async with self._operation():
            try:
                payload = _CreateUpload(name=name, bytes=size, sha256=sha256, content_type=content_type)
            except ValidationError:
                raise _error(422, "上传名称、字节数或 SHA-256 声明无效。") from None
            suffix = _validate_name(payload.name, payload.content_type)
            if suffix in _PHOTO_EXTENSIONS and size > min(MAX_PHOTO_BYTES, self.max_bytes):
                raise _error(413, f"图片不能超过 {min(MAX_PHOTO_BYTES, self.max_bytes) / 1024**2:g} MiB；图片安全上限为 50 MiB，不适用通用单文件 500 MiB 上限。")
            if size > self.max_bytes:
                raise _error(413, "单个上传超过大小上限。")
            if not isinstance(owner, str) or not 1 <= len(owner) <= 256 or any(ord(c) < 32 for c in owner):
                raise _error(403, "缺少可信上传会话。")
            owner_hash = hashlib.sha256(owner.encode("utf-8")).hexdigest()
            async with self._admission:
                self._cleanup_stale()
                self._available()
                now = time.time()
                entries = [entry for entry in self._budgets.entries if entry.at > now - 3600]
                own = [record for record in self._records.values() if record.owner_hash == owner_hash]
                if len(self._records) + self._orphans >= MAX_SESSIONS:
                    raise _error(429, "全站上传名额已满，请稍后重试。")
                if len(own) >= self.max_files or sum(record.size for record in own) + size > self.max_total_bytes:
                    raise _error(429, "当前会话最多保留 20 个文件，合计不超过 5 GiB。")
                if len(entries) >= GLOBAL_CREATIONS_PER_HOUR or sum(entry.owner == owner_hash for entry in entries) >= OWNER_CREATIONS_PER_HOUR:
                    raise _error(429, "新建上传过于频繁，请稍后重试。")
                self._space(self._reservation(size))
                upload_id, token = "up_" + secrets.token_hex(16), secrets.token_urlsafe(32)
                record = _Manifest(
                    id=upload_id, name=name, size=size, sha256=sha256.lower(),
                    token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(), owner_hash=owner_hash,
                    content_type=content_type, created_at=now, expires_at=now + UPLOAD_TTL_SECONDS,
                )
                directory = self.root / upload_id
                try:
                    _safe_path(directory, exists=False, directory=True).mkdir(mode=0o700)
                    # Persist the budget first: an interrupted create cannot
                    # evade the hourly budget by deleting its session directory.
                    self._budgets = _Budgets(entries=[*entries, _BudgetEntry(owner=owner_hash, at=now)])
                    _atomic_json(self.root / "budgets.json", self._budgets.model_dump(mode="json"))
                    self._save(record)
                except OSError:
                    self._orphans += 1
                    raise _error(507, "无法安全创建上传会话。") from None
                except BaseException:
                    self._orphans += 1
                    raise
                self._records[upload_id], self._locks[upload_id] = record, asyncio.Lock()
                return {"upload_id": upload_id, "chunk_size": CHUNK_SIZE,
                        "put_url": f"/api/uploads/{upload_id}/chunks/{{index}}", "access_token": token}

    def authorize(self, upload_id: str, token: str | None) -> _Manifest:
        self._available()
        if not isinstance(upload_id, str) or not _ID.fullmatch(upload_id) or not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise _not_found()
        record = self._records.get(upload_id)
        expected = record.token_hash if record is not None else "0" * 64
        if not hmac.compare_digest(hashlib.sha256(token.encode("ascii")).hexdigest(), expected) or record is None or record.expires_at <= time.time():
            raise _not_found()
        try:
            _safe_path(self._directory(record) / "manifest.json")
        except OSError:
            raise _error(503, "上传存储暂时不可用。") from None
        return record

    def read(self, upload_id: str, token: str | None) -> dict[str, Any]:
        return self._public(self.authorize(upload_id, token))

    def _public(self, record: _Manifest) -> dict[str, Any]:
        # Keep durable recovery states private without losing retry eligibility.
        status = record.status
        if status == "probed":
            status = "uploading"  # Metadata is not preprocessing readiness.
        elif status == "processing":
            status = "transcribing" if record.phase == "asr" else "probing"
        elif status in {"asr_failed", "interrupted"}:
            status = "failed"
        return {
            "id": record.id, "name": record.name, "bytes": record.size,
            "sec": record.probe.sec if record.probe else None,
            "is_image": record.probe.is_image if record.probe else None,
            "has_audio": record.probe.has_audio if record.probe else None,
            "width": record.probe.width if record.probe else None,
            "height": record.probe.height if record.probe else None,
            "fps": record.probe.fps if record.probe else None,
            "metadata_only": record.status == "probed",
            "status": status, "has_speech": record.has_speech,
            "speech_evidence": record.speech_evidence, "probe_ok": record.probe is not None,
            "transcript": record.transcript.model_dump(mode="json"),
            "speakers": [speaker.model_dump(mode="json") for speaker in record.transcript.speakers],
            "precision": record.transcript.precision,
            "silences": [list(pair) for pair in record.silences],
            "silence_method": "pcm_rms_le_minus50dbfs_20ms" if record.wave and record.probe and record.probe.has_audio else None,
            "speech_intervals": record.speech_intervals,
            "thumb_url": f"/api/uploads/{record.id}/thumb" if record.thumb else None,
            "wave_url": f"/api/uploads/{record.id}/wave" if record.wave else None,
            "asr_confidence": record.asr_result.confidence if record.asr_result else None,
            "progress": record.progress, "phase": record.phase,
            "chunks": sorted(int(index) for index in record.chunks),
            "received_bytes": sum(chunk.size for chunk in record.chunks.values()),
            "chunk_size": CHUNK_SIZE, "total_chunks": math.ceil(record.size / CHUNK_SIZE),
            "max_bytes": min(MAX_PHOTO_BYTES, self.max_bytes) if Path(record.name).suffix.lower() in _PHOTO_EXTENSIONS else self.max_bytes,
            "max_photo_bytes": min(MAX_PHOTO_BYTES, self.max_bytes), "max_photo_pixels": MAX_PHOTO_PIXELS,
            "max_seconds": self.max_seconds,
            "max_files": self.max_files, "max_total_bytes": self.max_total_bytes,
            "max_total_seconds": self.max_total_seconds, "expires_at": record.expires_at,
            "error": record.error, "error_code": record.error_code,
            "can_materialize": record.probe is not None and record.status in {"ready", "asr_failed"},
            "asr_retry_in_task": record.status == "asr_failed", "asr_cache_hit": record.asr_cache_hit,
        }

    @staticmethod
    def _expected(record: _Manifest, index: int) -> int:
        if type(index) is not int or not 0 <= index < math.ceil(record.size / CHUNK_SIZE):
            raise _error(400, "上传分块索引超出范围。")
        return min(CHUNK_SIZE, record.size - index * CHUNK_SIZE)

    async def put_chunk(self, upload_id: str, token: str | None, index: int, stream: AsyncIterable[bytes], content_length: int | None) -> dict[str, Any]:
        async with self._operation():
            record = self.authorize(upload_id, token)
            expected = self._expected(record, index)
            if type(content_length) is not int:
                raise _error(411, "上传分块必须声明准确的 Content-Length。")
            if content_length != expected or content_length > CHUNK_SIZE:
                raise _error(413, "上传分块长度与预期不一致。")
            if self._writers.locked() or self._locks[upload_id].locked():
                raise _error(409, "该上传正在写入或处理，请稍后重试。")
            async with self._writers, self._locks[upload_id]:
                self.authorize(upload_id, token)
                immutable_retry = record.status != "uploading"
                if immutable_retry and str(index) not in record.chunks:
                    raise _error(409, "上传已完成，不能再写入分块。")
                self._space()
                directory = self._directory(record)
                temporary = directory / f"write-{secrets.token_hex(16)}.tmp"
                destination = directory / f"chunk-{index:05d}.part"
                digest, count = hashlib.sha256(), 0
                try:
                    with _open_file(temporary, exclusive=True) as target:
                        async with asyncio.timeout(CHUNK_TIMEOUT_SECONDS):
                            async for data in stream:
                                if not isinstance(data, bytes):
                                    raise _error(400, "分块流无效。")
                                count += len(data)
                                if count > expected:
                                    raise _error(413, "上传分块实际长度超过声明。")
                                # Do not aggregate ASGI frames or a whole chunk.
                                for offset in range(0, len(data), IO_BLOCK):
                                    view = memoryview(data)[offset:offset + IO_BLOCK]
                                    target.write(view)
                                    digest.update(view)
                                await asyncio.sleep(0)
                        if count != expected:
                            raise _error(400, "上传分块未完整传输。")
                        target.flush()
                        os.fsync(target.fileno())
                    self.authorize(upload_id, token)
                    hexdigest = digest.hexdigest()
                    if destination.exists():
                        old_hash, old_size = await _blocking(_digest_file, destination, expected)
                        if old_hash != hexdigest or old_size != count:
                            raise _error(409, "已存在的分块内容不同，不能覆盖。")
                        if immutable_retry:
                            return {"id": upload_id, "index": index, "received": count, "chunks": sorted(map(int, record.chunks))}
                    else:
                        if immutable_retry:
                            raise _error(409, "已完成上传的分块缺失，不能重新写入。")
                        _safe_path(destination, exists=False)
                        os.replace(temporary, destination)
                    previous, previous_progress = record.chunks.get(str(index)), record.progress
                    record.chunks[str(index)] = _Chunk(size=count, sha256=hexdigest)
                    record.progress = int(40 * sum(chunk.size for chunk in record.chunks.values()) / record.size)
                    try:
                        self._save(record)
                    except BaseException:
                        if previous is None:
                            del record.chunks[str(index)]
                        else:
                            record.chunks[str(index)] = previous
                        record.progress = previous_progress
                        raise
                except TimeoutError:
                    raise _error(408, "上传分块超时，请重试该分块。") from None
                except OSError:
                    raise _error(507, "无法安全保存上传分块。") from None
                finally:
                    if temporary.exists():
                        _safe_path(temporary).unlink()
                return {"id": upload_id, "index": index, "received": count, "chunks": sorted(map(int, record.chunks))}

    def _assemble(self, record: _Manifest, stop: threading.Event) -> None:
        directory = self._directory(record)
        temporary = directory / f"write-{secrets.token_hex(16)}.tmp"
        destination = directory / ("source" + Path(record.name).suffix.lower())
        digest, total = hashlib.sha256(), 0
        try:
            with _open_file(temporary, exclusive=True) as target:
                for index in range(math.ceil(record.size / CHUNK_SIZE)):
                    entry = record.chunks[str(index)]
                    part_hash, count = hashlib.sha256(), 0
                    with _open_file(directory / f"chunk-{index:05d}.part") as source:
                        while data := source.read(IO_BLOCK):
                            _check_stop(stop)
                            count += len(data)
                            total += len(data)
                            if count > entry.size or total > record.size:
                                raise _error(409, "上传分块大小校验失败。")
                            digest.update(data)
                            part_hash.update(data)
                            target.write(data)
                    if count != entry.size or part_hash.hexdigest() != entry.sha256:
                        raise _error(409, "上传分块完整性校验失败。")
                if total != record.size or digest.hexdigest() != record.sha256:
                    raise _error(409, "上传文件大小或 SHA-256 校验失败。")
                target.flush()
                os.fsync(target.fileno())
            _check_stop(stop)
            _safe_path(destination, exists=False)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                _safe_path(temporary).unlink()

    async def probe_only(self, upload_id: str, token: str | None) -> dict[str, Any]:
        """Verify owned original bytes and metadata only; never schedule ASR.

        Persist a distinct immutable state so chunk writes cannot invalidate a
        successful probe. Restart and GET preserve this state without work.
        """
        async with self._operation():
            record = self.authorize(upload_id, token)
            if record.status != "uploading":
                return self._public(record)
            if self._locks[upload_id].locked():
                raise _error(409, "上传仍在写入，请稍后重试。")
            async with self._locks[upload_id], _slots():
                self.authorize(upload_id, token)
                if record.status != "uploading":
                    return self._public(record)
                if set(record.chunks) != {str(i) for i in range(math.ceil(record.size / CHUNK_SIZE))}:
                    raise _error(409, "上传分块尚未齐全。")
                self._space()
                await _blocking(self._assemble, record)
                probe = await self._probe(record)
                self.authorize(upload_id, token)
                previous = record.probe, record.status
                record.probe, record.status = probe, "probed"
                try:
                    self._save(record)
                except BaseException:
                    record.probe, record.status = previous
                    raise
                return self._public(record)

    async def complete(self, upload_id: str, token: str | None) -> dict[str, Any]:
        async with self._operation():
            record = self.authorize(upload_id, token)
            if record.status not in {"uploading", "probed", "interrupted"}:
                return self._public(record)
            if self._locks[upload_id].locked():
                raise _error(409, "该上传仍在写入或导入，请稍后重试。")
            async with self._locks[upload_id]:
                self.authorize(upload_id, token)
                if record.status not in {"uploading", "probed", "interrupted"}:
                    return self._public(record)
                if set(record.chunks) != {str(i) for i in range(math.ceil(record.size / CHUNK_SIZE))}:
                    raise _error(409, "上传分块尚未齐全，请继续断点上传。")
                self._space()
                try:
                    await _blocking(self._assemble, record)
                except OSError:
                    raise _error(507, "无法安全合并上传分块。") from None
                self.authorize(upload_id, token)
                previous = record.status, record.phase, record.progress, record.error, record.error_code
                record.status, record.phase, record.progress = "processing", "queued", 45
                record.error = record.error_code = None
                try:
                    self._save(record)
                except BaseException as exc:
                    record.status, record.phase, record.progress, record.error, record.error_code = previous
                    if isinstance(exc, OSError):
                        raise _error(507, "无法安全保存上传完成状态，请重试完成请求。") from None
                    raise
                job = asyncio.create_task(self._preprocess(record), name=f"upload-preprocess-{record.id}")
                self._jobs[record.id] = job
                job.add_done_callback(lambda done, key=record.id: self._job_done(key, done))
                return self._public(record)

    def _job_done(self, key: str, task: asyncio.Task[None]) -> None:
        if self._jobs.get(key) is task:
            del self._jobs[key]
        if not task.cancelled():
            task.exception()  # Retrieved, never log raw provider/path/credential errors.

    def _timeout(self, seconds: float) -> float:
        return max(5.0, min(float(self.settings.media_command_timeout_seconds), seconds * 0.5))

    @staticmethod
    def _input_options(record: _Manifest) -> list[str]:
        formats = _FORMATS[Path(record.name).suffix.lower()][0]
        options = ["-protocol_whitelist", "file,pipe", "-format_whitelist", formats,
                   "-probesize", str(CHUNK_SIZE), "-analyzeduration", "10000000"]
        if formats == "mov":
            options += ["-enable_drefs", "0", "-use_absolute_path", "0"]
        return options

    async def _probe(self, record: _Manifest) -> _Probe:
        suffix = Path(record.name).suffix.lower()
        if suffix in _PHOTO_EXTENSIONS:
            try:
                if PHOTO_DURATION > self.max_seconds:
                    raise ValueError("Photo duration exceeds configured limit")
                width, height = await _blocking(_decode_upload_photo, self._source(record), self.settings, None)
                # fps=1 describes the still input, not a measured video cadence.
                return _Probe(sec=PHOTO_DURATION, width=width, height=height, fps=1.0,
                              is_image=True, has_audio=False, audio_offset=0.0,
                              format_name="jpeg" if suffix in {".jpg", ".jpeg"} else suffix[1:])
            except Exception:
                raise _error(415, _PHOTO_ERROR) from None
        raw = await _run_media([
            "ffprobe", "-v", "error", "-max_alloc", "134217728", *self._input_options(record),
            "-show_entries", "format=format_name,duration,start_time:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration,start_time",
            "-of", "json", str(self._source(record)),
        ], timeout=15.0, limit=256 * 1024)
        try:
            payload = json.loads(raw)
            streams, info = payload["streams"], payload["format"]
            if not isinstance(streams, list) or not 1 <= len(streams) <= 32:
                raise ValueError("Invalid streams")
            video = next(s for s in streams if s.get("codec_type") == "video")
            audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
            names = set(info["format_name"].split(","))
            formats = set(_FORMATS[Path(record.name).suffix.lower()][0].split(","))
            if not names & formats or names - {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi"}:
                raise ValueError("Invalid container")
            origin = _number(info.get("start_time", 0))
            durations = []
            if info.get("duration") is not None:
                durations.append(_number(info["duration"]))
            for stream in streams:
                if stream.get("codec_type") in {"video", "audio"} and stream.get("duration") is not None:
                    durations.append(_number(stream["duration"]) + _number(stream.get("start_time", origin)) - origin)
            sec = max(durations)
            width, height = video["width"], video["height"]
            rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate"))
            numerator, separator, denominator = rate.partition("/")
            fps = _number(numerator) / (_number(denominator) if separator else 1)
            if type(width) is not int or type(height) is not int or not 0 < width <= self.settings.max_source_width or not 0 < height <= self.settings.max_source_height:
                raise ValueError("Invalid dimensions")
            if not 0 < fps <= self.settings.max_source_frame_rate or not 0 < sec <= self.max_seconds:
                raise ValueError("Invalid duration/framerate")
            return _Probe(sec=sec, width=width, height=height, fps=fps, has_audio=audio is not None,
                          audio_offset=_number(audio.get("start_time", origin)) - origin if audio else 0.0,
                          format_name=info["format_name"])
        except (ValueError, KeyError, TypeError, StopIteration, AttributeError, ZeroDivisionError):
            raise _error(415, "视频格式、时长、帧率或分辨率不符合上传要求。") from None

    async def _thumbnail(self, record: _Manifest) -> None:
        assert record.probe is not None
        directory = self._directory(record)
        thumb = directory / "thumb.jpg"
        if thumb.exists():
            _safe_path(thumb).unlink()
        temporary = directory / f"write-{secrets.token_hex(16)}.tmp" if record.probe.is_image else None
        try:
            if temporary is not None:
                await _blocking(_decode_upload_photo, self._source(record), self.settings, temporary)
                source = _safe_path(temporary)
                options = ["-protocol_whitelist", "file,pipe", "-format_whitelist", "image2",
                           "-f", "image2", "-pattern_type", "none", "-c:v", "png"]
            else:
                source, options = self._source(record), self._input_options(record)
            await _run_media([
                "ffmpeg", "-v", "error", "-nostdin", "-n", "-max_alloc", "134217728",
                "-threads", "1", "-filter_threads", "1", *options, "-i", str(source),
                "-map", "0:v:0", "-an", "-sn", "-dn", "-frames:v", "1",
                "-vf", "scale=480:480:force_original_aspect_ratio=decrease", "-c:v", "mjpeg",
                "-threads", "1", "-q:v", "3", "-f", "image2", "-update", "1", str(thumb),
            ], timeout=self._timeout(record.probe.sec), limit=1024)
        finally:
            if temporary is not None and temporary.exists():
                _safe_path(temporary).unlink()
        if not 0 < _safe_path(thumb).stat().st_size <= MAX_THUMB_BYTES:
            raise _error(415, "无法安全生成素材首帧缩略图。")
        record.thumb = True

    async def _previews(self, record: _Manifest) -> _Wave:
        assert record.probe is not None
        directory = self._directory(record)
        audio = directory / "audio.wav"
        if audio.exists():
            _safe_path(audio).unlink()
        await self._thumbnail(record)
        if record.probe.has_audio:
            shared = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-max_alloc", "134217728",
                      "-threads", "1", "-filter_threads", "1", *self._input_options(record), "-i", str(self._source(record))]
            await _run_media([*shared, "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", "asetpts=PTS-STARTPTS",
                              "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-threads", "1",
                              "-t", str(self.max_seconds + 0.02), "-fs", str(MAX_PCM_BYTES + 4096), str(audio)],
                             timeout=self._timeout(record.probe.sec), limit=1024)
            wave_data = await _blocking(_analyze_pcm, audio, record.probe.audio_offset, self.max_seconds,
                                       bool(self.settings.sync_sound_vad_enabled), float(self.settings.sync_sound_vad_threshold),
                                       int(self.settings.sync_sound_vad_min_speech_ms))
        else:
            wave_data = _Wave([], [], 0, [], False, "no_audio")
        _atomic_json(directory / "wave.json", {
            "interval_ms": WAVE_INTERVAL_MS, "sample_rate": SAMPLE_RATE,
            "offset_seconds": record.probe.audio_offset, "samples": wave_data.samples,
            "duration_seconds": wave_data.samples / SAMPLE_RATE,
            "last_interval_ms": wave_data.counts[-1] * 1000 / SAMPLE_RATE if wave_data.counts else 0,
            "rms": [round(value, 8) for value in wave_data.rms], "has_audio": record.probe.has_audio,
            "measured": wave_data.samples > 0,
        })
        record.wave = True
        return wave_data

    def _cache_key(self, record: _Manifest, audio_hash: str, provider: Any) -> str:
        scope = {
            "version": "uploads-asr-words-ssd200-pcm16k-v1", "owner": record.owner_hash,
            "audio": audio_hash, "provider": f"{type(provider).__module__}.{type(provider).__qualname__}",
            "endpoint": getattr(provider, "base_url", ""), "path": getattr(provider, "endpoint_path", ""),
            "resource": getattr(provider, "resource_id", ""), "cluster": getattr(provider, "cluster_id", ""),
            "app": getattr(provider, "app_id", ""), "credential": getattr(provider, "access_token", ""),
            "nonstream": getattr(provider, "enable_nonstream", None),
            "speaker": getattr(provider, "enable_speaker_info", None),
        }
        # Scope can contain credentials in RAM; only this digest is persisted.
        return hashlib.sha256(json.dumps(scope, sort_keys=True).encode("utf-8")).hexdigest()

    @asynccontextmanager
    async def _cache_lock(self, key: str):
        lock, users = self._cache_locks.get(key, (asyncio.Lock(), 0))
        self._cache_locks[key] = lock, users + 1
        try:
            async with lock:
                yield
        finally:
            _, users = self._cache_locks[key]
            if users == 1:
                del self._cache_locks[key]
            else:
                self._cache_locks[key] = lock, users - 1

    async def _transcribe(self, record: _Manifest) -> ASRTranscript:
        self.before_asr(record)
        audio = _safe_path(self._directory(record) / "audio.wav")
        audio_hash, _ = await _blocking(_digest_file, audio, MAX_PCM_BYTES + 4096)
        async with create_asr_provider(self.settings) as provider:
            key = self._cache_key(record, audio_hash, provider)
            record.asr_cache_key = key
            async with self._cache_lock(key):
                now = time.time()
                ttl = min(UPLOAD_TTL_SECONDS, int(self.settings.asr_cache_ttl_hours) * 3600)
                if self.settings.asr_cache_enabled:
                    found = self._cache.get(key)
                    if found and found[0] + ttl > now:
                        record.asr_cache_hit = True
                        record.asr_cached_at = found[0]
                        return found[1].model_copy(deep=True)
                    for other in self._records.values():
                        if other.owner_hash == record.owner_hash and other.status == "ready" and other.expires_at > now and other.asr_cache_key == key and other.asr_result is not None and other.asr_cached_at is not None and other.asr_cached_at + ttl > now:
                            record.asr_cache_hit = True
                            record.asr_cached_at = other.asr_cached_at
                            return other.asr_result.model_copy(deep=True)
                # No await between this current-batch check and dispatch. Never
                # acquire task/file locks here: preprocessing owns a file lock.
                self.before_asr(record)
                record.asr_attempted = True
                self._save(record)  # Durable BEFORE sending any billable request.
                provider.validate_configuration()
                assert record.probe is not None
                async with asyncio.timeout(self._timeout(record.probe.sec)):
                    result = ASRTranscript.model_validate(await provider.transcribe(audio))
                if len(result.utterances) > 4096 or sum(len(item.words) for item in result.utterances) + len(result.words) > 30_000 or len(result.text) > 100_000 or sum(len(item.text) for item in result.utterances) > 100_000:
                    raise _error(413, "转写结果超出安全限制。")
                result_bytes = len(result.model_dump_json().encode("utf-8"))
                if result_bytes > 1024**2:
                    raise _error(413, "转写结果超出安全限制。")
                record.asr_cached_at = time.time()
                if self.settings.asr_cache_enabled:
                    self._cache[key] = (record.asr_cached_at, result.model_copy(deep=True), result_bytes)
                    self._cache.move_to_end(key)
                    while len(self._cache) > min(16, self.settings.asr_cache_max_entries) or sum(item[2] for item in self._cache.values()) > 4 * 1024**2:
                        self._cache.popitem(last=False)
                return result

    async def _preprocess(self, record: _Manifest) -> None:
        try:
            async with _slots(), self._locks[record.id]:
                self._available()
                record.phase, record.progress = "probe", 50
                self._save(record)
                record.probe = await self._probe(record)
                record.phase, record.progress = "waveform", 60
                self._save(record)
                wave_data = await self._previews(record)
                record.has_speech, record.speech_evidence = wave_data.has_speech, wave_data.evidence
                record.speech_intervals = list(wave_data.speech_intervals)
                record.silences = [(max(0.0, lo), min(record.probe.sec, hi)) for lo, hi in wave_data.silences if max(0.0, lo) < min(record.probe.sec, hi)]
                if record.has_speech is not False:
                    record.phase, record.progress = "asr", 75
                    self._save(record)
                    try:
                        result = await self._transcribe(record)
                        transcript = await _blocking(_editorial_transcript, record, result, wave_data)
                        self._transcript_budget(record, result, transcript)
                        record.transcript = transcript
                        record.asr_result = result
                        if result.text.strip() or any(s.text.strip() for s in result.utterances):
                            record.has_speech, record.speech_evidence = True, "asr"
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        record.asr_result = None
                        record.transcript = _Transcript()
                        record.status, record.phase, record.progress = "asr_failed", "error", 100
                        record.error_code, record.error = "ASR_FAILED", "预转写未完成；素材校验已通过，可在正式任务中重试识别。"
                        self._save(record)
                        return
                record.status, record.phase, record.progress = "ready", "done", 100
                record.error = record.error_code = None
                self._save(record)
        except asyncio.CancelledError:
            record.status = "asr_failed" if record.asr_attempted and record.probe else "interrupted"
            record.phase, record.error_code = "error", "INTERRUPTED"
            record.error = "上传预处理已中断；可在正式任务中重试识别。" if record.asr_attempted else "上传预处理已中断，请重新完成上传。"
            self._save(record)
            raise
        except Exception:
            record.status, record.phase, record.progress = "failed", "error", 100
            record.error_code = "MEDIA_INVALID"
            record.error = _PHOTO_ERROR if Path(record.name).suffix.lower() in _PHOTO_EXTENSIONS else "视频无法安全预处理，或超出格式、时长、资源限制。"
            self._save(record)
        finally:
            audio = self.root / record.id / "audio.wav"
            if audio.exists():
                _safe_path(audio).unlink()

    def media(self, upload_id: str, token: str | None, kind: Literal["thumb", "wave"]) -> bytes:
        record = self.authorize(upload_id, token)
        try:
            if kind == "thumb" and record.thumb:
                return _read_bytes(self._directory(record) / "thumb.jpg", MAX_THUMB_BYTES)
            if kind == "wave" and record.wave:
                return _read_bytes(self._directory(record) / "wave.json", MAX_JSON_BYTES)
        except OSError:
            raise _error(503, "上传预览暂时不可用。") from None
        raise _error(404, "上传预览尚未就绪。")

    def _selection(self, ids: list[str], tokens: dict[str, str]) -> list[_Manifest]:
        if not isinstance(ids, list) or not 1 <= len(ids) <= self.max_files or not all(isinstance(key, str) for key in ids) or len(set(ids)) != len(ids) or not isinstance(tokens, dict):
            raise _error(400, "上传选择不能为空、重复或超出数量上限。")
        records = [self.authorize(key, tokens.get(key)) for key in ids]
        for record in records:
            if record.status not in {"ready", "asr_failed"} or record.probe is None:
                raise _error(409, "所选素材尚未通过安全预处理。")
            source = self._source(record)
            if source.stat().st_size != record.size:
                raise _error(409, "上传原文件已发生变化。")
        if sum(record.size for record in records) > self.max_total_bytes:
            raise _error(413, "所选素材合计超过大小上限。")
        if sum(record.probe.sec for record in records if record.probe) > self.max_total_seconds:
            raise _error(413, "所选素材合计时长不能超过 60 分钟。")
        return records

    def snapshots(self, ids: list[str], tokens: dict[str, str]) -> list[dict[str, Any]]:
        """Detached, capability-checked match inputs; no disk paths/hashes/tokens."""
        return [self._public(record) for record in self._selection(ids, tokens)]

    async def materialize(self, upload_ids: list[str], upload_tokens: dict[str, str], task_dir: Path) -> tuple[list[UploadedAsset], list[dict[str, Any]]]:
        # Serialize raw copies across store instances in the service event loop;
        # concurrent imports cannot each spend the same checked free disk bytes.
        loop = asyncio.get_running_loop()
        if loop not in _MATERIALIZE_SLOTS:
            _MATERIALIZE_SLOTS[loop] = asyncio.Semaphore(1)
        gate = _MATERIALIZE_SLOTS[loop]
        if gate.locked():
            raise _error(409, "其他素材正在导入任务，请稍后重试。")
        async with gate:
            return await self._materialize(upload_ids, upload_tokens, task_dir)

    async def _materialize(self, upload_ids: list[str], upload_tokens: dict[str, str], task_dir: Path) -> tuple[list[UploadedAsset], list[dict[str, Any]]]:
        async with self._operation():
            records = self._selection(upload_ids, upload_tokens)
            task_dir = _safe_path(task_dir, directory=True)
            data_dir = Path(os.path.abspath(self.settings.data_dir))
            if not task_dir.is_relative_to(data_dir) or task_dir == data_dir or task_dir.is_relative_to(self.root):
                raise _error(409, "任务素材目标目录无效。")
            if any(self._locks[key].locked() for key in upload_ids):
                raise _error(409, "所选上传仍在使用，请稍后重试。")
            async with AsyncExitStack() as stack:
                for key in sorted(upload_ids):
                    await stack.enter_async_context(self._locks[key])
                self._selection(upload_ids, upload_tokens)
                self._space(sum(record.size for record in records))
                raw = _safe_path(task_dir / "raw", exists=False, directory=True)
                raw.mkdir(exist_ok=True)
                assets: list[UploadedAsset] = []
                copied: list[Path] = []
                try:
                    for record in records:
                        suffix = Path(record.name).suffix.lower()
                        destination = raw / f"{secrets.token_hex(16)}{suffix}"
                        copied.append(destination)
                        await _blocking(_copy_verified, self._source(record), destination, record.size, record.sha256)
                        self.authorize(record.id, upload_tokens.get(record.id))
                        assets.append(_MaterializedAsset(
                            upload_id=record.id, original_name=record.name, stored_name=destination.name,
                            path=destination, size=record.size, content_type=record.content_type,
                            source_duration_seconds=record.probe.sec if record.probe else None,
                        ))
                    snapshots = self.snapshots(upload_ids, upload_tokens)
                    # Original provider objects let the parent reuse its existing
                    # SourceASRRecord pipeline without reverse-guessing word cues.
                    for snapshot, record in zip(snapshots, records):
                        snapshot["asr_result"] = record.asr_result.model_dump(mode="json") if record.asr_result else None
                        snapshot["audio_offset_seconds"] = record.probe.audio_offset if record.probe else 0.0
                    return assets, snapshots
                except BaseException as exc:
                    for path in copied:
                        if path.exists():
                            _safe_path(path).unlink()
                    if isinstance(exc, OSError):
                        raise _error(507, "无法安全复制任务素材。") from None
                    raise

    def _remove_owned(self, record: _Manifest) -> bool:
        directory = self._directory(record)
        files = list(directory.iterdir())
        allowed = {"manifest.json", "thumb.jpg", "wave.json", "audio.wav", "source" + Path(record.name).suffix.lower()}
        for path in files:
            if path.name not in allowed and not re.fullmatch(r"chunk-[0-9]{5}\.part|write-[0-9a-f]{32}\.tmp", path.name):
                return False
            _safe_path(path)
        # Manifest is last: a partial deletion remains quota-counted/recoverable.
        for path in sorted(files, key=lambda file: file.name == "manifest.json"):
            _safe_path(path).unlink()
        directory.rmdir()
        self._records.pop(record.id, None)
        self._locks.pop(record.id, None)
        return True

    def _cleanup_stale(self) -> int:
        count, now = 0, time.time()
        for record in list(self._records.values()):
            if record.expires_at > now or self._locks[record.id].locked() or record.id in self._jobs:
                continue
            try:
                count += int(self._remove_owned(record))
            except (OSError, HTTPException):
                continue
        return count

    async def cleanup_owned(self) -> int:
        """Remove only expired, recognized uploads; never inspect task data."""
        async with self._operation(), self._admission:
            return self._cleanup_stale()

    async def delete(self, upload_id: str, token: str | None) -> None:
        async with self._operation():
            record = self.authorize(upload_id, token)
            job = self._jobs.get(upload_id)
            if job is not None:
                job.cancel()
                if await _drain(job):
                    raise asyncio.CancelledError
            lock = self._locks[upload_id]
            if lock.locked():
                raise _error(409, "该上传仍在使用，请稍后删除。")
            async with lock, self._admission:
                self.authorize(upload_id, token)
                if not self._remove_owned(record):
                    raise _error(409, "上传目录包含未知文件，未执行删除。")

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finish_close())
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await _drain(self._close_task)
            raise

    async def _finish_close(self) -> None:
        try:
            tasks = set(self._jobs.values()) | set(self._operations)
            for task in tasks:
                task.cancel()
            for task in tasks:
                await _drain(task)
            self._cleanup_stale()
            self._cache.clear()
        finally:
            self._instance.release()


def _number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Not a media number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite media number")
    return result


async def _reap_process(process: asyncio.subprocess.Process, readers: tuple[asyncio.Task[Any], ...] = ()) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    for reader in readers:
        reader.cancel()
    await asyncio.gather(*readers, return_exceptions=True)

    async def discard(reader: asyncio.StreamReader | None) -> None:
        if reader is not None:
            while await reader.read(IO_BLOCK):
                pass

    # Draining after kill is necessary: Process.wait can otherwise wait forever
    # for a pipe transport that paused at its high-water mark.
    await asyncio.gather(discard(process.stdout), discard(process.stderr), process.wait())


async def _run_media(command: list[str], *, timeout: float, limit: int) -> bytes:
    """Bound both pipes and own a child even during asynchronous process creation."""
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=IO_BLOCK,
    ))
    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.shield(spawning)
    except BaseException:
        await _drain(spawning)
        if not spawning.cancelled() and spawning.exception() is None:
            await _drain(asyncio.create_task(_reap_process(spawning.result())))
        raise

    async def bounded(reader: asyncio.StreamReader | None, maximum: int) -> bytes:
        assert reader is not None
        output = bytearray()
        while data := await reader.read(min(IO_BLOCK, maximum + 1)):
            if len(output) + len(data) > maximum:
                raise _error(415, "媒体工具输出超出安全限制。")
            output.extend(data)
        return bytes(output)

    out = asyncio.create_task(bounded(process.stdout, limit))
    err = asyncio.create_task(bounded(process.stderr, 64 * 1024))
    try:
        async with asyncio.timeout(timeout):
            stdout, _ = await asyncio.gather(out, err)
            if await process.wait() != 0:
                raise _error(415, "媒体无法安全解码。")
            return stdout
    finally:
        if await _drain(asyncio.create_task(_reap_process(process, (out, err)))):
            raise asyncio.CancelledError


def _analyze_pcm(path: Path, offset: float, max_seconds: float, vad_enabled: bool, threshold: float, min_speech_ms: int, stop: threading.Event) -> _Wave:
    detector = None
    if vad_enabled:
        try:
            # Same installed, local Silero model as the existing ASR pipeline;
            # do not import that pipeline's media/task/global-cache machinery.
            from silero_vad_lite import SileroVAD
            detector = SileroVAD(SAMPLE_RATE)
        except Exception:
            detector = None  # Fail open, never invent a speech probability.
    rms, counts, quiet = [], [], []
    pending = array.array("f")
    total = speech_frames = analyzed_frames = nonzero = 0
    speech_intervals: list[tuple[float, float]] = []
    silence_start: float | None = None
    with _open_file(path) as source, wave.open(source, "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != SAMPLE_RATE or audio.getcomptype() != "NONE":
            raise _error(415, "解码音频格式无效。")
        if not 0 < audio.getnframes() <= int(max_seconds * SAMPLE_RATE):
            raise _error(415, "解码音频时长超出限制。")
        while data := audio.readframes(WAVE_SAMPLES):
            _check_stop(stop)
            samples = array.array("h")
            samples.frombytes(data)
            if sys.byteorder != "little":
                samples.byteswap()
            count = len(samples)
            total += count
            if total > int(max_seconds * SAMPLE_RATE):
                raise _error(415, "解码音频时长超出限制。")
            energy = sum(sample * sample for sample in samples)
            nonzero += int(energy != 0)
            level = math.sqrt(energy / count) / 32768.0
            rms.append(level)
            counts.append(count)
            lo, hi = offset + (total - count) / SAMPLE_RATE, offset + total / SAMPLE_RATE
            if level <= QUIET_RMS:
                if silence_start is None:
                    silence_start = lo
            elif silence_start is not None:
                quiet.append((silence_start, lo))
                silence_start = None
            if detector is not None:
                pending.extend(sample / 32768.0 for sample in samples)
                while len(pending) >= 512:
                    frame, pending = pending[:512], pending[512:]
                    try:
                        probability = float(detector.process(frame))
                        if not math.isfinite(probability) or not 0 <= probability <= 1:
                            raise ValueError("Invalid VAD result")
                    except Exception:
                        detector = None
                        break
                    analyzed_frames += 1
                    speech_frames += int(probability >= threshold)
                    if probability >= threshold:
                        lo = offset + (analyzed_frames - 1) * .032
                        hi = offset + analyzed_frames * .032
                        if speech_intervals and abs(speech_intervals[-1][1] - lo) < 1e-8:
                            speech_intervals[-1] = (speech_intervals[-1][0], hi)
                        else:
                            speech_intervals.append((lo, hi))
        if total != audio.getnframes():
            raise _error(415, "解码音频被截断。")
    if silence_start is not None:
        quiet.append((silence_start, offset + total / SAMPLE_RATE))
    if nonzero == 0:
        return _Wave(rms, counts, total, quiet, False, "digital_silence")
    if detector is not None and analyzed_frames * 32 >= min_speech_ms:
        # The incomplete trailing VAD frame is UNKNOWN, not confirmed silence.
        if analyzed_frames * 512 < total:
            speech_intervals.append((offset + analyzed_frames * .032, offset + total / SAMPLE_RATE))
        return _Wave(rms, counts, total, quiet, speech_present(speech_frames * 0.032, total / SAMPLE_RATE),
                     "silero_vad_v2_15percent_or_5seconds", tuple(speech_intervals))
    return _Wave(rms, counts, total, quiet, None, "unknown")


def _scoped_speaker_id(upload_id: str, source_id: str | None) -> str | None:
    # New editorial IDs are opaque, <=128 chars and upload-scoped, NOT identities.
    # Original evidence remains in asr_result/observed_words. Restored manifests
    # are never rewritten, so older colon-containing editorial IDs remain valid.
    if source_id is None or not source_id.strip():
        return None
    return f"{upload_id}_spk_{hashlib.sha256(source_id.encode('utf-8')).hexdigest()}"


def _editorial_transcript(record: _Manifest, result: ASRTranscript, waveform: _Wave, stop: threading.Event) -> _Transcript:
    assert record.probe is not None
    segments: list[TranscriptSegment] = []
    precision: dict[str, Literal["word", "segment"]] = {}
    confidences: dict[str, list[float | None]] = {}
    word_speakers: dict[str, list[str | None]] = {}
    observed_words: dict[str, list[dict[str, Any]]] = {}
    offset = record.probe.audio_offset
    global_words = sorted(result.words, key=lambda word: (word.start_time_ms, word.end_time_ms))
    global_starts = [word.start_time_ms for word in global_words]
    for index, utterance in enumerate(result.utterances):
        _check_stop(stop)
        start, end = utterance.start_time_ms / 1000 + offset, utterance.end_time_ms / 1000 + offset
        if not utterance.definite or not utterance.text.strip() or not 0 <= start < end <= record.probe.sec:
            continue  # Never clamp, interpolate, or turn an interim cue into proof.
        first = bisect_left(global_starts, utterance.start_time_ms)
        last = bisect_right(global_starts, utterance.end_time_ms)
        raw_words = utterance.words or global_words[first:last]
        words, word_conf, word_ids = [], [], []
        unsafe_word = False
        for word in raw_words:
            s, e = word.start_time_ms / 1000 + offset, word.end_time_ms / 1000 + offset
            if word.text.strip() and start <= s < e <= end:
                words.append(Word(w=word.text, s=s, e=e))
                word_conf.append(word.confidence)
                word_ids.append(_scoped_speaker_id(record.id, word.speaker_id))
            else:
                unsafe_word = True
        raw_speaker = utterance.speaker_id
        if raw_speaker is None and raw_words and all(word.speaker_id is not None for word in raw_words):
            ids = {word.speaker_id for word in raw_words}
            if len(ids) == 1:
                raw_speaker = raw_words[0].speaker_id
        speaker = _scoped_speaker_id(record.id, raw_speaker) or ""
        segment = TranscriptSegment(id=f"seg_{index}", start=start, end=end, speaker_id=speaker,
                                    text=utterance.text, words=words, confidence=utterance.confidence)
        take = QuoteTake(take_id=f"precision_{index}", upload_id=record.id, start=start, end=end,
                         speaker_id=speaker, asr_text=utterance.text, score=0.0, words=words)
        observed_words[segment.id] = [word.model_dump(mode="json") for word in raw_words]
        precision[segment.id] = "segment" if unsafe_word else take.precision
        if precision[segment.id] == "segment":
            # Do not leave a seemingly complete subset that align_quotes could
            # promote back to word precision after one invalid cue was dropped.
            # Original provider evidence remains in observed_words, never repaired.
            segment.words = []
            word_conf, word_ids = [], []
        confidences[segment.id], word_speakers[segment.id] = word_conf, word_ids
        segments.append(segment)
    _check_stop(stop)
    snrs = adjacent_snr(waveform.rms, waveform.counts, SAMPLE_RATE,
                        [(segment.start, segment.end) for segment in segments], offset=offset)
    for segment, snr in zip(segments, snrs, strict=True):
        segment.snr_db = snr
    speakers: OrderedDict[str, Speaker] = OrderedDict()
    for segment in segments:
        if segment.speaker_id:
            if segment.speaker_id not in speakers:
                speakers[segment.speaker_id] = Speaker(id=segment.speaker_id, auto_label=f"说话人 {len(speakers) + 1}")
            speaker = speakers[segment.speaker_id]
            speaker.appearances += 1
            speaker.seconds += segment.end - segment.start
    all_words = bool(segments) and all(value == "word" for value in precision.values())
    return _Transcript(text=result.text, segments=segments, speakers=list(speakers.values()),
                       precision="word" if all_words else "segment" if segments else "unavailable",
                       segment_precision=precision, word_trim_allowed=all_words, word_confidences=confidences,
                       word_speaker_ids=word_speakers, observed_words=observed_words,
                       snr_method="estimated_pcm_adjacent_nonspeech_500ms_v2")


def _copy_verified(source: Path, destination: Path, size: int, expected_hash: str, stop: threading.Event) -> None:
    digest, count = hashlib.sha256(), 0
    with _open_file(source) as origin, _open_file(destination, exclusive=True) as target:
        before = os.fstat(origin.fileno())
        while data := origin.read(IO_BLOCK):
            _check_stop(stop)
            count += len(data)
            if count > size:
                raise _error(409, "上传原文件大小已改变。")
            digest.update(data)
            target.write(data)
        after = os.fstat(origin.fileno())
        if count != size or digest.hexdigest() != expected_hash or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise _error(409, "上传原文件完整性校验失败。")
        target.flush()
        os.fsync(target.fileno())
    _safe_path(source)
    _check_stop(stop)


def _request_token(request: Request, *, media: bool = False) -> str:
    headers = request.headers.getlist("x-upload-token")
    query = request.query_params.getlist("token")
    if query and not media:
        raise _not_found()
    supplied = headers + query
    if not supplied or any(not _TOKEN.fullmatch(value) for value in supplied) or any(not hmac.compare_digest(supplied[0], other) for other in supplied[1:]):
        raise _not_found()
    return supplied[0]


def _request_owner(request: Request, settings: Settings) -> str:
    session = getattr(request.state, "anonymous_session_id", None)
    if isinstance(session, str) and re.fullmatch(r"[A-Za-z0-9_-]{20,128}", session):
        return "session:" + session
    host = request.headers.getlist("host")
    authority = host[0].lower() if len(host) == 1 else ""
    match = re.fullmatch(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", authority)
    forwarded = any(key.lower() in {"forwarded", "x-real-ip", "via"} or key.lower().startswith("x-forwarded-") for key in request.headers)
    if settings.app_env != "production" and request.client and request.client.host in {"127.0.0.1", "::1"} and match and (match[1] is None or 0 < int(match[1]) < 65536) and not forwarded:
        return "local:exact-loopback"
    raise _error(403, "需要可信匿名会话才能新建上传。")


def _content_length(request: Request, *, required: bool) -> int | None:
    values = request.headers.getlist("content-length")
    if request.headers.getlist("transfer-encoding") or len(values) > 1:
        raise _error(400, "请求长度头无效。")
    if not values:
        if required:
            raise _error(411, "上传分块必须声明 Content-Length。")
        return None
    if not re.fullmatch(r"[0-9]{1,12}", values[0]):
        raise _error(400, "请求长度头无效。")
    return int(values[0])


async def _json_body(request: Request) -> _CreateUpload:
    length = _content_length(request, required=False)
    if length is not None and length > 4096:
        raise _error(413, "上传声明过大。")
    data = bytearray()
    async with asyncio.timeout(5.0):
        async for chunk in request.stream():
            if len(data) + len(chunk) > 4096:
                raise _error(413, "上传声明过大。")
            data.extend(chunk)
    if length is not None and len(data) != length:
        raise _error(400, "上传声明长度不一致。")
    try:
        return _CreateUpload.model_validate_json(bytes(data))
    except ValidationError:
        raise _error(422, "上传声明需要 name、bytes、sha256 和可选 content_type。") from None


def create_upload_router(store: UploadStore) -> APIRouter:
    """No main imports or auth middleware bypasses; mount beneath app middleware."""
    router = APIRouter(prefix="/api/uploads", tags=["uploads"])

    @router.post("", status_code=201)
    async def create(request: Request, response: Response) -> dict[str, Any]:
        owner = _request_owner(request, store.settings)
        try:
            payload = await _json_body(request)
        except TimeoutError:
            raise _error(408, "上传声明接收超时。") from None
        response.headers.update(_PRIVATE_HEADERS)
        return await store.create(name=payload.name, size=payload.bytes, sha256=payload.sha256,
                                  content_type=payload.content_type, owner=owner)

    @router.get("/{upload_id}")
    async def read(upload_id: str, request: Request, response: Response) -> dict[str, Any]:
        response.headers.update(_PRIVATE_HEADERS)
        return store.read(upload_id, _request_token(request))

    @router.put("/{upload_id}/chunks/{index}")
    async def put(upload_id: str, index: str, request: Request, response: Response) -> dict[str, Any]:
        token = _request_token(request)
        store.authorize(upload_id, token)  # Before inspecting/iterating any body.
        if not re.fullmatch(r"0|[1-9][0-9]{0,4}", index):
            raise _error(400, "上传分块索引无效。")
        length = _content_length(request, required=True)
        response.headers.update(_PRIVATE_HEADERS)
        return await store.put_chunk(upload_id, token, int(index), request.stream(), length)

    @router.post("/{upload_id}/complete", status_code=202)
    async def complete(upload_id: str, request: Request, response: Response) -> dict[str, Any]:
        response.headers.update(_PRIVATE_HEADERS)
        return await store.complete(upload_id, _request_token(request))

    @router.delete("/{upload_id}", status_code=204)
    async def delete(upload_id: str, request: Request) -> Response:
        await store.delete(upload_id, _request_token(request))
        return Response(status_code=204, headers=_PRIVATE_HEADERS)

    @router.get("/{upload_id}/thumb")
    async def thumb(upload_id: str, request: Request) -> Response:
        data = store.media(upload_id, _request_token(request, media=True), "thumb")
        return Response(data, media_type="image/jpeg", headers=_PRIVATE_HEADERS)

    @router.get("/{upload_id}/wave")
    async def waveform(upload_id: str, request: Request) -> Response:
        data = store.media(upload_id, _request_token(request, media=True), "wave")
        return Response(data, media_type="application/json", headers=_PRIVATE_HEADERS)

    return router