---
name: golden-mic-backend-map
description: 金话筒后端地图（背景知识，由模型自动加载）。内容包括：模块分工；V2 任务生命周期（草稿、分块上传、预转写、对齐、开始、十阶段制作、应用修改产生新版本、检查与导出、到期清理）；十个阶段与重做规则；三种模式 voiceover/mixed/original 的差异；限额与常量；数据保留时长；"需求 → 代码位置"索引；新增检查码、服务商、导出格式时的改动清单。用户问"这一步在哪段代码""改这里会影响什么""某功能实现了没有""阶段、模式、限额是多少""任务多久过期"时使用，开始任何后端改动或规划交接文档 §12 的后续功能前也要先用。只读参考，不要用来真实运行制作或修改配置。关键词：后端地图 backend map、流水线 pipeline、阶段 stage、重做 redo、模式 mode、生命周期 lifecycle、限额 limits、保留 retention、代码位置。
user-invocable: false
---

# 金话筒后端地图

帮你快速定位代码、评估改动的影响面。下面的内容按代码现状整理；交接文档或旧报告和代码冲突时，以代码为准；已更正和仍未消除的差异列在"文档与代码的差异"。

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
| 媒体 | `rendering.py`、`graphics.py`（字幕、人名条；AI 标识只给旧版本重建）、`subtitles.py`（字体常量和旧 ASS）、`audio_filters.py`、`music.py`、`studio_render.py`（导出） | 见 golden-mic-media |
| 规则 | `backend/mode_rules.json` | 模式、阶段名称、阈值、检查码，前后端共用 |
| 外部服务 | `backend/providers/` | 见 golden-mic-providers |
| 旧功能 | `studio.py`、`asr_pipeline.py`、`script_segmentation.py` 等 | 只按兼容范围维护，不恢复已下线的 Studio、课堂入口 |

## V2 任务生命周期

细节和出处见 [references/lifecycle.md](references/lifecycle.md)。

1. **建草稿**：`POST /api/tasks`，不带 script → `DraftService.create`。状态为 draft，闲置 24 小时到期。
2. **编辑稿件**，然后**分块上传**：每块 8 MiB，传完调用 complete。
3. **预处理和预转写**：probe → 波形和静音区间 → Seed ASR。**这一步就开始收费，早于开始制作。**
4. **对齐预览**：`POST /{id}/align`，只预览，不提交。
5. **显式开始**：`POST /{id}/start`。先校验原话（缺原话 422）和等待队列（最多 50 个，满了返回 503，不占额度）；再预留磁盘、复制素材、做说话人分析和匹配，最后复查一次队列容量；之后才写准入账本（每小时额度，已接收的不退，用完 429，重复 409）；然后入队。
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

实际执行的阶段由 `v2_editing.compile_plan` 计算（`v2_editing.py:211-259`、`workbench.py:600-606`），**不读** `mode_rules.json` 的 `plan_stages`。读它的只有后端 `production_modes.recommend_plan` 和前端 `recommendPlan`，两者除测试外都没有调用方；2026-10-22 起它的值和 `compile_plan` 一致（`tests/cases/text-rules.json` 有用例）。界面显示的是服务端回执里的阶段。

| 修改 | 重跑阶段 |
|---|---|
| 首版制作 | 1–10 |
| 删句、改字、本人录音、调语速、剪短原声、换一段 | 7–10 |
| 改字幕样式或人声增强（只有旧的 `POST /api/tasks/{id}/edit` 接受；V2 `/apply` 没有这两个字段） | 7–10 |
| 转成旁白（仅 B） | 6–10 |
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
- 原话匹配的状态：分数 ≥ 0.85 为 `ok`；0.6–0.85 为 `low`，发 `QUOTE_MATCH_LOW`（1 级，要人工确认）；< 0.6 或没有来源为 `missing`，发 `QUOTE_NOT_FOUND`（0 级，阻断发布）（`production_modes.py:496-500`）。
- 句型的优先级：手动标记 > 行首标记（"同期："、"【同期】"、"姓名（身份）："）> 默认旁白。分句：原声句按"。！？"拆，旁白按"，。；！？"拆（交接文档 §2；实现见 `production_modes.parse_sentences`）。

## 限额与常量（摘要）

完整清单，以及仍未消除的不一致，见 [references/limits.md](references/limits.md)。

- **素材**：文件最多 20 个；单个不超过 500 MiB，合计不超过 5 GiB；单个不超过 30 分钟，合计不超过 60 分钟；镜头最多 120 个；图片定格 3 秒。
- **稿件**：最多 8000 字，超过 3000 字给提示；标题不超过 40 字；最多 200 句，单句不超过 2000 字。
- **原声与节奏**：原声句时长 1 / 20 / 30 秒；语速 230 / 265 / 290 字每分钟，容差 ±8%；单句画面不超过 6.5 秒。
- **准入**：每小时按会话 / IP / 全站分别计数，默认 2 / 5 / 10 次，生产模板 10 / 30 / 60 次；V2 等待队列最多 50 个；单 worker。
- 前端以 `GET /api/config/limits` 返回的值为准，每个字段都取自执行处（见 golden-mic-api-change）。

