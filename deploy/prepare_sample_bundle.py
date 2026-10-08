"""Offline preparation, NOT sample installation, rendering or human approval.

CLI requires --source ABSOLUTE_DIRECTORY --output FRESH_ABSOLUTE_DIRECTORY
--rights-reviewed. The caller attests distribution rights were reviewed; this
tool cannot verify that assertion, editorial quality, consent or privacy.

Dedicated input directory: reviewed-sample.json, report.json, final.mp4 and
optionally timings.json. No directory discovery, task lookup or fallback.
The descriptor is {"schema_version":1,"task_id":"<32 lowercase hex>",
"title":"<1..80 chars>","files":{"report.json":{"sha256":"<64 hex>",
"bytes":123},"final.mp4":{"sha256":"<64 hex>","bytes":456}}}.

Report profile: task_id, rows, optional mode. Each row requires sentence_id,
sentence, shot_id (nullable), thumb_url (null), description, duration,
confidence, is_fallback, explicit audio_kind (tts or sync); optional kind,
spoken_text, visual_beats. Original mode requires sync audio on every row.
Each beat has beat_id,text,shot_id,thumb_url (empty string),description,
confidence and optional requires_entity_coverage. Unknown fields are rejected,
not silently stripped: prepare a separately reviewed public report, not a raw
task export. Quality/provider/source metadata is intentionally unsupported.
Optional timings are [{"sentence_id":0,"start":0,"end":1}], in row order.
Without timings the runtime does not invent seek clocks from row durations.

This conservative stdlib profile fits ReportResponse and _safe_report in
backend/v2_editing.packaged_sample; no backend/config/provider import occurs.
Optional walkthrough (how the sample was made, shown step by step):
walkthrough.json {"schema_version":1,"script":"...","mode":"voiceover",
"settings":[{"label":"..","value":".."}],"sources":[{"file":"sources/01.mp4",
"thumb":"sources/01.jpg","name":"01-xx.mp4","duration":8.0,"used_by":[0]}]}
plus every sources/NN.{mp4,jpg} it names, and optional thumbs/<sentence_id>.jpg.

Output: registry.json plus default/{report.json,final.mp4[,timings.json,...]}.
Registry uses the runtime's SHA-string map, not descriptor byte-count objects.
Copying is streamed and reverified. Invalid input creates no output; copy
failure retains an incomplete fresh directory WITHOUT a usable registry. Do
not reuse that directory. Run only with quiescent, operator-owned directories;
this is not an OS sandbox against concurrent hostile filesystem mutation.
An MP4 ftyp signature is checked, NOT ffprobe, decoding or playback validity.
No auto-install into backend/assets/samples, network, subprocess or dependency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any

MAX_JSON = 8 * 1024**2
MAX_VIDEO = 512 * 1024**2
MAX_IMAGE = 5 * 1024**2
MEDIA_NAME = re.compile(r"(?:sources/[0-9]{2}\.(?:mp4|jpg)|thumbs/[0-9]{1,4}\.jpg)")
CHUNK = 1024**2
DESCRIPTOR = "reviewed-sample.json"
ROOT = Path(__file__).absolute().parent.parent


class BundleError(ValueError):
    """Safe, content-free validation failure."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise BundleError(code)


def checked_path(path: Path, *, directory: bool = False) -> os.stat_result:
    # Check ancestors before reading anything; Windows junctions are reparse
    # points even on Python versions without Path.is_junction().
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400, "linked_path")
        if part != path or directory:
            require(stat.S_ISDIR(info.st_mode), "not_directory")
    info = path.lstat()
    if not directory:
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "not_single_regular_file")
    return info


def absolute_local(value: Path) -> Path:
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "absolute_local_path_required")
    require(not str(path).startswith(("\\\\", "//")), "network_or_device_path")
    for part in path.parts[1:]:
        require(not any(c in part for c in '<>:"|?*') and not any(ord(c) < 32 for c in part)
                and not part.endswith((".", " ")), "noncanonical_path")
    return path


