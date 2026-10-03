import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar, cast

from pydantic import BaseModel, ValidationError

from .models import (
    AnnotatedShot,
    EDLClip,
    EDLItem,
    GeneratedMediaDisclosureManifest,
    MatchPlanItem,
    SegmentManifestItem,
    SentenceTiming,
    is_generated_media_path,
)
from .storage import write_json_atomic


class ShotReplacementValidationError(RuntimeError):
    """Raised when current task artifacts cannot support a requested shot replacement."""


SHOT_REPLACEMENT_TEMP_FILES = {
    "selection": "match_plan.replacement.selection.json",
    "match_plan": "match_plan.replacement.json",
    "edl": "edl.replacement.json",
    "segment_manifest": "segment_manifest.replacement.json",
    "video_only": "video_only.replacement.mp4",
    "final": "final.replacement.mp4",
    "report": "report.replacement.json",
    "quality": "quality_report.replacement.json",
    "state": "shot_replacement_state.json.tmp",
    "history": "shot_replacement_history.json.tmp",
}
SHOT_REPLACEMENT_GENERATED_DISCLOSURE_TEMP_FILE = (
    "generated_media_disclosure.replacement.json"
)


@dataclass(frozen=True)
class ShotReplacementContext:
    match_plan: list[MatchPlanItem]
    timings: list[SentenceTiming]
    edl: list[EDLItem]
    source_edl: list[EDLItem]
    segment_manifest: list[SegmentManifestItem]
    shots: list[AnnotatedShot]
    current_item: MatchPlanItem
    timing: SentenceTiming
    excluded_shot_ids: set[int]


def load_shot_replacement_context(task_dir: Path, sentence_id: int) -> ShotReplacementContext:
    full_match_plan = _load_models(task_dir / "match_plan.json", MatchPlanItem)
    timings = _load_models(task_dir / "timings.json", SentenceTiming)
    edl = _load_models(task_dir / "edl.json", EDLItem)
    source_edl = _load_models(task_dir / "source_edl.json", EDLItem)
    segment_manifest = _load_models(task_dir / "segment_manifest.json", SegmentManifestItem)
    shots = _load_models(task_dir / "shots_annotated.json", AnnotatedShot)

    timing_ids = [timing.sentence_id for timing in timings]
    edl_ids = [item.sentence_id for item in edl]
    match_plan_by_id = {item.sentence_id: item for item in full_match_plan}
    if len(match_plan_by_id) != len(full_match_plan):
        raise ShotReplacementValidationError("当前匹配计划包含重复句子。")
    if not timing_ids or timing_ids != edl_ids:
        raise ShotReplacementValidationError("当前匹配计划、旁白时间线与 EDL 的句子顺序不一致。")
    match_plan = [match_plan_by_id[sentence_id] for sentence_id in timing_ids if sentence_id in match_plan_by_id]
    if [item.sentence_id for item in match_plan] != timing_ids:
        raise ShotReplacementValidationError("当前匹配计划、旁白时间线与 EDL 的句子顺序不一致。")

    plan_by_id = {item.sentence_id: item for item in match_plan}
    timing_by_id = {timing.sentence_id: timing for timing in timings}
    current_item = plan_by_id.get(sentence_id)
    timing = timing_by_id.get(sentence_id)
    if current_item is None or timing is None:
        raise ShotReplacementValidationError(f"当前匹配报告不包含句子 {sentence_id}。")

    if sentence_id not in {item.sentence_id for item in source_edl}:
        raise ShotReplacementValidationError(f"源 EDL 不包含句子 {sentence_id}。")
    if sentence_id not in {item.sentence_id for item in segment_manifest}:
        raise ShotReplacementValidationError(f"视频片段清单不包含句子 {sentence_id}。")

    excluded_shot_ids = {
        current_item.shot_id,
        *(beat.shot_id for beat in current_item.beat_matches),
    }
    for item in match_plan:
        if item.sentence_id == sentence_id:
            continue
        if item.sync_sound is not None:
            excluded_shot_ids.add(item.sync_sound.shot_id)
        elif item.beat_matches:
            excluded_shot_ids.update(beat.shot_id for beat in item.beat_matches)
        else:
            excluded_shot_ids.add(item.shot_id)
    excluded_shot_ids.update(
        clip.shot_id
        for item in edl
        if item.sentence_id != sentence_id
        for clip in item.clips
    )
    history_path = task_dir / "shot_replacement_history.json"
    if history_path.is_file():
        try:
            history_payload = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(history_payload, list):
                for raw_entry in cast(list[object], history_payload):
                    if not isinstance(raw_entry, dict):
                        continue
                    entry = cast(dict[str, object], raw_entry)
                    if entry.get("sentence_id") != sentence_id:
                        continue
                    previous = entry.get("previous_shot_ids")
                    if isinstance(previous, list):
                        excluded_shot_ids.update(
                            shot_id
                            for shot_id in cast(list[object], previous)
                            if isinstance(shot_id, int)
                        )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    replacement_shots = [
        shot
        for shot in shots
        if shot.status == "available"
        and shot.description
        and shot.quality is not None
        and shot.media_origin != "generated"
        and not is_generated_media_path(shot.norm_path)
        and shot.shot_id not in excluded_shot_ids
    ]
    if not replacement_shots:
        raise ShotReplacementValidationError("没有不同于当前画面的可用镜头，无法执行换镜。")

    return ShotReplacementContext(
        match_plan=match_plan,
        timings=timings,
        edl=edl,
        source_edl=source_edl,
        segment_manifest=segment_manifest,
        shots=shots,
        current_item=current_item,
        timing=timing,
        excluded_shot_ids=excluded_shot_ids,
    )


