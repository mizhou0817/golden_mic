"""Offline CLI contracts: synthetic EnvironmentFiles, no exec or services."""
from __future__ import annotations

import io
import os
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from deploy import run_with_environment as wrapper


ROOT = Path(__file__).resolve().parents[1]


class EnvironmentCommandTests(unittest.TestCase):
    def test_wrapper_options_before_or_after_environment_file(self):
        for args in (
            ["--cwd", "/safe work", "--set", "KEY=value with spaces", "service.env"],
            ["service.env", "--cwd", "/safe work", "--set", "KEY=value with spaces"],
        ):
            with self.subTest(args=args):
                value = wrapper.parse_arguments([*args, "--", "python", "-m", "backend.preflight"])
                self.assertEqual(value.environment_file, Path("service.env"))
                self.assertEqual(value.cwd, Path("/safe work"))
                self.assertEqual(value.overrides, ["KEY=value with spaces"])
                self.assertEqual(value.command, ["python", "-m", "backend.preflight"])

    def test_child_options_and_separator_are_never_reparsed(self):
        command = ["python", "--cwd", "child cwd", "--set", "CHILD=1", "--", "literal"]
        value = wrapper.parse_arguments(["service.env", "--", *command])
        self.assertEqual(value.command, command)
        self.assertIsNone(value.cwd)
        self.assertEqual(value.overrides, [])

    def test_missing_command_or_separator_and_unknown_option_refused_before_io(self):
        for args in ([], ["service.env"], ["service.env", "--"],
                     ["service.env", "--bad", "x", "--", "python"],
                     ["service.env", "python", "-m", "backend.preflight"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), \
                    patch.object(wrapper, "parse_environment_file", side_effect=AssertionError("no file read")), \
                    patch.object(wrapper.os, "execvpe", side_effect=AssertionError("no execution")), \
                    self.assertRaises(SystemExit) as caught:
                wrapper.main(args)
            self.assertEqual(caught.exception.code, 2)

    def test_actual_deployment_calls_preserve_all_options(self):
        for name, variable in (("deploy/deploy_release.sh", "TARGET"),
                               ("deploy/validate_target_host.sh", "CURRENT")):
            text = (ROOT / name).read_text(encoding="utf-8")
            lines = text.splitlines()
            start = next(i for i, line in enumerate(lines) if "run_with_environment.py" in line)
            block = []
            for line in lines[start:]:
                block.append(line.strip().removesuffix("\\"))
                if not line.endswith("\\"):
                    break
            words = shlex.split(" ".join(block))
            index = next(i for i, word in enumerate(words) if word.endswith("/deploy/run_with_environment.py"))
            args = wrapper.parse_arguments(words[index + 1:])
            self.assertEqual(args.cwd, Path("/srv/golden-mic-data/tmp"))
            self.assertEqual(args.overrides, [f"PYTHONPATH=${{{variable}}}", "HOME=/nonexistent",
                                             "TMPDIR=/srv/golden-mic-data/tmp"])
            self.assertEqual(args.command, [f"${{{variable}}}/.venv/bin/python", "-m", "backend.preflight"])
            self.assertLess(words.index("--cwd"), words.index(args.environment_file.as_posix()))

    def test_main_applies_overrides_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / "synthetic.env"
            environment.write_text("BASE=one\nKEY='literal $HOME $(not-a-command)'\n", encoding="utf-8")
            with patch.dict(os.environ, {"INHERITED": "kept"}, clear=True), \
                    patch.object(wrapper.os, "chdir") as chdir, \
                    patch.object(wrapper.os, "execvpe") as execute:
                self.assertEqual(wrapper.main(["--cwd", str(root), "--set", "BASE=two", "--set", "EMPTY=",
                                               str(environment), "--", "python", "-c", "literal child"]), 127)
                chdir.assert_called_once_with(root)
                execute.assert_called_once_with("python", ["python", "-c", "literal child"],
                                                {"INHERITED": "kept", "BASE": "two", "EMPTY": "",
                                                 "KEY": "literal $HOME $(not-a-command)"})

    def test_invalid_override_never_changes_directory_or_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "synthetic.env"
            environment.write_text("BASE=one\n", encoding="utf-8")
            with patch.object(wrapper.os, "chdir") as chdir, patch.object(wrapper.os, "execvpe") as execute, \
                    self.assertRaises(ValueError):
                wrapper.main(["--cwd", directory, "--set", "NOT VALID=value", str(environment), "--", "python"])
            chdir.assert_not_called()
            execute.assert_not_called()