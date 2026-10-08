"""File-backed M0 drafts, task-owned upload capabilities and explicit admission.

UploadStore remains the authority for streamed chunks, verification, preprocessing
and media bounds. Its capability is held only in private task_state, never sent
to the browser. No GET or restart starts a provider. M0 retention is explicit:
drafts expire after 24 idle hours; ALL terminal v2 tasks (including failed tasks)
expire after 72 hours. Legacy retention and immutable reports are unchanged.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import secrets
import tempfile
import time
import unicodedata
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .admission import AdmissionLedger, owner_digest
from .media_input import prepare_media_inputs
from .models import EditingPreferences, TaskState
from .production_modes import (Mode, SentenceInput, Speaker, align_quotes, parse_sentences,
                               quote_text_is_contiguous)
from .storage import create_task_dir, write_json_atomic
from .task_manager import TaskManager, TaskRecord
from .task_operations import drain_task, _operation as task_operation
from .uploads import (CHUNK_SIZE, IO_BLOCK, UploadStore, _blocking, _check_stop,
                      _open_file, _safe_path, _copy_verified, _Chunk, _Manifest)


class DraftUploadStore(UploadStore):
    """Small adapter for optional whole-file digest and explicit failed-ASR retry.

The ordinary UploadStore API stays unchanged. A missing digest is calculated
from verified persisted chunks, then the existing assembler verifies it again.
"""

    def __init__(self, settings):
        super().__init__(settings)
        self._root_identity = AdmissionLedger._identity(self.root)
        self._data_identity = AdmissionLedger._identity(self.root.parent)
        self.external_reserved = lambda: 0

    def _directory(self, record):
        if hasattr(self, "_root_identity"):
            if (AdmissionLedger._identity(self.root) != self._root_identity
                    or AdmissionLedger._identity(self.root.parent) != self._data_identity):
                raise HTTPException(503, "Upload storage identity changed")
        return super()._directory(record)

    def _space(self, additional=0):
        super()._space(additional + (self.external_reserved() if hasattr(self, "external_reserved") else 0))

    async def complete_optional(self, upload_id: str, token: str, *, supplied_hash: bool) -> dict:
        if not supplied_hash:
            record = self.authorize(upload_id, token)
            if record.status == "uploading":
                lock = self._locks[upload_id]
                if lock.locked():
                    raise HTTPException(409, "File is busy")
                async with self._operation(), lock:
                    self.authorize(upload_id, token)
                    if len(record.chunks) != math.ceil(record.size / CHUNK_SIZE):
                        raise HTTPException(422, "Upload is incomplete")

                    def digest_chunks(stop):
                        digest, total = hashlib.sha256(), 0
                        for index in range(math.ceil(record.size / CHUNK_SIZE)):
                            entry = record.chunks.get(str(index))
                            if entry is None:
                                raise HTTPException(422, "Upload is incomplete")
                            count, part = 0, hashlib.sha256()
                            with _open_file(self._directory(record) / f"chunk-{index:05d}.part") as source:
                                while data := source.read(IO_BLOCK):
                                    _check_stop(stop)
                                    count += len(data)
                                    total += len(data)
                                    if count > entry.size or total > record.size:
                                        raise HTTPException(409, "Chunk changed")
                                    digest.update(data)
                                    part.update(data)
                            if count != entry.size or part.hexdigest() != entry.sha256:
                                raise HTTPException(409, "Chunk changed")
                        if total != record.size:
                            raise HTTPException(422, "Upload is incomplete")
                        return digest.hexdigest()

                    digest = await _blocking(digest_chunks)
                    self.authorize(upload_id, token)
                    record.sha256 = digest
                    self._save(record)
        return await self.complete(upload_id, token)

    async def retry_pretranscribe(self, upload_id: str, token: str) -> dict:
        record = self.authorize(upload_id, token)
        if record.status not in {"asr_failed", "interrupted"}:
            raise HTTPException(409, "Only interrupted/failed pretranscription can be explicitly retried")
        if self._locks[upload_id].locked() or upload_id in self._jobs:
            raise HTTPException(409, "Preprocessing is still running")
        # complete() re-verifies all original chunks and schedules the real
        # preprocess implementation. It never fabricates ASR or cache reuse.
        previous = record.status
        record.status = "interrupted"
        try:
            self._save(record)
            return await self.complete(upload_id, token)
        except BaseException:
            if record.status == "interrupted":
                record.status = previous
                self._save(record)
            raise

    def plan_quota_release(self, source_task: TaskRecord, owner: str, files: list[dict]) -> list[tuple[str, str]] | None:
        """Staging copies of the failed task itself to release so a recovery fits the session quota.

        An accepted task keeps its originals under task_dir/raw and import_recovery falls
        back to them, so its upload staging records (kept for 72 h) are redundant. Without
        this, a task with more than half the per-session file limit could never be
        recovered: staging copies plus recovered copies exceed it. Returns [] when it
        already fits, a list when releasing only this task's own idle same-owner records
        makes it fit, and None when it cannot fit (nothing may then be touched).
        """
        digest = hashlib.sha256(owner.encode()).hexdigest()
        own = [record for record in self._records.values() if record.owner_hash == digest]
        need_count = len(files)
        need_bytes = sum(int(file["manifest"]["size"]) for file in files)
        free_count = self.max_files - len(own)
        free_bytes = self.max_total_bytes - sum(record.size for record in own)
        plan: list[tuple[str, str]] = []
        for item in source_task.draft_context.get("files", []):
            if free_count >= need_count and free_bytes >= need_bytes:
                return plan
            live = self._records.get(item.get("up_id"))
            capability = item.get("capability")
            if live is None or live.owner_hash != digest or not isinstance(capability, str):
                continue
            plan.append((live.id, capability))
            free_count += 1
            free_bytes += live.size
        return plan if free_count >= need_count and free_bytes >= need_bytes else None

    async def import_recovery(self, source_task: TaskRecord, evidence: dict, owner: str) -> dict:
        """Import only an owned, committed raw source; no caller-supplied path.

        Even a live staging session is copied into independent ownership. Old
        task deletion can therefore never delete a recovered draft's sources.
        This bypasses complete()/preprocess(), NOT UploadStore admission/IO.
        """
        manifest = _validate(_Manifest, evidence["manifest"])
        if (manifest.owner_hash != hashlib.sha256(source_task.owner_hash.encode()).hexdigest()
                or manifest.status != "ready" or manifest.probe is None):
            raise HTTPException(409, {"code": "recovery_source_unverified"})
        asset = next((a for a in source_task.uploads if a.upload_id == manifest.id), None)
        if asset is None or asset.original_name != manifest.name or asset.size != manifest.size:
            raise HTTPException(409, {"code": "recovery_source_unverified"})
        # No absolute/relative path from a request or report is accepted.
        if Path(asset.stored_name).name != asset.stored_name or any(c in asset.stored_name for c in "/\\:"):
            raise HTTPException(409, {"code": "recovery_source_unverified"})
        source = source_task.task_dir / "raw" / asset.stored_name
        live = self._records.get(manifest.id)
        source_lock = None
        if live is not None and live.expires_at > time.time():
            if (live.owner_hash != manifest.owner_hash or live.status != "ready"
                    or live.sha256 != manifest.sha256 or live.size != manifest.size
                    or self._locks[live.id].locked() or live.id in self._jobs):
                raise HTTPException(409, {"code": "recovery_source_unverified"})
            source = self._source(live)
            source_lock = self._locks[live.id]
        _safe_path(source)
        if source.stat().st_size != manifest.size:
            raise HTTPException(409, {"code": "recovery_source_unverified"})

        def check_transcript_budget():
            from .uploads import MAX_TRANSCRIPT_MEMORY_BYTES
            if (self._transcript_size(manifest)
                    + sum(self._transcript_size(other) for other in self._records.values())
                    > MAX_TRANSCRIPT_MEMORY_BYTES):
                raise HTTPException(503, "Upload analysis cache is full")

        async with self._operation(), AsyncExitStack() as pins:
            # No suspension between choosing a live source and acquiring its
            # uncontended lock. Task-operation ownership protects raw fallback;
            # staging expiry/deletion instead consults this upload lock.
            if source_lock is not None:
                await pins.enter_async_context(source_lock)
            check_transcript_budget()
            receipt = await self.create(name=manifest.name, size=manifest.size, sha256=manifest.sha256,
                                        owner=owner, content_type=manifest.content_type, count_budget=False)
            imported = self.authorize(receipt["upload_id"], receipt["access_token"])
            try:
                destination = self._directory(imported) / ("source" + Path(imported.name).suffix.lower())
                await _blocking(_copy_verified, source, destination, manifest.size, manifest.sha256)

                def chunks(stop):
                    result = {}
                    with _open_file(self._source(imported)) as stream:
                        index = 0
                        while data := stream.read(CHUNK_SIZE):
                            _check_stop(stop)
                            result[str(index)] = _Chunk(size=len(data), sha256=hashlib.sha256(data).hexdigest())
                            index += 1
                    return result

                imported.chunks = await _blocking(chunks)
                # Other preprocessing may have committed while copy/hash work
                # awaited. Recheck immediately before publishing retained ASR.
                check_transcript_budget()
                for key in ("probe", "has_speech", "speech_evidence", "speech_intervals", "transcript",
                            "silences", "asr_result", "asr_attempted", "asr_cached_at"):
                    setattr(imported, key, copy.deepcopy(getattr(manifest, key)))
                imported.status, imported.phase, imported.progress = "ready", "done", 100
                # Keep create()'s created_at + TTL bound: extending by copy time
                # makes _restore reject our own receipt as an orphan.
                self._save(imported)
                return receipt
            except BaseException:
                await drain_task(asyncio.create_task(self.delete(imported.id, receipt["access_token"])))
                raise


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DraftCreate(_Strict):
    mode: Mode


class DraftRecovery(_Strict):
    step: int = Field(ge=1, le=2, strict=True)
    mode: Mode | None = None
    # Explicit consent to repeated footage, carried into the recovered draft (only after a shortage failure).
    allow_shot_reuse: bool | None = Field(default=None, strict=True)


class FileOptions(_Strict):
    note: str = Field(default="", max_length=20)
    in_sec: float = Field(default=0, ge=0, strict=True)
    out_sec: float | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def valid(self):
        if any(ord(c) < 32 or ord(c) == 127 for c in self.note):
            raise ValueError("Invalid note")
        if self.out_sec is not None and self.out_sec <= self.in_sec:
            raise ValueError("Invalid source range")
        return self


class DraftFile(FileOptions):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, strict=True)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    content_type: str = Field(default="application/octet-stream", max_length=100)


class AssetOptions(_Strict):
    note: str = Field(default="", max_length=20)
    trim_start: float = Field(default=0, ge=0, strict=True)
    trim_end: float | None = Field(default=None, gt=0, strict=True)

    def file_options(self) -> FileOptions:
        return _validate(FileOptions, {"note": self.note, "in_sec": self.trim_start, "out_sec": self.trim_end})


def _validate(model, raw):
    try:
        return model.model_validate(raw)
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(422, "Invalid draft parameters") from None


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


async def bounded_json(request: Request) -> dict:
    data = bytearray()
    async with asyncio.timeout(30):
        async for chunk in request.stream():
            if len(data) + len(chunk) > 256 * 1024:
                raise HTTPException(413, "Request is too large")
            data.extend(chunk)
    try:
        raw = json.loads(data or b"{}")
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Invalid JSON") from None
    if not isinstance(raw, dict):
        raise HTTPException(422, "Expected an object")
    return raw


def _retry_binding(record: TaskRecord, settings) -> str:
    """Bind accepted inputs, source bytes and implementation; never persist secrets."""
    from .mode_pipeline import _sha, _key
    return _key({"script": record.script, "mode": record.mode, "rules": record.rules_version,
                 "preferences": record.preferences.model_dump(mode="json"),
                 "sentences": [s.model_dump(mode="json") for s in record.sentences],
                 "speakers": [s.model_dump(mode="json") for s in record.speakers],
                 "settings": settings.model_dump(mode="json"),
                 "speaker_model": (_sha(settings.local_speaker_model_path)
                                   if settings.local_speaker_model_path and settings.local_speaker_model_path.is_file() else None),
                 "snapshots": _sha(_safe_path(record.task_dir / "pretranscripts.json")),
                 "sources": [[a.model_dump(mode="json"), _sha(_safe_path(a.path))] for a in record.uploads],
                 "implementation": {name: _sha(Path(__file__).parent / name) for name in
                                    ("mode_pipeline.py", "production_modes.py", "mode_rules.json", "pipeline.py",
                                     "media.py", "speech_analysis.py", "providers/local_speech.py", "vision_pipeline.py",
                                     "providers/vision.py", "matching.py", "assignment.py", "tts_pipeline.py",
                                     "audio_filters.py", "providers/tts.py", "pronunciation.py", "rendering.py", "graphics.py")}})


async def retry_verified_mode(record: TaskRecord, service) -> dict:
    """Default lazy hook: verify real cache receipts BEFORE queueing; never charge.

    The pipeline independently recalculates every stage signature. A begun but
    unconfirmed provider receipt is not permission to resubmit it, even when its
    signature no longer matches. Missing receipts for finished provider stages
    likewise fail closed instead of quietly replaying earlier work.
    """
    from .mode_pipeline import ModeStageCache
    from .pipeline import _run_blocking_until_complete
    from .revisions import read_json

    def verify():
        if not record.draft_context.get("retry_binding") or record.rules_version != 2:
            raise HTTPException(409, {"code": "retry_same_unavailable", "supported": False})
        if _retry_binding(record, service.settings) != record.draft_context["retry_binding"]:
            raise HTTPException(409, {"code": "retry_inputs_changed"})
        cache = ModeStageCache(record.task_dir)
        verified = set()
        for stage in (2, 3, 4, 6, 7, 9):
            path = record.task_dir / "mode_stage_cache" / f"stage-{stage}.json"
            if not path.exists():
                if stage in (4, 6, 7) and record.stages[stage - 1].status.value != "pending":
                    raise HTTPException(409, {"code": "retry_cache_missing", "stage": stage})
                continue
            value = read_json(record.task_dir, path.relative_to(record.task_dir).as_posix())
            if stage == 6 and value.get("state") == "retry_safe":
                continue  # failed locally after provider work ended; it is simply rerun
            if value.get("state") != "complete" or not isinstance(value.get("signature"), str):
                raise HTTPException(409, {"code": "retry_provider_uncertain", "stage": stage})
            if cache.load(stage, value["signature"]) is None:
                raise HTTPException(409, {"code": "retry_cache_invalid", "stage": stage})
            verified.add(stage)
        if not verified:
            raise HTTPException(409, {"code": "retry_same_unavailable", "supported": False})
        snapshots = read_json(record.task_dir, "pretranscripts.json")
        if any(s.get("task_asr_status") in {"started", "cancelled", "failed"} for s in snapshots):
            raise HTTPException(409, {"code": "retry_provider_uncertain", "stage": 2})
        return next((stage.number for stage in record.stages if stage.status.value != "done"
                     or (stage.number in (2, 3, 4, 6, 7, 9) and stage.number not in verified)), 10)

    try:
        first = await _run_blocking_until_complete(verify)
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        raise HTTPException(409, {"code": "retry_cache_invalid"}) from None
    service.check(record)
    service.manager.ensure_start_capacity(exclude_task_id=record.task_id, v2=True)
    total = sum(a.size for a in record.uploads)
    requested = total + math.ceil(service.uploads.reserved_bytes / service.settings.task_disk_reservation_multiplier)
    async with service.reserve(requested) as reservation:
        previous = (record.status, record.resume_from, record.reserved_disk_bytes)
        record.reserved_disk_bytes = reservation.reserved_bytes
        try:
            service.manager.enqueue_retry(record.task_id, first)
            reservation.retain()
        except BaseException:
            record.status, record.resume_from, record.reserved_disk_bytes = previous
            service.manager._persist_record(record)
            raise
    return {"task_id": record.task_id, "id": record.task_id, "status": "queued",
            "queue": service.manager.status_response(record).queue, "resume_from": first}


class DraftService:
    def __init__(self, settings, manager: TaskManager, uploads, guard) -> None:
        self.settings, self.manager, self.uploads, self.guard = settings, manager, uploads, guard
        self.uploads.before_asr = self._before_asr
        self.ledger = AdmissionLedger(settings.data_dir)
        self._gate = asyncio.Lock()
        self._operations: set[asyncio.Task] = set()
        self._closed = False
        self._identities: dict[str, tuple[int, int]] = {}
        for record in manager._tasks.values():
            if record.lifecycle_v2:
                self._identities[record.task_id] = AdmissionLedger._identity(_safe_path(record.task_dir, directory=True))
                if record.status == TaskState.draft and self.ledger.accepted(record.task_id):
                    record.status = TaskState.failed
                    record.error_kind = "transient"
                    record.error_message = "Accepted start was interrupted before enqueue; no automatic retry"
                    record.message = "已接收的制作请求中断，请检查状态"
                    manager._persist_record(record)
        self.retry_hook = retry_verified_mode
        self.legacy_counts = lambda owner, ip: (0, 0, 0)

    def _probe_budget(self, record: TaskRecord) -> None:
        """Current attached originals, not edited ranges or caller metadata.

        Synchronous on the service loop; no lock inversion with upload workers.
        Unknown concurrent probes are rejected, never counted as zero seconds.
        """
        self.check(record, draft=True)
        files = record.draft_context.get("files", [])
        if not files or len(files) > self.uploads.max_files:
            raise HTTPException(409, "No validated file selection")
        seconds = 0.0
        for item in files:
            upload = self.uploads.authorize(item["up_id"], item["capability"])
            probe = upload.probe
            if (probe is None or upload.status not in {"probed", "processing", "ready", "asr_failed", "interrupted"}
                    or len(upload.chunks) != math.ceil(upload.size / CHUNK_SIZE)):
                raise HTTPException(409, "All attached files require server metadata validation before transcription")
            if (not math.isfinite(probe.sec) or not 0 < probe.sec <= self.uploads.max_seconds
                    or not 0 < probe.width <= self.settings.max_source_width
                    or not 0 < probe.height <= self.settings.max_source_height
                    or not math.isfinite(probe.fps) or not 0 < probe.fps <= self.settings.max_source_frame_rate):
                raise HTTPException(413, "Original source metadata exceeds limits")
            seconds += probe.sec
        if seconds > self.uploads.max_total_seconds:
            raise HTTPException(413, "Original source duration exceeds task budget")

    def _before_asr(self, upload) -> None:
        for record in self.manager._tasks.values():
            if record.lifecycle_v2 and any(item["up_id"] == upload.id for item in record.draft_context.get("files", [])):
                self._probe_budget(record)
                return

    @asynccontextmanager
    async def operation(self):
        if self._closed or self.manager.is_draining:
            raise HTTPException(503, "Service is draining")
        # One gate serialises every visitor's draft operations. They are short, so wait for a turn
        # instead of failing a concurrent visitor at once (that left uploads stuck at "等待上传").
        try:
            await asyncio.wait_for(self._gate.acquire(), timeout=20)
        except TimeoutError:
            raise HTTPException(409, "Draft operation pending; read state before retrying") from None
        task = asyncio.current_task()
        assert task is not None
        try:
            if self._closed or self.manager.is_draining:
                raise HTTPException(503, "Service is draining")
            self._operations.add(task)
            try:
                self.ledger.check()
                yield
            finally:
                self._operations.discard(task)
        finally:
            self._gate.release()

    def check(self, record: TaskRecord, *, draft: bool = False) -> None:
        self.ledger.check()
        if not record.lifecycle_v2 or record.local_only or self.manager.get(record.task_id) is not record:
            raise HTTPException(404, "Task not found")
        path = _safe_path(record.task_dir, directory=True)
        if path.parent != self.ledger.data_dir or path.name != record.task_id:
            raise HTTPException(404, "Task not found")
        identity = AdmissionLedger._identity(path)
        if self._identities.setdefault(record.task_id, identity) != identity:
            raise HTTPException(409, "Task directory changed")
        if record.status == TaskState.draft and record.draft_context.get("expires_at", 0) <= time.time():
            raise HTTPException(410, {"code": "draft_expired", "task_id": record.task_id})
        if draft and (record.status != TaskState.draft or self.ledger.accepted(record.task_id)):
            raise HTTPException(409, {"code": "already_accepted", "task_id": record.task_id,
                                      "status": record.status.value})

    def touch(self, record: TaskRecord) -> None:
        self.check(record, draft=True)
        record.updated_at = datetime.now(timezone.utc)
        record.draft_context["expires_at"] = time.time() + 86400
        for item in record.draft_context["files"]:
            upload = self.uploads.authorize(item["up_id"], item["capability"])
            # Upload manifests are restored against their original 72h ceiling.
            # Extending beyond it makes an otherwise valid probe disappear on
            # restart; task edits cannot renew the original staging lease.
            upload.expires_at = min(upload.created_at + 72 * 3600, max(upload.expires_at, time.time() + 72 * 3600))
            self.uploads._save(upload)
        self.manager._persist_record(record)

    async def create(self, raw: dict, owner: str) -> dict:
        payload = _validate(DraftCreate, raw)
        async with self.operation():
            # Drafts do not occupy queue slots or spend start quota, but they
            # still have bounded disk/metadata admission.
            own = sum(r.lifecycle_v2 and r.owner_hash == owner and r.status == TaskState.draft
                      for r in self.manager._tasks.values())
            count = sum(r.status == TaskState.draft for r in self.manager._tasks.values())
            if own >= 20 or count >= 1000:
                raise HTTPException(429, "Draft storage limit reached")
            capacity = await self.guard.snapshot()
            if capacity.available_after_reservations_bytes < self.settings.minimum_free_disk_bytes + 1024 * 1024:
                raise HTTPException(507, "Insufficient draft storage")
            key = uuid4().hex
            directory = create_task_dir(self.settings, key)
            record = TaskRecord(key, directory, "", [], mode=payload.mode, mode_contract=True,
                                lifecycle_v2=True, owner_hash=owner, status=TaskState.draft,
                                message="草稿", draft_context={"version": 2, "files": [],
                                "expires_at": time.time() + 86400, "type_marks": {}, "speaker_names": {},
                                "source_voice_preferred": False})
            try:
                self.manager._persist_record(record)
            except BaseException:
                await self.manager.discard_unaccepted_upload(key, directory)
                raise
            self.manager._tasks[key] = record
            self._identities[key] = AdmissionLedger._identity(directory)
            return {"id": key, "task_id": key, "access_token": record.access_token,
                    "status": "draft", "expires_at": _iso(record.draft_context["expires_at"])}

    def item(self, record: TaskRecord, file_id: str) -> dict:
        self.check(record)
        item = next((item for item in record.draft_context.get("files", []) if item["file_id"] == file_id), None)
        if item is None:
            raise HTTPException(404, "File not found")
        self.uploads.authorize(item["up_id"], item["capability"])
        return item

    def file_view(self, record: TaskRecord, item: dict) -> dict:
        try:
            view = self.uploads.read(item["up_id"], item["capability"])
        except HTTPException as exc:
            if exc.status_code != 404 or record.status == TaskState.draft:
                raise
            # Completed jobs own materialized copies. Expired staging sessions
            # must not make the task status/history unpollable after 72 hours.
            asset = next((a for a in record.uploads if a.upload_id == item["up_id"]), None)
            if asset is None:
                raise
            view = {"id": item["up_id"], "name": asset.original_name, "bytes": asset.size,
                    "sec": asset.source_duration_seconds, "status": "expired", "has_speech": None,
                    "transcript": {}, "received_bytes": asset.size, "thumb_url": None, "wave_url": None}
        view.update({"file_id": item["file_id"], "up_id": item["up_id"], "upload_id": item["up_id"], "size": view["bytes"],
                     "note": item["note"], "in_sec": item["in_sec"], "out_sec": item["out_sec"],
                     "chunksize": CHUNK_SIZE})
        base = f"/api/tasks/{record.task_id}/files/{item['file_id']}"
        view["thumb_url"] = base + "/thumb" if view["thumb_url"] else None
        view["wave_url"] = base + "/wave" if view["wave_url"] else None
        return view

    def view(self, record: TaskRecord) -> dict:
        self.check(record)
        files = [self.file_view(record, item) for item in record.draft_context.get("files", [])]
        ready_files = [file for file in files if file["status"] == "ready"]
        prefs = record.preferences.model_dump(mode="json")
        prefs["target_cpm"] = record.preferences.target_chars_per_minute
        return {**self.manager.status_response(record).model_dump(mode="json"),
                "id": record.task_id, "files": files, "script": record.script,
                "preferences": prefs, "prefs": prefs,
                "sentences": [s.model_dump(mode="json") for s in record.sentences],
                "speakers": [s.model_dump(mode="json") for s in record.speakers],
                "rules_version": record.rules_version,
                "asset_options": [{"note": f["note"], "trim_start": f["in_sec"], "trim_end": f["out_sec"]}
                                  for f in record.draft_context.get("files", [])],
                "local_speech": record.draft_context.get("local_speech"),
                "type_marks": record.draft_context.get("type_marks", {}),
                "speaker_names": record.draft_context.get("speaker_names", {}),
                "source_voice_preferred": record.draft_context.get("source_voice_preferred", False),
                "stats": {"files": len(ready_files), "bytes": sum(f["bytes"] for f in ready_files),
                          "seconds": sum(f["sec"] or 0 for f in ready_files)},
                "ready": bool(files) and len(ready_files) == len(files),
                "transcripts": {f["file_id"]: f["transcript"] for f in files},
                "speech": {f["file_id"]: f["has_speech"] for f in files},
                "upload_bytes": sum(f["bytes"] for f in files),
                "received_bytes": sum(f["received_bytes"] for f in files),
                "upload_seconds": sum(f["sec"] or 0 for f in files),
                "expires_at": (_iso(record.draft_context["expires_at"]) if record.status == TaskState.draft else
                               _iso(self.manager._record_retention_reference(record).timestamp() + 72 * 3600)
                               if record.status in {TaskState.done, TaskState.failed, TaskState.cancelled} else None),
                "accepted": self.ledger.accepted(record.task_id),
                "retry_same_supported": self.retry_hook is not None,
                # The client decides between cache-resume retry and the legacy full re-run from this flag.
                "lifecycle_v2": record.lifecycle_v2,
                "shot_reuse_accepted": (record.draft_context.get("shot_reuse") or {}).get("accepted") is True,
                "created_at": record.created_at.isoformat(), "updated_at": record.updated_at.isoformat()}

    async def reclaim_finished_staging(self, owner: str, size: int) -> int:
        """Free upload staging that only finished tasks still hold, when it blocks a new upload.

        An accepted task copies its originals into its own task_dir/raw; the staging session
        (kept 72 h) is then redundant, and recovery/retry fall back to the task's own copy. Without
        this, a handful of finished or failed works use up the per-session file quota and the
        next work can upload one file before every other file is refused. Only this owner's
        idle sessions of accepted, terminal tasks whose copy exists are released, oldest first,
        and only as many as needed; drafts and running tasks are never touched.
        """
        digest = hashlib.sha256(owner.encode()).hexdigest()
        own = [record for record in self.uploads._records.values() if record.owner_hash == digest]
        free_count = self.uploads.max_files - len(own)
        free_bytes = self.uploads.max_total_bytes - sum(record.size for record in own)
        if free_count >= 1 and free_bytes >= size:
            return 0
        terminal = {TaskState.done, TaskState.failed, TaskState.cancelled}
        candidates = sorted((task for task in self.manager._tasks.values() if task.owner_hash == owner
                             and task.status in terminal and task.lifecycle_v2 and self.ledger.accepted(task.task_id)),
                            key=lambda task: task.updated_at)
        released = 0
        for task in candidates:
            materialized = {asset.upload_id: asset for asset in task.uploads}
            for item in task.draft_context.get("files", []):
                if free_count >= 1 and free_bytes >= size:
                    return released
                live = self.uploads._records.get(item.get("up_id"))
                asset = materialized.get(item.get("up_id"))
                capability = item.get("capability")
                if (live is None or live.owner_hash != digest or asset is None or not isinstance(capability, str)
                        or not (task.task_dir / "raw" / asset.stored_name).is_file()):
                    continue
                try:
                    await self.uploads.delete(live.id, capability)
                except (HTTPException, OSError):
                    continue
                released += 1
                free_count += 1
                free_bytes += live.size
        return released

    async def add_file(self, record: TaskRecord, raw: dict, task_token: str) -> dict:
        payload = _validate(DraftFile, raw)
        async with self.operation():
            self.check(record, draft=True)
            files = record.draft_context["files"]
            if len(files) >= min(20, self.settings.max_files):
                raise HTTPException(422, "Too many files")
            if sum(self.uploads.read(f["up_id"], f["capability"])["bytes"] for f in files) + payload.size > self.uploads.max_total_bytes:
                raise HTTPException(413, "Task upload budget exceeded")
            capacity = await self.guard.snapshot()
            if capacity.free_bytes < self.settings.minimum_free_disk_bytes + capacity.reserved_bytes + self.uploads.reserved_bytes + self.uploads._reservation(payload.size):
                raise HTTPException(507, "Insufficient shared upload/task storage")
            await self.reclaim_finished_staging(record.owner_hash, payload.size)
            receipt = await self.uploads.create(name=payload.name, size=payload.size,
                sha256=payload.sha256 or "0" * 64, owner=record.owner_hash, content_type=payload.content_type)
            item = {"file_id": receipt["upload_id"], "up_id": receipt["upload_id"],
                    "capability": receipt["access_token"], "supplied_hash": payload.sha256 is not None,
                    **payload.model_dump(include={"note", "in_sec", "out_sec"})}
            files.append(item)
            try:
                self.touch(record)
            except BaseException:
                files.remove(item)
                await drain_task(asyncio.create_task(self.uploads.delete(item["up_id"], item["capability"])))
                raise
            return {**self.file_view(record, item), "token": task_token, "access_token": task_token,
                    "chunk_size": CHUNK_SIZE,
                    "put_url": f"/api/tasks/{record.task_id}/files/{item['file_id']}/chunks/{{index}}"}

    async def file_action(self, record: TaskRecord, file_id: str, action: str,
                          *, raw: dict | None = None, request: Request | None = None, index: int = 0):
        async with self.operation():
            self.check(record, draft=True)
            item = self.item(record, file_id)
            if action == "chunk":
                assert request is not None
                length = request.headers.get("content-length", "")
                result = await self.uploads.put_chunk(item["up_id"], item["capability"], index,
                    request.stream(), int(length) if length.isdigit() else None)
            elif action == "probe":
                if raw != {} or not item["supplied_hash"]:
                    raise HTTPException(422, "Metadata probe requires a verified digest and empty body")
                result = await self.uploads.probe_only(item["up_id"], item["capability"])
            elif action == "complete":
                # Two-phase clients cannot schedule preprocessing until every
                # attached original is known. Legacy completion still probes in
                # its worker and encounters the same guard before paid ASR.
                if self.uploads.authorize(item["up_id"], item["capability"]).status == "probed":
                    self._probe_budget(record)
                result = await self.uploads.complete_optional(item["up_id"], item["capability"], supplied_hash=item["supplied_hash"])
            elif action == "pretranscribe":
                if raw != {"retry": True}:
                    raise HTTPException(422, "Explicit retry:true is required")
                self._probe_budget(record)
                result = await self.uploads.retry_pretranscribe(item["up_id"], item["capability"])
            elif action == "patch":
                options = _validate(FileOptions, {**{k: item[k] for k in ("note", "in_sec", "out_sec")}, **(raw or {})})
                state = self.uploads.read(item["up_id"], item["capability"])
                if state["sec"] is not None and (options.in_sec >= state["sec"] or (options.out_sec or 0) > state["sec"]):
                    raise HTTPException(422, "Source range exceeds file duration")
                item.update(options.model_dump())
                result = {}
            elif action == "delete":
                await self.uploads.delete(item["up_id"], item["capability"])
                record.draft_context["files"].remove(item)
                self.touch(record)
                return None
            else:
                raise HTTPException(404, "Unknown action")
            if action != "probe":
                self.touch(record)
            return {**result, **self.file_view(record, item)}

    def editorial(self, record: TaskRecord, raw: dict, *, starting: bool, preview: bool = False) -> dict:
        allowed = {"script", "mode", "prefs", "preferences", "type_marks", "speaker_names", "sentences", "speakers", "quality_gate_mode", "asset_options", "source_voice_preferred"}
        if set(raw) - allowed:
            raise HTTPException(422, "Unsupported creation parameters")
        source_voice_preferred = raw.get("source_voice_preferred", record.draft_context.get("source_voice_preferred", False))
        if type(source_voice_preferred) is not bool:
            raise HTTPException(422, "source_voice_preferred must be a boolean")
        mode = raw.get("mode", record.mode)
        if mode not in {"voiceover", "mixed", "original"}:
            raise HTTPException(422, "Invalid mode")
        script = raw.get("script", record.script)
        if not isinstance(script, str) or len(script) > 8000 or any(ord(c) < 32 and c not in "\n\r\t" for c in script):
            raise HTTPException(422, "Invalid script")
        preferences = raw.get("prefs", raw.get("preferences", record.preferences.model_dump()))
        if not isinstance(preferences, dict):
            raise HTTPException(422, "Invalid preferences")
        preferences = dict(preferences)
        gate = raw.get("quality_gate_mode", preferences.pop("quality_gate_mode", self.settings.quality_gate_mode))
        target = preferences.pop("target_cpm", None)
        prefs = _validate(EditingPreferences, preferences)
        if target is not None and (type(target) not in (int, float) or target != prefs.target_chars_per_minute):
            raise HTTPException(422, "target_cpm is derived by the server")
        if gate not in {"warn", "block"} or (self.settings.quality_gate_mode == "block" and gate != "block"):
            raise HTTPException(422, "Quality gate cannot weaken server policy")
        names = raw.get("speaker_names", record.draft_context.get("speaker_names", {}))
        if not isinstance(names, dict) or len(names) > 100:
            raise HTTPException(422, "Invalid speaker names")
        people = {s.id: s for s in record.speakers}
        if "speakers" in raw:
            if not isinstance(raw["speakers"], list) or len(raw["speakers"]) > 100:
                raise HTTPException(422, "Invalid speakers")
            validated = [_validate(Speaker, item) for item in raw["speakers"]]
            people = {s.id: s for s in validated}
            if len(people) != len(validated):
                raise HTTPException(422, "Duplicate speaker identity")
        for key, value in names.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 128:
                raise HTTPException(422, "Invalid speaker identity")
            value = {"name": value} if isinstance(value, str) else value
            if not isinstance(value, dict) or set(value) - {"name", "role", "title"}:
                raise HTTPException(422, "Invalid speaker name/role")
            if any(not isinstance(v, str) or len(v) > (8 if k == "name" else 12) for k, v in value.items()):
                raise HTTPException(422, "Invalid speaker name/role")
            previous = people.get(key, Speaker(id=key)).model_dump()
            people[key] = _validate(Speaker, {**previous, "name": value.get("name", previous["name"]),
                                            "title": value.get("role", value.get("title", previous["title"]))})
        for person in people.values():
            # Upload-scoped opaque IDs are not display names (currently 104
            # characters); preserve the upload/frontend 128-character contract.
            if (len(person.id) > 128 or len(person.name) > 8 or len(person.title) > 12
                    or any(ord(c) < 32 or ord(c) == 127 for c in person.id + person.name + person.title)):
                raise HTTPException(422, "Speaker identity max 128, name max 8 and role max 12 characters")
        marks = raw.get("type_marks", record.draft_context.get("type_marks", {}))
        if not isinstance(marks, (dict, list)) or len(marks) > 200:
            raise HTTPException(422, "Invalid type marks")
        if starting:
            # Preserve body newlines: mixed-mode name/role and quote markers
            # are line-scoped. The legacy helper joins them and loses intent.
            lines = script.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip().splitlines()
            title = lines[0].strip() if lines else ""
            body = "\n".join(lines[1:]).strip()
            if not title or len(title) > 40 or title[-1] in "。！？.!?；;" or not body:
                raise HTTPException(422, "A headline and nonempty body are required")
            if unicodedata.normalize("NFKC", title) == "(写一个标题)":
                raise HTTPException(422, "Replace the placeholder headline")
            if mode != "original" and len(script.strip()) < 20:
                raise HTTPException(422, "A/B script must contain at least 20 characters")
            script = title + "\n\n" + body
        try:
            parsed = parse_sentences(script, mode) if script.strip() else []
        except (ValueError, TypeError):
            raise HTTPException(422, "Unable to parse the supplied script") from None
        supplied = raw.get("sentences")
        if supplied is None:
            sentences = parsed
        elif not isinstance(supplied, list) or len(supplied) > 200:
            raise HTTPException(422, "Invalid sentences")
        else:
            sentences = [_validate(SentenceInput, item) for item in supplied]
        entries = enumerate(marks) if isinstance(marks, list) else marks.items()
        for key, kind in entries:
            if str(key).isdigit() and int(key) < len(sentences) and kind in {"quote", "narration"}:
                sentences[int(key)] = sentences[int(key)].model_copy(update={"kind": kind})
            else:
                raise HTTPException(422, "Type marks must refer to existing sentence indices")
        def literal(text):
            return "".join(c for c in unicodedata.normalize("NFKC", text) if c.isalnum())
        if supplied is not None:
            if literal("".join(s.text for s in parsed)) != literal("".join(s.text for s in sentences)):
                raise HTTPException(422, "Sentences do not match the full script")
            # Bind to the actual parser's source boundaries, not row numbers or
            # a second parse using manually changed kinds. Repeated text and
            # client-confirmed merged/split rows retain their literal offsets.
            boundaries, offset = {}, 0
            for sentence in parsed:
                offset += len(literal(sentence.text))
                boundaries[offset] = sentence.terminal_punctuation
            offset = 0
            for index, sentence in enumerate(sentences):
                offset += len(literal(sentence.text))
                terminal = boundaries.get(offset, "")
                if ("terminal_punctuation" in sentence.model_fields_set
                        and sentence.terminal_punctuation != terminal):
                    raise HTTPException(422, "Sentence punctuation does not match the script boundary")
                sentences[index] = sentence.model_copy(update={"terminal_punctuation": terminal})
        if starting and not sentences:
            raise HTTPException(422, "At least one body sentence is required")
        if [s.idx for s in sentences] != list(range(len(sentences))):
            raise HTTPException(422, "Sentence indices must be contiguous")
        if mode == "voiceover" and (starting or not preview) and any(s.kind != "narration" for s in sentences):
            raise HTTPException(422, "Voiceover requires narration")
        if mode == "original" and any(s.kind != "quote" for s in sentences):
            raise HTTPException(422, "Original mode requires quotes")
        ids = {f["up_id"] for f in record.draft_context["files"]}
        if any(s.source_hint and s.source_hint.upload_id not in ids for s in sentences):
            raise HTTPException(422, "Source hint belongs to another task")
        if mode == "original":
            prefs = prefs.model_copy(update={"voice": "ai", "pacing": "normal", "generative_fill": False})
        files = record.draft_context["files"]
        options = [{"note": f["note"], "in_sec": f["in_sec"], "out_sec": f["out_sec"]} for f in files]
        if "asset_options" in raw:
            supplied_options = raw["asset_options"]
            if not isinstance(supplied_options, list) or len(supplied_options) != len(files):
                raise HTTPException(422, "asset_options must match the current file order and count")
            options = [_validate(AssetOptions, value).file_options().model_dump() for value in supplied_options]
        for item, option in zip(files, options, strict=True):
            state = self.uploads.read(item["up_id"], item["capability"])
            seconds = state["sec"]
            if seconds is not None and (option["in_sec"] >= seconds or (option["out_sec"] or 0) > seconds):
                raise HTTPException(422, "Source range exceeds verified file duration")
        return {"mode": mode, "script": script, "preferences": prefs, "sentences": sentences,
                "speakers": list(people.values()), "quality_gate_mode": gate,
            "type_marks": marks, "speaker_names": names, "file_options": options,
            "source_voice_preferred": source_voice_preferred}

    def apply_editorial(self, record: TaskRecord, data: dict) -> None:
        for key in ("mode", "script", "preferences", "sentences", "speakers", "quality_gate_mode"):
            setattr(record, key, data[key])
        for key in ("type_marks", "speaker_names", "source_voice_preferred"):
            record.draft_context[key] = data[key]
        for item, options in zip(record.draft_context["files"], data["file_options"], strict=True):
            item.update(options)
        record.__post_init__()

    async def patch(self, record: TaskRecord, raw: dict) -> dict:
        async with self.operation():
            self.check(record, draft=True)
            data = self.editorial(record, raw, starting=False)
            previous = {key: copy.deepcopy(value) for key, value in vars(record).items() if key not in {"background"}}
            try:
                self.apply_editorial(record, data)
                self.touch(record)
            except BaseException:
                vars(record).update(previous)
                raise
            return self.view(record)

    def snapshots(self, record: TaskRecord, *, ready: bool, options: list[dict] | None = None) -> list[dict]:
        files = record.draft_context["files"]
        if ready and not files:
            raise HTTPException(422, "At least one ready file is required")
        result = []
        for index, source in enumerate(files):
            item = {**source, **(options[index] if options is not None else {})}
            state = self.uploads.read(item["up_id"], item["capability"])
            if state["status"] != "ready":
                if ready:
                    raise HTTPException(422, {"code": "files_not_ready", "file_id": item["file_id"], "status": state["status"]})
                continue
            if item["in_sec"] >= state["sec"] or (item["out_sec"] or 0) > state["sec"]:
                raise HTTPException(422, "Source marks exceed verified duration")
            # Only complete source segments inside the selected range can be
            # matched. Never invent boundary word timing or trimmed transcript.
            lo, hi = item["in_sec"], item["out_sec"] or state["sec"]
            state["transcript"]["segments"] = [s for s in state["transcript"]["segments"] if s["start"] >= lo and s["end"] <= hi]
            result.append(state)
        return result

    async def matches(self, data: dict, snapshots: list[dict]) -> list[dict]:
        from functools import partial
        from .pipeline import _run_blocking_until_complete
        try:
            return await _run_blocking_until_complete(partial(align_quotes, data["sentences"], snapshots, data["speakers"]))
        except ValueError:
            raise HTTPException(422, "Unable to safely align the supplied sentences") from None

    async def analyze_speakers(self, record: TaskRecord, snapshots: list[dict], *, assets=None) -> dict:
        """Use real task-local raw copies, never another ASR call or invented IDs.

        The warm key binds current provider evidence and source digests. Preview
        copies live in a bounded temporary child and are drained before removal.
        Unavailable runtimes return their actual prerequisites without copying.
        """
        from .mode_pipeline import analyze_task_speakers, _key, _sha
        from .providers.local_speech import local_speech_readiness, SpeechUnavailable
        from .pipeline import _run_blocking_until_complete
        readiness = local_speech_readiness("speaker_embedding", self.settings.local_speaker_model_path,
                                          license_reviewed=self.settings.local_speech_license_reviewed)
        if not readiness.available:
            result = readiness.as_dict()
            if self.settings.local_speech_required:
                raise HTTPException(503, {"code": "local_speech_unavailable", **result})
            record.draft_context["local_speech"] = result
            return result
        items = {f["up_id"]: f for f in record.draft_context["files"]}
        ids = [s["id"] for s in snapshots]
        tokens = {key: items[key]["capability"] for key in ids}
        uploads = [self.uploads.authorize(key, tokens[key]) for key in ids]
        for snapshot, upload in zip(snapshots, uploads, strict=True):
            snapshot["audio_offset_seconds"] = upload.probe.audio_offset if upload.probe else 0.0
        model_path = self.settings.local_speaker_model_path
        key = await _run_blocking_until_complete(lambda: _key([
            [_sha(self.uploads._source(upload)) for upload in uploads], snapshots,
            _sha(model_path), self.settings.local_speech_license_reviewed,
            [_sha(Path(__file__).parent / name) for name in ("mode_pipeline.py", "speech_analysis.py", "providers/local_speech.py")]]))
        prior = record.draft_context.get("speaker_analysis_cache", {})
        if prior.get("key") == key:
            snapshots[:] = copy.deepcopy(prior["snapshots"])
            return copy.deepcopy(prior["result"])
        temporary_record = copy.copy(record)
        try:
            if assets is not None:
                temporary_record.uploads = assets
                result = await analyze_task_speakers(temporary_record, snapshots, self.settings)
            elif ids:
                total = sum(u.size for u in uploads)
                async with self.reserve(total):
                    with tempfile.TemporaryDirectory(prefix=".draft-speakers-", dir=record.task_dir) as directory:
                        temporary_record.task_dir = Path(directory)
                        temporary_record.uploads, _ = await self.uploads.materialize(ids, tokens, Path(directory))
                        result = await analyze_task_speakers(temporary_record, snapshots, self.settings)
            else:
                return readiness.as_dict()
        except SpeechUnavailable:
            raise HTTPException(503, {"code": "local_speech_inference_unavailable", "inference_verified": False}) from None
        record.draft_context["speaker_analysis_cache"] = {"key": key, "snapshots": copy.deepcopy(snapshots), "result": result}
        record.draft_context["local_speech"] = result
        self.manager._persist_record(record)
        return result

    async def align(self, record: TaskRecord, raw: dict) -> dict:
        async with self.operation():
            self.check(record, draft=True)
            if "script" not in raw and not record.script.strip() and isinstance(raw.get("sentences"), list):
                # Sentence-only callers (the three preview screens) share the
                # same matcher without having to persist an incomplete script.
                supplied = [_validate(SentenceInput, item) for item in raw["sentences"]]
                raw = {**raw, "script": "预览\n\n" + "\n".join(s.text for s in supplied)}
            data = self.editorial(record, raw, starting=False, preview=True)
            snapshots = self.snapshots(record, ready=False, options=data["file_options"])
            speech = await self.analyze_speakers(record, snapshots)
            rows = await self.matches(data, snapshots)
            self.check(record, draft=True)
            # Persist task evidence even when prerequisites are unavailable or
            # there are no ready sources. Never commit projected editorial data.
            record.draft_context["local_speech"] = speech
            self.manager._persist_record(record)
            return {"matches": rows, "match_ok": .85, "match_low": .6, "local_speech": speech,
                    "rules_version": record.rules_version}

    async def start(self, record: TaskRecord, raw: dict, ip: str, task_token: str | None = None) -> dict:
        async with self.operation():
            self.check(record, draft=True)
            # Bound failed/pre-admission attempts: only this operation's newly
            # created raw files are removed. Never delete pre-existing sources.
            raw_dir = record.task_dir / "raw"
            previous_raw = set(raw_dir.iterdir()) if raw_dir.is_dir() else set()
            previous_record = {key: copy.deepcopy(value) for key, value in vars(record).items() if key != "background"}
            try:
                return await self._start_locked(record, raw, ip, task_token)
            except BaseException:
                if not self.ledger.accepted(record.task_id):
                    self.check(record, draft=True)
                    if raw_dir.is_dir():
                        for path in raw_dir.iterdir():
                            if path not in previous_raw:
                                _safe_path(path).unlink()
                    vars(record).update(previous_record)
                    self.manager._persist_record(record)
                raise

    @asynccontextmanager
    async def reserve(self, requested: int):
        context = self.guard.reserve(requested)
        reservation = await context.__aenter__()
        try:
            yield reservation
        finally:
            # A second request cancellation cannot strand retained bytes or an
            # upload slot while the capacity guard is releasing its lock.
            await drain_task(asyncio.create_task(context.__aexit__(None, None, None)))

    async def _start_locked(self, record: TaskRecord, raw: dict, ip: str, task_token: str | None = None) -> dict:
            data = self.editorial(record, raw, starting=True)
            snapshots = self.snapshots(record, ready=True, options=data["file_options"])

            def validate_quotes(rows: list[dict]) -> None:
                missing = [row["idx"] + 1 for row in rows if row["kind"] == "quote" and row["source"] is None]
                if missing:
                    raise HTTPException(422, {"code": "quote_missing", "badRows": missing})
                if data["mode"] == "original":
                    texts = {sentence.idx: sentence.text for sentence in data["sentences"]}
                    unverified = [row["idx"] + 1 for row in rows if row["kind"] == "quote"
                                  and not quote_text_is_contiguous(texts[row["idx"]], row["source"])]
                    if unverified:
                        raise HTTPException(422, {"code": "quote_unverified", "badRows": unverified})

            # Recompute from owned upload ASR, never a preview/cache/hint claim.
            # Reject before copying media, speaker inference or admission writes.
            # Similarity (even 1.0) is not evidence of a continuous C quote;
            # B deliberately retains its existing uncertain-match policy.
            validate_quotes(await self.matches(data, snapshots))
            try:
                self.manager.ensure_start_capacity(v2=True)
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from None
            files = record.draft_context["files"]
            ids = [f["up_id"] for f in files]
            tokens = {f["up_id"]: f["capability"] for f in files}
            total = sum(s["bytes"] for s in snapshots)
            # Include the upload store's outstanding commitments in this
            # reservation; ordinary legacy starts see it through the same guard.
            requested = total + math.ceil(self.uploads.reserved_bytes / self.settings.task_disk_reservation_multiplier)
            async with self.reserve(requested) as reservation:
                assets, cached = await self.uploads.materialize(ids, tokens, record.task_dir)
                for snapshot, original in zip(snapshots, cached, strict=True):
                    snapshot["audio_offset_seconds"] = original["audio_offset_seconds"]
                await self.analyze_speakers(record, snapshots, assets=assets)
                rows = await self.matches(data, snapshots)
                # Speaker analysis can change which take wins. Revalidate the
                # final selection too, before persisting generation inputs.
                validate_quotes(rows)
                options = [{"note": f["note"], "trim_start": f["in_sec"], "trim_end": f["out_sec"]} for f in data["file_options"]]
                assets = await prepare_media_inputs(record.task_dir, assets, json.dumps(options), None, self.settings)
                self.check(record, draft=True)
                try:
                    self.manager.ensure_start_capacity(v2=True)
                except RuntimeError as exc:
                    raise HTTPException(503, str(exc)) from None
                self.apply_editorial(record, data)
                record.uploads, record.upload_ids = assets, ids
                record.draft_context["creation_inputs"] = self.creation_inputs(record)
                # Retain verified provider evidence, but use the SAME bounded,
                # cross-file annotated snapshot used by preview/start matching.
                cached = [{**original, **snapshot} for original, snapshot in zip(cached, snapshots, strict=True)]
                write_json_atomic(record.task_dir / "pretranscripts.json", cached)
                self.manager._write_upload_manifest(record)
                from .pipeline import _run_blocking_until_complete
                record.draft_context["retry_binding"] = await _run_blocking_until_complete(lambda: _retry_binding(record, self.settings))
                # Snapshot all generation inputs before accepted-start commit.
                self.manager._persist_record(record)
                fingerprint = hashlib.sha256(json.dumps({"script": record.script, "mode": record.mode,
                    "preferences": record.preferences.model_dump(), "sentences": [s.model_dump() for s in record.sentences],
                    "speakers": [s.model_dump() for s in record.speakers], "files": options, "ids": ids},
                    sort_keys=True).encode()).hexdigest()
                self.ledger.accept(record.task_id, record.owner_hash, owner_digest(ip), fingerprint,
                    session_limit=self.settings.effective_session_start_limit,
                    ip_limit=self.settings.anonymous_ip_task_rate_limit_per_hour,
                    global_limit=self.settings.anonymous_global_task_rate_limit_per_hour,
                    legacy_counts=self.legacy_counts(record.owner_hash, ip))
                # No suspension between durable acceptance and queue publication.
                record.status = TaskState.queued
                record.reserved_disk_bytes = reservation.reserved_bytes
                try:
                    self.manager.start_task(record.task_id)
                    reservation.retain()
                except BaseException:
                    record.reserved_disk_bytes = 0
                    record.status = TaskState.failed
                    record.error_kind = "transient"
                    record.error_message = "Accepted start could not be queued; inspect state, do not repeat start"
                    self.manager._persist_record(record)
                    raise HTTPException(409, {"code": "accepted_start_uncertain", "task_id": record.task_id}) from None
                return {"id": record.task_id, "task_id": record.task_id, "status": "queued", "accepted": True,
                    "access_token": task_token if task_token is not None else record.access_token,
                    "queue": self.manager.status_response(record).queue}

    def creation_inputs(self, record: TaskRecord) -> dict:
        """Immutable creation inputs, never revision/report text or edited timings."""
        return {"script": record.script, "mode": record.mode,
                "preferences": record.preferences.model_dump(mode="json"),
                "sentences": [s.model_dump(mode="json") for s in record.sentences],
                "speakers": [s.model_dump(mode="json") for s in record.speakers],
                "quality_gate_mode": record.quality_gate_mode,
                **{k: copy.deepcopy(record.draft_context.get(k, {} if k != "source_voice_preferred" else False))
                   for k in ("type_marks", "speaker_names", "source_voice_preferred")},
                "files": [{"options": {k: f[k] for k in ("note", "in_sec", "out_sec")},
                           "manifest": self.uploads.authorize(f["up_id"], f["capability"]).model_dump(mode="json")}
                          for f in record.draft_context["files"]]}

    def recovery_source(self, record: TaskRecord) -> None:
        self.ledger.check()
        if (self.manager.get(record.task_id) is not record or record.local_only
                or not record.mode_contract or record.rules_version != 2):
            raise HTTPException(409, {"code": "recovery_legacy_unsupported"})
        path = _safe_path(record.task_dir, directory=True)
        if path.parent != self.ledger.data_dir or path.name != record.task_id:
            raise HTTPException(404, "Task not found")
        identity = AdmissionLedger._identity(path)
        if self._identities.setdefault(record.task_id, identity) != identity:
            raise HTTPException(409, "Task directory changed")
        if record.status not in {TaskState.done, TaskState.failed}:
            raise HTTPException(409, {"code": "recovery_requires_terminal_task"})
        if record.lifecycle_v2 and not self.ledger.accepted(record.task_id):
            raise HTTPException(409, {"code": "recovery_requires_accepted_task"})

    async def recover(self, source: TaskRecord, raw: dict) -> dict:
        payload = _validate(DraftRecovery, raw)
        async with self.operation():
            self.recovery_source(source)
            with task_operation(source):
                original = copy.deepcopy(source.draft_context.get("creation_inputs"))
                if original is None:
                    # Pre-snapshot v2 compatibility: no edited revisions, no
                    # reports; only retained inputs and an owned ready manifest.
                    if source.revision != 0 or not source.uploads or not source.draft_context.get("files"):
                        raise HTTPException(409, {"code": "recovery_source_unverified"})
                    original = self.creation_inputs(source)
                files = original.get("files")
                if not isinstance(files, list) or not 1 <= len(files) <= min(20, self.settings.max_files):
                    raise HTTPException(409, {"code": "recovery_source_unverified"})
                if payload.allow_shot_reuse and (source.error_kind != "shortage" or (payload.mode or original["mode"]) == "original"):
                    raise HTTPException(409, {"code": "shot_reuse_not_applicable"})
                release = self.uploads.plan_quota_release(source, source.owner_hash, files)
                if release is None:
                    raise HTTPException(429, {"code": "recovery_quota",
                        "message": f"恢复需要 {len(files)} 个文件名额，当前会话最多保留 {self.uploads.max_files} 个文件、合计 5 GiB，"
                                   "释放这个作品的暂存副本后仍放不下。请先删除不需要的草稿或作品，再回去修改。"})
                for up_id, capability in release:
                    try:
                        await self.uploads.delete(up_id, capability)
                    except (HTTPException, OSError):
                        continue  # a later create() reports any real shortage; nothing was copied yet
                if (sum(r.status == TaskState.draft and r.owner_hash == source.owner_hash for r in self.manager._tasks.values()) >= 20
                        or sum(r.status == TaskState.draft for r in self.manager._tasks.values()) >= 1000):
                    raise HTTPException(429, {"code": "recovery_draft_limit",
                        "message": "未提交的草稿已达上限（每个会话最多 20 份）。请先在“我的作品”里删除不需要的草稿，再回去修改。"})
                key = uuid4().hex
                directory = create_task_dir(self.settings, key)
                record = TaskRecord(key, directory, "", [], mode=payload.mode or original["mode"],
                    mode_contract=True, lifecycle_v2=True, owner_hash=source.owner_hash, status=TaskState.draft,
                    message="恢复草稿", draft_context={"version": 2, "files": [], "expires_at": time.time() + 86400,
                                                      **({"shot_reuse": {"accepted": True}} if payload.allow_shot_reuse else {})})
                imports = []
                try:
                    mapping = {}
                    for evidence in files:
                        receipt = await self.uploads.import_recovery(source, evidence, record.owner_hash)
                        imports.append(receipt)
                        mapping[evidence["manifest"]["id"]] = receipt["upload_id"]
                        record.draft_context["files"].append({"file_id": receipt["upload_id"], "up_id": receipt["upload_id"],
                            "capability": receipt["access_token"], "supplied_hash": True, **evidence["options"]})
                    data = {k: copy.deepcopy(v) for k, v in original.items() if k != "files"}
                    if payload.mode is not None and payload.mode != original["mode"]:
                        data["mode"] = payload.mode
                        data.pop("sentences", None)
                        data["type_marks"] = {}
                    else:
                        for sentence in data["sentences"]:
                            if sentence.get("source_hint"):
                                sentence["source_hint"]["upload_id"] = mapping[sentence["source_hint"]["upload_id"]]
                    self.apply_editorial(record, self.editorial(record, data, starting=False))
                    # Original source clocks; no trim, ASR, quota or queue.
                    ids = [f["up_id"] for f in record.draft_context["files"]]
                    tokens = {f["up_id"]: f["capability"] for f in record.draft_context["files"]}
                    record.uploads, snapshots = await self.uploads.materialize(ids, tokens, directory)
                    record.upload_ids = ids
                    for snapshot, evidence in zip(snapshots, files, strict=True):
                        snapshot["source_sha256"] = evidence["manifest"]["sha256"]
                    write_json_atomic(directory / "pretranscripts.json", snapshots)
                    record.draft_context["recovery_receipt"] = {"source_task_id": source.task_id,
                        "task_id": key, "step": payload.step, "mode_reconfirm": payload.mode is not None and payload.mode != original["mode"],
                        "created_at": record.created_at.isoformat()}
                    self.recovery_source(source)
                    self.manager._persist_record(record)
                    self.manager._tasks[key] = record
                    self._identities[key] = AdmissionLedger._identity(directory)
                    return {**self.view(record), "access_token": record.access_token, "step": payload.step,
                            "recovery": record.draft_context["recovery_receipt"],
                            "files": [{**self.file_view(record, f), "sha256": self.uploads.authorize(f["up_id"], f["capability"]).sha256,
                                       "token": record.access_token, "access_token": record.access_token,
                                       "lastModified": int(record.created_at.timestamp() * 1000) + index}
                                      for index, f in enumerate(record.draft_context["files"])]}
                except BaseException as exc:
                    for receipt in imports:
                        await drain_task(asyncio.create_task(self.uploads.delete(receipt["upload_id"], receipt["access_token"])))
                    self.manager._tasks.pop(key, None)
                    self._identities.pop(key, None)
                    await drain_task(asyncio.create_task(self.manager.discard_unaccepted_upload(key, directory)))
                    if isinstance(exc, (OSError, KeyError, ValueError, TypeError)):
                        raise HTTPException(409, {"code": "recovery_source_unverified"}) from None
                    raise

    async def retry(self, record: TaskRecord, raw: dict) -> dict:
        async with self.operation():
            self.check(record)
            if record.status != TaskState.failed or not self.ledger.accepted(record.task_id):
                raise HTTPException(409, "Only an accepted failed task can RetrySame")
            base = {"expected_revision": record.revision}
            # Strict boolean: Python treats 1 == True, which must never count as consent.
            reuse = bool(raw) and raw.get("allow_shot_reuse") is True and raw == {**base, "allow_shot_reuse": True}
            if raw and raw != base and not reuse:
                raise HTTPException(422, "RetrySame does not accept changed inputs")
            if self.retry_hook is None:
                raise HTTPException(409, {"code": "retry_same_unavailable", "supported": False,
                    "message": "Cache-validated first-incomplete resume is not installed; no task was started or charged"})
            previous = record.draft_context.get("shot_reuse")
            if reuse:
                # An explicit, per-task consent to repeated footage. It is not an input of the
                # script/preferences binding, so cached stages stay valid; only matching reruns.
                if record.error_kind != "shortage" or record.mode == "original":
                    raise HTTPException(409, {"code": "shot_reuse_not_applicable"})
                record.draft_context["shot_reuse"] = {"accepted": True}
            try:
                return await self.retry_hook(record, self)
            except BaseException:
                if reuse:
                    if previous is None:
                        record.draft_context.pop("shot_reuse", None)
                    else:
                        record.draft_context["shot_reuse"] = previous
                raise

    async def delete(self, record: TaskRecord, *, expired: bool = False) -> None:
        async def finish():
            self.ledger.check()
            # Removing source sessions first is safe: accepted tasks own verified
            # copies. Keep task metadata on any upload cleanup failure.
            for item in list(record.draft_context.get("files", [])):
                try:
                    await self.uploads.delete(item["up_id"], item["capability"])
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                record.draft_context["files"].remove(item)
                self.manager._persist_record(record)
            if expired:
                reason = "draft_idle_expiry" if record.status == TaskState.draft else "task_retention_expiry"
                self.ledger.tombstone(record.task_id, record.owner_hash, record.access_token_hash, reason)
            await self.manager.cancel_and_delete(record.task_id)
        async with self.operation():
            # Expiry cleanup must bypass the 410 read guard, not identity checks.
            self.ledger.check()
            if self.manager.get(record.task_id) is not record or not record.lifecycle_v2 or record.local_only:
                raise HTTPException(404, "Task not found")
            path = _safe_path(record.task_dir, directory=True)
            if path.parent != self.ledger.data_dir or AdmissionLedger._identity(path) != self._identities.setdefault(record.task_id, AdmissionLedger._identity(path)):
                raise HTTPException(409, "Task directory changed")
            await drain_task(asyncio.create_task(finish()))

    async def cleanup(self, *, now: float | None = None) -> list[str]:
        stamp = time.time() if now is None else now
        removed = []
        for record in list(self.manager._tasks.values()):
            if not record.lifecycle_v2 or record.local_only:
                continue
            draft_expired = (record.status == TaskState.draft
                             and record.draft_context.get("expires_at", float("inf")) <= stamp
                             and not self.ledger.accepted(record.task_id))
            terminal_expired = (record.status in {TaskState.done, TaskState.failed, TaskState.cancelled}
                                and self.manager._record_retention_reference(record).timestamp() + 72 * 3600 <= stamp)
            if draft_expired or terminal_expired:
                try:
                    self.manager._ensure_media_idle(record)
                    await self.delete(record, expired=True)
                    removed.append(record.task_id)
                except (HTTPException, OSError, RuntimeError):
                    continue
        return removed

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._operations)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await drain_task(task)
            except asyncio.CancelledError:
                if not task.done():
                    raise
            except Exception:
                pass


def create_draft_router(service, authorize) -> APIRouter:
    """Factories are lazy so import/OpenAPI never opens storage or providers."""
    router = APIRouter(prefix="/api/tasks", tags=["drafts-v2"])

    async def access(request, task_id, write=False):
        record = await authorize(request, task_id, write=write)
        service().check(record)
        return record

    @router.get("/{task_id}/draft")
    async def draft(task_id: str, request: Request):
        record = await access(request, task_id)
        return service().view(record)

    @router.post("/{task_id}/recover-draft", status_code=201)
    async def recover_draft(task_id: str, request: Request):
        record = await authorize(request, task_id, write=True)
        tokens = request.headers.getlist("x-task-token")
        if len(tokens) != 1 or not service().manager.token_matches(record, tokens[0]):
            raise HTTPException(404, "Task not found")
        raw = await bounded_json(request)
        # Reauthorize after the body await; never inherit local tokenless writes.
        record = await authorize(request, task_id, write=True)
        if not service().manager.token_matches(record, tokens[0]):
            raise HTTPException(404, "Task not found")
        return await service().recover(record, raw)

    @router.patch("/{task_id}/draft")
    @router.patch("/{task_id}")
    async def patch(task_id: str, request: Request):
        record = await access(request, task_id, True)
        return await service().patch(record, await bounded_json(request))

    @router.get("/{task_id}/files")
    async def files(task_id: str, request: Request):
        record = await access(request, task_id)
        return {"files": service().view(record)["files"]}

    @router.post("/{task_id}/files", status_code=201)
    async def add_file(task_id: str, request: Request):
        record = await access(request, task_id, True)
        return await service().add_file(record, await bounded_json(request), request.headers.get("x-task-token", ""))

    @router.get("/{task_id}/files/{file_id}")
    @router.get("/{task_id}/files/{file_id}/status")
    async def file_status(task_id: str, file_id: str, request: Request):
        record = await access(request, task_id)
        return service().file_view(record, service().item(record, file_id))

    @router.put("/{task_id}/files/{file_id}/chunks/{index}")
    async def chunk(task_id: str, file_id: str, index: str, request: Request):
        if not index.isdigit() or len(index) > 8:
            raise HTTPException(404, "Chunk not found")
        record = await access(request, task_id, True)
        return await service().file_action(record, file_id, "chunk", request=request, index=int(index))

    @router.post("/{task_id}/files/{file_id}/complete", status_code=202)
    async def complete(task_id: str, file_id: str, request: Request):
        record = await access(request, task_id, True)
        return await service().file_action(record, file_id, "complete")

    @router.post("/{task_id}/files/{file_id}/probe")
    async def probe(task_id: str, file_id: str, request: Request):
        await access(request, task_id, True)
        raw = await bounded_json(request)
        record = await access(request, task_id, True)
        return await service().file_action(record, file_id, "probe", raw=raw)

    @router.patch("/{task_id}/files/{file_id}")
    async def file_patch(task_id: str, file_id: str, request: Request):
        record = await access(request, task_id, True)
        return await service().file_action(record, file_id, "patch", raw=await bounded_json(request))

    @router.delete("/{task_id}/files/{file_id}", status_code=204)
    async def file_delete(task_id: str, file_id: str, request: Request):
        record = await access(request, task_id, True)
        await service().file_action(record, file_id, "delete")
        return Response(status_code=204)

    @router.post("/{task_id}/files/{file_id}/pretranscribe")
    async def pretranscribe(task_id: str, file_id: str, request: Request):
        record = await access(request, task_id, True)
        return await service().file_action(record, file_id, "pretranscribe", raw=await bounded_json(request))

    @router.post("/{task_id}/pretranscribe")
    async def task_pretranscribe(task_id: str, request: Request):
        record = await access(request, task_id, True)
        raw = await bounded_json(request)
        file_id = raw.pop("file_id", None)
        if not isinstance(file_id, str):
            raise HTTPException(422, "file_id and retry:true are required")
        return await service().file_action(record, file_id, "pretranscribe", raw=raw)

    @router.get("/{task_id}/files/{file_id}/thumb")
    @router.get("/{task_id}/files/{file_id}/wave")
    async def media(task_id: str, file_id: str, request: Request):
        record = await access(request, task_id)
        item = service().item(record, file_id)
        kind = "thumb" if request.url.path.endswith("/thumb") else "wave"
        data = service().uploads.media(item["up_id"], item["capability"], kind)
        return Response(data, media_type="image/jpeg" if kind == "thumb" else "application/json")

    @router.post("/{task_id}/align")
    async def align(task_id: str, request: Request):
        record = await access(request, task_id, True)
        return await service().align(record, await bounded_json(request))

    @router.post("/{task_id}/start", status_code=202)
    async def start(task_id: str, request: Request):
        record = await access(request, task_id, True)
        ip = request.headers.get("x-forwarded-for") if service().settings.is_production else None
        return await service().start(record, await bounded_json(request), ip or (request.client.host if request.client else "unknown"),
                         task_token=request.headers.get("x-task-token", ""))

    return router