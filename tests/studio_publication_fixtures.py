"""Explicit synthetic publication evidence for isolated Studio router tests.

These are schema-valid test reports, NOT measured QC or factual verification.
Never call this from authorization, request wrappers or renderer doubles: each
fixture author must deliberately acknowledge the current evidence before export.
No settings, main singleton, network or provider imports.
"""
from __future__ import annotations

from typing import Any
from unittest import TestCase

from backend.models import QualityIssue, QualitySummary, ReportResponse, ReportRow
from backend.publication import confirm_checks, publication_gate


REVIEW_CODE = "STUDIO_SYNTHETIC_REVIEW"
QC_BLOCKER = "pipeline QC blockers must be resolved before studio render/export"


def synthetic_publication_files(*, blocked: bool = False) -> dict[str, bytes]:
    """Build both report and sidecar with matching, explicit QC issue counts."""
    quality = QualitySummary(
        blocking_issue_count=int(blocked), warning_count=1,
        metrics={"fixture": "synthetic; not measured media QC"},
        issues=[QualityIssue(code=REVIEW_CODE, severity="warning", level=1,
                             message="Review this synthetic Studio fixture before export."),
                *([QualityIssue(code="SYNTHETIC_QC_BLOCKER", severity="error", level=0,
                                message="Deliberate synthetic QC failure.")] if blocked else [])],
    )
    report = ReportResponse(task_id="task", rows=[ReportRow(
        sentence_id=0, sentence="Synthetic Studio fixture", shot_id=0,
        thumb_url=None, description="Synthetic fixture; not news evidence",
        duration=2.0, confidence=1.0, is_fallback=False,
    )], quality=quality)
    return {"report.json": report.model_dump_json().encode("utf-8"),
            "quality_report.json": quality.model_dump_json().encode("utf-8")}


def confirm_synthetic_publication(check: TestCase, record: Any, *, generated: bool = False) -> None:
    """Assert the exact pending obligations, then use real revision-bound consent."""
    gate = publication_gate(record)
    expected = {REVIEW_CODE, "generated_media"} if generated else {REVIEW_CODE}
    check.assertEqual({item["code"] for item in gate["checks"]}, expected, gate)
    check.assertEqual(len(gate["checks"]), len(expected), gate)
    check.assertFalse(gate["passed"], gate)
    check.assertEqual(gate["pending_count"], 1, gate)
    check.assertEqual(gate["blocking_count"], int(generated), gate)
    for item in gate["checks"]:
        check.assertFalse(item["checked"], gate)
        check.assertEqual(item["level"], 0 if item["code"] == "generated_media" else 1)
    confirmed = confirm_checks(record, record.revision, [item["key"] for item in gate["checks"]])
    check.assertTrue(confirmed["passed"], confirmed)
    check.assertEqual((confirmed["blocking_count"], confirmed["pending_count"]), (0, 0))
    check.assertTrue(all(item["checked"] for item in confirmed["checks"]))
    check.assertEqual(publication_gate(record), confirmed)


def seed_synthetic_publication(check: TestCase, record: Any) -> dict[str, bytes]:
    files = synthetic_publication_files()
    for name, content in files.items():
        (record.task_dir / name).write_bytes(content)
    confirm_synthetic_publication(check, record)
    return files


def assert_publication_rejected(check: TestCase, response: Any, code: str) -> None:
    check.assertEqual(response.status_code, 409, response.text)
    detail = response.json()["detail"]
    check.assertEqual(detail["code"], "publication_checks_required")
    check.assertFalse(detail["passed"])
    check.assertEqual(detail["blocking_count"], 1)
    check.assertEqual(detail["pending_count"], 0)
    check.assertEqual([item["code"] for item in detail["checks"]], [code])