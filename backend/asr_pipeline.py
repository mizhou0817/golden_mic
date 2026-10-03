from __future__ import annotations

import asyncio
import array
import hashlib
import json
import math
import re
import sys
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal, Protocol, cast

from pydantic import BaseModel, Field
from silero_vad_lite import SileroVAD  # pyright: ignore[reportMissingTypeStubs]

from .media import probe_media, run_logged_command
from .models import (
    AnnotatedShot,
    BeatMatch,
    MatchCandidate,
    MatchPlanItem,
    Sentence,
    SyncSoundSelection,
    UploadedAsset,
)
from .providers.asr import ASRTranscript
from .storage import write_json_atomic, write_text_log
from .speech_analysis import speech_present


ASRProgressCallback = Callable[[int, int, str], None]
ASR_MAX_CONCURRENCY = 4
VAD_SAMPLE_RATE = 16_000
VAD_FRAME_MS = 32
VAD_FRAME_SAMPLES = VAD_SAMPLE_RATE * VAD_FRAME_MS // 1000
ASR_CACHE_SCHEMA_VERSION = 1
ASR_NORMALIZATION_RECIPE = "pcm_s16le_mono_16000_v1"
_ASR_CACHE_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class SpeechActivity:
    has_speech: bool
    speech_ms: int
    analyzed_ms: int
    max_probability: float


