"""Focused v2 contracts + real synthetic media pipeline; run this file safely.

Providers in the real-media fixture are deterministic local doubles; all
matching-stage orchestration, PCM, FFmpeg, reports and revision commits are real.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


CONTRACT_EVIDENCE = []


class PlanTests(unittest.TestCase):
    def setUp(self):
        from backend.config import Settings
        from backend.task_manager import TaskManager
        from tests.test_mode_workbench import _seed
        self.temp = tempfile.TemporaryDirectory(prefix="v2-plan-")
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(_env_file=None, app_env="test", data_dir=Path(self.temp.name), min_free_disk_gb=0)
        self.manager = TaskManager(self.settings)
        self.record = _seed(self.manager)

    def compile(self, **values):
        from backend.v2_editing import ApplyRequest, compile_plan
        return compile_plan(self.record, ApplyRequest(expected_revision=0, **values), self.settings)

    def test_union_one_remix_then_replacements(self):
        result = self.compile(edits={"0": "Changed narration"}, replaces={"0": {"instruction": "different source"}},
                              speakers={"speaker-a": {"name": "Person", "role": "Witness"}})
        self.assertEqual([s["stages"] for s in result.steps], [[7, 8, 9, 10], [6, 9, 10]])
        self.assertEqual(result.speakers[0].title, "Witness")
        self.assertEqual(result.edits[0].text, "Changed narration")

    def test_conversion_and_speakers_stage_sets(self):
        self.assertEqual(self.compile(to_narration=[1]).steps[0]["stages"], [6, 7, 8, 9, 10])
        self.assertEqual(self.compile(speakers={"speaker-a": {"name": "New", "role": "Witness"}}).steps[0]["stages"], [8, 9, 10])

    def test_empty_apply_recomposes_and_person_limits(self):
        from pydantic import ValidationError
        self.assertEqual(self.compile().steps[0]["stages"], [9, 10])
        for name, role in (("n" * 9, "r"), ("n", "r" * 13)):
            with self.assertRaises(ValidationError):
                self.compile(speakers={"speaker-a": {"name": name, "role": role}})

    def test_bad_server_identity_conflicts_and_original_quote_rejected(self):
        from backend.revisions import RevisionError
        from pydantic import ValidationError
        cases = [dict(takes={"1": "file:///secret"}), dict(voices={"0": "../voice.wav"}),
                 dict(replaces={"1": {"instruction": "replace"}}), dict(deleted=[0], edits={"0": "X"}),
                 dict(deleted=[0, 1]), dict(trims={"1": {"t0": 0.0, "t1": 99.0}}),
                 dict(speakers={"invented": {"name": "X", "role": "Y"}}), dict(edits={"00": "X"})]
        for values in cases:
            with self.subTest(values=values), self.assertRaises((RevisionError, ValidationError)):
                self.compile(**values)

    def test_durable_publish_rollback_and_recovery(self):
        from backend.revisions import copy_files, artifact_files, publish_artifacts, recover_v2_publication
        from backend.storage import write_json_atomic
        root = self.record.task_dir
        stage = root / "stage"
        copy_files(root, stage, artifact_files(root))
        before = (root / "final.mp4").read_bytes()
        (stage / "final.mp4").write_bytes(b"replacement")
        def fail():
            raise OSError("persistence fault")
        with self.assertRaises(OSError):
            publish_artifacts(stage, root, fail, durable=True)
        self.assertEqual((root / "final.mp4").read_bytes(), before)
        transaction = root / (".publish-" + "a" * 32)
        (transaction / "backup").mkdir(parents=True)
        (root / "final.mp4").replace(transaction / "backup/final.mp4")
        (root / "final.mp4").write_bytes(b"half-installed")
        write_json_atomic(transaction / "state.json", json.loads((root / "task_state.json").read_text(encoding="utf-8")))
        write_json_atomic(root / "v2_publish_journal.json", {"transaction": transaction.name, "phase": "installing",
            "previous": ["final.mp4"], "affected": ["final.mp4"], "had_state": True})
        self.assertTrue(recover_v2_publication(root))
        self.assertEqual((root / "final.mp4").read_bytes(), before)
        self.assertFalse(recover_v2_publication(root))


class RealApplyTests(unittest.IsolatedAsyncioTestCase):
    mixed = False
    @classmethod
    def setUpClass(cls):
        from tests.test_mode_pipeline import ModeMediaIntegration, load_product
        cls.mp = load_product()
        cls.fixture = ModeMediaIntegration()
        cls.fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    async def asyncSetUp(self):
        import httpx
        from fastapi import FastAPI, HTTPException
        from backend.task_manager import TaskManager, TaskRecord
        from backend.models import TaskState
        from backend.v2_editing import create_v2_editing_router
        from tests.test_mode_pipeline import Reporter
        from backend.revisions import snapshot_revision
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        from backend import v2_editing
        self.pipeline_errors = []
        real_execute = v2_editing.execute_plan
        async def capture(*args, **kwargs):
            try:
                return await real_execute(*args, **kwargs)
            except Exception:
                import traceback
                self.pipeline_errors.append(traceback.format_exc())
                raise
        self.stack.enter_context(patch.object(v2_editing, "execute_plan", side_effect=capture))
        self.fixture.providers(self.stack, all_original=not self.mixed)
        source = self.fixture.record("mixed", with_broll=True, texts=[("今天活动开幕。", "quote"), ("现场活动开始了。", "narration")]) if self.mixed else self.fixture.record("original")
        if self.mixed:
            from tests.test_mode_pipeline import invoke
            from backend.storage import write_json_atomic
            snapshots = json.loads((source.task_dir / "pretranscripts.json").read_text(encoding="utf-8"))
            # Independent synthetic pictures, not duplicate IDs for one shot.
            for i in range(2):
                identity = f"extra{i}"
                path = source.task_dir / "raw" / (("c" if i == 0 else "d") * 32 + ".mp4")
                invoke(["ffmpeg", "-v", "error", "-y", "-i", str(self.fixture.broll),
                        "-vf", f"hue=h={90+i*70}", "-c:v", "libx264", "-preset", "ultrafast", "-an", str(path)])
                source.uploads.append(source.uploads[-1].model_copy(update={"upload_id": identity,
                    "original_name": path.name, "stored_name": path.name, "path": path, "size": path.stat().st_size}))
                source.upload_ids.append(identity)
                snapshots.append({**snapshots[1], "id": identity, "name": path.name})
            write_json_atomic(source.task_dir / "pretranscripts.json", snapshots)
        self.record = TaskRecord(**vars(source), status=TaskState.done)
        self.manager = TaskManager(self.fixture.settings)
        self.manager._tasks[self.record.task_id] = self.record
        await self.mp.run_mode_pipeline(self.record, Reporter(), self.fixture.settings)
        self.record.status = TaskState.done
        snapshot_revision(self.record)
        self.denied = False
        self.intent = None
        async def authorize(request, task_id, write=False):
            if self.denied:
                raise HTTPException(403, "reauth")
            return self.record
        async def intent(*args, **kwargs):
            if self.intent:
                await self.intent()
            return await authorize(*args, **kwargs)
        app = FastAPI()
        app.include_router(create_v2_editing_router(self.fixture.settings, self.manager, authorize, intent))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.prefix = "/api/tasks/" + self.record.task_id

    async def asyncTearDown(self):
        if self.record.background and not self.record.background.done():
            self.record.background.cancel()
            await asyncio.gather(self.record.background, return_exceptions=True)

    async def test_real_delete_apply_atomic_read_history_and_restore(self):
        from backend import workbench
        from backend.revisions import version_summaries
        root = self.record.task_dir
        old = hashlib.sha256((root / "final.mp4").read_bytes()).hexdigest()
        entered, proceed = asyncio.Event(), asyncio.Event()
        real = workbench._edit_workspace
        async def held(*args):
            entered.set()
            await proceed.wait()
            await real(*args)
        with patch.object(workbench, "_edit_workspace", side_effect=held):
            response = await self.client.post(self.prefix + "/apply", json={"expected_revision": 0, "deleted": [1]})
            self.assertEqual(response.status_code, 202, response.text)
            await entered.wait()
            try:
                self.assertEqual(self.record.revision, 0)
                self.assertEqual(hashlib.sha256((root / "final.mp4").read_bytes()).hexdigest(), old)
                self.assertEqual((await self.client.get(self.prefix + "/versions/0")).status_code, 200)
                conflict = await self.client.post(self.prefix + "/apply", json={"expected_revision": 0, "deleted": [1]})
                self.assertEqual(conflict.status_code, 409)
            finally:
                proceed.set()
            await self.record.background
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        self.assertEqual(len(version_summaries(root)), 2)
        receipt = (await self.client.get(self.prefix + "/operations/" + response.json()["operation_id"])).json()
        self.assertEqual(receipt["state"], "succeeded", receipt)
        self.assertEqual(receipt["steps"][0]["state"], "succeeded")
        restore = await self.client.post(self.prefix + "/restore", json={"rev": 0, "expected_revision": 1})
        self.assertEqual(restore.status_code, 202, restore.text)
        await self.record.background
        self.assertEqual(self.record.revision, 2)
        self.assertEqual(hashlib.sha256((root / "final.mp4").read_bytes()).hexdigest(), old)

    async def test_final_authorization_denial_and_cancel_preserve_revision(self):
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def intent():
            entered.set()
            await proceed.wait()
        self.intent = intent
        pending = asyncio.create_task(self.client.post(self.prefix + "/apply", json={"expected_revision": 0, "deleted": [1]}))
        await entered.wait()
        self.denied = True
        proceed.set()
        response = await pending
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.record.revision, 0)
        self.assertEqual(self.record.status, "done")
        self.assertFalse((self.record.task_dir / "v2_operations").exists())

    async def test_repeated_worker_cancel_drains_and_rolls_back(self):
        from backend import workbench
        entered, draining, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def blocked(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                draining.set()
                from backend.task_operations import drain_task
                await drain_task(asyncio.create_task(release.wait()))
        with patch.object(workbench, "_edit_workspace", side_effect=blocked):
            response = await self.client.post(self.prefix + "/apply", json={"expected_revision": 0, "deleted": [1]})
            self.assertEqual(response.status_code, 202, response.text)
            await entered.wait()
            worker = self.record.background
            worker.cancel()
            await draining.wait()
            worker.cancel()
            await asyncio.sleep(0)
            self.assertFalse(worker.done())
            self.assertEqual(self.record.revision, 0)
            release.set()
            await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(self.record.status, "done")
        self.assertFalse((self.record.task_dir / "revisions/r1").exists())
        receipt = (await self.client.get(self.prefix + "/operations/" + response.json()["operation_id"])).json()
        self.assertEqual(receipt["state"], "cancelled")


class MixedApplyTests(RealApplyTests):
    mixed = True

    # Inherited base tests also exercise the mixed retained-quote timeline.
    async def test_conversion_then_failed_replace_one_visible_version(self):
        from backend.revisions import version_summaries
        response = await self.client.post(self.prefix + "/apply", json={"expected_revision": 0,
            "to_narration": [0], "replaces": {"0": {"instruction": "different picture"}}})
        self.assertEqual(response.status_code, 202, response.text)
        await self.record.background
        self.assertEqual(self.record.revision, 2, self.pipeline_errors or self.record.error_message)
        self.assertEqual([v["revision"] for v in version_summaries(self.record.task_dir)], [2, 0])
        receipt = (await self.client.get(self.prefix + "/operations/" + response.json()["operation_id"])).json()
        self.assertEqual(receipt["state"], "succeeded", receipt)
        self.assertEqual(receipt["steps"][0]["stages"], [6, 7, 8, 9, 10])
        self.assertEqual(self.record.sentences[0].kind, "narration")

    async def test_real_replacement_and_failed_row_retained(self):
        from backend import v2_editing
        from backend.revisions import version_summaries
        # Two replacement stages after conversion; inject only the first row's
        # known-candidate rejection. The second traverses real mode FFmpeg/QC.
        real = v2_editing._replacement
        async def first_fails(work, row, instruction, settings):
            if row == 0:
                raise v2_editing.ReplacementUnavailable("abstract")
            return await real(work, row, instruction, settings)
        with patch.object(v2_editing, "_replacement", side_effect=first_fails):
            response = await self.client.post(self.prefix + "/apply", json={"expected_revision": 0,
                "to_narration": [0], "replaces": {"0": {"instruction": "abstract"}, "1": {"instruction": "different source"}}})
            self.assertEqual(response.status_code, 202, response.text)
            await self.record.background
        self.assertEqual(self.record.revision, 3, self.pipeline_errors or self.record.error_message)
        self.assertEqual(len(version_summaries(self.record.task_dir)), 2)
        receipt = (await self.client.get(self.prefix + "/operations/" + response.json()["operation_id"])).json()
        self.assertEqual(receipt["replace_failures"]["0"], "abstract")
        self.assertEqual(receipt["steps"][2]["state"], "succeeded", receipt)


class MainColdIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Cold-load the actual main source with isolated settings, no HTTP server."""
    async def asyncSetUp(self):
        import importlib.util
        import httpx
        from backend.config import Settings
        from tests.test_mode_pipeline import ModeMediaIntegration, load_product
        load_product()
        self.fixture = ModeMediaIntegration()
        self.fixture.setUpClass()
        self.addCleanup(self.fixture.tearDownClass)
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.asyncTearDown)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fixture.providers(self.stack)
        from backend import workbench
        self.failures = []
        for name in ("snapshot_revision", "publish_artifacts"):
            original = getattr(workbench, name)
            def capture(*args, _original=original, **kwargs):
                try:
                    return _original(*args, **kwargs)
                except BaseException:
                    import traceback
                    self.failures.append(traceback.format_exc())
                    raise
            self.stack.enter_context(patch.object(workbench, name, side_effect=capture))
        self.settings = Settings(_env_file=None, app_env="test", data_dir=self.fixture.root / "tasks",
            asr_cache_dir=self.fixture.root / "cache", min_free_disk_gb=0, enforce_origin_check=True,
            video_embedding_enabled=False, entity_verification_enabled=False, quality_gate_mode="warn",
            shutdown_grace_seconds=.01)
        self.settings.data_dir.mkdir()
        from backend import studio
        self.stack.enter_context(patch.multiple(studio, _BUSY=set(), _RUNNING={}, _PREPARING={},
                                               _INGESTING={}, _DISK_RESERVATIONS={}))
        spec = importlib.util.spec_from_file_location("backend._v2_main_integration", Path(__file__).resolve().parents[1] / "backend/main.py")
        self.main = importlib.util.module_from_spec(spec)
        with patch("backend.config.get_settings", return_value=self.settings):
            spec.loader.exec_module(self.main)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.main.app), base_url="http://127.0.0.1:8000",
                           headers={"Origin": "http://127.0.0.1:8000"})
        self.addAsyncCleanup(self.client.aclose)
        async def close():
            if self.main._draft_service is not None:
                await self.main._draft_service.close()
            if self.main._upload_store is not None:
                await self.main._upload_store.close()
            await self.main.task_manager.shutdown()
        self.addAsyncCleanup(close)

    async def test_actual_main_draft_to_five_exports(self):
        from backend import studio
        from backend.revisions import version_summaries
        from tests.test_mode_pipeline import invoke
        response = await self.client.post("/api/tasks", json={"mode": "voiceover"})
        self.assertEqual(response.status_code, 201, response.text)
        receipt = response.json()
        prefix = "/api/tasks/" + receipt["task_id"]
        headers = {"X-Task-Token": receipt["access_token"]}
        record = self.main.task_manager.get(receipt["task_id"])
        self.assertIsNone(record.background)
        for index in range(2):
            source = self.fixture.broll
            if index:
                source = self.fixture.root / "other.mp4"
                invoke(["ffmpeg", "-v", "error", "-y", "-i", str(self.fixture.broll), "-vf", "hue=h=120",
                        "-c:v", "libx264", "-preset", "ultrafast", "-an", str(source)])
            content = source.read_bytes()
            added = await self.client.post(prefix + "/files", headers=headers,
                json={"name": source.name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
            self.assertEqual(added.status_code, 201, added.text)
            file_url = prefix + "/files/" + added.json()["file_id"]
            chunk = await self.client.put(file_url + "/chunks/0", headers=headers, content=content)
            self.assertEqual(chunk.status_code, 200, chunk.text)
            complete = await self.client.post(file_url + "/complete", headers=headers, json={})
            self.assertEqual(complete.status_code, 202, complete.text)
            await asyncio.gather(*list(self.main._uploads()._jobs.values()))
            state = await self.client.get(file_url, headers=headers)
            self.assertEqual(state.json()["status"], "ready", state.text)
        script = "测试新闻标题\n\n今天现场活动正式开始了。"
        started = await self.client.post(prefix + "/start", headers=headers, json={"script": script})
        self.assertEqual(started.status_code, 202, started.text)
        await record.background
        self.assertEqual(record.status, "done", record.error_message)
        self.assertTrue(all(s.started_at and s.completed_at for s in record.stages))
        self.assertEqual((await self.client.get(prefix + "/versions", headers=headers)).status_code, 200)
        self.assertEqual((await self.client.get(prefix + "/versions")).status_code, 404)

        async def confirm(revision):
            gate = (await self.client.get(prefix + "/checks", headers=headers)).json()
            keys = [c["key"] for c in gate["checks"] if c["confirmable"]]
            response = await self.client.put(prefix + "/checks", headers=headers,
                json={"expected_revision": revision, "checked": {key: True for key in keys}})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["passed"], response.text)
        await confirm(0)
        original = hashlib.sha256((record.task_dir / "final.mp4").read_bytes()).hexdigest()
        from backend import v2_editing
        entered, release = asyncio.Event(), asyncio.Event()
        real_execute = v2_editing.execute_plan
        async def held_execute(*args, **kwargs):
            entered.set()
            await release.wait()
            return await real_execute(*args, **kwargs)
        with patch.object(v2_editing, "execute_plan", side_effect=held_execute):
            applied = await self.client.post(prefix + "/apply", headers=headers, json={"expected_revision": 0,
                "edits": {"0": "今天现场活动正式开幕了。"}, "replaces": {"0": {"instruction": "另一幅测试画面"}}})
            self.assertEqual(applied.status_code, 202, applied.text)
            await entered.wait()
            try:
                self.assertEqual(applied.json()["current_revision"], 0)
                self.assertEqual(applied.json()["revision"], 2)
                self.assertEqual(len(applied.json()["plan"]["steps"]), 2)
                pending = (await self.client.get(prefix, headers=headers)).json()
                self.assertEqual(pending["revision"], 0)
                self.assertEqual(pending["plan"]["expected_revision"], 0)
                self.assertEqual(pending["plan"]["revision"], 2)
                self.assertEqual(pending["plan"]["steps"], applied.json()["plan"]["steps"])
                self.assertEqual((await self.client.get(prefix + "/status", headers=headers)).json()["plan"], pending["plan"])
            finally:
                release.set()
            await record.background
        self.assertEqual(record.revision, 2, self.failures or record.error_message)
        self.assertEqual([v["revision"] for v in version_summaries(record.task_dir)], [2, 0])
        self.assertEqual(hashlib.sha256((record.task_dir / "revisions/r0/final.mp4").read_bytes()).hexdigest(), original)
        operation = (await self.client.get(prefix + "/operations/" + applied.json()["operation_id"], headers=headers)).json()
        self.assertEqual(operation["state"], "succeeded", operation)
        self.assertTrue(all(step["state"] == "succeeded" for step in operation["steps"]), operation)
        canonical = (await self.client.get(prefix, headers=headers)).json()
        self.assertEqual(canonical["plan"], operation)
        self.assertNotIn(receipt["access_token"], json.dumps(canonical))
        CONTRACT_EVIDENCE.append({"kind": "apply", "task_id": record.task_id,
            "receipt": applied.json(), "pending": pending["plan"], "operation": operation})
        await confirm(2)
        for body, expected in (({"fmt": "mp4"}, 422), ({"fmt": "mp4", "expected_revision": 0}, 409),
                               ({"fmt": "mp4", "expected_revision": True}, 422)):
            refused = await self.client.post(prefix + "/exports", headers=headers, json=body)
            self.assertEqual(refused.status_code, expected, refused.text)
            self.assertFalse(studio._BUSY)
        for invalid in ({}, {"X-Task-Token": "wrong"}, {**headers, "Origin": "https://foreign.invalid"}):
            refused = await self.client.post(prefix + "/exports", headers=invalid,
                json={"fmt": "mp4", "expected_revision": 2})
            self.assertIn(refused.status_code, (403, 404), refused.text)
            self.assertFalse(studio._BUSY)
        for fmt in ("mp4", "mp3", "gif", "srt", "png"):
            options = {"fmt": fmt, "expected_revision": 2, "sub": "std"}
            if fmt in {"mp4", "gif", "png"}:
                options["res"] = "360p"
            if fmt == "png":
                options["frame_seconds"] = json.loads((record.task_dir / "timings.json").read_text(encoding="utf-8"))[-1]["end"]
            response = await self.client.post(prefix + "/exports", headers=headers, json=options)
            self.assertEqual(response.status_code, 202, response.text)
            self.assertNotIn("id", response.json())
            identity = response.json()["export_id"]
            await asyncio.gather(*list(studio._RUNNING.values()))
            job = (await self.client.get(prefix + "/exports/" + identity, headers=headers)).json()
            self.assertEqual(job["state"], "succeeded", job)
            self.assertNotIn("id", job)
            self.assertEqual(job["revision"], 2)
            CONTRACT_EVIDENCE.append({"kind": "export", "task_id": record.task_id,
                "receipt": response.json(), "job": job})
            self.assertNotIn("sha256", json.dumps(job))
            self.assertNotIn(str(record.task_dir), json.dumps(job))
            download = await self.client.get(prefix + "/exports/" + identity + "/file", headers={**headers, "Range": "bytes=0-31"})
            self.assertEqual(download.status_code, 206, download.text[:100] if fmt == "srt" else fmt)
            self.assertEqual(len(download.content), 32)
            self.assertEqual((await self.client.get(prefix + "/exports/" + identity)).status_code, 404)
        self.assertFalse(studio._BUSY)
        self.assertFalse(studio._PREPARING)
        self.assertFalse(studio._DISK_RESERVATIONS)

    async def test_main_routes_unique_checks_validation_and_sample_absence(self):
        from collections import Counter
        def walk(router):
            for route in router.routes:
                if hasattr(route, "original_router"):
                    yield from walk(route.original_router)
                else:
                    yield route
        mounted = list(walk(self.main.app))
        routes = Counter((r.path, method) for r in mounted for method in getattr(r, "methods", []))
        self.assertEqual([key for key, count in routes.items() if count > 1], [])
        for suffix, method in (("apply", "POST"), ("addop", "POST"), ("run", "POST"), ("versions", "GET"),
                               ("restore", "POST"), ("recordings", "POST"), ("exports", "POST"), ("checks", "PUT"),
                               ("exports/{export_id}", "GET"), ("exports/{export_id}", "DELETE"),
                               ("exports/{export_id}/file", "GET")):
            self.assertEqual(routes[("/api/tasks/{task_id}/" + suffix, method)], 1,
                (suffix, [(type(r).__name__, getattr(r, "path", None)) for r in self.main.app.routes]))
        aliases = [r.endpoint for r in mounted if getattr(r, "path", "") in
                   {"/api/tasks/{task_id}/" + name for name in ("apply", "addop", "run")}]
        self.assertEqual(len(aliases), 3)
        self.assertTrue(all(callback is aliases[0] for callback in aliases))
        # Only the legacy delegate is stubbed: actual main routing, v2 identity
        # dispatch and task capability checks remain active.
        draft = (await self.client.post("/api/tasks", json={"mode": "voiceover"})).json()
        async def legacy(request, task_id, identity):
            return {"legacy_dispatch": identity}
        with patch.object(self.main, "_private_endpoint", return_value=legacy) as delegate:
            response = await self.client.get("/api/tasks/" + draft["task_id"] + "/exports/" + "a" * 32,
                headers={"X-Task-Token": draft["access_token"]})
            self.assertEqual(response.json(), {"legacy_dispatch": "a" * 32})
            delegate.assert_called_once_with(self.main._studio_router, "get_job")
        from backend.v2_editing import SAMPLE_ASSETS
        with patch("backend.v2_editing.SAMPLE_ASSETS", self.fixture.root / "no-package"):
            response = await self.client.get("/api/samples/default")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["code"], "sample_unavailable")
        from pydantic import ValidationError
        for body in ({"checked": {"x": 1}}, {"checked_keys": ["x", "x"]},
                     {"checked_keys": [], "checked": {}}, {}):
            with self.assertRaises(ValidationError):
                self.main.CheckConfirmation(expected_revision=0, **body)


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from tests.run_core_validation import Guards, Result, synthetic_environment
    temporary = Path(tempfile.mkdtemp(prefix="gm-v2-editing-validation-"))
    environment, ffmpeg = synthetic_environment(temporary)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        for name in ("tmp", "tasks", "cache"):
            (temporary / name).mkdir()
        tempfile.tempdir = str(temporary / "tmp")
        guard = Guards(temporary, temporary / "evidence", ffmpeg)
        guard.install(stack)
        guard.self_check()
        guard.stage = "v2_focused"
        checked = [root / "backend" / name for name in ("v2_editing.py", "main.py", "workbench.py", "revisions.py", "studio_render.py")]
        checked += [root / "tests" / name for name in ("test_v2_editing.py", "test_v2_exports.py")]
        checked += [root / "frontend/src/lib" / name for name in ("appApi.ts", "workbenchApi.ts")]
        initial = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in checked}
        suite = unittest.TestSuite()
        for module in (sys.argv[1:] or ("tests.test_v2_editing", "tests.test_v2_exports")):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromName(module))
        with (temporary / "tests.log").open("x", encoding="utf-8") as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Result).run(suite)
        print((temporary / "tests.log").read_text(encoding="utf-8"))
        final = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in checked}
        drift = [name for name in initial if initial[name] != final[name]]
        evidence = getattr(sys.modules.get("tests.test_v2_editing"), "CONTRACT_EVIDENCE", [])
        (temporary / "contracts.json").write_text(json.dumps(evidence, ensure_ascii=True), encoding="utf-8")
        clean = result.wasSuccessful() and not drift and not guard.ports and not any(k.startswith("v2_focused:denied:") for k in guard.counts)
        summary = {"tests": result.testsRun, "passed": result.passed, "failures": len(result.failures),
                   "errors": len(result.errors), "skipped": len(result.skipped), "guard": dict(guard.counts),
               "source_hashes": final, "source_drift": drift,
                   "evidence": str(temporary), "success": clean}
        (temporary / "summary.json").write_text(json.dumps(summary, ensure_ascii=True), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=True))
        return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())