import re
from collections import deque


SCRIPT_SEGMENTATION_DELIMITER = " / "
_MAX_ALIGNMENT_STATES = 100_000
_LINE_END_PATTERN = re.compile(r"\r\n|\r|\n")
_NARRATION_BREAK_PUNCTUATION = frozenset("，。！？；：,.!?;:…")
_CLOSING_PUNCTUATION = frozenset("”’》」』）)]】")
_TITLE_MAX_CHARS = 40


class ScriptSegmentationValidationError(ValueError):
    """Raised when an LLM segmentation response changes the source script."""


def normalize_script_for_segmentation(source: str) -> str:
    """Return the model-facing script with platform-independent line endings."""
    normalized, _ = _normalize_line_endings_with_boundary_map(source)
    return normalized


def prepare_headline_script(source: str) -> str:
    """Validate an opt-in headline and add exactly one blank separator line.

    Inspect the actual first line before trimming, so a leading blank line is
    not silently skipped. The trimmed title is 1–40 Unicode code points (not
    UTF-16 units or graphemes). Normalize CRLF/CR to LF and trim title/body
    boundaries only; preserve internal text, punctuation and body line breaks.
    Legacy auto-format callers must not use this helper.
    """
    normalized = normalize_script_for_segmentation(source)
    lines = normalized.splitlines(keepends=True)
    title = lines[0].strip() if lines else ""
    if not title:
        raise ValueError("新闻稿首行标题不能为空，请将标题放在第一行。")
    if len(title) > _TITLE_MAX_CHARS:
        raise ValueError("新闻稿首行标题不能超过 40 个 Unicode 码点。")
    if title[-1] in "。！？.!?；;":
        raise ValueError("新闻稿首行标题不能以句末标点（。！？.!?；;）结尾。")
    body = "".join(lines[1:]).strip()
    if not body:
        raise ValueError("新闻稿标题后必须包含非空正文。")
    return f"{title}\n\n{body}"


def validate_segmented_script(source: str, segmented: str) -> list[int]:
    """Return inserted break offsets after proving that all source text is unchanged."""
    if not source.strip():
        raise ScriptSegmentationValidationError("新闻稿上屏断句输入不能为空。")
    if not segmented:
        raise ScriptSegmentationValidationError("新闻稿上屏断句响应为空。")

    break_offsets = _find_inserted_break_offsets(source, segmented)
    if len(break_offsets) != len(set(break_offsets)):
        raise ScriptSegmentationValidationError("新闻稿上屏断句响应在同一位置重复插入了断句符。")
    for offset in break_offsets:
        if (
            offset <= 0
            or offset >= len(source)
            or source[offset - 1] in "\r\n"
            or source[offset] in "\r\n"
        ):
            raise ScriptSegmentationValidationError("新闻稿上屏断句符不得位于行首、行尾或换行内部。")

    offset_set = set(break_offsets)
    for line_start, line_end in _line_content_ranges(source):
        line_breaks = sorted(offset for offset in offset_set if line_start < offset < line_end)
        if not source[line_start:line_end].strip():
            if line_breaks:
                raise ScriptSegmentationValidationError("新闻稿上屏断句响应在空白行中插入了断句符。")
            continue
        boundaries = [line_start, *line_breaks, line_end]
        if any(not source[start:end].strip() for start, end in zip(boundaries, boundaries[1:])):
            raise ScriptSegmentationValidationError("新闻稿上屏断句响应产生了空白字幕屏。")
    return break_offsets


def reconcile_segmented_script(source: str, segmented: str) -> str:
    """Rebuild a model segmentation from source offsets without trusting copied text."""
    if not source.strip():
        raise ScriptSegmentationValidationError("新闻稿上屏断句输入不能为空。")
    if not segmented:
        raise ScriptSegmentationValidationError("新闻稿上屏断句响应为空。")

    normalized_source, source_boundary_map = _normalize_line_endings_with_boundary_map(source)
    normalized_segmented = normalize_script_for_segmentation(segmented)
    try:
        normalized_offsets = validate_segmented_script(normalized_source, normalized_segmented)
    except ScriptSegmentationValidationError:
        # Models commonly render a break at an existing title separator as
        # " / ", consuming that one ASCII space. Align that representation to
        # a source offset, then reconstruct from the source so the space is kept.
        normalized_offsets = _find_break_offsets_allowing_replaced_spaces(
            normalized_source,
            normalized_segmented,
        )
        normalized_candidate = _insert_break_delimiters(normalized_source, normalized_offsets)
        validate_segmented_script(normalized_source, normalized_candidate)

    source_offsets = [source_boundary_map[offset] for offset in normalized_offsets]
    reconciled = _insert_break_delimiters(source, source_offsets)
    validate_segmented_script(source, reconciled)
    return coalesce_unsafe_narration_breaks(source, reconciled)


