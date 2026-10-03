import json
import hashlib
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app, rate_counter, task_manager
from backend.tts_pipeline import SENTENCE_GAP_SECONDS


class _ProviderHandler(BaseHTTPRequestHandler):
    sample_mp3 = b""
    calls = {"vision": 0, "embedding": 0, "llm": 0, "tts": 0}
    lock = threading.Lock()

    def do_POST(self) -> None:
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        model = payload.get("model")
        if self.path == "/v1/embeddings" and model == "e2e-embed":
            with type(self).lock:
                type(self).calls["embedding"] += 1
            texts = payload["input"]
            self._send_json(
                200,
                {
                    "data": [
                        {"index": index, "embedding": [float(index + 1), 1.0, 0.5]}
                        for index in range(len(texts))
                    ]
                },
            )
            return
        if self.path == "/v1/chat/completions" and model == "e2e-vision":
            with type(self).lock:
                number = type(self).calls["vision"]
                type(self).calls["vision"] += 1
            annotation = {
                "description": f"新闻现场镜头{number}",
                "scene_type": "outdoor",
                "subjects": ["市民", "工作人员"],
                "actions": ["参与活动"],
                "keywords": ["新闻", "现场", "市民"],
                "quality": {"sharp": 0.75 + number * 0.05, "bright": 0.8},
            }
            self._send_json(
                200,
                {"choices": [{"message": {"content": json.dumps(annotation, ensure_ascii=False)}}]},
            )
            return
        if self.path == "/v1/chat/completions" and model == "kimi-k3":
            with type(self).lock:
                type(self).calls["llm"] += 1
            script = payload["messages"][1]["content"]
            segmented = script.replace("。", "。 / ", 2)
            self._send_json(200, {"choices": [{"message": {"content": segmented}}]})
            return
        if self.path == "/v1/chat/completions" and model == "e2e-llm":
            with type(self).lock:
                type(self).calls["llm"] += 1
            items = json.loads(payload["messages"][1]["content"])["sentences"]
            used: set[int] = set()
            decisions: list[dict[str, object]] = []
            for index, item in enumerate(items):
                candidate_ids = [candidate["shot_id"] for candidate in item["candidates"]]
                if index == len(items) - 1:
                    decisions.append(
                        {
                            "sentence_id": item["sentence_id"],
                            "shot_id": None,
                            "confidence": 0.2,
                            "alternates": [candidate_ids[0]],
                        }
                    )
                    continue
                selected = next(shot_id for shot_id in candidate_ids if shot_id not in used)
                used.add(selected)
                decisions.append(
                    {
                        "sentence_id": item["sentence_id"],
                        "shot_id": selected,
                        "confidence": 0.91 - index * 0.05,
                        "alternates": [shot_id for shot_id in candidate_ids if shot_id != selected][:2],
                    }
                )
            self._send_json(200, {"choices": [{"message": {"content": json.dumps(decisions)}}]})
            return
        if self.path == "/v1/audio/speech" and model == "e2e-tts":
            with type(self).lock:
                type(self).calls["tts"] += 1
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(type(self).sample_mp3)))
            self.end_headers()
            self.wfile.write(type(self).sample_mp3)
            return
        self._send_json(404, {"error": "unexpected request", "path": self.path, "model": model})

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return None


