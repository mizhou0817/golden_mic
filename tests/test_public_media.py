"""Poster regression: real main GET + synthetic FFmpeg, owned TEMP only.

Execute this file for confinement before any product import. No lifespan,
listening service, real dotenv/data/history or paid provider is used.
"""
from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


def hashes(root):
    # Complete recursive file inventory, no artifact allowlist or exclusions.
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class PublicPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from backend.config import Settings
        from backend.task_manager import TaskRecord
        from backend.revisions import snapshot_revision
        from tests.studio_publication_fixtures import synthetic_publication_files
        self.pm = importlib.import_module("backend.public_media")
        self.temporary = tempfile.TemporaryDirectory(prefix="poster-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.task = self.root / "tasks" / ("d" * 32)
        self.task.mkdir(parents=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        settings = Settings(_env_file=None, app_env="test", data_dir=self.root / "tasks",
                            asr_cache_dir=self.root / "cache", min_free_disk_gb=0)
        with patch("backend.config.get_settings", return_value=settings):
            self.main = importlib.import_module("backend.main")
        self.stack.enter_context(patch.object(self.main.settings, "data_dir", settings.data_dir))
        self.stack.enter_context(patch.object(self.pm, "_LIMIT", asyncio.Semaphore(2)))
        self.record = TaskRecord(task_id="d" * 32, task_dir=self.task, script="Synthetic",
                                 uploads=[], status="done", revision=0)
        self.stack.enter_context(patch.dict(self.main.task_manager._tasks, {self.record.task_id: self.record}))
        for name, content in synthetic_publication_files().items():
            payload = json.loads(content)
            if name == "report.json":
                payload["task_id"] = self.record.task_id
            self.save(name, payload)
        self.save("edl.json", [{"timeline_start": 0, "clips": [{"shot_id": 0, "in": 0, "out": 1}]}])
        self.save("timings.json", [])
        self.save("match_plan.json", [])
        self.make_film("red")
        snapshot_revision(self.record)
        self.base = "http://127.0.0.1:8765"  # ASGI scope only: never a listening socket.
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.main.app, client=("127.0.0.1", 45678)),
                                        base_url=self.base, headers={"Origin": self.base})
        self.url = f"/api/tasks/{self.record.task_id}/poster"

    async def asyncTearDown(self):
        await self.client.aclose()

    def save(self, name, value):
        (self.task / name).write_text(json.dumps(value), encoding="utf-8")

    def make_film(self, color):
        result = subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i",
                                 f"color={color}:s=480x270:r=10:d=1", "-c:v", "libx264", "-threads", "1",
                                 str(self.task / "final.mp4")], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, "Synthetic film creation failed")

    async def test_real_get_cold_and_warm_preserves_complete_revision_hashes(self):
        before = hashes(self.task / "revisions")
        self.assertIn("r0/revision.json", before)
        real = self.pm.run_logged_command
        with patch.object(self.pm, "run_logged_command", wraps=real) as command:
            first = await self.client.get(self.url)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.headers["content-type"], "image/jpeg")
            self.assertTrue(first.content.startswith(b"\xff\xd8"))
            self.assertEqual(hashes(self.task / "revisions"), before)
            all_after_first = hashes(self.task)
            second = await self.client.get(self.url)
            self.assertEqual(second.status_code, 200)
            self.assertEqual(first.content, second.content)
            self.assertEqual(command.await_count, 1)
            self.assertEqual(hashes(self.task), all_after_first)
        self.assertEqual(hashes(self.task / "revisions"), before)
        cache = self.task / "public_previews"
        self.assertEqual(len(list(cache.glob("*.jpg"))), 1)
        self.assertIn("Published film preview", (cache / "task.log").read_text(encoding="utf-8"))

    async def test_new_revision_invalidates_cache_and_old_revision_stays_bound(self):
        from backend.revisions import snapshot_revision
        first = await self.pm.public_preview(self.record)
        first_bytes = first.read_bytes()
        before = hashes(self.task / "revisions")
        self.make_film("blue")
        # Mutable working film cannot retarget a committed r0 poster.
        self.assertEqual(await self.pm.public_preview(self.record), first)
        self.record.revision = 1
        snapshot_revision(self.record)
        both = hashes(self.task / "revisions")
        second = await self.pm.public_preview(self.record)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first_bytes, second.read_bytes())
        self.assertEqual(hashes(self.task / "revisions"), both)
        self.assertEqual({k: v for k, v in both.items() if k.startswith("r0/")}, before)
        self.record.revision = 0
        self.assertEqual(await self.pm.public_preview(self.record), first)
        self.assertEqual(first.read_bytes(), first_bytes)

    async def test_legacy_to_snapshot_same_stat_invalidates_source_location(self):
        from backend.revisions import snapshot_revision
        self.record.revision = 1  # Deliberately no snapshot yet: legacy fallback.
        first = await self.pm.public_preview(self.record)
        stat = (self.task / "final.mp4").stat()
        snapshot_revision(self.record)
        final = self.task / "revisions/r1/final.mp4"
        self.assertEqual((final.stat().st_size, final.stat().st_mtime_ns), (stat.st_size, stat.st_mtime_ns))
        before = hashes(self.task / "revisions")
        second = await self.pm.public_preview(self.record)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(hashes(self.task / "revisions"), before)

    async def test_legacy_source_fingerprint_change_invalidates_cache(self):
        self.record.revision = 1
        first = await self.pm.public_preview(self.record)
        self.make_film("blue")
        second = await self.pm.public_preview(self.record)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first.read_bytes(), second.read_bytes())

    async def test_inflight_preview_keeps_captured_revision_and_two_slot_limit(self):
        from backend.revisions import snapshot_revision
        entered, release = asyncio.Event(), asyncio.Event()
        calls, active, peak = [], 0, 0
        real = self.pm.run_logged_command
        async def held(command, directory, label):
            nonlocal active, peak
            calls.append(list(command))
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            try:
                await release.wait()
                return await real(command, directory, label)
            finally:
                active -= 1
        before = hashes(self.task / "revisions")
        with patch.object(self.pm, "run_logged_command", new=held):
            pending = [asyncio.create_task(self.pm.public_preview(self.record)) for _ in range(3)]
            try:
                await asyncio.wait_for(entered.wait(), 10)
                self.assertEqual(len(calls), 2)
                self.assertFalse(any(p.done() for p in pending))
                self.make_film("blue")
                self.record.revision = 1
                snapshot_revision(self.record)
                both = hashes(self.task / "revisions")
            finally:
                release.set()
                results = await asyncio.gather(*pending)
        self.assertEqual(peak, 2)
        self.assertEqual(len(calls), 2)  # Third waiter rechecks the completed cache.
        self.assertEqual(len(set(results)), 1)
        self.assertTrue(all(Path(c[c.index("-i") + 1]) == self.task / "revisions/r0/final.mp4" for c in calls))
        self.assertEqual(hashes(self.task / "revisions"), both)
        self.assertEqual({k: v for k, v in both.items() if k.startswith("r0/")}, before)
        current = await self.pm.public_preview(self.record)
        self.assertNotEqual(current.read_bytes(), results[0].read_bytes())
        self.assertEqual(len(list((self.task / "public_previews").glob("*.jpg"))), 2)

    async def test_failure_and_cancellation_remove_only_temporary_output(self):
        from fastapi import HTTPException
        before = hashes(self.task / "revisions")
        for failure in (RuntimeError("synthetic encoding failure"), asyncio.CancelledError()):
            async def failed(command, directory, label):
                Path(command[-1]).write_bytes(b"partial")
                raise failure
            with patch.object(self.pm, "run_logged_command", new=failed):
                with self.assertRaises(asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else HTTPException) as caught:
                    await self.pm.public_preview(self.record)
                if isinstance(caught.exception, HTTPException):
                    self.assertEqual(caught.exception.status_code, 503)
            self.assertEqual(list((self.task / "public_previews").glob("*.jpg")), [])
            self.assertEqual(hashes(self.task / "revisions"), before)
        self.assertEqual((await self.client.get(self.url)).status_code, 200)

    async def test_real_get_authorization_before_and_after_await_and_publication_gate(self):
        from backend.publication import publication_gate
        before = hashes(self.task)
        with patch.object(self.pm, "run_logged_command", side_effect=AssertionError("Unauthorized render")):
            denied = await self.client.get(self.url, headers={"X-Task-Token": "invalid"})
            self.assertEqual(denied.status_code, 404)
        self.assertEqual(hashes(self.task), before)
        self.assertFalse(publication_gate(self.record)["passed"])
        denied = await self.client.get(self.url.replace("/poster", "/video?download=true"))
        self.assertEqual(denied.status_code, 409)
        before_revisions = hashes(self.task / "revisions")
        entered, release = asyncio.Event(), asyncio.Event()
        real = self.pm.run_logged_command
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await real(*args, **kwargs)
        token = "synthetic-poster-capability"
        self.record.access_token_hash = hashlib.sha256(token.encode()).hexdigest()
        with patch.object(self.pm, "run_logged_command", new=held):
            pending = asyncio.create_task(self.client.get(self.url, headers={"X-Task-Token": token}))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                self.record.access_token_hash = hashlib.sha256(b"revoked-poster-capability").hexdigest()
            finally:
                release.set()
                response = await pending
        self.assertEqual(response.status_code, 404)
        self.assertFalse(publication_gate(self.record)["passed"])
        self.assertEqual(hashes(self.task / "revisions"), before_revisions)
        # Preview is intentionally allowed without acknowledging publication.
        self.assertEqual((await self.client.get(self.url)).status_code, 200)
        self.assertEqual((await self.client.get(self.url.replace("/poster", "/video?download=true"))).status_code, 409)

    async def test_missing_shot_and_traversal_are_rejected_before_writes(self):
        from backend.revisions import RevisionError, local_file
        from fastapi import HTTPException
        before = hashes(self.task)
        with self.assertRaises(HTTPException) as caught:
            await self.pm.public_preview(self.record, 999)
        self.assertEqual(caught.exception.status_code, 404)
        for name in ("../escape.jpg", "/escape.jpg", "public_previews/../escape.jpg", "public_previews/x:ads", "public_previews\\x"):
            with self.subTest(name=name), self.assertRaises(RevisionError):
                local_file(self.task, name, exists=False)
        self.assertEqual(hashes(self.task), before)

    async def test_linked_cache_or_log_cannot_write_into_revision(self):
        from backend.revisions import RevisionError
        revision = self.task / "revisions/r0"
        cache = self.task / "public_previews"
        before = hashes(revision)
        # Model link detection without requiring Windows symlink privilege or
        # creating a reparse point under the test runner's confinement.
        original = Path.is_symlink
        for linked in (cache, cache / "task.log"):
            with self.subTest(linked=linked.name):
                with patch.object(Path, "is_symlink", new=lambda p: p == linked or original(p)):
                    with self.assertRaises(RevisionError):
                        await self.pm.public_preview(self.record)
                self.assertEqual(hashes(revision), before)


