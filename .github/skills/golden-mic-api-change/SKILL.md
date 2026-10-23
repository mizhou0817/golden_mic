---
name: golden-mic-api-change
description: 用于在金话筒后端新增或修改 HTTP 接口（FastAPI 路由）：先对照现有 V2 接口和文档约定，再处理错误码、Origin/CSRF、任务凭据 X-Task-Token 与匿名会话、请求体上限、版本条件 expected_revision、没有幂等键时怎样防重复提交、/api/config/limits 与前端常量同步、用模拟服务写测试、要同步的文档和配置模板。设计或实现 /api/tasks/{id}/ 下的接口（pretranscribe、align、apply、versions、restore、recordings、exports、start 等），或改它们的请求、响应、状态码时使用。不要在只改界面文案、只调 FFmpeg 参数或部署上线时使用。关键词：接口 API、endpoint、路由 router、错误码 status code、CSRF、capability、幂等 idempotency、重试 retry、限额 limits、FastAPI。
---

# 金话筒接口变更流程

新增或修改接口时，先找现有实现，再按本文的规则设计、写测试、实现、同步文档。规则都来自当前代码；如果以后出现要改代码才能消除的不一致，列在文末的"不一致"一节，不要自行裁决。目前没有（见"2026-10-22 已消除的不一致"）。

## 何时使用 / 不要使用

- 使用：
  - 新增 `/api/tasks/{id}/...` 接口；
  - 修改现有接口的请求、响应、状态码、鉴权或限额；
  - 评审接口相关的改动。
- 不要使用：
  - 只改界面文案（用 gm-ux-copy）；
  - 只调渲染参数（用 golden-mic-media）；
  - 调用外部 AI 服务的细节（用 golden-mic-providers）；
  - 部署上线（见 `deploy/README.md`）。
- FastAPI 的通用写法（`Annotated` 依赖、返回类型、SSE）看 fastapi skill。它和本文冲突时以本文为准。

## 先读：现状（附出处）

- **V2 链路。** 草稿回执 → 在该任务下分块上传 → complete/探测/预转写 → 对齐预览 → 显式 start → 异步制作 → apply 产生新版本 → 检查和导出（`docs/DEVELOPMENT.md` 第 2 节）。
- **§1.4 的接口大多已经存在。** 交接文档 §12 原先列为"需新增后端"的 pretranscribe、align、apply（剪短 `trims`、换段 `takes`、换画面 `replaces`、转旁白 `to_narration`、改说话人 `speakers`）、versions、restore、recordings、exports，代码里都已经有了（2026-10-22 §12 已按代码更新），见 [references/endpoints.md](references/endpoints.md)。**先扩展现有接口，不要另起一套。**
- **路由挂载。**
  - 路由由各模块的工厂函数创建：`create_draft_router`（`drafts.py`）、`create_workbench_router`（`workbench.py`）、`create_v2_editing_router`（`v2_editing.py`）、`create_upload_router`（`uploads.py`）。它们在 `main.py:1329-1366` 挂载，必须排在兜底路由 `/api/{unmatched_path:path}`（`main.py:1568`）之前。
  - 工厂是惰性的：导入模块或生成 OpenAPI 时不能打开存储（`main.py:92-95`）。
- **鉴权回调** 的签名是 `authorize(request, task_id, write=False)`（`main.py:878-894`）。
  - V2 任务即使从本机访问也必须带 `X-Task-Token`（`main.py:862-867`）。
  - 凭据有误一律返回 404；能证明任务已删除时返回 410 `{"code":"task_gone"}`（`main.py:835-876`）。
  - 后端不返回 401。

## 规则

