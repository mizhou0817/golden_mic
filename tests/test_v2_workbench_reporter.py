"""Edit progress may come from the edit job's parallel sub-tasks; only a real takeover is rejected."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend import workbench as wb
from backend.revisions import RevisionError


class ReporterOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gm-reporter-")
        self.addCleanup(temp.cleanup)
        self.record = SimpleNamespace(task_id="a" * 32, background=asyncio.current_task())
        self.manager = SimpleNamespace(get=lambda task_id: self.record, _persist_record=lambda record: None)
        self.work = SimpleNamespace(task_dir=Path(temp.name), task_id=self.record.task_id, mode="voiceover", stages=[],
                                    current_stage=0, stage_name="", progress=0, message="", processing_started_at=None)
        wb._reset_mode_progress(self.work, 6)
        self.reporter = wb._WorkbenchReporter(self.record, self.work, self.manager, 6)
        self.reporter.start_stage(self.work, 6, "match")

    async def test_progress_from_parallel_sub_tasks_of_the_owning_job_is_accepted(self):
        async def embed_one(index):
            self.reporter.update_stage(self.work, 6, index / 4, f"video {index}/4")
        await asyncio.gather(*(embed_one(index) for index in range(1, 5)))
        self.assertEqual(self.work.stages[5].fraction, 1.0)

    async def test_a_real_takeover_is_still_rejected(self):
        async def other_job():
            return asyncio.current_task()
        self.record.background = await asyncio.create_task(other_job())
        with self.assertRaises(RevisionError):
            self.reporter.update_stage(self.work, 6, 0.5, "late progress")


if __name__ == "__main__":
    unittest.main()
