"""Ownerless task retry/copy operations and read-only legacy risk protection.

No application globals, account database, scheduler or provider calls. All
manager admissions belong to the application's single owning event loop.
"""
from __future__ import annotations

# This adapter deliberately shares the manager's persistence/admission hooks.
# pyright: reportPrivateUsage=false

import asyncio
import copy
import inspect
import ipaddress
import math
import re
import shutil
import sqlite3
import stat
from collections.abc import Awaitable, Callable, Generator
from contextlib import asynccontextmanager, closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator, Protocol, TypeVar, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Message

from .config import Settings
from .models import PIPELINE_STAGE_NAMES, ReportResponse, StageSnapshot, TaskState
from .operations import InsufficientDiskSpaceError, UploadReservation
from .revisions import (
    ARTIFACTS, MAX_SNAPSHOT_BYTES, RevisionError, artifact_files, copy_files,
    local_file, read_json, snapshot_revision,
)
from .storage import write_json_atomic

if TYPE_CHECKING:
    from .task_manager import TaskManager, TaskRecord

_T = TypeVar("_T")
_COPYING: dict[str, asyncio.Task[Any]] = {}
INCOMPLETE_COPY_MARKER = ".incomplete-copy"
_MEDIA_ROOTS = frozenset({
    "raw", "norm", "generated", "thumbs", "tts", "segments", "recordings",
    "workbench_audio", "student_audio",  # Historical media paths, not accounts.
})
_MODE_INPUTS = frozenset({"pipeline_manifest.json", "asr_transcripts.json", "shots.json"})
_EXTRA_ARTIFACTS = frozenset({"media_input_manifest.json", "own_voice.wav", "script.txt"}) | _MODE_INPUTS
# Only schema-owned media fields are dependencies. ASR transcript.segments is
# linguistic evidence (objects), not SegmentManifestItem.segments (paths).
_DEPENDENCY_FIELDS = {
    "timings.json": ("*.audio_path",),
    "source_timings.json": ("*.audio_path",),
    "segment_manifest.json": ("*.segments.*",),
    "source_segment_manifest.json": ("*.segments.*",),
    "edl.json": ("*.clips.*.src",),
    "source_edl.json": ("*.clips.*.src",),
    "shots_annotated.json": ("*.norm_path", "*.thumb_path"),
    "shots.json": ("*.norm_path", "*.thumb_path"),
    "match_plan.json": ("*.sync_sound.source_media_path",),
    "source_match_plan.json": ("*.sync_sound.source_media_path",),
    "tts_manifest.json": ("groups.*.source_audio_path", "continuous_groups.*.source_audio_path"),
    "production_mode.json": (
        "source_clocks.*.raw_path", "source_clocks.*.norm_path",
        "audio_cache.*.timing.audio_path", "audio_cache.*.raw_timing.audio_path",
        "audio_cache.*.raw_recipe.source_audio",
    ),
    "media_input_manifest.json": ("own_voice.normalized_path",),
    "student_narration.json": ("source_audio", "units.*.audio_path", "raw_recipes.*.source_audio"),
    "asr_transcripts.json": ("*.source_media_path",),
}
_PRIVATE_KEYS = frozenset({
    "access_token", "access_token_hash", "token", "consent", "confirmed_by",
    "confirmed_binding", "confirmed_revision", "published", "teacher_id",
    "student_id", "actor_id", "actor_role", "author_id", "class_id",
    "author_name", "teacher_name", "student_name", "class_code", "classroom",
    "classroom_task", "queue_hold", "local_only",
})
_LEGACY_APPLICATION_ID = 0x474D434C
_MAX_JSON_BYTES = 256 * 1024


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)


