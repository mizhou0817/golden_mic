"""Global one-shot-per-beat assignment must not fail while enough distinct shots exist."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.matching import MatchingError, ShotShortageError, _BeatAssignment, _enforce_global_unique_shots
from backend.models import BeatMatch, MatchCandidate


def assignment(sentence_id: int, pool: list[int], *, chosen: int | None = None, entity: bool = False) -> _BeatAssignment:
    candidates = [MatchCandidate(shot_id=shot, similarity=0.9 - index * 0.1, combined_score=0.8 - index * 0.1,
                                 matched_terms=["市长"] if entity else [])
                  for index, shot in enumerate(pool)]
    return _BeatAssignment(sentence_id, sentence_id, 0.9, BeatMatch(
        beat_id=sentence_id, text=f"第 {sentence_id} 句", requires_entity_coverage=entity,
        shot_id=pool[0] if chosen is None else chosen, confidence=0.9, candidates=candidates))


def shots(count: int, duration: float = 20.0):
    return [SimpleNamespace(shot_id=index, duration=duration) for index in range(count)]


class GlobalUniqueShotTests(unittest.TestCase):
    def test_overlapping_candidate_lists_are_widened_instead_of_failing(self):
        # Both beats only list shot 0; strictly impossible, but three distinct shots exist.
        items = [assignment(0, [0]), assignment(1, [0]), assignment(2, [1, 2])]
        result = _enforce_global_unique_shots(items, shots(3))
        chosen = [item.match.shot_id for item in result]
        self.assertEqual(len(set(chosen)), 3, chosen)
        filler = [item for item in result if item.match.is_fallback]
        self.assertEqual(len(filler), 1, "exactly the beat that had to borrow a shot is flagged")
        self.assertLessEqual(filler[0].match.confidence, 0.34)
        self.assertIn(filler[0].match.shot_id, {candidate.shot_id for candidate in filler[0].match.candidates})
        self.assertEqual([item.match.is_fallback for item in result if not item.match.is_fallback], [False, False])

    def test_already_distinct_assignments_are_untouched(self):
        items = [assignment(0, [0, 1]), assignment(1, [1, 2]), assignment(2, [2, 0])]
        before = [item.match.model_dump() for item in items]
        result = _enforce_global_unique_shots(items, shots(3))
        self.assertEqual([item.match.model_dump() for item in result], before)

    def test_real_shortage_is_typed_and_says_the_numbers(self):
        items = [assignment(0, [0]), assignment(1, [0]), assignment(2, [1])]
        with self.assertRaises(ShotShortageError) as caught:
            _enforce_global_unique_shots(items, shots(2))
        self.assertEqual(caught.exception.error_kind, "shortage")
        self.assertIsInstance(caught.exception, MatchingError)
        self.assertIn("需要 3 个", str(caught.exception))
        self.assertIn("只有 2 个", str(caught.exception))
        self.assertIn("不足", str(caught.exception))

    def test_entity_bound_beats_are_never_filled_with_unrelated_footage(self):
        items = [assignment(0, [0], entity=True), assignment(1, [0], entity=True), assignment(2, [1, 2])]
        with self.assertRaises(ShotShortageError) as caught:
            _enforce_global_unique_shots(items, shots(3))
        self.assertIn("一对一全局分配", str(caught.exception))
        self.assertNotIn("只有 3 个可用镜头", str(caught.exception), "shots were sufficient; do not claim a count shortage")


class ConsentedReuseTests(unittest.TestCase):
    def test_entity_bound_beats_may_share_a_related_shot_only_with_consent(self):
        def items():
            return [assignment(0, [0], entity=True), assignment(1, [0], entity=True), assignment(2, [1, 2])]
        with self.assertRaises(ShotShortageError):
            _enforce_global_unique_shots(items(), shots(3))
        result = _enforce_global_unique_shots(items(), shots(3, duration=20.0), reuse_window_seconds=6.0)
        chosen = [item.match.shot_id for item in result]
        self.assertEqual(chosen[:2], [0, 0], "the related shot is reused, never replaced by unrelated footage")
        self.assertFalse(any(item.match.is_fallback for item in result[:2]))

    def test_a_shot_is_reused_no_more_often_than_it_has_windows(self):
        # Shot 0 is 10 s: two 4 s windows at most; three entity-bound beats cannot all use it.
        items = [assignment(index, [0], entity=True) for index in range(3)]
        with self.assertRaises(ShotShortageError) as caught:
            _enforce_global_unique_shots(items, shots(1, duration=10.0), reuse_window_seconds=4.0)
        self.assertIn("即使允许重复使用", str(caught.exception))
        self.assertEqual(caught.exception.error_kind, "shortage")
        items = [assignment(index, [0], entity=True) for index in range(2)]
        result = _enforce_global_unique_shots(items, shots(1, duration=10.0), reuse_window_seconds=4.0)
        self.assertEqual([item.match.shot_id for item in result], [0, 0])

    def test_unique_assignment_is_still_preferred_when_it_exists(self):
        items = [assignment(0, [0, 1]), assignment(1, [0, 2]), assignment(2, [2, 1])]
        result = _enforce_global_unique_shots(items, shots(3), reuse_window_seconds=4.0)
        self.assertEqual(len({item.match.shot_id for item in result}), 3)


class BestEffortMatchingTests(unittest.TestCase):
    def test_best_effort_always_finds_an_assignment_and_prefers_related_footage(self):
        items = lambda: [assignment(index, [0], entity=True) for index in range(5)]
        with self.assertRaises(ShotShortageError):
            _enforce_global_unique_shots(items(), shots(3, duration=1.0), reuse_window_seconds=4.0)  # windows impossible
        result = _enforce_global_unique_shots(items(), shots(3, duration=1.0), reuse_window_seconds=4.0, best_effort=True)
        self.assertEqual([item.match.shot_id for item in result], [0] * 5, "related shot first, however often it is needed")
        self.assertFalse(any(item.match.is_fallback for item in result))

    def test_best_effort_borrows_other_shots_for_beats_with_no_related_footage_and_flags_them(self):
        entity = [assignment(index, [0], entity=True) for index in range(3)]
        plain = [assignment(10 + index, [1]) for index in range(3)]
        result = _enforce_global_unique_shots(entity + plain, shots(4, duration=1.0), reuse_window_seconds=4.0, best_effort=True)
        self.assertEqual(len(result), 6); self.assertTrue(all(0 <= item.match.shot_id < 4 for item in result))
        self.assertTrue(all(item.match.shot_id == 0 for item in result[:3]))
        # Beats with their own listed shot keep it: related footage wins over borrowed footage.
        self.assertTrue(all(item.match.shot_id == 1 and not item.match.is_fallback for item in result[3:]))

    def test_best_effort_prefers_a_beats_own_candidate_over_unrelated_footage_when_entity_wording_misses(self):
        # The real 腊肉腊肠 case: the right clip ranks first semantically, yet only a weaker clip happens to
        # share a literal term. Entity-bound beats with no literal match keep their own ranking.
        def beat(sentence_id, ranked, literal=()):
            candidates = [MatchCandidate(shot_id=shot, similarity=0.6 - index * 0.1, combined_score=0.5 - index * 0.1,
                                         matched_terms=["年货"] if shot in literal else [])
                          for index, shot in enumerate(ranked)]
            return _BeatAssignment(sentence_id, sentence_id, 0.5, BeatMatch(
                beat_id=sentence_id, text="x", requires_entity_coverage=True, shot_id=ranked[0],
                confidence=0.5, candidates=candidates))
        items = [beat(0, [3, 4, 0]), beat(1, [4, 3, 0]), beat(2, [5, 6]), beat(3, [6, 5]), beat(4, [0, 3], literal=(0,))]
        result = _enforce_global_unique_shots(items, shots(7, duration=1.0), reuse_window_seconds=4.0, best_effort=True)
        self.assertEqual([item.match.shot_id for item in result], [3, 4, 5, 6, 0], "each beat keeps its best own clip")

    def test_retrieval_terms_split_on_connector_words(self):
        from backend.matching import extract_retrieval_terms
        self.assertIn("腊肠", extract_retrieval_terms("腊肉腊肠等传统年货"))
        self.assertIn("寿司", extract_retrieval_terms("现做寿司的摊位前同样人气十足"))
        self.assertIn("汽车", extract_retrieval_terms("新能源汽车在现场集中亮相"))
        self.assertEqual(extract_retrieval_terms("灌阳油茶"), ["灌阳油茶", "油茶"])

    def test_best_effort_off_keeps_every_earlier_rule(self):
        items = [assignment(index, [0], entity=True) for index in range(2)]
        with self.assertRaises(ShotShortageError):
            _enforce_global_unique_shots(items, shots(2, duration=1.0))


class AllocateReusedFootageTests(unittest.TestCase):
    PREFS = SimpleNamespace(target_chars_per_minute=255)

    def setUp(self):
        from backend import mode_pipeline
        from backend.models import AnnotatedShot, MatchPlanItem
        self.mp = mode_pipeline
        temporary = tempfile.TemporaryDirectory(prefix="gm-reuse-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.record = SimpleNamespace(task_dir=self.root)
        self.shot = lambda shot_id, start, end: AnnotatedShot(
            shot_id=shot_id, source_index=0, source_scene_index=shot_id, source_name="clip.mp4",
            norm_path="norm/clip.mp4", start=start, end=end, duration=end - start, status="available", description="航拍村庄")
        self.item = lambda sentence_id, shot_id: MatchPlanItem(
            sentence_id=sentence_id, text="一句话", kind="narration", shot_id=shot_id, confidence=0.9,
            candidates=[], beat_matches=[BeatMatch(beat_id=0, text="一句话", shot_id=shot_id, confidence=0.9,
                                                    candidates=[MatchCandidate(shot_id=shot_id, similarity=0.5)])])
        self.manifest = {"source_clocks": {"up_a": {"source_index": 0, "norm_source_offset": 2.0}},
                         "broll_shot_ids": [1, 2], "next_shot_id": 100}

    def allocate(self, plan, shots, text="字" * 20):
        async def thumbnail(task_dir, shot):
            path = task_dir / "thumbs" / f"shot_{shot.shot_id}.jpg"
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b"jpg")
            return path
        texts = {item.sentence_id: text for item in plan}
        with patch.object(self.mp, "extract_shot_thumbnail", thumbnail):
            return asyncio.run(self.mp._allocate_reused_footage(self.record, plan, shots, self.manifest, texts, self.PREFS))

    def test_enough_footage_is_cut_proportionally_one_window_per_sentence(self):
        # 20 characters need about 0.25 s x 1.8 + 1.2 of picture each; a 30 s shot holds three of them easily.
        shots = [self.shot(1, 0.0, 30.0), self.shot(2, 0.0, 30.0)]
        plan = [self.item(0, 1), self.item(1, 1), self.item(2, 1), self.item(3, 2)]
        result, report = self.allocate(plan, shots)
        self.assertEqual(report["chained_sentences"], 0)
        ids = [item.shot_id for item in plan]
        self.assertEqual(len(set(ids)), 4, ids)
        self.assertEqual(ids[3], 2, "a shot used once and long enough keeps its id")
        windows = sorted((shot.start, shot.end) for shot in result if shot.shot_id in ids[:3])
        self.assertAlmostEqual(windows[0][0], 0.0); self.assertAlmostEqual(windows[-1][1], 30.0)
        for left, right in zip(windows, windows[1:]):
            self.assertAlmostEqual(left[1], right[0], msg="windows tile the footage without overlap or gap")
        self.assertNotIn(1, {shot.shot_id for shot in result}); self.assertNotIn(1, self.manifest["broll_shot_ids"])
        self.assertNotIn("shot_chains", {key for key, value in self.manifest.items() if value == {} and key != "shot_chains"})
        self.assertEqual(self.mp._physical_shot_id(self.manifest, "up_a", 2.0, 2.0 + windows[0][1] - windows[0][0]), ids[0])

    def test_not_enough_footage_wraps_around_with_a_chain_of_real_windows_and_distinct_ids(self):
        shots = [self.shot(1, 0.0, 3.0)]  # a 3 s photo-like clip shared by six sentences
        plan = [self.item(index, 1) for index in range(6)]
        result, report = self.allocate(plan, shots)
        chains = self.manifest["shot_chains"]
        need = self.mp._estimated_footage_need("字" * 20, self.PREFS)
        everything = [identifier for item in plan for identifier in (chains.get(str(item.sentence_id)) or [item.shot_id])]
        self.assertEqual(len(everything), len(set(everything)), "every window has its own shot id")
        by_id = {shot.shot_id: shot for shot in result}
        for item in plan:
            ids = chains.get(str(item.sentence_id)) or [item.shot_id]
            self.assertEqual(ids[0], item.shot_id)
            self.assertGreaterEqual(sum(by_id[i].duration for i in ids), need - 0.2, "each sentence gets its estimated picture time")
            for i in ids:
                self.assertGreaterEqual(by_id[i].start, 0.0); self.assertLessEqual(by_id[i].end, 3.0 + 1e-9)
                self.assertGreater(by_id[i].duration, 0.0)
        self.assertGreater(report["chained_sentences"], 0); self.assertGreater(report["windows"], 6)
        self.assertEqual(sorted(self.manifest["broll_shot_ids"]), sorted([2] + [shot.shot_id for shot in result if shot.shot_id != 2]))

    def test_one_short_shot_used_once_is_chained_with_itself(self):
        plan = [self.item(0, 1)]
        result, report = self.allocate(plan, [self.shot(1, 0.0, 2.0)], text="字" * 60)
        ids = self.manifest["shot_chains"]["0"]
        self.assertGreater(len(ids), 1); self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(plan[0].shot_id, ids[0]); self.assertEqual([beat.shot_id for beat in plan[0].beat_matches], [ids[0]])

    def test_nothing_changes_without_repeats(self):
        shots = [self.shot(1, 0.0, 18.0), self.shot(2, 0.0, 9.0)]
        plan = [self.item(0, 1), self.item(1, 2)]
        result, report = self.allocate(plan, shots, text="字" * 10)
        self.assertEqual(report, {"repeated_shots": 0, "windows": 0, "chained_sentences": 0})
        self.assertEqual([shot.shot_id for shot in result], [1, 2]); self.assertEqual([item.shot_id for item in plan], [1, 2])
        self.assertNotIn("shot_chains", self.manifest)

    def test_repeated_footage_is_allowed_by_default_and_only_an_explicit_no_turns_it_off(self):
        accepted = lambda context: self.mp._shot_reuse_accepted(SimpleNamespace(draft_context=context))
        # On by default so a shortage never stalls production; only an explicit "no" keeps strict uniqueness.
        for context in ({"shot_reuse": {"accepted": True}}, {}, {"shot_reuse": {}}, {"shot_reuse": {"accepted": "yes"}}, {"shot_reuse": True}, None):
            self.assertTrue(accepted(context), context)
        self.assertFalse(accepted({"shot_reuse": {"accepted": False}}))
        self.assertTrue(self.mp._shot_reuse_accepted(SimpleNamespace()))
        self.assertTrue(self.mp.REUSE_MIN_WINDOW_SECONDS <= self.mp._reuse_window_seconds([], SimpleNamespace(target_chars_per_minute=255)))

    def test_distinct_variants_only_for_deliberately_repeated_ranges(self):
        first = self.mp._physical_shot_id(self.manifest, "up_a", 2.0, 5.0)
        again = self.mp._physical_shot_id(self.manifest, "up_a", 2.0, 5.0)
        repeat = self.mp._physical_shot_id(self.manifest, "up_a", 2.0, 5.0, 1)
        self.assertEqual(first, again); self.assertNotEqual(first, repeat)
        self.assertEqual(repeat, self.mp._physical_shot_id(self.manifest, "up_a", 2.0, 5.0, 1))

    def test_retry_safe_receipt_is_never_a_cache_hit_and_only_replaces_a_started_one(self):
        cache = self.mp.ModeStageCache(self.root)
        cache.mark_retry_safe(6)  # no receipt: nothing to mark
        self.assertIsNone(cache.load(6, "sig"))
        cache.begin(6, "sig")
        cache.mark_retry_safe(6)
        self.assertIsNone(cache.load(6, "sig"))
        cache.save(6, "sig", {"x": 1}, [])
        cache.mark_retry_safe(6)  # a complete receipt must stay complete
        self.assertEqual(cache.load(6, "sig"), {"x": 1})


if __name__ == "__main__":
    unittest.main()
