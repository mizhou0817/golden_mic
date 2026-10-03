"""Synthetic producer contracts: real TEMP PCM, no config/runtime/model import."""
from __future__ import annotations

import hashlib
import importlib.util
import math
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from typing import Any

import numpy as np


def _load_source() -> Any:
    # Execute the real standalone adapter, not providers/__init__ or config.
    name = "_v2_speaker_vectors_source"
    path = Path(__file__).resolve().parents[1] / "backend/providers/local_speech.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass resolves its defining module here.
    spec.loader.exec_module(module)
    return module


speech = _load_source()
INVALID = "speaker_embedding_invalid: local encoder produced no usable vector"


class FakeStream:
    def __init__(self) -> None:
        self.rate = 0
        self.samples = np.empty(0, dtype=np.float32)
        self.finished = False

    def accept_waveform(self, rate: int, samples: np.ndarray) -> None:
        self.rate, self.samples = rate, samples.copy()

    def input_finished(self) -> None:
        self.finished = True


class FakeExtractor:
    def __init__(self, vector: Any, dim: Any = 3, ready: bool = True) -> None:
        self.vector, self.dim, self.ready = vector, dim, ready
        self.streams: list[FakeStream] = []
        self.computes = 0

    def create_stream(self) -> FakeStream:
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream: FakeStream) -> bool:
        assert stream.finished and stream.rate == 16000
        return self.ready

    def compute(self, stream: FakeStream) -> Any:
        assert stream is self.streams[-1] and stream.finished
        self.computes += 1
        if isinstance(self.vector, Exception):
            raise self.vector
        return self.vector


class SpeakerVectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="gm-speaker-vectors-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "original.wav"
        self.pcm = ((np.arange(24 * 16000, dtype=np.int32) % 60001) - 30000).astype("<i2")
        self.write_pcm(self.pcm.tobytes())

    def write_pcm(self, raw: bytes, *, channels: int = 1, rate: int = 16000, width: int = 2) -> None:
        with wave.open(str(self.path), "wb") as audio:
            audio.setparams((channels, width, rate, 0, "NONE", "not compressed"))
            audio.writeframes(raw)

    def encoder(self, vector: Any, *, dim: Any = 3, ready: bool = True) -> tuple[Any, FakeExtractor]:
        # Constructor bypass is synthetic-only; no readiness/license patching.
        encoder = object.__new__(speech.LocalSpeakerEncoder)
        extractor = FakeExtractor(vector, dim, ready)
        encoder.extractor = extractor
        return encoder, extractor

    def reject(self, vector: Any, *, dim: Any = 3) -> None:
        encoder, _ = self.encoder(vector, dim=dim)
        with self.assertRaises(speech.SpeechUnavailable) as caught:
            encoder.embedding(self.path, 0, 0.1)
        self.assertEqual(str(caught.exception), INVALID)
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_empty(self) -> None:
        self.reject([])

    def test_nonfinite_components(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest():
                self.reject([value, 1.0, 2.0])

    def test_zero_norm(self) -> None:
        self.reject([0.0, -0.0, 0.0])

    def test_nested_and_scalar(self) -> None:
        for value in ([[1.0], [2.0], [3.0]], np.ones((3, 1)), np.array(1.0), 1.0):
            with self.subTest():
                self.reject(value)

    def test_strings_and_booleans(self) -> None:
        cases: tuple[Any, ...] = (["1", 2, 3], [True, 2, 3], [np.bool_(False), 2, 3],
                                 np.ones(3, dtype=bool), np.array(["1", "2", "3"]), "123")
        for value in cases:
            with self.subTest():
                self.reject(value)

    def test_non_numeric(self) -> None:
        cases: tuple[Any, ...] = ([None, 1, 2], [object(), 1, 2], [1j, 1, 2], {0: 1, 1: 2, 2: 3})
        for value in cases:
            with self.subTest():
                self.reject(value)

    def test_dimension_mismatch(self) -> None:
        for value in ([1.0], [1.0] * 4, np.ones(100000)):
            with self.subTest():
                self.reject(value)

    def test_invalid_declared_dimension(self) -> None:
        for dim in (0, -1, True, 3.0, "3", None):
            with self.subTest():
                self.reject([1.0, 2.0, 3.0], dim=dim)

    def test_dimension_stays_stable(self) -> None:
        encoder, extractor = self.encoder([1.0, 2.0, 3.0])
        self.assertEqual(encoder.embedding(self.path, 0, 0.1), [1.0, 2.0, 3.0])
        extractor.dim, extractor.vector = 2, [1.0, 2.0]
        with self.assertRaisesRegex(speech.SpeechUnavailable, "^speaker_embedding_invalid:"):
            encoder.embedding(self.path, 0, 0.1)

    def test_unrepresentable_norm_or_component(self) -> None:
        self.reject([sys.float_info.max] * 3)
        self.reject([10 ** 1000, 1, 2])

    def test_native_error_is_static_and_suppressed(self) -> None:
        self.reject(RuntimeError("native private/model.onnx " + "x" * 10000))

    def test_valid_float32_unchanged_native_floats(self) -> None:
        original = np.array([0.1, -0.25, 3.5], dtype=np.float32)
        before = original.tobytes()
        encoder, _ = self.encoder(original)
        result = encoder.embedding(self.path, 0, 0.1)
        self.assertEqual(result, [float(x) for x in original])
        self.assertTrue(all(type(x) is float for x in result))
        self.assertEqual(original.tobytes(), before)

    def test_no_arbitrary_norm_threshold_or_rescaling(self) -> None:
        # hypot avoids both squared-norm underflow and intermediate overflow.
        for original in ([5e-324, 0.0, 0.0], [1e-200, -1e-200, 0.0],
                         [1e308, 0.0, 0.0], [1, -2, 0], (0.5, 0.0, -2.0)):
            with self.subTest():
                encoder, _ = self.encoder(original)
                result = encoder.embedding(self.path, 0, 0.1)
                self.assertEqual(result, list(original))
                self.assertTrue(all(type(x) is float for x in result))
                self.assertGreater(math.hypot(*result), 0)
                self.assertTrue(math.isfinite(math.hypot(*result)))

    def test_model_dimension_not_hardcoded(self) -> None:
        for dim in (1, 192, 256):
            with self.subTest():
                original = np.full(dim, 0.125, dtype=np.float32)
                encoder, _ = self.encoder(original, dim=dim)
                self.assertEqual(encoder.embedding(self.path, 0, 0.1), original.tolist())

    def test_tiny_pcm_is_decided_by_ready_and_output_not_duration(self) -> None:
        for frames in (1, 16, 80, 160, 320, 400):
            with self.subTest():
                encoder, extractor = self.encoder([0.1, 0.2, 0.3])
                self.assertEqual(encoder.embedding(self.path, 0, frames / 16000), [0.1, 0.2, 0.3])
                self.assertEqual(len(extractor.streams[0].samples), frames)

    def test_not_ready_unchanged(self) -> None:
        encoder, extractor = self.encoder([1, 2, 3], ready=False)
        with self.assertRaisesRegex(speech.SpeechUnavailable, "^Speaker segment too short for local encoder$"):
            encoder.embedding(self.path, 0, 1 / 16000)
        self.assertEqual(extractor.computes, 0)

    def test_middle_crop_and_pcm_integrity_unchanged(self) -> None:
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        encoder, extractor = self.encoder([1.0, 2.0, 3.0])
        encoder.embedding(self.path, 0, 24)
        expected = self.pcm[2 * 16000:22 * 16000].astype(np.float32) / 32768
        np.testing.assert_array_equal(extractor.streams[0].samples, expected)
        self.assertEqual(len(extractor.streams[0].samples), 320000)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_invalid_pcm_and_windows_still_rejected_before_stream(self) -> None:
        encoder, extractor = self.encoder([1.0, 2.0, 3.0])
        for start, end in ((-1, 1), (0, 0), (math.nan, 1), (0, math.inf), (23, 25)):
            with self.subTest(), self.assertRaises(ValueError):
                encoder.embedding(self.path, start, end)
        for channels, rate, width in ((2, 16000, 2), (1, 8000, 2), (1, 16000, 1)):
            self.write_pcm(self.pcm.tobytes(), channels=channels, rate=rate, width=width)
            with self.subTest(), self.assertRaises(ValueError):
                encoder.embedding(self.path, 0, 0.1)
        self.write_pcm(self.pcm[:1600].tobytes())
        self.path.write_bytes(self.path.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, "^Truncated speech PCM$"):
            encoder.embedding(self.path, 0, 0.1)
        self.assertEqual(extractor.streams, [])


if __name__ == "__main__":
    unittest.main()