"""Credential-free, immutable workbench snapshots. No TaskManager import cycle.

Only explicitly listed artifacts and their local audio/segment dependencies are
copied. Raw uploads, logs, provider caches and task_state.json are never archived.
Copies (not hard links) are intentional: legacy renderers overwrite audio in place.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .models import SentenceInput, Speaker
from .storage import write_json_atomic


ARTIFACTS = frozenset({
    "final.mp4", "video_only.mp4", "report.json", "quality_report.json",
    "timings.json", "subs.ass", "subtitle_manifest.json", "narration.m4a",
    "narration_profile.json", "tts_manifest.json", "pronunciation_plan.json",
    "match_plan.json", "edl.json", "segment_manifest.json", "sentences.json",
    "script_structure.json", "script_segmented.txt", "shots_annotated.json",
    "source_timings.json", "source_edl.json", "source_segment_manifest.json",
    "source_match_plan.json", "source_narration_profile.json",
    "graphics.ass", "contextual_overlays.json", "generated_media_disclosure.json",
    "music_selection.json", "remix_state.json", "shot_replacement_state.json",
    "shot_replacement_history.json", "workbench_audio.json",
    "student_narration.json",
    "production_mode.json", "pretranscripts.json", "broll_pool.json",
    "jumpcuts.json", "lower_thirds.json",
    "v2_apply_plan.json",
})
STATE_FIELDS = (
    "script", "revision", "current_stage", "stage_name", "progress", "message",
    "processing_started_at", "processing_completed_at", "total_elapsed_seconds",
    "mode", "mode_contract", "upload_ids", "speakers", "sentences", "quality_gate_mode",
)
MAX_SNAPSHOT_BYTES = 8 * 1024**3
MAX_REVISIONS = 100


class RevisionError(ValueError):
    """Missing, unsafe or oversized revision artifacts."""


def _snapshot_io_path(path: Path) -> Path:
    """Use Win32 extended paths for nested snapshot I/O, not persisted names.

    A workbench copy adds two UUID directories and atomic JSON adds another
    UUID suffix. A valid task path can therefore exceed MAX_PATH internally.
    Resolve first; this is not permission-error recovery or a rename retry.
    """
    path = path.resolve()
    if os.name != "nt" or str(path).startswith("\\\\?\\"):
        return path
    value = str(path)
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def local_file(root: Path, relative: object, *, exists: bool = True) -> Path:
    """Reject absolute paths, traversal, Windows ADS and symlink/junction escape."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise RevisionError("Unsafe artifact path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(p in {".", ".."} for p in relative.split("/")):
        raise RevisionError("Unsafe artifact path")
    if any(ord(c) < 32 for c in relative):
        raise RevisionError("Unsafe artifact path")
    root = root.resolve()
    path = root.joinpath(*parts.parts)
    cursor = path
    while cursor != root:
        if cursor.is_symlink() or (cursor.exists() and cursor.resolve() != cursor.absolute()):
            raise RevisionError("Linked artifact paths are not allowed")
        cursor = cursor.parent
    if not path.resolve().is_relative_to(root) or (exists and not path.is_file()):
        raise RevisionError("Artifact missing or outside task")
    return path


def read_json(root: Path, name: str) -> Any:
    path = local_file(root, name)
    if path.stat().st_size > 16 * 1024**2:
        raise RevisionError("Metadata exceeds limit")
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_files(root: Path) -> list[str]:
    names = {name for name in ARTIFACTS if (root / name).exists()}
    if "production_mode.json" in names:
        manifest = read_json(root, "production_mode.json")
        if not isinstance(manifest, dict):
            raise RevisionError("Invalid production-mode metadata")
        cache = manifest.get("audio_cache", {})
        if not isinstance(cache, dict):
            raise RevisionError("Invalid mode audio cache")
        for entry in cache.values():
            if not isinstance(entry, dict):
                raise RevisionError("Invalid mode audio cache entry")
            for key in ("timing", "raw_timing"):
                if key in entry:
                    if not isinstance(entry[key], dict):
                        raise RevisionError("Invalid mode audio timing")
                    audio = entry[key].get("audio_path")
                    if not isinstance(audio, str) or not audio.startswith(("tts/", "recordings/", "workbench_audio/", "student_audio/")):
                        raise RevisionError("Unexpected mode audio dependency location")
                    names.add(audio)
        # New quote takes create new task-owned thumbnail IDs. Publish/snapshot
        # those actual dependencies, never the manifest's raw/source clocks.
        if "shots_annotated.json" in names:
            for shot in read_json(root, "shots_annotated.json"):
                thumb = shot.get("thumb_path") or f"thumbs/shot_{shot['shot_id']}.jpg"
                if not isinstance(thumb, str) or not thumb.startswith("thumbs/") or not thumb.endswith(".jpg"):
                    raise RevisionError("Unexpected mode thumbnail location")
                if shot.get("thumb_path") or (root / thumb).exists():
                    names.add(thumb)
    for name in ("timings.json", "source_timings.json"):
        if name in names:
            for timing in read_json(root, name):
                audio = timing["audio_path"]
                if not audio.startswith(("tts/", "recordings/", "workbench_audio/", "student_audio/")):
                    raise RevisionError("Unexpected sentence audio location")
                names.add(audio)
    for name in ("segment_manifest.json", "source_segment_manifest.json"):
        if name in names:
            for item in read_json(root, name):
                for segment in item["segments"]:
                    if not segment.startswith("segments/"):
                        raise RevisionError("Unexpected segment location")
                    names.add(segment)
    total = 0
    for name in names:
        total += local_file(root, name).stat().st_size
        if total > MAX_SNAPSHOT_BYTES:
            raise RevisionError("Revision exceeds 8 GiB workbench limit")
    return sorted(names)


