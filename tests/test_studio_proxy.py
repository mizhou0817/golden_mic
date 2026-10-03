"""Bounded RAW preview proxies, independent of removed account/cloud routers.

Router tests use explicit codec CONTRACT DOUBLES, isolated TEMP and ASGITransport
(no sockets, main/settings singletons, credentials or provider calls). Only the
FFmpeg-gated class asserts actual encoding/pixels/audio, with sources <=2 seconds.
"""
from __future__ import annotations

import array
import asyncio
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend import studio, studio_proxy as proxy
from backend.task_operations import _COPYING
from backend.media import MediaProcessingError
from backend.operations import InsufficientDiskSpaceError
from backend.storage import write_json_atomic
from backend.studio_render import Clip, ExportOptions, Project, RenderError, Track, input_args, probe, render_project
from tests.studio_publication_fixtures import seed_synthetic_publication, synthetic_publication_files


def source_info(*, duration: float = 1, audio: bool = True) -> dict[str, Any]:
    return {"duration": duration, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 160, "height": 90, "avg_frame_rate": "24/1"},
        *([{"codec_type": "audio", "codec_name": "aac"}] if audio else []),
    ]}


def restore_mtime(path: Path, content: bytes) -> None:
    before = path.stat()
    path.write_bytes(content)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def without_ctime(info: os.stat_result) -> list[int]:
    """Deliberately hide ctime to prove hashes, not Linux ctime, catch tampering."""
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, 0]


