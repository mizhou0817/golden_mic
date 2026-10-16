import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .assignment import CapacityOption, solve_capacity_assignment
from .media import MediaProcessingError, probe_media, require_media_tools, run_logged_command
from .music import MusicTrack, prepare_music_bed
from .models import (
    AnnotatedShot,
    BeatMatch,
    CaptionStyle,
    EDLClip,
    EDLItem,
    MatchCandidate,
    MatchPlanItem,
    SegmentManifestItem,
    SentenceTiming,
    SyncSoundSelection,
    is_generated_media_path,
)
from .pronunciation import NUMBER_EXPRESSION_PATTERN
from .storage import write_json_atomic, write_text_log
from .subtitles import SUBTITLE_FONT_NAME, subtitle_burn_in_artifact, validate_subtitle_artifacts
from .tts_pipeline import probe_audio_duration


RenderProgressCallback = Callable[[float, str], None]
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30.0
MIN_PREFERRED_CLIP_SECONDS = 1.5

# Cross-source exposure/level consistency: link channels (no white-balance shift),
# blend at partial strength, and smooth over frames to avoid flicker, then a gentle tone adjustment.
_COLOR_CONSISTENCY_FILTER = "normalize=smoothing=50:independence=0:strength=0.5,eq=contrast=1.04:saturation=1.05"


@dataclass(frozen=True)
class SegmentRenderOptions:
    """Optional cinematic filters applied per segment. Defaults reproduce the
    historical hard-cut, static render so existing tasks and tests are unchanged."""

    color_consistency: bool = False
    motion: bool = False
    zoom_ratio: float = 0.08
    jump_zoom_frames: int = 0
    jump_zoom_ratio: float = 0.08

    @property
    def enabled(self) -> bool:
        return self.color_consistency or (self.motion and self.zoom_ratio > 0) or self.jump_zoom_frames > 0


def _ken_burns_filter(total_frames: int, zoom_ratio: float) -> str:
    """Centered push-in using fractional source coordinates, not integer crops.

    zoompan truncates crop dimensions and aligns x/y to the chroma grid. Even
    on a 2560x1440 canvas that made a stationary center jump by ~1.5 output
    pixels between frames. perspective resamples at 1/256-pixel precision;
    4:4:4 keeps all planes on the same grid until final delivery conversion.
    It forwards one frame and its timestamps, without duplicating video frames.
    """
    denominator = max(total_frames - 1, 1)
    peak = 1.0 + zoom_ratio
    # perspective's per-frame `on` is one-based, unlike zoompan's. Subtract one
    # so the first frame is identity and the Nth reaches (but never exceeds) peak.
    zoom = f"min(1+{zoom_ratio:.6f}*max(on-1,0)/{denominator},{peak:.6f})"
    left = f"W/2*(1-1/({zoom}))"
    right = f"W/2*(1+1/({zoom}))"
    top = f"H/2*(1-1/({zoom}))"
    bottom = f"H/2*(1+1/({zoom}))"
    return (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:flags=bicubic,format=yuv444p,"
        f"perspective=x0='{left}':y0='{top}':x1='{right}':y1='{top}':"
        f"x2='{left}':y2='{bottom}':x3='{right}':y3='{bottom}':"
        "sense=source:eval=frame:interpolation=cubic"
    )


def _build_segment_filters(
    source_duration: float,
    freeze_pad: float,
    options: SegmentRenderOptions,
) -> list[str]:
    filters = [f"trim=duration={source_duration:.6f}", "setpts=PTS-STARTPTS"]
    if freeze_pad > 0:
        filters.append(f"tpad=stop_mode=clone:stop_duration={freeze_pad:.6f}")
    filters.append("fps=30")
    if options.color_consistency:
        filters.append(_COLOR_CONSISTENCY_FILTER)
    if options.jump_zoom_frames > 0:
        # A real six-frame push-in, NOT a dissolve or a held/fabricated frame.
        filters.append(_ken_burns_filter(options.jump_zoom_frames, options.jump_zoom_ratio))
    elif options.motion and options.zoom_ratio > 0:
        total_frames = max(1, round((source_duration + freeze_pad) * VIDEO_FPS))
        filters.append(_ken_burns_filter(total_frames, options.zoom_ratio))
    filters.append("format=yuv420p")
    return filters


@dataclass(frozen=True)
class MusicMixOptions:
    """A resolved background-music bed and its ducking/loudness targets."""

    track: MusicTrack
    mood: str
    bed_lufs: float = -32.0
    duck_ratio: float = 8.0
    speech_intervals: tuple[tuple[float, float], ...] | None = None


@dataclass(frozen=True)
class FinishOptions:
    """Final-mix enhancements and body-caption presentation.

    Defaults reproduce the historical mix, including the unchanged news ASS."""

    music: MusicMixOptions | None = None
    graphics_path: Path | None = None
    fade_in: float = 0.0
    fade_out: float = 0.0
    caption_style: CaptionStyle = "news"
    mode_contract: bool = False
    narration_path: Path | None = None

    def __post_init__(self) -> None:
        if self.caption_style not in ("news", "big", "none"):
            raise ValueError("未知正文字幕样式。")

    @property
    def has_video_overlay(self) -> bool:
        return self.graphics_path is not None or self.fade_in > 0 or self.fade_out > 0

    @property
    def enabled(self) -> bool:
        return self.mode_contract or self.music is not None or self.has_video_overlay or self.caption_style != "news"


class RenderingError(RuntimeError):
    """Raised when an EDL or final video cannot be produced."""


