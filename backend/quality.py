import json
import math
import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from .matching import SHOT_REUSE_COOLDOWN_BEATS
from .models import AnnotatedShot, EDLItem, GeneratedMediaDisclosureManifest, MatchPlanItem, ScriptDocument, SentenceTiming, is_generated_media_path
from .production_modes import Mode, Speaker, evaluate_mode_checks
from .storage import write_json_atomic


QualitySeverity = Literal["warning", "error"]
MAX_NARRATION_LOUDNESS_SPREAD_LU = 2.0
MIN_NEWS_SPEAKING_RATE_CPM = 240.0
MAX_NEWS_SPEAKING_RATE_CPM = 280.0
MAX_VISUAL_SHOT_USE_COUNT = 1
QUALITY_MEDIA_COMMAND_TIMEOUT_SECONDS = 600.0
MAX_FREEZE_PAD_SECONDS = 0.3
MAX_VISUAL_CLIP_SECONDS = 6.5
GENERATED_MEDIA_DISCLOSURE_FILE = "generated_media_disclosure.json"
MODE_CHECK_CODE_ALIASES = {"NARRATION_SPEAKING_RATE_OUT_OF_RANGE": "NARRATION_SPEAKING_RATE"}


def generate_quality_report(
    task_dir: Path,
    shots: Sequence[AnnotatedShot],
    match_plan: Sequence[MatchPlanItem],
    timings: Sequence[SentenceTiming],
    *,
    minimum_confidence: float,
    target_loudness_lufs: float = -20.0,
    maximum_loudness_spread_lu: float = MAX_NARRATION_LOUDNESS_SPREAD_LU,
    minimum_speaking_rate_cpm: float = MIN_NEWS_SPEAKING_RATE_CPM,
    maximum_speaking_rate_cpm: float = MAX_NEWS_SPEAKING_RATE_CPM,
    target_chars_per_minute: float | None = None,
    rate_tolerance: float | None = None,
    maximum_true_peak_dbfs: float = -1.0,
    output_path: Path | None = None,
    media_path: Path | None = None,
    edl_path: Path | None = None,
    mode: Mode | None = None,
    mode_rows: list[dict[str, Any]] | None = None,
    speakers: Sequence[Speaker] = (),
    preferences: dict[str, Any] | None = None,
    gate_mode: Literal["warn", "block"] = "warn",
    jumpcuts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    all_plan = list(match_plan)
    if mode is not None:
        if gate_mode not in {"warn", "block"}:
            raise ValueError("模式质量门禁必须为 warn 或 block。")
        # Quote semantic confidence is lexical ASR evidence, NOT a visual score.
        # Old visual and speaking-rate checks apply only to narration units.
        match_plan = [item for item in all_plan if item.kind == "narration"]
    narration_ids = {item.sentence_id for item in match_plan}
    if target_chars_per_minute is not None:
        if target_chars_per_minute <= 0:
            raise ValueError("新闻旁白目标语速必须大于 0。")
        resolved_tolerance = 0.08 if rate_tolerance is None else rate_tolerance
        if not 0.0 <= resolved_tolerance <= 0.25:
            raise ValueError("新闻旁白语速容差必须位于 0 到 0.25。")
        minimum_speaking_rate_cpm = target_chars_per_minute * (1.0 - resolved_tolerance)
        maximum_speaking_rate_cpm = target_chars_per_minute * (1.0 + resolved_tolerance)
    document = _load_script_document(task_dir)
    shots_by_id = {shot.shot_id: shot for shot in shots}
    issues: list[dict[str, Any]] = []

    fallback_count = sum(item.is_fallback for item in match_plan)
    contextual_overlay_count = sum(item.overlay_kind is not None for item in match_plan)
    low_confidence_count = 0
    explicit_beat_count = 0
    covered_explicit_beat_count = 0
    multi_beat_sentence_count = 0
    distinct_multi_shot_sentence_count = 0
    visual_assignments: list[tuple[int, int, int, int]] = []
    for item in match_plan:
        group_id = item.visual_group_id if item.visual_group_id is not None else item.sentence_id
        if item.sync_sound is not None:
            visual_assignments.append(
                (item.sentence_id, 0, item.sync_sound.shot_id, group_id)
            )
        elif item.beat_matches:
            visual_assignments.extend(
                (item.sentence_id, beat.beat_id, beat.shot_id, group_id)
                for beat in item.beat_matches
            )
        else:
            visual_assignments.append((item.sentence_id, 0, item.shot_id, group_id))
    visual_shot_usage: Counter[int] = Counter(
        shot_id for _, _, shot_id, _ in visual_assignments
    )
    visual_group_shot_usage: Counter[int] = Counter(
        shot_id
        for _, shot_id in {
            (group_id, shot_id)
            for _, _, shot_id, group_id in visual_assignments
        }
    )
    available_shot_count = sum(
        shot.status == "available" and shot.quality is not None for shot in shots
    )
    visual_shot_reuse_limit = MAX_VISUAL_SHOT_USE_COUNT
    overused_visual_shots = {
        shot_id: count
        for shot_id, count in visual_shot_usage.items()
        if count > visual_shot_reuse_limit
    }
    rapid_visual_reuse_count = sum(
        any(
            previous_shot_id == shot_id
            for _, _, previous_shot_id, _ in visual_assignments[
                max(0, index - SHOT_REUSE_COOLDOWN_BEATS) : index
            ]
        )
        for index, (_, _, shot_id, _) in enumerate(visual_assignments)
    )

    if overused_visual_shots:
        details = "、".join(
            f"镜头 {shot_id} 使用 {count} 次"
            for shot_id, count in sorted(overused_visual_shots.items())
        )
        issues.append(
            _issue(
                "VISUAL_SHOT_OVERUSED",
                "error",
                f"{details}，违反每个镜头最多使用 {visual_shot_reuse_limit} 次的硬约束。",
            )
        )
    if rapid_visual_reuse_count and available_shot_count > 1:
        issues.append(
            _issue(
                "VISUAL_SHOT_REUSED_TOO_SOON",
                "warning",
                f"发现 {rapid_visual_reuse_count} 次同一镜头在相邻或短间隔视觉节拍中重复。",
            )
        )
    if contextual_overlay_count:
        issues.append(
            _issue(
                "CONTEXTUAL_BROLL_OVERLAY",
                "warning",
                f"有 {contextual_overlay_count} 个播音单元使用相关环境画面加事实文字叠层，"
                "该画面不表示对文字事实的直接视觉证明。",
            )
        )

    for item in match_plan:
        if item.confidence < minimum_confidence:
            low_confidence_count += 1
            issues.append(
                _issue(
                    "LOW_MATCH_CONFIDENCE",
                    "warning",
                    f"匹配置信度 {item.confidence:.3f} 低于 {minimum_confidence:.3f}。",
                    sentence_id=item.sentence_id,
                )
            )
        if item.is_fallback:
            issues.append(
                _issue(
                    "MATCH_FALLBACK",
                    "error",
                    "该播音单元使用了无语义保证的兜底镜头。",
                    sentence_id=item.sentence_id,
                )
            )

        beat_matches = item.beat_matches
        if len(beat_matches) > 1:
            multi_beat_sentence_count += 1
            if len({beat.shot_id for beat in beat_matches}) > 1:
                distinct_multi_shot_sentence_count += 1
            else:
                issues.append(
                    _issue(
                        "MULTI_BEAT_SINGLE_SHOT",
                        "warning",
                        "多个视觉节拍仍被压缩到同一个镜头。",
                        sentence_id=item.sentence_id,
                    )
                )

        for beat in beat_matches:
            if not beat.requires_entity_coverage:
                continue
            explicit_beat_count += 1
            shot = shots_by_id.get(beat.shot_id)
            shot_text = _shot_text(shot) if shot else ""
            matched_entities = [
                entity
                for entity in beat.entities
                if len(entity.strip()) >= 2 and _normalize(entity) in _normalize(shot_text)
            ]
            selected_candidate = next(
                (candidate for candidate in beat.candidates if candidate.shot_id == beat.shot_id),
                None,
            )
            verified_entities = (
                selected_candidate.verified_terms
                if selected_candidate is not None
                and selected_candidate.verification_confidence is not None
                and selected_candidate.verification_confidence >= 0.75
                else []
            )
            if matched_entities or verified_entities:
                covered_explicit_beat_count += 1
            else:
                issues.append(
                    _issue(
                        "EXPLICIT_ENTITY_NOT_COVERED",
                        "error",
                        f"镜头元数据未覆盖视觉节拍实体：{'、'.join(beat.entities) or beat.text}。",
                        sentence_id=item.sentence_id,
                        beat_id=beat.beat_id,
                        shot_id=beat.shot_id,
                    )
                )

    word_aligned_count = sum(bool(timing.words) for timing in timings)
    excessive_gap_count = sum(timing.gap_after > 0.30 for timing in timings[:-1])
    if excessive_gap_count:
        issues.append(
            _issue(
                "EXCESSIVE_CONFIGURED_GAP",
                "warning",
                f"有 {excessive_gap_count} 个配置句间隔超过 300ms。",
            )
        )

    title_spoken = bool(document.title) and any(
        document.title and document.title.strip() == timing.text.strip()
        for timing in timings
    )
    if title_spoken:
        issues.append(_issue("TITLE_SPOKEN_AS_BODY", "error", "标题仍被作为普通正文播报。"))

    tts_audio_metrics: list[dict[str, Any]] = []
    for timing in timings:
        if timing.audio_kind != "tts" or (mode is not None and timing.sentence_id not in narration_ids):
            continue
        measured_rate = timing.speaking_rate_cpm
        if measured_rate is None and timing.spoken_unit_count is not None:
            measured_rate = timing.spoken_unit_count * 60.0 / timing.duration
        tts_audio_metrics.append(
            {
                "sentence_id": timing.sentence_id,
                "spoken_unit_count": timing.spoken_unit_count,
                "speaking_rate_cpm": round(measured_rate, 3) if measured_rate is not None else None,
                "tempo_adjustment": timing.tempo_adjustment,
                "integrated_lufs": timing.integrated_lufs,
                "true_peak_dbfs": timing.true_peak_dbfs,
                "tts_session_id": timing.tts_group_id,
                "voice_profile_id": timing.voice_profile_id,
            }
        )
    rate_metrics = [
        item
        for item in tts_audio_metrics
        if isinstance(item.get("speaking_rate_cpm"), (int, float))
    ]
    rate_values = [float(item["speaking_rate_cpm"]) for item in rate_metrics]
    out_of_range_rates = [
        item
        for item in rate_metrics
        if not minimum_speaking_rate_cpm
        <= float(item["speaking_rate_cpm"])
        <= maximum_speaking_rate_cpm
    ]
    if out_of_range_rates:
        details = "、".join(
            f"句 {item['sentence_id']} 为 {float(item['speaking_rate_cpm']):.1f} 字/分钟"
            for item in out_of_range_rates[:8]
        )
        issues.append(
            _issue(
                "NARRATION_SPEAKING_RATE_OUT_OF_RANGE",
                "warning" if mode is not None and gate_mode == "warn" else "error",
                f"有 {len(out_of_range_rates)} 个 TTS 播音单元超出专业新闻播报目标 "
                f"{minimum_speaking_rate_cpm:.0f}–{maximum_speaking_rate_cpm:.0f} 字/分钟：{details}。",
            )
        )
    total_spoken_units = sum(
        timing.spoken_unit_count or 0
        for timing in timings
        if timing.audio_kind == "tts"
    )
    measured_tts_duration = sum(
        timing.duration
        for timing in timings
        if timing.audio_kind == "tts" and timing.spoken_unit_count is not None
    )
    overall_speaking_rate = (
        total_spoken_units * 60.0 / measured_tts_duration
        if total_spoken_units and measured_tts_duration > 0
        else None
    )
    voice_profile_ids = {
        timing.voice_profile_id
        for timing in timings
        if timing.audio_kind == "tts" and timing.voice_profile_id
    }
    tts_session_ids = {
        timing.tts_group_id
        for timing in timings
        if timing.audio_kind == "tts" and timing.tts_group_id
    }
    if len(voice_profile_ids) > 1:
        issues.append(
            _issue(
                "NARRATION_VOICE_PROFILE_INCONSISTENT",
                "error",
                f"同一成片使用了 {len(voice_profile_ids)} 套 TTS 音色配置，无法保证音色一致。",
            )
        )
    if len(tts_session_ids) > 1:
        issues.append(
            _issue(
                "NARRATION_TTS_SESSION_FRAGMENTED",
                "warning",
                f"同一成片的 TTS 来自 {len(tts_session_ids)} 个独立合成会话，跨句音色与韵律可能漂移。",
            )
        )

    resolved_media_path = media_path or task_dir / "final.mp4"
    media_metrics = _media_quality_metrics(resolved_media_path)
    sentence_loudness = _sentence_loudness_metrics(
        resolved_media_path, timings, **({"include_recorded": True} if mode is not None else {}),
    )
    if not media_metrics:
        issues.append(
            _issue(
                "MEDIA_QUALITY_UNMEASURABLE",
                "error",
                "无法测量成片时长、静音、响度或真峰值，已拒绝将未知质量视为通过。",
            )
        )
    sentence_loudness_values = [
        float(item["integrated_lufs"])
        for item in sentence_loudness
        if isinstance(item.get("integrated_lufs"), (int, float))
    ]
    sentence_loudness_spread = (
        max(sentence_loudness_values) - min(sentence_loudness_values)
        if len(sentence_loudness_values) >= 2
        else 0.0
    )
    if sentence_loudness_spread > maximum_loudness_spread_lu:
        issues.append(
            _issue(
                "NARRATION_LOUDNESS_INCONSISTENT",
                "warning",
                f"逐句 TTS 响度跨度 {sentence_loudness_spread:.1f} LU，"
                f"超过 {maximum_loudness_spread_lu:.1f} LU。",
            )
        )

    edl = _load_optional_edl(edl_path or task_dir / "edl.json")
    generated_disclosure_metrics = _validate_generated_media_disclosure(
        task_dir,
        edl,
        issues,
    )
    freeze_pads = [
        float(clip.freeze_pad or 0.0)
        for item in edl
        for clip in item.clips
        if clip.freeze_pad
    ]
    clip_durations = [
        float(clip.out_time - clip.in_time + (clip.freeze_pad or 0.0))
        for item in edl
        if mode is None or item.sentence_id in narration_ids
        for clip in item.clips
    ]
    excessive_freeze_count = sum(value > MAX_FREEZE_PAD_SECONDS for value in freeze_pads)
    excessive_clip_count = sum(value > MAX_VISUAL_CLIP_SECONDS for value in clip_durations)
    if excessive_freeze_count:
        issues.append(
            _issue(
                "FREEZE_PAD_EXCESSIVE",
                "error",
                f"有 {excessive_freeze_count} 个镜头定格超过 {MAX_FREEZE_PAD_SECONDS:.1f} 秒，"
                f"最长 {max(freeze_pads):.3f} 秒。",
            )
        )
    if excessive_clip_count:
        issues.append(
            _issue(
                "VISUAL_CLIP_TOO_LONG",
                "warning",
                f"有 {excessive_clip_count} 个视觉片段超过 {MAX_VISUAL_CLIP_SECONDS:.1f} 秒，"
                f"最长 {max(clip_durations):.3f} 秒。",
            )
        )
    sentence_loudness_outliers = [
        item
        for item in sentence_loudness
        if isinstance(item.get("integrated_lufs"), (int, float))
        and abs(float(item["integrated_lufs"]) - target_loudness_lufs) > 2.0
    ]
    if sentence_loudness_outliers:
        issues.append(
            _issue(
                "NARRATION_SENTENCE_LOUDNESS_OUT_OF_RANGE",
                "warning",
                f"有 {len(sentence_loudness_outliers)} 个 TTS 播音单元超出逐句目标 "
                f"{target_loudness_lufs:.1f}±2 LUFS。",
            )
        )
    duration = float(media_metrics.get("duration_seconds") or 0.0)
    silence_total = float(media_metrics.get("total_silence_seconds") or 0.0)
    if duration > 0 and silence_total / duration > 0.12:
        issues.append(
            _issue(
                "EXCESSIVE_AUDIO_SILENCE",
                "warning",
                f"检测静音 {silence_total:.3f}s，占成片 {silence_total / duration:.1%}。",
            )
        )
    integrated_lufs = media_metrics.get("integrated_lufs")
    mode_manifest = task_dir / "production_mode.json"
    v2_audio = False
    if mode is not None and mode_manifest.is_file():
        v2_audio = json.loads(mode_manifest.read_text(encoding="utf-8")).get("media_contract") == "approved-v2-m0-20260929"
    loudness_tolerance = 1.0 if v2_audio else 2.0
    if isinstance(integrated_lufs, float) and not (
        target_loudness_lufs - loudness_tolerance <= integrated_lufs <= target_loudness_lufs + loudness_tolerance
    ):
        issues.append(
            _issue(
                "LOUDNESS_OUT_OF_RANGE",
                "warning",
                f"综合响度 {integrated_lufs:.1f} LUFS，不在目标 {target_loudness_lufs:.1f}±{loudness_tolerance:g} LUFS。",
            )
        )
    true_peak_dbfs = media_metrics.get("true_peak_dbfs")
    if isinstance(true_peak_dbfs, float) and true_peak_dbfs > maximum_true_peak_dbfs:
        issues.append(
            _issue(
                "TRUE_PEAK_TOO_HIGH",
                "warning",
                f"成片 True Peak 为 {true_peak_dbfs:.1f} dBFS，高于上限 {maximum_true_peak_dbfs:.1f} dBFS。",
            )
        )

    if mode is not None:
        rows = mode_rows if mode_rows is not None else [
            {"idx": item.sentence_id, "kind": item.kind, "text": item.text,
             "source": item.source.model_dump(mode="json") if item.source else None}
            for item in all_plan
        ]
        issues.extend(evaluate_mode_checks(
            rows, list(speakers), mode, preferences or {}, gate_mode, jumpcuts,
        ))
        # This is a human confirmation request, not an automated fact verdict.
        people = [person.name.strip() for person in speakers if person.name.strip()]
        fact_rows = [
            (position + 1, item.sentence_id)
            for position, item in enumerate(all_plan)
            if re.search(r"\d|[零一二三四五六七八九十百千万亿]+[年月日元人]|主办|协办|承办|先生|女士|主任|经理|负责人|师傅", item.text)
            or any(name in item.text or (item.source and name in item.source.asr_text) for name in people)
        ]
        if fact_rows:
            listed = "、".join(str(position) for position, _ in fact_rows[:3])
            issues.append({**_issue(
                "FACT_CHECK", "warning",
                f"请核对稿子里的数字、人名、日期（问过当事人 / 看过公告）——第 {listed} 句"
                + (f"等共 {len(fact_rows)} 句。" if len(fact_rows) > 3 else "。"),
                sentence_id=fact_rows[0][1],
            ), "level": 1, "action": "去看"})
        mode_manifest = task_dir / "production_mode.json"
        fallback = json.loads(mode_manifest.read_text(encoding="utf-8")).get("broll_fallback") if mode_manifest.is_file() else None
        if fallback:
            from .mode_pipeline import BROLL_FALLBACK_TEXT
            issues.append({**_issue("BROLL_FALLBACK", "warning", BROLL_FALLBACK_TEXT.get(fallback, "空镜不够，部分旁白画面用了替代片段；请看看画面是否合适。")),
                           "level": 1, "action": "去看"})
        measured_ids = {value["sentence_id"] for value in sentence_loudness}
        if {t.sentence_id for t in timings} - measured_ids:
            issues.append(_issue("MODE_UNIT_LOUDNESS_UNMEASURABLE", "error", "部分原声或旁白单元无法测量响度；不视为通过。"))
        for value in sentence_loudness:
            if abs(value["integrated_lufs"] - target_loudness_lufs) > loudness_tolerance or value.get("true_peak_dbfs", 0) > maximum_true_peak_dbfs:
                issues.append(_issue("MODE_UNIT_AUDIO_OUT_OF_RANGE", "error", f"此音频单元未达到 -20±{loudness_tolerance:g} LUFS / -3 dBTP。", sentence_id=value["sentence_id"]))
        for issue in issues:
            if issue["code"] in {"LOUDNESS_OUT_OF_RANGE", "TRUE_PEAK_TOO_HIGH"}:
                issue["severity"] = "error"
            issue.setdefault("level", 0 if issue["severity"] == "error" else 1)
            issue.setdefault("action", "去看")
        caption_manifest = task_dir / "subtitle_manifest.json"
        if caption_manifest.is_file():
            caption_data = json.loads(caption_manifest.read_text(encoding="utf-8"))
            long_captions = {e["sentence_id"] for e in caption_data.get("events", [])
                             if e.get("kind") != "title" and len(e.get("text", "").split("\n")) > 2}
            for sentence_id in sorted(long_captions):
                issues.append({**_issue("CAPTION_SEGMENT_TOO_LONG", "error", "只有整段时间证据，字幕超过两行；请提供真实词时间或缩短原声，不能均分时间。", sentence_id=sentence_id), "level": 0, "action": "去看"})

    sentence_count = len(match_plan)
    report: dict[str, Any] = {
        "schema_version": 2,
        "metrics": {
            "title_detected": bool(document.title),
            "title_spoken_as_body": title_spoken,
            "narration_unit_count": sentence_count,
            "visual_beat_count": len(visual_assignments),
            "distinct_visual_shot_count": len(visual_shot_usage),
            "maximum_visual_shot_use_count": max(visual_shot_usage.values(), default=0),
            "recommended_visual_shot_use_limit": visual_shot_reuse_limit,
            "rapid_visual_reuse_count": rapid_visual_reuse_count,
            "visual_shot_usage": dict(sorted(visual_shot_usage.items())),
            "visual_group_shot_usage": dict(sorted(visual_group_shot_usage.items())),
            "multi_beat_sentence_count": multi_beat_sentence_count,
            "distinct_multi_shot_sentence_count": distinct_multi_shot_sentence_count,
            "fallback_count": fallback_count,
            "fallback_ratio": round(fallback_count / sentence_count, 6) if sentence_count else 0.0,
            "contextual_overlay_count": contextual_overlay_count,
            "low_confidence_count": low_confidence_count,
            "explicit_entity_beat_count": explicit_beat_count,
            "explicit_entity_coverage_ratio": (
                round(covered_explicit_beat_count / explicit_beat_count, 6)
                if explicit_beat_count
                else 1.0
            ),
            "word_timing_coverage_ratio": round(word_aligned_count / len(timings), 6) if timings else 0.0,
            "maximum_configured_gap_seconds": max((timing.gap_after for timing in timings), default=0.0),
            "narration_tts_audio_metrics": tts_audio_metrics,
            "narration_overall_speaking_rate_cpm": (
                round(overall_speaking_rate, 3) if overall_speaking_rate is not None else None
            ),
            "narration_speaking_rate_min_cpm": round(min(rate_values), 3) if rate_values else None,
            "narration_speaking_rate_max_cpm": round(max(rate_values), 3) if rate_values else None,
            "narration_speaking_rate_outlier_count": len(out_of_range_rates),
            # Snapshot the actual QC input/window, not measured extrema or a
            # future Settings lookup. Cached audio is checked against this
            # version's QC window; this is not a provider/acoustic guarantee.
            **({"narration_rate_target": {
                "target_cpm": target_chars_per_minute,
                "min_cpm": minimum_speaking_rate_cpm,
                "max_cpm": maximum_speaking_rate_cpm,
            }} if tts_audio_metrics and mode != "original"
                    and target_chars_per_minute is not None
               and type(target_chars_per_minute) in (int, float)
               and math.isfinite(target_chars_per_minute) else {}),
            "narration_voice_profile_count": len(voice_profile_ids),
            "narration_tts_session_count": len(tts_session_ids),
            "narration_sentence_loudness": sentence_loudness,
            "narration_loudness_spread_lu": round(sentence_loudness_spread, 3),
            "freeze_clip_count": len(freeze_pads),
            "excessive_freeze_clip_count": excessive_freeze_count,
            "total_freeze_pad_seconds": round(sum(freeze_pads), 6),
            "maximum_freeze_pad_seconds": round(max(freeze_pads), 6) if freeze_pads else 0.0,
            "maximum_visual_clip_seconds": round(max(clip_durations), 6) if clip_durations else 0.0,
            **generated_disclosure_metrics,
            **media_metrics,
            **({"audioFinal": {
                "measured": bool(media_metrics),
                "integrated_lufs": media_metrics.get("integrated_lufs"),
                "true_peak_dbfs": media_metrics.get("true_peak_dbfs"),
                "target_lufs": -20.0, "tolerance_lu": loudness_tolerance,
                "maximum_true_peak_dbfs": -3.0,
            }} if mode is not None else {}),
        },
        "blocking_issue_count": sum(issue["severity"] == "error" for issue in issues),
        "warning_count": sum(issue["severity"] == "warning" and issue.get("level") != 2 for issue in issues),
        "issues": issues,
    }
    if mode is not None:
        report["mode"] = mode
        report["checks"] = issues
        report["metrics"].update({
            "quote_unit_count": sum(item.kind == "quote" for item in all_plan),
            "mode_unit_loudness": sentence_loudness,
            "mode": mode, "check_code_aliases": MODE_CHECK_CODE_ALIASES,
            "confirmation_count": report["blocking_issue_count"] + report["warning_count"],
            "informational_count": sum(issue.get("level") == 2 for issue in issues),
            "fact_check_sentence_count": len(fact_rows),
        })
    write_json_atomic(output_path or task_dir / "quality_report.json", report)
    return report


def enforce_quality_gate(report: dict[str, Any], mode: str) -> None:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"warn", "block"}:
        raise ValueError("QUALITY_GATE_MODE 仅支持 warn 或 block。")
    blocking_count = int(report.get("blocking_issue_count", 0))
    if normalized_mode == "block" and blocking_count:
        raise RuntimeError(f"成片质量门禁发现 {blocking_count} 个阻断问题，请人工复核 quality_report.json。")


