"""Guarded TEMP-only integrity integration; synthetic bytes are NOT models."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from contextlib import ExitStack
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from backend import readiness
from backend.config import Settings
from backend.providers import local_speech
from deploy import verify_local_speech_bundle as validator
from tests import test_v2_model_bundle as fixtures
from tests.test_v2_speech_preflight import _NoAcousticImports  # pyright: ignore[reportPrivateUsage] -- Reuse import guard.


class SpeechBundleReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        # Composition, not inheritance: do not rediscover the fixture's tests.
        self.bundle = fixtures.ModelBundleTests()
        self.addCleanup(self.bundle.doCleanups)
        self.bundle.setUp()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue] -- BaseSettings runtime option.
            app_env="test", local_speech_required=True,
            local_speaker_model_path=self.bundle.root / "speaker/model.onnx",
            local_alignment_model_path=self.bundle.root / "ctc",
            local_speech_license_reviewed=True,
        )
        # Exercise the production branch without unrelated production Settings constraints.
        self.settings.app_env = "production"
        for name in ("validate_frontend_dist", "validate_font_asset", "_check_data_directories",
                     "_check_disk_capacity", "_check_media_capabilities", "validate_no_legacy_environment"):
            self.stack.enter_context(patch.object(readiness, name, return_value=None))
        self.stack.enter_context(patch.object(readiness, "_validate_pipeline_configuration", new=AsyncMock()))
        self.probe = self.stack.enter_context(patch.object(
            local_speech.importlib.util, "find_spec", side_effect=self.runtime_present))
        self.speaker = self.stack.enter_context(patch.object(
            local_speech, "LocalSpeakerEncoder", side_effect=AssertionError("inference_forbidden")))
        self.aligner = self.stack.enter_context(patch.object(
            local_speech, "LocalForcedAligner", side_effect=AssertionError("inference_forbidden")))
        finder = _NoAcousticImports()
        sys.meta_path.insert(0, finder)
        self.stack.callback(sys.meta_path.remove, finder)
        self.native_before = self.native_modules()

    @staticmethod
    def runtime_present(name: str) -> ModuleSpec:
        return ModuleSpec(name, loader=None)

    @staticmethod
    def native_modules() -> set[str]:
        return {name for name in sys.modules if name.split(".")[0] in {
            "torch", "transformers", "sherpa_onnx", "onnxruntime", "safetensors"}}

    def configured(self) -> None:
        setattr(self.settings, "local_speech_bundle_manifest_path", self.bundle.manifest_path)

    def run_preflight(self) -> readiness.ReadinessReport:
        report = asyncio.run(readiness.run_full_preflight(self.settings))
        self.speaker.assert_not_called()
        self.aligner.assert_not_called()
        self.assertEqual(self.native_before, self.native_modules())
        payload = json.dumps(report.as_dict())
        for private in (str(self.bundle.home), "example.invalid", "source_uri", "private-marker", "models"):
            self.assertNotIn(private, payload)
        if report.local_speech:
            for state in report.local_speech.values():
                self.assertFalse(state.inference_verified)
        return report

    def blocked(self) -> readiness.ReadinessReport:
        report = self.run_preflight()
        self.assertIn("local_speech_bundle_integrity", report.checks)
        self.assertFalse(report.checks["local_speech_bundle_integrity"])
        self.assertFalse(report.ready)
        codes = [error for error in report.errors if error.startswith("local_speech_bundle_integrity:")]
        self.assertEqual(len(codes), 1)
        self.assertRegex(codes[0], r"^local_speech_bundle_integrity: [a-z_]+$")
        return report

    def test_production_required_missing_manifest_blocks(self) -> None:
        # Baseline regression: metadata probes pass, but the integrity check is absent.
        report = self.blocked()
        self.assertTrue(report.checks["local_speech_speaker_embedding"])
        self.assertTrue(report.checks["local_speech_forced_alignment"])

    def test_external_manifest_integrity_only_success_read_only(self) -> None:
        self.configured()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.bundle.home.rglob("*") if p.is_file()}
        with patch.object(validator, "_read", wraps=getattr(validator, "_read")) as reads:
            report = self.run_preflight()
        self.assertTrue(report.ready)
        self.assertTrue(report.checks["local_speech_bundle_integrity"])
        self.assertEqual(reads.call_count, 8)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                 for p in self.bundle.home.rglob("*") if p.is_file()})

    def test_root_manifest_success(self) -> None:
        self.bundle.manifest_path.unlink()
        self.bundle.manifest_path = self.bundle.root / validator.MANIFEST_NAME
        self.bundle.save()
        self.configured()
        self.assertTrue(self.run_preflight().ready)

    def test_missing_manifest_file(self) -> None:
        self.configured()
        self.bundle.manifest_path.unlink()
        self.blocked()

    def test_empty_and_fake_manifests(self) -> None:
        self.configured()
        for raw in (b"", b"{}", b"private-marker", b'{"status":"downloaded"}'):
            with self.subTest():
                self.bundle.manifest_path.write_bytes(raw)
                self.blocked()

    def test_empty_ctc_directory(self) -> None:
        self.configured()
        for path in (self.bundle.root / "ctc").iterdir():
            path.unlink()
        self.blocked()

    def test_same_size_weight_tamper_rehashed_on_every_preflight(self) -> None:
        self.configured()
        self.assertTrue(self.run_preflight().ready)
        path = self.bundle.root / "ctc/model.safetensors"
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        self.blocked()

    def test_speaker_tamper(self) -> None:
        self.configured()
        (self.bundle.root / "speaker/model.onnx").write_bytes(b"fake")
        self.blocked()

    def test_manifest_digest_tamper(self) -> None:
        self.configured()
        cast(Any, self.bundle).manifest["models"]["speaker"]["files"][0]["sha256"] = "0" * 64
        self.bundle.save()
        self.blocked()

    def test_unlisted_file(self) -> None:
        self.configured()
        (self.bundle.root / "ctc/extra.json").write_bytes(b"{}")
        self.blocked()

    def test_pickle_file(self) -> None:
        self.configured()
        (self.bundle.root / "ctc/pytorch_model.bin").write_bytes(b"not executable pickle")
        self.blocked()

    def test_hardlink(self) -> None:
        self.configured()
        os.link(self.bundle.root / "speaker/model.onnx", self.bundle.home / "hardlink.onnx")
        self.blocked()

    def test_directory_link(self) -> None:
        self.configured()
        alias = self.bundle.home / "alias"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(self.bundle.root / "ctc"), str(alias))
            self.addCleanup(alias.rmdir)
        else:
            alias.symlink_to(self.bundle.root / "ctc", target_is_directory=True)
            self.addCleanup(alias.unlink)
        self.settings.local_alignment_model_path = alias
        with patch.object(validator, "_read", side_effect=AssertionError("bytes_forbidden")) as reads:
            self.blocked()
        reads.assert_not_called()

    def test_wrong_alignment_bundle_before_bytes(self) -> None:
        self.configured()
        other = self.bundle.home / "other/ctc"
        other.mkdir(parents=True)
        self.settings.local_alignment_model_path = other
        with patch.object(validator, "_read", side_effect=AssertionError("bytes_forbidden")) as reads:
            self.blocked()
        reads.assert_not_called()

    def test_wrong_speaker_name_before_bytes(self) -> None:
        self.configured()
        other = self.bundle.root / "speaker/other.onnx"
        other.write_bytes(b"fake")
        self.settings.local_speaker_model_path = other
        with patch.object(validator, "_read", side_effect=AssertionError("bytes_forbidden")) as reads:
            self.blocked()
        reads.assert_not_called()

    def test_parent_traversal_alias_before_bytes(self) -> None:
        self.configured()
        self.settings.local_alignment_model_path = self.bundle.root / "ctc/../ctc"
        with patch.object(validator, "_read", side_effect=AssertionError("bytes_forbidden")) as reads:
            self.blocked()
        reads.assert_not_called()

    def test_missing_configured_paths(self) -> None:
        self.configured()
        for field in ("local_speaker_model_path", "local_alignment_model_path"):
            original = getattr(self.settings, field)
            with self.subTest():
                setattr(self.settings, field, None)
                self.blocked()
            setattr(self.settings, field, original)

    def test_relative_manifest_before_bytes(self) -> None:
        setattr(self.settings, "local_speech_bundle_manifest_path", Path("relative.json"))
        with patch.object(validator, "_read", side_effect=AssertionError("bytes_forbidden")) as reads:
            self.blocked()
        reads.assert_not_called()

    def test_manifest_wrong_inside_location(self) -> None:
        self.bundle.manifest_path = self.bundle.root / "ctc/manifest.json"
        self.bundle.save()
        self.configured()
        self.blocked()

    def test_manifest_from_other_bundle(self) -> None:
        self.configured()
        other = fixtures.ModelBundleTests()
        self.addCleanup(other.doCleanups)
        other.setUp()
        cast(Any, other).put("speaker/model.onnx", b"different synthetic bytes")
        other.save()
        setattr(self.settings, "local_speech_bundle_manifest_path", other.manifest_path)
        self.blocked()

    def test_unreviewed_license_before_bytes(self) -> None:
        self.configured()
        self.settings.local_speech_license_reviewed = False
        with patch.object(validator, "_read", side_effect=AssertionError("bytes_forbidden")) as reads:
            report = self.blocked()
        reads.assert_not_called()
        self.assertFalse(report.checks["local_speech_speaker_embedding"])
        self.assertFalse(report.checks["local_speech_forced_alignment"])

    def test_private_verifier_exception_redacted(self) -> None:
        self.configured()
        with patch.object(validator, "verify_local_speech_bundle", side_effect=RuntimeError(
                "private-marker " + str(self.bundle.home) + ' {"models": "https://example.invalid"}')):
            self.blocked()

    def test_development_configured_required_strict(self) -> None:
        self.settings.app_env = "development"
        self.configured()
        self.assertTrue(self.run_preflight().ready)
        self.bundle.manifest_path.write_bytes(b"{}")
        self.blocked()

    def test_legacy_required_no_manifest_metadata_only(self) -> None:
        for env in ("test", "development"):
            self.settings.app_env = env
            with self.subTest(), patch.object(validator, "_read", side_effect=AssertionError("bytes_forbidden")) as reads:
                report = self.run_preflight()
                self.assertTrue(report.ready)
                self.assertNotIn("local_speech_bundle_integrity", report.checks)
            reads.assert_not_called()

    def test_required_false_ignores_configured_manifest_and_paths(self) -> None:
        self.configured()
        self.settings.local_speech_required = False
        with patch.object(Path, "open", side_effect=AssertionError("reads_forbidden")), \
                patch.object(validator, "verify_local_speech_bundle", side_effect=AssertionError("verifier_forbidden")) as verify:
            report = self.run_preflight()
        self.assertTrue(report.ready)
        self.assertIsNone(report.local_speech)
        self.assertNotIn("local_speech_bundle_integrity", report.checks)
        self.probe.assert_not_called()
        verify.assert_not_called()

    def test_blank_manifest_setting_is_none_and_defaults_optional(self) -> None:
        with patch.dict(os.environ, {"LOCAL_SPEECH_BUNDLE_MANIFEST_PATH": ""}):
            settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
        self.assertIsNone(getattr(settings, "local_speech_bundle_manifest_path"))
        self.assertIs(Settings.model_fields["local_speech_required"].default, False)
        self.assertIsNone(Settings.model_fields["local_speech_bundle_manifest_path"].default)

    def test_templates_inventory_and_blank_manifest(self) -> None:
        from tests.test_v2_config_template import TEMPLATES, parse_template, source_fields, template_text
        fields = source_fields()
        for name in TEMPLATES:
            values = parse_template(template_text(name), fields)
            self.assertEqual(values["LOCAL_SPEECH_BUNDLE_MANIFEST_PATH"], "")
