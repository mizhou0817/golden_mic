"""Relative DATA_DIR must never produce relative task directories (regression)."""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.config import Settings


class DataPathTests(unittest.TestCase):
    def test_relative_data_dir_becomes_absolute_for_task_paths(self):
        # With DATA_DIR=data/tasks a task dir was relative while uploads were
        # absolute, so path.relative_to(task_dir) failed at the ASR stage.
        settings = Settings(_env_file=None, data_dir="data/tasks")
        self.assertTrue(settings.data_dir.is_absolute())
        self.assertTrue(settings.effective_asr_cache_dir.is_absolute())
        upload = settings.data_dir / "task" / "raw" / "clip.mp4"
        self.assertEqual(upload.relative_to(settings.data_dir / "task").as_posix(), "raw/clip.mp4")

    def test_explicit_relative_asr_cache_also_becomes_absolute(self):
        settings = Settings(_env_file=None, data_dir="custom/tasks", asr_cache_dir="custom/asr-cache")
        self.assertEqual(settings.effective_asr_cache_dir, Path("custom/asr-cache").absolute())

    def test_production_still_rejects_relative_directories(self):
        with self.assertRaises(ValueError):
            Settings(_env_file=None, app_env="production", enable_api_docs=False, enforce_origin_check=True,
                     task_ttl_hours=72, min_free_disk_gb=50, task_disk_reservation_multiplier=8, data_dir="data/tasks")


if __name__ == "__main__":
    unittest.main()