### 1. 鉴权、Origin 与 CSRF
- **写路径要在前缀列表里。** 排空（503）、Origin 检查（403）、生产环境 CSRF（403，响应头 `X-Anonymous-Session: required`）都按前缀生效，列表在 `main.py:1245-1271`。新增顶层前缀时要同步这些列表。
- **新的自定义请求头** 要加进 CORS 的 `allow_headers`（`main.py:326`）。
- **读完请求体后再复核一次授权**，写法参照 `main.py:907-910`（metadata）和 `workbench.py:1040-1048`（`mutation_intent`：先 `before_mutation` 复核，再 `ready()`）。
- **不新增 401。** 凭据失败返回 404，不暴露任务是否存在。

### 2. 请求体
- **长度和上限。** `/api/tasks/` 下的写请求必须带 Content-Length，缺了返回 411。默认上限 256 KiB，`recordings` 为 21 MiB，分片 PUT 为 8 MiB，超限返回 413（`main.py:251-266`）。
- **读取方式。** JSON 要用有上限、有超时的读取函数：`main._bounded_json`、`drafts.bounded_json`、`v2_editing.parse_request`。
- **请求模型** 用 strict、禁止额外字段，参照 `ApplyRequest`（`v2_editing.py:166-193`）。
- **OpenAPI。** 手动读取请求体的路由，要用 `openapi_extra` 声明请求体（`main.py:653-657`），或登记进 `_openapi_document`（`main.py:1537-1544`）。生产环境关闭 OpenAPI（`config.py:202-203`）。

### 3. 状态码和错误体
- **按约定选状态码**，不新造含义。对照表见 [references/status-codes.md](references/status-codes.md)。
- **错误体** 是 `{"detail": 字符串或对象}`。需要前端区分的错误用 `detail.code`，并在 `frontend/src/lib/appApi.ts:136-148` 加中文文案。
- **Retry-After。** 中间件返回的 503（排空，60 秒）和 507（磁盘不足，300 秒）、部分 429（`start_budget` 等）带 `Retry-After`（秒）；直接抛出的 429、507 和队列满的 503 不带，逐项见 [references/status-codes.md](references/status-codes.md)。前端会解析这个头并提示用户手动重试（`appApi.ts:149-170`）。

### 4. 没有幂等键：防重复提交
- **现有去重方式。** 现有接口都没有 `Idempotency-Key`，靠状态检查、版本条件和账本去重：
  - start 用 SQLite 账本，重复接收返回 409 `already_accepted`（`admission.py:108`；表结构在 `admission.py:53-56`）；
  - apply、restore、checks 要求 `expected_revision`，不符返回 409 `{code: stale_revision, current_revision}`；metadata 还要 `expected_metadata_revision`，不符返回 409 `stale_metadata`；exports 不符（或任务忙、需要恢复）返回 409 纯文本 `Task is stale, busy or requires recovery`，没有 code，也没有 `current_revision`（`v2_editing.py::ready`）；
  - pretranscribe 必须显式传 `{"retry": true}`；
  - 分片 PUT：同一索引、相同字节可以重放，字节不同返回 409。
- **新的收费或改状态的 POST**，必须有上面至少一种防护，推荐 `expected_revision` 或状态检查。返回 202 加 `operation_id` 或 `status`，由客户端轮询状态。
- **响应不明时先查状态。** 超时、断网时，客户端只能先 GET 状态，**不能自动重发**（`appApi.ts:319` 注释 "Mutations are never replayed"；`workbenchApi.ts:4`）。服务端和客户端都不加自动重试收费调用的逻辑。
- **真要加幂等键** 属于新设计：先和用户确认命名、存储、有效期，以及它和 `expected_revision` 的关系。

### 5. 限额与常量
- **前端以 `GET /api/config/limits` 返回的值为准**（`docs/CONFIGURATION.md:83`）。
- **新增或修改限额时：**
  1. 规则阈值放在 `backend/mode_rules.json`，前端 `productionModes.ts` 直接导入同一个文件；
  2. 运行时配置放在 `backend/config.py` 的 `Settings`，并同步两份配置模板（见下方同步清单）；
  3. 对外暴露时改 `PublicLimitsResponse`（`models.py:473-506`）和 `main.py:363-397`，同时同步前端的 `PublicLimits` 类型（`frontend/src/types.ts:35-66`）和 `getPublicLimits` 校验（`appApi.ts:212-238`）。
