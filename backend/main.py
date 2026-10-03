import asyncio
import hashlib
import ipaddress
import json
import re
import secrets
import unicodedata
from contextlib import asynccontextmanager, suppress
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .anonymous_access import (
    ANONYMOUS_CSRF_HEADER,
    ANONYMOUS_SESSION_COOKIE,
    AnonymousSession,
    csrf_token_matches,
    issue_anonymous_session,
    verify_anonymous_session,
)
from .config import get_settings
from .frontend_static import FrontendStaticFiles
from .instance_lock import InstanceLock
from .media import configure_media_command_timeout
from .public_media import committed_root, public_preview
from .media_input import prepare_media_inputs
from .models import (
    EditingPreferences,
    PublicLimitsResponse,
    RemixRequest,
    RemixResponse,
    ReportResponse,
    ShotReplacementRequest,
    ShotReplacementResponse,
    TaskCreateResponse,
    TaskCreateRequest,
    TaskStatusResponse,
    MatchPlanItem,
    SentenceTiming,
    TaskState,
)
from .production_modes import SentenceInput, Speaker, align_quotes, parse_sentences
from .uploads import UploadStore, create_upload_router
from .drafts import DraftService, DraftUploadStore, create_draft_router
from .admission import AdmissionLedger, owner_digest
from .task_metadata import MetadataInput, commit as commit_metadata, expires_at, history_entry
from .publication import confirm_checks, publication_gate, require_publication
from .remix import RemixValidationError
from .operations import InsufficientDiskSpaceError, UploadCapacityGuard
from .readiness import ReadinessReport, dynamic_readiness_errors, run_full_preflight
from .providers.generative import generative_fill_configured
from .script_segmentation import prepare_headline_script
from .shot_replacement import ShotReplacementValidationError
from .storage import ALLOWED_EXTENSIONS, create_task_dir, save_uploads, write_json_atomic
from .studio import create_studio_router, studio_task_busy
from .studio_assets import IMAGE_BYTES as STUDIO_IMAGE_BYTES, LUT_BYTES as STUDIO_LUT_BYTES
from .workbench import create_workbench_router
from .task_manager import TaskManager, TaskRecord
from .task_operations import (
    create_task_operations_router, legacy_task_busy, task_operation_busy, trusted_local_request,
    _managed_root, _operation,
)


settings = get_settings()
configure_media_command_timeout(settings.media_command_timeout_seconds)
upload_capacity_guard = UploadCapacityGuard(settings)
task_manager = TaskManager(settings, upload_capacity_guard)
rate_counter: dict[str, deque[datetime]] = defaultdict(deque)
MAX_RATE_LIMIT_CLIENTS = 10_000
instance_lock = InstanceLock(settings.data_dir.parent / ".golden-mic.instance.lock")
_upload_store: UploadStore | None = None
_draft_service: DraftService | None = None
_DEV_SESSION_COOKIE = "golden_mic_local_session"
_preview_slot = asyncio.Semaphore(1)


def _uploads() -> UploadStore:
    # Lazy initialization is important: importing the application/OpenAPI must
    # never create an upload directory or take a second data-volume lock.
    global _upload_store
    if _upload_store is None:
        _upload_store = DraftUploadStore(settings)
        _upload_store.external_reserved = lambda: upload_capacity_guard._reserved_bytes
    elif _upload_store.root.parent != settings.data_dir.absolute():
        raise HTTPException(503, "Upload storage configuration changed; drain and restart required")
    return _upload_store


def _drafts() -> DraftService:
    global _draft_service
    if _draft_service is None:
        # Creation is the only path that may initialize a fresh data directory.
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _draft_service = DraftService(settings, task_manager, _LazyUploads(), upload_capacity_guard)
        _draft_service.legacy_counts = _legacy_start_counts
    elif _draft_service.manager is not task_manager or _draft_service.settings.data_dir != settings.data_dir:
        raise HTTPException(503, "Draft storage configuration changed; drain and restart required")
    _draft_service.ledger.check()
    return _draft_service


def _draft_owner(request: Request) -> str | None:
    if settings.is_production:
        session = _anonymous_session(request)
        return owner_digest(session.session_id) if session else None
    token = request.cookies.get(_DEV_SESSION_COOKIE, "")
    return owner_digest(token) if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) else None


def _legacy_start_counts(owner: str, ip: str) -> tuple[int, int, int]:
    _prune_rate_counter(datetime.now(timezone.utc) - timedelta(hours=1))
    global_count = len(rate_counter.get("anonymous-global", ()))
    session_count = 0
    for key, entries in rate_counter.items():
        if key.startswith("anonymous-session=") and owner_digest(key.split("=", 1)[1]) == owner:
            session_count += len(entries)
        elif key.startswith("ip="):
            global_count += len(entries)
    ip_count = len(rate_counter.get(f"anonymous-ip={ip}", ())) + len(rate_counter.get(f"ip={ip}", ()))
    return global_count, session_count, ip_count


