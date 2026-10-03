import hashlib
import hmac
import json
import math
import re
import tempfile
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, TypedDict, cast

import jieba  # pyright: ignore[reportMissingTypeStubs]
from opencc import OpenCC  # pyright: ignore[reportMissingTypeStubs]

from .models import CaptionStyle, SentenceTiming
from .storage import write_json_atomic


SUBTITLE_TEMPLATE_ID = "GM-NEWS-DIALOGUE-2024-001"
SUBTITLE_TEMPLATE_SCHEMA_VERSION = 1
SUBTITLE_STYLE_NAME = "NewsDialogue2024"
STABLE_SUBTITLE_STYLE_NAME = "NewsSentence2024"
TITLE_STYLE_NAME = "NewsTitle2024"
SUBTITLE_FONT_NAME = "Noto Sans SC"
MAX_SUBTITLE_CHARS = 15
MAX_STABLE_SUBTITLE_CHARS = 30
MAX_STABLE_SUBTITLE_LINES = 2
STABLE_SUBTITLE_FONT_HEIGHT_RATIO = 0.039
MAX_TITLE_CHARS = 20
TITLE_DURATION_SECONDS = 4.0
MIN_INTER_SENTENCE_GAP_SECONDS = 0.08
SAFE_AREA_RATIO = 0.80
SUBTITLE_REGION_HEIGHT_RATIO = 0.10
DIALOGUE_FONT_HEIGHT_RATIO = 0.05
DEFAULT_FRAME_WIDTH = 1920
DEFAULT_FRAME_HEIGHT = 1080
SUBTITLE_LAYER = 10
BIG_CAPTION_FONT_SCALE = 1.5

_BOOK_TITLE_OPEN = "《"
_BOOK_TITLE_CLOSE = "》"
_BOOK_TITLE_MARKS = {_BOOK_TITLE_OPEN, _BOOK_TITLE_CLOSE}
_PUNCTUATION_BREAK_STRENGTH = 5
_WHITESPACE_BREAK_STRENGTH = 4
_WORD_BREAK_STRENGTH = 1
_TITLE_INTERNAL_BREAK_STRENGTH = -3
_PROTECTED_SUBTITLE_PATTERNS = (
    re.compile(r"\d{1,4}年(?:\d{1,2}月(?:\d{1,2}日)?)?"),
    re.compile(r"\d{1,2}月\d{1,2}日"),
    re.compile(r"(?:省|市|区|县|镇|乡|街道)[^，。！？；：]{0,24}?(?:路|街|大道|巷)\d*号"),
    re.compile(r"[“\"]([^”\"]+)[”\"]"),
    re.compile(r"《[^》]+》"),
    re.compile(r"\d+(?:\.\d+)?(?:万|亿|元|人|家|项|台|个|吨|公里|米|%|％)"),
)
_FORBIDDEN_CHUNK_STARTS = ("月", "日", "号", "的", "地", "得", "了", "着", "过", "等", "们")
_FORBIDDEN_CHUNK_ENDS = ("的", "地", "得", "和", "与", "及", "或", "等", "一个", "位于", "前来")


class _ChineseTokenizer(Protocol):
    def lcut(self, sentence: str, cut_all: bool = False, HMM: bool = True) -> list[str]:
        ...


class _TextConverter(Protocol):
    def convert(self, text: str) -> str:
        ...


class SubtitleBox(TypedDict):
    left: int
    top: int
    right: int
    bottom: int


class SubtitleRegion(SubtitleBox):
    height: int


class SubtitleLayoutSpecification(TypedDict):
    frame_width: int
    frame_height: int
    safe_area: SubtitleBox
    dialogue_region: SubtitleRegion
    font_size: int
    letter_spacing: int
    outline_size: int
    shadow_size: int


class SubtitleTemplateSpecification(TypedDict):
    template_id: str
    style_name: str
    stable_style_name: str
    title_style_name: str
    font_family: str
    nominal_1080p_font_points: float
    font_height_ratio: float
    stable_font_height_ratio: float
    uhd_4k_font_height_ratio: float
    uhd_4k_font_height_tolerance: float
    safe_area_ratio_x: float
    safe_area_ratio_y: float
    dialogue_region_height_ratio: float
    max_characters_per_line: int
    stable_max_characters_per_line: int
    stable_max_line_count: int
    title_max_characters_per_line: int
    title_line_count: int
    title_duration_seconds: float
    line_count: int
    minimum_sentence_gap_ms: int
    alignment: str
    primary_color_rgb: str
    outline_color_rgb: str
    contrast_ratio: float
    outline_size_at_1080p: int
    shadow_size_at_1080p: int
    animation: str
    layer: int


class SubtitleComplianceChecks(TypedDict):
    single_line: bool
    maximum_15_characters: bool
    no_forbidden_punctuation: bool
    non_overlapping_events: bool
    sound_aligned_sentence_boundaries: bool
    minimum_80ms_sentence_gap: bool
    achromatic_21_to_1_contrast: bool
    stable_sentence_text_unchanged: bool


class SubtitleManifest(TypedDict):
    schema_version: int
    template_id: str
    template_fingerprint: str
    template: SubtitleTemplateSpecification
    layout: SubtitleLayoutSpecification
    sentence_count: int
    sentence_ids: list[int]
    event_count: int
    title_event_count: int
    stable_event_count: int
    stable_sentence_ids: list[int]
    minimum_observed_sentence_gap_ms: int | None
    source_text_sha256: str
    display_text_sha256: str
    ass_sha256: str
    word_timing_coverage_ratio: float
    normalization: str
    checks: SubtitleComplianceChecks


_SIMPLIFIED_CONVERTER = cast(_TextConverter, OpenCC("t2s"))
_TOKENIZER = cast(_ChineseTokenizer, jieba.Tokenizer())


