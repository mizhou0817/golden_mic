"""Real-main download auth regression. Run under the existing V2 IO guards.

Only synthetic TEMP bytes/receipts: no render, provider, server or lifespan.
The matching header+query contract predates metadata/tombstones (see
test_workspace_access.case_conflicting_tokens). Never log request URLs/tokens.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

import httpx

from backend import main, studio
from backend.admission import AdmissionLedger, owner_digest
from backend.config import Settings
from backend.models import TaskState
from backend.publication import publication_gate
from backend.task_manager import TaskRecord
from tests.studio_publication_fixtures import confirm_synthetic_publication, synthetic_publication_files


class ExportDownloadAuthTests(unittest.IsolatedAsyncioTestCase):
    # Closed, credential-free observation rows for the focused diagnostic runner.
    observations: list[dict[str, object]] = []

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-export-auth-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = Settings(_env_file=None, app_env="test", data_dir=self.root / "tasks",
                            asr_cache_dir=self.root / "cache", min_free_disk_gb=0,
                            frontend_origins="http://localhost", enforce_origin_check=True)
        settings.data_dir.mkdir()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Keep the REAL manager object captured by the already mounted routers.
        # Only its owned storage/records are replaced; authorization is untouched.
        self.stack.enter_context(patch.multiple(main.task_manager, settings=settings,
                                                _tasks={}, _draining=False))
        self.ledger = AdmissionLedger(settings.data_dir)
        self.stack.enter_context(patch.multiple(main, settings=settings,
            _draft_service=SimpleNamespace(manager=main.task_manager, settings=settings, ledger=self.ledger)))
        task_id, self.export_id = uuid4().hex, uuid4().hex
        folder = settings.data_dir / task_id
        folder.mkdir()
        self.owner = "O" * 43
        self.record = TaskRecord(task_id, folder, "Synthetic headline", [], status=TaskState.done,
            lifecycle_v2=True, mode_contract=True, owner_hash=owner_digest(self.owner),
            processing_completed_at=datetime.now(timezone.utc))
        main.task_manager._tasks[task_id] = self.record
        self.payload = b"1\n00:00:00,000 --> 00:00:01,000\nSynthetic auth fixture.\n"
        (folder / "final.mp4").write_bytes(b"synthetic-source-not-a-codec-test")
        for name, content in synthetic_publication_files().items():
            value = json.loads(content)
            if name == "report.json":
                value["task_id"] = task_id
            (folder / name).write_text(json.dumps(value), encoding="utf-8")
        confirm_synthetic_publication(self, self.record)
        self.work = folder / "v2_exports" / self.export_id
        (self.work / "media").mkdir(parents=True)
        self.output = self.work / "media" / "output.srt"
        self.output.write_bytes(self.payload)
        self.job = {"export_id": self.export_id, "revision": 0, "state": "succeeded",
            "output_id": self.export_id, "options": {"fmt": "srt"},
            "result": {"file": "output.srt", "bytes": len(self.payload), "duration": 1.,
                       "sha256": hashlib.sha256(self.payload).hexdigest()},
            "source_sha256": studio.mode_export_hashes(studio.mode_export_inputs(folder)),
            "publication": publication_gate(self.record)}
        self.save_job()
        self.path = f"/api/tasks/{task_id}/exports/{self.export_id}/file"
        self.native_url = self.path + "?" + urlencode({"token": self.record.access_token})
        # Non-loopback peer prevents trusted-local fallback masking missing auth.
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app,
            client=("198.51.100.23", 12345)), base_url="http://localhost")
        self.addAsyncCleanup(self.client.aclose)

    def save_job(self):
        (self.work / "job.json").write_text(json.dumps(self.job), encoding="utf-8")

    def hashes(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    async def request_case(self, label, *, query=None, headers=(), expected=404, ranged=True):
        before = self.hashes()
        url = self.path + ("?" + urlencode(query) if query is not None else "")
        request_headers = list(headers) + ([("Range", "bytes=0-7")] if ranged else [])
        response = await self.client.get(url, headers=request_headers)
        self.observations.append({"case": label, "status": response.status_code,
                                  "range": ranged, "ownerCookie": bool(self.client.cookies)})
        self.assertEqual(self.hashes(), before, "GET changed fixture storage")
        self.assertEqual(response.status_code, expected)
        if expected == 206:
            self.assertEqual(response.content, self.payload[:8])
            self.assertEqual(response.headers["content-range"], f"bytes 0-7/{len(self.payload)}")
            self.assertEqual(response.headers["content-length"], "8")
            self.assertIn("no-store", response.headers["cache-control"])
        elif expected == 200:
            self.assertEqual(response.content, self.payload)
        else:
            self.assertNotIn("content-range", response.headers)
            self.assertNotIn(self.payload, response.content)
        return response

    async def test_native_query_only_full_and_range(self):
        # Exactly the native hyperlink credential shape, no X-Task-Token header.
        response = await self.client.get(self.native_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.payload)
        self.assertNotIn("x-task-token", response.request.headers)
        await self.request_case("query_only", query={"token": self.record.access_token}, expected=206)

    async def test_header_only_range(self):
        await self.request_case("header_only", headers=[("X-Task-Token", self.record.access_token)], expected=206)

    async def test_matching_header_query_established_contract(self):
        await self.request_case("matching_header_query", query={"token": self.record.access_token},
            headers=[("X-Task-Token", self.record.access_token)], expected=206)

    async def test_conflicting_credentials_both_orders(self):
        for header, query in ((self.record.access_token, "wrong"), ("wrong", self.record.access_token)):
            await self.request_case("conflicting", query={"token": query}, headers=[("X-Task-Token", header)])

    async def test_repeated_query_and_header_even_when_equal(self):
        for second in (self.record.access_token, "wrong"):
            await self.request_case("duplicate_query", query=[("token", self.record.access_token), ("token", second)])
            await self.request_case("duplicate_header", headers=[("X-Task-Token", self.record.access_token),
                                                                 ("X-Task-Token", second)])

    async def test_owner_cookie_cannot_override_invalid_or_ambiguous_proof(self):
        self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner)
        await self.request_case("owner_without_capability")
        await self.request_case("owner_bad_query", query={"token": "wrong"})
        await self.request_case("owner_conflict", query={"token": self.record.access_token},
                                headers=[("X-Task-Token", "wrong")])
        await self.request_case("owner_duplicate_query", query=[("token", self.record.access_token)] * 2)
        await self.request_case("owner_valid_query", query={"token": self.record.access_token}, expected=206)

    async def test_empty_missing_foreign_capability(self):
        for query, headers in ((None, []), ({"token": ""}, []), (None, [("X-Task-Token", "")]),
                               ({"token": "F" * 43}, [])):
            await self.request_case("missing_empty_foreign", query=query, headers=headers)

    async def test_real_integrity_revision_and_publication_guards_not_bypassed(self):
        self.output.write_bytes(b"corrupted")
        await self.request_case("tampered_output", query={"token": self.record.access_token}, expected=409)
        self.output.write_bytes(self.payload)
        self.record.revision = 1
        await self.request_case("stale_revision", query={"token": self.record.access_token}, expected=409)
        self.record.revision = 0
        self.job["publication"] = {}
        self.save_job()
        await self.request_case("publication_binding", query={"token": self.record.access_token}, expected=409)

    async def test_tombstone_owner_and_explicit_proof_precedence(self):
        self.ledger.tombstone(self.record.task_id, self.record.owner_hash,
                              self.record.access_token_hash, "synthetic-expiry")
        main.task_manager._tasks.pop(self.record.task_id)
        await self.request_case("gone_without_proof")
        await self.request_case("gone_valid_query", query={"token": self.record.access_token}, expected=410)
        self.client.cookies.set(main._DEV_SESSION_COOKIE, self.owner)
        await self.request_case("gone_owner", expected=410)
        await self.request_case("gone_owner_bad_query", query={"token": "wrong"})
        await self.request_case("gone_owner_duplicate_query", query=[("token", self.record.access_token)] * 2)
        await self.request_case("gone_owner_conflict", query={"token": self.record.access_token},
                                headers=[("X-Task-Token", "wrong")])