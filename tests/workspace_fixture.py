"""Synthetic-only workspace fixtures. Importing this module starts nothing.

The HTTP shapes follow test_m6_e2e; all ten product stages, revision snapshots,
subtitle validation and QC are real. Vision/embeddings/reranking are deterministic
test responses, NOT model-quality evidence. TTS is a tone, NOT intelligible speech
or word-alignment evidence. No accounts, historical task copies or dotenv imports.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import threading
import unicodedata
import wave
from collections import Counter
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Iterator
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = "合成色卡验收样片\n色块画面缓缓移动。\n测试图案清晰可见。"
EDITED_SENTENCE = "色块图案继续移动。"
DISCLOSURE = (
    "SYNTHETIC ACCEPTANCE ONLY: FFmpeg test patterns and tone audio; deterministic "
    "loopback fake Vision/Embedding/LLM/TTS responses. Real ten-stage pipeline, "
    "media, subtitles, revisions and QC. No speech intelligibility, word timing, "
    "model accuracy, accounts, external services or production-readiness claim."
)
REQUIRED_ARTIFACTS = (
    "upload_manifest.json", "pipeline_manifest.json", "asr_transcripts.json",
    "shots.json", "shots_annotated.json", "script_structure.json",
    "script_segmented.txt", "sentences.json", "match_plan.json",
    "pronunciation_plan.json", "narration_profile.json", "timings.json",
    "source_timings.json", "source_edl.json", "edl.json", "segment_manifest.json",
    "narration.m4a", "subs.ass", "subtitle_manifest.json", "video_only.mp4",
    "final.mp4", "quality_report.json", "report.json", "task_state.json",
)


class SafetyError(RuntimeError):
    """Deliberately generic: never include request bodies, tokens or credentials."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise SafetyError(code)


def unlinked(path: Path, *, exists: bool = True) -> Path:
    """Reject links/junctions at every existing component, including Windows roots."""
    absolute = path.absolute()
    for part in (absolute, *absolute.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            require(not exists or part != absolute, "missing_path")
            continue
        require(not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, "st_file_attributes", 0) & 0x400), "linked_path")
    require(absolute.resolve() == absolute, "noncanonical_path")
    return absolute


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with unlinked(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_frontend(path: Path) -> tuple[Path, dict[str, str]]:
    path = unlinked(path)
    require(path.parent == PROJECT_ROOT / "frontend"
            and re.fullmatch(r"dist(?:-[a-zA-Z0-9_-]+)?", path.name) is not None,
            "frontend_must_be_dist_child")
    # Inspect only this build, never neighboring builds or historical evidence.
    hashes: dict[str, str] = {}
    for line in (path / "ASSET_MANIFEST.sha256").read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})  (index\.html|assets/[a-zA-Z0-9_./-]+)", line)
        require(match is not None, "invalid_build_manifest")
        assert match is not None
        name = match[2]
        require(".." not in name.split("/") and name not in hashes, "invalid_build_entry")
        asset = unlinked(path / name)
        require(asset.is_relative_to(path) and sha256(asset) == match[1].lower(), "build_hash_mismatch")
        hashes[name] = match[1].lower()
    require("index.html" in hashes and len(hashes) >= 3, "incomplete_build")
    return path, hashes


def isolated_environment(root: Path) -> dict[str, str]:
    keep = {"systemroot", "windir", "comspec", "pathext", "path", "userprofile",
            "localappdata", "appdata", "systemdrive", "number_of_processors"}
    result = {key: value for key, value in os.environ.items() if key.lower() in keep}
    result.update(TEMP=str(root / "tmp"), TMP=str(root / "tmp"), TMPDIR=str(root / "tmp"),
                  APP_ENV="test", PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1",
                  PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                  XDG_CACHE_HOME=str(root / "cache"), HF_HOME=str(root / "cache" / "hf"),
                  MPLCONFIGDIR=str(root / "cache" / "matplotlib"), NUMBA_CACHE_DIR=str(root / "cache" / "numba"))
    return result


def media_command(arguments: list[str], *, content: bytes | None = None) -> bytes:
    result = subprocess.run(arguments, input=content, capture_output=True, timeout=120, check=False)
    require(result.returncode == 0, "synthetic_media_command_failed")
    return result.stdout


