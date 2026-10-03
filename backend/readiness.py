import hashlib
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .operations import disk_capacity_snapshot, ensure_data_directories
from .pipeline import _validate_pipeline_configuration
from .providers import local_speech as speech_provider
from .storage import sanitize_sensitive_text


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_INDEX = PROJECT_ROOT / "frontend" / "dist" / "index.html"
FRONTEND_MANIFEST = PROJECT_ROOT / "frontend" / "dist" / "ASSET_MANIFEST.sha256"
FONT_DIRECTORY = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_LICENSE = FONT_DIRECTORY / "OFL.txt"
LEGACY_ENVIRONMENT_KEYS = {
    "VIDEO_PROCESSING_PROVIDER",
    "VOLCENGINE_MEDIAKIT_API_KEYS",
    "VOLCENGINE_MEDIAKIT_BASE_URL",
    "VOLCENGINE_MEDIAKIT_BITRATE_LEVEL",
    "VOLCENGINE_MEDIAKIT_FPS",
    "VOLCENGINE_MEDIAKIT_MAX_CONCURRENCY",
    "VOLCENGINE_MEDIAKIT_MAX_DOWNLOAD_MB",
    "VOLCENGINE_MEDIAKIT_MAX_RETRIES",
    "VOLCENGINE_MEDIAKIT_OUTPUT_DESTINATION",
    "VOLCENGINE_MEDIAKIT_POLL_INTERVAL_SECONDS",
    "VOLCENGINE_MEDIAKIT_RESOLUTION",
    "VOLCENGINE_MEDIAKIT_TASK_TIMEOUT_SECONDS",
}
_FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}
_FONT_SIGNATURES = {b"\x00\x01\x00\x00", b"OTTO", b"ttcf", b"true"}
EXPECTED_FONT_SHA256 = {
    "NotoSansSC-Variable.ttf": "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da",
}
EXPECTED_FONT_LICENSE_SHA256 = "1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9"


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    checks: dict[str, bool]
    errors: tuple[str, ...]
    local_speech: dict[str, speech_provider.LocalSpeechReadiness] | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "ready": self.ready,
            "checks": dict(self.checks),
            "errors": list(self.errors),
        }
        if self.local_speech is not None:
            result["local_speech"] = {
                name: state.as_dict() for name, state in self.local_speech.items()
            }
        return result