class ProxyModelTests(unittest.TestCase):
    def test_only_strict_revision_and_source_id_no_user_export_or_edit_controls(self):
        body = {"expected_revision": 0, "source_id": "final"}
        self.assertEqual(studio.ProxyRequest.model_validate(body).model_dump(), body)
        for extra in ({"options": {}}, {"options": {"resolution": 1080}}, {"trim": 1}, {"duration": 1},
                      {"project": {}}, {"kind": "render"}, {"profile": {}}, {"subtitles": "none"}, {"mute": True}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                studio.ProxyRequest.model_validate({**body, **extra})
        for revision in (True, "0", 0.0, -1, None):
            with self.subTest(revision=revision), self.assertRaises(ValidationError):
                studio.ProxyRequest.model_validate({**body, "expected_revision": revision})
        for source in ("../final.mp4", "https://host/video", "C:\\private.mp4", "", "a" * 65, 1, None):
            with self.subTest(source=source), self.assertRaises(ValidationError):
                studio.ProxyRequest.model_validate({**body, "source_id": source})

    def test_profile_exact_single_whole_raw_source_no_timeline_controls(self):
        options = proxy.options()
        self.assertEqual(options.dimensions, (640, 360))
        self.assertEqual((options.fps, options.video_bitrate_kbps, options.audio_bitrate_kbps), (30, 1000, 128))
        self.assertEqual((options.format, options.subtitles), ("mp4", "standard"))
        for audio in (True, False):
            project = proxy.project("final", 1.75, audio)
            self.assertEqual(project.duration, 1.75)
            self.assertEqual(len(project.tracks), 1)
            clip = project.tracks[0].clips[0]
            self.assertEqual(clip.model_dump(), Clip(id="proxy_source", source_id="final", duration=1.75,
                                                   fit="contain", trim=0, mute=not audio).model_dump())
            self.assertEqual((project.assets, project.markers, project.workspace.source_marks), ([], [], []))
        caps = studio.capabilities()
        tool = next(item for item in caps["tools"] if item["id"] == "proxy")
        self.assertTrue(tool["available"])
        self.assertEqual(tool["classification"], "partial")
        self.assertIn("no automatic batch", tool["reason"])
        self.assertEqual([caps["limits"][key] for key in ("proxy_duration_seconds", "proxy_width", "proxy_height", "proxy_fps")],
                         [120, 640, 360, 30])

    def test_content_profile_pipeline_disclosure_version_are_all_cache_inputs(self):
        first = proxy.cache_key("a" * 64, 0, False)
        self.assertRegex(first, r"^[a-f0-9]{64}$")
        self.assertEqual(first, proxy.cache_key("a" * 64, 0, False))
        self.assertNotEqual(first, proxy.cache_key("b" * 64, 0, False))
        self.assertNotEqual(first, proxy.cache_key("a" * 64, 1, False))
        self.assertNotEqual(first, proxy.cache_key("a" * 64, 0, True))
        with patch.object(proxy, "DISCLOSURE_VERSION", "future-version"):
            self.assertNotEqual(first, proxy.cache_key("a" * 64, 0, False))
        with patch.object(proxy, "options", return_value=ExportOptions(resolution=360, video_bitrate_kbps=1100)):
            self.assertNotEqual(first, proxy.cache_key("a" * 64, 0, False))

    def test_sources_are_videos_not_cover_art_images_or_truncated_long_movies(self):
        self.assertEqual(proxy.source_facts(source_info(duration=120)), (120, True))
        for duration in (0, -1, 120.001, 3600, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(RenderError):
                proxy.source_facts(source_info(duration=duration))
        for info in ({"duration": 1, "streams": [{"codec_type": "audio"}]},
                     {**source_info(), "is_image": True},
                     {"duration": 1, "streams": [{"codec_type": "video", "disposition": {"attached_pic": 1}}]}):
            with self.assertRaises(RenderError):
                proxy.source_facts(info)
        args = input_args(Path("source.mp4"))
        self.assertEqual(args[args.index("-f") + 1], "mov")
        self.assertEqual(args[args.index("-protocol_whitelist") + 1], "file,pipe")


class ProxyHashTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_in_at_most_1mib_chunks_without_read_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "source.mp4"
            data = b"bounded-content" * 180000
            path.write_bytes(data)
            reads: list[int] = []
            real_open = Path.open

            class Reader:
                def __init__(self, stream: Any):
                    self.stream = stream
                def __enter__(self):
                    return self
                def __exit__(self, *args: Any):
                    self.stream.close()
                def fileno(self):
                    return self.stream.fileno()
                def read(self, count: int):
                    reads.append(count)
                    self.assert_bounded(count)
                    return self.stream.read(count)
                @staticmethod
                def assert_bounded(count: int):
                    if not 0 < count <= 1024 * 1024:
                        raise AssertionError("unbounded file read")

            def opened(value: Path, *args: Any, **kwargs: Any):
                stream = real_open(value, *args, **kwargs)
                return Reader(stream) if value == path else stream

            with patch.object(Path, "open", new=opened), patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
                actual = await proxy.hash_file(root, path)
            self.assertEqual(actual.sha256, hashlib.sha256(data).hexdigest())
            self.assertGreater(len(reads), 1)
            self.assertEqual(actual.fingerprint[2], len(data))

    async def test_existing_probe_guards_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"probe contract only")
            for changes in ({"color_transfer": "smpte2084"}, {"color_primaries": "bt2020"},
                            {"width": 5000}, {"avg_frame_rate": "121/1"}):
                info = source_info()
                info["streams"][0].update(changes)
                info["format"] = {"duration": "1"}
                with patch("backend.studio_render.run_logged_command", new=AsyncMock(return_value=json.dumps(info).encode())) as command:
                    with self.assertRaises(RenderError):
                        await probe(source, root)
                    args = command.call_args.args[0]
                    self.assertEqual(args[args.index("-f") + 1], "mov")


class ProxyAPIHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "tasks"
        self.root = self.data / "task"
        self.root.mkdir(parents=True)
        self.originals = {"final.mp4": b"contract-only source; NOT a codec fixture", "script.txt": b"original text",
                          **synthetic_publication_files(blocked=True)}
        for name, data in self.originals.items():
            (self.root / name).write_bytes(data)
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, uploads=[], status="done", revision=0, background=None)
        self.records = {"task": self.record}
        self.manager = SimpleNamespace(_draining=False, get=self.records.get)
        self.settings = SimpleNamespace(data_dir=self.data, media_command_timeout_seconds=60, minimum_free_disk_bytes=0,
                                        shutdown_grace_seconds=0.001)
        self.calls: list[tuple[str, bool]] = []
        self.denied = False
        self.copying = False
        self.auth_hook: Any = None

        async def authorize(request: Any, task_id: str, write: bool = False):
            self.calls.append((request.url.path, write))
            if self.denied or task_id != "task" or request.headers.get("X-Token") != "private":
                raise HTTPException(403, "private task required")
            if write and request.headers.get("X-CSRF") != "csrf":
                raise HTTPException(403, "CSRF")
            if write and self.copying:
                raise HTTPException(409, "copy active")
            # Core authorization does not emulate removed account approval.
            # The real Studio router below must enforce QC/export boundaries.
            if self.auth_hook:
                self.auth_hook()
            return self.record

        self.authorize = authorize
        self.router = studio.create_studio_router(self.settings, self.manager, authorize)
        self.app = FastAPI()
        self.app.include_router(self.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                       base_url="http://test", headers={"X-Token": "private", "X-CSRF": "csrf"})
        self.prefix = "/api/tasks/task/studio"

    async def asyncTearDown(self):
        tasks = [task for key, task in studio._RUNNING.items() if Path(key).is_relative_to(self.root)]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
        self.assertFalse(any(Path(key).is_relative_to(self.root) for key in studio._RUNNING))
        self.assertNotIn(str(self.root), studio._BUSY)
        self.assertNotIn(str(self.root), studio._DISK_RESERVATIONS)
        self.assertNotIn(str(self.root), studio._INGESTING)
        for name, data in self.originals.items():
            self.assertEqual((self.root / name).read_bytes(), data, name)
        self.temp.cleanup()

    async def post(self, source: str = "final", revision: int = 0, **extra: Any):
        return await self.client.post(self.prefix + "/proxies", json={"source_id": source, "expected_revision": revision, **extra})

    async def finish(self, response: httpx.Response) -> dict[str, Any]:
        self.assertEqual(response.status_code, 202, response.text)
        task = studio._RUNNING.get(str(self.job_path(response.json()["id"])))
        if task is not None:
            await task
        job = await self.client.get(self.prefix + "/jobs/" + response.json()["id"])
        self.assertEqual(job.status_code, 200, job.text)
        return job.json()

    def job_path(self, job_id: str) -> Path:
        return self.root / "studio/jobs" / (job_id + ".json")

    def work(self, job: dict[str, Any]) -> Path:
        return self.root / "studio/outputs" / f"r{job['revision']}" / job["id"]

    def media_url(self, job: dict[str, Any]) -> str:
        return self.prefix + "/proxies/" + job["id"] + "/media"


