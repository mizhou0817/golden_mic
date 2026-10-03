"""Persist real QC targets and read immutable revisions; no TTS/media decoding.

Uses the existing opaque mode-workbench fixture. Only audio verification and
acoustic measurements are doubled; QC/report/revision/router code is real.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from tests.test_mode_workbench import _seed, TaskManager
from backend.config import Settings
from backend.models import AnnotatedShot, EditingPreferences, MatchPlanItem, ScriptDocument, Sentence, SentenceTiming
from backend.mode_pipeline import REQUIRED_ARTIFACTS, _complete_mode
from backend.quality import generate_quality_report
from backend.revisions import read_json, snapshot_revision
from backend.storage import write_json_atomic
from backend.tts_pipeline import standardize_tts_speaking_rate
from backend.workbench import _safe_report, create_workbench_router


class RateSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="rate-summary-")
        self.addCleanup(temporary.cleanup)
        with patch.dict(os.environ, {}, clear=True):
            self.settings = Settings(_env_file=None, app_env="test", data_dir=Path(temporary.name) / "tasks",
                                     min_free_disk_gb=0, tts_news_rate_tolerance=.125)
        self.manager = TaskManager(self.settings)
        self.record = _seed(self.manager)
        self.root = self.record.task_dir
        self.plan = [MatchPlanItem.model_validate(v) for v in read_json(self.root, "match_plan.json")]
        self.shots = [AnnotatedShot.model_validate(v) for v in read_json(self.root, "shots_annotated.json")]
        self.timings = [SentenceTiming.model_validate(v) for v in read_json(self.root, "timings.json")]
        self.timings[0] = self.timings[0].model_copy(update={"speaking_rate_cpm": 201., "spoken_unit_count": 13.4})
        write_json_atomic(self.root / "script_structure.json", ScriptDocument(sentences=[
            Sentence(sentence_id=p.sentence_id, text=p.text) for p in self.plan]).model_dump(mode="json"))
        for name in REQUIRED_ARTIFACTS:
            if not (self.root / name).exists():
                (self.root / name).write_text("{}", encoding="utf-8")
        self.enterContext(patch("backend.mode_pipeline._verify_mode_audio_sources"))
        self.enterContext(patch("backend.quality._media_quality_metrics", return_value={"duration_seconds": 8.}))
        self.enterContext(patch("backend.quality._sentence_loudness_metrics", return_value=[]))
        self.forbidden = [self.enterContext(patch(name, side_effect=AssertionError("Offline boundary"))) for name in (
            "backend.mode_pipeline.create_tts_provider", "backend.config.get_settings", "subprocess.Popen",
            "httpx.HTTPTransport.handle_request", "httpx.AsyncHTTPTransport.handle_async_request")]

    async def asyncTearDown(self) -> None:
        for forbidden in self.forbidden:
            forbidden.assert_not_called()

    async def complete(self, pacing: str = "fast") -> dict:
        self.record.preferences = EditingPreferences(pacing=pacing)
        manifest = read_json(self.root, "production_mode.json")
        await _complete_mode(self.record, self.plan, self.shots, self.timings, manifest, self.settings)
        return read_json(self.root, "report.json")

    async def test_actual_mode_completion_persists_nondefault_qc_window_all_paces(self) -> None:
        for pacing, target in (("slow", 230), ("normal", 265), ("fast", 290)):
            with self.subTest(pacing=pacing):
                report = await self.complete(pacing)
                expected = {"target_cpm": target, "min_cpm": target * .875, "max_cpm": target * 1.125}
                self.assertEqual(report["quality"]["metrics"].get("narration_rate_target"), expected)
                self.assertEqual(read_json(self.root, "quality_report.json")["metrics"]["narration_rate_target"], expected)
                self.assertEqual(report["quality"]["metrics"]["narration_speaking_rate_min_cpm"], 201.)

    async def test_real_revision_and_context_gets_use_saved_qc_not_new_settings(self) -> None:
        await self.complete()
        snapshot_revision(self.record)
        old = self.root / "revisions/r0"
        before = {p.relative_to(old).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in old.rglob("*") if p.is_file()}
        self.settings.tts_news_rate_tolerance = .01
        self.record.revision = 1
        await self.complete("slow")
        snapshot_revision(self.record)
        app = FastAPI()
        app.include_router(create_workbench_router(self.settings, self.manager, lambda *_a, **_k: self.record))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated", trust_env=False) as client:
            prefix = "/api/tasks/mode-task/workbench"
            historical = await client.get(prefix + "/versions/0/report")
            current = await client.get(prefix + "/context")
        self.assertEqual(historical.status_code, 200)
        self.assertEqual(current.status_code, 200)
        self.assertEqual(historical.json()["report"]["quality"]["metrics"].get("narration_rate_target"),
                         {"target_cpm": 290, "min_cpm": 253.75, "max_cpm": 326.25})
        self.assertEqual(current.json()["report"]["quality"]["metrics"]["narration_rate_target"],
                         {"target_cpm": 230, "min_cpm": 227.7, "max_cpm": 232.3})
        self.assertEqual(before, {p.relative_to(old).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in old.rglob("*") if p.is_file()})

    async def test_old_snapshot_without_target_remains_missing_and_byte_identical(self) -> None:
        snapshot_revision(self.record)
        old = self.root / "revisions/r0"
        path = old / "report.json"
        before = path.read_bytes()
        self.settings.tts_news_rate_tolerance = .2
        report = _safe_report(old, self.record.task_id, {}, read_json(old, "revision.json")["state"])
        self.assertNotIn("narration_rate_target", report["quality"]["metrics"])
        self.assertEqual(path.read_bytes(), before)

    async def test_no_tts_or_original_mode_has_no_target(self) -> None:
        for mode in ("voiceover", "mixed", "original"):
            with self.subTest(mode=mode):
                report = generate_quality_report(self.root, self.shots, self.plan,
                    [t.model_copy(update={"audio_kind": "sync"}) for t in self.timings],
                    minimum_confidence=.5, target_chars_per_minute=290, rate_tolerance=.125, mode=mode)
                self.assertNotIn("narration_rate_target", report["metrics"])

    async def test_legacy_qc_without_explicit_target_does_not_invent_midpoint(self) -> None:
        report = generate_quality_report(self.root, self.shots, self.plan, self.timings,
                                         minimum_confidence=.5, minimum_speaking_rate_cpm=222, maximum_speaking_rate_cpm=333)
        self.assertNotIn("narration_rate_target", report["metrics"])

    async def test_saved_target_projection_is_strict_and_strips_private_extras(self) -> None:
        report = await self.complete()
        valid = {"target_cpm": 290, "min_cpm": 253.75, "max_cpm": 326.25}
        for value in (None, [], True, {}, {**valid, "target_cpm": True}, {**valid, "min_cpm": "253.75"},
                      {**valid, "min_cpm": 0}, {**valid, "max_cpm": 289}, {**valid, "min_cpm": 291}):
            report["quality"]["metrics"]["narration_rate_target"] = value
            write_json_atomic(self.root / "report.json", report)
            self.assertNotIn("narration_rate_target", _safe_report(self.root, self.record.task_id, {})["quality"]["metrics"])
        report["quality"]["metrics"]["narration_rate_target"] = {**valid, "path": "private-sentinel"}
        write_json_atomic(self.root / "report.json", report)
        self.assertEqual(_safe_report(self.root, self.record.task_id, {})["quality"]["metrics"]["narration_rate_target"], valid)

    async def test_actual_tts_window_and_qc_agree_at_custom_bounds_without_audio_or_provider(self) -> None:
        target, tolerance = 290, .125
        for measured, outliers in ((253.75, 0), (326.25, 0), (253.7, 1), (326.3, 1)):
            # TTS counts actual text/word units, not the persisted count field.
            # Integer units / 20 gives exact boundary rates without audio I/O.
            timing = self.timings[0].model_copy(update={"text": "a" * round(measured * 20),
                "duration": 1200., "start": 0., "end": 1200., "words": [],
                "speaking_rate_cpm": measured, "spoken_unit_count": round(measured * 20)})
            async def unchanged(_root, original, _factor):
                return original  # No fake acoustics claim: isolate window classification.
            with patch("backend.tts_pipeline._retime_tts_unit", side_effect=unchanged):
                _, metrics = await standardize_tts_speaking_rate(self.root, [timing], target_chars_per_minute=target, tolerance=tolerance)
            qc = generate_quality_report(self.root, self.shots, self.plan, [timing], minimum_confidence=.5,
                                         target_chars_per_minute=target, rate_tolerance=tolerance, mode="mixed")
            self.assertEqual(metrics["tts_unit_rate_outlier_count"], outliers)
            self.assertEqual(qc["metrics"]["narration_speaking_rate_outlier_count"], outliers)
            self.assertEqual(qc["metrics"].get("narration_rate_target"),
                             {"target_cpm": 290, "min_cpm": 253.75, "max_cpm": 326.25})


if __name__ == "__main__":
    unittest.main()