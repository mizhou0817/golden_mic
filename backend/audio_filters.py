"""Fixed local DSP constants, with no renderer, pipeline or provider imports.

Callers must resample to 48 kHz BEFORE this chain (and after any speed change).
It contains only AFFTDN and its algorithmic clock compensation, not a high-pass,
gain normalizer, silence trimmer or an authored-duration repair.
"""
import math
from collections.abc import Sequence
from typing import Final


AFFTDN_SAMPLE_RATE: Final[int] = 48_000
# libavfilter/af_afftdn.c (output_frame): window_length = 3 * (rate / 80),
# content delay = 2 * (rate / 80). Bare afftdn preserves the sample count but
# does not flush the delayed content at EOF; equal WAV lengths do not prove
# that the onset and final samples survived.
AFFTDN_DELAY_SAMPLES: Final[int] = 2 * (AFFTDN_SAMPLE_RATE // 80)
# Delay compensation alone leaves the first window without prior overlap.
# The documented 48 kHz / 880 Hz regression lost ~38% of the first 37 samples'
# amplitude. A fixed silent pre-roll warms the overlap-add state. Discard it
# together with the algorithmic delay, never with a desired-length end trim.
AFFTDN_PREROLL_SAMPLES: Final[int] = AFFTDN_DELAY_SAMPLES
AFFTDN_CLOCK_FILTER: Final[str] = (
    f"adelay={AFFTDN_PREROLL_SAMPLES}S:all=1,"
    f"apad=pad_len={AFFTDN_DELAY_SAMPLES},"
    # Fixed white-noise floor and denoised output, not input/noise output.
    # tn=0 disables floor tracking, NOT adaptive per-bin gains (ad=0.5).
    "afftdn=nr=12:nf=-50:nt=w:rf=-38:tn=0:tr=0:om=o:ad=0.5:gs=0,"
    f"atrim=start_sample={AFFTDN_PREROLL_SAMPLES + AFFTDN_DELAY_SAMPLES},"
    "asetpts=PTS-STARTPTS"
)


def speech_duck_filter(intervals: Sequence[tuple[float, float]], *, reduction_db: float = 18.0, transition: float = 0.3) -> str:
    """Measured speech-clock gain envelope, exact -18dB plateau, 300ms ramps.

    Attack finishes at speech onset; release starts at speech end. Overlapping
    ramps use the strongest attenuation, never compounded gains. This is not
    an amplitude-dependent compressor falsely labelled '18dB ducking'.
    """
    if len(intervals) > 2048 or not math.isfinite(reduction_db) or not 0 <= reduction_db <= 60 or not math.isfinite(transition) or transition <= 0:
        raise ValueError("Invalid bounded duck envelope")
    envelopes = []
    for start, end in intervals:
        if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
            raise ValueError("Invalid speech clock")
        attack = f"clip((t-({start:.12f}-{transition:.12f}))/{transition:.12f},0,1)"
        release = f"clip(({end:.12f}+{transition:.12f}-t)/{transition:.12f},0,1)"
        envelopes.append(f"min({attack},{release})")
    if not envelopes:
        return "anull"
    # Balanced expression avoids pathological expression-parser recursion.
    while len(envelopes) > 1:
        envelopes = [f"max({envelopes[i]},{envelopes[i + 1]})" if i + 1 < len(envelopes) else envelopes[i]
                     for i in range(0, len(envelopes), 2)]
    return f"volume='pow(10,-{reduction_db:.6f}*({envelopes[0]})/20)':eval=frame"