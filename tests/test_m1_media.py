import asyncio
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.media import (
    MediaProcessingError,
    configure_media_command_timeout,
    detect_shots,
    normalize_assets,
    probe_media,
    run_logged_command,
    sample_shots_by_duration,
    validate_source_media,
    validate_total_source_duration,
)
from backend.models import Shot, UploadedAsset


class DurationSamplingTest(unittest.TestCase):
    def test_keeps_exact_limit_in_timeline_order(self) -> None:
        shots = [
            Shot(
                shot_id=index,
                source_index=0,
                source_scene_index=index,
                source_name="input.mp4",
                norm_path="norm/norm_0.mp4",
                start=float(index),
                end=float(index + 1),
                duration=1.0 if index != 5 else 8.0,
            )
            for index in range(10)
        ]

        sampled = sample_shots_by_duration(shots, 4)

        self.assertEqual(len(sampled), 4)
        self.assertEqual([shot.shot_id for shot in sampled], [0, 1, 2, 3])
        self.assertEqual(
            [shot.source_scene_index for shot in sampled],
            sorted(shot.source_scene_index for shot in sampled),
        )
        self.assertIn(5, [shot.source_scene_index for shot in sampled])

    def test_source_validation_rejects_non_video_and_invalid_dimensions(self) -> None:
        with self.assertRaisesRegex(MediaProcessingError, "不包含视频流"):
            validate_source_media(
                {"streams": [{"codec_type": "audio"}]},
                "fake.mp4",
            )
        with self.assertRaisesRegex(MediaProcessingError, "无法读取上传视频分辨率"):
            validate_source_media(
                {
                    "streams": [
                        {
                            "codec_type": "video",
                            "width": 0,
                            "height": 1080,
                        }
                    ]
                },
                "invalid.mp4",
            )

    def test_source_validation_accepts_files_for_local_processing(self) -> None:
        validate_source_media(
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "width": 320,
                        "height": 240,
                        "color_transfer": "smpte2084",
                    }
                ]
            },
            "local-input.mp4",
        )

    def test_source_validation_enforces_production_media_limits(self) -> None:
        probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 3840,
                    "height": 2160,
                    "avg_frame_rate": "60/1",
                }
            ],
            "format": {"duration": "120.5"},
        }
        duration = validate_source_media(
            probe,
            "valid.mp4",
            max_width=3840,
            max_height=2160,
            max_duration_seconds=121.0,
            max_frame_rate=60.0,
        )
        self.assertEqual(duration, 120.5)

        with self.assertRaisesRegex(MediaProcessingError, "宽度"):
            validate_source_media(probe, "wide.mp4", max_width=1920)
        with self.assertRaisesRegex(MediaProcessingError, "高度"):
            validate_source_media(probe, "tall.mp4", max_height=1080)
        with self.assertRaisesRegex(MediaProcessingError, "帧率"):
            validate_source_media(probe, "fast.mp4", max_frame_rate=30.0)
        with self.assertRaisesRegex(MediaProcessingError, "单文件上限"):
            validate_source_media(probe, "long.mp4", max_duration_seconds=120.0)

    def test_total_source_duration_limit_is_enforced(self) -> None:
        validate_total_source_duration(3599.0, 3600.0)
        with self.assertRaisesRegex(MediaProcessingError, "总时长"):
            validate_total_source_duration(3600.1, 3600.0)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class MediaPipelineSmokeTest(unittest.TestCase):
    def test_normalizes_and_detects_three_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            task_dir = Path(temporary_directory)
            raw_dir = task_dir / "raw"
            raw_dir.mkdir()
            source_path = raw_dir / "generated.mp4"
            self._generate_test_video(source_path)
            upload = UploadedAsset(
                original_name="30-second-multiscene.mp4",
                stored_name=source_path.name,
                path=source_path,
                size=source_path.stat().st_size,
                content_type="video/mp4",
            )
            progress_events: list[str] = []

            normalized = asyncio.run(
                normalize_assets(
                    task_dir,
                    [upload],
                    lambda completed, total, message: progress_events.append(message),
                )
            )
            shots = asyncio.run(
                detect_shots(
                    task_dir,
                    normalized,
                    [upload],
                    max_shots=120,
                    progress=lambda completed, total, message: progress_events.append(message),
                )
            )
            probe = asyncio.run(probe_media(normalized[0], task_dir))

            video_stream = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
            audio_streams = [stream for stream in probe["streams"] if stream["codec_type"] == "audio"]
            self.assertEqual(video_stream["codec_name"], "h264")
            self.assertEqual((video_stream["width"], video_stream["height"]), (1920, 1080))
            self.assertEqual(video_stream["avg_frame_rate"], "30/1")
            self.assertEqual(audio_streams, [])
            self.assertGreaterEqual(len(shots), 3)
            self.assertTrue(all(shot.duration >= 1.0 for shot in shots))
            self.assertTrue(all(shot.end > shot.start for shot in shots))
            self.assertTrue((task_dir / "shots.json").is_file())
            self.assertTrue(progress_events)

    @staticmethod
    def _generate_test_video(output_path: Path) -> None:
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=640x360:r=30:d=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:r=30:d=10",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=640x360:r=30:d=10",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode != 0:
            raise AssertionError(result.stderr.decode("utf-8", errors="replace"))


class MediaCommandTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_media_command_timeout_terminates_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            configure_media_command_timeout(0.05)
            try:
                with self.assertRaisesRegex(MediaProcessingError, "超时"):
                    await run_logged_command(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        task_dir,
                        "媒体超时测试",
                    )
            finally:
                configure_media_command_timeout(7200.0)


if __name__ == "__main__":
    unittest.main()
