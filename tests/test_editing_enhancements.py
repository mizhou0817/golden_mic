import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import httpx

from backend.config import Settings
from backend.models import (
    AnnotatedShot,
    BeatMatch,
    EDLClip,
    EDLItem,
    EditingPreferences,
    MatchCandidate,
    MatchPlanItem,
    Sentence,
    SentenceTiming,
    VisualBeat,
)
from backend.matching import extract_visual_beats
from backend.music import load_music_library, select_music_track
from backend.pipeline import (  # pyright: ignore[reportPrivateUsage]
    _build_finish_options,
    _generated_media_intervals,
    _segment_render_options,
)
from backend.graphics import generate_news_graphics
from backend.providers.generative import (
    GenerativeFillError,
    GenerativeFillProvider,
    apply_generative_fill,
    filter_generated_media_disclosure,
    generative_fill_configured,
    synthesize_fill_clip,
)
from backend.rendering import (
    FinishOptions,
    MusicMixOptions,
    SegmentRenderOptions,
    build_edl,
    render_final_video,
)
from backend.subtitles import generate_ass_subtitles
from backend.tts_pipeline import probe_audio_duration


MUSIC_LIBRARY_DIR = Path(__file__).resolve().parent.parent / "backend" / "assets" / "music"


def _ffmpeg_available() -> bool:
    from shutil import which

    return which("ffmpeg") is not None and which("ffprobe") is not None


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True)


def _probe_stream(path: Path, kind: str) -> dict[str, str]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0" if kind == "video" else "a:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,sample_rate",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


class EditingPreferencesTest(unittest.IsolatedAsyncioTestCase):
    def test_defaults_reproduce_current_behaviour(self) -> None:
        preferences = EditingPreferences()
        self.assertFalse(preferences.background_music)
        self.assertFalse(preferences.motion_effects)
        self.assertFalse(preferences.transitions)
        self.assertFalse(preferences.news_graphics)
        self.assertFalse(preferences.color_consistency)
        self.assertFalse(preferences.generative_fill)
        self.assertEqual(preferences.pacing, "normal")
        self.assertEqual(preferences.tone, "neutral")
        self.assertEqual(preferences.music_mood, "auto")
        self.assertEqual(preferences.custom_instructions, "")

    def test_resolved_music_mood_follows_tone_when_auto(self) -> None:
        self.assertEqual(EditingPreferences(tone="solemn").resolved_music_mood, "solemn")
        self.assertEqual(EditingPreferences(tone="neutral").resolved_music_mood, "neutral")
        self.assertEqual(EditingPreferences(tone="energetic").resolved_music_mood, "uplifting")

    def test_explicit_music_mood_overrides_tone(self) -> None:
        preferences = EditingPreferences(tone="energetic", music_mood="tense")
        self.assertEqual(preferences.resolved_music_mood, "tense")

    def test_pacing_maps_to_chars_per_minute(self) -> None:
        self.assertEqual(EditingPreferences(pacing="slow").target_chars_per_minute, 230)
        self.assertEqual(EditingPreferences(pacing="normal").target_chars_per_minute, 265)
        self.assertEqual(EditingPreferences(pacing="fast").target_chars_per_minute, 290)

    def test_custom_instructions_are_stripped_and_control_chars_rejected(self) -> None:
        self.assertEqual(EditingPreferences(custom_instructions="  多用远景  ").custom_instructions, "多用远景")
        with self.assertRaises(ValueError):
            EditingPreferences(custom_instructions="bad\x07text")

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            EditingPreferences.model_validate({"unknown_flag": True})

    def test_pipeline_segment_options_follow_task_preferences(self) -> None:
        record = SimpleNamespace(
            preferences=EditingPreferences(
                motion_effects=True,
                color_consistency=True,
            )
        )
        options = _segment_render_options(record, Settings())

        self.assertTrue(options.motion)
        self.assertTrue(options.color_consistency)

    async def test_pipeline_finish_options_follow_task_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            record = SimpleNamespace(
                task_dir=task_dir,
                script="城市要闻\n本市公共服务持续改善。",
                preferences=EditingPreferences(
                    tone="energetic",
                    background_music=True,
                    music_mood="uplifting",
                    transitions=True,
                    news_graphics=True,
                ),
            )
            timing = SentenceTiming(
                sentence_id=0,
                text="本市公共服务持续改善。",
                audio_path="tts/sent_0.mp3",
                duration=2.0,
                start=0.0,
                end=2.0,
            )
            settings = Settings(music_library_dir=MUSIC_LIBRARY_DIR)
            options = await _build_finish_options(record, settings, [timing])

            self.assertIsNotNone(options.music)
            self.assertIsNotNone(options.graphics_path)
            self.assertEqual(options.fade_in, 0.5)
            self.assertEqual(options.fade_out, 0.6)
            self.assertTrue((task_dir / "music_selection.json").is_file())
            self.assertTrue((task_dir / "graphics.ass").is_file())

    async def test_generated_disclosure_is_mandatory_without_news_graphics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            record = SimpleNamespace(
                task_dir=task_dir,
                script="城市要闻\n公共服务持续改善。",
                preferences=EditingPreferences(news_graphics=False),
            )
            timing = SentenceTiming(
                sentence_id=0,
                text="公共服务持续改善。",
                audio_path="tts/sent_0.mp3",
                duration=2.0,
                start=0.0,
                end=2.0,
            )
            edl = [
                EDLItem(
                    sentence_id=0,
                    clips=[
                        EDLClip(
                            shot_id=5,
                            src="generated/fill_shot_5.mp4",
                            **{"in": 0.0, "out": 2.0},
                            media_origin="generated",
                        )
                    ],
                    timeline_start=0.0,
                    timeline_end=2.0,
                )
            ]

            options = await _build_finish_options(
                record,
                Settings(),
                [timing],
                edl=edl,
            )

            self.assertIsNotNone(options.graphics_path)
            content = Path(options.graphics_path).read_text(encoding="utf-8")
            self.assertIn("AI生成示意画面", content)
            self.assertNotIn("GfxTopic,,", content)
            self.assertIn("0:00:00.00,0:00:02.00,GfxDisclosure", content)


