"""Synthetic mode exports; real main ASGI + FFmpeg, no live services.

Execute this file for the same confinement as run_core_validation, with fresh
TEMP only. Labels/tones are synthetic evidence, not ASR or factual acceptance.
"""
from __future__ import annotations

import asyncio
import ast
import array
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def media(*args):
    result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-threads", "1", *map(str, args)], capture_output=True, timeout=90)
    if result.returncode:
        raise AssertionError("Synthetic FFmpeg fixture failed: " + result.stderr.decode("utf-8", "replace")[-1500:])
    return result.stdout


def save(root, name, obj):
    (root / name).write_text(json.dumps(obj, ensure_ascii=True), encoding="utf-8")


class ModeExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import backend.studio as studio
        import backend.studio_render as renderer
        from backend.graphics import generate_mode_graphics, generate_mode_subtitles
        from backend.models import EditingPreferences, MatchPlanItem, SentenceTiming, TTSWordTiming
        from backend.production_modes import Speaker, QuoteTake
        self.s, self.r = studio, renderer
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Separate containers, not clear()/update(): never cancel another
        # fixture's workers or discard its reservations. Restore object identity.
        for name in ("_RUNNING", "_INGESTING", "_PREPARING", "_DISK_RESERVATIONS"):
            self.stack.enter_context(patch.object(studio, name, {}))
        self.stack.enter_context(patch.object(studio, "_BUSY", set()))
        self.temp = tempfile.TemporaryDirectory(prefix="mode-export-")
        self.addCleanup(self.temp.cleanup)
        self.addAsyncCleanup(self._cleanup_exports)
        self.root = Path(self.temp.name)
        self.sources = {"final": self.root / "final.mp4", "video_only": self.root / "video_only.mp4"}
        media("-y", "-f", "lavfi", "-i", "color=black:s=640x360:r=30:d=7", "-vf", "drawbox=x=300:y=150:w=20:h=20:c=white:t=fill:enable='between(t,1,2)'", "-c:v", "libx264", "-threads", "1", self.sources["video_only"])
        for index, (frequency, duration) in enumerate(((440, 3), (880, 4))):
            media("-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={duration}", "-ac", "2", "-c:a", "pcm_s16le", self.root / f"unit{index}.wav")
        # Finished soundtrack deliberately contains a different BGM frequency.
        media("-y", "-i", self.sources["video_only"], "-f", "lavfi", "-i", "sine=frequency=1600:sample_rate=48000:duration=7", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", self.sources["final"])
        text = "abcdefghijklmnopqrSTUVWXYZ"
        self.preferences = EditingPreferences(caption_style="big", quote_caption="asr", lower_third=True)
        self.timings = [
            SentenceTiming(sentence_id=0, text=text, audio_path="unit0.wav", duration=3, start=0, end=3, gap_after=0, audio_kind="sync",
                           words=[TTSWordTiming(text=text[:18], start=.31, end=1.13), TTSWordTiming(text=text[18:], start=1.77, end=2.81)]),
            SentenceTiming(sentence_id=1, text="Narration", audio_path="unit1.wav", duration=4, start=3, end=7, gap_after=0,
                           words=[TTSWordTiming(text="Narration", start=.23, end=3.61)]),
        ]
        self.plan = [MatchPlanItem(sentence_id=0, text=text, kind="quote", shot_id=0, confidence=1, candidates=[],
                                   source=QuoteTake(take_id="take", upload_id="u", start=0., end=3., speaker_id="s", asr_text=text, score=1.)),
                     MatchPlanItem(sentence_id=1, text="Narration", shot_id=1, confidence=1, candidates=[])]
        self.speakers = [Speaker(id="s", name="Alice", title="Reporter")]
        self.generate_subtitles, self.generate_graphics = generate_mode_subtitles, generate_mode_graphics
        self.write_contract()

    def write_contract(self):
        self.generate_subtitles(self.root, self.timings, self.plan, self.preferences, title="Headline")
        _, lower = self.generate_graphics(self.root, self.timings, self.plan, self.speakers, self.preferences, title="Headline")
        save(self.root, "timings.json", [x.model_dump(mode="json") for x in self.timings])
        save(self.root, "match_plan.json", [x.model_dump(mode="json") for x in self.plan])
        save(self.root, "script_structure.json", {"title": "Headline"})
        save(self.root, "lower_thirds.json", lower)
        save(self.root, "production_mode.json", {"mode_contract": True, "mode": "mixed", "preferences": self.preferences.model_dump(mode="json"),
            "speakers": [x.model_dump(mode="json") for x in self.speakers], "lower_thirds": lower,
            "audio_cache": {str(i): {"sha256": sha(self.root / f"unit{i}.wav")} for i in range(2)}})

    async def _cleanup_exports(self):
        # Also runs when asyncSetUp fails; registrations remain isolated until
        # all owned work/client cleanup finishes, then ExitStack restores state.
        pending = list({*self.s._RUNNING.values(), *self.s._INGESTING.values(), *self.s._PREPARING.values()})
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if hasattr(self, "client"):
            await self.client.aclose()
        self.assertFalse(self.s._BUSY)
        self.assertFalse(self.s._DISK_RESERVATIONS)
        self.assertFalse(self.s._PREPARING)
        self.assertFalse(self.s._INGESTING)
        self.assertFalse(self.s._RUNNING)

    async def export(self, label, **kwargs):
        fmt = kwargs.get("format", "mp4")
        defaults = {} if fmt == "mp3" else {"resolution": 360}
        if fmt == "mp4":
            defaults["video_bitrate_kbps"] = 1500
        options = self.r.ExportOptions(**(defaults | kwargs))
        work = self.root / label
        work.mkdir()
        overlays = []
        project = await self.s.simple_project(self.root, self.sources, options, work, mode_contract=True, mode_export=overlays)
        result = await self.r.render_project(project, options, self.sources, work, **({"mode_export": overlays[0]} if overlays else {}))
        return work / result["file"], result, overlays

    def pixels(self, path, at, crop):
        return media("-ss", at, "-i", path, "-frames:v", "1", "-vf", "crop=" + crop, "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1")

    async def test_real_subtitle_pixels_word_gaps_title_lower_third_and_clock(self):
        before = self.s.mode_export_hashes(self.s.mode_export_inputs(self.root))
        outputs = {}
        for style in ("standard", "large", "none"):
            output, result, overlay = await self.export(style, subtitles=style)
            outputs[style] = output
            self.assertAlmostEqual(result["duration"], 7, places=2)
            self.assertIn("0:00:02.50,ModeTitle", overlay[0].captions)
            if style != "none":
                self.assertIn("0:00:00.31,0:00:01.13,ModeQuote", overlay[0].captions)
                self.assertIn("0:00:01.77,0:00:02.81,ModeQuote", overlay[0].captions)
            else:
                self.assertNotIn(",ModeQuote,,", overlay[0].captions)
                self.assertNotIn(",ModeNarration,,", overlay[0].captions)
            self.assertGreater(max(self.pixels(output, .8, "260:40:20:260")), 180)  # name
            self.assertLess(max(self.pixels(output, 2.6, "260:40:20:260")), 30)  # name EOF
            self.assertGreater(max(self.pixels(output, 2.4, "320:65:160:0")), 100)
            self.assertLess(max(self.pixels(output, 2.6, "320:65:160:0")), 30)
            self.assertGreater(max(self.pixels(output, 1.5, "20:20:300:150")), 200)
            self.assertLess(max(self.pixels(output, 2.2, "20:20:300:150")), 30)
            self.assertLess(max(self.pixels(output, 1.4, "640:50:0:310")), 30)  # actual word gap
        counts = {k: sum(v > 150 for v in self.pixels(p, .8, "640:60:0:300")) for k, p in outputs.items()}
        self.assertGreater(counts["large"], counts["standard"] * 1.3)
        self.assertGreater(counts["standard"], 100)
        self.assertEqual(counts["none"], 0)
        self.assertEqual(before, self.s.mode_export_hashes(self.s.mode_export_inputs(self.root)))

    async def test_disabled_quote_is_not_reintroduced_but_narration_can_be_enabled(self):
        self.preferences.quote_caption = "none"
        self.preferences.caption_style = "none"
        self.write_contract()
        output, _, overlays = await self.export("enabled", subtitles="standard")
        self.assertNotIn(",ModeQuote,,", overlays[0].captions)
        self.assertIn(",ModeNarration,,", overlays[0].captions)
        self.assertLess(max(self.pixels(output, .8, "640:60:0:300")), 30)
        self.assertGreater(max(self.pixels(output, 4, "640:60:0:300")), 180)

    async def test_mp3_has_both_voice_units_no_bgm_and_lossless_input(self):
        output, result, _ = await self.export("voice", format="mp3")
        with wave.open(str(output.parent / "narration.wav"), "rb") as assembled:
            self.assertEqual(assembled.getnframes(), 7 * 48000)
            pcm = assembled.readframes(assembled.getnframes())
        expected = b""
        for index in range(2):
            with wave.open(str(self.root / f"unit{index}.wav"), "rb") as source:
                expected += source.readframes(source.getnframes())
        self.assertEqual(pcm, expected)
        raw = media("-i", output, "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1")
        samples = array.array("f", raw)
        self.assertEqual(len(samples), 7 * 48000)
        for start, tone in ((1, 440), (4, 880)):
            window = samples[start*48000:(start+1)*48000]
            def amplitude(frequency):
                step = 2 * math.pi * frequency / 48000
                return math.hypot(sum(x * math.cos(step*i) for i, x in enumerate(window)),
                                  sum(x * math.sin(step*i) for i, x in enumerate(window)))
            self.assertGreater(amplitude(tone), 100)
            self.assertGreater(amplitude(tone), amplitude(1600) * 100)
            self.assertGreater(amplitude(tone), amplitude(880 if tone == 440 else 440) * 100)
        self.assertEqual(result["duration"], 7)

    async def test_pcm_authored_gap_is_exact_and_units_are_not_retimed(self):
        self.timings[0].gap_after = .25
        self.timings[1].start = 3.25
        self.timings[1].end = 7.25
        self.write_contract()
        work = self.root / "gaps"
        work.mkdir()
        output, duration = self.s.assemble_mode_voice(self.root, work)
        with wave.open(str(output), "rb") as audio:
            actual = audio.readframes(audio.getnframes())
        units = []
        for index in range(2):
            with wave.open(str(self.root / f"unit{index}.wav"), "rb") as audio:
                units.append(audio.readframes(audio.getnframes()))
        self.assertEqual(actual, units[0] + bytes(12000 * 4) + units[1])
        self.assertEqual(duration, 7.25)

    async def test_portrait_and_square_keep_name_and_captions_visible(self):
        for aspect in ("9:16", "1:1"):
            with self.subTest(aspect=aspect):
                output, result, _ = await self.export("aspect-" + aspect.replace(":", "-"), aspect=aspect)
                stream = next(x for x in result["probe"]["streams"] if x["codec_type"] == "video")
                width, height = stream["width"], stream["height"]
                frame = self.pixels(output, .8, f"{width}:{height}:0:0")
                self.assertGreater(sum(x > 150 for x in frame[int(.86*height)*width:]), 20)
                self.assertGreater(sum(x > 150 for x in frame[int(.73*height)*width:int(.81*height)*width]), 20)

    async def test_pcm_tamper_rejected(self):
        from fastapi import HTTPException
        source = self.root / "unit0.wav"
        source.write_bytes(source.read_bytes()[:-4] + b"xxxx")
        with self.assertRaises(HTTPException) as caught:
            await self.export("badvoice", format="mp3")
        self.assertEqual(caught.exception.status_code, 422)

    async def test_ass_and_rehashed_ass_and_manifest_tamper_rejected(self):
        from fastapi import HTTPException
        ass = self.root / "subs.ass"
        manifest = self.root / "subtitle_manifest.json"
        original_ass, original_manifest = ass.read_bytes(), manifest.read_bytes()
        for tamper in ("raw", "rehash", "events", "policy", "graphics"):
            with self.subTest(tamper=tamper):
                ass.write_bytes(original_ass)
                manifest.write_bytes(original_manifest)
                payload = json.loads(original_manifest)
                if tamper in {"raw", "rehash"}:
                    ass.write_bytes(original_ass.replace(b"abcdefghijklmnopqr", b"{\\p1}m 0 0 l 1 1"))
                    if tamper == "rehash":
                        payload["ass_sha256"] = sha(ass)
                elif tamper == "events":
                    payload["events"][1]["start"] = .52
                elif tamper == "policy":
                    payload["quote_caption"] = "none"
                else:
                    (self.root / "graphics.ass").write_text("unreviewed", encoding="utf-8")
                save(self.root, "subtitle_manifest.json", payload)
                with self.assertRaises(HTTPException) as caught:
                    await self.export("bad-" + tamper)
                self.assertEqual(caught.exception.status_code, 422)
                self.assertFalse((self.root / ("bad-" + tamper) / "output.mp4").exists())

    async def test_long_film_gif_prefix_keeps_full_source_fade_clock(self):
        media("-y", "-f", "lavfi", "-i", "color=black:s=160x90:r=30:d=121", "-c:v", "libx264", "-threads", "1", self.sources["video_only"])
        self.sources["final"].write_bytes(self.sources["video_only"].read_bytes())
        self.timings[1].duration = 118
        self.timings[1].end = 121
        self.preferences.transitions = True
        self.write_contract()
        output, result, overlays = await self.export("longgif", format="gif")
        self.assertEqual(result["duration"], 6)
        self.assertEqual(overlays[0].source_duration, 121)
        self.assertFalse(any(s["codec_type"] == "audio" for s in result["probe"]["streams"]))
        self.assertGreater(max(self.pixels(output, 5.9, "640:60:0:300")), 150)  # no false GIF-tail fade

    async def api(self, confirmed=True):
        import httpx
        from fastapi import FastAPI, HTTPException
        from tests.studio_publication_fixtures import seed_synthetic_publication, synthetic_publication_files
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, uploads=[], status="done", revision=0, mode_contract=True, mode="mixed")
        if confirmed:
            seed_synthetic_publication(self, self.record)
        else:
            for name, content in synthetic_publication_files().items():
                (self.root / name).write_bytes(content)
        async def authorize(request, task_id, write=False):
            if request.headers.get("X-Token") != "synthetic" or task_id != "task":
                raise HTTPException(403, "denied")
            return self.record
        app = FastAPI()
        settings = SimpleNamespace(data_dir=self.root.parent, media_command_timeout_seconds=90, minimum_free_disk_bytes=0)
        manager = SimpleNamespace(_draining=False, get=lambda _: self.record)
        app.include_router(self.s.create_studio_router(settings, manager, authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers={"X-Token": "synthetic"})
        self.prefix = "/api/tasks/task/studio"

    async def submit(self):
        return await self.client.post(self.prefix + "/export", json={"expected_revision": 0, "options": {"format": "mp4", "resolution": 360}})

    async def test_publication_gate_and_private_errors(self):
        await self.api(confirmed=False)
        response = await self.submit()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.s._RUNNING)
        self.assertNotIn(str(self.root), response.text)

    async def test_real_job_immutable_download_and_revision_binding(self):
        await self.api()
        response = await self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.gather(*list(self.s._RUNNING.values()))
        job = (await self.client.get(self.prefix + "/jobs/" + response.json()["id"])).json()
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["export_semantics"]["warnings"], [])
        url = self.prefix + "/outputs/" + job["id"]
        download = await self.client.get(url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(hashlib.sha256(download.content).hexdigest(), job["result"]["sha256"])
        self.assertEqual((await self.client.get(url, headers={"X-Token": "bad"})).status_code, 403)
        path = self.root / "studio/outputs/r0" / job["id"] / "output.mp4"
        path.write_bytes(b"tampered")
        self.assertEqual((await self.client.get(url)).status_code, 409)
        self.record.revision += 1
        self.assertEqual((await self.client.get(url)).status_code, 409)

    async def test_same_size_mtime_source_change_during_render_discards_and_cleans(self):
        await self.api()
        real = self.s.render_project
        async def changed(*args, **kwargs):
            result = await real(*args, **kwargs)
            source = self.root / "unit0.wav"
            stat = source.stat()
            source.write_bytes(source.read_bytes()[:-4] + b"zzzz")
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return result
        with patch.object(self.s, "render_project", new=changed):
            response = await self.submit()
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.gather(*list(self.s._RUNNING.values()))
        job = (await self.client.get(self.prefix + "/jobs/" + response.json()["id"])).json()
        self.assertEqual(job["state"], "failed")
        self.assertIsNone(job["output_id"])
        self.assertNotIn(str(self.root), json.dumps(job))
        self.assertFalse((self.root / "studio/outputs/r0" / job["id"]).exists())

    async def test_cancellation_cleans_job_and_releases_busy(self):
        await self.api()
        entered = asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.s, "render_project", new=held):
            response = await self.submit()
            self.assertEqual(response.status_code, 202, response.text)
            await entered.wait()
            response = await self.client.delete(self.prefix + "/jobs/" + response.json()["id"])
        job = response.json()
        self.assertEqual(job["state"], "cancelled")
        self.assertFalse((self.root / "studio/outputs/r0" / job["id"]).exists())
        self.assertFalse(self.s._BUSY)

    async def test_source_changed_during_preparation_is_not_admitted(self):
        await self.api()
        real = self.s.simple_project
        async def changed(*args, **kwargs):
            project = await real(*args, **kwargs)
            source = self.root / "unit0.wav"
            stat = source.stat()
            source.write_bytes(source.read_bytes()[:-4] + b"xxxx")
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return project
        with patch.object(self.s, "simple_project", new=changed):
            response = await self.submit()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.s._BUSY)
        self.assertFalse(self.s._RUNNING)

    async def test_native_encoder_cancellation_cleans_job(self):
        from backend.media import run_logged_command
        await self.api()
        entered = asyncio.Event()
        processes = []
        original_spawn = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            processes.append(process)
            if "-re" in args:
                entered.set()
            return process
        async def native(project, options, sources, work, **kwargs):
            await run_logged_command(["ffmpeg", "-nostdin", "-y", "-re", "-f", "lavfi", "-i",
                                      "color=black:s=64x64:r=30", "-t", "60", "-c:v", "libx264",
                                      str(work / "output.mp4")], work, "synthetic owned cancellation")
        with patch.object(self.s, "render_project", new=native), patch.object(asyncio, "create_subprocess_exec", new=spawn):
            response = await self.submit()
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.wait_for(entered.wait(), 10)
            cancelled = await self.client.delete(self.prefix + "/jobs/" + response.json()["id"])
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertTrue(processes)
        self.assertTrue(all(p.returncode is not None for p in processes))
        self.assertFalse((self.root / "studio/outputs/r0" / response.json()["id"]).exists())

    async def test_publication_revoked_during_render_discards_output(self):
        await self.api()
        real = self.s.render_project
        async def changed(*args, **kwargs):
            result = await real(*args, **kwargs)
            self.record.revision += 1
            return result
        with patch.object(self.s, "render_project", new=changed):
            response = await self.submit()
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.gather(*list(self.s._RUNNING.values()))
        job = (await self.client.get(self.prefix + "/jobs/" + response.json()["id"])).json()
        self.assertEqual(job["state"], "failed")
        self.assertFalse((self.root / "studio/outputs/r0" / job["id"]).exists())


