"""V2-only durable accepted-start ledger, never the retired classroom database.

The caller holds its async admission/capacity reservation BEFORE accept(). All
SQL transactions contain only SQL: no await, provider, callback or media I/O.
An accepted row is never refunded, even if enqueue/persistence subsequently fails.
RetrySame reads this acceptance; it never inserts another row or extends the
hourly budget. V2 terminal retention (done/failed/cancelled) is 72 hours; deletion
keeps accepted rows and retains capability-bound tombstones for 30 days.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException

from .uploads import _safe_path


def owner_digest(owner: str) -> str:
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()



def start_budget_detail(limit: int, wait_seconds: int) -> dict[str, object]:
    """A plain refusal for "too many starts this hour": how long to wait and that nothing was lost."""
    minutes = max(1, -(-wait_seconds // 60))
    return {"code": "start_budget", "retry_after": wait_seconds,
            "message": f"这一小时内开始制作的次数已经用完（每小时最多 {limit} 次）。约 {minutes} 分钟后可以再点“开始制作”；"
                       "稿件和已上传的素材都保留着，不需要重新上传。"}

class AdmissionLedger:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = _safe_path(data_dir, directory=True)
        self.root = self.data_dir / "_v2"
        _safe_path(self.root, exists=False, directory=True).mkdir(exist_ok=True)
        self.path = self.root / "admission.sqlite3"
        self._data_identity = self._identity(self.data_dir)
        self._root_identity = self._identity(self.root)
        _safe_path(self.path, exists=False)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accepted (
                    task_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    ip TEXT NOT NULL, at REAL NOT NULL, input_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS accepted_time ON accepted(at);
                CREATE TABLE IF NOT EXISTS tombstones (
                    task_id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    token_hash TEXT NOT NULL, at REAL NOT NULL, reason TEXT NOT NULL
                );
            """)
            db.commit()
        self._db_identity = self._identity(self.path)

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        info = path.stat()
        return info.st_dev, info.st_ino

    def check(self) -> None:
        for path, identity, directory in (
            (self.data_dir, self._data_identity, True),
            (self.root, self._root_identity, True),
            (self.path, self._db_identity, False),
        ):
            _safe_path(path, directory=directory)
            if self._identity(path) != identity:
                raise HTTPException(503, "V2 storage identity changed; restart required")

    def _connect(self) -> sqlite3.Connection:
        self.check()
        for suffix in ("-journal", "-wal", "-shm"):
            _safe_path(self.path.with_name(self.path.name + suffix), exists=False)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def accepted(self, task_id: str) -> bool:
        with closing(self._connect()) as db:
            return db.execute("SELECT 1 FROM accepted WHERE task_id=?", (task_id,)).fetchone() is not None

    def counts(self, owner: str, ip: str, *, now: float | None = None) -> tuple[int, int, int]:
        stamp = time.time() if now is None else now
        with closing(self._connect()) as db:
            row = db.execute("SELECT COUNT(*), COALESCE(SUM(owner=?),0), COALESCE(SUM(ip=?),0) FROM accepted WHERE at>?",
                             (owner, ip, stamp - 3600)).fetchone()
            assert row is not None
            return row[0], row[1], row[2]

    def accept(self, task_id: str, owner: str, ip: str, input_hash: str,
               *, session_limit: int = 2, ip_limit: int = 5, global_limit: int = 10,
               now: float | None = None, legacy_counts: tuple[int, int, int] = (0, 0, 0)) -> None:
        stamp = time.time() if now is None else now
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM accepted WHERE task_id=?", (task_id,)).fetchone():
                raise HTTPException(409, {"code": "already_accepted", "task_id": task_id,
                                          "message": "Read task state; never automatically repeat start"})
            counts = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(owner=?),0), COALESCE(SUM(ip=?),0) "
                "FROM accepted WHERE at>?", (owner, ip, stamp - 3600),
            ).fetchone()
            assert counts is not None
            # The caller passes the effective per-session limit (production keeps its stricter cap).
            hit = [(scope, limit) for scope, used, limit in (("global", counts[0] + legacy_counts[0], global_limit),
                                                                ("owner", counts[1] + legacy_counts[1], session_limit),
                                                                ("ip", counts[2] + legacy_counts[2], ip_limit)) if used >= limit]
            if hit:
                scope, limit = hit[0]
                where = {"global": ("", ()), "owner": (" AND owner=?", (owner,)), "ip": (" AND ip=?", (ip,))}[scope]
                oldest = db.execute(f"SELECT MIN(at) FROM accepted WHERE at>?{where[0]}", (stamp - 3600, *where[1])).fetchone()[0]
                wait = max(60, int((oldest or stamp) + 3600 - stamp) + 1)
                raise HTTPException(429, start_budget_detail(limit, wait), headers={"Retry-After": str(wait)})
            db.execute("INSERT INTO accepted VALUES (?,?,?,?,?)", (task_id, owner, ip, stamp, input_hash))

    def tombstone(self, task_id: str, owner: str, token_hash: str, reason: str,
                  *, now: float | None = None) -> None:
        stamp = time.time() if now is None else now
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO tombstones VALUES (?,?,?,?,?)",
                       (task_id, owner, token_hash, stamp, reason))
            db.execute("DELETE FROM tombstones WHERE at<=?", (stamp - 30 * 86400,))

    @classmethod
    def existing(cls, data_dir: Path) -> AdmissionLedger | None:
        """Open existing V2 storage without constructor DDL or lazy creation."""
        path = data_dir / "_v2" / "admission.sqlite3"
        if not path.is_file():
            return None
        ledger = cls.__new__(cls)
        ledger.data_dir = _safe_path(data_dir, directory=True)
        ledger.root = _safe_path(data_dir / "_v2", directory=True)
        ledger.path = _safe_path(path)
        ledger._data_identity = cls._identity(ledger.data_dir)
        ledger._root_identity = cls._identity(ledger.root)
        ledger._db_identity = cls._identity(ledger.path)
        return ledger

    def proves_gone(self, task_id: str, tokens: list[str], owner: str | None,
                    *, now: float | None = None) -> bool:
        # An explicit capability always takes precedence over owner cookies.
        if len(tokens) > 1 or (tokens and not tokens[0]):
            return False
        stamp = time.time() if now is None else now
        with closing(self._connect()) as db:
            row = db.execute("SELECT owner,token_hash FROM tombstones WHERE task_id=? AND at>?",
                             (task_id, stamp - 30 * 86400)).fetchone()
        if row is None:
            return False
        if tokens:
            return bool(row[1] and secrets.compare_digest(row[1], owner_digest(tokens[0])))
        return bool(owner and row[0] and secrets.compare_digest(row[0], owner))

    def history(self, owner: str, *, now: float | None = None) -> list[dict[str, str]]:
        stamp = time.time() if now is None else now
        if not owner:
            return []
        with closing(self._connect()) as db:
            return [{"id": row[0], "task_id": row[0], "status": "gone", "title": "未命名视频"}
                    for row in db.execute("SELECT task_id FROM tombstones WHERE owner=? AND at>? ORDER BY at DESC",
                                          (owner, stamp - 30 * 86400))]