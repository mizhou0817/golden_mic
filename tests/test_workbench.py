"""Isolated workbench contract/transaction tests; never import main or paid clients."""
import asyncio
import io
import json
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

from backend.config import Settings
from backend.media import run_logged_command
from backend.models import (
    AnnotatedShot, EDLClip, EDLItem, MatchCandidate, MatchPlanItem,
    RerankDecision, SegmentManifestItem, SentenceTiming, TaskState, VisionQuality,
)
from backend.providers.tts import TTSProvider
from backend.revisions import (
    RevisionError, artifact_files, local_file, publish_artifacts, read_json,
    snapshot_revision,
)
from backend.storage import write_json_atomic
from backend.task_manager import TaskManager, TaskRecord
from backend.tts_pipeline import probe_audio_duration, _voice_profile_id
from backend.workbench import create_workbench_router


def wav_bytes(duration=1.0, frequency=440):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * frequency * i / 48000)))
                                   for i in range(round(duration * 48000))))
    return output.getvalue()


class FakeTTS(TTSProvider):
    calls = []

    def validate_configuration(self):
        pass

    async def synthesize(self, text, output_path):
        self.calls.append(text)
        output_path.write_bytes(wav_bytes(1.1))
        return []


class FakeEmbedding:
    calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def validate_configuration(self):
        pass

    async def embed(self, texts, **kwargs):
        self.calls.extend(texts)
        return [[1., .1, .2] for _ in texts]


class FakeLLM(FakeEmbedding):
    calls = []

    async def rerank(self, items, **kwargs):
        self.calls.extend(items)
        used = set()
        results = []
        for item in items:
            shot_id = next(c["shot_id"] for c in item["candidates"] if c["shot_id"] not in used)
            used.add(shot_id)
            results.append(RerankDecision(sentence_id=item["sentence_id"], beat_id=item.get("beat_id", 0), shot_id=shot_id, confidence=.9))
        return results


class WorkbenchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "task1"
        self.root.mkdir()
        self.settings = Settings(_env_file=None, app_env="test", data_dir=Path(self.temporary.name), quality_gate_mode="warn")
        self.manager = TaskManager(self.settings)
        self.record = TaskRecord(task_id="task1", task_dir=self.root, script="城市新闻。\n公园开放。", uploads=[], status=TaskState.done, progress=100)
        self.manager._tasks[self.record.task_id] = self.record
        self.auth_calls = []
        async def authorize(request, task_id, write=False):
            self.auth_calls.append((task_id, write))
            if request.headers.get("authorization") != "test-owner":
                raise HTTPException(403, "Denied")
            return self.manager.get(task_id)
        self.app = FastAPI()
        self.app.include_router(create_workbench_router(self.settings, self.manager, authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver", headers={"authorization": "test-owner"})
        self.url = "/api/tasks/task1/workbench"
        self.fixture()
        self.manager._persist_record(self.record)
        # Any accidental unmocked paid operation fails at construction, not network.
        self.patches = [patch("backend.workbench.EmbeddingProvider.from_settings", side_effect=AssertionError("Unexpected embedding")),
                        patch("backend.workbench.LLMProvider.from_settings", side_effect=AssertionError("Unexpected LLM")),
                        patch("backend.workbench.create_tts_provider", side_effect=AssertionError("Unexpected TTS"))]
        for p in self.patches:
            p.start()

    def fixture(self):
        for directory in ("tts", "norm", "thumbs", "segments"):
            (self.root / directory).mkdir()
        shots = []
        for i in range(4):
            (self.root / f"norm/{i}.mp4").write_bytes(b"source")
            (self.root / f"thumbs/shot_{i}.jpg").write_bytes(b"thumb")
            shots.append(AnnotatedShot(shot_id=i, source_index=i, source_scene_index=0,
                                      source_name=f"source{i}.mp4", norm_path=f"norm/{i}.mp4", start=0, end=5, duration=5,
                                      status="available", description=f"公园城市新闻画面{i}", keywords=["公园", "城市", "新闻"],
                                      quality=VisionQuality(sharp=.9, bright=.8)))
        timings, plan, edl, manifest = [], [], [], []
        cursor = 0
        for i, text in enumerate(("城市新闻。", "公园开放。")):
            (self.root / f"tts/{i}.wav").write_bytes(wav_bytes())
            (self.root / f"segments/{i}.mp4").write_bytes(b"segment")
            gap = .12 if i == 0 else 0
            timings.append(SentenceTiming(sentence_id=i, text=text, audio_path=f"tts/{i}.wav", duration=1, start=cursor, end=cursor+1,
                                          gap_after=gap, voice_profile_id=_voice_profile_id({"provider": "FakeTTS"})))
            plan.append(MatchPlanItem(sentence_id=i, text=text, shot_id=i, confidence=.9, candidates=[MatchCandidate(shot_id=i, similarity=.9)]))
            edl.append(EDLItem(sentence_id=i, clips=[EDLClip(shot_id=i, src=f"norm/{i}.mp4", in_time=0, out_time=1+gap)], timeline_start=cursor, timeline_end=cursor+1+gap))
            manifest.append(SegmentManifestItem(sentence_id=i, segments=[f"segments/{i}.mp4"]))
            cursor += 1+gap
        for name, models in (("shots_annotated.json", shots), ("timings.json", timings), ("source_timings.json", timings),
                             ("match_plan.json", plan), ("edl.json", edl), ("source_edl.json", edl), ("segment_manifest.json", manifest)):
            write_json_atomic(self.root / name, [m.model_dump(mode="json", by_alias=True) for m in models])
        write_json_atomic(self.root / "report.json", {"task_id": "task1", "rows": [
            {"sentence_id": i, "sentence": p.text, "shot_id": i, "thumb_url": "http://bad/?token=secret", "description": "城市", "duration": 1, "confidence": .9, "is_fallback": False} for i, p in enumerate(plan)]})
        (self.root / "final.mp4").write_bytes(b"old-final")
        (self.root / "video_only.mp4").write_bytes(b"old-video")
        (self.root / "narration.m4a").write_bytes(b"old-narration")

    async def asyncTearDown(self):
        if self.record.background and not self.record.background.done():
            self.record.background.cancel()
            await asyncio.gather(self.record.background, return_exceptions=True)
        await self.client.aclose()
        for p in reversed(self.patches):
            p.stop()
        self.temporary.cleanup()

    async def finish(self):
        await self.record.background

    async def submit(self, **kwargs):
        return await self.client.post(self.url + "/edit", json={"expected_revision": self.record.revision, "keep_sentence_ids": [0, 1], **kwargs})

    async def test_private_context_and_real_version_reads(self):
        denied = await self.client.get(self.url + "/context", headers={"authorization": "wrong"})
        self.assertEqual(denied.status_code, 403)
        self.assertFalse((self.root / "revisions").exists())
        response = await self.client.get(self.url + "/context")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["script"], self.record.script)
        self.assertEqual([s["shot_id"] for s in data["shots"] if s["unused"]], [2, 3])
        self.assertNotIn("audio_path", response.text)
        self.assertNotIn("norm_path", response.text)
        self.assertNotIn("secret", response.text)
        self.assertNotIn(self.record.access_token, response.text)
        snapshot = read_json(self.root / "revisions/r0", "revision.json")
        self.assertNotIn("access_token", json.dumps(snapshot))
        (self.root / "final.mp4").write_bytes(b"changed-current")
        video = await self.client.get(self.url + "/versions/0/video")
        self.assertEqual(video.content, b"old-final")
        self.assertEqual((await self.client.get(self.url + "/versions/0/report")).status_code, 200)
        self.assertEqual((await self.client.get(self.url + "/versions/99/video")).status_code, 404)

    async def test_stale_invalid_shots_and_resource_validation(self):
        for edit in ({"sentence_id": 0, "shot_id": 1}, {"sentence_id": 0, "shot_id": 99}, {"sentence_id": 0, "recording_id": "../secret"}):
            self.assertEqual((await self.submit(edits=[edit])).status_code, 422)
        self.assertEqual((await self.submit(expected_revision=99, edits=[{"sentence_id": 0, "text": "变更"}])).status_code, 409)
        self.assertEqual((await self.submit(keep_sentence_ids=[0, 0])).status_code, 422)
        self.assertEqual((await self.submit(keep_sentence_ids=[])).status_code, 422)
        self.assertEqual((await self.submit(edits=[{"sentence_id": 0, "text": "   "}])).status_code, 422)
        self.assertEqual((await self.submit()).status_code, 422)
        self.assertIsNone(self.record.background)

    async def test_failure_and_cancellation_keep_done(self):
        snapshot_revision(self.record)
        for error in (RuntimeError("provider token=secret"), asyncio.CancelledError()):
            with patch("backend.workbench._edit_workspace", side_effect=error):
                response = await self.submit(edits=[{"sentence_id": 0, "shot_id": 2}])
                self.assertEqual(response.status_code, 202)
                await asyncio.gather(self.record.background, return_exceptions=True)
            self.assertEqual(self.record.status, TaskState.done)
            self.assertEqual(self.record.revision, 0)
            self.assertEqual((self.root / "final.mp4").read_bytes(), b"old-final")
            self.assertTrue(self.record.error_message.startswith("operation_"))
            self.assertFalse(list(self.root.glob(".workbench-*")))
            self.assertFalse((self.root / "revisions/r1").exists())
        context = (await self.client.get(self.url + "/context")).json()
        self.assertEqual(context["last_operation_error"], "operation_cancelled")

    async def test_queued_synchronously_and_global_semaphore(self):
        snapshot_revision(self.record)
        await self.manager._semaphore.acquire()
        response = await self.submit(edits=[{"sentence_id": 0, "shot_id": 2}])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.record.status, TaskState.queued)
        self.assertEqual(read_json(self.root, "task_state.json")["status"], "queued")
        self.assertEqual((await self.submit(edits=[{"sentence_id": 0, "shot_id": 3}])).status_code, 409)
        self.record.background.cancel()
        await asyncio.gather(self.record.background, return_exceptions=True)
        await asyncio.sleep(0)  # allow completion callback, not wall-clock polling
        self.manager._semaphore.release()
        self.assertEqual(self.record.status, TaskState.done)

    async def test_restore_is_new_revision_and_restores_script_preferences(self):
        snapshot_revision(self.record, "baseline")
        self.record.script = "Changed script"
        self.record.preferences.pacing = "fast"
        self.record.revision = 1
        (self.root / "final.mp4").write_bytes(b"new-final")
        snapshot_revision(self.record, "changed")
        response = await self.client.post(self.url + "/restore", json={"expected_revision": 1, "revision": 0})
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.record.revision, 2)
        self.assertEqual(self.record.preferences.pacing, "normal")
        self.assertEqual(self.record.script, "城市新闻。\n公园开放。")
        self.assertEqual((self.root / "final.mp4").read_bytes(), b"old-final")
        self.assertEqual((self.root / "revisions/r1/final.mp4").read_bytes(), b"new-final")
        self.assertEqual(read_json(self.root, "task_state.json")["revision"], 2)
        self.assertIn(("task1", True), self.auth_calls)

    async def test_publish_rolls_back_media_metadata_and_persist_failure(self):
        staged = self.root / "staged"
        shutil.copytree(self.root, staged, ignore=shutil.ignore_patterns("staged"))
        (staged / "final.mp4").write_bytes(b"bad-final")
        write_json_atomic(staged / "report.json", {"bad": True})
        before = {name: (self.root / name).read_bytes() for name in artifact_files(self.root)}
        def fail():
            (self.root / "task_state.json").write_text("bad")
            raise OSError("disk failure")
        with self.assertRaises(OSError):
            publish_artifacts(staged, self.root, fail)
        self.assertEqual({name: (self.root / name).read_bytes() for name in before}, before)
        self.assertEqual(read_json(self.root, "task_state.json")["revision"], 0)

    async def test_path_security_and_no_secret_snapshot(self):
        for path in ("../secret", "C:/secret", "tts/file:stream", "\\server\\file", "/etc/passwd", "tts/../../secret"):
            with self.assertRaises(RevisionError):
                local_file(self.root, path, exists=False)
        timings = read_json(self.root, "timings.json")
        timings[0]["audio_path"] = "task_state.json"
        write_json_atomic(self.root / "timings.json", timings)
        with self.assertRaises(RevisionError):
            snapshot_revision(self.record)
        self.assertFalse((self.root / "revisions/r0").exists())

    async def test_sync_authorizer_supported(self):
        app = FastAPI()
        app.include_router(create_workbench_router(self.settings, self.manager, lambda request, task_id, write=False: self.record))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            self.assertEqual((await client.get(self.url + "/versions")).status_code, 200)

    async def real_media(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("Local FFmpeg/ffprobe required")
        await run_logged_command(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:r=30:d=5",
                                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(self.root / "norm/0.mp4")], self.root, "Test source")
        for i in range(1, 4):
            shutil.copy2(self.root / "norm/0.mp4", self.root / f"norm/{i}.mp4")
        for i, duration in ((0, "1.12"), (1, "1")):
            await run_logged_command(["ffmpeg", "-y", "-i", str(self.root / "norm/0.mp4"), "-t", duration,
                                      "-c:v", "libx264", "-preset", "ultrafast", "-an", str(self.root / f"segments/{i}.mp4")], self.root, "Test segment")
        from backend.subtitles import generate_ass_subtitles
        from backend.tts_pipeline import rebuild_narration_from_existing
        timings = [SentenceTiming.model_validate(t) for t in read_json(self.root, "timings.json")]
        await rebuild_narration_from_existing(self.root, timings, [0, 1], timings_path=self.root / "timings.json",
                                             narration_path=self.root / "narration.m4a", narration_profile_path=self.root / "narration_profile.json")
        generate_ass_subtitles(self.root, timings)

    async def test_real_recording_upload_apply_pace_delete_and_restore(self):
        await self.real_media()
        response = await self.client.post(self.url + "/recordings", data={"sentence_id": 0, "expected_revision": 0}, files={"file": ("voice.wav", wav_bytes(1.7, 650), "audio/wav")})
        self.assertEqual(response.status_code, 201, response.text)
        recording = response.json()
        self.assertAlmostEqual(recording["duration"], 1.7, places=2)
        self.assertNotIn("path", response.text)
        response = await self.submit(keep_sentence_ids=[0], pacing="fast", edits=[{"sentence_id": 0, "recording_id": recording["recording_id"], "shot_id": 2}])
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        context = (await self.client.get(self.url + "/context")).json()
        self.assertEqual(len(context["timings"]), 1)
        timing = context["timings"][0]
        self.assertEqual(timing["audio_kind"], "sync")
        self.assertEqual(timing["audio_source"], "recording")
        self.assertFalse(timing["transcript_verified"])
        self.assertLess(timing["duration"], 1.7)
        self.assertEqual(context["current_shots"][0]["shot_ids"], [2])
        self.assertAlmostEqual(await probe_audio_duration(self.root / "narration.m4a", self.root), timing["duration"], delta=.15)
        self.assertTrue((self.root / "final.mp4").stat().st_size > 1000)
        self.assertEqual(read_json(self.root, "source_timings.json"), read_json(self.root, "timings.json"))
        restore = await self.client.post(self.url + "/restore", json={"revision": 0, "expected_revision": 1})
        self.assertEqual(restore.status_code, 202)
        await self.finish()
        self.assertEqual(self.record.revision, 2)
        self.assertEqual(len(read_json(self.root, "timings.json")), 2)

    async def test_real_text_edit_rematches_with_specific_mock_providers(self):
        await self.real_media()
        FakeTTS.calls, FakeLLM.calls, FakeEmbedding.calls = [], [], []
        before_audio = (self.root / "tts/1.wav").read_bytes()
        before_clips = read_json(self.root, "edl.json")[1]["clips"]
        with patch("backend.workbench.create_tts_provider", return_value=FakeTTS()), \
             patch("backend.workbench.EmbeddingProvider.from_settings", return_value=FakeEmbedding()), \
             patch("backend.workbench.LLMProvider.from_settings", return_value=FakeLLM()):
            response = await self.submit(edits=[{"sentence_id": 0, "text": "市民参观公园。"}])
            self.assertEqual(response.status_code, 202, response.text)
            await self.finish()
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        self.assertEqual(FakeTTS.calls, ["市民参观公园。"])
        self.assertTrue(FakeLLM.calls)
        self.assertTrue(FakeEmbedding.calls)
        self.assertEqual((self.root / "tts/1.wav").read_bytes(), before_audio)
        self.assertEqual(read_json(self.root, "edl.json")[1]["clips"], before_clips)
        self.assertEqual(read_json(self.root, "report.json")["rows"][0]["sentence"], "市民参观公园。")
        self.assertIn("市民参观公园。", read_json(self.root, "task_state.json")["script"])
        narration = (self.root / "narration.m4a").read_bytes()
        current_shot = read_json(self.root, "report.json")["rows"][0]["shot_id"]
        with patch("backend.workbench.EmbeddingProvider.from_settings", return_value=FakeEmbedding()), \
             patch("backend.workbench.LLMProvider.from_settings", return_value=FakeLLM()):
            response = await self.submit(edits=[{"sentence_id": 0, "instruction": "选择公园画面"}])
            self.assertEqual(response.status_code, 202)
            await self.finish()
        self.assertEqual(self.record.revision, 2, self.record.error_message)
        self.assertNotEqual(read_json(self.root, "report.json")["rows"][0]["shot_id"], current_shot)
        self.assertEqual((self.root / "narration.m4a").read_bytes(), narration)
        self.assertEqual(FakeTTS.calls, ["市民参观公园。"])

    def student_narration_fixture(self):
        timings = read_json(self.root, "timings.json")
        (self.root / "student_audio").mkdir()
        shutil.copy2(self.root / "tts/0.wav", self.root / "student_audio/0.wav")
        timings[0].update(audio_kind="sync", audio_path="student_audio/0.wav", voice_profile_id="student-test")
        for name in ("timings.json", "source_timings.json"):
            write_json_atomic(self.root / name, timings)
        report = read_json(self.root, "report.json")
        report["rows"][0]["audio_kind"] = "sync"
        write_json_atomic(self.root / "report.json", report)
        write_json_atomic(self.root / "student_narration.json", {
            "schema_version": 1, "audio_source": "student_recording", "transcript_verified": True,
            "units": [{"sentence_id": 0, "audio_path": "student_audio/0.wav", "source_start": 0, "source_end": 1}],
        })

    async def test_student_audio_metadata_defaults_and_recording_override(self):
        self.student_narration_fixture()
        from backend.workbench import _audio_metadata
        self.assertEqual(_audio_metadata(self.root)["0"], {"audio_source": "recording", "transcript_verified": True})
        override = {"audio_source": "recording", "transcript_verified": False,
                    "recording_id": "a" * 32, "uploaded_revision": 0}
        write_json_atomic(self.root / "workbench_audio.json", {"0": override})
        context = (await self.client.get(self.url + "/context")).json()
        for key, value in override.items():
            self.assertEqual(context["timings"][0][key], value)
        self.assertEqual(context["timings"][1]["audio_source"], "tts")
        self.assertNotIn("transcript_verified", context["timings"][1])
        self.assertEqual(context["report"]["rows"][0]["audio_source"], "recording")

    async def test_resynthesized_student_audio_ignores_stale_metadata_on_read(self):
        self.student_narration_fixture()
        # Reproduce an already-published revision: TTS timings, but the original
        # student manifest survives and the edit used to remove its override.
        timings = read_json(self.root, "timings.json")
        timings[0].update(audio_kind="tts", audio_path="tts/0.wav")
        write_json_atomic(self.root / "timings.json", timings)
        report = read_json(self.root, "report.json")
        report["rows"][0]["audio_kind"] = "tts"
        write_json_atomic(self.root / "report.json", report)
        from backend.workbench import _audio_metadata
        for metadata in ({}, {"0": {"audio_source": "recording", "transcript_verified": True,
                                    "recording_id": "a" * 32, "uploaded_revision": 0}}):
            with self.subTest(metadata=metadata):
                write_json_atomic(self.root / "workbench_audio.json", metadata)
                self.assertEqual(_audio_metadata(self.root)["0"], {"audio_source": "tts"})
        context = (await self.client.get(self.url + "/context")).json()
        self.assertEqual(context["timings"][0]["audio_source"], "tts")
        for key in ("transcript_verified", "recording_id", "uploaded_revision"):
            self.assertNotIn(key, context["timings"][0])
        self.assertEqual(context["report"]["rows"][0]["audio_source"], "tts")
        version = (await self.client.get(self.url + "/versions/0/report")).json()
        self.assertEqual(version["report"]["rows"][0]["audio_source"], "tts")

    async def student_text_edit(self, pacing=None):
        await self.real_media()
        self.student_narration_fixture()
        original_manifest = (self.root / "student_narration.json").read_bytes()
        FakeTTS.calls = []
        with patch("backend.workbench.create_tts_provider", return_value=FakeTTS()):
            response = await self.submit(pacing=pacing, edits=[{
                "sentence_id": 0, "text": "市民参观公园。", "shot_id": 2,
            }])
            self.assertEqual(response.status_code, 202, response.text)
            await self.finish()
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        self.assertEqual(FakeTTS.calls, ["市民参观公园。"])
        timing = read_json(self.root, "timings.json")[0]
        self.assertEqual(timing["audio_kind"], "tts")
        self.assertTrue(timing["audio_path"].startswith("workbench_audio/"))
        self.assertEqual(read_json(self.root, "workbench_audio.json")["0"], {"audio_source": "tts"})
        self.assertEqual(read_json(self.root, "narration_profile.json")["audio_sources"]["0"], {"audio_source": "tts"})
        context = (await self.client.get(self.url + "/context")).json()
        self.assertEqual(context["timings"][0]["audio_source"], "tts")
        self.assertNotIn("transcript_verified", context["timings"][0])
        self.assertEqual(context["report"]["rows"][0]["audio_source"], "tts")
        for revision, source in ((0, "recording"), (1, "tts")):
            version = (await self.client.get(self.url + f"/versions/{revision}/report")).json()
            self.assertEqual(version["report"]["rows"][0]["audio_source"], source)
        self.assertEqual((self.root / "student_narration.json").read_bytes(), original_manifest)
        response = await self.client.post(self.url + "/restore", json={"revision": 0, "expected_revision": 1})
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.record.revision, 2, self.record.error_message)
        context = (await self.client.get(self.url + "/context")).json()
        self.assertEqual(context["timings"][0]["audio_source"], "recording")
        self.assertTrue(context["timings"][0]["transcript_verified"])
        version = (await self.client.get(self.url + "/versions/1/report")).json()
        self.assertEqual(version["report"]["rows"][0]["audio_source"], "tts")

    async def test_student_text_edit_replaces_recording_provenance_with_tts(self):
        await self.student_text_edit()

    async def test_student_text_and_pacing_edit_replaces_recording_provenance_with_tts(self):
        await self.student_text_edit(pacing="fast")

    async def test_student_pacing_only_preserves_verified_recording_provenance(self):
        await self.real_media()
        self.student_narration_fixture()
        response = await self.submit(pacing="fast")
        self.assertEqual(response.status_code, 202, response.text)
        await self.finish()
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        context = (await self.client.get(self.url + "/context")).json()
        timing = context["timings"][0]
        self.assertEqual(timing["audio_kind"], "sync")
        self.assertEqual(timing["audio_source"], "recording")
        self.assertTrue(timing["transcript_verified"])
        self.assertLess(timing["duration"], 1)
        self.assertTrue(read_json(self.root, "timings.json")[0]["audio_path"].startswith("workbench_audio/"))
        self.assertEqual(context["report"]["rows"][0]["audio_source"], "recording")

    async def test_recording_rejects_size_duration_invalid_content_and_sentence(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg required")
        for filename, content, expected in (("bad.txt", b"bad", 415), ("bad.wav", b"not media", 422),
                                             ("empty.wav", b"", 422), ("huge.wav", b"x" * (20 * 1024**2 + 1), 413)):
            response = await self.client.post(self.url + "/recordings", data={"sentence_id": 0, "expected_revision": 0}, files={"file": (filename, content)})
            self.assertEqual(response.status_code, expected, response.text)
            self.assertEqual(self.record.status, TaskState.done)
        with patch("backend.workbench.probe_audio_duration", return_value=121):
            response = await self.client.post(self.url + "/recordings", data={"sentence_id": 0, "expected_revision": 0}, files={"file": ("voice.wav", wav_bytes(.1))})
            self.assertEqual(response.status_code, 422)
        response = await self.client.post(self.url + "/recordings", data={"sentence_id": 99, "expected_revision": 0}, files={"file": ("voice.wav", wav_bytes(.1))})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(list((self.root / "recordings").glob("*.wav")))

    async def test_real_shot_only_swap_preserves_narration_and_subtitles(self):
        await self.real_media()
        before = {name: (self.root / name).read_bytes() for name in ("narration.m4a", "subs.ass", "timings.json", "tts/0.wav", "tts/1.wav")}
        response = await self.submit(edits=[{"sentence_id": 0, "shot_id": 2}])
        self.assertEqual(response.status_code, 202)
        await self.finish()
        self.assertEqual(self.record.revision, 1, self.record.error_message)
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content, name)
        report = read_json(self.root, "report.json")
        self.assertEqual(report["rows"][0]["shot_id"], 2)
        self.assertEqual(report["rows"][0]["confidence"], 0)
        self.assertTrue(report["rows"][0]["is_fallback"])

    async def test_real_browser_recording_formats(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("FFmpeg required")
        source = self.root / "input.wav"
        source.write_bytes(wav_bytes(.25))
        for extension, codec in (("webm", "libopus"), ("ogg", "libopus"), ("mp3", "libmp3lame"), ("m4a", "aac")):
            encoded = self.root / f"input.{extension}"
            await run_logged_command(["ffmpeg", "-y", "-i", str(source), "-c:a", codec, str(encoded)], self.root, "Test recording format")
            response = await self.client.post(self.url + "/recordings", data={"sentence_id": 0, "expected_revision": 0},
                                              files={"file": (f"voice.{extension}", encoded.read_bytes())})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertAlmostEqual(response.json()["duration"], .25, delta=.1)

    async def test_persist_failure_after_render_removes_uncommitted_version(self):
        snapshot_revision(self.record)
        original_persist = self.manager._persist_record
        def persist(record):
            if record.revision == 1:
                raise OSError("injected persistence failure")
            original_persist(record)
        async def edited(work, *args):
            (work.task_dir / "final.mp4").write_bytes(b"new-video")
            work.script = "changed"
        with patch("backend.workbench._edit_workspace", side_effect=edited), patch.object(self.manager, "_persist_record", side_effect=persist):
            response = await self.submit(edits=[{"sentence_id": 0, "shot_id": 2}])
            self.assertEqual(response.status_code, 202)
            await self.finish()
        self.assertEqual(self.record.status, TaskState.done)
        self.assertEqual(self.record.revision, 0)
        self.assertEqual(self.record.script, "城市新闻。\n公园开放。")
        self.assertEqual((self.root / "final.mp4").read_bytes(), b"old-final")
        self.assertFalse((self.root / "revisions/r1").exists())
        self.assertEqual(read_json(self.root, "task_state.json")["status"], "done")

    async def test_unauthorized_mutations_and_foreign_recordings(self):
        for endpoint, body in (("edit", {"expected_revision": 0, "keep_sentence_ids": [0]}),
                               ("restore", {"expected_revision": 0, "revision": 0})):
            response = await self.client.post(self.url + "/" + endpoint, json=body, headers={"authorization": "wrong"})
            self.assertEqual(response.status_code, 403)
        directory = self.root / "recordings"
        directory.mkdir()
        recording_id = "a" * 32
        (directory / f"{recording_id}.wav").write_bytes(wav_bytes(.1))
        write_json_atomic(directory / f"{recording_id}.json", {"sentence_id": 1, "revision": 0})
        response = await self.submit(edits=[{"sentence_id": 0, "recording_id": recording_id}])
        self.assertEqual(response.status_code, 422)

    async def test_snapshot_copy_is_immutable_and_quota_is_enforced(self):
        snapshot_revision(self.record)
        old = (self.root / "revisions/r0/tts/0.wav").read_bytes()
        (self.root / "tts/0.wav").write_bytes(b"overwritten")
        self.assertEqual((self.root / "revisions/r0/tts/0.wav").read_bytes(), old)
        self.record.revision = 1
        with patch("backend.revisions.MAX_SNAPSHOT_BYTES", 1):
            with self.assertRaises(RevisionError):
                snapshot_revision(self.record)
        self.assertFalse((self.root / "revisions/r1").exists())


if __name__ == "__main__":
    unittest.main()