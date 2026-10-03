"""Ownerless lifecycle contracts using real persistence and opaque TEMP media.

No main singleton, dotenv, accounts, retired routers, providers or codecs.
The shared fixture is complete for copy/revision validation, NOT decodable media.
"""
from __future__ import annotations

# These tests deliberately exercise admission, persistence and worker ownership.
# pyright: reportPrivateUsage=false

import asyncio
import ctypes
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from contextlib import ExitStack, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend.config import Settings
from backend.models import (
    PIPELINE_STAGE_NAMES, AnnotatedShot, EDLClip, EDLItem, MatchCandidate,
    MatchPlanItem, QualitySummary, ReportResponse, ReportRow, SegmentManifestItem,
    SentenceTiming, StageState, TaskState, UploadedAsset, VisionQuality,
)
from backend.operations import UploadCapacityGuard
from backend import revisions
from backend.revisions import snapshot_revision
from backend.storage import write_json_atomic
from backend import task_operations as operations


def _import_probe(command: list[str]) -> int:
    if command == ["ffmpeg", "-v", "quiet"]:
        return 0  # SceneDetect import probe only; no actual executable is needed.
    raise AssertionError("Unexpected import-time subprocess")


with patch("platform._syscmd_ver", return_value=("", "", "")), patch(
    "subprocess.call", side_effect=_import_probe,
), patch("subprocess.Popen", side_effect=AssertionError("Import subprocess forbidden")):
    from backend.task_manager import TaskDeletionError, TaskManager, TaskRecord


def isolated_settings(root: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test", "data_dir": root,
        "asr_cache_dir": root.parent / "cache/asr", "min_free_disk_gb": 0,
        "shutdown_grace_seconds": 0.1, "max_pending_tasks": 20,
        "max_concurrent_tasks": 1, "task_rate_limit_per_hour": 100,
    }
    values.update(overrides)
    with patch.dict(os.environ, {}, clear=True):
        return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def tree_bytes(root: Path, *, exclude: tuple[str, ...] = ()) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file() and path.name not in exclude}


def synthetic_record(
    manager: TaskManager, task_id: str | None = None, *, complete: bool = False,
    revision: int = 0, local_only: bool = False,
) -> TaskRecord:
    """Real typed metadata/snapshots; every media dependency is an opaque sentinel."""
    task_id = task_id or uuid4().hex
    root = manager.settings.data_dir / task_id
    (root / "raw").mkdir(parents=True)
    source = root / "raw/clip.mp4"
    source.write_bytes(b"synthetic original media; NOT a decodable video")
    record = TaskRecord(
        task_id=task_id, task_dir=root, script="First sentence.\nSecond sentence.",
        uploads=[UploadedAsset(original_name="clip.mp4", stored_name="clip.mp4", path=source,
                               size=source.stat().st_size, content_type="video/mp4")],
        status=TaskState.done if complete else TaskState.failed,
        revision=revision, local_only=local_only,
    )
    if complete:
        for name in (
            "final.mp4", "video_only.mp4", "narration.m4a", "subs.ass", "tts/group.wav",
            "norm/0.mp4", "norm/1.mp4", "norm/2.mp4", "thumbs/shot_0.jpg",
            "thumbs/shot_1.jpg", "thumbs/shot_2.jpg", "tts/0.wav", "tts/1.wav",
            "segments/0.mp4", "segments/1.mp4",
        ):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"opaque synthetic r{revision} artifact: {name}".encode())
        shots = [AnnotatedShot(
            shot_id=i, source_index=0, source_scene_index=i, source_name="clip.mp4",
            norm_path=f"norm/{i}.mp4", thumb_path=f"thumbs/shot_{i}.jpg",
            start=0, end=5, duration=5, status="available", description=f"Synthetic shot {i}",
            quality=VisionQuality(sharp=.9, bright=.9),
        ) for i in range(3)]
        timings = [SentenceTiming(sentence_id=i, text=text, audio_path=f"tts/{i}.wav",
                                  duration=1, start=i, end=i + 1, gap_after=0)
                   for i, text in enumerate(record.script.splitlines())]
        plan = [MatchPlanItem(sentence_id=i, text=timing.text, shot_id=i, confidence=.9,
                              candidates=[MatchCandidate(shot_id=i, similarity=.9)])
                for i, timing in enumerate(timings)]
        edl = [EDLItem(sentence_id=i, timeline_start=i, timeline_end=i + 1,
                       clips=[EDLClip(shot_id=i, src=f"norm/{i}.mp4", in_time=0, out_time=1)])
               for i in range(2)]
        segments = [SegmentManifestItem(sentence_id=i, segments=[f"segments/{i}.mp4"]) for i in range(2)]
        rows = [ReportRow(sentence_id=i, sentence=timing.text, shot_id=i,
                          thumb_url=f"/api/tasks/{task_id}/thumbs/{i}.jpg", description=f"Synthetic shot {i}",
                          duration=1, confidence=.9, is_fallback=False)
                for i, timing in enumerate(timings)]
        for name, items in (
            ("shots_annotated.json", shots), ("timings.json", timings), ("source_timings.json", timings),
            ("match_plan.json", plan), ("edl.json", edl), ("source_edl.json", edl),
            ("segment_manifest.json", segments), ("source_segment_manifest.json", segments),
        ):
            write_json_atomic(root / name, [item.model_dump(mode="json", by_alias=True) for item in items])
        write_json_atomic(root / "report.json", ReportResponse(
            task_id=task_id, rows=rows, quality=QualitySummary(blocking_issue_count=0, warning_count=0),
        ).model_dump(mode="json"))
        write_json_atomic(root / "subtitle_manifest.json", {"schema_version": 1, "fixture": "opaque-only"})
        write_json_atomic(root / "tts_manifest.json", {"continuous_groups": [{"source_audio_path": "tts/group.wav"}]})
        (root / "script.txt").write_text(record.script, encoding="utf-8")
        record.progress = 100
        record.current_stage = len(PIPELINE_STAGE_NAMES)
        record.stage_name = PIPELINE_STAGE_NAMES[-1]
        record.processing_completed_at = datetime.now(timezone.utc)
        record.processing_started_at = record.processing_completed_at - timedelta(seconds=2)
        record.total_elapsed_seconds = 2.0
        for stage in record.stages:
            stage.status = StageState.done
            stage.started_at = record.processing_started_at
            stage.completed_at = record.processing_completed_at
    manager._tasks[task_id] = record
    manager._write_upload_manifest(record)
    manager._persist_record(record)
    if complete:
        snapshot_revision(record, "Complete synthetic contract fixture; no decoding")
    return record


