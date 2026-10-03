"""TEMP-only release tests: no backend import, shared dist, tools or services.

The default unittest suite is compatible with the existing guarded V2 runner:
no subprocesses, environment files, models, network, or dependency generation.
Dependency/font validation is explicitly stubbed ONLY in packaging fixtures.
NODE_TEST_PROGRAM is separately runnable via installed Node stdin, not discovery;
its fake Vite is an orchestration unit fixture, NOT a real compiler acceptance.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any, BinaryIO
from unittest.mock import patch

from deploy import build_release as builder
from deploy.frontend_binding import (
    BINDING_NAME, MANIFEST_NAME, SOURCE_FILES, parse_binding, source_hashes,
    validate_frontend_binding,
)
from deploy.verify_release_archive import REQUIRED_MEMBERS, validate_release_archive


V2_REQUIRED = (
    "backend/drafts.py", "backend/admission.py", "backend/v2_editing.py",
    "backend/frontend_static.py", "backend/windows_asyncio.py",
    "backend/mode_pipeline.py", "backend/production_modes.py", "backend/mode_rules.json",
    "backend/speech_analysis.py", "backend/providers/local_speech.py", "backend/task_metadata.py",
    "backend/local_speech_bundle.py", "deploy/verify_local_speech_bundle.py",
    "docs/RUNBOOK.md", "docs/V2_IMPLEMENTATION_20260929.md", "docs/DESIGN-MAP.md",
    "deploy/frontend_binding.py",
)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class ReleaseIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gm-release-integrity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        self.dist = self.root / "frontend/dist"
        # All bytes are synthetic, including dummy font/wheel/locks/backend.
        names = set(builder.INCLUDED_FILES) | set(SOURCE_FILES)
        names.update(name.removeprefix("golden-mic/") for name in REQUIRED_MEMBERS)
        names.update({"frontend/src/main.tsx", "frontend/src/ui/style.css",
                      "backend/local_speech_bundle.py", "deploy/verify_local_speech_bundle.py",
                      "frontend/dist/assets/app.js", "frontend/dist/assets/app.css"})
        for name in names:
            self.put(name, ("fixture:" + name).encode())
        self.write_binding()
        root_patch = patch.object(builder, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        # Any accidental tool invocation is an error, not a fake success.
        process_patch = patch.object(builder.subprocess, "run", side_effect=AssertionError("unexpected native tool"))
        process_patch.start()
        self.addCleanup(process_patch.stop)

    def put(self, name: str, value: bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def write_binding(self, *, custom: bool = False) -> None:
        assets = {path.relative_to(self.dist).as_posix(): digest(path.read_bytes())
                  for path in self.dist.rglob("*") if path.is_file()
                  and (path.name == "index.html" or "assets" in path.relative_to(self.dist).parts)}
        manifest = "".join(f"{assets[name]}  {name}\n" for name in sorted(assets)).encode()
        (self.dist / MANIFEST_NAME).write_bytes(manifest)
        sources = source_hashes(self.root)
        self.binding: dict[str, Any] = {"kind": "synthetic-mode-custom-build" if custom else "frontend-source-build",
                        "sourceHashes": sources, "sourceHashesAfter": dict(sources),
                        "sourceUnchangedDuringBuild": True, "assets": assets}
        if not custom:
            self.binding.update(schemaVersion=1, assetManifestSHA256=digest(manifest))
        self.save_binding()

    def save_binding(self) -> None:
        (self.dist / BINDING_NAME).write_text(json.dumps(self.binding, sort_keys=True), encoding="utf-8")

    def validate(self) -> None:
        validate_frontend_binding(self.root, self.dist)

    def archive_entries(self) -> dict[str, bytes]:
        return {"golden-mic/" + relative.as_posix(): path.read_bytes()
                for path, relative in builder._release_entries()}  # pyright: ignore[reportPrivateUsage]

    def archive(self, entries: dict[str, bytes] | None = None, *, extra: tarfile.TarInfo | None = None,
                directories: tuple[str, ...] | set[str] = ()) -> Path:
        target = self.root.parent / "release.tar.gz"
        with tarfile.open(target, "w:gz") as archive:
            for name, payload in (entries if entries is not None else self.archive_entries()).items():
                info = tarfile.TarInfo(name)
                if name in directories:
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                else:
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
            if extra is not None:
                archive.addfile(extra)
        return target

    def test_complete_binding_accepts(self):
        self.validate()

    def test_complete_existing_custom_binding_accepts_without_rewriting(self):
        self.write_binding(custom=True)
        before = (self.dist / BINDING_NAME).read_bytes()
        self.validate()
        self.assertEqual(before, (self.dist / BINDING_NAME).read_bytes())

    def test_missing_binding_blocks_before_backend_import_or_tools(self):
        (self.dist / BINDING_NAME).unlink()
        modules = set(sys.modules)
        with self.assertRaisesRegex(RuntimeError, "Missing frontend"):
            builder._validate_generated_artifacts()  # pyright: ignore[reportPrivateUsage]
        self.assertFalse({name for name in set(sys.modules) - modules if name.startswith("backend")})

    def test_every_source_byte_change_blocks(self):
        for name in source_hashes(self.root):
            with self.subTest(source=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    self.validate()
                path.write_bytes(original)

    def test_added_nested_source_blocks(self):
        self.put("frontend/src/deeper/new.ts", b"export {}")
        with self.assertRaisesRegex(RuntimeError, "stale"):
            self.validate()

    def test_deleted_source_blocks(self):
        (self.root / "frontend/src/ui/style.css").unlink()
        with self.assertRaisesRegex(RuntimeError, "stale"):
            self.validate()

    def test_added_config_blocks(self):
        self.put("frontend/tsconfig.build.json", b"{}")
        with self.assertRaisesRegex(RuntimeError, "stale"):
            self.validate()

    def test_unrelated_files_not_in_source_inventory(self):
        before = source_hashes(self.root)
        self.put("frontend/notes.txt", b"not a compiler input")
        self.put("docs/unrelated.md", b"not a compiler input")
        self.assertEqual(before, source_hashes(self.root))
        self.validate()

    def test_incomplete_custom_helper_inventory_rejected(self):
        self.write_binding(custom=True)
        for field in ("sourceHashes", "sourceHashesAfter"):
            self.binding[field].pop("frontend/package-lock.json")
        self.save_binding()
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            self.validate()

    def test_custom_subset_of_src_not_accepted(self):
        self.write_binding(custom=True)
        for field in ("sourceHashes", "sourceHashesAfter"):
            self.binding[field].pop("frontend/src/ui/style.css")
        self.save_binding()
        with self.assertRaisesRegex(RuntimeError, "stale"):
            self.validate()

    def test_source_snapshot_mismatch_rejected(self):
        self.binding["sourceHashesAfter"]["frontend/src/main.tsx"] = "a" * 64
        self.save_binding()
        with self.assertRaisesRegex(RuntimeError, "drift"):
            self.validate()

    def test_false_or_nonboolean_source_assertion_rejected(self):
        for value in (False, 1, "true", None):
            with self.subTest(value=value):
                self.binding["sourceUnchangedDuringBuild"] = value
                self.save_binding()
                with self.assertRaisesRegex(RuntimeError, "drift"):
                    self.validate()

    def test_unknown_kind_or_schema_rejected(self):
        for field, value in (("kind", "asset-only"), ("schemaVersion", 2), ("schemaVersion", True)):
            with self.subTest(field=field, value=value):
                self.write_binding()
                self.binding[field] = value
                self.save_binding()
                with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                    self.validate()

    def test_asset_mutation_rejected_even_with_new_asset_manifest(self):
        path = self.dist / "assets/app.js"
        path.write_bytes(b"changed")
        manifest = self.dist / MANIFEST_NAME
        manifest.write_text("".join(f"{digest((self.dist / name).read_bytes())}  {name}\n"
                                    for name in sorted(self.binding["assets"])), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            self.validate()

    def test_added_or_removed_asset_rejected(self):
        path = self.dist / "assets/extra.js"
        path.write_bytes(b"extra")
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            self.validate()
        path.unlink()
        (self.dist / "assets/app.js").unlink()
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            self.validate()

    def test_manifest_byte_hash_bound(self):
        path = self.dist / MANIFEST_NAME
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(RuntimeError, "manifest binding mismatch"):
            self.validate()

    def test_duplicate_manifest_entry_rejected(self):
        path = self.dist / MANIFEST_NAME
        original = path.read_bytes()
        path.write_bytes(original + original.splitlines(keepends=True)[0])
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            self.validate()

    def test_duplicate_receipt_json_keys_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            parse_binding(b'{"assets": {}, "assets": {}}')

    def test_unsafe_or_unrelated_source_references_rejected(self):
        for name in ("../escape", "/absolute", "frontend/src/../secret", "frontend/src\\bad.ts",
                     "frontend/.env", "backend/main.py", "frontend/src/trailing. "):
            with self.subTest(name=name):
                self.write_binding()
                for field in ("sourceHashes", "sourceHashesAfter"):
                    self.binding[field][name] = "a" * 64
                self.save_binding()
                with self.assertRaisesRegex(RuntimeError, "reference"):
                    self.validate()

    def test_case_aliased_source_references_rejected(self):
        for field in ("sourceHashes", "sourceHashesAfter"):
            self.binding[field]["frontend/src/MAIN.tsx"] = "a" * 64
        self.save_binding()
        with self.assertRaisesRegex(RuntimeError, "Case-aliased"):
            self.validate()

    def test_unexpected_dist_file_rejected(self):
        self.put("frontend/dist/private.json", b"{}")
        with self.assertRaisesRegex(RuntimeError, "Unexpected"):
            self.validate()

    def test_required_v2_members_and_docs_are_included(self):
        entries = self.archive_entries()
        for name in V2_REQUIRED:
            self.assertIn("golden-mic/" + name, REQUIRED_MEMBERS)
            self.assertIn("golden-mic/" + name, entries)
        self.assertNotIn("golden-mic/frontend/src/main.tsx", entries)
        validate_release_archive(self.archive(entries))

    def test_each_required_member_omission_rejected(self):
        entries = self.archive_entries()
        for name in sorted(REQUIRED_MEMBERS):
            with self.subTest(member=name):
                reduced = dict(entries)
                del reduced[name]
                with self.assertRaisesRegex(RuntimeError, "missing required members"):
                    validate_release_archive(self.archive(reduced))

    def test_current_selection_and_closure_docs_are_not_historical_requirements(self):
        names = ("docs/V2_MODEL_SELECTION_20261002.md",
                 "docs/v2-model-selection-20261002.json",
                 "docs/V2_CURRENT_CLOSURE_20260930.md")
        entries = self.archive_entries()
        for name in names:
            self.assertIn(name, builder.INCLUDED_FILES)
            self.assertIn("golden-mic/" + name, entries)
            self.assertNotIn("golden-mic/" + name, REQUIRED_MEMBERS)
            del entries["golden-mic/" + name]
        # Only synthetic TEMP bytes; no real release build or archive required.
        validate_release_archive(self.archive(entries))

    def test_missing_frontend_static_rejected_with_or_without_binding(self):
        # Independent literal: deriving this case only from REQUIRED_MEMBERS
        # would silently miss a newly imported backend module absent there.
        for bound in (False, True):
            with self.subTest(bound=bound):
                entries = self.archive_entries()
                entries.pop("golden-mic/backend/frontend_static.py", None)
                if not bound:
                    entries.pop("golden-mic/frontend/dist/" + BINDING_NAME)
                with self.assertRaisesRegex(RuntimeError, r"missing required members: .*backend/frontend_static\.py"):
                    validate_release_archive(self.archive(entries), require_binding=bound)

    def test_builder_missing_frontend_static_rejected_before_archive_write(self):
        path = self.root / "backend/frontend_static.py"
        path.unlink(missing_ok=True)
        output = self.root.parent / "must-not-exist.tar.gz"
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate), \
                patch.object(sys, "argv", ["build_release", "--output", str(output)]), \
                self.assertRaisesRegex(SystemExit, r"Missing required release members: .*backend/frontend_static\.py"):
            builder.main()
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".gz.sha256").exists())

    def test_missing_windows_asyncio_rejected_with_or_without_binding(self):
        # Opt-in runtime use does not make shipping this component optional.
        # Keep a literal independent of REQUIRED_MEMBERS so omission cannot
        # disappear from both the fixture and its required-member loop.
        for bound in (False, True):
            with self.subTest(bound=bound):
                entries = self.archive_entries()
                entries.pop("golden-mic/backend/windows_asyncio.py", None)
                if not bound:
                    entries.pop("golden-mic/frontend/dist/" + BINDING_NAME)
                with self.assertRaisesRegex(RuntimeError, r"missing required members: .*backend/windows_asyncio\.py"):
                    validate_release_archive(self.archive(entries), require_binding=bound)

    def test_builder_missing_windows_asyncio_rejected_before_archive_write(self):
        (self.root / "backend/windows_asyncio.py").unlink(missing_ok=True)
        output = self.root.parent / "must-not-exist.tar.gz"
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate), \
                patch.object(sys, "argv", ["build_release", "--output", str(output)]), \
                self.assertRaisesRegex(SystemExit, r"Missing required release members: .*backend/windows_asyncio\.py"):
            builder.main()
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".gz.sha256").exists())

    def test_current_validation_record_included_but_not_legacy_anchor(self):
        name = "docs/V2_VALIDATION_20260930.md"
        self.assertIn(name, builder.INCLUDED_FILES)
        entries = self.archive_entries()
        member = "golden-mic/" + name
        self.assertIn(member, entries)
        self.assertNotIn(member, REQUIRED_MEMBERS)
        # Dated documentation is shipped by current builders, not a runtime
        # dependency. Both receipt schemas may omit this later-added record.
        del entries[member]
        validate_release_archive(self.archive(entries), require_binding=True)
        del entries["golden-mic/frontend/dist/" + BINDING_NAME]
        validate_release_archive(self.archive(entries))

    def test_current_builder_missing_validation_record_rejected(self):
        (self.root / "docs/V2_VALIDATION_20260930.md").unlink(missing_ok=True)
        output = self.root.parent / "must-not-exist.tar.gz"
        with patch.object(sys, "argv", ["build_release", "--output", str(output)]), \
                self.assertRaisesRegex(SystemExit, r"Missing release inputs: .*V2_VALIDATION_20260930\.md"):
            builder.main()
        self.assertFalse(output.exists())

    def test_required_files_cannot_be_directories(self):
        for name in V2_REQUIRED:
            with self.subTest(member=name), self.assertRaisesRegex(RuntimeError, "not a regular file"):
                validate_release_archive(self.archive(directories={"golden-mic/" + name}))

    def test_legacy_asset_only_tar_schema_still_accepted(self):
        # Same shape as the existing deployment regression: no receipt, opaque
        # payloads. Current REQUIRED_MEMBERS still apply; no grandfathered gaps.
        entries = {name: b"valid" for name in REQUIRED_MEMBERS}
        validate_release_archive(self.archive(entries))

    def test_current_builder_cannot_downgrade_to_legacy_without_receipt(self):
        entries = self.archive_entries()
        del entries["golden-mic/frontend/dist/" + BINDING_NAME]
        with self.assertRaisesRegex(RuntimeError, "missing its frontend build binding"):
            validate_release_archive(self.archive(entries), require_binding=True)

    def test_packaging_postcheck_rejects_source_drift(self):
        original = builder._release_entries  # pyright: ignore[reportPrivateUsage]
        def change_after_precheck():
            entries = original()
            self.put("frontend/src/main.tsx", b"changed after initial validation")
            return entries
        output = self.root.parent / "drift.tar.gz"
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate), \
                patch.object(builder, "_release_entries", side_effect=change_after_precheck), \
                patch.object(sys, "argv", ["build_release", "--output", str(output)]), \
                self.assertRaisesRegex(RuntimeError, "stale"):
            builder.main()
        self.assertFalse(output.with_suffix(".gz.sha256").exists())

    def test_archive_tampered_assets_rejected(self):
        entries = self.archive_entries()
        entries["golden-mic/frontend/dist/assets/app.js"] = b"tampered"
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            validate_release_archive(self.archive(entries))

    def test_archive_critical_mode_rules_json_not_ignored(self):
        entries = self.archive_entries()
        entries["golden-mic/backend/mode_rules.json"] = b"{}"
        with self.assertRaisesRegex(RuntimeError, "mode rules"):
            validate_release_archive(self.archive(entries))

    def test_archive_malformed_receipt_never_legacy_fallback(self):
        entries = self.archive_entries()
        for value in (b"invalid", b"[]", b"{}"):
            with self.subTest(value=value):
                entries["golden-mic/frontend/dist/" + BINDING_NAME] = value
                with self.assertRaises(RuntimeError):
                    validate_release_archive(self.archive(entries))

    def test_archive_receipt_directory_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "receipt/manifest"):
            validate_release_archive(self.archive(directories={"golden-mic/frontend/dist/" + BINDING_NAME}))

    def test_archive_paths_duplicates_links_and_devices_rejected(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("golden-mic/.env", tarfile.REGTYPE),
                           ("golden-mic/backend/./main.py", tarfile.REGTYPE),
                           ("golden-mic/link", tarfile.SYMTYPE), ("golden-mic/device", tarfile.CHRTYPE),
                           ("golden-mic/backend/main.py", tarfile.REGTYPE)):
            with self.subTest(name=name):
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = "golden-mic/backend/main.py" if kind == tarfile.SYMTYPE else ""
                with self.assertRaises(RuntimeError):
                    validate_release_archive(self.archive(extra=info))

    def test_packaging_is_deterministic_and_temp_only(self):
        outputs = [self.root.parent / f"built-{n}.tar.gz" for n in (1, 2)]
        # Only external artifact/font checks are replaced; real freshness,
        # inclusion, tar writer, postcheck and archive verification execute.
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate), patch("builtins.print"):
            for output in outputs:
                with patch.object(sys, "argv", ["build_release", "--output", str(output)]):
                    self.assertEqual(builder.main(), 0)
                validate_release_archive(output)
        self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
        self.assertTrue(outputs[0].with_suffix(".gz.sha256").read_text().startswith(digest(outputs[0].read_bytes())))

    def test_builder_blocks_missing_required_module(self):
        (self.root / "backend/drafts.py").unlink()
        output = self.root.parent / "must-not-exist.tar.gz"
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate), \
                patch.object(sys, "argv", ["build_release", "--output", str(output)]), \
                self.assertRaisesRegex(SystemExit, "Missing required release members"):
            builder.main()
        self.assertFalse(output.exists())

    def test_builder_missing_doc_rejected(self):
        (self.root / "docs/RUNBOOK.md").unlink()
        output = self.root.parent / "must-not-exist.tar.gz"
        with patch.object(sys, "argv", ["build_release", "--output", str(output)]), \
                self.assertRaisesRegex(SystemExit, "Missing release inputs"):
            builder.main()
        self.assertFalse(output.exists())

    def test_builder_stale_source_refuses_before_archive_write(self):
        self.put("frontend/src/main.tsx", b"new source, old assets")
        output = self.root.parent / "must-not-exist.tar.gz"
        with patch.object(sys, "argv", ["build_release", "--output", str(output)]), \
                self.assertRaisesRegex(RuntimeError, "stale"):
            builder.main()
        self.assertFalse(output.exists())

    def selected_dist(self) -> Path:
        selected = self.root / "frontend/dist-canary-Release_01"
        shutil.copytree(self.dist, selected)
        self.dist = selected
        self.put("frontend/dist-canary-Release_01/assets/app.js", b"isolated JS")
        self.put("frontend/dist-canary-Release_01/assets/app.js.map", b'{"version":3}')
        self.write_binding()
        return selected

    def validate_selected(self, frontend_dir: Path) -> None:
        self.assertEqual(frontend_dir, self.dist)
        self.validate()

    def invoke(self, output: Path, frontend: str | None = None) -> int:
        args = ["build_release", "--output", str(output)]
        if frontend is not None:
            args += ["--frontend-dir", frontend]
        with patch.object(sys, "argv", args), patch("builtins.print"):
            return builder.main()

    def test_speech_bundle_anchors_independently_required_with_without_receipt(self):
        for name in ("golden-mic/backend/local_speech_bundle.py",
                     "golden-mic/deploy/verify_local_speech_bundle.py"):
            for bound in (False, True):
                with self.subTest(name=name, bound=bound):
                    entries = self.archive_entries()
                    entries.pop(name, None)
                    if not bound:
                        entries.pop("golden-mic/frontend/dist/" + BINDING_NAME)
                    with self.assertRaisesRegex(RuntimeError, "missing required members") as raised:
                        validate_release_archive(self.archive(entries), require_binding=bound)
                    self.assertIn(name, str(raised.exception))

    def test_missing_each_speech_bundle_anchor_fails_before_archive(self):
        for name in ("backend/local_speech_bundle.py", "deploy/verify_local_speech_bundle.py"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.unlink()
                output = self.root.parent / "missing-anchor.tar.gz"
                try:
                    with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate), \
                            self.assertRaisesRegex(SystemExit, "Missing required release members") as raised:
                        self.invoke(output)
                    self.assertIn(name, str(raised.exception))
                    self.assertFalse(output.exists())
                    self.assertFalse(output.with_suffix(".gz.sha256").exists())
                finally:
                    path.write_bytes(original)

    def test_progress_doc_shipped_not_runtime_anchor(self):
        name = "docs/V2_PRODUCT_PROGRESS_20261002.md"
        self.assertIn(name, builder.INCLUDED_FILES)
        entries = self.archive_entries()
        self.assertIn("golden-mic/" + name, entries)
        self.assertNotIn("golden-mic/" + name, REQUIRED_MEMBERS)
        del entries["golden-mic/" + name]
        validate_release_archive(self.archive(entries), require_binding=True)
        del entries["golden-mic/frontend/dist/" + BINDING_NAME]
        validate_release_archive(self.archive(entries))

    def test_missing_progress_doc_fails_before_archive(self):
        (self.root / "docs/V2_PRODUCT_PROGRESS_20261002.md").unlink(missing_ok=True)
        output = self.root.parent / "missing-doc.tar.gz"
        with self.assertRaisesRegex(SystemExit, "Missing release inputs: .*V2_PRODUCT_PROGRESS"):
            self.invoke(output)
        self.assertFalse(output.exists())

    def test_selected_dist_canonical_mapping_binding_maps_and_determinism(self):
        shared = {p.relative_to(self.dist): p.read_bytes() for p in self.dist.rglob("*") if p.is_file()}
        selected = self.selected_dist()
        receipt = (selected / BINDING_NAME).read_bytes()
        outputs = [self.root.parent / f"isolated-{n}.tar.gz" for n in range(2)]
        def precheck(frontend_dir: Path) -> None:
            self.assertEqual(frontend_dir, selected)
            self.validate()
        with patch.object(builder, "_validate_generated_artifacts", side_effect=precheck):
            for output, argument in zip(outputs, ("frontend/" + selected.name, str(selected))):
                self.assertEqual(self.invoke(output, argument), 0)
                validate_release_archive(output, require_binding=True)
                with tarfile.open(output) as archive:
                    names = archive.getnames()
                    self.assertFalse(any("dist-canary" in name for name in names))
                    for leaf in (BINDING_NAME, "assets/app.js", "assets/app.js.map"):
                        stream = archive.extractfile("golden-mic/frontend/dist/" + leaf)
                        self.assertIsNotNone(stream)
                        assert stream is not None
                        with stream:
                            self.assertEqual(stream.read(), (selected / leaf).read_bytes())
                    for member in archive.getmembers():
                        self.assertEqual(member.mtime, builder.FIXED_MTIME)
                        self.assertEqual((member.uid, member.gid), (0, 0))
                        self.assertEqual((member.uname, member.gname), ("root", "root"))
                header = output.read_bytes()[:10]
                self.assertEqual(header[3], 0)  # No original output filename.
                self.assertEqual(int.from_bytes(header[4:8], "little"), builder.FIXED_MTIME)
        self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
        self.assertEqual(receipt, (selected / BINDING_NAME).read_bytes())
        self.assertEqual(shared, {p.relative_to(self.root / "frontend/dist"): p.read_bytes()
                                 for p in (self.root / "frontend/dist").rglob("*") if p.is_file()})

    def test_selected_dist_does_not_require_or_autodiscover_shared_dist(self):
        selected = self.selected_dist()
        shutil.rmtree(self.root / "frontend/dist")
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate_selected):
            self.assertEqual(self.invoke(self.root.parent / "selected.tar.gz", str(selected)), 0)
        with self.assertRaisesRegex(SystemExit, "Missing release inputs"):
            self.invoke(self.root.parent / "default-missing.tar.gz")

    def test_explicit_default_dist_and_zero_argument_legacy_calls(self):
        output = self.root.parent / "explicit-default.tar.gz"
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate) as check:
            self.assertEqual(self.invoke(output, "frontend/dist"), 0)
            check.assert_called_once_with()

    def test_frontend_directory_policy_rejects_noncanonical_inputs(self):
        selected = self.selected_dist()
        for argument in ("frontend/dist-canary-", "frontend/dist-canary-" + "x" * 65,
                         "frontend/dist-canary-../bad", "frontend/dist-canary-bad label",
                         "frontend/dist-canary-Release_01/", "frontend/./" + selected.name,
                         "frontend/../frontend/" + selected.name, "Frontend/" + selected.name,
                         "frontend/" + selected.name.lower(), "frontend/nested/" + selected.name,
                         "frontend/arbitrary", str(self.root.parent / selected.name),
                         str(selected.parent) + os.sep + ".." + os.sep + "frontend" + os.sep + selected.name):
            with self.subTest(argument=argument), \
                    patch.object(builder, "_validate_generated_artifacts") as check:
                output = self.root.parent / "unsafe.tar.gz"
                with self.assertRaisesRegex(SystemExit, "frontend-dir"):
                    self.invoke(output, argument)
                check.assert_not_called()
                self.assertFalse(output.exists())

    def test_selected_directory_junction_or_symlink_rejected(self):
        target = self.selected_dist()
        link = self.root / "frontend/dist-canary-link"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            self.addCleanup(link.rmdir)
        else:
            link.symlink_to(target, target_is_directory=True)
            self.addCleanup(link.unlink)
        with self.assertRaisesRegex((RuntimeError, SystemExit), "links|junction|frontend-dir"):
            self.invoke(self.root.parent / "linked.tar.gz", str(link))
        self.assertFalse((self.root.parent / "linked.tar.gz").exists())

    def test_selected_dist_rejects_missing_stale_or_unlisted_before_archive(self):
        selected = self.selected_dist()
        mutations = ((selected / BINDING_NAME, None),
                     (self.root / "frontend/src/main.tsx", b"stale"),
                     (selected / "private.json", b"{}"),
                     (selected / "assets/unlisted.js", b"unlisted"))
        for path, payload in mutations:
            with self.subTest(path=path.name):
                original = path.read_bytes() if path.exists() else None
                if payload is None:
                    path.unlink()
                else:
                    path.write_bytes(payload)
                output = self.root.parent / "invalid-selected.tar.gz"
                try:
                    with self.assertRaises(RuntimeError):
                        self.invoke(output, str(selected))
                    self.assertFalse(output.exists())
                finally:
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)

    def test_selected_postcheck_rejects_source_drift(self):
        selected = self.selected_dist()
        original = builder._release_entries  # pyright: ignore[reportPrivateUsage]
        def entries_then_drift(frontend_dir: Path) -> list[tuple[Path, Path]]:
            entries = original(frontend_dir)
            self.put("frontend/src/main.tsx", b"changed after precheck")
            return entries
        output = self.root.parent / "selected-drift.tar.gz"
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate_selected), \
                patch.object(builder, "_release_entries", side_effect=entries_then_drift), \
                self.assertRaisesRegex(RuntimeError, "stale"):
            self.invoke(output, str(selected))
        self.assertFalse(output.with_suffix(".gz.sha256").exists())

    def test_packed_selected_assets_checked_even_if_disk_restored(self):
        selected = self.selected_dist()
        original = tarfile.TarFile.addfile
        def corrupt(archive: tarfile.TarFile, info: tarfile.TarInfo, fileobj: BinaryIO | None = None) -> None:
            if info.name == "golden-mic/frontend/dist/assets/app.js":
                fileobj = io.BytesIO(b"x" * info.size)
            return original(archive, info, fileobj)
        output = self.root.parent / "packed-corrupt.tar.gz"
        with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate_selected), \
                patch.object(tarfile.TarFile, "addfile", new=corrupt), \
                self.assertRaisesRegex(RuntimeError, "mismatch"):
            self.invoke(output, str(selected))
        self.assertFalse(output.with_suffix(".gz.sha256").exists())

    def test_existing_output_or_checksum_not_overwritten(self):
        for suffix in ("", ".sha256"):
            with self.subTest(suffix=suffix):
                output = self.root.parent / ("occupied" + ("-sum" if suffix else "") + ".tar.gz")
                occupied = Path(str(output) + suffix)
                occupied.write_bytes(b"preserve original")
                with patch.object(builder, "_validate_generated_artifacts") as check, \
                        self.assertRaisesRegex(SystemExit, "existing"):
                    self.invoke(output)
                check.assert_not_called()
                self.assertEqual(occupied.read_bytes(), b"preserve original")
                self.assertFalse(Path(str(output) + ("" if suffix else ".sha256")).exists())

    def test_output_inside_source_tree_rejected_before_validation(self):
        with patch.object(builder, "_validate_generated_artifacts") as check, \
                self.assertRaisesRegex(SystemExit, "input tree"):
            self.invoke(self.root / "deploy/release.tar.gz")
        check.assert_not_called()
        self.assertFalse((self.root / "deploy/release.tar.gz").exists())

    def test_forbidden_files_in_included_tree_fail_before_archive(self):
        for name in ("backend/.env", "deploy/data/private.sqlite3"):
            with self.subTest(name=name):
                self.put(name, b"synthetic forbidden input")
                output = self.root.parent / "forbidden.tar.gz"
                try:
                    with patch.object(builder, "_validate_generated_artifacts", side_effect=self.validate), \
                            self.assertRaisesRegex((RuntimeError, SystemExit), "Forbidden"):
                        self.invoke(output)
                    self.assertFalse(output.exists())
                finally:
                    (self.root / name).unlink()

    def test_generated_checks_select_dist_without_changing_readiness_globals(self):
        from backend import readiness
        selected = self.selected_dist()
        constants = (readiness.FRONTEND_INDEX, readiness.FRONTEND_MANIFEST)
        def tool(command: list[str], **kwargs: object) -> None:
            if "export" in command:
                Path(command[-1]).write_bytes((self.root / "requirements-production.lock").read_bytes())
            elif "--output" in command:
                Path(command[-1]).write_bytes((self.root / "sbom.cdx.json").read_bytes())
        with patch.object(readiness, "validate_frontend_dist") as default_check, \
                patch.object(readiness, "validate_font_asset") as font_check, \
                patch.object(builder.subprocess, "run", side_effect=tool) as tools:
            builder._validate_generated_artifacts(selected)  # pyright: ignore[reportPrivateUsage]
            default_check.assert_not_called()
            font_check.assert_called_once_with()
            self.assertEqual(tools.call_count, 3)
            self.assertEqual(tools.call_args_list[0].args[0], ["uv", "lock", "--check"])
            self.assertEqual(constants, (readiness.FRONTEND_INDEX, readiness.FRONTEND_MANIFEST))
        with patch.object(readiness, "validate_frontend_dist") as default_check, \
                patch.object(readiness, "validate_font_asset") as font_check, \
                patch.object(builder.subprocess, "run", side_effect=tool):
            builder._validate_generated_artifacts()  # pyright: ignore[reportPrivateUsage]
            default_check.assert_called_once_with()
            font_check.assert_called_once_with()

    def test_selected_readiness_empty_index_and_insufficient_manifest_rejected(self):
        selected = self.selected_dist()
        (selected / "index.html").write_bytes(b"")
        self.write_binding()
        with self.assertRaisesRegex(RuntimeError, "index.html"):
            builder._validate_generated_artifacts(selected)  # pyright: ignore[reportPrivateUsage]
        (selected / "index.html").write_bytes(b"index")
        (selected / "assets/app.css").unlink()
        (selected / "assets/app.js.map").unlink()
        self.write_binding()
        with self.assertRaisesRegex(RuntimeError, "manifest|清单"):
            builder._validate_generated_artifacts(selected)  # pyright: ignore[reportPrivateUsage]

    def test_selected_checks_still_reject_dependency_and_sbom_drift(self):
        from backend import readiness
        selected = self.selected_dist()
        for stale in ("requirements-production.lock", "sbom.cdx.json"):
            def tool(command: list[str], **kwargs: object) -> None:
                if "export" in command or "--output" in command:
                    target = Path(command[-1])
                    target.write_bytes(b"stale" if target.name == stale else (self.root / target.name).read_bytes())
            with self.subTest(stale=stale), patch.object(readiness, "validate_font_asset"), \
                    patch.object(builder.subprocess, "run", side_effect=tool), \
                    self.assertRaisesRegex(RuntimeError, "not synchronized"):
                builder._validate_generated_artifacts(selected)  # pyright: ignore[reportPrivateUsage]


# Manual, separately executed Node tests use ONLY this script's synthetic TEMP
# repo and a fake Vite package. No dependencies/build output from the real repo.
NODE_TEST_PROGRAM = r"""
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const assert = require('node:assert/strict'), cp = require('node:child_process');
const writer = fs.readFileSync(path.join(process.cwd(), 'frontend/scripts/write-manifest.mjs'));
const base = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-release-manifest-'));
let passed = 0;
function fixture() {
  const root = fs.mkdtempSync(path.join(base, 'repo-'));
  const put = (name, bytes) => {const p=path.join(root,name);fs.mkdirSync(path.dirname(p),{recursive:true});fs.writeFileSync(p,bytes);};
  for(const name of ['frontend/index.html','frontend/package.json','frontend/package-lock.json',
    'frontend/vite.config.ts','frontend/postcss.config.cjs','frontend/tailwind.config.ts',
    'frontend/tsconfig.json','frontend/tsconfig.node.json','backend/mode_rules.json']) put(name,'{}');
  put('frontend/src/main.tsx','export {};');put('frontend/src/ui/style.css','body{}');
  put('frontend/scripts/write-manifest.mjs',writer);
  put('frontend/node_modules/vite/package.json',JSON.stringify({name:'vite',type:'module',exports:'./index.mjs'}));
  put('frontend/node_modules/vite/index.mjs',`import fs from 'node:fs';import path from 'node:path';
    export async function build(options) {
      if(options.envDir!==false || options.publicDir!==false) throw Error('fixture policy');
      const root=options.root,out=options.build.outDir;
      fs.mkdirSync(path.join(out,'assets'),{recursive:true});
      fs.writeFileSync(path.join(out,'index.html'),'<div>fixture</div>');
      fs.writeFileSync(path.join(out,'assets/app.js'),'fixture bundle');
      if(fs.existsSync(path.join(root,'drift'))) fs.appendFileSync(path.join(root,'src/main.tsx'),'drift');
      if(fs.existsSync(path.join(root,'fail'))) throw Error('fixture compiler failed');
    }`);
  const frontend=path.join(root,'frontend'),dist=path.join(frontend,'dist-canary-test');
  const run = (build=true,label='test') => cp.spawnSync(process.execPath,
    [path.join(frontend,'scripts/write-manifest.mjs'),...(build?['--build']:[]),'--out-dir','dist-canary-'+label],
    {cwd:frontend,encoding:'utf8',env:Object.fromEntries(Object.entries(process.env).filter(([k])=>/^(?:SystemRoot|WINDIR|PATH|PATHEXT|TEMP|TMP)$/i.test(k)))});
  const receipt=()=>JSON.parse(fs.readFileSync(path.join(dist,'MODE_BUILD_BINDING.json')));
  return {root,put,frontend,dist,run,receipt};
}
function test(fn){fn();passed++;}
try {
  test(()=>{const f=fixture();assert.equal(f.run().status,0);const b=f.receipt();
    assert.equal(Object.keys(b.sourceHashes).length,11);assert.deepEqual(b.sourceHashes,b.sourceHashesAfter);
    assert.ok(b.sourceHashes['frontend/package-lock.json']);assert.equal(b.kind,'frontend-source-build');
    assert.equal(f.run(false).status,0);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);assert.equal(f.run(true,'second').status,0);
    assert.deepEqual(fs.readFileSync(path.join(f.dist,'MODE_BUILD_BINDING.json')),
      fs.readFileSync(path.join(f.frontend,'dist-canary-second/MODE_BUILD_BINDING.json')));});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);const original=fs.readFileSync(path.join(f.dist,'MODE_BUILD_BINDING.json'));
    f.put('frontend/src/main.tsx','changed');assert.notEqual(f.run(false).status,0);
    assert.deepEqual(fs.readFileSync(path.join(f.dist,'MODE_BUILD_BINDING.json')),original);});
  test(()=>{const f=fixture();f.put('frontend/dist-canary-test/index.html','old');f.put('frontend/dist-canary-test/assets/app.js','old');
    assert.notEqual(f.run(false).status,0);assert.equal(fs.existsSync(path.join(f.dist,'MODE_BUILD_BINDING.json')),false);
    assert.equal(fs.existsSync(path.join(f.dist,'ASSET_MANIFEST.sha256')),false);});
  test(()=>{const f=fixture();f.put('frontend/drift','1');assert.notEqual(f.run().status,0);
    assert.equal(fs.existsSync(path.join(f.dist,'MODE_BUILD_BINDING.json')),false);});
  test(()=>{const f=fixture();f.put('frontend/fail','1');assert.notEqual(f.run().status,0);
    assert.equal(fs.existsSync(path.join(f.dist,'MODE_BUILD_BINDING.json')),false);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);f.put('frontend/src/nested/new.ts','new');assert.notEqual(f.run(false).status,0);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);f.put('frontend/package-lock.json','changed');assert.notEqual(f.run(false).status,0);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);f.put('frontend/tsconfig.extra.json','{}');assert.notEqual(f.run(false).status,0);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);f.put('frontend/unrelated.txt','ignore');assert.equal(f.run(false).status,0);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);f.put('frontend/dist-canary-test/assets/app.js','changed');assert.notEqual(f.run(false).status,0);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);const b=f.receipt();b.kind='synthetic-mode-custom-build';
    delete b.schemaVersion;delete b.assetManifestSHA256;f.put('frontend/dist-canary-test/MODE_BUILD_BINDING.json',JSON.stringify(b));
    assert.equal(f.run(false).status,0);delete b.sourceHashes['frontend/package-lock.json'];delete b.sourceHashesAfter['frontend/package-lock.json'];
    f.put('frontend/dist-canary-test/MODE_BUILD_BINDING.json',JSON.stringify(b));assert.notEqual(f.run(false).status,0);});
  test(()=>{const f=fixture();assert.equal(f.run().status,0);assert.notEqual(f.run().status,0);});
  console.log(JSON.stringify({status:'passed',tests:passed,compiler:'synthetic fixture only',sharedDistAccessed:false}));
} finally { fs.rmSync(base,{recursive:true,force:true}); }
"""


if __name__ == "__main__":
    unittest.main()