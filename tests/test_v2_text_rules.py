"""Pure M0 tests. Synthetic text/clocks only; no app/config/dotenv/TestClient."""
from __future__ import annotations

import itertools
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from backend import production_modes as pm

CASES = json.loads(Path(__file__).with_name("cases").joinpath("text-rules.json").read_text(encoding="utf-8"))


def segment(text, id="s", start=0.0, end=5.0, speaker="S1", words=None):
    return {"id": id, "start": start, "end": end, "speaker_id": speaker, "text": text, "words": words or []}


def upload(*segments, id="u", sec=100.0):
    return {"id": id, "sec": sec, "transcript": list(segments)}


def quote(text, idx=0, **kwargs):
    return pm.SentenceInput(idx=idx, text=text, kind="quote", **kwargs)


def row(text="今天开放", start=0.0, end=2.0, snr=18.0, score=1.0, idx=0, actual=None):
    return {"idx": idx, "kind": "quote", "text": text, "source": {
        "upload_id": "u", "start": start, "end": end, "speaker_id": "S1", "score": score,
        "asr_text": actual if actual is not None else text, "snr_db": snr}}


class GoldenTests(unittest.TestCase):
    def test_shared_cases(self):
        handlers = {
            "terminal": lambda c: [[s.text, s.terminal_punctuation] for s in pm.parse_sentences(
                c["script"], c["mode"], {int(k): v for k, v in c.get("overrides", {}).items()})],
            "normalization": lambda c: pm.normalize_text(c["input"]),
            "similarity": lambda c: pm.similarity(c["left"], c["right"]),
            "split": lambda c: pm.split_text(c["text"], c["kind"]),
            "parseLine": lambda c: pm.parse_line(c["input"]),
            "fillers": lambda c: pm.strip_leading_fillers(c["input"]),
            "facts": lambda c: pm.facts_of(c["input"]),
            "status": lambda c: pm.score_status(c["score"], c.get("has_source", True)),
            "caption": lambda c: pm.canonical_quote_caption(c["input"]),
            "plan": lambda c: pm.recommend_plan(c["operations"], c["mode"]),
            "samples": lambda c: pm.sentence_needs(pm.parse_sentences(c["script"], c["mode"])),
        }
        for group, handler in handlers.items():
            for index, case in enumerate(CASES[group]):
                with self.subTest(group=group, index=index):
                    if case.get("error"):
                        with self.assertRaises(ValueError):
                            handler(case)
                    else:
                        self.assertEqual(handler(case), case["expected"])
        for case in CASES["checks"]:
            definition = pm.check_definition(case["code"])
            self.assertEqual({key: definition[key] for key in case if key != "code"},
                             {key: value for key, value in case.items() if key != "code"})

    def test_contract_and_no_import_side_effects(self):
        self.assertEqual(pm.RULES_VERSION, 2)
        self.assertEqual(CASES["rules_version"], pm.RULES_VERSION)
        for name in ("parse_sentences", "split_text", "parse_line", "normalize_text", "similarity",
                 "facts_of", "sentence_needs", "recommend_plan", "check_definition"):
            self.assertIn(name, pm.__all__)
            self.assertTrue(callable(getattr(pm, name)))
        self.assertEqual(len(pm.MODE_RULES["checks"]), 17)
        self.assertEqual(pm.LIMITS["alternative_takes"], 5)
        self.assertEqual(pm.LIMITS["alternative_match_min"], .5)
        self.assertEqual(pm.LIMITS["quote_snr_min_db"], 18)
        for name in ("backend.main", "backend.config", "backend.mode_pipeline", "dotenv", "httpx"):
            self.assertNotIn(name, sys.modules)
        self.assertEqual(pm.MODE_RULES["stage_weights_raw"], {
            "voiceover": [1] * 10, "mixed": [1,2,1,1,1,2,1,1,1.5,1],
            "original": [1,3,1,.5,1,2.5,1,1,2.5,1]})

    def test_manual_kind_map_does_not_resplit(self):
        script = "标题\n甲，乙。\n同期：丙，丁。"
        before = pm.parse_sentences(script, "mixed")
        after = pm.parse_sentences(script, "mixed", {0: "quote", 2: "narration"})
        self.assertEqual([s.text for s in before], [s.text for s in after])
        self.assertEqual([s.kind for s in after], ["quote", "narration", "narration"])
        self.assertEqual([s.terminal_punctuation for s in after], ["，", "。", "。"])

    def test_terminal_literal_schema_legacy_and_source_hint_roundtrip(self):
        legacy = {"idx": 0, "text": "原文。", "kind": "quote",
                  "source_hint": {"upload_id": "u", "seg_id": "s"}, "speaker_hint": "王红（主办方）"}
        original = deepcopy(legacy)
        self.assertEqual(pm.SentenceInput.model_validate(legacy).terminal_punctuation, "")
        for mark in ("", "，", ",", "。", ".", ";", "；", "!", "?", "！", "？"):
            value = pm.SentenceInput.model_validate({**legacy, "terminal_punctuation": mark})
            self.assertEqual(pm.SentenceInput.model_validate(value.model_dump()), value)
            self.assertEqual(value.source_hint.model_dump(), legacy["source_hint"])
        for mark in (None, 0, False, "。！", " ", "x", "、", "…", "）"):
            with self.subTest(mark=mark), self.assertRaises(ValueError):
                pm.SentenceInput.model_validate({**legacy, "terminal_punctuation": mark})
        self.assertEqual(legacy, original)
        schema = pm.SentenceInput.model_json_schema()
        self.assertNotIn("terminal_punctuation", schema["required"])
        self.assertEqual(schema["properties"]["terminal_punctuation"]["default"], "")

    def test_t1_sixteen_rows_source_endings_and_b_numeric_quotes(self):
        for sample in CASES["samples"]:
            rows = pm.parse_sentences(sample["script"], sample["mode"])
            self.assertEqual([s.terminal_punctuation for s in rows], CASES["sample_terminal"][sample["mode"]])
        a, b = CASES["samples"][:2]
        rows = pm.parse_sentences(a["script"], "voiceover")
        self.assertEqual(len(rows), 16)
        self.assertIn("“金马贺岁，高新同驰”", rows[1].text)
        rows = pm.parse_sentences(b["script"], "mixed")
        self.assertEqual([s.idx for s in rows if s.kind == "quote"], [3, 7, 8, 12, 15])
        self.assertIn("两百多斤", rows[8].text)
        self.assertIn("数字", pm.facts_of(rows[8].text))
        self.assertIn("1月31号", rows[15].text)
        self.assertEqual(rows[15].speaker_hint, "王红（市集主办方）")

    def test_original_sample_has_three_fact_rows_without_invented_duration(self):
        sample = next(case for case in CASES["samples"] if case["mode"] == "original")
        rows = pm.parse_sentences(sample["script"], "original")
        self.assertEqual([row.idx for row in rows if pm.facts_of(row.text)], [2, 4, 7])
        self.assertFalse(any(hasattr(row, "duration") for row in rows))

    def test_noise_duration_score_boundaries_and_immutability(self):
        for snr, noisy in ((17.999999, True), (18, False), (18.000001, False)):
            original = row(snr=snr)
            snapshot = deepcopy(original)
            issues = pm.evaluate_mode_checks([original], [], "original", {})
            self.assertEqual(any(i["code"] == "QUOTE_AUDIO_NOISY" for i in issues), noisy)
            self.assertEqual(original, snapshot)
        for seconds, level in ((1, None), (20, None), (20.000001, 1), (30, 1), (30.000001, 0)):
            issues = pm.evaluate_mode_checks([row(end=seconds)], [], "original", {})
            self.assertEqual(next((i["level"] for i in issues if i["code"] == "QUOTE_TOO_LONG"), None), level)
        self.assertFalse(any(i["code"] == "QUOTE_TOO_SHORT" for i in pm.evaluate_mode_checks([row(start=1.3, end=2.3)], [], "original", {})))
        self.assertTrue(any(i["code"] == "QUOTE_TOO_SHORT" for i in pm.evaluate_mode_checks([row(end=.999999)], [], "original", {})))

    def test_jump_every_non_broll_in_b_and_c(self):
        rows = [row(idx=0), row(idx=1, start=10, end=12)]
        for mode, cover in itertools.product(("mixed", "original"), ("zoom", "hard", "broll")):
            issues = pm.evaluate_mode_checks(rows, [], mode, {"jump_cut_cover": cover})
            jump = [i for i in issues if i["code"] == "JUMP_CUT_UNCOVERED"]
            self.assertEqual(len(jump), 1)
            self.assertEqual(jump[0]["level"], 1)
        covered = deepcopy(rows)
        covered[1]["cover_shot"] = 3
        self.assertFalse(any(i["code"] == "JUMP_CUT_UNCOVERED" for i in pm.evaluate_mode_checks(covered, [], "mixed", {})))

    def test_noncontiguous_c_is_blocked_even_with_high_score(self):
        self.assertFalse(pm.quote_text_is_contiguous("嗯，啊", {"asr_text": "嗯，啊"}))
        for expected, actual in (("abcd", "cdab"), ("我们来了", "我们，然后来了")):
            issues = pm.evaluate_mode_checks([row(expected, actual=actual)], [], "original", {})
            self.assertTrue(any(i["code"] == "QUOTE_NONCONTIGUOUS" and i["level"] == 0 for i in issues))


