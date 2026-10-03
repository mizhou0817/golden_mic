"""Real TaskManager own-voice intent lifecycle; synthetic tones, not speech.

Run this file explicitly for the focused, TEMP-only install_v2_guards runner.
It is also safe to collect from the already-guarded parent V2 runner. Product
imports are deferred until setUpClass; no live providers or dotenv are needed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from backend.task_manager import TaskRecord


ROOT = Path(__file__).resolve().parents[1]
MEDIA_PROOF: list[dict[str, Any]] = []


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class OwnVoiceLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        from tests import test_mode_pipeline, test_v2_drafts
        from backend import mode_pipeline, task_manager

        cls.media = test_mode_pipeline
        cls.drafts = test_v2_drafts
        cls.mp = mode_pipeline
        cls.tm = task_manager
        cls.media.ModeMediaIntegration.setUpClass()
        cls.addClassCleanup(cls.media.ModeMediaIntegration.tearDownClass)

    async def asyncSetUp(self):
        # Composition, not inheritance: do not silently collect the draft suite.
        self.context = self.drafts.DraftTests()
        await self.context.asyncSetUp()
        self.addAsyncCleanup(self.context.asyncTearDown)
        self.manager = self.context.manager
        self.service = self.context.service
        self.settings = self.context.settings
        self.settings.video_embedding_enabled = False
        self.settings.entity_verification_enabled = False
        self.settings.quality_gate_mode = "warn"
        self.fixture = self.media.ModeMediaIntegration()
        asyncio.get_running_loop().slow_callback_duration = 10

    async def record(self, *, lifecycle_v2: bool = True) -> TaskRecord:
        from backend.admission import owner_digest
        from backend.drafts import _retry_binding
        from backend.models import TaskState

        record, _ = await self.context.draft()
        assert record is not None
        self.fixture.root = record.task_dir
        fixture = self.fixture.record("voiceover", texts=[("今天活动开幕。", "narration")])
        for key in ("uploads", "upload_ids", "script", "sentences"):
            setattr(record, key, getattr(fixture, key))
        record.preferences.voice = "mine"
        record.lifecycle_v2 = lifecycle_v2
        record.status = TaskState.queued
        record.draft_context["retry_binding"] = _retry_binding(record, self.settings)
        self.service.ledger.accept(record.task_id, record.owner_hash, owner_digest("ip"), "synthetic-input")
        self.manager._persist_record(record)
        self.assertTrue(record.mode_contract)
        self.assertFalse((record.task_dir / "own_voice.wav").exists())
        return record

    def intent(self, record: TaskRecord, *, revision: bool = False) -> None:
        self.assertEqual(record.preferences.voice, "mine")
        state_path = record.task_dir / "task_state.json"
        state = read(state_path)
        self.assertEqual(state["preferences"]["voice"], "mine")
        restored = self.manager._load_record(state_path, record.task_dir)
        assert restored is not None
        self.assertEqual(restored.preferences.voice, "mine")
        self.assertEqual(restored.status, record.status)
        self.assertEqual(restored.current_stage, record.current_stage)
        if revision:
            saved = read(record.task_dir / "revisions/r0/revision.json")
            self.assertEqual(saved["state"]["preferences"]["voice"], "mine")
            self.assertEqual(saved["state"]["stages"], state["stages"])
            for name in ("final.mp4", "timings.json", "narration_profile.json", self.mp.MODE_MANIFEST):
                self.assertEqual(sha(record.task_dir / name), sha(record.task_dir / "revisions/r0" / name))

    def actual_media(self, record: TaskRecord, expected_kind: str = "tts") -> None:
        """Independent ffprobe and decoded PCM evidence, not existence-only proof."""
        root = record.task_dir
        timings = json.loads((root / "timings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(timings), 1)
        timing = timings[0]
        self.assertEqual(timing["audio_kind"], expected_kind)
        with wave.open(str(root / timing["audio_path"]), "rb") as pcm:
            self.assertEqual(pcm.getframerate(), 48000)
            frames, rate = pcm.getnframes(), pcm.getframerate()
            self.assertGreater(frames, rate)
            self.assertAlmostEqual(frames / rate, timing["duration"], delta=1 / rate)
            self.assertTrue(any(pcm.readframes(frames)), "PCM must not be silent")
        probe = json.loads(self.media.invoke([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(root / "final.mp4")]))
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        self.assertEqual((video["codec_name"], video["width"], video["height"], video["r_frame_rate"]),
                         ("h264", 1920, 1080, "30/1"))
        self.assertEqual((audio["codec_name"], audio["sample_rate"]), ("aac", "48000"))
        duration = float(probe["format"]["duration"])
        self.assertTrue(math.isfinite(duration))
        self.assertAlmostEqual(duration, timing["end"], delta=0.1)
        decoded = self.media.invoke(["ffmpeg", "-v", "error", "-i", str(root / "final.mp4"),
                                     "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "s16le", "pipe:1"])
        self.assertGreater(len(decoded), 48000 * 2)
        self.assertTrue(any(decoded))
        self.assertTrue(any(self.media.video_frame(root / "final.mp4", duration / 2)))
        MEDIA_PROOF.append({"test": self._testMethodName, "audio_kind": expected_kind,
                            "unit_pcm_frames": frames, "sample_rate": rate,
                            "timing_duration": timing["duration"], "mp4_duration": duration,
                            "decoded_audio_frames": len(decoded) // 2,
                            "final_sha256": sha(root / "final.mp4"), "synthetic_tone_not_speech": True})

    async def failed_after_tts(self, record: TaskRecord, voice: Any) -> dict[str, Any]:
        from backend.models import TaskState

        with patch("backend.mode_pipeline.generate_mode_subtitles", side_effect=RuntimeError("synthetic local stage-8 failure")):
            await self.manager._run(record)
        self.assertEqual(record.status, TaskState.failed, record.error_message)
        self.assertEqual(record.current_stage, 8)
        self.assertEqual(voice.calls, ["今天活动开幕。"])
        receipt = read(record.task_dir / "mode_stage_cache/stage-7.json")
        self.assertEqual(receipt["state"], "complete")
        self.assertIsNotNone(self.mp.ModeStageCache(record.task_dir).load(7, receipt["signature"]))
        self.intent(record)
        return receipt

    async def test_initial_mine_without_recording_runs_real_pipeline_and_persists_intent(self):
        from backend.models import TaskState, StageState
        from backend.revisions import STATE_FIELDS

        record = await self.record()
        voice = self.media.FakeVoice()
        # Spies delegate every operation to the real pipeline, not a fake result.
        with ExitStack() as stack:
            self.fixture.providers(stack, voice=voice)
            pipeline = stack.enter_context(patch("backend.task_manager.run_pipeline", wraps=self.tm.run_pipeline))
            mode = stack.enter_context(patch("backend.mode_pipeline.run_mode_pipeline", wraps=self.mp.run_mode_pipeline))
            await self.manager._run(record)
        self.assertEqual(record.status, TaskState.done, record.error_message)
        pipeline.assert_awaited_once()
        mode.assert_awaited_once()
        assert pipeline.await_args is not None and mode.await_args is not None
        execution = pipeline.await_args.args[0]
        self.assertIs(mode.await_args.args[0], execution)
        self.assertIsNot(execution, record)
        self.assertIsNot(execution.preferences, record.preferences)
        self.assertEqual(execution.preferences.voice, "ai")
        self.assertEqual(voice.calls, ["今天活动开幕。"])
        self.assertFalse((record.task_dir / "own_voice.wav").exists())
        self.assertFalse((record.task_dir / "student_narration.json").exists())
        self.assertTrue(all(stage.status == StageState.done for stage in record.stages))
        self.assertEqual((record.current_stage, record.progress, record.resume_from), (10, 100, None))
        self.assertIsNotNone(record.processing_completed_at)
        self.intent(record, revision=True)
        manifest = read(record.task_dir / self.mp.MODE_MANIFEST)
        self.assertEqual(manifest["preferences"]["voice"], "ai")
        self.assertEqual(manifest["audio_cache"]["0"]["origin"], "tts")
        profile = read(record.task_dir / "narration_profile.json")
        self.assertEqual(profile["units"][0]["audio_kind"], "tts")
        self.assertEqual(read(record.task_dir / "tts_manifest.json")["tts_unit_count"], 1)
        # Investigate real copy mutations: no invented audio attributes or fake
        # assignments. Reporter clocks intentionally belong only to the original.
        self.assertEqual(set(vars(execution)), set(vars(record)))
        clocks = {"processing_started_at", "processing_completed_at", "total_elapsed_seconds", "updated_at",
              "current_stage", "stage_name", "progress", "message"}
        for field in STATE_FIELDS:
            if field not in clocks:
                self.assertEqual(getattr(execution, field, None), getattr(record, field, None), field)
        self.actual_media(record)

    async def test_explicit_retry_reuses_verified_tts_and_preserves_mine(self):
        from backend.admission import owner_digest
        from backend.models import TaskState

        record = await self.record()
        voice = self.media.FakeVoice()
        with ExitStack() as stack:
            self.fixture.providers(stack, voice=voice)
            receipt = await self.failed_after_tts(record, voice)
            hashes = {name: sha(record.task_dir / name) for name in receipt["files"]}
            completed = [s.model_dump(mode="json") for s in record.stages[:7]]
            counts = self.service.ledger.counts(record.owner_hash, owner_digest("ip"))
            # Any provider construction on the warm retry is a failure, including
            # a new TTS object whose local call counter would hide a duplicate.
            with patch("backend.mode_pipeline.create_tts_provider", side_effect=AssertionError("verified TTS must be reused")), \
                 patch("backend.providers.vision.VisionProvider.from_settings", side_effect=AssertionError("Vision must be reused")), \
                 patch("backend.mode_pipeline.build_match_plan", side_effect=AssertionError("matching must be reused")):
                response = await self.service.retry(record, {})
                self.assertEqual(response["resume_from"], 8)
                assert record.background is not None
                await record.background
        self.assertEqual(record.status, TaskState.done, record.error_message)
        self.assertEqual(voice.calls, ["今天活动开幕。"])
        self.assertEqual(hashes, {name: sha(record.task_dir / name) for name in hashes})
        self.assertEqual(completed, [s.model_dump(mode="json") for s in record.stages[:7]])
        self.assertEqual(counts, (1, 1, 1))
        self.assertEqual(counts, self.service.ledger.counts(record.owner_hash, owner_digest("ip")))
        self.assertEqual(self.context.guard._reserved_bytes, 0)
        self.assertFalse((record.task_dir / "own_voice.wav").exists())
        self.intent(record, revision=True)
        self.actual_media(record)

    async def test_unknown_provider_state_and_corrupt_tts_block_retry_without_fallback(self):
        from backend.admission import owner_digest
        from backend.models import TaskState
        from backend.storage import write_json_atomic
        from fastapi import HTTPException

        record = await self.record()
        voice = self.media.FakeVoice()
        with ExitStack() as stack:
            self.fixture.providers(stack, voice=voice)
            receipt = await self.failed_after_tts(record, voice)
            before_state = (record.task_dir / "task_state.json").read_bytes()
            receipt_path = record.task_dir / "mode_stage_cache/stage-7.json"
            with patch.object(self.manager, "enqueue_retry", wraps=self.manager.enqueue_retry) as enqueue:
                for state in ("started", "unknown"):
                    with self.subTest(provider_state=state):
                        write_json_atomic(receipt_path, {**receipt, "state": state})
                        with self.assertRaises(HTTPException) as caught:
                            await self.service.retry(record, {})
                        self.assertEqual(caught.exception.status_code, 409)
                        self.assertEqual(caught.exception.detail, {"code": "retry_provider_uncertain", "stage": 7})
                write_json_atomic(receipt_path, receipt)
                # A complete receipt alone is insufficient; actual bytes matter.
                (record.task_dir / "narration.m4a").write_bytes(b"synthetic-corrupted-cache")
                with self.assertRaises(HTTPException) as caught:
                    await self.service.retry(record, {})
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(caught.exception.detail, {"code": "retry_cache_invalid"})
                enqueue.assert_not_called()
        self.assertIsNone(record.background)
        self.assertEqual(record.status, TaskState.failed)
        self.assertEqual(voice.calls, ["今天活动开幕。"])
        self.assertEqual(before_state, (record.task_dir / "task_state.json").read_bytes())
        self.assertEqual(self.service.ledger.counts(record.owner_hash, owner_digest("ip")), (1, 1, 1))
        self.assertEqual(self.context.guard._reserved_bytes, 0)
        self.intent(record)

    async def test_non_v2_mine_without_recording_fails_without_ai_fallback(self):
        from backend.models import TaskState

        record = await self.record(lifecycle_v2=False)
        voice = self.media.FakeVoice()
        with ExitStack() as stack:
            self.fixture.providers(stack, voice=voice)
            tts = stack.enter_context(patch("backend.mode_pipeline.create_tts_provider",
                                             side_effect=AssertionError("legacy mine cannot fall back to AI")))
            pipeline = stack.enter_context(patch("backend.task_manager.run_pipeline", wraps=self.tm.run_pipeline))
            await self.manager._run(record)
        assert pipeline.await_args is not None
        self.assertIs(pipeline.await_args.args[0], record)
        self.assertEqual(record.status, TaskState.failed)
        self.assertEqual(record.current_stage, 7)
        assert record.error_message is not None
        self.assertIn("没有整篇录音", record.error_message)
        tts.assert_not_called()
        self.assertEqual(voice.calls, [])
        self.assertFalse((record.task_dir / "final.mp4").exists())
        self.assertFalse((record.task_dir / "revisions/r0").exists())
        self.intent(record)

    async def test_existing_whole_recording_runs_manager_without_tts(self):
        """Reuse the existing own-WAV fixture and synthetic transcript, not speech."""
        from backend.models import TaskState

        record = await self.record(lifecycle_v2=False)
        own_voice = record.task_dir / "own_voice.wav"
        self.media.invoke(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                           "sine=frequency=550:sample_rate=48000:duration=3", "-c:a", "pcm_s16le", str(own_voice)])
        original_hash = sha(own_voice)
        transcript = self.mp.ASRTranscript.model_validate(self.media.speech_snapshot()["asr_result"])
        transcript.utterances = transcript.utterances[:1]
        transcript.text = transcript.utterances[0].text
        transcript.duration_ms = 3000
        class RecordedVoiceProvider(self.media.FakeContext):
            transcribe = AsyncMock(return_value=transcript)

            def validate_configuration(self) -> None:
                pass

        provider = RecordedVoiceProvider()
        with ExitStack() as stack:
            self.fixture.providers(stack)
            tts = stack.enter_context(patch("backend.mode_pipeline.create_tts_provider",
                                             side_effect=AssertionError("own recording forbids TTS")))
            stack.enter_context(patch("backend.mode_pipeline.create_asr_provider", return_value=provider))
            await self.manager._run(record)
        self.assertEqual(record.status, TaskState.done, record.error_message)
        tts.assert_not_called()
        provider.transcribe.assert_awaited_once()
        self.assertEqual(original_hash, sha(own_voice))
        self.assertEqual(read(record.task_dir / self.mp.MODE_MANIFEST)["audio_cache"]["0"]["origin"], "own_voice")
        self.intent(record, revision=True)
        self.actual_media(record, "sync")


def focused_main() -> int:
    """Only these five tests; exact fresh TEMP ledger exception, no broad suite."""
    import ast
    import os
    import sys
    import tempfile
    import time
    from contextlib import redirect_stderr, redirect_stdout

    sys.path.insert(0, str(ROOT))
    from tests.run_core_validation import synthetic_environment
    from tests.run_v2_validation import SafeLoader, SafeResult, Sink, install_v2_guards, safe_exception, write_json

    root = Path(tempfile.mkdtemp(prefix="gm-own-voice-focused-", dir=Path(os.environ["LOCALAPPDATA"]) / "Temp"))
    evidence = root / "evidence"
    for name in ("evidence", "tmp", "tasks", "cache/asr", "cache/hf", "cache/matplotlib", "cache/numba"):
        (root / name).mkdir(parents=True, exist_ok=True)
    environment, ffmpeg = synthetic_environment(root)
    os.environ.clear()
    os.environ.update(environment)
    tempfile.tempdir = str(root / "tmp")
    sources = sorted({*ROOT.glob("backend/**/*.py"), ROOT / "backend/mode_rules.json",
                      *(ROOT / "tests" / name for name in ("test_v2_own_voice.py", "test_v2_drafts.py", "test_mode_pipeline.py",
                                                            "run_v2_validation.py", "run_core_validation.py"))})
    before = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}
    write_json(evidence / "source-before.json", before)
    summary: dict[str, Any] = {"scope": "five_own_voice_tests_only", "temp_root": str(root)}
    started = time.perf_counter()
    guard = None
    code = 2
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(Sink()))
        stack.enter_context(redirect_stderr(Sink()))
        try:
            ast.parse((ROOT / "tests/test_v2_own_voice.py").read_text(encoding="utf-8"))
            guard = install_v2_guards(stack, root, evidence, ffmpeg, lambda: None)
            loader, result = SafeLoader(before), SafeResult(before)
            suite = loader.loadTestsFromName("tests.test_v2_own_voice.OwnVoiceLifecycleTests")
            summary["discovered"] = suite.countTestCases()
            suite.run(result)
            module = sys.modules["tests.test_v2_own_voice"]
            write_json(evidence / "media-proof.json", module.MEDIA_PROOF)
            summary.update(tests=result.testsRun, passed=result.passed, failures=len(result.failures),
                           errors=len(result.errors), skipped=len(result.skipped),
                           expected_failures=len(result.expectedFailures), discovery_errors=len(loader.errors),
                           diagnostics=result.events)
            code = 0 if result.wasSuccessful() and result.testsRun == 5 and not result.skipped and not loader.errors else 1
        except BaseException as error:
            summary["runner_failure"] = safe_exception(error, before)
        finally:
            after = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}
            write_json(evidence / "source-after.json", after)
            summary["source_drift"] = [name for name in before if before[name] != after[name]]
            summary["source_files_checked"] = len(before)
            if guard is not None:
                summary["guard_counters"] = dict(guard.counts)
                summary["remaining_owned_http_listeners"] = len(guard.ports)
                summary["registered_admission_databases"] = len(guard.databases)
                if guard.ports or any(k.startswith("suite:denied:") for k in guard.counts):
                    code = 1
            if summary["source_drift"]:
                code = 1
            summary.update(exit_code=code, duration_seconds=time.perf_counter() - started)
            write_json(evidence / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(focused_main())