def path_boundaries(source: Path, output: Path, root: Path) -> None:
    for forbidden in (root / "data", root / "eval_sample", root / "backend/assets/samples"):
        require(not source.is_relative_to(forbidden) and not output.is_relative_to(forbidden),
                "runtime_or_user_storage_forbidden")
    require(not source.is_relative_to(output) and not output.is_relative_to(source), "overlapping_paths")


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def consume(path: Path, limit: int, *, target: Path | None = None,
            capture: bool = False) -> tuple[dict[str, Any], bytes]:
    before = checked_path(path)
    require(0 < before.st_size <= limit, "file_size_limit")
    digest, count, collected = hashlib.sha256(), 0, bytearray()
    with path.open("rb") as stream:
        require(signature(os.fstat(stream.fileno())) == signature(before), "source_changed")
        # No replace, overwrite, link or copy2 metadata preservation.
        output = target.open("xb") if target is not None else None
        try:
            while block := stream.read(CHUNK):
                count += len(block)
                require(count <= limit, "file_size_limit")
                digest.update(block)
                if capture:
                    collected.extend(block)
                elif len(collected) < 32:
                    collected.extend(block[:32 - len(collected)])
                if output is not None:
                    output.write(block)
        finally:
            if output is not None:
                output.close()
        require(signature(os.fstat(stream.fileno())) == signature(before), "source_changed")
    require(signature(checked_path(path)) == signature(before) and count == before.st_size,
            "source_changed")
    return {"sha256": digest.hexdigest(), "bytes": count}, bytes(collected)


