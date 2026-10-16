"""Run unmodified unittest discovery with TEMP storage and no external providers.

No product imports occur before sanitization, dotenv suppression and guards.
Only this process's fake HTTP server and Windows socketpair initialization may
connect. MockTransport and ASGITransport are untouched. Python children are
limited to the existing isolated real-main harness and the media timeout test;
the former supplies explicit Settings(_env_file=None) and its own guards.

This is Python-level confinement, not an OS sandbox for native media codecs.
Run once; a native crash must be investigated, not automatically retried.
"""
from __future__ import annotations

import faulthandler
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from collections import Counter
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from http.server import HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit


PROJECT = Path(__file__).resolve().parents[1]
OS_KEYS = frozenset({
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "LOCALAPPDATA",
    "APPDATA", "USERPROFILE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
})
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class SafetyViolation(RuntimeError):
    """Messages deliberately exclude request contents and environment values."""


def normalized(path: Any) -> str:
    value = os.fsdecode(path)
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def synthetic_environment(root: Path) -> tuple[dict[str, str], Path]:
    from tests.validation_environment import media_tools

    environment = {key: value for key, value in os.environ.items() if key.upper() in OS_KEYS}
    ffmpeg, _ = media_tools(environment)
    inherited_path = next((value for key, value in environment.items() if key.upper() == "PATH"), "")
    environment = {key.upper(): value for key, value in environment.items()}
    environment.update({
        "PATH": str(ffmpeg.parent) + os.pathsep + inherited_path,
        "APP_ENV": "test", "DATA_DIR": str(root / "tasks"),
        "ASR_CACHE_DIR": str(root / "cache/asr"),
        "TEMP": str(root / "tmp"), "TMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
        "PYTHONFAULTHANDLER": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "XDG_CACHE_HOME": str(root / "cache"), "HF_HOME": str(root / "cache/hf"),
        "MPLCONFIGDIR": str(root / "cache/matplotlib"), "NUMBA_CACHE_DIR": str(root / "cache/numba"),
    })
    for key in (
        "VISION_BASE_URL", "VOLCENGINE_VISION_BASE_URL", "KIMI_BASE_URL", "EMBED_BASE_URL",
        "VOLCENGINE_EMBEDDING_BASE_URL", "LLM_BASE_URL", "VOLCENGINE_LLM_BASE_URL",
        "TTS_BASE_URL", "VOLCENGINE_TTS_BASE_URL", "VOLCENGINE_GEN_BASE_URL",
    ):
        environment[key] = "https://safe.test/v1"
    environment["VOLCENGINE_ASR_BASE_URL"] = "wss://safe.test/asr"
    return environment, ffmpeg