class ProxyAPITests(ProxyAPIHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tools_patch = patch("backend.studio.shutil.which", return_value="contract-only-not-an-executable")
        self.tools_patch.start()
        self.addCleanup(self.tools_patch.stop)
        self.probe_patch = patch("backend.studio.probe", new=AsyncMock(return_value=source_info()))
        self.prober = self.probe_patch.start()
        self.addCleanup(self.probe_patch.stop)
        self.encoder_patch = patch("backend.studio.render_project", new=AsyncMock(side_effect=self.contract_render))
        self.encoder = self.encoder_patch.start()
        self.addCleanup(self.encoder_patch.stop)

    async def contract_render(self, project: Project, options: ExportOptions, sources: dict[str, Path], work: Path,
                              *, disclosure: bool = False, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        self.assertEqual(set(sources), {project.tracks[0].clips[0].source_id})
        output = b"CONTRACT DOUBLE ONLY -- not encoded media" * 8
        (work / "output.mp4").write_bytes(output)
        return {"file": "output.mp4", "bytes": len(output), "duration": project.duration,
                "applied": options.model_dump(), "disclosure": disclosure, "probe": {
                    "streams": [{"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360,
                                 "avg_frame_rate": "30/1", "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"},
                                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2}],
                    "format": {"duration": str(project.duration)}}}

    async def success(self, **kwargs: Any) -> dict[str, Any]:
        job = await self.finish(await self.post(**kwargs))
        self.assertEqual(job["state"], "succeeded", job)
        return job

    async def test_contract_raw_only_no_project_revision_catalog_or_pipeline_mutation(self):
        original_catalog = studio.catalog(self.record)
        edited = Project(tracks=[Track(id="v", type="video", clips=[Clip(id="c", source_id="final", duration=0.5,
                          reverse=True, mute=True, brightness=0.5, fit="cover")])])
        saved = await self.client.post(self.prefix + "/project", json={"expected_revision": 0, "project": edited.model_dump()})
        self.assertEqual(saved.status_code, 200, saved.text)
        before = studio.public_state(studio.read_state(self.root))
        job = await self.success(revision=1)
        self.assertEqual(job["kind"], "proxy")
        self.assertEqual(job["source_id"], "final")
        self.assertRegex(job["cache_key"], r"^[a-f0-9]{64}$")
        self.assertEqual(job["options"], proxy.options().model_dump())
        self.assertEqual(job["pipeline_revision"], 0)
        self.assertEqual(job["revision"], 1)
        self.assertEqual(studio.public_state(studio.read_state(self.root)), before)
        self.assertEqual(studio.catalog(self.record), original_catalog)
        self.assertEqual((self.record.revision, self.record.status, self.record.background), (0, "done", None))
        project, _, sources, _ = self.encoder.call_args.args
        self.assertEqual(project.model_dump(), proxy.project("final", 1, True).model_dump())
        self.assertEqual(sources, {"final": self.root / "final.mp4"})
        listed = await self.client.get(self.prefix + "/proxies")
        self.assertEqual(listed.json(), {"proxies": [job]})
        self.assertEqual((await self.client.get(self.prefix + "/sources/" + job["id"])).status_code, 404)
        snapshot = json.loads((self.work(job) / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["proxy"]["source_sha256"], hashlib.sha256(self.originals["final.mp4"]).hexdigest())
        self.assertNotIn(str(self.root), json.dumps(job))
        self.assertEqual([a["op"] for a in studio.read_state(self.root)["audit"]], ["project.save", "job.submit", "job.succeeded"])

    async def test_private_and_csrf_delegate_before_body_or_job_lookup(self):
        for method, suffix in (("POST", "/proxies"), ("GET", "/proxies"), ("GET", "/proxies/" + "a" * 32 + "/media")):
            response = await self.client.request(method, self.prefix + suffix, headers={"X-Token": "bad"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.calls[-1][1], method == "POST")
        called = False
        async def body():
            nonlocal called
            called = True
            yield b'{"source_id":"final","expected_revision":0}'
        response = await self.client.post(self.prefix + "/proxies", content=body(), headers={"X-CSRF": "bad"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(called)
        self.prober.assert_not_awaited()
        self.encoder.assert_not_awaited()
        self.assertFalse((self.root / "studio").exists())

    async def test_qc_and_range_are_private_raw_only_never_edited_export_bypass(self):
        job = await self.success()  # Raw proxies do not clear actual pipeline QC.
        response = await self.client.get(self.media_url(job), headers={"Range": "bytes=3-18"})
        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, (self.work(job) / "output.mp4").read_bytes()[3:19])
        self.assertEqual(response.headers["content-range"], f"bytes 3-18/{job['result']['bytes']}")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual((await self.client.get(self.media_url(job), headers={"X-Token": "bad"})).status_code, 403)
        blocked = await self.client.post(self.prefix + "/export", json={"expected_revision": 0})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("QC blockers", blocked.json()["detail"])
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job["id"])).status_code, 409)
        original = self.job_path(job["id"]).read_bytes()
        for kind in ("render", "export"):
            changed = {**job, "kind": kind}
            self.job_path(job["id"]).write_text(json.dumps(changed), encoding="utf-8")
            self.assertEqual((await self.client.get(self.media_url(job))).status_code, 404)
        self.job_path(job["id"]).write_bytes(original)
        snapshot_path = self.work(job) / "snapshot.json"
        snapshot = snapshot_path.read_bytes()
        for field, value in (("trim", 0.25), ("brightness", 0.5), ("mute", True), ("fit", "cover")):
            changed = json.loads(snapshot)
            changed["project"]["tracks"][0]["clips"][0][field] = value
            snapshot_path.write_text(json.dumps(changed), encoding="utf-8")
            response = await self.client.get(self.media_url(job), headers={"Range": "bytes=0-31"})
            self.assertEqual(response.status_code, 409, response.text)
        snapshot_path.write_bytes(snapshot)

    async def test_unknown_image_audio_path_and_extra_options_are_not_proxy_inputs(self):
        (self.root / "narration.m4a").write_bytes(b"audio only")
        for source in ("narration", "image_" + "a" * 24, "lut_" + "a" * 24, "missing", "../final.mp4", "https://host/file"):
            self.assertEqual((await self.post(source=source)).status_code, 422)
        for extra in ({"options": {}}, {"project": {}}, {"trim": 0}, {"duration": 120}):
            self.assertEqual((await self.post(**extra)).status_code, 422)
        self.prober.assert_not_awaited()
        for info in (source_info(duration=120.1), {"duration": 1, "streams": [{"codec_type": "audio"}]},
                     {**source_info(), "is_image": True}):
            self.prober.return_value = info
            response = await self.post()
            self.assertEqual(response.status_code, 422, response.text)
        self.encoder.assert_not_awaited()
        self.assertFalse(list(self.root.glob("studio/outputs/*/*/output.*")))
        self.assertFalse(list(self.root.glob("studio/jobs/*.json")))

    async def test_probe_and_actual_failed_job_errors_never_expose_paths(self):
        self.prober.side_effect = MediaProcessingError("decoder rejected " + str(self.root / "private.mp4"))
        rejected = await self.post()
        self.assertEqual(rejected.status_code, 422, rejected.text)
        self.assertNotIn(str(self.root), rejected.text)
        self.prober.side_effect = None
        self.encoder.side_effect = RenderError("decoder command " + str(self.root / "private.mp4"))
        failed = await self.finish(await self.post())
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["source_id"], "final")
        self.assertNotIn(str(self.root), json.dumps(failed))
        self.assertFalse(self.work(failed).exists())

    async def test_original_and_normalized_catalog_ids_only_not_a_studio_output_upload(self):
        raw = self.root / "raw/accepted.mp4"
        raw.parent.mkdir()
        raw.write_bytes(b"accepted original")
        norm = self.root / "norm/norm_1.mp4"
        norm.parent.mkdir()
        norm.write_bytes(b"accepted normalized")
        self.record.uploads = [SimpleNamespace(path=raw)]
        ids = {path: key for key, path in studio.catalog(self.record).items()}
        for path in (raw, norm):
            job = await self.success(source=ids[path])
            self.assertEqual(job["source_id"], ids[path])
            self.assertEqual(self.encoder.call_args.args[2], {ids[path]: path})
        derived = self.root / "studio/outputs/forbidden.mp4"
        derived.write_bytes(b"edited output must not become a raw proxy")
        self.record.uploads.append(SimpleNamespace(path=derived))
        bad_id = next(key for key, path in studio.catalog(self.record).items() if path == derived)
        self.assertEqual((await self.post(source=bad_id)).status_code, 422)

    async def test_busy_legacy_copy_pipeline_and_global_limits_before_hash_or_probe(self):
        with patch.object(proxy, "hash_file", new=AsyncMock()) as hashing:
            studio._BUSY.add(str(self.root))
            try:
                self.assertEqual((await self.post()).status_code, 409)
            finally:
                studio._BUSY.discard(str(self.root))
            studio._BUSY.update({"proxy-test-other-1", "proxy-test-other-2"})
            try:
                self.assertEqual((await self.post()).status_code, 429)
            finally:
                studio._BUSY.difference_update({"proxy-test-other-1", "proxy-test-other-2"})
            with patch("backend.studio.legacy_task_busy", return_value=True):
                self.assertEqual((await self.post()).status_code, 409)
            owner = asyncio.current_task()
            assert owner is not None
            _COPYING[str(self.root)] = owner
            try:
                self.assertEqual((await self.post()).status_code, 409)
            finally:
                _COPYING.pop(str(self.root), None)
            self.record.status = "running"
            self.assertEqual((await self.post()).status_code, 409)
            self.record.status = "done"
            self.record.background = asyncio.create_task(asyncio.Event().wait())
            self.assertEqual((await self.post()).status_code, 409)
            self.record.background.cancel()
            await asyncio.gather(self.record.background, return_exceptions=True)
            self.record.background = None
            self.manager._draining = True
            self.assertEqual((await self.post()).status_code, 503)
            self.manager._draining = False
            hashing.assert_not_awaited()
        self.prober.assert_not_awaited()

    async def test_cache_uses_same_job_even_at_twenty_and_no_new_encoder_or_audit(self):
        job = await self.success()
        before_state = (self.root / "studio/state.json").read_bytes()
        for i in range(19):
            write_json_atomic(self.job_path(f"{i:032x}"), {"id": f"{i:032x}", "kind": "render", "state": "failed"})
        self.assertEqual(len(list((self.root / "studio/jobs").glob("*.json"))), 20)
        second = await self.post()
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), {**job, "cached": True})
        self.assertEqual(self.encoder.await_count, 1)
        self.assertEqual(self.prober.await_count, 1)
        self.assertEqual((self.root / "studio/state.json").read_bytes(), before_state)
        self.assertEqual((await self.client.get(self.prefix + "/proxies")).json(), {"proxies": [job]})
        self.record.revision = 1
        self.assertEqual((await self.post()).status_code, 429)
        self.record.revision = 0
        self.assertEqual(self.encoder.await_count, 1)

    async def test_cache_project_revision_is_checked_but_is_not_a_raw_cache_key(self):
        job = await self.success()
        response = await self.client.post(self.prefix + "/project", json={"expected_revision": 0, "project": {"name": "new edit"}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.post()).status_code, 409)
        cached = await self.post(revision=1)
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertEqual(cached.json(), {**job, "cached": True})
        self.assertEqual(self.encoder.await_count, 1)
        self.assertEqual((await self.client.get(self.media_url(job))).status_code, 200)

    async def test_cache_hit_reauthorizes_even_after_awaited_disk_lease_release(self):
        await self.success()
        @asynccontextmanager
        async def reserved(amount: int):
            try:
                yield SimpleNamespace(reserved_bytes=amount)
            finally:
                await asyncio.sleep(0)
                self.record.revision = 1
        self.manager._upload_capacity_guard = SimpleNamespace(reserve=reserved)
        try:
            response = await self.post()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.encoder.await_count, 1)
        finally:
            self.record.revision = 0

    async def test_edited_and_simple_exports_keep_their_existing_original_source_semantics(self):
        await self.success()
        quality = self.root / "quality_report.json"
        report = self.root / "report.json"
        try:
            seed_synthetic_publication(self, self.record)
            project = Project(tracks=[Track(id="v", type="video", clips=[
                Clip(id="c", source_id="final", trim=0.25, duration=0.5, brightness=0.5)])])
            saved = await self.client.post(self.prefix + "/project", json={"expected_revision": 0, "project": project.model_dump()})
            self.assertEqual(saved.status_code, 200, saved.text)
            for route, kind in (("/render", "render"), ("/export", "export")):
                response = await self.client.post(self.prefix + route, json={"expected_revision": 1, "options": {"resolution": 720}})
                job = await self.finish(response)
                self.assertEqual(job["state"], "succeeded", job)
                self.assertEqual(job["kind"], kind)
                applied, options, sources, _ = self.encoder.call_args.args
                self.assertEqual(sources, {"final": self.root / "final.mp4"})
                self.assertEqual(options.resolution, 720)
                self.assertNotIn("source_id", job)
                if kind == "render":
                    self.assertEqual(applied.model_dump(), project.model_dump())
                else:
                    self.assertEqual(applied.tracks[0].clips[0].fit, "cover")
                    self.assertEqual(job["export_semantics"]["picture"], "finished_final")
                self.assertEqual((await self.client.get(self.media_url(job))).status_code, 404)
        finally:
            quality.write_bytes(self.originals["quality_report.json"])
            report.write_bytes(self.originals["report.json"])

    async def test_corrupt_profile_manifest_and_source_binding_never_serve_or_reuse_output(self):
        job = await self.success()
        snapshot_path = self.work(job) / "snapshot.json"
        manifest_path = self.work(job) / "manifest.json"
        snapshot_bytes, manifest_bytes = snapshot_path.read_bytes(), manifest_path.read_bytes()
        snapshot = json.loads(snapshot_bytes)
        for changed in ("profile", "source_path", "source_hash", "pipeline", "extra_track"):
            document = copy.deepcopy(snapshot)
            if changed == "profile":
                document["proxy"]["profile"]["options"]["resolution"] = 1080
            elif changed == "source_path":
                document["proxy"]["source_path"] = "studio/outputs/edited.mp4"
            elif changed == "source_hash":
                document["proxy"]["source_sha256"] = "0" * 64
            elif changed == "pipeline":
                document["proxy"]["pipeline_revision"] = "0"
            else:
                document["project"]["tracks"].append(Track(id="text", type="text", clips=[Clip(id="t", duration=1, text="edited")]).model_dump())
            snapshot_path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual((await self.client.get(self.media_url(job))).status_code, 409, changed)
        snapshot_path.write_bytes(snapshot_bytes)
        manifest = json.loads(manifest_bytes)
        manifest["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual((await self.client.get(self.media_url(job))).status_code, 409)
        replacement = await self.success()
        self.assertNotEqual(replacement["id"], job["id"])
        self.assertEqual(self.encoder.await_count, 2)

    async def test_cache_and_media_verify_output_hash_even_if_all_fingerprints_match(self):
        with patch.object(proxy, "_stamp", side_effect=without_ctime):
            job = await self.success()
            output = self.work(job) / "output.mp4"
            original_stamp = proxy.file_stamp(self.root, output)
            damaged = b"X" + output.read_bytes()[1:]
            restore_mtime(output, damaged)
            self.assertEqual(proxy.file_stamp(self.root, output), original_stamp)
            with patch.object(proxy, "VerifiedFileResponse") as response:
                rejected = await self.client.get(self.media_url(job), headers={"Range": "bytes=0-7"})
                self.assertEqual(rejected.status_code, 409, rejected.text)
                response.assert_not_called()
            replacement = await self.success()
            self.assertNotEqual(replacement["id"], job["id"])
            self.assertEqual(replacement["cache_key"], job["cache_key"])
            self.assertEqual(self.encoder.await_count, 2)

    async def test_finish_hash_detects_source_mutation_with_restored_size_and_mtime(self):
        source = self.root / "final.mp4"
        original = source.read_bytes()
        async def changed(*args: Any, **kwargs: Any):
            result = await self.contract_render(*args, **kwargs)
            restore_mtime(source, b"X" + original[1:])
            return result
        try:
            with patch.object(proxy, "_stamp", side_effect=without_ctime):
                self.encoder.side_effect = changed
                job = await self.finish(await self.post())
            self.assertEqual(job["state"], "failed")
            self.assertEqual(job["source_id"], "final")
            self.assertFalse(self.work(job).exists())
            self.assertNotIn(str(self.root), json.dumps(job))
        finally:
            restore_mtime(source, original)

    async def test_media_source_hash_pipeline_status_and_read_authorization_are_current(self):
        source = self.root / "final.mp4"
        original = source.read_bytes()
        try:
            with patch.object(proxy, "_stamp", side_effect=without_ctime):
                job = await self.success()
                restore_mtime(source, b"X" + original[1:])
                self.assertEqual((await self.client.get(self.media_url(job))).status_code, 409)
                restore_mtime(source, original)
                for field, value in (("revision", 1), ("status", "running")):
                    old = getattr(self.record, field)
                    setattr(self.record, field, value)
                    self.assertEqual((await self.client.get(self.media_url(job), headers={"Range": "bytes=0-9"})).status_code, 409)
                    setattr(self.record, field, old)
                real_hash = proxy.hash_file
                async def revoke(root: Path, path: Path, **kwargs: Any):
                    digest = await real_hash(root, path, **kwargs)
                    if path.name == "output.mp4":
                        self.denied = True
                    return digest
                with patch.object(proxy, "hash_file", side_effect=revoke):
                    self.assertEqual((await self.client.get(self.media_url(job))).status_code, 403)
                self.denied = False
        finally:
            restore_mtime(source, original)

    async def test_pending_duplicate_cancel_and_asset_import_share_one_slot(self):
        entered = asyncio.Event()
        async def blocked(*args: Any, **kwargs: Any):
            work = args[3]
            (work / "partial.mp4").write_bytes(b"partial")
            entered.set()
            await asyncio.Event().wait()
        self.encoder.side_effect = blocked
        first = await self.post()
        self.assertEqual(first.status_code, 202, first.text)
        await asyncio.wait_for(entered.wait(), 5)
        self.assertTrue(studio.studio_task_busy(self.root))
        self.assertEqual((await self.post()).status_code, 409)
        self.assertEqual((await self.client.post(self.prefix + "/assets/lut?expected_revision=0", content=b"not read",
                         headers={"Content-Type": "text/plain"})).status_code, 409)
        listed = (await self.client.get(self.prefix + "/proxies")).json()["proxies"]
        self.assertEqual(len(listed), 1)
        cancelled = await self.client.delete(self.prefix + "/jobs/" + first.json()["id"])
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertEqual(cancelled.json()["source_id"], "final")
        self.assertFalse(self.work(first.json()).exists())
        self.assertEqual(self.encoder.await_count, 1)

    async def test_same_raw_snapshot_survives_allowed_project_edits_after_admission(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args: Any, **kwargs: Any):
            entered.set()
            await release.wait()
            return await self.contract_render(*args, **kwargs)
        self.encoder.side_effect = held
        first = await self.post()
        try:
            await asyncio.wait_for(entered.wait(), 5)
            saved = await self.client.post(self.prefix + "/project", json={"expected_revision": 0, "project": {"name": "later edit"}})
            self.assertEqual(saved.status_code, 200, saved.text)
        finally:
            release.set()
        job = await self.finish(first)
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["revision"], 0)
        self.assertEqual(studio.read_state(self.root)["revision"], 1)
        self.assertEqual((await self.client.get(self.media_url(job))).status_code, 200)

    async def test_preexisting_asset_ingest_guard_also_rejects_proxy(self):
        owner = asyncio.create_task(asyncio.Event().wait())
        studio._INGESTING[str(self.root)] = owner
        studio._BUSY.add(str(self.root))
        try:
            response = await self.post()
            self.assertEqual(response.status_code, 409, response.text)
            self.prober.assert_not_awaited()
            self.encoder.assert_not_awaited()
        finally:
            studio._INGESTING.pop(str(self.root), None)
            studio._BUSY.discard(str(self.root))
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)

    async def test_cancelled_hash_worker_drains_before_busy_and_reservations_release(self):
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = proxy._hash_file
        def held(*args: Any):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError("hash fixture not released")
            return original(*args)
        with patch.object(proxy, "_hash_file", side_effect=held):
            request = asyncio.create_task(self.post())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                self.assertFalse(request.done())
                self.assertTrue(studio.studio_task_busy(self.root))
                self.assertIn(str(self.root), studio._DISK_RESERVATIONS)
                self.assertTrue(studio.active_studio_tasks())
                self.assertEqual((await self.post()).status_code, 409)
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await request
        self.prober.assert_not_awaited()
        self.encoder.assert_not_awaited()

    async def test_repeated_delete_cancellation_cannot_interrupt_renderer_cleanup(self):
        entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def held(*args: Any, **kwargs: Any):
            (args[3] / "partial.mp4").write_bytes(b"partial")
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await release.wait()
                raise
        self.encoder.side_effect = held
        first = await self.post()
        await asyncio.wait_for(entered.wait(), 5)
        deleting = asyncio.create_task(self.client.delete(self.prefix + "/jobs/" + first.json()["id"]))
        try:
            await asyncio.wait_for(cancelling.wait(), 5)
            deleting.cancel()
            await asyncio.sleep(0)
            deleting.cancel()
            await asyncio.sleep(0)
            self.assertFalse(deleting.done())
            self.assertTrue(studio.studio_task_busy(self.root))
            self.assertTrue(self.work(first.json()).exists())
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await deleting
        job = (await self.client.get(self.prefix + "/jobs/" + first.json()["id"])).json()
        self.assertEqual(job["state"], "cancelled")
        self.assertFalse(self.work(job).exists())

    async def test_restart_get_only_marks_interrupted_cleans_and_never_resubmits(self):
        job_id = "a" * 32
        job = {"id": job_id, "revision": 0, "state": "running", "kind": "proxy", "source_id": "final", "cache_key": "b" * 64}
        write_json_atomic(self.job_path(job_id), job)
        work = self.work(job)
        work.mkdir(parents=True)
        (work / "partial.mp4").write_bytes(b"partial")
        app = FastAPI()
        app.include_router(studio.create_studio_router(self.settings, self.manager, self.authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=self.client.headers) as client:
            response = await client.get(self.prefix + "/proxies")
            self.assertEqual(response.status_code, 200, response.text)
            recovered = response.json()["proxies"][0]
            self.assertEqual(recovered["state"], "interrupted")
            self.assertEqual(recovered["source_id"], "final")
            self.assertFalse(work.exists())
            self.assertEqual((await client.get(self.prefix + "/proxies")).json(), response.json())
        self.assertEqual(len(list((self.root / "studio/jobs").glob("*.json"))), 1)
        self.prober.assert_not_awaited()
        self.encoder.assert_not_awaited()

    async def test_revision_session_record_copy_and_metadata_rechecked_after_awaits(self):
        real_hash = proxy.hash_file
        for mutation in ("project", "pipeline", "status", "removed", "copy", "metadata", "session"):
            with self.subTest(mutation=mutation):
                def mutate():
                    if mutation == "project":
                        state = studio.read_state(self.root)
                        state["revision"] = 1
                        studio.write_state(self.root, state)
                    elif mutation == "pipeline":
                        self.record.revision = 1
                    elif mutation == "status":
                        self.record.status = "running"
                    elif mutation == "removed":
                        self.records.pop("task")
                    elif mutation == "copy":
                        owner = asyncio.current_task()
                        assert owner is not None
                        _COPYING[str(self.root)] = owner
                    elif mutation == "metadata":
                        (self.root / "shots_annotated.json").write_text("[]", encoding="utf-8")
                    else:
                        self.denied = True
                async def changed(*args: Any, **kwargs: Any):
                    digest = await real_hash(*args, **kwargs)
                    mutate()
                    return digest
                try:
                    with patch.object(proxy, "hash_file", side_effect=changed):
                        result = await self.post()
                    self.assertEqual(result.status_code, 403 if mutation == "session" else 409, result.text)
                finally:
                    self.record.revision, self.record.status = 0, "done"
                    self.records["task"] = self.record
                    self.denied = False
                    _COPYING.pop(str(self.root), None)
                    (self.root / "shots_annotated.json").unlink(missing_ok=True)
                    (self.root / "studio/state.json").unlink(missing_ok=True)
        self.prober.assert_not_awaited()
        self.encoder.assert_not_awaited()

    async def test_body_probe_and_last_authorization_revision_races(self):
        async def body():
            self.record.revision = 1
            yield b'{"expected_revision":0,"source_id":"final"}'
        response = await self.client.post(self.prefix + "/proxies", content=body())
        self.assertEqual(response.status_code, 409)
        self.record.revision = 0
        async def stale_probe(*args: Any, **kwargs: Any):
            state = studio.read_state(self.root)
            state["revision"] = 1
            studio.write_state(self.root, state)
            return source_info()
        self.prober.side_effect = stale_probe
        self.assertEqual((await self.post()).status_code, 409)
        (self.root / "studio/state.json").unlink()
        self.prober.side_effect = None
        initial_calls = len(self.calls)
        def final_auth():
            if len(self.calls) - initial_calls == 5:
                self.record.revision = 1
        self.auth_hook = final_auth
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.encoder.assert_not_awaited()
        self.record.revision = 0
        self.auth_hook = None
        job = (await self.client.get(self.prefix + "/proxies")).json()["proxies"][0]
        self.assertEqual(job["state"], "cancelled")
        self.assertFalse(self.work(job).exists())

    async def test_disk_task_quota_and_host_reservation_are_real_admission_guards(self):
        with patch("backend.studio.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            self.assertEqual((await self.post()).status_code, 507)
        with patch("backend.studio.studio_assets.studio_bytes", return_value=studio.MAX_TASK_BYTES - 1):
            self.assertEqual((await self.post()).status_code, 507)
        self.prober.assert_not_awaited()
        events = []
        @asynccontextmanager
        async def reserved(amount: int):
            self.assertGreaterEqual(amount, studio.MAX_BYTES * 2 + studio.MAX_STATE)
            self.assertTrue(studio.studio_task_busy(self.root))
            self.assertTrue(studio.active_studio_tasks())
            events.append("acquired")
            try:
                yield SimpleNamespace(reserved_bytes=amount)
            finally:
                self.assertTrue(studio.studio_task_busy(self.root))
                events.append("released")
        self.manager._upload_capacity_guard = SimpleNamespace(reserve=reserved)
        await self.success()
        self.assertEqual(events, ["acquired", "released"])
        @asynccontextmanager
        async def denied(_amount: int):
            raise InsufficientDiskSpaceError("private path should not leak")
            yield  # pragma: no cover -- async context manager shape
        self.manager._upload_capacity_guard = SimpleNamespace(reserve=denied)
        response = await self.post()
        self.assertEqual(response.status_code, 507, response.text)
        self.assertNotIn("private path", response.text)

    async def test_low_disk_during_hash_does_not_release_lease_before_thread_drains(self):
        entered, checked, release = asyncio.Event(), asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        low = False
        original = proxy._hash_file
        def hashing(*args: Any):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError("hash fixture was not released")
            return original(*args)
        def disk(_root: Any):
            if low:
                checked.set()
            return SimpleNamespace(free=1 if low else 10**10)
        with patch.object(proxy, "_hash_file", side_effect=hashing), patch.object(proxy, "POLL_SECONDS", 0.001), \
                patch("backend.studio.shutil.disk_usage", side_effect=disk):
            request = asyncio.create_task(self.post())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                low = True
                await asyncio.wait_for(checked.wait(), 5)
                self.assertFalse(request.done())
                self.assertTrue(studio.studio_task_busy(self.root))
            finally:
                release.set()
                response = await request
        self.assertEqual(response.status_code, 507, response.text)
        self.encoder.assert_not_awaited()

    async def test_disk_lease_entry_and_release_are_cancellation_drained(self):
        for phase in ("enter", "release"):
            entered, release = asyncio.Event(), asyncio.Event()
            events: list[str] = []
            @asynccontextmanager
            async def reserved(amount: int):
                if phase == "enter":
                    entered.set()
                    await release.wait()
                events.append("acquired")
                try:
                    yield SimpleNamespace(reserved_bytes=amount)
                finally:
                    if phase == "release":
                        entered.set()
                        await release.wait()
                    events.append("released")
            self.manager._upload_capacity_guard = SimpleNamespace(reserve=reserved)
            self.prober.return_value = source_info(duration=121)  # Release preparation without creating a job.
            request = asyncio.create_task(self.post())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                self.assertTrue(studio.studio_task_busy(self.root))
                self.assertFalse(request.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await request
            self.assertEqual(events, ["acquired", "released"])
            self.assertNotIn(str(self.root), studio._DISK_RESERVATIONS)
        self.encoder.assert_not_awaited()

    async def test_router_lifespan_cancels_owned_proxy_and_cleans_partial_without_retry(self):
        entered = asyncio.Event()
        async def held(*args: Any, **kwargs: Any):
            (args[3] / "partial.mp4").write_bytes(b"partial")
            entered.set()
            await asyncio.Event().wait()
        self.encoder.side_effect = held
        async with self.router.lifespan_context(self.app):
            response = await self.post()
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.wait_for(entered.wait(), 5)
        job = (await self.client.get(self.prefix + "/jobs/" + response.json()["id"])).json()
        self.assertEqual(job["state"], "cancelled")
        self.assertFalse(self.work(job).exists())
        self.assertEqual(self.encoder.await_count, 1)

    async def test_read_holds_lease_until_range_response_and_rechecks_final_state(self):
        job = await self.success()
        entered, release = asyncio.Event(), asyncio.Event()
        original = proxy.VerifiedFileResponse.__call__
        async def held(response: Any, *args: Any):
            entered.set()
            await release.wait()
            return await original(response, *args)
        with patch.object(proxy.VerifiedFileResponse, "__call__", new=held):
            request = asyncio.create_task(self.client.get(self.media_url(job), headers={"Range": "bytes=0-7"}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                self.assertTrue(studio.studio_task_busy(self.root))
                self.assertEqual((await self.post()).status_code, 409)
                self.record.revision = 1
            finally:
                release.set()
                response = await request
                self.record.revision = 0
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn(b"CONTRACT DOUBLE", response.content)

    async def test_disclosure_presence_and_metadata_changes_invalidate_cache_without_qc_gate(self):
        job = await self.success()
        document = {"schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence", "items": [
            {"sentence_id": 0, "beat_id": 0, "shot_id": 0, "mode": "video", "prompt_sha256": "a" * 64,
             "disclosure_text": "AI生成示意画面"}]}
        write_json_atomic(self.root / "generated_media_disclosure.json", document)
        self.assertEqual((await self.client.get(self.media_url(job))).status_code, 409)
        disclosed = await self.success()
        self.assertTrue(disclosed["disclosure"])
        self.assertNotEqual(disclosed["cache_key"], job["cache_key"])
        self.assertTrue(self.encoder.call_args.kwargs["disclosure"])
        manifest = json.loads((self.work(disclosed) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["disclosure_intervals"], [[0, 1]])


def ffmpeg(*args: str) -> bytes:
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1", *args],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45).stdout


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "REAL FFmpeg/ffprobe required")
class ProxyRealMediaTests(ProxyAPIHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        source = self.root / "final.mp4"
        # A portrait source makes wrong cover/crop semantics visible. Both the
        # picture and tone are exactly two seconds; no expensive long-source run.
        ffmpeg("-y", "-f", "lavfi", "-i", "color=red:s=90x160:r=24:d=2",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
               "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "2", str(source))
        self.originals["final.mp4"] = source.read_bytes()

    async def test_real_dimensions_fps_audio_duration_original_hash_cache_and_private_range(self):
        with patch("backend.studio.render_project", wraps=render_project) as encoder:
            job = await self.finish(await self.post())
            self.assertEqual(job["state"], "succeeded", job)
            output = self.work(job) / "output.mp4"
            result = job["result"]
            streams = result["probe"]["streams"]
            video = next(s for s in streams if s["codec_type"] == "video")
            audio = next(s for s in streams if s["codec_type"] == "audio")
            self.assertEqual((video["width"], video["height"], video["codec_name"]), (640, 360, "h264"))
            numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
            self.assertEqual(numerator / denominator, 30)
            self.assertEqual((audio["codec_name"], audio["sample_rate"], audio["channels"]), ("aac", "48000", 2))
            self.assertAlmostEqual(float(result["probe"]["format"]["duration"]), 2, delta=0.15)
            self.assertEqual(result["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertFalse((self.work(job) / "studio.ass").exists())
            frame = ffmpeg("-ss", "0.5", "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
            pixel = lambda x, y: frame[(y * 640 + x) * 3:(y * 640 + x) * 3 + 3]
            self.assertLess(max(pixel(20, 180)), 10, "contain-fit left padding must be black")
            self.assertLess(max(pixel(620, 180)), 10, "contain-fit right padding must be black")
            self.assertGreater(pixel(320, 180)[0], 200, "raw portrait picture must survive")
            pcm = array.array("h", ffmpeg("-i", str(output), "-t", "1", "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"))
            self.assertGreater(sum(abs(x) for x in pcm) / len(pcm), 200, "source tone must not be silently muted")
            cached = await self.post()
            self.assertEqual(cached.status_code, 200, cached.text)
            self.assertEqual(cached.json(), {**job, "cached": True})
            self.assertEqual(encoder.await_count, 1, "cache hit must not run a second encoder")
        response = await self.client.get(self.media_url(job), headers={"Range": "bytes=0-1023"})
        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.content, output.read_bytes()[:1024])
        self.assertEqual((await self.client.get(self.media_url(job), headers={"X-Token": "bad"})).status_code, 403)
        self.record.revision += 1
        self.assertEqual((await self.client.get(self.media_url(job), headers={"Range": "bytes=0-31"})).status_code, 409)
        self.record.revision = 0
        self.assertEqual(hashlib.sha256((self.root / "final.mp4").read_bytes()).hexdigest(),
                         hashlib.sha256(self.originals["final.mp4"]).hexdigest())

    async def test_real_silent_video_and_mandatory_whole_proxy_disclosure(self):
        source = self.root / "norm/norm_silent.mp4"
        source.parent.mkdir()
        ffmpeg("-y", "-i", str(self.root / "final.mp4"), "-an", "-c:v", "copy", str(source))
        source_id = next(key for key, path in studio.catalog(self.record).items() if path == source)
        write_json_atomic(self.root / "generated_media_disclosure.json", {
            "schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence", "items": [
                {"sentence_id": 0, "beat_id": 0, "shot_id": 0, "mode": "video", "prompt_sha256": "a" * 64,
                 "disclosure_text": "AI生成示意画面"}]})
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        job = await self.finish(await self.post(source=source_id))
        self.assertEqual(job["state"], "succeeded", job)
        snapshot = json.loads((self.work(job) / "snapshot.json").read_text(encoding="utf-8"))
        self.assertTrue(snapshot["project"]["tracks"][0]["clips"][0]["mute"])
        self.assertEqual(snapshot["project"]["tracks"][0]["clips"][0]["trim"], 0)
        self.assertTrue(job["disclosure"])
        ass = (self.work(job) / "studio.ass").read_text(encoding="utf-8")
        self.assertIn("AI生成示意画面", ass)
        self.assertIn("Dialogue: 100,0:00:00.00,0:00:02.00", ass)
        pixels = ffmpeg("-ss", "0.5", "-i", str(self.work(job) / "output.mp4"), "-frames:v", "1", "-vf", "crop=200:65:0:0",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertGreater(sum(value > 180 for value in pixels), 100, "burned disclosure must change normally black padding")
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    async def test_real_audio_only_mp4_and_renamed_playlist_rejected_without_renderer(self):
        raw = self.root / "raw"
        raw.mkdir()
        audio = raw / "audio.mp4"
        ffmpeg("-y", "-i", str(self.root / "final.mp4"), "-vn", "-c:a", "copy", str(audio))
        playlist = raw / "playlist.mp4"
        playlist.write_text("#EXTM3U\n/private/not-a-video\n", encoding="utf-8")
        self.record.uploads = [SimpleNamespace(path=audio), SimpleNamespace(path=playlist)]
        ids = {path: key for key, path in studio.catalog(self.record).items()}
        with patch("backend.studio.render_project", wraps=render_project) as encoder:
            for path in (audio, playlist):
                response = await self.post(source=ids[path])
                self.assertEqual(response.status_code, 422, response.text)
                self.assertNotIn(str(self.root), response.text)
            encoder.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()