- **限额只有一份。** 新的限额和阈值写进 `mode_rules.json`，后端经 `production_modes`（`LIMITS`、`SCRIPT_MAX_CHARS`、`TITLE_MAX_CHARS`、`MATCH_OK`、`MATCH_LOW`）读取。`models.py` 里 `PublicLimitsResponse` 的默认值只为兼容旧载荷，`public_limits` 每个字段都显式给值（`tests/test_v2_code_doc_agreement.py` 检查）。

### 6. 计费
- 接口里会触发 ASR、TTS、Kimi、Embedding 的地方，在接口说明里写清"可能计费"，并且只在用户显式操作时触发。调用细节和模拟方法见 golden-mic-providers。

## 步骤

1. **对照。** 在 [references/endpoints.md](references/endpoints.md) 找相近的现有接口，读它的 handler、鉴权方式、请求模型和测试。确认需求是不是已经实现。
2. **设计。** 写出以下内容，交用户确认：
   - 方法和路径，鉴权方式（P、W、E 三类，见 endpoints.md）；
   - 请求模型（strict）和响应（202 加状态，或 200）；
   - 每个状态码的触发条件；
   - 防重复提交的方式，是否计费，是否要进排空列表。
3. **先写测试。** 按 [references/testing.md](references/testing.md) 的写法，在 `tests/test_v2_<主题>.py` 里写。收费服务的工厂函数用 `side_effect=AssertionError(...)` 打桩。
4. **实现。** 把路由加进对应的工厂，按需改中间件前缀、CORS、OpenAPI 声明，再改前端客户端和文案。
5. **同步** 文档和配置，见下方清单。
6. **验证。** 用 golden-mic-verify。只有 `tests/test_v2_*.py` 会被受保护入口跑到；`test_workbench.py`、`test_mode_api.py`、`test_workspace_access.py` 不在默认运行范围里，也不在 `--legacy` 里。

## 同步清单

- [ ] **接口索引。** `docs/README.md` 的接口索引（第 31–54 行），以及相关的不带日期的 `*_API.md`（如 `WORKBENCH_API.md`、`MEDIA_INPUT_API.md`）。带日期的文档是快照，不要改写。
- [ ] **新配置字段：**
  - 同时加进 `.env.example` 和 `deploy/golden-mic.env.production.example`。**改 `deploy/` 要先经用户批准。**
  - `tests/test_v2_config_template.py` 会校验两份模板和 `Settings` 的键完全一致；新增布尔字段要写进 `DEV_BOOLEAN_POLICY`。
  - 更新 `docs/CONFIGURATION.md` 里的字段数（现为 138）和字段表。
- [ ] **配置字段的副作用。** RetrySame 的 `_retry_binding` 包含完整的 `settings.model_dump`。新增任何字段，都会让此前失败的任务在 RetrySame 时返回 409 `retry_inputs_changed`（`drafts.py:310-345`）。要在交付说明里写明。
- [ ] **前端：**
  - `appApi.ts` 的路径白名单和超时（第 290–302 行），`detail.code` 文案；
  - `PublicLimits` 类型和校验；
  - `workbenchApi.ts`、`uploadSessions.ts` 的回执解析。
- [ ] **发布白名单。** 新的可发布文档要加进 `deploy/build_release.py` 的 `INCLUDED_FILES`，同样要先经批准。随包发布的文档只能链接包里有的文件，否则 `tests/test_v2_release_integrity.py` 会失败。
- [ ] **回归测试。** 补一个 `tests/test_v2_*.py`，不弱化原有的鉴权、时序、取值范围、版本和资源断言（`docs/DEVELOPMENT.md` 第 9 节）。

## 示例：给 `/align` 加一个返回字段

