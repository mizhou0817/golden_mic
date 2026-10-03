import io
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.datastructures import Headers

from backend.config import Settings
from backend.main import app, rate_counter, settings as app_settings, task_manager
from backend.main import (
    _client_ip,
    _enforce_rate_limit,
    _enforce_rate_limit_identities,
    _enforce_rate_limit_rules,
    _is_invalid_request_path,
)
from backend.models import (
    AnnotatedShot,
    MatchCandidate,
    MatchPlanItem,
    SentenceTiming,
    StageState,
    TaskState,
    VisionQuality,
)
from backend.reporting import ReportGenerationError, generate_report
from backend.operations import DiskCapacitySnapshot, InsufficientDiskSpaceError, UploadCapacityGuard
from backend.readiness import (
    FONT_LICENSE,
    ReadinessReport,
    find_bundled_noto_sans_sc_fonts,
    validate_no_legacy_environment,
)
from backend.task_manager import TaskManager, TaskRecord
from backend.storage import ALLOWED_EXTENSIONS, sanitize_sensitive_text, save_uploads


class ReportGenerationTest(unittest.TestCase):
    def test_generates_complete_atomic_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "final.mp4").write_bytes(b"mp4")
            thumbs_dir = task_dir / "thumbs"
            thumbs_dir.mkdir()
            (thumbs_dir / "shot_3.jpg").write_bytes(b"jpg")
            shot = _shot(3)
            match = MatchPlanItem(
                sentence_id=0,
                text="本市发布新的产业政策。",
                shot_id=3,
                confidence=0.42,
                alternates=[],
                is_fallback=True,
                candidates=[MatchCandidate(shot_id=3, similarity=0.7)],
                overlay_kind="organization",
                overlay_text="本市有关部门",
            )
            timing = SentenceTiming(
                sentence_id=0,
                text=match.text,
                audio_path="tts/sent_0.mp3",
                duration=1.25,
                start=0.0,
                end=1.25,
            )

            report = generate_report(task_dir, "task-123", [shot], [match], [timing])
            persisted = json.loads((task_dir / "report.json").read_text(encoding="utf-8"))

            self.assertEqual(len(report.rows), 1)
            self.assertEqual(persisted["task_id"], "task-123")
            self.assertEqual(persisted["rows"][0]["shot_id"], 3)
            self.assertEqual(persisted["rows"][0]["duration"], 1.25)
            self.assertEqual(persisted["rows"][0]["confidence"], 0.42)
            self.assertTrue(persisted["rows"][0]["is_fallback"])
            self.assertEqual(persisted["rows"][0]["audio_kind"], "tts")
            self.assertIsNone(persisted["rows"][0]["spoken_text"])
            self.assertEqual(persisted["rows"][0]["thumb_url"], "/api/tasks/task-123/thumbs/3.jpg")
            self.assertEqual(persisted["rows"][0]["overlay_kind"], "organization")
            self.assertEqual(persisted["rows"][0]["overlay_text"], "本市有关部门")
            self.assertFalse((task_dir / "report.json.tmp").exists())

    def test_rejects_missing_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "final.mp4").write_bytes(b"mp4")
            match = MatchPlanItem(
                sentence_id=0,
                text="新闻句子。",
                shot_id=0,
                confidence=0.9,
                candidates=[MatchCandidate(shot_id=0, similarity=1.0)],
            )
            timing = SentenceTiming(
                sentence_id=0,
                text=match.text,
                audio_path="tts/sent_0.mp3",
                duration=1.0,
                start=0.0,
                end=1.0,
            )
            with self.assertRaisesRegex(ReportGenerationError, "缩略图不存在"):
                generate_report(task_dir, "task", [_shot(0)], [match], [timing])


class TaskCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_removes_expired_memory_and_orphan_tasks_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "tasks"
            settings = Settings(data_dir=data_dir, task_ttl_hours=2)
            manager = TaskManager(settings)
            now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

            expired_dir = _make_task_dir(data_dir, "expired-memory")
            fresh_dir = _make_task_dir(data_dir, "fresh-memory")
            orphan_expired_dir = _make_task_dir(data_dir, "expired-orphan")
            orphan_fresh_dir = _make_task_dir(data_dir, "fresh-orphan")
            active_expired_dir = _make_task_dir(data_dir, "active-expired")
            active_orphan_dir = _make_task_dir(data_dir, "active-orphan")
            _write_manifest(orphan_expired_dir, now - timedelta(hours=3))
            _write_manifest(orphan_fresh_dir, now - timedelta(minutes=30))
            active_orphan = TaskRecord(
                task_id="active-orphan",
                task_dir=active_orphan_dir,
                script="active orphan",
                uploads=[],
                status=TaskState.running,
                created_at=now - timedelta(hours=5),
                updated_at=now - timedelta(hours=5),
            )
            manager._persist_record(active_orphan)

            manager._tasks["expired-memory"] = TaskRecord(
                task_id="expired-memory",
                task_dir=expired_dir,
                script="expired",
                uploads=[],
                status=TaskState.done,
                created_at=now - timedelta(hours=3),
                updated_at=now - timedelta(hours=3),
            )
            manager._tasks["fresh-memory"] = TaskRecord(
                task_id="fresh-memory",
                task_dir=fresh_dir,
                script="fresh",
                uploads=[],
                status=TaskState.done,
                created_at=now - timedelta(minutes=30),
                updated_at=now - timedelta(minutes=30),
            )
            manager._tasks["active-expired"] = TaskRecord(
                task_id="active-expired",
                task_dir=active_expired_dir,
                script="active",
                uploads=[],
                status=TaskState.running,
                created_at=now - timedelta(hours=4),
                updated_at=now - timedelta(hours=4),
            )

            removed = await manager.cleanup_expired(now)

            self.assertEqual(set(removed), {"expired-memory", "expired-orphan"})
            self.assertIsNone(manager.get("expired-memory"))
            self.assertIsNotNone(manager.get("fresh-memory"))
            self.assertFalse(expired_dir.exists())
            self.assertFalse(orphan_expired_dir.exists())
            self.assertTrue(fresh_dir.exists())
            self.assertTrue(orphan_fresh_dir.exists())
            self.assertTrue(active_expired_dir.exists())
            self.assertTrue(active_orphan_dir.exists())

    async def test_zero_ttl_keeps_history_until_manual_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "tasks"
            manager = TaskManager(Settings(data_dir=data_dir, task_ttl_hours=0))
            task_dir = _make_task_dir(data_dir, "persistent-history")
            record = TaskRecord(
                task_id="persistent-history",
                task_dir=task_dir,
                script="历史新闻稿",
                uploads=[],
                created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
            manager._tasks[record.task_id] = record

            removed = await manager.cleanup_expired(datetime(2030, 1, 1, tzinfo=timezone.utc))

            self.assertEqual(removed, [])
            self.assertTrue(task_dir.exists())


class TaskHistoryPersistenceTest(unittest.TestCase):
    def test_restores_completed_task_using_token_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "tasks"
            task_dir = _make_task_dir(data_dir, "history-task")
            (task_dir / "final.mp4").write_bytes(b"video")
            (task_dir / "report.json").write_text(
                json.dumps({"task_id": "history-task", "rows": []}),
                encoding="utf-8",
            )
            source_manager = TaskManager(Settings(data_dir=data_dir))
            record = TaskRecord(
                task_id="history-task",
                task_dir=task_dir,
                script="这是用于恢复的新闻稿。",
                uploads=[],
                status=TaskState.done,
                progress=100,
                message="处理完成",
            )
            token = record.access_token
            source_manager._tasks[record.task_id] = record
            source_manager._persist_record(record)
            state_text = (task_dir / "task_state.json").read_text(encoding="utf-8")

            restored_manager = TaskManager(Settings(data_dir=data_dir))
            restored_count = restored_manager.restore_tasks()
            restored = restored_manager.authorize(record.task_id, token)

            self.assertEqual(restored_count, 1)
            self.assertNotIn(token, state_text)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.status, TaskState.done)
            self.assertEqual(restored.script, record.script)
            self.assertEqual(restored.access_token, "")


