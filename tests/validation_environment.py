"""Stdlib-only validation prerequisites; no app imports or automatic installs."""
from __future__ import annotations

import os
import shutil
import stat
import sys
from collections.abc import Mapping
from pathlib import Path


def _directory(path: Path) -> None:
    """Reject links/junctions, including linked ancestors, before any write."""
    if not path.is_absolute() or path.resolve() != path.absolute():
        raise RuntimeError("unsafe_validation_directory")
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        # Name-surrogate reparse points include Windows junctions/symlinks,
        # not ordinary OneDrive cloud placeholders.
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_reparse_tag", 0) & 0x20000000):
            raise RuntimeError("unsafe_validation_directory")


def evidence_parent(project: Path) -> Path:
    parent = project / "canary_test"
    _directory(parent)
    evidence = parent / "artifacts"
    try:
        evidence.mkdir()
    except FileExistsError:
        pass
    _directory(evidence)
    return evidence


def temporary_base(environment: Mapping[str, str], *, platform: str = sys.platform) -> Path:
    if platform == "win32":
        local = next((value for key, value in environment.items() if key.upper() == "LOCALAPPDATA"), "")
        base = Path(local) / "Temp"
    elif platform == "linux":
        # Do not inherit arbitrary TMPDIR/provider configuration before guards.
        base = Path("/tmp")
    else:
        raise RuntimeError("unsupported_validation_platform")
    _directory(base)
    return base


def media_tools(environment: Mapping[str, str], *, platform: str = sys.platform) -> tuple[Path, Path]:
    path = next((value for key, value in environment.items() if key.upper() == "PATH"), "")
    if platform == "win32":
        local = next((value for key, value in environment.items() if key.upper() == "LOCALAPPDATA"), "")
        if not local or not Path(local).is_absolute():
            raise RuntimeError("windows_media_tools_required")
        candidates = [file for package in (Path(local) / "Microsoft/WinGet/Packages").glob("Gyan.FFmpeg*")
                      for file in package.rglob("ffmpeg.exe") if file.with_name("ffprobe.exe").is_file()]
        if not candidates:
            raise RuntimeError("windows_media_tools_required")
        ffmpeg = max(candidates, key=lambda file: file.stat().st_mtime_ns)
        ffprobe = ffmpeg.with_name("ffprobe.exe")
    elif platform == "linux":
        encoder, probe = shutil.which("ffmpeg", path=path), shutil.which("ffprobe", path=path)
        if not encoder or not probe:
            raise RuntimeError("linux_media_tools_required")
        ffmpeg, ffprobe = Path(encoder), Path(probe)
    else:
        raise RuntimeError("unsupported_validation_platform")
    for tool in (ffmpeg, ffprobe):
        if (not tool.is_absolute() or tool.is_symlink() or not tool.is_file()
                or not os.access(tool, os.X_OK)):
            raise RuntimeError("unsafe_media_tool")
        _directory(tool.parent)
    if ffmpeg.parent != ffprobe.parent:
        raise RuntimeError("paired_media_tools_required")
    return ffmpeg, ffprobe