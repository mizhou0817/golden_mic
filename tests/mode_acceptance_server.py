"""Three-mode browser host: ONLY 127.0.0.1:8786, fresh TEMP, synthetic media.

The parent builds a NEW frontend/dist-canary-* and starts this process explicitly:
  python -B -X faulthandler -m tests.mode_acceptance_server
      --manifest <absolute-unused-TEMP-json> --frontend-dir <absolute-custom-dist>
--check-only checks isolation and fixture contracts, with NO listener or render.

Nothing is seeded from data/eval_sample, and no task is created on startup.
Uploads, stage 6 alignment, all ten stages, revisions, QC and exports are real.
Only UploadStore._transcribe is injected, by ORIGINAL name AND source SHA-256;
_editorial_transcript still consumes those provider words in the product code.
Words are EVENLY SPACED TEST LABELS over tones, NOT recognized human speech.
The existing controlled loopback Vision/Embedding/LLM/tone-TTS fixture is reused.
This is Python/provider confinement, NOT an OS sandbox for native codecs/Edge.
No implicit launch, automatic retry, process killing, dotenv or shared dist use.
"""
from __future__ import annotations

import argparse
import asyncio
import faulthandler
import inspect
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import wave
from collections import Counter
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

sys.dont_write_bytecode = True
# This helper imports ONLY stdlib; it does not import the app or start a service.
from tests.workspace_fixture import (
    PROJECT_ROOT, NetworkGuard, SafetyError, fake_provider, isolated_environment,
    media_command, require, sha256, unlinked, validate_frontend,
)

BASE_URL = "http://127.0.0.1:8786"
PORT = 8786
DISCLOSURE = (
    "SYNTHETIC ACCEPTANCE ONLY: 320x180/30fps FFmpeg patterns and sine tones; "
    "prototype interview TEXT, speakers and evenly spaced word timestamps are "
    "injected test labels, NOT speech recognition, intelligibility, diarization, "
    "word-alignment accuracy, factual verification or SNR evidence. No real or "
    "paid provider calls. Real uploads, alignment, rendering, revisions and QC."
)
MIXED_SCRIPT = (
    "数十家企业汇聚南宁信息港 迎春市集开张备年货\n"
    "马年新春将至，位于南宁市西乡塘区鲁班路95号的南宁信息港广场1月30日举办了“金马贺岁，高新同驰”迎春市集，吸引众多市民前来赶集购年货、品年味。\n"
    "同期 王红（市集主办方）：今年我们特意把新能源汽车和传统年货放在一起，就是想让大家感受高新和年味的结合。\n"
    "活动现场汇聚几十家参展企业，近20款新能源汽车科技感十足，各类传统年货如腊肉、酒类、礼盒等一应俱全。\n"
    "同期 李大姐（腊味摊摊主）：我们家的腊肠是自己熏的，用的是广西本地的猪肉。这两天人特别多，昨天一天卖了两百多斤。\n"
    "灌阳油茶、现做寿司等特色小吃引来众多品尝者，市集现场人流涌动，欢声不断。\n"
    "同期 张先生（市民）：挺热闹的，感觉年味一下就来了。\n"
    "本次活动由南宁信息港主办、悦和物业公司协办，将持续到1月31日。\n"
    "同期 王红（市集主办方）：我们这个市集会一直持续到1月31号，欢迎大家来。"
)
ORIGINAL_SCRIPT = (
    "年味从一口腊肠开始：市集上的三个人\n"
    "买了点腊肉，还有礼盒，准备给老人送去\n"
    "我们家的腊肠是自己熏的，用的是广西本地的猪肉\n"
    "这两天人特别多，昨天一天卖了两百多斤\n"
    "今年我们特意把新能源汽车和传统年货放在一起，就是想让大家感受高新和年味的结合\n"
    "三十多家企业，基本上都是园区里的\n"
    "就是希望大家过年都能吃上一口家乡味\n"
    "挺热闹的，感觉年味一下就来了\n"
    "我们这个市集会一直持续到1月31号，欢迎大家来"
)
INTERVIEWS = (
    ("synthetic-organizer.mp4", 35, "S1", "王红", "市集主办方", (
        (3, 9, "我们这个市集会一直持续到一月三十一号，欢迎大家来"),
        (12, 21, "今年我们特意把新能源汽车和传统年货放在一起，就是想让大家感受高新和年味的结合"),
        (24, 31, "三十多家企业，基本上都是园区里的"),
    )),
    ("synthetic-vendor.mp4", 58, "S2", "李大姐", "腊味摊摊主", (
        (2, 10, "我们家的腊肠是自己熏的，用的是广西本地的猪肉"),
        (15, 22, "这两天人特别多，昨天一天卖了两百多斤"),
        (30, 38, "嗯…就是希望大家过年都能吃上一口家乡味"),
    )),
    ("synthetic-citizen.mp4", 40, "S3", "张先生", "市民", (
        (1, 7, "买了点腊肉，还有礼盒，准备给老人送去"),
        (10, 16, "挺热闹的，感觉年味一下就来了"),
    )),
)
VOICEOVER_SCRIPT = "合成色卡验收样片\n色块画面缓缓移动。\n测试图案清晰可见。"
SHORT_MIXED_SCRIPT = "合成采访与色卡验收\n旁白：色块画面缓缓移动。\n同期：挺热闹的，感觉年味一下就来了。"
SHORT_ORIGINAL_SCRIPT = "合成原声剪辑验收\n买了点腊肉，还有礼盒，准备给老人送去"


