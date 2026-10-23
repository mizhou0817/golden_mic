"""Regressions for the 2026-10-22 pass that made the code and the design documents agree.

Pure or in-memory checks only: synthetic inputs, no provider call, media tool,
network, service start or child process.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import re
import struct
import tempfile
import typing
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend import drafts, graphics, main, mode_pipeline, studio
from backend.admission import TOMBSTONE_SECONDS
from backend.config import Settings
from backend.matching import ShotShortageError
from backend.media import descendants_in_kill_order, kill_windows_descendants
from backend.models import TaskState, TaskStatusResponse
from backend.production_modes import LIMITS, MATCH_LOW, MATCH_OK, PACING_CPM
from backend.providers.generative import GenerativeFillProvider
from backend.providers.llm import LLMConfigurationError, LLMProvider
from backend.providers.local_speech import SpeechUnavailable
from backend.providers.vision import VisionConfigurationError, create_vision_provider
from backend.quality import MAX_VISUAL_CLIP_SECONDS
from backend.studio_render import ExportOptions, Project, subtitle_document
from backend.task_manager import TaskManager, TaskRecord
from backend.uploads import CHUNK_SIZE, UploadStore

ROOT = Path(__file__).resolve().parents[1]


def _settings(root: Path, **values) -> Settings:
    return Settings(_env_file=None, app_env="test", data_dir=root / "tasks", asr_cache_dir=root / "cache",
                    min_free_disk_gb=0, **values)


def _literal_values(annotation) -> set[str]:
    values: set[str] = set()
    for argument in typing.get_args(annotation):
        values |= _literal_values(argument) if typing.get_args(argument) else {argument} if isinstance(argument, str) else set()
    return values


def _glyph_ink_heights(font: Path, text: str) -> tuple[int, dict[str, int]]:
    """(line box, {character: ink height}) in font units, read straight from the TrueType tables."""
    data = font.read_bytes()
    tables = {data[12 + 16 * i: 16 + 16 * i].decode("latin-1"): struct.unpack(">I", data[20 + 16 * i: 24 + 16 * i])[0]
              for i in range(struct.unpack(">H", data[4:6])[0])}
    os2 = tables["OS/2"]
    win_ascent, win_descent = struct.unpack(">HH", data[os2 + 74: os2 + 78])
    long_offsets = struct.unpack(">h", data[tables["head"] + 50: tables["head"] + 52])[0] == 1
    cmap, groups = tables["cmap"], []
    for index in range(struct.unpack(">H", data[cmap + 2: cmap + 4])[0]):
        subtable = cmap + struct.unpack(">I", data[cmap + 8 + 8 * index: cmap + 12 + 8 * index])[0]
        if struct.unpack(">H", data[subtable: subtable + 2])[0] == 12:
            count = struct.unpack(">I", data[subtable + 12: subtable + 16])[0]
            groups = [struct.unpack(">III", data[subtable + 16 + 12 * g: subtable + 28 + 12 * g]) for g in range(count)]
    heights = {}
    for char in set(text):
        glyph = next(first + ord(char) - start for start, end, first in groups if start <= ord(char) <= end)
        loca = tables["loca"]
        offset = (struct.unpack(">I", data[loca + 4 * glyph: loca + 4 * glyph + 4])[0] if long_offsets
                  else 2 * struct.unpack(">H", data[loca + 2 * glyph: loca + 2 * glyph + 2])[0])
        _, _, y_min, _, y_max = struct.unpack(">hhhhh", data[tables["glyf"] + offset: tables["glyf"] + offset + 10])
        heights[char] = y_max - y_min
    return win_ascent + win_descent, heights


def _mode_fixture():
    """Two sentences (a quote, then narration) with a speaker, as the mode export tests use them."""
    from backend.models import EditingPreferences, MatchPlanItem, SentenceTiming, TTSWordTiming
    from backend.production_modes import QuoteTake, Speaker
    text = "abcdefghijklmnopqrSTUVWXYZ"
    preferences = EditingPreferences(caption_style="big", quote_caption="asr", lower_third=True, news_graphics=True)
    timings = [
        SentenceTiming(sentence_id=0, text=text, audio_path="unit0.wav", duration=3, start=0, end=3, gap_after=0, audio_kind="sync",
                       words=[TTSWordTiming(text=text[:18], start=.31, end=1.13), TTSWordTiming(text=text[18:], start=1.77, end=2.81)]),
        SentenceTiming(sentence_id=1, text="Narration", audio_path="unit1.wav", duration=4, start=3, end=7, gap_after=0,
                       words=[TTSWordTiming(text="Narration", start=.23, end=3.61)]),
    ]
    plan = [MatchPlanItem(sentence_id=0, text=text, kind="quote", shot_id=0, confidence=1, candidates=[],
                          source=QuoteTake(take_id="take", upload_id="u", start=0., end=3., speaker_id="s", asr_text=text, score=1.)),
            MatchPlanItem(sentence_id=1, text="Narration", shot_id=1, confidence=1, candidates=[])]
    return preferences, timings, plan, [Speaker(id="s", name="Alice", title="Reporter")]


class StatusContractTests(unittest.TestCase):
    def test_every_error_kind_a_failure_can_carry_is_in_the_status_contract(self):
        allowed = _literal_values(TaskStatusResponse.model_fields["errorKind"].annotation)
        produced = {SpeechUnavailable.error_kind, ShotShortageError.error_kind,
                    mode_pipeline.QuoteMissingError.error_kind, mode_pipeline.ModeQualityError.error_kind, "transient"}
        self.assertLessEqual(produced, allowed)
        self.assertIn("speech_prerequisite", allowed)


class PublicLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_published_limit_comes_from_where_it_is_enforced(self):
        with tempfile.TemporaryDirectory(prefix="v2-limits-") as directory:
            settings = _settings(Path(directory), anonymous_session_task_rate_limit_per_hour=3,
                                 anonymous_ip_task_rate_limit_per_hour=7, anonymous_global_task_rate_limit_per_hour=33,
                                 tts_news_rate_tolerance=0.2)
            with patch.object(main, "settings", settings):
                limits = await main.public_limits()
        self.assertEqual((limits.browser_submissions_per_hour, limits.ip_submissions_per_hour,
                          limits.global_submissions_per_hour), (3, 7, 33))
        self.assertEqual(limits.rate_tolerance, 0.2)
        self.assertEqual((limits.quote_max_sec, limits.quote_warn_sec, limits.quote_min_sec),
                         (LIMITS["quote_max_seconds"], LIMITS["quote_warn_seconds"], LIMITS["quote_min_seconds"]))
        self.assertEqual((limits.match_ok, limits.match_low), (MATCH_OK, MATCH_LOW))
        self.assertEqual((limits.max_script_length, limits.script_soft_max), (LIMITS["script_max_chars"], LIMITS["script_soft_chars"]))
        self.assertEqual(limits.pacing_cpm, PACING_CPM)
        self.assertEqual((limits.chunk_size, limits.max_visual_sec), (CHUNK_SIZE, MAX_VISUAL_CLIP_SECONDS))
        self.assertEqual((limits.retention_hours, limits.draft_ttl_hours, limits.tombstone_days),
                         (72, 24, TOMBSTONE_SECONDS // 86400))
        # No field falls back to the model's compatibility defaults.
        self.assertEqual(limits.model_fields_set, set(type(limits).model_fields))


class _StalledBody:
    """A client that stops sending mid-body: asyncio.timeout reports that as TimeoutError."""

    async def stream(self):
        yield b'{"partial":'
        raise TimeoutError


class RequestBodyTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_stalled_body_is_a_408_not_a_500(self):
        for reader in (main._bounded_json, drafts.bounded_json):
            with self.subTest(reader=reader.__module__), self.assertRaises(HTTPException) as caught:
                await reader(_StalledBody())
            self.assertEqual(caught.exception.status_code, 408)


class DrainSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """deploy/docker/update.sh waits for tasks and for uploads still being transcribed; a restart cancels the latter."""

    async def test_an_upload_counts_until_its_job_is_done(self):
        loop = asyncio.get_running_loop()
        running, finished = loop.create_future(), loop.create_future()
        finished.set_result(None)
        store = SimpleNamespace(_jobs={"a": running, "b": finished})
        self.assertEqual(UploadStore.active_jobs(store), 1)
        running.set_result(None)
        self.assertEqual(UploadStore.active_jobs(store), 0)
        self.assertEqual(UploadStore.active_jobs(SimpleNamespace(_jobs={})), 0)

    async def test_every_drain_endpoint_reports_upload_jobs_beside_the_task_counts(self):
        request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
        script = (ROOT / "deploy" / "docker" / "update.sh").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="v2-drain-") as directory:
            settings = _settings(Path(directory))
            settings.data_dir.mkdir()
            manager = TaskManager(settings)
            asking = (("GET", main.drain_status), ("POST", main.begin_drain), ("DELETE", main.cancel_drain))
            # Asking how much work is running must not create the upload store (its directory and its lock).
            with patch.object(main, "task_manager", manager), \
                    patch.object(main, "_uploads", side_effect=AssertionError("the upload store was created")):
                for store, expected in ((None, 0), (SimpleNamespace(active_jobs=lambda: 3), 3)):
                    with patch.object(main, "_upload_store", store):
                        for method, endpoint in asking:
                            with self.subTest(method=method, uploads=expected):
                                body = json.loads((await endpoint(request)).body)
                                self.assertEqual(body["upload_jobs"], expected)
                                self.assertEqual(body["active_tasks"], 0, "an upload is not a task")
                                self.assertEqual(body["draining"], method == "POST")
                                # The script adds up exactly these two keys.
                                for key in ("active_tasks", "upload_jobs"):
                                    self.assertIn(key, body)
                                    self.assertIn(f'"{key}"', script)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_configuration_errors_name_the_keys_the_operator_actually_sets(self):
        with tempfile.TemporaryDirectory(prefix="v2-provider-keys-") as directory:
            settings = _settings(Path(directory), llm_provider="kimi", vision_provider="kimi", kimi_api_key="",
                                 volcengine_llm_api_keys="")
            async with LLMProvider.from_settings(settings) as kimi, LLMProvider.for_script_segmentation(settings) as segmenter:
                for provider in (kimi, segmenter):
                    with self.assertRaises(LLMConfigurationError) as caught:
                        provider.validate_configuration()
                    self.assertIn("KIMI_API_KEY", str(caught.exception))
                    self.assertNotIn("LLM_API_KEY", str(caught.exception))
            async with LLMProvider.from_settings(settings.model_copy(update={"llm_provider": "volcengine"})) as ark:
                with self.assertRaises(LLMConfigurationError) as caught:
                    ark.validate_configuration()
                self.assertIn("VOLCENGINE_LLM_API_KEYS", str(caught.exception))
            async with create_vision_provider(settings) as vision:
                with self.assertRaises(VisionConfigurationError) as caught:
                    vision.validate_configuration()
                self.assertIn("KIMI_API_KEY", str(caught.exception))
                self.assertNotIn("VISION_API_KEY", str(caught.exception))

    def test_generated_fill_defaults_to_video_like_the_setting(self):
        provider = GenerativeFillProvider(base_url="https://ark.invalid", api_key="k", image_model="i", video_model="v",
                                          client=SimpleNamespace())
        self.assertEqual(provider.mode, Settings.model_fields["generative_fill_mode"].default)
        self.assertEqual(provider.mode, "video")

    def test_every_v2_speech_recognition_call_is_a_single_billable_attempt(self):
        sites = 0
        for name in ("uploads.py", "media_input.py", "workbench.py", "mode_pipeline.py"):
            for node in ast.walk(ast.parse((ROOT / "backend" / name).read_text(encoding="utf-8"))):
                if not isinstance(node, ast.AsyncWith):
                    continue
                for item in node.items:
                    call = item.context_expr
                    if not (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "create_asr_provider"):
                        continue
                    sites += 1
                    alias = item.optional_vars.id
                    first = node.body[0]
                    # Before any other use: `if hasattr(provider, "max_retries"): provider.max_retries = 0`.
                    self.assertIsInstance(first, ast.If, f"{name}:{node.lineno}")
                    assigned = [n for n in ast.walk(first) if isinstance(n, ast.Assign)
                                and any(isinstance(t, ast.Attribute) and t.attr == "max_retries"
                                        and getattr(t.value, "id", None) == alias for t in n.targets)
                                and isinstance(n.value, ast.Constant) and n.value.value == 0]
                    self.assertTrue(assigned, f"{name}:{node.lineno}")
        self.assertEqual(sites, 5)


class LoudnessTests(unittest.TestCase):
    def test_v2_limiter_target_leaves_headroom_under_the_qc_ceiling_and_units_use_the_same_values(self):
        self.assertEqual((mode_pipeline.V2_TARGET_LUFS, mode_pipeline.V2_TARGET_LRA), (-20, 5))
        self.assertLess(mode_pipeline.V2_TRUE_PEAK_TARGET_DBFS, mode_pipeline.V2_TRUE_PEAK_MAX_DBFS)
        units = (ROOT / "backend/tts_pipeline.py").read_text(encoding="utf-8")
        self.assertIn(f"loudnorm=I={mode_pipeline.V2_TARGET_LUFS}:LRA={mode_pipeline.V2_TARGET_LRA}:"
                      f"TP={mode_pipeline.V2_TRUE_PEAK_TARGET_DBFS}:", units)


class DisclosureTests(unittest.TestCase):
    def render(self, duration: float, intervals) -> str:
        with tempfile.TemporaryDirectory(prefix="v2-disclosure-") as directory:
            path = graphics.generate_news_graphics(Path(directory), topic="", headline="", total_duration=duration,
                                                   disclosure_intervals=intervals)
            return path.read_text(encoding="utf-8") if path else ""

    def test_any_generated_shot_labels_the_video_from_its_first_frame(self):
        content = self.render(8.0, [(5.0, 8.0)])
        self.assertIn("Dialogue: 10,0:00:00.00,0:00:03.00,GfxDisclosure,,0,0,0,,{\\an9\\bord1.5\\shad1}本片含AI生成内容", content)
        self.assertIn("Dialogue: 10,0:00:05.00,0:00:08.00,GfxDisclosure,,0,0,0,,{\\an9\\bord1.5\\shad1}AI生成示意画面", content)
        # No fixed position: libass stacks a shot label under the opening one instead of printing over it.
        self.assertNotIn("\\pos(", content.split("[Events]")[1])
        self.assertIn("0:00:00.00,0:00:01.50,GfxDisclosure,,0,0,0,,{\\an9\\bord1.5\\shad1}本片含", self.render(1.5, [(0.0, 1.5)]))
        self.assertEqual(self.render(8.0, []), "")

    def test_mode_graphics_label_the_opening_too(self):
        from backend.models import EditingPreferences, SentenceTiming
        timing = SentenceTiming(sentence_id=0, text="旁白", audio_path="a.wav", duration=6.0, start=0.0, end=6.0)
        with tempfile.TemporaryDirectory(prefix="v2-disclosure-") as directory:
            path, _ = graphics.generate_mode_graphics(Path(directory), [timing], [], [], EditingPreferences(lower_third=False),
                                                      disclosure_intervals=[(4.0, 6.0)])
            content = path.read_text(encoding="utf-8")
        self.assertIn("0:00:00.00,0:00:03.00,GfxDisclosure,,0,0,0,,{\\an9\\bord1.5\\shad1}本片含AI生成内容", content)
        self.assertEqual(content.count("AI生成示意画面"), 1)

    def test_label_text_is_at_least_five_percent_of_the_short_side(self):
        self.assertIn(f"Style: GfxDisclosure,Noto Sans SC,{graphics.DISCLOSURE_FONT_SIZE},", graphics._header())
        text = graphics.OPENING_DISCLOSURE_TEXT + "AI生成示意画面（本片含AI生成内容）"
        fonts = sorted((ROOT / "backend/assets/fonts").glob("NotoSansSC-*.ttf"))
        self.assertEqual(len(fonts), 2)
        for font in fonts:
            # libass scales the font's line box (win ascent + descent) to the ASS font size.
            line_box, heights = _glyph_ink_heights(font, text)
            for char, height in heights.items():
                if "一" <= char <= "鿿":
                    with self.subTest(font=font.name, char=char):
                        self.assertGreaterEqual(graphics.DISCLOSURE_FONT_SIZE * height / line_box, 0.05 * 1080)

    def test_labels_sit_below_the_title_and_the_topic_band_so_no_export_shape_prints_over_them(self):
        # Vertical positions all scale with the frame height, so comparing ASS units covers 16:9, 1:1 and 9:16 at once.
        # (Measured with libass: at margin 40 the opening label covered 54% of the title in 1:1 and 94% in 9:16.)
        title = next(line for line in graphics._mode_caption_header("standard").split("\n")
                     if line.startswith("Style: ModeTitle,")).split(",")
        title_bottom = int(title[21]) + int(title[2]) + int(title[16])  # MarginV + font size + outline
        style = next(line for line in graphics._header().split("\n") if line.startswith("Style: GfxDisclosure,")).split(",")
        self.assertEqual(style[18], "9")  # anchored top right: the label stays on the right edge
        self.assertEqual(int(style[21]), graphics.DISCLOSURE_MARGIN_V)
        self.assertGreaterEqual(graphics.DISCLOSURE_MARGIN_V, title_bottom)
        with tempfile.TemporaryDirectory(prefix="v2-topic-") as directory:
            graphics.generate_news_graphics(Path(directory), topic="话题", headline="", total_duration=6.0)
            band = next(line for line in (Path(directory) / "graphics.ass").read_text(encoding="utf-8").split("\n")
                        if "GfxBand" in line and "\\p1" in line)
        top = int(re.search(r"\\pos\(\d+,(\d+)\)", band).group(1))
        height = int(re.search(r"l \d+ (\d+) l 0 \1", band).group(1))
        shadow = int(re.search(r"\\shad(\d+)", band).group(1))
        self.assertGreaterEqual(graphics.DISCLOSURE_MARGIN_V, top + height + shadow)

    def test_persistent_studio_line_is_3_5_percent_of_the_height_and_fits_every_export_shape(self):
        project = Project.model_validate({"tracks": [{"id": "main", "type": "video", "clips": [
            {"id": "clip", "source_id": "final", "duration": 1}]}]})
        text = "AI生成示意画面（本片含AI生成内容）"
        font = sorted((ROOT / "backend/assets/fonts").glob("NotoSansSC-*.ttf"))[0]
        line_box, _ = _glyph_ink_heights(font, text)
        data = font.read_bytes()
        head = next(struct.unpack(">I", data[20 + 16 * i: 24 + 16 * i])[0] for i in range(struct.unpack(">H", data[4:6])[0])
                    if data[12 + 16 * i: 16 + 16 * i] == b"head")
        units_per_em = struct.unpack(">H", data[head + 18: head + 20])[0]
        for aspect in ("16:9", "9:16", "1:1"):
            options = ExportOptions(format="mp4", resolution=720, aspect=aspect)
            width, height = options.dimensions
            document = subtitle_document(project, options, disclosure=True)
            size = float(re.search(r"\\fs([0-9.]+)\\", document).group(1))
            # Unchanged since the line was introduced: it follows the frame height, which makes it about 5% tall in 9:16.
            self.assertEqual(size, round(max(16, height * 0.035), 2), aspect)
            # libass maps the font's line box to the font size, so one em is size * units_per_em / line_box wide. One em
            # per character, pinned 20 px from the left edge, is an upper bound of the line's width.
            self.assertLess(20 + len(text) * size * units_per_em / line_box, width, aspect)


class FirstGenerationOverlayTests(unittest.TestCase):
    """graphics.ass is hashed and regenerated on export, so works made before the opening label keep their bytes."""

    # SHA-256 (newlines as LF) of what the code before the opening label wrote for the inputs below.
    FIRST_GENERATION = {"mode": "4c97cf8581177c21967157e4d802d18bbebf903e3645653c0c0564a9c3297a96",
                        "news": "95b61cf76d885e915979214f56b011cf74af36ef40a1a826ed5a27d046a47e51"}

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

    def render(self, root: Path, **flags) -> dict[str, Path]:
        preferences, timings, plan, speakers = _mode_fixture()
        mode, _ = graphics.generate_mode_graphics(root, timings, plan, speakers, preferences, title="Headline",
                                                  disclosure_intervals=[(3.5, 6.0)], **flags)
        news = graphics.generate_news_graphics(root, topic="City news", headline="Public service", total_duration=6.0,
                                               disclosure_intervals=[(1.0, 2.5)], output_path=root / "news.ass", **flags)
        return {"mode": mode, "news": news}

    def test_the_first_generation_is_reproduced_byte_for_byte(self):
        with tempfile.TemporaryDirectory(prefix="v2-first-generation-") as directory:
            paths = self.render(Path(directory), disclosure_v2=False)
            self.assertEqual({name: self.digest(path) for name, path in paths.items()}, self.FIRST_GENERATION)
            for path in paths.values():
                self.assertFalse(graphics.uses_disclosure_v2(path.read_bytes()))
                self.assertNotIn(graphics.OPENING_DISCLOSURE_TEXT, path.read_text(encoding="utf-8"))

    def test_the_current_generation_names_itself_and_differs(self):
        with tempfile.TemporaryDirectory(prefix="v2-first-generation-") as directory:
            paths = self.render(Path(directory))
            for name, path in paths.items():
                with self.subTest(name):
                    self.assertTrue(graphics.uses_disclosure_v2(path.read_bytes()))
                    self.assertNotEqual(self.digest(path), self.FIRST_GENERATION[name])
                    self.assertIn(graphics.OPENING_DISCLOSURE_TEXT, path.read_text(encoding="utf-8"))


class StoredOverlayExportTests(unittest.TestCase):
    """studio.prepare_mode_export regenerates graphics.ass and refuses anything but the stored bytes."""

    def write_work(self, root: Path, *, disclosure_v2: bool, v2: bool = True) -> bytes:
        preferences, timings, plan, speakers = _mode_fixture()
        graphics.generate_mode_subtitles(root, timings, plan, preferences, title="Headline", v2=v2)
        _, lower = graphics.generate_mode_graphics(root, timings, plan, speakers, preferences, title="Headline",
                                                   disclosure_intervals=[(3.5, 6.0)], v2=v2, disclosure_v2=disclosure_v2)
        documents = {
            "timings.json": [timing.model_dump(mode="json") for timing in timings],
            "match_plan.json": [item.model_dump(mode="json") for item in plan],
            "script_structure.json": {"title": "Headline"},
            "lower_thirds.json": lower,
            "production_mode.json": {"mode_contract": True, "mode": "mixed", "preferences": preferences.model_dump(mode="json"),
                                     "speakers": [speaker.model_dump(mode="json") for speaker in speakers],
                                     "lower_thirds": lower, "generated_intervals": [[3.5, 6.0]]},
        }
        for name, value in documents.items():
            (root / name).write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
        return (root / "graphics.ass").read_bytes()

    def export(self, root: Path):
        work = root / "work"
        work.mkdir()
        return studio.prepare_mode_export(root, work, ExportOptions(format="mp4", resolution=360, video_bitrate_kbps=1500))

    def test_a_work_finished_before_the_opening_label_exports_with_the_overlay_it_was_made_with(self):
        with tempfile.TemporaryDirectory(prefix="v2-stored-overlay-") as directory:
            root = Path(directory)
            stored = self.write_work(root, disclosure_v2=False)
            exported = self.export(root)
        self.assertFalse(graphics.uses_disclosure_v2(stored))
        self.assertEqual(exported.graphics, stored.decode("utf-8"))
        self.assertNotIn(graphics.OPENING_DISCLOSURE_TEXT, exported.graphics)

    def test_a_current_work_exports_with_the_opening_label(self):
        with tempfile.TemporaryDirectory(prefix="v2-stored-overlay-") as directory:
            root = Path(directory)
            stored = self.write_work(root, disclosure_v2=True)
            exported = self.export(root)
        self.assertTrue(graphics.uses_disclosure_v2(stored))
        self.assertEqual(exported.graphics, stored.decode("utf-8"))
        self.assertIn(graphics.OPENING_DISCLOSURE_TEXT, exported.graphics)

    def test_a_work_made_before_v2_exports_with_its_end_card(self):
        # Before V2 the overlay ended with a headline card; the export has to regenerate it the same way.
        with tempfile.TemporaryDirectory(prefix="v2-stored-overlay-") as directory:
            root = Path(directory)
            stored = self.write_work(root, disclosure_v2=False, v2=False)
            exported = self.export(root)
        self.assertIn(b"GfxEnd", stored)
        self.assertEqual(exported.graphics, stored.decode("utf-8"))

    def test_any_other_stored_overlay_is_still_refused(self):
        claim = "[Script Info]\n; template: " + graphics.DISCLOSURE_TEMPLATE + "\n"
        for name, disclosure_v2, tamper in (
            ("edited first generation", False, lambda data: data.replace("示意".encode(), "别的".encode())),
            ("edited current generation", True, lambda data: data.replace("示意".encode(), "别的".encode())),
            ("first generation claiming to be current", False, lambda data: data.replace(b"[Script Info]\n", claim.encode(), 1)),
            ("missing", True, None),
        ):
            with self.subTest(name), tempfile.TemporaryDirectory(prefix="v2-stored-overlay-") as directory:
                root = Path(directory)
                stored = self.write_work(root, disclosure_v2=disclosure_v2)
                if tamper is None:
                    (root / "graphics.ass").unlink()
                else:
                    changed = tamper(stored)
                    self.assertNotEqual(changed, stored)
                    (root / "graphics.ass").write_bytes(changed)
                with self.assertRaises(HTTPException) as raised:
                    self.export(root)
                self.assertEqual(raised.exception.status_code, 422)


class ProcessTreeTests(unittest.TestCase):
    def test_windows_tree_kill_lists_children_first_and_never_adopts_a_reused_pid(self):
        parents = {10: 1, 11: 10, 12: 11, 13: 10, 20: 99, 30: 11}
        created = {1: 100, 10: 110, 11: 120, 12: 130, 13: 125, 20: 90, 30: 50}
        order = descendants_in_kill_order(1, parents, created.get)
        # 30 names 11 as its parent but is older than 11: Windows reused the number, it is not ours.
        self.assertEqual(sorted(order), [10, 11, 12, 13])
        for pid in order:
            if parents[pid] in order:
                self.assertLess(order.index(pid), order.index(parents[pid]))
        self.assertEqual(descendants_in_kill_order(1, parents, lambda pid: None), [], "unknown start times are never guessed")
        self.assertEqual(descendants_in_kill_order(5, {6: 5, 5: 6}, {5: 1, 6: 2}.get), [6], "a parent loop ends")

    def test_tree_kill_skips_a_process_object_without_a_pid(self):
        # The caller passes getattr(process, "pid", None) and then kills the process itself: test doubles
        # and half-built process objects must never make the tree kill raise.
        for pid in (None, "12", 1.5, object()):
            with self.subTest(pid=pid):
                kill_windows_descendants(pid)


class FailureLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_first_run_keeps_a_sanitised_traceback_in_the_task_log(self):
        with tempfile.TemporaryDirectory(prefix="v2-failure-log-") as directory:
            settings = _settings(Path(directory))
            settings.data_dir.mkdir()
            manager = TaskManager(settings)
            task_dir = settings.data_dir / "task"
            task_dir.mkdir()
            record = TaskRecord("task", task_dir, "标题\n\n正文。", [])

            async def broken(*_args, **_kwargs):
                raise RuntimeError("synthetic provider failure api_key=sk-SECRET")

            with patch("backend.task_manager.run_pipeline", broken):
                await manager._run(record)
            log = (task_dir / "task.log").read_text(encoding="utf-8")
        self.assertEqual((record.status, record.error_kind), (TaskState.failed, "transient"))
        self.assertIn("任务失败：", log)
        self.assertIn("失败详情：", log)
        self.assertIn("Traceback (most recent call last)", log)
        self.assertIn("RuntimeError", log)
        self.assertNotIn("sk-SECRET", log)


if __name__ == "__main__":
    unittest.main()
