"""Synthetic words/RMS, real editorial adapter, SNR calculation and alignment.

No ASR/model inference, host, real recording or claimed acoustic accuracy.
Run under run_v2_validation's guards, never against application data.
"""
from __future__ import annotations

import copy
import threading
import unittest
from types import SimpleNamespace

from backend import production_modes as pm
from backend.providers.asr import ASRTranscript
from backend.uploads import _Wave, _editorial_transcript
from tests.mode_acceptance_server import INTERVIEWS, MIXED_SCRIPT, ORIGINAL_SCRIPT, provider_fixture


def measured_snapshot(identity, duration, emissions, noise_levels):
    """10ms synthetic RMS observations; production computes adjacent SNR."""
    levels = [.02] * (duration * 100)
    for utterance, noise in zip(emissions["utterances"], noise_levels, strict=True):
        start = utterance["start_time_ms"] // 10
        end = utterance["end_time_ms"] // 10
        levels[max(0, start - 50):start] = [noise] * min(50, start)
        levels[start:end] = [.1] * (end - start)
        levels[end:min(len(levels), end + 50)] = [noise] * min(50, len(levels) - end)
    record = SimpleNamespace(id=identity, probe=SimpleNamespace(sec=float(duration), audio_offset=0.0))
    actual = _editorial_transcript(record, ASRTranscript.model_validate(emissions),
        _Wave(levels, [160] * len(levels), duration * 16000, [], True, "synthetic_rms"), threading.Event())
    return {"id": identity, "sec": duration, "transcript": actual.model_dump(mode="json"), "silences": []}


def fixture_snapshots():
    return [measured_snapshot(f"up_{index:032x}", item[1], provider_fixture(item[0]),
                              [.002 + .003 * (index * 3 + n) for n in range(len(item[5]))])
            for index, item in enumerate(INTERVIEWS)]


def evidence_payload():
    snapshots = fixture_snapshots()
    result = {}
    for mode, script in (("original", ORIGINAL_SCRIPT), ("mixed", MIXED_SCRIPT)):
        sentences = pm.parse_sentences(script, mode)
        result[mode] = {"sentences": [row.model_dump(mode="json") for row in sentences],
                        "matches": pm.align_quotes(sentences, snapshots)}
    return result


def source(text, *, identity="full", snr=10., words=None, speaker="S", start=1., end=5.):
    return {"id": identity, "sec": 60., "transcript": [
        {"id": "s", "text": text, "start": start, "end": end, "speaker_id": speaker,
         "words": words or [], "snr_db": snr}]}


def sentence(text, **kwargs):
    return pm.SentenceInput(idx=0, text=text, kind="quote", **kwargs)


