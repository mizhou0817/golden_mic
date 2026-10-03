from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class InstanceLockError(RuntimeError):
    """Raised when another production process owns the data-volume lock."""


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+b")
        try:
            lock_file.seek(0)
            if lock_file.read(1) != b"1":
                lock_file.seek(0)
                lock_file.write(b"1")
                lock_file.flush()
                os.fsync(lock_file.fileno())
            lock_file.seek(0)
            _lock_file(lock_file)
        except (OSError, BlockingIOError) as exc:
            lock_file.close()
            raise InstanceLockError(
                "另一个 Golden Mic 生产进程已占用数据盘；禁止多 worker 或多实例。"
            ) from exc
        self._file = lock_file

    def release(self) -> None:
        lock_file = self._file
        if lock_file is None:
            return
        self._file = None
        try:
            _unlock_file(lock_file)
        finally:
            lock_file.close()

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def _lock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
