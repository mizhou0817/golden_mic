import hashlib
import json
import math
import os
import re
import statistics
import tempfile
import unicodedata
import wave
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from .audio_filters import AFFTDN_CLOCK_FILTER, AFFTDN_DELAY_SAMPLES, AFFTDN_PREROLL_SAMPLES
from .media import (
    MediaProcessingError,
    probe_media,
    require_media_tools,
    run_capture_stderr_command,
    run_logged_command,
)
from .models import (
    MatchPlanItem,
    PronunciationDecision,
    Sentence,
    SentenceTiming,
    SyncSoundSelection,
    TTSWordTiming,
)
from .pronunciation import build_pronunciation_manifest
from .providers.tts import TTSProvider
from .storage import write_json_atomic, write_text_log


TTSProgressCallback = Callable[[int, int, str], None]
SENTENCE_GAP_SECONDS = 0.12
CLAUSE_GAP_SECONDS = 0.09
FULL_SENTENCE_GAP_SECONDS = 0.12
PARAGRAPH_GAP_SECONDS = 0.18
NARRATION_SAMPLE_RATE = 48_000
NARRATION_AUDIO_FORMAT = (
    f"aformat=sample_fmts=fltp:sample_rates={NARRATION_SAMPLE_RATE}:channel_layouts=stereo,"
    f"aresample={NARRATION_SAMPLE_RATE}:async=1:first_pts=0,"
    "asetpts=PTS-STARTPTS"
)
NARRATION_TARGET_LUFS = -20.0
NARRATION_TARGET_LRA = 5.0
NARRATION_TRUE_PEAK_DBFS = -2.0
AUDIO_EDGE_FADE_SECONDS = 0.012
# Fixed local DSP, not generative/AI repair. Applied only to recorded inputs at
# assembly time, never to the reusable sentence files or the mixed/TTS track.
# Share only the fixed 48 kHz clock chain, not pipeline dependencies. Narration
# retains its existing 70 Hz high-pass; Studio's denoise contract does not.
SPEECH_ENHANCEMENT_DELAY_SAMPLES: Final[int] = AFFTDN_DELAY_SAMPLES
SPEECH_ENHANCEMENT_PREROLL_SAMPLES: Final[int] = AFFTDN_PREROLL_SAMPLES
SPEECH_ENHANCEMENT_FILTER: Final[str] = f"highpass=f=70:p=2,{AFFTDN_CLOCK_FILTER}"
SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE: Final[int] = 1
TTS_EDGE_PADDING_BEFORE_SECONDS = 0.04
DEFAULT_NEWS_TARGET_CHARS_PER_MINUTE = 255
DEFAULT_NEWS_RATE_TOLERANCE = 0.08
MIN_TTS_TEMPO_FACTOR = 0.90
MAX_TTS_TEMPO_FACTOR = 1.10
MAX_TTS_UNIT_RATE_PASSES = 3
_BOUNDARY_PUNCTUATION = "，、：；。！？!?"
_SPOKEN_UNIT_PATTERN = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")


class TTSProcessingError(RuntimeError):
    """Raised when stage 7 cannot synthesize or assemble narration."""


@dataclass(frozen=True)
class _PreparedTTSUnit:
    audio_path: Path
    words: list[TTSWordTiming]
    group_id: str