def replace_match_plan_item(
    match_plan: Sequence[MatchPlanItem],
    replacement: MatchPlanItem,
) -> list[MatchPlanItem]:
    replacement_count = sum(item.sentence_id == replacement.sentence_id for item in match_plan)
    if replacement_count != 1:
        raise ShotReplacementValidationError("换镜目标句在当前匹配计划中不存在或不唯一。")
    updated = [
        replacement if item.sentence_id == replacement.sentence_id else item
        for item in match_plan
    ]
    used_shot_ids = [
        shot_id
        for item in updated
        for shot_id in _effective_match_shot_ids(item)
    ]
    if len(used_shot_ids) != len(set(used_shot_ids)):
        raise ShotReplacementValidationError("换镜后的匹配计划包含重复镜头，已拒绝提交。")
    return updated


def replace_edl_clips(
    edl: Sequence[EDLItem],
    sentence_id: int,
    clips: Sequence[EDLClip],
) -> list[EDLItem]:
    if not clips:
        raise ShotReplacementValidationError("换镜后的 EDL 不能为空。")
    replacement_count = sum(item.sentence_id == sentence_id for item in edl)
    if replacement_count != 1:
        raise ShotReplacementValidationError("换镜目标句在 EDL 中不存在或不唯一。")
    updated = [
        item.model_copy(update={"clips": list(clips)})
        if item.sentence_id == sentence_id
        else item
        for item in edl
    ]
    used_shot_ids = [clip.shot_id for item in updated for clip in item.clips]
    if len(used_shot_ids) != len(set(used_shot_ids)):
        raise ShotReplacementValidationError("换镜后的 EDL 包含重复镜头，已拒绝提交。")
    return updated


def _effective_match_shot_ids(item: MatchPlanItem) -> list[int]:
    if item.sync_sound is not None:
        return [item.sync_sound.shot_id]
    if item.beat_matches:
        return [beat.shot_id for beat in item.beat_matches]
    return [item.shot_id]


def replace_segment_manifest_item(
    manifest: Sequence[SegmentManifestItem],
    sentence_id: int,
    segment_paths: Sequence[Path],
    task_dir: Path,
) -> list[SegmentManifestItem]:
    if not segment_paths:
        raise ShotReplacementValidationError("换镜后没有生成视频片段。")
    replacement_count = sum(item.sentence_id == sentence_id for item in manifest)
    if replacement_count != 1:
        raise ShotReplacementValidationError("换镜目标句在视频片段清单中不存在或不唯一。")
    task_root = task_dir.resolve()
    relative_paths: list[str] = []
    for path in segment_paths:
        resolved = path.resolve()
        if not resolved.is_relative_to(task_root) or not resolved.is_file():
            raise ShotReplacementValidationError(f"换镜视频片段不存在或路径不安全：{path.name}")
        relative_paths.append(resolved.relative_to(task_root).as_posix())
    return [
        item.model_copy(update={"segments": relative_paths})
        if item.sentence_id == sentence_id
        else item
        for item in manifest
    ]