def parse_json(payload: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def nonfinite(_value: str) -> None:
        raise BundleError("nonfinite_json")

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BundleError("invalid_json") from exc


def fields(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    require(isinstance(value, dict) and required <= value.keys()
            and value.keys() <= required | (optional or set()), "unsupported_fields")


def text(value: Any, maximum: int = 8000, *, blank: bool = True) -> None:
    require(isinstance(value, str) and len(value) <= maximum
            and (blank or bool(value.strip()))
            and not any(ord(c) < 32 and c not in "\n\r\t" for c in value), "invalid_text")


def integer(value: Any) -> None:
    require(type(value) is int and 0 <= value <= 100_000_000, "invalid_id")


def number(value: Any, low: float, high: float) -> None:
    require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
            "invalid_number")


def validate_report(report: Any, task_id: str) -> list[int]:
    fields(report, {"task_id", "rows"}, {"mode"})
    require(report["task_id"] == task_id, "report_identity")
    require(report.get("mode", "voiceover") in ("voiceover", "mixed", "original"), "report_mode")
    require(isinstance(report["rows"], list) and 1 <= len(report["rows"]) <= 200, "report_rows")
    ids: list[int] = []
    for row in report["rows"]:
        fields(row, {"sentence_id", "sentence", "shot_id", "thumb_url", "description",
                   "duration", "confidence", "is_fallback", "audio_kind"},
               {"kind", "spoken_text", "visual_beats"})
        integer(row["sentence_id"])
        require(row["sentence_id"] not in ids, "duplicate_row")
        ids.append(row["sentence_id"])
        text(row["sentence"], blank=False)
        text(row["description"])
        if row.get("spoken_text") is not None:
            text(row["spoken_text"])
        if row["shot_id"] is not None:
            integer(row["shot_id"])
        require(row["thumb_url"] is None, "public_thumbnail_required")
        number(row["duration"], 0.000001, 600)
        number(row["confidence"], 0, 1)
        require(type(row["is_fallback"]) is bool, "invalid_boolean")
        require(row.get("kind", "narration") in ("narration", "quote")
            and row["audio_kind"] in ("tts", "sync"), "row_kind")
        require(report.get("mode") != "original" or row["audio_kind"] == "sync",
            "original_audio_contradiction")
        beats = row.get("visual_beats", [])
        require(isinstance(beats, list) and len(beats) <= 200, "report_beats")
        seen: set[int] = set()
        for beat in beats:
            fields(beat, {"beat_id", "text", "shot_id", "thumb_url", "description", "confidence"},
                   {"requires_entity_coverage"})
            integer(beat["beat_id"])
            integer(beat["shot_id"])
            require(beat["beat_id"] not in seen, "duplicate_beat")
            seen.add(beat["beat_id"])
            text(beat["text"])
            text(beat["description"])
            require(beat["thumb_url"] == "", "public_thumbnail_required")
            require(type(beat.get("requires_entity_coverage", False)) is bool, "invalid_boolean")
            number(beat["confidence"], 0, 1)
    return ids


def file_limit(name: str) -> int:
    return MAX_VIDEO if name.endswith(".mp4") else MAX_IMAGE if name.endswith(".jpg") else MAX_JSON


def validate_walkthrough(value: Any, inventory: dict[str, Any], ids: list[int]) -> None:
    fields(value, {"schema_version", "script", "mode", "sources"}, {"settings"})
    require(type(value["schema_version"]) is int and value["schema_version"] == 1, "schema_version")
    text(value["script"], 4000, blank=False)
    require(value["mode"] in ("voiceover", "mixed", "original"), "walkthrough_mode")
    settings = value.get("settings", [])
    require(isinstance(settings, list) and len(settings) <= 12, "walkthrough_settings")
    for item in settings:
        fields(item, {"label", "value"})
        text(item["label"], 20, blank=False)
        text(item["value"], 80, blank=False)
    sources = value["sources"]
    require(isinstance(sources, list) and 1 <= len(sources) <= 20, "walkthrough_sources")
    for index, item in enumerate(sources, 1):
        fields(item, {"file", "thumb", "name", "duration"}, {"used_by"})
        require(item["file"] == f"sources/{index:02d}.mp4" and item["thumb"] == f"sources/{index:02d}.jpg"
                and item["file"] in inventory and item["thumb"] in inventory, "walkthrough_source_files")
        text(item["name"], 80, blank=False)
        number(item["duration"], 0.000001, 3600)
        used = item.get("used_by", [])
        require(isinstance(used, list) and all(type(sid) is int and sid in ids for sid in used), "walkthrough_used_by")
    named = {name for item in sources for name in (item["file"], item["thumb"])}
    require({name for name in inventory if name.startswith("sources/")} == named, "unreferenced_source")


def prepare(source: Path, output: Path, *, rights_reviewed: bool = False) -> dict[str, Any]:
    require(rights_reviewed is True, "rights_review_required")
    source, output = absolute_local(source), absolute_local(output)
    path_boundaries(source, output, ROOT)
    checked_path(source, directory=True)
    checked_path(output.parent, directory=True)
    # Only resolve AFTER rejecting symlink/reparse ancestors. Windows 8.3
    # ancestor names are aliases without links; lexical checks alone miss them.
    # Resolve existing paths strictly, append only the fresh output leaf, and
    # repeat BOTH boundaries before reading content or creating anything.
    source = absolute_local(source.resolve(strict=True))
    output = absolute_local(output.parent.resolve(strict=True) / output.name)
    path_boundaries(source, output, ROOT.resolve(strict=True))
    require(not os.path.lexists(output), "output_not_fresh")
    descriptor_hash, raw = consume(source / DESCRIPTOR, MAX_JSON, capture=True)
    descriptor = parse_json(raw)
    fields(descriptor, {"schema_version", "task_id", "title", "files"})
    require(type(descriptor["schema_version"]) is int and descriptor["schema_version"] == 1, "schema_version")
    identity = descriptor["task_id"]
    require(isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{32}", identity) is not None, "task_identity")
    text(descriptor["title"], 80, blank=False)
    inventory = descriptor["files"]
    # Fixed canonical relative names reject traversal, ADS, case aliases and
    # destination collisions without ever opening those supplied names.
    require(isinstance(inventory, dict) and len(inventory) <= 100, "unsupported_fields")
    fields(inventory, {"report.json", "final.mp4"}, {"timings.json", "walkthrough.json"}
           | {name for name in inventory if MEDIA_NAME.fullmatch(name)})
    metadata: dict[str, Any] = {}
    for name, expected in inventory.items():
        fields(expected, {"sha256", "bytes"})
        require(isinstance(expected["sha256"], str)
                and re.fullmatch(r"[a-f0-9]{64}", expected["sha256"]) is not None, "invalid_digest")
        limit = file_limit(name)
        require(type(expected["bytes"]) is int and 0 < expected["bytes"] <= limit, "declared_size_limit")
        actual, payload = consume(source / name, limit, capture=name.endswith(".json"))
        require(actual == expected, "file_integrity")
        if name.endswith(".jpg"):
            require(payload[:3] == b"\xff\xd8\xff", "jpeg_header")
        elif name.endswith(".mp4"):
            box_size = int.from_bytes(payload[:4], "big")
            require(len(payload) >= 16 and payload[4:8] == b"ftyp"
                    and 16 <= box_size <= actual["bytes"], "mp4_header")
        else:
            metadata[name] = parse_json(payload)
    ids = validate_report(metadata["report.json"], identity)
    if "timings.json" in metadata:
        timings = metadata["timings.json"]
        require(isinstance(timings, list) and len(timings) == len(ids), "timing_rows")
        previous = 0.0
        for sid, timing in zip(ids, timings):
            fields(timing, {"sentence_id", "start", "end"})
            integer(timing["sentence_id"])
            require(timing["sentence_id"] == sid, "timing_identity")
            number(timing["start"], previous, 600)
            number(timing["end"], 0, 600)
            require(timing["end"] > timing["start"], "timing_order")
            previous = timing["end"]
    for name in inventory:
        if name.startswith("thumbs/"):
            require(int(name[len("thumbs/"):-len(".jpg")]) in ids, "thumbnail_identity")
    if "walkthrough.json" in metadata:
        validate_walkthrough(metadata["walkthrough.json"], inventory, ids)
    else:
        require(not any(name.startswith("sources/") for name in inventory), "unreferenced_source")
    # All validation precedes creation/copy; source bytes are checked AGAIN
    # during copying, and destination bytes before publishing registry last.
    output.mkdir()
    folder = output / "default"
    folder.mkdir()
    for name, expected in inventory.items():
        limit = file_limit(name)
        (folder / name).parent.mkdir(exist_ok=True)
        actual, _ = consume(source / name, limit, target=folder / name)
        require(actual == expected, "copy_source_changed")
        copied, _ = consume(folder / name, limit)
        require(copied == expected, "copy_integrity")
    require(consume(source / DESCRIPTOR, MAX_JSON)[0] == descriptor_hash, "descriptor_changed")
    registry = {"default": {"directory": "default", "task_id": identity, "title": descriptor["title"],
                            "files": {name: item["sha256"] for name, item in inventory.items()}}}
    with (output / "registry.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(registry, stream, ensure_ascii=True, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return {"status": "prepared_not_installed", "files": inventory, "file_count": len(inventory),
            "rights_review": "caller_attested_not_verified", "report_profile": "minimal_public_v1",
            "media_check": "ftyp_header_only", "ffprobe_performed": False,
            "quality_verified": False, "privacy_verified": False, "runtime_render_verified": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rights-reviewed", required=True, action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = prepare(args.source, args.output, rights_reviewed=args.rights_reviewed)
    except (OSError, ValueError, OverflowError, RecursionError) as exc:
        # Do not echo source paths, report text or OS exception payloads.
        print(json.dumps({"status": "rejected", "code": str(exc) if isinstance(exc, BundleError) else "io_or_json_error"}))
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())