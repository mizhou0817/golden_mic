"""Export despite unresolved checks: only with an explicit acknowledgement, and everything else still gates.

The real v2 export router over a synthetic TEMP task; the encoder is replaced so only the gates are exercised.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

from backend import studio
from backend.publication import publication_gate
from backend.v2_editing import create_v2_export_router
from tests.studio_publication_fixtures import synthetic_publication_files

SRT = b"1\n00:00:00,000 --> 00:00:01,000\nSynthetic export.\n"


class UnresolvedExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-export-unresolved-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        (self.folder / "final.mp4").write_bytes(b"synthetic-source-not-a-codec-test")
        (self.folder / "voice.wav").write_bytes(b"synthetic-voice")
        (self.folder / "timings.json").write_text(json.dumps([{"end": 5.0, "audio_path": "voice.wav"}]), encoding="utf-8")
        # A real error (blocking), an unconfirmed warning (pending) AND a pipeline QC blocker; nothing is acknowledged.
        for name, content in synthetic_publication_files(blocked=True).items():
            value = json.loads(content)
            if name == "report.json":
                value["task_id"] = "task"
            (self.folder / name).write_text(json.dumps(value), encoding="utf-8")
        self.record = SimpleNamespace(task_id="task", task_dir=self.folder, uploads=[], status="done", revision=0,
                                      mode_contract=True, mode="mixed", background=None)
        self.gate = publication_gate(self.record)
        self.assertGreaterEqual(self.gate["blocking_count"], 1)
        self.assertGreaterEqual(self.gate["pending_count"], 1)

        async def authorize(request, task_id, write=False):
            if request.headers.get("X-Token") != "synthetic" or task_id != "task":
                raise HTTPException(403, "denied")
            return self.record

        async def fake_render(root, work, options, budget):
            work.mkdir(parents=True, exist_ok=True)
            (work / "output.srt").write_bytes(SRT)
            return {"file": "output.srt", "bytes": len(SRT), "duration": 1.0, "sha256": hashlib.sha256(SRT).hexdigest()}
        # The router imports the renderer when it is built, so patch before building it.
        self.stack.enter_context(patch("backend.studio_render.render_v2_export", fake_render))
        settings = SimpleNamespace(data_dir=self.folder.parent, media_command_timeout_seconds=90, minimum_free_disk_bytes=0)
        manager = SimpleNamespace(_draining=False, get=lambda _: self.record)
        app = FastAPI()
        app.include_router(create_v2_export_router(settings, manager, authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                        headers={"X-Token": "synthetic"})
        self.addAsyncCleanup(self.client.aclose)
        self.headers = {}
        self.prefix = "/api/tasks/task"

    async def post(self, **extra):
        return await self.client.post(self.prefix + "/exports", headers=self.headers,
                                      json={"fmt": "srt", "expected_revision": 0, **extra})

    async def finish(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.gather(*list(studio._RUNNING.values()))
        return (await self.client.get(f"{self.prefix}/exports/{response.json()['export_id']}", headers=self.headers)).json()

    async def test_without_acknowledgement_the_gates_still_refuse_and_nothing_is_created(self):
        for extra in ({}, {"acknowledge_unresolved": False}):
            response = await self.post(**extra)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertFalse((self.folder / "v2_exports").exists())

    async def test_acknowledged_export_runs_is_labelled_and_downloads(self):
        response = await self.post(acknowledge_unresolved=True)
        job = await self.finish(response)
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["unresolved"]["qc_blockers"], True)
        self.assertGreaterEqual(job["unresolved"]["blocking"], 1)
        self.assertGreaterEqual(job["unresolved"]["pending"], 1)
        download = await self.client.get(f"{self.prefix}/exports/{job['export_id']}/file", headers=self.headers)
        self.assertEqual(download.status_code, 200, download.text)
        self.assertEqual(download.content, SRT)

    async def test_the_acknowledgement_belongs_to_that_export_only(self):
        job = await self.finish(await self.post(acknowledge_unresolved=True))
        path = self.folder / "v2_exports" / job["export_id"] / "job.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["options"]["acknowledge_unresolved"] = False
        path.write_text(json.dumps(record), encoding="utf-8")
        refused = await self.client.get(f"{self.prefix}/exports/{job['export_id']}/file", headers=self.headers)
        self.assertEqual(refused.status_code, 409, "an export not acknowledged at creation cannot be downloaded past a failing gate")

    async def test_acknowledgement_must_be_the_exact_boolean(self):
        for bad in ("true", 1, "yes", None):
            response = await self.post(acknowledge_unresolved=bad)
            self.assertIn(response.status_code, (409, 422), bad)
            self.assertEqual((await self.post(acknowledge_unresolved="true")).status_code, 422)
        self.assertEqual((await self.post(acknowledge_unresolved=1)).status_code, 422)

    async def test_acknowledgement_does_not_waive_stale_revision_or_required_ai_disclosure(self):
        stale = await self.client.post(self.prefix + "/exports", headers=self.headers,
                                       json={"fmt": "srt", "expected_revision": 7, "acknowledge_unresolved": True})
        self.assertEqual(stale.status_code, 409, stale.text)
        with patch.object(studio, "requires_disclosure", return_value=True):
            response = await self.post(acknowledge_unresolved=True)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("AI disclosure", response.text)
        self.assertFalse((self.folder / "v2_exports").exists())


if __name__ == "__main__":
    unittest.main()
