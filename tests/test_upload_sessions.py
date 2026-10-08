"""Focused uploads/ASR tests: TEMP only, fake providers, no dotenv or paid calls.

Real-media cases use local FFmpeg's synthetic color/sine/silence sources. They
prove decoding/limits/20ms waveform mechanics, NOT real ASR quality or RTF <= .3.
Run this module under the project's isolated validation guards when available.
"""
from __future__ import annotations

import array
import asyncio
import gzip
import hashlib
import json
import math
import os
import shutil
import stat
import struct
import tempfile
import threading
import unittest
import wave
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend.config import Settings
from backend.models import UploadedAsset
from backend.production_modes import QuoteTake, SentenceInput, align_quotes, validate_quote_trim
from backend.providers.asr import (
    ASRProvider, ASRProviderError, ASRTranscript, ASRUtterance, ASRWord,
    VolcengineASRProvider, _transcript_from_payload,
)
from backend import uploads as module
from backend.uploads import CHUNK_SIZE, UploadStore, create_upload_router


def _settings(root: Path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, **{
        "app_env": "test", "data_dir": root / "tasks", "asr_cache_dir": root / "cache",
        "min_free_disk_gb": 0, "sync_sound_vad_enabled": False,
        "volcengine_app_id": "", "volcengine_access_token": "",
        "volcengine_asr_base_url": "wss://invalid.test/no-network",
        **overrides,
    })


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _speaker_id(upload_id: str, source_id: str) -> str:
    return upload_id + "_spk_" + _sha(source_id.encode("utf-8"))


def _png_header(width: int, height: int) -> bytes:
    header = b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\0\0\0"
    return b"\x89PNG\r\n\x1a\n\0\0\0\r" + header + struct.pack(">I", zlib.crc32(header))


def _gif_data(width: int = 1, height: int = 1, frames: int = 2, *, second_rect: tuple[int, int, int, int] | None = None) -> bytes:
    # Two-color synthetic animation: red first frame, blue second. Oversized
    # header fixtures are rejected before decoding; no large bitmap allocation.
    header = b"GIF89a" + struct.pack("<HH", width, height) + b"\x80\0\0\xff\0\0\0\0\xff"
    result = bytearray(header)
    for index in range(frames):
        rect = second_rect if index == 1 and second_rect is not None else (0, 0, width, height)
        result.extend(b"\x21\xf9\x04\0\x0a\0\0\0\x2c" + struct.pack("<HHHHB", *rect, 0))
        result.extend(b"\x02\x02" + bytes([0x44 if index % 2 == 0 else 0x4C, 0x01, 0]))
    return bytes(result) + b"\x3b"


