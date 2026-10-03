"""TEST-ONLY fixed CAMPPlus technical probe, never product activation.

Import is inert: no native imports, IO, context, test collection, or skip.
Explicit --run-test-only launches one -I -B -S child using the fixed, existing
runtime. Only the adapter's _require is temporarily mocked, inside that child;
the real constructor still receives its default license_reviewed=False.
Python audit confinement is NOT an OS sandbox for native DLLs/ONNX Runtime.
No approved corpus: synthetic tone results cannot establish speech quality.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import wave
import zipfile
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
HOME = Path(r"C:\Users\zhoumi\AppData\Local\GoldenMic\local-speech-20261002")
RUNTIME = HOME / "runtime"
EXECUTABLE = RUNTIME / "Scripts/python.exe"
WEIGHT = HOME / "acquired/campplus-api-complete.onnx"
SOURCE = PROJECT / "backend/providers/local_speech.py"
SELF = PROJECT / "tests/local_speaker_runtime_smoke.py"
MODEL_BYTES = 28281138
MODEL_SHA = "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11"
EXE_SHA = "21bb438c0d4a6f1f164b9a646f6ee000340185e5871180aec06db8d3f07c0082"
WHEELS = (
    ("numpy-2.4.2-cp311-cp311-win_amd64.whl", 12607972,
     "b9c618d56a29c9cb1c4da979e9899be7578d2e0b3c24d52079c166324c9e8695"),
    ("sherpa_onnx-1.13.8-cp311-cp311-win_amd64.whl", 2283357,
     "171e6fac715bae20e11829e8dbfc70ed990ede1b35e6332f8121b27691a002de"),
    ("sherpa_onnx_core-1.13.8-py3-none-win_amd64.whl", 16903581,
     "5579e80196d516e6dae23c8f629292ce3142ab8869925d32b94612fd86f93733"),
)


class ProbeFailure(RuntimeError):
    """Static, deliberately message-free failure."""


def require(condition):
    if not condition:
        raise ProbeFailure()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_bytes(raw, size, expected):
    require(len(raw) == size and digest(raw) == expected)
    return expected


def file_hash(path):
    # Reject reparse points and hardlinks, including ancestor junctions. This
    # reduces accidental indirection; it is not race-free OS containment.
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def claims():
    return {"test_only": True, "license_gate_bypassed_for_test": False,
            "runtime_path_verified": False, "production_activation": False,
            "real_speech_quality": False, "approved_corpus": False,
            "os_sandbox": False, "python_audit_guard": True,
            "native_io_confinement_proven": False,
            "application_base_compatibility_certified": False}


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ProbeFailure()


def arguments(argv):
    parser = QuietParser(add_help=False)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-test-only", action="store_true")
    group.add_argument("--test-only-child", metavar="OWNED_TEMP")
    return parser.parse_args(argv)


def clean_environment(root):
    # No inherited credentials, provider URLs, Python hooks, model acknowledgments
    # or user cache paths. -S also prevents site/.pth processing before this code.
    windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    return {"SYSTEMROOT": str(windows), "WINDIR": str(windows),
            "PATH": str(windows / "System32"), "TEMP": str(root), "TMP": str(root),
            "USERPROFILE": str(root), "HOME": str(root), "APPDATA": str(root),
            "LOCALAPPDATA": str(root), "PYTHON_DOTENV_DISABLED": "1",
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1"}


class Guard:
    def __init__(self, root):
        self.root = root.resolve()
        self.self_check = False
        self.expected_denials = 0
        self.unexpected_denials = 0
        base = Path(sys.base_prefix).resolve()
        self.trees = (base / "Lib", base / "DLLs", RUNTIME / "Lib/site-packages")
        self.files = {p.resolve() for p in (SOURCE, SELF, WEIGHT, EXECUTABLE,
                      base / "python311.zip", base / "python311.dll",
                      RUNTIME / "pyvenv.cfg")}
        self.files.update((HOME / "wheels-mirror-verified" / row[0]).resolve() for row in WHEELS)
        self.base_site = base / "Lib/site-packages"

    def reject(self):
        if self.self_check:
            self.expected_denials += 1
        else:
            self.unexpected_denials += 1
        raise ProbeFailure()

    def path(self, value, write=False):
        if not isinstance(value, (str, bytes, os.PathLike)):
            self.reject()  # No untracked file-descriptor opens.
        path = Path(os.fsdecode(value)).resolve()
        if path.is_relative_to(self.root):
            return
        if write or any(part.lower() == ".env" or part.lower().startswith(".env.") for part in path.parts):
            self.reject()
        if path.is_relative_to(self.base_site):
            self.reject()
        if path in self.files or any(path.is_relative_to(tree) for tree in self.trees):
            return
        self.reject()

    def audit(self, event, args):
        if event.startswith("socket.") or event in {
            "subprocess.Popen", "os.system", "os.startfile", "os.startfile/2",
            "os.exec", "os.posix_spawn", "os.fork", "os.forkpty",
            "winreg.OpenKey",
        }:
            self.reject()
        if event == "open":
            flags = args[2] if isinstance(args[2], int) else 0
            self.path(args[0], bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)))
        elif event in {"os.listdir", "os.scandir", "os.chdir"}:
            self.path(args[0])
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "shutil.rmtree"}:
            self.path(args[0], True)
        elif event in {"os.rename", "os.link", "os.symlink"}:
            self.path(args[0], True)
            self.path(args[1], True)
        elif event.startswith("sqlite3."):
            self.reject()

    def check(self):
        self.self_check = True
        try:
            cases = [("open", (str(PROJECT / name), "r", 0))
                     for name in (".env", "data/denied", "eval/denied", "eval_sample/denied")]
            cases += [("open", (str(SOURCE), "w", os.O_WRONLY)),
                      ("socket.getaddrinfo", ()), ("socket.connect", ()),
                      ("subprocess.Popen", ())]
            for event, args in cases:
                try:
                    self.audit(event, args)  # No actual private file/network access.
                except ProbeFailure:
                    pass
                else:
                    raise ProbeFailure()
            require(self.expected_denials == len(cases))
        finally:
            self.self_check = False


def verify_installation():
    site = RUNTIME / "Lib/site-packages"
    result = []
    for name, size, expected in WHEELS:
        wheel = HOME / "wheels-mirror-verified" / name
        require(wheel.stat().st_size == size and file_hash(wheel) == expected)
        checked = 0
        bytecode_variants = 0
        with zipfile.ZipFile(wheel) as archive:
            for item in archive.infolist():
                # Compare all installed package/binary payloads, not generated
                # console launchers, RECORD, or installer-added metadata.
                if item.is_dir() or any(p.endswith((".dist-info", ".data")) for p in Path(item.filename).parts):
                    continue
                relative = Path(item.filename)
                require(not relative.is_absolute() and ".." not in relative.parts)
                installed_hash = file_hash(site / relative)
                payload_hash = digest(archive.read(item))
                if installed_hash != payload_hash:
                    # Existing installation contains one differing pyc.
                    # Do NOT accept arbitrary regenerated bytecode or silently
                    # ignore it: bind the observed exception by all three hashes.
                    require(relative.suffix == ".pyc" and "__pycache__" in relative.parts
                            and digest(item.filename.encode()) == "ab92531e2829c1c09f25b0de6e70459c9327fc14da38f5833ed04c6586834317"
                            and payload_hash == "5d125f7e0bb8ed32be6fc7ec52fd3f4e418b1dd4ee7fc5cd6e44b49d5f325a4f"
                            and installed_hash == "4d2ce67aaa8cc4e55ab866a2506446f64b140698c496445d7ab4bb3f0792b2b3")
                    bytecode_variants += 1
                checked += 1
        require(checked > 0)
        result.append({"sha256": expected, "bytes": size, "installed_payload_files_verified": checked,
                   "hash_pinned_installed_bytecode_variants": bytecode_variants})
    return result


def write_wave(path, raw, channels=1, rate=16000):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(raw)


def gate(module, result, suffix):
    state = module.local_speech_readiness("speaker_embedding", WEIGHT, license_reviewed=False)
    require(not state.available and not state.model_license_reviewed and not state.inference_verified)
    require(state.blocked_prerequisites == ("model_license_and_language_not_reviewed",))
    try:
        module.LocalSpeakerEncoder(WEIGHT, license_reviewed=False)
    except module.SpeechUnavailable:
        result["license_gate_" + suffix] = "rejected"
    else:
        raise ProbeFailure()


def exercise(module, folder, result):
    import numpy as np

    clock = np.arange(28 * 16000, dtype=np.float64) / 16000
    raw = (10000 * np.sin(2 * np.pi * (180 * clock + 5 * clock ** 2))
           + 2000 * np.sin(2 * np.pi * 391 * clock)).astype("<i2").tobytes()
    full, crop = folder / "tone.wav", folder / "crop.wav"
    write_wave(full, raw)
    middle = raw[3 * 16000 * 2:23 * 16000 * 2]
    write_wave(crop, middle)
    result["synthetic_pcm_sha256"] = digest(raw)
    result["crop_pcm_sha256"] = digest(middle)
    require(middle != raw[:20 * 16000 * 2])
    require(np.array_equal(module._pcm(full, 3, 23), module._pcm(crop, 0, 20)))
    original = module._require
    try:
        with patch.object(module, "_require", return_value=None) as bypass:
            result["license_gate_bypassed_for_test"] = True
            encoder = module.LocalSpeakerEncoder(WEIGHT)  # DEFAULT FALSE, never True.
            bypass.assert_called_once_with("speaker_embedding", WEIGHT, False)
        # The only bypass ends immediately after construction. All subsequent
        # methods and even _require are original while inference is performed.
        require(module._require is original)

        def embedding(path, start, end):
            vector = np.asarray(encoder.embedding(path, start, end), dtype=np.float64)
            require(vector.shape == (192,) and bool(np.isfinite(vector).all()))
            require(float(np.linalg.norm(vector)) > 0)
            return vector

        first = embedding(full, 0, 6)
        repeat = embedding(full, 0, 6)
        long = embedding(full, 1, 25)  # Actual adapter selects [3, 23].
        direct = embedding(full, 3, 23)
        cropped = embedding(crop, 0, 20)
        for left, right in ((first, repeat), (long, direct), (long, cropped)):
            require(bool(np.allclose(left, right, rtol=1e-5, atol=1e-6)))
        result.update({"dimension": 192, "norm": float(np.linalg.norm(first)),
                       "repeat_max_abs_delta": float(np.max(np.abs(first - repeat))),
                       "middle_direct_max_abs_delta": float(np.max(np.abs(long - direct))),
                       "middle_crop_max_abs_delta": float(np.max(np.abs(long - cropped))),
                       "middle_pcm_content_equal": True, "middle_frames": 320000,
                       "rtol": 1e-5, "atol": 1e-6})
        negatives = {}
        for name, channels, rate in (("channels", 2, 16000), ("rate", 1, 8000), ("truncation", 1, 16000)):
            path = folder / (name + ".wav")
            write_wave(path, raw[:32000], channels, rate)
            if name == "truncation":
                path.write_bytes(path.read_bytes()[:-4])  # Header still claims full PCM.
            for method in (module._pcm, encoder.embedding):
                try:
                    method(path, 0, 1)
                except ValueError:
                    pass
                else:
                    raise ProbeFailure()
            negatives[name] = "rejected_pcm_and_embedding"
        for start, end in ((0, 29), (0, 0), (-1, 1), (math.nan, 1)):
            try:
                module._pcm(full, start, end)
            except ValueError:
                pass
            else:
                raise ProbeFailure()
        short = []
        for frames in (1, 16, 80, 160, 320, 400, 800):
            # Probe observations, not an assumed model-specific duration cutoff.
            require(len(module._pcm(full, 0, frames / 16000)) == frames)
            try:
                vector = np.asarray(encoder.embedding(full, 0, frames / 16000), dtype=np.float64)
            except module.SpeechUnavailable:
                short.append({"frames": frames, "status": "rejected_too_short"})
            else:
                require(vector.shape == (192,))
                finite = bool(np.isfinite(vector).all())
                short.append({"frames": frames,
                          "status": "returned_finite" if finite else "returned_nonfinite_limitation",
                          "finite": finite, "dimension": int(vector.size)})
        require(any(row["status"] == "rejected_too_short" for row in short))
        result.update({"invalid_pcm": negatives, "invalid_windows_rejected": 4, "short_segments": short,
                       "short_segment_finite_output_guaranteed": False,
                       "technical_probe_not_product_acceptance": True})
    finally:
        require(module._require is original)
        gate(module, result, "after")


def write_json(path, value):
    with path.open("x", encoding="ascii") as output:
        json.dump(value, output, ensure_ascii=True, allow_nan=False, indent=2)


def child(root):
    # This function is reachable only via the explicit test script entry point.
    require(root.is_absolute() and root.name.startswith("gm-speaker-runtime-")
            and root.resolve() == root and root.is_dir())
    os.environ.clear()
    os.environ.update(clean_environment(root))
    os.chdir(root)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(RUNTIME / "Lib/site-packages"))
    result = claims()
    result.update({"status": "failed", "stage": 1})
    guard = Guard(root)
    sys.addaudithook(guard.audit)
    folder = root / "fixtures"
    try:
        guard.check()
        require(os.name == "nt" and tuple(sys.version_info[:3]) == (3, 11, 9)
                and struct.calcsize("P") == 8 and sys.flags.isolated and sys.flags.no_site)
        require(Path(sys.executable).resolve() == EXECUTABLE.resolve() and file_hash(EXECUTABLE) == EXE_SHA)
        result["runtime_path_verified"] = True
        result["runtime_executable_sha256"] = EXE_SHA
        result["python_version"] = [3, 11, 9]
        result["stage"] = 2
        result["wheels"] = verify_installation()
        require(WEIGHT.stat().st_size == MODEL_BYTES and file_hash(WEIGHT) == MODEL_SHA)
        result["weight_before_sha256"] = MODEL_SHA
        result["source_before_sha256"] = file_hash(SOURCE)
        result["stage"] = 3
        spec = importlib.util.spec_from_file_location("_gm_test_only_speech", SOURCE)
        require(spec is not None and spec.loader is not None)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        # Execute the verified source, not an unverified adjacent cached pyc.
        # importlib still supplies the standalone module/spec for dataclasses.
        exec(compile(SOURCE.read_bytes(), str(SOURCE), "exec"), module.__dict__)
        require("backend" not in sys.modules and "backend.config" not in sys.modules)
        gate(module, result, "before")
        folder.mkdir()
        result["stage"] = 4
        exercise(module, folder, result)
        result["stage"] = 5
        result["status"] = "passed"
    except BaseException:
        result["status"] = "failed"  # Never persist raw errors/paths/tracebacks.
    finally:
        try:
            result["source_after_sha256"] = file_hash(SOURCE)
            result["weight_after_sha256"] = file_hash(WEIGHT)
            require(result.get("source_before_sha256") == result["source_after_sha256"])
            require(result["weight_after_sha256"] == MODEL_SHA)
            if folder.exists():
                shutil.rmtree(folder)
            result["owned_fixtures_removed"] = not folder.exists()
        except BaseException:
            result["status"] = "failed"
        result["guard_expected_denials"] = guard.expected_denials
        result["guard_unexpected_denials"] = guard.unexpected_denials
        if guard.unexpected_denials:
            result["status"] = "failed"
        write_json(root / "child.json", result)
    return 0 if result["status"] == "passed" else 1


def run():
    require(Path(sys.executable).resolve() == EXECUTABLE.resolve()
            and sys.flags.isolated and sys.flags.no_site)
    # Do not consult inherited TEMP or the application's cwd for evidence.
    temp = Path(r"C:\Users\zhoumi\AppData\Local\Temp")
    root = Path(tempfile.mkdtemp(prefix="gm-speaker-runtime-", dir=temp)).resolve()
    result = claims()
    result["status"] = "failed"
    try:
        before = file_hash(SOURCE)
        require(file_hash(WEIGHT) == MODEL_SHA and WEIGHT.stat().st_size == MODEL_BYTES)
        require(file_hash(EXECUTABLE) == EXE_SHA)
        completed = subprocess.run(
            [str(EXECUTABLE), "-I", "-B", "-S", str(SELF), "--test-only-child", str(root)],
            cwd=root, env=clean_environment(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
        result["child_exit_code"] = completed.returncode
        if (root / "child.json").is_file():
            result.update(json.loads((root / "child.json").read_text(encoding="ascii")))
        result["parent_source_before_sha256"] = before
        result["parent_source_after_sha256"] = file_hash(SOURCE)
        result["parent_weight_after_sha256"] = file_hash(WEIGHT)
        require(completed.returncode == 0 and result["status"] == "passed"
                and before == result["parent_source_after_sha256"] == result["source_before_sha256"]
                and result["parent_weight_after_sha256"] == MODEL_SHA)
    except BaseException:
        result["status"] = "failed"
    finally:
        if (root / "fixtures").exists():
            shutil.rmtree(root / "fixtures")
        result["owned_fixtures_removed"] = not (root / "fixtures").exists()
        write_json(root / "receipt.json", result)
    print(json.dumps({"status": result["status"], "evidence_temp": str(root),
                      "receipt_sha256": file_hash(root / "receipt.json")}))
    return 0 if result["status"] == "passed" else 1


def main(argv=None):
    try:
        args = arguments(sys.argv[1:] if argv is None else argv)
        return child(Path(args.test_only_child)) if args.test_only_child else run()
    except BaseException:
        print('{"status":"failed","stage":0}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())