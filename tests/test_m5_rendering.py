import json
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import cv2
from fastapi.testclient import TestClient

from backend.main import app, task_manager
from backend.models import (
    AnnotatedShot,
    BeatMatch,
    MatchCandidate,
    MatchPlanItem,
    SentenceTiming,
    SyncSoundSelection,
    TTSWordTiming,
    VisionQuality,
)
from backend.rendering import (
    MIN_PREFERRED_CLIP_SECONDS,
    RenderingError,
    build_edl,
    fill_clips,
    prepare_information_card_shots,
    render_final_video,
)
from backend.subtitles import (
    MAX_SUBTITLE_CHARS,
    SUBTITLE_TEMPLATE_ID,
    generate_ass_subtitles,
    normalize_subtitle_text,
    split_subtitle_text,
    validate_subtitle_artifacts,
)
from backend.task_manager import TaskRecord
from backend.tts_pipeline import SENTENCE_GAP_SECONDS, probe_audio_duration


class SubtitleGenerationTest(unittest.TestCase):
    def test_protected_address_date_and_event_name_use_one_stable_event(self) -> None:
        text = (
            "位于南宁市西乡塘区鲁班路95号的南宁信息港广场"
            "1月30日举办了“金马贺岁，高新同驰”迎春市集，"
        )
        timing = SentenceTiming(
            sentence_id=0,
            text=text,
            audio_path="tts/sent_0.mp3",
            duration=8.0,
            start=0.0,
            end=8.0,
            gap_after=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            events = generate_ass_subtitles(task_dir, [timing])
            ass_text = (task_dir / "subs.ass").read_text(encoding="utf-8")

        self.assertEqual(len(events), 1)
        self.assertIn("NewsSentence2024", ass_text)
        self.assertIn("1月30日", ass_text)

    def test_short_contextual_overlay_does_not_force_stable_subtitle(self) -> None:
        timing = SentenceTiming(
            sentence_id=0,
            text="主办单位发布通知。",
            audio_path="tts/sent_0.mp3",
            duration=2.0,
            start=0.0,
            end=2.0,
            gap_after=0.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            generate_ass_subtitles(
                task_dir,
                [timing],
                information_card_sentence_ids={0},
            )
            dialogue_lines = [
                line
                for line in (task_dir / "subs.ass").read_text(encoding="utf-8").splitlines()
                if line.startswith("Dialogue:")
            ]

        self.assertTrue(any(",NewsDialogue2024,," in line for line in dialogue_lines))
        self.assertFalse(any(",NewsSentence2024,," in line for line in dialogue_lines))

    def test_uses_natural_single_line_breaks_and_broadcast_template(self) -> None:
        text = "本市今天发布重要产业政策，多个重点项目将在本月正式启动，有关部门将完善配套服务措施。"
        timing = SentenceTiming(
            sentence_id=0,
            text=text,
            audio_path="tts/sent_0.mp3",
            duration=4.0,
            start=1.0,
            end=5.0,
        )
        chunks = split_subtitle_text(text)
        self.assertEqual(
            chunks,
            [
                "本市今天发布重要产业政策",
                "多个重点项目将在本月正式启动",
                "有关部门将完善配套服务措施",
            ],
        )
        self.assertTrue(all(len(chunk) <= MAX_SUBTITLE_CHARS and "\n" not in chunk for chunk in chunks))

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            events = generate_ass_subtitles(task_dir, [timing])
            ass_text = (task_dir / "subs.ass").read_text(encoding="utf-8")
            manifest = validate_subtitle_artifacts(
                task_dir / "subs.ass",
                task_dir / "subtitle_manifest.json",
            )

        self.assertEqual(events[0].start, timing.start)
        self.assertEqual(events[-1].end, timing.end)
        self.assertTrue(all(events[index].end == events[index + 1].start for index in range(len(events) - 1)))
        self.assertIn(f"TemplateId: {SUBTITLE_TEMPLATE_ID}", ass_text)
        self.assertIn("Style: NewsDialogue2024,Noto Sans SC,54", ass_text)
        self.assertIn(",1,4,1,5,192,192,108,1", ass_text)
        self.assertIn(r"\pos(960,918)\clip(192,864,1728,972)", ass_text)
        self.assertEqual(manifest["layout"]["dialogue_region"], {
            "left": 192,
            "top": 864,
            "right": 1728,
            "bottom": 972,
            "height": 108,
        })
        self.assertEqual(manifest["template"]["nominal_1080p_font_points"], 40.5)
        self.assertEqual(manifest["template"]["font_height_ratio"], 0.05)
        self.assertEqual(manifest["template"]["contrast_ratio"], 21.0)
        self.assertEqual(ass_text.count("Dialogue:"), len(events))

    def test_normalizes_simplified_chinese_and_allows_only_book_title_marks(self) -> None:
        text = "《繁體新聞標題》發布，相關部門表示：項目將於今日啟動！"

        chunks = split_subtitle_text(text)

        self.assertEqual("".join(chunks).replace(" ", ""), "《繁体新闻标题》发布相关部门表示项目将于今日启动")
        self.assertEqual(normalize_subtitle_text(text), "《繁体新闻标题》发布 相关部门表示 项目将于今日启动")
        self.assertEqual(normalize_subtitle_text("《哈利·波特》正式出版。"), "《哈利 波特》正式出版")
        self.assertTrue(all(len(chunk) <= 15 for chunk in chunks))
        self.assertTrue(all(not any(mark in chunk for mark in "，。！？；：、,.!?;:") for chunk in chunks))

    def test_scales_template_geometry_to_4k_at_five_percent_font_height(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            generate_ass_subtitles(
                task_dir,
                [_timing(duration=1.0)],
                frame_width=3840,
                frame_height=2160,
            )
            ass_text = (task_dir / "subs.ass").read_text(encoding="utf-8")
            manifest = validate_subtitle_artifacts(
                task_dir / "subs.ass",
                task_dir / "subtitle_manifest.json",
            )

        self.assertIn("Style: NewsDialogue2024,Noto Sans SC,108", ass_text)
        self.assertIn(",2,0,1,8,2,5,384,384,216,1", ass_text)
        self.assertIn(r"\pos(1920,1836)\clip(384,1728,3456,1944)", ass_text)
        self.assertEqual(manifest["layout"]["font_size"], 108)
        self.assertEqual(manifest["layout"]["outline_size"], 8)
        self.assertEqual(manifest["layout"]["dialogue_region"]["height"], 216)
        self.assertEqual(manifest["template"]["uhd_4k_font_height_tolerance"], 0.01)

    def test_rejects_sub_eighty_millisecond_sentence_gap(self) -> None:
        first = _timing(duration=1.0)
        second = SentenceTiming(
            sentence_id=1,
            text="下一句新闻内容。",
            audio_path="tts/sent_1.mp3",
            duration=1.0,
            start=1.07,
            end=2.07,
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "80ms"):
                generate_ass_subtitles(Path(directory), [first, second])

    def test_allows_contiguous_units_in_one_tts_group_and_clamps_word_end(self) -> None:
        first = SentenceTiming(
            sentence_id=0,
            text="马年新春将至，",
            audio_path="tts/sent_0.wav",
            duration=1.6,
            start=0.0,
            end=1.6,
            tts_group_id="group_0_1",
            gap_after=0.0,
            words=[TTSWordTiming(text="马年新春将至，", start=0.04, end=1.614)],
        )
        second = SentenceTiming(
            sentence_id=1,
            text="一个迎春平台。",
            audio_path="tts/sent_1.wav",
            duration=1.0,
            start=1.6,
            end=2.6,
            tts_group_id="group_0_1",
            gap_after=0.0,
            words=[TTSWordTiming(text="一个迎春平台。", start=0.0, end=0.95)],
        )

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            events = generate_ass_subtitles(task_dir, [first, second])
            manifest = validate_subtitle_artifacts(
                task_dir / "subs.ass",
                task_dir / "subtitle_manifest.json",
            )

        first_events = [event for event in events if event.sentence_id == 0]
        second_events = [event for event in events if event.sentence_id == 1]
        self.assertEqual(first_events[-1].end, 1.6)
        self.assertEqual(second_events[0].start, 1.6)
        self.assertLessEqual(first_events[-1].end, second_events[0].start)
        self.assertIsNone(manifest["minimum_observed_sentence_gap_ms"])

    def test_uses_word_timestamps_and_renders_title_without_body_narration(self) -> None:
        timing = SentenceTiming(
            sentence_id=0,
            text="灌阳油茶、现做寿司等特色小吃。",
            audio_path="tts/sent_0.mp3",
            duration=2.0,
            start=1.0,
            end=3.0,
            gap_after=0.0,
            words=[
                TTSWordTiming(text="灌阳油茶", start=0.2, end=0.8),
                TTSWordTiming(text="现做寿司", start=0.9, end=1.5),
                TTSWordTiming(text="特色小吃", start=1.5, end=1.75),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            events = generate_ass_subtitles(task_dir, [timing], title="数十家企业汇聚迎春市集")
            manifest = validate_subtitle_artifacts(
                task_dir / "subs.ass",
                task_dir / "subtitle_manifest.json",
            )
            ass_text = (task_dir / "subs.ass").read_text(encoding="utf-8")

        self.assertEqual(events[0].start, 1.2)
        self.assertEqual(events[-1].end, 2.75)
        self.assertEqual(manifest["word_timing_coverage_ratio"], 1.0)
        self.assertEqual(manifest["title_event_count"], 1)
        self.assertIn("NewsTitle2024", ass_text)

    def test_keeps_full_subtitle_static_while_visual_beats_change(self) -> None:
        text = "灌阳油茶、现做寿司等特色小吃引来众多品尝者。"
        timing = SentenceTiming(
            sentence_id=4,
            text=text,
            audio_path="tts/sent_4.mp3",
            duration=5.04,
            start=28.741146,
            end=33.781146,
            gap_after=0.16,
            words=[
                TTSWordTiming(text="灌阳油茶、", start=0.04, end=0.81),
                TTSWordTiming(text="现做寿司", start=1.33, end=2.34),
                TTSWordTiming(text="等特色小吃引来众多品尝者。", start=2.34, end=4.98),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            events = generate_ass_subtitles(task_dir, [timing], stable_sentence_ids={4})
            manifest = validate_subtitle_artifacts(
                task_dir / "subs.ass",
                task_dir / "subtitle_manifest.json",
            )
            ass_text = (task_dir / "subs.ass").read_text(encoding="utf-8")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].text, "灌阳油茶 现做寿司等特色小吃引来众多品尝者")
        self.assertAlmostEqual(events[0].start, 28.78, places=6)
        self.assertAlmostEqual(events[0].end, 33.72, places=6)
        self.assertEqual(ass_text.count("Dialogue:"), 1)
        self.assertIn("Dialogue: 11,", ass_text)
        self.assertIn(",NewsSentence2024,,", ass_text)
        self.assertNotIn(",NewsDialogue2024,,", ass_text)
        self.assertEqual(manifest["stable_event_count"], 1)
        self.assertEqual(manifest["stable_sentence_ids"], [4])

    def test_information_card_sentence_keeps_standard_body_style(self) -> None:
        timing = _timing(duration=1.0).model_copy(
            update={"text": "本次活动将持续到1月31日。", "gap_after": 0.0}
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            generate_ass_subtitles(
                task_dir,
                [timing],
                title="迎春市集新闻",
                information_card_sentence_ids={0},
            )
            manifest = validate_subtitle_artifacts(
                task_dir / "subs.ass",
                task_dir / "subtitle_manifest.json",
            )
            ass_text = (task_dir / "subs.ass").read_text(encoding="utf-8")

        body_line = next(
            line
            for line in ass_text.splitlines()
            if line.startswith("Dialogue:") and "本次活动将持续到1月31日" in line
        )
        self.assertIn(",NewsDialogue2024,,", body_line)
        self.assertIn(r"\pos(960,918)\clip(192,864,1728,972)", body_line)
        self.assertNotIn(",NewsTitle2024,,", body_line)
        self.assertEqual(ass_text.count("Dialogue: 10,"), 1)
        self.assertEqual(ass_text.count("Dialogue: 20,"), 1)
        self.assertEqual(manifest["event_count"], 2)
        self.assertEqual(manifest["title_event_count"], 1)

    def test_detects_tampered_template_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            generate_ass_subtitles(task_dir, [_timing(duration=1.0)])
            ass_path = task_dir / "subs.ass"
            manifest_path = task_dir / "subtitle_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["template_id"] = "WRONG-TEMPLATE"
            manifest["ass_sha256"] = hashlib.sha256(ass_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "模板编号"):
                validate_subtitle_artifacts(ass_path, manifest_path)

    def test_detects_tampered_style_even_with_recomputed_ass_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            generate_ass_subtitles(task_dir, [_timing(duration=1.0)])
            ass_path = task_dir / "subs.ass"
            manifest_path = task_dir / "subtitle_manifest.json"
            ass_text = ass_path.read_text(encoding="utf-8").replace(
                "Style: NewsDialogue2024,Noto Sans SC,54,",
                "Style: NewsDialogue2024,Noto Sans SC,40,",
            )
            ass_path.write_text(ass_text, encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["ass_sha256"] = hashlib.sha256(ass_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "样式参数"):
                validate_subtitle_artifacts(ass_path, manifest_path)


class VideoRangeEndpointTest(unittest.TestCase):
    def test_serves_partial_content_for_player_seeking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            content = bytes(range(256))
            (task_dir / "final.mp4").write_bytes(content)
            task_id = "m5-range-test"
            task_manager._tasks[task_id] = TaskRecord(
                task_id=task_id,
                task_dir=task_dir,
                script="test",
                uploads=[],
            )
            try:
                with TestClient(app) as client:
                    response = client.get(
                        f"/api/tasks/{task_id}/video?token={task_manager._tasks[task_id].access_token}",
                        headers={"Range": "bytes=10-29"},
                    )
            finally:
                task_manager._tasks.pop(task_id, None)

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, content[10:30])
        self.assertEqual(response.headers["content-range"], "bytes 10-29/256")
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))


class EDLGenerationTest(unittest.TestCase):
    def test_avoids_short_fragments_and_freezes_long_shot(self) -> None:
        timing = _timing(duration=2.95)
        pool = [
            _shot(0, duration=1.0, quality=0.5),
            _shot(1, duration=0.5, quality=0.6),
            _shot(2, duration=2.2, quality=0.7),
        ]

        clips = fill_clips(timing, pool)

        self.assertEqual([clip.shot_id for clip in clips], [2])
        self.assertAlmostEqual(clips[0].out_time - clips[0].in_time, 2.2, places=5)
        self.assertAlmostEqual(clips[0].freeze_pad or 0.0, timing.duration + SENTENCE_GAP_SECONDS - 2.2, places=5)

    def test_preserves_semantic_primary_before_later_long_shot(self) -> None:
        timing = _timing(duration=3.0)
        pool = [
            _shot(0, duration=1.6, quality=0.9),
            _shot(1, duration=1.7, quality=0.8),
            _shot(2, duration=4.0, quality=0.7),
        ]

        clips = fill_clips(timing, pool)

        self.assertEqual([clip.shot_id for clip in clips], [0, 2])
        self.assertAlmostEqual(clips[0].out_time - clips[0].in_time, 1.6, places=5)
        self.assertAlmostEqual(clips[1].out_time - clips[1].in_time, 1.4 + SENTENCE_GAP_SECONDS, places=5)

    def test_freezes_last_frame_when_material_is_insufficient(self) -> None:
        clips = fill_clips(_timing(duration=2.0), [_shot(0, duration=1.0, quality=0.8)])

        self.assertEqual(len(clips), 1)
        self.assertAlmostEqual(clips[0].freeze_pad or 0.0, 1.0 + SENTENCE_GAP_SECONDS, places=6)

    def test_build_edl_persists_aliases_and_timeline(self) -> None:
        timing = _timing(duration=1.0)
        shot = _shot(0, duration=2.0, quality=0.9)
        match = MatchPlanItem(
            sentence_id=0,
            text=timing.text,
            shot_id=0,
            confidence=0.9,
            alternates=[],
            is_fallback=False,
            candidates=[MatchCandidate(shot_id=0, similarity=1.0)],
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            edl = build_edl(task_dir, [timing], [match], [shot])
            persisted = json.loads((task_dir / "edl.json").read_text(encoding="utf-8"))

        self.assertAlmostEqual(edl[0].timeline_end, 1.0 + SENTENCE_GAP_SECONDS, places=6)
        self.assertIn("in", persisted[0]["clips"][0])
        self.assertIn("out", persisted[0]["clips"][0])
        self.assertNotIn("in_time", persisted[0]["clips"][0])

    def test_build_edl_rejects_reused_shot(self) -> None:
        timings = [
            _timing(duration=3.0),
            _timing(duration=3.0).model_copy(
                update={
                    "sentence_id": 1,
                    "text": "第二句测试新闻内容。",
                    "audio_path": "tts/sent_1.mp3",
                    "start": 3.12,
                    "end": 6.12,
                }
            ),
        ]
        shot = _shot(0, duration=10.0, quality=0.9)
        matches = [
            MatchPlanItem(
                sentence_id=timing.sentence_id,
                text=timing.text,
                shot_id=0,
                confidence=0.9,
                candidates=[MatchCandidate(shot_id=0, similarity=1.0)],
            )
            for timing in timings
        ]

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RenderingError, "重复镜头"):
                build_edl(Path(directory), timings, matches, [shot])

    def test_build_edl_freezes_primary_instead_of_consuming_alternate(self) -> None:
        timing = _timing(duration=3.0)
        primary = _shot(0, duration=1.0, quality=0.9)
        alternate = _shot(1, duration=5.0, quality=0.8)
        match = MatchPlanItem(
            sentence_id=0,
            text=timing.text,
            shot_id=primary.shot_id,
            confidence=0.9,
            alternates=[alternate.shot_id],
            candidates=[
                MatchCandidate(shot_id=primary.shot_id, similarity=0.9),
                MatchCandidate(shot_id=alternate.shot_id, similarity=0.8),
            ],
        )

        with tempfile.TemporaryDirectory() as directory:
            edl = build_edl(Path(directory), [timing], [match], [primary, alternate])

        self.assertEqual([clip.shot_id for clip in edl[0].clips], [primary.shot_id])
        self.assertAlmostEqual(
            edl[0].clips[0].freeze_pad or 0.0,
            timing.duration + timing.gap_after - primary.duration,
            places=6,
        )

    def test_build_edl_honors_and_persists_evidence_aligned_in_point(self) -> None:
        timing = _timing(duration=3.0)
        shot = _shot(0, duration=10.0, quality=0.9)
        candidate = MatchCandidate(
            shot_id=0,
            similarity=0.8,
            combined_score=0.9,
            preferred_in_time=5.0,
        )
        match = MatchPlanItem(
            sentence_id=0,
            text=timing.text,
            shot_id=0,
            confidence=0.9,
            candidates=[candidate],
        )

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            edl = build_edl(task_dir, [timing], [match], [shot])
            persisted = json.loads((task_dir / "edl.json").read_text(encoding="utf-8"))

        clip = edl[0].clips[0]
        self.assertEqual(clip.in_time, 5.0)
        self.assertAlmostEqual(clip.out_time, 8.0 + SENTENCE_GAP_SECONDS, places=6)
        self.assertTrue(clip.evidence_aligned)
        self.assertTrue(persisted[0]["clips"][0]["evidence_aligned"])

    def test_sync_sound_uses_original_source_time_range(self) -> None:
        timing = _timing(duration=1.5).model_copy(update={"audio_kind": "sync"})
        shot = _shot(0, duration=5.0, quality=0.9)
        selection = SyncSoundSelection(
            source_index=0,
            source_media_path="raw/source.mp4",
            shot_id=0,
            text="项目今天正式启动。",
            start=1.0,
            end=2.5,
            similarity=0.95,
        )
        match = MatchPlanItem(
            sentence_id=0,
            text=timing.text,
            shot_id=0,
            confidence=0.95,
            candidates=[MatchCandidate(shot_id=0, similarity=0.95)],
            sync_sound=selection,
        )

        with tempfile.TemporaryDirectory() as directory:
            edl = build_edl(Path(directory), [timing], [match], [shot])

        clip = edl[0].clips[0]
        self.assertEqual(clip.src, "norm/norm_0.mp4")
        self.assertAlmostEqual(clip.in_time, 1.0, places=6)
        self.assertAlmostEqual(clip.out_time, 2.5, places=6)
        self.assertAlmostEqual(clip.freeze_pad or 0.0, SENTENCE_GAP_SECONDS, places=6)

    def test_build_edl_uses_distinct_shots_for_visual_beats(self) -> None:
        timing = _timing(duration=5.04).model_copy(
            update={
                "text": "灌阳油茶、现做寿司等特色小吃引来众多品尝者。",
                "gap_after": 0.16,
                "words": [
                    TTSWordTiming(text="灌", start=0.04, end=0.32),
                    TTSWordTiming(text="阳", start=0.32, end=0.59),
                    TTSWordTiming(text="油", start=0.59, end=0.78),
                    TTSWordTiming(text="茶、", start=0.78, end=0.81),
                    TTSWordTiming(text="现", start=1.33, end=1.65),
                    TTSWordTiming(text="做", start=1.65, end=1.82),
                    TTSWordTiming(text="寿", start=1.82, end=2.08),
                    TTSWordTiming(text="司", start=2.08, end=2.34),
                ],
            }
        )
        shots = [_shot(0, duration=6.0, quality=0.9), _shot(1, duration=6.0, quality=0.8)]
        oil_candidate = MatchCandidate(shot_id=0, similarity=0.8, combined_score=0.85, matched_terms=["油茶"])
        sushi_candidate = MatchCandidate(shot_id=1, similarity=0.8, combined_score=0.85, matched_terms=["寿司"])
        match = MatchPlanItem(
            sentence_id=0,
            text="灌阳油茶、现做寿司等特色小吃。",
            shot_id=0,
            confidence=0.8,
            candidates=[oil_candidate],
            beat_matches=[
                BeatMatch(
                    beat_id=0,
                    text="灌阳油茶",
                    entities=["油茶"],
                    requires_entity_coverage=True,
                    shot_id=0,
                    confidence=0.8,
                    candidates=[oil_candidate],
                ),
                BeatMatch(
                    beat_id=1,
                    text="现做寿司",
                    entities=["寿司"],
                    requires_entity_coverage=True,
                    shot_id=1,
                    confidence=0.8,
                    candidates=[sushi_candidate],
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as directory:
            edl = build_edl(Path(directory), [timing], [match], shots)

        self.assertEqual([clip.shot_id for clip in edl[0].clips], [0, 1])
        first_duration = edl[0].clips[0].out_time - edl[0].clips[0].in_time
        second_duration = edl[0].clips[1].out_time - edl[0].clips[1].in_time
        self.assertAlmostEqual(first_duration, 1.33, places=6)
        self.assertAlmostEqual(second_duration, 3.87, places=6)
        self.assertAlmostEqual(first_duration + second_duration, 5.20, places=6)


class InformationCardTest(unittest.IsolatedAsyncioTestCase):
    async def test_factual_overlay_never_selects_ai_generated_media(self) -> None:
        timing = SentenceTiming(
            sentence_id=0,
            text="本次活动由南宁信息港主办。",
            audio_path="tts/sent_0.mp3",
            duration=2.0,
            start=0.0,
            end=2.0,
            gap_after=0.0,
        )
        source = _shot(0, duration=3.0, quality=0.7).model_copy(
            update={"description": "南宁信息港活动入口", "entities": ["南宁信息港"]}
        )
        generated = _shot(1, duration=3.0, quality=1.0).model_copy(
            update={
                "source_name": "generated_fill_1",
                "norm_path": "generated/fill_shot_1.mp4",
                "media_origin": "generated",
                "description": "AI生成示意画面：南宁信息港",
                "entities": ["南宁信息港"],
            }
        )
        candidate = MatchCandidate(shot_id=generated.shot_id, similarity=0.99)
        plan = MatchPlanItem(
            sentence_id=0,
            text=timing.text,
            shot_id=generated.shot_id,
            confidence=0.0,
            is_fallback=True,
            candidates=[candidate],
            overlay_kind="organization",
            overlay_text=timing.text,
        )

        with tempfile.TemporaryDirectory() as directory:
            plans = [plan]
            await prepare_information_card_shots(
                Path(directory),
                [timing],
                plans,
                [source, generated],
            )

        self.assertEqual(plans[0].shot_id, source.shot_id)

    async def test_joint_overlay_assignment_caps_dominant_event_identity_shot(self) -> None:
        timings = [
            SentenceTiming(
                sentence_id=index,
                text=f"第{index + 1}条活动事实。",
                audio_path=f"tts/sent_{index}.mp3",
                duration=1.5,
                start=index * 1.5,
                end=(index + 1) * 1.5,
                gap_after=0.0,
            )
            for index in range(7)
        ]
        shots = [_shot(index, duration=4.0, quality=0.9) for index in range(8)]
        shots[0] = shots[0].model_copy(
            update={
                "description": "南宁信息港迎春市集入口",
                "ocr_texts": ["南宁信息港迎春市集"],
                "entities": ["南宁信息港", "迎春市集"],
                "search_text": "南宁信息港迎春市集入口活动展板",
            }
        )
        plans = [
            MatchPlanItem(
                sentence_id=index,
                text=timing.text,
                shot_id=0,
                confidence=0.7,
                candidates=[
                    MatchCandidate(shot_id=0, similarity=0.9, combined_score=0.8),
                    MatchCandidate(
                        shot_id=index + 1,
                        similarity=0.82,
                        combined_score=0.72,
                    ),
                ],
                overlay_kind="date" if index in {0, 6} else "abstract",
                overlay_text=timing.text,
                visual_group_id=index // 2,
            )
            for index, timing in enumerate(timings)
        ]

        with tempfile.TemporaryDirectory() as directory:
            selected_ids = await prepare_information_card_shots(
                Path(directory),
                timings,
                plans,
                shots,
            )

        assigned_shots = [item.shot_id for item in plans]
        self.assertEqual(selected_ids, list(range(7)))
        self.assertEqual(assigned_shots.count(0), 1)
        self.assertEqual(len(set(assigned_shots)), 7)

    async def test_overlay_assignment_rejects_insufficient_unique_shots(self) -> None:
        timings = [
            SentenceTiming(
                sentence_id=index,
                text=f"第{index + 1}条活动事实。",
                audio_path=f"tts/sent_{index}.mp3",
                duration=1.0,
                start=float(index),
                end=float(index + 1),
                gap_after=0.0,
            )
            for index in range(2)
        ]
        shot = _shot(0, duration=3.0, quality=0.9)
        plans = [
            MatchPlanItem(
                sentence_id=timing.sentence_id,
                text=timing.text,
                shot_id=shot.shot_id,
                confidence=0.7,
                candidates=[MatchCandidate(shot_id=shot.shot_id, similarity=0.8)],
                overlay_kind="abstract",
            )
            for timing in timings
        ]

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RenderingError, "一对一分配"):
                await prepare_information_card_shots(
                    Path(directory),
                    timings,
                    plans,
                    [shot],
                )

    async def test_organization_claim_uses_related_environment_shot_and_overlay(self) -> None:
        timing = SentenceTiming(
            sentence_id=0,
            text="本次活动由南宁信息港主办、悦和物业公司协办。",
            audio_path="tts/sent_0.mp3",
            duration=3.0,
            start=0.0,
            end=3.0,
            gap_after=0.0,
        )
        generic = _shot(0, duration=4.0, quality=0.98)
        event = _shot(1, duration=4.0, quality=0.8).model_copy(
            update={
                "description": "南宁信息港迎春市集入口",
                "ocr_texts": ["南宁信息港迎春市集"],
                "entities": ["南宁信息港"],
                "search_text": "南宁信息港迎春市集入口活动展板",
            }
        )
        plan = MatchPlanItem(
            sentence_id=0,
            text=timing.text,
            shot_id=generic.shot_id,
            confidence=0.2,
            candidates=[MatchCandidate(shot_id=generic.shot_id, similarity=0.2)],
            overlay_kind="organization",
            overlay_text=timing.text,
            visual_group_id=3,
        )

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            shots = [generic, event]
            plans = [plan]
            sentence_ids = await prepare_information_card_shots(task_dir, [timing], plans, shots)

        self.assertEqual(sentence_ids, [0])
        self.assertEqual(plans[0].shot_id, event.shot_id)
        self.assertEqual(plans[0].overlay_kind, "organization")
        self.assertEqual(plans[0].beat_matches[0].intent_type, "organization")

    async def test_replaces_date_fallback_with_source_footage_overlay(self) -> None:
        timing = SentenceTiming(
            sentence_id=0,
            text="本次活动将持续到1月31日。",
            audio_path="tts/sent_0.mp3",
            duration=2.0,
            start=0,
            end=2.0,
            gap_after=0.0,
        )
        shot = _shot(0, duration=3.0, quality=0.8)
        candidate = MatchCandidate(shot_id=0, similarity=0.2, combined_score=0.2)
        plan = MatchPlanItem(
            sentence_id=0,
            text=timing.text,
            shot_id=0,
            confidence=0.0,
            is_fallback=True,
            candidates=[candidate],
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            shots = [shot]
            plans = [plan]
            sentence_ids = await prepare_information_card_shots(task_dir, [timing], plans, shots)

            self.assertEqual(sentence_ids, [0])
            self.assertTrue(plans[0].is_fallback)
            self.assertEqual(plans[0].shot_id, shot.shot_id)
            self.assertEqual(plans[0].confidence, 0.0)
            self.assertEqual([item.shot_id for item in shots], [shot.shot_id])
            self.assertFalse((task_dir / "generated_cards").exists())

    async def test_repairs_existing_generated_card_with_event_identity_shot(self) -> None:
        timing = SentenceTiming(
            sentence_id=1,
            text="本次活动将持续到1月31日。",
            audio_path="tts/sent_1.mp3",
            duration=2.0,
            start=2.0,
            end=4.0,
            gap_after=0.0,
        )
        generic_shot = _shot(0, duration=4.0, quality=0.95)
        event_shot = _shot(1, duration=4.0, quality=0.8).model_copy(
            update={
                "description": "南宁信息港迎春市集活动入口",
                "keywords": ["迎春市集", "活动现场", "入口"],
                "entities": ["南宁信息港", "迎春市集"],
                "search_text": "南宁信息港迎春市集活动现场入口",
            }
        )
        generated_shot = _shot(2, duration=2.0, quality=1.0).model_copy(
            update={
                "source_index": 2,
                "source_name": "generated_information_card_1.mp4",
                "norm_path": "generated_cards/sentence_1.mp4",
                "thumb_path": "thumbs/shot_2.jpg",
                "description": "新闻结束信息卡",
            }
        )
        previous = MatchPlanItem(
            sentence_id=0,
            text="南宁信息港举办迎春市集，为市民打造迎春平台。",
            shot_id=0,
            confidence=0.8,
            candidates=[MatchCandidate(shot_id=0, similarity=0.8)],
        )
        generated_candidate = MatchCandidate(
            shot_id=2,
            similarity=1.0,
            lexical_score=1.0,
            combined_score=1.0,
        )
        ending = MatchPlanItem(
            sentence_id=1,
            text=timing.text,
            shot_id=2,
            confidence=1.0,
            is_fallback=False,
            candidates=[generated_candidate],
        )
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "generated_cards").mkdir()
            (task_dir / "thumbs").mkdir()
            generated_norm_path = generated_shot.norm_path
            generated_thumb_path = generated_shot.thumb_path
            assert generated_norm_path is not None
            assert generated_thumb_path is not None
            (task_dir / generated_norm_path).write_bytes(b"generated")
            (task_dir / generated_thumb_path).write_bytes(b"thumb")
            shots = [generic_shot, event_shot, generated_shot]
            plans = [previous, ending]

            sentence_ids = await prepare_information_card_shots(task_dir, [timing], plans, shots)

            self.assertEqual(sentence_ids, [1])
            self.assertEqual(plans[1].shot_id, event_shot.shot_id)
            self.assertEqual([shot.shot_id for shot in shots], [0, 1])
            self.assertFalse((task_dir / generated_norm_path).exists())
            self.assertFalse((task_dir / generated_thumb_path).exists())


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class RenderingSmokeTest(unittest.IsolatedAsyncioTestCase):
    def test_ass_runtime_font_directory_contains_only_fonts(self) -> None:
        from backend.rendering import _prepare_ass_font_directory

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory) / "task"
            fonts_dir = Path(directory) / "fonts"
            task_dir.mkdir()
            fonts_dir.mkdir()
            (fonts_dir / "NotoSansSC-Test.ttf").write_bytes(b"font-data")
            (fonts_dir / "OFL.txt").write_text("license", encoding="utf-8")
            (fonts_dir / "FONT_ASSET.md").write_text("notes", encoding="utf-8")

            runtime_dir = _prepare_ass_font_directory(task_dir, fonts_dir)
            runtime_files = [path.name for path in runtime_dir.iterdir()]

        self.assertEqual(runtime_files, ["NotoSansSC-Test.ttf"])

    async def test_freeze_concat_subtitle_burn_and_mix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            norm_dir = task_dir / "norm"
            norm_dir.mkdir()
            source_path = norm_dir / "norm_0.mp4"
            narration_path = task_dir / "narration.m4a"
            _run_ffmpeg(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    "color=c=0x23536f:s=1920x1080:r=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source_path),
                ]
            )
            _run_ffmpeg(
                [
                    "ffmpeg", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=1.5",
                    "-c:a", "aac", str(narration_path),
                ]
            )
            timing = SentenceTiming(
                sentence_id=0,
                text="这是一条用于验证字幕烧录同步的新闻。",
                audio_path="tts/sent_0.mp3",
                duration=1.5,
                start=0.0,
                end=1.5,
            )
            shot = _shot(0, duration=1.0, quality=0.9)
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
            edl = build_edl(task_dir, [timing], [match], [shot])
            progress: list[str] = []

            final_path = await render_final_video(
                task_dir,
                edl,
                lambda fraction, message: progress.append(message),
            )
            final_duration = _duration(final_path)
            narration_duration = await probe_audio_duration(narration_path, task_dir)
            probe = _probe_streams(final_path)
            bright_bounds = _bright_pixel_bounds(final_path, time_seconds=0.5)
            log_text = (task_dir / "task.log").read_text(encoding="utf-8")

            self.assertTrue(final_path.is_file())
            self.assertLessEqual(abs(final_duration - narration_duration), 0.3)
            self.assertEqual(probe["video"]["codec_name"], "h264")
            self.assertEqual((probe["video"]["width"], probe["video"]["height"]), (1920, 1080))
            self.assertEqual(probe["audio"]["codec_name"], "aac")
            x, y, width, height = bright_bounds
            self.assertGreaterEqual(x, 192)
            self.assertGreaterEqual(y, 864)
            self.assertLessEqual(x + width, 1729)
            self.assertLessEqual(y + height, 973)
            self.assertLess(abs((x + width / 2) - 960), 20)
            self.assertIn("tpad=stop_mode=clone", log_text)
            self.assertNotIn("backend/assets/fonts 未提供字体", log_text)
            self.assertEqual(progress[-1], "成片规格与时长校验完成")


def _timing(duration: float) -> SentenceTiming:
    return SentenceTiming(
        sentence_id=0,
        text="测试新闻句子内容。",
        audio_path="tts/sent_0.mp3",
        duration=duration,
        start=0.0,
        end=duration,
    )


def _shot(shot_id: int, duration: float, quality: float) -> AnnotatedShot:
    return AnnotatedShot(
        shot_id=shot_id,
        source_index=0,
        source_scene_index=shot_id,
        source_name="input.mp4",
        norm_path="norm/norm_0.mp4",
        start=0.0,
        end=duration,
        duration=duration,
        thumb_path=f"thumbs/shot_{shot_id}.jpg",
        status="available",
        description=f"镜头 {shot_id}",
        scene_type="outdoor",
        subjects=["市民"],
        actions=["行走"],
        keywords=["新闻", "现场", "市民"],
        quality=VisionQuality(sharp=quality, bright=quality),
    )


def _run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))


def _duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, check=True, text=True, encoding="utf-8",
    )
    return float(result.stdout.strip())


def _probe_streams(path: Path) -> dict[str, dict[str, object]]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, check=True, text=True, encoding="utf-8",
    )
    streams = json.loads(result.stdout)["streams"]
    return {
        "video": next(stream for stream in streams if stream["codec_type"] == "video"),
        "audio": next(stream for stream in streams if stream["codec_type"] == "audio"),
    }


def _bright_pixel_bounds(path: Path, *, time_seconds: float) -> tuple[int, int, int, int]:
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000)
        read, frame = capture.read()
    finally:
        capture.release()
    if not read or frame is None:
        raise AssertionError("无法读取成片画面以验证字幕安全区。")
    mask = cv2.inRange(frame, (210, 210, 210), (255, 255, 255))
    pixels = cv2.findNonZero(mask)
    if pixels is None:
        raise AssertionError("成片画面中未检测到白色字幕像素。")
    return cv2.boundingRect(pixels)


if __name__ == "__main__":
    unittest.main()
