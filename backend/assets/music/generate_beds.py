"""Reproducibly synthesize genuinely license-free news underscore beds.

Every bed is generated from pure tones with FFmpeg — there is no third-party
copyrighted material, so the output is safe to bundle and redistribute (CC0).
The beds are deliberately soft, low, and slow so they sit unobtrusively under
narration at roughly -32 LUFS after the pipeline's ducking pass.

Run from the repository root (FFmpeg must be on PATH):

    python backend/assets/music/generate_beds.py

To use higher-fidelity music, drop your own CC0/royalty-free files into this
folder and edit ``manifest.json`` (keep the schema and mood tags).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


ASSETS_DIR = Path(__file__).resolve().parent
DURATION_SECONDS = 150
SAMPLE_RATE = 48000
GENERATION_LUFS = -24.0


@dataclass(frozen=True)
class BedSpec:
    mood: str
    title: str
    frequencies: tuple[float, ...]
    tremolo_hz: float
    lowpass_hz: int
    echo_ms: int
    description: str
    tempo_bpm: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)


# Consonant, low, slow voicings. Frequencies are equal-tempered note pitches (Hz).
BED_SPECS: tuple[BedSpec, ...] = (
    BedSpec(
        mood="solemn",
        title="Solemn Underscore",
        frequencies=(110.00, 164.81, 220.00, 261.63),  # A2 E3 A3 C4 (A minor)
        tremolo_hz=0.10,
        lowpass_hz=1100,
        echo_ms=900,
        description="Low, still A-minor pad for grave or memorial news.",
        tags=("solemn", "serious", "memorial", "grave"),
    ),
    BedSpec(
        mood="neutral",
        title="Neutral Underscore",
        frequencies=(130.81, 196.00, 261.63, 329.63),  # C3 G3 C4 E4 (C major)
        tremolo_hz=0.10,
        lowpass_hz=1300,
        echo_ms=800,
        description="Calm C-major pad for general daily news.",
        tags=("neutral", "calm", "general", "daily"),
    ),
    BedSpec(
        mood="uplifting",
        title="Uplifting Underscore",
        frequencies=(146.83, 220.00, 293.66, 369.99),  # D3 A3 D4 F#4 (D major)
        tremolo_hz=0.14,
        lowpass_hz=1600,
        echo_ms=700,
        description="Brighter D-major pad for positive or human-interest news.",
        tags=("uplifting", "positive", "bright", "feature"),
    ),
    BedSpec(
        mood="tense",
        title="Tense Underscore",
        frequencies=(146.83, 220.00, 293.66, 349.23),  # D3 A3 D4 F4 (D minor)
        tremolo_hz=0.20,
        lowpass_hz=1200,
        echo_ms=650,
        description="Restless D-minor pulse for developing or urgent news.",
        tags=("tense", "urgent", "developing", "breaking"),
    ),
)


def _build_command(spec: BedSpec, output_path: Path) -> list[str]:
    command = ["ffmpeg", "-y"]
    for frequency in spec.frequencies:
        command += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate={SAMPLE_RATE}:duration={DURATION_SECONDS}",
        ]
    fade_out_start = max(0, DURATION_SECONDS - 4)
    audio_filter = (
        f"amix=inputs={len(spec.frequencies)}:normalize=1,"
        "highpass=f=60,"
        f"tremolo=f={spec.tremolo_hz}:d=0.5,"
        f"lowpass=f={spec.lowpass_hz},"
        f"aecho=0.8:0.85:{spec.echo_ms}:0.25,"
        "afade=t=in:st=0:d=4,"
        f"afade=t=out:st={fade_out_start}:d=4,"
        f"loudnorm=I={GENERATION_LUFS}:LRA=6:TP=-3,"
        f"aresample={SAMPLE_RATE}"
    )
    command += [
        "-filter_complex",
        audio_filter,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return command


def main() -> int:
    tracks: list[dict[str, object]] = []
    for spec in BED_SPECS:
        output_path = ASSETS_DIR / f"{spec.mood}_underscore.m4a"
        command = _build_command(spec, output_path)
        print(f"Generating {output_path.name} ...")
        subprocess.run(command, check=True, capture_output=True)
        tracks.append(
            {
                "file": output_path.name,
                "mood": spec.mood,
                "title": spec.title,
                "description": spec.description,
                "tempo_bpm": spec.tempo_bpm,
                "tags": list(spec.tags),
                "license": "CC0-1.0",
                "source": "Procedurally synthesized with FFmpeg (no third-party audio).",
            }
        )
    manifest = {
        "schema_version": 1,
        "note": "Royalty-free (CC0) news underscore beds. Replace files and update this manifest to use your own CC0 tracks.",
        "duration_seconds": DURATION_SECONDS,
        "tracks": tracks,
    }
    (ASSETS_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(tracks)} tracks and manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
