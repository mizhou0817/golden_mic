"""Offline prerequisite checks, not model compatibility or acoustic acceptance.

Run under the shared guarded TEMP validator. Runtime discovery is controlled;
the real local_speech_readiness utility still checks actual TEMP path types.
"""
from __future__ import annotations

import asyncio
import importlib.abc
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import AsyncMock, patch

from backend import readiness
from backend.config import Settings
from backend.providers import local_speech


class _NoAcousticImports(importlib.abc.MetaPathFinder):
    def find_spec(
        self, fullname: str, path: object = None, target: object = None,
    ) -> ModuleSpec | None:
        if fullname.split(".")[0] in {"torch", "transformers", "sherpa_onnx"}:
            raise AssertionError("acoustic_runtime_import_forbidden")
        return None


class SpeechPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="speech-preflight-")))
        self.speaker = self.root / "private-speaker.onnx"
        self.speaker.write_bytes(b"not compatible weights; existence-only fixture")
        self.alignment = self.root / "private-ctc"
        self.alignment.mkdir()  # Empty is deliberately NOT a usable CTC model.
        self.settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue] -- BaseSettings runtime option.
            app_env="test", data_dir=self.root / "tasks",
            asr_cache_dir=self.root / "asr", local_speech_required=True,
            local_speaker_model_path=self.speaker,
            local_alignment_model_path=self.alignment,
            local_speech_license_reviewed=True,
        )
        for name in (
            "validate_frontend_dist", "validate_font_asset", "_check_data_directories",
            "_check_disk_capacity", "_check_media_capabilities", "validate_no_legacy_environment",
        ):
            self.stack.enter_context(patch.object(readiness, name, return_value=None))
        self.provider_check = self.stack.enter_context(patch.object(
            readiness, "_validate_pipeline_configuration", new=AsyncMock(return_value=None),
        ))
        self.runtime_probe = self.stack.enter_context(patch.object(
            local_speech.importlib.util, "find_spec",
            side_effect=self.runtime_present,
        ))
        self.utility = self.stack.enter_context(patch.object(
            local_speech, "local_speech_readiness", wraps=local_speech.local_speech_readiness,
        ))
        self.speaker_constructor = self.stack.enter_context(patch.object(
            local_speech, "LocalSpeakerEncoder", side_effect=AssertionError("inference_forbidden"),
        ))
        self.aligner_constructor = self.stack.enter_context(patch.object(
            local_speech, "LocalForcedAligner", side_effect=AssertionError("inference_forbidden"),
        ))
        finder = _NoAcousticImports()
        sys.meta_path.insert(0, finder)
        self.stack.callback(sys.meta_path.remove, finder)
        self.runtime_modules = {name for name in sys.modules if name.split(".")[0] in {
            "torch", "transformers", "sherpa_onnx",
        }}

    def run_preflight(self) -> readiness.ReadinessReport:
        report = asyncio.run(readiness.run_full_preflight(self.settings))
        self.speaker_constructor.assert_not_called()
        self.aligner_constructor.assert_not_called()
        self.assertEqual(self.runtime_modules, {name for name in sys.modules if name.split(".")[0] in {
            "torch", "transformers", "sherpa_onnx",
        }})
        payload = report.as_dict()
        encoded = json.dumps(payload)
        for private in (str(self.root), str(self.speaker), str(self.alignment), "private-speaker", "private-ctc"):
            self.assertNotIn(private, encoded)
        return report

    def assert_blocked(self, report: readiness.ReadinessReport, capability: str, code: str) -> None:
        self.assertFalse(report.ready)
        self.assertFalse(report.checks["local_speech_" + capability])
        self.assertIn("local_speech_" + capability + ": " + code, report.errors)

    @staticmethod
    def runtime_present(name: str) -> ModuleSpec:
        return ModuleSpec(name, loader=None)

    def test_required_missing_speaker_runtime(self) -> None:
        def probe(name: str) -> ModuleSpec | None:
            return None if name == "sherpa_onnx" else self.runtime_present(name)

        self.runtime_probe.side_effect = probe
        report = self.run_preflight()
        self.assert_blocked(report, "speaker_embedding", "missing_runtime:sherpa_onnx")
        self.assertTrue(report.checks["local_speech_forced_alignment"])

    def test_required_missing_each_alignment_runtime(self) -> None:
        for missing in ("torch", "transformers"):
            with self.subTest(runtime=missing):
                def probe(name: str) -> ModuleSpec | None:
                    return None if name == missing else self.runtime_present(name)

                self.runtime_probe.side_effect = probe
                report = self.run_preflight()
                self.assert_blocked(report, "forced_alignment", "missing_runtime:" + missing)
                self.assertTrue(report.checks["local_speech_speaker_embedding"])

    def test_required_missing_all_runtimes_checks_both_capabilities(self) -> None:
        self.runtime_probe.return_value = None
        self.runtime_probe.side_effect = None
        report = self.run_preflight()
        self.assert_blocked(report, "speaker_embedding", "missing_runtime:sherpa_onnx")
        self.assert_blocked(report, "forced_alignment", "missing_runtime:torch")
        self.assert_blocked(report, "forced_alignment", "missing_runtime:transformers")
        self.assertEqual(self.utility.call_count, 2)

    def test_required_missing_speaker_path(self) -> None:
        for path in (None, self.root / "private-absent.onnx", self.alignment):
            with self.subTest():
                self.settings.local_speaker_model_path = path
                report = self.run_preflight()
                self.assert_blocked(report, "speaker_embedding", "missing_local_speaker_onnx")
                self.assertTrue(report.checks["local_speech_forced_alignment"])

    def test_required_missing_alignment_path(self) -> None:
        for path in (None, self.root / "private-absent-ctc", self.speaker):
            with self.subTest():
                self.settings.local_alignment_model_path = path
                report = self.run_preflight()
                self.assert_blocked(report, "forced_alignment", "missing_local_ctc_model_and_tokenizer")
                self.assertTrue(report.checks["local_speech_speaker_embedding"])

    def test_required_unreviewed_license_blocks_both(self) -> None:
        self.settings.local_speech_license_reviewed = False
        report = self.run_preflight()
        for capability in ("speaker_embedding", "forced_alignment"):
            self.assert_blocked(report, capability, "model_license_and_language_not_reviewed")

    def test_prerequisites_ready_does_not_verify_inference_or_weight_compatibility(self) -> None:
        report = self.run_preflight()
        self.assertTrue(report.ready)
        self.assertEqual(report.errors, ())
        self.assertEqual(self.utility.call_count, 2)
        self.assertEqual([call.args for call in self.utility.call_args_list], [
            ("speaker_embedding", self.speaker), ("forced_alignment", self.alignment),
        ])
        self.assertEqual(report.as_dict()["local_speech"], {
            "speaker_embedding": {
                "available": True, "capability": "speaker_embedding", "blocked_prerequisites": (),
                "runtime_license": "Apache-2.0", "model_license_reviewed": True, "inference_verified": False,
            },
            "forced_alignment": {
                "available": True, "capability": "forced_alignment", "blocked_prerequisites": (),
                "runtime_license": "Apache-2.0 / BSD-3-Clause", "model_license_reviewed": True, "inference_verified": False,
            },
        })
        self.assertEqual(self.speaker.read_bytes(), b"not compatible weights; existence-only fixture")
        self.assertEqual(list(self.alignment.iterdir()), [])

    def test_false_optional_preserves_legacy_checks_and_never_probes(self) -> None:
        self.settings.local_speech_required = False
        self.settings.local_speaker_model_path = None
        self.settings.local_alignment_model_path = None
        self.settings.local_speech_license_reviewed = False
        self.utility.side_effect = AssertionError("optional_check_must_not_run")
        report = self.run_preflight()
        self.assertTrue(report.ready)
        self.assertEqual(report.errors, ())
        self.assertEqual(set(report.checks), {
            "frontend_dist", "font_asset", "data_directories", "disk_capacity",
            "media_capabilities", "provider_configuration",
        })
        self.assertEqual(set(report.as_dict()), {"ready", "checks", "errors"})
        self.utility.assert_not_called()

    def test_false_optional_does_not_hide_other_preflight_failure(self) -> None:
        self.settings.local_speech_required = False
        self.provider_check.side_effect = RuntimeError("provider_configuration_missing")
        report = self.run_preflight()
        self.assertFalse(report.ready)
        self.assertFalse(report.checks["provider_configuration"])
        self.utility.assert_not_called()

    def test_runtime_probe_exception_is_static_and_does_not_skip_alignment(self) -> None:
        private_error = "private-error-marker " + str(self.speaker)

        def probe(name: str) -> ModuleSpec:
            if name == "sherpa_onnx":
                raise OSError(private_error)
            return self.runtime_present(name)

        self.runtime_probe.side_effect = probe
        report = self.run_preflight()
        self.assert_blocked(report, "speaker_embedding", "prerequisite_check_failed")
        self.assertTrue(report.checks["local_speech_forced_alignment"])
        self.assertNotIn(private_error, json.dumps(report.as_dict()))
        self.assertNotIn("private-error-marker", json.dumps(report.as_dict()))

    def test_path_probe_exception_is_static(self) -> None:
        real_is_dir = Path.is_dir

        def probe(path: Path) -> bool:
            if path == self.alignment:
                raise PermissionError("private-path-error " + str(path))
            return real_is_dir(path)

        with patch.object(Path, "is_dir", new=probe):
            report = self.run_preflight()
        self.assert_blocked(report, "forced_alignment", "prerequisite_check_failed")
        self.assertTrue(report.checks["local_speech_speaker_embedding"])
        self.assertNotIn("private-path-error", json.dumps(report.as_dict()))

    def test_default_remains_optional_including_production_model_default(self) -> None:
        self.assertIs(Settings.model_fields["local_speech_required"].default, False)
        # No implicit production override: exercise the branch, not Settings validation.
        self.settings.app_env = "production"
        self.settings.local_speech_required = False
        report = self.run_preflight()
        self.assertTrue(report.ready)
        self.assertTrue(report.checks["legacy_environment"])
        self.utility.assert_not_called()

    def test_unknown_utility_error_codes_cannot_leak_private_text(self) -> None:
        # Defensive output-boundary test only; prerequisite tests above wrap
        # the REAL utility instead of replacing its readiness decision.
        self.utility.return_value = local_speech.LocalSpeechReadiness(
            available=False, capability="private-capability " + str(self.speaker),
            blocked_prerequisites=("private-blocked-code " + str(self.alignment),),
            runtime_license="private-license", model_license_reviewed=True,
            inference_verified=True,
        )
        report = self.run_preflight()
        for capability in ("speaker_embedding", "forced_alignment"):
            self.assert_blocked(report, capability, "prerequisite_check_failed")
        payload = json.dumps(report.as_dict())
        for value in ("private-blocked-code", "private-capability", "private-license"):
            self.assertNotIn(value, payload)
        self.assertNotIn('"inference_verified": true', payload)

    def test_production_template_explicitly_requires_speech_not_implicitly_ready(self) -> None:
        from deploy.run_with_environment import parse_environment_file

        values = parse_environment_file(
            Path(__file__).resolve().parents[1] / "deploy/golden-mic.env.production.example",
        )
        self.assertEqual(values["LOCAL_SPEECH_REQUIRED"], "true")
        self.assertEqual(values["LOCAL_SPEECH_LICENSE_REVIEWED"], "false")
        # Do not stat the template's operator-owned paths; use missing TEMP
        # paths and the exact opt-in/license values to prove gate behavior.
        self.settings.local_speech_required = values["LOCAL_SPEECH_REQUIRED"] == "true"
        self.settings.local_speech_license_reviewed = values["LOCAL_SPEECH_LICENSE_REVIEWED"] == "true"
        self.settings.local_speaker_model_path = self.root / "absent.onnx"
        self.settings.local_alignment_model_path = self.root / "absent-ctc"
        report = self.run_preflight()
        self.assert_blocked(report, "speaker_embedding", "missing_local_speaker_onnx")
        self.assert_blocked(report, "forced_alignment", "missing_local_ctc_model_and_tokenizer")
        for capability in ("speaker_embedding", "forced_alignment"):
            self.assert_blocked(report, capability, "model_license_and_language_not_reviewed")