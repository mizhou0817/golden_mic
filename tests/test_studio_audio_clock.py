"""Studio AFFTDN clock contracts and real, local WAV regressions for the main runner.

Authoring this file does not execute commands. Media cases skip ONLY if FFmpeg
or ffprobe is missing; codec/filter failures are failures, not fallback/skip.
All assets are synthetic, created in owned TEMP. No main app, Settings, dotenv,
TTS provider or HTTP transport is constructed. Command doubles abort before
execution and are not evidence of successful DSP; media cases use the actual
renderer, probe and PCM bytes with no media doubles.

WAV sample-count tolerance is ONE sample (duration-to-sample rounding), not an
AAC frame. Speed tests compare against the SAME real post-atempo none render:
WSOLA/codec EOF rounding is not repaired by padding/cropping either recording.
Independent carrier clocks, passband gain and nonzero boundary PCM prevent the
renderer's existing silent bed/duration cap from concealing lost content.
Low white-noise fixtures test AFFTDN, not removal of a 35 Hz hum by a high-pass.
These synthetic sentinels are not a speech-intelligibility or AI-repair claim.
"""
from __future__ import annotations

import array
import ast
import hashlib
import math
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, patch

from backend import studio_render
from backend.audio_filters import (
    AFFTDN_CLOCK_FILTER, AFFTDN_DELAY_SAMPLES, AFFTDN_PREROLL_SAMPLES,
    AFFTDN_SAMPLE_RATE,
)
from backend.studio_render import Clip, ExportOptions, Project, Track, TransitionIn, render_project


TOOLS = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
# Measurement policy is independent of the production filter constants.
RATE = 48_000
ODD_FRAMES = 2 * RATE + 37
ODD_DURATION = ODD_FRAMES / RATE
SAMPLE_TOLERANCE = 1
CLOCK_WINDOW = 480  # 10 ms coherent tone window, stepped at 1 ms.
CLOCK_STEP = 48
ORIGINAL_CLOCK_BOUND = 960  # Retain the original 20 ms requirement too.
MIN_CARRIER_GAIN = 0.75
PCM = tuple[tuple[float, ...], tuple[float, ...]]
Effect = Literal["none", "denoise", "limiter"]
TrackKind = Literal["audio", "video"]


def _single_project(*, effect: Effect = "denoise", duration: float = ODD_DURATION,
                    speed: float = 1, start: float = 0, trim: float = 0,
                    track_type: TrackKind = "audio") -> Project:
    return Project(tracks=[Track(id="track", type=track_type, clips=[Clip(
        id="clip", source_id="source", duration=duration, speed=speed,
        start=start, trim=trim, audio_effect=effect,
    )])])


def _with_effect(project: Project, effect: Effect) -> Project:
    data = project.model_dump()
    for track in data["tracks"]:
        for clip in track["clips"]:
            clip["audio_effect"] = effect
    return Project.model_validate(data)


def _transition_project(speed: float, audio: bool | None) -> Project:
    transition = None if audio is None else TransitionIn(
        left_clip_id="left", kind="dissolve", duration=0.8,
        easing="ease_in", audio=audio,
    )
    return Project(tracks=[Track(id="video", type="video", clips=[
        Clip(id="left", source_id="left", start=0.2, trim=0.125,
             duration=2, speed=speed, fit="cover", audio_effect="denoise"),
        Clip(id="right", source_id="right", start=1.4, trim=0.125,
             duration=2, speed=speed, fit="cover", audio_effect="denoise",
             transition_in=transition),
    ])])


def _rms(samples: Sequence[float]) -> float:
    if not samples:
        raise AssertionError("empty PCM measurement window")
    return math.sqrt(sum(value * value for value in samples) / len(samples))


def _tone_amplitude(samples: Sequence[float], frequency: float) -> float:
    if not samples:
        raise AssertionError("empty carrier measurement window")
    omega = 2 * math.pi * frequency / RATE
    real = sum(value * math.cos(omega * i) for i, value in enumerate(samples))
    imaginary = sum(value * math.sin(omega * i) for i, value in enumerate(samples))
    return 2 * math.hypot(real, imaginary) / len(samples)


