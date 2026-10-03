import asyncio
import inspect
import time
from contextlib import AsyncExitStack, suppress
from functools import partial
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, TypeVar

from .config import Settings
from .graphics import generate_news_graphics
from .music import load_music_library, select_music_track, write_music_selection
from .asr_pipeline import SourceASRRecord, apply_sync_sound_matches, transcribe_source_audio
from .matching import (
    MatchingError,
    build_match_plan,
    extract_visual_beats,
    parse_script,
    parse_segmented_script,
    write_script_document,
    write_sentences,
)
from .media import (
    detect_shots,
    normalize_assets,
    probe_media,
    require_media_tools,
    validate_source_media,
    validate_total_source_duration,
)
from .media_input import (
    append_source_notes, processing_uploads, synthesize_student_narration,
    validate_original_asset,
)
from .models import AnnotatedShot, EDLItem, EditingPreferences, MatchPlanItem, PIPELINE_STAGE_NAMES, Sentence, SentenceTiming, Shot, UploadedAsset, VisualBeat, is_generated_media_path
from .providers.embedding import EmbeddingProvider
from .providers.asr import create_asr_provider
from .providers.generative import (
    GenerativeFillProvider,
    apply_generative_fill,
    filter_generated_media_disclosure,
    generative_fill_configured,
)
from .providers.llm import LLMProvider
from .providers.tts import create_tts_provider
from .providers.vision import VisionProvider
from .pronunciation import extract_number_expressions
from .provenance import write_pipeline_manifest
from .quality import enforce_quality_gate, generate_quality_report
from .remix import (
    REMIX_TEMP_FILES,
    build_remix_edl,
    cleanup_remix_temp_files,
    commit_remix,
    filter_match_plan,
    load_annotated_shots,
    load_match_plan,
    load_segment_manifest,
    load_source_edl,
    load_source_timings,
    prepare_remix_sources,
    select_existing_segments,
    validate_remix_selection,
    write_remix_generated_media_disclosure,
    write_remix_state,
)
from .rendering import (
    FinishOptions,
    MusicMixOptions,
    SegmentRenderOptions,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    build_edl,
    fill_clips,
    prepare_information_card_shots,
    render_final_video,
    render_from_existing_segments,
    render_replacement_segments,
)
from .reporting import generate_report
from .shot_replacement import (
    SHOT_REPLACEMENT_TEMP_FILES,
    cleanup_replacement_revision_segments,
    cleanup_shot_replacement_temp_files,
    commit_shot_replacement,
    load_shot_replacement_context,
    replace_edl_clips,
    replace_match_plan_item,
    replace_segment_manifest_item,
    write_model_list,
    write_replacement_generated_media_disclosure,
    write_shot_replacement_state,
)
from .storage import write_json_atomic, write_text_log
from .subtitles import generate_ass_subtitles
from .tts_pipeline import rebuild_narration_from_existing, synthesize_narration
from .vision_pipeline import annotate_shots


STAGE_2_ASR_WEIGHT = 0.82
STAGE_2_NORMALIZATION_WEIGHT = 1.0 - STAGE_2_ASR_WEIGHT
BlockingResultT = TypeVar("BlockingResultT")


class PipelineTask(Protocol):
    task_id: str
    task_dir: Path
    script: str
    uploads: list[UploadedAsset]
    preferences: EditingPreferences


class PipelineReporter(Protocol):
    def start_stage(self, record: PipelineTask, stage_number: int, message: str) -> None:
        ...

    def update_stage(self, record: PipelineTask, stage_number: int, fraction: float, message: str) -> None:
        ...

    def complete_stage(self, record: PipelineTask, stage_number: int, message: str) -> None:
        ...


async def run_pipeline(record: PipelineTask, reporter: PipelineReporter, settings: Settings) -> None:
    if getattr(record, "mode_contract", False):
        # Deliberately local: mode orchestration reuses selected legacy helpers,
        # but must never enter legacy auto-sync / quote-to-TTS fallback paths.
        from .mode_pipeline import run_mode_pipeline

        return await run_mode_pipeline(record, reporter, settings)
    _preferences(record)
    if (record.task_dir / "own_voice.wav").is_file():
        await _validate_pipeline_configuration(settings, student_voice=True)
    else:
        await _validate_pipeline_configuration(settings)
    write_pipeline_manifest(
        record.task_dir,
        settings,
        preferences=_preferences(record).model_dump(mode="json"),
    )
    await _run_upload_validation(record, reporter, settings)
    normalized_paths, source_asr = await _run_normalization(record, reporter, settings)
    shots = await _run_scene_detection(record, reporter, normalized_paths, settings)
    annotated_shots = await _run_vision_annotation(record, reporter, shots, source_asr, settings)
    append_source_notes(record.task_dir, annotated_shots, record.uploads)
    sentences = await _run_sentence_split(record, reporter, settings)
    match_plan = await _run_semantic_matching(record, reporter, sentences, annotated_shots, source_asr, settings)
    timings = await _run_tts_synthesis(record, reporter, sentences, match_plan, settings)
    await _run_subtitle_generation(record, reporter, timings)
    await _run_video_rendering(record, reporter, timings, match_plan, annotated_shots, settings)
    await _run_completion(record, reporter, annotated_shots, match_plan, timings, settings)


async def _validate_pipeline_configuration(settings: Settings, *, student_voice: bool = False) -> None:
    require_media_tools()
    providers: list[object] = []
    try:
        if settings.sync_sound_enabled or student_voice:
            providers.append(create_asr_provider(settings))
        providers.extend(
            [
                VisionProvider.from_settings(settings),
                EmbeddingProvider.from_settings(settings),
                LLMProvider.for_script_segmentation(settings),
                LLMProvider.from_settings(settings),
            ]
        )
        if not student_voice:
            providers.append(create_tts_provider(settings))
        for provider in providers:
            validate = getattr(provider, "validate_configuration", None)
            if callable(validate):
                validate()
    finally:
        for provider in providers:
            close = getattr(provider, "aclose", None)
            if callable(close):
                close_result = close()
                if inspect.isawaitable(close_result):
                    await close_result


