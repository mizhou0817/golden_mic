"""Opt-in v2 editing adapter. No globals, settings loading or automatic mounting.

Host: include_router(create_v2_editing_router(settings, manager, authorize,
before_mutation)). Authorization callbacks have the workbench signature and
must enforce task capability, Origin/CSRF and host admission. Startup must call
revisions.recover_v2_publication before restoring TaskRecords or serving reads.
Publication /checks PUT remains the host's responsibility.

JSON maps use canonical decimal row IDs. voices maps rows to server recording
IDs; takes maps rows to server-known take IDs; speakers maps speaker IDs to
{name, role}. There are no client media paths or nickname/alias objects.
"""
from __future__ import annotations

import asyncio
import copy
import math
import re
import shutil
import uuid
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import MatchPlanItem, SentenceInput, Speaker, StageState
from .revisions import RevisionError, read_json, record_metadata
from .storage import write_json_atomic


SAMPLE_ASSETS = Path(__file__).parent / "assets" / "samples"


SAMPLE_MEDIA = re.compile(r"(?:sources/[0-9]{2}\.(?:mp4|jpg)|thumbs/[0-9]{1,4}\.jpg)")
SAMPLE_MODES = {"voiceover", "mixed", "original"}
def _sample_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum \
            or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise RevisionError("Invalid sample walkthrough text")
    return value


def _sample_walkthrough(folder: Path, files: dict[str, str], row_ids: set[int]) -> dict[str, Any]:
    """The steps that produced the sample: script, chosen mode/settings and the uploaded clips."""
    data = read_json(folder, "walkthrough.json")
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("mode") not in SAMPLE_MODES:
        raise RevisionError("Invalid sample walkthrough")
    settings = data.get("settings", [])
    sources = data.get("sources")
    if not isinstance(settings, list) or len(settings) > 12 or not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise RevisionError("Invalid sample walkthrough")
    result_sources = []
    for index, source in enumerate(sources, 1):
        name = f"sources/{index:02d}"
        if not isinstance(source, dict) or source.get("file") != f"{name}.mp4" or source.get("thumb") != f"{name}.jpg" \
                or f"{name}.mp4" not in files or f"{name}.jpg" not in files:
            raise RevisionError("Unregistered sample source")
        duration = source.get("duration")
        used_by = source.get("used_by", [])
        if type(duration) not in (int, float) or not 0 < duration <= 3600 or not isinstance(used_by, list) \
                or any(type(value) is not int or value not in row_ids for value in used_by):
            raise RevisionError("Invalid sample source")
        result_sources.append({"index": index, "name": _sample_text(source.get("name"), 80), "duration": round(float(duration), 2),
                               "used_by": sorted(set(used_by)), "video_url": f"/api/samples/default/{name}.mp4",
                               "thumb_url": f"/api/samples/default/{name}.jpg"})
    return {"script": _sample_text(data.get("script"), 4000), "mode": data["mode"],
            "settings": [{"label": _sample_text(item.get("label"), 20), "value": _sample_text(item.get("value"), 80)}
                         for item in settings if isinstance(item, dict)][:12],
            "sources": result_sources}


