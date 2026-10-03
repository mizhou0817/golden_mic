"""Private task workbench. Mount explicitly; no application globals or dotenv reads."""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import Settings
from .matching import build_match_plan, extract_visual_beats, parse_script
from .media import run_logged_command
from .models import (
    AnnotatedShot, CaptionStyle, EDLItem, EditingPreferences, MatchCandidate, MatchPlanItem,
    Mode, QuoteTake, ReportResponse, ScriptDocument, SegmentManifestItem, Sentence, SentenceInput,
    SentenceTiming, Speaker, StageSnapshot, StageState, TaskState, UploadedAsset, VisualBeat,
    is_generated_media_path,
)
from .pipeline import (  # Existing helpers deliberately reused without changing pipeline exports.
    _build_finish_options,  # pyright: ignore[reportPrivateUsage]
    _run_blocking_until_complete,  # pyright: ignore[reportPrivateUsage]
    _segment_render_options,  # pyright: ignore[reportPrivateUsage]
)
from .providers.embedding import EmbeddingProvider
from .providers.llm import LLMProvider
from .providers.generative import filter_generated_media_disclosure
from .providers.tts import create_tts_provider
from .pronunciation import extract_number_expressions
from .production_modes import MODE_RULES, STAGE_WEIGHTS, validate_quote_trim
from .quality import enforce_quality_gate, generate_quality_report
from .remix import load_models
from .rendering import build_edl, render_from_existing_segments, render_replacement_segments
from .reporting import generate_report
from .revisions import (
    MAX_REVISIONS, MAX_SNAPSHOT_BYTES, STATE_FIELDS, RevisionError, artifact_files, copy_files, local_file,
    publish_artifacts, read_json, record_metadata, revision_dir, snapshot_revision,
    version_summaries,
)
from .storage import sanitize_sensitive_text, write_json_atomic
from .subtitles import generate_ass_subtitles
from .tts_pipeline import (
    probe_audio_duration, rebuild_narration_from_existing, synthesize_narration,
)
from .task_operations import legacy_task_busy, task_operation_busy

MAX_RECORDING_BYTES = 20 * 1024**2
MAX_RECORDINGS_BYTES = 200 * 1024**2
MAX_RECORDINGS = 100
MAX_OPERATION_SECONDS = 1800
RECORDING_ID = r"^[0-9a-f]{32}$"


class SentenceEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sentence_id: int = Field(ge=0)
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    shot_id: int | None = Field(default=None, ge=0)
    instruction: str | None = Field(default=None, min_length=1, max_length=500)
    recording_id: str | None = Field(default=None, pattern=RECORDING_ID)

    @field_validator("text", "instruction")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value or any(ord(c) < 32 for c in value):
                raise ValueError("Text must be nonempty and contain no control characters")
        return value

    @model_validator(mode="after")
    def exclusive_selection(self) -> SentenceEdit:
        if self.shot_id is not None and self.instruction is not None:
            raise ValueError("Use either shot_id or instruction")
        if all(getattr(self, key) is None for key in ("text", "shot_id", "instruction", "recording_id")):
            raise ValueError("Empty sentence edit")
        return self


class QuoteTrimEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    id: int = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered_range(self) -> QuoteTrimEdit:
        if self.end <= self.start:
            raise ValueError("原声结束时间必须晚于开始时间。")
        return self


class QuoteTakeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: int = Field(ge=0)
    take_id: str = Field(min_length=1, max_length=200)


class EditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    keep_sentence_ids: list[int] = Field(min_length=1, max_length=200)
    edits: list[SentenceEdit] = Field(default_factory=lambda: list[SentenceEdit](), max_length=200)
    pacing: Literal["slow", "normal", "fast"] | None = None
    caption_style: CaptionStyle | None = None
    enhance_speech: bool | None = None
    quote_trims: list[QuoteTrimEdit] = Field(default_factory=list, max_length=200)
    quote_takes: list[QuoteTakeEdit] = Field(default_factory=list, max_length=200)
    to_narration: list[int] = Field(default_factory=list, max_length=200)
    speakers: list[Speaker] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self) -> EditRequest:
        ids = self.keep_sentence_ids
        edit_ids = ([edit.sentence_id for edit in self.edits]
                    + [edit.id for edit in self.quote_trims]
                    + [edit.id for edit in self.quote_takes] + self.to_narration)
        if any(isinstance(i, bool) or i < 0 for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("Invalid or duplicate keep_sentence_ids")
        if (any(isinstance(i, bool) or i < 0 for i in edit_ids)
                or len(edit_ids) != len(set(edit_ids)) or not set(edit_ids).issubset(ids)):
            raise ValueError("Each retained sentence may have only one edit, trim, take or conversion")
        if self.speakers is not None and len({person.id for person in self.speakers}) != len(self.speakers):
            raise ValueError("Duplicate speaker identity")
        return self


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)


class BatchEditRequest(EditRequest):
    """Internal compiled v2 plan, not accepted by the legacy edit endpoint."""
    operation_id: str = Field(pattern=RECORDING_ID)
    steps: list[dict[str, Any]]
    replacements: dict[int, str]
    remix: bool


def _models(root: Path, name: str, model: Any) -> Any:
    local_file(root, name)
    return load_models(root / name, model)


def _write_models(root: Path, name: str, models: Any) -> None:
    write_json_atomic(root / name, [m.model_dump(mode="json", by_alias=True) for m in models])


def _progress(*_args: object) -> None:
    """Workbench status is job-level; avoid mutating the original stage history."""


def _plan_ids(item: MatchPlanItem) -> list[int]:
    if item.sync_sound:
        return [item.sync_sound.shot_id]
    return [b.shot_id for b in item.beat_matches] or [item.shot_id]


def _current(root: Path) -> tuple[list[SentenceTiming], list[MatchPlanItem], list[AnnotatedShot], list[EDLItem]]:
    timings = _models(root, "timings.json", SentenceTiming)
    all_plan = _models(root, "match_plan.json", MatchPlanItem)
    shots = _models(root, "shots_annotated.json", AnnotatedShot)
    edl = _models(root, "edl.json", EDLItem)
    ids = [t.sentence_id for t in timings]
    by_id = {p.sentence_id: p for p in all_plan}
    if not ids or len(ids) != len(set(ids)) or len(by_id) != len(all_plan):
        raise RevisionError("Invalid sentence identity")
    if ids != [e.sentence_id for e in edl] or not set(ids).issubset(by_id):
        raise RevisionError("Current timeline and match plan disagree")
    if ids != sorted(ids):
        raise RevisionError("Unsupported noncanonical sentence order")
    plan = [by_id[i] for i in ids]
    if len({s.shot_id for s in shots}) != len(shots):
        raise RevisionError("Duplicate shot identity")
    used = [c.shot_id for e in edl for c in e.clips]
    if len(used) != len(set(used)):
        raise RevisionError("Current timeline violates physical shot uniqueness")
    for timing in timings:
        local_file(root, timing.audio_path)
    for item in edl:
        for clip in item.clips:
            local_file(root, clip.src)
    return timings, plan, shots, edl


def _shot_ok(root: Path, shot: AnnotatedShot) -> bool:
    if shot.status != "available" or not shot.description or shot.quality is None:
        return False
    if shot.media_origin != "source" or is_generated_media_path(shot.norm_path):
        return False
    try:
        local_file(root, shot.norm_path)
    except RevisionError:
        return False
    return math.isfinite(shot.end) and 0 <= shot.start < shot.end and shot.duration > 0


def _recording(root: Path, recording_id: str, sentence_id: int) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(RECORDING_ID, recording_id):
        raise RevisionError("Invalid recording ID")
    metadata = read_json(root, f"recordings/{recording_id}.json")
    if metadata.get("sentence_id") != sentence_id:
        raise RevisionError("Recording belongs to another sentence")
    return local_file(root, f"recordings/{recording_id}.wav"), metadata


def _mode(record: Any) -> Mode:
    mode = getattr(record, "mode", "voiceover")
    if mode not in MODE_RULES["modes"]:
        raise RevisionError("Unknown production mode")
    return mode


def _uses_mode_pipeline(record: Any) -> bool:
    return bool(getattr(record, "mode_contract", False)) or _mode(record) != "voiceover"


def _quote_ids(record: Any, plan: list[MatchPlanItem]) -> set[int]:
    # A historical automatic sync selection is also source speech, unlike a
    # recording of narration (audio_kind=sync alone does not make it a quote).
    explicit = {SentenceInput.model_validate(s).idx for s in getattr(record, "sentences", [])
                if SentenceInput.model_validate(s).kind == "quote"}
    return {p.sentence_id for p in plan if _mode(record) == "original"
            or p.sentence_id in explicit or p.kind == "quote" or p.sync_sound is not None
            or (p.source is not None and not p.to_narration)}


