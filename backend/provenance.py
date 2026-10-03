import hashlib
from pathlib import Path
from typing import Any

from .config import Settings
from .providers.embedding import (
    VOLCENGINE_CORPUS_INSTRUCTIONS,
    VOLCENGINE_QUERY_INSTRUCTIONS,
    VOLCENGINE_STS_INSTRUCTIONS,
    VOLCENGINE_VIDEO_CORPUS_INSTRUCTIONS,
    VOLCENGINE_VIDEO_QUERY_INSTRUCTIONS,
)
from .providers.llm import (
    PRONUNCIATION_SYSTEM_PROMPT,
    RERANK_SYSTEM_PROMPT,
    SCRIPT_SEGMENTATION_MODEL,
    SCRIPT_SEGMENTATION_SYSTEM_PROMPT,
)
from .providers.vision import ENTITY_VERIFICATION_SYSTEM_PROMPT, VISION_SYSTEM_PROMPT
from .storage import write_json_atomic


PIPELINE_IMPLEMENTATION_VERSION = "2026.09.28.1"
_SENSITIVE_SETTING_FRAGMENTS = ("api_key", "api_keys", "access_token", "app_key", "secret")


def write_pipeline_manifest(
    task_dir: Path,
    settings: Settings,
    *,
    preferences: dict[str, Any] | None = None,
) -> Path:
    safe_settings = {
        key: _json_value(value)
        for key, value in settings.model_dump().items()
        if not any(fragment in key.lower() for fragment in _SENSITIVE_SETTING_FRAGMENTS)
    }
    prompt_payload = {
        "vision": VISION_SYSTEM_PROMPT,
        "entity_verification": ENTITY_VERIFICATION_SYSTEM_PROMPT,
        "script_segmentation": SCRIPT_SEGMENTATION_SYSTEM_PROMPT,
        "rerank": RERANK_SYSTEM_PROMPT,
        "pronunciation": PRONUNCIATION_SYSTEM_PROMPT,
        "embedding_query": VOLCENGINE_QUERY_INSTRUCTIONS,
        "embedding_corpus": VOLCENGINE_CORPUS_INSTRUCTIONS,
        "embedding_sts": VOLCENGINE_STS_INSTRUCTIONS,
        "embedding_video_query": VOLCENGINE_VIDEO_QUERY_INSTRUCTIONS,
        "embedding_video_corpus": VOLCENGINE_VIDEO_CORPUS_INSTRUCTIONS,
    }
    source_hashes: dict[str, str] = {}
    backend_root = Path(__file__).resolve().parent
    for relative_name in (
        "config.py",
        "assignment.py",
        "models.py",
        "media.py",
        "asr_pipeline.py",
        "uploads.py",
        "production_modes.py",
        "mode_rules.json",
        "mode_pipeline.py",
        "publication.py",
        "workbench.py",
        "revisions.py",
        "vision_pipeline.py",
        "script_segmentation.py",
        "matching.py",
        "tts_pipeline.py",
        "subtitles.py",
        "rendering.py",
        "readiness.py",
        "reporting.py",
        "quality.py",
        "pipeline.py",
        "provenance.py",
        "remix.py",
        "music.py",
        "graphics.py",
        "shot_replacement.py",
        "providers/asr.py",
        "providers/embedding.py",
        "providers/embedding_concurrency.py",
        "providers/generative.py",
        "providers/llm.py",
        "providers/tts.py",
        "providers/vision.py",
    ):
        path = backend_root / relative_name
        if path.is_file():
            source_hashes[relative_name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "implementation_version": PIPELINE_IMPLEMENTATION_VERSION,
        "default_models": {"script_segmentation": SCRIPT_SEGMENTATION_MODEL},
        "settings": safe_settings,
        "editing_preferences": preferences or {},
        "prompt_sha256": {
            name: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for name, content in prompt_payload.items()
        },
        "source_sha256": source_hashes,
    }
    output_path = task_dir / "pipeline_manifest.json"
    write_json_atomic(output_path, manifest)
    return output_path


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