async def run_remix_pipeline(
    record: PipelineTask,
    reporter: PipelineReporter,
    keep_sentence_ids: list[int],
    revision: int,
    settings: Settings,
) -> None:
    remix_started = time.perf_counter()
    cleanup_remix_temp_files(record.task_dir)
    try:
        ordered_ids = validate_remix_selection(record.task_dir, keep_sentence_ids)
        source_timings = load_source_timings(record.task_dir)
        source_edl = load_source_edl(record.task_dir)
        segment_manifest = load_segment_manifest(record.task_dir)
        full_match_plan = load_match_plan(record.task_dir)
        shots = load_annotated_shots(record.task_dir)
        filtered_plan = filter_match_plan(full_match_plan, ordered_ids)

        stage_number = 7
        started = _start_stage(record, reporter, stage_number, "正在复用逐句音频重拼旁白")
        try:
            timings = await rebuild_narration_from_existing(
                record.task_dir,
                source_timings,
                ordered_ids,
                timings_path=record.task_dir / REMIX_TEMP_FILES["timings"],
                narration_path=record.task_dir / REMIX_TEMP_FILES["narration"],
                narration_profile_path=(
                    record.task_dir / REMIX_TEMP_FILES["narration_profile"]
                ),
                enhance_speech=_preferences(record).enhance_speech,
            )
            reporter.update_stage(record, stage_number, 1.0, f"已复用 {len(timings)} 段逐句音频")
        except BaseException:
            _log_stage_abort(record, stage_number, started)
            raise
        _complete_stage(record, reporter, stage_number, started, f"旁白重拼完成，共保留 {len(timings)} 句")

        stage_number = 8
        started = _start_stage(record, reporter, stage_number, "正在重建删减版字幕")
        try:
            events = generate_ass_subtitles(
                record.task_dir,
                timings,
                output_path=record.task_dir / REMIX_TEMP_FILES["subtitles"],
                manifest_path=record.task_dir / REMIX_TEMP_FILES["subtitle_manifest"],
                frame_width=VIDEO_WIDTH,
                frame_height=VIDEO_HEIGHT,
                title=parse_script(record.script).title,
                information_card_sentence_ids={
                    item.sentence_id for item in filtered_plan if item.overlay_kind == "date"
                },
            )
            reporter.update_stage(record, stage_number, 1.0, f"删减版字幕已生成，共 {len(events)} 条")
        except BaseException:
            _log_stage_abort(record, stage_number, started)
            raise
        _complete_stage(record, reporter, stage_number, started, f"删减版字幕生成完成，共 {len(events)} 条")

        stage_number = 9
        started = _start_stage(record, reporter, stage_number, "正在复用既有片段重新合并成片")

        def render_progress(fraction: float, message: str) -> None:
            reporter.update_stage(record, stage_number, fraction, message)

        try:
            remixed_edl = build_remix_edl(
                source_edl,
                timings,
                record.task_dir / REMIX_TEMP_FILES["edl"],
            )
            write_remix_generated_media_disclosure(record.task_dir, remixed_edl)
            selected_segments = select_existing_segments(record.task_dir, segment_manifest, ordered_ids)
            reporter.update_stage(
                record,
                stage_number,
                0.1,
                f"已选择 {len(selected_segments)} 个既有视频片段，不重新切片",
            )
            await render_from_existing_segments(
                record.task_dir,
                selected_segments,
                render_progress,
                narration_path=record.task_dir / REMIX_TEMP_FILES["narration"],
                subtitles_path=record.task_dir / REMIX_TEMP_FILES["subtitles"],
                subtitle_manifest_path=record.task_dir / REMIX_TEMP_FILES["subtitle_manifest"],
                video_only_path=record.task_dir / REMIX_TEMP_FILES["video_only"],
                final_path=record.task_dir / REMIX_TEMP_FILES["final"],
                finish_options=await _build_finish_options(
                    record,
                    settings,
                    timings,
                    edl=remixed_edl,
                    graphics_output_path=record.task_dir / "graphics.remix.ass",
                ),
            )
        except BaseException:
            _log_stage_abort(record, stage_number, started)
            raise
        _complete_stage(record, reporter, stage_number, started, "既有片段重新合并与渲染完成")

        stage_number = 10
        started = _start_stage(record, reporter, stage_number, "正在提交删减版并更新匹配报告")
        try:
            quality_report = await _run_blocking_until_complete(partial(
                generate_quality_report,
                record.task_dir,
                shots,
                filtered_plan,
                timings,
                minimum_confidence=settings.quality_min_match_confidence,
                output_path=record.task_dir / REMIX_TEMP_FILES["quality"],
                media_path=record.task_dir / REMIX_TEMP_FILES["final"],
                edl_path=record.task_dir / REMIX_TEMP_FILES["edl"],
                target_chars_per_minute=_preferences(record).target_chars_per_minute,
                rate_tolerance=settings.tts_news_rate_tolerance,
                maximum_loudness_spread_lu=settings.tts_max_loudness_spread_lu,
            ))
            enforce_quality_gate(quality_report, settings.quality_gate_mode)
            report = generate_report(
                record.task_dir,
                record.task_id,
                shots,
                filtered_plan,
                timings,
                final_video_path=record.task_dir / REMIX_TEMP_FILES["final"],
                output_path=record.task_dir / REMIX_TEMP_FILES["report"],
                quality_report=quality_report,
            )
            elapsed = time.perf_counter() - remix_started
            write_remix_state(
                record.task_dir,
                revision=revision,
                keep_sentence_ids=ordered_ids,
                all_sentence_ids=[timing.sentence_id for timing in source_timings],
                elapsed_seconds=elapsed,
            )
            commit_remix(record.task_dir)
            reporter.update_stage(
                record,
                stage_number,
                1.0,
                f"删减版报告与质量检查已更新，共 {len(report.rows)} 行",
            )
        except BaseException:
            _log_stage_abort(record, stage_number, started)
            raise
        _complete_stage(
            record,
            reporter,
            stage_number,
            started,
            f"文本驱动重剪完成，第 {revision} 版共 {len(report.rows)} 句",
        )
        elapsed = time.perf_counter() - remix_started
        suffix = "（达到 30 秒目标）" if elapsed <= 30.0 else "（超过 30 秒目标，请检查机器负载）"
        write_text_log(
            record.task_dir,
            f"M7 重剪完成 revision={revision} elapsed={elapsed:.3f}s keep={ordered_ids} {suffix}",
        )
    finally:
        cleanup_remix_temp_files(record.task_dir)