class _LazyUploads:
    @property
    def settings(self):
        return settings

    def __getattr__(self, name: str):
        return getattr(_uploads(), name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task_manager.start_accepting()
    readiness = await run_full_preflight(settings)
    app.state.startup_readiness = readiness
    if settings.is_production and not readiness.ready:
        raise RuntimeError("生产启动预检失败：" + "；".join(readiness.errors))
    lock_acquired = False
    if settings.is_production:
        instance_lock.acquire()
        lock_acquired = True
    cleanup_task: asyncio.Task[None] | None = None
    uploads_cleanup_task: asyncio.Task[None] | None = None
    try:
        # Recovery is local rollback only; never resumes media/provider work.
        # It must precede TaskRecord restoration, otherwise memory keeps the
        # half-published revision even after the durable state is rolled back.
        from .revisions import recover_v2_publication
        for directory in (settings.data_dir.iterdir() if settings.data_dir.exists() else ()):
            if re.fullmatch(r"[0-9a-f]{32}", directory.name) and directory.is_dir():
                recover_v2_publication(directory)
        restored_count = task_manager.restore_tasks()
        if any(record.lifecycle_v2 for record in task_manager._tasks.values()) or (settings.data_dir / "_v2" / "admission.sqlite3").is_file():
            _drafts()
        if restored_count:
            print(f"Restored {restored_count} historical task(s).")
        cleanup_task = asyncio.create_task(task_manager.cleanup_loop(), name="task-ttl-cleanup")
        uploads_cleanup_task = asyncio.create_task(_cleanup_uploads_loop(), name="upload-ttl-cleanup")
        yield
    finally:
        try:
            if cleanup_task is not None:
                cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup_task
            if uploads_cleanup_task is not None:
                uploads_cleanup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await uploads_cleanup_task
            global _draft_service
            if _draft_service is not None:
                await _draft_service.close()
                _draft_service = None
            await task_manager.shutdown()
            global _upload_store
            if _upload_store is not None:
                await _upload_store.close()
                _upload_store = None
        finally:
            if lock_acquired:
                instance_lock.release()


app = FastAPI(
    title="AI 智能新闻剪辑",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_api_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_api_docs else None,
)


async def _cleanup_uploads_loop() -> None:
    while True:
        if _draft_service is not None:
            await _draft_service.cleanup()
        elif any(record.lifecycle_v2 for record in task_manager._tasks.values()):
            await _drafts().cleanup()
        if _upload_store is not None:
            await _upload_store.cleanup_owned()
        await asyncio.sleep(3600)


@app.middleware("http")
async def security_headers(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    request_path = str(request.scope.get("path") or "")
    if _is_invalid_request_path(request_path):
        response = JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "页面不存在。"},
        )
    elif _must_reject_for_drain(request):
        response = JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "服务正在排空并准备停止，暂不接受新任务。"},
            headers={"Retry-After": "60"},
        )
    elif _must_check_origin(request) and not _has_allowed_origin(request):
        response = JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "请求来源不受信任。"},
        )
    elif _must_require_anonymous_session(request) and not _attach_valid_anonymous_session(request):
        response = JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "匿名会话无效或已过期，请刷新页面后重试。"},
            headers={"X-Anonymous-Session": "required"},
        )
    else:
        try:
            if request.method in {"POST", "PUT", "PATCH"} and request_path.startswith("/api/tasks/"):
                body_limit = 21 * 1024 * 1024 if request_path.endswith("/recordings") else 256 * 1024
                if request.method == "PUT" and re.fullmatch(r"/api/tasks/[0-9a-f]{32}/files/up_[0-9a-f]{32}/chunks/[0-9]+", request_path):
                    body_limit = 8 * 1024 * 1024
                # Only exact raw-asset POSTs get Studio's image/LUT limits, not
                # JSON edits, other methods or path aliases. The router still
                # bounds actual streamed bytes and checks the declared length.
                if request.method == "POST":
                    asset_route = re.fullmatch(r"/api/tasks/[0-9a-f]{32}/studio/assets/(image|lut)", request_path)
                    if asset_route is not None:
                        body_limit = STUDIO_IMAGE_BYTES if asset_route.group(1) == "image" else STUDIO_LUT_BYTES
                content_length = request.headers.get("content-length")
                if content_length is None:
                    raise HTTPException(411, "请求必须包含 Content-Length。")
                if not content_length.isdigit() or int(content_length) > body_limit:
                    raise HTTPException(413, "编辑请求超出允许大小。")
            if request.method == "POST" and request_path == "/api/tasks":
                content_length = _validated_upload_content_length(request)
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "application/json":
                    if content_length > 256 * 1024:
                        raise HTTPException(413, "制作请求过大，请缩短稿件。")
                    response = await call_next(request)
                else:
                    _enforce_task_rate_limits(request)
                    async with upload_capacity_guard.reserve(content_length) as reservation:
                        request.state.upload_reservation = reservation
                        response = await call_next(request)
            else:
                response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
        except InsufficientDiskSpaceError as exc:
            response = JSONResponse(
                status_code=507,
                content={"detail": str(exc)},
                headers={"Retry-After": "300"},
            )
        except OSError as exc:
            if getattr(exc, "winerror", None) != 123:
                raise
            response = JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "页面不存在。"},
            )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "media-src 'self' blob:; connect-src 'self'; font-src 'self'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if settings.is_production and _request_is_https(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/api/", "/health/")) or request.headers.get("authorization"):
        response.headers["Cache-Control"] = "private, no-store"
    return response

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Content-Range", "X-Task-Token", "X-Upload-Token", ANONYMOUS_CSRF_HEADER],
)


@app.get("/health/live", include_in_schema=False)
async def health_live() -> JSONResponse:
    return JSONResponse({"status": "live"})


@app.get("/health/ready", include_in_schema=False)
async def health_ready(request: Request) -> JSONResponse:
    startup = getattr(
        request.app.state,
        "startup_readiness",
        ReadinessReport(ready=False, checks={}, errors=("startup_checks_not_run",)),
    )
    capacity = await upload_capacity_guard.snapshot()
    dynamic_errors = dynamic_readiness_errors(
        settings,
        draining=task_manager.is_draining,
        reserved_bytes=capacity.reserved_bytes,
    )
    ready = startup.ready and not dynamic_errors
    payload: dict[str, Any] = {
        "status": "ready" if ready else "not_ready",
        "checks": startup.checks,
        "errors": [*startup.errors, *dynamic_errors],
    }
    return JSONResponse(payload, status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE)

@app.get("/api/config/availability")
async def browser_availability(request: Request) -> JSONResponse:
    """Browser-safe readiness; operational checks and disk/provider details stay private."""
    response = await health_ready(request)
    return JSONResponse({"status": "ready" if response.status_code == 200 else "not_ready"})


@app.get("/api/config/limits", response_model=PublicLimitsResponse)
async def public_limits() -> PublicLimitsResponse:
    return PublicLimitsResponse(
        max_files=min(settings.max_files, 20),
        max_upload_bytes=min(settings.upload_limit_bytes, 500 * 1024**2),
        max_total_upload_bytes=min(settings.total_upload_limit_bytes, 5 * 1024**3),
        max_script_length=8000,
        max_video_duration_seconds=settings.max_source_duration_seconds_per_file,
        max_total_video_duration_seconds=settings.max_total_source_duration_seconds,
        allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        max_shots=settings.max_shots,
        max_concurrent_tasks=min(settings.max_concurrent_tasks, 1),
        max_pending_tasks=min(settings.max_pending_tasks, 5),
        maxPending=settings.v2_max_waiting_tasks,
        maintenance=task_manager.is_draining,
        retry_same_supported=_draft_service is not None and _draft_service.retry_hook is not None,
        legacy_max_pending_tasks=min(settings.max_pending_tasks, 5),
    )


@app.get("/api/config/workspace")
async def workspace_config(request: Request) -> dict[str, bool]:
    return {
        "local_history": trusted_local_request(request, settings),
        "generative_fill_available": generative_fill_configured(settings),
    }


