from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from .production_modes import Kind, Mode, QuoteTake, SentenceInput, Speaker, JumpCut


PIPELINE_STAGE_NAMES = [
    "上传校验",
    "同期声识别与本地格式适配",
    "镜头切分",
    "画面理解",
    "文稿分句",
    "语义匹配",
    "配音与同期声",
    "字幕生成",
    "视频渲染",
    "完成",
]


class TaskState(str, Enum):
    draft = "draft"
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class StageState(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class UploadedAsset(BaseModel):
    original_name: str
    stored_name: str
    path: Path
    size: int
    content_type: str
    upload_id: str | None = None
    note: str = Field(default="", max_length=20)
    trim_start: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    trim_end: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    prepared_stored_name: str | None = None
    source_duration_seconds: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)


class Shot(BaseModel):
    shot_id: int
    source_index: int
    source_scene_index: int
    source_name: str
    norm_path: str
    start: float
    end: float
    duration: float


class VisionQuality(BaseModel):
    sharp: float = Field(ge=0.0, le=1.0)
    bright: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


class VisionAnnotation(BaseModel):
    description: str = Field(min_length=1, max_length=40)
    scene_type: Literal["indoor", "outdoor", "unknown"]
    subjects: list[str]
    actions: list[str]
    keywords: list[str] = Field(min_length=3, max_length=6)
    ocr_texts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    quality: VisionQuality

    model_config = ConfigDict(extra="forbid")


class VisualEntityVerification(BaseModel):
    verified: bool
    confidence: float = Field(ge=0.0, le=1.0)
    matched_entities: list[str] = Field(default_factory=list)
    evidence: str = Field(default="", max_length=120)
    preferred_relative_time: float | None = Field(default=None, ge=0.0)

    model_config = ConfigDict(extra="forbid")


class ShotTranscriptSpan(BaseModel):
    text: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "ShotTranscriptSpan":
        if self.end <= self.start:
            raise ValueError("镜头转写片段结束时间必须晚于起始时间。")
        return self


class ShotSemanticWindow(BaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    text: str = ""

    @model_validator(mode="after")
    def validate_time_range(self) -> "ShotSemanticWindow":
        if self.end <= self.start:
            raise ValueError("镜头语义窗口结束时间必须晚于起始时间。")
        return self


class AnnotatedShot(Shot):
    thumb_path: str | None = None
    status: Literal["available", "unavailable"]
    media_origin: Literal["source", "generated"] = "source"
    description: str | None = None
    scene_type: Literal["indoor", "outdoor", "unknown"] | None = None
    subjects: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    ocr_texts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    source_transcript: str = ""
    source_transcript_spans: list[ShotTranscriptSpan] = Field(default_factory=list)
    semantic_windows: list[ShotSemanticWindow] = Field(default_factory=list)
    search_text: str = ""
    quality: VisionQuality | None = None
    error: str | None = None


def is_generated_media_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower().lstrip("./")
    return normalized.startswith("generated/")


class VisualBeat(BaseModel):
    beat_id: int = Field(ge=0)
    text: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)
    requires_entity_coverage: bool = False
    intent_type: Literal["general", "entity", "organization", "date", "abstract"] = "general"


class Sentence(BaseModel):
    sentence_id: int = Field(ge=0)
    text: str = Field(min_length=1)
    kind: Kind = "narration"
    speaker_hint: str = ""
    paragraph_index: int = Field(default=0, ge=0)
    visual_beats: list[VisualBeat] = Field(default_factory=list)
    retrieval_context: str | None = None
    visual_group_id: int | None = Field(default=None, ge=0)


class ScriptDocument(BaseModel):
    title: str | None = None
    sentences: list[Sentence] = Field(min_length=1)


CaptionStyle = Literal["news", "big", "none"]


