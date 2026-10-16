"""Validation bootstrap tests; owned TEMP paths, no tools or app imports."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests import validation_environment as bootstrap
from tests.run_v2_validation import DARWIN_EXCLUSIONS, LINUX_EXCLUSIONS, SetupFailure, platform_suite


class ValidationEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gm-validation-bootstrap-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def tool(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic test file; never execute")
        path.chmod(0o755)
        return path

    def test_clean_clone_creates_only_empty_evidence_parent(self):
        (self.root / "canary_test").mkdir()
        parent = bootstrap.evidence_parent(self.root)
        self.assertEqual(parent, self.root / "canary_test/artifacts")
        self.assertEqual(list(parent.iterdir()), [])
        (parent / "old-receipt").write_bytes(b"keep")
        self.assertEqual(bootstrap.evidence_parent(self.root), parent)
        self.assertEqual((parent / "old-receipt").read_bytes(), b"keep")

    def test_missing_or_file_parent_refused(self):
        with self.assertRaises(OSError):
            bootstrap.evidence_parent(self.root)
        (self.root / "canary_test").write_bytes(b"not directory")
        with self.assertRaises(RuntimeError):
            bootstrap.evidence_parent(self.root)

    def test_evidence_directory_name_surrogate_rejected(self):
        (self.root / "canary_test").mkdir()
        parent = self.root / "canary_test/artifacts"
        parent.mkdir()
        original = Path.lstat
        def info(path):
            if path == parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_reparse_tag=0xA0000003)
            return original(path)
        with patch.object(Path, "lstat", info), self.assertRaises(RuntimeError):
            bootstrap.evidence_parent(self.root)

    def test_linux_paired_tools_and_missing_probe(self):
        encoder, probe = self.tool("bin/ffmpeg"), self.tool("bin/ffprobe")
        mapping = {"ffmpeg": str(encoder), "ffprobe": str(probe)}
        with patch.object(bootstrap.shutil, "which", side_effect=lambda name, **kw: mapping.get(name)):
            self.assertEqual(bootstrap.media_tools({"PATH": "explicit"}, platform="linux"), (encoder, probe))
            mapping.pop("ffprobe")
            with self.assertRaisesRegex(RuntimeError, "linux_media_tools_required"):
                bootstrap.media_tools({}, platform="linux")

    def test_linux_unpaired_tool_directories_refused(self):
        encoder, probe = self.tool("one/ffmpeg"), self.tool("two/ffprobe")
        with patch.object(bootstrap.shutil, "which", side_effect=[str(encoder), str(probe)]), \
                self.assertRaisesRegex(RuntimeError, "paired_media_tools_required"):
            bootstrap.media_tools({}, platform="linux")

    def test_non_executable_or_linked_tool_refused(self):
        encoder, probe = self.tool("bin/ffmpeg"), self.tool("bin/ffprobe")
        for member in ("is_symlink", "access"):
            target = Path if member == "is_symlink" else bootstrap.os
            with patch.object(bootstrap.shutil, "which", side_effect=[str(encoder), str(probe)]), \
                    patch.object(target, member, return_value=member == "is_symlink"), \
                    self.assertRaisesRegex(RuntimeError, "unsafe_media_tool"):
                bootstrap.media_tools({}, platform="linux")

    def test_windows_pair_uses_owned_winget_inventory(self):
        stem = "Microsoft/WinGet/Packages/Gyan.FFmpeg-test/bin/"
        encoder, probe = self.tool(stem + "ffmpeg.exe"), self.tool(stem + "ffprobe.exe")
        self.assertEqual(bootstrap.media_tools({"LOCALAPPDATA": str(self.root)}, platform="win32"), (encoder, probe))
        probe.unlink()
        with self.assertRaisesRegex(RuntimeError, "windows_media_tools_required"):
            bootstrap.media_tools({"LOCALAPPDATA": str(self.root)}, platform="win32")

    def test_windows_temp_ignores_untrusted_tmpdir(self):
        (self.root / "Temp").mkdir()
        self.assertEqual(bootstrap.temporary_base({"LOCALAPPDATA": str(self.root), "TMPDIR": "ignored"},
                                                 platform="win32"), self.root / "Temp")
        with self.assertRaises(RuntimeError):
            bootstrap.temporary_base({}, platform="unsupported")

    def test_exact_linux_selection_and_windows_full_selection(self):
        class Case(unittest.TestCase):
            def __init__(self, identity):
                super().__init__()
                self.identity = identity
            def id(self):
                return self.identity
            def runTest(self):
                raise AssertionError("inventory only")
        identities = sorted(LINUX_EXCLUSIONS) + ["tests.portable.ImportTests.contract"]
        suite = unittest.TestSuite(Case(identity) for identity in identities)
        selected, excluded = platform_suite(suite, platform="linux")
        self.assertEqual(excluded, sorted(LINUX_EXCLUSIONS))
        self.assertEqual([case.id() for case in selected], [identities[-1]])
        selected, excluded = platform_suite(suite, platform="win32")
        self.assertEqual(selected.countTestCases(), len(identities))
        self.assertEqual(excluded, [])
        with self.assertRaises(SetupFailure):
            platform_suite(unittest.TestSuite([Case("renamed.platform.case")]), platform="linux")

    def test_darwin_temp_is_fixed_real_directory(self):
        # /tmp and /var are symlinks on macOS; the base must be the real /private/tmp.
        with patch.object(bootstrap, "_directory"):
            self.assertEqual(bootstrap.temporary_base({"TMPDIR": "ignored"}, platform="darwin"), Path("/private/tmp"))

    def test_darwin_homebrew_style_links_resolve_to_paired_real_tools(self):
        encoder, probe = self.tool("Cellar/ffmpeg/1/bin/ffmpeg"), self.tool("Cellar/ffmpeg/1/bin/ffprobe")
        (self.root / "bin").mkdir()
        links = {"ffmpeg": self.root / "bin/ffmpeg", "ffprobe": self.root / "bin/ffprobe"}
        links["ffmpeg"].symlink_to(encoder)
        links["ffprobe"].symlink_to(probe)
        with patch.object(bootstrap.shutil, "which", side_effect=lambda name, **kw: str(links[name]) if name in links else None):
            self.assertEqual(bootstrap.media_tools({"PATH": "explicit"}, platform="darwin"),
                             (encoder.resolve(), probe.resolve()))
        with patch.object(bootstrap.shutil, "which", return_value=None), \
                self.assertRaisesRegex(RuntimeError, "darwin_media_tools_required"):
            bootstrap.media_tools({}, platform="darwin")

    def test_darwin_selection_lists_windows_native_and_case_probe_exclusions(self):
        class Case(unittest.TestCase):
            def __init__(self, identity):
                super().__init__()
                self.identity = identity
            def id(self):
                return self.identity
            def runTest(self):
                raise AssertionError("inventory only")
        self.assertTrue(LINUX_EXCLUSIONS < DARWIN_EXCLUSIONS)
        identities = sorted(DARWIN_EXCLUSIONS) + ["tests.portable.ImportTests.contract"]
        selected, excluded = platform_suite(unittest.TestSuite(Case(name) for name in identities), platform="darwin")
        self.assertEqual(excluded, sorted(DARWIN_EXCLUSIONS))
        self.assertEqual([case.id() for case in selected], [identities[-1]])
