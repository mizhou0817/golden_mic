"""Classroom previews are frames of the published film, never discarded source media."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .media import run_logged_command
from .revisions import local_file, read_json, revision_dir

_LIMIT = asyncio.Semaphore(2)


def committed_root(record: Any) -> Path:
    root = Path(record.task_dir)
    if (root / "revisions" / f"r{record.revision}" / "revision.json").is_file():
        return revision_dir(root, record.revision)
    return root


def _frame_time(root: Path, shot_id: int | None) -> float:
    edl = read_json(root, "edl.json")
    for item in edl:
        cursor = float(item["timeline_start"])
        for clip in item["clips"]:
            duration = float(clip["out"]) - float(clip["in"]) + float(clip.get("freeze_pad") or 0)
            if shot_id is None or clip["shot_id"] == shot_id:
                return max(0, cursor + min(duration / 2, 0.5))
            cursor += duration
    raise HTTPException(404, "该镜头未出现在当前成片中。")


async def public_preview(record: Any, shot_id: int | None = None) -> Path:
    task_root = Path(record.task_dir).resolve()
    root = committed_root(record)
    if not (root / "final.mp4").is_file() or not (root / "edl.json").is_file():
        raise HTTPException(404, "成片预览尚未生成。")
    final = local_file(root, "final.mp4")
    point = _frame_time(root, shot_id)
    stat = final.stat()
    source = final.relative_to(task_root).as_posix()
    key = hashlib.sha256(f"{source}:{record.revision}:{stat.st_size}:{stat.st_mtime_ns}:{point:.6f}".encode()).hexdigest()[:24]
    # Pin the input before awaiting, but keep ALL runtime writes outside immutable
    # revisions. Include source location: legacy final.mp4 -> revisions/rN/final.mp4
    # can preserve revision/size/mtime via copy2 and must not reuse the legacy key.
    output = local_file(task_root, f"public_previews/{key}.jpg", exists=False)
    if output.is_file():
        return output
    async with _LIMIT:
        if output.is_file():
            return output
        output.parent.mkdir(exist_ok=True)
        # run_logged_command appends task.log in its directory. Validate that
        # implicit output too, so a cache/log link cannot write into a revision.
        local_file(task_root, "public_previews/task.log", exists=False)
        temporary = output.with_name(f"{key}-{uuid4().hex}.jpg")
        try:
            await asyncio.wait_for(run_logged_command([
                "ffmpeg", "-y", "-nostdin", "-ss", f"{point:.6f}",
                "-protocol_whitelist", "file,pipe", "-f", "mov", "-i", str(final),
                "-frames:v", "1", "-an", "-vf", "scale=480:-2", "-q:v", "3",
                "-threads", "1", "-update", "1", str(temporary),
            ], output.parent, "Published film preview"), 30)
            temporary.replace(output)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise HTTPException(503, "暂时无法生成成片预览，请稍后重试。") from exc
        finally:
            temporary.unlink(missing_ok=True)
    return output