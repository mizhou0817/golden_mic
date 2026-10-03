import asyncio
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid
import wave
from array import array
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx

from backend.models import (
    MatchCandidate,
    MatchPlanItem,
    PronunciationDecision,
    PronunciationReading,
    Sentence,
    SentenceTiming,
    SyncSoundSelection,
    TTSWordTiming,
)
from backend.providers.tts import (
    EdgeTTSProvider,
    NEWS_CONTEXT_INSTRUCTION,
    OpenAITTSProvider,
    TTSProvider,
    TTSProviderError,
    VolcengineTTSProvider,
)
from backend.tts_pipeline import (
    FULL_SENTENCE_GAP_SECONDS,
    NARRATION_AUDIO_FORMAT,
    TTSProcessingError,
    _split_group_word_timings,  # pyright: ignore[reportPrivateUsage]
    _two_pass_loudnorm_filter,  # pyright: ignore[reportPrivateUsage]
    concatenate_narration,
    probe_audio_duration,
    standardize_tts_speaking_rate,
    synthesize_narration,
    trim_tts_audio_edges,
)


class ContinuousTTSBoundaryTest(unittest.TestCase):
    def test_rejects_boundaries_that_would_truncate_a_word_or_overlapping_audio(self) -> None:
        unsafe_cases = [
            [TTSWordTiming(text="甲乙丙丁", start=0.1, end=1.9)],
            [
                TTSWordTiming(text="甲乙", start=0.1, end=1.1),
                TTSWordTiming(text="丙丁", start=1.0, end=1.9),
            ],
        ]

        for words in unsafe_cases:
            with self.subTest(words=[word.text for word in words]):
                with self.assertRaisesRegex(TTSProcessingError, "不能安全切分音频"):
                    _split_group_word_timings(["甲乙", "丙丁"], words, 2.0)

    def test_preserves_audio_after_early_final_word_timestamp(self) -> None:
        words = [
            TTSWordTiming(text="迎春平", start=0.1, end=0.9),
            TTSWordTiming(text="台。", start=0.9, end=1.0),
        ]

        split = _split_group_word_timings(["迎春平台。"], words, 1.8)

        self.assertAlmostEqual(split[0][0][0], 0.06, places=6)
        self.assertEqual(split[0][0][1], 1.8)
        self.assertEqual(split[0][1][-1].text, "台。")
        self.assertEqual(split[0][1][-1].end, 0.94)


class EdgeProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_twice_before_success(self) -> None:
        attempts = 0
        voices: list[str] = []
        options: list[dict[str, object]] = []

        class FakeCommunicate:
            def __init__(self, text: str, voice: str, **kwargs: object) -> None:
                voices.append(voice)
                options.append(kwargs)

            async def save(self, output_path: str) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise RuntimeError("temporary edge failure")
                Path(output_path).write_bytes(b"mock-mp3")

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "sentence.mp3"
            provider = EdgeTTSProvider(
                voice="zh-CN-YunjianNeural",
                max_retries=2,
                backoff_base=0,
            )
            with patch("backend.providers.tts.edge_tts.Communicate", FakeCommunicate):
                await provider.synthesize("测试新闻旁白。", output_path)
            self.assertEqual(output_path.read_bytes(), b"mock-mp3")

        self.assertEqual(attempts, 3)
        self.assertEqual(voices, ["zh-CN-YunjianNeural"] * 3)
        self.assertTrue(all(item["rate"] == "+0%" for item in options))
        self.assertTrue(all(item["volume"] == "+0%" for item in options))


class OpenAIProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_compatible_branch_retries_and_writes_mp3(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            payload = json.loads(request.content)
            self.assertEqual(request.url.path, "/v1/audio/speech")
            self.assertEqual(payload["response_format"], "mp3")
            self.assertEqual(payload["voice"], "alloy")
            if attempts < 3:
                return httpx.Response(503, json={"error": "temporary"})
            return httpx.Response(200, content=b"compatible-mp3")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAITTSProvider(
                base_url="https://tts.example/v1",
                api_key="test-key",
                model="tts-test",
                voice="alloy",
                client=client,
                max_retries=2,
                backoff_base=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                output_path = Path(directory) / "sentence.mp3"
                await provider.synthesize("测试新闻旁白。", output_path)
                self.assertEqual(output_path.read_bytes(), b"compatible-mp3")
        self.assertEqual(attempts, 3)


class VolcengineProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_factory_applies_task_pacing_to_news_context(self) -> None:
        from backend.config import Settings
        from backend.providers.tts import create_tts_provider

        provider = create_tts_provider(
            Settings(
                _env_file=None,
                tts_provider="volcengine",
                volcengine_tts_api_keys="test-key",
            ),
            target_chars_per_minute=230,
        )
        try:
            provider.set_document_context("新闻稿正文。")
            self.assertTrue(any("230" in text for text in provider.context_texts))  # type: ignore[attr-defined]
        finally:
            await provider.aclose()

    async def test_rotates_api_keys_and_decodes_chunked_audio(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = json.loads(request.content)
            self.assertEqual(request.url.path, "/api/v3/tts/unidirectional")
            self.assertEqual(request.headers["X-Api-Resource-Id"], "seed-icl-2.0")
            self.assertEqual(payload["req_params"]["speaker"], "S_testVoice")
            self.assertEqual(payload["req_params"]["model"], "seed-tts-2.0-standard")
            self.assertEqual(payload["req_params"]["audio_params"]["format"], "mp3")
            self.assertEqual(payload["req_params"]["audio_params"]["sample_rate"], 48000)
            self.assertEqual(payload["req_params"]["audio_params"]["bit_rate"], 160000)
            self.assertTrue(payload["req_params"]["audio_params"]["enable_subtitle"])
            uuid.UUID(request.headers["X-Api-Request-Id"])
            if len(requests) == 1:
                return httpx.Response(503, json={"message": "temporary"})
            events = [
                {
                    "code": 0,
                    "message": "OK",
                    "data": base64.b64encode(b"mp3-").decode(),
                    "sentence": {
                        "words": [
                            {"word": "测试", "startTime": 0.12, "endTime": 0.42, "confidence": 0.96},
                        ]
                    },
                },
                {
                    "code": 0,
                    "message": "OK",
                    "data": base64.b64encode(b"audio").decode(),
                    "sentence": {
                        "words": [
                            {"word": "旁白", "startTime": 0.42, "endTime": 0.81, "confidence": 0.94},
                        ]
                    },
                },
                {"code": 20000000, "message": "OK"},
            ]
            content = "\n".join(json.dumps(event) for event in events)
            return httpx.Response(200, text=content, headers={"Content-Type": "application/x-ndjson"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineTTSProvider(
                base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                api_keys="first-key, second-key",
                voice_type="S_testVoice",
                resource_id="seed-icl-2.0",
                model="seed-tts-2.0-standard",
                client=client,
                max_retries=1,
                backoff_base=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                output_path = Path(directory) / "sentence.mp3"
                words = await provider.synthesize("测试火山引擎新闻旁白。", output_path)
                self.assertEqual(output_path.read_bytes(), b"mp3-audio")
                assert words is not None
                self.assertEqual([word.text for word in words], ["测试", "旁白"])
                self.assertAlmostEqual(words[0].start, 0.12)

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].headers["X-Api-Key"], "first-key")
        self.assertEqual(requests[1].headers["X-Api-Key"], "second-key")

    async def test_sends_news_instruction_and_shared_section_id(self) -> None:
        payloads: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            events = [
                {"code": 0, "message": "OK", "data": base64.b64encode(b"audio").decode()},
                {"code": 20000000, "message": "OK"},
            ]
            return httpx.Response(200, text="\n".join(json.dumps(event) for event in events))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineTTSProvider(
                base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                api_keys="test-key",
                voice_type="zh_male_m191_uranus_bigtts",
                resource_id="seed-tts-2.0",
                client=client,
            )
            provider.set_document_context("型号为M20。首批共有20台设备。")
            with tempfile.TemporaryDirectory() as directory:
                await provider.synthesize("型号为M二零。", Path(directory) / "first.mp3")
                await provider.synthesize("首批共有二十台设备。", Path(directory) / "second.mp3")

        first_params = cast(dict[str, object], payloads[0]["req_params"])
        second_params = cast(dict[str, object], payloads[1]["req_params"])
        self.assertIsInstance(first_params["additions"], str)
        first_additions = cast(
            dict[str, object],
            json.loads(cast(str, first_params["additions"])),
        )
        second_additions = cast(
            dict[str, object],
            json.loads(cast(str, second_params["additions"])),
        )
        first_audio_params = cast(dict[str, object], first_params["audio_params"])
        self.assertEqual(first_params["speaker"], "zh_male_m191_uranus_bigtts")
        self.assertEqual(first_additions["context_texts"], [NEWS_CONTEXT_INSTRUCTION])
        self.assertEqual(first_additions["section_id"], second_additions["section_id"])
        uuid.UUID(str(first_additions["section_id"]))
        self.assertNotIn("context_texts", first_params)
        self.assertNotIn("section_id", first_params)
        self.assertEqual(first_audio_params["speech_rate"], 0)
        self.assertEqual(first_audio_params["loudness_rate"], 0)

    async def test_does_not_retry_deterministic_resource_error(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            event = {"code": 55000000, "message": "resource ID is mismatched with speaker related resource"}
            return httpx.Response(200, text=json.dumps(event))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineTTSProvider(
                base_url="https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                api_keys="first-key, second-key",
                voice_type="S_wrongVoice",
                resource_id="seed-icl-2.0",
                client=client,
                max_retries=2,
                backoff_base=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(TTSProviderError, "在 1 次尝试后失败"):
                    await provider.synthesize("测试新闻旁白。", Path(directory) / "sentence.mp3")

        self.assertEqual(attempts, 1)


class SyncSoundNarrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_sound_replaces_tts_for_matched_sentence(self) -> None:
        sentence = Sentence(sentence_id=0, text="稿件中的项目启动句子。")
        selection = SyncSoundSelection(
            source_index=0,
            source_media_path="raw/source.mp4",
            shot_id=0,
            text="本市重点项目今天正式启动。",
            start=0.4,
            end=1.6,
            similarity=0.96,
        )
        match = MatchPlanItem(
            sentence_id=0,
            text=sentence.text,
            shot_id=0,
            confidence=0.96,
            candidates=[MatchCandidate(shot_id=0, similarity=0.96)],
            sync_sound=selection,
        )

        async def fake_extract(task_dir: Path, sync: SyncSoundSelection, output_path: Path) -> Path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"sync-mp3")
            return output_path

        async def fake_concatenate(task_dir: Path, timings: object, **kwargs: object) -> Path:
            output_path = Path(kwargs.get("output_path") or task_dir / "narration.m4a")
            output_path.write_bytes(b"narration")
            return output_path

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            with (
                patch("backend.tts_pipeline.require_media_tools"),
                patch("backend.tts_pipeline.extract_sync_sound_audio", side_effect=fake_extract),
                patch("backend.tts_pipeline.concatenate_narration", side_effect=fake_concatenate),
                patch("backend.tts_pipeline.probe_audio_duration", return_value=1.2),
            ):
                timings = await synthesize_narration(
                    task_dir,
                    [sentence],
                    AlwaysFailProvider(),
                    lambda *args: None,
                    match_plan=[match],
                )

        self.assertEqual(timings[0].audio_kind, "sync")
        self.assertEqual(timings[0].text, selection.text)
        self.assertEqual(timings[0].duration, 1.2)

    async def test_sync_extraction_failure_falls_back_to_tts(self) -> None:
        class FallbackProvider(TTSProvider):
            def validate_configuration(self) -> None:
                return None

            async def synthesize(self, text: str, output_path: Path) -> None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"tts-fallback")

        sentence = Sentence(sentence_id=0, text="稿件句子。")
        match = MatchPlanItem(
            sentence_id=0,
            text=sentence.text,
            shot_id=0,
            confidence=0.95,
            candidates=[MatchCandidate(shot_id=0, similarity=0.95)],
            sync_sound=SyncSoundSelection(
                source_index=0,
                source_media_path="raw/missing.mp4",
                shot_id=0,
                text="同期声句子。",
                start=0.2,
                end=1.2,
                similarity=0.95,
            ),
        )

        async def fake_concatenate(task_dir: Path, timings: object, **kwargs: object) -> Path:
            output_path = Path(kwargs.get("output_path") or task_dir / "narration.m4a")
            output_path.write_bytes(b"narration")
            return output_path

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            with (
                patch("backend.tts_pipeline.require_media_tools"),
                patch("backend.tts_pipeline.extract_sync_sound_audio", side_effect=RuntimeError("bad audio")),
                patch("backend.tts_pipeline.concatenate_narration", side_effect=fake_concatenate),
                patch("backend.tts_pipeline.probe_audio_duration", return_value=1.0),
            ):
                timings = await synthesize_narration(
                    task_dir,
                    [sentence],
                    FallbackProvider(),
                    lambda *args: None,
                    match_plan=[match],
                )

        self.assertEqual(timings[0].audio_kind, "tts")
        self.assertIsNone(match.sync_sound)


class SemanticPronunciationNarrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_uses_semantic_number_readings_but_preserves_original_timing_text(self) -> None:
        class CapturingProvider(TTSProvider):
            def __init__(self) -> None:
                self.document_context = ""
                self.texts: list[str] = []

            def set_document_context(self, full_text: str) -> None:
                self.document_context = full_text

            def validate_configuration(self) -> None:
                return None

            async def synthesize(self, text: str, output_path: Path) -> None:
                self.texts.append(text)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"tts-audio")

        async def fake_concatenate(task_dir: Path, timings: object, **kwargs: object) -> Path:
            output_path = Path(kwargs.get("output_path") or task_dir / "narration.m4a")
            output_path.write_bytes(b"narration")
            return output_path

        async def fake_standardize(
            task_dir: Path,
            timings: object,
            **kwargs: object,
        ) -> tuple[list[SentenceTiming], dict[str, float]]:
            return (
                list(cast(list[SentenceTiming], timings)),
                {
                    "tempo_correction_factor": 1.0,
                    "measured_chars_per_minute_before": 255.0,
                    "measured_chars_per_minute_after": 255.0,
                    "tts_unit_rate_cv": 0.0,
                },
            )

        full_script = "新设备型号为M20。首批共有20台设备投入使用。"
        sentences = [
            Sentence(sentence_id=0, text="新设备型号为M20。"),
            Sentence(sentence_id=1, text="首批共有20台设备投入使用。"),
        ]
        decisions = [
            PronunciationDecision(
                sentence_id=0,
                readings=[PronunciationReading(source="20", spoken="二零")],
            ),
            PronunciationDecision(
                sentence_id=1,
                readings=[PronunciationReading(source="20", spoken="二十")],
            ),
        ]
        provider = CapturingProvider()

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            with (
                patch("backend.tts_pipeline.require_media_tools"),
                patch("backend.tts_pipeline.concatenate_narration", side_effect=fake_concatenate),
                patch("backend.tts_pipeline.probe_audio_duration", side_effect=[1.0, 1.0, 2.25]),
                patch(
                    "backend.tts_pipeline.standardize_tts_speaking_rate",
                    side_effect=fake_standardize,
                ),
            ):
                timings = await synthesize_narration(
                    task_dir,
                    sentences,
                    provider,
                    lambda *args: None,
                    full_script=full_script,
                    pronunciation_plan=decisions,
                    rate_tolerance=0.25,
                )
            manifest = json.loads((task_dir / "pronunciation_plan.json").read_text(encoding="utf-8"))

        self.assertEqual(provider.document_context, full_script)
        self.assertEqual(provider.texts, ["新设备型号为M二零。", "首批共有二十台设备投入使用。"])
        self.assertEqual([timing.text for timing in timings], [sentence.text for sentence in sentences])
        self.assertEqual([item["tts_text"] for item in manifest], provider.texts)


class TTSNormalizationRegressionTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _timing(sentence_id: int, units: int, rate: float) -> SentenceTiming:
        word_end = 0.1 + units * 60 / rate
        return SentenceTiming(
            sentence_id=sentence_id,
            text="甲" * units,
            audio_path=f"tts/sent_{sentence_id}.wav",
            duration=word_end + 0.7,
            start=0,
            end=word_end + 0.7,
            tts_group_id="group_0_14",
            words=[TTSWordTiming(text="甲" * units, start=0.1, end=word_end, confidence=0.96)],
        )

    async def _standardize_fake_audio(
        self,
        timings: list[SentenceTiming],
        *,
        tolerance: float = 0.08,
        first_encode_extra_seconds: float = 0.0,
    ) -> tuple[list[SentenceTiming], dict[str, float], dict[str, list[float]]]:
        factors: dict[str, list[float]] = {}
        durations = {timing.audio_path: timing.duration for timing in timings}
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "tts").mkdir()
            for timing in timings:
                (task_dir / timing.audio_path).write_bytes(b"fake-local-audio")

            async def retime(root: Path, path: Path, factor: float) -> None:
                self.assertGreaterEqual(factor, 0.9)
                self.assertLessEqual(factor, 1.1)
                key = path.relative_to(root).as_posix()
                applied = factors.setdefault(key, [])
                durations[key] /= factor
                if not applied:
                    durations[key] += first_encode_extra_seconds
                applied.append(factor)

            async def probe(path: Path, root: Path) -> float:
                return durations[path.relative_to(root).as_posix()]

            with (
                patch("backend.tts_pipeline._apply_tempo_correction", side_effect=retime),
                patch("backend.tts_pipeline.probe_audio_duration", side_effect=probe) as measured,
            ):
                result, metrics = await standardize_tts_speaking_rate(
                    task_dir, timings, target_chars_per_minute=265, tolerance=tolerance,
                )
            self.assertEqual(measured.await_count, sum(len(values) for values in factors.values()))
        return result, metrics, factors

    async def test_corrects_canary_unit_outliers_before_and_after_document_normalization(self) -> None:
        # Credential-free live Canary measurements; no runtime dependency on
        # the live task, provider, or text content for this deterministic test.
        units = [6, 44, 16, 13, 6, 14, 20, 20, 12, 14, 20, 8, 11, 23, 14]
        rates = [242.878, 253.764, 261.411, 280.939, 271.214, 274.151, 226.245,
                 240.542, 242.878, 273.165, 271.891, 297.221, 283.454, 312.287, 307.449]
        gaps = [0.0, 0.0, 0.18, 0.0, 0.12, 0.0, 0.0, 0.12, 0.0, 0.18, 0.0, 0.0, 0.0, 0.12, 0.0]
        for already_standardized in (False, True):
            with self.subTest(already_standardized=already_standardized):
                timings = [
                    self._timing(index, count, rate if already_standardized else rate / 0.904047)
                    for index, (count, rate) in enumerate(zip(units, rates, strict=True))
                ]
                for timing, gap in zip(timings, gaps, strict=True):
                    timing.gap_after = gap
                    timing.tempo_adjustment = 0.904047 if already_standardized else 1.0
                snapshot = [timing.model_dump() for timing in timings]
                result, metrics, factors = await self._standardize_fake_audio(timings)
                if already_standardized:
                    self.assertEqual(metrics["tempo_correction_factor"], 1.0)
                    self.assertEqual(set(factors), {f"tts/sent_{i}.wav" for i in (0, 6, 7, 8, 11, 13, 14)})
                self.assertEqual(metrics["tts_unit_rate_outlier_count"], 0)
                self.assertEqual([timing.model_dump() for timing in timings], snapshot)
                cursor = 0.0
                for old, new in zip(timings, result, strict=True):
                    factor = math.prod(factors.get(old.audio_path, []))
                    self.assertAlmostEqual(new.tempo_adjustment, old.tempo_adjustment * factor, places=5)
                    self.assertEqual(new.spoken_unit_count, units[new.sentence_id])
                    self.assertIsNotNone(new.speaking_rate_cpm)
                    self.assertTrue(243.8 <= cast(float, new.speaking_rate_cpm) <= 286.2)
                    self.assertEqual(new.text, old.text)
                    self.assertEqual(new.tts_group_id, old.tts_group_id)
                    self.assertEqual(new.gap_after, old.gap_after)
                    self.assertAlmostEqual(new.start, cursor, places=5)
                    self.assertAlmostEqual(new.end, new.start + new.duration, places=5)
                    self.assertAlmostEqual(new.words[0].start, old.words[0].start / factor, places=5)
                    self.assertAlmostEqual(new.words[0].end, old.words[0].end / factor, places=5)
                    self.assertEqual(new.words[0].confidence, old.words[0].confidence)
                    self.assertAlmostEqual(new.duration - new.words[-1].end, 0.7 / factor, places=5)
                    cursor = new.end + new.gap_after

    async def test_remeasures_encoded_duration_and_retries_without_word_timings(self) -> None:
        timings = [self._timing(i, 12, rate) for i, rate in enumerate((245.0, 290.0))]
        for timing, rate in zip(timings, (245.0, 290.0), strict=True):
            timing.words = []
            timing.duration = 720 / rate
            timing.end = timing.duration
        result, metrics, factors = await self._standardize_fake_audio(
            timings, tolerance=0.01, first_encode_extra_seconds=0.04,
        )
        self.assertEqual(metrics["tempo_correction_factor"], 1.0)
        self.assertGreater(len(factors[timings[0].audio_path]), 1)
        for timing in result:
            applied = factors[timing.audio_path]
            self.assertLessEqual(len(applied), 3)
            self.assertTrue(0.9 <= math.prod(applied) <= 1.1)
            self.assertTrue(262.35 <= cast(float, timing.speaking_rate_cpm) <= 267.65)
            self.assertAlmostEqual(cast(float, timing.speaking_rate_cpm), 720 / timing.duration, places=3)
            self.assertEqual(timing.words, [])

    async def test_budget_exhaustion_keeps_real_outliers_instead_of_claiming_target_rate(self) -> None:
        timing = self._timing(0, 20, 500.0)
        result, metrics, factors = await self._standardize_fake_audio([timing])
        self.assertEqual(factors[timing.audio_path], [0.9, 0.9])
        self.assertEqual(result[0].tempo_adjustment, 0.81)
        self.assertAlmostEqual(cast(float, result[0].speaking_rate_cpm), 405, places=3)
        self.assertEqual(metrics["tts_unit_rate_outlier_count"], 1)
        self.assertIsNone(result[0].integrated_lufs)

    async def test_sync_recording_and_in_range_tts_are_not_processed(self) -> None:
        tts = self._timing(0, 20, 265)
        recording = self._timing(1, 20, 500)
        recording.audio_kind = "sync"
        recording.tts_group_id = None
        result, _, factors = await self._standardize_fake_audio([tts, recording])
        self.assertEqual(factors, {})
        self.assertEqual(result[0].duration, tts.duration)
        self.assertEqual(result[1].model_dump(exclude={"start", "end"}),
                         recording.model_dump(exclude={"start", "end"}))
        self.assertIsNone(result[1].speaking_rate_cpm)

    async def test_persists_unit_tempo_factors_in_timings_and_narration_profile(self) -> None:
        class LocalProvider(TTSProvider):
            def validate_configuration(self) -> None:
                return None

            async def synthesize(self, text: str, output_path: Path) -> None:
                output_path.write_bytes(b"local-fake-audio")

        async def normalize(
            _root: Path, timings: Sequence[SentenceTiming], **_kwargs: object,
        ) -> tuple[list[SentenceTiming], dict[str, float]]:
            return [
                timing.model_copy(update={"tempo_adjustment": factor})
                for timing, factor in zip(timings, (0.96, 0.82), strict=True)
            ], {
                "tempo_correction_factor": 0.91,
                "measured_chars_per_minute_before": 291.0,
                "measured_chars_per_minute_after": 265.0,
                "tts_unit_rate_cv": 0.02,
            }

        def progress(_completed: int, _total: int, _message: str) -> None:
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("backend.tts_pipeline.require_media_tools"),
                patch("backend.tts_pipeline.trim_tts_audio_edges", return_value=[]),
                patch("backend.tts_pipeline.probe_audio_duration", side_effect=[1.0, 1.0, 2.12]),
                patch("backend.tts_pipeline.standardize_tts_speaking_rate", side_effect=normalize),
                patch("backend.tts_pipeline.concatenate_narration", return_value=root / "narration.m4a"),
            ):
                result = await synthesize_narration(
                    root, [Sentence(sentence_id=0, text="第一句。"), Sentence(sentence_id=1, text="第二句。")],
                    LocalProvider(), progress,
                )
            persisted = cast(list[dict[str, object]], json.loads((root / "timings.json").read_text(encoding="utf-8")))
            profile = cast(dict[str, object], json.loads((root / "narration_profile.json").read_text(encoding="utf-8")))
            units = cast(list[dict[str, object]], profile["units"])
        self.assertEqual([t.tempo_adjustment for t in result], [0.96, 0.82])
        self.assertEqual([t["tempo_adjustment"] for t in persisted], [0.96, 0.82])
        self.assertEqual([t["tempo_adjustment"] for t in units], [0.96, 0.82])

    async def test_loudness_measures_final_units_not_shared_group_or_recording(self) -> None:
        measured_paths: list[Path] = []
        commands: list[list[str]] = []

        async def analyze(_root: Path, path: Path, **_kwargs: object) -> str:
            measured_paths.append(path)
            return "loudnorm=I=-20:LRA=5:TP=-2"

        async def render(command: Sequence[str], _root: Path, _label: str) -> bytes:
            commands.append(list(command))
            Path(command[-1]).write_bytes(b"fake-narration")
            return b""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tts" / "groups").mkdir(parents=True)
            group = root / "tts" / "groups" / "group_0_14.mp3"
            group.write_bytes(b"unsplit-unretimed-source")
            timings = [self._timing(i, 12, 265) for i in range(3)]
            timings[0].gap_after = 0.0
            timings[2].gap_after = 0.0
            timings[2].audio_kind = "sync"
            timings[2].tts_group_id = None
            for timing in timings:
                (root / timing.audio_path).write_bytes(b"final-split-unit")
            with (
                patch("backend.tts_pipeline._two_pass_loudnorm_filter", side_effect=analyze),
                patch("backend.tts_pipeline.run_logged_command", side_effect=render),
            ):
                await concatenate_narration(root, timings)
            self.assertEqual(measured_paths, [(root / t.audio_path).resolve() for t in timings[:2]])
            graph = commands[0][commands[0].index("-filter_complex") + 1].split(";")
            self.assertTrue(graph[0].startswith(f"[0:a]{NARRATION_AUDIO_FORMAT},loudnorm="))
            self.assertNotIn("afade=t=out", graph[0])
            self.assertNotIn("afade=t=in", graph[1])
            self.assertNotIn("loudnorm", graph[2])
            self.assertIn("atrim=duration=0.120", ";".join(graph))
            self.assertEqual(group.read_bytes(), b"unsplit-unretimed-source")

    async def test_loudnorm_analysis_uses_same_channel_layout_as_render(self) -> None:
        commands: list[list[str]] = []

        async def capture(command: Sequence[str], _root: Path, _label: str) -> str:
            commands.append(list(command))
            return json.dumps({"input_i": -23.0, "input_lra": 2.0, "input_tp": -8.0,
                               "input_thresh": -33.0, "target_offset": 0.1})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("backend.tts_pipeline.run_capture_stderr_command", side_effect=capture):
                loudnorm = await _two_pass_loudnorm_filter(
                    root, root / "unit.wav", target_lufs=-20, target_lra=5, true_peak_dbfs=-2,
                )
        self.assertTrue(commands[0][commands[0].index("-af") + 1].startswith(NARRATION_AUDIO_FORMAT + ","))
        self.assertIn("measured_I=-23.0", loudnorm)
        self.assertIn("linear=true", loudnorm)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class NarrationPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_full_audio_tail_when_trimming_with_word_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            audio_path = task_dir / "sentence.mp3"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                    "-c:a", "libmp3lame", "-b:a", "96k", str(audio_path),
                ],
                check=True,
            )

            words = [TTSWordTiming(text="测试。", start=0.2, end=1.0)]
            adjusted = await trim_tts_audio_edges(task_dir, audio_path, words)
            duration = await probe_audio_duration(audio_path, task_dir)

        self.assertGreater(duration, 1.75)
        self.assertAlmostEqual(adjusted[0].start, 0.04, places=2)
        self.assertAlmostEqual(adjusted[0].end, 0.84, places=2)

    async def test_synthesizes_one_natural_sentence_in_one_provider_call(self) -> None:
        class TimestampProvider(TTSProvider):
            def __init__(self) -> None:
                self.texts: list[str] = []

            @property
            def supports_word_timings(self) -> bool:
                return True

            def validate_configuration(self) -> None:
                return None

            async def synthesize(self, text: str, output_path: Path) -> list[TTSWordTiming]:
                self.texts.append(text)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                        "-c:a", "libmp3lame", "-b:a", "160k", str(output_path),
                    ],
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    raise TTSProviderError(result.stderr.decode("utf-8", errors="replace"))
                return [
                    TTSWordTiming(text="甲乙丙丁，", start=0.05, end=0.99),
                    TTSWordTiming(text="戊己庚辛。", start=1.01, end=1.95),
                ]

        sentences = [
            Sentence(sentence_id=0, text="甲乙丙丁，", visual_group_id=7),
            Sentence(sentence_id=1, text="戊己庚辛。", visual_group_id=7),
        ]
        provider = TimestampProvider()
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            timings = await synthesize_narration(
                task_dir,
                sentences,
                provider,
                lambda *args: None,
            )
            manifest = json.loads((task_dir / "tts_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(provider.texts, ["甲乙丙丁，戊己庚辛。"])
        self.assertEqual(manifest["continuous_group_count"], 1)
        self.assertEqual(timings[0].tts_group_id, "group_0_1")
        self.assertEqual(timings[1].tts_group_id, "group_0_1")
        self.assertEqual(timings[0].gap_after, 0.0)
        self.assertTrue(all(timing.audio_path.endswith(".wav") for timing in timings))

    async def test_timings_and_narration_duration_match(self) -> None:
        sentences = [
            Sentence(sentence_id=0, text="第一句新闻旁白内容。"),
            Sentence(sentence_id=1, text="第二句新闻旁白内容。"),
            Sentence(sentence_id=2, text="第三句新闻旁白内容。"),
        ]
        provider = GeneratedAudioProvider([0.8, 1.1, 0.7])
        progress_messages: list[str] = []

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            timings = await synthesize_narration(
                task_dir,
                sentences,
                provider,
                lambda completed, total, message: progress_messages.append(message),
            )
            persisted = json.loads((task_dir / "timings.json").read_text(encoding="utf-8"))
            narration_duration = await probe_audio_duration(task_dir / "narration.m4a", task_dir)
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=codec_name",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(task_dir / "narration.m4a"),
                ],
                capture_output=True,
                check=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(len(timings), 3)
            self.assertAlmostEqual(timings[0].start, 0.0, places=6)
            self.assertAlmostEqual(timings[1].start, timings[0].end + FULL_SENTENCE_GAP_SECONDS, places=5)
            self.assertAlmostEqual(timings[2].start, timings[1].end + FULL_SENTENCE_GAP_SECONDS, places=5)
            self.assertLessEqual(abs(narration_duration - timings[-1].end), 0.3)
            self.assertEqual([item["sentence_id"] for item in persisted], [0, 1, 2])
            self.assertTrue(all((task_dir / item["audio_path"]).is_file() for item in persisted))
            self.assertEqual(probe.stdout.strip(), "aac")
            self.assertEqual(progress_messages[-1], "配音合成 3/3")
            manifest = json.loads((task_dir / "tts_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["profile"], "zh_cn_professional_news_v1")

    async def test_screen_units_inside_one_natural_sentence_have_no_added_gap(self) -> None:
        sentences = [
            Sentence(sentence_id=0, text="活动现场汇聚多家企业，", visual_group_id=0),
            Sentence(sentence_id=1, text="展品丰富多样。", visual_group_id=0),
            Sentence(sentence_id=2, text="市民踊跃参与。", visual_group_id=1),
        ]
        provider = GeneratedAudioProvider([1.5, 1.5, 1.5])
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            timings = await synthesize_narration(
                task_dir,
                sentences,
                provider,
                lambda *args: None,
            )

        self.assertEqual(timings[0].gap_after, 0.0)
        self.assertEqual(timings[1].gap_after, FULL_SENTENCE_GAP_SECONDS)
        self.assertAlmostEqual(timings[1].start, timings[0].end, places=5)

    async def test_applies_bounded_document_and_unit_tempo_factors_and_scales_words(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            tts_dir = task_dir / "tts"
            tts_dir.mkdir()
            path = tts_dir / "sent_0.mp3"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
                    "-c:a", "libmp3lame", "-b:a", "96k", str(path),
                ],
                check=True,
            )
            timing = SentenceTiming(
                sentence_id=0,
                text="一二三四五六七八九十",
                audio_path="tts/sent_0.mp3",
                duration=3.0,
                start=0.0,
                end=3.0,
                gap_after=0.0,
                words=[
                    {"text": "一二三四五六七八九十", "start": 0.0, "end": 3.0},
                ],
            )
            adjusted, metrics = await standardize_tts_speaking_rate(
                task_dir,
                [timing],
                target_chars_per_minute=255,
                tolerance=0.08,
            )

        self.assertEqual(metrics["tempo_correction_factor"], 1.1)
        self.assertEqual(adjusted[0].tempo_adjustment, 1.21)
        self.assertLess(adjusted[0].duration, 2.6)
        # Codec/frame rounding may clamp a cue at the measured EOF slightly
        # before its ideal 3/1.21 position; never lengthen the audio to fit it.
        self.assertAlmostEqual(adjusted[0].words[0].end, 3.0 / 1.21, delta=0.03)
        self.assertLessEqual(adjusted[0].words[0].end, adjusted[0].duration)

    async def test_rate_correction_preserves_audible_tail_beyond_last_word_cue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "unit.wav"
            # The final 0.5s has a distinct audible marker AFTER the early cue.
            samples = array("h", (
                int(6000 * math.sin(2 * math.pi * (880 if i >= 81600 else 440) * i / 48000))
                for i in range(105600)
            ))
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                audio.writeframes(samples.tobytes())
            timing = SentenceTiming(
                sentence_id=0, text="甲乙丙丁戊己庚", audio_path="unit.wav",
                duration=2.2, start=0, end=2.2, gap_after=0,
                words=[TTSWordTiming(text="甲乙丙丁戊己庚", start=0.07, end=1.35)],
            )
            result, _ = await standardize_tts_speaking_rate(
                root, [timing], target_chars_per_minute=265, tolerance=0.08,
            )
            corrected = result[0]
            factor = corrected.tempo_adjustment
            self.assertGreaterEqual(corrected.duration, 2.2 / factor - 0.06)
            self.assertAlmostEqual(corrected.words[-1].end, 1.35 / factor, places=5)
            self.assertGreater(corrected.duration - corrected.words[-1].end, 0.9)
            with wave.open(str(path), "rb") as audio:
                audio.setpos(audio.getnframes() - 9600)
                tail = array("h", audio.readframes(9600))
            self.assertGreater(math.sqrt(sum(value * value for value in tail) / len(tail)), 1000)
            crossings = sum((a < 0) != (b < 0) for a, b in zip(tail, tail[1:]))
            self.assertAlmostEqual(crossings / 0.4, 880, delta=25)

    async def test_clamps_word_timestamps_to_audio_duration_without_tempo_change(self) -> None:
        timing = SentenceTiming(
            sentence_id=0,
            text="一二三四五六七八九十",
            audio_path="tts/sent_0.wav",
            duration=2.4,
            start=0.0,
            end=2.4,
            gap_after=0.0,
            words=[TTSWordTiming(text="一二三四五六七八九十", start=0.0, end=2.5)],
        )

        with tempfile.TemporaryDirectory() as directory:
            adjusted, metrics = await standardize_tts_speaking_rate(
                Path(directory),
                [timing],
                target_chars_per_minute=255,
                tolerance=0.08,
            )

        self.assertEqual(metrics["tempo_correction_factor"], 1.0)
        self.assertEqual(adjusted[0].words[0].end, adjusted[0].duration)

    async def test_normalizes_tts_sentence_loudness_before_concatenation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            tts_dir = task_dir / "tts"
            tts_dir.mkdir()
            for sentence_id, volume in enumerate((0.02, 0.20)):
                subprocess.run(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency=440:sample_rate=48000:duration=3,volume={volume}",
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "96k",
                        str(tts_dir / f"sent_{sentence_id}.mp3"),
                    ],
                    check=True,
                )
            starts = [0.0, 3.0]
            timings = [
                SentenceTiming(
                    sentence_id=sentence_id,
                    text=f"第{sentence_id + 1}句新闻旁白。",
                    audio_path=f"tts/sent_{sentence_id}.mp3",
                    duration=3.0,
                    start=starts[sentence_id],
                    end=starts[sentence_id] + 3.0,
                    gap_after=0.0,
                )
                for sentence_id in range(2)
            ]

            output_path = await concatenate_narration(task_dir, timings)
            levels = [
                _measure_integrated_lufs(output_path, start=starts[sentence_id], duration=3.0)
                for sentence_id in range(2)
            ]

        self.assertLessEqual(max(levels) - min(levels), 1.0)

    @unittest.skipUnless(os.environ.get("GOLDEN_MIC_CANARY_TTS_REPLAY") == "1", "Opt-in local Canary audio replay")
    async def test_live_canary_replay_corrects_rates_and_loudness_without_mutating_sources(self) -> None:
        artifacts = Path(__file__).resolve().parents[1] / "canary_test" / "artifacts" / "live"
        manifest = cast(dict[str, object], json.loads((artifacts / "manifest.json").read_text(encoding="utf-8")))
        submission = cast(dict[str, object], json.loads((artifacts / "ui-submission.json").read_text(encoding="utf-8")))
        task_id = submission.get("taskId")
        if task_id != "22d4b7aa53a34adfbdfda4f7e887425a":
            self.skipTest("The verified September 21 Canary is not the active local fixture")
        root = Path(str(manifest["taskDataDir"])) / str(task_id)
        if not (root / "source_timings.json").is_file():
            self.skipTest("Local Canary temporary audio is no longer available")
        source = cast(list[dict[str, object]], json.loads((root / "source_timings.json").read_text(encoding="utf-8")))
        original = [SentenceTiming.model_validate(item) for item in source]
        self.assertEqual(len(original), 15)
        self.assertTrue(all(t.audio_kind == "tts" for t in original))
        allowed_sources = [root / name for name in (
            "source_timings.json", "timings.json", "tts_manifest.json", "narration_profile.json", "narration.m4a",
        )]
        for timing in original:
            path = (root / timing.audio_path).resolve()
            self.assertTrue(path.is_relative_to((root / "tts").resolve()))
            self.assertEqual(path.suffix.lower(), ".wav")
            allowed_sources.append(path)
        allowed_sources.extend((root / "tts" / "groups").glob("group_*.mp3"))
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in allowed_sources}
        before_rates = [cast(float, t.speaking_rate_cpm) for t in original]
        self.assertEqual(sum(1 for rate in before_rates if not 243.8 <= rate <= 286.2), 7)
        before_levels = [_measure_integrated_lufs(root / "narration.m4a", start=t.start, duration=t.duration)
                         for t in original]
        with tempfile.TemporaryDirectory(prefix="canary-tts-regression-") as directory:
            replay = Path(directory)
            (replay / "tts" / "groups").mkdir(parents=True)
            for path in allowed_sources:
                if path.suffix.lower() in {".wav", ".mp3"}:
                    shutil.copyfile(path, replay / path.relative_to(root))
            corrected, metrics = await standardize_tts_speaking_rate(
                replay, original, target_chars_per_minute=265, tolerance=0.08,
            )
            self.assertEqual(metrics["tts_unit_rate_outlier_count"], 0)
            self.assertEqual([t.gap_after for t in corrected], [t.gap_after for t in original])
            for old, new in zip(original, corrected, strict=True):
                factor = new.tempo_adjustment / old.tempo_adjustment
                self.assertEqual(new.text, old.text)
                self.assertEqual(new.tts_group_id, old.tts_group_id)
                self.assertEqual([w.text for w in new.words], [w.text for w in old.words])
                self.assertGreaterEqual(new.duration, old.duration / factor - 0.06)
                self.assertAlmostEqual(new.words[-1].end, old.words[-1].end / factor, places=5)
                self.assertTrue(all(0 <= w.start < w.end <= new.duration for w in new.words))
            output = await concatenate_narration(replay, corrected)
            duration = await probe_audio_duration(output, replay)
            self.assertAlmostEqual(duration, corrected[-1].end, delta=0.03)
            levels = [_measure_integrated_lufs(output, start=t.start, duration=t.duration) for t in corrected]
            self.assertLessEqual(max(levels) - min(levels), 1.0)
            self.assertTrue(all(abs(level + 20) <= 1.0 for level in levels))
            print("Canary local-only TTS replay:", json.dumps({
                "before_cpm": before_rates,
                "after_cpm": [t.speaking_rate_cpm for t in corrected],
                "before_lufs": before_levels, "after_lufs": levels,
                "before_spread_lu": round(max(before_levels) - min(before_levels), 3),
                "after_spread_lu": round(max(levels) - min(levels), 3),
                "duration_seconds": duration, "metrics": metrics,
            }))
        self.assertTrue(all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items()))

    async def test_failure_message_contains_sentence_text(self) -> None:
        sentence = Sentence(sentence_id=0, text="这句配音必须在错误中显示。")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TTSProcessingError, sentence.text):
                await synthesize_narration(
                    Path(directory),
                    [sentence],
                    AlwaysFailProvider(),
                    lambda *args: None,
                )


class GeneratedAudioProvider(TTSProvider):
    def __init__(self, durations: list[float]) -> None:
        self.durations = durations
        self.index = 0

    def validate_configuration(self) -> None:
        return None

    async def synthesize(self, text: str, output_path: Path) -> None:
        duration = self.durations[self.index]
        self.index += 1
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=24000:duration={duration}",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(output_path),
        ]
        result = await asyncio.to_thread(subprocess.run, command, capture_output=True, check=False)
        if result.returncode != 0:
            raise TTSProviderError(result.stderr.decode("utf-8", errors="replace"))


class AlwaysFailProvider(TTSProvider):
    def validate_configuration(self) -> None:
        return None

    async def synthesize(self, text: str, output_path: Path) -> None:
        raise TTSProviderError("模拟 TTS 失败")


def _measure_integrated_lufs(path: Path, *, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            str(path),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    matches = re.findall(r"I:\s+(-?[\d.]+) LUFS", result.stderr)
    if not matches:
        raise AssertionError("FFmpeg did not report integrated loudness")
    return float(matches[-1])


if __name__ == "__main__":
    unittest.main()
