"""Focused TEMP-only validation using the core runner's actual safety guards."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.run_core_validation import Guards, synthetic_environment


def main():
    sys.dont_write_bytecode = True
    root = Path(tempfile.mkdtemp(prefix="gm-v2-drafts-", dir=Path(os.environ["LOCALAPPDATA"]) / "Temp"))
    for name in ("tasks", "tmp", "cache", "evidence"):
        (root / name).mkdir()
    environment, ffmpeg = synthetic_environment(root)
    os.environ.clear()
    os.environ.update(environment)
    tempfile.tempdir = str(root / "tmp")
    with ExitStack() as stack:
        guard = Guards(root, root / "evidence", ffmpeg)
        guard.install(stack)
        guard.self_check()
        suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_v2_drafts")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        clean = result.wasSuccessful() and not any(k.startswith("suite:denied:") for k in guard.counts)
        print(f"V2 tests={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} TEMP={root}")
        return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())