async def prepare_information_card_shots(
    task_dir: Path,
    timings: Sequence[SentenceTiming],
    match_plan: list[MatchPlanItem],
    shots: list[AnnotatedShot],
) -> list[int]:
    timings_by_id = {timing.sentence_id: timing for timing in timings}
    shots_by_id = {shot.shot_id: shot for shot in shots}
    generated_shot_ids = {
        shot.shot_id for shot in shots if shot.description == "新闻结束信息卡"
    }
    source_shots = [
        shot
        for shot in shots
        if shot.status == "available"
        and shot.quality is not None
        and shot.media_origin != "generated"
        and not is_generated_media_path(shot.norm_path)
        and shot.shot_id not in generated_shot_ids
        and not shot.source_name.startswith("generated_information_card_")
    ]
    overlay_entries: list[tuple[int, MatchPlanItem, Literal["date", "organization", "abstract"]]] = []
    for plan_index, item in enumerate(match_plan):
        existing_shot = shots_by_id.get(item.shot_id)
        uses_generated_card = bool(
            existing_shot is not None and existing_shot.shot_id in generated_shot_ids
        )
        overlay_kind = item.overlay_kind
        if overlay_kind is None and _is_information_card_text(item.text) and (
            item.is_fallback or uses_generated_card
        ):
            overlay_kind = "date"
        if (
            overlay_kind is not None
            and item.sync_sound is None
            and item.sentence_id in timings_by_id
        ):
            overlay_entries.append((plan_index, item, overlay_kind))
    if not overlay_entries:
        return []

    # An overlay annotates a narration unit; it is not permission to replace
    # its beat-level edit with a single shot. Keep existing source assignments
    # (including fallback/evidence metadata) and reserve EVERY retained slot.
    # Invalid multibeat plans need matching repair, not silent slot deletion.
    source_shot_ids = {shot.shot_id for shot in source_shots}
    preserved_overlay_indexes: set[int] = set()
    for plan_index, item, _ in overlay_entries:
        if len(item.beat_matches) > 1 or _has_verified_overlay_evidence(item):
            shot_ids = [beat.shot_id for beat in item.beat_matches] or [item.shot_id]
            if any(shot_id not in source_shot_ids for shot_id in shot_ids):
                raise RenderingError(
                    f"句子 {item.sentence_id} 的事实叠层计划引用了不可用或非原始素材镜头，"
                    "请重新匹配；不能通过合并视觉节拍或覆盖已核验证据修复。"
                )
            preserved_overlay_indexes.add(plan_index)
    if not source_shots:
        return []

    assignable_entries = [
        entry for entry in overlay_entries if entry[0] not in preserved_overlay_indexes
    ]
    assignable_indexes = {plan_index for plan_index, _, _ in assignable_entries}
    base_usage: dict[int, int] = {}
    for plan_index, item in enumerate(match_plan):
        if plan_index in assignable_indexes:
            continue
        shot_ids = (
            [item.sync_sound.shot_id]
            if item.sync_sound is not None
            else [beat.shot_id for beat in item.beat_matches] or [item.shot_id]
        )
        for shot_id in shot_ids:
            base_usage[shot_id] = base_usage.get(shot_id, 0) + 1
    repeated_base_shots = {
        shot_id: count
        for shot_id, count in base_usage.items()
        if count > 1
    }
    if repeated_base_shots:
        details = "、".join(
            f"镜头 {shot_id} 使用 {count} 次"
            for shot_id, count in sorted(repeated_base_shots.items())
        )
        raise RenderingError(f"事实叠层分配前已存在重复镜头：{details}。")
    reuse_limit = 1
    capacities = {
        shot.shot_id: max(0, reuse_limit - base_usage.get(shot.shot_id, 0))
        for shot in source_shots
    }
    options_by_overlay: list[list[CapacityOption]] = []
    utility_by_overlay: dict[int, dict[int, float]] = {}
    for plan_index, item, overlay_kind in assignable_entries:
        group_context = " ".join(
            plan_item.text
            for plan_item in match_plan
            if plan_item.visual_group_id == item.visual_group_id
        )
        local_context = " ".join(part for part in (item.text, group_context) if part)
        candidates_by_id = {candidate.shot_id: candidate for candidate in item.candidates}
        utilities = {
            shot.shot_id: _overlay_candidate_utility(
                item,
                overlay_kind,
                shot,
                local_context,
                candidates_by_id.get(shot.shot_id),
            )
            for shot in source_shots
        }
        utility_by_overlay[plan_index] = utilities
        options_by_overlay.append(
            [
                CapacityOption(resource_id=shot_id, utility=utility)
                for shot_id, utility in utilities.items()
            ]
        )
    selected_ids = solve_capacity_assignment(
        options_by_overlay,
        capacities,
        reuse_penalty=0.12,
    )
    if selected_ids is None:
        raise RenderingError(
            "事实信息语境镜头无法形成一对一分配，无法保证每个镜头只使用一次。"
            "请增加素材或减少需要叠加事实文字的播音单元。"
        )
    selected_by_plan_index = {
        entry[0]: selected_id
        for entry, selected_id in zip(assignable_entries, selected_ids, strict=True)
    }

    overlay_sentence_ids: list[int] = []
    replaced_generated_ids: set[int] = set()
    overlay_assignment_rows: list[dict[str, object]] = []
    source_shots_by_id = {shot.shot_id: shot for shot in source_shots}
    for plan_index, item, overlay_kind in overlay_entries:
        if plan_index in preserved_overlay_indexes:
            match_plan[plan_index] = item.model_copy(update={
                "overlay_kind": overlay_kind,
                "overlay_text": item.overlay_text or item.text,
            })
            preserved_ids = [beat.shot_id for beat in item.beat_matches] or [item.shot_id]
            overlay_sentence_ids.append(item.sentence_id)
            overlay_assignment_rows.append({
                "sentence_id": item.sentence_id,
                "visual_group_id": item.visual_group_id,
                "kind": overlay_kind,
                "previous_shot_id": item.shot_id,
                "selected_shot_id": item.shot_id,
                "preserved_assignment": True,
                "preserved_shot_ids": preserved_ids,
            })
            write_text_log(
                task_dir,
                f"事实信息叠层保留原视觉节拍 sentence={item.sentence_id} "
                f"kind={overlay_kind} shots={preserved_ids}",
            )
            continue

        selected_id = selected_by_plan_index[plan_index]
        existing_shot = shots_by_id.get(item.shot_id)
        uses_generated_card = bool(
            existing_shot is not None and existing_shot.shot_id in generated_shot_ids
        )
        selected_shot = source_shots_by_id[selected_id]
        candidates_by_id = {candidate.shot_id: candidate for candidate in item.candidates}
        contextual_candidate = candidates_by_id.get(selected_id) or _contextual_overlay_candidate(
            selected_shot,
            utility_by_overlay[plan_index][selected_id],
        )
        preserved_candidates = [
            contextual_candidate,
            *(candidate for candidate in item.candidates if candidate.shot_id != selected_id),
        ]
        previous_beat = item.beat_matches[0] if item.beat_matches else None
        confidence = min(
            item.confidence,
            previous_beat.confidence if previous_beat is not None else item.confidence,
            max(0.35, contextual_candidate.combined_score),
        )
        is_fallback = item.is_fallback or bool(previous_beat and previous_beat.is_fallback)
        if previous_beat is not None:
            beat = previous_beat.model_copy(update={
                "shot_id": selected_shot.shot_id,
                "confidence": confidence,
                "alternates": [],
                "is_fallback": is_fallback,
                "candidates": preserved_candidates,
                "preferred_in_time": (
                    previous_beat.preferred_in_time
                    if previous_beat.shot_id == selected_id and previous_beat.preferred_in_time is not None
                    else contextual_candidate.preferred_in_time
                ),
            })
        else:
            beat = BeatMatch(
                beat_id=0,
                text=item.text,
                entities=[],
                requires_entity_coverage=False,
                shot_id=selected_shot.shot_id,
                confidence=confidence,
                is_fallback=is_fallback,
                candidates=preserved_candidates,
                intent_type=overlay_kind,
                visual_group_id=item.visual_group_id,
                preferred_in_time=contextual_candidate.preferred_in_time,
            )
        match_plan[plan_index] = item.model_copy(
            update={
                "shot_id": selected_shot.shot_id,
                "confidence": beat.confidence,
                "alternates": [],
                "is_fallback": is_fallback,
                "candidates": preserved_candidates,
                "beat_matches": [beat],
                "overlay_kind": overlay_kind,
                "overlay_text": item.overlay_text or item.text,
            }
        )
        if uses_generated_card and existing_shot is not None:
            replaced_generated_ids.add(existing_shot.shot_id)
        overlay_sentence_ids.append(item.sentence_id)
        overlay_assignment_rows.append(
            {
                "sentence_id": item.sentence_id,
                "visual_group_id": item.visual_group_id,
                "kind": overlay_kind,
                "previous_shot_id": item.shot_id,
                "selected_shot_id": selected_shot.shot_id,
                "utility": round(utility_by_overlay[plan_index][selected_id], 6),
                "base_usage_before_overlay": base_usage.get(selected_shot.shot_id, 0),
                "remaining_capacity_before_overlay": capacities.get(selected_shot.shot_id, 0),
            }
        )
        write_text_log(
            task_dir,
            f"事实信息改用语境素材并叠加文字 sentence={item.sentence_id} "
            f"kind={overlay_kind} previous_shot={item.shot_id} "
            f"shot={selected_shot.shot_id} source={selected_shot.source_name} "
            f"utility={utility_by_overlay[plan_index][selected_id]:.4f}",
        )

    if replaced_generated_ids:
        removed = [shot for shot in shots if shot.shot_id in replaced_generated_ids]
        shots[:] = [shot for shot in shots if shot.shot_id not in replaced_generated_ids]
        for shot in removed:
            (task_dir / shot.norm_path).unlink(missing_ok=True)
            if shot.thumb_path:
                (task_dir / shot.thumb_path).unlink(missing_ok=True)
    write_json_atomic(
        task_dir / "overlay_shot_assignment.json",
        {
            "schema_version": 1,
            "strategy": "strict_one_to_one_local_context_assignment",
            "default_total_shot_capacity": reuse_limit,
            "base_usage": dict(sorted(base_usage.items())),
            "remaining_capacities": dict(sorted(capacities.items())),
            "assignments": overlay_assignment_rows,
        },
    )
    return overlay_sentence_ids


def _has_verified_overlay_evidence(item: MatchPlanItem) -> bool:
    # Use the same accepted-evidence threshold as quality reporting. A soft
    # local-context utility must never displace an already verified source.
    selections = [
        (item.shot_id, item.candidates),
        *((beat.shot_id, beat.candidates) for beat in item.beat_matches),
    ]
    return any(
        candidate.shot_id == shot_id
        and bool(candidate.verified_terms)
        and candidate.verification_confidence is not None
        and candidate.verification_confidence >= 0.75
        for shot_id, candidates in selections
        for candidate in candidates
    )


