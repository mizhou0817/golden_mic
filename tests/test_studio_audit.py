"""Failure-only Studio authorization and durable-cleanup regression contracts.

These are executable regressions, not expectedFailure or account-policy tests.
One independent IsolatedAsyncioTestCase avoids imported concrete-test discovery.

Each case owns TEMP/tasks/task, explicit settings, a fresh FastAPI router, and an
ASGI-only client. No main/TaskManager/Settings startup, dotenv, paid factories or
live HTTP are used. The contained final is a catalog-only byte sentinel, NOT a
decodable-media fixture. A real project is saved through the API before any
authorization race is armed. Preparation doubles return valid project/probe
shapes only; renderer doubles either fail or block until failure/cancellation.
None of this is evidence of successful media generation or real main authorization.

Proposed job contract: cleanup_pending is bool; cleanup_error is None after
cleanup, otherwise a nonempty generic diagnostic of at most 256 characters.
Filesystem diagnostics must not disclose private paths or replace job.error.
"""
from __future__ import annotations

import asyncio
import copy
import json
import shutil
import tempfile
import unittest
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException, Request

from backend import studio, studio_assets
from backend.studio_render import Clip, ExportOptions, Project, RenderError, Track
from tests.studio_publication_fixtures import confirm_synthetic_publication, synthetic_publication_files


WAIT_SECONDS = 10
CLEANUP_ERROR_LIMIT = 256
RENDER_FAILURE = "synthetic render failure; no media was generated"
PARTIAL_BYTES = b"synthetic unfinished render bytes, not a media success"
PRIVATE_CLEANUP_DETAIL = "private-cleanup-detail-must-not-be-published"
TreeSnapshot = tuple[dict[str, bytes], set[str]]


def tree_snapshot(root: Path) -> TreeSnapshot:
    """Independent byte/directory oracle, including empty output directories."""
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            directories.add(name)
        else:
            files[name] = path.read_bytes()
    return files, directories


def denial_detail(status: int) -> dict[str, str]:
    return {
        "code": "synthetic_session_revoked" if status == 403 else "synthetic_host_gate_closed",
        "message": "Synthetic host authorization was revoked during this request.",
    }