async def run_shot_replacement_pipeline(
    record: PipelineTask,
    reporter: PipelineReporter,
    sentence_id: int,
    instruction: str,
    revision: int,
    settings: Settings,
) -> None:
    replacement_started = time.perf_counter()
    cleanup_shot_replacement_temp_files(record.task_dir)
    cleanup_replacement_revision_segments(record.task_dir, revision, sentence_id)
    committed = False
    try:
        context = load_shot_replacement_context(record.task_dir, sentence_id)

        stage_number = 6
        started = _start_stage(record, reporter, stage_number, "正在根据文字指令重新匹配目标句镜头")

        def matching_progress(fraction: float, message: str) -> None:
            reporter.update_stage(record, stage_number, fraction, message)

        try:
            query_sentence = Sentence(
                sentence_id=sentence_id,
                text=context.current_item.text,
                visual_beats=[VisualBeat(beat_id=0, text=instruction)],
            )
            async with EmbeddingProvider.from_settings(
                settings,
                task_dir=record.task_dir,
            ) as embedding_provider:
                async with LLMProvider.from_settings(settings) as llm_provider:
                    selected_plan = await build_match_plan(
                        record.task_dir,
                        [query_sentence],
                        context.shots,
                        embedding_provider,
                        llm_provider,
                        matching_progress,
                        top_k=settings.retrieval_top_k,
                        lexical_rescue_k=settings.retrieval_lexical_rescue_k,
                        # Interactive replacement reuses rich Vision/OCR/ASR annotations so it
                        # does not mutate the full-run video cache or wait for video embeddings.
                        video_embedding_enabled=False,
                        video_embedding_candidate_top_k=settings.video_embedding_candidate_top_k,
                        video_embedding_concurrency=settings.video_embedding_concurrency,
                        excluded_shot_ids=context.excluded_shot_ids,
                        editing_brief=_preferences(record).custom_instructions,
                        output_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["selection"],
                    )
            replacement_item = selected_plan[0].model_copy(
                update={
                    "text": context.current_item.text,
                    "sync_sound": None,
                    "replacement_instruction": instruction,
                }
            )
            updated_plan = replace_match_plan_item(context.match_plan, replacement_item)
            write_model_list(
                record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["match_plan"],
                updated_plan,
            )
        except BaseException:
            _log_stage_abort(record, stage_number, started)
            raise
        _complete_stage(
            record,
            reporter,
            stage_number,
            started,
            f"文字指令匹配完成，句子 {sentence_id + 1} 改用镜头 {replacement_item.shot_id}",
        )

        stage_number = 9
        started = _start_stage(record, reporter, stage_number, "正在仅重渲染目标句并复用其余视频片段")

        def render_progress(fraction: float, message: str) -> None:
            reporter.update_stage(record, stage_number, fraction, message)

        try:
            shots_by_id = {shot.shot_id: shot for shot in context.shots}
            replacement_shot = shots_by_id.get(replacement_item.shot_id)
            if replacement_shot is None:
                raise RuntimeError(f"换镜结果引用了不存在的镜头 {replacement_item.shot_id}。")
            replacement_candidate = next(
                (
                    candidate
                    for candidate in replacement_item.candidates
                    if candidate.shot_id == replacement_item.shot_id
                ),
                None,
            )
            replacement_preferred_in_time = next(
                (
                    beat.preferred_in_time
                    for beat in replacement_item.beat_matches
                    if beat.shot_id == replacement_item.shot_id
                    and beat.preferred_in_time is not None
                ),
                replacement_candidate.preferred_in_time if replacement_candidate else None,
            )
            replacement_clips = fill_clips(
                context.timing,
                [replacement_shot],
                preferred_in_time=replacement_preferred_in_time,
            )
            updated_edl = replace_edl_clips(context.edl, sentence_id, replacement_clips)
            write_model_list(
                record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["edl"],
                updated_edl,
                by_alias=True,
            )
            write_replacement_generated_media_disclosure(record.task_dir, updated_edl)

            replacement_segments = await render_replacement_segments(
                record.task_dir,
                replacement_clips,
                revision=revision,
                sentence_id=sentence_id,
                progress=lambda fraction, message: render_progress(0.35 * fraction, message),
                segment_options=_segment_render_options(record, settings),
            )
            updated_manifest = replace_segment_manifest_item(
                context.segment_manifest,
                sentence_id,
                replacement_segments,
                record.task_dir,
            )
            write_model_list(
                record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["segment_manifest"],
                updated_manifest,
            )
            selected_segments = select_existing_segments(
                record.task_dir,
                updated_manifest,
                [timing.sentence_id for timing in context.timings],
            )
            await render_from_existing_segments(
                record.task_dir,
                selected_segments,
                lambda fraction, message: render_progress(0.35 + 0.65 * fraction, message),
                narration_path=record.task_dir / "narration.m4a",
                subtitles_path=record.task_dir / "subs.ass",
                subtitle_manifest_path=record.task_dir / "subtitle_manifest.json",
                video_only_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["video_only"],
                final_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["final"],
                finish_options=await _build_finish_options(
                    record,
                    settings,
                    context.timings,
                    edl=updated_edl,
                    graphics_output_path=record.task_dir / "graphics.replacement.ass",
                ),
            )
        except BaseException:
            _log_stage_abort(record, stage_number, started)
            raise
        _complete_stage(
            record,
            reporter,
            stage_number,
            started,
            f"目标句镜头已重渲染，其余 {max(0, len(context.timings) - 1)} 句已复用",
        )

        stage_number = 10
        started = _start_stage(record, reporter, stage_number, "正在提交换镜成片并更新匹配报告")
        try:
            quality_report = await _run_blocking_until_complete(partial(
                generate_quality_report,
                record.task_dir,
                context.shots,
                updated_plan,
                context.timings,
                minimum_confidence=settings.quality_min_match_confidence,
                output_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["quality"],
                media_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["final"],
                edl_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["edl"],
                target_chars_per_minute=_preferences(record).target_chars_per_minute,
                rate_tolerance=settings.tts_news_rate_tolerance,
                maximum_loudness_spread_lu=settings.tts_max_loudness_spread_lu,
            ))
            enforce_quality_gate(quality_report, settings.quality_gate_mode)
            report = generate_report(
                record.task_dir,
                record.task_id,
                context.shots,
                updated_plan,
                context.timings,
                final_video_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["final"],
                output_path=record.task_dir / SHOT_REPLACEMENT_TEMP_FILES["report"],
                quality_report=quality_report,
            )
            elapsed = time.perf_counter() - replacement_started
            write_shot_replacement_state(
                record.task_dir,
                revision=revision,
                sentence_id=sentence_id,
                instruction=instruction,
                previous_shot_ids=sorted(context.excluded_shot_ids),
                replacement_shot_ids=[
                    beat.shot_id for beat in replacement_item.beat_matches
                ] or [replacement_item.shot_id],
                elapsed_seconds=elapsed,
            )
            commit_shot_replacement(record.task_dir)
            committed = True
            reporter.update_stage(
                record,
                stage_number,
                1.0,
                f"换镜成片与匹配报告已更新，共 {len(report.rows)} 行",
            )
        except BaseException:
            _log_stage_abort(record, stage_number, started)
            raise
        _complete_stage(
            record,
            reporter,
            stage_number,
            started,
            f"文字指令换镜完成，第 {revision} 版已更新句子 {sentence_id + 1}",
        )
        write_text_log(
            record.task_dir,
            f"文字指令换镜完成 revision={revision} sentence={sentence_id} "
            f"shot={replacement_item.shot_id} elapsed={time.perf_counter() - replacement_started:.3f}s",
        )
    finally:
        cleanup_shot_replacement_temp_files(record.task_dir)
        if not committed:
            cleanup_replacement_revision_segments(record.task_dir, revision, sentence_id)