def copy_files(source: Path, destination: Path, names: list[str]) -> None:
    source, destination = _snapshot_io_path(source), _snapshot_io_path(destination)
    total = sum(local_file(source, name).stat().st_size for name in names)
    if total > MAX_SNAPSHOT_BYTES:
        raise RevisionError("Workspace exceeds 8 GiB limit")
    destination.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination).free < total + 64 * 1024**2:
        raise RevisionError("Insufficient workbench disk space")
    for name in names:
        output = local_file(destination, name, exists=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_file(source, name), output)


def record_metadata(record: Any) -> dict[str, Any]:
    state: dict[str, Any] = {}
    defaults: dict[str, Any] = {
        "mode": "voiceover", "mode_contract": False, "upload_ids": [],
        "speakers": [], "sentences": [], "quality_gate_mode": None,
    }
    for key in STATE_FIELDS:
        value = getattr(record, key, defaults.get(key))
        if key == "speakers":
            value = [Speaker.model_validate(item).model_dump(mode="json") for item in value]
        elif key == "sentences":
            value = [SentenceInput.model_validate(item).model_dump(mode="json") for item in value]
        elif key == "upload_ids":
            value = list(value)
        state[key] = value.isoformat() if isinstance(value, datetime) else value
    state["preferences"] = record.preferences.model_dump(mode="json")
    state["stages"] = [stage.model_dump(mode="json") for stage in record.stages]
    # upload_ids are identities, not upload capabilities. Never serialize uploads,
    # record.__dict__, upload_tokens, access_token or access_token_hash. Publication
    # acknowledgements deliberately are NOT revision artifacts or restored state.
    return state


def revision_dir(task_dir: Path, revision: object) -> Path:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise RevisionError("Invalid revision")
    path = local_file(task_dir, f"revisions/r{revision}/revision.json")
    return path.parent


def snapshot_revision(record: Any, label: str = "Completed") -> dict[str, Any]:
    """Idempotent success hook; raises on unsafe/incomplete data, never calls providers.

    Invoke after setting the successful revision/script/preferences. Offload with
    cancellation shielding if called from an async hook (large videos are copied).
    The manager remains responsible for persisting its credential-bearing state.
    """
    root = _snapshot_io_path(Path(record.task_dir))
    revision = record.revision
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise RevisionError("Invalid revision")
    target = local_file(root, f"revisions/r{revision}", exists=False)
    if target.exists():
        return read_json(target, "revision.json")
    for required in ("final.mp4", "report.json", "timings.json", "edl.json", "match_plan.json"):
        local_file(root, required)
    versions_root = local_file(root, "revisions", exists=False)
    versions_root.mkdir(exist_ok=True)
    if len(list(versions_root.glob("r*/revision.json"))) >= MAX_REVISIONS:
        raise RevisionError("Revision limit reached (100); duplicate/export before continuing")
    temporary = versions_root / f".snapshot-{uuid.uuid4().hex}"
    try:
        names = artifact_files(root)
        copy_files(root, temporary, names)
        metadata: dict[str, Any] = {
            "schema_version": 1, "revision": revision, "label": label[:120],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": record_metadata(record), "files": names,
        }
        write_json_atomic(temporary / "revision.json", metadata)
        temporary.rename(target)
        return metadata
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def version_summaries(task_dir: Path) -> list[dict[str, Any]]:
    root = local_file(task_dir, "revisions", exists=False)
    if not root.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.name.startswith("r") or not directory.name[1:].isdigit():
            continue
        revision = int(directory.name[1:])
        metadata = read_json(revision_dir(task_dir, revision), "revision.json")
        summaries.append({key: metadata[key] for key in ("revision", "label", "created_at")})
    return sorted(summaries, key=lambda item: item["revision"], reverse=True)