def _carrier_span(samples: Sequence[float], frequency: float, level: float) -> tuple[int, int] | None:
    # Prefix projections avoid repeatedly measuring overlapping windows. Using
    # global oscillator phase changes no magnitude and does not move samples.
    real, imaginary = [0.0], [0.0]
    omega = 2 * math.pi * frequency / RATE
    for index, value in enumerate(samples):
        real.append(real[-1] + value * math.cos(omega * index))
        imaginary.append(imaginary[-1] + value * math.sin(omega * index))
    active = []
    for first in range(0, len(samples) - CLOCK_WINDOW + 1, CLOCK_STEP):
        last = first + CLOCK_WINDOW
        amplitude = 2 * math.hypot(real[last] - real[first], imaginary[last] - imaginary[first]) / CLOCK_WINDOW
        if amplitude > 0.5 * level:
            active.append(first + CLOCK_WINDOW // 2)
    return (active[0], active[-1]) if active else None


def _assert_carrier_clock(case: unittest.TestCase, before: Sequence[float], after: Sequence[float],
                          frequency: float, *, steady_start: float, steady_end: float) -> None:
    steady = slice(round(steady_start * RATE), round(steady_end * RATE))
    before_level = _tone_amplitude(before[steady], frequency)
    after_level = _tone_amplitude(after[steady], frequency)
    before_span = _carrier_span(before, frequency, before_level)
    after_span = _carrier_span(after, frequency, after_level)
    detail = (f"Studio carrier clock: hz={frequency}, counts={len(before), len(after)}, "
              f"amplitudes={before_level, after_level}, spans={before_span, after_span}")
    case.assertLessEqual(abs(len(after) - len(before)), SAMPLE_TOLERANCE, detail)
    case.assertGreater(before_level, 0.01, detail)
    case.assertGreater(after_level, MIN_CARRIER_GAIN * before_level, detail)
    if before_span is None or after_span is None:
        case.fail(detail)
    for name, original, actual in zip(("onset", "tail"), before_span, after_span, strict=True):
        shift = actual - original
        message = f"{detail}, {name} signed_shift_samples={shift}"
        case.assertLessEqual(abs(shift), ORIGINAL_CLOCK_BOUND, message)
        # Stronger than the prior 20 ms check: strictly less than a 10 ms
        # block, with 1 ms resolution. Equal-length 25 ms delays cannot pass.
        case.assertLess(abs(shift), CLOCK_WINDOW, message)


def _assert_pcm_edges(case: unittest.TestCase, before: Sequence[float], after: Sequence[float],
                      frequency: float) -> None:
    case.assertLessEqual(abs(len(after) - len(before)), SAMPLE_TOLERANCE)
    for label, section in (("first_10ms", slice(0, CLOCK_WINDOW)),
                           ("last_10ms", slice(-CLOCK_WINDOW, None))):
        reference, actual = _tone_amplitude(before[section], frequency), _tone_amplitude(after[section], frequency)
        detail = f"PCM boundary {label}: reference={reference}, actual={actual}"
        case.assertGreater(reference, 0.01, detail)
        case.assertGreater(actual, MIN_CARRIER_GAIN * reference, detail)
    # 37 samples need not contain a complete carrier cycle: measure their
    # actual energy, not a WAV header or a sinusoid-fit amplitude.
    for label, section in (("first_37", slice(0, 37)), ("last_37", slice(-37, None))):
        reference, actual = _rms(before[section]), _rms(after[section])
        detail = f"PCM boundary {label}: reference_rms={reference}, actual_rms={actual}"
        case.assertGreater(reference, 0.01, detail)
        case.assertGreater(actual, MIN_CARRIER_GAIN * reference, detail)


class StudioAudioMeasurementTests(unittest.TestCase):
    def test_clock_accepts_gain_changes_without_realigning_content(self) -> None:
        before, after = [], []
        for index in range(ODD_FRAMES):
            time = index / RATE
            tone = 0.12 * math.sin(2 * math.pi * 880 * time) if 0.4 <= time < 1.6 else 0.0
            hum = 0.04 * math.sin(2 * math.pi * 35 * time)
            before.append(tone + hum)
            after.append(0.8 * tone + 0.25 * hum)
        _assert_carrier_clock(self, before, after, 880, steady_start=0.7, steady_end=1.3)

    def test_clock_rejects_equal_count_delay_advance_clipping_and_gain_loss(self) -> None:
        before = [0.12 * math.cos(2 * math.pi * 880 * i / RATE) if 0.4 * RATE <= i < 1.6 * RATE else 0.0
                  for i in range(ODD_FRAMES)]
        lost, onset, tail = 1200, round(0.4 * RATE), round(1.6 * RATE)
        for name, after in (
            ("delay", [0.0] * lost + before[:-lost]),
            ("advance", before[lost:] + [0.0] * lost),
            ("clipped_onset", before[:onset] + [0.0] * lost + before[onset + lost:]),
            ("clipped_tail", before[:tail - lost] + [0.0] * lost + before[tail:]),
            ("gain_loss", [0.74 * value for value in before]),
        ):
            with self.subTest(corruption=name):
                self.assertEqual(len(after), len(before))
                with self.assertRaisesRegex(AssertionError, "Studio carrier clock"):
                    _assert_carrier_clock(self, before, after, 880, steady_start=0.7, steady_end=1.3)

    def test_edges_reject_silent_or_attenuated_partial_pcm_despite_equal_length(self) -> None:
        before = [0.12 * math.cos(2 * math.pi * 880 * i / RATE) for i in range(ODD_FRAMES)]
        after = [0.9 * value for value in before]
        _assert_pcm_edges(self, before, after, 880)
        for name, damaged in (
            ("zero_onset", [0.0] * 37 + after[37:]),
            ("zero_tail", after[:-37] + [0.0] * 37),
            ("cold_overlap", [0.62 * value for value in before[:37]] + after[37:]),
        ):
            with self.subTest(corruption=name), self.assertRaisesRegex(AssertionError, "PCM boundary"):
                _assert_pcm_edges(self, before, damaged, 880)


class StudioAudioFilterContractTests(unittest.TestCase):
    def test_fixed_afftdn_clock_chain_has_no_highpass_or_duration_repair(self) -> None:
        self.assertEqual(AFFTDN_SAMPLE_RATE, 48_000)
        self.assertEqual(AFFTDN_DELAY_SAMPLES, 1200)
        self.assertEqual(AFFTDN_PREROLL_SAMPLES, 1200)
        self.assertEqual(AFFTDN_CLOCK_FILTER, (
            "adelay=1200S:all=1,apad=pad_len=1200,"
            "afftdn=nr=12:nf=-50:nt=w:rf=-38:tn=0:tr=0:om=o:ad=0.5:gs=0,"
            "atrim=start_sample=2400,asetpts=PTS-STARTPTS"
        ))
        self.assertNotIn("highpass", AFFTDN_CLOCK_FILTER)
        self.assertNotIn("duration=", AFFTDN_CLOCK_FILTER)
        self.assertNotIn("end_sample=", AFFTDN_CLOCK_FILTER)

    def test_shared_constants_module_has_no_pipeline_or_renderer_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1] / "backend"
        tree = ast.parse((root / "audio_filters.py").read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                imports.append(node.module)
        self.assertEqual(imports, ["typing"])
        renderer = ast.parse((root / "studio_render.py").read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.module == "tts_pipeline"
                             for node in ast.walk(renderer)))