def _owned_uploads(record: Any) -> list[UploadedAsset]:
    """Credential-free server upload identities; never read a client media path."""
    uploads = [UploadedAsset.model_validate(asset).model_copy(deep=True)
               for asset in getattr(record, "uploads", [])]
    declared = list(getattr(record, "upload_ids", []))
    if (any(not isinstance(identity, str) or not identity for identity in declared)
            or len(declared) != len(set(declared))
            or (declared and len(declared) != len(uploads))):
        raise RevisionError("Invalid server upload identities")
    identities: set[str] = set()
    for index, asset in enumerate(uploads):
        # Older records may only have the positional upload_ids list. Never
        # derive an upload capability or identity from a submitted filename.
        if asset.upload_id is None and declared:
            asset.upload_id = declared[index]
        if asset.upload_id is not None:
            if asset.upload_id in identities or (declared and asset.upload_id != declared[index]):
                raise RevisionError("Ambiguous server upload identity")
            identities.add(asset.upload_id)
    return uploads


def _owned_raw(record: Any, asset: UploadedAsset) -> str:
    if Path(asset.stored_name).name != asset.stored_name:
        raise RevisionError("Unsafe stored upload name")
    name = f"raw/{asset.stored_name}"
    path = local_file(record.task_dir, name)
    if path != asset.path.absolute() or asset.size <= 0 or path.stat().st_size != asset.size:
        raise RevisionError("Original upload is missing, moved or changed")
    return name


def _validate_take_source(record: Any, take: QuoteTake) -> None:
    asset = next((asset for asset in _owned_uploads(record) if asset.upload_id == take.upload_id), None)
    if asset is None:
        raise RevisionError("Quote take does not belong to this task's uploads")
    _owned_raw(record, asset)
    if asset.source_duration_seconds is not None and take.end > asset.source_duration_seconds:
        raise RevisionError("Quote take exceeds its original upload")
    if asset.trim_start is not None and take.start < asset.trim_start:
        raise RevisionError("Quote take precedes the retained upload range")
    if asset.trim_end is not None and take.end > asset.trim_end:
        raise RevisionError("Quote take exceeds the retained upload range")


def _selected_quote_operations(record: Any, payload: EditRequest, plan: list[MatchPlanItem]) -> None:
    quotes = _quote_ids(record, plan)
    if any(edit.sentence_id in quotes for edit in payload.edits):
        raise RevisionError("原声句不能改字、录音、换画面或使用换镜指令；请剪短、换原声段或显式改成旁白。")
    if _mode(record) == "original" and (payload.pacing is not None or payload.to_narration):
        raise RevisionError("只用原声模式不能改成旁白或改变语速。")
    operations = {edit.id for edit in [*payload.quote_trims, *payload.quote_takes]} | set(payload.to_narration)
    if not operations.issubset(quotes):
        raise RevisionError("Quote operations require a retained original-speech sentence")
    if operations and not _uses_mode_pipeline(record):
        raise RevisionError("Legacy sync speech cannot use the mode editor; create an explicit mode task")
    if payload.to_narration and _mode(record) != "mixed":
        raise RevisionError("Only mixed mode permits explicit quote-to-narration conversion")
    by_id = {item.sentence_id: item for item in plan}
    for edit in payload.quote_trims:
        item = by_id[edit.id]
        source = item.source
        if source is None:
            raise RevisionError("Quote has no verified source to trim")
        _validate_take_source(record, source)
        try:
            if item.trim is not None:
                if set(item.trim) != {"start", "end"}:
                    raise RevisionError("Invalid committed quote trim")
                source = QuoteTake.model_validate(validate_quote_trim(source, item.trim["start"], item.trim["end"]))
            validate_quote_trim(source, edit.start, edit.end)
        except ValueError as exc:
            raise RevisionError(str(exc)) from exc
        if (edit.start, edit.end) == (source.start, source.end):
            raise RevisionError("Quote trim does not shorten the current interval")
    for edit in payload.quote_takes:
        item = by_id[edit.id]
        alternatives = [take for take in item.alt_takes if take.take_id == edit.take_id]
        if len(alternatives) != 1 or (item.source is not None and item.source.take_id == edit.take_id):
            raise RevisionError("Select a distinct server-owned alternative take for this sentence")
        source = alternatives[0]
        _validate_take_source(record, source)
        try:
            validate_quote_trim(source, source.start, source.end)
        except ValueError as exc:
            raise RevisionError(str(exc)) from exc
    if payload.speakers is not None:
        if not _uses_mode_pipeline(record):
            raise RevisionError("Speaker edits require an explicit production-mode task")
        known = {Speaker.model_validate(s).id for s in getattr(record, "speakers", [])}
        known.update(take.speaker_id for item in plan for take in
                     ([item.source] if item.source is not None else []) + item.alt_takes if take.speaker_id)
        if any(speaker.id not in known for speaker in payload.speakers):
            raise RevisionError("Speaker edits cannot invent source speaker identities")
    if not _uses_mode_pipeline(record) and quotes and payload.pacing is not None:
        raise RevisionError("Legacy source speech cannot be re-timed through narration pacing")


def _validate_edit(record: Any, payload: EditRequest, _settings: Settings) -> None:
    timings, plan, shots, edl = _current(record.task_dir)
    ids = [t.sentence_id for t in timings]
    if not set(payload.keep_sentence_ids).issubset(ids):
        raise RevisionError("Unknown sentence; restore a version to recover deleted sentences")
    if payload.keep_sentence_ids != [i for i in ids if i in payload.keep_sentence_ids]:
        raise RevisionError("Keep sentence IDs in their current timeline order")
    _selected_quote_operations(record, payload, plan)
    shots_by_id = {s.shot_id: s for s in shots}
    # A direct choice cannot steal a shot used by another current sentence,
    # including one pending deletion in this batch. Restore/submit deletion first.
    used_by = {c.shot_id: e.sentence_id for e in edl for c in e.clips}
    selected: set[int] = set()
    for edit in payload.edits:
        if edit.shot_id is not None:
            shot = shots_by_id.get(edit.shot_id)
            if shot is None or not _shot_ok(record.task_dir, shot):
                raise RevisionError("Selected shot is not valid source footage")
            if used_by.get(edit.shot_id, edit.sentence_id) != edit.sentence_id or edit.shot_id in selected:
                raise RevisionError("Selected shot is already used by another sentence")
            selected.add(edit.shot_id)
        if edit.recording_id:
            _recording(record.task_dir, edit.recording_id, edit.sentence_id)
    text = {p.sentence_id: p.text for p in plan}
    text.update({e.sentence_id: e.text for e in payload.edits if e.text is not None})
    if sum(len(text[i]) for i in payload.keep_sentence_ids) > 8000:
        raise RevisionError("Edited script exceeds configured length limit")
    speaker_changed = payload.speakers is not None and (
        [s.model_dump(mode="json") for s in payload.speakers]
        != [Speaker.model_validate(s).model_dump(mode="json") for s in getattr(record, "speakers", [])]
    )
    if (not payload.edits and not payload.quote_trims and not payload.quote_takes
            and not payload.to_narration and not speaker_changed
            and set(payload.keep_sentence_ids) == set(ids) and all(
        getattr(payload, key) is None or getattr(payload, key) == getattr(record.preferences, key)
        for key in ("pacing", "caption_style", "enhance_speech")
    )):
        raise RevisionError("No changes requested")


def _safe_text(text: str, root: Path) -> str:
    text = sanitize_sensitive_text(text).replace(str(root), "[task]").replace(root.as_posix(), "[task]")
    text = re.sub(r"[A-Za-z]:[\\/][^\s\"<>]+", "[local path]", text)
    text = re.sub(r"(?:https?://|/)[^\s]*\?[^\s]+", "[private URL]", text)
    return text