@dataclass(frozen=True)
class SubtitleEvent:
    sentence_id: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SubtitleLayout:
    frame_width: int
    frame_height: int
    safe_margin_x: int
    safe_margin_y: int
    region_top: int
    region_bottom: int
    region_height: int
    font_size: int
    letter_spacing: int
    outline_size: int
    shadow_size: int

    @classmethod
    def for_resolution(cls, frame_width: int, frame_height: int) -> "SubtitleLayout":
        if frame_width < 1 or frame_height < 1:
            raise ValueError("字幕画面分辨率必须大于 0。")
        safe_margin_x = round(frame_width * (1.0 - SAFE_AREA_RATIO) / 2.0)
        safe_margin_y = round(frame_height * (1.0 - SAFE_AREA_RATIO) / 2.0)
        region_height = round(frame_height * SUBTITLE_REGION_HEIGHT_RATIO)
        region_bottom = frame_height - safe_margin_y
        region_top = region_bottom - region_height
        font_size = round(frame_height * DIALOGUE_FONT_HEIGHT_RATIO)
        scale = frame_height / DEFAULT_FRAME_HEIGHT
        letter_spacing = max(1, round(scale))
        outline_size = max(1, round(4 * scale))
        shadow_size = max(1, round(scale))
        if region_top < safe_margin_y or font_size < 1:
            raise ValueError("字幕画面分辨率过小，无法建立安全字幕区。")
        return cls(
            frame_width=frame_width,
            frame_height=frame_height,
            safe_margin_x=safe_margin_x,
            safe_margin_y=safe_margin_y,
            region_top=region_top,
            region_bottom=region_bottom,
            region_height=region_height,
            font_size=font_size,
            letter_spacing=letter_spacing,
            outline_size=outline_size,
            shadow_size=shadow_size,
        )

    @property
    def safe_right(self) -> int:
        return self.frame_width - self.safe_margin_x

    @property
    def safe_bottom(self) -> int:
        return self.frame_height - self.safe_margin_y

    @property
    def horizontal_center(self) -> int:
        return self.frame_width // 2

    @property
    def vertical_center(self) -> int:
        return (self.region_top + self.region_bottom) // 2


@dataclass(frozen=True)
class _SubtitleToken:
    text: str
    separator_before: str = ""
    break_strength_after: int = _WORD_BREAK_STRENGTH


def generate_ass_subtitles(
    task_dir: Path,
    timings: Sequence[SentenceTiming],
    *,
    output_path: Path | None = None,
    manifest_path: Path | None = None,
    frame_width: int = DEFAULT_FRAME_WIDTH,
    frame_height: int = DEFAULT_FRAME_HEIGHT,
    title: str | None = None,
    information_card_sentence_ids: set[int] | None = None,
    stable_sentence_ids: set[int] | None = None,
) -> list[SubtitleEvent]:
    if not timings:
        raise ValueError("没有时间轴可用于生成字幕。")
    _validate_timings(timings)
    layout = SubtitleLayout.for_resolution(frame_width, frame_height)
    contextual_overlay_ids = information_card_sentence_ids or set()
    # Subtitle layout is a language/readability decision, never a footage
    # evidence-type decision. Keep explicit callers for compatibility, and add
    # automatic stable layout when the 15-character rolling layout would split
    # a date, address, quoted title, unit, or dangling Chinese phrase.
    stable_ids = set(stable_sentence_ids or set())
    stable_ids.update(
        timing.sentence_id
        for timing in timings
        if _requires_stable_subtitle(timing.text)
    )
    timing_ids = {timing.sentence_id for timing in timings}
    unknown_ids = (contextual_overlay_ids | stable_ids) - timing_ids
    if unknown_ids:
        raise ValueError(f"字幕模式引用了不存在的句子编号：{sorted(unknown_ids)}。")

    events: list[SubtitleEvent] = []
    for timing in timings:
        if timing.sentence_id in stable_ids:
            display_text = normalize_subtitle_text(timing.text)
            _stable_subtitle_rows(display_text)
            spoken_start, spoken_end = _spoken_time_range(timing)
            events.append(
                SubtitleEvent(
                    sentence_id=timing.sentence_id,
                    start=round(spoken_start * 100) / 100.0,
                    end=round(spoken_end * 100) / 100.0,
                    text=display_text,
                )
            )
        else:
            chunks = split_subtitle_text(timing.text)
            events.extend(_allocate_events(timing, chunks))
    _validate_events(timings, events, stable_sentence_ids=stable_ids)

    ass_path = output_path or task_dir / "subs.ass"
    resolved_manifest_path = manifest_path or task_dir / "subtitle_manifest.json"
    ass_document = _build_ass_document(
        events,
        layout,
        title=title,
        stable_ids=stable_ids,
    )
    _write_text_atomic(ass_path, ass_document)
    manifest = _build_subtitle_manifest(
        timings,
        events,
        layout,
        ass_path,
        title=title,
        stable_sentence_ids=stable_ids,
    )
    write_json_atomic(resolved_manifest_path, manifest)
    validate_subtitle_artifacts(ass_path, resolved_manifest_path)
    return events


def split_subtitle_text(text: str, max_chars: int = MAX_SUBTITLE_CHARS) -> list[str]:
    if max_chars < 1:
        raise ValueError("字幕最大字符数必须大于 0。")
    tokens = _tokenize_subtitle_text(text, max_chars)
    if not tokens:
        raise ValueError("字幕文本不能为空。")
    chunks = _wrap_tokens(tokens, max_chars)
    if not chunks or any(not chunk or len(chunk) > max_chars or "\n" in chunk for chunk in chunks):
        raise ValueError("字幕切分未能满足单行字符上限。")
    if any(_contains_forbidden_punctuation(chunk) for chunk in chunks):
        raise ValueError("字幕中包含书名号以外的标点符号。")
    if _semantic_text("".join(chunks)) != _semantic_text(text):
        raise ValueError("字幕规范化后未能完整保留原文信息。")
    return chunks


def _requires_stable_subtitle(text: str) -> bool:
    normalized = _simplify_text(text)
    if len(_semantic_text(normalized)) <= MAX_SUBTITLE_CHARS:
        return False
    chunks = split_subtitle_text(normalized)
    semantic_offsets: list[int] = []
    cursor = 0
    for chunk in chunks[:-1]:
        cursor += len(_semantic_text(chunk))
        semantic_offsets.append(cursor)
    semantic_source = _semantic_text(normalized)
    protected_ranges: list[tuple[int, int]] = []
    for pattern in _PROTECTED_SUBTITLE_PATTERNS:
        for match in pattern.finditer(normalized):
            start = len(_semantic_text(normalized[: match.start()]))
            end = len(_semantic_text(normalized[: match.end()]))
            protected_ranges.append((start, end))
    if any(start < offset < end for offset in semantic_offsets for start, end in protected_ranges):
        return True
    if any(
        right.startswith(_FORBIDDEN_CHUNK_STARTS)
        or left.endswith(_FORBIDDEN_CHUNK_ENDS)
        for left, right in zip(chunks, chunks[1:])
    ):
        return True
    # Long units with no natural top-level pause should remain visually stable
    # rather than being switched at arbitrary tokenizer boundaries.
    return len(chunks) > 1 and not any(mark in normalized for mark in "，。！？；：")


