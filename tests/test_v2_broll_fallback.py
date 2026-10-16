"""旁白 + 原声 with noisy footage: B-roll must not vanish just because nothing is measurably quiet."""
from __future__ import annotations

import unittest

from backend import mode_pipeline as mp
from backend.models import Shot


def clip(segments, *, has_speech=True, silences=None):
    return ({"id": "up", "sec": 20.0, "has_speech": has_speech, "silences": silences or [],
             "transcript": [{"id": f"s{i}", "start": a, "end": b, "speaker_id": "S1", "text": "现场采访"} for i, (a, b) in enumerate(segments)]},
            {"prepared_start": 0.0, "prepared_end": 20.0, "norm_source_offset": 0.0, "source_index": 0})


class BrollFallbackTests(unittest.TestCase):
    def test_noisy_footage_has_no_strict_broll_but_the_fallback_tiers_find_picture(self):
        snapshot, clock = clip([(5.0, 8.0)])  # someone speaks 5-8 s; market noise everywhere else
        self.assertEqual(mp._broll_intervals(snapshot, clock, "mixed", "quiet"), [], "the old rule: nothing quiet, nothing usable")
        self.assertEqual(mp._broll_intervals(snapshot, clock, "mixed", "no_speech"), [(0.0, 4.7), (8.3, 20.0)])
        self.assertEqual(mp._broll_intervals(snapshot, clock, "mixed", "any"), [(0.0, 20.0)])

    def test_voiceover_mode_is_unchanged(self):
        snapshot, clock = clip([(5.0, 8.0)])
        for tier in mp.BROLL_TIERS:
            self.assertEqual(mp._broll_intervals(snapshot, clock, "voiceover", tier), [(0.0, 20.0)])

    def test_pool_shots_follow_the_tier(self):
        snapshot, clock = clip([(0.0, 20.0)])  # speech end to end, like a long interview
        shot = Shot(shot_id=0, source_index=0, source_scene_index=0, source_name="a.mp4", norm_path="norm/norm_0.mp4",
                    start=0.0, end=20.0, duration=20.0)
        clocks = {"up": clock}
        self.assertEqual(mp._pool_shots([shot], [snapshot], clocks, "mixed", "quiet"), [])
        self.assertEqual(mp._pool_shots([shot], [snapshot], clocks, "mixed", "no_speech"), [])
        self.assertEqual([s.duration for s in mp._pool_shots([shot], [snapshot], clocks, "mixed", "any")], [20.0])
        self.assertEqual(set(mp.BROLL_FALLBACK_TEXT), {"no_speech", "any", "quote_overlap", "gap_fill"})


class QuoteGapFillTests(unittest.TestCase):
    """只用原声: a quote ending at its file's end still needs real picture for the short pause after it."""
    def shot(self, shot_id, source, start, end):
        from backend.models import AnnotatedShot, VisionQuality
        return AnnotatedShot(shot_id=shot_id, source_index=source, source_scene_index=0, source_name=f"{source}.mp4",
                             norm_path=f"norm/norm_{source}.mp4", start=start, end=end, duration=end - start,
                             status="available", description="画面", quality=VisionQuality(sharp=0.7, bright=0.7))

    def manifest(self):
        return {"source_clocks": {"a": {"source_index": 0, "prepared_start": 0.0, "norm_source_offset": 0.0},
                                  "b": {"source_index": 1, "prepared_start": 0.0, "norm_source_offset": 0.0}}}

    def test_used_broll_is_reused_first(self):
        quote, broll = self.shot(1, 0, 5.0, 9.0), self.shot(2, 1, 0.0, 4.0)
        chosen, start = mp._gap_fill([broll], {1: quote, 2: broll}, quote, 5.0, 0.138, self.manifest())
        self.assertEqual((chosen.shot_id, start), (2, 0.0))

    def test_without_broll_the_same_file_just_before_the_quote_fills_the_pause(self):
        quote = self.shot(1, 0, 5.0, 9.0)
        chosen, start = mp._gap_fill([], {1: quote}, quote, 5.0, 0.138, self.manifest())
        self.assertIs(chosen, quote)
        self.assertLess(start + 0.138, 5.0 + 1e-9, "real frames before the quote, not after the file's end")
        self.assertGreaterEqual(start, 0.0)

    def test_a_quote_at_the_very_start_borrows_another_files_footage(self):
        quote, other = self.shot(1, 0, 0.0, 3.0), self.shot(3, 1, 0.0, 6.0)
        chosen, _ = mp._gap_fill([], {1: quote, 3: other}, quote, 0.0, 0.138, self.manifest())
        self.assertEqual(chosen.shot_id, 3)

    def test_nothing_at_all_still_reports_honestly(self):
        quote = self.shot(1, 0, 0.0, 0.1)
        self.assertEqual(mp._gap_fill([], {1: quote}, quote, 0.0, 0.5, self.manifest()), (None, 0.0))


class RepeatedFootageIdTests(unittest.TestCase):
    def test_identical_lines_get_distinct_ids_and_a_rerender_reuses_them(self):
        from backend.models import AnnotatedShot, EDLClip, EDLItem, VisionQuality
        from backend.rendering import SegmentRenderOptions, _validate_unique_edl_shots
        shot = AnnotatedShot(shot_id=4, source_index=0, source_scene_index=0, source_name="a.mp4", norm_path="norm/norm_0.mp4",
                             start=4.77, end=4.93, duration=0.16, status="available", description="人", quality=VisionQuality(sharp=0.7, bright=0.7))
        manifest = {"source_clocks": {"a": {"source_index": 0, "norm_source_offset": 0.0}}, "next_shot_id": 50, "quote_shot_registry": {}}

        def build():
            return [EDLItem(sentence_id=i, clips=[EDLClip(shot_id=4, src="norm/norm_0.mp4", in_time=4.77, out_time=4.93)],
                            timeline_start=i * 0.2, timeline_end=i * 0.2 + 0.16) for i in range(3)]  # three "人" lines

        shots = [shot]
        for _ in range(2):  # a later edit re-renders the same plan
            edl, options, by_id = build(), {4: SegmentRenderOptions()}, {s.shot_id: s for s in shots}
            mp._distinct_repeat_ids(edl, shots, by_id, options, manifest)
            ids = [clip.shot_id for item in edl for clip in item.clips]
            self.assertEqual(len(set(ids)), 3)
            _validate_unique_edl_shots(edl)  # the renderer's rule now holds
            self.assertTrue(all(clip.in_time == 4.77 and clip.out_time == 4.93 for item in edl for clip in item.clips), "same real frames")
        self.assertEqual(len(shots), 3, "the copies from the first render are reused, not duplicated again")


if __name__ == "__main__":
    unittest.main()