def coalesce_unsafe_narration_breaks(source: str, segmented: str) -> str:
    """Remove body breaks that can switch shots before a spoken phrase is complete."""
    break_offsets = validate_segmented_script(source, segmented)
    title_range = _title_line_range(source)
    safe_offsets = [
        offset
        for offset in break_offsets
        if (
            title_range is not None
            and title_range[0] < offset < title_range[1]
        )
        or _has_prosodic_boundary(source, offset)
    ]
    safe_segmented = _insert_break_delimiters(source, safe_offsets)
    validate_segmented_script(source, safe_segmented)
    return safe_segmented


def _has_prosodic_boundary(source: str, offset: int) -> bool:
    index = offset - 1
    while index >= 0 and source[index].isspace() and source[index] not in "\r\n":
        index -= 1
    while index >= 0 and source[index] in _CLOSING_PUNCTUATION:
        index -= 1
    return index >= 0 and source[index] in _NARRATION_BREAK_PUNCTUATION


def _title_line_range(source: str) -> tuple[int, int] | None:
    raw_lines = source.splitlines(keepends=True)
    line_records: list[tuple[int, int, int, str]] = []
    absolute_start = 0
    for line_index, raw_line in enumerate(raw_lines):
        line_text = raw_line.rstrip("\r\n")
        if line_text.strip():
            line_records.append(
                (line_index, absolute_start, absolute_start + len(line_text), line_text.strip())
            )
        absolute_start += len(raw_line)
    if len(line_records) <= 1:
        return None

    first_line_index, first_start, first_end, first_text = line_records[0]
    next_text = line_records[1][3]
    followed_by_blank = (
        first_line_index + 1 < len(raw_lines)
        and not raw_lines[first_line_index + 1].strip()
    )
    next_looks_like_body = (
        len(next_text) > len(first_text)
        and bool(re.search(r"[。！？；]", next_text))
    )
    if (
        (followed_by_blank or next_looks_like_body)
        and len(first_text) <= _TITLE_MAX_CHARS
        and not re.search(r"[。！？；]$", first_text)
    ):
        return first_start, first_end
    return None


def _find_inserted_break_offsets(source: str, segmented: str) -> list[int]:
    delimiter = SCRIPT_SEGMENTATION_DELIMITER
    length_delta = len(segmented) - len(source)
    if length_delta < 0 or length_delta % len(delimiter) != 0:
        raise ScriptSegmentationValidationError("新闻稿上屏断句响应增删改了原文。")

    if delimiter not in source:
        if segmented.replace(delimiter, "") != source:
            raise ScriptSegmentationValidationError("新闻稿上屏断句响应增删改了原文。")
        offsets: list[int] = []
        source_index = 0
        segmented_index = 0
        while segmented_index < len(segmented):
            if segmented.startswith(delimiter, segmented_index):
                offsets.append(source_index)
                segmented_index += len(delimiter)
                continue
            source_index += 1
            segmented_index += 1
        return offsets

    # Existing literal " / " text makes delimiter ownership ambiguous. Align both
    # strings exactly so original delimiters remain text and only insertions become
    # break offsets.
    start = (0, 0)
    goal = (len(source), len(segmented))
    queue: deque[tuple[int, int]] = deque([start])
    parents: dict[
        tuple[int, int],
        tuple[tuple[int, int], int | None] | None,
    ] = {start: None}

    while queue:
        source_index, segmented_index = queue.popleft()
        state = (source_index, segmented_index)
        if state == goal:
            break
        remaining_extra = (len(segmented) - segmented_index) - (len(source) - source_index)
        if remaining_extra < 0 or remaining_extra % len(delimiter) != 0:
            continue

        transitions: list[tuple[tuple[int, int], int | None]] = []
        if (
            source_index < len(source)
            and segmented_index < len(segmented)
            and source[source_index] == segmented[segmented_index]
        ):
            transitions.append(((source_index + 1, segmented_index + 1), None))
        if remaining_extra > 0 and segmented.startswith(delimiter, segmented_index):
            transitions.append(((source_index, segmented_index + len(delimiter)), source_index))

        for next_state, inserted_at in transitions:
            if next_state in parents:
                continue
            parents[next_state] = (state, inserted_at)
            queue.append(next_state)
            if len(parents) > _MAX_ALIGNMENT_STATES:
                raise ScriptSegmentationValidationError("新闻稿中的斜杠结构过于复杂，无法安全校验断句结果。")

    if goal not in parents:
        raise ScriptSegmentationValidationError("新闻稿上屏断句响应增删改了原文。")

    offsets: list[int] = []
    current = goal
    while current != start:
        parent = parents[current]
        if parent is None:
            raise ScriptSegmentationValidationError("新闻稿上屏断句响应无法完成一致性校验。")
        previous, inserted_at = parent
        if inserted_at is not None:
            offsets.append(inserted_at)
        current = previous
    offsets.reverse()
    return offsets