def provider_fixture(name: str) -> dict[str, Any]:
    """Test data, not an ASR implementation: character words on even ms clocks."""
    fixture = next((item for item in INTERVIEWS if item[0] == name), None)
    require(fixture is not None, "unknown_synthetic_interview")
    assert fixture is not None
    _, duration, speaker, _, _, lines = fixture
    utterances = []
    for start, end, text in lines:
        tokens: list[str] = []
        for char in text:
            if unicodedata.category(char)[0] in "PZS":
                require(bool(tokens), "fixture_starts_with_punctuation")
                tokens[-1] += char
            else:
                tokens.append(char)
        lo, span = start * 1000, (end - start) * 1000
        words = [{"text": token, "start_time_ms": lo + span * i // len(tokens),
                  "end_time_ms": lo + span * (i + 1) // len(tokens),
                  "speaker_id": speaker, "confidence": 0.96} for i, token in enumerate(tokens)]
        utterances.append({"text": text, "start_time_ms": lo, "end_time_ms": end * 1000,
                           "speaker_id": speaker, "definite": True, "confidence": 0.96, "words": words})
    return {"text": "\n".join(line[2] for line in lines), "duration_ms": duration * 1000,
            "utterances": utterances, "words": [], "confidence": 0.96}


def media_tools() -> tuple[Path, Path]:
    found = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    candidates = [Path(found)] if found else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.extend(file for package in (Path(local) / "Microsoft/WinGet/Packages").glob("Gyan.FFmpeg*")
                          for file in package.rglob("ffmpeg.exe"))
    candidates = [file for file in candidates if file.with_name("ffprobe" + file.suffix).is_file()]
    require(bool(candidates), "ffmpeg_and_ffprobe_required_no_skip")
    ffmpeg = unlinked(max(candidates, key=lambda file: file.stat().st_mtime_ns))
    return ffmpeg, unlinked(ffmpeg.with_name("ffprobe" + ffmpeg.suffix))


def build_sources(frontend: Path, asset_hashes: dict[str, str]) -> dict[str, str]:
    """Read-only legacy modes compatibility; v2 requires the complete new policy."""
    from deploy import frontend_binding as binding

    v2 = frontend.name.startswith("dist-canary-modes-v2-")
    receipt = frontend / "MODE_BUILD_BINDING.json"
    if not receipt.exists():
        require(not v2, "v2_build_binding_required")
        return {}  # External custom builds still require their real asset hashes.
    require(unlinked(receipt).stat().st_size <= 1024**2, "bounded_build_binding")
    value = binding.parse_binding(binding.read_regular(receipt))
    require(value.get("kind") in {"synthetic-mode-custom-build", "frontend-source-build"}
            and value.get("assets") == asset_hashes and value.get("sourceUnchangedDuringBuild") is True,
            "custom_build_binding_mismatch")
    hashes = value.get("sourceHashes")
    require(isinstance(hashes, dict) and bool(hashes), "missing_custom_build_source_hashes")
    # Old modes receipts never contained an after-map. Do not invent/rewrite it;
    # if supplied it must match, and v2 always requires it below.
    if "sourceHashesAfter" in value:
        require(value["sourceHashesAfter"] == hashes, "custom_build_source_drift")
    for name, digest in hashes.items():
        require(isinstance(name, str) and binding.source_name(name),
                "unsafe_custom_build_source")
        require(isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) is not None
                and sha256(PROJECT_ROOT / name) == digest, "frontend_source_changed_after_build")
    require(len({name.casefold() for name in hashes}) == len(hashes), "case_aliased_build_sources")
    manifest = binding.read_regular(frontend / binding.MANIFEST_NAME)
    require(binding.manifest_hashes(manifest) == asset_hashes, "custom_build_manifest_mismatch")
    if "assetManifestSHA256" in value or v2:
        require(value.get("assetManifestSHA256") == sha256(frontend / binding.MANIFEST_NAME),
                "custom_build_manifest_hash_mismatch")
    if v2 or value["kind"] == "frontend-source-build":
        binding.validate_frontend_binding(PROJECT_ROOT, frontend)
    helpers = value.get("helperHashes")
    if v2 and value["kind"] == "synthetic-mode-custom-build":
        required = {"frontend/e2e/v2/build.mjs", "frontend/e2e/modes/io.mjs"}
        if value.get("cssEvidence", {}).get("engine") == "lightningcss":
            required.add("frontend/e2e/v2/lightning-css.mjs")
        require(isinstance(helpers, dict) and set(helpers) == required, "complete_v2_build_helpers_required")
    if helpers is not None:
        require(isinstance(helpers, dict), "invalid_build_helpers")
        for name, digest in helpers.items():
            require(name in {"frontend/e2e/v2/build.mjs", "frontend/e2e/modes/io.mjs",
                             "frontend/e2e/v2/lightning-css.mjs"}
                    and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest) is not None,
                    "unsafe_build_helper")
            require(sha256(unlinked(PROJECT_ROOT / name)) == digest, "build_helper_changed_after_build")
    return hashes


