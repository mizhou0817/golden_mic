"""Real-main three-mode HTTP contracts in ONE credential-free child process.

Discovery imports only the standard library. The child patches get_settings
before importing main, never enters lifespan, and uses async ASGITransport.
All persistence is real and TEMP-owned, including UploadStore chunks, hashes,
capabilities, materialization, TaskManager and workbench/publication validation.
Only the codec command boundary is synthetic; saved transcripts are explicitly
labelled fixtures, not ASR evidence. No paid providers, sockets or codecs run.

Run this file or ``python -B -m unittest tests.test_mode_api -v``. Contract gaps
are ordinary failures (NOT TODO/xfail); the owning agent must fix product code.
"""
from __future__ import annotations

# Deliberately inspect real private persistence/worker state in isolated tests.
# pyright: reportPrivateUsage=false

import asyncio
import base64
import hashlib
import importlib
import json
import logging
import os
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
CHILD_FLAG = "--isolated-mode-api-child"
FAKE_KEY = "mode-api-synthetic-key-not-a-live-credential"
TOKENS: list[str] = [FAKE_KEY]
TEXT = "今天活动开幕。"


def _file_boundary(value, *, write: bool, owned: Path) -> None:
    """Validate comparison paths only; never rewrite the actual I/O argument."""
    raw = os.fsdecode(value)
    extended = False

    def deny(label):
        raise AssertionError("Isolation boundary: " + label)

    if os.name == "nt":
        if raw.startswith(("\\\\", "//", "\\??\\")):
            if re.match(r"^\\\\\?\\[A-Za-z]:\\", raw) is None:
                deny("non-local Windows namespace")
            extended = True
            raw = raw[4:]
            parts = raw[3:].split("\\")
            if any(not part for part in parts) or "/" in raw:
                deny("ambiguous extended path")
        else:
            drive, tail = os.path.splitdrive(raw)
            if drive and (not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith(("\\", "/"))):
                deny("drive-relative path")
            parts = re.split(r"[\\/]", tail)
        if any(part and (part in {".", ".."} or part.endswith((".", " "))
                         or any(ord(c) < 32 or c in ':<>"|?*' for c in part)
                         or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part))
               for part in parts):
            deny("unsafe Windows path component")
    elif ".." in raw.split("/"):
        deny("path traversal")
    path = Path(raw).absolute()
    protected = tuple(PROJECT / name for name in ("data", "eval", "eval_sample", "canary_test/artifacts"))

    def check(candidate):
        if any(part.lower() == ".env" or part.lower().startswith(".env.") for part in candidate.parts):
            deny("dotenv access")
        if any(candidate.is_relative_to(root) for root in protected):
            deny("real-data/historical evidence access")
        # Extended spelling never grants new read privileges outside owned TEMP.
        if (write or extended) and not candidate.is_relative_to(owned):
            deny("write outside owned TEMP" if write else "extended read outside owned TEMP")

    check(path)
    if write or extended or path.is_relative_to(owned):
        # Check every ancestor, including dangling links and in-root junctions.
        # Keep extended spelling for stat so long paths are actually inspected.
        cursor = Path(os.fsdecode(value)) if extended else path
        for entry in (cursor, *cursor.parents):
            try:
                info = entry.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                deny("linked/reparse path")
    check(path.resolve())


def _audit_file_boundary(event, args, owned: Path) -> None:
    paths = []
    writing = False
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        paths = [args[0]]
        mode, flags = args[1], args[2]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)))
    elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "shutil.rmtree"}:
        paths = list(args[:2] if event == "os.rename" else args[:1])
        writing = True
    for value in paths:
        if isinstance(value, (str, bytes, os.PathLike)):
            _file_boundary(value, write=writing, owned=owned)


def _redact(value: object) -> str:
    text = str(value)
    for token in TOKENS:
        text = text.replace(token, "[synthetic-capability]")
    if "backend.storage" in sys.modules:
        text = sys.modules["backend.storage"].sanitize_sensitive_text(text)
    return text


class _RedactingStream:
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        return self.stream.write(_redact(text))

    def flush(self):
        self.stream.flush()