def packaged_sample(video: bool = False, media: str | None = None, _digests: dict[tuple[Any, ...], str] = {}) -> Any:  # noqa: B006
    """Only an explicitly packaged hash registry can supply the read-only demo.

    No fallback to user tasks, downloads or synthesized media. The registry's
    default entry binds report.json and final.mp4 under its relative directory;
    an optional walkthrough.json with its source clips and thumbnails is bound the same way.
    _digests remembers a file's SHA-256 only while its size/inode/times are unchanged, so the
    page's many picture requests do not re-hash every clip.
    """
    import hashlib
    from fastapi.responses import FileResponse, JSONResponse
    from .models import ReportResponse
    from .revisions import local_file
    from .workbench import _safe_report
    try:
        entry = read_json(SAMPLE_ASSETS, "registry.json")["default"]
        folder = local_file(SAMPLE_ASSETS, entry["directory"], exists=False)
        if not re.fullmatch(r"[a-f0-9]{32}", entry["task_id"]) or not isinstance(entry["title"], str):
            raise RevisionError("Invalid sample identity")
        files = entry["files"]
        if not isinstance(files, dict) or not {"report.json", "final.mp4"} <= files.keys() or len(files) > 100:
            raise RevisionError("Incomplete sample registry")
        for name, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise RevisionError("Invalid sample digest")
            path = local_file(folder, name)
            info = path.stat()
            key = (str(path), info.st_size, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
            if key not in _digests:
                with path.open("rb") as stream:
                    _digests[key] = hashlib.file_digest(stream, "sha256").hexdigest()
            if _digests[key] != expected:
                raise RevisionError("Sample integrity failed")
        # Reject unregistered auxiliary metadata consumed by the projection.
        for name in ("timings.json", "shots_annotated.json", "v2_apply_plan.json", "walkthrough.json"):
            if (folder / name).exists() and name not in files:
                raise RevisionError("Unregistered sample metadata")
        if media is not None:
            if not SAMPLE_MEDIA.fullmatch(media) or media not in files:
                return JSONResponse({"code": "sample_media_missing", "detail": "范例里没有这个文件。", "read_only": True}, status_code=404)
            return FileResponse(local_file(folder, media), media_type="video/mp4" if media.endswith(".mp4") else "image/jpeg",
                                headers={"Cache-Control": "no-store"})
        report = _safe_report(folder, entry["task_id"], {})
        ReportResponse.model_validate(report)
        for row in report["rows"]:
            thumb = f"thumbs/{row['sentence_id']}.jpg"
            row["thumb_url"] = f"/api/samples/default/{thumb}" if thumb in files else None
            for beat in row["visual_beats"]:
                beat["thumb_url"] = None
        if video:
            return FileResponse(local_file(folder, "final.mp4"), media_type="video/mp4",
                                headers={"Cache-Control": "no-store"})
        result = {"read_only": True, "task_id": entry["task_id"], "title": entry["title"][:80],
                  "report": report, "video_url": "/api/samples/default/video"}
        if "walkthrough.json" in files:
            result["walkthrough"] = _sample_walkthrough(folder, files, {row["sentence_id"] for row in report["rows"]})
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return JSONResponse({"code": "sample_unavailable", "detail": "示例素材尚未安装或校验未通过，请创建自己的作品。", "read_only": True}, status_code=503)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Trim(StrictRequest):
    t0: float = Field(ge=0)
    t1: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> Trim:
        if self.t1 <= self.t0:
            raise ValueError("Invalid trim interval")
        return self


class Person(StrictRequest):
    name: str = Field(max_length=8)
    role: str = Field(max_length=12)


class Replace(StrictRequest):
    instruction: str = Field(min_length=1, max_length=500)


class V2RestoreRequest(StrictRequest):
    rev: int = Field(ge=0)
    expected_revision: int | None = Field(default=None, ge=0)


class ApplyRequest(StrictRequest):
    expected_revision: int = Field(ge=0)
    deleted: list[int] = Field(default_factory=list, max_length=200)
    edits: dict[str, str] = Field(default_factory=dict, max_length=200)
    voices: dict[str, str] = Field(default_factory=dict, max_length=200)
    pacing: Literal["slow", "normal", "fast"] | None = None
    trims: dict[str, Trim] = Field(default_factory=dict, max_length=200)
    takes: dict[str, str] = Field(default_factory=dict, max_length=200)
    to_narration: list[int] = Field(default_factory=list, max_length=200)
    speakers: dict[str, Person] = Field(default_factory=dict, max_length=100)
    replaces: dict[str, Replace] = Field(default_factory=dict, max_length=200)

    @model_validator(mode="after")
    def identities(self) -> ApplyRequest:
        for values in (self.deleted, self.to_narration):
            if len(values) != len(set(values)) or any(type(i) is not int or i < 0 for i in values):
                raise ValueError("Invalid row identities")
        for values in (self.edits, self.voices, self.trims, self.takes, self.replaces):
            if any(not re.fullmatch(r"0|[1-9][0-9]{0,8}", key) for key in values):
                raise ValueError("Row map keys must be canonical decimal IDs")
        operations = set(self.edits) | set(self.voices) | set(self.trims) | set(self.takes) | set(self.replaces)
        if set(self.deleted) & ({int(key) for key in operations} | set(self.to_narration)):
            raise ValueError("Deleted rows cannot also be edited")
        for value in [*self.edits.values(), *(r.instruction for r in self.replaces.values()),
                      *(v for p in self.speakers.values() for v in (p.name, p.role))]:
            if any(ord(c) < 32 for c in value):
                raise ValueError("Control characters are not allowed")
        return self


async def parse_request(request: Request, model: type[BaseModel]) -> Any:
    body = bytearray()
    try:
        async with asyncio.timeout(30):
            async for block in request.stream():
                if len(body) + len(block) > 256 * 1024:
                    raise HTTPException(413, "Request exceeds 256 KiB")
                body.extend(block)
        return model.model_validate_json(bytes(body))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Invalid v2 request") from exc
    except TimeoutError as exc:
        raise HTTPException(408, "Request body timed out") from exc


def compile_plan(record: Any, request: ApplyRequest, settings: Any) -> Any:
    from . import workbench as wb

    if not wb._uses_mode_pipeline(record):
        raise RevisionError("v2 apply requires a production-mode task; use the legacy workbench")
    timings, plan, _, _ = wb._current(record.task_dir)
    ids = [t.sentence_id for t in timings]
    if not set(request.deleted).issubset(ids):
        raise RevisionError("Unknown deleted row")
    keep = [i for i in ids if i not in request.deleted]
    if not keep:
        raise RevisionError("Retain at least one sentence")
    replaces = {int(k): v.instruction.strip() for k, v in request.replaces.items()}
    if not set(replaces).issubset(keep) or any(not value for value in replaces.values()):
        raise RevisionError("Invalid replacement row or instruction")
    if set(replaces) & (wb._quote_ids(record, plan) - set(request.to_narration)):
        raise RevisionError("Original speech cannot replace pictures without explicit conversion")
    people = [Speaker.model_validate(p).model_copy(deep=True) for p in record.speakers]
    known = {p.id: p for p in people}
    for key, value in request.speakers.items():
        if key not in known:
            raise RevisionError("Unknown server speaker identity")
        known[key].name, known[key].title = value.name.strip(), value.role.strip()
    try:
        payload = wb.EditRequest(
            expected_revision=request.expected_revision, keep_sentence_ids=keep,
            edits=[wb.SentenceEdit(sentence_id=int(key), text=request.edits.get(key), recording_id=request.voices.get(key))
                   for key in sorted(set(request.edits) | set(request.voices), key=int)],
            pacing=request.pacing,
            quote_trims=[wb.QuoteTrimEdit(id=int(key), start=value.t0, end=value.t1) for key, value in request.trims.items()],
            quote_takes=[wb.QuoteTakeEdit(id=int(key), take_id=value) for key, value in request.takes.items()],
            to_narration=request.to_narration, speakers=people if request.speakers else None,
        )
        remix = bool(request.deleted or request.edits or request.voices or request.pacing is not None
                     or request.trims or request.takes or request.to_narration or request.speakers)
        if remix:
            wb._validate_edit(record, payload, settings)
        elif not replaces:
            # An explicit empty Apply is a real composition/QC run, not a no-op.
            remix = True
        stages = list(range(6 if request.to_narration else wb._first_edit_stage(payload, record_metadata(record)), 11))
        if not request.model_dump(exclude={"expected_revision"}, exclude_defaults=True):
            stages = [9, 10]
        steps = ([{"kind": "remix", "stages": stages, "state": "pending"}] if remix else [])
        steps += [{"kind": "replace", "row_id": row, "stages": [6, 9, 10], "state": "pending"} for row in sorted(replaces)]
        return wb.BatchEditRequest(**payload.model_dump(), operation_id=uuid.uuid4().hex,
                                   steps=steps, replacements=replaces, remix=remix)
    except ValidationError as exc:
        raise RevisionError("Invalid or conflicting v2 edits") from exc


def save_plan(root: Path, payload: Any, revision: int) -> None:
    folder = root / "v2_operations"
    folder.mkdir(exist_ok=True)
    # Failed operations also consume a bounded durable receipt slot.
    if len(list(folder.glob("*.json"))) >= 200:
        raise RevisionError("v2 operation receipt limit reached")
    write_json_atomic(folder / f"{payload.operation_id}.json", {
        "schema_version": 1, "operation_id": payload.operation_id, "state": "queued",
        "expected_revision": payload.expected_revision, "revision": revision,
        "steps": copy.deepcopy(payload.steps), "replace_failures": {},
    })


def finish_plan(root: Path, payload: Any, state: str) -> None:
    name = f"v2_operations/{payload.operation_id}.json"
    value = read_json(root, name)
    value["state"] = state
    write_json_atomic(root / name, value)


def current_plan(root: Path) -> dict[str, Any] | None:
    """Read the latest durable operation, including failures; no auto-resume."""
    from .revisions import local_file
    folder = local_file(root, "v2_operations", exists=False)
    files = list(folder.glob("*.json")) if folder.exists() else []
    files = [p for p in files if re.fullmatch(r"[0-9a-f]{32}\.json", p.name)]
    if not files:
        return None
    return read_json(folder, max(files, key=lambda p: p.stat().st_mtime_ns).name)


class ReplacementUnavailable(RevisionError):
    def __init__(self, code: Literal["none", "used", "abstract"]) -> None:
        self.code = code
        super().__init__(code)


async def _replacement(work: Any, row: int, instruction: str, settings: Any) -> None:
    """Actual mode matcher -> cached render -> measured QC, no audio/subtitle edit."""
    from . import mode_pipeline as mp, workbench as wb

    root = work.task_dir
    timings, plan, shots, edl = wb._current(root)
    manifest = read_json(root, "production_mode.json")
    snapshots = read_json(root, "pretranscripts.json")
    reporter = work.reporter
    reporter.start_stage(work, 6, "Select a different unused source shot")
    used = {clip.shot_id for item in edl for clip in item.clips}
    legal = set(manifest["broll_shot_ids"])
    available = [s for s in shots if s.shot_id in legal and wb._shot_ok(root, s)]
    if not available:
        raise ReplacementUnavailable("none")
    pool = [s for s in available if s.shot_id not in used]
    reusing = mp._shot_reuse_accepted(work)
    if reusing:
        # Repeated footage is allowed: search all of it except what this sentence shows now (unused leftovers
        # alone are often tiny windows). A shared pick is cut into its own real window below; nothing is frozen.
        current = {clip.shot_id for entry in edl if entry.sentence_id == row for clip in entry.clips} | {item_shot for item_shot in [
            next(p.shot_id for p in plan if p.sentence_id == row)]}
        pool = [s for s in available if s.shot_id not in current]
    if not pool:
        raise ReplacementUnavailable("used")
    item = next(p for p in plan if p.sentence_id == row)
    query = mp._sentences([SentenceInput(idx=row, text=item.text, kind="narration")])[0]
    query.visual_beats[0].text = item.text + " " + instruction
    matches = await mp._match_narration(work, [query], pool, settings,
        lambda f, text: reporter.update_stage(work, 6, f, text))
    match = matches[0]
    if not any(c.shot_id == match.shot_id for c in match.candidates):
        raise ReplacementUnavailable("abstract")
    if match.is_fallback or match.confidence < settings.quality_min_match_confidence:
        if not reusing:
            # Without consent to repeat footage keep the strict rule: a weak retrieval is no evidence.
            raise ReplacementUnavailable("abstract")
        # The user asked for this picture: use the best real candidate and keep it flagged for review
        # (the quality check lists it before export) instead of refusing.
        match.is_fallback = True
    match.text, match.replacement_instruction = item.text, instruction
    plan = [match if p.sentence_id == row else p for p in plan]
    if reusing and sum(p.shot_id == match.shot_id for p in plan) > 1:
        narration = [p for p in plan if p.kind == "narration"]
        shots, _ = await mp._allocate_reused_footage(work, narration, shots, manifest, {p.sentence_id: p.text for p in narration},
                                                     mp._preferences(work))
    reporter.complete_stage(work, 6, "Selected candidate retains actual matcher confidence and evidence")
    reporter.start_stage(work, 9, "Render replacement against unchanged voice and subtitle clocks")
    await mp._render_mode(work, plan, timings, shots, snapshots, manifest, settings,
                         lambda f, text: reporter.update_stage(work, 9, f, text))
    reporter.complete_stage(work, 9, "Replacement media rendered")
    reporter.start_stage(work, 10, "Measure replacement and regenerate truthful quality report")
    await mp._complete_mode(work, plan, shots, timings, manifest, settings)
    reporter.complete_stage(work, 10, "Replacement quality check complete")


async def execute_plan(work: Any, original: Any, record: Any, payload: Any, settings: Any, manager: Any) -> None:
    from . import mode_pipeline as mp, workbench as wb

    name = f"v2_operations/{payload.operation_id}.json"
    receipt = read_json(record.task_dir, name)
    receipt["state"] = "running"
    succeeded = 0
    for step in receipt["steps"]:
        step["state"] = "running"
        write_json_atomic(record.task_dir / name, receipt)
        candidate = work
        folder = work.task_dir.parent / f".v2-step-{uuid.uuid4().hex}"
        try:
            if step["kind"] == "replace":
                await wb._prepare_workspace(work, folder, work.task_dir)
                candidate = SimpleNamespace(task_dir=folder, task_id=work.task_id, uploads=[a.model_copy(deep=True) for a in work.uploads])
                wb._apply_state(candidate, record_metadata(work))
            first = step["stages"][0]
            wb._reset_mode_progress(candidate, first)
            for stage in candidate.stages[first - 1:]:
                if stage.number not in step["stages"]:
                    stage.status, stage.fraction = StageState.done, 1.0
            candidate.reporter = wb._WorkbenchReporter(record, candidate, manager, first)
            if step["kind"] == "remix":
                edit = wb.EditRequest.model_validate(payload.model_dump(exclude={"operation_id", "steps", "replacements", "remix"}))
                if step["stages"] == [9, 10]:
                    timings, plan, shots, _ = wb._current(candidate.task_dir)
                    manifest = read_json(candidate.task_dir, "production_mode.json")
                    snapshots = read_json(candidate.task_dir, "pretranscripts.json")
                    candidate.reporter.start_stage(candidate, 9, "Recompose committed picture and audio")
                    await mp._render_mode(candidate, plan, timings, shots, snapshots, manifest, settings,
                        lambda f, text: candidate.reporter.update_stage(candidate, 9, f, text))
                    candidate.reporter.complete_stage(candidate, 9, "Recomposition complete")
                    candidate.reporter.start_stage(candidate, 10, "Measure recomposed output")
                    await mp._complete_mode(candidate, plan, shots, timings, manifest, settings)
                    candidate.reporter.complete_stage(candidate, 10, "Quality check complete")
                if edit.to_narration:
                    candidate.reporter.start_stage(candidate, 6, "Match explicitly converted narration to source footage")
                    _, plan, shots, _ = wb._current(candidate.task_dir)
                    manifest = read_json(candidate.task_dir, "production_mode.json")
                    converted = set(edit.to_narration)
                    retained = [p for p in plan if p.sentence_id in edit.keep_sentence_ids]
                    for item in retained:
                        if item.sentence_id in converted:
                            item.kind, item.to_narration, item.sync_sound = "narration", True, None
                    pool = await mp._exclude_quotes(candidate, [s for s in shots if s.shot_id in set(manifest.get("base_broll_shot_ids", manifest["broll_shot_ids"]))], retained, manifest)
                    used = {p.shot_id for p in retained if p.sentence_id not in converted}
                    queries = mp._sentences([SentenceInput(idx=p.sentence_id, text=p.text, kind="narration") for p in retained if p.sentence_id in converted])
                    replacements = {p.sentence_id: p for p in await mp._match_narration(candidate, queries,
                        [s for s in pool if s.shot_id not in used], settings,
                        lambda f, text: candidate.reporter.update_stage(candidate, 6, f, text))}
                    for item in plan:
                        if item.sentence_id in converted:
                            selected = replacements[item.sentence_id]
                            selected.source, selected.alt_takes, selected.trim = item.source, item.alt_takes, item.trim
                            selected.to_narration = True
                            plan[plan.index(item)] = selected
                    wb._write_models(candidate.task_dir, "match_plan.json", plan)
                    wb._write_models(candidate.task_dir, "shots_annotated.json", list({s.shot_id: s for s in [*shots, *pool]}.values()))
                    write_json_atomic(candidate.task_dir / "production_mode.json", manifest)
                    candidate.reporter.complete_stage(candidate, 6, "Conversion source selection complete")
                    candidate.reporter.first_stage = 7
                    edit = edit.model_copy(update={"to_narration": []})
                if step["stages"] != [9, 10]:
                    await wb._edit_workspace(candidate, original, edit, settings)
            else:
                await _replacement(candidate, step["row_id"], payload.replacements[step["row_id"]], settings)
            if any(candidate.stages[i - 1].status != StageState.done or candidate.stages[i - 1].started_at is None
                   or candidate.stages[i - 1].completed_at is None for i in step["stages"]):
                raise RevisionError("Required pipeline stages did not actually finish")
            if candidate is not work:
                # Only the unpublished work directory is replaced. Its successful
                # predecessors remain intact if matching/rendering/QC fails.
                old = work.task_dir
                wb._apply_state(work, record_metadata(candidate))
                work.task_dir = candidate.task_dir
                candidate.task_dir = old
                backup = old.parent / f".v2-prior-{uuid.uuid4().hex}"
                old.rename(backup)
                try:
                    work.task_dir.rename(old)
                except BaseException:
                    backup.rename(old)
                    raise
                finally:
                    shutil.rmtree(backup, ignore_errors=True)
                work.task_dir = old
            step["state"] = "succeeded"
            succeeded += 1
        except Exception as exc:
            step["state"] = "failed"
            step["error"] = exc.code if isinstance(exc, ReplacementUnavailable) else "processing_failed"
            if not isinstance(exc, ReplacementUnavailable):
                # Server-side diagnosis only (sanitised); the client still sees the error code.
                import traceback
                from .storage import write_text_log
                try:
                    write_text_log(record.task_dir, f"修改步骤 {step['kind']} 失败: {type(exc).__name__}: {exc}\n"
                                   + "".join(traceback.format_exception(exc))[-4000:])
                except Exception:
                    pass
            if step["kind"] != "replace":
                write_json_atomic(record.task_dir / name, receipt)
                raise
            receipt["replace_failures"][str(step["row_id"])] = step["error"]
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            write_json_atomic(record.task_dir / name, receipt)
    if not succeeded:
        raise RevisionError("No requested change succeeded; prior revision retained")
    receipt["state"] = "succeeded"
    write_json_atomic(work.task_dir / "v2_apply_plan.json", receipt)


def create_v2_editing_router(settings: Any, manager: Any, authorize: Any, before_mutation: Any = None,
                             *, legacy_export_status: Any = None) -> Any:
    from fastapi import APIRouter
    from .workbench import create_workbench_router
    router = APIRouter()
    router.include_router(create_workbench_router(settings, manager, authorize, before_mutation, v2=True))
    router.include_router(create_v2_export_router(settings, manager, authorize,
                                                 legacy_status=legacy_export_status))
    return router


def create_v2_export_router(settings: Any, manager: Any, authorize: Any, *, legacy_status: Any = None) -> Any:
    """Plural submission is independent of the legacy singular export route.

    POST /exports -> 202 {export_id,...}; GET /exports/{id}; GET
    /exports/{id}/file (Range supported); DELETE /exports/{id} cancels/drains.
    Activity is registered in Studio's existing global lifecycle inventory.
    """
    import inspect
    from dataclasses import asdict
    from fastapi import APIRouter
    from fastapi.responses import FileResponse
    from . import studio
    from .publication import require_publication, publication_gate
    from .revisions import local_file
    from .pipeline import _run_blocking_until_complete
    from .studio_render import V2ExportOptions, v2_export_budget, render_v2_export, RenderError
    from .task_operations import drain_task, legacy_task_busy, task_operation_busy

    router = APIRouter(prefix="/api/tasks/{task_id}", tags=["v2-exports"])

    async def access(request: Request, task_id: str, write: bool = False) -> Any:
        # Host's existing Studio exception allows our OWN reservation through
        # reauthorization, while every ordinary mutation still sees busy.
        scope = dict(request.scope)
        scope["path"] = f"/api/tasks/{task_id}/studio/export"
        scope["raw_path"] = scope["path"].encode("ascii")
        result = authorize(Request(scope, request.receive), task_id, write=write)
        record = await result if inspect.isawaitable(result) else result
        if record is None or record.task_id != task_id:
            raise HTTPException(403, "Task authorization changed")
        return record

    def unresolved_summary(record: Any) -> dict[str, Any]:
        """What is still unresolved right now: publication checks (errors and unconfirmed warnings) and pipeline QC blockers."""
        gate = publication_gate(record)
        try:
            studio.check_qc(record.task_dir)
            qc_blocked = False
        except HTTPException:
            qc_blocked = True
        return {"blocking": gate["blocking_count"], "pending": gate["pending_count"], "qc_blockers": qc_blocked}

    def ready(record: Any, revision: int, acknowledged: bool = False) -> None:
        if (manager.get(record.task_id) is not record or record.revision != revision
                or str(getattr(record.status, "value", record.status)) != "done"
                or (record.background is not None and not record.background.done())
                or legacy_task_busy(record.task_dir) or task_operation_busy(record.task_dir)
                or (record.task_dir / "v2_publish_journal.json").exists()):
            raise HTTPException(409, "Task is stale, busy or requires recovery")
        if not getattr(record, "mode_contract", False):
            raise HTTPException(422, "v2 export requires a production-mode task")
        if acknowledged:
            # The user explicitly accepted the unresolved checks for this export. Every other gate
            # (task state, revision, busy, generated-media disclosure, source/output binding) still applies.
            return
        studio.check_qc(record.task_dir)
        require_publication(record)

    def job_path(record: Any, identity: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise HTTPException(404, "Export not found")
        return local_file(record.task_dir, f"v2_exports/{identity}/job.json", exists=False)

    def load(record: Any, identity: str) -> dict[str, Any]:
        path = job_path(record, identity)
        try:
            job = read_json(path.parent, "job.json")
        except RevisionError as exc:
            raise HTTPException(404, "Export not found") from exc
        if job["state"] in {"queued", "running"} and str(path) not in studio._RUNNING:
            return {**job, "state": "interrupted", "error": "not_resumed", "output_id": None}
        return job

    def public_job(job: dict[str, Any]) -> dict[str, Any]:
        # Hashes, publication bindings, paths and source metadata are private.
        # `id` identifies the legacy Studio schema in the client. V2 must keep
        # only export_id on POST, GET and DELETE, alongside its fmt options.
        result = {key: job[key] for key in ("export_id", "revision", "state", "output_id", "options", "error", "cleanup_pending", "unresolved") if key in job}
        if job.get("state") == "succeeded":
            result["result"] = {key: job["result"][key] for key in ("bytes", "duration", "frame_seconds", "audio") if key in job["result"]}
        return result

    @router.post("/exports", status_code=202)
    async def export(request: Request, task_id: str) -> Any:
        record = await access(request, task_id, True)
        payload = await parse_request(request, V2ExportOptions)
        if await access(request, task_id, True) is not record:
            raise HTTPException(403, "Task authorization changed")
        acknowledged = payload.acknowledge_unresolved
        ready(record, payload.expected_revision, acknowledged)
        # Recorded with the job so the client can say honestly that this file was exported with open checks.
        unresolved = unresolved_summary(record) if acknowledged else None
        if getattr(manager, "_draining", False):
            raise HTTPException(503, "Server is draining")
        root = record.task_dir.resolve()
        key = str(root)
        if key in studio._BUSY:
            raise HTTPException(409, "Task export already active")
        if len(studio._BUSY) >= 2:
            raise HTTPException(429, "Export capacity reached")
        folder = local_file(root, "v2_exports", exists=False)
        if folder.exists() and len(list(folder.iterdir())) >= 20:
            raise HTTPException(429, "v2 export retention limit reached")
        timings = read_json(root, "timings.json")
        try:
            duration = float(timings[-1]["end"])
            budget = v2_export_budget(duration, payload, settings)
        except (ValueError, IndexError, KeyError) as exc:
            raise HTTPException(422, "Invalid export timeline or current frame") from exc
        if studio.requires_disclosure(root) and payload.fmt in {"mp3", "srt"}:
            raise HTTPException(422, "This format would remove required AI disclosure")
        reserve = int(getattr(settings, "minimum_free_disk_bytes", 0))
        if shutil.disk_usage(root).free < reserve + sum(studio._DISK_RESERVATIONS.values()) + budget.reservation_bytes:
            raise HTTPException(507, "Insufficient reserved export disk")
        identity = uuid.uuid4().hex
        path = job_path(record, identity)
        job_key = str(path)
        work = path.parent / "media"
        owner = asyncio.current_task()
        assert owner is not None
        studio._BUSY.add(key)
        studio._DISK_RESERVATIONS[key] = budget.reservation_bytes
        studio._PREPARING[job_key] = owner
        admitted = False
        reservation = None
        leased = False

        async def release() -> None:
            try:
                if leased:
                    await drain_task(asyncio.create_task(reservation.__aexit__(None, None, None)))
            finally:
                studio._PREPARING.pop(job_key, None)
                studio._RUNNING.pop(job_key, None)
                studio._DISK_RESERVATIONS.pop(key, None)
                studio._BUSY.discard(key)

        def stable() -> None:
            ready(record, payload.expected_revision, acknowledged)
            if (root.stat().st_dev, root.stat().st_ino) != root_stamp or publication_gate(record) != publication:
                raise HTTPException(409, "Export source or publication changed")

        try:
            inputs = studio.mode_export_inputs(root)
            root_stamp = (root.stat().st_dev, root.stat().st_ino)
            publication = publication_gate(record)
            guard = getattr(manager, "_upload_capacity_guard", None)
            if guard is not None:
                reservation = guard.reserve(max(1, math.ceil(budget.reservation_bytes / settings.task_disk_reservation_multiplier)))
                async def acquire() -> None:
                    nonlocal leased
                    await reservation.__aenter__()
                    leased = True
                await drain_task(asyncio.create_task(acquire()))
            hashes = await _run_blocking_until_complete(partial(studio.mode_export_hashes, inputs))
            if await access(request, task_id, True) is not record:
                raise HTTPException(403, "Task authorization changed")
            stable()
            if getattr(manager, "_draining", False):
                raise HTTPException(503, "Server is draining")
            work.mkdir(parents=True, exist_ok=False)
            job = {"export_id": identity, "revision": record.revision, "state": "queued", "output_id": None,
                   "options": payload.model_dump(), "budget": asdict(budget), "source_sha256": hashes,
                   "publication": publication, "error": None,
                   **({"unresolved": unresolved} if acknowledged and (unresolved["blocking"] or unresolved["pending"] or unresolved["qc_blockers"]) else {})}
            write_json_atomic(path, job)

            async def run() -> None:
                try:
                    job["state"] = "running"
                    write_json_atomic(path, job)
                    result = await render_v2_export(root, work, payload, budget)
                    if await access(request, task_id) is not record:
                        raise HTTPException(403, "Task authorization changed")
                    stable()
                    actual = await _run_blocking_until_complete(partial(studio.mode_export_hashes, inputs))
                    if actual != hashes:
                        raise RenderError("Source changed during export")
                    if await access(request, task_id) is not record:
                        raise HTTPException(403, "Task authorization changed")
                    stable()
                    job.update(state="succeeded", output_id=identity, result=result)
                    write_json_atomic(path, job)
                except BaseException as exc:
                    job.update(state="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                               output_id=None, error="export_not_published")
                    # Never expose a partial result even if its cleanup fails.
                    try:
                        shutil.rmtree(work)
                    except OSError:
                        job["cleanup_pending"] = True
                    write_json_atomic(path, job)
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                finally:
                    await release()

            task = asyncio.create_task(run(), name=f"v2-export-{identity}")
            studio._RUNNING[job_key] = task
            admitted = True
            # Ensure cancellation before first media instruction still owns a
            # running finally, including cancellation of this request itself.
            try:
                await asyncio.sleep(0)
            except BaseException:
                task.cancel()
                try:
                    await drain_task(task)
                except asyncio.CancelledError:
                    pass
                raise
            return public_job(job)
        finally:
            studio._PREPARING.pop(job_key, None)
            if not admitted:
                try:
                    if path.parent.exists():
                        shutil.rmtree(path.parent)
                finally:
                    await release()

    @router.get("/exports/{export_id}")
    async def status(request: Request, task_id: str, export_id: str) -> Any:
        record = await access(request, task_id)
        # Dispatch by known identity, never by catching errors from an existing
        # v2 receipt (a damaged v2 job must not fall through to legacy).
        if job_path(record, export_id).exists():
            return public_job(load(record, export_id))
        if legacy_status is not None:
            return await legacy_status(request, task_id, export_id)
        raise HTTPException(404, "Export not found")

    @router.delete("/exports/{export_id}")
    async def cancel(request: Request, task_id: str, export_id: str) -> Any:
        record = await access(request, task_id, True)
        job = load(record, export_id)
        task = studio._RUNNING.get(str(job_path(record, export_id)))
        if task is not None:
            task.cancel()
            try:
                await drain_task(task)
            except asyncio.CancelledError:
                if (caller := asyncio.current_task()) is not None and caller.cancelling():
                    raise
        if await access(request, task_id, True) is not record:
            raise HTTPException(403, "Task authorization changed")
        return public_job(load(record, export_id) if task is not None else job)

    @router.get("/exports/{export_id}/file")
    async def download(request: Request, task_id: str, export_id: str) -> Any:
        record = await access(request, task_id)
        job = load(record, export_id)
        acknowledged = job.get("options", {}).get("acknowledge_unresolved") is True
        ready(record, job["revision"], acknowledged)
        if job["state"] != "succeeded" or job["output_id"] != export_id:
            raise HTTPException(409, "Export not published")
        path = job_path(record, export_id).parent / "media"
        output = local_file(path, job["result"]["file"])
        actual = await _run_blocking_until_complete(partial(studio.mode_export_hashes, {
            **studio.mode_export_inputs(record.task_dir), "output": output}))
        if await access(request, task_id) is not record:
            raise HTTPException(403, "Task authorization changed")
        ready(record, job["revision"], acknowledged)
        if (actual.pop("output") != job["result"]["sha256"] or actual != job["source_sha256"]
                or output.stat().st_size != job["result"]["bytes"] or publication_gate(record) != job["publication"]):
            raise HTTPException(409, "Export source/output binding changed")
        media = {"mp4": "video/mp4", "gif": "image/gif", "png": "image/png", "mp3": "audio/mpeg", "srt": "application/x-subrip"}
        return FileResponse(output, media_type=media[job["options"]["fmt"]], filename=output.name,
                            headers={"Cache-Control": "private, no-store"})

    return router