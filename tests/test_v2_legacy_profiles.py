"""Focused, stdlib-only inventory contracts; no application or legacy imports.

Direct -I -B execution runs ONLY this module and prints counts, never tracebacks
or assertion messages. All filesystem fixtures are new TemporaryDirectory trees.
These tests do not exercise the proposed legacy execution adapter (not built).
"""
from __future__ import annotations

import builtins
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


# Load only this owned stdlib utility, not tests package initialization or runners.
_path = Path(__file__).with_name("run_legacy_validation.py")
_spec = importlib.util.spec_from_file_location("gm_legacy_inventory", _path)
assert _spec is not None and _spec.loader is not None
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


class LegacyProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="gm-legacy-profile-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "tests").mkdir()

    def source(self, name: str = "test_assignment.py", text: str = "def test_one(): pass\n") -> Path:
        path = self.root / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def cli(self, *args: str) -> tuple[int, dict]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(runner, "PROJECT", self.root), redirect_stdout(stdout), redirect_stderr(stderr):
            code = runner.main(list(args))
        self.assertEqual(stderr.getvalue(), "")
        return code, json.loads(stdout.getvalue())

    def test_default_is_inventory_not_execution(self) -> None:
        self.source()
        code, report = self.cli()
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "inventory_complete_not_execution")
        self.assertEqual(report["tests_executed"], 0)
        self.assertFalse(report["modules"][0]["execution_authorized"])
        self.assertEqual(report["plan"][0]["blocker"], "execution_review_pending")

    def test_all_explicit_profiles_have_resources_and_no_duplicates(self) -> None:
        names = [name for group in runner.PROFILE_GROUPS.values() for name in group]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(runner.PROFILE_GROUPS), set(runner.PROFILE_RESOURCES))
        self.assertTrue(all(runner.PROFILE_RESOURCES.values()))
        self.assertEqual(runner.PROFILES["tests.test_workspace_access"], "python_child_and_sqlite")
        self.assertEqual(runner.PROFILES["tests.test_task_operations"], "synthetic_legacy_sqlite")

    def test_inventory_does_not_execute_top_level_or_imports(self) -> None:
        self.source(text="import backend.main\nimport torch\nraise RuntimeError('DO_NOT_EXECUTE')\n")
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"backend", "torch", "tests"}:
                raise AssertionError("Application import attempted")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            code, report = self.cli("--inventory-only")
        self.assertEqual(code, 0)
        self.assertEqual(report["modules"][0]["imports"], ["backend.main", "torch"])

    def test_execute_refuses_even_explicit_profile(self) -> None:
        self.source(text="raise RuntimeError('MUST_NOT_RUN')\n")
        code, report = self.cli("--execute", "--module", "tests.test_assignment")
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "execution_refused")
        self.assertEqual(report["tests_executed"], 0)

    def test_execute_without_exact_selection_refuses(self) -> None:
        self.source()
        code, report = self.cli("--execute")
        self.assertEqual(code, 2)
        self.assertTrue(report["execution_request_requires_exact_modules"])

    def test_unknown_new_module_is_fail_closed(self) -> None:
        self.source("test_new_contract.py")
        code, report = self.cli()
        self.assertEqual(code, 0)  # a valid inventory, NOT an execution approval
        self.assertEqual(report["modules"][0]["profile"], "unprofiled")
        self.assertEqual(report["plan"][0]["blocker"], "unprofiled")
        self.assertFalse(report["modules"][0]["execution_authorized"])

    def test_missing_exact_module_has_nonzero_exit(self) -> None:
        self.source()
        code, report = self.cli("--module", "tests.test_missing")
        self.assertEqual(code, 1)
        self.assertEqual(report["plan"][0]["blocker"], "module_not_found")

    def test_patterns_paths_and_duplicates_refused_before_reads(self) -> None:
        for selection in (("tests.test_*",), ("../test_secret",), ("tests.test_a:TestCase",),
                          ("tests.test_a", "tests.test_a"), ("tests.test_a.py",)):
            with self.subTest(selection=selection), patch.object(runner, "snapshot") as snapshot:
                with self.assertRaises(runner.InventoryError):
                    runner.inventory(self.root, selection)
                snapshot.assert_not_called()

    def test_exact_selection_does_not_expand_dependency_execution(self) -> None:
        self.source(text="from tests.test_other import Fixture\ndef test_one(): pass\n")
        self.source("test_other.py")
        code, report = self.cli("--module", "tests.test_assignment")
        self.assertEqual(code, 0)
        self.assertEqual([item["module"] for item in report["plan"]], ["tests.test_assignment"])
        self.assertEqual(report["module_count"], 2)

    def test_declarations_are_not_runtime_discovery(self) -> None:
        self.source(text="""class Base:
    def test_base(self): pass
class Child(Base): pass
def load_tests(loader, tests, pattern):
    raise RuntimeError('never called')
async def test_async(): pass
""")
        code, report = self.cli()
        self.assertEqual(code, 0)
        self.assertEqual(report["modules"][0]["declarations_not_runtime_cases"], 2)
        self.assertIn("custom_discovery", report["modules"][0]["resource_signals"])
        self.assertTrue(report["declaration_count_is_not_discovery"])

    def test_resource_hints_do_not_grant_permissions(self) -> None:
        self.source(text="import subprocess as sp\nfrom sqlite3 import connect as c\nimport backend.providers.asr\n")
        _, report = self.cli()
        row = report["modules"][0]
        self.assertEqual(row["resource_signals"], ["model_or_provider", "native_process", "sqlite"])
        self.assertFalse(row["execution_authorized"])
        self.assertTrue(report["resource_signals_are_incomplete_not_permissions"])

    def test_syntax_error_does_not_echo_source(self) -> None:
        self.source(text="VERY_PRIVATE_SENTINEL = (\n")
        code, report = self.cli()
        self.assertEqual(code, 1)
        self.assertEqual(report["modules"][0]["parse_status"], "invalid_source")
        self.assertNotIn("VERY_PRIVATE_SENTINEL", json.dumps(report))

    def test_invalid_cli_never_echoes_unknown_arguments(self) -> None:
        code, report = self.cli("--private-token=VERY_PRIVATE_SENTINEL")
        self.assertEqual(code, 2)
        self.assertEqual(report["code"], "invalid_arguments")
        self.assertNotIn("VERY_PRIVATE_SENTINEL", json.dumps(report))

    def test_io_error_message_is_not_reported(self) -> None:
        with patch.object(runner, "snapshot", side_effect=OSError("VERY_PRIVATE_SENTINEL")):
            code, report = self.cli()
        self.assertEqual(code, 2)
        self.assertEqual(report["code"], "source_inspection_failed")
        self.assertNotIn("VERY_PRIVATE_SENTINEL", json.dumps(report))

    def test_source_drift_add_remove_and_change(self) -> None:
        before = {"tests/test_assignment.py": b"def test_a(): pass\n", "tests/helper.py": b""}
        after = {"tests/test_assignment.py": b"def test_b(): pass\n", "tests/new.py": b""}
        with patch.object(runner, "snapshot", side_effect=[before, after]):
            report, code = runner.inventory(self.root)
        self.assertEqual(code, 1)
        self.assertEqual(report["source_drift"], sorted(before.keys() | after.keys()))

    def test_stable_hashes_include_helpers_and_package_initializers(self) -> None:
        self.source()
        self.source("helper.py", "raise RuntimeError('not imported')\n")
        self.source("__init__.py", "raise RuntimeError('not imported')\n")
        _, report = self.cli()
        self.assertEqual(len(report["source_sha256"]), 3)
        self.assertEqual(report["module_count"], 1)
        self.assertEqual(report["source_drift"], [])

    def test_nested_modules_are_inventoried_without_import(self) -> None:
        self.source("nested/test_extra.py", "raise RuntimeError('not imported')\n")
        _, report = self.cli()
        self.assertEqual(report["modules"][0]["module"], "tests.nested.test_extra")
        self.assertEqual(report["modules"][0]["profile"], "unprofiled")

    def test_non_python_fixture_contents_are_never_opened(self) -> None:
        self.source()
        self.source("cases/opaque.json", "not source")
        original = Path.open
        opened: list[Path] = []

        def tracked(path, *args, **kwargs):
            opened.append(path)
            self.assertEqual(args, ("rb",))
            self.assertEqual(path.suffix, ".py")
            return original(path, *args, **kwargs)

        with patch.object(Path, "open", new=tracked):
            _, report = self.cli()
        self.assertEqual(len(opened), 2)
        self.assertEqual(report["module_count"], 1)

    def test_private_directory_refuses_without_descending(self) -> None:
        self.source("data/test_secret.py", "raise RuntimeError('secret')\n")
        original = os.scandir
        scanned: list[Path] = []

        def tracked(path):
            scanned.append(Path(path))
            return original(path)

        with patch.object(os, "scandir", side_effect=tracked):
            code, report = self.cli()
        self.assertEqual(code, 2)
        self.assertEqual(report["code"], "unexpected_source_boundary")
        self.assertEqual(scanned, [self.root / "tests"])

    def test_link_and_windows_reparse_rejected(self) -> None:
        path = self.source()
        original = Path.lstat
        for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFREG, 0x400)):
            def injected(item, *args, **kwargs):
                if item == path:
                    return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
                return original(item, *args, **kwargs)
            with patch.object(Path, "lstat", new=injected):
                with self.assertRaises(runner.InventoryError):
                    runner.plain_path(path, directory=False)

    def test_source_size_and_count_limits(self) -> None:
        self.source()
        with patch.object(runner, "MAX_SOURCE_BYTES", 2):
            code, report = self.cli()
        self.assertEqual((code, report["code"]), (2, "source_size_limit"))
        with patch.object(runner, "MAX_SOURCE_FILES", 0):
            code, report = self.cli()
        self.assertEqual((code, report["code"]), (2, "source_count_limit"))

    def test_hardlinked_sources_refused_before_open(self) -> None:
        # Both ends are disposable synthetic fixtures, never repository data.
        for name, excluded in (
            ("test_assignment.py", "tests/cases/opaque.json"),
            ("helper.py", "data/opaque.bin"),
            ("__init__.py", ".env"),
            ("nested/test_extra.py", "tests/__pycache__/opaque.bin"),
        ):
            with self.subTest(source=name):
                target = self.root / excluded
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"SYNTHETIC_EXCLUDED_BYTES = True\n")
                source = self.root / "tests" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                os.link(target, source)
                try:
                    source_info, target_info = source.lstat(), target.lstat()
                    self.assertTrue(stat.S_ISREG(source_info.st_mode))
                    self.assertEqual(source_info.st_nlink, 2)
                    self.assertEqual(target_info.st_nlink, 2)
                    self.assertEqual((source_info.st_dev, source_info.st_ino),
                                     (target_info.st_dev, target_info.st_ino))
                    with patch.object(Path, "open", side_effect=AssertionError("Must reject before open")) as opened:
                        code, report = self.cli()
                    opened.assert_not_called()
                    self.assertEqual(report, {"status": "inventory_refused",
                                              "code": "source_indirection", "tests_executed": 0})
                    self.assertEqual(code, 2)
                finally:
                    source.unlink()
                    target.unlink()

    def test_regular_source_requires_exactly_one_actual_link(self) -> None:
        source = self.source()
        original = Path.lstat
        for count in (0, 2):
            def injected(path, *args, **kwargs):
                info = original(path, *args, **kwargs)
                if path == source:
                    return SimpleNamespace(st_mode=info.st_mode, st_nlink=count,
                                           st_file_attributes=getattr(info, "st_file_attributes", 0))
                return info

            with self.subTest(nlink=count), patch.object(Path, "lstat", new=injected):
                with patch.object(Path, "open", side_effect=AssertionError("Must reject before open")) as opened:
                    code, report = self.cli()
                opened.assert_not_called()
                self.assertEqual((code, report["code"]), (2, "source_indirection"))

    def test_directory_link_counts_do_not_restrict_source_inventory(self) -> None:
        source = self.source("nested/test_assignment.py")
        original = Path.lstat
        for count in (0, 2, 3):
            def injected(path, *args, **kwargs):
                info = original(path, *args, **kwargs)
                if stat.S_ISDIR(info.st_mode):
                    return SimpleNamespace(st_mode=info.st_mode, st_nlink=count,
                                           st_file_attributes=getattr(info, "st_file_attributes", 0))
                return info

            with self.subTest(nlink=count), patch.object(Path, "lstat", new=injected):
                self.assertEqual(source.lstat().st_nlink, 1)
                code, report = self.cli()
                self.assertEqual(code, 0)
                self.assertEqual(report["source_files_checked"], 1)
                self.assertEqual(report["module_count"], 1)
                self.assertEqual(report["tests_executed"], 0)

    def test_cached_direntry_link_count_is_not_used_for_regular_source(self) -> None:
        source = self.source()
        actual = source.lstat()
        self.assertEqual(actual.st_nlink, 1)
        cached = SimpleNamespace(st_mode=actual.st_mode, st_nlink=0,
                                 st_file_attributes=getattr(actual, "st_file_attributes", 0))
        entry = SimpleNamespace(name=source.name, path=str(source),
                                stat=lambda **kwargs: cached)
        with patch.object(os, "scandir") as scan, patch.object(Path, "lstat", autospec=True,
                                                              side_effect=Path.lstat) as actual_stat:
            scan.return_value.__enter__.side_effect = lambda: iter([entry])
            code, report = self.cli()
        self.assertEqual(code, 0)
        self.assertEqual(report["source_files_checked"], 1)
        self.assertEqual(report["tests_executed"], 0)
        self.assertIn(unittest.mock.call(source), actual_stat.call_args_list)

    def test_summary_and_mutually_exclusive_modes(self) -> None:
        self.source()
        code, report = self.cli("--summary")
        self.assertEqual(code, 0)
        self.assertNotIn("modules", report)
        self.assertNotIn("source_sha256", report)
        code, report = self.cli("--inventory-only", "--execute")
        self.assertEqual((code, report["code"]), (2, "invalid_arguments"))

    def test_utility_imports_are_stdlib_only(self) -> None:
        import ast
        tree = ast.parse(_path.read_bytes())
        imports = {node.module.split(".")[0] for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module}
        imports.update(alias.name.split(".")[0] for node in ast.walk(tree)
                       if isinstance(node, ast.Import) for alias in node.names)
        self.assertLessEqual(imports, sys.stdlib_module_names)


class _QuietResult(unittest.TestResult):
    """Never call unittest's error/skip formatter, including subtest parameters."""
    def addError(self, test, err):
        self.errors.append((None, "redacted"))

    def addFailure(self, test, err):
        self.failures.append((None, "redacted"))

    def addSkip(self, test, reason):
        self.skipped.append((None, "redacted"))

    def addSubTest(self, test, subtest, err):
        if err is not None:
            if issubclass(err[0], test.failureException):
                self.addFailure(test, err)
            else:
                self.addError(test, err)


if __name__ == "__main__":
    result = _QuietResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(LegacyProfileTests).run(result)
    print(json.dumps({"scope": "new_inventory_tool_only", "tests": result.testsRun,
                      "failures": len(result.failures), "errors": len(result.errors),
                      "skipped": len(result.skipped), "legacy_tests_executed": 0}))
    raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)