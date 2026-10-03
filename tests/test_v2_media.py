"""Small real FFmpeg/TEMP media, no models, services or human recordings."""
from __future__ import annotations

import json
import asyncio
import math
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch

import numpy as np

from backend.audio_filters import speech_duck_filter
from backend.graphics import generate_mode_subtitles, validate_mode_subtitle_artifacts, generate_mode_graphics
from backend.models import EditingPreferences, MatchPlanItem, SentenceTiming, TTSWordTiming


class CaptionTests(unittest.TestCase):
    def test_new_size_wrap_and_exact_word_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = 'abcdefghijklmnopqr'
            timing = SentenceTiming(sentence_id=0, text=text, audio_path='x.wav', duration=2., start=0., end=2.,
                                    words=[TTSWordTiming(text=text, start=.123456, end=1.876543)])
            plan = [MatchPlanItem(sentence_id=0, text=text, shot_id=0, confidence=1., candidates=[])]
            events = generate_mode_subtitles(root, [timing], plan, EditingPreferences(caption_style='big'))
            self.assertEqual(events[0]['start'], .123456)
            self.assertEqual(events[0]['end'], 1.876543)
            self.assertEqual(events[0]['ass_start'], .13)
            self.assertEqual(events[0]['ass_end'], 1.87)
            self.assertTrue(all(len(line) <= 14 for line in events[0]['text'].split('\n')))
            self.assertIn('Noto Sans SC,54,', (root/'subs.ass').read_text(encoding='utf-8'))
            self.assertEqual(validate_mode_subtitle_artifacts(root/'subs.ass', root/'subtitle_manifest.json')['quote_caption'], 'spoken')

    def test_old_manifest_read_without_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timing = SentenceTiming(sentence_id=0, text='hello', audio_path='x.wav', duration=2., start=0., end=2.)
            plan = [MatchPlanItem(sentence_id=0, text='hello', shot_id=0, confidence=1., candidates=[])]
            generate_mode_subtitles(root, [timing], plan, EditingPreferences(), v2=False)
            before = [(root/name).read_bytes() for name in ('subs.ass', 'subtitle_manifest.json')]
            validate_mode_subtitle_artifacts(root/'subs.ass', root/'subtitle_manifest.json')
            self.assertEqual(before, [(root/name).read_bytes() for name in ('subs.ass', 'subtitle_manifest.json')])
            self.assertIn(b'Noto Sans SC,54,', before[0])

    def test_no_endcard_over_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timing = SentenceTiming(sentence_id=0, text='hello', audio_path='x.wav', duration=3., start=0., end=3.)
            path, _ = generate_mode_graphics(root, [timing], [], [], EditingPreferences(news_graphics=True), title='headline')
            self.assertIsNotNone(path)
            self.assertFalse(any(',GfxEnd,' in line for line in path.read_text(encoding='utf-8').splitlines() if line.startswith('Dialogue:')))


class RealMediaTests(unittest.TestCase):
    def run_media(self, arguments):
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *arguments], capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))

    def test_real_duck_plateau_18db_ramps_no_sample_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root/'tone.wav', root/'duck.wav'
            rate, count = 48000, 192037
            samples = (np.sin(np.arange(count)*2*np.pi*880/rate)*8000).astype('<i2')
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((1, 2, rate, count, 'NONE', 'not compressed'))
                audio.writeframes(samples.tobytes())
            self.run_media(['-i', str(source), '-af', speech_duck_filter([(1., 2.)]), '-c:a', 'pcm_s16le', str(output)])
            with wave.open(str(output), 'rb') as audio:
                self.assertEqual(audio.getnframes(), count)
                actual = np.frombuffer(audio.readframes(count), dtype='<i2').astype(float)
            def gain(start, end):
                a, b = int(start*rate), int(end*rate)
                return 20*math.log10(np.sqrt(np.mean(actual[a:b]**2))/np.sqrt(np.mean(samples[a:b].astype(float)**2)))
            self.assertAlmostEqual(gain(1.2, 1.8), -18., delta=.02)
            self.assertAlmostEqual(gain(.1, .4), 0., delta=.02)
            self.assertTrue(-18 < gain(.8, .9) < 0)
            self.assertTrue(-18 < gain(2.1, 2.2) < 0)
            self.assertAlmostEqual(gain(2.5, 3.), 0., delta=.02)

    def test_fractional_zoom_real_frame_clock(self):
        from backend.rendering import _ken_burns_filter
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root/'motion.mkv'
            expression = _ken_burns_filter(12, .06)
            self.assertNotIn('zoompan', expression)
            self.assertIn('on-1', expression)
            # Small independent frame-clock fixture, product perspective math.
            expression = expression.replace('scale=1920:1080', 'scale=320:180')
            self.run_media(['-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30:duration=0.4',
                            '-vf', expression, '-c:v', 'ffv1', str(output)])
            probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                                    '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(output)],
                                   capture_output=True, check=True, timeout=15)
            frames = json.loads(probe.stdout)['frames']
            self.assertEqual(len(frames), 12)
            clocks = [float(frame['best_effort_timestamp_time']) for frame in frames]
            for i, clock in enumerate(clocks):
                self.assertAlmostEqual(clock, i/30, delta=.00051)  # Matroska ms timebase.

    def test_real_final_loudness_measurement(self):
        from backend.quality import _media_quality_metrics
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'measured.wav'
            self.run_media(['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=3',
                            '-af', 'loudnorm=I=-20:TP=-3.5:LRA=5,aresample=48000', '-c:a', 'pcm_s16le', str(output)])
            metrics = _media_quality_metrics(output)
            self.assertTrue(metrics)
            self.assertLessEqual(abs(metrics['integrated_lufs'] + 20), 1.)
            self.assertLessEqual(metrics['true_peak_dbfs'], -3.)


