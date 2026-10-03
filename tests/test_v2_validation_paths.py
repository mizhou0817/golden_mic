"""FD-relative guard regressions; synthetic descriptors plus real owned cleanup."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from tests import run_core_validation as core


class ValidationPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gm-validation-paths-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "owned"
        self.evidence = self.base / "evidence"
        self.foreign = self.base / "foreign"
        for directory in (self.root, self.evidence, self.foreign):
            directory.mkdir()
        self.guard = core.Guards(self.root, self.evidence, self.root / "not-executed-ffmpeg")
        self.guard.stage = "suite"

    @contextmanager
    def descriptors(self):
        paths = {71: self.root, 72: self.foreign, 73: self.evidence}
        def readlink(name):
            try:
                return str(paths[int(str(name).rsplit("/", 1)[-1])])
            except (KeyError, ValueError):
                raise OSError("synthetic invalid descriptor") from None
        def fstat(fd):
            if fd not in paths:
                raise OSError("synthetic invalid descriptor")
            return paths[fd].stat()
        with patch.object(core.sys, "platform", "linux"), \
                patch.object(core.os, "readlink", side_effect=readlink), \
                patch.object(core.os, "fstat", side_effect=fstat):
            yield

    def test_relative_mutation_audits_use_owned_directory(self):
        events = (("os.remove", ("child", 71)), ("os.rmdir", ("child", 71)),
                  ("os.mkdir", ("child", 0o700, 71)), ("os.chmod", ("child", 0o600, 71)),
                  ("os.utime", ("child", None, None, 71)), ("shutil.rmtree", ("child", 71)))
        with self.descriptors():
            for event, arguments in events:
                with self.subTest(event=event):
                    self.guard.audit(event, arguments)
        self.assertEqual(self.guard.counts["owned_dir_fd_paths"], len(events))

    def test_invalid_foreign_and_traversing_descriptors_refused(self):
        with self.descriptors():
            for path, fd in (("child", 72), ("child", 999), ("child", -2),
                             ("../child", 71), ("a/../../child", 71), ("./child", 71)):
                with self.subTest(path=path, fd=fd), self.assertRaises(core.SafetyViolation):
                    self.guard.audit("os.remove", (path, fd))

    def test_absolute_and_cwd_paths_cannot_borrow_fd_authority(self):
        with self.descriptors():
            self.guard.audit("os.remove", (str(self.root / "child"), 999))
            for path, fd in ((str(self.foreign / "child"), 71), ("child", -1), ("child", None)):
                with self.subTest(fd=fd), self.assertRaises(core.SafetyViolation):
                    self.guard.audit("os.remove", (path, fd))

    def test_rename_and_link_check_source_and_destination_descriptors(self):
        with self.descriptors():
            for event in ("os.rename", "os.link"):
                self.guard.audit(event, ("source", "destination", 71, 73))
                for source, destination in ((72, 73), (71, 72)):
                    with self.subTest(event=event, source=source), self.assertRaises(core.SafetyViolation):
                        self.guard.audit(event, ("source", "destination", source, destination))

    def test_changed_deleted_or_non_directory_descriptor_refused(self):
        file = self.root / "not-directory"
        file.write_bytes(b"synthetic")
        with self.descriptors():
            for info in (self.foreign.stat(), file.stat()):
                with patch.object(core.os, "fstat", return_value=info), self.assertRaises(core.SafetyViolation):
                    self.guard.audit("os.remove", ("child", 71))
            with patch.object(core.os, "readlink", return_value=str(self.root) + " (deleted)"), \
                    self.assertRaises(core.SafetyViolation):
                self.guard.audit("os.remove", ("child", 71))

    def test_symlink_relative_target_is_bound_to_destination_parent(self):
        with self.descriptors():
            self.guard.audit("os.symlink", ("source", "destination", 71))
            with self.assertRaises(core.SafetyViolation):
                self.guard.audit("os.symlink", (str(self.foreign / "source"), "destination", 71))
            with self.assertRaises(core.SafetyViolation):
                self.guard.audit("os.symlink", (str(self.root / "source"), "destination", 72))

    def test_open_audit_preserves_original_fd_and_clears_context(self):
        def native(file, flags, mode, *, dir_fd=None):
            self.guard.audit("open", (os.fspath(file), None, flags | getattr(os, "O_CLOEXEC", 0)))
            return 123
        with self.descriptors(), ExitStack() as stack:
            original = stack.enter_context(patch.object(core.os, "open", side_effect=native))
            self.guard.install_file_guards(stack)
            self.assertEqual(os.open("data", os.O_RDONLY, dir_fd=71), 123)
            original.assert_called_once_with("data", os.O_RDONLY, 0o777, dir_fd=71)
            self.assertIsNone(self.guard.local.dir_fd_open)
            original.reset_mock()
            with self.assertRaises(core.SafetyViolation):
                os.open("child", os.O_WRONLY, dir_fd=72)
            original.assert_not_called()

    def test_open_context_rejects_other_paths_flags_and_unscoped_private_read(self):
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        with self.descriptors():
            self.guard.local.dir_fd_open = ("child", flags, 71)
            for arguments in (("other", None, flags), ("child", None, flags | os.O_WRONLY),
                              (str(core.PROJECT / ".env"), "r", flags)):
                with self.subTest(arguments=arguments), self.assertRaises(core.SafetyViolation):
                    self.guard.audit("open", arguments)
            self.guard.local.dir_fd_open = None
            with self.assertRaises(core.SafetyViolation):
                self.guard.audit("open", (str(core.PROJECT / ".env"), "r", flags))

    def test_real_owned_temporary_tree_cleanup_with_private_looking_names(self):
        active = True
        def audit(event, arguments):
            if active:
                self.guard.audit(event, arguments)
        with ExitStack() as stack:
            self.guard.install_file_guards(stack)
            sys.addaudithook(audit)
            try:
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    for name in ("data/tasks", "canary_test/artifacts", "nested"):
                        target = root / name
                        target.mkdir(parents=True)
                        (target / ".env").write_bytes(b"synthetic only")
                    self.assertTrue(root.exists())
                self.assertFalse(root.exists())
                self.assertFalse(any(":denied:" in name for name in self.guard.counts))
                if sys.platform == "linux":
                    self.assertGreater(self.guard.counts["owned_dir_fd_paths"], 0)
            finally:
                active = False


if __name__ == "__main__":
    unittest.main()