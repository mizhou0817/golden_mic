import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from .config import Settings
from .models import UploadedAsset


ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".jpg", ".jpeg", ".png", ".gif"}
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "application/octet-stream",
}


def create_task_dir(settings: Settings, task_id: str) -> Path:
    task_dir = settings.data_dir / task_id
    (task_dir / "raw").mkdir(parents=True, exist_ok=False)
    return task_dir


async def save_uploads(task_dir: Path, files: list[UploadFile], settings: Settings) -> list[UploadedAsset]:
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请至少上传一个视频素材。")
    if len(files) > settings.max_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"最多只能上传 {settings.max_files} 个视频文件。")

    declared_total = sum(upload.size for upload in files if upload.size is not None)
    if declared_total > settings.total_upload_limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"全部视频文件合计不能超过 {settings.max_total_upload_mb} MB。",
        )

    raw_dir = task_dir / "raw"
    saved: list[UploadedAsset] = []
    total_size = 0
    for upload in files:
        # Preserve the submitted display name; it is never used as a disk path.
        original_name = upload.filename or "video"
        if len(original_name) > 255 or any(ord(character) < 32 for character in original_name):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="视频文件名无效或过长。")
        suffix = Path(original_name).suffix.lower()
        content_type = upload.content_type or "application/octet-stream"
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"不支持的文件后缀：{original_name}")
        if content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"不支持的文件 MIME 类型：{content_type}")

        stored_name = f"{uuid4().hex}{suffix}"
        output_path = raw_dir / stored_name
        size = 0
        with output_path.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                total_size += len(chunk)
                if size > settings.upload_limit_bytes:
                    output.close()
                    output_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=f"单个文件不能超过 {settings.max_upload_mb} MB。")
                if total_size > settings.total_upload_limit_bytes:
                    output.close()
                    output_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"全部视频文件合计不能超过 {settings.max_total_upload_mb} MB。",
                    )
                output.write(chunk)

        if size == 0:
            output_path.unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"视频文件不能为空：{original_name}")

        saved.append(
            UploadedAsset(
                original_name=original_name,
                stored_name=stored_name,
                path=output_path,
                size=size,
                content_type=content_type,
            )
        )
    return saved


def write_text_log(task_dir: Path, message: str) -> None:
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with (task_dir / "task.log").open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {sanitize_sensitive_text(message)}\n")


def sanitize_sensitive_text(message: object) -> str:
    text = str(message)
    text = re.sub(r"(?i)(Bearer\s+)[^\s,;\"']+", r"\1***", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|app[_-]?key|authorization)\s*[=:]\s*)[^\s,;\"']+",
        r"\1***",
        text,
    )
    text = re.sub(r"(?i)([?&](?:auth_key|signature|token|x-amz-signature)=)[^&\s]+", r"\1***", text)
    return text


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        for attempt in range(4):
            try:
                temporary_path.replace(path)
                return
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary_path.unlink(missing_ok=True)
