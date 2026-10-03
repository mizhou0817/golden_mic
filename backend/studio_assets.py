"""Private, content-addressed Studio images and 3D LUTs; no project imports.

Only this module publishes studio/assets/index.json. Files become visible LAST,
through an atomic index replacement; orphan files are never a catalog. Names,
extensions and paths are server generated, not taken from upload headers/JSON.

Cube subset: UTF-8 (optional BOM), blank lines and # comments; one mandatory
LUT_3D_SIZE 2..33, optional quoted TITLE (discarded), optional DOMAIN_MIN 0 0 0
and DOMAIN_MAX 1 1 1, then exactly size**3 RGB rows of finite numbers in [0,1].
Headers precede rows. Row order is the .cube red-fast, then green, then blue
order; it is NOT reordered or approximated with RGB curves. Canonical numbers
use nine significant decimal digits (half-up), LF and no title/comments/metadata.
Numeric tokens are <=64 characters, nonzero magnitude >=1e-999; lines <=512
characters, at most 50000 lines (including comments). Other directives fail.

Image dimensions are checked before decompression/probing. PNG ancillary data
and JPEG private APP/COM data are removed BEFORE invoking a decoder (including
EXIF, ICC profiles and compressed text). Only a fixed Adobe RGB/YUV encoding
flag may survive sanitization; no private fields do. EXIF orientation is ignored.
FFmpeg re-encodes pixels to 8-bit RGBA PNG; alpha is retained, metadata is not.
"""
from __future__ import annotations

import asyncio
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .media import run_logged_command

IMAGE_BYTES = 8 * 1024 * 1024
LUT_BYTES = 2 * 1024 * 1024
MAX_ASSETS = 64
MAX_DIMENSION = 4096
MAX_PIXELS = 4096 * 2160
MAX_LUT_SIZE = 33
MAX_JSON = 256 * 1024
# -fs is not a hard per-packet bound: one encoded PNG can exceed it. Reserve
# raw + sanitized input + a conservative full RGBA PNG packet BEFORE decoding.
IMAGE_WORK_BYTES = IMAGE_BYTES * 2 + MAX_PIXELS * 8 + MAX_JSON * 2
LUT_WORK_BYTES = LUT_BYTES * 2 + MAX_JSON * 2
LIMITS = {"image_bytes": IMAGE_BYTES, "lut_bytes": LUT_BYTES, "assets": MAX_ASSETS,
          "dimension": MAX_DIMENSION, "pixels": MAX_PIXELS, "lut_size": MAX_LUT_SIZE}
IMAGE_ID_PATTERN = r"^image_[0-9a-f]{24}$"
LUT_ID_PATTERN = r"^lut_[0-9a-f]{24}$"
_PNG = b"\x89PNG\r\n\x1a\n"
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?\Z")
T = TypeVar("T")