@contextmanager
def fail_only_job_cleanup(work: Path) -> Iterator[list[Path]]:
    """Raise only for THIS job workspace; unrelated cleanup/TEMP teardown works."""
    original = shutil.rmtree
    attempts: list[Path] = []
    target = work.absolute()

    def remove(path: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            matches = Path(path).absolute() == target
        except TypeError:
            matches = False
        if matches:
            attempts.append(target)
            # Deliberately long and private: publishing str(exc), even truncated,
            # is not an acceptable cleanup_error implementation.
            raise OSError(f"{target / PRIVATE_CLEANUP_DETAIL}: " + PRIVATE_CLEANUP_DETAIL * 80)
        return original(path, *args, **kwargs)

    with patch.object(studio.shutil, "rmtree", side_effect=remove):
        yield attempts


class StudioAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="golden-mic-studio-audit-")
        self.addCleanup(self.temp.cleanup)
        # The read-only legacy ledger lookup's grandparent is also owned TEMP.
        self.data = Path(self.temp.name).resolve() / "tasks"
        self.root = self.data / "task"
        self.root.mkdir(parents=True)
        self.originals = {
            "final.mp4": b"catalog-only completed-final sentinel; never decoded",
            "script.txt": b"unchanged synthetic source manuscript",
            **synthetic_publication_files(),
            "task_state.json": b'{"task_id":"task","status":"done","revision":0}',
        }
        for name, content in self.originals.items():
            (self.root / name).write_bytes(content)
        self.record = SimpleNamespace(
            task_id="task", task_dir=self.root, uploads=[], status="done",
            revision=0, background=None,
        )
        confirm_synthetic_publication(self, self.record)
        self.record_before = copy.deepcopy(vars(self.record))
        self.records = {"task": self.record}
        self.manager = SimpleNamespace(_draining=False, get=self.records.get)
        self.settings = SimpleNamespace(
            data_dir=self.data, media_command_timeout_seconds=30,
            minimum_free_disk_bytes=0, shutdown_grace_seconds=0,
        )
        self.project = Project(tracks=[Track(
            id="video", type="video",
            clips=[Clip(id="picture", source_id="final", duration=1)],
        )])
        self.prefix = "/api/tasks/task/studio"
        self.revoked_status: int | None = None
        self.auth_calls: list[tuple[str, bool, int | None, str]] = []
        self.watched_request = ""
        self.first_authorized = asyncio.Event()
        self.auth_hook: Callable[[Request, bool], Awaitable[None]] | None = None

        async def authorize(request: Request, task_id: str, write: bool = False) -> SimpleNamespace:
            marker = request.headers.get("X-Audit-Request", "")
            self.auth_calls.append((request.url.path, write, self.revoked_status, marker))
            if task_id != "task" or request.headers.get("X-Token") != "synthetic-private":
                raise HTTPException(403, "synthetic private task required")
            if write and request.headers.get("X-CSRF") != "synthetic-csrf":
                raise HTTPException(403, "synthetic CSRF required")
            if self.revoked_status is not None:
                raise HTTPException(self.revoked_status, denial_detail(self.revoked_status))
            if self.auth_hook is not None:
                await self.auth_hook(request, write)
            if marker and marker == self.watched_request:
                self.first_authorized.set()
            return self.record

        self.authorize = authorize
        self.external_guards = [self.enterContext(patch(
            target, side_effect=AssertionError("Audit contracts forbid external execution"),
        )) for target in (
            "httpx.HTTPTransport.handle_request",
            "httpx.AsyncHTTPTransport.handle_async_request",
            "socket.create_connection",
            "backend.studio_render.run_logged_command",
            "backend.studio_render.probe",
        )]
        self.probe_guard = self.enterContext(patch.object(
            studio, "probe", new=AsyncMock(side_effect=AssertionError("No real probe in an audit contract")),
        ))
        self.render_guard = self.enterContext(patch.object(
            studio, "render_project", new=AsyncMock(side_effect=RenderError(RENDER_FAILURE)),
        ))
        self.real_simple_project = studio.simple_project
        self.prepare_guard = self.enterContext(patch.object(
            studio, "simple_project", new=AsyncMock(return_value=self.project.model_copy(deep=True)),
        ))
        # Admission must not fail accidentally on the runner's available disk.
        # Only tiny synthetic workspaces are written; this is not a disk metric.
        self.enterContext(patch.object(
            studio.shutil, "disk_usage", return_value=SimpleNamespace(free=1024**4),
        ))
        self.client = self.new_client()
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.drain_owned, True)
        saved = await self.client.post(self.prefix + "/project", json={
            "expected_revision": 0, "project": self.project.model_dump(),
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["revision"], 1)
        self.assertEqual(saved.json()["project"], self.project.model_dump())
        self.assertEqual(studio.catalog(self.record)["final"], self.root / "final.mp4")
        self.assertFalse((self.root / "studio/jobs").exists())
        self.assertFalse((self.root / "studio/outputs").exists())
        self.auth_calls.clear()  # Setup authorization is never the race's signal.

    async def asyncTearDown(self) -> None:
        await self.drain_owned(True)
        self.assert_record_and_sources_unchanged()
        self.assert_no_owned_leases()
        for guard in self.external_guards:
            guard.assert_not_called()
        self.probe_guard.assert_not_awaited()

    def new_client(self) -> httpx.AsyncClient:
        app = FastAPI()
        app.include_router(studio.create_studio_router(self.settings, self.manager, self.authorize))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://testserver",
            headers={"X-Token": "synthetic-private", "X-CSRF": "synthetic-csrf"},
        )

    def owned_workers(self) -> list[asyncio.Task[Any]]:
        return [worker for key, worker in studio._RUNNING.items() if Path(key).is_relative_to(self.root)]

    async def drain_owned(self, cancel: bool) -> None:
        workers = self.owned_workers()
        if cancel:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
        if workers:
            await asyncio.wait_for(asyncio.gather(*workers, return_exceptions=True), WAIT_SECONDS)

    def state(self) -> dict[str, Any]:
        return json.loads((self.root / "studio/state.json").read_bytes())

    def job_path(self, job_id: str) -> Path:
        return self.root / "studio/jobs" / (job_id + ".json")

    def job(self, job_id: str) -> dict[str, Any]:
        return json.loads(self.job_path(job_id).read_bytes())

    def work_path(self, job: dict[str, Any]) -> Path:
        return self.root / "studio/outputs" / f"r{job['revision']}" / job["id"]

    def assert_record_and_sources_unchanged(self) -> None:
        self.assertIs(self.manager.get("task"), self.record)
        self.assertEqual(vars(self.record), self.record_before)
        for name, content in self.originals.items():
            self.assertEqual((self.root / name).read_bytes(), content, name)

    def assert_no_owned_leases(self) -> None:
        self.assertFalse(studio.studio_task_busy(self.root))
        for collection in (studio._BUSY, studio._RUNNING, studio._INGESTING, studio._DISK_RESERVATIONS):
            self.assertFalse([key for key in collection if Path(key).is_relative_to(self.root)])

    def assert_no_admission(self, before: TreeSnapshot, *, prepared: bool = False) -> None:
        after = tree_snapshot(self.root)
        self.assertEqual(after[0], before[0], "Rejected write changed source/state/audit or persisted a job")
        if not prepared:
            self.assertEqual(after[1], before[1], "Body rejection must not create output directories")
            self.assertFalse((self.root / "studio/outputs").exists())
        else:
            # Authorized preparation may create empty outputs/rN parents, but no
            # job workspace, snapshot, result, or job record may survive denial.
            self.assertFalse(list((self.root / "studio/outputs").glob("r*/*")))
        self.assertFalse(list((self.root / "studio/jobs").glob("*.json")))
        self.assert_no_owned_leases()
        self.assert_record_and_sources_unchanged()
        self.render_guard.assert_not_awaited()  # Failure-only proof, never media success.

    def arm_authorization(self, marker: str) -> None:
        self.assertIsNone(self.revoked_status)
        self.watched_request = marker
        self.first_authorized.clear()
        self.auth_calls.clear()

    def watched_calls(self) -> list[tuple[str, bool, int | None, str]]:
        return [call for call in self.auth_calls if call[3] == self.watched_request]

    def assert_reauthorized_denial(self, response: httpx.Response, status: int) -> None:
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json(), {"detail": denial_detail(status)})
        calls = self.watched_calls()
        self.assertGreaterEqual(len(calls), 2, "Host authorizer was not called again after the await")
        self.assertIsNone(calls[0][2], "Initial authorization must genuinely succeed")
        self.assertEqual(calls[-1][2], status)
        self.assertTrue(all(call[1] is True for call in calls), "Reauthorization must retain write=True")

    def payload(self, route: str, revision: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"expected_revision": self.state()["revision"] if revision is None else revision}
        if route in {"/project", "/project/import"}:
            project = copy.deepcopy(self.state()["project"])
            project["tracks"][0]["clips"][0]["brightness"] = 0.2
            body["project"] = project
        elif route == "/actions":
            body.update(op="marker", new_id="audit_marker", at=0.25, label="Synthetic edit")
        elif route == "/subtitles/import":
            body.update(track_id="captions", srt="1\n00:00:00,000 --> 00:00:00,500\nSynthetic caption")
        else:
            body["options"] = {"format": "mp4", "resolution": 360}
        return body

    async def assert_streamed_revocation(
        self, route: str, status: int, payload: dict[str, Any] | None = None,
    ) -> None:
        before = tree_snapshot(self.root)
        encoded = json.dumps(self.payload(route) if payload is None else payload).encode("utf-8")
        split = len(encoded) // 2
        self.arm_authorization("streamed-body")
        emitted: list[int] = []

        async def body() -> AsyncIterator[bytes]:
            await asyncio.wait_for(self.first_authorized.wait(), WAIT_SECONDS)
            self.assertIsNone(self.revoked_status)
            emitted.append(1)
            yield encoded[:split]
            # Only the external host gate changes between parts. No TaskRecord,
            # revision/status, busy flag, file or saved project is changed here.
            self.revoked_status = status
            emitted.append(2)
            yield encoded[split:]

        try:
            response = await asyncio.wait_for(self.client.post(
                self.prefix + route, content=body(),
                headers={"Content-Type": "application/json", "X-Audit-Request": self.watched_request},
            ), WAIT_SECONDS)
            # On a broken baseline, let an incorrectly admitted failure double
            # finish, so an unawaited-yet renderer cannot falsely look untouched.
            await self.drain_owned(False)
        finally:
            self.revoked_status = None
        self.assertEqual(emitted, [1, 2])
        self.assert_reauthorized_denial(response, status)
        self.assert_no_admission(before)
        self.prepare_guard.assert_not_awaited()

    async def test_project_save_streamed_session_revocation_403(self) -> None:
        await self.assert_streamed_revocation("/project", 403)

    async def test_project_save_streamed_host_gate_revocation_409(self) -> None:
        await self.assert_streamed_revocation("/project", 409)

    async def test_project_import_streamed_session_revocation_403(self) -> None:
        await self.assert_streamed_revocation("/project/import", 403)

    async def test_project_import_streamed_host_gate_revocation_409(self) -> None:
        await self.assert_streamed_revocation("/project/import", 409)

    async def test_actions_streamed_session_revocation_403(self) -> None:
        await self.assert_streamed_revocation("/actions", 403)

    async def test_actions_streamed_host_gate_revocation_409(self) -> None:
        await self.assert_streamed_revocation("/actions", 409)

    async def test_subtitle_import_streamed_session_revocation_403(self) -> None:
        await self.assert_streamed_revocation("/subtitles/import", 403)

    async def test_subtitle_import_streamed_host_gate_revocation_409(self) -> None:
        await self.assert_streamed_revocation("/subtitles/import", 409)

    async def test_render_streamed_session_revocation_403(self) -> None:
        await self.assert_streamed_revocation("/render", 403)

    async def test_render_streamed_host_gate_revocation_409(self) -> None:
        await self.assert_streamed_revocation("/render", 409)

    async def test_export_streamed_session_revocation_403(self) -> None:
        await self.assert_streamed_revocation("/export", 403)

    async def test_export_streamed_host_gate_revocation_409(self) -> None:
        await self.assert_streamed_revocation("/export", 409)

    async def await_boundary(self, request: asyncio.Task[httpx.Response], event: asyncio.Event) -> None:
        observer = asyncio.create_task(event.wait())
        try:
            await asyncio.wait({request, observer}, timeout=WAIT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if not event.is_set():
                if request.done():
                    response = request.result()
                    self.fail(f"Request returned HTTP {response.status_code} without the required awaited boundary")
                self.fail("Request did not reach the required awaited boundary")
        finally:
            observer.cancel()
            await asyncio.gather(observer, return_exceptions=True)

    async def assert_preparation_revocation(self, *, via_probe: bool, status: int) -> None:
        before = tree_snapshot(self.root)
        self.arm_authorization("preparation")
        entered, release, returned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        workspaces: list[Path] = []

        async def suspend(work: Path) -> None:
            self.assertTrue(self.first_authorized.is_set())
            self.assertIsNone(self.revoked_status)
            self.assertTrue(work.is_dir())
            workspaces.append(work)
            entered.set()
            await release.wait()
            self.revoked_status = status  # AFTER the real suspension, before returning.
            returned.set()

        async def blocked_project(root: Path, sources: dict[str, Path], options: ExportOptions, work: Path) -> Project:
            self.assertEqual(root, self.root)
            self.assertEqual(sources["final"], self.root / "final.mp4")
            await suspend(work)
            return self.project.model_copy(deep=True)  # Schema only; no media/result files.

        async def blocked_probe(source: Path, work: Path, **kwargs: Any) -> dict[str, Any]:
            self.assertEqual(source, self.root / "final.mp4")
            await suspend(work)
            return {"duration": 1.0}  # Synthetic preparation input, NOT a measured duration.

        preparation = AsyncMock(side_effect=blocked_probe if via_probe else blocked_project)
        with ExitStack() as patches:
            patches.enter_context(patch.object(
                studio, "simple_project", new=self.real_simple_project if via_probe else preparation,
            ))
            if via_probe:
                patches.enter_context(patch.object(studio, "probe", new=preparation))
            request = asyncio.create_task(self.client.post(
                self.prefix + "/export", json=self.payload("/export"),
                headers={"X-Audit-Request": self.watched_request},
            ))
            try:
                await self.await_boundary(request, entered)
                self.assertIsNone(self.revoked_status)
                self.assertEqual(tree_snapshot(self.root)[0], before[0])
                self.assert_record_and_sources_unchanged()
                release.set()
                response = await asyncio.wait_for(request, WAIT_SECONDS)
                await self.drain_owned(False)
            finally:
                release.set()
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)
                self.revoked_status = None
        preparation.assert_awaited_once()
        self.assertTrue(returned.is_set())
        self.assert_reauthorized_denial(response, status)
        self.assertEqual(len(workspaces), 1)
        self.assertFalse(workspaces[0].exists())
        self.assert_no_admission(before, prepared=True)

    async def test_export_reauthorizes_after_awaited_simple_project_409(self) -> None:
        await self.assert_preparation_revocation(via_probe=False, status=409)

    async def test_export_reauthorizes_after_awaited_probe_403(self) -> None:
        await self.assert_preparation_revocation(via_probe=True, status=403)

    async def test_redo_reauthorizes_before_consuming_history(self) -> None:
        marked = await self.client.post(self.prefix + "/actions", json=self.payload("/actions"))
        self.assertEqual(marked.status_code, 200, marked.text)
        undone = await self.client.post(self.prefix + "/actions", json={"expected_revision": 2, "op": "undo"})
        self.assertEqual(undone.status_code, 200, undone.text)
        self.assertTrue(undone.json()["can_redo"])
        await self.assert_streamed_revocation("/actions", 403, {"expected_revision": 3, "op": "redo"})
        before = tree_snapshot(self.root)
        stale = await self.client.post(self.prefix + "/actions", json={"expected_revision": 2, "op": "redo"})
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"], {"message": "revision conflict", "current_revision": 3})
        self.assert_no_admission(before)
        redone = await self.client.post(self.prefix + "/actions", json={"expected_revision": 3, "op": "redo"})
        self.assertEqual(redone.status_code, 200, redone.text)
        self.assertEqual(redone.json()["revision"], 4)
        self.assertEqual(redone.json()["project"], marked.json()["project"])
        self.assertFalse(redone.json()["can_redo"])
        self.assertEqual([row["op"] for row in self.state()["audit"]].count("redo"), 1)
        self.render_guard.assert_not_awaited()

    async def test_revoked_session_precedes_stale_revision_conflict(self) -> None:
        body = self.payload("/project", revision=0)
        await self.assert_streamed_revocation("/project", 403, body)
        before = tree_snapshot(self.root)
        allowed_but_stale = await self.client.post(self.prefix + "/project", json=body)
        self.assertEqual(allowed_but_stale.status_code, 409, allowed_but_stale.text)
        self.assertEqual(allowed_but_stale.json()["detail"], {"message": "revision conflict", "current_revision": 1})
        self.assert_no_admission(before)

    async def test_revision_conflict_rechecked_after_latest_authorization_await(self) -> None:
        self.arm_authorization("late-authorization")
        body_finished, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        encoded = json.dumps(self.payload("/project")).encode("utf-8")

        async def body() -> AsyncIterator[bytes]:
            await asyncio.wait_for(self.first_authorized.wait(), WAIT_SECONDS)
            yield encoded[:len(encoded) // 2]
            yield encoded[len(encoded) // 2:]
            body_finished.set()

        async def hold_latest(request: Request, write: bool) -> None:
            if write and request.headers.get("X-Audit-Request") == self.watched_request and body_finished.is_set():
                entered.set()
                await release.wait()

        self.auth_hook = hold_latest
        request = asyncio.create_task(self.client.post(
            self.prefix + "/project", content=body(),
            headers={"Content-Type": "application/json", "X-Audit-Request": self.watched_request},
        ))
        try:
            await self.await_boundary(request, entered)
            # A second real API mutation commits while latest auth is suspended.
            # Neither request changes TaskRecord.revision/status or source bytes.
            other = await self.client.post(self.prefix + "/actions", json=self.payload("/actions"))
            self.assertEqual(other.status_code, 200, other.text)
            self.assertEqual(other.json()["revision"], 2)
            committed = tree_snapshot(self.root)
            release.set()
            response = await asyncio.wait_for(request, WAIT_SECONDS)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"], {"message": "revision conflict", "current_revision": 2})
            self.assertGreaterEqual(len(self.watched_calls()), 2)
            self.assertTrue(all(call[1] is True and call[2] is None for call in self.watched_calls()))
            self.assert_no_admission(committed)
        finally:
            release.set()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            self.auth_hook = None

    def assert_terminal_job(self, job: dict[str, Any], state: str, *, pending: bool) -> None:
        self.assertEqual(job["state"], state, "Cleanup failure must not leave durable queued/running state")
        self.assertIsNone(job.get("output_id"))
        self.assertIsNone(job.get("result"), "A synthetic failure must never acquire a media result")
        self.assertIsInstance(job.get("error"), str)
        self.assertTrue(job["error"])
        if state == "failed":
            self.assertEqual(job["error"], RENDER_FAILURE)
        elif state == "cancelled":
            self.assertIn("cancel", job["error"].lower())
        self.assertIs(job.get("cleanup_pending"), pending)
        self.assertIn("cleanup_error", job)
        if pending:
            diagnostic = job["cleanup_error"]
            self.assertIsInstance(diagnostic, str)
            self.assertTrue(diagnostic.strip())
            self.assertLessEqual(len(diagnostic), CLEANUP_ERROR_LIMIT)
            self.assertNotIn(job["id"], diagnostic)
            self.assertNotIn("partial.bin", diagnostic)
            self.assertNotRegex(diagnostic, r"(?i)(?:[a-z]:[\\/]|\\\\|studio[\\/]outputs[\\/])")
        else:
            self.assertIsNone(job["cleanup_error"])
        public = json.dumps(job, ensure_ascii=True)
        for secret in (str(self.root), self.root.as_posix(), self.temp.name,
                       Path(self.temp.name).name, PRIVATE_CLEANUP_DETAIL):
            self.assertNotIn(json.dumps(secret, ensure_ascii=True)[1:-1], public)

    def assert_residual_is_counted(self, work: Path) -> None:
        self.assertTrue(work.is_dir())
        self.assertEqual((work / "partial.bin").read_bytes(), PARTIAL_BYTES)
        files = [path for path in (self.root / "studio").rglob("*") if path.is_file()]
        residual = sum(path.stat().st_size for path in files if path.is_relative_to(work))
        other = sum(path.stat().st_size for path in files if not path.is_relative_to(work))
        self.assertGreaterEqual(residual, len(PARTIAL_BYTES))
        self.assertEqual(studio_assets.studio_bytes(self.root), other + residual)
        self.assertEqual(studio_assets.studio_bytes(self.root) - other, residual)

    def assert_one_terminal_job(
        self, before: dict[str, Any], job: dict[str, Any], *, submitted: bool,
    ) -> None:
        current = self.state()
        for field in ("revision", "project", "past", "future"):
            self.assertEqual(current[field], before[field], field)
        self.assertEqual(current["audit"][:len(before["audit"])], before["audit"])
        operations = [row["op"] for row in current["audit"][len(before["audit"]):] if row.get("job_id") == job["id"]]
        self.assertEqual(operations.count("job.submit"), 1 if submitted else 0)
        self.assertEqual(operations.count("job." + job["state"]), 1)
        self.assertNotIn("job.succeeded", operations)
        if job["state"] != "interrupted":
            self.assertNotIn("job.interrupted", operations, "Worker failure must not be relabelled as a restart")
        self.assertEqual(sorted(path.name for path in self.job_path(job["id"]).parent.iterdir()), [job["id"] + ".json"])
        self.assert_no_owned_leases()
        self.assert_record_and_sources_unchanged()

    async def assert_output_unavailable(self, client: httpx.AsyncClient, job_id: str) -> None:
        with patch.object(studio, "FileResponse", side_effect=AssertionError("Partial output must never be served")) as serve:
            response = await client.get(self.prefix + "/outputs/" + job_id, headers={"Range": "bytes=0-15"})
        self.assertEqual(response.status_code, 409, response.text)
        serve.assert_not_called()

    async def assert_authorized_cleanup_retry(
        self, client: httpx.AsyncClient, original: dict[str, Any], work: Path,
        before: dict[str, Any], *, submitted: bool,
    ) -> None:
        url = self.prefix + "/jobs/" + original["id"]
        retained = tree_snapshot(self.root)
        self.revoked_status = 403
        try:
            denied = await client.get(url)
            self.assertEqual(denied.status_code, 403, denied.text)
            self.assertEqual(tree_snapshot(self.root), retained, "Unauthorized GET must not perform cleanup/recovery")
        finally:
            self.revoked_status = None
        response = await client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        recovered = response.json()
        self.assert_terminal_job(recovered, original["state"], pending=False)
        for field in ("id", "revision", "pipeline_revision", "kind", "options", "created_at", "error", "finished_at"):
            self.assertEqual(recovered.get(field), original.get(field), field)
        self.assertFalse(work.exists())
        self.assertEqual(self.job(original["id"]), recovered)
        await self.assert_output_unavailable(client, original["id"])
        settled = tree_snapshot(self.root)
        again = await client.get(url)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json(), recovered)
        self.assertEqual(tree_snapshot(self.root), settled, "Already-clean terminal GET must be idempotent")
        self.assert_one_terminal_job(before, recovered, submitted=submitted)
        physical = sum(path.stat().st_size for path in (self.root / "studio").rglob("*") if path.is_file())
        self.assertEqual(studio_assets.studio_bytes(self.root), physical)

    async def assert_worker_cleanup_failure(self, *, cancel: bool) -> None:
        before = self.state()
        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def blocked(project: Project, options: ExportOptions, sources: dict[str, Path], work: Path, **kwargs: Any) -> None:
            (work / "partial.bin").write_bytes(PARTIAL_BYTES)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise RenderError(RENDER_FAILURE)

        renderer = AsyncMock(side_effect=blocked)
        with patch.object(studio, "render_project", new=renderer):
            try:
                admitted = await self.client.post(self.prefix + "/render", json=self.payload("/render"))
                self.assertEqual(admitted.status_code, 202, admitted.text)
                job_id = admitted.json()["id"]
                work = self.work_path(admitted.json())
                worker = studio._RUNNING[str(self.job_path(job_id))]
                await asyncio.wait_for(entered.wait(), WAIT_SECONDS)
                self.assertTrue(studio.studio_task_busy(self.root))
                self.assertEqual(self.job(job_id)["state"], "running")
                with fail_only_job_cleanup(work) as attempts:
                    cancellation_response = None
                    if cancel:
                        cancellation_response = await asyncio.wait_for(
                            self.client.delete(self.prefix + "/jobs/" + job_id), WAIT_SECONDS,
                        )
                    else:
                        release.set()
                    # Inspect the durable outcome even if the broken baseline's
                    # finally raises OSError. That exception is NOT accepted.
                    outcomes = await asyncio.wait_for(
                        asyncio.gather(worker, return_exceptions=True), WAIT_SECONDS,
                    )
                    persisted = self.job(job_id)  # Before GET could attempt recovery.
                    state = "cancelled" if cancel else "failed"
                    self.assert_terminal_job(persisted, state, pending=True)
                    if cancel:
                        self.assertTrue(cancelled.is_set())
                        self.assertIsNotNone(cancellation_response)
                        assert cancellation_response is not None
                        self.assertEqual(cancellation_response.status_code, 200, cancellation_response.text)
                        self.assert_terminal_job(cancellation_response.json(), state, pending=True)
                    self.assertTrue(all(
                        outcome is None or (cancel and isinstance(outcome, asyncio.CancelledError))
                        for outcome in outcomes
                    ), "Cleanup OSError must not escape the worker")
                    for _ in range(2):
                        polled = await self.client.get(self.prefix + "/jobs/" + job_id)
                        self.assertEqual(polled.status_code, 200, polled.text)
                        self.assert_terminal_job(polled.json(), state, pending=True)
                        self.assertEqual(polled.json()["error"], persisted["error"])
                    await self.assert_output_unavailable(self.client, job_id)
                    self.assertTrue(attempts, "The intended workspace removal must actually fail")
                    self.assert_residual_is_counted(work)
                    self.assert_one_terminal_job(before, persisted, submitted=True)
                # rmtree now works; only an authorized GET may finish cleanup.
                await self.assert_authorized_cleanup_retry(self.client, persisted, work, before, submitted=True)
                renderer.assert_awaited_once()
            finally:
                release.set()
                await self.drain_owned(True)

    async def test_synthetic_cancelled_render_cleanup_failure_is_terminal_and_retryable(self) -> None:
        await self.assert_worker_cleanup_failure(cancel=True)

    async def test_synthetic_failed_render_cleanup_failure_is_terminal_and_retryable(self) -> None:
        await self.assert_worker_cleanup_failure(cancel=False)

    async def test_restart_orphan_cleanup_failure_is_terminal_and_retryable(self) -> None:
        before = self.state()
        orphan = {
            "id": "b" * 32, "revision": before["revision"], "pipeline_revision": self.record.revision,
            "kind": "render", "state": "running", "created_at": 1.0,
            "options": ExportOptions(resolution=360).model_dump(), "output_id": None,
            "error": None, "qc": "unreviewed synthetic derivative", "disclosure": False,
        }
        work = self.work_path(orphan)
        work.mkdir(parents=True)
        (work / "partial.bin").write_bytes(PARTIAL_BYTES)
        (work / "snapshot.json").write_text(json.dumps({
            "project": self.project.model_dump(), "options": orphan["options"], "sources": {},
        }), encoding="utf-8")
        self.job_path(orphan["id"]).parent.mkdir(parents=True)
        self.job_path(orphan["id"]).write_text(json.dumps(orphan), encoding="utf-8")
        self.assert_no_owned_leases()  # No live owner: this is a restart orphan.
        async with self.new_client() as restarted:
            with fail_only_job_cleanup(work) as attempts:
                response = await restarted.get(self.prefix + "/jobs/" + orphan["id"])
                self.assertEqual(response.status_code, 200, response.text)
                interrupted = response.json()
                self.assert_terminal_job(interrupted, "interrupted", pending=True)
                self.assertEqual(self.job(orphan["id"]), interrupted)
                again = await restarted.get(self.prefix + "/jobs/" + orphan["id"])
                self.assertEqual(again.status_code, 200, again.text)
                self.assert_terminal_job(again.json(), "interrupted", pending=True)
                self.assertEqual(again.json()["error"], interrupted["error"])
                await self.assert_output_unavailable(restarted, orphan["id"])
                self.assertTrue(attempts)
                self.assert_residual_is_counted(work)
                self.assert_one_terminal_job(before, interrupted, submitted=False)
            await self.assert_authorized_cleanup_retry(restarted, interrupted, work, before, submitted=False)
        self.render_guard.assert_not_awaited()
        self.prepare_guard.assert_not_awaited()

    # Additional negative storage contracts: a renderer RETURN below is only a
    # typed mapping over bounded, undecodable bytes. It reaches an injected
    # manifest failure, never acceptance of a successful real-media output.

    async def finish_storage_worker(
        self, worker: asyncio.Task[Any] | None, release: asyncio.Event,
    ) -> None:
        """Consume captured worker errors even after its _RUNNING entry is gone."""
        if worker is not None and not worker.done():
            worker.cancel()
        release.set()
        if worker is not None:
            await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), WAIT_SECONDS)
        await self.drain_owned(True)

    async def assert_manifest_write_failure(self, *, cleanup_fails: bool) -> None:
        before = self.state()
        entered, release, returned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        worker: asyncio.Task[Any] | None = None

        async def rendered(
            project: Project, options: ExportOptions, sources: dict[str, Path], work: Path, **kwargs: Any,
        ) -> dict[str, Any]:
            # NOT an encoder/probe success: these bytes are deliberately not MP4.
            (work / "output.mp4").write_bytes(PARTIAL_BYTES)
            (work / "partial.bin").write_bytes(PARTIAL_BYTES)
            entered.set()
            await release.wait()
            result: dict[str, Any] = {
                "file": "output.mp4", "bytes": len(PARTIAL_BYTES), "duration": 1.0,
                "applied": options.model_dump(),
            }
            returned.set()
            return result

        def assert_failed(job: dict[str, Any], *, pending: bool) -> None:
            self.assertEqual(job["state"], "failed")
            self.assertIsNone(job["output_id"])
            self.assertNotIn("result", job, "A failed manifest must not publish the renderer's synthetic result")
            self.assertEqual(job["error"], "local media render failed; inspect protected job logs before retry")
            self.assertIs(job["cleanup_pending"], pending)
            diagnostic = job["cleanup_error"]
            if pending:
                self.assertIsInstance(diagnostic, str)
                self.assertTrue(diagnostic.strip())
                self.assertLessEqual(len(diagnostic), CLEANUP_ERROR_LIMIT)
                for private in (job["id"], "output.mp4", "partial.bin", "manifest.json"):
                    self.assertNotIn(private, diagnostic)
                self.assertNotRegex(diagnostic, r"(?i)(?:[a-z]:[\\/]|\\\\|studio[\\/]outputs[\\/])")
            else:
                self.assertIsNone(diagnostic)
            public = json.dumps(job, ensure_ascii=True)
            for private in (str(self.root), self.root.as_posix(), self.temp.name,
                            Path(self.temp.name).name, PRIVATE_CLEANUP_DETAIL):
                self.assertNotIn(json.dumps(private, ensure_ascii=True)[1:-1], public)

        self.render_guard.side_effect = rendered
        try:
            admitted = await self.client.post(self.prefix + "/render", json=self.payload("/render"))
            self.assertEqual(admitted.status_code, 202, admitted.text)
            job_id = admitted.json()["id"]
            work = self.work_path(admitted.json())
            worker = studio._RUNNING[str(self.job_path(job_id))]
            await asyncio.wait_for(entered.wait(), WAIT_SECONDS)
            self.assertEqual(self.job(job_id)["state"], "running")
            self.assertEqual((work / "output.mp4").read_bytes(), PARTIAL_BYTES)
            self.assertTrue(0 < len(PARTIAL_BYTES) < min(1024, studio.MAX_BYTES))
            manifest = work / "manifest.json"
            manifest_attempts: list[dict[str, Any]] = []
            original_write = studio.write_json_atomic

            def fail_manifest(path: Path, payload: Any) -> None:
                if path.absolute() == manifest.absolute():
                    manifest_attempts.append(copy.deepcopy(payload))
                    raise OSError(f"{manifest}: {PRIVATE_CLEANUP_DETAIL}")
                original_write(path, payload)  # Admission, job and audit writes remain real.

            cleanup_attempts: list[Path] = []
            with ExitStack() as faults:
                faults.enter_context(patch.object(studio, "write_json_atomic", side_effect=fail_manifest))
                if cleanup_fails:
                    cleanup_attempts = faults.enter_context(fail_only_job_cleanup(work))
                release.set()
                outcomes = await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), WAIT_SECONDS)
                self.assertEqual(outcomes, [None], "Manifest failure should persist as a failed job, not escape")
                self.assertTrue(returned.is_set(), "The renderer must return before the injected storage failure")
                self.assertEqual(len(manifest_attempts), 1)
                self.assertEqual(manifest_attempts[0]["job"], job_id)
                self.assertEqual(manifest_attempts[0]["file"], "output.mp4")
                self.assertEqual(manifest_attempts[0]["bytes"], len(PARTIAL_BYTES))
                self.assertEqual(manifest_attempts[0]["duration"], 1.0)
                self.assertFalse(manifest.exists())
                failed = self.job(job_id)  # Inspect disk BEFORE a GET could repair cleanup.
                assert_failed(failed, pending=cleanup_fails)
                settled = tree_snapshot(self.root)
                for _ in range(2):
                    polled = await self.client.get(self.prefix + "/jobs/" + job_id)
                    self.assertEqual(polled.status_code, 200, polled.text)
                    self.assertEqual(polled.json(), failed)
                await self.assert_output_unavailable(self.client, job_id)
                self.assertEqual(tree_snapshot(self.root), settled, "Polling must not resubmit or repeat terminal audit")
                if cleanup_fails:
                    self.assertTrue(cleanup_attempts)
                    self.assert_residual_is_counted(work)
                    self.assertEqual((work / "output.mp4").read_bytes(), PARTIAL_BYTES)
                else:
                    self.assertFalse(work.exists())
                self.assert_one_terminal_job(before, failed, submitted=True)

            # Once removal works, authorized cleanup may change ONLY its flags.
            clean_response = await self.client.get(self.prefix + "/jobs/" + job_id)
            self.assertEqual(clean_response.status_code, 200, clean_response.text)
            clean = clean_response.json()
            assert_failed(clean, pending=False)
            self.assertEqual(clean, {**failed, "cleanup_pending": False, "cleanup_error": None})
            self.assertEqual(self.job(job_id), clean)
            self.assertFalse(work.exists())
            settled = tree_snapshot(self.root)
            again = await self.client.get(self.prefix + "/jobs/" + job_id)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json(), clean)
            await self.assert_output_unavailable(self.client, job_id)
            self.assertEqual(tree_snapshot(self.root), settled)
            self.assert_one_terminal_job(before, clean, submitted=True)
            self.render_guard.assert_awaited_once()
            self.prepare_guard.assert_not_awaited()
        finally:
            await self.finish_storage_worker(worker, release)

    async def test_synthetic_manifest_write_failure_discards_result_and_cleans_output(self) -> None:
        await self.assert_manifest_write_failure(cleanup_fails=False)

    async def test_synthetic_manifest_write_failure_retains_counted_cleanup_pending(self) -> None:
        await self.assert_manifest_write_failure(cleanup_fails=True)

    async def test_terminal_job_write_failure_surfaces_from_cancellation_and_releases_leases(self) -> None:
        before = self.state()
        entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        worker: asyncio.Task[Any] | None = None

        async def blocked(
            project: Project, options: ExportOptions, sources: dict[str, Path], work: Path, **kwargs: Any,
        ) -> None:
            (work / "partial.bin").write_bytes(PARTIAL_BYTES)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise RenderError(RENDER_FAILURE)

        self.render_guard.side_effect = blocked
        try:
            admitted = await self.client.post(self.prefix + "/render", json=self.payload("/render"))
            self.assertEqual(admitted.status_code, 202, admitted.text)
            job_id = admitted.json()["id"]
            target = self.job_path(job_id)
            work = self.work_path(admitted.json())
            worker = studio._RUNNING[str(target)]
            await asyncio.wait_for(entered.wait(), WAIT_SECONDS)
            self.assertEqual(self.job(job_id)["state"], "running")
            running_bytes = target.read_bytes()
            admitted_state = self.state()
            attempts: list[dict[str, Any]] = []
            writes: list[Path] = []
            original_write = studio.write_json_atomic
            failure = OSError(f"{target}: {PRIVATE_CLEANUP_DETAIL}")

            def fail_terminal_job(path: Path, payload: Any) -> None:
                writes.append(path.absolute())
                # Not the manifest, queued/running admission, or audit file.
                if path.absolute() == target.absolute() and isinstance(payload, dict) and payload.get("state") == "cancelled":
                    attempts.append(copy.deepcopy(payload))
                    raise failure
                original_write(path, payload)

            with patch.object(studio, "write_json_atomic", side_effect=fail_terminal_job):
                response = await asyncio.wait_for(self.client.delete(self.prefix + "/jobs/" + job_id), WAIT_SECONDS)
                outcomes = await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), WAIT_SECONDS)
                self.assertTrue(cancelled.is_set())
                self.assertEqual(len(attempts), 1)
                self.assert_terminal_job(attempts[0], "cancelled", pending=False)
                self.assertEqual(writes, [target.absolute()])
                self.assertEqual(len(outcomes), 1)
                self.assertIs(outcomes[0], failure, "Terminal persistence failure must escape the background worker")
                self.assertEqual(response.status_code, 500, "DELETE must not claim cancellation completed")
                self.assertEqual(response.text, "Internal Server Error")
                self.assertEqual(target.read_bytes(), running_bytes)
                self.assertEqual(self.state(), admitted_state)
                self.assertFalse(work.exists())
                self.assert_no_owned_leases()

            # No recovery GET here: an unwritten terminal record is not durable
            # cancellation, and this test makes no crash/power-loss repair claim.
            self.assertEqual(admitted_state["audit"][:len(before["audit"])], before["audit"])
            self.assertEqual([row["op"] for row in admitted_state["audit"][len(before["audit"]):]], ["job.submit"])
            for field in ("revision", "project", "past", "future"):
                self.assertEqual(admitted_state[field], before[field], field)
            self.assertEqual(sorted(path.name for path in target.parent.iterdir()), [target.name])
            self.render_guard.assert_awaited_once()
            self.prepare_guard.assert_not_awaited()
        finally:
            # Consume the captured failing task, not just the now-empty registry;
            # the injected writer is restored before ordinary TEMP teardown.
            await self.finish_storage_worker(worker, release)

    async def test_substituted_temp_root_is_not_cleaned_or_written_by_original_worker(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        worker: asyncio.Task[Any] | None = None
        held = self.data / "original-held"
        replacement = self.data / "replacement"
        substituted = False

        async def blocked(
            project: Project, options: ExportOptions, sources: dict[str, Path], work: Path, **kwargs: Any,
        ) -> None:
            (work / "partial.bin").write_bytes(PARTIAL_BYTES)
            entered.set()
            await release.wait()
            raise RenderError(RENDER_FAILURE)

        self.render_guard.side_effect = blocked
        try:
            admitted = await self.client.post(self.prefix + "/render", json=self.payload("/render"))
            self.assertEqual(admitted.status_code, 202, admitted.text)
            job_id = admitted.json()["id"]
            worker = studio._RUNNING[str(self.job_path(job_id))]
            await asyncio.wait_for(entered.wait(), WAIT_SECONDS)
            self.assertEqual(self.job(job_id)["state"], "running")
            running_tree = tree_snapshot(self.root)
            self.assertFalse(held.exists())
            replacement.mkdir()
            other_work = replacement / self.work_path(admitted.json()).relative_to(self.root)
            other_work.mkdir(parents=True)
            (other_work / "partial.bin").write_bytes(b"unrelated synthetic replacement output; preserve")
            (replacement / "keep.bin").write_bytes(b"unrelated synthetic replacement root; preserve")
            replacement_tree = tree_snapshot(replacement)
            for directory in (self.root, replacement):
                self.assertFalse(directory.is_symlink())
                # Also rejects junctions/name-surrogate reparse points on Windows.
                self.assertEqual(studio_assets.contained(self.data, directory), directory)
            original_stat, replacement_stat = self.root.stat(), replacement.stat()
            self.assertNotEqual((original_stat.st_dev, original_stat.st_ino),
                                (replacement_stat.st_dev, replacement_stat.st_ino))
            writes: list[Path] = []
            removals: list[Path] = []
            original_write, original_remove = studio.write_json_atomic, shutil.rmtree

            def protect_writes(path: Path, payload: Any) -> None:
                if substituted and path.absolute().is_relative_to(self.data):
                    writes.append(path.absolute())
                    raise OSError("synthetic root-swap write tripwire")
                original_write(path, payload)

            def protect_removals(path: Any, *args: Any, **kwargs: Any) -> Any:
                candidate = Path(path).absolute()
                if substituted and candidate.is_relative_to(self.data):
                    removals.append(candidate)
                    raise OSError("synthetic root-swap removal tripwire")
                return original_remove(path, *args, **kwargs)

            # Real nonsymlink directories, no open media handles, no real task
            # data. Tripwires prevent damage even on a regressed implementation;
            # ANY attempt still fails the assertions, so they cannot fake a pass.
            with patch.object(studio, "write_json_atomic", side_effect=protect_writes), \
                    patch.object(studio.shutil, "rmtree", side_effect=protect_removals):
                self.root.rename(held)
                try:
                    replacement.rename(self.root)
                    substituted = True
                    try:
                        release.set()
                        outcomes = await asyncio.wait_for(asyncio.gather(worker, return_exceptions=True), WAIT_SECONDS)
                        self.assertEqual(len(outcomes), 1)
                        error = outcomes[0]
                        self.assertIsInstance(error, HTTPException)
                        assert isinstance(error, HTTPException)
                        self.assertEqual(error.status_code, 409)
                        self.assertEqual(error.detail, "task directory changed during studio job")
                        self.assertEqual(writes, [], "The old worker must not write job/audit into a replacement root")
                        self.assertEqual(removals, [], "Root identity must be checked BEFORE attempting workspace removal")
                        self.assertEqual(tree_snapshot(self.root), replacement_tree)
                        self.assertEqual(tree_snapshot(held), running_tree)
                        self.assert_no_owned_leases()
                    finally:
                        try:
                            await self.finish_storage_worker(worker, release)
                        finally:
                            # Park, do not delete, the replacement; restore the
                            # original identity BEFORE the case's source checks.
                            self.root.rename(replacement)
                            substituted = False
                finally:
                    held.rename(self.root)
            self.assertEqual(tree_snapshot(self.root), running_tree)
            self.assertEqual(tree_snapshot(replacement), replacement_tree)
            self.assert_record_and_sources_unchanged()
            self.render_guard.assert_awaited_once()
            self.prepare_guard.assert_not_awaited()
        finally:
            await self.finish_storage_worker(worker, release)