def _select_information_overlay_shot(
    item: MatchPlanItem,
    match_plan: Sequence[MatchPlanItem],
    source_shots: Sequence[AnnotatedShot],
) -> tuple[AnnotatedShot, MatchCandidate]:
    candidates_by_id = {candidate.shot_id: candidate for candidate in item.candidates}
    local_text = _normalized_context_text(
        " ".join(
            plan_item.text
            for plan_item in match_plan
            if plan_item.visual_group_id == item.visual_group_id
        )
        or item.text
    )
    selected_shot = max(
        source_shots,
        key=lambda shot: (
            _information_context_score(shot, local_text),
            candidates_by_id.get(
                shot.shot_id,
                MatchCandidate(shot_id=shot.shot_id, similarity=0.0),
            ).combined_score,
            (shot.quality.sharp * shot.quality.bright) if shot.quality else 0.0,
            min(shot.duration, 10.0),
            -shot.shot_id,
        ),
    )
    existing_candidate = candidates_by_id.get(selected_shot.shot_id)
    if existing_candidate is not None:
        return selected_shot, existing_candidate
    context_score = _information_context_score(selected_shot, local_text)
    normalized_score = min(1.0, 0.35 + context_score / 100.0)
    return selected_shot, MatchCandidate(
        shot_id=selected_shot.shot_id,
        similarity=normalized_score,
        lexical_score=min(1.0, context_score / 60.0),
        combined_score=normalized_score,
        matched_terms=["活动语境"],
    )


def _overlay_candidate_utility(
    item: MatchPlanItem,
    overlay_kind: str,
    shot: AnnotatedShot,
    local_context: str,
    candidate: MatchCandidate | None,
) -> float:
    context_score = min(
        1.0,
        _information_context_score(shot, _normalized_context_text(local_context)) / 60.0,
    )
    retrieval_score = max(0.0, candidate.combined_score) if candidate is not None else 0.0
    quality_score = (
        shot.quality.sharp * shot.quality.bright
        if shot.quality is not None
        else 0.0
    )
    if overlay_kind == "abstract":
        utility = 0.30 * context_score + 0.42 * retrieval_score + 0.08 * quality_score
        preserve_bonus = 0.20
    else:
        utility = 0.50 * context_score + 0.30 * retrieval_score + 0.08 * quality_score
        preserve_bonus = 0.12
    if shot.shot_id == item.shot_id:
        utility += preserve_bonus
    return utility


def _contextual_overlay_candidate(
    shot: AnnotatedShot,
    utility: float,
) -> MatchCandidate:
    normalized_score = max(0.0, min(1.0, utility))
    return MatchCandidate(
        shot_id=shot.shot_id,
        similarity=normalized_score,
        lexical_score=0.0,
        combined_score=normalized_score,
        verification_evidence="相关环境画面加事实文字叠层，不代表直接视觉证明。",
    )


def _information_context_score(shot: AnnotatedShot, document_text: str) -> float:
    score = 0.0
    phrases = list(
        dict.fromkeys(
            [
                *shot.entities,
                *shot.ocr_texts,
                *shot.keywords,
                *shot.subjects,
            ]
        )
    )
    for phrase in phrases:
        normalized = _normalized_context_text(phrase)
        if len(normalized) >= 2 and normalized in document_text:
            score += min(36.0, float(len(normalized) ** 2))
    shot_text = _normalized_context_text(shot.search_text or shot.description or "")
    for anchor, weight in (
        ("活动现场", 8.0),
        ("活动", 3.0),
        ("市集", 5.0),
        ("入口", 5.0),
        ("广场", 4.0),
        ("展板", 4.0),
    ):
        normalized_anchor = _normalized_context_text(anchor)
        if normalized_anchor in document_text and normalized_anchor in shot_text:
            score += weight
    return score


def _normalized_context_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text.lower())


def _is_information_card_text(text: str) -> bool:
    return bool(re.search(r"(?:持续到|截至|截止|活动时间|报名时间|结束于|\d{1,2}月\d{1,2}日)", text))


def build_edl(
    task_dir: Path,
    timings: Sequence[SentenceTiming],
    match_plan: Sequence[MatchPlanItem],
    shots: Sequence[AnnotatedShot],
) -> list[EDLItem]:
    timings_by_id = {timing.sentence_id: timing for timing in timings}
    matches_by_id = {item.sentence_id: item for item in match_plan}
    available_shots = [
        shot for shot in shots if shot.status == "available" and shot.quality is not None
    ]
    shots_by_id = {shot.shot_id: shot for shot in available_shots}
    if not available_shots:
        raise RenderingError("没有可用于构建时间线的镜头。")
    if set(timings_by_id) != set(matches_by_id):
        raise RenderingError("timings 与 match_plan 的句子集合不一致。")

    planned_shot_ids = [
        shot_id
        for item in match_plan
        for shot_id in (
            [item.sync_sound.shot_id]
            if item.sync_sound is not None
            else [beat.shot_id for beat in item.beat_matches]
            or [item.shot_id]
        )
    ]
    duplicate_planned_shots = sorted(
        shot_id
        for shot_id in set(planned_shot_ids)
        if planned_shot_ids.count(shot_id) > 1
    )
    if duplicate_planned_shots:
        raise RenderingError(
            "匹配计划包含重复镜头，已拒绝构建 EDL："
            + "、".join(str(shot_id) for shot_id in duplicate_planned_shots)
        )
    timeline_cursor = 0.0
    edl: list[EDLItem] = []

    for sentence_id in sorted(timings_by_id):
        timing = timings_by_id[sentence_id]
        match = matches_by_id[sentence_id]
        if match.sync_sound is not None:
            sync_shot = shots_by_id.get(match.sync_sound.shot_id)
            if sync_shot is None:
                raise RenderingError(f"句子 {sentence_id} 的同期声镜头不可用。")
            clips = fill_sync_sound_clip(timing, match.sync_sound, sync_shot)
        elif len({beat.shot_id for beat in match.beat_matches}) > 1:
            clips = fill_visual_beat_clips(timing, match, shots_by_id)
        else:
            primary_shot_id = (
                match.beat_matches[0].shot_id
                if match.beat_matches
                else match.shot_id
            )
            primary_shot = shots_by_id.get(primary_shot_id)
            if primary_shot is None:
                raise RenderingError(f"句子 {sentence_id} 的镜头 {primary_shot_id} 不可用。")
            clips = fill_clips(
                timing,
                [primary_shot],
                preferred_in_time=_match_preferred_in_time(match, primary_shot_id),
            )
        need = timing.duration + timing.gap_after
        edl.append(
            EDLItem(
                sentence_id=sentence_id,
                clips=clips,
                timeline_start=round(timeline_cursor, 6),
                timeline_end=round(timeline_cursor + need, 6),
            )
        )
        timeline_cursor += need

    _validate_unique_edl_shots(edl)
    write_json_atomic(
        task_dir / "edl.json",
        [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in edl],
    )
    return edl


def _validate_unique_edl_shots(edl: Sequence[EDLItem]) -> None:
    shot_ids = [clip.shot_id for item in edl for clip in item.clips]
    duplicates = sorted(
        shot_id
        for shot_id in set(shot_ids)
        if shot_ids.count(shot_id) > 1
    )
    if duplicates:
        raise RenderingError(
            "EDL 包含重复镜头，已拒绝渲染："
            + "、".join(str(shot_id) for shot_id in duplicates)
        )


def fill_sync_sound_clip(
    timing: SentenceTiming,
    selection: SyncSoundSelection,
    shot: AnnotatedShot,
) -> list[EDLClip]:
    if shot.source_index != selection.source_index:
        raise RenderingError("同期声镜头与源素材不一致。")
    source_duration = selection.end - selection.start
    if source_duration <= 0:
        raise RenderingError("同期声入出点无效。")
    need = timing.duration + timing.gap_after
    take = min(source_duration, need)
    freeze_pad = need - take
    return [
        EDLClip(
            shot_id=shot.shot_id,
            src=shot.norm_path,
            in_time=round(selection.start, 6),
            out_time=round(selection.start + take, 6),
            freeze_pad=round(freeze_pad, 6) if freeze_pad > 0.000001 else None,
            media_origin=shot.media_origin,
        )
    ]