class EditingPreferences(BaseModel):
    """User-controllable editing requirements ("按照用户的要求").

    Every default reproduces the historical fully-automatic behaviour, so tasks and
    tests that omit preferences render exactly as before. Professional enhancements
    activate only when the corresponding flag is explicitly enabled.
    """

    pacing: Literal["slow", "normal", "fast"] = "normal"
    voice: Literal["ai", "mine"] = "ai"
    tone: Literal["solemn", "neutral", "energetic"] = "neutral"
    lower_third: bool = True
    jump_cut_cover: Literal["broll", "zoom", "hard"] = "broll"
    quote_caption: Literal["asr", "none"] = "asr"
    background_music: bool = False
    music_mood: Literal["auto", "solemn", "neutral", "uplifting", "tense"] = "auto"
    motion_effects: bool = False
    transitions: bool = False
    news_graphics: bool = False
    caption_style: CaptionStyle = "news"
    enhance_speech: bool = Field(default=False, strict=True)
    color_consistency: bool = False
    generative_fill: bool = False
    custom_instructions: str = Field(default="", max_length=500)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def normalize_spoken_caption(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("quote_caption") == "spoken":
            return {**value, "quote_caption": "asr"}
        return value

    @field_validator("custom_instructions")
    @classmethod
    def validate_custom_instructions(cls, value: str) -> str:
        text = value.strip()
        if any(ord(character) < 32 and character not in "\n\t" for character in text):
            raise ValueError("剪辑要求不能包含控制字符。")
        return text

    @property
    def resolved_music_mood(self) -> Literal["solemn", "neutral", "uplifting", "tense"]:
        if self.music_mood != "auto":
            return self.music_mood
        return {"solemn": "solemn", "neutral": "neutral", "energetic": "uplifting"}[self.tone]

    @property
    def target_chars_per_minute(self) -> int:
        return {"slow": 230, "normal": 265, "fast": 290}[self.pacing]


class PronunciationReading(BaseModel):
    source: str = Field(min_length=1)
    spoken: str = Field(min_length=1, max_length=32)

    model_config = ConfigDict(extra="forbid")


class PronunciationDecision(BaseModel):
    sentence_id: int = Field(ge=0)
    readings: list[PronunciationReading] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class MatchCandidate(BaseModel):
    shot_id: int = Field(ge=0)
    similarity: float = Field(ge=-1.0, le=1.0)
    lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    combined_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    matched_terms: list[str] = Field(default_factory=list)
    description_score: float = Field(default=0.0, ge=0.0, le=1.0)
    entity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    ocr_score: float = Field(default=0.0, ge=0.0, le=1.0)
    asr_score: float = Field(default=0.0, ge=0.0, le=1.0)
    action_score: float = Field(default=0.0, ge=0.0, le=1.0)
    verified_terms: list[str] = Field(default_factory=list)
    verification_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verification_evidence: str | None = None
    preferred_in_time: float | None = Field(default=None, ge=0.0)


class SyncSoundSelection(BaseModel):
    source_index: int = Field(ge=0)
    source_media_path: str = Field(min_length=1)
    shot_id: int = Field(ge=0)
    text: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    similarity: float = Field(ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "SyncSoundSelection":
        if self.end <= self.start:
            raise ValueError("同期声结束时间必须晚于起始时间。")
        return self


class RerankDecision(BaseModel):
    sentence_id: int = Field(ge=0)
    beat_id: int = Field(default=0, ge=0)
    shot_id: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    alternates: list[int] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class BeatMatch(BaseModel):
    beat_id: int = Field(ge=0)
    text: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)
    requires_entity_coverage: bool = False
    shot_id: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    alternates: list[int] = Field(default_factory=list)
    is_fallback: bool = False
    candidates: list[MatchCandidate] = Field(min_length=1)
    intent_type: Literal["general", "entity", "organization", "date", "abstract"] = "general"
    visual_group_id: int | None = Field(default=None, ge=0)
    preferred_in_time: float | None = Field(default=None, ge=0.0)


class MatchPlanItem(BaseModel):
    sentence_id: int = Field(ge=0)
    text: str = Field(min_length=1)
    kind: Kind = "narration"
    source: QuoteTake | None = None
    alt_takes: list[QuoteTake] = Field(default_factory=list)
    trim: dict[str, float] | None = None
    to_narration: bool = False
    jumpcut_before: bool = False
    shot_id: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    alternates: list[int] = Field(default_factory=list)
    is_fallback: bool = False
    candidates: list[MatchCandidate]
    beat_matches: list[BeatMatch] = Field(default_factory=list)
    sync_sound: SyncSoundSelection | None = None
    replacement_instruction: str | None = Field(default=None, max_length=500)
    visual_group_id: int | None = Field(default=None, ge=0)
    overlay_text: str | None = Field(default=None, max_length=500)
    overlay_kind: Literal["date", "organization", "abstract"] | None = None


class TTSWordTiming(BaseModel):
    text: str = Field(min_length=1)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TTSWordTiming":
        if self.end <= self.start:
            raise ValueError("词级时间结束点必须晚于起始点。")
        return self


class SentenceTiming(BaseModel):
    sentence_id: int = Field(ge=0)
    text: str = Field(min_length=1)
    audio_path: str = Field(min_length=1)
    duration: float = Field(gt=0.0)
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    audio_kind: Literal["tts", "sync"] = "tts"
    tts_group_id: str | None = None
    voice_profile_id: str | None = None
    spoken_unit_count: int | None = Field(default=None, ge=0)
    speaking_rate_cpm: float | None = Field(default=None, gt=0.0)
    tempo_adjustment: float = Field(default=1.0, gt=0.0)
    integrated_lufs: float | None = None
    true_peak_dbfs: float | None = None
    gap_after: float = Field(default=0.12, ge=0.0)
    words: list[TTSWordTiming] = Field(default_factory=list)


class EDLClip(BaseModel):
    shot_id: int = Field(ge=0)
    src: str = Field(min_length=1)
    in_time: float = Field(ge=0.0, alias="in")
    out_time: float = Field(gt=0.0, alias="out")
    freeze_pad: float | None = Field(default=None, gt=0.0)
    evidence_aligned: bool | None = None
    media_origin: Literal["source", "generated"] = "source"

    model_config = ConfigDict(populate_by_name=True)


class EDLItem(BaseModel):
    sentence_id: int = Field(ge=0)
    clips: list[EDLClip] = Field(min_length=1)
    timeline_start: float = Field(ge=0.0)
    timeline_end: float = Field(gt=0.0)


class GeneratedMediaDisclosureItem(BaseModel):
    sentence_id: int = Field(ge=0)
    beat_id: int = Field(ge=0)
    shot_id: int = Field(ge=0)
    mode: Literal["image", "video"]
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disclosure_text: Literal["AI生成示意画面"]

    model_config = ConfigDict(extra="forbid")


class GeneratedMediaDisclosureManifest(BaseModel):
    schema_version: Literal[1]
    policy: Literal["generated_visuals_are_disclosed_and_not_evidence"]
    items: list[GeneratedMediaDisclosureItem]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_unique_shots(self) -> "GeneratedMediaDisclosureManifest":
        shot_ids = [item.shot_id for item in self.items]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("生成媒体披露清单不能包含重复镜头。")
        return self


class SegmentManifestItem(BaseModel):
    sentence_id: int = Field(ge=0)
    segments: list[str] = Field(min_length=1)


class RemixRequest(BaseModel):
    keep_sentence_ids: list[int] = Field(min_length=1)

    @field_validator("keep_sentence_ids")
    @classmethod
    def validate_sentence_ids(cls, value: list[int]) -> list[int]:
        if any(sentence_id < 0 for sentence_id in value):
            raise ValueError("sentence_id 必须大于或等于 0。")
        if len(value) != len(set(value)):
            raise ValueError("keep_sentence_ids 不能包含重复值。")
        return value


class RemixResponse(BaseModel):
    task_id: str
    revision: int = Field(ge=1)


class ShotReplacementRequest(BaseModel):
    sentence_id: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=500)

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        instruction = value.strip()
        if not instruction:
            raise ValueError("换镜指令不能为空。")
        if any(ord(character) < 32 for character in instruction):
            raise ValueError("换镜指令不能包含控制字符。")
        return instruction