class FullM6FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        fixture_dir = Path(cls.temporary_directory.name)
        audio_path = fixture_dir / "sample.mp3"
        cls.source_path = fixture_dir / "input.mp4"
        _run_command(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=24000:duration=0.9",
                "-c:a", "libmp3lame", "-b:a", "64k", str(audio_path),
            ]
        )
        _ProviderHandler.sample_mp3 = audio_path.read_bytes()
        _ProviderHandler.calls = {"vision": 0, "embedding": 0, "llm": 0, "tts": 0}
        _run_command(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=2",
                "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=2",
                "-f", "lavfi", "-i", "color=c=white:s=640x360:r=30:d=2",
                "-f", "lavfi", "-i", "color=c=green:s=640x360:r=30:d=2",
                "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0,format=yuv420p[v]",
                "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", str(cls.source_path),
            ]
        )
        cls.provider_server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        cls.provider_thread = threading.Thread(target=cls.provider_server.serve_forever, daemon=True)
        cls.provider_thread.start()
        base_url = f"http://127.0.0.1:{cls.provider_server.server_port}/v1"
        settings = task_manager.settings
        cls.original_data_dir = settings.data_dir
        cls.original_tasks = dict(task_manager._tasks)  # pyright: ignore[reportPrivateUsage]
        settings.data_dir = fixture_dir / "tasks"
        task_manager._tasks.clear()  # pyright: ignore[reportPrivateUsage]
        settings.sync_sound_enabled = False
        settings.vision_provider = "openai-compatible"
        settings.vision_base_url = base_url
        settings.vision_api_key = "vision-key"
        settings.vision_model = "e2e-vision"
        settings.embedding_provider = "openai-compatible"
        settings.embed_base_url = base_url
        settings.embed_api_key = "embed-key"
        settings.embed_model = "e2e-embed"
        settings.llm_provider = "openai-compatible"
        settings.llm_base_url = base_url
        settings.llm_api_key = "llm-key"
        settings.llm_model = "e2e-llm"
        settings.kimi_base_url = base_url
        settings.kimi_api_key = "kimi-key"
        settings.kimi_model = "kimi-k3"
        settings.tts_provider = "openai"
        settings.tts_base_url = base_url
        settings.tts_api_key = "tts-key"
        settings.tts_model = "e2e-tts"
        settings.tts_voice = "alloy"
        rate_counter.clear()

    @classmethod
    def tearDownClass(cls) -> None:
        rate_counter.clear()
        task_manager.settings.data_dir = cls.original_data_dir
        task_manager._tasks.clear()  # pyright: ignore[reportPrivateUsage]
        task_manager._tasks.update(cls.original_tasks)  # pyright: ignore[reportPrivateUsage]
        cls.provider_server.shutdown()
        cls.provider_server.server_close()
        cls.provider_thread.join(timeout=2)
        cls.temporary_directory.cleanup()

    def test_all_ten_stages_remix_report_range_cors_and_cancel(self) -> None:
        script = (
            "本市今天发布人工智能产业扶持政策。"
            "多个重点项目将在本月正式启动。"
            "有关部门将持续完善配套服务措施。"
        )
        with TestClient(app) as client:
            cors = client.options(
                "/api/tasks",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                },
            )
            self.assertEqual(cors.status_code, 200)
            self.assertEqual(cors.headers["access-control-allow-origin"], "http://localhost:5173")

            task_id, task_token = self._submit(client, script)
            self.assertEqual(client.get(f"/api/tasks/{task_id}").status_code, 404)
            status_payload = self._wait(client, task_id, task_token)
            self.assertEqual(status_payload["status"], "done", status_payload)
            self.assertEqual(status_payload["progress"], 100)
            self.assertTrue(all(stage["status"] == "done" for stage in status_payload["stages"]))
            self.assertIsNotNone(status_payload["processing_started_at"])
            self.assertIsNotNone(status_payload["processing_completed_at"])
            self.assertGreater(status_payload["total_elapsed_seconds"], 0.0)
            self.assertTrue(all(stage["elapsed_seconds"] is not None for stage in status_payload["stages"]))
            self.assertEqual(status_payload["stages"][1]["name"], "同期声识别与本地格式适配")
            self.assertIn("匹配报告", status_payload["stages"][9]["message"])
            self.assertFalse(any("mock" in stage["message"].lower() for stage in status_payload["stages"]))

            record = task_manager.get(task_id)
            self.assertIsNotNone(record)
            assert record is not None
            required_files = [
                "upload_manifest.json", "asr_transcripts.json", "shots.json", "shots_annotated.json", "sentences.json",
                "match_plan.json", "pronunciation_plan.json", "narration_profile.json", "timings.json", "narration.m4a", "subs.ass",
                "subtitle_manifest.json", "edl.json",
                "segment_manifest.json", "source_timings.json", "source_edl.json",
                "video_only.mp4", "final.mp4", "report.json", "task.log",
            ]
            self.assertTrue(all((record.task_dir / file_name).is_file() for file_name in required_files))
            self.assertFalse((record.task_dir / "mediakit").exists())
            self.assertFalse((record.task_dir / "mediakit_manifest.json").exists())
            manifest = json.loads((record.task_dir / "upload_manifest.json").read_text(encoding="utf-8"))
            self.assertIn("created_at", manifest)

            report_response = client.get(
                f"/api/tasks/{task_id}/report",
                headers={"X-Task-Token": task_token},
            )
            report_response.raise_for_status()
            report = report_response.json()
            self.assertEqual(len(report["rows"]), 3)
            self.assertEqual([row["sentence_id"] for row in report["rows"]], [0, 1, 2])
            self.assertTrue(all(row["shot_id"] is not None and row["thumb_url"] for row in report["rows"]))
            self.assertGreater(report["rows"][2]["confidence"], 0.0)
            self.assertLess(report["rows"][2]["confidence"], 0.5)
            self.assertTrue(report["rows"][2]["is_fallback"])
            for row in report["rows"]:
                thumbnail = client.get(f"{row['thumb_url']}?token={task_token}")
                self.assertEqual(thumbnail.status_code, 200)
                self.assertEqual(thumbnail.headers["content-type"], "image/jpeg")

            video = client.get(
                f"/api/tasks/{task_id}/video?token={task_token}",
                headers={"Range": "bytes=0-255"},
            )
            self.assertEqual(video.status_code, 206)
            self.assertEqual(len(video.content), 256)
            self.assertEqual(video.headers["accept-ranges"], "bytes")
            log_text = (record.task_dir / "task.log").read_text(encoding="utf-8")
            self.assertIn("阶段 10 完成 start", log_text)
            self.assertIn("阶段 10 完成 done", log_text)
            self.assertNotIn("mock", log_text.lower())

            segment_paths = sorted((record.task_dir / "segments").glob("seg_*.mp4"))
            segment_hashes_before = {path.name: _sha256(path) for path in segment_paths}
            tts_hashes_before = {
                path.name: _sha256(path) for path in sorted((record.task_dir / "tts").glob("sent_*.mp3"))
            }
            rendered_segment_logs_before = log_text.count("渲染视频片段")
            initial_final_hash = _sha256(record.task_dir / "final.mp4")
            original_shot_id = report["rows"][0]["shot_id"]

            replacement_response = client.post(
                f"/api/tasks/{task_id}/replace-shot",
                json={
                    "sentence_id": 0,
                    "instruction": "换成蓝色背景的新闻活动全景镜头",
                },
                headers={"X-Task-Token": task_token},
            )
            self.assertEqual(replacement_response.status_code, 202, replacement_response.text)
            self.assertEqual(replacement_response.json(), {"task_id": task_id, "revision": 1})
            replacement_status = self._wait(client, task_id, task_token)
            self.assertEqual(replacement_status["status"], "done", replacement_status)
            self.assertEqual(replacement_status["revision"], 1)
            self.assertTrue(all(stage["status"] == "done" for stage in replacement_status["stages"]))
            self.assertIsNotNone(replacement_status["stages"][5]["elapsed_seconds"])
            self.assertIsNotNone(replacement_status["stages"][8]["elapsed_seconds"])
            self.assertIsNotNone(replacement_status["stages"][9]["elapsed_seconds"])
            self.assertTrue(
                all(
                    replacement_status["stages"][index]["elapsed_seconds"] is None
                    for index in [0, 1, 2, 3, 4, 6, 7]
                )
            )

            replacement_report = client.get(
                f"/api/tasks/{task_id}/report",
                headers={"X-Task-Token": task_token},
            ).json()
            self.assertNotEqual(replacement_report["rows"][0]["shot_id"], original_shot_id)
            self.assertEqual(
                replacement_report["rows"][0]["replacement_instruction"],
                "换成蓝色背景的新闻活动全景镜头",
            )
            self.assertEqual(
                {path.name: _sha256(path) for path in sorted((record.task_dir / "tts").glob("sent_*.mp3"))},
                tts_hashes_before,
            )
            self.assertEqual(
                {path.name: _sha256(path) for path in segment_paths},
                segment_hashes_before,
            )
            self.assertNotEqual(_sha256(record.task_dir / "final.mp4"), initial_final_hash)
            replacement_state = json.loads(
                (record.task_dir / "shot_replacement_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(replacement_state["revision"], 1)
            self.assertEqual(replacement_state["sentence_id"], 0)
            self.assertTrue(replacement_state["reused_narration"])
            self.assertTrue(replacement_state["reused_subtitles"])

            provider_calls_before_remix = dict(_ProviderHandler.calls)
            replacement_final_hash = _sha256(record.task_dir / "final.mp4")
            replacement_log = (record.task_dir / "task.log").read_text(encoding="utf-8")
            rendered_segment_logs_after_replacement = replacement_log.count("渲染视频片段")
            self.assertGreater(rendered_segment_logs_after_replacement, rendered_segment_logs_before)

            remix_started = time.monotonic()
            remix_response = client.post(
                f"/api/tasks/{task_id}/remix",
                json={"keep_sentence_ids": [0, 2]},
                headers={"X-Task-Token": task_token},
            )
            self.assertEqual(remix_response.status_code, 202, remix_response.text)
            self.assertEqual(remix_response.json(), {"task_id": task_id, "revision": 2})
            remix_status = self._wait(client, task_id, task_token)
            remix_elapsed = time.monotonic() - remix_started
            self.assertEqual(remix_status["status"], "done", remix_status)
            self.assertEqual(remix_status["revision"], 2)
            self.assertLess(remix_elapsed, 30.0)
            self.assertTrue(all(stage["status"] == "done" for stage in remix_status["stages"]))
            self.assertGreater(remix_status["total_elapsed_seconds"], 0.0)
            self.assertTrue(all(stage["elapsed_seconds"] is None for stage in remix_status["stages"][:6]))
            self.assertTrue(all(stage["elapsed_seconds"] is not None for stage in remix_status["stages"][6:]))
            self.assertTrue(all("复用已有产物" in stage["message"] for stage in remix_status["stages"][:6]))
            self.assertIn("文本驱动重剪完成", remix_status["stages"][9]["message"])
            self.assertEqual(_ProviderHandler.calls, provider_calls_before_remix)
            self.assertEqual(
                {path.name: _sha256(path) for path in segment_paths},
                segment_hashes_before,
            )
            self.assertEqual(
                {path.name: _sha256(path) for path in sorted((record.task_dir / "tts").glob("sent_*.mp3"))},
                tts_hashes_before,
            )
            self.assertNotEqual(_sha256(record.task_dir / "final.mp4"), replacement_final_hash)

            remix_report = client.get(
                f"/api/tasks/{task_id}/report",
                headers={"X-Task-Token": task_token},
            ).json()
            self.assertEqual([row["sentence_id"] for row in remix_report["rows"]], [0, 2])
            self.assertEqual(
                remix_report["rows"][0]["replacement_instruction"],
                "换成蓝色背景的新闻活动全景镜头",
            )
            remix_timings = json.loads((record.task_dir / "timings.json").read_text(encoding="utf-8"))
            self.assertEqual([timing["sentence_id"] for timing in remix_timings], [0, 2])
            remix_narration_profile = json.loads(
                (record.task_dir / "narration_profile.json").read_text(encoding="utf-8")
            )
            self.assertTrue(remix_narration_profile["remixed"])
            self.assertEqual(
                [unit["sentence_id"] for unit in remix_narration_profile["units"]],
                [0, 2],
            )
            self.assertAlmostEqual(
                remix_timings[1]["start"],
                remix_timings[0]["end"] + remix_timings[0]["gap_after"],
                places=5,
            )
            source_timings = json.loads((record.task_dir / "source_timings.json").read_text(encoding="utf-8"))
            source_edl = json.loads((record.task_dir / "source_edl.json").read_text(encoding="utf-8"))
            remixed_edl = json.loads((record.task_dir / "edl.json").read_text(encoding="utf-8"))
            self.assertEqual(len(source_timings), 3)
            self.assertEqual(len(source_edl), 3)
            self.assertEqual([item["sentence_id"] for item in remixed_edl], [0, 2])
            remix_state = json.loads((record.task_dir / "remix_state.json").read_text(encoding="utf-8"))
            self.assertEqual(remix_state["revision"], 2)
            self.assertEqual(remix_state["keep_sentence_ids"], [0, 2])
            self.assertEqual(remix_state["deleted_sentence_ids"], [1])
            self.assertLess(remix_state["elapsed_seconds"], 30.0)
            self.assertTrue(remix_state["reused_tts_audio"])
            self.assertTrue(remix_state["reused_video_segments"])
            remixed_log = (record.task_dir / "task.log").read_text(encoding="utf-8")
            self.assertEqual(
                remixed_log.count("渲染视频片段"),
                rendered_segment_logs_after_replacement,
            )
            self.assertIn("M7 重剪完成 revision=2", remixed_log)

            unknown_remix = client.post(
                f"/api/tasks/{task_id}/remix",
                json={"keep_sentence_ids": [999]},
                headers={"X-Task-Token": task_token},
            )
            self.assertEqual(unknown_remix.status_code, 400)
            empty_remix = client.post(
                f"/api/tasks/{task_id}/remix",
                json={"keep_sentence_ids": []},
                headers={"X-Task-Token": task_token},
            )
            self.assertEqual(empty_remix.status_code, 422)
            self.__class__.completed_task_dir = record.task_dir

            cancel_id, cancel_token = self._submit(client, script)
            cancel_record = task_manager.get(cancel_id)
            self.assertIsNotNone(cancel_record)
            assert cancel_record is not None
            cancel_dir = cancel_record.task_dir
            cancelled = client.delete(
                f"/api/tasks/{cancel_id}",
                headers={"X-Task-Token": cancel_token},
            )
            self.assertEqual(cancelled.status_code, 204)
            self.assertIsNone(task_manager.get(cancel_id))
            self.assertFalse(cancel_dir.exists())
            self.assertEqual(client.get(f"/api/tasks/{cancel_id}").status_code, 404)

        print(f"M6 full-flow task: {self.completed_task_dir}")

    def _submit(self, client: TestClient, script: str) -> tuple[str, str]:
        with self.source_path.open("rb") as video:
            response = client.post(
                "/api/tasks",
                data={"script": script},
                files=[("files", (self.source_path.name, video, "video/mp4"))],
            )
        response.raise_for_status()
        payload = response.json()
        return payload["task_id"], payload["access_token"]

    @staticmethod
    def _wait(client: TestClient, task_id: str, task_token: str) -> dict[str, object]:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = client.get(
                f"/api/tasks/{task_id}",
                headers={"X-Task-Token": task_token},
            )
            response.raise_for_status()
            payload = response.json()
            if payload["status"] in {"done", "failed", "cancelled"}:
                return payload
            time.sleep(0.2)
        raise AssertionError("task timed out")


def _run_command(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()