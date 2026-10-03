"""Fresh TEMP-only synthetic bundles; ZERO real model weights or inference.

Default test_v2*.py discovery includes this module without runner changes.
No children/network/native ONNX parser/model runtimes/dependencies are needed.
Native symlink creation is attempted (Windows privilege availability is reported
in NATIVE_PATH_PROBES); no skip hides an unavailable native fixture. Reparse-bit
and symlink-mode rejection are additionally unconditional unit assertions.
"""
from __future__ import annotations

import builtins
import copy
import hashlib
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deploy import verify_local_speech_bundle as validator


NATIVE_PATH_PROBES: dict[str, str] = {}


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


class ModelBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gm-model-bundle-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / "bundle"
        (self.root / "ctc").mkdir(parents=True)
        (self.root / "speaker").mkdir()
        self.manifest_path = self.home / "actual-installed.json"
        self.manifest = {"schema_version": 1, "kind": "local-speech-bundle", "status": "installed", "models": {}}
        for role in ("speaker", "ctc"):
            self.manifest["models"][role] = {
                "format": "onnx" if role == "speaker" else "safetensors",
                "upstream_revision": "1" * 40,
                "license": {"identifier": "Apache-2.0", "acknowledged": True,
                            "source_uri": "https://example.invalid/synthetic/LICENSE"}, "files": []}
        self.config = {"model_type": "wav2vec2", "architectures": ["Wav2Vec2ForCTC"],
                       "conv_kernel": [10, 3, 3, 3, 3, 2, 2], "conv_stride": [5, 2, 2, 2, 2, 2, 2],
                       "conv_dim": [1] * 7, "hidden_size": 2, "vocab_size": 4, "pad_token_id": 0}
        self.preprocessor = {"sampling_rate": 16000, "feature_extractor_type": "Wav2Vec2FeatureExtractor",
                             "feature_size": 1, "do_normalize": True, "padding_value": 0.0}
        self.tokenizer = {"tokenizer_class": "Wav2Vec2CTCTokenizer", "processor_class": "Wav2Vec2Processor",
                          "pad_token": "<pad>", "unk_token": "<unk>", "word_delimiter_token": "|"}
        self.vocab = {"<pad>": 0, "<unk>": 1, "|": 2, "\u4e2d": 3}
        self.special = {"pad_token": "<pad>", "unk_token": "<unk>"}
        self.put("ctc/config.json", encoded(self.config))
        self.put("ctc/preprocessor_config.json", encoded(self.preprocessor))
        self.put("ctc/tokenizer_config.json", encoded(self.tokenizer))
        self.put("ctc/vocab.json", encoded(self.vocab))
        self.put("ctc/special_tokens_map.json", encoded(self.special))
        # Intentionally NOT a protobuf and NOT usable weights. Integrity is not inference.
        self.put("speaker/model.onnx", b"SYNTHETIC FAKE ONNX - NOT A MODEL")
        self.header = {"lm_head.weight": {"dtype": "F32", "shape": [4, 2], "data_offsets": [0, 32]},
                       "lm_head.bias": {"dtype": "F32", "shape": [4], "data_offsets": [32, 48]}}
        offset = 48
        for i, kernel in enumerate([10, 3, 3, 3, 3, 2, 2]):
            self.header[f"wav2vec2.feature_extractor.conv_layers.{i}.conv.weight"] = {
                "dtype": "F32", "shape": [1, 1, kernel], "data_offsets": [offset, offset + kernel * 4]}
            offset += kernel * 4
        self.payload = bytes(offset)
        self.tensor()
        self.save()

    def put(self, name, raw):
        (self.root / name).write_bytes(raw)
        role = name.split("/")[0]
        records = self.manifest["models"][role]["files"]
        records[:] = [record for record in records if record["path"] != name]
        records.append({"path": name, "bytes": len(raw), "sha256": sha(raw), "source_sha256": "2" * 64,
                        "source_uri": "https://example.invalid/synthetic/source"})

    def tensor(self, header=None, payload=None, raw_header=None):
        raw = encoded(self.header if header is None else header) if raw_header is None else raw_header
        raw += b" " * (-len(raw) % 8)
        self.put("ctc/model.safetensors", len(raw).to_bytes(8, "little") + raw
                 + (self.payload if payload is None else payload))

    def save(self):
        self.manifest_path.write_bytes(encoded(self.manifest))

    def verify(self):
        self.save()
        return validator.verify_local_speech_bundle(self.root, self.manifest_path)

    def reject(self, code=None, *, save=True):
        if save:
            self.save()
        with self.assertRaises(validator.BundleError) as raised:
            validator.verify_local_speech_bundle(self.root, self.manifest_path)
        text = str(raised.exception)
        self.assertRegex(text, r"^[a-z_]+$")
        self.assertNotIn(str(self.home), text)
        self.assertIsNone(raised.exception.__context__)
        if code:
            self.assertEqual(text, code)

    def test_positive_tiny_synthetic_is_integrity_not_inference(self):
        receipt = self.verify()
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["files_verified"], 7)
        self.assertEqual(receipt["tensor_count"], 9)
        self.assertEqual(receipt["vocabulary_entries"], 4)
        self.assertEqual(receipt["manifest_sha256"], sha(self.manifest_path.read_bytes()))
        self.assertEqual(receipt["bytes_verified"], sum(r["bytes"] for m in self.manifest["models"].values() for r in m["files"]))
        for key in ("inference_verified", "activation_authorized", "onnx_parsed", "auto_processor_load_verified",
                    "upstream_provenance_verified", "conversion_equivalence_verified"):
            self.assertIs(receipt[key], False)
        self.assertNotIn(str(self.home), json.dumps(receipt))

    def test_verification_read_only(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file()}
        validator.verify_local_speech_bundle(self.root, self.manifest_path)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.home.rglob("*") if p.is_file()})

    def test_manifest_inside_explicit_canonical_root_name(self):
        self.manifest_path.unlink()
        self.manifest_path = self.root / validator.MANIFEST_NAME
        self.assertTrue(self.verify()["ok"])

    def test_optional_license_and_processor_metadata(self):
        self.put("speaker/LICENSE", b"Synthetic license fixture; not upstream evidence")
        self.put("ctc/README.md", b"Synthetic tiny fixture; not real weights")
        self.put("ctc/processor_config.json", encoded({"processor_class": "Wav2Vec2Processor"}))
        self.assertEqual(self.verify()["files_verified"], 10)

    def test_decision_document_and_deployable_false_not_install_manifest(self):
        original = copy.deepcopy(self.manifest)
        for value in ({"schema_version": 1, "kind": "golden-mic-v2-model-selection-not-install-lock",
                       "speaker_embedding": {"deployable": False}, "forced_alignment": {"deployable": False}},
                      dict(original, deployable=False), dict(original, status="selected")):
            with self.subTest(kind=value.get("kind")):
                self.manifest = value
                self.reject()

    def test_schema_exact_keys_and_types(self):
        original = copy.deepcopy(self.manifest)
        for key, value in (("schema_version", True), ("schema_version", 2), ("kind", "decision"), ("models", []),
                           ("status", False), ("extra", True)):
            with self.subTest(key=key, value=value):
                self.manifest = dict(original, **{key: value})
                self.reject()
        self.manifest = original
        self.manifest["models"]["ctc"]["deployable"] = False
        self.reject("schema_keys")

    def test_license_ack_must_be_explicit_true_each_role(self):
        for role in ("speaker", "ctc"):
            for value in (False, None, 1, "true"):
                with self.subTest(role=role, value=value):
                    self.manifest["models"][role]["license"]["acknowledged"] = value
                    self.reject("license_acknowledgement")
            self.manifest["models"][role]["license"]["acknowledged"] = True

    def test_immutable_revision_and_source_digest_required(self):
        ctc = self.manifest["models"]["ctc"]
        for revision in ("main", "v1.0", "a" * 39, "A" * 40, None):
            ctc["upstream_revision"] = revision
            self.reject("upstream_revision")
        ctc["upstream_revision"] = "a" * 64
        self.assertTrue(self.verify()["ok"])
        for digest in (None, "", "f" * 63, "F" * 64):
            ctc["files"][0]["source_sha256"] = digest
            self.reject("digest_schema")

    def test_source_uris_no_credentials_queries_or_non_https(self):
        record = self.manifest["models"]["speaker"]["files"][0]
        for uri in ("http://example.invalid/a", "file:///private", "https://user:secret@example.invalid/a",
                    "https://example.invalid/a?token=secret", "https://example.invalid/a#x", "https://[bad",
                    "https://example.invalid:8080/a", "https://example.invalid/a\n"):
            with self.subTest(uri=uri):
                record["source_uri"] = uri
                self.reject()

    def test_relative_manifest_paths_and_aliases_rejected(self):
        record = self.manifest["models"]["speaker"]["files"][0]
        for path in ("../private", "/speaker/model.onnx", "C:/private", "speaker\\model.onnx", "speaker//model.onnx",
                     "speaker/./model.onnx", "speaker/../model.onnx", "Speaker/model.onnx", "speaker/MODEL.onnx",
                     "speaker/model.onnx.", "speaker/model.onnx ", "speaker/model.onnx:ads", "speaker/%2e%2e", "speaker/\u043cod el.onnx"):
            with self.subTest(path=path):
                record["path"] = path
                self.reject()

    def test_explicit_absolute_inputs_only_no_unc_or_devices(self):
        for path in ("bundle", "../bundle", "//server/share/bundle", "\\\\server\\share\\bundle",
                     "\\\\?\\C:\\bundle", str(self.root) + "/../bundle", str(self.root) + "/", str(self.root) + "/.",
                     str(self.home) + "/CON", str(self.home) + "/NUL.json", str(self.home) + "/COM1", str(self.home) + "/bad\nname"):
            with self.subTest(path=path), self.assertRaises(validator.BundleError):
                validator.verify_local_speech_bundle(path, self.manifest_path)
        with self.assertRaises(validator.BundleError):
            validator.verify_local_speech_bundle(self.root, "relative.json")

    def test_duplicate_missing_and_wrong_role_files(self):
        original = copy.deepcopy(self.manifest)
        speaker = self.manifest["models"]["speaker"]["files"]
        speaker.append(copy.deepcopy(speaker[0]))
        self.reject("file_duplicate_or_role")
        self.manifest = copy.deepcopy(original)
        self.manifest["models"]["ctc"]["files"].pop()
        self.reject("file_set")
        self.manifest = original
        self.manifest["models"]["speaker"]["files"][0]["path"] = "ctc/config.json"
        self.reject("file_duplicate_or_role")

    def test_forbidden_deployed_formats_even_when_manifest_lists_them(self):
        record = self.manifest["models"]["ctc"]["files"][0]
        for name in ("pytorch_model.bin", "model.pkl", "pickle", "model.py", "model.onnx", "tokenizer.json", "model.safetensors.index.json"):
            record["path"] = "ctc/" + name
            self.reject("file_not_allowed")

    def test_unlisted_files_and_directories_rejected_without_reading(self):
        for name in ("ctc/pytorch_model.bin", "ctc/custom.py", "ctc/.env", "speaker/extra.onnx", "secret"):
            path = self.root / name
            path.write_bytes(b"must not be opened")
            self.reject("bundle_inventory")
            path.unlink()
        (self.root / "ctc/nested").mkdir()
        self.reject("bundle_inventory")

    def test_native_case_alias_filename_rejected(self):
        path = self.root / "ctc/config.json"
        intermediate = self.root / "ctc/temporary-name"
        path.rename(intermediate)
        intermediate.rename(path.with_name("Config.json"))
        self.reject("bundle_inventory")

    def test_native_case_alias_supplied_root(self):
        if os.name == "nt":
            with self.assertRaisesRegex(validator.BundleError, "path_alias"):
                validator.verify_local_speech_bundle(self.root.with_name("BUNDLE"), self.manifest_path)
            NATIVE_PATH_PROBES["root_case_alias"] = "rejected_native_windows"
        else:
            with self.assertRaises(validator.BundleError):
                validator.verify_local_speech_bundle(self.root.with_name("BUNDLE"), self.manifest_path)
            NATIVE_PATH_PROBES["root_case_alias"] = "case_sensitive_missing_rejected"

    def test_symlink_and_windows_reparse_stat_gate(self):
        for mode, attributes in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFREG | 0o600, 0x400),
                                 (stat.S_IFDIR | 0o700, 0x400)):
            fake = SimpleNamespace(st_mode=mode, st_file_attributes=attributes, st_nlink=1)
            with self.assertRaisesRegex(validator.BundleError, "path_link"):
                validator._regular(fake, directory=stat.S_ISDIR(mode))

    def test_native_symlink_file_and_parent_when_permitted(self):
        target = self.home / "synthetic-target"
        target.write_bytes(b"SYNTHETIC FAKE ONNX - NOT A MODEL")
        link = self.root / "speaker/model.onnx"
        original = link.read_bytes()
        link.unlink()
        try:
            link.symlink_to(target)
        except OSError as error:
            # Capability outcome is explicit, never unittest.skip or a false native pass.
            self.assertIn(getattr(error, "winerror", None) if os.name == "nt" else error.errno,
                          (5, 1314) if os.name == "nt" else (1, 13, 95))
            NATIVE_PATH_PROBES["symlink"] = "creation_denied_no_native_link_coverage"
            link.write_bytes(original)
            with self.assertRaisesRegex(validator.BundleError, "path_link"):
                validator._regular(SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0, st_nlink=1))
            return
        NATIVE_PATH_PROBES["symlink"] = "native_file_and_directory_rejected"
        self.reject("path_link")
        link.unlink()
        link.write_bytes(original)
        alias = self.home / "linked-parent"
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        with self.assertRaisesRegex(validator.BundleError, "path_link"):
            validator.verify_local_speech_bundle(alias, self.manifest_path)
        with self.assertRaisesRegex(validator.BundleError, "path_link"):
            validator.verify_local_speech_bundle(alias / "ctc", self.manifest_path)

    def test_native_hardlink_rejected(self):
        source = self.root / "speaker/model.onnx"
        target = self.home / "hardlink"
        os.link(source, target)
        self.addCleanup(target.unlink)
        self.reject("path_hardlink")
        NATIVE_PATH_PROBES["hardlink"] = "native_rejected"

    def test_native_windows_junction_rejected(self):
        if os.name != "nt":
            # Windows junctions do not exist on this platform. Reparse rejection
            # is still checked, and the receipt must not call this native coverage.
            with self.assertRaisesRegex(validator.BundleError, "path_link"):
                validator._regular(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400), directory=True)
            NATIVE_PATH_PROBES["junction"] = "not_windows_stat_gate_only"
            return
        import _winapi
        alias = self.home / "junction"
        _winapi.CreateJunction(str(self.root), str(alias))
        self.addCleanup(alias.rmdir)
        self.assertTrue(alias.lstat().st_file_attributes & 0x400)
        with self.assertRaisesRegex(validator.BundleError, "path_link"):
            validator.verify_local_speech_bundle(alias, self.manifest_path)
        with self.assertRaisesRegex(validator.BundleError, "path_link"):
            validator.verify_local_speech_bundle(alias / "ctc", self.manifest_path)
        NATIVE_PATH_PROBES["junction"] = "native_root_and_ancestor_rejected"

    def test_manifest_must_be_json_not_environment_file(self):
        with patch.object(validator, "_absolute", side_effect=AssertionError("must not inspect env path")):
            with self.assertRaisesRegex(validator.BundleError, "manifest_filename"):
                validator.verify_local_speech_bundle(self.root, self.home / ".env")

    def test_file_size_digest_and_budget(self):
        record = self.manifest["models"]["speaker"]["files"][0]
        original = copy.deepcopy(record)
        for size in (True, 0, -1, validator.MAX_ONNX_BYTES + 1):
            record["bytes"] = size
            self.reject("file_size")
        record.update(original)
        record["bytes"] += 1
        self.reject("file_size")
        record.update(original)
        record["sha256"] = "0" * 64
        self.reject("file_digest")
        record.update(original)
        with patch.object(validator, "MAX_BUNDLE_BYTES", 1):
            self.reject("bundle_size")

    def test_content_change_with_same_length_detected(self):
        path = self.root / "speaker/model.onnx"
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        self.reject("file_digest")

    def test_manifest_and_metadata_json_size_limits(self):
        self.manifest_path.write_bytes(b" " * (validator.MAX_MANIFEST_BYTES + 1))
        self.reject("file_size", save=False)
        self.put("ctc/config.json", b" " * (validator.MAX_JSON_BYTES + 1))
        self.reject("file_size")

    def test_json_duplicate_invalid_depth_numbers_and_utf8(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":' + b"1" * 21 + b"}",
                    b'{"x":' + b"[" * 33 + b"0" + b"]" * 33 + b"}", b'{"x":"\xff"}', b"[]", b"{}junk"):
            with self.subTest(raw=raw[:40]):
                self.put("ctc/config.json", raw)
                self.reject()

    def test_duplicate_manifest_key(self):
        self.manifest_path.write_bytes(encoded(self.manifest).replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'))
        self.reject("json_duplicate_key", save=False)

    def test_tensor_header_length_limits_no_big_allocation(self):
        for raw in (b"tiny", (0).to_bytes(8, "little"), (validator.MAX_HEADER_BYTES + 1).to_bytes(8, "little") + b"{}",
                    (2**64 - 1).to_bytes(8, "little") + b"{}", (1024).to_bytes(8, "little") + b"{}"):
            self.put("ctc/model.safetensors", raw)
            self.reject("tensor_header_size")

    def test_tensor_header_invalid_json_and_duplicate_name(self):
        for raw in (b"[]", b'{"x":0,"x":1}', b'{"__metadata__":{"x":1}}', b'{"__metadata__":[]}'):
            self.tensor(raw_header=raw)
            self.reject()

    def test_tensor_dtype_shape_offsets_malformed(self):
        original = copy.deepcopy(self.header)
        for field, value in (("dtype", "float32"), ("dtype", "F8_E4M3"), ("dtype", []), ("shape", [-1, 2]),
                             ("shape", [True, 2]), ("shape", [1_000_001]), ("shape", [1] * 9), ("shape", "4,2"),
                             ("data_offsets", [-1, 31]), ("data_offsets", [0, True]), ("data_offsets", [32, 0]),
                             ("data_offsets", [0, len(self.payload) + 1]), ("data_offsets", [0, 31])):
            with self.subTest(field=field, value=value):
                header = copy.deepcopy(original)
                header["lm_head.weight"][field] = value
                self.tensor(header)
                self.reject()

    def test_tensor_overlap_gap_and_trailing_bytes(self):
        header = copy.deepcopy(self.header)
        header["lm_head.bias"]["data_offsets"] = [28, 44]
        self.tensor(header)
        self.reject("tensor_contiguity")
        header["lm_head.bias"]["data_offsets"] = [36, 52]
        self.tensor(header)
        self.reject("tensor_contiguity")
        self.tensor(payload=self.payload + b"tail")
        self.reject("tensor_contiguity")

    def test_tensor_scalar_zero_length_and_all_supported_dtypes(self):
        header = copy.deepcopy(self.header)
        payload = self.payload
        for dtype, width in validator.DTYPE_BYTES.items():
            start = len(payload)
            header["fixture_scalar_" + dtype] = {"dtype": dtype, "shape": [], "data_offsets": [start, start + width]}
            payload += bytes(width)
        header["empty"] = {"dtype": "F32", "shape": [0, 2], "data_offsets": [0, 0]}
        header["__metadata__"] = {"format": "pt", "fixture": "not real weights"}
        self.tensor(header, payload)
        self.assertTrue(self.verify()["ok"])

    def test_tensor_count_and_size_arithmetic_bounded(self):
        with patch.object(validator, "MAX_TENSORS", 8):
            self.reject("tensor_count")
        header = copy.deepcopy(self.header)
        header["lm_head.weight"]["shape"] = [1_000_000] * 8
        self.tensor(header)
        self.reject("tensor_size")

    def test_ctc_standard_architecture_only(self):
        for field, value in (("model_type", "custom"), ("architectures", ["Wav2Vec2ForPreTraining"]),
                             ("architectures", ["Wav2Vec2ForCTC", "Custom"]), ("add_adapter", True)):
            config = dict(self.config, **{field: value})
            self.put("ctc/config.json", encoded(config))
            self.reject("ctc_architecture")

    def test_custom_auto_map_rejected_in_every_metadata_document(self):
        for name, base in (("config.json", self.config), ("preprocessor_config.json", self.preprocessor),
                           ("tokenizer_config.json", self.tokenizer), ("special_tokens_map.json", self.special)):
            for key in ("auto_map", "trust_remote_code", "custom_pipelines"):
                self.put("ctc/" + name, encoded(dict(base, nested={key: "private_module.Class"})))
                self.reject("custom_code_metadata")
            self.put("ctc/" + name, encoded(base))

    def test_ctc_convolution_and_hidden_size(self):
        for field, value in (("conv_stride", [2] * 7), ("conv_kernel", [3] * 7), ("conv_dim", [1] * 6),
                             ("conv_dim", [True] * 7), ("num_feat_extract_layers", 8), ("hidden_size", True)):
            self.put("ctc/config.json", encoded(dict(self.config, **{field: value})))
            self.reject()

    def test_ctc_preprocessor_16khz_and_feature_extractor(self):
        for field, value in (("sampling_rate", 8000), ("sampling_rate", "16000"), ("feature_size", 2),
                             ("feature_extractor_type", "Custom"), ("do_normalize", "true"), ("padding_value", 1)):
            self.put("ctc/preprocessor_config.json", encoded(dict(self.preprocessor, **{field: value})))
            self.reject("ctc_preprocessor")

    def test_ctc_tokenizer_and_processor_classes(self):
        for field, value in (("tokenizer_class", "AutoTokenizer"), ("tokenizer_class", "Custom"),
                             ("processor_class", "Custom"), ("do_lower_case", True)):
            self.put("ctc/tokenizer_config.json", encoded(dict(self.tokenizer, **{field: value})))
            self.reject()
        self.put("ctc/tokenizer_config.json", encoded(self.tokenizer))
        self.put("ctc/processor_config.json", encoded({"processor_class": "Custom"}))
        self.reject("ctc_processor")

    def test_contiguous_integer_vocab_direct_cjk(self):
        for vocab in ({"<pad>": 0, "<unk>": 1, "|": 2, "\u4e2d": 4},
                      {"<pad>": 0, "<unk>": 1, "|": 2, "\u4e2d": 2},
                      {"<pad>": False, "<unk>": 1, "|": 2, "\u4e2d": 3},
                      {"<pad>": 0, "<unk>": 1, "|": 2, "\u2581\u4e2d": 3},
                      {"<pad>": 0, "<unk>": 1, "|": 2, "a": 3}):
            self.put("ctc/vocab.json", encoded(vocab))
            self.reject()

    def test_pad_special_tokens_and_vocab_size_agree(self):
        self.put("ctc/config.json", encoded(dict(self.config, pad_token_id=1)))
        self.reject("ctc_pad")
        self.put("ctc/config.json", encoded(dict(self.config, vocab_size=5)))
        self.reject("ctc_vocab_size")
        self.put("ctc/config.json", encoded(self.config))
        self.put("ctc/tokenizer_config.json", encoded(dict(self.tokenizer, pad_token_id=1)))
        self.reject("ctc_pad")
        self.put("ctc/tokenizer_config.json", encoded(self.tokenizer))
        self.put("ctc/special_tokens_map.json", encoded(dict(self.special, pad_token="<unk>")))
        self.reject("ctc_special_tokens")

    def test_lm_head_and_convolution_tensor_geometry(self):
        header = copy.deepcopy(self.header)
        header["lm_head.weight"]["shape"] = [2, 4]  # same bytes, wrong vocabulary axis
        self.tensor(header)
        self.reject("ctc_tensor_shape")
        header = copy.deepcopy(self.header)
        header["lm_head.bias"]["dtype"] = "I32"
        self.tensor(header)
        self.reject("ctc_tensor_shape")
        header = copy.deepcopy(self.header)
        header["wav2vec2.feature_extractor.conv_layers.0.conv.weight"]["shape"] = [1, 2, 5]
        self.tensor(header)
        self.reject("ctc_tensor_shape")

    def test_stream_reads_bounded_and_all_bytes_hashed(self):
        self.put("speaker/model.onnx", b"SYNTHETIC" * (validator.CHUNK_BYTES // 3))
        self.save()
        real_fdopen = os.fdopen
        reads = []

        class Observed:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, size=-1):
                self_outer.assertGreater(size, 0)
                self_outer.assertLessEqual(size, validator.MAX_HEADER_BYTES)
                reads.append(size)
                return self.stream.read(size)

        self_outer = self
        with patch.object(validator.os, "fdopen", side_effect=lambda *a, **k: Observed(real_fdopen(*a, **k))):
            receipt = validator.verify_local_speech_bundle(self.root, self.manifest_path)
        self.assertTrue(receipt["ok"])
        self.assertGreater(reads.count(validator.CHUNK_BYTES), 7)

    def test_inventory_change_during_verification_rejected(self):
        original = validator._ctc
        def mutate(*args):
            result = original(*args)
            (self.root / "ctc/extra.py").write_bytes(b"fixture only")
            return result
        with patch.object(validator, "_ctc", side_effect=mutate):
            self.reject("bundle_inventory")

    def test_manifest_change_during_verification_rejected(self):
        original = validator._ctc
        def mutate(*args):
            result = original(*args)
            self.manifest_path.write_bytes(self.manifest_path.read_bytes() + b" ")
            return result
        with patch.object(validator, "_ctc", side_effect=mutate):
            self.reject("file_changed")

    def test_bad_paths_rejected_before_artifact_open(self):
        self.manifest["models"]["ctc"]["files"][0]["path"] = "../private-secret.bin"
        self.save()
        original = validator._read
        reads = []
        def observe(path, *args, **kwargs):
            reads.append(path)
            self.assertEqual(path, self.manifest_path)
            return original(path, *args, **kwargs)
        with patch.object(validator, "_read", side_effect=observe):
            self.reject("path_relative", save=False)
        self.assertEqual(reads, [self.manifest_path])

    def test_verification_never_imports_native_runtime_or_uses_network(self):
        # os.fdopen lazily imports stdlib io; no other imports are needed.
        original_import = builtins.__import__
        def importing(name, *args, **kwargs):
            self.assertEqual(name, "io")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=importing), \
             patch.object(os, "system", side_effect=AssertionError("execution")), \
             patch.object(os, "getenv", side_effect=AssertionError("environment")):
            self.assertTrue(validator.verify_local_speech_bundle(self.root, self.manifest_path)["ok"])

    def test_fdopen_failure_closes_descriptor(self):
        real_open = validator.os.open
        descriptors = []
        def observe(*args):
            fd = real_open(*args)
            descriptors.append(fd)
            return fd
        with patch.object(validator.os, "open", side_effect=observe), \
             patch.object(validator.os, "fdopen", side_effect=OSError("synthetic fdopen failure")):
            self.reject("validation_failed")
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError):
            os.fstat(descriptors[0])

    def test_cli_success_error_and_argument_errors_json_only(self):
        for args, expected in ((["--bundle", str(self.root), "--manifest", str(self.manifest_path)], 0),
                               (["--bundle", str(self.home / "private-missing"), "--manifest", str(self.manifest_path)], 1),
                               (["--private-secret-argument"], 1), ([], 1)):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = validator.main(args)
            self.assertEqual(code, expected)
            self.assertEqual(err.getvalue(), "")
            receipt = json.loads(out.getvalue())
            self.assertEqual(receipt["ok"], expected == 0)
            self.assertIs(receipt["inference_verified"], False)
            self.assertNotIn(str(self.home), out.getvalue())
            self.assertNotIn("private-secret", out.getvalue())

    def test_os_and_parser_errors_have_no_raw_exception_context(self):
        with patch.object(validator, "_verify", side_effect=OSError("private path secret")):
            self.reject("validation_failed")
        with patch.object(validator, "_verify", side_effect=ValueError("private parser fragment")):
            self.reject("validation_failed")

    def test_import_safe_and_stdlib_only_no_filesystem_model_or_env(self):
        source = Path(validator.__file__)
        spec = importlib.util.spec_from_file_location("synthetic_import_speech_validator", source)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        original_import = builtins.__import__
        blocked = {"backend", "numpy", "torch", "transformers", "onnx", "onnxruntime", "pickle", "dotenv",
                   "subprocess", "socket", "sherpa_onnx"}
        def importing(name, *args, **kwargs):
            self.assertNotIn(name.split(".")[0], blocked)
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=importing), \
             patch.object(Path, "open", side_effect=AssertionError("import IO")), \
             patch.object(os, "scandir", side_effect=AssertionError("import browse")), \
             patch.object(os, "getenv", side_effect=AssertionError("import environment")), \
             patch.object(os, "open", side_effect=AssertionError("import file")):
            spec.loader.exec_module(module)
        self.assertTrue(callable(module.verify_local_speech_bundle))


if __name__ == "__main__":
    unittest.main()