def fill_clips(
    timing: SentenceTiming,
    pool: Sequence[AnnotatedShot],
    *,
    preferred_in_time: float | None = None,
) -> list[EDLClip]:
    need = timing.duration + timing.gap_after
    if pool[0].duration >= need - 0.01:
        return [_clip_from_shot(pool[0], need, preferred_in_time=preferred_in_time)]

    eligible = [shot for shot in pool if shot.duration >= MIN_PREFERRED_CLIP_SECONDS]
    if eligible:
        first = pool[0] if pool[0].duration >= MIN_PREFERRED_CLIP_SECONDS else max(
            eligible,
            key=lambda shot: (shot.duration, -pool.index(shot)),
        )
        remaining = [shot for shot in eligible if shot.shot_id != first.shot_id]
        ordered_pool = [
            first,
            *sorted(
                remaining,
                key=lambda shot: (-shot.duration, pool.index(shot)),
            ),
        ]
    else:
        ordered_pool = [max(pool, key=lambda shot: shot.duration)]

    clips: list[EDLClip] = []
    got = 0.0
    for shot in ordered_pool:
        remaining = need - got
        if remaining <= 0.0:
            break
        if clips and remaining < MIN_PREFERRED_CLIP_SECONDS:
            break
        take = min(shot.duration, remaining)
        if clips and take < MIN_PREFERRED_CLIP_SECONDS:
            continue
        clips.append(
            _clip_from_shot(
                shot,
                take,
                preferred_in_time=(preferred_in_time if not clips and shot.shot_id == pool[0].shot_id else None),
            )
        )
        got += take
        if got >= need - 0.01:
            break

    if not clips:
        raise RenderingError(f"句子 {timing.sentence_id} 无法填充任何视频片段。")
    if got < need:
        freeze_pad = need - got
        clips[-1] = clips[-1].model_copy(update={"freeze_pad": round(freeze_pad, 6)})
    return clips


def fill_visual_beat_clips(
    timing: SentenceTiming,
    match: MatchPlanItem,
    shots_by_id: dict[int, AnnotatedShot],
) -> list[EDLClip]:
    beats: list[BeatMatch] = []
    for beat in match.beat_matches:
        if not beats or beats[-1].shot_id != beat.shot_id:
            beats.append(beat)
    if len(beats) <= 1:
        shot = shots_by_id.get(beats[0].shot_id if beats else match.shot_id)
        if shot is None:
            raise RenderingError(f"句子 {timing.sentence_id} 的视觉节拍镜头不可用。")
        return fill_clips(timing, [shot])

    need = timing.duration + timing.gap_after
    starts = _visual_beat_starts(timing, beats)
    if starts is None:
        weights = [max(1, len(_normalize_alignment_text(beat.text))) for beat in beats]
        total_weight = sum(weights)
        starts = [0.0]
        cursor = 0.0
        for weight in weights[:-1]:
            cursor += need * weight / total_weight
            starts.append(cursor)
    durations = [
        (starts[index + 1] if index + 1 < len(starts) else need) - start
        for index, start in enumerate(starts)
    ]

    clips: list[EDLClip] = []
    for beat, duration in zip(beats, durations, strict=True):
        shot = shots_by_id.get(beat.shot_id)
        if shot is None:
            raise RenderingError(
                f"句子 {timing.sentence_id} 的视觉节拍 {beat.beat_id} 引用了不可用镜头 {beat.shot_id}。"
            )
        take = min(shot.duration, duration)
        clip = _clip_from_shot(
            shot,
            take,
            preferred_in_time=beat.preferred_in_time,
        )
        if take < duration:
            clip = clip.model_copy(update={"freeze_pad": round(duration - take, 6)})
        clips.append(clip)
    return clips


def _visual_beat_starts(
    timing: SentenceTiming,
    beats: Sequence[BeatMatch],
) -> list[float] | None:
    """Return only unambiguous beat onsets backed by existing word cues."""
    if not timing.words or len(beats) < 2:
        return None
    normalized_words = [_normalize_spoken_alignment_text(word.text) for word in timing.words]
    stream_parts: list[str] = []
    word_ranges: list[tuple[int, int]] = []
    cursor = 0
    for part in normalized_words:
        start = cursor
        cursor += len(part)
        stream_parts.append(part)
        word_ranges.append((start, cursor))
    stream = "".join(stream_parts)
    if not stream:
        return None

    # Older timings have only text + words. Optional persisted spoken context
    # must agree with the cues; it must never manufacture missing word times.
    spoken_text = getattr(timing, "spoken_text", None)
    if spoken_text is not None and (
        not isinstance(spoken_text, str) or _normalize_spoken_alignment_text(spoken_text) != stream
    ):
        return None
    source = _normalize_spoken_alignment_text(timing.text)
    offsets = _spoken_alignment_offsets(timing.text, stream)
    if offsets is None and spoken_text is None and source.startswith(stream):
        # Preserve exact plain-word onsets when only trailing cues are absent.
        offsets = [index if index <= len(stream) else None for index in range(len(source) + 1)]
    if offsets is None:
        return None

    starts = [0.0]
    minimum_character = 0
    for beat_index, beat in enumerate(beats):
        phrase = _normalize_spoken_alignment_text(beat.text)
        if not phrase:
            return None
        position = source.find(phrase, minimum_character)
        if position < 0 or source.find(phrase, position + 1) >= 0:
            # A repeated date/entity is not evidence of which occurrence this
            # beat means. Never recover with a suffix such as "办了".
            return None
        source_end = position + len(phrase)
        found_character = offsets[position]
        spoken_end = offsets[source_end]
        if found_character is None or spoken_end is None or spoken_end <= found_character:
            return None
        minimum_character = source_end
        if beat_index == 0:
            # Consume the first beat too, so later beats cannot reuse its words.
            continue
        word_index = next(
            (
                index
                for index, (word_start, word_end) in enumerate(word_ranges)
                if word_start == found_character and word_end > word_start
            ),
            None,
        )
        if word_index is None:
            # A phrase beginning inside a multi-character cue has no separate
            # measured onset. Do not interpolate or borrow that word's start.
            return None
        start_time = timing.words[word_index].start
        if start_time <= starts[-1] + 0.001 or start_time >= timing.duration:
            return None
        starts.append(start_time)
    return starts


def _spoken_alignment_offsets(text: str, stream: str) -> list[int | None] | None:
    """Map source boundaries through number-only replacements, without guessing.

    Use the same source slots as apply_pronunciation_readings. Unchanged text
    must match in full and in order; replacement characters come ONLY from the
    stored word stream. None marks an ambiguous or intra-number boundary.
    """
    source = _normalize_spoken_alignment_text(text)
    if source == stream:
        return list(range(len(source) + 1))
    numbers = [
        (
            len(_normalize_spoken_alignment_text(text[:match.start()])),
            len(_normalize_spoken_alignment_text(text[:match.end()])),
        )
        for match in NUMBER_EXPRESSION_PATTERN.finditer(text)
    ]
    if not numbers or not stream.startswith(source[:numbers[0][0]]):
        return None

    # Mirrors the pronunciation validator's allowlist and the reading model's
    # 32-character limit, without importing/calling any provider. No numeric
    # value conversion: e.g. 101 may be the persisted "幺洞幺", not "一百零一".
    spoken_digits = frozenset("零〇一二两三四五六七八九十百千万亿兆京垓点负正分之幺洞拐勾杠廿")
    # Each state is an offset and its unique reading spans. Two paths reaching
    # the same offset mark it ambiguous; carry that fact forward, never pick one.
    states: dict[int, tuple[tuple[int, int], ...] | None] = {numbers[0][0]: ()}
    for index, (start, end) in enumerate(numbers):
        following = numbers[index + 1][0] if index + 1 < len(numbers) else len(source)
        anchor = source[end:following]
        number = source[start:end]
        next_states: dict[int, tuple[tuple[int, int], ...] | None] = {}
        for spoken_start, spans in states.items():
            ends = [spoken_start + len(number)] if stream.startswith(number, spoken_start) else []
            for spoken_end in range(spoken_start + 1, min(len(stream), spoken_start + 32) + 1):
                if stream[spoken_end - 1] not in spoken_digits:
                    break
                ends.append(spoken_end)
            for spoken_end in ends:
                if not stream.startswith(anchor, spoken_end):
                    continue
                next_offset = spoken_end + len(anchor)
                next_states[next_offset] = (
                    None
                    if next_offset in next_states or spans is None
                    else (*spans, (spoken_start, spoken_end))
                )
        states = next_states
        if not states:
            return None
    reading_spans = states.get(len(stream))
    if reading_spans is None:
        return None

    offsets: list[int | None] = [None] * (len(source) + 1)
    source_cursor = spoken_cursor = 0
    for (start, end), (spoken_start, spoken_end) in zip(numbers, reading_spans, strict=True):
        for position in range(source_cursor, start + 1):
            offsets[position] = spoken_cursor + position - source_cursor
        if source[start:end] == stream[spoken_start:spoken_end]:
            for position in range(start + 1, end):
                offsets[position] = spoken_start + position - start
        # Replaced digits have only measured outer boundaries, not a guessed
        # per-digit alignment to an expanded/contracted Chinese reading.
        offsets[end] = spoken_end
        source_cursor, spoken_cursor = end, spoken_end
    for position in range(source_cursor, len(source) + 1):
        offsets[position] = spoken_cursor + position - source_cursor
    return offsets


