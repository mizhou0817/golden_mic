"""Pure probe contracts only; default V2 discovery never opts into inference."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import local_speaker_runtime_smoke as probe


class SpeakerRuntimeProbeTests(unittest.TestCase):
    def test_hash_and_size_fail_closed(self):
        raw = b"synthetic-not-a-weight"
        expected = hashlib.sha256(raw).hexdigest()
        self.assertEqual(probe.verify_bytes(raw, len(raw), expected), expected)
        for data, size, value in ((raw + b"x", len(raw), expected),
                                  (raw, len(raw) + 1, expected),
                                  (raw, len(raw), "0" * 64)):
            with self.assertRaises(probe.ProbeFailure):
                probe.verify_bytes(data, size, value)

    def test_arguments_require_explicit_test_optin(self):
        for argv in ([], ["--license-reviewed"], ["--run-test-only", "--model", "other"],
                     ["--run-test-only", "--test-only-child", "other"]):
            with self.assertRaises(probe.ProbeFailure):
                probe.arguments(argv)
        self.assertTrue(probe.arguments(["--run-test-only"]).run_test_only)

    def test_import_is_inert(self):
        before = set(sys.modules)
        spec = importlib.util.spec_from_file_location("_speaker_probe_import_contract", Path(probe.__file__))
        self.assertIsNotNone(spec)
        assert spec is not None and spec.loader is not None
        with patch.object(probe.subprocess, "run", side_effect=AssertionError), \
             patch.object(probe.tempfile, "mkdtemp", side_effect=AssertionError):
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        additions = set(sys.modules) - before
        self.assertFalse(any(n.split(".")[0] in {"numpy", "sherpa_onnx", "backend"} for n in additions))

    def test_claims_never_activate_or_approve(self):
        result = probe.claims()
        for key in ("production_activation", "real_speech_quality", "approved_corpus",
                    "license_gate_bypassed_for_test", "os_sandbox",
                    "application_base_compatibility_certified"):
            self.assertIs(result[key], False)

    def test_environment_drops_secrets_and_acknowledgments(self):
        with patch.dict(probe.os.environ, {"PROVIDER_TOKEN": "synthetic",
                                         "PYTHONPATH": "synthetic", "LICENSE_REVIEWED": "true"}):
            environment = probe.clean_environment(Path("synthetic-owned-temp"))
        self.assertNotIn("PROVIDER_TOKEN", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("LICENSE_REVIEWED", environment)
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")

    def test_lock_hashes_match_fixed_wheels(self):
        lock = (probe.PROJECT / "deploy/local-speech-speaker-windows-cp311.lock").read_text(encoding="ascii")
        pins = [line for line in lock.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(pins), 3)
        for _, _, expected in probe.WHEELS:
            self.assertEqual(sum(line.endswith("--hash=sha256:" + expected) for line in pins), 1)

    def test_bypass_is_scoped_to_only_require(self):
        tree = ast.parse(Path(probe.__file__).read_text(encoding="utf-8"))
        patches = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                   and node.func.value.id == "patch"]
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].func.attr, "object")
        self.assertEqual(patches[0].args[1].value, "_require")
        constructors = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and node.func.attr == "LocalSpeakerEncoder"]
        self.assertEqual(len(constructors), 2)
        for node in constructors:
            self.assertTrue(all(k.arg == "license_reviewed" and isinstance(k.value, ast.Constant)
                                and k.value.value is False for k in node.keywords))


if __name__ == "__main__":
    unittest.main()