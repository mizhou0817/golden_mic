import asyncio
import json
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from .assignment import CapacityOption, solve_capacity_assignment
from .media import run_logged_command
from .models import (
    AnnotatedShot,
    BeatMatch,
    MatchCandidate,
    MatchPlanItem,
    RerankDecision,
    ScriptDocument,
    Sentence,
    VisualEntityVerification,
    VisualBeat,
)
from .script_segmentation import coalesce_unsafe_narration_breaks, validate_segmented_script
from .storage import write_json_atomic, write_text_log


MatchProgressCallback = Callable[[float, str], None]
SENTENCE_END_PATTERN = re.compile(r"[^。！？；]+[。！？；]?")
SCRIPT_TITLE_MAX_CHARS = 40
NARRATION_TARGET_CHARS = 42
NARRATION_HARD_MAX_CHARS = 60
NARRATION_MIN_CLAUSE_CHARS = 12
MAX_VISUAL_BEATS = 3
# Broadcast news cuts roughly every 3s. At ~4.25 chars/s that is ~13-14 chars per shot,
# so a long multi-clause narration unit is split into several visual beats for tighter pacing.
BEAT_TARGET_CHARS = 14
BEAT_DENSIFY_MIN_CHARS = 16
MIN_SHOT_REUSE_LIMIT = 2
SHOT_REUSE_COOLDOWN_BEATS = 2
MAX_DIVERSITY_SCORE_LOSS = 0.05
_QUOTE_PAIRS = {"“": "”", "‘": "’", "《": "》"}
_VISUAL_STOP_TERMS = {
    "本次活动",
    "活动现场",
    "现场",
    "市民",
    "众多",
    "一应俱全",
    "引来众多品尝者",
    "科技感十足",
    "特色小吃",
    "人气十足",
    "十足",
    "欢迎",
    "正浓",
    "涌动",
    "亮相",
}


class EmbeddingClient(Protocol):
    def validate_configuration(self) -> None:
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class RerankClient(Protocol):
    def validate_configuration(self) -> None:
        ...

    async def rerank(self, items: Sequence[dict[str, Any]], *, editing_brief: str = "") -> list[RerankDecision]:
        ...


class EntityVerifier(Protocol):
    async def verify_visual_entities(
        self,
        image_path: Path,
        visual_beat: str,
        entities: list[str],
    ) -> VisualEntityVerification:
        ...


class MatchingError(RuntimeError):
    """Raised when semantic matching cannot produce a complete plan."""


class ShotShortageError(MatchingError):
    """Not enough distinct usable shots; the user can trim the script or add footage."""

    error_kind = "shortage"


@dataclass
class _BeatAssignment:
    sentence_id: int
    visual_group_id: int
    decision_confidence: float
    match: BeatMatch


def recommended_shot_reuse_limit(assignment_count: int, available_shot_count: int) -> int:
    """Return a balanced per-shot budget while still allowing limited reuse."""
    if assignment_count <= 0:
        return 0
    if available_shot_count <= 0:
        return assignment_count
    return min(
        assignment_count,
        max(MIN_SHOT_REUSE_LIMIT, math.ceil(assignment_count / available_shot_count)),
    )


def parse_script(script: str) -> ScriptDocument:
    _, title, _, body_lines = _script_layout(script)

    pieces: list[tuple[int, str]] = []
    for paragraph_index, (_, paragraph) in enumerate(body_lines):
        for match in SENTENCE_END_PATTERN.finditer(paragraph):
            sentence = match.group(0).strip()
            if sentence:
                pieces.extend((paragraph_index, part) for part in _split_long_narration_sentence(sentence))

    merged: list[tuple[int, str]] = []
    for paragraph_index, piece in pieces:
        if len(piece) < 6 and merged:
            previous_paragraph, previous_text = merged[-1]
            merged[-1] = (previous_paragraph, previous_text + piece)
        else:
            merged.append((paragraph_index, piece))
    if not merged:
        raise MatchingError("新闻稿正文分句后没有有效内容。")

    sentences = [
        Sentence(
            sentence_id=sentence_id,
            text=text,
            paragraph_index=paragraph_index,
            visual_beats=extract_visual_beats(text),
            retrieval_context=text,
            visual_group_id=sentence_id,
        )
        for sentence_id, (paragraph_index, text) in enumerate(merged)
    ]
    return ScriptDocument(title=title, sentences=sentences)


def parse_segmented_script(script: str, segmented_script: str) -> ScriptDocument:
    segmented_script = coalesce_unsafe_narration_breaks(script, segmented_script)
    break_offsets = validate_segmented_script(script, segmented_script)
    _, title, _, body_lines = _script_layout(script)
    paragraph_by_line = {
        line_index: paragraph_index
        for paragraph_index, (line_index, _) in enumerate(body_lines)
    }

    pieces: list[tuple[int, str, int, str]] = []
    absolute_start = 0
    next_visual_group_id = 0
    for line_index, raw_line in enumerate(script.splitlines(keepends=True)):
        line_text = raw_line.rstrip("\r\n")
        line_end = absolute_start + len(line_text)
        paragraph_index = paragraph_by_line.get(line_index)
        if paragraph_index is not None:
            line_breaks = [
                offset
                for offset in break_offsets
                if absolute_start < offset < line_end
            ]
            boundaries = [absolute_start, *line_breaks, line_end]
            natural_sentences = _natural_sentence_ranges(line_text, absolute_start)
            for start, end in zip(boundaries, boundaries[1:]):
                text = script[start:end].strip()
                if not text:
                    raise MatchingError("新闻稿上屏断句后产生了空白播音单元。")
                natural_index, retrieval_context = next(
                    (index, natural_text)
                    for index, (natural_start, natural_end, natural_text) in enumerate(natural_sentences)
                    if start < natural_end and end > natural_start
                )
                pieces.append(
                    (
                        paragraph_index,
                        text,
                        next_visual_group_id + natural_index,
                        retrieval_context,
                    )
                )
            next_visual_group_id += len(natural_sentences)
        absolute_start += len(raw_line)

    if not pieces:
        raise MatchingError("新闻稿正文上屏断句后没有有效内容。")
    sentences = [
        Sentence(
            sentence_id=sentence_id,
            text=text,
            paragraph_index=paragraph_index,
            visual_beats=extract_visual_beats(text),
            retrieval_context=retrieval_context,
            visual_group_id=visual_group_id,
        )
        for sentence_id, (paragraph_index, text, visual_group_id, retrieval_context) in enumerate(pieces)
    ]
    return ScriptDocument(title=title, sentences=sentences)


def _natural_sentence_ranges(text: str, absolute_start: int) -> list[tuple[int, int, str]]:
    ranges = [
        (
            absolute_start + match.start(),
            absolute_start + match.end(),
            match.group(0).strip(),
        )
        for match in SENTENCE_END_PATTERN.finditer(text)
        if match.group(0).strip()
    ]
    return ranges or [(absolute_start, absolute_start + len(text), text.strip())]


def split_script(script: str) -> list[Sentence]:
    return parse_script(script).sentences


def _script_layout(
    script: str,
) -> tuple[list[str], str | None, int | None, list[tuple[int, str]]]:
    raw_lines = script.splitlines()
    nonempty = [(line_index, line.strip()) for line_index, line in enumerate(raw_lines) if line.strip()]
    if not nonempty:
        raise MatchingError("新闻稿分句后没有有效内容。")

    title: str | None = None
    title_line_index: int | None = None
    body_lines = nonempty
    first_line_index, first_text = nonempty[0]
    followed_by_blank = first_line_index + 1 < len(raw_lines) and not raw_lines[first_line_index + 1].strip()
    next_looks_like_body = (
        len(nonempty) > 1
        and len(nonempty[1][1]) > len(first_text)
        and bool(re.search(r"[。！？；]", nonempty[1][1]))
    )
    if (
        len(nonempty) > 1
        and (followed_by_blank or next_looks_like_body)
        and len(first_text) <= SCRIPT_TITLE_MAX_CHARS
        and not re.search(r"[。！？；]$", first_text)
    ):
        title = first_text
        title_line_index = first_line_index
        body_lines = nonempty[1:]
    return raw_lines, title, title_line_index, body_lines


def _split_long_narration_sentence(sentence: str) -> list[str]:
    if len(sentence) <= NARRATION_HARD_MAX_CHARS or "，" not in sentence:
        return [sentence]
    clauses = _split_top_level_commas(sentence)
    if len(clauses) <= 1:
        return [sentence]

    groups: list[str] = []
    current = ""
    for clause in clauses:
        if not current:
            current = clause
            continue
        combined = current + clause
        if len(combined) <= NARRATION_TARGET_CHARS or (
            len(current) < NARRATION_MIN_CLAUSE_CHARS and len(combined) <= NARRATION_HARD_MAX_CHARS
        ):
            current = combined
        else:
            groups.append(current)
            current = clause
    if current:
        groups.append(current)
    if len(groups) > 1 and len(groups[-1]) < 8 and len(groups[-2] + groups[-1]) <= NARRATION_HARD_MAX_CHARS:
        groups[-2] += groups.pop()
    return groups


