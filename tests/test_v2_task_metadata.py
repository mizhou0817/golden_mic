"""Focused real-main ASGI metadata contracts; run under existing V2 IO guards.

Synthetic task artifacts only. Real task-state replace and real owned-TEMP
admission ledger; no provider/model, media processing or service startup.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from pydantic import SecretStr

from backend import main
from backend.admission import AdmissionLedger, owner_digest
from backend.anonymous_access import ANONYMOUS_SESSION_COOKIE, issue_anonymous_session
from backend.config import Settings
from backend.drafts import DraftService
from backend.models import TaskState
from backend.operations import UploadCapacityGuard
from backend.storage import write_json_atomic
from backend.task_manager import TaskManager, TaskRecord
from backend.task_operations import _operation, task_operation_busy


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-metadata-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, app_env="test", data_dir=self.root / "tasks",
            asr_cache_dir=self.root / "cache", min_free_disk_gb=0, enforce_origin_check=True,
            frontend_origins="http://localhost,https://testserver", shutdown_grace_seconds=.01)
        self.settings.data_dir.mkdir()
        self.manager = TaskManager(self.settings)
        self.guard = UploadCapacityGuard(self.settings)
        # No upload store is needed: these tasks have zero files. Its lazy main
        # facade is never touched, while the actual ledger is created/registered.
        self.service = DraftService(self.settings, self.manager, main._LazyUploads(), self.guard)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.multiple(main, settings=self.settings,
            task_manager=self.manager, upload_capacity_guard=self.guard,
            _draft_service=self.service, _upload_store=None))
        self.owner_token = "O" * 43
        self.record = self.record_new()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,
            client=("127.0.0.1", 12345)), base_url="http://localhost")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.service.close)

    def record_new(self, *, lifecycle=True, status=TaskState.done):
        key = uuid4().hex
        directory = self.settings.data_dir / key
        directory.mkdir()
        reference = datetime.now(timezone.utc) - timedelta(hours=1)
        record = TaskRecord(key, directory, "原始标题\n\n未改变的新闻正文。", [],
            status=status, lifecycle_v2=lifecycle, owner_hash=owner_digest(self.owner_token),
            created_at=reference, updated_at=reference, processing_completed_at=reference,
            draft_context={"files": [], "expires_at": time.time() + 86400})
        (directory / "final.mp4").write_bytes(b"synthetic-media-not-a-codec-fixture")
        write_json_atomic(directory / "report.json", {"synthetic": True})
        self.manager._persist_record(record)
        self.manager._tasks[key] = record
        return record

    def headers(self, record=None):
        return {"Origin": "http://localhost", "X-Task-Token": (record or self.record).access_token}

    def body(self, **updates):
        return {"expected_revision": 0, "expected_metadata_revision": 0, "title": "新标题", **updates}

    def url(self, record=None):
        return "/api/tasks/" + (record or self.record).task_id

    async def rename(self, **updates):
        return await self.client.patch(self.url() + "/metadata", headers=self.headers(), json=self.body(**updates))

    def hashes(self, root=None):
        root = root or self.root
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}

    def seed_versions(self):
        for revision in (0, 3):
            root = self.record.task_dir / "revisions" / f"r{revision}"
            root.mkdir(parents=True)
            write_json_atomic(root / "revision.json", {"revision": revision,
                "label": "synthetic", "created_at": self.record.created_at.isoformat()})
            (root / "final.mp4").write_bytes(b"immutable-synthetic-r0")
        (self.record.task_dir / "revisions" / ".snapshot-uncommitted").mkdir()

    async def test_exact_shape_persistence_restart_r0_and_no_other_effects(self):
        self.seed_versions()
        before = copy.deepcopy(self.record.__dict__)
        disk_before = self.hashes()
        response = await self.rename()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"task_id": self.record.task_id,
            "title": "新标题", "revision": 0, "metadata_revision": 1})
        self.assertIn("no-store", response.headers["cache-control"])
        for key, value in before.items():
            if key not in {"display_title", "metadata_revision"}:
                self.assertEqual(getattr(self.record, key), value, key)
        after = self.hashes()
        changed = [p for p in disk_before.keys() | after.keys() if disk_before.get(p) != after.get(p)]
        self.assertEqual(changed, [f"tasks/{self.record.task_id}/task_state.json"])
        self.assertFalse(self.service.ledger.accepted(self.record.task_id))
        fresh = TaskManager(self.settings)
        self.assertEqual(fresh.restore_tasks(), 1)
        restored = fresh.get(self.record.task_id)
        self.assertEqual((restored.display_title, restored.metadata_revision), ("新标题", 1))
        self.assertEqual(self.hashes(), after)

    async def test_old_state_fallback_no_get_baseline_write(self):
        path = self.record.task_dir / "task_state.json"
        old = json.loads(path.read_text(encoding="utf-8"))
        old.pop("display_title"); old.pop("metadata_revision")
        write_json_atomic(path, old)
        fresh = TaskManager(self.settings)
        before = self.hashes()
        self.assertEqual(fresh.restore_tasks(), 1)
        restored = fresh.get(self.record.task_id)
        self.assertIsNone(restored.display_title)
        self.assertEqual(restored.metadata_revision, 0)
        for _ in range(2):
            status = await self.client.get(self.url(), headers=self.headers())
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["title"], "原始标题")
            self.assertEqual(status.json()["version_count"], 0)
        self.assertFalse((self.record.task_dir / "revisions").exists())
        self.assertEqual(self.hashes(), before)

    async def test_status_alias_owner_and_local_history_shared_projection_read_only(self):
        self.seed_versions()
        self.assertEqual((await self.rename()).status_code, 200)
        before = self.hashes()
        statuses = [(await self.client.get(self.url() + suffix, headers=self.headers())).json()
                    for suffix in ("", "/status")]
        local = (await self.client.get("/api/tasks")).json()["tasks"][0]
        self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner_token)
        owner = (await self.client.get("/api/tasks")).json()["tasks"][0]
        for field in ("title", "metadata_revision", "version_count", "expires_at"):
            self.assertTrue(all(row[field] == statuses[0][field] for row in [*statuses, local, owner]))
        self.assertEqual(local["version_count"], 2)  # committed count, NOT revision + 1
        self.assertNotIn("publication", local)
        self.assertEqual(self.hashes(), before)
        etag = (await self.client.get(self.url(), headers=self.headers())).headers["etag"]
        self.assertEqual((await self.client.get(self.url(), headers={**self.headers(), "If-None-Match": etag})).status_code, 304)

    async def test_exact_ttl_rename_and_reads_never_extend(self):
        for state in (TaskState.done, TaskState.failed, TaskState.cancelled, TaskState.draft, TaskState.queued):
            with self.subTest(state=state):
                self.record.status = state
                # Cover updated_at fallback as well as completion-time retention.
                self.record.processing_completed_at = None
                self.manager._persist_record(self.record)
                before = copy.deepcopy(self.record.__dict__)
                result = await self.rename(expected_metadata_revision=self.record.metadata_revision)
                self.assertEqual(result.status_code, 200)
                view = (await self.client.get(self.url(), headers=self.headers())).json()
                expected = (datetime.fromtimestamp(self.record.draft_context["expires_at"], timezone.utc).isoformat()
                    if state == TaskState.draft else (self.record.updated_at + timedelta(hours=72)).isoformat()
                    if state in {TaskState.done, TaskState.failed, TaskState.cancelled} else None)
                self.assertEqual(view["expires_at"], expected)
                self.assertEqual(self.record.updated_at, before["updated_at"])
                self.assertEqual(self.record.draft_context, before["draft_context"])
        self.record.lifecycle_v2 = False
        self.assertIsNone(self.manager.status_response(self.record).expires_at)

    async def test_strict_title_and_cas_inputs_are_sanitized_no_write(self):
        bad = [None, True, 42, "", " " * 4, "界" * 41, "a\n", "x\x00", "x\x7f", "x\x85", "x\u2028", "x\ud800"]
        before = self.hashes()
        for title in bad:
            # ASCII JSON preserves the surrogate so validation, not httpx encoding, rejects it.
            response = await self.client.patch(self.url() + "/metadata", headers=self.headers(),
                content=json.dumps(self.body(title=title), ensure_ascii=True))
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {"detail": "Invalid task metadata"})
        for field in ("expected_revision", "expected_metadata_revision"):
            for value in (True, -1, "0", 0.0, None):
                self.assertEqual((await self.rename(**{field: value})).status_code, 422)
        self.assertEqual((await self.rename(extra="no")).status_code, 422)
        self.assertEqual(self.hashes(), before)
        for title in ("😀" * 40, "字", "  保留空格  "):
            r = await self.rename(title=title, expected_metadata_revision=self.record.metadata_revision)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["title"], title)

    async def test_concurrent_cas_only_one_commit_and_media_revision_must_match(self):
        responses = await asyncio.gather(self.rename(title="一"), self.rename(title="二"))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        self.assertEqual(self.record.metadata_revision, 1)
        self.record.revision = 3
        before = self.hashes()
        response = await self.rename(expected_metadata_revision=1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], {"code": "stale_metadata", "revision": 3, "metadata_revision": 1})
        self.assertEqual(self.hashes(), before)

    async def held(self, action, *, cancel=False):
        entered, release = asyncio.Event(), asyncio.Event()
        data = json.dumps(self.body()).encode()
        async def stream():
            entered.set()
            await release.wait()
            yield data
        request = asyncio.create_task(self.client.patch(self.url() + "/metadata",
            headers={**self.headers(), "Content-Length": str(len(data))}, content=stream()))
        await asyncio.wait_for(entered.wait(), 5)
        action()
        if cancel:
            request.cancel()
        release.set()
        return await request

    async def test_held_body_token_revocation_reauthorized(self):
        before = self.hashes()
        response = await self.held(lambda: setattr(self.record, "access_token_hash", owner_digest("revoked")))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.hashes(), before)

    async def test_held_body_identity_replacement_cannot_mutate_new_record(self):
        replacement = copy.copy(self.record)
        response = await self.held(lambda: self.manager._tasks.update({self.record.task_id: replacement}))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(replacement.metadata_revision, 0)

    async def test_held_body_revision_change_is_conflict(self):
        response = await self.held(lambda: setattr(self.record, "revision", 1))
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.record.display_title)

    async def test_cancel_held_body_has_no_mutation_or_lock_leak(self):
        before = self.hashes()
        with self.assertRaises(asyncio.CancelledError):
            await self.held(lambda: None, cancel=True)
        self.assertEqual(self.hashes(), before)
        self.assertFalse(task_operation_busy(self.record.task_dir))

    async def test_atomic_replace_failure_and_cancellation_roll_back_memory(self):
        before = self.hashes()
        original = Path.replace
        def fail(source, target):
            if Path(target) == self.record.task_dir / "task_state.json":
                raise OSError("synthetic replace failure")
            return original(source, target)
        with patch.object(Path, "replace", new=fail):
            self.assertEqual((await self.rename()).status_code, 503)
        self.assertEqual(self.hashes(), before)
        self.assertEqual((self.record.display_title, self.record.metadata_revision), (None, 0))
        # An injected cancellation before commit must follow the same rollback.
        with patch.object(self.manager, "_persist_record", side_effect=asyncio.CancelledError):
            from backend.task_metadata import commit, MetadataInput
            with _operation(self.record), self.assertRaises(asyncio.CancelledError):
                commit(self.manager, self.record, MetadataInput.model_validate(self.body()))
        self.assertEqual(self.hashes(), before)
        self.assertFalse(task_operation_busy(self.record.task_dir))
        self.assertEqual((await self.rename()).status_code, 200)

    async def test_existing_operation_lock_blocks_rename(self):
        with _operation(self.record):
            self.assertEqual((await self.rename()).status_code, 409)
        self.assertEqual(self.record.metadata_revision, 0)

    async def test_live_worker_blocks_metadata_until_idle(self):
        release = asyncio.Event()
        self.record.background = asyncio.create_task(release.wait())
        try:
            before = self.hashes()
            self.assertEqual((await self.rename()).status_code, 409)
            self.assertEqual(self.hashes(), before)
        finally:
            release.set()
            await self.record.background
        self.assertEqual((await self.rename()).status_code, 200)

    async def test_v2_directory_identity_replacement_rejects_without_write(self):
        self.service.check(self.record)
        moved = self.record.task_dir.with_name("retained-original")
        self.record.task_dir.rename(moved)
        self.record.task_dir.mkdir()
        before = self.hashes()
        self.assertEqual((await self.rename()).status_code, 409)
        self.assertEqual(self.hashes(), before)
        self.assertEqual(self.record.metadata_revision, 0)

    async def test_capability_origin_and_owner_history_scope(self):
        before = self.hashes()
        self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner_token)
        for headers in ({"Origin": "http://localhost"},
                        {"Origin": "http://localhost", "X-Task-Token": "wrong"},
                        {"Origin": "https://wrong.test", "X-Task-Token": self.record.access_token},
                        {"X-Task-Token": self.record.access_token}):
            r = await self.client.patch(self.url() + "/metadata", headers=headers, json=self.body())
            self.assertIn(r.status_code, (403, 404))
        other = self.record_new()
        other.owner_hash = owner_digest("another-owner")
        history = (await self.client.get("/api/tasks")).json()
        self.assertEqual([r["task_id"] for r in history["tasks"]], [self.record.task_id])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("198.51.100.2", 1234)),
                                    base_url="http://testserver") as public:
            self.assertEqual((await public.get("/api/tasks")).status_code, 404)
        self.assertEqual(self.record.metadata_revision, 0)
        self.assertEqual(self.hashes(self.record.task_dir), {p.split(self.record.task_id + "/", 1)[1]: h
            for p, h in before.items() if self.record.task_id + "/" in p})

    def gone(self, *, age=0):
        self.service.ledger.tombstone(self.record.task_id, self.record.owner_hash,
            self.record.access_token_hash, "private-expiry-reason", now=time.time() - age)
        self.manager._tasks.pop(self.record.task_id)

    async def test_tombstone_token_or_owner_opaque_and_bad_explicit_proof_wins(self):
        self.gone()
        before = self.hashes()
        self.assertEqual((await self.client.get(self.url())).status_code, 404)
        good = await self.client.get(self.url(), headers=self.headers())
        self.assertEqual((good.status_code, good.json()), (410, {"detail": {"code": "task_gone"}}))
        self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner_token)
        self.assertEqual((await self.client.get(self.url())).status_code, 410)
        for headers, query in (({"X-Task-Token": "wrong"}, ""), ({"X-Task-Token": ""}, ""),
            ([("X-Task-Token", self.record.access_token)] * 2, ""),
            ({"X-Task-Token": self.record.access_token}, "?token=wrong"),
            ({}, "?token=wrong&token=wrong")):
            self.assertEqual((await self.client.get(self.url() + query, headers=headers)).status_code, 404)
        history = (await self.client.get("/api/tasks")).json()["tasks"]
        self.assertEqual(history, [{"id": self.record.task_id, "task_id": self.record.task_id,
                                    "status": "gone", "title": "未命名视频"}])
        self.assertEqual(self.hashes(), before)

    async def test_tombstone_expired_30_days_is_404_and_no_get_pruning(self):
        self.gone(age=30 * 86400 + 1)
        self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner_token)
        before = self.hashes()
        self.assertEqual((await self.client.get(self.url(), headers=self.headers())).status_code, 404)
        self.assertEqual((await self.client.get("/api/tasks")).json()["tasks"], [])
        self.assertEqual(self.hashes(), before)

    async def test_live_expired_task_no_invalid_proof_leak_no_write(self):
        self.record.processing_completed_at = datetime.now(timezone.utc) - timedelta(hours=73)
        before = self.hashes()
        self.assertEqual((await self.client.get(self.url())).status_code, 404)
        self.assertEqual((await self.client.get(self.url(), headers=self.headers())).status_code, 410)
        self.assertEqual((await self.rename()).status_code, 410)
        self.assertEqual(self.hashes(), before)

    async def test_cold_tombstone_read_existing_ledger_without_constructor(self):
        self.gone()
        before = self.hashes()
        with patch.object(main, "_draft_service", None), patch.object(AdmissionLedger, "__init__", side_effect=AssertionError):
            self.assertEqual((await self.client.get(self.url(), headers=self.headers())).status_code, 410)
        self.assertEqual(self.hashes(), before)

    async def test_cold_status_never_initializes_service_or_recovers_on_get(self):
        before = self.hashes()
        with patch.object(main, "_draft_service", None), patch.object(DraftService, "__init__", side_effect=AssertionError):
            self.assertEqual((await self.client.get(self.url(), headers=self.headers())).status_code, 503)
            self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner_token)
            self.assertEqual((await self.client.get("/api/tasks")).status_code, 200)
        self.assertEqual(self.hashes(), before)

    async def test_missing_ledger_get_never_creates_database(self):
        empty = self.root / "empty-tasks"
        empty.mkdir()
        settings = self.settings.model_copy(update={"data_dir": empty})
        with patch.multiple(main, settings=settings, task_manager=TaskManager(settings), _draft_service=None):
            before = self.hashes()
            self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner_token)
            self.assertEqual((await self.client.get(self.url(), headers=self.headers())).status_code, 404)
            self.assertEqual((await self.client.get("/api/tasks")).json(), {"tasks": [], "total": 0})
            self.assertEqual(self.hashes(), before)
        self.assertFalse((empty / "_v2").exists())

    async def test_production_tombstone_requires_signed_owner_and_explicit_token_precedence(self):
        settings = self.settings.model_copy(update={"app_env": "production",
            "anonymous_session_secret": SecretStr("synthetic-metadata-secret-" * 3)})
        cookie, session = issue_anonymous_session(settings.anonymous_session_secret.get_secret_value(),
                                                 settings.anonymous_session_ttl_seconds)
        self.record.owner_hash = owner_digest(session.session_id)
        self.gone()
        before = self.hashes()
        with patch.object(main, "settings", settings):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://testserver") as client:
                client.cookies.set(main._DEV_SESSION_COOKIE, self.owner_token)
                self.assertEqual((await client.get(self.url())).status_code, 404)
                client.cookies.set(ANONYMOUS_SESSION_COOKIE, cookie + "forged")
                self.assertEqual((await client.get(self.url())).status_code, 404)
                client.cookies.set(ANONYMOUS_SESSION_COOKIE, cookie)
                response = await client.get(self.url())
                self.assertEqual((response.status_code, response.json()), (410, {"detail": {"code": "task_gone"}}))
                self.assertEqual((await client.get(self.url(), headers={"X-Task-Token": "wrong"})).status_code, 404)
                self.assertEqual((await client.get(self.url(), headers=[("X-Task-Token", self.record.access_token)] * 2)).status_code, 404)
                with patch("backend.anonymous_access.time.time", return_value=session.expires_at + 1):
                    self.assertEqual((await client.get(self.url())).status_code, 404)
        self.assertEqual(self.hashes(), before)

    async def test_malformed_missing_and_bounded_body_are_read_only(self):
        before = self.hashes()
        for data in (b"{", b"[]", b"{}"):
            response = await self.client.patch(self.url() + "/metadata", headers=self.headers(), content=data)
            self.assertEqual(response.status_code, 422)
        data = b" " * (256 * 1024 + 1)
        self.assertEqual((await self.client.patch(self.url() + "/metadata", headers=self.headers(), content=data)).status_code, 413)
        self.assertEqual(self.hashes(), before)

    async def test_production_csrf_session_origin_and_postbody_expiry(self):
        settings = self.settings.model_copy(update={"app_env": "production",
            "anonymous_session_secret": SecretStr("synthetic-metadata-secret-" * 3)})
        cookie, session = issue_anonymous_session(settings.anonymous_session_secret.get_secret_value(),
                                                 settings.anonymous_session_ttl_seconds)
        headers = {"Origin": "https://testserver", "X-Task-Token": self.record.access_token,
                   "X-CSRF-Token": session.csrf_token}
        with patch.object(main, "settings", settings):
            # Keep service settings binding to the same owned directory.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="https://testserver") as client:
                self.assertEqual((await client.patch(self.url() + "/metadata", headers=headers, json=self.body())).status_code, 403)
                client.cookies.set(ANONYMOUS_SESSION_COOKIE, cookie)
                self.assertEqual((await client.patch(self.url() + "/metadata", headers={**headers, "X-CSRF-Token": "wrong"}, json=self.body())).status_code, 403)
                entered, release = asyncio.Event(), asyncio.Event()
                data = json.dumps(self.body()).encode()
                async def stream():
                    entered.set(); await release.wait(); yield data
                job = asyncio.create_task(client.patch(self.url() + "/metadata", headers={**headers,
                    "Content-Length": str(len(data))}, content=stream()))
                await asyncio.wait_for(entered.wait(), 5)
                with patch("backend.anonymous_access.time.time", return_value=session.expires_at + 1):
                    release.set()
                    self.assertEqual((await job).status_code, 403)
                self.assertEqual(self.record.metadata_revision, 0)
                self.assertEqual((await client.patch(self.url() + "/metadata", headers=headers, json=self.body())).status_code, 200)