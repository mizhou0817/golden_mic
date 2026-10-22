---
name: golden-mic-api-change
description: 用于在金话筒后端新增或修改 HTTP 接口（FastAPI 路由）：先对照现有 V2 接口和文档约定，再处理错误码、Origin/CSRF、任务凭据 X-Task-Token 与匿名会话、请求体上限、版本条件 expected_revision、没有幂等键时怎样防重复提交、/api/config/limits 与前端常量同步、用模拟服务写测试、要同步的文档和配置模板。设计或实现 /api/tasks/{id}/ 下的接口（pretranscribe、align、apply、versions、restore、recordings、exports、start 等），或改它们的请求、响应、状态码时使用。不要在只改界面文案、只调 FFmpeg 参数或部署上线时使用。关键词：接口 API、endpoint、路由 router、错误码 status code、CSRF、capability、幂等 idempotency、重试 retry、限额 limits、FastAPI。
---

# 金话筒接口变更流程

新增或修改接口时，先找现有实现，再按本文的规则设计、写测试、实现、同步文档。规则都来自当前代码；文档和代码不一致的地方列在"待确认"，不要自行裁决。

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
- **§1.4 的接口大多已经存在。** 交接文档 §12 列为"需新增后端"的 pretranscribe、align、apply（剪短 `trims`、换段 `replaces`、转旁白 `to_narration`、改说话人 `speakers`）、versions、restore、recordings、exports，代码里都已经有了，见 [references/endpoints.md](references/endpoints.md)。**先扩展现有接口，不要另起一套。**
- **路由挂载。**
  - 路由由各模块的工厂函数创建：`create_draft_router`（`drafts.py`）、`create_workbench_router`（`workbench.py`）、`create_v2_editing_router`（`v2_editing.py`）、`create_upload_router`（`uploads.py`）。它们在 `main.py:1293-1330` 挂载，必须排在兜底路由 `/api/{unmatched_path:path}`（`main.py:1532`）之前。
  - 工厂是惰性的：导入模块或生成 OpenAPI 时不能打开存储（`main.py:88-91`）。
- **鉴权回调** 的签名是 `authorize(request, task_id, write=False)`（`main.py:844-860`）。
  - V2 任务即使从本机访问也必须带 `X-Task-Token`（`main.py:828-833`）。
  - 凭据有误一律返回 404；能证明任务已删除时返回 410 `{"code":"task_gone"}`（`main.py:801-842`）。
  - 后端不返回 401。

## 规则

### 1. 鉴权、Origin 与 CSRF
- **写路径要在前缀列表里。** 排空（503）、Origin 检查（403）、生产环境 CSRF（403，响应头 `X-Anonymous-Session: required`）都按前缀生效，列表在 `main.py:1211-1235`。新增顶层前缀时要同步这些列表。
- **新的自定义请求头** 要加进 CORS 的 `allow_headers`（`main.py:322`）。
- **读完请求体后再复核一次授权**，写法参照 `main.py:873-876`、`workbench.py:1034`。
- **不新增 401。** 凭据失败返回 404，不暴露任务是否存在。

### 2. 请求体
- **长度和上限。** `/api/tasks/` 下的写请求必须带 Content-Length，缺了返回 411。默认上限 256 KiB，`recordings` 为 21 MiB，分片 PUT 为 8 MiB，超限返回 413（`main.py:247-262`）。
- **读取方式。** JSON 要用有上限、有超时的读取函数：`main._bounded_json`、`drafts.bounded_json`、`v2_editing.parse_request`。
- **请求模型** 用 strict、禁止额外字段，参照 `ApplyRequest`（`v2_editing.py:165-192`）。
- **OpenAPI。** 手动读取请求体的路由，要用 `openapi_extra` 声明请求体（`main.py:622-626`），或登记进 `_openapi_document`（`main.py:1501-1508`）。生产环境关闭 OpenAPI（`config.py:202-203`）。

### 3. 状态码和错误体
- **按约定选状态码**，不新造含义。对照表见 [references/status-codes.md](references/status-codes.md)。
- **错误体** 是 `{"detail": 字符串或对象}`。需要前端区分的错误用 `detail.code`，并在 `frontend/src/lib/appApi.ts:136-148` 加中文文案。
- **Retry-After。** 429、503、507 带 `Retry-After`（秒）。前端会解析这个头并提示用户手动重试（`appApi.ts:149-170`）。

### 4. 没有幂等键：防重复提交
- **现有去重方式。** 现有接口都没有 `Idempotency-Key`，靠状态检查、版本条件和账本去重：
  - start 用 SQLite 账本，重复接收返回 409 `already_accepted`（`admission.py:49-52`）；
  - apply、checks、exports、metadata 要求 `expected_revision`，不符返回 409 `stale_revision`；
  - pretranscribe 必须显式传 `{"retry": true}`；
  - 分片 PUT：同一索引、相同字节可以重放，字节不同返回 409。
- **新的收费或改状态的 POST**，必须有上面至少一种防护，推荐 `expected_revision` 或状态检查。返回 202 加 `operation_id` 或 `status`，由客户端轮询状态。
- **响应不明时先查状态。** 超时、断网时，客户端只能先 GET 状态，**不能自动重发**（`appApi.ts:317` 注释 "Mutations are never replayed"；`workbenchApi.ts:4`）。服务端和客户端都不加自动重试收费调用的逻辑。
- **真要加幂等键** 属于新设计：先和用户确认命名、存储、有效期，以及它和 `expected_revision` 的关系。

