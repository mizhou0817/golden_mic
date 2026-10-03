from __future__ import annotations

import asyncio
import math
import time
import weakref
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator


@dataclass(frozen=True)
class ConcurrencyLease:
    queue_wait_seconds: float
    limit: int
    active: int


class AdaptiveConcurrencyController:
    """Fair, event-loop-local account concurrency controller.

    Jobs are dispatched round-robin by task ID. The active limit is shared by
    every provider instance that resolves to the same account key.
    """

    def __init__(
        self,
        *,
        initial_limit: int,
        minimum_limit: int,
        maximum_limit: int,
        adaptive: bool,
        success_window: int,
        p95_regression_ratio: float = 1.2,
    ) -> None:
        if not 1 <= minimum_limit <= initial_limit <= maximum_limit:
            raise ValueError("视频 Embedding 全局并发配置无效。")
        if success_window < 1:
            raise ValueError("视频 Embedding 自适应成功窗口必须大于 0。")
        if p95_regression_ratio < 1.0:
            raise ValueError("视频 Embedding P95 回退比例不能小于 1。")
        self.minimum_limit = minimum_limit
        self.maximum_limit = maximum_limit
        self.current_limit = initial_limit
        self.adaptive = adaptive
        self.success_window = success_window
        self.p95_regression_ratio = p95_regression_ratio

        self._lock = asyncio.Lock()
        self._active = 0
        self._waiters: dict[str, deque[tuple[asyncio.Future[ConcurrencyLease], float]]] = {}
        self._task_order: deque[str] = deque()
        self._cooldown_until = 0.0
        self._cooldown_handle: asyncio.TimerHandle | None = None
        self._latencies: list[float] = []
        self._reference_p95: float | None = None
        self._evaluating_increase = False
        self._rate_limit_count = 0
        self._transient_failure_count = 0
        self._consecutive_transient_failures = 0
        self._limit_change_count = 0

    @asynccontextmanager
    async def slot(self, task_id: str) -> AsyncGenerator[ConcurrencyLease, None]:
        lease = await self.acquire(task_id)
        try:
            yield lease
        finally:
            await self.release()

    async def acquire(self, task_id: str) -> ConcurrencyLease:
        normalized_task_id = task_id.strip() or "unscoped"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConcurrencyLease] = loop.create_future()
        enqueued_at = time.monotonic()
        async with self._lock:
            queue: deque[tuple[asyncio.Future[ConcurrencyLease], float]] | None = (
                self._waiters.get(normalized_task_id)
            )
            if queue is None:
                queue = deque[tuple[asyncio.Future[ConcurrencyLease], float]]()
                self._waiters[normalized_task_id] = queue
                self._task_order.append(normalized_task_id)
            queue.append((future, enqueued_at))
            self._dispatch_locked()
        try:
            return await future
        except asyncio.CancelledError:
            granted = future.done() and not future.cancelled()
            async with self._lock:
                if not granted:
                    self._remove_waiter_locked(normalized_task_id, future)
                else:
                    self._active = max(0, self._active - 1)
                self._dispatch_locked()
            raise

    async def release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)
            self._dispatch_locked()

    async def report_success(self, latency_seconds: float) -> None:
        if not math.isfinite(latency_seconds) or latency_seconds < 0:
            return
        async with self._lock:
            self._consecutive_transient_failures = 0
            self._latencies.append(latency_seconds)
            if len(self._latencies) < self.success_window:
                return
            sample = self._latencies[-self.success_window :]
            current_p95 = _percentile(sample, 0.95)
            self._latencies.clear()
            if not self.adaptive:
                return

            if self._evaluating_increase and self._reference_p95 is not None:
                if current_p95 > self._reference_p95 * self.p95_regression_ratio:
                    new_limit = max(self.minimum_limit, self.current_limit - 1)
                    if new_limit != self.current_limit:
                        self.current_limit = new_limit
                        self._limit_change_count += 1
                    self._evaluating_increase = False
                    self._dispatch_locked()
                    return
                self._reference_p95 = current_p95
                self._evaluating_increase = False

            if self.current_limit < self.maximum_limit:
                self._reference_p95 = current_p95
                self.current_limit += 1
                self._limit_change_count += 1
                self._evaluating_increase = True
                self._dispatch_locked()

    async def report_rate_limit(self, retry_after_seconds: float | None) -> None:
        cooldown = retry_after_seconds if retry_after_seconds is not None else 1.0
        cooldown = max(0.1, min(300.0, cooldown))
        async with self._lock:
            self._rate_limit_count += 1
            reduced = max(self.minimum_limit, math.ceil(self.current_limit / 2))
            if reduced != self.current_limit:
                self.current_limit = reduced
                self._limit_change_count += 1
            self._latencies.clear()
            self._evaluating_increase = False
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + cooldown)
            self._schedule_cooldown_wake_locked()

    async def report_transient_failure(self) -> None:
        async with self._lock:
            self._transient_failure_count += 1
            self._consecutive_transient_failures += 1
            if self._consecutive_transient_failures < 3:
                return
            reduced = max(self.minimum_limit, math.ceil(self.current_limit / 2))
            if reduced != self.current_limit:
                self.current_limit = reduced
                self._limit_change_count += 1
            self._consecutive_transient_failures = 0
            self._latencies.clear()
            self._evaluating_increase = False

    async def snapshot(self) -> dict[str, int | float | bool]:
        async with self._lock:
            return {
                "current_limit": self.current_limit,
                "minimum_limit": self.minimum_limit,
                "maximum_limit": self.maximum_limit,
                "adaptive": self.adaptive,
                "active": self._active,
                "queued": sum(len(queue) for queue in self._waiters.values()),
                "queued_tasks": len(self._waiters),
                "cooldown_remaining_seconds": round(
                    max(0.0, self._cooldown_until - time.monotonic()),
                    6,
                ),
                "rate_limit_count": self._rate_limit_count,
                "transient_failure_count": self._transient_failure_count,
                "limit_change_count": self._limit_change_count,
            }

    def _dispatch_locked(self) -> None:
        if time.monotonic() < self._cooldown_until:
            self._schedule_cooldown_wake_locked()
            return
        while self._active < self.current_limit and self._task_order:
            task_id = self._task_order.popleft()
            queue = self._waiters.get(task_id)
            if queue is None:
                continue
            future: asyncio.Future[ConcurrencyLease] | None = None
            enqueued_at = 0.0
            while queue:
                candidate, candidate_enqueued_at = queue.popleft()
                if not candidate.cancelled():
                    future = candidate
                    enqueued_at = candidate_enqueued_at
                    break
            if queue:
                self._task_order.append(task_id)
            else:
                self._waiters.pop(task_id, None)
            if future is None:
                continue
            self._active += 1
            future.set_result(
                ConcurrencyLease(
                    queue_wait_seconds=max(0.0, time.monotonic() - enqueued_at),
                    limit=self.current_limit,
                    active=self._active,
                )
            )

    def _remove_waiter_locked(
        self,
        task_id: str,
        future: asyncio.Future[ConcurrencyLease],
    ) -> None:
        queue = self._waiters.get(task_id)
        if queue is None:
            return
        retained = deque((item, created) for item, created in queue if item is not future)
        if retained:
            self._waiters[task_id] = retained
            return
        self._waiters.pop(task_id, None)
        self._task_order = deque(item for item in self._task_order if item != task_id)

    def _schedule_cooldown_wake_locked(self) -> None:
        delay = max(0.0, self._cooldown_until - time.monotonic())
        if self._cooldown_handle is not None:
            self._cooldown_handle.cancel()
        loop = asyncio.get_running_loop()
        self._cooldown_handle = loop.call_later(
            delay,
            lambda: asyncio.create_task(self._resume_after_cooldown()),
        )

    async def _resume_after_cooldown(self) -> None:
        async with self._lock:
            self._cooldown_handle = None
            self._dispatch_locked()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


_CONTROLLERS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[str, AdaptiveConcurrencyController],
] = weakref.WeakKeyDictionary()


def get_video_embedding_controller(
    account_key: str,
    *,
    initial_limit: int,
    minimum_limit: int,
    maximum_limit: int,
    adaptive: bool,
    success_window: int,
) -> AdaptiveConcurrencyController:
    loop = asyncio.get_running_loop()
    by_account = _CONTROLLERS.setdefault(loop, {})
    controller = by_account.get(account_key)
    if controller is None:
        controller = AdaptiveConcurrencyController(
            initial_limit=initial_limit,
            minimum_limit=minimum_limit,
            maximum_limit=maximum_limit,
            adaptive=adaptive,
            success_window=success_window,
        )
        by_account[account_key] = controller
    return controller


def reset_video_embedding_controllers() -> None:
    _CONTROLLERS.clear()
