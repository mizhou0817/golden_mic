"""Server publication gate contracts. TEMP JSON/opaque media; no codecs/providers."""
from __future__ import annotations

# These tests await the actual owned jobs and check lease cleanup.
# pyright: reportPrivateUsage=false

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from backend.publication import CHECKS_FILE, MAX_CHECKS, confirm_checks, publication_gate, require_publication
from backend.storage import write_json_atomic


def _report(task_id: str = "task", issues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    values = issues or []
    return {"task_id": task_id, "mode": "mixed", "rows": [{
        "sentence_id": 0, "sentence": "A synthetic statement", "kind": "narration", "shot_id": 0,
        "duration": 2, "description": "opaque source", "confidence": .9, "is_fallback": False,
        "thumb_url": None,
    }], "quality": {"blocking_issue_count": sum(issue.get("severity") == "error" for issue in values),
                    "warning_count": sum(issue.get("severity") == "warning" for issue in values), "issues": values}}


def _warning(code: str = "QUOTE_MATCH_LOW", sentence: int | None = 0, **fields: Any) -> dict[str, Any]:
    return {"code": code, "severity": "warning", "message": "Please listen to the actual quote", "sentence_id": sentence,
            "level": 1, "action": "Listen", **fields}


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="pub-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "task"
        self.root.mkdir()
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, revision=0, status="done",
                                      mode="mixed", mode_contract=True, background=None)
        self.write(_report(issues=[_warning()]))

    def write(self, report: dict[str, Any]) -> None:
        write_json_atomic(self.root / "report.json", report)

    def keys(self) -> list[str]:
        return [check["key"] for check in publication_gate(self.record)["checks"] if check["level"] == 1]

    def test_warning_requires_confirmation_and_get_is_read_only(self) -> None:
        before = (self.root / "report.json").read_bytes()
        gate = publication_gate(self.record)
        self.assertEqual(set(gate), {"revision", "blocking_count", "pending_count", "passed", "checks"})
        self.assertEqual(set(gate["checks"][0]), {"key", "code", "level", "message", "sentence_id", "action", "checked"})
        self.assertFalse(gate["passed"])
        self.assertEqual((gate["blocking_count"], gate["pending_count"]), (0, 1))
        self.assertFalse((self.root / CHECKS_FILE).exists())
        with self.assertRaises(HTTPException) as caught:
            require_publication(self.record)
        self.assertEqual(caught.exception.status_code, 409)
        confirmed = confirm_checks(self.record, 0, self.keys())
        self.assertTrue(confirmed["passed"])
        self.assertTrue(publication_gate(self.record)["checks"][0]["checked"])
        self.assertIsNone(require_publication(self.record))
        saved = json.loads((self.root / CHECKS_FILE).read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"schema_version", "revision", "report_sha256", "checked_keys"})
        self.assertEqual((self.root / "report.json").read_bytes(), before)
        self.assertNotIn("message", saved)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_confirm_replaces_set_and_empty_list_revokes(self) -> None:
        self.write(_report(issues=[_warning(), _warning("SPEAKER_UNNAMED")]))
        keys = self.keys()
        self.assertTrue(confirm_checks(self.record, 0, keys)["passed"])
        self.assertEqual(confirm_checks(self.record, 0, keys[:1])["pending_count"], 1)
        self.assertEqual(confirm_checks(self.record, 0, [])["pending_count"], 2)

    def test_errors_never_confirmable_even_if_report_says_checked(self) -> None:
        self.write(_report(issues=[_warning("QUOTE_NOT_FOUND", severity="error", level=2, checked=True, confirmable=True)]))
        check = publication_gate(self.record)["checks"][0]
        self.assertEqual(check["level"], 0)
        with self.assertRaises(HTTPException) as caught:
            confirm_checks(self.record, 0, [check["key"]])
        self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse((self.root / CHECKS_FILE).exists())
        self.assertFalse(check["checked"])

    def test_unknown_duplicate_boolean_and_non_key_rejected_atomically(self) -> None:
        keys = self.keys()
        confirm_checks(self.record, 0, keys)
        before = (self.root / CHECKS_FILE).read_bytes()
        invalid: list[Any] = [["f" * 64], keys * 2, [True], ["../x"], "not-a-list", [None]]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(HTTPException) as caught:
                confirm_checks(self.record, 0, values)
            self.assertEqual(caught.exception.status_code, 422)
            self.assertEqual((self.root / CHECKS_FILE).read_bytes(), before)

    def test_stale_revision_and_report_content_invalidate_old_keys(self) -> None:
        keys = self.keys()
        confirm_checks(self.record, 0, keys)
        self.record.revision = 1
        self.assertFalse(publication_gate(self.record)["passed"])
        self.assertNotEqual(self.keys(), keys)
        with self.assertRaises(HTTPException) as caught:
            confirm_checks(self.record, 0, keys)
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(HTTPException) as caught:
            confirm_checks(self.record, 1, keys)
        self.assertEqual(caught.exception.status_code, 422)
        new_keys = self.keys()
        confirm_checks(self.record, 1, new_keys)
        changed = _report(issues=[_warning()])
        changed["rows"][0]["sentence"] = "Changed without bumping revision"
        self.write(changed)
        self.assertFalse(publication_gate(self.record)["passed"])
        self.assertNotEqual(self.keys(), new_keys)

    def test_stable_keys_ignore_json_format_and_object_key_order(self) -> None:
        keys = self.keys()
        payload = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        (self.root / "report.json").write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        self.assertEqual(self.keys(), keys)

    def test_report_and_sidecar_deduplicate_without_dropping_real_errors(self) -> None:
        report = _report(issues=[_warning()])
        self.write(report)
        write_json_atomic(self.root / "quality_report.json", report["quality"])
        self.assertEqual(publication_gate(self.record)["pending_count"], 1)
        report["checks"] = [_warning()]
        self.write(report)
        self.assertEqual(publication_gate(self.record)["pending_count"], 1)
        write_json_atomic(self.root / "quality_report.json", {"issues": [_warning("FREEZE_PAD_EXCESSIVE", severity="error", level=0)],
                                                               "blocking_issue_count": 1, "warning_count": 0})
        gate = publication_gate(self.record)
        self.assertEqual((gate["blocking_count"], gate["pending_count"]), (1, 1))

    def test_level_two_is_read_only_and_not_pending(self) -> None:
        self.write(_report(issues=[_warning("MIXED_NO_NARRATION", level=2)]))
        gate = publication_gate(self.record)
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["pending_count"], 0)
        with self.assertRaises(HTTPException) as caught:
            confirm_checks(self.record, 0, [gate["checks"][0]["key"]])
        self.assertEqual(caught.exception.status_code, 422)

    def test_deduplication_precedes_unique_cap_but_raw_inputs_are_bounded(self) -> None:
        for count in (667, MAX_CHECKS):
            with self.subTest(unique=count):
                values = [_warning(message=f"Distinct issue {index}") for index in range(count)]
                report = _report(issues=values)
                report["checks"] = values
                self.write(report)
                write_json_atomic(self.root / "quality_report.json", report["quality"])
                gate = publication_gate(self.record)
                self.assertEqual((gate["blocking_count"], gate["pending_count"]), (0, count))
                self.assertEqual(len(gate["checks"]), count)
                self.assertTrue(confirm_checks(self.record, 0, self.keys())["passed"])
        # A list of duplicates is still bounded before normalization; it is not
        # permission to parse an arbitrarily large adversarial issue list.
        report["checks"] = [_warning()] * (MAX_CHECKS + 1)
        self.write(report)
        gate = publication_gate(self.record)
        self.assertEqual([c["code"] for c in gate["checks"]], ["PUBLICATION_REPORT_INVALID"])
        self.assertFalse(gate["passed"])

    def test_unique_union_over_cap_and_oversized_sidecar_fail_closed(self) -> None:
        report = _report(issues=[_warning(message=f"Issue {i}") for i in range(MAX_CHECKS)])
        report["checks"] = [_warning(message="One additional distinct warning")]
        self.write(report)
        self.assertEqual(publication_gate(self.record)["checks"][0]["code"], "PUBLICATION_REPORT_INVALID")
        self.write(_report())
        write_json_atomic(self.root / "quality_report.json", {
            "blocking_issue_count": 0, "warning_count": MAX_CHECKS + 1,
            "issues": [_warning()] * (MAX_CHECKS + 1),
        })
        self.assertFalse(publication_gate(self.record)["passed"])

    def test_same_code_preserves_sentence_message_and_beat_identity(self) -> None:
        values = [_warning(sentence=0), _warning(sentence=1),
                  _warning(message="Different actionable diagnostic"), _warning(beat_id=1)]
        report = _report(issues=values)
        report["rows"].append({**report["rows"][0], "sentence_id": 1})
        report["checks"] = values
        self.write(report)
        write_json_atomic(self.root / "quality_report.json", report["quality"])
        self.assertEqual(publication_gate(self.record)["pending_count"], 4)

    def test_known_global_fact_warning_becomes_exact_current_row_checks(self) -> None:
        values = [_warning("FACT_CHECK", sentence=2, action="去看", message=
                  "请核对稿子里的数字、人名、日期（问过当事人 / 看过公告）——第 3、5、8 句。")]
        report = _report(issues=values)
        base = report["rows"][0]
        report["rows"] = [{**base, "sentence_id": i, "sentence": "Ordinary statement"} for i in range(8)]
        report["rows"][2]["sentence"] = "三十人到场"
        report["rows"][4]["sentence"] = "活动在 2026 年举行"
        report["rows"][7]["spoken_text"] = "主任致辞"
        report["checks"] = values
        self.write(report)
        write_json_atomic(self.root / "quality_report.json", report["quality"])
        gate = publication_gate(self.record)
        self.assertEqual([(c["code"], c["sentence_id"]) for c in gate["checks"]],
                         [("FACT_CHECK", 2), ("FACT_CHECK", 4), ("FACT_CHECK", 7)])
        self.assertEqual(gate["pending_count"], 3)
        self.assertTrue(all("——第" not in c["message"] for c in gate["checks"]))
        self.assertFalse(confirm_checks(self.record, 0, self.keys()[:2])["passed"])
        self.assertTrue(confirm_checks(self.record, 0, self.keys())["passed"])

    def test_global_fact_errors_and_unrecognized_warnings_are_never_suppressed(self) -> None:
        aggregate = "请核对稿子里的数字、人名、日期（问过当事人 / 看过公告）——第 1 句。"
        for issue in (_warning("FACT_CHECK", message=aggregate, severity="error", level=1),
                      _warning("FACT_CHECK", sentence=None, message="Unrecognized global fact concern"),
                      _warning("FACT_CHECK", message="请核对稿子里的数字、人名、日期（问过当事人 / 看过公告）——第 8 句。")):
            with self.subTest(message=issue["message"]):
                report = _report(issues=[issue])
                report["rows"][0]["sentence"] = "三十人到场"
                self.write(report)
                gate = publication_gate(self.record)
                self.assertTrue(any(c["message"] == issue["message"] for c in gate["checks"]))
                self.assertFalse(gate["passed"])
                if issue["severity"] == "error":
                    self.assertEqual(gate["blocking_count"], 1)
                    with self.assertRaises(HTTPException):
                        confirm_checks(self.record, 0, [gate["checks"][0]["key"]])
                elif issue["sentence_id"] is None:
                    self.assertEqual(gate["pending_count"], 2, "Global concern cannot cover a factual row")

    def test_fact_aggregate_with_count_detailed_rows_and_narration_error(self) -> None:
        global_issue = _warning("FACT_CHECK", message=
            "请核对稿子里的数字、人名、日期（问过当事人 / 看过公告）——第 1、2、3 句等共 4 句。")
        detailed = _warning("FACT_CHECK", sentence=1, message="第 2 句的事实需核对", action="去看")
        critical = _warning("NARRATION_FACT_CRITICAL", sentence=2, severity="error", level=0,
                            message="旁白关键事实未核实")
        report = _report(issues=[global_issue, detailed, critical])
        report["rows"] = [{**report["rows"][0], "sentence_id": i, "sentence": "三十人到场"} for i in range(4)]
        report["checks"] = [global_issue, detailed, critical]
        self.write(report)
        write_json_atomic(self.root / "quality_report.json", report["quality"])
        gate = publication_gate(self.record)
        self.assertEqual((gate["blocking_count"], gate["pending_count"]), (1, 4))
        facts = [c for c in gate["checks"] if c["code"] == "FACT_CHECK"]
        self.assertEqual([c["sentence_id"] for c in facts], [0, 1, 2, 3])
        self.assertEqual(facts[1]["message"], detailed["message"])
        self.assertFalse(confirm_checks(self.record, 0, self.keys())["passed"])
        with self.assertRaises(HTTPException):
            confirm_checks(self.record, 0, [c["key"] for c in gate["checks"]])

    def test_speaker_name_in_quote_evidence_and_chinese_spoken_number_require_checks(self) -> None:
        report = _report()
        report["speakers"] = [{"id": "speaker", "name": "Alice"}]
        report["rows"][0]["sentence"] = "Alice attended"
        report["rows"].append({**report["rows"][0], "sentence_id": 1,
                                "sentence": "Ordinary narration", "spoken_text": "三十"})
        self.write(report)
        self.assertEqual(publication_gate(self.record)["pending_count"], 2)

    def test_facts_come_from_current_report_not_browser_state(self) -> None:
        report = _report()
        report["rows"][0]["sentence"] = "主任说共有 12 人"
        self.write(report)
        self.record.quality = {"issues": []}
        self.record.checked = {"FACT_CHECK": True}
        gate = publication_gate(self.record)
        self.assertEqual([c["code"] for c in gate["checks"]], ["FACT_CHECK"])
        self.assertFalse(gate["passed"])
        self.assertTrue(confirm_checks(self.record, 0, self.keys())["passed"])

    def test_missing_report_or_quality_blocks_new_and_legacy_tasks(self) -> None:
        variants: list[dict[str, Any]] = [{"task_id": "task", "rows": []}, {"task_id": "task", "rows": _report()["rows"]}]
        for payload in variants:
            self.write(payload)
            self.assertFalse(publication_gate(self.record)["passed"])
        (self.root / "report.json").unlink()
        self.assertFalse(publication_gate(self.record)["passed"])
        del self.record.mode
        del self.record.mode_contract
        legacy = publication_gate(self.record)
        self.assertFalse(legacy["passed"])
        self.assertEqual(legacy["checks"][0]["code"], "PUBLICATION_QC_UNAVAILABLE")
        write_json_atomic(self.root / "quality_report.json", _report()["quality"])
        self.assertFalse(publication_gate(self.record)["passed"], "Sidecar alone is not a committed report")
        self.write({"task_id": "task", "rows": _report()["rows"]})
        self.assertFalse(publication_gate(self.record)["passed"], "Sidecar cannot replace missing embedded QC")
        self.write(_report())
        self.assertTrue(publication_gate(self.record)["passed"], "Complete passed legacy QC remains supported")
        self.write(_report(issues=[_warning()]))
        self.assertFalse(publication_gate(self.record)["passed"], "Legacy actual warnings must still be confirmed")

    def test_malformed_metadata_count_mismatch_and_wrong_identity_fail_closed(self) -> None:
        bad: list[Any] = [None, [], {"task_id": "foreign", "rows": []},
                          {"task_id": "task", "revision": 3, "rows": []},
                          {"task_id": "task", "rows": [], "quality": {"issues": "bad"}},
                          {"task_id": "task", "rows": _report()["rows"], "quality": {}},
                          {"task_id": "task", "rows": [], "quality": {"blocking_issue_count": 1, "issues": []}},
                          {"task_id": "task", "rows": _report()["rows"], "quality": {"warning_count": 2, "issues": []}}]
        for payload in bad:
            self.write(payload)
            self.assertFalse(publication_gate(self.record)["passed"], str(payload))
        (self.root / "report.json").write_bytes(b"{invalid json")
        gate = publication_gate(self.record)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["checks"][0]["code"], "PUBLICATION_REPORT_INVALID")

    def test_in_progress_and_boolean_revision_confirmation_rejected(self) -> None:
        keys = self.keys()
        for status in ("queued", "running", "failed", "cancelled"):
            self.record.status = status
            self.assertFalse(publication_gate(self.record)["passed"])
            with self.assertRaises(HTTPException) as caught:
                confirm_checks(self.record, 0, keys)
            self.assertEqual(caught.exception.status_code, 409)
        self.record.status = "done"
        invalid: list[Any] = [True, "0", 0.0, -1]
        for revision in invalid:
            with self.assertRaises(HTTPException) as caught:
                confirm_checks(self.record, revision, keys)
            self.assertEqual(caught.exception.status_code, 422)

    def test_corrupt_saved_acknowledgements_never_grant_publication(self) -> None:
        variants: list[dict[str, Any]] = [{"checked_keys": self.keys()},
            {"schema_version": 1, "revision": 0, "report_sha256": "bad", "checked_keys": self.keys()}]
        for payload in variants:
            write_json_atomic(self.root / CHECKS_FILE, payload)
            self.assertFalse(publication_gate(self.record)["passed"])
        (self.root / CHECKS_FILE).write_bytes(b"invalid json")
        self.assertFalse(publication_gate(self.record)["passed"])

    def test_busy_with_previously_confirmed_root_cannot_export_or_write(self) -> None:
        confirm_checks(self.record, 0, self.keys())
        before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        for status, background in (("queued", None), ("running", None),
                                   ("done", SimpleNamespace(done=lambda: False))):
            with self.subTest(status=status):
                self.record.status, self.record.background = status, background
                gate = publication_gate(self.record)
                self.assertFalse(gate["passed"])
                self.assertTrue(any(c["code"] == "PUBLICATION_NOT_READY" for c in gate["checks"]))
                with self.assertRaises(HTTPException) as caught:
                    require_publication(self.record)
                self.assertEqual(caught.exception.status_code, 409)
                with self.assertRaises(HTTPException) as caught:
                    confirm_checks(self.record, 0, self.keys())
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}, before)

    def test_atomic_confirmation_failure_keeps_previous_bytes(self) -> None:
        confirm_checks(self.record, 0, self.keys())
        before = (self.root / CHECKS_FILE).read_bytes()
        with patch("backend.publication.write_json_atomic", side_effect=OSError("disk failure")):
            with self.assertRaises(HTTPException) as caught:
                confirm_checks(self.record, 0, [])
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual((self.root / CHECKS_FILE).read_bytes(), before)
        self.assertTrue(publication_gate(self.record)["passed"])

    def test_private_diagnostics_are_not_reflected(self) -> None:
        issue = _warning(message=f"{self.root}/private token=private-value https://example.invalid/x?token=private-value Bearer private-bearer")
        self.write(_report(issues=[issue]))
        public = json.dumps(publication_gate(self.record))
        self.assertNotIn("private-value", public)
        self.assertNotIn("private-bearer", public)
        self.assertNotIn(str(self.root), public)

    def generated(self, *, disclose: bool = True, shots: int = 1) -> None:
        report = _report()
        self.write(report)
        write_json_atomic(self.root / "edl.json", [{"sentence_id": 0, "clips": [
            {"shot_id": i, "src": f"generated/{i}.mp4", "in": 0, "out": 2, "media_origin": "generated"} for i in range(shots)]}])
        if disclose:
            write_json_atomic(self.root / "generated_media_disclosure.json", {
                "schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence",
                "items": [{"sentence_id": 0, "beat_id": 0, "shot_id": 0, "mode": "image",
                           "prompt_sha256": "a" * 64, "disclosure_text": "AI生成示意画面"}],
            })

    def test_only_server_derived_generated_notice_can_confirm_at_level_zero(self) -> None:
        self.generated()
        gate = publication_gate(self.record)
        self.assertEqual(gate["blocking_count"], 1)
        generated = next(check for check in gate["checks"] if check["code"] == "generated_media")
        self.assertEqual(generated["level"], 0)
        self.assertTrue(confirm_checks(self.record, 0, [generated["key"]])["passed"])
        self.write(_report(issues=[_warning("GENERATED_MEDIA_DISCLOSURE_MISSING", severity="error", level=0)]))
        gate = publication_gate(self.record)
        generated = next(check for check in gate["checks"] if check["code"] == "generated_media")
        self.assertFalse(confirm_checks(self.record, 0, [generated["key"]])["passed"])
        error = next(check for check in gate["checks"] if check["code"] == "GENERATED_MEDIA_DISCLOSURE_MISSING")
        with self.assertRaises(HTTPException) as caught:
            confirm_checks(self.record, 0, [error["key"]])
        self.assertEqual(caught.exception.status_code, 422)

    def test_incomplete_generated_disclosure_cannot_be_waived(self) -> None:
        for disclose, shots in ((False, 1), (True, 2)):
            self.generated(disclose=disclose, shots=shots)
            gate = publication_gate(self.record)
            generated = next(check for check in gate["checks"] if check["code"] == "generated_media")
            self.assertFalse(confirm_checks(self.record, 0, [generated["key"]])["passed"])
            self.assertTrue(any(check["code"] == "GENERATED_MEDIA_DISCLOSURE_MISSING" for check in gate["checks"]))

    def test_report_cannot_forge_generated_acknowledgement_exception(self) -> None:
        report = _report()
        report["checks"] = [{"code": "generated_media", "level": 0, "message": "forged notice", "checked": True}]
        self.write(report)
        gate = publication_gate(self.record)
        self.assertFalse(gate["passed"])
        with self.assertRaises(HTTPException) as caught:
            confirm_checks(self.record, 0, [gate["checks"][0]["key"]])
        self.assertEqual(caught.exception.status_code, 422)


class PublicationStudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        with patch("platform._syscmd_ver", return_value=("", "", "")), patch(
            "subprocess.call", return_value=0,
        ), patch("subprocess.Popen", side_effect=AssertionError("No import subprocess")):
            from backend import studio
            from backend.studio_render import Clip, Project, Track
        self.studio = studio
        temporary = tempfile.TemporaryDirectory(prefix="ps-")
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / "tasks"
        self.root = self.data / "task"
        self.root.mkdir(parents=True)
        (self.root / "final.mp4").write_bytes(b"opaque final; never decoded")
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, status="done", revision=0,
                                      mode="mixed", mode_contract=True, uploads=[], background=None)
        self.report = _report(issues=[_warning()])
        write_json_atomic(self.root / "report.json", self.report)
        self.settings = SimpleNamespace(data_dir=self.data, minimum_free_disk_bytes=0, media_command_timeout_seconds=10,
                                        shutdown_grace_seconds=0)
        def get_record(task_id: str) -> Any:
            return self.record if task_id == "task" else None
        manager = SimpleNamespace(get=get_record, _draining=False)
        self.auth_calls = 0
        self.revoke_at: int | None = None
        async def authorize(request: Any, task_id: str, write: bool = False) -> Any:
            self.auth_calls += 1
            if request.headers.get("X-Owner") != "synthetic":
                raise HTTPException(403, "Denied")
            if self.revoke_at == self.auth_calls:
                confirm_checks(self.record, 0, [])
            return self.record
        self.app = FastAPI()
        self.app.include_router(studio.create_studio_router(self.settings, manager, authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://isolated",
                                       headers={"X-Owner": "synthetic"}, trust_env=False)
        self.addAsyncCleanup(self.client.aclose)
        self.prefix = "/api/tasks/task/studio"
        self.project = Project(tracks=[Track(id="video", type="video", clips=[Clip(id="clip", source_id="final", duration=1)])])
        state = studio.read_state(self.root)
        state["project"] = self.project.model_dump(mode="json")
        studio.write_state(self.root, state)
        self.simple = self.enterContext(patch.object(studio, "simple_project", new=AsyncMock(return_value=self.project)))
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.hold = False
        async def render(project: Any, options: Any, sources: Any, work: Path, **kwargs: Any) -> dict[str, Any]:
            self.started.set()
            if self.hold:
                await self.release.wait()
            output = work / "output.mp4"
            output.write_bytes(b"opaque derivative; no encoding")
            content = output.read_bytes()
            return {"file": output.name, "bytes": len(content), "duration": 1, "format": "mp4",
                    "sha256": hashlib.sha256(content).hexdigest()}
        self.render = self.enterContext(patch.object(studio, "render_project", new=AsyncMock(side_effect=render)))
        for target in ("subprocess.Popen", "httpx.HTTPTransport.handle_request", "httpx.AsyncHTTPTransport.handle_async_request"):
            self.enterContext(patch(target, side_effect=AssertionError("Offline boundary crossed")))

    async def asyncTearDown(self) -> None:
        jobs = [job for key, job in self.studio._RUNNING.items() if key.startswith(str(self.root))]
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    def confirm(self) -> None:
        confirm_checks(self.record, 0, [check["key"] for check in publication_gate(self.record)["checks"] if check["level"] == 1])

    async def post(self, endpoint: str) -> httpx.Response:
        return await self.client.post(self.prefix + endpoint, json={"expected_revision": 0, "options": {"format": "mp4"}})

    async def wait_job(self, response: httpx.Response) -> dict[str, Any]:
        path = self.root / "studio/jobs" / f"{response.json()['id']}.json"
        task = self.studio._RUNNING.get(str(path))
        if task is not None:
            await task
        return json.loads(path.read_text(encoding="utf-8"))

    async def test_unconfirmed_simple_and_nle_block_before_work_allocation(self) -> None:
        for endpoint in ("/export", "/render"):
            response = await self.post(endpoint)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "publication_checks_required")
        self.simple.assert_not_awaited()
        self.render.assert_not_awaited()
        self.assertFalse((self.root / "studio/jobs").exists())
        self.assertFalse((self.root / "studio/outputs").exists())

    async def test_confirmed_simple_and_nle_export_and_download(self) -> None:
        self.confirm()
        for endpoint in ("/export", "/render"):
            response = await self.post(endpoint)
            self.assertEqual(response.status_code, 202, response.text)
            job = await self.wait_job(response)
            self.assertEqual(job["state"], "succeeded", job.get("error"))
            media = await self.client.get(self.prefix + "/outputs/" + job["id"])
            self.assertEqual(media.status_code, 200, media.text)
            self.assertEqual(media.content, b"opaque derivative; no encoding")
            self.assertEqual(job["result"]["bytes"], len(media.content))
            self.assertEqual(job["result"]["sha256"], hashlib.sha256(media.content).hexdigest())
        self.assertEqual(self.render.await_count, 2)

    async def test_confirmed_simple_and_nle_tampered_output_still_blocks_download(self) -> None:
        self.confirm()
        for endpoint in ("/export", "/render"):
            with self.subTest(endpoint=endpoint):
                response = await self.post(endpoint)
                self.assertEqual(response.status_code, 202, response.text)
                job = await self.wait_job(response)
                self.assertEqual(job["state"], "succeeded", job.get("error"))
                url = self.prefix + "/outputs/" + job["id"]
                media = await self.client.get(url)
                self.assertEqual(media.status_code, 200, media.text)
                output = self.root / "studio/outputs" / f"r{job['revision']}" / job["id"] / job["result"]["file"]
                job_path = self.root / "studio/jobs" / f"{job['id']}.json"
                manifest_path = output.parent / "manifest.json"
                metadata = (job_path.read_bytes(), manifest_path.read_bytes())
                original = output.read_bytes()
                self.assertEqual(media.content, original)
                self.assertEqual(job["result"]["bytes"], len(original))
                self.assertEqual(job["result"]["sha256"], hashlib.sha256(original).hexdigest())
                # Change bytes, not length, revision or publication approval.
                tampered = bytes([original[0] ^ 1]) + original[1:]
                output.write_bytes(tampered)
                self.assertEqual(output.stat().st_size, job["result"]["bytes"])
                self.assertNotEqual(hashlib.sha256(tampered).hexdigest(), job["result"]["sha256"])
                self.assertTrue(publication_gate(self.record)["passed"])
                rejected = await self.client.get(url)
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertEqual(rejected.json()["detail"], "mode output integrity verification failed")
                self.assertEqual((job_path.read_bytes(), manifest_path.read_bytes()), metadata)
        self.assertEqual(self.render.await_count, 2)

    async def test_post_probe_reauthorization_rechecks_acknowledgements(self) -> None:
        self.confirm()
        async def revoke(*args: Any, **kwargs: Any) -> Any:
            confirm_checks(self.record, 0, [])
            return self.project
        self.simple.side_effect = revoke
        response = await self.post("/export")
        self.assertEqual(response.status_code, 409, response.text)
        self.render.assert_not_awaited()
        self.assertFalse((self.root / "studio/jobs").exists())
        self.assertNotIn(str(self.root), self.studio._BUSY)

    async def test_nle_final_reauthorization_rechecks_gate(self) -> None:
        self.confirm()
        self.revoke_at = self.auth_calls + 3
        response = await self.post("/render")
        self.assertEqual(response.status_code, 409, response.text)
        self.render.assert_not_awaited()

    async def test_gate_rechecks_after_encoding_and_discards_revoked_output(self) -> None:
        self.confirm()
        self.hold = True
        response = await self.post("/render")
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.wait_for(self.started.wait(), 5)
        confirm_checks(self.record, 0, [])
        self.release.set()
        job = await self.wait_job(response)
        self.assertEqual(job["state"], "failed")
        self.assertIsNone(job["output_id"])
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job["id"])).status_code, 409)

    async def test_prior_download_cannot_borrow_new_revision_confirmation(self) -> None:
        self.confirm()
        response = await self.post("/render")
        self.assertEqual(response.status_code, 202, response.text)
        job = await self.wait_job(response)
        self.record.revision = 1
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job["id"])).status_code, 409)
        confirm_checks(self.record, 1, [check["key"] for check in publication_gate(self.record)["checks"] if check["level"] == 1])
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job["id"])).status_code, 409)

    async def test_raw_proxy_previews_do_not_call_publication_gate(self) -> None:
        response = await self.client.get(self.prefix + "/proxies")
        self.assertEqual(response.status_code, 200, response.text)
        # Invalid source reaches the RAW source validation, not the pending
        # publication gate. A successful proxy needs real hashing/encoding and
        # is deliberately outside this mock-only validation.
        with patch.object(self.studio, "require_publication", side_effect=AssertionError("RAW must not be gated")):
            response = await self.client.post(self.prefix + "/proxies", json={"expected_revision": 0, "source_id": "unknown"})
        self.assertEqual(response.status_code, 422, response.text)

    async def test_legacy_without_qc_keeps_read_and_proxy_but_blocks_exports(self) -> None:
        self.record.mode, self.record.mode_contract = "voiceover", False
        (self.root / "report.json").unlink()
        self.assertEqual((await self.client.get(self.prefix + "/proxies")).status_code, 200)
        with patch.object(self.studio, "require_publication", side_effect=AssertionError("Preview must not be gated")):
            preview = await self.client.post(self.prefix + "/proxies", json={"expected_revision": 0, "source_id": "unknown"})
        self.assertEqual(preview.status_code, 422)
        for endpoint in ("/export", "/render"):
            response = await self.post(endpoint)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertTrue(any(c["code"] == "PUBLICATION_QC_UNAVAILABLE" for c in response.json()["detail"]["checks"]))
        self.render.assert_not_awaited()
        self.simple.assert_not_awaited()
        self.assertFalse((self.root / "studio/jobs").exists())


if __name__ == "__main__":
    unittest.main()