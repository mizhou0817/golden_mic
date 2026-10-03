"""Offline motion audit/re-render. Never publishes or overwrites a managed work.

Uses the saved EDL, normalized footage, ASS and selected music, not model calls.
An unused output directory is mandatory. The deliverable copies the original
compressed audio stream exactly; original classroom state/QC stays untouched.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from backend.models import EDLClip, EDLItem, EditingPreferences
from backend.music import load_music_library
from backend.provenance import PIPELINE_IMPLEMENTATION_VERSION
from backend.rendering import (
    FinishOptions,
    MusicMixOptions,
    SegmentRenderOptions,
    _build_segment_filters,  # pyright: ignore[reportPrivateUsage]
    render_final_video,
)
from backend.revisions import local_file
from tests.test_motion_rendering import marker_centers, marker_frames


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_SIZE = (960, 540)
MAX_COPY_BYTES = 8 * 1024**3
RENDER_ALLOWANCE_BYTES = 2 * 1024**3
MIN_FREE_BYTES = 5 * 1024**3


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _write(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, ensure_ascii=True, indent=2, allow_nan=False)
        output.write("\n")


def _run(command: list[str], *, timeout: int = 120) -> bytes:
    result = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-4000:])
    return result.stdout


def _probe(path: Path) -> dict[str, Any]:
    return json.loads(_run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]))


def _frame_clock(path: Path) -> list[tuple[int, int]]:
    data = json.loads(_run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=pts,duration", "-of", "json", str(path),
    ]))
    clock: list[tuple[int, int]] = []
    for index, frame in enumerate(data["frames"]):
        try:
            pts, duration = int(frame["pts"]), int(frame["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Clock unavailable: {path.name}, frame {index}") from exc
        if duration <= 0 or (clock and pts != clock[-1][0] + clock[-1][1]):
            raise RuntimeError(f"Invalid or discontinuous clock: {path.name}, frame {index}")
        clock.append((pts, duration))
    if not clock:
        raise RuntimeError(f"No frame clock: {path.name}")
    return clock


def _audio_packets(path: Path) -> list[dict[str, Any]]:
    data = json.loads(_run([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_packets",
        "-show_entries", "packet=pts,dts,duration,size,data_hash", "-show_data_hash", "sha256",
        "-of", "json", str(path),
    ]))
    return data["packets"]


def _legacy_filter(frames: int, ratio: float) -> str:
    # Exact historical filter, only for the diagnostic reference. Never used for
    # the fixed film. Its match to the original task log is checked below.
    return (
        "scale=2560:1440:flags=bicubic,"
        f"zoompan=z='min(1+{ratio:.6f}*on/{max(frames - 1, 1)},{1 + ratio:.6f})':d=1:"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30"
    )


def _synthetic_measurements() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, filters in (
        ("motion_off", ",".join(_build_segment_filters(3, 0, SegmentRenderOptions()))),
        ("color_only", ",".join(_build_segment_filters(3, 0, SegmentRenderOptions(color_consistency=True)))),
        ("legacy_motion", _legacy_filter(90, 0.08) + ",format=yuv420p"),
        ("fixed_motion", ",".join(_build_segment_filters(3, 0, SegmentRenderOptions(motion=True)))),
        ("fixed_motion_and_color", ",".join(_build_segment_filters(3, 0, SegmentRenderOptions(motion=True, color_consistency=True)))),
    ):
        images = marker_frames(filters, 90)
        center = marker_centers(images[:, :160, :160])
        step = np.linalg.norm(np.diff(center, axis=0), axis=1)
        result[name] = {
            "frame_count": len(images), "center_peak_to_peak_px": np.ptp(center, axis=0).tolist(),
            "max_center_step_px": float(step.max()), "centers_px": center.tolist(),
        }
    return result


def _gray(path: Path, count: int, *, clip: EDLClip | None = None) -> NDArray[np.uint8]:
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    filters: list[str] = []
    if clip is not None:
        command += ["-ss", f"{clip.in_time:.6f}"]
        filters = _build_segment_filters(clip.out_time - clip.in_time, clip.freeze_pad or 0, SegmentRenderOptions())
    command += ["-i", str(path)]
    filters += ["scale=960:540:flags=area", "format=gray"]
    raw = _run(command + [
        "-vf", ",".join(filters), "-frames:v", str(count), "-fps_mode", "passthrough", "-an",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ])
    images = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 540, 960)
    if len(images) != count:
        raise RuntimeError(f"Frame count mismatch for {path.name}: {len(images)} != {count}")
    return images


def _center_residual(source: NDArray[np.uint8], output: NDArray[np.uint8]) -> tuple[list[float], int]:
    """Same-time source→output geometry, so camera/subject motion cancels out.

    General affine (not translation-only) fit separates intentional zoom from
    unwanted center translation. All reported coordinates are 1080p pixels.
    """
    # OpenCV stubs omit None on unsuccessful detections. Keep the runtime guard.
    points = cast(NDArray[np.float32] | None, cv2.goodFeaturesToTrack(
        source, maxCorners=400, qualityLevel=0.02, minDistance=12, blockSize=7,
    ))
    if points is None or len(points) < 40:
        raise RuntimeError("Insufficient source features for motion measurement")
    found, status, _ = cast(
        tuple[NDArray[np.float32] | None, NDArray[np.uint8] | None, NDArray[np.float32] | None],
        cv2.calcOpticalFlowPyrLK(source, output, points, np.empty_like(points), winSize=(31, 31), maxLevel=4),
    )
    if found is None or status is None:
        raise RuntimeError("Could not match source/output features")
    returned, reverse, _ = cast(
        tuple[NDArray[np.float32] | None, NDArray[np.uint8] | None, NDArray[np.float32] | None],
        cv2.calcOpticalFlowPyrLK(output, source, found, np.empty_like(found), winSize=(31, 31), maxLevel=4),
    )
    if returned is None or reverse is None:
        raise RuntimeError("Could not verify source/output matches")
    keep = (status.reshape(-1) == 1) & (reverse.reshape(-1) == 1)
    keep &= np.linalg.norm(points.reshape(-1, 2) - returned.reshape(-1, 2), axis=1) < 0.4
    original = points.reshape(-1, 2)[keep]
    tracked = found.reshape(-1, 2)[keep]
    if len(original) < 40:
        raise RuntimeError("Insufficient verified feature matches")
    matrix, inliers = cast(
        tuple[NDArray[np.float64] | None, NDArray[np.uint8] | None],
        cv2.estimateAffine2D(original, tracked, ransacReprojThreshold=0.5, maxIters=2000),
    )
    if matrix is None or inliers is None or int(inliers.sum()) < 30:
        raise RuntimeError("Could not fit a reliable source/output transform")
    center = np.array([480.0, 270.0, 1.0])
    offset = (matrix @ center - center[:2]) * 2
    return offset.tolist(), int(inliers.sum())


def _measure_clip(source: Path, old: Path, fixed: Path, clip: EDLClip, count: int) -> dict[str, Any]:
    original = _gray(source, count, clip=clip)
    result: dict[str, Any] = {"frames": count, "measurement_resolution": list(MEASUREMENT_SIZE)}
    for name, path in (("before", old), ("after", fixed)):
        rendered = _gray(path, count)
        offsets: list[list[float]] = []
        inliers: list[int] = []
        for source_frame, output_frame in zip(original, rendered, strict=True):
            offset, matches = _center_residual(source_frame, output_frame)
            offsets.append(offset)
            inliers.append(matches)
        array: NDArray[np.float64] = np.asarray(offsets, dtype=np.float64)
        steps = np.linalg.norm(np.diff(array, axis=0), axis=1)
        squared: NDArray[np.float64] = array * array
        mean_squared = cast(np.float64, np.mean(np.sum(squared, axis=1)))
        result[name] = {
            "center_rms_px": float(np.sqrt(mean_squared)),
            "center_step_p95_px": float(np.percentile(steps, 95)),
            "center_step_max_px": float(steps.max()),
            "minimum_inliers": min(inliers), "center_offsets_px": offsets,
        }
    return result


async def audit(source: Path, output: Path) -> None:
    source = source.resolve(strict=True)
    output = output.resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Use an unused output directory outside the original task")
    state = _json(local_file(source, "task_state.json"))
    preferences = EditingPreferences.model_validate(state["preferences"])
    if state["status"] != "done" or not preferences.motion_effects:
        raise ValueError("A completed motion-enabled work is required")
    manifest = _json(local_file(source, "pipeline_manifest.json"))
    settings = manifest["settings"]
    ratio = float(settings["motion_zoom_ratio"])
    if not math.isfinite(ratio) or not 0 < ratio <= 0.25:
        raise ValueError("Saved motion zoom must be finite and in (0, 0.25]")
    edl = [EDLItem.model_validate(item) for item in _json(local_file(source, "edl.json"))]
    clips = [clip for item in edl for clip in item.clips]
    if not 1 <= len(clips) <= 64 or not 0 < edl[-1].timeline_end <= 120:
        raise ValueError("This diagnostic is bounded to 64 clips / 120 seconds")
    segment_manifest = _json(local_file(source, "segment_manifest.json"))
    original_segments = [local_file(source, name) for item in segment_manifest for name in item["segments"]]
    if len(original_segments) != len(clips):
        raise ValueError("EDL and segment manifest differ")
    log = local_file(source, "task.log").read_text(encoding="utf-8")
    for clip in clips:
        count = max(1, round((clip.out_time - clip.in_time + (clip.freeze_pad or 0)) * 30))
        if _legacy_filter(count, ratio) not in log:
            raise ValueError("Original log does not match the historical filter")
    names = {
        "task_state.json", "pipeline_manifest.json", "upload_manifest.json", "media_input_manifest.json",
        "edl.json", "source_edl.json", "timings.json", "source_timings.json", "match_plan.json",
        "segment_manifest.json", "quality_report.json", "report.json", "narration.m4a",
        "subs.ass", "subtitle_manifest.json", "final.mp4", "video_only.mp4", "task.log",
        *(clip.src for clip in clips), *(p.relative_to(source).as_posix() for p in original_segments),
    }
    optional = ("graphics.ass", "music_selection.json", "music_bed.m4a", "generated_media_disclosure.json")
    names.update(name for name in optional if (source / name).is_file())
    copy_bytes = sum(local_file(source, name).stat().st_size for name in {c.src for c in clips})
    if copy_bytes > MAX_COPY_BYTES:
        raise ValueError("Referenced source copies exceed the 8 GiB diagnostic cap")
    existing_parent = next(parent for parent in output.parents if parent.exists())
    if shutil.disk_usage(existing_parent).free < copy_bytes + RENDER_ALLOWANCE_BYTES + MIN_FREE_BYTES:
        raise ValueError("Insufficient disk space for source copies, render allowance and safety margin")
    original_hashes = {name: _hash(local_file(source, name)) for name in sorted(names)}
    output.mkdir(parents=True, exist_ok=False)
    binding: dict[str, Any] = {
        "at": datetime.now(timezone.utc).isoformat(), "task_id": state["task_id"],
        "original_revision": state["revision"], "original_files": original_hashes,
        "implementation_version": PIPELINE_IMPLEMENTATION_VERSION,
        "changed_source_hashes": {name: _hash(PROJECT_ROOT / "backend" / name)
                                  for name in ("rendering.py", "readiness.py", "provenance.py")},
        "script_sha256": _hash(Path(__file__)), "provider_calls_planned": 0,
        "original_classroom_work_modified": False,
    }
    _write(output / "source-binding.json", binding)
    synthetic = _synthetic_measurements()
    _write(output / "synthetic-motion.json", synthetic)
    fixed_root = output / "fixed"
    fixed_root.mkdir()
    copy_names = {"narration.m4a", "subs.ass", "subtitle_manifest.json", "edl.json", *(c.src for c in clips)}
    copy_names.update(name for name in ("graphics.ass", "generated_media_disclosure.json") if (source / name).is_file())
    for name in sorted(copy_names):
        target = fixed_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_file(source, name), target)
    music = None
    if preferences.background_music:
        selection = _json(local_file(source, "music_selection.json"))["track"]
        if selection is not None:
            tracks = load_music_library(PROJECT_ROOT / "backend" / "assets" / "music")
            track = next(track for track in tracks if track.file == selection["file"])
            music = MusicMixOptions(track=track, mood=selection["mood"],
                                    bed_lufs=settings["music_bed_target_lufs"], duck_ratio=settings["music_duck_ratio"])
    options = FinishOptions(
        music=music, graphics_path=fixed_root / "graphics.ass" if (fixed_root / "graphics.ass").is_file() else None,
        fade_in=0.5 if preferences.transitions else 0, fade_out=0.6 if preferences.transitions else 0,
        caption_style=preferences.caption_style,
    )
    def progress(fraction: float, _message: str) -> None:
        print(f"Local re-render {fraction:.0%}", flush=True)
    await render_final_video(
        fixed_root, edl, progress, target_loudness_lufs=settings["tts_target_lufs"],
        target_loudness_range=settings["tts_target_lra"], maximum_true_peak_dbfs=settings["tts_true_peak_dbfs"],
        segment_options=SegmentRenderOptions(color_consistency=preferences.color_consistency, motion=True, zoom_ratio=ratio),
        finish_options=options,
    )
    deliverable = output / "news-motion-fixed.mp4"
    _run([
        "ffmpeg", "-v", "error", "-nostdin", "-n", "-i", str(fixed_root / "final.mp4"),
        "-i", str(source / "final.mp4"), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
        "-map_metadata", "-1", "-movflags", "+faststart", str(deliverable),
    ])
    before_probe, after_probe = _probe(source / "final.mp4"), _probe(deliverable)
    clocks = [_frame_clock(path) for path in (source / "final.mp4", deliverable)]
    packets = [_audio_packets(path) for path in (source / "final.mp4", deliverable)]
    if clocks[0] != clocks[1] or packets[0] != packets[1]:
        raise RuntimeError("Output video timing or original compressed audio changed")
    for a, b in zip(before_probe["streams"], after_probe["streams"], strict=True):
        for key in ("codec_type", "codec_name", "width", "height", "sample_aspect_ratio", "pix_fmt",
                    "color_space", "color_range", "sample_rate", "channels", "time_base", "start_time", "duration"):
            if a.get(key) != b.get(key):
                raise RuntimeError(f"Stream property changed: {key}: {a.get(key)} != {b.get(key)}")
    segment_clocks: list[int] = []
    for index, original in enumerate(original_segments):
        fixed = fixed_root / "segments" / f"seg_{index:04d}.mp4"
        before, after = _frame_clock(original), _frame_clock(fixed)
        if before != after:
            raise RuntimeError(f"Segment {index} frame clock changed")
        segment_clocks.append(len(after))
    # Every frame of every segment, including the existing freeze tail. Compare
    # against its same-time source so camera/subject motion is not called jitter.
    selected = range(len(clips))
    real: dict[str, Any] = {}
    cv2.setNumThreads(2)
    cv2.setRNGSeed(0)
    for index in selected:
        clip = clips[index]
        print(f"Measuring source-matched segment {index + 1}/{len(clips)}", flush=True)
        real[str(index)] = _measure_clip(
            local_file(source, clip.src), original_segments[index],
            fixed_root / "segments" / f"seg_{index:04d}.mp4", clip, segment_clocks[index],
        )
        _write(output / f"segment-{index:02d}-motion.json", real[str(index)])
    final_hashes = {name: _hash(local_file(source, name)) for name in sorted(names)}
    if final_hashes != original_hashes:
        raise RuntimeError("Original work changed during audit; do not claim immutable-source verification")
    quality = _json(local_file(source, "quality_report.json"))
    real_summary: dict[str, Any] = {}
    for name, data in real.items():
        row: dict[str, Any] = dict(data)
        for phase in ("before", "after"):
            metrics: dict[str, Any] = data[phase]
            row[phase] = {key: value for key, value in metrics.items() if key != "center_offsets_px"}
        real_summary[name] = row
    summary: dict[str, Any] = {
        "status": "verified_local_derivative_not_published", "task_id": state["task_id"],
        "implementation_version": PIPELINE_IMPLEMENTATION_VERSION, "deliverable": deliverable.name,
        "deliverable_sha256": _hash(deliverable), "original_sha256": original_hashes["final.mp4"],
        "duration_seconds": float(after_probe["format"]["duration"]), "video_frames": len(clocks[1]),
        "all_video_pts_and_durations_equal": True, "all_segment_clocks_equal": True,
        "segment_frames": segment_clocks, "audio_packets": len(packets[1]),
        "all_original_audio_packets_hashes_and_timestamps_equal": True,
        "original_files_unchanged": len(original_hashes), "paid_provider_calls": 0,
        "synthetic": {name: {k: v for k, v in data.items() if k != "centers_px"} for name, data in synthetic.items()},
        "real_segments": real_summary,
        "original_blocking_issue_count_unchanged": quality.get("blocking_issue_count"),
        "existing_freeze_pad_seconds_unchanged": sum(clip.freeze_pad or 0 for clip in clips),
        "scope": "Rendering-only repair; no camera stabilization, cut/EDL/audio changes, classroom revision or fresh full QC approval.",
    }
    _write(output / "validation.json", summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(audit(args.task_dir, args.out_dir))


if __name__ == "__main__":
    main()