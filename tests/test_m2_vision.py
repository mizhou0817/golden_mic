import asyncio
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import httpx

from backend.asr_pipeline import SourceASRRecord
from backend.models import Shot, VisionAnnotation
from backend.providers.asr import ASRTranscript, ASRUtterance
from backend.providers.vision import (
    KimiVisionProvider,
    VISION_SYSTEM_PROMPT,
    VisionProvider,
    VisionProviderError,
    VolcengineVisionProvider,
)
from backend.vision_pipeline import VisionProcessingError, annotate_shots, extract_shot_thumbnail


VALID_ANNOTATION = {
    "description": "市民在新闻发布厅听取介绍",
    "scene_type": "indoor",
    "subjects": ["市民", "工作人员"],
    "actions": ["听取介绍"],
    "keywords": ["发布厅", "市民", "介绍"],
    "quality": {"sharp": 0.91, "bright": 0.84},
}


class VisionProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_entity_verification_parses_visible_evidence(self) -> None:
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_payload
            request_payload = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "verified": True,
                                        "confidence": 0.92,
                                        "matched_entities": ["油茶"],
                                        "evidence": "人物从锅中盛汤并加入炒米",
                                        "preferred_relative_time": 4.2,
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VisionProvider(
                base_url="https://vision.example/v1",
                api_key="test-key",
                model="vision-test",
                client=client,
                max_retries=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                image_path = Path(directory) / "shot_35.jpg"
                image_path.write_bytes(b"jpeg-test-data")
                result = await provider.verify_visual_entities(
                    image_path,
                    "灌阳油茶",
                    ["灌阳油茶", "油茶"],
                )

        self.assertTrue(result.verified)
        self.assertEqual(result.matched_entities, ["油茶"])
        self.assertAlmostEqual(result.preferred_relative_time or 0.0, 4.2)
        self.assertEqual(request_payload["messages"][0]["role"], "system")
        self.assertEqual(request_payload["messages"][1]["role"], "user")

    async def test_retries_and_strips_markdown_fence(self) -> None:
        attempts = 0
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts, request_payload
            attempts += 1
            request_payload = json.loads(request.content)
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            if attempts < 3:
                return httpx.Response(500, json={"error": "temporary"})
            content = f"```json\n{json.dumps(VALID_ANNOTATION, ensure_ascii=False)}\n```"
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = VisionProvider(
                base_url="https://vision.example/v1",
                api_key="test-key",
                model="vision-test",
                client=client,
                max_retries=2,
                backoff_base=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                image_path = Path(directory) / "frame.jpg"
                image_path.write_bytes(b"jpeg-test-data")
                annotation = await provider.annotate_image(image_path)

        self.assertEqual(attempts, 3)
        self.assertEqual(annotation.description, VALID_ANNOTATION["description"])
        self.assertEqual(request_payload["messages"][0]["content"], VISION_SYSTEM_PROMPT)
        image_url = request_payload["messages"][1]["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/jpeg;base64,"))

    async def test_invalid_json_is_retried_and_reported(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VisionProvider(
                base_url="https://vision.example/v1",
                api_key="test-key",
                model="vision-test",
                client=client,
                max_retries=2,
                backoff_base=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                image_path = Path(directory) / "frame.jpg"
                image_path.write_bytes(b"jpeg-test-data")
                with self.assertRaisesRegex(VisionProviderError, "3 次尝试"):
                    await provider.annotate_image(image_path)
        self.assertEqual(attempts, 3)


class VolcengineVisionProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_responses_api_rotates_keys_and_parses_output_text(self) -> None:
        requests: list[httpx.Request] = []
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_payload
            requests.append(request)
            request_payload = json.loads(request.content)
            if len(requests) == 1:
                return httpx.Response(401, json={"error": {"message": "invalid api key"}})
            content = f"```json\n{json.dumps(VALID_ANNOTATION, ensure_ascii=False)}\n```"
            return httpx.Response(
                200,
                json={
                    "id": "resp_test",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    ],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineVisionProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_keys="first-key, second-key",
                model="doubao-seed-2-1-pro-260628",
                client=client,
                max_retries=1,
                backoff_base=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                image_path = Path(directory) / "frame.jpg"
                image_path.write_bytes(b"jpeg-test-data")
                annotation = await provider.annotate_image(image_path)

        self.assertEqual(annotation.description, VALID_ANNOTATION["description"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url.path, "/api/v3/responses")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer first-key")
        self.assertEqual(requests[1].headers["Authorization"], "Bearer second-key")
        self.assertEqual(request_payload["model"], "doubao-seed-2-1-pro-260628")
        self.assertEqual(request_payload["thinking"], {"type": "disabled"})
        content = request_payload["input"][0]["content"]
        self.assertEqual(content[0]["type"], "input_image")
        self.assertTrue(content[0]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(content[1]["type"], "input_text")
        self.assertIn(VISION_SYSTEM_PROMPT, content[1]["text"])

    async def test_missing_output_text_is_retried_and_reported(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(200, json={"output": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineVisionProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_keys="test-key",
                model="doubao-seed-2-1-pro-260628",
                client=client,
                max_retries=2,
                backoff_base=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                image_path = Path(directory) / "frame.jpg"
                image_path.write_bytes(b"jpeg-test-data")
                with self.assertRaisesRegex(VisionProviderError, "3 次尝试"):
                    await provider.annotate_image(image_path)

        self.assertEqual(attempts, 3)


class KimiVisionProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_uses_k3_visual_format_without_temperature(self) -> None:
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(VALID_ANNOTATION, ensure_ascii=False)}}
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = KimiVisionProvider(
                base_url="https://api.moonshot.cn/v1",
                api_key="test-key",
                model="kimi-k3",
                client=client,
                max_retries=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                image_path = Path(directory) / "contact-sheet.jpg"
                image_path.write_bytes(b"jpeg-test-data")
                annotation = await provider.annotate_image(image_path)

        self.assertEqual(annotation.description, VALID_ANNOTATION["description"])
        self.assertEqual(request_payload["model"], "kimi-k3")
        self.assertEqual(request_payload["reasoning_effort"], "low")
        self.assertEqual(request_payload["response_format"], {"type": "json_object"})
        self.assertNotIn("temperature", request_payload)
        content = request_payload["messages"][1]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    async def test_truncates_description_when_model_slightly_exceeds_limit(self) -> None:
        overlong = {**VALID_ANNOTATION, "description": "甲" * 41}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(overlong, ensure_ascii=False)}}
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = KimiVisionProvider(
                base_url="https://api.moonshot.cn/v1",
                api_key="test-key",
                model="kimi-k3",
                client=client,
                max_retries=0,
            )
            with tempfile.TemporaryDirectory() as directory:
                image_path = Path(directory) / "contact-sheet.jpg"
                image_path.write_bytes(b"jpeg-test-data")
                annotation = await provider.annotate_image(image_path)

        self.assertEqual(annotation.description, "甲" * 40)


class FakeVisionProvider:
    def __init__(self, failing_ids: set[int] | None = None) -> None:
        self.failing_ids = failing_ids or set()
        self.active = 0
        self.max_active = 0

    def validate_configuration(self) -> None:
        return None

    async def annotate_image(self, image_path: Path) -> VisionAnnotation:
        shot_id = int(image_path.stem.split("_")[-1])
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            if shot_id in self.failing_ids:
                raise VisionProviderError("模拟单镜头调用失败")
            return VisionAnnotation.model_validate(
                {**VALID_ANNOTATION, "description": f"镜头{shot_id}中的新闻现场"}
            )
        finally:
            self.active -= 1


class VisionAnnotationPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrency_is_capped_and_single_failure_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            provider = FakeVisionProvider(failing_ids={2})
            progress_messages: list[str] = []

            async def fake_extract(root: Path, shot: Shot) -> Path:
                thumb_path = root / "thumbs" / f"shot_{shot.shot_id}.jpg"
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                thumb_path.write_bytes(b"jpeg")
                return thumb_path

            with patch("backend.vision_pipeline.extract_shot_thumbnail", side_effect=fake_extract):
                annotated = await annotate_shots(
                    task_dir,
                    _make_shots(8),
                    provider,
                    lambda completed, total, message: progress_messages.append(message),
                    concurrency=20,
                    source_records=[
                        SourceASRRecord(
                            source_index=0,
                            source_name="input.mp4",
                            source_media_path="raw/input.mp4",
                            status="available",
                            transcript=ASRTranscript(
                                text="现场正在制作灌阳油茶。",
                                duration_ms=8000,
                                utterances=[
                                    ASRUtterance(
                                        text="现场正在制作灌阳油茶。",
                                        start_time_ms=0,
                                        end_time_ms=8000,
                                    )
                                ],
                            ),
                        )
                    ],
                )

            self.assertLessEqual(provider.max_active, 4)
            self.assertEqual(len(annotated), 8)
            self.assertEqual(annotated[2].status, "unavailable")
            self.assertTrue(all(shot.status == "available" for index, shot in enumerate(annotated) if index != 2))
            self.assertIn("灌阳油茶", annotated[0].source_transcript)
            self.assertIn("现场语音识别", annotated[0].search_text)
            self.assertEqual(len(annotated[0].source_transcript_spans), 1)
            self.assertEqual(annotated[0].source_transcript_spans[0].start, 0.0)
            self.assertEqual(annotated[0].source_transcript_spans[0].end, 1.0)
            self.assertEqual(len(annotated[0].semantic_windows), 1)
            self.assertIn("灌阳油茶", annotated[0].semantic_windows[0].text)
            self.assertEqual(progress_messages[-1], "画面理解 8/8")
            self.assertTrue((task_dir / "shots_annotated.json").is_file())
            log_text = (task_dir / "task.log").read_text(encoding="utf-8")
            self.assertIn("description=镜头0中的新闻现场", log_text)
            self.assertIn("unavailable=模拟单镜头调用失败", log_text)

    async def test_wrong_api_key_marks_all_unavailable_then_fails_clearly(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(401, json={"error": "invalid api key"})

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)

            async def fake_extract(root: Path, shot: Shot) -> Path:
                thumb_path = root / "thumbs" / f"shot_{shot.shot_id}.jpg"
                thumb_path.parent.mkdir(parents=True, exist_ok=True)
                thumb_path.write_bytes(b"jpeg")
                return thumb_path

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = VisionProvider(
                    base_url="https://vision.example/v1",
                    api_key="wrong-key",
                    model="vision-test",
                    client=client,
                    max_retries=2,
                    backoff_base=0,
                )
                with patch("backend.vision_pipeline.extract_shot_thumbnail", side_effect=fake_extract):
                    with self.assertRaisesRegex(VisionProcessingError, "检查 VOLCENGINE_VISION_BASE_URL"):
                        await annotate_shots(task_dir, _make_shots(2), provider, lambda *args: None)

            self.assertEqual(attempts, 6)
            payload = json.loads((task_dir / "shots_annotated.json").read_text(encoding="utf-8"))
            self.assertTrue(all(shot["status"] == "unavailable" for shot in payload))


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class ThumbnailExtractionTest(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_midpoint_jpeg_with_512_long_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            norm_dir = task_dir / "norm"
            norm_dir.mkdir()
            video_path = norm_dir / "norm_0.mp4"
            command = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=1920x1080:r=30:d=2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video_path),
            ]
            result = subprocess.run(command, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            shot = _make_shots(1)[0]
            shot = shot.model_copy(update={"norm_path": "norm/norm_0.mp4", "start": 0.0, "end": 2.0, "duration": 2.0})

            thumb_path = await extract_shot_thumbnail(task_dir, shot)
            image = cv2.imread(str(thumb_path))

            self.assertIsNotNone(image)
            self.assertEqual(max(image.shape[:2]), 512)
            self.assertEqual(thumb_path.name, "shot_0.jpg")


def _make_shots(count: int) -> list[Shot]:
    return [
        Shot(
            shot_id=index,
            source_index=0,
            source_scene_index=index,
            source_name="input.mp4",
            norm_path="norm/norm_0.mp4",
            start=float(index),
            end=float(index + 1),
            duration=1.0,
        )
        for index in range(count)
    ]


if __name__ == "__main__":
    unittest.main()