class Guards:
    def __init__(self, root: Path, evidence: Path, ffmpeg: Path) -> None:
        self.root, self.evidence, self.ffmpeg = root, evidence, ffmpeg
        self.allowed_write = tuple(normalized(path) for path in (root, evidence))
        self.private = tuple(normalized(PROJECT / name) for name in (
            ".env", "data", "eval_sample", "canary_test/artifacts",
        ))
        self.ports: set[int] = set()
        self.local = threading.local()
        self.counts: Counter[str] = Counter()
        self.stage = "self_check"
        self.file_guards_installed = False

    def reject(self, reason: str) -> Any:
        self.counts[self.stage + ":denied:" + reason] += 1
        raise SafetyViolation("Validation boundary: " + reason)

    def fd_path(self, value: Any, dir_fd: int | None = None) -> Any:
        """Resolve Linux *at audit paths against a proven owned directory FD."""
        if not isinstance(value, (str, bytes, os.PathLike)):
            return value
        raw = os.fsdecode(value)
        # Absolute paths ignore dir_fd in the actual OS call. Never let an
        # owned descriptor confer authority over an absolute external path.
        if os.path.isabs(raw) or dir_fd is None or dir_fd == -1:
            return raw
        if sys.platform not in {"linux", "darwin"} or type(dir_fd) is not int or dir_fd < 0:
            self.reject("unsupported directory descriptor")
        if any(part in {".", ".."} for part in raw.split("/")):
            self.reject("directory descriptor traversal")
        try:
            opened = os.fstat(dir_fd)
            if sys.platform == "darwin":
                import fcntl  # F_GETPATH is the macOS counterpart of /proc/self/fd
                base = os.fsdecode(fcntl.fcntl(dir_fd, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0])
            else:
                base = os.readlink(f"/proc/self/fd/{dir_fd}")
            if (not stat.S_ISDIR(opened.st_mode) or not os.path.isabs(base)
                    or base.endswith(" (deleted)") or Path(base).resolve() != Path(base).absolute()):
                self.reject("unsafe directory descriptor")
            current = os.stat(base, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or not os.path.samestat(opened, current):
                self.reject("directory descriptor identity changed")
        except (OSError, ValueError):
            self.reject("unverifiable directory descriptor")
        if not any(under(normalized(base), prefix) for prefix in self.allowed_write):
            self.reject("directory descriptor outside owned TEMP/evidence")
        candidate = Path(base) / raw
        if candidate.parent.resolve() != candidate.parent.absolute():
            self.reject("directory descriptor path indirection")
        self.counts["owned_dir_fd_paths"] += 1
        return candidate

    def path(self, value: Any, *, write: bool = False, dir_fd: int | None = None) -> None:
        if not isinstance(value, (str, bytes, os.PathLike)):
            return
        path = normalized(self.fd_path(value, dir_fd))
        if path == normalized(os.devnull):
            return
        if any(under(path, prefix) for prefix in self.allowed_write):
            return
        if any(under(path, prefix) for prefix in self.private):
            self.reject("real dotenv/data/historical evidence access")
        if write:
            self.reject("write outside owned TEMP/evidence")

    def address(self, address: Any, *, socketpair: bool = False) -> None:
        if not isinstance(address, tuple) or len(address) < 2:
            self.reject("non-loopback socket")
        host = os.fsdecode(address[0]).lower()
        if host not in LOOPBACK:
            self.reject("external socket")
        if socketpair and getattr(self.local, "socketpair", False):
            self.counts["socketpair_connections"] += 1
            return
        if address[1] not in self.ports:
            self.reject("unowned loopback listener")

    def url(self, url: Any) -> None:
        parsed = urlsplit(str(url))
        if parsed.scheme not in {"http", "https"}:
            self.reject("non-HTTP transport")
        self.address((parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)))

    def audit(self, event: str, arguments: tuple[Any, ...]) -> None:
        if event == "open":
            flags = arguments[2] if len(arguments) > 2 and isinstance(arguments[2], int) else 0
            context = getattr(self.local, "dir_fd_open", None)
            dir_fd = None
            if context is not None and arguments[1] is None:
                if arguments[0] != context[0] or flags != context[1]:
                    self.reject("unapproved directory-relative open audit")
                dir_fd = context[2]
            self.path(arguments[0], dir_fd=dir_fd,
                      write=bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        elif event in {"os.remove", "os.rmdir", "shutil.rmtree"}:
            self.path(arguments[0], write=True, dir_fd=arguments[1])
        elif event in {"os.mkdir", "os.chmod", "os.utime"}:
            self.path(arguments[0], write=True, dir_fd=arguments[-1])
        elif event in {"os.rename", "os.link"}:
            self.path(arguments[0], write=True, dir_fd=arguments[2])
            self.path(arguments[1], write=True, dir_fd=arguments[3])
        elif event == "os.symlink":
            destination = self.fd_path(arguments[1], arguments[2])
            self.path(destination, write=True)
            # Relative link targets are relative to the link's parent, not cwd.
            # Keep rejecting links to external/private targets as before.
            self.path(Path(destination).parent / os.fsdecode(arguments[0]), write=True)
        elif event in {"os.listdir", "os.scandir", "sqlite3.connect"}:
            value = arguments[0]
            if event == "sqlite3.connect" and isinstance(value, str) and value.startswith("file:"):
                from urllib.request import url2pathname
                value = url2pathname(urlsplit(value).path)
            if value != ":memory:":
                self.path(value, write=event == "sqlite3.connect")
        elif event == "socket.connect":
            self.address(arguments[1], socketpair=True)
        elif event in {"socket.sendto", "socket.sendmsg"}:
            self.address(arguments[-1])
        elif event == "socket.bind":
            address = arguments[1]
            if not isinstance(address, tuple) or os.fsdecode(address[0]).lower() not in LOOPBACK:
                self.reject("non-loopback bind")
        elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}:
            host = arguments[0]
            if not isinstance(host, (str, bytes)) or os.fsdecode(host).lower() not in LOOPBACK:
                self.reject("external DNS")
        elif event in {"os.system", "os.startfile", "os.exec", "os.posix_spawn"}:
            self.reject("unreviewed process launch")

    def install_file_guards(self, stack: ExitStack) -> None:
        if self.file_guards_installed:
            return
        original_open = os.open

        def opened(file, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is None:
                return original_open(file, flags, mode)
            self.path(file, dir_fd=dir_fd,
                      write=bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
            previous = getattr(self.local, "dir_fd_open", None)
            # CPython's open audit omits dir_fd and adds O_CLOEXEC on Linux.
            # Bind only this exact native call; preserve its actual FD-relative
            # operation rather than replacing it with a race-prone absolute open.
            self.local.dir_fd_open = (os.fspath(file), flags | getattr(os, "O_CLOEXEC", 0), dir_fd)
            try:
                return original_open(file, flags, mode, dir_fd=dir_fd)
            finally:
                self.local.dir_fd_open = previous

        stack.enter_context(patch.object(os, "open", new=opened))
        self.file_guards_installed = True
        stack.callback(setattr, self, "file_guards_installed", False)

    def install(self, stack: ExitStack) -> None:
        self.install_file_guards(stack)
        # Import-time platform probing is not an application/media assertion.
        # Windows platform probing shells out to ver; POSIX uses uname instead.
        if sys.platform == "win32":
            stack.enter_context(patch("platform._syscmd_ver", return_value=("", "", "")))
        sys.addaudithook(self.audit)
        from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource
        stack.enter_context(patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}))
        stack.enter_context(patch.object(DotEnvSettingsSource, "_read_env_file", return_value={}))
        stack.enter_context(patch("dotenv.dotenv_values", return_value={}))
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        # Readiness also scans cwd/.env directly. Model an absent real dotenv;
        # keep its actual environment-key validation and TEMP decoy tests intact.
        original_is_file = Path.is_file
        dotenv_path = normalized(PROJECT / ".env")

        def is_file(path: Path) -> bool:
            if normalized(path) == dotenv_path:
                self.counts["real_dotenv_presence_suppressed"] += 1
                return False
            return original_is_file(path)

        stack.enter_context(patch.object(Path, "is_file", new=is_file))

        original_pair = socket.socketpair

        def pair(*args: Any, **kwargs: Any) -> Any:
            previous = getattr(self.local, "socketpair", False)
            self.local.socketpair = True
            try:
                return original_pair(*args, **kwargs)
            finally:
                self.local.socketpair = previous

        stack.enter_context(patch.object(socket, "socketpair", new=pair))
        original_bind, original_close = HTTPServer.server_bind, HTTPServer.server_close

        def bind(server: Any) -> None:
            original_bind(server)
            self.ports.add(server.server_port)

        def close(server: Any) -> None:
            try:
                original_close(server)
            finally:
                self.ports.discard(server.server_port)

        stack.enter_context(patch.object(HTTPServer, "server_bind", new=bind))
        stack.enter_context(patch.object(HTTPServer, "server_close", new=close))

        import httpx
        import aiohttp
        import websockets.asyncio.client
        sync_request = httpx.HTTPTransport.handle_request
        async_request = httpx.AsyncHTTPTransport.handle_async_request
        aio_request = aiohttp.ClientSession._request

        def sync_send(transport: Any, request: Any) -> Any:
            self.url(request.url)
            self.counts["owned_loopback_http"] += 1
            return sync_request(transport, request)

        async def async_send(transport: Any, request: Any) -> Any:
            self.url(request.url)
            self.counts["owned_loopback_http"] += 1
            return await async_request(transport, request)

        async def aio_send(session: Any, method: Any, url: Any, **kwargs: Any) -> Any:
            self.url(url)
            self.counts["owned_loopback_aiohttp"] += 1
            return await aio_request(session, method, url, **kwargs)

        def no_websocket(*args: Any, **kwargs: Any) -> Any:
            return self.reject("real websocket")

        stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", new=sync_send))
        stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", new=async_send))
        stack.enter_context(patch.object(aiohttp.ClientSession, "_request", new=aio_send))
        stack.enter_context(patch.object(aiohttp.ClientSession, "_ws_connect", new=no_websocket))
        stack.enter_context(patch.object(websockets.asyncio.client, "connect", new=no_websocket))
        original_init = subprocess.Popen.__init__

        def popen(process: Any, command: Any, *args: Any, **kwargs: Any) -> None:
            if not isinstance(command, (list, tuple)) or not command or kwargs.get("shell"):
                self.reject("unreviewed subprocess shape")
            executable = os.fsdecode(command[0])
            name = Path(executable).name.lower()
            child = normalized(executable) == normalized(sys.executable)
            if child:
                isolated = (len(command) == 6 and list(command[1:5]) ==
                            ["-B", "-m", "tests.test_workspace_access", "--child"])
                mode_api = list(command[1:]) == ["-B", "-m", "tests.test_mode_api", "--isolated-mode-api-child"]
                timeout_case = list(command[1:]) == ["-c", "import time; time.sleep(30)"]
                if not isolated and not mode_api and not timeout_case:
                    self.reject("unreviewed Python child")
                self.counts["isolated_main_children" if isolated or mode_api else "timeout_test_children"] += 1
            elif name not in {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}:
                self.reject("non-media subprocess")
            else:
                self.counts["native_media_processes"] += 1
                for argument in command[1:]:
                    if re.match(r"(?i)^(?:https?|wss?|ftp|tcp|udp|rtsp|srt)://", str(argument)):
                        self.reject("native network input")
            environment = dict(kwargs.get("env") or os.environ)
            environment.update(PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1",
                               PYTHONFAULTHANDLER="1")
            # The child harness's explicit environment and fresh paths are kept.
            for key in ("DATA_DIR", "ASR_CACHE_DIR"):
                if key in environment and not under(normalized(environment[key]), normalized(self.root)):
                    self.reject("child data outside owned TEMP")
            kwargs["env"] = environment
            original_init(process, command, *args, **kwargs)

        stack.enter_context(patch.object(subprocess.Popen, "__init__", new=popen))

    def self_check(self) -> None:
        import httpx
        for operation in (
            lambda: self.path(PROJECT / ".env"),
            lambda: self.path(PROJECT / "data/tasks"),
            lambda: self.path(PROJECT / "backend/forbidden-write", write=True),
            lambda: self.address(("127.0.0.1", 8000)),
            lambda: self.url("https://safe.test/v1"),
        ):
            try:
                operation()
            except SafetyViolation:
                pass
            else:
                raise RuntimeError("Safety guard self-check failed")
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
            if client.get("https://safe.test/mock-only").status_code != 200:
                raise RuntimeError("MockTransport must remain usable")
        first, second = socket.socketpair()
        first.close()
        second.close()
        self.stage = "suite"


