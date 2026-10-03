"""Offline admission regressions: synthetic TEMP artifacts, no main or rendering."""
# Admission tests deliberately inspect the shared manager's operational state.
# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import copy
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from backend.config import Settings
from backend.models import (
    AnnotatedShot, EDLClip, EDLItem, MatchCandidate, MatchPlanItem, SentenceTiming, TaskState,
    VisionQuality,
)
from backend.revisions import artifact_files, record_metadata, snapshot_revision
from backend.storage import write_json_atomic
from backend.task_manager import TaskManager, TaskRecord
from backend.workbench import create_workbench_router


class WorkbenchCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # The guard nests TEMP; a long prefix makes snapshot atomic JSON paths
        # hit Windows MAX_PATH before publication, unrelated to admission.
        temporary = tempfile.TemporaryDirectory(prefix="wbc-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.dict(os.environ, {}, clear=True):
            self.settings = Settings(
                _env_file=None, app_env="test", data_dir=self.root,  # pyright: ignore[reportCallIssue]
                max_concurrent_tasks=1, max_pending_tasks=1,
            )
        self.manager = TaskManager(self.settings)
        self.publications: dict[str, dict[str, Any]] = {}
        self.invalidations: list[str] = []
        self.requests: list[asyncio.Task[httpx.Response]] = []
        self.record = self.make_record("work")
        # Keep accepted jobs queued until a test explicitly allows completion.
        await self.manager._semaphore.acquire()
        self.client = self.make_client(self.invalidate)
        self.edit: dict[str, Any] = {"expected_revision": 0, "keep_sentence_ids": [0],
                         "edits": [{"sentence_id": 0, "shot_id": 1}]}
        self.restore: dict[str, Any] = {"expected_revision": 0, "revision": 0}
        self.enterContext(patch("backend.workbench._edit_workspace", side_effect=self.render))
        for target in ("EmbeddingProvider.from_settings", "LLMProvider.from_settings", "create_tts_provider"):
            self.enterContext(patch(f"backend.workbench.{target}", side_effect=AssertionError("No paid providers")))
        self.enterContext(patch("backend.workbench.run_logged_command", side_effect=AssertionError("No media processes")))
        self.enterContext(patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("No network")))
        self.enterContext(patch("httpx.AsyncHTTPTransport.handle_async_request", side_effect=AssertionError("No network")))

    async def asyncTearDown(self) -> None:
        jobs: set[asyncio.Task[Any]] = set(self.requests)
        jobs.update(r.background for r in self.manager._tasks.values() if r.background is not None)
        current = asyncio.current_task()
        if current is not None:
            jobs.discard(current)
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    def make_record(self, task_id: str) -> TaskRecord:
        root = self.root / task_id
        for directory in ("tts", "norm"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "tts/0.wav").write_bytes(b"synthetic audio; never decoded")
        for i in range(2):
            (root / f"norm/{i}.mp4").write_bytes(b"synthetic source; never decoded")
        record = TaskRecord(task_id=task_id, task_dir=root, script="Original script", uploads=[],
                            status=TaskState.done, progress=100, message="Published revision")
        models: dict[str, list[BaseModel]] = {
            "timings.json": [SentenceTiming(sentence_id=0, text="Original", audio_path="tts/0.wav",
                                             duration=1, start=0, end=1)],
            "match_plan.json": [MatchPlanItem(sentence_id=0, text="Original", shot_id=0, confidence=.9,
                                               candidates=[MatchCandidate(shot_id=0, similarity=.9)])],
            "shots_annotated.json": [AnnotatedShot(
                shot_id=i, source_index=i, source_scene_index=0, source_name=f"{i}.mp4",
                norm_path=f"norm/{i}.mp4", start=0, end=2, duration=2, status="available",
                description="Synthetic source", quality=VisionQuality(sharp=.9, bright=.9),
            ) for i in range(2)],
            "edl.json": [EDLItem(sentence_id=0, timeline_start=0, timeline_end=1,
                                  clips=[EDLClip.model_validate({"shot_id": 0, "src": "norm/0.mp4", "in": 0, "out": 1})])],
        }
        for name, values in models.items():
            write_json_atomic(root / name, [value.model_dump(mode="json", by_alias=True) for value in values])
        write_json_atomic(root / "report.json", {"task_id": task_id, "rows": []})
        (root / "final.mp4").write_bytes(b"published media")
        self.manager._tasks[task_id] = record
        self.manager._persist_record(record)
        snapshot_revision(record)
        self.publications[task_id] = {
            "published": True, "exemplar": True, "confirmed_by": "teacher",
            "confirmed_revision": 0, "confirmed_binding": "original", "edit_epoch": 0,
            "manual_checks": {"fact": True},
        }
        return record

    def authorize(self, request: Request, task_id: str, write: bool = False) -> TaskRecord | None:
        if request.headers.get("authorization") != "owner":
            raise HTTPException(403, "Denied")
        return self.manager.get(task_id)

    def make_client(self, before: Any) -> httpx.AsyncClient:
        app = FastAPI()
        app.include_router(create_workbench_router(self.settings, self.manager, self.authorize, before))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated",
                                   headers={"authorization": "owner"})
        self.addAsyncCleanup(client.aclose)
        return client

    def invalidate(self, request: Request, task_id: str, write: bool = True) -> None:
        self.assertTrue(write)
        self.invalidations.append(task_id)
        work = self.publications[task_id]
        work.update(published=False, exemplar=False, confirmed_by=None,
                    confirmed_revision=None, confirmed_binding=None, manual_checks={},
                    edit_epoch=work["edit_epoch"] + 1)

    async def invalidate_async(self, request: Request, task_id: str, write: bool = True) -> None:
        await asyncio.sleep(0)  # Deterministic event-loop handoff, not wall-clock waiting.
        self.invalidate(request, task_id, write)

    async def render(self, work: Any, *_args: Any) -> None:
        (work.task_dir / "final.mp4").write_bytes(b"new media")
        work.script = "Edited script"

    def unchanged_state(self) -> tuple[Any, ...]:
        record = self.record
        return (record_metadata(record), record.status, record.background, record.error_stage,
                record.error_message, record.updated_at, copy.deepcopy(self.publications),
                (record.task_dir / "task_state.json").read_bytes(),
                {name: (record.task_dir / name).read_bytes() for name in artifact_files(record.task_dir)})

    async def post(self, endpoint: str, body: dict[str, Any], *, client: httpx.AsyncClient | None = None,
                   task_id: str = "work") -> httpx.Response:
        return await (client or self.client).post(f"/api/tasks/{task_id}/workbench/{endpoint}", json=body)

    def start_request(self, endpoint: str, body: dict[str, Any], client: httpx.AsyncClient) -> asyncio.Task[httpx.Response]:
        task = asyncio.create_task(self.post(endpoint, body, client=client))
        self.requests.append(task)
        return task

    async def test_full_capacity_preserves_confirmation_and_publication(self) -> None:
        for hook in (self.invalidate, self.invalidate_async):
            client = self.make_client(hook)
            for status in (TaskState.queued, TaskState.running):
                self.manager._tasks["pending"] = TaskRecord(
                    task_id="pending", task_dir=self.root / "pending", script="", uploads=[], status=status,
                )
                for endpoint, body in (("edit", self.edit), ("restore", self.restore)):
                    with self.subTest(hook=hook.__name__, status=status, endpoint=endpoint):
                        before = self.unchanged_state()
                        response = await self.post(endpoint, body, client=client)
                        self.assertEqual(response.status_code, 429, response.text)
                        self.assertEqual(self.invalidations, [])
                        self.assertEqual(self.unchanged_state(), before)

    async def test_revision_limit_is_checked_before_invalidation(self) -> None:
        with patch("backend.workbench.MAX_REVISIONS", 1):
            for endpoint, body in (("edit", self.edit), ("restore", self.restore)):
                with self.subTest(endpoint=endpoint):
                    before = self.unchanged_state()
                    response = await self.post(endpoint, body)
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(self.invalidations, [])
                    self.assertEqual(self.unchanged_state(), before)

    async def test_lazy_baseline_capacity_rejection_keeps_published_work(self) -> None:
        self.manager._tasks["pending"] = TaskRecord(
            task_id="pending", task_dir=self.root / "pending", script="", uploads=[],
        )
        for endpoint, body in (("edit", self.edit), ("restore", self.restore)):
            shutil.rmtree(self.record.task_dir / "revisions")
            before = self.unchanged_state()
            response = await self.post(endpoint, body)
            self.assertEqual(response.status_code, 429, response.text)
            self.assertTrue((self.record.task_dir / "revisions/r0/revision.json").is_file())
            self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.invalidations, [])

    async def test_other_rejections_do_not_invalidate(self) -> None:
        cases: list[tuple[str, dict[str, Any], int]] = [
            ("edit", {**self.edit, "expected_revision": 1}, 409),
            ("edit", {**self.edit, "edits": []}, 422),
            ("edit", {**self.edit, "keep_sentence_ids": [99], "edits": []}, 422),
            ("edit", {**self.edit, "edits": [{"sentence_id": 0, "shot_id": 99}]}, 422),
            ("restore", {**self.restore, "revision": 99}, 404),
            ("restore", {**self.restore, "expected_revision": 1}, 409),
        ]
        for endpoint, body, expected in cases:
            with self.subTest(endpoint=endpoint, body=body):
                before = self.unchanged_state()
                response = await self.post(endpoint, body)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual(self.unchanged_state(), before)
        self.manager._draining = True
        for endpoint, body in (("edit", self.edit), ("restore", self.restore)):
            before = self.unchanged_state()
            self.assertEqual((await self.post(endpoint, body)).status_code, 503)
            self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.invalidations, [])

    async def test_pending_added_during_intent_cannot_cause_late_429(self) -> None:
        for endpoint, body in (("edit", self.edit), ("restore", self.restore)):
            with self.subTest(endpoint=endpoint):
                entered, resume = asyncio.Event(), asyncio.Event()
                async def intent(request: Request, task_id: str, write: bool = True) -> None:
                    self.invalidate(request, task_id, write)
                    entered.set()
                    await resume.wait()
                client = self.make_client(intent)
                job = self.start_request(endpoint, body, client)
                await asyncio.wait_for(entered.wait(), 5)
                # Deliberately bypass admission, modelling a legacy status-only
                # writer. Our already-admitted request must not be rejected now.
                self.manager._tasks["pending"] = TaskRecord(
                    task_id="pending", task_dir=self.root / "pending", script="", uploads=[],
                )
                resume.set()
                response = await job
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(self.record.status, TaskState.queued)
                self.assertEqual(self.record.revision, 0)
                self.assertEqual((self.record.task_dir / "final.mp4").read_bytes(), b"published media")
                background = self.record.background
                assert background is not None
                background.cancel()
                await asyncio.gather(background, return_exceptions=True)
                self.manager._tasks.pop("pending")

    async def test_real_manager_admission_observes_reserved_slot(self) -> None:
        entered, resume = asyncio.Event(), asyncio.Event()
        async def intent(request: Request, task_id: str, write: bool = True) -> None:
            self.invalidate(request, task_id, write)
            entered.set()
            await resume.wait()
        job = self.start_request("edit", self.edit, self.make_client(intent))
        await asyncio.wait_for(entered.wait(), 5)
        self.assertEqual(self.record.status, TaskState.queued)
        self.assertIs(self.record.background, job)
        with self.assertRaises(RuntimeError):
            self.manager.add_task("competitor", self.root / "competitor", "", [], defer_start=True)
        self.assertNotIn("competitor", self.manager._tasks)
        resume.set()
        self.assertEqual((await job).status_code, 202)

    async def test_spare_capacity_can_fill_without_rechecking_our_slot(self) -> None:
        self.settings.max_pending_tasks = 2
        entered, resume = asyncio.Event(), asyncio.Event()
        async def intent(request: Request, task_id: str, write: bool = True) -> None:
            self.invalidate(request, task_id, write)
            entered.set()
            await resume.wait()
        job = self.start_request("restore", self.restore, self.make_client(intent))
        await asyncio.wait_for(entered.wait(), 5)
        competitor = self.root / "competitor"
        competitor.mkdir()
        self.manager.add_task("competitor", competitor, "", [], defer_start=True)
        self.assertEqual(sum(r.status == TaskState.queued for r in self.manager._tasks.values()), 2)
        resume.set()
        self.assertEqual((await job).status_code, 202)

    async def test_reservation_is_shared_across_router_instances(self) -> None:
        other = self.make_record("other")
        entered, resume = asyncio.Event(), asyncio.Event()
        async def intent(request: Request, task_id: str, write: bool = True) -> None:
            self.invalidate(request, task_id, write)
            entered.set()
            await resume.wait()
        job = self.start_request("edit", self.edit, self.make_client(intent))
        await asyncio.wait_for(entered.wait(), 5)
        other_publication = copy.deepcopy(self.publications[other.task_id])
        second_router = self.make_client(self.invalidate)
        for endpoint, body in (("edit", self.edit), ("restore", self.restore)):
            self.assertEqual((await self.post(endpoint, body, client=second_router)).status_code, 409)
            response = await self.post(endpoint, body, client=second_router, task_id=other.task_id)
            self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(self.invalidations, ["work"])
        self.assertEqual(self.publications[other.task_id], other_publication)
        resume.set()
        self.assertEqual((await job).status_code, 202)

    async def test_authorization_failure_restores_operational_state(self) -> None:
        self.record.error_stage, self.record.error_message = "workbench", "previous failure"
        self.record.background = asyncio.create_task(asyncio.sleep(0))
        await self.record.background
        self.manager._persist_record(self.record)
        def denied(request: Request, task_id: str, write: bool = True) -> None:
            self.assertEqual(self.record.status, TaskState.queued)
            raise HTTPException(403, "Authorization expired")
        async def denied_async(request: Request, task_id: str, write: bool = True) -> None:
            await asyncio.sleep(0)
            denied(request, task_id, write)
        for hook in (denied, denied_async):
            for endpoint, body in (("edit", self.edit), ("restore", self.restore)):
                before = self.unchanged_state()
                response = await self.post(endpoint, body, client=self.make_client(hook))
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.invalidations, [])
        self.assertEqual((await self.post("edit", self.edit)).status_code, 202)

    async def test_cancelled_authorization_releases_reservation(self) -> None:
        entered = asyncio.Event()
        async def wait_for_authorization(request: Request, task_id: str, write: bool = True) -> None:
            entered.set()
            await asyncio.Event().wait()
        before = self.unchanged_state()
        job = self.start_request("edit", self.edit, self.make_client(wait_for_authorization))
        await asyncio.wait_for(entered.wait(), 5)
        self.assertEqual(self.record.status, TaskState.queued)
        job.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await job
        self.assertEqual(self.unchanged_state(), before)
        self.assertEqual(self.invalidations, [])
        self.assertEqual((await self.post("restore", self.restore)).status_code, 202)

    async def test_deletion_cancels_reservation_without_resurrecting_task(self) -> None:
        entered = asyncio.Event()
        async def wait_for_authorization(request: Request, task_id: str, write: bool = True) -> None:
            entered.set()
            await asyncio.Event().wait()
        job = self.start_request("edit", self.edit, self.make_client(wait_for_authorization))
        await asyncio.wait_for(entered.wait(), 5)
        self.assertIs(self.record.background, job)
        self.assertTrue(await asyncio.wait_for(self.manager.cancel_and_delete("work"), 5))
        self.assertTrue(job.cancelled())
        self.assertIsNone(self.manager.get("work"))
        self.assertEqual(self.record.status, TaskState.cancelled)
        self.assertFalse(self.record.task_dir.exists())
        self.assertEqual(self.invalidations, [])

    async def test_lost_reservation_rejects_without_overwriting_new_owner(self) -> None:
        async def other_job() -> None:
            await asyncio.Event().wait()
        async def changed_owner(request: Request, task_id: str, write: bool = True) -> None:
            await asyncio.sleep(0)
            self.record.background = asyncio.create_task(other_job())
            self.record.message = "Owned by a different operation"
        response = await self.post("edit", self.edit, client=self.make_client(changed_owner))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.record.status, TaskState.queued)
        self.assertEqual(self.record.message, "Owned by a different operation")
        self.assertIsNotNone(self.record.background)
        self.assertEqual(self.invalidations, [])

    async def test_old_cancel_callback_cannot_release_new_reservation(self) -> None:
        self.assertEqual((await self.post("edit", self.edit)).status_code, 202)
        old = self.record.background
        assert old is not None
        await asyncio.sleep(0)  # Let the old job enter its semaphore wait/finally.
        entered, resume = asyncio.Event(), asyncio.Event()
        async def intent(request: Request, task_id: str, write: bool = True) -> None:
            self.invalidate(request, task_id, write)
            entered.set()
            await resume.wait()
        client = self.make_client(intent)
        old.cancel()
        # Cancellation restores done, then this request reserves before the old
        # job's deferred done callback runs. That callback must check ownership.
        job = self.start_request("edit", self.edit, client)
        await asyncio.wait_for(entered.wait(), 5)
        self.assertEqual(self.record.status, TaskState.queued)
        self.assertIs(self.record.background, job)
        resume.set()
        self.assertEqual((await job).status_code, 202)
        self.assertTrue(old.cancelled())

    async def test_persist_failure_precedes_intent_and_rolls_back(self) -> None:
        before = self.unchanged_state()
        persist = self.manager._persist_record
        def fail_reservation(record: TaskRecord) -> None:
            persist(record)
            if record.status == TaskState.queued:
                raise OSError("Injected reservation persistence failure")
        with patch.object(self.manager, "_persist_record", side_effect=fail_reservation):
            with self.assertRaises(OSError):
                await self.post("edit", self.edit)
        self.assertEqual(self.invalidations, [])
        self.assertEqual(self.unchanged_state(), before)

    async def test_drain_after_admission_does_not_reject_invalidated_work(self) -> None:
        async def intent(request: Request, task_id: str, write: bool = True) -> None:
            self.invalidate(request, task_id, write)
            await asyncio.sleep(0)
            self.manager._draining = True
        response = await self.post("edit", self.edit, client=self.make_client(intent))
        self.assertEqual(response.status_code, 202, response.text)

    async def test_accepted_render_failure_retains_intent_but_not_changed_media(self) -> None:
        with patch("backend.workbench._edit_workspace", side_effect=RuntimeError("Synthetic rendering failure")):
            response = await self.post("edit", self.edit)
            self.assertEqual(response.status_code, 202, response.text)
            self.manager._semaphore.release()
            assert self.record.background is not None
            await self.record.background
        self.assertEqual(self.record.status, TaskState.done)
        self.assertEqual(self.record.revision, 0)
        self.assertEqual(self.record.error_message, "operation_failed")
        self.assertEqual((self.record.task_dir / "final.mp4").read_bytes(), b"published media")
        self.assertFalse((self.record.task_dir / "revisions/r1").exists())
        self.assertEqual(self.invalidations, ["work"])
        self.assertFalse(self.publications["work"]["published"])
        self.assertIsNone(self.publications["work"]["confirmed_by"])

    async def test_sync_async_and_optional_hooks_preserve_snapshot_publication(self) -> None:
        for index, hook in enumerate((self.invalidate, self.invalidate_async, None)):
            record = self.make_record(f"accepted{index}")
            old_state = record_metadata(record)
            response = await self.post("edit", self.edit, client=self.make_client(hook), task_id=record.task_id)
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual((record.task_dir / "final.mp4").read_bytes(), b"published media")
            self.assertEqual(record.revision, 0)
            self.assertFalse((record.task_dir / "revisions/r1").exists())
            self.manager._semaphore.release()
            assert record.background is not None
            await record.background
            self.assertEqual(record.status, TaskState.done)
            self.assertEqual(record.revision, 1, record.error_message)
            self.assertEqual((record.task_dir / "final.mp4").read_bytes(), b"new media")
            self.assertEqual((record.task_dir / "revisions/r0/final.mp4").read_bytes(), b"published media")
            self.assertEqual(old_state["script"], "Original script")
            await self.manager._semaphore.acquire()
        self.assertEqual(self.invalidations, ["accepted0", "accepted1"])


if __name__ == "__main__":
    unittest.main()