async def synthesize_narration(
    task_dir: Path,
    sentences: Sequence[Sentence],
    provider: TTSProvider,
    progress: TTSProgressCallback,
    *,
    match_plan: Sequence[MatchPlanItem] | None = None,
    full_script: str = "",
    pronunciation_plan: Sequence[PronunciationDecision] | None = None,
    target_chars_per_minute: int = DEFAULT_NEWS_TARGET_CHARS_PER_MINUTE,
    rate_tolerance: float = DEFAULT_NEWS_RATE_TOLERANCE,
    target_lufs: float = NARRATION_TARGET_LUFS,
    target_lra: float = NARRATION_TARGET_LRA,
    true_peak_dbfs: float = NARRATION_TRUE_PEAK_DBFS,
    enhance_speech: bool = False,
    assemble_narration: bool = True,
) -> list[SentenceTiming]:
    """assemble_narration=False: the caller builds its own exact track from the per-sentence units (the
    three-mode pipeline does, with a sample-exact check), so the provisional full track and its coarse
    total-length check are skipped instead of failing on accumulated per-unit rounding."""
    if not sentences:
        raise TTSProcessingError("没有可供配音的文稿句子。")
    if any(sentence.kind == "quote" for sentence in sentences):
        raise TTSProcessingError("显式原声句不能进入 TTS 合成或失败回退路径。")
    require_media_tools()
    sync_by_sentence = {
        item.sentence_id: item.sync_sound
        for item in (match_plan or [])
        if item.sync_sound is not None
    }
    matches_by_sentence = {item.sentence_id: item for item in (match_plan or [])}
    match_plan_changed = False
    if any(sentence.sentence_id not in sync_by_sentence for sentence in sentences):
        provider.validate_configuration()
    document_text = full_script.strip() or "".join(sentence.text for sentence in sentences)
    provider.set_document_context(document_text)
    try:
        pronunciation_manifest = build_pronunciation_manifest(
            sentences,
            pronunciation_plan or [],
        )
    except ValueError as exc:
        raise TTSProcessingError(f"数字读音计划无法应用：{exc}") from exc
    write_json_atomic(task_dir / "pronunciation_plan.json", pronunciation_manifest)
    tts_text_by_sentence = {
        sentence.sentence_id: str(item["tts_text"])
        for sentence, item in zip(sentences, pronunciation_manifest, strict=True)
    }
    tts_dir = task_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    prepared_tts, continuous_groups = await _prepare_continuous_tts_groups(
        task_dir,
        sentences,
        provider,
        tts_text_by_sentence,
        set(sync_by_sentence),
    )
    timings: list[SentenceTiming] = []
    cursor = 0.0
    total = len(sentences)
    previous_sentence: Sentence | None = None

    for index, sentence in enumerate(sentences):
        prepared = prepared_tts.get(sentence.sentence_id)
        audio_path = (
            prepared.audio_path
            if prepared is not None
            else tts_dir / f"sent_{sentence.sentence_id}.mp3"
        )
        sync_sound = sync_by_sentence.get(sentence.sentence_id)
        tts_text = tts_text_by_sentence[sentence.sentence_id]
        word_timings: list[TTSWordTiming] = []
        try:
            if prepared is not None:
                word_timings = list(prepared.words)
                audio_kind = "tts"
                timing_text = sentence.text
            elif sync_sound is not None:
                try:
                    await extract_sync_sound_audio(task_dir, sync_sound, audio_path)
                    audio_kind = "sync"
                    timing_text = sync_sound.text
                except Exception as exc:
                    write_text_log(
                        task_dir,
                        f"句子 {sentence.sentence_id} 同期声提取失败，自动回退 TTS：{exc}",
                    )
                    provider.validate_configuration()
                    word_timings = list(await provider.synthesize(tts_text, audio_path) or [])
                    audio_kind = "tts"
                    timing_text = sentence.text
                    match = matches_by_sentence.get(sentence.sentence_id)
                    if match is not None:
                        match.sync_sound = None
                        match_plan_changed = True
            else:
                word_timings = list(await provider.synthesize(tts_text, audio_path) or [])
                audio_kind = "tts"
                timing_text = sentence.text
            if audio_kind == "tts" and prepared is None:
                trim_start = (
                    previous_sentence is None
                    or previous_sentence.paragraph_index != sentence.paragraph_index
                    or _ends_natural_sentence(previous_sentence.text)
                )
                trim_end = (
                    index + 1 >= total
                    or sentences[index + 1].paragraph_index != sentence.paragraph_index
                    or _ends_natural_sentence(sentence.text)
                )
                word_timings = await trim_tts_audio_edges(
                    task_dir,
                    audio_path,
                    word_timings,
                    trim_start=trim_start,
                    trim_end=trim_end,
                )
            duration = await probe_audio_duration(audio_path, task_dir)
        except Exception as exc:
            raise TTSProcessingError(f"句子“{sentence.text}”音频生成失败：{exc}") from exc

        start = cursor
        end = start + duration
        next_sentence = sentences[index + 1] if index + 1 < total else None
        gap_after = _narration_gap_after(sentence, next_sentence)
        timings.append(
            SentenceTiming(
                sentence_id=sentence.sentence_id,
                text=timing_text,
                audio_path=audio_path.relative_to(task_dir).as_posix(),
                duration=round(duration, 6),
                start=round(start, 6),
                end=round(end, 6),
                audio_kind=audio_kind,
                tts_group_id=prepared.group_id if prepared is not None else None,
                gap_after=gap_after,
                words=word_timings,
            )
        )
        cursor = end + gap_after
        previous_sentence = sentence
        write_text_log(
            task_dir,
            f"句子 {sentence.sentence_id} audio_kind={audio_kind} duration={duration:.6f}s "
            f"pronunciation_adjusted={tts_text != sentence.text} text={timing_text}",
        )
        action = "同期声提取" if audio_kind == "sync" else "配音合成"
        progress(index + 1, total, f"{action} {index + 1}/{total}")

    if match_plan_changed and match_plan is not None:
        write_json_atomic(
            task_dir / "match_plan.json",
            [item.model_dump(mode="json", exclude_none=True) for item in match_plan],
        )

    timings, rate_metrics = await standardize_tts_speaking_rate(
        task_dir,
        timings,
        target_chars_per_minute=target_chars_per_minute,
        tolerance=rate_tolerance,
    )
    provider_profile = provider.synthesis_profile()
    voice_profile_id = _voice_profile_id(provider_profile)
    timings = [
        timing.model_copy(
            update={
                "voice_profile_id": voice_profile_id if timing.audio_kind == "tts" else None,
                "spoken_unit_count": (
                    _spoken_unit_count(timing) if timing.audio_kind == "tts" else None
                ),
                "speaking_rate_cpm": (
                    round(_timing_chars_per_minute(timing), 3)
                    if timing.audio_kind == "tts"
                    else None
                ),
            }
        )
        for timing in timings
    ]
    timings_path = task_dir / "timings.json"
    write_json_atomic(
        timings_path,
        [timing.model_dump(mode="json") for timing in timings],
    )
    write_json_atomic(
        task_dir / "tts_manifest.json",
        {
            "schema_version": 1,
            "profile": "zh_cn_professional_news_v1",
            "provider_profile": provider_profile,
            "target_chars_per_minute": target_chars_per_minute,
            "rate_tolerance": rate_tolerance,
            "tempo_correction_factor": rate_metrics["tempo_correction_factor"],
            "tempo_correction_scope": "document_before_per_unit_correction",
            "measured_chars_per_minute_before": rate_metrics["measured_chars_per_minute_before"],
            "measured_chars_per_minute_after": rate_metrics["measured_chars_per_minute_after"],
            "tts_unit_rate_cv": rate_metrics["tts_unit_rate_cv"],
            "tts_unit_count": sum(timing.audio_kind == "tts" for timing in timings),
            "sync_unit_count": sum(timing.audio_kind == "sync" for timing in timings),
                "provider_request_count": len(continuous_groups) + sum(
                    timing.audio_kind == "tts" and timing.tts_group_id is None
                    for timing in timings
                ),
                "continuous_group_count": sum(
                    bool(group.get("continuous")) for group in continuous_groups
                ),
            "continuous_groups": continuous_groups,
            "sentence_ids": [timing.sentence_id for timing in timings],
        },
    )
    write_json_atomic(
        task_dir / "narration_profile.json",
        _narration_profile_payload(
            timings,
            provider_profile=provider_profile,
            target_chars_per_minute=target_chars_per_minute,
            rate_tolerance=rate_tolerance,
            remixed=False,
        ),
    )
    if not assemble_narration:
        return timings
    await concatenate_narration(
        task_dir,
        timings,
        target_lufs=target_lufs,
        target_lra=target_lra,
        true_peak_dbfs=true_peak_dbfs,
        enhance_speech=enhance_speech,
    )
    narration_duration = await probe_audio_duration(task_dir / "narration.m4a", task_dir)
    expected_duration = timings[-1].end
    if abs(narration_duration - expected_duration) > 0.3:
        raise TTSProcessingError(
            f"旁白拼接时长校验失败：期望 {expected_duration:.3f}s，实际 {narration_duration:.3f}s。"
        )
    write_text_log(
        task_dir,
        f"旁白拼接完成 expected={expected_duration:.6f}s actual={narration_duration:.6f}s",
    )
    return timings


async def _prepare_continuous_tts_groups(
    task_dir: Path,
    sentences: Sequence[Sentence],
    provider: TTSProvider,
    tts_text_by_sentence: dict[int, str],
    sync_sentence_ids: set[int],
) -> tuple[dict[int, _PreparedTTSUnit], list[dict[str, object]]]:
    if not provider.supports_word_timings:
        return {}, []
    groups = _continuous_sentence_groups(sentences, sync_sentence_ids)
    if not groups:
        return {}, []

    groups_dir = task_dir / "tts" / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)
    prepared: dict[int, _PreparedTTSUnit] = {}
    manifest: list[dict[str, object]] = []
    for group in groups:
        first_id = group[0].sentence_id
        last_id = group[-1].sentence_id
        group_id = f"group_{first_id}_{last_id}"
        group_path = groups_dir / f"{group_id}.mp3"
        group_texts = [tts_text_by_sentence[sentence.sentence_id] for sentence in group]
        output_paths = [task_dir / "tts" / f"sent_{sentence.sentence_id}.wav" for sentence in group]
        try:
            words = list(await provider.synthesize("".join(group_texts), group_path) or [])
            if not words:
                raise TTSProcessingError("提供方未返回连续合成所需的词级时间戳。")
            raw_duration = await probe_audio_duration(group_path, task_dir)
            split_words = _split_group_word_timings(group_texts, words, raw_duration)
            await _split_continuous_group_audio(
                task_dir,
                group_path,
                output_paths,
                [item[0] for item in split_words],
            )
        except Exception as exc:
            group_path.unlink(missing_ok=True)
            for output_path in output_paths:
                output_path.unlink(missing_ok=True)
            write_text_log(
                task_dir,
                f"连续自然句 TTS 合成失败，回退逐单元合成 group={group_id}：{exc}",
            )
            continue

        for sentence, output_path, (_, sentence_words) in zip(
            group,
            output_paths,
            split_words,
            strict=True,
        ):
            prepared[sentence.sentence_id] = _PreparedTTSUnit(
                audio_path=output_path,
                words=sentence_words,
                group_id=group_id,
            )
        manifest.append(
            {
                "group_id": group_id,
                "sentence_ids": [sentence.sentence_id for sentence in group],
                    "continuous": len(group) > 1,
                "source_audio_path": group_path.relative_to(task_dir).as_posix(),
                "text": "".join(group_texts),
            }
        )
        write_text_log(
            task_dir,
            f"连续自然句 TTS 合成完成 group={group_id} units={len(group)}",
        )
    return prepared, manifest


