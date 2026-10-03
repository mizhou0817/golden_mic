"""Read-only, stdlib-only installed local speech bundle integrity gate.

CLI: --bundle ABSOLUTE_DIRECTORY --manifest ABSOLUTE_JSON_FILE. No discovery,
downloads, conversion, environment/config access, model imports or execution.

Schema v1 (unknown manifest keys are rejected):
  {schema_version: 1, kind: "local-speech-bundle", status: "installed",
   models: {speaker: MODEL, ctc: MODEL}}
  MODEL = {format: "onnx" | "safetensors", upstream_revision: lowercase Git SHA
           (40 or 64 hex), license: {identifier: nonempty string,
           acknowledged: true, source_uri: HTTPS URI}, files: [FILE, ...]}
  FILE = {path: canonical bundle-relative path, bytes: positive integer,
          sha256: installed bytes SHA256, source_sha256: upstream bytes SHA256,
          source_uri: HTTPS URI}
For converted weights, source_sha256/source_uri describe the original artifact;
sha256 describes the installed safetensors. Source digests/URIs and license
acknowledgements are operator assertions, NOT authenticated by this validator.

Layout: speaker/model.onnx; ctc/{model.safetensors,config.json,vocab.json,
preprocessor_config.json,tokenizer_config.json,special_tokens_map.json}.
Optional: ctc/processor_config.json; either role's README.md, LICENSE, LICENSE.txt.
The separately supplied manifest may be outside the bundle or its one root file
installed-manifest.json. No unlisted files/directories, links or hardlinks.

Limits: manifest 128 KiB, other JSON 1 MiB, safetensors header 4 MiB, 20,000
tensors, rank 8, dimension 1,000,000; CTC weights 2 GiB, ONNX 256 MiB, total
3 GiB. Single unsharded standard Wav2Vec2ForCTC, 16 kHz, direct CJK vocabulary,
standard seven-layer convolution clock only. This intentionally narrow profile
does not certify full tensor completeness, native ONNX validity, AutoProcessor
loading, source authenticity, conversion equivalence, quality or inference.
Use a quiescent operator-controlled directory: identity/stat rechecks detect
ordinary changes, but are not an OS sandbox against a hostile concurrent writer.
No activation/readiness settings are changed. A success is prerequisite integrity
only; every receipt explicitly leaves inference and activation unverified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


MAX_MANIFEST_BYTES = 128 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 4 * 1024 * 1024
MAX_WEIGHT_BYTES = 2 * 1024**3
MAX_ONNX_BYTES = 256 * 1024**2
MAX_BUNDLE_BYTES = 3 * 1024**3
MAX_FILES = 14
MAX_TENSORS = 20_000
CHUNK_BYTES = 1024 * 1024
MANIFEST_NAME = "installed-manifest.json"
CTC_REQUIRED = frozenset({"model.safetensors", "config.json", "vocab.json",
                          "preprocessor_config.json", "tokenizer_config.json",
                          "special_tokens_map.json"})
OPTIONAL_TEXT = frozenset({"README.md", "LICENSE", "LICENSE.txt"})
DTYPE_BYTES = {"BOOL": 1, "U8": 1, "I8": 1, "I16": 2, "U16": 2,
               "I32": 4, "U32": 4, "I64": 8, "U64": 8, "F16": 2,
               "BF16": 2, "F32": 4, "F64": 8}
KERNELS = [10, 3, 3, 3, 3, 2, 2]
STRIDES = [5, 2, 2, 2, 2, 2, 2]


class BundleError(ValueError):
    """Arguments contain only a validator-authored static code."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise BundleError(code)


def _keys(value: Any, required: set[str], optional: frozenset[str] = frozenset()) -> None:
    _require(type(value) is dict and required <= value.keys()
             and value.keys() <= required | optional, "schema_keys")


def _integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _digest(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _uri(value: Any) -> None:
    _require(type(value) is str and 1 <= len(value) <= 2048
             and all(32 < ord(c) < 127 for c in value) and "\\" not in value, "source_uri")
    parsed = urlsplit(value)
    _require(parsed.scheme == "https" and bool(parsed.hostname)
             and parsed.username is None and parsed.password is None
             and not parsed.query and not parsed.fragment
             and parsed.port in (None, 443), "source_uri")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "json_duplicate_key")
        result[key] = value
    return result