@app.get("/api/tasks")
async def task_history(
    request: Request,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    owner = _draft_owner(request)
    if owner:
        records = [r for r in task_manager._tasks.values() if r.lifecycle_v2 and r.owner_hash == owner]
        records.sort(key=lambda r: r.updated_at, reverse=True)
        entries = [{"id": r.task_id, **history_entry(r)} for r in records]
        ledger = _metadata_ledger()
        if ledger is not None:
            entries += ledger.history(owner)
        return {"tasks": entries[offset:offset + min(limit, 100)], "total": len(entries)}
    if not trusted_local_request(request, settings):
        raise HTTPException(404, "页面不存在。")
    return task_manager.list_history(offset, limit)


@app.get("/api/session", include_in_schema=False)
async def browser_session(request: Request) -> JSONResponse:
    if not settings.is_production:
        response = JSONResponse(
            {"access_mode": "development", "csrf_token": None, "expires_at": None}
        )
        if not _draft_owner(request):
            response.set_cookie(_DEV_SESSION_COOKIE, secrets.token_urlsafe(32), max_age=30 * 86400,
                                httponly=True, samesite="strict", path="/")
        return response
    secret = settings.anonymous_session_secret.get_secret_value()
    session = verify_anonymous_session(
        request.cookies.get(ANONYMOUS_SESSION_COOKIE),
        secret,
        settings.anonymous_session_ttl_seconds,
    )
    session_token: str | None = None
    if session is None:
        session_token, session = issue_anonymous_session(
            secret,
            settings.anonymous_session_ttl_seconds,
        )
    response = JSONResponse(
        {
            "access_mode": "anonymous",
            "csrf_token": session.csrf_token,
            "expires_at": datetime.fromtimestamp(
                session.expires_at,
                tz=timezone.utc,
            ).isoformat(),
        }
    )
    if session_token is not None:
        response.set_cookie(
            ANONYMOUS_SESSION_COOKIE,
            session_token,
            max_age=settings.anonymous_session_ttl_seconds,
            path="/",
            secure=True,
            httponly=True,
            samesite="strict",
        )
    return response


@app.get("/api/admin/drain", include_in_schema=False)
async def drain_status(request: Request) -> JSONResponse:
    _require_loopback(request)
    return JSONResponse(task_manager.operational_snapshot())


@app.post("/api/admin/drain", include_in_schema=False)
async def begin_drain(request: Request) -> JSONResponse:
    _require_loopback(request)
    return JSONResponse(task_manager.begin_drain())


@app.delete("/api/admin/drain", include_in_schema=False)
async def cancel_drain(request: Request) -> JSONResponse:
    _require_loopback(request)
    task_manager.start_accepting()
    return JSONResponse(task_manager.operational_snapshot())


async def _create_multipart_task(
    request: Request,
    script: str = Form(...),
    files: list[UploadFile] = File(...),
    preferences: str | None = Form(default=None),
    asset_options: str | None = Form(default=None),
    own_voice: UploadFile | None = File(default=None),
    script_format: Literal["auto", "headline_first"] = Form(default="auto"),
) -> TaskCreateResponse:
    # FastAPI has already parsed this form; reject the removed workflow before
    # creating a task directory or starting any paid processing.
    if "classroom" in await request.form():
        raise HTTPException(422, "提交方式已更新，请刷新页面后直接创建视频任务。")
    if len(script) > 8000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新闻稿不能超过 8000 字。")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in script):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新闻稿包含不支持的控制字符。")
    # Only explicit wizard submissions impose a first-line headline. Legacy
    # clients keep strip-only preparation and the existing auto-parse heuristic.
    try:
        clean_script = prepare_headline_script(script) if script_format == "headline_first" else script.strip()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not clean_script:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新闻稿不能为空。")
    if len(clean_script) > 8000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="新闻稿不能超过 8000 字。")
    editing_preferences = _parse_editing_preferences(preferences)

    task_id = uuid4().hex
    task_dir = create_task_dir(settings, task_id)
    try:
        try:
            uploads = await save_uploads(task_dir, files, settings)
            # Preserve the existing video-only validation path; the creator supplies
            # explicit media options for still images, trims and recorded narration.
            if asset_options is not None or own_voice is not None or any(
                Path(asset.original_name).suffix.lower() in {".jpg", ".jpeg", ".png", ".gif"} for asset in uploads
            ):
                uploads = await prepare_media_inputs(task_dir, uploads, asset_options, own_voice, settings)
        finally:
            for upload in files:
                await upload.close()
            if own_voice is not None:
                await own_voice.close()
        reservation = request.state.upload_reservation
        record = task_manager.add_task(
            task_id=task_id,
            task_dir=task_dir,
            script=clean_script,
            uploads=uploads,
            preferences=editing_preferences,
            reserved_disk_bytes=reservation.reserved_bytes,
        )
        reservation.retain()
    except BaseException as exc:
        await task_manager.discard_unaccepted_upload(task_id, task_dir)
        if isinstance(exc, RuntimeError):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        raise
    assert record.access_token
    return TaskCreateResponse(task_id=task_id, access_token=record.access_token)


async def _bounded_json(request: Request, limit: int = 256 * 1024) -> dict[str, Any]:
    body = bytearray()
    async with asyncio.timeout(30):
        async for chunk in request.stream():
            if len(body) + len(chunk) > limit:
                raise HTTPException(413, "请求内容过大，请缩短后重试。")
            body.extend(chunk)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "请求内容无法读取，请刷新后重试。") from exc
    if not isinstance(value, dict):
        raise HTTPException(422, "请求内容必须是一个对象。")
    return value


