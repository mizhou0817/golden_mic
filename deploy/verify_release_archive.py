from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path, PurePosixPath

if __package__:
    from .frontend_binding import BINDING_NAME, MANIFEST_NAME, MAX_RECEIPT_BYTES, asset_name, parse_binding, validate_binding
else:  # The deployment scripts invoke this verifier by filename.
    from frontend_binding import BINDING_NAME, MANIFEST_NAME, MAX_RECEIPT_BYTES, asset_name, parse_binding, validate_binding


MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 5000
REQUIRED_MEMBERS = {
    "golden-mic/backend/assets/fonts/NotoSansSC-Variable.ttf",
    "golden-mic/backend/assets/fonts/OFL.txt",
    "golden-mic/backend/main.py",
    "golden-mic/backend/frontend_static.py",
    # Shipped even though its guarded loop factory is opt-in at runtime.
    "golden-mic/backend/windows_asyncio.py",
    "golden-mic/backend/preflight.py",
    "golden-mic/backend/readiness.py",
    "golden-mic/backend/drafts.py",
    "golden-mic/backend/admission.py",
    "golden-mic/backend/v2_editing.py",
    "golden-mic/backend/mode_pipeline.py",
    "golden-mic/backend/production_modes.py",
    "golden-mic/backend/mode_rules.json",
    "golden-mic/backend/speech_analysis.py",
    "golden-mic/backend/local_speech_bundle.py",
    "golden-mic/backend/providers/local_speech.py",
    "golden-mic/backend/task_metadata.py",
    "golden-mic/docs/RUNBOOK.md",
    "golden-mic/docs/V2_IMPLEMENTATION_20260929.md",
    "golden-mic/docs/DESIGN-MAP.md",
    "golden-mic/deploy/frontend_binding.py",
    "golden-mic/deploy/deploy_release.sh",
    "golden-mic/deploy/install_host.sh",
    "golden-mic/deploy/nginx/golden-mic.conf.template",
    "golden-mic/deploy/nginx/golden-mic-proxy.conf",
    "golden-mic/deploy/rollback_release.sh",
    "golden-mic/deploy/run_with_environment.py",
    "golden-mic/deploy/systemd/golden-mic.service",
    "golden-mic/deploy/validate_target_host.sh",
    "golden-mic/deploy/validate_environment_file.py",
    "golden-mic/deploy/verify_python_environment.py",
    "golden-mic/deploy/verify_local_speech_bundle.py",
    "golden-mic/deploy/verify_release_archive.py",
    "golden-mic/frontend/dist/ASSET_MANIFEST.sha256",
    "golden-mic/frontend/dist/index.html",
    "golden-mic/pyproject.toml",
    "golden-mic/requirements-production.lock",
    "golden-mic/sbom.cdx.json",
    "golden-mic/uv.lock",
    "golden-mic/vendor/scenedetect-0.7.1-py3-none-any.whl",
}
FORBIDDEN_PARTS = {".env", ".venv", "data", "eval_sample", "node_modules", "private-models"}


def forbidden_payload(name: str) -> bool:
    """Git ignore is not a packaging boundary: reject private payloads too."""
    path = PurePosixPath(name.lower())
    leaf = path.name
    parts = path.parts[1:] if path.parts and path.parts[0] == "golden-mic" else path.parts
    return (any(part.casefold() in FORBIDDEN_PARTS for part in path.parts)
            or (leaf.startswith(".env") and leaf != ".env.example")
            or leaf.endswith((".env", ".pem", ".key", ".pfx", ".p12", ".onnx", ".safetensors", ".pt", ".pth"))
            or parts[:3] == ("backend", "assets", "samples")
            or "/artifacts/" in "/" + path.as_posix() + "/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Golden Mic release archive before root extraction.")
    parser.add_argument("archive", type=Path)
    arguments = parser.parse_args()
    validate_release_archive(arguments.archive.resolve())
    print(f"release_archive=valid path={arguments.archive.name}")
    return 0


def validate_release_archive(path: Path, *, require_binding: bool = False) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("Release archive does not exist or is empty.")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RuntimeError("Release archive exceeds the compressed size limit.")

    names: set[str] = set()
    total_size = 0
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not 1 <= len(members) <= MAX_MEMBERS:
            raise RuntimeError("Release archive member count is invalid.")
        for member in members:
            _validate_member(member)
            if member.name in names:
                raise RuntimeError(f"Duplicate release archive member: {member.name}")
            names.add(member.name)
            if member.name in REQUIRED_MEMBERS and not member.isfile():
                raise RuntimeError(f"Required release member is not a regular file: {member.name}")
            if member.isfile():
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise RuntimeError(f"Release member size is invalid: {member.name}")
                total_size += member.size
                if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise RuntimeError("Release archive exceeds the uncompressed size limit.")

        missing = sorted(REQUIRED_MEMBERS - names)
        if missing:
            raise RuntimeError("Release archive is missing required members: " + ", ".join(missing))
        # Legacy tar schema remains accepted with CURRENT required members,
        # not grandfathered omissions. Never ignore a supplied receipt;
        # current builders always require one against their actual source tree.
        prefix = "golden-mic/frontend/dist/"
        binding_name = prefix + BINDING_NAME
        if require_binding and binding_name not in names:
            raise RuntimeError("Current release archive is missing its frontend build binding.")
        if binding_name in names:
            def read_small(name: str) -> bytes:
                member = archive.getmember(name)
                if not member.isfile() or member.size > MAX_RECEIPT_BYTES:
                    raise RuntimeError("Invalid frontend receipt/manifest member.")
                stream = archive.extractfile(member)
                if stream is None:
                    raise RuntimeError("Missing frontend receipt stream.")
                with stream:
                    return stream.read()

            assets: dict[str, str] = {}
            for member in members:
                if not member.name.startswith(prefix) or not member.isfile():
                    continue
                name = member.name[len(prefix):]
                if name in {BINDING_NAME, MANIFEST_NAME}:
                    continue
                if not asset_name(name):
                    raise RuntimeError("Unexpected bound frontend archive asset.")
                stream = archive.extractfile(member)
                if stream is None:
                    raise RuntimeError("Missing frontend asset stream.")
                with stream:
                    digest = hashlib.sha256()
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                    assets[name] = digest.hexdigest()
            validate_binding(parse_binding(read_small(binding_name)), assets,
                             read_small(prefix + MANIFEST_NAME),
                             mode_rules_hash=hashlib.sha256(read_small("golden-mic/backend/mode_rules.json")).hexdigest())


def _validate_member(member: tarfile.TarInfo) -> None:
    name = member.name
    if not name or "\\" in name or any(ord(character) < 32 for character in name):
        raise RuntimeError(f"Release archive member name is invalid: {name!r}")
    pure_path = PurePosixPath(name)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise RuntimeError(f"Unsafe release archive path: {name}")
    if pure_path.as_posix() != name or any(part.endswith((".", " ")) or ":" in part for part in pure_path.parts):
        raise RuntimeError(f"Non-canonical release archive path: {name}")
    if len(pure_path.parts) < 2 or pure_path.parts[0] != "golden-mic":
        raise RuntimeError(f"Release archive member is outside golden-mic/: {name}")
    if forbidden_payload(name):
        raise RuntimeError(f"Forbidden release archive path: {name}")
    if not (member.isfile() or member.isdir()):
        raise RuntimeError(f"Release archive links/devices are forbidden: {name}")
    if member.mode & 0o6000:
        raise RuntimeError(f"Release archive setuid/setgid bits are forbidden: {name}")


if __name__ == "__main__":
    raise SystemExit(main())