def write_model_list(path: Path, models: Sequence[BaseModel], *, by_alias: bool = False) -> None:
    write_json_atomic(
        path,
        [
            model.model_dump(mode="json", by_alias=by_alias, exclude_none=True)
            for model in models
        ],
    )


def write_replacement_generated_media_disclosure(
    task_dir: Path,
    edl: Sequence[EDLItem],
) -> Path | None:
    source_path = task_dir / "generated_media_disclosure.json"
    generated_shot_ids = {
        clip.shot_id
        for item in edl
        for clip in item.clips
        if clip.media_origin == "generated"
        or is_generated_media_path(clip.src)
    }
    if not source_path.is_file():
        if generated_shot_ids:
            raise ShotReplacementValidationError(
                "换镜成片包含 AI 生成画面，但缺少原始披露清单。"
            )
        return None
    try:
        payload = GeneratedMediaDisclosureManifest.model_validate_json(
            source_path.read_text(encoding="utf-8")
        )
        items = [
            item.model_dump(mode="json")
            for item in payload.items
            if item.shot_id in generated_shot_ids
        ]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ShotReplacementValidationError("无法读取生成式媒体披露清单。") from exc
    disclosed_ids = {int(item["shot_id"]) for item in items}
    missing_ids = sorted(generated_shot_ids - disclosed_ids)
    if missing_ids:
        raise ShotReplacementValidationError(
            "换镜成片 AI 生成镜头缺少披露记录："
            + "、".join(str(shot_id) for shot_id in missing_ids)
        )
    output_path = task_dir / SHOT_REPLACEMENT_GENERATED_DISCLOSURE_TEMP_FILE
    write_json_atomic(
        output_path,
        {
            "schema_version": 1,
            "policy": "generated_visuals_are_disclosed_and_not_evidence",
            "items": items,
        },
    )
    return output_path


def write_shot_replacement_state(
    task_dir: Path,
    *,
    revision: int,
    sentence_id: int,
    instruction: str,
    previous_shot_ids: Sequence[int],
    replacement_shot_ids: Sequence[int],
    elapsed_seconds: float,
) -> Path:
    state_path = task_dir / SHOT_REPLACEMENT_TEMP_FILES["state"]
    write_json_atomic(
        state_path,
        {
            "revision": revision,
            "sentence_id": sentence_id,
            "instruction": instruction,
            "previous_shot_ids": list(dict.fromkeys(previous_shot_ids)),
            "replacement_shot_ids": list(dict.fromkeys(replacement_shot_ids)),
            "elapsed_seconds": round(elapsed_seconds, 6),
            "reused_stages": [1, 2, 3, 4, 5, 7, 8],
            "reused_narration": True,
            "reused_subtitles": True,
            "rerendered_sentence_ids": [sentence_id],
        },
    )
    _write_shot_replacement_history(
        task_dir,
        revision=revision,
        sentence_id=sentence_id,
        instruction=instruction,
        previous_shot_ids=previous_shot_ids,
        replacement_shot_ids=replacement_shot_ids,
        elapsed_seconds=elapsed_seconds,
    )
    return state_path