def main():
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    from tests.run_core_validation import Guards, synthetic_environment, normalized
    temporary = Path(tempfile.mkdtemp(prefix="gm-poster-validation-"))
    environment, ffmpeg = synthetic_environment(temporary)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        for name in ("tmp", "tasks", "cache"):
            (temporary / name).mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(temporary / "tmp")
        guard = Guards(temporary, temporary / "evidence", ffmpeg)
        guard.private += (normalized(project / "eval"),)
        guard.install(stack)
        guard.self_check()
        guard.stage = "poster_regression"
        bound = sorted([*project.glob("backend/**/*.py"), Path(__file__), project / "tests/test_mode_api.py",
                        project / "tests/test_publication.py", project / "tests/run_core_validation.py",
                        project / "tests/studio_publication_fixtures.py"])
        source = lambda: {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest() for p in bound}
        before = source()
        (temporary / "source-before.json").write_text(json.dumps(before, indent=2), encoding="utf-8")
        for path in bound:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(PublicPreviewTests)
        if "--repro" in sys.argv:
            suite = unittest.TestSuite([PublicPreviewTests("test_real_get_cold_and_warm_preserves_complete_revision_hashes")])
        elif "--regression" in sys.argv:
            for name in ("tests.test_publication", "tests.test_mode_api"):
                suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
        with (temporary / "tests.log").open("x", encoding="utf-8") as log:
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        unchanged = before == source()
        summary = {"tests": result.testsRun, "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
                   "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
                   "sourceUnchanged": unchanged, "guard": dict(guard.counts), "evidence": str(temporary)}
        (temporary / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print((temporary / "tests.log").read_text(encoding="utf-8"))
        print(json.dumps(summary, ensure_ascii=True))
        return 0 if result.wasSuccessful() and unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())