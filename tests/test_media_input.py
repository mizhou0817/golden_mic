"""Synthetic media only; no main import, real task data, credentials or cloud calls."""
import base64
import io
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import cv2
import numpy as np
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from starlette.datastructures import Headers

from backend.config import Settings
from backend.media import probe_media
from backend.media_input import (
    _decode_photo, _image_dimensions, align_student_narration, append_source_notes,
    parse_asset_options, prepare_media_inputs, processing_uploads, synthesize_student_narration,
)
from backend.models import (
    AnnotatedShot, EditingPreferences, MatchCandidate, MatchPlanItem, PublicLimitsResponse,
    Sentence, TaskCreateResponse, UploadedAsset, VisionQuality,
)
from backend.providers.asr import ASRTranscript, ASRUtterance
from backend.storage import save_uploads


def upload(data: bytes, name: str, content_type: str = "application/octet-stream") -> UploadFile:
    return UploadFile(io.BytesIO(data), filename=name, size=len(data), headers=Headers({"content-type": content_type}))


def wav_bytes(seconds: float = 3.0, channels: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        samples = (np.sin(np.arange(round(16000 * seconds)) * (440 * 2 * np.pi / 16000)) * 2000).astype("<i2")
        audio.writeframes(np.repeat(samples, channels).tobytes())
    return output.getvalue()


def transcript() -> ASRTranscript:
    return ASRTranscript(text="你好世界。欢迎大家。", duration_ms=3000, utterances=[
        ASRUtterance(text="你好世界。", start_time_ms=200, end_time_ms=1200),
        ASRUtterance(text="欢迎大家。", start_time_ms=1500, end_time_ms=2800),
    ])


def sentences() -> list[Sentence]:
    return [Sentence(sentence_id=0, text="你好世界。"), Sentence(sentence_id=1, text="欢迎大家。")]


class OptionAndAlignmentTests(unittest.TestCase):
    def test_optional_models_keep_old_payloads_valid(self) -> None:
        asset = UploadedAsset(original_name="a.mp4", stored_name="a.mp4", path=Path("a.mp4"), size=1, content_type="video/mp4")
        self.assertEqual(asset.note, "")
        self.assertIsNone(asset.trim_end)
        limits = PublicLimitsResponse(max_files=1, max_upload_bytes=1, max_total_upload_bytes=1, max_script_length=1)
        self.assertIsNone(limits.allowed_extensions)

    def test_task_create_response_requires_bounded_token_for_every_task(self) -> None:
        schema = TaskCreateResponse.model_json_schema()
        self.assertIn("access_token", schema["required"])
        token_schema = schema["properties"]["access_token"]
        self.assertEqual((token_schema["type"], token_schema["minLength"], token_schema["maxLength"]),
                         ("string", 32, 256))
        for task_id in ("task", "classroom"):
            for payload in ({}, {"access_token": None}, {"access_token": ""},
                            {"access_token": "x" * 31}, {"access_token": "x" * 257}):
                with self.subTest(task_id=task_id, payload=payload), self.assertRaises(ValidationError) as caught:
                    TaskCreateResponse.model_validate({"task_id": task_id, **payload})
                self.assertEqual([error["loc"] for error in caught.exception.errors()], [("access_token",)])
            for length in (32, 256):
                response = TaskCreateResponse(task_id=task_id, access_token="x" * length)
                self.assertEqual(response.model_dump(), {"task_id": task_id, "access_token": "x" * length})

    def test_longer_stream_duration_cannot_hide_behind_container_hint(self) -> None:
        from backend.media import source_media_duration
        self.assertEqual(source_media_duration({"format": {"duration": "1"}, "streams": [
            {"codec_type": "video", "duration": "1900"}]}), 1900)

    def test_options_strict_bounds_and_unknown_keys(self) -> None:
        invalid = ["{}", "[]", '[{"note": "123456789012345678901"}]', '[{"note": "a\\nb"}]',
                   '[{"trim_start": true}]', '[{"trim_start": "1"}]', '[{"trim_end": NaN}]',
                   '[{"trim_end": Infinity}]', '[{"trim_start": -1}]', '[{"trim_end": 0}]',
                   '[{"trim_start": 2, "trim_end": 1}]', '[{"path": "C:/secret"}]', '[{"filter": "movie=x"}]']
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_asset_options(value, 1)
        self.assertEqual(parse_asset_options(None, 1), [{"note": "", "trim_start": 0, "trim_end": None}])

    def test_alignment_uses_real_timestamps_and_preserves_full_recording(self) -> None:
        aligned = align_student_narration(sentences(), transcript(), 3)
        self.assertEqual([(a, b) for a, b, _ in aligned], [(0, 1.35), (1.35, 3)])
        self.assertAlmostEqual(aligned[1][2][0].start, 0.15)
        self.assertAlmostEqual(aligned[0][2][0].end, 1.2)

    def test_normalized_text_alignment(self) -> None:
        tr = ASRTranscript(text="ABC，１２３。", utterances=[ASRUtterance(text="ABC１２３", start_time_ms=0, end_time_ms=900)])
        self.assertEqual(len(align_student_narration([Sentence(sentence_id=0, text="abc123")], tr, 1)), 1)

    def test_never_proportionally_splits_one_utterance(self) -> None:
        tr = ASRTranscript(text="你好世界欢迎大家", utterances=[ASRUtterance(text="你好世界欢迎大家", start_time_ms=0, end_time_ms=2900)])
        with self.assertRaisesRegex(ValueError, "无法可靠对齐"):
            align_student_narration(sentences(), tr, 3)

    def test_rejects_missing_repeated_overlapping_or_unsafe_timing(self) -> None:
        for mode in ("mismatch", "repeated", "overlap", "out_of_range", "no_pause", "indefinite", "missing_text"):
            tr = transcript()
            if mode == "mismatch":
                tr.utterances[1].text = "其他内容"
            elif mode == "repeated":
                tr.utterances.append(tr.utterances[1])
            elif mode == "overlap":
                tr.utterances[1].start_time_ms = 1000
            elif mode == "out_of_range":
                tr.utterances[1].end_time_ms = 3100
            elif mode == "no_pause":
                tr.utterances[1].start_time_ms = 1250
            elif mode == "indefinite":
                tr.utterances[0].definite = False
            else:
                tr.text = ""
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "无法可靠对齐"):
                align_student_narration(sentences(), tr, 3)

    def test_notes_are_retrieval_context_not_visual_facts(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            asset = UploadedAsset(original_name="x.png", stored_name="x.png", path=root / "x.png", size=1,
                                  content_type="image/png", note="油茶")
            shot = AnnotatedShot(shot_id=0, source_index=0, source_scene_index=0, source_name="x.png",
                                 norm_path="norm/0.mp4", start=0, end=3, duration=3, status="available",
                                 description="有人做饭", keywords=["厨房", "餐具", "食物"], quality=VisionQuality(sharp=1, bright=1))
            append_source_notes(root, [shot], [asset])
            append_source_notes(root, [shot], [asset])
            self.assertEqual(shot.search_text.count("油茶"), 1)
            self.assertIn("未经画面验证", shot.search_text)
            self.assertEqual(shot.description, "有人做饭")
            self.assertEqual(shot.entities, [])
            from backend.matching import _shot_retrieval_text
            self.assertIn("油茶", _shot_retrieval_text(shot))


class LocalPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "raw").mkdir()
        self.settings = Settings(_env_file=None, data_dir=self.root / "isolated-data")

    async def test_preserves_original_name_and_raw_bytes(self) -> None:
        data = b"raw data"
        assets = await save_uploads(self.root, [upload(data, r"C:\camera\原片.png", "image/png")], self.settings)
        self.assertEqual(assets[0].original_name, r"C:\camera\原片.png")
        self.assertRegex(assets[0].stored_name, r"^[a-f0-9]{32}\.png$")
        self.assertEqual(assets[0].path.read_bytes(), data)

    async def test_bad_image_and_bomb_rejected_before_ffmpeg(self) -> None:
        huge = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 100000, 100000) + b"\0" * 9
        for index, data in enumerate((b"not an image", huge)):
            assets = await save_uploads(self.root, [upload(data, f"bad{index}.png")], self.settings)
            with patch("backend.media_input.run_logged_command", new_callable=AsyncMock) as command:
                with self.assertRaises(HTTPException) as caught:
                    await prepare_media_inputs(self.root, assets, None, None, self.settings)
                self.assertEqual(caught.exception.status_code, 422)
                command.assert_not_awaited()

    async def test_gif_bomb_rejected_before_native_decode(self) -> None:
        data = b"GIF89a" + struct.pack("<HH", 65535, 65535) + b"\0\0\0"
        with patch("backend.media_input.cv2.imdecode") as decoder:
            with self.assertRaises(ValueError):
                _image_dimensions(data, ".gif", self.settings)
            decoder.assert_not_called()

    async def test_path_escape_rejected(self) -> None:
        path = self.root / "outside.mp4"
        path.write_bytes(b"x")
        asset = UploadedAsset(original_name="x", stored_name="../outside.mp4", path=path, size=1, content_type="video/mp4")
        with self.assertRaises(HTTPException):
            await prepare_media_inputs(self.root, [asset], None, None, self.settings)

    async def test_original_limits_checked_before_trim(self) -> None:
        assets = await save_uploads(self.root, [upload(b"x", "x.mp4")], self.settings)
        probe = {"streams": [{"codec_type": "video", "width": 640, "height": 360, "avg_frame_rate": "30/1"}],
                 "format": {"duration": "1801"}}
        with patch("backend.media_input._probe", new=AsyncMock(return_value=probe)), patch("backend.media_input.run_logged_command", new_callable=AsyncMock) as command:
            with self.assertRaises(HTTPException) as caught:
                await prepare_media_inputs(self.root, assets, '[{"trim_end": 1}]', None, self.settings)
            self.assertIn("单文件上限", str(caught.exception.detail))
            command.assert_not_awaited()

    async def test_total_original_duration_not_trimmed_duration(self) -> None:
        assets = await save_uploads(self.root, [upload(b"x", "x.mp4"), upload(b"x", "y.mp4")], self.settings)
        self.settings.max_total_source_duration_seconds = 1800
        with patch("backend.media_input.validate_original_asset", new=AsyncMock(return_value=1000)), patch("backend.media_input.run_logged_command", new_callable=AsyncMock) as command:
            with self.assertRaises(HTTPException):
                await prepare_media_inputs(self.root, assets, '[{"trim_end": 1}, {"trim_end": 1}]', None, self.settings)
            command.assert_not_awaited()

    async def test_self_voice_skips_source_sync_without_changing_visuals(self) -> None:
        from backend.pipeline import _run_semantic_matching
        from backend.matching import extract_visual_beats
        (self.root / "own_voice.wav").write_bytes(b"local-placeholder")
        sentence = Sentence(sentence_id=0, text="欢迎大家。", visual_beats=extract_visual_beats("欢迎大家。"))
        shot = AnnotatedShot(shot_id=7, source_index=0, source_scene_index=0, source_name="a",
                             norm_path="norm/0.mp4", start=0, end=3, duration=3, status="available",
                             description="学校活动", quality=VisionQuality(sharp=1, bright=1))
        plan = [MatchPlanItem(sentence_id=0, text=sentence.text, shot_id=7, confidence=1,
                              candidates=[MatchCandidate(shot_id=7, similarity=1)])]
        record = SimpleNamespace(task_dir=self.root, task_id="isolated", preferences=EditingPreferences())
        self.settings.entity_verification_enabled = False
        with patch("backend.pipeline.EmbeddingProvider.from_settings", return_value=AsyncMock()), patch("backend.pipeline.LLMProvider.from_settings", return_value=AsyncMock()), patch("backend.pipeline.build_match_plan", new=AsyncMock(return_value=plan)), patch("backend.pipeline.apply_sync_sound_matches", new_callable=AsyncMock) as sync:
            result = await _run_semantic_matching(record, Mock(), [sentence], [shot], [], self.settings)
            sync.assert_not_awaited()
        self.assertEqual(result[0].shot_id, 7)
        self.assertIsNone(result[0].sync_sound)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class RealMediaPreparationTests(LocalPreparationTests):
    async def test_photos_produce_three_second_h264_and_preserve_original(self) -> None:
        gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        for suffix, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".gif", "image/gif")):
            data = gif if suffix == ".gif" else cv2.imencode(suffix, np.zeros((32, 48, 3), np.uint8))[1].tobytes()
            assets = await save_uploads(self.root, [upload(data, "照片" + suffix, mime)], self.settings)
            with patch("backend.media_input.create_asr_provider") as asr:
                result = await prepare_media_inputs(self.root, assets, '[{"note":"学校"}]', None, self.settings)
                asr.assert_not_called()
            self.assertEqual(result[0].path.read_bytes(), data)
            self.assertEqual(result[0].stored_name, assets[0].stored_name)
            self.assertEqual(result[0].size, len(data))
            derived = processing_uploads(self.root, result)[0]
            probe = await probe_media(derived.path, self.root)
            self.assertAlmostEqual(float(probe["format"]["duration"]), 3, places=2)
            video = probe["streams"][0]
            self.assertEqual((video["codec_name"], video["width"], video["height"]), ("h264", 1920, 1080))
            manifest = json.loads((self.root / "media_input_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["assets"][0]["note"], "学校")

    async def test_trim_preserves_audio_for_asr_and_original_bytes(self) -> None:
        source = self.root / "fixture.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x48:r=30:d=3",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
        data = source.read_bytes()
        assets = await save_uploads(self.root, [upload(data, "原片.mp4")], self.settings)
        result = await prepare_media_inputs(self.root, assets, '[{"trim_start":0.5,"trim_end":2}]', None, self.settings)
        self.assertEqual(result[0].path.read_bytes(), data)
        derived = processing_uploads(self.root, result)[0]
        probe = await probe_media(derived.path, self.root)
        self.assertTrue(any(s["codec_type"] == "audio" for s in probe["streams"]))
        self.assertAlmostEqual(float(probe["format"]["duration"]), 1.5, delta=0.08)
        # Prove stage 2 sends the derivative (with trimmed audio) to ASR, not raw.
        from backend.pipeline import _run_normalization
        record = SimpleNamespace(task_dir=self.root, task_id="isolated", uploads=result)
        provider = AsyncMock()
        with patch("backend.pipeline.create_asr_provider", return_value=provider), patch("backend.pipeline.transcribe_source_audio", new=AsyncMock(return_value=[])) as asr, patch("backend.pipeline.normalize_assets", new=AsyncMock(return_value=[])) as norm:
            await _run_normalization(record, Mock(), self.settings)
            self.assertEqual(asr.await_args.args[1][0].path, derived.path)
            self.assertEqual(norm.await_args.args[1][0].path, derived.path)
            self.assertEqual(record.uploads[0].path, assets[0].path)

    async def test_voice_standardization_and_actual_asr_alignment_pipeline(self) -> None:
        photo = cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
        assets = await save_uploads(self.root, [upload(photo, "a.png")], self.settings)
        raw = wav_bytes()
        with patch("backend.media_input.create_asr_provider") as no_cloud:
            await prepare_media_inputs(self.root, assets, None, upload(raw, "朗读.wav"), self.settings)
            no_cloud.assert_not_called()
        manifest = json.loads((self.root / "media_input_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((self.root / "raw" / manifest["own_voice"]["stored_name"]).read_bytes(), raw)
        provider = AsyncMock()
        provider.__aenter__.return_value = provider
        provider.validate_configuration = Mock()
        provider.transcribe.return_value = transcript()
        from backend.pipeline import _run_tts_synthesis
        plan = [MatchPlanItem(sentence_id=s.sentence_id, text=s.text, shot_id=i, confidence=1,
                              candidates=[MatchCandidate(shot_id=i, similarity=1)]) for i, s in enumerate(sentences())]
        record = SimpleNamespace(task_dir=self.root, task_id="isolated", script="你好世界。欢迎大家。", preferences=EditingPreferences())
        with patch("backend.media_input.create_asr_provider", return_value=provider), patch("backend.pipeline.create_tts_provider") as tts:
            timings = await _run_tts_synthesis(record, Mock(), sentences(), plan, self.settings)
            tts.assert_not_called()
            provider.transcribe.assert_awaited_once_with(self.root / "own_voice.wav")
        self.assertEqual([t.audio_kind for t in timings], ["sync", "sync"])
        self.assertTrue(all(t.tts_group_id is None and t.voice_profile_id.startswith("student-") and t.words for t in timings))
        self.assertAlmostEqual(sum(t.duration for t in timings), 3, delta=0.03)
        self.assertEqual([p.shot_id for p in plan], [0, 1])
        self.assertTrue((self.root / "student_narration.json").is_file())
        from backend.subtitles import generate_ass_subtitles
        generate_ass_subtitles(self.root, timings)

    async def test_voice_failure_does_not_touch_existing_final_or_timings(self) -> None:
        (self.root / "own_voice.wav").write_bytes(wav_bytes())
        (self.root / "final.mp4").write_bytes(b"unchanged-final")
        (self.root / "timings.json").write_bytes(b"unchanged-timings")
        provider = AsyncMock()
        provider.__aenter__.return_value = provider
        provider.validate_configuration = Mock()
        provider.transcribe.return_value = ASRTranscript(text="different", utterances=[])
        with patch("backend.media_input.create_asr_provider", return_value=provider):
            with self.assertRaisesRegex(ValueError, "无法可靠对齐"):
                await synthesize_student_narration(self.root, sentences(), self.settings, Mock())
        self.assertEqual((self.root / "final.mp4").read_bytes(), b"unchanged-final")
        self.assertEqual((self.root / "timings.json").read_bytes(), b"unchanged-timings")
        self.assertFalse((self.root / "student_narration.json").exists())

    async def test_voice_limits_and_channel_validation(self) -> None:
        from backend.media_input import _prepare_voice
        for name, data in (("bad.mp4", b"x"), ("empty.wav", b""), ("surround.wav", wav_bytes(0.1, 3))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                await _prepare_voice(self.root, upload(data, name), self.settings, 0)
        with patch("backend.media_input.MAX_OWN_VOICE_BYTES", 10):
            with self.assertRaises(HTTPException) as caught:
                await _prepare_voice(self.root, upload(wav_bytes(0.1), "large.wav"), self.settings, 0)
            self.assertEqual(caught.exception.status_code, 413)
        self.settings.max_source_duration_seconds_per_file = 0.1
        with self.assertRaises(ValueError):
            await _prepare_voice(self.root, upload(wav_bytes(0.2), "long.wav"), self.settings, 0)

    async def test_durationless_audio_measured_after_bounded_decode(self) -> None:
        from backend.media_input import _prepare_voice
        no_duration = {"streams": [{"codec_type": "audio", "channels": 1}], "format": {}}
        with patch("backend.media_input._probe", new=AsyncMock(return_value=no_duration)):
            result = await _prepare_voice(self.root, upload(wav_bytes(0.2), "browser.wav"), self.settings, 0)
            self.assertAlmostEqual(result["duration"], 0.2, places=2)
            self.settings.max_source_duration_seconds_per_file = 0.1
            with self.assertRaises(ValueError):
                await _prepare_voice(self.root, upload(wav_bytes(0.3), "too-long.wav"), self.settings, 0)

    async def test_disguised_playlist_is_rejected(self) -> None:
        assets = await save_uploads(self.root, [upload(b"#EXTM3U\nhttps://invalid.example/secret\n", "fake.mp4")], self.settings)
        with self.assertRaises(HTTPException):
            await prepare_media_inputs(self.root, assets, None, None, self.settings)


if __name__ == "__main__":
    unittest.main()