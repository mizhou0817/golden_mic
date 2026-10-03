import asyncio
import math
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

from .config import Settings


class InsufficientDiskSpaceError(RuntimeError):
    """Raised when a new upload would violate the configured disk reserve."""


@dataclass(frozen=True)
class DiskCapacitySnapshot:
    path: Path
    total_bytes: int
    used_bytes: int
    free_bytes: int
    reserved_bytes: int
    minimum_free_bytes: int

    @property
    def available_after_reservations_bytes(self) -> int:
        return max(0, self.free_bytes - self.reserved_bytes)

    @property
    def ready(self) -> bool:
        return self.available_after_reservations_bytes >= self.minimum_free_bytes


@dataclass
class UploadReservation:
    guard: "UploadCapacityGuard"
    reserved_bytes: int
    retained: bool = False
    released: bool = False

    def retain(self) -> None:
        if self.released:
            raise RuntimeError("不能保留已经释放的磁盘预留。")
        self.retained = True

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        await self.guard.release_reserved(self.reserved_bytes)


class UploadCapacityGuard:
    """Serializes upload reservations against the data volume's free space."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = asyncio.Lock()
        self._upload_slots = asyncio.Semaphore(settings.max_concurrent_uploads)
        self._reserved_bytes = 0

    @asynccontextmanager
    async def reserve(self, request_bytes: int) -> AsyncGenerator[UploadReservation, None]:
        if request_bytes <= 0:
            raise ValueError("上传请求大小必须大于 0。")
        reservation_bytes = math.ceil(
            request_bytes * self.settings.task_disk_reservation_multiplier
        )
        await self._upload_slots.acquire()
        reservation: UploadReservation | None = None
        try:
            async with self._lock:
                snapshot = disk_capacity_snapshot(self.settings, self._reserved_bytes)
                required_free = self.settings.minimum_free_disk_bytes + reservation_bytes
                if snapshot.free_bytes - self._reserved_bytes < required_free:
                    raise InsufficientDiskSpaceError(
                        "数据盘剩余空间不足，无法安全接收本次上传。"
                    )
                self._reserved_bytes += reservation_bytes
                reservation = UploadReservation(self, reservation_bytes)
            yield reservation
        finally:
            if reservation is not None and not reservation.retained:
                await reservation.release()
            self._upload_slots.release()

    async def release_reserved(self, reserved_bytes: int) -> None:
        if reserved_bytes <= 0:
            return
        async with self._lock:
            self._reserved_bytes = max(0, self._reserved_bytes - reserved_bytes)

    async def expand(self, reservation: UploadReservation, source_bytes: int) -> None:
        """Expand an owned multipart slot for already-uploaded source material."""
        if reservation.guard is not self or reservation.released or reservation.retained:
            raise RuntimeError("素材空间预留已失效。")
        required = math.ceil(source_bytes * self.settings.task_disk_reservation_multiplier)
        async with self._lock:
            extra = max(0, required - reservation.reserved_bytes)
            snapshot = disk_capacity_snapshot(self.settings, self._reserved_bytes)
            if snapshot.free_bytes - self._reserved_bytes < self.settings.minimum_free_disk_bytes + extra:
                raise InsufficientDiskSpaceError("数据盘剩余空间不足，无法安全接收本次上传。")
            self._reserved_bytes += extra
            reservation.reserved_bytes += extra

    async def snapshot(self) -> DiskCapacitySnapshot:
        async with self._lock:
            return disk_capacity_snapshot(self.settings, self._reserved_bytes)


def disk_capacity_snapshot(settings: Settings, reserved_bytes: int = 0) -> DiskCapacitySnapshot:
    data_root = _existing_capacity_path(settings.data_dir)
    usage = shutil.disk_usage(data_root)
    return DiskCapacitySnapshot(
        path=data_root,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        reserved_bytes=max(0, reserved_bytes),
        minimum_free_bytes=settings.minimum_free_disk_bytes,
    )


def ensure_data_directories(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.asr_cache_enabled:
        settings.effective_asr_cache_dir.mkdir(parents=True, exist_ok=True)


def _existing_capacity_path(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if not candidate.exists():
        raise OSError(f"无法找到数据目录所在文件系统：{path}")
    return candidate
