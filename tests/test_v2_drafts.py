from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend.admission import AdmissionLedger, owner_digest
from backend.config import Settings
from backend.drafts import DraftService, DraftUploadStore, create_draft_router, _retry_binding
from backend.models import TaskState, StageState
from backend.storage import write_json_atomic
from backend.operations import UploadCapacityGuard
from backend.task_manager import TaskManager, TaskRecord
from backend.uploads import _Probe


class DraftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-draft-test-")
        self.root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, app_env="test", data_dir=self.root / "tasks",
                                 asr_cache_dir=self.root / "cache", min_free_disk_gb=0,
                                 enforce_origin_check=False, shutdown_grace_seconds=.01)
        self.settings.data_dir.mkdir()
        self.guard = UploadCapacityGuard(self.settings)
        self.manager = TaskManager(self.settings, self.guard)
        self.store = DraftUploadStore(self.settings)
        self.service = DraftService(self.settings, self.manager, self.store, self.guard)

    async def asyncTearDown(self):
        await self.service.close()
        await self.manager.shutdown()
        await self.store.close()
        self.temp.cleanup()

    async def draft(self, owner="owner", mode="voiceover"):
        receipt = await self.service.create({"mode": mode}, owner_digest(owner))
        return self.manager.get(receipt["task_id"]), receipt

    async def file(self, record, token="", data=b"synthetic-video", sha=True):
        raw = {"name": "source.mp4", "size": len(data)}
        if sha:
            raw["sha256"] = hashlib.sha256(data).hexdigest()
        receipt = await self.service.add_file(record, raw, token)
        item = self.service.item(record, receipt["file_id"])
        async def stream():
            yield data
        await self.store.put_chunk(item["up_id"], item["capability"], 0, stream(), len(data))
        return receipt, item

    async def ready(self, record, *, sha=True):
        receipt, item = await self.file(record, sha=sha)
        async def preprocess(upload):
            upload.probe = _Probe(sec=3, width=64, height=64, fps=30, has_audio=False, format_name="mov")
            upload.has_speech = False
            upload.status, upload.phase, upload.progress = "ready", "done", 100
            self.store._save(upload)
        with patch.object(self.store, "_preprocess", side_effect=preprocess):
            await self.service.file_action(record, item["file_id"], "complete")
            await asyncio.gather(*list(self.store._jobs.values()))
        return receipt, item

    async def test_create_does_not_queue_or_charge(self):
        record, receipt = await self.draft()
        self.assertEqual(receipt["status"], "draft")
        self.assertIsNone(record.background)
        self.assertFalse(self.service.ledger.accepted(record.task_id))
        self.assertEqual(self.manager.operational_snapshot()["active_tasks"], 0)
        self.assertAlmostEqual(record.draft_context["expires_at"] - time.time(), 86400, delta=2)

    async def test_targeted_a_projection_never_commits_editorial(self):
        record, _ = await self.draft()
        script = "新闻标题\n\n今天介绍新的公共服务，让生活更加便利。"
        await self.service.patch(record, {"script": script})
        before = (record.mode, record.script, [s.model_dump() for s in record.sentences],
                  dict(record.draft_context["type_marks"]))
        projected = [{**s.model_dump(), "kind": "quote"} for s in record.sentences]
        result = await self.service.align(record, {"mode": "voiceover", "sentences": projected})
        self.assertEqual([row["kind"] for row in result["matches"]], ["quote", "quote"])
        self.assertTrue(all(row["source"] is None for row in result["matches"]))
        self.assertEqual(result["rules_version"], record.rules_version)
        self.assertEqual((record.mode, record.script, [s.model_dump() for s in record.sentences],
                          record.draft_context["type_marks"]), before)
        for operation in (self.service.patch(record, {"sentences": projected}),
                          self.service.start(record, {"sentences": projected}, "ip")):
            with self.assertRaises(HTTPException) as caught:
                await operation
            self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse(self.service.ledger.accepted(record.task_id))
        self.assertIsNone(record.background)

    async def test_targeted_missing_punctuation_binds_offsets_and_restores(self):
        record, _ = await self.draft(mode="mixed")
        script = "标题\n重复，重复；结束！"
        supplied = [{"idx": i, "text": text, "kind": "narration"}
                    for i, text in enumerate(("重复重复", "结束"))]
        view = await self.service.patch(record, {"script": script, "sentences": supplied})
        self.assertEqual([s["terminal_punctuation"] for s in view["sentences"]], ["；", "！"])
        self.assertEqual(view["script"], script)
        self.assertTrue(all("terminal_punctuation" not in s for s in supplied))
        restored = self.manager._load_record(record.task_dir / "task_state.json", record.task_dir)
        self.assertEqual([s.terminal_punctuation for s in restored.sentences], ["；", "！"])
        self.assertEqual(restored.script, script)

    async def test_targeted_forged_punctuation_rejected_exactly(self):
        record, _ = await self.draft(mode="mixed")
        for wrong in (",", "。", "", "！"):
            with self.subTest(wrong=wrong):
                for starting in (False, True):
                    with self.assertRaises(HTTPException) as caught:
                        self.service.editorial(record, {"script": "标题\n这是一个足够长的公共服务新闻报道正文，",
                            "sentences": [{"idx": 0, "text": "这是一个足够长的公共服务新闻报道正文",
                                           "kind": "narration", "terminal_punctuation": wrong}]}, starting=starting)
                    self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(record.script, "")

    async def test_targeted_manual_kinds_do_not_resegment(self):
        record, _ = await self.draft(mode="mixed")
        script = "标题\n旁白：甲乙，丙丁；戊己。\n【同期】庚辛，壬癸。"
        supplied = [{"idx": i, "text": text, "kind": kind}
                    for i, (text, kind) in enumerate((("甲乙丙丁", "quote"), ("戊", "quote"),
                                                      ("己", "quote"), ("庚辛，壬癸", "narration")))]
        result = self.service.editorial(record, {"script": script, "sentences": supplied,
                                                "type_marks": {"0": "narration", "3": "quote"}}, starting=False)
        self.assertEqual([s.text for s in result["sentences"]], [s["text"] for s in supplied])
        self.assertEqual([s.terminal_punctuation for s in result["sentences"]], ["；", "", "。", "。"])
        self.assertEqual([s.kind for s in result["sentences"]], ["narration", "quote", "quote", "quote"])
        self.assertEqual(result["script"], script)

    async def test_targeted_source_preference_strict_persistence(self):
        record, _ = await self.draft()
        self.assertIs(self.service.view(record)["source_voice_preferred"], False)
        view = await self.service.patch(record, {"source_voice_preferred": True})
        self.assertIs(view["source_voice_preferred"], True)
        await self.service.align(record, {"script": "标题\n预览文字。", "source_voice_preferred": False})
        restored = self.manager._load_record(record.task_dir / "task_state.json", record.task_dir)
        self.assertIs(restored.draft_context["source_voice_preferred"], True)
        for invalid in (1, "true", None):
            with self.assertRaises(HTTPException):
                await self.service.patch(record, {"source_voice_preferred": invalid})
        self.assertIs(record.draft_context["source_voice_preferred"], True)

    async def test_targeted_ready_statistics_exclude_incomplete_files(self):
        record, _ = await self.draft()
        await self.ready(record)
        _, item = await self.file(record, data=b"pending")
        upload = self.store.authorize(item["up_id"], item["capability"])
        upload.probe = _Probe(sec=99, width=64, height=64, fps=30, has_audio=True, format_name="mov")
        self.store._save(upload)
        for status in ("uploading", "asr_failed", "interrupted"):
            upload.status = status
            self.store._save(upload)
            view = self.service.view(record)
            self.assertEqual(view["stats"], {"files": 1, "bytes": len(b"synthetic-video"), "seconds": 3})
            self.assertFalse(view["ready"])
        await self.service.file_action(record, item["file_id"], "delete")
        self.assertTrue(self.service.view(record)["ready"])
        empty, _ = await self.draft()
        self.assertEqual(self.service.view(empty)["stats"], {"files": 0, "bytes": 0, "seconds": 0})
        self.assertFalse(self.service.view(empty)["ready"])

    async def test_targeted_task_speech_readiness_persisted_without_inference(self):
        record, _ = await self.draft()
        with patch.object(self.store, "materialize", side_effect=AssertionError("no copying")), \
                patch("backend.mode_pipeline.analyze_task_speakers", side_effect=AssertionError("no inference")):
            result = await self.service.align(record, {"script": "标题\n预览文字。"})
        speech = result["local_speech"]
        self.assertFalse(speech["available"])
        self.assertFalse(speech["inference_verified"])
        self.assertIn("missing_local_speaker_onnx", speech["blocked_prerequisites"])
        restored = self.manager._load_record(record.task_dir / "task_state.json", record.task_dir)
        self.assertEqual(json.loads(json.dumps(speech)), restored.draft_context["local_speech"])
        self.assertEqual(result["rules_version"], 2)
        self.assertEqual(record.speakers, [])

    async def test_targeted_a_and_c_share_caption_source_hints_not_ability(self):
        from backend.production_modes import TranscriptSegment
        results = []
        for mode in ("voiceover", "original"):
            record, _ = await self.draft(mode=mode)
            _, item = await self.ready(record)
            upload = self.store.authorize(item["up_id"], item["capability"])
            # Explicitly synthetic transcript evidence, no ASR/model claim.
            upload.has_speech = True
            upload.transcript.segments = [TranscriptSegment(id="seg_0", start=.2, end=2.5,
                text="今天介绍公共服务。", speaker_id="", words=[])]
            self.store._save(upload)
            result = await self.service.align(record, {"script": "标题\n今天介绍公共服务。",
                "prefs": {"quote_caption": "spoken"}, "sentences": [{"idx": 0, "text": "今天介绍公共服务",
                    "kind": "quote", "source_hint": {"upload_id": item["up_id"], "seg_id": "seg_0"}}]})
            row = result["matches"][0]
            self.assertIsNotNone(row["source"])
            self.assertEqual(row["source"]["upload_id"], item["up_id"])
            self.assertEqual(row["source"]["precision"], "segment")
            self.assertEqual(row["source"]["words"], [])
            self.assertFalse(result["local_speech"]["inference_verified"])
            self.assertEqual(record.mode, mode)
            self.assertEqual(record.sentences, [])
            results.append((row["score"], row["source"]["asr_text"], row["source"]["start"], row["source"]["end"]))
        self.assertEqual(results[0], results[1])

    async def test_restore_draft_without_provider(self):
        record, _ = await self.draft()
        await self.service.patch(record, {"script": "新闻标题\n\n今天介绍新的公共服务。", "prefs": {"quote_caption": "spoken"}})
        manager = TaskManager(self.settings, self.guard)
        self.assertEqual(manager.restore_tasks(), 1)
        restored = manager.get(record.task_id)
        self.assertEqual(restored.status, TaskState.draft)
        self.assertEqual(restored.preferences.quote_caption, "asr")
        self.assertIsNone(restored.background)

    async def test_file_capability_not_exposed(self):
        record, task = await self.draft()
        receipt, item = await self.file(record, task["access_token"])
        self.assertEqual(receipt["token"], task["access_token"])
        self.assertEqual(receipt["chunksize"], 8 * 1024 * 1024)
        self.assertNotIn(item["capability"], json.dumps(self.service.view(record)))
        other, _ = await self.draft("other")
        with self.assertRaises(HTTPException) as caught:
            self.service.item(other, receipt["file_id"])
        self.assertEqual(caught.exception.status_code, 404)

    async def test_optional_sha_uses_real_assembler(self):
        record, _ = await self.draft()
        _, item = await self.ready(record, sha=False)
        upload = self.store.authorize(item["up_id"], item["capability"])
        self.assertEqual(upload.sha256, hashlib.sha256(b"synthetic-video").hexdigest())
        self.assertEqual(self.store._source(upload).read_bytes(), b"synthetic-video")

    async def test_chunk_idempotence_and_conflicting_bytes(self):
        record, _ = await self.draft()
        _, item = await self.file(record, data=b"abcd")
        async def same():
            yield b"abcd"
        async def other():
            yield b"ABCD"
        await self.store.put_chunk(item["up_id"], item["capability"], 0, same(), 4)
        with self.assertRaises(HTTPException) as caught:
            await self.store.put_chunk(item["up_id"], item["capability"], 0, other(), 4)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_incomplete_start_422_no_quota(self):
        record, _ = await self.draft()
        await self.file(record)
        with self.assertRaises(HTTPException) as caught:
            await self.service.start(record, {"script": "新闻标题\n\n今天介绍新的公共服务。"}, "ip")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse(self.service.ledger.accepted(record.task_id))

    async def test_start_once_then_explicit_retry_capability(self):
        record, _ = await self.draft()
        await self.ready(record)
        async def prepare(directory, assets, options, voice, settings):
            return assets
        async def fail_pipeline(record):
            record.status = TaskState.failed
            await self.manager._release_disk_reservation(record)
        with patch("backend.drafts.prepare_media_inputs", side_effect=prepare), patch.object(self.manager, "_run", side_effect=fail_pipeline):
            result = await self.service.start(record, {"script": "新闻标题\n\n今天介绍新的公共服务，让生活更加便利。"}, "ip")
            self.assertTrue(result["accepted"])
            await record.background
        with self.assertRaises(HTTPException) as caught:
            await self.service.start(record, {}, "ip")
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(HTTPException) as caught:
            await self.service.retry(record, {})
        self.assertEqual(caught.exception.detail["code"], "retry_same_unavailable")
        self.assertTrue(self.service.ledger.accepted(record.task_id))

    async def test_title_40_and_derived_prefs(self):
        record, _ = await self.draft()
        for raw in ({"script": "题" * 41 + "\n\n今天介绍公共服务。"},
                    {"prefs": {"target_cpm": 999}}, {"prefs": {"grade": "primary"}}):
            with self.assertRaises(HTTPException) as caught:
                self.service.editorial(record, raw, starting=True)
            self.assertEqual(caught.exception.status_code, 422)
        data = self.service.editorial(record, {"prefs": {"pacing": "fast", "target_cpm": 290}}, starting=False)
        self.assertEqual(data["preferences"].target_chars_per_minute, 290)

    async def test_server_quality_policy(self):
        record, _ = await self.draft()
        self.settings.quality_gate_mode = "block"
        with self.assertRaises(HTTPException):
            self.service.editorial(record, {"quality_gate_mode": "warn"}, starting=False)
        self.assertEqual(self.service.editorial(record, {}, starting=False)["quality_gate_mode"], "block")

    async def test_speakers_name_role_and_align_same_function(self):
        record, _ = await self.draft(mode="original")
        result = await self.service.align(record, {"script": "新闻标题\n\n今天介绍新的公共服务。",
            "speaker_names": {"speaker1": {"name": "张三", "role": "负责人"}}})
        self.assertEqual(len(result["matches"]), 1)
        self.assertIsNone(result["matches"][0]["source"])
        data = self.service.editorial(record, {"speaker_names": {"speaker1": {"name": "张三", "role": "负责人"}}}, starting=False)
        self.assertEqual(data["speakers"][0].title, "负责人")

    async def test_original_missing_quote_rejected_before_quota(self):
        record, _ = await self.draft(mode="original")
        await self.ready(record)
        with self.assertRaises(HTTPException) as caught:
            await self.service.start(record, {"script": "新闻标题\n\n今天介绍新的公共服务。"}, "ip")
        self.assertEqual(caught.exception.detail["code"], "quote_missing")
        self.assertFalse(self.service.ledger.accepted(record.task_id))

    async def test_manual_pretranscribe_never_implicit(self):
        record, _ = await self.draft()
        _, item = await self.ready(record)
        upload = self.store.authorize(item["up_id"], item["capability"])
        upload.status = "asr_failed"
        self.store._save(upload)
        job = AsyncMock()
        with patch.object(self.store, "_preprocess", job):
            self.service.view(record)
            await self.service.file_action(record, item["file_id"], "complete")
            job.assert_not_called()
            with self.assertRaises(HTTPException):
                await self.service.file_action(record, item["file_id"], "pretranscribe", raw={})
            await self.service.file_action(record, item["file_id"], "pretranscribe", raw={"retry": True})
            await asyncio.gather(*list(self.store._jobs.values()))
            job.assert_awaited_once()

    async def test_expiry_only_v2_and_owned_tombstone(self):
        record, _ = await self.draft()
        legacy = TaskRecord("b" * 32, self.settings.data_dir / ("b" * 32), "legacy", [], local_only=True)
        legacy.task_dir.mkdir()
        self.manager._tasks[legacy.task_id] = legacy
        self.assertEqual(await self.service.cleanup(now=time.time() + 86401), [record.task_id])
        self.assertIs(self.manager.get(legacy.task_id), legacy)
        self.assertEqual(len(self.service.ledger.history(owner_digest("owner"))), 1)
        self.assertEqual(self.service.ledger.history(owner_digest("other")), [])
        self.assertEqual(self.service.ledger.history(owner_digest("owner"), now=time.time() + 31 * 86400), [])

    async def test_delete_releases_task_and_upload(self):
        record, _ = await self.draft()
        _, item = await self.file(record)
        await self.service.delete(record)
        self.assertIsNone(self.manager.get(record.task_id))
        self.assertFalse(record.task_dir.exists())
        self.assertNotIn(item["up_id"], self.store._records)

    async def test_directory_replacement_fails_closed(self):
        record, _ = await self.draft()
        record.task_dir.rename(record.task_dir.with_name("old-task"))
        record.task_dir.mkdir()
        with self.assertRaises(HTTPException):
            self.service.view(record)

    async def test_queue_v2_50_legacy_5(self):
        for i in range(50):
            key = f"{i:032x}"
            self.manager._tasks[key] = TaskRecord(key, self.root / key, "", [])
        with self.assertRaises(RuntimeError):
            self.manager.ensure_start_capacity(v2=True)
        self.manager._tasks.pop(f"{0:032x}")
        self.manager.ensure_start_capacity(v2=True)
        with self.assertRaises(RuntimeError):
            self.manager.ensure_start_capacity()

    async def test_concurrent_visitors_wait_for_the_draft_gate_instead_of_failing(self):
        import asyncio
        receipts = await asyncio.gather(*(self.service.create({"mode": "voiceover"}, owner_digest(f"visitor-{index}"))
                                          for index in range(3)))
        self.assertEqual(len({receipt["task_id"] for receipt in receipts}), 3)

    async def test_start_limit_refusal_says_how_long_to_wait_and_a_local_limit_is_honoured(self):
        ledger = self.service.ledger
        ledger.accept("a", "owner", "ip", "hash", now=10000)
        ledger.accept("b", "owner", "ip", "hash", now=10001)
        with self.assertRaises(HTTPException) as caught:
            ledger.accept("c", "owner", "ip", "hash", now=10002)
        detail = caught.exception.detail
        self.assertEqual(detail["code"], "start_budget")
        self.assertEqual(detail["retry_after"], 3599)
        self.assertEqual(caught.exception.headers["Retry-After"], "3599")
        self.assertIn("约 60 分钟后", detail["message"]); self.assertIn("不需要重新上传", detail["message"])
        # A local install that configured more starts per hour gets them (no hidden cap of 2).
        ledger.accept("c", "owner", "ip", "hash", now=10003, session_limit=3)
        with self.assertRaises(HTTPException):
            ledger.accept("d", "owner", "ip", "hash", now=10004, session_limit=3)

    def test_effective_session_start_limit_keeps_the_public_cap_only_in_production(self):
        from backend.config import Settings
        self.assertEqual(Settings.model_construct(app_env="development", anonymous_session_task_rate_limit_per_hour=10).effective_session_start_limit, 10)
        self.assertEqual(Settings.model_construct(app_env="production", anonymous_session_task_rate_limit_per_hour=10).effective_session_start_limit, 2)
        self.assertEqual(Settings.model_construct(app_env="production", anonymous_session_task_rate_limit_per_hour=1).effective_session_start_limit, 1)

    async def test_ledger_restart_sliding_nonrefund(self):
        ledger = self.service.ledger
        ledger.accept("a", "owner", "ip", "hash", now=10000)
        ledger.accept("b", "owner", "ip", "hash", now=10001)
        restarted = AdmissionLedger(self.settings.data_dir)
        with self.assertRaises(HTTPException) as caught:
            restarted.accept("c", "owner", "ip", "hash", now=10002)
        self.assertEqual(caught.exception.status_code, 429)
        restarted.accept("c", "owner", "ip", "hash", now=13601)
        with self.assertRaises(HTTPException) as caught:
            restarted.accept("a", "owner", "ip", "hash", now=20000)
        self.assertEqual(caught.exception.status_code, 409)

    async def test_main_routes_draft_etag_ownership_and_chunk_limit(self):
        from backend import main
        with patch.multiple(main, settings=self.settings, task_manager=self.manager,
                            upload_capacity_guard=self.guard, _upload_store=self.store,
                            _draft_service=self.service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
                response = await client.post("/api/tasks", json={"mode": "voiceover"})
                self.assertEqual(response.status_code, 201, response.text)
                receipt = response.json()
                path = "/api/tasks/" + receipt["task_id"]
                headers = {"X-Task-Token": receipt["access_token"]}
                get = await client.get(path, headers=headers)
                self.assertEqual(get.status_code, 200, get.text)
                cached = await client.get(path, headers={**headers, "If-None-Match": get.headers["etag"]})
                self.assertEqual(cached.status_code, 304)
                self.assertEqual((await client.get(path)).status_code, 404)
                history = await client.get("/api/tasks?limit=100")
                self.assertEqual(history.json()["total"], 1)
                added = await client.post(path + "/files", headers=headers, json={"name": "source.mp4", "size": 300000})
                self.assertEqual(added.status_code, 201, added.text)
                uploaded = await client.put(path + "/files/" + added.json()["file_id"] + "/chunks/0", headers=headers, content=b"a" * 300000)
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                rejected = await client.post(path + "/start", headers=headers, json={"script": "新闻标题\n\n今天介绍新的公共服务。"})
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertFalse(self.service.ledger.accepted(receipt["task_id"]))
                other = await client.get(path, headers={"X-Task-Token": "bad"})
                self.assertEqual(other.status_code, 404)
                unknown = await client.post(path + "/unknown", headers=headers, json={})
                self.assertEqual(unknown.status_code, 404)

    async def test_real_silent_video_preprocess_without_asr(self):
        from backend.media import run_logged_command
        source = self.root / "fixture.mp4"
        await run_logged_command(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=25",
            "-t", "1", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], self.root, "synthetic fixture")
        record, _ = await self.draft()
        _, item = await self.file(record, data=source.read_bytes(), sha=False)
        with patch("backend.uploads.create_asr_provider", side_effect=AssertionError("silent fixture must not call ASR")):
            await self.service.file_action(record, item["file_id"], "complete")
            await asyncio.gather(*list(self.store._jobs.values()))
        view = self.service.file_view(record, item)
        self.assertEqual(view["status"], "ready", view)
        self.assertIs(view["has_speech"], False)
        self.assertTrue(view["probe_ok"])
        self.assertIsNotNone(view["thumb_url"])

    async def test_same_draft_concurrent_start_accepts_only_once(self):
        record, _ = await self.draft()
        await self.ready(record)
        entered, release = asyncio.Event(), asyncio.Event()
        async def prepare(directory, assets, options, voice, settings):
            entered.set()
            await release.wait()
            return assets
        async def runner(record):
            record.status = TaskState.failed
            await self.manager._release_disk_reservation(record)
        with patch("backend.drafts.prepare_media_inputs", side_effect=prepare), patch.object(self.manager, "_run", side_effect=runner):
            first = asyncio.create_task(self.service.start(record, {"script": "新闻标题\n\n今天介绍新的公共服务，让生活更加便利。"}, "ip"))
            await asyncio.wait_for(entered.wait(), 10)
            with self.assertRaises(HTTPException) as caught:
                await self.service.start(record, {}, "ip")
            self.assertEqual(caught.exception.status_code, 409)
            release.set()
            await first
            await record.background
        self.assertTrue(self.service.ledger.accepted(record.task_id))

    async def test_preaccept_cancel_releases_disk_and_copies(self):
        record, _ = await self.draft()
        await self.ready(record)
        entered = asyncio.Event()
        async def prepare(*args):
            entered.set()
            await asyncio.Future()
        with patch("backend.drafts.prepare_media_inputs", side_effect=prepare):
            task = asyncio.create_task(self.service.start(record, {"script": "新闻标题\n\n今天介绍新的公共服务，让生活更加便利。"}, "ip"))
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(self.service.ledger.accepted(record.task_id))
        self.assertEqual(self.guard._reserved_bytes, 0)
        self.assertEqual(list((record.task_dir / "raw").iterdir()), [])

    async def test_expired_upload_does_not_break_accepted_status(self):
        record, _ = await self.draft()
        _, item = await self.ready(record)
        assets, _ = await self.store.materialize([item["up_id"]], {item["up_id"]: item["capability"]}, record.task_dir)
        record.uploads = assets
        record.status = TaskState.failed
        upload = self.store.authorize(item["up_id"], item["capability"])
        upload.expires_at = time.time() - 1
        view = self.service.view(record)
        self.assertEqual(view["status"], "failed")
        self.assertEqual(view["files"][0]["status"], "expired")

    async def test_failed_save_restores_draft_memory(self):
        record, _ = await self.draft()
        with patch.object(self.manager, "_persist_record", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await self.service.patch(record, {"script": "changed"})
        self.assertEqual(record.script, "")

    async def test_legacy_orphan_cleanup_excludes_v2(self):
        record, _ = await self.draft()
        self.assertIsNone(self.manager._orphan_retention_reference(record.task_dir))

    async def test_recovery_accepted_draft_never_autostarts(self):
        record, _ = await self.draft()
        self.service.ledger.accept(record.task_id, record.owner_hash, "ip", "hash")
        recovered = DraftService(self.settings, self.manager, self.store, self.guard)
        self.assertEqual(record.status, TaskState.failed)
        self.assertIsNone(record.background)
        self.assertTrue(recovered.view(record)["accepted"])
        await recovered.close()

    async def test_sentence_only_alignment(self):
        record, _ = await self.draft(mode="original")
        result = await self.service.align(record, {"sentences": [{"idx": 0, "text": "今天介绍公共服务。", "kind": "quote"}]})
        self.assertEqual(result["matches"][0]["kind"], "quote")

    async def test_close_cancels_and_drains_owned_start(self):
        record, _ = await self.draft()
        await self.ready(record)
        entered = asyncio.Event()
        async def prepare(*args):
            entered.set()
            await asyncio.Future()
        with patch("backend.drafts.prepare_media_inputs", side_effect=prepare):
            task = asyncio.create_task(self.service.start(record, {"script": "新闻标题\n\n今天介绍新的公共服务，让生活更加便利。"}, "ip"))
            await asyncio.wait_for(entered.wait(), 10)
            await self.service.close()
            self.assertTrue(task.cancelled())
        self.assertEqual(self.guard._reserved_bytes, 0)
        self.assertFalse(self.service.ledger.accepted(record.task_id))

    async def test_terminal_expiry_keeps_nonrefundable_ledger(self):
        record, _ = await self.draft()
        record.status = TaskState.failed
        self.service.ledger.accept(record.task_id, record.owner_hash, "ip", "hash")
        with patch.object(self.service.ledger, "tombstone", wraps=self.service.ledger.tombstone) as tombstone:
            removed = await self.service.cleanup(now=time.time() + 73 * 3600)
        tombstone.assert_called_once_with(record.task_id, record.owner_hash, record.access_token_hash,
                                         "task_retention_expiry")
        self.assertEqual(removed, [record.task_id])
        self.assertTrue(self.service.ledger.accepted(record.task_id))
        self.assertEqual(self.service.ledger.history(record.owner_hash), [{"id": record.task_id,
                         "task_id": record.task_id, "status": "gone", "title": "未命名视频"}])

    async def test_failed_admission_restores_input_and_cleans_new_raw(self):
        record, _ = await self.draft()
        await self.ready(record)
        async def prepare(directory, assets, *args):
            return assets
        with patch("backend.drafts.prepare_media_inputs", side_effect=prepare), patch.object(self.service.ledger, "accept", side_effect=HTTPException(429, "full")):
            with self.assertRaises(HTTPException):
                await self.service.start(record, {"script": "新闻标题\n\n今天介绍新的公共服务，让生活更加便利。"}, "ip")
        self.assertEqual(record.script, "")
        self.assertEqual(record.uploads, [])
        self.assertEqual(list((record.task_dir / "raw").iterdir()), [])
        self.assertEqual(self.guard._reserved_bytes, 0)

    async def test_shared_legacy_budget_is_not_a_second_pool(self):
        with self.assertRaises(HTTPException) as caught:
            self.service.ledger.accept("a", "owner", "ip", "hash", legacy_counts=(10, 0, 0))
        self.assertEqual(caught.exception.status_code, 429)
        self.assertFalse(self.service.ledger.accepted("a"))
        self.service.ledger.accept("b", "owner", "ip", "hash")
        self.assertEqual(self.service.ledger.counts("owner", "ip"), (1, 1, 1))

    async def test_storage_root_replacement_is_not_followed(self):
        original = self.settings.data_dir
        moved = original.with_name("moved-tasks")
        # UploadStore owns an open lock file on Windows; test the ledger's own
        # directory independently rather than pretending that a locked root can move.
        ledger_root = original / "isolated-ledger"
        ledger_root.mkdir()
        ledger = AdmissionLedger(ledger_root)
        ledger_root.rename(moved)
        ledger_root.mkdir()
        with self.assertRaises(HTTPException):
            ledger.accepted("unknown")

    async def test_editorial_v2_boundaries(self):
        record, _ = await self.draft()
        for size in (21, 40):
            self.assertEqual(self.service.editorial(record, {"script": "题" * size + "\n正文"}, starting=True)["script"].splitlines()[0], "题" * size)
        for text in ("标题\n短文", "（写一个标题）\n这是一个足够长但标题仍然是占位内容的正文。", "题" * 41 + "\n正文"):
            with self.assertRaises(HTTPException):
                self.service.editorial(record, {"script": text}, starting=True)
        self.assertEqual(len(self.service.editorial(record, {"mode": "original", "script": "题\n字"}, starting=True)["sentences"]), 1)
        for person in ({"id": "s" * 129}, {"id": "s1", "name": "名" * 9}, {"id": "s1", "title": "职" * 13}):
            with self.assertRaises(HTTPException):
                self.service.editorial(record, {"speakers": [person]}, starting=False)
        self.service.editorial(record, {"speakers": [{"id": "s" * 8, "name": "名" * 8, "title": "职" * 12}]}, starting=False)

    async def test_upload_scoped_speaker_identity_roundtrip(self):
        from backend.uploads import _scoped_speaker_id
        record, _ = await self.draft(mode="original")
        identity = _scoped_speaker_id("up_" + "a" * 32, "synthetic-provider-speaker")
        self.assertEqual(len(identity), 104)
        for identifier in (identity, "s" * 128):
            for supplied in ({"speakers": [{"id": identifier, "name": "名" * 8, "title": "职" * 12}]},
                             {"speaker_names": {identifier: {"name": "名" * 8, "role": "职" * 12}}}):
                with self.subTest(length=len(identifier), field=next(iter(supplied))):
                    data = self.service.editorial(record, {"script": "合成标题\n原声内容。", **supplied}, starting=True)
                    self.assertEqual(data["speakers"][0].id, identifier)
                    self.assertEqual(data["speakers"][0].name, "名" * 8)
                    self.assertEqual(data["speakers"][0].title, "职" * 12)
        for identifier in ("s" * 129, "bad\nidentity", "bad\x7fidentity", " "):
            for supplied in ({"speakers": [{"id": identifier}]}, {"speaker_names": {identifier: "名字"}}):
                with self.subTest(invalid=repr(identifier), field=next(iter(supplied))):
                    with self.assertRaises(HTTPException) as caught:
                        self.service.editorial(record, supplied, starting=False)
                    self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(record.speakers, [])
        self.assertFalse(self.service.ledger.accepted(record.task_id))

    async def test_ordered_options_strict_and_persisted(self):
        record, _ = await self.draft()
        await self.ready(record)
        await self.ready(record)
        for options in ([], [{"trim_start": True}, {}], [{"trim_start": "1"}, {}],
                        [{"trim_end": float("nan")}, {}], [{"note": "a" * 21}, {}],
                        [{"note": "x\n"}, {}], [{"trim_end": 4}, {}], [{"upload_id": "foreign"}, {}]):
            with self.assertRaises(HTTPException):
                await self.service.patch(record, {"asset_options": options})
        options = [{"note": "first", "trim_start": .25, "trim_end": 2}, {"note": "second", "trim_start": 0, "trim_end": 3}]
        view = await self.service.patch(record, {"asset_options": options})
        self.assertEqual(view["asset_options"], options)
        restored = self.manager._load_record(record.task_dir / "task_state.json", record.task_dir)
        self.assertEqual(restored.draft_context["files"][0]["in_sec"], .25)
        self.assertEqual(restored.draft_context["files"][1]["note"], "second")

    def router(self):
        app = FastAPI()
        async def authorize(request, task_id, write=False):
            record = self.manager.authorize(task_id, request.headers.get("x-task-token"))
            if record is None:
                raise HTTPException(404)
            return record
        app.include_router(create_draft_router(lambda: self.service, authorize))
        return app

    async def recovery_fixture(self):
        from backend.production_modes import TranscriptSegment
        record, receipt = await self.draft(owner=f"recovery-owner-{len(self.manager._tasks)}", mode="mixed")
        _, item = await self.ready(record)
        upload = self.store.authorize(item["up_id"], item["capability"])
        upload.probe.audio_offset = .125
        upload.has_speech = True
        upload.transcript.segments = [TranscriptSegment(id="seg_0", start=.25, end=2.75,
            text="今天介绍新的公共服务，让生活更加便利。", speaker_id="S1", words=[])]
        self.store._save(upload)
        await self.service.patch(record, {"script": "新闻标题\n今天介绍新的公共服务，让生活更加便利。",
            "preferences": {"voice": "mine", "background_music": True}, "speakers": [{"id": "S1", "name": "记者"}],
            "asset_options": [{"note": "原素材", "trim_start": .2, "trim_end": 2.9}]})
        record.uploads, snapshots = await self.store.materialize([item["up_id"]], {item["up_id"]: item["capability"]}, record.task_dir)
        record.upload_ids = [item["up_id"]]
        write_json_atomic(record.task_dir / "pretranscripts.json", snapshots)
        record.draft_context["creation_inputs"] = self.service.creation_inputs(record)
        self.service.ledger.accept(record.task_id, record.owner_hash, "ip", "hash")
        record.status = TaskState.failed
        self.manager._persist_record(record)
        return record, receipt, item

    async def test_recovery_new_capabilities_two_failed_sources_and_persistence(self):
        first, cap1, _ = await self.recovery_fixture()
        second, cap2, _ = await self.recovery_fixture()
        before = {r.task_id: (r.task_dir / "task_state.json").read_bytes() for r in (first, second)}
        counts = self.service.ledger.counts(first.owner_hash, "ip")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.router()), base_url="http://testserver") as client:
            denied = await client.post(f"/api/tasks/{first.task_id}/recover-draft", headers={"X-Task-Token": cap2["access_token"]}, json={"step": 1})
            self.assertEqual(denied.status_code, 404)
            recovered = []
            with patch.object(self.store, "_preprocess", side_effect=AssertionError("No ASR")), \
                    patch.object(self.manager, "start_task", side_effect=AssertionError("No queue")):
                for source, cap in ((first, cap1), (second, cap2)):
                    result = await client.post(f"/api/tasks/{source.task_id}/recover-draft", headers={"X-Task-Token": cap["access_token"]}, json={"step": 2})
                    self.assertEqual(result.status_code, 201, result.text)
                    view = result.json()
                    recovered.append(view)
                    self.assertNotEqual(view["task_id"], source.task_id)
                    self.assertNotEqual(view["access_token"], cap["access_token"])
                    self.assertFalse(view["accepted"])
                    self.assertEqual(view["status"], "draft")
                    self.assertEqual(view["preferences"]["voice"], "mine")
                    self.assertEqual(view["speakers"][0]["name"], "记者")
                    file = view["files"][0]
                    self.assertEqual(file["token"], view["access_token"])
                    self.assertEqual(file["bytes"], len(b"synthetic-video"))
                    self.assertEqual(file["in_sec"], .2)
                    self.assertEqual(file["transcript"]["segments"][0]["start"], .25)
                    draft = self.manager.get(view["task_id"])
                    snapshot = json.loads((draft.task_dir / "pretranscripts.json").read_text(encoding="utf-8"))[0]
                    self.assertEqual(snapshot["audio_offset_seconds"], .125)
                    self.assertEqual(snapshot["source_sha256"], hashlib.sha256(b"synthetic-video").hexdigest())
                    restored = self.manager._load_record(draft.task_dir / "task_state.json", draft.task_dir)
                    self.assertTrue(self.manager.token_matches(restored, view["access_token"]))
                    self.assertEqual(restored.draft_context["recovery_receipt"]["source_task_id"], source.task_id)
                    self.assertIsNone(draft.background)
                    self.assertAlmostEqual(draft.draft_context["expires_at"] - time.time(), 86400, delta=5)
                    for token in (cap1["access_token"], cap2["access_token"]):
                        self.assertEqual((await client.get(f"/api/tasks/{draft.task_id}/draft", headers={"X-Task-Token": token})).status_code, 404)
                    own = {"X-Task-Token": view["access_token"]}
                    self.assertEqual((await client.get(f"/api/tasks/{draft.task_id}/files/{file['file_id']}/status", headers=own)).status_code, 200)
                    self.assertEqual((await client.get(f"/api/tasks/{source.task_id}/draft", headers=own)).status_code, 404)
                    self.assertEqual((await client.patch(f"/api/tasks/{source.task_id}/draft", headers={"X-Task-Token": cap["access_token"]}, json={"script": "changed"})).status_code, 409)
            self.assertEqual((await client.get(f"/api/tasks/{recovered[0]['task_id']}/draft", headers={"X-Task-Token": recovered[1]["access_token"]})).status_code, 404)
        self.assertEqual(self.service.ledger.counts(first.owner_hash, "ip"), counts)
        for r in (first, second):
            self.assertEqual((r.task_dir / "task_state.json").read_bytes(), before[r.task_id])

    async def test_recovery_expired_staging_uses_owned_raw_and_survives_original_deletion(self):
        source, _, item = await self.recovery_fixture()
        await self.store.delete(item["up_id"], item["capability"])
        view = await self.service.recover(source, {"step": 1})
        draft = self.manager.get(view["task_id"])
        await self.service.delete(source)
        self.assertEqual(draft.uploads[0].path.read_bytes(), b"synthetic-video")
        self.assertTrue(self.service.view(draft)["ready"])
        imported = draft.draft_context["files"][0]
        self.assertEqual(self.store._source(self.store.authorize(imported["up_id"], imported["capability"])).read_bytes(), b"synthetic-video")

    async def test_recovery_releases_the_failed_tasks_own_staging_copies_when_the_file_quota_is_full(self):
        source, _, item = await self.recovery_fixture()
        # One live staging copy already fills this session's quota, so a plain copy could never fit.
        self.store.max_files = 1
        view = await self.service.recover(source, {"step": 1})
        draft = self.manager.get(view["task_id"])
        self.assertNotIn(item["up_id"], self.store._records, "redundant staging copy is released")
        self.assertEqual(draft.uploads[0].path.read_bytes(), b"synthetic-video")
        self.assertEqual(self.store._source(self.store.authorize(*[draft.draft_context["files"][0][k] for k in ("up_id", "capability")])).read_bytes(), b"synthetic-video")
        self.assertEqual((source.task_dir / "raw" / source.uploads[0].stored_name).read_bytes(), b"synthetic-video", "original task keeps its files")

    async def test_recovery_can_carry_shot_reuse_consent_only_after_a_shortage(self):
        source, _, _ = await self.recovery_fixture()
        source.error_kind = "shortage"
        plain = await self.service.recover(source, {"step": 1})
        self.assertFalse(plain["shot_reuse_accepted"])
        self.assertNotIn("shot_reuse", self.manager.get(plain["task_id"]).draft_context)
        await self.service.delete(self.manager.get(plain["task_id"]))
        view = await self.service.recover(source, {"step": 1, "allow_shot_reuse": True})
        draft = self.manager.get(view["task_id"])
        self.assertTrue(view["shot_reuse_accepted"])
        self.assertEqual(draft.draft_context["shot_reuse"], {"accepted": True})
        self.assertTrue(view["lifecycle_v2"], "the client needs this flag to pick cache-resume retry")

    async def test_recovery_consent_is_refused_for_other_failures_original_mode_and_non_boolean_values(self):
        source, _, _ = await self.recovery_fixture()
        before = set(self.manager._tasks)
        source.error_kind = "transient"
        with self.assertRaises(HTTPException) as caught:
            await self.service.recover(source, {"step": 1, "allow_shot_reuse": True})
        self.assertEqual(caught.exception.detail["code"], "shot_reuse_not_applicable")
        source.error_kind = "shortage"
        with self.assertRaises(HTTPException) as caught:
            await self.service.recover(source, {"step": 1, "mode": "original", "allow_shot_reuse": True})
        self.assertEqual(caught.exception.detail["code"], "shot_reuse_not_applicable")
        for bad in (1, "true", "yes"):
            with self.assertRaises(HTTPException) as caught:
                await self.service.recover(source, {"step": 1, "allow_shot_reuse": bad})
            self.assertEqual(caught.exception.status_code, 422, bad)
        self.assertEqual(set(self.manager._tasks), before, "a refused recovery creates nothing")

    async def new_draft_for(self, source):
        receipt = await self.service.create({"mode": "voiceover"}, source.owner_hash)
        return self.manager.get(receipt["task_id"])

    async def test_finished_tasks_staging_is_reclaimed_when_it_blocks_the_next_upload(self):
        source, _, item = await self.recovery_fixture()
        self.store.max_files = 1  # the finished work's staging copy fills this session's quota
        draft = await self.new_draft_for(source)
        receipt, new_item = await self.file(draft)
        self.assertNotIn(item["up_id"], self.store._records, "the finished task's redundant staging was released")
        self.assertIn(new_item["up_id"], self.store._records)
        self.assertEqual((source.task_dir / "raw" / source.uploads[0].stored_name).read_bytes(), b"synthetic-video",
                         "the finished task keeps its own copy")

    async def test_staging_is_left_alone_when_there_is_room_or_it_is_still_needed(self):
        source, _, item = await self.recovery_fixture()
        draft = await self.new_draft_for(source)
        await self.file(draft)
        self.assertIn(item["up_id"], self.store._records, "nothing is released while the quota has room")
        for label, change in (("running task", lambda: setattr(source, "status", TaskState.running)),
                              ("missing task copy", lambda: (source.task_dir / "raw" / source.uploads[0].stored_name).unlink())):
            with self.subTest(label):
                change()
                self.store.max_files = len([r for r in self.store._records.values() if r.owner_hash == hashlib.sha256(source.owner_hash.encode()).hexdigest()])
                another = await self.new_draft_for(source)
                with self.assertRaises(HTTPException) as caught:
                    await self.service.add_file(another, {"name": "x.mp4", "size": 1}, "")
                self.assertEqual(caught.exception.status_code, 429)
                self.assertIn(item["up_id"], self.store._records, "never released: " + label)

    async def test_only_the_same_owners_own_finished_staging_is_ever_reclaimed(self):
        mine, _, mine_item = await self.recovery_fixture()
        other, _, other_item = await self.recovery_fixture()
        self.assertNotEqual(mine.owner_hash, other.owner_hash)
        self.store.max_files = 1
        draft = await self.new_draft_for(mine)
        await self.file(draft)
        self.assertNotIn(mine_item["up_id"], self.store._records)
        self.assertIn(other_item["up_id"], self.store._records, "another owner's staging is untouched")

    async def test_recovery_copies_do_not_consume_the_hourly_new_upload_budget(self):
        source, _, _ = await self.recovery_fixture()
        entries = len(self.store._budgets.entries)
        # The fixture already created uploads this hour; a budget of exactly that many is now full.
        with patch("backend.uploads.OWNER_CREATIONS_PER_HOUR", entries):
            with self.assertRaises(HTTPException) as refused:
                await self.store.create(name="x.mp4", size=1, sha256="0" * 64, owner=source.owner_hash)
            self.assertEqual(refused.exception.status_code, 429, "ordinary new uploads are still limited")
            view = await self.service.recover(source, {"step": 1})
        self.assertEqual(self.manager.get(view["task_id"]).status, TaskState.draft)
        self.assertEqual(len(self.store._budgets.entries), entries, "recovery adds no budget entries")

    async def test_recovery_quota_shortage_is_reported_with_a_specific_code_and_creates_nothing(self):
        source, _, _ = await self.recovery_fixture()
        self.store.max_files = 0
        before = set(self.manager._tasks)
        with self.assertRaises(HTTPException) as caught:
            await self.service.recover(source, {"step": 1})
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.detail["code"], "recovery_quota")
        self.assertEqual(set(self.manager._tasks), before)

    async def test_recovery_rejects_unverified_or_foreign_source_without_draft(self):
        for failure in ("tamper", "missing", "owner", "path"):
            with self.subTest(failure=failure):
                source, _, item = await self.recovery_fixture()
                await self.store.delete(item["up_id"], item["capability"])
                if failure == "tamper": source.uploads[0].path.write_bytes(b"x" * len(b"synthetic-video"))
                if failure == "missing": source.uploads[0].path.unlink()
                if failure == "owner": source.draft_context["creation_inputs"]["files"][0]["manifest"]["owner_hash"] = "0" * 64
                if failure == "path": source.uploads[0].stored_name = "../foreign.mp4"
                tasks = set(self.manager._tasks)
                uploads = set(self.store._records)
                with self.assertRaises(HTTPException) as caught:
                    await self.service.recover(source, {"step": 2})
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(set(self.manager._tasks), tasks)
                self.assertEqual(set(self.store._records), uploads)

    async def test_recovery_immutable_creation_not_edited_report_or_current_script(self):
        source, _, _ = await self.recovery_fixture()
        original = source.script
        source.script = "后来修改的稿件"
        source.revision = 3
        (source.task_dir / "report.json").write_text('{"script":"not original"}', encoding="utf-8")
        source.status = TaskState.done
        view = await self.service.recover(source, {"step": 1})
        self.assertEqual(view["script"], original)

    async def test_recovery_strict_request_busy_and_legacy_boundaries(self):
        from backend.task_operations import _operation
        source, _, _ = await self.recovery_fixture()
        for raw in ({}, {"step": True}, {"step": 3}, {"step": 1, "path": "raw/anything"}, {"step": 1, "mode": "legacy"}):
            with self.assertRaises(HTTPException): await self.service.recover(source, raw)
        with _operation(source):
            with self.assertRaises(HTTPException): await self.service.recover(source, {"step": 1})
        for attribute, value in (("rules_version", 1), ("mode_contract", False), ("status", TaskState.running)):
            previous = getattr(source, attribute)
            setattr(source, attribute, value)
            with self.assertRaises(HTTPException): await self.service.recover(source, {"step": 1})
            setattr(source, attribute, previous)
        source.lifecycle_v2 = False
        source.draft_context.pop("creation_inputs")
        self.assertEqual((await self.service.recover(source, {"step": 1}))["status"], "draft")

    async def test_recovery_explicit_mode_resets_quote_semantics_without_queue(self):
        source, _, _ = await self.recovery_fixture()
        view = await self.service.recover(source, {"step": 2, "mode": "original"})
        self.assertEqual(view["mode"], "original")
        self.assertTrue(view["recovery"]["mode_reconfirm"])
        self.assertTrue(all(s["kind"] == "quote" for s in view["sentences"]))
        self.assertEqual(source.mode, "mixed")
        self.assertIsNone(self.manager.get(view["task_id"]).background)

    async def test_recovery_copy_pins_live_source_and_task_against_cleanup(self):
        from backend import drafts
        source, _, item = await self.recovery_fixture()
        live = self.store.authorize(item["up_id"], item["capability"])
        original_bytes = self.store._source(live).read_bytes()
        entered, release = asyncio.Event(), asyncio.Event()
        blocking = drafts._blocking

        async def held(function, *args):
            if function is drafts._copy_verified and not entered.is_set():
                entered.set()
                await release.wait()
            return await blocking(function, *args)

        with patch("backend.drafts._blocking", side_effect=held):
            job = asyncio.create_task(self.service.recover(source, {"step": 1}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                with self.assertRaises(RuntimeError):
                    await self.manager.cancel_and_delete(source.task_id)
                with self.assertRaises(HTTPException):
                    await self.service.delete(source)
                # Automatic staging expiry has no task lock: the upload itself
                # must remain pinned until the cancellation-safe copy drains.
                live.expires_at = time.time() - 1
                self.assertEqual(await self.store.cleanup_owned(), 0)
                self.assertEqual(self.store._source(live).read_bytes(), original_bytes)
            finally:
                live.expires_at = time.time() + 60
                release.set()
                await job

    async def test_recovery_upload_receipt_survives_actual_store_restart(self):
        source, _, _ = await self.recovery_fixture()
        view = await self.service.recover(source, {"step": 1})
        record = self.manager.get(view["task_id"])
        item = record.draft_context["files"][0]
        await self.store.close()
        restored = DraftUploadStore(self.settings)
        try:
            manifest = restored.authorize(item["up_id"], item["capability"])
            self.assertEqual(manifest.status, "ready")
            self.assertEqual(restored._source(manifest).read_bytes(), b"synthetic-video")
            self.assertLessEqual(manifest.expires_at, manifest.created_at + 72 * 3600)
        finally:
            await restored.close()

    async def test_recovery_original_word_clocks_hints_preferences_not_prepared_media(self):
        from backend.production_modes import Word, SourceHint
        source, _, item = await self.recovery_fixture()
        manifest = self.store.authorize(item["up_id"], item["capability"])
        manifest.transcript.segments[0].words = [Word(w="今天", s=.25, e=.6), Word(w="服务", s=2.1, e=2.75)]
        self.store._save(manifest)
        source.sentences[0].source_hint = SourceHint(upload_id=item["up_id"], seg_id="seg_0")
        source.draft_context["source_voice_preferred"] = True
        original = self.service.creation_inputs(source)
        source.draft_context["creation_inputs"] = original
        source.uploads[0].prepared_stored_name = "already-trimmed.mp4"
        (source.task_dir / "raw" / "already-trimmed.mp4").write_bytes(b"not-original")
        source.preferences = source.preferences.model_copy(update={"voice": "ai", "background_music": False})
        source.draft_context["source_voice_preferred"] = False
        # Exercise the committed raw fallback, not merely the staging source.
        await self.store.delete(item["up_id"], item["capability"])
        with patch("backend.drafts.prepare_media_inputs", side_effect=AssertionError("No second trim")), \
                patch.object(self.store, "_preprocess", side_effect=AssertionError("No model")):
            view = await self.service.recover(source, {"step": 2})
        record = self.manager.get(view["task_id"])
        self.assertTrue(view["source_voice_preferred"])
        self.assertEqual(view["preferences"]["voice"], "mine")
        self.assertTrue(view["preferences"]["background_music"])
        self.assertEqual(view["sentences"][0]["source_hint"], {"upload_id": view["files"][0]["up_id"], "seg_id": "seg_0"})
        self.assertNotEqual(view["files"][0]["up_id"], item["up_id"])
        self.assertEqual(view["files"][0]["transcript"], original["files"][0]["manifest"]["transcript"])
        snapshot = json.loads((record.task_dir / "pretranscripts.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(snapshot["audio_offset_seconds"], .125)
        self.assertEqual(snapshot["transcript"], original["files"][0]["manifest"]["transcript"])
        self.assertEqual(record.uploads[0].path.read_bytes(), b"synthetic-video")
        self.assertIsNone(record.uploads[0].prepared_stored_name)
        self.assertEqual((view["files"][0]["in_sec"], view["files"][0]["out_sec"]), (.2, 2.9))

    async def test_recovery_reauthorizes_after_held_body_and_rejects_forged_reference(self):
        source, receipt, _ = await self.recovery_fixture()
        other, other_receipt, _ = await self.recovery_fixture()
        tasks = set(self.manager._tasks)
        entered, release = asyncio.Event(), asyncio.Event()

        async def body():
            entered.set()
            await release.wait()
            yield b'{"step":1}'

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.router()), base_url="http://testserver") as client:
            route = f"/api/tasks/{source.task_id}/recover-draft"
            forged = await client.post(route, headers={"X-Task-Token": receipt["access_token"]},
                                       json={"step": 1, "draftTaskId": other.task_id})
            self.assertEqual(forged.status_code, 422)
            job = asyncio.create_task(client.post(route, headers={"X-Task-Token": receipt["access_token"]}, content=body()))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                old_hash = source.access_token_hash
                source.access_token_hash = other.access_token_hash
            finally:
                release.set()
            try:
                self.assertEqual((await job).status_code, 404)
            finally:
                source.access_token_hash = old_hash
            foreign = await client.post(route, headers={"X-Task-Token": other_receipt["access_token"]}, json={"step": 1})
            self.assertEqual(foreign.status_code, 404)
        self.assertEqual(set(self.manager._tasks), tasks)

    async def test_recovery_storage_limits_fail_before_copy_without_generation_quota(self):
        source, _, _ = await self.recovery_fixture()
        tasks, uploads = set(self.manager._tasks), set(self.store._records)
        counts = self.service.ledger.counts(source.owner_hash, "ip")
        for limit in ("max_files", "max_total_bytes", "disk"):
            with self.subTest(limit=limit), ExitStack() as stack:
                if limit == "disk":
                    stack.enter_context(patch.object(self.store, "_space", side_effect=HTTPException(507)))
                else:
                    stack.enter_context(patch.object(self.store, limit, 0 if limit == "max_files" else 1))
                copied = stack.enter_context(patch("backend.drafts._copy_verified", side_effect=AssertionError("No oversized copy")))
                with self.assertRaises(HTTPException) as caught:
                    await self.service.recover(source, {"step": 1})
                self.assertEqual(caught.exception.status_code, 507 if limit == "disk" else 429)
                copied.assert_not_called()
                self.assertEqual(set(self.manager._tasks), tasks)
                self.assertEqual(set(self.store._records), uploads)
                self.assertEqual(self.service.ledger.counts(source.owner_hash, "ip"), counts)

    async def test_recovery_retained_transcript_cannot_bypass_store_memory_budget(self):
        source, _, item = await self.recovery_fixture()
        manifest = self.store.authorize(item["up_id"], item["capability"])
        current_bytes = self.store._transcript_size(manifest)
        self.assertGreater(current_bytes, 0)
        tasks, uploads = set(self.manager._tasks), set(self.store._records)
        # A small injected cap exercises the real boundary without allocating
        # oversized fixtures; the original fits, the clone would exceed it.
        with patch("backend.uploads.MAX_TRANSCRIPT_MEMORY_BYTES", current_bytes + 1):
            with self.assertRaises(HTTPException) as caught:
                await self.service.recover(source, {"step": 1})
            self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(set(self.manager._tasks), tasks)
        self.assertEqual(set(self.store._records), uploads)

    async def test_recovery_main_requires_original_cap_and_origin(self):
        from backend import main
        source, receipt, _ = await self.recovery_fixture()
        path = f"/api/tasks/{source.task_id}/recover-draft"
        with patch.multiple(main, settings=self.settings, task_manager=self.manager,
                            upload_capacity_guard=self.guard, _upload_store=self.store, _draft_service=self.service), \
                patch.object(self.settings, "enforce_origin_check", True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 1000)), base_url="http://localhost") as client:
                headers = {"X-Task-Token": receipt["access_token"], "Origin": "http://localhost"}
                self.assertEqual((await client.post(path, headers={"Origin": "http://localhost"}, json={"step": 1})).status_code, 404)
                self.assertEqual((await client.post(path, headers={**headers, "Origin": "http://foreign.invalid"}, json={"step": 1})).status_code, 403)
                response = await client.post(path, headers=headers, json={"step": 1})
                self.assertEqual(response.status_code, 201, response.text)
                self.assertNotEqual(response.json()["task_id"], source.task_id)

    async def test_router_file_status_restore_cap_and_start_receipt(self):
        record, receipt = await self.draft()
        headers = {"X-Task-Token": receipt["access_token"]}
        path = f"/api/tasks/{record.task_id}"
        data = b"file-bytes"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.router()), base_url="http://testserver") as client:
            added = await client.post(path + "/files", headers=headers, json={"name": "source.mp4", "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "content_type": "video/mp4"})
            self.assertEqual(added.status_code, 201, added.text)
            file = added.json()
            self.assertEqual(file["access_token"], receipt["access_token"])
            self.assertEqual(file["upload_id"], file["up_id"])
            item = record.draft_context["files"][0]
            self.assertNotIn(item["capability"], added.text)
            url = path + "/files/" + file["file_id"]
            self.assertEqual((await client.put(url + "/chunks/0", headers=headers, content=data)).status_code, 200)
            restored = self.manager._load_record(record.task_dir / "task_state.json", record.task_dir)
            self.manager._tasks[record.task_id] = restored
            record = restored
            self.assertEqual(record.access_token, "")
            status = await client.get(url + "/status", headers=headers)
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json()["chunks"], [0])
            self.assertEqual((await client.get(url + "/status", headers={"X-Task-Token": item["capability"]})).status_code, 404)
            async def preprocess(upload):
                upload.probe = _Probe(sec=3, width=64, height=64, fps=30, has_audio=False, format_name="mov")
                upload.has_speech = False
                upload.status = "ready"
                self.store._save(upload)
            with patch.object(self.store, "_preprocess", side_effect=preprocess):
                complete = await client.post(url + "/complete", headers=headers)
                self.assertEqual(complete.status_code, 202, complete.text)
                await asyncio.gather(*list(self.store._jobs.values()))
            async def prepare(directory, assets, *args):
                return assets
            async def pipeline(execution, reporter, settings):
                self.assertEqual(execution.preferences.voice, "ai")
                reporter.start_stage(execution, 7, "TTS")
                reporter.complete_stage(execution, 7, "TTS done")
            with patch("backend.drafts.prepare_media_inputs", side_effect=prepare), patch("backend.task_manager.run_pipeline", side_effect=pipeline):
                started = await client.post(path + "/start", headers=headers, json={"mode": "voiceover",
                    "script": "新闻标题\n今天介绍新的公共服务，让生活更加便利。", "preferences": {"voice": "mine"},
                    "asset_options": [{"note": "kept", "trim_start": 0, "trim_end": 2}]})
                self.assertEqual(started.status_code, 202, started.text)
                self.assertEqual(started.json()["access_token"], receipt["access_token"])
                self.assertEqual(started.json()["queue"], 1)
                await record.background
            self.assertEqual(record.preferences.voice, "mine")
            self.assertEqual(record.status, TaskState.done)
            self.assertEqual(record.draft_context["files"][0]["note"], "kept")
            self.assertTrue(self.manager.token_matches(record, receipt["access_token"]))

    async def test_rules_v1_restore_keeps_report_and_stage_metadata(self):
        record, _ = await self.draft()
        self.assertEqual(record.rules_version, 2)
        state_path = record.task_dir / "task_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        del state["rules_version"]
        state["stages"][0]["name"] = "Legacy stage"
        state["stages"][0]["weight"] = .123
        write_json_atomic(state_path, state)
        report = record.task_dir / "report.json"
        report.write_bytes(b'{"rules_version":1,"immutable":true}')
        before = (state_path.read_bytes(), report.read_bytes())
        restored = self.manager._load_record(state_path, record.task_dir)
        self.assertEqual(restored.rules_version, 1)
        self.assertEqual(restored.stages[0].name, "Legacy stage")
        self.assertEqual(restored.stages[0].weight, .123)
        self.assertEqual((state_path.read_bytes(), report.read_bytes()), before)

    async def retry_fixture(self):
        from backend.mode_pipeline import ModeStageCache
        record, _ = await self.draft()
        _, item = await self.ready(record)
        record.uploads, snapshots = await self.store.materialize([item["up_id"]], {item["up_id"]: item["capability"]}, record.task_dir)
        record.upload_ids = [item["up_id"]]
        write_json_atomic(record.task_dir / "pretranscripts.json", snapshots)
        record.status, record.current_stage, record.progress = TaskState.failed, 3, 20
        for stage in record.stages[:2]:
            stage.status, stage.fraction = StageState.done, 1
        record.stages[2].status = StageState.failed
        cache = ModeStageCache(record.task_dir)
        cache.save(2, "verified-stage2", {"source_clocks": {}}, [record.uploads[0].path.relative_to(record.task_dir).as_posix()])
        record.draft_context["retry_binding"] = _retry_binding(record, self.settings)
        self.service.ledger.accept(record.task_id, record.owner_hash, owner_digest("ip"), "accepted-input")
        self.manager._persist_record(record)
        return record, cache

    async def test_retry_real_cache_manager_queue_preserves_stages_quota(self):
        record, cache = await self.retry_fixture()
        completed = [s.model_dump() for s in record.stages[:2]]
        before = self.service.ledger.counts(record.owner_hash, owner_digest("ip"))
        async def pipeline(execution, reporter, settings):
            self.assertEqual(execution.resume_from, 3)
            self.assertIsNotNone(cache.load(2, "verified-stage2"))
            reporter.start_stage(execution, 1, "verify source")
            reporter.update_stage(execution, 1, .1, "verify source")
            reporter.complete_stage(execution, 1, "verified")
            self.assertEqual(record.progress, 20)
            self.assertEqual([s.model_dump() for s in record.stages[:2]], completed)
            reporter.start_stage(execution, 3, "first incomplete")
            raise RuntimeError("controlled local stop")
        with patch("backend.task_manager.run_pipeline", side_effect=pipeline):
            response = await self.service.retry(record, {})
            self.assertEqual(response["resume_from"], 3)
            await record.background
        self.assertEqual(record.status, TaskState.failed)
        self.assertEqual(before, self.service.ledger.counts(record.owner_hash, owner_digest("ip")))
        self.assertEqual(self.guard._reserved_bytes, 0)
        self.assertEqual([s.model_dump() for s in record.stages[:2]], completed)

    async def shortage_retry_fixture(self):
        record, cache = await self.retry_fixture()
        record.error_kind = "shortage"
        record.draft_context["retry_binding"] = _retry_binding(record, self.settings)
        return record, cache

    async def test_retry_with_shot_reuse_consent_is_recorded_and_resumes_without_changing_inputs(self):
        record, cache = await self.shortage_retry_fixture()
        cache.begin(6, "sig6")
        cache.mark_retry_safe(6)
        binding = record.draft_context["retry_binding"]
        seen = {}
        async def pipeline(execution, reporter, settings):
            seen["consent"] = record.draft_context.get("shot_reuse")
            raise RuntimeError("controlled local stop")
        with patch("backend.task_manager.run_pipeline", side_effect=pipeline):
            response = await self.service.retry(record, {"expected_revision": record.revision, "allow_shot_reuse": True})
            await record.background
        self.assertEqual(response["resume_from"], 3)
        self.assertEqual(seen["consent"], {"accepted": True})
        self.assertEqual(record.draft_context["shot_reuse"], {"accepted": True})
        self.assertEqual(record.draft_context["retry_binding"], binding, "consent is not part of the input binding")

    async def test_shot_reuse_consent_is_refused_unless_the_failure_was_a_shot_shortage(self):
        record, _ = await self.shortage_retry_fixture()
        record.error_kind = "transient"
        with patch.object(self.manager, "enqueue_retry") as enqueue:
            with self.assertRaises(HTTPException) as caught:
                await self.service.retry(record, {"expected_revision": record.revision, "allow_shot_reuse": True})
            enqueue.assert_not_called()
        self.assertEqual(caught.exception.detail["code"], "shot_reuse_not_applicable")
        self.assertNotIn("shot_reuse", record.draft_context)

    async def test_shot_reuse_consent_must_be_the_exact_boolean_and_nothing_else(self):
        record, _ = await self.shortage_retry_fixture()
        for body in ({"expected_revision": record.revision, "allow_shot_reuse": "yes"},
                     {"expected_revision": record.revision, "allow_shot_reuse": 1},
                     {"expected_revision": record.revision, "allow_shot_reuse": True, "script": "x"},
                     {"allow_shot_reuse": True}):
            with patch.object(self.manager, "enqueue_retry") as enqueue, self.assertRaises(HTTPException) as caught:
                await self.service.retry(record, body)
            self.assertEqual(caught.exception.status_code, 422, body)
            enqueue.assert_not_called()
        self.assertNotIn("shot_reuse", record.draft_context)

    async def test_failed_retry_rolls_the_shot_reuse_consent_back(self):
        record, cache = await self.shortage_retry_fixture()
        cache.begin(4, "uncertain-provider")  # a started provider receipt still blocks automatic resume
        with patch.object(self.manager, "enqueue_retry") as enqueue, self.assertRaises(HTTPException) as caught:
            await self.service.retry(record, {"expected_revision": record.revision, "allow_shot_reuse": True})
        self.assertEqual(caught.exception.detail["code"], "retry_provider_uncertain")
        enqueue.assert_not_called()
        self.assertNotIn("shot_reuse", record.draft_context, "a refused retry must not leave a consent behind")

    async def test_unconfirmed_stage_six_receipt_still_blocks_but_a_retry_safe_one_does_not(self):
        record, cache = await self.shortage_retry_fixture()
        cache.begin(6, "sig6")
        with patch.object(self.manager, "enqueue_retry") as enqueue, self.assertRaises(HTTPException) as caught:
            await self.service.retry(record, {})
        self.assertEqual(caught.exception.detail["code"], "retry_provider_uncertain")
        enqueue.assert_not_called()
        cache.mark_retry_safe(6)
        async def pipeline(execution, reporter, settings):
            raise RuntimeError("controlled local stop")
        with patch("backend.task_manager.run_pipeline", side_effect=pipeline):
            response = await self.service.retry(record, {})
            await record.background
        self.assertEqual(response["resume_from"], 3)

    async def test_retry_uncertain_and_changed_sources_never_enqueue(self):
        record, cache = await self.retry_fixture()
        cache.begin(4, "uncertain-provider")
        with patch.object(self.manager, "enqueue_retry") as enqueue:
            with self.assertRaises(HTTPException) as caught:
                await self.service.retry(record, {})
            self.assertEqual(caught.exception.detail["code"], "retry_provider_uncertain")
            enqueue.assert_not_called()
        record.uploads[0].path.write_bytes(b"changed")
        with self.assertRaises(HTTPException) as caught:
            await self.service.retry(record, {})
        self.assertEqual(caught.exception.detail["code"], "retry_inputs_changed")
        self.assertEqual(self.guard._reserved_bytes, 0)

    async def test_local_speech_unavailable_is_honest_no_materialization(self):
        record, _ = await self.draft(mode="original")
        await self.ready(record)
        with patch.object(self.store, "materialize", side_effect=AssertionError("unavailable inference must not copy")):
            result = await self.service.align(record, {"script": "标题\n原话。"})
        self.assertFalse(result["local_speech"]["available"])
        self.assertFalse(result["local_speech"]["inference_verified"])
        self.assertIn("missing_local_speaker_onnx", result["local_speech"]["blocked_prerequisites"])
        self.assertEqual(record.speakers, [])
        self.settings.local_speech_required = True
        with self.assertRaises(HTTPException) as caught:
            await self.service.align(record, {"script": "标题\n原话。"})
        self.assertEqual(caught.exception.status_code, 503)

    async def test_real_pipeline_retry_reuses_tts_after_local_stage_failure(self):
        """Actual manager -> pipeline -> receipts -> retry; synthetic providers, real FFmpeg."""
        from tests.test_mode_pipeline import ModeMediaIntegration, FakeVoice
        from backend import mode_pipeline
        ModeMediaIntegration.setUpClass()
        case = ModeMediaIntegration()
        await case.asyncSetUp()
        try:
            record, _ = await self.draft()
            case.root = record.task_dir
            self.assertTrue((case.root / "raw").is_dir())
            fixture = case.record("voiceover", texts=[("今天活动开幕。", "narration")])
            for key in ("uploads", "upload_ids", "script", "sentences"):
                setattr(record, key, getattr(fixture, key))
            self.settings.video_embedding_enabled = False
            self.settings.entity_verification_enabled = False
            record.status = TaskState.queued
            record.draft_context["retry_binding"] = _retry_binding(record, self.settings)
            self.service.ledger.accept(record.task_id, record.owner_hash, owner_digest("ip"), "input")
            voice = FakeVoice()
            with ExitStack() as stack:
                case.providers(stack, voice=voice)
                with patch("backend.mode_pipeline.generate_mode_subtitles", side_effect=RuntimeError("local subtitle failure")):
                    await self.manager._run(record)
                self.assertEqual(record.status, TaskState.failed, record.error_message)
                self.assertEqual(record.current_stage, 8, record.error_message)
                self.assertEqual(len(voice.calls), 1)
                audio_hash = mode_pipeline._sha(record.task_dir / "narration.m4a")
                completed = [s.model_dump() for s in record.stages[:7]]
                response = await self.service.retry(record, {})
                self.assertEqual(response["resume_from"], 8)
                await record.background
            self.assertEqual(record.status, TaskState.done, record.error_message)
            self.assertEqual(len(voice.calls), 1)
            self.assertEqual(mode_pipeline._sha(record.task_dir / "narration.m4a"), audio_hash)
            self.assertEqual([s.model_dump() for s in record.stages[:7]], completed)
            self.assertEqual(self.service.ledger.counts(record.owner_hash, owner_digest("ip")), (1, 1, 1))
            self.assertEqual(self.guard._reserved_bytes, 0)
        finally:
            await case.asyncTearDown()
            ModeMediaIntegration.tearDownClass()

    async def test_crossfile_preview_real_analysis_adapter_and_warm_cache(self):
        """Real PCM extraction/clustering with explicitly synthetic embeddings, not a real model."""
        from backend.media import run_logged_command
        from backend.production_modes import TranscriptSegment
        from backend.providers.local_speech import LocalSpeechReadiness
        source = self.root / "speakers.mp4"
        await run_logged_command(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=3", "-t", "3",
            "-c:v", "libx264", "-c:a", "aac", str(source)], self.root, "synthetic speaker fixture")
        record, _ = await self.draft(mode="original")
        for _ in range(2):
            _, item = await self.file(record, data=source.read_bytes())
            async def preprocess(upload):
                upload.probe = _Probe(sec=3, width=64, height=64, fps=25, has_audio=True, format_name="mov")
                upload.has_speech, upload.status = True, "ready"
                upload.transcript.segments = [TranscriptSegment(id="seg_0", start=.2, end=1.5, text="今天活动开幕。", speaker_id="unverified")]
                self.store._save(upload)
            with patch.object(self.store, "_preprocess", side_effect=preprocess):
                await self.service.file_action(record, item["file_id"], "complete")
                await asyncio.gather(*list(self.store._jobs.values()))
        model = self.root / "synthetic-model.onnx"
        model.write_bytes(b"not-a-real-model")
        self.settings.local_speaker_model_path = model
        self.settings.local_speech_license_reviewed = True
        calls = []
        class Encoder:
            def __init__(self, *args, **kwargs):
                pass
            def embedding(self, audio, start, end):
                import wave
                with wave.open(str(audio)) as pcm:
                    self_test.assertEqual(pcm.getframerate(), 16000)
                    self_test.assertGreater(pcm.getnframes(), 16000)
                calls.append((start, end))
                return [1., 0.]
        self_test = self
        readiness = LocalSpeechReadiness(True, "speaker_embedding", (), "synthetic test", True)
        with patch("backend.providers.local_speech.local_speech_readiness", return_value=readiness), patch("backend.providers.local_speech.LocalSpeakerEncoder", Encoder):
            first = await self.service.align(record, {"script": "标题\n今天活动开幕。"})
            second = await self.service.align(record, {"script": "标题\n今天活动开幕。"})
            async def prepare(directory, assets, *args):
                return assets
            async def runner(record):
                record.status = TaskState.failed
                await self.manager._release_disk_reservation(record)
            with patch("backend.drafts.prepare_media_inputs", side_effect=prepare), patch.object(self.manager, "_run", side_effect=runner):
                await self.service.start(record, {"script": "标题\n今天活动开幕。"}, "ip")
                await record.background
            stored = json.loads((record.task_dir / "pretranscripts.json").read_text(encoding="utf-8"))
            self.assertEqual([s["transcript"]["segments"][0]["speaker_id"] for s in stored], ["spk_1", "spk_1"])
        self.assertEqual(len(calls), 2)
        self.assertEqual([a["speaker_id"] for a in first["local_speech"]["assignments"]], ["spk_1", "spk_1"])
        self.assertEqual(first, second)
        self.assertEqual(self.guard._reserved_bytes, 0)
        self.assertEqual(list(record.task_dir.glob(".draft-speakers-*")), [])