# 状态码、触发位置与前端处理

2026-10-21 按工作区代码整理。行号会随代码变化，以函数名为准。错误体统一是 `{"detail": 字符串或对象}`；对象带 `code` 时，前端在 `frontend/src/lib/appApi.ts:136-148` 把它映射成中文文案。

| 状态码 | 含义 | 典型触发（出处） | Retry-After |
|---|---|---|---|
| 400 | 请求内容非法 | 稿件内容非法；Content-Length 非法（`main.py:267-268`、`main.py:1220-1244`） | — |
| 401 | **不使用** | 后端没有任何 401。凭据失败一律 404 | — |
| 403 | 来源或会话不可信 | ① Origin 不在 `FRONTEND_ORIGINS`（生产环境，或 `ENFORCE_ORIGIN_CHECK=true`）；② 生产环境缺少有效会话 cookie，或 `X-CSRF-Token` 不一致，响应头带 `X-Anonymous-Session: required`（`main.py:238-248`）；③ 生产环境建草稿时没有会话（`main.py:665-666`）；④ 读完请求体后复核失败（`main.py:909-912`、`workbench.py:1040-1048`），或返回的记录与 URL 里的任务不一致（`workbench.py:1037`）；⑤ 新建上传时没有可信会话（`uploads.py:1550-1560`） | — |
| 404 | 不存在或无权访问（不区分） | 任何 capability 失败、令牌冲突、任务不存在（`main.py:837-878`）；未知的 `/api` 路径（`main.py:1570-1573`）；请求路径过长或含非法字符（`main.py:226-230`） | — |
| 408 | 读取请求体超时（30 秒） | `task_operations` 路由类、`v2_editing.parse_request`（`task_operations.py:119-122`、`v2_editing.py:196-208`）、`main._bounded_json`、`drafts.bounded_json` | — |
| 409 | 状态冲突，先读状态再决定 | `stale_revision`（apply、restore：`workbench.py:1056`；checks：`publication.py:348`；都带 `current_revision`）；导出的版本不符、任务忙或需要恢复：纯文本 `Task is stale, busy or requires recovery`，没有 code（`v2_editing.py::ready`）；任务忙（`workbench.py:1065`）；版本数已达上限（`workbench.py:1225`）；`already_accepted`（`admission.py:108`）；`accepted_start_uncertain`（`drafts.py:1217-1227`）；不是草稿（`drafts.py:483-485`）；操作锁等待 20 秒超时（`drafts.py:452-456`）；分片字节不同（`uploads.py:750-758`）；`stale_metadata`（`task_metadata.py:84-86`）；RetrySame 输入已变 `retry_inputs_changed`（`drafts.py:343-345`） | — |
| 410 | 已到期 | `task_gone`：记录已到期（草稿和作品都是，`main.py:868-870`），或记录已清理、墓碑能证明（要凭访问令牌或同一会话，`main.py:848-852`、`admission.py:150-164`）；`draft_expired` 只在请求通过授权后、处理过程中（等待操作锁或接收分块时）草稿恰好到期才出现（`drafts.py::DraftService.check`）。手动删除不写墓碑，之后访问返回 404 | — |
| 411 | 缺少 Content-Length | `/api/tasks/` 下的写请求（`main.py:251-266`） | — |
| 413 | 请求体超过上限 | 默认 256 KiB；`recordings` 21 MiB；分片 PUT 8 MiB（`main.py:251-266`）；上传字节超限 | — |
| 415 | 媒体类型不支持 | 录音、上传的格式检查（见 `docs/WORKBENCH_API.md:116`） | — |
| 422 | 字段非法或请求无法执行 | FastAPI 校验失败；strict 模型出现多余字段；空操作；`quote_missing`、`quote_unverified`（`drafts.py:1144-1153`）；无法安全对齐（`drafts.py:1031-1032`）；pretranscribe 的请求体不是 `{"retry": true}`（`drafts.py:825-827`）；原声句上传替代录音（`workbench.py:1417-1422`） | — |
| 429 | 频率或容量受限，不自动重试 | `start_budget`：Retry-After 动态计算，至少 60 秒（`admission.py:116-124`）；旧接口的创建和重试：3600 秒（`main.py:1160`、`main.py:1196`）；原话预检：1 秒（`main.py:714`）；AI 写作：600 秒或 5 秒（`main.py:746`、`main.py:781`）。**不带 Retry-After 的**：草稿数上限（`drafts.py:509`）、workbench 待处理已满（`workbench.py:1229`）、导出容量（`v2_editing.py:579-582`）、上传配额（`uploads.py:613-617`）、恢复配额（`drafts.py:1280`、`drafts.py:1290`） | 见左 |
| 503 | 暂不可用 | 排空期间的写请求，`Retry-After: 60`（`main.py:232-237`）；服务或 workbench 未就绪（`drafts.py:449-461`、`workbench.py:1068-1070`）；V2 等待队列已满（`task_manager.py:668-679`）；必需的本地语音不可用（`drafts.py:1049`、`drafts.py:1082`）；存储身份变化（`admission.py:79`）。`/health/ready` 未就绪时返回 503；`/api/config/availability` 总是返回 200，状态写在正文里 | 排空时 60 |
| 507 | 磁盘保护 | `InsufficientDiskSpaceError` 由中间件转换，`Retry-After: 300`（`main.py:286-291`）；草稿创建和添加文件（`drafts.py:512`、`drafts.py:775`）；V2 导出（`v2_editing.py:593`）。后三处直接抛 `HTTPException(507)`，不带 Retry-After | 300（只对中间件转换的那一种） |