def _safe_report(root: Path, task_id: str, audio: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    report = ReportResponse.model_validate(read_json(root, "report.json")).model_dump(mode="json")
    if state is not None:
        report["mode"] = state.get("mode", report["mode"])
        report["speakers"] = [Speaker.model_validate(s).model_dump(mode="json")
                              for s in state.get("speakers", report["speakers"])]
    timings = {t["sentence_id"]: t for t in read_json(root, "timings.json")} if (root / "timings.json").is_file() else {}
    origins = {s["shot_id"]: s.get("media_origin", "source") for s in read_json(root, "shots_annotated.json")} if (root / "shots_annotated.json").is_file() else {}
    for row in report["rows"]:
        row["thumb_url"] = f"/api/tasks/{task_id}/thumbs/{row['shot_id']}.jpg" if row["shot_id"] is not None else None
        row["audio_source"] = audio.get(str(row["sentence_id"]), {}).get("audio_source", row["audio_kind"])
        if row["sentence_id"] in timings:
            row["start"] = timings[row["sentence_id"]]["start"]
            row["end"] = timings[row["sentence_id"]]["end"]
        if row["shot_id"] in origins:
            row["media_origin"] = origins[row["shot_id"]]
        for beat in row["visual_beats"]:
            beat["thumb_url"] = f"/api/tasks/{task_id}/thumbs/{beat['shot_id']}.jpg"
            if beat["shot_id"] in origins:
                beat["media_origin"] = origins[beat["shot_id"]]
    quality = report.get("quality")
    if quality:
        # Arbitrary diagnostic metrics may contain internal paths/provider payloads.
        rate_target = quality["metrics"].get("narration_rate_target")
        quality["metrics"] = {k: v for k, v in quality["metrics"].items() if isinstance(v, (int, float, bool))}
        quality["metrics"].pop("narration_rate_target", None)
        # Only this version's persisted QC contract; never recover missing
        # historical bounds from live settings/preferences or measured CPM.
        rate_keys = ("target_cpm", "min_cpm", "max_cpm")
        if (isinstance(rate_target, dict)
            and all(type(rate_target.get(key)) in (int, float)
                and math.isfinite(rate_target[key]) and rate_target[key] > 0 for key in rate_keys)
            and rate_target["min_cpm"] <= rate_target["target_cpm"] <= rate_target["max_cpm"]):
            quality["metrics"]["narration_rate_target"] = {key: rate_target[key] for key in rate_keys}
        for issue in quality["issues"]:
            issue["message"] = _safe_text(issue["message"], root)
            issue["action"] = _safe_text(issue.get("action", ""), root)
        # The report's checks may contain arbitrary legacy dictionaries; expose only
        # the known editorial fields, never a provider payload or stored authority.
        report["checks"] = [{key: (_safe_text(value, root) if isinstance(value, str) else value)
                  for key, value in check.items()
                  if key in {"code", "severity", "message", "sentence_id", "level", "action"}
                  and (value is None or isinstance(value, (str, int, float, bool)))}
                 for check in report["checks"]]
    report["metrics"] = {key: value for key, value in report["metrics"].items()
                         if isinstance(value, (int, float, bool))}
    if (root / "v2_apply_plan.json").is_file():
        receipt = read_json(root, "v2_apply_plan.json")
        report["replace_failures"] = {key: value for key, value in receipt.get("replace_failures", {}).items()
            if re.fullmatch(r"0|[1-9][0-9]{0,8}", key) and value in {"none", "used", "abstract", "processing_failed"}}
    return report


def _audio_metadata(root: Path) -> dict[str, Any]:
    audio: dict[str, Any] = {}
    if (root / "student_narration.json").is_file():
        student = read_json(root, "student_narration.json")
        for unit in student.get("units", []):
            audio[str(unit["sentence_id"])] = {"audio_source": "recording", "transcript_verified": True}
    if (root / "workbench_audio.json").exists():
        audio.update(read_json(root, "workbench_audio.json"))
    # Rendering receipts are derived from the current profile, never inherited
    # as upload/ASR provenance when an effect is later disabled or restored.
    for entry in audio.values():
        entry.pop("speech_enhancement_applied", None)
    if (root / "timings.json").is_file():
        # The student manifest describes the original recording, not every later
        # revision. Also repair reads of revisions published before TTS overrides
        # were persisted; never attach recording verification/IDs to TTS audio.
        timings = read_json(root, "timings.json")
        for timing in timings:
            key = str(timing["sentence_id"])
            if timing.get("audio_kind") == "tts" and key in audio:
                audio[key] = {"audio_source": "tts"}
        # This is a receipt of local deterministic processing, not ASR
        # verification or an "AI repaired" claim. Current TTS always wins over
        # historical recording/profile metadata.
        if (root / "narration_profile.json").is_file():
            profile = read_json(root, "narration_profile.json")
            effect = profile.get("speech_enhancement", {})
            processed_ids = {unit["sentence_id"] for unit in effect.get("units", [])}
            if effect.get("schema_version") == 1:
                for timing in timings:
                    key = str(timing["sentence_id"])
                    entry = audio.setdefault(key, {"audio_source": timing.get("audio_kind", "tts")})
                    entry["speech_enhancement_applied"] = (
                        effect.get("applied") is True
                        and timing.get("audio_kind") == "sync"
                        and timing["sentence_id"] in processed_ids
                    )
    return audio


def _apply_state(record: Any, state: dict[str, Any]) -> None:
    # Restore only the revision allowlist, never attributes injected into JSON
    # (task_dir, uploads, capabilities, background owners or manager hooks).
    typed = {"mode", "mode_contract", "upload_ids", "speakers", "sentences", "quality_gate_mode"}
    for key in STATE_FIELDS:
        if key in typed or key not in state:
            continue
        value = state[key]
        if key in {"processing_started_at", "processing_completed_at"} and value is not None:
            value = datetime.fromisoformat(value)
        setattr(record, key, value)
    record.preferences = EditingPreferences.model_validate(state["preferences"])
    record.stages = [StageSnapshot.model_validate(s) for s in state["stages"]]
    mode = state.get("mode", "voiceover")
    contract = state.get("mode_contract", False)
    gate_mode = state.get("quality_gate_mode")
    identities = state.get("upload_ids", [])
    if (mode not in MODE_RULES["modes"] or type(contract) is not bool
            or gate_mode not in {None, "warn", "block"}
            or not isinstance(identities, list)
            or any(not isinstance(value, str) or not value for value in identities)
            or len(identities) != len(set(identities))):
        raise RevisionError("Invalid revision production-mode state")
    record.mode, record.mode_contract, record.quality_gate_mode = mode, contract, gate_mode
    record.upload_ids = list(identities)
    record.speakers = [Speaker.model_validate(s) for s in state.get("speakers", [])]
    record.sentences = [SentenceInput.model_validate(s) for s in state.get("sentences", [])]


async def _prepare_workspace(record: Any, workspace: Path, source: Path, payload: EditRequest | None = None) -> None:
    names = artifact_files(source)
    # Video/thumbnail inputs are task-owned and read-only to the edit pipeline.
    shots = _models(source, "shots_annotated.json", AnnotatedShot)
    references: set[str] = set()
    for shot in shots:
        if shot.status == "available":
            references.add(shot.norm_path)
            thumb = f"thumbs/shot_{shot.shot_id}.jpg"
            if (record.task_dir / thumb).exists():
                references.add(thumb)
    if _uses_mode_pipeline(record):
        plan = _models(source, "match_plan.json", MatchPlanItem)
        uploads = _owned_uploads(record)
        manifest = read_json(source, "production_mode.json") if "production_mode.json" in names else {}
        clocks = manifest.get("source_clocks", {})
        if not isinstance(clocks, dict):
            raise RevisionError("Invalid committed source clock inventory")
        upload_by_id = {asset.upload_id: (index, asset) for index, asset in enumerate(uploads)}
        for identity, clock in clocks.items():
            if identity not in upload_by_id or not isinstance(clock, dict):
                raise RevisionError("Source clock does not belong to this task's upload inventory")
            index, asset = upload_by_id[identity]
            raw = _owned_raw(record, asset)
            normalized = f"norm/norm_{index}.mp4"
            if (type(clock.get("source_index")) is not int or clock["source_index"] != index
                    or clock.get("raw_path") != raw or clock.get("norm_path") != normalized):
                raise RevisionError("Source clock paths disagree with the server upload inventory")
            references.update({raw, normalized})
            if asset.prepared_stored_name is not None:
                references.add(f"raw/{asset.prepared_stored_name}")
        needed = {take.upload_id for item in plan for take in
                  ([item.source] if item.source is not None else []) + item.alt_takes}
        for item in plan:
            if item.source is not None:
                _validate_take_source(record, item.source)
            for take in item.alt_takes:
                _validate_take_source(record, take)
            if item.sync_sound is not None:
                index = item.sync_sound.source_index
                if index >= len(uploads):
                    raise RevisionError("Quote source index is outside server upload inventory")
                name = _owned_raw(record, uploads[index])
                if item.sync_sound.source_media_path != name:
                    raise RevisionError("Quote source path disagrees with server upload inventory")
                references.add(name)
        for index, asset in enumerate(uploads):
            if asset.upload_id not in needed:
                continue
            references.add(_owned_raw(record, asset))
            # Stage 2's actual full-file normalization convention. Also copy
            # unselected originals needed when an alternate take changes source.
            normalized = f"norm/norm_{index}.mp4"
            if (record.task_dir / normalized).exists():
                references.add(normalized)
            if asset.prepared_stored_name is not None:
                references.add(f"raw/{asset.prepared_stored_name}")
        if needed and not needed.issubset({asset.upload_id for asset in uploads}):
            raise RevisionError("Quote source is not a task-owned upload")
        if "pretranscripts.json" not in names and (record.task_dir / "pretranscripts.json").exists():
            references.add("pretranscripts.json")
        for sentence_id, entry in manifest.get("audio_cache", {}).items():
            if payload is not None and int(sentence_id) not in payload.keep_sentence_ids:
                continue
            recipe = entry.get("raw_recipe")
            if recipe is not None:
                relative = recipe.get("source_audio")
                if not isinstance(relative, str) or not (
                    relative == "own_voice.wav" or re.fullmatch(r"recordings/[0-9a-f]{32}\.wav", relative)
                ):
                    raise RevisionError("Mode recording recipe has an unexpected original audio path")
                references.add(relative)
        # Immutable initial-run audit inputs required by the mode pipeline's
        # validation. They are working inputs, not revision-authority/state.
        for name in ("pipeline_manifest.json", "asr_transcripts.json", "shots.json"):
            if (record.task_dir / name).exists():
                references.add(name)
        # New narration recordings are not part of the old revision yet.
        for edit in payload.edits if payload is not None else []:
            if edit.recording_id:
                _recording(record.task_dir, edit.recording_id, edit.sentence_id)
                references.update({f"recordings/{edit.recording_id}.wav", f"recordings/{edit.recording_id}.json"})
    # Bound the combined workspace, not two independently valid 8-GiB copies.
    references.difference_update(names)
    if (sum(local_file(source, name).stat().st_size for name in names)
            + sum(local_file(record.task_dir, name).stat().st_size for name in references) > MAX_SNAPSHOT_BYTES):
        raise RevisionError("Workspace exceeds 8 GiB limit")
    await _run_blocking_until_complete(partial(copy_files, source, workspace, names))
    await _run_blocking_until_complete(partial(copy_files, record.task_dir, workspace, sorted(references)))


def _first_edit_stage(payload: EditRequest, state: dict[str, Any]) -> int:
    ids = [SentenceInput.model_validate(s).idx for s in state.get("sentences", [])]
    speakers_only = (payload.speakers is not None and not payload.edits and not payload.quote_trims
                     and not payload.quote_takes and not payload.to_narration
                     and payload.keep_sentence_ids == ids
                     and all(getattr(payload, key) is None for key in ("pacing", "caption_style", "enhance_speech")))
    return 8 if speakers_only else 7


def _reset_mode_progress(record: Any, first_stage: int) -> None:
    mode = _mode(record)
    old = {stage.number: stage for stage in record.stages}
    stages: list[StageSnapshot] = []
    for index, definition in enumerate(MODE_RULES["modes"][mode]["stages"]):
        number = index + 1
        stage = (old[number].model_copy(deep=True) if number in old else
                 StageSnapshot(number=number, name=definition["name"]))
        stage.name, stage.weight = definition["name"], STAGE_WEIGHTS[mode][index]
        if number >= first_stage:
            stage.status, stage.fraction, stage.message = StageState.pending, 0.0, ""
            stage.started_at = stage.completed_at = stage.elapsed_seconds = None
        else:
            # Reused committed output is not new work and gets no new clock.
            stage.status, stage.fraction = StageState.done, 1.0
            stage.message = "沿用已提交版本"
        stages.append(stage)
    record.stages = stages
    record.current_stage = record.stage_name = None
    record.progress = int(sum(stage.weight * stage.fraction for stage in stages) * 100)


class _WorkbenchReporter:
    """work.reporter implements PipelineReporter, scoped to this owned edit.

    mode_pipeline must call start_stage/update_stage/complete_stage(work, ...)
    at REAL operations (7..10, or 8..10 for speakers-only). It may use existing
    pipeline helpers with this reporter. No timers, simulated ticks or implicit
    stage completions. Only progress, not uncommitted editorial state, is mirrored
    to TaskRecord while the job runs. work.progress_callback() can mirror a mode
    pipeline that already maintains its own ten StageSnapshots instead.
    """

    def __init__(self, record: Any, work: Any, manager: Any, first_stage: int) -> None:
        self.record, self.work, self.manager, self.first_stage = record, work, manager, first_stage
        self.started: dict[int, float] = {}

    def publish(self) -> None:
        if (self.manager.get(self.record.task_id) is not self.record
                or self.record.background is not asyncio.current_task()):
            raise RevisionError("Workbench no longer owns its task")
        if len(self.work.stages) != 10 or [stage.number for stage in self.work.stages] != list(range(1, 11)):
            raise RevisionError("Mode reporter requires ten ordered stages")
        self.work.progress = min(99, int(sum(stage.weight * stage.fraction for stage in self.work.stages) * 100))
        for name in ("current_stage", "stage_name", "progress", "message", "processing_started_at"):
            setattr(self.record, name, getattr(self.work, name))
        self.record.stages = [stage.model_copy(deep=True) for stage in self.work.stages]
        self.record.updated_at = datetime.now(timezone.utc)
        self.manager._persist_record(self.record)

    def _stage(self, work: Any, number: int) -> StageSnapshot:
        if work is not self.work or type(number) is not int or not self.first_stage <= number <= 10:
            raise RevisionError("Unexpected workbench stage event")
        return self.work.stages[number - 1]

    def start_stage(self, work: Any, stage_number: int, message: str) -> None:
        stage = self._stage(work, stage_number)
        if stage.status != StageState.pending or any(
                s.status != StageState.done for s in work.stages[self.first_stage - 1:stage_number - 1]):
            raise RevisionError("Workbench stages must start in operation order")
        stage.status, stage.fraction = StageState.running, 0.0
        stage.started_at = datetime.now(timezone.utc)
        stage.completed_at = stage.elapsed_seconds = None
        self.started[stage_number] = time.monotonic()
        work.current_stage, work.stage_name = stage_number, stage.name
        work.message = stage.message = _safe_text(message, work.task_dir)
        self.publish()

    def update_stage(self, work: Any, stage_number: int, fraction: float, message: str) -> None:
        stage = self._stage(work, stage_number)
        if stage.status != StageState.running or not math.isfinite(fraction):
            raise RevisionError("Workbench progress requires a running stage and finite fraction")
        stage.fraction = max(stage.fraction, min(1.0, max(0.0, fraction)))
        work.message = stage.message = _safe_text(message, work.task_dir)
        self.publish()

    def complete_stage(self, work: Any, stage_number: int, message: str) -> None:
        stage = self._stage(work, stage_number)
        if stage.status != StageState.running or stage_number not in self.started:
            raise RevisionError("Workbench cannot complete an unstarted stage")
        stage.status, stage.fraction = StageState.done, 1.0
        stage.completed_at = datetime.now(timezone.utc)
        stage.elapsed_seconds = max(0.0, time.monotonic() - self.started.pop(stage_number))
        work.message = stage.message = _safe_text(message, work.task_dir)
        self.publish()


async def _rematch(work: Any, sentences: list[Sentence], shots: list[AnnotatedShot], excluded: set[int], settings: Settings) -> list[MatchPlanItem]:
    available = [s for s in shots if _shot_ok(work.task_dir, s) and s.shot_id not in excluded]
    if sum(len(s.visual_beats or extract_visual_beats(s.text)) for s in sentences) > len(available):
        raise RevisionError("Not enough unused source shots for changed sentences")
    async with EmbeddingProvider.from_settings(settings, task_dir=work.task_dir) as embedding:
        async with LLMProvider.from_settings(settings) as llm:
            return await build_match_plan(
                work.task_dir, sentences, available, embedding, llm, _progress,
                video_embedding_enabled=False, excluded_shot_ids=excluded,
                top_k=settings.retrieval_top_k, lexical_rescue_k=settings.retrieval_lexical_rescue_k,
                video_embedding_candidate_top_k=settings.video_embedding_candidate_top_k,
                editing_brief=work.preferences.custom_instructions,
                output_path=work.task_dir / "selection.json",
            )


async def _tempo(root: Path, timing: SentenceTiming, factor: float) -> SentenceTiming:
    source = local_file(root, timing.audio_path)
    relative = f"workbench_audio/{uuid.uuid4().hex}.wav"
    output = local_file(root, relative, exists=False)
    output.parent.mkdir(exist_ok=True)
    await run_logged_command([
        "ffmpeg", "-y", "-i", str(source), "-map", "0:a:0", "-vn",
        "-af", f"atempo={factor:.9f}", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output),
    ], root, "Workbench pacing")
    duration = await probe_audio_duration(output, root)
    words = [w.model_copy(update={"start": w.start / factor, "end": min(duration, w.end / factor)})
             for w in timing.words if w.start / factor < min(duration, w.end / factor)]
    return timing.model_copy(update={
        "audio_path": relative, "duration": duration, "words": words,
        "tempo_adjustment": timing.tempo_adjustment * factor,
        "speaking_rate_cpm": timing.speaking_rate_cpm * factor if timing.speaking_rate_cpm else None,
    })


