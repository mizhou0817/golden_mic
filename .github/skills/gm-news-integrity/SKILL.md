---
name: gm-news-integrity
description: 金话筒新闻真实性与发布门禁的背景知识（news integrity / publish checks / 发布前检查）。内容包括三种模式的声音规则、稿件与原声的红线、AI 示意画面披露、20 个检查码（级别、能否确认、产生位置）、与红线冲突的现状（TODO），以及禁止的捷径。修改稿件处理、原声剪辑、匹配、质量检查、发布检查、导出、AI 画面相关代码，或讨论"能不能导出""检查码""原话""同期""AI 画面""豁免"时自动参考。
user-invocable: false
---

# 金话筒新闻真实性与发布门禁

本文档描述 2026-10-21（提交 af1e4e7）的代码现状，并标出与维护者红线冲突的地方。常驻规则见 [.github/copilot-instructions.md](../../copilot-instructions.md)；本文只写细节和流程。

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
- **冲突 TODO(待确认)**：维护者的红线是"不用 AI 写稿、改写或润色"，但 A/B 模式现有"AI 写一稿"和"润色 / 精简 / 更适合口播 / 语气更庄重"（`backend/script_assist.py`、`frontend/src/components/ScriptAssistant.tsx`，[USER_GUIDE](../../../docs/USER_GUIDE.md)"用 AI 写稿或润色"一节）。
  - 现有约束：由记者主动触发；只改旁白；不得编造事实；不改"同期："开头的行；数字有变化时提示。
  - **已知缺陷**：保护原话行用的正则是 `QUOTE_LINE = ^\s*同期\s*[：:]`，匹配不到规范写法"同期 姓名（身份）："，这类原话行不受保护。
  - 在维护者决定之前：不要扩大 AI 改写的范围；任何涉及该功能的改动，都要先让所有同期写法受到保护（复用 `production_modes` 的结构化解析）。

### 2. 原声
- 只能在原区间内按真实词边界连续剪短：至少 1 秒，不能剪开一个词，"句段精度"的原声不能剪；剪完保留连续的 `words`/`asr_text` 片段和原始匹配分数（`production_modes.validate_quote_trim`）。时间是原始上传文件里的绝对秒数。
- 编辑不能改原话文字（`workbench.py`、`mode_pipeline.py`）。换一段原声时，旧的那段留在 `alt_takes` 里，最多 5 段备选。
- C 模式用 QUOTE_NONCONTIGUOUS 拦截不连续的原话。
- 剪短后，当前版本只保存剪过的证据，完整证据留在上一个版本里。

### 3. AI 示意画面
- 默认关闭。要同时满足三个条件才会生成：全局 `GENERATIVE_FILL_ENABLED`、配置了 Key、本任务勾选。默认生成的是**视频**（Seedance）。C 模式不可用；涉及实体、日期、机构的事实句子会跳过。
- 画面上烧录"AI生成示意画面"，放在 `graphics.ass` 里，和字幕开关无关；导出时总会烧录 graphics。
- 含 AI 披露的作品，服务端用 422 拒绝导出 MP3/SRT（`v2_editing.py`）。**已知问题**：界面仍然显示这两个选项。
- AI 句子会同时触发 `generated_media`（可确认）和不可确认的 MATCH_FALLBACK。
- 标识是否符合国家标准，见 gm-ai-labeling。

### 4. 发布前检查与导出
- 级别 0 是错误，不能确认，唯一例外是 `generated_media`（`acknowledgeable: true`）。级别 1 是警告，逐项确认；确认键绑定"版本号 + 报告哈希"，版本一变就失效，过期的版本号返回 409。级别 2 只做提示，不计入待确认。
- 常规导出要同时过 `check_qc` 和 `require_publication`。
- **冲突 TODO(待确认)**：V2 的"仍然导出（带未通过的检查）"（`acknowledge_unresolved: true`，在 `v2_editing.py` 的 `ready()` 里）会跳过 `check_qc` 和 `require_publication`，**连级别 0 的错误也跳过**。任务状态、版本、忙碌状态、AI 披露、源和输出绑定这些检查仍然有效。
  - 这条路径来自 2026-10-08 的提交 6310015。README 第 123 行和 `docs/CONFIGURATION.md` 第 81 行仍写着"不能靠勾选豁免、硬门禁"，和代码不一致。
  - 在维护者决定之前：不要新增或扩大任何绕过路径；不能让披露检查被跳过；改动这条路径要先问用户。

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

新增或修改检查码时要同步改哪些文件，见 [gm-pipeline-map 的改动清单](../gm-pipeline-map/references/change-checklists.md) 第 1 节。总数 17 写死在测试里。

## 禁止的捷径
- 为了通过检查，把错误降成警告、扩大 `acknowledgeable`、放宽阈值，或关闭 QC、发布门禁。
- 在前端隐藏或折叠错误项，或在文档、前端另造阈值（阈值以 `mode_rules.json` 为准）。
- 删除或弱化测试断言，伪造 ASR 证据、时间戳、匹配分数或报告哈希。
- 修改原话文字、拼接不连续的词，或在事实性句子里生成 AI 画面。
- 让 AI 披露可被关闭、裁掉，或通过会丢失披露的格式导出。
- 填假 Key 伪造就绪，或改额度、删账本绕过限制。

## 来源
- [backend/mode_rules.json](../../../backend/mode_rules.json)、[README.md](../../../README.md)、[docs/USER_GUIDE.md](../../../docs/USER_GUIDE.md)
- [backend/quality.py](../../../backend/quality.py)、[backend/publication.py](../../../backend/publication.py)、[backend/production_modes.py](../../../backend/production_modes.py)、[backend/v2_editing.py](../../../backend/v2_editing.py)、[backend/script_assist.py](../../../backend/script_assist.py)、[backend/script_segmentation.py](../../../backend/script_segmentation.py)、[backend/provenance.py](../../../backend/provenance.py)（只写流水线清单和散列，不含披露逻辑）
