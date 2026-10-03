import json
import shutil
from collections.abc import Sequence
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


class RemixValidationError(RuntimeError):
    """Raised when existing artifacts cannot support a requested remix."""


REMIX_TEMP_FILES = {
    "timings": "timings.remix.json",
    "narration": "narration.remix.m4a",
    "narration_profile": "narration_profile.remix.json",
    "subtitles": "subs.remix.ass",
    "subtitle_manifest": "subtitle_manifest.remix.json",
    "edl": "edl.remix.json",
    "video_only": "video_only.remix.mp4",
    "final": "final.remix.mp4",
    "report": "report.remix.json",
    "quality": "quality_report.remix.json",
    "state": "remix_state.json.tmp",
}
REMIX_GENERATED_DISCLOSURE_TEMP_FILE = "generated_media_disclosure.remix.json"


def prepare_remix_sources(task_dir: Path) -> None:
    required = ["timings.json", "edl.json", "segment_manifest.json"]
    missing = [file_name for file_name in required if not (task_dir / file_name).is_file()]
    if missing:
        raise RemixValidationError(f"缺少重剪基础产物：{', '.join(missing)}")
    _copy_once(task_dir / "timings.json", task_dir / "source_timings.json")
    _copy_once(task_dir / "edl.json", task_dir / "source_edl.json")


def validate_remix_selection(task_dir: Path, keep_sentence_ids: Sequence[int]) -> list[int]:
    prepare_remix_sources(task_dir)
    source_timings = load_models(task_dir / "source_timings.json", SentenceTiming)
    source_order = [timing.sentence_id for timing in source_timings]
    keep_set = set(keep_sentence_ids)
    if not keep_set:
        raise RemixValidationError("重剪至少需要保留一个句子。")
    unknown = sorted(keep_set - set(source_order))
    if unknown:
        raise RemixValidationError(f"重剪包含未知句子：{unknown}")
    if len(keep_set) != len(keep_sentence_ids):
        raise RemixValidationError("重剪句子列表不能包含重复值。")
    return [sentence_id for sentence_id in source_order if sentence_id in keep_set]


def load_source_timings(task_dir: Path) -> list[SentenceTiming]:
    return load_models(task_dir / "source_timings.json", SentenceTiming)


def load_source_edl(task_dir: Path) -> list[EDLItem]:
    return load_models(task_dir / "source_edl.json", EDLItem)


def load_segment_manifest(task_dir: Path) -> list[SegmentManifestItem]:
    return load_models(task_dir / "segment_manifest.json", SegmentManifestItem)


def load_match_plan(task_dir: Path) -> list[MatchPlanItem]:
    return load_models(task_dir / "match_plan.json", MatchPlanItem)


def load_annotated_shots(task_dir: Path) -> list[AnnotatedShot]:
    return load_models(task_dir / "shots_annotated.json", AnnotatedShot)


def filter_match_plan(plan: Sequence[MatchPlanItem], keep_sentence_ids: Sequence[int]) -> list[MatchPlanItem]:
    keep_set = set(keep_sentence_ids)
    filtered = [item for item in plan if item.sentence_id in keep_set]
    if [item.sentence_id for item in filtered] != list(keep_sentence_ids):
        raise RemixValidationError("match_plan 与重剪句子顺序不一致。")
    return filtered


def build_remix_edl(
    source_edl: Sequence[EDLItem],
    timings: Sequence[SentenceTiming],
    output_path: Path,
) -> list[EDLItem]:
    source_by_id = {item.sentence_id: item for item in source_edl}
    timeline_cursor = 0.0
    remixed: list[EDLItem] = []
    for timing in timings:
        source = source_by_id.get(timing.sentence_id)
        if source is None:
            raise RemixValidationError(f"source_edl 缺少句子 {timing.sentence_id}。")
        need = timing.duration + timing.gap_after
        source_duration = source.timeline_end - source.timeline_start
        duration_delta = source_duration - need
        if abs(duration_delta) <= 0.02:
            clips = source.clips
        elif timing.gap_after == 0.0 and 0.0 < duration_delta <= 0.2:
            clips = _trim_edl_tail(source.clips, duration_delta)
        else:
            raise RemixValidationError(
                f"句子 {timing.sentence_id} 的既有视频片段时长与旁白不一致。"
            )
        remixed.append(
            EDLItem(
                sentence_id=timing.sentence_id,
                clips=clips,
                timeline_start=round(timeline_cursor, 6),
                timeline_end=round(timeline_cursor + need, 6),
            )
        )
        timeline_cursor += need
    shot_ids = [clip.shot_id for item in remixed for clip in item.clips]
    duplicates = sorted(
        shot_id
        for shot_id in set(shot_ids)
        if shot_ids.count(shot_id) > 1
    )
    if duplicates:
        raise RemixValidationError(
            "删减版 EDL 包含重复镜头，已拒绝生成："
            + "、".join(str(shot_id) for shot_id in duplicates)
        )
    write_json_atomic(
        output_path,
        [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in remixed],
    )
    return remixed


def _trim_edl_tail(clips: Sequence[EDLClip], seconds: float) -> list[EDLClip]:
    if not clips or seconds <= 0.0:
        return list(clips)
    updated = list(clips)
    last = updated[-1]
    freeze_pad = last.freeze_pad or 0.0
    if freeze_pad + 1e-6 >= seconds:
        remaining_freeze = max(0.0, freeze_pad - seconds)
        updated[-1] = last.model_copy(
            update={
                "freeze_pad": round(remaining_freeze, 6)
                if remaining_freeze > 1e-6
                else None
            }
        )
        return updated
    remaining = seconds - freeze_pad
    if last.out_time - last.in_time <= remaining + 0.02:
        raise RemixValidationError("删减版末尾视频片段不足，无法移除原句间停顿。")
    updated[-1] = last.model_copy(
        update={
            "out_time": round(last.out_time - remaining, 6),
            "freeze_pad": None,
        }
    )
    return updated