async def _run_upload_validation(record: PipelineTask, reporter: PipelineReporter, settings: Settings) -> None:
    stage_number = 1
    started = _start_stage(record, reporter, stage_number, "正在复核上传文件")
    try:
        raw_dir = (record.task_dir / "raw").resolve()
        if not 1 <= len(record.uploads) <= settings.max_files:
            raise ValueError(f"上传文件数量必须为 1 到 {settings.max_files} 个。")

        total_duration = 0.0
        if sum(upload.size for upload in record.uploads) > settings.total_upload_limit_bytes:
            raise ValueError("素材总大小超过上传上限。")
        for index, upload in enumerate(record.uploads):
            resolved_path = upload.path.resolve()
            if not resolved_path.is_relative_to(raw_dir):
                raise ValueError(f"上传文件路径不安全：{upload.original_name}")
            if not resolved_path.is_file() or resolved_path.stat().st_size <= 0:
                raise ValueError(f"上传文件不存在或为空：{upload.original_name}")
            if resolved_path.stat().st_size != upload.size:
                raise ValueError(f"上传文件大小校验失败：{upload.original_name}")
            total_duration += await validate_original_asset(record.task_dir, upload, settings)
            if upload.prepared_stored_name:
                prepared = processing_uploads(record.task_dir, [upload])[0]
                probe = await probe_media(prepared.path, record.task_dir)
                validate_source_media(probe, upload.original_name)
            reporter.update_stage(
                record,
                stage_number,
                (index + 1) / len(record.uploads),
                f"上传校验 {index + 1}/{len(record.uploads)}",
            )
            validate_total_source_duration(total_duration, settings.max_total_source_duration_seconds)
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise

    _complete_stage(record, reporter, stage_number, started, f"上传校验完成，共 {len(record.uploads)} 个文件")


async def _run_normalization(
    record: PipelineTask,
    reporter: PipelineReporter,
    settings: Settings,
) -> tuple[list[Path], list[SourceASRRecord]]:
    stage_number = 2
    started = _start_stage(record, reporter, stage_number, "准备识别同期声并进行本地格式适配")

    try:
        prepared_uploads = processing_uploads(record.task_dir, record.uploads)
        if settings.sync_sound_enabled:
            asr_fraction = 0.0
            normalization_fraction = 0.0

            def asr_progress(completed: int, total: int, message: str) -> None:
                nonlocal asr_fraction
                asr_fraction = completed / total
                reporter.update_stage(
                    record,
                    stage_number,
                    STAGE_2_ASR_WEIGHT * asr_fraction
                    + STAGE_2_NORMALIZATION_WEIGHT * normalization_fraction,
                    message,
                )

            def normalize_progress(completed: int, total: int, message: str) -> None:
                nonlocal normalization_fraction
                normalization_fraction = completed / total
                reporter.update_stage(
                    record,
                    stage_number,
                    STAGE_2_ASR_WEIGHT * asr_fraction
                    + STAGE_2_NORMALIZATION_WEIGHT * normalization_fraction,
                    message,
                )

            async with create_asr_provider(settings) as asr_provider:
                write_text_log(
                    record.task_dir,
                    f"阶段 2 有界流水线启动 asr_concurrency={settings.asr_concurrency} "
                    f"vad_enabled={settings.sync_sound_vad_enabled}",
                )
                asr_task = asyncio.create_task(
                    transcribe_source_audio(
                        record.task_dir,
                        prepared_uploads,
                        asr_provider,
                        asr_progress,
                        concurrency=settings.asr_concurrency,
                        vad_enabled=settings.sync_sound_vad_enabled,
                        vad_threshold=settings.sync_sound_vad_threshold,
                        vad_min_speech_ms=settings.sync_sound_vad_min_speech_ms,
                        cache_dir=(
                            settings.effective_asr_cache_dir
                            if settings.asr_cache_enabled
                            else None
                        ),
                        cache_ttl_hours=settings.asr_cache_ttl_hours,
                        cache_max_entries=settings.asr_cache_max_entries,
                        sparse_retry_enabled=settings.asr_sparse_retry_enabled,
                    ),
                    name=f"stage2-asr-{record.task_id}",
                )
                normalization_task = asyncio.create_task(
                    normalize_assets(
                        record.task_dir,
                        prepared_uploads,
                        normalize_progress,
                    ),
                    name=f"stage2-normalize-{record.task_id}",
                )
                try:
                    await asyncio.gather(asr_task, normalization_task)
                except BaseException:
                    asr_task.cancel()
                    normalization_task.cancel()
                    await asyncio.gather(asr_task, normalization_task, return_exceptions=True)
                    raise
                source_asr = asr_task.result()
                normalized_paths = normalization_task.result()
        else:
            source_asr = []
            (record.task_dir / "asr_transcripts.json").write_text("[]", encoding="utf-8")
            reporter.update_stage(record, stage_number, 0.0, "同期声功能已关闭，正在进行本地格式适配")

            def normalize_progress(completed: int, total: int, message: str) -> None:
                reporter.update_stage(record, stage_number, completed / total, message)

            normalized_paths = await normalize_assets(
                record.task_dir,
                prepared_uploads,
                normalize_progress,
            )
        processing_message = f"本地格式适配 {len(normalized_paths)} 个文件"
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    available_asr = sum(record.status == "available" and record.transcript is not None for record in source_asr)
    vad_skipped = sum(record.status == "no_speech" for record in source_asr)
    if settings.sync_sound_enabled:
        completion_message = (
            f"同期声识别 {available_asr}/{len(record.uploads)}，VAD 跳过 {vad_skipped} 个无讲话素材，"
            f"{processing_message}"
        )
    else:
        completion_message = f"同期声功能已关闭，{processing_message}"
    _complete_stage(
        record,
        reporter,
        stage_number,
        started,
        completion_message,
    )
    return normalized_paths, source_asr