class _CapturedCommand(Exception):
    def __init__(self, command: list[str]):
        super().__init__("command-only capture; no media execution or success")
        self.command = command


class StudioAudioCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="studio-audio-contract-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "command-only.mkv"
        self.source.write_bytes(b"not decodable: command contract only")
        self.info = {"duration": 8.0, "streams": [
            {"codec_type": "video", "width": 160, "height": 90, "avg_frame_rate": "25/1"},
            {"codec_type": "audio", "sample_rate": "32000", "channels": 2},
        ]}

    async def command(self, project: Project) -> list[str]:
        async def capture(command: list[str], *_args: Any, **_kwargs: Any) -> bytes:
            raise _CapturedCommand(command)

        with patch.object(studio_render, "probe", new=AsyncMock(return_value=self.info)), \
                patch.object(studio_render.shutil, "which", return_value="command-only"), \
                patch.object(studio_render, "run_logged_command", side_effect=capture):
            with self.assertRaises(_CapturedCommand) as caught:
                await render_project(project, ExportOptions(format="wav"),
                                     {key: self.source for key in ("source", "left", "right")}, self.root / "out")
        self.assertFalse((self.root / "out/output.wav").exists())
        return caught.exception.command

    @staticmethod
    def graph(command: list[str]) -> str:
        return command[command.index("-filter_complex") + 1]

    async def test_none_keeps_exact_legacy_audio_graph_and_omitted_default(self) -> None:
        project = _single_project(effect="none", duration=2)
        command = await self.command(project)
        self.assertEqual(self.graph(command), (
            "anullsrc=r=48000:cl=stereo,atrim=duration=2.0[silence];"
            "[0:a:0]atrim=duration=2.0,asetpts=PTS-STARTPTS,aresample=48000,"
            "aformat=channel_layouts=stereo,volume=1.0,pan=stereo|c0=1*c0|c1=1*c1,"
            "atrim=duration=2.0,adelay=0S:all=1[a0];"
            "[silence][a0]amix=inputs=2:normalize=0:duration=longest,"
            "alimiter=limit=0.95:level=false:latency=true,atrim=duration=2.0[audio]"
        ))
        data = project.model_dump()
        del data["tracks"][0]["clips"][0]["audio_effect"]
        self.assertEqual(await self.command(Project.model_validate(data)), command)

    async def test_denoise_clock_is_post_tempo_48k_and_pre_transition_envelopes(self) -> None:
        for speed in (0.5, 1.0, 2.0):
            for audio in (False, True):
                with self.subTest(speed=speed, transition_audio=audio):
                    project = _transition_project(speed, audio)
                    command = await self.command(project)
                    graph = self.graph(command)
                    self.assertNotIn("highpass=", graph)
                    for index, envelope in ((0, "afade=t=out:st=1.2:d=0.8:curve=tri"),
                                             (1, "afade=t=in:st=0:d=0.8:curve=tri")):
                        part = next(item for item in graph.split(";") if item.startswith(f"[{index}:a:0]"))
                        self.assertIn(f"atrim=duration={2 * speed}", part)
                        if speed == 1:
                            self.assertNotIn("atempo=", part)
                            self.assertIn("aresample=48000,", part)
                        else:
                            self.assertIn(f"atempo={speed},aresample=48000,", part)
                        self.assertIn(AFFTDN_CLOCK_FILTER, part)
                        self.assertLess(part.index("aresample=48000"), part.index(AFFTDN_CLOCK_FILTER))
                        self.assertEqual(part.count("apad="), 1)
                        self.assertEqual(part.count("afftdn="), 1)
                        self.assertEqual(part.count("asetpts=PTS-STARTPTS"), 2)
                        self.assertNotIn("end_sample=", part)
                        self.assertTrue(part.endswith(f"atrim=duration=2.0,adelay={9600 if index == 0 else 67200}S:all=1[a{index}]"))
                        if audio:
                            self.assertIn(envelope, part)
                            self.assertLess(part.index(AFFTDN_CLOCK_FILTER) + len(AFFTDN_CLOCK_FILTER), part.index(envelope))
                            self.assertLess(part.index(envelope), part.rindex("adelay="))
                        else:
                            self.assertNotIn("afade=", part)
                    if not audio:
                        self.assertEqual(command, await self.command(_transition_project(speed, None)))

    async def test_opt_in_limiter_and_existing_mix_limiter_are_unchanged(self) -> None:
        graph = self.graph(await self.command(_single_project(effect="limiter", duration=2)))
        clip_audio = next(part for part in graph.split(";") if part.startswith("[0:a:0]"))
        self.assertIn("alimiter=limit=0.95:level=false,atrim=duration=2.0", clip_audio)
        self.assertNotIn("latency=true", clip_audio)
        self.assertNotIn("apad=", clip_audio)
        self.assertIn("alimiter=limit=0.95:level=false:latency=true,atrim=duration=2.0[audio]", graph)


