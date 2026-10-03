"""Rebuild the official SceneDetect wheel with a headless OpenCV dependency.

Only the wheel METADATA requirement is changed. Every build verifies the exact
upstream artifact hash and rewrites RECORD before creating a deterministic wheel.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


UPSTREAM_FILENAME = "scenedetect-0.7.1-py3-none-any.whl"
UPSTREAM_SHA256 = "91b67902275b2e0d29a12f6d70435f04e0bd7852a6dc8a69c901c6069328dffa"
OUTPUT_FILENAME = UPSTREAM_FILENAME
ORIGINAL_REQUIREMENT = b"Requires-Dist: opencv-python\n"
PATCHED_REQUIREMENT = b"Requires-Dist: opencv-python-headless==5.0.0.93\n"
FIXED_TIMESTAMP = (2026, 7, 28, 0, 0, 0)


def main() -> int:
    output_directory = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="scenedetect-headless-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--no-deps",
                "--only-binary=:all:",
                "--dest",
                str(temporary_path),
                "scenedetect==0.7.1",
            ],
            check=True,
        )
        upstream_path = temporary_path / UPSTREAM_FILENAME
        if _sha256(upstream_path.read_bytes()) != UPSTREAM_SHA256:
            raise RuntimeError("SceneDetect upstream wheel SHA-256 does not match the audited artifact.")
        output_path = output_directory / OUTPUT_FILENAME
        output_path.write_bytes(_patched_wheel(upstream_path))
        print(f"{output_path.name} sha256={_sha256(output_path.read_bytes())}")
    return 0


def _patched_wheel(upstream_path: Path) -> bytes:
    with zipfile.ZipFile(upstream_path, "r") as archive:
        entries = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}

    metadata_paths = [name for name in entries if name.endswith(".dist-info/METADATA")]
    record_paths = [name for name in entries if name.endswith(".dist-info/RECORD")]
    if len(metadata_paths) != 1 or len(record_paths) != 1:
        raise RuntimeError("Unexpected SceneDetect wheel metadata layout.")
    metadata_path = metadata_paths[0]
    record_path = record_paths[0]
    metadata = entries[metadata_path][1].replace(b"\r\n", b"\n")
    if metadata.count(ORIGINAL_REQUIREMENT) != 1:
        raise RuntimeError("Expected exactly one opencv-python requirement in SceneDetect METADATA.")
    metadata = metadata.replace(ORIGINAL_REQUIREMENT, PATCHED_REQUIREMENT)
    entries[metadata_path] = (entries[metadata_path][0], metadata)

    record_buffer = io.StringIO(newline="")
    writer = csv.writer(record_buffer, lineterminator="\n")
    for name in sorted(entries):
        if name == record_path:
            continue
        payload = entries[name][1]
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")
        writer.writerow((name, f"sha256={digest}", len(payload)))
    writer.writerow((record_path, "", ""))
    entries[record_path] = (entries[record_path][0], record_buffer.getvalue().encode("utf-8"))

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(entries):
            original_info, payload = entries[name]
            info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = original_info.external_attr
            info.create_system = original_info.create_system
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
