"""No-account restart and legacy-hold safety with real immutable snapshots.

No main singleton, removed router imports, account DB, codecs or providers.
Opaque synthetic media proves lifecycle/integrity, never decoding correctness.
"""
from __future__ import annotations

# These integration tests deliberately exercise the manager's persistence hooks.
# pyright: reportPrivateUsage=false

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend.models import PIPELINE_STAGE_NAMES, StageState, TaskState
from backend.revisions import artifact_files, read_json
from backend.storage import write_json_atomic
from backend.task_operations import INCOMPLETE_COPY_MARKER, create_task_operations_router
from tests.test_task_operations import isolated_settings, synthetic_record


def _import_probe(command: list[str]) -> int:
    # SceneDetect probes FFmpeg during import. Do not launch a media executable
    # just to import TaskManager; all other subprocess attempts remain forbidden.
    if command == ["ffmpeg", "-v", "quiet"]:
        return 0
    raise AssertionError("Unexpected import-time subprocess")


# Python 3.11's Windows platform probe otherwise shells out to `ver` during
# aiohttp import. An empty result makes it use sys.getwindowsversion instead.
with patch("platform._syscmd_ver", return_value=("", "", "")), patch(
    "subprocess.call", side_effect=_import_probe
), patch(
    "subprocess.Popen", side_effect=AssertionError("Subprocesses forbidden")
):
    from backend.task_manager import TaskManager, TaskRecord
    from backend.workbench import create_workbench_router


class PostponedSemaphore(asyncio.Semaphore):
    """Observe actual worker entry without allowing it to acquire a slot."""

    def __init__(self) -> None:
        super().__init__(0)
        self.waiting = asyncio.Event()

    async def acquire(self) -> Literal[True]:
        self.waiting.set()
        return await super().acquire()


class TaskRestartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="golden-mic-restart-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "tasks"
        self.root.mkdir()
        self.guards = ExitStack()
        self.addCleanup(self.guards.close)
        self.guards.enter_context(patch.dict(os.environ, {
            "APP_ENV": "test", "DATA_DIR": str(self.root),
        }, clear=True))
        self.forbidden: list[Mock] = []
        for target in (
            "backend.config.get_settings",
            "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._read_env_file",
            "httpx.HTTPTransport.handle_request",
            "httpx.AsyncHTTPTransport.handle_async_request",
            "socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection",
            "subprocess.Popen", "sqlite3.connect",
            "backend.providers.asr.VolcengineASRProvider.from_settings",
            "backend.providers.embedding.EmbeddingProvider.from_settings",
            "backend.providers.llm.LLMProvider.from_settings",
            "backend.providers.llm.LLMProvider.for_script_segmentation",
            "backend.providers.vision.VisionProvider.from_settings",
            "backend.providers.generative.GenerativeFillProvider.from_settings",
            "backend.providers.tts.EdgeTTSProvider.__init__",
            "backend.providers.tts.OpenAITTSProvider.__init__",
            "backend.providers.tts.VolcengineTTSProvider.__init__",
            "backend.task_manager.run_pipeline",
            "backend.task_manager.run_remix_pipeline",
            "backend.task_manager.run_shot_replacement_pipeline",
            "backend.workbench._edit_workspace",
        ):
            self.forbidden.append(self.guards.enter_context(patch(
                target, side_effect=AssertionError("Offline lifecycle test crossed an isolation boundary")
            )))
        self.settings = isolated_settings(self.root)
        self.manager = TaskManager(self.settings)
        self.records: list[TaskRecord] = []
        self.account = self.root.parent / "classroom.sqlite3"
        self.account.write_bytes(b"legacy account DB must never be opened")
        self.account_before = self.account.read_bytes()

    async def asyncTearDown(self) -> None:
        for record in self.records:
            if record.background is not None and not record.background.done():
                record.background.cancel()
                await asyncio.gather(record.background, return_exceptions=True)
        self.assertEqual(sum(mock.call_count for mock in self.forbidden), 0,
                         "No provider, pipeline, network, dotenv, account DB or subprocess is permitted")
        self.assertEqual(self.account.read_bytes(), self.account_before)
        self.assertEqual(list(self.root.parent.glob("classroom.sqlite3*")), [self.account])

    def _record(self, task_id: str) -> TaskRecord:
        record = synthetic_record(self.manager, task_id)
        record.status = TaskState.queued
        self.manager._persist_record(record)
        self.records.append(record)
        return record

    def _completed(self, task_id: str, revision: int = 0, *, timed: bool = True) -> TaskRecord:
        record = synthetic_record(self.manager, task_id, complete=True, revision=revision)
        self.records.append(record)
        if not timed:
            record.processing_started_at = record.processing_completed_at = None
            record.total_elapsed_seconds = None
            for stage in record.stages:
                stage.started_at = stage.completed_at = None
        self.manager._persist_record(record)
        snapshot = record.task_dir / f"revisions/r{revision}"
        metadata = read_json(snapshot, "revision.json")
        self.assertEqual(set(metadata["files"]), set(artifact_files(snapshot)))
        self.assertTrue({"final.mp4", "report.json", "timings.json", "edl.json", "match_plan.json"}
                        .issubset(metadata["files"]))
        self.assertFalse(record.access_token in json.dumps(metadata), "Snapshot leaked the fixture token")
        return record

    def _hashes(self, root: Path) -> dict[str, str]:
        return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*") if path.is_file() and path.name not in {"task_state.json", "task.log"}}

    def _app(self, manager: TaskManager) -> FastAPI:
        def authorize(request: Request, task_id: str, write: bool = False) -> TaskRecord:
            record = manager.authorize(task_id, request.headers.get("x-task-token"))
            if record is None:
                raise HTTPException(404, "Task unavailable")
            return record
        app = FastAPI()
        app.include_router(create_task_operations_router(self.settings, manager, authorize, check_rate_limit=lambda _: None))
        app.include_router(create_workbench_router(self.settings, manager, authorize))
        return app

    def _client(self, manager: TaskManager, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self._app(manager)),
                                 base_url="http://testserver", trust_env=False,
                                 headers={"x-task-token": token})

    async def _crash(self, record: TaskRecord, gate: PostponedSemaphore) -> None:
        await asyncio.wait_for(gate.waiting.wait(), 2)
        job = record.background
        assert job is not None
        self.assertFalse(job.done(), "Mutation did not actually wait for the semaphore")
        state_path = record.task_dir / "task_state.json"
        queued_bytes = state_path.read_bytes()
        self.assertEqual(json.loads(queued_bytes)["status"], "queued")
        self.assertFalse(record.access_token in queued_bytes.decode(), "Persistence leaked the fixture token")
        # Simulate abrupt process loss, NOT a graceful cancellation that persists
        # cancelled/done. Drain the old coroutine with its shutdown writes disabled;
        # a fresh manager must load the actual, untouched pre-crash disk state.
        with patch.object(self.manager, "_persist_record"), patch("backend.task_manager.write_text_log"):
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            await asyncio.sleep(0)  # drain workbench's cancellation callback
        self.assertTrue(job.cancelled())
        self.assertTrue(state_path.read_bytes() == queued_bytes, "Crash simulation altered durable state")
        self.manager._tasks.clear()
        self.manager = TaskManager(self.settings)
        with patch.object(self.manager, "_run", new_callable=AsyncMock) as initial:
            self.assertGreater(self.manager.restore_tasks(), 0)
        self.assertEqual(initial.call_count, 0, "Restore must not schedule initial generation")

    async def _no_dispatch(self, record: TaskRecord) -> None:
        # There is intentionally no dispatcher to call. Inspect actual worker
        # ownership, then allow ready callbacks to run before checking again.
        with patch.object(self.manager, "_run", new_callable=AsyncMock) as initial:
            await asyncio.sleep(0)
            self.assertEqual(initial.call_count, 0)
        self.assertEqual(record.status, TaskState.failed)
        self.assertIsNone(record.background)
        self.assertEqual(self.manager.operational_snapshot()["active_tasks"], 0)

    async def _pending_mutation(self, operation: str, revision: int = 0, *, timed: bool = True) -> None:
        task_id = f"{operation}-{revision}"
        record = self._completed(task_id, revision, timed=timed)
        token = record.access_token
        before = self._hashes(record.task_dir)
        gate = PostponedSemaphore()
        self.manager._semaphore = gate
        if operation == "remix":
            _, target = self.manager.start_remix(task_id, [0])
            self.assertEqual(target, revision + 1)
            self.assertIsNone(record.processing_started_at)
        elif operation == "replacement":
            _, target = self.manager.start_shot_replacement(task_id, 0, "Use another school shot")
            self.assertEqual(target, revision + 1)
            self.assertIsNone(record.processing_started_at)
        else:
            async with self._client(self.manager, token) as client:
                response = await client.post(f"/api/tasks/{task_id}/workbench/edit", json={
                    "expected_revision": revision, "keep_sentence_ids": [0, 1],
                    "edits": [{"sentence_id": 0, "shot_id": 2}],
                })
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["revision"], revision + 1)
        await self._crash(record, gate)
        restored = self.manager.get(task_id)
        assert restored is not None
        self.assertTrue(self.manager.authorize(task_id, token) is restored, "Original token no longer authorizes")
        self.assertTrue(restored.access_token == "", "Restored record must retain only the token hash")
        self.assertEqual(restored.revision, revision)
        self.assertEqual(self._hashes(record.task_dir), before)
        await self._no_dispatch(restored)
        self.assertEqual(read_json(record.task_dir, "task_state.json")["status"], "failed")
        # The existing initial-only retry API must not turn this failure into a
        # full run, including a successful revision zero.
        async with self._client(self.manager, token) as client:
            retry = await client.post(f"/api/tasks/{task_id}/retry", json={"expected_revision": revision})
            video = await client.get(f"/api/tasks/{task_id}/workbench/versions/{revision}/video")
            report = await client.get(f"/api/tasks/{task_id}/workbench/versions/{revision}/report")
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(video.status_code, 200)
        self.assertEqual(hashlib.sha256(video.content).hexdigest(), before[f"revisions/r{revision}/final.mp4"])
        self.assertEqual(report.status_code, 200)
        self.assertEqual(restored.revision, revision)
        self.assertEqual(self._hashes(record.task_dir), before)
        # Failed stays failed on the next restart, with no hidden retry/dispatch.
        self.manager = TaskManager(self.settings)
        with patch.object(self.manager, "_run", new_callable=AsyncMock) as initial:
            self.manager.restore_tasks()
        self.assertEqual(initial.call_count, 0)
        again = self.manager.get(task_id)
        assert again is not None
        await self._no_dispatch(again)
        self.assertEqual(self._hashes(record.task_dir), before)

    async def test_queued_no_account_remix_revision_zero(self) -> None:
        await self._pending_mutation("remix")

    async def test_queued_no_account_remix_later_revision(self) -> None:
        await self._pending_mutation("remix", 3)

    async def test_queued_no_account_replacement_revision_zero(self) -> None:
        await self._pending_mutation("replacement")

    async def test_queued_no_account_replacement_later_revision(self) -> None:
        await self._pending_mutation("replacement", 3)

    async def test_queued_no_account_workbench(self) -> None:
        await self._pending_mutation("workbench")

    async def test_queued_no_account_workbench_legacy_no_clock(self) -> None:
        await self._pending_mutation("workbench", timed=False)

    async def test_queued_no_account_workbench_later_revision_no_clock(self) -> None:
        await self._pending_mutation("workbench", 3, timed=False)

    async def test_no_clock_mutations_still_fail_closed(self) -> None:
        for operation in ("remix", "replacement", "workbench"):
            with self.subTest(operation=operation):
                await self._pending_mutation(operation, timed=False)

    async def test_legacy_held_uploads_restore_failed_local_only_and_never_start(self) -> None:
        for flags in ({"classroom_task": True, "queue_hold": True},
                      {"classroom_task": True, "queue_hold": False}, {"queue_hold": True}):
            with self.subTest(flags=flags):
                record = self._record(f"legacy-{len(self.records)}")
                state = read_json(record.task_dir, "task_state.json")
                state.update(flags)
                write_json_atomic(record.task_dir / "task_state.json", state)
                original = self._hashes(record.task_dir)
                self.manager = TaskManager(self.settings)
                with patch.object(self.manager, "_run", new_callable=AsyncMock) as initial:
                    self.manager.restore_tasks()
                    await asyncio.sleep(0)
                initial.assert_not_called()
                restored = self.manager.get(record.task_id)
                assert restored is not None
                self.assertTrue(restored.local_only)
                self.assertIsNone(self.manager.authorize(record.task_id, record.access_token))
                self.assertEqual(restored.access_token, "")
                self.assertIn("显式", restored.error_message or "")
                await self._no_dispatch(restored)
                self.assertEqual(self._hashes(record.task_dir), original)
                persisted = read_json(record.task_dir, "task_state.json")
                self.assertTrue(persisted["local_only"])
                self.assertNotIn("queue_hold", persisted)
                self.assertNotIn("classroom_task", persisted)

    async def test_explicit_retry_restart_never_resumes_or_remints_capability(self) -> None:
        record = self._record("initial-retry")
        record.status = TaskState.failed
        self.manager._persist_record(record)
        gate = PostponedSemaphore()
        self.manager._semaphore = gate
        async with self._client(self.manager, record.access_token) as client:
            response = await client.post("/api/tasks/initial-retry/retry", json={"expected_revision": 0})
        self.assertEqual(response.status_code, 202)
        await self._crash(record, gate)
        restored = self.manager.get(record.task_id)
        assert restored is not None
        await self._no_dispatch(restored)
        self.assertFalse(restored.local_only)
        self.assertEqual(restored.access_token, "")
        self.assertTrue(self.manager.authorize(record.task_id, record.access_token) is restored,
                "Restart changed the existing task capability")

    async def test_output_history_or_operation_evidence_is_not_an_initial_upload(self) -> None:
        for kind in ("revision", "final", "report", "snapshot", "remix", "replacement", "stage", "clock"):
            with self.subTest(evidence=kind):
                record = self._record(kind)
                if kind == "revision":
                    record.revision = 1
                elif kind == "final":
                    (record.task_dir / "final.mp4").write_bytes(b"legacy completed output")
                elif kind == "report":
                    write_json_atomic(record.task_dir / "report.json", {"task_id": kind, "rows": []})
                elif kind == "snapshot":
                    # Move a real, valid committed snapshot into an otherwise
                    # fresh-looking record (current media was lost after success).
                    source = self._completed("snapshot-source")
                    shutil.copytree(source.task_dir / "revisions", record.task_dir / "revisions")
                elif kind in {"remix", "replacement"}:
                    record.current_stage = 7 if kind == "remix" else 6
                    record.stage_name = PIPELINE_STAGE_NAMES[record.current_stage - 1]
                elif kind == "stage":
                    record.stages[0].status = StageState.done
                else:
                    record.processing_completed_at = datetime.now(timezone.utc)
                    record.total_elapsed_seconds = 1.0
                self.manager._persist_record(record)
                self.manager = TaskManager(self.settings)
                self.manager.restore_tasks()
                restored = self.manager.get(record.task_id)
                assert restored is not None
                await self._no_dispatch(restored)

    async def test_failed_previous_success_rejects_initial_retry_even_if_current_media_lost(self) -> None:
        for snapshot_only in (False, True):
            with self.subTest(snapshot_only=snapshot_only):
                record = self._completed(f"retry-success-{snapshot_only}")
                record.status = TaskState.failed
                if snapshot_only:
                    for name in artifact_files(record.task_dir):
                        (record.task_dir / name).unlink()
                self.manager._persist_record(record)
                before = self._hashes(record.task_dir)
                async with self._client(self.manager, record.access_token) as client:
                    response = await client.post(f"/api/tasks/{record.task_id}/retry", json={"expected_revision": 0})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(record.status, TaskState.failed)
                self.assertEqual(record.revision, 0)
                self.assertEqual(self._hashes(record.task_dir), before)
                await self._no_dispatch(record)

    async def test_running_initial_and_no_account_queued_jobs_are_not_resumed(self) -> None:
        for status in (TaskState.running, TaskState.queued):
            with self.subTest(status=status):
                record = self._record(f"interrupted-{status.value}")
                record.status = status
                self.manager._persist_record(record)
                self.manager = TaskManager(self.settings)
                self.manager.restore_tasks()
                restored = self.manager.get(record.task_id)
                assert restored is not None
                await self._no_dispatch(restored)

    async def test_completed_task_still_restores_done_with_original_token_and_snapshot(self) -> None:
        record = self._completed("completed", 2)
        token = record.access_token
        before = self._hashes(record.task_dir)
        self.manager = TaskManager(self.settings)
        self.assertEqual(self.manager.restore_tasks(), 1)
        restored = self.manager.get(record.task_id)
        assert restored is not None
        self.assertTrue(self.manager.authorize(record.task_id, token) is restored)
        self.assertTrue(restored.access_token == "", "Restored record must retain only the token hash")
        self.assertEqual(restored.status, TaskState.done)
        self.assertEqual(restored.revision, 2)
        self.assertEqual(self._hashes(record.task_dir), before)
        self.assertEqual(self.manager.operational_snapshot()["active_tasks"], 0)

    async def test_archive_local_only_flag_survives_snapshot_restore_and_ttl(self) -> None:
        record = self._completed("archive", 2)
        state = read_json(record.task_dir, "task_state.json")
        state["classroom_task"] = True
        old = datetime.now(timezone.utc) - timedelta(days=100)
        for name in ("created_at", "updated_at", "processing_completed_at"):
            state[name] = old.isoformat()
        write_json_atomic(record.task_dir / "task_state.json", state)
        before = self._hashes(record.task_dir)
        self.settings.task_ttl_hours = 1
        self.manager = TaskManager(self.settings)
        with patch.object(self.manager, "_run", new_callable=AsyncMock) as initial:
            self.assertEqual(self.manager.restore_tasks(), 1)
            self.assertEqual(await self.manager.cleanup_expired(), [])
        initial.assert_not_called()
        restored = self.manager.get(record.task_id)
        assert restored is not None
        self.assertEqual(restored.status, TaskState.done)
        self.assertTrue(restored.local_only)
        self.assertIsNone(self.manager.authorize(record.task_id, record.access_token))
        self.manager._persist_record(restored)
        self.assertTrue(read_json(record.task_dir, "task_state.json")["local_only"])
        self.assertEqual(self._hashes(record.task_dir), before)
        # Orphan sweep has the same preservation rule after an in-memory removal.
        self.manager._tasks.clear()
        self.assertEqual(await self.manager.cleanup_expired(), [])
        self.assertEqual(self._hashes(record.task_dir), before)

    async def test_incomplete_copy_and_malformed_legacy_records_are_not_ttl_deleted(self) -> None:
        self.settings.task_ttl_hours = 1
        incomplete = self._completed("unpublished")
        write_json_atomic(incomplete.task_dir / INCOMPLETE_COPY_MARKER, {"version": 1})
        malformed = self._record("malformed")
        (malformed.task_dir / "task_state.json").write_bytes(b'{"queue_hold":true,')
        before = self._hashes(self.root)
        self.manager = TaskManager(self.settings)
        with patch("builtins.print"), patch.object(self.manager, "_run", new_callable=AsyncMock) as initial:
            self.assertEqual(self.manager.restore_tasks(), 0)
            self.assertEqual(await self.manager.cleanup_expired(datetime.now(timezone.utc) + timedelta(days=365)), [])
        initial.assert_not_called()
        self.assertEqual(self._hashes(self.root), before)

    async def test_nonlegacy_terminal_expiration_still_deletes_only_expired_task(self) -> None:
        self.settings.task_ttl_hours = 1
        record = self._completed("expired")
        record.processing_completed_at = datetime.now(timezone.utc) - timedelta(hours=2)
        self.manager._persist_record(record)
        retained = self._completed("retained")
        retained.local_only = True
        retained.processing_completed_at = record.processing_completed_at
        self.manager._persist_record(retained)
        before = self._hashes(retained.task_dir)
        self.assertEqual(await self.manager.cleanup_expired(), [record.task_id])
        self.assertFalse(record.task_dir.exists())
        self.assertEqual(self._hashes(retained.task_dir), before)


if __name__ == "__main__":
    unittest.main()