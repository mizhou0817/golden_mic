"""Synthetic TEMP receipts only: no build, app import, service or old evidence."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import frontend_binding as binding
from tests import mode_acceptance_server as modes


class V2BuildBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="gm-binding-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        names = set(binding.SOURCE_FILES) | {
            "frontend/src/main.tsx", "frontend/src/ui/tokens.css", "frontend/tsconfig.extra.json",
            "frontend/vite.config.mts", "frontend/e2e/v2/build.mjs", "frontend/e2e/modes/io.mjs",
            "frontend/e2e/v2/lightning-css.mjs",
        }
        for name in names:
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("synthetic " + name, encoding="utf-8")
        self.frontend = self.root / "frontend/dist-canary-modes-v2-unit"
        (self.frontend / "assets").mkdir(parents=True)
        self.assets = {}
        for name in ("index.html", "assets/app.js", "assets/app.css"):
            file = self.frontend / name
            file.write_text("synthetic " + name, encoding="utf-8")
            self.assets[name] = modes.sha256(file)
        manifest = "".join(f"{digest}  {name}\n" for name, digest in sorted(self.assets.items()))
        (self.frontend / binding.MANIFEST_NAME).write_bytes(manifest.encode())
        sources = binding.source_hashes(self.root)
        self.receipt = {
            "kind": "synthetic-mode-custom-build", "sourceHashes": sources,
            "sourceHashesAfter": dict(sources), "sourceUnchangedDuringBuild": True,
            "assets": self.assets, "assetManifestSHA256": hashlib.sha256(manifest.encode()).hexdigest(),
            "helperHashes": {name: modes.sha256(self.root / name) for name in
                             ("frontend/e2e/v2/build.mjs", "frontend/e2e/modes/io.mjs")},
            "cssEvidence": {"engine": "postcss"}, "viteConfigLoaded": False,
        }
        owner = patch.object(modes, "PROJECT_ROOT", self.root)
        owner.start()
        self.addCleanup(owner.stop)

    def write(self) -> None:
        (self.frontend / binding.BINDING_NAME).write_text(json.dumps(self.receipt), encoding="utf-8")

    def validate(self) -> dict[str, str]:
        self.write()
        return modes.build_sources(self.frontend, dict(self.assets))

    def test_complete_custom_and_standard_receipts(self) -> None:
        self.assertEqual(self.validate(), binding.source_hashes(self.root))
        self.receipt.update(kind="frontend-source-build", schemaVersion=1)
        self.receipt.pop("helperHashes")
        self.assertEqual(self.validate(), binding.source_hashes(self.root))

    def test_each_anchor_and_discovered_config_required(self) -> None:
        for name in binding.source_hashes(self.root):
            with self.subTest(name=name):
                before = self.receipt["sourceHashes"].pop(name)
                after = self.receipt["sourceHashesAfter"].pop(name)
                try:
                    with self.assertRaises(RuntimeError):
                        self.validate()
                finally:
                    self.receipt["sourceHashes"][name] = before
                    self.receipt["sourceHashesAfter"][name] = after

    def test_unsafe_paths_rejected_before_hash_read(self) -> None:
        for name in ("frontend/package-lock.json?query", "frontend/../package.json",
                     "frontend/src/./main.tsx", "frontend/src//main.tsx", "frontend/.env",
                     "frontend/config/vite.config.ts", "frontend/scripts/unapproved.mjs",
                     "frontend/tsconfig.foo/bar.json", "frontend/src/a.ts.", "frontend/src/a\\b.ts"):
            with self.subTest(name=name):
                self.receipt["sourceHashes"][name] = "0" * 64
                self.receipt["sourceHashesAfter"][name] = "0" * 64
                try:
                    with self.assertRaisesRegex(RuntimeError, "unsafe_custom_build_source"):
                        self.validate()
                finally:
                    del self.receipt["sourceHashes"][name]
                    del self.receipt["sourceHashesAfter"][name]

    def test_bad_before_after_and_missing_after(self) -> None:
        self.receipt["sourceHashesAfter"]["frontend/package.json"] = "0" * 64
        with self.assertRaises(RuntimeError):
            self.validate()
        self.receipt.pop("sourceHashesAfter")
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_stale_input_and_added_config(self) -> None:
        file = self.root / "frontend/vite.config.ts"
        original = file.read_bytes()
        file.write_bytes(b"changed but still configFile:false")
        with self.assertRaises(RuntimeError):
            self.validate()
        file.write_bytes(original)
        (self.root / "frontend/tsconfig.new.json").write_text("{}")
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_stale_assets_and_manifest(self) -> None:
        asset = self.frontend / "assets/app.js"
        original = asset.read_bytes()
        asset.write_bytes(b"stale")
        with self.assertRaises(RuntimeError):
            self.validate()
        asset.write_bytes(original)
        manifest = self.frontend / binding.MANIFEST_NAME
        manifest.write_bytes(manifest.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaisesRegex(RuntimeError, "manifest_hash"):
            self.validate()

    def test_missing_stale_and_unsafe_helpers(self) -> None:
        helpers = self.receipt["helperHashes"]
        for replacement in ({}, {**helpers, "frontend/e2e/v2/build.mjs": "0" * 64},
                            {**helpers, "frontend/scripts/other.mjs": "0" * 64}):
            self.receipt["helperHashes"] = replacement
            with self.assertRaises(RuntimeError):
                self.validate()
        self.receipt["helperHashes"] = helpers
        self.receipt["cssEvidence"] = {"engine": "lightningcss"}
        with self.assertRaises(RuntimeError):
            self.validate()
        helpers["frontend/e2e/v2/lightning-css.mjs"] = modes.sha256(self.root / "frontend/e2e/v2/lightning-css.mjs")
        self.validate()

    def test_legacy_modes_shape_read_only_current_only(self) -> None:
        old = self.root / "frontend/dist-canary-modes-legacy-unit"
        self.frontend.rename(old)
        self.frontend = old
        self.receipt["sourceHashes"] = {name: digest for name, digest in self.receipt["sourceHashes"].items()
            if name.startswith("frontend/src/") or name in {
                "frontend/index.html", "frontend/package.json", "frontend/postcss.config.cjs",
                "frontend/tailwind.config.ts", "backend/mode_rules.json"}}
        for key in ("sourceHashesAfter", "helperHashes", "assetManifestSHA256"):
            self.receipt.pop(key)
        self.write()
        snapshot = {file.name: file.read_bytes() for file in old.iterdir() if file.is_file()}
        self.assertEqual(modes.build_sources(old, self.assets), self.receipt["sourceHashes"])
        self.assertEqual(snapshot, {file.name: file.read_bytes() for file in old.iterdir() if file.is_file()})
        (self.root / "frontend/src/main.tsx").write_text("stale")
        with self.assertRaises(RuntimeError):
            modes.build_sources(old, self.assets)

    def test_v2_cannot_use_legacy_or_absent_receipt(self) -> None:
        with self.assertRaises(RuntimeError):
            modes.build_sources(self.frontend, self.assets)
        self.receipt.pop("sourceHashesAfter")
        self.receipt.pop("assetManifestSHA256")
        with self.assertRaises(RuntimeError):
            self.validate()


if __name__ == "__main__":
    unittest.main()