class EmbeddingClient(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class ASRClient(Protocol):
    def validate_configuration(self) -> None:
        ...

    async def transcribe(self, audio_path: Path) -> ASRTranscript:
        ...


class _SpeechDetector(Protocol):
    def process(self, data: array.array[float]) -> float:
        ...


class SourceASRRecord(BaseModel):
    source_index: int = Field(ge=0)
    source_name: str
    source_media_path: str
    asr_audio_path: str | None = None
    status: Literal["available", "no_audio", "no_speech", "failed"]
    transcript: ASRTranscript | None = None
    vad_speech_ms: int | None = Field(default=None, ge=0)
    vad_analyzed_ms: int | None = Field(default=None, ge=0)
    vad_max_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    cache_hit: bool = False
    semantic_retry_count: int = Field(default=0, ge=0)
    error: str | None = None


class _UtteranceCandidate(BaseModel):
    candidate_id: int = Field(ge=0)
    source_index: int = Field(ge=0)
    source_media_path: str
    text: str
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    shot_id: int = Field(ge=0)


async def transcribe_source_audio(
    task_dir: Path,
    uploads: Sequence[UploadedAsset],
    provider: ASRClient,
    progress: ASRProgressCallback,
    *,
    concurrency: int = 1,
    vad_enabled: bool = False,
    vad_threshold: float = 0.2,
    vad_min_speech_ms: int = VAD_FRAME_MS,
    cache_dir: Path | None = None,
    cache_ttl_hours: int = 168,
    cache_max_entries: int = 5000,
    sparse_retry_enabled: bool = True,
) -> list[SourceASRRecord]:
    if not 1 <= concurrency <= ASR_MAX_CONCURRENCY:
        raise ValueError(f"ASR 并发数必须为 1 到 {ASR_MAX_CONCURRENCY}。")
    if not 0.0 <= vad_threshold <= 1.0:
        raise ValueError("VAD 阈值必须在 0 到 1 之间。")
    if vad_min_speech_ms < VAD_FRAME_MS:
        raise ValueError(f"VAD 最短语音时长不能小于 {VAD_FRAME_MS}ms。")

    asr_dir = task_dir / "asr"
    asr_dir.mkdir(parents=True, exist_ok=True)
    if cache_dir is not None:
        await asyncio.to_thread(
            _prune_asr_cache,
            cache_dir,
            ttl_hours=cache_ttl_hours,
            max_entries=cache_max_entries,
        )
    records: list[SourceASRRecord | None] = [None] * len(uploads)
    total = len(uploads)
    semaphore = asyncio.Semaphore(concurrency)
    progress_lock = asyncio.Lock()
    completed = 0

    async def process_source(source_index: int, upload: UploadedAsset) -> None:
        nonlocal completed
        source_media_path = upload.path.relative_to(task_dir).as_posix()
        async with semaphore:
            try:
                probe = await probe_media(upload.path, task_dir)
                streams = probe.get("streams")
                typed_streams = cast(list[object], streams) if isinstance(streams, list) else []
                has_audio = any(
                    isinstance(stream, dict)
                    and cast(dict[str, object], stream).get("codec_type") == "audio"
                    for stream in typed_streams
                )
                if not has_audio:
                    record = SourceASRRecord(
                        source_index=source_index,
                        source_name=upload.original_name,
                        source_media_path=source_media_path,
                        status="no_audio",
                    )
                    write_text_log(task_dir, f"同期声 source={source_index} 无音轨，跳过 ASR")
                else:
                    provider.validate_configuration()
                    asr_audio_path = asr_dir / f"source_{source_index}.wav"
                    await run_logged_command(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(upload.path),
                            "-map",
                            "0:a:0",
                            "-vn",
                            "-ac",
                            "1",
                            "-ar",
                            str(VAD_SAMPLE_RATE),
                            "-c:a",
                            "pcm_s16le",
                            str(asr_audio_path),
                        ],
                        task_dir,
                        f"提取同期声音轨 source={source_index}",
                    )
                    activity = await _analyze_vad_or_fail_open(
                        task_dir,
                        source_index,
                        asr_audio_path,
                        enabled=vad_enabled,
                        threshold=vad_threshold,
                        min_speech_ms=vad_min_speech_ms,
                    )
                    if activity is not None and not activity.has_speech:
                        record = SourceASRRecord(
                            source_index=source_index,
                            source_name=upload.original_name,
                            source_media_path=source_media_path,
                            asr_audio_path=asr_audio_path.relative_to(task_dir).as_posix(),
                            status="no_speech",
                            vad_speech_ms=activity.speech_ms,
                            vad_analyzed_ms=activity.analyzed_ms,
                            vad_max_probability=activity.max_probability,
                        )
                        write_text_log(
                            task_dir,
                            f"同期声 source={source_index} VAD 未检测到讲话，跳过 ASR "
                            f"speech_ms={activity.speech_ms} analyzed_ms={activity.analyzed_ms} "
                            f"max_probability={activity.max_probability:.3f}",
                        )
                    else:
                        cache_hit = False
                        semantic_retry_count = 0
                        if cache_dir is not None:
                            audio_hash = await asyncio.to_thread(_sha256_file, asr_audio_path)
                            cache_signature = _asr_cache_signature(provider)
                            cache_key = hashlib.sha256(
                                json.dumps(
                                    {
                                        "audio_sha256": audio_hash,
                                        "provider": cache_signature,
                                        "normalization": ASR_NORMALIZATION_RECIPE,
                                        "schema_version": ASR_CACHE_SCHEMA_VERSION,
                                    },
                                    ensure_ascii=False,
                                    sort_keys=True,
                                ).encode("utf-8")
                            ).hexdigest()
                            cache_path = cache_dir / cache_key[:2] / f"{cache_key}.json"
                            lock = _ASR_CACHE_LOCKS.setdefault(str(cache_path.resolve()), asyncio.Lock())
                            async with lock:
                                transcript = _load_asr_cache(
                                    cache_path,
                                    cache_key,
                                    ttl_hours=cache_ttl_hours,
                                )
                                if transcript is not None and _is_suspicious_transcript(
                                    transcript,
                                    activity,
                                ):
                                    cache_path.unlink(missing_ok=True)
                                    transcript = None
                                if transcript is not None:
                                    cache_hit = True
                                else:
                                    transcript, semantic_retry_count = await _transcribe_stably(
                                        task_dir,
                                        source_index,
                                        asr_audio_path,
                                        provider,
                                        activity,
                                        sparse_retry_enabled=sparse_retry_enabled,
                                    )
                                    _write_asr_cache(cache_path, cache_key, transcript)
                        else:
                            transcript, semantic_retry_count = await _transcribe_stably(
                                task_dir,
                                source_index,
                                asr_audio_path,
                                provider,
                                activity,
                                sparse_retry_enabled=sparse_retry_enabled,
                            )
                        record = SourceASRRecord(
                            source_index=source_index,
                            source_name=upload.original_name,
                            source_media_path=source_media_path,
                            asr_audio_path=asr_audio_path.relative_to(task_dir).as_posix(),
                            status="available",
                            transcript=transcript,
                            vad_speech_ms=activity.speech_ms if activity else None,
                            vad_analyzed_ms=activity.analyzed_ms if activity else None,
                            vad_max_probability=activity.max_probability if activity else None,
                            cache_hit=cache_hit,
                            semantic_retry_count=semantic_retry_count,
                        )
                        write_text_log(
                            task_dir,
                            f"同期声 source={source_index} ASR utterances={len(transcript.utterances)} "
                            f"duration_ms={transcript.duration_ms} cache_hit={cache_hit} "
                            f"semantic_retries={semantic_retry_count}",
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = str(exc)[:1000]
                record = SourceASRRecord(
                    source_index=source_index,
                    source_name=upload.original_name,
                    source_media_path=source_media_path,
                    status="failed",
                    error=error,
                )
                write_text_log(task_dir, f"同期声 source={source_index} ASR unavailable={error}")

        async with progress_lock:
            records[source_index] = record
            completed += 1
            if record.status == "no_speech":
                action = "VAD 跳过无讲话素材"
            elif record.status == "no_audio":
                action = "跳过无音轨素材"
            elif record.status == "failed":
                action = "同期声识别失败并回退 TTS"
            else:
                action = "同期声识别"
            progress(completed, total, f"{action} {completed}/{total}")

    await asyncio.gather(
        *(process_source(source_index, upload) for source_index, upload in enumerate(uploads))
    )
    finalized_records = [record for record in records if record is not None]
    if len(finalized_records) != total:
        raise RuntimeError("同期声识别结果数量与上传素材不一致。")

    write_json_atomic(
        task_dir / "asr_transcripts.json",
        [record.model_dump(mode="json") for record in finalized_records],
    )
    return finalized_records


async def _transcribe_stably(
    task_dir: Path,
    source_index: int,
    audio_path: Path,
    provider: ASRClient,
    activity: SpeechActivity | None,
    *,
    sparse_retry_enabled: bool,
) -> tuple[ASRTranscript, int]:
    first = await provider.transcribe(audio_path)
    if not sparse_retry_enabled or not _is_suspicious_transcript(first, activity):
        return first, 0
    write_text_log(
        task_dir,
        f"同期声 source={source_index} 首次 ASR 文本相对 VAD 讲话量偏少，执行一次稳定性复核",
    )
    try:
        second = await provider.transcribe(audio_path)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        write_text_log(
            task_dir,
            f"同期声 source={source_index} ASR 稳定性复核失败，保留首次结果：{exc}",
        )
        return first, 1
    selected = max((first, second), key=_transcript_quality_score)
    write_text_log(
        task_dir,
        f"同期声 source={source_index} ASR 稳定性复核完成 "
        f"first_chars={_normalized_transcript_length(first.text)} "
        f"second_chars={_normalized_transcript_length(second.text)}",
    )
    return selected, 1


def _is_suspicious_transcript(
    transcript: ASRTranscript,
    activity: SpeechActivity | None,
) -> bool:
    if activity is None or activity.speech_ms < 3000 or transcript.duration_ms < 8000:
        return False
    minimum_characters = max(12, math.ceil(activity.speech_ms / 1000.0 * 4.0))
    return _normalized_transcript_length(transcript.text) < minimum_characters


def _normalized_transcript_length(text: str) -> int:
    return len(re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text))