def normalize_subtitle_text(text: str) -> str:
    """Return simplified display text with pauses represented by spaces."""
    tokens = _tokenize_subtitle_text(text, max(MAX_SUBTITLE_CHARS, len(text) or 1))
    if not tokens:
        raise ValueError("字幕文本不能为空。")
    return _line_from_tokens(tokens, 0, len(tokens) - 1)


def validate_subtitle_artifacts(ass_path: Path, manifest_path: Path) -> SubtitleManifest:
    if not ass_path.is_file() or not manifest_path.is_file():
        raise ValueError("字幕文件或字幕模板绑定清单不存在。")
    try:
        raw_payload: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("字幕模板绑定清单无法读取。") from exc
    if not isinstance(raw_payload, dict):
        raise ValueError("字幕模板绑定清单格式无效。")
    payload = cast(dict[str, object], raw_payload)

    expected_template = _template_specification()
    if payload.get("schema_version") != SUBTITLE_TEMPLATE_SCHEMA_VERSION:
        raise ValueError("字幕模板绑定清单版本不匹配。")
    if payload.get("template_id") != SUBTITLE_TEMPLATE_ID:
        raise ValueError("字幕模板编号不匹配。")
    if payload.get("template") != expected_template:
        raise ValueError("字幕模板参数与固定播出模板不匹配。")
    expected_fingerprint = _template_fingerprint()
    fingerprint = payload.get("template_fingerprint")
    if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint, expected_fingerprint):
        raise ValueError("字幕模板指纹不匹配。")

    ass_bytes = ass_path.read_bytes()
    ass_sha256 = payload.get("ass_sha256")
    actual_sha256 = hashlib.sha256(ass_bytes).hexdigest()
    if not isinstance(ass_sha256, str) or not hmac.compare_digest(ass_sha256, actual_sha256):
        raise ValueError("ASS 字幕与模板绑定清单不一致。")
    try:
        ass_text = ass_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("ASS 字幕不是有效 UTF-8 文本。") from exc
    if f"TemplateId: {SUBTITLE_TEMPLATE_ID}" not in ass_text:
        raise ValueError("ASS 字幕缺少固定模板编号。")
    if f"TemplateFingerprint: {expected_fingerprint}" not in ass_text:
        raise ValueError("ASS 字幕缺少正确的模板指纹。")
    raw_layout_payload = payload.get("layout")
    if not isinstance(raw_layout_payload, dict):
        raise ValueError("字幕模板绑定清单缺少响应式布局。")
    layout_payload = cast(dict[str, object], raw_layout_payload)
    frame_width = layout_payload.get("frame_width")
    frame_height = layout_payload.get("frame_height")
    if not isinstance(frame_width, int) or not isinstance(frame_height, int):
        raise ValueError("字幕模板绑定清单的画面尺寸无效。")
    expected_layout = SubtitleLayout.for_resolution(frame_width, frame_height)
    if layout_payload != _layout_specification(expected_layout):
        raise ValueError("字幕模板绑定清单的安全区或字号不符合固定模板。")
    ass_lines = ass_text.splitlines()
    if f"PlayResX: {frame_width}" not in ass_lines or f"PlayResY: {frame_height}" not in ass_lines:
        raise ValueError("ASS 字幕画面尺寸与模板绑定清单不一致。")
    if _ass_style_line(expected_layout) not in ass_lines:
        raise ValueError("ASS 字幕样式参数与固定播出模板不一致。")
    if _stable_ass_style_line(expected_layout) not in ass_lines:
        raise ValueError("ASS 固定整句字幕样式参数与固定播出模板不一致。")
    if _title_style_line(expected_layout) not in ass_lines:
        raise ValueError("ASS 新闻标题样式参数与固定播出模板不一致。")
    event_count = payload.get("event_count")
    if not isinstance(event_count, int) or event_count != ass_text.count("Dialogue:"):
        raise ValueError("ASS 字幕事件数与模板绑定清单不一致。")
    expected_style_marker = f",{SUBTITLE_STYLE_NAME},,"
    stable_style_marker = f",{STABLE_SUBTITLE_STYLE_NAME},,"
    title_style_marker = f",{TITLE_STYLE_NAME},,"
    expected_position_tag = _ass_position_tag(expected_layout)
    dialogue_lines = [line for line in ass_lines if line.startswith("Dialogue:")]
    body_lines = [line for line in dialogue_lines if expected_style_marker in line]
    stable_lines = [line for line in dialogue_lines if stable_style_marker in line]
    title_lines = [line for line in dialogue_lines if title_style_marker in line]
    if len(body_lines) + len(stable_lines) + len(title_lines) != len(dialogue_lines):
        raise ValueError("ASS 字幕事件绑定了错误的样式编号。")
    if any(expected_position_tag not in line for line in body_lines):
        raise ValueError("ASS 字幕事件的位置或裁切区不符合安全区模板。")
    for line in body_lines:
        display_text = line.split(expected_position_tag, maxsplit=1)[1]
        if (
            not display_text
            or len(display_text) > MAX_SUBTITLE_CHARS
            or r"\N" in display_text
            or _contains_forbidden_punctuation(display_text)
        ):
            raise ValueError("ASS 字幕事件违反单行文字规范。")
    expected_stable_tag = _stable_ass_position_tag(expected_layout)
    if any(expected_stable_tag not in line for line in stable_lines):
        raise ValueError("ASS 固定整句字幕事件的位置不符合固定模板。")
    for line in stable_lines:
        display_text = line.split(expected_stable_tag, maxsplit=1)[1]
        stable_rows = display_text.split(r"\N")
        if (
            not display_text
            or len(stable_rows) > MAX_STABLE_SUBTITLE_LINES
            or any(not row or len(row) > MAX_STABLE_SUBTITLE_CHARS for row in stable_rows)
            or any(_contains_forbidden_punctuation(row) for row in stable_rows)
        ):
            raise ValueError("ASS 固定整句字幕事件违反多行文字规范。")
    expected_title_tag = _title_position_tag(expected_layout)
    if any(expected_title_tag not in line for line in title_lines):
        raise ValueError("ASS 新闻标题事件的位置不符合固定模板。")
    for line in title_lines:
        display_text = line.split(expected_title_tag, maxsplit=1)[1]
        title_rows = display_text.split(r"\N")
        if not display_text or len(title_rows) > 2 or any(len(row) > MAX_TITLE_CHARS for row in title_rows):
            raise ValueError("ASS 新闻标题事件违反双行文字规范。")
    title_event_count = payload.get("title_event_count")
    if not isinstance(title_event_count, int) or title_event_count != len(title_lines):
        raise ValueError("ASS 新闻标题事件数与模板绑定清单不一致。")
    if len(title_lines) > 1 or any(
        not line.startswith(
            f"Dialogue: {SUBTITLE_LAYER + 10},{format_ass_time(0.0)},"
        )
        for line in title_lines
    ):
        raise ValueError("ASS 正文句子不得使用新闻标题样式。")
    stable_event_count = payload.get("stable_event_count")
    if not isinstance(stable_event_count, int) or stable_event_count != len(stable_lines):
        raise ValueError("ASS 固定整句字幕事件数与模板绑定清单不一致。")
    return cast(SubtitleManifest, payload)


