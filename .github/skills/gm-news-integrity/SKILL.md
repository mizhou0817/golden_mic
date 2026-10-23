---
name: gm-news-integrity
description: 金话筒新闻真实性与发布门禁的背景知识（news integrity / publish checks / 发布前检查）。内容包括三种模式的声音规则、稿件与原声的红线、AI 示意画面披露、20 个检查码（级别、能否确认、产生位置）、与红线冲突的现状，以及禁止的捷径。修改稿件处理、原声剪辑、匹配、质量检查、发布检查、导出、AI 画面相关代码，或讨论"能不能导出""检查码""原话""同期""AI 画面""豁免"时自动参考。
user-invocable: false
---

# 金话筒新闻真实性与发布门禁

本文档描述 2026-10-22 的代码现状，并标出与维护者红线冲突的地方。常驻规则见 [.github/copilot-instructions.md](../../copilot-instructions.md)；本文只写细节和流程。

## 何时使用 / 不要使用
- 使用：改稿件处理、原声剪辑、匹配、质量检查、发布检查、导出或 AI 画面相关代码；回答“这个检查能不能确认”“为什么不能导出”；评审可能放宽红线的改动。
- 不要使用：
  - 改界面文案：见 gm-ux-copy。
  - AI 内容标识的合规设计：见 gm-ai-labeling。
  - 新增或修改接口：见 golden-mic-api-change。
  - 声称完成前的验证：见 golden-mic-verify。

## 三种模式（`backend/mode_rules.json` 的 `modes`）

| 代码 | 界面名 | 官方说明 | 允许的句子类型 |
|---|---|---|---|
| voiceover | AI 配音 | 稿子全由 AI 读，素材只出画面 | 只有旁白 |
| mixed | 旁白 + 原声 | 旁白 AI 读，人说话的地方直接用现场原声 | 旁白、原话 |
| original | 只用原声 | 稿子就是采访原话，直接剪出来 | 只有原话；不调用 TTS，可从转写里挑句子 |

B 模式稿件里原话的写法：`同期 姓名（身份）：原话`（[USER_GUIDE](../../../docs/USER_GUIDE.md)）。

## 红线和现状

### 1. 稿件原文
- 分句时 LLM 只能插入断句符 ` / `，不得增删改任何字（`providers/llm.py` 的提示词，以及 `script_segmentation.reconcile_segmented_script` 的逐字校验）。这条守卫**只用于旧流水线**；V2 第 5 步是规则分句（`production_modes.parse_sentences`），不调用 LLM。
- **现有功能**（原定红线是"不用 AI 写稿、改写或润色"；2026-10-22 维护者要求文档以代码为准，是否保留由维护者决定，不要扩大范围）：A/B 模式有"AI 写一稿"和"润色 / 精简 / 更适合口播 / 语气更庄重"（`backend/script_assist.py`、`frontend/src/components/ScriptAssistant.tsx`，[USER_GUIDE](../../../docs/USER_GUIDE.md)"用 AI 写稿或润色"一节）。B 模式的原话行和分句器用同一个解析（`production_modes.parse_line`）：以"同期"或"【同期】"开头的行、"姓名（身份）："开头的行都算原话；A 模式整稿都按旁白处理，只有明确写着"同期"的行才算原话（`production_modes.has_sync_mark`）；写稿时 AI 写出的原话行会被删掉，润色时必须逐字保留、顺序不变，否则整个结果作废（2026-10-22 修复）。AI 仍可能写错人名、数字和日期。手动标成原声的句子（类型标记，文字里没有行首前缀）：B 模式润色时，前端把当前所有原声句（含手动标的）作为 `quotes` 一起发给 `/api/script/assist`，提示词逐条列出；结果里每一句都必须仍是分句器切出的完整一句、顺序不变，否则整个结果作废（`script_assist._keeps_in_order`）。采用或撤销后，前端按文字把它们重新标回原声（`productionModes.restoreQuoteMarks`）。2026-10-23 修复，之前这些句子对 AI 不可见。
  - 现有约束：由记者主动触发；只改旁白；不得编造事实；不改任何原话行；标题不超过 40 字、不以句末标点结尾；数字有变化时提示。
  - 改动这个功能时：不要扩大 AI 改写的范围；判断原话行必须继续复用 `production_modes.parse_line` 和 `has_sync_mark`，不要另写正则。

