"""Prototype preferences: isolated contracts plus optional LOCAL codec/pixel tests.

No main/app import, dotenv loading, HTTP client, provider construction or paid
operation. Fixtures are synthetic and task-owned TEMP files. FFmpeg tests skip
only when local binaries are absent; effect/codec failures are not skips.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from pydantic import ValidationError

from backend import pipeline, rendering, tts_pipeline, workbench
from backend.config import Settings
from backend.graphics import generate_news_graphics
from backend.models import (
    AnnotatedShot, CaptionStyle, EDLClip, EDLItem, EditingPreferences,
    MatchCandidate, MatchPlanItem, PIPELINE_STAGE_NAMES, SegmentManifestItem,
    Sentence, SentenceTiming, SyncSoundSelection, TTSWordTiming, VisionQuality,
)
from backend.providers.tts import TTSProvider
from backend.revisions import record_metadata
from backend.storage import write_json_atomic
from backend.subtitles import (
    BIG_CAPTION_FONT_SCALE, STABLE_SUBTITLE_STYLE_NAME, SUBTITLE_STYLE_NAME,
    TITLE_STYLE_NAME, generate_ass_subtitles, subtitle_burn_in_artifact,
    validate_subtitle_artifacts,
)
from backend.tts_pipeline import (
    NARRATION_AUDIO_FORMAT, NARRATION_SAMPLE_RATE, SPEECH_ENHANCEMENT_FILTER,
    SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE, TTSProcessingError,
    concatenate_narration, probe_audio_duration, rebuild_narration_from_existing,
)


def _timing(
    index: int = 0, *, kind: str = "tts", path: str | None = None,
    text: str = "城市服务改善。", start: float = 0.0, gap: float = 0.0,
) -> SentenceTiming:
    return SentenceTiming(
        sentence_id=index, text=text, audio_path=path or f"tts/{index}.wav",
        duration=1.0, start=start, end=start + 1.0, gap_after=gap,
        audio_kind=cast(Any, kind),
        words=[TTSWordTiming(text=text, start=0.1, end=0.9)],
    )


def _write_pcm(
    path: Path, *, frames: int = NARRATION_SAMPLE_RATE, channels: int = 2,
    sample: Callable[[int], float] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(NARRATION_SAMPLE_RATE)
        if sample is None:
            audio.writeframes(b"\0" * frames * channels * 2)
        else:
            pcm = bytearray()
            for i in range(frames):
                value = round(max(-1.0, min(1.0, sample(i))) * 32767)
                pcm.extend(struct.pack("<h", value) * channels)
            audio.writeframes(pcm)


def _settings() -> Settings:
    # Only explicit non-secret values; BaseSettings is deliberately not invoked.
    return cast(Settings, SimpleNamespace(
        tts_news_rate_tolerance=0.08, tts_target_lufs=-20.0, tts_target_lra=5.0,
        tts_true_peak_dbfs=-2.0, tts_max_loudness_spread_lu=2.0,
        motion_zoom_ratio=0.08, quality_min_match_confidence=0.6,
        quality_gate_mode="block", retrieval_top_k=5, retrieval_lexical_rescue_k=4,
        video_embedding_candidate_top_k=15, video_embedding_concurrency=1,
    ))


def _record(root: Path, **preferences: Any) -> Any:
    return SimpleNamespace(
        task_dir=root, task_id="preference-fixture", script="城市新闻\n\n城市服务改善。\n公园今日开放。",
        preferences=EditingPreferences(**preferences), uploads=[], stages=[], revision=1,
    )


def _fixture(root: Path) -> tuple[list[SentenceTiming], list[MatchPlanItem], list[AnnotatedShot], list[EDLItem]]:
    root.mkdir(parents=True, exist_ok=True)
    timings = [
        _timing(0, kind="sync", path="student_audio/0.wav", gap=0.12),
        _timing(1, text="公园今日开放。", start=1.12),
    ]
    for timing in timings:
        _write_pcm(root / timing.audio_path, channels=1)
    shots = [AnnotatedShot(
        shot_id=i, source_index=i, source_scene_index=0, source_name=f"{i}.mp4",
        norm_path=f"norm/{i}.mp4", start=0.0, end=5.0, duration=5.0,
        status="available", description="城市公园", quality=VisionQuality(sharp=0.9, bright=0.9),
    ) for i in range(3)]
    for shot in shots:
        path = root / shot.norm_path
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"synthetic-contract-video-not-decoded")
    plan = [MatchPlanItem(
        sentence_id=t.sentence_id, text=t.text, shot_id=t.sentence_id,
        confidence=0.9, candidates=[MatchCandidate(shot_id=t.sentence_id, similarity=0.9)],
    ) for t in timings]
    edl = [EDLItem(
        sentence_id=t.sentence_id,
        clips=[EDLClip(shot_id=t.sentence_id, src=f"norm/{t.sentence_id}.mp4",
                       in_time=0.0, out_time=t.duration + t.gap_after)],
        timeline_start=t.start, timeline_end=t.end + t.gap_after,
    ) for t in timings]
    manifest = [SegmentManifestItem(sentence_id=i, segments=[f"segments/{i}.mp4"]) for i in range(2)]
    for item in manifest:
        segment = root / item.segments[0]
        segment.parent.mkdir(exist_ok=True)
        segment.write_bytes(b"contract-segment-not-decoded")
    for name, models in (
        ("timings.json", timings), ("source_timings.json", timings),
        ("match_plan.json", plan), ("shots_annotated.json", shots),
        ("edl.json", edl), ("source_edl.json", edl), ("segment_manifest.json", manifest),
    ):
        write_json_atomic(root / name, [m.model_dump(mode="json", by_alias=True) for m in models])
    write_json_atomic(root / "narration_profile.json", {"schema_version": 1, "units": [], "sentinel": "retain"})
    write_json_atomic(root / "student_narration.json", {"units": [{"sentence_id": 0}]})
    for name in ("final.mp4", "video_only.mp4", "narration.m4a"):
        (root / name).write_bytes(b"previous-contract-output")
    generate_ass_subtitles(root, timings, title="城市新闻")
    return timings, plan, shots, edl


class _LocalProvider(TTSProvider):
    def validate_configuration(self) -> None:
        pass

    async def synthesize(self, text: str, output_path: Path) -> list[TTSWordTiming]:
        raise AssertionError("Contract test must explicitly mock synthesis")


class _LocalContext:
    async def __aenter__(self) -> _LocalContext:
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass


class _AudioCommands:
    """Command contract double; not evidence of a real DSP effect."""
    def __init__(self, *, sample_delta: int = 0, cancel: bool = False) -> None:
        self.commands: list[list[str]] = []
        self.sample_delta = sample_delta
        self.cancel = cancel

    async def __call__(self, command: Sequence[str], _root: Path, _label: str) -> bytes:
        values = list(command)
        self.commands.append(values)
        if SPEECH_ENHANCEMENT_FILTER in " ".join(values):
            reference = next(Path(arg) for arg in values if arg.endswith(".reference.wav"))
            enhanced = next(Path(arg) for arg in values if arg.endswith(".enhanced.wav"))
            _write_pcm(reference)
            if self.cancel:
                raise asyncio.CancelledError()
            _write_pcm(enhanced, frames=NARRATION_SAMPLE_RATE + self.sample_delta)
        else:
            Path(values[-1]).write_bytes(b"assembled-contract-output")
        return b""


def _post_render_mocks(stack: ExitStack, module: str) -> None:
    stack.enter_context(patch(f"{module}._run_blocking_until_complete", new=AsyncMock(side_effect=lambda fn: fn())))
    stack.enter_context(patch(f"{module}.generate_quality_report", return_value={
        "blocking_issue_count": 0, "warning_count": 0, "issues": [],
    }))
    stack.enter_context(patch(f"{module}.generate_report", return_value=SimpleNamespace(rows=[])))


class PrototypePreferenceModelTests(unittest.TestCase):
    def test_defaults_and_roundtrip_preserve_historical_choices(self) -> None:
        defaults = EditingPreferences()
        self.assertEqual(defaults.caption_style, "news")
        self.assertFalse(defaults.enhance_speech)
        self.assertFalse(rendering.FinishOptions().enabled)
        self.assertFalse(rendering.FinishOptions(caption_style="news").enabled)
        self.assertEqual(len(PIPELINE_STAGE_NAMES), 10)
        for style in ("news", "big", "none"):
            prefs = EditingPreferences(caption_style=cast(CaptionStyle, style), enhance_speech=True)
            self.assertEqual(EditingPreferences.model_validate_json(prefs.model_dump_json()), prefs)

    def test_invalid_values_and_unknown_fields_are_rejected(self) -> None:
        for value in ("", "large", "News", "BIG", None, True, 1, [], {}):
            with self.subTest(caption=value), self.assertRaises(ValidationError):
                EditingPreferences.model_validate({"caption_style": value})
        for value in ("true", "false", 0, 1, None, [], {}):
            with self.subTest(enhance=value), self.assertRaises(ValidationError):
                EditingPreferences.model_validate({"enhance_speech": value})
        with self.assertRaises(ValidationError):
            EditingPreferences.model_validate({"caption_style": "news", "speech_repair": True})
        with self.assertRaises(ValueError):
            rendering.FinishOptions(caption_style=cast(Any, "large"))

    def test_legacy_mapping_and_revision_state_do_not_reset_preferences(self) -> None:
        record = _record(Path("unused"), caption_style="none", enhance_speech=True)
        saved = record_metadata(record)
        restored = SimpleNamespace()
        workbench._apply_state(restored, saved)
        self.assertEqual(restored.preferences, record.preferences)
        mapped = SimpleNamespace(preferences=saved["preferences"])
        self.assertEqual(pipeline._preferences(mapped), record.preferences)
        self.assertIsInstance(mapped.preferences, EditingPreferences)
        self.assertEqual(pipeline._preferences(SimpleNamespace()), EditingPreferences())
        with self.assertRaises(ValidationError):
            pipeline._preferences(SimpleNamespace(preferences={"caption_style": "invalid"}))

    def test_workbench_explicit_false_is_a_change_not_an_omission(self) -> None:
        payload = workbench.EditRequest.model_validate({
            "expected_revision": 0, "keep_sentence_ids": [0],
            "caption_style": "none", "enhance_speech": False,
        })
        self.assertIs(payload.enhance_speech, False)
        self.assertEqual(payload.caption_style, "none")
        with self.assertRaises(ValidationError):
            workbench.EditRequest.model_validate({
                "expected_revision": 0, "keep_sentence_ids": [0], "enhance_speech": "false",
            })


class CaptionArtifactTests(unittest.TestCase):
    def test_news_is_exact_original_and_none_omits_only_body_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate_ass_subtitles(root, [_timing()], title="城市新闻")
            ass, manifest = root / "subs.ass", root / "subtitle_manifest.json"
            original, original_manifest = ass.read_bytes(), manifest.read_bytes()
            title = next(line for line in original.decode().splitlines() if line.startswith("Dialogue:") and TITLE_STYLE_NAME in line)
            with subtitle_burn_in_artifact(ass, manifest) as news:
                self.assertEqual(news, ass)
                self.assertEqual(news.read_bytes(), original)
            with subtitle_burn_in_artifact(ass, manifest, caption_style="none") as omitted:
                self.assertNotEqual(omitted, ass)
                events = [line for line in omitted.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")]
                self.assertEqual(events, [title])
                self.assertIn(hashlib.sha256(original).hexdigest(), omitted.read_text(encoding="utf-8"))
            self.assertFalse(omitted.exists())
            self.assertEqual(ass.read_bytes(), original)
            self.assertEqual(manifest.read_bytes(), original_manifest)
            validate_subtitle_artifacts(ass, manifest)
            self.assertFalse(list(root.glob(".caption-burn-*")))

    def test_none_without_title_is_valid_empty_burner_not_empty_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate_ass_subtitles(root, [_timing()])
            with subtitle_burn_in_artifact(root / "subs.ass", root / "subtitle_manifest.json", caption_style="none") as path:
                text = path.read_text(encoding="utf-8")
                self.assertIn("[Events]", text)
                self.assertNotIn("Dialogue:", text)
            self.assertGreater(validate_subtitle_artifacts(root / "subs.ass", root / "subtitle_manifest.json")["event_count"], 0)

    def test_big_scales_both_body_styles_from_validated_base_and_reflows_safely(self) -> None:
        for width, height in ((1920, 1080), (3840, 2160)):
            with self.subTest(width=width), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                timings = [
                    _timing(gap=0.12),
                    _timing(1, start=1.12, text="位于南宁市西乡塘区鲁班路九十五号的南宁信息港广场今日举办迎春市集活动市民游客前来体验传统手工与地方美食"),
                ]
                generate_ass_subtitles(root, timings, title="城市新闻", stable_sentence_ids={1}, frame_width=width, frame_height=height)
                ass, manifest = root / "subs.ass", root / "subtitle_manifest.json"
                original, original_manifest = ass.read_bytes(), manifest.read_bytes()
                baseline_lines = original.decode().splitlines()
                with subtitle_burn_in_artifact(ass, manifest, caption_style="big") as path:
                    lines = path.read_text(encoding="utf-8").splitlines()
                    for style in (SUBTITLE_STYLE_NAME, STABLE_SUBTITLE_STYLE_NAME):
                        before = next(line for line in baseline_lines if line.startswith(f"Style: {style},")).split(",")
                        after = next(line for line in lines if line.startswith(f"Style: {style},")).split(",")
                        self.assertEqual(float(after[2]), float(before[2]) * BIG_CAPTION_FONT_SCALE)
                        self.assertEqual(after[:2] + after[3:], before[:2] + before[3:])
                    # Default rolling body event keeps exact center, clip, line breaks and clock.
                    before_body = next(line for line in baseline_lines if line.startswith("Dialogue:") and f",{SUBTITLE_STYLE_NAME}," in line)
                    self.assertIn(before_body, lines)
                    for line in baseline_lines:
                        if TITLE_STYLE_NAME in line:
                            self.assertIn(line, lines)
                    old_stable = next(line for line in baseline_lines if line.startswith("Dialogue:") and f",{STABLE_SUBTITLE_STYLE_NAME}," in line)
                    new_stable = next(line for line in lines if line.startswith("Dialogue:") and f",{STABLE_SUBTITLE_STYLE_NAME}," in line)
                    self.assertEqual(old_stable.split(",", 9)[:9], new_stable.split(",", 9)[:9])
                    old_text = old_stable.split("}", 1)[1].replace(r"\N", "").replace(" ", "")
                    new_text = new_stable.split("}", 1)[1].replace(r"\N", "").replace(" ", "")
                    self.assertEqual(new_text, old_text)
                    self.assertGreater(new_stable.count(r"\N"), old_stable.count(r"\N"))
                    for left, top, right, bottom in re.findall(r"\\clip\((\d+),(\d+),(\d+),(\d+)\)", "\n".join(lines)):
                        self.assertGreaterEqual(int(left), round(width * 0.1))
                        self.assertGreaterEqual(int(top), round(height * 0.1))
                        self.assertLessEqual(int(right), round(width * 0.9))
                        self.assertLessEqual(int(bottom), round(height * 0.9))
                self.assertEqual(ass.read_bytes(), original)
                self.assertEqual(manifest.read_bytes(), original_manifest)
                self.assertEqual(validate_subtitle_artifacts(ass, manifest)["schema_version"], 1)

    def test_all_styles_refuse_tampering_before_creating_derivatives(self) -> None:
        for style in ("news", "big", "none"):
            with self.subTest(style=style), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                generate_ass_subtitles(root, [_timing()])
                (root / "subs.ass").write_bytes((root / "subs.ass").read_bytes() + b"; tampered\n")
                with self.assertRaisesRegex(ValueError, "不一致"):
                    with subtitle_burn_in_artifact(root / "subs.ass", root / "subtitle_manifest.json", caption_style=cast(CaptionStyle, style)):
                        self.fail("Tampered baseline must never be burned")
                self.assertFalse(list(root.glob(".caption-burn-*")))

    def test_unique_temporary_burners_cleanup_even_on_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate_ass_subtitles(root, [_timing()])
            args = (root / "subs.ass", root / "subtitle_manifest.json")
            with self.assertRaises(asyncio.CancelledError):
                with subtitle_burn_in_artifact(*args, caption_style="big") as first:
                    with subtitle_burn_in_artifact(*args, caption_style="big") as second:
                        self.assertNotEqual(first, second)
                        raise asyncio.CancelledError()
            self.assertFalse(list(root.glob(".caption-burn-*")))
            validate_subtitle_artifacts(*args)


class CaptionRenderContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_mix_command_unchanged_and_graphics_survive_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            graphics = generate_news_graphics(root, topic="城市新闻", headline="公共服务", total_duration=2.12, disclosure_intervals=[(0, 1)])
            assert graphics is not None
            before = {name: (root / name).read_bytes() for name in ("subs.ass", "subtitle_manifest.json", "graphics.ass")}
            burned: list[tuple[Path, str]] = []

            def subtitle_filter(_root: Path, path: Path, _fonts: Path) -> str:
                burned.append((path, path.read_text(encoding="utf-8")))
                return f"ASS({path.name})"

            commands = AsyncMock(return_value=b"")
            with patch.object(rendering, "_prepare_ass_font_directory", return_value=root), patch.object(rendering, "_build_subtitle_filter", side_effect=subtitle_filter), patch.object(rendering, "_build_graphics_filter", return_value="MANDATORY_GRAPHICS"), patch.object(rendering, "run_logged_command", commands):
                for options in (None, rendering.FinishOptions(caption_style="news"), rendering.FinishOptions(caption_style="none", graphics_path=graphics)):
                    await rendering._mix_subtitles_and_narration(
                        root, root / "video_only.mp4", root / "narration.m4a", root / "subs.ass", root / "final.mp4",
                        subtitle_manifest_path=root / "subtitle_manifest.json", finish_options=options,
                    )
            self.assertEqual(commands.await_args_list[0].args[0], commands.await_args_list[1].args[0])
            graph_command = commands.await_args_list[2].args[0]
            self.assertIn("MANDATORY_GRAPHICS", graph_command[graph_command.index("-vf") + 1])
            self.assertNotIn("afftdn", " ".join(graph_command))
            self.assertIn(f",{TITLE_STYLE_NAME},", burned[-1][1])
            self.assertNotIn(f",{SUBTITLE_STYLE_NAME},,", burned[-1][1])
            self.assertNotIn(f",{STABLE_SUBTITLE_STYLE_NAME},,", burned[-1][1])
            self.assertFalse(burned[-1][0].exists())
            self.assertEqual(before, {name: (root / name).read_bytes() for name in before})

    async def test_none_cannot_bypass_canonical_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            (root / "subtitle_manifest.json").write_text("{}", encoding="utf-8")
            with patch.object(rendering, "run_logged_command", new_callable=AsyncMock) as run:
                with self.assertRaises(rendering.RenderingError):
                    await rendering._mix_subtitles_and_narration(
                        root, root / "video_only.mp4", root / "narration.m4a", root / "subs.ass", root / "final.mp4",
                        subtitle_manifest_path=root / "subtitle_manifest.json", finish_options=rendering.FinishOptions(caption_style="none"),
                    )
                run.assert_not_awaited()

    async def test_finish_builder_retains_mandatory_disclosure_in_every_caption_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timing = _timing()
            edl = [EDLItem(sentence_id=0, timeline_start=0, timeline_end=1, clips=[EDLClip(
                shot_id=9, src="generated/9.mp4", in_time=0, out_time=1, media_origin="generated",
            )])]
            for style in ("news", "big", "none"):
                for suffix in ("", ".remix", ".replacement"):
                    record = _record(root, caption_style=style, enhance_speech=True)
                    options = await pipeline._build_finish_options(record, _settings(), [timing], edl=edl, graphics_output_path=root / f"graphics{suffix}.ass")
                    self.assertEqual(options.caption_style, style)
                    assert options.graphics_path is not None
                    content = options.graphics_path.read_text(encoding="utf-8")
                    self.assertIn("AI生成示意画面", content)
                    self.assertIn("0:00:00.00,0:00:01.00,GfxDisclosure", content)
                    self.assertNotIn("GfxTopic,,", content)

    async def test_existing_segment_render_validates_canonical_not_derivative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _fixture(root)
            with patch.object(rendering, "require_media_tools"), patch.object(rendering, "_concatenate_segments", new_callable=AsyncMock), patch.object(rendering, "_mix_subtitles_and_narration", new_callable=AsyncMock) as mix, patch.object(rendering, "validate_final_video", new_callable=AsyncMock) as validate:
                options = rendering.FinishOptions(caption_style="none")
                await rendering.render_from_existing_segments(
                    root, [root / "segments/0.mp4"], lambda *_args: None,
                    narration_path=root / "narration.m4a", subtitles_path=root / "subs.ass",
                    subtitle_manifest_path=root / "subtitle_manifest.json", video_only_path=root / "video_only.mp4",
                    final_path=root / "final.mp4", finish_options=options,
                )
                self.assertIs(mix.await_args.kwargs["finish_options"], options)
                self.assertEqual(validate.await_args.kwargs["subtitles_path"], root / "subs.ass")
                self.assertEqual(validate.await_args.kwargs["subtitle_manifest_path"], root / "subtitle_manifest.json")


class SpeechAssemblyContractTests(unittest.IsolatedAsyncioTestCase):
    def test_fixed_local_dsp_contract_pins_denoised_mode_and_one_sample_bound(self) -> None:
        highpass, preroll, flush, denoiser, startup, timestamps = SPEECH_ENHANCEMENT_FILTER.split(",")
        self.assertEqual(highpass, "highpass=f=70:p=2")
        self.assertEqual(tts_pipeline.SPEECH_ENHANCEMENT_DELAY_SAMPLES, 1200)
        self.assertEqual(tts_pipeline.SPEECH_ENHANCEMENT_PREROLL_SAMPLES, 1200)
        self.assertEqual(preroll, "adelay=1200S:all=1")
        self.assertEqual(flush, "apad=pad_len=1200")
        self.assertEqual(startup, "atrim=start_sample=2400")
        self.assertEqual(timestamps, "asetpts=PTS-STARTPTS")
        self.assertTrue(denoiser.startswith("afftdn="))
        self.assertEqual(dict(option.split("=", 1) for option in denoiser.removeprefix("afftdn=").split(":")), {
            "nr": "12", "nf": "-50", "nt": "w", "rf": "-38", "tn": "0",
            "tr": "0", "om": "o", "ad": "0.5", "gs": "0",
        })
        self.assertEqual(SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE, 1)

    async def test_filters_only_sync_whole_student_and_per_sentence_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timings = [
                _timing(0, gap=0.12),
                _timing(1, kind="sync", path="tts/source_sync.mp3", start=1.12, gap=0.12),
                _timing(2, kind="sync", path="student_audio/2.wav", start=2.24, gap=0.12),
                _timing(3, kind="sync", path="recordings/recorded.wav", start=3.36),
            ]
            for timing in timings:
                path = root / timing.audio_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(f"original-{timing.sentence_id}".encode())
            originals = {t.audio_path: (root / t.audio_path).read_bytes() for t in timings}
            timing_values = [t.model_dump(mode="json") for t in timings]
            commands = _AudioCommands()
            with patch.object(tts_pipeline, "run_logged_command", side_effect=commands.__call__), patch.object(tts_pipeline, "_two_pass_loudnorm_filter", new=AsyncMock(return_value="loudnorm=I=-20")) as loudness:
                await concatenate_narration(root, timings, enhance_speech=True)
            filtered = [cmd for cmd in commands.commands if SPEECH_ENHANCEMENT_FILTER in " ".join(cmd)]
            self.assertEqual(len(filtered), 3)
            self.assertEqual([cmd[cmd.index("-i") + 1] for cmd in filtered], [str((root / t.audio_path).resolve()) for t in timings[1:]])
            self.assertEqual(loudness.await_count, 1)
            self.assertEqual(loudness.await_args.args[1], (root / timings[0].audio_path).resolve())
            for cmd in filtered:
                graph = cmd[cmd.index("-filter_complex") + 1]
                self.assertIn(SPEECH_ENHANCEMENT_FILTER, graph)
                # Only the source-derived fixed FFT flush/startup correction is
                # permitted. Never use end trimming or target-length padding.
                self.assertEqual(graph.count("apad="), 1)
                self.assertEqual(graph.count("atrim="), 1)
                self.assertEqual(graph.count("adelay="), 1)
                for forbidden in ("end_sample", "whole_len", "atempo", "silenceremove", "asetrate", "areverse"):
                    self.assertNotIn(forbidden, graph)
                self.assertNotIn("-t", cmd)
            self.assertNotIn("afftdn", " ".join(commands.commands[-1]))
            self.assertEqual([t.model_dump(mode="json") for t in timings], timing_values)
            self.assertEqual({p: (root / p).read_bytes() for p in originals}, originals)
            effect = json.loads((root / "narration_profile.json").read_text(encoding="utf-8"))["speech_enhancement"]
            self.assertTrue(effect["applied"])
            self.assertFalse(effect["tts_filtered"])
            self.assertEqual(effect["filtered_unit_count"], 3)
            for unit in effect["units"]:
                self.assertEqual(unit["source_sha256"], hashlib.sha256(originals[unit["source_audio_path"]]).hexdigest())
                self.assertEqual(unit["source_decoded_samples"], unit["processed_samples"])
                self.assertEqual(unit["algorithmic_delay_samples"], 1200)
            self.assertFalse(list(root.glob(".speech-enhance-*")))

    async def test_all_tts_bypasses_dsp_even_with_stale_recording_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timing = _timing(path="recordings/now_tts.wav")
            _write_pcm(root / timing.audio_path)
            write_json_atomic(root / "student_narration.json", {"units": [{"sentence_id": 0}]})
            write_json_atomic(root / "workbench_audio.json", {"0": {"audio_source": "recording"}})
            commands = _AudioCommands()
            with patch.object(tts_pipeline, "_prepare_enhanced_recording", new=AsyncMock(side_effect=AssertionError("TTS must not be filtered"))) as filter_audio, patch.object(tts_pipeline, "run_logged_command", side_effect=commands.__call__), patch.object(tts_pipeline, "_two_pass_loudnorm_filter", new=AsyncMock(return_value="anull")):
                await concatenate_narration(root, [timing], enhance_speech=True)
                filter_audio.assert_not_awaited()
            effect = json.loads((root / "narration_profile.json").read_text(encoding="utf-8"))["speech_enhancement"]
            self.assertTrue(effect["requested"])
            self.assertFalse(effect["applied"])
            self.assertEqual(effect["units"], [])
            self.assertEqual(len(commands.commands), 1)

    async def test_sample_drift_and_cancellation_fail_closed_and_cleanup(self) -> None:
        for delta, cancel, error in (
            (-2, False, TTSProcessingError), (2, False, TTSProcessingError),
            (80, False, TTSProcessingError), (0, True, asyncio.CancelledError),
        ):
            with self.subTest(delta=delta, cancel=cancel), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                timing = _timing(kind="sync")
                _write_pcm(root / timing.audio_path)
                original = (root / timing.audio_path).read_bytes()
                commands = _AudioCommands(sample_delta=delta, cancel=cancel)
                with patch.object(tts_pipeline, "run_logged_command", side_effect=commands.__call__):
                    with self.assertRaises(error):
                        await concatenate_narration(root, [timing], enhance_speech=True)
                self.assertFalse((root / "narration.m4a").exists())
                self.assertFalse((root / "narration_profile.json").exists())
                self.assertFalse(list(root.glob(".speech-enhance-*")))
                self.assertEqual((root / timing.audio_path).read_bytes(), original)

    def test_truncated_pcm_cannot_pass_sample_header_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truncated.wav"
            _write_pcm(path)
            path.write_bytes(path.read_bytes()[:-20])
            with self.assertRaisesRegex(TTSProcessingError, "不完整"):
                tts_pipeline._verified_pcm_samples(path)

    async def test_assembly_refuses_to_overwrite_reusable_source_audio(self) -> None:
        for enabled in (False, True):
            with self.subTest(enhance=enabled), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                timing = _timing(kind="sync")
                source = root / timing.audio_path
                _write_pcm(source)
                original = source.read_bytes()
                commands = _AudioCommands()
                with patch.object(tts_pipeline, "run_logged_command", side_effect=commands.__call__):
                    with self.assertRaisesRegex(TTSProcessingError, "不能覆盖"):
                        await concatenate_narration(root, [timing], output_path=source, enhance_speech=enabled)
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(list(root.glob(".speech-enhance-*")))
                self.assertFalse((root / "narration_profile.json").exists())

    async def test_disabled_and_remix_rebuild_use_original_sources_not_processed_mix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timings, *_ = _fixture(root)
            original_audio = (root / timings[0].audio_path).read_bytes()
            original_profile = (root / "narration_profile.json").read_bytes()
            commands = _AudioCommands()
            with patch.object(tts_pipeline, "run_logged_command", side_effect=commands.__call__), patch.object(tts_pipeline, "probe_audio_duration", new=AsyncMock(return_value=1.0)):
                rebuilt = await rebuild_narration_from_existing(
                    root, timings, [0], timings_path=root / "timings.remix.json",
                    narration_path=root / "narration.remix.m4a", narration_profile_path=root / "narration_profile.remix.json",
                    enhance_speech=True,
                )
                self.assertEqual(rebuilt[0].words, timings[0].words)
                self.assertEqual(rebuilt[0].duration, timings[0].duration)
                self.assertEqual(rebuilt[0].audio_path, timings[0].audio_path)
                self.assertEqual(commands.commands[0][commands.commands[0].index("-i") + 1], str((root / timings[0].audio_path).resolve()))
                self.assertEqual((root / "narration_profile.json").read_bytes(), original_profile)
                # Disabling on a subsequent assembly must erase the old receipt.
                commands.commands.clear()
                await concatenate_narration(root, rebuilt, output_path=root / "narration.remix.m4a", narration_profile_path=root / "narration_profile.remix.json", enhance_speech=False)
            self.assertEqual(len(commands.commands), 1)
            self.assertNotIn("afftdn", " ".join(commands.commands[0]))
            self.assertNotIn("speech_enhancement", json.loads((root / "narration_profile.remix.json").read_text(encoding="utf-8")))
            self.assertEqual((root / timings[0].audio_path).read_bytes(), original_audio)


class PreferencePipelineThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_stage_seven_passes_flag_without_new_provider_or_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = _record(root, caption_style="big", enhance_speech=True)
            reporter = Mock()
            timing = _timing()
            with patch.object(pipeline, "create_tts_provider", return_value=_LocalProvider()) as factory, patch.object(pipeline, "synthesize_narration", new=AsyncMock(return_value=[timing])) as synthesize, patch.object(pipeline.LLMProvider, "from_settings", side_effect=AssertionError("No pronunciation request expected")):
                result = await pipeline._run_tts_synthesis(record, reporter, [Sentence(sentence_id=0, text=timing.text)], [], _settings())
            self.assertEqual(result, [timing])
            self.assertIs(synthesize.await_args.kwargs["enhance_speech"], True)
            factory.assert_called_once()
            self.assertEqual([call.args[1] for call in reporter.start_stage.call_args_list], [7])
            self.assertEqual([call.args[1] for call in reporter.complete_stage.call_args_list], [7])

    async def test_synthesis_assembly_receives_flag_for_real_sync_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentence = Sentence(sentence_id=0, text="城市服务改善。")
            match = MatchPlanItem(
                sentence_id=0, text=sentence.text, shot_id=0, confidence=0.9,
                candidates=[MatchCandidate(shot_id=0, similarity=0.9)],
                sync_sound=SyncSoundSelection(source_index=0, source_media_path="raw/0.mp4", shot_id=0, text=sentence.text, start=0.3, end=1.3, similarity=0.9),
            )
            with patch.object(tts_pipeline, "require_media_tools"), patch.object(tts_pipeline, "extract_sync_sound_audio", new_callable=AsyncMock), patch.object(tts_pipeline, "probe_audio_duration", new=AsyncMock(return_value=1.0)), patch.object(tts_pipeline, "concatenate_narration", new_callable=AsyncMock) as assemble:
                timings = await tts_pipeline.synthesize_narration(root, [sentence], _LocalProvider(), lambda *_args: None, match_plan=[match], enhance_speech=True)
            self.assertEqual(timings[0].audio_kind, "sync")
            self.assertIs(assemble.await_args.kwargs["enhance_speech"], True)
            self.assertEqual(match.sync_sound.start, 0.3)
            self.assertEqual(match.sync_sound.end, 1.3)

    async def test_whole_student_branch_reuses_aligned_units_without_second_asr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timings, plan, *_ = _fixture(root)
            record = _record(root, caption_style="none", enhance_speech=True)
            (root / "own_voice.wav").write_bytes(b"untouched-student-upload")
            manifest = (root / "student_narration.json").read_bytes()
            words = [t.model_dump(mode="json") for t in timings]
            with patch.object(pipeline, "synthesize_student_narration", new=AsyncMock(return_value=timings)) as student, patch.object(pipeline, "rebuild_narration_from_existing", new=AsyncMock(return_value=timings)) as rebuild, patch.object(pipeline, "create_tts_provider", side_effect=AssertionError("No TTS for student narration")):
                result = await pipeline._run_tts_synthesis(record, Mock(), [Sentence(sentence_id=t.sentence_id, text=t.text) for t in timings], plan, _settings())
            student.assert_awaited_once()
            self.assertIs(rebuild.await_args.kwargs["enhance_speech"], True)
            self.assertEqual(rebuild.await_args.args[1], timings)
            self.assertEqual([t.model_dump(mode="json") for t in result], words)
            self.assertEqual((root / "own_voice.wav").read_bytes(), b"untouched-student-upload")
            self.assertEqual((root / "student_narration.json").read_bytes(), manifest)

    async def test_initial_render_threads_caption_choice_and_generated_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timings, plan, shots, _ = _fixture(root)
            record = _record(root, caption_style="big", enhance_speech=True)
            with patch.object(pipeline, "prepare_information_card_shots", new=AsyncMock(return_value=[])), patch.object(pipeline, "render_final_video", new_callable=AsyncMock) as render:
                await pipeline._run_video_rendering(record, Mock(), timings, plan, shots, _settings())
            self.assertEqual(render.await_args.kwargs["finish_options"].caption_style, "big")
            self.assertTrue(record.preferences.enhance_speech)

    async def test_remix_retains_both_preferences_and_qc_gate(self) -> None:
        for blocking in (False, True):
            with self.subTest(blocking=blocking), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                timings, *_ = _fixture(root)
                record = _record(root, caption_style="none", enhance_speech=True)
                kept = [timings[0].model_copy(update={"gap_after": 0.0})]
                rebuild = stack.enter_context(patch.object(pipeline, "rebuild_narration_from_existing", new=AsyncMock(return_value=kept)))
                render = stack.enter_context(patch.object(pipeline, "render_from_existing_segments", new_callable=AsyncMock))
                commit = stack.enter_context(patch.object(pipeline, "commit_remix"))
                _post_render_mocks(stack, "backend.pipeline")
                if blocking:
                    stack.enter_context(patch.object(pipeline, "generate_quality_report", return_value={"blocking_issue_count": 1}))
                    with self.assertRaisesRegex(RuntimeError, "质量门禁"):
                        await pipeline.run_remix_pipeline(record, Mock(), [0], 2, _settings())
                    commit.assert_not_called()
                else:
                    await pipeline.run_remix_pipeline(record, Mock(), [0], 2, _settings())
                    commit.assert_called_once()
                self.assertIs(rebuild.await_args.kwargs["enhance_speech"], True)
                self.assertEqual(render.await_args.kwargs["finish_options"].caption_style, "none")
                self.assertEqual(record.preferences, EditingPreferences(caption_style="none", enhance_speech=True))

    async def test_visual_replacement_reuses_narration_and_current_caption_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            _, plan, *_ = _fixture(root)
            record = _record(root, caption_style="none", enhance_speech=True)
            narration = (root / "narration.m4a").read_bytes()
            profile = (root / "narration_profile.json").read_bytes()
            alternative = plan[0].model_copy(update={"shot_id": 2, "candidates": [MatchCandidate(shot_id=2, similarity=0.9)]})
            stack.enter_context(patch.object(pipeline.EmbeddingProvider, "from_settings", return_value=_LocalContext()))
            stack.enter_context(patch.object(pipeline.LLMProvider, "from_settings", return_value=_LocalContext()))
            stack.enter_context(patch.object(pipeline, "build_match_plan", new=AsyncMock(return_value=[alternative])))

            async def segments(*_args: Any, **_kwargs: Any) -> list[Path]:
                path = root / "segments/replacement.mp4"
                path.write_bytes(b"new-segment")
                return [path]

            stack.enter_context(patch.object(pipeline, "render_replacement_segments", side_effect=segments))
            render = stack.enter_context(patch.object(pipeline, "render_from_existing_segments", new_callable=AsyncMock))
            rebuild = stack.enter_context(patch.object(pipeline, "rebuild_narration_from_existing", new=AsyncMock(side_effect=AssertionError("Visual replacement must not process narration again"))))
            stack.enter_context(patch.object(pipeline, "commit_shot_replacement"))
            _post_render_mocks(stack, "backend.pipeline")
            await pipeline.run_shot_replacement_pipeline(record, Mock(), 0, "公园画面", 2, _settings())
            rebuild.assert_not_awaited()
            self.assertEqual(render.await_args.kwargs["finish_options"].caption_style, "none")
            self.assertEqual(render.await_args.kwargs["narration_path"], root / "narration.m4a")
            self.assertEqual((root / "narration.m4a").read_bytes(), narration)
            self.assertEqual((root / "narration_profile.json").read_bytes(), profile)


class WorkbenchPreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_preference_only_edit_preserves_asr_subtitle_baseline_and_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            _fixture(root)
            original = _record(root)
            work = _record(root, caption_style="none", enhance_speech=True)
            before = {name: (root / name).read_bytes() for name in (
                "subs.ass", "subtitle_manifest.json", "student_narration.json", "student_audio/0.wav", "tts/1.wav",
            )}
            payload = workbench.EditRequest(expected_revision=1, keep_sentence_ids=[0, 1], caption_style="none", enhance_speech=True)
            workbench._validate_edit(original, payload, _settings())
            commands = _AudioCommands()
            stack.enter_context(patch.object(tts_pipeline, "run_logged_command", side_effect=commands.__call__))
            stack.enter_context(patch.object(tts_pipeline, "probe_audio_duration", new=AsyncMock(return_value=2.12)))
            stack.enter_context(patch.object(tts_pipeline, "_two_pass_loudnorm_filter", new=AsyncMock(return_value="anull")))
            render = stack.enter_context(patch.object(workbench, "render_from_existing_segments", new_callable=AsyncMock))
            segments = stack.enter_context(patch.object(workbench, "render_replacement_segments", new=AsyncMock(side_effect=AssertionError("Unchanged clocks must reuse segments"))))
            stack.enter_context(patch.object(workbench, "create_tts_provider", side_effect=AssertionError("No provider for preference-only changes")))
            _post_render_mocks(stack, "backend.workbench")
            await workbench._edit_workspace(work, original, payload, _settings())
            segments.assert_not_awaited()
            self.assertEqual(render.await_args.kwargs["finish_options"].caption_style, "none")
            self.assertEqual(before, {name: (root / name).read_bytes() for name in before})
            metadata = workbench._audio_metadata(root)
            self.assertTrue(metadata["0"]["speech_enhancement_applied"])
            self.assertTrue(metadata["0"]["transcript_verified"])
            self.assertFalse(metadata["1"]["speech_enhancement_applied"])
            self.assertEqual(len([c for c in commands.commands if SPEECH_ENHANCEMENT_FILTER in " ".join(c)]), 1)

    async def test_caption_only_edit_does_not_rebuild_or_reprocess_audio(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            _fixture(root)
            original = _record(root, enhance_speech=True)
            work = _record(root, caption_style="big", enhance_speech=True)
            narration = (root / "narration.m4a").read_bytes()
            payload = workbench.EditRequest(expected_revision=1, keep_sentence_ids=[0, 1], caption_style="big")
            workbench._validate_edit(original, payload, _settings())
            rebuild = stack.enter_context(patch.object(workbench, "rebuild_narration_from_existing", new=AsyncMock(side_effect=AssertionError("Caption style is not an audio edit"))))
            render = stack.enter_context(patch.object(workbench, "render_from_existing_segments", new_callable=AsyncMock))
            _post_render_mocks(stack, "backend.workbench")
            await workbench._edit_workspace(work, original, payload, _settings())
            rebuild.assert_not_awaited()
            self.assertEqual((root / "narration.m4a").read_bytes(), narration)
            self.assertEqual(render.await_args.kwargs["finish_options"].caption_style, "big")

    async def test_per_sentence_recording_is_filtered_without_claiming_asr_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root, author = Path(directory) / "workspace", Path(directory) / "author"
            _fixture(root)
            recording_id = "a" * 32
            source = author / f"recordings/{recording_id}.wav"
            _write_pcm(source, channels=1)
            source_bytes = source.read_bytes()
            write_json_atomic(author / f"recordings/{recording_id}.json", {"sentence_id": 0, "revision": 1})
            original = _record(author, caption_style="big", enhance_speech=True)
            work = _record(root, caption_style="big", enhance_speech=True)
            payload = workbench.EditRequest(expected_revision=1, keep_sentence_ids=[0, 1], edits=[workbench.SentenceEdit(sentence_id=0, recording_id=recording_id)])
            commands = _AudioCommands()
            stack.enter_context(patch.object(tts_pipeline, "run_logged_command", side_effect=commands.__call__))
            stack.enter_context(patch.object(tts_pipeline, "probe_audio_duration", new=AsyncMock(return_value=2.12)))
            stack.enter_context(patch.object(tts_pipeline, "_two_pass_loudnorm_filter", new=AsyncMock(return_value="anull")))
            stack.enter_context(patch.object(workbench, "probe_audio_duration", new=AsyncMock(return_value=1.0)))
            stack.enter_context(patch.object(workbench, "render_from_existing_segments", new_callable=AsyncMock))
            stack.enter_context(patch.object(workbench, "create_tts_provider", side_effect=AssertionError("Recording must not invoke TTS")))
            _post_render_mocks(stack, "backend.workbench")
            await workbench._edit_workspace(work, original, payload, _settings())
            metadata = workbench._audio_metadata(root)["0"]
            self.assertEqual(metadata["audio_source"], "recording")
            self.assertFalse(metadata["transcript_verified"])
            self.assertTrue(metadata["speech_enhancement_applied"])
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual((root / f"recordings/{recording_id}.wav").read_bytes(), source_bytes)
            profile = json.loads((root / "narration_profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["speech_enhancement"]["units"][0]["source_audio_path"], f"recordings/{recording_id}.wav")

    def test_current_tts_overrides_old_processing_receipt_and_no_op_stays_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timings, *_ = _fixture(root)
            timings[0].audio_kind = "tts"
            write_json_atomic(root / "timings.json", [t.model_dump(mode="json") for t in timings])
            write_json_atomic(root / "narration_profile.json", {"speech_enhancement": {
                "schema_version": 1, "applied": True, "units": [{"sentence_id": 0}],
            }})
            metadata = workbench._audio_metadata(root)["0"]
            self.assertEqual(metadata["audio_source"], "tts")
            self.assertFalse(metadata["speech_enhancement_applied"])
            self.assertNotIn("transcript_verified", metadata)
            record = _record(root, caption_style="none", enhance_speech=True)
            with self.assertRaisesRegex(ValueError, "No changes"):
                workbench._validate_edit(record, workbench.EditRequest(expected_revision=1, keep_sentence_ids=[0, 1], caption_style="none", enhance_speech=True), _settings())


def _run_local(command: list[str]) -> bytes:
    return subprocess.run(command, check=True, capture_output=True, timeout=120).stdout


def _decode_mono(path: Path) -> tuple[float, ...]:
    pcm = _run_local([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-map", "0:a:0", "-ac", "1", "-ar", str(NARRATION_SAMPLE_RATE), "-f", "f32le", "pipe:1",
    ])
    return struct.unpack(f"<{len(pcm) // 4}f", pcm)


def _rms(samples: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in samples) / len(samples))


def _tone_amplitude(samples: Sequence[float], frequency: float) -> float:
    real = sum(v * math.cos(2 * math.pi * frequency * i / NARRATION_SAMPLE_RATE) for i, v in enumerate(samples))
    imaginary = sum(v * math.sin(2 * math.pi * frequency * i / NARRATION_SAMPLE_RATE) for i, v in enumerate(samples))
    return 2 * math.hypot(real, imaginary) / len(samples)


# These are fixture acceptance bounds, not a claimed FFmpeg latency or an ASR
# guarantee. Keep the original 10 ms resolution, 20 ms clock bound and 75%
# passband-amplitude floor independent of the production filter parameters.
_TONE_BLOCK_SAMPLES = NARRATION_SAMPLE_RATE // 100
_TONE_MAX_SHIFT_BLOCKS = 2
_TONE_MIN_GAIN = 0.75


def _active_rms_blocks(samples: Sequence[float]) -> list[int]:
    block = _TONE_BLOCK_SAMPLES
    return [i for i in range(len(samples) // block)
            if _rms(samples[i * block:(i + 1) * block]) > 0.05]


def _block_span(blocks: Sequence[int]) -> list[int]:
    return [blocks[0], blocks[-1]] if blocks else []


def _assert_tone_clock(
    case: unittest.TestCase, baseline: Sequence[float], result: Sequence[float],
    *, frequency: float, steady: slice,
) -> None:
    """Separate gain from clock position; never realign/rescale the samples."""
    before_level = _tone_amplitude(baseline[steady], frequency)
    after_level = _tone_amplitude(result[steady], frequency)

    def active_blocks(samples: Sequence[float], level: float) -> list[int]:
        block = _TONE_BLOCK_SAMPLES
        # Half of each signal's steady carrier amplitude defines the SAME
        # envelope crossing after allowed gain changes. Broad-band RMS also
        # includes the hum deliberately removed by the filter. The separate
        # 75% gain assertion prevents normalization from excusing lost speech.
        return [i for i in range(len(samples) // block)
                if _tone_amplitude(samples[i * block:(i + 1) * block], frequency) > 0.5 * level]

    before_blocks = active_blocks(baseline, before_level)
    after_blocks = active_blocks(result, after_level)
    shifts = ([after_blocks[0] - before_blocks[0], after_blocks[-1] - before_blocks[-1]]
              if before_blocks and after_blocks else [])
    # Failure-only, synthetic scalar evidence. In particular, retain the old
    # detector and SIGNED shifts so a real FFT delay cannot be relabelled as a
    # gain change merely because decoded sample counts match.
    detail = "synthetic tone clock: " + json.dumps({
        "frequency_hz": frequency, "block_samples": _TONE_BLOCK_SAMPLES,
        "sample_counts": [len(baseline), len(result)],
        "steady_amplitudes": [before_level, after_level],
        "tone_first_last_blocks": [_block_span(before_blocks), _block_span(after_blocks)],
        "signed_onset_tail_shift_blocks": shifts,
        "legacy_rms_first_last_blocks": [
            _block_span(_active_rms_blocks(baseline)), _block_span(_active_rms_blocks(result)),
        ],
    }, sort_keys=True)
    case.assertLessEqual(abs(len(result) - len(baseline)), SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE, detail)
    case.assertGreater(before_level, 0, detail)
    case.assertGreater(after_level, _TONE_MIN_GAIN * before_level, detail)
    case.assertTrue(before_blocks, detail)
    case.assertTrue(after_blocks, detail)
    case.assertLessEqual(abs(shifts[0]), _TONE_MAX_SHIFT_BLOCKS, "onset " + detail)
    case.assertLessEqual(abs(shifts[1]), _TONE_MAX_SHIFT_BLOCKS, "tail " + detail)


def _assert_tone_edges(
    case: unittest.TestCase, baseline: Sequence[float], result: Sequence[float],
    *, frequency: float, partial_samples: int,
) -> None:
    """Check audible boundary data, including the odd final PCM fragment."""
    block = _TONE_BLOCK_SAMPLES
    case.assertLessEqual(abs(len(result) - len(baseline)), SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE)
    for name, section in (("first_10ms", slice(0, block)), ("last_10ms", slice(-block, None))):
        before = _tone_amplitude(baseline[section], frequency)
        after = _tone_amplitude(result[section], frequency)
        detail = f"synthetic tone edge {name}: reference_amplitude={before:.9f}, processed_amplitude={after:.9f}"
        case.assertGreater(before, 0, detail)
        case.assertGreater(after, _TONE_MIN_GAIN * before, detail)
    # A 37-sample fragment is shorter than one carrier cycle; a sinusoidal
    # amplitude projection is not meaningful there. Check actual PCM energy
    # instead, at both ends, without demanding phase/bit-identical samples.
    for name, section in (("first_partial_pcm", slice(0, partial_samples)), ("last_partial_pcm", slice(-partial_samples, None))):
        before, after = _rms(baseline[section]), _rms(result[section])
        detail = f"synthetic tone edge {name}: reference_rms={before:.9f}, processed_rms={after:.9f}"
        case.assertGreater(before, 0, detail)
        case.assertGreater(after, _TONE_MIN_GAIN * before, detail)


class SpeechEnhancementMeasurementTests(unittest.TestCase):
    def test_tone_clock_ignores_allowed_gain_and_hum_removal_not_time(self) -> None:
        # A constructed counterexample to the old absolute RMS detector, NOT
        # evidence that the observed FFmpeg failure was caused by gain alone.
        before: list[float] = []
        after: list[float] = []
        for i in range(2 * NARRATION_SAMPLE_RATE + 37):
            time = i / NARRATION_SAMPLE_RATE
            envelope = max(0.0, min(1.0, (time - 0.4) / 0.2, (1.6 - time) / 0.2))
            hum = 0.04 * math.sin(2 * math.pi * 35 * time)
            tone = 0.12 * envelope * math.sin(2 * math.pi * 880 * time)
            before.append(hum + tone)
            after.append(0.25 * hum + 0.76 * tone)
        self.assertGreater(abs(_active_rms_blocks(before)[-1] - _active_rms_blocks(after)[-1]), 2)
        _assert_tone_clock(self, before, after, frequency=880, steady=slice(33_600, 62_400))

    def test_tone_clock_rejects_equal_length_delay_clipped_edges_and_excess_gain_loss(self) -> None:
        rate = NARRATION_SAMPLE_RATE
        before = [0.12 * math.sin(2 * math.pi * 880 * i / rate) if 0.4 * rate <= i < 1.6 * rate else 0.0
                  for i in range(2 * rate + 37)]
        lost = 3 * _TONE_BLOCK_SAMPLES
        onset, tail = round(0.4 * rate), round(1.6 * rate)
        for name, after in (
            ("delay", [0.0] * lost + before[:-lost]),
            ("advance", before[lost:] + [0.0] * lost),
            ("clipped_onset", before[:onset] + [0.0] * lost + before[onset + lost:]),
            ("clipped_tail", before[:tail - lost] + [0.0] * lost + before[tail:]),
            ("excessive_attenuation", [0.74 * sample for sample in before]),
        ):
            with self.subTest(corruption=name):
                self.assertEqual(len(after), len(before))
                with self.assertRaisesRegex(AssertionError, "synthetic tone clock"):
                    _assert_tone_clock(self, before, after, frequency=880, steady=slice(33_600, 62_400))

    def test_edge_measurement_accepts_gain_but_rejects_silent_partial_pcm(self) -> None:
        before = [0.12 * math.cos(2 * math.pi * 880 * i / NARRATION_SAMPLE_RATE)
                  for i in range(NARRATION_SAMPLE_RATE + 37)]
        after = [0.9 * sample for sample in before]
        _assert_tone_edges(self, before, after, frequency=880, partial_samples=37)
        for name, corrupted in (
            ("first_partial_pcm", [0.0] * 37 + after[37:]),
            ("last_partial_pcm", after[:-37] + [0.0] * 37),
        ):
            with self.subTest(corruption=name):
                self.assertEqual(len(corrupted), len(before))
                with self.assertRaisesRegex(AssertionError, name):
                    _assert_tone_edges(self, before, corrupted, frequency=880, partial_samples=37)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "Optional local FFmpeg/ffprobe required")
class PrototypePreferenceLocalMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_noise_reduction_preserves_odd_sample_count_source_and_speech(self) -> None:
        # Deliberately not an FFT/AAC frame multiple: no padding/truncation is
        # allowed to make the duration assertion pass.
        count = 2 * NARRATION_SAMPLE_RATE + 37

        def waveform(i: int) -> float:
            time = i / NARRATION_SAMPLE_RATE
            hum = 0.04 * math.sin(2 * math.pi * 35 * time)
            voice = 0.12 * math.sin(2 * math.pi * 880 * time) if 0.4 <= time < 1.6 else 0.0
            return hum + voice

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recordings/author.wav"
            _write_pcm(source, frames=count, channels=1, sample=waveform)
            original = source.read_bytes()
            timing = _timing(kind="sync", path="recordings/author.wav").model_copy(update={"duration": count / NARRATION_SAMPLE_RATE, "end": count / NARRATION_SAMPLE_RATE})
            original_timing = timing.model_dump(mode="json")
            temporary = root / "derivatives"
            temporary.mkdir()
            processed, evidence = await tts_pipeline._prepare_enhanced_recording(root, timing, temporary, 0)
            # Compare at the same channel layout as the actual effect, not a
            # mono/stereo rematrix gain difference mistaken for noise reduction.
            reference = root / "reference.wav"
            _run_local([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-af", NARRATION_AUDIO_FORMAT, "-c:a", "pcm_s16le", str(reference),
            ])
            baseline, result = _decode_mono(reference), _decode_mono(processed)
            self.assertLessEqual(abs(len(result) - len(baseline)), SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE)
            self.assertEqual(evidence["source_decoded_samples"], count)
            noise = slice(round(0.08 * NARRATION_SAMPLE_RATE), round(0.32 * NARRATION_SAMPLE_RATE))
            voice = slice(round(0.7 * NARRATION_SAMPLE_RATE), round(1.3 * NARRATION_SAMPLE_RATE))
            self.assertLess(_rms(result[noise]), 0.5 * _rms(baseline[noise]))
            # This synthetic carrier is a clock/content-loss sentinel, not a
            # real-speech intelligibility or ASR equivalence test. Keep the
            # 75% passband floor AND the original <=20 ms edge bound. The
            # measurement itself never re-aligns or pads the resulting audio.
            _assert_tone_clock(self, baseline, result, frequency=880, steady=voice)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(timing.model_dump(mode="json"), original_timing)
            repeated, repeated_evidence = await tts_pipeline._prepare_enhanced_recording(root, timing, temporary, 1)
            self.assertEqual(processed.read_bytes(), repeated.read_bytes())
            self.assertEqual(evidence["source_sha256"], repeated_evidence["source_sha256"])

    async def test_real_enhancement_preserves_nonzero_onset_and_odd_pcm_tail(self) -> None:
        # The old fixture has hum-only margins. A full-duration cosine makes
        # delayed/zero-filled boundaries observable, even if the WAV length is
        # correct. Test the derivative BEFORE assembly's existing fades/AAC.
        count = NARRATION_SAMPLE_RATE + 37
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recordings/edge-carrier.wav"
            _write_pcm(source, frames=count, channels=1, sample=lambda i: 0.12 * math.cos(2 * math.pi * 880 * i / NARRATION_SAMPLE_RATE))
            original = source.read_bytes()
            timing = _timing(kind="sync", path="recordings/edge-carrier.wav").model_copy(update={
                "duration": count / NARRATION_SAMPLE_RATE, "end": count / NARRATION_SAMPLE_RATE,
            })
            original_timing = timing.model_dump(mode="json")
            temporary = root / "derivatives"
            temporary.mkdir()
            processed, evidence = await tts_pipeline._prepare_enhanced_recording(root, timing, temporary, 0)
            reference = root / "reference.wav"
            _run_local([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-af", NARRATION_AUDIO_FORMAT, "-c:a", "pcm_s16le", str(reference),
            ])
            baseline, result = _decode_mono(reference), _decode_mono(processed)
            self.assertEqual(len(baseline), count)
            self.assertEqual(evidence["source_decoded_samples"], count)
            self.assertEqual(evidence["processed_samples"], len(result))
            self.assertGreater(abs(baseline[0]), 1 / 32768)
            self.assertGreater(abs(baseline[-1]), 1 / 32768)
            _assert_tone_edges(self, baseline, result, frequency=880, partial_samples=37)
            self.assertLess(max(abs(sample) for sample in result), 1.0, "DSP must not hard-clip this low-level fixture")
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(timing.model_dump(mode="json"), original_timing)

    async def test_real_mixed_audio_keeps_tts_pcm_and_remix_never_reuses_filtered_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timings = [_timing(0, gap=0.12), _timing(1, kind="sync", path="recordings/1.wav", start=1.12)]
            _write_pcm(root / timings[0].audio_path, channels=1, sample=lambda i: 0.1 * math.sin(2 * math.pi * 880 * i / NARRATION_SAMPLE_RATE))
            _write_pcm(root / timings[1].audio_path, channels=1, sample=lambda i: 0.04 * math.sin(2 * math.pi * 35 * i / NARRATION_SAMPLE_RATE) + 0.12 * math.sin(2 * math.pi * 440 * i / NARRATION_SAMPLE_RATE))
            originals = {t.audio_path: (root / t.audio_path).read_bytes() for t in timings}
            baseline = await concatenate_narration(root, timings, output_path=root / "baseline.m4a")
            enhanced = await concatenate_narration(root, timings, output_path=root / "enhanced.m4a", enhance_speech=True)
            before, after = _decode_mono(baseline), _decode_mono(enhanced)
            self.assertLessEqual(abs(len(after) - len(before)), SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE)
            tts_slice = slice(round(0.2 * NARRATION_SAMPLE_RATE), round(0.75 * NARRATION_SAMPLE_RATE))
            self.assertLessEqual(max(abs(a - b) for a, b in zip(before[tts_slice], after[tts_slice], strict=True)), 1 / 32768)
            recorded_slice = slice(round(1.4 * NARRATION_SAMPLE_RATE), round(1.95 * NARRATION_SAMPLE_RATE))
            self.assertLess(_tone_amplitude(after[recorded_slice], 35), 0.5 * _tone_amplitude(before[recorded_slice], 35))
            self.assertGreater(_tone_amplitude(after[recorded_slice], 440), 0.75 * _tone_amplitude(before[recorded_slice], 440))
            await rebuild_narration_from_existing(
                root, timings, [1], timings_path=root / "timings.remix.json", narration_path=root / "narration.remix.m4a",
                narration_profile_path=root / "narration_profile.remix.json", enhance_speech=True,
            )
            first = json.loads((root / "narration_profile.json").read_text(encoding="utf-8"))["speech_enhancement"]["units"][0]
            remixed = json.loads((root / "narration_profile.remix.json").read_text(encoding="utf-8"))["speech_enhancement"]["units"][0]
            self.assertEqual(first["source_sha256"], remixed["source_sha256"])
            self.assertEqual(first["processed_pcm_sha256"], remixed["processed_pcm_sha256"])
            self.assertEqual({p: (root / p).read_bytes() for p in originals}, originals)
            self.assertLessEqual(abs(await probe_audio_duration(root / "narration.remix.m4a", root) - 1), 1024 / NARRATION_SAMPLE_RATE)
            self.assertFalse(list(root.glob(".speech-enhance-*")))

    async def test_real_large_stable_caption_is_not_clipped_or_outside_safe_area(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "位于南宁市西乡塘区鲁班路九十五号的南宁信息港广场今日举办迎春市集活动市民游客前来体验传统手工与地方美食"
            generate_ass_subtitles(root, [_timing(text=text)], stable_sentence_ids={0})
            font_dir = rendering._prepare_ass_font_directory(root, Path(rendering.__file__).parent / "assets/fonts")
            with subtitle_burn_in_artifact(root / "subs.ass", root / "subtitle_manifest.json", caption_style="big") as burner:
                unclipped = root / "unclipped-reference.ass"
                unclipped.write_text(re.sub(r"\\clip\([^)]*\)", "", burner.read_text(encoding="utf-8")), encoding="utf-8")
                frames = []
                for path in (burner, unclipped):
                    raw = _run_local([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30:d=1",
                        "-vf", rendering._build_subtitle_filter(root, path, font_dir), "-ss", "0.5",
                        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                    ])
                    frames.append(np.frombuffer(raw, dtype=np.uint8).reshape((1080, 1920, 3)))
                self.assertTrue(np.array_equal(frames[0], frames[1]), "Safe-area clip must not remove any rendered glyph pixels")
                ys, xs = np.nonzero(np.all(frames[0] > 180, axis=2))
                self.assertGreater(len(xs), 100)
                self.assertGreaterEqual(int(xs.min()), 192)
                self.assertLess(int(xs.max()), 1728)
                self.assertGreaterEqual(int(ys.min()), 108)
                self.assertLess(int(ys.max()), 972)

    async def test_real_caption_pixels_big_none_title_disclosure_and_source_overlay(self) -> None:
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video_only.mp4"
            narration = root / "narration.wav"
            _run_local([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30:d=1",
                "-vf", "drawbox=x=220:y=950:w=70:h=14:color=lime:t=fill", "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video),
            ])
            _write_pcm(narration, channels=1, sample=lambda i: 0.1 * math.sin(2 * math.pi * 440 * i / NARRATION_SAMPLE_RATE))
            generate_ass_subtitles(root, [_timing()], title="城市新闻")
            graphics = generate_news_graphics(root, topic="地方新闻", headline="", total_duration=1, disclosure_intervals=[(0, 1)])
            assert graphics is not None
            originals = {name: (root / name).read_bytes() for name in ("subs.ass", "subtitle_manifest.json", "graphics.ass", "video_only.mp4", "narration.wav")}
            boxes: dict[str, tuple[int, int, int]] = {}
            for style in ("news", "big", "none"):
                final = root / f"{style}.mp4"
                await rendering._mix_subtitles_and_narration(
                    root, video, narration, root / "subs.ass", final,
                    subtitle_manifest_path=root / "subtitle_manifest.json",
                    finish_options=rendering.FinishOptions(caption_style=cast(CaptionStyle, style), graphics_path=graphics),
                )
                await rendering.validate_final_video(root, final, narration, subtitles_path=root / "subs.ass", subtitle_manifest_path=root / "subtitle_manifest.json")
                capture = cv2.VideoCapture(str(final))
                try:
                    capture.set(cv2.CAP_PROP_POS_MSEC, 500)
                    ok, frame = capture.read()
                finally:
                    capture.release()
                self.assertTrue(ok)
                self.assertIsNotNone(frame)
                assert frame is not None
                body = np.all(frame[810:990, 192:1728] > 180, axis=2)
                ys, xs = np.nonzero(body)
                boxes[style] = (len(xs), int(np.ptp(xs)) if len(xs) else 0, int(np.ptp(ys)) if len(ys) else 0)
                # None is not an eraser: retain a green source-burned overlay.
                self.assertGreater(int(frame[956, 250, 1]), 150)
                self.assertLess(int(frame[956, 250, 2]), 90)
                title = frame[180:360, 250:1670]
                self.assertGreater(int(np.count_nonzero((title[:, :, 2] > 170) & (title[:, :, 1] > 140) & (title[:, :, 0] < 120))), 100)
                disclosure = frame[20:100, 1450:1900]
                self.assertGreater(int(np.count_nonzero(np.all(disclosure > 180, axis=2))), 100)
                topic = frame[50:120, 90:700]
                self.assertGreater(int(np.count_nonzero(np.all(topic > 180, axis=2))), 100)
            self.assertGreater(boxes["news"][0], 100)
            self.assertGreater(boxes["big"][0], boxes["news"][0] * 1.5)
            self.assertGreater(boxes["big"][1], boxes["news"][1] * 1.3)
            self.assertGreater(boxes["big"][2], boxes["news"][2] * 1.2)
            self.assertEqual(boxes["none"][0], 0)
            self.assertEqual(originals, {name: (root / name).read_bytes() for name in originals})
            self.assertFalse(list(root.glob(".caption-burn-*")))


if __name__ == "__main__":
    unittest.main()