def _mode_payload(raw: dict[str, Any]) -> TaskCreateRequest:
    try:
        payload = TaskCreateRequest.model_validate(raw)
        payload.script = prepare_headline_script(payload.script)
        if not payload.sentences:
            payload.sentences = parse_sentences(payload.script, payload.mode)
        if not payload.sentences:
            raise ValueError("至少需要一句正文——请写稿或从转写中挑一句。")
        if sum(len(s.text) for s in payload.sentences) > 8000:
            raise ValueError("稿子不能超过 8000 字。")
        def literal(value: str) -> str:
            return "".join(c for c in unicodedata.normalize("NFKC", value) if c.isalnum())
        parsed = parse_sentences(payload.script, payload.mode)
        if literal("".join(s.text for s in parsed)) != literal("".join(s.text for s in payload.sentences)):
            raise ValueError("句子清单和稿子不一致——请刷新后重新确认稿件。")
        if any(s.source_hint and s.source_hint.upload_id not in payload.upload_ids for s in payload.sentences):
            raise ValueError("句子引用了未选择的素材，请重新核对出处。")
        if any(s.source_hint and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", s.source_hint.seg_id) for s in payload.sentences):
            raise ValueError("原话出处编号无效，请重新从转写中挑选。")
        return payload
    except (ValueError, ValidationError) as exc:
        # Validation errors can include raw request inputs and capabilities.
        message = "制作参数无效——请核对模式、句子类型、稿件和已上传素材。" if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(422, message) from exc


async def _create_preuploaded_task(request: Request, payload: TaskCreateRequest, own_voice: UploadFile | None = None) -> TaskCreateResponse:
    store = _uploads()
    snapshots = store.snapshots(payload.upload_ids, payload.upload_tokens)
    task_manager.ensure_start_capacity()
    if own_voice is not None and payload.mode == "original":
        await own_voice.close()
        raise HTTPException(422, "只用原声模式不接收配音文件。")
    total = sum(snapshot["bytes"] for snapshot in snapshots)
    # Reserve actual source bytes, not the tiny JSON request body.
    async with _source_reservation(request, total) as reservation:
        task_id = uuid4().hex
        task_dir = create_task_dir(settings, task_id)
        try:
            uploads, snapshots = await store.materialize(payload.upload_ids, payload.upload_tokens, task_dir)
            asset_options = json.dumps(payload.asset_options, ensure_ascii=False) if payload.asset_options else None
            uploads = await prepare_media_inputs(task_dir, uploads, asset_options, own_voice, settings)
            write_json_atomic(task_dir / "pretranscripts.json", snapshots)
            record = task_manager.add_task(
                task_id, task_dir, payload.script, uploads, preferences=payload.preferences,
                mode=payload.mode, mode_contract=True, upload_ids=payload.upload_ids,
                sentences=payload.sentences, speakers=payload.speakers,
                quality_gate_mode="block" if settings.is_production else (payload.quality_gate_mode or settings.quality_gate_mode),
                reserved_disk_bytes=reservation.reserved_bytes,
            )
            reservation.retain()
        except BaseException:
            await task_manager.discard_unaccepted_upload(task_id, task_dir)
            raise
        finally:
            if own_voice is not None:
                await own_voice.close()
    return TaskCreateResponse(task_id=task_id, access_token=record.access_token)


@asynccontextmanager
async def _source_reservation(request: Request, total: int):
    existing = getattr(request.state, "upload_reservation", None)
    if existing is not None:
        await upload_capacity_guard.expand(existing, total + int(request.headers.get("content-length", "0")))
        yield existing
    else:
        async with upload_capacity_guard.reserve(total) as reservation:
            yield reservation


@app.post("/api/tasks", response_model=TaskCreateResponse, status_code=202,
          openapi_extra={"requestBody": {"required": True, "content": {
              "application/json": {"schema": TaskCreateRequest.model_json_schema()},
              "multipart/form-data": {"schema": {"type": "object", "description": "旧客户端文件提交，或已上传素材加自录配音。"}},
          }}})
async def create_task(request: Request) -> TaskCreateResponse | JSONResponse:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "application/json":
        raw = await _bounded_json(request)
        if "script" not in raw and "upload_ids" not in raw:
            owner = _draft_owner(request)
            local_token = None
            if owner is None:
                if settings.is_production:
                    raise HTTPException(403, "Anonymous session required")
                local_token = secrets.token_urlsafe(32)
                owner = owner_digest(local_token)
            response = JSONResponse(await _drafts().create(raw, owner), status_code=201)
            if local_token:
                response.set_cookie(_DEV_SESSION_COOKIE, local_token, max_age=30 * 86400,
                                    httponly=True, samesite="strict", path="/")
            return response
        _enforce_task_rate_limits(request)
        return await _create_preuploaded_task(request, _mode_payload(raw))
    form = await request.form(max_files=101, max_fields=32, max_part_size=256 * 1024)
    if form.get("upload_ids"):
        try:
            raw: dict[str, Any] = {"mode": str(form.get("mode", "voiceover")), "script": str(form.get("script", ""))}
            for key in ("upload_ids", "upload_tokens", "sentences", "speakers", "preferences", "asset_options"):
                if key in form:
                    raw[key] = json.loads(str(form[key]))
            payload = _mode_payload(raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "制作参数不完整，请重新核对后提交。") from exc
        return await _create_preuploaded_task(request, payload, form.get("own_voice"))
    if "mode" in form:
        raise HTTPException(422, "三模式制作需要先完成素材上传——请刷新后从向导提交。")
    return await _create_multipart_task(
        request, script=str(form.get("script", "")), files=form.getlist("files"),
        preferences=form.get("preferences"), asset_options=form.get("asset_options"),
        own_voice=form.get("own_voice"), script_format=str(form.get("script_format", "auto")),
    )


class MatchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentences: list[SentenceInput] = Field(max_length=200)
    upload_ids: list[str] = Field(min_length=1, max_length=20)
    upload_tokens: dict[str, str]
    speakers: list[Speaker] = Field(default_factory=list, max_length=100)


@app.post("/api/match/preview")
async def preview_match(request: Request) -> dict[str, Any]:
    try:
        payload = MatchPreviewRequest.model_validate(await _bounded_json(request))
    except ValidationError as exc:
        raise HTTPException(422, "原话预检参数无效，请核对稿句和素材。") from exc
    if sum(len(sentence.text) for sentence in payload.sentences) > 8000:
        raise HTTPException(422, "稿子不能超过 8000 字。")
    snapshots = _uploads().snapshots(payload.upload_ids, payload.upload_tokens)
    if _preview_slot.locked():
        raise HTTPException(429, "另一份原话正在核对，请稍后重试。", headers={"Retry-After": "1"})
    from .pipeline import _run_blocking_until_complete
    from functools import partial
    async with _preview_slot:
        try:
            # Preview may suggest original audio for narration, but never
            # changes the journalist's chosen sentence kind or stage-6 input.
            candidates = [sentence.model_copy(update={"kind": "quote"}) for sentence in payload.sentences]
            matches = await _run_blocking_until_complete(partial(align_quotes, candidates, snapshots, payload.speakers))
            for match, sentence in zip(matches, payload.sentences, strict=True):
                match["kind"] = sentence.kind
        except ValueError as exc:
            raise HTTPException(422, "这份原话暂时无法安全核对——请缩短单句，或从转写中直接挑选。") from exc
    return {"matches": matches, "match_ok": 0.85, "match_low": 0.6}


def _metadata_ledger() -> AdmissionLedger | None:
    if _draft_service is not None:
        if _draft_service.manager is not task_manager or _draft_service.settings.data_dir != settings.data_dir:
            raise HTTPException(503, "Draft storage configuration changed")
        return _draft_service.ledger
    return AdmissionLedger.existing(settings.data_dir)


async def _authorize_task(
    request: Request, task_id: str, *, write: bool = False, allow_operation_owner: bool = False,
) -> TaskRecord:
    header_tokens = request.headers.getlist("x-task-token")
    query_tokens = request.query_params.getlist("token")
    # A single matching header/query pair is an established download contract.
    # Repetition within either channel and conflicting channels remain invalid.
    if (len(header_tokens) > 1 or len(query_tokens) > 1
            or (header_tokens and query_tokens and header_tokens[0] != query_tokens[0])):
        raise HTTPException(404, "任务不存在或无权访问。")
    supplied = header_tokens or query_tokens
    if task_manager.get(task_id) is None:
        ledger = _metadata_ledger()
        if ledger is not None and ledger.proves_gone(task_id, supplied, _draft_owner(request)):
            raise HTTPException(410, {"code": "task_gone"})
        raise HTTPException(404, "任务不存在或无权访问。")
    if trusted_local_request(request, settings, mutation=write):
        record = task_manager.get(task_id)
    else:
        record = task_manager.authorize(task_id, supplied[0] if supplied else None)
    # Explicit empty, invalid or conflicting capabilities never fall back to
    # local authority. Account cookies are deliberately not inspected at all.
    if record is not None and any(not task_manager.token_matches(record, token) for token in supplied):
        raise HTTPException(404, "任务不存在或无权访问。")
    if record is None or task_manager.get(task_id) is not record:
        raise HTTPException(404, "任务不存在或无权访问。")
    if record.lifecycle_v2:
        # V2 files are always task-capability scoped, even on loopback. A local
        # history view must not confer another browser's draft/upload authority.
        if not supplied or not all(task_manager.token_matches(record, token) for token in supplied):
            raise HTTPException(404, "任务不存在或无权访问。")
        expiry = expires_at(record)
        if expiry is not None and expiry <= datetime.now(timezone.utc):
            raise HTTPException(410, {"code": "task_gone"})
    if write and task_operation_busy(record.task_dir, allow_owner=allow_operation_owner):
        raise HTTPException(409, "作品副本正在保存，请稍后再编辑或删除。")
    if write and "/studio/" not in request.url.path and studio_task_busy(record.task_dir):
        raise HTTPException(409, "专业剪辑导出进行中，请先完成或取消导出。")
    if write and legacy_task_busy(record.task_dir):
        raise HTTPException(409, "历史云任务仍在处理或等待核对，暂不能编辑或删除作品。")
    return record


async def _authorize_private(request: Request, task_id: str, *, write: bool = False) -> TaskRecord:
    return await _authorize_task(request, task_id, write=write)


async def _authorize_workbench_mutation(request: Request, task_id: str, *, write: bool = True) -> TaskRecord:
    # Workbench rechecks its owned queued reservation and revision after this.
    return await _authorize_task(request, task_id, write=write)


async def _authorize_operation(request: Request, task_id: str, *, write: bool = False) -> TaskRecord:
    return await _authorize_task(request, task_id, write=write, allow_operation_owner=True)


async def _authorize_studio(request: Request, task_id: str, *, write: bool = False) -> TaskRecord:
    # Studio retains its own real QC, source/revision and path checks. Immutable
    # successful outputs no longer depend on an account's current approval.
    return await _authorize_task(request, task_id, write=write)


@app.patch("/api/tasks/{task_id}/metadata", openapi_extra={"requestBody": {
    "required": True, "content": {"application/json": {"schema": MetadataInput.model_json_schema()}}
}})
async def patch_task_metadata(task_id: str, request: Request) -> dict[str, Any]:
    record = await _authorize_task(request, task_id, write=True)
    try:
        raw = await _bounded_json(request)
    except TimeoutError as exc:
        raise HTTPException(408, "Metadata request timed out") from exc
    # Revalidate session expiry, Origin, capability and identity after body await.
    if _must_check_origin(request) and not _has_allowed_origin(request):
        raise HTTPException(403, "请求来源不受信任。")
    if _must_require_anonymous_session(request) and not _attach_valid_anonymous_session(request):
        raise HTTPException(403, "Anonymous session expired")
    current = await _authorize_task(request, task_id, write=True)
    if current is not record:
        raise HTTPException(404, "任务不存在或无权访问。")
    try:
        payload = MetadataInput.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid task metadata") from exc
    with _operation(record):
        _managed_root(settings, record)
        if record.lifecycle_v2:
            if _draft_service is None:
                raise HTTPException(503, "Draft storage is not initialized")
            _metadata_ledger()
            _draft_service.check(record)
        try:
            return commit_metadata(task_manager, record, payload)
        except OSError as exc:
            raise HTTPException(503, "Task metadata could not be saved") from exc


@app.get("/api/tasks/{task_id}/status", response_model=TaskStatusResponse)
@app.get("/api/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str, request: Request, x_task_token: str | None = Header(default=None)) -> Response:
    record = await _authorize_task(request, task_id)
    if record.lifecycle_v2:
        # Startup initializes/restores this service. A GET must never run its
        # constructor's DDL or interrupted-start recovery writes lazily.
        if _draft_service is None:
            raise HTTPException(503, "Draft storage is not initialized")
        _metadata_ledger()  # verify the already initialized service binding
        payload = _draft_service.view(record)
    else:
        payload = task_manager.status_response(record).model_dump(mode="json")
    from .v2_editing import current_plan
    payload["plan"] = current_plan(record.task_dir)
    etag = '"' + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(payload, headers={"ETag": etag})


@app.get("/api/tasks/{task_id}/report", response_model=ReportResponse)
async def get_report(task_id: str, request: Request, x_task_token: str | None = Header(default=None)) -> JSONResponse:
    record = await _authorize_task(request, task_id)
    root = committed_root(record)
    report_path = root / "report.json"
    if not report_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告尚未生成。")
    from .workbench import _audio_metadata, _safe_report
    return JSONResponse(_safe_report(root, task_id, _audio_metadata(root)))


@app.post("/api/tasks/{task_id}/remix", status_code=status.HTTP_202_ACCEPTED)
async def remix_task(
    task_id: str,
    http_request: Request,
    x_task_token: str | None = Header(default=None),
) -> Any:
    record = await _authorize_task(http_request, task_id, write=True)
    raw = await _bounded_json(http_request)
    from .workbench import EditRequest
    if record.mode_contract or set(raw) != {"keep_sentence_ids"}:
        # Normalize the documented id spelling, preserving the older workbench
        # spelling for existing callers. Never drop unrecognized editing keys.
        converted = dict(raw)
        if "expected_revision" not in converted:
            converted["expected_revision"] = record.revision
        if "keep_sentence_ids" not in converted:
            from .revisions import read_json
            converted["keep_sentence_ids"] = [r["sentence_id"] for r in read_json(record.task_dir, "report.json")["rows"]]
        edit_items = converted.get("edits", [])
        if not isinstance(edit_items, list) or any(not isinstance(item, dict) for item in edit_items):
            raise HTTPException(422, "修改清单无效——请重新检查待应用列表。")
        if any("id" in item and "sentence_id" in item for item in edit_items):
            raise HTTPException(422, "修改清单的句子编号重复。")
        converted["edits"] = [
            {("sentence_id" if key == "id" else key): value for key, value in edit.items()}
            for edit in edit_items
        ]
        try:
            payload = EditRequest.model_validate(converted)
        except ValidationError as exc:
            raise HTTPException(422, "修改参数无效——同一句只选一种修改，原声不能改字。") from exc
        return await _private_endpoint(_workbench_router, "edit")(http_request, task_id, payload)
    try:
        request = RemixRequest.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(422, "请至少保留一句，并检查句子编号。") from exc
    try:
        _, revision = task_manager.start_remix(task_id, request.keep_sentence_ids)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在。") from None
    except RemixValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RemixResponse(task_id=task_id, revision=revision)


@app.post(
    "/api/tasks/{task_id}/replace-shot",
    response_model=ShotReplacementResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replace_shot(
    task_id: str,
    request: ShotReplacementRequest,
    http_request: Request,
    x_task_token: str | None = Header(default=None),
) -> ShotReplacementResponse:
    record = await _authorize_task(http_request, task_id, write=True)
    from .revisions import read_json
    plan = [MatchPlanItem.model_validate(item) for item in read_json(record.task_dir, "match_plan.json")]
    selected = next((item for item in plan if item.sentence_id == request.sentence_id), None)
    if selected is not None and (selected.kind == "quote" or record.mode == "original"):
        raise HTTPException(422, "原声句不能换画面——请剪短、换一段原声或删除。")
    if record.mode_contract:
        from .workbench import EditRequest
        payload = EditRequest(expected_revision=record.revision,
                              keep_sentence_ids=[item.sentence_id for item in plan],
                              edits=[{"sentence_id": request.sentence_id, "instruction": request.instruction}])
        return await _private_endpoint(_workbench_router, "edit")(http_request, task_id, payload)
    try:
        _, revision = task_manager.start_shot_replacement(
            task_id,
            request.sentence_id,
            request.instruction,
        )
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在。") from None
    except ShotReplacementValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ShotReplacementResponse(task_id=task_id, revision=revision)


@app.get("/api/tasks/{task_id}/video")
async def get_video(task_id: str, request: Request, token: str | None = None, download: bool = False) -> FileResponse:
    record = await _authorize_task(request, task_id)
    if download:
        require_publication(record)
    final_path = committed_root(record) / "final.mp4"
    if not final_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成片尚未生成。")
    return FileResponse(
        final_path,
        media_type="video/mp4",
        filename="ai-news-edit.mp4",
        content_disposition_type="attachment" if download else "inline",
        headers={"Accept-Ranges": "bytes"},
    )


@app.get("/api/tasks/{task_id}/thumbs/{shot_id}.jpg")
async def get_thumb(task_id: str, shot_id: int, request: Request, token: str | None = None) -> FileResponse:
    record = await _authorize_task(request, task_id)
    if shot_id < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="shot_id 必须大于或等于 0。")
    thumb_path = record.task_dir / "thumbs" / f"shot_{shot_id}.jpg"
    if not thumb_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="缩略图尚未生成。")
    return FileResponse(thumb_path, media_type="image/jpeg")


@app.get("/api/tasks/{task_id}/poster")
async def get_poster(task_id: str, request: Request) -> FileResponse:
    record = await _authorize_task(request, task_id)
    poster = await public_preview(record)
    await _authorize_task(request, task_id)
    return FileResponse(poster, media_type="image/jpeg")


@app.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: str, request: Request, x_task_token: str | None = Header(default=None)) -> None:
    from .task_manager import TaskDeletionError

    record = await _authorize_task(request, task_id, write=True)
    try:
        if record.lifecycle_v2:
            await _drafts().delete(record)
            deleted = True
        else:
            deleted = await task_manager.cancel_and_delete(task_id)
    except TaskDeletionError as exc:
        raise HTTPException(503, {
            "code": "task_artifact_cleanup_failed",
            "message": str(exc),
        }) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在。")


