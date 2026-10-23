# 新接口的测试写法

测试文件放在 `tests/test_v2_<主题>.py`。受保护入口默认只发现 `tests/test_v2*.py`，`--legacy` 只多加 8 个模块（`tests/run_v2_validation.py:33-36`、`tests/run_v2_validation.py:469`）。放在别的文件里的测试不会被跑到。

受保护入口导入应用前做这些事（`tests/run_core_validation.py:63-88`、`tests/run_core_validation.py:245-325`）：

- 设置 `PYTHON_DOTENV_DISABLED=1`、`APP_ENV=test`，`DATA_DIR` 指向 TEMP；
- 把所有服务商的 base URL 改成 `https://safe.test`；
- 拦截 httpx、aiohttp 和 websocket，只放行本进程的回环假服务。

**测试仍然要自己保证不构造真实的服务商客户端**，不能只靠这层拦截。

## 写法一：只挂路由（最常用）

参照 `tests/test_v2_drafts.py:27-38`、`tests/test_v2_drafts.py:689-698`、`tests/test_workbench.py:88-115`。

```python
import httpx
from fastapi import FastAPI, HTTPException, Request
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from backend.config import Settings

class NewEndpointTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = ...  # 测试自己的临时目录
        self.settings = Settings(_env_file=None, app_env="test",
                                 data_dir=root / "tasks", asr_cache_dir=root / "cache",
                                 min_free_disk_gb=0)
        # 按被测模块建 TaskManager / DraftUploadStore / DraftService（照抄现有测试）
        app = FastAPI()

        async def authorize(request: Request, task_id: str, write: bool = False):
            if not self.manager.authorize(task_id, request.headers.get("x-task-token")):
                raise HTTPException(404)

        app.include_router(create_xxx_router(..., authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://testserver")

    async def test_paid_providers_are_never_built(self):
        with patch("backend.workbench.create_tts_provider",
                   side_effect=AssertionError("must not bill")):
            response = await self.client.post(...)
        self.assertEqual(response.status_code, 202)
```

- **收费服务的工厂**用 `side_effect=AssertionError(...)` 打桩：一旦被构造，测试就失败。
- **需要假实现时**：换成 `FakeTTS`、`FakeEmbedding`、`FakeLLM`（`tests/test_workbench.py:45-86`），或 `FakeVoice`、`FakeVision`（`tests/test_mode_pipeline.py:103`、`tests/test_mode_pipeline.py:133`）。服务商的模拟方法详见 golden-mic-providers。
- **需要匿名会话时**，加一个小中间件写入 `request.state.anonymous_session_id`（`tests/test_upload_sessions.py:175-183`）。

## 写法二：真实 `main.app`，不进 lifespan

参照 `tests/test_v2_drafts.py:454-459`、`tests/test_v2_export_download_auth.py:36-85`。

- 用 `patch.multiple(main, settings=..., task_manager=..., upload_capacity_guard=..., _upload_store=..., _draft_service=...)` 换掉全局对象。
- 客户端用 `httpx.ASGITransport(app=main.app, client=("198.51.100.23", 12345))`。故意用非回环地址，避免"本机可信"的放行逻辑掩盖鉴权缺失。
- 用来验证中间件：Origin、请求体上限、411、413、排空 503、未知路径 404。

## 写法三：生产环境的 CSRF、Origin 和频控

参照子进程隔离的用例 `tests/test_workspace_access.py:39-63`、`tests/test_workspace_access.py:682-713`。这些用例要以生产配置启动，比较重，只在改动涉及会话或 CSRF 时写。

## 必测的情况

- 没有 token、token 错误：都返回 404；任务已到期：返回 410。
- `expected_revision` 过期：返回 409。apply、restore、checks 的响应体带 `current_revision`；metadata 是 `stale_metadata`；exports 是纯文本（见 status-codes.md）。
- 请求体超限：返回 413；缺 Content-Length：返回 411。
- 多余字段：返回 422（strict 模型）。
- 收费服务的工厂一次都没有被构造；会收费的路径只被调用预期的次数，例如 `factory.call_count == 1`。
- 响应和日志里没有令牌、密钥或带 token 的 URL。

## 运行

用 golden-mic-verify 的命令 B。本机上已知的基线失败只有符号链接权限那一项（`WinError 1314`）；`test_v2_release_integrity` 曾有的 2 项失败已在 2026-10-22 修好，见该 skill 的"已知情况"。
