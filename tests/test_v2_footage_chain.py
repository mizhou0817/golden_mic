"""Narration longer than the footage: chains of real windows, rendered by the real encoder (no freeze, no invented frames)."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import mode_pipeline as mp, rendering
from backend.models import AnnotatedShot, BeatMatch, EDLItem, MatchCandidate, MatchPlanItem, SentenceTiming, VisionQuality
from tests.test_mode_exports import media


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FootageChainTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-footage-chain-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # A 3 s clip: shorter than any of the sentences below, which is the whole point.
        media("-y", "-f", "lavfi", "-i", "testsrc2=size=256x144:rate=30:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", self.root / "clip.mp4")
        self.shot = lambda shot_id, start, end: AnnotatedShot(
            shot_id=shot_id, source_index=0, source_scene_index=shot_id, source_name="clip.mp4", norm_path="clip.mp4",
            start=start, end=end, duration=end - start, status="available", description="测试图案",
            quality=VisionQuality(sharp=0.7, bright=0.7))
        self.item = lambda sentence_id, shot_id: MatchPlanItem(
            sentence_id=sentence_id, text="一句话", kind="narration", shot_id=shot_id, confidence=0.9,
            candidates=[MatchCandidate(shot_id=shot_id, similarity=0.5)],
            beat_matches=[BeatMatch(beat_id=0, text="一句话", shot_id=shot_id, confidence=0.9,
                                    candidates=[MatchCandidate(shot_id=shot_id, similarity=0.5)])])

    def timings(self, durations):
        cursor, result = 0.0, []
        for index, duration in enumerate(durations):
            result.append(SentenceTiming(sentence_id=index, text="一句话", audio_path=f"u{index}.wav", duration=duration,
                                         start=cursor, end=cursor + duration, gap_after=0.12))
            cursor += duration + 0.12
        return result

    def edl_for(self, plan, shots, chains, durations):
        manifest = {"media_contract": mp.V2_MEDIA_CONTRACT, "mode": "voiceover", "broll_shot_ids": [s.shot_id for s in shots],
                    "source_clocks": {"up": {"source_index": 0, "norm_path": "clip.mp4", "norm_source_offset": 0.0}}, "shot_chains": chains}
        return mp.build_mode_edl(plan, self.timings(durations), shots, [], manifest, mp.EditingPreferences())

    async def test_chain_covers_each_sentence_with_distinct_real_windows_and_the_encoder_renders_it(self):
        # Three 5.5 s sentences on a 3 s clip: 3 s + 2.62 s each, wrapping around the same footage.
        shots = [self.shot(10, 0.0, 3.0), self.shot(11, 0.0, 2.62), self.shot(12, 0.0, 3.0), self.shot(13, 0.0, 2.62),
                 self.shot(14, 0.0, 3.0), self.shot(15, 0.0, 2.62)]
        chains = {"0": [10, 11], "1": [12, 13], "2": [14, 15]}
        durations = [5.5, 5.5, 5.5]
        edl, options = self.edl_for([self.item(0, 10), self.item(1, 12), self.item(2, 14)], shots, chains, durations)
        ids = [clip.shot_id for item in edl for clip in item.clips]
        self.assertEqual(ids, [10, 11, 12, 13, 14, 15]); self.assertEqual(len(ids), len(set(ids)))
        for item, duration in zip(edl, durations):
            total = sum(clip.out_time - clip.in_time for clip in item.clips)
            self.assertAlmostEqual(total, duration + 0.12, places=3, msg="the sentence is covered exactly")
            self.assertTrue(all(not clip.freeze_pad for clip in item.clips), "no frozen padding")
        self.assertTrue(set(ids) <= set(options), "every window has render options")
        with patch.object(rendering, "_mix_subtitles_and_narration", new=AsyncMock()), patch.object(rendering, "validate_final_video", new=AsyncMock()):
            result = await rendering.render_mode_video(self.root, edl, lambda *_: None, clip_options=options,
                                                       source_hashes={"clip.mp4": digest(self.root / "clip.mp4")}, finish_options=rendering.FinishOptions())
        frames = [value["frames"] for value in result["frames"]]
        self.assertEqual(len(frames), 6, "one rendered clip per window")
        self.assertTrue(all(count > 0 for count in frames))
        self.assertAlmostEqual(sum(frames) / 30, 3 * (5.5 + 0.12), delta=1 / 30 + 1e-6, msg="the picture lasts exactly as long as the narration")
        self.assertTrue((self.root / "video_only.mp4").is_file() and (self.root / "video_only.mp4").stat().st_size > 0)
        self.assertEqual({value["actual_source_in"] for value in result["frames"]}, {0.0}, "every window restarts the real footage; nothing is frozen")

    async def test_a_chain_is_ignored_once_the_sentence_has_a_different_shot_and_a_too_short_single_shot_still_stops(self):
        shots = [self.shot(10, 0.0, 3.0), self.shot(11, 0.0, 2.62), self.shot(20, 0.0, 3.0)]
        # Sentence 0 was re-assigned to shot 20 (e.g. "换画面"): its old chain must not be used.
        with self.assertRaises(mp.MatchingError):
            self.edl_for([self.item(0, 20)], shots, {"0": [10, 11]}, [5.5])
        edl, _ = self.edl_for([self.item(0, 20)], shots, {"0": [10, 11]}, [2.0])
        self.assertEqual([clip.shot_id for clip in edl[0].clips], [20])

    async def test_a_chain_that_is_too_short_for_the_real_audio_is_reported_not_faked(self):
        shots = [self.shot(10, 0.0, 3.0), self.shot(11, 0.0, 1.0)]
        with self.assertRaises(mp.MatchingError) as caught:
            self.edl_for([self.item(0, 10)], shots, {"0": [10, 11]}, [9.0])
        self.assertIn("画面仍差", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

    async def extend(self, plan, shots, durations, context=None):
        manifest = {"media_contract": mp.V2_MEDIA_CONTRACT, "mode": "voiceover", "broll_shot_ids": [s.shot_id for s in shots],
                    "source_clocks": {"up": {"source_index": 0, "norm_path": "clip.mp4", "norm_source_offset": 0.0}},
                    "next_shot_id": 100, "quote_shot_registry": {}}
        record = type("Record", (), {"task_dir": self.root, "draft_context": context})()

        async def thumb(root, shot):
            path = root / "thumbs" / f"shot_{shot.shot_id}.jpg"
            path.parent.mkdir(exist_ok=True); path.write_bytes(b"jpg")
            return path

        with patch.object(mp, "extract_shot_thumbnail", new=thumb):
            count = await mp._extend_short_coverage(record, plan, self.timings(durations), shots, manifest)
        return count, manifest

    async def test_narration_that_outgrows_its_shot_continues_on_the_same_real_footage(self):
        # An edit made sentence 0 longer (5.5 s) than its 3 s shot; sentence 1 still fits.
        shots = [self.shot(20, 0.0, 3.0), self.shot(21, 0.0, 3.0)]
        plan = [self.item(0, 20), self.item(1, 21)]
        count, manifest = await self.extend(plan, shots, [5.5, 2.0])
        self.assertEqual(count, 1)
        chain = manifest["shot_chains"]["0"]
        self.assertEqual(chain[0], 20)
        self.assertNotIn("1", manifest["shot_chains"], "a sentence that fits is left alone")
        edl = mp.build_mode_edl(plan, self.timings([5.5, 2.0]), shots, [], manifest, mp.EditingPreferences())[0]
        ids = [clip.shot_id for item in edl for clip in item.clips]
        self.assertEqual(len(ids), len(set(ids)), "every window keeps its own shot id")
        self.assertAlmostEqual(sum(c.out_time - c.in_time for c in edl[0].clips), 5.5 + 0.12, places=3)
        self.assertTrue(all(not clip.freeze_pad for item in edl for clip in item.clips))

    async def test_declined_reuse_is_not_extended_and_the_shortage_is_still_reported(self):
        shots = [self.shot(20, 0.0, 3.0)]
        plan = [self.item(0, 20)]
        count, manifest = await self.extend(plan, shots, [5.5], context={"shot_reuse": {"accepted": False}})
        self.assertEqual(count, 0)
        with self.assertRaises(mp.MatchingError):
            mp.build_mode_edl(plan, self.timings([5.5]), shots, [], manifest, mp.EditingPreferences())