def seed_legacy_ledger(path: Path, rows: list[tuple[str, str, int, str | None]], *, wal: bool = False) -> None:
    """Minimal actual legacy schema; never import the retired CloudStore/router."""
    with closing(sqlite3.connect(path)) as db:
        if wal:
            db.execute("PRAGMA journal_mode=WAL")
        db.execute(f"PRAGMA application_id={0x474D434C}")
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE jobs(task_id TEXT, state TEXT, attempted INTEGER, remote_id TEXT)")
        db.executemany("INSERT INTO jobs VALUES(?,?,?,?)", rows)
        db.commit()


class TaskOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="golden-mic-task-operations-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "tasks"
        self.root.mkdir()
        self.account = self.root.parent / "classroom.sqlite3"
        self.account.write_bytes(b"inert account sentinel; never open or migrate")
        self.account_before = self.account.read_bytes()
        self.settings = isolated_settings(self.root)
        self.capacity = UploadCapacityGuard(self.settings)
        self.manager = TaskManager(self.settings, self.capacity)
        self.guards = ExitStack()
        self.addCleanup(self.guards.close)
        self.forbidden: list[Mock] = []
        for target in (
            "backend.config.get_settings", "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._read_env_file",
            "backend.task_manager.run_pipeline", "backend.task_manager.run_remix_pipeline",
            "backend.task_manager.run_shot_replacement_pipeline", "httpx.HTTPTransport.handle_request",
            "httpx.AsyncHTTPTransport.handle_async_request", "socket.create_connection",
            "socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen", "sqlite3.connect",
        ):
            self.forbidden.append(self.guards.enter_context(patch(
                target, side_effect=AssertionError("Offline operation crossed an isolation boundary"),
            )))
        self.authorizations = 0
        self.denied = False
        self.local_principal = False  # Test-selected authority, never a request header/account.

        async def authorize(request: Request, task_id: str, *, write: bool = False) -> TaskRecord:
            self.authorizations += 1
            record = (self.manager.get(task_id) if self.local_principal else
                      self.manager.authorize(task_id, request.headers.get("x-task-token")))
            if self.denied or record is None:
                raise HTTPException(404, "Task unavailable")
            return record

        self.rate = Mock()
        app = FastAPI()
        self.router = operations.create_task_operations_router(
            self.settings, self.manager, authorize, check_rate_limit=self.rate,
        )
        app.include_router(self.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", trust_env=False)
        self.addAsyncCleanup(self.client.aclose)

    async def asyncTearDown(self) -> None:
        for record in self.manager._tasks.values():
            if record.background is not None and not record.background.done():
                record.background.cancel()
                await asyncio.gather(record.background, return_exceptions=True)
        self.assertFalse(any(Path(key).is_relative_to(self.root) for key in operations._COPYING))
        self.assertEqual((await self.capacity.snapshot()).reserved_bytes, 0, "Owned disk reservation leaked")
        self.assertEqual(self.account.read_bytes(), self.account_before)
        self.assertEqual(list(self.root.parent.glob("classroom.sqlite3*")), [self.account])
        for forbidden in self.forbidden:
            forbidden.assert_not_called()

    async def post(self, record: TaskRecord, operation: str, data: Any = None) -> httpx.Response:
        return await self.client.post(f"/api/tasks/{record.task_id}/{operation}",
                                      headers={"X-Task-Token": record.access_token},
                                      json={"expected_revision": record.revision} if data is None else data)

    async def test_exact_core_endpoint_shapes_and_strict_revision_input(self) -> None:
        paths = {getattr(route, "path", "") for route in self.router.routes}
        self.assertEqual(paths, {"/api/tasks/{task_id}/retry", "/api/tasks/{task_id}/duplicate"})
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(self.root)
        for operation in ("retry", "duplicate"):
            for body in ({}, {"expected_revision": True}, {"expected_revision": "0"},
                         {"expected_revision": 0.0}, {"expected_revision": -1},
                         {"expected_revision": 0, "class_id": "removed"}):
                with self.subTest(operation=operation, field_names=list(body)):
                    response = await self.post(source, operation, body)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["detail"], "Invalid task operation request")
                    self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(tree_bytes(self.root), before)
        self.rate.assert_not_called()

    async def test_token_required_and_oversized_stream_rejected_without_mutation(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(self.root)
        path = f"/api/tasks/{source.task_id}/duplicate"
        for token in (None, "", "invalid"):
            headers = {} if token is None else {"X-Task-Token": token}
            response = await self.client.post(path, json={"expected_revision": 0}, headers=headers)
            self.assertEqual(response.status_code, 404)

        async def chunks():
            for _ in range(5):
                yield b" " * 65536

        response = await self.client.post(path, content=chunks(), headers={
            "X-Task-Token": source.access_token, "Content-Type": "application/json",
        })
        self.assertEqual(response.status_code, 413)
        self.assertEqual(tree_bytes(self.root), before)

    async def test_retry_starts_once_immediately_and_retains_real_disk_lease(self) -> None:
        record = synthetic_record(self.manager)
        record.current_stage, record.stage_name = 3, PIPELINE_STAGE_NAMES[2]
        record.error_message = "synthetic initial failure"
        record.progress = 20
        record.stages[0].status = StageState.done
        record.processing_completed_at = datetime.now(timezone.utc)
        self.manager._persist_record(record)
        original = record.uploads[0].path.read_bytes()
        token_hash = record.access_token_hash
        entered = asyncio.Event()

        async def block(active: TaskRecord) -> None:
            self.assertTrue(active is record, "Retry scheduled a different record")
            entered.set()
            await asyncio.Event().wait()

        with patch.object(self.manager, "_run", side_effect=block) as runner:
            response = await self.post(record, "retry")
            self.assertEqual(response.status_code, 202)
            await asyncio.wait_for(entered.wait(), 3)
            self.assertEqual(response.json(), {"task_id": record.task_id, "status": "queued", "revision": 0})
            self.assertEqual(record.status, TaskState.queued)
            self.assertIsNone(record.error_message)
            self.assertIsNone(record.current_stage)
            self.assertIsNone(record.processing_completed_at)
            self.assertTrue(all(stage.status == StageState.pending for stage in record.stages))
            self.assertEqual(record.progress, 0)
            self.assertTrue(record.access_token_hash == token_hash)
            self.assertGreater(record.reserved_disk_bytes, 0)
            self.assertEqual((await self.capacity.snapshot()).reserved_bytes, record.reserved_disk_bytes)
            self.assertFalse(operations.task_operation_busy(record.task_dir))
            self.assertEqual((await self.post(record, "retry")).status_code, 409)
            self.assertEqual(runner.call_count, 1)
            self.rate.assert_called_once()
            self.assertEqual(record.uploads[0].path.read_bytes(), original)
            self.assertTrue(await self.manager.cancel_and_delete(record.task_id))

    async def test_retry_rejects_completed_history_revision_missing_or_modified_upload(self) -> None:
        for kind in ("done", "later-revision", "final", "report", "revisions", "missing", "size"):
            with self.subTest(kind=kind):
                record = synthetic_record(self.manager)
                if kind == "done":
                    record.status = TaskState.done
                elif kind == "later-revision":
                    record.revision = 1
                elif kind == "revisions":
                    (record.task_dir / kind).mkdir()
                elif kind in {"final", "report"}:
                    (record.task_dir / ("final.mp4" if kind == "final" else "report.json")).write_bytes(b"old success")
                elif kind == "missing":
                    record.uploads[0].path.unlink()
                else:
                    record.uploads[0].path.write_bytes(b"changed")
                before = tree_bytes(record.task_dir)
                self.assertEqual((await self.post(record, "retry")).status_code, 409)
                self.assertEqual(tree_bytes(record.task_dir), before)
                self.assertIsNone(record.background)
        self.rate.assert_not_called()

    async def test_retry_rechecks_identity_after_waiting_for_disk_admission(self) -> None:
        record = synthetic_record(self.manager)
        before = tree_bytes(record.task_dir)
        self.capacity._upload_slots = asyncio.Semaphore(0)
        reached = asyncio.Event()

        # Use the real lease and lock, observing its wait instead of replacing
        # admission. Authorization is revoked while the POST holds its copy lock.
        original_acquire = self.capacity._upload_slots.acquire

        async def acquire() -> bool:
            reached.set()
            return await original_acquire()

        with patch.object(self.capacity._upload_slots, "acquire", side_effect=acquire):
            job = asyncio.create_task(self.post(record, "retry"))
            try:
                await asyncio.wait_for(reached.wait(), 3)
                self.assertTrue(operations.task_operation_busy(record.task_dir))
                self.assertFalse(job.done())
                self.denied = True
            finally:
                self.capacity._upload_slots.release()
            response = await asyncio.wait_for(job, 5)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(tree_bytes(record.task_dir), before)
        self.assertIsNone(record.background)
        self.rate.assert_not_called()

    async def test_insufficient_real_shared_disk_reservation_never_admits_copy_or_retry(self) -> None:
        from backend.operations import InsufficientDiskSpaceError

        for complete, action in ((False, "retry"), (True, "duplicate")):
            record = synthetic_record(self.manager, complete=complete)
            before = tree_bytes(self.root)
            # Actual admission computes required bytes and fails; no reservation
            # mock, target publication, history mutation or pipeline call.
            with patch("backend.operations.shutil.disk_usage", return_value=shutil._ntuple_diskusage(1, 1, 0)):
                with self.assertRaises(InsufficientDiskSpaceError):
                    await self.post(record, action)
            self.assertEqual(tree_bytes(self.root), before)
            self.assertIsNone(record.background)
        self.rate.assert_not_called()

    async def test_retry_stale_capacity_drain_and_rate_denials_do_not_reset_failure(self) -> None:
        record = synthetic_record(self.manager)
        before = tree_bytes(record.task_dir)
        self.assertEqual((await self.post(record, "retry", {"expected_revision": 1})).status_code, 409)
        self.manager.begin_drain()
        self.assertEqual((await self.post(record, "retry")).status_code, 503)
        self.manager.start_accepting()
        with patch.object(self.manager, "ensure_start_capacity", side_effect=RuntimeError("full")):
            self.assertEqual((await self.post(record, "retry")).status_code, 503)
        self.rate.side_effect = HTTPException(429, "synthetic rate limit")
        self.assertEqual((await self.post(record, "retry")).status_code, 429)
        self.assertEqual(tree_bytes(record.task_dir), before)
        self.assertEqual(record.status, TaskState.failed)
        self.assertIsNone(record.background)

    async def test_retry_persist_failure_rolls_back_owned_record_and_reservation(self) -> None:
        record = synthetic_record(self.manager)
        before = tree_bytes(record.task_dir)
        original = self.manager._persist_record
        attempted = False

        def fail_once(active: TaskRecord) -> None:
            nonlocal attempted
            if not attempted:
                attempted = True
                raise OSError("synthetic persistence failure")
            original(active)

        with patch.object(self.manager, "_persist_record", side_effect=fail_once):
            with self.assertRaises(OSError):
                await self.post(record, "retry")
        self.assertTrue(attempted)
        self.assertEqual(record.status, TaskState.failed)
        self.assertIsNone(record.background)
        self.assertEqual(tree_bytes(record.task_dir), before)

    async def test_duplicate_complete_independent_media_and_clean_initial_snapshot(self) -> None:
        source = synthetic_record(self.manager, complete=True, revision=3)
        source.preferences.background_music = True
        for name in ("task.log", "studio/private.json", "provider_cache/private.json"):
            path = source.task_dir / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"not copied")
        report = json.loads((source.task_dir / "report.json").read_bytes())
        report["rows"][0]["thumb_url"] += "?token=removed-capability"
        report.update({"teacher_id": "removed-owner", "consent": {"private": True}, "published": True})
        write_json_atomic(source.task_dir / "report.json", report)
        before = tree_bytes(source.task_dir)
        response = await self.post(source, "duplicate")
        self.assertEqual(response.status_code, 201)
        result = response.json()
        self.assertEqual(set(result), {"task_id", "access_token", "status", "revision"})
        self.assertEqual((result["status"], result["revision"]), ("done", 0))
        self.assertRegex(result["task_id"], r"^[0-9a-f]{32}$")
        self.assertTrue(isinstance(result["access_token"], str) and len(result["access_token"]) >= 32)
        target = self.manager.get(result["task_id"])
        assert target is not None
        self.assertTrue(self.manager.authorize(target.task_id, result["access_token"]) is target,
                "New duplicate capability did not authorize its own record")
        self.assertTrue(target.access_token_hash != source.access_token_hash)
        self.assertIsNone(self.manager.authorize(target.task_id, source.access_token))
        self.assertFalse(target.local_only)
        self.assertIsNone(target.background)
        self.assertEqual(target.script, source.script)
        self.assertTrue(target.preferences.background_music)
        self.assertFalse((target.task_dir / operations.INCOMPLETE_COPY_MARKER).exists())
        self.assertFalse((target.task_dir / "studio").exists())
        self.assertFalse((target.task_dir / "task.log").exists())
        self.assertTrue((target.task_dir / "tts/group.wav").is_file())
        for name in ("report.json", "revisions/r0/report.json"):
            data = (target.task_dir / name).read_text(encoding="utf-8")
            self.assertNotIn("removed-owner", data)
            self.assertNotIn("removed-capability", data)
            self.assertNotIn('"published"', data)
            self.assertEqual(json.loads(data)["task_id"], target.task_id)
            self.assertTrue(all(row["thumb_url"].startswith(f"/api/tasks/{target.task_id}/")
                                for row in json.loads(data)["rows"]))
        for name in ("final.mp4", "raw/clip.mp4", "norm/0.mp4", "tts/0.wav"):
            self.assertEqual((target.task_dir / name).read_bytes(), before[name])
            self.assertNotEqual((target.task_dir / name).stat().st_ino, (source.task_dir / name).stat().st_ino)
        target.uploads[0].path.write_bytes(b"independent clone")
        self.assertEqual(tree_bytes(source.task_dir), before)
        archive = tree_bytes(target.task_dir / "revisions")
        self.assertTrue(all(result["access_token"].encode() not in data for data in archive.values()))
        self.rate.assert_not_called()  # Duplicating local media never starts paid work.

    async def test_local_only_duplicate_stays_private_with_valid_capability_after_restore(self) -> None:
        source = synthetic_record(self.manager, complete=True, revision=3, local_only=True)
        before = tree_bytes(source.task_dir)
        source_fields = vars(source).copy()
        source_upload = source.uploads[0].model_dump()
        self.assertTrue(self.manager.token_matches(source, source.access_token))
        self.assertEqual((await self.post(source, "duplicate")).status_code, 404)
        self.assertEqual(list(self.manager._tasks), [source.task_id])

        # Local authority grants the source operation, not remote token access.
        self.local_principal = True
        try:
            response = await self.post(source, "duplicate")
        finally:
            self.local_principal = False
        self.assertEqual(response.status_code, 201)
        result = response.json()
        token = result["access_token"]
        self.assertTrue(isinstance(token, str) and len(token) >= 32, "Missing duplicate capability")
        self.assertEqual((result["status"], result["revision"]), ("done", 0))
        target = self.manager.get(result["task_id"])
        assert target is not None
        self.assertNotEqual(target.task_id, source.task_id)
        self.assertTrue(target.access_token_hash != source.access_token_hash, "Source capability reused")
        persisted = json.loads((target.task_dir / "task_state.json").read_bytes())
        self.assertIs(persisted["local_only"], True)
        self.assertTrue(persisted["access_token_hash"] == target.access_token_hash)
        self.assertTrue("access_token" not in persisted, "Plain capability field persisted")

        restored_manager = TaskManager(self.settings)
        self.assertEqual(restored_manager.restore_tasks(), 2)
        restored = restored_manager.get(target.task_id)
        assert restored is not None
        self.assertTrue(restored.access_token == "", "Restore retained a plaintext capability")
        for manager, record in ((self.manager, target), (restored_manager, restored)):
            with self.subTest(restored=manager is restored_manager):
                self.assertIs(record.local_only, True)
                self.assertEqual((record.status, record.revision), (TaskState.done, 0))
                self.assertIsNone(record.background)
                self.assertTrue(manager.token_matches(record, token), "Returned capability is not valid")
                # Boolean assertions cannot echo TaskRecord's token on failure.
                self.assertTrue(manager.authorize(record.task_id, token) is None,
                                "Valid capability exposed a local-only duplicate remotely")
                self.assertTrue(manager.authorize(source.task_id, source.access_token) is None,
                                "Original local-only task became remotely accessible")
                self.assertEqual(len(record.uploads), 1)
                self.assertEqual(record.task_dir, self.root / target.task_id)
                self.assertEqual(record.uploads[0].path, record.task_dir / "raw/clip.mp4")
                self.assertEqual(record.uploads[0].model_dump(exclude={"path"}),
                                 source.uploads[0].model_dump(exclude={"path"}))
                self.assertFalse(record.uploads[0].path.samefile(source.uploads[0].path))
        copied, source_after = tree_bytes(target.task_dir), tree_bytes(source.task_dir)
        self.assertTrue(all(value.encode() not in data for value in (token, source.access_token)
                            for artifacts in (copied, source_after) for data in artifacts.values()),
                        "Plain capability copied to an artifact or log")
        for name in ("raw/clip.mp4", "norm/0.mp4", "tts/0.wav", "tts/group.wav", "final.mp4"):
            self.assertEqual((target.task_dir / name).read_bytes(), before[name])
        for name in ("report.json", "revisions/r0/report.json"):
            report = json.loads((target.task_dir / name).read_bytes())
            self.assertEqual(report["task_id"], target.task_id)
            self.assertEqual([row["thumb_url"] for row in report["rows"]],
                             [f"/api/tasks/{target.task_id}/thumbs/{index}.jpg" for index in range(2)])
        self.assertTrue(vars(source) == source_fields, "Source record mutated during duplication")
        self.assertEqual(source.uploads[0].model_dump(), source_upload)
        self.assertEqual(source_after, before)
        self.rate.assert_not_called()

    async def test_copy_ignores_transcript_and_diagnostic_keys_but_keeps_known_dependencies(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        transcript = [{"id": "upload-a", "sec": 5., "status": "ready", "transcript": {
            "segments": [{"id": "segment-a", "start": 0., "end": 1., "speaker_id": "speaker-a",
                          "text": "hello", "words": [{"w": "hello", "s": 0., "e": 1.}]}]},
            "asr_result": {"text": "hello", "duration_ms": 5000,
                "utterances": [{"text": "hello", "start_time_ms": 0, "end_time_ms": 1000,
                                "words": [{"text": "hello", "start_time_ms": 0, "end_time_ms": 1000}]}]}}]
        write_json_atomic(source.task_dir / "pretranscripts.json", transcript)
        # An arbitrary path-looking key in linguistic/diagnostic evidence must
        # not cause copying provider caches or treating ASR objects as paths.
        write_json_atomic(source.task_dir / "quality_report.json", {
            "blocking_issue_count": 0, "warning_count": 0, "issues": [],
            "metrics": {"segments": [{"text": "not media"}], "audio_path": "provider_cache/private.wav"},
        })
        private = source.task_dir / "provider_cache/private.wav"
        private.parent.mkdir()
        private.write_bytes(b"private cache sentinel")
        before = tree_bytes(source.task_dir)
        response = await self.post(source, "duplicate")
        self.assertEqual(response.status_code, 201, response.text)
        target = self.manager.get(response.json()["task_id"])
        assert target is not None
        self.assertEqual(json.loads((target.task_dir / "pretranscripts.json").read_bytes()), transcript)
        self.assertFalse((target.task_dir / "provider_cache").exists())
        self.assertEqual((target.task_dir / "tts/group.wav").read_bytes(), before["tts/group.wav"])
        self.assertEqual(tree_bytes(source.task_dir), before)
        self.rate.assert_not_called()

    def test_dependency_collection_is_artifact_and_field_scoped(self) -> None:
        self.assertEqual(operations._dependencies("pretranscripts.json", {
            "transcript": {"segments": [{"text": "hello", "words": []}]},
            "audio_path": "never-follow-this.wav",
        }), set())
        self.assertEqual(operations._dependencies("production_mode.json", {
            "source_clocks": {"upload": {"raw_path": "raw/clip.mp4", "norm_path": "norm/0.mp4"}},
            "audio_cache": {"0": {"raw_timing": {"audio_path": "tts/original.wav"},
                                   "raw_recipe": {"source_audio": "own_voice.wav"}}},
            "diagnostic": {"segments": [{"text": "not a path"}], "src": "private.mp4"},
        }), {"raw/clip.mp4", "norm/0.mp4", "tts/original.wav", "own_voice.wav"})
        for payload in ([{"segments": [{"text": "not media"}]}], [{"segments": [42]}]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                operations._dependencies("segment_manifest.json", payload)
        self.assertEqual(operations._dependencies("segment_manifest.json", [{"segments": ["segments/0.mp4"]}]),
                         {"segments/0.mp4"})

    async def test_duplicate_stale_failed_and_draining_leave_no_target(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(self.root)
        self.assertEqual((await self.post(source, "duplicate", {"expected_revision": 1})).status_code, 409)
        source.status = TaskState.failed
        self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
        source.status = TaskState.done
        self.manager.begin_drain()
        self.assertEqual((await self.post(source, "duplicate")).status_code, 503)
        self.assertEqual(tree_bytes(self.root), before)

    async def test_duplicate_missing_allowlisted_dependencies_fail_cleanly(self) -> None:
        for missing in ("norm/0.mp4", "thumbs/shot_1.jpg", "tts/group.wav", "raw/clip.mp4",
                        "segments/0.mp4", "subtitle_manifest.json", "narration.m4a"):
            with self.subTest(missing=missing):
                source = synthetic_record(self.manager, complete=True)
                (source.task_dir / missing).unlink()
                before = tree_bytes(self.root)
                roots = set(self.root.iterdir())
                self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
                self.assertEqual(tree_bytes(self.root), before)
                self.assertEqual(set(self.root.iterdir()), roots)

    def test_nested_snapshot_exceeds_max_path_without_shortening_task_identity(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        # Reproduce the real task / .workbench-UUID / revisions /
        # .snapshot-UUID / revision.json.UUID.tmp expansion on Windows.
        workspace = source.task_dir / (".workbench-" + "b" * 32)
        native = revisions._snapshot_io_path(workspace)
        revisions.copy_files(source.task_dir, native, revisions.artifact_files(source.task_dir))
        work = SimpleNamespace(**{**vars(source), "task_dir": workspace, "revision": 1})
        before = tree_bytes(source.task_dir / "revisions")
        paths: list[Path] = []
        original_write = revisions.write_json_atomic

        def observe(path: Path, value: Any) -> None:
            paths.append(path)
            original_write(path, value)

        with patch.object(revisions, "write_json_atomic", side_effect=observe):
            metadata = snapshot_revision(work)
        self.assertEqual(len(paths), 1)
        # Strip the Win32 namespace prefix only when measuring the lexical path.
        lexical = str(paths[0]).removeprefix("\\\\?\\")
        self.assertGreater(len(lexical + "." + "a" * 32 + ".tmp"), 260)
        target = native / "revisions/r1"
        self.assertEqual(revisions.read_json(target, "revision.json"), metadata)
        self.assertEqual(metadata["revision"], 1)
        for name in metadata["files"]:
            self.assertEqual((target / name).read_bytes(), (source.task_dir / name).read_bytes())
        self.assertEqual(tree_bytes(source.task_dir / "revisions"), before)
        self.assertFalse(list((native / "revisions").glob(".snapshot-*")))
        self.assertEqual(snapshot_revision(work), metadata)

    def test_snapshot_native_delete_sharing_denial_is_not_retried_or_published(self) -> None:
        if os.name != "nt":
            self.skipTest("Win32 directory delete-sharing contract")
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(source.task_dir)
        source.revision = 1
        # A scanner/reader may hold a child file without FILE_SHARE_DELETE.
        # Hold a real read handle at commit; do not simulate the OS failure by
        # mocking rename itself. An access=0 directory handle does NOT block it.
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
        original_write = revisions.write_json_atomic
        attempts: list[Path] = []

        def hold_commit(path: Path, value: Any) -> None:
            original_write(path, value)
            handle = create(str(path), 0x80000000, 3, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            # Close before snapshot's finally cleanup, but only AFTER the real
            # rename has failed. This makes the native failure deterministic.
            original_rename = Path.rename

            def rename(directory: Path, target: Path) -> Path:
                attempts.append(directory)
                try:
                    return original_rename(directory, target)
                finally:
                    self.assertTrue(close(handle))

            self.enterContext(patch.object(Path, "rename", new=rename))

        with patch.object(revisions, "write_json_atomic", side_effect=hold_commit):
            with self.assertRaises(PermissionError) as caught:
                snapshot_revision(source)
        self.assertEqual(caught.exception.winerror, 5)
        self.assertEqual(len(attempts), 1)
        self.assertFalse((source.task_dir / "revisions/r1").exists())
        self.assertEqual(tree_bytes(source.task_dir), before)
        self.assertFalse(list((source.task_dir / "revisions").glob(".snapshot-*")))

    async def test_duplicate_unsafe_paths_cannot_escape_task(self) -> None:
        for unsafe in ("../outside.mp4", "C:/private.mp4", "https://remote/media.mp4", "norm/../final.mp4"):
            with self.subTest(path_kind=unsafe.split(":", 1)[0]):
                source = synthetic_record(self.manager, complete=True)
                document = json.loads((source.task_dir / "edl.json").read_bytes())
                document[0]["clips"][0]["src"] = unsafe
                write_json_atomic(source.task_dir / "edl.json", document)
                before = tree_bytes(self.root)
                self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
                self.assertEqual(tree_bytes(self.root), before)

    async def test_copy_byte_cap_and_persistence_failure_remove_incomplete_target(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(self.root)
        with patch.object(operations, "MAX_SNAPSHOT_BYTES", 1):
            self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
        with patch.object(self.manager, "_persist_record", side_effect=OSError("synthetic storage failure")):
            self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
        self.assertEqual(tree_bytes(self.root), before)
        self.assertEqual(list(self.manager._tasks), [source.task_id])

    async def test_duplicate_reauthorizes_after_copy_and_rolls_back_on_revocation(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(self.root)
        original = operations._drain_copy

        async def revoke(*args: Any, **kwargs: Any) -> None:
            await original(*args, **kwargs)
            self.denied = True

        with patch.object(operations, "_drain_copy", side_effect=revoke):
            self.assertEqual((await self.post(source, "duplicate")).status_code, 404)
        self.assertGreaterEqual(self.authorizations, 3)
        self.assertEqual(tree_bytes(self.root), before)
        self.assertEqual(list(self.manager._tasks), [source.task_id])

    async def test_duplicate_rechecks_revision_after_copy(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(self.root)
        original = operations._drain_copy

        async def stale(*args: Any, **kwargs: Any) -> None:
            await original(*args, **kwargs)
            source.revision += 1

        with patch.object(operations, "_drain_copy", side_effect=stale):
            self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
        self.assertEqual(tree_bytes(self.root), before)

    async def test_duplicate_rechecks_source_file_identity_after_copy(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        original = operations.copy_files
        roots = set(self.root.iterdir())

        def changed(source_root: Path, target_root: Path, names: list[str]) -> None:
            original(source_root, target_root, names)
            (source_root / "norm/0.mp4").write_bytes(b"synthetic concurrent source change")

        with patch.object(operations, "copy_files", side_effect=changed):
            self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
        self.assertEqual(set(self.root.iterdir()), roots)
        self.assertEqual(list(self.manager._tasks), [source.task_id])

    async def test_copy_repeated_cancellation_holds_lock_until_worker_drains(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        before = tree_bytes(self.root)
        entered, release = threading.Event(), threading.Event()
        original = operations._copy_completed

        def block(*args: Any, **kwargs: Any) -> None:
            entered.set()
            if not release.wait(8):
                raise AssertionError("Synthetic copy worker was not released")
            original(*args, **kwargs)

        with patch.object(operations, "_copy_completed", side_effect=block):
            job = asyncio.create_task(self.post(source, "duplicate"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.assertTrue(operations.task_operation_busy(source.task_dir))
                self.assertGreater((await self.capacity.snapshot()).reserved_bytes, 0)
                self.assertEqual((await self.post(source, "duplicate")).status_code, 409)
                with self.assertRaises(RuntimeError):
                    await self.manager.cancel_and_delete(source.task_id)
                job.cancel()
                await asyncio.sleep(0)
                job.cancel()
                await asyncio.sleep(0)
                self.assertFalse(job.done())
                self.assertTrue(operations.task_operation_busy(source.task_dir))
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(job, 8)
        self.assertEqual(tree_bytes(self.root), before)
        self.assertEqual(list(self.manager._tasks), [source.task_id])

    async def test_operation_owner_is_exclusive_and_visible_to_shutdown(self) -> None:
        source = synthetic_record(self.manager)
        with operations._operation(source):
            self.assertTrue(operations.task_operation_busy(source.task_dir))
            self.assertFalse(operations.task_operation_busy(source.task_dir, allow_owner=True))
            self.assertIn(asyncio.current_task(), operations.active_task_operations())

            async def other() -> None:
                self.assertTrue(operations.task_operation_busy(source.task_dir, allow_owner=True))
                with self.assertRaises(HTTPException) as caught:
                    with operations._operation(source):
                        self.fail("Another task acquired the copy lock")
                self.assertEqual(caught.exception.status_code, 409)

            await asyncio.create_task(other())
        self.assertFalse(operations.task_operation_busy(source.task_dir))

    async def test_immediate_delete_cancels_unentered_runner_and_releases_disk_once(self) -> None:
        source = synthetic_record(self.manager)
        self.manager._tasks.pop(source.task_id)
        with patch.object(self.capacity, "release_reserved", wraps=self.capacity.release_reserved) as released:
            async with self.capacity.reserve(128) as lease:
                record = self.manager.add_task(source.task_id, source.task_dir, source.script, source.uploads,
                                               reserved_disk_bytes=lease.reserved_bytes)
                lease.retain()
            self.assertTrue(await self.manager.cancel_and_delete(record.task_id))
            self.assertEqual(released.await_count, 1)
            self.assertEqual(record.reserved_disk_bytes, 0)
        self.assertFalse(source.task_dir.exists())
        self.assertIsNone(self.manager.get(source.task_id))
        self.assertFalse(await self.manager.cancel_and_delete(source.task_id))

    async def test_delete_drains_cancelled_worker_and_disconnected_request(self) -> None:
        source = synthetic_record(self.manager)
        entered, unwinding, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def worker() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                unwinding.set()
                await finish.wait()
                (source.task_dir / "last-worker-write").write_bytes(b"must finish before removal")
                raise

        source.background = asyncio.create_task(worker())
        await asyncio.wait_for(entered.wait(), 3)
        deletion = asyncio.create_task(self.manager.cancel_and_delete(source.task_id))
        try:
            await asyncio.wait_for(unwinding.wait(), 3)
            self.assertIsNone(self.manager.get(source.task_id))
            self.assertEqual(self.manager.operational_snapshot()["deleting_tasks"], 1)
            with self.assertRaises(RuntimeError):
                await self.manager.cancel_and_delete(source.task_id)
            deletion.cancel()
            await asyncio.sleep(0)
            deletion.cancel()
            await asyncio.sleep(0)
            self.assertFalse(deletion.done(),
                             f"Deletion escaped blocked worker cleanup; cancel requests={source.background.cancelling()}")
            self.assertTrue(source.task_dir.exists())
        finally:
            finish.set()
            outcome, = await asyncio.wait_for(asyncio.gather(deletion, return_exceptions=True), 5)
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertFalse(source.task_dir.exists())
        self.assertFalse(self.manager._deletions)

    async def test_delete_partial_disk_failure_is_truthful_and_explicitly_retryable(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        async with self.capacity.reserve(128) as lease:
            source.reserved_disk_bytes = lease.reserved_bytes
            lease.retain()

        def fail(root: Path) -> None:
            (root / "final.mp4").unlink()
            raise OSError("private path diagnostic must not appear in the public error")

        with patch.object(self.manager, "_remove_task_directory", side_effect=fail):
            with self.assertRaises(TaskDeletionError) as caught:
                await self.manager.cancel_and_delete(source.task_id)
        self.assertTrue(caught.exception.committed)
        self.assertNotIn("private path", str(caught.exception))
        self.assertTrue(self.manager.get(source.task_id) is source, "Failed deletion did not retain its own record")
        self.assertEqual(source.status, TaskState.cancelled)
        self.assertEqual(json.loads((source.task_dir / "task_state.json").read_bytes())["status"], "cancelled")
        self.assertEqual((await self.capacity.snapshot()).reserved_bytes, 0)
        self.assertTrue(await self.manager.cancel_and_delete(source.task_id))
        self.assertFalse(source.task_dir.exists())

    async def test_duplicate_cleanup_failure_keeps_unpublished_marker_and_no_capability(self) -> None:
        source = synthetic_record(self.manager, complete=True)
        with patch.object(operations, "_drain_copy", new=AsyncMock(side_effect=OSError("copy failed"))), patch.object(
            self.manager, "_remove_task_directory", side_effect=OSError("cleanup failed"),
        ):
            response = await self.post(source, "duplicate")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "duplicate_cleanup_failed")
        self.assertNotIn("access_token", response.json())
        incomplete = [path for path in self.root.iterdir() if path != source.task_dir]
        self.assertEqual(len(incomplete), 1)
        self.assertTrue((incomplete[0] / operations.INCOMPLETE_COPY_MARKER).is_file())
        restored = TaskManager(self.settings)
        self.assertEqual(restored.restore_tasks(), 1)
        self.assertIsNone(restored.get(incomplete[0].name))
        self.assertEqual(list(self.manager._tasks), [source.task_id])


class LegacyLedgerReadOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="golden-mic-ledger-readonly-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.task = self.root / "tasks/task"
        self.task.mkdir(parents=True)
        self.ledger = self.root / "cloud_jobs.sqlite3"

    def unchanged_check(self, expected: bool) -> None:
        before = tree_bytes(self.root)
        stat = self.ledger.stat() if self.ledger.exists() else None
        self.assertIs(operations.legacy_task_busy(self.task), expected)
        self.assertEqual(tree_bytes(self.root), before)
        if stat is not None:
            after = self.ledger.stat()
            self.assertEqual((stat.st_ino, stat.st_size, stat.st_mtime_ns),
                             (after.st_ino, after.st_size, after.st_mtime_ns))

    def test_absent_ledger_never_opens_or_creates_database(self) -> None:
        with patch("sqlite3.connect", side_effect=AssertionError("No ledger exists")) as connect:
            self.unchanged_check(False)
            connect.assert_not_called()

    def test_terminal_and_unattempted_interrupted_are_idle_without_rewriting_billing(self) -> None:
        rows = [("task", state, attempted, remote) for state, attempted, remote in (
            ("succeeded", 1, "finished"), ("failed", 1, "failed-remote"),
            ("cancelled", 1, "cancelled-remote"), ("interrupted", 0, None),
        )]
        rows.append(("another-task", "unknown", 1, "unresolved"))
        seed_legacy_ledger(self.ledger, rows)
        self.unchanged_check(False)

    def test_unresolved_states_are_busy_and_unchanged(self) -> None:
        for state, attempted, remote in (
            ("queued", 0, None), ("submitted", 1, "remote"), ("running", 1, "remote"),
            ("unknown", 1, None), ("future-state", 0, None),
            ("interrupted", 1, None), ("interrupted", 0, "remote"),
        ):
            with self.subTest(state=state, attempted=attempted, has_remote=remote is not None):
                self.ledger.unlink(missing_ok=True)
                seed_legacy_ledger(self.ledger, [("task", state, attempted, remote)])
                self.unchanged_check(True)

    def test_corrupt_foreign_or_unsupported_schema_fails_closed_without_migration(self) -> None:
        self.ledger.write_bytes(b"not a database")
        self.unchanged_check(True)
        for application_id, version in ((0, 1), (0x474D434C, 2)):
            self.ledger.unlink()
            seed_legacy_ledger(self.ledger, [])
            with closing(sqlite3.connect(self.ledger)) as db:
                db.execute(f"PRAGMA application_id={application_id}")
                db.execute(f"PRAGMA user_version={version}")
            self.unchanged_check(True)

    def test_directory_ledger_and_dangling_sidecars_fail_closed(self) -> None:
        self.ledger.mkdir()
        self.assertTrue(operations.legacy_task_busy(self.task))
        self.ledger.rmdir()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.ledger) + suffix)
            sidecar.write_bytes(b"unresolved sidecar")
            self.unchanged_check(True)
            sidecar.unlink()

    def test_checkpointed_wal_uses_immutable_and_does_not_create_empty_sidecars(self) -> None:
        seed_legacy_ledger(self.ledger, [("task", "succeeded", 1, "completed")], wal=True)
        self.assertFalse(Path(str(self.ledger) + "-wal").exists())
        original = sqlite3.connect
        calls: list[str] = []
        statements: list[str] = []

        def connect(database: str, *args: Any, **kwargs: Any):
            calls.append(database)
            db = original(database, *args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch("sqlite3.connect", side_effect=connect):
            self.unchanged_check(False)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("?mode=ro&immutable=1"))
        self.assertTrue(all(sql.strip().upper().startswith(("SELECT", "PRAGMA QUERY_ONLY=ON",
                                                          "PRAGMA APPLICATION_ID", "PRAGMA USER_VERSION"))
                            for sql in statements))
        self.assertFalse(Path(str(self.ledger) + "-wal").exists())
        self.assertFalse(Path(str(self.ledger) + "-shm").exists())

    def test_uncheckpointed_wal_is_not_ignored_and_main_wal_bytes_stay_unchanged(self) -> None:
        seed_legacy_ledger(self.ledger, [], wal=True)
        with closing(sqlite3.connect(self.ledger)) as writer:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO jobs VALUES('task','submitted',1,'in-wal-only')")
            writer.commit()
            wal = Path(str(self.ledger) + "-wal")
            self.assertTrue(wal.is_file())
            files_before = set(self.root.iterdir())
            main_before, wal_before = self.ledger.read_bytes(), wal.read_bytes()
            original = sqlite3.connect
            with patch("sqlite3.connect", wraps=original) as connect:
                self.assertTrue(operations.legacy_task_busy(self.task))
            self.assertTrue(connect.call_args.args[0].endswith("?mode=ro"))
            self.assertNotIn("immutable", connect.call_args.args[0])
            self.assertEqual(self.ledger.read_bytes(), main_before)
            self.assertEqual(wal.read_bytes(), wal_before)
            self.assertEqual(set(self.root.iterdir()), files_before)
            # SQLite's shared read marks may change while a read connection is
            # open. Do not mistake SHM lock bookkeeping for a billing mutation.
            self.assertEqual(writer.execute("SELECT state,attempted,remote_id FROM jobs").fetchall(),
                             [("submitted", 1, "in-wal-only")])


if __name__ == "__main__":
    unittest.main()