def _transcript_quality_score(transcript: ASRTranscript) -> tuple[int, int, int]:
    definite_duration = sum(
        max(0, utterance.end_time_ms - utterance.start_time_ms)
        for utterance in transcript.utterances
        if utterance.definite and utterance.text.strip()
    )
    return (
        _normalized_transcript_length(transcript.text),
        definite_duration,
        len(transcript.utterances),
    )


def _asr_cache_signature(provider: ASRClient) -> str:
    identity = {
        "provider": f"{type(provider).__module__}.{type(provider).__qualname__}",
        "base_url": str(getattr(provider, "base_url", "")),
        "endpoint_path": str(getattr(provider, "endpoint_path", "")),
        "resource_id": str(getattr(provider, "resource_id", "")),
        "cluster_id": str(getattr(provider, "cluster_id", "")),
        "request": "bigmodel_zh_cn_itn_punc_utterances_full_v1",
    }
    return json.dumps(identity, ensure_ascii=False, sort_keys=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_asr_cache(
    path: Path,
    expected_key: str,
    *,
    ttl_hours: int,
) -> ASRTranscript | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created_at = float(payload["created_at_epoch"])
        if (
            payload.get("schema_version") != ASR_CACHE_SCHEMA_VERSION
            or payload.get("cache_key") != expected_key
            or time.time() - created_at > ttl_hours * 3600
        ):
            path.unlink(missing_ok=True)
            return None
        return ASRTranscript.model_validate(payload["transcript"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def _write_asr_cache(path: Path, cache_key: str, transcript: ASRTranscript) -> None:
    write_json_atomic(
        path,
        {
            "schema_version": ASR_CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "created_at_epoch": time.time(),
            "transcript": transcript.model_dump(mode="json"),
        },
    )


def _prune_asr_cache(cache_dir: Path, *, ttl_hours: int, max_entries: int) -> None:
    if not cache_dir.is_dir():
        return
    now = time.time()
    live: list[Path] = []
    for path in cache_dir.rglob("*.json"):
        try:
            if now - path.stat().st_mtime > ttl_hours * 3600:
                path.unlink(missing_ok=True)
            else:
                live.append(path)
        except OSError:
            continue
    if len(live) <= max_entries:
        return
    for path in sorted(live, key=lambda item: item.stat().st_mtime)[: len(live) - max_entries]:
        path.unlink(missing_ok=True)


async def _analyze_vad_or_fail_open(
    task_dir: Path,
    source_index: int,
    audio_path: Path,
    *,
    enabled: bool,
    threshold: float,
    min_speech_ms: int,
) -> SpeechActivity | None:
    if not enabled:
        return None
    try:
        activity = await asyncio.to_thread(
            analyze_speech_activity,
            audio_path,
            threshold=threshold,
            min_speech_ms=min_speech_ms,
        )
    except Exception as exc:
        write_text_log(
            task_dir,
            f"同期声 source={source_index} VAD 分析失败，保守继续 ASR：{exc}",
        )
        return None
    write_text_log(
        task_dir,
        f"同期声 source={source_index} VAD has_speech={activity.has_speech} "
        f"speech_ms={activity.speech_ms} analyzed_ms={activity.analyzed_ms} "
        f"max_probability={activity.max_probability:.3f}",
    )
    return activity


def analyze_speech_activity(
    audio_path: Path,
    *,
    threshold: float,
    min_speech_ms: int,
) -> SpeechActivity:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("VAD 阈值必须在 0 到 1 之间。")
    if min_speech_ms < VAD_FRAME_MS:
        raise ValueError(f"VAD 最短语音时长不能小于 {VAD_FRAME_MS}ms。")

    detector = cast(_SpeechDetector, SileroVAD(VAD_SAMPLE_RATE))
    analyzed_frames = 0
    speech_frames = 0
    max_probability = 0.0
    with wave.open(str(audio_path), "rb") as audio_file:
        if (
            audio_file.getnchannels() != 1
            or audio_file.getsampwidth() != 2
            or audio_file.getframerate() != VAD_SAMPLE_RATE
        ):
            raise ValueError("VAD 仅支持 16kHz、16-bit、单声道 PCM WAV。")
        while True:
            frame = audio_file.readframes(VAD_FRAME_SAMPLES)
            if len(frame) < VAD_FRAME_SAMPLES * 2:
                break
            samples = array.array("h")
            samples.frombytes(frame)
            if sys.byteorder != "little":
                samples.byteswap()
            normalized = array.array("f", (sample / 32768.0 for sample in samples))
            probability = float(detector.process(normalized))
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("Invalid local VAD probability")
            analyzed_frames += 1
            max_probability = max(max_probability, probability)
            if probability >= threshold:
                speech_frames += 1

    analyzed_ms = analyzed_frames * VAD_FRAME_MS
    speech_ms = speech_frames * VAD_FRAME_MS
    # Short, malformed, or otherwise insufficient inputs fail open so VAD can never
    # suppress an audio clip without enough evidence.
    has_speech = analyzed_ms < min_speech_ms or speech_present(speech_ms / 1000, audio_file.getnframes() / VAD_SAMPLE_RATE)
    return SpeechActivity(
        has_speech=has_speech,
        speech_ms=speech_ms,
        analyzed_ms=analyzed_ms,
        max_probability=round(max_probability, 6),
    )


async def apply_sync_sound_matches(
    task_dir: Path,
    sentences: Sequence[Sentence],
    shots: Sequence[AnnotatedShot],
    match_plan: Sequence[MatchPlanItem],
    source_records: Sequence[SourceASRRecord],
    embedding_provider: EmbeddingClient,
    *,
    similarity_threshold: float,
    min_text_overlap: float,
    max_duration_seconds: float,
) -> list[MatchPlanItem]:
    candidates = _collect_candidates(source_records, shots, max_duration_seconds)
    if not candidates:
        return list(match_plan)

    sentence_vectors = await embedding_provider.embed([sentence.text for sentence in sentences])
    utterance_vectors = await embedding_provider.embed([candidate.text for candidate in candidates])
    pairs: list[tuple[float, float, int, int]] = []
    for sentence_index, sentence in enumerate(sentences):
        for candidate_index, candidate in enumerate(candidates):
            similarity = _cosine_similarity(sentence_vectors[sentence_index], utterance_vectors[candidate_index])
            text_overlap = _text_overlap(sentence.text, candidate.text)
            if similarity >= similarity_threshold and text_overlap >= min_text_overlap:
                pairs.append((similarity, text_overlap, sentence_index, candidate_index))

    selected: dict[int, SyncSoundSelection] = {}
    used_candidates: set[int] = set()
    reserved_owner_by_shot: dict[int, int] = {}
    reserved_shots_by_sentence: dict[int, set[int]] = {}
    for item in match_plan:
        item_shots = set(_effective_match_shot_ids(item))
        reserved_shots_by_sentence[item.sentence_id] = item_shots
        for shot_id in item_shots:
            owner = reserved_owner_by_shot.get(shot_id)
            if owner is not None and owner != item.sentence_id:
                raise RuntimeError(
                    f"同期声匹配前镜头 {shot_id} 已被句子 {owner} 和 {item.sentence_id} 重复引用。"
                )
            reserved_owner_by_shot[shot_id] = item.sentence_id
    for similarity, text_overlap, sentence_index, candidate_index in sorted(
        pairs,
        key=lambda item: (-item[0], -item[1], item[2], item[3]),
    ):
        sentence_id = sentences[sentence_index].sentence_id
        candidate = candidates[candidate_index]
        if sentence_id in selected or candidate.candidate_id in used_candidates:
            continue
        owner = reserved_owner_by_shot.get(candidate.shot_id)
        if owner is not None and owner != sentence_id:
            continue
        for previous_shot_id in reserved_shots_by_sentence.get(sentence_id, set()):
            if reserved_owner_by_shot.get(previous_shot_id) == sentence_id:
                reserved_owner_by_shot.pop(previous_shot_id, None)
        reserved_shots_by_sentence[sentence_id] = {candidate.shot_id}
        reserved_owner_by_shot[candidate.shot_id] = sentence_id
        selected[sentence_id] = SyncSoundSelection(
            source_index=candidate.source_index,
            source_media_path=candidate.source_media_path,
            shot_id=candidate.shot_id,
            text=candidate.text,
            start=candidate.start,
            end=candidate.end,
            similarity=round(similarity, 6),
        )
        used_candidates.add(candidate.candidate_id)

    if not selected:
        return list(match_plan)

    updated: list[MatchPlanItem] = []
    for item in match_plan:
        selection = selected.get(item.sentence_id)
        if selection is None:
            updated.append(item)
            continue
        candidates_by_id = {candidate.shot_id: candidate for candidate in item.candidates}
        candidates_by_id[selection.shot_id] = MatchCandidate(
            shot_id=selection.shot_id,
            similarity=selection.similarity,
        )
        ranked_candidates = sorted(
            candidates_by_id.values(),
            key=lambda candidate: (-candidate.similarity, candidate.shot_id),
        )[:5]
        updated_item = item.model_copy(
            update={
                "shot_id": selection.shot_id,
                "confidence": max(item.confidence, selection.similarity),
                "is_fallback": False,
                "candidates": ranked_candidates,
                "beat_matches": [
                    BeatMatch(
                        beat_id=0,
                        text=item.text,
                        shot_id=selection.shot_id,
                        confidence=max(item.confidence, selection.similarity),
                        candidates=ranked_candidates,
                    )
                ],
                "sync_sound": selection,
            }
        )
        updated.append(updated_item)
        write_text_log(
            task_dir,
            f"句子 {item.sentence_id} 使用同期声 source={selection.source_index} "
            f"range={selection.start:.3f}-{selection.end:.3f}s similarity={selection.similarity:.3f}",
        )

    effective_shots = [
        shot_id
        for item in updated
        for shot_id in _effective_match_shot_ids(item)
    ]
    if len(effective_shots) != len(set(effective_shots)):
        raise RuntimeError("同期声替换后出现重复镜头，已拒绝提交匹配计划。")
    write_json_atomic(
        task_dir / "match_plan.json",
        [item.model_dump(mode="json", exclude_none=True) for item in updated],
    )
    return updated


def _effective_match_shot_ids(item: MatchPlanItem) -> list[int]:
    if item.sync_sound is not None:
        return [item.sync_sound.shot_id]
    if item.beat_matches:
        return [beat.shot_id for beat in item.beat_matches]
    return [item.shot_id]


def _collect_candidates(
    source_records: Sequence[SourceASRRecord],
    shots: Sequence[AnnotatedShot],
    max_duration_seconds: float,
) -> list[_UtteranceCandidate]:
    candidates: list[_UtteranceCandidate] = []
    for record in source_records:
        if record.status != "available" or record.transcript is None:
            continue
        for utterance in record.transcript.utterances:
            if not utterance.definite:
                continue
            text = utterance.text.strip()
            start = utterance.start_time_ms / 1000.0
            end = utterance.end_time_ms / 1000.0
            duration = end - start
            if len(_normalize_text(text)) < 4 or duration < 0.6 or duration > max_duration_seconds:
                continue
            shot = _best_overlapping_shot(record.source_index, start, end, shots)
            if shot is None:
                continue
            padded_start = max(0.0, start - 0.12)
            source_end = record.transcript.duration_ms / 1000.0 if record.transcript.duration_ms else end + 0.18
            padded_end = min(source_end, end + 0.18)
            if padded_end <= padded_start:
                continue
            candidates.append(
                _UtteranceCandidate(
                    candidate_id=len(candidates),
                    source_index=record.source_index,
                    source_media_path=record.source_media_path,
                    text=text,
                    start=round(padded_start, 6),
                    end=round(padded_end, 6),
                    shot_id=shot.shot_id,
                )
            )
    return candidates


def _best_overlapping_shot(
    source_index: int,
    start: float,
    end: float,
    shots: Sequence[AnnotatedShot],
) -> AnnotatedShot | None:
    source_shots = [
        shot
        for shot in shots
        if shot.source_index == source_index and shot.status == "available"
    ]
    if not source_shots:
        return None
    midpoint = (start + end) / 2.0
    best = max(
        source_shots,
        key=lambda shot: (
            max(0.0, min(end, shot.end) - max(start, shot.start)),
            -abs(((shot.start + shot.end) / 2.0) - midpoint),
            -shot.shot_id,
        ),
    )
    overlap = max(0.0, min(end, best.end) - max(start, best.start))
    return best if overlap > 0.0 else None


def _normalize_text(text: str) -> str:
    return "".join(re.findall(r"[\w\u4e00-\u9fff]", text.lower()))


def _text_overlap(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot_product / (left_norm * right_norm)))