class LongModeExportTests(unittest.IsolatedAsyncioTestCase):
    # Retain the prior agent's owned-registry and cleanup isolation unchanged.
    asyncSetUp = ModeExportTests.asyncSetUp
    _cleanup_exports = ModeExportTests._cleanup_exports
    write_contract = ModeExportTests.write_contract
    export = ModeExportTests.export
    pixels = ModeExportTests.pixels
    api = ModeExportTests.api
    submit = ModeExportTests.submit

    def long_fixture(self, duration, mode="mixed"):
        media("-y", "-f", "lavfi", "-i", f"color=black:s=160x90:r=24:d={duration}",
              "-vf", f"drawbox=x=75:y=38:w=5:h=5:c=white:t=fill:enable='gte(t,{duration-1})'",
              "-c:v", "libx264", "-threads", "1", self.sources["video_only"])
        media("-y", "-i", self.sources["video_only"], "-f", "lavfi", "-i",
              f"sine=frequency=1600:sample_rate=48000:duration={duration}",
              "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", self.sources["final"])
        first = 60
        for i, (tone, length) in enumerate(((440, first), (880, duration-first-.25))):
            media("-y", "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000:duration={length}",
                  "-ac", "2", "-c:a", "pcm_s16le", self.root / f"unit{i}.wav")
            timing = self.timings[i]
            timing.start = 0 if i == 0 else first + .25
            timing.duration = length
            timing.end = timing.start + length
            timing.gap_after = .25 if i == 0 else 0
            timing.words = []
        self.write_contract()
        payload = json.loads((self.root / "production_mode.json").read_text())
        payload["mode"] = mode
        save(self.root, "production_mode.json", payload)

    def assert_voice(self, output, duration):
        with wave.open(str(output.parent / "narration.wav"), "rb") as assembled:
            self.assertEqual(assembled.getnframes(), round(duration * 48000))
            for i in range(2):
                with wave.open(str(self.root / f"unit{i}.wav"), "rb") as source:
                    while chunk := source.readframes(65536):
                        self.assertEqual(assembled.readframes(len(chunk)//4), chunk)
                if i == 0:
                    self.assertEqual(assembled.readframes(12000), bytes(12000*4))
            self.assertEqual(assembled.readframes(1), b"")
        decoded = output.parent / "decoded-mono.pcm"
        media("-y", "-i", output, "-ac", "1", "-ar", "48000", "-f", "s16le", decoded)
        self.assertEqual(decoded.stat().st_size, round(duration * 48000) * 2)
        for start, tone in ((1, 440), (duration-1, 880)):
            with decoded.open("rb") as stream:
                stream.seek(round(start*48000)*2)
                samples = array.array("h", stream.read(48000*2))
            def amplitude(frequency):
                step = 2*math.pi*frequency/48000
                return math.hypot(sum(x*math.cos(step*i) for i, x in enumerate(samples)),
                                  sum(x*math.sin(step*i) for i, x in enumerate(samples)))
            self.assertGreater(amplitude(tone), amplitude(1600)*100)
            self.assertGreater(amplitude(tone), 100000)
        with decoded.open("rb") as stream:
            stream.seek(round(60.08*48000)*2)
            gap = array.array("h", stream.read(round(.08*48000)*2))
        self.assertLess(max(abs(x) for x in gap), 10)

    async def check_long_exports(self, duration, mode):
        self.long_fixture(duration, mode)
        before = self.s.mode_export_hashes(self.s.mode_export_inputs(self.root))
        video, result, overlays = await self.export(f"{mode}-video", fps=24, video_bitrate_kbps=250)
        self.assertEqual(result["duration"], duration)
        self.assertAlmostEqual(float(result["probe"]["format"]["duration"]), duration, delta=.06)
        self.assertEqual(overlays[0].source_duration, duration)
        self.assertGreater(max(self.pixels(video, duration-.5, "20:20:300:150")), 180)
        self.assertGreater(max(self.pixels(video, duration-.5, "640:60:0:300")), 150)
        self.assertIn("ModeNarration", overlays[0].captions)
        self.assertIn("Alice", overlays[0].graphics)
        audio, result, _ = await self.export(f"{mode}-voice", format="mp3")
        self.assertEqual(result["duration"], duration)
        self.assert_voice(audio, duration)
        self.assertEqual(before, self.s.mode_export_hashes(self.s.mode_export_inputs(self.root)))

    async def test_real_121_seconds_all_three_mode_exports(self):
        for mode in ("voiceover", "interview", "mixed"):
            with self.subTest(mode=mode):
                await self.check_long_exports(121, mode)

    async def test_real_600_second_hard_boundary_video_and_voice(self):
        await self.check_long_exports(600, "mixed")

    async def test_legacy_limits_and_json_cannot_grant_server_context(self):
        from pydantic import ValidationError
        from fastapi import HTTPException
        self.long_fixture(121)
        for fmt in ("mp4", "mp3"):
            work = self.root / ("legacy-"+fmt)
            work.mkdir()
            with self.assertRaises(HTTPException) as caught:
                await self.s.simple_project(self.root, self.sources, self.r.ExportOptions(format=fmt), work)
            self.assertIn("120", caught.exception.detail)
        with self.assertRaises(ValidationError):
            self.r.Clip(id="long", duration=121)
        with self.assertRaises(ValidationError):
            self.r.Clip(id="long", start=119, duration=2)
        with self.assertRaises(ValidationError):
            self.r.Project(tracks=[self.r.Track(id=f"t{i}", type="audio") for i in range(9)])
        with self.assertRaises(ValidationError):
            self.r.Track(id="t", type="audio", clips=[self.r.Clip(id=f"c{i}", source_id="final", duration=1) for i in range(65)])
        plan = self.r.SimpleModeProject("mp4", 121, 600)
        for payload in (plan.model_dump(), {"tracks": [], "mode_contract": True},
                        {"tracks": [], "mode_export": {"limit": 600}}, {"tracks": [], "context": {"trusted": True}}):
            with self.assertRaises(ValidationError):
                self.r.Project.model_validate_json(json.dumps(payload))
        await self.api()
        for field in ("mode_contract", "mode_duration_limit", "mode_export", "context"):
            response = await self.client.post(self.prefix+"/export", json={"expected_revision": 0, field: 600})
            self.assertEqual(response.status_code, 422)
        # Even unvalidated in-process mutation of an ordinary Project is not
        # authorized by attaching canonical ModeExport documents.
        ordinary = self.r.Project(tracks=[self.r.Track(id="a", type="audio", clips=[self.r.Clip(id="c", source_id="final", duration=1)])])
        ordinary.tracks[0].clips[0].__dict__["duration"] = 121
        with self.assertRaises(ValidationError):
            await self.r.render_project(ordinary, self.r.ExportOptions(), self.sources, self.root / "invalid",
                                        mode_export=self.r.ModeExport(""))

    async def test_configured_and_hard_caps_source_clock_and_bitrate(self):
        from fastapi import HTTPException
        self.long_fixture(121)
        for limit in (120, 601, float("inf"), float("nan")):
            work = self.root / ("limit-"+str(limit))
            work.mkdir()
            with self.assertRaises(HTTPException):
                await self.s.simple_project(self.root, self.sources, self.r.ExportOptions(), work,
                                            mode_contract=True, mode_export=[], mode_duration_limit=limit)
        for duration in (600.01, float("inf"), float("nan")):
            with self.assertRaises(self.r.RenderError):
                self.r.SimpleModeProject("mp4", duration, 600).evaluate()
        self.timings[1].end -= 1
        self.write_contract()
        with self.assertRaises(HTTPException):
            await self.export("clock-mismatch")
        with self.assertRaises(self.r.RenderError):
            await self.r.render_project(self.r.SimpleModeProject("mp4", 600, 600), self.r.ExportOptions(),
                                        self.sources, self.root/"bitrate", mode_export=self.r.ModeExport(""))

    async def test_real_long_job_uses_existing_publication_revision_and_storage_guards(self):
        self.long_fixture(121)
        await self.api()
        response = await self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.gather(*list(self.s._RUNNING.values()))
        job = (await self.client.get(self.prefix+"/jobs/"+response.json()["id"])).json()
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["result"]["duration"], 121)
        self.record.revision += 1
        self.assertEqual((await self.client.get(self.prefix+"/outputs/"+job["id"])).status_code, 409)


class MainModeExportTests(unittest.IsolatedAsyncioTestCase):
    """Actual main adapter, authorization and Studio; never a replacement router."""

    async def asyncSetUp(self):
        await ModeExportTests.asyncSetUp(self)
        import importlib
        import httpx
        from backend.config import Settings
        from backend.task_manager import TaskRecord
        from tests.studio_publication_fixtures import synthetic_publication_files, confirm_synthetic_publication
        settings = Settings(_env_file=None, app_env="test", data_dir=self.root.parent,
                            min_free_disk_gb=0, media_command_timeout_seconds=90)
        with patch("backend.config.get_settings", return_value=settings):
            self.main = importlib.import_module("backend.main")
        # TestClient predecessors shut down this singleton and leave it draining.
        # ASGITransport does not run startup. Establish this fixture's accepting
        # baseline only, preserving the predecessor's exact drain value on exit.
        self.stack.enter_context(patch.object(self.main.task_manager, "_draining", False))
        self.stack.enter_context(patch.object(self.main.task_manager, "_tasks", {}))
        self.stack.enter_context(patch.object(self.main.settings, "data_dir", self.root.parent))
        self.stack.enter_context(patch.object(self.main.settings, "min_free_disk_gb", 0))
        self.record = TaskRecord(task_id="e" * 32, task_dir=self.root, script="Headline", uploads=[],
                                 status="done", revision=1, mode="mixed", mode_contract=True)
        for name, content in synthetic_publication_files().items():
            document = json.loads(content)
            if name == "report.json":
                document["task_id"] = self.record.task_id
            save(self.root, name, document)
        confirm_synthetic_publication(self, self.record)
        self.main.task_manager._tasks[self.record.task_id] = self.record
        self.base = "http://127.0.0.1:8765"
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.main.app, client=("127.0.0.1", 45678)),
                                       base_url=self.base, headers={"Origin": self.base})
        self.prefix = f"/api/tasks/{self.record.task_id}"

    write_contract = ModeExportTests.write_contract
    _cleanup_exports = ModeExportTests._cleanup_exports

    async def submit(self, **options):
        return await self.client.post(self.prefix + "/export", json={"fmt": "mp4", "res": "360p", **options})

    async def test_main_first_export_without_studio_initialization(self):
        self.assertFalse((self.root / "studio").exists())
        self.assertEqual(self.s.read_state(self.root)["revision"], 0)
        self.assertFalse((self.root / "studio").exists())
        response = await self.submit()
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.gather(*list(self.s._RUNNING.values()))
        job = (await self.client.get(self.prefix + "/exports/" + response.json()["export_id"])).json()
        self.assertEqual(job["state"], "succeeded", job)

    async def test_main_awaited_preparation_retains_every_gate(self):
        from backend.publication import confirm_checks
        entered, release = asyncio.Event(), asyncio.Event()
        real = self.s.simple_project
        async def held(*args, **kwargs):
            entered.set()
            await release.wait()
            return await real(*args, **kwargs)
        with patch.object(self.s, "simple_project", new=held):
            pending = asyncio.create_task(self.submit())
            try:
                await asyncio.wait_for(entered.wait(), 10)
                self.assertTrue(self.s.active_studio_tasks())
                self.assertTrue(self.s._BUSY)
                for suffix, method, body in (("/export", "post", {}), ("/checks", "post", {"expected_revision": 1, "checked_keys": []}),
                                              ("/studio/export", "post", {"expected_revision": 0}), ("", "delete", None)):
                    response = await self.client.request(method, self.prefix + suffix, **({"json": body} if body is not None else {}))
                    self.assertEqual(response.status_code, 409, response.text)
                denied = await self.client.post(self.prefix + "/export", json={}, headers={"X-Task-Token": "invalid"})
                self.assertEqual(denied.status_code, 404)
                confirm_checks(self.record, 1, [])
            finally:
                release.set()
                response = await pending
        self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(self.s._BUSY)
        self.assertFalse(self.s._RUNNING)
        self.assertFalse(list((self.root / "studio").glob("jobs/*.json")))

    async def test_main_every_pre_admission_failure_releases_reservations(self):
        from fastapi import HTTPException
        real_project, real_write = self.s.simple_project, self.s.write_json_atomic
        for phase in ("prepare", "cancel", "snapshot", "job", "state"):
            with self.subTest(phase=phase):
                async def prepare(*args, **kwargs):
                    if phase == "prepare":
                        raise HTTPException(422, "synthetic preparation failure")
                    if phase == "cancel":
                        raise asyncio.CancelledError
                    return await real_project(*args, **kwargs)
                def write(path, data):
                    if ((phase == "snapshot" and path.name == "snapshot.json")
                            or (phase == "job" and path.parent.name == "jobs")
                            or (phase == "state" and path.name == "state.json")):
                        raise OSError("synthetic persistence failure")
                    return real_write(path, data)
                # Direct endpoint dispatch preserves the actual main/Studio
                # authorization while surfacing cancellation instead of an ASGI
                # disconnected-response wrapper exception.
                from starlette.requests import Request
                body = b'{"res":"360p"}'
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}
                request = Request({"type": "http", "method": "POST", "scheme": "http", "path": self.prefix + "/export",
                    "query_string": b"", "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 45678),
                    "headers": [(b"host", b"127.0.0.1:8765"), (b"origin", self.base.encode())]}, receive)
                with patch.object(self.s, "simple_project", new=prepare), patch.object(self.s, "write_json_atomic", new=write):
                    with self.assertRaises((HTTPException, OSError, asyncio.CancelledError)):
                        await self.main.export_task(self.record.task_id, request)
                self.assertFalse(self.s._BUSY)
                self.assertFalse(self.s._DISK_RESERVATIONS)
                self.assertFalse(self.s._PREPARING)
                self.assertFalse(self.s._RUNNING)
                self.assertFalse(list((self.root / "studio/outputs/r0").glob("*/snapshot.json")))

    async def test_main_drain_during_preparation_prevents_admission(self):
        real = self.s.simple_project
        async def draining(*args, **kwargs):
            self.assertIn(asyncio.current_task(), self.s.active_studio_tasks())
            project = await real(*args, **kwargs)
            self.main.task_manager._draining = True
            return project
        with patch.object(self.main.task_manager, "_draining", False):
            with patch.object(self.s, "simple_project", new=draining):
                response = await self.submit()
            self.assertEqual(response.status_code, 503, response.text)
            self.assertFalse(self.s._BUSY)
            self.assertFalse(self.s._RUNNING)

    async def test_main_disconnected_cancel_drains_before_releasing_busy(self):
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        from starlette.requests import Request
        with patch.object(self.s, "render_project", new=held):
            response = await self.submit()
            self.assertEqual(response.status_code, 202, response.text)
            await entered.wait()
            identity = response.json()["export_id"]
            endpoint = self.main._private_endpoint(self.main._studio_router, "cancel_job")
            request = Request({"type": "http", "method": "DELETE", "scheme": "http", "path": self.prefix + "/studio/jobs/" + identity,
                "query_string": b"", "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 45678),
                "headers": [(b"host", b"127.0.0.1:8765"), (b"origin", self.base.encode())]})
            cancelling = asyncio.create_task(endpoint(request, self.record.task_id, identity))
            await asyncio.wait_for(cleaning.wait(), 10)
            cancelling.cancel()
            await asyncio.sleep(0)
            cancelling.cancel()
            await asyncio.sleep(0)
            self.assertFalse(cancelling.done())
            self.assertTrue(self.s._BUSY)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await cancelling
        job = (await self.client.get(self.prefix + "/exports/" + identity)).json()
        self.assertEqual(job["state"], "cancelled", job)
        self.assertFalse(self.s._BUSY)
        self.assertFalse((self.root / "studio/outputs/r0" / identity).exists())

    async def test_main_canonical_pipeline_artifacts_all_modes_and_revision(self):
        from tests.test_mode_pipeline import ModeMediaIntegration, Reporter, load_product
        from backend.publication import publication_gate, confirm_checks
        from backend.revisions import snapshot_revision
        from backend.workbench import _prepare_workspace
        from backend.models import Speaker
        mp = load_product()
        fixture = ModeMediaIntegration()
        fixture.setUpClass()
        try:
            for mode in ("original", "mixed", "voiceover"):
                with self.subTest(mode=mode):
                    await fixture.asyncSetUp()
                    try:
                        source = fixture.record(mode, with_broll=mode != "original",
                            texts=([("今天活动开幕。", "quote"), ("现场活动开始了。", "narration")] if mode == "mixed" else None))
                        for name, value in vars(source).items():
                            if name != "task_id":
                                setattr(self.record, name, value)
                        self.record.status = "done"
                        with ExitStack() as providers:
                            fixture.providers(providers, all_original=mode == "original")
                            await mp.run_mode_pipeline(self.record, Reporter(), fixture.settings)
                            snapshot_revision(self.record)
                            # A real workspace copy/edit creates new revision artifacts
                            # while source_clocks still refer to the source cache.
                            workspace = fixture.root / "edit"
                            await _prepare_workspace(self.record, workspace, fixture.root)
                            work = SimpleNamespace(**{**vars(self.record), "task_dir": workspace, "revision": 1})
                            await mp.edit_mode_workspace(work, self.record, {"keep_sentence_ids": [s.idx for s in source.sentences],
                                "edits": [], "speakers": [Speaker(id="u:1", name="Test", title="Synthetic")]}, fixture.settings)
                        # Publish the edited canonical artifacts through the real copier.
                        from backend.revisions import publish_artifacts
                        publish_artifacts(workspace, fixture.root, lambda: None)
                        self.record.revision = 1
                        self.record.speakers = work.speakers
                        snapshot_revision(self.record)
                        gate = publication_gate(self.record)
                        self.assertEqual(gate["blocking_count"], 0, gate)
                        confirm_checks(self.record, 1, [c["key"] for c in gate["checks"] if c["level"] == 1])
                        self.assertTrue(publication_gate(self.record)["passed"])
                        before = self.s.mode_export_hashes(self.s.mode_export_inputs(fixture.root))
                        for fmt in ("mp4", "mp3", "gif"):
                            response = await self.submit(fmt=fmt)
                            self.assertEqual(response.status_code, 202, response.text)
                            await asyncio.gather(*list(self.s._RUNNING.values()))
                            identity = response.json()["export_id"]
                            job = (await self.client.get(self.prefix + "/exports/" + identity)).json()
                            self.assertEqual(job["state"], "succeeded", job)
                            download = await self.client.get(self.prefix + "/studio/outputs/" + identity)
                            self.assertEqual(download.status_code, 200, download.text[:100] if download.status_code != 200 else "")
                            self.assertEqual(hashlib.sha256(download.content).hexdigest(), job["result"]["sha256"])
                        self.assertEqual(before, self.s.mode_export_hashes(self.s.mode_export_inputs(fixture.root)))
                    finally:
                        await fixture.asyncTearDown()
        finally:
            fixture.tearDownClass()


