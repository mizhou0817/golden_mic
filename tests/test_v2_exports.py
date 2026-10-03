"""v2 fixed export tests, separate from legacy Studio assertions."""
from __future__ import annotations

import asyncio
import json
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class BudgetTests(unittest.TestCase):
    def test_revision_and_subtitle_contract(self):
        from backend.studio_render import V2ExportOptions
        for body in ({}, {"expected_revision": None}, {"expected_revision": True},
                     {"expected_revision": -1}, {"expected_revision": "0"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                V2ExportOptions.model_validate(body)
        for alias, canonical in (("std", "standard"), ("big", "large"), ("none", "none")):
            self.assertEqual(V2ExportOptions(expected_revision=2, sub=alias).sub, canonical)

    def test_8000_character_budget_not_artificial_600s_or_128mib(self):
        from backend.studio_render import V2ExportOptions, v2_export_budget, Project, Clip, Track, RenderError
        settings = SimpleNamespace(media_command_timeout_seconds=7200)
        duration = 8000 / 230 * 60 + 200 * .25
        budget = v2_export_budget(duration, V2ExportOptions(expected_revision=0), settings)
        self.assertGreater(duration, 600)
        self.assertGreater(budget.output_bytes, 128 * 1024**2)
        self.assertGreater(budget.reservation_bytes, budget.output_bytes * 2)
        for duration in (3600.1, float("nan"), float("inf"), 0):
            with self.assertRaises(RenderError):
                v2_export_budget(duration, V2ExportOptions(expected_revision=0), settings)
        with self.assertRaises(ValueError):
            Project(tracks=[Track(id="v", type="video", clips=[Clip(id="c", source_id="final", duration=121)])])


class RealExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from tests.test_mode_exports import ModeExportTests
        self.fixture = ModeExportTests()
        await self.fixture.asyncSetUp()
        self.root = self.fixture.root

    async def asyncTearDown(self):
        await self.fixture._cleanup_exports()
        self.fixture.stack.close()
        self.fixture.temp.cleanup()

    async def render(self, fmt, **kwargs):
        from backend.studio_render import V2ExportOptions, v2_export_budget, render_v2_export
        options = V2ExportOptions(expected_revision=0, fmt=fmt, **kwargs)
        budget = v2_export_budget(7., options, SimpleNamespace(media_command_timeout_seconds=90))
        work = self.root / (fmt + str(len(list(self.root.iterdir()))))
        result = await render_v2_export(self.root, work, options, budget)
        return work / result["file"], result

    async def test_real_five_formats_and_canonical_srt_eof_frame(self):
        from tests.test_mode_exports import media
        from backend.studio import mode_export_inputs, mode_export_hashes
        from backend.graphics import validate_mode_subtitle_artifacts
        before = mode_export_hashes(mode_export_inputs(self.root))
        events = validate_mode_subtitle_artifacts(self.root / "subs.ass", self.root / "subtitle_manifest.json")["events"]
        srt, _ = await self.render("srt")
        text = srt.read_text(encoding="utf-8")
        self.assertEqual(text.count(" --> "), len(events))
        self.assertIn("00:00:00,310 --> 00:00:01,130", text)
        png, result = await self.render("png", frame_seconds=7., res="360p")
        self.assertAlmostEqual(result["frame_seconds"], 209 / 30)
        self.assertTrue(png.read_bytes().startswith(b"\x89PNG"))
        gif, result = await self.render("gif", res="360p")
        self.assertEqual(result["duration"], 6)
        raw = media("-i", gif, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(raw), 480 * 270 * 3)
        mp4, result = await self.render("mp4", res="360p")
        self.assertEqual(result["audio"], "finished_mix")
        self.assertGreater(mp4.stat().st_size, 0)
        mp3, result = await self.render("mp3")
        self.assertEqual(result["audio"], "voice_only_no_bgm")
        pcm = media("-i", mp3, "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1")
        import array
        samples = array.array("f", pcm)
        def energy(frequency):
            part = samples[48000:96000]
            return abs(sum(value * complex(math.cos(2*math.pi*frequency*i/48000), math.sin(2*math.pi*frequency*i/48000)) for i, value in enumerate(part)))
        self.assertGreater(energy(440), energy(1600) * 100)
        self.assertEqual(before, mode_export_hashes(mode_export_inputs(self.root)))

    async def test_invalid_measured_duration_and_fps_fail_closed(self):
        from backend import studio_render
        real_command = studio_render.run_logged_command
        for defect in ("nan_duration", "wrong_fps"):
            async def altered(command, *args, **kwargs):
                result = await real_command(command, *args, **kwargs)
                if command[0] == "ffprobe" and str(command[-1]).endswith("output.mp4"):
                    measured = json.loads(result)
                    if defect == "nan_duration":
                        measured["format"]["duration"] = "nan"
                    else:
                        video = next(s for s in measured["streams"] if s["codec_type"] == "video")
                        video["avg_frame_rate"] = "24/1"
                    return json.dumps(measured)
                return result
            with self.subTest(defect=defect), patch.object(studio_render, "run_logged_command", side_effect=altered):
                with self.assertRaises(studio_render.RenderError):
                    await self.render("mp4", res="360p")

    async def test_router_publication_hash_revision_and_range(self):
        import httpx
        from fastapi import FastAPI
        from backend.config import Settings
        from backend.task_manager import TaskRecord
        from backend.v2_editing import create_v2_export_router
        from tests.studio_publication_fixtures import synthetic_publication_files, confirm_synthetic_publication
        from backend import studio
        record = TaskRecord(task_id="a" * 32, task_dir=self.root, script="Headline", uploads=[], status="done", mode="mixed", mode_contract=True)
        for name, content in synthetic_publication_files().items():
            value = json.loads(content)
            if name == "report.json":
                value["task_id"] = record.task_id
            (self.root / name).write_text(json.dumps(value), encoding="utf-8")
        settings = Settings(_env_file=None, app_env="test", data_dir=self.root.parent, min_free_disk_gb=0)
        manager = SimpleNamespace(get=lambda _: record, _draining=False, _upload_capacity_guard=None)
        async def authorize(*args, **kwargs):
            return record
        app = FastAPI()
        app.include_router(create_v2_export_router(settings, manager, authorize))
        prefix = "/api/tasks/" + record.task_id
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # Hard gate is exercised, not mocked.
            gate = await client.post(prefix + "/exports", json={"expected_revision": 0, "fmt": "mp3"})
            self.assertEqual(gate.status_code, 409)
            confirm_synthetic_publication(self, record)
            # Reproduce a path/input failure AFTER the busy slot is owned.
            from fastapi import HTTPException
            with patch.object(studio, "mode_export_inputs", side_effect=HTTPException(422, "invalid input")):
                failed = await client.post(prefix + "/exports", json={"expected_revision": 0, "fmt": "mp3"})
                self.assertEqual(failed.status_code, 422)
            self.assertFalse(studio._BUSY)
            self.assertFalse(studio._PREPARING)
            self.assertFalse(studio._DISK_RESERVATIONS)
            self.assertFalse(studio._RUNNING)
            # Refuse insufficient disk BEFORE any admission or media work.
            with patch("backend.v2_editing.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
                failed = await client.post(prefix + "/exports", json={"expected_revision": 0, "fmt": "mp3"})
                self.assertEqual(failed.status_code, 507)
            self.assertFalse(studio._BUSY)
            for body, expected in (({"fmt": "mp3"}, 422), ({"expected_revision": 1, "fmt": "mp3"}, 409)):
                refused = await client.post(prefix + "/exports", json=body)
                self.assertEqual(refused.status_code, expected, refused.text)
                self.assertFalse(studio._BUSY)
            response = await client.post(prefix + "/exports", json={"expected_revision": 0, "fmt": "mp3", "sub": "std"})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertNotIn("id", response.json())
            identity = response.json()["export_id"]
            await asyncio.gather(*list(studio._RUNNING.values()))
            job = (await client.get(prefix + "/exports/" + identity)).json()
            self.assertEqual(job["state"], "succeeded", job)
            self.assertEqual(job["export_id"], identity)
            self.assertNotIn("id", job)
            for secret in ("source_sha256", "publication", "sha256", "file", "budget"):
                self.assertNotIn('"' + secret + '"', json.dumps(job))
            result = await client.get(prefix + "/exports/" + identity + "/file", headers={"Range": "bytes=0-1023"})
            self.assertEqual(result.status_code, 206, result.text[:100])
            self.assertEqual(len(result.content), 1024)
            record.revision += 1
            self.assertEqual((await client.get(prefix + "/exports/" + identity + "/file")).status_code, 409)
        self.assertFalse(studio._BUSY)
        self.assertFalse(studio._DISK_RESERVATIONS)