def _continuous_sentence_groups(
    sentences: Sequence[Sentence],
    sync_sentence_ids: set[int],
) -> list[list[Sentence]]:
    groups: list[list[Sentence]] = []
    current: list[Sentence] = []
    current_key: tuple[int, int] | None = None
    for sentence in sentences:
        key: tuple[int, int] | None = None
        if sentence.sentence_id not in sync_sentence_ids:
            group_key = (
                sentence.visual_group_id
                if sentence.visual_group_id is not None
                else -(sentence.sentence_id + 1)
            )
            key = (sentence.paragraph_index, group_key)
        if key is None or (current_key is not None and key != current_key):
            if current:
                groups.append(current)
            current = []
            current_key = None
        if key is None:
            continue
        if current_key is None:
            current_key = key
        current.append(sentence)
    if current:
        groups.append(current)
    return groups


def _split_group_word_timings(
    texts: Sequence[str],
    words: Sequence[TTSWordTiming],
    raw_duration: float,
) -> list[tuple[tuple[float, float], list[TTSWordTiming]]]:
    normalized_parts = [_alignment_text(text) for text in texts]
    if any(not part for part in normalized_parts):
        raise TTSProcessingError("连续合成分句包含无法对齐的空播音文本。")
    expected = "".join(normalized_parts)
    returned_parts = [_alignment_text(word.text) for word in words]
    returned = "".join(returned_parts)
    if returned != expected:
        raise TTSProcessingError(
            f"连续合成词级时间戳无法与输入对齐：expected={len(expected)} returned={len(returned)}。"
        )

    component_ends: list[int] = []
    cursor = 0
    for part in normalized_parts:
        cursor += len(part)
        component_ends.append(cursor)

    word_spans: list[tuple[int, int, TTSWordTiming]] = []
    cursor = 0
    for word, normalized in zip(words, returned_parts, strict=True):
        start = cursor
        cursor += len(normalized)
        word_spans.append((start, cursor, word))

    first_spoken = next((word for normalized, word in zip(returned_parts, words) if normalized), None)
    last_spoken = next(
        (word for normalized, word in zip(reversed(returned_parts), reversed(words)) if normalized),
        None,
    )
    if first_spoken is None or last_spoken is None:
        raise TTSProcessingError("连续合成未返回有效播音词时间戳。")
    edges = [max(0.0, first_spoken.start - TTS_EDGE_PADDING_BEFORE_SECONDS)]
    edges.extend(
        _group_boundary_time(boundary, word_spans)
        for boundary in component_ends[:-1]
    )
    # Provider word timestamps are subtitle alignment cues, not safe acoustic
    # cut points. Seed TTS can report the final word hundreds of milliseconds
    # before its audible syllable finishes, so retain the source tail when no
    # following unit can be affected.
    edges.append(raw_duration)
    if any(right - left < 0.02 for left, right in zip(edges, edges[1:])):
        raise TTSProcessingError("连续合成切分边界过近或顺序无效。")

    words_by_component: list[list[TTSWordTiming]] = [[] for _ in texts]
    for span_start, span_end, word in word_spans:
        if span_end > span_start:
            midpoint = (span_start + span_end) / 2
            component_index = min(len(texts) - 1, bisect_right(component_ends, midpoint))
        else:
            component_index = min(len(texts) - 1, bisect_left(component_ends, span_start))
        segment_start = edges[component_index]
        segment_end = edges[component_index + 1]
        adjusted_start = max(0.0, word.start - segment_start)
        adjusted_end = min(segment_end - segment_start, word.end - segment_start)
        if adjusted_end <= adjusted_start:
            continue
        words_by_component[component_index].append(
            word.model_copy(
                update={
                    "start": round(adjusted_start, 6),
                    "end": round(adjusted_end, 6),
                }
            )
        )
    if any(not component_words for component_words in words_by_component):
        raise TTSProcessingError("连续合成切分后存在没有词级时间戳的播音单元。")
    return [
        ((edges[index], edges[index + 1]), words_by_component[index])
        for index in range(len(texts))
    ]


def _alignment_text(text: str) -> str:
    return "".join(_SPOKEN_UNIT_PATTERN.findall(unicodedata.normalize("NFKC", text)))


def _group_boundary_time(
    boundary: int,
    word_spans: Sequence[tuple[int, int, TTSWordTiming]],
) -> float:
    previous_end: float | None = None
    next_start: float | None = None
    for span_start, span_end, word in word_spans:
        if span_start < boundary < span_end:
            raise TTSProcessingError("连续合成断句落在同一个词内部，不能安全切分音频。")
        if span_end <= boundary:
            previous_end = word.end
            continue
        if span_start >= boundary:
            next_start = word.start
            break
    if previous_end is None or next_start is None:
        raise TTSProcessingError("连续合成缺少可用的单元切分边界。")
    if next_start < previous_end:
        raise TTSProcessingError("连续合成断句两侧词时间戳重叠，不能安全切分音频。")
    return (previous_end + next_start) / 2


async def _split_continuous_group_audio(
    task_dir: Path,
    group_path: Path,
    output_paths: Sequence[Path],
    ranges: Sequence[tuple[float, float]],
) -> None:
    if len(output_paths) != len(ranges):
        raise TTSProcessingError("连续合成音频切分数量不一致。")
    temporary_paths: list[Path] = []
    try:
        for output_path, (start, end) in zip(output_paths, ranges, strict=True):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = output_path.with_name(f"{output_path.stem}.part.wav")
            temporary_path.unlink(missing_ok=True)
            temporary_paths.append(temporary_path)
            await run_logged_command(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(group_path),
                    "-af",
                    f"atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS",
                    "-ac",
                    "1",
                    "-ar",
                    str(NARRATION_SAMPLE_RATE),
                    "-c:a",
                    "pcm_s16le",
                    str(temporary_path),
                ],
                task_dir,
                f"切分连续 TTS {output_path.name}",
            )
            if not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
                raise TTSProcessingError(f"连续 TTS 切分未生成有效音频：{output_path.name}")
            temporary_path.replace(output_path)
    except BaseException:
        for output_path in output_paths:
            output_path.unlink(missing_ok=True)
        raise
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


async def rebuild_narration_from_existing(
    task_dir: Path,
    source_timings: Sequence[SentenceTiming],
    keep_sentence_ids: Sequence[int],
    *,
    timings_path: Path,
    narration_path: Path,
    narration_profile_path: Path,
    enhance_speech: bool = False,
) -> list[SentenceTiming]:
    keep_ids = set(keep_sentence_ids)
    selected = [timing for timing in source_timings if timing.sentence_id in keep_ids]
    if not selected:
        raise TTSProcessingError("重剪至少需要保留一个句子。")
    if {timing.sentence_id for timing in selected} != keep_ids:
        unknown = sorted(keep_ids - {timing.sentence_id for timing in source_timings})
        raise TTSProcessingError(f"重剪包含未知句子：{unknown}")

    rebuilt: list[SentenceTiming] = []
    cursor = 0.0
    for timing in selected:
        end = cursor + timing.duration
        selected_index = len(rebuilt)
        next_timing = selected[selected_index + 1] if selected_index + 1 < len(selected) else None
        gap_after = timing.gap_after if next_timing is not None else 0.0
        rebuilt.append(
            timing.model_copy(
                update={
                    "start": round(cursor, 6),
                    "end": round(end, 6),
                    "gap_after": gap_after,
                }
            )
        )
        cursor = end + gap_after

    write_json_atomic(
        timings_path,
        [timing.model_dump(mode="json") for timing in rebuilt],
    )
    source_profile = _load_narration_profile(task_dir / "narration_profile.json")
    profile_payload = dict(source_profile)
    # A revision mixes the original sentence inputs again. A previous effect
    # receipt is not evidence about this mix (especially when turning it off).
    profile_payload.pop("speech_enhancement", None)
    profile_payload.update(
        {
            "schema_version": 1,
            "remixed": True,
            "units": [_narration_profile_unit(timing) for timing in rebuilt],
        }
    )
    write_json_atomic(narration_profile_path, profile_payload)
    await concatenate_narration(
        task_dir, rebuilt, output_path=narration_path,
        enhance_speech=enhance_speech, narration_profile_path=narration_profile_path,
    )
    narration_duration = await probe_audio_duration(narration_path, task_dir)
    if abs(narration_duration - rebuilt[-1].end) > 0.3:
        raise TTSProcessingError(
            f"重剪旁白时长校验失败：期望 {rebuilt[-1].end:.3f}s，实际 {narration_duration:.3f}s。"
        )
    write_text_log(
        task_dir,
        f"重剪旁白完成 sentences={len(rebuilt)} expected={rebuilt[-1].end:.6f}s actual={narration_duration:.6f}s",
    )
    return rebuilt