async def _run_scene_detection(
    record: PipelineTask,
    reporter: PipelineReporter,
    normalized_paths: list[Path],
    settings: Settings,
) -> list[Shot]:
    stage_number = 3
    started = _start_stage(record, reporter, stage_number, "正在使用 PySceneDetect 切分镜头")

    def progress(completed: int, total: int, message: str) -> None:
        reporter.update_stage(record, stage_number, completed / total, message)

    try:
        shots = await detect_shots(
            record.task_dir,
            normalized_paths,
            record.uploads,
            settings.max_shots,
            progress,
        )
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    _complete_stage(record, reporter, stage_number, started, f"镜头切分完成，保留 {len(shots)} 个镜头")
    return shots


async def _run_vision_annotation(
    record: PipelineTask,
    reporter: PipelineReporter,
    shots: list[Shot],
    source_asr: list[SourceASRRecord],
    settings: Settings,
) -> list[AnnotatedShot]:
    stage_number = 4
    started = _start_stage(record, reporter, stage_number, "正在提取关键帧并理解画面")

    def progress(completed: int, total: int, message: str) -> None:
        reporter.update_stage(record, stage_number, completed / total, message)

    try:
        async with VisionProvider.from_settings(settings) as provider:
            annotated_shots = await annotate_shots(
                record.task_dir,
                shots,
                provider,
                progress,
                concurrency=4,
                source_records=source_asr,
            )
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise

    available_count = sum(shot.status == "available" for shot in annotated_shots)
    _complete_stage(
        record,
        reporter,
        stage_number,
        started,
        f"画面理解完成，可用 {available_count}/{len(annotated_shots)} 个镜头",
    )
    return annotated_shots


async def _run_sentence_split(
    record: PipelineTask,
    reporter: PipelineReporter,
    settings: Settings,
) -> list[Sentence]:
    stage_number = 5
    started = _start_stage(record, reporter, stage_number, "正在调用 Kimi K3 进行新闻稿上屏断句")
    try:
        async with LLMProvider.for_script_segmentation(settings) as segmentation_provider:
            segmented_script = await segmentation_provider.segment_script(record.script)
        document = parse_segmented_script(record.script, segmented_script)
        sentences = document.sentences
        if len(sentences) > settings.max_sentences:
            raise ValueError(
                f"文稿分句数量 {len(sentences)} 超过上限 {settings.max_sentences}，"
                "请合并过短句子或缩短文稿。"
            )
        (record.task_dir / "script_segmented.txt").write_text(
            segmented_script,
            encoding="utf-8",
            newline="",
        )
        write_script_document(record.task_dir, document)
        write_sentences(record.task_dir, sentences)
        beat_count = sum(len(sentence.visual_beats) for sentence in sentences)
        title_message = "，已识别标题" if document.title else ""
        reporter.update_stage(
            record,
            stage_number,
            1.0,
            f"稿件结构化完成，共 {len(sentences)} 个播音单元、{beat_count} 个视觉节拍{title_message}",
        )
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    _complete_stage(record, reporter, stage_number, started, f"稿件结构化完成，共 {len(sentences)} 个播音单元")
    return sentences