class _TaskOperationRoute(APIRoute):
    """Bound actual streamed JSON and never echo arbitrary validation inputs."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                body = bytearray()
                async with asyncio.timeout(30):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > _MAX_JSON_BYTES:
                            raise HTTPException(413, "Task operation JSON exceeds 256 KiB")
                        body.extend(chunk)
                original_receive = request.receive
                replayed = False

                async def receive() -> Message:
                    nonlocal replayed
                    if not replayed:
                        replayed = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await original_receive()

                response = await original(Request(request.scope, receive))
            except TimeoutError as exc:
                raise HTTPException(408, "Task operation request body timed out", headers={"Cache-Control": "no-store"}) from exc
            except RequestValidationError as exc:
                raise HTTPException(422, "Invalid task operation request", headers={"Cache-Control": "no-store"}) from exc
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            response.headers["Cache-Control"] = "no-store"
            return response

        return handler


class TaskAuthorizer(Protocol):
    def __call__(self, request: Request, task_id: str, *, write: bool = False) -> TaskRecord | None | Awaitable[TaskRecord | None]: ...


def trusted_local_request(request: Request, settings: Settings, *, mutation: bool = False) -> bool:
    """Direct loopback authority only; cookies and forwarded identity never count.

    Reads may omit Origin. Implicit local writes must carry the exact base
    origin, even in development and even if ordinary Origin checking is off.
    """
    if settings.is_production or request.client is None:
        return False
    try:
        if not ipaddress.ip_address(request.client.host).is_loopback:
            return False
        host = request.url.hostname or ""
        if host.lower() != "localhost" and not ipaddress.ip_address(host).is_loopback:
            return False
    except ValueError:
        return False
    if len(request.headers.getlist("host")) != 1:
        return False
    if any(name in {"forwarded", "x-real-ip"} or name.startswith("x-forwarded-") for name in request.headers):
        return False
    sites = request.headers.getlist("sec-fetch-site")
    if sites and (len(sites) != 1 or sites[0] not in {"same-origin", "none"}):
        return False
    origins = request.headers.getlist("origin")
    base_origin = f"{request.base_url.scheme}://{request.base_url.netloc}"
    if origins:
        return len(origins) == 1 and origins[0] == base_origin
    return not mutation


def legacy_task_busy(task_dir: Path) -> bool:
    """Fail closed on unresolved old remote work, without opening its old router.

    Unlike CloudStore's normal connection this cannot create a database, migrate
    a schema, recover jobs, checkpoint WAL, release money, or submit/poll anything.
    Completed jobs' billing evidence remains untouched outside task directories.
    """
    try:
        root = Path(task_dir).resolve()
        ledger = root.parent.parent / "cloud_jobs.sqlite3"
        try:
            info = ledger.lstat()
        except FileNotFoundError:
            return any(Path(str(ledger) + suffix).exists() for suffix in ("-wal", "-shm"))
        if not stat.S_ISREG(info.st_mode) or ledger.is_symlink() or ledger.resolve() != ledger.absolute():
            return True
        wal, shared = Path(str(ledger) + "-wal"), Path(str(ledger) + "-shm")
        has_wal, has_shared = wal.exists(), shared.exists()
        if has_wal != has_shared:
            return True
        # Even mode=ro creates empty WAL/SHM sidecars for a checkpointed WAL
        # database. Immutable is safe ONLY when no WAL exists, with a stability
        # recheck after the query. Never ignore an existing uncheckpointed WAL.
        uri = ledger.as_uri() + ("?mode=ro" if has_wal else "?mode=ro&immutable=1")
        before = _file_fingerprint(ledger)
        with closing(sqlite3.connect(uri, uri=True, timeout=0.1)) as db:
            db.execute("PRAGMA query_only=ON")
            if (db.execute("PRAGMA application_id").fetchone()[0] != _LEGACY_APPLICATION_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != 1):
                return True
            busy = db.execute("""SELECT 1 FROM jobs WHERE task_id=? AND
                (state NOT IN ('succeeded','failed','cancelled','interrupted')
                 OR (state='interrupted' AND (attempted=1 OR remote_id IS NOT NULL))) LIMIT 1""",
                (root.name,)).fetchone() is not None
        if before != _file_fingerprint(ledger) or (not has_wal and (wal.exists() or shared.exists())):
            return True
        return busy
    except (OSError, ValueError, sqlite3.Error):
        return True


def task_operation_busy(task_dir: Path, *, allow_owner: bool = False) -> bool:
    """Shared by the host, manager and editing routers, including post-await checks."""
    owner = _COPYING.get(str(Path(task_dir).resolve()))
    if owner is None:
        return False
    if allow_owner:
        try:
            return owner is not asyncio.current_task()
        except RuntimeError:
            pass
    return True


def active_task_operations() -> list[asyncio.Task[Any]]:
    return list({owner for owner in _COPYING.values() if not owner.done()})


@contextmanager
def _operation(record: TaskRecord) -> Generator[None, None, None]:
    from .studio import studio_task_busy

    key = str(record.task_dir.resolve())
    if key in _COPYING or studio_task_busy(record.task_dir) or legacy_task_busy(record.task_dir) or (
        record.background is not None and not record.background.done()
    ):
        raise HTTPException(409, "Task operation already in progress or legacy work awaits reconciliation")
    owner = asyncio.current_task()
    assert owner is not None
    _COPYING[key] = owner
    try:
        yield
    finally:
        if _COPYING.get(key) is owner:
            del _COPYING[key]


async def drain_task(job: asyncio.Task[_T]) -> _T:
    """Finish a worker even through repeated request cancellation, then propagate it."""
    cancelled = False
    while not job.done():
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not job.cancelled():
            job.exception()
        raise asyncio.CancelledError
    return job.result()


def _originals(record: TaskRecord) -> set[str]:
    if record.task_dir.absolute() != record.task_dir.resolve():
        raise RevisionError("Linked task roots are not supported")
    if not record.uploads:
        raise RevisionError("Original uploads are unavailable")
    names: set[str] = set()
    for asset in record.uploads:
        name = f"raw/{asset.stored_name}"
        path = local_file(record.task_dir, name)
        if path != asset.path.absolute() or path.stat().st_size != asset.size or asset.size <= 0:
            raise RevisionError("Original upload missing, moved or size changed")
        names.add(name)
        if asset.prepared_stored_name:
            names.add(f"raw/{asset.prepared_stored_name}")
    manifest = record.task_dir / "media_input_manifest.json"
    if manifest.exists():
        voice = read_json(record.task_dir, manifest.name).get("own_voice")
        if voice:
            names.update({"own_voice.wav", f"raw/{voice['stored_name']}"})
    for name in names:
        if local_file(record.task_dir, name).stat().st_size <= 0:
            raise RevisionError("Empty source media")
    return names


def _managed_root(settings: Settings, record: TaskRecord) -> None:
    if (record.task_dir.absolute() != Path(settings.data_dir).resolve() / record.task_id
            or record.task_dir.absolute() != record.task_dir.resolve()):
        raise HTTPException(409, "Only managed local task roots are supported")


def _dependencies(artifact: str, value: Any) -> set[str]:
    """Walk only known artifact fields; never infer paths from arbitrary keys."""
    names: set[str] = set()

    def collect(node: Any, fields: list[str]) -> None:
        if node is None:
            return
        if not fields:
            if not isinstance(node, str):
                raise RevisionError("Invalid media dependency")
            names.add(node)
        elif fields[0] == "*":
            if not isinstance(node, (dict, list)):
                raise RevisionError("Invalid media dependency collection")
            for child in node.values() if isinstance(node, dict) else node:
                collect(child, fields[1:])
        elif isinstance(node, dict):
            if fields[0] in node:
                collect(node[fields[0]], fields[1:])
        else:
            raise RevisionError("Invalid media dependency object")

    for field in _DEPENDENCY_FIELDS.get(artifact, ()):
        collect(value, field.split("."))
    return names


def _remap(value: Any, old_id: str, new_task_id: str, names: set[str]) -> Any:
    if isinstance(value, list):
        return [_remap(item, old_id, new_task_id, names) for item in cast(list[Any], value)]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, child in cast(dict[str, Any], value).items():
        if key in _PRIVATE_KEYS:
            continue
        if key == "task_id":
            result[key] = new_task_id
        elif key == "revision":
            result[key] = 0
        elif key == "thumb_url" and child is not None:
            if not isinstance(child, str):
                raise RevisionError("Invalid report thumbnail URL")
            match = re.fullmatch(rf"/api/tasks/{re.escape(old_id)}/thumbs/(\d+)\.jpg(?:\?[^#]*)?", child)
            if not match:
                raise RevisionError("Unexpected report thumbnail URL")
            names.add(f"thumbs/shot_{match[1]}.jpg")
            result[key] = f"/api/tasks/{new_task_id}/thumbs/{match[1]}.jpg"
        else:
            result[key] = _remap(child, old_id, new_task_id, names)
    return result


@dataclass(frozen=True)
class _CopyPlan:
    names: tuple[str, ...]
    rewritten: dict[str, Any]
    fingerprints: dict[str, tuple[int, int, int, int]]
    root_identity: tuple[int, int]
    bytes_required: int


def _file_fingerprint(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _plan_completed(source: TaskRecord, target: TaskRecord) -> _CopyPlan:
    from .studio import requires_disclosure

    root = source.task_dir
    for name in ("final.mp4", "video_only.mp4", "narration.m4a", "subs.ass", "subtitle_manifest.json",
                 "report.json", "timings.json", "edl.json", "match_plan.json", "shots_annotated.json",
                 "segment_manifest.json"):
        local_file(root, name)
    ReportResponse.model_validate(read_json(root, "report.json"))
    requires_disclosure(root)
    snapshot_names = artifact_files(root)
    names = set(snapshot_names) | _originals(source)
    names.update(name for name in _EXTRA_ARTIFACTS if (root / name).exists())
    if getattr(source, "mode_contract", False) or getattr(source, "mode", "voiceover") != "voiceover":
        # These immutable edit inputs are deliberately NOT revision snapshots.
        # A successful new-mode copy must be editable without the source task.
        names.update(_MODE_INPUTS)
    rewritten: dict[str, Any] = {}
    for name in sorted(names):
        if name.endswith(".json"):
            payload = read_json(root, name)
            if name == "pretranscripts.json":
                if not isinstance(payload, list):
                    raise RevisionError("Invalid retained upload snapshots")
                snapshots = cast(list[Any], payload)
                if any(not isinstance(row, dict) for row in snapshots):
                    raise RevisionError("Invalid retained upload snapshots")
                # UploadStore's top-level thumbnail is staging UI metadata,
                # not a report shot dependency. Do not retain its old authority
                # or relax the strict task thumbnail checks in _remap.
                payload = [{key: value for key, value in cast(dict[str, Any], row).items() if key != "thumb_url"}
                           for row in snapshots]
            if name == "pipeline_manifest.json":
                if not isinstance(payload, dict):
                    raise RevisionError("Invalid pipeline provenance")
                # Historical settings dumps can include absolute paths and
                # credentials. Retain audit identity, never copy that dump.
                payload = {key: payload[key] for key in (
                    "schema_version", "implementation_version", "default_models",
                    "editing_preferences", "prompt_sha256", "source_sha256", "mode", "mode_recipe",
                ) if key in payload}
            names.update(_dependencies(name, payload))
            rewritten[name] = _remap(payload, source.task_id, target.task_id, names)
    fingerprints: dict[str, tuple[int, int, int, int]] = {}
    for name in names:
        if name not in ARTIFACTS | _EXTRA_ARTIFACTS and name.split("/", 1)[0] not in _MEDIA_ROOTS:
            raise RevisionError("Unexpected media dependency location")
        path = local_file(root, name)
        fingerprint = _file_fingerprint(path)
        if fingerprint[2] <= 0:
            raise RevisionError("Empty task artifact")
        if "/" in name and path.suffix.lower() not in {
            ".mp4", ".mov", ".mkv", ".webm", ".avi", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg",
            ".jpg", ".jpeg", ".png", ".gif", ".webp",
        }:
            raise RevisionError("Unexpected media dependency type")
        fingerprints[name] = fingerprint
    total = sum(value[2] for value in fingerprints.values())
    snapshot_bytes = sum(fingerprints[name][2] for name in snapshot_names)
    if total + snapshot_bytes > MAX_SNAPSHOT_BYTES:
        raise RevisionError("Duplicate including initial snapshot exceeds 8 GiB")
    info = root.stat()
    return _CopyPlan(tuple(sorted(names)), rewritten, fingerprints, (info.st_dev, info.st_ino),
                     total + snapshot_bytes + 64 * 1024**2)


def _check_copy_source(source: TaskRecord, plan: _CopyPlan) -> None:
    info = source.task_dir.stat()
    if source.task_dir.absolute() != source.task_dir.resolve() or (info.st_dev, info.st_ino) != plan.root_identity:
        raise RevisionError("Source root changed during duplication")
    for name, fingerprint in plan.fingerprints.items():
        if _file_fingerprint(local_file(source.task_dir, name)) != fingerprint:
            raise RevisionError("Source changed during duplication")


def _copy_completed(source: TaskRecord, target: TaskRecord, plan: _CopyPlan | None = None) -> None:
    """Blocking, allowlisted copy; caller MUST shield and drain before rollback."""
    from .studio import requires_disclosure

    plan = plan or _plan_completed(source, target)
    _check_copy_source(source, plan)
    if shutil.disk_usage(target.task_dir.parent).free < plan.bytes_required:
        raise RevisionError("Insufficient duplicate disk space")
    copy_files(source.task_dir, target.task_dir, list(plan.names))
    _check_copy_source(source, plan)
    for name, fingerprint in plan.fingerprints.items():
        if local_file(target.task_dir, name).stat().st_size != fingerprint[2]:
            raise RevisionError("Incomplete duplicate artifact")
    for name, payload in plan.rewritten.items():
        write_json_atomic(target.task_dir / name, payload)
    requires_disclosure(target.task_dir)
    snapshot_revision(target, "Duplicate initial version")


async def _drain_copy(source: TaskRecord, target: TaskRecord, plan: _CopyPlan | None = None) -> None:
    await drain_task(asyncio.create_task(asyncio.to_thread(_copy_completed, source, target, plan)))


@asynccontextmanager
async def _reserve_disk(manager: TaskManager, required: int) -> AsyncGenerator[UploadReservation | None, None]:
    guard = manager._upload_capacity_guard
    if guard is None:
        # Standalone embeddings may not have upload admission. The mounted app
        # always supplies its shared guard, including all other uploads/jobs.
        if shutil.disk_usage(manager.settings.data_dir).free < manager.settings.minimum_free_disk_bytes + required:
            raise InsufficientDiskSpaceError("Insufficient task operation disk space")
        yield None
        return
    request_bytes = max(1, math.ceil(required / manager.settings.task_disk_reservation_multiplier))
    context = guard.reserve(request_bytes)
    reservation = await context.__aenter__()
    try:
        yield reservation
    finally:
        await drain_task(asyncio.create_task(context.__aexit__(None, None, None)))


def create_task_operations_router(
    settings: Settings,
    manager: TaskManager,
    authorize: TaskAuthorizer,
    *,
    check_rate_limit: Callable[[Request], None],
) -> APIRouter:
    """Host enforces tokens/Origin/CSRF and permits its own operation's recheck.

    Retry consumes the same synchronous paid-start rate limiter as task creation.
    Duplicate never runs the pipeline, renews approval, or reuses a capability.
    """
    router = APIRouter(prefix="/api/tasks", tags=["tasks"], route_class=_TaskOperationRoute)

    async def access(request: Request, task_id: str) -> TaskRecord:
        result = authorize(request, task_id, write=True)
        record = await result if inspect.isawaitable(result) else result
        if record is None or record.task_id != task_id or manager.get(task_id) is not record:
            raise HTTPException(404, "Task not found")
        return record

    def retriable(record: TaskRecord, expected: int) -> set[str]:
        _managed_root(settings, record)
        if record.status != TaskState.failed or record.revision != 0 or expected != 0:
            raise HTTPException(409, "Only an idle failed initial run can be retried")
        if any((record.task_dir / name).exists() or (record.task_dir / name).is_symlink()
               for name in ("revisions", "final.mp4", "report.json")):
            raise HTTPException(409, "Existing completed artifacts cannot be retried")
        try:
            return _originals(record)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, "Original uploads are missing or unsafe") from exc

    @router.post("/{task_id}/retry", status_code=202)
    async def retry(task_id: str, request: Request, data: RevisionInput) -> dict[str, Any]:
        record = await access(request, task_id)
        with _operation(record):
            names = retriable(record, data.expected_revision)
            required = math.ceil(sum(local_file(record.task_dir, name).stat().st_size for name in names)
                                 * settings.task_disk_reservation_multiplier)
            async with _reserve_disk(manager, required) as reservation:
                if await access(request, task_id) is not record:
                    raise HTTPException(409, "Task changed during retry admission")
                retriable(record, data.expected_revision)
                try:
                    manager.ensure_start_capacity()
                except RuntimeError as exc:
                    raise HTTPException(503, str(exc)) from exc
                check_rate_limit(request)
                old = vars(record).copy()
                record.status = TaskState.queued
                record.current_stage = record.stage_name = record.error_stage = record.error_message = None
                record.progress = 0
                record.processing_started_at = record.processing_completed_at = None
                record.total_elapsed_seconds = None
                record.processing_started_monotonic = record.stage_started_monotonic = None
                record.stages = [StageSnapshot(number=i + 1, name=name) for i, name in enumerate(PIPELINE_STAGE_NAMES)]
                record.updated_at = datetime.now(timezone.utc)
                record.message = "Queued for explicit retry"
                record.background = None
                record.reserved_disk_bytes = reservation.reserved_bytes if reservation is not None else 0
                try:
                    manager.start_task(task_id)
                    if reservation is not None:
                        reservation.retain()
                except BaseException:
                    vars(record).update(old)
                    manager._persist_record(record)
                    raise
        return {"task_id": task_id, "status": "queued", "revision": 0}

    @router.post("/{task_id}/duplicate", status_code=201)
    async def duplicate(task_id: str, request: Request, data: RevisionInput) -> dict[str, Any]:
        from .task_manager import TaskRecord

        source = await access(request, task_id)
        initial_stat = source.task_dir.stat()
        source_identity = (initial_stat.st_dev, initial_stat.st_ino)

        def current_source() -> None:
            _managed_root(settings, source)
            info = source.task_dir.stat()
            if (info.st_dev, info.st_ino) != source_identity:
                raise HTTPException(409, "Source directory changed during copy")
            if manager.get(task_id) is not source or source.status != TaskState.done or source.revision != data.expected_revision:
                raise HTTPException(409, "Completed current revision required")
            if manager.is_draining:
                raise HTTPException(503, "Task manager is draining")

        with _operation(source):
            current_source()
            target = TaskRecord(
                task_id=uuid4().hex, task_dir=Path(settings.data_dir), script=source.script,
                uploads=[], preferences=source.preferences.model_copy(deep=True),
                local_only=source.local_only,
                lifecycle_v2=source.lifecycle_v2, owner_hash=source.owner_hash,
                rules_version=source.rules_version, display_title=source.display_title,
                status=TaskState.done, revision=0, progress=100, message="Duplicated completed task",
                current_stage=source.current_stage, stage_name=source.stage_name,
                stages=copy.deepcopy(source.stages),
                processing_started_at=source.processing_started_at,
                # Copy creation is new, but it must not renew V2 retention.
                processing_completed_at=(source.processing_completed_at or source.updated_at or source.created_at)
                    if source.lifecycle_v2 else source.processing_completed_at,
                total_elapsed_seconds=source.total_elapsed_seconds,
            )
            # A duplicate is the same production mode, never an implicit new
            # generation. RevisionInput rejects client mode overrides. These are
            # editorial identities/metadata only, not upload/task capabilities.
            for name, default in (
                ("mode", "voiceover"), ("mode_contract", False), ("upload_ids", []),
                ("sentences", []), ("speakers", []), ("quality_gate_mode", None),
            ):
                setattr(target, name, copy.deepcopy(getattr(source, name, default)))
            target.task_dir = Path(settings.data_dir) / target.task_id
            target.uploads = [asset.model_copy(update={"path": target.task_dir / "raw" / asset.stored_name}) for asset in source.uploads]
            created = False
            try:
                plan = await drain_task(asyncio.create_task(asyncio.to_thread(_plan_completed, source, target)))
                async with _reserve_disk(manager, plan.bytes_required):
                    if await access(request, task_id) is not source:
                        raise HTTPException(409, "Source authorization changed")
                    current_source()
                    if target.task_id in manager._tasks or target.task_id in manager._deletions:
                        raise HTTPException(409, "Task identifier collision")
                    # Never remove somebody else's pre-existing directory.
                    target.task_dir.mkdir(parents=True, exist_ok=False)
                    created = True
                    write_json_atomic(target.task_dir / INCOMPLETE_COPY_MARKER, {"version": 1})
                    await _drain_copy(source, target, plan)
                    if await access(request, task_id) is not source:
                        raise HTTPException(409, "Source authorization changed during copy")
                    current_source()
                    _check_copy_source(source, plan)
                    manager._write_upload_manifest(target)
                    manager._persist_record(target)
                if await access(request, task_id) is not source:
                    raise HTTPException(409, "Source authorization changed before publication")
                current_source()
                (target.task_dir / INCOMPLETE_COPY_MARKER).unlink()
                # Publication has no suspension point after final reauthorization
                # and the cancellation-drained disk-reservation release.
                manager._tasks[target.task_id] = target
            except BaseException as exc:
                if created:
                    if manager.get(target.task_id) is target:
                        manager._tasks.pop(target.task_id)
                    try:
                        await drain_task(asyncio.create_task(asyncio.to_thread(manager._remove_task_directory, target.task_dir)))
                    except asyncio.CancelledError:
                        raise
                    except Exception as cleanup_error:
                        if isinstance(exc, asyncio.CancelledError):
                            raise exc from cleanup_error
                        raise HTTPException(503, {"code": "duplicate_cleanup_failed",
                            "message": "Duplicate was not published; incomplete files could not be removed."}) from cleanup_error
                if isinstance(exc, (OSError, ValueError, KeyError, TypeError)):
                    raise HTTPException(409, "Duplicate requires complete, safe local media and sufficient disk space") from exc
                raise
            return {"task_id": target.task_id, "access_token": target.access_token, "status": "done", "revision": 0}

    return router