class Boundaries(NetworkGuard):
    """Extend exact-fake-port socket/HTTP/WS guards with dotenv/process guards."""

    def __init__(self, port: int, root: Path, manifest: Path, tools: tuple[Path, Path]) -> None:
        super().__init__(port, root, manifest)
        self.tools = {path.resolve() for path in tools}
        self.checking = False
        self.self_check_denials = 0
        self.media_processes = 0

    def deny(self) -> None:
        if self.checking:
            self.self_check_denials += 1
            raise SafetyError("expected_guard_self_check_denial")
        super().deny()

    def file_boundary(self, value: Any, *, write: bool) -> None:
        original_extended: Path | None = None
        if os.name == "nt" and isinstance(value, (str, bytes, os.PathLike)):
            raw = os.fsdecode(value)
            if raw.startswith(("\\\\", "//", "\\??\\")):
                # Only the ordinary local absolute-drive spelling is equivalent.
                # Never turn UNC, devices, drive-relative or NT namespaces into
                # a local path. This changes comparison ONLY, not the I/O argument.
                if re.match(r"^\\\\\?\\[A-Za-z]:\\", raw) is None:
                    self.deny()
                ordinary = raw[4:]
                components = ordinary[3:].split("\\")
                if any(not part or part in {".", ".."} or part.endswith((".", " "))
                       or any(ord(char) < 32 or char in '/:<>"|?*' for char in part)
                       or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part)
                       for part in components):
                    self.deny()
                original_extended = Path(raw)
                value = ordinary
        super().file_boundary(value, write=write)
        if isinstance(value, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(value)).absolute().resolve()
            if path.is_relative_to(PROJECT_ROOT / "frontend/dist") or path.is_relative_to(PROJECT_ROOT / "eval"):
                self.deny()
            if original_extended is not None:
                # The new spelling grants no general read access outside the
                # owned root. Resolve first so aliases cannot hide an escape;
                # also reject links/junctions even when their target is owned.
                if not path.is_relative_to(self.root) and path != self.manifest:
                    self.deny()
                try:
                    unlinked(original_extended, exists=False)
                except (SafetyError, OSError, ValueError):
                    self.deny()

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        super().audit(event, args)
        if event in {"os.system", "os.startfile", "os.exec", "os.posix_spawn"}:
            self.deny()
        elif event == "sqlite3.connect":
            # This host has no accounts/legacy database. No reason to open one.
            self.deny()
        elif event == "shutil.rmtree":
            self.file_boundary(args[0], write=True)
        elif event == "subprocess.Popen":
            # Windows converts the argv list to one command-line string BEFORE
            # emitting this audit event. Validate original argv in __init__,
            # then bind the audit event to that thread-local reviewed command.
            approved = getattr(self.local, "native_command", None)
            expected = subprocess.list2cmdline(approved) if approved and os.name == "nt" else approved
            if not approved or args[1] != expected:
                self.deny()
            self.media_processes += 1

    def install(self, stack: ExitStack) -> None:
        # aiohttp -> platform may otherwise shell out to cmd.exe / ver.
        stack.enter_context(patch("platform._syscmd_ver", return_value=("", "", "")))
        original_popen = subprocess.Popen.__init__

        def popen(process: Any, args: Any, *extra: Any, **kwargs: Any) -> None:
            if not isinstance(args, (list, tuple)) or not args or extra or kwargs.get("shell"):
                self.deny()
            command = [os.fsdecode(value) for value in args]
            executable = shutil.which(os.fsdecode(kwargs.get("executable") or command[0]))
            if not executable or Path(executable).resolve() not in self.tools:
                self.deny()
            command[0] = executable
            for text in command[1:]:
                if re.search(r"(?i)(?:https?|wss?|ftp|tcp|udp|rtsp|rtmp|srt|smb|concat|subfile):", text):
                    self.deny()
                if Path(text).is_absolute():
                    self.file_boundary(text, write=False)
                    if not Path(text).resolve().is_relative_to(self.root):
                        self.deny()
            cwd = Path(kwargs.get("cwd") or self.root).absolute().resolve()
            if cwd != self.root and not cwd.is_relative_to(self.root):
                self.deny()
            input_format = None
            for index, text in enumerate(command[:-1]):
                if text == "-f":
                    input_format = command[index + 1]
                elif text == "-i":
                    source = command[index + 1]
                    if input_format != "lavfi" and source not in {"pipe:0", "-"}:
                        target = unlinked(Path(source) if Path(source).is_absolute() else cwd / source)
                        if not target.is_relative_to(self.root):
                            self.deny()
                    input_format = None
            kwargs["cwd"], kwargs["env"] = str(cwd), dict(os.environ)
            previous = getattr(self.local, "native_command", None)
            self.local.native_command = command
            try:
                original_popen(process, command, **kwargs)
            finally:
                self.local.native_command = previous

        stack.enter_context(patch.object(subprocess.Popen, "__init__", popen))
        super().install(stack)
        from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource
        stack.enter_context(patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}))
        stack.enter_context(patch.object(DotEnvSettingsSource, "_read_env_file", return_value={}))
        stack.enter_context(patch("dotenv.dotenv_values", return_value={}))
        stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
        original_is_file, original_exists = Path.is_file, Path.exists

        def is_file(path: Path) -> bool:
            if path.name == ".env" or path.name.startswith(".env."):
                return False
            return original_is_file(path)

        def exists(path: Path) -> bool:
            # Do not even mount or inspect the user's shared frontend build.
            if path.absolute() == PROJECT_ROOT / "frontend/dist":
                return False
            return original_exists(path)

        stack.enter_context(patch.object(Path, "is_file", is_file))
        stack.enter_context(patch.object(Path, "exists", exists))

    def file_self_check(self) -> None:
        """Boundary decisions only: never open protected files or create links."""
        previous = self.checking
        self.checking = True
        try:
            owned = self.root / "tasks/guard-path-check/revisions/r0/final.mp4"
            self.file_boundary(owned, write=True)
            if os.name != "nt":
                return

            def extended(path: Path) -> str:
                return "\\\\?\\" + str(path)

            for value in (extended(owned), Path(extended(owned)), os.fsencode(extended(owned)),
                          extended(owned.parent / ("x" * 180) / "artifact.json")):
                for write in (False, True):
                    self.file_boundary(value, write=write)
            denied = [
                self.root.parent / "outside-guard-owned-root/artifact.json",
                self.root / ".env", self.root / ".env.test",
                PROJECT_ROOT / ".env", PROJECT_ROOT / "data/guard-no-read.json",
                PROJECT_ROOT / "eval_sample/guard-no-read.json",
                PROJECT_ROOT / "eval/guard-no-read.json",
                PROJECT_ROOT / "canary_test/artifacts/guard-no-read.json",
                PROJECT_ROOT / "frontend/dist/index.html",
            ]
            invalid = [
                "\\\\?\\UNC\\server\\share\\file", "\\\\server\\share\\file", "//server/share/file",
                "\\\\.\\C:\\file", "\\??\\C:\\file", "\\\\?\\GLOBALROOT\\Device\\file",
                "\\\\?\\C:relative", "\\\\?\\relative", "\\\\?\\C:/file",
                extended(owned.parent / ".." / "file"), extended(owned) + ":stream",
                extended(owned) + ".", extended(owned) + " ", extended(owned.parent / "NUL"),
            ]
            for value in [*(extended(path) for path in denied), *invalid]:
                for write in (False, True):
                    try:
                        self.file_boundary(value, write=write)
                    except SafetyError:
                        continue
                    raise SafetyError("extended_path_guard_self_check_failed")
            # Deterministic link metadata, no symlink privilege or target I/O.
            # Include an ancestor reparse point as well as a leaf symlink.
            original_lstat = Path.lstat
            for linked, info in (
                (Path(extended(owned)), SimpleNamespace(st_mode=0o120777, st_file_attributes=0)),
                (Path(extended(owned.parent)), SimpleNamespace(st_mode=0o040777, st_file_attributes=0x400)),
            ):
                def lstat(path: Path, _linked: Path = linked, _info: Any = info) -> Any:
                    return _info if path == _linked else original_lstat(path)

                with patch.object(Path, "lstat", lstat):
                    for write in (False, True):
                        try:
                            self.file_boundary(extended(owned), write=write)
                        except SafetyError:
                            continue
                        raise SafetyError("extended_link_guard_self_check_failed")
        finally:
            self.checking = previous

    def self_check(self) -> None:
        import httpx
        import websockets

        self.file_self_check()
        self.checking = True
        try:
            for operation in (
                lambda: self.file_boundary(PROJECT_ROOT / ".env", write=False),
                lambda: self.file_boundary(PROJECT_ROOT / "data", write=False),
                lambda: self.file_boundary(PROJECT_ROOT / "eval_sample", write=False),
                lambda: self.file_boundary(PROJECT_ROOT / "frontend/dist/index.html", write=False),
                lambda: self.file_boundary(PROJECT_ROOT / "backend/forbidden", write=True),
                lambda: self.audit("socket.connect", (None, ("127.0.0.1", 8000))),
                lambda: self.audit("socket.connect", (None, ("203.0.113.1", 443))),
                lambda: self.audit("sqlite3.connect", (":memory:",)),
            ):
                try:
                    operation()
                except SafetyError:
                    continue
                raise SafetyError("guard_self_check_failed")
            # These are intercepted BEFORE DNS/transport, not real requests.
            with httpx.Client(trust_env=False) as client:
                for operation in (
                    lambda: client.get("http://127.0.0.1:8000/forbidden"),
                    lambda: client.get("https://safe.test/forbidden"),
                    lambda: websockets.connect("ws://127.0.0.1:8000/forbidden"),
                ):
                    try:
                        operation()
                    except SafetyError:
                        continue
                    raise SafetyError("http_websocket_guard_self_check_failed")
            first, second = socket.socketpair()
            first.close()
            second.close()
        finally:
            self.checking = False


