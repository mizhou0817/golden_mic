---
name: golden-mic-backend-map
description: 金话筒后端地图（背景知识，由模型自动加载）。内容包括：模块分工；V2 任务生命周期（草稿、分块上传、预转写、对齐、开始、十阶段制作、应用修改产生新版本、检查与导出、到期清理）；十个阶段与重做规则；三种模式 voiceover/mixed/original 的差异；限额与常量；数据保留时长；"需求 → 代码位置"索引；新增检查码、服务商、导出格式时的改动清单。用户问"这一步在哪段代码""改这里会影响什么""某功能实现了没有""阶段、模式、限额是多少""任务多久过期"时使用，开始任何后端改动或规划交接文档 §12 的后续功能前也要先用。只读参考，不要用来真实运行制作或修改配置。关键词：后端地图 backend map、流水线 pipeline、阶段 stage、重做 redo、模式 mode、生命周期 lifecycle、限额 limits、保留 retention、代码位置。
user-invocable: false
---

# 金话筒后端地图

帮你快速定位代码、评估改动的影响面。下面的内容按代码现状整理；交接文档或旧报告和代码冲突时，以代码为准，冲突列在"待确认"。

## 何时使用 / 不要使用

- 使用：
  - 定位模块和函数；
  - 判断改动影响哪些阶段、版本、缓存和费用；
  - 确认某个需求是否已经实现；
  - 查限额、保留时长和模式规则。
- 不要使用：
  - 真实运行制作或调用服务（需要用户另行授权）。
  - 以下内容各有专门的 skill：
    - 验证：golden-mic-verify；
    - 接口细节：golden-mic-api-change；
    - 外部服务：golden-mic-providers；
    - 渲染和音频参数：golden-mic-media；
    - 检查码含义和新闻真实性红线：gm-news-integrity。

## 模块分工

| 范围 | 模块 | 职责 |
|---|---|---|
| HTTP 与启动 | `main.py`、`config.py`、`preflight.py`、`readiness.py` | 路由挂载、中间件（排空、Origin、CSRF、请求体上限）、会话、`/api/config/limits`、预检 |
| V2 生命周期 | `drafts.py`、`admission.py`、`uploads.py`、`task_manager.py`、`task_metadata.py`、`operations.py` | 草稿、分块上传、align、start、retry、恢复；SQLite 准入账本；上传会话和预转写；单 worker 队列、状态和重启恢复；到期时间；磁盘预留 |
| 制作流水线 | `pipeline.py`（入口和旧流水线）、`mode_pipeline.py`（V2 编排、十个阶段、改后重算）、`production_modes.py`（分句、原话对齐、检查、跳切、切点）、`matching.py`、`assignment.py`、`vision_pipeline.py`、`tts_pipeline.py`、`media_input.py`、`media.py` | 十阶段制作 |
| 编辑与发布 | `v2_editing.py`（apply 请求、`compile_plan`、导出路由）、`workbench.py`（版本、应用、录音、恢复）、`revisions.py`（版本快照）、`publication.py`（发布门禁）、`quality.py`（质检） | 修改出新版本、检查、导出 |
| 媒体 | `rendering.py`、`graphics.py`（字幕、人名条、AI 标注）、`subtitles.py`（字体常量和旧 ASS）、`audio_filters.py`、`music.py`、`studio_render.py`（导出） | 见 golden-mic-media |
| 规则 | `backend/mode_rules.json` | 模式、阶段名称、阈值、检查码，前后端共用 |
| 外部服务 | `backend/providers/` | 见 golden-mic-providers |
| 旧功能 | `studio.py`、`asr_pipeline.py`、`script_segmentation.py` 等 | 只按兼容范围维护，不恢复已下线的 Studio、课堂入口 |

## V2 任务生命周期

细节和出处见 [references/lifecycle.md](references/lifecycle.md)。

1. **建草稿**：`POST /api/tasks`，不带 script → `DraftService.create`。状态为 draft，闲置 24 小时到期。
2. **编辑稿件**，然后**分块上传**：每块 8 MiB，传完调用 complete。
3. **预处理和预转写**：probe → 波形和静音区间 → Seed ASR。**这一步就开始收费，早于开始制作。**
4. **对齐预览**：`POST /{id}/align`，只预览，不提交。
5. **显式开始**：`POST /{id}/start`。缺原话返回 422；先过准入账本（每小时额度，已接收的不退），等待队列最多 50 个；然后入队。
6. **执行十个阶段**：单 worker。状态依次为 queued → running → done、failed 或 cancelled。**重启后不续跑**：queued 和 running 一律改为 failed。
7. **修改**：`POST /{id}/apply` → `compile_plan` → 产生新版本。失败时保留上一版。
8. **检查与导出**：经过 `publication_gate`，再 `POST /{id}/exports`。
9. **到期清理**：草稿闲置 24 小时；终态作品在完成后 72 小时到期，修改或恢复会重置倒计时；之后返回 410 `task_gone`，墓碑保留 30 天。