class MusicLibraryTest(unittest.TestCase):
    def test_bundled_library_exposes_four_moods(self) -> None:
        tracks = load_music_library(MUSIC_LIBRARY_DIR)
        moods = {track.mood for track in tracks}
        self.assertEqual(moods, {"solemn", "neutral", "uplifting", "tense"})
        for track in tracks:
            self.assertTrue(track.path.is_file())

    def test_select_prefers_exact_mood_then_neutral(self) -> None:
        tracks = load_music_library(MUSIC_LIBRARY_DIR)
        self.assertEqual(select_music_track(tracks, "tense").mood, "tense")
        self.assertEqual(select_music_track(tracks, "unknown-mood").mood, "neutral")

    def test_empty_directory_returns_no_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_music_library(Path(directory)), [])


def _build_render_fixture(task_dir: Path, narration_seconds: float) -> list:
    norm_dir = task_dir / "norm"
    norm_dir.mkdir()
    source_path = norm_dir / "norm_0.mp4"
    narration_path = task_dir / "narration.m4a"
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            "testsrc2=s=1920x1080:r=30:d=1.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source_path),
        ]
    )
    _run_ffmpeg(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i",
            f"sine=frequency=300:sample_rate=48000:duration={narration_seconds}",
            "-c:a", "aac", str(narration_path),
        ]
    )
    timing = SentenceTiming(
        sentence_id=0,
        text="这是一条用于验证增强渲染的新闻。",
        audio_path="tts/sent_0.mp3",
        duration=narration_seconds,
        start=0.0,
        end=narration_seconds,
    )
    shot = AnnotatedShot(
        shot_id=0,
        source_index=0,
        source_scene_index=0,
        source_name="input.mp4",
        norm_path="norm/norm_0.mp4",
        start=0.0,
        end=1.2,
        duration=1.2,
        thumb_path="thumbs/shot_0.jpg",
        status="available",
        description="测试镜头",
        scene_type="outdoor",
        keywords=["测试", "镜头", "新闻"],
        quality={"sharp": 0.9, "bright": 0.9},
    )
    match = MatchPlanItem(
        sentence_id=0,
        text=timing.text,
        shot_id=0,
        confidence=0.9,
        alternates=[],
        is_fallback=False,
        candidates=[MatchCandidate(shot_id=0, similarity=1.0)],
    )
    generate_ass_subtitles(task_dir, [timing])
    return build_edl(task_dir, [timing], [match], [shot])