def _json(raw: bytes, limit: int) -> dict[str, Any]:
    _require(0 < len(raw) <= limit, "json_size")
    # Bound nesting BEFORE the C/Python JSON decoder sees attacker-controlled input.
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            _require(depth <= 32, "json_depth")
        elif byte in (93, 125):
            depth -= 1
            _require(depth >= 0, "json_invalid")

    def integer(text: str) -> int:
        _require(len(text) <= 20, "json_number")
        return int(text)

    def floating(text: str) -> float:
        value = float(text)
        _require(math.isfinite(value), "json_number")
        return value

    def constant(_text: str) -> Any:
        raise BundleError("json_number")

    result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                        parse_int=integer, parse_float=floating, parse_constant=constant)
    _require(type(result) is dict, "json_object")
    return result


def _regular(info: os.stat_result, *, directory: bool = False) -> None:
    _require(not stat.S_ISLNK(info.st_mode)
             and not (getattr(info, "st_file_attributes", 0) & 0x400), "path_link")
    _require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "path_type")
    if not directory:
        _require(info.st_nlink == 1, "path_hardlink")


def _absolute(value: str | Path, *, directory: bool) -> Path:
    text = os.fspath(value)
    _require(type(text) is str and bool(text) and "\x00" not in text, "path_absolute")
    slash = text.replace("\\", "/") if os.name == "nt" else text
    # Never stat UNC/network/device paths, including Windows extended path syntax.
    _require(not slash.startswith("//"), "path_absolute")
    parts = slash.split("/")
    _require(all(p not in ("", ".", "..") for p in parts[1:])
             and "\\" not in slash and all(not p.endswith((".", " ")) for p in parts), "path_canonical")
    for component in parts[1:]:
        _require(not any(ord(c) < 32 or c in '<>:"|?*' for c in component)
                 and re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[0-9\u00b9\u00b2\u00b3]|LPT[0-9\u00b9\u00b2\u00b3])(?:\..*)?",
                                  component, re.IGNORECASE) is None, "path_canonical")
    path = Path(text)
    _require(path.is_absolute() and path != Path(path.anchor), "path_absolute")
    if os.name == "nt":
        _require(re.fullmatch(r"[A-Za-z]:", parts[0]) is not None
                 and all(":" not in p for p in parts[1:]), "path_absolute")
    for ancestor in reversed(path.parents):
        _regular(ancestor.lstat(), directory=True)
    _regular(path.lstat(), directory=directory)
    actual, supplied = str(path.resolve(strict=True)), str(path)
    if os.name == "nt":
        actual, supplied = actual[0].lower() + actual[1:], supplied[0].lower() + supplied[1:]
    _require(actual == supplied, "path_alias")
    return path


def _relative(value: Any) -> str:
    _require(type(value) is str and re.fullmatch(r"(?:speaker|ctc)/[A-Za-z0-9_.-]+", value) is not None,
             "path_relative")
    role, name = value.split("/")
    allowed = ({"model.onnx"} | OPTIONAL_TEXT if role == "speaker" else
               CTC_REQUIRED | OPTIONAL_TEXT | {"processor_config.json"})
    _require(name in allowed, "file_not_allowed")
    return value


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0))


def _read(path: Path, limit: int, expected: dict[str, Any] | None = None) -> tuple[bytes, str, tuple[int, ...]]:
    """Hash bounded chunks; retain only small JSON/text or the bounded tensor header."""
    before = path.lstat()
    _regular(before)
    _require(0 < before.st_size <= limit, "file_size")
    if expected is not None:
        _require(before.st_size == expected["bytes"], "file_size")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with opened as stream:
        _require(_identity(os.fstat(stream.fileno())) == _identity(before), "file_changed")
        digest = hashlib.sha256()
        prefix = b""
        total = 0
        if path.suffix == ".safetensors":
            first = stream.read(8)
            _require(len(first) == 8, "tensor_header_size")
            size = int.from_bytes(first, "little")
            _require(2 <= size <= MAX_HEADER_BYTES and size <= before.st_size - 8, "tensor_header_size")
            prefix = stream.read(size)
            _require(len(prefix) == size and prefix.startswith(b"{"), "tensor_header_size")
            digest.update(first)
            digest.update(prefix)
            total = 8 + size
        while chunk := stream.read(CHUNK_BYTES):
            total += len(chunk)
            _require(total <= before.st_size, "file_changed")
            digest.update(chunk)
            if path.suffix != ".safetensors" and limit <= MAX_JSON_BYTES:
                prefix += chunk
        _require(total == before.st_size and _identity(os.fstat(stream.fileno())) == _identity(before), "file_changed")
    _require(_identity(path.lstat()) == _identity(before), "file_changed")
    actual = digest.hexdigest()
    if expected is not None:
        _require(actual == expected["sha256"], "file_digest")
    return prefix, actual, _identity(before)


