import asyncio
import gzip
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from pydantic import ValidationError

from backend.asr_pipeline import (
    SourceASRRecord,
    SpeechActivity,
    analyze_speech_activity,
    apply_sync_sound_matches,
    transcribe_source_audio,
)
from backend.config import Settings
from backend.models import (
    AnnotatedShot,
    MatchCandidate,
    MatchPlanItem,
    Sentence,
    UploadedAsset,
    VisionQuality,
)
from backend.providers.asr import (
    ASRProviderError,
    ASRTranscript,
    ASRUtterance,
    VolcengineASRProvider,
)
from backend.pipeline import _run_normalization


class FakeWebSocket:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = list(responses)
        self.sent: list[bytes] = []

    async def send(self, message: bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes:
        if not self.responses:
            raise RuntimeError("no fake ASR response remains")
        return self.responses.pop(0)


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class FakeConnectFactory:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.uri = ""
        self.kwargs: dict[str, Any] = {}

    def __call__(self, uri: str, **kwargs: Any) -> FakeConnection:
        self.uri = uri
        self.kwargs = kwargs
        return FakeConnection(self.websocket)


class VolcengineASRProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_streams_audio_and_returns_timestamped_transcript(self) -> None:
        initial = _server_response(
            {"result": {"text": "", "utterances": []}, "audio_info": {"duration": 0}},
            sequence=1,
            final=False,
        )
        final = _server_response(
            {
                "result": {
                    "text": "这是同期声。",
                    "utterances": [
                        {
                            "text": "这是同期声。",
                            "start_time": 120,
                            "end_time": 1280,
                            "definite": True,
                            "speaker_id": "1",
                        }
                    ],
                },
                "audio_info": {"duration": 1400},
            },
            sequence=-2,
            final=True,
        )
        websocket = FakeWebSocket([initial, final])
        factory = FakeConnectFactory(websocket)
        provider = VolcengineASRProvider(
            base_url="wss://openspeech.bytedance.com/api/v3/sauc",
            endpoint_path="bigmodel_async",
            app_id="123456",
            access_token="test-token",
            resource_id="volc.seedasr.sauc.duration",
            cluster_id="Doubao_Seed_ASR_Streaming_2.0",
            max_retries=0,
            audio_chunk_bytes=4,
            connect_factory=factory,
        )

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "source.mp3"
            audio_path.write_bytes(b"0123456789")
            transcript = await provider.transcribe(audio_path)

        self.assertEqual(factory.uri, "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async")
        headers = factory.kwargs["additional_headers"]
        self.assertEqual(headers["X-Api-App-Key"], "123456")
        self.assertEqual(headers["X-Api-Access-Key"], "test-token")
        self.assertEqual(headers["X-Api-Resource-Id"], "volc.seedasr.sauc.duration")
        self.assertTrue(headers["X-Api-Connect-Id"])
        self.assertEqual(transcript.text, "这是同期声。")
        self.assertEqual(transcript.duration_ms, 1400)
        self.assertEqual(transcript.utterances[0].start_time_ms, 120)
        self.assertEqual(transcript.utterances[0].end_time_ms, 1280)
        self.assertEqual(transcript.utterances[0].speaker_id, "1")

        request_payload = _decode_client_json(websocket.sent[0])
        self.assertEqual(request_payload["audio"]["format"], "mp3")
        self.assertEqual(request_payload["audio"]["rate"], 16000)
        self.assertEqual(request_payload["request"]["model_name"], "bigmodel")
        self.assertTrue(request_payload["request"]["enable_nonstream"])
        self.assertTrue(request_payload["request"]["show_utterances"])
        self.assertEqual(len(websocket.sent), 4)
        self.assertEqual(websocket.sent[-1][1] >> 4, 0x2)
        self.assertEqual(websocket.sent[-1][1] & 0x0F, 0x2)

    async def test_protocol_error_is_reported(self) -> None:
        websocket = FakeWebSocket([_server_error(45000001, "invalid request")])
        provider = VolcengineASRProvider(
            base_url="wss://openspeech.bytedance.com/api/v3/sauc",
            endpoint_path="bigmodel_async",
            app_id="123456",
            access_token="test-token",
            resource_id="volc.seedasr.sauc.duration",
            max_retries=0,
            connect_factory=FakeConnectFactory(websocket),
        )

        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "source.wav"
            audio_path.write_bytes(b"RIFF-test-audio")
            with self.assertRaisesRegex(ASRProviderError, "45000001"):
                await provider.transcribe(audio_path)


class FakeASRProvider:
    def validate_configuration(self) -> None:
        return None

    async def transcribe(self, audio_path: Path) -> ASRTranscript:
        return ASRTranscript(
            text="本市重点项目今天正式启动。",
            duration_ms=3000,
            utterances=[
                ASRUtterance(
                    text="本市重点项目今天正式启动。",
                    start_time_ms=500,
                    end_time_ms=2200,
                )
            ],
        )


class ContextFakeASRProvider(FakeASRProvider):
    async def __aenter__(self) -> "ContextFakeASRProvider":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class SourceASRPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_content_addressed_cache_reuses_identical_audio_across_tasks(self) -> None:
        class CountingProvider(FakeASRProvider):
            def __init__(self) -> None:
                self.calls = 0

            async def transcribe(self, audio_path: Path) -> ASRTranscript:
                self.calls += 1
                return await super().transcribe(audio_path)

        async def fake_probe(path: Path, task_dir: Path) -> dict[str, object]:
            return {"streams": [{"codec_type": "audio"}]}

        async def fake_run(command: list[str], task_dir: Path, label: str) -> bytes:
            Path(command[-1]).write_bytes(b"identical-normalized-audio")
            return b""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache_dir = root / "cache"
            provider = CountingProvider()
            records: list[SourceASRRecord] = []
            with (
                patch("backend.asr_pipeline.probe_media", side_effect=fake_probe),
                patch("backend.asr_pipeline.run_logged_command", side_effect=fake_run),
            ):
                for task_name in ("task-a", "task-b"):
                    task_dir = root / task_name
                    task_dir.mkdir()
                    records = await transcribe_source_audio(
                        task_dir,
                        [_make_upload(task_dir, 0)],
                        provider,
                        lambda *args: None,
                        cache_dir=cache_dir,
                    )

        self.assertEqual(provider.calls, 1)
        self.assertTrue(records[0].cache_hit)
        self.assertEqual(records[0].transcript.text, "本市重点项目今天正式启动。")

    async def test_sparse_transcript_is_retried_once_and_richer_result_wins(self) -> None:
        class SparseThenRichProvider(FakeASRProvider):
            def __init__(self) -> None:
                self.calls = 0

            async def transcribe(self, audio_path: Path) -> ASRTranscript:
                self.calls += 1
                if self.calls == 1:
                    return ASRTranscript(
                        text="炒米会淋湿。",
                        duration_ms=20_000,
                        utterances=[
                            ASRUtterance(text="炒米会淋湿。", start_time_ms=1700, end_time_ms=13_000)
                        ],
                    )
                return ASRTranscript(
                    text="炒米会淋湿。油茶很好喝，加炒米和配料，等会盛给你吃。",
                    duration_ms=20_000,
                    utterances=[
                        ASRUtterance(
                            text="炒米会淋湿。油茶很好喝，加炒米和配料，等会盛给你吃。",
                            start_time_ms=1700,
                            end_time_ms=13_000,
                        )
                    ],
                )

        async def fake_probe(path: Path, task_dir: Path) -> dict[str, object]:
            return {"streams": [{"codec_type": "audio"}]}

        async def fake_run(command: list[str], task_dir: Path, label: str) -> bytes:
            Path(command[-1]).write_bytes(b"normalized-audio")
            return b""

        async def fake_vad(*args: object, **kwargs: object) -> SpeechActivity:
            return SpeechActivity(
                has_speech=True,
                speech_ms=6400,
                analyzed_ms=20_000,
                max_probability=0.99,
            )

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            provider = SparseThenRichProvider()
            with (
                patch("backend.asr_pipeline.probe_media", side_effect=fake_probe),
                patch("backend.asr_pipeline.run_logged_command", side_effect=fake_run),
                patch("backend.asr_pipeline._analyze_vad_or_fail_open", side_effect=fake_vad),
            ):
                records = await transcribe_source_audio(
                    task_dir,
                    [_make_upload(task_dir, 0)],
                    provider,
                    lambda *args: None,
                    vad_enabled=True,
                )

        self.assertEqual(provider.calls, 2)
        self.assertEqual(records[0].semantic_retry_count, 1)
        self.assertIn("油茶很好喝", records[0].transcript.text)

    async def test_extracts_audio_and_persists_transcript(self) -> None:
        async def fake_probe(path: Path, task_dir: Path) -> dict[str, object]:
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        async def fake_run(command: list[str], task_dir: Path, label: str) -> bytes:
            Path(command[-1]).write_bytes(b"wav")
            return b""

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            raw_dir = task_dir / "raw"
            raw_dir.mkdir()
            source_path = raw_dir / "source.mp4"
            source_path.write_bytes(b"video")
            upload = UploadedAsset(
                original_name="source.mp4",
                stored_name="source.mp4",
                path=source_path,
                size=source_path.stat().st_size,
                content_type="video/mp4",
            )
            with (
                patch("backend.asr_pipeline.probe_media", side_effect=fake_probe),
                patch("backend.asr_pipeline.run_logged_command", side_effect=fake_run),
            ):
                records = await transcribe_source_audio(
                    task_dir,
                    [upload],
                    FakeASRProvider(),
                    lambda *args: None,
                )
            persisted = json.loads((task_dir / "asr_transcripts.json").read_text(encoding="utf-8"))

        self.assertEqual(records[0].status, "available")
        self.assertEqual(records[0].transcript.text, "本市重点项目今天正式启动。")
        self.assertEqual(persisted[0]["transcript"]["utterances"][0]["start_time_ms"], 500)

    async def test_vad_skips_silence_before_provider_call(self) -> None:
        class CountingProvider(FakeASRProvider):
            def __init__(self) -> None:
                self.calls = 0

            async def transcribe(self, audio_path: Path) -> ASRTranscript:
                self.calls += 1
                return await super().transcribe(audio_path)

        async def fake_probe(path: Path, task_dir: Path) -> dict[str, object]:
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        async def fake_run(command: list[str], task_dir: Path, label: str) -> bytes:
            _write_pcm_wav(Path(command[-1]), duration_ms=640)
            return b""

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            upload = _make_upload(task_dir, 0)
            provider = CountingProvider()
            with (
                patch("backend.asr_pipeline.probe_media", side_effect=fake_probe),
                patch("backend.asr_pipeline.run_logged_command", side_effect=fake_run),
            ):
                records = await transcribe_source_audio(
                    task_dir,
                    [upload],
                    provider,
                    lambda *args: None,
                    vad_enabled=True,
                )

            persisted = json.loads((task_dir / "asr_transcripts.json").read_text(encoding="utf-8"))

        self.assertEqual(provider.calls, 0)
        self.assertEqual(records[0].status, "no_speech")
        self.assertEqual(records[0].vad_speech_ms, 0)
        self.assertEqual(persisted[0]["status"], "no_speech")
        self.assertLess(persisted[0]["vad_max_probability"], 0.2)

    async def test_vad_error_fails_open_and_preserves_asr(self) -> None:
        class CountingProvider(FakeASRProvider):
            def __init__(self) -> None:
                self.calls = 0

            async def transcribe(self, audio_path: Path) -> ASRTranscript:
                self.calls += 1
                return await super().transcribe(audio_path)

        async def fake_probe(path: Path, task_dir: Path) -> dict[str, object]:
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        async def fake_run(command: list[str], task_dir: Path, label: str) -> bytes:
            Path(command[-1]).write_bytes(b"not-a-wave-file")
            return b""

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            provider = CountingProvider()
            with (
                patch("backend.asr_pipeline.probe_media", side_effect=fake_probe),
                patch("backend.asr_pipeline.run_logged_command", side_effect=fake_run),
            ):
                records = await transcribe_source_audio(
                    task_dir,
                    [_make_upload(task_dir, 0)],
                    provider,
                    lambda *args: None,
                    vad_enabled=True,
                )
            log_text = (task_dir / "task.log").read_text(encoding="utf-8")

        self.assertEqual(provider.calls, 1)
        self.assertEqual(records[0].status, "available")
        self.assertIn("VAD 分析失败，保守继续 ASR", log_text)

    async def test_asr_concurrency_is_capped_and_results_keep_source_order(self) -> None:
        class ConcurrentProvider(FakeASRProvider):
            def __init__(self) -> None:
                self.active = 0
                self.max_active = 0

            async def transcribe(self, audio_path: Path) -> ASRTranscript:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                try:
                    await asyncio.sleep(0.02)
                    return await super().transcribe(audio_path)
                finally:
                    self.active -= 1

        async def fake_probe(path: Path, task_dir: Path) -> dict[str, object]:
            return {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}

        async def fake_run(command: list[str], task_dir: Path, label: str) -> bytes:
            Path(command[-1]).write_bytes(b"wav")
            return b""

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            uploads = [_make_upload(task_dir, index) for index in range(8)]
            provider = ConcurrentProvider()
            progress_values: list[int] = []
            with (
                patch("backend.asr_pipeline.probe_media", side_effect=fake_probe),
                patch("backend.asr_pipeline.run_logged_command", side_effect=fake_run),
            ):
                records = await transcribe_source_audio(
                    task_dir,
                    uploads,
                    provider,
                    lambda completed, total, message: progress_values.append(completed),
                    concurrency=3,
                )

        self.assertEqual(provider.max_active, 3)
        self.assertEqual([record.source_index for record in records], list(range(8)))
        self.assertEqual(progress_values, list(range(1, 9)))

    def test_silero_vad_reports_silent_pcm_as_no_speech(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio_path = Path(directory) / "silence.wav"
            _write_pcm_wav(audio_path, duration_ms=320)
            activity = analyze_speech_activity(
                audio_path,
                threshold=0.2,
                min_speech_ms=32,
            )

        self.assertFalse(activity.has_speech)
        self.assertEqual(activity.speech_ms, 0)
        self.assertEqual(activity.analyzed_ms, 320)
        self.assertLess(activity.max_probability, 0.2)

    def test_asr_concurrency_configuration_is_limited_to_four(self) -> None:
        self.assertEqual(Settings(asr_concurrency=4).asr_concurrency, 4)
        with self.assertRaises(ValidationError):
            Settings(asr_concurrency=5)

    async def test_matches_high_confidence_overlapping_sync_sound(self) -> None:
        class FakeEmbedding:
            async def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        sentence = Sentence(sentence_id=0, text="本市重点项目今天正式启动。")
        shot = AnnotatedShot(
            shot_id=3,
            source_index=0,
            source_scene_index=0,
            source_name="source.mp4",
            norm_path="norm/norm_0.mp4",
            start=0.0,
            end=3.0,
            duration=3.0,
            thumb_path="thumbs/shot_3.jpg",
            status="available",
            description="项目启动现场",
            scene_type="outdoor",
            subjects=["工作人员"],
            actions=["启动项目"],
            keywords=["项目", "启动", "现场"],
            quality=VisionQuality(sharp=0.9, bright=0.8),
        )
        record = SourceASRRecord(
            source_index=0,
            source_name="source.mp4",
            source_media_path="raw/source.mp4",
            asr_audio_path="asr/source_0.wav",
            status="available",
            transcript=await FakeASRProvider().transcribe(Path("unused.wav")),
        )
        plan = MatchPlanItem(
            sentence_id=0,
            text=sentence.text,
            shot_id=1,
            confidence=0.8,
            candidates=[MatchCandidate(shot_id=1, similarity=0.8)],
        )

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            updated = await apply_sync_sound_matches(
                task_dir,
                [sentence],
                [shot],
                [plan],
                [record],
                FakeEmbedding(),
                similarity_threshold=0.88,
                min_text_overlap=0.2,
                max_duration_seconds=15.0,
            )

        self.assertIsNotNone(updated[0].sync_sound)
        assert updated[0].sync_sound is not None
        self.assertEqual(updated[0].shot_id, 3)
        self.assertEqual(updated[0].sync_sound.source_media_path, "raw/source.mp4")
        self.assertAlmostEqual(updated[0].sync_sound.start, 0.38, places=6)
        self.assertAlmostEqual(updated[0].sync_sound.end, 2.38, places=6)
        self.assertEqual(updated[0].sync_sound.similarity, 1.0)

    async def test_sync_sound_cannot_collide_with_another_sentence_shot(self) -> None:
        class FakeEmbedding:
            async def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        text = "本市重点项目今天正式启动。"
        sentences = [
            Sentence(sentence_id=index, text=text)
            for index in range(2)
        ]
        sync_shot = AnnotatedShot(
            shot_id=3,
            source_index=0,
            source_scene_index=0,
            source_name="source.mp4",
            norm_path="norm/norm_0.mp4",
            start=0.0,
            end=3.0,
            duration=3.0,
            status="available",
            description="项目启动现场",
            quality=VisionQuality(sharp=0.9, bright=0.8),
        )
        record = SourceASRRecord(
            source_index=0,
            source_name="source.mp4",
            source_media_path="raw/source.mp4",
            asr_audio_path="asr/source_0.wav",
            status="available",
            transcript=await FakeASRProvider().transcribe(Path("unused.wav")),
        )
        plans = [
            MatchPlanItem(
                sentence_id=0,
                text=text,
                shot_id=1,
                confidence=0.8,
                candidates=[MatchCandidate(shot_id=1, similarity=0.8)],
            ),
            MatchPlanItem(
                sentence_id=1,
                text=text,
                shot_id=sync_shot.shot_id,
                confidence=0.8,
                candidates=[MatchCandidate(shot_id=sync_shot.shot_id, similarity=0.8)],
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            updated = await apply_sync_sound_matches(
                Path(directory),
                sentences,
                [sync_shot],
                plans,
                [record],
                FakeEmbedding(),
                similarity_threshold=0.88,
                min_text_overlap=0.2,
                max_duration_seconds=15.0,
            )

        self.assertIsNone(updated[0].sync_sound)
        self.assertIsNotNone(updated[1].sync_sound)
        self.assertEqual([item.shot_id for item in updated], [1, 3])
        self.assertEqual(len({item.shot_id for item in updated}), 2)


class StageTwoPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_asr_and_normalization_branches_overlap(self) -> None:
        asr_started = asyncio.Event()
        normalization_started = asyncio.Event()

        async def fake_transcribe(
            task_dir: Path,
            uploads: list[UploadedAsset],
            provider: FakeASRProvider,
            progress: Any,
            **kwargs: object,
        ) -> list[SourceASRRecord]:
            asr_started.set()
            await asyncio.wait_for(normalization_started.wait(), timeout=1.0)
            progress(1, 1, "同期声识别 1/1")
            return [
                SourceASRRecord(
                    source_index=0,
                    source_name=uploads[0].original_name,
                    source_media_path="raw/source_0.mp4",
                    status="no_audio",
                )
            ]

        async def fake_normalize(
            task_dir: Path,
            uploads: list[UploadedAsset],
            progress: Any,
        ) -> list[Path]:
            normalization_started.set()
            await asyncio.wait_for(asr_started.wait(), timeout=1.0)
            output_path = task_dir / "norm" / "norm_0.mp4"
            output_path.parent.mkdir()
            output_path.write_bytes(b"video")
            progress(1, 1, "素材规格化 1/1")
            return [output_path]

        class Reporter:
            def __init__(self) -> None:
                self.fractions: list[float] = []

            def start_stage(self, record: object, stage_number: int, message: str) -> None:
                return None

            def update_stage(
                self,
                record: object,
                stage_number: int,
                fraction: float,
                message: str,
            ) -> None:
                self.fractions.append(fraction)

            def complete_stage(self, record: object, stage_number: int, message: str) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            upload = _make_upload(task_dir, 0)
            record = SimpleNamespace(task_id="pipeline-test", task_dir=task_dir, uploads=[upload])
            reporter = Reporter()
            settings = Settings(sync_sound_enabled=True, asr_concurrency=2)
            with (
                patch("backend.pipeline.create_asr_provider", return_value=ContextFakeASRProvider()),
                patch("backend.pipeline.transcribe_source_audio", side_effect=fake_transcribe),
                patch("backend.pipeline.normalize_assets", side_effect=fake_normalize),
            ):
                normalized, source_asr = await _run_normalization(record, reporter, settings)

        self.assertTrue(asr_started.is_set())
        self.assertTrue(normalization_started.is_set())
        self.assertEqual(len(normalized), 1)
        self.assertEqual(source_asr[0].status, "no_audio")
        self.assertEqual(max(reporter.fractions), 1.0)


def _server_response(payload: dict[str, object], *, sequence: int, final: bool) -> bytes:
    encoded = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    flags = 0x3 if final else 0x1
    header = bytes([0x11, (0x9 << 4) | flags, 0x11, 0x00])
    return header + struct.pack(">iI", sequence, len(encoded)) + encoded


def _server_error(code: int, message: str) -> bytes:
    encoded = json.dumps({"message": message}).encode("utf-8")
    header = bytes([0x11, 0xF0, 0x10, 0x00])
    return header + struct.pack(">II", code, len(encoded)) + encoded


def _decode_client_json(frame: bytes) -> dict[str, object]:
    payload_size = struct.unpack_from(">I", frame, 4)[0]
    payload = gzip.decompress(frame[8 : 8 + payload_size])
    result = json.loads(payload.decode("utf-8"))
    assert isinstance(result, dict)
    return result


def _make_upload(task_dir: Path, source_index: int) -> UploadedAsset:
    raw_dir = task_dir / "raw"
    raw_dir.mkdir(exist_ok=True)
    source_path = raw_dir / f"source_{source_index}.mp4"
    source_path.write_bytes(b"video")
    return UploadedAsset(
        original_name=source_path.name,
        stored_name=source_path.name,
        path=source_path,
        size=source_path.stat().st_size,
        content_type="video/mp4",
    )


def _write_pcm_wav(path: Path, *, duration_ms: int) -> None:
    sample_count = 16_000 * duration_ms // 1000
    path.parent.mkdir(parents=True, exist_ok=True)
    import wave

    with wave.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(16_000)
        audio_file.writeframes(b"\x00\x00" * sample_count)


if __name__ == "__main__":
    unittest.main()