@unittest.skipUnless(_ffmpeg_available(), "FFmpeg/ffprobe required")
class EnhancedRenderTest(unittest.IsolatedAsyncioTestCase):
    async def test_generated_media_disclosure_is_burned_into_final_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            norm_dir = task_dir / "generated"
            norm_dir.mkdir()
            source_path = norm_dir / "fill_shot_7.mp4"
            _run_ffmpeg(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=1920x1080:r=30:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source_path),
                ]
            )
            _run_ffmpeg(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=300:sample_rate=48000:duration=2",
                    "-c:a", "aac", str(task_dir / "narration.m4a"),
                ]
            )
            timing = SentenceTiming(
                sentence_id=0,
                text="用于验证披露烧录。",
                audio_path="tts/sent_0.mp3",
                duration=2.0,
                start=0.0,
                end=2.0,
                gap_after=0.0,
            )
            shot = AnnotatedShot(
                shot_id=7,
                source_index=7,
                source_scene_index=0,
                source_name="generated_fill_7",
                norm_path="generated/fill_shot_7.mp4",
                start=0.0,
                end=2.0,
                duration=2.0,
                status="available",
                media_origin="generated",
                description="AI生成示意画面",
                quality={"sharp": 0.8, "bright": 0.8},
            )
            candidate = MatchCandidate(shot_id=7, similarity=0.0)
            plan = MatchPlanItem(
                sentence_id=0,
                text=timing.text,
                shot_id=7,
                confidence=0.0,
                is_fallback=True,
                candidates=[candidate],
            )
            generate_ass_subtitles(task_dir, [timing])
            edl = build_edl(task_dir, [timing], [plan], [shot])
            graphics_path = generate_news_graphics(
                task_dir,
                topic="",
                headline="",
                total_duration=2.0,
                disclosure_intervals=_generated_media_intervals(edl),
            )

            final_path = await render_final_video(
                task_dir,
                edl,
                lambda fraction, message: None,
                finish_options=FinishOptions(graphics_path=graphics_path),
            )

            capture = cv2.VideoCapture(str(final_path))
            try:
                capture.set(cv2.CAP_PROP_POS_MSEC, 500)
                read, frame = capture.read()
            finally:
                capture.release()
            self.assertTrue(read)
            self.assertIsNotNone(frame)
            assert frame is not None
            disclosure_region = frame[20:100, 1450:1900]
            bright_pixels = cv2.countNonZero(
                cv2.inRange(disclosure_region, (180, 180, 180), (255, 255, 255))
            )
            self.assertGreater(bright_pixels, 100)

    async def test_motion_and_color_preserve_geometry_and_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            edl = _build_render_fixture(task_dir, 2.0)
            final_path = await render_final_video(
                task_dir,
                edl,
                lambda fraction, message: None,
                segment_options=SegmentRenderOptions(color_consistency=True, motion=True, zoom_ratio=0.08),
            )
            video = _probe_stream(final_path, "video")
            self.assertEqual(video["codec_name"], "h264")
            self.assertEqual((video["width"], video["height"]), ("1920", "1080"))
            self.assertEqual(video["avg_frame_rate"], "30/1")
            final_duration = await probe_audio_duration(task_dir / "narration.m4a", task_dir)
            actual = await probe_audio_duration(final_path, task_dir)
            self.assertLessEqual(abs(final_duration - actual), 0.3)

    async def test_background_music_bed_is_mixed_and_ducked(self) -> None:
        tracks = load_music_library(MUSIC_LIBRARY_DIR)
        track = select_music_track(tracks, "neutral")
        self.assertIsNotNone(track)
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            edl = _build_render_fixture(task_dir, 2.0)
            final_path = await render_final_video(
                task_dir,
                edl,
                lambda fraction, message: None,
                finish_options=FinishOptions(
                    music=MusicMixOptions(track=track, mood="neutral", bed_lufs=-32.0, duck_ratio=8.0)
                ),
            )
            self.assertTrue((task_dir / "music_bed.m4a").is_file())
            audio = _probe_stream(final_path, "audio")
            self.assertEqual(audio["codec_name"], "aac")
            self.assertEqual(audio["sample_rate"], "48000")
            narration_duration = await probe_audio_duration(task_dir / "narration.m4a", task_dir)
            actual = await probe_audio_duration(final_path, task_dir)
            self.assertLessEqual(abs(narration_duration - actual), 0.3)

    async def test_news_graphics_and_fades_burn_and_preserve_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            edl = _build_render_fixture(task_dir, 3.0)
            graphics_path = generate_news_graphics(
                task_dir,
                topic="南宁民生",
                headline="南宁信息港春季市集开市",
                total_duration=3.0,
                accent="neutral",
            )
            self.assertIsNotNone(graphics_path)
            final_path = await render_final_video(
                task_dir,
                edl,
                lambda fraction, message: None,
                finish_options=FinishOptions(graphics_path=graphics_path, fade_in=0.5, fade_out=0.6),
            )
            video = _probe_stream(final_path, "video")
            self.assertEqual(video["codec_name"], "h264")
            self.assertEqual((video["width"], video["height"]), ("1920", "1080"))
            narration_duration = await probe_audio_duration(task_dir / "narration.m4a", task_dir)
            actual = await probe_audio_duration(final_path, task_dir)
            self.assertLessEqual(abs(narration_duration - actual), 0.3)

    async def test_all_finish_options_combined(self) -> None:
        tracks = load_music_library(MUSIC_LIBRARY_DIR)
        track = select_music_track(tracks, "neutral")
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            edl = _build_render_fixture(task_dir, 3.0)
            graphics_path = generate_news_graphics(
                task_dir, topic="城市要闻", headline="重点项目启动", total_duration=3.0, accent="energetic"
            )
            final_path = await render_final_video(
                task_dir,
                edl,
                lambda fraction, message: None,
                segment_options=SegmentRenderOptions(color_consistency=True, motion=True, zoom_ratio=0.08),
                finish_options=FinishOptions(
                    music=MusicMixOptions(track=track, mood="neutral"),
                    graphics_path=graphics_path,
                    fade_in=0.5,
                    fade_out=0.6,
                ),
            )
            video = _probe_stream(final_path, "video")
            audio = _probe_stream(final_path, "audio")
            self.assertEqual((video["width"], video["height"]), ("1920", "1080"))
            self.assertEqual(audio["codec_name"], "aac")
            narration_duration = await probe_audio_duration(task_dir / "narration.m4a", task_dir)
            actual = await probe_audio_duration(final_path, task_dir)
            self.assertLessEqual(abs(narration_duration - actual), 0.3)