async def _run_semantic_matching(
    record: PipelineTask,
    reporter: PipelineReporter,
    sentences: list[Sentence],
    shots: list[AnnotatedShot],
    source_asr: list[SourceASRRecord],
    settings: Settings,
) -> list[MatchPlanItem]:
    stage_number = 6
    started = _start_stage(record, reporter, stage_number, "正在进行批量向量检索与精排")

    required_beat_count = sum(
        len(sentence.visual_beats or extract_visual_beats(sentence.text))
        for sentence in sentences
    )
    available_shot_count = sum(
        shot.status == "available"
        and bool(shot.description)
        and shot.quality is not None
        for shot in shots
    )
    if required_beat_count > available_shot_count:
        error = MatchingError(
            f"视觉节拍数量 {required_beat_count} 超过可用镜头数量 {available_shot_count}，"
            f"无法保证每个镜头只使用一次（播音单元 {len(sentences)} 个）。"
            "请增加不同画面素材或缩短稿件。"
        )
        _log_stage_abort(record, stage_number, started)
        raise error

    def progress(fraction: float, message: str) -> None:
        reporter.update_stage(record, stage_number, 0.85 * fraction, message)

    try:
        async with AsyncExitStack() as stack:
            embedding_provider = await stack.enter_async_context(
                EmbeddingProvider.from_settings(settings, task_dir=record.task_dir)
            )
            llm_provider = await stack.enter_async_context(LLMProvider.from_settings(settings))
            entity_verifier = (
                await stack.enter_async_context(VisionProvider.from_settings(settings))
                if settings.entity_verification_enabled
                else None
            )
            plan = await build_match_plan(
                record.task_dir,
                sentences,
                shots,
                embedding_provider,
                llm_provider,
                progress,
                top_k=settings.retrieval_top_k,
                lexical_rescue_k=settings.retrieval_lexical_rescue_k,
                video_embedding_enabled=settings.video_embedding_enabled,
                video_embedding_candidate_top_k=settings.video_embedding_candidate_top_k,
                video_embedding_concurrency=settings.video_embedding_concurrency,
                entity_verifier=entity_verifier,
                entity_verification_max_shots=settings.entity_verification_max_shots,
                entity_verification_min_confidence=settings.entity_verification_min_confidence,
                editing_brief=_preferences(record).custom_instructions,
            )
            if _preferences(record).generative_fill:
                if generative_fill_configured(settings):
                    reporter.update_stage(record, stage_number, 0.86, "正在为无合适素材的视觉节拍生成示意画面")
                    async with GenerativeFillProvider.from_settings(settings) as generative_provider:
                        synthetic_shots, plan, filled_count = await apply_generative_fill(
                            record.task_dir,
                            sentences,
                            shots,
                            plan,
                            generative_provider,
                            max_clips=settings.generative_fill_max_clips,
                            mode=settings.generative_fill_mode,
                            clip_duration=settings.generative_fill_duration_seconds,
                        )
                    if synthetic_shots:
                        shots.extend(synthetic_shots)
                        write_json_atomic(
                            record.task_dir / "shots_annotated.json",
                            [shot.model_dump(mode="json", exclude_none=True) for shot in shots],
                        )
                        write_json_atomic(
                            record.task_dir / "match_plan.json",
                            [item.model_dump(mode="json", exclude_none=True) for item in plan],
                        )
                    write_text_log(record.task_dir, f"生成式补拍接入完成 filled={filled_count}")
                else:
                    write_text_log(record.task_dir, "用户请求生成式补拍，但运营配置或凭证未就绪，保留原兜底镜头")
            # Self-read narration must not cause source-sync matching to replace
            # either its audio or the independently selected stage-6 visuals.
            if (
                settings.sync_sound_enabled
                and not getattr(record, "mode_contract", False)
                and not (record.task_dir / "own_voice.wav").is_file()
            ):
                reporter.update_stage(record, stage_number, 0.9, "正在匹配稿件与同期声分句")
                try:
                    plan = await apply_sync_sound_matches(
                        record.task_dir,
                        sentences,
                        shots,
                        plan,
                        source_asr,
                        embedding_provider,
                        similarity_threshold=settings.sync_sound_similarity_threshold,
                        min_text_overlap=settings.sync_sound_min_text_overlap,
                        max_duration_seconds=settings.sync_sound_max_duration_seconds,
                    )
                except Exception as exc:
                    write_text_log(record.task_dir, f"同期声匹配失败，自动回退 TTS：{exc}")
                    reporter.update_stage(record, stage_number, 0.95, "同期声匹配失败，已自动回退 TTS")
            reporter.update_stage(record, stage_number, 1.0, "画面与同期声匹配完成")
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    fallback_count = sum(item.is_fallback for item in plan)
    sync_count = sum(item.sync_sound is not None for item in plan)
    _complete_stage(
        record,
        reporter,
        stage_number,
        started,
        f"语义匹配完成，共 {len(plan)} 句，同期声 {sync_count} 句，兜底 {fallback_count} 句",
    )
    return plan


async def _run_tts_synthesis(
    record: PipelineTask,
    reporter: PipelineReporter,
    sentences: list[Sentence],
    match_plan: list[MatchPlanItem],
    settings: Settings,
) -> list[SentenceTiming]:
    stage_number = 7
    started = _start_stage(record, reporter, stage_number, "正在理解全文语义并合成新闻旁白")

    def progress(completed: int, total: int, message: str) -> None:
        fraction = 0.1 + 0.8 * completed / total
        reporter.update_stage(record, stage_number, fraction, message)

    try:
        if (record.task_dir / "own_voice.wav").is_file():
            timings = await synthesize_student_narration(record.task_dir, sentences, settings, progress)
            if _preferences(record).enhance_speech:
                # The self-recorded narration adapter has already aligned/split the untouched
                # recording. Reassemble those PCM inputs, not narration.m4a;
                # do not retranscribe, retime, or change its ASR evidence.
                timings = await rebuild_narration_from_existing(
                    record.task_dir, timings, [timing.sentence_id for timing in timings],
                    timings_path=record.task_dir / "timings.json",
                    narration_path=record.task_dir / "narration.m4a",
                    narration_profile_path=record.task_dir / "narration_profile.json",
                    enhance_speech=True,
                )
            # Defensive resume protection: never let an old source sync offset
            # drive stage 9 after the self-recorded narration replaces the audio.
            for item in match_plan:
                item.sync_sound = None
            write_json_atomic(record.task_dir / "match_plan.json", [item.model_dump(mode="json") for item in match_plan])
            _complete_stage(record, reporter, stage_number, started, f"自录整篇配音对齐完成，共 {len(timings)} 句")
            return timings
        if any(extract_number_expressions(sentence.text) for sentence in sentences):
            reporter.update_stage(record, stage_number, 0.02, "正在结合整篇新闻稿规划数字读音")
            async with LLMProvider.from_settings(settings) as pronunciation_provider:
                pronunciation_plan = await pronunciation_provider.plan_pronunciations(
                    record.script,
                    sentences,
                )
            reporter.update_stage(record, stage_number, 0.1, "全文语义与数字读音规划完成")
        else:
            pronunciation_plan = []
            reporter.update_stage(record, stage_number, 0.1, "稿件不含阿拉伯数字，无需规划读音")
        async with create_tts_provider(
            settings,
            target_chars_per_minute=_preferences(record).target_chars_per_minute,
        ) as provider:
            timings = await synthesize_narration(
                record.task_dir,
                sentences,
                provider,
                progress,
                match_plan=match_plan,
                full_script=record.script,
                pronunciation_plan=pronunciation_plan,
                target_chars_per_minute=_preferences(record).target_chars_per_minute,
                rate_tolerance=settings.tts_news_rate_tolerance,
                target_lufs=settings.tts_target_lufs,
                target_lra=settings.tts_target_lra,
                true_peak_dbfs=settings.tts_true_peak_dbfs,
                enhance_speech=_preferences(record).enhance_speech,
            )
        reporter.update_stage(record, stage_number, 1.0, "旁白拼接与时长校验完成")
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    sync_count = sum(timing.audio_kind == "sync" for timing in timings)
    _complete_stage(
        record,
        reporter,
        stage_number,
        started,
        f"音频生成完成，共 {len(timings)} 句，同期声替换 {sync_count} 句",
    )
    return timings