async def _edit_workspace(work: Any, original: Any, payload: EditRequest, settings: Settings) -> None:
    if _uses_mode_pipeline(work):
        # Deliberately lazy: legacy tasks do not import or accidentally invoke
        # the new mode pipeline. Its reporter must publish REAL stage events.
        from .mode_pipeline import edit_mode_workspace

        await edit_mode_workspace(work, original, payload, settings)
        return
    root = work.task_dir
    timings, plan, shots, old_edl = _current(root)
    old_timings = {t.sentence_id: t for t in timings}
    old_plan = {p.sentence_id: p for p in plan}
    keep = set(payload.keep_sentence_ids)
    edits = {e.sentence_id: e for e in payload.edits}
    plan = [p.model_copy(deep=True) for p in plan if p.sentence_id in keep]
    text_changed = {e.sentence_id for e in payload.edits if e.text is not None and e.text != old_plan[e.sentence_id].text}
    rerank_ids = {e.sentence_id for e in payload.edits if e.instruction is not None or (e.sentence_id in text_changed and e.shot_id is None)}
    visual_changed = rerank_ids | {e.sentence_id for e in payload.edits if e.shot_id is not None}
    for item in plan:
        edit = edits.get(item.sentence_id)
        if edit and edit.text is not None:
            item.text = edit.text
        if edit and edit.shot_id is not None:
            # Manual selection is not semantic verification. Never retain old confidence.
            replacement = MatchPlanItem(
                sentence_id=item.sentence_id, text=item.text, shot_id=edit.shot_id,
                confidence=0.0, is_fallback=True,
                candidates=[MatchCandidate(shot_id=edit.shot_id, similarity=0.0)],
                replacement_instruction="Manual shot selection; semantic match not verified",
            )
            plan[plan.index(item)] = replacement
    excluded = {sid for p in plan if p.sentence_id not in rerank_ids for sid in _plan_ids(p)}
    # Also exclude physical EDL selections for every unchanged current sentence.
    excluded.update(c.shot_id for e in old_edl if e.sentence_id not in rerank_ids for c in e.clips)
    queries: list[Sentence] = []
    for item in plan:
        if item.sentence_id not in rerank_ids:
            continue
        edit = edits[item.sentence_id]
        if edit.instruction:
            excluded.update(_plan_ids(old_plan[item.sentence_id]))
        query = f"{item.text} {edit.instruction}" if edit.instruction else item.text
        queries.append(Sentence(sentence_id=item.sentence_id, text=item.text,
                                visual_beats=extract_visual_beats(query)))
    if queries:
        replacements = {p.sentence_id: p for p in await _rematch(work, queries, shots, excluded, settings)}
        if set(replacements) != rerank_ids:
            raise RevisionError("Matching provider omitted changed sentences")
        plan = [replacements.get(p.sentence_id, p) for p in plan]
        for p in plan:
            if p.sentence_id in rerank_ids:
                p.text = edits[p.sentence_id].text or old_plan[p.sentence_id].text
                p.sync_sound = None
                p.replacement_instruction = edits[p.sentence_id].instruction
    used = [sid for p in plan for sid in _plan_ids(p)]
    if len(used) != len(set(used)):
        raise RevisionError("Edit would reuse a physical shot")
    title = parse_script(original.script).title
    sentences = [Sentence(sentence_id=p.sentence_id, text=p.text, paragraph_index=i,
                          visual_beats=[VisualBeat(beat_id=b.beat_id, text=b.text) for b in p.beat_matches])
                 for i, p in enumerate(plan)]
    work.script = original.script
    if text_changed or keep != set(old_timings):
        work.script = ((title + "\n") if title else "") + "\n".join(p.text for p in plan)
    write_json_atomic(root / "script_structure.json", ScriptDocument(title=title, sentences=sentences).model_dump(mode="json"))
    _write_models(root, "sentences.json", sentences)
    (root / "script_segmented.txt").write_text(work.script, encoding="utf-8")
    recording_ids = {e.sentence_id: e.recording_id for e in payload.edits if e.recording_id}
    audio_meta = {k: v for k, v in _audio_metadata(root).items() if int(k) in keep}
    for entry in audio_meta.values():
        entry.pop("speech_enhancement_applied", None)
    new_tts_ids = text_changed - recording_ids.keys()
    selected_timings = {i: old_timings[i].model_copy(deep=True) for i in keep}
    if new_tts_ids:
        # Synthesis in its own directory cannot overwrite unchanged unit audio/profile.
        synthesis_dir = root / "synthesis"
        synthesis_dir.mkdir()
        selected_sentences = [s for s in sentences if s.sentence_id in new_tts_ids]
        pronunciations = []
        if any(extract_number_expressions(s.text) for s in selected_sentences):
            async with LLMProvider.from_settings(settings) as llm:
                pronunciations = await llm.plan_pronunciations(work.script, selected_sentences)
        async with create_tts_provider(settings, target_chars_per_minute=work.preferences.target_chars_per_minute) as provider:
            synthesized = await synthesize_narration(
                synthesis_dir, selected_sentences, provider, _progress,
                full_script=work.script, pronunciation_plan=pronunciations,
                target_chars_per_minute=work.preferences.target_chars_per_minute,
                rate_tolerance=settings.tts_news_rate_tolerance,
                target_lufs=settings.tts_target_lufs, target_lra=settings.tts_target_lra,
                true_peak_dbfs=settings.tts_true_peak_dbfs,
            )
        for timing in synthesized:
            relative = f"workbench_audio/{uuid.uuid4().hex}{Path(timing.audio_path).suffix}"
            target = local_file(root, relative, exists=False)
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(local_file(synthesis_dir, timing.audio_path), target)
            selected_timings[timing.sentence_id] = timing.model_copy(update={"audio_path": relative})
            # Absence would fall back to the original student recording manifest.
            audio_meta[str(timing.sentence_id)] = {"audio_source": "tts"}
    for sentence_id, recording_id in recording_ids.items():
        source, metadata = _recording(original.task_dir, recording_id, sentence_id)
        relative = f"recordings/{recording_id}.wav"
        target = local_file(root, relative, exists=False)
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(source, target)
        duration = await probe_audio_duration(target, root)
        if not 0 < duration <= 120:
            raise RevisionError("Recording duration is invalid")
        text = next(p.text for p in plan if p.sentence_id == sentence_id)
        selected_timings[sentence_id] = SentenceTiming(
            sentence_id=sentence_id, text=text, audio_path=relative, duration=duration,
            start=0, end=duration, audio_kind="sync", gap_after=0.12,
        )
        audio_meta[str(sentence_id)] = {"audio_source": "recording", "recording_id": recording_id,
                                        "transcript_verified": False, "uploaded_revision": metadata["revision"]}
    pace_changed = work.preferences.pacing != original.preferences.pacing
    if pace_changed:
        factor = work.preferences.target_chars_per_minute / original.preferences.target_chars_per_minute
        for sentence_id in keep - new_tts_ids:
            selected_timings[sentence_id] = await _tempo(root, selected_timings[sentence_id], factor)
    for item in plan:
        if item.sentence_id in text_changed or item.sentence_id in recording_ids or pace_changed:
            # Recorded/re-timed audio is no longer the original source sync interval.
            if item.sync_sound is not None:
                item.shot_id = item.sync_sound.shot_id
                item.beat_matches = []
            item.sync_sound = None
    audio_changed = bool(text_changed or recording_ids or pace_changed or keep != set(old_timings))
    speech_filter_changed = work.preferences.enhance_speech != original.preferences.enhance_speech
    timings = [selected_timings[p.sentence_id] for p in plan]
    if audio_changed:
        for i, timing in enumerate(timings):
            timing.gap_after = 0.12 if i + 1 < len(timings) else 0.0
            timing.tts_group_id = None
    if audio_changed or speech_filter_changed:
        timings = await rebuild_narration_from_existing(
            root, timings, [t.sentence_id for t in timings], timings_path=root / "timings.json",
            narration_path=root / "narration.m4a", narration_profile_path=root / "narration_profile.json",
            enhance_speech=work.preferences.enhance_speech,
        )
        profile = read_json(root, "narration_profile.json")
        profile["target_chars_per_minute"] = work.preferences.target_chars_per_minute
        profile["audio_sources"] = audio_meta
        write_json_atomic(root / "narration_profile.json", profile)
    if audio_changed:
        generate_ass_subtitles(root, timings, title=title)
    _write_models(root, "match_plan.json", plan)
    write_json_atomic(root / "workbench_audio.json", audio_meta)
    # Construct each changed timeline item independently; unchanged clips/in-points
    # remain byte-identical, even when preceding deletions shift its start time.
    edl_by_id = {e.sentence_id: e for e in old_edl}
    manifest = {m.sentence_id: m for m in _models(root, "segment_manifest.json", SegmentManifestItem)}
    edl: list[EDLItem] = []
    new_manifest: list[SegmentManifestItem] = []
    segment_paths: list[Path] = []
    for timing, item in zip(timings, plan, strict=True):
        old = old_timings[timing.sentence_id]
        unchanged = timing.sentence_id not in visual_changed and abs((timing.duration + timing.gap_after) - (old.duration + old.gap_after)) < 1e-6
        if unchanged:
            entry = edl_by_id[timing.sentence_id].model_copy(update={"timeline_start": timing.start, "timeline_end": timing.end + timing.gap_after})
            segments = [local_file(root, p) for p in manifest[timing.sentence_id].segments]
        else:
            entry = build_edl(root, [timing], [item], shots)[0].model_copy(update={"timeline_start": timing.start, "timeline_end": timing.end + timing.gap_after})
            segments = await render_replacement_segments(
                root, entry.clips, revision=work.revision, sentence_id=timing.sentence_id,
                progress=_progress, segment_options=_segment_render_options(work, settings),
            )
        edl.append(entry)
        segment_paths.extend(segments)
        new_manifest.append(SegmentManifestItem(sentence_id=timing.sentence_id, segments=[p.relative_to(root).as_posix() for p in segments]))
    used = [c.shot_id for e in edl for c in e.clips]
    if len(used) != len(set(used)):
        raise RevisionError("Rendered timeline would reuse a physical shot")
    _write_models(root, "edl.json", edl)
    _write_models(root, "segment_manifest.json", new_manifest)
    filter_generated_media_disclosure(root, edl)
    await render_from_existing_segments(
        root, segment_paths, _progress, narration_path=root / "narration.m4a",
        subtitles_path=root / "subs.ass", subtitle_manifest_path=root / "subtitle_manifest.json",
        video_only_path=root / "video_only.mp4", final_path=root / "final.mp4",
        target_loudness_lufs=settings.tts_target_lufs, target_loudness_range=settings.tts_target_lra,
        maximum_true_peak_dbfs=settings.tts_true_peak_dbfs,
        finish_options=await _build_finish_options(work, settings, timings, edl=edl),
    )
    quality = await _run_blocking_until_complete(partial(
        generate_quality_report, root, shots, plan, timings,
        minimum_confidence=settings.quality_min_match_confidence,
        target_chars_per_minute=work.preferences.target_chars_per_minute,
        rate_tolerance=settings.tts_news_rate_tolerance,
        maximum_loudness_spread_lu=settings.tts_max_loudness_spread_lu,
    ))
    enforce_quality_gate(quality, settings.quality_gate_mode)
    generate_report(root, work.task_id, shots, plan, timings, quality_report=quality)
    # Source baselines must agree with this successful edit for subsequent legacy
    # deletion APIs; earlier baselines remain available in immutable revisions.
    for current, source in (("timings.json", "source_timings.json"), ("edl.json", "source_edl.json"),
                            ("segment_manifest.json", "source_segment_manifest.json"),
                            ("match_plan.json", "source_match_plan.json"),
                            ("narration_profile.json", "source_narration_profile.json")):
        if (root / current).exists():
            shutil.copy2(root / current, root / source)