### 2. 原声
- 只能在原区间内按真实词边界连续剪短：至少 1 秒，不能剪开一个词，"句段精度"的原声不能剪；剪完保留连续的 `words`/`asr_text` 片段和原始匹配分数（`production_modes.validate_quote_trim`）。时间是原始上传文件里的绝对秒数。
- 编辑不能改原话文字（`workbench.py`、`mode_pipeline.py`）。换一段原声时，旧的那段留在 `alt_takes` 里，最多 5 段备选。
- C 模式用 QUOTE_NONCONTIGUOUS 拦截不连续的原话。
- 剪短后，当前版本只保存剪过的证据，完整证据留在上一个版本里。

### 3. AI 示意画面
- 默认关闭。要同时满足三个条件才会生成：全局 `GENERATIVE_FILL_ENABLED`、配置了 Key、本任务勾选。默认生成的是**视频**（Seedance）。C 模式不可用；涉及实体、日期、机构的事实句子会跳过。
- 画面上烧录 AI 标注，放在 `graphics.ass` 里，和字幕开关无关；导出时总会烧录 graphics：片头 3 秒"本片含AI生成内容"，每个 AI 镜头期间"AI生成示意画面"，每个字符（包括"AI"）字高都不低于最短边的 5%，不遮挡标题（见 gm-ai-labeling）。
- 含 AI 披露的作品，服务端用 422 拒绝导出 MP3/SRT（`v2_editing.py`）；含 AI 画面的作品，界面上也不再给这两个选项（`ResultWorkbench.tsx`）。
- AI 句子会同时触发 `generated_media`（可确认）和不可确认的 MATCH_FALLBACK。
- 标识是否符合国家标准，见 gm-ai-labeling。
- 作品保留制作时那一代 graphics.ass，导出时按存档字节重新生成（`graphics.read_disclosure_generation`）：2026-10-22 之前是第一代（只有 AI 镜头期间的小标识），2026-10-22 起第二代加了片头标识，2026-10-23 起第三代放大了"AI"、不遮挡旧流水线的长标题。

### 4. 发布前检查与导出
- 级别 0 是错误，不能确认，唯一例外是 `generated_media`（`acknowledgeable: true`）。级别 1 是警告，逐项确认；确认键绑定"版本号 + 报告哈希"，版本一变就失效，过期的版本号返回 409。级别 2 只做提示，不计入待确认。
- 常规导出要同时过 `check_qc` 和 `require_publication`。
- **维护者决定保留的行为**（2026-10-22 确认；不要扩大范围，也不要新增其他绕过路径）：V2 的"仍然导出（带未通过的检查）"（`acknowledge_unresolved: true`，在 `v2_editing.py` 的 `ready()` 里）会跳过 `check_qc` 和 `require_publication`，**连级别 0 的错误也跳过**。任务状态、版本、忙碌状态、AI 披露、源和输出绑定这些检查仍然有效。
  - 这条路径来自 2026-10-08 的提交 6310015。2026-10-22 起，README、`docs/USER_GUIDE.md`、`docs/CONFIGURATION.md`、`docs/RUNBOOK.md`、`docs/TROUBLESHOOTING.md`、`docs/DESIGN-MAP.md` 和交接文档都已写明这条路径。
  - 不要新增或扩大任何绕过路径；不能让披露检查被跳过；改动这条路径要先问用户。

## 检查码（`mode_rules.json`：`checks` 17 个，`safety_checks` 3 个）