def _ffmpeg(*arguments: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1", *arguments],
        check=True, capture_output=True, timeout=60,
    ).stdout


def _write_pcm(path: Path, rate: int, channels: int, frames: int,
               sample: Callable[[float, int], float]) -> None:
    values = array.array("h", (round(sample(index / rate, channel) * 32767)
                               for index in range(frames) for channel in range(channels)))
    if sys.byteorder != "little":
        values.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, rate, frames, "NONE", "not compressed"))
        output.writeframes(values.tobytes())


def _read_pcm(path: Path) -> PCM:
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (RATE, 2, 2, "NONE"):
            raise AssertionError("renderer must produce actual stereo 48 kHz PCM16 WAV")
        frames = source.getnframes()
        payload = source.readframes(frames)
        if frames <= 0 or len(payload) != frames * 4 or source.readframes(1):
            raise AssertionError("actual PCM data is missing or disagrees with WAV frame count")
    values = array.array("h", payload)
    if sys.byteorder != "little":
        values.byteswap()
    return tuple(value / 32768 for value in values[::2]), tuple(value / 32768 for value in values[1::2])


@unittest.skipUnless(TOOLS, "FFmpeg/ffprobe must be on PATH; media failures are never skipped")
class StudioAudioClockMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="studio-audio-clock-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source_hashes: dict[Path, bytes] = {}
        self.serial = 0

    async def asyncTearDown(self) -> None:
        for path, digest in self.source_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest, "synthetic source was modified")

    async def audio(self, label: str, *, rate: int = RATE, channels: int = 2,
                    codec: str = "pcm_s16le", duration: float = ODD_DURATION,
                    sample: Callable[[float, int], float]) -> Path:
        native = self.root / f"{label}-native.wav"
        _write_pcm(native, rate, channels, round(duration * rate), sample)
        self.source_hashes[native] = hashlib.sha256(native.read_bytes()).digest()
        path = native
        if codec != "pcm_s16le":
            path = self.root / f"{label}-{codec}.wav"
            _ffmpeg("-y", "-protocol_whitelist", "file,pipe", "-f", "wav", "-i", str(native),
                    "-map", "0:a:0", "-c:a", codec, str(path))
            self.source_hashes[path] = hashlib.sha256(path.read_bytes()).digest()
        info = await studio_render.probe(path, self.root)
        stream = next(item for item in info["streams"] if item["codec_type"] == "audio")
        self.assertEqual((stream["sample_rate"], stream["channels"], stream["codec_name"]), (str(rate), channels, codec))
        return path

    async def video(self, label: str, audio: Path, duration: float) -> Path:
        # Copy native stereo PCM into real Matroska video: no AAC encoder delay
        # or mono/stereo gain change is introduced into the clock reference.
        path = self.root / f"{label}.mkv"
        _ffmpeg("-y", "-f", "lavfi", "-i", f"color=c=blue:s=160x90:r=25:d={duration}",
                "-protocol_whitelist", "file,pipe", "-f", "wav", "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "copy", "-threads", "1", str(path))
        self.source_hashes[path] = hashlib.sha256(path.read_bytes()).digest()
        info = await studio_render.probe(path, self.root)
        self.assertEqual({stream["codec_type"] for stream in info["streams"]}, {"video", "audio"})
        self.assertEqual(next(stream for stream in info["streams"] if stream["codec_type"] == "audio")["codec_name"], "pcm_s16le")
        return path

    async def render_wav(self, project: Project, sources: dict[str, Path]) -> PCM:
        self.serial += 1
        work = self.root / f"render-{self.serial}"
        snapshot = project.model_dump()
        result = await render_project(project, ExportOptions(format="wav"), sources, work, timeout=90)
        self.assertEqual(project.model_dump(), snapshot)
        self.assertEqual(result["applied"]["format"], "wav")
        self.assertEqual([stream["codec_name"] for stream in result["probe"]["streams"]], ["pcm_s16le"])
        pcm = _read_pcm(work / result["file"])
        self.assertLessEqual(abs(len(pcm[0]) - round(project.duration * RATE)), SAMPLE_TOLERANCE)
        return pcm

    async def test_real_denoise_carrier_clock_across_source_rates_formats_and_channels(self) -> None:
        for rate, channels, codec in ((48000, 1, "pcm_s16le"), (48000, 2, "pcm_s16le"),
                                       (44100, 1, "pcm_s24le"), (32000, 2, "pcm_f32le")):
            with self.subTest(rate=rate, channels=channels, codec=codec):
                def sample(time: float, channel: int) -> float:
                    frequency = 880 if channel == 0 else 1320
                    tone = 0.12 * math.sin(2 * math.pi * frequency * time) if 0.4 <= time < 1.6 else 0.0
                    return tone + 0.04 * math.sin(2 * math.pi * 35 * time)

                source = await self.audio(f"clock-{rate}-{channels}", rate=rate, channels=channels, codec=codec, sample=sample)
                project = _single_project()
                before = await self.render_wav(_with_effect(project, "none"), {"source": source})
                after = await self.render_wav(project, {"source": source})
                for channel, frequency in enumerate((880, 880 if channels == 1 else 1320)):
                    # Do NOT claim hum removal here. This measures the carrier
                    # independently of 35 Hz energy and format rematrix gain.
                    _assert_carrier_clock(self, before[channel], after[channel], frequency, steady_start=0.7, steady_end=1.3)

    async def test_real_denoise_preserves_nonzero_onset_and_final_37_pcm_samples(self) -> None:
        for rate, channels, codec in ((48000, 1, "pcm_s16le"), (48000, 2, "pcm_s16le"),
                                       (44100, 1, "pcm_s24le"), (32000, 2, "pcm_f32le")):
            with self.subTest(rate=rate, channels=channels, codec=codec):
                source = await self.audio(
                    f"edges-{rate}-{channels}", rate=rate, channels=channels, codec=codec,
                    sample=lambda time, channel: 0.12 * math.cos(2 * math.pi * (880 if channel == 0 else 1320) * time),
                )
                project = _single_project()
                before = await self.render_wav(_with_effect(project, "none"), {"source": source})
                after = await self.render_wav(project, {"source": source})
                for channel, frequency in enumerate((880, 880 if channels == 1 else 1320)):
                    # A resampled boundary may round by one sample. Require
                    # energy in the actual 37-sample fragments, not a nonzero
                    # value at every individual sample/possible zero crossing.
                    _assert_pcm_edges(self, before[channel], after[channel], frequency)
                    self.assertLess(max(abs(value) for value in after[channel]), 0.3)

    async def test_real_none_default_matches_explicit_none_pcm(self) -> None:
        source = await self.audio("none-default", sample=lambda time, channel: (
            0.12 * math.cos(2 * math.pi * (880 if channel == 0 else 1320) * time)
        ))
        explicit = _single_project(effect="none")
        data = explicit.model_dump()
        del data["tracks"][0]["clips"][0]["audio_effect"]
        omitted = Project.model_validate(data)
        before = await self.render_wav(omitted, {"source": source})
        after = await self.render_wav(explicit, {"source": source})
        self.assertTrue(before == after, "omitted/explicit none must produce identical PCM")
        for channel, frequency in enumerate((880, 1320)):
            _assert_pcm_edges(self, before[channel], after[channel], frequency)

    async def test_real_denoise_reduces_low_white_noise_without_faking_passband_gain(self) -> None:
        generators = (random.Random(6401), random.Random(6402))

        def sample(time: float, channel: int) -> float:
            # Uniform +/-0.002 has ~-59 dBFS RMS, below the fixed -50 dB
            # floor. No hum: a 70 Hz high-pass alone cannot pass this test.
            noise = 0.002 * generators[channel].uniform(-1, 1)
            frequency = 880 if channel == 0 else 1320
            tone = 0.12 * math.sin(2 * math.pi * frequency * time) if 0.4 <= time < 1.6 else 0.0
            return tone + noise

        source = await self.audio("white-noise", sample=sample)
        project = _single_project()
        before = await self.render_wav(_with_effect(project, "none"), {"source": source})
        after = await self.render_wav(project, {"source": source})
        for channel, frequency in enumerate((880, 1320)):
            for start, end in ((0.08, 0.32), (1.75, 1.95)):
                section = slice(round(start * RATE), round(end * RATE))
                reference, actual = _rms(before[channel][section]), _rms(after[channel][section])
                self.assertGreater(reference, 8 / 32768, "fixture must contain measurable broadband noise")
                self.assertLess(actual, 0.65 * reference, f"white noise channel={channel}: before={reference}, after={actual}")
            _assert_carrier_clock(self, before[channel], after[channel], frequency, steady_start=0.7, steady_end=1.3)

    async def test_real_split_audio_and_video_keep_post_atempo_clocks_at_all_speeds(self) -> None:
        from backend.studio import Action, apply_action

        track_types: tuple[TrackKind, ...] = ("audio", "video")
        for speed in (0.5, 1.0, 2.0):
            source_duration = 0.25 + 2 * speed + 0.25

            def sample(time: float, channel: int) -> float:
                local = (time - 0.25) / speed
                return (0.12 * math.cos(2 * math.pi * (880 if channel == 0 else 1320) * time)
                        if 0.4 <= local < 1.6 else 0.0)

            audio = await self.audio(f"split-{speed}", rate=32000, duration=source_duration, sample=sample)
            video = await self.video(f"split-video-{speed}", audio, source_duration)
            for track_type in track_types:
                with self.subTest(speed=speed, track_type=track_type):
                    original = _single_project(duration=2, start=0.2, trim=0.25, speed=speed, track_type=track_type)
                    snapshot = original.model_dump()
                    split = apply_action(original, Action(expected_revision=0, op="split", clip_id="clip", at=1.2, new_id="right"))
                    self.assertEqual(original.model_dump(), snapshot)
                    left, right = split.tracks[0].clips
                    self.assertEqual((left.start, left.duration, left.trim), (0.2, 1, 0.25))
                    self.assertEqual((right.start, right.duration, right.trim), (1.2, 1, 0.25 + speed))
                    self.assertEqual((left.audio_effect, right.audio_effect), ("denoise", "denoise"))
                    sources = {"source": audio if track_type == "audio" else video}
                    before = await self.render_wav(_with_effect(split, "none"), sources)
                    after = await self.render_wav(split, sources)
                    for channel, frequency in enumerate((880, 1320)):
                        _assert_carrier_clock(self, before[channel], after[channel], frequency, steady_start=0.8, steady_end=1.0)
                        # A fresh AFFTDN instance starts at the real split.
                        # The carrier is already nonzero there; warm-up must
                        # protect it too, not only the first clip's onset.
                        first = round(1.2 * RATE)
                        reference = _rms(before[channel][first:first + 37])
                        self.assertGreater(reference, 0.01)
                        self.assertGreater(_rms(after[channel][first:first + 37]), MIN_CARRIER_GAIN * reference)

    async def test_real_denoise_transitions_keep_clocks_and_linear_audio_false_true_semantics(self) -> None:
        sources: dict[str, Path] = {}
        for label, channel, rate, frequency in (("left", 0, 32000, 880), ("right", 1, 44100, 1320)):
            audio_path = await self.audio(
                label, rate=rate, duration=4.5,
                sample=lambda time, current, audible=channel, hz=frequency: (
                    0.12 * math.cos(2 * math.pi * hz * time) if current == audible else 0.0
                ),
            )
            sources[label] = await self.video(label, audio_path, 4.5)
        for speed in (0.5, 1.0, 2.0):
            with self.subTest(speed=speed):
                rendered: dict[bool, PCM] = {}
                for transition_audio in (False, True):
                    project = _transition_project(speed, transition_audio)
                    before = await self.render_wav(_with_effect(project, "none"), sources)
                    after = await self.render_wav(project, sources)
                    rendered[transition_audio] = after
                    for channel, frequency, start, end in ((0, 880, 0.5, 0.7), (1, 1320, 2.6, 2.8)):
                        _assert_carrier_clock(self, before[channel], after[channel], frequency, steady_start=start, steady_end=end)
                overlap = await self.render_wav(_transition_project(speed, None), sources)
                self.assertTrue(rendered[False] == overlap, "audio=false must be PCM-identical to the existing denoised overlap mix")
                for progress in (0.25, 0.5, 0.75):
                    center = 1.4 + 0.8 * progress
                    section = slice(round((center - 0.05) * RATE), round((center + 0.05) * RATE))
                    for channel, frequency, expected in ((0, 880, 1 - progress), (1, 1320, progress)):
                        reference = _tone_amplitude(rendered[False][channel][section], frequency)
                        actual = _tone_amplitude(rendered[True][channel][section], frequency)
                        self.assertGreater(reference, 0.075)
                        # Nonlinear VISUAL easing must leave audio complementary
                        # and linear in post-tempo output seconds, not source time.
                        self.assertAlmostEqual(actual / reference, expected, delta=0.025)


if __name__ == "__main__":
    unittest.main()