## 数据保留（摘要）

- **草稿**：闲置 24 小时到期，每次操作续期，到期返回 410 `task_gone`（`draft_expired` 只在请求处理中途草稿恰好到期时出现）。
- **终态作品**：完成后 72 小时到期，**每次成功修改或恢复都会重置倒计时**。到期返回 410 `task_gone`，墓碑保留 30 天（凭访问令牌或同一会话才返回 410，否则 404）；手动删除的不写墓碑，之后返回 404。
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

## 步骤（用这张地图回答问题时）

1. 先在"需求 → 代码位置"和 [references/requirements-index.md](references/requirements-index.md) 里找到入口，再读那段代码确认现状，不凭文档下结论。
2. 判断影响面：改动落在哪个阶段（"十个阶段"）、会重跑哪些阶段（"重做规则"）、会不会增加外部调用或收费（见 golden-mic-providers）。
3. 涉及限额、保留时长或检查码时，到 [references/limits.md](references/limits.md) 核对数值；数值的来源是 `mode_rules.json` 和 `config.py`，不要在别处再写一份。
4. 要新增检查码、服务商或导出格式，照 [references/change-checklists.md](references/change-checklists.md) 逐项列出要改的文件。
5. 代码和文档冲突时，以代码为准，把差异写进汇报，不要自行裁决。

## 示例：问"转成旁白会重跑哪些阶段"

1. 在"重做规则"里查到：转成旁白（仅 B 模式）重跑阶段 6–10。
2. 读 `v2_editing.compile_plan` 确认（实际执行的阶段由它计算，不读 `mode_rules.json` 的 `plan_stages`）。
3. 回答：从阶段 6（找画面，调火山 Embedding 和 Kimi 精排、实体核验）到阶段 10；阶段 7 还会调 Seed TTS，所以这一步会再次收费。回答里写明依据的函数名，没读到代码的部分写"未核实"。

## 验证

- 回答里的每个函数名、常量和数值都能在代码里找到；找不到的写"未核实"。
- 行号会变，引用以函数名为准；换了代码后再引用，要重新核对。
- 这个 skill 只做定位和说明，不运行制作，也不改文件。

## 文档与代码的差异

2026-10-22 按维护者要求，交接文档正文和 `docs/` 下的现行文档已按代码更正：§12 的接口现状、转旁白从阶段 6 重跑、revision 计数、恢复不重新合成、后端不烧录水印与"仍然导出"、`QUALITY_GATE_MODE`、预转写超时、保留期、C 模式开始条件、偏好默认值、`errorKind`、墓碑、已删除的 canary 证据。

随后按维护者要求改了代码：`plan_stages` 的转旁白改为 6–10，和 `compile_plan` 一致；`admission.py` 的注释写明手动删除不留墓碑。只做记录的：

1. **`quote_caption` 的两个名字。** `mode_rules.json` 写 canonical 是 `spoken`；`models.py` 存的是 `asr`，并把 `spoken` 转成 `asr`；`production_modes.py` 又把 `asr` 转成 `spoken`。两个名字指同一种字幕，只是别名，不是缺陷（`models.py` 有注释）。
2. **附录里的原型。** 交接文档附录 A/B 是 2026-09-28 的原型源码，dev 标签、字段名（`t0`/`t1`/`spk`/`alts`）和 `gateMode` 等保持原样，现状以正文 §12 为准。

## 参考

- 交接文档（根目录的 `金话筒 · 新闻视频生成工具 - UI 设计交接文档（GPT-6 Astra）.md`）§1、2、6、7、8、10、12（2026-10-22 已按代码更正正文）。只在维护者要求时修改：`tests/run_v2_validation.py` 在每次运行前后比对它的 SHA-256，并解析附录 B 里含 `renderVals(){` 的代码块来统计设计索引，所以不要改附录 A/B/C，也不要在回归运行期间改它。
- `backend/mode_rules.json`、`drafts.py`、`task_manager.py`、`v2_editing.py`、`workbench.py`、`revisions.py`、`admission.py`、`uploads.py`、`production_modes.py`、`mode_pipeline.py`、`config.py`、`models.py`
- `docs/DEVELOPMENT.md` 第 2 节（那里没有列出 graphics、tts_pipeline、audio_filters、music、task_metadata、operations）
- 2026-10-21 按工作区代码核对，行号会变，以函数名为准。
