"""Opt-in, offline local speech adapters. Never install or download anything.

Speaker encoder: sherpa-onnx SpeakerEmbeddingExtractor (Apache-2.0 runtime).
Alignment: local Hugging Face CTC model (transformers Apache-2.0, torch BSD).
Model weights/vocabulary/language have independent licenses: an operator must
provide a reviewed local directory/file and explicitly acknowledge its license.
Readiness is prerequisites only, NOT a claim of acoustic quality or inference.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np


class SpeechUnavailable(RuntimeError):
    error_kind = "speech_prerequisite"


@dataclass(frozen=True)
class LocalSpeechReadiness:
    available: bool
    capability: str
    blocked_prerequisites: tuple[str, ...]
    runtime_license: str
    model_license_reviewed: bool
    inference_verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def local_speech_readiness(capability: str, model_path: Path | None = None, *, license_reviewed: bool = False) -> LocalSpeechReadiness:
    packages: dict[str, tuple[str, ...]] = {"speaker_embedding": ("sherpa_onnx",), "forced_alignment": ("torch", "transformers")}
    if capability not in packages:
        raise ValueError("Unknown local speech capability")
    blocked = [f"missing_runtime:{package}" for package in packages[capability] if importlib.util.find_spec(package) is None]
    if model_path is None or not (model_path.is_file() if capability == "speaker_embedding" else model_path.is_dir()):
        blocked.append("missing_local_speaker_onnx" if capability == "speaker_embedding" else "missing_local_ctc_model_and_tokenizer")
    if not license_reviewed:
        blocked.append("model_license_and_language_not_reviewed")
    return LocalSpeechReadiness(not blocked, capability, tuple(blocked),
                                "Apache-2.0" if capability == "speaker_embedding" else "Apache-2.0 / BSD-3-Clause",
                                license_reviewed)


def _require(capability: str, model: Path | None, reviewed: bool) -> None:
    state = local_speech_readiness(capability, model, license_reviewed=reviewed)
    if not state.available:
        raise SpeechUnavailable(capability + " blocked: " + ", ".join(state.blocked_prerequisites))


def _pcm(path: Path, start: float, end: float) -> np.ndarray:
    if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end or end - start > 30:
        raise ValueError("Local speech windows must be finite and <=30 seconds")
    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError("Local speech requires original-clock PCM16k mono s16le")
        first, last = math.ceil(start * 16000), math.floor(end * 16000)
        if not 0 <= first < last <= audio.getnframes():
            raise ValueError("Speech window exceeds actual PCM; no padding allowed")
        audio.setpos(first)
        data = audio.readframes(last - first)
        if len(data) != (last - first) * 2:
            raise ValueError("Truncated speech PCM")
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768


class LocalSpeakerEncoder:
    def __init__(self, model: Path, *, license_reviewed: bool = False) -> None:
        _require("speaker_embedding", model, license_reviewed)
        runtime = importlib.import_module("sherpa_onnx")
        config = runtime.SpeakerEmbeddingExtractorConfig(model=str(model.resolve()), num_threads=1, debug=False, provider="cpu")
        if not config.validate():
            raise SpeechUnavailable("Invalid local speaker ONNX model/configuration")
        self.extractor = runtime.SpeakerEmbeddingExtractor(config)

    def embedding(self, audio_path: Path, start: float, end: float) -> list[float]:
        # Bound long utterances to a real middle window, never fabricate samples.
        if end - start > 20:
            start += (end - start - 20) / 2
            end = start + 20
        samples = _pcm(audio_path, start, end)
        stream = self.extractor.create_stream()
        stream.accept_waveform(16000, samples)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            raise SpeechUnavailable("Speaker segment too short for local encoder")
        try:
            vector = self.extractor.compute(stream)
            # sherpa's dim is an integer property, not a method. Bound conversion
            # by the model's dimension; do not materialize arbitrary iterables.
            dimension = self.extractor.dim
            if type(dimension) is not int or dimension <= 0:
                raise ValueError
            if getattr(self, "_embedding_dim", dimension) != dimension:
                raise ValueError
            if not isinstance(vector, (list, tuple, np.ndarray)):
                raise ValueError
            if isinstance(vector, np.ndarray) and vector.ndim != 1:
                raise ValueError
            values = cast(list[Any] | tuple[Any, ...] | np.ndarray[Any, Any], vector)
            if len(values) != dimension:
                raise ValueError
            result: list[float] = []
            for value in values:
                # float() alone would silently accept strings and booleans.
                if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
                    raise ValueError
                converted = float(cast(Any, value))
                if not math.isfinite(converted):
                    raise ValueError
                result.append(converted)
            # Stable Euclidean norm: no squared-sum underflow/overflow and no
            # arbitrary small-vector threshold. Never rescale a valid vector.
            norm = math.hypot(*result)
            if not math.isfinite(norm) or norm == 0:
                raise ValueError
        except Exception:
            # Native errors may contain model paths or huge output payloads.
            raise SpeechUnavailable("speaker_embedding_invalid: local encoder produced no usable vector") from None
        self._embedding_dim = dimension
        return result


def ctc_word_spans(log_probs: np.ndarray, tokens: list[int], blank: int, duration: float, *, minimum_probability: float = 0.5,
                   frame_stride_seconds: float | None = None, frame_offset_seconds: float = 0.0) -> list[tuple[float, float, float]]:
    """Monotonic CTC Viterbi alignment against REAL model emissions, not LCS.

    Every token needs nonblank acoustic evidence; no interpolation for missing
    tokens. Trellis is bounded to 4M states; low confidence fails explicitly.
    """
    frames, vocabulary = log_probs.shape
    stride = duration / frames if frames and frame_stride_seconds is None else frame_stride_seconds
    if stride is None or not math.isfinite(stride) or stride <= 0 or not math.isfinite(frame_offset_seconds) or frame_offset_seconds < 0:
        raise SpeechUnavailable("Invalid CTC acoustic frame clock")
    if not tokens or frames <= 0 or frames * (2 * len(tokens) + 1) > 4_000_000 or not 0 < duration <= 30:
        raise SpeechUnavailable("CTC alignment input exceeds bounded trellis or is empty")
    if not np.isfinite(log_probs).all() or any(t == blank or not 0 <= t < vocabulary for t in tokens) or not 0 <= blank < vocabulary:
        raise SpeechUnavailable("CTC tokens/emissions are invalid")
    labels = [blank]
    for token in tokens:
        labels.extend((token, blank))
    states = len(labels)
    previous: list[float] = [-math.inf] * states
    previous[0] = log_probs[0, blank]
    previous[1] = log_probs[0, tokens[0]]
    back = np.zeros((frames, states), dtype=np.int8)
    for frame in range(1, frames):
        current: list[float] = [-math.inf] * states
        for state, label in enumerate(labels):
            choices = [(previous[state], 0)]
            if state:
                choices.append((previous[state - 1], 1))
            if state > 1 and label != blank and label != labels[state - 2]:
                choices.append((previous[state - 2], 2))
            score, step = max(choices, key=lambda pair: pair[0])
            current[state] = score + float(log_probs[frame, label])
            back[frame, state] = step
        previous = current
    state = states - 1 if previous[-1] >= previous[-2] else states - 2
    if not np.isfinite(previous[state]):
        raise SpeechUnavailable("No complete acoustic CTC path")
    path: list[int] = []
    for frame in range(frames - 1, -1, -1):
        path.append(state)
        state -= int(back[frame, state])
    path.reverse()
    result: list[tuple[float, float, float]] = []
    for index, token in enumerate(tokens):
        observed = [frame for frame, state in enumerate(path) if state == 2 * index + 1]
        if not observed:
            raise SpeechUnavailable("CTC token has no acoustic frames")
        confidence = float(np.exp(np.mean(log_probs[observed, token])))
        if confidence < minimum_probability:
            raise SpeechUnavailable("CTC token acoustic confidence below threshold")
        start = frame_offset_seconds + observed[0] * stride
        end = min(duration, frame_offset_seconds + (observed[-1] + 1) * stride)
        if start >= end:
            raise SpeechUnavailable("CTC frame exceeds actual audio clock")
        result.append((start, end, confidence))
    return result


class LocalForcedAligner:
    def __init__(self, model: Path, *, license_reviewed: bool = False) -> None:
        _require("forced_alignment", model, license_reviewed)
        library = importlib.import_module("transformers")
        self.torch = importlib.import_module("torch")
        # Local-only, no custom code, no pickle weight loading, no auto-download.
        self.processor = library.AutoProcessor.from_pretrained(str(model.resolve()), local_files_only=True, trust_remote_code=False)
        self.model = library.AutoModelForCTC.from_pretrained(str(model.resolve()), local_files_only=True, trust_remote_code=False, use_safetensors=True).eval()

    def align(self, audio: Path, text: str, start: float, end: float) -> list[dict[str, Any]]:
        samples = _pcm(audio, start, end)
        # Character vocabulary models only: reject unrepresentable punctuation
        # and scripts rather than silently relabeling approximate word clocks.
        vocabulary = self.processor.tokenizer.get_vocab()
        characters = [c for c in text if c.isalnum()]
        if not characters or any(c not in vocabulary for c in characters):
            raise SpeechUnavailable("Local CTC vocabulary does not cover spoken characters/language")
        inputs = self.processor(samples, sampling_rate=16000, return_tensors="pt")
        with self.torch.inference_mode():
            logits = self.model(**inputs).logits[0].log_softmax(-1).cpu().numpy()
        # Map convolution frames to their real receptive-field centers, not
        # uniform text slices or an inferred duration/number-of-characters clock.
        kernels, strides = getattr(self.model.config, "conv_kernel", None), getattr(self.model.config, "conv_stride", None)
        if not kernels or not strides or len(kernels) != len(strides):
            raise SpeechUnavailable("Local CTC model lacks verified convolution frame geometry")
        receptive, jump = 1, 1
        for kernel, step in zip(kernels, strides, strict=True):
            receptive += (int(kernel) - 1) * jump
            jump *= int(step)
        spans = ctc_word_spans(logits, [vocabulary[c] for c in characters], self.model.config.pad_token_id, len(samples) / 16000,
                               frame_stride_seconds=jump / 16000, frame_offset_seconds=max(0, receptive - jump) / 32000)
        actual_start = math.ceil(start * 16000) / 16000
        return [{"w": c, "s": actual_start + s, "e": actual_start + e, "confidence": confidence,
                 "method": "local_ctc_acoustic_alignment"} for c, (s, e, confidence) in zip(characters, spans, strict=True)]