def _load_narration_profile(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(cast(dict[str, object], payload)) if isinstance(payload, dict) else {}


async def extract_sync_sound_audio(
    task_dir: Path,
    selection: SyncSoundSelection,
    output_path: Path,
) -> Path:
    task_root = task_dir.resolve()
    raw_root = (task_dir / "raw").resolve()
    source_path = (task_dir / selection.source_media_path).resolve()
    if (
        not source_path.is_relative_to(task_root)
        or not source_path.is_relative_to(raw_root)
        or not source_path.is_file()
    ):
        raise TTSProcessingError(f"同期声源文件不存在或路径不安全：{selection.source_media_path}")
    duration = selection.end - selection.start
    if duration <= 0:
        raise TTSProcessingError("同期声入出点无效。")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".part.mp3")
    temporary_path.unlink(missing_ok=True)
    try:
        await run_logged_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-ss",
                f"{selection.start:.6f}",
                "-t",
                f"{duration:.6f}",
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "96k",
                str(temporary_path),
            ],
            task_dir,
            f"提取同期声 sentence={selection.text[:20]}",
        )
        if not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
            raise TTSProcessingError("同期声未生成有效音频文件。")
        temporary_path.replace(output_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return output_path


async def probe_audio_duration(audio_path: Path, task_dir: Path) -> float:
    probe = await probe_media(audio_path, task_dir)
    duration_value: object | None = None
    format_info = probe.get("format")
    if isinstance(format_info, dict):
        typed_format = cast(dict[str, object], format_info)
        duration_value = typed_format.get("duration")
    if duration_value is None:
        streams = probe.get("streams")
        if isinstance(streams, list):
            typed_streams = [
                cast(dict[str, object], stream)
                for stream in cast(list[object], streams)
                if isinstance(stream, dict)
            ]
            audio_stream = next(
                (stream for stream in typed_streams if stream.get("codec_type") == "audio"),
                None,
            )
            if audio_stream:
                duration_value = audio_stream.get("duration")
    if not isinstance(duration_value, (str, int, float)):
        raise MediaProcessingError(f"无法读取音频时长：{audio_path.name}")
    try:
        duration = float(duration_value)
    except (TypeError, ValueError) as exc:
        raise MediaProcessingError(f"无法读取音频时长：{audio_path.name}") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaProcessingError(f"音频时长无效：{audio_path.name}")
    return duration


async def trim_tts_audio_edges(
    task_dir: Path,
    audio_path: Path,
    words: Sequence[TTSWordTiming],
    *,
    trim_start: bool = True,
    trim_end: bool = True,
) -> list[TTSWordTiming]:
    temporary_path = audio_path.with_name(f"{audio_path.stem}.trim.mp3")
    temporary_path.unlink(missing_ok=True)
    if words:
        raw_duration = await probe_audio_duration(audio_path, task_dir)
        trim_offset = (
            max(0.0, words[0].start - TTS_EDGE_PADDING_BEFORE_SECONDS)
            if trim_start
            else 0.0
        )
        # A final word timestamp can end before the synthesized syllable's
        # acoustic tail. Keep the full source ending rather than risk removing
        # spoken content; the provider's bounded trailing silence is harmless.
        trim_boundary = raw_duration
        if trim_boundary <= trim_offset:
            return list(words)
        if trim_offset <= 0.000001 and trim_boundary >= raw_duration - 0.000001:
            return list(words)
        audio_filter = f"atrim=start={trim_offset:.6f}:end={trim_boundary:.6f},asetpts=PTS-STARTPTS"
    else:
        trim_offset = 0.0
        filters: list[str] = []
        if trim_start:
            filters.append("silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB")
        if trim_end:
            filters.append(
                "areverse,silenceremove=start_periods=1:start_duration=0.08:"
                "start_threshold=-45dB,areverse"
            )
        if not filters:
            return []
        audio_filter = ",".join(filters)
    try:
        await run_logged_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(audio_path),
                "-af",
                audio_filter,
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "96k",
                str(temporary_path),
            ],
            task_dir,
            f"裁剪 TTS 首尾静音 {audio_path.name}",
        )
        if temporary_path.is_file() and temporary_path.stat().st_size > 0:
            temporary_path.replace(audio_path)
    except Exception as exc:
        write_text_log(task_dir, f"TTS 首尾静音裁剪失败，保留原音频：{audio_path.name}；{exc}")
        return list(words)
    finally:
        temporary_path.unlink(missing_ok=True)
    if not words:
        return []
    adjusted: list[TTSWordTiming] = []
    for word in words:
        start = max(0.0, word.start - trim_offset)
        end = max(start + 0.001, word.end - trim_offset)
        adjusted.append(word.model_copy(update={"start": round(start, 6), "end": round(end, 6)}))
    return adjusted


