"""Display-only metadata. No editorial, provider, publication or retention writes."""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import TaskState
from .revisions import version_summaries
from .storage import sanitize_sensitive_text

if TYPE_CHECKING:
    from .task_manager import TaskManager, TaskRecord


def validate_title(value: str) -> str:
    # Code points, not UTF-8 bytes. Preserve exactly; never silently trim.
    if not 1 <= len(value) <= 40 or not value.strip() or any(
        unicodedata.category(c) in {"Cc", "Cs"} or c in "\u2028\u2029" for c in value
    ):
        raise ValueError("Invalid display title")
    return value


class MetadataInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    expected_metadata_revision: int = Field(ge=0)
    title: str

    @field_validator("title")
    @classmethod
    def title_valid(cls, value: str) -> str:
        return validate_title(value)


def title(record: TaskRecord) -> str:
    if record.display_title is not None:
        return record.display_title
    return sanitize_sensitive_text(next(
        (line.strip() for line in record.script.splitlines() if line.strip()), "未命名视频"
    ))[:120]


def expires_at(record: TaskRecord) -> datetime | None:
    if not record.lifecycle_v2:
        return None
    if record.status == TaskState.draft:
        return datetime.fromtimestamp(record.draft_context["expires_at"], timezone.utc)
    if record.status in {TaskState.done, TaskState.failed, TaskState.cancelled}:
        return (record.processing_completed_at or record.updated_at or record.created_at) + timedelta(hours=72)
    return None


def projection(record: TaskRecord) -> dict[str, Any]:
    expiry = expires_at(record)
    return {"title": title(record), "metadata_revision": record.metadata_revision,
            "version_count": len(version_summaries(record.task_dir)),
            "expires_at": expiry.isoformat() if expiry is not None else None}


def history_entry(record: TaskRecord) -> dict[str, Any]:
    return {"task_id": record.task_id, "mode": record.mode, "status": record.status.value,
            "revision": record.revision, "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(), **projection(record)}


def commit(manager: TaskManager, record: TaskRecord, payload: MetadataInput) -> dict[str, Any]:
    """Called under the existing operation lock, on the event loop; no await.

    Atomic state replacement is the commit point. Cancellation cannot interleave
    memory publication and disk replacement. In particular updated_at is NOT
    touched: it is the retention anchor for some terminal archives.
    """
    if manager.get(record.task_id) is not record:
        raise HTTPException(404, "Task not found")
    if (payload.expected_revision != record.revision
            or payload.expected_metadata_revision != record.metadata_revision):
        raise HTTPException(409, {"code": "stale_metadata", "revision": record.revision,
                                  "metadata_revision": record.metadata_revision})
    previous = record.display_title, record.metadata_revision
    record.display_title, record.metadata_revision = payload.title, record.metadata_revision + 1
    try:
        manager._persist_record(record)  # pyright: ignore[reportPrivateUsage] -- shared atomic persistence hook
    except BaseException:
        record.display_title, record.metadata_revision = previous
        raise
    return {"task_id": record.task_id, "title": record.display_title,
            "revision": record.revision, "metadata_revision": record.metadata_revision}