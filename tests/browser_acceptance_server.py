"""No-login workspace acceptance host; standalone, synthetic, fresh TEMP only.

Run with -B -m tests.browser_acceptance_server after the parent builds the UI.
Default listener: http://127.0.0.1:8782 (allowed dedicated range 8782..8799).
No launcher, account fixture, real-data canary, dotenv or inherited DATA_DIR.
The manifest and full original synthetic seed snapshot are retained on shutdown.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import tempfile
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from tests.workspace_fixture import (
    DISCLOSURE, EDITED_SENTENCE, PROJECT_ROOT, SCRIPT, NetworkGuard, SafetyError,
    create_inputs, fake_provider, isolated_environment, make_settings, require,
    seed_completed, sha256, unlinked, validate_frontend,
)


class Manifest:
    """Exclusive, owned descriptor. Never truncate/reuse a pre-existing manifest."""

    def __init__(self, path: Path, state: dict[str, Any]) -> None:
        self.path, self.state = path, state
        self.stream = path.open("x+", encoding="utf-8", newline="\n")
        self.flush()

    def flush(self) -> None:
        require(os.path.samestat(os.fstat(self.stream.fileno()), self.path.stat()), "manifest_ownership_lost")
        self.stream.seek(0)
        json.dump(self.state, self.stream, indent=2, ensure_ascii=True)
        self.stream.write("\n")
        self.stream.truncate()
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self) -> None:
        try:
            if self.state["serverState"] not in {"stopped", "failed"}:
                self.state.update(serverState="failed", failureCode="startup_or_shutdown_incomplete")
                self.flush()
        finally:
            self.stream.close()


def dedicated_port(value: str) -> int:
    port = int(value)
    if not 8782 <= port <= 8799:
        raise argparse.ArgumentTypeError("Use a dedicated acceptance port from 8782 through 8799")
    return port


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=dedicated_port, default=8782)
    parser.add_argument("--frontend-dir", type=Path, default=PROJECT_ROOT / "frontend" / "dist")
    parser.add_argument("--manifest", type=Path, help="Unused absolute JSON path below system TEMP; never reused")
    args = parser.parse_args()
    require(not any(name == "backend" or name.startswith("backend.") for name in sys.modules), "fresh_process_required")
    sys.dont_write_bytecode = True
    frontend, build_hashes = validate_frontend(args.frontend_dir)
    system_temp = unlinked(Path(tempfile.gettempdir()))
    require(not system_temp.is_relative_to(PROJECT_ROOT), "system_temp_must_be_outside_workspace")
    if args.manifest is not None:
        require(args.manifest.is_absolute(), "absolute_manifest_required")
        manifest_path = unlinked(args.manifest, exists=False)
        require(manifest_path.is_relative_to(system_temp) and not manifest_path.is_relative_to(PROJECT_ROOT)
                and manifest_path.suffix == ".json" and not manifest_path.exists(), "unused_temp_manifest_required")
        require(manifest_path.parent.is_dir(), "manifest_parent_must_exist")

    with ExitStack() as stack:
        # Fail on an occupied port BEFORE creating a seed or a manifest.
        listener = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen(128)
        listener.setblocking(False)
        root = unlinked(Path(tempfile.mkdtemp(prefix="golden-mic-workspace-", dir=system_temp)))
        for directory in ("tasks", "tmp", "cache"):
            (root / directory).mkdir()
        manifest_path = args.manifest if args.manifest is not None else root / "manifest.json"
        base_url = f"http://127.0.0.1:{args.port}"
        instance = uuid4().hex  # Public ownership marker, NOT an auth token.
        state: dict[str, Any] = {
            "schemaVersion": 1, "kind": "golden-mic-workspace-acceptance", "instanceId": instance,
            "synthetic": True, "disclosure": DISCLOSURE, "baseURL": base_url,
            "root": str(root), "taskRoot": str(root / "tasks"), "serverState": "starting",
            "frontend": {"directory": str(frontend), "hashes": build_hashes},
            "script": SCRIPT, "editedSentence": EDITED_SENTENCE, "inputs": [], "seed": None,
            "policy": {"dotenv": False, "inheritedSettings": False, "realData": False,
                       "paidCalls": False, "accounts": False, "allTenStages": True,
                       "qcPatched": False, "asr": False, "ownVoice": False, "videoEmbeddings": False,
                       "httpEgress": "exact-fake-listener-only", "websocketEgress": "deny-all"},
            "shutdownComplete": False,
        }
        manifest = Manifest(manifest_path, state)
        stack.callback(manifest.close)
        print(f"WORKSPACE_MANIFEST={manifest_path}", flush=True)
        print(f"WORKSPACE_URL={base_url} (synthetic only; wait for manifest serverState=ready)", flush=True)
        stack.enter_context(patch.dict(os.environ, isolated_environment(root), clear=True))
        stack.enter_context(patch.object(tempfile, "tempdir", str(root / "tmp")))
        provider = stack.enter_context(fake_provider())
        guard = NetworkGuard(provider.server_port, root, manifest_path)
        guard.install(stack)
        settings = make_settings(root, base_url, provider.server_port)
        # config is the ONLY backend module imported before this replacement.
        stack.enter_context(patch("backend.config.get_settings", return_value=settings))
        from backend.main import app, task_manager
        from backend.task_operations import trusted_local_request
        from fastapi import HTTPException, Request
        from fastapi.responses import JSONResponse
        from starlette.background import BackgroundTask
        from starlette.staticfiles import StaticFiles

        # Keep actual build and provider checks; only relocate the selected dist.
        stack.enter_context(patch("backend.readiness.FRONTEND_INDEX", frontend / "index.html"))
        stack.enter_context(patch("backend.readiness.FRONTEND_MANIFEST", frontend / "ASSET_MANIFEST.sha256"))
        # Test-only routes must precede the application's unknown-API fallback.
        # Reattach that same fallback below them, before the static frontend.
        api_fallback = next(route for route in app.router.routes if getattr(route, "name", None) == "missing_api")
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, "name", None) not in {"frontend", "missing_api"}]
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, workers=1,
            access_log=False, proxy_headers=False, lifespan="on", timeout_graceful_shutdown=45,
            log_config={"version": 1, "disable_existing_loggers": False,
                        "handlers": {"quiet": {"class": "logging.NullHandler"}},
                        "loggers": {name: {"handlers": ["quiet"], "propagate": False}
                                    for name in ("uvicorn", "uvicorn.error", "uvicorn.access")}}))

        def local(request: Request, *, mutation: bool = False) -> None:
            if not trusted_local_request(request, settings, mutation=mutation):
                raise HTTPException(404, "Acceptance endpoint unavailable")

        # Avoid postponed local Request annotations: FastAPI resolves globals.
        globals().update(Request=Request, JSONResponse=JSONResponse)

        @app.get("/api/test/workspace", include_in_schema=False)
        async def identify(request: Request) -> JSONResponse:
            local(request)
            return JSONResponse({"instanceId": instance, "synthetic": True, "baseURL": base_url,
                "serverState": state["serverState"], "seedTaskId": (state.get("seed") or {}).get("taskId"),
                "providerCalls": provider.snapshot(), "deniedEgress": guard.denied,
                "policy": state["policy"]})

        @app.post("/api/test/shutdown", include_in_schema=False)
        async def shutdown(request: Request) -> JSONResponse:
            local(request, mutation=True)
            if await request.json() != {"instanceId": instance}:
                raise HTTPException(409, "Acceptance instance mismatch")
            task_manager.begin_drain()
            return JSONResponse({"status": "stopping"}, background=BackgroundTask(setattr, server, "should_exit", True))

        @app.middleware("http")
        async def acceptance_boundary(request: Request, call_next: Any) -> Any:
            seed_id = (state.get("seed") or {}).get("taskId")
            if request.method not in {"GET", "HEAD", "OPTIONS"} and seed_id:
                prefix = f"/api/tasks/{seed_id}"
                if (request.url.path == prefix or request.url.path.startswith(prefix + "/")) and not (
                    request.method == "POST" and request.url.path == prefix + "/duplicate"
                ):
                    return JSONResponse({"detail": "Synthetic seed is read-only; duplicate it first"}, status_code=409)
            response = await call_next(request)
            response.headers["X-Workspace-Acceptance"] = instance
            response.headers["X-Workspace-Synthetic"] = "true"
            return response

        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application: Any) -> Any:
            try:
                async with original_lifespan(application):
                    state["preflightChecks"] = application.state.startup_readiness.checks
                    require(application.state.startup_readiness.ready, "real_preflight_failed")
                    require(task_manager.list_history()["total"] == 0, "fresh_history_required")
                    state["inputs"] = await asyncio.to_thread(create_inputs, root)
                    state["seed"] = await seed_completed(task_manager, root, state["inputs"])
                    state["serverState"] = "ready"
                    state["providerCalls"] = provider.snapshot()
                    manifest.flush()
                    yield
                state["shutdownComplete"] = True
                state["serverState"] = "stopped"
            except BaseException as failure:
                state["serverState"] = "failed"
                state["failureType"] = type(failure).__name__
                if isinstance(failure, SafetyError):
                    state["failureCode"] = str(failure)
                raise
            finally:
                state["providerCalls"] = provider.snapshot()
                state["deniedEgress"] = guard.denied
                if state.get("seed"):
                    snapshot = Path(state["seed"]["snapshotDirectory"])
                    state["originalSnapshotUnchanged"] = all(
                        sha256(snapshot / name) == digest for name, digest in state["seed"]["snapshotHashes"].items())
                manifest.flush()

        app.router.lifespan_context = lifespan
        app.router.routes.append(api_fallback)
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass  # Uvicorn handles the signal; only lifespan may claim clean shutdown.
        require(state["serverState"] == "stopped" and state["shutdownComplete"], "host_did_not_stop_cleanly")
        print("WORKSPACE_STATUS=stopped (owned TEMP retained)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("WORKSPACE_STATUS=interrupted (orderly shutdown not asserted)", flush=True)
        raise SystemExit(130) from None
    except Exception as exc:
        # No raw chains, provider payloads, task capabilities or session bodies.
        code = str(exc) if isinstance(exc, SafetyError) else "host_failure"
        print(f"WORKSPACE_STATUS=failed type={type(exc).__name__} code={code}", flush=True)
        raise SystemExit(1) from None