class ShotReplacementResponse(BaseModel):
    task_id: str
    revision: int = Field(ge=1)


class StageSnapshot(BaseModel):
    number: int
    name: str
    weight: float = Field(default=0.1, ge=0, le=1)
    fraction: float = Field(default=0.0, ge=0, le=1)
    status: StageState = StageState.pending
    message: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float | None = Field(default=None, ge=0.0)

    @computed_field
    @property
    def n(self) -> int:
        return self.number

    @computed_field
    @property
    def msg(self) -> str:
        return self.message

    @computed_field
    @property
    def elapsed(self) -> float | None:
        return self.elapsed_seconds


class TaskCreateResponse(BaseModel):
    task_id: str
    access_token: str = Field(min_length=32, max_length=256)


class PublicLimitsResponse(BaseModel):
    max_files: int = Field(ge=1)
    max_upload_bytes: int = Field(gt=0)
    max_total_upload_bytes: int = Field(gt=0)
    max_script_length: int = Field(gt=0)
    max_video_duration_seconds: float | None = Field(default=None, gt=0.0)
    max_total_video_duration_seconds: float | None = Field(default=None, gt=0.0)
    allowed_extensions: list[str] | None = None
    quote_max_sec: float = 30.0
    quote_warn_sec: float = 20.0
    quote_min_sec: float = 1.0
    match_ok: float = 0.85
    match_low: float = 0.6
    script_soft_max: int = 3000
    rate_tolerance: float = 0.08
    pacing_cpm: dict[str, int] = Field(default_factory=lambda: {"slow": 230, "normal": 265, "fast": 290})
    chunk_size: int = 8 * 1024 * 1024
    max_shots: int = 120
    max_visual_sec: float = 6.5
    retention_hours: int = 72
    browser_submissions_per_hour: int = 2
    ip_submissions_per_hour: int = 5
    global_submissions_per_hour: int = 10
    max_concurrent_tasks: int = 1
    max_pending_tasks: int = 5
    maxPending: int = 50
    draft_ttl_hours: int = 24
    tombstone_days: int = 30
    maintenance: bool = False
    retry_same_supported: bool = False
    legacy_max_pending_tasks: int = 5


