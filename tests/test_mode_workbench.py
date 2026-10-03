"""Mode workbench boundaries with opaque TEMP media and a mock mode editor.

No application singleton, real data, dotenv, provider requests or media decoding.
The fake editor exercises the reporter/transaction contract, not acoustic quality.
"""
from __future__ import annotations

# Contract tests deliberately inspect the manager's owned transaction state.
# pyright: reportPrivateUsage=false

import asyncio
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import ValidationError

from backend.config import Settings
from backend.models import (
    AnnotatedShot, EDLClip, EDLItem, MatchCandidate, MatchPlanItem,
    QualitySummary, QuoteTake, ReportResponse, ReportRow, SegmentManifestItem, SentenceInput,
    SentenceTiming, Speaker, StageState, TaskState, UploadedAsset, VisionQuality,
)
from backend.production_modes import JumpCut, SourceHint, validate_quote_trim  # pyright: ignore[reportUnknownVariableType]
from backend.providers.asr import ASRTranscript, ASRUtterance, ASRWord
from backend.revisions import ARTIFACTS, STATE_FIELDS, artifact_files, read_json, record_metadata, snapshot_revision
from backend.storage import write_json_atomic


def _import_probe(command: list[str]) -> int:
    if command == ["ffmpeg", "-v", "quiet"]:
        return 0
    raise AssertionError("Unexpected import-time media command")


with patch("platform._syscmd_ver", return_value=("", "", "")), patch("subprocess.call", side_effect=_import_probe), patch(
    "subprocess.Popen", side_effect=AssertionError("No import subprocess"),
):
    from backend.task_manager import TaskManager, TaskRecord
    from backend.workbench import EditRequest, _apply_state, _ingest_recording, _prepare_workspace, create_workbench_router


def _take(identity: str = "main", upload: str = "upload-a", start: float = 1.0) -> QuoteTake:
    text = "alpha beta gamma delta"
    return QuoteTake.model_validate({
        "take_id": identity, "upload_id": upload, "start": start, "end": start + 4,
        "speaker_id": "speaker-a", "asr_text": text, "score": .95,
        "words": [{"w": word, "s": start + i, "e": start + i + 1} for i, word in enumerate(text.split())],
    })


def _write_models(root: Path, name: str, values: list[Any]) -> None:
    write_json_atomic(root / name, [value.model_dump(mode="json", by_alias=True) for value in values])


