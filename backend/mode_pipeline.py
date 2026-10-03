"""Evidence-preserving A/B/C pipeline and temporary-workspace revision API.

No task-manager/application/workbench import and no module-level pipeline import.
pretranscripts.json is the capability-free UploadStore.materialize snapshot list;
ready entries are NEVER transcribed again. Quote source clocks always refer to
the original upload. production_mode.json holds derivative/cache receipts, never
credentials. A caller publishes a revision only after this module returns.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import tempfile
import wave
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path
from typing import Any, TYPE_CHECKING

from . import production_modes as rules
from .asr_pipeline import SourceASRRecord
from .graphics import generate_mode_graphics, generate_mode_subtitles
from .matching import MatchingError, build_match_plan, extract_visual_beats
from .media import normalize_assets, probe_media, require_media_tools, run_logged_command
from .media_input import append_source_notes, processing_uploads
from .models import (
    AnnotatedShot, EDLClip, EDLItem, EditingPreferences, MatchCandidate,
    MatchPlanItem, ScriptDocument, Sentence, SentenceTiming, Shot, TTSWordTiming,
    VisualBeat,
)
from .production_modes import Mode, QuoteTake, SentenceInput, Speaker, TranscriptSegment, Word
from .providers.asr import ASRTranscript, create_asr_provider
from .providers.embedding import EmbeddingProvider
from .providers.llm import LLMProvider
from .providers.tts import create_tts_provider
from .providers.vision import VisionProvider
from .pronunciation import extract_number_expressions
from .quality import enforce_quality_gate, generate_quality_report
from .rendering import (
    FinishOptions, MusicMixOptions, RenderingError, SegmentRenderOptions, render_mode_video,
)
from .reporting import generate_report, mode_report_metadata as _base_mode_report_metadata
from .revisions import local_file, read_json
from .storage import write_json_atomic, write_text_log
from .tts_pipeline import (
    NARRATION_SAMPLE_RATE, TTSProcessingError, _verified_pcm_samples,
    concatenate_narration, extract_mode_pcm, normalize_mode_unit, measure_mode_unit,
    probe_audio_duration, synthesize_narration,
    mode_narration_gap,
)
from .vision_pipeline import extract_shot_thumbnail

if TYPE_CHECKING:
    from .config import Settings
    from .pipeline import PipelineReporter, PipelineTask


MODE_MANIFEST = "production_mode.json"
MODE_RECIPE = "three-mode-evidence-v1"
V2_MEDIA_CONTRACT = "approved-v2-m0-20260929"
REQUIRED_ARTIFACTS = (
    "pipeline_manifest.json", "asr_transcripts.json", "pretranscripts.json",
    "shots.json", "shots_annotated.json", "script_structure.json", "script_segmented.txt",
    "sentences.json", "match_plan.json", "pronunciation_plan.json", "timings.json",
    "narration_profile.json", "tts_manifest.json", "subs.ass", "subtitle_manifest.json",
    "edl.json", "segment_manifest.json", "source_timings.json", "source_edl.json",
    "source_segment_manifest.json", "source_match_plan.json", "source_narration_profile.json",
    "narration.m4a", "video_only.mp4", "final.mp4", "report.json", "quality_report.json",
    MODE_MANIFEST, "broll_pool.json", "jumpcuts.json", "lower_thirds.json",
)
_FPS = 30.0
_SAMPLE = 1 / NARRATION_SAMPLE_RATE


class QuoteMissingError(RuntimeError):
    error_kind = "quote_missing"

    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self.bad_rows = [int(row["idx"]) + 1 for row in rows]
        details = "；".join(f"第 {row['idx'] + 1} 句（实际匹配 {row.get('score', 0):.2f}）" for row in rows)
        super().__init__(f"原话在素材中未找到：{details}。请核对稿句、删除或补传确实含这些原话的素材；不会改用配音。")


class ModeQualityError(RuntimeError):
    error_kind = "qc"
    bad_rows: list[int] = []


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _key(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _v2(manifest: dict[str, Any]) -> bool:
    # Absent on old revisions: never retroactively opt historical work in.
    return manifest.get("media_contract") == V2_MEDIA_CONTRACT


class ModeStageCache:
    """Task-local completed-stage receipts, verified bytes before provider reuse.

    Callers supply stage-specific non-secret signatures, not the requested retry
    index. Missing/incomplete/mismatched receipts are misses; changed bound bytes
    are corruption and block rather than silently spending on another provider.
    Receipt payloads are snapshots; later stages may overwrite conventional JSON.
    """
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, stage: int, signature: str) -> dict[str, Any] | None:
        path = self.root / "mode_stage_cache" / f"stage-{stage}.json"
        if not path.is_file():
            return None
        value = read_json(self.root, path.relative_to(self.root).as_posix())
        if value.get("signature") != signature or value.get("contract") != V2_MEDIA_CONTRACT:
            return None
        if value.get("state") == "started":
            raise ModeQualityError(f"阶段 {stage} 上次 Provider 工作未确认完成；请核对结果后再显式重试，不能自动重复请求。")
        for relative, digest in value["files"].items():
            if _sha(local_file(self.root, relative)) != digest:
                raise ModeQualityError(f"阶段 {stage} 缓存文件绑定不一致；拒绝自动重跑 Provider。")
        return value["payload"]

    def save(self, stage: int, signature: str, payload: dict[str, Any], files: Sequence[str] = ()) -> None:
        value = {"contract": V2_MEDIA_CONTRACT, "state": "complete", "signature": signature, "payload": payload,
                 "files": {relative: _sha(local_file(self.root, relative)) for relative in dict.fromkeys(files)}}
        directory = self.root / "mode_stage_cache"
        directory.mkdir(exist_ok=True)
        write_json_atomic(directory / f"stage-{stage}.json", value)

    def begin(self, stage: int, signature: str) -> None:
        directory = self.root / "mode_stage_cache"
        directory.mkdir(exist_ok=True)
        write_json_atomic(directory / f"stage-{stage}.json", {
            "contract": V2_MEDIA_CONTRACT, "state": "started", "signature": signature,
        })


def _stage_signature(stage: int, settings: Any, *inputs: Any) -> str:
    prefixes = {2: ("asr_", "sync_sound_", "volcengine_asr_", "local_speech_", "local_speaker_"),
                3: ("scene_", "shot_"),
                4: ("vision_", "kimi_", "volcengine_vision_"),
                6: ("retrieval_", "video_embedding_", "entity_", "llm_", "embed_", "kimi_", "volcengine_embedding_", "generative_"),
                7: ("tts_", "volcengine_tts_", "volcengine_voice_", "llm_", "kimi_")}.get(stage, ())
    # Bind credential changes by digest, never persist secret settings values.
    configured = settings.model_dump(mode="json") if hasattr(settings, "model_dump") else vars(settings)
    selected = {key: str(value) for key, value in configured.items() if key.startswith(prefixes)} if prefixes else {}
    stage_sources = {
        2: ("media.py", "speech_analysis.py", "providers/local_speech.py"),
        3: ("media.py",),
        4: ("vision_pipeline.py", "providers/vision.py"),
        6: ("matching.py", "assignment.py", "production_modes.py", "mode_rules.json"),
        7: ("tts_pipeline.py", "audio_filters.py", "providers/tts.py", "pronunciation.py"),
    }
    implementation = {name: _sha(Path(__file__).parent / name) for name in stage_sources.get(stage, ())}
    return _key([V2_MEDIA_CONTRACT, stage, implementation, _key(selected), inputs])


async def analyze_task_speakers(record: Any, snapshots: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    """Explicit optional local task-level clustering; no filename/name merging.

    Parent may call this after materialization and before preview alignment.
    Settings owned by lifecycle: local_speaker_model_path, local_speech_license_reviewed,
    local_speech_required. No implicit model download or provider call.
    """
    from .providers.local_speech import LocalSpeakerEncoder, SpeechUnavailable, local_speech_readiness
    from .speech_analysis import task_speaker_segments
    from .pipeline import _run_blocking_until_complete

    configured = getattr(settings, "local_speaker_model_path", None)
    path = Path(configured) if configured else None
    reviewed = bool(getattr(settings, "local_speech_license_reviewed", False))
    readiness = local_speech_readiness("speaker_embedding", path, license_reviewed=reviewed)
    if not readiness.available:
        if getattr(settings, "local_speech_required", False):
            raise SpeechUnavailable("speaker_embedding blocked: " + ", ".join(readiness.blocked_prerequisites))
        return readiness.as_dict()
    assert path is not None
    sources = []
    with tempfile.TemporaryDirectory(prefix=".task-speakers-", dir=record.task_dir) as directory:
        for index, (snapshot, upload) in enumerate(zip(snapshots, record.uploads, strict=True)):
            segments = _segments(snapshot)
            payload = snapshot.get("transcript", {})
            observed = payload.get("observed_words", {}) if isinstance(payload, dict) else {}
            # Do not feed two speakers or overlapping turns into a single
            # embedding and then call its centroid a real speaker identity.
            segments = [segment for segment in segments
                        if len({word.get("speaker_id") for word in observed.get(segment.id, []) if word.get("speaker_id")}) <= 1
                        and not any(other.id != segment.id and other.start < segment.end and other.end > segment.start
                                    for other in segments)]
            if not segments:
                continue
            offset = float(snapshot.get("audio_offset_seconds", 0))
            # Each segment remains in its original recording's clock. The local
            # PCM starts at audio zero, so subtract its measured stream offset.
            shifted = [segment.model_copy(update={"start": segment.start - offset, "end": segment.end - offset}) for segment in segments]
            audio = Path(directory) / f"source-{index}.wav"
            await run_logged_command([
                "ffmpeg", "-y", "-protocol_whitelist", "file,pipe", "-i", str(upload.path),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
            ], record.task_dir, "本地说话人特征提取（不联网）")
            sources.append((snapshot["id"], audio, shifted))
        def infer() -> dict[str, Any]:
            return task_speaker_segments(sources, LocalSpeakerEncoder(path, license_reviewed=reviewed))
        result = await _run_blocking_until_complete(infer)
    result["readiness"] = {**readiness.as_dict(), "inference_verified": bool(result["assignments"])}
    result["model_sha256"] = _sha(path)
    # Keep the existing observed/provider speaker IDs intact as evidence; only
    # editorial task snapshots get the stable cross-file cluster assignment.
    mapping = {(a["upload_id"], a["segment_id"]): a["speaker_id"] for a in result["assignments"]}
    for snapshot in snapshots:
        payload = snapshot.get("transcript", [])
        segments = payload.get("segments", []) if isinstance(payload, dict) else payload
        for segment in segments:
            if (snapshot["id"], segment["id"]) in mapping:
                segment["speaker_id"] = mapping[snapshot["id"], segment["id"]]
    write_json_atomic(record.task_dir / "pretranscripts.json", snapshots)
    return result


def _write_models(root: Path, name: str, values: Sequence[Any]) -> None:
    write_json_atomic(root / name, [value.model_dump(mode="json", by_alias=True) for value in values])


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _preferences(record: Any) -> EditingPreferences:
    return EditingPreferences.model_validate(_get(record, "preferences", {})).model_copy(deep=True)


def _mode(record: Any) -> Mode:
    value = _get(record, "mode", "voiceover")
    if value not in rules.MODE_RULES["modes"]:
        raise ValueError("未知制作模式。")
    return value


def _gate(record: Any, settings: Settings) -> str:
    # A per-task preference cannot weaken production's mandatory block policy.
    value = "block" if settings.app_env == "production" else (_get(record, "quality_gate_mode") or settings.quality_gate_mode)
    if value not in {"warn", "block"}:
        raise ValueError("quality_gate_mode 必须为 warn 或 block。")
    return value


def _inputs(record: Any) -> list[SentenceInput]:
    inputs = [SentenceInput.model_validate(value) for value in _get(record, "sentences", [])]
    if not inputs or len({value.idx for value in inputs}) != len(inputs):
        raise ValueError("三模式需要客户端确认的非空、唯一句子列表；不会由模型重写稿件。")
    allowed = rules.MODE_RULES["modes"][_mode(record)]["allowed_kinds"]
    if any(value.kind not in allowed for value in inputs):
        raise ValueError("已确认句子类型与制作模式不一致。")
    return inputs


def _sentences(inputs: Sequence[SentenceInput]) -> list[Sentence]:
    result = []
    for index, value in enumerate(inputs):
        beats = extract_visual_beats(value.text) if value.kind == "narration" else []
        entities = list(dict.fromkeys(entity for beat in beats for entity in beat.entities))
        priority = {"general": 0, "abstract": 1, "entity": 2, "organization": 3, "date": 4}
        intent = max((beat.intent_type for beat in beats), key=priority.get, default="general")
        result.append(Sentence(
            sentence_id=value.idx, text=value.text, kind=value.kind, speaker_hint=value.speaker_hint,
            paragraph_index=index, visual_group_id=value.idx,
            visual_beats=[VisualBeat(beat_id=0, text=value.text, entities=entities,
                                     requires_entity_coverage=any(b.requires_entity_coverage for b in beats),
                                     intent_type=intent)] if value.kind == "narration" else [],
        ))
    return result


def _title(script: str) -> str | None:
    return next((line.strip() for line in script.lstrip("\ufeff").splitlines() if line.strip()), None)


def _write_script(record: Any, inputs: Sequence[SentenceInput]) -> list[Sentence]:
    sentences = _sentences(inputs)
    document = ScriptDocument(title=_title(record.script), sentences=sentences)
    write_json_atomic(record.task_dir / "script_structure.json", document.model_dump(mode="json"))
    _write_models(record.task_dir, "sentences.json", sentences)
    (record.task_dir / "script_segmented.txt").write_text(record.script, encoding="utf-8", newline="")
    return sentences


def _segments(snapshot: dict[str, Any]) -> list[TranscriptSegment]:
    payload = snapshot.get("transcript", [])
    if isinstance(payload, dict):
        payload = payload.get("segments", [])
    if not isinstance(payload, list):
        raise ValueError("预转写必须是 segments 列表或包含 segments 的对象。")
    return [TranscriptSegment.model_validate(value) for value in payload]


def _snapshots(record: Any) -> list[dict[str, Any]]:
    values = read_json(record.task_dir, "pretranscripts.json")
    if not isinstance(values, list) or len(values) != len(record.uploads):
        raise ValueError("预转写快照与原始上传数量不一致。")
    ids = [value.get("id") for value in values if isinstance(value, dict)]
    expected = _get(record, "upload_ids", []) or [upload.upload_id for upload in record.uploads]
    if ids != list(expected) or len(set(ids)) != len(ids) or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("预转写上传顺序/身份与任务不一致。")
    for upload, snapshot in zip(record.uploads, values, strict=True):
        if upload.upload_id and snapshot["id"] != upload.upload_id:
            raise ValueError("预转写快照绑定了其他素材。")
        seconds = snapshot.get("sec")
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("预转写缺少真实源时长。")
        _segments(snapshot)  # Strict validation, no time/text repair.
        if snapshot.get("asr_result") is not None:
            ASRTranscript.model_validate(snapshot["asr_result"])
    return values


def _provider_segments(transcript: ASRTranscript, upload_id: str, seconds: float, offset: float) -> list[dict[str, Any]]:
    """Adapt a NEW real response, never reconstruct ASR from editorial text.

    Same conservative rules as UploadStore: incomplete/overlapping words make a
    segment untrimmed. No speaker clustering, confidence, silence or SNR invented.
    """
    segments = []
    for index, utterance in enumerate(transcript.utterances):
        start, end = utterance.start_time_ms / 1000 + offset, utterance.end_time_ms / 1000 + offset
        if not utterance.definite or not utterance.text.strip() or not 0 <= start < end <= seconds:
            continue
        observed = utterance.words or [w for w in transcript.words if utterance.start_time_ms <= w.start_time_ms <= utterance.end_time_ms]
        words: list[Word] = []
        unsafe = False
        for word in observed:
            s, e = word.start_time_ms / 1000 + offset, word.end_time_ms / 1000 + offset
            if not word.text.strip() or not start <= s < e <= end:
                unsafe = True
            else:
                words.append(Word(w=word.text, s=s, e=e))
        speaker_id = utterance.speaker_id
        if speaker_id is None and observed and all(w.speaker_id is not None for w in observed) and len({w.speaker_id for w in observed}) == 1:
            speaker_id = observed[0].speaker_id
        take = QuoteTake(take_id=f"evidence_{index}", upload_id=upload_id, start=start, end=end,
                         asr_text=utterance.text, score=0.0, speaker_id="", words=words)
        segment = TranscriptSegment(
            id=f"seg_{index}", start=start, end=end, text=utterance.text,
            speaker_id=f"{upload_id}:{speaker_id}" if speaker_id is not None else "",
            words=words if not unsafe and take.precision == "word" else [], confidence=utterance.confidence,
        )
        segments.append(segment.model_dump(mode="json"))
    return segments


async def _retry_failed_asr(record: Any, snapshots: list[dict[str, Any]], settings: Settings, progress: Callable[[int, int, str], None]) -> list[SourceASRRecord]:
    records: list[SourceASRRecord] = []
    for index, (snapshot, upload) in enumerate(zip(snapshots, record.uploads, strict=True)):
        status = snapshot.get("status")
        if status not in {"ready", "asr_failed", "failed"}:
            raise ValueError("素材未完成预转写，不能伪装 ready 或重新提交识别。")
        if status != "ready" and not snapshot.get("task_asr_attempted"):
            # Durable attempt flag BEFORE constructing a provider: failure or
            # cancellation cannot trigger another billable retry on resume.
            snapshot["task_asr_attempted"] = True
            snapshot["task_asr_status"] = "started"
            write_json_atomic(record.task_dir / "pretranscripts.json", snapshots)
            try:
                audio = record.task_dir / "asr" / f"mode_source_{index}.wav"
                audio.parent.mkdir(exist_ok=True)
                await run_logged_command([
                    "ffmpeg", "-y", "-protocol_whitelist", "file,pipe", "-i", str(upload.path),
                    "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
                ], record.task_dir, "正式任务补跑一次原始素材转写")
                async with create_asr_provider(settings) as provider:
                    if hasattr(provider, "max_retries"):
                        provider.max_retries = 0
                    provider.validate_configuration()
                    result = await provider.transcribe(audio)
                snapshot["asr_result"] = result.model_dump(mode="json")
                snapshot["transcript"] = _provider_segments(result, snapshot["id"], snapshot["sec"], snapshot.get("audio_offset_seconds", 0.0))
                snapshot["status"] = "ready"
                snapshot["task_asr_status"] = "completed"
                if snapshot["transcript"]:
                    snapshot["has_speech"] = True
            except asyncio.CancelledError:
                snapshot["task_asr_status"] = "cancelled"
                write_json_atomic(record.task_dir / "pretranscripts.json", snapshots)
                raise
            except Exception:
                # Never serialize a provider exception (it may include secrets).
                snapshot["task_asr_status"] = "failed"
                snapshot["status"] = "asr_failed"
                snapshot["asr_result"] = None
                snapshot["transcript"] = []
                write_text_log(record.task_dir, f"素材 {index + 1} 正式 ASR 补跑失败；不回退 TTS，不再次补跑。")
            write_json_atomic(record.task_dir / "pretranscripts.json", snapshots)
        result = ASRTranscript.model_validate(snapshot["asr_result"]) if snapshot.get("asr_result") is not None else None
        records.append(SourceASRRecord(
            source_index=index, source_name=upload.original_name,
            source_media_path=upload.path.relative_to(record.task_dir).as_posix(),
            status="available" if result is not None else "no_speech" if snapshot.get("has_speech") is False else "failed",
            transcript=result, cache_hit=snapshot.get("status") == "ready" and not snapshot.get("task_asr_attempted", False),
        ))
        progress(index + 1, len(snapshots), f"{'复用预转写' if not snapshot.get('task_asr_attempted') else '正式补跑已处理'} {index + 1}/{len(snapshots)}；未改配音")
    _write_models(record.task_dir, "asr_transcripts.json", records)
    return records


async def _source_clocks(record: Any, snapshots: Sequence[dict[str, Any]], normalized: Sequence[Path]) -> dict[str, Any]:
    clocks = {}
    for index, (upload, snapshot, norm) in enumerate(zip(record.uploads, snapshots, normalized, strict=True)):
        duration = float(snapshot["sec"])
        start, end = float(upload.trim_start or 0.0), float(upload.trim_end if upload.trim_end is not None else duration)
        if not 0 <= start < end <= duration:
            raise ValueError("素材 prepared trim 必须落在原始绝对时钟内。")
        if (start > 0 or end < duration) and not upload.prepared_stored_name:
            raise ValueError("声明了素材裁剪却没有 prepared 文件，不能猜测规格化视频偏移。")
        actual = await probe_audio_duration(norm, record.task_dir)
        if abs(actual - (end - start)) > 1 / _FPS + _SAMPLE:
            raise ValueError("规格化时长与 prepared 源时钟不一致；拒绝伪造 quote_source 映射。")
        raw = local_file(record.task_dir, upload.path.relative_to(record.task_dir).as_posix())
        raw_probe = await probe_media(raw, record.task_dir)
        fmt = raw_probe.get("format", {})
        origin = float(fmt.get("start_time", 0))
        audio = next((s for s in raw_probe.get("streams", []) if s.get("codec_type") == "audio"), None)
        measured_audio_offset = float(audio.get("start_time", origin)) - origin if audio else 0.0
        offset = float(snapshot.get("audio_offset_seconds", 0.0))
        if not math.isfinite(offset) or abs(offset - measured_audio_offset) > _SAMPLE:
            raise ValueError("预转写音轨偏移与原素材不一致；请重新核对来源时钟。")
        clocks[snapshot["id"]] = {
            "source_index": index, "source_name": upload.original_name,
            "raw_path": raw.relative_to(record.task_dir).as_posix(),
            "norm_path": norm.relative_to(record.task_dir).as_posix(),
            "source_duration": duration, "prepared_start": start, "prepared_end": end,
            "norm_source_offset": start, "norm_duration": actual, "audio_offset_seconds": offset,
            "has_audio": audio is not None,
            "raw_sha256": _sha(raw), "norm_sha256": _sha(norm),
            "clock_assertion": "norm_time = original_source_time - prepared_start",
        }
    return clocks


def _subtract(ranges: Sequence[tuple[float, float]], excluded: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    result = list(ranges)
    for lo, hi in sorted(excluded):
        updated = []
        for start, end in result:
            if end <= lo or start >= hi:
                updated.append((start, end))
            else:
                if start < lo:
                    updated.append((start, lo))
                if end > hi:
                    updated.append((hi, end))
        result = updated
    return result


def broll_source_intervals(snapshot: dict[str, Any], clock: dict[str, Any], mode: Mode) -> list[tuple[float, float]]:
    """Absolute intervals backed by no-speech OR measured silences, not ASR gaps."""
    start, end = clock["prepared_start"], clock["prepared_end"]
    if mode == "voiceover":
        return [(start, end)]
    segments = _segments(snapshot)
    excluded = [(segment.start, segment.end) for segment in segments]
    excluded.extend((float(start), float(end)) for start, end in snapshot.get("speech_intervals", []))
    if snapshot.get("has_speech") is False:
        # Below the clip-level speech threshold != every sample is non-speech.
        return _subtract([(start, end)], excluded)
    quiet = rules._silences(snapshot.get("silences"), snapshot["sec"])
    quiet = [(max(start, a), min(end, b)) for a, b in quiet if max(start, a) < min(end, b)]
    # Conflicting ASR evidence wins over a quiet detector. Do not use dialogue
    # merely because it has a low PCM amplitude or a missing ASR word.
    return _subtract(quiet, excluded)


def _pool_shots(shots: Sequence[Shot], snapshots: Sequence[dict[str, Any]], clocks: dict[str, Any], mode: Mode) -> list[Shot]:
    result = []
    for shot in shots:
        snapshot = snapshots[shot.source_index]
        clock = clocks[snapshot["id"]]
        offset = clock["norm_source_offset"]
        for start, end in broll_source_intervals(snapshot, clock, mode):
            lo, hi = max(shot.start, start - offset), min(shot.end, end - offset)
            if hi - lo >= 1.0:
                result.append(shot.model_copy(update={"shot_id": len(result), "start": lo, "end": hi, "duration": hi - lo}))
    return result


def _physical_shot_id(manifest: dict[str, Any], upload_id: str, start: float, end: float) -> int:
    registry = manifest.setdefault("quote_shot_registry", {})
    identity = _key([upload_id, float(start).hex(), float(end).hex()])
    if identity not in registry:
        registry[identity] = manifest["next_shot_id"]
        manifest["next_shot_id"] += 1
    return registry[identity]


def _quote_core(source: QuoteTake) -> tuple[float, float]:
    return (source.words[0].s, source.words[-1].e) if source.precision == "word" and source.words else (source.start, source.end)


def _continuous(left: MatchPlanItem, right: MatchPlanItem, snapshots: dict[str, dict[str, Any]]) -> bool:
    a, b = left.source, right.source
    if left.kind != "quote" or right.kind != "quote" or a is None or b is None:
        return False
    if not a.speaker_id.strip() or not b.speaker_id.strip() or a.upload_id != b.upload_id or a.speaker_id != b.speaker_id:
        return False
    _, end = _quote_core(a)
    start, _ = _quote_core(b)
    if not 0 <= rules._clock_delta(start, end) <= rules.LIMITS["jump_continuity_seconds"]:
        return False
    for segment in _segments(snapshots[a.upload_id]):
        if segment.words:
            if any(word.s < start and word.e > end for word in segment.words):
                return False
        elif segment.start < start and segment.end > end:
            if segment.id not in set(a.segment_ids) | set(b.segment_ids):
                return False
    return True


def quote_layout(plan: Sequence[MatchPlanItem], snapshots: Sequence[dict[str, Any]], clocks: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], list[list[int]]]:
    """Coalesce handles at safe source gaps; source QuoteTake remains preview-equal."""
    by_upload = {s["id"]: s for s in snapshots}
    layout: dict[int, dict[str, Any]] = {}
    groups: list[list[int]] = []
    previous: MatchPlanItem | None = None
    for item in plan:
        if item.kind != "quote":
            previous = None
            continue
        source = item.source
        if source is None or source.score < rules.MATCH_LOW:
            raise QuoteMissingError([{"idx": item.sentence_id, "score": source.score if source else 0}])
        clock = clocks[source.upload_id]
        if not clock["prepared_start"] <= source.start < source.end <= clock["prepared_end"]:
            raise ValueError(f"第 {item.sentence_id + 1} 句原声超出已选素材裁剪范围；源时钟没有被重写。")
        layout[item.sentence_id] = {"upload_id": source.upload_id, "start": source.start, "end": source.end}
        continuous = previous is not None and _continuous(previous, item, by_upload)
        if continuous and previous is not None and (previous.trim is not None or item.trim is not None):
            # An explicit editorial range is authoritative, not another handle
            # suggestion. A real gap must remain a cut, never a merged extract.
            continuous = layout[previous.sentence_id]["end"] == source.start
        if continuous and previous is not None:
            assert previous.source is not None
            if previous.trim is None and item.trim is None:
                boundary = (_quote_core(previous.source)[1] + _quote_core(source)[0]) / 2
                layout[previous.sentence_id]["end"] = boundary
                layout[item.sentence_id]["start"] = boundary
            groups[-1].append(item.sentence_id)
        else:
            groups.append([item.sentence_id])
        previous = item
    for group in groups:
        group_id = "quote-" + _key([layout[i] for i in group])[:24]
        for sentence_id in group:
            value = layout[sentence_id]
            clock = clocks[value["upload_id"]]
            offset = clock["audio_offset_seconds"]
            explicit = next(item for item in plan if item.sentence_id == sentence_id).trim is not None
            # Quantize explicit trims inward: never admit unconfirmed samples.
            start_sample = (math.ceil((value["start"] - offset) * NARRATION_SAMPLE_RATE - 1e-8) if explicit
                            else round((value["start"] - offset) * NARRATION_SAMPLE_RATE))
            end_sample = (math.floor((value["end"] - offset) * NARRATION_SAMPLE_RATE + 1e-8) if explicit
                          else round((value["end"] - offset) * NARRATION_SAMPLE_RATE))
            if start_sample < 0 or end_sample <= start_sample:
                raise TTSProcessingError("原声范围超出真实音轨，不能填充头部静音。")
            value.update(start=offset + start_sample / NARRATION_SAMPLE_RATE,
                         end=offset + end_sample / NARRATION_SAMPLE_RATE,
                         group_id=group_id, start_sample=start_sample, end_sample=end_sample)
    return layout, groups


async def _quote_shots(record: Any, plan: Sequence[MatchPlanItem], layout: dict[int, dict[str, Any]], manifest: dict[str, Any], shots: list[AnnotatedShot]) -> None:
    by_id = {shot.shot_id: shot for shot in shots}
    clocks = manifest["source_clocks"]
    source_map = {}
    for item in plan:
        if item.kind != "quote":
            continue
        source = item.source
        assert source is not None
        value, clock = layout[item.sentence_id], clocks[source.upload_id]
        start, end = value["start"] - clock["norm_source_offset"], value["end"] - clock["norm_source_offset"]
        if start < 0 or end > clock["norm_duration"] + _SAMPLE:
            raise ValueError("原声视频的相对 prepared 时钟超出规格化媒体。")
        shot_id = _physical_shot_id(manifest, source.upload_id, value["start"], value["end"])
        item.shot_id, item.sync_sound, item.beat_matches = shot_id, None, []
        item.candidates = []  # No fabricated visual/provider confidence.
        shot = AnnotatedShot(
            shot_id=shot_id, source_index=clock["source_index"], source_scene_index=shot_id,
            source_name=clock["source_name"], norm_path=clock["norm_path"], start=start, end=end,
            duration=end - start, status="available", description="真实原声片段（ASR 对齐，未作画面语义推断）",
            source_transcript=source.asr_text,
        )
        thumb = record.task_dir / "thumbs" / f"shot_{shot_id}.jpg"
        if not thumb.is_file():
            await extract_shot_thumbnail(record.task_dir, shot)
        shot.thumb_path = thumb.relative_to(record.task_dir).as_posix()
        by_id[shot_id] = shot
        source_map[str(item.sentence_id)] = {
            **value, "shot_id": shot_id, "take_id": source.take_id,
            "actual_range": {"start": value["start"], "end": value["end"], "clock": "original_source_seconds"},
            "cut_head_seconds": _quote_core(source)[0] - value["start"],
            "cut_tail_seconds": value["end"] - _quote_core(source)[1],
            "selected_source_start": source.start, "selected_source_end": source.end,
            "source_media_path": clock["raw_path"], "norm_path": clock["norm_path"],
            "norm_start": start, "norm_end": end, "norm_source_offset": clock["norm_source_offset"],
            "audio_offset_seconds": clock["audio_offset_seconds"],
        }
    shots[:] = list(by_id.values())
    manifest["quote_source"] = source_map


async def _exclude_quotes(record: Any, pool: Sequence[AnnotatedShot], plan: Sequence[MatchPlanItem], manifest: dict[str, Any]) -> list[AnnotatedShot]:
    if manifest["mode"] != "mixed":
        return list(pool)
    exclusions: dict[int, list[tuple[float, float]]] = {}
    for item in plan:
        if item.kind == "quote" and item.source:
            clock = manifest["source_clocks"][item.source.upload_id]
            exclusions.setdefault(clock["source_index"], []).append((
                item.source.start - clock["norm_source_offset"] - 2,
                item.source.end - clock["norm_source_offset"] + 2,
            ))
    filtered = []
    for shot in pool:
        for start, end in _subtract([(shot.start, shot.end)], exclusions.get(shot.source_index, [])):
            if end - start < 1.0:
                continue
            if start == shot.start and end == shot.end:
                filtered.append(shot)
                continue
            upload_id, clock = next((key, value) for key, value in manifest["source_clocks"].items()
                                    if value["source_index"] == shot.source_index)
            shot_id = _physical_shot_id(manifest, upload_id, start + clock["norm_source_offset"], end + clock["norm_source_offset"])
            sliced = shot.model_copy(update={"shot_id": shot_id, "start": start, "end": end, "duration": end - start})
            thumb = record.task_dir / "thumbs" / f"shot_{shot_id}.jpg"
            if not thumb.is_file():
                thumb = await extract_shot_thumbnail(record.task_dir, sliced)
            sliced.thumb_path = thumb.relative_to(record.task_dir).as_posix()
            filtered.append(sliced)
    return filtered


async def _match_narration(record: Any, sentences: list[Sentence], pool: list[AnnotatedShot], settings: Settings, progress: Callable[[float, str], None]) -> list[MatchPlanItem]:
    if not sentences:
        return []  # Crucially before constructing ANY embedding/LLM/TTS provider.
    if any(s.kind != "narration" or len(s.visual_beats) != 1 for s in sentences):
        raise ValueError("语义检索只能接收一条旁白一个视觉节拍。")
    available = [s for s in pool if s.status == "available" and s.description and s.quality is not None]
    if len(available) < len(sentences):
        raise MatchingError("可用非原声画面不足以逐句唯一分配；请增加空镜或减少旁白，不会冻结补帧。")
    async with AsyncExitStack() as stack:
        embedding = await stack.enter_async_context(EmbeddingProvider.from_settings(settings, task_dir=record.task_dir))
        llm = await stack.enter_async_context(LLMProvider.from_settings(settings))
        verifier = await stack.enter_async_context(VisionProvider.from_settings(settings)) if settings.entity_verification_enabled else None
        plan = await build_match_plan(
            record.task_dir, sentences, available, embedding, llm, progress,
            top_k=settings.retrieval_top_k, lexical_rescue_k=settings.retrieval_lexical_rescue_k,
            video_embedding_enabled=settings.video_embedding_enabled,
            video_embedding_candidate_top_k=settings.video_embedding_candidate_top_k,
            video_embedding_concurrency=settings.video_embedding_concurrency,
            entity_verifier=verifier, entity_verification_max_shots=settings.entity_verification_max_shots,
            entity_verification_min_confidence=settings.entity_verification_min_confidence,
            editing_brief=_preferences(record).custom_instructions,
        )
    expected = {sentence.sentence_id: sentence.text for sentence in sentences}
    if len(plan) != len(expected) or {p.sentence_id for p in plan} != set(expected):
        raise MatchingError("匹配结果与已确认旁白身份不一致。")
    legal = {shot.shot_id for shot in available}
    for item in plan:
        if item.shot_id not in legal or item.sync_sound is not None:
            raise MatchingError("三模式旁白匹配越过素材池或错误使用自动同期声。")
        item.text, item.kind = expected[item.sentence_id], "narration"
    return plan


async def _generated_narration(
    record: Any, sentences: list[Sentence], plan: list[MatchPlanItem],
    shots: list[AnnotatedShot], manifest: dict[str, Any], settings: Settings,
) -> list[MatchPlanItem]:
    """Optional illustrations are narration-only and retain fallback disclosure."""
    if not plan or not _preferences(record).generative_fill or manifest["mode"] == "original":
        return plan
    if any(item.kind != "narration" for item in plan) or any(sentence.kind != "narration" for sentence in sentences):
        raise ValueError("生成式补图只允许处理旁白，不能替代原声来源。")
    from .providers.generative import GenerativeFillProvider, apply_generative_fill, generative_fill_configured

    if not generative_fill_configured(settings):
        manifest["generative_fill"] = {"requested": True, "applied": False, "reason": "not_configured"}
        return plan
    old = read_json(record.task_dir, "generated_media_disclosure.json") if (record.task_dir / "generated_media_disclosure.json").is_file() else {"items": []}
    async with GenerativeFillProvider.from_settings(settings) as provider:
        synthetic, plan, count = await apply_generative_fill(
            record.task_dir, sentences, shots, plan, provider,
            max_clips=settings.generative_fill_max_clips, mode=settings.generative_fill_mode,
            clip_duration=settings.generative_fill_duration_seconds,
        )
    shots.extend(synthetic)
    manifest["next_shot_id"] = max(manifest["next_shot_id"], max((s.shot_id for s in shots), default=-1) + 1)
    manifest["generative_fill"] = {"requested": True, "applied": bool(count), "generated_count_this_render": count}
    hashes = manifest.setdefault("generated_source_hashes", {})
    for shot in synthetic:
        hashes[shot.norm_path] = _sha(local_file(record.task_dir, shot.norm_path))
    if count:
        current = read_json(record.task_dir, "generated_media_disclosure.json")
        entries = {item["shot_id"]: item for item in [*old["items"], *current["items"]]}
        current["items"] = list(entries.values())
        write_json_atomic(record.task_dir / "generated_media_disclosure.json", current)
    return plan


def _rows(plan: Sequence[MatchPlanItem]) -> list[dict[str, Any]]:
    return [{"idx": item.sentence_id, "kind": item.kind, "text": item.text,
             "source": item.source.model_dump(mode="json") if item.source else None,
             "alt_takes": [take.model_dump(mode="json") for take in item.alt_takes],
             "trim": item.trim, "to_narration": item.to_narration,
             "jumpcut_before": item.jumpcut_before} for item in plan]


def mode_report_metadata(task_dir: Path) -> dict[str, Any]:
    """Expose actual PCM ranges separately from immutable preview selections.

    Waveform coordinates use actual_range.start + local audio seconds, NOT
    interpolation across the selected source handles. No private paths escape.
    """
    metadata = _base_mode_report_metadata(task_dir)
    if metadata:
        manifest = read_json(task_dir, MODE_MANIFEST)
        metadata["metrics"]["quote_actual_ranges"] = {
            key: {name: value[name] for name in ("actual_range", "cut_head_seconds", "cut_tail_seconds") if name in value}
            for key, value in manifest.get("quote_source", {}).items()
        }
    return metadata


async def _align_quotes_offloop(inputs: list[SentenceInput], snapshots: list[dict[str, Any]], speakers: list[Speaker]) -> list[dict[str, Any]]:
    from .pipeline import _run_blocking_until_complete

    return await _run_blocking_until_complete(partial(rules.align_quotes, inputs, snapshots, speakers))


def _split_pcm(source: Path, destination: Path, start_sample: int, end_sample: int) -> None:
    """Copy actual PCM frames, not a concat of padded per-quote extracts."""
    with wave.open(str(source), "rb") as origin:
        if not 0 <= start_sample < end_sample <= origin.getnframes():
            raise TTSProcessingError("原声 PCM 切片超出实测样本范围。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as output:
            output.setparams(origin.getparams())
            origin.setpos(start_sample)
            remaining = end_sample - start_sample
            while remaining:
                frames = min(remaining, 65_536)
                block = origin.readframes(frames)
                if len(block) != frames * origin.getnchannels() * origin.getsampwidth():
                    raise TTSProcessingError("原声 PCM 数据提前结束，拒绝补齐。")
                output.writeframesraw(block)
                remaining -= frames


def _cached_audio(root: Path, previous: dict[str, Any] | None, key: str) -> SentenceTiming | None:
    if not previous or previous.get("key") != key:
        return None
    timing = SentenceTiming.model_validate(previous["timing"])
    path = local_file(root, timing.audio_path)
    if _sha(path) != previous.get("sha256"):
        raise TTSProcessingError("既有逐句音频与缓存绑定不一致；不能静默替换或重新合成。")
    return timing


def _audio_entry(root: Path, key: str, timing: SentenceTiming, origin: str) -> dict[str, Any]:
    return {"key": key, "timing": timing.model_dump(mode="json"),
            "sha256": _sha(local_file(root, timing.audio_path)), "origin": origin}


async def _prepare_quote_audio(
    record: Any, plan: Sequence[MatchPlanItem], layout: dict[int, dict[str, Any]],
    groups: list[list[int]], manifest: dict[str, Any], progress: Callable[[float, str], None],
) -> tuple[dict[int, SentenceTiming], dict[str, Any]]:
    root = record.task_dir
    by_id = {item.sentence_id: item for item in plan}
    old = manifest.get("audio_cache", {})
    preferences = _preferences(record)
    selected: dict[int, SentenceTiming] = {}
    entries: dict[str, Any] = {}
    receipts: list[dict[str, Any]] = []
    for group in groups:
        desired = {}
        group_binding = [layout[i] for i in group]
        for sentence_id in group:
            source = by_id[sentence_id].source
            assert source is not None
            clock, value = manifest["source_clocks"][source.upload_id], layout[sentence_id]
            desired[sentence_id] = _key([MODE_RECIPE, "quote-continuous-dsp-v2", group_binding, clock["raw_sha256"],
                                         value["start_sample"], value["end_sample"],
                                         source.asr_text, [w.model_dump() for w in source.words],
                                         source.precision, preferences.enhance_speech])
            cached = _cached_audio(root, old.get(str(sentence_id)), desired[sentence_id])
            if cached:
                selected[sentence_id] = cached.model_copy(update={"tts_group_id": value["group_id"]})
                entries[str(sentence_id)] = _audio_entry(root, desired[sentence_id], selected[sentence_id], "quote")
        changed = [sentence_id for sentence_id in group if sentence_id not in selected]
        if changed:
            first, last = layout[group[0]], layout[group[-1]]
            clock = manifest["source_clocks"][first["upload_id"]]
            with tempfile.TemporaryDirectory(prefix=".mode-quotes-", dir=root) as directory:
                group_path = Path(directory) / "source.wav"
                receipt = await extract_mode_pcm(
                    root, local_file(root, clock["raw_path"]), group_path,
                    source_start=first["start"], source_end=last["end"],
                    audio_offset_seconds=clock["audio_offset_seconds"],
                )
                receipts.append({**receipt, "sentence_ids": group, "normalized_sentence_ids": changed,
                                 "upload_id": first["upload_id"], "one_continuous_source_extract": True,
                                 "dsp_scope": "continuous_source_group", "unit_metrics": "measured_after_exact_split"})
                duration = _verified_pcm_samples(group_path) / NARRATION_SAMPLE_RATE
                group_timing = SentenceTiming(
                    sentence_id=group[0], text="".join(by_id[i].source.asr_text for i in group),
                    audio_path=group_path.relative_to(root).as_posix(), duration=duration,
                    start=0, end=duration, audio_kind="sync", gap_after=0,
                )
                processed = Path(directory) / "processed.wav"
                # Both normalization passes see this SAME post-denoise group.
                # Never reset gain/high-pass/FFT state at editorial sentence seams.
                await normalize_mode_unit(root, group_timing, processed, enhance_speech=preferences.enhance_speech)
                for sentence_id in changed:
                    source, value = by_id[sentence_id].source, layout[sentence_id]
                    assert source is not None
                    raw = root / "tts" / f"mode-{desired[sentence_id]}.wav"
                    _split_pcm(processed, raw, value["start_sample"] - first["start_sample"], value["end_sample"] - first["start_sample"])
                    duration = _verified_pcm_samples(raw) / NARRATION_SAMPLE_RATE
                    words = [TTSWordTiming(text=word.w, start=word.s - value["start"], end=word.e - value["start"])
                             for word in source.words] if source.precision == "word" else []
                    timing = SentenceTiming(
                        sentence_id=sentence_id, text=source.asr_text,
                        audio_path=raw.relative_to(root).as_posix(), duration=duration, start=0.0, end=duration,
                        audio_kind="sync", tts_group_id=value["group_id"], words=words, gap_after=0,
                    )
                    # Measure each actual unit, not inherited group statistics.
                    # QC reports local level outliers; correcting them separately
                    # would reintroduce the gain discontinuity we just removed.
                    selected[sentence_id] = await measure_mode_unit(root, timing)
                    entries[str(sentence_id)] = _audio_entry(root, desired[sentence_id], selected[sentence_id], "quote")
        progress(0.45 * len(selected) / max(len(layout), 1), f"原声已按真实源时间处理 {len(selected)}/{len(layout)}；配音请求 0")
    manifest["quote_audio_extractions"] = receipts
    return selected, entries


async def _recorded_narration(
    root: Path, source: Path, sentences: list[Sentence], settings: Settings,
) -> tuple[list[SentenceTiming], dict[str, Any]]:
    duration = await probe_audio_duration(source, root)
    async with create_asr_provider(settings) as provider:
        if hasattr(provider, "max_retries"):
            provider.max_retries = 0
        provider.validate_configuration()
        transcript = await provider.transcribe(source)
    segments = [TranscriptSegment.model_validate(s) for s in _provider_segments(transcript, "own_voice", duration, 0.0)]
    if any(not segment.words for segment in segments):
        from .providers.local_speech import LocalForcedAligner, local_speech_readiness
        from .pipeline import _run_blocking_until_complete
        configured = getattr(settings, "local_alignment_model_path", None)
        path = Path(configured) if configured else None
        reviewed = bool(getattr(settings, "local_speech_license_reviewed", False))
        ready = local_speech_readiness("forced_alignment", path, license_reviewed=reviewed)
        if ready.available and path is not None:
            with tempfile.TemporaryDirectory(prefix=".recording-alignment-", dir=root) as directory:
                pcm = Path(directory) / "source.wav"
                await run_logged_command(["ffmpeg", "-y", "-i", str(source), "-map", "0:a:0", "-vn",
                                          "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(pcm)], root, "本地声学对齐 PCM")
                def align_missing() -> None:
                    provider = LocalForcedAligner(path, license_reviewed=reviewed)
                    for segment in segments:
                        if not segment.words:
                            aligned = provider.align(pcm, segment.text, segment.start, segment.end)
                            segment.words = [Word(w=w["w"], s=w["s"], e=w["e"]) for w in aligned]
                await _run_blocking_until_complete(align_missing)
        elif getattr(settings, "local_speech_required", False):
            from .providers.local_speech import SpeechUnavailable
            raise SpeechUnavailable("forced_alignment blocked: " + ", ".join(ready.blocked_prerequisites))
    # New-mode-only alignment: retain the legacy segment API unchanged. Use
    # literal production word maps, then numeric-aware normalized LOCAL text
    # ranges. A word is atomic; a manuscript boundary inside one is rejected.
    atoms: list[tuple[str, float, float, list[Word]]] = []
    for segment in segments:
        spans = rules._word_spans(segment.text, segment.words, segment.start, segment.end)
        if spans is None:
            atoms.append((segment.text, segment.start, segment.end, []))
        else:
            for index, word in enumerate(segment.words):
                lo, hi = rules._word_text_range(segment.text, spans, index, index)
                atoms.append((segment.text[lo:hi], word.s, word.e, [word]))
    expected = [rules.normalize_text(sentence.text) for sentence in sentences]
    if (not atoms or not all(expected)
            or rules.normalize_text("".join(a[0] for a in atoms)) != "".join(expected)
            or rules.normalize_text(transcript.text) != "".join(expected)
            or any(a[2] > b[1] for a, b in zip(atoms, atoms[1:]))):
        raise TTSProcessingError("自己的配音没有完整对齐已确认稿句，不能伪造字幕或词时间。")
    aligned = []
    cursor = 0
    for target in expected:
        text = ""
        first = cursor
        while cursor < len(atoms):
            text += atoms[cursor][0]
            cursor += 1
            if rules.normalize_text(text) == target:
                break
        else:
            raise TTSProcessingError("录音分句落在同一个 ASR 词/段内部，不能均分时间。")
        aligned.append((atoms[first][1], atoms[cursor - 1][2], text,
                        [w for atom in atoms[first:cursor] for w in atom[3]]
                        if all(atom[3] for atom in atoms[first:cursor]) else []))
    if cursor != len(atoms):
        raise TTSProcessingError("录音含未对齐的额外内容。")
    prepared = []
    recipes = {}
    source_hash = _sha(source)
    for sentence, (start, end, text, observed_words) in zip(sentences, aligned, strict=True):
        key = _key(["recorded-narration", _sha(source), start, end, sentence.text])
        raw = root / "tts" / f"recorded-source-{key}.wav"
        receipt = await extract_mode_pcm(root, source, raw, source_start=start, source_end=end)
        recipes[str(sentence.sentence_id)] = {
            "source_audio": source.relative_to(root).as_posix(), "source_sha256": source_hash,
            "source_start": start, "source_end": end,
        }
        actual_start = float(receipt["actual_source_start"])
        actual_duration = int(receipt["samples"]) / NARRATION_SAMPLE_RATE
        words = [TTSWordTiming(text=w.w, start=w.s - actual_start, end=w.e - actual_start)
                 for w in observed_words]
        prepared.append(SentenceTiming(
            sentence_id=sentence.sentence_id, text=text, audio_path=raw.relative_to(root).as_posix(),
            duration=actual_duration, start=0, end=actual_duration, audio_kind="sync", words=words,
            voice_profile_id="recording-" + _sha(source)[:24], gap_after=0,
        ))
    return prepared, {"source_sha256": source_hash, "source_audio": source.relative_to(root).as_posix(),
                      "transcript": transcript.model_dump(mode="json"), "timestamp_granularity": "asr_word_or_segment",
                      "transcript_verified": True, "audio_source": "student_recording", "raw_recipes": recipes}


async def _assemble_audio(
    record: Any, plan: list[MatchPlanItem], snapshots: list[dict[str, Any]], manifest: dict[str, Any],
    settings: Settings, progress: Callable[[float, str], None], *,
    changed_text: set[int] | None = None, recordings: dict[int, str] | None = None,
) -> list[SentenceTiming]:
    root, preferences = record.task_dir, _preferences(record)
    changed_text, recordings = changed_text or set(), recordings or {}
    layout, groups = quote_layout(plan, snapshots, manifest["source_clocks"])
    selected, entries = await _prepare_quote_audio(record, plan, layout, groups, manifest, progress)
    narration = _sentences([SentenceInput(idx=p.sentence_id, text=p.text, kind="narration") for p in plan if p.kind == "narration"])
    previous = manifest.get("audio_cache", {})
    prepared_recordings: dict[int, SentenceTiming] = {}
    recording_keys: dict[int, str] = {}
    recording_recipes: dict[int, dict[str, Any]] = {}
    if not previous and (root / "own_voice.wav").is_file() and narration:
        prepared, receipt = await _recorded_narration(root, root / "own_voice.wav", narration, settings)
        prepared_recordings = {timing.sentence_id: timing for timing in prepared}
        for timing in prepared:
            recording_keys[timing.sentence_id] = _key([receipt["source_sha256"], timing.audio_path])
            recording_recipes[timing.sentence_id] = receipt["raw_recipes"][str(timing.sentence_id)]
        write_json_atomic(root / "student_narration.json", {**receipt, "units": [t.model_dump(mode="json") for t in prepared]})
    elif not previous and narration and preferences.voice == "mine":
        raise TTSProcessingError("选择了自己的配音但没有整篇录音，不能静默改用 AI。")
    for sentence in narration:
        recording_id = recordings.get(sentence.sentence_id)
        if recording_id:
            if not re.fullmatch(r"[0-9a-f]{32}", recording_id):
                raise ValueError("录音标识无效。")
            metadata = read_json(root, f"recordings/{recording_id}.json")
            if metadata.get("sentence_id") != sentence.sentence_id:
                raise ValueError("录音属于另一个句子。")
            prepared, receipt = await _recorded_narration(root, local_file(root, f"recordings/{recording_id}.wav"), [sentence], settings)
            prepared_recordings[sentence.sentence_id] = prepared[0]
            recording_keys[sentence.sentence_id] = _key([recording_id, receipt["source_sha256"], sentence.text])
            recording_recipes[sentence.sentence_id] = receipt["raw_recipes"][str(sentence.sentence_id)]
    wanted_tts: list[Sentence] = []
    desired: dict[int, str] = {}
    voice_signature = [settings.tts_provider, settings.tts_voice, settings.tts_model,
                       settings.volcengine_voice_type, settings.volcengine_tts_resource_id,
                       settings.volcengine_tts_model, settings.tts_provider_speech_rate,
                       settings.tts_provider_loudness_rate]
    for sentence in narration:
        sentence_id = sentence.sentence_id
        prior = previous.get(str(sentence_id))
        if sentence_id in prepared_recordings:
            desired[sentence_id] = _key([MODE_RECIPE, "recording", recording_keys[sentence_id], preferences.enhance_speech])
        elif prior and prior.get("origin") in {"recording", "own_voice"} and sentence_id not in changed_text:
            # Pace changes never retime a recording. When enhancement changes,
            # recover the original recording, not the previously enhanced WAV.
            if manifest.get("preferences", {}).get("enhance_speech") != preferences.enhance_speech:
                raw_timing = SentenceTiming.model_validate(prior["raw_timing"])
                raw_path = local_file(root, raw_timing.audio_path, exists=False)
                if not raw_path.is_file():
                    recipe = prior["raw_recipe"]
                    raw_source = local_file(root, recipe["source_audio"])
                    if _sha(raw_source) != recipe["source_sha256"]:
                        raise TTSProcessingError("原始录音来源绑定不一致。")
                    await extract_mode_pcm(root, raw_source, raw_path,
                                           source_start=recipe["source_start"], source_end=recipe["source_end"])
                prepared_recordings[sentence_id] = raw_timing
                desired[sentence_id] = _key([MODE_RECIPE, "recording", prior["recording_key"], preferences.enhance_speech])
                recording_keys[sentence_id] = prior["recording_key"]
                recording_recipes[sentence_id] = prior["raw_recipe"]
            else:
                desired[sentence_id] = prior["key"]
        else:
            desired[sentence_id] = _key([MODE_RECIPE, "tts", sentence.text, preferences.target_chars_per_minute,
                                         settings.tts_news_rate_tolerance, voice_signature])
        cached = _cached_audio(root, prior, desired[sentence_id])
        if cached:
            selected[sentence_id], entries[str(sentence_id)] = cached, dict(prior)
        elif sentence_id not in prepared_recordings:
            wanted_tts.append(sentence)
    pronunciation_by_id = {item["sentence_id"]: item for item in manifest.get("pronunciation_plan", [])}
    synthesized_count = 0
    if wanted_tts:
        # Isolation is essential: legacy synthesis writes global manifests and
        # may retime outputs in place. Never point it at an unchanged sentence.
        with tempfile.TemporaryDirectory(prefix=".mode-tts-", dir=root) as directory:
            synthesis = Path(directory)
            pronunciations = []
            if any(extract_number_expressions(sentence.text) for sentence in wanted_tts):
                async with LLMProvider.from_settings(settings) as provider:
                    pronunciations = await provider.plan_pronunciations(record.script, wanted_tts)
            async with create_tts_provider(settings, target_chars_per_minute=preferences.target_chars_per_minute) as provider:
                timings = await synthesize_narration(
                    synthesis, wanted_tts, provider, lambda done, total, msg: progress(0.45 + 0.35 * done / total, msg),
                    full_script=record.script, pronunciation_plan=pronunciations,
                    target_chars_per_minute=preferences.target_chars_per_minute,
                    rate_tolerance=settings.tts_news_rate_tolerance,
                    target_lufs=-20, target_lra=5, true_peak_dbfs=-3.5,
                )
            tts_manifest = read_json(synthesis, "tts_manifest.json")
            synthesized_count = tts_manifest["provider_request_count"]
            manifest["tts_provider_profile"] = tts_manifest["provider_profile"]
            for value in read_json(synthesis, "pronunciation_plan.json"):
                pronunciation_by_id[value["sentence_id"]] = value
            for timing in timings:
                timing.audio_path = (synthesis / timing.audio_path).relative_to(root).as_posix()
                output = root / "tts" / f"mode-{desired[timing.sentence_id]}.wav"
                normalized = await normalize_mode_unit(root, timing, output)
                selected[timing.sentence_id] = normalized
                entries[str(timing.sentence_id)] = _audio_entry(root, desired[timing.sentence_id], normalized, "tts")
    for sentence_id, timing in prepared_recordings.items():
        if sentence_id in selected:
            continue
        normalized = await normalize_mode_unit(root, timing, root / "tts" / f"mode-{desired[sentence_id]}.wav", enhance_speech=preferences.enhance_speech)
        selected[sentence_id] = normalized
        entries[str(sentence_id)] = {
            **_audio_entry(root, desired[sentence_id], normalized, "recording" if sentence_id in recordings else "own_voice"),
            "raw_timing": timing.model_dump(mode="json"), "recording_key": recording_keys[sentence_id],
            "raw_recipe": recording_recipes[sentence_id],
        }
    if set(selected) != {item.sentence_id for item in plan}:
        raise TTSProcessingError("真实音频结果缺少已确认句子。")
    timings = []
    cursor_samples = 0
    for index, item in enumerate(plan):
        timing = selected[item.sentence_id].model_copy(deep=True)
        following = plan[index + 1] if index + 1 < len(plan) else None
        if following is None:
            gap = 0.0
        elif item.kind == following.kind == "quote":
            gap = 0.0 if layout[item.sentence_id]["group_id"] == layout[following.sentence_id]["group_id"] else rules.LIMITS["quote_gap_seconds"]
        elif _v2(manifest):
            punctuation = manifest.get("terminal_punctuation", {}).get(str(item.sentence_id))
            gap = 0.15 if item.kind == "quote" else mode_narration_gap(item.text, terminal_punctuation=punctuation)
        elif item.kind != following.kind:
            gap = 0.25
        else:
            gap = 0.12
        samples = _verified_pcm_samples(local_file(root, timing.audio_path))
        timing.start, timing.end = cursor_samples / NARRATION_SAMPLE_RATE, (cursor_samples + samples) / NARRATION_SAMPLE_RATE
        timing.duration, timing.gap_after = samples / NARRATION_SAMPLE_RATE, gap
        timings.append(timing)
        cursor_samples += samples + round(gap * NARRATION_SAMPLE_RATE)
        entries[str(item.sentence_id)]["timing"] = timing.model_dump(mode="json")
    mix_key = _key([[entries[str(t.sentence_id)]["sha256"], t.gap_after] for t in timings])
    previous_mix = manifest.get("audio_mix", {})
    if mix_key == previous_mix.get("key") and (root / "narration.m4a").is_file():
        if _sha(root / "narration.m4a") != previous_mix.get("sha256"):
            raise TTSProcessingError("完整音轨缓存绑定不一致。")
    else:
        await concatenate_narration(root, timings, target_lufs=-20, target_lra=5, true_peak_dbfs=-3.5, pre_normalized=True)
        actual = await probe_audio_duration(root / "narration.m4a", root)
        if abs(actual - timings[-1].end) > 0.03:
            raise TTSProcessingError("完整音轨与真实 PCM 时长不一致；不会裁切原声以强行同步。")
    manifest["audio_mix"] = {"key": mix_key, "sha256": _sha(root / "narration.m4a")}
    manifest["audio_cache"] = entries
    manifest["pronunciation_plan"] = [pronunciation_by_id[s.sentence_id] for s in narration if s.sentence_id in pronunciation_by_id]
    _write_models(root, "timings.json", timings)
    write_json_atomic(root / "pronunciation_plan.json", manifest["pronunciation_plan"])
    write_json_atomic(root / "narration_profile.json", {
        "schema_version": 1, "mode": manifest["mode"], "target_lufs": -20.0, "maximum_true_peak_dbfs": -3.0,
        "target_chars_per_minute": preferences.target_chars_per_minute,
        "rate_tolerance": settings.tts_news_rate_tolerance,
        "provider_profile": manifest.get("tts_provider_profile", {}),
        "units": [t.model_dump(mode="json", exclude={"words", "text"}) for t in timings],
        "speech_enhancement": {"requested": preferences.enhance_speech, "tts_filtered": False,
                               "applied": preferences.enhance_speech and any(t.audio_kind == "sync" for t in timings)},
    })
    write_json_atomic(root / "tts_manifest.json", {
        "schema_version": 1, "mode": manifest["mode"], "provider_request_count_this_render": synthesized_count,
        "tts_unit_count": sum(t.audio_kind == "tts" for t in timings),
        "quote_unit_count": len(layout), "quotes_synthesized": 0,
    })
    progress(1.0, f"真实音轨完成：原声 {len(layout)} 句，本次配音请求 {synthesized_count} 次")
    return timings


def _quote_rows_for_cuts(plan: Sequence[MatchPlanItem], layout: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = _rows(plan)
    for row in rows:
        if row["kind"] == "quote":
            value = layout[row["idx"]]
            row["source"] = {**row["source"], "start": value["start"], "end": value["end"]}
    return rows


def build_mode_edl(
    plan: list[MatchPlanItem], timings: Sequence[SentenceTiming], shots: list[AnnotatedShot],
    snapshots: list[dict[str, Any]], manifest: dict[str, Any], preferences: EditingPreferences,
) -> tuple[list[EDLItem], dict[int, SegmentRenderOptions]]:
    """Video follows the audio clock, source quotes never use generated/frozen edges.

    The 150/250ms editorial silence is covered by existing source video after a
    quote, not a freeze. If the selected upload ends there, use an unused genuine
    B-roll interval. If neither exists, report a shortage instead of fabricating.
    """
    shots_by_id = {shot.shot_id: shot for shot in shots}
    layout, _groups = quote_layout(plan, snapshots, manifest["source_clocks"])
    rows = _quote_rows_for_cuts(plan, layout)
    initial_cuts = rules.calculate_jumpcuts(rows, preferences.jump_cut_cover)
    positions = {item.sentence_id: index for index, item in enumerate(plan)}
    broll_ids = set(manifest["broll_shot_ids"])
    pool = [shot for shot in shots if shot.shot_id in broll_ids and shot.status == "available" and shot.quality is not None and shot.media_origin == "source"]
    used = {item.shot_id for item in plan}
    covers: dict[int, AnnotatedShot] = {}
    for cut in initial_cuts:
        index = positions[cut.after_row] + 1
        timing = timings[index]
        cover_seconds = min(2.0, timing.duration * 0.4) if _v2(manifest) else 1.5
        if preferences.jump_cut_cover == "broll" and cover_seconds >= 1 / _FPS and timing.duration >= cover_seconds + 1 / _FPS:
            candidates = [shot for shot in pool if shot.shot_id not in used and shot.duration >= cover_seconds]
            if candidates:
                shot = max(candidates, key=lambda value: (value.duration, -value.shot_id))
                covers[plan[index].sentence_id] = shot
                used.add(shot.shot_id)
                rows[index]["cover_shot"] = shot.shot_id
    cuts = rules.calculate_jumpcuts(rows, preferences.jump_cut_cover)
    cuts_by_id = {plan[positions[cut.after_row] + 1].sentence_id: cut for cut in cuts}
    options: dict[int, SegmentRenderOptions] = {}
    edl = []
    rendering_receipts: list[dict[str, Any]] = []
    for index, (item, timing) in enumerate(zip(plan, timings, strict=True)):
        shot = shots_by_id[item.shot_id]
        item.jumpcut_before = item.sentence_id in cuts_by_id
        options[shot.shot_id] = SegmentRenderOptions(
            color_consistency=preferences.color_consistency,
            motion=preferences.motion_effects and item.kind == "narration",
        )
        need = timing.duration + timing.gap_after
        clips: list[EDLClip] = []
        if item.kind == "narration":
            preferred = next((beat.preferred_in_time for beat in item.beat_matches if beat.shot_id == shot.shot_id), None)
            if need > shot.duration + _SAMPLE:
                raise MatchingError(f"第 {item.sentence_id + 1} 句旁白 {need:.3f} 秒长于所选真实镜头 {shot.duration:.3f} 秒；不会冻结或重复镜头。")
            start = max(shot.start, min(preferred if preferred is not None else shot.start, shot.end - need))
            clips.append(EDLClip(shot_id=shot.shot_id, src=shot.norm_path, in_time=start, out_time=start + need, media_origin=shot.media_origin))
        else:
            assert item.source is not None
            value = layout[item.sentence_id]
            clock = manifest["source_clocks"][item.source.upload_id]
            start = value["start"] - clock["norm_source_offset"]
            end = value["end"] - clock["norm_source_offset"]
            if abs(end - start - timing.duration) > 2 * _SAMPLE:
                raise RenderingError("原声视频源区间与真实 PCM 时长不一致。")
            available_end = min(clock["norm_duration"], clock["prepared_end"] - clock["norm_source_offset"])
            extension = min(timing.gap_after, max(0, available_end - end))
            cover = covers.get(item.sentence_id)
            if cover:
                duration = min(2.0, timing.duration * 0.4, cover.duration) if _v2(manifest) else min(3.0, cover.duration, timing.duration - 1 / _FPS)
                # Snap only this *visual editorial cut* to frames, never the audio.
                duration = math.floor(duration * _FPS) / _FPS
                clips.append(EDLClip(shot_id=cover.shot_id, src=cover.norm_path,
                                     in_time=cover.start, out_time=cover.start + duration))
                options[cover.shot_id] = SegmentRenderOptions(color_consistency=preferences.color_consistency)
                start += duration
                rendering_receipts.append({"after_row": cuts_by_id[item.sentence_id].after_row,
                                           "following_row": item.sentence_id, "cover": "broll", "shot": cover.shot_id,
                                           "timeline_start": timing.start, "duration": duration, "audio_changed": False})
            elif item.jumpcut_before and cuts_by_id[item.sentence_id].cover == "zoom":
                cut = cuts_by_id[item.sentence_id]
                if timing.duration >= 6 / _FPS:
                    options[shot.shot_id] = SegmentRenderOptions(color_consistency=preferences.color_consistency, jump_zoom_frames=6,
                                                               jump_zoom_ratio=0.06 if _v2(manifest) else 0.08)
                    rendering_receipts.append({"after_row": cut.after_row, "following_row": item.sentence_id,
                                               "cover": "zoom", "ratio": 1.06 if _v2(manifest) else 1.08, "transition_frames": 6,
                                               "transition": "push_in_not_dissolve", "audio_changed": False})
                else:
                    cut.cover, cut.downgraded = "hard", True
            clips.append(EDLClip(shot_id=shot.shot_id, src=shot.norm_path,
                                 in_time=start, out_time=end + extension, evidence_aligned=True))
            remaining = timing.gap_after - extension
            if remaining > _SAMPLE:
                # A different physical B-roll range for a gap must not consume a
                # previously used shot, including a jump-cover already allocated.
                gap_shot = next((s for s in pool if s.shot_id not in used and s.duration >= remaining + 1 / _FPS), None)
                if gap_shot is None:
                    raise MatchingError(f"第 {item.sentence_id + 1} 句到源文件末尾，缺少覆盖 {remaining:.3f} 秒句间隔的真实画面；请补空镜或调整原声范围。")
                used.add(gap_shot.shot_id)
                clips.append(EDLClip(shot_id=gap_shot.shot_id, src=gap_shot.norm_path,
                                     in_time=gap_shot.start, out_time=gap_shot.start + remaining))
                options[gap_shot.shot_id] = SegmentRenderOptions(color_consistency=preferences.color_consistency)
        edl.append(EDLItem(sentence_id=item.sentence_id, clips=clips, timeline_start=timing.start, timeline_end=timing.end + timing.gap_after))
    manifest["jumpcuts"] = [cut.model_dump(mode="json") for cut in cuts]
    manifest["jumpcut_rendering"] = rendering_receipts
    manifest["rows"] = _rows(plan)
    manifest["broll_available"] = bool(pool)
    return edl, options


async def _finish_options(record: Any, timings: Sequence[SentenceTiming], plan: list[MatchPlanItem], manifest: dict[str, Any], settings: Settings) -> FinishOptions:
    from .music import load_music_library, select_music_track, write_music_selection

    preferences = _preferences(record)
    graphics, lower_thirds = generate_mode_graphics(
        record.task_dir, timings, plan, [Speaker.model_validate(value) for value in manifest["speakers"]],
        preferences, title=_title(record.script) or "",
        disclosure_intervals=[tuple(pair) for pair in manifest.get("generated_intervals", [])],
        v2=_v2(manifest),
    )
    manifest["lower_thirds"] = lower_thirds
    write_json_atomic(record.task_dir / "lower_thirds.json", lower_thirds)
    music = None
    if preferences.background_music:
        # Deterministic local selection: all-original/no-B-roll never constructs
        # a language model simply to name a music mood.
        mood = preferences.resolved_music_mood
        track = select_music_track(load_music_library(settings.music_library_dir), mood)
        write_music_selection(record.task_dir, mood=mood, track=track, resolved_by="tone" if preferences.music_mood == "auto" else "explicit")
        if track:
            music = MusicMixOptions(track=track, mood=mood, bed_lufs=settings.music_bed_target_lufs, duck_ratio=settings.music_duck_ratio,
                                    speech_intervals=tuple((t.start, t.end) for t in timings) if _v2(manifest) else None)
    return FinishOptions(music=music, graphics_path=graphics,
                         fade_in=0.5 if preferences.transitions else 0.0,
                         fade_out=0.6 if preferences.transitions else 0.0,
                         caption_style=preferences.caption_style, mode_contract=True)


def _save_mode(record: Any, manifest: dict[str, Any], plan: Sequence[MatchPlanItem], shots: Sequence[AnnotatedShot]) -> None:
    manifest["rows"] = _rows(plan)
    manifest["preferences"] = _preferences(record).model_dump(mode="json")
    _write_models(record.task_dir, "match_plan.json", plan)
    _write_models(record.task_dir, "shots_annotated.json", shots)
    write_json_atomic(record.task_dir / MODE_MANIFEST, manifest)
    write_json_atomic(record.task_dir / "jumpcuts.json", manifest.get("jumpcuts", []))
    write_json_atomic(record.task_dir / "broll_pool.json", {"shot_ids": manifest["broll_shot_ids"], "available": manifest.get("broll_available", False)})


def _verify_mode_audio_sources(root: Path, plan: Sequence[MatchPlanItem], timings: Sequence[SentenceTiming], manifest: dict[str, Any]) -> None:
    """Structural/byte checks, not a claim that ASR recognized speech correctly."""
    from .graphics import validate_mode_subtitle_artifacts

    captions = validate_mode_subtitle_artifacts(root / "subs.ass", root / "subtitle_manifest.json")
    events = captions["events"]
    by_id = {item.sentence_id: item for item in plan}
    for timing in timings:
        item = by_id[timing.sentence_id]
        entry = manifest["audio_cache"][str(timing.sentence_id)]
        audio = local_file(root, timing.audio_path)
        if _sha(audio) != entry["sha256"] or _verified_pcm_samples(audio) != round(timing.duration * NARRATION_SAMPLE_RATE):
            raise ModeQualityError("逐句 PCM 音频与渲染绑定不一致。")
        if item.kind != "quote":
            continue
        source = item.source
        if source is None or timing.audio_kind != "sync" or timing.tempo_adjustment != 1.0 or timing.text != source.asr_text:
            raise ModeQualityError("原声音频类型、实际转写或速度与源证据不一致。")
        clock = manifest["quote_source"][str(item.sentence_id)]
        if abs(clock["end"] - clock["start"] - timing.duration) > 2 * _SAMPLE:
            raise ModeQualityError("原声源区间与实际音频长度不一致。")
        expected = source.words if source.precision == "word" else []
        if len(expected) != len(timing.words) or any(
            original.w != word.text or abs(original.s - clock["start"] - word.start) > _SAMPLE
            or abs(original.e - clock["start"] - word.end) > _SAMPLE
            for original, word in zip(expected, timing.words)
        ):
            raise ModeQualityError("原声词时间没有保持真实源时钟偏移。")
        body = [e for e in events if e["kind"] == "quote" and e["sentence_id"] == timing.sentence_id]
        if manifest["preferences"]["quote_caption"] in {"spoken", "asr"}:
            actual_text = "".join(e["text"].replace("\n", "") for e in body)
            expected_text = source.asr_text.replace("\r", "").replace("\n", " ")
            if actual_text != expected_text:
                raise ModeQualityError("原声字幕没有完整保留 ASR 原话。")
        elif body:
            raise ModeQualityError("原声字幕关闭时仍存在原声字幕事件。")


def _baselines(root: Path) -> None:
    for name in ("timings", "edl", "segment_manifest", "match_plan", "narration_profile"):
        shutil.copy2(local_file(root, f"{name}.json"), root / f"source_{name}.json")


async def _complete_mode(record: Any, plan: list[MatchPlanItem], shots: list[AnnotatedShot], timings: list[SentenceTiming], manifest: dict[str, Any], settings: Settings) -> None:
    from .pipeline import _run_blocking_until_complete

    gate = _gate(record, settings)
    await _run_blocking_until_complete(partial(_verify_mode_audio_sources, record.task_dir, plan, timings, manifest))
    quality = await _run_blocking_until_complete(partial(
        generate_quality_report, record.task_dir, shots, plan, timings,
        minimum_confidence=settings.quality_min_match_confidence,
        target_chars_per_minute=_preferences(record).target_chars_per_minute,
        rate_tolerance=settings.tts_news_rate_tolerance,
        maximum_loudness_spread_lu=settings.tts_max_loudness_spread_lu,
        target_loudness_lufs=-20.0, maximum_true_peak_dbfs=-3.0,
        mode=manifest["mode"], mode_rows=_rows(plan),
        speakers=[Speaker.model_validate(value) for value in manifest["speakers"]],
        preferences=_preferences(record).model_dump(mode="json"), gate_mode=gate,
        jumpcuts=manifest.get("jumpcuts", []),
    ))
    # Persist truthful report/check counts even if the gate stops publication.
    generate_report(record.task_dir, record.task_id, shots, plan, timings, quality_report=quality)
    report = read_json(record.task_dir, "report.json")
    report["metrics"].update(mode_report_metadata(record.task_dir)["metrics"])
    write_json_atomic(record.task_dir / "report.json", report)
    _baselines(record.task_dir)
    missing = [name for name in REQUIRED_ARTIFACTS if not (record.task_dir / name).is_file()]
    if missing:
        raise RuntimeError("三模式缺少必要产物：" + ", ".join(missing))
    try:
        enforce_quality_gate(quality, gate)
    except RuntimeError as exc:
        raise ModeQualityError(str(exc)) from exc


async def _render_mode(
    record: Any, plan: list[MatchPlanItem], timings: list[SentenceTiming], shots: list[AnnotatedShot],
    snapshots: list[dict[str, Any]], manifest: dict[str, Any], settings: Settings,
    progress: Callable[[float, str], None],
) -> None:
    from .pipeline import _generated_media_intervals
    from .providers.generative import filter_generated_media_disclosure

    edl, options = build_mode_edl(plan, timings, shots, snapshots, manifest, _preferences(record))
    _write_models(record.task_dir, "edl.json", edl)
    filter_generated_media_disclosure(record.task_dir, edl)
    manifest["generated_intervals"] = _generated_media_intervals(edl)
    finish = await _finish_options(record, timings, plan, manifest, settings)
    if _v2(manifest) and manifest["mode"] == "voiceover":
        from dataclasses import replace
        ambient = await _ambient_narration(record.task_dir, edl, snapshots, manifest)
        if ambient is not None:
            finish = replace(finish, narration_path=ambient)
    hashes = {value["norm_path"]: value["norm_sha256"] for value in manifest["source_clocks"].values()}
    hashes.update(manifest.get("generated_source_hashes", {}))
    result = await render_mode_video(
        record.task_dir, edl, progress, clip_options=options, source_hashes=hashes,
        finish_options=finish, cache=manifest.get("video_cache", {}),
    )
    manifest["video_cache"], manifest["video_rendering"] = result["clips"], {key: value for key, value in result.items() if key != "clips"}
    # A jump-cut entry is only published AFTER the actual renderer succeeds.
    _save_mode(record, manifest, plan, shots)


async def _ambient_narration(root: Path, edl: Sequence[EDLItem], snapshots: list[dict[str, Any]], manifest: dict[str, Any]) -> Path | None:
    """A-mode ambience uses proven non-speech portions of ORIGINAL audio only.

    Missing/unknown speech evidence means omission, never source-voice narration.
    Silence in the composition bed is authored timeline silence, not padded
    speech. narration.m4a and every source PCM unit stay byte-identical.
    """
    from .tts_pipeline import NARRATION_AUDIO_FORMAT, _two_pass_loudnorm_filter

    by_norm = {value["norm_path"]: (upload, value) for upload, value in manifest["source_clocks"].items()}
    by_upload = {s["id"]: s for s in snapshots}
    ranges = []
    for item in edl:
        cursor = item.timeline_start
        for clip in item.clips:
            entry = by_norm.get(clip.src)
            if entry and entry[1].get("has_audio"):
                upload, clock = entry
                # Deliberately use non-speech B/C policy, never A's visual pool.
                safe = broll_source_intervals(by_upload[upload], clock, "original")
                absolute = clip.in_time + clock["norm_source_offset"]
                for lo, hi in safe:
                    start, end = max(lo, absolute), min(hi, clip.out_time + clock["norm_source_offset"])
                    start = max(start, clock["audio_offset_seconds"])
                    if end - start >= 0.1:
                        ranges.append((clock, start, end, cursor + start - absolute))
            cursor += clip.out_time - clip.in_time
    manifest["ambient"] = {"target_lufs": -24.0, "source_speech_used": False, "intervals": [], "applied": False}
    if not ranges:
        return None
    with tempfile.TemporaryDirectory(prefix=".mode-ambient-", dir=root) as directory:
        working = Path(directory)
        stems = []
        receipts = []
        for index, (clock, start, end, timeline_start) in enumerate(ranges):
            raw, stem = working / f"raw-{index}.wav", working / f"stem-{index}.wav"
            await extract_mode_pcm(root, local_file(root, clock["raw_path"]), raw,
                                   source_start=start, source_end=end, audio_offset_seconds=clock["audio_offset_seconds"])
            try:
                normalization = await _two_pass_loudnorm_filter(root, raw, target_lufs=-24, target_lra=5, true_peak_dbfs=-6, strict=True)
            except TTSProcessingError:
                # Digital silence/unmeasurable ambient must not be amplified or
                # represented as a successful -24LUFS measurement.
                continue
            await run_logged_command(["ffmpeg", "-y", "-i", str(raw), "-af",
                                      f"{NARRATION_AUDIO_FORMAT},{normalization},aresample=48000",
                                      "-c:a", "pcm_s16le", str(stem)], root, "原素材非语音环境声 -24 LUFS")
            if _verified_pcm_samples(raw) != _verified_pcm_samples(stem):
                raise TTSProcessingError("环境声处理改变了实际样本数。")
            stems.append((stem, round(timeline_start * NARRATION_SAMPLE_RATE)))
            receipts.append({"source": clock["raw_path"], "start": start, "end": end, "timeline_start": timeline_start})
        if not stems:
            return None
        # Mix each stem into a full-length lossless derivative. Bounded input
        # count avoids OS command-line limits even for fragmented VAD silence.
        mixed = root / "narration.m4a"
        for index, (stem, delay) in enumerate(stems):
            output = working / f"mix-{index}.wav"
            graph = (f"[0:a]{NARRATION_AUDIO_FORMAT}[n];[1:a]{NARRATION_AUDIO_FORMAT},adelay={delay}S:all=1[a];"
                     "[n][a]amix=inputs=2:normalize=0:duration=first[out]")
            await run_logged_command(["ffmpeg", "-y", "-i", str(mixed), "-i", str(stem), "-filter_complex", graph,
                                      "-map", "[out]", "-c:a", "pcm_s16le", str(output)], root, "混入非语音环境声（保留完整旁白）")
            mixed = output
        normalization = await _two_pass_loudnorm_filter(root, mixed, target_lufs=-20, target_lra=5, true_peak_dbfs=-3.5, strict=True)
        result = root / "ambient_narration.m4a"
        await run_logged_command(["ffmpeg", "-y", "-i", str(mixed), "-af",
                                  f"{NARRATION_AUDIO_FORMAT},{normalization},aresample=48000",
                                  "-c:a", "aac", "-b:a", "192k", str(result)], root, "实测环境声合成音轨归一")
    manifest["ambient"].update(applied=True, intervals=receipts, sha256=_sha(result))
    return result


async def run_mode_pipeline(record: PipelineTask, reporter: PipelineReporter, settings: Settings) -> None:
    """Run all ten stages; only stage-specific, genuinely needed providers exist."""
    from .pipeline import (
        _run_upload_validation, _run_scene_detection, _run_vision_annotation,
        _start_stage, _complete_stage, _log_stage_abort,
    )
    from .provenance import write_pipeline_manifest

    inputs = _inputs(record)
    if len(inputs) > settings.max_sentences:
        raise ValueError("客户端句子数量超过上限。")
    require_media_tools()
    mode = _mode(record)
    snapshots = _snapshots(record)
    preferences = _preferences(record)
    stage_cache = ModeStageCache(record.task_dir)
    resume_from = getattr(record, "resume_from", None)
    # Requested stage is only an observation. Every reuse is independently
    # signature/hash verified, so an unsafe caller hint cannot skip missing work.
    manifest: dict[str, Any] = {
        "schema_version": 1, "recipe": MODE_RECIPE, "mode": mode,
        "media_contract": V2_MEDIA_CONTRACT,
        "resume_requested_from": resume_from,
        "terminal_punctuation": {str(value.idx): getattr(value, "terminal_punctuation", None) for value in inputs},
        "mode_contract": True, "speakers": [Speaker.model_validate(value).model_dump(mode="json") for value in _get(record, "speakers", [])],
        "sentences": [value.model_dump(mode="json") for value in inputs],
        "upload_ids": [value["id"] for value in snapshots], "rows": [], "jumpcuts": [],
        "lower_thirds": [], "broll_available": False, "preferences": preferences.model_dump(mode="json"),
    }
    write_pipeline_manifest(record.task_dir, settings, preferences=preferences.model_dump(mode="json"))
    provenance = read_json(record.task_dir, "pipeline_manifest.json")
    provenance["mode"] = mode
    provenance["mode_recipe"] = MODE_RECIPE
    provenance["media_contract"] = V2_MEDIA_CONTRACT
    provenance["source_sha256"].update({name: _sha(Path(__file__).parent / name) for name in ("mode_pipeline.py", "production_modes.py", "mode_rules.json", "speech_analysis.py", "providers/local_speech.py", "graphics.py", "audio_filters.py")})
    write_json_atomic(record.task_dir / "pipeline_manifest.json", provenance)
    write_json_atomic(record.task_dir / MODE_MANIFEST, manifest)
    await _run_upload_validation(record, reporter, settings)
    stage = 2
    started = _start_stage(record, reporter, stage, "复用预转写并规格化素材；失败转写仅正式补跑一次")
    try:
        await _retry_failed_asr(record, snapshots, settings, lambda done, total, msg: reporter.update_stage(record, 2, 0.65 * done / total, msg))
        source_binding = [[upload.model_dump(mode="json"), _sha(upload.path)] for upload in record.uploads]
        local_model = getattr(settings, "local_speaker_model_path", None)
        signature2 = _stage_signature(2, settings, source_binding,
                          _sha(Path(local_model)) if local_model and Path(local_model).is_file() else None,
                          [[s["id"], s.get("asr_result"), s.get("speech_intervals"), s.get("audio_offset_seconds")] for s in snapshots])
        cached2 = stage_cache.load(2, signature2)
        if cached2:
            manifest["source_clocks"] = cached2["source_clocks"]
            manifest["local_speech"] = cached2["local_speech"]
            normalized = [local_file(record.task_dir, cached2["source_clocks"][s["id"]]["norm_path"]) for s in snapshots]
        else:
            manifest["local_speech"] = await analyze_task_speakers(record, snapshots, settings)
            normalized = await normalize_assets(record.task_dir, processing_uploads(record.task_dir, record.uploads),
                                                lambda done, total, msg: reporter.update_stage(record, 2, 0.65 + 0.35 * done / total, msg))
            manifest["source_clocks"] = await _source_clocks(record, snapshots, normalized)
            stage_cache.save(2, signature2, {"source_clocks": manifest["source_clocks"], "local_speech": manifest["local_speech"]},
                             [p.relative_to(record.task_dir).as_posix() for p in normalized])
    except BaseException:
        _log_stage_abort(record, 2, started)
        raise
    _complete_stage(record, reporter, 2, started, "预转写复用和真实源时钟校验完成")
    # Scene detection remains local. Filter before any Vision request.
    signature3 = _stage_signature(3, settings, manifest["source_clocks"], settings.model_dump(mode="json").get("scene_threshold"))
    cached3 = stage_cache.load(3, signature3)
    if cached3:
        started = _start_stage(record, reporter, 3, "复用已校验的镜头切分")
        raw_shots = [Shot.model_validate(value) for value in cached3["shots"]]
        _complete_stage(record, reporter, 3, started, "镜头切分缓存绑定已校验")
    else:
        raw_shots = await _run_scene_detection(record, reporter, normalized, settings)
        stage_cache.save(3, signature3, {"shots": [s.model_dump(mode="json") for s in raw_shots]})
    pool_shots = _pool_shots(raw_shots, snapshots, manifest["source_clocks"], mode)
    _write_models(record.task_dir, "shots.json", pool_shots)
    signature4 = _stage_signature(4, settings, signature3, [s.model_dump(mode="json") for s in pool_shots])
    cached4 = stage_cache.load(4, signature4)
    if pool_shots:
        # Do NOT pass raw ASR into legacy shot-time fusion: its clock is audio-
        # relative and may not match a prepared crop. Mode pools use safe metadata.
        if cached4:
            started = _start_stage(record, reporter, 4, "复用已校验的画面理解；不重复 Provider 请求")
            shots = [AnnotatedShot.model_validate(value) for value in cached4["shots"]]
            _complete_stage(record, reporter, 4, started, "画面理解缓存绑定已校验")
        else:
            stage_cache.begin(4, signature4)
            shots = await _run_vision_annotation(record, reporter, pool_shots, [], settings)
            append_source_notes(record.task_dir, shots, record.uploads)
            stage_cache.save(4, signature4, {"shots": [s.model_dump(mode="json") for s in shots]},
                             [s.thumb_path for s in shots if s.thumb_path])
    else:
        started = _start_stage(record, reporter, 4, "无可用空镜，跳过画面理解；原声无需 Vision")
        shots = []
        _write_models(record.task_dir, "shots_annotated.json", shots)
        _complete_stage(record, reporter, 4, started, "空镜为空，未调用 Vision；跳切将按实际条件降级推近")
    manifest["next_shot_id"] = max((shot.shot_id for shot in shots), default=-1) + 1
    manifest["base_broll_shot_ids"] = [shot.shot_id for shot in shots]
    started = _start_stage(record, reporter, 5, "读取客户端已确认句子；不调用模型分句或改写")
    sentences = _write_script(record, inputs)
    _complete_stage(record, reporter, 5, started, f"保留 {len(sentences)} 个确认句子；每句旁白一个视觉节拍")
    started = _start_stage(record, reporter, 6, "使用与预览相同的原话匹配函数；仅旁白检索画面")
    signature6 = _stage_signature(6, settings, signature4, manifest["sentences"], snapshots,
                                  manifest["speakers"], preferences.custom_instructions, preferences.generative_fill)
    cached6 = stage_cache.load(6, signature6)
    try:
        aligned = await _align_quotes_offloop(inputs, snapshots, [Speaker.model_validate(value) for value in manifest["speakers"]])
        missing = [row for row in aligned if row["kind"] == "quote" and (row["source"] is None or row["source"]["score"] < rules.MATCH_LOW)]
        if missing:
            manifest["rows"] = aligned
            write_json_atomic(record.task_dir / MODE_MANIFEST, manifest)
            raise QuoteMissingError(missing)
        quote_plan = [MatchPlanItem(
            sentence_id=row["idx"], text=next(value.text for value in inputs if value.idx == row["idx"]),
            kind="quote", source=QuoteTake.model_validate(row["source"]),
            alt_takes=[QuoteTake.model_validate(take) for take in row["alt_takes"]],
            shot_id=0, confidence=row["score"], candidates=[],
        ) for row in aligned if row["kind"] == "quote"]
        filtered_pool = await _exclude_quotes(record, shots, quote_plan, manifest)
        all_shots = {shot.shot_id: shot for shot in [*shots, *filtered_pool]}
        shots = list(all_shots.values())
        manifest["broll_shot_ids"] = [shot.shot_id for shot in filtered_pool]
        if cached6:
            narration = [MatchPlanItem.model_validate(value) for value in cached6["narration"]]
            shots = [AnnotatedShot.model_validate(value) for value in cached6["shots"]]
            manifest.update(cached6["manifest"])
        else:
            stage_cache.begin(6, signature6)
            narration = await _match_narration(record, [s for s in sentences if s.kind == "narration"], filtered_pool, settings,
                                              lambda fraction, msg: reporter.update_stage(record, 6, fraction, msg))
            narration = await _generated_narration(record, [s for s in sentences if s.kind == "narration"], narration, shots, manifest, settings)
            stage_cache.save(6, signature6, {
                "narration": [p.model_dump(mode="json") for p in narration],
                "shots": [s.model_dump(mode="json") for s in shots],
                "manifest": {key: manifest[key] for key in ("next_shot_id", "generated_source_hashes", "generative_fill") if key in manifest},
            }, list(manifest.get("generated_source_hashes", {})))
        by_id = {item.sentence_id: item for item in [*quote_plan, *narration]}
        plan = [by_id[value.idx] for value in inputs]
        layout, _groups = quote_layout(plan, snapshots, manifest["source_clocks"])
        await _quote_shots(record, plan, layout, manifest, shots)
        _save_mode(record, manifest, plan, shots)
    except BaseException:
        _log_stage_abort(record, 6, started)
        raise
    _complete_stage(record, reporter, 6, started, f"匹配完成：原声 {len(quote_plan)} 句，旁白 {len(narration)} 句；未使用自动同期声替换")
    started = _start_stage(record, reporter, 7, "只为旁白配音；原声使用真实源 PCM48k")
    signature7 = _stage_signature(7, settings, signature6, [p.model_dump(mode="json") for p in plan],
                                  preferences.voice, preferences.target_chars_per_minute, preferences.enhance_speech,
                                  _sha(record.task_dir / "own_voice.wav") if (record.task_dir / "own_voice.wav").is_file() else None)
    cached7 = stage_cache.load(7, signature7)
    try:
        if cached7:
            manifest.update(cached7["manifest"])
        else:
            stage_cache.begin(7, signature7)
        timings = await _assemble_audio(record, plan, snapshots, manifest, settings, lambda fraction, msg: reporter.update_stage(record, 7, fraction, msg))
        stage_cache.save(7, signature7, {"manifest": {key: manifest[key] for key in (
            "audio_cache", "audio_mix", "pronunciation_plan", "tts_provider_profile") if key in manifest}},
            [t.audio_path for t in timings] + ["narration.m4a"])
    except BaseException:
        _log_stage_abort(record, 7, started)
        raise
    _complete_stage(record, reporter, 7, started, "逐句与完整音轨已生成，原声未替换为 TTS")
    started = _start_stage(record, reporter, 8, "按实际语音证据生成固定字幕，原声不按稿子改字")
    generate_mode_subtitles(record.task_dir, timings, plan, preferences, title=_title(record.script))
    _complete_stage(record, reporter, 8, started, "模式字幕与固定模板绑定完成")
    started = _start_stage(record, reporter, 9, "渲染真实画面、跳切遮盖和独立人名条")
    signature9 = _key([V2_MEDIA_CONTRACT, signature7, preferences.model_dump(mode="json"),
                       _sha(record.task_dir / "subs.ass"), manifest["speakers"],
                       _sha(Path(__file__).with_name("rendering.py")), _sha(Path(__file__).with_name("graphics.py"))])
    cached9 = stage_cache.load(9, signature9)
    try:
        if cached9:
            manifest.update(cached9["manifest"])
            for item in plan:
                item.jumpcut_before = next((row.get("jumpcut_before", False) for row in manifest["rows"] if row["idx"] == item.sentence_id), False)
            _save_mode(record, manifest, plan, shots)
        else:
            await _render_mode(record, plan, timings, shots, snapshots, manifest, settings, lambda fraction, msg: reporter.update_stage(record, 9, fraction, msg))
            names = ["final.mp4", "video_only.mp4", "edl.json", "segment_manifest.json", "lower_thirds.json"]
            names += [entry["path"] for entry in manifest.get("video_cache", {}).values()]
            stage_cache.save(9, signature9, {"manifest": {key: manifest[key] for key in (
                "video_cache", "video_rendering", "lower_thirds", "jumpcuts", "jumpcut_rendering", "rows", "ambient", "generated_intervals") if key in manifest}}, names)
    except BaseException:
        _log_stage_abort(record, 9, started)
        raise
    _complete_stage(record, reporter, 9, started, "三模式成片渲染完成")
    started = _start_stage(record, reporter, 10, "测量成片与各单元音频，合并原声和旁白检查")
    try:
        await _complete_mode(record, plan, shots, timings, manifest, settings)
    except BaseException:
        _log_stage_abort(record, 10, started)
        raise
    _complete_stage(record, reporter, 10, started, f"真实质量检查与匹配报告完成，共 {len(plan)} 行")


async def edit_mode_workspace(work: Any, original: Any, payload: Any, settings: Settings) -> None:
    """Rebuild a caller-owned TEMP revision; never mutate/publish original.

    payload: keep_sentence_ids, edits(sentence_id/text/shot_id/instruction/
    recording_id), quote_trims(id/start/end), quote_takes(id/take_id),
    to_narration:list[int], speakers, pacing, caption_style, enhance_speech.
    Validation/auth/revision ownership is the workbench's responsibility; the
    media boundary defensively rechecks identity, raw paths, immutable quotes,
    known takes, prepared clocks and caches. All REQUIRED_ARTIFACTS + report are
    present on successful return. source_* copies describe this new baseline.
    """
    from .pipeline import _run_blocking_until_complete

    root = Path(work.task_dir)
    if root.resolve() == Path(original.task_dir).resolve():
        raise ValueError("模式编辑只能写临时工作目录，不能直接修改原任务。")
    manifest = read_json(root, MODE_MANIFEST)
    if manifest.get("recipe") != MODE_RECIPE or manifest.get("mode") != _mode(original):
        raise ValueError("模式修订缺少匹配的来源清单，不能走旧自动同期声路径。")
    stage_reporter = _get(work, "reporter")
    first_stage = _get(stage_reporter, "first_stage", 7)
    if first_stage not in (7, 8):
        raise ValueError("模式修订只能从真实音频或字幕阶段开始。")
    if stage_reporter is not None:
        stage_reporter.start_stage(work, first_stage, "核对变化句与真实来源；未变化的音频和画面按绑定复用")
    work.mode, work.mode_contract = manifest["mode"], True
    work.quality_gate_mode = _get(original, "quality_gate_mode")
    preferences = _preferences(work)
    for name in ("pacing", "caption_style", "enhance_speech"):
        value = _get(payload, name)
        if value is not None:
            setattr(preferences, name, value)
    work.preferences = EditingPreferences.model_validate(preferences.model_dump())
    if work.mode == "original" and (_get(payload, "pacing") is not None or _get(payload, "to_narration", [])):
        raise ValueError("只用原声模式不能改旁白或调整原声语速。")
    old_plan = [MatchPlanItem.model_validate(value) for value in read_json(root, "match_plan.json")]
    keep = list(_get(payload, "keep_sentence_ids", []))
    by_id = {item.sentence_id: item for item in old_plan}
    if not keep or len(set(keep)) != len(keep) or any(type(i) is not int or i not in by_id for i in keep):
        raise ValueError("保留句子列表含未知或重复身份。")
    # Preserve canonical source order; clients cannot accidentally reorder speech.
    if keep != [item.sentence_id for item in old_plan if item.sentence_id in set(keep)]:
        raise ValueError("不支持打乱原稿句子顺序。")
    plan = [by_id[i].model_copy(deep=True) for i in keep]
    by_id = {item.sentence_id: item for item in plan}
    edits_list = list(_get(payload, "edits", []))
    edits = {_get(value, "sentence_id"): value for value in edits_list}
    if len(edits) != len(edits_list) or not set(edits).issubset(by_id):
        raise ValueError("编辑对象必须是不同的保留句子。")
    conversions = set(_get(payload, "to_narration", []))
    trims = list(_get(payload, "quote_trims", []))
    takes = list(_get(payload, "quote_takes", []))
    if conversions and work.mode != "mixed":
        raise ValueError("只有混合模式允许显式将原声转为旁白。")
    operations = [_get(value, "id") for value in [*trims, *takes]] + list(conversions)
    if len(operations) != len(set(operations)) or any(i not in by_id or by_id[i].kind != "quote" for i in operations):
        raise ValueError("原声操作必须引用唯一的保留原声句。")
    # Immutable quote text/voice/visuals: conversion is a separate explicit act.
    # Same-batch text is rejected to match the public workbench guard.
    if any(by_id[sentence_id].kind == "quote" for sentence_id in edits):
        raise ValueError("原声不能改字、配音或换画面；请先单独转为旁白。")
    changed_text: set[int] = set()
    rematch: set[int] = set()
    for sentence_id in conversions:
        item = by_id[sentence_id]
        item.kind, item.to_narration, item.sync_sound = "narration", True, None
        item.jumpcut_before = False
        # Retain source, alt_takes and trim as historical attribution.
        changed_text.add(sentence_id)
        rematch.add(sentence_id)
    for value in trims:
        item = by_id[_get(value, "id")]
        if item.source is None:
            raise ValueError("原声裁剪缺少真实源范围。")
        item.source = QuoteTake.model_validate(rules.validate_quote_trim(item.source, _get(value, "start"), _get(value, "end")))
        item.trim = {"start": item.source.start, "end": item.source.end}
        # Only this explicit, validated source edit changes confirmed quote text.
        # The caller's immutable revision retains the manuscript; current script,
        # sentences and report below must describe the audio actually retained.
        item.text = item.source.asr_text
    for value in takes:
        item = by_id[_get(value, "id")]
        selected = next((take for take in item.alt_takes if take.take_id == _get(value, "take_id")), None)
        if selected is None:
            raise ValueError("换段必须选择当前匹配计划里的真实候选 take。")
        previous = item.source
        item.source = QuoteTake.model_validate(rules.validate_quote_trim(selected, selected.start, selected.end))
        item.trim = None
        item.text = item.source.asr_text
        item.alt_takes = ([previous] if previous else []) + [take for take in item.alt_takes if take.take_id != selected.take_id]
        item.alt_takes = item.alt_takes[:rules.LIMITS["alternative_takes"]]
    recordings = {}
    for sentence_id, edit in edits.items():
        item = by_id[sentence_id]
        text = _get(edit, "text")
        if text is not None and text != item.text:
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise ValueError("旁白文字无效。")
            item.text = text
            changed_text.add(sentence_id)
            if _get(edit, "shot_id") is None:
                rematch.add(sentence_id)
        if _get(edit, "instruction"):
            rematch.add(sentence_id)
        if _get(edit, "recording_id"):
            recordings[sentence_id] = _get(edit, "recording_id")
    snapshots = read_json(root, "pretranscripts.json")
    shots = [AnnotatedShot.model_validate(value) for value in read_json(root, "shots_annotated.json")]
    # The original workbench currently copies only enumerated artifacts. Copy
    # missing immutable dependencies individually, never overwrite old media.
    dependencies = []
    for clock in manifest["source_clocks"].values():
        for key, digest_key in (("raw_path", "raw_sha256"), ("norm_path", "norm_sha256")):
            relative = clock[key]
            target = local_file(root, relative, exists=False)
            if not target.is_file():
                source = local_file(original.task_dir, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                await _run_blocking_until_complete(partial(shutil.copy2, source, target))
            dependencies.append((target, clock[digest_key]))
    for target, expected in dependencies:
        if await _run_blocking_until_complete(partial(_sha, target)) != expected:
            raise ValueError("模式修订源媒体与初始绑定不一致。")
    for sentence_id, recording_id in recordings.items():
        for suffix in ("wav", "json"):
            relative = f"recordings/{recording_id}.{suffix}"
            target = local_file(root, relative, exists=False)
            if not target.is_file():
                source = local_file(original.task_dir, relative)
                target.parent.mkdir(exist_ok=True)
                await _run_blocking_until_complete(partial(shutil.copy2, source, target))
    for sentence_id, entry in manifest.get("audio_cache", {}).items():
        if (entry.get("raw_recipe") and int(sentence_id) in keep
                and manifest.get("preferences", {}).get("enhance_speech") != work.preferences.enhance_speech):
            # Published revisions need not retain unprocessed derived PCM: the
            # recipe re-extracts it from the owned original recording, without
            # rerunning ASR or denoising a previously processed derivative.
            relative = entry["raw_recipe"]["source_audio"]
            target = local_file(root, relative, exists=False)
            if not target.is_file():
                source = local_file(original.task_dir, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                await _run_blocking_until_complete(partial(shutil.copy2, source, target))
    # Physical source ranges may be changed by a take/trim, including neighbours
    # in a formerly continuous group. Their audio keys, not a blanket invalidation,
    # decide which units are regenerated.
    layout, _groups = quote_layout(plan, snapshots, manifest["source_clocks"])
    await _quote_shots(work, plan, layout, manifest, shots)
    base_ids = set(manifest.get("base_broll_shot_ids", manifest["broll_shot_ids"]))
    pool = await _exclude_quotes(work, [shot for shot in shots if shot.shot_id in base_ids], plan, manifest)
    all_shots = {shot.shot_id: shot for shot in [*shots, *pool]}
    shots = list(all_shots.values())
    manifest["broll_shot_ids"] = [shot.shot_id for shot in pool]
    # Retained narration might become illegal after a quote take moves nearby.
    legal = {shot.shot_id for shot in pool if shot.status == "available" and shot.quality is not None}
    generated_ids = {shot.shot_id for shot in shots if shot.media_origin == "generated"}
    rematch.update(item.sentence_id for item in plan if item.kind == "narration" and item.shot_id not in legal | generated_ids)
    for sentence_id, edit in edits.items():
        shot_id = _get(edit, "shot_id")
        if shot_id is not None:
            if shot_id not in legal:
                raise ValueError("所选画面不在当前合法空镜池，或与原声前后两秒重叠。")
            item = by_id[sentence_id]
            item.shot_id, item.confidence, item.is_fallback = shot_id, 0.0, True
            item.candidates = [MatchCandidate(shot_id=shot_id, similarity=0.0)]
            item.beat_matches, item.sync_sound = [], None
            item.replacement_instruction = "人工选镜，未验证语义匹配"
            rematch.discard(sentence_id)
    reserved = {item.shot_id for item in plan if item.sentence_id not in rematch}
    queries = []
    for item in plan:
        if item.sentence_id not in rematch:
            continue
        instruction = _get(edits.get(item.sentence_id, {}), "instruction", "") or ""
        sentence = _sentences([SentenceInput(idx=item.sentence_id, text=item.text, kind="narration")])[0]
        sentence.visual_beats[0].text = (item.text + " " + instruction).strip()
        queries.append(sentence)
    replacements = await _match_narration(work, queries, [shot for shot in pool if shot.shot_id not in reserved], settings, lambda *_: None)
    replacements = await _generated_narration(work, queries, replacements, shots, manifest, settings)
    for replacement in replacements:
        item = by_id[replacement.sentence_id]
        item.shot_id, item.confidence, item.is_fallback = replacement.shot_id, replacement.confidence, replacement.is_fallback
        item.candidates, item.beat_matches, item.alternates = replacement.candidates, replacement.beat_matches, replacement.alternates
        item.sync_sound = None
        item.replacement_instruction = _get(edits.get(item.sentence_id, {}), "instruction")
    for item in plan:
        relative = f"thumbs/shot_{item.shot_id}.jpg"
        target = local_file(root, relative, exists=False)
        if not target.is_file():
            source = local_file(original.task_dir, relative, exists=False)
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                await _run_blocking_until_complete(partial(shutil.copy2, source, target))
            else:
                await extract_shot_thumbnail(root, next(s for s in shots if s.shot_id == item.shot_id))
    ids = [item.shot_id for item in plan]
    if len(set(ids)) != len(ids):
        raise ValueError("修订导致同一个物理范围镜头被重复使用。")
    people = _get(payload, "speakers")
    if people is not None:
        manifest["speakers"] = [Speaker.model_validate(value).model_dump(mode="json") for value in people]
    work.speakers = [Speaker.model_validate(value) for value in manifest["speakers"]]
    old_inputs = {value["idx"]: value for value in manifest["sentences"]}
    inputs = [SentenceInput.model_validate({**old_inputs[item.sentence_id], "text": item.text, "kind": item.kind}) for item in plan]
    work.sentences, work.upload_ids = inputs, manifest["upload_ids"]
    work.script = (_title(original.script) or "") + "\n" + "\n".join(item.text for item in plan)
    manifest["sentences"] = [value.model_dump(mode="json") for value in inputs]
    _write_script(work, inputs)
    # Repair only missing conventional immutable audit files in TEMP.
    for name in ("pipeline_manifest.json", "asr_transcripts.json", "shots.json"):
        target = root / name
        if not target.is_file():
            shutil.copy2(local_file(original.task_dir, name), target)
    timings = await _assemble_audio(work, plan, snapshots, manifest, settings,
                                   (lambda fraction, message: stage_reporter.update_stage(work, 7, fraction, message))
                                   if stage_reporter is not None and first_stage == 7 else (lambda *_: None),
                                   changed_text=changed_text, recordings=recordings)
    if stage_reporter is not None and first_stage == 7:
        stage_reporter.complete_stage(work, 7, "变化音频处理与未变化音频复用完成")
        stage_reporter.start_stage(work, 8, "按实际语音证据更新字幕与说话人信息")
    generate_mode_subtitles(root, timings, plan, work.preferences, title=_title(work.script), v2=_v2(manifest))
    if stage_reporter is not None:
        stage_reporter.complete_stage(work, 8, "固定模板字幕和真实时钟绑定完成")
        stage_reporter.start_stage(work, 9, "复用未变化画面，仅渲染变化片段及重新烧录图文")
    await _render_mode(work, plan, timings, shots, snapshots, manifest, settings,
                       (lambda fraction, message: stage_reporter.update_stage(work, 9, fraction, message))
                       if stage_reporter is not None else (lambda *_: None))
    if stage_reporter is not None:
        stage_reporter.complete_stage(work, 9, "修订成片已实际渲染并完成规格校验")
        stage_reporter.start_stage(work, 10, "重新测量修订成片并合并所有检查")
    await _complete_mode(work, plan, shots, timings, manifest, settings)
    if stage_reporter is not None:
        stage_reporter.complete_stage(work, 10, "修订质量检查、报告及必要产物校验完成")


__all__ = ["run_mode_pipeline", "edit_mode_workspace", "mode_report_metadata", "QuoteMissingError",
           "ModeQualityError", "REQUIRED_ARTIFACTS", "broll_source_intervals", "quote_layout", "build_mode_edl"]