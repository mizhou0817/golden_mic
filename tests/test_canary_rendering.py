"""Provider/FFmpeg-free regressions for overlays and visual-beat word alignment.

Sentence/beat text and word timings are from live canary 22d4b7aa (2026-09-21).
Its persisted plan is already collapsed; the original three shot assignments
cannot be recovered from it. IDs 101+ and their evidence below are TEST FIXTURES,
not a reconstruction of those assignments. Every output goes to a fresh TEMP.
"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.models import (
    AnnotatedShot,
    BeatMatch,
    MatchCandidate,
    MatchPlanItem,
    PronunciationReading,
    SentenceTiming,
    SyncSoundSelection,
    TTSWordTiming,
    VisionQuality,
)
from backend.pronunciation import apply_pronunciation_readings
from backend.rendering import (
    RenderingError,
    _visual_beat_starts,  # pyright: ignore[reportPrivateUsage]
    build_edl,
    prepare_information_card_shots,
)


EXACT_SENTENCE = (
    "位于南宁市西乡塘区鲁班路95号的南宁信息港广场"
    "1月30日举办了“金马贺岁，高新同驰”迎春市集，"
)
# Copied from the same canary's persisted pronunciation plan, not inferred here.
LIVE_READINGS = (("95", "九十五"), ("1", "一"), ("30", "三十"))
EXACT_SPOKEN_SENTENCE = (
    "位于南宁市西乡塘区鲁班路九十五号的南宁信息港广场"
    "一月三十日举办了“金马贺岁，高新同驰”迎春市集，"
)
# Actual returned words use Chinese number readings, not the script's digits.
LIVE_WORDS = (
    ("位", 0.295915, 0.345691), ("于", 0.345691, 0.400998),
    ("南", 0.400998, 0.577980), ("宁", 0.577980, 0.766023),
    ("市", 0.766023, 1.053619), ("西", 1.053619, 1.274847),
    ("乡", 1.274847, 1.451829), ("塘", 1.451829, 1.728363),
    ("区", 1.728363, 1.971713), ("鲁", 1.971713, 2.192941),
    ("班", 2.192941, 2.414168), ("路", 2.414168, 2.613273),
    ("九", 2.994937, 3.077896), ("十", 3.077896, 3.144265),
    ("五", 3.144265, 3.177449), ("号", 3.177449, 3.376553),
    ("的", 3.376553, 3.520352), ("南", 3.520352, 3.719456),
    ("宁", 3.719456, 3.929623), ("信", 3.929623, 4.161911),
    ("息", 4.161911, 4.338894), ("港", 4.609943, 4.692903),
    ("广", 4.692903, 4.770333), ("场", 4.770333, 5.102174),
    ("一", 5.400831, 5.555690), ("月", 5.555690, 5.677365),
    ("三", 5.677365, 5.920716), ("十", 5.920716, 6.108759),
    ("日", 6.108759, 6.307864), ("举", 6.307864, 6.518030),
    ("办", 6.518030, 6.860932), ("了“", 6.860932, 6.894117),
    ("金", 7.170651, 7.414001), ("马", 7.414001, 7.690535),
    ("贺", 7.690535, 7.933887), ("岁，", 7.933887, 8.243604),
    ("高", 8.636329, 8.719290), ("新", 8.719290, 8.885210),
    ("同", 8.885210, 9.172805), ("驰”", 9.637383, 9.670568),
    ("迎", 9.670568, 9.858611), ("春", 9.858611, 10.090899),
    ("市", 10.090899, 10.312128), ("集，", 10.312128, 10.699276),
)


def _source(shot_id: int) -> AnnotatedShot:
    return AnnotatedShot(
        shot_id=shot_id,
        source_index=shot_id,
        source_scene_index=0,
        source_name=f"test_{shot_id}.mp4",
        norm_path=f"norm/test_{shot_id}.mp4",
        start=0.0,
        end=8.067,
        duration=8.067,
        status="available",
        description="测试素材",
        quality=VisionQuality(sharp=0.8, bright=0.8),
    )


def _fixture() -> tuple[SentenceTiming, MatchPlanItem, list[AnnotatedShot]]:
    timing = SentenceTiming(
        sentence_id=1,
        text=EXACT_SENTENCE,
        audio_path="tts/sent_1.wav",
        duration=10.800104,
        start=1.809583,
        end=12.609687,
        gap_after=0.0,
        words=[TTSWordTiming(text=text, start=start, end=end) for text, start, end in LIVE_WORDS],
    )
    candidates = [
        MatchCandidate(shot_id=shot_id, similarity=0.2, combined_score=0.2)
        for shot_id in (101, 102, 103)
    ]
    beats = [
        BeatMatch(
            beat_id=0,
            text="位于南宁市西乡塘区鲁班路95号的南宁信息港广场",
            entities=["位于南宁市西乡塘区鲁班路95号的南宁信息港广场", "广场"],
            requires_entity_coverage=True,
            intent_type="entity",
            shot_id=101,
            confidence=0.2,
            is_fallback=True,
            candidates=[candidates[0]],
            visual_group_id=0,
        ),
        BeatMatch(
            beat_id=1,
            text="1月30日举办了",
            entities=["1月30日举办了", "办了"],
            intent_type="date",
            shot_id=102,
            confidence=0.2,
            is_fallback=True,
            candidates=[candidates[1]],
            visual_group_id=0,
        ),
        BeatMatch(
            beat_id=2,
            text="“金马贺岁，高新同驰”迎春市集",
            entities=["金马贺岁", "贺岁", "高新同驰迎春市集", "市集"],
            requires_entity_coverage=True,
            intent_type="entity",
            shot_id=103,
            confidence=0.2,
            is_fallback=True,
            candidates=[candidates[2]],
            visual_group_id=0,
        ),
    ]
    return timing, MatchPlanItem(
        sentence_id=1,
        text=EXACT_SENTENCE,
        shot_id=101,
        confidence=0.2,
        is_fallback=True,
        candidates=[candidates[0]],
        beat_matches=beats,
        visual_group_id=0,
    ), [_source(shot_id) for shot_id in (101, 102, 103)]


def _single_overlay() -> tuple[SentenceTiming, MatchPlanItem]:
    text = "本次活动将持续到1月31日。"
    return SentenceTiming(
        sentence_id=2, text=text, audio_path="tts/sent_2.wav",
        duration=2.0, start=12.609687, end=14.609687, gap_after=0.0,
    ), MatchPlanItem(
        sentence_id=2, text=text, shot_id=102, confidence=0.0, is_fallback=True,
        candidates=[
            MatchCandidate(shot_id=shot_id, similarity=0.99, combined_score=0.99)
            for shot_id in (101, 102, 103)
        ],
        overlay_kind="date", visual_group_id=0,
    )


class _SpokenSentenceTiming(SentenceTiming):
    # Optional compatibility context; production SentenceTiming has no such field.
    spoken_text: str | None = None


def _alignment_fixture(
    text: str,
    beat_texts: list[str],
    words: list[tuple[str, float, float]],
    *,
    spoken_text: str | None = None,
) -> tuple[SentenceTiming, list[BeatMatch]]:
    """Synthetic cues are explicit test inputs, never estimated from text length."""
    duration = max(end for _, _, end in words) + 0.1
    timing = _SpokenSentenceTiming(
        sentence_id=0, text=text, audio_path="tts/test.wav",
        start=0.0, end=duration, duration=duration, gap_after=0.0,
        words=[TTSWordTiming(text=word, start=start, end=end) for word, start, end in words],
        spoken_text=spoken_text,
    )
    beats = [
        BeatMatch(
            beat_id=index, text=beat_text, shot_id=101 + index, confidence=0.0,
            candidates=[MatchCandidate(shot_id=101 + index, similarity=0.0)],
        )
        for index, beat_text in enumerate(beat_texts)
    ]
    return timing, beats


class VisualBeatWordAlignmentTest(unittest.TestCase):
    def test_exact_live_date_starts_at_first_number_cue_not_suffix_entity(self) -> None:
        timing, item, shots = _fixture()
        before = (timing.model_dump(), item.model_dump())

        self.assertEqual(_visual_beat_starts(timing, item.beat_matches), [0.0, 5.400831, 7.170651])
        self.assertEqual(timing.words[24].text, "一")
        self.assertEqual(timing.words[24].start, 5.400831)
        self.assertEqual(timing.words[30].text, "办")
        self.assertEqual(timing.words[30].start, 6.518030)
        with tempfile.TemporaryDirectory() as directory:
            edl = build_edl(Path(directory), [timing], [item], shots)
        self.assertEqual([clip.shot_id for clip in edl[0].clips], [101, 102, 103])
        durations = [clip.out_time - clip.in_time for clip in edl[0].clips]
        for actual, expected in zip(durations, [5.400831, 1.769820, 3.629453], strict=True):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertTrue(all(clip.freeze_pad is None for clip in edl[0].clips))
        self.assertAlmostEqual(sum(durations), timing.duration, places=6)
        self.assertEqual((timing.model_dump(), item.model_dump()), before)

    def test_persisted_live_readings_agree_with_optional_spoken_text(self) -> None:
        timing, item, _ = _fixture()
        spoken = apply_pronunciation_readings(
            timing.text,
            [PronunciationReading(source=source, spoken=reading) for source, reading in LIVE_READINGS],
        )
        self.assertEqual(spoken, EXACT_SPOKEN_SENTENCE)
        with_context = _SpokenSentenceTiming(**timing.model_dump(), spoken_text=spoken)
        self.assertEqual(_visual_beat_starts(with_context, item.beat_matches), [0.0, 5.400831, 7.170651])

    def test_grouped_chinese_date_word_keeps_the_original_onset(self) -> None:
        timing, item, _ = _fixture()
        timing.words[26:28] = [TTSWordTiming(text="三十", start=5.677365, end=6.108759)]
        self.assertEqual(_visual_beat_starts(timing, item.beat_matches), [0.0, 5.400831, 7.170651])

    def test_fullwidth_and_unchanged_numbers_keep_character_to_word_ranges(self) -> None:
        for source_number, spoken_number in (("３０", "三十"), ("30", "30"), ("３０", "３０")):
            with self.subTest(source=source_number, spoken=spoken_number):
                timing, beats = _alignment_fixture(
                    f"开场，{source_number}日开幕。", ["开场", f"{source_number}日开幕"],
                    [("开场，", 0.1, 0.4), (spoken_number, 0.8, 1.3), ("日开幕。", 1.3, 2.0)],
                )
                self.assertEqual(_visual_beat_starts(timing, beats), [0.0, 0.8])

    def test_custom_number_readings_are_taken_from_cues_not_recalculated(self) -> None:
        for year in ("二零二四", "二〇二四"):
            for with_context in (False, True):
                with self.subTest(year=year, with_context=with_context):
                    spoken = f"开场介绍，幺洞幺号展厅已开放，{year}年项目启动。"
                    timing, beats = _alignment_fixture(
                        "开场介绍，101号展厅已开放，2024年项目启动。",
                        ["开场介绍", "101号展厅已开放", "2024年项目启动"],
                        [
                            ("开场介绍，", 0.1, 0.9), ("幺洞幺", 1.2, 1.8),
                            ("号展厅已开放，", 1.8, 2.8), (year, 3.1, 3.9),
                            ("年项目启动。", 3.9, 4.9),
                        ],
                        spoken_text=spoken if with_context else None,
                    )
                    self.assertEqual(_visual_beat_starts(timing, beats), [0.0, 1.2, 3.1])

    def test_same_source_number_can_have_distinct_persisted_readings(self) -> None:
        timing, beats = _alignment_fixture(
            "开场介绍，101号展厅开放，101号车辆到达。",
            ["开场介绍", "101号展厅开放", "101号车辆到达"],
            [
                ("开场介绍，", 0.1, 0.9), ("幺洞幺", 1.2, 1.8),
                ("号展厅开放，", 1.8, 2.8), ("一〇一", 3.1, 3.9),
                ("号车辆到达。", 3.9, 4.9),
            ],
            spoken_text="开场介绍，幺洞幺号展厅开放，一〇一号车辆到达。",
        )
        self.assertEqual(_visual_beat_starts(timing, beats), [0.0, 1.2, 3.1])

    def test_repeated_date_consumed_by_first_beat_is_not_reused(self) -> None:
        timing, beats = _alignment_fixture(
            "预告1月30日举办了活动，确认后1月30日举办了庆典。",
            ["预告1月30日举办了活动，确认后", "1月30日举办了"],
            [
                ("预告一月三十日举办了活动，", 0.1, 1.1), ("确认后", 1.3, 1.7),
                ("一", 2.1, 2.3), ("月", 2.3, 2.5), ("三十", 2.5, 2.7),
                ("日举办了庆典。", 2.7, 3.5),
            ],
        )
        beats[1].entities = ["1月30日举办了", "办了"]
        self.assertEqual(_visual_beat_starts(timing, beats), [0.0, 2.1])

    def test_ambiguous_repeated_dates_return_none_not_first_occurrence(self) -> None:
        timing, beats = _alignment_fixture(
            "开场，1月30日举办了活动，1月30日举办了庆典。",
            ["开场", "1月30日举办了"],
            [
                ("开场，", 0.1, 0.4), ("一月三十日", 0.8, 1.5),
                ("举办了活动，", 1.5, 2.2), ("一月三十日", 2.6, 3.3),
                ("举办了庆典。", 3.3, 4.0),
            ],
        )
        beats[1].entities = ["办了", "庆典"]
        self.assertIsNone(_visual_beat_starts(timing, beats))

    def test_ambiguous_numeric_anchors_cannot_choose_a_reading_boundary(self) -> None:
        timing, beats = _alignment_fixture(
            "开场，1一2收尾。", ["开场", "1一2收尾"],
            [("开场，", 0.1, 0.4), ("一一一一", 0.8, 1.5), ("收尾。", 1.7, 2.0)],
            spoken_text="开场，一一一一收尾。",
        )
        beats[1].entities = ["收尾"]
        self.assertIsNone(_visual_beat_starts(timing, beats))

    def test_missing_date_cues_do_not_fall_through_to_suffix_entity(self) -> None:
        timing, item, _ = _fixture()
        timing.words = timing.words[:24] + timing.words[29:]
        self.assertIsNone(_visual_beat_starts(timing, item.beat_matches))

    def test_unrelated_repeated_entity_is_not_word_alignment(self) -> None:
        timing, beats = _alignment_fixture(
            "活动开场，观众现场体验。", ["活动开场", "观众现场体验"],
            [("活动开场，", 0.1, 0.9), ("活动", 1.4, 1.8)],
        )
        beats[1].entities = ["活动"]
        self.assertIsNone(_visual_beat_starts(timing, beats))

    def test_spoken_text_must_agree_with_real_word_stream(self) -> None:
        timing, item, _ = _fixture()
        for spoken in (
            EXACT_SPOKEN_SENTENCE.replace("一月", "二月"),
            EXACT_SPOKEN_SENTENCE.replace("举办了", "取消了"),
            "",
        ):
            with self.subTest(spoken=spoken):
                with_context = _SpokenSentenceTiming(**timing.model_dump(), spoken_text=spoken)
                self.assertIsNone(_visual_beat_starts(with_context, item.beat_matches))

    def test_spoken_text_only_allows_number_replacements_not_rewritten_anchors(self) -> None:
        for opening, reading in (("开场", "大约一百"), ("改写开场", "幺洞幺")):
            with self.subTest(opening=opening, reading=reading):
                timing, beats = _alignment_fixture(
                    "开场，101号展厅开放。", ["开场", "101号展厅开放"],
                    [(opening, 0.1, 0.4), (reading, 0.8, 1.2), ("号展厅开放。", 1.2, 2.0)],
                    spoken_text=f"{opening}，{reading}号展厅开放。",
                )
                beats[1].entities = ["展厅开放"]
                self.assertIsNone(_visual_beat_starts(timing, beats))

    def test_no_words_with_spoken_text_preserves_existing_proportional_fallback(self) -> None:
        timing, item, shots = _fixture()
        timing.words = []
        with_context = _SpokenSentenceTiming(**timing.model_dump(), spoken_text=EXACT_SPOKEN_SENTENCE)
        self.assertIsNone(_visual_beat_starts(timing, item.beat_matches))
        self.assertIsNone(_visual_beat_starts(with_context, item.beat_matches))
        with tempfile.TemporaryDirectory() as directory:
            plain = build_edl(Path(directory) / "plain", [timing], [item], shots)
            contextual = build_edl(Path(directory) / "contextual", [with_context], [item], shots)
        self.assertEqual(contextual, plain)
        self.assertEqual(len(contextual[0].clips), 3)
        self.assertNotAlmostEqual(contextual[0].clips[0].out_time, 5.400831, places=6)

    def test_date_inside_one_word_has_no_independent_onset(self) -> None:
        timing, item, _ = _fixture()
        timing.words[23:25] = [TTSWordTiming(text="场一", start=4.770333, end=5.555690)]
        self.assertIsNone(_visual_beat_starts(timing, item.beat_matches))

    def test_beat_inside_replaced_digits_cannot_invent_a_character_time(self) -> None:
        timing, beats = _alignment_fixture(
            "开场，101号展厅开放。", ["开场", "01号展厅开放"],
            [("开场，", 0.1, 0.4), ("幺", 0.8, 1.0), ("洞幺", 1.0, 1.4), ("号展厅开放。", 1.4, 2.0)],
        )
        beats[1].entities = ["号展厅开放"]
        self.assertIsNone(_visual_beat_starts(timing, beats))

    def test_non_increasing_or_out_of_duration_cuts_return_none(self) -> None:
        for onset in (0.0, 0.001, 2.1):
            with self.subTest(onset=onset):
                timing, beats = _alignment_fixture(
                    "开场，1日开幕。", ["开场", "1日开幕"],
                    [("开场，", 0.1, 0.4), ("一", 0.8, 1.3), ("日开幕。", 1.3, 2.0)],
                )
                timing.words[1] = TTSWordTiming(text="一", start=onset, end=onset + 0.2)
                self.assertIsNone(_visual_beat_starts(timing, beats))

    def test_plain_canonical_word_onset_survives_incomplete_trailing_cues(self) -> None:
        timing, beats = _alignment_fixture(
            "灌阳油茶、现做寿司等特色小吃引来众多品尝者。", ["灌阳油茶", "现做寿司"],
            [
                ("灌", 0.04, 0.32), ("阳", 0.32, 0.59), ("油", 0.59, 0.78),
                ("茶、", 0.78, 0.81), ("现", 1.33, 1.65), ("做", 1.65, 1.82),
                ("寿", 1.82, 2.08), ("司", 2.08, 2.34),
            ],
        )
        beats[1].entities = ["寿司"]
        self.assertEqual(_visual_beat_starts(timing, beats), [0.0, 1.33])


class ContextualOverlayRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_all_overlay_kinds_preserve_existing_multibeat_plan(self) -> None:
        for kind in ("date", "organization", "abstract"):
            with self.subTest(kind=kind):
                timing, item, shots = _fixture()
                item.overlay_kind = kind
                before = item.model_dump(exclude={"overlay_text"})
                plans = [item]
                with tempfile.TemporaryDirectory() as directory:
                    await prepare_information_card_shots(Path(directory), [timing], plans, shots)
                self.assertEqual(plans[0].model_dump(exclude={"overlay_text"}), before)

    async def test_exact_sentence_retains_three_slots_and_zero_planned_freeze(self) -> None:
        timing, item, shots = _fixture()
        original = item.model_dump()
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            before = build_edl(task_dir / "before", [timing], plans, shots)
            ids = await prepare_information_card_shots(task_dir, [timing], plans, shots)
            after = build_edl(task_dir / "after", [timing], plans, shots)
            self.assertEqual(ids, [1])
            self.assertEqual(plans[0].overlay_kind, "date")
            self.assertEqual(plans[0].overlay_text, EXACT_SENTENCE)
            self.assertEqual(plans[0].model_dump(exclude={"overlay_kind", "overlay_text"}), {
                key: value for key, value in original.items()
                if key not in {"overlay_kind", "overlay_text"}
            })
            self.assertEqual(after, before)
            self.assertEqual([clip.shot_id for clip in after[0].clips], [101, 102, 103])
            self.assertTrue(all(clip.freeze_pad is None for clip in after[0].clips))
            self.assertAlmostEqual(sum(clip.out_time - clip.in_time for clip in after[0].clips), 10.800104)
            # Also check repeated preparation cannot collapse or consume slots.
            prepared = plans[0].model_dump()
            await prepare_information_card_shots(task_dir, [timing], plans, shots)
            self.assertEqual(plans[0].model_dump(), prepared)

    async def test_all_multibeat_shots_are_reserved_against_single_overlay(self) -> None:
        timing, item, shots = _fixture()
        other_timing, other = _single_overlay()
        shots.append(_source(104))
        plans = [item, other]
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            ids = await prepare_information_card_shots(task_dir, [timing, other_timing], plans, shots)
            edl = build_edl(task_dir, [timing, other_timing], plans, shots)
            audit = json.loads((task_dir / "overlay_shot_assignment.json").read_text(encoding="utf-8"))

        self.assertEqual(ids, [1, 2])
        self.assertEqual([clip.shot_id for row in edl for clip in row.clips], [101, 102, 103, 104])
        for shot_id in (101, 102, 103):
            self.assertEqual(audit["base_usage"][str(shot_id)], 1)
            self.assertEqual(audit["remaining_capacities"][str(shot_id)], 0)
        preserved = next(row for row in audit["assignments"] if row["sentence_id"] == 1)
        self.assertTrue(preserved["preserved_assignment"])
        self.assertEqual(preserved["preserved_shot_ids"], [101, 102, 103])

    async def test_two_multibeat_overlays_cannot_share_retained_shots(self) -> None:
        timing, item, shots = _fixture()
        other_timing = timing.model_copy(update={"sentence_id": 2})
        other = item.model_copy(deep=True, update={"sentence_id": 2})
        plans = [item, other]
        before = [plan.model_dump() for plan in plans]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RenderingError, "重复镜头"):
                await prepare_information_card_shots(Path(directory), [timing, other_timing], plans, shots)
        self.assertEqual([plan.model_dump() for plan in plans], before)

    async def test_existing_non_overlay_and_sync_usage_cannot_conflict_with_retained_beats(self) -> None:
        for sync in (False, True):
            with self.subTest(sync=sync):
                timing, item, shots = _fixture()
                other = MatchPlanItem(
                    sentence_id=2, text="其他画面。", shot_id=103, confidence=0.8,
                    candidates=[MatchCandidate(shot_id=103, similarity=0.8)],
                )
                if sync:
                    other.sync_sound = SyncSoundSelection(
                        source_index=103, source_media_path="raw/test_103.mp4", shot_id=103,
                        text=other.text, start=0.0, end=2.0, similarity=0.9,
                    )
                plans = [item, other]
                before = [plan.model_dump() for plan in plans]
                with tempfile.TemporaryDirectory() as directory:
                    with self.assertRaisesRegex(RenderingError, "重复镜头"):
                        await prepare_information_card_shots(Path(directory), [timing], plans, shots)
                self.assertEqual([plan.model_dump() for plan in plans], before)

    async def test_multibeat_without_word_alignment_keeps_proportional_fallback(self) -> None:
        timing, item, shots = _fixture()
        timing.words = []
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            before = build_edl(task_dir / "before", [timing], plans, shots)
            await prepare_information_card_shots(task_dir, [timing], plans, shots)
            after = build_edl(task_dir / "after", [timing], plans, shots)
        self.assertEqual(after, before)
        self.assertEqual(len(after[0].clips), 3)
        self.assertTrue(all(clip.freeze_pad is None for clip in after[0].clips))

    async def test_short_retained_source_keeps_freeze_instead_of_consuming_new_shot(self) -> None:
        timing, item, shots = _fixture()
        shots[0].end = shots[0].duration = 1.0
        shots.append(_source(104))
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            before = build_edl(task_dir / "before", [timing], plans, shots)
            await prepare_information_card_shots(task_dir, [timing], plans, shots)
            after = build_edl(task_dir / "after", [timing], plans, shots)
        self.assertEqual(after, before)
        self.assertEqual([clip.shot_id for clip in after[0].clips], [101, 102, 103])
        self.assertGreater(after[0].clips[0].freeze_pad or 0.0, 0.3)

    async def test_preserves_verified_evidence_in_points_and_fallback_without_confidence_boost(self) -> None:
        timing, item, shots = _fixture()
        event = item.beat_matches[2]
        event.confidence = 0.91
        event.is_fallback = False
        event.preferred_in_time = 2.25
        event.candidates[0] = event.candidates[0].model_copy(update={
            "verified_terms": ["金马贺岁"], "verification_confidence": 0.95,
            "verification_evidence": "TEST evidence", "preferred_in_time": 2.25,
        })
        item.confidence = 0.0
        beats_before = [beat.model_dump() for beat in item.beat_matches]
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            await prepare_information_card_shots(task_dir, [timing], plans, shots)
            edl = build_edl(task_dir, [timing], plans, shots)

        self.assertEqual([beat.model_dump() for beat in plans[0].beat_matches], beats_before)
        self.assertEqual(plans[0].confidence, 0.0)
        self.assertTrue(plans[0].is_fallback)
        self.assertEqual([beat.is_fallback for beat in plans[0].beat_matches], [True, True, False])
        self.assertEqual(edl[0].clips[2].in_time, 2.25)
        self.assertTrue(edl[0].clips[2].evidence_aligned)

    async def test_unsafe_multibeat_sources_are_rejected_before_mutation(self) -> None:
        for case in ("missing", "unavailable", "quality", "generated", "generated_path", "legacy_card", "legacy_name", "empty"):
            with self.subTest(case=case):
                timing, item, shots = _fixture()
                if case == "missing":
                    shots.pop()
                elif case == "unavailable":
                    shots[2].status = "unavailable"
                elif case == "quality":
                    shots[2].quality = None
                elif case == "generated":
                    shots[2].media_origin = "generated"
                elif case == "generated_path":
                    shots[2].norm_path = "generated/fill.mp4"
                elif case == "legacy_card":
                    shots[2].description = "新闻结束信息卡"
                elif case == "legacy_name":
                    shots[2].source_name = "generated_information_card_1.mp4"
                else:
                    shots.clear()
                plans = [item]
                before = item.model_dump()
                with tempfile.TemporaryDirectory() as directory:
                    task_dir = Path(directory)
                    with self.assertRaises(RenderingError):
                        await prepare_information_card_shots(task_dir, [timing], plans, shots)
                    self.assertEqual(plans[0].model_dump(), before)
                    self.assertEqual(list(task_dir.iterdir()), [])

    async def test_repeated_multibeat_shot_is_not_hidden_by_collapsing(self) -> None:
        timing, item, shots = _fixture()
        item.beat_matches[1].shot_id = item.beat_matches[0].shot_id
        plans = [item]
        before = item.model_dump()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RenderingError, "重复镜头"):
                await prepare_information_card_shots(Path(directory), [timing], plans, shots)
        self.assertEqual(plans[0].model_dump(), before)

    async def test_insufficient_capacity_does_not_drop_multibeat_slots(self) -> None:
        timing, item, shots = _fixture()
        other_timing, other = _single_overlay()
        plans = [item, other]
        before = [plan.model_dump() for plan in plans]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RenderingError, "一对一分配"):
                await prepare_information_card_shots(Path(directory), [timing, other_timing], plans, shots)
        self.assertEqual([plan.model_dump() for plan in plans], before)

    async def test_single_overlay_uses_contextual_source_and_keeps_beat_fallback(self) -> None:
        timing, item = _single_overlay()
        item.candidates = [MatchCandidate(shot_id=102, similarity=0.2, combined_score=0.2)]
        source = _source(104).model_copy(update={
            "description": "活动现场", "entities": [timing.text],
        })
        generated = _source(105).model_copy(update={
            "media_origin": "generated", "description": "活动现场",
            "entities": [timing.text],
        })
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            await prepare_information_card_shots(Path(directory), [timing], plans, [_source(102), source, generated])
        self.assertEqual(plans[0].shot_id, 104)
        self.assertEqual(plans[0].confidence, 0.0)
        self.assertTrue(plans[0].is_fallback)
        self.assertTrue(plans[0].beat_matches[0].is_fallback)

    async def test_existing_single_beat_keeps_identity_entities_and_same_source_in_point(self) -> None:
        timing, item = _single_overlay()
        item.candidates = [MatchCandidate(shot_id=102, similarity=0.2, combined_score=0.2)]
        item.beat_matches = [BeatMatch(
            beat_id=7, text=item.text, shot_id=102, confidence=0.0, is_fallback=True,
            entities=["活动"], requires_entity_coverage=True, intent_type="entity",
            candidates=item.candidates, preferred_in_time=2.5,
        )]
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            await prepare_information_card_shots(Path(directory), [timing], plans, [_source(102)])
        beat = plans[0].beat_matches[0]
        self.assertEqual(beat.beat_id, 7)
        self.assertEqual(beat.text, item.text)
        self.assertEqual(beat.entities, ["活动"])
        self.assertTrue(beat.requires_entity_coverage)
        self.assertEqual(beat.intent_type, "entity")
        self.assertEqual(beat.preferred_in_time, 2.5)
        self.assertTrue(beat.is_fallback)

    async def test_single_reassignment_does_not_transfer_old_source_in_point(self) -> None:
        timing, item = _single_overlay()
        item.candidates = [MatchCandidate(shot_id=102, similarity=0.2, combined_score=0.2)]
        item.beat_matches = [BeatMatch(
            beat_id=7, text=item.text, shot_id=102, confidence=0.0, is_fallback=True,
            candidates=item.candidates, preferred_in_time=2.5,
        )]
        source = _source(104).model_copy(update={"entities": [timing.text]})
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            await prepare_information_card_shots(Path(directory), [timing], plans, [_source(102), source])
        self.assertEqual(plans[0].shot_id, 104)
        self.assertIsNone(plans[0].beat_matches[0].preferred_in_time)
        self.assertTrue(plans[0].beat_matches[0].is_fallback)

    async def test_single_beat_fallback_cannot_be_cleared_by_parent_summary(self) -> None:
        timing, item = _single_overlay()
        item.is_fallback = False
        item.confidence = 0.8
        item.beat_matches = [BeatMatch(
            beat_id=7, text=item.text, shot_id=item.shot_id, confidence=0.19,
            is_fallback=True, candidates=item.candidates, intent_type="date",
        )]
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            await prepare_information_card_shots(Path(directory), [timing], plans, [_source(102)])
        self.assertTrue(plans[0].is_fallback)
        self.assertTrue(plans[0].beat_matches[0].is_fallback)
        self.assertLessEqual(plans[0].confidence, 0.19)

    async def test_verified_parent_candidate_remains_protected_when_beat_has_no_copy(self) -> None:
        timing, item = _single_overlay()
        item.candidates = [MatchCandidate(
            shot_id=102, similarity=0.2, combined_score=0.2,
            verified_terms=["主办展板"], verification_confidence=0.95,
            verification_evidence="TEST parent-only evidence",
        )]
        item.beat_matches = [BeatMatch(
            beat_id=7, text=item.text, shot_id=102, confidence=0.0, is_fallback=True,
            candidates=[MatchCandidate(shot_id=102, similarity=0.2, combined_score=0.2)],
        )]
        source = _source(104).model_copy(update={"entities": [timing.text]})
        plans = [item]
        before = item.model_dump(exclude={"overlay_kind", "overlay_text"})
        with tempfile.TemporaryDirectory() as directory:
            await prepare_information_card_shots(Path(directory), [timing], plans, [_source(102), source])
        self.assertEqual(plans[0].model_dump(exclude={"overlay_kind", "overlay_text"}), before)

    async def test_unaccepted_verification_does_not_change_baseline_contextual_selection(self) -> None:
        timing, item = _single_overlay()
        item.candidates = [MatchCandidate(
            shot_id=102, similarity=0.2, combined_score=0.2,
            verified_terms=["主办展板"], verification_confidence=0.74,
        )]
        source = _source(104).model_copy(update={"entities": [timing.text]})
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            await prepare_information_card_shots(Path(directory), [timing], plans, [_source(102), source])
        self.assertEqual(plans[0].shot_id, 104)
        self.assertEqual(plans[0].confidence, 0.0)

    async def test_verified_single_source_is_not_replaced_by_contextual_utility(self) -> None:
        timing, item = _single_overlay()
        item.candidates = [MatchCandidate(
            shot_id=102, similarity=0.2, combined_score=0.2,
            verified_terms=["主办展板"], verification_confidence=0.95,
            verification_evidence="TEST direct evidence", preferred_in_time=2.0,
        )]
        stronger_context = _source(104).model_copy(update={"entities": [timing.text]})
        before = item.model_dump(exclude={"overlay_kind", "overlay_text"})
        plans = [item]
        with tempfile.TemporaryDirectory() as directory:
            await prepare_information_card_shots(Path(directory), [timing], plans, [_source(102), stronger_context])
        self.assertEqual(plans[0].model_dump(exclude={"overlay_kind", "overlay_text"}), before)

    async def test_sync_sound_is_untouched_and_only_reserves_its_effective_shot(self) -> None:
        timing, item, shots = _fixture()
        item.sync_sound = SyncSoundSelection(
            source_index=103, source_media_path="raw/test_103.mp4", shot_id=103,
            text=timing.text, start=0.0, end=8.067, similarity=0.9,
        )
        other_timing, other = _single_overlay()
        other.shot_id = 103
        other.candidates = [MatchCandidate(shot_id=103, similarity=0.9, combined_score=0.9)]
        plans = [item, other]
        before = item.model_dump()
        with tempfile.TemporaryDirectory() as directory:
            ids = await prepare_information_card_shots(Path(directory), [timing, other_timing], plans, shots)
        self.assertEqual(ids, [2])
        self.assertEqual(plans[0].model_dump(), before)
        self.assertNotEqual(plans[1].shot_id, 103)


if __name__ == "__main__":
    unittest.main()