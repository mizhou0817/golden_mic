from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deploy.frontend_binding import canonical_name, manifest_hashes, read_regular, regular_file, validate_frontend_binding
from deploy.verify_release_archive import FORBIDDEN_PARTS, REQUIRED_MEMBERS, forbidden_payload, validate_release_archive

INCLUDED_FILES = (
    "README.md",
    "docs/README.md",
    "docs/QUICKSTART.md",
    "docs/USER_GUIDE.md",
    "docs/CONFIGURATION.md",
    "docs/TROUBLESHOOTING.md",
    "docs/DEVELOPMENT.md",
    "docs/CORE_WORKSPACE_20260927.md",
    "docs/CORE_WORKSPACE_VALIDATION_20260927.md",
    "docs/RUNBOOK.md",
    "docs/V2_IMPLEMENTATION_20260929.md",
    # Current source builds ship the latest validation record. Dated evidence
    # is documentation, not a required runtime anchor for historical archives.
    "docs/V2_VALIDATION_20260930.md",
    "docs/V2_MODEL_SELECTION_20261002.md",
    "docs/V2_PRODUCT_PROGRESS_20261002.md",
    "docs/V2_PRODUCT_DELIVERY_20261003.md",
    "docs/V2_DESIGN_REVIEW_20260930.md",
    "docs/v2-model-selection-20261002.json",
    "docs/V2_CURRENT_CLOSURE_20260930.md",
    "docs/DESIGN-MAP.md",
    "docs/WORKBENCH_API.md",
    "docs/STUDIO_API.md",
    "docs/MEDIA_INPUT_API.md",
    # Explicit historical documentation only, never recursive evidence/media.
    "docs/CLASSROOM_API.md",
    "docs/CLASSROOM_QUEUE_API.md",
    "docs/CLOUD_ACCEPTANCE_20260923.md",
    "docs/CLOUD_API.md",
    "docs/CLOUD_CAPABILITY_PLAN_20260923.md",
    "docs/MOTION_JITTER_FIX_20260927.md",
    "docs/PROTOTYPE_CAPABILITY_MATRIX_20260925.md",
    "docs/PROTOTYPE_IMPLEMENTATION_20260925.md",
    "docs/PROTOTYPE_REFACTOR_20260923.md",
    "docs/REFACTOR_CLEANUP_20260923.md",
    "docs/THREE_MODE_API_20260928.md",
    "docs/THREE_MODE_IMPLEMENTATION_20260928.md",
    "docs/THREE_MODE_VALIDATION_20260928.md",
    "requirements.txt",
    "requirements-production.lock",
    "sbom.cdx.json",
    "pyproject.toml",
    "uv.lock",
)
INCLUDED_DIRECTORIES = (
    "backend",
    "frontend/dist",
    "vendor",
    "deploy",
)
EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
FIXED_MTIME = 1785196800  # 2026-07-28T00:00:00Z


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a minimal Golden Mic Linux release archive.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontend-dir", help="Existing frontend/dist or direct frontend/dist-canary-<safe-label>; never builds assets.")
    arguments = parser.parse_args()
    frontend_dir = _select_frontend_dir(arguments.frontend_dir)
    _no_links(arguments.output.absolute(), allow_missing=True)
    output = arguments.output.resolve()
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    if output.is_relative_to(ROOT.resolve()):
        raise SystemExit("Refusing release output inside the input tree.")
    if os.path.lexists(output) or os.path.lexists(checksum_path):
        raise SystemExit("Refusing existing release output/checksum.")

    required = [frontend_dir if item == "frontend/dist" else ROOT / item
                for item in (*INCLUDED_FILES, *INCLUDED_DIRECTORIES)]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        raise SystemExit("Missing release inputs: " + ", ".join(missing))
    if (ROOT / ".env") in required:
        raise SystemExit("Refusing to include .env in a release.")

    # Preserve the legacy zero-argument entry points, including explicit dist.
    selected = frontend_dir != ROOT / "frontend/dist"
    if selected:
        _validate_generated_artifacts(frontend_dir)
        entries = _release_entries(frontend_dir)
    else:
        _validate_generated_artifacts()
        entries = _release_entries()

    missing_members = REQUIRED_MEMBERS - {"golden-mic/" + relative.as_posix() for _, relative in entries}
    if missing_members:
        raise SystemExit("Missing required release members: " + ", ".join(sorted(missing_members)))
    output.parent.mkdir(parents=True, exist_ok=True)
    _no_links(output.parent)
    # Exclusive creation also closes the ordinary precheck/open race. Never
    # truncate an existing archive or checksum, even if a later check fails.
    with output.open("xb") as raw_output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_output, mtime=FIXED_MTIME) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for source, relative in entries:
                    payload = read_regular(source)
                    info = tarfile.TarInfo(PurePosixPath("golden-mic", *relative.parts).as_posix())
                    info.size = len(payload)
                    info.mtime = FIXED_MTIME
                    info.mode = 0o755 if source.suffix == ".sh" else 0o644
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    archive.addfile(info, io.BytesIO(payload))

    # Recheck freshness after packaging, then validate the bytes actually packed.
    validate_frontend_binding(ROOT, frontend_dir)
    validate_release_archive(output, require_binding=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with checksum_path.open("x", encoding="ascii", newline="\n") as checksum:
        checksum.write(f"{digest}  {output.name}\n")
    print(f"archive={output} files={len(entries)} sha256={digest}")
    return 0


def _no_links(path: Path, *, allow_missing: bool = False) -> None:
    for ancestor in reversed((path, *path.parents)):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            if allow_missing:
                return
            raise
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Release paths may not traverse links/junctions.")


def _select_frontend_dir(value: str | None) -> Path:
    if value is None:
        return ROOT / "frontend/dist"
    # Validate the raw spelling BEFORE Path normalizes dot segments or trailing
    # separators. Absolute Windows paths accept either separator, not aliases.
    raw = value.replace("\\", "/") if os.name == "nt" else value
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    name = candidate.name
    expected = ROOT / "frontend" / name
    if (name != "dist" and not re.fullmatch(r"dist-canary-[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name)
            or raw not in {"frontend/" + name, expected.as_posix()}):
        raise SystemExit("--frontend-dir must select a canonical direct frontend/dist[-canary-<safe-label>] directory.")
    try:
        _no_links(expected)
        # Path equality on Windows is case-insensitive; directory entries are
        # required to prove the exact on-disk spelling, not merely resolution.
        if ("frontend" not in {p.name for p in ROOT.iterdir()}
                or name not in {p.name for p in (ROOT / "frontend").iterdir()}
                or not expected.is_dir()):
            raise SystemExit("--frontend-dir is missing or case-aliased.")
    except FileNotFoundError as error:
        raise SystemExit("--frontend-dir must be an existing directory.") from error
    return expected


def _release_entries(frontend_dir: Path | None = None) -> list[tuple[Path, Path]]:
    entries: list[tuple[Path, Path]] = []

    def add(path: Path, relative: Path) -> None:
        if (not canonical_name(relative.as_posix())
                or any(part.casefold() in FORBIDDEN_PARTS for part in relative.parts)):
            raise RuntimeError("Forbidden release input path.")
        if forbidden_payload(relative.as_posix()):
            raise RuntimeError("Private configuration/model/sample is not a release input.")
        regular_file(path)
        entries.append((path, relative))

    for relative_name in INCLUDED_FILES:
        path = ROOT / relative_name
        add(path, Path(relative_name))
    for directory_name in INCLUDED_DIRECTORIES:
        directory = frontend_dir if directory_name == "frontend/dist" and frontend_dir is not None else ROOT / directory_name
        _no_links(directory)
        for path in directory.rglob("*"):
            relative = Path(directory_name) / path.relative_to(directory)
            if any(part.casefold() in FORBIDDEN_PARTS for part in relative.parts):
                raise RuntimeError("Forbidden release input path.")
            _no_links(path)
            if not path.is_file() or _excluded(relative):
                continue
            add(path, relative)
    return sorted(entries, key=lambda item: item[1].as_posix())


def _excluded(path: Path) -> bool:
    return any(part in EXCLUDED_NAMES for part in path.parts) or path.suffix in EXCLUDED_SUFFIXES


def _validate_generated_artifacts(frontend_dir: Path | None = None) -> None:
    # Fail on missing/stale source binding before importing runtime settings or
    # invoking dependency tools. A post-Vite asset-only manifest is insufficient.
    dist = frontend_dir if frontend_dir is not None else ROOT / "frontend/dist"
    validate_frontend_binding(ROOT, dist)
    if frontend_dir is not None:
        # Binding validation already verifies every manifest hash/path and
        # rejects unlisted files. Retain readiness's additional content gates
        # without redirecting its process-global runtime constants.
        if not read_regular(dist / "index.html"):
            raise RuntimeError("Selected frontend index.html is empty.")
        if len(manifest_hashes(read_regular(dist / "ASSET_MANIFEST.sha256"))) < 3:
            raise RuntimeError("Selected frontend asset manifest has too few entries.")
    from backend.readiness import validate_font_asset, validate_frontend_dist

    if frontend_dir is None:
        validate_frontend_dist()
    validate_font_asset()
    subprocess.run(["uv", "lock", "--check"], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory(prefix="golden-mic-release-check-") as directory:
        temporary_root = Path(directory)
        requirements_path = temporary_root / "requirements-production.lock"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-header",
                "--output-file",
                str(requirements_path),
            ],
            cwd=ROOT,
            check=True,
        )
        if requirements_path.read_bytes() != (ROOT / "requirements-production.lock").read_bytes():
            raise RuntimeError("requirements-production.lock is not synchronized with uv.lock.")
        sbom_path = temporary_root / "sbom.cdx.json"
        subprocess.run(
            [sys.executable, str(ROOT / "deploy" / "generate_sbom.py"), "--output", str(sbom_path)],
            cwd=ROOT,
            check=True,
        )
        if sbom_path.read_bytes() != (ROOT / "sbom.cdx.json").read_bytes():
            raise RuntimeError("sbom.cdx.json is not synchronized with uv.lock.")


if __name__ == "__main__":
    raise SystemExit(main())
