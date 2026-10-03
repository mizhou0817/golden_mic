"""Parent-launched v2 acceptance host; never launch on import.

Only 127.0.0.1:8787, fresh TEMP, real draft/upload/start/apply/export routes.
Reuses ONLY the modes host's confinement and explicitly synthetic providers and
inputs. Does not mount modes test routes or edit its harness/evidence. No seeds,
real .env/data/eval/history, QC patch, auto retry, model download or broad delete.
"""
from __future__ import annotations

import argparse
import asyncio
import faulthandler
import json
import os
import re
import socket
import sys
import tempfile
import time
import wave
from collections import Counter
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

sys.dont_write_bytecode = True
from tests import mode_acceptance_server as base

PORT = 8787
BASE_URL = f"http://127.0.0.1:{PORT}"
ROOT = base.PROJECT_ROOT


class V2Boundaries(base.Boundaries):
    """The single exception to modes' DB ban is this instance's NEW ledger."""

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event == "sqlite3.connect":
            value = args[0]
            if not isinstance(value, (str, Path)):
                self.deny()
                return
            path = Path(value)
            expected = self.root / "tasks/_v2/admission.sqlite3"
            if not path.is_absolute() or path != expected:
                self.deny()  # includes :memory:, URI, real DB and alternate TEMP
            self.file_boundary(path, write=True)
            base.unlinked(path, exists=False)
            return
        super().audit(event, args)


