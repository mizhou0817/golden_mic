from functools import lru_cache
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    enable_api_docs: bool = True
    enforce_origin_check: bool = False
    anonymous_session_secret: SecretStr = SecretStr("")
    anonymous_session_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    anonymous_session_task_rate_limit_per_hour: int = Field(default=2, ge=1, le=20)
    anonymous_ip_task_rate_limit_per_hour: int = Field(default=5, ge=1, le=100)
    anonymous_global_task_rate_limit_per_hour: int = Field(default=10, ge=1, le=1000)
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    vision_provider: str = "kimi"
    vision_base_url: str = ""
    vision_api_key: str = ""
    vision_model: str = ""
    volcengine_vision_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_vision_api_keys: str = ""
    volcengine_vision_model: str = "doubao-seed-2-1-pro-260628"
    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_api_key: str = ""
    kimi_model: str = "kimi-k3"
    kimi_reasoning_effort: Literal["low", "high", "max"] = "low"
    embedding_provider: str = "volcengine"
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = ""
    volcengine_embedding_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_embedding_model: str = "doubao-embedding-vision-251215"
    volcengine_embedding_dimensions: int = Field(default=2048, ge=1)
    llm_provider: str = "kimi"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    volcengine_llm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_llm_api_keys: str = ""
    volcengine_llm_model: str = "doubao-seed-2-1-pro-260628"
    tts_provider: str = "volcengine"
    tts_voice: str = "zh-CN-YunjianNeural"
    tts_base_url: str = ""
    tts_api_key: str = ""
    tts_model: str = "tts-1"
    tts_news_target_chars_per_minute: int = Field(default=255, ge=180, le=360)
    tts_news_rate_tolerance: float = Field(default=0.08, ge=0.0, le=0.25)
    tts_provider_speech_rate: int = Field(default=0, ge=-50, le=100)
    tts_provider_loudness_rate: int = Field(default=0, ge=-50, le=100)
    tts_target_lufs: float = Field(default=-20.0, ge=-24.0, le=-16.0)
    tts_target_lra: float = Field(default=5.0, ge=1.0, le=12.0)
    tts_true_peak_dbfs: float = Field(default=-2.0, ge=-6.0, le=-1.0)
    tts_max_loudness_spread_lu: float = Field(default=2.0, ge=0.5, le=6.0)
    volcengine_tts_base_url: str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
    volcengine_tts_api_keys: str = ""
    volcengine_voice_type: str = "zh_male_m191_uranus_bigtts"
    volcengine_tts_resource_id: str = "seed-tts-2.0"
    volcengine_tts_model: str = ""
    volcengine_app_id: str = ""
    volcengine_app_key: str = ""
    volcengine_access_token: str = ""
    asr_provider: str = "volcengine"
    volcengine_asr_base_url: str = "wss://openspeech.bytedance.com/api/v3/sauc"
    volcengine_asr_cluster_id: str = ""
    volcengine_asr_resource_id: str = "volc.seedasr.sauc.duration"
    volcengine_asr_endpoint_path: str = "bigmodel_async"
    asr_concurrency: int = Field(default=4, ge=1, le=4)
    asr_cache_enabled: bool = True
    asr_cache_dir: Path = Path("data/cache/asr")
    asr_cache_ttl_hours: int = Field(default=168, ge=1)
    asr_cache_max_entries: int = Field(default=5000, ge=1)
    asr_sparse_retry_enabled: bool = True
    # Opt-in local files only; these settings never install/download models.
    local_speaker_model_path: Path | None = None
    local_alignment_model_path: Path | None = None
    local_speech_bundle_manifest_path: Path | None = None
    local_speech_license_reviewed: bool = False
    local_speech_required: bool = False
    sync_sound_enabled: bool = True
    sync_sound_vad_enabled: bool = True
    sync_sound_vad_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    sync_sound_vad_min_speech_ms: int = Field(default=32, ge=32)
    sync_sound_similarity_threshold: float = Field(default=0.88, ge=-1.0, le=1.0)
    sync_sound_min_text_overlap: float = Field(default=0.2, ge=0.0, le=1.0)
    sync_sound_max_duration_seconds: float = Field(default=15.0, gt=0.0)
    max_upload_mb: int = Field(default=500, gt=0)
    max_files: int = Field(default=20, ge=1, le=100)
    max_shots: int = Field(default=120, gt=0)
    max_sentences: int = Field(default=200, gt=0)
    retrieval_top_k: int = Field(default=10, ge=5, le=30)
    retrieval_lexical_rescue_k: int = Field(default=4, ge=0, le=10)
    entity_verification_enabled: bool = True
    entity_verification_max_shots: int = Field(default=6, ge=0, le=20)
    entity_verification_min_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    video_embedding_enabled: bool = True
    video_embedding_candidate_top_k: int = Field(default=15, ge=5, le=30)
    video_embedding_fps: float = Field(default=0.5, ge=0.2, le=5.0)
    video_embedding_max_video_tokens: int = Field(default=10240, ge=10240, le=204800)
    video_embedding_concurrency: int = Field(default=4, ge=1, le=8)
    video_embedding_global_concurrency: int = Field(default=4, ge=1, le=8)
    video_embedding_global_max_concurrency: int = Field(default=8, ge=1, le=8)
    video_embedding_min_concurrency: int = Field(default=2, ge=1, le=8)
    video_embedding_adaptive_concurrency: bool = False
    video_embedding_adaptive_success_window: int = Field(default=20, ge=5, le=200)
    video_embedding_total_timeout_seconds: float = Field(default=300.0, ge=30.0, le=900.0)
    video_embedding_connect_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    video_embedding_write_timeout_seconds: float = Field(default=120.0, ge=10.0, le=300.0)
    video_embedding_read_timeout_seconds: float = Field(default=120.0, ge=10.0, le=300.0)
    video_embedding_pool_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    quality_gate_mode: Literal["warn", "block"] = "warn"
    quality_min_match_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    music_library_dir: Path = Path("backend/assets/music")
    music_bed_target_lufs: float = Field(default=-32.0, ge=-40.0, le=-20.0)
    music_duck_ratio: float = Field(default=8.0, ge=1.0, le=20.0)
    motion_zoom_ratio: float = Field(default=0.08, ge=0.0, le=0.25)
    color_consistency_target_lufs: float = Field(default=-20.0, ge=-30.0, le=-10.0)
    generative_fill_enabled: bool = False
    generative_fill_provider: str = "volcengine"
    volcengine_gen_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_gen_api_keys: str = ""
    seedance_2_0_api_key: str = ""
    volcengine_gen_video_model: str = "doubao-seedance-2-5-260628"
    volcengine_gen_image_model: str = "doubao-seedream-3-0-t2i-250415"
    generative_fill_mode: Literal["image", "video"] = "video"
    generative_fill_resolution: Literal["480p", "720p", "1080p"] = "1080p"
    generative_fill_max_clips: int = Field(default=2, ge=0, le=10)
    generative_fill_duration_seconds: int = Field(default=5, ge=4, le=30)
    generative_fill_timeout_seconds: float = Field(default=300.0, gt=0.0, le=1800.0)
    task_ttl_hours: int = Field(default=0, ge=0)
    min_free_disk_gb: float = Field(default=5.0, ge=0.0)
    task_disk_reservation_multiplier: float = Field(default=8.0, ge=1.0)
    media_command_timeout_seconds: float = Field(default=7200.0, gt=0.0)
    max_total_upload_mb: int = Field(default=5120, gt=0)
    max_concurrent_uploads: int = Field(default=2, gt=0)
    max_source_duration_seconds_per_file: float = Field(default=1800.0, gt=0.0)
    max_total_source_duration_seconds: float = Field(default=3600.0, gt=0.0)
    max_source_width: int = Field(default=7680, gt=0)
    max_source_height: int = Field(default=4320, gt=0)
    max_source_frame_rate: float = Field(default=120.0, gt=0.0)
    max_concurrent_tasks: int = Field(default=1, gt=0)
    max_pending_tasks: int = Field(default=5, gt=0)
    shutdown_grace_seconds: float = Field(default=7500.0, gt=0.0)
    task_rate_limit_per_hour: int = Field(default=5, gt=0)
    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    data_dir: Path = Path("data/tasks")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("local_speech_bundle_manifest_path", mode="before")
    @classmethod
    def normalize_local_speech_manifest(cls, value: object) -> object:
        # Only this optional field has a documented blank/unconfigured value.
        # Do not turn a blank template value into Path('.') or relax other paths.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def v2_max_waiting_tasks(self) -> int:
        # M0 queue policy; legacy admission keeps its independently configured
        # five-task ceiling. Disk/provider/global hourly budgets are unchanged.
        return 50

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def total_upload_limit_bytes(self) -> int:
        return self.max_total_upload_mb * 1024 * 1024

    @property
    def request_body_limit_bytes(self) -> int:
        multipart_overhead = self.max_files * 1024 * 1024
        return self.total_upload_limit_bytes + multipart_overhead

    @property
    def minimum_free_disk_bytes(self) -> int:
        return int(self.min_free_disk_gb * 1024**3)

    @model_validator(mode="after")
    def validate_video_embedding_concurrency(self) -> "Settings":
        if self.max_total_upload_mb < self.max_upload_mb:
            raise ValueError("MAX_TOTAL_UPLOAD_MB 不能小于 MAX_UPLOAD_MB。")
        if self.max_total_source_duration_seconds < self.max_source_duration_seconds_per_file:
            raise ValueError(
                "MAX_TOTAL_SOURCE_DURATION_SECONDS 不能小于 "
                "MAX_SOURCE_DURATION_SECONDS_PER_FILE。"
            )
        if not (
            self.video_embedding_min_concurrency
            <= self.video_embedding_global_concurrency
            <= self.video_embedding_global_max_concurrency
        ):
            raise ValueError(
                "视频 Embedding 并发必须满足 MIN <= GLOBAL <= GLOBAL_MAX。"
            )
        if self.app_env == "production":
            if self.enable_api_docs:
                raise ValueError("production 模式必须设置 ENABLE_API_DOCS=false。")
            if not self.enforce_origin_check:
                raise ValueError("production 模式必须设置 ENFORCE_ORIGIN_CHECK=true。")
            if self.task_ttl_hours <= 0:
                raise ValueError("production 模式必须设置非零 TASK_TTL_HOURS。")
            # Generation may stop at a reviewable warning result. Publication
            # and export still enforce the independent hard QC gate.
            if not self.data_dir.is_absolute() or not self.effective_asr_cache_dir.is_absolute():
                raise ValueError("production 模式的数据目录和 ASR 缓存目录必须使用绝对路径。")
            if self.min_free_disk_gb < 50:
                raise ValueError("production 模式必须至少保留 50 GiB 数据盘安全余量。")
            if self.task_disk_reservation_multiplier < 6.0:
                raise ValueError("production 模式的任务磁盘预留倍数不能小于 6。")
            if self.shutdown_grace_seconds <= 0:
                raise ValueError("production 模式必须配置非零 SHUTDOWN_GRACE_SECONDS。")
            if self.media_command_timeout_seconds > self.shutdown_grace_seconds:
                raise ValueError(
                    "production 模式的 MEDIA_COMMAND_TIMEOUT_SECONDS 不能大于 "
                    "SHUTDOWN_GRACE_SECONDS。"
                )
            if self.max_total_upload_mb > 5120 or self.max_files > 20:
                raise ValueError("production 模式首次部署最多允许 5120 MB、20 个源文件。")
            if self.max_total_source_duration_seconds > 3600:
                raise ValueError("production 模式首次部署的素材总时长不能超过 3600 秒。")
            if (
                self.max_source_width > 7680
                or self.max_source_height > 4320
                or self.max_source_frame_rate > 120
            ):
                raise ValueError("production 模式首次部署最多允许 7680×4320、120 fps 源视频。")
            if self.max_concurrent_uploads > 1 or self.max_concurrent_tasks > 1:
                raise ValueError("production 模式首次部署只允许 1 个上传和 1 个处理任务并发。")
            if self.max_pending_tasks > 5:
                raise ValueError("production 模式首次部署的待处理任务上限不能超过 5。")
            anonymous_secret = self.anonymous_session_secret.get_secret_value()
            if len(anonymous_secret.encode("utf-8")) < 32:
                raise ValueError(
                    "production 模式必须配置至少 32 字节的 ANONYMOUS_SESSION_SECRET。"
                )
            if self.anonymous_session_task_rate_limit_per_hour > self.anonymous_ip_task_rate_limit_per_hour:
                raise ValueError(
                    "匿名会话每小时任务上限不能大于匿名 IP 每小时任务上限。"
                )
            if self.anonymous_ip_task_rate_limit_per_hour > self.anonymous_global_task_rate_limit_per_hour:
                raise ValueError(
                    "匿名 IP 每小时任务上限不能大于匿名全站每小时任务上限。"
                )
            if not self.trusted_hosts or any("*" in host for host in self.trusted_hosts):
                raise ValueError("production 模式必须显式配置 ALLOWED_HOSTS，且不能使用通配符。")
            if not self.cors_origins:
                raise ValueError("production 模式必须显式配置 FRONTEND_ORIGINS。")
            placeholder_fragments = ("change_me", "placeholder", "your-")
            if any(
                any(fragment in value.lower() for fragment in placeholder_fragments)
                for value in (*self.trusted_hosts, *self.cors_origins)
            ):
                raise ValueError("production 模式不能使用模板占位域名或 Origin。")
            for origin in self.cors_origins:
                parsed = urlsplit(origin)
                if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
                    raise ValueError(
                        "production 模式的 FRONTEND_ORIGINS 必须是无路径的 HTTPS Origin。"
                    )
            if (
                self.vision_provider.strip().lower() in {"kimi", "moonshot"}
                or self.llm_provider.strip().lower() in {"kimi", "moonshot"}
            ) and not self.kimi_api_key.strip():
                raise ValueError("production 模式使用 Kimi 时必须配置 KIMI_API_KEY。")
            provider_urls = {
                "VOLCENGINE_VISION_BASE_URL": self.volcengine_vision_base_url,
                "VOLCENGINE_EMBEDDING_BASE_URL": self.volcengine_embedding_base_url,
                "VOLCENGINE_LLM_BASE_URL": self.volcengine_llm_base_url,
                "VOLCENGINE_TTS_BASE_URL": self.volcengine_tts_base_url,
                "VOLCENGINE_ASR_BASE_URL": self.volcengine_asr_base_url,
            }
            if self.vision_provider.strip().lower() == "openai-compatible":
                provider_urls["VISION_BASE_URL"] = self.vision_base_url
            if self.vision_provider.strip().lower() in {"kimi", "moonshot"}:
                provider_urls["KIMI_BASE_URL"] = self.kimi_base_url
            if self.embedding_provider.strip().lower() == "openai-compatible":
                provider_urls["EMBED_BASE_URL"] = self.embed_base_url
            if self.llm_provider.strip().lower() == "openai-compatible":
                provider_urls["LLM_BASE_URL"] = self.llm_base_url
            if self.llm_provider.strip().lower() in {"kimi", "moonshot"}:
                provider_urls["KIMI_BASE_URL"] = self.kimi_base_url
            if self.tts_provider.strip().lower() == "openai":
                provider_urls["TTS_BASE_URL"] = self.tts_base_url
            if self.generative_fill_enabled:
                provider_urls["VOLCENGINE_GEN_BASE_URL"] = self.volcengine_gen_base_url
            for name, value in provider_urls.items():
                parsed = urlsplit(value)
                expected_scheme = "wss" if name == "VOLCENGINE_ASR_BASE_URL" else "https"
                if parsed.scheme != expected_scheme or not parsed.netloc:
                    raise ValueError(f"production 模式的 {name} 必须使用 {expected_scheme}。")
                hostname = (parsed.hostname or "").lower()
                if (
                    hostname in {"localhost", "example.com", "example.net", "example.org"}
                    or hostname.endswith(
                        (".invalid", ".example", ".example.com", ".example.net", ".example.org")
                    )
                ):
                    raise ValueError(f"production 模式的 {name} 不能使用保留示例域名。")
            sensitive_values = {
                self.vision_api_key,
                self.volcengine_vision_api_keys,
                self.kimi_api_key,
                self.embed_api_key,
                self.volcengine_llm_api_keys,
                self.llm_api_key,
                self.tts_api_key,
                self.volcengine_tts_api_keys,
                self.volcengine_app_id,
                self.volcengine_app_key,
                self.volcengine_access_token,
                self.volcengine_gen_api_keys,
                self.seedance_2_0_api_key,
                self.anonymous_session_secret.get_secret_value(),
            }
            if any(
                any(fragment in value.strip().lower() for fragment in placeholder_fragments)
                for value in sensitive_values
                if value.strip()
            ):
                raise ValueError("production 模式不能使用模板占位凭证。")
        return self

    @model_validator(mode="after")
    def absolute_data_paths(self) -> "Settings":
        # A relative DATA_DIR (the .env.example default) made task directories
        # relative while upload paths were absolute, so every
        # path.relative_to(task_dir) failed mid-pipeline. Pin both to absolute
        # paths. This runs after the production check so production still
        # rejects relative values instead of silently accepting them.
        cache = self.effective_asr_cache_dir
        self.data_dir = self.data_dir.absolute()
        self.asr_cache_dir = cache.absolute()
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def trusted_hosts(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def effective_session_start_limit(self) -> int:
        """Starts per browser session per hour, as configured (the field itself is capped at 20).

        Production used to force 2, which stopped a normal user after two works in an hour; the operator's
        ANONYMOUS_SESSION_TASK_RATE_LIMIT_PER_HOUR now applies everywhere (with the per-IP/global limits)."""
        return self.anonymous_session_task_rate_limit_per_hour

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def effective_asr_cache_dir(self) -> Path:
        if self.asr_cache_dir == Path("data/cache/asr") and self.data_dir != Path("data/tasks"):
            return self.data_dir.parent / "cache" / "asr"
        return self.asr_cache_dir


@lru_cache
def get_settings() -> Settings:
    if os.environ.get("APP_ENV", "").strip().lower() == "production":
        return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    return Settings()
