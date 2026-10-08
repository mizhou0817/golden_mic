"""Chinese subtitles must render with the bundled Noto Sans SC, never a system fallback.

libass cannot select the variable font, so on a Linux server without system CJK fonts every character
rendered as a box while macOS hid it by falling back to PingFang. This renders through the real FFmpeg
with only backend/assets/fonts and checks which face libass actually picked.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from backend import readiness
from backend.subtitles import SUBTITLE_FONT_NAME

FONTS = Path(__file__).resolve().parent.parent / "backend" / "assets" / "fonts"
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Plain,{font},28,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,10,10,10,1
Style: Strong,{font},28,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Plain,,0,0,0,,南宁信息港迎春市集
Dialogue: 0,0:00:00.00,0:00:01.00,Strong,,0,0,0,,新能源汽车亮相
"""


class SubtitleFontTests(unittest.TestCase):
    def test_bundled_fonts_are_static_and_pinned(self):
        fonts = sorted(path.name for path in FONTS.glob("*.ttf"))
        self.assertEqual(fonts, sorted(readiness.EXPECTED_FONT_SHA256))
        self.assertFalse(any("variable" in name.lower() for name in fonts), "libass cannot select a variable font")
        readiness.validate_font_asset()

    def test_libass_renders_chinese_with_the_bundled_faces_only(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "FFmpeg is required for the subtitle renderer check")
        with tempfile.TemporaryDirectory(prefix="gm-subtitle-font-") as directory:
            ass = Path(directory) / "sample.ass"
            ass.write_text(ASS.format(font=SUBTITLE_FONT_NAME), encoding="utf-8")
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-nostdin", "-v", "verbose", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
                 "-vf", f"ass=filename={ass.as_posix()}:fontsdir={FONTS.as_posix()}", "-frames:v", "1",
                 "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=120, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        log = result.stderr.decode("utf-8", "replace")
        chosen = set(re.findall(r"fontselect: \(([^,]+), (\d+), \d+\) -> ([^,]+),", log))
        self.assertEqual(chosen, {(SUBTITLE_FONT_NAME, "400", "NotoSansSC-Regular"), (SUBTITLE_FONT_NAME, "700", "NotoSansSC-Bold")}, log[-1500:])
        self.assertNotIn("Glyph 0x", log, "every character must exist in the bundled font")
        frame = result.stdout
        self.assertEqual(len(frame), 320 * 180)
        self.assertGreater(sum(1 for value in frame if value > 128), 400, "text was actually drawn")


if __name__ == "__main__":
    unittest.main()
