import asyncio
import copy
import hashlib
import json
import math
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from .config import Settings
from .models import (
    PIPELINE_STAGE_NAMES,
    EditingPreferences,
    StageSnapshot,
    StageState,
    TaskState,
    TaskStatusResponse,
    UploadedAsset,
)
from .operations import UploadCapacityGuard
from .production_modes import Mode, SentenceInput, Speaker, MODE_RULES, STAGE_WEIGHTS
from .pipeline import run_pipeline, run_remix_pipeline, run_shot_replacement_pipeline
from .revisions import snapshot_revision
from .remix import validate_remix_selection
from .shot_replacement import load_shot_replacement_context
from .storage import sanitize_sensitive_text, write_json_atomic, write_text_log
from .task_operations import (
    INCOMPLETE_COPY_MARKER, active_task_operations, drain_task, legacy_task_busy, task_operation_busy,
)


class TaskDeletionError(RuntimeError):
    """A failed deletion, distinguished from a pre-admission busy rejection."""

    def __init__(self, message: str, *, committed: bool) -> None:
        super().__init__(message)
        self.committed = committed


@dataclass
class TaskRecord:
    task_id: str
    task_dir: Path
    script: str
    uploads: list[UploadedAsset]
    preferences: EditingPreferences = field(default_factory=EditingPreferences)
    mode: Mode = "voiceover"
    mode_contract: bool = False
    upload_ids: list[str] = field(default_factory=list)
    sentences: list[SentenceInput] = field(default_factory=list)
    speakers: list[Speaker] = field(default_factory=list)
    quality_gate_mode: str | None = None
    error_kind: str | None = None
    bad_rows: list[int] = field(default_factory=list)
    access_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    access_token_hash: str = ""
    status: TaskState = TaskState.queued
    current_stage: int | None = None
    stage_name: str | None = None
    progress: int = 0
    message: str = "排队中"
    error_stage: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    total_elapsed_seconds: float | None = None
    revision: int = 0
    display_title: str | None = None
    metadata_revision: int = 0
    stages: list[StageSnapshot] = field(default_factory=lambda: [
        StageSnapshot(number=index + 1, name=name) for index, name in enumerate(PIPELINE_STAGE_NAMES)
    ])
    background: asyncio.Task[None] | None = None
    processing_started_monotonic: float | None = field(default=None, repr=False, compare=False)
    stage_started_monotonic: float | None = field(default=None, repr=False, compare=False)
    reserved_disk_bytes: int = field(default=0, repr=False, compare=False)
    local_only: bool = False
    lifecycle_v2: bool = False
    owner_hash: str = ""
    draft_context: dict[str, Any] = field(default_factory=dict)
    rules_version: int = 2
    resume_from: int | None = None

    def __post_init__(self) -> None:
        from .task_metadata import validate_title
        if self.display_title is not None:
            if not isinstance(self.display_title, str):
                raise ValueError("Invalid display title")
            validate_title(self.display_title)
        if type(self.metadata_revision) is not int or self.metadata_revision < 0:
            raise ValueError("Invalid metadata revision")
        if type(self.rules_version) is not int or self.rules_version not in (1, 2):
            raise ValueError("Unsupported task rules version")
        if self.resume_from is not None and (type(self.resume_from) is not int or not 1 <= self.resume_from <= 10):
            raise ValueError("Invalid retry stage")
        if not self.access_token_hash and self.access_token:
            self.access_token_hash = _token_hash(self.access_token)
        if self.mode not in MODE_RULES["modes"]:
            raise ValueError("制作模式无效。")
        if self.mode_contract and self.rules_version == 2:
            for stage, definition, weight in zip(self.stages, MODE_RULES["modes"][self.mode]["stages"], STAGE_WEIGHTS[self.mode], strict=True):
                stage.name = definition["name"]
                stage.weight = weight


