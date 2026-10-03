"""Revision-bound publication acknowledgements; no settings, providers or routes.

The host MUST authorize before calling these synchronous functions, on the same
owning event loop as revision publication. A confirmation replaces the complete
checked-key set (an empty list revokes it). GET never writes or repairs anything.

All actual backend QC warnings, including legacy ones, require confirmation;
errors cannot be waived. All tasks fail closed without a committed report AND
its QC, including pre-contract/imported Studio work. Preview remains separate
from publication. Malformed/present QC always fails closed. Existing
QC sidecars cannot weaken the report. No frontend-computed quality is accepted.

Only generated_media notices DERIVED HERE from current media provenance may be
confirmed at level 0. Missing disclosure, fallback and other real errors remain
unconfirmable. Acknowledging generated imagery never establishes news truth.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .models import GeneratedMediaDisclosureManifest, ReportResponse, is_generated_media_path
from .revisions import RevisionError, local_file, read_json
from .storage import sanitize_sensitive_text, write_json_atomic


CHECKS_FILE = "publication_checks.json"
MAX_CHECKS = 2000
_KEY = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,119}$")
_FACT = re.compile(r"\d|[零〇一二两三四五六七八九十百千万亿]|主办|协办|承办|先生|女士|主任|经理|负责人|师傅|教授|博士")
_GLOBAL_FACT = re.compile(
    r"请核对稿子里的数字、人名、日期（问过当事人 / 看过公告）——第 (\d+(?:、\d+)*) 句(?:等共 (\d+) 句)?。"
)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _revision(record: Any) -> int:
    value = getattr(record, "revision", 0)
    if type(value) is not int or value < 0:
        raise HTTPException(409, {"code": "invalid_revision", "message": "Current revision is unavailable"})
    return value


def _safe_message(value: str, root: Path) -> str:
    text = sanitize_sensitive_text(value).replace(str(root), "[task]").replace(root.as_posix(), "[task]")
    text = re.sub(r"[A-Za-z]:[\\/][^\s\"<>]+", "[local path]", text)
    text = re.sub(r"(?:https?://|/)[^\s]*\?[^\s]+", "[private URL]", text)
    text = re.sub(r"(?i)((?:token|secret|password)\s*[=:]\s*)[^\s,;\"']+", r"\1***", text)
    return "".join(c for c in text if ord(c) >= 32 or c in "\n\t")[:2000]


def _optional_json(root: Path, name: str) -> Any:
    path = local_file(root, name, exists=False)
    if not path.exists():
        return None
    value = read_json(root, name)
    if value is None:
        raise RevisionError("Present publication metadata cannot be null")
    return value


def _issue(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RevisionError("Invalid publication issue")
    code, message = value.get("code"), value.get("message")
    level, severity = value.get("level"), value.get("severity")
    sentence_id = value.get("sentence_id")
    action = value.get("action", "")
    if (not isinstance(code, str) or not _CODE.fullmatch(code)
            or not isinstance(message, str) or not message.strip()
            or not isinstance(action, str)
            or (sentence_id is not None and (type(sentence_id) is not int or sentence_id < 0))
            or (level is not None and (type(level) is not int or level not in (0, 1, 2)))
            or severity not in (None, "error", "warning", "info")
            or (level is None and severity is None)):
        raise RevisionError("Invalid publication issue")
    # An explicitly contradictory level never downgrades an actual error.
    actual = 0 if severity == "error" or level == 0 else (level if level is not None else 2 if severity == "info" else 1)
    return {"code": code, "level": actual, "message": message,
            "sentence_id": sentence_id, "action": action,
            "beat_id": value.get("beat_id"), "shot_id": value.get("shot_id")}


def _notice(code: str, level: int, message: str, sentence_id: int | None = None,
            action: str = "") -> dict[str, Any]:
    return {"code": code, "level": level, "message": message, "sentence_id": sentence_id,
            "action": action, "beat_id": None, "shot_id": None}


def _ready(record: Any) -> bool:
    background = getattr(record, "background", None)
    return getattr(record, "status", None) == "done" and (background is None or background.done())


def _canonical(record: Any, revision: int) -> tuple[list[dict[str, Any]], set[str], str]:
    """Return public checks, confirmable keys and a private content binding."""
    root = Path(record.task_dir)
    issues: list[tuple[dict[str, Any], bool]] = []
    issue_keys: set[str] = set()
    evidence: dict[str, Any] = {}
    mode_contract = bool(getattr(record, "mode_contract", False)) or getattr(record, "mode", "voiceover") != "voiceover"

    def add(issue: dict[str, Any], confirmable: bool = False) -> None:
        can_confirm = confirmable or issue["level"] == 1
        identity = _hash({"issue": issue, "confirmable": can_confirm})
        if identity in issue_keys:
            return
        issue_keys.add(identity)
        issues.append((issue, can_confirm))
        if len(issues) > MAX_CHECKS:
            raise RevisionError("Too many publication checks")

    try:
        report = _optional_json(root, "report.json")
        quality = _optional_json(root, "quality_report.json")
        evidence.update(report=report, quality=quality)
        if report is not None and not isinstance(report, dict):
            raise RevisionError("Invalid committed report")
        # Bound raw work BEFORE Pydantic validation or deduplication. JSON reads
        # are independently byte-bounded by read_json; at most 3 * 2000 issues
        # may be inspected across embedded QC, sidecar QC and report checks.
        raw_report = report or {}
        raw_lists = [raw_report.get("checks", [])]
        for summary in (raw_report.get("quality"), quality):
            if summary is not None:
                if not isinstance(summary, dict):
                    raise RevisionError("Invalid committed quality summary")
                raw_lists.append(summary.get("issues", []))
        if any(not isinstance(values, list) or len(values) > MAX_CHECKS for values in raw_lists):
            raise RevisionError("Too many raw publication checks")
        if mode_contract and report is not None:
            ReportResponse.model_validate(report)
        report = report or {}
        if report.get("task_id", getattr(record, "task_id", None)) != getattr(record, "task_id", None):
            raise RevisionError("Report belongs to another task")
        if "revision" in report and (type(report["revision"]) is not int or report["revision"] != revision):
            raise RevisionError("Report belongs to another revision")
        rows = report.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 200:
            raise RevisionError("Invalid committed report rows")
        row_ids: set[int] = set()
        selected: dict[int, set[int]] = {}
        generated: set[int] = set()
        generated_by_row: dict[int, set[int]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise RevisionError("Invalid committed report row")
            sentence_id = row.get("sentence_id")
            if type(sentence_id) is not int or sentence_id < 0 or sentence_id in row_ids:
                raise RevisionError("Invalid committed sentence identity")
            row_ids.add(sentence_id)
            selected[sentence_id] = set()
            beats = row.get("visual_beats", [])
            if not isinstance(beats, list) or any(not isinstance(beat, dict) for beat in beats):
                raise RevisionError("Invalid committed visual beats")
            for item in [row, *beats]:
                shot_id = item.get("shot_id")
                if shot_id is not None:
                    if type(shot_id) is not int or shot_id < 0:
                        raise RevisionError("Invalid committed shot identity")
                    selected[sentence_id].add(shot_id)
                if item.get("media_origin") == "generated":
                    generated.add(sentence_id)
                    if shot_id is not None:
                        generated_by_row.setdefault(sentence_id, set()).add(shot_id)
        people = report.get("speakers", [])
        if not isinstance(people, list) or any(not isinstance(person, dict) for person in people):
            raise RevisionError("Invalid committed speakers")
        person_names = [person.get("name", "") for person in people]
        if any(not isinstance(name, str) for name in person_names):
            raise RevisionError("Invalid committed speaker name")
        fact_positions: dict[int, int] = {}
        for position, row in enumerate(rows, 1):
            source = row.get("source") or {}
            if not isinstance(source, dict):
                raise RevisionError("Invalid committed quote source")
            texts = [row.get("sentence", ""), row.get("spoken_text") or "", source.get("asr_text", "")]
            if any(not isinstance(text, str) for text in texts):
                raise RevisionError("Invalid committed sentence text")
            text = " ".join(texts)
            if _FACT.search(text) or any(name.strip() and name.strip() in text for name in person_names):
                fact_positions[position] = row["sentence_id"]

        def add_report_issue(item: dict[str, Any]) -> None:
            # Only the known warning-only aggregate is replaced. Its first
            # sentence_id is not a genuine row binding. Unknown messages and
            # ALL errors are retained, even when they use the FACT_CHECK code.
            match = _GLOBAL_FACT.fullmatch(item["message"]) if item["code"] == "FACT_CHECK" and item["level"] == 1 else None
            if match and item["beat_id"] is None and item["shot_id"] is None:
                positions = [int(value) for value in match[1].split("、")]
                count = int(match[2]) if match[2] else len(positions)
                if (positions and len(set(positions)) == len(positions)
                        and all(position in fact_positions for position in positions)
                        and len(positions) <= count <= len(fact_positions)
                        and item["sentence_id"] in (None, fact_positions[positions[0]])):
                    return
            add(item)

        summaries = [report.get("quality"), quality]
        if not rows or not isinstance(report.get("quality"), dict):
            add(_notice("PUBLICATION_QC_UNAVAILABLE", 0, "当前版本缺少完整的成片报告或质量检查，不能发布。"))
        for summary in summaries:
            if summary is None:
                continue
            if (not isinstance(summary, dict) or not isinstance(summary.get("issues", []), list)
                    or not {"blocking_issue_count", "warning_count"}.issubset(summary)):
                raise RevisionError("Invalid committed quality summary")
            raw_issues = summary.get("issues", [])
            canonical = [_issue(value) for value in raw_issues]
            for item in canonical:
                add_report_issue(item)
            for name, observed in (("blocking_issue_count", sum(i["level"] == 0 for i in canonical)),
                                   ("warning_count", sum(i["level"] in (1, 2) for i in canonical))):
                count = summary.get(name, 0)
                if type(count) is not int or count < 0:
                    raise RevisionError("Invalid committed quality count")
                if count > observed:
                    add(_notice("PUBLICATION_QC_INCOMPLETE", 0,
                                "质量报告的汇总与检查明细不完整，必须重新生成报告。"))
        checks = report.get("checks", [])
        if not isinstance(checks, list):
            raise RevisionError("Invalid committed checks")
        for value in checks:
            # checked/done/key/confirmable from the report are NEVER authority.
            add_report_issue(_issue(value))

        edl = _optional_json(root, "edl.json")
        shots = _optional_json(root, "shots_annotated.json")
        disclosure = _optional_json(root, "generated_media_disclosure.json")
        evidence.update(edl=edl, shots=shots, disclosure=disclosure)
        if edl is not None:
            if not isinstance(edl, list):
                raise RevisionError("Invalid current EDL")
            for item in edl:
                if not isinstance(item, dict) or not isinstance(item.get("clips"), list):
                    raise RevisionError("Invalid current EDL row")
                sentence_id = item.get("sentence_id")
                if sentence_id not in row_ids:
                    continue
                for clip in item["clips"]:
                    if not isinstance(clip, dict) or type(clip.get("shot_id")) is not int:
                        raise RevisionError("Invalid current EDL clip")
                    selected[sentence_id].add(clip["shot_id"])
                    if clip.get("media_origin") == "generated" or is_generated_media_path(str(clip.get("src", ""))):
                        generated.add(sentence_id)
                        generated_by_row.setdefault(sentence_id, set()).add(clip["shot_id"])
        if shots is not None:
            if not isinstance(shots, list) or any(not isinstance(shot, dict) for shot in shots):
                raise RevisionError("Invalid current shot provenance")
            generated_shots = {shot["shot_id"] for shot in shots
                               if shot.get("media_origin") == "generated" or is_generated_media_path(str(shot.get("norm_path", "")))}
            generated.update(sentence_id for sentence_id, ids in selected.items() if ids & generated_shots)
            for sentence_id, ids in selected.items():
                generated_by_row.setdefault(sentence_id, set()).update(ids & generated_shots)
        disclosed: dict[int, set[int]] = {}
        if disclosure is not None:
            for item in GeneratedMediaDisclosureManifest.model_validate(disclosure).items:
                disclosed.setdefault(item.sentence_id, set()).add(item.shot_id)
                if item.sentence_id in selected and item.shot_id in selected[item.sentence_id]:
                    generated.add(item.sentence_id)
                    generated_by_row.setdefault(item.sentence_id, set()).add(item.shot_id)
        for sentence_id in sorted(generated):
            actual_ids = generated_by_row.get(sentence_id) or selected[sentence_id]
            if not actual_ids or not actual_ids.issubset(disclosed.get(sentence_id, set())):
                add(_notice("GENERATED_MEDIA_DISCLOSURE_MISSING", 0,
                            "AI 示意画面缺少当前版本的披露记录，知情勾选不能替代成片标注。", sentence_id, "去看"))
            add(_notice("generated_media", 0, "这句使用 AI 生成的示意画面；我知道它不是现场，不能作为事实证据。",
                        sentence_id, "去看"), confirmable=True)
        existing_facts = {item["sentence_id"] for item, _ in issues if item["code"] == "FACT_CHECK" and item["level"] in (0, 1)}
        for position, sentence_id in fact_positions.items():
            if sentence_id not in existing_facts:
                add(_notice("FACT_CHECK", 1,
                            f"第 {position} 句的数字、人名、日期和称谓需人工核对；勾选表示已问过当事人或查过可靠资料。",
                            sentence_id, "去看"))
        binding = _hash({"task_id": getattr(record, "task_id", ""), "revision": revision, "evidence": evidence})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        # Do not expose parser messages/paths/provider payloads. No half-valid
        # subset may turn a malformed authoritative report into a green gate.
        issues = [(_notice("PUBLICATION_REPORT_INVALID", 0,
                           "当前版本的发布检查资料缺失、损坏或不安全，不能发布。"), False)]
        binding = _hash({"task_id": getattr(record, "task_id", ""), "revision": revision, "invalid": True})

    if not _ready(record):
        issues.append((_notice("PUBLICATION_NOT_READY", 0, "当前任务尚未完成，不能确认或发布。"), False))
    result: dict[str, dict[str, Any]] = {}
    confirmable: set[str] = set()
    for item, can_confirm in issues:
        key = _hash({"revision": revision, "binding": binding, "issue": item, "confirmable": can_confirm})
        result[key] = {"key": key, "code": item["code"], "level": item["level"],
                       "message": _safe_message(item["message"], root), "sentence_id": item["sentence_id"],
                       "action": _safe_message(item["action"], root)[:80], "checked": False}
        if can_confirm:
            confirmable.add(key)
    return sorted(result.values(), key=lambda c: (c["level"], c["sentence_id"] if c["sentence_id"] is not None else -1,
                                                  c["code"], c["key"])), confirmable, binding


def _saved_keys(record: Any, revision: int, binding: str) -> set[str]:
    try:
        path = local_file(Path(record.task_dir), CHECKS_FILE, exists=False)
        if not path.exists() or path.stat().st_size > 256 * 1024:
            return set()
        saved = read_json(Path(record.task_dir), CHECKS_FILE)
        if (not isinstance(saved, dict) or set(saved) != {"schema_version", "revision", "report_sha256", "checked_keys"}
                or type(saved["schema_version"]) is not int or saved["schema_version"] != 1
                or type(saved["revision"]) is not int or saved["revision"] != revision
                or saved["report_sha256"] != binding
                or not isinstance(saved["checked_keys"], list) or len(saved["checked_keys"]) > MAX_CHECKS
                or any(not isinstance(key, str) or not _KEY.fullmatch(key) for key in saved["checked_keys"])):
            return set()
        return set(saved["checked_keys"])
    except (OSError, ValueError, TypeError, KeyError):
        return set()


def _gate(revision: int, checks: list[dict[str, Any]], confirmable: set[str], checked: set[str]) -> dict[str, Any]:
    for check in checks:
        check["checked"] = check["key"] in checked and check["key"] in confirmable
    blocking = sum(check["level"] == 0 and not check["checked"] for check in checks)
    pending = sum(check["level"] == 1 and not check["checked"] for check in checks)
    return {"revision": revision, "blocking_count": blocking, "pending_count": pending,
            "passed": blocking == 0 and pending == 0, "checks": checks}


def publication_gate(record: Any) -> dict[str, Any]:
    """Read the current committed checks; no writes, network or authorization."""
    revision = _revision(record)
    checks, confirmable, binding = _canonical(record, revision)
    return _gate(revision, checks, confirmable, _saved_keys(record, revision, binding))


def confirm_checks(record: Any, expected_revision: int, checked_keys: list[str]) -> dict[str, Any]:
    """Replace acknowledgements atomically; stale=409, unknown/error/info keys=422."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise HTTPException(422, "expected_revision must be a nonnegative integer")
    revision = _revision(record)
    if expected_revision != revision:
        raise HTTPException(409, {"code": "stale_revision", "current_revision": revision})
    if not _ready(record):
        raise HTTPException(409, "Task must have a completed current revision")
    if (not isinstance(checked_keys, list) or len(checked_keys) > MAX_CHECKS
            or any(not isinstance(key, str) or not _KEY.fullmatch(key) for key in checked_keys)
            or len(checked_keys) != len(set(checked_keys))):
        raise HTTPException(422, "checked_keys must contain distinct current check keys")
    checks, confirmable, binding = _canonical(record, revision)
    selected = set(checked_keys)
    if not selected.issubset(confirmable):
        raise HTTPException(422, "Unknown, stale or non-confirmable publication check")
    try:
        target = local_file(Path(record.task_dir), CHECKS_FILE, exists=False)
        write_json_atomic(target, {"schema_version": 1, "revision": revision,
                                   "report_sha256": binding, "checked_keys": sorted(selected)})
    except (OSError, RevisionError) as exc:
        raise HTTPException(409, "Publication acknowledgements could not be saved") from exc
    return _gate(revision, checks, confirmable, selected)


def require_publication(record: Any) -> None:
    """Host-side export gate. Preview is a separate, explicitly ungated concern."""
    gate = publication_gate(record)
    if not gate["passed"]:
        raise HTTPException(409, {"code": "publication_checks_required", **gate})