def source_hashes() -> dict[str, str]:
    files = [*PROJECT.glob("backend/**/*.py"), *PROJECT.glob("tests/test_*.py"),
             *PROJECT.glob("deploy/**/*.py"), Path(__file__), PROJECT / "tests/__init__.py",
             PROJECT / "deploy/golden-mic.env.production.example", PROJECT / "pyproject.toml"]
    return {file.relative_to(PROJECT).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(set(files))}


class Result(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.passed = 0

    def addSuccess(self, test: Any) -> None:
        self.passed += 1
        super().addSuccess(test)


def main() -> int:
    os.chdir(PROJECT)
    sys.path.insert(0, str(PROJECT))
    sys.dont_write_bytecode = True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    evidence = PROJECT / "canary_test/artifacts" / ("workspace-validation-" + stamp)
    evidence.mkdir(exist_ok=False)
    root = Path(tempfile.mkdtemp(prefix="golden-mic-core-validation-",
                                 dir=Path(os.environ["LOCALAPPDATA"]) / "Temp"))
    for name in ("tmp", "tasks", "cache/asr", "cache/hf", "cache/matplotlib", "cache/numba"):
        (root / name).mkdir(parents=True, exist_ok=True)
    log_path = evidence / "backend.log"
    summary: dict[str, Any] = {"started_at": datetime.now(timezone.utc).isoformat(),
                              "log": str(log_path), "temp_root": str(root),
                              "python": sys.version.split()[0], "status": "setup_failed"}
    print(json.dumps({"backend_run_started": summary}, ensure_ascii=True), flush=True)
    started = time.perf_counter()
    exit_code = 2
    with log_path.open("x", encoding="utf-8", buffering=1) as log:
        faulthandler.enable(file=log, all_threads=True)
        with redirect_stdout(log), redirect_stderr(log), ExitStack() as stack:
            try:
                environment, ffmpeg = synthetic_environment(root)
                os.environ.clear()
                os.environ.update(environment)
                tempfile.tempdir = str(root / "tmp")
                guard = Guards(root, evidence, ffmpeg)
                guard.install(stack)
                guard.self_check()
                summary["ffmpeg"] = str(ffmpeg)
                summary["ffmpeg_version"] = subprocess.run(
                    [str(ffmpeg), "-version"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", check=True,
                ).stdout.splitlines()[0]
                probe = shutil.which("ffprobe")
                if probe is None or normalized(probe) != normalized(ffmpeg.parent / "ffprobe.exe"):
                    raise SafetyViolation("ffprobe is not executable from sanitized PATH")
                initial = source_hashes()
                (evidence / "source-before.json").write_text(json.dumps(initial, indent=2), encoding="utf-8")
                suite = unittest.defaultTestLoader.discover(start_dir="tests")
                summary["discovered"] = suite.countTestCases()
                if "backend.main" in sys.modules:
                    settings = sys.modules["backend.main"].settings
                    if settings.app_env != "test" or normalized(settings.data_dir) != normalized(root / "tasks"):
                        raise SafetyViolation("Main singleton did not bind the isolated test storage")
                print("DISCOVERED", summary["discovered"], flush=True)
                suite_started = time.perf_counter()
                result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Result).run(suite)
                summary.update({
                    "tests": result.testsRun, "passed": result.passed,
                    "skipped": len(result.skipped), "failures": len(result.failures),
                    "errors": len(result.errors), "expected_failures": len(result.expectedFailures),
                    "unexpected_successes": len(result.unexpectedSuccesses),
                    "duration_seconds": round(time.perf_counter() - suite_started, 6),
                    "failure_details": [{"test": str(test), "traceback": detail}
                                        for test, detail in [*result.failures, *result.errors]],
                    "skip_details": [{"test": str(test), "reason": reason} for test, reason in result.skipped],
                })
                final = source_hashes()
                summary["source_drift"] = sorted(name for name in initial.keys() | final.keys()
                                                if initial.get(name) != final.get(name))
                summary["source_files_checked"] = len(initial)
                summary["guard_counters"] = dict(guard.counts)
                summary["remaining_owned_http_listeners"] = sorted(guard.ports)
                denied = any(key.startswith("suite:denied:") for key in guard.counts)
                clean = (result.wasSuccessful() and not denied and not guard.ports and not summary["source_drift"])
                summary["status"] = "passed" if clean else "failed_or_requires_review"
                exit_code = 0 if clean else 1
            except BaseException:
                detail = traceback.format_exc()
                log.write(detail)
                summary["runner_exception"] = detail
            finally:
                summary["total_duration_seconds"] = round(time.perf_counter() - started, 6)
                summary["finished_at"] = datetime.now(timezone.utc).isoformat()
                (evidence / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
                log.flush()
    print(json.dumps(summary, indent=2, ensure_ascii=True), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())