def _load_script_document(task_dir: Path) -> ScriptDocument:
    path = task_dir / "script_structure.json"
    if not path.is_file():
        raise ValueError("缺少 script_structure.json，无法执行成片质量检查。")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ScriptDocument.model_validate(payload)


def _load_optional_edl(path: Path) -> list[EDLItem]:
    if not path.is_file():
        return []
    try:
        return TypeAdapter(list[EDLItem]).validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []


def _validate_generated_media_disclosure(
    task_dir: Path,
    edl: list[EDLItem],
    issues: list[dict[str, object]],
) -> dict[str, object]:
    generated_clip_ids = {
        clip.shot_id
        for item in edl
        for clip in item.clips
        if clip.media_origin == "generated"
        or is_generated_media_path(clip.src)
    }
    if not generated_clip_ids:
        return {
            "generated_media_clip_count": 0,
            "generated_media_disclosed_shot_count": 0,
            "generated_media_disclosure_complete": True,
        }

    path = task_dir / GENERATED_MEDIA_DISCLOSURE_FILE
    try:
        payload = GeneratedMediaDisclosureManifest.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        disclosed_ids = {item.shot_id for item in payload.items}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        disclosed_ids: set[int] = set()

    disclosed_generated_ids = generated_clip_ids & disclosed_ids
    missing_ids = sorted(generated_clip_ids - disclosed_ids)
    if missing_ids:
        issues.append(
            _issue(
                "GENERATED_MEDIA_DISCLOSURE_MISSING",
                "error",
                "生成式画面缺少可审计披露清单：镜头 "
                + "、".join(str(shot_id) for shot_id in missing_ids)
                + "。",
            )
        )
    return {
        "generated_media_clip_count": len(generated_clip_ids),
        "generated_media_disclosed_shot_count": len(disclosed_generated_ids),
        "generated_media_disclosure_complete": not missing_ids,
    }