async def _ingest_recording(record: Any, sentence_id: int, upload: UploadFile, settings: Settings | None = None) -> dict[str, Any]:
    plan = _models(record.task_dir, "match_plan.json", MatchPlanItem)
    if sentence_id in _quote_ids(record, plan):
        await upload.close()
        raise HTTPException(422, "原声句不能上传替代录音；请使用原始素材中的原声。")
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".webm", ".ogg", ".wav", ".mp3", ".m4a"}:
        raise HTTPException(415, "Supported recordings: webm, ogg, wav, mp3, m4a")
    root = record.task_dir
    directory = local_file(root, "recordings", exists=False)
    directory.mkdir(exist_ok=True)
    existing = list(directory.glob("*.wav"))
    if len(existing) >= MAX_RECORDINGS or sum(p.stat().st_size for p in existing) >= MAX_RECORDINGS_BYTES:
        raise HTTPException(413, "Task recording quota reached")
    recording_id = uuid.uuid4().hex
    temporary = directory / f".upload-{recording_id}"
    temporary.mkdir()
    source, decoded = temporary / f"input{suffix}", temporary / "decoded.wav"
    try:
        size = 0
        with source.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_RECORDING_BYTES:
                    raise HTTPException(413, "Recording exceeds 20 MiB")
                output.write(chunk)
        if not size:
            raise HTTPException(422, "Empty recording")
        # Demuxer/protocol allowlists prevent disguised playlists, network fetches
        # and arbitrary local-file references. Browser WebM may lack duration:
        # decode only 120.1 s, then measure the actual PCM rather than trusting tags.
        allowed = "matroska,webm,ogg,wav,mp3,mov,mp4,m4a,3gp,3g2,mj2"
        raw = await run_logged_command([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-format_whitelist", allowed, "-show_streams", "-show_format", "-of", "json", str(source),
        ], temporary, "Validate recording", capture_stdout=True)
        probe = json.loads(raw)
        streams = probe.get("streams", [])
        if sum(s.get("codec_type") == "audio" for s in streams) != 1 or any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams):
            raise HTTPException(422, "Recording must contain one audio stream and no video")
        duration_hint = probe.get("format", {}).get("duration")
        if duration_hint is not None and (not math.isfinite(float(duration_hint)) or float(duration_hint) > 120):
            raise HTTPException(422, "Recording must be at most 120 seconds")
        await run_logged_command([
            "ffmpeg", "-y", "-protocol_whitelist", "file,pipe", "-format_whitelist", allowed,
            "-i", str(source), "-t", "120.1", "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "48000",
            "-c:a", "pcm_s16le", str(decoded),
        ], temporary, "Decode recording")
        duration = await probe_audio_duration(decoded, temporary)
        if not 0 < duration <= 120:
            raise HTTPException(422, "Recording must be at most 120 seconds")
        if sum(p.stat().st_size for p in existing) + decoded.stat().st_size > MAX_RECORDINGS_BYTES:
            raise HTTPException(413, "Task recording quota reached")
        metadata: dict[str, Any] = {"recording_id": recording_id, "sentence_id": sentence_id, "revision": record.revision,
                    "duration": duration, "size": size, "audio_source": "recording", "transcript_verified": False}
        if settings is not None:
            from .providers.asr import create_asr_provider
            asr_audio = temporary / "asr.wav"
            await run_logged_command(["ffmpeg", "-y", "-nostdin", "-protocol_whitelist", "file",
                "-f", "wav", "-i", str(decoded), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(asr_audio)],
                temporary, "Prepare recording ASR")
            async with create_asr_provider(settings) as provider:
                transcript = await provider.transcribe(asr_audio)
            metadata.update(transcript=transcript.text, asr_confidence=transcript.confidence,
                            cpm=sum(c.isalnum() for c in transcript.text) * 60 / duration,
                            cpm_method="asr_alphanumeric_characters_per_decoded_minute")
            # ASR is evidence, not a claim that the recording matches the script.
        decoded.replace(directory / f"{recording_id}.wav")
        try:
            write_json_atomic(directory / f"{recording_id}.json", metadata)
        except BaseException:
            (directory / f"{recording_id}.wav").unlink(missing_ok=True)
            raise
        return metadata
    finally:
        await upload.close()
        shutil.rmtree(temporary, ignore_errors=True)