def make_settings(root: Path, fake_port: int) -> Any:
    from backend.config import Settings

    values = {name: field.get_default(call_default_factory=True) for name, field in Settings.model_fields.items()}
    provider_url = f"http://127.0.0.1:{fake_port}/v1"
    for name in values:
        if re.search(r"api_keys?|app_key|app_id|access_token|secret|password|credential", name):
            values[name] = ""
        if name.endswith("_base_url"):
            values[name] = provider_url
    values.update(
        app_env="test", enable_api_docs=False, data_dir=root / "tasks", asr_cache_dir=root / "cache/asr",
        music_library_dir=PROJECT_ROOT / "backend/assets/music", asr_cache_enabled=False,
        asr_sparse_retry_enabled=False, sync_sound_enabled=False, sync_sound_vad_enabled=False,
        video_embedding_enabled=False, entity_verification_enabled=False, generative_fill_enabled=False,
        generative_fill_max_clips=0, task_ttl_hours=72, min_free_disk_gb=0,
        max_concurrent_tasks=1, max_pending_tasks=5, max_files=20, max_shots=120,
        max_upload_mb=16, max_total_upload_mb=192, max_source_duration_seconds_per_file=120,
        max_total_source_duration_seconds=600, max_sentences=40, max_concurrent_uploads=1,
        task_rate_limit_per_hour=20, media_command_timeout_seconds=180, shutdown_grace_seconds=45,
        quality_gate_mode="warn", frontend_origins=BASE_URL, allowed_hosts="127.0.0.1", enforce_origin_check=True,
        vision_provider="openai-compatible", vision_model="workspace-vision",
        embedding_provider="openai-compatible", embed_model="workspace-embedding",
        llm_provider="openai-compatible", llm_model="workspace-rerank", kimi_model="workspace-segmentation",
        tts_provider="openai", tts_model="workspace-tone-tts", tts_voice="synthetic-tone-not-speech",
        volcengine_asr_base_url=f"ws://127.0.0.1:{fake_port}/asr-disabled",
    )
    for name in ("vision_api_key", "embed_api_key", "llm_api_key", "kimi_api_key", "tts_api_key"):
        values[name] = "synthetic-placeholder-not-a-credential"
    return Settings(_env_file=None, **values)


