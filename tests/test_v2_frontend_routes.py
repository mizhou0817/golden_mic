"""Socket-free real ASGI/static-file regression tests; no product app/settings.

All fixtures live in a fresh system TEMP directory. The per-test guards reject
application imports, subprocesses, network and reads/writes outside that TEMP
(apart from interpreter dependencies). No lifespan or task pipeline is started.
"""
from __future__ import annotations

import builtins
import io
import mimetypes
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch
from urllib.parse import unquote

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Message, Scope

from backend.frontend_static import FrontendStaticFiles


INDEX = b'<!doctype html><html><main id="root"></main><script src="/assets/app.js"></script></html>'
SCRIPT = b'globalThis.syntheticFrontend = true;'


async def request(app: ASGIApp, target: str, method: str = "GET", *,
                  headers: list[tuple[bytes, bytes]] | None = None,
                  decoded_path: str | None = None) -> tuple[int, dict[str, str], bytes]:
    """Send the raw target without a browser/HTTP client's dot normalization."""
    raw_path, _, query = target.partition("?")
    scope: Scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "root_path": "",
        "path": unquote(raw_path) if decoded_path is None else decoded_path,
        "raw_path": raw_path.encode("ascii"), "query_string": query.encode("ascii"),
        "headers": headers or [], "client": ("127.0.0.1", 12345),
        "server": ("static.invalid", 80),
    }
    messages: list[Message] = []

    async def receive() -> Message:
        raise AssertionError("Static requests must not consume a request body")

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {k.decode("latin1"): v.decode("latin1") for k, v in start["headers"]}
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], response_headers, body


class FrontendRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="gm-static-routes-"))).resolve()
        self.frontend = self.root / "frontend"
        self.frontend.mkdir()
        (self.frontend / "assets").mkdir()
        (self.frontend / "index.html").write_bytes(INDEX)
        (self.frontend / "assets/app.js").write_bytes(SCRIPT)
        (self.frontend / "404.html").write_bytes(b"synthetic static 404")
        self.static = FrontendStaticFiles(directory=self.frontend, html=True)
        self.app = Starlette(routes=[Mount("/", app=self.static, name="frontend")])
        self.lookups: list[str] = []
        self.opened: list[Path] = []
        self.denials: list[str] = []
        original_lookup = self.static.lookup_path

        def lookup(path: str) -> Any:
            self.lookups.append(path)
            return original_lookup(path)

        self.stack.enter_context(patch.object(self.static, "lookup_path", lookup))
        # FileResponse lazily initializes the host MIME database on Linux.
        # Use real stdlib default mappings, but no host files/registry and no
        # wider IO exception. Patch only this fixture's response resolver.
        with patch.object(mimetypes, "inited", True):
            mime = mimetypes.MimeTypes(filenames=())
        self.stack.enter_context(patch("starlette.responses.guess_type", mime.guess_type))
        original_import = builtins.__import__

        def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "dotenv" or name.startswith("dotenv.") or name == "backend" or name.startswith("backend."):
                self.denials.append("application_import")
                raise AssertionError("Application/config/provider import forbidden")
            return original_import(name, *args, **kwargs)

        self.stack.enter_context(patch("builtins.__import__", guarded_import))

        def denied(*_args: Any, **_kwargs: Any) -> Any:
            self.denials.append("network_process_database")
            raise AssertionError("No network, subprocess or database allowed")

        for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket.socket, "bind"),
                            (socket, "create_connection"), (socket, "getaddrinfo"), (subprocess, "Popen"),
                            (os, "system"), (sqlite3, "connect")):
            self.stack.enter_context(patch.object(owner, name, denied))
        dependency_roots = (Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve())

        def wrap_open(original: Any) -> Any:
            def guarded(file: Any, *args: Any, **kwargs: Any) -> Any:
                if not isinstance(file, int):
                    path = Path(os.fsdecode(file)).resolve()
                    mode = args[0] if args else kwargs.get("mode", "r")
                    writing = (bool(mode & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                               if isinstance(mode, int) else any(char in mode for char in "wax+"))
                    owned = path.is_relative_to(self.root)
                    dependency = any(path.is_relative_to(root) for root in dependency_roots)
                    if not owned and (writing or not dependency):
                        self.denials.append("outside_temp_io")
                        raise AssertionError("Only TEMP fixtures and read-only interpreter dependencies allowed")
                    if owned:
                        self.opened.append(path)
                return original(file, *args, **kwargs)
            return guarded

        for target, original in (("builtins.open", builtins.open), ("io.open", io.open), ("os.open", os.open)):
            self.stack.enter_context(patch(target, wrap_open(original)))

    async def asyncTearDown(self) -> None:
        self.assertEqual(self.denials, [])

    async def shell(self, target: str) -> None:
        status, headers, body = await request(self.app, target)
        self.assertEqual(status, 200)
        self.assertEqual(body, INDEX)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(int(headers["content-length"]), len(INDEX))
        self.assertNotIn("location", headers)

    async def not_found(self, target: str, method: str = "GET", **kwargs: Any) -> None:
        status, _, body = await request(self.app, target, method, **kwargs)
        self.assertEqual(status, 404)
        self.assertNotEqual(body, INDEX)

    async def test_root_ordinary_index(self) -> None:
        await self.shell("/")
        await self.shell("/index.html")

    async def test_canonical_task_reload(self) -> None:
        await self.shell("/tasks/" + "a" * 32)

    async def test_task_trailing_slash(self) -> None:
        await self.shell("/tasks/Ab_09-/")

    async def test_safe_unknown_task_loads_ui_then_api_404(self) -> None:
        await self.shell("/tasks/v2-design-invalid-link")
        await self.not_found("/api/tasks/v2-design-invalid-link")

    async def test_task_id_length_and_alphabet(self) -> None:
        for identifier in ("a", "Z", "0", "_", "-", "aZ0_-" * 25 + "abc"):
            with self.subTest(identifier_length=len(identifier)):
                await self.shell("/tasks/" + identifier)

    async def test_sample_exact_route(self) -> None:
        await self.shell("/samples/default")

    async def test_sample_aliases_are_not_ui_routes(self) -> None:
        for target in ("/sample", "/sample/default", "/samples", "/samples/default/", "/samples/other"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_invalid_task_routes(self) -> None:
        for target in ("/tasks", "/tasks/", "/tasks/" + "x" * 129, "/tasks/a.b", "/tasks/a/extra",
                       "/Tasks/a", "/tasks/a%20b", "/tasks/%E4%B8%AD", "/tasks/a//"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_unknown_paths_are_not_catchall(self) -> None:
        for target in ("/unknown", "/history", "/create", "/studio", "/missing.js"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_reserved_api_and_health_are_404_for_all_methods(self) -> None:
        for target in ("/api", "/api/", "/api/missing", "/api/tasks/a", "/health", "/health/missing"):
            for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                with self.subTest(target=target, method=method):
                    await self.not_found(target, method)

    async def test_reserved_names_never_serve_physical_files(self) -> None:
        for prefix in ("api", "health"):
            (self.frontend / prefix).mkdir()
            (self.frontend / prefix / "index.html").write_bytes(INDEX)
            (self.frontend / prefix / "leak.txt").write_bytes(b"not public")
            for suffix in ("", "/", "/leak.txt"):
                with self.subTest(target=prefix + suffix):
                    await self.not_found("/" + prefix + suffix)

    async def test_missing_assets_remain_404(self) -> None:
        for target in ("/assets/missing.js", "/assets/missing.css", "/assets/index.html"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_existing_asset_bytes(self) -> None:
        status, headers, body = await request(self.app, "/assets/app.js")
        self.assertEqual((status, body), (200, SCRIPT))
        self.assertIn("javascript", headers["content-type"])

    async def test_responses_never_initialize_host_mime_database(self) -> None:
        with patch.object(mimetypes, "init", side_effect=AssertionError("No host MIME reads")) as initialize:
            await self.shell("/tasks/a")
            status, headers, body = await request(self.app, "/assets/app.js")
            self.assertEqual((status, body), (200, SCRIPT))
            self.assertIn("javascript", headers["content-type"])
            initialize.assert_not_called()
        self.assertEqual(set(self.opened), {self.frontend / "index.html", self.frontend / "assets/app.js"})

    async def test_head_matches_get_without_body(self) -> None:
        for target in ("/", "/index.html", "/tasks/a", "/tasks/a/", "/samples/default", "/assets/app.js"):
            with self.subTest(target=target):
                get_status, get_headers, _ = await request(self.app, target)
                status, headers, body = await request(self.app, target, "HEAD")
                self.assertEqual((get_status, status, body), (200, 200, b""))
                for name in ("content-type", "content-length", "etag", "last-modified"):
                    self.assertEqual(headers[name], get_headers[name])

    async def test_non_read_static_methods_are_405(self) -> None:
        for target in ("/", "/tasks/a", "/samples/default", "/assets/app.js", "/assets/missing.js"):
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                with self.subTest(target=target, method=method):
                    status, _, body = await request(self.app, target, method)
                    self.assertEqual(status, 405)
                    self.assertNotEqual(body, INDEX)

    async def test_dot_segments_never_normalize_into_shell_or_asset(self) -> None:
        for target in ("/other/../tasks/a", "/other/../", "/tasks/./a", "/assets/../index.html",
                       "/assets/./app.js", "/../index.html", "//tasks/a", "/tasks//a"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_encoded_traversal_and_separators(self) -> None:
        for target in ("/other/%2e%2e/tasks/a", "/assets/%2E%2E/index.html", "/tasks%2fa",
                       "/tasks%5ca", "/tasks/%252e%252e", "/assets/%255capp.js",
                       "/%2f/tasks/a", "/%2e/index.html", "/tasks/a%2f"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_raw_path_cannot_hide_traversal_after_normalization(self) -> None:
        await self.not_found("/other/%2e%2e/tasks/a", decoded_path="/tasks/a")

    async def test_backslash_control_and_windows_invalid_characters(self) -> None:
        for target in ("/assets\\app.js", "/tasks\\a", "/tasks/a%00", "/tasks/a%1f", "/tasks/a%7f",
                       "/tasks/a%3a", "/tasks/a%3f", "/tasks/a%22", "/tasks/a%3c", "/tasks/a%3e",
                       "/tasks/a%7c", "/tasks/a%2a", "/assets/app.js.", "/assets/app.js%20"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_hidden_files_and_directories_never_served(self) -> None:
        (self.frontend / ".secret").write_bytes(b"synthetic private file")
        (self.frontend / ".hidden").mkdir()
        (self.frontend / ".hidden/index.html").write_bytes(INDEX)
        for target in ("/.secret", "/%2esecret", "/.hidden/", "/.hidden/index.html", "/tasks/.secret"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_query_and_credentials_never_change_or_enter_html(self) -> None:
        for target in ("/", "/tasks/a", "/tasks/a/", "/samples/default"):
            with self.subTest(target=target):
                plain = await request(self.app, target)
                private = await request(self.app, target + "?access_token=synthetic-only&next=%3Cscript%3E",
                                        headers=[(b"authorization", b"Bearer synthetic-only"), (b"x-task-token", b"synthetic-only")])
                self.assertEqual(private, plain)
                self.assertEqual(private[2], INDEX)
                self.assertNotIn(b"synthetic-only", private[2])

    async def test_get_reads_only_index_never_task_state_or_pipeline(self) -> None:
        for target in ("/tasks/a", "/tasks/v2-design-invalid-link", "/samples/default"):
            await self.shell(target)
        self.assertEqual(self.lookups, ["index.html"] * 3)
        self.assertTrue(self.opened)
        self.assertEqual(set(self.opened), {self.frontend / "index.html"})
        self.assertFalse((self.root / "tasks").exists())

    async def test_conditional_get_uses_original_index_etag(self) -> None:
        _, headers, _ = await request(self.app, "/")
        status, _, body = await request(self.app, "/tasks/a", headers=[(b"if-none-match", headers["etag"].encode())])
        self.assertEqual((status, body), (304, b""))

    async def test_missing_index_does_not_fake_success(self) -> None:
        (self.frontend / "index.html").unlink()
        for target in ("/", "/tasks/a", "/samples/default"):
            with self.subTest(target=target):
                await self.not_found(target)

    async def test_real_routes_before_mount_keep_precedence(self) -> None:
        async def endpoint(_: Any) -> JSONResponse:
            return JSONResponse({"synthetic": True})

        app = Starlette(routes=[Route("/health/live", endpoint), Route("/api/known", endpoint),
                                Mount("/", app=self.static)])
        for target in ("/health/live", "/api/known"):
            status, headers, body = await request(app, target)
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-type"], "application/json")
            self.assertNotEqual(body, INDEX)


if __name__ == "__main__":
    unittest.main()