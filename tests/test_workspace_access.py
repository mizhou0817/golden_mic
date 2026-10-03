"""Real-main no-login API regressions, each in a credential-free subprocess.

Only the child imports backend.main, with explicit Settings(_env_file=None),
TEMP DATA_DIR and blocked retired modules/transports. No user's app/data/lifespan
is reused. Opaque media tests routing and Range bytes, not codec correctness.
Deployment's two production cases reuse this same isolated real-main harness.
"""
from __future__ import annotations

# Deliberate assertions of server-owned state, never private user settings.
# pyright: reportPrivateUsage=false

import asyncio
import builtins
import importlib
import importlib.abc
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch


RETIRED_MODULES = frozenset({
    "backend.classroom", "backend.classroom_store", "backend.classroom_queue", "backend.cloud_jobs",
})


def run_isolated_main_case(check: unittest.TestCase, case: str) -> None:
    """No inherited credentials/settings; only OS executable/temporary paths."""
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="golden-mic-workspace-api-") as directory:
        root = Path(directory).resolve()
        working = root / "working"
        working.mkdir()
        # A decoy demonstrates that main never consumes a cwd dotenv file.
        (working / ".env").write_text("APP_ENV=development\nDATA_DIR=must-not-be-used\n", encoding="utf-8")
        retained = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
                    "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in retained}
        environment.update({
            "PYTHONPATH": str(project), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1", "APP_ENV": "test", "DATA_DIR": str(root / "tasks"),
            "ASR_CACHE_DIR": str(root / "cache/asr"), "TMPDIR": str(root / "tmp"),
        })
        result = subprocess.run(
            [sys.executable, "-B", "-m", "tests.test_workspace_access", "--child", case],
            cwd=working, env=environment, capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False, timeout=75,
        )
        # Child assertions never print credentials/session/task-token values.
        check.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        check.assertIn(f"workspace_case={case}:passed", result.stdout)
        check.assertFalse((working / "must-not-be-used").exists())


class _RejectRetiredImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname in RETIRED_MODULES:
            raise AssertionError("Real main imported a removed account/cloud module")
        return None