def fixture_contract() -> dict[str, Any]:
    from backend.production_modes import SentenceInput, align_quotes, parse_sentences
    from backend.providers.asr import ASRTranscript
    from backend.uploads import UploadStore, _Wave, _editorial_transcript

    require(list(inspect.signature(UploadStore._transcribe).parameters) == ["self", "record"], "upload_transcribe_signature_changed")
    mixed = parse_sentences(MIXED_SCRIPT, "mixed")
    original = parse_sentences(ORIGINAL_SCRIPT, "original")
    require(len(mixed) == 16 and sum(row.kind == "narration" for row in mixed) == 11
            and [row.idx for row in mixed if row.kind == "quote"] == [3, 7, 8, 12, 15], "mixed_sample_must_be_11_plus_5")
    require(len(original) == 8 and all(row.kind == "quote" for row in original), "original_sample_must_be_8")
    snapshots = []
    for index, item in enumerate(INTERVIEWS):
        transcript = ASRTranscript.model_validate(provider_fixture(item[0]))
        require(all("".join(word.text for word in u.words) == u.text for u in transcript.utterances), "fixture_word_coverage")
        # STRUCTURAL dry check only: no waveform samples/energy/SNR are invented.
        # The running host invokes the same adapter with genuinely decoded PCM.
        record = SimpleNamespace(id=f"up_{index:032x}", probe=SimpleNamespace(sec=float(item[1]), audio_offset=0.0))
        editorial = _editorial_transcript(record, transcript, _Wave([], [], 0, [], None, "unknown"), threading.Event())
        require(editorial.precision == "word", "synthetic_words_must_pass_real_editorial_adapter")
        snapshots.append({"id": record.id, "sec": float(item[1]), "status": "ready", "has_speech": True,
                          "transcript": editorial.model_dump(mode="json"), "silences": []})
    matches = align_quotes(original, snapshots)
    require(len(matches) == 8 and all(row["source"] and row["score"] >= 0.85 for row in matches), "original_alignment_fixture_contract")
    require(len([row for row in align_quotes(mixed, snapshots) if row["kind"] == "quote" and row["source"]]) == 5,
            "mixed_alignment_fixture_contract")
    missing = align_quotes([SentenceInput(idx=0, text="今年我们还请了舞狮队", kind="quote")], snapshots[2:])
    require(missing[0]["source"] is None, "missing_quote_must_actually_be_missing")
    scripts = {"mixed": MIXED_SCRIPT, "original": ORIGINAL_SCRIPT, "voiceover": VOICEOVER_SCRIPT,
               "shortMixed": SHORT_MIXED_SCRIPT, "shortOriginal": SHORT_ORIGINAL_SCRIPT}
    return {"scripts": scripts, "mixedSentences": [row.model_dump(mode="json") for row in mixed],
            "expected": {"mixedNarration": 11, "mixedQuotes": 5, "originalQuotes": 8,
                         "unpaddedTranscriptSeconds": 57, "originalDurationBounds": [50, 65],
                 "snr14Claimed": False, "exact55SecondsClaimed": False},
            "dryAlignment": [{"idx": row["idx"], "text": original[row["idx"]].text,
                      "asr_text": row["source"]["asr_text"], "score": row["score"],
                      "start": row["source"]["start"], "end": row["source"]["end"]} for row in matches]}


def create_inputs(root: Path) -> list[dict[str, Any]]:
    directory = root / "inputs"
    directory.mkdir()
    specs = [(item[0], item[1], "interview", item) for item in INTERVIEWS]
    specs += [(f"synthetic-broll-{i + 1}.mp4", 3, "broll", None) for i in range(4)]
    result = []
    for index, (name, seconds, kind, fixture) in enumerate(specs):
        target = directory / name
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                   "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={seconds}"]
        if kind == "interview":
            command += ["-f", "lavfi", "-i", f"sine=frequency={440 + index * 170}:sample_rate=48000:duration={seconds}"]
        command += ["-vf", f"hue=h={index * 47}", "-c:v", "libx264", "-threads", "1", "-preset", "ultrafast",
                    "-crf", "32", "-pix_fmt", "yuv420p"]
        command += ["-c:a", "aac", "-b:a", "96k", "-shortest"] if kind == "interview" else ["-an"]
        media_command(command + ["-movflags", "+faststart", str(target)])
        probe = json.loads(media_command(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                                        "-show_streams", "-show_format", "-of", "json", str(target)]))
        video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
        require(video["width"] == 320 and video["height"] == 180 and video["r_frame_rate"] == "30/1", "input_video_geometry")
        require(abs(float(probe["format"]["duration"]) - seconds) <= 0.1, "input_duration")
        require(0 < target.stat().st_size <= 16 * 1024**2, "bounded_input_size")
        item: dict[str, Any] = {"path": str(target), "name": name, "kind": kind, "synthetic": True,
                               "bytes": target.stat().st_size, "sha256": sha256(target),
                               "durationSeconds": float(probe["format"]["duration"]),
                               "audioKind": "sine-tone-not-speech" if fixture else "no-audio",
                               "speaker": None, "segments": []}
        if fixture:
            item["speaker"] = {"id": fixture[2], "name": fixture[3], "title": fixture[4]}
            item["segments"] = [{"id": f"seg_{n}", "start": float(lo), "end": float(hi), "text": text}
                                for n, (lo, hi, text) in enumerate(fixture[5])]
        result.append(item)
    return result