async def _run_subtitle_generation(
    record: PipelineTask,
    reporter: PipelineReporter,
    timings: list[SentenceTiming],
) -> None:
    stage_number = 8
    started = _start_stage(record, reporter, stage_number, "正在生成新闻风格 ASS 字幕")
    try:
        events = generate_ass_subtitles(
            record.task_dir,
            timings,
            frame_width=VIDEO_WIDTH,
            frame_height=VIDEO_HEIGHT,
            title=parse_script(record.script).title,
        )
        reporter.update_stage(record, stage_number, 1.0, f"字幕生成完成，共 {len(events)} 条")
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    _complete_stage(record, reporter, stage_number, started, f"字幕生成完成，共 {len(events)} 条")


async def _run_video_rendering(
    record: PipelineTask,
    reporter: PipelineReporter,
    timings: list[SentenceTiming],
    match_plan: list[MatchPlanItem],
    shots: list[AnnotatedShot],
    settings: Settings,
) -> list[EDLItem]:
    stage_number = 9
    started = _start_stage(record, reporter, stage_number, "正在构建 EDL 并渲染视频")

    def progress(fraction: float, message: str) -> None:
        reporter.update_stage(record, stage_number, fraction, message)

    try:
        overlay_sentence_ids = await prepare_information_card_shots(
            record.task_dir,
            timings,
            match_plan,
            shots,
        )
        if overlay_sentence_ids:
            write_json_atomic(
                record.task_dir / "match_plan.json",
                [item.model_dump(mode="json", exclude_none=True) for item in match_plan],
            )
            write_json_atomic(
                record.task_dir / "shots_annotated.json",
                [shot.model_dump(mode="json", exclude_none=True) for shot in shots],
            )
            write_json_atomic(
                record.task_dir / "contextual_overlays.json",
                {
                    "schema_version": 1,
                    "overlays": [
                        {
                            "sentence_id": item.sentence_id,
                            "visual_group_id": item.visual_group_id,
                            "kind": item.overlay_kind,
                            "text": item.overlay_text or item.text,
                            "shot_id": item.shot_id,
                        }
                        for item in match_plan
                        if item.sentence_id in overlay_sentence_ids
                    ],
                },
            )
            generate_ass_subtitles(
                record.task_dir,
                timings,
                frame_width=VIDEO_WIDTH,
                frame_height=VIDEO_HEIGHT,
                title=parse_script(record.script).title,
                information_card_sentence_ids={
                    item.sentence_id for item in match_plan if item.overlay_kind == "date"
                },
            )
            reporter.update_stage(
                record,
                stage_number,
                0.03,
                f"已生成 {len(overlay_sentence_ids)} 个事实信息语境叠加层",
            )
        edl = build_edl(record.task_dir, timings, match_plan, shots)
        filter_generated_media_disclosure(record.task_dir, edl)
        reporter.update_stage(record, stage_number, 0.06, f"EDL 生成完成，共 {sum(len(item.clips) for item in edl)} 个片段")
        await render_final_video(
            record.task_dir,
            edl,
            progress,
            target_loudness_lufs=settings.tts_target_lufs,
            target_loudness_range=settings.tts_target_lra,
            maximum_true_peak_dbfs=settings.tts_true_peak_dbfs,
            segment_options=_segment_render_options(record, settings),
            finish_options=await _build_finish_options(record, settings, timings, edl=edl),
        )
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    _complete_stage(record, reporter, stage_number, started, f"视频渲染完成，共 {len(edl)} 句")
    return edl


async def _run_completion(
    record: PipelineTask,
    reporter: PipelineReporter,
    shots: list[AnnotatedShot],
    match_plan: list[MatchPlanItem],
    timings: list[SentenceTiming],
    settings: Settings,
) -> None:
    stage_number = 10
    started = _start_stage(record, reporter, stage_number, "正在校验产物并生成匹配报告")
    try:
        reporter.update_stage(record, stage_number, 0.25, "正在校验成片与镜头引用")
        prepare_remix_sources(record.task_dir)
        quality_report = await _run_blocking_until_complete(partial(
            generate_quality_report,
            record.task_dir,
            shots,
            match_plan,
            timings,
            minimum_confidence=settings.quality_min_match_confidence,
            target_chars_per_minute=_preferences(record).target_chars_per_minute,
            rate_tolerance=settings.tts_news_rate_tolerance,
            maximum_loudness_spread_lu=settings.tts_max_loudness_spread_lu,
        ))
        enforce_quality_gate(quality_report, settings.quality_gate_mode)
        report = generate_report(
            record.task_dir,
            record.task_id,
            shots,
            match_plan,
            timings,
            quality_report=quality_report,
        )
        reporter.update_stage(record, stage_number, 0.75, f"匹配报告已生成，共 {len(report.rows)} 行")
        reporter.update_stage(
            record,
            stage_number,
            0.9,
            f"质量检查完成，阻断项 {quality_report['blocking_issue_count']} 个、警告 {quality_report['warning_count']} 个",
        )
        required_files = [
            "pipeline_manifest.json",
            "asr_transcripts.json",
            "shots.json",
            "shots_annotated.json",
            "script_structure.json",
            "script_segmented.txt",
            "sentences.json",
            "match_plan.json",
            "pronunciation_plan.json",
            "timings.json",
            "subs.ass",
            "subtitle_manifest.json",
            "edl.json",
            "segment_manifest.json",
            "source_timings.json",
            "source_edl.json",
            "narration.m4a",
            "final.mp4",
            "report.json",
            "quality_report.json",
        ]
        missing = [file_name for file_name in required_files if not (record.task_dir / file_name).is_file()]
        if _match_plan_uses_generated_media(shots, match_plan):
            for generated_artifact in (
                "generated_media_disclosure.json",
                "graphics.ass",
            ):
                if not (record.task_dir / generated_artifact).is_file():
                    missing.append(generated_artifact)
        if missing:
            raise RuntimeError(f"任务完成校验发现缺失产物：{', '.join(missing)}")
        reporter.update_stage(record, stage_number, 1.0, "全部任务产物校验完成")
    except BaseException:
        _log_stage_abort(record, stage_number, started)
        raise
    _complete_stage(record, reporter, stage_number, started, f"处理完成，匹配报告共 {len(report.rows)} 行")


