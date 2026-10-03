"""News graphics package rendered as a SEPARATE ASS overlay (``graphics.ass``).

This layer is intentionally independent from the validated dialogue subtitle
template (``subs.ass`` / ``subtitle_manifest.json``): it is burned as a second
``ass`` filter after the subtitles and never mutates the hashed subtitle
artifacts. It can contain the optional top-left news package and mandatory,
time-bounded generated-media disclosures. The opening title ("片头") is already
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

_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
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
    "Style: GfxDisclosure,Noto Sans SC,30,&H00FFFFFF&,&H00FFFFFF&,&H00101418&,&H80141018&,-1,0,0,0,100,100,0,0,3,1,0,9,40,40,40,1\n\n"
    "[Events]\n"
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


def _disclosure_events(
    intervals: Sequence[tuple[float, float]],
    text: str,
) -> list[str]:
    label = _sanitize(text)
    if not label:
        return []
    events: list[str] = []
    for start_time, end_time in intervals:
        if start_time < 0 or end_time <= start_time:
            raise GraphicsError("AI 生成画面披露时间范围无效。")
        events.append(
            f"Dialogue: 10,{_ass_time(start_time)},{_ass_time(end_time)},"
            f"GfxDisclosure,,0,0,0,,{{\\an9\\pos(1870,40)\\bord1.5\\shad1}}{label}"
        )
    return events


def generate_news_graphics(
    task_dir: Path,
    *,
    topic: str,
    headline: str,
    total_duration: float,
    accent: str = "neutral",
    output_path: Path | None = None,
    disclosure_intervals: Sequence[tuple[float, float]] = (),
    disclosure_text: str = "AI生成示意画面",
) -> Path | None:
    """Write one ASS overlay for optional news graphics and mandatory disclosures."""
    events = (
        _topic_events(topic, total_duration, accent)
        + _end_card_events(headline, total_duration, accent)
        + _disclosure_events(disclosure_intervals, disclosure_text)
    )
    if not events:
        return None
    destination = output_path or task_dir / GRAPHICS_FILE_NAME
    destination.write_text(_HEADER + "\n".join(events) + "\n", encoding="utf-8")
    write_text_log(
        task_dir,
        f"新闻图文包装已生成 accent={accent} topic={'有' if topic.strip() else '无'} "
        f"end_card={'有' if headline.strip() else '无'} "
        f"generated_disclosures={len(disclosure_intervals)}",
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
) -> tuple[Path | None, list[dict[str, Any]]]:
    """Fixed brown/gold lower thirds, first 2.5s, then >=60s after last showing.

    The display is bounded by the *actual* uninterrupted appearance. Short
    clips do not claim 2.5s or a fictional repeat at t=60. Unknown names stay
    '受访者'; a supplied hint is not an authenticated real identity.
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
    events += _disclosure_events(disclosure_intervals, "AI生成示意画面")
    if not events:
        return None, receipts
    destination = task_dir / GRAPHICS_FILE_NAME
    destination.write_text(_HEADER + "\n".join(events) + "\n", encoding="utf-8", newline="\n")
    return destination, receipts