def _photo_data(suffix: str) -> bytes:
    if suffix.lower() == ".gif":
        return _gif_data()
    import cv2
    import numpy as np
    image = np.full((48, 64, 3), (20, 70, 210), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg" if suffix.lower() == ".jpeg" else suffix.lower(), image)
    if not ok:
        raise AssertionError("Local synthetic photo encoding failed")
    return encoded.tobytes()


async def _stream(data: bytes, block: int = 65536):
    for start in range(0, len(data), block):
        yield data[start:start + block]


def _pcm(path: Path, values: list[int]) -> None:
    samples = array.array("h", values)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(samples.tobytes())


def _result(*, words: bool = True, speaker: str | None = "7", confidence: float | None = 0.82) -> ASRTranscript:
    return ASRTranscript(
        text="你好世界。", duration_ms=1000, confidence=confidence,
        utterances=[ASRUtterance(
            text="你好世界。", start_time_ms=200, end_time_ms=600, speaker_id=speaker,
            confidence=confidence,
            words=[ASRWord(text="你好", start_time_ms=200, end_time_ms=400, confidence=0.91),
                   ASRWord(text="世界", start_time_ms=400, end_time_ms=600)] if words else [],
        )],
    )


class FakeProvider(ASRProvider):
    def __init__(self, sink: list[str], result: ASRTranscript | None = None, error: Exception | None = None) -> None:
        self.sink, self.result, self.error = sink, result or _result(), error
        self.base_url, self.endpoint_path = "wss://provider.invalid", "bigmodel_async"
        self.resource_id, self.cluster_id = "synthetic-resource", ""
        self.app_id, self.access_token = "synthetic-app", "synthetic-key"
        self.enable_nonstream, self.enable_speaker_info = True, True
        self.closed = False

    def validate_configuration(self) -> None:
        return None

    async def transcribe(self, audio_path: Path) -> ASRTranscript:
        self.sink.append(_sha(audio_path.read_bytes()))
        if self.error:
            raise self.error
        return self.result.model_copy(deep=True)

    async def aclose(self) -> None:
        self.closed = True


class StubStore(UploadStore):
    """Only the media producer is replaced; storage/auth/hash/worker code is real."""
    async def _probe(self, record: Any) -> Any:
        return module._Probe(sec=1.0, width=64, height=48, fps=25.0, has_audio=True, format_name="mov")

    async def _previews(self, record: Any) -> Any:
        directory = self._directory(record)
        (directory / "thumb.jpg").write_bytes(b"synthetic-thumbnail")
        _pcm(directory / "audio.wav", [33] * 3200 + [3277] * 6400 + [33] * 6400)
        analyzed = await module._blocking(module._analyze_pcm, directory / "audio.wav", 0.0, 1800.0, False, 0.2, 32)
        module._atomic_json(directory / "wave.json", {"interval_ms": 20, "rms": analyzed.rms})
        record.thumb = record.wave = True
        return analyzed


class UploadSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="gm-upload-tests-")
        self.root = Path(self.temp.name)
        self.settings = _settings(self.root)
        self.calls: list[str] = []
        self.providers: list[FakeProvider] = []
        self.provider_result = _result()
        self.provider_error: Exception | None = None
        self.credential = "synthetic-key"

        def factory(settings: Any) -> FakeProvider:
            provider = FakeProvider(self.calls, self.provider_result, self.provider_error)
            provider.access_token = self.credential
            self.providers.append(provider)
            return provider

        self.provider_patch = patch.object(module, "create_asr_provider", side_effect=factory)
        self.provider_patch.start()
        self.store = StubStore(self.settings)
        self.client = self._client(self.store)

    def _client(self, store: UploadStore, *, host: str = "127.0.0.1", base: str = "http://127.0.0.1", session: str | None = None) -> httpx.AsyncClient:
        app = FastAPI()
        if session is not None:
            @app.middleware("http")
            async def trusted_session(request: Any, call_next: Any) -> Any:
                request.state.anonymous_session_id = session
                return await call_next(request)
        app.include_router(create_upload_router(store))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 31415)), base_url=base)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.store.close()
        self.provider_patch.stop()
        self.temp.cleanup()

    async def _new(self, data: bytes = b"raw", *, owner: str = "owner-a", name: str = "source.mp4") -> dict[str, Any]:
        return await self.store.create(name=name, size=len(data), sha256=_sha(data), owner=owner)

    async def _upload(self, data: bytes = b"raw", *, owner: str = "owner-a") -> dict[str, Any]:
        created = await self._new(data, owner=owner)
        for index, start in enumerate(range(0, len(data), CHUNK_SIZE)):
            chunk = data[start:start + CHUNK_SIZE]
            await self.store.put_chunk(created["upload_id"], created["access_token"], index, _stream(chunk), len(chunk))
        return created

    async def _ready(self, data: bytes = b"raw", *, owner: str = "owner-a") -> dict[str, Any]:
        created = await self._upload(data, owner=owner)
        await self.store.complete(created["upload_id"], created["access_token"])
        job = self.store._jobs.get(created["upload_id"])
        if job is not None:
            await job
        return created

    async def _reopen(self) -> None:
        await self.client.aclose()
        await self.store.close()
        self.store = StubStore(self.settings)
        self.client = self._client(self.store)

    async def test_create_contract_token_is_only_returned_once_and_never_persisted(self) -> None:
        response = await self.client.post("/api/uploads", json={"name": "采访.mp4", "bytes": 3, "sha256": _sha(b"raw")})
        self.assertEqual(response.status_code, 201, response.text)
        created = response.json()
        self.assertEqual(set(created), {"upload_id", "access_token", "chunk_size", "put_url"})
        self.assertRegex(created["upload_id"], r"^up_[0-9a-f]{32}$")
        self.assertEqual(len(created["access_token"]), 43)
        self.assertEqual(created["chunk_size"], 8388608)
        self.assertEqual(created["put_url"], f'/api/uploads/{created["upload_id"]}/chunks/{{index}}')
        raw = (self.store.root / created["upload_id"] / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn(created["access_token"], raw)
        self.assertEqual(json.loads(raw)["token_hash"], _sha(created["access_token"].encode()))
        read = await self.client.get(f'/api/uploads/{created["upload_id"]}', headers={"X-Upload-Token": created["access_token"]})
        self.assertEqual(read.status_code, 200)
        for secret in (created["access_token"], _sha(b"raw"), str(self.root), "token_hash", "owner_hash"):
            self.assertNotIn(secret, read.text)
        self.assertEqual(read.headers["cache-control"], "no-store")
        self.assertIsNone(read.json()["is_image"])
        self.assertIsNone(read.json()["has_audio"])
        self.assertIsNone(read.json()["silence_method"])
        self.assertEqual(self.calls, [])
        self.assertFalse((self.settings.data_dir / "task_state.json").exists())

    async def test_all_existing_endpoints_require_capability_even_on_loopback(self) -> None:
        created = await self._ready()
        base = f'/api/uploads/{created["upload_id"]}'
        for method, suffix, content in (("GET", "", None), ("PUT", "/chunks/0", b"raw"),
                                        ("POST", "/complete", None), ("GET", "/thumb", None),
                                        ("GET", "/wave", None), ("DELETE", "", None)):
            for headers in ({}, {"X-Upload-Token": "bad"}, {"X-Upload-Token": "x" * 43}):
                response = await self.client.request(method, base + suffix, headers=headers, content=content)
                self.assertEqual(response.status_code, 404, (method, suffix, response.text))
        self.assertEqual(len(self.calls), 1)

    async def test_media_query_tokens_work_but_conflicts_and_nonmedia_queries_fail(self) -> None:
        created = await self._ready()
        base, token = f'/api/uploads/{created["upload_id"]}', created["access_token"]
        for suffix in ("/thumb", "/wave"):
            result = await self.client.get(base + suffix, params={"token": token})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["referrer-policy"], "no-referrer")
            result = await self.client.get(base + suffix, params={"token": token}, headers={"X-Upload-Token": "z" * 43})
            self.assertEqual(result.status_code, 404)
            result = await self.client.get(base + suffix, params=[("token", token), ("token", "")])
            self.assertEqual(result.status_code, 404)
        self.assertEqual((await self.client.get(base, params={"token": token})).status_code, 404)
        duplicate = [("X-Upload-Token", token), ("X-Upload-Token", "z" * 43)]
        self.assertEqual((await self.client.get(base, headers=duplicate)).status_code, 404)

    async def test_router_happy_path_and_completed_chunk_retries_remain_idempotent(self) -> None:
        response = await self.client.post("/api/uploads", json={"name": "file.mp4", "bytes": 3, "sha256": _sha(b"raw")})
        created = response.json()
        base = f'/api/uploads/{created["upload_id"]}'
        headers = {"X-Upload-Token": created["access_token"]}
        self.assertEqual((await self.client.put(created["put_url"].format(index=0), content=b"raw", headers=headers)).status_code, 200)
        completed = await self.client.post(base + "/complete", headers=headers)
        self.assertEqual(completed.status_code, 202)
        self.assertIn(completed.json()["status"], {"probing", "transcribing", "ready"})
        await self.store._jobs[created["upload_id"]]
        self.assertEqual((await self.client.put(base + "/chunks/0", content=b"raw", headers=headers)).status_code, 200)
        self.assertEqual((await self.client.put(base + "/chunks/0", content=b"bad", headers=headers)).status_code, 409)
        self.assertEqual((await self.client.get(base, headers=headers)).json()["status"], "ready")
        await self.client.post(base + "/complete", headers=headers)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((await self.client.delete(base, headers=headers)).status_code, 204)
        self.assertEqual((await self.client.get(base, headers=headers)).status_code, 404)

    async def test_owner_requires_verified_session_or_exact_nonforwarded_loopback(self) -> None:
        payload = {"name": "a.mp4", "bytes": 3, "sha256": _sha(b"raw")}
        for host, base in (("198.51.100.3", "http://127.0.0.1"), ("127.1.2.3", "http://127.0.0.1"),
                           ("127.0.0.1", "http://attacker.test"), ("127.0.0.1", "http://localhost.")):
            async with self._client(self.store, host=host, base=base) as client:
                response = await client.post("/api/uploads", json=payload, headers={"X-Owner": "owner-a", "X-Anonymous-Session-Id": "a" * 32})
                self.assertEqual(response.status_code, 403)
        for header in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Proto", "X-Real-IP", "Via"):
            response = await self.client.post("/api/uploads", json=payload, headers={header: "127.0.0.1"})
            self.assertEqual(response.status_code, 403)
        async with self._client(self.store, host="198.51.100.4", base="https://app.invalid", session="a" * 32) as client:
            self.assertEqual((await client.post("/api/uploads", json=payload)).status_code, 201)
        async with self._client(self.store, host="::1", base="http://[::1]") as client:
            self.assertEqual((await client.post("/api/uploads", json=payload)).status_code, 201)

    async def test_auth_rejects_before_consuming_chunk_body(self) -> None:
        created = await self._new()
        consumed = False

        async def body():
            nonlocal consumed
            consumed = True
            raise AssertionError("Unauthorized body was consumed")
            yield b""

        response = await self.client.put(f'/api/uploads/{created["upload_id"]}/chunks/0', content=body(), headers={"Content-Length": "3"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(consumed)

    async def test_exact_8mib_out_of_order_resume_and_idempotent_hash_retry(self) -> None:
        content = b"A" * CHUNK_SIZE + b"tail-13-bytes"
        created = await self._new(content)
        key, token = created["upload_id"], created["access_token"]
        tail = content[CHUNK_SIZE:]
        await self.store.put_chunk(key, token, 1, _stream(tail, 1), len(tail))
        self.assertEqual(self.store.read(key, token)["chunks"], [1])
        await self._reopen()
        self.assertEqual(self.store.read(key, token)["chunks"], [1])
        await self.store.put_chunk(key, token, 0, _stream(content[:CHUNK_SIZE]), CHUNK_SIZE)
        await self.store.put_chunk(key, token, 0, _stream(content[:CHUNK_SIZE], 77777), CHUNK_SIZE)
        with self.assertRaises(HTTPException) as conflict:
            await self.store.put_chunk(key, token, 0, _stream(b"B" * CHUNK_SIZE), CHUNK_SIZE)
        self.assertEqual(conflict.exception.status_code, 409)
        await self.store.complete(key, token)
        await self.store._jobs[key]
        self.assertEqual(_sha(self.store._source(self.store.authorize(key, token)).read_bytes()), _sha(content))
        self.assertEqual(self.store.read(key, token)["chunks"], [0, 1])
        self.assertEqual(len(self.calls), 1)
        await self._reopen()
        self.assertEqual(self.store.read(key, token)["status"], "ready")
        await self.store.complete(key, token)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.store._jobs, {})

    async def test_indices_lengths_stream_overrun_and_disconnect_leave_no_partial_chunk(self) -> None:
        created = await self._new(b"12345")
        key, token = created["upload_id"], created["access_token"]
        for index, length, body, status in ((-1, 5, b"12345", 400), (1, 5, b"12345", 400),
                                           (False, 5, b"12345", 400), (0, None, b"12345", 411),
                                           (0, 4, b"12345", 413), (0, CHUNK_SIZE + 1, b"", 413),
                                           (0, 5, b"123456", 413), (0, 5, b"1234", 400)):
            with self.assertRaises(HTTPException) as error:
                await self.store.put_chunk(key, token, index, _stream(body, 2), length)
            self.assertEqual(error.exception.status_code, status)
        self.assertEqual(self.store.read(key, token)["chunks"], [])
        self.assertEqual([p.name for p in (self.store.root / key).iterdir()], ["manifest.json"])

    async def test_duplicate_length_or_transfer_encoding_and_bad_index_are_rejected(self) -> None:
        created = await self._new()
        base = f'/api/uploads/{created["upload_id"]}/chunks/'
        token = ("X-Upload-Token", created["access_token"])
        for headers in ([token, ("Content-Length", "3"), ("Content-Length", "3")],
                        [token, ("Content-Length", "3"), ("Transfer-Encoding", "chunked")],
                        [token, ("Content-Length", "+3")]):
            response = await self.client.put(base + "0", headers=headers, content=b"raw")
            self.assertEqual(response.status_code, 400)
        for index in ("-1", "00", "1.0", "999999999999999999999999999"):
            response = await self.client.put(base + index, headers=[token], content=b"raw")
            self.assertEqual(response.status_code, 400)

    async def test_cancelled_stream_and_stream_timeout_are_retryable(self) -> None:
        created = await self._new()
        key, token = created["upload_id"], created["access_token"]
        entered = asyncio.Event()

        async def blocked():
            yield b"r"
            entered.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(self.store.put_chunk(key, token, 0, blocked(), 3))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        with patch.object(module, "CHUNK_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(HTTPException) as expired:
                await self.store.put_chunk(key, token, 0, blocked(), 3)
        self.assertEqual(expired.exception.status_code, 408)
        self.assertEqual(self.store.read(key, token)["chunks"], [])
        self.assertFalse(list((self.store.root / key).glob("write-*.tmp")))
        await self.store.put_chunk(key, token, 0, _stream(b"raw"), 3)
        self.assertEqual(self.store.read(key, token)["chunks"], [0])

    async def test_complete_checks_missing_chunks_declared_digest_and_tampered_parts(self) -> None:
        missing = await self._new()
        with self.assertRaises(HTTPException) as error:
            await self.store.complete(missing["upload_id"], missing["access_token"])
        self.assertEqual(error.exception.status_code, 409)
        wrong = await self.store.create(name="x.mp4", size=3, sha256=_sha(b"bad"), owner="owner-a")
        await self.store.put_chunk(wrong["upload_id"], wrong["access_token"], 0, _stream(b"raw"), 3)
        with self.assertRaises(HTTPException) as error:
            await self.store.complete(wrong["upload_id"], wrong["access_token"])
        self.assertEqual(error.exception.status_code, 409)
        changed = await self._upload()
        (self.store.root / changed["upload_id"] / "chunk-00000.part").write_bytes(b"bad")
        with self.assertRaises(HTTPException):
            await self.store.complete(changed["upload_id"], changed["access_token"])
        self.assertEqual(self.calls, [])
        self.assertFalse(list(self.store.root.glob("*/source.mp4")))

    async def test_chunk_manifest_failure_does_not_falsely_acknowledge_received_index(self) -> None:
        created = await self._new()
        key, token = created["upload_id"], created["access_token"]
        with patch.object(self.store, "_save", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(HTTPException) as error:
                await self.store.put_chunk(key, token, 0, _stream(b"raw"), 3)
        self.assertEqual(error.exception.status_code, 507)
        self.assertEqual(self.store.read(key, token)["chunks"], [])
        await self.store.put_chunk(key, token, 0, _stream(b"raw"), 3)
        self.assertEqual(self.store.read(key, token)["chunks"], [0])

    async def test_owner_count_byte_global_and_disk_reservation_limits(self) -> None:
        self.store.owner_max_files = 20  # the mechanism, not the shipped size of the visitor quota
        created = [await self._new() for _ in range(20)]
        with self.assertRaises(HTTPException) as error:
            await self._new()
        self.assertEqual(error.exception.status_code, 429)
        await self.store.delete(created[0]["upload_id"], created[0]["access_token"])
        await self._new()
        with patch.object(module, "MAX_SESSIONS", 20):
            with self.assertRaises(HTTPException):
                await self._new(owner="different-owner")
        with patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=self.store.reserved_bytes + 1)):
            with self.assertRaises(HTTPException) as error:
                await self._new(owner="new-owner")
        self.assertEqual(error.exception.status_code, 507)
        self.assertNotIn(str(self.root), str(error.exception.detail))

    async def test_complete_manifest_failure_restores_retryable_upload_state(self) -> None:
        created = await self._upload()
        key, token = created["upload_id"], created["access_token"]
        with patch.object(self.store, "_save", side_effect=OSError("synthetic manifest unavailable")):
            with self.assertRaises(HTTPException) as error:
                await self.store.complete(key, token)
        self.assertEqual(error.exception.status_code, 507)
        self.assertEqual(self.store.read(key, token)["status"], "uploading")
        self.assertEqual(self.store._jobs, {})
        await self.store.complete(key, token)
        await self.store._jobs[key]
        self.assertEqual(len(self.calls), 1)

    async def test_owner_declared_bytes_not_just_received_bytes_are_reserved(self) -> None:
        self.store.owner_max_bytes = 5 * 1024**3
        with patch.object(module.shutil, "disk_usage", return_value=SimpleNamespace(free=1 << 50)):
            for _ in range(10):
                await self.store.create(name="large.mp4", size=module.MAX_FILE_BYTES, sha256="0" * 64, owner="large")
            with self.assertRaises(HTTPException) as error:
                await self.store.create(name="large.mp4", size=module.MAX_FILE_BYTES, sha256="0" * 64, owner="large")
        self.assertEqual(error.exception.status_code, 429)
        self.assertGreater(self.store.reserved_bytes, 10 * module.MAX_FILE_BYTES)

    async def test_hourly_budget_survives_delete_and_restart(self) -> None:
        with patch.object(module, "OWNER_CREATIONS_PER_HOUR", 2), patch.object(module, "GLOBAL_CREATIONS_PER_HOUR", 3):
            for _ in range(2):
                created = await self._new()
                await self.store.delete(created["upload_id"], created["access_token"])
            await self._reopen()
            with self.assertRaises(HTTPException) as error:
                await self._new()
            self.assertEqual(error.exception.status_code, 429)
            other = await self._new(owner="b")
            await self.store.delete(other["upload_id"], other["access_token"])
            with self.assertRaises(HTTPException):
                await self._new(owner="c")
            now = module.time.time()
            with patch.object(module.time, "time", return_value=now + 3601):
                await self._new()

    async def test_bad_names_formats_empty_oversize_and_unbounded_json_are_rejected(self) -> None:
        for name in ("bad.wmv", "bad.webm", "bad.m4v", "bad.WEBM", "bad.M4V", "hidden.m3u8", "../a.mp4", "C:\\a.mp4", "a\x00.mp4", "https://x/a.mp4", " "):
            with self.assertRaises(HTTPException):
                await self._new(name=name)
        for size in (0, -1, True, module.MAX_FILE_BYTES + 1):
            with self.assertRaises(HTTPException):
                await self.store.create(name="a.mp4", size=size, sha256="0" * 64, owner="a")
        response = await self.client.post("/api/uploads", content=b" " * 4097)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.store._records, {})

    async def test_published_formats_match_admission_and_mime_checks(self) -> None:
        from backend.storage import ALLOWED_EXTENSIONS
        expected = {".mp4", ".mov", ".avi", ".mkv", ".jpg", ".jpeg", ".png", ".gif"}
        self.assertEqual(set(module._FORMATS), expected)
        self.assertEqual(expected, ALLOWED_EXTENSIONS)
        for suffix, mime in ((".mp4", "video/mp4"), (".mov", "video/quicktime"),
                             (".avi", "video/x-msvideo"), (".mkv", "video/x-matroska"),
                             (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                             (".png", "image/png"), (".gif", "image/gif")):
            with self.subTest(suffix=suffix):
                created = await self.store.create(name="fixture" + suffix.upper(), size=3, sha256=_sha(b"raw"), owner="owner-a", content_type=mime)
                self.assertEqual(self.store.read(created["upload_id"], created["access_token"])["status"], "uploading")
                with self.assertRaises(HTTPException) as error:
                    await self.store.create(name="fixture" + suffix, size=3, sha256=_sha(b"raw"), owner="owner-a", content_type="text/plain")
                self.assertEqual(error.exception.status_code, 415)
        for name, mime in (("bad.png", "image/jpeg"), ("bad.jpg", "video/mp4"),
                           ("bad.webm", "video/webm"), ("bad.m4v", "video/mp4")):
            response = await self.client.post("/api/uploads", json={"name": name, "bytes": 3, "sha256": _sha(b"raw"), "content_type": mime})
            self.assertEqual(response.status_code, 415)
        self.assertEqual(self.calls, [])

    async def test_photo_admission_discloses_production_50mib_not_general_500mib_limit(self) -> None:
        from backend import media_input
        self.assertEqual(module.MAX_PHOTO_BYTES, media_input.MAX_PHOTO_BYTES)
        self.assertEqual(module.MAX_PHOTO_BYTES, 50 * 1024**2)
        self.assertEqual(module.MAX_PHOTO_PIXELS, media_input.MAX_PHOTO_PIXELS)
        self.assertEqual(module.MAX_PHOTO_PIXELS, 20_000_000)
        self.assertEqual(module.PHOTO_DURATION, media_input.PHOTO_DURATION)
        for suffix in (".jpg", ".jpeg", ".png", ".gif"):
            with self.subTest(suffix=suffix):
                created = await self.store.create(name="photo" + suffix, size=module.MAX_PHOTO_BYTES, sha256="0" * 64, owner="owner-a")
                state = self.store.read(created["upload_id"], created["access_token"])
                self.assertEqual(state["max_bytes"], 50 * 1024**2)
                self.assertEqual(state["max_photo_bytes"], 50 * 1024**2)
                self.assertEqual(state["max_photo_pixels"], 20_000_000)
                before = set(self.store._records)
                with self.assertRaises(HTTPException) as error:
                    await self.store.create(name="photo" + suffix, size=module.MAX_PHOTO_BYTES + 1, sha256="0" * 64, owner="owner-a")
                self.assertEqual(error.exception.status_code, 413)
                self.assertIn("50 MiB", str(error.exception.detail))
                self.assertIn("500 MiB", str(error.exception.detail))
                self.assertEqual(set(self.store._records), before)
        video = await self._new()
        self.assertEqual(self.store.read(video["upload_id"], video["access_token"])["max_bytes"], module.MAX_FILE_BYTES)
        with patch.object(self.store, "max_bytes", 1024**2):
            with self.assertRaises(HTTPException) as error:
                await self.store.create(name="small.png", size=1024**2 + 1, sha256="0" * 64, owner="owner-a")
            self.assertIn("1 MiB", str(error.exception.detail))
        self.assertEqual(self.calls, [])

    async def test_public_statuses_are_normalized_without_changing_internal_recovery(self) -> None:
        created = await self._new()
        key, token = created["upload_id"], created["access_token"]
        record = self.store._records[key]
        for internal, phase, public in (("uploading", "upload", "uploading"),
                                         ("processing", "queued", "probing"), ("processing", "probe", "probing"),
                                         ("processing", "waveform", "probing"), ("processing", "asr", "transcribing"),
                                         ("ready", "done", "ready"), ("failed", "error", "failed"),
                                         ("asr_failed", "error", "failed"), ("interrupted", "error", "failed")):
            with self.subTest(internal=internal, phase=phase):
                record.status, record.phase = internal, phase
                record.probe = await self.store._probe(record) if internal in {"ready", "asr_failed"} else None
                response = await self.client.get(f"/api/uploads/{key}", headers={"X-Upload-Token": token})
                self.assertEqual(response.status_code, 200)
                state = response.json()
                self.assertEqual(state["status"], public)
                self.assertEqual(state["phase"], phase)
                self.assertEqual(state["can_materialize"], internal in {"ready", "asr_failed"})
                self.assertEqual(state["asr_retry_in_task"], internal == "asr_failed")
                self.assertEqual(record.status, internal)
        self.assertEqual(self.calls, [])

    async def test_new_upload_ttl_is_72_hours_and_does_not_slide_on_read_or_restart(self) -> None:
        created = await self._new()
        key, token = created["upload_id"], created["access_token"]
        record = self.store._records[key]
        self.assertEqual(module.UPLOAD_TTL_SECONDS, 72 * 3600)
        self.assertEqual(record.expires_at - record.created_at, 72 * 3600)
        expires = record.expires_at
        manifest = self.store.root / key / "manifest.json"
        before = manifest.read_bytes()
        with patch.object(module.time, "time", return_value=record.created_at + 48 * 3600):
            await self._reopen()
            self.assertEqual(self.store.read(key, token)["expires_at"], expires)
            self.assertEqual(await self.store.cleanup_owned(), 0)
            self.assertEqual(manifest.read_bytes(), before)
        with patch.object(module.time, "time", return_value=expires - 1):
            self.assertEqual(self.store.read(key, token)["status"], "uploading")
        with patch.object(module.time, "time", return_value=expires):
            with self.assertRaises(HTTPException) as error:
                self.store.read(key, token)
            self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(await self.store.cleanup_owned(), 1)
        self.assertFalse(manifest.exists())
        self.assertEqual(self.calls, [])

    async def test_materialize_copies_not_links_and_returns_pathless_snapshots(self) -> None:
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        task = self.settings.data_dir / "test-task"
        task.mkdir()
        assets, snapshots = await self.store.materialize([key], {key: token}, task)
        asset = assets[0]
        self.assertIsInstance(asset, UploadedAsset)
        self.assertEqual(asset.model_dump()["upload_id"], key)
        self.assertEqual(asset.source_duration_seconds, 1.0)
        self.assertEqual(asset.original_name, "source.mp4")
        self.assertEqual(asset.path.parent, task / "raw")
        self.assertEqual(asset.path.read_bytes(), b"raw")
        original = self.store._source(self.store.authorize(key, token))
        self.assertNotEqual(asset.path.stat().st_ino, original.stat().st_ino)
        asset.path.write_bytes(b"changed-task-copy")
        self.assertEqual(original.read_bytes(), b"raw")
        text = json.dumps(snapshots)
        for secret in (token, str(self.root), _sha(b"raw"), "token_hash"):
            self.assertNotIn(secret, text)
        self.assertIn("asr_result", snapshots[0])
        self.assertFalse((task / "pretranscripts.json").exists())
        snapshots[0]["transcript"]["segments"].clear()
        self.assertEqual(len(self.store.snapshots([key], {key: token})[0]["transcript"]["segments"]), 1)

    async def test_materialize_all_tokens_and_aggregate_duration_checked_before_copy(self) -> None:
        first, second = await self._ready(), await self._ready(b"other")
        ids = [first["upload_id"], second["upload_id"]]
        tokens = {first["upload_id"]: first["access_token"], second["upload_id"]: second["access_token"]}
        task = self.settings.data_dir / "target"
        task.mkdir()
        with self.assertRaises(HTTPException) as error:
            await self.store.materialize(ids, {ids[0]: tokens[ids[0]], ids[1]: "x" * 43}, task)
        self.assertEqual(error.exception.status_code, 404)
        self.assertFalse((task / "raw").exists())
        for key in ids:
            self.store._records[key].probe.sec = 1800.0
        third = await self._ready(b"third")
        ids.append(third["upload_id"])
        tokens[ids[-1]] = third["access_token"]
        with self.assertRaises(HTTPException) as error:
            await self.store.materialize(ids, tokens, task)
        self.assertEqual(error.exception.status_code, 413)
        self.assertFalse((task / "raw").exists())

    async def test_materialize_rehash_failure_rolls_back_only_its_own_copies(self) -> None:
        first, second = await self._ready(), await self._ready(b"two")
        ids, tokens = [first["upload_id"], second["upload_id"]], {first["upload_id"]: first["access_token"], second["upload_id"]: second["access_token"]}
        task = self.settings.data_dir / "target"
        (task / "raw").mkdir(parents=True)
        keep = task / "raw" / "existing.mp4"
        keep.write_bytes(b"existing")
        self.store._source(self.store._records[ids[1]]).write_bytes(b"BAD")
        with self.assertRaises(HTTPException) as error:
            await self.store.materialize(ids, tokens, task)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(list((task / "raw").iterdir()), [keep])
        self.assertEqual(keep.read_bytes(), b"existing")

    async def test_hardlink_and_reparse_point_paths_are_rejected(self) -> None:
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        source = self.store._source(self.store._records[key])
        link = self.root / "outside-hardlink.mp4"
        os.link(source, link)
        try:
            with self.assertRaises(HTTPException):
                self.store.snapshots([key], {key: token})
        finally:
            link.unlink()
        original_lstat = Path.lstat

        def reparse(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path == self.store.root / key:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original_lstat(path, *args, **kwargs)

        with patch.object(Path, "lstat", new=reparse):
            with self.assertRaises(HTTPException):
                self.store.read(key, token)
        self.assertEqual(source.read_bytes(), b"raw")

    async def test_materialize_rejects_unready_duplicates_and_unowned_target(self) -> None:
        unready = await self._new()
        task = self.settings.data_dir / "target"
        task.mkdir()
        with self.assertRaises(HTTPException):
            await self.store.materialize([unready["upload_id"]], {unready["upload_id"]: unready["access_token"]}, task)
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        for ids, target in (([key, key], task), ([key], self.root), ([key], self.store.root)):
            with self.assertRaises(HTTPException):
                await self.store.materialize(ids, {key: token}, target)

    async def test_asr_failure_is_sanitized_importable_and_complete_never_retries_it(self) -> None:
        self.provider_error = ASRProviderError("fake /private/path access_token=DO_NOT_EXPOSE")
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        state = self.store.read(key, token)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(self.store._records[key].status, "asr_failed")
        self.assertEqual(state["error_code"], "ASR_FAILED")
        self.assertTrue(state["probe_ok"])
        self.assertTrue(state["can_materialize"])
        self.assertTrue(state["asr_retry_in_task"])
        self.assertIsNone(state["asr_confidence"])
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(state))
        self.assertNotIn("/private/path", json.dumps(state))
        await self.store.complete(key, token)
        await self._reopen()
        await self.store.complete(key, token)
        self.assertEqual(self.store.read(key, token)["status"], "failed")
        self.assertEqual(len(self.calls), 1)
        task = self.settings.data_dir / "target"
        task.mkdir()
        assets, snapshots = await self.store.materialize([key], {key: token}, task)
        self.assertEqual(len(assets), 1)
        self.assertEqual(snapshots[0]["status"], "failed")
        self.assertEqual(snapshots[0]["error_code"], "ASR_FAILED")
        self.assertTrue(snapshots[0]["asr_retry_in_task"])
        self.assertTrue(snapshots[0]["can_materialize"])
        self.assertIsNone(snapshots[0]["asr_result"])

    async def test_segment_precision_remains_matchable_but_wordtrim_is_forbidden(self) -> None:
        self.provider_result = _result(words=False, speaker=None, confidence=None)
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        snapshot = self.store.snapshots([key], {key: token})[0]
        transcript = snapshot["transcript"]
        self.assertEqual(transcript["precision"], "segment")
        self.assertFalse(transcript["word_trim_allowed"])
        self.assertEqual(transcript["segments"][0]["words"], [])
        self.assertEqual(transcript["segments"][0]["speaker_id"], "")
        self.assertEqual(transcript["speakers"], [])
        self.assertIsNone(snapshot["asr_confidence"])
        matches = align_quotes([SentenceInput(idx=0, text="你好世界", kind="quote")], [snapshot])
        self.assertEqual(matches[0]["source"]["precision"], "segment")
        with self.assertRaises(ValueError):
            validate_quote_trim(QuoteTake.model_validate(matches[0]["source"]), 0.25, 0.55)

    async def test_word_precision_confidence_and_scoped_speaker_evidence(self) -> None:
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        snapshot = self.store.read(key, token)
        transcript = snapshot["transcript"]
        self.assertEqual(transcript["precision"], "word")
        self.assertTrue(transcript["word_trim_allowed"])
        self.assertEqual(transcript["segments"][0]["words"], [{"w": "你好", "s": 0.2, "e": 0.4}, {"w": "世界", "s": 0.4, "e": 0.6}])
        self.assertEqual(transcript["word_confidences"]["seg_0"], [0.91, None])
        self.assertEqual(transcript["speakers"][0]["id"], _speaker_id(key, "7"))
        self.assertEqual(transcript["speakers"][0]["name"], "")
        self.assertNotIn("confidence", transcript["speakers"][0])
        self.assertEqual(snapshot["asr_confidence"], 0.82)
        expected = 10 * math.log10((3277**2 - 33**2) / 33**2)
        self.assertAlmostEqual(transcript["segments"][0]["snr_db"], expected, places=6)
        self.assertTrue(transcript["snr_is_estimate"])

    async def test_new_speaker_ids_are_bounded_stable_upload_scoped_and_preserve_raw_ids(self) -> None:
        for source_id in ("up_x:1", "a" * 128):
            with self.subTest(source_id=source_id):
                self.provider_result = _result(speaker=source_id)
                for word in self.provider_result.utterances[0].words:
                    word.speaker_id = source_id
                self.credential = "synthetic-" + source_id
                first = await self._ready()
                second = await self._ready(b"other-container-same-pcm")
                expected = []
                for created in (first, second):
                    key, token = created["upload_id"], created["access_token"]
                    state = self.store.read(key, token)
                    speaker = state["speakers"][0]["id"]
                    expected.append(speaker)
                    self.assertEqual(speaker, _speaker_id(key, source_id))
                    self.assertRegex(speaker, r"^up_[a-f0-9]{32}_spk_[a-f0-9]{64}$")
                    self.assertLessEqual(len(speaker), 128)
                    self.assertEqual(state["transcript"]["segments"][0]["speaker_id"], speaker)
                    self.assertEqual(state["transcript"]["word_speaker_ids"]["seg_0"], [speaker, speaker])
                    self.assertEqual(state["transcript"]["observed_words"]["seg_0"][0]["speaker_id"], source_id)
                    self.assertEqual(self.store._records[key].asr_result.utterances[0].speaker_id, source_id)
                self.assertNotEqual(*expected)
                self.assertTrue(self.store.read(second["upload_id"], second["access_token"])["asr_cache_hit"])
                await self._reopen()
                self.assertEqual(self.store.read(first["upload_id"], first["access_token"])["speakers"][0]["id"], expected[0])
        self.assertEqual(len(self.calls), 2)

    async def test_legacy_probe_speaker_ids_and_expiry_restore_without_migration(self) -> None:
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        manifest = self.store.root / key / "manifest.json"
        payload = json.loads(manifest.read_bytes())
        payload["probe"].pop("is_image")
        payload["expires_at"] = payload["created_at"] + 24 * 3600
        legacy_id = key + ":7"
        payload["transcript"]["speakers"][0]["id"] = legacy_id
        payload["transcript"]["segments"][0]["speaker_id"] = legacy_id
        payload["transcript"]["word_speaker_ids"]["seg_0"] = [legacy_id, None]
        module._atomic_json(manifest, payload)
        before = manifest.read_bytes()
        await self._reopen()
        state = self.store.read(key, token)
        self.assertFalse(state["is_image"])
        self.assertEqual(state["speakers"][0]["id"], legacy_id)
        self.assertEqual(state["transcript"]["segments"][0]["speaker_id"], legacy_id)
        self.assertEqual(state["transcript"]["word_speaker_ids"]["seg_0"], [legacy_id, None])
        self.assertEqual(state["expires_at"], payload["expires_at"])
        await self.store.complete(key, token)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        with patch.object(module.time, "time", return_value=payload["expires_at"]):
            with self.assertRaises(HTTPException) as error:
                self.store.read(key, token)
            self.assertEqual(error.exception.status_code, 404)

    async def test_partial_invalid_or_out_of_bounds_words_never_become_synthesized_timing(self) -> None:
        self.provider_result = _result()
        utterance = self.provider_result.utterances[0]
        utterance.words[1].start_time_ms = utterance.words[1].end_time_ms
        created = await self._ready()
        transcript = self.store.read(created["upload_id"], created["access_token"])["transcript"]
        self.assertEqual(transcript["precision"], "segment")
        self.assertEqual(transcript["segments"][0]["words"], [])
        self.assertEqual(transcript["observed_words"]["seg_0"][0]["start_time_ms"], 200)
        self.assertEqual(transcript["observed_words"]["seg_0"][1]["start_time_ms"], 600)
        self.assertFalse(transcript["word_trim_allowed"])

    async def test_invalid_extra_word_cannot_promote_remaining_words_to_trim_precision(self) -> None:
        self.provider_result = _result()
        self.provider_result.utterances[0].words.append(ASRWord(text="坏", start_time_ms=600, end_time_ms=600))
        created = await self._ready()
        snapshot = self.store.snapshots([created["upload_id"]], {created["upload_id"]: created["access_token"]})[0]
        matches = align_quotes([SentenceInput(idx=0, text="你好世界", kind="quote")], [snapshot])
        self.assertEqual(snapshot["precision"], "segment")
        self.assertEqual(matches[0]["source"]["precision"], "segment")

    async def test_probe_whitelists_and_all_media_limits_are_checked_before_provider(self) -> None:
        created = await self._upload()
        record = self.store._records[created["upload_id"]]
        await module._blocking(self.store._assemble, record)
        normal = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1.0", "start_time": "0"},
                  "streams": [{"codec_type": "video", "width": 64, "height": 48, "avg_frame_rate": "25/1", "duration": "1"}]}
        with patch.object(module, "_run_media", new_callable=AsyncMock, return_value=json.dumps(normal).encode()) as command:
            probe = await UploadStore._probe(self.store, record)
        self.assertEqual(probe.sec, 1.0)
        args = command.await_args.args[0]
        self.assertEqual(args[args.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertEqual(args[args.index("-format_whitelist") + 1], "mov")
        self.assertEqual(args[args.index("-enable_drefs") + 1], "0")
        for field, value in (("duration", "1800.01"), ("duration", "NaN"), ("width", 100000),
                             ("height", 100000), ("avg_frame_rate", "121/1"), ("avg_frame_rate", "0/0"),
                             ("format_name", "hls"), ("codec_type", "audio")):
            bad = json.loads(json.dumps(normal))
            if field == "format_name":
                bad["format"][field] = value
            else:
                bad["streams"][0][field] = value
            with patch.object(module, "_run_media", new_callable=AsyncMock, return_value=json.dumps(bad).encode()):
                with self.assertRaises(HTTPException) as error:
                    await UploadStore._probe(self.store, record)
                self.assertEqual(error.exception.status_code, 415)
        self.assertEqual(self.calls, [])

    async def test_no_word_times_and_provider_words_only_paths_preserve_actual_evidence(self) -> None:
        self.provider_result = ASRTranscript(text="有文本但没有任何时间。")
        created = await self._ready()
        state = self.store.read(created["upload_id"], created["access_token"])
        self.assertEqual(state["transcript"]["text"], "有文本但没有任何时间。")
        self.assertEqual(state["precision"], "unavailable")
        self.assertEqual(state["transcript"]["segments"], [])
        self.credential = "different-schema-fixture"
        self.provider_result = _result()
        self.provider_result.words = self.provider_result.utterances[0].words
        self.provider_result.utterances[0].words = []
        created = await self._ready()
        self.assertEqual(self.store.read(created["upload_id"], created["access_token"])["precision"], "word")

    async def test_materialize_cancellation_waits_for_copy_and_deletes_only_partial_copy(self) -> None:
        created = await self._ready()
        key, token = created["upload_id"], created["access_token"]
        task = self.settings.data_dir / "cancelled-target"
        task.mkdir()
        entered, finished = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def copying(source: Path, destination: Path, size: int, digest: str, stop: threading.Event) -> None:
            with module._open_file(destination, exclusive=True) as output:
                output.write(b"r")
                loop.call_soon_threadsafe(entered.set)
                stop.wait()
            finished.set()
            raise InterruptedError("synthetic copy cancelled")

        with patch.object(module, "_copy_verified", side_effect=copying):
            pending = asyncio.create_task(self.store.materialize([key], {key: token}, task))
            await asyncio.wait_for(entered.wait(), 5)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertTrue(finished.is_set())
        self.assertEqual(list((task / "raw").iterdir()), [])
        self.assertEqual(self.store._source(self.store.authorize(key, token)).read_bytes(), b"raw")

    async def test_cache_scope_owner_provider_credentials_and_restart_are_explicit(self) -> None:
        first = await self._ready()
        second = await self._ready(b"different-video")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.store.read(second["upload_id"], second["access_token"])["asr_cache_hit"])
        other = await self._ready(owner="owner-b")
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.store.read(other["upload_id"], other["access_token"])["asr_cache_hit"])
        self.credential = "rotated-synthetic-key"
        await self._ready()
        self.assertEqual(len(self.calls), 3)
        self.credential = "synthetic-key"
        await self._reopen()
        restored = await self._ready()
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(self.store.read(restored["upload_id"], restored["access_token"])["asr_cache_hit"])
        self.assertEqual(self.store.read(first["upload_id"], first["access_token"])["status"], "ready")
        self.assertFalse(self.settings.asr_cache_dir.exists())

    async def test_raw_asr_cache_recomputes_snr_without_reusing_editorial_value(self) -> None:
        first = await self._ready()
        record = self.store.authorize(first["upload_id"], first["access_token"])
        assert record.transcript is not None
        # Simulate a persisted result from the old numerical recipe. The raw
        # ASR cache must still hit, but cannot copy this derived SNR to a new
        # upload. No invalidation that would trigger another provider request.
        record.transcript.segments[0].snr_db = -154.5973974851091
        self.store._save(record)
        await self._reopen()
        second = await self._ready(b"same-pcm-new-upload")
        state = self.store.read(second["upload_id"], second["access_token"])
        self.assertTrue(state["asr_cache_hit"])
        self.assertEqual(len(self.calls), 1)
        new_record = self.store.authorize(second["upload_id"], second["access_token"])
        assert new_record.transcript is not None
        value = new_record.transcript.segments[0].snr_db
        self.assertIsNotNone(value)
        self.assertAlmostEqual(value, 10 * math.log10((3277**2 - 33**2) / 33**2), places=9)
        old_record = self.store.authorize(first["upload_id"], first["access_token"])
        assert old_record.transcript is not None
        self.assertEqual(old_record.transcript.segments[0].snr_db, -154.5973974851091)

    async def test_maximum_four_preprocess_jobs_and_progress_reflects_real_phases(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        active = maximum = 0
        original = self.store._previews

        async def held(record: Any) -> Any:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                entered.set()
            try:
                await release.wait()
                return await original(record)
            finally:
                active -= 1

        with patch.object(self.store, "_previews", side_effect=held):
            created = [await self._upload(bytes([index])) for index in range(6)]
            for item in created:
                await self.store.complete(item["upload_id"], item["access_token"])
            await asyncio.wait_for(entered.wait(), 5)
            states = [self.store.read(item["upload_id"], item["access_token"])["phase"] for item in created]
            self.assertEqual(states.count("waveform"), 4)
            self.assertEqual(states.count("queued"), 2)
            self.assertTrue(all(self.store.read(item["upload_id"], item["access_token"])["status"] == "probing" for item in created))
            release.set()
            await asyncio.gather(*list(self.store._jobs.values()))
        self.assertEqual(maximum, 4)
        self.assertTrue(all(record.status == "ready" for record in self.store._records.values()))

    async def test_cache_respects_configured_ttl_after_restart(self) -> None:
        self.settings.asr_cache_ttl_hours = 1
        await self._ready()
        now = module.time.time()
        with patch.object(module.time, "time", return_value=now + 3601):
            await self._reopen()
            created = await self._ready(b"new-video-same-audio")
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.store.read(created["upload_id"], created["access_token"])["asr_cache_hit"])

    async def test_close_cancels_and_drains_owned_blocking_worker_before_unlock(self) -> None:
        entered, exited = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def blocked(*args: Any) -> Any:
            stop = args[-1]
            loop.call_soon_threadsafe(entered.set)
            stop.wait()
            exited.set()
            raise InterruptedError("synthetic cancelled decoder")

        with patch.object(module, "_analyze_pcm", side_effect=blocked):
            created = await self._upload()
            await self.store.complete(created["upload_id"], created["access_token"])
            await asyncio.wait_for(entered.wait(), 5)
            await self.store.close()
        self.assertTrue(exited.is_set())
        self.assertEqual(self.store._jobs, {})
        with self.assertRaises(HTTPException) as error:
            await self._new()
        self.assertEqual(error.exception.status_code, 503)
        await self._reopen()
        state = self.store.read(created["upload_id"], created["access_token"])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error_code"], "INTERRUPTED")
        self.assertFalse(state["asr_retry_in_task"])
        self.assertFalse(state["can_materialize"])
        self.assertEqual(self.store._records[created["upload_id"]].status, "interrupted")
        self.assertEqual(self.calls, [])

    async def test_interrupted_attempted_asr_is_not_auto_replayed_on_restart(self) -> None:
        created = await self._upload()
        record = self.store._records[created["upload_id"]]
        await module._blocking(self.store._assemble, record)
        record.status, record.phase, record.asr_attempted = "processing", "asr", True
        record.probe = await self.store._probe(record)
        self.store._save(record)
        await self._reopen()
        read = self.store.read(created["upload_id"], created["access_token"])
        self.assertEqual(read["status"], "failed")
        self.assertEqual(read["error_code"], "INTERRUPTED")
        self.assertTrue(read["asr_retry_in_task"])
        self.assertTrue(read["can_materialize"])
        self.assertEqual(self.store._records[created["upload_id"]].status, "asr_failed")
        await self.store.complete(created["upload_id"], created["access_token"])
        self.assertEqual(self.calls, [])

    async def test_cleanup_only_expired_owned_uploads_and_no_task_or_unknown_directory(self) -> None:
        created = await self._new()
        record = self.store._records[created["upload_id"]]
        record.expires_at = module.time.time() - 1
        self.store._save(record)
        task = self.settings.data_dir / "existing-task"
        task.mkdir()
        keep = task / "task_state.json"
        keep.write_bytes(b"existing-task-state")
        stranger = self.store.root / ("up_" + "f" * 32)
        stranger.mkdir()
        (stranger / "do-not-delete.txt").write_bytes(b"unknown")
        self.assertEqual(await self.store.cleanup_owned(), 1)
        self.assertFalse((self.store.root / created["upload_id"]).exists())
        self.assertEqual(keep.read_bytes(), b"existing-task-state")
        self.assertEqual((stranger / "do-not-delete.txt").read_bytes(), b"unknown")

    async def test_cleanup_rejects_unknown_files_and_corrupt_manifest_reserves_capacity(self) -> None:
        created = await self._new()
        record = self.store._records[created["upload_id"]]
        unknown = self.store.root / record.id / "not-owned.txt"
        unknown.write_text("retain", encoding="utf-8")
        record.expires_at = module.time.time() - 1
        self.store._save(record)
        self.assertEqual(await self.store.cleanup_owned(), 0)
        self.assertTrue(unknown.exists())
        record.expires_at = module.time.time() + 3600
        (self.store.root / record.id / "manifest.json").write_bytes(b"invalid")
        await self._reopen()
        self.assertEqual(self.store._orphans, 1)
        self.assertGreaterEqual(self.store.reserved_bytes, module.MAX_FILE_BYTES)
        with self.assertRaises(HTTPException):
            self.store.read(created["upload_id"], created["access_token"])
        self.assertTrue(unknown.exists())

    async def test_second_store_cannot_bypass_budgets_with_same_root(self) -> None:
        with self.assertRaises(HTTPException) as error:
            StubStore(self.settings)
        self.assertEqual(error.exception.status_code, 503)
        created = await self._new()
        self.assertEqual(self.store.read(created["upload_id"], created["access_token"])["status"], "uploading")


