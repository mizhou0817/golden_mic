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
        self.assertEqual(set(mp.BROLL_FALLBACK_TEXT), {"no_speech", "any", "quote_overlap"})


if __name__ == "__main__":
    unittest.main()
