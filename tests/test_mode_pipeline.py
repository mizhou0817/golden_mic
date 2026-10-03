"""Isolated mode tests: synthetic ASR/voices, real local FFmpeg media.

Run explicitly, never discovery. The __main__ runner sanitizes the environment
before product imports, blocks dotenv/data/network access, and allows only local
FFmpeg/ffprobe subprocesses. No speech recognition accuracy or paid/live provider
claim is made by these synthetic fixtures.
"""
from __future__ import annotations

import asyncio
import ast
import copy
import hashlib
import importlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
mp = None


def load_product():
    global mp
    if mp is None:
        mp = importlib.import_module("backend.mode_pipeline")
    return mp


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(command):
    result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace")[-4000:])
    return result.stdout


def video_frame(path, at):
    return invoke(["ffmpeg", "-v", "error", "-ss", f"{at:.6f}", "-i", str(path), "-map", "0:v:0",
                   "-frames:v", "1", "-vf", "scale=160:90", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"])


def audio_bytes(path):
    return invoke(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "copy", "-f", "adts", "pipe:1"])


def speech_snapshot(upload_id="u", seconds=10.0, offset=0.0):
    # Deliberately uneven word durations. These are synthetic ground truth labels,
    # not inferred from the tone fixture and not acoustically validated speech.
    utterances = [
        {"text": "今天活动开幕。", "start_time_ms": 500, "end_time_ms": 2200, "speaker_id": "1",
         "words": [{"text": "今天", "start_time_ms": 500, "end_time_ms": 750},
                   {"text": "活动", "start_time_ms": 800, "end_time_ms": 1530},
                   {"text": "开幕", "start_time_ms": 1550, "end_time_ms": 2200}]},
        {"text": "欢迎大家参与。", "start_time_ms": 2300, "end_time_ms": 4300, "speaker_id": "1",
         "words": [{"text": "欢迎", "start_time_ms": 2300, "end_time_ms": 2720},
                   {"text": "大家", "start_time_ms": 2800, "end_time_ms": 3500},
                   {"text": "参与", "start_time_ms": 3570, "end_time_ms": 4300}]},
        {"text": "今天天气很好。", "start_time_ms": 6200, "end_time_ms": 8350, "speaker_id": "1",
         "words": [{"text": "今天", "start_time_ms": 6200, "end_time_ms": 6730},
                   {"text": "天气", "start_time_ms": 6810, "end_time_ms": 7260},
                   {"text": "很好", "start_time_ms": 7330, "end_time_ms": 8350}]},
    ]
    transcript = {"text": "".join(u["text"] for u in utterances), "duration_ms": int(seconds * 1000), "utterances": utterances}
    provider = load_product().ASRTranscript.model_validate(transcript)
    segments = load_product()._provider_segments(provider, upload_id, seconds, offset)
    return {"id": upload_id, "name": "synthetic.mp4", "sec": seconds, "status": "ready",
            "has_speech": True, "transcript": {"segments": segments}, "silences": [],
            "asr_result": provider.model_dump(mode="json"), "audio_offset_seconds": offset}


class Reporter:
    def __init__(self):
        self.started, self.completed, self.fractions = [], [], []

    def start_stage(self, record, number, message):
        self.started.append(number)

    def update_stage(self, record, number, fraction, message):
        assert 0 <= fraction <= 1
        self.fractions.append((number, fraction, message))

    def complete_stage(self, record, number, message):
        self.completed.append(number)


class FakeVoice:
    supports_word_timings = False

    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def validate_configuration(self):
        return None

    def set_document_context(self, text):
        self.context = text

    def synthesis_profile(self):
        return {"provider": "isolated-synthetic-tone", "voice": "fixture"}

    async def synthesize(self, text, output):
        self.calls.append(text)
        duration = max(1.3, len(text) * 60 / 265)
        invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                f"sine=frequency=710:sample_rate=48000:duration={duration:.6f}",
                "-c:a", "libmp3lame", "-b:a", "128k", str(output)])
        return []


class FakeVision:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def validate_configuration(self):
        return None

    async def annotate_image(self, path):
        from backend.models import VisionAnnotation, VisionQuality
        assert path.is_file()
        return VisionAnnotation(description="合成测试图案（非真实语义验证）", scene_type="unknown", subjects=[], actions=[],
                                keywords=["合成", "图案", "测试"], quality=VisionQuality(sharp=0.7, bright=0.7))


class FakeContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


async def fake_match(root, sentences, shots, embedding, llm, progress, **kwargs):
    from backend.models import MatchCandidate, MatchPlanItem, BeatMatch
    result = []
    for sentence, shot in zip(sentences, shots, strict=False):
        assert sentence.kind == "narration" and len(sentence.visual_beats) == 1
        candidate = MatchCandidate(shot_id=shot.shot_id, similarity=0.9)
        result.append(MatchPlanItem(sentence_id=sentence.sentence_id, text=sentence.text, shot_id=shot.shot_id,
                                    confidence=0.9, candidates=[candidate],
                                    beat_matches=[BeatMatch(beat_id=0, text=sentence.text, shot_id=shot.shot_id,
                                                           confidence=0.9, candidates=[candidate])]))
    progress(1.0, "isolated synthetic matching result")
    return result


class ModeContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_product()

    def test_exact_public_signatures_and_no_top_level_pipeline_cycle(self):
        import inspect
        self.assertEqual(list(inspect.signature(mp.edit_mode_workspace).parameters), ["work", "original", "payload", "settings"])
        tree = ast.parse((ROOT / "backend/mode_pipeline.py").read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.module in {"pipeline", "main", "task_manager", "workbench"} for node in tree.body))

    def test_preview_parity_and_uneven_words(self):
        snapshot = speech_snapshot()
        sentences = [mp.SentenceInput(idx=0, text="今天活动开幕。", kind="quote")]
        source = mp.rules.align_quotes(sentences, [snapshot])[0]["source"]
        self.assertEqual(source["words"], snapshot["transcript"]["segments"][0]["words"])
        self.assertAlmostEqual(source["start"], 0.38)
        self.assertAlmostEqual(source["end"], 2.3)  # next utterance bounds the tail

    def test_single_visual_beat_and_client_text_is_authoritative(self):
        inputs = [mp.SentenceInput(idx=4, text="这是客户端的很长一句，包含多个停顿，但不能重写。", kind="narration")]
        output = mp._sentences(inputs)
        self.assertEqual(output[0].text, inputs[0].text)
        self.assertEqual(len(output[0].visual_beats), 1)
        self.assertEqual(output[0].sentence_id, 4)

    def test_continuous_quotes_have_no_duplicated_handles(self):
        snapshot = speech_snapshot()
        inputs = [mp.SentenceInput(idx=i, text=text, kind="quote") for i, text in enumerate(["今天活动开幕。", "欢迎大家参与。"])]
        aligned = mp.rules.align_quotes(inputs, [snapshot])
        plan = [mp.MatchPlanItem(sentence_id=i, text=inputs[i].text, kind="quote", source=mp.QuoteTake.model_validate(row["source"]),
                                 shot_id=i, confidence=row["score"], candidates=[]) for i, row in enumerate(aligned)]
        clocks = {"u": {"prepared_start": 0, "prepared_end": 10, "audio_offset_seconds": 0}}
        layout, groups = mp.quote_layout(plan, [snapshot], clocks)
        self.assertEqual(groups, [[0, 1]])
        self.assertEqual(layout[0]["end_sample"], layout[1]["start_sample"])
        self.assertAlmostEqual(layout[0]["end"], 2.25)
        self.assertEqual(plan[0].source.model_dump(mode="json"), aligned[0]["source"])

    def test_explicit_trim_is_not_extended_by_continuity_midpoint(self):
        snapshot = speech_snapshot()
        inputs = [mp.SentenceInput(idx=i, text=text, kind="quote") for i, text in enumerate(["今天活动开幕。", "欢迎大家参与。"])]
        rows = mp.rules.align_quotes(inputs, [snapshot])
        plan = [mp.MatchPlanItem(sentence_id=i, text=inputs[i].text, kind="quote", source=row["source"],
                                 shot_id=i, confidence=1, candidates=[]) for i, row in enumerate(rows)]
        plan[0].source = mp.QuoteTake.model_validate(mp.rules.validate_quote_trim(plan[0].source, 0.5, 2.2))
        plan[0].trim = {"start": 0.5, "end": 2.2}
        plan[1].source = mp.QuoteTake.model_validate(mp.rules.validate_quote_trim(plan[1].source, 2.3, 4.3))
        plan[1].trim = {"start": 2.3, "end": 4.3}
        layout, groups = mp.quote_layout(plan, [snapshot], {"u": {"prepared_start": 0, "prepared_end": 10, "audio_offset_seconds": 0}})
        self.assertEqual((layout[0]["end"], layout[1]["start"]), (2.2, 2.3))
        self.assertEqual(groups, [[0], [1]])

    def test_unknown_speakers_are_not_continuous(self):
        snapshot = speech_snapshot()
        rows = mp.rules.align_quotes([mp.SentenceInput(idx=i, text=text, kind="quote") for i, text in enumerate(["今天活动开幕。", "欢迎大家参与。"])], [snapshot])
        plan = [mp.MatchPlanItem(sentence_id=i, text=row["source"]["asr_text"], kind="quote", source=row["source"],
                                 shot_id=i, confidence=1, candidates=[]) for i, row in enumerate(rows)]
        for item in plan:
            item.source.speaker_id = ""
        self.assertFalse(mp._continuous(*plan, {"u": snapshot}))

    def test_broll_includes_measured_speech_file_silence_not_untranscribed_gap(self):
        snapshot = speech_snapshot()
        snapshot["silences"] = [[4.8, 5.9], [6.1, 7.0]]
        clock = {"prepared_start": 0, "prepared_end": 10}
        self.assertEqual(mp.broll_source_intervals(snapshot, clock, "original"), [(4.8, 5.9), (6.1, 6.2)])
        self.assertEqual(mp.broll_source_intervals(snapshot, clock, "voiceover"), [(0, 10)])

    def test_missing_quote_error_is_one_based_and_truthful(self):
        error = mp.QuoteMissingError([{"idx": 3, "score": 0.51}])
        self.assertEqual(error.bad_rows, [4])
        self.assertEqual(error.error_kind, "quote_missing")
        self.assertIn("0.51", str(error))
        self.assertIn("不会改用配音", str(error))

    def test_new_warn_alias_legacy_error_and_quote_visual_exemption(self):
        from backend.quality import generate_quality_report, MODE_CHECK_CODE_ALIASES
        from backend.models import ScriptDocument
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentence = mp.Sentence(sentence_id=0, text="王红说有3个人参加", kind="narration")
            mp.write_json_atomic(root / "script_structure.json", ScriptDocument(title="标题", sentences=[sentence]).model_dump(mode="json"))
            plan = [mp.MatchPlanItem(sentence_id=0, text=sentence.text, shot_id=0, confidence=0.9, candidates=[])]
            timing = mp.SentenceTiming(sentence_id=0, text=sentence.text, audio_path="tts/fixture.wav", duration=2.0, start=0, end=2,
                                       speaking_rate_cpm=500, spoken_unit_count=10, gap_after=0)
            metrics = {"duration_seconds": 2, "integrated_lufs": -20.0, "true_peak_dbfs": -4.0}
            with patch("backend.quality._media_quality_metrics", return_value=metrics), patch("backend.quality._sentence_loudness_metrics", return_value=[{"sentence_id": 0, "integrated_lufs": -20.0, "true_peak_dbfs": -4.0}]):
                legacy = generate_quality_report(root, [], plan, [timing], minimum_confidence=0.5)
                warn = generate_quality_report(root, [], plan, [timing], minimum_confidence=0.5, mode="voiceover", gate_mode="warn")
                block = generate_quality_report(root, [], plan, [timing], minimum_confidence=0.5, mode="voiceover", gate_mode="block")
                code = "NARRATION_SPEAKING_RATE_OUT_OF_RANGE"
                self.assertEqual([i["severity"] for r in (legacy, warn, block) for i in r["issues"] if i["code"] == code], ["error", "warning", "error"])
                self.assertEqual(MODE_CHECK_CODE_ALIASES[code], "NARRATION_SPEAKING_RATE")
                self.assertEqual(sum(i["code"] == "FACT_CHECK" for i in warn["issues"]), 1)
                self.assertEqual(warn["checks"], warn["issues"])

    def test_lower_thirds_first_show_short_clip_and_sixty_second_repeat(self):
        from backend.graphics import generate_mode_graphics
        snapshot = speech_snapshot()
        source = mp.QuoteTake.model_validate(mp.rules.align_quotes([mp.SentenceInput(idx=0, kind="quote", text="今天活动开幕")], [snapshot])[0]["source"])
        plan = [mp.MatchPlanItem(sentence_id=i, text="原话", kind="quote", source=source, shot_id=i, confidence=1, candidates=[]) for i in range(3)]
        timings = [mp.SentenceTiming(sentence_id=i, text="原话", audio_path="fixture.wav", duration=duration, start=start, end=start + duration, audio_kind="sync", gap_after=0)
                   for i, (start, duration) in enumerate([(0, 1.5), (5, 2.0), (65, 3.0)])]
        with tempfile.TemporaryDirectory() as directory:
            path, receipts = generate_mode_graphics(Path(directory), timings, plan, [], mp.EditingPreferences(caption_style="none"))
            self.assertEqual([(r["start"], r["end"]) for r in receipts], [(0, 1.5), (65, 67.5)])
            self.assertIn("受访者", path.read_text(encoding="utf-8"))
            self.assertIn("3CA8E2", path.read_text(encoding="utf-8"))

    def test_segment_caption_does_not_invent_words_or_distribute_time(self):
        from backend.graphics import generate_mode_subtitles, validate_mode_subtitle_artifacts
        text = "只有整段时间的原话不能被偷偷均分成多个伪造的逐词字幕"
        source = mp.QuoteTake(take_id="s", upload_id="u", start=0.0, end=4.0, speaker_id="", asr_text=text, score=1, words=[])
        plan = [mp.MatchPlanItem(sentence_id=0, text="稿子与原话不同", kind="quote", source=source, shot_id=0, confidence=1, candidates=[])]
        timings = [mp.SentenceTiming(sentence_id=0, text=text, audio_path="fixture.wav", start=0, end=4, duration=4, audio_kind="sync", gap_after=0)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = generate_mode_subtitles(root, timings, plan, mp.EditingPreferences(caption_style="none"))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["precision"], "segment")
            self.assertEqual(events[0]["text"].replace("\n", ""), text)
            self.assertTrue(all(len(line) <= 18 for line in events[0]["text"].split("\n")))
            validate_mode_subtitle_artifacts(root / "subs.ass", root / "subtitle_manifest.json")
            (root / "subs.ass").write_text((root / "subs.ass").read_text(encoding="utf-8").replace("54,", "55,"), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_mode_subtitle_artifacts(root / "subs.ass", root / "subtitle_manifest.json")

    def test_quote_visual_rules_and_level_two_hint_are_not_confirmation_count(self):
        from backend.models import ScriptDocument
        from backend.quality import generate_quality_report
        snapshot = speech_snapshot()
        source = mp.QuoteTake.model_validate(mp.rules.align_quotes([mp.SentenceInput(idx=0, text="今天活动开幕。", kind="quote")], [snapshot])[0]["source"])
        plan = [mp.MatchPlanItem(sentence_id=0, text="今天活动开幕。", kind="quote", source=source, shot_id=0, confidence=0.1, is_fallback=True, candidates=[])]
        timing = mp.SentenceTiming(sentence_id=0, text=source.asr_text, audio_path="fixture.wav", start=0, end=1.92, duration=1.92, audio_kind="sync", gap_after=0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mp.write_json_atomic(root / "script_structure.json", ScriptDocument(sentences=mp._sentences([mp.SentenceInput(idx=0, text=source.asr_text, kind="quote")])).model_dump(mode="json"))
            with patch("backend.quality._media_quality_metrics", return_value={"duration_seconds": 1.92, "integrated_lufs": -20.0, "true_peak_dbfs": -4.0}), patch("backend.quality._sentence_loudness_metrics", return_value=[{"sentence_id": 0, "integrated_lufs": -20.0, "true_peak_dbfs": -4.0}]):
                report = generate_quality_report(root, [], plan, [timing], minimum_confidence=0.5, mode="mixed", preferences={"lower_third": False})
            codes = {i["code"] for i in report["issues"]}
            self.assertNotIn("MATCH_FALLBACK", codes)
            self.assertNotIn("LOW_MATCH_CONFIDENCE", codes)
            self.assertIn("MIXED_NO_NARRATION", codes)
            self.assertEqual(report["warning_count"], 0)
            self.assertEqual(report["metrics"]["informational_count"], 1)

    def test_boundary_conditions_refuse_fake_range_and_unique_identity(self):
        manifest = {"next_shot_id": 5}
        self.assertEqual(mp._physical_shot_id(manifest, "u", 1.0, 2.0), 5)
        self.assertEqual(mp._physical_shot_id(manifest, "u", 1.0, 2.0), 5)
        self.assertEqual(mp._physical_shot_id(manifest, "u", 2.0, 3.0), 6)
        snapshot = speech_snapshot()
        source = mp.QuoteTake.model_validate(mp.rules.align_quotes([mp.SentenceInput(idx=0, text="今天活动开幕。", kind="quote")], [snapshot])[0]["source"])
        plan = [mp.MatchPlanItem(sentence_id=0, text=source.asr_text, kind="quote", source=source, shot_id=0, confidence=1, candidates=[])]
        with self.assertRaises(ValueError):
            mp.quote_layout(plan, [snapshot], {"u": {"prepared_start": 1.0, "prepared_end": 9.0, "audio_offset_seconds": 0.0}})

    def test_literal_word_caption_clocks_are_not_equal_slices(self):
        from backend.graphics import generate_mode_subtitles
        text = "原声字幕必须保留真正说出口的内容不能按稿件平均分配"
        words = [mp.TTSWordTiming(text=text[:18], start=0.12, end=0.63), mp.TTSWordTiming(text=text[18:], start=0.9, end=3.6)]
        source = mp.QuoteTake(take_id="uneven", upload_id="u", start=0, end=4, speaker_id="", asr_text=text, score=1,
                             words=[mp.Word(w=w.text, s=w.start, e=w.end) for w in words])
        plan = [mp.MatchPlanItem(sentence_id=0, text="不同的稿件", source=source, kind="quote", shot_id=0, confidence=1, candidates=[])]
        timing = mp.SentenceTiming(sentence_id=0, text=text, audio_path="fixture.wav", start=0, end=4, duration=4, audio_kind="sync", gap_after=0, words=words)
        with tempfile.TemporaryDirectory() as directory:
            events = generate_mode_subtitles(Path(directory), [timing], plan, mp.EditingPreferences())
        self.assertEqual([(e["start"], e["end"]) for e in events], [(0.12, 0.63), (0.9, 3.6)])


class ModeEdgeContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        load_product()

    async def test_mixed_pool_excludes_quote_plus_minus_two_seconds(self):
        from backend.models import VisionQuality
        snapshot = speech_snapshot()
        take = mp.QuoteTake(take_id="range", upload_id="u", start=4, end=6, speaker_id="", asr_text="原话", score=1)
        plan = [mp.MatchPlanItem(sentence_id=0, text="原话", kind="quote", source=take, shot_id=1, confidence=1, candidates=[])]
        shot = mp.AnnotatedShot(shot_id=0, source_index=0, source_scene_index=0, source_name="fixture", norm_path="norm/norm_0.mp4",
                                start=0, end=10, duration=10, status="available", description="测试画面", quality=VisionQuality(sharp=0.8, bright=0.8))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {"mode": "mixed", "next_shot_id": 2, "source_clocks": {"u": {"source_index": 0, "norm_source_offset": 0}}}
            async def thumb(task_dir, value):
                path = task_dir / "thumbs" / f"shot_{value.shot_id}.jpg"
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"synthetic-not-media")
                return path
            with patch("backend.mode_pipeline.extract_shot_thumbnail", side_effect=thumb):
                result = await mp._exclude_quotes(SimpleNamespace(task_dir=root), [shot], plan, manifest)
            self.assertEqual([(s.start, s.end) for s in result], [(0, 2), (8, 10)])

    async def test_edit_guard_rejects_quote_text_even_in_conversion_batch(self):
        from backend.config import Settings
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            root, original_root = Path(a), Path(b)
            source = mp.QuoteTake(take_id="guard", upload_id="u", start=0, end=2, speaker_id="", asr_text="原话", score=1)
            item = mp.MatchPlanItem(sentence_id=0, text="原话", kind="quote", source=source, shot_id=0, confidence=1, candidates=[])
            mp.write_json_atomic(root / mp.MODE_MANIFEST, {"recipe": mp.MODE_RECIPE, "mode": "mixed"})
            mp._write_models(root, "match_plan.json", [item])
            original = SimpleNamespace(task_dir=original_root, mode="mixed", preferences=mp.EditingPreferences())
            work = SimpleNamespace(task_dir=root, preferences=mp.EditingPreferences())
            with self.assertRaisesRegex(ValueError, "先单独转为旁白"):
                await mp.edit_mode_workspace(work, original, {"keep_sentence_ids": [0], "to_narration": [0], "edits": [{"sentence_id": 0, "text": "伪造新词"}]}, Settings(_env_file=None, data_dir=root, app_env="test"))

    async def test_explicit_quote_cannot_enter_legacy_tts_even_by_accidental_call(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeVoice()
            with self.assertRaisesRegex(mp.TTSProcessingError, "显式原声"):
                await mp.synthesize_narration(Path(directory), [mp.Sentence(sentence_id=0, text="真实原声", kind="quote")], provider, lambda *_: None)
            self.assertEqual(provider.calls, [])

    async def test_matching_offloop_heartbeat_and_repeated_cancel_drains(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        worker_threads = []
        def blocking(*_):
            worker_threads.append(threading.get_ident())
            started.set()
            if not release.wait(5):
                raise AssertionError("worker was not released")
            finished.set()
            return []
        with patch.object(mp.rules, "align_quotes", side_effect=blocking):
            task = asyncio.create_task(mp._align_quotes_offloop([], [], []))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                heartbeat = asyncio.Event()
                asyncio.get_running_loop().call_soon(heartbeat.set)
                await asyncio.wait_for(heartbeat.wait(), 1)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done(), "cancellation must drain the live worker")
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())
        self.assertNotEqual(worker_threads, [loop_thread])

    async def test_initial_pipeline_uses_offloop_and_edits_do_not_rematch_quotes(self):
        import inspect
        initial = ast.parse(inspect.getsource(mp.run_mode_pipeline))
        self.assertTrue(any(isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                            and isinstance(n.value.func, ast.Name) and n.value.func.id == "_align_quotes_offloop"
                            for n in ast.walk(initial)))
        edit = inspect.getsource(mp.edit_mode_workspace)
        self.assertNotIn("align_quotes(", edit)


class ModeMediaIntegration(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        load_product()
        cls.fixture_dir = tempfile.TemporaryDirectory(prefix="mode-fixture-")
        cls.source = Path(cls.fixture_dir.name) / "speech.mp4"
        cls.broll = Path(cls.fixture_dir.name) / "broll.mp4"
        invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=10",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=10",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "192k", "-shortest", str(cls.source)])
        invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=7",
                "-vf", "hue=h=80", "-c:v", "libx264", "-preset", "ultrafast", "-an", str(cls.broll)])

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    async def asyncSetUp(self):
        from backend.config import Settings
        self.temp = tempfile.TemporaryDirectory(prefix="mode-case-")
        self.root = Path(self.temp.name)
        (self.root / "raw").mkdir()
        self.settings = Settings(_env_file=None, app_env="test", data_dir=self.root,
                                 video_embedding_enabled=False, entity_verification_enabled=False,
                                 sync_sound_enabled=True, quality_gate_mode="warn")
        # Keep useful tracebacks without enormous debug slow-callback logs.
        asyncio.get_running_loop().slow_callback_duration = 10.0

    async def asyncTearDown(self):
        self.temp.cleanup()

    def record(self, mode="original", with_broll=False, texts=None):
        from backend.models import UploadedAsset
        raw = self.root / "raw" / ("a" * 32 + ".mp4")
        shutil.copy2(self.source, raw)
        uploads = [UploadedAsset(upload_id="u", original_name="speech.mp4", stored_name=raw.name, path=raw,
                                  size=raw.stat().st_size, content_type="video/mp4", source_duration_seconds=10)]
        snapshots = [speech_snapshot()]
        if with_broll:
            path = self.root / "raw" / ("b" * 32 + ".mp4")
            shutil.copy2(self.broll, path)
            uploads.append(UploadedAsset(upload_id="b", original_name="broll.mp4", stored_name=path.name, path=path,
                                         size=path.stat().st_size, content_type="video/mp4", source_duration_seconds=7))
            snapshots.append({"id": "b", "name": "broll.mp4", "sec": 7.0, "status": "ready", "has_speech": False,
                              "transcript": [], "silences": [], "asr_result": None, "audio_offset_seconds": 0.0})
        texts = texts or ([('今天活动开幕。', 'quote'), ('今天天气很好。', 'quote')] if mode == "original" else [('现场活动开始了。', 'narration')])
        sentences = [mp.SentenceInput(idx=i, text=text, kind=kind) for i, (text, kind) in enumerate(texts)]
        record = SimpleNamespace(task_id="isolated", task_dir=self.root, mode=mode, mode_contract=True,
                                 script="测试标题\n" + "\n".join(s.text for s in sentences), sentences=sentences, uploads=uploads,
                                 upload_ids=[s["id"] for s in snapshots], speakers=[], quality_gate_mode="warn",
                                 preferences=mp.EditingPreferences(), revision=0)
        mp.write_json_atomic(self.root / "pretranscripts.json", snapshots)
        return record

    def providers(self, stack, voice=None, all_original=False):
        stack.enter_context(patch("backend.pipeline.apply_sync_sound_matches", side_effect=AssertionError("autoSync forbidden")))
        stack.enter_context(patch("backend.pipeline._validate_pipeline_configuration", side_effect=AssertionError("legacy preflight forbidden")))
        stack.enter_context(patch("backend.providers.llm.LLMProvider.for_script_segmentation", side_effect=AssertionError("LLM segmentation forbidden")))
        stack.enter_context(patch("backend.mode_pipeline.create_asr_provider", side_effect=AssertionError("ready ASR must be reused")))
        if all_original:
            stack.enter_context(patch("backend.mode_pipeline.create_tts_provider", side_effect=AssertionError("original MUST NOT CONSTRUCT TTS")))
            stack.enter_context(patch("backend.providers.vision.VisionProvider.from_settings", side_effect=AssertionError("no broll Vision forbidden")))
            stack.enter_context(patch("backend.providers.embedding.EmbeddingProvider.from_settings", side_effect=AssertionError("original embedding forbidden")))
            stack.enter_context(patch("backend.providers.llm.LLMProvider.from_settings", side_effect=AssertionError("original LLM forbidden")))
        else:
            stack.enter_context(patch("backend.mode_pipeline.create_tts_provider", return_value=voice or FakeVoice()))
            stack.enter_context(patch("backend.providers.vision.VisionProvider.from_settings", return_value=FakeVision()))
            stack.enter_context(patch("backend.providers.embedding.EmbeddingProvider.from_settings", return_value=FakeContext()))
            stack.enter_context(patch("backend.providers.llm.LLMProvider.from_settings", return_value=FakeContext()))
            stack.enter_context(patch("backend.mode_pipeline.build_match_plan", side_effect=fake_match))

    async def test_original_all_ten_real_stages_no_broll_no_provider_construction_and_cached_revision(self):
        from backend.pipeline import run_pipeline
        record = self.record()
        snapshot_hash = digest(self.root / "pretranscripts.json")
        reporter = Reporter()
        with ExitStack() as stack:
            self.providers(stack, all_original=True)
            await run_pipeline(record, reporter, self.settings)
            self.assertEqual(reporter.started, list(range(1, 11)))
            self.assertEqual(reporter.completed, list(range(1, 11)))
            self.assertTrue(all((self.root / name).is_file() for name in mp.REQUIRED_ARTIFACTS))
            self.assertEqual(digest(self.root / "pretranscripts.json"), snapshot_hash)
            report = mp.read_json(self.root, "report.json")
            manifest = mp.read_json(self.root, mp.MODE_MANIFEST)
            self.assertEqual(manifest["jumpcuts"][0]["cover"], "zoom")
            self.assertTrue(manifest["jumpcuts"][0]["downgraded"])
            self.assertEqual(manifest["video_rendering"]["frames"][1]["jump_zoom_frames"], 6)
            self.assertEqual(report["rows"][0]["spoken_text"], "今天活动开幕。")
            self.assertFalse(any(i["code"] == "NARRATION_SPEAKING_RATE_OUT_OF_RANGE" for i in report["checks"]))
            previews = mp.rules.align_quotes(record.sentences, [speech_snapshot()])
            self.assertEqual(report["rows"][0]["source"], previews[0]["source"])
            self.assertEqual(len({r["shot_id"] for r in report["rows"]}), 2)
            original_hash = digest(self.root / "final.mp4")
            audio_hash = digest(self.root / "narration.m4a")
            with tempfile.TemporaryDirectory(prefix="mode-revision-") as directory:
                target = Path(directory)
                shutil.copytree(self.root, target, dirs_exist_ok=True)
                work = SimpleNamespace(**{**record.__dict__, "task_dir": target, "revision": 1})
                payload = {"keep_sentence_ids": [0, 1], "edits": [], "speakers": [mp.Speaker(id="u:1", name="测试姓名", title="测试身份")]}
                await mp.edit_mode_workspace(work, record, payload, self.settings)
                revision = mp.read_json(target, mp.MODE_MANIFEST)
                self.assertEqual(revision["video_rendering"]["rendered_clips"], 0)
                self.assertEqual(revision["video_rendering"]["reused_clips"], 2)
                self.assertEqual(digest(target / "narration.m4a"), audio_hash)
                self.assertNotEqual(digest(target / "final.mp4"), original_hash)
                self.assertEqual(digest(self.root / "final.mp4"), original_hash)
                self.assertTrue(all((target / name).is_file() for name in mp.REQUIRED_ARTIFACTS))

    async def test_voiceover_does_not_autosync_even_with_matching_speech(self):
        record = self.record("voiceover", texts=[("今天活动开幕。", "narration")])
        voice = FakeVoice()
        with ExitStack() as stack:
            self.providers(stack, voice=voice)
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
        plan = mp.read_json(self.root, "match_plan.json")
        timings = mp.read_json(self.root, "timings.json")
        self.assertEqual(voice.calls, ["今天活动开幕。"])
        self.assertIsNone(plan[0]["sync_sound"])
        self.assertIsNone(plan[0]["source"])
        self.assertEqual(timings[0]["audio_kind"], "tts")

    async def test_mixed_only_narration_tts_and_actual_quarter_second_gaps(self):
        record = self.record("mixed", with_broll=True, texts=[("现场活动开始了。", "narration"), ("今天活动开幕。", "quote")])
        voice = FakeVoice()
        with ExitStack() as stack:
            self.providers(stack, voice=voice)
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
        self.assertEqual(voice.calls, ["现场活动开始了。"])
        timings = mp.read_json(self.root, "timings.json")
        self.assertAlmostEqual(timings[1]["start"] - timings[0]["end"], 0.25)
        self.assertEqual(timings[1]["audio_kind"], "sync")
        edl = mp.read_json(self.root, "edl.json")
        self.assertIn("norm_1", edl[0]["clips"][0]["src"])
        self.assertIsNone(edl[1]["clips"][0]["freeze_pad"])

    async def test_ready_and_failed_asr_reuse_exactly_once_no_fallback(self):
        record = self.record()
        snapshots = mp.read_json(self.root, "pretranscripts.json")
        original = copy.deepcopy(snapshots)
        with patch("backend.mode_pipeline.create_asr_provider", side_effect=AssertionError("not allowed")):
            results = await mp._retry_failed_asr(record, snapshots, self.settings, lambda *_: None)
        self.assertEqual(snapshots, original)
        self.assertEqual(results[0].transcript.model_dump(mode="json"), original[0]["asr_result"])
        snapshots[0].update(status="asr_failed", asr_result=None, transcript=[])
        provider = FakeContext()
        provider.max_retries = 2
        provider.validate_configuration = lambda: None
        provider.transcribe = AsyncMock(side_effect=RuntimeError("SECRET_MUST_NOT_PERSIST"))
        with patch("backend.mode_pipeline.create_asr_provider", return_value=provider) as factory:
            await mp._retry_failed_asr(record, snapshots, self.settings, lambda *_: None)
            await mp._retry_failed_asr(record, snapshots, self.settings, lambda *_: None)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(provider.transcribe.await_count, 1)
        self.assertEqual(provider.max_retries, 0)
        self.assertNotIn("SECRET_MUST_NOT_PERSIST", (self.root / "pretranscripts.json").read_text(encoding="utf-8"))

    async def test_missing_quote_stops_stage_six_without_any_tts(self):
        record = self.record(texts=[("完全不存在的其他陌生主题", "quote")])
        reporter = Reporter()
        with ExitStack() as stack:
            self.providers(stack, all_original=True)
            with self.assertRaises(mp.QuoteMissingError) as caught:
                await mp.run_mode_pipeline(record, reporter, self.settings)
        self.assertEqual(caught.exception.bad_rows, [1])
        self.assertEqual(reporter.started[-1], 6)
        self.assertEqual(reporter.completed[-1], 5)
        self.assertFalse((self.root / "narration.m4a").exists())

    async def test_pcm_extraction_never_mp3_or_atempo_and_preserves_samples(self):
        record = self.record()
        output = self.root / "tts" / "quote.wav"
        receipt = await mp.extract_mode_pcm(self.root, record.uploads[0].path, output, source_start=0.38, source_end=2.3)
        with wave.open(str(output), "rb") as pcm:
            self.assertEqual((pcm.getframerate(), pcm.getnchannels(), pcm.getsampwidth()), (48000, 2, 2))
            self.assertEqual(pcm.getnframes(), round(1.92 * 48000))
            self.assertEqual(len(pcm.readframes(pcm.getnframes())), pcm.getnframes() * 4)
        self.assertEqual(receipt["samples"], round(1.92 * 48000))
        with self.assertRaises(mp.TTSProcessingError):
            await mp.extract_mode_pcm(self.root, record.uploads[0].path, self.root / "bad.wav", source_start=9, source_end=11)

    async def test_decoded_continuous_source_clock_has_no_duplicate_or_missing_frame(self):
        import numpy as np
        from backend import rendering
        source = self.root / "clock.mp4"
        invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                "nullsrc=s=256x144:r=30:d=5,geq=lum='32+192*mod(floor(N/pow(2,floor(X/32))),2)':cb=128:cr=128",
                "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", str(source)])
        edl = [mp.EDLItem(sentence_id=i, timeline_start=start - 0.38, timeline_end=end - 0.38,
                          clips=[mp.EDLClip(shot_id=i, src="clock.mp4", **{"in": start, "out": end})])
               for i, (start, end) in enumerate([(0.38, 2.25), (2.25, 4.46)])]
        with patch.object(rendering, "_mix_subtitles_and_narration", new=AsyncMock()), patch.object(rendering, "validate_final_video", new=AsyncMock()):
            result = await rendering.render_mode_video(self.root, edl, lambda *_: None, clip_options={},
                                                       source_hashes={"clock.mp4": digest(source)}, finish_options=mp.FinishOptions())
        decoded = invoke(["ffmpeg", "-v", "error", "-i", str(self.root / "video_only.mp4"),
                          "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"])
        pixels = np.frombuffer(decoded, dtype=np.uint8).reshape(-1, 144, 256)
        values = [sum(int(frame[72, bit * 32 + 16] > 128) << bit for bit in range(8)) for frame in pixels]
        # Every source clock must be exactly the next source frame, including
        # the old 67->67 boundary. No approximate image/duplicate tolerance.
        self.assertEqual(values, list(range(11, 11 + 123)))
        self.assertEqual(result["frames"][1]["actual_source_in"], 68 / 30)
        probe = json.loads(invoke(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
                                   "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(self.root / "video_only.mp4")]))
        for index, frame in enumerate(probe["frames"]):
            self.assertAlmostEqual(float(frame["best_effort_timestamp_time"]), index / 30, places=6)

    async def test_recording_one_utterance_two_sentences_uneven_words_number_parity(self):
        source = self.root / "own_voice.wav"
        invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000:duration=5",
                "-c:a", "pcm_s16le", str(source)])
        utterance = {"text": "今天有三个人。欢迎大家参与。", "start_time_ms": 200, "end_time_ms": 4500,
                     "words": [{"text": text, "start_time_ms": start, "end_time_ms": end} for text, start, end in
                               [("今天", 200, 450), ("有", 470, 600), ("三个", 650, 1610), ("人", 1640, 1810),
                                ("欢迎", 2300, 2550), ("大家", 2600, 3680), ("参与", 3740, 4500)]]}
        transcript = mp.ASRTranscript.model_validate({"text": utterance["text"], "duration_ms": 5000, "utterances": [utterance]})
        provider = FakeContext()
        provider.validate_configuration = lambda: None
        provider.transcribe = AsyncMock(return_value=transcript)
        sentences = [mp.Sentence(sentence_id=i, text=text) for i, text in enumerate(["今天有3个人。", "欢迎大家参与。"]) ]
        with patch("backend.mode_pipeline.create_asr_provider", return_value=provider):
            timings, receipt = await mp._recorded_narration(self.root, source, sentences, self.settings)
        self.assertEqual([mp.rules.normalize_text(t.text) for t in timings], [mp.rules.normalize_text(s.text) for s in sentences])
        self.assertEqual([len(t.words) for t in timings], [4, 3])
        for i, timing in enumerate(timings):
            start = receipt["raw_recipes"][str(i)]["source_start"]
            original_words = utterance["words"][:4] if i == 0 else utterance["words"][4:]
            for actual, expected in zip(timing.words, original_words, strict=True):
                self.assertAlmostEqual(actual.start + start, expected["start_time_ms"] / 1000)
                self.assertAlmostEqual(actual.end + start, expected["end_time_ms"] / 1000)

    async def test_continuous_group_dsp_matches_unsplit_reference_sample_for_sample(self):
        import numpy as np
        snapshot = speech_snapshot()
        inputs = [mp.SentenceInput(idx=i, text=text, kind="quote") for i, text in enumerate(["今天活动开幕。", "欢迎大家参与。"]) ]
        rows = mp.rules.align_quotes(inputs, [snapshot])
        plan = [mp.MatchPlanItem(sentence_id=i, text=inputs[i].text, kind="quote", source=row["source"],
                                 shot_id=i, confidence=1, candidates=[]) for i, row in enumerate(rows)]
        raw = self.root / "raw" / "tone.wav"
        # Continuous low-frequency component exposes reset high-pass/denoiser
        # state; two levels expose incorrect inheritance of whole-group LUFS.
        invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                "aevalsrc=(0.06+0.01*gte(t\\,2.25))*sin(2*PI*440*t)+0.02*sin(2*PI*83*t):s=48000:d=5",
                "-ac", "2", "-c:a", "pcm_s16le", str(raw)])
        clock = {"prepared_start": 0, "prepared_end": 10, "audio_offset_seconds": 0,
                 "raw_path": "raw/tone.wav", "raw_sha256": digest(raw)}
        layout, groups = mp.quote_layout(plan, [snapshot], {"u": clock})
        record = SimpleNamespace(task_dir=self.root, preferences=mp.EditingPreferences(enhance_speech=True))
        selected, _ = await mp._prepare_quote_audio(record, plan, layout, groups, {"source_clocks": {"u": clock}}, lambda *_: None)
        merged = self.root / "reference-raw.wav"
        await mp.extract_mode_pcm(self.root, raw, merged, source_start=layout[0]["start"], source_end=layout[1]["end"])
        duration = mp._verified_pcm_samples(merged) / 48000
        timing = mp.SentenceTiming(sentence_id=0, text="参考", audio_kind="sync", audio_path=merged.name, duration=duration, start=0, end=duration, gap_after=0)
        reference = self.root / "reference-normalized.wav"
        await mp.normalize_mode_unit(self.root, timing, reference, enhance_speech=True)
        def pcm(path):
            with wave.open(str(path), "rb") as stream:
                return stream.readframes(stream.getnframes())
        joined = b"".join(pcm(self.root / selected[i].audio_path) for i in range(2))
        self.assertEqual(len(joined), len(pcm(reference)))
        self.assertTrue(np.array_equal(np.frombuffer(joined, dtype="<i2"), np.frombuffer(pcm(reference), dtype="<i2")),
                        "split DSP must equal merged DSP at EVERY PCM sample, including the seam")
        from backend.tts_pipeline import _parse_loudnorm_measurement
        levels = []
        for timing in selected.values():
            measured = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(self.root / timing.audio_path),
                                       "-af", "loudnorm=I=-20:LRA=5:TP=-3.5:print_format=json", "-f", "null", os.devnull], capture_output=True, check=True)
            metrics = _parse_loudnorm_measurement(measured.stderr.decode())
            self.assertEqual(timing.integrated_lufs, metrics["input_i"])
            self.assertEqual(timing.true_peak_dbfs, metrics["input_tp"])
            levels.append(timing.integrated_lufs)
        self.assertNotEqual(*levels)

    async def test_prepared_offset_maps_norm_picture_and_original_audio(self):
        record = self.record(texts=[("今天天气很好。", "quote")])
        prepared = self.root / "raw" / ("c" * 32 + ".mp4")
        invoke(["ffmpeg", "-v", "error", "-y", "-i", str(record.uploads[0].path), "-ss", "5", "-t", "4",
                "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "ultrafast", str(prepared)])
        record.uploads[0].trim_start, record.uploads[0].trim_end = 5.0, 9.0
        record.uploads[0].prepared_stored_name = prepared.name
        with ExitStack() as stack:
            self.providers(stack, all_original=True)
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
        manifest = mp.read_json(self.root, mp.MODE_MANIFEST)
        source = manifest["quote_source"]["0"]
        self.assertAlmostEqual(source["selected_source_start"], 6.08)
        self.assertAlmostEqual(source["norm_start"], 1.08)
        self.assertEqual(source["norm_source_offset"], 5.0)
        self.assertTrue(source["source_media_path"].endswith("a" * 32 + ".mp4"))
        timing = mp.read_json(self.root, "timings.json")[0]
        self.assertAlmostEqual(timing["words"][0]["start"], 0.12)
        self.assertAlmostEqual(timing["words"][1]["start"], 6.81 - 6.08)

    async def test_continuous_source_pcm_no_duplicate_padding_and_neighbour_only_rebuild(self):
        record = self.record(texts=[("今天活动开幕。", "quote"), ("欢迎大家参与。", "quote"), ("今天天气很好。", "quote")])
        with ExitStack() as stack:
            self.providers(stack, all_original=True)
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
            before = mp.read_json(self.root, mp.MODE_MANIFEST)
            timing = mp.read_json(self.root, "timings.json")
            self.assertEqual(timing[0]["gap_after"], 0)
            self.assertEqual(timing[1]["gap_after"], 0.15)
            self.assertEqual(timing[0]["end"], timing[1]["start"])
            self.assertEqual(before["quote_audio_extractions"][0]["sentence_ids"], [0, 1])
            self.assertEqual(before["quote_source"]["0"]["actual_range"], {"start": 0.38, "end": 2.25, "clock": "original_source_seconds"})
            public = mp.mode_report_metadata(self.root)["metrics"]["quote_actual_ranges"]
            self.assertEqual(public["0"]["actual_range"], before["quote_source"]["0"]["actual_range"])
            self.assertAlmostEqual(public["0"]["cut_tail_seconds"], 0.05)
            self.assertNotIn("raw_path", json.dumps(public))
            self.assertEqual(mp.read_json(self.root, "report.json")["metrics"]["quote_actual_ranges"], public)
            with tempfile.TemporaryDirectory(prefix="mode-explicit-trim-") as directory:
                target = Path(directory)
                shutil.copytree(self.root, target, dirs_exist_ok=True)
                work = SimpleNamespace(**{**record.__dict__, "task_dir": target, "revision": 1})
                await mp.edit_mode_workspace(work, record, {"keep_sentence_ids": [0, 1, 2], "edits": [],
                    "quote_trims": [{"id": 0, "start": 0.5, "end": 2.2}, {"id": 1, "start": 2.3, "end": 4.3}]}, self.settings)
                edited = mp.read_json(target, mp.MODE_MANIFEST)
                edited_timing = mp.read_json(target, "timings.json")
                self.assertEqual(edited["quote_source"]["0"]["actual_range"]["end"], 2.2)
                self.assertEqual(edited["quote_source"]["1"]["actual_range"]["start"], 2.3)
                self.assertEqual(edited_timing[0]["duration"], 1.7)
                self.assertEqual(edited_timing[0]["gap_after"], mp.rules.LIMITS["quote_gap_seconds"])
                self.assertEqual(mp.read_json(target, "report.json")["rows"][0]["source"]["end"], 2.2)
            with tempfile.TemporaryDirectory(prefix="mode-neighbour-") as directory:
                target = Path(directory)
                shutil.copytree(self.root, target, dirs_exist_ok=True)
                work = SimpleNamespace(**{**record.__dict__, "task_dir": target, "revision": 1, "reporter": Reporter()})
                await mp.edit_mode_workspace(work, record, {"keep_sentence_ids": [1, 2], "edits": []}, self.settings)
                after = mp.read_json(target, mp.MODE_MANIFEST)
                self.assertEqual(work.reporter.started, [7, 8, 9, 10])
                self.assertEqual(work.reporter.completed, [7, 8, 9, 10])
                self.assertNotEqual(after["audio_cache"]["1"]["key"], before["audio_cache"]["1"]["key"])
                self.assertEqual(after["audio_cache"]["2"]["sha256"], before["audio_cache"]["2"]["sha256"])
                self.assertEqual(after["video_rendering"]["reused_clips"], 1)

    async def test_broll_and_zoom_change_real_pixels_not_audio(self):
        record = self.record(with_broll=True)
        with ExitStack() as stack:
            self.providers(stack)
            stack.enter_context(patch("backend.mode_pipeline.create_tts_provider", side_effect=AssertionError("quote TTS forbidden")))
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
        broll_manifest = mp.read_json(self.root, mp.MODE_MANIFEST)
        self.assertEqual(broll_manifest["jumpcuts"][0]["cover"], "broll")
        cover = broll_manifest["jumpcut_rendering"][0]
        self.assertGreaterEqual(cover["duration"], 1.5)
        self.assertLessEqual(cover["duration"], 3.0)
        broll_frame = video_frame(self.root / "video_only.mp4", cover["timeline_start"] + 0.4)
        broll_audio = audio_bytes(self.root / "final.mp4")
        snapshots = mp.read_json(self.root, "pretranscripts.json")
        plan = [mp.MatchPlanItem.model_validate(value) for value in mp.read_json(self.root, "match_plan.json")]
        timings = [mp.SentenceTiming.model_validate(value) for value in mp.read_json(self.root, "timings.json")]
        shots = [mp.AnnotatedShot.model_validate(value) for value in mp.read_json(self.root, "shots_annotated.json")]
        record.preferences.jump_cut_cover = "zoom"
        await mp._render_mode(record, plan, timings, shots, snapshots, broll_manifest, self.settings, lambda *_: None)
        zoom_frame = video_frame(self.root / "video_only.mp4", cover["timeline_start"] + 0.4)
        self.assertNotEqual(broll_frame, zoom_frame)
        self.assertEqual(audio_bytes(self.root / "final.mp4"), broll_audio)
        record.preferences.jump_cut_cover = "hard"
        await mp._render_mode(record, plan, timings, shots, snapshots, broll_manifest, self.settings, lambda *_: None)
        hard_frame = video_frame(self.root / "video_only.mp4", cover["timeline_start"] + 0.4)
        self.assertNotEqual(zoom_frame, hard_frame)
        self.assertEqual(audio_bytes(self.root / "final.mp4"), broll_audio)

    async def test_mixed_conversion_preserves_source_and_synthesizes_only_new_narration(self):
        record = self.record("mixed", with_broll=True, texts=[("今天活动开幕。", "quote")])
        with ExitStack() as stack:
            self.providers(stack)
            stack.enter_context(patch("backend.mode_pipeline.create_tts_provider", side_effect=AssertionError("all quotes cannot construct TTS")))
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
        before = mp.read_json(self.root, "match_plan.json")[0]
        with tempfile.TemporaryDirectory(prefix="mode-conversion-") as directory:
            target = Path(directory)
            shutil.copytree(self.root, target, dirs_exist_ok=True)
            work = SimpleNamespace(**{**record.__dict__, "task_dir": target, "revision": 1})
            voice = FakeVoice()
            with ExitStack() as stack:
                self.providers(stack, voice=voice)
                await mp.edit_mode_workspace(work, record, {"keep_sentence_ids": [0], "edits": [], "to_narration": [0]}, self.settings)
            after = mp.read_json(target, "match_plan.json")[0]
            self.assertEqual(voice.calls, ["今天活动开幕。"])
            self.assertEqual(after["kind"], "narration")
            self.assertTrue(after["to_narration"])
            self.assertEqual(after["source"], before["source"])
            self.assertEqual(mp.read_json(target, "timings.json")[0]["audio_kind"], "tts")

    async def test_trim_reuses_other_quote_and_contiguous_take_rebuilds_neighbour_without_tts(self):
        record = self.record()
        snapshot = mp.read_json(self.root, "pretranscripts.json")[0]
        # Add a separately occurring version of the first utterance. Exact
        # duplicate query yields a real alternative, not a user-supplied range.
        repeat = copy.deepcopy(snapshot["asr_result"]["utterances"][0])
        repeat["start_time_ms"], repeat["end_time_ms"] = 4500, 6000
        repeat["words"] = [{"text": "今天", "start_time_ms": 4500, "end_time_ms": 4800},
                           {"text": "活动", "start_time_ms": 4900, "end_time_ms": 5500},
                           {"text": "开幕", "start_time_ms": 5600, "end_time_ms": 6000}]
        result = mp.ASRTranscript.model_validate({**snapshot["asr_result"], "utterances": [*snapshot["asr_result"]["utterances"], repeat]})
        snapshot["asr_result"] = result.model_dump(mode="json")
        snapshot["transcript"] = mp._provider_segments(result, "u", 10, 0)
        mp.write_json_atomic(self.root / "pretranscripts.json", [snapshot])
        with ExitStack() as stack:
            self.providers(stack, all_original=True)
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
            before = mp.read_json(self.root, mp.MODE_MANIFEST)
            first = mp.read_json(self.root, "match_plan.json")[0]
            self.assertTrue(first["alt_takes"])
            for operation in ({"quote_trims": [{"id": 0, "start": 0.8, "end": 2.3}]},
                              {"quote_takes": [{"id": 0, "take_id": first["alt_takes"][0]["take_id"]}]}):
                with tempfile.TemporaryDirectory(prefix="mode-take-trim-") as directory:
                    target = Path(directory)
                    shutil.copytree(self.root, target, dirs_exist_ok=True)
                    work = SimpleNamespace(**{**record.__dict__, "task_dir": target, "revision": 1})
                    await mp.edit_mode_workspace(work, record, {"keep_sentence_ids": [0, 1], "edits": [], **operation}, self.settings)
                    after = mp.read_json(target, mp.MODE_MANIFEST)
                    if "quote_trims" in operation:
                        self.assertEqual(after["audio_cache"]["1"]["sha256"], before["audio_cache"]["1"]["sha256"])
                    else:
                        self.assertNotEqual(after["audio_cache"]["1"]["key"], before["audio_cache"]["1"]["key"])
                        self.assertEqual(after["quote_source"]["0"]["end_sample"], after["quote_source"]["1"]["start_sample"])
                        self.assertEqual(mp.read_json(target, "timings.json")[0]["gap_after"], 0.0)
                    self.assertNotEqual(after["audio_cache"]["0"]["key"], before["audio_cache"]["0"]["key"])

    async def test_real_music_mix_keeps_measurable_audio_and_reports_it(self):
        record = self.record(texts=[("今天活动开幕。", "quote")])
        record.preferences.background_music = True
        record.preferences.music_mood = "neutral"
        with ExitStack() as stack:
            self.providers(stack, all_original=True)
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
        report = mp.read_json(self.root, "quality_report.json")
        self.assertLessEqual(report["metrics"]["true_peak_dbfs"], -3.0)
        self.assertLessEqual(abs(report["metrics"]["integrated_lufs"] + 20.0), 2.0)
        self.assertTrue((self.root / "music_selection.json").is_file())
        self.assertTrue((self.root / "music_bed.m4a").is_file())

    async def test_own_voice_never_tts_and_recovers_raw_recipe_for_enhancement(self):
        from backend.revisions import artifact_files, copy_files
        record = self.record("voiceover", texts=[("今天活动开幕。", "narration")])
        record.preferences.voice = "mine"
        own_voice = self.root / "own_voice.wav"
        invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=550:sample_rate=48000:duration=3",
                "-c:a", "pcm_s16le", str(own_voice)])
        transcript = mp.ASRTranscript.model_validate(speech_snapshot()["asr_result"])
        transcript.utterances = transcript.utterances[:1]
        transcript.text = transcript.utterances[0].text
        transcript.duration_ms = 3000
        provider = FakeContext()
        provider.validate_configuration = lambda: None
        provider.transcribe = AsyncMock(return_value=transcript)
        with ExitStack() as stack:
            self.providers(stack)
            stack.enter_context(patch("backend.mode_pipeline.create_tts_provider", side_effect=AssertionError("own voice must not construct TTS")))
            stack.enter_context(patch("backend.mode_pipeline.create_asr_provider", return_value=provider))
            await mp.run_mode_pipeline(record, Reporter(), self.settings)
        self.assertEqual(provider.transcribe.await_count, 1)
        with tempfile.TemporaryDirectory(prefix="mode-own-voice-revision-") as directory:
            target = Path(directory)
            # Reproduce actual revision copies, not a permissive copytree: raw
            # derivatives are omitted and must be reconstructed from the recipe.
            copy_files(self.root, target, artifact_files(self.root))
            work = SimpleNamespace(**{**record.__dict__, "task_dir": target, "revision": 1})
            with ExitStack() as stack:
                self.providers(stack)
                stack.enter_context(patch("backend.mode_pipeline.create_tts_provider", side_effect=AssertionError("TTS forbidden")))
                await mp.edit_mode_workspace(work, record, {"keep_sentence_ids": [0], "edits": [], "enhance_speech": True}, self.settings)
            result = mp.read_json(target, "timings.json")[0]
            self.assertEqual(result["audio_kind"], "sync")
            self.assertEqual(result["tempo_adjustment"], 1.0)
            self.assertEqual(result["words"], mp.read_json(self.root, "timings.json")[0]["words"])
            self.assertTrue(mp.read_json(target, "narration_profile.json")["speech_enhancement"]["applied"])