class TaskManager:
    def __init__(
        self,
        settings: Settings,
        upload_capacity_guard: UploadCapacityGuard | None = None,
    ) -> None:
        self.settings = settings
        self._upload_capacity_guard = upload_capacity_guard
        self._tasks: dict[str, TaskRecord] = {}
        self._deletions: dict[str, asyncio.Task[None]] = {}
        self._semaphore = asyncio.Semaphore(min(settings.max_concurrent_tasks, 1))
        self._draining = False

    def add_task(
        self,
        task_id: str,
        task_dir: Path,
        script: str,
        uploads: list[UploadedAsset],
        *,
        preferences: EditingPreferences | None = None,
        mode: Mode = "voiceover",
        mode_contract: bool = False,
        upload_ids: list[str] | None = None,
        sentences: list[SentenceInput] | None = None,
        speakers: list[Speaker] | None = None,
        quality_gate_mode: str | None = None,
        reserved_disk_bytes: int = 0,
        defer_start: bool = False,
    ) -> TaskRecord:
        self.ensure_start_capacity()
        if task_id in self._tasks or task_id in self._deletions:
            raise RuntimeError("任务标识已被占用，不能覆盖已有任务。")
        if reserved_disk_bytes > 0 and self._upload_capacity_guard is None:
            raise RuntimeError("任务磁盘预留管理器未配置。")
        record = TaskRecord(
            task_id=task_id,
            task_dir=task_dir,
            script=script,
            uploads=uploads,
            preferences=preferences or EditingPreferences(),
            mode=mode,
            mode_contract=mode_contract,
            upload_ids=list(upload_ids or []),
            sentences=list(sentences or []),
            speakers=list(speakers or []),
            quality_gate_mode=quality_gate_mode,
            reserved_disk_bytes=max(0, reserved_disk_bytes),
        )
        self._write_upload_manifest(record)
        self._persist_record(record)
        self._tasks[task_id] = record
        if not defer_start:
            runner = self._run(record)
            try:
                record.background = asyncio.create_task(runner)
            except BaseException:
                runner.close()
                self._tasks.pop(task_id)
                raise
        return record

    async def discard_unaccepted_upload(self, task_id: str, task_dir: Path) -> None:
        """Roll back an exclusively created upload before its capability is returned.

        The middleware still owns (and releases) this upload's disk reservation.
        Cancellation cannot leave a worker recreating files after rollback.
        """
        async def cleanup() -> None:
            pending = self._tasks.get(task_id)
            if pending is not None and pending.task_dir != task_dir:
                raise RuntimeError("任务标识冲突，拒绝清理其他任务。")
            if pending is not None:
                self._tasks.pop(task_id)
                pending.reserved_disk_bytes = 0
                if pending.background is not None:
                    pending.background.cancel()
                    try:
                        await pending.background
                    except (asyncio.CancelledError, Exception):
                        pass
            await asyncio.to_thread(self._remove_task_directory, task_dir)

        await drain_task(asyncio.create_task(cleanup()))

    def start_task(self, task_id: str) -> TaskRecord:
        """Explicitly start an internal deferred upload or an admitted retry."""
        record = self._tasks[task_id]
        self.ensure_start_capacity(exclude_task_id=task_id, v2=record.lifecycle_v2)
        self._ensure_media_idle(record, allow_operation_owner=True)
        if record.status != TaskState.queued or (record.background and not record.background.done()):
            raise RuntimeError("任务不在可启动的待处理状态。")
        record.message = "素材已保存，排队制作中"
        self._persist_record(record)
        runner = self._run(record)
        try:
            record.background = asyncio.create_task(runner)
        except BaseException:
            runner.close()
            raise
        return record

    def restore_tasks(self) -> int:
        data_dir = self.settings.data_dir
        if not data_dir.is_dir():
            return 0
        restored = 0
        for task_dir in sorted(data_dir.iterdir()):
            if not task_dir.is_dir() or task_dir.name in self._tasks or task_dir.name in self._deletions:
                continue
            if (task_dir / INCOMPLETE_COPY_MARKER).exists():
                continue  # An interrupted duplicate has never been published.
            state_path = task_dir / "task_state.json"
            if not state_path.is_file():
                continue
            try:
                record = self._load_record(state_path, task_dir)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                print(f"Skipping invalid task state {task_dir.name}: {sanitize_sensitive_text(exc)}")
                continue
            never_started = (
                record.local_only
                and record.status == TaskState.queued
                and record.revision == 0
                and record.processing_started_at is None
                and record.processing_completed_at is None
                and record.total_elapsed_seconds is None
                and record.current_stage is None
                and record.stage_name is None
                and record.progress == 0
                and all(
                    stage.status == StageState.pending
                    and stage.started_at is None
                    and stage.completed_at is None
                    and stage.elapsed_seconds is None
                    for stage in record.stages
                )
                and not any((task_dir / name).exists() for name in ("final.mp4", "report.json", "revisions"))
            )
            if record.status in {TaskState.queued, TaskState.running}:
                interrupted_at = datetime.now(timezone.utc)
                self._finish_active_stage(record, interrupted_at)
                self._finish_processing(record, interrupted_at)
                record.status = TaskState.failed
                record.error_stage = record.stage_name or "服务重启"
                record.error_message = (
                    "历史上传尚未开始制作；素材已保留，请显式点击重试以启动制作。"
                    if never_started else "服务重启时任务尚未完成；初版可显式重试，已有成片请使用版本恢复或副本。"
                )
                record.message = "等待显式重试，不会自动制作" if never_started else "任务因服务重启而中断"
                if record.current_stage is not None and 1 <= record.current_stage <= len(record.stages):
                    record.stages[record.current_stage - 1].status = StageState.failed
                    record.stages[record.current_stage - 1].message = record.error_message
                record.updated_at = datetime.now(timezone.utc)
                self._persist_record(record)
            elif record.status == TaskState.done and not (
                (task_dir / "final.mp4").is_file() and (task_dir / "report.json").is_file()
            ):
                record.status = TaskState.failed
                record.error_stage = "历史恢复"
                record.error_message = "历史成片或匹配报告缺失。"
                record.message = "历史记录不完整"
                record.updated_at = datetime.now(timezone.utc)
                self._persist_record(record)
            self._tasks[record.task_id] = record
            restored += 1
        return restored

    def enqueue_retry(self, task_id: str, stage: int) -> TaskRecord:
        """Only called after the draft hook verifies immutable inputs/cache bytes."""
        record = self._tasks[task_id]
        if not record.lifecycle_v2 or record.status != TaskState.failed or type(stage) is not int or not 1 <= stage <= 10:
            raise RuntimeError("Invalid cache-validated retry")
        self.ensure_start_capacity(exclude_task_id=task_id, v2=True)
        self._ensure_media_idle(record, allow_operation_owner=True)
        if record.background is not None and not record.background.done():
            raise RuntimeError("Previous worker has not finished cleanup")
        previous = (record.status, record.resume_from, record.message)
        record.status, record.resume_from = TaskState.queued, stage
        try:
            return self.start_task(task_id)
        except BaseException:
            record.status, record.resume_from, record.message = previous
            raise

    def get(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def authorize(self, task_id: str, access_token: str | None) -> TaskRecord | None:
        record = self._tasks.get(task_id)
        if (
            record is None
            or record.local_only
            or not self.token_matches(record, access_token)
        ):
            return None
        return record

    @staticmethod
    def token_matches(record: TaskRecord, access_token: str | None) -> bool:
        return bool(access_token and record.access_token_hash
                    and secrets.compare_digest(record.access_token_hash, _token_hash(access_token)))

    def list_history(self, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        """Credential-free metadata; the host must require direct local authority."""
        from .task_metadata import history_entry
        records = sorted(self._tasks.values(), key=lambda item: (item.updated_at, item.created_at, item.task_id), reverse=True)
        return {
            "tasks": [history_entry(record) for record in records[offset:offset + limit]],
            "total": len(records),
        }

    def start_remix(self, task_id: str, keep_sentence_ids: list[int]) -> tuple[TaskRecord, int]:
        self._ensure_accepting_tasks()
        record = self._tasks.get(task_id)
        if record is None:
            raise KeyError(task_id)
        self._ensure_media_idle(record)
        if record.status != TaskState.done:
            raise RuntimeError("只有已完成且当前未处理的任务可以重剪。")
        ordered_ids = validate_remix_selection(record.task_dir, keep_sentence_ids)
        target_revision = record.revision + 1
        record.status = TaskState.queued
        record.current_stage = 7
        record.stage_name = PIPELINE_STAGE_NAMES[6]
        record.progress = 60
        record.message = f"第 {target_revision} 版重剪排队中"
        record.error_stage = None
        record.error_message = None
        record.processing_started_at = None
        record.processing_completed_at = None
        record.total_elapsed_seconds = None
        record.processing_started_monotonic = None
        record.stage_started_monotonic = None
        record.updated_at = datetime.now(timezone.utc)
        for stage in record.stages:
            stage.started_at = None
            stage.completed_at = None
            stage.elapsed_seconds = None
            if stage.number <= 6:
                stage.status = StageState.done
                stage.message = "复用已有产物（文本重剪不重复执行）"
            else:
                stage.status = StageState.pending
                stage.message = "等待重剪"
        self._persist_record(record)
        record.background = asyncio.create_task(
            self._run_remix(record, ordered_ids, target_revision)
        )
        return record, target_revision

    def start_shot_replacement(
        self,
        task_id: str,
        sentence_id: int,
        instruction: str,
    ) -> tuple[TaskRecord, int]:
        self._ensure_accepting_tasks()
        record = self._tasks.get(task_id)
        if record is None:
            raise KeyError(task_id)
        self._ensure_media_idle(record)
        if record.status != TaskState.done:
            raise RuntimeError("只有已完成且当前未处理的任务可以更换镜头。")
        load_shot_replacement_context(record.task_dir, sentence_id)
        target_revision = record.revision + 1
        record.status = TaskState.queued
        record.current_stage = 6
        record.stage_name = PIPELINE_STAGE_NAMES[5]
        record.progress = 50
        record.message = f"第 {target_revision} 版文字换镜排队中"
        record.error_stage = None
        record.error_message = None
        record.processing_started_at = None
        record.processing_completed_at = None
        record.total_elapsed_seconds = None
        record.processing_started_monotonic = None
        record.stage_started_monotonic = None
        record.updated_at = datetime.now(timezone.utc)
        reused_stages = {1, 2, 3, 4, 5, 7, 8}
        for stage in record.stages:
            stage.started_at = None
            stage.completed_at = None
            stage.elapsed_seconds = None
            if stage.number in reused_stages:
                stage.status = StageState.done
                stage.message = "复用已有产物（文字换镜不重复执行）"
            else:
                stage.status = StageState.pending
                stage.message = "等待文字换镜"
        self._persist_record(record)
        record.background = asyncio.create_task(
            self._run_shot_replacement(
                record,
                sentence_id,
                instruction,
                target_revision,
            )
        )
        return record, target_revision

    def status_response(self, record: TaskRecord) -> TaskStatusResponse:
        from .task_metadata import projection
        now = datetime.now(timezone.utc)
        stages = [
            stage.model_copy(
                update={
                    "elapsed_seconds": _datetime_elapsed(stage.started_at, now)
                    if stage.status == StageState.running and stage.started_at is not None
                    else stage.elapsed_seconds
                }
            )
            for stage in record.stages
        ]
        total_elapsed_seconds = record.total_elapsed_seconds
        if record.processing_started_at is not None and record.processing_completed_at is None:
            total_elapsed_seconds = _datetime_elapsed(record.processing_started_at, now)
        return TaskStatusResponse(
            task_id=record.task_id,
            mode=record.mode,
            current=record.current_stage,
            queue=(list(r.task_id for r in self._tasks.values() if r.status == TaskState.queued).index(record.task_id) + 1) if record.status == TaskState.queued else 0,
            errorKind=record.error_kind,
            badRows=record.bad_rows,
            status=record.status,
            current_stage=record.current_stage,
            stage_name=record.stage_name,
            progress=record.progress,
            message=record.message,
            error_stage=record.error_stage,
            error_message=record.error_message,
            revision=record.revision,
            processing_started_at=record.processing_started_at,
            processing_completed_at=record.processing_completed_at,
            total_elapsed_seconds=total_elapsed_seconds,
            stages=stages,
            **projection(record),
        )

    async def cancel_and_delete(self, task_id: str) -> bool:
        """Cancel/drain the worker and remove task files, never account/ledger data.

        Once admitted, repeated request cancellation cannot abandon cleanup.
        Physical deletion is not atomic: partial failures are reported, and a
        surviving record remains available for an explicit cleanup retry.
        """
        # Admission is checked and the record removed without a suspension point.
        # All new writers authorize against the manager, so none can start after
        # deletion has reserved this task.
        if task_id in self._deletions:
            raise RuntimeError("作品正在删除，请等待清理完成。")
        existing = self._tasks.get(task_id)
        if existing is not None:
            self._ensure_media_idle(existing)
        record = self._tasks.pop(task_id, None)
        if record is None:
            return False
        record.status = TaskState.cancelled
        if record.background and not record.background.done():
            record.background.cancel()
        cleanup = asyncio.create_task(self._delete_reserved(record))
        self._deletions[task_id] = cleanup
        cancelled = False
        try:
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception:
                    break
            if cancelled:
                # The worker, including rollback/restoration, has finished. A
                # second cancellation cannot abandon a still-committing thread.
                if not cleanup.cancelled():
                    cleanup.exception()
                raise asyncio.CancelledError
            cleanup.result()
            return True
        finally:
            if self._deletions.get(task_id) is cleanup:
                del self._deletions[task_id]

    async def _delete_reserved(self, record: TaskRecord) -> None:
        try:
            record.status = TaskState.cancelled
            if record.background is not None:
                # Admission already requested cancellation. A second cancel here
                # interrupts the worker's finally/cleanup awaits, so drain the
                # one cancellation before removing files it may still touch.
                try:
                    await record.background
                except (asyncio.CancelledError, Exception):
                    # A stopped worker may itself have failed during unwinding;
                    # it can no longer write, so deletion must still continue.
                    pass
            record.status = TaskState.cancelled
            try:
                await asyncio.to_thread(self._remove_task_directory, record.task_dir)
            except Exception as exc:
                record.error_stage = "文件清理"
                record.error_message = "任务已停止，但部分文件未能删除，请重试删除。"
                record.message = "删除未完成"
                record.updated_at = datetime.now(timezone.utc)
                if record.task_dir.is_dir():
                    self._tasks.setdefault(record.task_id, record)
                    try:
                        self._persist_record(record)
                    except OSError:
                        pass  # Report the original failure; never claim success.
                raise TaskDeletionError(
                    record.error_message, committed=True,
                ) from exc
        finally:
            # A task cancelled before its coroutine first enters `_run()` never
            # reaches that method's `finally` block. Release here as an
            # idempotent ownership fallback so retained upload capacity cannot
            # leak until the process restarts.
            await self._release_disk_reservation(record)

    @staticmethod
    def _remove_task_directory(task_dir: Path) -> None:
        try:
            shutil.rmtree(task_dir)
        except FileNotFoundError:
            # An already absent root is idempotent success, NOT permission to
            # swallow failures for individual files in a still-present tree.
            try:
                task_dir.lstat()
            except FileNotFoundError:
                return
            raise

    async def cleanup_expired(self, now: datetime | None = None) -> list[str]:
        from .studio import studio_task_busy
        if self.settings.task_ttl_hours <= 0 and not any(record.mode_contract for record in self._tasks.values()):
            return []
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        cutoff = current_time - timedelta(hours=self.settings.task_ttl_hours)
        removed: list[str] = []

        expired_task_ids: list[str] = []
        for task_id, record in list(self._tasks.items()):
            if (record.local_only or record.lifecycle_v2 or task_operation_busy(record.task_dir) or studio_task_busy(record.task_dir)
                    or legacy_task_busy(record.task_dir)):
                continue
            if record.status not in {TaskState.done, TaskState.failed, TaskState.cancelled}:
                continue
            if not record.mode_contract and self.settings.task_ttl_hours <= 0:
                continue
            record_cutoff = current_time - timedelta(hours=72) if record.mode_contract else cutoff
            if self._record_retention_reference(record) <= record_cutoff:
                expired_task_ids.append(task_id)
        for task_id in expired_task_ids:
            record = self._tasks.get(task_id)
            if record is None or record.local_only:
                continue
            record_cutoff = current_time - timedelta(hours=72) if record.mode_contract else cutoff
            if self._record_retention_reference(record) > record_cutoff:
                continue
            if record.status not in {TaskState.done, TaskState.failed, TaskState.cancelled}:
                continue
            try:
                if await self.cancel_and_delete(task_id):
                    removed.append(task_id)
            except RuntimeError:
                continue

        data_dir = self.settings.data_dir
        if data_dir.is_dir() and self.settings.task_ttl_hours > 0:
            for task_dir in list(data_dir.iterdir()):
                if (
                    task_dir.name in {"_uploads", "_v2"}
                    or not task_dir.is_dir()
                    or task_dir.name in self._tasks
                    or task_dir.name in self._deletions
                ):
                    continue
                if (task_operation_busy(task_dir) or studio_task_busy(task_dir)
                        or legacy_task_busy(task_dir)):
                    continue
                retention_reference = self._orphan_retention_reference(task_dir)
                if retention_reference is not None and retention_reference <= cutoff:
                    try:
                        await drain_task(asyncio.create_task(asyncio.to_thread(self._remove_task_directory, task_dir)))
                    except OSError:
                        continue
                    if task_dir.name not in removed:
                        removed.append(task_dir.name)
        return removed

    async def cleanup_loop(self, interval_seconds: float = 3600.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("清理间隔必须大于 0。")
        while True:
            removed = await self.cleanup_expired()
            if removed:
                print(f"TTL cleanup removed {len(removed)} task(s): {', '.join(removed)}")
            await asyncio.sleep(interval_seconds)

    async def shutdown(self) -> None:
        self._draining = True
        from .studio import active_studio_tasks
        backgrounds = [
            record.background
            for record in self._tasks.values()
            if record.background is not None and not record.background.done()
        ]
        backgrounds.extend(active_studio_tasks())
        backgrounds.extend(active_task_operations())
        deletions = set(self._deletions.values())
        backgrounds.extend(deletions)
        if not backgrounds:
            return
        _, pending = await asyncio.wait(
            backgrounds,
            timeout=self.settings.shutdown_grace_seconds,
        )
        for background in pending:
            if background not in deletions:
                background.cancel()
        await asyncio.gather(*backgrounds, return_exceptions=True)

    @property
    def is_draining(self) -> bool:
        return self._draining

    def begin_drain(self) -> dict[str, int | bool]:
        self._draining = True
        return self.operational_snapshot()

    def start_accepting(self) -> None:
        self._draining = False

    def operational_snapshot(self) -> dict[str, int | bool]:
        from .studio import active_studio_tasks
        queued = sum(record.status == TaskState.queued for record in self._tasks.values())
        running = sum(record.status == TaskState.running for record in self._tasks.values())
        studio = len(active_studio_tasks())
        operations = len(active_task_operations())
        deletions = len(self._deletions)
        return {
            "draining": self._draining,
            "queued_tasks": queued,
            "running_tasks": running,
            "active_tasks": queued + running + studio + operations + deletions,
            "studio_jobs": studio,
            "task_operations": operations,
            "deleting_tasks": deletions,
            "known_tasks": len(self._tasks),
        }

    def ensure_start_capacity(self, *, exclude_task_id: str | None = None, v2: bool = False) -> None:
        self._ensure_accepting_tasks()
        if v2:
            waiting = sum(record.status == TaskState.queued for task_id, record in self._tasks.items()
                          if task_id != exclude_task_id)
            if waiting >= self.settings.v2_max_waiting_tasks:
                raise RuntimeError("待处理任务已达到系统上限，请稍后再试。")
            return
        pending = sum(record.status in {TaskState.queued, TaskState.running}
                      for task_id, record in self._tasks.items() if task_id != exclude_task_id)
        if pending >= min(self.settings.max_pending_tasks, 5):
            raise RuntimeError("待处理任务已达到系统上限，请稍后再试。")

    @staticmethod
    def _ensure_media_idle(record: TaskRecord, *, allow_operation_owner: bool = False) -> None:
        from .studio import studio_task_busy
        if (task_operation_busy(record.task_dir, allow_owner=allow_operation_owner)
                or studio_task_busy(record.task_dir) or legacy_task_busy(record.task_dir)):
            raise RuntimeError("作品有正在进行的副本、导出或待核对的历史云任务，请稍后重试。")

    def _ensure_accepting_tasks(self) -> None:
        if self._draining:
            raise RuntimeError("服务正在排空并准备停止，暂不接受新任务。")

    def _begin_processing(self, record: TaskRecord) -> None:
        now = datetime.now(timezone.utc)
        record.processing_started_at = now
        record.processing_completed_at = None
        record.total_elapsed_seconds = None
        record.processing_started_monotonic = time.perf_counter()
        record.stage_started_monotonic = None
        record.updated_at = now
        self._persist_record(record)

    def start_stage(self, record: TaskRecord, stage_number: int, message: str) -> None:
        if record.resume_from is not None and stage_number < record.resume_from:
            return  # Pipeline still verifies cache; do not rewrite completed clocks.
        now = datetime.now(timezone.utc)
        record.status = TaskState.running
        record.current_stage = stage_number
        record.stage_name = record.stages[stage_number - 1].name
        record.message = message
        record.updated_at = now
        for stage in record.stages:
            if stage.number < stage_number:
                stage.status = StageState.done
            elif stage.number == stage_number:
                stage.status = StageState.running
                stage.fraction = 0.0
                stage.message = message
                stage.started_at = now
                stage.completed_at = None
                stage.elapsed_seconds = None
        record.stage_started_monotonic = time.perf_counter()
        record.progress = max(record.progress, int(sum(s.weight for s in record.stages[:stage_number - 1]) * 100))
        self._persist_record(record)

    def update_stage(self, record: TaskRecord, stage_number: int, fraction: float, message: str) -> None:
        if record.resume_from is not None and stage_number < record.resume_from:
            return
        record.current_stage = stage_number
        record.stage_name = record.stages[stage_number - 1].name
        record.message = message
        record.updated_at = datetime.now(timezone.utc)
        fraction = max(0.0, min(1.0, fraction))
        record.stages[stage_number - 1].fraction = fraction
        progress = min(99, int((sum(s.weight for s in record.stages[:stage_number - 1]) + record.stages[stage_number - 1].weight * fraction) * 100))
        record.progress = max(record.progress, progress) if record.resume_from is not None else progress
        record.stages[stage_number - 1].message = message

    def complete_stage(self, record: TaskRecord, stage_number: int, message: str) -> None:
        if record.resume_from is not None and stage_number < record.resume_from:
            return
        now = datetime.now(timezone.utc)
        stage = record.stages[stage_number - 1]
        stage.status = StageState.done
        stage.fraction = 1.0
        stage.message = message
        stage.completed_at = now
        if stage.started_at is not None:
            stage.elapsed_seconds = _measured_elapsed(
                stage.started_at,
                now,
                record.stage_started_monotonic,
            )
        record.stage_started_monotonic = None
        record.message = message
        record.updated_at = now
        if stage_number == len(PIPELINE_STAGE_NAMES):
            record.progress = 100
        self._persist_record(record)

    def _finish_active_stage(self, record: TaskRecord, completed_at: datetime) -> None:
        if record.current_stage is None or not 1 <= record.current_stage <= len(record.stages):
            record.stage_started_monotonic = None
            return
        stage = record.stages[record.current_stage - 1]
        if stage.status == StageState.running and stage.started_at is not None:
            stage.completed_at = completed_at
            stage.elapsed_seconds = _measured_elapsed(
                stage.started_at,
                completed_at,
                record.stage_started_monotonic,
            )
        record.stage_started_monotonic = None

    def _finish_processing(self, record: TaskRecord, completed_at: datetime | None = None) -> None:
        if record.processing_started_at is None:
            record.processing_started_monotonic = None
            return
        finished_at = completed_at or datetime.now(timezone.utc)
        record.processing_completed_at = finished_at
        record.total_elapsed_seconds = _measured_elapsed(
            record.processing_started_at,
            finished_at,
            record.processing_started_monotonic,
        )
        record.processing_started_monotonic = None
        record.updated_at = finished_at

    async def _run(self, record: TaskRecord) -> None:
        try:
            async with self._semaphore:
                if record.status == TaskState.cancelled:
                    return
                self._begin_processing(record)
                record.error_kind = None
                record.bad_rows = []
                record.error_stage = record.error_message = None
                if record.lifecycle_v2 and record.preferences.voice == "mine" and not (record.task_dir / "own_voice.wav").is_file():
                    # V2 records sentence narration on the result screen. Keep
                    # user intent persisted; initial generation is explicitly AI.
                    execution = copy.copy(record)
                    execution.preferences = record.preferences.model_copy(update={"voice": "ai"})
                    manager = self
                    class Reporter:
                        def start_stage(self, _record, *args):
                            manager.start_stage(record, *args)
                        def update_stage(self, _record, *args):
                            manager.update_stage(record, *args)
                        def complete_stage(self, _record, *args):
                            manager.complete_stage(record, *args)
                    await run_pipeline(execution, Reporter(), self.settings)
                else:
                    await run_pipeline(record, self, self.settings)
                record.current_stage = len(PIPELINE_STAGE_NAMES)
                record.stage_name = PIPELINE_STAGE_NAMES[-1]
                record.progress = 100
                record.message = "处理完成"
                self._finish_processing(record)
                await self._snapshot_success(record, "初版")
                record.status = TaskState.done
                record.resume_from = None
                write_text_log(record.task_dir, "任务完成")
                self._persist_record(record)
        except asyncio.CancelledError:
            cancelled_at = datetime.now(timezone.utc)
            self._finish_active_stage(record, cancelled_at)
            self._finish_processing(record, cancelled_at)
            record.status = TaskState.cancelled
            record.message = "任务已取消"
            write_text_log(record.task_dir, "任务取消")
            self._persist_record(record)
            raise
        except Exception as exc:
            failed_at = datetime.now(timezone.utc)
            self._finish_active_stage(record, failed_at)
            self._finish_processing(record, failed_at)
            safe_error = sanitize_sensitive_text(exc)
            record.status = TaskState.failed
            record.error_stage = record.stage_name or "未知阶段"
            record.error_message = safe_error
            record.error_kind = getattr(exc, "error_kind", None) or ("shortage" if "镜头" in safe_error and "不足" in safe_error else "transient")
            record.bad_rows = list(getattr(exc, "bad_rows", []))
            record.message = "处理失败"
            if record.current_stage is not None:
                record.stages[record.current_stage - 1].status = StageState.failed
                record.stages[record.current_stage - 1].message = safe_error
            write_text_log(record.task_dir, f"任务失败：{safe_error}")
            self._persist_record(record)
        finally:
            await self._release_disk_reservation(record)

    async def _release_disk_reservation(self, record: TaskRecord) -> None:
        reserved_bytes = record.reserved_disk_bytes
        record.reserved_disk_bytes = 0
        if reserved_bytes > 0 and self._upload_capacity_guard is not None:
            await drain_task(asyncio.create_task(self._upload_capacity_guard.release_reserved(reserved_bytes)))

    async def _snapshot_success(self, record: TaskRecord, label: str) -> None:
        # Legacy partial fixtures/history remain readable; a real pipeline writes
        # all five artifacts. Never archive credentials or mutable task_state.
        required = ("final.mp4", "report.json", "timings.json", "edl.json", "match_plan.json")
        if all((record.task_dir / name).is_file() for name in required):
            from .pipeline import _run_blocking_until_complete
            from functools import partial
            await _run_blocking_until_complete(partial(snapshot_revision, record, label))

    async def _run_remix(
        self,
        record: TaskRecord,
        keep_sentence_ids: list[int],
        target_revision: int,
    ) -> None:
        try:
            async with self._semaphore:
                if record.status == TaskState.cancelled:
                    return
                await self._snapshot_success(record, "修订前版本")
                self._begin_processing(record)
                write_text_log(
                    record.task_dir,
                    f"M7 重剪开始 revision={target_revision} keep={keep_sentence_ids}",
                )
                await run_remix_pipeline(record, self, keep_sentence_ids, target_revision, self.settings)
                record.revision = target_revision
                record.current_stage = len(PIPELINE_STAGE_NAMES)
                record.stage_name = PIPELINE_STAGE_NAMES[-1]
                record.progress = 100
                record.message = f"第 {target_revision} 版重剪完成"
                self._finish_processing(record)
                await self._snapshot_success(record, "删句重剪")
                record.status = TaskState.done
                self._persist_record(record)
        except asyncio.CancelledError:
            cancelled_at = datetime.now(timezone.utc)
            self._finish_active_stage(record, cancelled_at)
            self._finish_processing(record, cancelled_at)
            record.status = TaskState.cancelled
            record.message = "重剪任务已取消"
            write_text_log(record.task_dir, "M7 重剪取消")
            self._persist_record(record)
            raise
        except Exception as exc:
            failed_at = datetime.now(timezone.utc)
            self._finish_active_stage(record, failed_at)
            self._finish_processing(record, failed_at)
            safe_error = sanitize_sensitive_text(exc)
            record.status = TaskState.failed
            record.error_stage = record.stage_name or "未知阶段"
            record.error_message = safe_error
            record.message = "重剪失败"
            if record.current_stage is not None:
                record.stages[record.current_stage - 1].status = StageState.failed
                record.stages[record.current_stage - 1].message = safe_error
            write_text_log(record.task_dir, f"M7 重剪失败：{safe_error}")
            self._persist_record(record)

    async def _run_shot_replacement(
        self,
        record: TaskRecord,
        sentence_id: int,
        instruction: str,
        target_revision: int,
    ) -> None:
        try:
            async with self._semaphore:
                if record.status == TaskState.cancelled:
                    return
                await self._snapshot_success(record, "修订前版本")
                self._begin_processing(record)
                write_text_log(
                    record.task_dir,
                    f"文字指令换镜开始 revision={target_revision} sentence={sentence_id}",
                )
                await run_shot_replacement_pipeline(
                    record,
                    self,
                    sentence_id,
                    instruction,
                    target_revision,
                    self.settings,
                )
                record.revision = target_revision
                record.current_stage = len(PIPELINE_STAGE_NAMES)
                record.stage_name = PIPELINE_STAGE_NAMES[-1]
                record.progress = 100
                record.message = f"第 {target_revision} 版文字换镜完成"
                self._finish_processing(record)
                await self._snapshot_success(record, "文字换镜")
                record.status = TaskState.done
                self._persist_record(record)
        except asyncio.CancelledError:
            cancelled_at = datetime.now(timezone.utc)
            self._finish_active_stage(record, cancelled_at)
            self._finish_processing(record, cancelled_at)
            record.status = TaskState.cancelled
            record.message = "文字换镜任务已取消"
            write_text_log(record.task_dir, "文字指令换镜取消")
            self._persist_record(record)
            raise
        except Exception as exc:
            failed_at = datetime.now(timezone.utc)
            self._finish_active_stage(record, failed_at)
            self._finish_processing(record, failed_at)
            safe_error = sanitize_sensitive_text(exc)
            record.status = TaskState.failed
            record.error_stage = record.stage_name or "未知阶段"
            record.error_message = safe_error
            record.message = "文字换镜失败"
            if record.current_stage is not None:
                record.stages[record.current_stage - 1].status = StageState.failed
                record.stages[record.current_stage - 1].message = safe_error
            write_text_log(record.task_dir, f"文字指令换镜失败：{safe_error}")
            self._persist_record(record)

    def _write_upload_manifest(self, record: TaskRecord) -> None:
        manifest: dict[str, Any] = {
            "task_id": record.task_id,
            "created_at": record.created_at.isoformat(),
            "script_length": len(record.script),
            "uploads": [
                {
                    "original_name": asset.original_name,
                    "stored_name": asset.stored_name,
                    "size": asset.size,
                    "content_type": asset.content_type,
                }
                for asset in record.uploads
            ],
        }
        write_json_atomic(record.task_dir / "upload_manifest.json", manifest)

    def _persist_record(self, record: TaskRecord) -> None:
        state: dict[str, Any] = {
            "version": 1,
            "task_id": record.task_id,
            "access_token_hash": record.access_token_hash,
            "script": record.script,
            "mode": record.mode,
            "mode_contract": record.mode_contract,
            "rules_version": record.rules_version,
            "resume_from": record.resume_from,
            "upload_ids": record.upload_ids,
            "sentences": [sentence.model_dump(mode="json") for sentence in record.sentences],
            "speakers": [speaker.model_dump(mode="json") for speaker in record.speakers],
            "quality_gate_mode": record.quality_gate_mode,
            "error_kind": record.error_kind,
            "bad_rows": record.bad_rows,
            "status": record.status.value,
            "current_stage": record.current_stage,
            "stage_name": record.stage_name,
            "progress": record.progress,
            "message": record.message,
            "error_stage": record.error_stage,
            "error_message": record.error_message,
            "preferences": record.preferences.model_dump(mode="json"),
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "processing_started_at": record.processing_started_at.isoformat()
            if record.processing_started_at is not None
            else None,
            "processing_completed_at": record.processing_completed_at.isoformat()
            if record.processing_completed_at is not None
            else None,
            "total_elapsed_seconds": record.total_elapsed_seconds,
            "revision": record.revision,
            "local_only": record.local_only,
            "display_title": record.display_title,
            "metadata_revision": record.metadata_revision,
            "lifecycle_v2": record.lifecycle_v2,
            "owner_hash": record.owner_hash,
            "draft_context": record.draft_context,
            "stages": [stage.model_dump(mode="json") for stage in record.stages],
            "uploads": [
                asset.model_dump(mode="json", exclude={"path"})
                for asset in record.uploads
            ],
        }
        write_json_atomic(record.task_dir / "task_state.json", state)

    @staticmethod
    def _load_record(state_path: Path, task_dir: Path) -> TaskRecord:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("不支持的任务状态版本。")
        state = cast(dict[str, Any], payload)
        if state.get("version") != 1:
            raise ValueError("不支持的任务状态版本。")
        task_id = str(state["task_id"])
        if task_id != task_dir.name:
            raise ValueError("任务状态 ID 与目录不一致。")
        token_hash = state.get("access_token_hash") or ""
        if not isinstance(token_hash, str) or (token_hash and (
            len(token_hash) != 64 or any(character not in "0123456789abcdef" for character in token_hash)
        )):
            raise ValueError("任务访问令牌哈希无效。")
        created_at = _parse_datetime(state["created_at"])
        updated_at = _parse_datetime(state["updated_at"])
        uploads_payload = state.get("uploads")
        if not isinstance(uploads_payload, list):
            raise ValueError("任务上传记录无效。")
        upload_items = cast(list[Any], uploads_payload)
        uploads = [
            UploadedAsset(
                original_name=str(item["original_name"]),
                stored_name=str(item["stored_name"]),
                path=task_dir / "raw" / str(item["stored_name"]),
                size=int(item["size"]),
                content_type=str(item["content_type"]),
                upload_id=item.get("upload_id"),
                note=item.get("note", ""),
                trim_start=item.get("trim_start"),
                trim_end=item.get("trim_end"),
                prepared_stored_name=item.get("prepared_stored_name"),
                source_duration_seconds=item.get("source_duration_seconds"),
            )
            for item in upload_items
            if isinstance(item, dict)
        ]
        if len(uploads) != len(upload_items):
            raise ValueError("任务上传记录包含无效项目。")
        stages_payload = state.get("stages")
        if not isinstance(stages_payload, list):
            raise ValueError("任务阶段记录无效。")
        stages = [StageSnapshot.model_validate(item) for item in cast(list[Any], stages_payload)]
        if len(stages) != len(PIPELINE_STAGE_NAMES):
            raise ValueError("任务阶段数量无效。")
        preferences_payload = state.get("preferences")
        if isinstance(preferences_payload, dict):
            # Legacy archives may contain a retired audience selector; do not
            # migrate it into the explicit preference model or write it back.
            preferences_payload = {key: value for key, value in preferences_payload.items() if key != "grade"}
        preferences = (
            EditingPreferences.model_validate(preferences_payload)
            if isinstance(preferences_payload, dict)
            else EditingPreferences()
        )
        return TaskRecord(
            task_id=task_id,
            task_dir=task_dir,
            script=str(state["script"]),
            uploads=uploads,
            preferences=preferences,
            mode=state.get("mode", "voiceover"),
            mode_contract=state.get("mode_contract") is True,
            rules_version=state.get("rules_version", 1),
            resume_from=state.get("resume_from"),
            upload_ids=list(state.get("upload_ids", [])),
            sentences=[SentenceInput.model_validate(s) for s in state.get("sentences", [])],
            speakers=[Speaker.model_validate(s) for s in state.get("speakers", [])],
            quality_gate_mode=state.get("quality_gate_mode"),
            lifecycle_v2=state.get("lifecycle_v2") is True,
            owner_hash=str(state.get("owner_hash", "")),
            draft_context=dict(state.get("draft_context", {})),
            error_kind=state.get("error_kind"),
            bad_rows=list(state.get("bad_rows", [])),
            access_token="",
            access_token_hash=token_hash,
            status=TaskState("queued" if state["status"] == "held" else str(state["status"])),
            current_stage=int(state["current_stage"]) if state.get("current_stage") is not None else None,
            stage_name=str(state["stage_name"]) if state.get("stage_name") is not None else None,
            progress=int(state["progress"]),
            message=str(state["message"]),
            error_stage=str(state["error_stage"]) if state.get("error_stage") is not None else None,
            error_message=str(state["error_message"]) if state.get("error_message") is not None else None,
            created_at=created_at,
            updated_at=updated_at,
            processing_started_at=_parse_optional_datetime(state.get("processing_started_at")),
            processing_completed_at=_parse_optional_datetime(state.get("processing_completed_at")),
            total_elapsed_seconds=_parse_optional_elapsed(state.get("total_elapsed_seconds")),
            revision=int(state["revision"]),
            display_title=state.get("display_title"),
            metadata_revision=state.get("metadata_revision", 0),
            # Read-only compatibility: no token minting, DB lookup or migration.
            local_only=(state.get("local_only") is True or state.get("classroom_task") is True
                        or state.get("queue_hold") is True),
            stages=stages,
        )

    @staticmethod
    def _directory_created_at(task_dir: Path) -> datetime:
        manifest_path = task_dir / "upload_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                created_at = datetime.fromisoformat(str(manifest["created_at"]))
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                return created_at.astimezone(timezone.utc)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                pass
        return datetime.fromtimestamp(task_dir.stat().st_mtime, tz=timezone.utc)

    @staticmethod
    def _record_retention_reference(record: TaskRecord) -> datetime:
        return record.processing_completed_at or record.updated_at or record.created_at

    @staticmethod
    def _orphan_retention_reference(task_dir: Path) -> datetime | None:
        if (task_dir / INCOMPLETE_COPY_MARKER).exists():
            return None
        state_path = task_dir / "task_state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if (state.get("lifecycle_v2") is True or state.get("local_only") is True or state.get("classroom_task") is True
                        or state.get("queue_hold") is True):
                    return None
                status = TaskState(str(state["status"]))
                if status in {TaskState.queued, TaskState.running}:
                    return None
                for key in ("processing_completed_at", "updated_at", "created_at"):
                    value = state.get(key)
                    if value is not None:
                        return _parse_datetime(value)
            except (OSError, KeyError, TypeError, ValueError, AttributeError):
                return None  # Unreadable legacy state cannot be safely classified.
        return TaskManager._directory_created_at(task_dir)


def _token_hash(access_token: str) -> str:
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_optional_datetime(value: object) -> datetime | None:
    return None if value is None else _parse_datetime(value)


def _parse_optional_elapsed(value: object) -> float | None:
    if value is None:
        return None
    elapsed = float(value)
    if not math.isfinite(elapsed) or elapsed < 0.0:
        raise ValueError("任务耗时无效。")
    return elapsed


def _datetime_elapsed(started_at: datetime, completed_at: datetime) -> float:
    return round(max(0.0, (completed_at - started_at).total_seconds()), 3)


def _measured_elapsed(
    started_at: datetime,
    completed_at: datetime,
    monotonic_started_at: float | None,
) -> float:
    if monotonic_started_at is not None:
        return round(max(0.0, time.perf_counter() - monotonic_started_at), 3)
    return _datetime_elapsed(started_at, completed_at)