def _inventory(root: Path, expected: set[str], manifest_inside: bool) -> dict[str, tuple[int, ...]]:
    seen: dict[str, tuple[int, ...]] = {}
    root_names = {"speaker", "ctc"} | ({MANIFEST_NAME} if manifest_inside else set())
    with os.scandir(root) as entries:
        for entry in entries:
            _require(entry.name in root_names and entry.name not in seen, "bundle_inventory")
            # Windows DirEntry.stat caches enumeration data (nlink/ino can be 0).
            # Path.lstat obtains real link counts and identity without following links.
            info = (root / entry.name).lstat()
            _regular(info, directory=entry.name in {"speaker", "ctc"})
            seen[entry.name] = _identity(info)
    _require(set(seen) == root_names, "bundle_inventory")
    for role in ("speaker", "ctc"):
        with os.scandir(root / role) as entries:
            for entry in entries:
                name = role + "/" + entry.name
                _require(name in expected and name not in seen, "bundle_inventory")
                info = (root / role / entry.name).lstat()
                _regular(info)
                seen[name] = _identity(info)
    _require(set(seen) == expected | root_names, "bundle_inventory")
    return seen


def _manifest(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _keys(value, {"schema_version", "kind", "status", "models"})
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["kind"] == "local-speech-bundle" and value["status"] == "installed", "manifest_kind")
    _keys(value["models"], {"speaker", "ctc"})
    records: dict[str, dict[str, Any]] = {}
    for role, model in value["models"].items():
        _keys(model, {"format", "upstream_revision", "license", "files"})
        _require(model["format"] == ("onnx" if role == "speaker" else "safetensors"), "model_format")
        _require(type(model["upstream_revision"]) is str and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", model["upstream_revision"]) is not None,
                 "upstream_revision")
        license_info = model["license"]
        _keys(license_info, {"identifier", "acknowledged", "source_uri"})
        _require(license_info["acknowledged"] is True, "license_acknowledgement")
        _require(type(license_info["identifier"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .()+-]{0,127}", license_info["identifier"]) is not None,
                 "license_identifier")
        _uri(license_info["source_uri"])
        files = model["files"]
        _require(type(files) is list and 1 <= len(files) <= MAX_FILES, "file_count")
        for record in files:
            _keys(record, {"path", "bytes", "sha256", "source_sha256", "source_uri"})
            name = _relative(record["path"])
            _require(name.startswith(role + "/") and name not in records, "file_duplicate_or_role")
            _require(_integer(record["bytes"], 1, _file_limit(name)), "file_size")
            _require(_digest(record["sha256"]) and _digest(record["source_sha256"]), "digest_schema")
            _uri(record["source_uri"])
            records[name] = record
    required = {"speaker/model.onnx"} | {"ctc/" + n for n in CTC_REQUIRED}
    _require(required <= records.keys() and len(records) <= MAX_FILES, "file_set")
    _require(sum(r["bytes"] for r in records.values()) <= MAX_BUNDLE_BYTES, "bundle_size")
    return records


def _file_limit(name: str) -> int:
    if name == "ctc/model.safetensors":
        return MAX_WEIGHT_BYTES
    if name == "speaker/model.onnx":
        return MAX_ONNX_BYTES
    return MAX_JSON_BYTES


def _no_custom(value: Any) -> None:
    if type(value) is dict:
        _require(not ({"auto_map", "custom_pipelines", "trust_remote_code"} & value.keys()), "custom_code_metadata")
        for child in value.values():
            _no_custom(child)
    elif type(value) is list:
        for child in value:
            _no_custom(child)


def _tensors(raw: bytes, file_bytes: int) -> dict[str, Any]:
    header = _json(raw, MAX_HEADER_BYTES)
    metadata = header.pop("__metadata__", {})
    _require(type(metadata) is dict and all(type(v) is str for v in metadata.values()), "tensor_metadata")
    _require(1 <= len(header) <= MAX_TENSORS, "tensor_count")
    budget = file_bytes - 8 - len(raw)
    ranges = []
    for name, tensor in header.items():
        _require(re.fullmatch(r"[A-Za-z0-9_.]{1,256}", name) is not None, "tensor_name")
        _keys(tensor, {"dtype", "shape", "data_offsets"})
        dtype, shape, offsets = tensor["dtype"], tensor["shape"], tensor["data_offsets"]
        _require(type(dtype) is str and dtype in DTYPE_BYTES, "tensor_dtype")
        _require(type(shape) is list and len(shape) <= 8
                 and all(_integer(d, 0, 1_000_000) for d in shape), "tensor_shape")
        _require(type(offsets) is list and len(offsets) == 2
                 and all(_integer(v, 0, budget) for v in offsets), "tensor_offsets")
        size = math.prod(shape) * DTYPE_BYTES[dtype]
        _require(size <= budget and offsets[1] - offsets[0] == size, "tensor_size")
        ranges.append(tuple(offsets))
    cursor = 0
    for start, end in sorted(ranges):
        _require(start == cursor, "tensor_contiguity")
        cursor = end
    _require(cursor == budget, "tensor_contiguity")
    return header


def _ctc(documents: dict[str, dict[str, Any]], tensors: dict[str, Any]) -> int:
    for document in documents.values():
        _no_custom(document)
    config = documents["config.json"]
    _require(config.get("model_type") == "wav2vec2"
             and config.get("architectures") == ["Wav2Vec2ForCTC"]
             and config.get("add_adapter", False) is False, "ctc_architecture")
    dims = config.get("conv_dim")
    _require(config.get("conv_kernel") == KERNELS and config.get("conv_stride") == STRIDES
             and all(type(n) is int for n in config["conv_kernel"] + config["conv_stride"])
             and type(dims) is list and len(dims) == 7
             and all(_integer(n, 1, 8192) for n in dims)
             and _integer(config.get("num_feat_extract_layers", 7), 7, 7), "ctc_convolution")
    hidden = config.get("hidden_size")
    _require(_integer(hidden, 1, 8192), "ctc_hidden_size")
    preprocessor = documents["preprocessor_config.json"]
    _require(_integer(preprocessor.get("sampling_rate"), 16000, 16000)
             and preprocessor.get("feature_extractor_type") == "Wav2Vec2FeatureExtractor"
             and _integer(preprocessor.get("feature_size"), 1, 1)
             and type(preprocessor.get("do_normalize")) is bool
             and type(preprocessor.get("padding_value")) in (int, float)
             and preprocessor["padding_value"] == 0, "ctc_preprocessor")
    tokenizer = documents["tokenizer_config.json"]
    _require(tokenizer.get("tokenizer_class") == "Wav2Vec2CTCTokenizer"
             and tokenizer.get("do_lower_case", False) is False, "ctc_tokenizer")
    for document in documents.values():
        if "processor_class" in document:
            _require(document["processor_class"] == "Wav2Vec2Processor", "ctc_processor")
    if "processor_config.json" in documents:
        _require(documents["processor_config.json"].get("processor_class") == "Wav2Vec2Processor", "ctc_processor")
    vocab = documents["vocab.json"]
    count = len(vocab)
    _require(4 <= count <= 65536 and all(1 <= len(k) <= 128 and _integer(v, 0, count - 1) for k, v in vocab.items())
             and set(vocab.values()) == set(range(count)), "ctc_vocabulary")
    _require(any(len(k) == 1 and 0x3400 <= ord(k) <= 0x9FFF for k in vocab), "ctc_cjk")
    _require(_integer(config.get("vocab_size"), count, count), "ctc_vocab_size")
    special = documents["special_tokens_map.json"]
    for name in ("pad_token", "unk_token"):
        token = tokenizer.get(name)
        _require(type(token) is str and token in vocab and special.get(name) == token, "ctc_special_tokens")
    _require(tokenizer["pad_token"] != tokenizer["unk_token"], "ctc_special_tokens")
    pad = vocab[tokenizer["pad_token"]]
    _require(_integer(config.get("pad_token_id"), pad, pad)
             and ("pad_token_id" not in tokenizer or _integer(tokenizer["pad_token_id"], pad, pad)), "ctc_pad")
    delimiter = tokenizer.get("word_delimiter_token")
    _require(type(delimiter) is str and delimiter in vocab
             and delimiter not in (tokenizer["pad_token"], tokenizer["unk_token"]), "ctc_special_tokens")
    for name, token in special.items():
        _require(name in {"pad_token", "unk_token", "bos_token", "eos_token", "word_delimiter_token"}
                 and type(token) is str and token in vocab
                 and (name not in tokenizer or tokenizer[name] == token), "ctc_special_tokens")

    def tensor_shape(name: str, shape: list[int]) -> None:
        tensor = tensors.get(name, {})
        _require(tensor.get("shape") == shape and tensor.get("dtype") in {"F16", "BF16", "F32"}, "ctc_tensor_shape")

    tensor_shape("lm_head.weight", [count, hidden])
    tensor_shape("lm_head.bias", [count])
    previous = 1
    for i, (dimension, kernel) in enumerate(zip(dims, KERNELS, strict=True)):
        tensor_shape(f"wav2vec2.feature_extractor.conv_layers.{i}.conv.weight", [dimension, previous, kernel])
        previous = dimension
    return count


def _verify(bundle: str | Path, manifest: str | Path) -> dict[str, Any]:
    _require(os.fspath(manifest).endswith(".json"), "manifest_filename")
    root = _absolute(bundle, directory=True)
    manifest_path = _absolute(manifest, directory=False)
    inside = manifest_path.is_relative_to(root)
    _require(not inside or manifest_path == root / MANIFEST_NAME, "manifest_location")
    raw, manifest_digest, manifest_identity = _read(manifest_path, MAX_MANIFEST_BYTES)
    records = _manifest(_json(raw, MAX_MANIFEST_BYTES))
    before = _inventory(root, set(records), inside)
    documents: dict[str, dict[str, Any]] = {}
    tensors: dict[str, Any] = {}
    for name, record in sorted(records.items()):
        prefix, _sha, identity = _read(root / name, _file_limit(name), record)
        _require(identity == before[name], "file_changed")
        if name.endswith(".json"):
            documents[name.split("/")[1]] = _json(prefix, MAX_JSON_BYTES)
        elif name == "ctc/model.safetensors":
            tensors = _tensors(prefix, record["bytes"])
    vocab_count = _ctc(documents, tensors)
    _require(_inventory(root, set(records), inside) == before
             and _identity(manifest_path.lstat()) == manifest_identity, "file_changed")
    return {"ok": True, "code": "integrity_verified", "schema_version": 1,
            "scope": "local_bundle_prerequisite_integrity", "manifest_sha256": manifest_digest,
            "files_verified": len(records), "bytes_verified": sum(r["bytes"] for r in records.values()),
            "tensor_count": len(tensors), "vocabulary_entries": vocab_count,
            "inference_verified": False, "activation_authorized": False,
            "onnx_parsed": False, "auto_processor_load_verified": False,
            "upstream_provenance_verified": False, "conversion_equivalence_verified": False}


def verify_local_speech_bundle(bundle: str | Path, manifest: str | Path) -> dict[str, Any]:
    """Return a path-free receipt or raise BundleError with a static code only."""
    code = "validation_failed"
    try:
        return _verify(bundle, manifest)
    except BundleError as error:
        code = str(error)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        # Neither private paths nor raw parser/OS messages leave the library.
        pass
    raise BundleError(code) from None


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise BundleError("cli_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Offline local speech bundle prerequisite integrity only.", allow_abbrev=False)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--manifest", required=True)
    try:
        arguments = parser.parse_args(argv)
        receipt = verify_local_speech_bundle(arguments.bundle, arguments.manifest)
    except BundleError as error:
        receipt = {"ok": False, "code": str(error), "scope": "local_bundle_prerequisite_integrity",
                   "inference_verified": False, "activation_authorized": False}
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())