class NewsGraphicsTest(unittest.TestCase):
    def test_returns_none_without_topic_or_headline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(
                generate_news_graphics(
                    Path(directory), topic="", headline="", total_duration=5.0, accent="neutral"
                )
            )

    def test_writes_valid_ass_with_topic_and_end_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            path = generate_news_graphics(
                task_dir,
                topic="城市要闻",
                headline="重点项目今日启动",
                total_duration=6.0,
                accent="energetic",
            )
            self.assertIsNotNone(path)
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("[Events]", content)
            self.assertIn("城市要闻", content)
            self.assertIn("重点项目今日启动", content)
            self.assertIn("GfxEnd", content)

    def test_writes_time_bounded_generated_media_disclosures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            path = generate_news_graphics(
                task_dir,
                topic="",
                headline="",
                total_duration=6.0,
                disclosure_intervals=[(0.25, 1.75), (3.0, 4.5)],
            )
            self.assertIsNotNone(path)
            content = Path(path).read_text(encoding="utf-8")
            self.assertEqual(content.count("AI生成示意画面"), 2)
            self.assertIn("0:00:00.25,0:00:01.75,GfxDisclosure", content)
            self.assertIn("0:00:03.00,0:00:04.50,GfxDisclosure", content)

    def test_generated_intervals_follow_clip_timeline(self) -> None:
        edl = [
            EDLItem(
                sentence_id=0,
                timeline_start=0.0,
                timeline_end=3.0,
                clips=[
                    EDLClip(shot_id=1, src="norm/source.mp4", **{"in": 0.0, "out": 1.0}),
                    EDLClip(
                        shot_id=2,
                        src="generated/fill_shot_2.mp4",
                        **{"in": 0.0, "out": 1.5},
                        freeze_pad=0.5,
                        media_origin="generated",
                    ),
                ],
            )
        ]

        self.assertEqual(_generated_media_intervals(edl), [(1.0, 3.0)])

    def test_generated_intervals_do_not_mislabel_procedural_information_cards(self) -> None:
        edl = [
            EDLItem(
                sentence_id=0,
                timeline_start=0.0,
                timeline_end=2.0,
                clips=[
                    EDLClip(
                        shot_id=9,
                        src="generated_cards/sentence_0.mp4",
                        **{"in": 0.0, "out": 2.0},
                    )
                ],
            )
        ]

        self.assertEqual(_generated_media_intervals(edl), [])