class TaskShutdownTest(unittest.IsolatedAsyncioTestCase):
    async def test_immediate_cancel_releases_retained_disk_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "tasks"
            settings = Settings(data_dir=data_dir)
            guard = UploadCapacityGuard(settings)
            manager = TaskManager(settings, guard)
            task_dir = _make_task_dir(data_dir, "cancel-before-start")
            reservation_bytes = 12345
            await guard._lock.acquire()
            guard._reserved_bytes = reservation_bytes
            guard._lock.release()

            record = manager.add_task(
                "cancel-before-start",
                task_dir,
                "立即取消测试。",
                [],
                reserved_disk_bytes=reservation_bytes,
            )
            self.assertTrue(await manager.cancel_and_delete(record.task_id))

            self.assertEqual((await guard.snapshot()).reserved_bytes, 0)
            self.assertEqual(record.reserved_disk_bytes, 0)
            self.assertFalse(task_dir.exists())

    async def test_shutdown_waits_then_cancels_remaining_backgrounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = TaskManager(
                Settings(
                    data_dir=Path(directory),
                    shutdown_grace_seconds=0.01,
                )
            )
            record = TaskRecord(
                task_id="shutdown-task",
                task_dir=Path(directory),
                script="shutdown",
                uploads=[],
            )
            record.background = asyncio.create_task(asyncio.sleep(30))
            manager._tasks[record.task_id] = record

            await manager.shutdown()

            self.assertTrue(manager.is_draining)
            self.assertTrue(record.background.cancelled())


class TaskTimingTest(unittest.TestCase):
    def test_tracks_live_completed_and_restored_timings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "tasks"
            task_dir = _make_task_dir(data_dir, "timed-task")
            (task_dir / "final.mp4").write_bytes(b"video")
            (task_dir / "report.json").write_text(
                json.dumps({"task_id": "timed-task", "rows": []}),
                encoding="utf-8",
            )
            manager = TaskManager(Settings(data_dir=data_dir))
            record = TaskRecord(
                task_id="timed-task",
                task_dir=task_dir,
                script="计时测试新闻稿。",
                uploads=[],
            )
            manager._tasks[record.task_id] = record

            manager._begin_processing(record)
            manager.start_stage(record, 1, "正在计时")
            assert record.processing_started_at is not None
            assert record.stages[0].started_at is not None
            record.processing_started_at -= timedelta(seconds=5)
            record.stages[0].started_at -= timedelta(seconds=2)
            record.processing_started_monotonic = None
            record.stage_started_monotonic = None

            live = manager.status_response(record)

            self.assertEqual(live.stages[0].status, StageState.running)
            self.assertIsNotNone(live.total_elapsed_seconds)
            self.assertIsNotNone(live.stages[0].elapsed_seconds)
            assert live.total_elapsed_seconds is not None
            assert live.stages[0].elapsed_seconds is not None
            self.assertGreaterEqual(live.total_elapsed_seconds, 5.0)
            self.assertGreaterEqual(live.stages[0].elapsed_seconds, 2.0)

            manager.complete_stage(record, 1, "计时完成")
            manager.start_stage(record, 2, "下一阶段处理中")
            after_transition = manager.status_response(record)

            self.assertEqual(after_transition.stages[0].status, StageState.done)
            self.assertIsNotNone(after_transition.stages[0].completed_at)
            self.assertGreaterEqual(after_transition.stages[0].elapsed_seconds or 0.0, 2.0)
            self.assertEqual(after_transition.stages[1].status, StageState.running)
            self.assertIsNotNone(after_transition.stages[1].elapsed_seconds)

            manager.complete_stage(record, 2, "下一阶段完成")
            manager._finish_processing(record)
            record.status = TaskState.done
            record.progress = 100
            manager._persist_record(record)
            completed = manager.status_response(record)

            self.assertIsNotNone(completed.processing_started_at)
            self.assertIsNotNone(completed.processing_completed_at)
            self.assertGreaterEqual(completed.total_elapsed_seconds or 0.0, 5.0)
            self.assertGreaterEqual(completed.stages[0].elapsed_seconds or 0.0, 2.0)
            self.assertIsNotNone(completed.stages[0].completed_at)

            restored_manager = TaskManager(Settings(data_dir=data_dir))
            self.assertEqual(restored_manager.restore_tasks(), 1)
            restored = restored_manager.get(record.task_id)
            self.assertIsNotNone(restored)
            assert restored is not None
            restored_status = restored_manager.status_response(restored)
            self.assertEqual(restored_status.total_elapsed_seconds, completed.total_elapsed_seconds)
            self.assertEqual(
                restored_status.stages[0].elapsed_seconds,
                completed.stages[0].elapsed_seconds,
            )


class RateLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        rate_counter.clear()

    def tearDown(self) -> None:
        rate_counter.clear()

    def test_sixth_task_in_one_hour_is_rejected(self) -> None:
        for _ in range(5):
            _enforce_rate_limit("203.0.113.10")
        with self.assertRaises(HTTPException) as context:
            _enforce_rate_limit("203.0.113.10")
        self.assertEqual(context.exception.status_code, 429)
        self.assertIn("每小时最多启动 5 次任务（含重试）", str(context.exception.detail))

    def test_expired_client_identities_do_not_exhaust_global_table(self) -> None:
        expired = datetime.now(timezone.utc) - timedelta(hours=2)
        for index in range(10_000):
            rate_counter[f"expired-{index}"].append(expired)

        _enforce_rate_limit("current-client")

        self.assertEqual(set(rate_counter), {"current-client"})

    def test_same_browser_session_cannot_bypass_quota_by_changing_ip(self) -> None:
        for index in range(5):
            _enforce_rate_limit_identities(
                ["anonymous-session=browser", f"anonymous-ip=203.0.113.{index}"]
            )
        with self.assertRaises(HTTPException) as context:
            _enforce_rate_limit_identities(
                ["anonymous-session=browser", "anonymous-ip=198.51.100.10"]
            )
        self.assertEqual(context.exception.status_code, 429)

    def test_anonymous_session_and_ip_can_have_distinct_quotas(self) -> None:
        for _ in range(2):
            _enforce_rate_limit_rules(
                [
                    ("anonymous-session=session-a", 2, "当前浏览器会话"),
                    ("anonymous-ip=203.0.113.10", 5, "当前网络出口"),
                ]
            )
        with self.assertRaises(HTTPException) as context:
            _enforce_rate_limit_rules(
                [
                    ("anonymous-session=session-a", 2, "当前浏览器会话"),
                    ("anonymous-ip=203.0.113.10", 5, "当前网络出口"),
                ]
            )
        self.assertEqual(context.exception.status_code, 429)
        self.assertIn("当前浏览器会话每小时最多启动 2 次任务（含重试）", str(context.exception.detail))


class SecurityUtilityTest(unittest.TestCase):
    def test_redacts_tokens_and_signed_urls(self) -> None:
        message = (
            "Authorization=Bearer secret-token "
            "access_token=my-access-token "
            "https://example.com/file?auth_key=signed-value&x=1"
        )
        sanitized = sanitize_sensitive_text(message)
        self.assertNotIn("secret-token", sanitized)
        self.assertNotIn("my-access-token", sanitized)
        self.assertNotIn("signed-value", sanitized)

    def test_task_access_token_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = TaskManager(Settings(data_dir=Path(directory)))
            record = TaskRecord(
                task_id="task-security",
                task_dir=Path(directory),
                script="test",
                uploads=[],
            )
            manager._tasks[record.task_id] = record
            self.assertIsNone(manager.authorize(record.task_id, None))
            self.assertIsNone(manager.authorize(record.task_id, "wrong"))
            self.assertIs(manager.authorize(record.task_id, record.access_token), record)

    def test_production_client_ip_uses_proxy_overwritten_forwarded_address(self) -> None:
        previous_environment = app_settings.app_env
        app_settings.app_env = "production"
        try:
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/health/live",
                    "headers": [(b"x-forwarded-for", b"203.0.113.25")],
                    "client": ("127.0.0.1", 50000),
                }
            )
            self.assertEqual(_client_ip(request), "203.0.113.25")
            invalid_request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/health/live",
                    "headers": [(b"x-forwarded-for", b"spoofed, 203.0.113.25")],
                    "client": ("127.0.0.1", 50000),
                }
            )
            self.assertEqual(_client_ip(invalid_request), "invalid-forwarded-client")
        finally:
            app_settings.app_env = previous_environment

    def test_rejects_malformed_windows_and_jndi_paths(self) -> None:
        self.assertTrue(_is_invalid_request_path("/:undefined"))
        self.assertTrue(_is_invalid_request_path("/api/v2/${jndi:dns:\\scanner}"))
        self.assertFalse(_is_invalid_request_path("/assets/index-safe.js"))

        with TestClient(app) as client:
            responses = [
                client.get("/:undefined"),
                client.get("/api/v2/$%7Bjndi:dns:%5C%5Cscanner%7D"),
                client.get("/JSPWiki/wiki/$%7Bjndi:dns:%5C%5Cscanner%7D"),
            ]

        self.assertTrue(all(response.status_code == 404 for response in responses))
        self.assertTrue(all(response.headers["x-content-type-options"] == "nosniff" for response in responses))


