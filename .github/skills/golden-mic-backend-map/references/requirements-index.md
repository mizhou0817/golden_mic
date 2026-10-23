# 需求 → 代码位置

对照交接文档 §12 原先标为"需新增后端"的条目，以及任务书 §1.4 的后续工作。2026-10-21 按工作区代码核对；2026-10-22 交接文档 §12 已按本表更新。

状态说明：**已有** 表示功能已实现；**部分** 表示有实现但和需求描述不一致；**未实现** 表示代码里找不到。

| 需求 | 状态 | 代码位置 | 和需求描述的差异 |
|---|---|---|---|
| 预转写：转写、区分说话人、词级时间戳，正式制作复用 | 部分 | 上传完成后自动执行：`uploads.complete` → `_preprocess` → `_transcribe`（`uploads.py:846-879`、`uploads.py:1085-1136`）。说话人用 ASR 返回的 `speaker_id`，另有可选的本地声纹。词级时间戳是 `Word(w, s, e)`，不可靠时降为 segment 精度（`uploads.py:1468-1495`）。复用：开始制作时写入 `pretranscripts.json`，阶段 2 直接读取 | `POST …/pretranscribe` 只用来重试失败的转写。"耗时不超过素材时长 × 0.3"在代码里不存在：ASR 超时 = max(5 秒, min(7200 秒, 0.5 × 时长))，而且这只是超时，不是性能保证 |
| 强制对齐：返回 score/t0/t1/spk/alts | 已有 | `POST /{id}/align` → `DraftService.align`（`drafts.py:1088-1106`）→ `production_modes.align_quotes`；另有 `/api/match/preview` | 字段名不同：`score`、`start`、`end`、`speaker_id`、`alt_takes`（最多 5 条） |
| 剪短原声 | 已有 | `ApplyRequest.trims{row: {t0, t1}}` → `validate_quote_trim`（`production_modes.py:1234-1267`） | 只能缩短，至少 1 秒，不能切进词里；segment 精度的原声不能剪 |
| 换一段 | 已有 | `ApplyRequest.takes`（`v2_editing.py:173`），只能选当前的 `alt_takes`；候选列表 `GET /{id}/quotes/{row}/takes`（`main.py:1413-1422`） | — |
| 转成旁白 | 已有（仅 B 模式） | `ApplyRequest.to_narration`（`v2_editing.py:391-417`）；`mode_pipeline.py:2070-2085` | 从阶段 6 开始重跑（2026-10-22 起 `mode_rules.json` 的 `plan_stages` 也写 6） |
| 改说话人 | 已有 | `ApplyRequest.speakers{id: {name ≤ 8, role ≤ 12}}`（`v2_editing.py:151-153`） | 从阶段 8 开始重跑 |
| 多处修改一次提交 | 已有 | `POST /{id}/apply`（`workbench.py:1364-1382`），回执写入 `v2_operations` | 一次 apply 产生一个快照，revision 一次跳过"步数"个号 |
| 版本历史与回滚 | 已有 | `GET /{id}/versions…`、`POST /{id}/restore`（`workbench.py:1321-1350`、`workbench.py:1451-1458`） | 路径是 `/api/tasks/{id}/…`，不是 `/workbench/…`；回滚是复制快照，不重新合成 |
| 录音上传，服务端对齐 | 已有 | `POST /{id}/recordings` → `_ingest_recording`（`workbench.py:937-1017`）；apply 时由 `_recorded_narration` 对齐（`mode_pipeline.py:1149-1189`） | 交接文档说的"每句 3 秒"在后端找不到；原声句不接受录音 |
| 逐句来源 `media_origin` | 已有 | `workbench.py:394-406` 写入，`GET /report` 返回 | — |
| 队列位置 | 已有 | `task_manager.py:435`；start 的回执里有 `queue` | — |
| 预计时间（ETA） | 未实现 | — | 交接文档明确不给 ETA，只用阶段权重 |
| 到期倒计时 | 部分 | 后端给出 `expires_at` 时间戳（`task_metadata.py:52-66`、`drafts.py:585-587`） | 倒计时要前端自己算；修改会重置到期时间 |
| 人名条烧录 | 已有 | `graphics.generate_mode_graphics`：首次显示 2.5 秒，同一人间隔 60 秒以上再显示，未命名显示"受访者"（`graphics.py:387-453`） | — |
| 跳切遮盖 | 已有 | 判定：`production_modes.calculate_jumpcuts`；渲染：`mode_pipeline.build_mode_edl`。细节见 golden-mic-media | — |
| 样片水印烧录 | **未实现** | 后端没有 watermark 或 drawtext；只有前端用 CSS 叠加（`ResultWorkbench.tsx:898`、`ResultWorkbench.css:337-338`） | 而且 `acknowledge_unresolved` 可以导出不带水印的成片（`v2_editing.py:522-535`） |
| 五要素、镜头数预估 | 部分 | `production_modes.facts_of`（时间、地点、主办方、数字、称谓）和 `sentence_needs`（`production_modes.py:470-494`） | 没有调用点，也没有接口；"何时、何地、何人、何事、为何"五要素没有实现 |
| 服务端历史 | 部分 | 只有按 owner 列出 V2 作品（`main.py:414-423`、`admission.py:165`） | 没有账号体系 |
| 字幕样式 `caption_style` | 已有 | `models.py:190` | 交接文档附录 C-6 说需要新增，已经过时 |