async def standardize_tts_speaking_rate(
    task_dir: Path,
    timings: Sequence[SentenceTiming],
    *,
    target_chars_per_minute: int,
    tolerance: float,
) -> tuple[list[SentenceTiming], dict[str, float]]:
    if target_chars_per_minute <= 0:
        raise TTSProcessingError("新闻旁白目标语速必须大于 0。")
    if not 0.0 <= tolerance <= 0.25:
        raise TTSProcessingError("新闻旁白语速容差必须位于 0 到 0.25。")

    measured_before = _aggregate_tts_rate(timings)
    lower_bound = target_chars_per_minute * (1.0 - tolerance)
    upper_bound = target_chars_per_minute * (1.0 + tolerance)
    requested_factor = target_chars_per_minute / measured_before if measured_before > 0 else 1.0
    if lower_bound <= measured_before <= upper_bound:
        tempo_factor = 1.0
    else:
        tempo_factor = round(
            min(MAX_TTS_TEMPO_FACTOR, max(MIN_TTS_TEMPO_FACTOR, requested_factor)), 6
        )

    adjusted: list[SentenceTiming] = []
    for timing in timings:
        if timing.audio_kind != "tts":
            # Recorded/sync audio is never rate-normalized or given TTS metrics.
            adjusted.append(timing.model_copy(deep=True))
            continue
        current = timing.model_copy(deep=True)
        if abs(tempo_factor - 1.0) > 0.001:
            current = await _retime_tts_unit(task_dir, current, tempo_factor)
        else:
            current.words = _fit_word_timings(current.words, duration=current.duration)

        # A correct document average can hide fast units and can even push
        # previously acceptable units below range. Correct the FINAL split units,
        # not the unsplit group or just their reported CPM. The residual budget
        # is cumulative +/-10% after the document pass (overall 0.81..1.21 for
        # newly synthesized audio); retries must not reset that budget.
        residual_factor = 1.0
        for _ in range(MAX_TTS_UNIT_RATE_PASSES):
            rate = _timing_chars_per_minute(current)
            if rate <= 0 or lower_bound <= rate <= upper_bound:
                break
            factor = round(
                min(
                    MAX_TTS_TEMPO_FACTOR,
                    MAX_TTS_TEMPO_FACTOR / residual_factor,
                    max(
                        MIN_TTS_TEMPO_FACTOR,
                        MIN_TTS_TEMPO_FACTOR / residual_factor,
                        target_chars_per_minute / rate,
                    ),
                ),
                6,
            )
            if abs(factor - 1.0) <= 0.000001:
                break
            current = await _retime_tts_unit(task_dir, current, factor)
            residual_factor *= factor
        rate = _timing_chars_per_minute(current)
        adjusted.append(
            current.model_copy(
                update={
                    "spoken_unit_count": _spoken_unit_count(current),
                    "speaking_rate_cpm": round(rate, 3) if rate > 0 else None,
                }
            )
        )

    rebuilt = _rebuild_timing_cursor(adjusted)
    measured_after = _aggregate_tts_rate(rebuilt)
    unit_rates = [
        rate
        for timing in rebuilt
        if timing.audio_kind == "tts" and (rate := _timing_chars_per_minute(timing)) > 0
    ]
    rate_cv = (
        statistics.pstdev(unit_rates) / statistics.mean(unit_rates)
        if len(unit_rates) >= 2 and statistics.mean(unit_rates) > 0
        else 0.0
    )
    metrics: dict[str, float] = {
        "tempo_correction_factor": round(tempo_factor, 6),
        "measured_chars_per_minute_before": round(measured_before, 3),
        "measured_chars_per_minute_after": round(measured_after, 3),
        "tts_unit_rate_cv": round(rate_cv, 6),
        "tts_unit_rate_outlier_count": sum(
            1 for rate in unit_rates if not lower_bound <= rate <= upper_bound
        ),
    }
    write_text_log(
        task_dir,
        "新闻旁白语速校准 "
        f"target={target_chars_per_minute}cpm before={measured_before:.3f}cpm "
        f"document_tempo={tempo_factor:.6f} after={measured_after:.3f}cpm cv={rate_cv:.6f} "
        f"remaining_outliers={metrics['tts_unit_rate_outlier_count']}",
    )
    return rebuilt, metrics


async def _retime_tts_unit(
    task_dir: Path,
    timing: SentenceTiming,
    factor: float,
) -> SentenceTiming:
    audio_path = (task_dir / timing.audio_path).resolve()
    if not audio_path.is_relative_to(task_dir.resolve()) or not audio_path.is_file():
        raise TTSProcessingError(f"句子音频不存在或路径不安全：{timing.audio_path}")
    await _apply_tempo_correction(task_dir, audio_path, factor)
    # atempo/codec output length is not exactly input_duration / factor. Keep
    # the complete acoustic tail and use the measured duration for the cursor;
    # word cues follow the actual applied tempo, never an invented target rate.
    duration = await probe_audio_duration(audio_path, task_dir)
    return timing.model_copy(
        update={
            "duration": round(duration, 6),
            "words": _fit_word_timings(timing.words, duration=duration, scale=1.0 / factor),
            "tempo_adjustment": round(timing.tempo_adjustment * factor, 6),
            "integrated_lufs": None,
            "true_peak_dbfs": None,
        }
    )


def _fit_word_timings(
    words: Sequence[TTSWordTiming],
    *,
    duration: float,
    scale: float = 1.0,
) -> list[TTSWordTiming]:
    if not words:
        return []
    maximum_start = max(0.0, duration - 0.001)
    fitted: list[TTSWordTiming] = []
    for word in words:
        start = min(maximum_start, max(0.0, word.start * scale))
        end = min(duration, max(start + 0.001, word.end * scale))
        fitted.append(
            word.model_copy(
                update={
                    "start": round(start, 6),
                    "end": round(end, 6),
                }
            )
        )
    return fitted