def _parse_editing_preferences(raw: str | None) -> EditingPreferences:
    if raw is None or not raw.strip():
        return EditingPreferences()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="剪辑要求参数不是合法的 JSON。",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="剪辑要求参数必须是对象。",
        )
    try:
        return EditingPreferences.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"剪辑要求参数无效：{exc.errors()[0].get('msg', '校验失败')}",
        ) from exc


def _client_ip(request: Request) -> str:
    if settings.is_production:
        forwarded_for = request.headers.get("x-forwarded-for", "").strip()
        if forwarded_for:
            try:
                return str(ipaddress.ip_address(forwarded_for))
            except ValueError:
                return "invalid-forwarded-client"
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(client_ip: str) -> None:
    _enforce_rate_limit_identities([client_ip])


def _enforce_task_rate_limits(request: Request) -> None:
    client_ip = _client_ip(request)
    anonymous_session_id = getattr(request.state, "anonymous_session_id", "")
    # Legacy creates/retries cannot spend a second independent copy of the
    # global/IP budget while v2 starts use the durable ledger.
    if _draft_service is not None:
        owner = _draft_owner(request) or ""
        v2 = _draft_service.ledger.counts(owner, owner_digest(client_ip))
        legacy = _legacy_start_counts(owner, client_ip)
        if (v2[0] + legacy[0] >= settings.anonymous_global_task_rate_limit_per_hour
                or v2[2] + legacy[2] >= settings.anonymous_ip_task_rate_limit_per_hour
                or (anonymous_session_id and v2[1] + legacy[1] >= min(2, settings.anonymous_session_task_rate_limit_per_hour))):
            raise HTTPException(429, "Shared task start budget exhausted", headers={"Retry-After": "3600"})
    if anonymous_session_id:
        rules = [
            (f"anonymous-session={anonymous_session_id}", settings.anonymous_session_task_rate_limit_per_hour, "当前浏览器会话"),
            (f"anonymous-ip={client_ip}", settings.anonymous_ip_task_rate_limit_per_hour, "当前网络出口"),
            ("anonymous-global", settings.anonymous_global_task_rate_limit_per_hour, "匿名站点全局"),
        ]
    else:
        rules = [(f"ip={client_ip}", settings.task_rate_limit_per_hour, "当前 IP")]
    _enforce_rate_limit_rules(rules)