def _seed(manager: TaskManager) -> Any:
    root = Path(manager.settings.data_dir) / "mode-task"
    for directory in ("raw", "norm", "tts", "segments", "thumbs"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    uploads: list[UploadedAsset] = []
    for index, identity in enumerate(("upload-a", "upload-b")):
        raw = root / f"raw/source-{index}.mp4"
        raw.write_bytes(f"owned raw {index}; no decoding".encode())
        (root / f"norm/norm_{index}.mp4").write_bytes(f"normalized original {index}; no decoding".encode())
        uploads.append(UploadedAsset(original_name=f"source-{index}.mp4", stored_name=raw.name, path=raw,
                                     size=raw.stat().st_size, content_type="video/mp4", upload_id=identity,
                                     source_duration_seconds=12.0))
    # Real TaskRecord with additive fields: also exercises the pre-migration
    # manager while another adapter adds the declared dataclass fields.
    record: Any = TaskRecord(task_id="mode-task", task_dir=root, script="News title\nNarration\nalpha beta gamma delta",
                             uploads=uploads, status=TaskState.done, progress=100)
    record.mode, record.mode_contract = "mixed", True
    record.upload_ids = ["upload-a", "upload-b"]
    record.speakers = [Speaker(id="speaker-a", name="Interviewee", title="Witness")]
    record.sentences = [SentenceInput(idx=0, text="Narration", kind="narration"),
                        SentenceInput(idx=1, text="alpha beta gamma delta", kind="quote",
                                      source_hint=SourceHint(upload_id="upload-a", seg_id="segment-a"))]
    record.quality_gate_mode = "warn"
    record.processing_started_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    record.processing_completed_at = record.processing_started_at + timedelta(seconds=2)
    record.total_elapsed_seconds = 2.0
    for stage in record.stages:
        stage.status, stage.fraction = StageState.done, 1.0
        stage.started_at, stage.completed_at = record.processing_started_at, record.processing_completed_at
        stage.elapsed_seconds = .2
    shots = [AnnotatedShot(shot_id=i, source_index=0, source_scene_index=i, source_name="source-0.mp4",
                           norm_path="norm/norm_0.mp4", start=0, end=12, duration=12, status="available",
                           description="Synthetic source", quality=VisionQuality(sharp=.9, bright=.9)) for i in range(4)]
    timings: list[SentenceTiming] = []
    plan: list[MatchPlanItem] = []
    edl: list[EDLItem] = []
    segments: list[SegmentManifestItem] = []
    rows: list[ReportRow] = []
    for i, sentence in enumerate(record.sentences):
        (root / f"tts/{i}.wav").write_bytes(b"owned unit audio; no decoding")
        (root / f"segments/{i}.mp4").write_bytes(b"owned rendered segment; no decoding")
        source = _take() if i == 1 else None
        alts = [_take("alternative", "upload-b", 6.0)] if i == 1 else []
        timings.append(SentenceTiming(sentence_id=i, text=sentence.text, audio_path=f"tts/{i}.wav",
                                      duration=4, start=i * 4, end=i * 4 + 4, gap_after=0,
                                      audio_kind="sync" if i else "tts"))
        plan.append(MatchPlanItem(sentence_id=i, text=sentence.text, kind=sentence.kind, source=source, alt_takes=alts,
                                  shot_id=i, confidence=.95, candidates=[MatchCandidate(shot_id=i, similarity=.95)]))
        edl.append(EDLItem(sentence_id=i, clips=[EDLClip.model_validate({"shot_id": i, "src": "norm/norm_0.mp4", "in": 1, "out": 5})],
                           timeline_start=i * 4, timeline_end=i * 4 + 4))
        segments.append(SegmentManifestItem(sentence_id=i, segments=[f"segments/{i}.mp4"]))
        rows.append(ReportRow(sentence_id=i, sentence=sentence.text, kind=sentence.kind, source=source, alt_takes=alts,
                              shot_id=i, thumb_url=f"/api/tasks/mode-task/thumbs/{i}.jpg", description="Synthetic",
                              duration=4, confidence=.95, is_fallback=False, audio_kind="sync" if i else "tts"))
    for shot in shots:
        (root / f"thumbs/shot_{shot.shot_id}.jpg").write_bytes(b"thumbnail sentinel")
    for name, models in (("timings.json", timings), ("match_plan.json", plan), ("edl.json", edl),
                         ("segment_manifest.json", segments), ("shots_annotated.json", shots)):
        _write_models(root, name, models)
    report = ReportResponse(task_id=record.task_id, rows=rows, mode="mixed", speakers=record.speakers,
                            jumpcuts=[JumpCut(after_row=1, cover="zoom", downgraded=True)],
                            quality=QualitySummary(blocking_issue_count=0, warning_count=0))
    write_json_atomic(root / "report.json", report.model_dump(mode="json"))
    for name in ("final.mp4", "video_only.mp4", "narration.m4a", "subs.ass"):
        (root / name).write_bytes(b"original committed sentinel")
    for name in ("broll_pool.json", "jumpcuts.json", "lower_thirds.json"):
        write_json_atomic(root / name, {"synthetic": True})
    # Actual persisted ASR/UploadStore shapes, not the old {synthetic: true}
    # placeholder which hid transcript.segments objects from duplicate tests.
    clocks: dict[str, Any] = {}
    snapshots: list[dict[str, Any]] = []
    asr_records: list[dict[str, Any]] = []
    for index, asset in enumerate(uploads):
        assert asset.upload_id is not None
        raw, norm = f"raw/{asset.stored_name}", f"norm/norm_{index}.mp4"
        clocks[asset.upload_id] = {
            "source_index": index, "raw_path": raw, "norm_path": norm,
            "source_duration": 12., "prepared_start": 0., "prepared_end": 12.,
            "norm_source_offset": 0., "norm_duration": 12., "audio_offset_seconds": 0.,
            "raw_sha256": hashlib.sha256((root / raw).read_bytes()).hexdigest(),
            "norm_sha256": hashlib.sha256((root / norm).read_bytes()).hexdigest(),
        }
        take = _take(upload=asset.upload_id or "", start=1. if index == 0 else 6.)
        words = [ASRWord(text=word.w, start_time_ms=int(word.s * 1000), end_time_ms=int(word.e * 1000),
                         speaker_id="speaker-a") for word in take.words]
        transcript = ASRTranscript(text=take.asr_text, duration_ms=12000, words=words,
            utterances=[ASRUtterance(text=take.asr_text, start_time_ms=int(take.start * 1000),
                                    end_time_ms=int(take.end * 1000), speaker_id="speaker-a", words=words)])
        snapshots.append({"id": asset.upload_id, "sec": 12., "status": "ready", "has_speech": True,
            "transcript": {"segments": [{"id": "segment-a", "start": take.start, "end": take.end,
                "text": take.asr_text, "speaker_id": "speaker-a",
                "words": [word.model_dump(mode="json") for word in take.words]}]},
            "asr_result": transcript.model_dump(mode="json")})
        asr_records.append({"source_index": index, "source_name": asset.original_name,
            "source_media_path": raw, "status": "available", "cache_hit": True,
            "transcript": transcript.model_dump(mode="json")})
    write_json_atomic(root / "production_mode.json", {
        "schema_version": 1, "recipe": "three-mode-evidence-v1", "mode": "mixed", "mode_contract": True,
        "source_clocks": clocks, "base_broll_shot_ids": [0, 2, 3], "broll_shot_ids": [0, 2, 3],
        "upload_ids": record.upload_ids, "sentences": [s.model_dump(mode="json") for s in record.sentences],
        "speakers": [s.model_dump(mode="json") for s in record.speakers],
        "preferences": record.preferences.model_dump(mode="json"),
        "audio_cache": {"0": {"raw_timing": timings[0].model_dump(mode="json")}},
    })
    write_json_atomic(root / "pretranscripts.json", snapshots)
    write_json_atomic(root / "asr_transcripts.json", asr_records)
    _write_models(root, "shots.json", shots)
    write_json_atomic(root / "pipeline_manifest.json", {
        "schema_version": 1, "implementation_version": "synthetic-contract", "mode": "mixed",
        "mode_recipe": "three-mode-evidence-v1", "source_sha256": {"mode_pipeline.py": "a" * 64},
        "settings": {"data_dir": "C:/private/fixture", "api_key": "never-copy-this-sentinel"},
    })
    write_json_atomic(root / "subtitle_manifest.json", {"synthetic": True})
    manager._tasks[record.task_id] = record
    manager._write_upload_manifest(record)
    manager._persist_record(record)
    return record


class ModeRequestTests(unittest.TestCase):
    def test_duplicate_conflicting_and_unretained_operations_rejected(self) -> None:
        variants: list[dict[str, Any]] = [
            {"quote_trims": [{"id": 1, "start": 2.0, "end": 5.0}] * 2},
            {"quote_takes": [{"id": 1, "take_id": "alternative"}] * 2},
            {"to_narration": [1, 1]}, {"to_narration": [True]},
            {"to_narration": [1], "edits": [{"sentence_id": 1, "text": "replacement"}]},
            {"to_narration": [1], "quote_takes": [{"id": 1, "take_id": "alternative"}]},
            {"quote_takes": [{"id": 1, "take_id": "alternative"}], "quote_trims": [{"id": 1, "start": 2., "end": 5.}]},
            {"quote_trims": [{"id": 2, "start": 2., "end": 5.}]}, {"keep_sentence_ids": []},
            {"speakers": [{"id": "same"}, {"id": "same"}]},
        ]
        for change in variants:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                EditRequest.model_validate({"expected_revision": 0, "keep_sentence_ids": [0, 1], **change})

    def test_trim_no_nonfinite_coercion_or_client_paths(self) -> None:
        variants: list[dict[str, Any]] = [
            {"id": True, "start": 2., "end": 5.}, {"id": 1, "start": "2", "end": 5.},
            {"id": 1, "start": 2., "end": float("inf")}, {"id": 1, "start": 5., "end": 2.},
            {"id": 1, "start": 2., "end": 5., "path": "raw/foreign.mp4"},
        ]
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                EditRequest.model_validate({"expected_revision": 0, "keep_sentence_ids": [1], "quote_trims": [data]})
        with self.assertRaises(ValidationError):
            EditRequest.model_validate({"expected_revision": 0, "keep_sentence_ids": [1],
                                        "quote_takes": [{"id": 1, "take_id": "x", "source": {"upload_id": "foreign"}}]})


class ModeWorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="mw-")
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "tasks"
        with patch.dict(os.environ, {}, clear=True):
            self.settings = Settings(_env_file=None, app_env="test", data_dir=self.data,  # pyright: ignore[reportCallIssue]
                                     min_free_disk_gb=0, max_pending_tasks=2)
        self.manager = TaskManager(self.settings)
        self.record = _seed(self.manager)
        self.root = self.record.task_dir
        self.prefix = f"/api/tasks/{self.record.task_id}/workbench"
        self.denied = False
        self.hold = False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.events: list[int] = []
        self.work: Any = None
        self.original: Any = None
        module = types.ModuleType("backend.mode_pipeline")
        setattr(module, "edit_mode_workspace", self.fake_editor)
        self.enterContext(patch.dict(sys.modules, {"backend.mode_pipeline": module}))
        self.forbidden = [self.enterContext(patch(target, side_effect=AssertionError("Offline boundary crossed")))
                          for target in ("backend.workbench.create_tts_provider", "backend.workbench.EmbeddingProvider.from_settings",
                                         "backend.workbench.LLMProvider.from_settings", "backend.workbench.run_logged_command",
                                         "backend.config.get_settings", "subprocess.Popen", "httpx.HTTPTransport.handle_request",
                                         "httpx.AsyncHTTPTransport.handle_async_request")]
        self.client = self.make_client()

    async def asyncTearDown(self) -> None:
        for record in self.manager._tasks.values():
            if record.background is not None and not record.background.done():
                record.background.cancel()
                await asyncio.gather(record.background, return_exceptions=True)
        for forbidden in self.forbidden:
            forbidden.assert_not_called()

    def make_client(self, before: Any = None) -> httpx.AsyncClient:
        def authorize(request: Any, task_id: str, write: bool = False) -> Any:
            if self.denied or request.headers.get("X-Owner") != "synthetic":
                raise HTTPException(403, "Denied")
            return self.manager.get(task_id)
        app = FastAPI()
        app.include_router(create_workbench_router(self.settings, self.manager, authorize, before))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated",
                                   headers={"X-Owner": "synthetic"}, trust_env=False)
        self.addAsyncCleanup(client.aclose)
        return client

    async def submit(self, **change: Any) -> httpx.Response:
        return await self.client.post(self.prefix + "/edit", json={"expected_revision": self.record.revision,
                                                                   "keep_sentence_ids": [0, 1], **change})

    async def fake_editor(self, work: Any, original: Any, payload: EditRequest, settings: Any) -> None:
        self.work, self.original = work, original
        for stage in work.rerun_stages:
            work.reporter.start_stage(work, stage, f"mock operation {stage}")
            self.events.append(stage)
            work.reporter.update_stage(work, stage, .25, "mock bounded operation progress")
            if stage == work.rerun_stages[0]:
                self.entered.set()
                if self.hold:
                    await self.release.wait()
            work.reporter.complete_stage(work, stage, "mock operation complete")
        plan = [MatchPlanItem.model_validate(p) for p in read_json(work.task_dir, "match_plan.json")]
        by_id = {p.sentence_id: p for p in plan}
        for edit in payload.quote_trims:
            source = by_id[edit.id].source
            assert source is not None
            by_id[edit.id].source = QuoteTake.model_validate(validate_quote_trim(source, edit.start, edit.end))
            by_id[edit.id].trim = {"start": edit.start, "end": edit.end}
        for edit in payload.quote_takes:
            by_id[edit.id].source = next(take for take in by_id[edit.id].alt_takes if take.take_id == edit.take_id)
        for sentence_id in payload.to_narration:
            item = by_id[sentence_id]
            item.kind, item.source, item.to_narration = "narration", None, True
            item.alt_takes = []
        for edit in payload.edits:
            if edit.text is not None:
                by_id[edit.sentence_id].text = edit.text
        keep = set(payload.keep_sentence_ids)
        work.sentences = [s.model_copy(update={"text": by_id[s.idx].text, "kind": by_id[s.idx].kind})
                          for s in work.sentences if s.idx in keep]
        _write_models(work.task_dir, "match_plan.json", [p for p in plan if p.sentence_id in keep])
        report = read_json(work.task_dir, "report.json")
        report.update(mode=work.mode, speakers=[s.model_dump(mode="json") for s in work.speakers])
        report["rows"] = [row for row in report["rows"] if row["sentence_id"] in keep]
        for row in report["rows"]:
            item = by_id[row["sentence_id"]]
            row.update(kind=item.kind, source=item.source.model_dump(mode="json") if item.source else None,
                       trim=item.trim, to_narration=item.to_narration, sentence=item.text)
        write_json_atomic(work.task_dir / "report.json", report)
        (work.task_dir / "final.mp4").write_bytes(b"mock mode render; no decoding")

    async def finish(self) -> None:
        assert self.record.background is not None
        await self.record.background

    async def test_all_quote_edit_entries_rejected_before_mutation(self) -> None:
        before = record_metadata(self.record)
        for change in ({"text": "altered"}, {"recording_id": "a" * 32}, {"shot_id": 2}, {"instruction": "new picture"}):
            with self.subTest(change=change):
                response = await self.submit(edits=[{"sentence_id": 1, **change}])
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(record_metadata(self.record), before)
        self.assertFalse((self.root / "revisions").exists())
        self.assertIsNone(self.work)

    async def duplicate_record(self) -> Any:
        from backend.task_operations import create_task_operations_router
        app = FastAPI()
        app.include_router(create_task_operations_router(self.settings, self.manager,
                            lambda request, task_id, write=False: self.manager.get(task_id),
                            check_rate_limit=lambda request: self.fail("Duplicate cannot generate")))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated", trust_env=False) as client:
            response = await client.post(f"/api/tasks/{self.record.task_id}/duplicate", json={"expected_revision": self.record.revision})
        self.assertEqual(response.status_code, 201, response.text)
        duplicate = self.manager.get(response.json()["task_id"])
        assert duplicate is not None
        return duplicate

    async def test_original_forbids_conversion_and_even_same_pacing(self) -> None:
        self.record.mode = "original"
        variants: list[dict[str, Any]] = [{"to_narration": [1]}, {"pacing": "fast"}, {"pacing": "normal"},
                                         {"edits": [{"sentence_id": 0, "text": "new"}]}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertEqual((await self.submit(**changes)).status_code, 422)

    async def test_trim_minimum_word_boundary_expansion_and_segment_precision(self) -> None:
        for start, end in ((0., 5.), (1., 6.), (2.1, 5.), (2., 2.5), (1., 5.)):
            response = await self.submit(quote_trims=[{"id": 1, "start": start, "end": end}])
            self.assertEqual(response.status_code, 422, response.text)
        plan = read_json(self.root, "match_plan.json")
        plan[1]["source"].update(precision="segment", words=[])
        write_json_atomic(self.root / "match_plan.json", plan)
        self.assertEqual((await self.submit(quote_trims=[{"id": 1, "start": 2., "end": 5.}])).status_code, 422)

    async def test_repeat_trim_cannot_expand_committed_interval(self) -> None:
        plan = read_json(self.root, "match_plan.json")
        plan[1]["trim"] = {"start": 2., "end": 5.}
        write_json_atomic(self.root / "match_plan.json", plan)
        self.assertEqual((await self.submit(quote_trims=[{"id": 1, "start": 1., "end": 4.}])).status_code, 422)

    async def test_unknown_cross_task_take_and_narration_operation_rejected(self) -> None:
        variants: list[dict[str, Any]] = [{"id": 1, "take_id": "unknown"}, {"id": 1, "take_id": "main"},
                                         {"id": 0, "take_id": "alternative"}]
        for data in variants:
            self.assertEqual((await self.submit(quote_takes=[data])).status_code, 422)
        plan = read_json(self.root, "match_plan.json")
        plan[1]["alt_takes"][0]["upload_id"] = "other-task-upload"
        write_json_atomic(self.root / "match_plan.json", plan)
        self.assertEqual((await self.submit(quote_takes=[{"id": 1, "take_id": "alternative"}])).status_code, 422)
        self.assertEqual((await self.submit(to_narration=[0])).status_code, 422)

    async def test_quote_recording_route_and_direct_ingest_rejected(self) -> None:
        response = await self.client.post(self.prefix + "/recordings", data={"sentence_id": 1, "expected_revision": 0},
                                          files={"file": ("quote.wav", b"never decode", "audio/wav")})
        self.assertEqual(response.status_code, 422, response.text)
        upload = UploadFile(filename="quote.wav", file=io.BytesIO(b"never read"))
        with self.assertRaises(HTTPException) as caught:
            await _ingest_recording(self.record, 1, upload)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertTrue(upload.file.closed)
        self.assertFalse((self.root / "recordings").exists())
        self.assertEqual(self.record.status, TaskState.done)

    async def test_trim_dispatch_live_progress_and_no_raw_in_revision(self) -> None:
        self.hold = True
        response = await self.submit(quote_trims=[{"id": 1, "start": 2., "end": 5.}])
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.wait_for(self.entered.wait(), 5)
        self.assertEqual(self.record.revision, 0)
        self.assertEqual(self.record.current_stage, 7)
        self.assertEqual(self.record.stages[6].status, StageState.running)
        self.assertEqual(self.record.stages[6].fraction, .25)
        self.assertIsNotNone(self.record.stages[6].started_at)
        self.assertIsNone(self.record.stages[7].started_at)
        self.assertEqual(read_json(self.root, "task_state.json")["current_stage"], 7)
        self.assertEqual(self.work.mode, self.original.mode)
        self.assertTrue(self.original.mode_contract)
        self.assertEqual(self.work.upload_ids, ["upload-a", "upload-b"])
        self.assertEqual(self.original.task_dir, self.root)
        self.assertTrue(all(asset.path.is_relative_to(self.work.task_dir) and asset.path.is_file() for asset in self.work.uploads))
        self.assertTrue((self.work.task_dir / "norm/norm_1.mp4").is_file())
        self.release.set()
        await self.finish()
        self.assertEqual(self.events, [7, 8, 9, 10])
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        self.assertEqual(self.record.progress, 100)
        self.assertEqual(read_json(self.root, "match_plan.json")[1]["source"]["start"], 2.)
        metadata = read_json(self.root / "revisions/r1", "revision.json")
        self.assertFalse(any(name.startswith("raw/") for name in metadata["files"]))
        self.assertFalse((self.root / "revisions/r1/raw").exists())
        self.assertTrue({"production_mode.json", "pretranscripts.json", "broll_pool.json", "jumpcuts.json", "lower_thirds.json"}.issubset(metadata["files"]))
        self.assertIsInstance(self.record.sentences[1], SentenceInput)
        self.assertIsInstance(self.record.speakers[0], Speaker)

    async def test_take_and_explicit_conversion_reach_mode_editor(self) -> None:
        response = await self.submit(quote_takes=[{"id": 1, "take_id": "alternative"}])
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        self.assertEqual(read_json(self.root, "match_plan.json")[1]["source"]["upload_id"], "upload-b")
        response = await self.submit(to_narration=[1])
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.record.revision, 2, self.record.error_message)
        self.assertEqual(self.record.sentences[1].kind, "narration")
        self.assertIsNone(read_json(self.root, "match_plan.json")[1]["source"])

    async def test_speakers_only_real_hook_runs_eight_through_ten(self) -> None:
        response = await self.submit(speakers=[{"id": "speaker-a", "name": "Corrected", "title": "Reporter"}])
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.events, [8, 9, 10])
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        self.assertEqual(self.record.speakers[0].name, "Corrected")
        self.assertEqual(self.original.speakers[0].name, "Interviewee")
        context = (await self.client.get(self.prefix + "/context")).json()
        self.assertEqual(context["mode"], "mixed")
        self.assertEqual(context["speakers"][0]["name"], "Corrected")
        self.assertEqual(context["jumpcuts"], [{"after_row": 1, "cover": "zoom", "shot": None, "downgraded": True}])

    async def test_missing_actual_stage_reporter_cannot_publish_fake_completion(self) -> None:
        async def silent_editor(work: Any, *args: Any) -> None:
            (work.task_dir / "final.mp4").write_bytes(b"unreported derivative")
        with patch.object(sys.modules["backend.mode_pipeline"], "edit_mode_workspace", new=silent_editor):
            response = await self.submit(to_narration=[1])
            self.assertEqual(response.status_code, 202, response.text)
            await self.finish()
        self.assertEqual(self.record.revision, 0)
        self.assertEqual(self.record.status, TaskState.done)
        self.assertIn("did not report actual completion", self.record.error_message)
        self.assertEqual((self.root / "final.mp4").read_bytes(), b"original committed sentinel")

    async def test_actual_mode_editor_integrates_with_transaction_reporter_and_source_clocks(self) -> None:
        # Execute the other adapter's REAL orchestration. Only the media/ASR/QC
        # workers are mocked; schema, source hashing, plans, stages and commit are
        # real. No inference about decoded media quality is made by this test.
        original_root = self.root
        before = {p.relative_to(original_root).as_posix(): p.read_bytes()
              for p in original_root.rglob("*") if p.is_file()}
        self.record = await self.duplicate_record()
        self.root = self.record.task_dir
        self.prefix = f"/api/tasks/{self.record.task_id}/workbench"
        path = Path(__file__).resolve().parents[1] / "backend/mode_pipeline.py"
        spec = importlib.util.spec_from_file_location("backend.mode_pipeline", path)
        assert spec is not None and spec.loader is not None
        actual: Any = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"backend.mode_pipeline": actual}):
            spec.loader.exec_module(actual)
            self.assertEqual(read_json(self.root, "production_mode.json")["recipe"], actual.MODE_RECIPE)
            self.assertEqual(len(actual._snapshots(self.record)), 2)
            for name in ("pipeline_manifest.json", "asr_transcripts.json", "shots.json"):
                self.assertTrue((self.root / name).is_file(), name)
            async def assemble(work: Any, *args: Any, **kwargs: Any) -> list[SentenceTiming]:
                self.assertEqual(work.stages[6].status, StageState.running)
                args[-1](.5, "actual orchestrator/mock audio completion")
                return [SentenceTiming.model_validate(value) for value in read_json(work.task_dir, "timings.json")]
            async def exclude(work: Any, shots: list[AnnotatedShot], *args: Any) -> list[AnnotatedShot]:
                return shots
            async def render(work: Any, plan: list[MatchPlanItem], timings: Any, shots: Any, snapshots: Any,
                             mode_manifest: dict[str, Any], settings: Any, progress: Any) -> None:
                self.assertEqual(work.stages[8].status, StageState.running)
                progress(.5, "actual orchestrator/mock render completion")
                actual._save_mode(work, mode_manifest, plan, shots)
                (work.task_dir / "final.mp4").write_bytes(b"actual orchestration; mocked render")
            async def quality(work: Any, plan: list[MatchPlanItem], *args: Any) -> None:
                self.assertEqual(work.stages[9].status, StageState.running)
                report = read_json(work.task_dir, "report.json")
                for row, item in zip(report["rows"], plan, strict=True):
                    row.update(source=item.source.model_dump(mode="json") if item.source else None, trim=item.trim)
                write_json_atomic(work.task_dir / "report.json", report)
            with patch.object(actual, "_quote_shots", new=AsyncMock()), \
                 patch.object(actual, "_exclude_quotes", new=AsyncMock(side_effect=exclude)), \
                 patch.object(actual, "_assemble_audio", new=AsyncMock(side_effect=assemble)), \
                 patch.object(actual, "generate_mode_subtitles"), \
                 patch.object(actual, "_render_mode", new=AsyncMock(side_effect=render)), \
                 patch.object(actual, "_complete_mode", new=AsyncMock(side_effect=quality)), \
                 patch.object(actual, "_match_narration", new=AsyncMock(return_value=[])):
                response = await self.submit(quote_trims=[{"id": 1, "start": 2., "end": 5.}])
                self.assertEqual(response.status_code, 202, response.text)
                await self.finish()
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        self.assertTrue(all(stage.status == StageState.done and stage.elapsed_seconds is not None for stage in self.record.stages[6:]))
        self.assertEqual(read_json(self.root, "match_plan.json")[1]["source"]["asr_text"], "beta gamma delta")
        self.assertEqual((self.root / "raw/source-0.mp4").read_bytes(), b"owned raw 0; no decoding")
        self.assertEqual({p.relative_to(original_root).as_posix(): p.read_bytes()
                          for p in original_root.rglob("*") if p.is_file()}, before)
        for name in ("raw/source-0.mp4", "raw/source-1.mp4", "norm/norm_0.mp4", "norm/norm_1.mp4",
                     "tts/0.wav", "pretranscripts.json", "asr_transcripts.json"):
            self.assertEqual((self.root / name).read_bytes(), before[name])

    async def test_queued_clocks_do_not_start_before_global_slot(self) -> None:
        snapshot_revision(self.record)
        old = record_metadata(self.record)
        await self.manager._semaphore.acquire()
        try:
            response = await self.submit(to_narration=[1])
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(self.record.status, TaskState.queued)
            self.assertIsNone(self.record.processing_started_at)
            self.assertTrue(all(stage.started_at is None for stage in self.record.stages[6:]))
            assert self.record.background is not None
            self.record.background.cancel()
            await asyncio.gather(self.record.background, return_exceptions=True)
            await asyncio.sleep(0)
        finally:
            self.manager._semaphore.release()
        self.assertEqual(self.record.status, TaskState.done)
        self.assertEqual(self.record.revision, 0)
        self.assertEqual(self.record.processing_started_at.isoformat(), old["processing_started_at"])
        self.assertFalse(self.entered.is_set())

    async def test_cancellation_and_persist_failure_restore_mode_state_and_media(self) -> None:
        baseline = record_metadata(self.record)
        files = {name: (self.root / name).read_bytes() for name in artifact_files(self.root)}
        self.hold = True
        self.assertEqual((await self.submit(to_narration=[1])).status_code, 202)
        await asyncio.wait_for(self.entered.wait(), 5)
        assert self.record.background is not None
        self.record.background.cancel()
        await asyncio.gather(self.record.background, return_exceptions=True)
        self.assertEqual(self.record.mode, baseline["mode"])
        self.assertEqual(self.record.sentences[1].kind, "quote")
        self.assertEqual({name: (self.root / name).read_bytes() for name in files}, files)
        self.hold = False
        persist = self.manager._persist_record
        def fail_commit(record: Any) -> None:
            if record.revision == 1:
                raise OSError("synthetic persistence failure")
            persist(record)
        with patch.object(self.manager, "_persist_record", side_effect=fail_commit):
            self.assertEqual((await self.submit(to_narration=[1])).status_code, 202)
            await self.finish()
        self.assertEqual(self.record.revision, 0)
        self.assertEqual(self.record.sentences[1].kind, "quote")
        self.assertEqual({name: (self.root / name).read_bytes() for name in files}, files)
        self.assertFalse((self.root / "revisions/r1").exists())
        self.assertFalse(list(self.root.glob(".workbench-*")))

    async def test_reauthorization_rejection_restores_reserved_progress(self) -> None:
        snapshot_revision(self.record)
        before = record_metadata(self.record)
        def denied(*args: Any, **kwargs: Any) -> None:
            self.assertEqual(self.record.status, TaskState.queued)
            raise HTTPException(403, "expired")
        client = self.make_client(denied)
        response = await client.post(self.prefix + "/edit", json={"expected_revision": 0, "keep_sentence_ids": [0, 1], "to_narration": [1]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(record_metadata(self.record), before)
        self.assertEqual(self.record.status, TaskState.done)
        self.assertIsNone(self.record.background)

    async def test_metadata_restore_is_typed_allowlisted_and_legacy_compatible(self) -> None:
        state = record_metadata(self.record)
        self.assertEqual(set(state), set(STATE_FIELDS) | {"preferences", "stages"})
        self.assertNotIn(self.record.access_token, json.dumps(state))
        self.assertNotIn("upload_tokens", state)
        restored = SimpleNamespace(task_dir=self.root, access_token_hash="keep-host-secret")
        _apply_state(restored, {**state, "task_dir": "foreign", "access_token_hash": "injected", "uploads": []})
        self.assertEqual(restored.task_dir, self.root)
        self.assertEqual(restored.access_token_hash, "keep-host-secret")
        self.assertFalse(hasattr(restored, "uploads"))
        self.assertIsInstance(restored.speakers[0], Speaker)
        self.assertIsInstance(restored.sentences[0], SentenceInput)
        legacy = {key: value for key, value in state.items()
                  if key not in {"mode", "mode_contract", "upload_ids", "speakers", "sentences", "quality_gate_mode"}}
        _apply_state(restored, legacy)
        self.assertEqual((restored.mode, restored.mode_contract, restored.speakers, restored.sentences), ("voiceover", False, [], []))

    async def test_prepare_uses_owned_paths_and_snapshot_excludes_confirmations(self) -> None:
        write_json_atomic(self.root / "publication_checks.json", {"checked_keys": ["stale"]})
        snapshot_revision(self.record)
        source = self.root / "revisions/r0"
        work = self.data / "isolated-edit"
        await _prepare_workspace(self.record, work, source)
        self.assertEqual((work / "raw/source-1.mp4").read_bytes(), (self.root / "raw/source-1.mp4").read_bytes())
        self.assertTrue((work / "pretranscripts.json").is_file())
        self.assertNotIn("publication_checks.json", ARTIFACTS)
        self.assertFalse((source / "publication_checks.json").exists())
        self.record.uploads[0].path = self.data / "not-owned.mp4"
        with self.assertRaises(ValueError):
            await _prepare_workspace(self.record, self.data / "must-not-prepare", source)

    async def test_all_manifest_clocks_are_inventory_checked_not_just_current_quotes(self) -> None:
        manifest: dict[str, Any] = {"source_clocks": {
            asset.upload_id: {"source_index": i, "raw_path": f"raw/{asset.stored_name}",
                              "norm_path": f"norm/norm_{i}.mp4"}
            for i, asset in enumerate(self.record.uploads)
        }}
        write_json_atomic(self.root / "production_mode.json", manifest)
        snapshot_revision(self.record)
        source = self.root / "revisions/r0"
        await _prepare_workspace(self.record, self.data / "clock-work", source)
        self.assertTrue((self.data / "clock-work/raw/source-1.mp4").is_file())
        manifest["source_clocks"]["upload-a"]["raw_path"] = "raw/source-1.mp4"
        write_json_atomic(source / "production_mode.json", manifest)
        with self.assertRaises(ValueError):
            await _prepare_workspace(self.record, self.data / "unsafe-clock-work", source)
        self.assertFalse((self.data / "unsafe-clock-work").exists())

    async def test_raw_recording_cache_and_new_quote_thumbnail_are_revision_dependencies(self) -> None:
        raw_audio = self.root / "tts/recorded-original.wav"
        raw_audio.write_bytes(b"unprocessed narration recording")
        write_json_atomic(self.root / "production_mode.json", {"audio_cache": {
            "0": {"raw_timing": {"audio_path": "tts/recorded-original.wav"}},
        }})
        snapshot_revision(self.record)
        self.assertEqual((self.root / "revisions/r0/tts/recorded-original.wav").read_bytes(), raw_audio.read_bytes())
        self.assertTrue((self.root / "revisions/r0/thumbs/shot_0.jpg").is_file())
        write_json_atomic(self.root / "production_mode.json", {"audio_cache": {
            "0": {"raw_timing": {"audio_path": "raw/source-0.mp4"}},
        }})
        with self.assertRaises(ValueError):
            artifact_files(self.root)

    async def test_restore_typed_mode_metadata_without_reprocessing(self) -> None:
        snapshot_revision(self.record)
        self.record.revision = 1
        self.record.speakers[0].name = "New name"
        self.record.sentences[1].kind = "narration"
        snapshot_revision(self.record)
        response = await self.client.post(self.prefix + "/restore", json={"expected_revision": 1, "revision": 0})
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.record.revision, 2, self.record.error_message)
        self.assertEqual(self.record.speakers[0].name, "Interviewee")
        self.assertEqual(self.record.sentences[1].kind, "quote")
        self.assertEqual(self.record.mode, "mixed")
        self.assertEqual(self.events, [])

    async def test_duplicate_preserves_mode_and_rejects_client_mode_override(self) -> None:
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        from backend.task_operations import create_task_operations_router
        app = FastAPI()
        app.include_router(create_task_operations_router(self.settings, self.manager,
                            lambda request, task_id, write=False: self.manager.get(task_id),
                            check_rate_limit=lambda request: self.fail("Duplicate cannot generate")))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated", trust_env=False) as client:
            prefix = f"/api/tasks/{self.record.task_id}/duplicate"
            denied = await client.post(prefix, json={"expected_revision": 0, "mode": "voiceover"})
            self.assertEqual(denied.status_code, 422)
            response = await client.post(prefix, json={"expected_revision": 0})
            self.assertEqual(response.status_code, 201, response.text)
        duplicate: Any = self.manager.get(response.json()["task_id"])
        assert duplicate is not None
        self.assertEqual(duplicate.mode, "mixed")
        self.assertTrue(duplicate.mode_contract)
        self.assertEqual(duplicate.upload_ids, self.record.upload_ids)
        self.assertIsNot(duplicate.speakers, self.record.speakers)
        self.assertIsNot(duplicate.sentences[0], self.record.sentences[0])
        self.assertNotEqual(duplicate.access_token, self.record.access_token)
        self.assertEqual(duplicate.uploads[0].upload_id, "upload-a")
        self.assertEqual(read_json(duplicate.task_dir / "revisions/r0", "revision.json")["state"]["mode"], "mixed")
        self.assertEqual({p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        for name in ("raw/source-0.mp4", "raw/source-1.mp4", "norm/norm_0.mp4", "norm/norm_1.mp4",
                     "tts/0.wav", "pretranscripts.json", "asr_transcripts.json", "shots.json"):
            self.assertEqual((duplicate.task_dir / name).read_bytes(), before[name])
        provenance = read_json(duplicate.task_dir, "pipeline_manifest.json")
        self.assertNotIn("settings", provenance)
        self.assertEqual(provenance["source_sha256"], {"mode_pipeline.py": "a" * 64})
        self.assertNotIn("never-copy-this-sentinel", json.dumps(provenance))
        for name in ("pipeline_manifest.json", "asr_transcripts.json", "shots.json"):
            self.assertFalse((duplicate.task_dir / "revisions/r0" / name).exists())

    async def test_new_mode_missing_immutable_edit_input_refuses_copy(self) -> None:
        from backend.task_operations import _plan_completed
        for name in ("pipeline_manifest.json", "asr_transcripts.json", "shots.json"):
            path = self.root / name
            saved = path.read_bytes()
            path.unlink()
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    _plan_completed(self.record, self.record)
            finally:
                path.write_bytes(saved)


class ModeQuoteTextRevisionTests(unittest.IsolatedAsyncioTestCase):
    """Real mode editor -> mode QC -> report -> publication; media is stubbed.

    Opaque TEMP files are not acoustic evidence. Unlike the router transaction
    tests above, these execute the production editor's text/source propagation.
    """

    async def asyncSetUp(self) -> None:
        from backend import mode_pipeline
        self.editor = mode_pipeline
        temporary = tempfile.TemporaryDirectory(prefix="mq-text-")
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        with patch.dict(os.environ, {}, clear=True):
            self.settings = Settings(_env_file=None, app_env="test", data_dir=self.data,  # pyright: ignore[reportCallIssue]
                                     min_free_disk_gb=0)
        self.manager = TaskManager(self.settings)
        self.record = _seed(self.manager)
        self.record.mode = "original"
        self.record.sentences = self.record.sentences[1:]
        self.record.script = "News title\nalpha beta gamma delta"
        self.root = self.record.task_dir
        for name in ("match_plan.json", "timings.json", "edl.json", "segment_manifest.json"):
            write_json_atomic(self.root / name, read_json(self.root, name)[1:])
        manifest = read_json(self.root, "production_mode.json")
        manifest.update(mode="original", sentences=[s.model_dump(mode="json") for s in self.record.sentences])
        write_json_atomic(self.root / "production_mode.json", manifest)
        report = read_json(self.root, "report.json")
        report.update(mode="original", rows=report["rows"][1:], jumpcuts=[])
        write_json_atomic(self.root / "report.json", report)
        self.forbidden = [self.enterContext(patch(target, side_effect=AssertionError("No media/provider calls")))
                          for target in ("subprocess.Popen", "backend.config.get_settings",
                                         "httpx.HTTPTransport.handle_request", "httpx.AsyncHTTPTransport.handle_async_request")]

    async def asyncTearDown(self) -> None:
        for forbidden in self.forbidden:
            forbidden.assert_not_called()

    async def run_editor(self, **changes: Any) -> Any:
        snapshot_revision(self.record)
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        metadata = record_metadata(self.record)
        work = SimpleNamespace(task_id=self.record.task_id, task_dir=self.data / "edited")
        _apply_state(work, metadata)
        work.revision = 1
        work.status = TaskState.done
        await _prepare_workspace(self.record, work.task_dir, self.root / "revisions/r0")
        payload = EditRequest.model_validate({"expected_revision": 0, "keep_sentence_ids": [1], **changes})

        async def audio(work: Any, plan: Any, *args: Any, **kwargs: Any) -> Any:
            source = plan[0].source
            assert source is not None
            return [SentenceTiming(sentence_id=1, text=source.asr_text, audio_path="tts/1.wav",
                                   duration=source.end - source.start, start=0, end=source.end - source.start,
                                   gap_after=0, audio_kind="sync")]

        async def render(work: Any, plan: Any, timings: Any, shots: Any, snapshots: Any,
                         manifest: Any, settings: Any, progress: Any) -> None:
            self.editor._save_mode(work, manifest, plan, shots)

        def quality(root: Path, *args: Any, **kwargs: Any) -> Any:
            from backend.production_modes import evaluate_mode_checks  # pyright: ignore[reportUnknownVariableType]
            issues = cast(list[dict[str, Any]], evaluate_mode_checks(
                kwargs["mode_rows"], kwargs["speakers"], kwargs["mode"],
                kwargs["preferences"], kwargs["gate_mode"], kwargs["jumpcuts"]))
            result: dict[str, Any] = {"blocking_issue_count": sum(i["level"] == 0 for i in issues),
                      "warning_count": sum(i["level"] != 0 for i in issues), "issues": issues, "checks": issues}
            write_json_atomic(root / "quality_report.json", result)
            return result

        try:
            with patch.object(self.editor, "_quote_shots", new=AsyncMock()), \
                 patch.object(self.editor, "_exclude_quotes", new=AsyncMock(return_value=[])), \
                 patch.object(self.editor, "_match_narration", new=AsyncMock(return_value=[])), \
                 patch.object(self.editor, "_generated_narration", new=AsyncMock(return_value=[])), \
                 patch.object(self.editor, "_assemble_audio", new=audio), \
                 patch.object(self.editor, "generate_mode_subtitles"), \
                 patch.object(self.editor, "_render_mode", new=render), \
                 patch.object(self.editor, "_verify_mode_audio_sources"), \
                 patch.object(self.editor, "generate_quality_report", new=quality), \
                 patch.object(self.editor, "_baselines"), \
                 patch.object(self.editor, "REQUIRED_ARTIFACTS", ()):
                await self.editor.edit_mode_workspace(work, self.record, payload, self.settings)
        finally:
            self.assertEqual(record_metadata(self.record), metadata)
            self.assertEqual({p.relative_to(self.root).as_posix(): p.read_bytes()
                              for p in self.root.rglob("*") if p.is_file()}, before)
        return work

    def assert_current_text(self, work: Any, expected: str) -> None:
        from backend.publication import publication_gate
        self.assertEqual(work.sentences[0].text, expected)
        self.assertEqual(work.script, "News title\n" + expected)
        root = work.task_dir
        self.assertEqual(read_json(root, "match_plan.json")[0]["text"], expected)
        self.assertEqual(read_json(root, "match_plan.json")[0]["source"]["asr_text"], expected)
        self.assertEqual(read_json(root, "sentences.json")[0]["text"], expected)
        self.assertEqual(read_json(root, "script_structure.json")["sentences"][0]["text"], expected)
        self.assertEqual((root / "script_segmented.txt").read_text(encoding="utf-8"), work.script)
        self.assertEqual(read_json(root, "production_mode.json")["sentences"][0]["text"], expected)
        report = read_json(root, "report.json")
        self.assertEqual(report["rows"][0]["sentence"], expected)
        self.assertEqual(report["rows"][0]["spoken_text"], expected)
        self.assertNotIn("QUOTE_NONCONTIGUOUS", {i["code"] for i in report["checks"]})
        gate = publication_gate(work)
        self.assertEqual(gate["blocking_count"], 0, gate)
        self.assertNotIn("QUOTE_NONCONTIGUOUS", {i["code"] for i in gate["checks"]})
        baseline = read_json(self.root / "revisions/r0", "revision.json")["state"]
        self.assertEqual(baseline["script"], "News title\nalpha beta gamma delta")
        self.assertEqual(baseline["sentences"][0]["text"], "alpha beta gamma delta")

    async def test_original_word_trim_updates_current_text_not_baseline(self) -> None:
        work = await self.run_editor(quote_trims=[{"id": 1, "start": 2., "end": 5.}])
        self.assert_current_text(work, "beta gamma delta")
        source = read_json(work.task_dir, "match_plan.json")[0]["source"]
        self.assertEqual(source["score"], .95)  # No fabricated new matching score.
        self.assertEqual([w["w"] for w in source["words"]], ["beta", "gamma", "delta"])

    async def test_alternative_uses_selected_asr_not_old_manuscript(self) -> None:
        plan = read_json(self.root, "match_plan.json")
        selected = QuoteTake.model_validate(validate_quote_trim(_take("alternative", "upload-b", 6.), 7., 10.))
        plan[0]["alt_takes"] = [selected.model_dump(mode="json")]
        write_json_atomic(self.root / "match_plan.json", plan)
        work = await self.run_editor(quote_takes=[{"id": 1, "take_id": selected.take_id}])
        self.assert_current_text(work, "beta gamma delta")
        current = read_json(work.task_dir, "match_plan.json")[0]
        self.assertIsNone(current["trim"])
        self.assertEqual(current["source"], selected.model_dump(mode="json"))
        self.assertEqual(current["alt_takes"][0], plan[0]["source"])

    async def test_general_quote_text_edit_with_trim_is_still_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EditRequest.model_validate({"expected_revision": 0, "keep_sentence_ids": [1],
                                        "quote_trims": [{"id": 1, "start": 2., "end": 5.}],
                                        "edits": [{"sentence_id": 1, "text": "alpha gamma delta"}]})
        with self.assertRaisesRegex(ValueError, "原声不能改字"):
            await self.run_editor(edits=[{"sentence_id": 1, "text": "alpha gamma delta"}])

    async def test_no_quote_operation_does_not_launder_internal_deletion(self) -> None:
        from backend.publication import confirm_checks, publication_gate
        plan = read_json(self.root, "match_plan.json")
        plan[0]["text"] = "alpha gamma delta"
        write_json_atomic(self.root / "match_plan.json", plan)
        work = await self.run_editor(caption_style="big")
        self.assertEqual(work.sentences[0].text, "alpha gamma delta")
        gate = publication_gate(work)
        issue = next(i for i in gate["checks"] if i["code"] == "QUOTE_NONCONTIGUOUS")
        self.assertEqual(issue["level"], 0)
        self.assertGreater(gate["blocking_count"], 0)
        with self.assertRaises(HTTPException) as caught:
            confirm_checks(work, 1, [issue["key"]])
        self.assertEqual(caught.exception.status_code, 422)

    async def test_invalid_word_cut_fails_without_changing_original(self) -> None:
        with self.assertRaisesRegex(ValueError, "词内"):
            await self.run_editor(quote_trims=[{"id": 1, "start": 2.1, "end": 5.}])


if __name__ == "__main__":
    unittest.main()