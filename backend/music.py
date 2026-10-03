"""Background-music subsystem: a bundled royalty-free (CC0) underscore library,
deterministic mood selection, and a loudness-controlled bed prepared to the exact
narration duration. Ducking against the narration happens at final mix time.

Everything here is inert unless a task explicitly enables background music, so the
historical music-free render is unaffected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .media import run_logged_command
from .storage import write_json_atomic, write_text_log


MUSIC_MOODS = ("solemn", "neutral", "uplifting", "tense")
_MANIFEST_NAME = "manifest.json"


class MusicError(RuntimeError):
    """Raised when a requested music bed cannot be prepared."""


@dataclass(frozen=True)
class MusicTrack:
    file: str
    mood: str
    title: str
    tags: tuple[str, ...]
    path: Path


def load_music_library(library_dir: Path) -> list[MusicTrack]:
    manifest_path = library_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_tracks = data.get("tracks") if isinstance(data, dict) else None
    if not isinstance(raw_tracks, list):
        return []
    tracks: list[MusicTrack] = []
    for item in raw_tracks:
        if not isinstance(item, dict):
            continue
        file = str(item.get("file", "")).strip()
        if not file or "/" in file or "\\" in file or file.startswith("."):
            continue
        track_path = library_dir / file
        if not track_path.is_file():
            continue
        mood = str(item.get("mood", "neutral")).strip().lower()
        raw_tags = item.get("tags")
        tags = tuple(str(tag).strip().lower() for tag in raw_tags if str(tag).strip()) if isinstance(raw_tags, list) else ()
        tracks.append(
            MusicTrack(
                file=file,
                mood=mood if mood in MUSIC_MOODS else "neutral",
                title=str(item.get("title", file)),
                tags=tags,
                path=track_path,
            )
        )
    return tracks


def select_music_track(tracks: list[MusicTrack], mood: str) -> MusicTrack | None:
    """Pick the best track for a mood: exact mood, then tag match, then neutral, then first."""
    if not tracks:
        return None
    normalized = mood.strip().lower()
    for track in tracks:
        if track.mood == normalized:
            return track
    for track in tracks:
        if normalized in track.tags:
            return track
    for track in tracks:
        if track.mood == "neutral":
            return track
    return tracks[0]


async def prepare_music_bed(
    task_dir: Path,
    track: MusicTrack,
    *,
    target_duration: float,
    target_lufs: float,
    output_path: Path,
) -> Path:
    """Loop/trim the track to the narration duration, fade its edges, and set a low bed loudness."""
    if target_duration <= 0:
        raise MusicError("音乐床目标时长必须大于 0。")
    if not track.path.is_file():
        raise MusicError(f"音乐文件不存在：{track.file}")
    fade_out_start = max(0.0, target_duration - 3.0)
    audio_filter = (
        "afade=t=in:st=0:d=2,"
        f"afade=t=out:st={fade_out_start:.3f}:d=3,"
        f"loudnorm=I={target_lufs}:LRA=6:TP=-6,"
        "aresample=48000"
    )
    command = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(track.path),
        "-t",
        f"{target_duration:.6f}",
        "-af",
        audio_filter,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    await run_logged_command(command, task_dir, f"准备背景音乐床（{track.mood}）")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise MusicError("背景音乐床生成失败。")
    return output_path


def write_music_selection(
    task_dir: Path,
    *,
    mood: str,
    track: MusicTrack | None,
    resolved_by: str,
    output_path: Path | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "requested_mood": mood,
        "resolved_by": resolved_by,
        "track": None
        if track is None
        else {"file": track.file, "mood": track.mood, "title": track.title},
    }
    write_json_atomic(output_path or task_dir / "music_selection.json", payload)
    write_text_log(
        task_dir,
        f"背景音乐选曲 mood={mood} resolved_by={resolved_by} "
        f"track={'无可用曲目' if track is None else track.file}",
    )
    return payload
