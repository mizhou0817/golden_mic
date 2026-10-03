import asyncio
import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.models import (
    AnnotatedShot,
    BeatMatch,
    EDLClip,
    EDLItem,
    MatchCandidate,
    MatchPlanItem,
    ScriptDocument,
    Sentence,
    SentenceTiming,
    VisionQuality,
    VisualBeat,
)
from backend.quality import enforce_quality_gate, generate_quality_report
from backend.pipeline import _run_blocking_until_complete  # pyright: ignore[reportPrivateUsage]


class QualityGateTest(unittest.TestCase):
    def test_media_measurement_fails_closed_when_ffmpeg_scan_fails(self) -> None:
        from subprocess import CompletedProcess

        from backend.quality import _media_quality_metrics

        with tempfile.TemporaryDirectory() as directory:
            media_path = Path(directory) / "final.mp4"
            media_path.write_bytes(b"video")
            with patch("backend.quality.shutil.which", side_effect=lambda name: name), patch(
                "backend.quality.subprocess.run",
                side_effect=[
                    CompletedProcess([], 0, stdout="2.0\n", stderr=""),
                    CompletedProcess([], 1, stdout="", stderr="decoder failed"),
                ],
            ):
                metrics = _media_quality_metrics(media_path)

        self.assertEqual(metrics, {})


    def test_blocks_generated_media_without_matching_disclosure_manifest(self) -> None:
        sentence = Sentence(sentence_id=0, text="公共服务持续改善。")
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
            quality=VisionQuality(sharp=0.8, bright=0.8),
        )
        plan = MatchPlanItem(
            sentence_id=0,
            text=sentence.text,
            shot_id=7,
            confidence=0.0,
            is_fallback=True,
            candidates=[MatchCandidate(shot_id=7, similarity=0.0)],
        )
        timing = SentenceTiming(
            sentence_id=0,
            text=sentence.text,
            audio_path="tts/sent_0.mp3",
            duration=2.0,
            start=0.0,
            end=2.0,
            gap_after=0.0,
        )
        edl = [
            EDLItem(
                sentence_id=0,
                clips=[
                    EDLClip(
                        shot_id=7,
                        src="generated/fill_shot_7.mp4",
                        in_time=0.0,
                        out_time=2.0,
                        media_origin="generated",
                    )
                ],
                timeline_start=0.0,
                timeline_end=2.0,
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "script_structure.json").write_text(
                json.dumps(ScriptDocument(sentences=[sentence]).model_dump(mode="json")),
                encoding="utf-8",
            )
            (task_dir / "edl.json").write_text(
                json.dumps([item.model_dump(mode="json", by_alias=True) for item in edl]),
                encoding="utf-8",
            )
            (task_dir / "generated_media_disclosure.json").write_text(
                json.dumps({"schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence", "items": [{"shot_id": 7}]}),
                encoding="utf-8",
            )
            with patch(
                "backend.quality._media_quality_metrics",
                return_value={"duration_seconds": 2.0},
            ), patch("backend.quality._sentence_loudness_metrics", return_value=[]):
                report = generate_quality_report(
                    task_dir,
                    [shot],
                    [plan],
                    [timing],
                    minimum_confidence=0.5,
                )

        self.assertIn(
            "GENERATED_MEDIA_DISCLOSURE_MISSING",
            {issue["code"] for issue in report["issues"]},
        )
        self.assertFalse(report["metrics"]["generated_media_disclosure_complete"])


    def test_blocks_excessive_freeze_and_unmeasurable_media(self) -> None:
        sentence = Sentence(sentence_id=0, text="本市公共服务持续改善。")
        shot = AnnotatedShot(
            shot_id=0,
            source_index=0,
            source_scene_index=0,
            source_name="source.mp4",
            norm_path="norm/norm_0.mp4",
            start=0,
            end=2,
            duration=2,
            status="available",
            description="市民在服务大厅办理业务",
            keywords=["市民", "服务", "大厅"],
            quality=VisionQuality(sharp=0.8, bright=0.8),
        )
        candidate = MatchCandidate(shot_id=0, similarity=0.9)
        plan = MatchPlanItem(
            sentence_id=0,
            text=sentence.text,
            shot_id=0,
            confidence=0.9,
            candidates=[candidate],
        )
        timing = SentenceTiming(
            sentence_id=0,
            text=sentence.text,
            audio_path="tts/sent_0.mp3",
            duration=2.0,
            start=0.0,
            end=2.0,
            gap_after=0.0,
        )
        edl = [
            EDLItem(
                sentence_id=0,
                clips=[
                    EDLClip(
                        shot_id=0,
                        src="norm/norm_0.mp4",
                        in_time=0.0,
                        out_time=1.0,
                        freeze_pad=1.0,
                    )
                ],
                timeline_start=0.0,
                timeline_end=2.0,
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "script_structure.json").write_text(
                json.dumps(ScriptDocument(sentences=[sentence]).model_dump(mode="json"), ensure_ascii=False),
                encoding="utf-8",
            )
            (task_dir / "edl.json").write_text(
                json.dumps([item.model_dump(mode="json", by_alias=True) for item in edl]),
                encoding="utf-8",
            )
            with patch("backend.quality._media_quality_metrics", return_value={}), patch(
                "backend.quality._sentence_loudness_metrics",
                return_value=[],
            ):
                report = generate_quality_report(
                    task_dir,
                    [shot],
                    [plan],
                    [timing],
                    minimum_confidence=0.5,
                )

        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("FREEZE_PAD_EXCESSIVE", codes)
        self.assertIn("MEDIA_QUALITY_UNMEASURABLE", codes)
        self.assertEqual(report["metrics"]["maximum_freeze_pad_seconds"], 1.0)
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            enforce_quality_gate(report, "block")

    def test_prefers_persisted_effective_speaking_rate(self) -> None:
        sentence = Sentence(sentence_id=0, text="第一段专业新闻播报。")
        shot = AnnotatedShot(
            shot_id=0,
            source_index=0,
            source_scene_index=0,
            source_name="source.mp4",
            norm_path="norm/norm_0.mp4",
            start=0,
            end=5,
            duration=5,
            status="available",
            description="新闻播报画面",
            keywords=["新闻", "播报", "现场"],
            quality=VisionQuality(sharp=0.8, bright=0.8),
        )
        plan = MatchPlanItem(
            sentence_id=0,
            text=sentence.text,
            shot_id=0,
            confidence=0.9,
            candidates=[MatchCandidate(shot_id=0, similarity=0.9)],
        )
        timing = SentenceTiming(
            sentence_id=0,
            text=sentence.text,
            audio_path="tts/sent_0.mp3",
            duration=10.0,
            start=0.0,
            end=10.0,
            spoken_unit_count=10,
            speaking_rate_cpm=260.0,
            gap_after=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "script_structure.json").write_text(
                json.dumps(ScriptDocument(sentences=[sentence]).model_dump(mode="json"), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch(
                "backend.quality._media_quality_metrics",
                return_value={"duration_seconds": 10.0},
            ), patch("backend.quality._sentence_loudness_metrics", return_value=[]):
                report = generate_quality_report(
                    task_dir,
                    [shot],
                    [plan],
                    [timing],
                    minimum_confidence=0.5,
                    target_chars_per_minute=260,
                    rate_tolerance=0.08,
                )

        self.assertEqual(
            report["metrics"]["narration_tts_audio_metrics"][0]["speaking_rate_cpm"],
            260.0,
        )
        self.assertNotIn(
            "NARRATION_SPEAKING_RATE_OUT_OF_RANGE",
            {issue["code"] for issue in report["issues"]},
        )

    def test_reports_uncovered_explicit_entity_and_can_block(self) -> None:
        beat = VisualBeat(
            beat_id=0,
            text="灌阳油茶",
            entities=["灌阳油茶", "油茶"],
            requires_entity_coverage=True,
        )
        sentence = Sentence(sentence_id=0, text="灌阳油茶深受欢迎。", visual_beats=[beat])
        shot = AnnotatedShot(
            shot_id=0,
            source_index=0,
            source_scene_index=0,
            source_name="sushi.mp4",
            norm_path="norm/norm_0.mp4",
            start=0,
            end=5,
            duration=5,
            status="available",
            description="户外寿司摊位提供试吃",
            keywords=["寿司", "摊位", "试吃"],
            search_text="寿司摊位 免费试吃",
            quality=VisionQuality(sharp=0.8, bright=0.8),
        )
        candidate = MatchCandidate(shot_id=0, similarity=0.7, combined_score=0.7)
        plan = MatchPlanItem(
            sentence_id=0,
            text=sentence.text,
            shot_id=0,
            confidence=0.34,
            is_fallback=True,
            candidates=[candidate],
            beat_matches=[
                BeatMatch(
                    beat_id=0,
                    text=beat.text,
                    entities=beat.entities,
                    requires_entity_coverage=True,
                    shot_id=0,
                    confidence=0.34,
                    is_fallback=True,
                    candidates=[candidate],
                )
            ],
        )
        timing = SentenceTiming(
            sentence_id=0,
            text=sentence.text,
            audio_path="tts/sent_0.mp3",
            duration=2,
            start=0,
            end=2,
            gap_after=0,
        )

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "script_structure.json").write_text(
                json.dumps(ScriptDocument(title="迎春市集", sentences=[sentence]).model_dump(mode="json"), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch(
                "backend.quality._sentence_loudness_metrics",
                return_value=[
                    {"sentence_id": 0, "integrated_lufs": -24.0},
                    {"sentence_id": 1, "integrated_lufs": -18.0},
                ],
            ):
                report = generate_quality_report(
                    task_dir,
                    [shot],
                    [plan],
                    [timing],
                    minimum_confidence=0.5,
                )

        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("EXPLICIT_ENTITY_NOT_COVERED", codes)
        self.assertIn("MATCH_FALLBACK", codes)
        self.assertIn("NARRATION_LOUDNESS_INCONSISTENT", codes)
        self.assertEqual(report["metrics"]["narration_loudness_spread_lu"], 6.0)
        self.assertFalse(report["metrics"]["title_spoken_as_body"])
        enforce_quality_gate(report, "warn")
        with self.assertRaisesRegex(RuntimeError, "质量门禁"):
            enforce_quality_gate(report, "block")

    def test_reports_overused_and_rapidly_repeated_visual_shot(self) -> None:
        sentences = [
            Sentence(sentence_id=index, text=f"第{index}句新闻内容。")
            for index in range(3)
        ]
        shots = [
            AnnotatedShot(
                shot_id=index,
                source_index=index,
                source_scene_index=0,
                source_name=f"source_{index}.mp4",
                norm_path=f"norm/norm_{index}.mp4",
                start=0,
                end=5,
                duration=5,
                status="available",
                description=f"新闻现场镜头 {index}",
                keywords=["新闻", "现场", "活动"],
                quality=VisionQuality(sharp=0.8, bright=0.8),
            )
            for index in range(3)
        ]
        candidate = MatchCandidate(shot_id=0, similarity=0.8, combined_score=0.8)
        plans = [
            MatchPlanItem(
                sentence_id=sentence.sentence_id,
                text=sentence.text,
                shot_id=0,
                confidence=0.8,
                candidates=[candidate],
                beat_matches=[
                    BeatMatch(
                        beat_id=0,
                        text=sentence.text,
                        shot_id=0,
                        confidence=0.8,
                        candidates=[candidate],
                    )
                ],
            )
            for sentence in sentences
        ]
        timings = [
            SentenceTiming(
                sentence_id=sentence.sentence_id,
                text=sentence.text,
                audio_path=f"tts/sent_{sentence.sentence_id}.mp3",
                duration=1.0,
                start=float(sentence.sentence_id),
                end=float(sentence.sentence_id + 1),
                gap_after=0,
            )
            for sentence in sentences
        ]

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "script_structure.json").write_text(
                json.dumps(
                    ScriptDocument(sentences=sentences).model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch(
                "backend.quality._media_quality_metrics",
                return_value={"duration_seconds": 3.0},
            ):
                report = generate_quality_report(
                    task_dir,
                    shots,
                    plans,
                    timings,
                    minimum_confidence=0.5,
                )

        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("VISUAL_SHOT_OVERUSED", codes)
        self.assertIn("VISUAL_SHOT_REUSED_TOO_SOON", codes)
        self.assertEqual(report["metrics"]["maximum_visual_shot_use_count"], 3)
        self.assertEqual(report["metrics"]["recommended_visual_shot_use_limit"], 1)
        self.assertEqual(report["metrics"]["rapid_visual_reuse_count"], 2)
        duplicate_issue = next(
            issue for issue in report["issues"] if issue["code"] == "VISUAL_SHOT_OVERUSED"
        )
        self.assertEqual(duplicate_issue["severity"], "error")
        self.assertEqual(report["blocking_issue_count"], 1)

    def test_reports_rate_voice_profile_and_session_inconsistency(self) -> None:
        sentences = [
            Sentence(sentence_id=0, text="第一段专业新闻播报。"),
            Sentence(sentence_id=1, text="第二段专业新闻播报。"),
        ]
        shots = [
            AnnotatedShot(
                shot_id=index,
                source_index=index,
                source_scene_index=0,
                source_name=f"source_{index}.mp4",
                norm_path=f"norm/norm_{index}.mp4",
                start=0,
                end=5,
                duration=5,
                status="available",
                description=f"新闻播报画面 {index}",
                keywords=["新闻", "播报", "现场"],
                quality=VisionQuality(sharp=0.8, bright=0.8),
            )
            for index in range(2)
        ]
        plans = [
            MatchPlanItem(
                sentence_id=index,
                text=sentence.text,
                shot_id=index,
                confidence=0.9,
                candidates=[MatchCandidate(shot_id=index, similarity=0.9)],
            )
            for index, sentence in enumerate(sentences)
        ]
        timings = [
            SentenceTiming(
                sentence_id=0,
                text=sentences[0].text,
                audio_path="tts/sent_0.mp3",
                duration=1.5,
                start=0.0,
                end=1.5,
                tts_group_id="session-a",
                voice_profile_id="voice-a",
                spoken_unit_count=10,
                speaking_rate_cpm=400.0,
                tempo_adjustment=0.8,
                integrated_lufs=-20.0,
                true_peak_dbfs=-2.0,
                gap_after=0.0,
            ),
            SentenceTiming(
                sentence_id=1,
                text=sentences[1].text,
                audio_path="tts/sent_1.mp3",
                duration=3.0,
                start=1.5,
                end=4.5,
                tts_group_id="session-b",
                voice_profile_id="voice-b",
                spoken_unit_count=10,
                speaking_rate_cpm=200.0,
                tempo_adjustment=1.2,
                integrated_lufs=-20.0,
                true_peak_dbfs=-2.0,
                gap_after=0.0,
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "script_structure.json").write_text(
                json.dumps(ScriptDocument(sentences=sentences).model_dump(mode="json"), ensure_ascii=False),
                encoding="utf-8",
            )
            report = generate_quality_report(
                task_dir,
                shots,
                plans,
                timings,
                minimum_confidence=0.5,
            )

        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("NARRATION_SPEAKING_RATE_OUT_OF_RANGE", codes)
        self.assertIn("NARRATION_VOICE_PROFILE_INCONSISTENT", codes)
        self.assertIn("NARRATION_TTS_SESSION_FRAGMENTED", codes)
        self.assertEqual(report["metrics"]["narration_speaking_rate_outlier_count"], 2)
        self.assertEqual(report["metrics"]["narration_voice_profile_count"], 2)
        self.assertEqual(report["metrics"]["narration_tts_session_count"], 2)
        rate_issue = next(
            issue
            for issue in report["issues"]
            if issue["code"] == "NARRATION_SPEAKING_RATE_OUT_OF_RANGE"
        )
        self.assertEqual(rate_issue["severity"], "error")


class BlockingQualityWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_waits_for_blocking_worker_to_finish(self) -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def blocking_work() -> str:
            started.set()
            release.wait(timeout=5.0)
            finished.set()
            return "done"

        task = asyncio.create_task(_run_blocking_until_complete(blocking_work))
        await asyncio.to_thread(started.wait, 1.0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())


if __name__ == "__main__":
    unittest.main()