def _make_png(path: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x2a6f97:s=640x360:d=1",
            "-frames:v", "1", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path.read_bytes()


class GenerativeFillProviderTest(unittest.IsolatedAsyncioTestCase):
    def test_seedance_2_5_is_the_default_video_model(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(
            settings.volcengine_gen_video_model,
            "doubao-seedance-2-5-260628",
        )
        self.assertEqual(settings.generative_fill_resolution, "1080p")
        with self.assertRaises(ValueError):
            Settings(_env_file=None, generative_fill_duration_seconds=3)

    def test_filter_disclosure_uses_only_generated_shots_in_final_edl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "generated_media_disclosure.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "policy": "generated_visuals_are_disclosed_and_not_evidence",
                        "items": [
                            {"sentence_id": 0, "beat_id": 0, "shot_id": 7, "mode": "image", "prompt_sha256": "a" * 64, "disclosure_text": "AI生成示意画面"},
                            {"sentence_id": 1, "beat_id": 0, "shot_id": 8, "mode": "video", "prompt_sha256": "b" * 64, "disclosure_text": "AI生成示意画面"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            edl = [
                EDLItem(
                    sentence_id=1,
                    timeline_start=0.0,
                    timeline_end=1.0,
                    clips=[
                        EDLClip(
                            shot_id=8,
                            src="generated/fill_shot_8.mp4",
                            **{"in": 0.0, "out": 1.0},
                            media_origin="generated",
                        )
                    ],
                )
            ]

            path = filter_generated_media_disclosure(task_dir, edl)
            assert path is not None
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual([item["shot_id"] for item in payload["items"]], [8])

    def test_configured_predicate_requires_flag_and_key(self) -> None:
        self.assertFalse(
            generative_fill_configured(
                Settings(generative_fill_enabled=False, volcengine_gen_api_keys="", seedance_2_0_api_key="")
            )
        )
        self.assertFalse(
            generative_fill_configured(
                Settings(generative_fill_enabled=True, volcengine_gen_api_keys="", seedance_2_0_api_key="")
            )
        )
        self.assertTrue(
            generative_fill_configured(
                Settings(generative_fill_enabled=True, volcengine_gen_api_keys="k", seedance_2_0_api_key="")
            )
        )
        self.assertTrue(
            generative_fill_configured(
                Settings(generative_fill_enabled=True, volcengine_gen_api_keys="", seedance_2_0_api_key="ark-x")
            )
        )

    def test_validate_configuration_requires_key(self) -> None:
        provider = GenerativeFillProvider(
            base_url="https://ark.example.com/api/v3",
            api_key="",
            image_model="seedream",
            video_model="seedance",
            mode="image",
        )
        with self.assertRaises(GenerativeFillError):
            provider.validate_configuration()

    async def test_generate_image_decodes_b64(self) -> None:
        import base64

        with tempfile.TemporaryDirectory() as directory:
            png_bytes = _make_png(Path(directory) / "seed.png")
            encoded = base64.b64encode(png_bytes).decode("ascii")

            def handler(request: httpx.Request) -> httpx.Response:
                self.assertTrue(request.url.path.endswith("/images/generations"))
                self.assertEqual(request.headers["Authorization"], "Bearer k")
                return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            provider = GenerativeFillProvider(
                base_url="https://ark.example.com/api/v3",
                api_key="k",
                image_model="seedream",
                video_model="seedance",
                mode="image",
                client=client,
            )
            async with provider:
                result = await provider.generate_image("南宁夜景航拍")
            self.assertEqual(result, png_bytes)

    async def test_download_rejects_untrusted_host(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"url": "https://evil.example.com/x.png"}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GenerativeFillProvider(
            base_url="https://ark.example.com/api/v3",
            api_key="k",
            image_model="seedream",
            video_model="seedance",
            mode="image",
            client=client,
        )
        async with provider:
            with self.assertRaises(GenerativeFillError):
                await provider.generate_image("test")

    async def test_generate_video_uses_seedance_2_5_request_format(self) -> None:
        import json as _json

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "POST" and path.endswith("/contents/generations/tasks"):
                captured.update(_json.loads(request.content.decode("utf-8")))
                return httpx.Response(200, json={"id": "task-xyz"})
            if request.method == "GET" and "/contents/generations/tasks/" in path:
                return httpx.Response(
                    200,
                    json={
                        "status": "succeeded",
                        "content": {"video_url": "https://ark-project.tos-cn-beijing.volces.com/v.mp4"},
                    },
                )
            return httpx.Response(200, content=b"FAKEMP4BYTES")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = GenerativeFillProvider(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="ark-key",
            image_model="doubao-seedream-3-0-t2i-250415",
            video_model="doubao-seedance-2-5-260628",
            mode="video",
            duration=5,
            resolution="1080p",
            client=client,
        )
        async with provider:
            data = await provider.generate_video("城市天际线全景")
        self.assertEqual(data, b"FAKEMP4BYTES")
        self.assertEqual(captured["model"], "doubao-seedance-2-5-260628")
        self.assertEqual(captured["resolution"], "1080p")
        self.assertEqual(captured["ratio"], "16:9")
        self.assertEqual(captured["duration"], 5)
        self.assertFalse(captured["generate_audio"])
        self.assertFalse(captured["watermark"])
        self.assertEqual(captured["output_format"], "mp4")
        self.assertNotIn("omni_reference_task_type", captured)
        self.assertEqual(captured["content"][0]["type"], "text")

    @unittest.skipUnless(_ffmpeg_available(), "FFmpeg required")
    async def test_synthesize_fill_clip_produces_1080p(self) -> None:
        import base64

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            png_bytes = _make_png(task_dir / "seed.png")
            encoded = base64.b64encode(png_bytes).decode("ascii")

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            provider = GenerativeFillProvider(
                base_url="https://ark.example.com/api/v3",
                api_key="k",
                image_model="seedream",
                video_model="seedance",
                mode="image",
                client=client,
            )
            output = task_dir / "fill.mp4"
            async with provider:
                await synthesize_fill_clip(
                    provider, task_dir, "城市天际线", duration=1.5, output_path=output, mode="image"
                )
            video = _probe_stream(output, "video")
            self.assertEqual((video["width"], video["height"]), ("1920", "1080"))
            self.assertEqual(video["codec_name"], "h264")

    @unittest.skipUnless(_ffmpeg_available(), "FFmpeg required")
    async def test_apply_generative_fill_rewires_fallback_to_synthetic_shot(self) -> None:
        import base64

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            png_bytes = _make_png(task_dir / "seed.png")
            encoded = base64.b64encode(png_bytes).decode("ascii")

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            provider = GenerativeFillProvider(
                base_url="https://ark.example.com/api/v3",
                api_key="k",
                image_model="seedream",
                video_model="seedance",
                mode="image",
                client=client,
            )
            sentences = [Sentence(sentence_id=0, text="活动以市集为纽带，融合高新科技与传统节庆。")]
            existing_shot = AnnotatedShot(
                shot_id=3,
                source_index=0,
                source_scene_index=0,
                source_name="a.mp4",
                norm_path="norm/norm_0.mp4",
                start=0.0,
                end=2.0,
                duration=2.0,
                status="available",
                description="弱相关兜底镜头",
                quality={"sharp": 0.5, "bright": 0.5},
            )
            candidate = MatchCandidate(shot_id=3, similarity=0.3)
            item = MatchPlanItem(
                sentence_id=0,
                text=sentences[0].text,
                shot_id=3,
                confidence=0.28,
                is_fallback=True,
                candidates=[candidate],
                beat_matches=[
                    BeatMatch(
                        beat_id=0,
                        text=sentences[0].text,
                        shot_id=3,
                        confidence=0.28,
                        is_fallback=True,
                        candidates=[candidate],
                    )
                ],
            )
            async with provider:
                synthetic, plan, filled = await apply_generative_fill(
                    task_dir, sentences, [existing_shot], [item], provider, max_clips=2, mode="image"
                )
            self.assertEqual(filled, 1)
            self.assertEqual(len(synthetic), 1)
            self.assertTrue(plan[0].is_fallback)
            self.assertEqual(plan[0].confidence, 0.0)
            self.assertEqual(plan[0].shot_id, synthetic[0].shot_id)
            self.assertGreater(synthetic[0].shot_id, 3)
            self.assertEqual(synthetic[0].media_origin, "generated")
            self.assertTrue(synthetic[0].description.startswith("AI生成示意画面"))
            self.assertTrue((task_dir / synthetic[0].norm_path).is_file())
            self.assertTrue((task_dir / "thumbs" / f"shot_{synthetic[0].shot_id}.jpg").is_file())
            disclosure = task_dir / "generated_media_disclosure.json"
            self.assertTrue(disclosure.is_file())
            self.assertIn("AI生成示意画面", disclosure.read_text(encoding="utf-8"))

    @unittest.skipUnless(_ffmpeg_available(), "FFmpeg required")
    async def test_apply_generative_fill_assigns_one_synthetic_shot_per_beat(self) -> None:
        import base64

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            png_bytes = _make_png(task_dir / "seed.png")
            encoded = base64.b64encode(png_bytes).decode("ascii")

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            provider = GenerativeFillProvider(
                base_url="https://ark.example.com/api/v3",
                api_key="k",
                image_model="seedream",
                video_model="seedance",
                mode="image",
                client=client,
            )
            sentence = Sentence(sentence_id=0, text="城市生活更加便利，公共服务持续改善。")
            existing_shot = AnnotatedShot(
                shot_id=3,
                source_index=0,
                source_scene_index=0,
                source_name="a.mp4",
                norm_path="norm/norm_0.mp4",
                start=0.0,
                end=2.0,
                duration=2.0,
                status="available",
                description="弱相关兜底镜头",
                quality={"sharp": 0.5, "bright": 0.5},
            )
            candidate = MatchCandidate(shot_id=3, similarity=0.3)
            beats = [
                BeatMatch(
                    beat_id=index,
                    text=text,
                    shot_id=3,
                    confidence=0.2,
                    is_fallback=True,
                    candidates=[candidate],
                    intent_type="abstract",
                )
                for index, text in enumerate(("城市生活更加便利", "公共服务持续改善"))
            ]
            item = MatchPlanItem(
                sentence_id=0,
                text=sentence.text,
                shot_id=3,
                confidence=0.2,
                is_fallback=True,
                candidates=[candidate],
                beat_matches=beats,
            )
            async with provider:
                synthetic, plan, filled = await apply_generative_fill(
                    task_dir,
                    [sentence],
                    [existing_shot],
                    [item],
                    provider,
                    max_clips=2,
                    mode="image",
                )

            self.assertEqual(filled, 2)
            self.assertEqual(len(synthetic), 2)
            shot_ids = [beat.shot_id for beat in plan[0].beat_matches]
            self.assertEqual(len(shot_ids), len(set(shot_ids)))
            self.assertTrue(all(beat.is_fallback for beat in plan[0].beat_matches))


class BeatDensificationTest(unittest.TestCase):
    def test_multi_clause_sentence_splits_into_several_beats(self) -> None:
        beats = extract_visual_beats("市集现场人流涌动，欢声不断，洋溢着一派喜庆祥和的新春气氛。")
        self.assertGreaterEqual(len(beats), 2)

    def test_short_single_clause_stays_one_beat(self) -> None:
        self.assertEqual(len(extract_visual_beats("目标实体正在活动现场展示。")), 1)

    def test_long_address_event_sentence_gets_multiple_visual_beats(self) -> None:
        beats = extract_visual_beats(
            "位于南宁市西乡塘区鲁班路95号的南宁信息港广场"
            "1月30日举办了“金马贺岁，高新同驰”迎春市集。"
        )

        self.assertGreaterEqual(len(beats), 2)
        self.assertIn("南宁信息港广场", beats[0].text)
        self.assertTrue(any("迎春市集" in beat.text for beat in beats))

    def test_date_sentence_stays_one_beat(self) -> None:
        beats = extract_visual_beats("本次活动将持续到1月31日。")
        self.assertEqual(len(beats), 1)
        self.assertEqual(beats[0].intent_type, "date")

    def test_enumeration_behaviour_is_unchanged(self) -> None:
        beats = extract_visual_beats("灌阳油茶、现做寿司等特色小吃引来众多品尝者。")
        self.assertEqual([beat.text for beat in beats], ["灌阳油茶", "现做寿司"])


if __name__ == "__main__":
    unittest.main()