async def _apply_tempo_correction(task_dir: Path, audio_path: Path, factor: float) -> None:
    temporary_path = audio_path.with_name(f"{audio_path.stem}.tempo{audio_path.suffix}")
    temporary_path.unlink(missing_ok=True)
    codec_args = (
        ["-c:a", "pcm_s16le"]
        if audio_path.suffix.lower() == ".wav"
        else ["-c:a", "libmp3lame", "-b:a", "160k"]
    )
    try:
        await run_logged_command(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(audio_path),
                "-af",
                f"atempo={factor:.6f}",
                "-ac",
                "1",
                "-ar",
                str(NARRATION_SAMPLE_RATE),
                *codec_args,
                str(temporary_path),
            ],
            task_dir,
            f"校准新闻播报语速 {audio_path.name}",
        )
        if not temporary_path.is_file() or temporary_path.stat().st_size <= 0:
            raise TTSProcessingError(f"新闻播报语速校准未生成有效音频：{audio_path.name}")
        temporary_path.replace(audio_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _rebuild_timing_cursor(timings: Sequence[SentenceTiming]) -> list[SentenceTiming]:
    rebuilt: list[SentenceTiming] = []
    cursor = 0.0
    for timing in timings:
        end = cursor + timing.duration
        rebuilt.append(
            timing.model_copy(
                update={
                    "start": round(cursor, 6),
                    "end": round(end, 6),
                }
            )
        )
        cursor = end + timing.gap_after
    return rebuilt


def _aggregate_tts_rate(timings: Sequence[SentenceTiming]) -> float:
    spoken_units = 0
    active_seconds = 0.0
    for timing in timings:
        if timing.audio_kind != "tts":
            continue
        spoken_units += _spoken_unit_count(timing)
        active_seconds += _active_speech_duration(timing)
    return 60.0 * spoken_units / active_seconds if spoken_units and active_seconds > 0 else 0.0


def _timing_chars_per_minute(timing: SentenceTiming) -> float:
    units = _spoken_unit_count(timing)
    duration = _active_speech_duration(timing)
    return 60.0 * units / duration if units and duration > 0 else 0.0


def _spoken_unit_count(timing: SentenceTiming) -> int:
    source = "".join(word.text for word in timing.words) if timing.words else timing.text
    return len(_SPOKEN_UNIT_PATTERN.findall(unicodedata.normalize("NFKC", source)))


def _active_speech_duration(timing: SentenceTiming) -> float:
    if not timing.words:
        return timing.duration
    return max(0.001, timing.words[-1].end - timing.words[0].start)


def _voice_profile_id(provider_profile: dict[str, object]) -> str:
    stable_profile = {
        key: value
        for key, value in provider_profile.items()
        if key != "section_id"
    }
    canonical = json.dumps(
        stable_profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _narration_profile_unit(timing: SentenceTiming) -> dict[str, object]:
    return {
        "sentence_id": timing.sentence_id,
        "audio_kind": timing.audio_kind,
        "tts_group_id": timing.tts_group_id,
        "voice_profile_id": timing.voice_profile_id,
        "spoken_unit_count": timing.spoken_unit_count,
        "speaking_rate_cpm": timing.speaking_rate_cpm,
        "tempo_adjustment": timing.tempo_adjustment,
        "integrated_lufs": timing.integrated_lufs,
        "true_peak_dbfs": timing.true_peak_dbfs,
    }


def _narration_profile_payload(
    timings: Sequence[SentenceTiming],
    *,
    provider_profile: dict[str, object],
    target_chars_per_minute: int,
    rate_tolerance: float,
    remixed: bool,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "remixed": remixed,
        "provider_profile": provider_profile,
        "voice_profile_id": _voice_profile_id(provider_profile),
        "target_chars_per_minute": target_chars_per_minute,
        "rate_tolerance": rate_tolerance,
        "units": [_narration_profile_unit(timing) for timing in timings],
    }


def mode_narration_gap(text: str, *, final: bool = False, terminal_punctuation: str | None = None) -> float:
    """V2 authored silence, never a speech-length repair.

    Rules should retain terminal punctuation before stripping display text and
    supply it explicitly. Unknown/absent punctuation means a clause (250ms).
    This helper does not change historical synthesis/rebuild timing defaults.
    """
    if final:
        return 0.0
    ending = terminal_punctuation if terminal_punctuation is not None else text.rstrip().rstrip('”’"\'）)]」』')
    return 0.4 if ending.endswith(("。", "！", "？", ".", "!", "?")) else 0.25


def _ends_natural_sentence(text: str) -> bool:
    return text.rstrip().endswith(("。", "！", "？", "!", "?"))


def _narration_gap_after(sentence: Sentence, next_sentence: Sentence | None) -> float:
    if next_sentence is None:
        return 0.0
    if next_sentence.paragraph_index != sentence.paragraph_index:
        return PARAGRAPH_GAP_SECONDS
    return _text_gap_after(sentence.text, next_sentence.text)


def _text_gap_after(text: str, next_text: str | None) -> float:
    if next_text is None:
        return 0.0
    clean = text.rstrip()
    if clean.endswith(("。", "！", "？", "!", "?")):
        return FULL_SENTENCE_GAP_SECONDS
    # Screen/retrieval segmentation can split one grammatical sentence into
    # several TTS units. Keep those units contiguous; punctuation already gives
    # the synthesizer an internal pause and an extra silence causes audible resets.
    if clean.endswith(tuple(_BOUNDARY_PUNCTUATION)):
        return 0.0
    return 0.0


async def concatenate_narration(
    task_dir: Path,
    timings: Sequence[SentenceTiming],
    *,
    output_path: Path | None = None,
    target_lufs: float = NARRATION_TARGET_LUFS,
    target_lra: float = NARRATION_TARGET_LRA,
    true_peak_dbfs: float = NARRATION_TRUE_PEAK_DBFS,
    enhance_speech: bool = False,
    narration_profile_path: Path | None = None,
    pre_normalized: bool = False,
) -> Path:
    """Mix reusable, unprocessed sentence inputs; optionally reduce recording noise.

    A temporary PCM derivative per recorded unit allows sample-count and source
    hash verification before mixing. These paths NEVER enter SentenceTiming or
    revision snapshots. Every remix therefore processes the unchanged source
    once; a visual-only replacement just reuses the already assembled narration.
    TTS inputs retain their exact historical two-pass loudness/edge-fade chain.
    """
    profile_path = narration_profile_path or task_dir / "narration_profile.json"
    if pre_normalized and enhance_speech:
        raise TTSProcessingError("已处理的模式音频不能再次降噪。")
    if not enhance_speech:
        result = await _concatenate_narration(
            task_dir, timings, output_path=output_path,
            target_lufs=target_lufs, target_lra=target_lra, true_peak_dbfs=true_peak_dbfs,
            pre_normalized=pre_normalized,
        )
        profile = _load_narration_profile(profile_path)
        if "speech_enhancement" in profile:
            profile.pop("speech_enhancement")
            write_json_atomic(profile_path, profile)
        return result
    if not timings:
        raise TTSProcessingError("没有音频片段可供拼接。")
    for timing in timings:
        _sentence_audio_path(task_dir, timing)
    evidence: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix=".speech-enhance-", dir=task_dir) as directory:
        processed: dict[int, Path] = {}
        for index, timing in enumerate(timings):
            # audio_kind is authoritative, including after recording -> TTS
            # revisions. Stale student/recording metadata must never denoise TTS.
            if timing.audio_kind != "sync":
                continue
            processed[index], receipt = await _prepare_enhanced_recording(
                task_dir, timing, Path(directory), index,
            )
            evidence.append(receipt)
        result = await _concatenate_narration(
            task_dir, timings, output_path=output_path,
            target_lufs=target_lufs, target_lra=target_lra, true_peak_dbfs=true_peak_dbfs,
            recorded_inputs=processed,
        )
    profile = _load_narration_profile(profile_path)
    profile["speech_enhancement"] = {
        "schema_version": 1,
        "requested": True,
        "applied": bool(evidence),
        "algorithm": "local_highpass_afftdn_clock_compensated_v2",
        "filter": SPEECH_ENHANCEMENT_FILTER,
        "algorithmic_delay_samples": SPEECH_ENHANCEMENT_DELAY_SAMPLES,
        "preroll_samples": SPEECH_ENHANCEMENT_PREROLL_SAMPLES,
        "scope": "recorded_units_at_assembly_only",
        "tts_filtered": False,
        "filtered_unit_count": len(evidence),
        "sample_tolerance": SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE,
        "units": evidence,
    }
    write_json_atomic(profile_path, profile)
    return result


def _sentence_audio_path(task_dir: Path, timing: SentenceTiming) -> Path:
    path = (task_dir / timing.audio_path).resolve()
    if not path.is_relative_to(task_dir.resolve()) or not path.is_file():
        raise TTSProcessingError(f"句子音频不存在或路径不安全：{timing.audio_path}")
    return path


def _audio_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_pcm_samples(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (
                NARRATION_SAMPLE_RATE, 2, 2,
            ) or audio.getcomptype() != "NONE":
                raise TTSProcessingError("本地录音降噪输出的 PCM 规格无效。")
            samples = audio.getnframes()
            if samples <= 0:
                raise TTSProcessingError("本地录音降噪未生成有效 PCM 音频。")
            # Count actual PCM, not only the WAV header: truncated derivatives
            # cannot pass with a plausible duration tag. This proves storage
            # integrity, not that speech was not attenuated/shifted within it.
            actual_bytes = 0
            while chunk := audio.readframes(65_536):
                actual_bytes += len(chunk)
            if actual_bytes != samples * audio.getnchannels() * audio.getsampwidth():
                raise TTSProcessingError("本地录音降噪的 PCM 数据不完整。")
            return samples
    except (OSError, EOFError, wave.Error) as exc:
        raise TTSProcessingError("本地录音降噪无法校验 PCM 音频。") from exc


async def _prepare_enhanced_recording(
    task_dir: Path, timing: SentenceTiming, temporary_dir: Path, index: int,
) -> tuple[Path, dict[str, object]]:
    """Verify derivative length and unchanged source, not phonetic equivalence.

    The filters can change phase and amplitude. Synthetic passband/onset/EOF
    regressions test these separately; sample counts and unchanged word cues
    alone cannot prove alignment or preservation of every spoken sound.
    """
    if timing.audio_kind != "sync":
        raise TTSProcessingError("只允许对同期声或学生录音应用本地降噪。")
    source = _sentence_audio_path(task_dir, timing)
    source_hash = _audio_sha256(source)
    reference = temporary_dir / f"{index}.reference.wav"
    enhanced = temporary_dir / f"{index}.enhanced.wav"
    # Decode once and fork at the SAME sample format/rate/layout. The effect
    # compensates only afftdn's fixed buffering delay; there is no end trim,
    # forced duration, silence removal or repair of an observed size mismatch.
    # The shared media runner enforces the existing timeout/cancellation policy.
    await run_logged_command(
        [
            "ffmpeg", "-y", "-i", str(source), "-filter_complex",
            f"[0:a:0]{NARRATION_AUDIO_FORMAT},asplit=2[reference][recorded];"
            f"[recorded]{SPEECH_ENHANCEMENT_FILTER}[enhanced]",
            "-map", "[reference]", "-vn", "-c:a", "pcm_s16le", str(reference),
            "-map", "[enhanced]", "-vn", "-c:a", "pcm_s16le", str(enhanced),
        ],
        task_dir,
        f"本地录音降噪（非 AI 修复） sentence={timing.sentence_id}",
    )
    before = _verified_pcm_samples(reference)
    after = _verified_pcm_samples(enhanced)
    if abs(after - before) > SPEECH_ENHANCEMENT_SAMPLE_TOLERANCE:
        raise TTSProcessingError("本地录音降噪改变了音频样本数，已拒绝使用；不会裁切或填充原声。")
    if _audio_sha256(source) != source_hash:
        raise TTSProcessingError("本地录音降噪期间原始音频发生变化，已拒绝使用。")
    reference.unlink()
    return enhanced, {
        "sentence_id": timing.sentence_id,
        "audio_kind": timing.audio_kind,
        "source_audio_path": timing.audio_path,
        "source_sha256": source_hash,
        "source_preserved": True,
        "processed_pcm_sha256": _audio_sha256(enhanced),
        "sample_rate": NARRATION_SAMPLE_RATE,
        "source_decoded_samples": before,
        "processed_samples": after,
        "algorithmic_delay_samples": SPEECH_ENHANCEMENT_DELAY_SAMPLES,
        "preroll_samples": SPEECH_ENHANCEMENT_PREROLL_SAMPLES,
    }


async def _concatenate_narration(
    task_dir: Path,
    timings: Sequence[SentenceTiming],
    *,
    output_path: Path | None = None,
    target_lufs: float = NARRATION_TARGET_LUFS,
    target_lra: float = NARRATION_TARGET_LRA,
    true_peak_dbfs: float = NARRATION_TRUE_PEAK_DBFS,
    recorded_inputs: dict[int, Path] | None = None,
    pre_normalized: bool = False,
) -> Path:
    if not timings:
        raise TTSProcessingError("没有音频片段可供拼接。")
    output_path = output_path or task_dir / "narration.m4a"
    command = ["ffmpeg", "-y"]
    for index, timing in enumerate(timings):
        audio_path = _sentence_audio_path(task_dir, timing)
        if output_path.resolve() == audio_path or (output_path.exists() and output_path.samefile(audio_path)):
            raise TTSProcessingError("旁白输出不能覆盖原始逐句音频。")
        if recorded_inputs and index in recorded_inputs:
            if timing.audio_kind != "sync":
                raise TTSProcessingError("禁止替换 TTS 音频的降噪输入。")
            audio_path = recorded_inputs[index]
        command.extend(["-i", str(audio_path)])
    gaps = [max(0.0, timing.gap_after) for timing in timings[:-1]]
    audible_gaps = [
        (index, gap)
        for index, gap in enumerate(gaps)
        if gap > 0.000001
    ]
    for _, gap in audible_gaps:
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{gap:.3f}",
                "-i",
                f"anullsrc=r={NARRATION_SAMPLE_RATE}:cl=stereo",
            ]
        )

    audio_format = NARRATION_AUDIO_FORMAT
    loudnorm_filters: dict[int, str] = {}
    loudnorm_cache: dict[Path, str] = {}
    for index, timing in enumerate(timings):
        if pre_normalized or timing.audio_kind != "tts":
            continue
        audio_path = (task_dir / timing.audio_path).resolve()
        # Two-pass measurements must describe the SAME post-split, post-tempo
        # audio used in pass two. Group statistics hide sentence-level changes.
        if audio_path not in loudnorm_cache:
            loudnorm_cache[audio_path] = await _two_pass_loudnorm_filter(
                task_dir,
                audio_path,
                target_lufs=target_lufs,
                target_lra=target_lra,
                true_peak_dbfs=true_peak_dbfs,
            )
        loudnorm_filters[index] = loudnorm_cache[audio_path]
    filters: list[str] = []
    for index, timing in enumerate(timings):
        fade_duration = min(AUDIO_EDGE_FADE_SECONDS, timing.duration / 4)
        fade_out_start = max(0.0, timing.duration - fade_duration)
        normalization_filter = loudnorm_filters.get(index, "")
        previous_same_group = bool(
            timing.tts_group_id
            and index > 0
            and timings[index - 1].tts_group_id == timing.tts_group_id
        )
        next_same_group = bool(
            timing.tts_group_id
            and index + 1 < len(timings)
            and timings[index + 1].tts_group_id == timing.tts_group_id
        )
        operations = [audio_format]
        if normalization_filter:
            operations.extend(
                [normalization_filter, f"aresample={NARRATION_SAMPLE_RATE}"]
            )
        if not pre_normalized and not previous_same_group:
            operations.append(f"afade=t=in:st=0:d={fade_duration:.6f}")
        if not pre_normalized and not next_same_group:
            operations.append(
                f"afade=t=out:st={fade_out_start:.6f}:d={fade_duration:.6f}"
            )
        filters.append(f"[{index}:a]{','.join(operations)}[a{index}]")
    if audible_gaps:
        for input_offset, (timing_index, gap) in enumerate(audible_gaps):
            input_index = len(timings) + input_offset
            filters.append(
                f"[{input_index}:a]atrim=duration={gap:.3f},{audio_format}[s{timing_index}]"
            )
        concat_inputs = "".join(
            f"[a{index}]" + (f"[s{index}]" if index < len(gaps) and gaps[index] > 0.000001 else "")
            for index in range(len(timings))
        )
        filters.append(
            f"{concat_inputs}concat=n={len(timings) + len(audible_gaps)}:v=0:a=1[outa]"
        )
    elif len(timings) > 1:
        concat_inputs = "".join(f"[a{index}]" for index in range(len(timings)))
        filters.append(f"{concat_inputs}concat=n={len(timings)}:v=0:a=1[outa]")
    else:
        filters.append("[a0]anull[outa]")

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outa]",
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    await run_logged_command(command, task_dir, "拼接完整旁白")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise TTSProcessingError(f"旁白拼接未生成有效文件：{output_path.name}")
    return output_path


