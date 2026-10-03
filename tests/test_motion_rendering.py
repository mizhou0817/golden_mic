"""Pixel/clock regressions for optional news-segment motion, without providers.

A centered push-in must leave its center stationary and move other points
monotonically outwards. Merely checking resolution/duration misses crop jitter.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
from numpy.typing import NDArray

from backend.models import EDLClip
from backend.readiness import _check_media_capabilities  # pyright: ignore[reportPrivateUsage]
from backend.rendering import (
    SegmentRenderOptions,
    _build_segment_filters,  # pyright: ignore[reportPrivateUsage]
    _ken_burns_filter,  # pyright: ignore[reportPrivateUsage]
    _render_segment,  # pyright: ignore[reportPrivateUsage]
)


FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
_MARKERS = (
    "drawbox=x=953:y=533:w=15:h=15:color=white:t=fill,"
    "drawbox=x=1153:y=733:w=15:h=15:color=white:t=fill"
)


def _run(command: list[str]) -> bytes:
    result = subprocess.run(command, capture_output=True, check=False, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


def marker_frames(filters: str, frames: int, *, color: str = "white") -> NDArray[np.uint8]:
    """Bounded 400x400 ROI; render the actual 1080p filter BEFORE cropping."""
    source = f"color=c=black:s=1920x1080:r=30,{_MARKERS.replace('white', color)}"
    raw = _run([
        "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", source,
        "-vf", f"{filters},crop=400:400:880:460,format=gray",
        "-frames:v", str(frames), "-fps_mode", "passthrough", "-an",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ])
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, 400, 400)


def marker_centers(images: NDArray[np.uint8]) -> NDArray[np.float64]:
    """Intensity centroid, not a thresholded integer bounding box."""
    weights = images.astype(np.float64)
    mass = weights.sum(axis=(1, 2))
    if not np.all(mass > 0):
        raise AssertionError("Marker disappeared; a blank output is not stable motion")
    columns: NDArray[np.float64] = weights.sum(axis=1)
    rows: NDArray[np.float64] = weights.sum(axis=2)
    columns *= np.arange(images.shape[2], dtype=np.float64)
    rows *= np.arange(images.shape[1], dtype=np.float64)
    x: NDArray[np.float64] = columns.sum(axis=1) / mass
    y: NDArray[np.float64] = rows.sum(axis=1) / mass
    return np.column_stack((x, y))


class MotionFilterContractTest(unittest.TestCase):
    def test_motion_off_and_zero_zoom_keep_the_historical_filter_path(self) -> None:
        expected = ["trim=duration=1.250000", "setpts=PTS-STARTPTS", "fps=30", "format=yuv420p"]
        self.assertEqual(_build_segment_filters(1.25, 0, SegmentRenderOptions()), expected)
        self.assertEqual(
            _build_segment_filters(1.25, 0, SegmentRenderOptions(motion=True, zoom_ratio=0)), expected,
        )

    def test_media_preflight_requires_fractional_motion_filter(self) -> None:
        def capabilities(*command: str) -> str:
            if "-encoders" in command:
                return "libx264 aac"
            return "ass loudnorm ebur128 silencedetect"

        with patch("backend.readiness._run_media_capability_command", side_effect=capabilities):
            with self.assertRaisesRegex(RuntimeError, "perspective"):
                _check_media_capabilities()


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg/ffprobe required")
class MotionPixelTest(unittest.TestCase):
    def test_stationary_center_stays_fixed_with_motion(self) -> None:
        for color_consistency in (False, True):
            with self.subTest(color_consistency=color_consistency):
                options = SegmentRenderOptions(motion=True, color_consistency=color_consistency)
                frames = marker_frames(",".join(_build_segment_filters(3, 0, options)), 90)
                self.assertEqual(len(frames), 90)
                center = marker_centers(frames[:, :160, :160])
                self.assertLess(float(np.ptp(center, axis=0).max()), 0.1,
                                "A stationary center is shaking under a centered zoom")
                self.assertLess(float(np.linalg.norm(np.diff(center, axis=0), axis=1).max()), 0.1)

    def test_off_center_motion_has_no_backward_steps_or_endpoint_shift(self) -> None:
        for count, ratio in ((45, 0.08), (90, 0.01), (90, 0.08), (163, 0.25)):
            with self.subTest(frames=count, zoom_ratio=ratio):
                frames = marker_frames(_ken_burns_filter(count, ratio), count)
                points = marker_centers(frames[:, 200:390, 200:390])
                self.assertEqual(len(points), count)
                np.testing.assert_allclose(points[0], [80, 80], atol=0.1)
                np.testing.assert_allclose(points[-1], [80 + 200 * ratio] * 2, atol=0.1)
                # Fixed analytical trajectory, independent of the implementation.
                expected = 80 + 200 * ratio * np.arange(count) / (count - 1)
                np.testing.assert_allclose(points, np.column_stack((expected, expected)), atol=0.1)
                self.assertGreaterEqual(float(np.diff(points, axis=0).min()), -0.02,
                                        "Monotonic push-in must not reverse direction")

    def test_single_frame_is_identity_and_motion_clamps_after_final_frame(self) -> None:
        single = marker_frames(_ken_burns_filter(1, 0.08), 1)
        self.assertEqual(len(single), 1)
        np.testing.assert_allclose(marker_centers(single[:, 200:390, 200:390]), [[80, 80]], atol=0.1)
        extended = marker_frames(_ken_burns_filter(30, 0.08), 35)
        np.testing.assert_array_equal(extended[29], extended[34])
        np.testing.assert_allclose(marker_centers(extended[29:, 200:390, 200:390]), 96, atol=0.1)

    def test_colored_center_does_not_acquire_chroma_grid_jitter(self) -> None:
        filters = (
            _ken_burns_filter(90, 0.08)
            + ",format=yuv420p,format=yuv444p,crop=160:160:880:460"
        )
        raw = _run([
            "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "color=c=black:s=1920x1080:r=30:d=3," + _MARKERS.replace("white", "red"),
            "-vf", filters, "-an", "-fps_mode", "passthrough", "-f", "rawvideo",
            "-pix_fmt", "yuv444p", "pipe:1",
        ])
        planes = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3, 160, 160)
        self.assertEqual(len(planes), 90)
        for plane in (1, 2):
            with self.subTest(chroma_plane=plane):
                # Inspect actual U and V, not just the red marker's luma. The
                # final 4:2:0 conversion is included and neutral chroma removed.
                weights = np.abs(planes[:, plane].astype(np.int16) - 128).astype(np.uint8)
                center = marker_centers(weights)
                self.assertLess(float(np.ptp(center, axis=0).max()), 0.15)

    def test_h264_output_preserves_center_stability_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "norm").mkdir()
            source = root / "norm" / "markers.mp4"
            _run([
                "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                f"color=c=black:s=1920x1080:r=30:d=3,{_MARKERS}",
                "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p", "-an", str(source),
            ])
            output = root / "motion.mp4"
            clip = EDLClip.model_validate({"shot_id": 0, "src": "norm/markers.mp4", "in": 0, "out": 3})
            asyncio.run(_render_segment(root, clip, output, 0, SegmentRenderOptions(motion=True)))
            raw = _run([
                "ffmpeg", "-v", "error", "-nostdin", "-i", str(output),
                "-vf", "crop=160:160:880:460,format=gray", "-an", "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
            ])
            frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 160, 160)
            self.assertEqual(len(frames), 90)
            self.assertLess(float(np.ptp(marker_centers(frames), axis=0).max()), 0.15)
            stream = _probe(output)["streams"][0]
            self.assertEqual((stream["width"], stream["height"]), (1920, 1080))
            self.assertEqual(stream["avg_frame_rate"], "30/1")

    def test_real_frames_pts_seek_and_freeze_are_not_duplicated_or_retimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "norm").mkdir()
            source = root / "norm" / "clock.mkv"
            # Selected frames have distinct intensities: motion cannot hide a
            # dropped/repeated input behind changing crop geometry. Use steps
            # large enough to survive CRF20 DC quantization (0.01 did not).
            _run([
                "ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                "color=c=0x303030:s=1920x1080:r=30:d=1.2,eq=brightness='0.025*n':eval=frame",
                "-c:v", "ffv1", "-pix_fmt", "yuv420p", "-an", str(source),
            ])
            clip = EDLClip.model_validate({
                "shot_id": 0, "src": "norm/clock.mkv", "freeze_pad": 0.2, "in": 0.2, "out": 0.9,
            })
            outputs = [root / "plain.mp4", root / "motion.mp4"]
            for output, motion in zip(outputs, (False, True), strict=True):
                asyncio.run(_render_segment(root, clip, output, 0, SegmentRenderOptions(motion=motion)))
            probes = [_probe(output) for output in outputs]
            self.assertEqual(probes[0]["frames"], probes[1]["frames"])
            self.assertEqual(len(probes[1]["frames"]), 27)
            levels: list[NDArray[np.float64]] = []
            for output in outputs:
                raw = _run([
                    "ffmpeg", "-v", "error", "-nostdin", "-i", str(output), "-vf",
                    "crop=8:8:960:540,format=gray", "-an", "-fps_mode", "passthrough",
                    "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
                ])
                levels.append(np.frombuffer(raw, dtype=np.uint8).reshape(-1, 8, 8).mean(axis=(1, 2)))
            np.testing.assert_allclose(levels[0], levels[1], atol=1)
            self.assertTrue(np.all(np.diff(levels[1][:21]) > 1))
            np.testing.assert_allclose(levels[1][20:], levels[1][20], atol=1)


def _probe(path: Path) -> dict[str, Any]:
    return json.loads(_run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "stream=width,height,avg_frame_rate:frame=pts,duration",
        "-of", "json", str(path),
    ]))


if __name__ == "__main__":
    unittest.main()