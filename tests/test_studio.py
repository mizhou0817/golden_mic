"""Local-only studio tests. All media success assertions use real FFmpeg output."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend.studio import (
    Action, _BUSY, _RUNNING, apply_action, capabilities, catalog,
    create_studio_router, requires_disclosure, parse_srt, simple_project, studio_task_busy,
)
from backend.studio_render import (
    Clip, ExportOptions, Project, RenderError, Track, render_project, subtitle_document,
)
from tests.studio_publication_fixtures import (
    QC_BLOCKER, assert_publication_rejected, confirm_synthetic_publication,
    seed_synthetic_publication, synthetic_publication_files,
)

TOOLS = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def ffmpeg(*args: str) -> bytes:
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1", *args],
                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45).stdout


def make_fixture(path: Path) -> None:
    ffmpeg("-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
           "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "2", str(path))


def base_project(source: str = "final", duration: float = 0.5, **fields) -> Project:
    return Project(tracks=[Track(id="v", type="video", clips=[Clip(id="c", source_id=source, duration=duration, **fields)])])


class StudioModelTests(unittest.TestCase):
    def test_all_113_prototype_keys_classified(self):
        actual = capabilities()
        self.assertEqual(actual["tool_count"], 113)
        # The old HTML was retired. The retained 113-row requirements matrix
        # is the ID/classification baseline, not evidence of full implementation.
        path = Path(__file__).resolve().parents[1] / "docs/PROTOTYPE_CAPABILITY_MATRIX_20260925.md"
        rows = re.findall(r"^\| (\d{3}) \| `([^`]+)` \|[^\n]*? / `(partial|metadata|renderer|unsupported)`", path.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertEqual([int(row[0]) for row in rows], list(range(1, 114)))
        self.assertEqual(len({row[1] for row in rows}), 113)
        self.assertEqual([(row[1], row[2]) for row in rows],
                 [(tool["id"], tool["classification"]) for tool in actual["tools"]])
        for tool in actual["tools"]:
            self.assertTrue(tool["reason"])
            if tool["classification"] == "unsupported":
                self.assertFalse(tool["available"])

    def test_bounds_and_unknown_fields(self):
        for value in ({"keyframes": []}, {"speed": 0}, {"duration": float("nan")}, {"rotation": 45}, {"source_id": "../final.mp4"}, {"source_id": "http://localhost"}, {"fade_in": 2}, {"crop": {"x": 0.8, "width": 0.5}}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Clip.model_validate({"id": "c", "duration": 1, **value})
        for value in ({"resolution": 2160}, {"fps": 120}, {"format": "tga"}, {"filter": "movie=/etc/passwd"}, {"format": "png", "fps": 60}, {"format": "wav", "aspect": "1:1"}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ExportOptions.model_validate(value)
        with self.assertRaises(ValidationError):
            Track(id="t", type="audio", clips=[Clip(id="c", source_id="s", duration=1, brightness=0.5)])
        with self.assertRaises(ValidationError):
            Project(tracks=[Track(id="same", type="text", clips=[Clip(id="same", duration=1, text="x")])])

    def test_split_reversed_and_plain_time_mapping(self):
        for reverse in (False, True):
            project = base_project(duration=4, trim=2, speed=2, reverse=reverse)
            changed = apply_action(project, Action(expected_revision=0, op="split", clip_id="c", at=1, new_id="right"))
            left, right = changed.tracks[0].clips
            self.assertEqual((left.duration, right.duration, right.start), (1, 3, 1))
            self.assertEqual((left.trim, right.trim), (8, 2) if reverse else (2, 4))
            self.assertEqual(project.tracks[0].clips[0].duration, 4)

    def test_action_fields_not_silently_ignored(self):
        with self.assertRaises(ValidationError):
            Action(expected_revision=0, op="delete", clip_id="c", trim=2)
        with self.assertRaises(ValidationError):
            Action(expected_revision=0, op="split", clip_id="c")

    def test_subtitle_times_and_ass_injection(self):
        project = Project(tracks=[Track(id="text", type="text", clips=[Clip(id="t", start=1.25, duration=1.5, text=r"{\p1}hello" + "\nworld")])])
        srt = subtitle_document(project, ExportOptions(format="srt"))
        self.assertIn("00:00:01,250 --> 00:00:02,750", srt)
        ass = subtitle_document(project, ExportOptions(format="ass"))
        self.assertNotIn(r"{\p1}", ass)
        self.assertIn(r"\Nworld", ass)

    def test_new_controls_fail_closed(self):
        invalid = [
            {"keyframes": {"x": [{"time": 0, "value": 0}]}},
            {"keyframes": {"opacity": [{"time": 0, "value": 1}, {"time": 2, "value": 0}]}},
            {"keyframes": {"x": [{"time": 0, "value": 0}, {"time": 0, "value": 1}]}},
            {"keyframes": {"scale": [{"time": 0, "value": 0}, {"time": 1, "value": 1}]}},
            {"keyframes": {"x": [{"time": 0, "value": 0, "easing": "sin(t)"}, {"time": 1, "value": 1}]}},
            {"mask": {"type": "bezier"}}, {"mask": {"type": "ellipse", "feather": 1}},
            {"freeze": True}, {"freeze": True, "mute": True, "reverse": True}, {"bass_db": 13},
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                Clip(id="c", duration=1, **fields)
        with self.assertRaises(ValidationError):
            ExportOptions(frame_time=0)
        with self.assertRaises(ValidationError):
            Project(workspace={"shortcuts": {"undo": "Ctrl+Z", "redo": "Ctrl+Z"}})
        with self.assertRaises(ValidationError):
            Project(tracks=[Track(id="t", type="text", group_id="missing")])

    def test_safe_srt_roundtrip_and_rejection(self):
        cues = parse_srt("\ufeff1\r\n00:00:00,100 --> 00:00:01,000\r\n你好\r\nworld\r\n")
        self.assertEqual(cues[0].text, "你好\nworld")
        project = Project(tracks=[Track(id="t", type="text", clips=cues)])
        self.assertEqual(parse_srt(subtitle_document(project, ExportOptions(format="srt")))[0].text, cues[0].text)
        for text in ("[Script Info]\nDialogue: bad", "1\n00:00:00,000 --> 00:00:01,000\n{\\p1}shape",
                     "1\n00:00:00,000 --> 00:00:01,000\n<b>html</b>",
                     "1\n00:00:00,000 --> 00:02:00,001\nbeyond",
                     "1\n00:00:01,000 --> 00:00:00,000\nreverse",
                     "1\n00:00:00,000 --> 00:00:01,000\none\n\n2\n00:00:00,500 --> 00:00:02,000\noverlap"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_srt(text)

    def test_split_freeze_and_reject_animated_split(self):
        frozen = base_project(duration=2, freeze=True, mute=True, trim=0.3)
        split = apply_action(frozen, Action(expected_revision=0, op="split", clip_id="c", at=1, new_id="r"))
        self.assertEqual([c.trim for c in split.tracks[0].clips], [0.3, 0.3])
        animated = base_project(duration=2, keyframes={"x": [{"time": 0, "value": 0}, {"time": 2, "value": 1}]})
        with self.assertRaises(HTTPException):
            apply_action(animated, Action(expected_revision=0, op="split", clip_id="c", at=1, new_id="r"))


@unittest.skipUnless(TOOLS, "FFmpeg/ffprobe must be on PATH")
class StudioRenderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        make_fixture(self.source)
        self.hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    async def asyncTearDown(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.hash)
        self.temp.cleanup()

    async def render(self, project=None, **options):
        work = self.root / f"out{len(list(self.root.glob('out*')))}"
        result = await render_project(project or base_project(), ExportOptions(**options), {"final": self.source}, work)
        return result, work / result["file"]

    async def test_all_real_containers(self):
        for fmt in ("mp4", "mov", "mkv", "avi", "gif", "mp3", "wav", "png"):
            with self.subTest(format=fmt):
                options = {"format": fmt}
                if fmt not in {"mp3", "wav"}:
                    options["resolution"] = 360
                result, output = await self.render(**options)
                self.assertGreater(output.stat().st_size, 100)
                self.assertNotIn(str(self.root), json.dumps(result))
                streams = result["probe"]["streams"]
                if fmt in {"mp3", "wav"}:
                    self.assertFalse(any(s["codec_type"] == "video" for s in streams))
                    self.assertEqual(streams[0]["sample_rate"], "48000")
                    self.assertEqual(streams[0]["channels"], 2)
                else:
                    video = next(s for s in streams if s["codec_type"] == "video")
                    self.assertEqual((video["width"], video["height"]), (640, 360))
                if fmt == "mp3":
                    self.assertEqual(int(streams[0]["bit_rate"]), 192000)

    async def test_fps_resolution_aspect_and_audio_bitrate(self):
        cases = [(360, 24, "16:9", (640, 360)), (720, 25, "9:16", (720, 1280)),
                 (1080, 30, "1:1", (1080, 1080)), (360, 60, "1:1", (360, 360))]
        for res, fps, aspect, dimensions in cases:
            with self.subTest(res=res, fps=fps):
                result, _ = await self.render(resolution=res, fps=fps, aspect=aspect, video_bitrate_kbps=1000, audio_bitrate_kbps=128)
                video = result["probe"]["streams"][0]
                self.assertEqual((video["width"], video["height"]), dimensions)
                n, d = map(int, video["avg_frame_rate"].split("/"))
                self.assertAlmostEqual(n/d, fps)
                self.assertEqual(video["color_space"], "bt709")
                self.assertEqual(result["applied"]["video_bitrate_kbps"], 1000)

    async def test_real_filters_change_pixels_and_audio(self):
        original, p0 = await self.render(resolution=360)
        project = base_project(trim=0.25, speed=2, reverse=True, rotation=90, mirror=True, flip=True,
                               crop={"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}, scale=0.6,
                               x=0.1, y=-0.1, opacity=0.8, brightness=0.2, contrast=1.2,
                               saturation=0.5, sharpen=0.5, noise=2, volume=0.2, pan=1,
                               fade_in=0.1, fade_out=0.1, audio_effect="compressor")
        _, p1 = await self.render(project, resolution=360)
        def frame(path):
            return ffmpeg("-ss", "0.2", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertNotEqual(frame(p0), frame(p1))
        import array
        pcm = array.array("h", ffmpeg("-i", str(p1), "-f", "s16le", "-ac", "2", "pipe:1"))
        self.assertLess(sum(abs(v) for v in pcm[::2]), sum(abs(v) for v in pcm[1::2]) * 0.05)
        self.assertAlmostEqual(float(original["probe"]["format"]["duration"]), 0.5, delta=0.1)

    async def test_overlay_chroma_adjustment_and_text(self):
        project = base_project()
        project.tracks.extend([
            Track(id="overlay", type="overlay", clips=[Clip(id="o", source_id="final", start=0.1, duration=0.3, scale=0.3, chroma_color="00FF00", chroma_similarity=0.15)]),
            Track(id="adjustment", type="adjustment", clips=[Clip(id="a", start=0.2, duration=0.3, brightness=0.1)]),
            Track(id="text", type="text", clips=[Clip(id="t", duration=0.5, text="真实字幕 Test", font_size=90, fade_in=0.1, color="FF0000")]),
        ])
        _, p0 = await self.render(project, resolution=360, subtitles="none")
        _, p1 = await self.render(project, resolution=360, subtitles="large")
        self.assertNotEqual(p0.read_bytes(), p1.read_bytes())
        ass = p1.parent / "studio.ass"
        self.assertIn("真实字幕 Test", ass.read_text(encoding="utf-8"))

    async def test_hard_cut_concat_and_real_split(self):
        project = base_project(duration=1)
        project = apply_action(project, Action(expected_revision=0, op="split", clip_id="c", at=0.5, new_id="right"))
        project.tracks[0].clips[1].brightness = 0.5
        result, _ = await self.render(project, resolution=360)
        self.assertAlmostEqual(float(result["probe"]["format"]["duration"]), 1, delta=0.1)

    async def test_subtitle_files_real_timing(self):
        project = Project(tracks=[Track(id="text", type="text", clips=[Clip(id="t", start=0.1, duration=0.4, text="caption")])])
        for fmt in ("srt", "ass"):
            _, output = await self.render(project, format=fmt)
            self.assertIn("caption", output.read_text())

    async def test_generated_disclosure_burn_and_rejected_stripping(self):
        work = self.root / "disclosed"
        result = await render_project(base_project(), ExportOptions(resolution=360, subtitles="none"), {"final": self.source}, work, disclosure=True)
        self.assertTrue(result["disclosure"])
        self.assertIn("AI生成示意画面", (work / "studio.ass").read_text(encoding="utf-8"))
        for fmt in ("mp3", "wav", "ass", "srt"):
            with self.assertRaisesRegex(RenderError, "strips"):
                await render_project(base_project(), ExportOptions(format=fmt), {"final": self.source}, self.root / fmt, disclosure=True)

    async def test_source_range_and_budget_rejected(self):
        with self.assertRaisesRegex(RenderError, "source duration"):
            await self.render(base_project(duration=2, trim=1), resolution=360)
        with self.assertRaisesRegex(RenderError, "size budget"):
            await self.render(base_project(duration=120), video_bitrate_kbps=12000)
        project = Project(tracks=[Track(id="v", type="video", clips=[Clip(id=f"c{i}", source_id="final", duration=0.1) for i in range(17)])])
        with self.assertRaisesRegex(RenderError, "decoder budget"):
            await self.render(project, resolution=360)

    async def test_keyframe_motion_scale_opacity_and_masks_pixels(self):
        solid = self.root / "solid.mp4"
        ffmpeg("-y", "-f", "lavfi", "-i", "color=red:s=160x90:r=30:d=1", "-c:v", "libx264", "-threads", "1", str(solid))

        async def image(fields, time, name):
            project = base_project(duration=1, **fields)
            work = self.root / name
            await render_project(project, ExportOptions(format="png", resolution=360, aspect="1:1", frame_time=time), {"final": solid}, work)
            return ffmpeg("-i", str(work / "output.png"), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")

        def pixel(frame, x, y):
            return tuple(frame[(y*360+x)*3:(y*360+x)*3+3])

        fields = {"fit": "cover", "keyframes": {
            "x": [{"time": 0, "value": -0.25}, {"time": 1, "value": 0.25, "easing": "linear"}],
            "scale": [{"time": 0, "value": 0.2, "easing": "ease_in"}, {"time": 1, "value": 0.6}],
            "opacity": [{"time": 0, "value": 0.2, "easing": "ease_out"}, {"time": 1, "value": 1}],
        }}
        early = await image(fields, 0.1, "kfearly")
        late = await image(fields, 0.8, "kflate")
        def stats(frame):
            pixels = [(i//3 % 360, frame[i]) for i in range(0, len(frame), 3) if frame[i] > 15]
            return len(pixels), sum(x for x, _ in pixels)/len(pixels), max(v for _, v in pixels)
        a, b = stats(early), stats(late)
        self.assertGreater(b[0], a[0]*2)
        self.assertGreater(b[1], a[1]+80)
        self.assertGreater(b[2], a[2]+70)
        for kind in ("rectangle", "ellipse"):
            mask = {"type": kind, "width": 0.8, "height": 0.8, "feather": 0.3}
            frame = await image({"fit": "cover", "mask": mask}, 0.2, kind)
            self.assertGreater(pixel(frame, 180, 180)[0], 230)
            self.assertLess(pixel(frame, 10, 10)[0], 5)
            self.assertTrue(10 < pixel(frame, 310, 180)[0] < 200)
            inverted = await image({"fit": "cover", "mask": {**mask, "invert": True}}, 0.2, kind+"inv")
            self.assertLess(pixel(inverted, 180, 180)[0], 5)
            self.assertGreater(pixel(inverted, 10, 10)[0], 230)

    async def test_freeze_selected_png_and_frame_bounds(self):
        _, frozen = await self.render(base_project(duration=1, trim=0.5, freeze=True, mute=True), resolution=360)
        def frame(path, time):
            return ffmpeg("-ss", str(time), "-i", str(path), "-frames:v", "1", "-vf", "scale=32:18", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        a, b = frame(frozen, 0.1), frame(frozen, 0.8)
        self.assertLess(sum(abs(x-y) for x, y in zip(a, b))/len(a), 2)
        result, selected = await self.render(base_project(duration=1), format="png", resolution=360, frame_time=0.8)
        _, first = await self.render(base_project(duration=1), format="png", resolution=360)
        self.assertNotEqual(selected.read_bytes(), first.read_bytes())
        self.assertEqual(result["frame_time"], 0.8)
        with self.assertRaisesRegex(RenderError, "frame_time"):
            await self.render(format="png", resolution=360, frame_time=0.5)
        with self.assertRaisesRegex(RenderError, "reverse buffer"):
            with patch("backend.studio_render.MAX_REVERSE_BYTES", 1):
                await self.render(base_project(reverse=True), resolution=360)

    async def test_gif_cap_and_postcrop_disclosure_pixels(self):
        long = self.root / "long.mp4"
        ffmpeg("-y", "-f", "lavfi", "-i", "color=red:s=160x90:r=30:d=7", "-c:v", "libx264", "-threads", "1", str(long))
        work = self.root / "gifcap"
        result = await render_project(base_project(duration=7), ExportOptions(format="gif", resolution=360), {"final": long}, work)
        self.assertLessEqual(float(result["probe"]["format"]["duration"]), 6.01)
        self.assertEqual(result["duration"], 6)
        outputs = []
        for disclosure in (False, True):
            work = self.root / str(disclosure)
            await render_project(base_project(fit="cover"), ExportOptions(format="png", resolution=360, aspect="9:16"),
                                 {"final": long}, work, disclosure=disclosure)
            outputs.append(ffmpeg("-i", str(work / "output.png"), "-vf", "crop=360:80:0:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"))
        self.assertGreater(sum(x != y for x, y in zip(*outputs)), 500)

    async def test_audio_eq_reverb_normalization_are_real_stereo(self):
        import array
        _, baseline = await self.render(base_project(duration=1), format="wav")
        def samples(path):
            return array.array("h", ffmpeg("-i", str(path), "-f", "s16le", "-ac", "2", "pipe:1"))
        original = samples(baseline)
        for effect in ("reverb", "normalize", "none"):
            fields = {"audio_effect": effect}
            if effect == "none":
                fields.update(bass_db=12, treble_db=-12)
            result, output = await self.render(base_project(duration=1, **fields), format="wav")
            self.assertEqual(result["probe"]["streams"][0]["channels"], 2)
            self.assertNotEqual(samples(output), original)

    async def test_text_styles_change_pixels(self):
        outputs = []
        for styled in (False, True):
            fields = {"bold": True, "outline": 5, "shadow": 4, "background": True} if styled else {}
            project = base_project()
            project.tracks.append(Track(id="t", type="text", clips=[Clip(id="txt", duration=0.5, text="Style 字幕", font_size=100, **fields)]))
            _, path = await self.render(project, format="png", resolution=360)
            outputs.append(path.read_bytes())
        self.assertNotEqual(*outputs)

    async def test_simple_preserves_final_mix_graphics_and_center_crop(self):
        import array
        import math
        clean, final, narration = (self.root / name for name in ("clean.mp4", "finished.mp4", "narration.m4a"))
        # Red left / green center / blue right: a square/portrait crop must be green,
        # not contain-fit black. A white upper-center bar stands for burned graphics.
        picture = "color=green:s=320x180:r=30:d=2,drawbox=x=0:y=0:w=70:h=180:color=red:t=fill,drawbox=x=250:y=0:w=70:h=180:color=blue:t=fill"
        ffmpeg("-y", "-f", "lavfi", "-i", picture, "-c:v", "libx264", "-threads", "1", str(clean))
        ffmpeg("-y", "-i", str(clean), "-f", "lavfi", "-i", "aevalsrc=0.1*sin(2*PI*440*t)+0.1*sin(2*PI*880*t):s=48000:d=2",
               "-vf", "drawbox=x=145:y=25:w=30:h=20:color=white:t=fill", "-c:v", "libx264", "-threads", "1", "-c:a", "aac", "-t", "2", str(final))
        ffmpeg("-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1.93", "-c:a", "aac", str(narration))
        sources = {"final": final, "video_only": clean, "narration": narration}
        (self.root / "timings.json").write_text(json.dumps([{"start": 0, "end": 1.9, "text": "caption"}]))
        (self.root / "script_structure.json").write_text(json.dumps({"title": "Safe {\\p1} title"}))
        for style in ("standard", "none", "large"):
            work = self.root / ("preserve_"+style)
            work.mkdir()
            options = ExportOptions(resolution=360, aspect="1:1", subtitles=style)
            project = await simple_project(self.root, sources, options, work)
            self.assertNotIn("narration", [c.source_id for t in project.tracks for c in t.clips])
            if style != "standard":
                self.assertEqual(project.tracks[1].clips[0].source_id, "final")
                self.assertFalse(project.tracks[-1].clips[0].subtitle)
            result = await render_project(project, options, sources, work)
            self.assertAlmostEqual(result["duration"], 2)
            output = work / result["file"]
            pcm = array.array("h", ffmpeg("-i", str(output), "-t", "0.5", "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"))
            def energy(freq):
                return abs(sum(value * complex(math.cos(2*math.pi*freq*i/48000), math.sin(2*math.pi*freq*i/48000)) for i, value in enumerate(pcm)))
            self.assertGreater(energy(880), energy(440)*0.5, "finished music tone must remain")
            frame = ffmpeg("-ss", "0.5", "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
            top = frame[(10*360+10)*3:(10*360+10)*3+3]
            self.assertGreater(top[1], top[0]+50, "center crop must fill green, not black fit")
            if style == "standard":
                graphic = frame[(65*360+180)*3:(65*360+180)*3+3]
                self.assertTrue(all(v > 220 for v in graphic), "finished graphics must survive")

    async def test_simple_duration_607_uses_final_not_short_narration(self):
        sources = {"final": self.source, "video_only": self.source, "narration": self.root / "short.m4a"}
        with patch("backend.studio.probe", return_value={"duration": 60.7}):
            project = await simple_project(self.root, sources, ExportOptions(subtitles="none"), self.root)
        self.assertEqual(project.duration, 60.7)
        self.assertEqual(project.tracks[1].clips[0].source_id, "final")

    async def test_named_playlist_not_followed(self):
        evil = self.root / "playlist.mp4"
        evil.write_text("#EXTM3U\nhttp://127.0.0.1:9/secret\n")
        with self.assertRaises(Exception):
            await render_project(base_project(), ExportOptions(resolution=360), {"final": evil}, self.root / "bad")

    async def test_cancel_real_subprocess(self):
        from backend.media import run_logged_command
        work = self.root / "cancel"
        work.mkdir()
        task = asyncio.create_task(run_logged_command(
            ["ffmpeg", "-nostdin", "-re", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=30", "-t", "60", "-f", "null", "-"], work, "cancellation fixture"))
        await asyncio.sleep(0.15)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 12)


@unittest.skipUnless(TOOLS, "FFmpeg/ffprobe must be on PATH")
class StudioAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "tasks"
        self.root = self.data / "task"
        self.root.mkdir(parents=True)
        make_fixture(self.root / "final.mp4")
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, uploads=[], status="done", revision=0)
        self.publication_originals = seed_synthetic_publication(self, self.record)
        self.settings = SimpleNamespace(data_dir=self.data, media_command_timeout_seconds=60, minimum_free_disk_bytes=0)
        self.calls = []
        self.manager = SimpleNamespace(_draining=False)

        async def authorize(request, task_id, write=False):
            self.calls.append((task_id, write))
            if request.headers.get("X-Token") != "valid" or task_id != "task":
                raise HTTPException(403, "denied")
            if write and request.headers.get("X-CSRF") != "valid":
                raise HTTPException(403, "CSRF")
            return self.record

        self.authorize = authorize
        self.app = FastAPI()
        self.app.include_router(create_studio_router(self.settings, self.manager, authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver",
                                        headers={"X-Token": "valid", "X-CSRF": "valid"})
        self.prefix = "/api/tasks/task/studio"
        self.source_hash = hashlib.sha256((self.root / "final.mp4").read_bytes()).hexdigest()

    async def asyncTearDown(self):
        tasks = list(_RUNNING.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.client.aclose()
        self.assertFalse(_BUSY)
        self.assertEqual(hashlib.sha256((self.root / "final.mp4").read_bytes()).hexdigest(), self.source_hash)
        for name, content in self.publication_originals.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.temp.cleanup()

    async def save(self, project=None, revision=0):
        return await self.client.post(self.prefix + "/project", json={"expected_revision": revision, "project": (project or base_project()).model_dump()})

    async def finish(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.gather(*list(_RUNNING.values()))
        return (await self.client.get(self.prefix + "/jobs/" + response.json()["id"])).json()

    async def test_every_route_authorizes(self):
        routes = [("GET", "/capabilities"), ("GET", "/project"), ("GET", "/project/export"), ("POST", "/project"), ("POST", "/project/import"),
                  ("POST", "/actions"), ("POST", "/subtitles/import"), ("GET", "/audit"), ("GET", "/sources"), ("GET", "/sources/final"), ("GET", "/preview/final"),
                  ("POST", "/render"), ("POST", "/export"), ("GET", "/jobs/" + "a"*32), ("DELETE", "/jobs/" + "a"*32), ("GET", "/outputs/" + "a"*32)]
        for method, route in routes:
            with self.subTest(route=route):
                response = await self.client.request(method, self.prefix + route, headers={"X-Token": "bad"})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.calls[-1][1], method in {"POST", "DELETE"})
        response = await self.client.get("/api/tasks/other/studio/project")
        self.assertEqual(response.status_code, 403)

    async def test_capabilities_api_matches_classified_inventory(self):
        response = await self.client.get(self.prefix + "/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_count"], 113)
        self.assertEqual(response.json()["tools"], capabilities()["tools"])

    async def test_missing_publication_evidence_blocks_legacy_and_mode_exports(self):
        await self.save()
        for name in self.publication_originals:
            (self.root / name).unlink()
        try:
            with patch("backend.studio.render_project") as render:
                for contract in (False, True):
                    self.record.mode_contract = contract
                    for route in ("/render", "/export"):
                        with self.subTest(mode_contract=contract, route=route):
                            response = await self.client.post(self.prefix + route, json={"expected_revision": 1})
                            assert_publication_rejected(self, response, "PUBLICATION_QC_UNAVAILABLE")
                render.assert_not_called()
            # Raw authorized preview remains separate from publication.
            preview = await self.client.get(self.prefix + "/preview/final", headers={"Range": "bytes=0-31"})
            self.assertEqual(preview.status_code, 206)
            self.assertEqual(preview.content, (self.root / "final.mp4").read_bytes()[:32])
            self.assertFalse((self.root / "studio/jobs").exists())
            self.assertFalse(_BUSY)
        finally:
            del self.record.mode_contract
            for name, content in self.publication_originals.items():
                (self.root / name).write_bytes(content)

    async def test_real_export_download_and_readonly_source_range(self):
        await self.save()
        job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"resolution": 360, "fps": 25, "aspect": "9:16"}}))
        self.assertEqual(job["state"], "succeeded", job)
        output = await self.client.get(self.prefix + "/outputs/" + job["id"])
        self.assertEqual(output.status_code, 200)
        self.assertEqual(len(output.content), job["result"]["bytes"])
        self.assertNotIn(str(self.root), json.dumps(job))
        response = await self.client.get(self.prefix + "/preview/final", headers={"Range": "bytes=0-31"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(len(response.content), 32)
        self.assertEqual((self.root / "report.json").read_bytes(), self.publication_originals["report.json"])

    async def test_legacy_historical_output_survives_both_revisions(self):
        job = await self.finish(await self.client.post(self.prefix + "/export", json={
            "expected_revision": 0, "options": {"format": "png", "resolution": 360}}))
        self.assertEqual(job["state"], "succeeded", job)
        original = await self.client.get(self.prefix + "/outputs/" + job["id"])
        self.assertEqual(original.status_code, 200)
        self.assertEqual((await self.save()).status_code, 200)
        self.record.revision = 1
        rejected = await self.client.get(self.prefix + "/outputs/" + job["id"])
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json()["detail"]["code"], "publication_checks_required")
        confirm_synthetic_publication(self, self.record)
        output = await self.client.get(self.prefix + "/outputs/" + job["id"])
        self.assertEqual(output.status_code, 200)
        self.assertEqual(output.content, original.content)
        metadata = await self.client.get(self.prefix + "/jobs/" + job["id"])
        self.assertEqual(metadata.json(), job)

    async def test_historical_output_range_stays_private_without_account_approval(self):
        job = await self.finish(await self.client.post(self.prefix + "/export", json={
            "expected_revision": 0, "options": {"format": "png", "resolution": 360}}))
        self.assertEqual(job["state"], "succeeded", job)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test",
                                     headers={"X-Token": "valid"}) as client:
            self.assertEqual((await self.save()).status_code, 200)
            output = await client.get(self.prefix + "/outputs/" + job["id"])
            self.assertEqual(output.status_code, 200)
            job_path = self.root / "studio/jobs" / (job["id"] + ".json")
            metadata = job_path.read_bytes()
            self.record.revision = 1
            # Even a legacy download needs CURRENT revision-bound confirmation.
            rejected = await client.get(self.prefix + "/outputs/" + job["id"])
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(rejected.json()["detail"]["code"], "publication_checks_required")
            confirm_synthetic_publication(self, self.record)
            ranged = await client.get(self.prefix + "/outputs/" + job["id"], headers={"Range": "bytes=0-31"})
            self.assertEqual(ranged.status_code, 206)
            self.assertEqual(ranged.content, output.content[:32])
            self.assertEqual(ranged.headers["content-range"], f"bytes 0-31/{len(output.content)}")
            self.assertEqual((await client.get(self.prefix + "/jobs/" + job["id"])).json(), job)
            self.assertEqual(job_path.read_bytes(), metadata)
            self.assertTrue((self.root / "studio/outputs/r0" / job["id"] / "output.png").is_file())
            with patch("backend.studio.FileResponse") as file_response:
                self.assertEqual((await client.get(self.prefix + "/outputs/" + job["id"],
                    headers={"X-Token": "bad", "X-Authenticated-User": "operator"})).status_code, 403)
                file_response.assert_not_called()
            # Removing approval gates must not expose failed/unfinished bytes.
            for state in ("queued", "running", "failed", "cancelled", "interrupted"):
                with self.subTest(state=state):
                    changed = dict(job)
                    changed["state"] = state
                    try:
                        job_path.write_text(json.dumps(changed), encoding="utf-8")
                        with patch("backend.studio.FileResponse") as file_response:
                            response = await client.get(self.prefix + "/outputs/" + job["id"])
                            self.assertEqual(response.status_code, 409)
                            file_response.assert_not_called()
                    finally:
                        job_path.write_bytes(metadata)

    async def test_revision_history_persists_and_locks(self):
        self.assertEqual((await self.save()).status_code, 200)
        stale = await self.save()
        self.assertEqual(stale.status_code, 409)
        split = await self.client.post(self.prefix + "/actions", json={"expected_revision": 1, "op": "split", "clip_id": "c", "at": 0.25, "new_id": "right"})
        self.assertEqual(split.status_code, 200, split.text)
        for op, rev, size in (("undo", 2, 1), ("redo", 3, 2)):
            result = await self.client.post(self.prefix + "/actions", json={"expected_revision": rev, "op": op})
            self.assertEqual(len(result.json()["project"]["tracks"][0]["clips"]), size)
        app = FastAPI()
        app.include_router(create_studio_router(self.settings, self.manager, self.authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-Token": "valid"}) as other:
            result = await other.get(self.prefix + "/project")
            self.assertEqual(result.json()["revision"], 4)
        result = await self.client.post(self.prefix + "/actions", json={"expected_revision": 4, "op": "track", "track_id": "v", "locked": True})
        self.assertEqual(result.status_code, 200)
        result = await self.client.post(self.prefix + "/actions", json={"expected_revision": 5, "op": "delete", "clip_id": "c"})
        self.assertEqual(result.status_code, 409)
        self.assertEqual((await self.save(revision=5)).status_code, 409)
        records = (await self.client.get(self.prefix + "/audit")).json()["records"]
        self.assertEqual(len(records), 5)

    async def test_concurrent_revision_only_one_wins(self):
        responses = await asyncio.gather(self.save(), self.save())
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])

    async def test_metadata_srt_import_revision_and_history(self):
        project = base_project().model_dump()
        project.update(groups=[{"id": "group", "name": "A"}], assets=[{"source_id": "final", "rating": 4, "tags": ["news"]}],
                       workspace={"layout": "audio", "timeline_zoom": 2, "shortcuts": {"split": "Ctrl+B"}})
        project["tracks"][0]["group_id"] = "group"
        response = await self.save(Project.model_validate(project))
        self.assertEqual(response.status_code, 200, response.text)
        fetched = (await self.client.get(self.prefix + "/project/export")).json()["project"]
        self.assertEqual(fetched["assets"][0]["rating"], 4)
        self.assertEqual(fetched["workspace"]["shortcuts"], {"split": "Ctrl+B"})
        body = {"expected_revision": 1, "track_id": "captions", "srt": "1\n00:00:00,000 --> 00:00:00,500\nImported"}
        response = await self.client.post(self.prefix + "/subtitles/import", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)
        self.assertEqual(response.json()["project"]["tracks"][1]["clips"][0]["text"], "Imported")
        self.assertEqual((await self.client.post(self.prefix + "/subtitles/import", json=body)).status_code, 409)
        body.update(expected_revision=2, srt="invalid")
        self.assertEqual((await self.client.post(self.prefix + "/subtitles/import", json=body)).status_code, 422)
        undo = await self.client.post(self.prefix + "/actions", json={"expected_revision": 2, "op": "undo"})
        self.assertEqual(len(undo.json()["project"]["tracks"]), 1)
        self.assertEqual(undo.json()["project"]["groups"], fetched["groups"])
        project["assets"][0]["source_id"] = "unknown"
        self.assertEqual((await self.save(Project.model_validate(project), revision=3)).status_code, 422)

    async def test_safe_import_unknown_path_and_resource(self):
        data = base_project().model_dump()
        data["tracks"][0]["clips"][0]["source_id"] = "missing"
        result = await self.client.post(self.prefix + "/project/import", json={"expected_revision": 0, "project": data})
        self.assertEqual(result.status_code, 422)
        result = await self.client.post(self.prefix + "/project", content=b" " * (256*1024+1))
        self.assertEqual(result.status_code, 413)
        result = await self.client.get(self.prefix + "/sources/task_state")
        self.assertEqual(result.status_code, 404)
        result = await self.client.get(self.prefix + "/outputs/not-a-uuid")
        self.assertEqual(result.status_code, 404)

    async def test_symlink_and_outside_upload_rejected(self):
        outside = self.data / "outside.mp4"
        shutil.copyfile(self.root / "final.mp4", outside)
        self.record.uploads = [SimpleNamespace(path=outside)]
        with self.assertRaises(HTTPException):
            catalog(self.record)
        self.record.uploads = []
        norm = self.root / "norm"
        norm.mkdir()
        try:
            (norm / "norm_0.mp4").symlink_to(outside)
        except OSError:
            return  # Windows symlink privilege is host-controlled; outside-path assertion still runs.
        with self.assertRaises(HTTPException):
            catalog(self.record)

    async def test_busy_and_cancel_cleanup(self):
        await self.save()
        entered = asyncio.Event()

        async def blocked(project, options, sources, work, **kwargs):
            (work / "partial.mp4").write_bytes(b"not a result")
            entered.set()
            await asyncio.Event().wait()

        with patch("backend.studio.render_project", side_effect=blocked):
            first = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(first.status_code, 202)
            await entered.wait()
            self.assertTrue(studio_task_busy(self.root))
            second = await self.client.post(self.prefix + "/export", json={"expected_revision": 1})
            self.assertEqual(second.status_code, 409)
            job_id = first.json()["id"]
            result = await self.client.delete(self.prefix + "/jobs/" + job_id)
            self.assertEqual(result.json()["state"], "cancelled")
            self.assertFalse(list((self.root / "studio/outputs").rglob("partial.mp4")))
            self.assertFalse(_BUSY)

    async def test_simple_export_no_subtitles_clean_or_fail(self):
        result = await self.client.post(self.prefix + "/export", json={"expected_revision": 0, "options": {"resolution": 360, "subtitles": "none"}})
        self.assertEqual(result.status_code, 422)
        self.assertFalse(_BUSY)
        ffmpeg("-y", "-i", str(self.root / "final.mp4"), "-an", "-c:v", "copy", str(self.root / "video_only.mp4"))
        ffmpeg("-y", "-i", str(self.root / "final.mp4"), "-vn", "-c:a", "copy", str(self.root / "narration.m4a"))
        job = await self.finish(await self.client.post(self.prefix + "/export", json={"expected_revision": 0, "options": {"resolution": 360, "subtitles": "none"}}))
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["export_semantics"]["audio"], "finished_final_mix")
        self.assertTrue(job["export_semantics"]["warnings"])
        self.assertFalse(list((self.root / "studio/outputs").rglob("studio.ass")))

    async def test_qc_and_disclosure_fail_closed(self):
        await self.save()
        (self.root / "quality_report.json").write_bytes(synthetic_publication_files(blocked=True)["quality_report.json"])
        response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], QC_BLOCKER)
        (self.root / "quality_report.json").write_bytes(self.publication_originals["quality_report.json"])
        (self.root / "shots_annotated.json").write_text(json.dumps([{"media_origin": "generated"}]))
        with self.assertRaises(HTTPException):
            requires_disclosure(self.root)
        response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
        assert_publication_rejected(self, response, "PUBLICATION_REPORT_INVALID")

    async def test_failed_job_never_downloadable(self):
        await self.save(base_project(trim=20))
        job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"resolution": 360}}))
        self.assertEqual(job["state"], "failed")
        result = await self.client.get(self.prefix + "/outputs/" + job["id"])
        self.assertEqual(result.status_code, 409)
        self.assertFalse(list((self.root / "studio/outputs").rglob("output.mp4")))

    async def test_disk_budget_and_active_pipeline(self):
        self.record.status = "running"
        self.assertEqual((await self.save()).status_code, 409)
        self.record.status = "done"
        await self.save()
        with patch("backend.studio.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 507)

    async def test_simple_export_subtitle_styles_and_timed_files(self):
        ffmpeg("-y", "-i", str(self.root / "final.mp4"), "-an", "-c:v", "copy", str(self.root / "video_only.mp4"))
        ffmpeg("-y", "-i", str(self.root / "final.mp4"), "-vn", "-c:a", "copy", str(self.root / "narration.m4a"))
        (self.root / "timings.json").write_text(json.dumps([{"start": 0.2, "end": 1.8, "text": "Caption 字幕"}]), encoding="utf-8")
        for style in ("standard", "large"):
            job = await self.finish(await self.client.post(self.prefix + "/export", json={"expected_revision": 0, "options": {"resolution": 360, "subtitles": style}}))
            self.assertEqual(job["state"], "succeeded", job)
            script = self.root / "studio/outputs/r0" / job["id"] / "studio.ass"
            if style == "large":
                self.assertIn(r"\fs21.00", script.read_text(encoding="utf-8"))
            else:
                self.assertFalse(script.exists(), "standard must preserve finished final, not redraw captions")
        job = await self.finish(await self.client.post(self.prefix + "/export", json={"expected_revision": 0, "options": {"format": "srt"}}))
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["export_semantics"], {"picture": "none", "audio": "none", "framing": "not_applicable", "warnings": []})
        text = (await self.client.get(self.prefix + "/outputs/" + job["id"])).text
        self.assertIn("00:00:00,200 --> 00:00:01,800", text)

    async def test_restart_marks_interrupted_and_cleans(self):
        jobs = self.root / "studio/jobs"
        jobs.mkdir(parents=True)
        job_id = "b" * 32
        work = self.root / "studio/outputs/r0" / job_id
        work.mkdir(parents=True)
        (work / "output.mp4").write_bytes(b"partial")
        (jobs / f"{job_id}.json").write_text(json.dumps({"id": job_id, "revision": 0, "state": "running"}))
        response = await self.client.get(self.prefix + "/jobs/" + job_id)
        self.assertEqual(response.json()["state"], "interrupted")
        self.assertFalse(work.exists())

    async def test_source_change_discards_real_render(self):
        await self.save()
        original = render_project

        async def changing(*args, **kwargs):
            result = await original(*args, **kwargs)
            self.record.revision += 1
            return result

        with patch("backend.studio.render_project", side_effect=changing):
            job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"resolution": 360}}))
        self.assertEqual(job["state"], "failed")
        self.assertIn("changed", job["error"])
        self.assertFalse(list((self.root / "studio/outputs").rglob("output.mp4")))

    async def test_generated_manifest_audio_rejected_and_video_disclosed(self):
        await self.save()
        manifest = {"schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence", "items": [
            {"sentence_id": 0, "beat_id": 0, "shot_id": 0, "mode": "video", "prompt_sha256": "a" * 64, "disclosure_text": "AI生成示意画面"}]}
        (self.root / "generated_media_disclosure.json").write_text(json.dumps(manifest))
        confirm_synthetic_publication(self, self.record, generated=True)
        response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"format": "mp3"}})
        self.assertEqual(response.status_code, 422)
        job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"resolution": 360}}))
        self.assertEqual(job["state"], "succeeded", job)
        output_manifest = self.root / "studio/outputs/r1" / job["id"] / "manifest.json"
        self.assertEqual(json.loads(output_manifest.read_text(encoding="utf-8"))["disclosure_intervals"], [[0, 0.5]])

    async def test_global_capacity_and_job_quota(self):
        await self.save()
        _BUSY.update({"other1", "other2"})
        try:
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 429)
        finally:
            _BUSY.difference_update({"other1", "other2"})
        jobs = self.root / "studio/jobs"
        jobs.mkdir()
        for i in range(20):
            (jobs / f"{i:032x}.json").write_text("{}")
        response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()