def _find_break_offsets_allowing_replaced_spaces(source: str, segmented: str) -> list[int]:
    """Align output while allowing a delimiter to stand for one source ASCII space."""
    if len(segmented) < len(source):
        raise ScriptSegmentationValidationError("新闻稿上屏断句响应增删改了原文。")

    delimiter = SCRIPT_SEGMENTATION_DELIMITER
    start = (0, 0)
    goal = (len(source), len(segmented))
    queue: deque[tuple[int, int]] = deque([start])
    parents: dict[
        tuple[int, int],
        tuple[tuple[int, int], int | None] | None,
    ] = {start: None}

    while queue:
        source_index, segmented_index = queue.popleft()
        state = (source_index, segmented_index)
        if state == goal:
            break
        if len(segmented) - segmented_index < len(source) - source_index:
            continue

        transitions: list[tuple[tuple[int, int], int | None]] = []
        if (
            source_index < len(source)
            and segmented_index < len(segmented)
            and source[source_index] == segmented[segmented_index]
        ):
            transitions.append(((source_index + 1, segmented_index + 1), None))
        if segmented.startswith(delimiter, segmented_index):
            transitions.append(
                ((source_index, segmented_index + len(delimiter)), source_index)
            )
            if source_index < len(source) and source[source_index] == " ":
                transitions.append(
                    ((source_index + 1, segmented_index + len(delimiter)), source_index)
                )

        for next_state, inserted_at in transitions:
            if next_state in parents:
                continue
            parents[next_state] = (state, inserted_at)
            queue.append(next_state)
            if len(parents) > _MAX_ALIGNMENT_STATES:
                raise ScriptSegmentationValidationError(
                    "新闻稿中的空格或斜杠结构过于复杂，无法安全校验断句结果。"
                )

    if goal not in parents:
        raise ScriptSegmentationValidationError("新闻稿上屏断句响应增删改了原文。")

    offsets: list[int] = []
    current = goal
    while current != start:
        parent = parents[current]
        if parent is None:
            raise ScriptSegmentationValidationError("新闻稿上屏断句响应无法完成一致性校验。")
        previous, inserted_at = parent
        if inserted_at is not None:
            offsets.append(inserted_at)
        current = previous
    offsets.reverse()
    return offsets


def _insert_break_delimiters(source: str, offsets: list[int]) -> str:
    pieces: list[str] = []
    cursor = 0
    for offset in offsets:
        pieces.append(source[cursor:offset])
        pieces.append(SCRIPT_SEGMENTATION_DELIMITER)
        cursor = offset
    pieces.append(source[cursor:])
    return "".join(pieces)


def _normalize_line_endings_with_boundary_map(text: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    boundary_map = [0]
    index = 0
    while index < len(text):
        if text.startswith("\r\n", index):
            normalized.append("\n")
            index += 2
        elif text[index] == "\r":
            normalized.append("\n")
            index += 1
        else:
            normalized.append(text[index])
            index += 1
        boundary_map.append(index)
    return "".join(normalized), boundary_map


def _line_content_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for match in _LINE_END_PATTERN.finditer(text):
        ranges.append((start, match.start()))
        start = match.end()
    ranges.append((start, len(text)))
    return ranges