class AssetError(ValueError):
    """Only fixed, credential/path-free messages may cross the API boundary."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def is_image_id(value: str | None) -> bool:
    return isinstance(value, str) and re.fullmatch(IMAGE_ID_PATTERN, value) is not None


def is_lut_id(value: str | None) -> bool:
    return isinstance(value, str) and re.fullmatch(LUT_ID_PATTERN, value) is not None


def contained(root: Path, path: Path, *, exists: bool = True) -> Path:
    """Reject lexical escapes and links/junctions at EVERY existing component.

    Check lstat BEFORE resolve, including root/ancestors and dangling links.
    Name-surrogate reparse points include Windows junctions and symlinks, but
    not ordinary hydrated OneDrive cloud-file reparse points. This is a private
    single-worker application boundary, not an OS sandbox against a concurrent
    privileged filesystem writer.
    """
    root, path = Path(root).absolute(), Path(path).absolute()
    if ".." in root.parts or ".." in path.parts or path == root or not path.is_relative_to(root):
        raise HTTPException(403, "path escapes authorized task")
    try:
        cursor = Path(path.anchor)
        for part in path.parts[1:]:
            cursor /= part
            try:
                info = cursor.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
                raise HTTPException(403, "linked studio/source paths are not allowed")
        resolved_root = root.resolve(strict=True)
        result = path.resolve(strict=exists)
        if not result.is_relative_to(resolved_root) or result == resolved_root:
            raise HTTPException(403, "path escapes authorized task")
        return result
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, "studio/source path is unavailable") from exc


def bounded_read(root: Path, path: Path, limit: int) -> bytes:
    safe = contained(root, path)
    try:
        before = safe.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= limit:
            raise AssetError("asset is empty, linked or exceeds its byte limit")
        with safe.open("rb") as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise AssetError("asset changed while reading")
            data = source.read(limit + 1)
            after = os.fstat(source.fileno())
        contained(root, path)
        final = safe.stat()
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) != before.st_size or len(data) > limit or stamp(before) != stamp(after) or stamp(before) != stamp(final):
            raise AssetError("asset changed while reading")
        return data
    except OSError as exc:
        raise AssetError("asset is unavailable") from exc


def _dimensions(width: int, height: int) -> None:
    if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION and width * height <= MAX_PIXELS):
        raise AssetError("image dimensions exceed 4096 per side / 8847360 pixels")


def _png_document(data: bytes, *, canonical: bool = False, deflate: bool = False) -> tuple[bytes, int, int]:
    if not data.startswith(_PNG) or len(data) < 45:
        raise AssetError("invalid PNG signature/document")
    view = memoryview(data)
    cursor, count = 8, 0
    width = height = depth = color = interlace = 0
    seen: set[bytes] = set()
    chunks: list[bytes] = [_PNG]
    idats: list[memoryview] = []
    ended_idat = False
    palette_entries = 0
    while cursor < len(data):
        count += 1
        if count > 4096 or cursor + 12 > len(data):
            raise AssetError("invalid PNG chunk structure")
        length = struct.unpack_from(">I", data, cursor)[0]
        kind = data[cursor+4:cursor+8]
        end = cursor + length + 12
        if end > len(data) or not re.fullmatch(b"[A-Za-z]{4}", kind) or kind[2] & 32:
            raise AssetError("invalid PNG chunk structure")
        payload = view[cursor+8:end-4]
        if binascii.crc32(view[cursor+4:end-4]) & 0xFFFFFFFF != struct.unpack_from(">I", data, end-4)[0]:
            raise AssetError("invalid PNG checksum")
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise AssetError("animated PNG/APNG is not supported")
        if count == 1:
            if kind != b"IHDR" or length != 13:
                raise AssetError("PNG must start with one IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            _dimensions(width, height)
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if depth not in depths.get(color, set()) or compression or filtering or interlace not in {0, 1}:
                raise AssetError("unsupported PNG header")
            if canonical and (depth != 8 or color != 6 or interlace != 0):
                raise AssetError("stored image must be canonical 8-bit RGBA PNG")
        elif kind == b"IHDR":
            raise AssetError("duplicate PNG header")
        if kind == b"PLTE":
            if kind in seen or b"IDAT" in seen or color in {0, 4} or not length or length % 3 or length > 768:
                raise AssetError("invalid PNG palette")
            palette_entries = length // 3
            if color == 3 and palette_entries > 2**depth:
                raise AssetError("invalid PNG palette")
        if kind == b"tRNS":
            valid = (color == 0 and length == 2) or (color == 2 and length == 6) or (color == 3 and 0 < length <= palette_entries)
            if kind in seen or b"IDAT" in seen or not valid:
                raise AssetError("invalid PNG transparency")
        if kind == b"IDAT":
            if ended_idat or (color == 3 and not palette_entries):
                raise AssetError("invalid PNG image data order")
            idats.append(payload)
        elif b"IDAT" in seen:
            ended_idat = True
        if kind == b"IEND":
            if length or b"IDAT" not in seen or end != len(data):
                raise AssetError("PNG has missing image data or trailing bytes")
        keep = kind in {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}
        if not keep and not kind[0] & 32:
            raise AssetError("unsupported critical PNG chunk")
        if canonical and kind not in {b"IHDR", b"IDAT", b"IEND"}:
            raise AssetError("stored PNG contains unexpected metadata/chunks")
        if keep:
            chunks.append(data[cursor:end])
        seen.add(kind)
        cursor = end
    if b"IEND" not in seen:
        raise AssetError("truncated PNG document")
    if deflate:
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
        passes = [(0, 0, 1, 1)] if not interlace else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
        expected = 0
        for x, y, dx, dy in passes:
            w, h = max(0, (width-x+dx-1)//dx), max(0, (height-y+dy-1)//dy)
            if w and h:
                expected += h * (1 + (w * channels * depth + 7)//8)
        decoder, total = zlib.decompressobj(), 0
        try:
            for compressed in idats:
                for offset in range(0, len(compressed), 65536):
                    remaining = compressed[offset:offset+65536]
                    while remaining:
                        decoded = decoder.decompress(remaining, min(65536, expected-total+1))
                        total += len(decoded)
                        if total > expected or decoder.unused_data:
                            raise AssetError("PNG decompression exceeds its declared geometry or has trailing data")
                        remaining = decoder.unconsumed_tail
            if not decoder.eof or total != expected:
                raise AssetError("PNG image data is incomplete")
        except zlib.error as exc:
            raise AssetError("invalid PNG compressed image data") from exc
    return b"".join(chunks), width, height


def _jpeg_document(data: bytes) -> tuple[bytes, int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise AssetError("invalid JPEG signature")
    cursor, count, scans = 2, 0, 0
    width = height = 0
    adobe = False
    output = bytearray(b"\xff\xd8")
    while cursor < len(data):
        count += 1
        if count > 4096 or data[cursor] != 255:
            raise AssetError("invalid JPEG marker structure")
        start = cursor
        while cursor < len(data) and data[cursor] == 255:
            cursor += 1
        if cursor >= len(data):
            raise AssetError("truncated JPEG marker")
        marker = data[cursor]
        cursor += 1
        if marker == 0xD9:
            if not width or not scans or cursor != len(data):
                raise AssetError("JPEG must contain one image without trailing bytes/animation")
            output.extend(b"\xff\xd9")
            return bytes(output), width, height
        if marker in {0, 0xD8, 0x01} or 0xD0 <= marker <= 0xD7 or cursor + 2 > len(data):
            raise AssetError("invalid JPEG marker")
        length = struct.unpack_from(">H", data, cursor)[0]
        end = cursor + length
        if length < 2 or end > len(data):
            raise AssetError("truncated JPEG segment")
        payload = memoryview(data)[cursor+2:end]
        if marker in {0xC0, 0xC1, 0xC2}:
            if width or len(payload) < 6:
                raise AssetError("JPEG must contain one frame header")
            precision, height, width, components = struct.unpack_from(">BHHB", payload)
            _dimensions(width, height)
            if precision != 8 or components not in {1, 3} or len(payload) != 6 + components * 3:
                raise AssetError("only 8-bit grayscale/RGB JPEG is supported")
        elif marker == 0xDA:
            scans += 1
            if not width or scans > 64 or len(payload) < 6 or len(payload) != 4 + payload[0]*2:
                raise AssetError("invalid JPEG scan")
        elif marker == 0xE2 and bytes(payload[:4]) == b"MPF\x00":
            raise AssetError("multi-picture JPEG is not supported")
        elif marker == 0xEE and bytes(payload[:5]) == b"Adobe":
            if adobe or len(payload) != 12 or payload[-1] not in {0, 1}:
                raise AssetError("unsupported JPEG color encoding")
            adobe = True
            # APP14's transform bit affects the actual RGB/YUV interpretation,
            # not decoration. Preserve only that bit with fixed empty flags.
            output.extend(b"\xff\xee\x00\x0eAdobe\x00\x64\x00\x00\x00\x00" + bytes([payload[-1]]))
        elif not (0xE0 <= marker <= 0xEF or marker in {0xFE, 0xDB, 0xC4, 0xDD}):
            raise AssetError("unsupported JPEG marker/encoding")
        if not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            output.extend(data[start:end])
        cursor = end
        if marker == 0xDA:
            start = cursor
            while True:
                found = data.find(b"\xff", cursor)
                if found < 0:
                    raise AssetError("JPEG has no end marker")
                cursor = found + 1
                while cursor < len(data) and data[cursor] == 255:
                    cursor += 1
                if cursor >= len(data):
                    raise AssetError("truncated JPEG scan")
                if data[cursor] == 0 or 0xD0 <= data[cursor] <= 0xD7:
                    cursor += 1
                    continue
                output.extend(data[start:found])
                cursor = found
                break
    raise AssetError("truncated JPEG document")


def sanitize_image_document(data: bytes, content_type: str) -> tuple[bytes, int, int]:
    if not 0 < len(data) <= IMAGE_BYTES:
        raise AssetError("image exceeds 8 MiB or is empty", 413)
    if content_type == "image/png":
        return _png_document(data, deflate=True)
    if content_type == "image/jpeg":
        return _jpeg_document(data)
    raise AssetError("only image/png and image/jpeg are accepted", 415)


def canonical_cube(data: bytes) -> tuple[bytes, int]:
    if not 0 < len(data) <= LUT_BYTES:
        raise AssetError("LUT exceeds 2 MiB or is empty", 413)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise AssetError("LUT must be UTF-8 text") from exc
    if any((ord(c) < 32 and c not in "\t\r\n") or ord(c) == 127 for c in text):
        raise AssetError("LUT contains control characters")
    size = 0
    headers: set[str] = set()
    rows: list[str] = []

    def numbers(tokens: list[str]) -> list[Decimal]:
        if len(tokens) != 3 or any(len(v) > 64 or not _NUMBER.fullmatch(v) for v in tokens):
            raise AssetError("LUT rows/domains require exactly three decimal numbers")
        # Decimal checks precede float quantization: -1e-999 is negative, not
        # a permitted -0.0; a tiny nonzero DOMAIN_MIN is NOT the unit domain.
        values = [Decimal(v) for v in tokens]
        if any(not v.is_finite() or not 0 <= v <= 1 for v in values):
            raise AssetError("LUT numbers must be finite and within [0,1]")
        if any(v != 0 and v.adjusted() < -999 for v in values):
            raise AssetError("LUT numeric exponent exceeds the bounded decimal subset")
        return values

    def normalized(value: Decimal) -> str:
        # Canonicalize EXACT decimal input to nine significant digits with
        # explicit half-up rounding, independent of global Decimal context or
        # binary-float underflow. Nine digits preserve .cube float precision.
        if value == 0:
            return "0"
        exponent = value.adjusted()
        places = 8 - exponent
        rounded = value.quantize(Decimal((0, (1,), -places)), rounding=ROUND_HALF_UP)
        if rounded.adjusted() != exponent:
            exponent += 1
        if rounded == 1:
            return "1"
        if exponent >= -4:
            return format(rounded, "f").rstrip("0").rstrip(".")
        mantissa, scientific = format(rounded, "e").split("e")
        return mantissa.rstrip("0").rstrip(".") + "e" + str(int(scientific))

    for line_number, raw in enumerate(io.StringIO(text.replace("\r\n", "\n").replace("\r", "\n")), 1):
        if line_number > 50000 or len(raw) > 512:
            raise AssetError("LUT has too many lines or an overlong line")
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # A title is ignored, never interpreted as a path or FFmpeg expression.
        if line.startswith("TITLE"):
            if rows or "TITLE" in headers or not re.fullmatch(r'TITLE[ \t]+"[^"\\\r\n]{0,120}"[ \t]*(?:#.*)?', line):
                raise AssetError("invalid or repeated LUT TITLE")
            headers.add("TITLE")
            continue
        body = line.split("#", 1)[0]
        if not body.isascii():
            raise AssetError("LUT directives and numeric data must be ASCII")
        tokens = body.split()
        name = tokens[0]
        if name in {"LUT_3D_SIZE", "DOMAIN_MIN", "DOMAIN_MAX"}:
            if rows or name in headers:
                raise AssetError("LUT headers must occur once, before data rows")
            headers.add(name)
            if name == "LUT_3D_SIZE":
                if len(tokens) != 2 or not re.fullmatch(r"(?:[2-9]|[12][0-9]|3[0-3])", tokens[1]):
                    raise AssetError("LUT_3D_SIZE must be an integer from 2 to 33")
                size = int(tokens[1])
            elif numbers(tokens[1:]) != ([0]*3 if name == "DOMAIN_MIN" else [1]*3):
                raise AssetError("only unit-domain LUTs are supported")
        else:
            if not size or len(rows) >= size**3:
                raise AssetError("LUT requires exactly size cubed RGB rows after LUT_3D_SIZE")
            values = numbers(tokens)
            with localcontext() as context:
                context.prec = 80  # Tokens are capped at 64 characters.
                context.Emin, context.Emax = -2000, 2000
                rows.append(" ".join(normalized(v) for v in values))
    if not size or len(rows) != size**3:
        raise AssetError("LUT requires exactly size cubed RGB rows")
    canonical = (f"LUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n" + "\n".join(rows) + "\n").encode("ascii")
    if len(canonical) > LUT_BYTES:
        raise AssetError("canonical LUT exceeds 2 MiB", 413)
    return canonical, size


class _AssetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class ImageAsset(_AssetModel):
    id: Annotated[str, Field(pattern=IMAGE_ID_PATTERN)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bytes: int = Field(gt=0, le=IMAGE_BYTES)
    width: int = Field(gt=0, le=MAX_DIMENSION)
    height: int = Field(gt=0, le=MAX_DIMENSION)

    @model_validator(mode="after")
    def content_address(self) -> ImageAsset:
        _dimensions(self.width, self.height)
        if self.id != "image_" + self.sha256[:24]:
            raise ValueError("image ID/hash mismatch")
        return self

    def public(self, task_id: str) -> dict[str, Any]:
        return {"id": self.id, "name": f"贴纸 {self.sha256[:8]}.png", "bytes": self.bytes,
                "width": self.width, "height": self.height, "url": f"/api/tasks/{task_id}/studio/sources/{self.id}"}


class LutAsset(_AssetModel):
    id: Annotated[str, Field(pattern=LUT_ID_PATTERN)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bytes: int = Field(gt=0, le=LUT_BYTES)
    size: int = Field(ge=2, le=MAX_LUT_SIZE)

    @model_validator(mode="after")
    def content_address(self) -> LutAsset:
        if self.id != "lut_" + self.sha256[:24]:
            raise ValueError("LUT ID/hash mismatch")
        return self

    def public(self, _task_id: str) -> dict[str, Any]:
        return {"id": self.id, "name": f"LUT {self.sha256[:8]}", "bytes": self.bytes, "size": self.size}


class AssetIndex(_AssetModel):
    schema_version: Literal[1] = 1
    images: list[ImageAsset] = Field(default_factory=list, max_length=MAX_ASSETS)
    luts: list[LutAsset] = Field(default_factory=list, max_length=MAX_ASSETS)

    @model_validator(mode="after")
    def unique(self) -> AssetIndex:
        ids = [row.id for row in [*self.images, *self.luts]]
        if type(self.schema_version) is not int or len(ids) > MAX_ASSETS or len(set(ids)) != len(ids):
            raise ValueError("asset index count, schema or uniqueness violation")
        return self

    def public(self, task_id: str) -> dict[str, Any]:
        return {"images": [row.public(task_id) for row in self.images],
                "luts": [row.public(task_id) for row in self.luts], "limits": dict(LIMITS)}


def asset_path(root: Path, asset_id: str) -> Path:
    suffix = ".png" if is_image_id(asset_id) else ".cube" if is_lut_id(asset_id) else None
    if suffix is None:
        raise AssetError("invalid asset ID")
    return contained(root, root / "studio/assets" / (asset_id + suffix), exists=False)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AssetError("duplicate asset index key")
        result[key] = value
    return result


def load_index(root: Path) -> AssetIndex:
    path = contained(root, root / "studio/assets/index.json", exists=False)
    if not path.exists():
        return AssetIndex()
    try:
        payload = json.loads(bounded_read(root, path, MAX_JSON), object_pairs_hook=_object)
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "images", "luts"} or type(payload["schema_version"]) is not int:
            raise AssetError("invalid asset index schema")
        index = AssetIndex.model_validate(payload)
        for row in [*index.images, *index.luts]:
            data = bounded_read(root, asset_path(root, row.id), IMAGE_BYTES if isinstance(row, ImageAsset) else LUT_BYTES)
            if len(data) != row.bytes or hashlib.sha256(data).hexdigest() != row.sha256:
                raise AssetError("asset content hash mismatch")
            if isinstance(row, ImageAsset):
                _, width, height = _png_document(data, canonical=True)
                if (width, height) != (row.width, row.height):
                    raise AssetError("asset dimensions mismatch")
            else:
                canonical, size = canonical_cube(data)
                if canonical != data or size != row.size:
                    raise AssetError("asset LUT is not canonical")
        return index
    except (ValueError, TypeError, OSError, RecursionError) as exc:
        raise HTTPException(422, "invalid or changed studio asset index/content") from exc


def image_paths(root: Path, index: AssetIndex) -> dict[str, Path]:
    return {row.id: asset_path(root, row.id) for row in index.images}


def lut_paths(root: Path, index: AssetIndex) -> dict[str, Path]:
    return {row.id: asset_path(root, row.id) for row in index.luts}


def verified_asset(path: Path, asset_id: str, kind: Literal["image", "lut"],
                   cache: dict[Path, AssetIndex] | None = None) -> ImageAsset | LutAsset:
    """Defense in depth for a renderer's server-owned mapping (never a path API)."""
    suffix = ".png" if kind == "image" else ".cube"
    if not (is_image_id(asset_id) if kind == "image" else is_lut_id(asset_id)) or path.name != asset_id + suffix:
        raise AssetError("invalid asset mapping")
    if path.parent.name != "assets" or path.parent.parent.name != "studio":
        raise AssetError("asset is not in the authorized catalog")
    root = path.parent.parent.parent
    try:
        safe = contained(root, path)
        index = cache.get(root) if cache is not None else None
        if index is None:
            index = load_index(root)
            if cache is not None:
                cache[root] = index
    except HTTPException as exc:
        raise AssetError("asset is unavailable or changed") from exc
    rows = index.images if kind == "image" else index.luts
    row = next((row for row in rows if row.id == asset_id), None)
    if row is None or safe != asset_path(root, asset_id):
        raise AssetError("asset is not in the authorized catalog")
    return row