### 5. 限额与常量
- **前端以 `GET /api/config/limits` 返回的值为准**（`docs/CONFIGURATION.md:83`）。
- **新增或修改限额时：**
  1. 规则阈值放在 `backend/mode_rules.json`，前端 `productionModes.ts` 直接导入同一个文件；
  2. 运行时配置放在 `backend/config.py` 的 `Settings`，并同步两份配置模板（见下方同步清单）；
  3. 对外暴露时改 `PublicLimitsResponse`（`models.py:470-501`）和 `main.py:359-376`，同时同步前端的 `PublicLimits` 类型（`frontend/src/types.ts:35-66`）和 `getPublicLimits` 校验（`appApi.ts:212-238`）。
- **注意现状。** `models.py:478-496`、`drafts.py:1091`、`main.py:364` 里写死了和 `mode_rules.json` 相同的数值（0.85、0.6、30 秒、8000 字等）。改阈值时要一起改，或先问用户是否收敛到 `mode_rules.json`。

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
- [ ] **配置字段的副作用。** RetrySame 的 `_retry_binding` 包含完整的 `settings.model_dump`。新增任何字段，都会让此前失败的任务在 RetrySame 时返回 409 `retry_inputs_changed`（`drafts.py:305-340`）。要在交付说明里写明。
- [ ] **前端：**
  - `appApi.ts` 的路径白名单和超时（第 290–302 行），`detail.code` 文案；
  - `PublicLimits` 类型和校验；
  - `workbenchApi.ts`、`uploadSessions.ts` 的回执解析。
- [ ] **发布白名单。** 新的可发布文档要加进 `deploy/build_release.py` 的 `INCLUDED_FILES`，同样要先经批准。随包发布的文档只能链接包里有的文件，否则 `tests/test_v2_release_integrity.py` 会失败。
- [ ] **回归测试。** 补一个 `tests/test_v2_*.py`，不弱化原有的鉴权、时序、取值范围、版本和资源断言（`docs/DEVELOPMENT.md` 第 9 节）。

## 示例：给 `/align` 加一个返回字段

1. **现状。** `POST /api/tasks/{id}/align` 只做预览，不提交编辑（`drafts.py:1086-1091`）。返回 `{matches, match_ok, match_low, local_speech, rules_version}`，`matches[]` 每行有 `idx, kind, source, alt_takes, score, upload_id, start, end, speaker_id, asr_text`。
2. **设计。** 新字段加在 `matches[]` 的行里；旧客户端会忽略未知字段，所以不改状态码。
3. **测试。** 在 `tests/test_v2_drafts.py` 里照现有的 align 用例加断言。ASR 结果用测试里现成的合成转写，不调用真实 ASR。
4. **前端。** 在 `workbenchApi.ts` 或 `appApi.ts` 的回执解析里接受新字段。
5. **文档。** 更新 `docs/README.md` 的接口说明；不改带日期的 `THREE_MODE_API_20260928.md`。

## 验证

- 运行 golden-mic-verify 的命令 B（受保护的后端回归）。改了前端时，征得同意后运行命令 C。
- 自查：
  - 新接口在没有 token、token 错误、`expected_revision` 过期、请求体超限这四种情况下，分别返回 404、404、409、413；
  - 测试里没有构造任何真实的服务商客户端。

## 待确认（代码和文档不一致，不要自行裁决）

1. **401。** 文档写"propagate host 401/403/404"（`WORKBENCH_API.md:116`），但后端从不返回 401。
2. **403 后重试。** "403 后前端重试一次"只出现在已退役的 `CLASSROOM_API.md:279`；现行前端遇到 403 只清掉会话，不重放请求。
3. **pretranscribe 的语义。** 任务书 §1.4 写的是"转写 + 区分说话人 + 词级时间戳，耗时不超过素材时长 × 0.3，正式制作复用结果"。现有的 `pretranscribe` 接口只用来重试失败的转写，首次转写在上传完成后自动进行（`uploads._transcribe`）。
4. **字段名。** 任务书里 `/align` 的 `t0/t1/spk/alts`，在代码里叫 `start/end/speaker_id/alt_takes`；`t0/t1` 只出现在 apply 的 `trims` 里。
5. **限额写死。** limits 和对齐阈值在 `models.py`、`drafts.py`、`main.py` 里写死了，和 `mode_rules.json` 是两份副本。
6. **Retry-After 没写进文档。** 代码在 429、503、507 上都会设置，现行文档没有提。
7. **restore 的版本条件。** v2 restore 的 `expected_revision` 可以省略，省略时没有过期保护（`v2_editing.py:160-162`）。
8. **请求体读取超时。** `_bounded_json` 遇到慢请求体超时，可能返回 500 而不是 408（推断，未测）。
9. **排空范围。** 排空列表不包括 `/api/script/*`（`main.py:1217`）。
10. **start 的超时。** 前端 `/start` 超时 30 秒，服务端处理可能更久。超时后必须先读状态，不能重发。

## 参考

- [references/status-codes.md](references/status-codes.md)：状态码、触发位置、Retry-After 和前端处理
- [references/endpoints.md](references/endpoints.md)：现有 V2 接口表，以及 align、pretranscribe、apply、start、recordings、exports 的请求和响应
- [references/testing.md](references/testing.md)：新接口测试的三种写法
- 来源：`backend/main.py`、`drafts.py`、`workbench.py`、`v2_editing.py`、`uploads.py`、`admission.py`、`anonymous_access.py`、`frontend/src/lib/appApi.ts`、`workbenchApi.ts`、`uploadSessions.ts`、`docs/README.md`、`docs/DEVELOPMENT.md`、`docs/CORE_WORKSPACE_20260927.md`、`docs/THREE_MODE_API_20260928.md`、`docs/WORKBENCH_API.md`。2026-10-21 按工作区代码核对，行号会变，以函数名为准。
