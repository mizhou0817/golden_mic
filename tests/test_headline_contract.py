"""Pure opt-in headline preparation and legacy-parser regressions; no providers."""

import unittest

from backend.matching import parse_script, parse_segmented_script
from backend.script_segmentation import (
    _title_line_range,  # pyright: ignore[reportPrivateUsage]
    prepare_headline_script,
)


class HeadlineContractTests(unittest.TestCase):
    def test_short_decimal_body_excludes_headline_from_narration(self) -> None:
        title = "社区公园迎来3.5版便民服务正式开放"
        for body in ("票价3.5元。", "3.5."):
            with self.subTest(body=body):
                prepared = prepare_headline_script(f"{title}\n{body}")
                self.assertEqual(prepared, f"{title}\n\n{body}")
                self.assertEqual(_title_line_range(prepared), (0, len(title)))
                for document in (parse_script(prepared), parse_segmented_script(prepared, prepared)):
                    self.assertEqual(document.title, title)
                    self.assertEqual([sentence.text for sentence in document.sentences], [body])

    def test_line_endings_and_boundary_trimming_preserve_internal_content(self) -> None:
        source = " \t新闻  3.5版 \t\n \t\n\n \t票价  3.5元。 \n\n  第二段 / 原文\t保留。 \t\n"
        expected = "新闻  3.5版\n\n票价  3.5元。 \n\n  第二段 / 原文\t保留。"
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=newline):
                prepared = prepare_headline_script(source.replace("\n", newline))
                self.assertEqual(prepared, expected)
                self.assertEqual(prepare_headline_script(prepared), expected)

    def test_title_boundary_counts_code_points_not_utf16_or_graphemes(self) -> None:
        for title in ("新", "新" * 40, "📰" * 40, "e\u0301" * 20):
            with self.subTest(title=title):
                self.assertEqual(prepare_headline_script(f"{title}\n正文。"), f"{title}\n\n正文。")
        for title in ("新" * 41, "📰" * 41, "e\u0301" * 20 + "e"):
            with self.subTest(title=title):
                self.assertEqual(len(title), 41)
                with self.assertRaisesRegex(ValueError, "40"):
                    prepare_headline_script(f"{title}\n正文。")

    def test_rejects_actual_empty_first_line_and_missing_body(self) -> None:
        for source, message in (
            ("", "首行标题不能为空"),
            ("\n标题\n正文。", "首行标题不能为空"),
            ("\r\n标题\r\n正文。", "首行标题不能为空"),
            (" \t\n标题\n正文。", "首行标题不能为空"),
            ("标题", "正文"),
            ("标题\n \t\r\n", "正文"),
        ):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, message):
                prepare_headline_script(source)

    def test_rejects_all_forbidden_title_endings_even_before_whitespace(self) -> None:
        for ending in "。！？.!?；;":
            for padding in ("", " \t"):
                with self.subTest(ending=ending, padding=padding), self.assertRaisesRegex(ValueError, "句末标点"):
                    prepare_headline_script(f"新闻标题{ending}{padding}\n正文。")

    def test_legacy_auto_parsers_keep_their_existing_heuristics(self) -> None:
        title, body = "社区公园迎来3.5版便民服务正式开放", "票价3.5元。"
        for script in (f"{title}\n{body}", "城市新闻。\n公园开放。"):
            with self.subTest(script=script):
                document = parse_script(script)
                self.assertIsNone(document.title)
                self.assertIsNone(_title_line_range(script))
                self.assertEqual("".join(sentence.text for sentence in document.sentences), script.replace("\n", ""))
        for script in (f"{title}\n\n{body}", "新闻\n公园正式开放了。"):
            with self.subTest(script=script):
                self.assertEqual(parse_script(script).title, script.splitlines()[0])
                self.assertIsNotNone(_title_line_range(script))


if __name__ == "__main__":
    unittest.main()