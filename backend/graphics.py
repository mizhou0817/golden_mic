"""News graphics package rendered as a SEPARATE ASS overlay (``graphics.ass``).

This layer is intentionally independent from the validated dialogue subtitle
template (``subs.ass`` / ``subtitle_manifest.json``): it is burned as a second
``ass`` filter after the subtitles and never mutates the hashed subtitle
artifacts. It can contain the optional top-left news package. Overlays made with
label generations 1-3 (before 2026-10-23) also labelled generated media: an opening
label for the whole video plus one over each generated shot. New overlays carry no
label (see DISCLOSURE_GENERATION). The opening title ("片头") is already
handled by the subtitle title style, so it is deliberately not duplicated here.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from .models import EditingPreferences, MatchPlanItem, SentenceTiming
from .production_modes import LIMITS, Speaker, Word, _word_spans
from .storage import write_json_atomic, write_text_log


GRAPHICS_FILE_NAME = "graphics.ass"
_PLAY_RES_X = 1920
_PLAY_RES_Y = 1080
_DARK_BGR = "141018"
_ACCENT_BGR = {
    "solemn": "56472E",
    "neutral": "A85A1E",
    "energetic": "1E32C8",
    "uplifting": "1E32C8",
    "tense": "1E87C8",
}
_TOPIC_MAX_CHARS = 12
_END_CARD_SECONDS = 2.5
# Label generations 2 and 3, kept to regenerate the works made with them (generation 4 draws no label).
# GB 45438-2025 5.4: an explicit label on an edge or corner of the frame from the first frame for at
# least 2 s, its text at least 5% of the frame's short side. libass maps the bundled Noto Sans SC's
# 1448-unit line box (win ascent + descent) to the ASS font size, so 96 on the 1080 canvas keeps the ink
# of every Chinese label character (845 units or more, Regular or Bold) above 54 px. The Latin capitals of
# "AI" (733 or 741 units) only reach it at 115%, which the third label generation draws (below).
DISCLOSURE_FONT_SIZE = 96
DISCLOSURE_SHORT_SIDE_RATIO = DISCLOSURE_FONT_SIZE / _PLAY_RES_Y
OPENING_DISCLOSURE_TEXT = "本片含AI生成内容"
OPENING_DISCLOSURE_SECONDS = 3.0
# Top margin of the labels in ASS units of the 1080-high canvas: on the right edge, BELOW the headline title
# (subs.ass ModeTitle: margin 55, 60 tall, outline 3) and the topic band (top 50, 64 tall, shadow 6). Positions scale
# with the frame height but glyph widths do not follow the frame width, so a label at the very top covered the title
# in 1:1 (54% of its pixels) and in 9:16 (94%) exports; 130 clears both in every shape.
DISCLOSURE_MARGIN_V = 130

# graphics.ass is a hashed artifact that a V2 export regenerates and compares byte for byte
# (studio.prepare_mode_export), so a work keeps the label generation it was made with, as the captions
# keep LEGACY_MODE_CAPTION_TEMPLATE. Each generation from the second on names itself in a comment line:
#   1  a small label over each generated shot;
#   2  2026-10-22: an opening label for the whole video as well, size 96 on the right edge, stacked by libass;
#   3  2026-10-23: the Latin "AI" at 115% with Han-Latin spacing in one clean box per label, the slots placed
#      here, and in the legacy pipeline a label moves below the centred headline instead of covering it (legacy
#      overlays are never regenerated, so only the V2 part is kept);
#   4  2026-10-23: no AI label at all, in V2 and legacy overlays alike (the maintainer's decision, recorded with its
#      legal risk in .github/copilot-instructions.md). Generations 1-3 stay only so that works made with them export
#      with their labels unchanged; never make a new overlay with them.
DISCLOSURE_GENERATION = 4
_DISCLOSURE_TEMPLATES = {2: "GM-GFX-DISCLOSURE-V2-20261022", 3: "GM-GFX-DISCLOSURE-V3-20261023",
                         4: "GM-GFX-NO-AI-LABEL-V4-20261023"}
DISCLOSURE_TEMPLATE = _DISCLOSURE_TEMPLATES[DISCLOSURE_GENERATION]

# Third-generation label layout, in units of the 1920x1080 canvas. Noto Sans SC Bold metrics in font units (a
# test reads them from the font file): libass maps the 1448-unit line box (ascent 1160 + descent 288) to the
# font size.
_LINE_BOX_UNITS = 1448
_ASCENT_UNITS = 1160
# Mixed Han and Latin: composite fonts scale the Latin by 105-120%, mostly 110-115%; at 115% the capitals' top meets
# the Han top. About 1/8 em of space between the scripts (CSS text-autospace): 8 px after the Han character before
# "AI" (the A's own side bearing leaves 3 px) and 2 px after the I (which already leaves 10 px).
_LATIN_SCALE = 115
_SPACE_BEFORE_LATIN = 8
_SPACE_AFTER_LATIN = 2
# The larger "AI" makes its line's ascent taller by this much. The text is lifted by it, so its baseline stays where
# the box (drawn from the same text without the vertical scale, so as tall as one 96 line) expects it.
_LATIN_LIFT = (_LATIN_SCALE / 100 - 1) * _ASCENT_UNITS / _LINE_BOX_UNITS * DISCLOSURE_FONT_SIZE
_LABEL_RIGHT = _PLAY_RES_X - 50
_LABEL_BORDER = 1.5
_LABEL_PITCH = 100  # one slot: the 96 line box and its box border, the step libass stacked the second generation by


def read_disclosure_generation(content: bytes) -> int:
    """The label generation a stored graphics.ass was written with (1: the first, which has no marker)."""
    for generation in sorted(_DISCLOSURE_TEMPLATES, reverse=True):
        if f"; template: {_DISCLOSURE_TEMPLATES[generation]}".encode("ascii") in content:
            return generation
    return 1


def _header(generation: int = DISCLOSURE_GENERATION) -> str:
    if generation >= 4:
        style = "\n"  # no label, so no label style
    elif generation == 3:
        # GfxDisclosure draws one box per label (BorderStyle 4, in BackColour) behind the GfxDisclosureText text.
        style = (
            f"Style: GfxDisclosure,Noto Sans SC,{DISCLOSURE_FONT_SIZE},&H00FFFFFF&,&H00FFFFFF&,&H00101418&,&H00101418&,-1,0,0,0,100,100,0,0,4,{_LABEL_BORDER},0,9,40,50,{DISCLOSURE_MARGIN_V},1\n"
            f"Style: GfxDisclosureText,Noto Sans SC,{DISCLOSURE_FONT_SIZE},&H00FFFFFF&,&H00FFFFFF&,&H00101418&,&H00101418&,-1,0,0,0,100,100,0,0,1,0,0,9,40,50,{DISCLOSURE_MARGIN_V},1\n\n"
        )
    elif generation == 2:
        style = f"Style: GfxDisclosure,Noto Sans SC,{DISCLOSURE_FONT_SIZE},&H00FFFFFF&,&H00FFFFFF&,&H00101418&,&H80141018&,-1,0,0,0,100,100,0,0,3,1,0,9,40,50,{DISCLOSURE_MARGIN_V},1\n\n"
    else:
        style = "Style: GfxDisclosure,Noto Sans SC,30,&H00FFFFFF&,&H00FFFFFF&,&H00101418&,&H80141018&,-1,0,0,0,100,100,0,0,3,1,0,9,40,40,40,1\n\n"
    return (
        "[Script Info]\n"
        + (f"; template: {_DISCLOSURE_TEMPLATES[generation]}\n" if generation >= 2 else "")
        + "ScriptType: v4.00+\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {_PLAY_RES_X}\n"
        f"PlayResY: {_PLAY_RES_Y}\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: GfxBand,Noto Sans SC,20,&H00FFFFFF&,&H00FFFFFF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "Style: GfxTopic,Noto Sans SC,38,&H00FFFFFF&,&H00FFFFFF&,&H00101418&,&H00000000&,-1,0,0,0,100,100,0,0,1,1,1,7,0,0,0,1\n"
        "Style: GfxEnd,Noto Sans SC,60,&H00FFFFFF&,&H00FFFFFF&,&H00101418&,&H00000000&,-1,0,0,0,100,100,0,0,1,3,2,5,0,0,0,1\n"
        + style
        + "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


class GraphicsError(RuntimeError):
    """Raised when the news graphics overlay cannot be produced."""


def _ass_time(seconds: float) -> str:
    ticks = max(0, round(seconds * 100))
    hours, remainder = divmod(ticks, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    whole, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centiseconds:02d}"


def _sanitize(text: str) -> str:
    cleaned = text.replace("{", "").replace("}", "").replace("\\", "")
    cleaned = cleaned.replace("\r", " ").replace("\n", " ")
    return " ".join(cleaned.split()).strip()


def _rectangle(width: int, height: int) -> str:
    return f"m 0 0 l {width} 0 l {width} {height} l 0 {height}"


def _accent_color(accent: str) -> str:
    return _ACCENT_BGR.get(accent, _ACCENT_BGR["neutral"])


def _topic_events(topic: str, end_time: float, accent: str) -> list[str]:
    label = _sanitize(topic)[:_TOPIC_MAX_CHARS]
    if not label:
        return []
    band_width = len(label) * 44 + 64
    end = _ass_time(end_time)
    accent_color = _accent_color(accent)
    return [
        # Dark, near-opaque band with a soft drop shadow for legibility over busy footage.
        f"Dialogue: 0,0:00:00.00,{end},GfxBand,,0,0,0,,"
        f"{{\\pos(64,50)\\an7\\1c&H{_DARK_BGR}&\\1a&H1E&\\bord0\\shad6\\4c&H000000&\\4a&H50&\\p1}}{_rectangle(band_width + 8, 64)}{{\\p0}}",
        # Accent stripe on the left edge.
        f"Dialogue: 1,0:00:00.00,{end},GfxBand,,0,0,0,,"
        f"{{\\pos(64,50)\\an7\\1c&H{accent_color}&\\1a&H00&\\bord0\\shad0\\p1}}{_rectangle(10, 64)}{{\\p0}}",
        # Topic label text with a stronger outline so it stays readable on any background.
        f"Dialogue: 2,0:00:00.00,{end},GfxTopic,,0,0,0,,{{\\pos(100,62)\\an7\\bord2.4\\shad1\\3c&H000000&}}{label}",
    ]


def _end_card_events(headline: str, total_duration: float, accent: str) -> list[str]:
    text = _sanitize(headline)
    if not text or total_duration <= 0:
        return []
    start_time = max(0.0, total_duration - _END_CARD_SECONDS)
    start = _ass_time(start_time)
    end = _ass_time(total_duration)
    accent_color = _accent_color(accent)
    return [
        # Full-width translucent band.
        f"Dialogue: 0,{start},{end},GfxBand,,0,0,0,,"
        f"{{\\pos(0,468)\\an7\\1c&H{_DARK_BGR}&\\1a&H3C&\\bord0\\shad0\\fad(400,300)\\p1}}{_rectangle(_PLAY_RES_X, 152)}{{\\p0}}",
        # Centered accent rule under the headline.
        f"Dialogue: 1,{start},{end},GfxBand,,0,0,0,,"
        f"{{\\pos(710,598)\\an7\\1c&H{accent_color}&\\1a&H00&\\bord0\\shad0\\fad(400,300)\\p1}}{_rectangle(500, 6)}{{\\p0}}",
        # Headline text.
        f"Dialogue: 2,{start},{end},GfxEnd,,0,0,0,,{{\\an5\\pos(960,540)\\fad(500,300)}}{text}",
    ]


def _number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _is_han(text: str) -> bool:
    return len(text) == 1 and "一" <= text <= "鿿"


def _label_characters(text: str) -> list[tuple[str, bool, float]]:
    """(character, part of the scaled "AI", px of space after it) for each character of a label."""
    characters: list[tuple[str, bool, float]] = []
    index = 0
    while index < len(text):
        if text.startswith("AI", index):
            characters += [("A", True, 0), ("I", True, _SPACE_AFTER_LATIN if _is_han(text[index + 2:index + 3]) else 0)]
            index += 2
            continue
        space = _SPACE_BEFORE_LATIN if text.startswith("AI", index + 1) and _is_han(text[index]) else 0
        characters.append((text[index], False, space))
        index += 1
    return characters


def _label_markup(text: str, *, tall: bool) -> str:
    """The label with its Latin "AI" at _LATIN_SCALE, spaced from the Han text around it.

    `tall` False only widens the "AI": the box is drawn from that markup, so it is exactly as wide as the text and as
    tall as one plain line.
    """
    scale = f"\\fscx{_LATIN_SCALE}" + (f"\\fscy{_LATIN_SCALE}" if tall else "")
    reset = "\\fscx100" + ("\\fscy100" if tall else "")
    parts: list[str] = []
    for char, latin, space in _label_characters(text):
        if latin and char == "A":
            parts.append("{\\fsp0" + scale + "}")
        if space or (latin and char == "I"):
            # \fsp adds its space after every following glyph and is scaled with the run's \fscx.
            parts.append("{\\fsp" + _number(space * 100 / _LATIN_SCALE if latin else space) + "}")
        parts.append(char)
        if latin and char == "I":
            parts.append("{\\fsp0" + reset + "}")
    return "".join(parts)


def _label_tops(labels: Sequence[tuple[float, float, str]]) -> list[float]:
    """Top of each label's line (its box adds _LABEL_BORDER all round), stacked down from DISCLOSURE_MARGIN_V.

    A label takes the highest slot that is free when it starts and keeps it for its whole time, as libass stacked the
    second generation.
    """
    candidates = [DISCLOSURE_MARGIN_V + _LABEL_PITCH * step for step in range(8)]
    placed: list[tuple[float, float, float]] = []
    tops: list[float] = []
    for start_time, end_time, _ in labels:
        top = next(candidate for candidate in candidates if not any(
            start < end_time and start_time < end and abs(other - candidate) < _LABEL_PITCH for start, end, other in placed))
        placed.append((start_time, end_time, top))
        tops.append(top)
    return tops


def _disclosure_events(
    intervals: Sequence[tuple[float, float]],
    text: str,
    total_duration: float = 0.0,
    *,
    generation: int,
) -> list[str]:
    """The labels of a work made with label generation 1, 2 or 3; generation 4 draws none."""
    label = _sanitize(text)
    if generation >= 4 or not label or not intervals:
        return []
    for start_time, end_time in intervals:
        if start_time < 0 or end_time <= start_time:
            raise GraphicsError("AI 生成画面披露时间范围无效。")
    if generation == 1:
        # First generation: a small label over each generated shot only.
        return [
            f"Dialogue: 10,{_ass_time(start_time)},{_ass_time(end_time)},"
            f"GfxDisclosure,,0,0,0,,{{\\an9\\pos(1870,40)\\bord1.5\\shad1}}{label}"
            for start_time, end_time in intervals
        ]
    # Any generated shot labels the whole video from its first frame, wherever the shot is.
    video_end = max(total_duration, *(end_time for _, end_time in intervals))
    opening_end = min(OPENING_DISCLOSURE_SECONDS, video_end)
    if generation == 2:
        # No \pos: the style anchors the right edge under the title and the topic band, so libass stacks labels that
        # overlap (the opening one is listed first and keeps the top slot) instead of printing one over the other.
        events = [f"Dialogue: 10,{_ass_time(0)},{_ass_time(opening_end)},"
                  f"GfxDisclosure,,0,0,0,,{{\\an9\\bord1.5\\shad1}}{OPENING_DISCLOSURE_TEXT}"]
        for start_time, end_time in intervals:
            events.append(
                f"Dialogue: 10,{_ass_time(start_time)},{_ass_time(end_time)},"
                f"GfxDisclosure,,0,0,0,,{{\\an9\\bord1.5\\shad1}}{label}"
            )
        return events
    labels = [(0.0, opening_end, OPENING_DISCLOSURE_TEXT), *((start, end, label) for start, end in intervals)]
    events = []
    for (start_time, end_time, words), top in zip(labels, _label_tops(labels)):
        a, b = _ass_time(start_time), _ass_time(end_time)
        events += [
            # The plain label for anyone reading the file; renderers skip Comment lines.
            f"Comment: 10,{a},{b},GfxDisclosureText,,0,0,0,,{words}",
            f"Dialogue: 9,{a},{b},GfxDisclosure,,0,0,0,,"
            f"{{\\an9\\pos({_LABEL_RIGHT},{_number(top)})\\1a&HFF&\\3a&HFF&}}{_label_markup(words, tall=False)}",
            f"Dialogue: 10,{a},{b},GfxDisclosureText,,0,0,0,,"
            f"{{\\an9\\pos({_LABEL_RIGHT},{_number(top - _LATIN_LIFT)})}}{_label_markup(words, tall=True)}",
        ]
    return events


def generate_news_graphics(
    task_dir: Path,
    *,
    topic: str,
    headline: str,
    total_duration: float,
    accent: str = "neutral",
    output_path: Path | None = None,
) -> Path | None:
    """Write one ASS overlay for the optional news graphics; it labels no generated media (generation 4)."""
    events = _topic_events(topic, total_duration, accent) + _end_card_events(headline, total_duration, accent)
    if not events:
        return None
    destination = output_path or task_dir / GRAPHICS_FILE_NAME
    destination.write_text(_header() + "\n".join(events) + "\n", encoding="utf-8")
    write_text_log(
        task_dir,
        f"新闻图文包装已生成 accent={accent} topic={'有' if topic.strip() else '无'} "
        f"end_card={'有' if headline.strip() else '无'}",
    )
    return destination


LEGACY_MODE_CAPTION_TEMPLATE = "GM-MODE-CAPTIONS-20260928-001"
MODE_CAPTION_TEMPLATE = "GM-MODE-CAPTIONS-V2-20260929"
MODE_CAPTION_MAX_CHARS = 18


def _mode_caption_header(style: str, *, v2: bool = True) -> str:
    size = (54 if style == "big" else 42) if v2 else (72 if style == "big" else 54)
    template = MODE_CAPTION_TEMPLATE if v2 else LEGACY_MODE_CAPTION_TEMPLATE
    return (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        f"; TemplateId: {template}\nPlayResX: 1920\nPlayResY: 1080\n"
        "YCbCr Matrix: TV.709\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: ModeNarration,Noto Sans SC,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,3,0,2,192,192,108,1\n"
        f"Style: ModeQuote,Noto Sans SC,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,3,0,2,192,192,108,1\n"
        "Style: ModeTitle,Noto Sans SC,60,&H003CA8E2,&H003CA8E2,&H0026364A,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,3,0,8,192,192,55,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _mode_caption_document(style: str, events: Sequence[dict[str, Any]], *, v2: bool = True) -> str:
    lines = []
    for event in events:
        text = str(event["text"])
        # Escaping prevents ASR/manuscript contents from injecting ASS overrides.
        text = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
        text = text.replace("\r", "").replace("\n", r"\N")
        kind = event["kind"]
        name = {"quote": "ModeQuote", "narration": "ModeNarration", "title": "ModeTitle"}[kind]
        lines.append(
            f"Dialogue: {10 if kind == 'title' else 0},{_ass_time(event.get('ass_start', event['start']))},"
            f"{_ass_time(event.get('ass_end', event['end']))},{name},,0,0,0,,{text}"
        )
    return _mode_caption_header(style, v2=v2) + "\n".join(lines) + "\n"


def _caption_lines(text: str, max_chars: int = MODE_CAPTION_MAX_CHARS) -> str:
    # A line break changes layout only, never the underlying ASR word clock.
    text = text.replace("\r", "").replace("\n", " ")
    return "\n".join(text[i:i + max_chars] for i in range(0, len(text), max_chars))


def generate_mode_subtitles(
    task_dir: Path, timings: Sequence[SentenceTiming], plan: Sequence[MatchPlanItem],
    preferences: EditingPreferences, *, title: str | None = None, v2: bool = True,
) -> list[dict[str, Any]]:
    """Use literal spoken ASR for quotes; never proportionally invent word cues.

    Segment-only evidence displays the entire segment at once, wrapped at 18
    characters. It is explicitly NOT a word-aligned rolling caption. A long
    segment is reported to QC instead of silently manufacturing time boundaries.
    caption_style=none hides narration; quote_caption independently controls B/C.
    """
    by_id = {item.sentence_id: item for item in plan}
    max_chars = 14 if v2 and preferences.caption_style == "big" else 18
    events: list[dict[str, Any]] = []
    for timing in timings:
        item = by_id[timing.sentence_id]
        if (item.kind == "quote" and preferences.quote_caption == "none") or (
            item.kind == "narration" and preferences.caption_style == "none"
        ):
            continue
        text = timing.text if item.kind == "quote" or timing.audio_kind == "sync" else item.text
        words = [Word(w=w.text, s=w.start, e=w.end) for w in timing.words]
        spans = _word_spans(text, words, 0.0, timing.duration)
        groups: list[tuple[str, float, float, str]] = []
        if spans:
            first = 0
            while first < len(words):
                last = first
                lo = 0 if first == 0 else spans[first][0]
                while last + 1 < len(words):
                    stop = len(text) if last + 2 == len(words) else spans[last + 2][0]
                    if len(text[lo:stop].replace("\n", " ")) > max_chars:
                        break
                    last += 1
                hi = len(text) if last + 1 == len(words) else spans[last + 1][0]
                groups.append((text[lo:hi], words[first].s, words[last].e, "word"))
                first = last + 1
        else:
            # No interpolation, including legacy own-voice utterance evidence.
            start = timing.words[0].start if timing.words else 0.0
            end = timing.words[-1].end if timing.words else timing.duration
            groups.append((text, start, end, "segment"))
        for spoken, start, end, precision in groups:
            # ASS has 10ms resolution. Round INWARD; never extend into a neighbour.
            start_tick = math.ceil((timing.start + start) * 100 - 1e-8)
            end_tick = math.floor((timing.start + end) * 100 + 1e-8)
            if end_tick <= start_tick:
                raise GraphicsError("真实语音区间不足 ASS 的 10ms 精度，不能伪造字幕时间。")
            events.append({
                "sentence_id": timing.sentence_id, "kind": item.kind,
                "start": timing.start + start if v2 else start_tick / 100,
                "end": timing.start + end if v2 else end_tick / 100,
                **({"ass_start": start_tick / 100, "ass_end": end_tick / 100} if v2 else {}),
                "text": _caption_lines(spoken, max_chars), "precision": precision,
                "source": "asr" if item.kind == "quote" else timing.audio_kind,
            })
    if title and timings:
        events.insert(0, {
            "sentence_id": None, "kind": "title", "start": 0.0,
            "end": min(2.5, math.floor(timings[-1].end * 100) / 100),
            "text": _caption_lines(title, max_chars), "precision": "editorial", "source": "title",
        })
    document = _mode_caption_document(preferences.caption_style, events, v2=v2)
    destination = task_dir / "subs.ass"
    destination.write_text(document, encoding="utf-8", newline="\n")
    write_json_atomic(task_dir / "subtitle_manifest.json", {
        "schema_version": 1, "template_id": MODE_CAPTION_TEMPLATE if v2 else LEGACY_MODE_CAPTION_TEMPLATE,
        "mode_contract": True, "caption_style": preferences.caption_style,
        "quote_caption": "spoken" if v2 and preferences.quote_caption != "none" else preferences.quote_caption,
        "max_line_chars": max_chars,
        "ass_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "event_count": len(events), "events": events,
    })
    validate_mode_subtitle_artifacts(destination, task_dir / "subtitle_manifest.json")
    return events


def validate_mode_subtitle_artifacts(ass_path: Path, manifest_path: Path) -> dict[str, Any]:
    if ass_path.stat().st_size > 256 * 1024 or manifest_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("三模式字幕超过安全大小。")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("mode_contract") is not True or payload.get("template_id") not in {MODE_CAPTION_TEMPLATE, LEGACY_MODE_CAPTION_TEMPLATE} or payload.get("schema_version") != 1:
        raise ValueError("未知三模式字幕模板。")
    style, events = payload.get("caption_style"), payload.get("events")
    v2 = payload["template_id"] == MODE_CAPTION_TEMPLATE
    max_chars = 14 if v2 and style == "big" else 18
    if style not in {"news", "big", "none"} or not isinstance(events, list) or len(events) > 10000 or payload.get("quote_caption") not in {"spoken", "asr", "none"}:
        raise ValueError("三模式字幕清单无效。")
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in {"quote", "narration", "title"}:
            raise ValueError("字幕类型无效。")
        if not all(type(event.get(k)) in (float, int) and math.isfinite(event[k]) for k in ("start", "end")):
            raise ValueError("字幕时间无效。")
        if not 0 <= event["start"] < event["end"] or not isinstance(event.get("text"), str) or not event["text"]:
            raise ValueError("字幕区间或文字无效。")
        if any(ord(c) < 32 and c != "\n" for c in event["text"]):
            raise ValueError("字幕含控制字符。")
        if (event["kind"] == "quote" and payload["quote_caption"] == "none") or (event["kind"] == "narration" and style == "none"):
            raise ValueError("字幕事件违反已确认的显示策略。")
        if any(len(line) > max_chars for line in event["text"].split("\n")):
            raise ValueError(f"三模式字幕不能超过每行 {max_chars} 字。")
        if v2 and event["kind"] != "title":
            expected_start = math.ceil(event["start"] * 100 - 1e-8) / 100
            expected_end = math.floor(event["end"] * 100 + 1e-8) / 100
            if event.get("ass_start") != expected_start or event.get("ass_end") != expected_end or expected_start >= expected_end:
                raise ValueError("ASS 时间必须由真实语音时钟向内量化。")
    content = ass_path.read_bytes()
    if payload.get("ass_sha256") != hashlib.sha256(content).hexdigest() or payload.get("event_count") != len(events):
        raise ValueError("三模式字幕与清单不一致。")
    if content.decode("utf-8") != _mode_caption_document(style, events, v2=v2):
        raise ValueError("三模式字幕没有使用固定样式和原始事件。")
    return payload


def generate_mode_graphics(
    task_dir: Path, timings: Sequence[SentenceTiming], plan: Sequence[MatchPlanItem],
    speakers: Sequence[Speaker], preferences: EditingPreferences, *, title: str = "",
    disclosure_intervals: Sequence[tuple[float, float]] = (),
    v2: bool = True,
    disclosure_generation: int = DISCLOSURE_GENERATION,
) -> tuple[Path | None, list[dict[str, Any]]]:
    """Fixed brown/gold lower thirds, first 2.5s, then >=60s after last showing.

    The display is bounded by the *actual* uninterrupted appearance. Short
    clips do not claim 2.5s or a fictional repeat at t=60. Unknown names stay
    '受访者'; a supplied hint is not an authenticated real identity.

    `disclosure_intervals` only label a work made with label generation 1-3,
    which studio.prepare_mode_export regenerates; generation 4 draws no label.
    """
    people = {person.id: person for person in speakers}
    timings_by_id = {timing.sentence_id: timing for timing in timings}
    appearances: list[dict[str, Any]] = []
    for item in plan:
        if item.kind != "quote" or item.source is None:
            continue
        timing = timings_by_id[item.sentence_id]
        person = people.get(item.source.speaker_id)
        key = item.source.speaker_id or f"unknown:{item.sentence_id}"
        if appearances and appearances[-1]["speaker_id"] == key and abs(appearances[-1]["end"] - timing.start) < 1e-7:
            appearances[-1]["end"] = timing.end + timing.gap_after
        else:
            appearances.append({"speaker_id": key, "start": timing.start,
                                "end": timing.end + timing.gap_after,
                                "name": person.name.strip() if person and person.name.strip() else "受访者",
                                "title": person.title.strip() if person else ""})
    receipts: list[dict[str, Any]] = []
    events: list[str] = []
    last_end: dict[str, float] = {}
    if preferences.lower_third:
        for appearance in appearances:
            key = appearance["speaker_id"]
            start = max(appearance["start"], last_end.get(key, -60.0) + LIMITS["lower_third_repeat_gap_seconds"])
            while start < appearance["end"]:
                end = min(appearance["end"], start + LIMITS["lower_third_seconds"])
                if end - start < 0.01:
                    break
                label = _sanitize(appearance["name"])
                role = _sanitize(appearance["title"])
                # Keep exact labels rather than silently truncating someone's name.
                if len(label) > 40 or len(role) > 80:
                    raise GraphicsError("人名条过长，请缩短显示姓名或身份。")
                label = label + (" · " + role if role else "")
                size = min(38, max(20, int(1150 / max(len(label), 1))))
                width = min(1240, max(300, len(label) * size + 64))
                a, b = _ass_time(start), _ass_time(end)
                events.extend([
                    f"Dialogue: 20,{a},{b},GfxBand,,0,0,0,,{{\\pos(96,794)\\an7\\1c&H26364A&\\1a&H08&\\p1}}{_rectangle(width, 72)}{{\\p0}}",
                    f"Dialogue: 21,{a},{b},GfxBand,,0,0,0,,{{\\pos(96,794)\\an7\\1c&H3CA8E2&\\p1}}{_rectangle(8, 72)}{{\\p0}}",
                    f"Dialogue: 22,{a},{b},GfxTopic,,0,0,0,,{{\\pos(120,807)\\an7\\fs{size}\\1c&HFFFFFF&\\bord0\\shad0}}{label}",
                ])
                receipts.append({**appearance, "start": start, "end": end})
                last_end[key] = end
                start = end + LIMITS["lower_third_repeat_gap_seconds"]
    total = timings[-1].end if timings else 0.0
    if preferences.news_graphics:
        # V2 never covers the last spoken words with a retrospective end card.
        events = _topic_events(title[:12], total, preferences.tone) + ([] if v2 else _end_card_events(title, total, preferences.tone)) + events
    events += _disclosure_events(disclosure_intervals, "AI生成示意画面", total, generation=disclosure_generation)
    if not events:
        return None, receipts
    destination = task_dir / GRAPHICS_FILE_NAME
    destination.write_text(_header(disclosure_generation) + "\n".join(events) + "\n", encoding="utf-8", newline="\n")
    return destination, receipts
