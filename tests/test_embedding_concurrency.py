import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from backend.providers.embedding import (
    EmbeddingResponse,
    VolcengineMultimodalEmbeddingProvider,
)
from backend.providers.embedding_concurrency import (
    AdaptiveConcurrencyController,
    reset_video_embedding_controllers,
)


class AdaptiveConcurrencyControllerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_video_embedding_controllers()

    async def test_caps_global_activity_and_round_robins_tasks(self) -> None:
        controller = AdaptiveConcurrencyController(
            initial_limit=2,
            minimum_limit=1,
            maximum_limit=4,
            adaptive=False,
            success_window=5,
        )
        active = 0
        maximum_active = 0
        start_order: list[str] = []

        async def job(task_id: str, index: int) -> None:
            nonlocal active, maximum_active
            async with controller.slot(task_id):
                active += 1
                maximum_active = max(maximum_active, active)
                start_order.append(f"{task_id}{index}")
                try:
                    await asyncio.sleep(0.01)
                finally:
                    active -= 1

        first_task = [asyncio.create_task(job("A", index)) for index in range(5)]
        await asyncio.sleep(0)
        second_task = [asyncio.create_task(job("B", index)) for index in range(2)]
        await asyncio.gather(*first_task, *second_task)

        self.assertEqual(maximum_active, 2)
        first_b = next(index for index, value in enumerate(start_order) if value.startswith("B"))
        last_a = max(index for index, value in enumerate(start_order) if value.startswith("A"))
        self.assertLess(first_b, last_a)

    async def test_rate_limit_halves_limit_and_enforces_shared_cooldown(self) -> None:
        controller = AdaptiveConcurrencyController(
            initial_limit=8,
            minimum_limit=2,
            maximum_limit=8,
            adaptive=True,
            success_window=5,
        )
        await controller.report_rate_limit(0.2)
        snapshot = await controller.snapshot()
        self.assertEqual(snapshot["current_limit"], 4)
        self.assertEqual(snapshot["rate_limit_count"], 1)

        loop = asyncio.get_running_loop()
        started = loop.time()
        async with controller.slot("task"):
            waited = loop.time() - started
        self.assertGreaterEqual(waited, 0.15)

    async def test_adaptive_limit_increases_then_rolls_back_on_p95_regression(self) -> None:
        controller = AdaptiveConcurrencyController(
            initial_limit=2,
            minimum_limit=1,
            maximum_limit=4,
            adaptive=True,
            success_window=5,
        )
        for _ in range(5):
            await controller.report_success(1.0)
        self.assertEqual((await controller.snapshot())["current_limit"], 3)

        for _ in range(5):
            await controller.report_success(1.0)
        self.assertEqual((await controller.snapshot())["current_limit"], 4)

        for _ in range(5):
            await controller.report_success(2.0)
        self.assertEqual((await controller.snapshot())["current_limit"], 3)


class GlobalProviderConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_video_embedding_controllers()

    async def test_two_provider_instances_share_one_global_limit(self) -> None:
        class SlowProvider(VolcengineMultimodalEmbeddingProvider):
            active = 0
            maximum_active = 0

            async def _request_multimodal_response(
                self,
                payload: dict[str, object],
                api_key: str,
                *,
                timeout: httpx.Timeout | None = None,
            ) -> EmbeddingResponse:
                type(self).active += 1
                type(self).maximum_active = max(type(self).maximum_active, type(self).active)
                try:
                    await asyncio.sleep(0.02)
                    return EmbeddingResponse(
                        vector=[0.25] * 1024,
                        status_code=200,
                        request_id="test-request",
                    )
                finally:
                    type(self).active -= 1

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_a = root / "task-a"
            task_b = root / "task-b"
            task_a.mkdir()
            task_b.mkdir()
            items_a = _video_items(task_a, 4)
            items_b = _video_items(task_b, 4)
            providers = [
                SlowProvider(
                    base_url="https://ark.example/api/v3",
                    api_keys="same-account-key",
                    model="embedding-model",
                    dimensions=1024,
                    video_concurrency=4,
                    video_global_concurrency=2,
                    video_global_max_concurrency=2,
                    video_min_concurrency=1,
                    task_dir=task_dir,
                    backoff_base=0,
                )
                for task_dir in (task_a, task_b)
            ]
            results_a, results_b = await asyncio.gather(
                providers[0].embed_video_corpus(items_a),
                providers[1].embed_video_corpus(items_b),
            )
            metrics_a = _read_metrics(task_a)
            metrics_b = _read_metrics(task_b)

        self.assertEqual(SlowProvider.maximum_active, 2)
        self.assertTrue(all(vector is not None for vector in [*results_a, *results_b]))
        self.assertEqual(len(metrics_a), 4)
        self.assertEqual(len(metrics_b), 4)
        self.assertTrue(all(item["current_limit"] == 2 for item in [*metrics_a, *metrics_b]))

    async def test_http_429_uses_retry_after_reduces_global_limit_and_records_metrics(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0.05", "X-Request-Id": "rate-limited"},
                    json={"error": "too many requests"},
                )
            return httpx.Response(
                200,
                headers={"X-Request-Id": "success-id"},
                json={"data": {"embedding": [0.5] * 1024}},
            )

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory) / "task"
            task_dir.mkdir()
            items = _video_items(task_dir, 1)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                provider = VolcengineMultimodalEmbeddingProvider(
                    base_url="https://ark.example/api/v3",
                    api_keys="rate-test-key",
                    model="embedding-model",
                    dimensions=1024,
                    client=client,
                    video_global_concurrency=4,
                    video_global_max_concurrency=8,
                    video_min_concurrency=2,
                    task_dir=task_dir,
                    backoff_base=0,
                )
                result = await provider.embed_video_corpus(items)
                snapshot = await provider._get_video_controller().snapshot()
            metrics = _read_metrics(task_dir)

        self.assertIsNotNone(result[0])
        self.assertEqual(attempts, 2)
        self.assertEqual(snapshot["current_limit"], 2)
        self.assertEqual(snapshot["rate_limit_count"], 1)
        self.assertEqual([item["result"] for item in metrics], ["error", "success"])
        self.assertEqual(metrics[0]["status_code"], 429)
        self.assertEqual(metrics[0]["request_id"], "rate-limited")
        self.assertEqual(metrics[1]["request_id"], "success-id")


def _video_items(task_dir: Path, count: int) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for index in range(count):
        path = task_dir / f"shot_{index}.mp4"
        path.write_bytes(bytes([index + 1]) * (index + 1))
        items.append((path, f"metadata-{index}"))
    return items


def _read_metrics(task_dir: Path) -> list[dict[str, object]]:
    path = task_dir / "video_embedding_metrics.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
