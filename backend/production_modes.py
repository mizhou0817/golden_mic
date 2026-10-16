"""Deterministic three-mode editorial helpers, independent of application settings.

The only resource read is the adjacent, versioned mode_rules.json. No config,
dotenv, media, data directory, model provider or subprocess is imported/accessed.
Times are absolute seconds in the ORIGINAL upload, never relative word offsets.
Callers must supply genuine ASR/diarization evidence; structural validation cannot
prove that an upstream timestamp or speaker identity is acoustically correct.

parse_sentences is a preview/parser, not an override of client-confirmed sentences.
align_quotes consumes those sentences as supplied. A segment-precision take has
the entire segment(s) in asr_text and a local matched_text for its lexical score;
it MUST NOT be word-trimmed. Neither text nor word timing is synthesized.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypedDict, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Mode: TypeAlias = Literal["voiceover", "mixed", "original"]
Kind: TypeAlias = Literal["narration", "quote"]
TerminalPunctuation: TypeAlias = Literal["", "，", ",", "。", ".", ";", "；", "!", "?", "！", "？"]
Cover: TypeAlias = Literal["broll", "zoom", "hard"]

MODE_RULES: dict[str, Any] = json.loads(
    Path(__file__).with_name("mode_rules.json").read_text(encoding="utf-8")
)
LIMITS: dict[str, Any] = MODE_RULES["limits"]
RULES_VERSION: int = MODE_RULES["rules_version"]
PACING_CPM: dict[str, int] = dict(MODE_RULES["pacing_cpm"])
MATCH_OK: float = LIMITS["match_ok"]
MATCH_LOW: float = LIMITS["match_low"]
# Admission/work limits are not matching thresholds. Exhaustion fails explicitly;
# it never publishes a partial-search score or silently drops inexact matches.
MAX_TRANSCRIPT_SECONDS = 3600.0
MAX_TRANSCRIPT_CHARS = 200_000
MAX_SEARCH_STEPS = 1_000_000
STAGE_WEIGHTS: dict[Mode, tuple[float, ...]] = {
    cast(Mode, mode): tuple(float(w) / sum(weights) for w in weights)
    for mode, weights in MODE_RULES["stage_weights_raw"].items()
}


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class SourceHint(_Contract):
    upload_id: str = Field(min_length=1)
    seg_id: str = Field(min_length=1)

    @field_validator("upload_id", "seg_id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("素材和转写段标识不能为空。")
        return value


class SentenceInput(_Contract):
    idx: int = Field(ge=0, strict=True)
    text: str = Field(min_length=1, max_length=LIMITS["sentence_max_chars"])
    kind: Kind
    speaker_hint: str = ""
    source_hint: SourceHint | None = None
    # Source metadata, not inferred from punctuation-stripped display text.
    # Missing historical fields remain unknown; do not migrate old timing.
    terminal_punctuation: TerminalPunctuation = ""

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("句子不能为空。")
        return value


class Word(_Contract):
    w: str = Field(min_length=1)
    s: float = Field(ge=0, strict=True)
    e: float = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def valid_word(self) -> Word:
        if not self.w.strip() or self.e <= self.s:
            raise ValueError("词文本不能为空，结束时间必须晚于开始时间。")
        return self


class TranscriptSegment(_Contract):
    id: str = Field(min_length=1)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    speaker_id: str
    text: str
    words: list[Word] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1, strict=True)
    snr_db: float | None = Field(default=None, strict=True)

    @field_validator("words", mode="before")
    @classmethod
    def optional_words(cls, value: Any) -> Any:
        # A missing/null provider word array conveys no timing evidence.
        # Malformed non-null entries still fail validation, not fake precision.
        return [] if value is None else value

    @model_validator(mode="after")
    def valid_range(self) -> TranscriptSegment:
        if not self.id.strip() or self.end <= self.start:
            raise ValueError("转写段标识和时间范围无效。")
        return self


class Speaker(_Contract):
    id: str = Field(min_length=1)
    name: str = ""
    title: str = ""
    auto_label: str = ""
    appearances: int = Field(default=0, ge=0, strict=True)
    seconds: float = Field(default=0, ge=0, strict=True)

    @field_validator("id")
    @classmethod
    def nonblank_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("说话人标识不能为空。")
        return value


class QuoteTake(_Contract):
    take_id: str = Field(min_length=1)
    upload_id: str = Field(min_length=1)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    speaker_id: str
    asr_text: str
    score: float = Field(ge=0, le=1, strict=True)
    snr_db: float | None = Field(default=None, strict=True)
    words: list[Word] = Field(default_factory=list)
    precision: Literal["word", "segment"] = "word"
    matched_text: str = ""
    segment_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_take(self) -> QuoteTake:
        if self.end <= self.start or not self.upload_id.strip() or not self.take_id.strip():
            raise ValueError("原声片段标识和时间范围无效。")
        if self.precision == "word" and _word_spans(
            self.asr_text, self.words, self.start, self.end
        ) is None:
            self.precision = "segment"
        return self


class JumpCut(_Contract):
    after_row: int = Field(ge=0, strict=True)
    cover: Cover
    shot: int | None = Field(default=None, ge=0, strict=True)
    downgraded: bool = False


def _mode(value: str) -> Mode:
    if value not in STAGE_WEIGHTS:
        raise ValueError("mode 必须为 voiceover、mixed 或 original。")
    return cast(Mode, value)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须为有限数值。")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} 必须为有限数值。") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} 必须为有限数值。")
    return result


def _clock_delta(later: float, earlier: float) -> float:
    # Compare the supplied decimal-second clocks, not a cancellation-rounded
    # binary subtraction (2.3-1.3 < 1). No epsilon, padding or timestamp rewrite.
    return float(Decimal(str(later)) - Decimal(str(earlier)))


@dataclass(frozen=True, slots=True)
class _Normalized:
    text: str
    spans: tuple[tuple[int, int], ...]


def _unicode_chars(text: str) -> tuple[str, list[tuple[int, int]]]:
    """NFKC/casefold with original offsets, including combining/Jamo clusters."""
    output: list[str] = []
    offsets: list[tuple[int, int]] = []
    cluster = ""
    lo = 0
    for index, character in enumerate(text):
        combined = unicodedata.normalize("NFKC", cluster + character)
        separate = unicodedata.normalize("NFKC", cluster) + unicodedata.normalize("NFKC", character)
        if cluster and not unicodedata.combining(character) and combined == separate:
            normalized = unicodedata.normalize("NFKC", cluster).casefold()
            output.extend(normalized)
            offsets.extend([(lo, index)] * len(normalized))
            lo = index
            cluster = character
        else:
            cluster += character
    normalized = unicodedata.normalize("NFKC", cluster).casefold()
    output.extend(normalized)
    offsets.extend([(lo, len(text))] * len(normalized))
    return "".join(output), offsets


def _boundary(character: str) -> bool:
    return character.isspace() or unicodedata.category(character)[0] in "PZS"


_FILLERS = ("就是说", "那个", "就是", "然后", "嗯", "啊", "呃")
_DISCOURSE_NEXT = ("我们", "我", "你", "他们", "他", "她", "大家", "想", "希望", "要", "再", "今天", "这个", "其实")
_DIGITS = {
    character: str(value)
    for value, glyphs in enumerate(("零〇", "一壹", "二两兩贰貳", "三叁參", "四肆", "五伍", "六陆陸", "七柒", "八捌", "九玖"))
    for character in glyphs
}
_SMALL_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_NUMBER_RE = re.compile(r"[负負+-]?(?:[0-9零〇一二两兩三四五六七八九十百千万萬亿億壹贰貳叁參肆伍陆陸柒捌玖拾佰仟]+)(?:[.点點][0-9零〇一二两兩三四五六七八九]+)?")
_ONE_IDIOMS = ("起", "直", "样", "致", "般", "旦", "定", "些", "切", "律", "再", "并", "共", "味", "体")


def _integer(token: str) -> str:
    token = token.replace("萬", "万").replace("億", "亿")
    for unit, factor in (("亿", 100_000_000), ("万", 10_000)):
        if unit in token:
            left, right = token.rsplit(unit, 1)
            return str(int(_integer(left or "一")) * factor + int(_integer(right or "零")))
    if not any(c in _SMALL_UNITS for c in token):
        return "".join(_DIGITS.get(c, c) for c in token)
    total, previous_unit = 0, 10_000
    digits = ""
    for character in token:
        if character not in _SMALL_UNITS:
            digits += _DIGITS.get(character, character)
            continue
        factor = _SMALL_UNITS[character]
        if factor >= previous_unit:
            raise ValueError("不是明确的中文数值。")
        total += int(digits or "1") * factor
        digits = ""
        previous_unit = factor
    return str(total + int(digits or "0"))


def _number(token: str, following: str) -> str:
    if token == "一" and following.startswith(_ONE_IDIOMS):
        return token
    if token == "千万" and following.startswith(("别", "不", "要", "记")):
        return token
    sign = ""
    if token[0] in "负負+-":
        sign = "-" if token[0] in "负負-" else "+"
        token = token[1:]
    integer, *fraction = re.split(r"[.点點]", token)
    try:
        result = _integer(integer)
        if fraction:
            result += "." + _integer(fraction[0])
        return sign + result
    except (ValueError, RecursionError):
        return sign + token


def _normalize_with_mapping(text: str, *, remove_fillers: bool = True) -> _Normalized:
    if not isinstance(text, str):
        raise TypeError("text 必须为字符串。")
    raw, spans = _unicode_chars(text)
    retained: list[str] = []
    retained_spans: list[tuple[int, int]] = []
    index = 0
    after_filler = False
    while index < len(raw):
        at_boundary = index == 0 or after_filler or _boundary(raw[index - 1])
        removed = False
        if remove_fillers and at_boundary:
            for filler in _FILLERS:
                if not raw.startswith(filler, index):
                    continue
                # Only prefix tests follow. Avoid copying the entire remaining
                # 200k-character transcript at every discourse boundary.
                following = raw[index + len(filler):index + len(filler) + 4]
                standalone = not following or _boundary(following[0])
                discourse = following.startswith(_DISCOURSE_NEXT + _FILLERS)
                if standalone or discourse:
                    index += len(filler)
                    after_filler = removed = True
                    break
        if removed:
            continue
        after_filler = False
        if not raw[index].isspace():
            retained.append(raw[index])
            retained_spans.append(spans[index])
        index += 1
    # Resolve fillers with the original boundaries first; join whitespace only
    # afterwards, so 三十\n一 in adjacent ASR segments is the number 31, not 301.
    raw, spans = "".join(retained), retained_spans
    characters: list[str] = []
    mapping: list[tuple[int, int]] = []
    index = 0
    while index < len(raw):
        numeric = _NUMBER_RE.match(raw, index)
        if numeric:
            stop = numeric.end()
            normalized = _number(numeric.group(), raw[stop:stop + 2])
            characters.extend(normalized)
            mapping.extend([(spans[index][0], spans[stop - 1][1])] * len(normalized))
            index = stop
            continue
        character = raw[index]
        if not _boundary(character) and unicodedata.category(character)[0] != "C":
            characters.append(character)
            mapping.append(spans[index])
        index += 1
    normalized = "".join(characters)
    # A date's 号/日 are equivalent; a house number (95号) is not a date.
    for match in re.finditer(r"[0-9]+月[0-9]+号", normalized):
        characters[match.end() - 1] = "日"
    return _Normalized("".join(characters), tuple(mapping))


def normalize_text(text: str) -> str:
    """Normalize lexical comparison, not displayed/transcribed text.

    Fillers are removed at discourse boundaries, not with arbitrary substring
    replacement (那个人/然后果/呃逆 remain intact). Numeric decimal points and
    signs retain meaning. Ambiguous colloquial numbers/idioms are not inferred.
    """
    return _normalize_with_mapping(text).text


def quote_text_is_contiguous(text: str, take: QuoteTake | dict) -> bool:
    """True only for a nonempty normalized substring of actual source ASR.

    Formatting and equivalent numbers use the same mapping as matching, but
    requested fillers cannot be inserted or removed internally. A filler-only
    query has no lexical evidence. Similarity never licenses insertion, substitution
    (including a homophone) or deletion in the middle. This is lexical evidence,
    not proof of speaker identity or a licence to invent word-level timing.
    """
    actual = take.asr_text if isinstance(take, QuoteTake) else take.get("asr_text")
    if not isinstance(text, str) or not isinstance(actual, str):
        return False
    expected = _normalize_with_mapping(text, remove_fillers=False).text
    return bool(normalize_text(text)) and bool(expected) and expected in _normalize_with_mapping(actual, remove_fillers=False).text


_SPEAKER_PREFIX = re.compile(r"^([^\s（）():：，。！？,!?]{1,40})\s*[（(]([^（）()\n]{1,80})[）)]\s*[:：]\s*(.*)$")
_NAMED_SYNC = re.compile(r"^([^\s（）():：，。！？,!?]{1,40})(?:\s*[（(]([^（）()\n]{1,80})[）)])?\s*[:：]\s*(.*)$")
_SYNC_PREFIX = re.compile(r"^(?:【同期】\s*[:：]?\s*|同期(?:\s*[:：]\s*|\s+|$))(.*)$")


def _line_kind(line: str) -> tuple[Kind, str, str]:
    narrative = re.match(r"^(?:旁白|记者)\s*[:：]\s*(.*)$", line)
    if narrative:
        return "narration", narrative[1], ""
    sync = _SYNC_PREFIX.match(line)
    content = sync[1] if sync else line
    named = (_NAMED_SYNC if sync else _SPEAKER_PREFIX).match(content)
    if named:
        name, title, content = named.groups()
        hint = name + (f"（{title.strip()}）" if title else "")
        return "quote", content, hint
    return ("quote" if sync else "narration"), content, ""


def _source_terminal(text: str) -> TerminalPunctuation:
    ending = text.rstrip().rstrip('”’"\'）)]}】》」』').rstrip()
    return cast(TerminalPunctuation, ending[-1]) if ending and ending[-1] in "，,。.;；!?！？" else ""


def _split_line_parts(text: str, kind: Kind) -> list[tuple[str, TerminalPunctuation]]:
    text = text.strip()
    if len(text) >= 2 and (text[0], text[-1]) in (("“", "”"), ('"', '"'), ("‘", "’")):
        text = text[1:-1]
    delimiters = "，,。.;；!?！？" if kind == "narration" else "。.!?！？"
    pairs = {"（": "）", "(": ")", "【": "】", "[": "]", "{": "}", "《": "》", "“": "”", "‘": "’"}
    stack: list[str] = []
    result: list[tuple[str, TerminalPunctuation]] = []
    start = 0
    for index, character in enumerate(text):
        if character == '"' and (index == 0 or text[index - 1] != "\\"):
            if stack and stack[-1] == character:
                stack.pop()
            else:
                stack.append(character)
        elif character in pairs:
            stack.append(pairs[character])
        elif stack and character == stack[-1]:
            stack.pop()
        decimal = character == "." and 0 < index < len(text) - 1 and text[index - 1].isdigit() and text[index + 1].isdigit()
        if character in delimiters and not stack and not decimal:
            if text[start:index].strip():
                result.append((text[start:index].strip(), cast(TerminalPunctuation, character)))
            elif result:
                # A punctuation run belongs to the preceding source span.
                result[-1] = (result[-1][0], cast(TerminalPunctuation, character))
            start = index + 1
    if text[start:].strip():
        result.append((text[start:].strip(), _source_terminal(text[start:])))
    return result


def _split_line(text: str, kind: Kind) -> list[str]:
    return [part for part, _ in _split_line_parts(text, kind)]


def parse_line(line: str) -> dict[str, str]:
    """Public, lossless prefix classification; not sentence splitting."""
    kind, content, hint = _line_kind(line.strip())
    return {"kind": kind, "content": content, "hint": hint}


def split_text(text: str, kind: Kind = "narration") -> list[str]:
    if kind not in ("narration", "quote"):
        raise ValueError("无效句子类型。")
    return _split_line(text, kind)


def strip_leading_fillers(text: str) -> str:
    """Wizard selection ONLY. Never run on stored ASR or its word evidence."""
    result = text.strip()
    while result:
        for filler in _FILLERS:
            if not result.startswith(filler):
                continue
            following = result[len(filler):]
            if not following or _boundary(following[0]) or following.startswith(_DISCOURSE_NEXT + _FILLERS):
                result = following.lstrip(" \t\r\n，,。.…!?！？;；、")
                break
        else:
            break
    return result


def facts_of(text: str) -> list[str]:
    """Editorial prompts, not verified facts; includes Chinese-number quantities."""
    # Idiomatic 一下/一口 are not numerical claims in the handoff sample.
    value = normalize_text(text.replace("一下", "片刻").replace("一口", "口"))
    facts: list[str] = []
    if re.search(r"[0-9]+月[0-9]+日|[0-9]{4}年|[0-9]+[时分秒]", value):
        facts.append("时间")
    if re.search(r"[0-9]+号|区|广场|信息港", value):
        facts.append("地点")
    if re.search(r"主办|协办|承办", value):
        facts.append("主办方")
    if re.search(r"[0-9]", value):
        facts.append("数字")
    if re.search(r"先生|女士|大姐|主任|经理|教授|院士|记者|负责人|发言人", value):
        facts.append("称谓")
    return facts


def sentence_needs(sentences: list[SentenceInput]) -> dict[str, int]:
    """Wizard shot estimate, not assigned shots, render duration or availability."""
    narration = [sentence for sentence in sentences if sentence.kind == "narration"]
    extra = sum("、" in sentence.text for sentence in narration)
    return {"narration": len(narration), "quotes": len(sentences) - len(narration),
            "list_extra": extra, "needed_shots": len(narration) + extra}


def score_status(score: float, has_source: bool = True) -> str:
    value = _finite(score, "score")
    if not 0 <= value <= 1:
        raise ValueError("score 超出范围。")
    return "missing" if not has_source or value < MATCH_LOW else "low" if value < MATCH_OK else "ok"


def canonical_quote_caption(value: str) -> str:
    if value not in ("spoken", "asr", "none"):
        raise ValueError("原声字幕必须为 spoken 或 none。")
    return "spoken" if value == "asr" else value


def check_definition(code: str) -> dict[str, Any]:
    """Copy a current-rule template; never mutate historical report findings."""
    template = MODE_RULES["checks"].get(code, MODE_RULES["safety_checks"].get(code))
    if template is None:
        raise ValueError("未知检查代码。")
    return dict(template)


class PlanRecommendation(TypedDict):
    rules_version: int
    run: list[int]


def recommend_plan(operations: list[str], mode: Mode) -> PlanRecommendation:
    """Pure recommendation, NOT a workbench queue, revision or render command."""
    _mode(mode)
    stages: set[int] = set()
    for operation in operations:
        if operation not in MODE_RULES["plan_stages"]:
            raise ValueError("未知修改操作。")
        if mode == "original" and operation in ("pacing", "voice", "to_narration"):
            raise ValueError("只用原声不能调语速、配音或转旁白。")
        if operation == "to_narration" and mode != "mixed":
            raise ValueError("仅混合模式可以转旁白。")
        stages.update(MODE_RULES["plan_stages"][operation])
    return {"rules_version": RULES_VERSION, "run": sorted(stages)}


def parse_sentences(
    script: str, mode: Mode, kind_overrides: Mapping[int, str] | None = None
) -> list[SentenceInput]:
    """Skip the first nonempty line. Overrides address stable, parsed row indices.

    B overrides change kinds, not segmentation. A/C reject incompatible kinds.
    speaker_hint retains 姓名（身份） when supplied; it never proves identity.
    """
    _mode(mode)
    if not isinstance(script, str):
        raise TypeError("script 必须为字符串。")
    if len(script) > LIMITS["script_max_chars"]:
        raise ValueError("稿子不能超过 8000 字符。")
    lines = [line.strip() for line in script.lstrip("\ufeff").splitlines() if line.strip()]
    sentences: list[SentenceInput] = []
    for line in lines[1:]:
        kind, text, hint = _line_kind(line)
        if mode == "voiceover":
            kind, text, hint = "narration", line, ""
        elif mode == "original":
            kind = "quote"
        for part, terminal in _split_line_parts(text, kind):
            sentences.append(SentenceInput(idx=len(sentences), text=part, kind=kind,
                                           speaker_hint=hint, terminal_punctuation=terminal))
    for idx, kind in (kind_overrides or {}).items():
        if type(idx) is not int or not 0 <= idx < len(sentences):
            raise ValueError("句子类型覆盖的 idx 不存在。")
        if kind not in MODE_RULES["modes"][mode]["allowed_kinds"]:
            raise ValueError("句子类型与制作模式不兼容。")
        sentences[idx] = sentences[idx].model_copy(update={"kind": kind})
    return sentences


def _literal(text: str) -> _Normalized:
    raw, offsets = _unicode_chars(text)
    # Formatting punctuation is ignorable; numeric signs/decimal points are not.
    numeric_symbols = {
        index for match in _NUMBER_RE.finditer(raw)
        for index in range(match.start(), match.end()) if raw[index] in "+-."
    }
    retained = [(c, span) for index, (c, span) in enumerate(zip(raw, offsets))
                if index in numeric_symbols or (not _boundary(c) and unicodedata.category(c)[0] != "C")]
    return _Normalized("".join(c for c, _ in retained), tuple(span for _, span in retained))


def _word_spans(
    text: str, words: list[Word], start: float, end: float
) -> list[tuple[int, int]] | None:
    """Only a complete, ordered, literal transcript-to-word map is trim-safe."""
    if not words:
        return None
    literal = _literal(text)
    keys = [_literal(word.w).text for word in words]
    if not words or not all(keys) or "".join(keys) != literal.text:
        return None
    cursor = 0
    previous = start
    spans: list[tuple[int, int]] = []
    for word, key in zip(words, keys):
        if word.s < previous or word.e > end:
            return None
        spans.append((literal.spans[cursor][0], literal.spans[cursor + len(key) - 1][1]))
        previous = word.e
        cursor += len(key)
    return spans


def _word_text_range(text: str, spans: list[tuple[int, int]], first: int, last: int) -> tuple[int, int]:
    return (0 if first == 0 else spans[first][0], len(text) if last + 1 == len(spans) else spans[last + 1][0])


def _distance(left: str, right: str) -> int:
    """Exact global Levenshtein distance, using arbitrary-width Myers vectors."""
    if left == right:
        return 0
    if len(left) > len(right):
        left, right = right, left
    if not left:
        return len(right)
    masks: dict[str, int] = {}
    for i, character in enumerate(left):
        masks[character] = masks.get(character, 0) | (1 << i)
    full, top = (1 << len(left)) - 1, 1 << (len(left) - 1)
    positive, negative, distance = full, 0, len(left)
    for character in right:
        equal = masks.get(character, 0)
        vertical = equal | negative
        horizontal = (((equal & positive) + positive) ^ positive) | equal
        plus = negative | ~(horizontal | positive)
        minus = positive & horizontal
        distance += bool(plus & top) - bool(minus & top)
        plus = (plus << 1) | 1
        minus <<= 1
        positive = (minus | ~(vertical | plus)) & full
        negative = (plus & vertical) & full
    return distance


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if min(len(left), len(right)) == 1:
        return float(left in right or right in left)
    a = Counter(left[i:i + 2] for i in range(len(left) - 1))
    b = Counter(right[i:i + 2] for i in range(len(right) - 1))
    intersection = sum((a & b).values())
    return max(2.0 * intersection / (len(left) + len(right) - 2),
               intersection / min(len(left) - 1, len(right) - 1))


def similarity(left: str, right: str) -> float:
    """Normative §10.3, full precision. Similarity is NOT continuity evidence."""
    return _similarity(normalize_text(left), normalize_text(right))


@dataclass(slots=True)
class _SearchBudget:
    remaining: int = MAX_SEARCH_STEPS

    def consume(self, steps: int) -> None:
        self.remaining -= steps
        if self.remaining < 0:
            raise ValueError("原话精确搜索超出计算上限；请提供更具体的来源提示或缩短稿句/转写，不返回不完整匹配。")


def _local_spans(
    query: str, reference: str, boundaries: tuple[int, ...] | None = None,
    *, budget: _SearchBudget | None = None, best_only: bool = False,
) -> list[tuple[int, int, float]]:
    """Exact §10.3 scores on legal contiguous spans, with a shared work bound.

    The old edit-distance length bound is NOT valid for bigram containment.
    Incremental multiset intersections avoid rescoring each substring. Equal
    scores prefer query-sized evidence, then the earlier end, not a tiny fragment.
    No whole-stream score is assigned to a different candidate or its word clocks.
    """
    if not query or not reference:
        return []
    if not set(query) & set(reference):
        return []
    budget = budget if budget is not None else _SearchBudget()
    m, n = len(query), len(reference)
    grams = Counter(query[i:i + 2] for i in range(m - 1))
    points = boundaries if boundaries is not None else tuple(range(n + 1))
    ends = set(points)
    if best_only and n <= m:
        # Here containment reaches its upper bound 1 iff every span bigram
        # count fits the query multiset (or a singleton occurs in the query).
        # A sliding window finds the longest such LEGAL span in linear work.
        # Since all lengths <= m, longest/earliest is exactly the existing tie
        # order. If no perfect legal span exists, retain exhaustive fallback.
        budget.consume(2 * n)  # each character enters/leaves at most once
        window: Counter[str] = Counter()
        left = 0
        perfect: tuple[int, int, float] | None = None
        for right in range(n):
            if right > left:
                gram = reference[right - 1:right + 1]
                window[gram] += 1
                while window[gram] > grams[gram]:
                    window[reference[left:left + 2]] -= 1
                    left += 1
            if right + 1 not in ends:
                continue
            legal = points[bisect_left(points, left)]
            length = right + 1 - legal
            if length <= 0 or (length == 1 and reference[legal] not in query):
                continue
            if perfect is None or length > perfect[1] - perfect[0]:
                perfect = (legal, right + 1, 1.0)
        if perfect is not None:
            return [perfect]
    found: list[tuple[int, int, float]] = []
    for start in points[:-1]:
        if start + m in ends and reference.startswith(query, start):
            budget.consume(1)
            if best_only:
                return [(start, start + m, 1.0)]
            found.append((start, start + m, 1.0))
            continue
        counts: Counter[str] = Counter()
        hit = 0
        best_score, best_end = -1.0, start
        budget.consume(n - start)
        for index in range(start, n):
            length = index + 1 - start
            if length > 1:
                gram = reference[index - 1:index + 1]
                counts[gram] += 1
                hit += counts[gram] <= grams[gram]
            if index + 1 not in ends:
                continue
            score = (_similarity(query, reference[start:index + 1]) if min(m, length) == 1
                     else max(2.0 * hit / (m + length - 2), hit / min(m - 1, length - 1)))
            if score > best_score or (score == best_score and abs(length - m) < abs(best_end - start - m)):
                best_score, best_end = score, index + 1
        if best_end > start:
            if not best_only:
                found.append((start, best_end, best_score))
            elif not found or best_score > found[0][2] or (
                best_score == found[0][2] and (abs(best_end - start - m), start, best_end)
                < (abs(found[0][1] - found[0][0] - m), found[0][0], found[0][1])
            ):
                found = [(start, best_end, best_score)]
    return found


def _silences(values: Any, duration: float) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for pair in values or ():
        if isinstance(pair, dict):
            lo, hi = pair.get("start"), pair.get("end")
        else:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("静音必须是 start/end 时间区间。")
            lo, hi = pair
        lo, hi = _finite(lo, "silence.start"), _finite(hi, "silence.end")
        if not 0 <= lo < hi <= duration:
            raise ValueError("静音区间必须在素材内。")
        result.append((lo, hi))
    return sorted(result)


def refine_cut(
    words: list[Word], start: float, end: float, duration: float,
    silences: list[tuple[float, float]] = [],
) -> tuple[float, float]:
    """Preserve intersected words; target 120/200 ms handles, then safe silence.

    Silence snapping is at most 300 ms from each padded target. Source edges,
    adjacent words and snapping may shorten a handle. Empty words mean segment
    precision: leave the supplied interval untouched, without inventing boundaries.
    The contract's empty-list default and caller list are never mutated. Silence
    is caller-provided evidence, not inferred from the spaces between words.
    """
    start, end, duration = _finite(start, "start"), _finite(end, "end"), _finite(duration, "duration")
    if not 0 <= start < end <= duration:
        raise ValueError("剪切范围必须在素材内。")
    cues = [Word.model_validate(word) for word in words]
    previous = 0.0
    for word in cues:
        if word.s < previous or word.e > duration:
            raise ValueError("词时间必须按顺序、不重叠且位于素材内。")
        previous = word.e
    quiet = _silences(silences, duration)
    if not cues:
        return start, end
    selected = [i for i, word in enumerate(cues) if word.e > start and word.s < end]
    if not selected:
        raise ValueError("选区内没有真实词时间。")
    first, last = selected[0], selected[-1]
    core_start, core_end = min(start, cues[first].s), max(end, cues[last].e)
    lower = cues[first - 1].e if first else 0.0
    upper = cues[last + 1].s if last + 1 < len(cues) else duration
    head = max(lower, core_start - LIMITS["cut_head_seconds"])
    tail = min(upper, core_end + LIMITS["cut_tail_seconds"])
    quiet = [(lo, hi) for lo, hi in quiet if not any(w.s < hi and w.e > lo for w in cues)]

    def snap(target: float, low: float, high: float) -> float:
        options: list[float] = []
        for lo, hi in quiet:
            lo, hi = max(lo, low), min(hi, high)
            if lo <= hi:
                candidate = max(lo, min(hi, target))
                if abs(candidate - target) <= LIMITS["cut_snap_seconds"]:
                    options.append(candidate)
        return min(options, key=lambda value: (abs(value - target), value)) if options else target

    return snap(head, lower, core_start), snap(tail, core_end, upper)


@dataclass(slots=True)
class _Stream:
    upload_id: str
    duration: float
    segments: list[TranscriptSegment]
    all_segments: list[TranscriptSegment]
    text: str
    ranges: list[tuple[int, int]]
    word_ranges: list[list[tuple[int, int]] | None]
    normalized: _Normalized
    silences: list[tuple[float, float]]
    boundaries: tuple[int, ...] = ()


def _search_boundaries(stream: _Stream) -> tuple[int, ...]:
    """Do not start/end inside a supplied word or expanded numeric token."""
    word_ranges: list[tuple[int, int]] = []
    for (base, _), spans in zip(stream.ranges, stream.word_ranges):
        if spans is not None:
            word_ranges.extend((base + lo, base + hi) for lo, hi in spans)
    starts, ends = [lo for lo, _ in word_ranges], [hi for _, hi in word_ranges]
    expanded: list[tuple[int, int]] = []
    for lo, hi in stream.normalized.spans:
        first, stop = bisect_right(ends, lo), bisect_left(starts, hi)
        if first < stop:
            lo, hi = min(lo, starts[first]), max(hi, ends[stop - 1])
        expanded.append((lo, hi))
    return (0, *(i for i in range(1, len(expanded)) if expanded[i - 1][1] <= expanded[i][0]), len(expanded))


def _streams(uploads: list[dict]) -> tuple[list[_Stream], dict[tuple[str, str], tuple[float, float]]]:
    streams: list[_Stream] = []
    hints: dict[tuple[str, str], tuple[float, float]] = {}
    seen_uploads: set[str] = set()
    total_seconds = 0.0
    total_chars = 0
    total_segments = 0
    total_word_chars = 0
    total_words = 0
    for upload in uploads:
        upload_id = upload.get("id")
        if not isinstance(upload_id, str) or not upload_id.strip() or upload_id in seen_uploads:
            raise ValueError("素材 id 必须为非空、唯一字符串。")
        seen_uploads.add(upload_id)
        duration = _finite(upload.get("sec"), "upload.sec")
        if duration <= 0:
            raise ValueError("素材时长必须大于零。")
        total_seconds += duration
        if total_seconds > MAX_TRANSCRIPT_SECONDS:
            raise ValueError("原话搜索的素材总时长不能超过 60 分钟。")
        transcript = upload.get("transcript", [])
        if isinstance(transcript, dict):
            transcript = transcript.get("segments", [])
        if not isinstance(transcript, list):
            raise ValueError("transcript 必须是转写段列表或包含 segments 的对象。")
        total_segments += len(transcript)
        for raw_segment in transcript:
            text = raw_segment.text if isinstance(raw_segment, TranscriptSegment) else raw_segment.get("text", "")
            total_chars += len(text)
            raw_words = raw_segment.words if isinstance(raw_segment, TranscriptSegment) else raw_segment.get("words", [])
            if raw_words is not None:
                total_words += len(raw_words)
                if isinstance(raw_words, list):
                    for word in raw_words:
                        token = word.w if isinstance(word, Word) else word.get("w", "") if isinstance(word, dict) else ""
                        total_word_chars += len(token) if isinstance(token, str) else 0
            if total_words > MAX_TRANSCRIPT_CHARS or total_word_chars > MAX_TRANSCRIPT_CHARS:
                raise ValueError("词时间条目过多。")
        if total_chars > MAX_TRANSCRIPT_CHARS or total_segments > MAX_TRANSCRIPT_CHARS:
            raise ValueError("转写总字符/段数不能超过 200000。")
        segments = sorted((TranscriptSegment.model_validate(s) for s in transcript), key=lambda s: (s.start, s.end, s.id))
        quiet = _silences(upload.get("silences"), duration)
        groups: list[list[TranscriptSegment]] = []
        for segment in segments:
            key = (upload_id, segment.id)
            if key in hints or segment.end > duration:
                raise ValueError("转写段 id 重复或时间超出原素材。")
            hints[key] = (segment.start, segment.end)
            previous = groups[-1][-1] if groups else None
            if previous is None or not segment.speaker_id.strip() or segment.speaker_id != previous.speaker_id or not (
                0 <= segment.start - previous.end <= LIMITS["cross_segment_max_gap_seconds"]
            ):
                groups.append([])
            groups[-1].append(segment)
        for group in groups:
            text = "\n".join(s.text for s in group)
            ranges: list[tuple[int, int]] = []
            maps: list[list[tuple[int, int]] | None] = []
            cursor = 0
            for segment in group:
                ranges.append((cursor, cursor + len(segment.text)))
                maps.append(_word_spans(segment.text, segment.words, segment.start, segment.end))
                cursor += len(segment.text) + 1
            stream = _Stream(upload_id, duration, group, segments, text, ranges, maps, _normalize_with_mapping(text), quiet)
            stream.boundaries = _search_boundaries(stream)
            streams.append(stream)
    return streams, hints


def _take_id(upload_id: str, start: float, end: float, speaker_id: str) -> str:
    # Deliberately excludes file names, paths, labels, score and sentence order.
    identity = ["quote-v1", upload_id, float(start).hex(), float(end).hex(), speaker_id]
    return "take_" + hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()


@dataclass(slots=True)
class _Candidate:
    take: QuoteTake
    core_start: float
    core_end: float
    pause: float


def _candidate(
    stream: _Stream, query: str, a: int, b: int,
    query_edges: tuple[str, str] = ("", ""),
) -> _Candidate | None:
    lo, hi = stream.normalized.spans[a][0], stream.normalized.spans[b - 1][1]
    # Search ignores discourse fillers, but a requested edge still needs its
    # original source letters and word clocks. Restore only an adjacent literal
    # edge in this same continuous speaker stream, never a remote occurrence or
    # an invented query word. Retained neighbouring characters bound the gaps.
    prefix, suffix = query_edges
    if prefix:
        left = stream.normalized.spans[a - 1][1] if a else 0
        gap = _literal(stream.text[left:lo])
        if gap.text.endswith(prefix):
            lo = left + gap.spans[-len(prefix)][0]
    if suffix:
        right = stream.normalized.spans[b][0] if b < len(stream.normalized.spans) else len(stream.text)
        gap = _literal(stream.text[hi:right])
        if gap.text.startswith(suffix):
            hi += gap.spans[len(suffix) - 1][1]
    first = max(0, bisect_right(stream.ranges, (lo, math.inf)) - 1)
    stop = bisect_left(stream.ranges, (hi, -math.inf))
    involved = [i for i in range(first, stop) if stream.ranges[i][1] > lo]
    if not involved:
        return None
    chosen = [stream.segments[i] for i in involved]
    words: list[Word] = []
    precision: Literal["word", "segment"] = "segment"
    core_start, core_end = chosen[0].start, chosen[-1].end
    spoken = "\n".join(s.text for s in chosen)
    matched = stream.text[lo:hi]
    all_cues: list[Word] = []
    if all(stream.word_ranges[i] is not None for i in involved):
        selected_words: list[tuple[Word, int, int]] = []
        for i in involved:
            segment = stream.segments[i]
            base = stream.ranges[i][0]
            spans = cast(list[tuple[int, int]], stream.word_ranges[i])
            for j, (left, right) in enumerate(spans):
                all_cues.append(segment.words[j])
                if base + left < hi and base + right > lo:
                    left, right = _word_text_range(segment.text, spans, j, j)
                    selected_words.append((segment.words[j], base + left, base + right))
        if selected_words:
            first, last = selected_words[0], selected_words[-1]
            core_start, core_end = first[0].s, last[0].e
            words = [word for word in all_cues if word.s >= core_start and word.e <= core_end]
            spoken = matched = stream.text[first[1]:last[2]]
            precision = "word"
    score = _similarity(query, normalize_text(matched))
    # Neighbour cues from the adjacent segments also prevent handles admitting
    # a new word. Incomplete timing elsewhere cannot justify extending into it.
    start, end = core_start, core_end
    if precision == "word":
        safe_left, safe_right = 0.0, stream.duration
        chosen_ids = {s.id for s in chosen}
        for segment in stream.all_segments:
            if segment.id in chosen_ids:
                continue
            if segment.end <= core_start:
                safe_left = max(safe_left, segment.end)
            elif segment.start >= core_end:
                safe_right = min(safe_right, segment.start)
            else:
                # Overlapping voices cannot justify extending either handle.
                safe_left, safe_right = core_start, core_end
        start, end = refine_cut(all_cues, core_start, core_end, stream.duration, stream.silences)
        start, end = max(start, safe_left), min(end, safe_right)
    snrs = [s.snr_db for s in chosen]
    snr = min(cast(list[float], snrs)) if all(value is not None for value in snrs) else None
    pause = sum(max(0.0, right.s - left.e) for left, right in zip(words, words[1:])) if words else sum(
        max(0.0, right.start - left.end) for left, right in zip(chosen, chosen[1:])
    )
    take = QuoteTake(
        take_id=_take_id(stream.upload_id, start, end, chosen[0].speaker_id),
        upload_id=stream.upload_id, start=start, end=end,
        speaker_id=chosen[0].speaker_id, asr_text=spoken, matched_text=matched,
        score=score, snr_db=snr, words=words, precision=precision,
        segment_ids=[s.id for s in chosen],
    )
    return _Candidate(take, core_start, core_end, pause)


def _speaker_matches(hint: str, speaker_id: str, speakers: dict[str, Speaker]) -> bool:
    hint = _literal(hint).text
    if not hint:
        return False
    if hint == _literal(speaker_id).text:
        return True
    speaker = speakers.get(speaker_id)
    return speaker is not None and hint in {
        _literal(speaker.name).text, _literal(speaker.name + speaker.title).text,
        _literal(speaker.auto_label).text,
    }


def _same_occurrence(left: _Candidate, right: _Candidate) -> bool:
    overlap = min(left.core_end, right.core_end) - max(left.core_start, right.core_start)
    shorter = min(left.core_end - left.core_start, right.core_end - right.core_start)
    return left.take.upload_id == right.take.upload_id and overlap > 0 and overlap / shorter >= 0.5


def _same_speaker(left: QuoteTake, right: QuoteTake) -> bool:
    """Unknown identity is local to one upload AND one segment, never a person."""
    if left.speaker_id.strip():
        return left.speaker_id == right.speaker_id
    return (not right.speaker_id.strip() and left.upload_id == right.upload_id
            and len(left.segment_ids) == 1 and left.segment_ids == right.segment_ids)


def _region_stream(stream: _Stream, lo: float, hi: float) -> _Stream | None:
    """Physically slice search text using evidence, never interpolate timestamps."""
    ranges: list[tuple[int, int]] = []
    for segment, (base, stop), words in zip(stream.segments, stream.ranges, stream.word_ranges):
        if segment.end < lo or segment.start > hi:
            continue
        if words is None:
            ranges.append((base, stop))
        else:
            ranges.extend((base + left, base + right)
                          for word, (left, right) in zip(segment.words, words)
                          if word.e >= lo and word.s <= hi)
    if not ranges:
        return None
    left, right = ranges[0][0], ranges[-1][1]
    spans = stream.normalized.spans
    a = bisect_left(spans, (left, -1))
    b = bisect_left(spans, (right, -1))
    # Snap outwards to indivisible words/numbers, preserving their source map.
    a = stream.boundaries[max(0, bisect_right(stream.boundaries, a) - 1)]
    b = stream.boundaries[min(len(stream.boundaries) - 1, bisect_left(stream.boundaries, b))]
    if a >= b:
        return None
    return _Stream(stream.upload_id, stream.duration, stream.segments, stream.all_segments,
                   stream.text, stream.ranges, stream.word_ranges,
                   _Normalized(stream.normalized.text[a:b], spans[a:b]), stream.silences,
                   tuple(point - a for point in stream.boundaries if a <= point <= b))


def _stream_candidates(stream: _Stream, query: str, budget: _SearchBudget) -> list[_Candidate]:
    reference = stream.normalized.text
    normalized_query = _normalize_with_mapping(query)
    if not reference or not normalized_query.text:
        return []
    query_edges = (
        _literal(query[:normalized_query.spans[0][0]]).text,
        _literal(query[normalized_query.spans[-1][1]:]).text,
    )
    query = normalized_query.text
    # With no word evidence a single segment is ONE physical take. An exact
    # legal match proves its maximum score; all other spans map to that take.
    if len(stream.segments) == 1 and stream.word_ranges[0] is None:
        start = reference.find(query)
        points = set(stream.boundaries)
        while start >= 0:
            if start in points and start + len(query) in points:
                candidate = _candidate(stream, query, start, start + len(query), query_edges)
                if candidate is not None and candidate.take.score == 1:
                    return [candidate]
            start = reference.find(query, start + 1)
    spans = _local_spans(query, reference, stream.boundaries, budget=budget,
                        best_only=len(stream.segments) == 1 and stream.word_ranges[0] is None)
    # Segment-precision candidates with the same source segments share trim,
    # identity and SNR. Materialize only the best lexical span for that take.
    # Word-precision spans are distinct: never erase an imperfect legal word
    # match because a better substring lies inside another indivisible word.
    best: dict[tuple[int, int], tuple[int, int, float]] = {}
    for a, b, score in spans:
        lo, hi = stream.normalized.spans[a][0], stream.normalized.spans[b - 1][1]
        first = max(0, bisect_right(stream.ranges, (lo, math.inf)) - 1)
        stop = bisect_left(stream.ranges, (hi, -math.inf))
        key = (a, b) if all(w is not None for w in stream.word_ranges[first:stop]) else (-first - 1, -stop - 1)
        # Several lexical spans can map to the SAME untimed physical take.
        # Keep the most complete query evidence on a score tie; otherwise an
        # earlier partial-containment span can hide the exact cross-segment one.
        previous = best.get(key)
        evidence_rank = lambda left, right: (abs(right - left - len(query)),
                                             _distance(query, reference[left:right]), left, right)
        if previous is None or score > previous[2] or (
            score == previous[2] and evidence_rank(a, b) < evidence_rank(previous[0], previous[1])
        ):
            best[key] = (a, b, score)
    result: list[_Candidate] = []
    for a, b, _ in best.values():
        # Candidate validation walks source text/cues and neighbouring segments;
        # many exact word starts must not evade the shared bound on that work.
        budget.consume(len(stream.text) + len(stream.all_segments))
        candidate = _candidate(stream, query, a, b, query_edges)
        if candidate is not None:
            result.append(candidate)
    return result


def align_quotes(
    sentences: list[SentenceInput], uploads: list[dict], speakers: list[Speaker] | None = None
) -> list[dict]:
    """Return one row per input; never substitute TTS or unrelated source media.

    Among candidates >= .6, prefer an explicitly supplied speaker identity,
    then the hint segment's +/-5 s region, lexical score, full contiguous query
    evidence in the actual take, known SNR, precision and shorter internal pauses.
    Coverage is a rank key, never a score boost or new word boundary. On failure source and
    ALL source flat fields are None; score is the actual best candidate score.
    Up to five non-overlapping alternatives >= .5 belong to the same known speaker.
    Unknown identities never imply a shared speaker. Local hint search is
    physically sliced first; a valid local speaker match avoids global fuzzy
    search. In that case alternatives are local, not an exhaustive global list.
    Expensive fallback fails explicitly rather than reporting partial scores.
    """
    inputs = [SentenceInput.model_validate(sentence) for sentence in sentences]
    if sum(len(sentence.text) for sentence in inputs) > LIMITS["script_max_chars"]:
        raise ValueError("稿子不能超过 8000 字符。")
    if len({sentence.idx for sentence in inputs}) != len(inputs):
        raise ValueError("句子 idx 不能重复。")
    people = [Speaker.model_validate(speaker) for speaker in speakers or []]
    if len({speaker.id for speaker in people}) != len(people):
        raise ValueError("说话人 id 不能重复。")
    people_by_id = {speaker.id: speaker for speaker in people}
    streams, hint_ranges = _streams(uploads)
    cache: dict[tuple, list[_Candidate]] = {}
    budget = _SearchBudget()

    def search(query: str, hint: SourceHint | None, region: tuple[float, float] | None,
               speaker_hint: str = "", only_speaker: bool = False) -> list[_Candidate]:
        key = (query, hint.upload_id if hint and region else None, region,
               speaker_hint if only_speaker else None)
        if key not in cache:
            candidates: dict[str, _Candidate] = {}
            for stream in streams:
                if only_speaker and not _speaker_matches(speaker_hint, stream.segments[0].speaker_id, people_by_id):
                    continue
                selected = stream
                if region and hint:
                    if stream.upload_id != hint.upload_id:
                        continue
                    selected = _region_stream(stream, region[0] - LIMITS["source_hint_radius_seconds"],
                                              region[1] + LIMITS["source_hint_radius_seconds"])
                    if selected is None:
                        continue
                for candidate in _stream_candidates(selected, query, budget):
                    identity = candidate.take.take_id
                    if identity not in candidates or candidate.take.score > candidates[identity].take.score:
                        candidates[identity] = candidate
            cache[key] = list(candidates.values())
        return cache[key]

    rows: list[dict] = []
    for sentence in inputs:
        row: dict[str, Any] = {
            "idx": sentence.idx, "kind": sentence.kind, "source": None,
            "alt_takes": [], "score": 0.0, "upload_id": None, "start": None,
            "end": None, "speaker_id": None, "asr_text": None,
        }
        rows.append(row)
        # Keep the raw query in the search/cache key: identical fuzzy keys can
        # require different literal filler edges and therefore different takes.
        query = sentence.text
        if sentence.kind != "quote" or not normalize_text(query):
            continue
        hint = sentence.source_hint
        region = hint_ranges.get((hint.upload_id, hint.seg_id)) if hint else None
        candidates_for_row = search(query, hint, region, sentence.speaker_hint, bool(sentence.speaker_hint))
        if not any(c.take.score >= MATCH_LOW for c in candidates_for_row) and region:
            candidates_for_row = search(query, None, None, sentence.speaker_hint, bool(sentence.speaker_hint))
        if not any(c.take.score >= MATCH_LOW for c in candidates_for_row) and sentence.speaker_hint:
            candidates_for_row = search(query, hint, region)
            if not any(c.take.score >= MATCH_LOW for c in candidates_for_row) and region:
                candidates_for_row = search(query, None, None)
        if not candidates_for_row:
            continue
        row["score"] = max(candidate.take.score for candidate in candidates_for_row)

        def rank(candidate: _Candidate) -> tuple:
            take = candidate.take
            near = bool(region and hint and take.upload_id == hint.upload_id
                        and candidate.core_start <= region[1] + LIMITS["source_hint_radius_seconds"]
                        and candidate.core_end >= region[0] - LIMITS["source_hint_radius_seconds"])
            speaker = _speaker_matches(sentence.speaker_hint, take.speaker_id, people_by_id)
            return (not speaker, not near, -take.score,
                    # Containment scores also equal 1 for tiny fragments. Prefer
                    # the whole literal query in observed ASR before acoustics;
                    # filler-stripped similarity alone cannot prove coverage.
                    not quote_text_is_contiguous(query, take),
                    -take.snr_db if take.snr_db is not None else math.inf,
                    take.precision != "word",
                    abs(len(normalize_text(take.matched_text)) - len(normalize_text(query))),
                    _distance(_literal(query).text, _literal(take.matched_text).text),
                    candidate.pause,
                    take.upload_id, take.start, take.end, take.take_id)

        eligible = sorted((candidate for candidate in candidates_for_row if candidate.take.score >= MATCH_LOW), key=rank)
        if not eligible:
            continue
        chosen = eligible[0]
        row["source"] = chosen.take.model_dump(mode="json")
        row["score"] = chosen.take.score
        for key in ("upload_id", "start", "end", "speaker_id", "asr_text"):
            row[key] = getattr(chosen.take, key)
        distinct = [chosen]
        alternatives = sorted((candidate for candidate in candidates_for_row
                               if candidate is not chosen and candidate.take.score >= LIMITS["alternative_match_min"]), key=rank)
        for alternative in alternatives:
            if not chosen.take.speaker_id.strip() or not _same_speaker(chosen.take, alternative.take):
                continue
            if any(_same_occurrence(alternative, other) for other in distinct):
                continue
            distinct.append(alternative)
            row["alt_takes"].append(alternative.take.model_dump(mode="json"))
            if len(row["alt_takes"]) == LIMITS["alternative_takes"]:
                break
    return rows


def validate_quote_trim(source: QuoteTake, start: float, end: float) -> dict:
    """Validate a contiguous shrink; return a new QuoteTake-shaped dictionary.

    Raises ValueError for expansion, <1 s, word cuts or unverified word timing.
    No replacement text/word list is accepted, so inserting or deleting middle
    words cannot be expressed. The original score is retained (no new match is
    claimed); asr_text/words are a contiguous slice of the original evidence.
    Segment precision permits only an unchanged interval.
    """
    source = QuoteTake.model_validate(source)
    start, end = _finite(start, "start"), _finite(end, "end")
    if not source.start <= start < end <= source.end:
        raise ValueError("原声只能缩小原来的连续区间。")
    if _clock_delta(end, start) < LIMITS["quote_min_seconds"]:
        raise ValueError("原声不能短于 1 秒。")
    if start == source.start and end == source.end:
        return source.model_dump(mode="json")
    spans = _word_spans(source.asr_text, source.words, source.start, source.end)
    if source.precision != "word" or spans is None:
        raise ValueError("只有整段时间证据，不能进行词级裁剪；需重新取得真实词时间。")
    if any(word.s < start < word.e or word.s < end < word.e for word in source.words):
        raise ValueError("剪切点不能落在词内。")
    retained = [i for i, word in enumerate(source.words) if word.s >= start and word.e <= end]
    if not retained:
        raise ValueError("剪切后没有保留真实词。")
    first, last = retained[0], retained[-1]
    lo, hi = _word_text_range(source.asr_text, spans, first, last)
    result = source.model_dump()
    result.update(
        start=start, end=end, asr_text=source.asr_text[lo:hi],
        matched_text=source.asr_text[lo:hi], words=source.words[first:last + 1],
        take_id=_take_id(source.upload_id, start, end, source.speaker_id),
    )
    return QuoteTake.model_validate(result).model_dump(mode="json")


def _row_id(row: dict, position: int) -> int:
    value = row.get("idx", row.get("id", row.get("sentence_id", position)))
    return value if type(value) is int and value >= 0 else position


def _source_data(row: dict) -> dict | None:
    source = row.get("source", row)
    if row.get("missing") or source is None:
        return None
    if isinstance(source, QuoteTake):
        return {**row, **source.model_dump()}
    if isinstance(source, str):
        return {**row, "upload_id": source}
    if isinstance(source, dict):
        return {**row, **source}
    return None


def calculate_jumpcuts(rows: list[dict], cover: Cover = "broll") -> list[JumpCut]:
    """Compare only adjacent quote rows, never across intervening narration.

    after_row identifies the PRECEDING row (numeric idx/id, else its position).
    A nonnegative gap <=.5 s in one file is continuous; any overlap/reversal is
    a jump. B-roll requires a real cover_shot on the following row; otherwise
    return zoom/downgraded=True rather than inventing a shot allocation.
    """
    if cover not in ("broll", "zoom", "hard"):
        raise ValueError("cover 必须为 broll、zoom 或 hard。")
    cuts: list[JumpCut] = []
    for position, (before, after) in enumerate(zip(rows, rows[1:])):
        if before.get("kind") != "quote" or after.get("kind") != "quote":
            continue
        left, right = _source_data(before), _source_data(after)
        if not left or not right or not left.get("upload_id") or not right.get("upload_id"):
            continue
        a, b = _finite(left.get("start"), "start"), _finite(left.get("end"), "end")
        c, d = _finite(right.get("start"), "start"), _finite(right.get("end"), "end")
        if not (0 <= a < b and 0 <= c < d):
            raise ValueError("原声时间范围无效。")
        if left["upload_id"] == right["upload_id"] and 0 <= _clock_delta(c, b) <= LIMITS["jump_continuity_seconds"]:
            continue
        shot = after.get("cover_shot") if cover == "broll" else None
        if shot is not None and (type(shot) is not int or shot < 0):
            raise ValueError("cover_shot 必须是有效镜头编号。")
        downgraded = cover == "broll" and shot is None
        cuts.append(JumpCut(after_row=_row_id(before, position), cover="zoom" if downgraded else cover, shot=shot, downgraded=downgraded))
    return cuts


def evaluate_mode_checks(
    rows: list[dict], speakers: list[Speaker], mode: Mode, preferences: dict,
    gate_mode: Literal["warn", "block"] = "warn", jumpcuts: list[dict] | None = None,
) -> list[dict]:
    """Mode-only issues in the existing QualityIssue shape plus level/action.

    severity remains error/warning for compatibility; level 2 is informational.
    gate_mode controls the caller's workflow, not the truth of a finding: errors
    remain errors in warn mode too. Existing general/rendering QC is not replaced.
    """
    _mode(mode)
    if gate_mode not in ("warn", "block"):
        raise ValueError("gate_mode 必须为 warn 或 block。")
    if mode == "voiceover":
        return []
    people = {speaker.id: Speaker.model_validate(speaker) for speaker in speakers}
    issues: list[dict] = []
    unnamed: set[str] = set()
    jump_rows = list(rows)

    def add(code: str, sentence_id: int | None, *, level: int | None = None, **values: Any) -> None:
        template = check_definition(code)
        actual_level = template["level"] if level is None else level
        issues.append({
            "code": code, "severity": "error" if actual_level == 0 else "warning",
            # Readable numbers in messages: 0.84, not 0.8400000000000003.
            "message": template["message"].format(**{key: format(round(value, 2), "g") if isinstance(value, float) else value
                                                     for key, value in values.items()}), "sentence_id": sentence_id,
            "level": actual_level, "action": template["action"],
        })

    for position, row in enumerate(rows):
        if row.get("kind") != "quote":
            continue
        sentence_id = _row_id(row, position)
        values: dict[str, Any] = {"sentence": position + 1}
        source = _source_data(row)
        score_value = source.get("score", row.get("score", 0.0)) if source else row.get("score", 0.0)
        try:
            score = _finite(score_value, "score")
            if not 0 <= score <= 1:
                raise ValueError("score 超出范围。")
        except ValueError:
            score = 0.0
        if not source or not source.get("upload_id") or not source.get("asr_text") or score < MATCH_LOW:
            add("QUOTE_NOT_FOUND", sentence_id, **values, score=score, advice="改成旁白、" if mode == "mixed" else "")
            jump_rows[position] = {**row, "source": None}
            continue
        if score < MATCH_OK:
            add("QUOTE_MATCH_LOW", sentence_id, **values, score=score)
        try:
            start, end = _finite(source.get("start"), "start"), _finite(source.get("end"), "end")
            if not 0 <= start < end:
                raise ValueError("范围无效。")
        except ValueError:
            add("QUOTE_INVALID_RANGE", sentence_id, **values)
            jump_rows[position] = {**row, "source": None}
            continue
        seconds = _clock_delta(end, start)
        if seconds < LIMITS["quote_min_seconds"]:
            add("QUOTE_TOO_SHORT", sentence_id, **values, seconds=seconds)
        if seconds > LIMITS["quote_warn_seconds"]:
            add("QUOTE_TOO_LONG", sentence_id, level=0 if seconds > LIMITS["quote_max_seconds"] else 1, **values, seconds=seconds)
        snr = source.get("snr_db", row.get("snr_db"))
        if snr is not None and _finite(snr, "snr_db") < LIMITS["quote_snr_min_db"]:
            add("QUOTE_AUDIO_NOISY", sentence_id, **values, snr=snr)
        text = row.get("text", row.get("sentence", row.get("s", "")))
        expected, actual = normalize_text(text), normalize_text(source["asr_text"])
        difference = _distance(expected, actual) / max(len(expected), len(actual), 1)
        contiguous = quote_text_is_contiguous(text, source)
        negation_changed = any(expected.count(token) != actual.count(token) for token in ("不", "没", "未", "无", "非", "否", "别", "勿", "莫"))
        if text and (difference > LIMITS["quote_text_difference"] or
                     (not contiguous and (mode == "original" or negation_changed))):
            add("QUOTE_TEXT_DIFFERS", sentence_id, **values, difference=difference)
        if mode == "original" and not contiguous:
            add("QUOTE_NONCONTIGUOUS", sentence_id, **values)
        speaker_id = source.get("speaker_id", "")
        key = speaker_id or f"unknown:{sentence_id}"
        person = people.get(speaker_id)
        if preferences.get("lower_third") is True and key not in unnamed and (person is None or not person.name.strip()):
            unnamed.add(key)
            add("SPEAKER_UNNAMED", sentence_id, **values, speaker=speaker_id or position + 1)
    if mode in ("mixed", "original"):
        cuts = [JumpCut.model_validate(cut) for cut in jumpcuts] if jumpcuts is not None else calculate_jumpcuts(jump_rows, preferences.get("jump_cut_cover", "broll"))
        affected = [cut for cut in cuts if cut.cover != "broll"]
        if affected:
            add("JUMP_CUT_UNCOVERED", affected[0].after_row, count=len(affected))
    if mode == "mixed" and rows and all(row.get("kind") == "quote" for row in rows):
        add("MIXED_NO_NARRATION", None)
    return sorted(issues, key=lambda issue: (issue["level"], issue["sentence_id"] if issue["sentence_id"] is not None else math.inf, issue["code"]))


__all__ = [
    "Mode", "Kind", "TerminalPunctuation", "PlanRecommendation", "SourceHint", "SentenceInput", "Word", "TranscriptSegment",
    "Speaker", "QuoteTake", "JumpCut", "MODE_RULES", "PACING_CPM", "MATCH_OK",
    "MATCH_LOW", "STAGE_WEIGHTS", "parse_sentences", "normalize_text",
    "align_quotes", "refine_cut", "calculate_jumpcuts", "validate_quote_trim",
    "evaluate_mode_checks", "quote_text_is_contiguous", "RULES_VERSION",
    "similarity", "score_status", "facts_of", "split_text", "parse_line",
    "strip_leading_fillers", "canonical_quote_caption", "recommend_plan", "check_definition", "sentence_needs",
]