class MainModeExportIsolationTests(unittest.TestCase):
    """Run the actual fixture lifecycle with hostile pre-existing state."""

    def check_restoration(self, phase):
        import importlib
        import backend.studio as studio
        main_module = importlib.import_module("backend.main")
        owner = self

        class Probe(MainModeExportTests):
            async def asyncSetUp(self):
                await super().asyncSetUp()
                owner.assertFalse(self.main.task_manager.is_draining)
                owner.assertEqual(list(self.main.task_manager._tasks), [self.record.task_id])
                if phase == "setup_failure":
                    raise RuntimeError("synthetic setup failure")

            async def exercise(self):
                # Same real main route/render/download assertions, not a mock
                # success or replacement drain/authorization function.
                await self.test_main_first_export_without_studio_initialization()
                with patch.object(self.main.task_manager, "_draining", True):
                    denied = await self.submit()
                    owner.assertEqual(denied.status_code, 503, denied.text)
                    owner.assertEqual(denied.headers.get("Retry-After"), "60")
                if phase == "body_failure":
                    self.fail("synthetic body failure")

        for draining in (False, True):
            with self.subTest(phase=phase, draining=draining), ExitStack() as outer:
                # Colliding ID proves teardown restores rather than blindly pops
                # an earlier record. Sentinels must never be cancelled/accessed.
                old_tasks = {"e" * 32: object(), "unrelated": object()}
                task_snapshot = old_tasks.copy()
                outer.enter_context(patch.object(main_module.task_manager, "_tasks", old_tasks))
                outer.enter_context(patch.object(main_module.task_manager, "_draining", draining))
                old_studio = {name: {"previous": object()} for name in
                              ("_RUNNING", "_INGESTING", "_PREPARING", "_DISK_RESERVATIONS")}
                old_studio["_BUSY"] = {"previous"}
                studio_snapshot = {name: value.copy() for name, value in old_studio.items()}
                for name, value in old_studio.items():
                    outer.enter_context(patch.object(studio, name, value))
                prior_settings = (main_module.settings.data_dir, main_module.settings.min_free_disk_gb)
                probe = Probe("exercise")
                result = unittest.TestResult()
                probe.run(result)
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(len(result.failures), int(phase == "body_failure"), result.failures)
                self.assertEqual(len(result.errors), int(phase == "setup_failure"), result.errors)
                self.assertEqual(len(result.skipped), 0)
                if phase == "body_failure":
                    self.assertIn("AssertionError: synthetic body failure", result.failures[0][1])
                if phase == "setup_failure":
                    self.assertIn("RuntimeError: synthetic setup failure", result.errors[0][1])
                self.assertIs(main_module.task_manager._draining, draining)
                self.assertIs(main_module.task_manager._tasks, old_tasks)
                self.assertEqual(old_tasks, task_snapshot)
                for name, value in old_studio.items():
                    self.assertIs(getattr(studio, name), value)
                    self.assertEqual(value, studio_snapshot[name])
                self.assertEqual((main_module.settings.data_dir, main_module.settings.min_free_disk_gb), prior_settings)
                self.assertTrue(probe.client.is_closed)
                self.assertFalse(probe.root.exists())

    def test_accepting_and_draining_state_restored_after_success(self):
        self.check_restoration("success")

    def test_accepting_and_draining_state_restored_after_body_failure(self):
        self.check_restoration("body_failure")

    def test_accepting_and_draining_state_restored_after_setup_failure(self):
        self.check_restoration("setup_failure")


