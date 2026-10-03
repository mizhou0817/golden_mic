import re
from collections.abc import Sequence

from .models import PronunciationDecision, PronunciationReading, Sentence


NUMBER_EXPRESSION_PATTERN = re.compile(
    r"[0-9０-９]+(?:[,，][0-9０-９]{3})*(?:[.．][0-9０-９]+)?"
    r"(?:/[0-9０-９]+(?:[.．][0-9０-９]+)?)?(?:[%％])?"
)


def extract_number_expressions(text: str) -> list[str]:
    return [match.group(0) for match in NUMBER_EXPRESSION_PATTERN.finditer(text)]


def apply_pronunciation_readings(
    text: str,
    readings: Sequence[PronunciationReading],
) -> str:
    matches = list(NUMBER_EXPRESSION_PATTERN.finditer(text))
    if len(matches) != len(readings):
        raise ValueError(f"数字读音数量不匹配：期望 {len(matches)}，实际 {len(readings)}。")

    parts: list[str] = []
    cursor = 0
    for match, reading in zip(matches, readings, strict=True):
        source = match.group(0)
        if reading.source != source:
            raise ValueError(f"数字读音原文不匹配：期望 {source}，实际 {reading.source}。")
        parts.append(text[cursor:match.start()])
        parts.append(reading.spoken)
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def build_pronunciation_manifest(
    sentences: Sequence[Sentence],
    decisions: Sequence[PronunciationDecision],
) -> list[dict[str, object]]:
    by_sentence = {decision.sentence_id: decision for decision in decisions}
    manifest: list[dict[str, object]] = []
    for sentence in sentences:
        decision = by_sentence.get(sentence.sentence_id)
        readings = decision.readings if decision is not None else []
        tts_text = apply_pronunciation_readings(sentence.text, readings)
        manifest.append(
            {
                "sentence_id": sentence.sentence_id,
                "original_text": sentence.text,
                "tts_text": tts_text,
                "readings": [reading.model_dump(mode="json") for reading in readings],
            }
        )
    return manifest