def create_inputs(root: Path) -> list[dict[str, Any]]:
    require(bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe")), "ffmpeg_and_ffprobe_required")
    directory = root / "inputs"
    directory.mkdir()
    result: list[dict[str, Any]] = []
    for index in range(4):
        path = directory / f"synthetic-pattern-{index + 1}.mp4"
        media_command([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4",
            "-vf", f"hue=h={index * 70}", "-an", "-c:v", "libx264", "-threads", "1",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
        ])
        result.append({"path": str(path), "name": path.name, "bytes": path.stat().st_size,
                       "sha256": sha256(path), "durationSeconds": 4, "synthetic": True})
    return result


def tone_mp3(text: str) -> bytes:
    units = len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", unicodedata.normalize("NFKC", text)))
    require(1 <= units <= 120, "synthetic_tts_text_out_of_scope")
    # Length follows the real normal-pacing target; no fabricated word cues.
    frames = round(max(0.65, units * 60 / 265) * 48000)
    pcm = bytearray()
    for index in range(frames):
        envelope = min(1.0, index / 480, (frames - 1 - index) / 480)
        pcm.extend(struct.pack("<h", round(6000 * envelope * math.sin(2 * math.pi * 440 * index / 48000))))
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(pcm)
    return media_command([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-protocol_whitelist", "file,pipe", "-f", "wav", "-i", "pipe:0",
        "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1",
    ], content=output.getvalue())


class FakeProvider(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.lock = threading.Lock()
        self.audio: dict[str, bytes] = {}
        super().__init__(("127.0.0.1", 0), FakeHandler)

    def server_bind(self) -> None:
        # HTTPServer normally performs reverse DNS even for a loopback bind.
        # There is no DNS exception here or before the guards are installed.
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def count(self, key: str) -> None:
        with self.lock:
            self.counts[key] += 1

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            return dict(self.counts)

    def handle_error(self, request: Any, client_address: Any) -> None:
        self.count("handler_error")  # Never print raw HTTP requests or exception chains.


class FakeHandler(BaseHTTPRequestHandler):
    server: FakeProvider

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            require(0 < length <= 8 * 1024 * 1024, "fake_request_size")
            payload = json.loads(self.rfile.read(length))
            model = payload.get("model")
            if self.path == "/v1/embeddings" and model == "workspace-embedding":
                self.server.count("embedding")
                self.send_json({"data": [{"index": i, "embedding": [1.0, 0.5, 0.25]}
                                         for i, _ in enumerate(payload["input"])]})
            elif self.path == "/v1/audio/speech" and model == "workspace-tone-tts":
                text = payload["input"]
                self.server.count("tts")
                with self.server.lock:
                    if text not in self.server.audio:
                        self.server.audio[text] = tone_mp3(text)
                    audio = self.server.audio[text]
                self.send_bytes(audio, "audio/mpeg")
            elif self.path == "/v1/chat/completions" and model == "workspace-vision":
                self.server.count("vision")
                self.chat({"description": "合成色块画面缓缓移动，测试图案清晰可见",
                           "scene_type": "unknown", "subjects": ["色块画面", "测试图案", "色块图案"],
                           "actions": ["缓缓移动", "继续移动", "清晰可见"],
                           "keywords": ["色块画面", "测试图案", "色块图案", "合成", "移动", "图案"],
                           "ocr_texts": [], "entities": ["色块画面", "测试图案", "色块图案"],
                           "quality": {"sharp": 0.9, "bright": 0.8}})
            elif self.path == "/v1/chat/completions" and model == "workspace-segmentation":
                self.server.count("segmentation")
                text = payload["messages"][1]["content"]
                # Only insert delimiters; preserve the actual manuscript verbatim.
                self.chat(re.sub(r"([。！？])(?=[^\s])", r"\1 / ", text))
            elif self.path == "/v1/chat/completions" and model == "workspace-rerank":
                self.server.count("llm")
                content = payload["messages"][1]["content"]
                if payload.get("max_tokens") == 16:
                    self.chat("neutral")
                    return
                items = json.loads(content)["sentences"]
                used: set[int] = set()
                decisions = []
                for item in items:
                    candidates = sorted(candidate["shot_id"] for candidate in item["candidates"])
                    selected = next(value for value in candidates if value not in used)
                    used.add(selected)
                    decisions.append({"sentence_id": item["sentence_id"], "beat_id": item.get("beat_id", 0),
                                      "shot_id": selected, "confidence": 0.95,
                                      "alternates": [value for value in candidates if value != selected][:3]})
                self.chat(decisions)
            else:
                self.server.count("unexpected")
                self.send_json({"error": "unsupported_synthetic_request"}, 422)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self.server.count("unexpected")
            self.send_json({"error": "invalid_synthetic_request"}, 422)

    def chat(self, value: Any) -> None:
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        self.send_json({"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    def send_json(self, value: Any, status: int = 200) -> None:
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json", status)

    def send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextmanager
def fake_provider() -> Iterator[FakeProvider]:
    server = FakeProvider()
    thread = threading.Thread(target=server.serve_forever, name="workspace-fake-provider", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        require(not thread.is_alive(), "fake_provider_shutdown_incomplete")


class NetworkGuard:
    """Exact fake-listener HTTP allowlist + deny-all WS + process socket audit.

    Accepted inbound connections are unaffected. Windows asyncio's *own* local
    socketpair construction is exempt only while the original socketpair runs,
    on its current thread. No arbitrary loopback port (especially 8000) is allowed.
    This is Python/provider egress confinement, not an OS firewall for native code.
    FFmpeg only receives bounded synthetic/local files through the real pipeline.
    """

    def __init__(self, port: int, root: Path, manifest: Path) -> None:
        self.port, self.root, self.manifest = port, root, manifest
        self.denied = 0
        self.local = threading.local()

    def deny(self) -> None:
        self.denied += 1
        raise SafetyError("workspace_egress_or_file_boundary_denied")

    def endpoint(self, host: Any, port: Any) -> bool:
        return host in ("127.0.0.1", b"127.0.0.1") and port == self.port

    def file_boundary(self, value: Any, *, write: bool) -> None:
        if not isinstance(value, (str, bytes, os.PathLike)):
            return
        raw = os.fsdecode(value)
        if os.path.normcase(raw) == os.path.normcase(os.devnull):
            return
        # resolve() makes dot/.. aliases unable to bypass protected data roots.
        path = Path(raw).absolute().resolve()
        if path.name == ".env" or path.name.startswith(".env."):
            self.deny()
        if any(path.is_relative_to(PROJECT_ROOT / directory)
               for directory in ("data", "eval_sample", "canary_test/artifacts")):
            self.deny()
        if write and not path.is_relative_to(self.root) and path != self.manifest:
            self.deny()

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        pair = getattr(self.local, "socketpair", False)
        if event == "socket.connect":
            address = args[1]
            if not isinstance(address, tuple) or len(address) < 2:
                self.deny()
            if pair and address[0] in ("127.0.0.1", "::1"):
                return
            if not self.endpoint(address[0], address[1]):
                self.deny()
        elif event == "socket.getaddrinfo":
            if not self.endpoint(args[0], args[1]) and not (pair and args[0] in ("127.0.0.1", "::1")):
                self.deny()
        elif event in {"socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg"}:
            self.deny()
        elif event == "socket.bind":
            # Windows ConnectEx binds an outbound socket before connecting.
            # Exempt only the exact socket whose destination was checked below.
            if not pair and args[0] is not getattr(self.local, "outbound_socket", None):
                self.deny()  # Both owned listeners were exclusively bound before installation.
        elif event == "open":
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            self.file_boundary(args[0], write=bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        elif event in {"os.listdir", "os.scandir"}:
            self.file_boundary(args[0], write=False)
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime"}:
            self.file_boundary(args[0], write=True)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.file_boundary(args[0], write=True)
            self.file_boundary(args[1], write=True)

    def install(self, stack: ExitStack) -> None:
        original_pair = socket.socketpair

        def pair(*args: Any, **kwargs: Any) -> Any:
            previous = getattr(self.local, "socketpair", False)
            self.local.socketpair = True
            try:
                return original_pair(*args, **kwargs)
            finally:
                self.local.socketpair = previous

        stack.enter_context(patch.object(socket, "socketpair", pair))
        sys.addaudithook(self.audit)
        if sys.platform == "win32":
            from asyncio.windows_events import IocpProactor
            original_connect = IocpProactor.connect

            def connect(proactor: Any, sock: socket.socket, address: Any) -> Any:
                if not isinstance(address, tuple) or not self.endpoint(address[0], address[1]):
                    self.deny()
                previous = getattr(self.local, "outbound_socket", None)
                self.local.outbound_socket = sock
                try:
                    return original_connect(proactor, sock, address)
                finally:
                    self.local.outbound_socket = previous

            stack.enter_context(patch.object(IocpProactor, "connect", connect))

        import httpx
        import websockets
        import websockets.asyncio.client
        import websockets.sync.client
        import websockets.legacy.client
        import aiohttp

        def check(request: httpx.Request) -> None:
            if (request.url.scheme != "http" or not self.endpoint(request.url.raw_host, request.url.port)
                    or request.url.userinfo):
                self.deny()

        original_sync = httpx.HTTPTransport.handle_request
        original_async = httpx.AsyncHTTPTransport.handle_async_request
        original_send = httpx.Client.send
        original_async_send = httpx.AsyncClient.send

        def send(client: Any, request: httpx.Request, **kwargs: Any) -> httpx.Response:
            check(request)
            return original_send(client, request, **kwargs)

        async def send_async(client: Any, request: httpx.Request, **kwargs: Any) -> httpx.Response:
            check(request)
            return await original_async_send(client, request, **kwargs)

        def handle(transport: Any, request: httpx.Request) -> httpx.Response:
            check(request)
            return original_sync(transport, request)

        async def handle_async(transport: Any, request: httpx.Request) -> httpx.Response:
            check(request)
            return await original_async(transport, request)

        def no_websocket(*args: Any, **kwargs: Any) -> Any:
            self.deny()

        original_aiohttp = aiohttp.ClientSession._request

        async def request_aiohttp(session: Any, method: str, url: Any, **kwargs: Any) -> Any:
            # Covers edge-tts/aiohttp too, including numeric-IP Proactor connects
            # that need not pass through socket.socket.connect on Windows.
            check(httpx.Request(method, str(url)))
            require(not kwargs.get("proxy"), "provider_proxy_not_allowed")
            return await original_aiohttp(session, method, url, **kwargs)

        stack.enter_context(patch.object(httpx.HTTPTransport, "handle_request", handle))
        stack.enter_context(patch.object(httpx.AsyncHTTPTransport, "handle_async_request", handle_async))
        stack.enter_context(patch.object(httpx.Client, "send", send))
        stack.enter_context(patch.object(httpx.AsyncClient, "send", send_async))
        stack.enter_context(patch.object(aiohttp.ClientSession, "_request", request_aiohttp))
        stack.enter_context(patch.object(aiohttp.ClientSession, "_ws_connect", no_websocket))
        stack.enter_context(patch.object(websockets, "connect", no_websocket))
        for module in (websockets.asyncio.client, websockets.sync.client, websockets.legacy.client):
            stack.enter_context(patch.object(module, "connect", no_websocket))
        # backend.providers.asr captures connect as a default argument at import;
        # install BEFORE importing any backend module other than config.


def make_settings(root: Path, base_url: str, fake_port: int) -> Any:
    from backend.config import Settings

    values = {name: field.get_default(call_default_factory=True) for name, field in Settings.model_fields.items()}
    provider_url = f"http://127.0.0.1:{fake_port}/v1"
    for name in values:
        if re.search(r"(?:api_keys?|app_key|app_id|access_token|secret|password|credential)", name):
            values[name] = ""
        if name.endswith("_base_url"):
            values[name] = provider_url
    values.update(
        app_env="test", enable_api_docs=False, data_dir=root / "tasks",
        asr_cache_dir=root / "cache" / "asr", asr_cache_enabled=False,
        music_library_dir=PROJECT_ROOT / "backend" / "assets" / "music",
        sync_sound_enabled=False, sync_sound_vad_enabled=False,
        video_embedding_enabled=False, entity_verification_enabled=False,
        generative_fill_enabled=False, task_ttl_hours=0, min_free_disk_gb=0,
        max_concurrent_tasks=1, max_pending_tasks=4, max_files=4,
        max_upload_mb=8, max_total_upload_mb=32, max_source_duration_seconds_per_file=10,
        max_total_source_duration_seconds=40, max_sentences=8,
        task_rate_limit_per_hour=30, media_command_timeout_seconds=120,
        shutdown_grace_seconds=30, quality_gate_mode="block",
        frontend_origins=base_url, allowed_hosts="127.0.0.1", enforce_origin_check=True,
        vision_provider="openai-compatible", vision_model="workspace-vision",
        embedding_provider="openai-compatible", embed_model="workspace-embedding",
        llm_provider="openai-compatible", llm_model="workspace-rerank",
        kimi_model="workspace-segmentation", tts_provider="openai",
        tts_model="workspace-tone-tts", tts_voice="synthetic-tone-not-speech",
        volcengine_asr_base_url=f"ws://127.0.0.1:{fake_port}/disabled-asr",
    )
    for name in ("vision_api_key", "embed_api_key", "llm_api_key", "kimi_api_key", "tts_api_key"):
        values[name] = "synthetic-placeholder-not-a-credential"
    # Every field is explicit and the process environment has already been cleared.
    return Settings(_env_file=None, **values)


async def seed_completed(manager: Any, root: Path, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    from uuid import uuid4
    from backend.models import EditingPreferences, TaskState, UploadedAsset
    from backend.script_segmentation import prepare_headline_script

    task_id = uuid4().hex
    task_dir = root / "tasks" / task_id
    (task_dir / "raw").mkdir(parents=True)
    uploads = []
    for item in inputs:
        # Use the same unguessable stored-name shape as real upload admission;
        # stage 1 deliberately rejects arbitrary fixture basenames.
        target = task_dir / "raw" / f"{uuid4().hex}.mp4"
        shutil.copyfile(item["path"], target)
        uploads.append(UploadedAsset(original_name=item["name"], stored_name=target.name,
                                     path=target, size=target.stat().st_size, content_type="video/mp4"))
    record = manager.add_task(task_id, task_dir, prepare_headline_script(SCRIPT), uploads,
                              preferences=EditingPreferences())
    await asyncio.wait_for(record.background, timeout=600)
    require(record.status == TaskState.done and record.progress == 100, "synthetic_seed_pipeline_failed")
    require(len(record.stages) == 10 and all(stage.status == "done" for stage in record.stages), "incomplete_ten_stages")
    for name in REQUIRED_ARTIFACTS:
        require((task_dir / name).is_file() and (task_dir / name).stat().st_size > 0, "missing_seed_artifact")
    quality = json.loads((task_dir / "quality_report.json").read_text(encoding="utf-8"))
    require(quality["blocking_issue_count"] == 0
            and not any(issue["severity"] == "error" for issue in quality["issues"]), "seed_qc_blocked")
    probe = json.loads(await asyncio.to_thread(media_command, [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format",
        "-of", "json", str(task_dir / "final.mp4"),
    ]))
    video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
    require(video["codec_name"] == "h264" and video["width"] == 1920 and video["height"] == 1080,
            "seed_video_not_validated")
    require(any(stream["codec_type"] == "audio" for stream in probe["streams"]), "seed_audio_missing")
    snapshot = root / "original-seed-snapshot"
    await asyncio.to_thread(shutil.copytree, task_dir, snapshot)
    snapshot_hashes = {path.relative_to(snapshot).as_posix(): sha256(path)
                       for path in sorted(snapshot.rglob("*")) if path.is_file()}
    return {"taskId": task_id, "revision": 0, "durationSeconds": float(probe["format"]["duration"]),
            "blockingIssues": 0, "stageCount": 10, "snapshotDirectory": str(snapshot),
            "snapshotHashes": snapshot_hashes,
            "artifactHashes": {name: sha256(task_dir / name) for name in REQUIRED_ARTIFACTS}}