## 十个阶段

各阶段的代码、配置和测试见 [references/stages.md](references/stages.md)。

| # | 名称（A / B / C，取自 `mode_rules.json`） | 外部服务 |
|---|---|---|
| 1 | 检查素材 | 无（ffprobe） |
| 2 | 听素材里的声音 / 听采访 / 听采访 | 只给失败的素材补跑一次 Seed ASR |
| 3 | 切分镜头 / 切分空镜 / 找空镜 | 无 |
| 4 | 看懂每个画面 | Kimi（没有空镜时跳过） |
| 5 | 给稿子分句 / 分旁白和原声 / 对稿子 | 无（V2 不调用模型） |
| 6 | 给每句话找画面 / 配画面、对原话 / 找原话 | 火山 Embedding、Kimi 精排和实体核验；可选生成补画面。原话对齐在本地 |
| 7 | 配音 / 配音 / 整理原声 | Seed TTS、Kimi 读音规划；C 模式不配音 |
| 8 | 做字幕 | 无 |
| 9 | 合成视频 | 无（FFmpeg） |
| 10 | 质量检查 | 无 |

阶段权重：A 全部为 1；B 为 [1,2,1,1,1,2,1,1,1.5,1]；C 为 [1,3,1,0.5,1,2.5,1,1,2.5,1]。不提供预计时间（`mode_rules.json` 的 `stage_weights_raw`）。

## 重做规则（改后重跑哪些阶段）

实际执行的阶段由 `v2_editing.compile_plan` 计算（`v2_editing.py:210-258`、`workbench.py:600-606`），**不读** `mode_rules.json` 的 `plan_stages`；后者只给前端显示用。

| 修改 | 重跑阶段 |
|---|---|
| 首版制作 | 1–10 |
| 删句、改字、本人录音、调语速、剪短原声、换一段、改字幕样式或人声增强 | 7–10 |
| 转成旁白（仅 B） | **6**–10（`mode_rules.json` 和交接文档写的是 7–10，以代码为准） |
| 只改说话人 | 8–10 |
| 空的 apply（只重新合成） | 9–10 |
| 换画面（每句一步） | 6、9、10 |
| 恢复旧版本 | 不重跑，复制旧快照发布为新版本 |

一次 apply 只产生一个快照，revision 一次跳过"步数"个号。版本最多 100 个，超出返回 409。

## 三种模式

| 代号 | 界面名 | 允许的句型 | 要点 |
|---|---|---|---|
| `voiceover`（A） | AI 配音 | 只有旁白 | 不把素材原声自动变成同期声 |
| `mixed`（B） | 旁白 + 原声 | 旁白和原声（`quote`），默认旁白 | 只有 B 能把原声转成旁白 |
| `original`（C） | 只用原声 | 只有原声 | 不调用 TTS；强制 `voice=ai`、`pacing=normal`、`generative_fill=False`；开始制作时拒绝不连续的原话（422 `quote_unverified`） |

- 原声句**不能改字**，只能剪短、换一段、转成旁白（仅 B）或删掉。
- 原话匹配的状态：分数 ≥ 0.85 为 `ok`；0.6–0.85 为 `low`，发 `QUOTE_MATCH_LOW`（1 级，要人工确认）；< 0.6 或没有来源为 `missing`，发 `QUOTE_NOT_FOUND`（0 级，阻断发布）（`production_modes.py:484-488`）。
- 句型的优先级：手动标记 > 行首标记（"同期："、"【同期】"、"姓名（身份）："）> 默认旁白。分句：原声句按"。！？"拆，旁白按"，。；！？"拆（交接文档 §2；实现见 `production_modes.parse_sentences`）。

## 限额与常量（摘要）

完整清单，以及和交接文档 §10 的差异，见 [references/limits.md](references/limits.md)。

- **素材**：文件最多 20 个；单个不超过 500 MiB，合计不超过 5 GiB；单个不超过 30 分钟，合计不超过 60 分钟；镜头最多 120 个；图片定格 3 秒。
- **稿件**：最多 8000 字，超过 3000 字给提示；标题不超过 40 字；最多 200 句，单句不超过 2000 字。
- **原声与节奏**：原声句时长 1 / 20 / 30 秒；语速 230 / 265 / 290 字每分钟，容差 ±8%；单句画面不超过 6.5 秒。
- **准入**：每小时按会话 / IP / 全站分别 2 / 5 / 10 次；V2 等待队列最多 50 个；单 worker。
- 前端以 `GET /api/config/limits` 返回的值为准，其中一部分字段是写死的（见 golden-mic-api-change）。