def _normalize_spoken_alignment_text(text: str) -> str:
    # Keep the allowed spoken zero "〇" and fullwidth source digits in the map.
    # This is separate from the existing text-length allocation fallback.
    return re.sub(r"[^0-9A-Za-z〇\u4e00-\u9fff]", "", unicodedata.normalize("NFKC", text)).lower()


def _normalize_alignment_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).lower()


def _clip_from_shot(
    shot: AnnotatedShot,
    duration: float,
    *,
    preferred_in_time: float | None = None,
) -> EDLClip:
    latest_start = max(shot.start, shot.end - duration)
    start = (
        min(latest_start, max(shot.start, preferred_in_time))
        if preferred_in_time is not None
        else shot.start
    )
    return EDLClip(
        shot_id=shot.shot_id,
        src=shot.norm_path,
        in_time=round(start, 6),
        out_time=round(min(shot.end, start + duration), 6),
        evidence_aligned=True if preferred_in_time is not None else None,
        media_origin=shot.media_origin,
    )


def _match_preferred_in_time(match: MatchPlanItem, shot_id: int) -> float | None:
    for beat in match.beat_matches:
        if beat.shot_id == shot_id and beat.preferred_in_time is not None:
            return beat.preferred_in_time
    candidate = next(
        (
            item
            for item in match.candidates
            if item.shot_id == shot_id and item.preferred_in_time is not None
        ),
        None,
    )
    return candidate.preferred_in_time if candidate is not None else None


async def render_final_video(
    task_dir: Path,
    edl: Sequence[EDLItem],
    progress: RenderProgressCallback,
    *,
    fonts_dir: Path | None = None,
    target_loudness_lufs: float = -20.0,
    target_loudness_range: float = 5.0,
    maximum_true_peak_dbfs: float = -2.0,
    segment_options: SegmentRenderOptions | None = None,
    finish_options: FinishOptions | None = None,
) -> Path:
    require_media_tools()
    flat_clips = [clip for item in edl for clip in item.clips]
    if not flat_clips:
        raise RenderingError("EDL 中没有可渲染片段。")
    segments_dir = task_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    for old_segment in segments_dir.glob("seg_*.mp4"):
        old_segment.unlink(missing_ok=True)

    segment_paths: list[Path] = []
    total_segments = len(flat_clips)
    for index, clip in enumerate(flat_clips):
        segment_path = segments_dir / f"seg_{index:04d}.mp4"
        await _render_segment(task_dir, clip, segment_path, index, segment_options)
        segment_paths.append(segment_path)
        progress(0.7 * (index + 1) / total_segments, f"视频渲染 {index + 1}/{total_segments}")

    _write_segment_manifest(task_dir, edl, segment_paths)

    video_only_path = task_dir / "video_only.mp4"
    await _concatenate_segments(task_dir, segment_paths, video_only_path)
    progress(0.82, "视频片段合并完成")

    narration_path = task_dir / "narration.m4a"
    subtitles_path = task_dir / "subs.ass"
    subtitle_manifest_path = task_dir / "subtitle_manifest.json"
    final_path = task_dir / "final.mp4"
    await _mix_subtitles_and_narration(
        task_dir,
        video_only_path,
        narration_path,
        subtitles_path,
        final_path,
        subtitle_manifest_path=subtitle_manifest_path,
        fonts_dir=fonts_dir,
        target_loudness_lufs=target_loudness_lufs,
        target_loudness_range=target_loudness_range,
        maximum_true_peak_dbfs=maximum_true_peak_dbfs,
        finish_options=finish_options,
    )
    progress(0.95, "字幕烧录与旁白混音完成")
    await validate_final_video(
        task_dir,
        final_path,
        narration_path,
        subtitles_path=subtitles_path,
        subtitle_manifest_path=subtitle_manifest_path,
    )
    progress(1.0, "成片规格与时长校验完成")
    return final_path


async def render_replacement_segments(
    task_dir: Path,
    clips: Sequence[EDLClip],
    *,
    revision: int,
    sentence_id: int,
    progress: RenderProgressCallback,
    segment_options: SegmentRenderOptions | None = None,
) -> list[Path]:
    require_media_tools()
    if revision < 1 or sentence_id < 0:
        raise RenderingError("换镜版本号或句子编号无效。")
    if not clips:
        raise RenderingError("换镜句子没有可渲染的视频片段。")

    output_dir = task_dir / "segments" / "revisions" / f"r{revision}_sentence_{sentence_id}"
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    try:
        for index, clip in enumerate(clips):
            output_path = output_dir / f"clip_{index:02d}.mp4"
            await _render_segment(task_dir, clip, output_path, index, segment_options)
            paths.append(output_path)
            progress(
                (index + 1) / len(clips),
                f"目标句镜头渲染 {index + 1}/{len(clips)}",
            )
    except BaseException:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return paths


async def render_from_existing_segments(
    task_dir: Path,
    segment_paths: Sequence[Path],
    progress: RenderProgressCallback,
    *,
    narration_path: Path,
    subtitles_path: Path,
    subtitle_manifest_path: Path,
    video_only_path: Path,
    final_path: Path,
    fonts_dir: Path | None = None,
    target_loudness_lufs: float = -20.0,
    target_loudness_range: float = 5.0,
    maximum_true_peak_dbfs: float = -2.0,
    finish_options: FinishOptions | None = None,
) -> Path:
    require_media_tools()
    if not segment_paths:
        raise RenderingError("重剪没有可复用的视频片段。")
    task_root = task_dir.resolve()
    segments_root = (task_dir / "segments").resolve()
    validated_segments: list[Path] = []
    for segment_path in segment_paths:
        resolved = segment_path.resolve()
        if not resolved.is_relative_to(task_root) or not resolved.is_relative_to(segments_root) or not resolved.is_file():
            raise RenderingError(f"重剪片段不存在或路径不安全：{segment_path}")
        validated_segments.append(resolved)

    await _concatenate_segments(task_dir, validated_segments, video_only_path)
    progress(0.5, f"已复用并合并 {len(validated_segments)} 个既有片段")
    await _mix_subtitles_and_narration(
        task_dir,
        video_only_path,
        narration_path,
        subtitles_path,
        final_path,
        subtitle_manifest_path=subtitle_manifest_path,
        fonts_dir=fonts_dir,
        target_loudness_lufs=target_loudness_lufs,
        target_loudness_range=target_loudness_range,
        maximum_true_peak_dbfs=maximum_true_peak_dbfs,
        finish_options=finish_options,
    )
    progress(0.9, "重剪字幕烧录与旁白混音完成")
    await validate_final_video(
        task_dir,
        final_path,
        narration_path,
        subtitles_path=subtitles_path,
        subtitle_manifest_path=subtitle_manifest_path,
    )
    progress(1.0, "重剪成片规格与时长校验完成")
    return final_path


async def _mix_subtitles_and_narration(
    task_dir: Path,
    video_only_path: Path,
    narration_path: Path,
    subtitles_path: Path,
    final_path: Path,
    *,
    subtitle_manifest_path: Path,
    fonts_dir: Path | None = None,
    target_loudness_lufs: float = -20.0,
    target_loudness_range: float = 5.0,
    maximum_true_peak_dbfs: float = -2.0,
    finish_options: FinishOptions | None = None,
) -> None:
    if (
        not video_only_path.is_file()
        or not narration_path.is_file()
        or not subtitles_path.is_file()
        or not subtitle_manifest_path.is_file()
    ):
        raise RenderingError("缺少视频、旁白、ASS 字幕或字幕模板绑定清单，无法生成成片。")
    options = finish_options or FinishOptions()
    if options.mode_contract:
        from .graphics import validate_mode_subtitle_artifacts

        validate_mode_subtitle_artifacts(subtitles_path, subtitle_manifest_path)
        font_directory = fonts_dir or Path(__file__).resolve().parent / "assets" / "fonts"
        render_font_directory = _prepare_ass_font_directory(task_dir, font_directory)
        await _mix_validated_narration(
            task_dir, video_only_path, narration_path, final_path,
            subtitle_filter=_build_subtitle_filter(task_dir, subtitles_path, render_font_directory),
            render_font_directory=render_font_directory, target_loudness_lufs=target_loudness_lufs,
            target_loudness_range=target_loudness_range, maximum_true_peak_dbfs=maximum_true_peak_dbfs,
            options=options,
        )
        return
    try:
        with subtitle_burn_in_artifact(
            subtitles_path, subtitle_manifest_path, caption_style=options.caption_style,
        ) as burn_in_path:
            font_directory = fonts_dir or Path(__file__).resolve().parent / "assets" / "fonts"
            render_font_directory = _prepare_ass_font_directory(task_dir, font_directory)
            subtitle_filter = _build_subtitle_filter(task_dir, burn_in_path, render_font_directory)
            await _mix_validated_narration(
                task_dir, video_only_path, narration_path, final_path,
                subtitle_filter=subtitle_filter,
                render_font_directory=render_font_directory,
                target_loudness_lufs=target_loudness_lufs,
                target_loudness_range=target_loudness_range,
                maximum_true_peak_dbfs=maximum_true_peak_dbfs,
                options=options,
            )
    except ValueError as exc:
        raise RenderingError(f"字幕模板绑定校验失败：{exc}") from exc