def create_workbench_router(settings: Settings, task_manager: Any, authorize: Any, before_mutation: Any = None,
                            *, v2: bool = False) -> APIRouter:
    """authorize(request, task_id, write=False) may be sync/async; returns record.

    The host owns task authorization, Origin/CSRF protection, studio locks,
    and cost/rate gating. Every route invokes authorization before reading task data.
    before_mutation may be sync/async and runs with an owned queued reservation;
    it reauthorizes the task but must not start another task job.
    """
    router = APIRouter(prefix="/api/tasks/{task_id}" if v2 else "/api/tasks/{task_id}/workbench", tags=["workbench"])
    locks: dict[str, asyncio.Lock] = {}

    async def access(request: Request, task_id: str, *, write: bool = False) -> Any:
        result = authorize(request, task_id, write=write)
        record = await result if inspect.isawaitable(result) else result
        if record is None:
            raise HTTPException(404, "Task not found")
        if record.task_id != task_id:
            raise HTTPException(403, "Task authorization mismatch")
        return record

    async def mutation_intent(request: Request, record: Any, expected: int, owner: asyncio.Task[Any]) -> None:
        # Reauthorize only after validation AND a shared pending-slot reservation;
        # a stale callback must not steal or consume another operation's slot.
        if before_mutation is not None:
            result = before_mutation(request, record.task_id, write=True)
            if inspect.isawaitable(result):
                await result
        ready(record, expected, reservation=owner)

    def ready(record: Any, expected: int, *, reservation: asyncio.Task[Any] | None = None) -> None:
        from .studio import studio_task_busy
        if studio_task_busy(record.task_dir) or (record.task_dir / "v2_publish_journal.json").exists():
            raise HTTPException(409, "Task export or interrupted publication is active")
        if task_manager.get(record.task_id) is not record:
            raise HTTPException(404, "Task not found")
        if record.revision != expected:
            raise HTTPException(409, {"code": "stale_revision", "current_revision": record.revision})
        if reservation is None:
            busy = record.status != TaskState.done or (record.background and not record.background.done())
        else:
            # Only this request may consume its reservation, never an unrelated
            # queued record. The background owner also makes deletion cancel us.
            busy = (record.status != TaskState.queued or record.background is not reservation
                    or reservation is not asyncio.current_task())
        if busy:
            raise HTTPException(409, "Task already has an active job or no successful output")
        if legacy_task_busy(record.task_dir) or task_operation_busy(record.task_dir):
            raise HTTPException(409, "Task copy is active or legacy work awaits reconciliation")
        # A drain stops NEW admissions, not a slot already reserved before intent.
        if reservation is None and getattr(task_manager, "_draining", False):
            raise HTTPException(503, "Server is draining")

    async def baseline(record: Any) -> None:
        if not (record.task_dir / f"revisions/r{record.revision}/revision.json").exists():
            if task_operation_busy(record.task_dir) or legacy_task_busy(record.task_dir):
                raise HTTPException(409, "Task copy is active or legacy work awaits reconciliation")
            if record.status != TaskState.done:
                raise HTTPException(409, "No successful workbench baseline yet")
            # Reserve the record while the copy runs off-loop. Legacy mutation
            # entry points also test status, so they cannot race a lazy snapshot.
            previous_background = record.background
            record.status = TaskState.queued
            try:
                task_manager._persist_record(record)
                job = asyncio.create_task(_run_blocking_until_complete(
                    partial(snapshot_revision, record, "Original / imported successful task")
                ))
                record.background = job
                await job
            finally:
                record.status = TaskState.done
                record.background = previous_background
                task_manager._persist_record(record)

    def restore_error(record: Any, state: dict[str, Any], code: str) -> None:
        _apply_state(record, state)
        record.status = TaskState.done
        record.error_stage = "workbench"
        record.error_message = code
        record.message = "Last successful revision retained; workbench operation did not complete"
        record.updated_at = datetime.now(timezone.utc)
        task_manager._persist_record(record)

    async def execute(record: Any, payload: EditRequest | RestoreRequest, saved: dict[str, Any], revision: int,
                      request: Request) -> None:
        temporary = record.task_dir / f".workbench-{uuid.uuid4().hex}"
        version_created = False
        committed = False
        try:
            async with task_manager._semaphore:
                async with asyncio.timeout(MAX_OPERATION_SECONDS):
                    started = time.monotonic()
                    record.status = TaskState.running
                    record.message = "Workbench revision processing"
                    record.processing_started_at = datetime.now(timezone.utc)
                    record.processing_completed_at = record.total_elapsed_seconds = None
                    record.updated_at = record.processing_started_at
                    task_manager._persist_record(record)
                    source = revision_dir(record.task_dir, payload.revision if isinstance(payload, RestoreRequest) else saved["revision"])
                    if isinstance(payload, RestoreRequest):
                        await _run_blocking_until_complete(partial(copy_files, source, temporary, artifact_files(source)))
                        state = read_json(source, "revision.json")["state"]
                    else:
                        await _prepare_workspace(record, temporary, source, payload)
                        state = copy.deepcopy(saved)
                    work = SimpleNamespace(task_dir=temporary, task_id=record.task_id)
                    _apply_state(work, state)
                    work.revision = revision
                    work.processing_started_at = record.processing_started_at
                    work.processing_completed_at = work.total_elapsed_seconds = None
                    if isinstance(payload, EditRequest):
                        for key in ("pacing", "caption_style", "enhance_speech"):
                            value = getattr(payload, key)
                            if value is not None:
                                setattr(work.preferences, key, value)
                        original = SimpleNamespace(task_dir=record.task_dir, task_id=record.task_id)
                        _apply_state(original, saved)
                        original.uploads = _owned_uploads(record)
                        work.uploads = [asset.model_copy(deep=True, update={
                            "path": local_file(temporary, f"raw/{asset.stored_name}", exists=False),
                        }) for asset in original.uploads]
                        if payload.speakers is not None:
                            work.speakers = [speaker.model_copy(deep=True) for speaker in payload.speakers]
                        if isinstance(payload, BatchEditRequest):
                            from .v2_editing import execute_plan
                            await execute_plan(work, original, record, payload, settings, task_manager)
                        elif _uses_mode_pipeline(work):
                            first_stage = _first_edit_stage(payload, saved)
                            _reset_mode_progress(work, first_stage)
                            work.reporter = _WorkbenchReporter(record, work, task_manager, first_stage)
                            work.progress_callback = work.reporter.publish
                            work.rerun_stages = list(range(first_stage, 11))
                            work.reporter.publish()
                        if not isinstance(payload, BatchEditRequest):
                            await _edit_workspace(work, original, payload, settings)
                        if not isinstance(payload, BatchEditRequest) and _uses_mode_pipeline(work) and any(
                            stage.status != StageState.done or stage.started_at is None or stage.completed_at is None
                            for stage in work.stages[first_stage - 1:]
                        ):
                            raise RevisionError("Mode editor did not report actual completion of its required stages")
                    work.progress = 100
                    work.message = f"Workbench revision {revision} complete"
                    work.processing_completed_at = datetime.now(timezone.utc)
                    work.total_elapsed_seconds = time.monotonic() - started
                    # Prepare immutable history BEFORE publication; rollback removes an
                    # uncommitted version. No cancellation point during actual publish.
                    await _run_blocking_until_complete(partial(snapshot_revision, work, f"Restore r{payload.revision}" if isinstance(payload, RestoreRequest) else "Workbench edit"))
                    if v2:
                        latest = await access(request, record.task_id, write=True)
                        if (latest is not record or task_manager.get(record.task_id) is not record
                                or record.background is not asyncio.current_task() or record.revision != saved["revision"]):
                            raise HTTPException(403, "Task authorization changed before publication")
                    target = local_file(record.task_dir, f"revisions/r{revision}", exists=False)
                    if target.exists():
                        raise RevisionError("Revision identity collision")
                    if not isinstance(payload, BatchEditRequest):
                        (temporary / f"revisions/r{revision}").rename(target)
                        version_created = True
                    def persist() -> None:
                        nonlocal version_created
                        if isinstance(payload, BatchEditRequest):
                            (temporary / f"revisions/r{revision}").rename(target)
                            version_created = True
                        _apply_state(record, record_metadata(work))
                        record.status = TaskState.done
                        record.error_stage = record.error_message = None
                        record.updated_at = datetime.now(timezone.utc)
                        task_manager._persist_record(record)
                        if isinstance(payload, BatchEditRequest):
                            from .v2_editing import finish_plan
                            finish_plan(record.task_dir, payload, "succeeded")
                    publish_artifacts(temporary, record.task_dir, persist, durable=isinstance(payload, BatchEditRequest))
                    committed = True
        except BaseException as exc:
            code = "operation_cancelled" if isinstance(exc, asyncio.CancelledError) else "operation_failed"
            if isinstance(exc, RevisionError):
                code = f"validation_failed: {exc}"
            elif isinstance(exc, TimeoutError):
                code = "operation_timed_out"
            # Do not expose provider exceptions, credentials, or arbitrary paths.
            restore_error(record, saved, code)
            if isinstance(payload, BatchEditRequest):
                from .v2_editing import finish_plan
                finish_plan(record.task_dir, payload, "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed")
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            from .revisions import _snapshot_io_path
            if version_created and not committed:
                shutil.rmtree(_snapshot_io_path(record.task_dir / f"revisions/r{revision}"), ignore_errors=True)
            shutil.rmtree(_snapshot_io_path(temporary), ignore_errors=True)

    async def enqueue(request: Request, record: Any, payload: EditRequest | RestoreRequest) -> dict[str, Any]:
        ready(record, payload.expected_revision)
        summaries = version_summaries(record.task_dir)
        if len(summaries) >= MAX_REVISIONS:
            raise HTTPException(409, "Revision limit reached")
        pending = sum(r.status in {TaskState.queued, TaskState.running} for r in task_manager._tasks.values())
        if pending >= settings.max_pending_tasks:
            raise HTTPException(429, "Pending task capacity reached")
        saved = record_metadata(record)
        revision = max([record.revision, *(s["revision"] for s in summaries)]) + (len(payload.steps) if isinstance(payload, BatchEditRequest) else 1)
        previous = {key: getattr(record, key) for key in (
            "status", "background", "message", "error_stage", "error_message", "updated_at",
            "current_stage", "stage_name", "progress", "stages", "processing_started_at",
            "processing_completed_at", "total_elapsed_seconds",
        )}
        owner = asyncio.current_task()
        assert owner is not None
        handed_off = False
        # No await between checking capacity and marking this manager-owned
        # record queued. Other routers AND TaskManager.add_task see the slot;
        # a router-local lock/counter would not protect against their admissions.
        record.status = TaskState.queued
        record.background = owner
        record.error_stage = record.error_message = None
        if isinstance(payload, EditRequest) and _uses_mode_pipeline(record):
            _reset_mode_progress(record, _first_edit_stage(payload, saved))
        record.processing_started_at = record.processing_completed_at = record.total_elapsed_seconds = None
        record.message = f"Workbench revision {revision} queued"
        record.updated_at = datetime.now(timezone.utc)
        try:
            # Persistence can reject too, so do it before the final host recheck.
            task_manager._persist_record(record)
            await mutation_intent(request, record, payload.expected_revision, owner)
            if isinstance(payload, BatchEditRequest):
                from .v2_editing import save_plan
                save_plan(record.task_dir, payload, revision)
            # Consume the owned slot without another capacity/limit/drain check:
            # other admissions during the callback cannot reject us after intent.
            job = asyncio.create_task(execute(record, payload, saved, revision, request), name=f"workbench-{record.task_id}")
            def completed(task: asyncio.Task[Any]) -> None:
                # Cancellation before the coroutine's first instruction has no finally.
                if (task.cancelled() and record.background is task and record.status == TaskState.queued
                        and task_manager.get(record.task_id) is record):
                    restore_error(record, saved, "operation_cancelled")
                    if isinstance(payload, BatchEditRequest):
                        from .v2_editing import finish_plan
                        finish_plan(record.task_dir, payload, "cancelled")
            job.add_done_callback(completed)
            record.background = job
            handed_off = True
            return {"task_id": record.task_id, "revision": revision, "current_revision": saved["revision"], "status": "queued",
                    **({"operation_id": payload.operation_id, "steps": payload.steps,
                        "plan": {"operation_id": payload.operation_id, "steps": payload.steps},
                        "visible_version": len(summaries)} if isinstance(payload, BatchEditRequest) else {})}
        finally:
            if not handed_off and record.background is owner:
                record.background = previous["background"]
                # Restore only our operational state on auth failure/cancellation;
                # never resurrect a deleted task or overwrite a different owner.
                if task_manager.get(record.task_id) is record and record.status == TaskState.queued:
                    for key, value in previous.items():
                        setattr(record, key, value)
                    task_manager._persist_record(record)

    @router.get("/context")
    async def context(request: Request, task_id: str) -> Any:
        record = await access(request, task_id)
        async with locks.setdefault(task_id, asyncio.Lock()):
            try:
                await baseline(record)
                root = revision_dir(record.task_dir, record.revision)
                timings = _models(root, "timings.json", SentenceTiming)
                shots = _models(root, "shots_annotated.json", AnnotatedShot)
                edl = _models(root, "edl.json", EDLItem)
                audio = _audio_metadata(root)
                used_by = {c.shot_id: e.sentence_id for e in edl for c in e.clips}
                state = read_json(root, "revision.json")["state"]
                report = _safe_report(root, task_id, audio, state)
                result: dict[str, Any] = {
                    "task_id": task_id, "revision": record.revision, "status": record.status.value,
                    "script": state["script"], "preferences": state["preferences"],
                    "last_operation_error": record.error_message if record.error_stage == "workbench" else None,
                    "mode": report["mode"], "mode_contract": state.get("mode_contract", False),
                    "speakers": report["speakers"], "jumpcuts": report["jumpcuts"],
                    "sentences": state.get("sentences", []), "quality_gate_mode": state.get("quality_gate_mode"),
                    "report": report,
                    "timings": [{**t.model_dump(mode="json", exclude={"audio_path"}),
                                 **audio.get(str(t.sentence_id), {"audio_source": t.audio_kind})} for t in timings],
                    "current_shots": [{"sentence_id": e.sentence_id, "shot_ids": [c.shot_id for c in e.clips]} for e in edl],
                    "shots": [{**s.model_dump(mode="json", include={"shot_id", "source_index", "source_scene_index", "start", "end", "duration", "status", "media_origin", "description", "scene_type", "subjects", "actions", "keywords", "ocr_texts", "entities", "source_transcript", "quality"}),
                               "unused": s.shot_id not in used_by, "used_by_sentence_id": used_by.get(s.shot_id),
                               "selectable": s.shot_id not in used_by and _shot_ok(record.task_dir, s),
                               "thumb_url": f"/api/tasks/{task_id}/thumbs/{s.shot_id}.jpg"} for s in shots],
                    "versions": version_summaries(record.task_dir),
                }
                return result
            except RevisionError as exc:
                raise HTTPException(409, str(exc)) from exc

    @router.get("/versions")
    async def versions(request: Request, task_id: str) -> Any:
        record = await access(request, task_id)
        async with locks.setdefault(task_id, asyncio.Lock()):
            await baseline(record)
            return {"current_revision": record.revision, "versions": version_summaries(record.task_dir)}

    @router.get("/versions/{rev}/video")
    async def video(request: Request, task_id: str, rev: int) -> Any:
        record = await access(request, task_id)
        async with locks.setdefault(task_id, asyncio.Lock()):
            await baseline(record)
            try:
                path = local_file(revision_dir(record.task_dir, rev), "final.mp4")
            except RevisionError as exc:
                raise HTTPException(404, "Revision not found") from exc
            return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})

    @router.get("/versions/{rev}/report")
    async def report(request: Request, task_id: str, rev: int) -> Any:
        record = await access(request, task_id)
        async with locks.setdefault(task_id, asyncio.Lock()):
            await baseline(record)
            try:
                root = revision_dir(record.task_dir, rev)
                state = read_json(root, "revision.json")["state"]
                return {"revision": rev, "report": _safe_report(root, task_id, _audio_metadata(root), state)}
            except RevisionError as exc:
                raise HTTPException(404, "Revision not found") from exc

    @router.post("/edit", status_code=202)
    async def edit(request: Request, task_id: str, payload: EditRequest) -> Any:
        record = await access(request, task_id, write=True)
        async with locks.setdefault(task_id, asyncio.Lock()):
            ready(record, payload.expected_revision)
            try:
                _validate_edit(record, payload, settings)
                await baseline(record)
                return await enqueue(request, record, payload)
            except RevisionError as exc:
                raise HTTPException(422, str(exc)) from exc

    if v2:
        @router.post("/apply", status_code=202)
        @router.post("/addop", status_code=202)
        @router.post("/run", status_code=202)
        async def apply(request: Request, task_id: str) -> Any:
            from .v2_editing import ApplyRequest, compile_plan, parse_request
            record = await access(request, task_id, write=True)
            payload = await parse_request(request, ApplyRequest)
            record = await access(request, task_id, write=True)
            async with locks.setdefault(task_id, asyncio.Lock()):
                ready(record, payload.expected_revision)
                try:
                    compiled = compile_plan(record, payload, settings)
                    await baseline(record)
                    latest = await access(request, task_id, write=True)
                    if latest is not record:
                        raise HTTPException(403, "Task authorization changed")
                    return await enqueue(request, record, compiled)
                except RevisionError as exc:
                    raise HTTPException(422, str(exc)) from exc

        @router.get("/operations/{operation_id}")
        async def operation(request: Request, task_id: str, operation_id: str) -> Any:
            record = await access(request, task_id)
            if not re.fullmatch(RECORDING_ID, operation_id):
                raise HTTPException(404, "Operation not found")
            try:
                result = read_json(record.task_dir, f"v2_operations/{operation_id}.json")
            except RevisionError as exc:
                raise HTTPException(404, "Operation not found") from exc
            if result["state"] in {"queued", "running"} and (record.background is None or record.background.done()):
                result = {**result, "state": "interrupted", "error": "not_resumed"}
            return result

        router.add_api_route("/versions/{rev}", report, methods=["GET"])

    @router.post("/restore-legacy" if v2 else "/restore", status_code=202)
    async def restore(request: Request, task_id: str, payload: RestoreRequest) -> Any:
        record = await access(request, task_id, write=True)
        async with locks.setdefault(task_id, asyncio.Lock()):
            ready(record, payload.expected_revision)
            await baseline(record)
            try:
                revision_dir(record.task_dir, payload.revision)
            except RevisionError as exc:
                raise HTTPException(404, "Revision not found") from exc
            return await enqueue(request, record, payload)

    @router.post("/recordings-legacy" if v2 else "/recordings", status_code=201)
    async def recordings(request: Request, task_id: str, sentence_id: int = Form(ge=0),
                         expected_revision: int = Form(ge=0), file: UploadFile = File()) -> Any:
        record = await access(request, task_id, write=True)
        async with locks.setdefault(task_id, asyncio.Lock()):
            ready(record, expected_revision)
            timings, plan, _, _ = _current(record.task_dir)
            if sentence_id not in {t.sentence_id for t in timings}:
                raise HTTPException(422, "Unknown sentence")
            if sentence_id in _quote_ids(record, plan):
                await file.close()
                raise HTTPException(422, "原声句不能上传替代录音；请使用原始素材中的原声。")
            saved = record_metadata(record)
            record.status = TaskState.queued
            record.message = "Validating sentence recording"
            task_manager._persist_record(record)
            async def ingest() -> Any:
                async with task_manager._semaphore:
                    async with asyncio.timeout(180):
                        return await _ingest_recording(record, sentence_id, file, settings) if v2 else await _ingest_recording(record, sentence_id, file)
            job = asyncio.create_task(ingest())
            record.background = job
            try:
                result = await job
                if v2:
                    latest = await access(request, task_id, write=True)
                    if latest is not record or task_manager.get(task_id) is not record or record.revision != expected_revision:
                        raise HTTPException(403, "Task authorization changed")
                return result
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(422, "Recording could not be validated or decoded") from exc
            finally:
                _apply_state(record, saved)
                record.status = TaskState.done
                task_manager._persist_record(record)
                await file.close()

    if v2:
        @router.post("/restore", status_code=202)
        async def restore_v2(request: Request, task_id: str) -> Any:
            from .v2_editing import V2RestoreRequest, parse_request
            record = await access(request, task_id, write=True)
            payload = await parse_request(request, V2RestoreRequest)
            if await access(request, task_id, write=True) is not record:
                raise HTTPException(403, "Task authorization changed")
            return await restore(request, task_id, RestoreRequest(revision=payload.rev,
                expected_revision=record.revision if payload.expected_revision is None else payload.expected_revision))

        @router.post("/recordings", status_code=201)
        async def recordings_v2(request: Request, task_id: str, rowid: int | None = Form(default=None, ge=0),
                                row_id: int | None = Form(default=None, ge=0),
                                expected_revision: int = Form(ge=0), audio: UploadFile = File()) -> Any:
            if (rowid is None and row_id is None) or (rowid is not None and row_id is not None and rowid != row_id):
                await audio.close()
                raise HTTPException(422, "Supply rowid or row_id without conflicting identities")
            return await recordings(request, task_id, rowid if rowid is not None else row_id, expected_revision, audio)

    return router