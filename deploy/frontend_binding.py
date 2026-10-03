"""Stdlib-only frontend build receipts; hashes are not compiler attestations.

The builder checks the COMPLETE current source inventory. An archive has no
frontend sources, so it checks allowed references, required anchors and assets,
including the packaged mode rules. Old archives without receipts remain valid.
Keep the source policy in sync with frontend/scripts/write-manifest.mjs.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, cast


BINDING_NAME = "MODE_BUILD_BINDING.json"
MANIFEST_NAME = "ASSET_MANIFEST.sha256"
SOURCE_FILES = frozenset({
    "frontend/index.html", "frontend/package.json", "frontend/package-lock.json",
    "frontend/vite.config.ts", "frontend/postcss.config.cjs", "frontend/tailwind.config.ts",
    "frontend/tsconfig.json", "frontend/tsconfig.node.json", "backend/mode_rules.json",
})
CONFIG_NAME = re.compile(r"(?:(?:vite|postcss|tailwind)\.config\.(?:[cm]?[jt]s)|tsconfig(?:\.[A-Za-z0-9_-]+)*\.json)")
SHA256 = re.compile(r"[a-f0-9]{64}")
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def canonical_name(name: object) -> bool:
    return (isinstance(name, str) and bool(name)
            and not re.search(r'[\\\x00-\x1f\x7f<>:"|?*]', name)
            and all(part and part not in {".", ".."} and not part.endswith((".", " "))
                    for part in name.split("/")))


def source_name(name: str) -> bool:
    if not canonical_name(name):
        return False
    parts = PurePosixPath(name).parts
    return (name in SOURCE_FILES or name.startswith("frontend/src/")
            or len(parts) == 2 and parts[0] == "frontend" and CONFIG_NAME.fullmatch(parts[1]) is not None)


def asset_name(name: str) -> bool:
    return canonical_name(name) and (name == "index.html" or name.startswith("assets/"))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError("Duplicate frontend binding JSON key.")
        result[key] = value
    return result


def parse_binding(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_RECEIPT_BYTES:
        raise RuntimeError("Frontend binding is too large.")
    try:
        value = json.loads(payload, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Invalid frontend binding JSON.") from error
    if not isinstance(value, dict):
        raise RuntimeError("Frontend binding must be an object.")
    return cast(dict[str, Any], value)


def _hash_map(value: Any, *, sources: bool) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise RuntimeError("Missing frontend hash inventory.")
    predicate = source_name if sources else asset_name
    result: dict[str, str] = {}
    for name, digest in cast(dict[object, object], value).items():
        if not isinstance(name, str) or not predicate(name) or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise RuntimeError("Invalid frontend hash reference.")
        result[name] = digest
    if len({name.casefold() for name in result}) != len(result):
        raise RuntimeError("Case-aliased frontend hash references.")
    if sources:
        if not SOURCE_FILES.issubset(result) or not any(name.startswith("frontend/src/") for name in result):
            raise RuntimeError("Incomplete frontend source inventory.")
    elif "index.html" not in result or not any(name.startswith("assets/") for name in result):
        raise RuntimeError("Incomplete frontend asset inventory.")
    return result


def manifest_hashes(payload: bytes) -> dict[str, str]:
    if len(payload) > MAX_RECEIPT_BYTES:
        raise RuntimeError("Frontend asset manifest is too large.")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise RuntimeError("Invalid frontend asset manifest encoding.") from error
    result: dict[str, str] = {}
    for line in text.splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise RuntimeError("Invalid frontend asset manifest line.")
        digest, name = line[:64], line[66:]
        if name in result:
            raise RuntimeError("Duplicate frontend asset manifest entry.")
        result[name] = digest
    return _hash_map(result, sources=False)


def validate_binding(value: dict[str, Any], assets: Mapping[str, str], manifest: bytes,
                     *, sources: Mapping[str, str] | None = None,
                     mode_rules_hash: str | None = None) -> None:
    if value.get("kind") not in {"frontend-source-build", "synthetic-mode-custom-build"}:
        raise RuntimeError("Unsupported frontend build binding kind.")
    before = _hash_map(value.get("sourceHashes"), sources=True)
    after = _hash_map(value.get("sourceHashesAfter"), sources=True)
    if value.get("sourceUnchangedDuringBuild") is not True or before != after:
        raise RuntimeError("Frontend source drift during build.")
    if sources is not None and before != dict(sources):
        raise RuntimeError("Frontend binding is stale or has an incomplete source inventory; rebuild, do not re-sign.")
    if mode_rules_hash is not None and before["backend/mode_rules.json"] != mode_rules_hash:
        raise RuntimeError("Packaged mode rules do not match the frontend build.")
    bound_assets = _hash_map(value.get("assets"), sources=False)
    if bound_assets != dict(assets) or bound_assets != manifest_hashes(manifest):
        raise RuntimeError("Frontend binding/manifest/assets mismatch.")
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    if value.get("kind") == "frontend-source-build":
        if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
            raise RuntimeError("Unsupported frontend binding schema.")
        if value.get("assetManifestSHA256") != manifest_digest:
            raise RuntimeError("Frontend asset manifest binding mismatch.")
    elif "assetManifestSHA256" in value and value["assetManifestSHA256"] != manifest_digest:
        raise RuntimeError("Frontend asset manifest binding mismatch.")


def regular_file(path: Path) -> None:
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Frontend inputs may not traverse links/junctions.")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("Frontend input must be a single-link regular file.")


def read_regular(path: Path) -> bytes:
    regular_file(path)
    before = path.stat()
    payload = path.read_bytes()
    regular_file(path)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or len(payload) != before.st_size:
        raise RuntimeError("Frontend input changed while reading.")
    return payload


def _files(directory: Path) -> Iterator[Path]:
    # Check directories before descending; never follow an untrusted tree link.
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise RuntimeError("Invalid frontend directory.")
    for path in sorted(directory.iterdir()):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("Frontend tree contains links/junctions.")
        if stat.S_ISDIR(info.st_mode):
            yield from _files(path)
        else:
            yield path


def source_hashes(root: Path) -> dict[str, str]:
    names = set(SOURCE_FILES)
    names.update(path.relative_to(root).as_posix() for path in _files(root / "frontend/src"))
    names.update("frontend/" + path.name for path in (root / "frontend").iterdir()
                 if CONFIG_NAME.fullmatch(path.name))
    result = {name: hashlib.sha256(read_regular(root / name)).hexdigest() for name in sorted(names)}
    return _hash_map(result, sources=True)


def validate_frontend_binding(root: Path, dist: Path) -> None:
    """No backend/config import, environment loading or generated output writes."""
    try:
        receipt = parse_binding(read_regular(dist / BINDING_NAME))
        manifest = read_regular(dist / MANIFEST_NAME)
        assets: dict[str, str] = {}
        for path in _files(dist):
            name = path.relative_to(dist).as_posix()
            if name in {BINDING_NAME, MANIFEST_NAME}:
                continue
            if not asset_name(name):
                raise RuntimeError("Unexpected frontend output file.")
            assets[name] = hashlib.sha256(read_regular(path)).hexdigest()
        validate_binding(receipt, assets, manifest, sources=source_hashes(root))
    except FileNotFoundError as error:
        raise RuntimeError("Missing frontend build binding/input; run a source-bound build, not manifest-only signing.") from error