async def _mix_validated_narration(
    task_dir: Path,
    video_only_path: Path,
    narration_path: Path,
    final_path: Path,
    *,
    subtitle_filter: str,
    render_font_directory: Path,
    target_loudness_lufs: float,
    target_loudness_range: float,
    maximum_true_peak_dbfs: float,
    options: FinishOptions,
) -> None:
    # Narration is already assembled from TTS + recorded units. Never denoise
    # this whole track here (nor on replacement); that would also alter TTS and
    # repeatedly process recordings. The recorded-only DSP lives in stage 7.
    if not options.enabled:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_only_path),
            "-i",
            str(narration_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            subtitle_filter,
            "-af",
            (
                f"loudnorm=I={target_loudness_lufs}:LRA={target_loudness_range}:"
                f"TP={maximum_true_peak_dbfs},aresample=48000"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final_path),
        ]
        await run_logged_command(command, task_dir, "混音并烧录字幕")
        return

    total_duration: float | None = None
    if options.fade_out > 0 or options.music is not None:
        total_duration = await probe_audio_duration(narration_path, task_dir)

    graphics_filter = None
    if options.graphics_path is not None:
        if not options.graphics_path.is_file():
            raise RenderingError("新闻图文包装 ASS 文件不存在。")
        graphics_filter = _build_graphics_filter(options.graphics_path, render_font_directory)
    video_filter = _build_finish_video_filter(
        subtitle_filter,
        graphics_filter,
        options.fade_in,
        options.fade_out,
        total_duration,
    )

    if options.mode_contract and options.music is None:
        # Stage 7 has normalized EVERY unit, including source quotes. A visual
        # revision must not encode/process that same recorded audio again.
        # Do not use -shortest: delivery must keep the entire last AAC packet.
        await run_logged_command([
            "ffmpeg", "-y", "-i", str(video_only_path), "-i", str(narration_path),
            "-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart", str(final_path),
        ], task_dir, "合成三模式画面与原样音频包（无二次原声处理）")
        return

    if options.music is not None:
        assert total_duration is not None
        music_bed_path = task_dir / "music_bed.m4a"
        await prepare_music_bed(
            task_dir,
            options.music.track,
            target_duration=total_duration,
            target_lufs=options.music.bed_lufs,
            output_path=music_bed_path,
        )
        audio_graph = _music_audio_filtergraph(
            options.music.duck_ratio,
            target_loudness_lufs,
            target_loudness_range,
            maximum_true_peak_dbfs,
        )
        if options.mode_contract:
            from .tts_pipeline import NARRATION_AUDIO_FORMAT, _two_pass_loudnorm_filter

            # Measure the SAME post-duck/mix signal that pass two will use.
            # The music has a finite bed and duration=first follows the full
            # narration; no -shortest may truncate an interview's last packet.
            with tempfile.TemporaryDirectory(prefix=".mode-music-", dir=task_dir) as directory:
                mixed = Path(directory) / "mixed.wav"
                graph = (
                    f"[0:a]{NARRATION_AUDIO_FORMAT},asplit=2[narr][key];"
                    "[1:a]aresample=48000[music];"
                    f"[music][key]sidechaincompress=threshold=0.03:ratio={options.music.duck_ratio:.3f}:attack=20:release=350[ducked];"
                    "[narr][ducked]amix=inputs=2:normalize=0:duration=first[mixraw]"
                )
                if options.music.speech_intervals is not None:
                    from .audio_filters import speech_duck_filter
                    envelope = speech_duck_filter(options.music.speech_intervals)
                    graph = (
                        f"[0:a]{NARRATION_AUDIO_FORMAT}[narr];"
                        f"[1:a]{NARRATION_AUDIO_FORMAT},{envelope}[ducked];"
                        "[narr][ducked]amix=inputs=2:normalize=0:duration=first[mixraw]"
                    )
                await run_logged_command([
                    "ffmpeg", "-y", "-i", str(narration_path), "-i", str(music_bed_path),
                    "-filter_complex", graph, "-map", "[mixraw]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(mixed),
                ], task_dir, "准备三模式真实音乐闪避混音（不截断原声）")
                normalization = await _two_pass_loudnorm_filter(
                    task_dir, mixed, target_lufs=-20.0, target_lra=5.0, true_peak_dbfs=-3.5, strict=True,
                )
                await run_logged_command([
                    "ffmpeg", "-y", "-i", str(video_only_path), "-i", str(mixed),
                    "-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter,
                    "-af", f"{NARRATION_AUDIO_FORMAT},{normalization},aresample=48000",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final_path),
                ], task_dir, "三模式音乐混音两遍响度归一并烧录字幕")
            return
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_only_path),
            "-i",
            str(narration_path),
            "-i",
            str(music_bed_path),
            "-filter_complex",
            f"[0:v]{video_filter}[v];{audio_graph}",
            "-map",
            "[v]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final_path),
        ]
        await run_logged_command(command, task_dir, "混音（含背景音乐闪避）并烧录字幕")
        return

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_only_path),
        "-i",
        str(narration_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        video_filter,
        "-af",
        (
            f"loudnorm=I={target_loudness_lufs}:LRA={target_loudness_range}:"
            f"TP={maximum_true_peak_dbfs},aresample=48000"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(final_path),
    ]
    await run_logged_command(command, task_dir, "混音并烧录字幕（含图文包装/淡入淡出）")


def _build_graphics_filter(graphics_path: Path, fonts_dir: Path) -> str:
    graphics_value = _escape_filter_path(graphics_path)
    fonts_value = _escape_filter_path(fonts_dir)
    return f"ass=filename='{graphics_value}':fontsdir='{fonts_value}'"


def _build_finish_video_filter(
    subtitle_filter: str,
    graphics_filter: str | None,
    fade_in: float,
    fade_out: float,
    total_duration: float | None,
) -> str:
    parts = [subtitle_filter]
    if graphics_filter:
        parts.append(graphics_filter)
    if fade_in > 0:
        parts.append(f"fade=t=in:st=0:d={fade_in:.3f}")
    if fade_out > 0 and total_duration is not None:
        start = max(0.0, total_duration - fade_out)
        parts.append(f"fade=t=out:st={start:.3f}:d={fade_out:.3f}")
    return ",".join(parts)


def _music_audio_filtergraph(
    duck_ratio: float,
    target_loudness_lufs: float,
    target_loudness_range: float,
    maximum_true_peak_dbfs: float,
) -> str:
    # Sidechain-duck the music bed under the narration key, mix, then lock final loudness.
    return (
        "[1:a]aresample=48000,asplit=2[narr][key];"
        "[2:a]aresample=48000[music];"
        f"[music][key]sidechaincompress=threshold=0.03:ratio={duck_ratio:.3f}:attack=20:release=350[ducked];"
        "[narr][ducked]amix=inputs=2:normalize=0:duration=first[mixraw];"
        f"[mixraw]loudnorm=I={target_loudness_lufs}:LRA={target_loudness_range}:"
        f"TP={maximum_true_peak_dbfs},aresample=48000[aout]"
    )


def _write_segment_manifest(
    task_dir: Path,
    edl: Sequence[EDLItem],
    segment_paths: Sequence[Path],
) -> list[SegmentManifestItem]:
    manifest: list[SegmentManifestItem] = []
    cursor = 0
    for item in edl:
        count = len(item.clips)
        item_paths = segment_paths[cursor : cursor + count]
        if len(item_paths) != count:
            raise RenderingError("视频片段数量与 EDL 不一致。")
        manifest.append(
            SegmentManifestItem(
                sentence_id=item.sentence_id,
                segments=[path.relative_to(task_dir).as_posix() for path in item_paths],
            )
        )
        cursor += count
    if cursor != len(segment_paths):
        raise RenderingError("存在未映射到句子的多余视频片段。")
    write_json_atomic(
        task_dir / "segment_manifest.json",
        [item.model_dump(mode="json") for item in manifest],
    )
    return manifest


async def validate_final_video(
    task_dir: Path,
    final_path: Path,
    narration_path: Path,
    *,
    subtitles_path: Path | None = None,
    subtitle_manifest_path: Path | None = None,
) -> None:
    if not final_path.is_file() or final_path.stat().st_size == 0:
        raise RenderingError("未生成有效 final.mp4。")
    probe = await probe_media(final_path, task_dir)
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise RenderingError("无法读取 final.mp4 流信息。")
    video = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"),
        None,
    )
    if not video or video.get("codec_name") != "h264":
        raise RenderingError("final.mp4 视频编码不是 H.264。")
    if video.get("width") != VIDEO_WIDTH or video.get("height") != VIDEO_HEIGHT:
        raise RenderingError("final.mp4 分辨率不是 1920×1080。")
    frame_rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
    try:
        numerator, denominator = frame_rate.split("/", maxsplit=1)
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError) as exc:
        raise RenderingError("无法读取 final.mp4 帧率。") from exc
    if abs(fps - VIDEO_FPS) > 0.01:
        raise RenderingError("final.mp4 帧率不是 30fps。")
    if not audio or audio.get("codec_name") != "aac":
        raise RenderingError("final.mp4 音频编码不是 AAC。")
    if str(audio.get("sample_rate")) != "48000":
        raise RenderingError("final.mp4 音频采样率不是 48kHz。")
    if (subtitles_path is None) != (subtitle_manifest_path is None):
        raise RenderingError("字幕文件与字幕模板绑定清单必须同时提供。")
    if subtitles_path is not None and subtitle_manifest_path is not None:
        try:
            manifest = json.loads(subtitle_manifest_path.read_text(encoding="utf-8"))
            if manifest.get("mode_contract") is True:
                from .graphics import validate_mode_subtitle_artifacts

                validate_mode_subtitle_artifacts(subtitles_path, subtitle_manifest_path)
            else:
                validate_subtitle_artifacts(subtitles_path, subtitle_manifest_path)
        except ValueError as exc:
            raise RenderingError(f"成片字幕模板绑定复核失败：{exc}") from exc

    final_duration = _probe_format_duration(probe, "final.mp4")
    narration_duration = await probe_audio_duration(narration_path, task_dir)
    if abs(final_duration - narration_duration) > 0.3:
        raise RenderingError(
            f"成片与旁白时长误差过大：成片 {final_duration:.3f}s，旁白 {narration_duration:.3f}s。"
        )
    write_text_log(
        task_dir,
        f"成片校验完成 duration={final_duration:.6f}s narration={narration_duration:.6f}s",
    )