def isolated_main():
    # Keep only OS execution context; never inherit provider credentials.
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE", "PROGRAMFILES",
            "PROGRAMFILES(X86)", "PROGRAMDATA", "LOCALAPPDATA", "APPDATA", "USERPROFILE", "NUMBER_OF_PROCESSORS"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
    local = next((value for key, value in environment.items() if key.upper() == "LOCALAPPDATA"), "")
    candidates = [file for package in (Path(local) / "Microsoft/WinGet/Packages").glob("Gyan.FFmpeg*") for file in package.rglob("ffmpeg.exe") if (file.parent / "ffprobe.exe").is_file()]
    if not candidates:
        raise RuntimeError("Real FFmpeg is required; tests will not silently skip media")
    ffmpeg = max(candidates, key=lambda file: file.stat().st_mtime_ns)
    environment = {key.upper(): value for key, value in environment.items()}
    environment["PATH"] = str(ffmpeg.parent) + os.pathsep + environment.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="golden-mic-mode-isolated-") as directory, ExitStack() as stack:
        safe_root = Path(directory)
        environment.update(DATA_DIR=str(safe_root / "data"), ASR_CACHE_DIR=str(safe_root / "asr"),
                           TEMP=directory, TMP=directory, TMPDIR=directory, APP_ENV="test",
                           PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1")
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        old_temp = tempfile.tempdir
        tempfile.tempdir = directory
        stack.callback(setattr, tempfile, "tempdir", old_temp)
        stack.enter_context(patch("platform._syscmd_ver", return_value=("", "", "")))
        from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource
        stack.enter_context(patch.object(DotEnvSettingsSource, "_read_env_files", return_value={}))
        forbidden = [ROOT / name for name in (".env", "data", "eval_sample", "canary_test/artifacts")]
        native_popen = subprocess.Popen.__init__
        state = threading.local()
        native_pair = socket.socketpair

        def socketpair(*args, **kwargs):
            state.pair = True
            try:
                return native_pair(*args, **kwargs)
            finally:
                state.pair = False

        def audit(event, args):
            if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(args[0])).absolute()
                if path == Path(os.devnull).absolute():
                    return
                if any(path == p or path.is_relative_to(p) for p in forbidden):
                    raise RuntimeError("Private workspace access forbidden in mode tests")
                flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and not path.is_relative_to(safe_root):
                    raise RuntimeError("Writes outside isolated TEMP forbidden")
            if event == "socket.connect" and not getattr(state, "pair", False):
                raise RuntimeError("Network forbidden in mode tests")
            if event in {"socket.getaddrinfo", "socket.gethostbyname", "os.system", "os.startfile"}:
                raise RuntimeError("Network/system launch forbidden in mode tests")

        def popen(process, command, *args, **kwargs):
            if not isinstance(command, (list, tuple)) or Path(str(command[0])).name.lower() not in {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"} or kwargs.get("shell"):
                raise RuntimeError("Only local FFmpeg subprocesses allowed")
            if any(str(value).startswith(("http:", "https:", "tcp:", "udp:", "rtsp:")) for value in command):
                raise RuntimeError("Native media networking forbidden")
            return native_popen(process, command, *args, **kwargs)

        sys.addaudithook(audit)
        stack.enter_context(patch.object(socket, "socketpair", socketpair))
        stack.enter_context(patch.object(subprocess.Popen, "__init__", popen))
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        load_product()
        # Explicit module/class selections are accepted; discovery is not.
        names = sys.argv[1:]
        if names == ["--legacy-audio-quality"]:
            # Explicit, bounded compatibility checks. Never import tests that
            # construct main/app globals or run a discovery/full-suite runner.
            suite = unittest.defaultTestLoader.loadTestsFromNames([
                "tests.test_m4_tts", "tests.test_quality", "tests.test_production_modes",
            ])
        else:
            suite = unittest.defaultTestLoader.loadTestsFromNames(names, module=sys.modules[__name__]) if names else unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print(f"Mode isolated result: tests={result.testsRun} failures={len(result.failures)} errors={len(result.errors)}")
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(isolated_main())