def _write_shot_replacement_history(
    task_dir: Path,
    *,
    revision: int,
    sentence_id: int,
    instruction: str,
    previous_shot_ids: Sequence[int],
    replacement_shot_ids: Sequence[int],
    elapsed_seconds: float,
) -> None:
    history: list[dict[str, Any]] = []
    committed_history_path = task_dir / "shot_replacement_history.json"
    if committed_history_path.is_file():
        try:
            payload = json.loads(committed_history_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                history = [
                    cast(dict[str, Any], item)
                    for item in cast(list[object], payload)
                    if isinstance(item, dict)
                ]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            history = []
    elif (task_dir / "shot_replacement_state.json").is_file():
        try:
            legacy = json.loads(
                (task_dir / "shot_replacement_state.json").read_text(encoding="utf-8")
            )
            if isinstance(legacy, dict):
                history.append({**legacy, "migrated_from_latest_state": True})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    history = [
        item
        for item in history
        if not (
            item.get("revision") == revision
            and item.get("sentence_id") == sentence_id
        )
    ]
    history.append(
        {
            "revision": revision,
            "sentence_id": sentence_id,
            "instruction": instruction,
            "previous_shot_ids": list(dict.fromkeys(previous_shot_ids)),
            "replacement_shot_ids": list(dict.fromkeys(replacement_shot_ids)),
            "elapsed_seconds": round(elapsed_seconds, 6),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    write_json_atomic(
        task_dir / SHOT_REPLACEMENT_TEMP_FILES["history"],
        history,
    )


def commit_shot_replacement(task_dir: Path) -> None:
    replacements = [
        (SHOT_REPLACEMENT_TEMP_FILES["match_plan"], "match_plan.json"),
        (SHOT_REPLACEMENT_TEMP_FILES["edl"], "edl.json"),
        (SHOT_REPLACEMENT_TEMP_FILES["segment_manifest"], "segment_manifest.json"),
        (SHOT_REPLACEMENT_TEMP_FILES["video_only"], "video_only.mp4"),
        (SHOT_REPLACEMENT_TEMP_FILES["final"], "final.mp4"),
        (SHOT_REPLACEMENT_TEMP_FILES["report"], "report.json"),
        (SHOT_REPLACEMENT_TEMP_FILES["quality"], "quality_report.json"),
        (SHOT_REPLACEMENT_TEMP_FILES["state"], "shot_replacement_state.json"),
        (SHOT_REPLACEMENT_TEMP_FILES["history"], "shot_replacement_history.json"),
    ]
    disclosure_source = task_dir / SHOT_REPLACEMENT_GENERATED_DISCLOSURE_TEMP_FILE
    if disclosure_source.is_file():
        replacements.append(
            (
                SHOT_REPLACEMENT_GENERATED_DISCLOSURE_TEMP_FILE,
                "generated_media_disclosure.json",
            )
        )
    missing = [source for source, _ in replacements if not (task_dir / source).is_file()]
    if missing:
        raise ShotReplacementValidationError(
            f"换镜提交前缺少临时产物：{', '.join(missing)}"
        )

    committed: list[tuple[Path, Path, bool]] = []
    try:
        for source_name, destination_name in replacements:
            source = task_dir / source_name
            destination = task_dir / destination_name
            backup = task_dir / f"{destination_name}.shot-replacement-backup"
            backup.unlink(missing_ok=True)
            had_destination = destination.is_file()
            if had_destination:
                destination.replace(backup)
            try:
                source.replace(destination)
            except BaseException:
                if backup.is_file():
                    backup.replace(destination)
                raise
            committed.append((destination, backup, had_destination))
    except BaseException:
        for destination, backup, had_destination in reversed(committed):
            destination.unlink(missing_ok=True)
            if had_destination and backup.is_file():
                backup.replace(destination)
        raise
    else:
        for _, backup, _ in committed:
            backup.unlink(missing_ok=True)


def cleanup_shot_replacement_temp_files(task_dir: Path) -> None:
    for file_name in (
        *SHOT_REPLACEMENT_TEMP_FILES.values(),
        SHOT_REPLACEMENT_GENERATED_DISCLOSURE_TEMP_FILE,
        "graphics.replacement.ass",
    ):
        (task_dir / file_name).unlink(missing_ok=True)
        for temporary in task_dir.glob(f"{file_name}.*.tmp"):
            temporary.unlink(missing_ok=True)


def cleanup_replacement_revision_segments(
    task_dir: Path,
    revision: int,
    sentence_id: int,
) -> None:
    directory = task_dir / "segments" / "revisions" / f"r{revision}_sentence_{sentence_id}"
    shutil.rmtree(directory, ignore_errors=True)


ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_models(path: Path, model: type[ModelT]) -> list[ModelT]:
    if not path.is_file():
        raise ShotReplacementValidationError(f"换镜基础产物不存在：{path.name}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShotReplacementValidationError(f"无法读取换镜基础产物：{path.name}") from exc
    if not isinstance(payload, list):
        raise ShotReplacementValidationError(f"换镜基础产物必须是数组：{path.name}")
    try:
        return [model.model_validate(item) for item in cast(list[object], payload)]
    except ValidationError as exc:
        raise ShotReplacementValidationError(
            f"换镜基础产物字段无效：{path.name}；{exc.errors()[0]['msg']}"
        ) from exc
