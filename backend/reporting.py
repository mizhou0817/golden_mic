import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .quality import MODE_CHECK_CODE_ALIASES
from .models import (
    AnnotatedShot,
    MatchPlanItem,
    QualitySummary,
    ReportBeatRow,
    ReportResponse,
    ReportRow,
    SentenceTiming,
)


class ReportGenerationError(RuntimeError):
    """Raised when the final matching report cannot be generated consistently."""


def mode_report_metadata(task_dir: Path) -> dict[str, Any]:
    """Public mode metadata only; no paths, providers, tokens or ASR internals."""
    path = task_dir / "production_mode.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("mode") not in {"voiceover", "mixed", "original"}:
        raise ReportGenerationError("制作模式清单无效。")
    quality_path = task_dir / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.is_file() else {}
    return {
        "mode": data["mode"], "speakers": data.get("speakers", []),
        "jumpcuts": data.get("jumpcuts", []),
        "checks": quality.get("checks", quality.get("issues", [])),
        "metrics": {"broll_available": bool(data.get("broll_available")),
                    "check_code_aliases": MODE_CHECK_CODE_ALIASES,
                    "lower_third_count": len(data.get("lower_thirds", [])),
                    "quote_count": sum(row.get("kind") == "quote" for row in data.get("rows", [])),
                    "confirmation_count": quality.get("blocking_issue_count", 0) + quality.get("warning_count", 0)},
    }


def generate_report(
    task_dir: Path,
    task_id: str,
    shots: Sequence[AnnotatedShot],
    match_plan: Sequence[MatchPlanItem],
    timings: Sequence[SentenceTiming],
    *,
    final_video_path: Path | None = None,
    output_path: Path | None = None,
    quality_report: dict[str, object] | None = None,
) -> ReportResponse:
    final_video_path = final_video_path or task_dir / "final.mp4"
    if not final_video_path.is_file():
        raise ReportGenerationError("final.mp4 不存在，无法完成任务。")

    shots_by_id = {shot.shot_id: shot for shot in shots}
    timings_by_id = {timing.sentence_id: timing for timing in timings}
    if not match_plan:
        raise ReportGenerationError("match_plan 为空，无法生成匹配报告。")
    if {item.sentence_id for item in match_plan} != set(timings_by_id):
        raise ReportGenerationError("match_plan 与 timings 的句子集合不一致。")

    rows: list[ReportRow] = []
    metadata = mode_report_metadata(task_dir)
    for item in (match_plan if metadata else sorted(match_plan, key=lambda match: match.sentence_id)):
        shot = shots_by_id.get(item.shot_id)
        timing = timings_by_id[item.sentence_id]
        if shot is None or shot.status != "available" or not shot.description:
            raise ReportGenerationError(f"句子 {item.sentence_id} 引用了不可用镜头 {item.shot_id}。")
        thumb_path = task_dir / "thumbs" / f"shot_{shot.shot_id}.jpg"
        if not thumb_path.is_file():
            raise ReportGenerationError(f"镜头 {shot.shot_id} 的缩略图不存在。")
        rows.append(
            ReportRow(
                sentence_id=item.sentence_id,
                sentence=item.text,
                kind=item.kind,
                source=item.source,
                alt_takes=item.alt_takes,
                trim=item.trim,
                to_narration=item.to_narration,
                jumpcut_before=item.jumpcut_before,
                shot_id=shot.shot_id,
                thumb_url=f"/api/tasks/{task_id}/thumbs/{shot.shot_id}.jpg",
                description=shot.description,
                duration=timing.duration,
                confidence=item.confidence,
                is_fallback=item.is_fallback,
                audio_kind=timing.audio_kind,
                spoken_text=timing.text if timing.audio_kind == "sync" else None,
                replacement_instruction=item.replacement_instruction,
                overlay_kind=item.overlay_kind,
                overlay_text=item.overlay_text,
                visual_beats=[
                    ReportBeatRow(
                        beat_id=beat.beat_id,
                        text=beat.text,
                        shot_id=beat.shot_id,
                        thumb_url=f"/api/tasks/{task_id}/thumbs/{beat.shot_id}.jpg",
                        description=(shots_by_id[beat.shot_id].description or ""),
                        confidence=beat.confidence,
                        requires_entity_coverage=beat.requires_entity_coverage,
                    )
                    for beat in item.beat_matches
                    if beat.shot_id in shots_by_id
                    and shots_by_id[beat.shot_id].status == "available"
                    and shots_by_id[beat.shot_id].description
                ],
            )
        )

    quality = QualitySummary.model_validate(quality_report) if quality_report is not None else None
    if metadata and quality_report is not None:
        metadata["checks"] = quality_report.get("checks", quality_report.get("issues", []))
    report = ReportResponse(task_id=task_id, rows=rows, quality=quality, **metadata)
    final_path = output_path or task_dir / "report.json"
    temporary_path = final_path.with_name(f"{final_path.name}.tmp")
    temporary_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(final_path)
    return report