class PipelineFocusedTests(unittest.IsolatedAsyncioTestCase):
    async def test_voiceover_retry_does_not_repeat_fixture_provider(self):
        from tests.test_mode_pipeline import ModeMediaIntegration, Reporter, FakeVoice
        from backend import mode_pipeline as pipeline
        ModeMediaIntegration.setUpClass()
        case = ModeMediaIntegration()
        await case.asyncSetUp()
        try:
            record = case.record('voiceover', texts=[('今天活动开幕。', 'narration')])
            voice = FakeVoice()
            with ExitStack() as stack:
                case.providers(stack, voice=voice)
                await pipeline.run_mode_pipeline(record, Reporter(), case.settings)
                first_calls = list(voice.calls)
                audio_hash = pipeline._sha(case.root/'narration.m4a')
                record.resume_from = 10
                await pipeline.run_mode_pipeline(record, Reporter(), case.settings)
            self.assertEqual(voice.calls, first_calls)
            self.assertEqual(len(first_calls), 1)
            self.assertEqual(pipeline._sha(case.root/'narration.m4a'), audio_hash)
            self.assertIn('Noto Sans SC,42,', (case.root/'subs.ass').read_text(encoding='utf-8'))
        finally:
            await case.asyncTearDown()
            ModeMediaIntegration.tearDownClass()

    async def test_real_ambient_derivative_preserves_narration_and_clock(self):
        await self._ambient_clock(144000)

    async def test_real_ambient_non_aac_frame_aligned_clock_preserves_samples(self):
        await self._ambient_clock(144037)

    async def _ambient_clock(self, sample_count):
        from backend import mode_pipeline
        from backend.mode_pipeline import _ambient_narration, _sha
        from backend.models import EDLItem, EDLClip
        from backend.quality import _media_quality_metrics
        from backend.tts_pipeline import probe_audio_duration
        duration = sample_count / 48000
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, frequency in [('raw.wav', 320), ('narration.m4a', 880)]:
                result = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        f'sine=frequency={frequency}:sample_rate=48000:duration={duration:.9f}',
                        '-af', 'loudnorm=I=-20:TP=-3.5:LRA=5,aresample=48000', str(root/name)], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
            before = _sha(root/'narration.m4a')
            raw_before = _sha(root/'raw.wav')
            manifest = {'source_clocks': {'u': {'norm_path': 'norm.mp4', 'has_audio': True,
                        'prepared_start': 0., 'prepared_end': duration, 'norm_source_offset': 0.,
                        'audio_offset_seconds': 0., 'raw_path': 'raw.wav'}}}
            edl = [EDLItem(sentence_id=0, timeline_start=0., timeline_end=duration, clips=[
                EDLClip(shot_id=0, src='norm.mp4', in_time=0., out_time=duration)])]
            with patch.object(mode_pipeline, 'run_logged_command', wraps=mode_pipeline.run_logged_command) as commands:
                result = await _ambient_narration(root, edl, [{'id': 'u', 'sec': duration, 'has_speech': False, 'transcript': [], 'silences': []}], manifest)
            self.assertIsNotNone(result)
            self.assertEqual(_sha(root/'narration.m4a'), before)
            self.assertEqual(_sha(root/'raw.wav'), raw_before)
            self.assertTrue(manifest['ambient']['applied'])
            self.assertEqual(manifest['ambient']['target_lufs'], -24)
            self.assertAlmostEqual(await probe_audio_duration(result, root), duration, delta=.03)
            metrics = _media_quality_metrics(result)
            self.assertLessEqual(abs(metrics['integrated_lufs'] + 20), 1.)
            self.assertLessEqual(metrics['true_peak_dbfs'], -3.)
            final = next(call.args[0] for call in commands.call_args_list if call.args[0][-1] == str(result))
            audio_filter = final[final.index('-af') + 1]
            self.assertTrue(audio_filter.endswith(',aresample=48000,asetpts=N/SR/TB'))
            self.assertNotIn('atrim', audio_filter)
            self.assertNotIn('apad', audio_filter)
            decoded = []
            for source in (root/'narration.m4a', result):
                pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source), '-map', '0:a:0',
                                      '-ar', '48000', '-ac', '1', '-f', 's16le', 'pipe:1'],
                                     capture_output=True, check=True, timeout=30)
                decoded.append(len(pcm.stdout) // 2)
            self.assertEqual(decoded[0], decoded[1])
            # Old/new AAC decoders differ only in handling the final frame's
            # padding. Neither the derivative nor this test deletes samples.
            self.assertGreaterEqual(decoded[0], sample_count)
            self.assertLess(decoded[0] - sample_count, 1024)

    async def test_real_original_resume_reuses_completed_provider_stages(self):
        from tests.test_mode_pipeline import ModeMediaIntegration, Reporter
        ModeMediaIntegration.setUpClass()
        case = ModeMediaIntegration()
        await case.asyncSetUp()
        try:
            from backend import mode_pipeline as pipeline
            record = case.record()
            with ExitStack() as stack:
                case.providers(stack, all_original=True)
                await pipeline.run_mode_pipeline(record, Reporter(), case.settings)
                first = pipeline.read_json(case.root, 'production_mode.json')
                audio_hash = pipeline._sha(case.root/'narration.m4a')
                record.resume_from = 8
                await pipeline.run_mode_pipeline(record, Reporter(), case.settings)
                second = pipeline.read_json(case.root, 'production_mode.json')
            self.assertEqual(pipeline._sha(case.root/'narration.m4a'), audio_hash)
            self.assertEqual(first['audio_cache'], second['audio_cache'])
            self.assertEqual(second['media_contract'], pipeline.V2_MEDIA_CONTRACT)
            self.assertEqual(second['resume_requested_from'], 8)
            self.assertEqual(second['jumpcut_rendering'][0]['ratio'], 1.06)
            self.assertFalse(second['local_speech']['available'])
            report = pipeline.read_json(case.root, 'quality_report.json')
            self.assertTrue(report['metrics']['audioFinal']['measured'])
            self.assertEqual(report['metrics']['audioFinal']['tolerance_lu'], 1.)
        finally:
            await case.asyncTearDown()
            ModeMediaIntegration.tearDownClass()

    async def test_ambient_unknown_or_speech_is_not_narration(self):
        from backend.mode_pipeline import _ambient_narration
        from backend.models import EDLItem, EDLClip
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # This source deliberately doesn't exist: voice/unknown must never
            # reach a decoder just because it was selected for A-mode visuals.
            manifest = {'source_clocks': {'u': {'norm_path': 'norm.mp4', 'has_audio': True,
                        'prepared_start': 0., 'prepared_end': 3., 'norm_source_offset': 0.,
                        'audio_offset_seconds': 0., 'raw_path': 'not-present.mp4'}}}
            edl = [EDLItem(sentence_id=0, timeline_start=0., timeline_end=3., clips=[
                EDLClip(shot_id=0, src='norm.mp4', in_time=0., out_time=3.)])]
            for speech in (True, None):
                result = await _ambient_narration(root, edl, [{'id': 'u', 'sec': 3., 'has_speech': speech, 'transcript': [], 'silences': []}], manifest)
                self.assertIsNone(result)
                self.assertFalse(manifest['ambient']['source_speech_used'])