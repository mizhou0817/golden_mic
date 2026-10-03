"""Focused actual duplicate API contracts; run only under the V2 IO guards.

One real manager/pipeline tone fixture, synthetic provider adapters, real FFmpeg.
No listening server, existing acceptance TEMP, raw response or capability logs.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI

from backend import main, task_operations
from backend.admission import owner_digest
from backend.config import Settings
from backend.drafts import DraftService, DraftUploadStore
from backend.models import TaskState
from backend.operations import UploadCapacityGuard
from backend.storage import write_json_atomic
from backend.task_manager import TaskManager
from backend.task_metadata import expires_at
from tests import test_task_operations as legacy


OBSERVATIONS = []  # Only schema-owned status/boolean evidence, never response bodies.


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class CopyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="v2-copy-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = Settings(_env_file=None, app_env="test", data_dir=self.root / "tasks",
            asr_cache_dir=self.root / "cache", min_free_disk_gb=0, shutdown_grace_seconds=.01,
            video_embedding_enabled=False, entity_verification_enabled=False,
            enforce_origin_check=True, frontend_origins="http://localhost")
        self.settings.data_dir.mkdir()
        self.guard = UploadCapacityGuard(self.settings)
        self.manager = TaskManager(self.settings, self.guard)
        self.store = DraftUploadStore(self.settings)
        self.service = DraftService(self.settings, self.manager, self.store, self.guard)
        self.addAsyncCleanup(self.store.close)
        self.addAsyncCleanup(self.manager.shutdown)
        self.addAsyncCleanup(self.service.close)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.multiple(main, settings=self.settings,
            task_manager=self.manager, _draft_service=self.service, _upload_store=self.store,
            upload_capacity_guard=self.guard))
        # Rebind the real router factory (its manager is a closure), not the API
        # implementation. Other real-main handlers use the patched singleton.
        app = FastAPI()
        app.middleware("http")(main.security_headers)
        app.router.routes.extend(route for route in main.app.routes
            if getattr(route, "path", "") in {"/api/tasks", "/api/tasks/{task_id}",
                                                 "/api/tasks/{task_id}/status"}
            and "GET" in getattr(route, "methods", set()))
        self.rate = Mock(side_effect=AssertionError("Copy must not charge"))
        app.include_router(task_operations.create_task_operations_router(
            self.settings, self.manager, main._authorize_operation, check_rate_limit=self.rate))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app,
            client=("127.0.0.1", 12345)), base_url="http://localhost", trust_env=False)
        self.addAsyncCleanup(self.client.aclose)

    async def post(self, source):
        return await self.client.post(f"/api/tasks/{source.task_id}/duplicate",
            headers={"Origin": "http://localhost", "X-Task-Token": source.access_token},
            json={"expected_revision": source.revision})

    async def assert_copy(self, source):
        before = hashes(source.task_dir)
        quota = self.service.ledger.counts(source.owner_hash, owner_digest("copy-ip"))
        with patch.object(self.manager, "start_task", side_effect=AssertionError("No dispatch")), \
                patch("backend.task_manager.run_pipeline", side_effect=AssertionError("No generation")):
            response = await self.post(source)
        # Classify only a known constant response. Never retain free-form detail.
        body = response.json()
        OBSERVATIONS.append({"status": response.status_code,
            "safe_media_rejection": body.get("detail") ==
                "Duplicate requires complete, safe local media and sufficient disk space",
            "source_unchanged": hashes(source.task_dir) == before,
            "source_lifecycle_v2": source.lifecycle_v2,
            "source_voice_mine": source.preferences.voice == "mine"})
        self.assertEqual(hashes(source.task_dir), before)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(body), {"task_id", "access_token", "status", "revision"})
        target = self.manager.get(body["task_id"])
        self.assertIsNotNone(target)
        self.assertTrue(target.access_token != source.access_token)
        self.assertTrue(target.access_token_hash != source.access_token_hash)
        self.assertFalse(self.manager.token_matches(target, source.access_token))
        self.assertTrue(self.manager.token_matches(target, body["access_token"]))
        self.assertEqual(target.lifecycle_v2, source.lifecycle_v2)
        self.assertEqual(target.owner_hash, source.owner_hash)
        self.assertEqual(target.rules_version, source.rules_version)
        self.assertEqual(target.preferences, source.preferences)
        self.assertEqual(target.display_title, source.display_title)
        self.assertEqual(target.metadata_revision, 0)
        self.assertGreaterEqual(target.created_at, source.created_at)
        self.assertEqual(expires_at(target), expires_at(source))
        self.assertEqual(target.draft_context, {})  # No staging capabilities/admission receipt.
        self.assertFalse(self.service.ledger.accepted(target.task_id))
        self.assertEqual(self.service.ledger.counts(source.owner_hash, owner_digest("copy-ip")), quota)
        self.assertEqual((await self.guard.snapshot()).reserved_bytes, 0)
        self.rate.assert_not_called()
        tokenless = await self.client.get(f"/api/tasks/{target.task_id}")
        self.assertEqual(tokenless.status_code, 404 if source.lifecycle_v2 else 200)
        headers = {"X-Task-Token": body["access_token"]}
        self.assertEqual((await self.client.get(f"/api/tasks/{target.task_id}", headers=headers)).status_code, 200)
        fresh = TaskManager(self.settings)
        self.assertEqual(fresh.restore_tasks(), 2)
        restored = fresh.get(target.task_id)
        self.assertEqual(restored.lifecycle_v2, source.lifecycle_v2)
        self.assertEqual(restored.owner_hash, source.owner_hash)
        self.assertEqual(restored.rules_version, source.rules_version)
        self.assertEqual(expires_at(restored), expires_at(source))
        if source.lifecycle_v2:
            self.client.cookies.set(main._DEV_SESSION_COOKIE, "copy-owner")
            history = (await self.client.get("/api/tasks")).json()["tasks"]
            self.assertTrue(any(row["task_id"] == target.task_id for row in history))
            self.client.cookies.clear()
        self.assertEqual(hashes(source.task_dir), before)
        return target

    async def test_generated_mine_intent_actual_duplicate_api(self):
        from tests.test_mode_pipeline import ModeMediaIntegration, FakeVoice
        ModeMediaIntegration.setUpClass()
        self.addCleanup(ModeMediaIntegration.tearDownClass)
        media = ModeMediaIntegration()
        await media.asyncSetUp()
        self.addAsyncCleanup(media.asyncTearDown)
        receipt = await self.service.create({"mode": "voiceover"}, owner_digest("copy-owner"))
        source = self.manager.get(receipt["task_id"])
        media.root = source.task_dir
        fixture = media.record("voiceover", texts=[("今天活动开幕。", "narration")])
        # UploadStore.view emits a staging thumbnail URL; DraftService persists
        # that public snapshot in pretranscripts. The older media helper omits it.
        snapshots = json.loads((source.task_dir / "pretranscripts.json").read_text(encoding="utf8"))
        snapshots[0]["thumb_url"] = "/api/uploads/up_" + "a" * 32 + "/thumb"
        write_json_atomic(source.task_dir / "pretranscripts.json", snapshots)
        for key in ("uploads", "upload_ids", "script", "sentences"):
            setattr(source, key, getattr(fixture, key))
        source.preferences = source.preferences.model_copy(update={"voice": "mine"})
        source.status = TaskState.queued
        self.service.ledger.accept(source.task_id, source.owner_hash, owner_digest("copy-ip"), "synthetic-input")
        voice = FakeVoice()
        with ExitStack() as providers:
            media.providers(providers, voice=voice)
            await self.manager._run(source)
        self.assertEqual(source.status, TaskState.done)
        self.assertEqual(len(voice.calls), 1)
        self.assertFalse((source.task_dir / "own_voice.wav").exists())
        source.display_title, source.metadata_revision = "Synthetic renamed copy", 1
        self.manager._persist_record(source)
        # Observe the actual plan's exception using only code locations/types.
        original = task_operations._plan_completed
        def observed_plan(*args):
            try:
                return original(*args)
            except Exception as error:
                import traceback
                OBSERVATIONS.append({"plan_exception": type(error).__name__, "locations": [
                    {"file": Path(frame.filename).name, "line": frame.lineno}
                    for frame in traceback.extract_tb(error.__traceback__)
                    if Path(frame.filename).name in {"task_operations.py", "revisions.py"}]})
                raise
        with patch.object(task_operations, "_plan_completed", side_effect=observed_plan):
            target = await self.assert_copy(source)
        self.assertEqual((target.task_dir / "final.mp4").read_bytes(), (source.task_dir / "final.mp4").read_bytes())
        self.assertTrue((target.task_dir / "revisions/r0/final.mp4").is_file())
        copied_snapshots = json.loads((target.task_dir / "pretranscripts.json").read_text(encoding="utf8"))
        self.assertNotIn("thumb_url", copied_snapshots[0])
        self.assertEqual(copied_snapshots[0], {k: v for k, v in snapshots[0].items() if k != "thumb_url"})

    async def test_legacy_rules_one_and_retention_defaults_stay_legacy(self):
        source = legacy.synthetic_record(self.manager, complete=True)
        source.rules_version = 1
        self.manager._persist_record(source)
        await self.assert_copy(source)

    async def test_v2_metadata_and_expiry_are_not_reset_or_capabilities_inherited(self):
        source = legacy.synthetic_record(self.manager, complete=True)
        source.lifecycle_v2 = True
        source.owner_hash = owner_digest("copy-owner")
        source.rules_version = 1
        source.display_title, source.metadata_revision = "Synthetic title", 4
        source.processing_completed_at = datetime.now(timezone.utc) - timedelta(hours=71)
        source.draft_context = {"files": [{"capability": "synthetic-staging-capability"}],
                                "creation_inputs": {"private": "must-not-copy"}}
        self.manager._persist_record(source)
        await self.assert_copy(source)

    async def test_v2_expiry_without_completion_uses_original_updated_time(self):
        source = legacy.synthetic_record(self.manager, complete=True)
        source.lifecycle_v2, source.owner_hash = True, owner_digest("copy-owner")
        source.processing_completed_at = None
        source.updated_at = datetime.now(timezone.utc) - timedelta(hours=71)
        self.manager._persist_record(source)
        await self.assert_copy(source)

    async def test_staging_url_is_removed_not_copied_but_report_urls_remain_strict(self):
        source = legacy.synthetic_record(self.manager, complete=True)
        source.lifecycle_v2, source.owner_hash = True, owner_digest("copy-owner")
        snapshot = {"id": "u", "thumb_url": "/api/uploads/up_synthetic/thumb?token=synthetic-only",
                    "token": "synthetic-only", "transcript": {"segments": [{"text": "synthetic", "start": .1, "end": .9}]}}
        write_json_atomic(source.task_dir / "pretranscripts.json", [snapshot])
        target = await self.assert_copy(source)
        copied = json.loads((target.task_dir / "pretranscripts.json").read_text(encoding="utf8"))
        self.assertEqual(copied, [{k: v for k, v in snapshot.items() if k not in {"thumb_url", "token"}}])
        report = json.loads((source.task_dir / "report.json").read_text(encoding="utf8"))
        report["rows"][0]["thumb_url"] = "/api/uploads/up_synthetic/thumb"
        write_json_atomic(source.task_dir / "report.json", report)
        before, count = hashes(source.task_dir), len(self.manager._tasks)
        self.assertEqual((await self.post(source)).status_code, 409)
        self.assertEqual(len(self.manager._tasks), count)
        self.assertEqual(hashes(source.task_dir), before)

    async def test_v2_revocation_at_each_async_boundary_never_publishes(self):
        import asyncio
        from contextlib import asynccontextmanager
        for boundary in ("plan", "reserve", "copy", "release"):
            with self.subTest(boundary=boundary):
                source = legacy.synthetic_record(self.manager, complete=True)
                source.lifecycle_v2, source.owner_hash = True, owner_digest("copy-owner")
                before, count = hashes(source.task_dir), len(self.manager._tasks)
                original_plan = task_operations._plan_completed
                original_copy = task_operations._drain_copy
                original_reserve = task_operations._reserve_disk
                def revoke():
                    source.access_token_hash = owner_digest("revoked")
                def plan(*args):
                    value = original_plan(*args)
                    if boundary == "plan":
                        revoke()
                    return value
                async def copy_media(*args):
                    await original_copy(*args)
                    if boundary == "copy":
                        revoke()
                @asynccontextmanager
                async def reserve(*args):
                    async with original_reserve(*args) as lease:
                        if boundary == "reserve":
                            revoke()
                        yield lease
                    if boundary == "release":
                        revoke()
                with patch.object(task_operations, "_plan_completed", side_effect=plan), \
                        patch.object(task_operations, "_drain_copy", side_effect=copy_media), \
                        patch.object(task_operations, "_reserve_disk", new=reserve):
                    response = await asyncio.wait_for(self.post(source), 10)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(len(self.manager._tasks), count)
                self.assertEqual(hashes(source.task_dir), before)
                self.assertFalse(task_operations.task_operation_busy(source.task_dir))
                self.assertEqual((await self.guard.snapshot()).reserved_bytes, 0)