def _shot_text(shot: AnnotatedShot | None) -> str:
    if shot is None:
        return ""
    return shot.search_text or " ".join(
        part
        for part in (
            shot.description or "",
            " ".join(shot.subjects),
            " ".join(shot.actions),
            " ".join(shot.keywords),
            " ".join(shot.ocr_texts),
            " ".join(shot.entities),
            shot.source_transcript,
        )
        if part
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.lower())


def _issue(
    code: str,
    severity: QualitySeverity,
    message: str,
    *,
    sentence_id: int | None = None,
    beat_id: int | None = None,
    shot_id: int | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "sentence_id": sentence_id,
        "beat_id": beat_id,
        "shot_id": shot_id,
    }


def _media_quality_metrics(media_path: Path) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe or not media_path.is_file():
        return {}
    try:
        duration_result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            timeout=QUALITY_MEDIA_COMMAND_TIMEOUT_SECONDS,
        )
        duration = float(duration_result.stdout.strip())
        silence_result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(media_path),
                "-af",
                "silencedetect=noise=-40dB:d=0.25",
                "-f",
                "null",
                "NUL" if os.name == "nt" else "/dev/null",
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=QUALITY_MEDIA_COMMAND_TIMEOUT_SECONDS,
        )
        if silence_result.returncode != 0:
            return {}
        silence_durations = [
            float(value)
            for value in re.findall(r"silence_duration:\s*([\d.]+)", silence_result.stderr)
        ]
        loudness_result = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(media_path),
                "-filter_complex",
                "ebur128=peak=true",
                "-f",
                "null",
                "NUL" if os.name == "nt" else "/dev/null",
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=QUALITY_MEDIA_COMMAND_TIMEOUT_SECONDS,
        )
        if loudness_result.returncode != 0:
            return {}
        integrated = re.findall(r"I:\s+(-?[\d.]+) LUFS", loudness_result.stderr)
        peaks = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", loudness_result.stderr)
        if not integrated or not peaks:
            return {}
        return {
            "duration_seconds": round(duration, 3),
            "silence_count": len(silence_durations),
            "total_silence_seconds": round(sum(silence_durations), 3),
            "maximum_silence_seconds": round(max(silence_durations), 3) if silence_durations else 0.0,
            "integrated_lufs": float(integrated[-1]),
            "true_peak_dbfs": float(peaks[-1]),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _sentence_loudness_metrics(
    media_path: Path,
    timings: Sequence[SentenceTiming],
    *, include_recorded: bool = False,
) -> list[dict[str, Any]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not media_path.is_file():
        return []
    measurements: list[dict[str, Any]] = []
    for timing in timings:
        if not include_recorded and (timing.audio_kind != "tts" or timing.duration < 1.0):
            continue
        try:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-nostats",
                    "-ss",
                    f"{timing.start:.6f}",
                    "-t",
                    f"{timing.duration:.6f}",
                    "-i",
                    str(media_path),
                    "-map",
                    "0:a:0",
                    "-af",
                    "ebur128=peak=true",
                    "-f",
                    "null",
                    "NUL" if os.name == "nt" else "/dev/null",
                ],
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=QUALITY_MEDIA_COMMAND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        integrated = re.findall(r"I:\s+(-?[\d.]+) LUFS", result.stderr)
        peaks = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", result.stderr)
        if not integrated or result.returncode != 0 or (include_recorded and not peaks):
            continue
        measurements.append(
            {
                "sentence_id": timing.sentence_id,
                "integrated_lufs": float(integrated[-1]),
                **({"true_peak_dbfs": float(peaks[-1])} if include_recorded else {}),
            }
        )
    return measurements