1. **现状。** `POST /api/tasks/{id}/align` 只做预览，不提交编辑（`drafts.py:1100-1105`）。返回 `{matches, match_ok, match_low, local_speech, rules_version}`，`matches[]` 每行有 `idx, kind, source, alt_takes, score, upload_id, start, end, speaker_id, asr_text`。
2. **设计。** 新字段加在 `matches[]` 的行里；旧客户端会忽略未知字段，所以不改状态码。
3. **测试。** 在 `tests/test_v2_drafts.py` 里照现有的 align 用例加断言。ASR 结果用测试里现成的合成转写，不调用真实 ASR。
4. **前端。** 在 `workbenchApi.ts` 或 `appApi.ts` 的回执解析里接受新字段。
5. **文档。** 更新 `docs/README.md` 的接口说明；不改带日期的 `THREE_MODE_API_20260928.md`。

## 验证

- 运行 golden-mic-verify 的命令 B（受保护的后端回归）。改了前端时，征得同意后运行命令 C。
- 自查：
  - 新接口在没有 token、token 错误、`expected_revision` 过期、请求体超限这四种情况下，分别返回 404、404、409、413；
  - 测试里没有构造任何真实的服务商客户端。

## 2026-10-22 已消除的不一致（目前没有未消除的）

2026-10-22 先按代码更正了文档（`docs/WORKBENCH_API.md` 不再写 401；交接文档 §12 写明 403 不重放、Retry-After、pretranscribe 和 align 的真实语义与字段名；`docs/RUNBOOK.md` 写明排空范围），随后按维护者要求改了代码：

1. **限额只有一份。** `GET /api/config/limits` 的每个字段都取自执行处：`mode_rules.json`（经 `production_modes.LIMITS`、`SCRIPT_MAX_CHARS`、`TITLE_MAX_CHARS`、`MATCH_OK`、`MATCH_LOW`）、`Settings`（提交额度、语速容差等）或模块常量（`uploads.CHUNK_SIZE`、`quality.MAX_VISUAL_CLIP_SECONDS`、`task_metadata` 的保留时长、`admission.TOMBSTONE_SECONDS`）。上传的三个上限取设置值和 `uploads.MAX_WORK_FILES`、`MAX_FILE_BYTES`、`MAX_WORK_BYTES` 里小的那个；只有并发 1 和待处理 5 的上限还是 `public_limits` 里的字面量。
2. **restore 必须带 `expected_revision`。** 缺少返回 422，和当前版本不一致返回 409 `stale_revision`。
3. **请求体 30 秒读不完返回 408**（`main._bounded_json`、`drafts.bounded_json`），不再变成 500。
4. **排空也拦 `/api/script/assist`**：AI 写稿返回 503，不再调用收费服务。`/api/script/effects` 不拦：排空期间它不调用模型，只按稿子里的关键词回答。
5. **start 的超时。** 前端对 `/start` 和 `/recover-draft` 等 30 分钟（服务端要先复制素材，可能还要转写），其他 JSON 请求仍是 30 秒；超时后仍要先读状态，不能重发。

`docs/CLASSROOM_API.md` 是已退役的历史文档，保持原样。任务书 §1.4 不在仓库里。

## 参考

- [references/status-codes.md](references/status-codes.md)：状态码、触发位置、Retry-After 和前端处理
- [references/endpoints.md](references/endpoints.md)：现有 V2 接口表，以及 align、pretranscribe、apply、start、recordings、exports 的请求和响应
- [references/testing.md](references/testing.md)：新接口测试的三种写法
- 来源：`backend/main.py`、`drafts.py`、`workbench.py`、`v2_editing.py`、`uploads.py`、`admission.py`、`anonymous_access.py`、`frontend/src/lib/appApi.ts`、`workbenchApi.ts`、`uploadSessions.ts`、`docs/README.md`、`docs/DEVELOPMENT.md`、`docs/CORE_WORKSPACE_20260927.md`、`docs/THREE_MODE_API_20260928.md`、`docs/WORKBENCH_API.md`。2026-10-21 按工作区代码核对，行号会变，以函数名为准。