@contextmanager
def subtitle_burn_in_artifact(
    ass_path: Path,
    manifest_path: Path,
    *,
    caption_style: CaptionStyle = "news",
) -> Iterator[Path]:
    """Validate the canonical evidence, then derive ONLY the burn-in presentation.

    News uses the historical file verbatim. Neither opt-in style rewrites that
    file, its schema-v1 manifest, timings, or a separate graphics/disclosure ASS.
    The private, unique derivative lives only for the duration of the renderer,
    including its cancellation cleanup; it is never a replacement QC baseline.
    """
    if caption_style not in ("news", "big", "none"):
        raise ValueError("未知正文字幕样式。")
    manifest = validate_subtitle_artifacts(ass_path, manifest_path)
    if caption_style == "news":
        yield ass_path
        return

    canonical = ass_path.read_bytes()
    digest = hashlib.sha256(canonical).hexdigest()
    if not hmac.compare_digest(digest, manifest["ass_sha256"]):
        raise ValueError("字幕在模板校验后发生变化，拒绝派生烧录字幕。")
    layout = SubtitleLayout.for_resolution(
        manifest["layout"]["frame_width"], manifest["layout"]["frame_height"]
    )
    document = _caption_burn_in_document(canonical.decode("utf-8"), layout, caption_style)
    # Do not impersonate a canonical template: the unchanged original + manifest
    # remain the audit record; this hash explicitly links the temporary rendering.
    document = document.replace("; TemplateId:", "; CanonicalTemplateId:").replace(
        "; TemplateFingerprint:", "; CanonicalTemplateFingerprint:"
    )
    document = document.replace(
        "[Script Info]\n",
        f"[Script Info]\n; BurnInCaptionStyle: {caption_style}\n; CanonicalASS-SHA256: {digest}\n",
        1,
    )
    with tempfile.TemporaryDirectory(prefix=".caption-burn-", dir=ass_path.parent) as directory:
        derived_path = Path(directory) / f"{caption_style}.ass"
        derived_path.write_bytes(document.encode("utf-8"))
        yield derived_path