class QuoteEvidenceRankingTests(unittest.TestCase):
    def test_measured_snr_fragment_cannot_outrank_complete_query(self):
        snapshots = fixture_snapshots()
        query = pm.parse_sentences(ORIGINAL_SCRIPT, "original")[0]
        fragment = pm.align_quotes([sentence("腊")], [snapshots[1]])[0]["source"]
        complete = pm.align_quotes([query], [snapshots[2]])[0]["source"]
        self.assertGreater(fragment["snr_db"], complete["snr_db"])
        self.assertEqual(pm.similarity(query.text, fragment["matched_text"]), 1.)
        self.assertEqual(complete["score"], 1.)
        self.assertEqual(fragment["asr_text"], "腊")
        selected = pm.align_quotes([query], snapshots)[0]["source"]
        self.assertEqual(selected, complete)

    def test_all_eight_original_and_five_mixed_selections_with_actual_snr(self):
        snapshots = fixture_snapshots()
        before = copy.deepcopy(snapshots)
        snrs = [s["snr_db"] for upload in snapshots for s in upload["transcript"]["segments"]]
        self.assertTrue(all(value is not None for value in snrs))
        self.assertEqual(len(set(snrs)), 8)
        for mode, script, expected in (("original", ORIGINAL_SCRIPT, 8), ("mixed", MIXED_SCRIPT, 5)):
            inputs = pm.parse_sentences(script, mode)
            rows = pm.align_quotes(inputs, snapshots)
            self.assertEqual(sum(s.kind == "quote" for s in inputs), expected)
            for item, row in zip(inputs, rows, strict=True):
                if item.kind != "quote":
                    self.assertIsNone(row["source"])
                    continue
                with self.subTest(mode=mode, idx=item.idx):
                    take = row["source"]
                    self.assertIsNotNone(take)
                    self.assertTrue(pm.quote_text_is_contiguous(item.text, take))
                    self.assertEqual(take["score"], pm.similarity(item.text, take["matched_text"]))
                    self.assertEqual(take["score"], 1.)
                    self.assertEqual(take["precision"], "word")
                    upload = next(u for u in snapshots if u["id"] == take["upload_id"])
                    observed = [w for s in upload["transcript"]["segments"] for w in s["words"]]
                    self.assertTrue(all(w in observed for w in take["words"]))
        self.assertEqual(snapshots, before)

    def test_complete_evidence_before_snr_unknown_snr_and_word_precision(self):
        for snr in (None, 5., 18.):
            with self.subTest(snr=snr):
                full = source("欢迎大家", snr=snr)
                fragment = source("欢迎", identity="fragment", snr=40.,
                                  words=[{"w": "欢迎", "s": 1., "e": 5.}])
                row = pm.align_quotes([sentence("欢迎大家")], [fragment, full])[0]
                self.assertEqual(row["upload_id"], "full")
                self.assertEqual(row["score"], 1.)
                self.assertEqual(row["alt_takes"][0]["upload_id"], "fragment")
                self.assertEqual(row["alt_takes"][0]["score"], 1.)

    def test_equal_complete_evidence_keeps_snr_tiebreak_and_stable_ids(self):
        sources = [source("欢迎大家", identity="quiet", snr=30.), source("欢迎大家", snr=10.)]
        first = pm.align_quotes([sentence("欢迎大家")], sources)[0]
        second = pm.align_quotes([sentence("欢迎大家")], list(reversed(sources)))[0]
        self.assertEqual(first, second)
        self.assertEqual(first["upload_id"], "quiet")

    def test_explicit_speaker_and_local_hint_keep_priority(self):
        sources = [source("欢迎大家", speaker="S1"), source("欢迎", identity="chosen", speaker="S2", snr=30.)]
        for hints in ({"speaker_hint": "S2"}, {"source_hint": pm.SourceHint(upload_id="chosen", seg_id="s")}):
            with self.subTest(hints=hints):
                row = pm.align_quotes([sentence("欢迎大家", **hints)], sources)[0]
                self.assertEqual(row["upload_id"], "chosen")
                self.assertEqual(row["asr_text"], "欢迎")
                self.assertFalse(pm.quote_text_is_contiguous("欢迎大家", row["source"]))

    def test_word_boundary_and_segment_evidence_are_not_fabricated(self):
        whole = source("前言欢迎大家尾声", words=[{"w": "前言欢迎大家尾声", "s": 1.3, "e": 4.7}])
        fragment = source("欢迎", identity="fragment", snr=40.)
        row = pm.align_quotes([sentence("欢迎大家")], [fragment, whole])[0]
        self.assertEqual(row["asr_text"], "前言欢迎大家尾声")
        self.assertEqual(row["source"]["words"], whole["transcript"][0]["words"])
        self.assertEqual(row["source"]["precision"], "word")
        self.assertEqual(row["source"]["score"], pm.similarity("欢迎大家", row["source"]["matched_text"]))

    def test_interior_deletions_and_missing_words_remain_unverified(self):
        for requested, actual in (("我们来了", "我们，然后来了"), ("欢迎参观", "欢迎大家参观"),
                                  ("买了点腊肉还有礼盒", "腊"), ("abcd", "cdab")):
            with self.subTest(requested=requested):
                row = pm.align_quotes([sentence(requested)], [source(actual, snr=40.)])[0]
                self.assertIsNotNone(row["source"])
                self.assertFalse(pm.quote_text_is_contiguous(requested, row["source"]))
                self.assertEqual(row["score"], pm.similarity(requested, row["source"]["matched_text"]))

    def test_internal_filler_not_preferred_over_actual_contiguous_evidence(self):
        sources = [source("我们，然后来了", identity="unsupported", snr=40.), source("我们来了", snr=10.)]
        row = pm.align_quotes([sentence("我们来了")], sources)[0]
        self.assertEqual(row["upload_id"], "full")
        self.assertEqual(row["score"], 1.)