class AlignmentSafetyTests(unittest.TestCase):
    def test_equal_score_partial_span_cannot_hide_exact_cross_segment_evidence(self):
        result = pm.align_quotes([quote("今天欢迎大家")], [upload(
            segment("前文今天", id="a", start=1., end=3.),
            segment("欢迎大家后文", id="b", start=3.1, end=7.))])[0]
        self.assertEqual(result["source"]["matched_text"], "今天\n欢迎大家")
        self.assertEqual(result["source"]["asr_text"], "前文今天\n欢迎大家后文")
        self.assertEqual((result["start"], result["end"]), (1, 7))
        self.assertEqual(result["source"]["words"], [])

    def test_exhaustive_short_span_oracle(self):
        # Independent list-removal multiset oracle; no product similarity calls.
        def oracle(a, b):
            if min(len(a), len(b)) == 1:
                return float(a in b or b in a)
            right = [b[i:i+2] for i in range(len(b)-1)]
            hit = 0
            for i in range(len(a)-1):
                gram = a[i:i+2]
                if gram in right:
                    right.remove(gram)
                    hit += 1
            return max(2*hit/(len(a)+len(b)-2), hit/min(len(a)-1, len(b)-1))
        values = ["".join(p) for n in range(1, 5) for p in itertools.product("ab", repeat=n)]
        for query, reference in itertools.product(values, repeat=2):
            spans = pm._local_spans(query, reference)
            if not set(query) & set(reference):
                self.assertEqual(spans, [])
                continue
            for start, end, score in spans:
                self.assertEqual(score, oracle(query, reference[start:end]))
                self.assertEqual(score, max(oracle(query, reference[start:stop]) for stop in range(start+1, len(reference)+1)))

    def test_no_whole_best_score_inflation_with_indivisible_word(self):
        inputs = [quote("abcd")]
        source = upload(segment("cdab", words=[{"w": "cdab", "s": 0., "e": 5.}]))
        matched = pm.align_quotes(inputs, [source])[0]
        self.assertEqual(matched["score"], 2/3)
        self.assertEqual(matched["source"]["score"], 2/3)
        self.assertEqual(matched["source"]["words"], source["transcript"][0]["words"])
        self.assertFalse(pm.quote_text_is_contiguous("abcd", matched["source"]))

    def test_normative_score_and_safety_are_independent(self):
        body = "希望大家过年都能吃上一口家乡味"
        requested = "就是" + body
        actual = "前文。" + requested
        words = [{"w": "前文就是", "s": 1., "e": 2.}, {"w": body, "s": 2.2, "e": 5.}]
        result = pm.align_quotes([quote(requested)], [upload(segment(actual, words=words))])[0]
        self.assertEqual(result["source"]["words"], words)
        self.assertEqual(result["source"]["asr_text"], actual)
        # Normative containment is 1 here, not a whole-stream boosted score.
        self.assertEqual(result["score"], pm.similarity(requested, result["source"]["matched_text"]))
        for text in (requested.replace("过年", "今年过年"), requested.replace("过年", "")):
            result = pm.align_quotes([quote(text)], [upload(segment(requested))])[0]
            self.assertEqual(result["score"], pm.similarity(text, result["source"]["matched_text"]))
            result["text"] = text
            issues = pm.evaluate_mode_checks([result], [], "original", {})
            self.assertTrue(any(i["code"] == "QUOTE_NONCONTIGUOUS" and i["level"] == 0 for i in issues))

    def test_alternatives_include_half_match_limit_five_known_speaker_only(self):
        sources = [upload(segment("abcde", speaker="S1"), id="primary")]
        for n in range(7):
            sources.append(upload(segment("abxyc", speaker="S1", words=[{"w": "abxyc", "s": 0., "e": 5.}]), id=f"alt{n}"))
        # abxyc shares only ab: .25, cannot be an alternative.
        sources += [upload(segment("abcxy", words=[{"w": "abcxy", "s": 0., "e": 5.}]), id=f"half{n}") for n in range(7)]
        sources.append(upload(segment("abcde", speaker="S2"), id="other"))
        matched = pm.align_quotes([quote("abcde", source_hint=pm.SourceHint(upload_id="primary", seg_id="s"))], sources)[0]
        # A source hint intentionally bounds search; without it inspect all uploads.
        matched = pm.align_quotes([quote("abcde", speaker_hint="S1")], sources)[0]
        self.assertEqual(len(matched["alt_takes"]), 5)
        self.assertTrue(all(t["score"] == .5 and t["speaker_id"] == "S1" for t in matched["alt_takes"]))
        unknown = [upload(segment("abcde", speaker=""), id=f"unknown{n}") for n in range(3)]
        self.assertEqual(pm.align_quotes([quote("abcde")], unknown)[0]["alt_takes"], [])

    def test_source_hint_and_raw_query_cache_filler_restoration(self):
        words = [{"w": "嗯", "s": 1., "e": 1.3}, {"w": "欢迎", "s": 1.5, "e": 2.7}, {"w": "大家", "s": 3., "e": 4.5}]
        source = upload(segment("嗯，欢迎大家", start=1., end=5., words=words))
        before = deepcopy(source)
        rows = pm.align_quotes([quote("欢迎大家", idx=0), quote("嗯，欢迎大家", idx=1)], [source])
        self.assertEqual(rows[0]["source"]["words"], words[1:])
        self.assertEqual(rows[1]["source"]["words"], words)
        self.assertEqual(source, before)
        hints = [upload(segment("欢迎大家", id="first", start=1., end=5.),
                        segment("欢迎大家", id="later", start=40., end=45.))]
        matched = pm.align_quotes([quote("欢迎大家", source_hint=pm.SourceHint(upload_id="u", seg_id="later"))], hints)[0]
        self.assertEqual(matched["source"]["segment_ids"], ["later"])

    def test_no_word_clock_fabrication_and_trim_preserves_observed_words(self):
        source = pm.QuoteTake(take_id="take", upload_id="u", start=0., end=5., speaker_id="S1", asr_text="甲乙丙", score=.7142857142857143,
            words=[pm.Word(w="甲", s=0., e=.4), pm.Word(w="乙", s=.8, e=2.2), pm.Word(w="丙", s=2.5, e=5.)])
        trimmed = pm.validate_quote_trim(source, .8, 5.)
        self.assertEqual(trimmed["words"], [w.model_dump() for w in source.words[1:]])
        self.assertEqual(trimmed["asr_text"], "乙丙")
        self.assertEqual(trimmed["score"], source.score)
        with self.assertRaises(ValueError):
            pm.validate_quote_trim(source, 1., 5.)
        malformed = source.model_copy(update={"words": [pm.Word(w="错", s=0., e=5.)]})
        self.assertEqual(pm.QuoteTake.model_validate(malformed).precision, "segment")


if __name__ == "__main__":
    unittest.main()