def _segment_render_options(
    record: PipelineTask,
    settings: Settings,
) -> SegmentRenderOptions:
    preferences = _preferences(record)
    return SegmentRenderOptions(
        color_consistency=preferences.color_consistency,
        motion=preferences.motion_effects,
        zoom_ratio=settings.motion_zoom_ratio,
    )


async def _build_finish_options(
    record: PipelineTask,
    settings: Settings,
    timings: list[SentenceTiming],
    *,
    edl: list[EDLItem] | None = None,
    graphics_output_path: Path | None = None,
) -> FinishOptions:
    preferences = _preferences(record)
    music_options: MusicMixOptions | None = None
    if preferences.background_music:
        tracks = load_music_library(settings.music_library_dir)
        mood = preferences.resolved_music_mood
        resolved_by = "explicit" if preferences.music_mood != "auto" else "tone"
        if preferences.music_mood == "auto":
            try:
                async with LLMProvider.from_settings(settings) as provider:
                    mood = await provider.classify_music_mood(record.script)
                resolved_by = "llm"
            except Exception as exc:
                write_text_log(record.task_dir, f"音乐基调模型判定失败，回退任务基调：{exc}")
        track = select_music_track(tracks, mood)
        write_music_selection(
            record.task_dir,
            mood=mood,
            track=track,
            resolved_by=resolved_by,
        )
        if track is not None:
            music_options = MusicMixOptions(
                track=track,
                mood=mood,
                bed_lufs=settings.music_bed_target_lufs,
                duck_ratio=settings.music_duck_ratio,
            )

    disclosure_intervals = _generated_media_intervals(edl or [])
    graphics_path: Path | None = None
    if preferences.news_graphics or disclosure_intervals:
        document = parse_script(record.script)
        headline = document.title or document.sentences[0].text
        topic = headline[:12]
        total_duration = max((timing.end + timing.gap_after for timing in timings), default=0.0)
        graphics_path = generate_news_graphics(
            record.task_dir,
            topic=topic if preferences.news_graphics else "",
            headline=headline if preferences.news_graphics else "",
            total_duration=total_duration,
            accent=preferences.tone,
            output_path=graphics_output_path,
            disclosure_intervals=disclosure_intervals,
        )
    elif graphics_output_path is None:
        (record.task_dir / "graphics.ass").unlink(missing_ok=True)

    return FinishOptions(
        music=music_options,
        graphics_path=graphics_path,
        fade_in=0.5 if preferences.transitions else 0.0,
        fade_out=0.6 if preferences.transitions else 0.0,
        caption_style=preferences.caption_style,
    )


def _generated_media_intervals(edl: list[EDLItem]) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for item in edl:
        cursor = item.timeline_start
        for clip in item.clips:
            duration = clip.out_time - clip.in_time + (clip.freeze_pad or 0.0)
            end = min(item.timeline_end, cursor + duration)
            if (
                clip.media_origin == "generated" or is_generated_media_path(clip.src)
            ) and end > cursor:
                intervals.append((round(cursor, 6), round(end, 6)))
            cursor = end
    return intervals


def _match_plan_uses_generated_media(
    shots: list[AnnotatedShot],
    match_plan: list[MatchPlanItem],
) -> bool:
    generated_shot_ids = {
        shot.shot_id
        for shot in shots
        if shot.media_origin == "generated"
        or shot.source_name.startswith("generated_fill_")
        or is_generated_media_path(shot.norm_path)
    }
    if not generated_shot_ids:
        return False
    return any(
        shot_id in generated_shot_ids
        for item in match_plan
        for shot_id in (
            [item.sync_sound.shot_id]
            if item.sync_sound is not None
            else [beat.shot_id for beat in item.beat_matches] or [item.shot_id]
        )
    )


async def _run_blocking_until_complete(
    function: Callable[[], BlockingResultT],
) -> BlockingResultT:
    """Run blocking work off-loop without abandoning its filesystem writes on cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        with suppress(BaseException):
            worker.result()
        raise


def _preferences(record: PipelineTask) -> EditingPreferences:
    preferences = getattr(record, "preferences", None)
    if isinstance(preferences, EditingPreferences):
        return preferences
    resolved = EditingPreferences.model_validate(preferences) if preferences is not None else EditingPreferences()
    try:
        setattr(record, "preferences", resolved)
    except (AttributeError, TypeError):
        pass
    return resolved


def _start_stage(record: PipelineTask, reporter: PipelineReporter, stage_number: int, message: str) -> float:
    stage_name = PIPELINE_STAGE_NAMES[stage_number - 1]
    reporter.start_stage(record, stage_number, message)
    write_text_log(record.task_dir, f"阶段 {stage_number} {stage_name} start")
    return time.perf_counter()


def _complete_stage(
    record: PipelineTask,
    reporter: PipelineReporter,
    stage_number: int,
    started: float,
    message: str,
) -> None:
    elapsed = time.perf_counter() - started
    stage_name = PIPELINE_STAGE_NAMES[stage_number - 1]
    reporter.complete_stage(record, stage_number, message)
    write_text_log(record.task_dir, f"阶段 {stage_number} {stage_name} done elapsed={elapsed:.3f}s")


def _log_stage_abort(record: PipelineTask, stage_number: int, started: float) -> None:
    elapsed = time.perf_counter() - started
    stage_name = PIPELINE_STAGE_NAMES[stage_number - 1]
    write_text_log(record.task_dir, f"阶段 {stage_number} {stage_name} aborted elapsed={elapsed:.3f}s")


