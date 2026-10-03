"""Private Studio assets: parser, transaction, model and REAL codec regressions.

Authoring these tests does not run commands. The main runner executes them.
API tests use an unmounted-router app, ASGITransport, fresh TEMP and explicit
in-memory settings/authorization; never import main, dotenv, production data or
provider clients. Codec doubles below are labelled contract-only; actual pixel
and alpha assertions live in the FFmpeg-gated media class, not those doubles.
"""
from __future__ import annotations

import asyncio
import binascii
import copy
import hashlib
import json
import os
import struct
import tempfile
import unittest
import zlib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend import studio_assets as assets
from backend.studio import (
    Action, _BUSY, _DISK_RESERVATIONS, _INGESTING, _RUNNING, apply_action,
    capabilities, catalog, create_studio_router, read_state, studio_task_busy,
    validate_references, write_state,
)
from backend.studio_render import (
    MAX_DURATION, Clip, ExportOptions, Project, RenderError, Track, color_filters,
    filter_path, input_args, probe, render_project,
)
from tests import test_studio as existing
from tests.studio_publication_fixtures import (
    QC_BLOCKER, confirm_synthetic_publication, synthetic_publication_files,
)


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)


def png(width: int = 16, height: int = 16, rgba: tuple[int, int, int, int] = (220, 60, 20, 128),
        *, metadata: bytes = b"", pixels: bytes | None = None) -> bytes:
    """Independent lossless fixture writer; not an image library/decoder oracle."""
    if pixels is None:
        pixels = bytes(rgba) * width * height
    scan = b"".join(b"\0" + pixels[y*width*4:(y+1)*width*4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + metadata + chunk(b"IDAT", zlib.compress(scan)) + chunk(b"IEND", b""))


def cube(size: int = 2, *, swap: bool = False, red: float = 1.0) -> bytes:
    rows = []
    # .cube order is red-fast. Swapping RED and BLUE catches channel/order bugs.
    for b in range(size):
        for g in range(size):
            for r in range(size):
                rgb = (b/(size-1), g/(size-1), r/(size-1)) if swap else (r/(size-1)*red, g/(size-1), b/(size-1))
                rows.append(" ".join(format(value, ".9g") for value in rgb))
    return (f"LUT_3D_SIZE {size}\n" + "\n".join(rows) + "\n").encode("ascii")


def image_project(source: str, *, lut_id: str | None = None, **fields: Any) -> Project:
    values = {"id": "image", "source_id": source, "duration": 1, "mute": True, "fit": "cover", "lut_id": lut_id, **fields}
    return Project(tracks=[Track(id="v", type="video", clips=[Clip.model_validate(values)])])


def install_canonical(root: Path, document: bytes, kind: str) -> assets.ImageAsset | assets.LutAsset:
    """Index-test fixture only. Real import/render tests go through HTTP import."""
    if kind == "image":
        data, width, height = assets._png_document(document, canonical=True, deflate=True)
        digest = hashlib.sha256(data).hexdigest()
        row = assets.ImageAsset(id="image_" + digest[:24], sha256=digest, bytes=len(data), width=width, height=height)
    else:
        data, size = assets.canonical_cube(document)
        digest = hashlib.sha256(data).hexdigest()
        row = assets.LutAsset(id="lut_" + digest[:24], sha256=digest, bytes=len(data), size=size)
    before = assets.load_index(root)
    path = assets.asset_path(root, row.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    index = assets.AssetIndex(images=[*before.images, row] if isinstance(row, assets.ImageAsset) else before.images,
                              luts=[*before.luts, row] if isinstance(row, assets.LutAsset) else before.luts)
    assets._write_index(root, index)
    return row


class StudioAssetParserTests(unittest.TestCase):
    def test_cube_canonical_titles_comments_bom_crlf_and_red_fast_order(self):
        expected, size = assets.canonical_cube(cube())
        decorated = (b'\xef\xbb\xbf# comment\r\nTITLE "ignored title"\r\nDOMAIN_MAX 1 1.0 1e0\r\n'
                     b'DOMAIN_MIN 0 0.0 -0\r\n' + cube().replace(b"\n", b"\r\n"))
        self.assertEqual(assets.canonical_cube(decorated), (expected, size))
        self.assertEqual(size, 2)
        self.assertEqual(expected.splitlines()[3:7], [b"0 0 0", b"1 0 0", b"0 1 0", b"1 1 0"])
        self.assertNotIn(b"TITLE", expected)
        self.assertEqual(assets.canonical_cube(expected)[0], expected)

    def test_cube_all_size_bounds_and_total_rows(self):
        for size in (2, 3, 17, 33):
            with self.subTest(size=size):
                data, actual = assets.canonical_cube(cube(size))
                self.assertEqual(actual, size)
                self.assertEqual(len(data.splitlines()) - 3, size**3)
                self.assertLessEqual(len(data), assets.LUT_BYTES)
        for size in (0, 1, 34, 65, -2):
            with self.subTest(size=size), self.assertRaises(assets.AssetError):
                assets.canonical_cube(f"LUT_3D_SIZE {size}\n".encode())

    def test_cube_canonical_decimal_rounding_is_stable_without_float_underflow(self):
        document = cube().replace(b"0 0 0", b"0.1234567895 0.9999999995 1e-320", 1)
        canonical, _ = assets.canonical_cube(document)
        self.assertIn(b"0.12345679 1 1e-320\n", canonical)
        self.assertEqual(assets.canonical_cube(canonical)[0], canonical)
        with self.assertRaises(assets.AssetError):
            assets.canonical_cube(cube().replace(b"0 0 0", b"0.0001e-999 0 0", 1))

    def test_cube_rejects_commands_paths_nonfinite_domains_precision_and_extra_rows(self):
        valid = cube()
        cases = [b"LUT_1D_SIZE 2\n0 0 0\n1 1 1", b"INCLUDE secret.cube\n" + valid,
                 b"movie=/secret\n" + valid, b"http://localhost/private\n" + valid,
                 b"DOMAIN_MIN 0.1 0 0\n" + valid, b"DOMAIN_MAX 2 1 1\n" + valid,
                 b"DOMAIN_MIN 1e-999 0 0\n" + valid, b"DOMAIN_MAX 0.9999999999999999999999999 1 1\n" + valid,
                 b"DOMAIN_MIN 0 0 0\nDOMAIN_MIN 0 0 0\n" + valid,
                 b"LUT_3D_SIZE 2.0\n" + valid, b"LUT_3D_SIZE 02\n" + valid,
                 valid + b"0 0 0\n", valid.rsplit(b"\n", 2)[0], valid + b"TITLE \"late\"\n",
                 b'TITLE "first"\nTITLE "second"\n' + valid, b'TITLE "bad\\name"\n' + valid,
                 b"\x00" + valid, b"\xff" + valid, b"#" + b"x"*513 + b"\n" + valid]
        for number in (b"NaN", b"Infinity", b"-inf", b"1.01", b"-0.01", b"-1e-999", b"1_0", b"0x1", b"1e9999"):
            cases.append(valid.replace(b"0 0 0", number + b" 0 0", 1))
        for document in cases:
            with self.subTest(document=document[:75]), self.assertRaises(assets.AssetError):
                assets.canonical_cube(document)
        with self.assertRaises(assets.AssetError):
            assets.canonical_cube(b" " * (assets.LUT_BYTES+1))

    def test_png_strips_metadata_without_decoding_or_losing_alpha_payload(self):
        document = png(metadata=chunk(b"tEXt", b"private\0not public") + chunk(b"eXIf", b"private-exif")
                       + chunk(b"iCCP", b"profile\0\0" + zlib.compress(b"secret")))
        canonical, width, height = assets.sanitize_image_document(document, "image/png")
        self.assertEqual((width, height), (16, 16))
        self.assertEqual(canonical, png())
        self.assertNotIn(b"private", canonical)
        self.assertNotIn(b"profile", canonical)
        with self.assertRaises(assets.AssetError):
            assets.sanitize_image_document(document, "image/jpeg")

    def test_png_header_budget_crc_animation_truncation_polyglot_and_deflate_bomb(self):
        good = png()
        documents = [b"<svg>no</svg>", b"GIF89a", b"#EXTM3U\nhttp://localhost", good[:-1], good+b"secret",
                     good+good, png(metadata=chunk(b"acTL", struct.pack(">II", 2, 0))),
                     png(metadata=chunk(b"fcTL", b"\0"*26)), png(metadata=chunk(b"fdAT", b"\0"*4)),
                     png(metadata=chunk(b"ABCD", b"unknown critical")), good[:29]+b"\0\0\0\0"+good[33:]]
        for width, height in ((4097, 1), (1, 4097), (4096, 2161), (4096, 4096), (0, 1)):
            documents.append(good[:8] + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + good[33:])
        documents.append(good[:33] + chunk(b"IDAT", zlib.compress(b"\0" * 100000)) + chunk(b"IEND", b""))
        documents.append(good[:33] + chunk(b"IDAT", zlib.compress(b"\0" * (16*(1+16*4))) + b"tail") + chunk(b"IEND", b""))
        for document in documents:
            with self.subTest(document=document[:45]), self.assertRaises(assets.AssetError):
                assets.sanitize_image_document(document, "image/png")
        with self.assertRaises(assets.AssetError):
            assets.sanitize_image_document(b"x"*(assets.IMAGE_BYTES+1), "image/png")

    def test_dimension_boundary_accepted_before_any_pixel_decode(self):
        assets._dimensions(4096, 2160)
        assets._dimensions(2160, 4096)
        for dimensions in ((4096, 2161), (4097, 1), (1, 0)):
            with self.assertRaises(assets.AssetError):
                assets._dimensions(*dimensions)

    def test_jpeg_marker_scan_strip_and_animation_rejection(self):
        def segment(marker: int, payload: bytes) -> bytes:
            return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload)+2) + payload
        sof = segment(0xC0, struct.pack(">BHHB", 8, 2, 2, 3) + b"\x01\x11\0\x02\x11\0\x03\x11\0")
        sos = segment(0xDA, b"\x03\x01\0\x02\0\x03\0\0\x3f\0")
        # Structural header test, not a decodable JPEG or codec-success double.
        plain = b"\xff\xd8" + sof + sos + b"\x11\xff\0\x22" + b"\xff\xd9"
        decorated = b"\xff\xd8" + segment(0xE1, b"Exif\0private") + segment(0xFE, b"private comment") + plain[2:]
        self.assertEqual(assets.sanitize_image_document(decorated, "image/jpeg"), (plain, 2, 2))
        adobe = segment(0xEE, b"Adobe\x00\x64\xfe\xed\xab\xcd\x00")
        normalized = assets.sanitize_image_document(plain[:2]+adobe+plain[2:], "image/jpeg")[0]
        self.assertIn(segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x00"), normalized)
        self.assertNotIn(b"\xfe\xed\xab\xcd", normalized)
        for invalid in (plain+plain, plain+b"tail", plain[:-2], b"\xff\xd8"+segment(0xE2, b"MPF\0data")+plain[2:]):
            with self.assertRaises(assets.AssetError):
                assets.sanitize_image_document(invalid, "image/jpeg")


class StudioAssetModelTests(unittest.TestCase):
    def test_lut_defaults_strict_ids_and_applicable_tracks(self):
        lut_id = "lut_" + "a"*24
        self.assertIsNone(Clip(id="c", duration=1).lut_id)
        for value in ("lut_"+"A"*24, "lut_"+"a"*23, "../x.cube", "/tmp/x.cube", "http://host", 1, True, [], {}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Clip(id="c", duration=1, lut_id=value)
        for kind in ("video", "overlay", "adjustment"):
            clip = Clip(id="c", duration=1, lut_id=lut_id, source_id=None if kind == "adjustment" else "final")
            self.assertEqual(Track(id="t", type=kind, clips=[clip]).clips[0].lut_id, lut_id)
        for kind in ("text", "audio"):
            with self.assertRaises(ValidationError):
                Track(id="t", type=kind, clips=[Clip(id="c", duration=1, lut_id=lut_id, text="text", source_id="final")])

    def test_still_controls_defaults_clip_duration_and_no_audio_source(self):
        source = "image_" + "b"*24
        self.assertEqual(image_project(source, duration=120).duration, 120)
        for fields in ({"mute": False}, {"trim": 0.01}, {"speed": 0.5}, {"reverse": True}, {"freeze": True},
                       {"volume": 0}, {"pan": 0.5}, {"audio_effect": "normalize"}, {"bass_db": 1}, {"treble_db": 1}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                image_project(source, **fields)
        with self.assertRaises(ValidationError):
            Track(id="a", type="audio", clips=[Clip(id="c", source_id=source, duration=1, mute=True)])

    def test_image_edits_change_hold_not_fictional_eof(self):
        project = image_project("image_" + "b"*24, duration=2)
        split = apply_action(project, Action(expected_revision=0, op="split", clip_id="image", at=1, new_id="right"))
        self.assertEqual([c.trim for c in split.tracks[0].clips], [0, 0])
        self.assertEqual([c.duration for c in split.tracks[0].clips], [1, 1])
        rolled = apply_action(split, Action(expected_revision=0, op="roll", clip_id="image", at=0.5))
        self.assertEqual([c.trim for c in rolled.tracks[0].clips], [0, 0])
        self.assertEqual([c.duration for c in rolled.tracks[0].clips], [0.5, 1.5])
        with self.assertRaises(HTTPException):
            apply_action(project, Action(expected_revision=0, op="slip", clip_id="image", trim=0))
        self.assertEqual(project.tracks[0].clips[0].duration, 2)

    def test_reference_and_filter_contract_fail_closed(self):
        lut_id = "lut_" + "a"*24
        project = Project(tracks=[Track(id="a", type="adjustment", clips=[Clip(id="c", duration=1, lut_id=lut_id)])])
        with self.assertRaises(HTTPException):
            validate_references(project, {})
        with self.assertRaises(RenderError):
            color_filters(project.tracks[0].clips[0])
        path = Path(tempfile.gettempdir()) / "space ' comma, [bracket]" / (lut_id+".cube")
        filters = color_filters(project.tracks[0].clips[0], path)
        self.assertIn(f"file='{filter_path(path)}'", filters[-1])
        self.assertTrue(filters[-1].endswith(":interp=tetrahedral"))
        self.assertTrue(filters[0].startswith("eq="))
        neutral = Clip(id="c", duration=1)
        self.assertEqual(color_filters(neutral), [f"eq=brightness={neutral.brightness}:contrast={neutral.contrast}:saturation={neutral.saturation}"])
        self.assertIn("image2", input_args(Path("image.png")))
        args = input_args(Path("image.png"))
        self.assertEqual(args[args.index("-pattern_type")+1], "none")
        self.assertEqual(args[args.index("-protocol_whitelist")+1], "file")

    def test_capabilities_are_honest_partial_imports_not_builtin_or_tracking(self):
        result = capabilities()
        tools = {item["id"]: item for item in result["tools"]}
        self.assertEqual(result["tool_count"], 113)
        for key in ("stickerCustom", "stickerLib", "stickerAnim", "lut"):
            self.assertEqual(tools[key]["classification"], "partial")
            self.assertTrue(tools[key]["available"])
        self.assertIn("tetrahedral", tools["lut"]["reason"])
        self.assertIn("no object/face/motion tracking", tools["stickerAnim"]["reason"])
        self.assertNotIn("image ingestion", result["unsupported"])


class StudioAssetIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "task"
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_strict_schema_no_user_paths_names_coercions_or_duplicate_ids(self):
        row = install_canonical(self.root, cube(), "lut")
        good = assets.load_index(self.root).model_dump()
        index_path = self.root / "studio/assets/index.json"
        cases = []
        for key, value in (("path", "../../secret"), ("name", "user.cube"), ("bytes", str(row.bytes)), ("size", True), ("id", "lut_"+"f"*24), ("sha256", "0"*64)):
            changed = copy.deepcopy(good)
            changed["luts"][0][key] = value
            cases.append(changed)
        for version in (True, "1", 1.0, 2):
            cases.append({**good, "schema_version": version})
        cases.extend([{}, {**good, "unknown": 1}, {**good, "luts": good["luts"]*2}, {**good, "images": None}])
        for changed in cases:
            index_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.subTest(changed=changed), self.assertRaises(HTTPException):
                assets.load_index(self.root)
        index_path.write_text('{"schema_version":1,"images":[],"luts":[],"luts":[]}', encoding="utf-8")
        with self.assertRaises(HTTPException):
            assets.load_index(self.root)
        index_path.write_bytes(b" "*(assets.MAX_JSON+1))
        with self.assertRaises(HTTPException):
            assets.load_index(self.root)

    def test_tamper_with_same_length_and_restored_mtime_fails_content_hash(self):
        row = install_canonical(self.root, cube(), "lut")
        path = assets.asset_path(self.root, row.id)
        before = path.stat()
        document = path.read_bytes()
        path.write_bytes(document.replace(b"0 0 0\n", b"1 0 0\n", 1))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual((path.stat().st_size, path.stat().st_mtime_ns), (before.st_size, before.st_mtime_ns))
        with self.assertRaises(HTTPException):
            assets.load_index(self.root)

    def test_orphans_not_catalog_and_lut_not_media(self):
        image = install_canonical(self.root, png(), "image")
        lut = install_canonical(self.root, cube(), "lut")
        orphan = self.root / "studio/assets" / ("image_"+"f"*24+".png")
        orphan.write_bytes(png())
        raw_image = self.root / "raw.png"
        raw_image.write_bytes(png(metadata=chunk(b"tEXt", b"private\0raw metadata")))
        record = SimpleNamespace(task_dir=self.root, uploads=[SimpleNamespace(path=raw_image)])
        sources = catalog(record)
        self.assertEqual(set(sources), {image.id})
        self.assertNotIn(lut.id, sources)
        self.assertGreater(assets.studio_bytes(self.root), image.bytes+lut.bytes)

    def test_lexical_traversal_and_dangling_component_links_rejected(self):
        for path in (self.root / ".." / "outside", self.root, self.root.parent / "outside"):
            with self.assertRaises(HTTPException):
                assets.contained(self.root, path, exists=False)
        target = self.root / "target"
        target.mkdir()
        link = self.root / "studio"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink creation privilege is unavailable")
        with self.assertRaises(HTTPException):
            assets.contained(self.root, link / "assets/new.png", exists=False)
        link.unlink()
        link.symlink_to(self.root / "missing", target_is_directory=True)
        with self.assertRaises(HTTPException):
            assets.load_index(self.root)

    def test_windows_junction_tag_checked_without_resolving_target(self):
        original = Path.lstat
        junction = self.root / "studio"

        def lstat(path: Path, *args: Any, **kwargs: Any):
            if path == junction:
                return SimpleNamespace(st_mode=0o40755, st_reparse_tag=0xA0000003)
            return original(path, *args, **kwargs)

        with patch.object(Path, "lstat", new=lstat), self.assertRaises(HTTPException):
            assets.contained(self.root, junction / "assets/index.json", exists=False)


class AssetAPIHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.root = self.data / "task"
        self.root.mkdir()
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, uploads=[], status="done", revision=0)
        self.records = {"task": self.record}
        self.manager = SimpleNamespace(_draining=False, get=self.records.get)
        self.settings = SimpleNamespace(data_dir=self.data, media_command_timeout_seconds=60, minimum_free_disk_bytes=0)
        self.calls: list[bool] = []
        self.denied = False
        self.on_authorize: Any = None
        self.originals = {"final.mp4": b"metadata only; never decoded in route tests",
                          **synthetic_publication_files(), "script.txt": b"private original"}
        for name, data in self.originals.items():
            (self.root / name).write_bytes(data)
        confirm_synthetic_publication(self, self.record)

        async def authorize(request: Any, task_id: str, write: bool = False):
            self.calls.append(write)
            if task_id != "task" or self.denied or request.headers.get("X-Token") != "test-only":
                raise HTTPException(403, "denied")
            if write and request.headers.get("X-CSRF") != "test-only":
                raise HTTPException(403, "CSRF")
            if self.on_authorize is not None:
                self.on_authorize()
            return self.record

        self.authorize = authorize
        self.app = FastAPI()
        self.app.include_router(create_studio_router(self.settings, self.manager, self.authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                       base_url="http://testserver", headers={"X-Token": "test-only", "X-CSRF": "test-only"})
        self.prefix = "/api/tasks/task/studio"

    async def asyncTearDown(self):
        owned = [task for key, task in _RUNNING.items() if Path(key).is_relative_to(self.root)]
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await self.client.aclose()
        self.assertFalse(any(Path(key).is_relative_to(self.root) for key in _BUSY))
        self.assertFalse(any(Path(key).is_relative_to(self.root) for key in _INGESTING))
        self.assertFalse(any(Path(key).is_relative_to(self.root) for key in _DISK_RESERVATIONS))
        self.assertFalse(list(self.root.rglob(".ingest-*")))
        for name, data in self.originals.items():
            self.assertEqual((self.root / name).read_bytes(), data)
        self.temp.cleanup()

    async def upload(self, kind: str, data: Any, *, revision: int = 0, **headers: str) -> httpx.Response:
        return await self.client.post(f"{self.prefix}/assets/{kind}?expected_revision={revision}", content=data,
                                      headers={"Content-Type": "image/png" if kind == "image" else "text/plain", **headers})

    async def save(self, project: Project, revision: int = 0) -> httpx.Response:
        return await self.client.post(self.prefix+"/project", json={"expected_revision": revision, "project": project.model_dump()})

    def assert_no_assets(self):
        self.assertEqual(assets.load_index(self.root), assets.AssetIndex())
        self.assertFalse(list(self.root.glob("studio/assets/image_*.png")))
        self.assertFalse(list(self.root.glob("studio/assets/lut_*.cube")))
        self.assertFalse(list(self.root.glob("studio/assets/.ingest-*")))


class StudioAssetRouteTests(AssetAPIHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # Contract tests must reach parser/command doubles even on a machine
        # without codecs. No genuine media-success assertion uses this patch.
        self.tools = patch("backend.studio.shutil.which", return_value="contract-only-not-an-executable")
        self.tools.start()
        self.addCleanup(self.tools.stop)

    async def test_exact_get_import_dedup_contract_no_revision_or_history_mutation(self):
        fetched = await self.client.get(self.prefix+"/assets")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json(), {"images": [], "luts": [], "limits": {
            "image_bytes": 8388608, "lut_bytes": 2097152, "assets": 64, "dimension": 4096, "pixels": 8847360, "lut_size": 33}})
        self.assertEqual((await self.save(Project())).status_code, 200)
        before = (self.root / "studio/state.json").read_bytes()
        first = await self.upload("lut", cube(), revision=1)
        self.assertEqual(first.status_code, 200, first.text)
        public = first.json()
        self.assertEqual(set(public), {"asset", "project_revision", "deduplicated"})
        self.assertEqual(set(public["asset"]), {"id", "name", "bytes", "size"})
        self.assertRegex(public["asset"]["id"], r"^lut_[a-f0-9]{24}$")
        self.assertRegex(public["asset"]["name"], r"^LUT [a-f0-9]{8}$")
        self.assertFalse(public["deduplicated"])
        self.assertEqual(public["project_revision"], 1)
        index_bytes = (self.root / "studio/assets/index.json").read_bytes()
        again = await self.upload("lut", b'TITLE "different"\n# comment\n'+cube(), revision=1)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json()["deduplicated"])
        self.assertEqual(again.json()["asset"], public["asset"])
        self.assertEqual((self.root / "studio/assets/index.json").read_bytes(), index_bytes)
        self.assertEqual((self.root / "studio/state.json").read_bytes(), before)
        self.assertEqual((await self.upload("lut", cube(), revision=0)).status_code, 409)
        self.assertEqual((await self.client.get(self.prefix+"/sources/"+public["asset"]["id"])).status_code, 404)
        lut_as_media = Project(tracks=[Track(id="v", type="video", clips=[Clip(id="c", source_id=public["asset"]["id"], duration=1)])])
        self.assertEqual((await self.save(lut_as_media, 1)).status_code, 422)
        self.assertEqual((self.root / "studio/state.json").read_bytes(), before)
        self.assertNotIn(str(self.root), first.text)
        self.assertNotIn("sha256", first.text)
        self.assertEqual(first.headers["cache-control"], "no-store")

    async def test_every_route_private_and_write_csrf_delegate_before_body(self):
        for method, route in (("GET", "/assets"), ("POST", "/assets/image?expected_revision=0"), ("POST", "/assets/lut?expected_revision=0")):
            response = await self.client.request(method, self.prefix+route, headers={"X-Token": "bad"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.calls[-1], method == "POST")
        for kind in ("image", "lut"):
            called = False
            async def body():
                nonlocal called
                called = True
                yield cube()
            response = await self.upload(kind, body(), **{"X-CSRF": "bad"})
            self.assertEqual(response.status_code, 403)
            self.assertFalse(called)
        self.assert_no_assets()

    async def test_mandatory_strict_revision_mime_no_filename_header_no_multipart(self):
        for query in ("", "?expected_revision=-1", "?expected_revision=+1", "?expected_revision=1.0", "?expected_revision=true",
                      "?expected_revision=01", "?expected_revision=0&expected_revision=0", "?expected_revision=1e0"):
            response = await self.client.post(self.prefix+"/assets/lut"+query, content=cube(), headers={"Content-Type": "text/plain"})
            self.assertEqual(response.status_code, 422, response.text)
        for header, value, status in (("Content-Type", "multipart/form-data; boundary=x", 415), ("Content-Type", "image/svg+xml", 415),
                                      ("Content-Type", "application/octet-stream", 415), ("Content-Encoding", "gzip", 415),
                                      ("X-Asset-Name", "..%2Fsecret.cube", 400)):
            response = await self.upload("lut", cube(), **{header: value})
            self.assertEqual(response.status_code, status, response.text)
        response = await self.upload("lut", cube(), **{"Content-Type": "text/plain; charset=utf-8"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_stream_and_declared_size_limits_clean_before_decoder(self):
        with patch("backend.studio_assets.prepare_image", new=AsyncMock()) as decoder:
            for kind, limit in (("image", assets.IMAGE_BYTES), ("lut", assets.LUT_BYTES)):
                response = await self.upload(kind, b"x", **{"Content-Length": str(limit+1)})
                self.assertEqual(response.status_code, 413)
                async def body():
                    yield b"x"*limit
                    yield b"x"
                response = await self.upload(kind, body())
                self.assertEqual(response.status_code, 413, response.text)
            decoder.assert_not_awaited()
        self.assertEqual((await self.upload("lut", b"")).status_code, 422)
        self.assertEqual((await self.upload("lut", cube(), **{"Content-Length": "1"})).status_code, 400)
        self.assert_no_assets()

    async def test_untrusted_image_rejected_before_any_decoder(self):
        with patch("backend.studio_assets._command", new=AsyncMock()) as command:
            for document in (b"<svg>no</svg>", b"GIF89a", png()[:-4], png(metadata=chunk(b"acTL", b"\0"*8))):
                response = await self.upload("image", document)
                self.assertEqual(response.status_code, 422, response.text)
            command.assert_not_awaited()
        self.assert_no_assets()

    async def test_busy_is_reserved_before_first_stream_chunk_all_mutations_block(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def body():
            self.assertTrue(studio_task_busy(self.root))
            self.assertIn(str(self.root), _DISK_RESERVATIONS)
            entered.set()
            await release.wait()
            yield cube()
        first = asyncio.create_task(self.upload("lut", body()))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            for route, payload in (("/project", {"expected_revision": 0, "project": {}}), ("/render", {"expected_revision": 0}),
                                   ("/actions", {"expected_revision": 0, "op": "marker", "new_id": "m", "at": 0}),
                                   ("/subtitles/import", {"expected_revision": 0, "track_id": "t", "srt": "invalid"})):
                response = await self.client.post(self.prefix+route, json=payload)
                self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual((await self.upload("lut", cube())).status_code, 409)
            # A separate router factory shares the SAME task exclusion set.
            other = FastAPI()
            other.include_router(create_studio_router(self.settings, self.manager, self.authorize))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other), base_url="http://testserver", headers=self.client.headers) as client:
                response = await client.post(self.prefix+"/assets/lut?expected_revision=0", content=cube(), headers={"Content-Type": "text/plain"})
                self.assertEqual(response.status_code, 409)
            self.assertEqual((await self.client.get(self.prefix+"/assets")).json()["luts"], [])
        finally:
            release.set()
            response = await first
        self.assertEqual(response.status_code, 200, response.text)

    async def test_existing_render_legacy_pipeline_and_global_capacity_reject_before_stream(self):
        called = False
        async def body():
            nonlocal called
            called = True
            yield cube()
        _BUSY.add(str(self.root))
        try:
            self.assertEqual((await self.upload("lut", body())).status_code, 409)
        finally:
            _BUSY.discard(str(self.root))
        with patch("backend.studio.legacy_task_busy", return_value=True) as busy:
            self.assertEqual((await self.upload("lut", body())).status_code, 409)
            busy.assert_called_with(self.root)
        self.record.status = "running"
        self.assertEqual((await self.upload("lut", body())).status_code, 409)
        self.record.status = "done"
        _BUSY.update({"fixture-other-1", "fixture-other-2"})
        try:
            self.assertEqual((await self.upload("lut", body())).status_code, 429)
        finally:
            _BUSY.difference_update({"fixture-other-1", "fixture-other-2"})
        self.assertFalse(called)

    async def test_status_record_pipeline_revision_and_project_revision_races_no_commit(self):
        for mutation in ("removed", "replaced", "status", "pipeline", "project", "legacy"):
            with self.subTest(mutation=mutation), patch("backend.studio.legacy_task_busy", return_value=False) as busy:
                async def body():
                    if mutation == "removed":
                        self.records.pop("task")
                    elif mutation == "replaced":
                        self.records["task"] = SimpleNamespace(**vars(self.record))
                    elif mutation == "status":
                        self.record.status = "running"
                    elif mutation == "pipeline":
                        self.record.revision += 1
                    elif mutation == "project":
                        state = read_state(self.root)
                        state["revision"] = 1
                        write_state(self.root, state)
                    else:
                        busy.assert_called_with(self.root)
                        busy.return_value = True
                    yield cube()
                response = await self.upload("lut", body())
                self.assertEqual(response.status_code, 409, response.text)
                if mutation == "legacy":
                    self.assertGreaterEqual(busy.call_count, 2)
                self.assert_no_assets()
                self.records["task"] = self.record
                self.record.status, self.record.revision = "done", 0
                (self.root / "studio/state.json").unlink(missing_ok=True)

    async def test_reauthorization_after_body_and_even_dedup(self):
        self.assertEqual((await self.upload("lut", cube())).status_code, 200)
        before = (self.root / "studio/assets/index.json").read_bytes()
        async def body():
            self.denied = True
            yield cube()
        rejected = await self.upload("lut", body())
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual((self.root / "studio/assets/index.json").read_bytes(), before)
        self.denied = False
        count = 0
        def stale_after_final_authorize():
            nonlocal count
            count += 1
            if count == 3:
                self.record.status = "running"
        self.on_authorize = stale_after_final_authorize
        response = await self.upload("lut", cube())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(count, 3)
        self.assertEqual((self.root / "studio/assets/index.json").read_bytes(), before)
        self.record.status = "done"
        self.on_authorize = None

    async def test_body_cancellation_disconnect_and_cleanup(self):
        entered = asyncio.Event()
        async def body():
            yield b"LUT_3D_SIZE 2\n"
            entered.set()
            await asyncio.Event().wait()
        request = asyncio.create_task(self.upload("lut", body()))
        await asyncio.wait_for(entered.wait(), 5)
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertFalse(studio_task_busy(self.root))
        self.assert_no_assets()
        from starlette.requests import ClientDisconnect
        async def disconnected():
            yield b"partial"
            raise ClientDisconnect()
        response = await self.upload("lut", disconnected())
        self.assertNotEqual(response.status_code, 200)
        self.assert_no_assets()

    async def test_body_timeout_and_missing_codec_are_explicit_without_partial_assets(self):
        async def hanging():
            yield b"LUT_3D_SIZE 2\n"
            await asyncio.Event().wait()
        with patch("backend.studio._ASSET_BODY_TIMEOUT", 0.01):
            response = await self.upload("lut", hanging())
        self.assertEqual(response.status_code, 408, response.text)
        self.assert_no_assets()
        with patch("backend.studio.shutil.which", return_value=None):
            self.assertEqual((await self.upload("image", png())).status_code, 503)

    async def test_cancel_during_image_command_drains_worker_under_repeated_cancellation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(_command: Any, _work: Any, _label: Any, **_kwargs: Any):
            entered.set()
            await release.wait()
            return json.dumps({"streams": [{"codec_type": "video", "codec_name": "png", "width": 16, "height": 16, "nb_read_packets": "1"}]}).encode()
        with patch("backend.studio_assets.run_logged_command", side_effect=blocked) as command:
            request = asyncio.create_task(self.upload("image", png()))
            await asyncio.wait_for(entered.wait(), 5)
            request.cancel()
            await asyncio.sleep(0)
            request.cancel()
            await asyncio.sleep(0)
            self.assertTrue(studio_task_busy(self.root))
            self.assertFalse(request.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await request
            self.assertEqual(command.await_count, 1, "cancelled imports must not advance to encoding")
        self.assert_no_assets()

    async def test_stage_race_and_output_size_contract_only_decoder_doubles(self):
        async def changed(_command: Any, _work: Any, _label: Any, **_kwargs: Any):
            self.record.revision += 1
            return b'{"streams":[]}'
        with patch("backend.studio_assets.run_logged_command", side_effect=changed):
            response = await self.upload("image", png())
        self.assertEqual(response.status_code, 409)
        self.record.revision = 0
        self.assert_no_assets()

        async def oversized(command: list[str], _work: Any, _label: Any, **kwargs: Any):
            self.assertEqual(command[command.index("-f")+1], "image2")
            self.assertEqual(command[command.index("-pattern_type")+1], "none")
            self.assertEqual(command[command.index("-protocol_whitelist")+1], "file")
            if kwargs.get("capture_stdout"):
                return b'{"streams":[{"codec_type":"video","codec_name":"png","width":16,"height":16,"nb_read_packets":"1"}]}'
            self.assertEqual(command[command.index("-map_metadata")+1], "-1")
            with Path(command[-1]).open("wb") as output:
                output.truncate(assets.IMAGE_BYTES+1)
            return b""
        with patch("backend.studio_assets.run_logged_command", side_effect=oversized):
            response = await self.upload("image", png())
        self.assertEqual(response.status_code, 413, response.text)
        self.assert_no_assets()

    async def test_low_disk_during_encoder_wait_keeps_lease_until_worker_drains(self):
        entered, release, checked = asyncio.Event(), asyncio.Event(), asyncio.Event()
        low = False
        async def blocked(_command: Any, _work: Any, _label: Any, **_kwargs: Any):
            entered.set()
            await release.wait()
            return b'{"streams":[]}'
        def disk(_path: Any):
            if low:
                checked.set()
            return SimpleNamespace(free=1 if low else 10**9)
        with patch("backend.studio_assets.run_logged_command", side_effect=blocked), \
                patch("backend.studio.shutil.disk_usage", side_effect=disk), \
                patch("backend.studio._ASSET_STORAGE_POLL_SECONDS", 0.001):
            request = asyncio.create_task(self.upload("image", png()))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                low = True
                await asyncio.wait_for(checked.wait(), 5)
                self.assertTrue(studio_task_busy(self.root))
                self.assertFalse(request.done())
            finally:
                release.set()
                response = await request
        self.assertEqual(response.status_code, 507, response.text)
        self.assert_no_assets()

    async def test_task_quota_free_disk_and_shared_reservation_release(self):
        with patch("backend.studio.MAX_TASK_BYTES", 1):
            self.assertEqual((await self.upload("lut", cube())).status_code, 507)
        with patch("backend.studio.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            self.assertEqual((await self.upload("lut", cube())).status_code, 507)
        with patch("backend.studio.shutil.disk_usage", return_value=SimpleNamespace(free=10**9)) as disk:
            async def body():
                disk.return_value = SimpleNamespace(free=1)
                yield cube()
            self.assertEqual((await self.upload("lut", body())).status_code, 507)
        events: list[str] = []
        @asynccontextmanager
        async def reserve(amount: int):
            self.assertEqual(amount, assets.LUT_WORK_BYTES)
            self.assertTrue(studio_task_busy(self.root))
            events.append("acquire")
            try:
                yield None
            finally:
                events.append("release")
        self.manager._upload_capacity_guard = SimpleNamespace(reserve=reserve)
        self.assertEqual((await self.upload("lut", b"not a cube")).status_code, 422)
        self.assertEqual(events, ["acquire", "release"])
        self.assert_no_assets()

    async def test_cancellation_while_disk_lease_enters_or_releases_is_drained(self):
        for phase in ("enter", "release"):
            entered, release = asyncio.Event(), asyncio.Event()
            events: list[str] = []
            @asynccontextmanager
            async def reserve(_amount: int):
                if phase == "enter":
                    entered.set()
                    await release.wait()
                events.append("acquired")
                try:
                    yield None
                finally:
                    if phase == "release":
                        entered.set()
                        await release.wait()
                    events.append("released")
            self.manager._upload_capacity_guard = SimpleNamespace(reserve=reserve)
            request = asyncio.create_task(self.upload("lut", b"invalid document"))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                self.assertTrue(studio_task_busy(self.root))
                self.assertFalse(request.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await request
            self.assertEqual(events, ["acquired", "released"])
            self.assert_no_assets()

    async def test_raw_body_wait_cannot_commit_into_replaced_task_directory(self):
        moved = self.data / "owned-original"
        replaced = False
        async def body():
            nonlocal replaced
            # This external-actor fixture needs a platform which permits an
            # open-file directory rename (Windows may refuse it safely).
            try:
                self.root.rename(moved)
            except OSError:
                raise HTTPException(409, "test host refused open directory rename")
            replaced = True
            self.root.mkdir()
            (self.root / "sentinel").write_bytes(b"do not touch replacement")
            yield cube()
        try:
            response = await self.upload("lut", body())
            self.assertNotEqual(response.status_code, 200)
            if replaced:
                self.assertEqual((self.root / "sentinel").read_bytes(), b"do not touch replacement")
                self.assertFalse((self.root / "studio").exists())
                (self.root / "sentinel").unlink()
                self.root.rmdir()
                moved.rename(self.root)
                # Original ownership can now be checked again for fixture cleanup.
                for work in self.root.glob("studio/assets/.ingest-*"):
                    assets.cleanup(self.root, work)
        finally:
            if moved.exists() and not self.root.exists():
                moved.rename(self.root)

    async def test_total64_quota_dedup_still_permitted_and_no_render_job_consumption(self):
        install_canonical(self.root, png(), "image")
        for index in range(63):
            install_canonical(self.root, cube(red=index/100), "lut")
        response = await self.upload("lut", cube(red=0.99))
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(len(assets.load_index(self.root).images) + len(assets.load_index(self.root).luts), 64)
        response = await self.upload("lut", cube(red=0.25))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["deduplicated"])
        self.assertFalse((self.root / "studio/jobs").exists())

    async def test_atomic_index_last_commit_and_dedup_hash_tamper(self):
        first = await self.upload("lut", cube())
        self.assertEqual(first.status_code, 200)
        before = (self.root / "studio/assets/index.json").read_bytes()
        with patch("backend.studio_assets._write_index", side_effect=OSError("private filesystem detail")):
            rejected = await self.upload("lut", cube(swap=True))
        self.assertEqual(rejected.status_code, 507)
        self.assertNotIn("private filesystem", rejected.text)
        self.assertEqual((self.root / "studio/assets/index.json").read_bytes(), before)
        self.assertEqual(len(list(self.root.glob("studio/assets/lut_*.cube"))), 1)
        path = assets.asset_path(self.root, first.json()["asset"]["id"])
        path.write_bytes(path.read_bytes().replace(b"0 0 0\n", b"1 0 0\n", 1))
        self.assertEqual((await self.upload("lut", cube())).status_code, 422)
        self.assertEqual((await self.client.get(self.prefix+"/assets")).status_code, 422)

    async def test_all_project_routes_validate_unknown_luts_and_restore_snapshots(self):
        imported = await self.upload("lut", cube())
        lut_id = imported.json()["asset"]["id"]
        project = Project(tracks=[Track(id="v", type="video", clips=[Clip(id="c", source_id="final", duration=1, lut_id=lut_id)])])
        self.assertEqual((await self.save(project)).status_code, 200)
        valid = (self.root / "studio/state.json").read_bytes()
        unknown = project.model_copy(deep=True)
        unknown.tracks[0].clips[0].lut_id = "lut_" + "f"*24
        for route in ("/project", "/project/import"):
            response = await self.client.post(self.prefix+route, json={"expected_revision": 1, "project": unknown.model_dump()})
            self.assertEqual(response.status_code, 422)
            self.assertEqual((self.root / "studio/state.json").read_bytes(), valid)
        # Forge only private fixture state to verify restoration fails closed.
        state = read_state(self.root)
        state["past"].append(unknown.model_dump())
        state["future"].append(unknown.model_dump())
        write_state(self.root, state)
        for op in ("undo", "redo"):
            response = await self.client.post(self.prefix+"/actions", json={"expected_revision": 1, "op": op})
            self.assertEqual(response.status_code, 422)
        state["project"] = unknown.model_dump()
        write_state(self.root, state)
        for route in ("/project", "/project/export"):
            self.assertEqual((await self.client.get(self.prefix+route)).status_code, 422)
        for route, payload in (("/actions", {"expected_revision": 1, "op": "marker", "new_id": "m", "at": 0}),
                               ("/subtitles/import", {"expected_revision": 1, "track_id": "captions", "srt": "1\n00:00:00,000 --> 00:00:00,500\nCaption"}),
                               ("/render", {"expected_revision": 1}), ("/export", {"expected_revision": 1})):
            self.assertEqual((await self.client.post(self.prefix+route, json=payload)).status_code, 422)

    async def test_lut_ids_roundtrip_history_restart_and_remain_server_paths_only(self):
        lut_id = (await self.upload("lut", cube())).json()["asset"]["id"]
        project = Project(tracks=[Track(id="a", type="adjustment", clips=[Clip(id="c", duration=1, lut_id=lut_id)])])
        self.assertEqual((await self.save(project)).status_code, 200)
        changed = project.model_copy(deep=True)
        changed.tracks[0].clips[0].lut_id = None
        self.assertEqual((await self.save(changed, 1)).status_code, 200)
        for op, revision, expected in (("undo", 2, lut_id), ("redo", 3, None), ("undo", 4, lut_id)):
            response = await self.client.post(self.prefix+"/actions", json={"expected_revision": revision, "op": op})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["project"]["tracks"][0]["clips"][0]["lut_id"], expected)
        app = FastAPI()
        app.include_router(create_studio_router(self.settings, self.manager, self.authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=self.client.headers) as client:
            snapshot = await client.get(self.prefix+"/project/export")
            self.assertEqual(snapshot.status_code, 200)
            self.assertEqual(snapshot.json()["project"], project.model_dump())
            self.assertEqual(snapshot.json()["revision"], 5)
            self.assertNotIn(str(self.root), snapshot.text)
            self.assertNotIn(".cube", snapshot.text)

    async def test_immutable_job_lut_watch_hash_catches_same_stat_change(self):
        imported = await self.upload("lut", cube())
        lut_id = imported.json()["asset"]["id"]
        project = Project(tracks=[Track(id="v", type="video", clips=[Clip(id="c", source_id="final", duration=1, lut_id=lut_id)])])
        self.assertEqual((await self.save(project)).status_code, 200)
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(_project: Any, _options: Any, _sources: Any, work: Path, *, luts: dict[str, Path], **_kwargs: Any):
            self.assertEqual(set(luts), {lut_id})
            snapshot = json.loads((work / "snapshot.json").read_text(encoding="utf-8"))
            self.assertIn(lut_id, snapshot["sources"])
            self.assertEqual(snapshot["asset_sha256"][lut_id], hashlib.sha256(luts[lut_id].read_bytes()).hexdigest())
            entered.set()
            await release.wait()
            path = luts[lut_id]
            stat = path.stat()
            path.write_bytes(path.read_bytes().replace(b"0 0 0\n", b"1 0 0\n", 1))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return {"duration": 1, "file": "not-media.mp4"}
        with patch("backend.studio.render_project", side_effect=blocked):
            response = await self.client.post(self.prefix+"/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.wait_for(entered.wait(), 5)
            self.assertEqual((await self.upload("lut", cube(), revision=1)).status_code, 409)
            release.set()
            await asyncio.gather(*list(_RUNNING.values()))
        job = (await self.client.get(self.prefix+"/jobs/"+response.json()["id"])).json()
        self.assertEqual(job["state"], "failed")
        self.assertNotIn(str(self.root), json.dumps(job))
        self.assertEqual((await self.client.get(self.prefix+"/outputs/"+job["id"])).status_code, 409)

    async def test_hidden_imported_ids_reach_immutable_renderer_mapping(self):
        image = install_canonical(self.root, png(), "image")
        lut = install_canonical(self.root, cube(), "lut")
        project = Project(tracks=[Track(id="v", type="video", clips=[Clip(id="c", source_id="final", duration=1)]),
                                  Track(id="hidden", type="overlay", hidden=True, clips=[Clip(id="h", source_id=image.id, duration=1, mute=True, lut_id=lut.id)])])
        self.assertEqual((await self.save(project)).status_code, 200)
        async def captured(_project: Any, _options: Any, sources: dict[str, Path], _work: Any, *, luts: dict[str, Path], **_kwargs: Any):
            self.assertIn(image.id, sources)
            self.assertIn(lut.id, luts)
            raise RenderError("contract capture only")
        with patch("backend.studio.render_project", side_effect=captured) as render:
            response = await self.client.post(self.prefix+"/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 202)
            await asyncio.gather(*list(_RUNNING.values()))
            render.assert_awaited_once()
            self.assertIn(image.id, render.await_args.args[2])
            self.assertIn(lut.id, render.await_args.kwargs["luts"])


class StudioAssetRendererContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_lut_fails_before_subtitle_early_return(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Project(tracks=[Track(id="a", type="adjustment", hidden=True, clips=[Clip(id="c", duration=1, lut_id="lut_"+"a"*24)]),
                                      Track(id="t", type="text", clips=[Clip(id="text", duration=1, text="caption")])])
            with self.assertRaisesRegex(RenderError, "lut_id"):
                await render_project(project, ExportOptions(format="srt"), {}, Path(temporary)/"output")

    async def test_catalog_only_probe_still_semantics_and_unknown_png_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = install_canonical(root, png(), "image")
            info = await probe(assets.asset_path(root, row.id), root)
            self.assertTrue(info["is_image"])
            self.assertEqual(info["duration"], MAX_DURATION)
            self.assertEqual(info["streams"][0]["avg_frame_rate"], "0/1")
            rogue = root / "unimported.png"
            rogue.write_bytes(png())
            with self.assertRaises(RenderError):
                await probe(rogue, root)

    async def test_explicit_loop_for_requested_fps_duration_and_real_lut_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = install_canonical(root, png(), "image")
            lut = install_canonical(root, cube(swap=True), "lut")
            captured: list[list[str]] = []
            async def stop(command: list[str], *_args: Any, **_kwargs: Any):
                captured.append(command)
                raise RenderError("command capture only, NOT rendered")
            project = image_project(image.id, lut_id=lut.id, duration=1.2, opacity=0.6,
                                    mask={"type": "ellipse", "width": 0.8, "height": 0.8},
                                    keyframes={"x": [{"time": 0, "value": -0.2}, {"time": 1.2, "value": 0.2}]})
            with patch("backend.studio_render.shutil.which", return_value="contract-double"), \
                    patch("backend.studio_render.run_logged_command", side_effect=stop), \
                    self.assertRaisesRegex(RenderError, "command capture"):
                await render_project(project, ExportOptions(resolution=360, fps=25), {image.id: assets.asset_path(root, image.id)},
                                     root/"work", luts={lut.id: assets.asset_path(root, lut.id)})
            self.assertEqual(len(captured), 1)
            command = captured[0]
            self.assertEqual(command[command.index("-loop")+1], "1")
            self.assertEqual(command[command.index("-framerate")+1], "25")
            self.assertEqual(command[command.index("-t")+1], "1.2")
            self.assertNotIn("-ss", command)
            graph = command[command.index("-filter_complex")+1]
            self.assertIn("lut3d=file=", graph)
            self.assertIn("interp=tetrahedral", graph)
            self.assertIn("alphaextract", graph)
            self.assertIn("alphamerge", graph)
            self.assertIn("alpha(X,Y)", graph)
            self.assertFalse((root/"work/output.mp4").exists())


@unittest.skipUnless(existing.TOOLS, "FFmpeg/ffprobe must be on PATH; no simulated codec success")
class StudioAssetMediaTests(AssetAPIHarness):
    async def imported_image(self, data: bytes | None = None, *, content_type: str = "image/png") -> dict[str, Any]:
        response = await self.upload("image", png() if data is None else data, **{"Content-Type": content_type})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["asset"]

    async def imported_lut(self, **fields: Any) -> dict[str, Any]:
        response = await self.upload("lut", cube(**fields))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["asset"]

    async def render(self, project: Project, *, at: float = 0, **options: Any) -> tuple[dict[str, Any], bytes]:
        index = assets.load_index(self.root)
        work = self.root / ("media-" + str(len(list(self.root.glob("media-*")))))
        source_before = {row.id: hashlib.sha256(assets.asset_path(self.root, row.id).read_bytes()).hexdigest() for row in [*index.images, *index.luts]}
        result = await render_project(project, ExportOptions(format="png", resolution=360, aspect="1:1", frame_time=at, **options),
                                      catalog(self.record), work, luts=assets.lut_paths(self.root, index))
        output = work / result["file"]
        frame = existing.ffmpeg("-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(frame), 360*360*3)
        for source_id, digest in source_before.items():
            self.assertEqual(hashlib.sha256(assets.asset_path(self.root, source_id).read_bytes()).hexdigest(), digest)
        self.assertNotIn(str(self.root), json.dumps(result))
        return result, frame

    @staticmethod
    def pixel(frame: bytes, x: int = 180, y: int = 180) -> tuple[int, ...]:
        start = (y*360+x)*3
        return tuple(frame[start:start+3])

    async def test_real_png_reencode_alpha_metadata_dedup_preview_and_no_original_disclosure(self):
        document = png(metadata=chunk(b"tEXt", b"secret\0private-original-name")+chunk(b"eXIf", b"private-exif"))
        image = await self.imported_image(document)
        self.assertEqual(set(image), {"id", "name", "bytes", "width", "height", "url"})
        self.assertRegex(image["name"], r"^贴纸 [0-9a-f]{8}\.png$")
        stored = assets.asset_path(self.root, image["id"]).read_bytes()
        self.assertNotIn(b"private", stored)
        self.assertNotIn(b"eXIf", stored)
        self.assertNotIn(b"tEXt", stored)
        self.assertEqual(image["id"], "image_"+hashlib.sha256(stored).hexdigest()[:24])
        raw = existing.ffmpeg("-i", str(assets.asset_path(self.root, image["id"])), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1")
        self.assertEqual(raw, bytes((220, 60, 20, 128))*256)
        again = await self.upload("image", png())
        self.assertEqual(again.status_code, 200, again.text)
        self.assertTrue(again.json()["deduplicated"])
        self.assertEqual(again.json()["asset"], image)
        response = await self.client.get(image["url"], headers={"Range": "bytes=0-31"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, stored[:32])
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual((await self.client.get(image["url"], headers={"X-Token": "bad"})).status_code, 403)
        self.assertFalse((self.root / "studio/state.json").exists())
        source = next(row for row in (await self.client.get(self.prefix+"/sources")).json()["sources"] if row["id"] == image["id"])
        self.assertTrue(source["is_image"])
        self.assertEqual(source["duration_semantics"], "still_hold_limit_not_source_eof")

    async def test_real_jpeg_normalizes_metadata_and_rejects_concatenated_images(self):
        source = self.root / "fixture.jpg"
        existing.ffmpeg("-y", "-f", "lavfi", "-i", "color=red:s=16x16", "-frames:v", "1", "-c:v", "mjpeg", "-update", "1", str(source))
        data = source.read_bytes()
        comment = b"private JPEG comment"
        decorated = data[:2] + b"\xff\xfe"+struct.pack(">H", len(comment)+2)+comment + data[2:]
        image = await self.imported_image(decorated, content_type="image/jpeg")
        self.assertTrue(image["name"].endswith(".png"))
        stored = assets.asset_path(self.root, image["id"]).read_bytes()
        self.assertNotIn(comment, stored)
        raw = existing.ffmpeg("-i", str(assets.asset_path(self.root, image["id"])), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1")
        self.assertTrue(all(value == 255 for value in raw[3::4]))
        self.assertEqual((await self.upload("image", data+data, **{"Content-Type": "image/jpeg"})).status_code, 422)
        self.assertEqual(source.read_bytes(), data)

    async def test_real_identity_vs_nonidentity_lut_correct_rgb_channel_and_alpha(self):
        image = await self.imported_image()
        identity, swapped = await self.imported_lut(), await self.imported_lut(swap=True)
        pixels = []
        for lut_id in (None, identity["id"], swapped["id"]):
            _, frame = await self.render(image_project(image["id"], lut_id=lut_id))
            pixels.append(self.pixel(frame))
        plain, unchanged, changed = pixels
        self.assertLess(max(abs(a-b) for a, b in zip(plain, unchanged)), 8)
        self.assertAlmostEqual(unchanged[0], 220*128/255, delta=12)
        self.assertAlmostEqual(unchanged[1], 60*128/255, delta=12)
        self.assertAlmostEqual(unchanged[2], 20*128/255, delta=12)
        self.assertGreater(changed[2], changed[0]+65, "red-fast cube must swap red to BLUE, not green")
        self.assertAlmostEqual(changed[1], unchanged[1], delta=10)
        self.assertAlmostEqual(changed[2], unchanged[0], delta=12)
        self.assertLess(changed[2], 145, "LUT must not turn source alpha opaque")

    async def test_real_lut_is_after_typed_rgb_curves_not_before(self):
        image = await self.imported_image(png(rgba=(220, 60, 20, 255)))
        lut = await self.imported_lut(swap=True)
        project = image_project(image["id"], lut_id=lut["id"], rgb_curves={"red": [{"x": 0, "y": 0}, {"x": 1, "y": 0.5}]})
        _, frame = await self.render(project)
        pixel = self.pixel(frame)
        self.assertAlmostEqual(pixel[2], 110, delta=15, msg="red attenuation precedes red/blue LUT swap")
        self.assertAlmostEqual(pixel[0], 20, delta=12)
        self.assertAlmostEqual(pixel[1], 60, delta=12)

    async def test_real_transparent_pixels_mask_opacity_and_keyframes(self):
        pixels = bytearray(bytes((240, 20, 20, 128))*16*16)
        for y in range(16):
            for x in range(4):
                pixels[(y*16+x)*4+3] = 0
        image = await self.imported_image(png(pixels=bytes(pixels)))
        lut = await self.imported_lut(swap=True)
        project = image_project(image["id"], lut_id=lut["id"], opacity=0.5)
        _, frame = await self.render(project)
        self.assertLess(max(self.pixel(frame, 20, 180)), 5)
        self.assertAlmostEqual(self.pixel(frame)[2], 240*128/255*0.5, delta=12)
        animated = image_project(image["id"], lut_id=lut["id"], mask={"type": "ellipse", "width": 0.8, "height": 0.8},
                                 keyframes={"opacity": [{"time": 0, "value": 0.2}, {"time": 1, "value": 1}]})
        _, early = await self.render(animated, at=0.1)
        _, late = await self.render(animated, at=0.8)
        self.assertGreater(self.pixel(late)[2], self.pixel(early)[2]+35)
        self.assertLess(max(self.pixel(late, 5, 5)), 5)

    async def test_real_adjustment_lut_time_bounds_and_lower_track_composite(self):
        image = await self.imported_image(png(rgba=(220, 60, 20, 255)))
        lut = await self.imported_lut(swap=True)
        project = image_project(image["id"])
        project.tracks.append(Track(id="adjust", type="adjustment", clips=[Clip(id="lut", start=0.4, duration=0.4, lut_id=lut["id"]) ]))
        frames = [(await self.render(project, at=at))[1] for at in (0.2, 0.6, 0.9)]
        before, during, after = [self.pixel(frame) for frame in frames]
        self.assertGreater(before[0], before[2]+150)
        self.assertGreater(during[2], during[0]+150)
        self.assertLess(max(abs(a-b) for a, b in zip(before, after)), 6)
        self.assertAlmostEqual(during[1], before[1], delta=10)

    async def test_real_lut_paths_with_spaces_apostrophe_comma_and_brackets(self):
        nested = self.root / "space ' comma, [asset]"
        nested.mkdir()
        self.record.task_dir = nested
        try:
            image = await self.imported_image(png(rgba=(220, 60, 20, 255)))
            lut = await self.imported_lut(swap=True)
            work = nested / "render ' [output]"
            result = await render_project(image_project(image["id"], lut_id=lut["id"]),
                ExportOptions(format="png", resolution=360, aspect="1:1"),
                {image["id"]: assets.asset_path(nested, image["id"])}, work,
                luts={lut["id"]: assets.asset_path(nested, lut["id"])})
            raw = existing.ffmpeg("-i", str(work/result["file"]), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
            self.assertGreater(self.pixel(raw)[2], self.pixel(raw)[0]+150)
        finally:
            self.record.task_dir = self.root

    async def test_real_image_loops_requested_duration_fps_not_source25fps_or_freeze(self):
        image = await self.imported_image(png(rgba=(220, 60, 20, 255)))
        index = assets.load_index(self.root)
        for fps in (24, 25, 30, 60):
            work = self.root / f"motion-{fps}"
            project = image_project(image["id"], duration=1.2)
            result = await render_project(project, ExportOptions(resolution=360, fps=fps), catalog(self.record), work,
                                          luts=assets.lut_paths(self.root, index))
            video = next(s for s in result["probe"]["streams"] if s["codec_type"] == "video")
            numerator, denominator = map(float, video["avg_frame_rate"].split("/"))
            self.assertAlmostEqual(numerator/denominator, fps)
            self.assertAlmostEqual(float(result["probe"]["format"]["duration"]), 1.2, delta=2/fps)
            last = existing.ffmpeg("-ss", "1", "-i", str(work/result["file"]), "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
            self.assertEqual(len(last), 3)
            self.assertGreater(last[0], 170, "a still must remain visible beyond image EOF")

    async def test_real_video_lut_and_overlay_image_channels_preserve_sources(self):
        video = self.root / "real.mp4"
        existing.ffmpeg("-y", "-f", "lavfi", "-i", "color=red:s=64x64:r=30:d=1", "-c:v", "libx264", "-threads", "1", str(video))
        digest = hashlib.sha256(video.read_bytes()).hexdigest()
        self.record.uploads = [SimpleNamespace(path=video)]
        video_id = next(key for key, path in catalog(self.record).items() if path == video)
        lut = await self.imported_lut(swap=True)
        image = await self.imported_image(png(rgba=(0, 240, 0, 128)))
        project = Project(tracks=[Track(id="v", type="video", clips=[Clip(id="video", source_id=video_id, duration=1, fit="cover", mute=True, lut_id=lut["id"])]),
                                  Track(id="o", type="overlay", clips=[Clip(id="sticker", source_id=image["id"], duration=1, mute=True, scale=0.5)])])
        _, frame = await self.render(project)
        corner, center = self.pixel(frame, 20, 20), self.pixel(frame)
        self.assertGreater(corner[2], 180)
        self.assertLess(corner[0], 30)
        self.assertGreater(center[1], 85)
        self.assertGreater(center[2], 75)
        self.assertLess(center[1], 160, "overlay should retain its intrinsic half alpha")
        self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), digest)

    async def test_real_image_lut_job_keeps_mandatory_generated_disclosure_and_qc(self):
        image = await self.imported_image()
        lut = await self.imported_lut(swap=True)
        self.assertEqual((await self.save(image_project(image["id"], lut_id=lut["id"]))).status_code, 200)
        manifest = {"schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence", "items": [
            {"sentence_id": 0, "beat_id": 0, "shot_id": 0, "mode": "video", "prompt_sha256": "a"*64, "disclosure_text": "AI生成示意画面"}]}
        (self.root / "generated_media_disclosure.json").write_text(json.dumps(manifest), encoding="utf-8")
        confirm_synthetic_publication(self, self.record, generated=True)
        for fmt in ("wav", "srt"):
            response = await self.client.post(self.prefix+"/render", json={"expected_revision": 1, "options": {"format": fmt}})
            self.assertEqual(response.status_code, 422)
        response = await self.client.post(self.prefix+"/render", json={"expected_revision": 1, "options": {"format": "png", "resolution": 360}})
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.gather(*list(_RUNNING.values()))
        job = (await self.client.get(self.prefix+"/jobs/"+response.json()["id"])).json()
        self.assertEqual(job["state"], "succeeded", job)
        self.assertTrue(job["disclosure"])
        work = self.root / "studio/outputs/r1" / job["id"]
        self.assertIn("AI生成示意画面", (work/"studio.ass").read_text(encoding="utf-8"))
        actual = existing.ffmpeg("-i", str(work/"output.png"), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertTrue(any(max(actual[i:i+3])-min(actual[i:i+3]) < 20 and min(actual[i:i+3]) > 180 for i in range(0, 640*65*3, 3)),
                        "disclosure must produce white pixels, not just an ASS file")
        before = (self.root / "quality_report.json").read_bytes()
        try:
            (self.root / "quality_report.json").write_bytes(synthetic_publication_files(blocked=True)["quality_report.json"])
            rejected = await self.client.post(self.prefix+"/render", json={"expected_revision": 1})
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(rejected.json()["detail"], QC_BLOCKER)
        finally:
            (self.root / "quality_report.json").write_bytes(before)


if __name__ == "__main__":
    unittest.main()