async def run_full_preflight(settings: Settings) -> ReadinessReport:
    checks: dict[str, bool] = {}
    errors: list[str] = []

    def execute(name: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as exc:
            checks[name] = False
            errors.append(f"{name}: {sanitize_sensitive_text(exc)}")
        else:
            checks[name] = True

    execute("frontend_dist", validate_frontend_dist)
    execute("font_asset", validate_font_asset)
    execute("data_directories", lambda: _check_data_directories(settings))
    execute("disk_capacity", lambda: _check_disk_capacity(settings))
    execute("media_capabilities", _check_media_capabilities)
    if settings.is_production:
        execute("legacy_environment", validate_no_legacy_environment)

    speech = None
    # Optional by default, including legacy production configurations. Only an
    # explicit requirement (as in the production template) gates startup.
    if settings.local_speech_required:
        speech = _required_local_speech_prerequisites(settings)
        for capability, state in speech.items():
            name = "local_speech_" + capability
            checks[name] = state.available
            errors.extend(f"{name}: {code}" for code in state.blocked_prerequisites)

        if settings.is_production or settings.local_speech_bundle_manifest_path is not None:
            # Keep optional/legacy metadata-only startup free of model reads.
            # Never pass private validator/OS/import exceptions to the sanitizer.
            try:
                from .local_speech_bundle import local_speech_bundle_integrity_error

                integrity_error = local_speech_bundle_integrity_error(
                    settings.local_speech_bundle_manifest_path,
                    settings.local_speaker_model_path,
                    settings.local_alignment_model_path,
                    license_reviewed=settings.local_speech_license_reviewed,
                )
            except Exception:
                integrity_error = "bundle_validation_failed"
            checks["local_speech_bundle_integrity"] = integrity_error is None
            if integrity_error is not None:
                errors.append("local_speech_bundle_integrity: " + integrity_error)

    try:
        await _validate_pipeline_configuration(settings)
    except Exception as exc:
        checks["provider_configuration"] = False
        errors.append(f"provider_configuration: {sanitize_sensitive_text(exc)}")
    else:
        checks["provider_configuration"] = True

    return ReadinessReport(
        ready=all(checks.values()),
        checks=checks,
        errors=tuple(errors),
        local_speech=speech,
    )


def _required_local_speech_prerequisites(
    settings: Settings,
) -> dict[str, speech_provider.LocalSpeechReadiness]:
    """Metadata-only checks: no runtime imports, model reads or inference.

    Path type + runtime discoverability + operator license acknowledgement do
    NOT establish usable/compatible weights, vocabulary or acoustic quality.
    Project only static codes, never filesystem/import exception text or paths.
    """
    states: dict[str, speech_provider.LocalSpeechReadiness] = {}
    for capability, path, runtime_license, allowed in (
        ("speaker_embedding", settings.local_speaker_model_path, "Apache-2.0", {
            "missing_runtime:sherpa_onnx", "missing_local_speaker_onnx",
            "model_license_and_language_not_reviewed",
        }),
        ("forced_alignment", settings.local_alignment_model_path, "Apache-2.0 / BSD-3-Clause", {
            "missing_runtime:torch", "missing_runtime:transformers",
            "missing_local_ctc_model_and_tokenizer", "model_license_and_language_not_reviewed",
        }),
    ):
        try:
            state = speech_provider.local_speech_readiness(
                capability, path, license_reviewed=settings.local_speech_license_reviewed,
            )
            blocked = tuple(
                code if code in allowed else "prerequisite_check_failed"
                for code in state.blocked_prerequisites
            )
            if not state.available and not blocked:
                blocked = ("prerequisite_check_failed",)
        except Exception:
            # Do not use the general exception sanitizer: arbitrary private
            # local paths/import messages are not necessarily recognized by it.
            blocked = ("prerequisite_check_failed",)
        states[capability] = speech_provider.LocalSpeechReadiness(
            available=not blocked,
            capability=capability,
            blocked_prerequisites=blocked,
            runtime_license=runtime_license,
            model_license_reviewed=settings.local_speech_license_reviewed,
            inference_verified=False,
        )
    return states


def dynamic_readiness_errors(
    settings: Settings,
    *,
    draining: bool,
    reserved_bytes: int = 0,
) -> list[str]:
    errors: list[str] = []
    if draining:
        errors.append("service_draining")
    try:
        snapshot = disk_capacity_snapshot(settings, reserved_bytes)
    except OSError:
        errors.append("data_volume_unavailable")
    else:
        if not snapshot.ready:
            errors.append("insufficient_disk_space")
    return errors


def find_bundled_noto_sans_sc_fonts() -> list[Path]:
    if not FONT_DIRECTORY.is_dir():
        return []
    return sorted(
        path
        for path in FONT_DIRECTORY.iterdir()
        if path.is_file()
        and path.suffix.lower() in _FONT_SUFFIXES
        and re.sub(r"[^a-z0-9]", "", path.stem.lower()).startswith("notosanssc")
    )


def validate_frontend_dist() -> None:
    if not FRONTEND_INDEX.is_file() or FRONTEND_INDEX.stat().st_size <= 0:
        raise RuntimeError("frontend/dist/index.html 不存在，请先完成前端生产构建。")
    if not FRONTEND_MANIFEST.is_file():
        raise RuntimeError("frontend/dist 缺少 ASSET_MANIFEST.sha256。")
    dist_root = FRONTEND_INDEX.parent.resolve()
    checked = 0
    for line in FRONTEND_MANIFEST.read_text(encoding="ascii").splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise RuntimeError("前端 SHA-256 清单格式无效。")
        expected_hash, relative_name = parts
        asset_path = (dist_root / relative_name.strip()).resolve()
        if not asset_path.is_relative_to(dist_root) or not asset_path.is_file():
            raise RuntimeError(f"前端清单引用了无效文件：{relative_name}")
        actual_hash = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash.lower():
            raise RuntimeError(f"前端制品 SHA-256 不匹配：{relative_name}")
        checked += 1
    if checked < 3:
        raise RuntimeError("前端 SHA-256 清单项目不足。")


def validate_font_asset() -> None:
    fonts = find_bundled_noto_sans_sc_fonts()
    if not fonts:
        raise RuntimeError("未随部署制品提供 Noto Sans SC 字体。")
    for font in fonts:
        expected_hash = EXPECTED_FONT_SHA256.get(font.name)
        if expected_hash is None:
            raise RuntimeError(f"存在未审计的 Noto Sans SC 字体文件：{font.name}")
        if font.stat().st_size < 100_000:
            raise RuntimeError(f"字体文件过小或无效：{font.name}")
        with font.open("rb") as font_file:
            signature = font_file.read(4)
        if signature not in _FONT_SIGNATURES:
            raise RuntimeError(f"字体文件签名无效：{font.name}")
        if hashlib.sha256(font.read_bytes()).hexdigest() != expected_hash:
            raise RuntimeError(f"字体文件 SHA-256 不匹配：{font.name}")
    if not FONT_LICENSE.is_file() or "SIL OPEN FONT LICENSE" not in FONT_LICENSE.read_text(
        encoding="utf-8",
        errors="replace",
    ).upper():
        raise RuntimeError("缺少 Noto Sans SC 的 SIL Open Font License 文本。")
    if hashlib.sha256(FONT_LICENSE.read_bytes()).hexdigest() != EXPECTED_FONT_LICENSE_SHA256:
        raise RuntimeError("Noto Sans SC 许可证 SHA-256 不匹配。")


def _check_data_directories(settings: Settings) -> None:
    ensure_data_directories(settings)
    if settings.is_production:
        temporary_value = os.environ.get("TMPDIR", "").strip()
        if not temporary_value:
            raise RuntimeError("production 模式必须显式设置 TMPDIR。")
        temporary_directory = Path(temporary_value)
        if not temporary_directory.is_absolute():
            raise RuntimeError("production 模式的 TMPDIR 必须使用绝对路径。")
        temporary_directory.mkdir(parents=True, exist_ok=True)
        data_device = settings.data_dir.stat().st_dev
        if settings.effective_asr_cache_dir.stat().st_dev != data_device:
            raise RuntimeError("ASR 缓存目录与任务目录不在同一数据盘文件系统。")
        if temporary_directory.stat().st_dev != data_device:
            raise RuntimeError("TMPDIR 与任务目录不在同一数据盘文件系统。")
    probe_path = settings.data_dir / f".readiness-{uuid4().hex}.tmp"
    try:
        probe_path.write_bytes(b"ready")
        if probe_path.read_bytes() != b"ready":
            raise OSError("数据目录写入校验失败。")
    finally:
        probe_path.unlink(missing_ok=True)


def _check_disk_capacity(settings: Settings) -> None:
    snapshot = disk_capacity_snapshot(settings)
    if not snapshot.ready:
        available_gib = snapshot.available_after_reservations_bytes / (1024**3)
        raise RuntimeError(
            f"数据盘可用空间 {available_gib:.2f} GiB 低于安全余量 "
            f"{settings.min_free_disk_gb:g} GiB。"
        )


def _check_media_capabilities() -> None:
    encoder_output = _run_media_capability_command("ffmpeg", "-hide_banner", "-encoders")
    filter_output = _run_media_capability_command("ffmpeg", "-hide_banner", "-filters")
    _run_media_capability_command("ffprobe", "-hide_banner", "-version")

    missing: list[str] = []
    if not re.search(r"\blibx264\b", encoder_output):
        missing.append("libx264 encoder")
    if not re.search(r"\baac\b", encoder_output):
        missing.append("AAC encoder")
    for filter_name in ("ass", "loudnorm", "ebur128", "silencedetect", "perspective"):
        if not re.search(rf"\b{re.escape(filter_name)}\b", filter_output):
            missing.append(f"{filter_name} filter")
    if missing:
        raise RuntimeError("FFmpeg 缺少必需能力：" + "、".join(missing))


def _run_media_capability_command(*command: str) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"无法执行 {command[0]} 能力检查：{exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} 能力检查退出码为 {result.returncode}。")
    return f"{result.stdout}\n{result.stderr}"


def validate_no_legacy_environment() -> None:
    present = {key for key in LEGACY_ENVIRONMENT_KEYS if key in os.environ}
    dotenv_path = Path.cwd() / ".env"
    if dotenv_path.is_file():
        for line in dotenv_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if match and match.group(1) in LEGACY_ENVIRONMENT_KEYS:
                present.add(match.group(1))
    if present:
        raise RuntimeError("发现已移除的 MediaKit 环境变量：" + "、".join(sorted(present)))
