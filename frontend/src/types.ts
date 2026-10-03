import type { ProductionMode, QuoteTake, SentenceKind } from "./lib/productionModes";

export type TaskState = "draft" | "uploading" | "queued" | "running" | "done" | "failed" | "cancelled";
export type StageState = "pending" | "running" | "done" | "failed";

export type EditingPacing = "slow" | "normal" | "fast";
export type EditingTone = "solemn" | "neutral" | "energetic";
export type MusicMood = "auto" | "solemn" | "neutral" | "uplifting" | "tense";
export type CaptionStyle = "news" | "big" | "none";

export interface EditingPreferences {
  pacing: EditingPacing;
  tone: EditingTone;
  /** V2: news 42px / big 54px at 1080p; none leaves required graphics/disclosure. */
  caption_style?: CaptionStyle;
  /** Clock-preserving noise reduction for source speech / recordings, never TTS. */
  enhance_speech?: boolean;
  background_music: boolean;
  music_mood: MusicMood;
  motion_effects: boolean;
  transitions: boolean;
  news_graphics: boolean;
  color_consistency: boolean;
  generative_fill: boolean;
  custom_instructions: string;
  voice?: "ai" | "mine";
  lower_third?: boolean;
  jump_cut_cover?: "broll" | "zoom" | "hard";
  /** asr is a read-compatible legacy value; new v2 requests use spoken. */
  quote_caption?: "spoken" | "asr" | "none";
  target_cpm?: number;
  quality_gate_mode?: "warn" | "block";
}

export interface PublicLimits {
  max_files: number;
  max_upload_bytes: number;
  max_total_upload_bytes: number;
  max_script_length: number;
  max_video_duration_seconds?: number;
  max_total_video_duration_seconds?: number;
  allowed_extensions?: string[];
  /** Optional server facts; omission must not establish a limit or permission. */
  readonly quote_max_sec?: number;
  readonly quote_warn_sec?: number;
  readonly quote_min_sec?: number;
  readonly match_ok?: number;
  readonly match_low?: number;
  readonly script_soft_max?: number;
  readonly rate_tolerance?: number;
  readonly pacing_cpm?: Readonly<Record<EditingPacing, number>>;
  readonly chunk_size?: number;
  readonly max_shots?: number;
  readonly max_visual_sec?: number;
  readonly retention_hours?: number;
  readonly browser_submissions_per_hour?: number;
  readonly ip_submissions_per_hour?: number;
  readonly global_submissions_per_hour?: number;
  readonly max_concurrent_tasks?: number;
  readonly max_pending_tasks?: number;
  readonly maintenance?: boolean;
  readonly quality_gate_mode?: "warn" | "block";
  readonly draft_ttl_hours?: number;
  readonly retry_same_supported?: boolean;
  readonly rules_version?: number;
}

export interface StageSnapshot {
  number: number;
  name: string;
  status: StageState;
  message: string;
  started_at: string | null;
  completed_at: string | null;
  elapsed_seconds: number | null;
  /** Server-reported fractions in [0, 1], never a client-side ETA. */
  weight?: number;
  fraction?: number;
}

export interface TaskStatusResponse {
  task_id: string;
  /** Display name only; independent of the burned-in editorial headline. */
  title?: string;
  metadata_revision?: number;
  /** Actual published entries, not revision + 1. */
  version_count?: number;
  mode?: ProductionMode;
  status: TaskState;
  current_stage: number | null;
  stage_name: string | null;
  progress: number;
  message: string;
  error_stage: string | null;
  error_message: string | null;
  revision: number;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  total_elapsed_seconds: number | null;
  stages: StageSnapshot[];
  /** Compatibility aliases; retain current_stage and all existing fields. */
  current?: number | null;
  queue?: number;
  errorKind?: "network" | "transient" | "shortage" | "qc" | "quote_missing" | null;
  /** One-based sentence numbers supplied by the server. */
  badRows?: number[];
  lifecycle_v2?: boolean;
  expires_at?: string | null;
  retry_same_supported?: boolean;
  plan?: { steps: { op: string; run: number[]; state?: string }[]; idx: number } | null;
}

export interface ReportRow {
  sentence_id: number;
  sentence: string;
  shot_id: number | null;
  thumb_url: string | null;
  description: string;
  duration: number;
  confidence: number;
  is_fallback: boolean;
  audio_kind: "tts" | "sync";
  spoken_text: string | null;
  visual_beats: ReportBeatRow[];
  replacement_instruction: string | null;
  overlay_kind?: "date" | "organization" | "abstract" | null;
  overlay_text?: string | null;
  kind?: SentenceKind;
  source?: QuoteTake | null;
  alt_takes?: QuoteTake[];
  trim?: { start: number; end: number } | null;
  to_narration?: boolean;
  jumpcut_before?: boolean;
}

export interface ReportBeatRow {
  beat_id: number;
  text: string;
  shot_id: number;
  thumb_url: string;
  description: string;
  confidence: number;
  requires_entity_coverage: boolean;
}

export interface QualityIssue {
  code: string;
  severity: "warning" | "error";
  message: string;
  sentence_id: number | null;
  beat_id: number | null;
  shot_id: number | null;
}

export interface QualitySummary {
  blocking_issue_count: number;
  warning_count: number;
  metrics: Record<string, unknown>;
  issues: QualityIssue[];
}