| 检查码 | 级别 | 能否确认 | 主要产生位置 |
|---|---|---|---|
| MATCH_FALLBACK | 0 错误 | 否 | `quality.py`（界面显示的文字由 quality 写死） |
| EXPLICIT_ENTITY_NOT_COVERED | 0 | 否 | `quality.py` |
| FREEZE_PAD_EXCESSIVE | 0 | 否 | `quality.py`（定格超过 0.3 秒） |
| generated_media | 0 | **能** | `publication.py` |
| QUOTE_NOT_FOUND | 0 | 否 | `production_modes.py` |
| QUOTE_MATCH_LOW | 1 警告 | 能 | `production_modes.py`（匹配度低于 85%） |
| QUOTE_TOO_LONG | 1（超过 30 秒为 0） | 能 | `production_modes.py` |
| QUOTE_AUDIO_NOISY | 1 | 能 | `production_modes.py`（信噪比低于 18 dB） |
| QUOTE_TEXT_DIFFERS | 1 | 能 | `production_modes.py` |
| SPEAKER_UNNAMED | 1 | 能 | `production_modes.py`（开启人名条时） |
| JUMP_CUT_UNCOVERED | 1 | 能 | `production_modes.py` |
| LOW_MATCH_CONFIDENCE | 1 | 能 | `quality.py` |
| VISUAL_CLIP_TOO_LONG | 1 | 能 | `quality.py`（超过 6.5 秒） |
| NARRATION_SPEAKING_RATE | 1（门禁设为 block 时为 0） | 能 | `quality.py`，实际发出的码名带 `_OUT_OF_RANGE` 后缀；仅 A/B 模式 |
| FACT_CHECK | 1 | 能 | `quality.py` 汇总后，由 `publication.py` 拆成逐句 |
| CONTEXTUAL_BROLL_OVERLAY | 规则写 2，**实际按 1 发出** | 能 | `quality.py` |
| MIXED_NO_NARRATION | 2 提示 | — | `production_modes.py` |
| QUOTE_TOO_SHORT（safety） | 0 | 否 | `production_modes.py` |
| QUOTE_INVALID_RANGE（safety） | 0 | 否 | `production_modes.py` |
| QUOTE_NONCONTIGUOUS（safety） | 0 | 否 | `production_modes.py`（仅 C 模式） |

另外，`quality.py` 和 `publication.py` 里还会产生约 22 个码，例如响度、真峰值、镜头复用、`GENERATED_MEDIA_DISCLOSURE_MISSING`、`PUBLICATION_QC_*`。前端不按检查码写死逻辑，统一显示服务端返回的 `message`；唯一的特例是 `workbenchApi.ts` 的 `canConfirmCheck`。

新增或修改检查码时要同步改哪些文件，见 [golden-mic-backend-map 的改动清单](../golden-mic-backend-map/references/change-checklists.md) 第 1 节。总数 17 写死在测试里。

## 禁止的捷径
- 为了通过检查，把错误降成警告、扩大 `acknowledgeable`、放宽阈值，或关闭 QC、发布门禁。
- 在前端隐藏或折叠错误项，或在文档、前端另造阈值（阈值以 `mode_rules.json` 为准）。
- 删除或弱化测试断言，伪造 ASR 证据、时间戳、匹配分数或报告哈希。
- 修改原话文字、拼接不连续的词，或在事实性句子里生成 AI 画面。
- 让 AI 披露可被关闭、裁掉，或通过会丢失披露的格式导出。
- 填假 Key 伪造就绪，或改额度、删账本绕过限制。

## 步骤（改相关代码时）
1. 先对照“红线和现状”和“禁止的捷径”，确认改动没有放宽任何一条；要放宽就先问用户。
2. 改检查码时，按 [golden-mic-backend-map 的改动清单](../golden-mic-backend-map/references/change-checklists.md) 第 1 节同步 `mode_rules.json`、产生位置、前端和测试。
3. 涉及稿件原文或原声时，改完确认原话文字、时间戳和匹配分数没有被改动。
4. 涉及 AI 画面时，确认披露仍随成片烧录、MP3/SRT 仍被拒绝（见 gm-ai-labeling）。

## 示例：有人要把 QUOTE_MATCH_LOW 的门槛从 85% 放宽到 70%（假设）
- 先不改：门槛在 `mode_rules.json` 的 `limits.match_ok`，放宽等于降低对原话的要求，属于“禁止的捷径”里的放宽阈值，先问用户。
- 用户同意后：只改 `mode_rules.json`，再按改动清单同步前端读取处和测试，并在汇报里写明新旧值。

## 验证
- 本文的检查码表与 `mode_rules.json` 一致：`checks` 17 个、`safety_checks` 3 个，`acknowledgeable` 只有 `generated_media`；改了就同步本表。
- 按 golden-mic-verify 跑静态检查和受保护回归；只改本文时，运行 `scripts/validate_agent_skills.py`。

## 来源
- [backend/mode_rules.json](../../../backend/mode_rules.json)、[README.md](../../../README.md)、[docs/USER_GUIDE.md](../../../docs/USER_GUIDE.md)
- [backend/quality.py](../../../backend/quality.py)、[backend/publication.py](../../../backend/publication.py)、[backend/production_modes.py](../../../backend/production_modes.py)、[backend/v2_editing.py](../../../backend/v2_editing.py)、[backend/script_assist.py](../../../backend/script_assist.py)、[backend/script_segmentation.py](../../../backend/script_segmentation.py)、[backend/provenance.py](../../../backend/provenance.py)（只写流水线清单和散列，不含披露逻辑）
