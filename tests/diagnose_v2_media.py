"""One guarded synthetic ambient-clock probe; numeric diagnostics, no providers."""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from unittest.mock import patch


def main():
    project = Path(__file__).resolve().parents[1]
    os.chdir(project)
    sys.path.insert(0, str(project))
    sys.dont_write_bytecode = True
    from tests.run_core_validation import synthetic_environment
    from tests.run_v2_validation import SafeResult, Sink, install_v2_guards, snapshot
    from tests.validation_environment import temporary_base

    root = Path(tempfile.mkdtemp(prefix="gm-media-clock-", dir=temporary_base(os.environ)))
    for name in ("tmp", "tasks", "cache/asr", "cache/hf", "cache/matplotlib", "cache/numba", "evidence"):
        (root / name).mkdir(parents=True, exist_ok=True)
    environment, ffmpeg = synthetic_environment(root)
    os.environ.clear()
    os.environ.update(environment)
    tempfile.tempdir = str(root / "tmp")
    observations = {}
    before = snapshot()
    with ExitStack() as stack:
        stack.enter_context(redirect_stdout(Sink()))
        stack.enter_context(redirect_stderr(Sink()))
        null = stack.enter_context(open(os.devnull, "w"))
        for fd in (1, 2):
            saved = os.dup(fd)
            def restore(fd=fd, saved=saved):
                os.dup2(saved, fd)
                os.close(saved)
            stack.callback(restore)
            os.dup2(null.fileno(), fd)
        guard = install_v2_guards(stack, root, root / "evidence", ffmpeg, lambda: None)
        from backend import mode_pipeline
        from tests.test_v2_media import PipelineFocusedTests
        original = mode_pipeline._ambient_narration

        async def measured(task_root, *args, **kwargs):
            result = await original(task_root, *args, **kwargs)
            for label, file in (("raw", task_root / "raw.wav"), ("narration", task_root / "narration.m4a"),
                                ("derivative", result)):
                if file is None:
                    continue
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                                        "-of", "json", str(file)], capture_output=True, check=True, timeout=30)
                data = json.loads(probe.stdout)
                stream = next(value for value in data["streams"] if value["codec_type"] == "audio")
                values = {"format_seconds": data["format"]["duration"], "stream_seconds": stream.get("duration"),
                          "sample_rate": stream["sample_rate"], "channels": stream["channels"]}
                observations[label] = {key: float(value) for key, value in values.items()
                                       if value is not None and math.isfinite(float(value))}
                decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(file), "-map", "0:a:0",
                                          "-ar", "48000", "-ac", "1", "-f", "s16le", "pipe:1"],
                                         capture_output=True, check=True, timeout=30)
                observations[label]["decoded_samples_48k_mono"] = len(decoded.stdout) // 2
            return result

        stack.enter_context(patch.object(mode_pipeline, "_ambient_narration", measured))
        result = SafeResult(before)
        unittest.TestSuite([PipelineFocusedTests("test_real_ambient_derivative_preserves_narration_and_clock")]).run(result)
        drift = before != snapshot()
        denied = {key: value for key, value in guard.counts.items() if key.startswith("suite:denied:")}
        report = {"diagnostic": "synthetic_ambient_clock", "observations": observations,
                  "tests": result.testsRun, "passed": result.passed, "failures": len(result.failures),
                  "errors": len(result.errors), "skipped": len(result.skipped), "source_drift": drift,
                  "denials": denied, "listeners": len(guard.ports), "failure_source": result.events}
        clean = result.wasSuccessful() and not result.skipped and not drift and not denied and not guard.ports
    print(json.dumps(report, ensure_ascii=True))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())