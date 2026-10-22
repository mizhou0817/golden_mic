# 状态码、触发位置与前端处理

2026-10-21 按工作区代码整理。行号会随代码变化，以函数名为准。错误体统一是 `{"detail": 字符串或对象}`；对象带 `code` 时，前端在 `frontend/src/lib/appApi.ts:136-148` 把它映射成中文文案。

| 状态码 | 含义 | 典型触发（出处） | Retry-After |
|---|---|---|---|
| 400 | 请求内容非法 | 稿件内容非法；Content-Length 非法（`main.py:263-264`、`main.py:1184-1208`） | — |
| 401 | **不使用** | 后端没有任何 401。凭据失败一律 404 | — |
| 403 | 来源或会话不可信 | ① Origin 不在 `FRONTEND_ORIGINS`（生产环境，或 `ENFORCE_ORIGIN_CHECK=true`）；② 生产环境缺少有效会话 cookie，或 `X-CSRF-Token` 不一致，响应头带 `X-Anonymous-Session: required`（`main.py:234-244`）；③ 生产环境建草稿时没有会话（`main.py:634-635`）；④ 读完请求体后复核失败（`main.py:873-876`、`workbench.py:1034`）；⑤ 新建上传时没有可信会话（`uploads.py:1540-1550`） | — |
| 404 | 不存在或无权访问（不区分） | 任何 capability 失败、令牌冲突、任务不存在（`main.py:801-842`）；未知的 `/api` 路径（`main.py:1532-1535`）；请求路径过长或含非法字符（`main.py:222-226`） | — |
| 408 | 读取请求体超时 | `task_operations` 路由类、`v2_editing.parse_request`（`task_operations.py:119-122`、`v2_editing.py:195-207`） | — |
| 409 | 状态冲突，先读状态再决定 | `stale_revision`（`workbench.py:1053`）；任务忙（`workbench.py:1062`）；版本数已达上限（`workbench.py:1222`）；`already_accepted`（`admission.py:49-52`）；`accepted_start_uncertain`（`drafts.py:1203-1213`）；不是草稿（`drafts.py:478-480`）；操作锁等待 20 秒超时（`drafts.py:447-451`）；分片字节不同（`uploads.py:749-757`）；`stale_metadata`（`task_metadata.py:80-82`）；RetrySame 输入已变 `retry_inputs_changed`（`drafts.py:338-340`） | — |
| 410 | 已到期或已删除 | `task_gone`：到期，或墓碑能证明已删除（`main.py:810-814`、`main.py:832-834`）；`draft_expired`（`drafts.py:476-477`）。手动删除不写墓碑，之后访问返回 404 | — |
| 411 | 缺少 Content-Length | `/api/tasks/` 下的写请求（`main.py:247-262`） | — |
| 413 | 请求体超过上限 | 默认 256 KiB；`recordings` 21 MiB；分片 PUT 8 MiB（`main.py:247-262`）；上传字节超限 | — |
| 415 | 媒体类型不支持 | 录音、上传的格式检查（见 `docs/WORKBENCH_API.md:69`） | — |
| 422 | 字段非法或请求无法执行 | FastAPI 校验失败；strict 模型出现多余字段；空操作；`quote_missing`、`quote_unverified`（`drafts.py:1130-1139`）；无法安全对齐（`drafts.py:1017-1018`）；pretranscribe 的请求体不是 `{"retry": true}`（`drafts.py:811-813`）；原声句上传替代录音（`workbench.py:1414-1419`） | — |
| 429 | 频率或容量受限，不自动重试 | `start_budget`：Retry-After 动态计算，至少 60 秒（`admission.py:112-120`）；旧接口的创建和重试：3600 秒（`main.py:1124`、`main.py:1160`）；原话预检：1 秒（`main.py:683`）；AI 写作：600 秒或 5 秒（`main.py:715`、`main.py:748`）。**不带 Retry-After 的**：草稿数上限（`drafts.py:504`）、workbench 待处理已满（`workbench.py:1226`）、导出容量（`v2_editing.py:578-581`）、上传配额（`uploads.py:612-616`）、恢复配额（`drafts.py:1266`、`drafts.py:1276`） | 见左 |
| 503 | 暂不可用 | 排空期间的写请求，`Retry-After: 60`（`main.py:227-233`）；服务或 workbench 未就绪（`drafts.py:444-456`、`workbench.py:1065-1067`）；V2 等待队列已满（`task_manager.py:666-677`）；必需的本地语音不可用（`drafts.py:1035`、`drafts.py:1068`）；存储身份变化（`admission.py:75`）。`/health/ready` 未就绪时返回 503；`/api/config/availability` 总是返回 200，状态写在正文里 | 排空时 60 |
| 507 | 磁盘保护 | `InsufficientDiskSpaceError` 由中间件转换，`Retry-After: 300`（`main.py:282-287`）；草稿创建和添加文件（`drafts.py:507`、`drafts.py:761`）；V2 导出（`v2_editing.py:592`） | 300 |

## 前端怎样处理

- **403 且带 `X-Anonymous-Session: required`**：调用 `clearBrowserSession()` 后报错，**不重放请求**（`appApi.ts:317-319`）。测试 `frontend/scripts/test-app-api.mjs:658` 断言"never replays the POST"。
- **429**：解析 `Retry-After`（秒数或 HTTP 日期，上限 604800 秒），提示"请在 N 秒后手动重试"，不自动重试（`appApi.ts:149-170`）。
- **超时**：
  - FormData 请求 30 分钟；
  - Blob 请求 120 秒加上 size/8 毫秒；
  - `/api/script/assist` 170 秒，`/api/script/effects` 60 秒；
  - 其余请求 30 秒（`appApi.ts:290-293`）。

  超时后的提示是"请先刷新作品列表，避免重复提交"（`appApi.ts:441`）。
- **唯一会自动重试的写请求是分片 PUT**：最多 3 次；只在没有状态码、≥500、408、429 时重试；重发前先 GET 状态，确认该分片还没收到（`uploadSessions.ts:416-478`）。
- **workbench 客户端**不引导会话、不存令牌、不自动重试写请求（`workbenchApi.ts:4`）。回执不符合预期时，提示"不要重复提交"。

## 中间件里的检查顺序（`security_headers`）

1. 路径过长或含非法字符：返回 404。
2. 排空期间，`/api/tasks*`、`/api/uploads*`、`/api/match*` 的写请求：返回 503。
3. Origin 检查：返回 403。
4. 生产环境检查 CSRF 会话：返回 403。
5. `/api/tasks/` 下的写请求检查 Content-Length 和请求体上限：返回 411 或 413。
6. `POST /api/tasks` 的频控和磁盘预留。
7. 给响应统一加安全头。`/api/` 下的响应带 `Cache-Control: private, no-store`。

出处：`main.py:217-314`、`main.py:1211-1246`。

中间件由外到内的顺序：ServerError → CORS → TrustedHost → `security_headers` → 路由。所以 Host 拒绝和 CORS 预检的响应不带安全头。