def _enforce_rate_limit_identities(identities: list[str]) -> None:
    _enforce_rate_limit_rules(
        [
            (identity, settings.task_rate_limit_per_hour, "当前用户和 IP")
            for identity in identities
        ]
    )


def _enforce_rate_limit_rules(rules: list[tuple[str, int, str]]) -> None:
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=1)
    _prune_rate_counter(window_start)
    unique_rules: dict[str, tuple[int, str]] = {}
    for identity, limit, label in rules:
        previous = unique_rules.get(identity)
        if previous is None or limit < previous[0]:
            unique_rules[identity] = (limit, label)
    unique_identities = list(unique_rules)
    new_identity_count = sum(identity not in rate_counter for identity in unique_identities)
    if len(rate_counter) + new_identity_count > MAX_RATE_LIMIT_CLIENTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求来源数量超过系统保护上限，请稍后再试。",
            headers={"Retry-After": "3600"},
        )
    for identity, (limit, label) in unique_rules.items():
        if len(rate_counter.get(identity, ())) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"{label}每小时最多启动 {limit} 次任务（含重试），"
                    "请稍后再试。"
                ),
                headers={"Retry-After": "3600"},
            )
    for identity in unique_identities:
        rate_counter[identity].append(now)


def _prune_rate_counter(window_start: datetime) -> None:
    for identity, records in list(rate_counter.items()):
        while records and records[0] < window_start:
            records.popleft()
        if not records:
            rate_counter.pop(identity, None)