async def _two_pass_loudnorm_filter(
    task_dir: Path,
    audio_path: Path,
    *,
    target_lufs: float,
    target_lra: float,
    true_peak_dbfs: float,
    strict: bool = False,
) -> str:
    # Match the render's channel conversion in BOTH passes. Measuring mono
    # with dual_mono then rematrixing only after loudnorm changes the gain.
    analysis_filter = (
        f"{NARRATION_AUDIO_FORMAT},"
        f"loudnorm=I={target_lufs}:LRA={target_lra}:TP={true_peak_dbfs}:"
        "dual_mono=true:print_format=json"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(audio_path),
        "-af",
        analysis_filter,
        "-f",
        "null",
        "NUL" if os.name == "nt" else "/dev/null",
    ]
    stderr = await run_capture_stderr_command(
        command,
        task_dir,
        f"分析逐句响度 {audio_path.name}",
    )
    measurement = _parse_loudnorm_measurement(stderr)
    if measurement is None:
        if strict:
            raise TTSProcessingError("无法测量模式音频响度，不能把未知响度视为达标。")
        write_text_log(task_dir, f"逐句响度两遍分析失败，回退动态归一化：{audio_path.name}")
        return (
            f"loudnorm=I={target_lufs}:LRA={target_lra}:TP={true_peak_dbfs}:"
            "dual_mono=true"
        )
    return (
        f"loudnorm=I={target_lufs}:LRA={target_lra}:TP={true_peak_dbfs}:dual_mono=true:"
        f"measured_I={measurement['input_i']}:measured_LRA={measurement['input_lra']}:"
        f"measured_TP={measurement['input_tp']}:measured_thresh={measurement['input_thresh']}:"
        f"offset={measurement['target_offset']}:linear=true:print_format=summary"
    )