def _split_top_level_commas(text: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    quote_stack: list[str] = []
    for index, character in enumerate(text):
        if character in _QUOTE_PAIRS:
            quote_stack.append(_QUOTE_PAIRS[character])
        elif quote_stack and character == quote_stack[-1]:
            quote_stack.pop()
        elif character == "，" and not quote_stack:
            pieces.append(text[start : index + 1])
            start = index + 1
    if start < len(text):
        pieces.append(text[start:])
    return [piece.strip() for piece in pieces if piece.strip()]


def _densify_clause_beats(clauses: list[str], full_text: str) -> list[str]:
    """Split a non-enumeration narration unit into several beats for broadcast-paced cutting.

    Short or single-clause units keep exactly one beat (historical behaviour). Longer
    multi-clause units are grouped into balanced beats of roughly ``BEAT_TARGET_CHARS``.
    """
    joined = full_text.strip()
    meaningful = [clause for clause in clauses if clause]
    total = sum(len(clause) for clause in meaningful)
    if len(meaningful) <= 1:
        return _split_long_single_clause_beats(joined)
    if total < BEAT_DENSIFY_MIN_CHARS:
        return [joined]
    target_groups = min(
        MAX_VISUAL_BEATS,
        len(meaningful),
        max(2, round(total / BEAT_TARGET_CHARS)),
    )
    if target_groups <= 1:
        return [joined]
    per_group = math.ceil(total / target_groups)
    groups: list[str] = []
    current: list[str] = []
    current_len = 0
    for clause in meaningful:
        if current and current_len + len(clause) > per_group and len(groups) < target_groups - 1:
            groups.append("，".join(current))
            current = [clause]
            current_len = len(clause)
        else:
            current.append(clause)
            current_len += len(clause)
    if current:
        groups.append("，".join(current))
    groups = [group for group in groups if len(group) >= 4]
    return groups[:MAX_VISUAL_BEATS] or [joined]


def _split_long_single_clause_beats(text: str) -> list[str]:
    normalized = text.strip("，。！？； ")
    if len(normalized) < 24:
        return [text.strip()]

    # Split only at semantics-preserving anchors. This keeps the narration and
    # subtitle sentence intact while giving matching/rendering several visual
    # intents for a long address/date/event sentence.
    candidate_boundaries: list[int] = []
    for pattern in (
        r"\d{1,2}月\d{1,2}日(?=举办|举行|启动|开幕|开展)",
        r"(?<=广场)(?=\d{1,2}月)",
        r"(?<=中心)(?=\d{1,2}月)",
        r"(?<=信息港)(?=\d{1,2}月)",
        r"(?<=举办了)(?=[“\"])",
        r"(?<=举行了)(?=[“\"])",
        r"(?<=启动了)(?=[“\"])",
        r"(?<=[”\"])(?=迎春|活动|市集|展会|论坛)",
    ):
        candidate_boundaries.extend(match.start() for match in re.finditer(pattern, normalized))
    boundaries = sorted(
        boundary
        for boundary in set(candidate_boundaries)
        if 8 <= boundary <= len(normalized) - 6
    )
    if not boundaries:
        # Last-resort visual-only split near the midpoint. Avoid splitting a
        # date, number+unit, quoted phrase, or address suffix.
        midpoint = len(normalized) // 2
        safe_boundaries = [
            index
            for index in range(8, len(normalized) - 5)
            if normalized[index - 1] not in "年月日号的地得和与及或、"
            and normalized[index] not in "年月日号的地得了着过等们、"
            and normalized[:index].count("“") == normalized[:index].count("”")
            and normalized[:index].count('"') % 2 == 0
        ]
        if safe_boundaries:
            boundaries = [min(safe_boundaries, key=lambda value: abs(value - midpoint))]
    if not boundaries:
        return [text.strip()]

    target_groups = min(MAX_VISUAL_BEATS, max(2, round(len(normalized) / BEAT_TARGET_CHARS)))
    selected: list[int] = []
    for target_index in range(1, target_groups):
        target = round(len(normalized) * target_index / target_groups)
        available = [boundary for boundary in boundaries if boundary not in selected]
        if not available:
            break
        selected.append(min(available, key=lambda value: abs(value - target)))
    cuts = [0, *sorted(selected), len(normalized)]
    groups = [
        normalized[start:end].strip("，。！？； ")
        for start, end in zip(cuts, cuts[1:])
        if end - start >= 4
    ]
    return groups if len(groups) >= 2 else [text.strip()]


def extract_visual_beats(text: str) -> list[VisualBeat]:
    clauses = [clause.strip("，。！？； ") for clause in _split_top_level_commas(text)]
    beat_texts: list[str] = []
    enumeration_indexes = [
        index
        for index, clause in enumerate(clauses)
        if "、" in clause and ("如" in clause or "等" in clause)
    ]
    for clause_index in enumeration_indexes:
        clause = clauses[clause_index]
        if clause_index > 0 and not beat_texts:
            prefix = "，".join(clauses[:clause_index]).strip()
            if len(prefix) >= 6:
                beat_texts.append(prefix)
        if len(beat_texts) >= MAX_VISUAL_BEATS:
            continue
        list_start = clause.rfind("如")
        list_text = clause[list_start + 1 :] if list_start >= 0 else clause
        list_text = re.split(r"等(?:特色|各类|商品|产品|小吃|年货)?", list_text, maxsplit=1)[0]
        items = [_clean_visual_entity(item) for item in list_text.split("、")]
        items = [item for item in items if len(item) >= 2]
        if len(items) < 2:
            continue
        available_slots = min(MAX_VISUAL_BEATS - len(beat_texts), len(items))
        group_size = math.ceil(len(items) / available_slots)
        beat_texts.extend("、".join(items[index : index + group_size]) for index in range(0, len(items), group_size))

    if not beat_texts:
        beat_texts = _densify_clause_beats(clauses, text)
    beat_texts = beat_texts[:MAX_VISUAL_BEATS]
    explicit_enumeration = bool(enumeration_indexes)
    beats: list[VisualBeat] = []
    for index, beat_text in enumerate(beat_texts):
        intent_type = _classify_visual_intent(beat_text, explicit_enumeration)
        beats.append(
            VisualBeat(
                beat_id=index,
                text=beat_text,
                entities=extract_retrieval_terms(beat_text),
                requires_entity_coverage=explicit_enumeration or intent_type == "entity",
                intent_type=intent_type,
            )
        )
    return beats


def _classify_visual_intent(
    text: str,
    explicit_entity: bool = False,
) -> Literal["general", "entity", "organization", "date", "abstract"]:
    normalized = text.strip("，。！？； ")
    if re.search(r"(?:持续到|截至|截止|活动时间|报名时间|结束于|\d{1,2}月\d{1,2}日)", normalized):
        return "date"
    if re.search(r"(?:主办|协办|承办|指导单位|支持单位)", normalized):
        return "organization"
    if re.search(r"(?:融合|纽带|打造|平台|意义|助力|推动|赋能|氛围|丰富多样)", normalized):
        return "abstract"
    if explicit_entity:
        return "entity"
    if re.search(
        r"(?:新能源汽车|汽车|车辆|油茶|寿司|腊肉|糕点|酒类|礼盒|年货|商品|食品|"
        r"无人机|机器人|展板|入口|[\u4e00-\u9fff]{2,}(?:公司|集团|银行|医院|学校|中心|信息港|广场|市集))",
        normalized,
    ):
        return "entity"
    return "general"


def extract_retrieval_terms(text: str) -> list[str]:
    normalized = re.sub(r"[“”‘’《》()（）]", "", text)
    # Connector words split "腊肉腊肠等传统年货" into "腊肉腊肠" and "传统年货", so a clip described
    # as "烟熏腊肠" can still be recognised by "腊肠" instead of needing the whole clause verbatim.
    raw_terms = [term.strip() for term in re.split(r"[\s，。！？；：、,/]+|等|和|与|及|的|也|在|同样|一直", normalized) if term.strip()]
    terms: list[str] = []
    for raw_term in raw_terms:
        term = _clean_visual_entity(raw_term)
        if len(term) < 2 or term in _VISUAL_STOP_TERMS:
            continue
        for candidate in (term, term[-2:] if len(term) > 2 else term):
            if candidate not in terms and candidate not in _VISUAL_STOP_TERMS:
                terms.append(candidate)
    return terms[:12]


def _clean_visual_entity(value: str) -> str:
    cleaned = value.strip("，。！？；：、 等")
    cleaned = re.sub(r"^(?:各类|传统年货|特色小吃|包括|包含|例如|近\d+款)", "", cleaned)
    cleaned = re.sub(r"(?:引来.*|一应俱全|科技感十足)$", "", cleaned)
    return cleaned.strip()


def write_sentences(task_dir: Path, sentences: Sequence[Sentence]) -> None:
    (task_dir / "sentences.json").write_text(
        json.dumps([sentence.model_dump(mode="json") for sentence in sentences], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_script_document(task_dir: Path, document: ScriptDocument) -> None:
    (task_dir / "script_structure.json").write_text(
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def build_match_plan(
    task_dir: Path,
    sentences: Sequence[Sentence],
    shots: Sequence[AnnotatedShot],
    embedding_provider: EmbeddingClient,
    llm_provider: RerankClient,
    progress: MatchProgressCallback,
    *,
    top_k: int = 10,
    lexical_rescue_k: int = 4,
    video_embedding_enabled: bool = False,
    video_embedding_candidate_top_k: int = 15,
    video_embedding_concurrency: int = 4,
    entity_verifier: EntityVerifier | None = None,
    entity_verification_max_shots: int = 6,
    entity_verification_min_confidence: float = 0.75,
    excluded_shot_ids: set[int] | None = None,
    editing_brief: str = "",
    output_path: Path | None = None,
    shot_reuse_window_seconds: float | None = None,
    shot_reuse_best_effort: bool = False,
) -> list[MatchPlanItem]:
    if not sentences:
        raise MatchingError("没有可供匹配的文稿句子。")
    excluded_ids = excluded_shot_ids or set()
    available_shots = [
        shot
        for shot in shots
        if shot.status == "available"
        and shot.description
        and shot.quality is not None
        and shot.shot_id not in excluded_ids
    ]
    if not available_shots:
        if excluded_ids:
            raise MatchingError("没有不同于当前画面的可用镜头。")
        raise MatchingError("没有可供语义匹配的可用镜头。")
    embedding_provider.validate_configuration()
    llm_provider.validate_configuration()
    if top_k < 1 or lexical_rescue_k < 0:
        raise MatchingError("语义召回数量配置无效。")
    if video_embedding_candidate_top_k < top_k:
        raise MatchingError("视频向量粗召回数量不能小于最终稠密召回数量。")

    query_units = [
        (sentence, beat)
        for sentence in sentences
        for beat in (sentence.visual_beats or extract_visual_beats(sentence.text))
    ]
    query_texts = [
        f"自然句上下文：{sentence.retrieval_context or sentence.text} 当前播音单元：{sentence.text} "
        f"视觉节拍：{beat.text} 视觉意图：{beat.intent_type}"
        + (f" 必须覆盖实体：{'、'.join(beat.entities)}" if beat.entities else "")
        for sentence, beat in query_units
    ]
    shot_texts = [_shot_retrieval_text(shot) for shot in available_shots]
    text_query_vectors = await _embed_for_role(embedding_provider, query_texts, role="query")
    sentence_vectors = text_query_vectors
    progress(0.15, f"已生成 {len(sentence_vectors)} 个视觉节拍查询向量")
    text_shot_vectors = await _embed_for_role(embedding_provider, shot_texts, role="corpus")
    shot_vectors = text_shot_vectors
    _validate_embedding_dimensions(text_query_vectors, text_shot_vectors)
    progress(0.3, f"已生成 {len(shot_vectors)} 个镜头文本/OCR/ASR 向量")

    coarse_candidate_ids_by_beat: dict[tuple[int, int], set[int]] = {}
    coarse_candidate_ids_by_group: dict[int, set[int]] = {}
    verified_evidence_by_beat: dict[tuple[int, int], dict[int, VisualEntityVerification]] = {}
    video_candidate_shot_ids: set[int] = set()
    for (sentence, beat), query_vector in zip(query_units, text_query_vectors, strict=True):
        ranked = _rank_retrieval_candidates(
            query_vector,
            available_shots,
            text_shot_vectors,
            shot_texts,
            beat.entities or extract_retrieval_terms(beat.text),
            dense_k=video_embedding_candidate_top_k,
            lexical_rescue_k=lexical_rescue_k,
            intent_type=beat.intent_type,
        )
        key = (sentence.sentence_id, beat.beat_id)
        candidate_ids = {candidate.shot_id for candidate in ranked}
        coarse_candidate_ids_by_beat[key] = candidate_ids
        group_id = sentence.visual_group_id if sentence.visual_group_id is not None else sentence.sentence_id
        coarse_candidate_ids_by_group.setdefault(group_id, set()).update(candidate_ids)
    if entity_verifier is not None and entity_verification_max_shots > 0:
        verified_evidence_by_beat = await _verify_missing_entity_candidates(
            task_dir,
            query_units,
            text_query_vectors,
            available_shots,
            text_shot_vectors,
            shot_texts,
            entity_verifier,
            max_shots=entity_verification_max_shots,
            minimum_confidence=entity_verification_min_confidence,
        )
        sentence_by_id = {sentence.sentence_id: sentence for sentence in sentences}
        for (sentence_id, beat_id), evidence_by_shot in verified_evidence_by_beat.items():
            sentence = sentence_by_id[sentence_id]
            group_id = sentence.visual_group_id if sentence.visual_group_id is not None else sentence.sentence_id
            verified_ids = set(evidence_by_shot)
            coarse_candidate_ids_by_beat[(sentence_id, beat_id)].update(verified_ids)
            coarse_candidate_ids_by_group.setdefault(group_id, set()).update(verified_ids)
    for sentence, beat in query_units:
        group_id = sentence.visual_group_id if sentence.visual_group_id is not None else sentence.sentence_id
        group_ids = coarse_candidate_ids_by_group[group_id]
        coarse_candidate_ids_by_beat[(sentence.sentence_id, beat.beat_id)].update(group_ids)
        video_candidate_shot_ids.update(group_ids)
    progress(
        0.38,
        f"文本/OCR/ASR/词法粗召回完成，筛选 {len(video_candidate_shot_ids)}/{len(available_shots)} 个视频候选",
    )

    candidate_limited_video_retrieval = False
    if video_embedding_enabled:
        video_query_method = getattr(embedding_provider, "embed_video_queries", None)
        video_corpus_method = getattr(embedding_provider, "embed_video_corpus", None)
        if callable(video_query_method) and callable(video_corpus_method):
            video_query = cast(
                Callable[[Sequence[str]], Awaitable[list[list[float]]]],
                video_query_method,
            )
            video_corpus = cast(
                Callable[
                    [Sequence[tuple[Path, str]], Callable[[int, int], None]],
                    Awaitable[list[list[float] | None]],
                ],
                video_corpus_method,
            )
            video_query_vectors = await video_query(query_texts)
            sentence_vectors = [
                _blend_vectors(text_vector, video_vector, second_weight=0.5)
                for text_vector, video_vector in zip(text_query_vectors, video_query_vectors, strict=True)
            ]
            candidate_indexes = [
                index
                for index, shot in enumerate(available_shots)
                if shot.shot_id in video_candidate_shot_ids
            ]
            candidate_shots = [available_shots[index] for index in candidate_indexes]
            clips = await _prepare_embedding_clips(
                task_dir,
                candidate_shots,
                concurrency=video_embedding_concurrency,
            )
            valid_indexes = [
                source_index
                for source_index, path in zip(candidate_indexes, clips, strict=True)
                if path is not None
            ]
            clips_by_index = {
                source_index: path
                for source_index, path in zip(candidate_indexes, clips, strict=True)
                if path is not None
            }
            cache_key = _video_embedding_cache_key(embedding_provider)
            media_by_index = _load_video_embedding_cache(
                task_dir,
                available_shots,
                cache_key,
            )
            cached_indexes = set(media_by_index)
            missing_indexes = [index for index in valid_indexes if index not in media_by_index]
            media_items = [(clips_by_index[index], shot_texts[index]) for index in missing_indexes]
            def video_progress(completed: int, total: int) -> None:
                progress(
                    0.45 + 0.17 * completed / total,
                    f"候选视频向量 {completed}/{total}",
                )

            media_vectors = (
                await video_corpus(media_items, video_progress)
                if media_items
                else []
            )
            media_by_index.update(
                {
                    source_index: vector
                    for source_index, vector in zip(missing_indexes, media_vectors, strict=True)
                    if vector is not None
                }
            )
            _write_video_embedding_cache(task_dir, available_shots, cache_key, media_by_index)
            shot_vectors = [
                _blend_vectors(text_vector, media_by_index[index], second_weight=0.65)
                if index in media_by_index
                else text_vector
                for index, text_vector in enumerate(text_shot_vectors)
            ]
            candidate_limited_video_retrieval = True
            write_json_atomic(
                task_dir / "video_embedding_selection.json",
                {
                    "schema_version": 1,
                    "strategy": "text_ocr_asr_lexical_candidates",
                    "available_shot_count": len(available_shots),
                    "selected_shot_count": len(candidate_indexes),
                    "selected_shot_ids": [available_shots[index].shot_id for index in candidate_indexes],
                    "coarse_top_k": video_embedding_candidate_top_k,
                    "lexical_rescue_k": lexical_rescue_k,
                    "cache_hit_count": sum(index in cached_indexes for index in candidate_indexes),
                    "requested_count": len(missing_indexes),
                    "successful_video_vector_count": sum(index in media_by_index for index in candidate_indexes),
                },
            )
            write_text_log(
                task_dir,
                f"候选级直接视频 Embedding 完成 selected={len(candidate_indexes)}/{len(available_shots)} "
                f"requested={len(missing_indexes)} vectors={sum(index in media_by_index for index in candidate_indexes)}，"
                "失败或无视频镜头回退文本向量",
            )
        else:
            write_text_log(task_dir, "当前 Embedding Provider 不支持视频输入，已回退文本向量")
    progress(0.62, f"候选级视频融合完成，共 {len(video_candidate_shot_ids)} 个粗召回候选")

    _validate_embedding_dimensions(sentence_vectors, shot_vectors)
    candidates_by_beat: dict[tuple[int, int], list[MatchCandidate]] = {}
    rerank_items: list[dict[str, Any]] = []
    shots_by_id = {shot.shot_id: shot for shot in available_shots}
    opening_sentence_id = sentences[0].sentence_id if len(sentences) > 1 else None
    closing_sentence_id = sentences[-1].sentence_id if len(sentences) > 1 else None
    for (sentence, beat), sentence_vector in zip(query_units, sentence_vectors, strict=True):
        key = (sentence.sentence_id, beat.beat_id)
        ranked = _rank_retrieval_candidates(
            sentence_vector,
            available_shots,
            shot_vectors,
            shot_texts,
            beat.entities or extract_retrieval_terms(beat.text),
            dense_k=top_k,
            lexical_rescue_k=lexical_rescue_k,
            intent_type=beat.intent_type,
            allowed_shot_ids=(
                coarse_candidate_ids_by_beat[key]
                if candidate_limited_video_retrieval
                else None
            ),
            verified_evidence=verified_evidence_by_beat.get(key),
        )
        candidates_by_beat[key] = ranked
        rerank_items.append(
            {
                "sentence_id": sentence.sentence_id,
                "beat_id": beat.beat_id,
                "visual_group_id": (
                    sentence.visual_group_id
                    if sentence.visual_group_id is not None
                    else sentence.sentence_id
                ),
                "sentence_text": sentence.text,
                "retrieval_context": sentence.retrieval_context or sentence.text,
                "visual_beat": beat.text,
                "intent_type": beat.intent_type,
                "structural_role": (
                    "opening"
                    if sentence.sentence_id == opening_sentence_id and beat.beat_id == 0
                    else "closing"
                    if sentence.sentence_id == closing_sentence_id
                    else "body"
                ),
                "entities": beat.entities,
                "candidates": [
                    {
                        "shot_id": candidate.shot_id,
                        "description": shots_by_id[candidate.shot_id].description,
                        "keywords": shots_by_id[candidate.shot_id].keywords,
                        "ocr_texts": shots_by_id[candidate.shot_id].ocr_texts,
                        "source_transcript": shots_by_id[candidate.shot_id].source_transcript,
                        "similarity": candidate.similarity,
                        "lexical_score": candidate.lexical_score,
                        "combined_score": candidate.combined_score,
                        "matched_terms": candidate.matched_terms,
                        "description_score": candidate.description_score,
                        "entity_score": candidate.entity_score,
                        "ocr_score": candidate.ocr_score,
                        "asr_score": candidate.asr_score,
                        "action_score": candidate.action_score,
                        "verified_terms": candidate.verified_terms,
                        "verification_confidence": candidate.verification_confidence,
                        "verification_evidence": candidate.verification_evidence,
                        "preferred_in_time": candidate.preferred_in_time,
                    }
                    for candidate in ranked
                ],
            }
        )
    progress(0.65, "混合召回完成，正在按视觉节拍进行一次性 LLM 批量精排")

    decisions = (
        await llm_provider.rerank(rerank_items, editing_brief=editing_brief)
        if editing_brief.strip()
        else await llm_provider.rerank(rerank_items)
    )
    progress(0.85, f"LLM 已一次性精排 {len(decisions)} 个视觉节拍")
    plan = apply_beat_decisions(
        sentences, available_shots, candidates_by_beat, decisions,
        shot_reuse_window_seconds=shot_reuse_window_seconds,
        shot_reuse_best_effort=shot_reuse_best_effort,
    )
    write_json_atomic(
        output_path or task_dir / "match_plan.json",
        [item.model_dump(mode="json") for item in plan],
    )
    for item in plan:
        write_text_log(
            task_dir,
            f"句子 {item.sentence_id} -> 镜头 {item.shot_id} confidence={item.confidence:.3f} fallback={item.is_fallback}",
        )
    progress(1.0, f"语义匹配完成，共 {len(plan)} 个句子")
    return plan


async def _embed_for_role(
    provider: EmbeddingClient,
    texts: Sequence[str],
    *,
    role: str,
) -> list[list[float]]:
    method_name = "embed_queries" if role == "query" else "embed_corpus"
    method = getattr(provider, method_name, None)
    if callable(method):
        typed_method = cast(Callable[[Sequence[str]], Awaitable[list[list[float]]]], method)
        return await typed_method(texts)
    return await provider.embed(texts)


async def _verify_missing_entity_candidates(
    task_dir: Path,
    query_units: Sequence[tuple[Sentence, VisualBeat]],
    query_vectors: Sequence[Sequence[float]],
    shots: Sequence[AnnotatedShot],
    shot_vectors: Sequence[Sequence[float]],
    shot_texts: Sequence[str],
    verifier: EntityVerifier,
    *,
    max_shots: int,
    minimum_confidence: float,
) -> dict[tuple[int, int], dict[int, VisualEntityVerification]]:
    remaining = max(0, max_shots)
    verified_by_beat: dict[tuple[int, int], dict[int, VisualEntityVerification]] = {}
    attempts: list[dict[str, Any]] = []
    for (sentence, beat), query_vector in zip(query_units, query_vectors, strict=True):
        if remaining <= 0:
            break
        terms = beat.entities or extract_retrieval_terms(beat.text)
        if not beat.requires_entity_coverage or not terms:
            continue
        if any(_lexical_match(terms, shot_text)[0] > 0 for shot_text in shot_texts):
            continue
        shortlist = _entity_verification_shortlist(
            beat,
            query_vector,
            shots,
            shot_vectors,
            shot_texts,
            limit=remaining,
            task_dir=task_dir,
        )
        if not shortlist:
            continue
        remaining -= len(shortlist)
        key = (sentence.sentence_id, beat.beat_id)

        async def verify(
            shot: AnnotatedShot,
        ) -> tuple[AnnotatedShot, VisualEntityVerification | None, str | None]:
            sheet = task_dir / "thumbs" / "contact_sheets" / f"shot_{shot.shot_id}.jpg"
            try:
                result = await verifier.verify_visual_entities(sheet, beat.text, list(terms))
                return shot, result, None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return shot, None, str(exc)[:500]

        results = await asyncio.gather(*(verify(shot) for shot in shortlist))
        for shot, result, error in results:
            sanitized_terms = (
                _sanitize_verified_terms(result.matched_entities, terms)
                if result is not None
                else []
            )
            accepted = bool(
                result is not None
                and result.verified
                and result.confidence >= minimum_confidence
                and sanitized_terms
            )
            attempts.append(
                {
                    "sentence_id": sentence.sentence_id,
                    "beat_id": beat.beat_id,
                    "visual_beat": beat.text,
                    "shot_id": shot.shot_id,
                    "accepted": accepted,
                    "confidence": result.confidence if result is not None else None,
                    "matched_entities": sanitized_terms,
                    "evidence": result.evidence if result is not None else None,
                    "preferred_relative_time": (
                        result.preferred_relative_time if result is not None else None
                    ),
                    "error": error,
                }
            )
            if accepted and result is not None:
                verified_by_beat.setdefault(key, {})[shot.shot_id] = result.model_copy(
                    update={"matched_entities": sanitized_terms}
                )

    write_json_atomic(
        task_dir / "entity_verification.json",
        {
            "schema_version": 1,
            "task_budget": max_shots,
            "minimum_confidence": minimum_confidence,
            "attempt_count": len(attempts),
            "accepted_count": sum(bool(item["accepted"]) for item in attempts),
            "attempts": attempts,
        },
    )
    if attempts:
        write_text_log(
            task_dir,
            f"显式实体定向画面复核 attempts={len(attempts)} "
            f"accepted={sum(bool(item['accepted']) for item in attempts)}",
        )
    return verified_by_beat


def _entity_verification_shortlist(
    beat: VisualBeat,
    query_vector: Sequence[float],
    shots: Sequence[AnnotatedShot],
    shot_vectors: Sequence[Sequence[float]],
    shot_texts: Sequence[str],
    *,
    limit: int,
    task_dir: Path,
) -> list[AnnotatedShot]:
    target = " ".join([beat.text, *beat.entities])
    category_terms = _visual_category_terms(target)
    ranked: list[tuple[int, float, float, int, AnnotatedShot]] = []
    for shot, vector, shot_text in zip(shots, shot_vectors, shot_texts, strict=True):
        sheet = task_dir / "thumbs" / "contact_sheets" / f"shot_{shot.shot_id}.jpg"
        if not sheet.is_file():
            continue
        normalized_shot = re.sub(r"\s+", "", shot_text.lower())
        category_hits = sum(term in normalized_shot for term in category_terms)
        quality = (shot.quality.sharp * shot.quality.bright) if shot.quality else 0.0
        ranked.append(
            (
                category_hits,
                cosine_similarity(query_vector, vector),
                quality,
                -shot.shot_id,
                shot,
            )
        )
    ranked.sort(key=lambda item: item[:4], reverse=True)
    return [item[4] for item in ranked[: max(0, limit)]]


def _visual_category_terms(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    categories: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
        (
            ("油茶", "寿司", "小吃", "腊肉", "糕点", "酒", "礼盒", "年货", "食品", "茶"),
            ("制作", "食品", "小吃", "餐", "食材", "配料", "盛汤", "汤", "锅", "碗", "饮品", "试吃", "摊位"),
        ),
        (
            ("汽车", "车辆", "新能源", "车展"),
            ("汽车", "车辆", "suv", "轿车", "停车场", "车展", "展车", "试驾"),
        ),
        (
            ("市民", "群众", "人流", "品尝者", "顾客"),
            ("市民", "群众", "顾客", "人群", "排队", "驻足", "选购", "交流"),
        ),
    ]
    for triggers, terms in categories:
        if any(trigger in normalized for trigger in triggers):
            return list(terms)
    return ["活动", "现场", "展示", "摊位", "人员"]


def _sanitize_verified_terms(values: Sequence[str], expected: Sequence[str]) -> list[str]:
    expected_by_normalized = {
        re.sub(r"\s+", "", value.lower()): value
        for value in expected
        if len(value.strip()) >= 2
    }
    result: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", "", value.lower())
        matched = expected_by_normalized.get(normalized)
        if matched is not None and matched not in result:
            result.append(matched)
    return result


def _rank_retrieval_candidates(
    query_vector: Sequence[float],
    shots: Sequence[AnnotatedShot],
    shot_vectors: Sequence[Sequence[float]],
    shot_texts: Sequence[str],
    terms: Sequence[str],
    *,
    dense_k: int,
    lexical_rescue_k: int,
    intent_type: str = "general",
    allowed_shot_ids: set[int] | None = None,
    verified_evidence: dict[int, VisualEntityVerification] | None = None,
) -> list[MatchCandidate]:
    if len(shots) != len(shot_vectors) or len(shots) != len(shot_texts):
        raise MatchingError("镜头、向量和检索文本数量不一致。")
    term_weights = _retrieval_term_weights(terms, shot_texts)
    all_candidates: list[MatchCandidate] = []
    for shot, shot_vector in zip(shots, shot_vectors, strict=True):
        if allowed_shot_ids is not None and shot.shot_id not in allowed_shot_ids:
            continue
        similarity = cosine_similarity(query_vector, shot_vector)
        lexical_score, matched_terms, field_scores, temporal_start = _fielded_lexical_match(
            term_weights,
            shot,
            intent_type=intent_type,
        )
        evidence_weight = 0.35 if intent_type in {"entity", "organization", "date"} else 0.24
        combined_score = max(
            -1.0,
            min(1.0, (1.0 - evidence_weight) * similarity + evidence_weight * lexical_score),
        )
        verification = (verified_evidence or {}).get(shot.shot_id)
        if verification is not None:
            combined_score = max(
                combined_score,
                min(1.0, 0.45 + 0.4 * verification.confidence),
            )
        all_candidates.append(
            MatchCandidate(
                shot_id=shot.shot_id,
                similarity=round(similarity, 6),
                lexical_score=round(lexical_score, 6),
                combined_score=round(combined_score, 6),
                matched_terms=matched_terms,
                description_score=round(field_scores["description"], 6),
                entity_score=round(field_scores["entity"], 6),
                ocr_score=round(field_scores["ocr"], 6),
                asr_score=round(field_scores["asr"], 6),
                action_score=round(field_scores["action"], 6),
                verified_terms=(verification.matched_entities if verification is not None else []),
                verification_confidence=(verification.confidence if verification is not None else None),
                verification_evidence=(verification.evidence if verification is not None else None),
                preferred_in_time=(
                    min(
                        shot.end,
                        max(
                            shot.start,
                            shot.start + verification.preferred_relative_time - 0.4,
                        ),
                    )
                    if verification is not None and verification.preferred_relative_time is not None
                    else temporal_start
                ),
            )
        )
    if not all_candidates:
        raise MatchingError("粗召回候选池为空。")
    dense = sorted(
        all_candidates,
        key=lambda candidate: (-candidate.similarity, candidate.shot_id),
    )[:dense_k]
    dense_ids = {candidate.shot_id for candidate in dense}
    lexical_rescue = [
        candidate
        for candidate in sorted(
            all_candidates,
            key=lambda candidate: (-candidate.lexical_score, -candidate.similarity, candidate.shot_id),
        )
        if candidate.lexical_score > 0 and candidate.shot_id not in dense_ids
    ][:lexical_rescue_k]
    selected_ids = {candidate.shot_id for candidate in [*dense, *lexical_rescue]}
    verified_rescue = [
        candidate
        for candidate in all_candidates
        if candidate.verified_terms and candidate.shot_id not in selected_ids
    ]
    return sorted(
        [*dense, *lexical_rescue, *verified_rescue],
        key=lambda candidate: (-candidate.combined_score, -candidate.similarity, candidate.shot_id),
    )


async def _prepare_embedding_clips(
    task_dir: Path,
    shots: Sequence[AnnotatedShot],
    *,
    concurrency: int,
) -> list[Path | None]:
    output_dir = task_dir / "embedding_clips"
    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def prepare(shot: AnnotatedShot) -> Path | None:
        output_path = output_dir / f"shot_{shot.shot_id}.mp4"
        if output_path.is_file() and output_path.stat().st_size > 0:
            return output_path
        source_path = (task_dir / shot.norm_path).resolve()
        if not source_path.is_relative_to(task_dir.resolve()) or not source_path.is_file():
            return None
        async with semaphore:
            try:
                await run_logged_command(
                    [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        f"{shot.start:.6f}",
                        "-i",
                        str(source_path),
                        "-t",
                        f"{shot.duration:.6f}",
                        "-map",
                        "0:v:0",
                        "-vf",
                        "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=10",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-crf",
                        "28",
                        "-pix_fmt",
                        "yuv420p",
                        "-an",
                        "-movflags",
                        "+faststart",
                        str(output_path),
                    ],
                    task_dir,
                    f"准备视频 Embedding 镜头 shot={shot.shot_id}",
                )
                return output_path if output_path.is_file() and output_path.stat().st_size > 0 else None
            except Exception as exc:
                output_path.unlink(missing_ok=True)
                write_text_log(task_dir, f"视频 Embedding 镜头 {shot.shot_id} 准备失败，回退文本：{exc}")
                return None

    return await asyncio.gather(*(prepare(shot) for shot in shots))


def _video_embedding_cache_key(provider: EmbeddingClient) -> str:
    values = (
        str(getattr(provider, "model", "")),
        str(getattr(provider, "dimensions", "")),
        str(getattr(provider, "video_fps", "")),
        str(getattr(provider, "video_max_tokens", "")),
    )
    return "|".join(values)


def _load_video_embedding_cache(
    task_dir: Path,
    shots: Sequence[AnnotatedShot],
    cache_key: str,
) -> dict[int, list[float]]:
    path = task_dir / "video_embeddings.json"
    if not path.is_file():
        return {}
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw_payload, dict):
            return {}
        payload = cast(dict[str, object], raw_payload)
        if payload.get("cache_key") != cache_key:
            return {}
        raw_vectors = payload.get("vectors")
        if not isinstance(raw_vectors, dict):
            return {}
        vectors = cast(dict[str, object], raw_vectors)
        shot_index_by_id = {shot.shot_id: index for index, shot in enumerate(shots)}
        result: dict[int, list[float]] = {}
        for raw_shot_id, raw_vector in vectors.items():
            shot_id = int(raw_shot_id)
            index = shot_index_by_id.get(shot_id)
            if index is None or not isinstance(raw_vector, list):
                continue
            typed_vector = cast(list[object], raw_vector)
            if any(not isinstance(value, (int, float)) for value in typed_vector):
                continue
            vector = [float(value) for value in typed_vector if isinstance(value, (int, float))]
            if vector and all(math.isfinite(value) for value in vector):
                result[index] = vector
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _write_video_embedding_cache(
    task_dir: Path,
    shots: Sequence[AnnotatedShot],
    cache_key: str,
    vectors_by_index: dict[int, list[float]],
) -> None:
    write_json_atomic(
        task_dir / "video_embeddings.json",
        {
            "schema_version": 1,
            "cache_key": cache_key,
            "vectors": {
                str(shots[index].shot_id): vector
                for index, vector in sorted(vectors_by_index.items())
                if 0 <= index < len(shots)
            },
        },
    )


def _blend_vectors(
    first: Sequence[float],
    second: Sequence[float],
    *,
    second_weight: float,
) -> list[float]:
    if len(first) != len(second) or not first:
        raise MatchingError("待融合的文本与视频向量维度不一致。")
    weight = max(0.0, min(1.0, second_weight))
    blended = [(1.0 - weight) * left + weight * right for left, right in zip(first, second, strict=True)]
    norm = math.sqrt(sum(value * value for value in blended))
    return [value / norm for value in blended] if norm else list(blended)


def _shot_retrieval_text(shot: AnnotatedShot) -> str:
    if shot.search_text.strip():
        return shot.search_text.strip()
    parts = [
        shot.description or "",
        " ".join(shot.subjects),
        " ".join(shot.actions),
        " ".join(shot.keywords),
        " ".join(shot.ocr_texts),
        " ".join(shot.entities),
        shot.source_transcript,
    ]
    return " ".join(part for part in parts if part).strip()


def _lexical_match(terms: Sequence[str], shot_text: str) -> tuple[float, list[str]]:
    normalized_shot = re.sub(r"\s+", "", shot_text.lower())
    normalized_terms = [re.sub(r"\s+", "", term.lower()) for term in terms if len(term.strip()) >= 2]
    if not normalized_terms:
        return 0.0, []
    matched = [term for term in normalized_terms if term in normalized_shot]
    return len(matched) / len(normalized_terms), matched


def _retrieval_term_weights(
    terms: Sequence[str],
    shot_texts: Sequence[str],
) -> dict[str, float]:
    normalized_terms = list(
        dict.fromkeys(
            re.sub(r"\s+", "", term.lower())
            for term in terms
            if len(term.strip()) >= 2
        )
    )
    normalized_shots = [re.sub(r"\s+", "", text.lower()) for text in shot_texts]
    weights: dict[str, float] = {}
    for term in normalized_terms:
        document_frequency = sum(term in shot_text for shot_text in normalized_shots)
        inverse_frequency = 1.0 + math.log((len(normalized_shots) + 1) / (document_frequency + 1))
        specificity = min(2.5, max(1.0, len(term) / 2.0))
        weights[term] = inverse_frequency * specificity
    return weights


def _fielded_lexical_match(
    term_weights: dict[str, float],
    shot: AnnotatedShot,
    *,
    intent_type: str,
) -> tuple[float, list[str], dict[str, float], float | None]:
    fields = {
        "description": " ".join([shot.description or "", *shot.keywords]),
        "entity": " ".join([*shot.entities, *shot.subjects]),
        "ocr": " ".join(shot.ocr_texts),
        "asr": shot.source_transcript,
        "action": " ".join(shot.actions),
    }
    scores = {
        name: _weighted_term_coverage(term_weights, text)
        for name, text in fields.items()
    }
    weights_by_intent = {
        "entity": {"description": 0.12, "entity": 0.31, "ocr": 0.21, "asr": 0.27, "action": 0.09},
        "organization": {"description": 0.16, "entity": 0.29, "ocr": 0.38, "asr": 0.11, "action": 0.06},
        "date": {"description": 0.15, "entity": 0.20, "ocr": 0.35, "asr": 0.20, "action": 0.10},
        "abstract": {"description": 0.31, "entity": 0.18, "ocr": 0.12, "asr": 0.14, "action": 0.25},
        "general": {"description": 0.29, "entity": 0.24, "ocr": 0.16, "asr": 0.16, "action": 0.15},
    }
    field_weights = weights_by_intent.get(intent_type, weights_by_intent["general"])
    score = sum(scores[name] * field_weights[name] for name in fields)
    supplemental_score = _weighted_term_coverage(term_weights, shot.search_text)
    score = max(score, 0.2 * supplemental_score)
    matched = [
        term
        for term in term_weights
        if any(term in re.sub(r"\s+", "", text.lower()) for text in fields.values())
        or term in re.sub(r"\s+", "", shot.search_text.lower())
    ]
    return min(1.0, score), matched, scores, _preferred_asr_in_time(term_weights, shot)


def _weighted_term_coverage(term_weights: dict[str, float], text: str) -> float:
    if not term_weights:
        return 0.0
    normalized = re.sub(r"\s+", "", text.lower())
    total_weight = sum(term_weights.values())
    matched_weight = sum(weight for term, weight in term_weights.items() if term in normalized)
    return matched_weight / total_weight if total_weight else 0.0


def _preferred_asr_in_time(
    term_weights: dict[str, float],
    shot: AnnotatedShot,
) -> float | None:
    best: tuple[float, float] | None = None
    for span in shot.source_transcript_spans:
        normalized = re.sub(r"\s+", "", span.text.lower())
        matched = [
            (term, weight)
            for term, weight in term_weights.items()
            if term in normalized
        ]
        if not matched or not normalized:
            continue
        matched_weight = sum(weight for _, weight in matched)
        anchor_term = max(matched, key=lambda item: (item[1], len(item[0])))[0]
        position = normalized.find(anchor_term)
        ratio = max(0.0, position) / max(1, len(normalized))
        evidence_time = span.start + ratio * (span.end - span.start)
        preferred = max(shot.start, evidence_time - 0.4)
        candidate = (matched_weight, preferred)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return round(best[1], 6) if best is not None else None


def apply_beat_decisions(
    sentences: Sequence[Sentence],
    shots: Sequence[AnnotatedShot],
    candidates_by_beat: dict[tuple[int, int], list[MatchCandidate]],
    decisions: Sequence[RerankDecision],
    *,
    shot_reuse_window_seconds: float | None = None,
    shot_reuse_best_effort: bool = False,
) -> list[MatchPlanItem]:
    expected_keys = [
        (sentence.sentence_id, beat.beat_id)
        for sentence in sentences
        for beat in (sentence.visual_beats or extract_visual_beats(sentence.text))
    ]
    decision_keys = [(decision.sentence_id, decision.beat_id) for decision in decisions]
    if decision_keys != expected_keys:
        raise MatchingError("LLM 精排结果与视觉节拍列表不一致。")
    shot_ids = {shot.shot_id for shot in shots}
    quality_order = sorted(
        shots,
        key=lambda shot: (
            -((shot.quality.sharp * shot.quality.bright) if shot.quality else 0.0),
            shot.shot_id,
        ),
    )
    used_shots = {
        decision.shot_id
        for decision in decisions
        if decision.shot_id is not None and decision.shot_id in shot_ids and decision.confidence >= 0.35
    }
    assignments: list[_BeatAssignment] = []
    sentence_by_id = {sentence.sentence_id: sentence for sentence in sentences}
    for decision in decisions:
        sentence = sentence_by_id[decision.sentence_id]
        beat = next(
            beat
            for beat in (sentence.visual_beats or extract_visual_beats(sentence.text))
            if beat.beat_id == decision.beat_id
        )
        candidates = candidates_by_beat[(decision.sentence_id, decision.beat_id)]
        selected = decision.shot_id
        is_fallback = selected is None or selected not in shot_ids or decision.confidence < 0.35
        if is_fallback:
            selected = next(
                (candidate.shot_id for candidate in candidates if candidate.shot_id not in used_shots),
                candidates[0].shot_id if candidates else quality_order[0].shot_id,
            )
            used_shots.add(selected)
        assert selected is not None
        selected_candidate = next(
            (candidate for candidate in candidates if candidate.shot_id == selected),
            None,
        )
        effective_confidence = _calibrated_match_confidence(
            decision.confidence,
            selected_candidate,
            candidates,
        )
        entity_supported = (
            not beat.requires_entity_coverage
            or bool(
                selected_candidate
                and (selected_candidate.matched_terms or selected_candidate.verified_terms)
            )
        )
        if not entity_supported:
            effective_confidence = min(effective_confidence, 0.34)
            is_fallback = True
        alternates = [
            shot_id
            for shot_id in decision.alternates
            if shot_id != selected and shot_id in shot_ids
        ]
        assignments.append(
            _BeatAssignment(
                sentence_id=decision.sentence_id,
                visual_group_id=(
                    sentence.visual_group_id
                    if sentence.visual_group_id is not None
                    else sentence.sentence_id
                ),
                decision_confidence=decision.confidence,
                match=BeatMatch(
                    beat_id=beat.beat_id,
                    text=beat.text,
                    entities=beat.entities,
                    requires_entity_coverage=beat.requires_entity_coverage,
                    shot_id=selected,
                    confidence=effective_confidence,
                    alternates=alternates,
                    is_fallback=is_fallback,
                    candidates=candidates,
                    intent_type=beat.intent_type,
                    visual_group_id=(
                        sentence.visual_group_id
                        if sentence.visual_group_id is not None
                        else sentence.sentence_id
                    ),
                    preferred_in_time=(
                        selected_candidate.preferred_in_time
                        if selected_candidate is not None
                        else None
                    ),
                ),
            )
        )

    assignments = _diversify_beat_assignments(assignments, len(shots))
    assignments = _enforce_global_unique_shots(
        assignments, shots, reuse_window_seconds=shot_reuse_window_seconds,
        best_effort=shot_reuse_best_effort,
    )
    beats_by_sentence: dict[int, list[BeatMatch]] = {
        sentence.sentence_id: [] for sentence in sentences
    }
    for assignment in assignments:
        beats_by_sentence[assignment.sentence_id].append(assignment.match)

    plan: list[MatchPlanItem] = []
    for sentence in sentences:
        beat_matches = beats_by_sentence[sentence.sentence_id]
        if not beat_matches:
            raise MatchingError(f"句子 {sentence.sentence_id} 没有视觉节拍匹配结果。")
        primary = beat_matches[0]
        overlay_kind = _contextual_overlay_kind(primary)
        plan.append(
            MatchPlanItem(
                sentence_id=sentence.sentence_id,
                text=sentence.text,
                shot_id=primary.shot_id,
                confidence=min(match.confidence for match in beat_matches),
                alternates=list(dict.fromkeys(
                    shot_id
                    for match in beat_matches
                    for shot_id in match.alternates
                    if shot_id != primary.shot_id
                )),
                is_fallback=any(match.is_fallback for match in beat_matches),
                candidates=primary.candidates,
                beat_matches=beat_matches,
                visual_group_id=(
                    sentence.visual_group_id
                    if sentence.visual_group_id is not None
                    else sentence.sentence_id
                ),
                overlay_text=sentence.text if overlay_kind is not None else None,
                overlay_kind=overlay_kind,
            )
        )
    return plan


def _contextual_overlay_kind(
    match: BeatMatch,
) -> Literal["date", "organization", "abstract"] | None:
    if match.intent_type == "date":
        return "date"
    if match.intent_type == "organization":
        return "organization"
    selected_candidate = next(
        (candidate for candidate in match.candidates if candidate.shot_id == match.shot_id),
        None,
    )
    has_direct_evidence = bool(
        selected_candidate
        and (selected_candidate.matched_terms or selected_candidate.verified_terms)
    )
    if match.intent_type == "abstract" and (match.confidence < 0.5 or not has_direct_evidence):
        return "abstract"
    return None


def _diversify_beat_assignments(
    assignments: Sequence[_BeatAssignment],
    available_shot_count: int,
) -> list[_BeatAssignment]:
    diversified = list(assignments)
    if len(diversified) <= 1 or available_shot_count <= 1:
        return diversified

    visual_group_count = len({item.visual_group_id for item in diversified})
    reuse_limit = recommended_shot_reuse_limit(visual_group_count, available_shot_count)

    for _ in range(len(diversified) * len(diversified)):
        usage = _group_shot_usage(diversified)
        overused = {shot_id for shot_id, count in usage.items() if count > reuse_limit}
        if not overused:
            break
        replacements: list[tuple[float, float, int, MatchCandidate]] = []
        for index, assignment in enumerate(diversified):
            if assignment.match.shot_id not in overused:
                continue
            candidate = _best_diversity_replacement(
                diversified,
                index,
                usage,
                reuse_limit,
            )
            if candidate is None:
                continue
            replacements.append(
                (
                    _replacement_penalty(assignment.match, candidate),
                    assignment.match.confidence,
                    index,
                    candidate,
                )
            )
        if not replacements:
            break
        _, _, index, candidate = min(
            replacements,
            key=lambda item: (item[0], item[1], item[2], item[3].shot_id),
        )
        _replace_assignment(diversified[index], candidate)

    for _ in range(len(diversified) * len(diversified)):
        usage = _group_shot_usage(diversified)
        collision_indexes: set[int] = set()
        for right in range(1, len(diversified)):
            left_boundary = max(0, right - SHOT_REUSE_COOLDOWN_BEATS)
            for left in range(left_boundary, right):
                if (
                    diversified[left].visual_group_id != diversified[right].visual_group_id
                    and diversified[left].match.shot_id == diversified[right].match.shot_id
                ):
                    collision_indexes.update((left, right))
        if not collision_indexes:
            break

        replacements = []
        for index in sorted(collision_indexes):
            candidate = _best_diversity_replacement(
                diversified,
                index,
                usage,
                reuse_limit,
            )
            if candidate is None:
                continue
            assignment = diversified[index]
            replacements.append(
                (
                    _replacement_penalty(assignment.match, candidate),
                    assignment.match.confidence,
                    index,
                    candidate,
                )
            )
        if not replacements:
            break
        _, _, index, candidate = min(
            replacements,
            key=lambda item: (item[0], item[1], item[2], item[3].shot_id),
        )
        _replace_assignment(diversified[index], candidate)

    return diversified


def _enforce_global_unique_shots(
    assignments: list[_BeatAssignment],
    shots: Sequence[AnnotatedShot],
    *,
    reuse_window_seconds: float | None = None,
    best_effort: bool = False,
) -> list[_BeatAssignment]:
    """Guarantee every beat maps to a distinct shot (matches EDL/QC capacity 1).

    A no-op when the greedy diversifier already produced globally distinct shots. Otherwise
    it re-solves a strict one-to-one minimum-cost assignment over each beat's own candidate
    pool, honouring entity coverage, and raises when no such assignment exists.
    """
    shot_id_list = [assignment.match.shot_id for assignment in assignments]
    if len(shot_id_list) == len(set(shot_id_list)):
        return assignments
    shot_id_set = {shot.shot_id for shot in shots}
    options_by_item: list[list[CapacityOption]] = []
    for assignment in assignments:
        match = assignment.match
        entity_required = match.requires_entity_coverage
        options: list[CapacityOption] = []
        for candidate in match.candidates:
            if candidate.shot_id not in shot_id_set:
                continue
            if entity_required and not (candidate.matched_terms or candidate.verified_terms):
                continue
            utility = _diversity_candidate_score(candidate)
            if candidate.shot_id == match.shot_id:
                utility += 0.0005
            options.append(CapacityOption(resource_id=candidate.shot_id, utility=utility))
        options_by_item.append(options)
    capacities = {shot_id: 1 for shot_id in shot_id_set}
    solved = solve_capacity_assignment(options_by_item, capacities)
    reuse_capacities: dict[int, int] | None = None
    if solved is None and reuse_window_seconds:
        # The user accepted repeated footage. A shot may serve several beats, but only
        # as many as it has non-overlapping windows of the needed length; the caller
        # then cuts such a shot into that many distinct windows (distinct shot ids), so
        # no downstream uniqueness rule is relaxed. Related shots are reused before any
        # unrelated shot is borrowed.
        reuse_capacities = {
            shot.shot_id: max(1, int(shot.duration // reuse_window_seconds)) for shot in shots
        }
        solved = solve_capacity_assignment(options_by_item, reuse_capacities)
    widened = False
    if solved is None:
        # Each beat only ranks a short candidate list, so overlapping lists can be
        # infeasible even when there are enough distinct shots overall. Widen every
        # beat that is not entity-bound to the remaining shots at a clearly lower
        # utility: they are used only when needed, and are flagged as low-confidence
        # fallbacks (never presented as a confident match) so they stay reviewable.
        widened = True
        for assignment, options in zip(assignments, options_by_item, strict=True):
            if assignment.match.requires_entity_coverage:
                continue
            known = {option.resource_id for option in options}
            floor = min((option.utility for option in options), default=0.0) - 1.0
            options.extend(
                CapacityOption(resource_id=shot_id, utility=floor)
                for shot_id in sorted(shot_id_set - known)
            )
        solved = solve_capacity_assignment(options_by_item, reuse_capacities or capacities)
    if solved is None and best_effort:
        # Last resort, only after the user accepted repeated footage: every beat may take any shot, in any
        # number. Related shots still win (their utility is higher) and use is spread by the solver's reuse
        # penalty; whatever is borrowed from outside a beat's own candidates is flagged as a low-confidence
        # fallback. The caller then lays the footage out so no frame is frozen or invented.
        widened = True
        for assignment, options in zip(assignments, options_by_item, strict=True):
            known = {option.resource_id for option in options}
            # The beat's own semantic candidates that only failed the strict entity wording rule
            # still beat unrelated footage by far: "腊肉腊肠" should land on the cured-meat clip.
            own = [candidate for candidate in assignment.match.candidates
                   if candidate.shot_id in shot_id_set and candidate.shot_id not in known]
            options.extend(
                CapacityOption(resource_id=candidate.shot_id, utility=_diversity_candidate_score(candidate) - 0.5)
                for candidate in own
            )
            known |= {candidate.shot_id for candidate in own}
            # Far below: repeating related footage (reuse penalty below) always wins over unrelated footage.
            floor = min((option.utility for option in options), default=0.0) - 100.0
            options.extend(
                CapacityOption(resource_id=shot_id, utility=floor)
                for shot_id in sorted(shot_id_set - known)
            )
        # Repeats only when they clearly pay off: distinct footage first whenever there is enough.
        solved = solve_capacity_assignment(
            options_by_item, {shot_id: len(assignments) for shot_id in shot_id_set}, reuse_penalty=0.35,
        )
    if solved is None:
        if reuse_capacities is not None:
            raise ShotShortageError(
                f"镜头不足：即使允许重复使用，现有素材的总时长也只够 {sum(reuse_capacities.values())} 段画面，"
                f"而需要 {len(assignments)} 段。请删减稿子，或多传几个视频/更长的素材。"
            )
        if len(shot_id_set) < len(assignments):
            raise ShotShortageError(
                f"镜头不足，无法形成一对一全局分配：需要 {len(assignments)} 个互不重复的画面，"
                f"但只有 {len(shot_id_set)} 个可用镜头。请删减稿子，或多传几个不同场景的视频。"
            )
        raise ShotShortageError(
            "视觉节拍无法形成一对一全局分配，无法保证每个镜头只使用一次：镜头数量够，"
            "但提到具体人物/地点的句子在素材里找不到足够多互不重复的对应画面（镜头不足以对应）。"
            "请删减或改写这些句子，或补传拍到这些人物/地点的视频。"
        )
    for assignment, shot_id in zip(assignments, solved, strict=True):
        if assignment.match.shot_id == shot_id:
            continue
        candidate = next(
            (item for item in assignment.match.candidates if item.shot_id == shot_id),
            None,
        )
        if candidate is None and widened:
            candidate = MatchCandidate(shot_id=shot_id, similarity=0.0)
            assignment.match = assignment.match.model_copy(
                update={"candidates": [*assignment.match.candidates, candidate]}
            )
        if candidate is not None:
            _replace_assignment(assignment, candidate)
            entity_unmet = assignment.match.requires_entity_coverage and not (candidate.matched_terms or candidate.verified_terms)
            if widened and (entity_unmet or candidate.combined_score == 0.0 and candidate.similarity == 0.0):
                assignment.match = assignment.match.model_copy(
                    update={"is_fallback": True, "confidence": min(assignment.match.confidence, 0.34)}
                )
    return assignments


def _group_shot_usage(assignments: Sequence[_BeatAssignment]) -> Counter[int]:
    return Counter(
        shot_id
        for _, shot_id in {
            (assignment.visual_group_id, assignment.match.shot_id)
            for assignment in assignments
        }
    )


def _best_diversity_replacement(
    assignments: Sequence[_BeatAssignment],
    index: int,
    usage: Counter[int],
    reuse_limit: int,
) -> MatchCandidate | None:
    current = assignments[index].match
    selected_candidate = next(
        (candidate for candidate in current.candidates if candidate.shot_id == current.shot_id),
        None,
    )
    selected_score = (
        _diversity_candidate_score(selected_candidate)
        if selected_candidate is not None
        else current.confidence
    )
    options: list[MatchCandidate] = []
    for candidate in current.candidates:
        if candidate.shot_id == current.shot_id:
            continue
        group_already_uses_candidate = any(
            other_index != index
            and other.visual_group_id == assignments[index].visual_group_id
            and other.match.shot_id == candidate.shot_id
            for other_index, other in enumerate(assignments)
        )
        if usage[candidate.shot_id] >= reuse_limit and not group_already_uses_candidate:
            continue
        if current.requires_entity_coverage and not candidate.matched_terms and not candidate.verified_terms:
            continue
        candidate_score = _diversity_candidate_score(candidate)
        if candidate_score + 1e-9 < selected_score - MAX_DIVERSITY_SCORE_LOSS:
            continue
        if any(
            other_index != index
            and other.visual_group_id != assignments[index].visual_group_id
            and abs(other_index - index) <= SHOT_REUSE_COOLDOWN_BEATS
            and other.match.shot_id == candidate.shot_id
            for other_index, other in enumerate(assignments)
        ):
            continue
        options.append(candidate)
    if not options:
        return None
    return max(
        options,
        key=lambda candidate: (
            _diversity_candidate_score(candidate) - 0.015 * usage[candidate.shot_id],
            candidate.lexical_score,
            candidate.similarity,
            -candidate.shot_id,
        ),
    )


def _replacement_penalty(match: BeatMatch, candidate: MatchCandidate) -> float:
    selected = next(
        (item for item in match.candidates if item.shot_id == match.shot_id),
        None,
    )
    selected_score = _diversity_candidate_score(selected) if selected is not None else match.confidence
    return selected_score - _diversity_candidate_score(candidate)


def _diversity_candidate_score(candidate: MatchCandidate) -> float:
    evidence_support = min(
        0.08,
        0.03 * len(candidate.matched_terms) + 0.05 * len(candidate.verified_terms),
    )
    return max(0.0, candidate.combined_score) + evidence_support


def _calibrated_match_confidence(
    decision_confidence: float,
    selected: MatchCandidate | None,
    candidates: Sequence[MatchCandidate],
) -> float:
    if selected is None:
        return 0.0
    rank = next(
        (index for index, candidate in enumerate(candidates) if candidate.shot_id == selected.shot_id),
        len(candidates) - 1,
    )
    rank_ratio = rank / max(1, len(candidates) - 1)
    rank_confidence = 0.94 - 0.54 * rank_ratio
    score_ratio = max(0.0, min(1.0, (selected.combined_score - 0.15) / 0.45))
    absolute_confidence = 0.25 + 0.65 * score_ratio
    if selected.verified_terms:
        absolute_confidence = max(
            absolute_confidence,
            selected.verification_confidence or 0.75,
        )
    elif selected.matched_terms:
        absolute_confidence = max(
            absolute_confidence,
            0.55 + 0.3 * selected.lexical_score,
        )
    retrieval_confidence = 0.65 * absolute_confidence + 0.35 * rank_confidence
    return round(
        math.sqrt(
            max(0.0, min(1.0, decision_confidence))
            * max(0.0, min(1.0, retrieval_confidence))
        ),
        6,
    )


def _replace_assignment(
    assignment: _BeatAssignment,
    candidate: MatchCandidate,
) -> None:
    previous_shot_id = assignment.match.shot_id
    alternates = list(
        dict.fromkeys(
            shot_id
            for shot_id in [previous_shot_id, *assignment.match.alternates]
            if shot_id != candidate.shot_id
        )
    )
    assignment.match = assignment.match.model_copy(
        update={
            "shot_id": candidate.shot_id,
            "confidence": _calibrated_match_confidence(
                assignment.decision_confidence,
                candidate,
                assignment.match.candidates,
            ),
            "alternates": alternates,
            "preferred_in_time": candidate.preferred_in_time,
        }
    )


def apply_fallbacks(
    sentences: Sequence[Sentence],
    shots: Sequence[AnnotatedShot],
    candidates_by_sentence: dict[int, list[MatchCandidate]],
    decisions: Sequence[RerankDecision],
) -> list[MatchPlanItem]:
    if [decision.sentence_id for decision in decisions] != [sentence.sentence_id for sentence in sentences]:
        raise MatchingError("LLM 精排结果与句子列表不一致。")
    shot_ids = {shot.shot_id for shot in shots}
    if len(sentences) > len(shot_ids):
        raise MatchingError("句子数量超过可用镜头，无法保证每个镜头只使用一次。")
    used_shots = {
        decision.shot_id
        for decision in decisions
        if decision.shot_id is not None and decision.shot_id in shot_ids and decision.confidence >= 0.35
    }
    quality_order = sorted(
        shots,
        key=lambda shot: (
            -((shot.quality.sharp * shot.quality.bright) if shot.quality else 0.0),
            shot.shot_id,
        ),
    )
    if not quality_order:
        raise MatchingError("没有镜头可用于兜底。")

    plan: list[MatchPlanItem] = []
    for sentence, decision in zip(sentences, decisions, strict=True):
        selected = decision.shot_id
        is_fallback = selected is None or selected not in shot_ids or decision.confidence < 0.35
        if is_fallback:
            selected = next(
                (shot.shot_id for shot in quality_order if shot.shot_id not in used_shots),
                quality_order[0].shot_id,
            )
            used_shots.add(selected)
        assert selected is not None
        alternates = [shot_id for shot_id in decision.alternates if shot_id != selected and shot_id in shot_ids]
        plan.append(
            MatchPlanItem(
                sentence_id=sentence.sentence_id,
                text=sentence.text,
                shot_id=selected,
                confidence=decision.confidence,
                alternates=alternates,
                is_fallback=is_fallback,
                candidates=candidates_by_sentence[sentence.sentence_id],
            )
        )
    return plan


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise MatchingError("Embedding 向量维度不一致或为空。")
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot_product / (left_norm * right_norm)))


def _validate_embedding_dimensions(
    sentence_vectors: Sequence[Sequence[float]],
    shot_vectors: Sequence[Sequence[float]],
) -> None:
    all_vectors = [*sentence_vectors, *shot_vectors]
    if not all_vectors or not all_vectors[0]:
        raise MatchingError("Embedding Provider 未返回有效向量。")
    dimension = len(all_vectors[0])
    if any(len(vector) != dimension for vector in all_vectors):
        raise MatchingError("句子与镜头 Embedding 向量维度不一致。")