class Manifest:
    def __init__(self, path: Path, state: dict[str, Any]) -> None:
        self.path, self.state = path, state
        self.stream = path.open("x+", encoding="utf-8", newline="\n")
        self.flush()

    def flush(self) -> None:
        require(os.path.samestat(os.fstat(self.stream.fileno()), self.path.stat()), "manifest_ownership_lost")
        self.stream.seek(0)
        json.dump(self.state, self.stream, indent=2, ensure_ascii=True, allow_nan=False)
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


def configure(root: Path, manifest: Path, fake_port: int, tools: tuple[Path, Path], stack: ExitStack) -> tuple[Boundaries, Any]:
    environment = isolated_environment(root)
    environment.update(PATH=str(tools[0].parent) + os.pathsep + next((v for k, v in environment.items() if k.lower() == "path"), ""),
                       DATA_DIR=str(root / "tasks"), ASR_CACHE_DIR=str(root / "cache/asr"),
                       PYTHONUTF8="1", PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1")
    stack.enter_context(patch.dict(os.environ, environment, clear=True))
    stack.enter_context(patch.object(tempfile, "tempdir", str(root / "tmp")))
    guard = Boundaries(fake_port, root, manifest, tools)
    guard.install(stack)
    guard.self_check()
    settings = make_settings(root, fake_port)
    stack.enter_context(patch("backend.config.get_settings", return_value=settings))
    return guard, settings


def run_host(root: Path, manifest: Manifest, frontend: Path, listener: socket.socket,
             tools: tuple[Path, Path]) -> None:
    state = manifest.state
    with ExitStack() as stack:
        provider = stack.enter_context(fake_provider())
        guard, settings = configure(root, manifest.path, provider.server_port, tools, stack)
        fault = stack.enter_context((root / "native-fault.log").open("x", encoding="utf-8"))
        faulthandler.enable(file=fault, all_threads=True)
        stack.callback(faulthandler.disable)
        state.update(fixture_contract())
        counts: Counter[str] = Counter()
        from backend.uploads import UploadStore
        from backend.providers.asr import ASRTranscript

        async def transcribe(store: Any, record: Any) -> Any:
            expected = next((item for item in state["inputs"] if item["name"] == record.name), None)
            if not expected or expected["kind"] != "interview" or record.sha256 != expected["sha256"] or record.size != expected["bytes"]:
                counts["unexpected"] += 1
                raise SafetyError("asr_fixture_name_and_hash_required")
            audio = unlinked(store.root / record.id / "audio.wav")
            require(audio.is_relative_to(root / "tasks/_uploads"), "asr_audio_must_be_owned")
            with wave.open(str(audio), "rb") as decoded:
                require(decoded.getnchannels() == 1 and decoded.getframerate() == 16000
                        and decoded.getnframes() > 0, "real_upload_audio_decode_required")
            record.asr_attempted = True
            store._save(record)
            counts[record.name] += 1
            record.asr_cached_at = time.time()
            return ASRTranscript.model_validate(provider_fixture(record.name))

        stack.enter_context(patch.object(UploadStore, "_transcribe", transcribe))
        from backend.main import app, task_manager
        from backend.task_operations import trusted_local_request
        from fastapi import HTTPException, Request
        from fastapi.responses import JSONResponse
        from starlette.background import BackgroundTask
        from starlette.staticfiles import StaticFiles
        import uvicorn

        # Fail closed while the parent is still implementing the real adapters.
        required = {("POST", "/api/uploads"), ("POST", "/api/tasks"), ("POST", "/api/match/preview"),
                    ("POST", "/api/tasks/{task_id}/remix"), ("GET", "/api/tasks/{task_id}/checks"),
                    ("POST", "/api/tasks/{task_id}/checks"), ("GET", "/api/tasks/{task_id}/quotes/{row_id}/takes"),
                    ("GET", "/api/tasks/{task_id}/waves/{row_id}.json"), ("POST", "/api/tasks/{task_id}/export"),
                    ("GET", "/api/tasks/{task_id}/exports/{export_id}")}
        # Parameter *names* are not part of the wire contract (row_id vs id).
        shape = lambda path: re.sub(r"\{[^}/]+\}", "{}", path)
        # FastAPI may retain included routers as lazy branches rather than
        # flattening their routes. Its generated schema resolves those branches;
        # the browser tests still exercise the actual HTTP endpoints afterwards.
        actual = {(method.upper(), shape(path))
              for path, operations in app.openapi()["paths"].items()
              for method in operations
              if method in {"get", "post", "put", "patch", "delete", "head", "options"}}
        state["missingProductRoutes"] = [f"{method} {path}" for method, path in sorted(required) if (method, shape(path)) not in actual]
        manifest.flush()
        require(not state["missingProductRoutes"], "mode_api_contract_incomplete_do_not_add_fake_routes")
        stack.enter_context(patch("backend.readiness.FRONTEND_INDEX", frontend / "index.html"))
        stack.enter_context(patch("backend.readiness.FRONTEND_MANIFEST", frontend / "ASSET_MANIFEST.sha256"))
        fallback = next(route for route in app.router.routes if getattr(route, "name", None) == "missing_api")
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, "name", None) not in {"frontend", "missing_api"}]
        globals().update(Request=Request, JSONResponse=JSONResponse)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, workers=1,
            access_log=False, proxy_headers=False, lifespan="on", timeout_graceful_shutdown=60,
            log_config={"version": 1, "disable_existing_loggers": False,
                        "handlers": {"quiet": {"class": "logging.NullHandler"}},
                        "loggers": {name: {"handlers": ["quiet"], "propagate": False}
                                    for name in ("uvicorn", "uvicorn.error", "uvicorn.access")}}))

        def local(request: Any, *, mutation: bool = False) -> None:
            if not trusted_local_request(request, settings, mutation=mutation):
                raise HTTPException(404, "Acceptance endpoint unavailable")

        @app.get("/api/test/modes", include_in_schema=False)
        async def identity(request: Request) -> JSONResponse:
            local(request)
            return JSONResponse({"instanceId": state["instanceId"], "synthetic": True, "baseURL": BASE_URL,
                "serverState": state["serverState"], "providerCalls": provider.snapshot(),
                "uploadASRCalls": dict(counts), "deniedEgress": guard.denied,
                "guardSelfCheckDenials": guard.self_check_denials, "policy": state["policy"]})

        @app.post("/api/test/modes/shutdown", include_in_schema=False)
        async def shutdown(request: Request) -> JSONResponse:
            local(request, mutation=True)
            if request.headers.get("content-length") is None or int(request.headers["content-length"]) > 256:
                raise HTTPException(413, "Bounded ownership marker required")
            if await request.json() != {"instanceId": state["instanceId"]}:
                raise HTTPException(409, "Acceptance instance mismatch")
            task_manager.begin_drain()
            return JSONResponse({"status": "stopping"}, background=BackgroundTask(setattr, server, "should_exit", True))

        @app.middleware("http")
        async def mark_instance(request: Request, call_next: Any) -> Any:
            response = await call_next(request)
            response.headers["X-Modes-Acceptance"] = state["instanceId"]
            response.headers["X-Modes-Synthetic"] = "true"
            return response

        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application: Any) -> Any:
            try:
                async with original_lifespan(application):
                    state["preflightChecks"] = application.state.startup_readiness.checks
                    require(application.state.startup_readiness.ready, "real_preflight_failed")
                    require(task_manager.list_history()["total"] == 0, "fresh_empty_history_required")
                    state["inputs"] = await asyncio.to_thread(create_inputs, root)
                    fixture_path = root / "synthetic-asr-fixtures.json"
                    with fixture_path.open("x", encoding="utf-8") as output:
                        json.dump({"synthetic": True, "disclosure": DISCLOSURE,
                                   "fixtures": {item[0]: provider_fixture(item[0]) for item in INTERVIEWS}}, output, ensure_ascii=True)
                    state["sourceArtifacts"] = [{"path": str(fixture_path), "kind": "injected-asr-not-speech-evidence",
                                                 "sha256": sha256(fixture_path)}]
                    state["serverState"] = "ready"
                    manifest.flush()
                    yield
                state["lifespanShutdownComplete"] = True
                state["serverState"] = "stopping"
            finally:
                state.update(providerCalls=provider.snapshot(), uploadASRCalls=dict(counts),
                             deniedEgress=guard.denied, guardSelfCheckDenials=guard.self_check_denials,
                             nativeMediaProcesses=guard.media_processes)
                state["inputHashesUnchanged"] = all(sha256(Path(item["path"])) == item["sha256"] for item in state["inputs"])
                manifest.flush()

        app.router.lifespan_context = lifespan
        app.router.routes.append(fallback)
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass  # Only successful lifespan + provider closure may say stopped.
        require(state["lifespanShutdownComplete"], "lifespan_shutdown_unconfirmed")
    # Both the real application and the owned fake-provider thread have closed.
    listener.close()
    state.update(serverState="stopped", shutdownComplete=True, fakeProviderStopped=True)
    manifest.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--frontend-dir", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    require(not any(name == "backend" or name.startswith("backend.") for name in sys.modules), "fresh_python_process_required")
    sys.dont_write_bytecode = True
    system_temp = unlinked(Path(tempfile.gettempdir()))
    require(not system_temp.is_relative_to(PROJECT_ROOT), "system_temp_must_be_outside_workspace")
    tools = media_tools()
    if args.check_only:
        root = unlinked(Path(tempfile.mkdtemp(prefix="golden-mic-modes-check-", dir=system_temp)))
        for name in ("tmp", "tasks", "cache"):
            (root / name).mkdir()
        with ExitStack() as stack:
            guard, settings = configure(root, root / "unused.json", 1, tools, stack)
            contract = fixture_contract()
            require(settings.max_concurrent_tasks == 1 and settings.max_pending_tasks == 5
                    and settings.task_ttl_hours == 72 and settings.max_files == 20, "settings_limits")
            require(guard.denied == 0 and guard.media_processes == 0, "check_only_must_not_render_or_connect")
            for executable in tools:
                # Version queries prove the Windows argv/audit allowlist; no
                # input, codec job, provider, port or frontend build is involved.
                media_command([str(executable), "-hide_banner", "-version"])

            async def async_version() -> None:
                process = await asyncio.create_subprocess_exec(str(tools[0]), "-version",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await asyncio.wait_for(process.communicate(), timeout=20)
                require(process.returncode == 0, "async_native_version_check_failed")

            asyncio.run(async_version())
            require(guard.media_processes == 3, "native_media_guard_not_exercised")
            print(json.dumps({"status": "static_and_fixture_checks_passed", "guardSelfCheckDenials": guard.self_check_denials,
                              "expected": contract["expected"], "listeners": 0, "renders": 0,
                              "nativeToolVersionChecks": 3, "appRoutesChecked": False, "browserExecuted": False,
                              "dryAlignment": contract["dryAlignment"], "temporaryRoot": str(root)}), flush=True)
        return
    require(args.manifest is not None and args.frontend_dir is not None, "explicit_manifest_and_custom_frontend_required")
    require(args.frontend_dir.is_absolute() and args.frontend_dir.name != "dist", "custom_frontend_only_shared_dist_forbidden")
    frontend, build_hashes = validate_frontend(args.frontend_dir)
    require(re.fullmatch(r"dist-canary-[A-Za-z0-9][A-Za-z0-9_-]{0,63}", frontend.name) is not None, "custom_frontend_name")
    frontend_sources = build_sources(frontend, build_hashes)
    require(args.manifest.is_absolute(), "absolute_manifest_required")
    path = unlinked(args.manifest, exists=False)
    require(path.is_relative_to(system_temp) and not path.is_relative_to(PROJECT_ROOT)
            and path.suffix == ".json" and not path.exists() and path.parent.is_dir(), "unused_temp_manifest_required")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # No process lookup/kill and no seed or manifest before successful bind.
        listener.bind(("127.0.0.1", PORT))
        listener.listen(128)
        listener.setblocking(False)
        root = unlinked(Path(tempfile.mkdtemp(prefix="golden-mic-modes-", dir=system_temp)))
        for name in ("tmp", "tasks", "cache"):
            (root / name).mkdir()
        state: dict[str, Any] = {
            "schemaVersion": 1, "kind": "golden-mic-mode-acceptance", "instanceId": uuid4().hex,
            "synthetic": True, "disclosure": DISCLOSURE, "baseURL": BASE_URL, "pid": os.getpid(),
            "root": str(root), "taskRoot": str(root / "tasks"), "serverState": "starting", "inputs": [],
            "frontend": {"directory": str(frontend), "hashes": build_hashes,
                         "sourceHashes": frontend_sources, "sourceBound": bool(frontend_sources)},
            "tools": {"ffmpeg": str(tools[0]), "ffprobe": str(tools[1])},
            "sourceHashes": {file.relative_to(PROJECT_ROOT).as_posix(): sha256(file) for file in
                             sorted({*PROJECT_ROOT.glob("backend/**/*.py"), PROJECT_ROOT / "backend/mode_rules.json", Path(__file__),
                                     PROJECT_ROOT / "tests/workspace_fixture.py"})},
                "testHashes": {file.relative_to(PROJECT_ROOT).as_posix(): sha256(file) for file in
                       sorted({PROJECT_ROOT / "frontend/playwright.modes.config.ts",
                           *(file for file in (PROJECT_ROOT / "frontend/e2e/modes").iterdir() if file.is_file())})},
            "policy": {"dotenv": False, "inheritedSettings": False, "realData": False, "paidCalls": False,
                       "accounts": False, "seededTasks": False, "qcPatched": False, "allTenStages": True,
                       "asr": "record-name-and-sha256-injected", "wordTiming": "evenly-spaced-synthetic-labels",
                       "audio": "sine-tones-not-human-speech", "httpEgress": "exact-fake-listener-only",
                       "websocketEgress": "deny-all", "sharedDistAccess": False, "automaticRetries": False},
            "limits": {"workers": 1, "pending": 5, "files": 20, "ttlHours": 72, "minFreeGB": 0,
                       "quality": "warn", "generative": False},
            "shutdownComplete": False, "lifespanShutdownComplete": False, "fakeProviderStopped": False,
        }
        manifest = Manifest(path, state)
        print(f"MODES_MANIFEST={path}", flush=True)
        print(f"MODES_URL={BASE_URL} (synthetic; wait for manifest serverState=ready)", flush=True)
        try:
            run_host(root, manifest, frontend, listener, tools)
        except BaseException as failure:
            state.update(serverState="failed", failureType=type(failure).__name__)
            if isinstance(failure, SafetyError):
                state["failureCode"] = str(failure)
            manifest.flush()
            raise
        finally:
            manifest.close()
        print("MODES_STATUS=stopped (owned TEMP retained; no automatic cleanup)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("MODES_STATUS=interrupted (orderly shutdown NOT claimed)", flush=True)
        raise SystemExit(130) from None
    except Exception as exc:
        code = str(exc) if isinstance(exc, SafetyError) else "host_failure"
        print(f"MODES_STATUS=failed type={type(exc).__name__} code={code}", flush=True)
        raise SystemExit(1) from None