async def _render_segment(
    task_dir: Path,
    clip: EDLClip,
    output_path: Path,
    index: int,
    options: SegmentRenderOptions | None = None,
) -> None:
    task_root = task_dir.resolve()
    source_path = (task_dir / clip.src).resolve()
    if not source_path.is_relative_to(task_root) or not source_path.is_file():
        raise RenderingError(f"片段 {index} 的源素材不存在或路径不安全：{clip.src}")
    source_duration = clip.out_time - clip.in_time
    if source_duration <= 0:
        raise RenderingError(f"片段 {index} 的入出点无效。")
    freeze_pad = clip.freeze_pad or 0.0
    total_duration = source_duration + freeze_pad

    filters = _build_segment_filters(source_duration, freeze_pad, options or SegmentRenderOptions())
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{clip.in_time:.6f}",
        "-i",
        str(source_path),
        "-t",
        f"{total_duration:.6f}",
        "-map",
        "0:v:0",
        "-vf",
        ",".join(filters),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await run_logged_command(command, task_dir, f"渲染视频片段 {index + 1}")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RenderingError(f"视频片段 {index + 1} 渲染失败。")


async def _concatenate_segments(task_dir: Path, segment_paths: Sequence[Path], output_path: Path) -> None:
    concat_path = task_dir / "segments" / "concat.txt"
    concat_path.write_text(
        "".join(f"file '{_escape_concat_path(path.resolve())}'\n" for path in segment_paths),
        encoding="utf-8",
    )
    copy_command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        "-an",
        str(output_path),
    ]
    try:
        await run_logged_command(copy_command, task_dir, "无损合并视频片段")
    except MediaProcessingError as exc:
        output_path.unlink(missing_ok=True)
        write_text_log(task_dir, f"无损 concat 失败，自动降级为重编码合并：{exc}")
        reencode_command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output_path),
        ]
        await run_logged_command(reencode_command, task_dir, "重编码合并视频片段")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RenderingError("视频片段合并未生成有效 video_only.mp4。")


def _build_subtitle_filter(task_dir: Path, subtitles_path: Path, fonts_dir: Path) -> str:
    font_files = _noto_sans_sc_font_files(fonts_dir)
    subtitle_value = _escape_filter_path(subtitles_path)
    resolved_fonts_dir = fonts_dir
    if not font_files:
        system_fonts_dir = _find_windows_noto_sans_sc_dir()
        if system_fonts_dir is None:
            raise RenderingError(
                f"未找到 {SUBTITLE_FONT_NAME} 字体。请将 Noto Sans SC 的 .ttf、.otf 或 .ttc 文件"
                "放入 backend/assets/fonts，禁止以不确定的系统字体替代播出模板。"
            )
        resolved_fonts_dir = system_fonts_dir
        write_text_log(
            task_dir,
            f"backend/assets/fonts 未提供字体，已验证并使用系统 {SUBTITLE_FONT_NAME}：{system_fonts_dir}",
        )
    fonts_value = _escape_filter_path(resolved_fonts_dir)
    return f"ass=filename='{subtitle_value}':fontsdir='{fonts_value}'"


def _prepare_ass_font_directory(task_dir: Path, fonts_dir: Path) -> Path:
    font_files = _font_files(fonts_dir)
    if not font_files:
        return fonts_dir
    runtime_dir = task_dir / ".ass-fonts"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    expected_names = {font.name for font in font_files}
    for existing in runtime_dir.iterdir():
        if existing.is_file() and existing.name not in expected_names:
            existing.unlink()
    for font in font_files:
        destination = runtime_dir / font.name
        if not destination.is_file() or destination.stat().st_size != font.stat().st_size:
            shutil.copy2(font, destination)
    return runtime_dir


def _font_files(fonts_dir: Path) -> list[Path]:
    if not fonts_dir.is_dir():
        return []
    return [
        path
        for path in fonts_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}
    ]


def _noto_sans_sc_font_files(fonts_dir: Path) -> list[Path]:
    return [
        path
        for path in _font_files(fonts_dir)
        if "".join(character for character in path.stem.lower() if character.isalnum()).startswith(
            "notosanssc"
        )
    ]


def _find_windows_noto_sans_sc_dir() -> Path | None:
    windows_directory = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates = [windows_directory]
    if local_app_data:
        candidates.append(Path(local_app_data) / "Microsoft" / "Windows" / "Fonts")
    for directory in candidates:
        if not directory.is_dir():
            continue
        if _noto_sans_sc_font_files(directory):
            return directory
    return None


def _escape_filter_path(path: Path) -> str:
    value = path.resolve().as_posix().replace("\\", "/")
    for character in ("\\", ":", "'", ",", "[", "]", ";"):
        value = value.replace(character, f"\\{character}")
    return value


def _escape_concat_path(path: Path) -> str:
    return path.as_posix().replace("'", "'\\''")