class _MainCases(unittest.TestCase):
    """case_* methods are child-only, not additional unittest-discovered tests."""

    def set_up_case(self, case: str) -> None:
        from backend.config import Settings
        from backend.models import TaskState
        from tests.test_task_operations import isolated_settings, seed_legacy_ledger, synthetic_record

        self.stack = ExitStack()
        self.root = Path(os.environ["DATA_DIR"]).resolve()
        self.root.mkdir()
        self.boundary_calls: list[str] = []
        self.account = self.root.parent / "classroom.sqlite3"
        self.account.write_bytes(b"inert legacy account DB; never open")
        self.account_before = self.account.read_bytes()
        self.production = case.startswith("production_")
        self.real_preflight = case in {"production_http", "production_csrf_and_rate"}
        self.base = "https://testserver" if self.production else "http://127.0.0.1:8765"
        values: dict[str, Any] = {
            "allowed_hosts": "testserver,localhost,127.0.0.1" if self.production else "*",
            "frontend_origins": "https://news.example.test" if self.production else self.base,
        }
        if self.production:
            values.update({
                "app_env": "production", "enable_api_docs": False, "enforce_origin_check": True,
                "anonymous_session_secret": "isolated-production-session-signing-material-not-live-48-bytes",
                "anonymous_session_ttl_seconds": 3600, "task_ttl_hours": 72,
                "min_free_disk_gb": 50, "task_disk_reservation_multiplier": 8,
                "quality_gate_mode": "block", "max_files": 20, "max_concurrent_uploads": 1,
                "max_concurrent_tasks": 1, "max_pending_tasks": 5,
                "shutdown_grace_seconds": 30, "media_command_timeout_seconds": 30,
                "vision_provider": "openai-compatible", "vision_base_url": "https://api.openai.com/v1",
                "vision_api_key": "synthetic-configuration-only", "vision_model": "synthetic-vision",
                "embedding_provider": "openai-compatible", "embed_base_url": "https://api.openai.com/v1",
                "embed_api_key": "synthetic-configuration-only", "embed_model": "synthetic-embedding",
                "llm_provider": "openai-compatible", "llm_base_url": "https://api.openai.com/v1",
                "llm_api_key": "synthetic-configuration-only", "llm_model": "synthetic-llm",
                "kimi_api_key": "synthetic-configuration-only", "volcengine_llm_api_keys": "synthetic-configuration-only",
                "tts_provider": "edge", "sync_sound_enabled": False,
            })
        # Retired PUBLIC_ACCESS_MODE is intentionally unable to disable anonymous
        # production protection. No Settings field or branch may depend on it.
        if self.production:
            os.environ["PUBLIC_ACCESS_MODE"] = "authenticated"
        self.settings = isolated_settings(self.root, **values)
        self.assertNotIn("public_access_mode", Settings.model_fields)
        self.stack.enter_context(patch("backend.config.get_settings", return_value=self.settings))
        self.stack.enter_context(patch(
            "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._read_env_file",
            side_effect=self.forbidden("dotenv"),
        ))
        self.finder = _RejectRetiredImports()
        self.assertFalse(RETIRED_MODULES.intersection(sys.modules))
        sys.meta_path.insert(0, self.finder)
        self.stack.callback(sys.meta_path.remove, self.finder)
        self.block_transports()

        if case == "legacy_busy":
            seed_legacy_ledger(self.root.parent / "cloud_jobs.sqlite3", [("a" * 32, "submitted", 1, "pending-remote")])
        original_connect = sqlite3.connect

        def readonly_connect(database: Any, *args: Any, **kwargs: Any):
            expected = (self.root.parent / "cloud_jobs.sqlite3").as_uri() + "?mode=ro"
            if not isinstance(database, str) or not database.startswith(expected) or kwargs.get("uri") is not True:
                return self.forbidden("database outside read-only legacy ledger")()
            return original_connect(database, *args, **kwargs)

        self.stack.enter_context(patch("sqlite3.connect", side_effect=readonly_connect))
        for module in (builtins, io):
            original_open = module.open

            def safe_open(file: Any, *args: Any, _original=original_open, **kwargs: Any):
                if isinstance(file, (str, bytes, os.PathLike)):
                    name = os.fsdecode(file)
                    if Path(name).name.startswith("classroom.sqlite3"):
                        return self.forbidden("account DB file access")()
                return _original(file, *args, **kwargs)

            self.stack.enter_context(patch.object(module, "open", side_effect=safe_open))

        self.main = importlib.import_module("backend.main")
        self.assertTrue(self.main.settings is self.settings, "Main did not use explicit isolated settings")
        self.assertEqual(self.main.task_manager.settings.data_dir, self.root)
        self.record = synthetic_record(self.main.task_manager, "a" * 32, complete=True)
        self.legacy = synthetic_record(self.main.task_manager, "b" * 32, complete=True, local_only=True)
        self.main.task_manager._tasks.clear()  # Actual app lifespan must restore TEMP states.
        from backend.readiness import ReadinessReport
        if not self.real_preflight:
            self.stack.enter_context(patch.object(self.main, "run_full_preflight", new=AsyncMock(
                return_value=ReadinessReport(ready=True, checks={"isolated_api_fixture": True}, errors=()),
            )))
        self.stack.enter_context(patch("backend.operations.shutil.disk_usage", return_value=shutil._ntuple_diskusage(
            500 * 1024**3, 100 * 1024**3, 400 * 1024**3,
        )))
        for target in ("backend.task_manager.run_pipeline", "backend.task_manager.run_remix_pipeline",
                       "backend.task_manager.run_shot_replacement_pipeline"):
            self.stack.enter_context(patch(target, side_effect=self.forbidden("pipeline/provider execution")))
        from fastapi.testclient import TestClient
        self.client = self.stack.enter_context(TestClient(
            self.main.app, base_url=self.base, client=("127.0.0.1", 50000),
        ))
        restored = self.main.task_manager.get(self.record.task_id)
        legacy = self.main.task_manager.get(self.legacy.task_id)
        assert restored is not None and legacy is not None
        self.token = self.record.access_token
        self.legacy_token = self.legacy.access_token
        self.record, self.legacy = restored, legacy
        self.assertEqual(self.record.status, TaskState.done)
        self.assertTrue(self.legacy.local_only)
        self.assertTrue(self.record.access_token == "")

    def forbidden(self, label: str):
        def reject(*args: Any, **kwargs: Any):
            self.boundary_calls.append(label)
            raise AssertionError(f"Isolated real-main test crossed boundary: {label}")
        return reject

    def block_transports(self) -> None:
        # Windows asyncio initializes a local socketpair. Only its construction
        # may connect; external connections remain forbidden even on loopback.
        original_pair = socket.socketpair
        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        thread = threading.local()

        def pair(*args: Any, **kwargs: Any):
            thread.socketpair = True
            try:
                return original_pair(*args, **kwargs)
            finally:
                thread.socketpair = False

        def connect(sock: socket.socket, *args: Any, **kwargs: Any):
            if getattr(thread, "socketpair", False):
                return original_connect(sock, *args, **kwargs)
            return self.forbidden("socket connect")()

        def connect_ex(sock: socket.socket, *args: Any, **kwargs: Any):
            if getattr(thread, "socketpair", False):
                return original_connect_ex(sock, *args, **kwargs)
            return self.forbidden("socket connect_ex")()

        self.stack.enter_context(patch("socket.socketpair", new=pair))
        self.stack.enter_context(patch("socket.socket.connect", new=connect))
        self.stack.enter_context(patch("socket.socket.connect_ex", new=connect_ex))
        for target in ("socket.create_connection", "httpx.HTTPTransport.handle_request",
                       "httpx.AsyncHTTPTransport.handle_async_request", "aiohttp.ClientSession._request"):
            self.stack.enter_context(patch(target, side_effect=self.forbidden("external transport")))
        original_popen = subprocess.Popen

        def local_capabilities_only(command: Any, *args: Any, **kwargs: Any):
            if self.real_preflight and isinstance(command, (list, tuple)) and list(command) in (
                ["ffmpeg", "-hide_banner", "-encoders"], ["ffmpeg", "-hide_banner", "-filters"],
                ["ffprobe", "-hide_banner", "-version"],
            ):
                return original_popen(command, *args, **kwargs)
            return self.forbidden("unexpected subprocess")()

        self.stack.enter_context(patch("subprocess.Popen", side_effect=local_capabilities_only))

    def tear_down_case(self) -> None:
        try:
            if hasattr(self, "client"):
                async def stop_owned() -> None:
                    for record in list(self.main.task_manager._tasks.values()):
                        if record.background is not None and not record.background.done():
                            record.background.cancel()
                            await asyncio.gather(record.background, return_exceptions=True)
                        await self.main.task_manager._release_disk_reservation(record)
                assert self.client.portal is not None
                self.client.portal.call(stop_owned)
                self.assertEqual(self.main.upload_capacity_guard._reserved_bytes, 0)
                self.assertFalse(RETIRED_MODULES.intersection(sys.modules))
                self.assertEqual(self.boundary_calls, [])
        finally:
            self.stack.close()
        self.assertEqual(self.account.read_bytes(), self.account_before)
        self.assertEqual(list(self.root.parent.glob("classroom.sqlite3*")), [self.account])

    def path(self, suffix: str = "", *, legacy: bool = False) -> str:
        return f"/api/tasks/{self.legacy.task_id if legacy else self.record.task_id}{suffix}"

    def other_client(self, *, base: str | None = None, peer: str = "testclient"):
        from fastapi.testclient import TestClient
        client = TestClient(self.main.app, base_url=base or self.base, client=(peer, 50001))
        self.stack.callback(client.close)
        return client  # No second lifespan or live app/data access.

    def assert_private(self, response: Any) -> None:
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def start_blocker(self):
        entered = threading.Event()

        async def block(record: Any) -> None:
            entered.set()
            await asyncio.Event().wait()

        mock = self.stack.enter_context(patch.object(self.main.task_manager, "_run", side_effect=block))
        return entered, mock

    def case_routes(self) -> None:
        schema = self.client.get("/openapi.json")
        self.assertEqual(schema.status_code, 200)
        paths = schema.json()["paths"]
        route_paths = [getattr(route, "path", "") for route in self.main.app.routes]
        for path in [*paths, *route_paths]:
            self.assertFalse(any(part in path.split("/") for part in (
                "account", "accounts", "login", "setup", "roster", "works", "work", "classroom", "cloud",
            )), "Retired workflow remains mounted")
        for path in ("/api/tasks", "/api/tasks/{task_id}/retry", "/api/tasks/{task_id}/duplicate",
                     "/api/tasks/{task_id}/workbench/context", "/api/tasks/{task_id}/studio/sources"):
            self.assertIn(path, paths)
        for path in ("/api/classroom/session", "/api/classroom/setup", "/api/classroom/login",
                     "/api/classroom/student-login", "/api/classroom/classes/x/roster",
                     "/api/classroom/works", "/api/works", "/api/account", "/api/login", "/api/setup",
                     self.path("/cloud/capabilities"), self.path("/cloud/jobs")):
            self.assertEqual(self.client.get(path).status_code, 404)
        for path in ("/api/classroom/setup", "/api/classroom/login", "/api/classroom/student-login",
                     "/api/classroom/works", self.path("/cloud/jobs")):
            self.assertEqual(self.client.post(path, json={}).status_code, 404)

    def case_api_fallback(self) -> None:
        from tests.test_task_operations import tree_bytes

        before = tree_bytes(self.root)
        paths = (
            "/api/UNKNOWN", "/api/unknown/nested", self.path("/unknown"),
            "/api/classroom/session", "/api/classroom/setup", "/api/classroom/login",
            "/api/classroom/student-login", "/api/classroom/classes/x/roster",
            "/api/classroom/works", "/api/works", "/api/account", "/api/login", "/api/setup",
            self.path("/cloud/capabilities"), self.path("/cloud/jobs"),
        )
        for path in paths:
            for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                with self.subTest(path=path, method=method):
                    # Valid framing reaches routing rather than the body/Origin
                    # guard; OPTIONS is not a CORS preflight interception.
                    response = self.client.request(method, path, headers={"Origin": self.base},
                        content=b"{}" if method in {"POST", "PUT", "PATCH"} else None)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers["content-type"], "application/json")
                    self.assertNotIn("allow", response.headers)
                    self.assert_private(response)
                    if method == "HEAD":
                        self.assertEqual(response.content, b"")
                    else:
                        self.assertEqual(set(response.json()), {"detail"})
        self.assertEqual(tree_bytes(self.root), before)

        # Real registered handlers must win over the catch-all, including both
        # methods on /api/tasks and the mounted task-operation router.
        for path in ("/api/tasks", "/api/config/workspace", self.path(),
                     self.path("/workbench/context"), self.path("/studio/sources")):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.post("/api/tasks", json={}).status_code, 422)
        response = self.client.post(self.path("/duplicate"), json={"expected_revision": 0},
                                    headers={"Origin": self.base})
        self.assertEqual(response.status_code, 201)
        duplicate_id = response.json()["task_id"]
        self.assertEqual(self.client.delete(f"/api/tasks/{duplicate_id}",
                                           headers={"Origin": self.base}).status_code, 204)
        self.assertEqual(tree_bytes(self.record.task_dir),
                         {name.removeprefix(self.record.task_id + "/"): data for name, data in before.items()
                          if name.startswith(self.record.task_id + "/")})

    def case_create(self) -> None:
        entered, runner = self.start_blocker()
        for old_cookie in (False, True):
            headers = {"Cookie": "classroom_session=obsolete-cookie; account_session=obsolete"} if old_cookie else {}
            response = self.client.post("/api/tasks", data={"script": "Standalone synthetic manuscript."},
                                        files={"files": ("clip.mp4", b"opaque synthetic upload", "video/mp4")}, headers=headers)
            self.assertEqual(response.status_code, 202)
            self.assertTrue(entered.wait(3), "Accepted no-login task did not enter _run")
            data = response.json()
            self.assertEqual(set(data), {"task_id", "access_token"})
            self.assertTrue(isinstance(data["access_token"], str) and len(data["access_token"]) >= 32)
            record = self.main.task_manager.get(data["task_id"])
            assert record is not None
            self.assertFalse(record.local_only)
            self.assertFalse(hasattr(record, "classroom_task"))
            self.assertIsNotNone(record.background)
            self.assertFalse(record.background.done())
            self.assertEqual(record.uploads[0].path.read_bytes(), b"opaque synthetic upload")
            state_bytes = (record.task_dir / "task_state.json").read_bytes()
            self.assertFalse(data["access_token"].encode() in state_bytes, "Plain task capability persisted")
            self.assertTrue(self.main.task_manager.authorize(record.task_id, data["access_token"]) is record)
            self.assertGreater(self.main.upload_capacity_guard._reserved_bytes, 0)
            self.assertEqual(self.client.delete(f"/api/tasks/{record.task_id}", headers={"Origin": self.base}).status_code, 204)
            self.assertTrue(record.background.cancelled())
            self.assertFalse(record.task_dir.exists())
            self.assertEqual(self.main.upload_capacity_guard._reserved_bytes, 0)
            entered.clear()
        self.assertEqual(runner.call_count, 2)

    def case_removed_form(self) -> None:
        _, runner = self.start_blocker()
        before = set(self.root.iterdir())
        response = self.client.post("/api/tasks", data={"script": "Synthetic manuscript", "classroom": "{}"},
                                    files={"files": ("clip.mp4", b"opaque", "video/mp4")})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.main.upload_capacity_guard._reserved_bytes, 0)
        runner.assert_not_called()

    def case_local_authority(self) -> None:
        accepted = [
            ("http://127.0.0.1:8765", "127.0.0.1", {}),
            ("http://localhost:8765", "127.0.0.1", {"Sec-Fetch-Site": "same-origin"}),
            ("http://127.0.0.2:8765", "127.0.0.3", {"Sec-Fetch-Site": "none"}),
            (self.base, "127.0.0.1", {"Origin": self.base}),
        ]
        for base, peer, headers in accepted:
            client = self.other_client(base=base, peer=peer)
            config = client.get("/api/config/workspace", headers=headers)
            self.assertEqual(config.status_code, 200)
            self.assertTrue(config.json()["local_history"])
            self.assertEqual(client.get("/api/tasks", headers=headers).status_code, 200)
        # Installed Starlette TestClient splits bracketed IPv6 authority at the
        # first colon before reaching ASGI. Exercise the real app through the
        # ASGI transport instead, without changing the product or omitting IPv6.
        async def ipv6() -> None:
            import httpx
            async with httpx.AsyncClient(transport=httpx.ASGITransport(
                app=self.main.app, client=("::1", 50002),
            ), base_url="http://[::1]:8765", trust_env=False) as client:
                config = await client.get("/api/config/workspace", headers={"Origin": "http://[::1]:8765"})
                self.assertTrue(config.json()["local_history"])
                self.assertEqual((await client.get("/api/tasks")).status_code, 200)
        assert self.client.portal is not None
        self.client.portal.call(ipv6)
        rejected = [
            (self.base, "testclient", {}), (self.base, "192.0.2.2", {}),
            (self.base, "localhost", {}), ("http://external.example.test", "127.0.0.1", {}),
            *[(self.base, "127.0.0.1", {key: value}) for key, value in (
                ("Forwarded", "for=127.0.0.1"), ("X-Forwarded-For", "127.0.0.1"),
                ("X-Forwarded-Host", "localhost"), ("X-Forwarded-Proto", "http"),
                ("X-Forwarded-Arbitrary", ""), ("X-Real-IP", "127.0.0.1"),
                ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Site", "same-site"),
                ("Origin", "http://evil.example.test"), ("Origin", "null"),
                ("Origin", "http://127.0.0.1"), ("Origin", self.base + "/"),
            )],
            (self.base, "127.0.0.1", [("Origin", self.base), ("Origin", self.base)]),
            (self.base, "127.0.0.1", [("Host", "127.0.0.1:8765"), ("Host", "127.0.0.1:8765")]),
            (self.base, "127.0.0.1", [("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "none")]),
        ]
        for base, peer, headers in rejected:
            client = self.other_client(base=base, peer=peer)
            self.assertFalse(client.get("/api/config/workspace", headers=headers).json()["local_history"])
            response = client.get("/api/tasks", headers=headers)
            self.assertEqual(response.status_code, 404)
            self.assert_private(response)

    def case_remote_tokens(self) -> None:
        remote = self.other_client()  # Default TestClient peer has NO local authority.
        for suffix in ("", "/report", "/video", "/workbench/context", "/workbench/versions/0/report",
                       "/workbench/versions/0/video", "/studio/project", "/studio/sources", "/studio/sources/final"):
            path = self.path(suffix)
            for headers in ({}, {"X-Task-Token": "bad"}, {"X-Authenticated-User": "operator"},
                            {"Cookie": "classroom_session=obsolete-cookie"}):
                self.assertEqual(remote.get(path, headers=headers).status_code, 404)
            response = remote.get(path, headers={"X-Task-Token": self.token})
            self.assertEqual(response.status_code, 200)
            self.assert_private(response)
            self.assertEqual(remote.get(path, headers={"X-Task-Token": self.token,
                "Cookie": "classroom_session=obsolete-cookie"}).status_code, 200)
            self.assertEqual(remote.get(self.path(suffix, legacy=True),
                                        headers={"X-Task-Token": self.legacy_token}).status_code, 404)
        ranged = remote.get(self.path("/video"), params={"token": self.token}, headers={"Range": "bytes=2-9"})
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, (self.record.task_dir / "final.mp4").read_bytes()[2:10])
        self.assertEqual(remote.get("/api/tasks", headers={"X-Task-Token": self.token}).status_code, 404)

    def case_local_reads(self) -> None:
        for legacy in (False, True):
            for suffix in ("", "/report", "/workbench/context", "/workbench/versions/0/report", "/studio/sources"):
                response = self.client.get(self.path(suffix, legacy=legacy))
                self.assertEqual(response.status_code, 200)
                self.assert_private(response)
                self.assertFalse(str(self.root) in response.text, "Core API leaked absolute task root")
            record = self.legacy if legacy else self.record
            data = (record.task_dir / "final.mp4").read_bytes()
            for suffix in ("/video", "/workbench/versions/0/video", "/studio/sources/final", "/studio/preview/final"):
                response = self.client.get(self.path(suffix, legacy=legacy), headers={"Range": "bytes=0-7"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, data[:8])
                self.assertEqual(response.headers["content-range"], f"bytes 0-7/{len(data)}")
                self.assert_private(response)
        context = self.client.get(self.path("/workbench/context")).json()
        self.assertEqual(len(context["timings"]), 2)
        self.assertTrue(all("audio_path" not in row for row in context["timings"]))
        self.assertTrue(all("norm_path" not in row and "thumb_path" not in row for row in context["shots"]))
        self.assertEqual(self.client.get(self.path("/video"), headers={"Range": "bytes=999999-"}).status_code, 416)

    def case_conflicting_tokens(self) -> None:
        for suffix in ("", "/report", "/video", "/workbench/context", "/studio/sources/final"):
            for headers, params in (
                ({"X-Task-Token": ""}, {}), ({"X-Task-Token": "bad"}, {}), ({}, {"token": ""}),
                ({"X-Task-Token": self.token}, {"token": "bad"}),
                ({"X-Task-Token": "bad"}, {"token": self.token}),
                ([("X-Task-Token", self.token), ("X-Task-Token", "bad")], {}),
                ({}, [("token", self.token), ("token", "bad")]),
            ):
                self.assertEqual(self.client.get(self.path(suffix), headers=headers, params=params).status_code, 404)
            self.assertEqual(self.client.get(self.path(suffix), headers={"X-Task-Token": self.token},
                                             params={"token": self.token}).status_code, 200)
        from backend.studio_render import Project
        response = self.client.post(self.path("/studio/project"), json={"expected_revision": 0, "project": Project().model_dump()},
                                    headers={"Origin": self.base, "X-Task-Token": "bad"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse((self.record.task_dir / "studio/state.json").exists())

    def case_local_writes(self) -> None:
        from backend.studio_render import Project
        payload = {"expected_revision": 0, "project": Project().model_dump()}
        for headers in ({}, {"Origin": "null"}, {"Origin": "http://evil.example.test"},
                        {"Origin": "http://127.0.0.1"}, {"Origin": self.base + "/"},
                        {"Origin": self.base, "X-Forwarded-For": "127.0.0.1"},
                        [("Origin", self.base), ("Origin", self.base)]):
            self.assertEqual(self.client.post(self.path("/studio/project"), json=payload, headers=headers).status_code, 404)
            self.assertFalse((self.record.task_dir / "studio/state.json").exists())
        response = self.client.post(self.path("/studio/project"), json=payload,
                                    headers={"Origin": self.base, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], 1)
        self.assertEqual(response.json()["project"], payload["project"])
        self.assertEqual(self.client.post(self.path("/studio/project"), json=payload,
                                         headers={"Origin": self.base}).status_code, 409)

    def case_local_origin_enforced(self) -> None:
        from backend.studio_render import Project
        self.settings.enforce_origin_check = True
        self.settings.frontend_origins = "http://configured-other-origin.test"
        payload = {"expected_revision": 0, "project": Project().model_dump()}
        # A configured CORS origin is not implicit LOCAL authorization.
        denied = self.client.post(self.path("/studio/project"), json=payload,
                                  headers={"Origin": self.settings.frontend_origins})
        self.assertEqual(denied.status_code, 404)
        self.assertFalse((self.record.task_dir / "studio/state.json").exists())
        self.assertEqual(self.client.post(self.path("/studio/project"), json=payload).status_code, 403)
        accepted = self.client.post(self.path("/studio/project"), json=payload, headers={"Origin": self.base})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["revision"], 1)

    def case_history(self) -> None:
        private = "fixture-private-sentinel"
        self.record.script = f"Public title\n{private} {self.root}"
        self.record.error_message = private
        self.record.student_pin = private
        self.record.account_metadata = {"private_path": str(self.root)}
        self.record.mode = "mixed"
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"tasks", "total"})
        self.assertEqual(body["total"], 2)
        for row in body["tasks"]:
            self.assertEqual(set(row), {"task_id", "title", "mode", "status", "revision", "created_at", "updated_at"})
            self.assertEqual(row["mode"], "mixed" if row["task_id"] == self.record.task_id else "voiceover")
        for value in (private, self.token, self.legacy_token, self.record.access_token_hash, str(self.root)):
            self.assertFalse(value in response.text, "Local history leaked private metadata")
        first = self.client.get("/api/tasks?offset=0&limit=1").json()
        second = self.client.get("/api/tasks?offset=1&limit=1").json()
        self.assertEqual(first["total"], 2)
        self.assertEqual(second["total"], 2)
        self.assertEqual(first["tasks"] + second["tasks"], body["tasks"])
        self.assertEqual(self.client.get("/api/tasks?offset=9").json()["tasks"], [])
        for query in ("offset=-1", "limit=0", "limit=201"):
            self.assertEqual(self.client.get("/api/tasks?" + query).status_code, 422)

    def case_delete_failure(self) -> None:
        with patch.object(self.main.task_manager, "_remove_task_directory", side_effect=OSError("private disk diagnostic")):
            response = self.client.delete(self.path(), headers={"Origin": self.base})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "task_artifact_cleanup_failed")
        self.assertNotIn("private disk diagnostic", response.text)
        self.assertTrue(self.record.task_dir.is_dir())
        status = self.client.get(self.path())
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "cancelled")
        self.assertEqual(self.client.delete(self.path(), headers={"Origin": self.base}).status_code, 204)
        self.assertFalse(self.record.task_dir.exists())
        self.assertEqual(self.client.get(self.path()).status_code, 404)

    def case_operations(self) -> None:
        from tests.test_task_operations import synthetic_record
        source_bytes = (self.record.task_dir / "final.mp4").read_bytes()
        headers = {"Origin": self.base}
        stale = self.client.post(self.path("/duplicate"), json={"expected_revision": 1}, headers=headers)
        self.assertEqual(stale.status_code, 409)
        response = self.client.post(self.path("/duplicate"), json={"expected_revision": 0}, headers=headers)
        self.assertEqual(response.status_code, 201)
        clone = response.json()
        self.assertTrue(isinstance(clone["access_token"], str) and bool(clone["access_token"]))
        remote = self.other_client()
        self.assertEqual(remote.get(f"/api/tasks/{clone['task_id']}/video",
                                   headers={"X-Task-Token": clone["access_token"]}).content, source_bytes)
        failed = synthetic_record(self.main.task_manager)
        entered, runner = self.start_blocker()
        retry = self.client.post(f"/api/tasks/{failed.task_id}/retry", json={"expected_revision": 0}, headers=headers)
        self.assertEqual(retry.status_code, 202)
        self.assertTrue(entered.wait(3))
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(self.client.post(f"/api/tasks/{failed.task_id}/retry",
                                         json={"expected_revision": 0}, headers=headers).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/tasks/{failed.task_id}", headers=headers).status_code, 204)

    def case_legacy_explicit_retry(self) -> None:
        from tests.test_task_operations import synthetic_record
        from backend.storage import write_json_atomic
        old = synthetic_record(self.main.task_manager)
        path = old.task_dir / "task_state.json"
        state = json.loads(path.read_bytes())
        state.update({"status": "queued", "classroom_task": True, "queue_hold": True})
        write_json_atomic(path, state)
        self.main.task_manager._tasks.pop(old.task_id)
        self.assertEqual(self.main.task_manager.restore_tasks(), 1)
        held = self.main.task_manager.get(old.task_id)
        assert held is not None
        self.assertTrue(held.local_only)
        self.assertEqual(held.status.value, "failed")
        self.assertIsNone(held.background)
        entered, runner = self.start_blocker()
        path = f"/api/tasks/{held.task_id}/retry"
        remote = self.other_client()
        self.assertEqual(remote.post(path, json={"expected_revision": 0},
                                     headers={"X-Task-Token": old.access_token}).status_code, 404)
        self.assertEqual(self.client.post(path, json={"expected_revision": 0}).status_code, 404)
        runner.assert_not_called()
        response = self.client.post(path, json={"expected_revision": 0}, headers={"Origin": self.base})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(entered.wait(3))
        self.assertEqual(runner.call_count, 1)
        self.assertTrue(held.local_only)
        self.assertEqual(held.access_token, "")
        self.assertNotIn("access_token", response.json())
        self.assertEqual(remote.get(f"/api/tasks/{held.task_id}", headers={"X-Task-Token": old.access_token}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/tasks/{held.task_id}", headers={"Origin": self.base}).status_code, 204)

    def case_legacy_busy(self) -> None:
        from tests.test_task_operations import tree_bytes
        ledger_before = {p.name: p.read_bytes() for p in self.root.parent.glob("cloud_jobs.sqlite3*")}
        before = tree_bytes(self.record.task_dir)
        self.assertEqual(self.client.get(self.path()).status_code, 200)
        self.assertEqual(self.client.get(self.path("/video")).status_code, 200)
        for suffix, body in (("/retry", {"expected_revision": 0}), ("/duplicate", {"expected_revision": 0}),
                             ("/studio/project", {"expected_revision": 0, "project": {}}),
                             ("/workbench/edit", {"expected_revision": 0, "keep_sentence_ids": [0]}),
                             ("/remix", {"keep_sentence_ids": [0]})):
            self.assertEqual(self.client.post(self.path(suffix), json=body, headers={"Origin": self.base}).status_code, 409)
        self.assertEqual(self.client.delete(self.path(), headers={"Origin": self.base}).status_code, 409)
        self.assertEqual(tree_bytes(self.record.task_dir), before)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.parent.glob("cloud_jobs.sqlite3*")}, ledger_before)

    def case_production_authority(self) -> None:
        # Both direct local requests and forwarded/spoofed identities lack local
        # privilege in production. Read capabilities remain session-independent.
        for headers in ({}, {"X-Authenticated-User": "operator"}, {"X-Forwarded-For": "127.0.0.1"}):
            self.assertFalse(self.client.get("/api/config/workspace", headers=headers).json()["local_history"])
            self.assertEqual(self.client.get("/api/tasks", headers=headers).status_code, 404)
            self.assertEqual(self.client.get(self.path(), headers=headers).status_code, 404)
        self.assertEqual(self.client.get(self.path(), headers={"X-Task-Token": self.token}).status_code, 200)
        self.assertEqual(self.client.get(self.path(legacy=True), headers={"X-Task-Token": self.legacy_token}).status_code, 404)
        self.assertEqual(self.client.delete(self.path(), headers={
            "Origin": "https://news.example.test", "X-Task-Token": self.token, "X-Authenticated-User": "operator",
        }).status_code, 403)

    def case_production_http(self) -> None:
        live = self.client.get("/health/live")
        self.assertEqual(live.status_code, 200)
        self.assertTrue(live.headers["strict-transport-security"].startswith("max-age="))
        self.assertTrue(live.headers["content-security-policy"])
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        for headers in ({}, {"X-Authenticated-User": "operator"}):
            self.assertEqual(self.client.get(self.path(), headers=headers).status_code, 404)
            self.assertEqual(self.client.get("/api/tasks/missing", headers=headers).status_code, 404)
            self.assertEqual(self.client.post("/api/tasks", headers=headers).status_code, 403)
        self.assertEqual(self.client.get("/health/live", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(self.other_client(peer="192.0.2.1").post("/api/admin/drain").status_code, 404)
        self.assertEqual(self.client.post("/api/admin/drain").status_code, 200)
        self.assertEqual(self.client.get("/health/ready").status_code, 503)
        self.assertEqual(self.client.delete("/api/admin/drain").status_code, 200)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.case_production_authority()

    def case_production_create(self) -> None:
        session = self.client.get("/api/session").json()
        headers = {"Origin": "https://news.example.test", "X-CSRF-Token": session["csrf_token"]}
        entered, runner = self.start_blocker()
        response = self.client.post("/api/tasks", data={"script": "Standalone production API fixture."},
                                    files={"files": ("clip.mp4", b"opaque synthetic upload", "video/mp4")}, headers=headers)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(entered.wait(3))
        self.assertEqual(runner.call_count, 1)
        data = response.json()
        self.assertTrue(isinstance(data["access_token"], str) and len(data["access_token"]) >= 32)
        path = f"/api/tasks/{data['task_id']}"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get(path, headers={"X-Task-Token": data["access_token"]}).status_code, 200)
        for invalid in ({"Origin": headers["Origin"], "X-Task-Token": data["access_token"]},
                        {"X-CSRF-Token": headers["X-CSRF-Token"], "X-Task-Token": data["access_token"]}):
            self.assertEqual(self.client.delete(path, headers=invalid).status_code, 403)
        self.assertEqual(self.client.delete(path, headers={**headers, "X-Task-Token": data["access_token"]}).status_code, 204)
        self.assertEqual(self.main.upload_capacity_guard._reserved_bytes, 0)

    def case_production_csrf_and_rate(self) -> None:
        session = self.client.get("/api/session")
        self.assertEqual(session.status_code, 200)
        body = session.json()
        self.assertEqual(body["access_mode"], "anonymous")
        self.assertTrue(isinstance(body["csrf_token"], str) and len(body["csrf_token"]) >= 32)
        cookie = session.headers.get("set-cookie", "")
        self.assertTrue(all(piece in cookie for piece in (
            "__Host-golden_mic_session=", "HttpOnly", "Secure", "SameSite=strict", "Path=/",
        )), "Missing anonymous cookie protection")
        self.assert_private(session)
        for headers in (
            {"Origin": "https://news.example.test"},
            {"Origin": "https://news.example.test", "X-Authenticated-User": "operator"},
            {"Origin": "https://news.example.test", "X-CSRF-Token": "invalid"},
            {"Origin": "https://evil.example.test", "X-CSRF-Token": body["csrf_token"]},
            {"X-CSRF-Token": body["csrf_token"]},
        ):
            self.assertEqual(self.client.post("/api/tasks", headers=headers).status_code, 403)
        valid = {"Origin": "https://news.example.test", "X-CSRF-Token": body["csrf_token"]}
        stranger = self.other_client(peer="192.0.2.1")
        self.assertEqual(stranger.post("/api/tasks", headers=valid).status_code, 403)
        # Correct session/CSRF does not itself grant access to any task.
        self.assertEqual(self.client.delete(self.path(), headers=valid).status_code, 404)
        for expected in (413, 413, 429):
            response = self.client.post("/api/tasks", headers={**valid, "Content-Length": "6000000000"})
            self.assertEqual(response.status_code, expected)
        # Quota identity ignores client-spoofed authenticated usernames.
        self.assertEqual(self.client.post("/api/tasks", headers={
            **valid, "Content-Length": "6000000000", "X-Authenticated-User": "another-operator",
        }).status_code, 429)


class WorkspaceAccessTests(unittest.TestCase):
    def test_removed_routes_and_core_paths_on_real_main(self) -> None:
        run_isolated_main_case(self, "routes")

    def test_unknown_and_retired_api_methods_are_404_without_shadowing_core_routes(self) -> None:
        run_isolated_main_case(self, "api_fallback")

    def test_no_metadata_create_starts_immediately_old_cookies_ignored(self) -> None:
        run_isolated_main_case(self, "create")

    def test_removed_classroom_form_never_creates_or_starts_task(self) -> None:
        run_isolated_main_case(self, "removed_form")

    def test_direct_loopback_host_client_fetch_site_origin_and_forwarding(self) -> None:
        run_isolated_main_case(self, "local_authority")

    def test_remote_tokens_enforced_legacy_local_only_denied(self) -> None:
        run_isolated_main_case(self, "remote_tokens")

    def test_local_core_report_video_range_workbench_and_studio_reads(self) -> None:
        run_isolated_main_case(self, "local_reads")

    def test_supplied_invalid_empty_or_conflicting_tokens_never_fall_back(self) -> None:
        run_isolated_main_case(self, "conflicting_tokens")

    def test_implicit_local_writes_require_exact_single_origin(self) -> None:
        run_isolated_main_case(self, "local_writes")

    def test_configured_cors_origin_is_not_implicit_local_authority(self) -> None:
        run_isolated_main_case(self, "local_origin_enforced")

    def test_local_history_is_paginated_and_credential_free(self) -> None:
        run_isolated_main_case(self, "history")

    def test_delete_reports_disk_failure_then_supports_explicit_retry(self) -> None:
        run_isolated_main_case(self, "delete_failure")

    def test_real_main_retry_duplicate_routes_and_stale_guards(self) -> None:
        run_isolated_main_case(self, "operations")

    def test_historical_hold_requires_explicit_local_retry_without_token_minting(self) -> None:
        run_isolated_main_case(self, "legacy_explicit_retry")

    def test_unresolved_legacy_cloud_work_blocks_writes_without_ledger_mutation(self) -> None:
        run_isolated_main_case(self, "legacy_busy")

    def test_production_has_no_implicit_local_or_spoofed_identity_grant(self) -> None:
        run_isolated_main_case(self, "production_authority")

    def test_production_create_and_delete_require_csrf_and_task_capability(self) -> None:
        run_isolated_main_case(self, "production_create")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        name = sys.argv[2]
        checks = _MainCases()
        try:
            checks.set_up_case(name)
            getattr(checks, "case_" + name)()
        finally:
            if hasattr(checks, "stack"):
                checks.tear_down_case()
        print(f"workspace_case={name}:passed")
    else:
        unittest.main()