class ASRWordSupportTests(unittest.IsolatedAsyncioTestCase):
    def test_real_adapter_fields_and_missing_confidence_are_not_fabricated(self) -> None:
        payload = {"result": [{"text": "你好", "confidence": 0.83, "utterances": [{
            "text": "你好", "start_time": 20, "end_time": 320, "extra": {"speaker_id": 0},
            "words": [{"text": "你", "start_time": 20, "end_time": 180, "confidence": 0.9, "extra": {"speaker_id": "0"}},
                      {"word": "好", "start_time_ms": 180, "end_time_ms": 320}],
        }]}], "audio_info": {"duration": 400}}
        result = _transcript_from_payload(payload)
        utterance = result.utterances[0]
        self.assertEqual(utterance.speaker_id, "0")
        self.assertIsNone(utterance.confidence)
        self.assertEqual(result.confidence, 0.83)
        self.assertEqual(utterance.words[0].speaker_id, "0")
        self.assertEqual(utterance.words[1].text, "好")
        self.assertIsNone(utterance.words[1].confidence)
        self.assertEqual(utterance.words[1].start_time_ms, 180)

    def test_invalid_missing_boolean_negative_timestamps_and_confidences_are_not_coerced(self) -> None:
        value = {"result": {"text": "test", "confidence": float("nan"), "utterances": [
            {"text": "missing"}, {"text": "negative", "start_time": -1, "end_time": 9},
            {"text": "bool", "start_time": False, "end_time": 9},
            {"text": "valid", "start_time": 0, "end_time": 100, "confidence": 99, "words": [
                {"text": "absent"}, {"text": "reverse", "start_time": 8, "end_time": 2},
                {"text": "zero", "start_time": 8, "end_time": 8, "confidence": True},
            ]},
        ]}, "audio_info": {"duration": True}}
        result = _transcript_from_payload(value)
        self.assertEqual(len(result.utterances), 1)
        self.assertIsNone(result.confidence)
        self.assertIsNone(result.utterances[0].confidence)
        self.assertEqual(result.duration_ms, 0)
        self.assertEqual(len(result.utterances[0].words), 1)
        self.assertEqual(result.utterances[0].words[0].start_time_ms, 8)
        self.assertEqual(result.utterances[0].words[0].end_time_ms, 8)
        self.assertIsNone(result.utterances[0].words[0].confidence)

    def test_malformed_provider_text_and_nonboolean_definite_are_not_invented(self) -> None:
        result = _transcript_from_payload({"result": {"text": {"not": "transcript"}, "utterances": [
            {"text": "interim", "start_time": 0, "end_time": 100, "definite": "false"},
        ]}})
        self.assertEqual(result.text, "")
        self.assertFalse(result.utterances[0].definite)

    async def test_nonstream_sends_without_realtime_delay_but_streaming_keeps_pacing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gm-asr-pace-") as directory:
            path = Path(directory) / "audio.pcm"
            path.write_bytes(b"x" * 10)
            for nonstream, expected in ((True, [0.0, 0.0]), (False, [4 / 32000, 4 / 32000])):
                provider = VolcengineASRProvider(base_url="wss://invalid.test", endpoint_path="asr", app_id="a", access_token="b", resource_id="r", audio_chunk_bytes=4, enable_nonstream=nonstream)
                websocket = SimpleNamespace(send=AsyncMock())
                with patch("backend.providers.asr.asyncio.sleep", new_callable=AsyncMock) as sleep:
                    await provider._send_audio(websocket, path, "pcm")
                self.assertEqual([call.args[0] for call in sleep.await_args_list], expected)
                self.assertEqual(websocket.send.await_count, 3)
                frames = [call.args[0] for call in websocket.send.await_args_list]
                self.assertEqual(b"".join(gzip.decompress(frame[8:]) for frame in frames), b"x" * 10)
                self.assertEqual(frames[-1][1] & 0xF, 2)

    async def test_official_request_switches_and_word_response_via_fake_websocket(self) -> None:
        def response(payload: Any, final: bool) -> bytes:
            data = gzip.compress(json.dumps(payload).encode())
            return bytes([0x11, 0x92 if final else 0x90, 0x11, 0]) + struct.pack(">I", len(data)) + data

        initial = response({"result": {"text": ""}}, False)
        final = response({"result": {"text": "test", "utterances": [{"text": "test", "start_time": 1, "end_time": 50,
                         "extra": {"speaker_id": "s"}, "words": [{"text": "test", "start_time": 1, "end_time": 50}]}]}}, True)
        socket = SimpleNamespace(send=AsyncMock(), recv=AsyncMock(side_effect=[initial, final]))

        class Connection:
            async def __aenter__(self) -> Any:
                return socket

            async def __aexit__(self, *args: Any) -> None:
                return None

        with tempfile.TemporaryDirectory(prefix="gm-asr-protocol-") as directory:
            path = Path(directory) / "a.pcm"
            path.write_bytes(b"a" * 10)
            provider = VolcengineASRProvider(base_url="wss://invalid.test", endpoint_path="bigmodel_async", app_id="a", access_token="b", resource_id="r", max_retries=0, connect_factory=lambda *args, **kwargs: Connection())
            result = await provider.transcribe(path)
        payload = json.loads(gzip.decompress(socket.send.await_args_list[0].args[0][8:]))
        self.assertTrue(payload["request"]["show_utterances"])
        self.assertTrue(payload["request"]["enable_nonstream"])
        self.assertTrue(payload["request"]["enable_speaker_info"])
        self.assertEqual(payload["request"]["ssd_version"], "200")
        self.assertNotIn("enable_words", payload["request"])
        self.assertEqual(result.utterances[0].words[0].start_time_ms, 1)
        self.assertEqual(result.utterances[0].speaker_id, "s")