def studio_bytes(root: Path) -> int:
    """All Studio files, including temp/log/history/output; never follow links."""
    start = contained(root, root / "studio", exists=False)
    if not start.exists():
        return 0
    total, entries = 0, 0
    pending = [start]
    while pending:
        folder = contained(root, pending.pop())
        with os.scandir(folder) as children:
            for child in children:
                entries += 1
                if entries > 10000:
                    raise HTTPException(507, "studio storage tree exceeds its safe entry limit")
                path = contained(root, Path(child.path))
                info = path.stat()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                else:
                    raise HTTPException(422, "unsupported studio storage entry")
    return total


async def drain(task: asyncio.Task[T]) -> T:
    """Drain shielded cleanup through repeated cancellation; preserve cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def _command(command: list[str], work: Path, label: str, *, capture: bool = False) -> bytes:
    # Do not cancel a worker while create_subprocess_exec is returning its PID.
    # A cancelled request holds its busy/disk leases until this ONE bounded
    # command finishes (or its own timeout terminates/reaps it). No later stage
    # or publication starts. Repeated caller cancellation cannot orphan it.
    async def bounded() -> bytes:
        return await asyncio.wait_for(run_logged_command(command, work, label, capture_stdout=capture), 30)

    worker = asyncio.create_task(bounded())
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await drain(worker)
        except (Exception, asyncio.CancelledError):
            pass
        raise
    except Exception as exc:
        raise AssetError("image decoder rejected the document or exceeded its time limit") from exc


def _write_new(root: Path, path: Path, data: bytes) -> None:
    safe = contained(root, path, exists=False)
    with safe.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


@dataclass(frozen=True)
class PreparedAsset:
    row: ImageAsset | LutAsset
    path: Path


async def prepare_image(root: Path, raw: Path, work: Path, content_type: str,
                        check: Callable[[], None]) -> PreparedAsset:
    document, width, height = sanitize_image_document(bounded_read(root, raw, IMAGE_BYTES), content_type)
    codec, extension = ("png", ".png") if content_type == "image/png" else ("mjpeg", ".jpg")
    safe_input = contained(root, work / ("decoded-input" + extension), exists=False)
    _write_new(root, safe_input, document)
    input_options = ["-threads", "1", "-protocol_whitelist", "file", "-format_whitelist", "image2",
                     "-f", "image2", "-pattern_type", "none"]
    check()
    result = await _command([
        "ffprobe", "-v", "fatal", "-max_alloc", str(128*1024*1024), "-probesize", str(IMAGE_BYTES),
        *input_options, "-codec_whitelist", codec, "-i", str(safe_input), "-count_packets",
        "-show_entries", "stream=codec_type,codec_name,width,height,nb_read_packets", "-of", "json",
    ], work, "studio image inspection", capture=True)
    check()
    try:
        if len(result) > MAX_JSON:
            raise ValueError("probe bound")
        streams = json.loads(result)["streams"]
        stream = streams[0]
        if (len(streams) != 1 or stream.get("codec_type") != "video" or stream.get("codec_name") != codec
                or (stream.get("width"), stream.get("height")) != (width, height) or str(stream.get("nb_read_packets")) != "1"):
            raise ValueError("not one bounded image")
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise AssetError("image probe did not confirm one bounded PNG/JPEG frame") from exc
    output = contained(root, work / "encoded.png", exists=False)
    await _command([
        "ffmpeg", "-y", "-nostdin", "-v", "fatal", "-xerror", "-max_alloc", str(128*1024*1024),
        "-filter_threads", "1", "-filter_complex_threads", "1",
        *input_options, "-noautorotate", "-err_detect", "explode", "-c:v", codec, "-i", str(safe_input),
        "-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
        "-frames:v", "1", "-vf", "format=rgba", "-c:v", "png", "-threads", "1", "-flags:v", "+bitexact",
        "-compression_level", "9", "-f", "image2", "-update", "1", "-fs", str(IMAGE_BYTES+1), str(output),
    ], work, "studio image canonicalization")
    check()
    if not output.is_file() or not 0 < output.stat().st_size <= IMAGE_BYTES:
        raise AssetError("canonical PNG exceeds 8 MiB or is empty", 413)
    canonical, out_width, out_height = _png_document(bounded_read(root, output, IMAGE_BYTES), deflate=True)
    _png_document(canonical, canonical=True)
    if (out_width, out_height) != (width, height):
        raise AssetError("canonical image dimensions changed")
    final = contained(root, work / "canonical.png", exists=False)
    _write_new(root, final, canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    return PreparedAsset(ImageAsset(id="image_"+digest[:24], sha256=digest, bytes=len(canonical), width=width, height=height), final)


def prepare_lut(root: Path, raw: Path, work: Path) -> PreparedAsset:
    canonical, size = canonical_cube(bounded_read(root, raw, LUT_BYTES))
    path = contained(root, work / "canonical.cube", exists=False)
    _write_new(root, path, canonical)
    digest = hashlib.sha256(canonical).hexdigest()
    return PreparedAsset(LutAsset(id="lut_"+digest[:24], sha256=digest, bytes=len(canonical), size=size), path)


def _write_index(root: Path, index: AssetIndex) -> None:
    payload = json.dumps(index.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_JSON:
        raise AssetError("asset index exceeds 256 KiB", 413)
    path = contained(root, root / "studio/assets/index.json", exists=False)
    temporary = contained(root, path.with_name("index-" + uuid4().hex + ".tmp"), exists=False)
    try:
        _write_new(root, temporary, payload)
        contained(root, path, exists=False)
        temporary.replace(path)
    except BaseException:
        try:
            contained(root, temporary, exists=False).unlink(missing_ok=True)
        except (OSError, HTTPException):
            pass
        raise
    # No fallible work after replace: a cleanup error must never tell publish()
    # to delete an asset whose reference has ALREADY committed successfully.


def publish(root: Path, prepared: PreparedAsset, original: AssetIndex,
            check: Callable[[], None]) -> tuple[ImageAsset | LutAsset, bool]:
    """Synchronous commit section: no await between revalidation and index swap."""
    check()
    current = load_index(root)
    if current != original:
        raise HTTPException(409, "asset collection changed during import")
    row = prepared.row
    data = bounded_read(root, prepared.path, IMAGE_BYTES if isinstance(row, ImageAsset) else LUT_BYTES)
    if len(data) != row.bytes or hashlib.sha256(data).hexdigest() != row.sha256:
        raise AssetError("prepared asset changed before publication")
    old = next((item for item in [*current.images, *current.luts] if item.id == row.id), None)
    if old is not None:
        if old != row:
            raise AssetError("asset content-address collision")
        check()  # Deduplication is NOT an authorization/revision bypass.
        return old, True
    if len(current.images) + len(current.luts) >= MAX_ASSETS:
        raise HTTPException(429, "studio permits at most 64 total image and LUT assets")
    updated = AssetIndex(images=[*current.images, row] if isinstance(row, ImageAsset) else list(current.images),
                         luts=[*current.luts, row] if isinstance(row, LutAsset) else list(current.luts))
    destination = asset_path(root, row.id)
    created = not destination.exists()
    if not created and bounded_read(root, destination, max(IMAGE_BYTES, LUT_BYTES)) != data:
        raise AssetError("unindexed asset conflicts with imported content")
    try:
        check()
        if created:
            contained(root, prepared.path).replace(destination)
        _write_index(root, updated)  # Last commit point; prior catalog stays intact on failure.
    except BaseException:
        if created:
            contained(root, destination, exists=False).unlink(missing_ok=True)
        raise
    return row, False


def cleanup(root: Path, work: Path, root_identity: tuple[int, int] | None = None) -> None:
    # Never recreate a deleted task or traverse a substituted root/junction.
    if not root.exists():
        return
    try:
        safe = contained(root, work, exists=False)
        info = root.stat()
        if root_identity is not None and (info.st_dev, info.st_ino) != root_identity:
            return
        if safe.exists():
            shutil.rmtree(safe)
    except (OSError, HTTPException):
        # A filesystem failure is not permission to remove a different path.
        # An unindexed leftover counts against studio_bytes on the next request.
        return