def main():
    # No product import before confinement, explicit fresh TEMP and dotenv veto.
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from tests.run_core_validation import Guards, Result, synthetic_environment
    temporary = Path(tempfile.mkdtemp(prefix="gm-mode-export-validation-"))
    environment, ffmpeg = synthetic_environment(temporary)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        for name in ("tmp", "tasks", "cache"):
            (temporary / name).mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(temporary / "tmp")
        guard = Guards(temporary, temporary / "evidence", ffmpeg)
        guard.install(stack)
        guard.self_check()
        guard.stage = "focused_mode_exports"
        bound = ("backend/main.py", "backend/studio.py", "backend/studio_render.py", "backend/graphics.py",
                 "backend/mode_pipeline.py", "tests/test_mode_exports.py", "tests/test_mode_api.py",
                 "tests/test_m6_operations.py", "tests/run_core_validation.py")
        hashes = {name: sha(root / name) for name in bound}
        save(temporary, "source-before.json", hashes)
        for name in bound:
            ast.parse((root / name).read_text(encoding="utf-8"), filename=name)
        transitions = []

        class StateResult(Result):
            def startTest(self, test):
                main_module = sys.modules.get("backend.main")
                self.before_drain = None if main_module is None else main_module.task_manager.is_draining
                super().startTest(test)

            def stopTest(self, test):
                main_module = sys.modules.get("backend.main")
                transitions.append({"test": test.id(), "before": self.before_drain,
                                    "after": None if main_module is None else main_module.task_manager.is_draining})
                super().stopTest(test)

        with (temporary / "tests.log").open("x", encoding="utf-8") as log:
            cases = ((LongModeExportTests,) if "--long-only" in sys.argv else
                     (ModeExportTests, LongModeExportTests) if "--focused" in sys.argv else
                     (MainModeExportTests, MainModeExportIsolationTests) if "--main-only" in sys.argv
                     else (ModeExportTests, LongModeExportTests, MainModeExportTests, MainModeExportIsolationTests))
            suite = unittest.TestSuite()
            if "--preceding-operations" in sys.argv:
                # Preserve discovery's module/class/method ordering, without
                # importing or replaying any historical evidence or real data.
                suite.addTests(unittest.defaultTestLoader.loadTestsFromName("tests.test_m6_operations"))
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case) for case in cases)
            if "--regression" in sys.argv:
                for file in sorted((root / "tests").glob("test_studio*.py")):
                    suite.addTests(unittest.defaultTestLoader.loadTestsFromName("tests." + file.stem))
                suite.addTests(unittest.defaultTestLoader.loadTestsFromName("tests.test_mode_api"))
            result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=StateResult).run(suite)
        assert hashes == {name: sha(root / name) for name in bound}, "Source changed during validation"
        print((temporary / "tests.log").read_text(encoding="utf-8"))
        clean = (result.wasSuccessful() and not guard.ports
             and not any(key.startswith("focused_mode_exports:denied:") for key in guard.counts))
        summary = {"status": "passed" if clean else "failed_or_requires_review",
               "tests": result.testsRun, "passed": result.passed,
               "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
               "guard": dict(guard.counts), "evidence": str(temporary), "sourceUnchanged": True, "sourceHashes": hashes,
               "remainingOwnedListeners": sorted(guard.ports), "drainTransitions": transitions}
        save(temporary, "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=True))
        return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())