## 数据保留（摘要）

- **草稿**：闲置 24 小时到期，每次操作续期，到期返回 410 `draft_expired`。
- **终态作品**：完成后 72 小时到期，**每次成功修改或恢复都会重置倒计时**。到期返回 410 `task_gone`，墓碑保留 30 天；手动删除的不写墓碑，之后返回 404。
- **上传会话**：从创建起最长 72 小时。导出文件随任务一起删除。
- **磁盘不足时可能提前删除**：先删已完成作品的上传副本，再删其他草稿的素材，最后删最早的已完成作品。
- **清理循环**：每 3600 秒运行一次。

## 需求 → 代码位置（摘要）

完整索引和差异见 [references/requirements-index.md](references/requirements-index.md)。

- **已有**：align、剪短、换段、转旁白、改说话人、多处修改一次提交（apply）、版本与回滚、录音上传加服务端对齐、`media_origin`、队列位置、人名条烧录、跳切遮盖。
- **部分实现**：
  - 预转写：上传后自动进行；端点只用于重试失败的转写；超时是 0.5 × 时长，没有"0.3 ×"的保证；
  - 到期倒计时：后端只给 `expires_at`；
  - 五要素和镜头数预估：有函数但没有调用点；
  - 服务端历史：只按 owner 列出，没有账号。
- **未实现**：ETA；**样片水印烧录**（目前只有前端 CSS 叠加）。

## 改动清单

新增发布检查码、新增 AI 服务商、新增导出格式时，要同步修改的文件见 [references/change-checklists.md](references/change-checklists.md)。

## 待确认（交接文档或其他文档与代码不一致，不要自行裁决）

1. **§12 大多已实现。** 交接文档 §12 列为"需新增后端"的功能大多已经存在，路径和字段名也不同（`/api/tasks/{id}/…`，`start`/`end`/`speaker_id`/`alt_takes`）。
2. **转旁白的重跑阶段。** 交接文档 §7、§9 和 `mode_rules.json` 写 7–10，代码是 6–10；交接文档第 2061 行自己也写 6–10。
3. **revision 计数。** 文档写"每一步一次调用、revision + 1"；代码是一次 apply 跳过"步数"个号，只产生一个快照。
4. **恢复是否重新合成。** 文档写"恢复 = 再重新合成一次"；代码只复制旧快照。
5. **样片水印与导出门禁。** 交接文档要求烧录样片水印、未通过门禁只能导出样片；后端没有水印，而且 `acknowledge_unresolved` 可以导出不带水印的成片。
6. **门禁默认值。** 文档写 `gateMode=block` 是现有行为；代码默认是 `warn`（`config.py:115`）。
7. **`quote_caption` 的规范值。** `mode_rules.json` 写 canonical 是 `spoken`；`models.py` 存的是 `asr`，并把 `spoken` 转成 `asr`；`production_modes.py` 又把 `asr` 转成 `spoken`。
8. **预转写的耗时。** 文档写不超过素材时长 × 0.3；代码的超时是 0.5 ×。
9. **保留承诺。** 交接文档 §1 写"稿子和素材永远保留"，实际是 24 小时 / 72 小时，还可能提前清理。
10. **C 模式的开始条件。** 文档要求全部"对上了"（≥ 0.85）；后端只拒绝 < 0.6 和不连续的原话。
11. **偏好默认值。** `EditingPreferences` 的 `background_music`、`motion_effects`、`enhance_speech` 默认都是 False；交接文档 §5.4 写 A/B 模式默认开启。前端必须发送完整的偏好。
12. **墓碑。** `admission.py:7-8` 说删除会保留墓碑，但手动删除实际不写。
13. **失效链接。** `docs/README.md:61` 引用的 canary 证据文件已在 2026-10-21 按用户要求删除，不要重建。

## 来源

- 交接文档（根目录的 `金话筒 · 新闻视频生成工具 - UI 设计交接文档（GPT-6 Astra）.md`）§1、2、6、7、8、10、12。**这个文件只读**：`tests/run_v2_validation.py` 会记录它的 SHA-256。
- `backend/mode_rules.json`、`drafts.py`、`task_manager.py`、`v2_editing.py`、`workbench.py`、`revisions.py`、`admission.py`、`uploads.py`、`production_modes.py`、`mode_pipeline.py`、`config.py`、`models.py`
- `docs/DEVELOPMENT.md` 第 2 节（那里没有列出 graphics、tts_pipeline、audio_filters、music、task_metadata、operations）
- 2026-10-21 按工作区代码核对，行号会变，以函数名为准。