def select_existing_segments(
    task_dir: Path,
    manifest: Sequence[SegmentManifestItem],
    keep_sentence_ids: Sequence[int],
) -> list[Path]:
    manifest_by_id = {item.sentence_id: item for item in manifest}
    task_root = task_dir.resolve()
    segments_root = (task_dir / "segments").resolve()
    selected: list[Path] = []
    for sentence_id in keep_sentence_ids:
        item = manifest_by_id.get(sentence_id)
        if item is None:
            raise RemixValidationError(f"segment_manifest 缺少句子 {sentence_id}。")
        for segment_path in item.segments:
            resolved = (task_dir / segment_path).resolve()
            if (
                not resolved.is_relative_to(task_root)
                or not resolved.is_relative_to(segments_root)
                or not resolved.is_file()
                or resolved.stat().st_size <= 0
            ):
                raise RemixValidationError(
                    f"重剪片段不存在、为空或路径不安全：{segment_path}"
                )
            selected.append(resolved)
    return selected


def write_remix_state(
    task_dir: Path,
    *,
    revision: int,
    keep_sentence_ids: Sequence[int],
    all_sentence_ids: Sequence[int],
    elapsed_seconds: float,
) -> Path:
    state_path = task_dir / REMIX_TEMP_FILES["state"]
    write_json_atomic(
        state_path,
        {
            "revision": revision,
            "keep_sentence_ids": list(keep_sentence_ids),
            "deleted_sentence_ids": [
                sentence_id for sentence_id in all_sentence_ids if sentence_id not in set(keep_sentence_ids)
            ],
            "elapsed_seconds": round(elapsed_seconds, 6),
            "reused_stages": [1, 2, 3, 4, 5, 6],
            "reused_tts_audio": True,
            "reused_video_segments": True,
        },
    )
    return state_path


def write_remix_generated_media_disclosure(
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
        if not generated_shot_ids:
            return None
        raise RemixValidationError("删减版包含 AI 生成画面，但缺少原始披露清单。")
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
        raise RemixValidationError("无法读取生成式媒体披露清单。") from exc
    disclosed_ids = {int(item["shot_id"]) for item in items}
    missing_ids = sorted(generated_shot_ids - disclosed_ids)
    if missing_ids:
        raise RemixValidationError(
            "删减版 AI 生成镜头缺少披露记录："
            + "、".join(str(shot_id) for shot_id in missing_ids)
        )
    output_path = task_dir / REMIX_GENERATED_DISCLOSURE_TEMP_FILE
    write_json_atomic(
        output_path,
        {
            "schema_version": 1,
            "policy": "generated_visuals_are_disclosed_and_not_evidence",
            "items": items,
        },
    )
    return output_path


def commit_remix(task_dir: Path) -> None:
    replacements = [
        (REMIX_TEMP_FILES["timings"], "timings.json"),
        (REMIX_TEMP_FILES["narration"], "narration.m4a"),
        (REMIX_TEMP_FILES["narration_profile"], "narration_profile.json"),
        (REMIX_TEMP_FILES["subtitles"], "subs.ass"),
        (REMIX_TEMP_FILES["subtitle_manifest"], "subtitle_manifest.json"),
        (REMIX_TEMP_FILES["edl"], "edl.json"),
        (REMIX_TEMP_FILES["video_only"], "video_only.mp4"),
        (REMIX_TEMP_FILES["final"], "final.mp4"),
        (REMIX_TEMP_FILES["report"], "report.json"),
        (REMIX_TEMP_FILES["quality"], "quality_report.json"),
        (REMIX_TEMP_FILES["state"], "remix_state.json"),
    ]
    missing = [source for source, _ in replacements if not (task_dir / source).is_file()]
    disclosure_source = task_dir / REMIX_GENERATED_DISCLOSURE_TEMP_FILE
    if disclosure_source.is_file():
        replacements.append(
            (REMIX_GENERATED_DISCLOSURE_TEMP_FILE, "generated_media_disclosure.json")
        )
    if missing:
        raise RemixValidationError(f"重剪提交前缺少临时产物：{', '.join(missing)}")

    committed: list[tuple[Path, Path, bool]] = []
    try:
        for source_name, destination_name in replacements:
            source = task_dir / source_name
            destination = task_dir / destination_name
            backup = task_dir / f"{destination_name}.remix-backup"
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


def cleanup_remix_temp_files(task_dir: Path) -> None:
    for file_name in (
        *REMIX_TEMP_FILES.values(),
        REMIX_GENERATED_DISCLOSURE_TEMP_FILE,
        "graphics.remix.ass",
    ):
        (task_dir / file_name).unlink(missing_ok=True)
        (task_dir / f"{file_name}.tmp").unlink(missing_ok=True)


ModelT = TypeVar("ModelT", bound=BaseModel)


def load_models(path: Path, model: type[ModelT]) -> list[ModelT]:
    if not path.is_file():
        raise RemixValidationError(f"重剪产物不存在：{path.name}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RemixValidationError(f"无法读取重剪产物：{path.name}") from exc
    if not isinstance(payload, list):
        raise RemixValidationError(f"重剪产物必须是数组：{path.name}")
    try:
        return [model.model_validate(item) for item in cast(list[object], payload)]
    except ValidationError as exc:
        raise RemixValidationError(f"重剪产物字段无效：{path.name}；{exc.errors()[0]['msg']}") from exc


def _copy_once(source: Path, destination: Path) -> None:
    if destination.is_file():
        return
    temporary = destination.with_name(f"{destination.name}.tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)
