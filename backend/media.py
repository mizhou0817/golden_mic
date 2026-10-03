import asyncio
import bisect
import json
import math
import os
import shutil
import signal
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from scenedetect import ContentDetector, SceneManager, open_video

from .models import Shot, UploadedAsset
from .storage import write_text_log


ProgressCallback = Callable[[int, int, str], None]
MIN_SCENE_DURATION_SECONDS = 1.0
SCENE_DETECTION_MIN_SECONDS = 1.5
NORMALIZE_VIDEO_FILTER = (
    "scale=1920:1080:force_original_aspect_ratio=decrease,"
    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
    "fps=30,setsar=1"
)
DEFAULT_MEDIA_COMMAND_TIMEOUT_SECONDS = 7200.0
_MEDIA_COMMAND_TIMEOUT_SECONDS = DEFAULT_MEDIA_COMMAND_TIMEOUT_SECONDS


def configure_media_command_timeout(timeout_seconds: float) -> None:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
        raise ValueError("媒体命令超时必须大于 0 秒。")
    global _MEDIA_COMMAND_TIMEOUT_SECONDS
    _MEDIA_COMMAND_TIMEOUT_SECONDS = timeout_seconds


class MediaProcessingError(RuntimeError):
    """Raised when an external media tool or scene detector fails."""


def require_media_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        names = "、".join(missing)
        raise MediaProcessingError(f"未找到媒体工具：{names}。请先安装 FFmpeg 并确保命令已加入 PATH。")


async def normalize_assets(
    task_dir: Path,
    uploads: Sequence[UploadedAsset],
    progress: ProgressCallback,
) -> list[Path]:
    require_media_tools()
    norm_dir = task_dir / "norm"
    norm_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[Path] = []
    total = len(uploads)

    for index, upload in enumerate(uploads):
        output_path = norm_dir / f"norm_{index}.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            "mov,matroska,webm,avi",
            "-i",
            str(upload.path),
            "-map",
            "0:v:0",
            "-vf",
            NORMALIZE_VIDEO_FILTER,
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
        await run_logged_command(command, task_dir, f"素材规格化 {index + 1}/{total}")
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise MediaProcessingError(f"素材规格化未生成有效文件：{upload.original_name}")

        probe = await probe_media(output_path, task_dir)
        validate_normalized_media(probe, upload.original_name)
        normalized.append(output_path)
        progress(index + 1, total, f"素材规格化 {index + 1}/{total}")

    return normalized


async def probe_media(media_path: Path, task_dir: Path) -> dict[str, object]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        "mov,matroska,webm,avi,wav,mp3,ogg,aac,flac,image2,png_pipe,jpeg_pipe,gif",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(media_path),
    ]
    stdout = await run_logged_command(command, task_dir, f"ffprobe {media_path.name}", capture_stdout=True)
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaProcessingError(f"无法解析 ffprobe 输出：{media_path.name}") from exc
    if not isinstance(payload, dict):
        raise MediaProcessingError(f"ffprobe 返回了无效结果：{media_path.name}")
    return payload


def validate_normalized_media(probe: dict[str, object], source_name: str) -> None:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise MediaProcessingError(f"无法读取规格化素材的视频流：{source_name}")

    video_streams = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"]
    if not video_streams:
        raise MediaProcessingError(f"规格化素材缺少视频流：{source_name}")

    video = video_streams[0]
    if video.get("codec_name") != "h264" or video.get("width") != 1920 or video.get("height") != 1080:
        raise MediaProcessingError(f"规格化素材参数校验失败：{source_name}")
    if audio_streams:
        raise MediaProcessingError(f"规格化素材仍包含音轨：{source_name}")

    frame_rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
    try:
        numerator, denominator = frame_rate.split("/", maxsplit=1)
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError) as exc:
        raise MediaProcessingError(f"无法读取规格化素材帧率：{source_name}") from exc
    if abs(fps - 30.0) > 0.01:
        raise MediaProcessingError(f"规格化素材帧率不是 30fps：{source_name}")


def validate_source_media(
    probe: dict[str, object],
    source_name: str,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
    max_duration_seconds: float | None = None,
    max_frame_rate: float | None = None,
) -> float | None:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        raise MediaProcessingError(f"无法读取上传素材的媒体流：{source_name}")
    video = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"),
        None,
    )
    if video is None:
        raise MediaProcessingError(f"上传文件不包含视频流：{source_name}")
    width = video.get("width")
    height = video.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise MediaProcessingError(f"无法读取上传视频分辨率：{source_name}")
    if max_width is not None and width > max_width:
        raise MediaProcessingError(
            f"上传视频宽度 {width} 超过上限 {max_width}：{source_name}"
        )
    if max_height is not None and height > max_height:
        raise MediaProcessingError(
            f"上传视频高度 {height} 超过上限 {max_height}：{source_name}"
        )

    if max_frame_rate is not None:
        frame_rate = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "")
        fps = _parse_frame_rate(frame_rate)
        if fps is None:
            raise MediaProcessingError(f"无法读取上传视频帧率：{source_name}")
        if fps > max_frame_rate:
            raise MediaProcessingError(
                f"上传视频帧率 {fps:.3f} 超过上限 {max_frame_rate:g}：{source_name}"
            )

    duration = source_media_duration(probe)
    if max_duration_seconds is not None:
        if duration is None:
            raise MediaProcessingError(f"无法读取上传视频时长：{source_name}")
        if duration > max_duration_seconds:
            raise MediaProcessingError(
                f"上传视频时长 {duration:.3f} 秒超过单文件上限 "
                f"{max_duration_seconds:g} 秒：{source_name}"
            )
    return duration


