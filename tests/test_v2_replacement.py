"""换画面 when every clip already serves a sentence: reuse real footage instead of refusing outright."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend import mode_pipeline as mp, v2_editing, workbench as wb
from backend.models import AnnotatedShot, EDLClip, EDLItem, MatchCandidate, MatchPlanItem, SentenceTiming, VisionQuality


def shot(shot_id: int) -> AnnotatedShot:
    return AnnotatedShot(shot_id=shot_id, source_index=shot_id, source_scene_index=0, source_name=f"{shot_id}.mp4",
                         norm_path=f"norm/norm_{shot_id}.mp4", start=0.0, end=8.0, duration=8.0, status="available",
                         description=f"画面 {shot_id}", quality=VisionQuality(sharp=0.7, bright=0.7))


def item(sentence_id: int, shot_id: int) -> MatchPlanItem:
    return MatchPlanItem(sentence_id=sentence_id, text=f"第{sentence_id}句", kind="narration", shot_id=shot_id, confidence=0.8,
                         candidates=[MatchCandidate(shot_id=shot_id, similarity=0.8)])


class ReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gm-replace-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.shots = [shot(index) for index in range(3)]
        self.plan = [item(index, index) for index in range(3)]  # every clip already used
        edl = [EDLItem(sentence_id=index, clips=[EDLClip(shot_id=index, src=f"norm/norm_{index}.mp4", in_time=0, out_time=3)],
                       timeline_start=index * 3, timeline_end=index * 3 + 3) for index in range(3)]
        timings = [SentenceTiming(sentence_id=index, text="x", audio_path=f"u{index}.wav", duration=3, start=index * 3, end=index * 3 + 3)
                   for index in range(3)]
        self.enterContext(patch.object(wb, "_current", lambda root: (timings, list(self.plan), list(self.shots), edl)))
        self.enterContext(patch.object(wb, "_shot_ok", lambda root, value: True))
        self.enterContext(patch.object(v2_editing, "read_json", lambda root, name: {"broll_shot_ids": [0, 1, 2]}))
        self.render = self.enterContext(patch.object(mp, "_render_mode", new=AsyncMock()))
        self.enterContext(patch.object(mp, "_complete_mode", new=AsyncMock()))
        self.allocate = self.enterContext(patch.object(mp, "_allocate_reused_footage", new=AsyncMock(side_effect=lambda w, plan, shots, *a: (shots, {}))))
        reporter = SimpleNamespace(start_stage=lambda *a: None, update_stage=lambda *a: None, complete_stage=lambda *a: None)
        self.work = SimpleNamespace(task_dir=self.root, task_id="a" * 32, reporter=reporter, draft_context=None)
        self.settings = SimpleNamespace(quality_min_match_confidence=0.5)

    def matcher(self, shot_id: int, confidence: float, fallback: bool):
        async def fake(work, queries, pool, settings, progress, **kwargs):
            self.pool = sorted(value.shot_id for value in pool)
            return [MatchPlanItem(sentence_id=queries[0].sentence_id, text="x", kind="narration", shot_id=shot_id,
                                  confidence=confidence, is_fallback=fallback,
                                  candidates=[MatchCandidate(shot_id=shot_id, similarity=0.53)])]
        return patch.object(mp, "_match_narration", new=fake)

    async def test_all_clips_used_reuses_other_footage_and_keeps_a_weak_match_flagged(self):
        with self.matcher(2, 0.34, True):
            await v2_editing._replacement(self.work, 0, "酒水礼盒", self.settings)
        self.assertEqual(self.pool, [1, 2], "everything except what this sentence shows now")
        rendered_plan = self.render.await_args.args[1]
        self.assertEqual(rendered_plan[0].shot_id, 2)
        self.assertTrue(rendered_plan[0].is_fallback, "a weak match stays flagged for review before export")
        self.allocate.assert_awaited_once()  # the shared clip gets its own real window

    async def test_declined_reuse_keeps_the_strict_rules(self):
        self.work.draft_context = {"shot_reuse": {"accepted": False}}
        with self.matcher(2, 0.34, True), self.assertRaises(v2_editing.ReplacementUnavailable) as caught:
            await v2_editing._replacement(self.work, 0, "酒水礼盒", self.settings)
        self.assertEqual(caught.exception.code, "used")
        self.render.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
