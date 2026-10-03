import asyncio
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .asr_pipeline import SourceASRRecord
from .media import MediaProcessingError, run_logged_command
from .models import (
    AnnotatedShot,
    Shot,
    ShotSemanticWindow,
    ShotTranscriptSpan,
    VisionAnnotation,
)
from .storage import write_text_log


VisionProgressCallback = Callable[[int, int, str], None]
MAX_VISION_CONCURRENCY = 4


class VisionClient(Protocol):
    def validate_configuration(self) -> None:
        ...

    async def annotate_image(self, image_path: Path) -> VisionAnnotation:
        ...


class VisionProcessingError(RuntimeError):
    """Raised when stage 4 cannot produce any usable shot annotations."""


async def annotate_shots(
    task_dir: Path,
    shots: Sequence[Shot],
    provider: VisionClient,
    progress: VisionProgressCallback,
    *,
    concurrency: int = MAX_VISION_CONCURRENCY,
    source_records: Sequence[SourceASRRecord] = (),
) -> list[AnnotatedShot]:
    if not shots:
        raise VisionProcessingError("没有可供画面理解的镜头。")
    provider.validate_configuration()

    worker_limit = min(MAX_VISION_CONCURRENCY, max(1, concurrency))
    semaphore = asyncio.Semaphore(worker_limit)
    progress_lock = asyncio.Lock()
    results: list[AnnotatedShot | None] = [None] * len(shots)
    completed = 0
    source_records_by_index = {record.source_index: record for record in source_records}

    async def process_shot(index: int, shot: Shot) -> None:
        nonlocal completed
        thumb_path: Path | None = None
        async with semaphore:
            try:
                thumb_path = await extract_shot_thumbnail(task_dir, shot)
                try:
                    analysis_image_path = await extract_shot_contact_sheet(task_dir, shot)
                    analyzed_frames = 5
                except Exception as exc:
                    analysis_image_path = thumb_path
                    analyzed_frames = 1
                    write_text_log(
                        task_dir,
                        f"镜头 {shot.shot_id} 多帧分析图生成失败，回退中点帧：{exc}",
                    )
                annotation = await provider.annotate_image(analysis_image_path)
                source_transcript = _shot_source_transcript(
                    shot,
                    source_records_by_index.get(shot.source_index),
                )
                transcript_spans = _shot_source_transcript_spans(
                    shot,
                    source_records_by_index.get(shot.source_index),
                )
                search_text = _build_shot_search_text(annotation, source_transcript)
                annotated = AnnotatedShot(
                    **shot.model_dump(),
                    thumb_path=thumb_path.relative_to(task_dir).as_posix(),
                    status="available",
                    description=annotation.description,
                    scene_type=annotation.scene_type,
                    subjects=annotation.subjects,
                    actions=annotation.actions,
                    keywords=annotation.keywords,
                    ocr_texts=annotation.ocr_texts,
                    entities=annotation.entities,
                    source_transcript=source_transcript,
                    source_transcript_spans=transcript_spans,
                    semantic_windows=_build_shot_semantic_windows(
                        shot,
                        annotation,
                        transcript_spans,
                    ),
                    search_text=search_text,
                    quality=annotation.quality,
                )
                write_text_log(
                    task_dir,
                    f"镜头 {shot.shot_id} description={annotation.description} "
                    f"asr_chars={len(source_transcript)} frames={analyzed_frames}",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_message = str(exc)[:1000]
                annotated = AnnotatedShot(
                    **shot.model_dump(),
                    thumb_path=thumb_path.relative_to(task_dir).as_posix() if thumb_path else None,
                    status="unavailable",
                    error=error_message,
                )
                write_text_log(task_dir, f"镜头 {shot.shot_id} unavailable={error_message}")

        async with progress_lock:
            results[index] = annotated
            completed += 1
            progress(completed, len(shots), f"画面理解 {completed}/{len(shots)}")

    await asyncio.gather(*(process_shot(index, shot) for index, shot in enumerate(shots)))
    annotated_shots = [result for result in results if result is not None]
    output_path = task_dir / "shots_annotated.json"
    output_path.write_text(
        json.dumps([shot.model_dump(mode="json") for shot in annotated_shots], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    available_count = sum(shot.status == "available" for shot in annotated_shots)
    if available_count == 0:
        first_error = next((shot.error for shot in annotated_shots if shot.error), "未知错误")
        raise VisionProcessingError(
            "所有镜头画面理解均失败，请检查 VOLCENGINE_VISION_BASE_URL / "
            "VOLCENGINE_VISION_API_KEYS / VOLCENGINE_VISION_MODEL 配置。"
            f"首个错误：{first_error}"
        )
    return annotated_shots


async def extract_shot_thumbnail(task_dir: Path, shot: Shot) -> Path:
    task_root = task_dir.resolve()
    source_path = (task_dir / shot.norm_path).resolve()
    if not source_path.is_relative_to(task_root) or not source_path.is_file():
        raise MediaProcessingError(f"镜头 {shot.shot_id} 的规格化素材不存在或路径不安全。")

    thumbs_dir = task_dir / "thumbs"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    output_path = thumbs_dir / f"shot_{shot.shot_id}.jpg"
    midpoint = shot.start + shot.duration / 2.0
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-ss",
        f"{midpoint:.3f}",
        "-frames:v",
        "1",
        "-vf",
        "scale=512:512:force_original_aspect_ratio=decrease",
        "-q:v",
        "2",
        "-update",
        "1",
        str(output_path),
    ]
    await run_logged_command(command, task_dir, f"提取镜头关键帧 shot={shot.shot_id}")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise MediaProcessingError(f"镜头 {shot.shot_id} 未生成有效关键帧。")
    return output_path


async def extract_shot_contact_sheet(task_dir: Path, shot: Shot, *, frame_count: int = 5) -> Path:
    if frame_count < 2:
        raise ValueError("多帧画面理解至少需要两帧。")
    task_root = task_dir.resolve()
    source_path = (task_dir / shot.norm_path).resolve()
    if not source_path.is_relative_to(task_root) or not source_path.is_file():
        raise MediaProcessingError(f"镜头 {shot.shot_id} 的规格化素材不存在或路径不安全。")
    sheets_dir = task_dir / "thumbs" / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    output_path = sheets_dir / f"shot_{shot.shot_id}.jpg"
    await asyncio.to_thread(
        _write_contact_sheet,
        source_path,
        output_path,
        shot.start,
        shot.duration,
        frame_count,
    )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise MediaProcessingError(f"镜头 {shot.shot_id} 未生成有效多帧分析图。")
    return output_path


def _write_contact_sheet(
    source_path: Path,
    output_path: Path,
    shot_start: float,
    shot_duration: float,
    frame_count: int,
) -> None:
    capture = cv2.VideoCapture(str(source_path))
    try:
        if not capture.isOpened():
            raise MediaProcessingError(f"无法打开镜头素材：{source_path.name}")
        cell_width = 384
        cell_height = 216
        columns = 3
        rows = (frame_count + columns - 1) // columns
        canvas = np.zeros((rows * cell_height, columns * cell_width, 3), dtype=np.uint8)
        for index in range(frame_count):
            ratio = (index + 0.5) / frame_count
            relative_time = shot_duration * ratio
            capture.set(cv2.CAP_PROP_POS_MSEC, (shot_start + relative_time) * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            scale = min(cell_width / width, cell_height / height)
            resized = cv2.resize(
                frame,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            cell = np.zeros((cell_height, cell_width, 3), dtype=np.uint8)
            y = (cell_height - resized.shape[0]) // 2
            x = (cell_width - resized.shape[1]) // 2
            cell[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
            cv2.putText(
                cell,
                f"T+{relative_time:.1f}s",
                (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            row, column = divmod(index, columns)
            canvas[
                row * cell_height : (row + 1) * cell_height,
                column * cell_width : (column + 1) * cell_width,
            ] = cell
        if not cv2.imwrite(str(output_path), canvas, [cv2.IMWRITE_JPEG_QUALITY, 92]):
            raise MediaProcessingError(f"无法写入多帧分析图：{output_path.name}")
    finally:
        capture.release()


def _shot_source_transcript(shot: Shot, record: SourceASRRecord | None) -> str:
    if record is None or record.status != "available" or record.transcript is None:
        return ""
    overlapping = [span.text for span in _shot_source_transcript_spans(shot, record)]
    transcript = "".join(overlapping) or record.transcript.text.strip()
    return transcript[:800]


def _shot_source_transcript_spans(
    shot: Shot,
    record: SourceASRRecord | None,
) -> list[ShotTranscriptSpan]:
    if record is None or record.status != "available" or record.transcript is None:
        return []
    spans: list[ShotTranscriptSpan] = []
    for utterance in record.transcript.utterances:
        utterance_start = utterance.start_time_ms / 1000.0
        utterance_end = utterance.end_time_ms / 1000.0
        text = utterance.text.strip()
        if (
            not utterance.definite
            or not text
            or utterance_end <= shot.start
            or utterance_start >= shot.end
        ):
            continue
        spans.append(
            ShotTranscriptSpan(
                text=text[:800],
                start=max(shot.start, utterance_start),
                end=min(shot.end, utterance_end),
            )
        )
    return spans


def _build_shot_semantic_windows(
    shot: Shot,
    annotation: VisionAnnotation,
    transcript_spans: list[ShotTranscriptSpan],
    *,
    window_seconds: float = 4.0,
) -> list[ShotSemanticWindow]:
    visual_text = " ".join(
        [
            annotation.description,
            *annotation.subjects,
            *annotation.actions,
            *annotation.keywords,
            *annotation.ocr_texts,
            *annotation.entities,
        ]
    ).strip()
    windows: list[ShotSemanticWindow] = []
    start = shot.start
    while start < shot.end - 0.001:
        end = min(shot.end, start + window_seconds)
        transcript = "".join(
            span.text
            for span in transcript_spans
            if span.end > start and span.start < end
        )
        windows.append(
            ShotSemanticWindow(
                start=round(start, 6),
                end=round(end, 6),
                text=" ".join(part for part in (visual_text, transcript) if part),
            )
        )
        start = end
    return windows


def _build_shot_search_text(annotation: VisionAnnotation, source_transcript: str) -> str:
    parts = [
        f"画面描述：{annotation.description}",
        f"主体：{'、'.join(annotation.subjects)}" if annotation.subjects else "",
        f"动作：{'、'.join(annotation.actions)}" if annotation.actions else "",
        f"视觉关键词：{'、'.join(annotation.keywords)}",
        f"画面文字：{'、'.join(annotation.ocr_texts)}" if annotation.ocr_texts else "",
        f"实体：{'、'.join(annotation.entities)}" if annotation.entities else "",
        f"现场语音识别：{source_transcript}" if source_transcript else "",
    ]
    return " ".join(part for part in parts if part)