## 前端怎样处理

- **403 且带 `X-Anonymous-Session: required`**：调用 `clearBrowserSession()` 后报错，**不重放请求**（`appApi.ts:319-321`）。测试 `frontend/scripts/test-app-api.mjs:658` 断言"never replays the POST"。
- **429**：解析 `Retry-After`（秒数或 HTTP 日期，上限 604800 秒），提示"请在 N 秒后手动重试"，不自动重试（`appApi.ts:149-170`）。
- **超时**：
  - FormData 请求 30 分钟；
  - Blob 请求 120 秒加上 size/8 毫秒；
  - `/api/tasks/{id}/start` 和 `/recover-draft` 30 分钟（服务端要先复制素材，可能还要转写）；
  - `/api/script/assist` 170 秒，`/api/script/effects` 60 秒；
  - 其余 JSON 请求 30 秒（`appApi.ts:290-295`）。

  超时后：旧的整表单上传（`POST /api/tasks` 带文件，XHR）提示"上传超时；请先刷新作品列表，避免重复提交。"（`appApi.ts:443`）；其余请求超时只是中止（`AbortError`，`appApi.ts:287-295`），没有统一的提示文案。
- **唯一会自动重试的写请求是分片 PUT**：最多 3 次；只在没有状态码、≥500、408、429 时重试；重发前先 GET 状态，确认该分片还没收到（`uploadSessions.ts:416-478`）。
- **workbench 客户端**不引导会话、不存令牌、不自动重试写请求（`workbenchApi.ts:4`）。回执不符合预期时，提示"不要重复提交"。

## 中间件里的检查顺序（`security_headers`）

1. 路径过长或含非法字符：返回 404。
2. 排空期间，`/api/tasks*`、`/api/uploads*`、`/api/match*`、`/api/script/assist` 的写请求：返回 503（`/api/script/effects` 不拦，排空期间只按关键词回答）。
3. Origin 检查：返回 403。
4. 生产环境检查 CSRF 会话：返回 403。
5. `/api/tasks/` 下的写请求检查 Content-Length 和请求体上限：返回 411 或 413。
6. `POST /api/tasks` 的频控和磁盘预留。
7. 给响应统一加安全头。`/api/` 下的响应带 `Cache-Control: private, no-store`。

出处：`main.py:221-318`、`main.py:1247-1284`。

中间件由外到内的顺序：ServerError → CORS → TrustedHost → `security_headers` → 路由。所以 Host 拒绝和 CORS 预检的响应不带安全头。