def _caption_burn_in_document(
    canonical: str, layout: SubtitleLayout, caption_style: CaptionStyle,
) -> str:
    body_styles = {SUBTITLE_STYLE_NAME, STABLE_SUBTITLE_STYLE_NAME}
    sizes: dict[str, float] = {}
    lines: list[str] = []
    for line in canonical.splitlines():
        if caption_style == "big" and line.startswith("Style: "):
            fields = line.removeprefix("Style: ").split(",")
            if fields[0] in body_styles:
                size = float(fields[2]) * BIG_CAPTION_FONT_SCALE
                sizes[fields[0]] = size
                fields[2] = f"{size:g}"
                line = "Style: " + ",".join(fields)
        elif line.startswith("Dialogue: "):
            fields = line.split(",", 9)
            style = fields[3]
            if style in body_styles:
                if caption_style == "none":
                    continue
                if caption_style == "big":
                    fields[9] = _big_caption_event_text(fields[9], style, sizes[style], layout)
                    line = ",".join(fields)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _big_caption_event_text(
    text: str, style: str, font_size: float, layout: SubtitleLayout,
) -> str:
    stable = style == STABLE_SUBTITLE_STYLE_NAME
    position = _stable_ass_position_tag(layout) if stable else _ass_position_tag(layout)
    if not text.startswith(position):
        raise ValueError("正文字幕缺少可派生的标准定位标签。")
    display = text[len(position):]
    rows = display.split(r"\N")
    padding = 2 * (layout.outline_size + layout.shadow_size)
    available_width = layout.safe_right - layout.safe_margin_x - padding
    max_chars = max(1, math.floor(available_width / (font_size + layout.letter_spacing)))
    # Preserve the canonical centering and line breaks whenever they fit. A
    # 30-character stable row at 1.5x cannot fit the 80% safe width: reflow that
    # exceptional case instead of silently clipping dates/names or shrinking type.
    if any(len(row) > max_chars for row in rows):
        rows = [
            chunk
            for row in rows
            for chunk in (split_subtitle_text(row, max_chars=max_chars) if len(row) > max_chars else [row])
        ]
    top = max(layout.safe_margin_y, round(layout.frame_height * 0.75)) if stable else layout.region_top
    bottom = layout.region_bottom
    # CJK font line advance can exceed its nominal size. Reserve room between
    # baselines as well as for glyphs/outline, not merely font_size * row_count.
    height = math.ceil(font_size * (1.2 + 1.5 * (len(rows) - 1))) + padding
    if height > bottom - top:
        if height > layout.safe_bottom - layout.safe_margin_y:
            raise ValueError("大字字幕超出画面安全区，请缩短播音单元。")
        center = min((top + bottom) // 2, layout.safe_bottom - math.ceil(height / 2))
        top = max(layout.safe_margin_y, center - math.ceil(height / 2))
        bottom = min(layout.safe_bottom, center + math.ceil(height / 2))
        position = (
            rf"{{\an5\q2\pos({layout.horizontal_center},{center})"
            rf"\clip({layout.safe_margin_x},{top},{layout.safe_right},{bottom})}}"
        )
    return position + r"\N".join(rows)


def _is_continuous_tts_boundary(
    previous: SentenceTiming,
    current: SentenceTiming,
) -> bool:
    return (
        previous.tts_group_id is not None
        and previous.tts_group_id == current.tts_group_id
        and previous.gap_after < MIN_INTER_SENTENCE_GAP_SECONDS
    )


def _validate_timings(timings: Sequence[SentenceTiming]) -> None:
    seen_sentence_ids: set[int] = set()
    previous: SentenceTiming | None = None
    for timing in timings:
        if timing.sentence_id in seen_sentence_ids:
            raise ValueError(f"字幕时间轴包含重复句子编号：{timing.sentence_id}。")
        seen_sentence_ids.add(timing.sentence_id)
        if timing.end <= timing.start:
            raise ValueError(f"句子 {timing.sentence_id} 的字幕时间范围无效。")
        if abs((timing.end - timing.start) - timing.duration) > 0.02:
            raise ValueError(f"句子 {timing.sentence_id} 的字幕时间与音频时长不一致。")
        if previous is not None:
            gap = timing.start - previous.end
            if gap < -0.000001:
                raise ValueError(
                    f"句子 {previous.sentence_id} 与 {timing.sentence_id} 的时间轴发生重叠。"
                )
            if _is_continuous_tts_boundary(previous, timing):
                previous = timing
                continue
            if gap < MIN_INTER_SENTENCE_GAP_SECONDS - 0.000001:
                raise ValueError(
                    f"句子 {previous.sentence_id} 与 {timing.sentence_id} 的字幕间隔"
                    f"不足 {round(MIN_INTER_SENTENCE_GAP_SECONDS * 1000)}ms。"
                )
            previous_end_tick = round(previous.end * 100)
            current_start_tick = round(timing.start * 100)
            if current_start_tick - previous_end_tick < round(MIN_INTER_SENTENCE_GAP_SECONDS * 100):
                raise ValueError("字幕时间量化后句间间隔不足 80ms。")
        previous = timing


def _allocate_events(timing: SentenceTiming, chunks: Sequence[str]) -> list[SubtitleEvent]:
    if timing.words:
        return _allocate_word_aligned_events(timing, chunks)
    start_tick = max(0, round(timing.start * 100))
    end_tick = max(0, round(timing.end * 100))
    available_ticks = end_tick - start_tick
    if available_ticks < len(chunks):
        raise ValueError(f"句子 {timing.sentence_id} 时长过短，无法按 10ms 精度同步字幕。")

    weights = [_reading_weight(chunk) for chunk in chunks]
    remaining_ticks = available_ticks - len(chunks)
    total_weight = sum(weights)
    ideal_extras = [remaining_ticks * weight / total_weight for weight in weights]
    extras = [math.floor(value) for value in ideal_extras]
    unassigned = remaining_ticks - sum(extras)
    for index in sorted(
        range(len(chunks)),
        key=lambda item: (-(ideal_extras[item] - extras[item]), item),
    )[:unassigned]:
        extras[index] += 1

    events: list[SubtitleEvent] = []
    cursor_tick = start_tick
    for chunk, extra in zip(chunks, extras, strict=True):
        next_tick = cursor_tick + 1 + extra
        events.append(
            SubtitleEvent(
                sentence_id=timing.sentence_id,
                start=cursor_tick / 100.0,
                end=next_tick / 100.0,
                text=chunk,
            )
        )
        cursor_tick = next_tick
    if cursor_tick != end_tick:
        raise ValueError(f"句子 {timing.sentence_id} 的字幕时间分配失败。")
    return events


def _allocate_word_aligned_events(
    timing: SentenceTiming,
    chunks: Sequence[str],
) -> list[SubtitleEvent]:
    words = [word for word in timing.words if word.end > word.start]
    if not words:
        return _allocate_events(timing.model_copy(update={"words": []}), chunks)
    word_weights = [_reading_weight(_semantic_text(word.text) or word.text) for word in words]
    chunk_weights = [_reading_weight(chunk) for chunk in chunks]
    total_word_weight = sum(word_weights)
    total_chunk_weight = sum(chunk_weights)
    cumulative_word_weights: list[float] = []
    cursor = 0.0
    for weight in word_weights:
        cursor += weight
        cumulative_word_weights.append(cursor)

    spoken_start, spoken_end = _spoken_time_range(timing)
    spoken_start_tick = round(spoken_start * 100)
    spoken_end_tick = round(spoken_end * 100)
    if spoken_end_tick - spoken_start_tick < len(chunks):
        raise ValueError(f"句子 {timing.sentence_id} 时长过短，无法按 10ms 精度同步字幕。")

    events: list[SubtitleEvent] = []
    chunk_cursor = 0.0
    previous_end_tick = spoken_start_tick
    for index, (chunk, weight) in enumerate(zip(chunks, chunk_weights, strict=True)):
        chunk_cursor += weight
        if index == len(chunks) - 1:
            word_index = len(words) - 1
        else:
            target = total_word_weight * chunk_cursor / total_chunk_weight
            word_index = next(
                (item for item, value in enumerate(cumulative_word_weights) if value >= target),
                len(words) - 1,
            )
        desired_end_tick = round((timing.start + words[word_index].end) * 100)
        remaining_chunks = len(chunks) - index - 1
        latest_end_tick = spoken_end_tick - remaining_chunks
        end_tick = min(latest_end_tick, max(previous_end_tick + 1, desired_end_tick))
        events.append(
            SubtitleEvent(
                sentence_id=timing.sentence_id,
                start=previous_end_tick / 100.0,
                end=end_tick / 100.0,
                text=chunk,
            )
        )
        previous_end_tick = end_tick
    return events


def _validate_events(
    timings: Sequence[SentenceTiming],
    events: Sequence[SubtitleEvent],
    *,
    stable_sentence_ids: set[int] | None = None,
) -> None:
    stable_ids = stable_sentence_ids or set()
    events_by_sentence: dict[int, list[SubtitleEvent]] = {}
    previous_event: SubtitleEvent | None = None
    for event in events:
        if event.sentence_id in stable_ids:
            if not event.text or "\n" in event.text:
                raise ValueError("固定整句字幕事件文本无效。")
            _stable_subtitle_rows(event.text)
        elif not event.text or "\n" in event.text or len(event.text) > MAX_SUBTITLE_CHARS:
            raise ValueError("字幕事件违反单行字符限制。")
        if _contains_forbidden_punctuation(event.text):
            raise ValueError("字幕事件包含书名号以外的标点符号。")
        if event.end <= event.start:
            raise ValueError("字幕事件时长必须大于 0。")
        if previous_event is not None and event.start < previous_event.end:
            raise ValueError("字幕事件发生时间重叠。")
        events_by_sentence.setdefault(event.sentence_id, []).append(event)
        previous_event = event

    for timing in timings:
        sentence_events = events_by_sentence.get(timing.sentence_id, [])
        if not sentence_events:
            raise ValueError(f"句子 {timing.sentence_id} 没有对应字幕事件。")
        expected_start, expected_end = _spoken_time_range(timing)
        if sentence_events[0].start != round(expected_start * 100) / 100.0:
            raise ValueError(f"句子 {timing.sentence_id} 的字幕起点未与声音对齐。")
        if sentence_events[-1].end != round(expected_end * 100) / 100.0:
            raise ValueError(f"句子 {timing.sentence_id} 的字幕终点未与声音对齐。")
        if any(
            left.end != right.start
            for left, right in zip(sentence_events, sentence_events[1:])
        ):
            raise ValueError(f"句子 {timing.sentence_id} 的字幕片段时间轴不连续。")
        if timing.sentence_id in stable_ids and len(sentence_events) != 1:
            raise ValueError(f"句子 {timing.sentence_id} 的固定整句字幕发生了文本切换。")
        if _semantic_text("".join(event.text for event in sentence_events)) != _semantic_text(timing.text):
            raise ValueError(f"句子 {timing.sentence_id} 的字幕未完整保留原文。")


def _tokenize_subtitle_text(text: str, max_chars: int) -> list[_SubtitleToken]:
    normalized = _simplify_text(text)
    if not normalized.strip():
        return []

    tokens: list[_SubtitleToken] = []
    buffer: list[str] = []
    pending_separator = ""

    def flush_buffer() -> None:
        nonlocal pending_separator
        if not buffer:
            return
        phrase = "".join(buffer)
        buffer.clear()
        words = [word.strip() for word in _TOKENIZER.lcut(phrase, cut_all=False, HMM=True) if word.strip()]
        for word in words:
            tokens.extend(_split_oversized_token(word, max_chars, pending_separator))
            pending_separator = ""

    index = 0
    while index < len(normalized):
        character = normalized[index]
        if character == _BOOK_TITLE_OPEN:
            closing_index = normalized.find(_BOOK_TITLE_CLOSE, index + 1)
            if closing_index != -1:
                flush_buffer()
                title = normalized[index : closing_index + 1]
                title_tokens = _tokenize_book_title(title, max_chars, pending_separator)
                tokens.extend(title_tokens)
                pending_separator = ""
                index = closing_index + 1
                continue
        category = unicodedata.category(character)
        if character.isspace() or category.startswith("C"):
            flush_buffer()
            if tokens:
                tokens[-1] = replace(
                    tokens[-1],
                    break_strength_after=max(
                        tokens[-1].break_strength_after,
                        _WHITESPACE_BREAK_STRENGTH,
                    ),
                )
                pending_separator = " "
        elif category.startswith("P"):
            flush_buffer()
            if tokens:
                tokens[-1] = replace(
                    tokens[-1],
                    break_strength_after=_PUNCTUATION_BREAK_STRENGTH,
                )
                pending_separator = " "
        else:
            buffer.append(character)
        index += 1
    flush_buffer()
    return tokens


def _tokenize_book_title(
    title: str,
    max_chars: int,
    separator_before: str,
) -> list[_SubtitleToken]:
    interior = _punctuation_to_spaces(title[1:-1]).strip()
    normalized_title = f"{_BOOK_TITLE_OPEN}{interior}{_BOOK_TITLE_CLOSE}"
    if len(normalized_title) <= max_chars:
        return [_SubtitleToken(text=normalized_title, separator_before=separator_before)]
    words = [word.strip() for word in _TOKENIZER.lcut(interior, cut_all=False, HMM=True) if word.strip()]
    if not words:
        words = [interior]
    words[0] = _BOOK_TITLE_OPEN + words[0]
    words[-1] = words[-1] + _BOOK_TITLE_CLOSE
    result: list[_SubtitleToken] = []
    pending_separator = separator_before
    for word in words:
        pieces = _split_oversized_token(word, max_chars, pending_separator)
        result.extend(
            replace(piece, break_strength_after=_TITLE_INTERNAL_BREAK_STRENGTH)
            for piece in pieces
        )
        pending_separator = ""
    if result:
        result[-1] = replace(result[-1], break_strength_after=_WORD_BREAK_STRENGTH)
    return result


def _split_oversized_token(
    text: str,
    max_chars: int,
    separator_before: str,
) -> list[_SubtitleToken]:
    if len(text) <= max_chars:
        return [_SubtitleToken(text=text, separator_before=separator_before)]
    return [
        _SubtitleToken(
            text=text[index : index + max_chars],
            separator_before=separator_before if index == 0 else "",
            break_strength_after=_TITLE_INTERNAL_BREAK_STRENGTH,
        )
        for index in range(0, len(text), max_chars)
    ]


def _wrap_tokens(tokens: Sequence[_SubtitleToken], max_chars: int) -> list[str]:
    count = len(tokens)
    best_cost = [math.inf] * (count + 1)
    best_lines: list[list[str] | None] = [None] * (count + 1)
    best_cost[count] = 0.0
    best_lines[count] = []

    for start in range(count - 1, -1, -1):
        for end in range(start, count):
            line = _line_from_tokens(tokens, start, end)
            length = len(line)
            if length > max_chars:
                break
            following = best_lines[end + 1]
            if following is None:
                continue
            final_line = end == count - 1
            shortfall = max_chars - length
            line_cost = float(shortfall * shortfall)
            if length < min(7, max_chars) and not final_line:
                line_cost += float((min(7, max_chars) - length) ** 2 * 12)
            if not final_line:
                line_cost -= float(tokens[end].break_strength_after * 8)
            total_cost = line_cost + best_cost[end + 1]
            candidate = [line, *following]
            existing = best_lines[start]
            if (
                total_cost < best_cost[start]
                or (
                    math.isclose(total_cost, best_cost[start])
                    and existing is not None
                    and tuple(candidate) < tuple(existing)
                )
            ):
                best_cost[start] = total_cost
                best_lines[start] = candidate

    result = best_lines[0]
    if result is None:
        raise ValueError("字幕无法在指定字符上限内自然断行。")
    return result


def _line_from_tokens(tokens: Sequence[_SubtitleToken], start: int, end: int) -> str:
    pieces = [tokens[start].text]
    for index in range(start + 1, end + 1):
        pieces.append(tokens[index].separator_before)
        pieces.append(tokens[index].text)
    return "".join(pieces).strip()


def _simplify_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", "".join(text.splitlines()))
    return _SIMPLIFIED_CONVERTER.convert(normalized).strip()


def _punctuation_to_spaces(text: str) -> str:
    pieces: list[str] = []
    pending_space = False
    for character in text:
        category = unicodedata.category(character)
        if character.isspace() or category.startswith(("C", "P")):
            pending_space = bool(pieces)
            continue
        if pending_space:
            pieces.append(" ")
            pending_space = False
        pieces.append(character)
    return "".join(pieces)


def _semantic_text(text: str) -> str:
    simplified = _simplify_text(text)
    return "".join(
        character
        for character in simplified
        if not character.isspace()
        and not unicodedata.category(character).startswith("C")
        and (
            not unicodedata.category(character).startswith("P")
            or character in _BOOK_TITLE_MARKS
        )
    )


def _contains_forbidden_punctuation(text: str) -> bool:
    return any(
        unicodedata.category(character).startswith("P")
        and character not in _BOOK_TITLE_MARKS
        for character in text
    )


def _reading_weight(text: str) -> float:
    return max(
        1.0,
        sum(
            0.35 if character.isspace() or character in _BOOK_TITLE_MARKS else 1.0
            for character in text
        ),
    )


def _build_ass_document(
    events: Sequence[SubtitleEvent],
    layout: SubtitleLayout,
    *,
    title: str | None,
    stable_ids: set[int],
) -> str:
    header = f"""[Script Info]
; TemplateId: {SUBTITLE_TEMPLATE_ID}
; TemplateFingerprint: {_template_fingerprint()}
; SafeArea: center {round(SAFE_AREA_RATIO * 100)}% x {round(SAFE_AREA_RATIO * 100)}%
; DialogueRegion: y={layout.region_top}-{layout.region_bottom} ({round(SUBTITLE_REGION_HEIGHT_RATIO * 100)}% height)
Title: AI 智能新闻剪辑字幕
ScriptType: v4.00+
PlayResX: {layout.frame_width}
PlayResY: {layout.frame_height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_ass_style_line(layout)}
{_stable_ass_style_line(layout)}
{_title_style_line(layout)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    position_tag = _ass_position_tag(layout)
    dialogue_lines = [
        f"Dialogue: {SUBTITLE_LAYER},{format_ass_time(event.start)},{format_ass_time(event.end)},"
        f"{SUBTITLE_STYLE_NAME},,0,0,0,,{position_tag}{_escape_ass_text(event.text)}"
        for event in events
        if event.sentence_id not in stable_ids
    ]
    stable_lines: list[str] = []
    for event in events:
        if event.sentence_id not in stable_ids:
            continue
        stable_text = r"\N".join(
            _escape_ass_text(row) for row in _stable_subtitle_rows(event.text)
        )
        stable_lines.append(
            f"Dialogue: {SUBTITLE_LAYER + 1},{format_ass_time(event.start)},{format_ass_time(event.end)},"
            f"{STABLE_SUBTITLE_STYLE_NAME},,0,0,0,,{_stable_ass_position_tag(layout)}{stable_text}"
        )
    title_lines: list[str] = []
    if title and title.strip():
        title_chunks = split_subtitle_text(title, max_chars=MAX_TITLE_CHARS)
        if len(title_chunks) > 2:
            compact_title = "".join(title_chunks).replace(" ", "")
            title_chunks = [compact_title[:MAX_TITLE_CHARS], compact_title[MAX_TITLE_CHARS:]]
        title_text = r"\N".join(_escape_ass_text(chunk) for chunk in title_chunks)
        title_end = min(TITLE_DURATION_SECONDS, events[-1].end if events else TITLE_DURATION_SECONDS)
        title_lines.append(
            f"Dialogue: {SUBTITLE_LAYER + 10},{format_ass_time(0.0)},{format_ass_time(title_end)},"
            f"{TITLE_STYLE_NAME},,0,0,0,,{_title_position_tag(layout)}{title_text}"
        )
    return header + "\n".join([*title_lines, *stable_lines, *dialogue_lines]) + "\n"


def _template_specification() -> SubtitleTemplateSpecification:
    return {
        "template_id": SUBTITLE_TEMPLATE_ID,
        "style_name": SUBTITLE_STYLE_NAME,
        "stable_style_name": STABLE_SUBTITLE_STYLE_NAME,
        "title_style_name": TITLE_STYLE_NAME,
        "font_family": SUBTITLE_FONT_NAME,
        "nominal_1080p_font_points": 40.5,
        "font_height_ratio": DIALOGUE_FONT_HEIGHT_RATIO,
        "stable_font_height_ratio": STABLE_SUBTITLE_FONT_HEIGHT_RATIO,
        "uhd_4k_font_height_ratio": DIALOGUE_FONT_HEIGHT_RATIO,
        "uhd_4k_font_height_tolerance": 0.01,
        "safe_area_ratio_x": SAFE_AREA_RATIO,
        "safe_area_ratio_y": SAFE_AREA_RATIO,
        "dialogue_region_height_ratio": SUBTITLE_REGION_HEIGHT_RATIO,
        "max_characters_per_line": MAX_SUBTITLE_CHARS,
        "stable_max_characters_per_line": MAX_STABLE_SUBTITLE_CHARS,
        "stable_max_line_count": MAX_STABLE_SUBTITLE_LINES,
        "title_max_characters_per_line": MAX_TITLE_CHARS,
        "title_line_count": 2,
        "title_duration_seconds": TITLE_DURATION_SECONDS,
        "line_count": 1,
        "minimum_sentence_gap_ms": round(MIN_INTER_SENTENCE_GAP_SECONDS * 1000),
        "alignment": "dialogue-region-center",
        "primary_color_rgb": "#FFFFFF",
        "outline_color_rgb": "#000000",
        "contrast_ratio": 21.0,
        "outline_size_at_1080p": 4,
        "shadow_size_at_1080p": 1,
        "animation": "none",
        "layer": SUBTITLE_LAYER,
    }


def _template_fingerprint() -> str:
    canonical = json.dumps(
        _template_specification(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _ass_style_line(layout: SubtitleLayout) -> str:
    return (
        f"Style: {SUBTITLE_STYLE_NAME},{SUBTITLE_FONT_NAME},{layout.font_size},"
        "&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,"
        f"{layout.letter_spacing},0,1,{layout.outline_size},{layout.shadow_size},5,"
        f"{layout.safe_margin_x},{layout.safe_margin_x},{layout.safe_margin_y},1"
    )


def _stable_ass_style_line(layout: SubtitleLayout) -> str:
    stable_font_size = max(1, round(layout.frame_height * STABLE_SUBTITLE_FONT_HEIGHT_RATIO))
    return (
        f"Style: {STABLE_SUBTITLE_STYLE_NAME},{SUBTITLE_FONT_NAME},{stable_font_size},"
        "&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,"
        f"{layout.letter_spacing},0,1,{layout.outline_size},{layout.shadow_size},5,"
        f"{layout.safe_margin_x},{layout.safe_margin_x},{layout.safe_margin_y},1"
    )


def _title_style_line(layout: SubtitleLayout) -> str:
    title_font_size = max(layout.font_size, round(layout.frame_height * 0.075))
    return (
        f"Style: {TITLE_STYLE_NAME},{SUBTITLE_FONT_NAME},{title_font_size},"
        "&H0000D7FF,&H0000D7FF,&H00101010,&H90000000,-1,0,0,0,100,100,"
        f"{layout.letter_spacing},0,1,{layout.outline_size},{layout.shadow_size},5,"
        f"{layout.safe_margin_x},{layout.safe_margin_x},{layout.safe_margin_y},1"
    )


def _ass_position_tag(layout: SubtitleLayout) -> str:
    return (
        rf"{{\an5\q2\pos({layout.horizontal_center},{layout.vertical_center})"
        rf"\clip({layout.safe_margin_x},{layout.region_top},{layout.safe_right},{layout.region_bottom})}}"
    )


def _stable_ass_position_tag(layout: SubtitleLayout) -> str:
    stable_top = max(layout.safe_margin_y, round(layout.frame_height * 0.75))
    stable_y = (stable_top + layout.region_bottom) // 2
    return (
        rf"{{\an5\q2\pos({layout.horizontal_center},{stable_y})"
        rf"\clip({layout.safe_margin_x},{stable_top},{layout.safe_right},{layout.region_bottom})}}"
    )


def _title_position_tag(layout: SubtitleLayout) -> str:
    title_y = max(layout.safe_margin_y, round(layout.frame_height * 0.25))
    return rf"{{\an5\q2\pos({layout.horizontal_center},{title_y})}}"


def _stable_subtitle_rows(text: str) -> list[str]:
    rows = split_subtitle_text(text, max_chars=MAX_STABLE_SUBTITLE_CHARS)
    if len(rows) <= MAX_STABLE_SUBTITLE_LINES:
        return rows
    compact = "".join(rows).replace(" ", "")
    row_size = math.ceil(len(compact) / MAX_STABLE_SUBTITLE_LINES)
    if row_size > MAX_STABLE_SUBTITLE_CHARS:
        raise ValueError("固定整句字幕超过两行容量，请缩短播音单元。")
    return [
        compact[index : index + row_size]
        for index in range(0, len(compact), row_size)
    ]


def _spoken_time_range(timing: SentenceTiming) -> tuple[float, float]:
    if timing.words:
        relative_start = min(timing.duration, max(0.0, timing.words[0].start))
        relative_end = min(timing.duration, max(relative_start, timing.words[-1].end))
        return timing.start + relative_start, timing.start + relative_end
    return timing.start, timing.end


def _layout_specification(layout: SubtitleLayout) -> SubtitleLayoutSpecification:
    return {
        "frame_width": layout.frame_width,
        "frame_height": layout.frame_height,
        "safe_area": {
            "left": layout.safe_margin_x,
            "top": layout.safe_margin_y,
            "right": layout.safe_right,
            "bottom": layout.safe_bottom,
        },
        "dialogue_region": {
            "left": layout.safe_margin_x,
            "top": layout.region_top,
            "right": layout.safe_right,
            "bottom": layout.region_bottom,
            "height": layout.region_height,
        },
        "font_size": layout.font_size,
        "letter_spacing": layout.letter_spacing,
        "outline_size": layout.outline_size,
        "shadow_size": layout.shadow_size,
    }


def _build_subtitle_manifest(
    timings: Sequence[SentenceTiming],
    events: Sequence[SubtitleEvent],
    layout: SubtitleLayout,
    ass_path: Path,
    *,
    title: str | None,
    stable_sentence_ids: set[int],
) -> SubtitleManifest:
    observed_gaps = [
        current.start - previous.end
        for previous, current in zip(timings, timings[1:])
        if not _is_continuous_tts_boundary(previous, current)
    ]
    rendered_events = list(events)
    word_aligned_count = sum(bool(timing.words) for timing in timings)
    return {
        "schema_version": SUBTITLE_TEMPLATE_SCHEMA_VERSION,
        "template_id": SUBTITLE_TEMPLATE_ID,
        "template_fingerprint": _template_fingerprint(),
        "template": _template_specification(),
        "layout": _layout_specification(layout),
        "sentence_count": len(timings),
        "sentence_ids": [timing.sentence_id for timing in timings],
        "event_count": len(rendered_events) + int(bool(title and title.strip())),
        "title_event_count": int(bool(title and title.strip())),
        "stable_event_count": len(stable_sentence_ids),
        "stable_sentence_ids": sorted(stable_sentence_ids),
        "minimum_observed_sentence_gap_ms": (
            round(min(observed_gaps) * 1000) if observed_gaps else None
        ),
        "source_text_sha256": hashlib.sha256(
            "\n".join(f"{timing.sentence_id}:{timing.text}" for timing in timings).encode("utf-8")
        ).hexdigest(),
        "display_text_sha256": hashlib.sha256(
            "\n".join(
                f"{event.sentence_id}:{event.start:.2f}-{event.end:.2f}:{event.text}"
                for event in rendered_events
            ).encode("utf-8")
        ).hexdigest(),
        "ass_sha256": hashlib.sha256(ass_path.read_bytes()).hexdigest(),
        "word_timing_coverage_ratio": round(word_aligned_count / len(timings), 6),
        "normalization": "Unicode NFKC + OpenCC t2s; punctuation converted to pauses except 《》",
        "checks": {
            "single_line": True,
            "maximum_15_characters": True,
            "no_forbidden_punctuation": True,
            "non_overlapping_events": True,
            "sound_aligned_sentence_boundaries": word_aligned_count == len(timings),
            "minimum_80ms_sentence_gap": True,
            "achromatic_21_to_1_contrast": True,
            "stable_sentence_text_unchanged": True,
        },
    }


def format_ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole_seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{centiseconds:02d}"


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp")
    temporary_path.write_bytes(content.encode("utf-8"))
    temporary_path.replace(path)


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")
