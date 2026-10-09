"""只用原声 with a very short or quiet quote must finish: unknown loudness is reported, not fatal."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from backend import tts_pipeline
from backend.models import SentenceTiming


class ShortAudioUnitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gm-short-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def tone(self, name: str, seconds: float, volume: str) -> SentenceTiming:
        path = self.root / "tts" / name
        path.parent.mkdir(exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=440:sample_rate=48000:duration={seconds}", "-af", f"volume={volume}",
                        "-ac", "2", "-c:a", "pcm_s16le", str(path)], check=True)
        return SentenceTiming(sentence_id=0, text="哈哈哈", audio_path=f"tts/{name}", duration=seconds,
                              start=0.0, end=seconds, audio_kind="sync")

    async def test_a_too_short_quiet_quote_is_kept_at_its_real_level_instead_of_stopping_the_work(self):
        timing = self.tone("short.wav", 0.2, "-70dB")  # far below loudnorm's 0.4 s measurement window
        result = await tts_pipeline.normalize_mode_unit(self.root, timing, self.root / "tts" / "short-norm.wav")
        self.assertTrue((self.root / "tts" / "short-norm.wav").is_file())
        self.assertAlmostEqual(result.duration, 0.2, places=3, msg="no sample added or removed")
        self.assertIsNone(result.integrated_lufs, "unknown loudness is recorded as unknown, never as compliant")
        self.assertIn("无法测量响度", (self.root / "task.log").read_text(encoding="utf-8"))

    async def test_a_normal_quote_is_still_normalised_to_the_target(self):
        timing = self.tone("normal.wav", 3.0, "-30dB")
        result = await tts_pipeline.normalize_mode_unit(self.root, timing, self.root / "tts" / "normal-norm.wav")
        self.assertIsNotNone(result.integrated_lufs)
        self.assertLessEqual(abs(result.integrated_lufs + 20.0), 2.0)


class CheckMessageNumberTests(unittest.TestCase):
    def test_check_messages_show_readable_seconds(self):
        from backend.production_modes import evaluate_mode_checks
        rows = [{"idx": 0, "kind": "quote", "text": "我在里面", "source": {"start": 1.0, "end": 1.84, "upload_id": "u",
                 "speaker_id": "S1", "asr_text": "我在里面。", "score": 1.0}}]
        messages = [issue["message"] for issue in evaluate_mode_checks(rows, [], "original", {}, "warn", [])
                    if issue["code"] == "QUOTE_TOO_SHORT"]
        self.assertEqual(messages, ["第 1 句原声只有 0.84 秒，不能短于 1 秒。"], "not 0.8400000000000003")


if __name__ == "__main__":
    unittest.main()