def _deduplicate(values: Sequence[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _probe_format_duration(probe: dict[str, object], file_name: str) -> float:
    format_info = probe.get("format")
    if not isinstance(format_info, dict):
        raise RenderingError(f"无法读取 {file_name} 时长。")
    try:
        duration = float(format_info.get("duration"))
    except (TypeError, ValueError) as exc:
        raise RenderingError(f"无法读取 {file_name} 时长。") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RenderingError(f"{file_name} 时长无效。")
    return duration


def _mode_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def render_mode_video(
    task_dir: Path, edl: Sequence[EDLItem], progress: RenderProgressCallback, *,
    clip_options: dict[int, SegmentRenderOptions], source_hashes: dict[str, str],
    finish_options: FinishOptions, cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Content-bound clip cache, real frames only, globally bounded frame rounding.

    Continuous same-source clips share one source-frame grid. Each next clip
    starts at the previous emitted end frame, never independently floor(in*30).
    The cache binds this actual start and frame count; timeline edits that change
    frame phase must invalidate it rather than replay a duplicated boundary.
    Prefix copies are derivatives, never overwritten caches. No tpad/loop/zoompan.
    """
    from .revisions import local_file

    require_media_tools()
    _validate_unique_edl_shots(edl)
    old = cache or {}
    entries: dict[str, Any] = {}
    cached_paths: list[Path] = []
    assembled_paths: list[Path] = []
    actual_frames: list[dict[str, Any]] = []
    total_clips = sum(len(item.clips) for item in edl)
    if not total_clips:
        raise RenderingError("三模式 EDL 为空。")
    segments_dir = task_dir / "segments" / "mode"
    segments_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mode-frames-", dir=task_dir) as temporary:
        index = 0
        previous_source = None
        previous_out = previous_timeline_end = None
        previous_end_frame = 0
        source_frames: dict[str, int | None] = {}
        for item in edl:
            cursor = item.timeline_start
            for clip in item.clips:
                if clip.freeze_pad:
                    raise RenderingError("三模式不允许用定格掩盖素材不足。")
                duration = clip.out_time - clip.in_time
                if not math.isfinite(duration) or duration <= 0:
                    raise RenderingError("三模式片段范围无效。")
                source = local_file(task_dir, clip.src)
                if clip.src not in source_hashes:
                    raise RenderingError("三模式片段缺少源媒体绑定。")
                options = clip_options.get(clip.shot_id, SegmentRenderOptions())
                next_cursor = cursor + duration
                want = math.ceil(next_cursor * VIDEO_FPS - 1e-8) - math.ceil(cursor * VIDEO_FPS - 1e-8)
                if want <= 0:
                    raise RenderingError("时间线片段短于一帧。")
                # Keep one optional real tail frame in the reusable clip so a
                # changed timeline phase can still use a lossless prefix. The
                # NEXT source in is based on emitted want, never cached frames.
                frames = max(want, math.ceil(duration * VIDEO_FPS - 1e-8))
                seek_frame = math.floor(clip.in_time * VIDEO_FPS + 1e-8)
                if clip.src not in source_frames:
                    probed = await probe_media(source, task_dir)
                    stream = next((s for s in probed.get("streams", []) if s.get("codec_type") == "video"), {})
                    count = stream.get("nb_frames")
                    source_frames[clip.src] = int(count) if str(count).isdigit() else None
                continuous = (previous_source == source_hashes[clip.src]
                              and previous_out is not None and abs(previous_out - clip.in_time) < 1e-8
                              and previous_timeline_end is not None and abs(previous_timeline_end - cursor) < 1e-8)
                if continuous:
                    seek_frame = previous_end_frame
                available = source_frames.get(clip.src)
                if available is not None and want <= available - seek_frame < frames:
                    # The optional tail frame would run past the source's real last frame (a clip that ends
                    # exactly at the end of a video): drop that optional frame instead of failing.
                    frames = available - seek_frame
                signature = {
                    "recipe": "mode-continuous-source-grid-v2", "source": source_hashes[clip.src],
                    "in_frame": seek_frame, "frames": frames,
                    "options": asdict(options),
                }
                key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
                relative = f"segments/mode/{key}.mp4"
                path = local_file(task_dir, relative, exists=False)
                previous = old.get(key)
                reused = bool(previous and path.is_file())
                if reused:
                    if _mode_file_hash(path) != previous.get("sha256"):
                        raise RenderingError("模式片段缓存发生变化，不能当作既有媒体复用。")
                else:
                    # Input normalized video is already 30fps. Do not invoke fps
                    # here: an insufficient source must fail rather than duplicate EOF.
                    filters = ["setpts=PTS-STARTPTS"]
                    if options.color_consistency:
                        filters.append(_COLOR_CONSISTENCY_FILTER)
                    if options.jump_zoom_frames:
                        filters.append(_ken_burns_filter(options.jump_zoom_frames, options.jump_zoom_ratio))
                    elif options.motion and options.zoom_ratio > 0:
                        filters.append(_ken_burns_filter(frames, options.zoom_ratio))
                    filters.append("format=yuv420p")
                    await run_logged_command([
                        "ffmpeg", "-y", "-ss", f"{seek_frame / VIDEO_FPS:.9f}", "-i", str(source),
                        "-map", "0:v:0", "-frames:v", str(frames), "-vf", ",".join(filters),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-bf", "0",
                        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(path),
                    ], task_dir, f"渲染模式真实片段 sentence={item.sentence_id} shot={clip.shot_id}")
                    probe = await probe_media(path, task_dir)
                    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
                    if int(video.get("nb_frames", 0)) != frames:
                        raise RenderingError("源媒体真实帧不足，拒绝冻结、重复边缘帧或伪造遮盖。")
                entries[key] = {"path": relative, "sha256": _mode_file_hash(path), **signature}
                cached_paths.append(path)
                next_cursor = cursor + duration
                want = math.ceil(next_cursor * VIDEO_FPS - 1e-8) - math.ceil(cursor * VIDEO_FPS - 1e-8)
                if not 0 < want <= frames:
                    raise RenderingError("时间线片段短于一帧或源帧数不足。")
                assembled = path
                if want < frames:
                    assembled = Path(temporary) / f"{index}.mp4"
                    await run_logged_command([
                        "ffmpeg", "-y", "-i", str(path), "-map", "0:v:0", "-frames:v", str(want),
                        "-c:v", "copy", "-an", str(assembled),
                    ], task_dir, "按全片帧时钟无损选择缓存前缀")
                assembled_paths.append(assembled)
                actual_frames.append({
                    "sentence_id": item.sentence_id, "shot_id": clip.shot_id,
                    "requested_source_in": clip.in_time, "actual_source_in": seek_frame / VIDEO_FPS,
                    "actual_source_out": (seek_frame + want) / VIDEO_FPS,
                    "continuous_source_grid": continuous,
                    "source_clock_quantization_max_seconds": 2 / VIDEO_FPS if continuous else 1 / VIDEO_FPS,
                    "source_clock_error_seconds": seek_frame / VIDEO_FPS - clip.in_time,
                    "timeline_start_frame": math.ceil(cursor * VIDEO_FPS - 1e-8),
                    "frames": want, "cached_frames": frames, "reused": reused,
                    "jump_zoom_frames": options.jump_zoom_frames,
                })
                previous_source, previous_out = source_hashes[clip.src], clip.out_time
                previous_timeline_end, previous_end_frame = next_cursor, seek_frame + want
                cursor = next_cursor
                index += 1
                progress(0.7 * index / total_clips, f"{'复用' if reused else '渲染'}真实片段 {index}/{total_clips}")
            if abs(cursor - item.timeline_end) > 1e-5:
                raise RenderingError("模式画面时长与真实音频时间线不一致。")
        _write_segment_manifest(task_dir, edl, cached_paths)
        await _concatenate_segments(task_dir, assembled_paths, task_dir / "video_only.mp4")
    progress(0.8, "真实片段拼接完成（未填充冻结帧）")
    await _mix_subtitles_and_narration(
        task_dir, task_dir / "video_only.mp4", finish_options.narration_path or task_dir / "narration.m4a", task_dir / "subs.ass",
        task_dir / "final.mp4", subtitle_manifest_path=task_dir / "subtitle_manifest.json",
        target_loudness_lufs=-20.0, target_loudness_range=5.0, maximum_true_peak_dbfs=-3.5,
        finish_options=finish_options,
    )
    await validate_final_video(
        task_dir, task_dir / "final.mp4", task_dir / "narration.m4a",
        subtitles_path=task_dir / "subs.ass", subtitle_manifest_path=task_dir / "subtitle_manifest.json",
    )
    progress(1.0, "成片规格与模式字幕绑定校验完成")
    return {"clips": entries, "frames": actual_frames,
            "rendered_clips": sum(not entry["reused"] for entry in actual_frames),
            "reused_clips": sum(entry["reused"] for entry in actual_frames)}