class TaskStatusResponse(BaseModel):
    title: str = "未命名视频"
    metadata_revision: int = Field(default=0, ge=0)
    version_count: int = Field(default=0, ge=0)
    expires_at: str | None = None
    task_id: str
    mode: Mode = "voiceover"
    status: TaskState
    current_stage: int | None = None
    stage_name: str | None = None
    progress: int = Field(ge=0, le=100)
    message: str = ""
    error_stage: str | None = None
    error_message: str | None = None
    revision: int = Field(default=0, ge=0)
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    total_elapsed_seconds: float | None = Field(default=None, ge=0.0)
    stages: list[StageSnapshot]
    current: int | None = None
    queue: int = 0
    errorKind: Literal["network", "transient", "shortage", "qc", "quote_missing"] | None = None
    badRows: list[int] = Field(default_factory=list)


class ReportBeatRow(BaseModel):
    beat_id: int = Field(ge=0)
    text: str
    shot_id: int = Field(ge=0)
    thumb_url: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    requires_entity_coverage: bool = False


class ReportRow(BaseModel):
    sentence_id: int
    sentence: str
    kind: Kind = "narration"
    source: QuoteTake | None = None
    alt_takes: list[QuoteTake] = Field(default_factory=list)
    trim: dict[str, float] | None = None
    to_narration: bool = False
    jumpcut_before: bool = False
    shot_id: int | None
    thumb_url: str | None
    description: str
    duration: float
    confidence: float
    is_fallback: bool
    audio_kind: Literal["tts", "sync"] = "tts"
    spoken_text: str | None = None
    visual_beats: list[ReportBeatRow] = Field(default_factory=list)
    replacement_instruction: str | None = None
    overlay_kind: Literal["date", "organization", "abstract"] | None = None
    overlay_text: str | None = None


class QualityIssue(BaseModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    sentence_id: int | None = None
    beat_id: int | None = None
    shot_id: int | None = None
    level: int | None = Field(default=None, ge=0, le=2)
    action: str = ""


class QualitySummary(BaseModel):
    blocking_issue_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)
    issues: list[QualityIssue] = Field(default_factory=list)


class ReportResponse(BaseModel):
    task_id: str
    rows: list[ReportRow]
    quality: QualitySummary | None = None
    mode: Mode = "voiceover"
    speakers: list[Speaker] = Field(default_factory=list)
    jumpcuts: list[JumpCut] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class TaskCreateRequest(BaseModel):
    """The resumable-upload contract. Unknown old fields are never persisted."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    mode: Mode = "voiceover"
    script: str = Field(min_length=1, max_length=8000)
    sentences: list[SentenceInput] = Field(default_factory=list, max_length=200)
    upload_ids: list[str] = Field(min_length=1, max_length=20)
    upload_tokens: dict[str, str] = Field(default_factory=dict)
    speakers: list[Speaker] = Field(default_factory=list, max_length=100)
    preferences: EditingPreferences = Field(default_factory=EditingPreferences)
    quality_gate_mode: Literal["warn", "block"] | None = None
    asset_options: list[dict[str, Any]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_contract(self) -> "TaskCreateRequest":
        if len(self.upload_ids) != len(set(self.upload_ids)):
            raise ValueError("素材清单有重复，请移除重复素材。")
        if len({speaker.id for speaker in self.speakers}) != len(self.speakers):
            raise ValueError("说话人编号有重复，请重新核对。")
        ids = [sentence.idx for sentence in self.sentences]
        if ids and ids != list(range(len(ids))):
            raise ValueError("句子编号必须与稿子顺序一致。")
        if self.mode == "voiceover" and any(s.kind != "narration" for s in self.sentences):
            raise ValueError("AI 配音模式只能包含旁白句。")
        if self.mode == "original":
            if any(s.kind != "quote" for s in self.sentences):
                raise ValueError("只用原声模式只能包含原声句。")
            self.preferences.voice = "ai"
            self.preferences.pacing = "normal"
            self.preferences.generative_fill = False
        if any(ord(c) < 32 and c not in "\n\r\t" for c in self.script):
            raise ValueError("稿子含有不能显示的字符，请重新粘贴纯文字。")
        return self