class WaveEvidenceTests(unittest.TestCase):
    def test_pcm_reads_are_streamed_20ms_and_keep_nonzero_partial_last_window(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gm-wave-") as directory:
            path = Path(directory) / "pcm.wav"
            _pcm(path, [0] * 320 + [3200] * 7)
            original = module._open_file
            reads: list[int] = []

            class BoundedReader:
                def __init__(self, stream: Any) -> None:
                    self.stream = stream

                def __getattr__(self, key: str) -> Any:
                    return getattr(self.stream, key)

                def read(self, size: int = -1) -> bytes:
                    if not 0 <= size <= 640:
                        raise AssertionError("Unbounded PCM read")
                    reads.append(size)
                    return self.stream.read(size)

                def __enter__(self) -> Any:
                    return self

                def __exit__(self, *args: Any) -> None:
                    self.stream.close()

            with patch.object(module, "_open_file", side_effect=lambda file: BoundedReader(original(file))):
                result = module._analyze_pcm(path, 0.0, 1800.0, False, 0.2, 32, threading.Event())
        self.assertEqual(result.samples, 327)
        self.assertEqual(result.counts, [320, 7])
        self.assertEqual(result.rms[0], 0.0)
        self.assertAlmostEqual(result.rms[1], 3200 / 32768)
        self.assertEqual(result.silences, [(0.0, 0.02)])
        self.assertIsNone(result.has_speech)
        self.assertTrue(reads)

    def test_exact_silence_does_not_need_vad_or_provider(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gm-wave-zero-") as directory:
            path = Path(directory) / "zero.wav"
            _pcm(path, [0] * 1600)
            result = module._analyze_pcm(path, 0.25, 1800.0, False, 0.2, 32, threading.Event())
        self.assertFalse(result.has_speech)
        self.assertEqual(result.evidence, "digital_silence")
        self.assertEqual(result.silences, [(0.25, 0.35)])

    def test_silero_model_is_reused_and_short_or_failed_analysis_fails_open(self) -> None:
        import silero_vad_lite
        with tempfile.TemporaryDirectory(prefix="gm-upload-vad-") as directory:
            path = Path(directory) / "quiet.wav"
            _pcm(path, [40] * 1600)
            process = unittest.mock.Mock(return_value=0.0)
            with patch.object(silero_vad_lite, "SileroVAD", return_value=SimpleNamespace(process=process)) as constructor:
                result = module._analyze_pcm(path, 0.0, 1800.0, True, 0.2, 32, threading.Event())
            constructor.assert_called_once_with(16000)
            self.assertEqual(process.call_count, 3)  # One detector reused across full 512-sample frames.
            self.assertFalse(result.has_speech)
            self.assertEqual(result.evidence, "silero_vad_v2_15percent_or_5seconds")
            self.assertEqual(result.speech_intervals, ((0.096, 0.1),))  # Unknown tail is not silence.
            with patch.object(silero_vad_lite, "SileroVAD", side_effect=RuntimeError("synthetic VAD failure")):
                result = module._analyze_pcm(path, 0.0, 1800.0, True, 0.2, 32, threading.Event())
            self.assertIsNone(result.has_speech)
            self.assertEqual(result.evidence, "unknown")
            _pcm(path, [40] * 16)
            with patch.object(silero_vad_lite, "SileroVAD", return_value=SimpleNamespace(process=lambda frame: 0.0)):
                result = module._analyze_pcm(path, 0.0, 1800.0, True, 0.2, 32, threading.Event())
            self.assertIsNone(result.has_speech)
            self.assertEqual(result.evidence, "unknown")

    def test_pcm_vad_or_thresholds_preserve_subthreshold_intervals(self) -> None:
        # Synthetic probabilities, real PCM framing; no local model is loaded.
        # Exactly 15% succeeds below 5s; just over 5s succeeds below 15%.
        for frames, speech_frames, expected in ((200, 30, True), (200, 29, False),
                                                (1250, 157, True), (1250, 156, False)):
            with self.subTest(frames=frames, speech_frames=speech_frames):
                probabilities = iter([0.9] * speech_frames + [0.0] * (frames - speech_frames))
                with tempfile.TemporaryDirectory(prefix="gm-vad-policy-") as directory:
                    path = Path(directory) / "pcm.wav"
                    _pcm(path, [40, -40] * (frames * 256))
                    with patch("silero_vad_lite.SileroVAD", return_value=SimpleNamespace(process=lambda frame: next(probabilities))):
                        result = module._analyze_pcm(path, 0.25, 1800.0, True, 0.2, 32, threading.Event())
                self.assertIs(result.has_speech, expected)
                self.assertEqual(result.evidence, "silero_vad_v2_15percent_or_5seconds")
                self.assertEqual(result.samples, frames * 512)
                self.assertEqual(result.speech_intervals, ((0.25, 0.25 + speech_frames * 0.032),))

    def test_pcm_vad_processing_failure_and_invalid_probabilities_fail_open(self) -> None:
        for invalid in (RuntimeError("synthetic processing failure"), float("nan"), float("inf"), -0.1, 1.1):
            with self.subTest(invalid_type=type(invalid).__name__):
                process = unittest.mock.Mock(side_effect=[0.9, invalid])
                with tempfile.TemporaryDirectory(prefix="gm-vad-fail-open-") as directory:
                    path = Path(directory) / "pcm.wav"
                    _pcm(path, [40, -40] * 800)
                    with patch("silero_vad_lite.SileroVAD", return_value=SimpleNamespace(process=process)):
                        result = module._analyze_pcm(path, 0.0, 1800.0, True, 0.2, 32, threading.Event())
                self.assertEqual(process.call_count, 2)
                self.assertIsNone(result.has_speech)
                self.assertEqual(result.evidence, "unknown")
                self.assertEqual(result.speech_intervals, ())

    def test_snr_is_unknown_without_measurable_noise_floor(self) -> None:
        probe = module._Probe(sec=1.0, width=64, height=48, fps=25.0, has_audio=True, format_name="mov")
        record = module._Manifest(id="up_" + "a" * 32, name="a.mp4", size=1, sha256="0" * 64,
                                  token_hash="1" * 64, owner_hash="2" * 64, content_type="video/mp4",
                                  created_at=0, expires_at=86400, probe=probe)
        for rms in ([0.0] * 10 + [0.1] * 20 + [0.0] * 20, [0.1] * 50):
            waveform = module._Wave(rms, [320] * 50, 16000, [], None, "unknown")
            converted = module._editorial_transcript(record, _result(), waveform, threading.Event())
            self.assertIsNone(converted.segments[0].snr_db)

    def test_equal_power_real_pcm_cannot_forge_measurable_snr(self) -> None:
        record = module._Manifest(id="up_" + "a" * 32, name="a.mp4", size=1, sha256="0" * 64,
                                  token_hash="1" * 64, owner_hash="2" * 64, content_type="video/mp4",
                                  created_at=0, expires_at=86400,
                                  probe=module._Probe(sec=1.0, width=64, height=48, fps=25.0, has_audio=True, format_name="mov"))
        # Identical zero-mean int16 frames inside/outside the ASR interval: no
        # excess signal power. Exercise sqrt + prefix subtraction, not hand-set SNR.
        frame = [48, -48] * 159 + [49, -49]
        with tempfile.TemporaryDirectory(prefix="gm-snr-equal-pcm-") as directory:
            path = Path(directory) / "pcm.wav"
            _pcm(path, frame * 50)
            waveform = module._analyze_pcm(path, 0.0, 1800.0, False, 0.2, 32, threading.Event())
        self.assertEqual(waveform.counts, [320] * 50)
        self.assertEqual(len(set(waveform.rms)), 1)
        self.assertEqual(sum(frame), 0)
        converted = module._editorial_transcript(record, _result(), waveform, threading.Event())
        self.assertIsNone(converted.segments[0].snr_db)

    def test_real_pcm_adjacent_noise_measures_both_sides_of_18db_without_global_floor(self) -> None:
        record = module._Manifest(id="up_" + "a" * 32, name="a.mp4", size=1, sha256="0" * 64,
                                  token_hash="1" * 64, owner_hash="2" * 64, content_type="video/mp4",
                                  created_at=0, expires_at=86400,
                                  probe=module._Probe(sec=3.0, width=64, height=48, fps=25.0, has_audio=True, format_name="mov"))
        transcript = ASRTranscript(text="synthetic", duration_ms=3000, utterances=[
            ASRUtterance(text="synthetic", start_time_ms=1000, end_time_ms=2000),
        ])
        for signal, above_threshold in ((3200, True), (2400, False)):
            with self.subTest(signal=signal):
                # Remote near-silence must not replace the actual 500ms adjacent
                # non-speech floor. Both adjacent sides have RMS 320/32768.
                values = ([1, -1] * 4000 + [320, -320] * 4000
                          + [signal, -signal] * 8000 + [320, -320] * 4000 + [1, -1] * 4000)
                with tempfile.TemporaryDirectory(prefix="gm-snr-adjacent-pcm-") as directory:
                    path = Path(directory) / "pcm.wav"
                    _pcm(path, values)
                    waveform = module._analyze_pcm(path, 0.0, 1800.0, False, 0.2, 32, threading.Event())
                converted = module._editorial_transcript(record, transcript, waveform, threading.Event())
                snr = converted.segments[0].snr_db
                self.assertIsNotNone(snr)
                assert snr is not None
                self.assertAlmostEqual(snr, 10 * math.log10((signal * signal - 320 * 320) / (320 * 320)), places=9)
                self.assertEqual(snr >= 18.0, above_threshold)
                self.assertEqual(converted.snr_method, "estimated_pcm_adjacent_nonspeech_500ms_v2")

    def test_real_pcm_slight_positive_excess_is_negative_db_not_unknown(self) -> None:
        from backend.speech_analysis import adjacent_snr
        # A single changed +/- pair per 320-sample frame: nonzero, zero-mean,
        # and only ~0.026% extra power. Keep the odd, real final PCM frame.
        floor = [48, -48] * 159 + [49, -49]
        signal = [48, -48] * 158 + [49, -49] * 2
        values = floor * 10 + signal * 20 + floor * 20 + [48, -48]
        with tempfile.TemporaryDirectory(prefix="gm-snr-small-pcm-") as directory:
            path = Path(directory) / "pcm.wav"
            _pcm(path, values)
            waveform = module._analyze_pcm(path, .25, 1800., False, .2, 32, threading.Event())
        self.assertEqual(waveform.counts, [320] * 50 + [2])
        self.assertEqual(waveform.samples, len(values))
        self.assertTrue(all(level > 0 for level in waveform.rms))
        self.assertEqual(sum(values), 0)
        value = adjacent_snr(waveform.rms, waveform.counts, 16000, [(.45, .85)], offset=.25)[0]
        noise_energy = sum(v*v for v in floor) * 30 + 2 * 48**2
        noise_power = noise_energy / (320 * 30 + 2)
        expected = 10 * math.log10((sum(v*v for v in signal) / 320 - noise_power) / noise_power)
        self.assertIsNotNone(value)
        assert value is not None
        self.assertLess(value, 0)
        self.assertAlmostEqual(value, expected, places=9)

    def test_real_pcm_loud_history_does_not_fabricate_local_snr(self) -> None:
        from backend.speech_analysis import adjacent_snr
        frame = [48, -48] * 159 + [49, -49]
        # Twenty seconds of full-scale remote PCM followed by equal local
        # non-dyadic RMS. Neither energy cancellation nor global quiet picking
        # may turn equal signal/noise into a measurement.
        with tempfile.TemporaryDirectory(prefix="gm-snr-history-pcm-") as directory:
            path = Path(directory) / "pcm.wav"
            _pcm(path, [32767, -32767] * 160000 + frame * 100)
            waveform = module._analyze_pcm(path, .25, 1800., False, .2, 32, threading.Event())
        self.assertEqual(waveform.counts, [320] * 1100)
        self.assertEqual(len(set(waveform.rms[-100:])), 1)
        self.assertEqual(adjacent_snr(waveform.rms, waveform.counts, 16000,
                                      [(20.75, 21.75)], offset=.25), [None])

    def test_word_only_diarization_unanimity_does_not_merge_different_people(self) -> None:
        record = module._Manifest(id="up_" + "a" * 32, name="a.mp4", size=1, sha256="0" * 64,
                                  token_hash="1" * 64, owner_hash="2" * 64, content_type="video/mp4",
                                  created_at=0, expires_at=86400,
                                  probe=module._Probe(sec=1.0, width=64, height=48, fps=25.0, has_audio=True, format_name="mov"))
        result = _result(speaker=None)
        for word in result.utterances[0].words:
            word.speaker_id = "speaker-a"
        waveform = module._Wave([], [], 0, [], None, "unknown")
        converted = module._editorial_transcript(record, result, waveform, threading.Event())
        self.assertEqual(converted.segments[0].speaker_id, _speaker_id(record.id, "speaker-a"))
        result.utterances[0].words[1].speaker_id = "speaker-b"
        converted = module._editorial_transcript(record, result, waveform, threading.Event())
        self.assertEqual(converted.segments[0].speaker_id, "")
        self.assertEqual(converted.word_speaker_ids["seg_0"], [_speaker_id(record.id, "speaker-a"), _speaker_id(record.id, "speaker-b")])

    def test_missing_or_blank_speaker_evidence_never_creates_a_cluster(self) -> None:
        record = module._Manifest(id="up_" + "a" * 32, name="a.mp4", size=1, sha256="0" * 64,
                                  token_hash="1" * 64, owner_hash="2" * 64, content_type="video/mp4",
                                  created_at=0, expires_at=module.UPLOAD_TTL_SECONDS,
                                  probe=module._Probe(sec=1.0, width=64, height=48, fps=25.0, has_audio=True, format_name="mov"))
        for source_id in (None, "", "  "):
            result = _result(speaker=source_id)
            for word in result.utterances[0].words:
                word.speaker_id = source_id
            converted = module._editorial_transcript(record, result, module._Wave([], [], 0, [], None, "unknown"), threading.Event())
            self.assertEqual(converted.speakers, [])
            self.assertEqual(converted.segments[0].speaker_id, "")
            self.assertEqual(converted.word_speaker_ids["seg_0"], [None, None])
            self.assertIsNone(converted.segments[0].snr_db)

    def test_provider_time_maps_by_measured_audio_offset_and_never_by_equal_word_splits(self) -> None:
        record = module._Manifest(id="up_" + "a" * 32, name="a.mp4", size=1, sha256="0" * 64,
                                  token_hash="1" * 64, owner_hash="2" * 64, content_type="video/mp4",
                                  created_at=0, expires_at=86400,
                                  probe=module._Probe(sec=2.0, width=64, height=48, fps=25.0, has_audio=True, audio_offset=0.25, format_name="mov"))
        result = _result()
        converted = module._editorial_transcript(record, result, module._Wave([], [], 0, [], None, "unknown"), threading.Event())
        self.assertEqual(converted.segments[0].start, 0.45)
        self.assertEqual(converted.segments[0].words[0].s, 0.45)
        self.assertEqual(converted.segments[0].words[0].e, 0.65)
        self.assertEqual(result.utterances[0].words[0].start_time_ms, 200)


class ProcessLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_final_reap_is_not_swallowed_into_success(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_data(b"done")
        stdout.feed_eof()
        stderr.feed_eof()
        process = SimpleNamespace(stdout=stdout, stderr=stderr, returncode=0, wait=AsyncMock(return_value=0))
        reaped = False

        async def reap(*args: Any) -> None:
            nonlocal reaped
            entered.set()
            await release.wait()
            reaped = True

        with patch.object(module.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=process), patch.object(module, "_reap_process", side_effect=reap):
            operation = asyncio.create_task(module._run_media(["ffmpeg"], timeout=5, limit=1024))
            await entered.wait()
            operation.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await operation
        self.assertTrue(reaped)

    async def test_cancel_during_process_creation_still_kills_and_reaps_child(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class Process:
            def __init__(self) -> None:
                self.returncode = None
                self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
                self.killed, self.waited = False, False

            def kill(self) -> None:
                self.killed, self.returncode = True, -1
                self.stdout.feed_eof()
                self.stderr.feed_eof()

            async def wait(self) -> int:
                self.waited = True
                return self.returncode or 0

        process = Process()

        async def spawn(*args: Any, **kwargs: Any) -> Any:
            entered.set()  # Native child creation can precede the async result.
            await release.wait()
            return process

        with patch.object(module.asyncio, "create_subprocess_exec", side_effect=spawn):
            operation = asyncio.create_task(module._run_media(["ffmpeg"], timeout=5, limit=1024))
            await entered.wait()
            operation.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await operation
        self.assertTrue(process.killed)
        self.assertTrue(process.waited)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Local FFmpeg/ffprobe required; no download attempted")
class RealUploadMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="gm-upload-real-media-")
        self.root = Path(self.temp.name)
        self.calls: list[str] = []
        self.settings = _settings(self.root)
        self.store = UploadStore(self.settings)
        self.patch = patch.object(module, "create_asr_provider", side_effect=lambda settings: FakeProvider(self.calls))
        self.patch.start()

    async def asyncTearDown(self) -> None:
        await self.store.close()
        self.patch.stop()
        self.temp.cleanup()

    async def _fixture(self, audio: str | None) -> Path:
        path = self.root / "fixture.mp4"
        command = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i", "color=c=red:s=64x48:r=25:d=1"]
        if audio is not None:
            command += ["-f", "lavfi", "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac"]
        command += ["-t", "1", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(path)]
        await module._run_media(command, timeout=15.0, limit=1024)
        return path

    async def _process(self, path: Path, *, content_type: str = "application/octet-stream") -> dict[str, Any]:
        data = path.read_bytes()
        created = await self.store.create(name=path.name, size=len(data), sha256=_sha(data), owner="synthetic-owner", content_type=content_type)
        key, token = created["upload_id"], created["access_token"]
        for index, start in enumerate(range(0, len(data), CHUNK_SIZE)):
            part = data[start:start + CHUNK_SIZE]
            await self.store.put_chunk(key, token, index, _stream(part, 257), len(part))
        await self.store.complete(key, token)
        await self.store._jobs[key]
        return {**created, "state": self.store.read(key, token)}

    async def _check_photo(self, suffix: str, mime: str) -> None:
        from backend import media_input
        data = _photo_data(suffix)
        source = self.root / ("photo" + suffix)
        source.write_bytes(data)
        loop_thread = threading.get_ident()
        decoder = media_input._decode_photo
        media = module._run_media
        decoded: list[tuple[Path, Path | None]] = []
        commands: list[list[str]] = []

        def decode(path: Path, settings: Settings, output: Path | None = None) -> tuple[int, int]:
            self.assertNotEqual(threading.get_ident(), loop_thread)
            result = decoder(path, settings, output)
            decoded.append((path, output))
            return result

        async def run(command: list[str], **kwargs: Any) -> bytes:
            commands.append(command)
            canonical = Path(command[command.index("-i") + 1])
            self.assertEqual(canonical, decoded[-1][1])
            self.assertTrue(canonical.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertNotEqual(canonical, decoded[-1][0])
            return await media(command, **kwargs)

        with patch.object(media_input, "_decode_photo", side_effect=decode), patch.object(module, "_run_media", side_effect=run):
            created = await self._process(source, content_type=mime)
        key, token, state = created["upload_id"], created["access_token"], created["state"]
        self.assertEqual(state["status"], "ready", state)
        self.assertEqual(state["sec"], 3.0)
        self.assertTrue(state["is_image"])
        self.assertFalse(state["has_audio"])
        self.assertFalse(state["has_speech"])
        self.assertEqual(state["speech_evidence"], "no_audio")
        self.assertEqual(state["precision"], "unavailable")
        self.assertEqual(state["transcript"]["segments"], [])
        self.assertEqual(state["speakers"], [])
        self.assertIsNone(state["asr_confidence"])
        self.assertEqual(state["silences"], [])
        self.assertIsNone(state["silence_method"])
        self.assertTrue(state["can_materialize"])
        self.assertFalse(state["asr_retry_in_task"])
        dimensions = (1, 1) if suffix.lower() == ".gif" else (64, 48)
        self.assertEqual((state["width"], state["height"]), dimensions)
        record = self.store.authorize(key, token)
        self.assertEqual(record.probe.audio_offset, 0.0)
        self.assertEqual(record.probe.fps, 1.0)
        self.assertEqual(record.probe.format_name, "jpeg" if suffix.lower() in {".jpg", ".jpeg"} else suffix.lower()[1:])
        self.assertEqual(len(decoded), 2)
        self.assertIsNone(decoded[0][1])
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertEqual(command[0], "ffmpeg")
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertEqual(command[command.index("-format_whitelist") + 1], "image2")
        self.assertEqual(command[command.index("-pattern_type") + 1], "none")
        self.assertEqual(command[command.index("-c:v") + 1], "png")
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertNotIn("-loop", command)
        wave_data = json.loads(self.store.media(key, token, "wave"))
        self.assertFalse(wave_data["has_audio"])
        self.assertFalse(wave_data["measured"])
        self.assertEqual(wave_data["rms"], [])
        for field in ("samples", "duration_seconds", "last_interval_ms", "offset_seconds"):
            self.assertEqual(wave_data[field], 0)
        thumb = self.store.media(key, token, "thumb")
        pixels = media_input.cv2.imdecode(media_input.np.frombuffer(thumb, dtype=media_input.np.uint8), media_input.cv2.IMREAD_COLOR)
        self.assertIsNotNone(pixels)
        self.assertLessEqual(max(pixels.shape[:2]), 480)
        blue, _, red = pixels[pixels.shape[0] // 2, pixels.shape[1] // 2]
        self.assertGreater(int(red), 190)
        self.assertLess(int(blue), 40)
        original = self.store._source(record)
        self.assertEqual(original.suffix, suffix.lower())
        self.assertEqual(_sha(original.read_bytes()), _sha(data))
        self.assertFalse(list(original.parent.glob("*.mp4")))
        self.assertFalse(list(original.parent.glob("write-*.tmp")))
        self.assertFalse((original.parent / "audio.wav").exists())
        task = self.settings.data_dir / "photo-task"
        task.mkdir()
        assets, snapshots = await self.store.materialize([key], {key: token}, task)
        asset = assets[0]
        self.assertEqual(asset.upload_id, key)
        self.assertEqual(asset.source_duration_seconds, 3.0)
        self.assertIsNone(asset.prepared_stored_name)
        self.assertEqual(asset.path.suffix, suffix.lower())
        self.assertEqual(asset.content_type, mime)
        self.assertEqual(asset.path.read_bytes(), data)
        self.assertNotEqual(asset.path.stat().st_ino, original.stat().st_ino)
        self.assertEqual(list((task / "raw").iterdir()), [asset.path])
        self.assertTrue(snapshots[0]["is_image"])
        self.assertIsNone(snapshots[0]["asr_result"])
        self.assertEqual(snapshots[0]["audio_offset_seconds"], 0.0)
        before = (original.parent / "manifest.json").read_bytes()
        await self.store.close()
        self.store = UploadStore(self.settings)
        self.assertEqual(self.store.read(key, token), state)
        self.assertEqual((original.parent / "manifest.json").read_bytes(), before)
        self.assertEqual(self.calls, [])

    async def test_real_jpg_is_safe_three_second_still_and_raw_copy(self) -> None:
        await self._check_photo(".JPG", "image/jpeg")

    async def test_real_jpeg_is_safe_three_second_still_and_raw_copy(self) -> None:
        await self._check_photo(".jpeg", "image/jpeg")

    async def test_real_png_is_safe_three_second_still_and_raw_copy(self) -> None:
        await self._check_photo(".png", "image/png")

    async def test_real_animated_gif_thumbnail_uses_only_first_frame_and_keeps_raw_animation(self) -> None:
        await self._check_photo(".gif", "image/gif")

    async def test_bad_photo_headers_dimensions_and_all_gif_frames_rejected_before_native_decode(self) -> None:
        from backend import media_input
        cases = [("invalid.jpg", b"not a jpeg"), ("invalid.png", b"not a png"), ("invalid.gif", b"not a gif"),
                 ("disguised.png", _photo_data(".jpg")), ("zero.png", _png_header(0, 48)),
                 ("wide.png", _png_header(7681, 48)), ("tall.png", _png_header(64, 4321)),
                 ("pixels.png", _png_header(5001, 4000)), ("canvas.gif", _gif_data(65535, 65535)),
                 ("rectangle.gif", _gif_data(second_rect=(0, 0, 65535, 65535))),
                 ("offset.gif", _gif_data(second_rect=(1, 0, 1, 1))),
                 ("frames.gif", _gif_data(frames=301)), ("total-pixels.gif", _gif_data(5000, 4000, 6)),
                 ("truncated.gif", _gif_data()[:-1])]
        with patch.object(media_input.cv2, "imdecode", side_effect=AssertionError("Unsafe native decode")) as decoder, patch.object(module, "_run_media", new_callable=AsyncMock) as command:
            for name, data in cases:
                with self.subTest(name=name):
                    path = self.root / name
                    path.write_bytes(data)
                    created = await self._process(path)
                    state = created["state"]
                    self.assertEqual(state["status"], "failed")
                    self.assertEqual(state["error_code"], "MEDIA_INVALID")
                    self.assertFalse(state["probe_ok"])
                    self.assertFalse(state["can_materialize"])
                    self.assertFalse(state["asr_retry_in_task"])
                    self.assertIsNone(state["is_image"])
                    self.assertIsNone(state["thumb_url"])
                    self.assertIsNone(state["wave_url"])
                    for text in ("50 MiB", "2000 万像素", "300 帧", "1 亿", "500 MiB"):
                        self.assertIn(text, state["error"])
                    self.assertNotIn(str(self.root), state["error"])
                    self.assertEqual(await self.store.complete(created["upload_id"], created["access_token"]), state)
                    await self.store.delete(created["upload_id"], created["access_token"])
            decoder.assert_not_called()
            command.assert_not_awaited()
        self.assertEqual(self.calls, [])

    async def test_valid_photo_header_still_requires_real_successful_decode(self) -> None:
        from backend import media_input
        path = self.root / "missing-pixels.png"
        path.write_bytes(_png_header(64, 48))
        with patch.object(media_input.cv2, "imdecode", wraps=media_input.cv2.imdecode) as decoder, patch.object(module, "_run_media", new_callable=AsyncMock) as command:
            created = await self._process(path)
            decoder.assert_called_once()
            command.assert_not_awaited()
        self.assertEqual(created["state"]["status"], "failed")
        self.assertFalse(created["state"]["probe_ok"])
        self.assertEqual(self.calls, [])

    async def test_photo_header_boundaries_equal_production_without_large_allocation(self) -> None:
        from backend import media_input
        self.assertEqual(media_input._image_dimensions(_png_header(5000, 4000), ".png", self.settings), (5000, 4000))
        self.assertEqual(media_input._image_dimensions(_gif_data(5000, 4000, 5), ".gif", self.settings), (5000, 4000))
        self.assertEqual(media_input._image_dimensions(_gif_data(frames=300), ".gif", self.settings), (1, 1))
        created = await self.store.create(name="limit.png", size=33, sha256=_sha(_png_header(5000, 4000)), owner="synthetic-owner")
        key, token = created["upload_id"], created["access_token"]
        await self.store.put_chunk(key, token, 0, _stream(_png_header(5000, 4000)), 33)
        record = self.store.authorize(key, token)
        await module._blocking(self.store._assemble, record)
        # Shape-only native substitute: this proves the exact header admission
        # boundary, not that this header-only fixture is a decodable image.
        with patch.object(media_input.cv2, "imdecode", return_value=SimpleNamespace(shape=(4000, 5000, 3))) as decoder:
            probe = await self.store._probe(record)
            decoder.assert_called_once()
        self.assertEqual((probe.width, probe.height, probe.sec, probe.is_image), (5000, 4000, 3.0, True))
        with patch.object(self.store, "max_seconds", 2.9), patch.object(media_input, "_decode_photo") as decoder:
            with self.assertRaises(HTTPException) as error:
                await self.store._probe(record)
            self.assertEqual(error.exception.status_code, 415)
            decoder.assert_not_called()
        self.assertEqual(self.calls, [])

    async def test_photo_byte_cap_is_rechecked_before_reading_payload_or_native_decode(self) -> None:
        from backend import media_input
        data = _photo_data(".png")
        created = await self.store.create(name="bounded.png", size=len(data), sha256=_sha(data), owner="synthetic-owner")
        key, token = created["upload_id"], created["access_token"]
        await self.store.put_chunk(key, token, 0, _stream(data), len(data))
        record = self.store.authorize(key, token)
        await module._blocking(self.store._assemble, record)
        source = self.store._source(record)
        original_stat = Path.stat

        def oversized(path: Path, **kwargs: Any) -> Any:
            info = original_stat(path, **kwargs)
            if path == source and kwargs.get("follow_symlinks", True):
                return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino, st_size=50 * 1024**2 + 1, st_mtime_ns=info.st_mtime_ns)
            return info

        with patch.object(Path, "stat", new=oversized), patch.object(Path, "read_bytes", side_effect=AssertionError("Oversized photo read")) as read, patch.object(media_input.cv2, "imdecode") as decoder:
            with self.assertRaises(HTTPException) as error:
                await self.store._probe(record)
            self.assertEqual(error.exception.status_code, 415)
            read.assert_not_called()
            decoder.assert_not_called()
        self.assertEqual(source.read_bytes(), data)
        self.assertEqual(self.calls, [])

    async def test_cancelled_photo_thumbnail_drains_decoder_before_removing_canonical_temp(self) -> None:
        from backend import media_input
        data = _photo_data(".png")
        created = await self.store.create(name="photo.png", size=len(data), sha256=_sha(data), owner="synthetic-owner")
        key, token = created["upload_id"], created["access_token"]
        await self.store.put_chunk(key, token, 0, _stream(data), len(data))
        record = self.store.authorize(key, token)
        await module._blocking(self.store._assemble, record)
        record.probe = await self.store._probe(record)
        original = media_input._decode_photo
        entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        outputs: list[Path] = []

        def held(path: Path, settings: Settings, output: Path | None = None) -> tuple[int, int]:
            assert output is not None
            dimensions = original(path, settings, output)
            outputs.append(output)
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise AssertionError("Photo cancellation test did not release worker")
            finished.set()
            return dimensions

        with patch.object(media_input, "_decode_photo", side_effect=held), patch.object(module, "_run_media", new_callable=AsyncMock) as command:
            operation = asyncio.create_task(self.store._thumbnail(record))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                operation.cancel()
                await asyncio.sleep(0)
                operation.cancel()
                await asyncio.sleep(0)
                self.assertFalse(operation.done())
                self.assertTrue(outputs[0].exists())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await operation
            command.assert_not_awaited()
        self.assertTrue(finished.is_set())
        self.assertFalse(outputs[0].exists())
        self.assertFalse(record.thumb)
        self.assertEqual(self.store._source(record).read_bytes(), data)
        self.assertEqual(self.calls, [])

    async def test_materialized_photos_prepare_separate_three_second_videos_without_replacing_raw(self) -> None:
        from backend.media_input import prepare_media_inputs, processing_uploads
        for suffix in (".jpg", ".png", ".gif"):
            with self.subTest(suffix=suffix):
                path = self.root / ("prepare" + suffix)
                data = _photo_data(suffix)
                path.write_bytes(data)
                created = await self._process(path)
                key, token = created["upload_id"], created["access_token"]
                self.assertEqual(created["state"]["status"], "ready", created["state"])
                task = self.settings.data_dir / ("prepare-" + suffix[1:])
                task.mkdir()
                assets, _ = await self.store.materialize([key], {key: token}, task)
                self.assertEqual(list((task / "raw").iterdir()), [assets[0].path])
                with patch("backend.media_input.create_asr_provider") as provider:
                    prepared = await prepare_media_inputs(task, assets, None, None, self.settings)
                    provider.assert_not_called()
                self.assertEqual(prepared[0].upload_id, key)
                self.assertEqual(prepared[0].path, assets[0].path)
                self.assertEqual(prepared[0].stored_name, assets[0].stored_name)
                self.assertEqual(prepared[0].path.read_bytes(), data)
                self.assertEqual(self.store._source(self.store.authorize(key, token)).read_bytes(), data)
                derived = processing_uploads(task, prepared)[0]
                self.assertNotEqual(derived.path, assets[0].path)
                self.assertEqual(derived.path.suffix, ".mp4")
                self.assertEqual(derived.upload_id, key)
                raw = await module._run_media(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov", "-show_streams", "-show_format", "-of", "json", str(derived.path)], timeout=15, limit=256 * 1024)
                probe = json.loads(raw)
                self.assertAlmostEqual(float(probe["format"]["duration"]), 3.0, places=2)
                self.assertEqual(len(probe["streams"]), 1)
                self.assertEqual(probe["streams"][0]["codec_type"], "video")
                self.assertEqual(probe["streams"][0]["codec_name"], "h264")
                self.assertEqual((probe["streams"][0]["width"], probe["streams"][0]["height"]), (1920, 1080))
        self.assertEqual(self.calls, [])

    async def test_real_probe_first_frame_and_20ms_silent_waveform_no_provider(self) -> None:
        fixture = await self._fixture("anullsrc=r=16000:cl=mono")
        created = await self._process(fixture)
        state = created["state"]
        self.assertEqual(state["status"], "ready", state)
        self.assertFalse(state["has_speech"])
        self.assertEqual(state["speech_evidence"], "digital_silence")
        self.assertAlmostEqual(state["sec"], 1.0, places=2)
        wave_data = json.loads(self.store.media(created["upload_id"], created["access_token"], "wave"))
        self.assertEqual(wave_data["interval_ms"], 20)
        self.assertTrue(wave_data["measured"])
        self.assertEqual(len(wave_data["rms"]), math.ceil(wave_data["samples"] / 320))
        self.assertTrue(all(level == 0.0 for level in wave_data["rms"]))
        thumb = self.store.media(created["upload_id"], created["access_token"], "thumb")
        self.assertTrue(thumb.startswith(b"\xff\xd8"))
        self.assertEqual(self.calls, [])
        self.assertFalse((self.store.root / created["upload_id"] / "audio.wav").exists())

    async def test_real_nonzero_audio_calls_only_mock_provider_and_preserves_words(self) -> None:
        fixture = await self._fixture("sine=frequency=440:sample_rate=16000:duration=1")
        created = await self._process(fixture)
        self.assertEqual(created["state"]["status"], "ready", created["state"])
        self.assertEqual(created["state"]["precision"], "word")
        self.assertTrue(created["state"]["has_speech"])
        self.assertEqual(len(self.calls), 1)
        waveform = json.loads(self.store.media(created["upload_id"], created["access_token"], "wave"))
        self.assertTrue(any(value > 0 for value in waveform["rms"]))
        self.assertGreaterEqual(self.store._timeout(0.02), 5.0)

    async def test_video_without_audio_has_real_thumb_and_no_fabricated_wave(self) -> None:
        created = await self._process(await self._fixture(None))
        self.assertEqual(created["state"]["status"], "ready", created["state"])
        self.assertEqual(created["state"]["speech_evidence"], "no_audio")
        waveform = json.loads(self.store.media(created["upload_id"], created["access_token"], "wave"))
        self.assertEqual(waveform["rms"], [])
        self.assertFalse(waveform["has_audio"])
        self.assertFalse(waveform["measured"])
        self.assertFalse(created["state"]["is_image"])
        self.assertFalse(created["state"]["has_speech"])
        self.assertIsNone(created["state"]["silence_method"])
        self.assertEqual(self.calls, [])

    async def test_hidden_hls_and_url_inputs_rejected_by_real_demuxer_before_asr(self) -> None:
        path = self.root / "hidden.mp4"
        path.write_bytes(b"#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://127.0.0.1:9/not-allowed.ts\n#EXT-X-ENDLIST\n")
        created = await self._process(path)
        self.assertEqual(created["state"]["status"], "failed")
        self.assertFalse(created["state"]["probe_ok"])
        self.assertEqual(self.calls, [])

    async def test_probe_timeout_and_both_pipe_output_caps_are_enforced(self) -> None:
        command = ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-f", "s16le", "pipe:1"]
        with self.assertRaises(HTTPException):
            await module._run_media(command, timeout=5.0, limit=1024)
        with self.assertRaises(TimeoutError):
            await module._run_media(["ffmpeg", "-v", "error", "-nostdin", "-re", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-f", "null", "-"], timeout=0.05, limit=1024)


if __name__ == "__main__":
    unittest.main()