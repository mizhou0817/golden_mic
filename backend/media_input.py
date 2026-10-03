"""Local-only upload preparation and timestamp-backed document narration.

Raw uploads are immutable. Derived videos are separate, randomly named files in
raw/ so existing task restoration can keep using raw/<stored_name> unchanged.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import struct
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
from fastapi import HTTPException, UploadFile

from .config import Settings
from .media import (
    MediaProcessingError, NORMALIZE_VIDEO_FILTER, run_logged_command,
    source_media_duration, validate_normalized_media, validate_source_media,
    validate_total_source_duration,
)
from .models import AnnotatedShot, Sentence, SentenceTiming, TTSWordTiming, UploadedAsset
from .providers.asr import ASRTranscript, create_asr_provider
from .storage import write_json_atomic
from .tts_pipeline import (
    _alignment_text, _split_continuous_group_audio, probe_audio_duration,
    rebuild_narration_from_existing,
)

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}
MAX_PHOTO_BYTES = 50 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000
MAX_OWN_VOICE_BYTES = 50 * 1024 * 1024
PHOTO_DURATION = 3.0
INPUT_FORMATS = {
    ".mp4": "mov", ".mov": "mov", ".m4a": "mov", ".avi": "avi",
    ".mkv": "matroska", ".webm": "matroska", ".ogg": "ogg",
    ".wav": "wav", ".mp3": "mp3",
}
VOICE_EXTENSIONS = {".webm", ".ogg", ".wav", ".mp3", ".m4a"}


def _input_flags(path: Path) -> list[str]:
    try:
        demuxer = INPUT_FORMATS[path.suffix.lower()]
    except KeyError as exc:
        raise ValueError("不支持的素材输入格式。") from exc
    return ["-protocol_whitelist", "file,pipe", "-f", demuxer]


async def _probe(path: Path, task_dir: Path) -> dict[str, Any]:
    raw = await run_logged_command([
        "ffprobe", "-v", "error", *_input_flags(path),
        "-show_streams", "-show_format", "-of", "json", str(path),
    ], task_dir, "校验上传素材", capture_stdout=True)
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise ValueError("素材探测结果无效。")
    return payload


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是有限数字。")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} 超出范围。") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} 必须是非负有限数字。")
    return result


def parse_asset_options(value: str | None, count: int) -> list[dict[str, Any]]:
    if value is None:
        return [{"note": "", "trim_start": 0.0, "trim_end": None} for _ in range(count)]
    if len(value) > 65536:
        raise ValueError("asset_options 过长。")
    payload = json.loads(value)
    if not isinstance(payload, list) or len(payload) != count:
        raise ValueError("asset_options 必须按上传顺序为每个素材提供一项。")
    result: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or set(item) - {"note", "trim_start", "trim_end"}:
            raise ValueError("asset_options 包含不支持的字段。")
        note = item.get("note", "")
        if not isinstance(note, str) or len(note) > 20 or any(ord(c) < 32 or ord(c) == 127 for c in note):
            raise ValueError("素材备注最多 20 字且不能含控制字符。")
        start = _finite_number(item.get("trim_start", 0), "trim_start")
        end = item.get("trim_end")
        if end is not None:
            end = _finite_number(end, "trim_end")
            if end <= start:
                raise ValueError("trim_end 必须大于 trim_start。")
        result.append({"note": note, "trim_start": start, "trim_end": end})
    return result


def _local_raw(task_dir: Path, stored_name: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", stored_name):
        raise ValueError("素材存储名称不安全。")
    raw = (task_dir / "raw").resolve()
    path = raw / stored_name
    if path.is_symlink() or path.resolve().parent != raw or not path.is_file():
        raise ValueError("原始素材缺失或路径不安全。")
    return path


def _check_dimensions(width: int, height: int, settings: Settings) -> None:
    if not (0 < width <= settings.max_source_width and 0 < height <= settings.max_source_height):
        raise ValueError("图片原始尺寸超过上限。")
    if width * height > MAX_PHOTO_PIXELS:
        raise ValueError("图片解码像素超过安全上限。")


def _image_dimensions(data: bytes, suffix: str, settings: Settings) -> tuple[int, int]:
    """Inspect bounded headers before any native decoder allocation."""
    if suffix == ".png":
        if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[8:16] != b"\0\0\0\rIHDR":
            raise ValueError("无效 PNG 图片。")
        width, height = struct.unpack(">II", data[16:24])
    elif suffix == ".gif":
        if len(data) < 13 or data[:6] not in {b"GIF87a", b"GIF89a"}:
            raise ValueError("无效 GIF 图片。")
        width, height = struct.unpack("<HH", data[6:10])
        _check_dimensions(width, height, settings)
        # Validate every frame rectangle/block without decoding the animation.
        pos = 13 + (3 * (2 ** ((data[10] & 7) + 1)) if data[10] & 128 else 0)
        frames = 0
        while pos < len(data):
            marker = data[pos]
            pos += 1
            if marker == 0x3B:
                if not frames:
                    raise ValueError("GIF 没有图像帧。")
                break
            if marker == 0x2C:
                if pos + 9 > len(data):
                    raise ValueError("GIF 帧不完整。")
                x, y, w, h, flags = struct.unpack("<HHHHB", data[pos:pos + 9])
                _check_dimensions(w, h, settings)
                frames += 1
                if x + w > width or y + h > height or frames > 300 or frames * width * height > 100_000_000:
                    raise ValueError("GIF 动画超过安全解码上限。")
                pos += 9 + (3 * (2 ** ((flags & 7) + 1)) if flags & 128 else 0) + 1
            elif marker == 0x21:
                pos += 1  # extension type
            else:
                raise ValueError("GIF 块无效。")
            while pos < len(data) and data[pos]:
                pos += 1 + data[pos]
            if pos >= len(data):
                raise ValueError("GIF 数据不完整。")
            pos += 1
        else:
            raise ValueError("GIF 缺少结束标记。")
    else:
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("无效 JPEG 图片。")
        pos = 2
        width = height = 0
        while pos < len(data):
            if data[pos] != 255:
                raise ValueError("JPEG 标记无效。")
            while pos < len(data) and data[pos] == 255:
                pos += 1
            if pos >= len(data):
                break
            marker = data[pos]
            pos += 1
            if marker in {0xD9, 0xDA}:
                break
            if pos + 2 > len(data):
                break
            length = int.from_bytes(data[pos:pos + 2], "big")
            if length < 2 or pos + length > len(data):
                raise ValueError("JPEG 数据不完整。")
            if marker in {0xC0, 0xC1, 0xC2}:
                if length < 8:
                    raise ValueError("JPEG 尺寸无效。")
                height, width = struct.unpack(">HH", data[pos + 3:pos + 7])
                break
            pos += length
        if not width or not height:
            raise ValueError("不支持或无效的 JPEG 图片。")
    _check_dimensions(width, height, settings)
    return width, height


def _decode_photo(path: Path, settings: Settings, output: Path | None = None) -> tuple[int, int]:
    if path.stat().st_size > min(MAX_PHOTO_BYTES, settings.upload_limit_bytes):
        raise ValueError("图片超过安全文件大小上限。")
    data = path.read_bytes()
    dimensions = _image_dimensions(data, path.suffix.lower(), settings)
    # Ignore EXIF orientation so header dimensions and decoded dimensions agree.
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if image is None or (image.shape[1], image.shape[0]) != dimensions:
        raise ValueError("图片无法完整解码或尺寸不一致。")
    if output is not None:
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("图片标准化失败。")
        output.write_bytes(encoded.tobytes())
    return dimensions


async def validate_original_asset(task_dir: Path, asset: UploadedAsset, settings: Settings) -> float:
    path = _local_raw(task_dir, asset.stored_name)
    if path.resolve() != asset.path.resolve() or path.stat().st_size != asset.size or not 0 < asset.size <= settings.upload_limit_bytes:
        raise ValueError("原始素材路径或大小校验失败。")
    if path.suffix.lower() in PHOTO_EXTENSIONS:
        await asyncio.to_thread(_decode_photo, path, settings)
        if PHOTO_DURATION > settings.max_source_duration_seconds_per_file:
            raise ValueError("照片视频时长超过单文件上限。")
        return PHOTO_DURATION
    probe = await _probe(path, task_dir)
    duration = validate_source_media(
        probe, asset.original_name, max_width=settings.max_source_width,
        max_height=settings.max_source_height,
        max_duration_seconds=settings.max_source_duration_seconds_per_file,
        max_frame_rate=settings.max_source_frame_rate,
    )
    assert duration is not None
    return duration


async def _prepare_voice(task_dir: Path, upload: UploadFile, settings: Settings, asset_bytes: int) -> dict[str, Any]:
    original_name = upload.filename or "recording.wav"
    suffix = Path(original_name).suffix.lower()
    if suffix not in VOICE_EXTENSIONS or len(original_name) > 255 or any(ord(c) < 32 for c in original_name):
        raise ValueError("自己的配音仅支持 webm/ogg/wav/mp3/m4a。")
    source = task_dir / "raw" / f"{uuid4().hex}{suffix}"
    decoded = task_dir / f"{uuid4().hex}.wav"
    maximum = min(1800.0, settings.max_source_duration_seconds_per_file)
    try:
        size = 0
        with source.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > min(MAX_OWN_VOICE_BYTES, settings.upload_limit_bytes) or size + asset_bytes > settings.total_upload_limit_bytes:
                    raise HTTPException(413, "自己的配音超过 50 MiB 或任务上传大小上限。")
                output.write(chunk)
        if not size:
            raise ValueError("自己的配音不能为空。")
        probe = await _probe(source, task_dir)
        streams = probe["streams"]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if len(audio) != 1 or any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams):
            raise ValueError("自己的配音必须仅包含一条音轨，不得包含视频。")
        if audio[0].get("channels") not in {1, 2}:
            raise ValueError("自己的配音必须为单声道或双声道。")
        hint = probe.get("format", {}).get("duration")
        if hint is not None and not 0 < _finite_number(float(hint), "duration") <= maximum:
            raise ValueError(f"自己的配音不能超过 {maximum:g} 秒。")
        # Browser WebM often has no duration; bounded decode then measure PCM.
        await run_logged_command([
            "ffmpeg", "-y", *_input_flags(source), "-i", str(source),
            "-t", f"{maximum + 0.1:.6f}", "-map", "0:a:0", "-vn", "-ac", "1",
            "-ar", "16000", "-c:a", "pcm_s16le", str(decoded),
        ], task_dir, "标准化完整自录配音")
        duration = await probe_audio_duration(decoded, task_dir)
        if not math.isfinite(duration) or not 0 < duration <= maximum:
            raise ValueError(f"自己的配音必须在 0 到 {maximum:g} 秒之间，请重新录制。")
        decoded.replace(task_dir / "own_voice.wav")
        return {"original_name": original_name, "stored_name": source.name, "size": size,
                "duration": duration, "normalized_path": "own_voice.wav", "transcript_verified": False}
    except BaseException:
        source.unlink(missing_ok=True)
        raise
    finally:
        decoded.unlink(missing_ok=True)
        await upload.close()


async def prepare_media_inputs(
    task_dir: Path, uploads: list[UploadedAsset], asset_options_json: str | None,
    own_voice: UploadFile | None, settings: Settings,
) -> list[UploadedAsset]:
    """Call after save_uploads, before add_task; performs no cloud requests."""
    created: list[Path] = []
    try:
        options = parse_asset_options(asset_options_json, len(uploads))
        if not 1 <= len(uploads) <= settings.max_files:
            raise ValueError("素材数量超出上限。")
        if sum(a.size for a in uploads) > settings.total_upload_limit_bytes:
            raise HTTPException(413, "素材总大小超过上传上限。")
        durations = [await validate_original_asset(task_dir, a, settings) for a in uploads]
        validate_total_source_duration(sum(durations), settings.max_total_source_duration_seconds)
        prepared: list[UploadedAsset] = []
        for asset, option, duration in zip(uploads, options, durations, strict=True):
            start, end = option["trim_start"], option["trim_end"]
            end = duration if end is None else end
            if not 0 <= start < end <= duration:
                raise ValueError("裁剪范围必须位于原始素材时长内。")
            photo = asset.path.suffix.lower() in PHOTO_EXTENSIONS
            if photo and (start != 0 or end != PHOTO_DURATION):
                raise ValueError("照片固定生成 3 秒视频，不支持裁剪。")
            derived_name = None
            if photo or start > 0 or end < duration:
                output = task_dir / "raw" / f"{uuid4().hex}.mp4"
                created.append(output)
                if photo:
                    still = task_dir / f"{uuid4().hex}.png"
                    created.append(still)
                    await asyncio.to_thread(_decode_photo, asset.path, settings, still)
                    command = ["ffmpeg", "-y", "-protocol_whitelist", "file,pipe", "-f", "image2",
                               "-pattern_type", "none", "-loop", "1", "-i", str(still), "-t", "3",
                               "-map", "0:v:0", "-vf", NORMALIZE_VIDEO_FILTER, "-an"]
                else:
                    command = ["ffmpeg", "-y", *_input_flags(asset.path), "-i", str(asset.path),
                               "-ss", f"{start:.6f}", "-t", f"{end - start:.6f}",
                               "-map", "0:v:0", "-map", "0:a:0?", "-vf", NORMALIZE_VIDEO_FILTER,
                               "-c:a", "aac", "-ar", "48000"]
                command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-map_metadata", "-1", "-movflags", "+faststart", str(output)]
                await run_logged_command(command, task_dir, "准备照片或裁剪素材（保留同期声音轨）")
                probe = await _probe(output, task_dir)
                if photo:
                    validate_normalized_media(probe, asset.original_name)
                    still.unlink(missing_ok=True)
                actual = source_media_duration(probe)
                if actual is None or abs(actual - (end - start)) > 0.15:
                    raise ValueError("素材预处理时长校验失败。")
                derived_name = output.name
            prepared.append(asset.model_copy(update={**option, "prepared_stored_name": derived_name,
                                                      "source_duration_seconds": duration}))
        voice = await _prepare_voice(task_dir, own_voice, settings, sum(a.size for a in uploads)) if own_voice else None
        if voice:
            created.extend([task_dir / "own_voice.wav", task_dir / "raw" / voice["stored_name"]])
        write_json_atomic(task_dir / "media_input_manifest.json", {
            "schema_version": 1, "note_policy": "user_context_not_visual_evidence",
            "original_total_duration_seconds": sum(durations),
            "assets": [a.model_dump(mode="json", exclude={"path"}) for a in prepared],
            "own_voice": voice,
        })
        return prepared
    except (ValueError, MediaProcessingError) as exc:
        for path in created:
            path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def processing_uploads(task_dir: Path, uploads: Sequence[UploadedAsset]) -> list[UploadedAsset]:
    """Ephemeral descriptors ONLY: never persist these over the raw upload list."""
    result: list[UploadedAsset] = []
    for asset in uploads:
        if asset.prepared_stored_name:
            path = _local_raw(task_dir, asset.prepared_stored_name)
            result.append(asset.model_copy(update={"path": path, "size": path.stat().st_size,
                                                  "content_type": "video/mp4"}))
        else:
            result.append(asset)
    return result


def append_source_notes(task_dir: Path, shots: Sequence[AnnotatedShot], uploads: Sequence[UploadedAsset]) -> None:
    for shot in shots:
        if 0 <= shot.source_index < len(uploads) and uploads[shot.source_index].note:
            context = f"用户素材备注（未经画面验证，仅供检索参考）：{uploads[shot.source_index].note}"
            if context not in shot.search_text:
                base = shot.search_text or " ".join([shot.description or "", *shot.keywords])
                shot.search_text = f"{base}\n{context}".strip()
    write_json_atomic(task_dir / "shots_annotated.json", [s.model_dump(mode="json") for s in shots])


def align_student_narration(sentences: Sequence[Sentence], transcript: ASRTranscript, duration: float) -> list[tuple[float, float, list[TTSWordTiming]]]:
    """Exact normalized text, contiguous ASR utterances, no invented word clocks.

    Seed's current adapter exposes utterances only. A timing word here therefore
    represents an entire real ASR utterance (recorded in the result manifest).
    Never split an utterance proportionally to fit a script/screen boundary.
    """
    def fail() -> ValueError:
        return ValueError("自己的配音无法可靠对齐稿件。请按正文逐句完整朗读、句间停顿，不朗读标题；核对漏读/重复/数字读法后重新上传，或选择 AI 配音。")

    if not sentences or not math.isfinite(duration) or duration <= 0:
        raise fail()
    units = [(_alignment_text(s.text).casefold(), s) for s in sentences]
    utterances = [u for u in transcript.utterances if _alignment_text(u.text)]
    spoken = [_alignment_text(u.text).casefold() for u in utterances]
    if any(not text for text, _ in units) or "".join(t for t, _ in units) != "".join(spoken):
        raise fail()
    if _alignment_text(transcript.text).casefold() != "".join(spoken):
        raise fail()
    previous = 0.0
    for u in utterances:
        start, end = u.start_time_ms / 1000, u.end_time_ms / 1000
        if not u.definite or not previous <= start < end <= duration:
            raise fail()
        previous = end
    groups: list[list[Any]] = []
    index = 0
    for target, _ in units:
        text = ""
        group = []
        while index < len(utterances) and len(text) < len(target):
            text += spoken[index]
            group.append(utterances[index])
            index += 1
        if text != target or not group:
            raise fail()
        groups.append(group)
    if index != len(utterances):
        raise fail()
    # Keep the full recording, including leading/trailing audio and pauses. A
    # boundary must have >=120ms of observed separation; midpoints protect edges.
    boundaries = [0.0]
    for left, right in zip(groups, groups[1:]):
        if right[0].start_time_ms - left[-1].end_time_ms < 120:
            raise fail()
        boundaries.append((left[-1].end_time_ms + right[0].start_time_ms) / 2000)
    boundaries.append(duration)
    result = []
    for group, start, end in zip(groups, boundaries, boundaries[1:]):
        words = [TTSWordTiming(text=u.text, start=u.start_time_ms / 1000 - start,
                               end=u.end_time_ms / 1000 - start) for u in group]
        result.append((start, end, words))
    return result


async def synthesize_student_narration(
    task_dir: Path, sentences: Sequence[Sentence], settings: Settings,
    progress: Callable[[int, int, str], None],
) -> list[SentenceTiming]:
    source = task_dir / "own_voice.wav"
    duration = await probe_audio_duration(source, task_dir)
    async with create_asr_provider(settings) as provider:
        provider.validate_configuration()
        transcript = await provider.transcribe(source)
    # Validate every sentence before writing timing/audio/result artifacts.
    aligned = align_student_narration(sentences, transcript, duration)
    voice_id = "student-" + hashlib.sha256(source.read_bytes()).hexdigest()[:24]
    paths = [task_dir / "student_audio" / f"{s.sentence_id}.wav" for s in sentences]
    await _split_continuous_group_audio(task_dir, source, paths, [(a, b) for a, b, _ in aligned])
    timings: list[SentenceTiming] = []
    for i, (sentence, path, (start, end, words)) in enumerate(zip(sentences, paths, aligned, strict=True)):
        actual = await probe_audio_duration(path, task_dir)
        if abs(actual - (end - start)) > 0.03:
            raise ValueError("自录配音分句音频时长校验失败。")
        timings.append(SentenceTiming(
            sentence_id=sentence.sentence_id, text=sentence.text,
            audio_path=path.relative_to(task_dir).as_posix(), duration=actual,
            start=0, end=actual, audio_kind="sync", tts_group_id=None,
            voice_profile_id=voice_id, words=words, gap_after=0.12 if i + 1 < len(sentences) else 0,
        ))
        progress(i + 1, len(sentences), "自录配音已按真实 ASR 时间戳对齐")
    timings = await rebuild_narration_from_existing(
        task_dir, timings, [s.sentence_id for s in sentences],
        timings_path=task_dir / "timings.json", narration_path=task_dir / "narration.m4a",
        narration_profile_path=task_dir / "narration_profile.json",
    )
    write_json_atomic(task_dir / "student_narration.json", {
        "schema_version": 1, "audio_source": "student_recording", "transcript_verified": True,
        "alignment": "exact_nfkc_alphanumeric_casefold", "text_coverage": 1.0,
        "timestamp_granularity": "asr_utterance", "voice_profile_id": voice_id,
        "source_audio": "own_voice.wav", "source_duration": duration,
        "transcript": transcript.model_dump(mode="json"),
        "units": [{"sentence_id": s.sentence_id, "source_start": a, "source_end": b,
                   "audio_path": t.audio_path} for s, (a, b, _), t in zip(sentences, aligned, timings, strict=True)],
    })
    return timings