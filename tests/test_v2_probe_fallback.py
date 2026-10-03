"""F06: TEMP-only originals, real ASGI/FFmpeg, synthetic provider spy.

Run only under install_v2_guards. No startup lifespan, listener, model or render.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

from tests.test_v2_drafts import DraftTests
from backend.providers.asr import ASRTranscript


class ProbeFallbackTests(DraftTests):
    # Reuse lifecycle/helpers, not the inherited historical test suite.
    async def make_file(self, record, *, duration=1, name="fixture.mkv", audio=False):
        path = self.root / name
        from backend.media import run_logged_command
        args = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                f"testsrc2=size=64x48:rate=25:duration={duration}"]
        if audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:a", "pcm_s16le"]
        await run_logged_command([*args, "-c:v", "ffv1", str(path)], self.root, "synthetic F06")
        data = path.read_bytes()
        receipt = await self.service.add_file(record, {"name": name, "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}, record.access_token)
        item = self.service.item(record, receipt["file_id"])
        async def stream():
            yield data
        await self.store.put_chunk(item["up_id"], item["capability"], 0, stream(), len(data))
        return item

    def provider(self):
        class Spy:
            calls = 0
            def validate_configuration(inner):
                pass
            async def __aenter__(inner):
                return inner
            async def __aexit__(inner, *args):
                pass
            async def transcribe(inner, audio):
                inner.calls += 1
                return ASRTranscript(text="", utterances=[])
        return Spy()

    async def join_jobs(self):
        await asyncio.wait_for(asyncio.gather(*list(self.store._jobs.values())), 20)
        await asyncio.sleep(0)

    async def test_f06_real_asgi_probe_authority_and_frontend_receipt(self):
        from backend import main
        record, _ = await self.draft()
        item = await self.make_file(record)
        path = f"/api/tasks/{record.task_id}/files/{item['file_id']}"
        headers = {"X-Task-Token": record.access_token, "Origin": "http://testserver"}
        with patch.multiple(main, settings=self.settings, task_manager=self.manager,
                            upload_capacity_guard=self.guard, _upload_store=self.store, _draft_service=self.service), \
                patch("backend.uploads.create_asr_provider", side_effect=AssertionError("no provider during probe")), \
                patch.object(self.manager, "_dispatch", create=True, side_effect=AssertionError("no render")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
                for bad in ({}, {"X-Task-Token": "invalid"}):
                    self.assertEqual((await client.post(path + "/probe", headers=bad, json={})).status_code, 404)
                forged = await client.post(path + "/probe", headers=headers, json={"sec": 1, "width": 1, "fps": 1})
                self.assertEqual(forged.status_code, 422)
                result = await client.post(path + "/probe", headers=headers, json={})
                self.assertEqual(result.status_code, 200)
                view = result.json()
                self.assertEqual((view["sec"], view["width"], view["height"], view["fps"]), (1, 64, 48, 25))
                self.assertTrue(view["metadata_only"])
                self.assertTrue(view["probe_ok"])
                self.assertFalse(view["can_materialize"])
                self.assertEqual(view["status"], "uploading")
                for _ in range(3):
                    self.assertEqual((await client.get(path, headers=headers)).json(), view)
                    self.assertEqual((await client.post(path + "/probe", headers=headers, json={})).json(), view)
                self.assertFalse(self.store._jobs)
                self.assertFalse(self.service.ledger.accepted(record.task_id))
                self.assertIsNone(record.background)
                # TEMP machine bridge contains only this test's synthetic receipt
                # and capability; never console output or a real user's data.
                target = os.environ.get("F06_BRIDGE")
                if target:
                    with Path(target).open("x", encoding="utf-8") as output:
                        json.dump({"snapshot": view, "scope": {"draftTaskId": record.task_id,
                            "server_file_id": item["file_id"]}}, output)

    async def test_f06_main_origin_foreign_task_and_incomplete_chunks(self):
        from backend import main
        record, _ = await self.draft()
        item = await self.make_file(record)
        other, _ = await self.draft("other")
        self.settings.enforce_origin_check = True
        self.settings.frontend_origins = "http://testserver"
        path = f"/api/tasks/{record.task_id}/files/{item['file_id']}/probe"
        with patch.multiple(main, settings=self.settings, task_manager=self.manager,
                            upload_capacity_guard=self.guard, _upload_store=self.store, _draft_service=self.service), \
                patch("backend.uploads.create_asr_provider", side_effect=AssertionError("no ASR")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
                token = {"X-Task-Token": record.access_token}
                self.assertEqual((await client.post(path, headers={**token, "Origin": "https://foreign.invalid"}, json={})).status_code, 403)
                foreign = f"/api/tasks/{other.task_id}/files/{item['file_id']}/probe"
                self.assertEqual((await client.post(foreign, headers={"X-Task-Token": other.access_token,
                    "Origin": "http://testserver"}, json={})).status_code, 404)
                manifest = self.store.authorize(item["up_id"], item["capability"])
                manifest.chunks.clear()
                rejected = await client.post(path, headers={**token, "Origin": "http://testserver"}, json={})
                self.assertEqual(rejected.status_code, 409)
                self.assertFalse(self.store._jobs)

    async def test_f06_corrupt_image_and_digest_never_probe_ready(self):
        record, _ = await self.draft()
        from fastapi import HTTPException
        with patch("backend.uploads.create_asr_provider", side_effect=AssertionError("no ASR")):
            for name, digest in (("bad.png", hashlib.sha256(b"invalid").hexdigest()), ("bad.mkv", "0" * 64)):
                receipt = await self.service.add_file(record, {"name": name, "size": 7, "sha256": digest}, record.access_token)
                item = self.service.item(record, receipt["file_id"])
                async def stream():
                    yield b"invalid"
                await self.store.put_chunk(item["up_id"], item["capability"], 0, stream(), 7)
                with self.assertRaises(HTTPException):
                    await self.service.file_action(record, item["file_id"], "probe", raw={})
                self.assertFalse(self.store.read(item["up_id"], item["capability"])["probe_ok"])
                self.assertFalse(self.store._jobs)

    async def test_f06_unknown_peer_blocks_paid_step_and_complete(self):
        record, _ = await self.draft()
        a = await self.make_file(record, audio=True)
        await self.file(record, data=b"unknown-peer")
        spy = self.provider()
        with patch("backend.uploads.create_asr_provider", return_value=spy):
            await self.service.file_action(record, a["file_id"], "probe", raw={})
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                await self.service.file_action(record, a["file_id"], "complete")
            self.assertEqual(caught.exception.status_code, 409)
            # A legacy complete also cannot bypass the guard in _transcribe.
            await self.store.complete(a["up_id"], a["capability"])
            await self.join_jobs()
        self.assertEqual(spy.calls, 0)
        self.assertFalse(self.store.authorize(a["up_id"], a["capability"]).asr_attempted)

    async def test_f06_legacy_complete_unknown_peer_provider_spy(self):
        record, _ = await self.draft()
        item = await self.make_file(record, audio=True)
        await self.file(record, data=b"unknown-peer")
        self.settings.sync_sound_vad_enabled = False
        spy = self.provider()
        with patch("backend.uploads.create_asr_provider", return_value=spy):
            await self.service.file_action(record, item["file_id"], "complete")
            await self.join_jobs()
        self.assertEqual(spy.calls, 0)
        self.assertFalse(self.store.authorize(item["up_id"], item["capability"]).asr_attempted)

    async def test_f06_original_aggregate_over_budget_ignores_trim(self):
        record, _ = await self.draft()
        a = await self.make_file(record, name="a.mkv", audio=True)
        b = await self.make_file(record, name="b.mkv", audio=True)
        self.store.max_total_seconds = 1.5  # Real 1+1 seconds; strict smaller configured limit.
        spy = self.provider()
        with patch("backend.uploads.create_asr_provider", return_value=spy):
            for item in (a, b):
                await self.service.file_action(record, item["file_id"], "probe", raw={})
                item["in_sec"], item["out_sec"] = .1, .2
            from fastapi import HTTPException
            for item in (a, b):
                with self.assertRaises(HTTPException) as caught:
                    await self.service.file_action(record, item["file_id"], "complete")
                self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(spy.calls, 0)
        self.assertFalse(self.store._jobs)

    async def test_f06_oversize_per_file_real_probe_before_provider(self):
        record, _ = await self.draft()
        item = await self.make_file(record, audio=True)
        self.store.max_seconds = .5
        spy = self.provider()
        with patch("backend.uploads.create_asr_provider", return_value=spy):
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as caught:
                await self.service.file_action(record, item["file_id"], "probe", raw={})
            self.assertEqual(caught.exception.status_code, 415)
        self.assertEqual(spy.calls, 0)
        self.assertIsNone(self.store.authorize(item["up_id"], item["capability"]).probe)

    async def test_f06_concurrent_probe_unknown_not_zero(self):
        record, _ = await self.draft()
        a = await self.make_file(record, name="a.mkv")
        b = await self.make_file(record, name="b.mkv")
        await self.service.file_action(record, a["file_id"], "probe", raw={})
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.store._probe
        async def held(upload):
            entered.set()
            await release.wait()
            return await original(upload)
        with patch.object(self.store, "_probe", side_effect=held):
            job = asyncio.create_task(self.store.probe_only(b["up_id"], b["capability"]))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                from fastapi import HTTPException
                with self.assertRaises(HTTPException):
                    self.service._before_asr(self.store.authorize(a["up_id"], a["capability"]))
            finally:
                release.set()
                await asyncio.wait_for(job, 5)
        self.service._probe_budget(record)

    async def test_f06_exact_original_budget_edges_and_interrupted_attempt(self):
        from fastapi import HTTPException
        from backend.uploads import _Probe
        from backend.drafts import DraftUploadStore
        record, _ = await self.draft()
        entries = []
        for data in (b"synthetic-a", b"synthetic-b", b"synthetic-c"):
            _, item = await self.file(record, data=data)
            upload = self.store.authorize(item["up_id"], item["capability"])
            upload.probe = _Probe(sec=1200, width=64, height=48, fps=25, has_audio=True, format_name="mov")
            upload.status = "probed"
            entries.append(upload)
        self.service._probe_budget(record)  # Exactly 3600 accepted.
        entries[-1].probe.sec = 1200.001
        with self.assertRaises(HTTPException) as caught:
            self.service._probe_budget(record)
        self.assertEqual(caught.exception.status_code, 413)
        entries[-1].probe.sec = 1
        entries[0].probe.sec = 1800
        entries[1].probe.sec = 1
        self.service._probe_budget(record)  # Exactly 1800 accepted.
        entries[0].probe.sec = 1800.001
        with self.assertRaises(HTTPException):
            self.service._probe_budget(record)
        entries[0].probe.sec = 1800
        entries[0].status, entries[0].phase, entries[0].asr_attempted = "processing", "asr", True
        for upload in entries:
            self.store._save(upload)
        await self.store.close()
        self.store = DraftUploadStore(self.settings)
        self.service.uploads = self.store
        self.store.before_asr = self.service._before_asr
        spy = self.provider()
        with patch("backend.uploads.create_asr_provider", return_value=spy):
            first = record.draft_context["files"][0]
            for _ in range(3):
                view = await self.store.complete(first["up_id"], first["capability"])
                self.assertEqual(view["status"], "failed")
            self.assertFalse(self.store._jobs)
        self.assertEqual(spy.calls, 0)

    async def test_f06_restart_and_concurrent_complete_no_double_paid_step(self):
        from backend.drafts import DraftUploadStore
        record, _ = await self.draft()
        item = await self.make_file(record, audio=True)
        await self.service.file_action(record, item["file_id"], "probe", raw={})
        await self.store.close()
        self.store = DraftUploadStore(self.settings)
        self.service.uploads = self.store
        self.store.before_asr = self.service._before_asr
        self.assertTrue(self.store.read(item["up_id"], item["capability"])["metadata_only"])
        self.assertFalse(self.store._jobs)
        spy = self.provider()
        # Real PCM extraction; force speech uncertainty rather than a model/VAD.
        self.settings.sync_sound_vad_enabled = False
        with patch("backend.uploads.create_asr_provider", return_value=spy):
            await asyncio.gather(*(self.store.complete(item["up_id"], item["capability"]) for _ in range(2)), return_exceptions=True)
            await self.join_jobs()
            for _ in range(3):
                await self.service.file_action(record, item["file_id"], "complete")
                self.store.read(item["up_id"], item["capability"])
            await self.join_jobs()
        self.assertEqual(spy.calls, 1)
        await self.store.close()
        self.store = DraftUploadStore(self.settings)
        self.service.uploads = self.store
        self.store.before_asr = self.service._before_asr
        self.assertEqual(self.store.read(item["up_id"], item["capability"])["status"], "ready")
        self.assertFalse(self.store._jobs)


def load_tests(loader, tests, pattern):
    # Deliberately exclude inherited tests; parent owns the full suite.
    import unittest
    return unittest.TestSuite(ProbeFallbackTests(name) for name in sorted(ProbeFallbackTests.__dict__)
                              if name.startswith("test_f06_"))