def _validated_upload_content_length(request: Request) -> int:
    raw_value = request.headers.get("content-length")
    if raw_value is None:
        raise HTTPException(
            status_code=status.HTTP_411_LENGTH_REQUIRED,
            detail="上传请求必须提供 Content-Length。",
        )
    try:
        content_length = int(raw_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Content-Length 无效。",
        ) from exc
    if content_length <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="上传请求体不能为空。",
        )
    if content_length > settings.request_body_limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"上传请求体超过系统总量上限 {settings.max_total_upload_mb} MB。",
        )
    return content_length


def _must_reject_for_drain(request: Request) -> bool:
    if not task_manager.is_draining:
        return False
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    path = request.url.path
    return request.method in {"POST", "PUT", "PATCH"} and path.startswith(("/api/tasks", "/api/uploads", "/api/match"))


def _must_check_origin(request: Request) -> bool:
    if (not settings.enforce_origin_check and not settings.is_production) or request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    path = request.url.path
    return path == "/api/tasks" or path.startswith(("/api/tasks/", "/api/uploads", "/api/match/"))


def _must_require_anonymous_session(request: Request) -> bool:
    return (
        settings.is_production
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and (
            request.url.path == "/api/tasks"
            or request.url.path.startswith(("/api/tasks/", "/api/uploads", "/api/match/"))
        )
    )


def _attach_valid_anonymous_session(request: Request) -> bool:
    session = _anonymous_session(request)
    if session is None or not csrf_token_matches(
        session,
        request.headers.get(ANONYMOUS_CSRF_HEADER),
    ):
        return False
    request.state.anonymous_session_id = session.session_id
    return True


def _anonymous_session(request: Request) -> AnonymousSession | None:
    return verify_anonymous_session(
        request.cookies.get(ANONYMOUS_SESSION_COOKIE),
        settings.anonymous_session_secret.get_secret_value(),
        settings.anonymous_session_ttl_seconds,
    )


def _has_allowed_origin(request: Request) -> bool:
    origins = request.headers.getlist("origin")
    return trusted_local_request(request, settings, mutation=True) or (
        len(origins) == 1 and origins[0] in settings.cors_origins
    )


def _request_is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").split(",", maxsplit=1)[0].strip() == "https"