class ModeAPIIsolationTest(unittest.TestCase):
    def test_real_main_contracts_in_sanitized_child(self):
        with tempfile.TemporaryDirectory(prefix="golden-mic-mode-api-") as directory:
            root = Path(directory).resolve()
            working = root / "working"
            working.mkdir()
            scratch = root / "tmp"
            scratch.mkdir()
            retained = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
                        "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}
            environment = {k: v for k, v in os.environ.items() if k.upper() in retained}
            environment.update(PYTHONPATH=str(PROJECT), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                               PYTHONDONTWRITEBYTECODE="1", MODE_API_TEMP=str(root),
                               APP_ENV="test", DATA_DIR=str(root / "unused-environment-data"),
                               TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
            result = subprocess.run(
                [sys.executable, "-B", "-m", "tests.test_mode_api", CHILD_FLAG],
                # Stream redacted case names/failures to the owning runner so
                # interruption cannot hide all progress behind communicate().
                cwd=working, env=environment, stdout=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace", timeout=180, check=False,
            )
            output = _redact(result.stdout)
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("mode_api_isolation=passed", output)


class _GuardContracts(unittest.TestCase):
    """Decision-only negative checks: never open protected paths or make links."""

    def setUp(self):
        self.owned = Path(os.environ["MODE_API_TEMP"]).resolve()
        self.target = self.owned / "tasks/revisions/r0/file.json"

    def spellings(self, path):
        yield str(path)
        yield path
        yield os.fsencode(path)
        if os.name == "nt":
            extended = "\\\\?\\" + str(path)
            yield extended
            yield Path(extended)
            yield os.fsencode(extended)

    def test_owned_normal_and_long_extended_comparisons(self):
        for path in (self.target, self.target.parent / ("x" * 180) / ("y" * 80) / "file.json"):
            for value in self.spellings(path):
                for write in (False, True):
                    with self.subTest(path=str(value), write=write):
                        _file_boundary(value, write=write, owned=self.owned)

    def test_dotenv_real_data_and_history_denied_for_read_and_write(self):
        paths = [self.owned / ".env", self.owned / ".env.test", PROJECT / ".env",
                 *(PROJECT / name / "guard-never-open.json" for name in
                   ("data", "eval", "eval_sample", "canary_test/artifacts"))]
        for path in paths:
            for value in self.spellings(path):
                for write in (False, True):
                    with self.subTest(path=str(value), write=write), self.assertRaises(AssertionError):
                        _file_boundary(value, write=write, owned=self.owned)

    def test_sibling_prefix_and_outside_temp_are_not_owned(self):
        for path in (self.owned.with_name(self.owned.name + "-sibling") / "file.json",
                     self.owned.parent / "outside-owned-temp/file.json"):
            for value in self.spellings(path):
                with self.subTest(path=str(value)), self.assertRaises(AssertionError):
                    _file_boundary(value, write=True, owned=self.owned)
            if os.name == "nt":
                with self.assertRaises(AssertionError):
                    _file_boundary("\\\\?\\" + str(path), write=False, owned=self.owned)

    @unittest.skipUnless(os.name == "nt", "Windows path namespace contract")
    def test_unc_devices_traversal_ads_and_ambiguous_names_denied(self):
        extended = "\\\\?\\" + str(self.target)
        invalid = ["\\\\server\\share\\file", "//server/share/file", "\\\\?\\UNC\\server\\share\\file",
                   "\\\\.\\C:\\file", "\\??\\C:\\file", "\\\\?\\GLOBALROOT\\Device\\file",
                   "\\\\?\\C:relative", "\\\\?\\relative", "\\\\?\\C:/file", "C:relative",
                   extended + ":stream", extended + ".", extended + " ", extended + "\\",
                   "\\\\?\\" + str(self.target.parent) + "\\\\file",
                   "\\\\?\\" + str(self.target.parent) + "\\.\\file",
                   "\\\\?\\" + str(self.target.parent) + "\\..\\file"]
        for name in ("..\\file", "file:stream", "NUL", "CON.txt", "COM1", "LPT¹.txt", "file\x01"):
            invalid.extend([str(self.target.parent) + "\\" + name,
                            "\\\\?\\" + str(self.target.parent) + "\\" + name])
        for value in invalid:
            for write in (False, True):
                with self.subTest(path=value, write=write), self.assertRaises(AssertionError):
                    _file_boundary(value, write=write, owned=self.owned)

    def test_leaf_symlink_and_ancestor_reparse_denied_without_creating_links(self):
        from types import SimpleNamespace
        original = Path.lstat
        for spelling in self.spellings(self.target):
            target = Path(os.fsdecode(spelling))
            for linked, metadata in ((target, SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)),
                                     (target.parent, SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400))):
                def lstat(path, _linked=linked, _metadata=metadata):
                    return _metadata if path == _linked else original(path)
                for write in (False, True):
                    with self.subTest(path=str(target), ancestor=linked == target.parent, write=write):
                        with patch.object(Path, "lstat", lstat), self.assertRaises(AssertionError):
                            _file_boundary(spelling, write=write, owned=self.owned)

    def test_audit_write_flags_and_both_rename_paths_checked(self):
        outside = self.owned.with_name(self.owned.name + "-sibling") / "file.json"
        for event, args in (("open", (outside, None, os.O_CREAT | os.O_WRONLY)),
                            ("open", (outside, "r+", 0)),
                            ("os.rename", (self.target, outside, -1, -1)),
                            ("os.rename", (outside, self.target, -1, -1)),
                            ("os.mkdir", (outside, 0o777, -1)),
                            ("os.remove", (outside, -1)), ("os.rmdir", (outside, -1)),
                            ("shutil.rmtree", (outside, None))):
            with self.subTest(event=event, args=str(args)), self.assertRaises(AssertionError):
                _audit_file_boundary(event, args, self.owned)


