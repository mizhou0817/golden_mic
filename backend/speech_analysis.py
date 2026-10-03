"""Bounded, local speech evidence; no network, settings or application imports.

Speaker IDs belong to one task, in source-order/first-seen order. Names never
participate in acoustic clustering. Missing evidence is unknown, not silence.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from typing import Any


SPEECH_RECIPE = "speech-v3-exact-energy-adjacent-noise-embedding075"


def speech_present(speech_seconds: float, duration_seconds: float) -> bool:
    """The two inclusive thresholds are alternatives, NOT a conjunction."""
    if not all(math.isfinite(v) for v in (speech_seconds, duration_seconds)) or not 0 <= speech_seconds <= duration_seconds or duration_seconds <= 0:
        raise ValueError("Invalid measured speech duration")
    return speech_seconds >= 5.0 or speech_seconds / duration_seconds >= 0.15


def adjacent_snr(
    rms: Sequence[float], counts: Sequence[int], sample_rate: int,
    spans: Sequence[tuple[float, float]], *, offset: float = 0.0,
    window_seconds: float = 0.5, minimum_noise_seconds: float = 0.06,
) -> list[float | None]:
    """Estimate each segment's SNR from adjacent non-speech PCM only.

    Whole frames only; exclude ALL speech spans, not just the current speaker.
    Do not cherry-pick globally quiet samples or impose a -50dB noise ceiling.
    Digital zero, insufficient evidence and signal<=noise remain unmeasurable.
    Exact fixed-point prefixes avoid subtracting rounded, possibly very loud
    history. Binary64 RMS values have power-of-two denominators; use the largest
    denominator as a common unit and square in integers. Prefix accumulation,
    interval subtraction, sample weighting and the excess comparison are exact.

    Allow two ULPs of RMS uncertainty (covers the rounded mean-square division
    followed by sqrt in PCM analysis; power-of-two PCM normalization is exact).
    For level L and uncertainty d, |energy error| <= count*(2*L*d + d*d).
    Only report excess when signal*n - noise*count exceeds the corresponding
    propagated bound. This is scale-dependent numerical uncertainty, NOT an
    SNR/noise-floor gate. Final float conversion happens only after that test.
    No rescans/fallback: O(frames + spans*log(frames)) after merging spans, with
    O(frames) storage and integer widths bounded by the binary64 exponent range
    plus sample-count bits.
    """
    if len(rms) != len(counts) or sample_rate <= 0 or window_seconds <= 0 or minimum_noise_seconds <= 0:
        raise ValueError("Invalid PCM envelope")
    levels: list[tuple[int, int, int, int]] = []
    scale = 1
    for level, count in zip(rms, counts, strict=True):
        if not math.isfinite(level) or level < 0 or type(count) is not int or count <= 0:
            raise ValueError("Invalid PCM frame")
        numerator, denominator = level.as_integer_ratio()
        ulp, ulp_denominator = math.ulp(level).as_integer_ratio()
        scale = max(scale, denominator, ulp_denominator)
        levels.append((numerator, denominator, ulp, ulp_denominator))
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("Invalid speech span")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    starts: list[float] = []
    ends: list[float] = []
    energy, samples, noise_energy, noise_samples = [0], [0], [0], [0]
    error, noise_error = [0], [0]
    cursor = region = 0
    for (numerator, denominator, ulp, ulp_denominator), count in zip(levels, counts, strict=True):
        start, end = offset + cursor / sample_rate, offset + (cursor + count) / sample_rate
        cursor += count
        starts.append(start)
        ends.append(end)
        while region < len(merged) and merged[region][1] <= start:
            region += 1
        outside = region == len(merged) or merged[region][0] >= end
        value = numerator * (scale // denominator)
        uncertainty = 2 * ulp * (scale // ulp_denominator)
        power = value * value * count
        bound = (2 * value * uncertainty + uncertainty * uncertainty) * count
        energy.append(energy[-1] + power)
        error.append(error[-1] + bound)
        samples.append(samples[-1] + count)
        noise_energy.append(noise_energy[-1] + (power if outside else 0))
        noise_error.append(noise_error[-1] + (bound if outside else 0))
        noise_samples.append(noise_samples[-1] + (count if outside else 0))

    def interval(a: float, b: float, energies: list[int], errors: list[int], sizes: list[int]) -> tuple[int, int, int]:
        first, last = bisect_left(starts, a), bisect_right(ends, b)
        return (energies[last] - energies[first], errors[last] - errors[first], sizes[last] - sizes[first]) if last > first else (0, 0, 0)

    result: list[float | None] = []
    for start, end in spans:
        signal, signal_error, count = interval(start, end, energy, error, samples)
        left, left_error, nl = interval(start - window_seconds, start, noise_energy, noise_error, noise_samples)
        right, right_error, nr = interval(end, end + window_seconds, noise_energy, noise_error, noise_samples)
        noise, n = left + right, nl + nr
        if count <= 0 or n < math.ceil(minimum_noise_seconds * sample_rate) or noise <= 0:
            result.append(None)
            continue
        excess = signal * n - noise * count
        bound = signal_error * n + (left_error + right_error) * count
        if excess <= bound:
            result.append(None)
            continue
        denominator = noise * count
        # Integer logarithms also support ratios outside binary64's float range.
        # Prefer a single division in the ordinary range for near-0dB accuracy.
        if excess.bit_length() - denominator.bit_length() in range(-1022, 1023):
            snr = 10 * math.log10(excess / denominator)
        else:
            snr = 10 * (math.log10(excess) - math.log10(denominator))
        result.append(snr)
    return result


class TaskSpeakers:
    """Deterministic acoustic clustering against first-seen anchors (no drift).

    A fresh instance is required per task. Embeddings are never derived from
    text/name/filename and never persisted in a global identity database.
    """
    def __init__(self, threshold: float = 0.75) -> None:
        if not 0.75 <= threshold <= 1:
            raise ValueError("Speaker cosine threshold must be >=0.75")
        self.threshold = threshold
        self.anchors: list[tuple[float, ...]] = []

    def assign(self, embedding: Sequence[float]) -> tuple[str, float | None]:
        vector = tuple(float(value) for value in embedding)
        norm = math.sqrt(sum(v * v for v in vector))
        if not vector or not all(math.isfinite(v) for v in vector) or not math.isfinite(norm) or norm <= 0:
            raise ValueError("Unmeasurable speaker embedding")
        vector = tuple(v / norm for v in vector)
        if self.anchors and len(vector) != len(self.anchors[0]):
            raise ValueError("Speaker embedding dimension changed")
        scores = [sum(a * b for a, b in zip(anchor, vector, strict=True)) for anchor in self.anchors]
        best = max(range(len(scores)), key=scores.__getitem__) if scores else None
        if best is not None and scores[best] >= self.threshold:
            return f"spk_{best + 1}", min(1.0, scores[best])
        self.anchors.append(vector)
        return f"spk_{len(self.anchors)}", None


def task_speaker_segments(sources: Sequence[tuple[str, Any, Sequence[Any]]], provider: Any) -> dict[str, Any]:
    """Actual local embeddings for single-speaker ASR segments in source order.

    This is segment-level diarization, not overlap separation or proof of a real
    identity. The caller must exclude mixed/overlapping speaker evidence.
    Call off the event loop; the caller owns cancellation/draining and PCM files.
    """
    clusters = TaskSpeakers()
    assignments: list[dict[str, Any]] = []
    for upload_id, audio_path, segments in sources:
        for segment in sorted(segments, key=lambda s: (s.start, s.end, s.id)):
            vector = provider.embedding(audio_path, segment.start, segment.end)
            speaker, similarity = clusters.assign(vector)
            assignments.append({"upload_id": upload_id, "segment_id": segment.id,
                                "speaker_id": speaker, "cosine": similarity})
    return {"recipe": SPEECH_RECIPE, "scope": "task", "threshold": 0.75,
            "method": "local_segment_speaker_embeddings", "overlap_separation": False,
            "assignments": assignments, "speaker_count": len(clusters.anchors)}