def _require_loopback(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if not settings.is_production and client_host == "testclient":
        return
    try:
        is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在。")


def _is_invalid_request_path(path: str) -> bool:
    if len(path) > 2048:
        return True
    invalid_windows_characters = '<>:"|?*\\'
    return any(ord(character) < 32 or character in invalid_windows_characters for character in path)


app.include_router(create_draft_router(_drafts, _authorize_private))


@app.post("/api/tasks/{task_id}/retry", status_code=202)
async def retry_task_v2(task_id: str, request: Request) -> Any:
    record = await _authorize_operation(request, task_id, write=True)
    raw = await _bounded_json(request)
    if record.lifecycle_v2:
        return await _drafts().retry(record, raw)
    # Preserve the legacy endpoint's revision checks and charged retry policy.
    from .task_operations import RevisionInput
    try:
        payload = RevisionInput.model_validate(raw)
    except ValidationError:
        raise HTTPException(422, "Invalid retry parameters") from None
    return await _legacy_retry_endpoint(task_id, request, payload)


_workbench_router = create_workbench_router(settings, task_manager, _authorize_private, _authorize_workbench_mutation)
_studio_router = create_studio_router(settings, task_manager, _authorize_studio)
app.include_router(_workbench_router)
app.include_router(_studio_router)
from .v2_editing import create_v2_editing_router


async def _legacy_export_status(request: Request, task_id: str, export_id: str) -> Any:
    return await _private_endpoint(_studio_router, "get_job")(request, task_id, export_id)


_v2_editing_router = create_v2_editing_router(settings, task_manager, _authorize_private,
    _authorize_workbench_mutation, legacy_export_status=_legacy_export_status)
app.include_router(_v2_editing_router)
app.include_router(create_upload_router(_LazyUploads()))
_operations_router = create_task_operations_router(settings, task_manager, _authorize_operation,
                                                 check_rate_limit=_enforce_task_rate_limits)
_legacy_retry_endpoint = next(route.endpoint for route in _operations_router.routes if route.name == "retry")
_operations_router.routes[:] = [route for route in _operations_router.routes if route.name != "retry"]
app.include_router(_operations_router)


def _private_endpoint(router: Any, name: str):
    """Reuse the authorized revision transaction, not a second HTTP request."""
    return next(route.endpoint for route in router.routes if route.name == name)


class CheckConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    checked_keys: list[str] | None = Field(default=None, max_length=2000)
    checked: dict[str, bool] | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def normalize_checks(self):
        if (self.checked_keys is None) == (self.checked is None):
            raise ValueError("Supply exactly one check representation")
        if self.checked is not None:
            self.checked_keys = [key for key, value in self.checked.items() if value]
        if len(set(self.checked_keys)) != len(self.checked_keys):
            raise ValueError("Duplicate check keys")
        return self


@app.get("/api/tasks/{task_id}/checks")
async def task_checks(task_id: str, request: Request) -> dict[str, Any]:
    return publication_gate(await _authorize_task(request, task_id))


@app.post("/api/tasks/{task_id}/checks")
@app.put("/api/tasks/{task_id}/checks")
async def check_confirmation(task_id: str, request: Request) -> dict[str, Any]:
    record = await _authorize_task(request, task_id, write=True)
    try:
        payload = CheckConfirmation.model_validate(await _bounded_json(request))
    except ValidationError as exc:
        raise HTTPException(422, "检查记录无效，请刷新当前版本后重新勾选。") from exc
    if await _authorize_task(request, task_id, write=True) is not record:
        raise HTTPException(403, "Task authorization changed")
    if record.status != TaskState.done or (record.background is not None and not record.background.done()):
        raise HTTPException(409, "Task is busy")
    return confirm_checks(record, payload.expected_revision, payload.checked_keys)


@app.get("/api/tasks/{task_id}/quotes/{row_id}/takes")
@app.get("/api/tasks/{task_id}/quote/{row_id}/takes")
async def quote_takes(task_id: str, row_id: int, request: Request) -> dict[str, Any]:
    record = await _authorize_task(request, task_id)
    from .revisions import read_json
    plan = [MatchPlanItem.model_validate(item) for item in read_json(committed_root(record), "match_plan.json")]
    row = next((item for item in plan if item.sentence_id == row_id and item.kind == "quote"), None)
    if row is None:
        raise HTTPException(404, "这句原声不存在。")
    return {"takes": [take.model_dump(mode="json") for take in row.alt_takes]}


@app.get("/api/tasks/{task_id}/waves/{row_id}.json")
@app.get("/api/tasks/{task_id}/waveform/{row_id}")
async def quote_wave(task_id: str, row_id: int, request: Request) -> dict[str, Any]:
    record = await _authorize_task(request, task_id)
    from .revisions import read_json, local_file
    from .pipeline import _run_blocking_until_complete
    root = committed_root(record)
    timings = [SentenceTiming.model_validate(item) for item in read_json(root, "timings.json")]
    timing = next((item for item in timings if item.sentence_id == row_id), None)
    plan = [MatchPlanItem.model_validate(item) for item in read_json(root, "match_plan.json")]
    row = next((item for item in plan if item.sentence_id == row_id), None)
    if timing is None or row is None or row.source is None or row.kind != "quote":
        raise HTTPException(404, "这句原声的波形尚未生成。")
    audio = local_file(root, timing.audio_path)
    actual_start, actual_end = row.source.start, row.source.end
    if (root / "production_mode.json").is_file():
        from .mode_pipeline import mode_report_metadata
        actual = mode_report_metadata(root).get("metrics", {}).get("quote_actual_ranges", {}).get(str(row_id), {}).get("actual_range", {})
        if actual:
            actual_start, actual_end = actual["start"], actual["end"]
    def measure():
        import array
        import math
        import sys
        import wave
        values = []
        with wave.open(str(audio), "rb") as stream:
            if stream.getsampwidth() != 2 or stream.getnframes() > 60 * stream.getframerate():
                raise HTTPException(409, "这段原声暂时不能显示波形，请先试听。")
            while chunk := stream.readframes(max(1, round(stream.getframerate() * .02))):
                samples = array.array("h", chunk)
                if sys.byteorder != "little":
                    samples.byteswap()
                values.append(math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768)
        return values
    samples = await _run_blocking_until_complete(measure)
    await _authorize_task(request, task_id)
    return {"interval_ms": 20, "samples": samples, "start": actual_start, "end": actual_end}


class SimpleExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fmt: Literal["mp4", "mp3", "gif"] = "mp4"
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    res: Literal["360p", "720p", "1080p"] = "1080p"
    sub: Literal["std", "big", "none"] = "std"


@app.post("/api/tasks/{task_id}/export", status_code=202)
async def export_task(task_id: str, request: Request) -> Any:
    record = await _authorize_task(request, task_id, write=True)
    require_publication(record)
    try:
        payload = SimpleExportRequest.model_validate(await _bounded_json(request))
    except ValidationError as exc:
        raise HTTPException(422, "导出选项无效，请重新选择格式、画幅和字幕。") from exc
    await _authorize_task(request, task_id, write=True)
    from .studio import read_state
    state = read_state(record.task_dir)
    body = json.dumps({"expected_revision": state["revision"], "options": {
        "format": payload.fmt,
        "aspect": "16:9" if payload.fmt == "mp3" else payload.aspect,
        "resolution": 1080 if payload.fmt == "mp3" else int(payload.res[:-1]),
        "subtitles": "standard" if payload.fmt == "mp3" else {"std": "standard", "big": "large", "none": "none"}[payload.sub],
    }}).encode()
    scope = dict(request.scope)
    # This is now an internal Studio request, including its authorization
    # identity. Keeping /export here makes the post-prepare authorization
    # reject Studio's own busy reservation as an unrelated pipeline mutation.
    scope["path"] = f"/api/tasks/{task_id}/studio/export"
    scope["raw_path"] = scope["path"].encode("ascii")
    scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k.lower() not in {b"content-length", b"content-type"}] + [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    adapted = Request(scope, receive)
    result = await _private_endpoint(_studio_router, "export")(adapted, task_id)
    if isinstance(result, dict):
        return {**result, "export_id": result["id"]}
    if isinstance(result, JSONResponse) and result.status_code == 202:
        receipt = json.loads(bytes(result.body))
        receipt["export_id"] = receipt["id"]
        return JSONResponse(receipt, status_code=result.status_code,
                            headers={key: value for key, value in result.headers.items()
                                     if key.lower() not in {"content-length", "content-type"}},
                            background=result.background)
    return result


async def export_status(task_id: str, export_id: str, request: Request) -> Any:
    return await _private_endpoint(_studio_router, "get_job")(request, task_id, export_id)


def _openapi_document() -> dict[str, Any]:
    from fastapi.openapi.utils import get_openapi
    if app.openapi_schema is not None:
        return app.openapi_schema
    document = get_openapi(title="金话筒 · 三种制作模式", version="2.0.0", routes=app.routes)
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    def promote(value: Any) -> None:
        if isinstance(value, dict):
            definitions = value.pop("$defs", {})
            for name, definition in definitions.items():
                promote(definition)
                schemas.setdefault(name, definition)
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                value["$ref"] = reference.replace("#/$defs/", "#/components/schemas/")
            for child in list(value.values()):
                promote(child)
        elif isinstance(value, list):
            for child in value:
                promote(child)
    promote(document)
    from .workbench import EditRequest
    for route, model in (("/api/match/preview", MatchPreviewRequest),
                         ("/api/tasks/{task_id}/remix", EditRequest),
                         ("/api/tasks/{task_id}/checks", CheckConfirmation),
                         ("/api/tasks/{task_id}/export", SimpleExportRequest)):
        schema = model.model_json_schema()
        promote(schema)
        document["paths"][route]["post"]["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema}}}
    app.openapi_schema = document
    return document


app.openapi = _openapi_document


@app.get("/api/samples/default")
@app.get("/api/samples/default/video")
async def default_sample(request: Request) -> Any:
    from .v2_editing import packaged_sample
    from .pipeline import _run_blocking_until_complete
    return await _run_blocking_until_complete(lambda: packaged_sample(request.url.path.endswith("/video")))


@app.api_route("/api/{unmatched_path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
async def missing_api(unmatched_path: str) -> None:
    # Never send the static frontend (or StaticFiles' method error) for a
    # removed login/management endpoint or another unknown API request.
    raise HTTPException(404, "接口不存在。")


frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", FrontendStaticFiles(directory=frontend_dist, html=True), name="frontend")
