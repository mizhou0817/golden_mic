"""Focused evidence tests. Model emissions/embeddings are explicit synthetic fixtures."""
from __future__ import annotations

import math
import array
import random
import sys
import tempfile
import threading
import unittest
import wave
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from backend.speech_analysis import TaskSpeakers, adjacent_snr, speech_present, task_speaker_segments
from backend.providers.local_speech import SpeechUnavailable, ctc_word_spans, local_speech_readiness


class SpeechPolicyTests(unittest.TestCase):
    def test_or_thresholds_inclusive(self):
        self.assertTrue(speech_present(1.5, 10))
        self.assertTrue(speech_present(5, 100))
        self.assertFalse(speech_present(1.499, 10))
        self.assertFalse(speech_present(4.999, 100))

    def test_invalid_duration(self):
        for speech, duration in [(0, 0), (2, 1), (float('nan'), 1), (1, float('inf'))]:
            with self.assertRaises(ValueError):
                speech_present(speech, duration)

    def test_adjacent_not_global_quiet(self):
        # Speech at 1..2 and 4..5: different adjacent noise, same signal.
        rms = [.001] * 50 + [.01] * 50 + [.1] * 100 + [.01] * 50 + [.001] * 100 + [.04] * 50 + [.1] * 100 + [.04] * 50
        result = adjacent_snr(rms, [160] * len(rms), 16000, [(1., 2.), (4., 5.)])
        self.assertAlmostEqual(result[0], 10 * math.log10(99), places=7)
        self.assertAlmostEqual(result[1], 10 * math.log10(5.25), places=7)

    def test_no_noise_unknown_not_inf(self):
        for levels in ([0.] * 50 + [.1] * 100 + [0.] * 50, [.1] * 200):
            result = adjacent_snr(levels, [160] * 200, 16000, [(0.5, 1.5)])
            self.assertIsNone(result[0])

    def test_excludes_all_other_speech(self):
        self.assertEqual(adjacent_snr([.1] * 200, [160] * 200, 16000, [(0., 1.), (1., 2.)]), [None, None])

    def test_offset_and_partial_frames(self):
        result = adjacent_snr([.01] * 10 + [.1] * 10 + [.01] * 10, [160] * 29 + [37], 16000,
                              [(2.1, 2.2)], offset=2.)
        self.assertAlmostEqual(result[0], 10 * math.log10(99), places=7)

    def test_roundoff_bound_not_a_low_positive_snr_cutoff(self):
        noise = .1
        # A one-ULP RMS change is inside the propagated measurement error;
        # sixteen ULPs are resolvable, even at roughly -144 dB excess/noise.
        for steps in (1, 16, 1024):
            signal = noise + steps * math.ulp(noise)
            result = adjacent_snr([noise] * 10 + [signal] * 20 + [noise] * 20,
                                  [320] * 50, 16000, [(.2, .6)])[0]
            with self.subTest(steps=steps):
                if steps == 1:
                    self.assertIsNone(result)
                else:
                    exact = (Fraction(signal) ** 2 - Fraction(noise) ** 2) / Fraction(noise) ** 2
                    self.assertIsNotNone(result)
                    assert result is not None
                    self.assertLess(result, 0)
                    self.assertAlmostEqual(result, 10 * math.log10(float(exact)), places=12)

    def test_loud_long_prefix_cannot_forge_or_erase_local_excess(self):
        # 20,000 remote full-scale frames must have no numerical influence.
        # Even tiny, signed-32-bit-resolution local evidence stays measurable.
        for signal in (48 / 2**31, 49 / 2**31):
            levels = [1.] * 20000 + [48 / 2**31] * 50 + [signal] * 100 + [48 / 2**31] * 50
            result = adjacent_snr(levels, [160] * len(levels), 16000, [(200.5, 201.5)])[0]
            if signal == 48 / 2**31:
                self.assertIsNone(result)
            else:
                self.assertIsNotNone(result)
                self.assertAlmostEqual(result, 10 * math.log10((49**2 - 48**2) / 48**2), places=12)

    def test_real_int32_pcm_gain_and_variable_frame_counts(self):
        # Independent WAV decoding into exact Python integer squares. Uploads
        # intentionally normalizes to int16; this is the shared RMS API's
        # int32 precision contract, NOT a claim uploads accepts 32-bit WAVs.
        sizes = [160, 320, 480, 160, 320, 480]
        for gain in (1, 2, 8):
            for increment in (0, 1):
                amplitudes = [2**27, 2**27, 2**27 + increment, 2**27 + increment, 2**27, 2**27]
                values = array.array('i', (v for a, n in zip(amplitudes, sizes)
                                          for v in [a * gain, -a * gain] * (n // 2)))
                self.assertEqual(values.itemsize, 4)
                if sys.byteorder != 'little':
                    values.byteswap()
                levels = []
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'int32.wav'
                    with wave.open(str(path), 'wb') as audio:
                        audio.setparams((1, 4, 16000, sum(sizes), 'NONE', 'not compressed'))
                        audio.writeframes(values.tobytes())
                    with wave.open(str(path), 'rb') as audio:
                        self.assertEqual(audio.getnframes(), sum(sizes))
                        for n in sizes:
                            decoded = array.array('i')
                            decoded.frombytes(audio.readframes(n))
                            if sys.byteorder != 'little':
                                decoded.byteswap()
                            self.assertEqual(len(decoded), n)
                            self.assertEqual(sum(decoded), 0)
                            levels.append(math.sqrt(sum(int(v)**2 for v in decoded) / n) / 2**31)
                        self.assertEqual(audio.readframes(1), b'')
                result = adjacent_snr(levels, sizes, 16000, [(.28, .32)], offset=.25)[0]
                with self.subTest(gain=gain, increment=increment):
                    if not increment:
                        self.assertIsNone(result)
                    else:
                        self.assertIsNotNone(result)
                        assert result is not None
                        self.assertLess(result, 0)
                        expected = 10 * math.log10(((2**27 + 1)**2 - (2**27)**2) / (2**27)**2)
                        self.assertAlmostEqual(result, expected, places=10)

    def test_variable_frames_match_independent_exact_interval_oracle(self):
        rng = random.Random(92871)
        for case in range(40):
            sizes = [rng.choice([7, 13, 32, 57]) for _ in range(100)]
            levels = [rng.choice([.001, .01, .1, .23]) for _ in sizes]
            starts, ends, cursor = [], [], 0
            for n in sizes:
                starts.append(.125 + cursor / 1000)
                cursor += n
                ends.append(.125 + cursor / 1000)
            spans = [(starts[20], ends[31]), (starts[35] + .001, ends[51] - .001),
                     (starts[48], ends[64]), (starts[78], ends[87])]
            expected = []
            for lo, hi in spans:
                speech = [i for i in range(100) if starts[i] >= lo and ends[i] <= hi]
                noise = [i for i in range(100)
                         if (lo - .5 <= starts[i] and ends[i] <= lo or hi <= starts[i] and ends[i] <= hi + .5)
                         and all(ends[i] <= a or starts[i] >= b for a, b in spans)]
                def mean(indices):
                    return sum((Fraction(levels[i])**2 * sizes[i] for i in indices), Fraction()) / sum(sizes[i] for i in indices)
                if not speech or sum(sizes[i] for i in noise) < 60:
                    expected.append(None)
                else:
                    excess = mean(speech) - mean(noise)
                    expected.append(10 * math.log10(float(excess / mean(noise))) if excess > 0 else None)
            actual = adjacent_snr(levels, sizes, 1000, spans, offset=.125)
            with self.subTest(case=case):
                for value, reference in zip(actual, expected):
                    if reference is None:
                        self.assertIsNone(value)
                    else:
                        self.assertAlmostEqual(value, reference, places=11)

    def test_repeated_spans_read_frames_only_linearly(self):
        class Counted(list):
            reads = 0
            def __iter__(self):
                for value in super().__iter__():
                    self.reads += 1
                    yield value
            def __getitem__(self, index):
                self.reads += 1
                return super().__getitem__(index)
        levels = Counted([.01] * 25 + [.1] * 50 + [.01] * 25)
        result = adjacent_snr(levels, [320] * 100, 16000, [(.5, 1.5)] * 4096)
        self.assertEqual(len(result), 4096)
        self.assertLessEqual(levels.reads, 2 * len(levels))
        for value in result:
            self.assertAlmostEqual(value, 10 * math.log10(99), places=12)

    def test_task_clustering_first_seen_and_threshold(self):
        speakers = TaskSpeakers()
        self.assertEqual(speakers.assign([1, 0])[0], 'spk_1')
        self.assertEqual(speakers.assign([.75, math.sqrt(1 - .75**2)])[0], 'spk_1')
        self.assertEqual(speakers.assign([0, 1])[0], 'spk_2')
        self.assertEqual(speakers.assign([1, 0])[0], 'spk_1')
        self.assertEqual(TaskSpeakers().assign([0, 1])[0], 'spk_1')

    def test_invalid_embeddings(self):
        speakers = TaskSpeakers()
        for vector in ([], [0, 0], [float('nan'), 1]):
            with self.assertRaises(ValueError):
                speakers.assign(vector)
        speakers.assign([1, 0])
        with self.assertRaises(ValueError):
            speakers.assign([1, 0, 0])

    def test_cross_file_actual_adapter_calls_not_names(self):
        calls = []
        class FixtureEncoder:
            def embedding(self, path, start, end):
                calls.append((path, start, end))
                return [1, 0] if path in ('a', 'c') else [0, 1]
        segment = SimpleNamespace(start=0., end=2., id='s')
        result = task_speaker_segments([(p, p, [segment]) for p in ('a', 'b', 'c')], FixtureEncoder())
        self.assertEqual([a['speaker_id'] for a in result['assignments']], ['spk_1', 'spk_2', 'spk_1'])
        self.assertEqual(len(calls), 3)

    def test_readiness_precise_and_no_import_download(self):
        with patch('importlib.util.find_spec', return_value=None):
            state = local_speech_readiness('forced_alignment')
        self.assertFalse(state.available)
        self.assertFalse(state.inference_verified)
        self.assertIn('missing_runtime:torch', state.blocked_prerequisites)
        self.assertIn('missing_local_ctc_model_and_tokenizer', state.blocked_prerequisites)
        self.assertIn('model_license_and_language_not_reviewed', state.blocked_prerequisites)

    def test_ctc_genuine_emission_path_with_repeated_token(self):
        probabilities = np.full((7, 3), .005)
        for frame, token in enumerate([0, 1, 1, 0, 1, 2, 0]):
            probabilities[frame, token] = .99
        result = ctc_word_spans(np.log(probabilities), [1, 1, 2], 0, .7)
        np.testing.assert_allclose([(a, b) for a, b, _ in result], [(.1, .3), (.4, .5), (.5, .6)])

    def test_ctc_missing_or_low_acoustic_evidence_blocks(self):
        with self.assertRaises(SpeechUnavailable):
            ctc_word_spans(np.log(np.array([[.99, .005, .005]] * 6)), [1, 2], 0, .6)
        with self.assertRaises(SpeechUnavailable):
            ctc_word_spans(np.log(np.array([[.01, .99]])), [1, 1], 0, .1)

    def test_upload_editorial_words_preserve_precision_and_local_snr(self):
        from backend.uploads import _Manifest, _Probe, _Wave, _editorial_transcript
        from backend.providers.asr import ASRTranscript
        record = _Manifest(id='up_' + 'a'*32, name='a.mp4', size=1, sha256='0'*64, token_hash='0'*64,
                           owner_hash='0'*64, content_type='video/mp4', created_at=1, expires_at=2,
                           probe=_Probe(sec=2., width=320, height=180, fps=30., has_audio=True, format_name='mov'))
        transcript = ASRTranscript.model_validate({'text': 'hello', 'duration_ms': 2000, 'utterances': [
            {'text': 'hello', 'start_time_ms': 503, 'end_time_ms': 1497, 'definite': True,
             'words': [{'text': 'hello', 'start_time_ms': 503, 'end_time_ms': 1497}]}]})
        waveform = _Wave([.01]*25 + [.1]*50 + [.01]*25, [320]*100, 32000, [], True, 'fixture')
        result = _editorial_transcript(record, transcript, waveform, threading.Event())
        self.assertEqual(result.segments[0].words[0].s, .503)
        self.assertEqual(result.segments[0].words[0].e, 1.497)
        self.assertIn('adjacent', result.snr_method)
        self.assertIsNotNone(result.segments[0].snr_db)

    def test_punctuation_gap(self):
        from backend.tts_pipeline import mode_narration_gap
        self.assertEqual(mode_narration_gap('clause,'), .25)
        self.assertEqual(mode_narration_gap('sentence.”'), .4)
        self.assertEqual(mode_narration_gap('stripped', terminal_punctuation='。'), .4)
        self.assertEqual(mode_narration_gap('last.', final=True), 0)

    def test_subthreshold_speech_not_admitted_to_broll(self):
        from backend.mode_pipeline import broll_source_intervals
        snapshot = {'id': 'u', 'sec': 10., 'has_speech': False, 'transcript': [],
                    'speech_intervals': [[1., 2.]], 'silences': []}
        clock = {'prepared_start': 0., 'prepared_end': 10.}
        self.assertEqual(broll_source_intervals(snapshot, clock, 'original'), [(0., 1.), (2., 10.)])

    def test_actual_pcm_vad_or_rule_keeps_subthreshold_ranges(self):
        import wave
        from backend.uploads import _analyze_pcm
        class FixtureVAD:
            def __init__(self, rate):
                self.index = 0
            def process(self, frame):
                self.index += 1
                return .9 if self.index <= 4 else .0
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pcm.wav'
            with wave.open(str(path), 'wb') as audio:
                audio.setparams((1, 2, 16000, 16000, 'NONE', 'not compressed'))
                audio.writeframes(np.full(16000, 200, dtype='<i2').tobytes())
            with patch('silero_vad_lite.SileroVAD', FixtureVAD):
                result = _analyze_pcm(path, 0., 2., True, .2, 32, threading.Event())
            self.assertFalse(result.has_speech)  # 128ms < 15%, <5sec.
            self.assertEqual(result.speech_intervals[0], (0., .128))
            self.assertEqual(result.speech_intervals[-1], (.992, 1.))

    def test_stage_cache_hashes_and_uncertain_provider_fence(self):
        from backend.mode_pipeline import ModeStageCache, ModeQualityError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / 'unit.wav'
            audio.write_bytes(b'fixture')
            cache = ModeStageCache(root)
            self.assertIsNone(cache.load(7, 'key'))
            cache.begin(7, 'key')
            with self.assertRaises(ModeQualityError):
                cache.load(7, 'key')
            cache.save(7, 'key', {'value': 1}, ['unit.wav'])
            self.assertEqual(cache.load(7, 'key'), {'value': 1})
            self.assertIsNone(cache.load(7, 'different'))
            audio.write_bytes(b'tampered')
            with self.assertRaises(ModeQualityError):
                cache.load(7, 'key')