def configure(root: Path, manifest: Path, port: int, tools: tuple[Path, Path], stack: ExitStack):
    environment = base.isolated_environment(root)
    environment.update(PATH=str(tools[0].parent) + os.pathsep + next(
        (v for k, v in environment.items() if k.lower() == "path"), ""),
        DATA_DIR=str(root / "tasks"), ASR_CACHE_DIR=str(root / "cache/asr"),
        PYTHONUTF8="1", PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1")
    stack.enter_context(patch.dict(os.environ, environment, clear=True))
    stack.enter_context(patch.object(tempfile, "tempdir", str(root / "tmp")))
    guard = V2Boundaries(port, root, manifest, tools)
    guard.install(stack)
    guard.self_check()
    settings = base.make_settings(root, port)
    settings.frontend_origins = BASE_URL
    # No quota monkeypatch: the product's min(2, session_limit) remains intact.
    # Genuine /api/session cookies create independent owners for each context.
    settings.anonymous_session_task_rate_limit_per_hour = 2
    settings.anonymous_ip_task_rate_limit_per_hour = 30
    settings.anonymous_global_task_rate_limit_per_hour = 30
    settings.task_rate_limit_per_hour = 30
    stack.enter_context(patch("backend.config.get_settings", return_value=settings))
    return guard, settings


def source_hashes() -> dict[str, str]:
    files = {*ROOT.glob("backend/**/*.py"), *ROOT.glob("frontend/src/**/*")}
    files.update(ROOT / name for name in (
        "backend/mode_rules.json", "tests/v2_acceptance_server.py",
        "tests/mode_acceptance_server.py", "tests/workspace_fixture.py"))
    return {p.relative_to(ROOT).as_posix(): base.sha256(base.unlinked(p)) for p in sorted(files) if p.is_file()}


def test_hashes() -> dict[str, str]:
    files = {*ROOT.glob("frontend/e2e/v2/**/*"), *ROOT.glob("frontend/e2e/modes/*")}
    files.add(ROOT / "frontend/playwright.v2.config.ts")
    files.add(ROOT / "frontend/scripts/test-v2-probe-guard.mjs")
    return {p.relative_to(ROOT).as_posix(): base.sha256(base.unlinked(p)) for p in sorted(files) if p.is_file()}


def build_binding(frontend: Path, assets: dict[str, str]) -> dict[str, str]:
    # Parent may use the existing isolated modes build helper --label v2-....
    # Require its receipt, and COMPLETE current frontend/src coverage, not a
    # hand-picked list that omits newly added v2 modules. Backend is bound at
    # host startup and checked again in setup/teardown/shutdown.
    hashes = base.build_sources(frontend, assets)
    required = {p.relative_to(ROOT).as_posix() for p in (ROOT / "frontend/src").rglob("*") if p.is_file()}
    base.require(bool(hashes) and required <= hashes.keys(), "complete_frontend_build_binding_required")
    return hashes


def run_host(root: Path, manifest: base.Manifest, frontend: Path, listener: socket.socket,
             tools: tuple[Path, Path]) -> None:
    state = manifest.state
    with ExitStack() as stack:
        provider = stack.enter_context(base.fake_provider())
        guard, settings = configure(root, manifest.path, provider.server_port, tools, stack)
        fault = stack.enter_context((root / "native-fault.log").open("x", encoding="utf-8"))
        faulthandler.enable(file=fault, all_threads=True)
        stack.callback(faulthandler.disable)
        # Only reuse text/word fixtures, not old modes parsing-count assertions.
        # V2 paragraph/comma rules are exercised by the real browser submission.
        state["scripts"] = {"original": base.ORIGINAL_SCRIPT, "mixed": base.MIXED_SCRIPT,
                    "voiceover": base.VOICEOVER_SCRIPT, "shortMixed": base.SHORT_MIXED_SCRIPT}
        counts: Counter[str] = Counter()
        from backend.uploads import UploadStore
        from backend.providers.asr import ASRTranscript

        async def transcribe(store: Any, record: Any) -> Any:
            expected = next((i for i in state["inputs"] if i["name"] == record.name), None)
            if not expected or expected["kind"] != "interview" or record.sha256 != expected["sha256"] or record.size != expected["bytes"]:
                counts["unexpected"] += 1
                raise base.SafetyError("synthetic_asr_name_hash_size_required")
            audio = base.unlinked(store.root / record.id / "audio.wav")
            base.require(audio.is_relative_to(root / "tasks/_uploads"), "owned_decoded_audio_required")
            with wave.open(str(audio), "rb") as decoded:
                base.require(decoded.getnchannels() == 1 and decoded.getframerate() == 16000
                             and decoded.getnframes() > 0, "real_pcm_decode_required")
            record.asr_attempted = True
            store._save(record)
            counts[record.name] += 1
            record.asr_cached_at = time.time()
            return ASRTranscript.model_validate(base.provider_fixture(record.name))

        stack.enter_context(patch.object(UploadStore, "_transcribe", transcribe))
        from backend.main import app, task_manager
        from backend.task_operations import trusted_local_request
        from fastapi import HTTPException, Request
        from fastapi.responses import JSONResponse
        from starlette.background import BackgroundTask
        from backend.frontend_static import FrontendStaticFiles
        import uvicorn

        required = {("post", "/api/tasks"), ("get", "/api/tasks/{task_id}/draft"),
                    ("post", "/api/tasks/{task_id}/files"), ("post", "/api/tasks/{task_id}/start"),
                    ("post", "/api/tasks/{task_id}/align"), ("post", "/api/tasks/{task_id}/apply"),
                    ("put", "/api/tasks/{task_id}/checks"), ("post", "/api/tasks/{task_id}/exports"),
                    ("get", "/api/tasks/{task_id}/exports/{export_id}/file"), ("get", "/api/samples/default")}
        def shape(path: str) -> str:
            return re.sub(r"\{[^}/]+\}", "{}", path)

        actual = {(m, shape(p)) for p, operations in app.openapi()["paths"].items() for m in operations}
        base.require(all((m, shape(p)) in actual for m, p in required), "real_v2_routes_missing")
        stack.enter_context(patch("backend.readiness.FRONTEND_INDEX", frontend / "index.html"))
        stack.enter_context(patch("backend.readiness.FRONTEND_MANIFEST", frontend / "ASSET_MANIFEST.sha256"))
        fallback = next(r for r in app.router.routes if getattr(r, "name", None) == "missing_api")
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "name", None) not in {"frontend", "missing_api"}]
        globals().update(Request=Request, JSONResponse=JSONResponse)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, workers=1,
            # Uvicorn 0.51 custom zero-argument factory; no global policy patch.
            # Windows CPython/layout is fail-closed; non-Windows remains auto.
            loop="backend.windows_asyncio:new_event_loop" if sys.platform == "win32" else "auto",
            access_log=False, proxy_headers=False, lifespan="on", timeout_graceful_shutdown=60,
            log_config={"version": 1, "disable_existing_loggers": False,
                        "handlers": {"quiet": {"class": "logging.NullHandler"}},
                        "loggers": {n: {"handlers": ["quiet"], "propagate": False}
                                    for n in ("uvicorn", "uvicorn.error", "uvicorn.access")}}))

        def local(request: Any, mutation: bool = False) -> None:
            if not trusted_local_request(request, settings, mutation=mutation):
                raise HTTPException(404, "Acceptance endpoint unavailable")

        @app.get("/api/test/v2", include_in_schema=False)
        async def identity(request: Request) -> JSONResponse:
            local(request)
            return JSONResponse({"instanceId": state["instanceId"], "synthetic": True,
                "serverState": state["serverState"], "baseURL": BASE_URL,
                "providerCalls": provider.snapshot(), "uploadASRCalls": dict(counts), "deniedEgress": guard.denied,
                "quotaPolicy": state["quotaPolicy"]})

        @app.post("/api/test/v2/shutdown", include_in_schema=False)
        async def shutdown(request: Request) -> JSONResponse:
            local(request, True)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 256:
                    raise HTTPException(413, "Bounded instance marker required")
            try:
                valid = json.loads(raw) == {"instanceId": state["instanceId"]}
            except ValueError:
                valid = False
            if not valid:
                raise HTTPException(409, "Acceptance instance mismatch")
            task_manager.begin_drain()
            return JSONResponse({"status": "stopping"}, background=BackgroundTask(setattr, server, "should_exit", True))

        @app.middleware("http")
        async def mark(request: Request, call_next: Any) -> Any:
            response = await call_next(request)
            response.headers["X-V2-Acceptance"] = state["instanceId"]
            return response

        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application: Any):
            try:
                async with original_lifespan(application):
                    base.require(application.state.startup_readiness.ready, "real_preflight_failed")
                    base.require(task_manager.list_history()["total"] == 0, "fresh_empty_history_required")
                    state["inputs"] = await asyncio.to_thread(base.create_inputs, root)
                    state["serverState"] = "ready"
                    manifest.flush()
                    yield
                state.update(lifespanShutdownComplete=True, serverState="stopping")
            finally:
                state.update(providerCalls=provider.snapshot(), uploadASRCalls=dict(counts), deniedEgress=guard.denied,
                    inputHashesUnchanged=all(base.sha256(Path(i["path"])) == i["sha256"] for i in state["inputs"]),
                    sourceHashesUnchanged=source_hashes() == state["sourceHashes"],
                    testHashesUnchanged=test_hashes() == state["testHashes"])
                manifest.flush()

        app.router.lifespan_context = lifespan
        app.router.routes.append(fallback)
        app.mount("/", FrontendStaticFiles(directory=frontend, html=True), name="frontend")
        server.run(sockets=[listener])
        base.require(state["lifespanShutdownComplete"], "lifespan_shutdown_unconfirmed")
    listener.close()
    state.update(serverState="stopped", shutdownComplete=True, fakeProviderStopped=True)
    manifest.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--frontend-dir", required=True, type=Path)
    args = parser.parse_args()
    base.require(not any(n == "backend" or n.startswith("backend.") for n in sys.modules), "fresh_process_required")
    temporary = base.unlinked(Path(tempfile.gettempdir()))
    base.require(not temporary.is_relative_to(ROOT), "external_system_temp_required")
    base.require(args.manifest.is_absolute(), "absolute_manifest_required")
    path = base.unlinked(args.manifest, exists=False)
    base.require(path.is_relative_to(temporary) and not path.is_relative_to(ROOT) and path.suffix == ".json"
                 and not path.exists() and path.parent.is_dir(), "fresh_temp_manifest_required")
    base.require(args.frontend_dir.is_absolute() and args.frontend_dir.name.startswith("dist-canary-modes-v2-"), "v2_labelled_custom_build_required")
    frontend, assets = base.validate_frontend(args.frontend_dir)
    sources = build_binding(frontend, assets)
    tools = base.media_tools()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", PORT))  # refuse occupied; never find/kill owner
        listener.listen(128)
        listener.setblocking(False)
        root = base.unlinked(Path(tempfile.mkdtemp(prefix="golden-mic-v2-", dir=temporary)))
        for name in ("tmp", "tasks", "cache"):
            (root / name).mkdir()
        state: dict[str, Any] = {"schemaVersion": 1, "kind": "golden-mic-v2-acceptance", "synthetic": True,
            "instanceId": uuid4().hex, "baseURL": BASE_URL, "pid": os.getpid(), "root": str(root),
            "taskRoot": str(root / "tasks"), "serverState": "starting", "inputs": [],
            "disclosure": base.DISCLOSURE, "frontend": {"directory": str(frontend), "hashes": assets, "sourceHashes": sources},
            "sourceHashes": source_hashes(), "testHashes": test_hashes(),
            "tools": {"ffmpeg": str(tools[0]), "ffprobe": str(tools[1])},
            "policy": {"dotenv": False, "realData": False, "paidCalls": False, "seededTasks": False,
                       "qcPatched": False, "rawArtifacts": False, "localSpeechModelPatched": False,
                       "sqlite": "only-owned-v2-admission-ledger", "retainAllNewTasksAndUploads": True},
            "quotaPolicy": {"session": 2, "ip": 30, "global": 30, "legacy": 30,
                            "owner": "genuine-api-session-cookie-per-browser-context", "quotaAcceptanceClaimed": False},
            "lifespanShutdownComplete": False, "shutdownComplete": False, "fakeProviderStopped": False}
        manifest = base.Manifest(path, state)
        print(f"V2_MANIFEST={path}", flush=True)
        try:
            run_host(root, manifest, frontend, listener, tools)
        except BaseException as exc:
            state.update(serverState="failed", failureType=type(exc).__name__)
            manifest.flush()
            raise
        finally:
            manifest.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        # No exception body, raw request URL, cookie or capability in stdout.
        print(f"V2_HOST_EXIT={type(exc).__name__}", flush=True)
        raise SystemExit(130 if isinstance(exc, KeyboardInterrupt) else 1) from None