def source_media_duration(probe: dict[str, object]) -> float | None:
    format_info = probe.get("format")
    candidates: list[object] = []
    if isinstance(format_info, dict):
        candidates.append(format_info.get("duration"))
    streams = probe.get("streams")
    if isinstance(streams, list):
        candidates.extend(
            stream.get("duration")
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        )
    durations: list[float] = []
    for candidate in candidates:
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0.0:
            durations.append(duration)
    # A short container duration must not conceal a longer source video stream.
    return max(durations) if durations else None


def validate_total_source_duration(total_seconds: float, maximum_seconds: float) -> None:
    if not math.isfinite(total_seconds) or total_seconds <= 0.0:
        raise MediaProcessingError("上传视频总时长无效。")
    if total_seconds > maximum_seconds:
        raise MediaProcessingError(
            f"上传视频总时长 {total_seconds:.3f} 秒超过上限 {maximum_seconds:g} 秒。"
        )


def _parse_frame_rate(value: str) -> float | None:
    try:
        if "/" in value:
            numerator, denominator = value.split("/", maxsplit=1)
            frame_rate = float(numerator) / float(denominator)
        else:
            frame_rate = float(value)
    except (ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(frame_rate) or frame_rate <= 0.0:
        return None
    return frame_rate


async def detect_shots(
    task_dir: Path,
    normalized_paths: Sequence[Path],
    uploads: Sequence[UploadedAsset],
    max_shots: int,
    progress: ProgressCallback,
) -> list[Shot]:
    shots: list[Shot] = []
    total = len(normalized_paths)

    for source_index, norm_path in enumerate(normalized_paths):
        try:
            scenes = await asyncio.to_thread(_detect_video_scenes, norm_path)
        except Exception as exc:
            raise MediaProcessingError(f"镜头切分失败：{uploads[source_index].original_name}；{exc}") from exc

        for source_scene_index, (start, end) in enumerate(scenes):
            duration = end - start
            if duration < MIN_SCENE_DURATION_SECONDS:
                write_text_log(
                    task_dir,
                    f"丢弃短镜头 source={source_index} scene={source_scene_index} duration={duration:.3f}s",
                )
                continue
            shots.append(
                Shot(
                    shot_id=len(shots),
                    source_index=source_index,
                    source_scene_index=source_scene_index,
                    source_name=uploads[source_index].original_name,
                    norm_path=norm_path.relative_to(task_dir).as_posix(),
                    start=round(start, 3),
                    end=round(end, 3),
                    duration=round(duration, 3),
                )
            )
        progress(source_index + 1, total, f"镜头切分 {source_index + 1}/{total}，已发现 {len(shots)} 个镜头")

    if not shots:
        raise MediaProcessingError(
            f"未检测到时长至少 {MIN_SCENE_DURATION_SECONDS:g} 秒的有效镜头，请检查上传素材。"
        )

    original_count = len(shots)
    shots = sample_shots_by_duration(shots, max_shots)
    if len(shots) < original_count:
        write_text_log(task_dir, f"镜头数超过上限：按素材时间线均匀采样 {original_count} -> {len(shots)}")

    shots_path = task_dir / "shots.json"
    shots_path.write_text(
        json.dumps([shot.model_dump(mode="json") for shot in shots], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return shots


def sample_shots_by_duration(shots: Sequence[Shot], limit: int) -> list[Shot]:
    if limit < 1:
        raise ValueError("MAX_SHOTS 必须大于 0。")
    if len(shots) <= limit:
        return [shot.model_copy(update={"shot_id": index}) for index, shot in enumerate(shots)]

    durations = [max(0.0, shot.duration) for shot in shots]
    total_duration = sum(durations)
    if total_duration <= 0:
        selected_indices = _uniform_indices(len(shots), limit)
    else:
        starts: list[float] = []
        ends: list[float] = []
        cursor = 0.0
        for duration in durations:
            starts.append(cursor)
            cursor += duration
            ends.append(cursor)

        available = set(range(len(shots)))
        selected_indices: list[int] = []
        for sample_index in range(limit):
            target = total_duration * (sample_index + 0.5) / limit
            preferred = min(bisect.bisect_left(ends, target), len(shots) - 1)
            if preferred in available:
                chosen = preferred
            else:
                chosen = min(
                    available,
                    key=lambda index: (
                        _distance_to_interval(target, starts[index], ends[index]),
                        abs(index - preferred),
                        index,
                    ),
                )
            selected_indices.append(chosen)
            available.remove(chosen)
        selected_indices.sort()

    return [shots[index].model_copy(update={"shot_id": new_id}) for new_id, index in enumerate(selected_indices)]


def _uniform_indices(item_count: int, limit: int) -> list[int]:
    if limit == 1:
        return [item_count // 2]
    return [round(index * (item_count - 1) / (limit - 1)) for index in range(limit)]


def _distance_to_interval(target: float, start: float, end: float) -> float:
    if target < start:
        return start - target
    if target > end:
        return target - end
    return 0.0


def _detect_video_scenes(norm_path: Path) -> list[tuple[float, float]]:
    video = open_video(str(norm_path), framerate=30.0)
    try:
        scene_manager = SceneManager()
        scene_manager.add_detector(
            ContentDetector(
                threshold=27.0,
                min_scene_len=round(30 * SCENE_DETECTION_MIN_SECONDS),
            )
        )
        scene_manager.detect_scenes(video=video, show_progress=False)
        scene_list = scene_manager.get_scene_list(start_in_scene=True)
        if not scene_list and video.duration.seconds > 0:
            return [(0.0, video.duration.seconds)]
        return [(start.seconds, end.seconds) for start, end in scene_list]
    finally:
        video.capture.release()


async def run_logged_command(
    command: Sequence[str],
    task_dir: Path,
    label: str,
    *,
    capture_stdout: bool = False,
) -> bytes:
    effective_command = _quiet_media_command(command)
    command_text = subprocess.list2cmdline(effective_command)
    write_text_log(task_dir, f"{label} command: {command_text}")
    try:
        process = await asyncio.create_subprocess_exec(
            *effective_command,
            stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise MediaProcessingError(f"无法启动媒体命令：{effective_command[0]}；{exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_MEDIA_COMMAND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        await _terminate_process(process)
        raise MediaProcessingError(
            f"{label}超过 {_MEDIA_COMMAND_TIMEOUT_SECONDS:g} 秒超时。"
        ) from exc
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise

    stderr_text = (stderr or b"").decode("utf-8", errors="replace")
    with (task_dir / "task.log").open("a", encoding="utf-8") as log_file:
        log_file.write(f"--- {label} stderr ---\n")
        log_file.write(stderr_text)
        if stderr_text and not stderr_text.endswith("\n"):
            log_file.write("\n")
        log_file.write(f"--- {label} exit={process.returncode} ---\n")

    if process.returncode != 0:
        detail_lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
        detail = detail_lines[-1] if detail_lines else "无 stderr 输出"
        raise MediaProcessingError(f"{label}失败：{detail}")
    return stdout or b""


async def run_capture_stderr_command(
    command: Sequence[str],
    task_dir: Path,
    label: str,
) -> str:
    effective_command = _quiet_media_command(command)
    command_text = subprocess.list2cmdline(effective_command)
    write_text_log(task_dir, f"{label} command: {command_text}")
    try:
        process = await asyncio.create_subprocess_exec(
            *effective_command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise MediaProcessingError(f"无法启动媒体命令：{effective_command[0]}；{exc}") from exc
    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_MEDIA_COMMAND_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        await _terminate_process(process)
        raise MediaProcessingError(
            f"{label}超过 {_MEDIA_COMMAND_TIMEOUT_SECONDS:g} 秒超时。"
        ) from exc
    except asyncio.CancelledError:
        await _terminate_process(process)
        raise
    stderr_text = (stderr or b"").decode("utf-8", errors="replace")
    if process.returncode != 0:
        detail_lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
        detail = detail_lines[-1] if detail_lines else "无 stderr 输出"
        raise MediaProcessingError(f"{label}失败：{detail}")
    return stderr_text


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (LookupError, OSError, ProcessLookupError):
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=10.0)
        return
    except asyncio.TimeoutError:
        pass
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (LookupError, OSError, ProcessLookupError):
        pass
    await process.wait()


def _quiet_media_command(command: Sequence[str]) -> list[str]:
    result = list(command)
    if not result:
        raise ValueError("媒体命令不能为空。")
    executable = Path(result[0]).name.lower()
    options: list[str] = []
    if executable in {"ffmpeg", "ffmpeg.exe", "ffprobe", "ffprobe.exe"}:
        if "-hide_banner" not in result:
            options.append("-hide_banner")
        if executable in {"ffmpeg", "ffmpeg.exe"} and "-nostats" not in result:
            options.append("-nostats")
    return [result[0], *options, *result[1:]]