def _parse_loudnorm_measurement(stderr: str) -> dict[str, float] | None:
    candidates = re.findall(r"\{\s*\"input_i\".*?\}", stderr, flags=re.DOTALL)
    if not candidates:
        return None
    try:
        payload = json.loads(candidates[-1])
        keys = ("input_i", "input_lra", "input_tp", "input_thresh", "target_offset")
        values = {key: float(payload[key]) for key in keys}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not all(math.isfinite(value) for value in values.values()):
        return None
    return values


async def extract_mode_pcm(
    task_dir: Path, source_path: Path, output_path: Path, *,
    source_start: float, source_end: float, audio_offset_seconds: float = 0.0,
) -> dict[str, object]:
    """Decode ORIGINAL audio once to lossless stereo PCM48k; no tempo/pad/fade.

    Editorial clocks are video-relative original-upload seconds. Decoded audio
    sample zero is audio_offset_seconds on that clock, NOT the prepared trim.
    Sample quantization is recorded; missing EOF samples are an error, not padded.
    """
    root = task_dir.resolve()
    source = source_path.resolve()
    destination = output_path.resolve()
    if not source.is_relative_to(root) or not source.is_file() or not destination.is_relative_to(root):
        raise TTSProcessingError("模式音频源或输出路径不安全。")
    if source == destination:
        raise TTSProcessingError("不能覆盖原声源文件。")
    if not all(math.isfinite(v) for v in (source_start, source_end, audio_offset_seconds)):
        raise TTSProcessingError("原声时钟必须是有限数值。")
    start_sample = round((source_start - audio_offset_seconds) * NARRATION_SAMPLE_RATE)
    end_sample = round((source_end - audio_offset_seconds) * NARRATION_SAMPLE_RATE)
    if source_start < audio_offset_seconds or start_sample < 0 or end_sample <= start_sample:
        raise TTSProcessingError("原声范围超出实际音轨，不能补静音或用 TTS 替代。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await run_logged_command([
        "ffmpeg", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
        "-map", "0:a:0", "-vn", "-af",
        f"aformat=sample_rates=48000:channel_layouts=stereo,aresample=48000,"
        f"asetpts=PTS-STARTPTS,atrim=start_sample={start_sample}:end_sample={end_sample},"
        "asetpts=PTS-STARTPTS", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
        str(output_path),
    ], task_dir, "提取真实模式音频 PCM（不补齐、不变速）")
    samples = _verified_pcm_samples(output_path)
    if samples != end_sample - start_sample:
        raise TTSProcessingError("原声音轨不足所选范围，已拒绝截断或填充音频。")
    return {
        "source_start": source_start, "source_end": source_end,
        "audio_offset_seconds": audio_offset_seconds,
        "decoded_start_sample": start_sample, "decoded_end_sample": end_sample,
        "actual_source_start": audio_offset_seconds + start_sample / NARRATION_SAMPLE_RATE,
        "actual_source_end": audio_offset_seconds + end_sample / NARRATION_SAMPLE_RATE,
        "sample_rate": NARRATION_SAMPLE_RATE, "samples": samples,
    }


async def normalize_mode_unit(
    task_dir: Path, timing: SentenceTiming, output_path: Path, *,
    enhance_speech: bool = False,
) -> SentenceTiming:
    """Normalize a derivative, including quotes; never modify reusable raw audio.

    -3.5 dBTP provides headroom for delivery AAC. The *measured* derivative is
    retained in SentenceTiming. No TTS tempo or edge removal is applied here.
    """
    source = _sentence_audio_path(task_dir, timing)
    if output_path.resolve() == source or not output_path.resolve().is_relative_to(task_dir.resolve()):
        raise TTSProcessingError("逐句响度输出不能覆盖源音频或离开任务目录。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mode-normalize-", dir=task_dir) as directory:
        reference = Path(directory) / "reference.wav"
        await run_logged_command([
            "ffmpeg", "-y", "-i", str(source), "-map", "0:a:0", "-vn",
            "-af", NARRATION_AUDIO_FORMAT, "-c:a", "pcm_s16le", str(reference),
        ], task_dir, "统一模式音频 PCM 通道（不改变时间）")
        before = _verified_pcm_samples(reference)
        if enhance_speech and timing.audio_kind == "sync":
            source, _receipt = await _prepare_enhanced_recording(task_dir, timing, Path(directory), 0)
        else:
            source = reference
        loudness = await _two_pass_loudnorm_filter(
            task_dir, source, target_lufs=-20.0, target_lra=5.0,
            true_peak_dbfs=-3.5, strict=True,
        )
        await run_logged_command([
            "ffmpeg", "-y", "-i", str(source), "-map", "0:a:0", "-vn", "-af",
            f"{NARRATION_AUDIO_FORMAT},{loudness},aresample=48000",
            "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(output_path),
        ], task_dir, "逐句模式响度归一（旁白和真实原声）")
        after = _verified_pcm_samples(output_path)
        if after != before:
            raise TTSProcessingError("响度处理改变了 PCM 样本数，拒绝补齐或裁短。")
    measured = await measure_mode_unit(task_dir, timing.model_copy(update={
        "audio_path": output_path.relative_to(task_dir).as_posix(),
        "duration": after / NARRATION_SAMPLE_RATE, "start": 0.0, "end": after / NARRATION_SAMPLE_RATE,
    }))
    if abs(measured.integrated_lufs + 20.0) > 2.0 or measured.true_peak_dbfs > -3.0:
        raise TTSProcessingError("逐句模式音频没有达到 -20±2 LUFS / -3 dBTP，不能宣称达标。")
    return measured


async def measure_mode_unit(task_dir: Path, timing: SentenceTiming) -> SentenceTiming:
    """Measure the actual post-edit unit without altering continuous-group DSP.

    Local outliers are retained for QC, not hidden by group metric inheritance
    or repaired by a discontinuous second per-sentence gain adjustment.
    """
    output_path = _sentence_audio_path(task_dir, timing)
    after = _verified_pcm_samples(output_path)
    stderr = await run_capture_stderr_command([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(output_path), "-af",
        f"{NARRATION_AUDIO_FORMAT},loudnorm=I=-20:LRA=5:TP=-3.5:print_format=json",
        "-f", "null", "NUL" if os.name == "nt" else "/dev/null",
    ], task_dir, "复测逐句模式响度和真峰值")
    measurement = _parse_loudnorm_measurement(stderr)
    if measurement is None:
        raise TTSProcessingError("逐句模式音频无法测量响度或峰值。")
    duration = after / NARRATION_SAMPLE_RATE
    if any(word.start < 0 or word.end > duration + 1 / NARRATION_SAMPLE_RATE for word in timing.words):
        raise TTSProcessingError("真实词时间超出处理后的音频，禁止推算或拉伸时间。")
    return timing.model_copy(update={
        "audio_path": output_path.relative_to(task_dir).as_posix(),
        "duration": duration, "start": 0.0, "end": duration,
        "integrated_lufs": measurement["input_i"], "true_peak_dbfs": measurement["input_tp"],
    })