class _Contracts(unittest.IsolatedAsyncioTestCase):
    """Child-only suite, excluded from ordinary discovery by load_tests."""

    @classmethod
    def setUpClass(cls):
        cls.temp = Path(os.environ["MODE_API_TEMP"]).resolve()
        cls.stack = ExitStack()
        cls.boundary_calls = []

        def forbidden(label):
            def reject(*args, **kwargs):
                cls.boundary_calls.append(label)
                raise AssertionError("Isolation boundary: " + label)
            return reject

        cls.forbidden = staticmethod(forbidden)
        # Audit is child-lifetime and cannot be removed. No cwd/user dotenv or
        # real data may even be read, and writes are confined to the owned TEMP.
        def audit(event, args):
            try:
                _audit_file_boundary(event, args, cls.temp)
            except AssertionError:
                cls.boundary_calls.append("file access")
                raise
        sys.addaudithook(audit)

        from backend.config import Settings
        with patch.dict(os.environ, {}, clear=True):
            cls.settings = Settings(
                _env_file=None, app_env="test", data_dir=cls.temp / "import-must-not-create",
                asr_cache_dir=cls.temp / "cache", min_free_disk_gb=0, max_files=100,
                max_pending_tasks=20, max_concurrent_tasks=1, task_rate_limit_per_hour=100,
                allowed_hosts="127.0.0.1,localhost,news.example.test",
                frontend_origins="http://127.0.0.1:8765,https://news.example.test",
                anonymous_session_secret="mode-api-only-signing-material-never-production-48-bytes",
                kimi_api_key=FAKE_KEY, enable_api_docs=True,
            )
        cls.stack.enter_context(patch("backend.config.get_settings", return_value=cls.settings))
        cls.stack.enter_context(patch(
            "pydantic_settings.sources.providers.dotenv.DotEnvSettingsSource._read_env_file",
            side_effect=forbidden("dotenv loader"),
        ))
        cls.stack.enter_context(patch("platform._syscmd_ver", return_value=("", "", "")))

        def import_probe(command, *args, **kwargs):
            if command == ["ffmpeg", "-v", "quiet"]:
                return 0  # SceneDetect's import-only executable probe.
            return forbidden("unexpected subprocess.call")()
        cls.stack.enter_context(patch("subprocess.call", side_effect=import_probe))
        cls.stack.enter_context(patch("subprocess.Popen", side_effect=forbidden("subprocess")))
        original_pair = socket.socketpair
        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        thread = threading.local()

        def pair(*args, **kwargs):
            thread.pair = True
            try:
                return original_pair(*args, **kwargs)
            finally:
                thread.pair = False

        def connect(sock, *args, **kwargs):
            if getattr(thread, "pair", False):
                return original_connect(sock, *args, **kwargs)
            return forbidden("network connect")()

        def connect_ex(sock, *args, **kwargs):
            if getattr(thread, "pair", False):
                return original_connect_ex(sock, *args, **kwargs)
            return forbidden("network connect_ex")()
        cls.stack.enter_context(patch("socket.socketpair", new=pair))
        cls.stack.enter_context(patch("socket.socket.connect", new=connect))
        cls.stack.enter_context(patch("socket.socket.connect_ex", new=connect_ex))
        cls.stack.enter_context(patch("socket.create_connection", side_effect=forbidden("network connection")))
        for target in ("httpx.HTTPTransport.handle_request", "httpx.AsyncHTTPTransport.handle_async_request",
                       "aiohttp.ClientSession._request"):
            cls.stack.enter_context(patch(target, side_effect=forbidden("provider transport")))
        old_factory = logging.getLogRecordFactory()

        def redacted_record(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.msg, record.args = _redact(record.getMessage()), ()
            return record
        logging.setLogRecordFactory(redacted_record)
        cls.stack.callback(logging.setLogRecordFactory, old_factory)
        cls.main = importlib.import_module("backend.main")
        assert cls.main.settings is cls.settings
        assert cls.main.task_manager.settings is cls.settings
        cls.import_created_directory = cls.settings.data_dir.exists()
        cls.import_created_uploads = cls.main._upload_store is not None
        # Fail if ASGITransport accidentally starts the real application lifespan.
        for name in ("restore_tasks", "cleanup_loop"):
            cls.stack.enter_context(patch.object(cls.main.task_manager, name, side_effect=forbidden("lifespan")))
        cls.stack.enter_context(patch.object(cls.main, "run_full_preflight", side_effect=forbidden("startup preflight")))

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()
        if cls.boundary_calls:
            raise AssertionError("Isolation violations: " + ", ".join(cls.boundary_calls))

    async def asyncSetUp(self):
        import httpx
        self.local = ExitStack()
        self.addCleanup(self.local.close)
        # Keep real Windows snapshot paths below MAX_PATH; method names remain
        # in unittest output, not duplicated inside every artifact pathname.
        self.root = self.temp / hashlib.sha256(self._testMethodName.encode()).hexdigest()[:8] / "tasks"
        self.root.mkdir(parents=True)
        self.local.enter_context(patch.object(self.settings, "data_dir", self.root))
        self.manager = self.main.task_manager
        self.manager._tasks.clear()
        self.manager._semaphore = asyncio.Semaphore(1)
        # A real admission queue held BEFORE any request; no worker/provider can
        # run even when a malformed request is mistakenly accepted.
        await self.manager._semaphore.acquire()
        self.main.rate_counter.clear()
        self.main._preview_slot = asyncio.Semaphore(1)
        self.base = "http://127.0.0.1:8765"
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.main.app, client=("127.0.0.1", 45678),
                                         raise_app_exceptions=False),
            base_url=self.base, headers={"Origin": self.base},
        )
        self.add_calls = []
        real_add = self.manager.add_task

        def deferred(*args, **kwargs):
            self.add_calls.append((args, dict(kwargs)))
            return real_add(*args, **{**kwargs, "defer_start": True})
        self.local.enter_context(patch.object(self.manager, "add_task", side_effect=deferred))
        self.codec_calls = []
        self.local.enter_context(patch("backend.uploads._run_media", side_effect=self.codec))
        self.local.enter_context(patch("backend.media_input.run_logged_command", side_effect=self.codec))

    async def asyncTearDown(self):
        await self.client.aclose()
        for record in list(self.manager._tasks.values()):
            if record.background and not record.background.done():
                record.background.cancel()
                await asyncio.gather(record.background, return_exceptions=True)
            await self.manager._release_disk_reservation(record)
        self.manager._tasks.clear()
        if self.main._upload_store is not None:
            await self.main._upload_store.close()
            self.main._upload_store = None
        self.assertEqual(self.main.upload_capacity_guard._reserved_bytes, 0)
        self.assertEqual(self.boundary_calls, [])

    async def codec(self, command, *args, **kwargs):
        """Synthetic codec boundary, not a replacement route/store/validator."""
        self.codec_calls.append(list(command))
        if command[0] == "ffprobe":
            source = Path(command[-1])
            self.assertTrue(source.is_relative_to(self.root) and source.is_file())
            return json.dumps({"format": {"format_name": "mov,mp4", "duration": "3", "start_time": "0"},
                               "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920,
                                            "height": 1080, "avg_frame_rate": "30/1", "duration": "3"}]}).encode()
        self.assertEqual(command[0], "ffmpeg")
        output = Path(command[-1])
        self.assertTrue(output.is_relative_to(self.root))
        output.write_bytes(b"synthetic codec output; not decodable media")
        return b""

    def status(self, response, expected):
        # No response bodies, request headers, cookies or tokens in failures.
        self.assertEqual(response.status_code, expected,
                         f"{response.request.method} {response.request.url.path}: expected {expected}, got {response.status_code}")

    async def upload(self, *, name="clip.mp4", content=None, content_type="video/mp4", ready=True):
        content = b"opaque synthetic upload fixture" if content is None else content
        response = await self.client.post("/api/uploads", json={
            "name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
            "content_type": content_type,
        })
        self.status(response, 201)
        created = response.json()
        upload_id, token = created["upload_id"], created["access_token"]
        TOKENS.append(token)
        headers = {"X-Upload-Token": token}
        self.status(await self.client.put(f"/api/uploads/{upload_id}/chunks/0", content=content, headers=headers), 200)
        if ready:
            self.status(await self.client.post(f"/api/uploads/{upload_id}/complete", headers=headers), 202)
            jobs = list(self.main._upload_store._jobs.values())
            await asyncio.gather(*jobs)
            response = await self.client.get(f"/api/uploads/{upload_id}", headers=headers)
            self.status(response, 200)
            self.assertEqual(response.json()["status"], "ready")
        return upload_id, token, content

    def payload(self, upload, *, mode="voiceover"):
        upload_id, token, _ = upload
        return {"mode": mode, "script": "活动简讯\n\n" + TEXT,
                "sentences": [{"idx": 0, "text": TEXT, "kind": "narration" if mode == "voiceover" else "quote"}],
                "upload_ids": [upload_id], "upload_tokens": {upload_id: token}}

    def transcript_fixture(self, upload):
        """Save synthetic ASR evidence; matching itself remains completely real."""
        from backend.uploads import _Transcript
        upload_id, token, _ = upload
        store = self.main._upload_store
        record = store.authorize(upload_id, token)
        record.has_speech = True
        record.transcript = _Transcript.model_validate({"text": TEXT, "precision": "segment", "segments": [
            {"id": "segment-0", "start": 0.5, "end": 2.5, "speaker_id": "fixture-speaker",
             "text": TEXT, "confidence": 0.99, "snr_db": 25.0},
        ]})
        store._save(record)

    def completed(self, *, quote=False, warning=False):
        from tests.test_task_operations import synthetic_record
        from backend.production_modes import QuoteTake, SentenceInput
        from backend.storage import write_json_atomic
        from backend.revisions import snapshot_revision
        record = synthetic_record(self.manager, complete=True)
        # Keep original r0, save the explicit-mode current contract as real r1.
        record.mode, record.mode_contract, record.revision = "mixed" if quote else "voiceover", True, 1
        record.sentences = [SentenceInput(idx=i, text=t, kind="quote" if quote and i == 0 else "narration")
                            for i, t in enumerate(record.script.splitlines())]
        record.upload_ids = ["up_" + "1" * 32]
        record.uploads[0].upload_id = record.upload_ids[0]
        record.uploads[0].source_duration_seconds = 5.0
        if quote:
            take = QuoteTake(take_id="fixture-take", upload_id=record.upload_ids[0], start=0.0, end=1.0,
                             speaker_id="fixture-speaker", asr_text=record.sentences[0].text,
                             score=1.0, precision="segment", segment_ids=["segment-0"])
            for name in ("match_plan.json", "report.json"):
                path = record.task_dir / name
                value = json.loads(path.read_text(encoding="utf-8"))
                row = value["rows"][0] if isinstance(value, dict) else value[0]
                row.update(kind="quote", source=take.model_dump(mode="json"),
                           alt_takes=[take.model_copy(update={"take_id": "alternate-take", "start": 2.0, "end": 3.0}).model_dump(mode="json")])
                write_json_atomic(path, value)
            with wave.open(str(record.task_dir / "tts/0.wav"), "wb") as stream:
                stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                stream.writeframes(struct.pack("<h", 16384) * 16000)
        if warning:
            path = record.task_dir / "report.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            report["quality"].update(warning_count=1, issues=[{
                "code": "FIXTURE_WARNING", "severity": "warning", "message": "Synthetic review required",
            }])
            write_json_atomic(path, report)
        self.manager._write_upload_manifest(record)
        self.manager._persist_record(record)
        snapshot_revision(record, "Synthetic mode API fixture")
        TOKENS.append(record.access_token)
        return record

    def task_path(self, record, suffix=""):
        return f"/api/tasks/{record.task_id}{suffix}"

    def tree(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file() and p != self.root / "_uploads" / ".lock"}

    @unittest.skipUnless(os.name == "nt", "Windows extended path I/O")
    async def test_owned_extended_long_path_real_io_under_installed_audit(self):
        ordinary = self.root / ("x" * 180) / ("y" * 80)
        extended = Path("\\\\?\\" + str(ordinary))
        self.assertGreater(len(str(ordinary)), 260)
        extended.mkdir(parents=True)
        source, target = extended / "before.json", extended / "after.json"
        source.write_bytes(b"owned long-path guard fixture")
        source.replace(target)
        self.assertEqual(target.read_bytes(), b"owned long-path guard fixture")
        target.unlink()
        extended.rmdir()
        extended.parent.rmdir()

    async def export_adapter_fixture(self, payload, *, json_response=False, record=None):
        """Real main adapter + real models; fake Studio endpoint, NO encoding."""
        from backend.studio import RenderRequest, read_state
        from starlette.background import BackgroundTask
        from starlette.responses import JSONResponse
        if record is None:
            record = self.completed()
        self.status(await self.client.get(self.task_path(record, "/checks")), 200)
        before = self.tree()
        receipt = {"id": "a" * 32, "state": "queued", "kind": "export", "output_id": None}
        calls, backgrounds = [], []
        original_response = JSONResponse(receipt, status_code=202,
                                         headers={"Cache-Control": "no-store", "X-Adapter-Fixture": "synthetic"},
                                         background=BackgroundTask(backgrounds.append, "ran"))

        async def endpoint(request, task_id):
            self.assertEqual(task_id, record.task_id)
            self.assertEqual(request.url.path, self.task_path(record, "/studio/export"))
            self.assertEqual(request.scope["raw_path"], request.url.path.encode("ascii"))
            self.assertEqual(request.headers["origin"], self.base)
            body = await request.body()
            self.assertEqual(request.headers["content-type"], "application/json")
            self.assertEqual(int(request.headers["content-length"]), len(body))
            forwarded = json.loads(body)
            RenderRequest.model_validate(forwarded)  # Actual strict Studio model, not an invented schema.
            self.assertEqual(forwarded["expected_revision"], read_state(record.task_dir)["revision"])
            calls.append(forwarded)
            return original_response if json_response else dict(receipt)

        def lookup(router, name):
            self.assertIs(router, self.main._studio_router)
            self.assertEqual(name, "export")
            return endpoint

        with patch.object(self.main, "_private_endpoint", side_effect=lookup):
            response = await self.client.post(self.task_path(record, "/export"), json=payload)
        self.status(response, 202)
        self.assertEqual(response.json(), {**receipt, "export_id": receipt["id"]})
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.tree(), before)
        self.assertIsNone(record.background)
        self.assertEqual(self.codec_calls, [])
        if json_response:
            self.assertEqual(response.headers["x-adapter-fixture"], "synthetic")
            # Real main middleware tightens all private API responses.
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            self.assertEqual(int(response.headers["content-length"]), len(response.content))
            self.assertEqual(response.headers["content-type"], "application/json")
            self.assertEqual(backgrounds, ["ran"])
            self.assertEqual(json.loads(bytes(original_response.body)), receipt)
        return calls[0]["options"]

    async def test_main_mp3_export_normalizes_irrelevant_video_options(self):
        record = self.completed()
        for aspect in ("16:9", "9:16", "1:1"):
            for res in ("360p", "720p", "1080p"):
                for sub in ("std", "big", "none"):
                    with self.subTest(aspect=aspect, res=res, sub=sub):
                        options = await self.export_adapter_fixture({"fmt": "mp3", "aspect": aspect, "res": res, "sub": sub},
                                                                    record=record)
                        self.assertEqual(options, {"format": "mp3", "aspect": "16:9", "resolution": 1080, "subtitles": "standard"})

    async def test_main_video_export_preserves_applicable_options_and_defaults(self):
        for payload, expected in (({}, {"format": "mp4", "aspect": "16:9", "resolution": 1080, "subtitles": "standard"}),
                                  ({"fmt": "mp4", "aspect": "9:16", "res": "720p", "sub": "big"},
                                   {"format": "mp4", "aspect": "9:16", "resolution": 720, "subtitles": "large"}),
                                  ({"fmt": "gif", "aspect": "1:1", "res": "360p", "sub": "none"},
                                   {"format": "gif", "aspect": "1:1", "resolution": 360, "subtitles": "none"})):
            with self.subTest(payload=payload):
                self.assertEqual(await self.export_adapter_fixture(payload), expected)

    async def test_main_export_real_jsonresponse_adds_id_and_preserves_response_contract(self):
        await self.export_adapter_fixture({"fmt": "mp3", "aspect": "9:16", "res": "360p", "sub": "none"},
                                          json_response=True)

    async def test_main_export_invalid_fields_rejected_before_adapter(self):
        record = self.completed()
        before = self.tree()
        for payload in ({"format": "mp3"}, {"fmt": "wav"}, {"res": 720}, {"res": "4k"},
                        {"sub": "large"}, {"aspect": "4:3"}, {"fmt": "mp3", "sub": "invalid"}):
            with self.subTest(payload=payload), patch.object(self.main, "_private_endpoint") as endpoint:
                self.status(await self.client.post(self.task_path(record, "/export"), json=payload), 422)
                endpoint.assert_not_called()
        self.assertEqual(self.tree(), before)

    async def test_import_and_public_reads_do_not_bootstrap_uploads(self):
        self.assertFalse(self.import_created_directory, "Import created DATA_DIR")
        self.assertFalse(self.import_created_uploads, "Import eagerly bootstrapped UploadStore")
        for path in ("/api/config/limits", "/api/config/workspace", "/openapi.json"):
            self.status(await self.client.get(path), 200)
        self.assertIsNone(self.main._upload_store)
        self.assertFalse((self.root / "_uploads").exists())

    async def test_public_limits_mode_caps_and_no_grade(self):
        response = await self.client.get("/api/config/limits")
        self.status(response, 200)
        limits = response.json()
        self.assertLessEqual(limits["max_files"], 20)
        self.assertEqual(limits["chunk_size"], 8 * 1024**2)
        self.assertEqual(limits["pacing_cpm"], {"slow": 230, "normal": 265, "fast": 290})
        self.assertEqual(limits["match_ok"], .85)
        self.assertEqual(limits["match_low"], .6)
        self.assertEqual(limits["max_script_length"], 8000)
        self.assertNotIn("grade", json.dumps(limits))
        self.assertNotIn(FAKE_KEY, response.text)
        self.assertEqual(set(limits["allowed_extensions"]), {".mp4", ".mov", ".avi", ".mkv", ".jpg", ".jpeg", ".png", ".gif"})

    async def test_openapi_all_document_refs_resolve(self):
        response = await self.client.get("/openapi.json")
        self.status(response, 200)
        document = response.json()
        refs = []

        def visit(value):
            if isinstance(value, dict):
                if "$ref" in value:
                    refs.append(value["$ref"])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(document)
        self.assertTrue(refs)
        for ref in sorted(set(refs)):
            with self.subTest(ref=ref):
                self.assertTrue(ref.startswith("#/"), "Unexpected external schema reference")
                value = document
                for part in ref[2:].split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    self.assertTrue(key in value, f"Unresolvable document reference: {ref}")
                    value = value[key]

    async def test_real_upload_repeated_chunks_complete_and_capabilities(self):
        from backend.uploads import UploadStore
        upload_id, token, content = await self.upload(ready=False)
        store = self.main._upload_store
        self.assertIsInstance(store, UploadStore)
        self.assertIs(self.main._uploads(), store)
        headers = {"X-Upload-Token": token}
        path = f"/api/uploads/{upload_id}"
        before = self.tree()
        for method, suffix, kwargs in (("get", "", {}), ("put", "/chunks/0", {"content": content}),
                                       ("post", "/complete", {}), ("delete", "", {})):
            self.status(await getattr(self.client, method)(path + suffix, **kwargs), 404)
        self.assertEqual(self.tree(), before)
        repeated = await self.client.put(path + "/chunks/0", content=content, headers=headers)
        self.status(repeated, 200)
        self.assertEqual(self.tree(), before)
        self.status(await self.client.put(path + "/chunks/0", content=b"x" * len(content), headers=headers), 409)
        self.assertEqual(self.tree(), before)
        self.status(await self.client.get(path, headers={"X-Upload-Token": "x" * 43}), 404)
        self.status(await self.client.get(path + "?token=" + token), 404)
        self.status(await self.client.post(path + "/complete", headers=headers), 202)
        await asyncio.gather(*list(store._jobs.values()))
        calls = len(self.codec_calls)
        self.status(await self.client.post(path + "/complete", headers=headers), 202)
        self.assertEqual(len(self.codec_calls), calls)
        self.assertIs(self.main._upload_store, store)
        snapshot = (await self.client.get(path, headers=headers)).json()
        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["received_bytes"], len(content))
        self.assertEqual(store._source(store.authorize(upload_id, token)).read_bytes(), content)
        self.assertNotIn(token, json.dumps(snapshot))
        self.assertNotIn("token=", snapshot["thumb_url"])

    async def test_upload_extension_mime_size_and_twenty_file_cap(self):
        common = {"bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
        cases = [("clip.webm", "video/webm", 415), ("photo.raw", "application/octet-stream", 415),
                 ("clip.mp4", "image/png", 415), ("../clip.mp4", "video/mp4", 422)]
        for name, mime, expected in cases:
            with self.subTest(name=name):
                self.status(await self.client.post("/api/uploads", json={**common, "name": name, "content_type": mime}), expected)
        self.status(await self.client.post("/api/uploads", json={**common, "name": "photo.png", "bytes": 50 * 1024**2 + 1}), 413)
        self.status(await self.client.post("/api/uploads", json={**common, "name": "clip.mp4", "bytes": 500 * 1024**2 + 1}), 422)
        for index in range(20):
            self.status(await self.client.post("/api/uploads", json={**common, "name": f"clip-{index}.mp4"}), 201)
        self.status(await self.client.post("/api/uploads", json={**common, "name": "overflow.mp4"}), 429)

    async def test_real_image_upload_preserves_raw_types_and_bytes(self):
        import cv2
        import numpy as np
        for suffix, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg")):
            with self.subTest(suffix=suffix):
                ok, image = cv2.imencode(suffix, np.full((8, 8, 3), 100, dtype=np.uint8))
                self.assertTrue(ok)
                upload = await self.upload(name="still" + suffix, content=image.tobytes(), content_type=mime)
                record = self.main._upload_store.authorize(upload[0], upload[1])
                self.assertTrue(record.probe.is_image)
                self.assertEqual(record.probe.sec, 3)
                self.assertFalse(record.probe.has_audio)
                self.assertEqual(self.main._upload_store._source(record).read_bytes(), upload[2])
                self.assertEqual(record.content_type, mime)
        gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        upload = await self.upload(name="still.gif", content=gif, content_type="image/gif")
        self.assertEqual(self.main._upload_store._source(self.main._upload_store.authorize(upload[0], upload[1])).read_bytes(), gif)

    async def test_create_json_modes_use_real_materialization_and_deferred_manager(self):
        upload = await self.upload()
        for mode in ("voiceover", "mixed", "original"):
            with self.subTest(mode=mode):
                response = await self.client.post("/api/tasks", json=self.payload(upload, mode=mode))
                self.status(response, 202)
                TOKENS.append(response.json()["access_token"])
                record = self.manager.get(response.json()["task_id"])
                self.assertEqual(record.mode, mode)
                self.assertTrue(record.mode_contract)
                self.assertIsNone(record.background)
                self.assertEqual(record.upload_ids, [upload[0]])
                self.assertEqual(record.uploads[0].path.read_bytes(), upload[2])
                self.assertEqual(record.sentences[0].text, TEXT)
                self.assertTrue((record.task_dir / "pretranscripts.json").is_file())
                self.assertEqual(self.add_calls[-1][1]["mode"], mode)
                status = await self.client.get(self.task_path(record))
                self.status(status, 200)
                self.assertEqual(status.json()["mode"], mode)
                self.assertEqual(len(status.json()["stages"]), 10)
                self.assertAlmostEqual(sum(s["weight"] for s in status.json()["stages"]), 1.0)

    async def test_create_grade_ignored_and_server_pacing_default(self):
        payload = self.payload(await self.upload())
        payload["grade"] = "retired-user-controlled-grade"
        response = await self.client.post("/api/tasks", json=payload)
        self.status(response, 202)
        record = self.manager.get(response.json()["task_id"])
        self.assertEqual(record.preferences.target_chars_per_minute, 265)
        self.assertEqual(record.preferences.pacing, "normal")
        state = json.loads((record.task_dir / "task_state.json").read_text(encoding="utf-8"))
        self.assertNotIn("grade", state)
        self.assertNotIn("grade", state["preferences"])

    async def test_create_rejects_client_target_cpm(self):
        payload = self.payload(await self.upload())
        for name in ("target_cpm", "target_chars_per_minute"):
            with self.subTest(field=name):
                payload["preferences"] = {name: 999}
                self.status(await self.client.post("/api/tasks", json=payload), 422)
        self.assertEqual(self.add_calls, [])

    async def test_create_256kib_json_body_limit(self):
        payload = self.payload(await self.upload())
        body = json.dumps(payload).encode()
        # An 8000-character UTF-8 manuscript is sent with its sentence list and
        # up to 20 capabilities. A made-up 32 KiB ceiling rejects valid input.
        body += b" " * (256 * 1024 + 1 - len(body))
        self.status(await self.client.post("/api/tasks", content=body, headers={"Content-Type": "application/json"}), 413)
        self.assertEqual(self.add_calls, [])

    async def test_create_rejects_unauthorized_unready_and_over_cap_uploads(self):
        upload = await self.upload(ready=False)
        payload = self.payload(upload)
        self.status(await self.client.post("/api/tasks", json=payload), 409)
        payload["upload_tokens"] = {upload[0]: "x" * 43}
        self.status(await self.client.post("/api/tasks", json=payload), 404)
        payload["upload_ids"] = ["up_" + f"{i:032x}" for i in range(21)]
        self.status(await self.client.post("/api/tasks", json=payload), 422)
        self.assertEqual(self.add_calls, [])

    async def test_create_rejects_script_body_diverging_from_sentences(self):
        payload = self.payload(await self.upload())
        payload["script"] = "活动简讯\n\n完全不同的事实。"
        self.status(await self.client.post("/api/tasks", json=payload), 422)
        self.assertEqual(self.add_calls, [])

    async def test_create_rejects_sentences_diverging_from_script_body(self):
        payload = self.payload(await self.upload())
        payload["sentences"][0]["text"] = "完全不同的事实。"
        self.status(await self.client.post("/api/tasks", json=payload), 422)
        self.assertEqual(self.add_calls, [])

    async def test_source_hint_upload_path_denied(self):
        payload = self.payload(await self.upload(), mode="mixed")
        payload["sentences"][0]["source_hint"] = {"upload_id": "../../data/private.mp4", "seg_id": "segment-0"}
        self.status(await self.client.post("/api/tasks", json=payload), 422)
        self.assertEqual(self.add_calls, [])

    async def test_source_hint_segment_path_denied(self):
        upload = await self.upload()
        payload = self.payload(upload, mode="mixed")
        payload["sentences"][0]["source_hint"] = {"upload_id": upload[0], "seg_id": "../../private.json"}
        self.status(await self.client.post("/api/tasks", json=payload), 422)
        self.assertEqual(self.add_calls, [])

    async def test_preview_quote_uses_saved_evidence_without_provider(self):
        upload = await self.upload()
        self.transcript_fixture(upload)
        payload = self.payload(upload, mode="mixed")
        payload = {key: payload[key] for key in ("sentences", "upload_ids", "upload_tokens")}
        before = self.tree()
        response = await self.client.post("/api/match/preview", json=payload)
        self.status(response, 200)
        row = response.json()["matches"][0]
        self.assertEqual(row["kind"], "quote")
        self.assertGreaterEqual(row["score"], .85)
        self.assertEqual(row["source"]["upload_id"], upload[0])
        self.assertEqual(row["source"]["asr_text"], TEXT)
        self.assertEqual(self.tree(), before)
        payload["upload_tokens"] = {}
        self.status(await self.client.post("/api/match/preview", json=payload), 404)

    async def test_preview_narration_suggests_evidence_without_changing_kind(self):
        # A-mode detection needs suggestions for EVERY sentence. align_quotes
        # intentionally skips narration; the HTTP preview adapter must bridge it.
        upload = await self.upload()
        self.transcript_fixture(upload)
        payload = self.payload(upload)
        payload = {key: payload[key] for key in ("sentences", "upload_ids", "upload_tokens")}
        before = self.tree()
        response = await self.client.post("/api/match/preview", json=payload)
        self.status(response, 200)
        rows = response.json()["matches"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "narration")
        self.assertGreaterEqual(rows[0]["score"], .85, "Narration preview must return high-score source evidence, not a silent zero")
        self.assertEqual(rows[0]["source"]["upload_id"], upload[0])
        self.assertEqual(payload["sentences"][0]["kind"], "narration")
        self.assertEqual(self.tree(), before)

    async def test_multipart_auto_retains_legacy_without_mode_guarantee(self):
        response = await self.client.post("/api/tasks", data={"script": TEXT, "script_format": "auto"},
                                          files={"files": ("clip.mp4", b"legacy fixture", "video/mp4")})
        self.status(response, 202)
        record = self.manager.get(response.json()["task_id"])
        self.assertEqual(record.mode, "voiceover")
        self.assertFalse(record.mode_contract)
        self.assertEqual(record.script, TEXT)
        self.assertEqual(record.uploads[0].path.read_bytes(), b"legacy fixture")
        self.assertIsNone(record.background)

    async def test_multipart_explicit_mode_requires_preupload(self):
        for mode in ("voiceover", "mixed", "original"):
            with self.subTest(mode=mode):
                response = await self.client.post("/api/tasks", data={"script": TEXT, "mode": mode},
                                                  files={"files": ("clip.mp4", b"legacy fixture", "video/mp4")})
                self.status(response, 422)
        self.assertEqual(self.add_calls, [])

    async def test_production_origin_and_anonymous_csrf_real_middleware(self):
        import httpx
        from backend.anonymous_access import ANONYMOUS_CSRF_HEADER
        with patch.object(self.settings, "app_env", "production"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.main.app, client=("203.0.113.7", 40000)),
                                         base_url="https://news.example.test") as client:
                declaration = {"name": "clip.mp4", "bytes": 1, "sha256": "0" * 64}
                for path, body in (("/api/tasks", {}), ("/api/uploads", declaration), ("/api/match/preview", {})):
                    self.status(await client.post(path, json=body), 403)
                    self.status(await client.post(path, json=body, headers={"Origin": "https://news.example.test"}), 403)
                self.assertIsNone(self.main._upload_store)
                session = await client.get("/api/session")
                self.status(session, 200)
                csrf = session.json()["csrf_token"]
                TOKENS.append(csrf)
                headers = {"Origin": "https://news.example.test", ANONYMOUS_CSRF_HEADER: csrf}
                self.status(await client.post("/api/uploads", json=declaration, headers={**headers, "Origin": "https://evil.example.test"}), 403)
                self.status(await client.post("/api/uploads", json=declaration, headers={**headers, ANONYMOUS_CSRF_HEADER: "fake"}), 403)
                response = await client.post("/api/uploads", json=declaration, headers=headers)
                self.status(response, 201)
                upload_id, token = response.json()["upload_id"], response.json()["access_token"]
                TOKENS.append(token)
                self.status(await client.put(f"/api/uploads/{upload_id}/chunks/0", content=b"x", headers={**headers, "X-Upload-Token": token}), 200)
                self.assertIn("max-age", response.headers["strict-transport-security"])
                self.assertEqual(self.add_calls, [])

    async def test_remix_missing_revision_defaults_to_current_and_id_alias(self):
        record = self.completed()
        response = await self.client.post(self.task_path(record, "/remix"), json={"edits": [{"id": 0, "text": "Changed sentence."}]})
        self.status(response, 202)
        self.assertEqual(response.json()["current_revision"], 1)
        self.assertEqual(response.json()["revision"], 2)
        self.assertIsNotNone(record.background)
        self.assertEqual(record.status, "queued")
        self.assertEqual(record.revision, 1)

    async def test_remix_stale_revision_409_before_worker(self):
        record = self.completed()
        before = self.tree()
        response = await self.client.post(self.task_path(record, "/remix"), json={
            "expected_revision": 0, "edits": [{"id": 0, "text": "Changed sentence."}],
        })
        self.status(response, 409)
        self.assertEqual(response.json()["detail"]["code"], "stale_revision")
        self.assertIsNone(record.background)
        self.assertEqual(self.tree(), before)

    async def test_remix_malformed_edit_lists_are_422_not_filtered(self):
        for value in ([None], ["invalid"], [7], "not-a-list", {"id": 0}, None,
                      [{"id": 0, "text": "Valid edit must not hide invalid sibling."}, None]):
            with self.subTest(shape=repr(value)):
                record = self.completed()
                before = self.tree()
                response = await self.client.post(self.task_path(record, "/remix"), json={
                    "keep_sentence_ids": [0], "edits": value,
                })
                self.status(response, 422)
                self.assertIsNone(record.background)
                self.assertEqual(self.tree(), before)

    async def test_quote_text_and_picture_edits_422_before_worker(self):
        record = self.completed(quote=True)
        before = self.tree()
        for edit in ({"id": 0, "text": "Invented quotation."}, {"id": 0, "shot_id": 2},
                     {"id": 0, "instruction": "Replace the original scene"}):
            with self.subTest(edit=edit):
                self.status(await self.client.post(self.task_path(record, "/remix"), json={"edits": [edit]}), 422)
        self.status(await self.client.post(self.task_path(record, "/replace-shot"), json={
            "sentence_id": 0, "instruction": "Replace the original scene",
        }), 422)
        self.assertIsNone(record.background)
        self.assertEqual(self.tree(), before)

    async def test_quote_takes_and_waves_real_saved_pcm_readonly(self):
        record = self.completed(quote=True)
        before = self.tree()
        takes = await self.client.get(self.task_path(record, "/quotes/0/takes"))
        self.status(takes, 200)
        self.assertEqual(takes.json()["takes"][0]["take_id"], "alternate-take")
        response = await self.client.get(self.task_path(record, "/waves/0.json"))
        self.status(response, 200)
        value = response.json()
        self.assertEqual(value["interval_ms"], 20)
        self.assertEqual(len(value["samples"]), 50)
        self.assertTrue(all(abs(sample - .5) < 1e-12 for sample in value["samples"]))
        self.assertEqual((value["start"], value["end"]), (0.0, 1.0))
        for suffix in ("/quotes/1/takes", "/waves/1.json"):
            self.status(await self.client.get(self.task_path(record, suffix)), 404)
        for suffix in ("/quotes/0/takes", "/waves/0.json"):
            self.status(await self.client.get(self.task_path(record, suffix), headers={"X-Task-Token": "invalid"}), 404)
        self.assertEqual(self.tree(), before)

    async def test_checks_get_post_revision_gate_and_export_409(self):
        record = self.completed(warning=True)
        path = self.task_path(record)
        before = self.tree()
        response = await self.client.get(path + "/checks")
        self.status(response, 200)
        gate = response.json()
        self.assertFalse(gate["passed"])
        self.assertEqual(self.tree(), before)
        self.status(await self.client.post(path + "/export", json={"fmt": "mp4"}), 409)
        self.status(await self.client.get(path + "/video?download=true"), 409)
        self.status(await self.client.post(path + "/checks", json={"expected_revision": 0, "checked_keys": []}), 409)
        self.status(await self.client.post(path + "/checks", json={"expected_revision": 1, "checked_keys": ["0" * 64]}), 422)
        self.assertEqual(self.tree(), before)
        keys = [check["key"] for check in gate["checks"] if check["level"] == 1]
        confirmed = await self.client.post(path + "/checks", json={"expected_revision": 1, "checked_keys": keys})
        self.status(confirmed, 200)
        self.assertTrue(confirmed.json()["passed"])
        self.status(await self.client.get(path + "/video?download=true"), 200)
        revoked = await self.client.post(path + "/checks", json={"expected_revision": 1, "checked_keys": []})
        self.status(revoked, 200)
        self.assertFalse(revoked.json()["passed"])
        self.status(await self.client.post(path + "/export", json={}), 409)
        self.assertIsNone(record.background)

    async def test_poster_and_export_status_gets_are_readonly_no_url_fallback(self):
        from backend.public_media import committed_root
        record = self.completed(warning=True)
        root = committed_root(record)
        final = root / "final.mp4"
        stat = final.stat()
        source = final.relative_to(record.task_dir).as_posix()
        key = hashlib.sha256(f"{source}:{record.revision}:{stat.st_size}:{stat.st_mtime_ns}:0.500000".encode()).hexdigest()[:24]
        cached = record.task_dir / "public_previews" / (key + ".jpg")
        cached.parent.mkdir()
        cached.write_bytes(b"synthetic cached poster")
        before = self.tree()
        response = await self.client.get(self.task_path(record, "/poster"))
        self.status(response, 200)
        self.assertEqual(response.content, cached.read_bytes())
        for suffix in ("/exports/" + "a" * 32, "/export", "/poster?token=invalid"):
            self.status(await self.client.get(self.task_path(record, suffix)), 404)
        self.assertEqual(self.tree(), before)
        self.assertIsNone(record.background)
        self.assertEqual(self.codec_calls, [])

    async def test_status_stage_aliases_preserve_canonical_fields(self):
        response = await self.client.post("/api/tasks", json=self.payload(await self.upload(), mode="mixed"))
        self.status(response, 202)
        record = self.manager.get(response.json()["task_id"])
        response = await self.client.get(self.task_path(record))
        self.status(response, 200)
        for stage in response.json()["stages"]:
            with self.subTest(number=stage["number"]):
                self.assertEqual(stage.get("n"), stage["number"], "Missing documented n alias")
                self.assertEqual(stage.get("msg"), stage["message"], "Missing documented msg alias")
                self.assertIn("elapsed", stage)
                self.assertEqual(stage["elapsed"], stage["elapsed_seconds"])

    async def test_old_held_record_restore_never_starts_worker(self):
        from tests.test_task_operations import synthetic_record
        from backend.task_manager import TaskManager
        from backend.storage import write_json_atomic
        record = synthetic_record(self.manager)
        state_path = record.task_dir / "task_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update(status="held", queue_hold=True)
        for key in ("mode", "mode_contract", "sentences", "speakers", "upload_ids"):
            state.pop(key, None)
        write_json_atomic(state_path, state)
        restored_manager = TaskManager(self.settings)
        self.assertEqual(restored_manager.restore_tasks(), 1)
        restored = restored_manager.get(record.task_id)
        self.assertTrue(restored.local_only)
        self.assertFalse(restored.mode_contract)
        self.assertEqual(restored.mode, "voiceover")
        self.assertIsNone(restored.background)
        self.assertNotEqual(restored.status, "running")


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(ModeAPIIsolationTest)


if __name__ == "__main__":
    if CHILD_FLAG in sys.argv:
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                                   for cls in (_GuardContracts, _Contracts))
        class CompactRunner(unittest.TextTestRunner):
            def _makeResult(self):
                result = super()._makeResult()
                # Keep the full error traceback, but summarize assertions without
                # rendering entire schemas/trees or exhausting terminal history.
                def print_errors():
                    for category, entries in (("FAIL", result.failures), ("ERROR", result.errors)):
                        for test, detail in entries:
                            self.stream.writeln(f"{category}: {test}")
                            self.stream.writeln(detail if category == "ERROR" else detail.splitlines()[-1])
                result.printErrors = print_errors
                return result
        result = CompactRunner(stream=_RedactingStream(sys.stderr), verbosity=2).run(suite)
        print(f"mode_api_cases={result.testsRun}; failures={len(result.failures)}; errors={len(result.errors)}")
        if result.wasSuccessful():
            print("mode_api_isolation=passed")
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main()