def publish_artifacts(staged: Path, task_dir: Path, persist: Any, *, durable: bool = False) -> None:
    """Exception-atomic publication, including persistence failure. No await points.

    Existing API reads must run on the owning event loop. This is not a power-loss
    journal; version endpoints always read immutable directories instead.
    """
    staged, task_dir = _snapshot_io_path(staged), _snapshot_io_path(task_dir)
    names = artifact_files(staged)
    current = artifact_files(task_dir)
    affected = sorted(set(names) | (set(current) & ARTIFACTS))
    transaction = task_dir / f".publish-{uuid.uuid4().hex}"
    incoming, backup = transaction / "incoming", transaction / "backup"
    moved: list[str] = []
    installed: list[str] = []
    state_path = task_dir / "task_state.json"
    state_bytes = state_path.read_bytes() if state_path.exists() else None
    journal = task_dir / "v2_publish_journal.json"
    if durable and journal.exists():
        raise RevisionError("Interrupted publication requires recovery")
    clean = False
    try:
        copy_files(staged, incoming, names)
        if durable:
            if state_bytes is not None:
                write_json_atomic(transaction / "state.json", json.loads(state_bytes))
            write_json_atomic(journal, {
                "schema_version": 1, "transaction": transaction.name, "phase": "installing",
                "names": names, "affected": affected, "previous": current,
                "had_state": state_bytes is not None,
                "new_revision": read_json(staged, "v2_apply_plan.json").get("revision")
                    if (staged / "v2_apply_plan.json").is_file() else None,
            })
        for name in affected:
            target = local_file(task_dir, name, exists=False)
            if target.exists():
                old = local_file(backup, name, exists=False)
                old.parent.mkdir(parents=True, exist_ok=True)
                target.replace(old)
                moved.append(name)
            if name in names:
                target.parent.mkdir(parents=True, exist_ok=True)
                local_file(incoming, name).replace(target)
                installed.append(name)
        persist()
        if durable:
            receipt = read_json(task_dir, journal.name)
            receipt["phase"] = "committed"
            write_json_atomic(journal, receipt)
        clean = True
    except BaseException:
        for name in reversed(installed):
            local_file(task_dir, name).unlink()
        for name in reversed(moved):
            local_file(backup, name).replace(local_file(task_dir, name, exists=False))
        if state_bytes is not None:
            state_path.write_bytes(state_bytes)
        else:
            state_path.unlink(missing_ok=True)
        clean = True
        raise
    finally:
        if durable and clean:
            journal.unlink(missing_ok=True)
        if not durable or clean:
            shutil.rmtree(transaction, ignore_errors=True)


def recover_v2_publication(task_dir: Path) -> bool:
    """Startup-only, BEFORE reads/admissions: rollback an interrupted v2 install.

    Never resumes providers. A failed recovery leaves the journal fail-closed.
    The host must reload TaskRecord from the restored task_state afterwards.
    """
    journal = local_file(task_dir, "v2_publish_journal.json", exists=False)
    if not journal.exists():
        return False
    value = read_json(task_dir, journal.name)
    name = value.get("transaction", "")
    if (not isinstance(name, str) or not name.startswith(".publish-")
            or len(name) != 41 or any(c not in "0123456789abcdef" for c in name[9:])
            or value.get("phase") not in {"installing", "committed"}):
        raise RevisionError("Invalid v2 publication recovery journal")
    transaction = local_file(task_dir, name, exists=False)
    if value["phase"] == "installing":
        revision = value.get("new_revision")
        if revision is not None:
            if type(revision) is not int or revision < 0:
                raise RevisionError("Invalid journal revision")
            uncommitted = local_file(task_dir, f"revisions/r{revision}", exists=False)
            if uncommitted.exists():
                shutil.rmtree(uncommitted)
        previous = set(value["previous"])
        for relative in value["affected"]:
            target = local_file(task_dir, relative, exists=False)
            backup = local_file(transaction / "backup", relative, exists=False)
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                backup.replace(target)
            elif relative not in previous:
                target.unlink(missing_ok=True)
        if value["had_state"]:
            write_json_atomic(task_dir / "task_state.json", read_json(transaction, "state.json"))
        else:
            (task_dir / "task_state.json").unlink(missing_ok=True)
    journal.unlink()
    shutil.rmtree(transaction, ignore_errors=True)
    return True