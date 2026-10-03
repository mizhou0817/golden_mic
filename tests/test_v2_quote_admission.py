"""Focused V2-06 safety contract, not a new browser/full-pipeline case.

Run only under run_v2_validation's guards. Synthetic ASR text/word clocks and
real 40-second color video; no recognition/model/provider accuracy claim.
Real main ASGI routes, materialization, media validation and admission ledger.
Intercept ONLY final dispatch so a broken rejection cannot run the pipeline.
The unchanged V2-06 manuscript must return 422, NOT a tolerated/xfail 202.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from backend import production_modes as pm
from backend.media import run_logged_command
from backend.providers.asr import ASRTranscript
from backend.uploads import _Probe, _Wave, _editorial_transcript
from tests import test_v2_drafts as draft_fixture
from tests.mode_acceptance_server import provider_fixture


class QuoteAdmissionTests(unittest.IsolatedAsyncioTestCase):
    root: Path
    settings: draft_fixture.Settings
    guard: draft_fixture.UploadCapacityGuard
    manager: draft_fixture.TaskManager
    store: draft_fixture.DraftUploadStore
    service: draft_fixture.DraftService

    # Reuse setup/helpers, not inheritance/discovery of all draft tests.
    asyncSetUp = draft_fixture.DraftTests.asyncSetUp
    asyncTearDown = draft_fixture.DraftTests.asyncTearDown
    draft = draft_fixture.DraftTests.draft
    file = draft_fixture.DraftTests.file

    async def citizen(self, *, actual=None, mode="original", whole_word=False):
        record, receipt = await self.draft(mode=mode)
        assert record is not None
        source = self.root / "synthetic-citizen.mp4"
        await run_logged_command([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=30",
            "-t", "40", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ], self.root, "synthetic admission fixture")
        _, item = await self.file(record, data=source.read_bytes())
        provider = ASRTranscript.model_validate(provider_fixture("synthetic-citizen.mp4"))

        async def preprocess(upload):
            upload.probe = _Probe(sec=40, width=64, height=64, fps=30,
                                  has_audio=False, format_name="mov")
            # Explicit fixture injection. Unknown SNR matches stopped I's public
            # citizen snapshots; do not invent acoustic evidence from this video.
            upload.transcript = _editorial_transcript(upload, provider,
                _Wave([], [], 0, [], None, "unknown"), threading.Event())
            if actual is not None:
                upload.transcript.segments = [pm.TranscriptSegment(id="seg_0",
                    start=.123456789, end=8.987654321, text=actual, speaker_id="",
                    words=[pm.Word(w=actual, s=.123456789, e=8.987654321)] if whole_word else [])]
            upload.has_speech = True
            upload.status, upload.phase, upload.progress = "ready", "done", 100
            self.store._save(upload)

        with patch.object(self.store, "_preprocess", side_effect=preprocess):
            await self.service.file_action(record, item["file_id"], "complete")
            await asyncio.gather(*list(self.store._jobs.values()))
        return record, receipt, provider

    async def rejection(self, text, *, missing):
        from backend import main

        record, receipt, provider = await self.citizen()
        script = "合成缺失原话验收\n" + text
        if missing:
            # Independent disjoint control; NEVER replace the V2-06 manuscript.
            self.assertFalse(set(pm.normalize_text(text)) & set(pm.normalize_text(provider.text)))
        dispatch = Mock()
        with patch.multiple(main, settings=self.settings, task_manager=self.manager,
                            upload_capacity_guard=self.guard, _upload_store=self.store,
                            _draft_service=self.service), patch.object(self.manager, "start_task", dispatch):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url="http://testserver") as client:
                path = "/api/tasks/" + record.task_id
                headers = {"X-Task-Token": receipt["access_token"]}
                raw = {"mode": "original", "script": script}
                preview = await client.post(path + "/align", headers=headers, json=raw)
                self.assertEqual(preview.status_code, 200)
                rows = preview.json()["matches"]
                self.assertEqual(len(rows), 1)
                row = rows[0]
                if missing:
                    self.assertIsNone(row["source"])
                    self.assertEqual(row["score"], 0)
                else:
                    self.assertEqual(row["score"], 1)
                    self.assertEqual(row["source"]["asr_text"], "了")
                    self.assertEqual((row["start"], row["end"]), (1.375, 1.75))
                    self.assertIsNone(row["source"]["snr_db"])
                    self.assertEqual(pm.score_status(row["score"], True), "ok")
                    self.assertFalse(pm.quote_text_is_contiguous(text, row["source"]))
                before = copy.deepcopy(self.service.view(record))
                response = await client.post(path + "/start", headers=headers, json=raw)
                # Safe bounded diagnostics only: never response text/capabilities.
                self.observed = {"http": response.status_code,
                    "ledger_accepted": self.service.ledger.accepted(record.task_id),
                    "dispatch_calls": dispatch.call_count, "status": record.status.value,
                    "pending_stages": sum(s.status.value == "pending" for s in record.stages)}
                self.assertEqual(response.status_code, 422, self.observed)
                if missing:
                    self.assertEqual(response.json()["detail"], {"code": "quote_missing", "badRows": [1]})
                else:
                    self.assertEqual(response.json()["detail"], {"code": "quote_unverified", "badRows": [1]})
                self.assertFalse(self.service.ledger.accepted(record.task_id))
                dispatch.assert_not_called()
                self.assertIsNone(record.background)
                self.assertEqual(record.status.value, "draft")
                self.assertTrue(all(s.status.value == "pending" for s in record.stages))
                self.assertEqual(self.service.view(record), before)

    async def test_v206_unchanged_manuscript_requires_422_before_admission(self):
        await self.rejection("今年我们还请了舞狮队", missing=False)

    async def test_independent_disjoint_quote_missing_rejects_without_admission(self):
        await self.rejection("麒麟翡翠琥珀", missing=True)

    def disk_hashes(self, directory):
        # Windows exclusively locks this store sentinel's first byte. Compare
        # its identity/size/mtime separately, not by opening a competing handle.
        return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file() and p != self.store.root / ".lock"}

    async def contract(self, actual, query, *, mode="original", accepted=False,
                       hinted=False, cached=False, score_range=None, code="quote_unverified"):
        """Real router/matcher/ledger; spies prove rejected requests do no work."""
        from backend import main
        from backend import drafts
        from backend.mode_pipeline import ModeStageCache

        record, receipt, _ = await self.citizen(actual=actual, mode=mode, whole_word=mode == "mixed")
        raw = {"mode": mode, "script": "合成原话验收\n" + query,
               "sentences": [{"idx": 0, "text": query, "kind": "quote"}]}
        if hinted:
            raw["sentences"][0]["source_hint"] = {
                "upload_id": record.draft_context["files"][0]["up_id"], "seg_id": "seg_0"}
        dispatch = Mock()
        with patch.multiple(main, settings=self.settings, task_manager=self.manager,
                            upload_capacity_guard=self.guard, _upload_store=self.store,
                            _draft_service=self.service), patch.object(self.manager, "start_task", dispatch):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                         base_url="http://testserver") as client:
                path = "/api/tasks/" + record.task_id
                headers = {"X-Task-Token": receipt["access_token"]}
                if cached:
                    # A previous valid preview and stale speaker-cache evidence
                    # cannot certify this different start request.
                    prior = await client.post(path + "/align", headers=headers,
                        json={"mode": mode, "script": "合成原话验收\n" + actual})
                    self.assertEqual(prior.status_code, 200)
                    record.draft_context["speaker_analysis_cache"] = {
                        "key": "synthetic-stale-key", "snapshots": self.service.snapshots(record, ready=True),
                        "result": {"inference_verified": True}}
                    self.manager._persist_record(record)
                data = self.service.editorial(record, raw, starting=True)
                rows = await self.service.matches(data, self.service.snapshots(record, ready=True))
                row = rows[0]
                if score_range is not None:
                    self.assertGreaterEqual(row["score"], score_range[0])
                    self.assertLess(row["score"], score_range[1])
                if code != "quote_missing":
                    self.assertIsNotNone(row["source"])
                    self.assertEqual(pm.quote_text_is_contiguous(query, row["source"]), accepted and mode == "original")
                before = copy.deepcopy(self.service.view(record))
                disk_before = self.disk_hashes(self.root)
                lock_before = (self.store.root / ".lock").stat()
                with ExitStack() as spies:
                    effects = [spies.enter_context(patch.object(obj, name, wraps=getattr(obj, name)))
                               for obj, name in ((self.store, "materialize"), (self.service, "analyze_speakers"),
                                                 (drafts, "prepare_media_inputs"), (self.service.ledger, "accept"),
                                                 (ModeStageCache, "begin"), (ModeStageCache, "save"))]
                    response = await client.post(path + "/start", headers=headers, json=raw)
                self.assertEqual(response.status_code, 202 if accepted else 422)
                if accepted:
                    self.assertTrue(self.service.ledger.accepted(record.task_id))
                    dispatch.assert_called_once_with(record.task_id)
                    # No rounding or rewriting of server lexical/timing evidence.
                    after = await self.service.matches(data, self.service.snapshots(record, ready=True))
                    self.assertEqual(after, rows)
                    if mode == "mixed":
                        # Word takes include the matcher's existing edge handles;
                        # the observed word clocks themselves stay exact.
                        self.assertEqual(row["source"]["words"], [
                            {"w": actual, "s": .123456789, "e": 8.987654321}])
                    else:
                        self.assertEqual((row["source"]["start"], row["source"]["end"]), (.123456789, 8.987654321))
                else:
                    self.assertEqual(response.json()["detail"], {"code": code, "badRows": [1]})
                    for effect in effects:
                        effect.assert_not_called()
                    self.assertFalse(self.service.ledger.accepted(record.task_id))
                    self.assertEqual(self.service.view(record), before)
                    self.assertEqual(self.disk_hashes(self.root), disk_before)
                    lock_after = (self.store.root / ".lock").stat()
                    self.assertEqual((lock_after.st_ino, lock_after.st_size, lock_after.st_mtime_ns),
                                     (lock_before.st_ino, lock_before.st_size, lock_before.st_mtime_ns))
                    self.assertIsNone(record.background)
                    self.assertEqual(record.status.value, "draft")
                    self.assertTrue(all(s.status.value == "pending" for s in record.stages))
                    dispatch.assert_not_called()
                return row

    async def test_c_contiguous_original_accepts(self):
        text = "今天我们来到社区了解公共服务如何让居民生活更加便利"
        await self.contract(text, text, accepted=True)

    async def test_c_contiguous_substring_accepts(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "公共服务如何让居民生活更加便利", accepted=True)

    async def test_c_numeric_equivalence_accepts(self):
        await self.contract("今年社区为三十名居民提供了便捷的公共服务", "今年社区为30名居民提供了便捷的公共服务", accepted=True)

    async def test_c_internal_deletion_rejects_high_match(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "今天我们来到社区公共服务如何让居民生活更加便利", score_range=(.85, 1.0000001))

    async def test_c_internal_insertion_rejects_high_match(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "今天我们来到社区真正了解公共服务如何让居民生活更加便利", score_range=(.85, 1.0000001))

    async def test_c_internal_filler_deletion_rejects_even_score_one(self):
        await self.contract("今天我们来到社区嗯了解公共服务如何让居民生活更加便利", "今天我们来到社区了解公共服务如何让居民生活更加便利", score_range=(.85, 1.0000001))

    async def test_c_internal_filler_insertion_rejects(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "今天我们来到社区嗯了解公共服务如何让居民生活更加便利", score_range=(.85, 1.0000001))

    async def test_c_source_hint_cannot_certify_noncontiguous_text(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "今天我们来到社区公共服务如何让居民生活更加便利", hinted=True)

    async def test_c_preview_and_speaker_cache_cannot_certify_changed_text(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "今天我们来到社区公共服务如何让居民生活更加便利", hinted=True, cached=True)

    async def test_missing_rejects_before_materialization_and_cache(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "麒麟翡翠琥珀", code="quote_missing", score_range=(0, .6))

    async def test_b_uncertain_noncontiguous_policy_is_preserved(self):
        await self.contract("今天我们来到社区了解公共服务如何让居民生活更加便利", "今天大家走进园区了解公共服务如何让居民生活更加便利", mode="mixed", accepted=True, score_range=(.6, .85))