class UploadCountLimitTest(unittest.IsolatedAsyncioTestCase):
    def test_configuration_caps_file_count_at_100(self) -> None:
        self.assertEqual(Settings(max_files=100).max_files, 100)
        with self.assertRaises(ValidationError):
            Settings(max_files=101)

    async def test_accepts_100_files_and_rejects_101(self) -> None:
        settings = Settings(max_files=100)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            accepted_dir = root / "accepted"
            (accepted_dir / "raw").mkdir(parents=True)
            accepted_uploads = [_video_upload(index) for index in range(100)]
            try:
                saved = await save_uploads(accepted_dir, accepted_uploads, settings)
            finally:
                for upload in accepted_uploads:
                    await upload.close()
            self.assertEqual(len(saved), 100)

            rejected_dir = root / "rejected"
            (rejected_dir / "raw").mkdir(parents=True)
            rejected_uploads = [_video_upload(index) for index in range(101)]
            try:
                with self.assertRaises(HTTPException) as context:
                    await save_uploads(rejected_dir, rejected_uploads, settings)
            finally:
                for upload in rejected_uploads:
                    await upload.close()
            self.assertEqual(context.exception.status_code, 400)
            self.assertIn("100", str(context.exception.detail))

    async def test_rejects_declared_total_upload_size_before_copying(self) -> None:
        settings = Settings(max_upload_mb=1, max_total_upload_mb=1)
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "raw").mkdir()
            uploads = [
                UploadFile(
                    file=io.BytesIO(b"video"),
                    size=600_000,
                    filename=f"source-{index}.mp4",
                    headers=Headers({"content-type": "video/mp4"}),
                )
                for index in range(2)
            ]
            try:
                with self.assertRaises(HTTPException) as context:
                    await save_uploads(task_dir, uploads, settings)
            finally:
                for upload in uploads:
                    await upload.close()
            self.assertEqual(context.exception.status_code, 413)
            self.assertIn("合计", str(context.exception.detail))
            self.assertEqual(list((task_dir / "raw").iterdir()), [])

    def test_configuration_rejects_inconsistent_aggregate_limits(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(max_upload_mb=500, max_total_upload_mb=499)
        with self.assertRaises(ValidationError):
            Settings(
                max_source_duration_seconds_per_file=1800,
                max_total_source_duration_seconds=1799,
            )


class ProductionGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_reservation_preserves_minimum_free_space(self) -> None:
        settings = Settings(
            min_free_disk_gb=1,
            max_concurrent_uploads=1,
            task_disk_reservation_multiplier=1,
        )
        guard = UploadCapacityGuard(settings)
        capacity = DiskCapacitySnapshot(
            path=Path.cwd(),
            total_bytes=10 * 1024**3,
            used_bytes=8 * 1024**3,
            free_bytes=2 * 1024**3,
            reserved_bytes=0,
            minimum_free_bytes=1 * 1024**3,
        )
        def snapshot_with_reservation(_settings: Settings, reserved_bytes: int = 0) -> DiskCapacitySnapshot:
            return DiskCapacitySnapshot(
                path=capacity.path,
                total_bytes=capacity.total_bytes,
                used_bytes=capacity.used_bytes,
                free_bytes=capacity.free_bytes,
                reserved_bytes=reserved_bytes,
                minimum_free_bytes=capacity.minimum_free_bytes,
            )

        with patch("backend.operations.disk_capacity_snapshot", side_effect=snapshot_with_reservation):
            async with guard.reserve(512 * 1024**2):
                pass
            with self.assertRaises(InsufficientDiskSpaceError):
                async with guard.reserve(1536 * 1024**2):
                    pass

            async with guard.reserve(512 * 1024**2) as reservation:
                reservation.retain()
            retained = await guard.snapshot()
            self.assertEqual(retained.reserved_bytes, 512 * 1024**2)
            await reservation.release()
            released = await guard.snapshot()
            self.assertEqual(released.reserved_bytes, 0)

    def test_production_settings_reject_unsafe_defaults(self) -> None:
        root = Path(Path.cwd().anchor) / "golden-mic-production-test"
        safe_values = {
            "_env_file": None,
            "app_env": "production",
            "enable_api_docs": False,
            "enforce_origin_check": True,
            "task_ttl_hours": 72,
            "quality_gate_mode": "block",
            "data_dir": root / "tasks",
            "asr_cache_dir": root / "cache" / "asr",
            "min_free_disk_gb": 50,
            "max_files": 20,
            "max_concurrent_uploads": 1,
            "max_concurrent_tasks": 1,
            "max_pending_tasks": 5,
            "shutdown_grace_seconds": 6900,
            "media_command_timeout_seconds": 6800,
            "allowed_hosts": "news.example.com,127.0.0.1",
            "frontend_origins": "https://news.example.com",
            "vision_provider": "volcengine",
            "llm_provider": "volcengine",
            "tts_provider": "edge",
            "sync_sound_enabled": False,
            "volcengine_vision_api_keys": "unit-test-secret",
            "volcengine_llm_api_keys": "unit-test-secret",
            "anonymous_session_secret": "unit-test-session-secret-with-at-least-32-bytes",
        }
        production = Settings(**safe_values)
        self.assertTrue(production.is_production)
        with self.assertRaises(ValidationError):
            Settings(**{**safe_values, "anonymous_session_secret": ""})
        anonymous_production = Settings(
            **{
                **safe_values,
                "anonymous_session_task_rate_limit_per_hour": 2,
                "anonymous_ip_task_rate_limit_per_hour": 5,
                "anonymous_global_task_rate_limit_per_hour": 10,
            }
        )
        self.assertTrue(anonymous_production.is_production)
        self.assertNotIn("public_access_mode", Settings.model_fields)
        with self.assertRaises(ValidationError):
            Settings(**{**safe_values, "enable_api_docs": True})
        with self.assertRaises(ValidationError):
            Settings(**{**safe_values, "frontend_origins": "http://news.example.com"})
        with self.assertRaises(ValidationError):
            Settings(**{**safe_values, "allowed_hosts": "*"})
        with self.assertRaises(ValidationError):
            Settings(
                **{
                    **safe_values,
                    "volcengine_vision_base_url": "http://ark.example.com/api/v3",
                }
            )
        with self.assertRaises(ValidationError):
            Settings(**{**safe_values, "volcengine_vision_api_keys": "CHANGE_ME"})
        with self.assertRaises(ValidationError):
            Settings(
                **{
                    **safe_values,
                    "vision_provider": "kimi",
                    "llm_provider": "kimi",
                    "kimi_api_key": "",
                }
            )
        with self.assertRaises(ValidationError):
            Settings(**{**safe_values, "max_files": 21})
        with self.assertRaises(ValidationError):
            Settings(**{**safe_values, "max_concurrent_tasks": 2})

    def test_bundled_font_and_license_are_present(self) -> None:
        fonts = find_bundled_noto_sans_sc_fonts()
        self.assertTrue(fonts)
        self.assertTrue(all(path.stat().st_size > 100_000 for path in fonts))
        self.assertIn("SIL OPEN FONT LICENSE", FONT_LICENSE.read_text(encoding="utf-8"))

    def test_production_preflight_rejects_removed_mediakit_environment(self) -> None:
        with patch.dict("os.environ", {"VIDEO_PROCESSING_PROVIDER": "removed"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "MediaKit"):
                validate_no_legacy_environment()

    def test_health_and_drain_endpoints(self) -> None:
        try:
            with TestClient(app) as client:
                app.state.startup_readiness = ReadinessReport(
                    ready=True,
                    checks={"font_asset": True},
                    errors=(),
                )
                live = client.get("/health/live")
                ready = client.get("/health/ready")
                drain = client.post("/api/admin/drain")
                rejected = client.post("/api/tasks")
                resumed = client.delete("/api/admin/drain")
        finally:
            task_manager.start_accepting()

        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 200, ready.text)
        self.assertTrue(ready.json()["checks"]["font_asset"])
        self.assertEqual(drain.status_code, 200)
        self.assertTrue(drain.json()["draining"])
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(resumed.status_code, 200)
        self.assertFalse(resumed.json()["draining"])

    def test_origin_check_rejects_missing_origin_before_body_parsing(self) -> None:
        previous = task_manager.settings.enforce_origin_check
        task_manager.settings.enforce_origin_check = True
        try:
            with TestClient(app) as client:
                response = client.post("/api/tasks")
        finally:
            task_manager.settings.enforce_origin_check = previous
        self.assertEqual(response.status_code, 403)

    def test_production_api_never_trusts_a_proxy_user_header(self) -> None:
        previous_environment = app_settings.app_env
        with TestClient(app) as client:
            app_settings.app_env = "production"
            try:
                unauthenticated = client.get("/api/tasks/not-found")
                authenticated = client.get(
                    "/api/tasks/not-found",
                    headers={"X-Authenticated-User": "operator"},
                )
            finally:
                app_settings.app_env = previous_environment
        self.assertEqual(unauthenticated.status_code, 404)
        self.assertEqual(authenticated.status_code, 404)


class OperationalEndpointTest(unittest.TestCase):
    def test_public_limits_reflect_runtime_settings(self) -> None:
        # Assert the exact public allowlist, including the fixed mode contract
        # and runtime settings below/above the browser admission caps.
        for files, upload_mb, total_mb, concurrent, pending, shots in (
            (3, 20, 100, 1, 2, 42), (100, 700, 7000, 4, 20, 180),
        ):
            with self.subTest(max_files=files), patch.multiple(
                app_settings, max_files=files, max_upload_mb=upload_mb,
                max_total_upload_mb=total_mb, max_concurrent_tasks=concurrent,
                max_pending_tasks=pending, max_shots=shots,
            ), TestClient(app) as client:
                response = client.get("/api/config/limits")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {
                "max_files": min(files, 20),
                "max_upload_bytes": min(upload_mb, 500) * 1024**2,
                "max_total_upload_bytes": min(total_mb, 5120) * 1024**2,
                "max_script_length": 8000,
                "max_video_duration_seconds": app_settings.max_source_duration_seconds_per_file,
                "max_total_video_duration_seconds": app_settings.max_total_source_duration_seconds,
                "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
                "quote_max_sec": 30.0, "quote_warn_sec": 20.0, "quote_min_sec": 1.0,
                "match_ok": 0.85, "match_low": 0.6, "script_soft_max": 3000,
                "rate_tolerance": 0.08, "pacing_cpm": {"slow": 230, "normal": 265, "fast": 290},
                "chunk_size": 8 * 1024**2, "max_shots": shots, "max_visual_sec": 6.5,
                "retention_hours": 72, "browser_submissions_per_hour": 2,
                "ip_submissions_per_hour": 5, "global_submissions_per_hour": 10,
                "max_concurrent_tasks": min(concurrent, 1), "max_pending_tasks": min(pending, 5),
            })

    def test_negative_thumbnail_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_id = "negative-shot-test"
            task_manager._tasks[task_id] = TaskRecord(
                task_id=task_id,
                task_dir=Path(directory),
                script="test",
                uploads=[],
            )
            try:
                with TestClient(app) as client:
                    token = task_manager._tasks[task_id].access_token
                    response = client.get(f"/api/tasks/{task_id}/thumbs/-1.jpg?token={token}")
            finally:
                task_manager._tasks.pop(task_id, None)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "shot_id 必须大于或等于 0。")


def _shot(shot_id: int) -> AnnotatedShot:
    return AnnotatedShot(
        shot_id=shot_id,
        source_index=0,
        source_scene_index=0,
        source_name="input.mp4",
        norm_path="norm/norm_0.mp4",
        start=0.0,
        end=2.0,
        duration=2.0,
        thumb_path=f"thumbs/shot_{shot_id}.jpg",
        status="available",
        description="市民在新闻发布厅听取介绍",
        scene_type="indoor",
        subjects=["市民"],
        actions=["听取介绍"],
        keywords=["新闻", "发布厅", "市民"],
        quality=VisionQuality(sharp=0.9, bright=0.8),
    )


def _video_upload(index: int) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(b"video"),
        filename=f"source-{index}.mp4",
        headers=Headers({"content-type": "video/mp4"}),
    )


def _make_task_dir(data_dir: Path, task_id: str) -> Path:
    task_dir = data_dir / task_id
    (task_dir / "raw").mkdir(parents=True)
    return task_dir


def _write_manifest(task_dir: Path, created_at: datetime) -> None:
    (task_dir / "upload_manifest.json").write_text(
        json.dumps({"task_id": task_dir.name, "created_at": created_at.isoformat()}),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()