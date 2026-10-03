"""Real sample preparation/runtime/ASGI integration under run_v2_validation.

Only fresh TEMP lavfi patterns and optional silence; no approved product sample,
human speech, rights review, quality assessment, installation or live host.
The CLI rights flag is explicitly a synthetic-test attestation. Reuse fixture
functions by composition, not TestCase inheritance or extracted product code.
No standalone unittest entry point: install the shared V2 guards BEFORE loading
this module/running its tests. Main is imported lazily inside guarded setup.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from deploy import prepare_sample_bundle as bundle
from tests.workspace_fixture import media_command, sha256


class SampleRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from backend import main, v2_editing
        from backend.config import Settings
        from backend.models import TaskState
        from backend.task_manager import TaskRecord

        self.main, self.runtime = main, v2_editing
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(
            tempfile.TemporaryDirectory(prefix="gm-sample-runtime-"))).resolve()
        self.source, self.output = self.root / "synthetic-source", self.root / "staging"
        self.source.mkdir()
        self.identity = "a" * 32
        self.title = "Synthetic lavfi test fixture - not approved news"
        settings = Settings(_env_file=None, app_env="test", data_dir=self.root / "tasks",
                            asr_cache_dir=self.root / "cache", min_free_disk_gb=0)
        # A readable-looking same-identity task exists, but must NEVER supply a
        # missing/invalid sample. This is our own TEMP decoy, not user storage.
        self.decoy = settings.data_dir / self.identity
        self.decoy.mkdir(parents=True)
        record = TaskRecord(self.identity, self.decoy, "Synthetic fallback decoy", [],
                            status=TaskState.done)
        self.stack.enter_context(patch.object(main, "settings", settings))
        self.stack.enter_context(patch.multiple(main.task_manager, settings=settings,
                                                _tasks={self.identity: record}))
        self.task_get = self.stack.enter_context(patch.object(
            main.task_manager, "get", side_effect=AssertionError("sample task fallback")))
        self.task_restore = self.stack.enter_context(patch.object(
            main.task_manager, "restore_tasks", side_effect=AssertionError("sample task discovery")))
        # Bind the EXACT fresh staging directory, never the shipped asset tree.
        self.stack.enter_context(patch.object(v2_editing, "SAMPLE_ASSETS", self.output))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(
            app=main.app, client=("198.51.100.23", 12345)), base_url="http://localhost")
        self.addAsyncCleanup(self.client.aclose)
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        self.assertIsNotNone(self.ffmpeg, "configured FFmpeg required; no skip/download")
        self.assertIsNotNone(self.ffprobe, "configured ffprobe required; no skip/download")
        self.assertEqual(Path(self.ffmpeg).parent, Path(self.ffprobe).parent)

    def hashes(self, root):
        return {p.relative_to(root).as_posix(): sha256(p)
                for p in root.rglob("*") if p.is_file()}

    def prepare(self, *, audio=True, timings=True):
        arguments = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                     "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10:duration=2"]
        if audio:
            arguments += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=2"]
        arguments += ["-map", "0:v:0", "-c:v", "libx264", "-threads", "1",
                      "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        arguments += ["-map", "1:a:0", "-c:a", "aac"] if audio else ["-an"]
        arguments += ["-t", "2", "-movflags", "+faststart", str(self.source / "final.mp4")]
        media_command(arguments)
        self.rows = [{"sentence_id": sid, "sentence": "Synthetic pattern; no speech evidence",
                      "shot_id": sid, "thumb_url": None, "description": "lavfi synthetic fixture",
                      "duration": 1.0, "confidence": 0.8, "is_fallback": False,
                      "kind": "narration" if sid == 0 else "quote", "audio_kind": kind,
                      "visual_beats": [{"beat_id": 0, "text": "Synthetic pattern", "shot_id": sid,
                                        "thumb_url": "", "description": "Not news evidence",
                                        "confidence": 0.8}]}
                     for sid, kind in enumerate(("tts", "sync"))]
        # These audio labels exercise metadata transport ONLY. Silence/test
        # pictures do not establish actual TTS, sync speech, or alignment.
        report = {"task_id": self.identity, "mode": "mixed", "rows": self.rows}
        (self.source / "report.json").write_text(json.dumps(report), encoding="utf-8")
        self.timings = [{"sentence_id": sid, "start": float(sid), "end": float(sid + 1)}
                        for sid in range(2)]
        if timings:
            (self.source / "timings.json").write_text(json.dumps(self.timings), encoding="utf-8")
        names = ["final.mp4", "report.json", *(["timings.json"] if timings else [])]
        inventory = {name: {"sha256": sha256(self.source / name),
                            "bytes": (self.source / name).stat().st_size} for name in names}
        descriptor = {"schema_version": 1, "task_id": self.identity,
                      "title": self.title, "files": inventory}
        (self.source / bundle.DESCRIPTOR).write_text(json.dumps(descriptor), encoding="utf-8")
        self.assertFalse(self.output.exists())
        source_before = self.hashes(self.source)
        stdout = io.StringIO()
        # Actual CLI entry function/argparse + prepare + streamed staging copy;
        # no Python child or mock of the preparer. Attestation is TEST ONLY.
        with redirect_stdout(stdout):
            code = bundle.main(["--source", str(self.source), "--output", str(self.output),
                                "--rights-reviewed"])
        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["status"], "prepared_not_installed")
        self.assertEqual(summary["rights_review"], "caller_attested_not_verified")
        self.assertEqual(summary["report_profile"], "minimal_public_v1")
        self.assertEqual(summary["media_check"], "ftyp_header_only")
        for key in ("ffprobe_performed", "quality_verified", "privacy_verified", "runtime_render_verified"):
            self.assertIs(summary[key], False)
        self.assertEqual(summary["files"], inventory)
        self.assertEqual(summary["file_count"], len(names))
        self.assertEqual(self.hashes(self.source), source_before)
        self.assertEqual(set(self.hashes(self.output)), {"registry.json", *("default/" + n for n in names)})
        registry = json.loads((self.output / "registry.json").read_bytes())
        self.assertEqual(registry, {"default": {"directory": "default", "task_id": self.identity,
                         "title": self.title, "files": {n: inventory[n]["sha256"] for n in names}}})
        self.video = (self.source / "final.mp4").read_bytes()
        self.assertGreater(len(self.video), 1024)
        self.assertLess(len(self.video), 256 * 1024)  # bounded full-body assertion
        for name in names:
            target = self.output / "default" / name
            self.assertEqual(sha256(target), inventory[name]["sha256"])
            self.assertNotEqual(target.stat().st_ino, (self.source / name).stat().st_ino)
            (self.decoy / name).write_bytes((self.source / name).read_bytes())

    @contextmanager
    def no_fallback_reads(self):
        # Extra fail-closed tripwires supplement shared guards: even our own
        # valid TEMP task and the actual packaged-assets directory are off limits.
        forbidden = (self.decoy.parent, bundle.ROOT / "backend/assets/samples")

        def wrapped(original):
            def checked(file, *args, **kwargs):
                if isinstance(file, (str, bytes, os.PathLike)):
                    path = Path(os.fsdecode(file)).absolute()
                    if any(path.is_relative_to(root) for root in forbidden):
                        self.fail("sample accessed task or shipped assets instead of staging")
                return original(file, *args, **kwargs)
            return checked

        with ExitStack() as stack:
            for owner, name in ((builtins, "open"), (io, "open"), (os, "open"),
                                (os, "listdir"), (os, "scandir")):
                stack.enter_context(patch.object(owner, name, wrapped(getattr(owner, name))))
            yield
        self.task_get.assert_not_called()
        self.task_restore.assert_not_called()

    async def get(self, path, *, headers=None):
        before = self.hashes(self.root)
        try:
            with self.no_fallback_reads():
                return await self.client.get(path, headers=headers)
        finally:
            self.assertEqual(self.hashes(self.root), before, "GET mutated source/staging/decoy")

    def direct(self, *, video=False):
        before = self.hashes(self.root)
        try:
            with self.no_fallback_reads():
                return self.runtime.packaged_sample(video=video)
        finally:
            self.assertEqual(self.hashes(self.root), before, "runtime mutated fixtures")

    def assert_report(self, payload, *, timings):
        self.assertIs(payload["read_only"], True)
        self.assertEqual((payload["task_id"], payload["title"]), (self.identity, self.title))
        self.assertEqual(payload["video_url"], "/api/samples/default/video")
        # Public projection deliberately nulls links after internal validation.
        report = payload["report"]
        self.assertIs(type(report), dict)
        self.assertEqual((report["task_id"], report["mode"]), (self.identity, "mixed"))
        rows = report["rows"]
        self.assertIs(type(rows), list)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["audio_kind"] for r in rows], ["tts", "sync"])
        self.assertEqual([r["sentence_id"] for r in rows], [0, 1])
        self.assertEqual([r["audio_source"] for r in rows], ["tts", "sync"])
        for row, expected in zip(rows, self.timings):
            self.assertIsNone(row["thumb_url"])
            self.assertEqual(len(row["visual_beats"]), 1)
            self.assertIsNone(row["visual_beats"][0]["thumb_url"])
            if timings:
                self.assertEqual((row["start"], row["end"]), (expected["start"], expected["end"]))
            else:
                self.assertIsNone(row.get("start"))
                self.assertIsNone(row.get("end"))

    def assert_real_media(self, *, audio):
        path = self.output / "default/final.mp4"
        before = self.hashes(self.root)
        probe = json.loads(media_command([self.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_format", "-show_streams", "-of", "json", str(path)]))
        self.assertIn("mp4", probe["format"]["format_name"].split(","))
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=0.001)
        videos = [s for s in probe["streams"] if s["codec_type"] == "video"]
        audios = [s for s in probe["streams"] if s["codec_type"] == "audio"]
        self.assertEqual(len(videos), 1)
        stream = videos[0]
        self.assertEqual((stream["codec_name"], stream["width"], stream["height"], stream["pix_fmt"]),
                         ("h264", 64, 64, "yuv420p"))
        self.assertEqual(int(stream["nb_frames"]), 20)
        self.assertAlmostEqual(float(stream["duration"]), 2.0, delta=0.001)
        self.assertEqual(len(audios), int(audio))
        if audio:
            self.assertEqual((audios[0]["codec_name"], audios[0]["sample_rate"], audios[0]["channels"]),
                             ("aac", "48000", 1))
            self.assertAlmostEqual(float(audios[0]["duration"]), 2.0, delta=0.03)
        # Decode every frame, not just the ftyp header or declared frame count.
        raw = media_command([self.ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-xerror",
            "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:v:0", "-an",
            "-fps_mode", "passthrough", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
        self.assertEqual(len(raw), 20 * 64 * 64 * 3)
        self.assertGreater(len(set(raw)), 1)
        self.assertEqual(self.hashes(self.root), before)

    async def assert_available(self, *, timings):
        direct = self.direct()
        self.assert_report(direct, timings=timings)
        file_response = self.direct(video=True)
        self.assertEqual(Path(file_response.path), self.output / "default/final.mp4")
        self.assertEqual(file_response.headers["cache-control"], "no-store")
        response = await self.get("/api/samples/default")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), direct)
        self.assert_report(response.json(), timings=timings)
        response = await self.get("/api/samples/default/video")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.video)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(int(response.headers["content-length"]), len(self.video))
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        # The mounted API middleware strengthens the direct file response.
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        size = len(self.video)
        for value, start, end in (("bytes=0-31", 0, 31), ("bytes=257-511", 257, 511),
                                  ("bytes=-32", size - 32, size - 1)):
            response = await self.get("/api/samples/default/video", headers={"Range": value})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.headers["content-range"], f"bytes {start}-{end}/{size}")
            self.assertEqual(int(response.headers["content-length"]), end - start + 1)
            self.assertEqual(response.headers["content-type"], "video/mp4")
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            self.assertEqual(response.content, self.video[start:end + 1])

    async def assert_unavailable(self):
        for video in (False, True):
            response = self.direct(video=video)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(json.loads(response.body)["code"], "sample_unavailable")
        for path, headers in (("/api/samples/default", {}), ("/api/samples/default/video", {}),
                              ("/api/samples/default/video", {"Range": "bytes=0-31"})):
            response = await self.get(path, headers=headers)
            self.assertEqual(response.status_code, 503)
            payload = response.json()
            self.assertEqual(payload["code"], "sample_unavailable")
            self.assertIs(payload["read_only"], True)
            self.assertNotIn("report", payload)
            self.assertNotIn("video_url", payload)
            self.assertNotIn("content-range", response.headers)

    async def test_prepared_h264_aac_report_and_mounted_full_range_reads(self):
        self.prepare()
        self.assert_real_media(audio=True)
        await self.assert_available(timings=True)

    async def test_video_only_without_timings_does_not_invent_seek_clocks(self):
        self.prepare(audio=False, timings=False)
        self.assert_real_media(audio=False)
        await self.assert_available(timings=False)

    async def test_tampered_media_fails_closed_without_task_fallback(self):
        self.prepare()
        path = self.output / "default/final.mp4"
        corrupted = bytearray(path.read_bytes())
        corrupted[-1] ^= 1  # same length, real MP4 origin; hash verification must fail
        path.write_bytes(corrupted)
        await self.assert_unavailable()

    async def test_tampered_registered_metadata_fails_closed(self):
        self.prepare()
        for name in ("report.json", "timings.json"):
            with self.subTest(metadata=name):
                path = self.output / "default" / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\n")  # valid JSON, different digest
                    await self.assert_unavailable()
                finally:
                    path.write_bytes(original)

    async def test_unregistered_projection_metadata_fails_closed(self):
        self.prepare(timings=False)
        for name, value in (("timings.json", self.timings), ("shots_annotated.json", []),
                            ("v2_apply_plan.json", {"replace_failures": {}})):
            with self.subTest(metadata=name):
                path = self.output / "default" / name
                try:
                    path.write_text(json.dumps(value), encoding="utf-8")
                    await self.assert_unavailable()
                finally:
                    path.unlink()

    async def test_missing_registry_fails_closed_despite_valid_task_decoy(self):
        self.prepare()
        (self.output / "registry.json").unlink()
        await self.assert_unavailable()