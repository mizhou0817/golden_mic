---
name: gm-ai-labeling
description: 金话筒 AI 内容标识（AI labeling / C2PA / 隐式标识 / AI 参与清单）P0 设计草稿流程。对照《人工智能生成合成内容标识办法》、GB 45438-2025 和 C2PA 2.4，梳理显式标识、隐式元数据、导出限制的现状差距，输出设计方案、"AI 参与清单"字段和任务拆解。用户说"AI 标识""合规标识""C2PA""隐式标识""AI 参与清单""AI 披露"时手动调用。只出方案；用户批准前不写产品代码、不加依赖、不调用任何服务。
disable-model-invocation: true
argument-hint: "[调研|方案|任务拆解]"
---

# 金话筒 AI 内容标识（P0 草稿）

## 目的
为"AI 内容隐式标识（含 C2PA）"和"发布检查增加 AI 参与清单"做设计准备：先把法规要求和现有行为对照清楚，再提出方案。本 skill 只产出文档，不改代码。

## 适用 / 不适用
- 适用：AI 标识合规调研、差距分析、方案设计、任务拆解、评审标识相关改动。
- 不适用：
  - 直接实现代码：须用户批准方案后，另按 golden-mic-verify 走改动和验证。
  - 法律意见：结论需由用户或法务确认。
  - 生成 AI 画面或调用 Seedream、Seedance。

## 硬性规则
- 2026-10-23 起成片不加显式标识（维护者决定，选的是"只移除观众看得到的"和"旧版本保持原样"；法律风险见 [.github/copilot-instructions.md](../../copilot-instructions.md)）。不要自行把标识或导出限制加回来；要恢复就按本 skill 出方案，经维护者批准后新增一代 `graphics.ass` 模板。保留的部分不能削弱：结果页的"AI 生成示意画面"徽标、发布前检查 `generated_media` 的知情勾选、`generated_media_disclosure.json` 来源记录，以及旧版本导出时按存档字节重建的第一到三代标识。
- 法规条款只能引用 [references/regulations.md](references/regulations.md) 里标了来源的内容。标为 [转载] 的条款，必须先对照官方全文核实才能写进方案结论；查不到的写 TODO(待确认)。
- 引入 `c2pa-python` 等新依赖、申请签名证书、改动导出格式，都要先经用户批准。
- 不读取或使用真实凭证，不上传素材，不调用付费服务。

## 现有行为（2026-10-22 读代码核对；显式标识、导出两行 2026-10-23 更新）
| 项目 | 现状 | 位置 |
|---|---|---|
| AI 画面开关 | 默认关闭（`GENERATIVE_FILL_ENABLED=False`），默认生成**视频**（Seedance）。需要全局开关、Key、本任务勾选三者同时满足，C 模式不可用。涉及事实的句子（实体、日期、机构）不会生成 | `backend/config.py`、`backend/providers/generative.py`、`backend/mode_pipeline.py` |
| 显式标识 | 2026-10-23 起**没有**：新作品和"应用修改"后的新版本，`graphics.ass` 是第四代（`; template: GM-GFX-NO-AI-LABEL-V4-20261023`），不画任何标识，旧流水线也一样；Studio 导出和原始素材预览也不再加整段小字（预览缓存的 `DISCLOSURE_VERSION` 改为 `studio-no-ai-label-v2`，带标识的旧预览不再复用）。之前做好的版本导出时按存档字节重建当时那一代（`graphics.read_disclosure_generation`）：第三代（2026-10-23，`…V3-20261023`）片头 0 秒起在右侧边缘显示"本片含AI生成内容"3 秒（片子更短就到片尾），每个 AI 镜头期间显示"AI生成示意画面"，字号 96，"AI"放大到 115%，实测汉字 57–63 像素、"AI" 56 像素，都不低于最短边的 5%；第二代（2026-10-22，`…V2-20261022`，"AI"约 49 像素，libass 叠放）；第一代（字号 30、`\pos(1870,40)`、没有片头标识、没有标记行）。2026-10-23 以前的 Studio 导出另有一条整段时长的左上角小字"AI生成示意画面（本片含AI生成内容）"（字号取画面高度的 3.5%） | `backend/graphics.py`、`backend/studio_render.py`、`backend/studio_proxy.py` |
| 导出 | 先裁切填满画幅，再烧录 graphics，所以旧版本的标识在竖屏、方形导出里也完整（2026-10-22 实测）。2026-10-23 起含 AI 画面的作品可以导出全部格式：V2 的 MP3/SRT 和 Studio 路径的 MP3/WAV/SRT/ASS 不再被拒，结果页照常给出这些选项；只有 AI 画面缺少有效的来源记录时返回 422 | `backend/studio_render.py`、`backend/v2_editing.py`、`backend/studio.py`、`frontend/src/components/ResultWorkbench.tsx` |
| 发布检查 | `generated_media` 是级别 0 但**可确认**的唯一例外；AI 句子同时会触发不可确认的 MATCH_FALLBACK | `backend/publication.py`、`backend/quality.py` |
| 隐式标识 | **没有**。`provenance.py` 只写流水线清单和散列，不写 AIGC 元数据或 C2PA | `backend/provenance.py` |
| 合成人声 | Seed TTS 旁白目前**没有任何标识** | `backend/mode_pipeline.py` |

## 待评估的差距（需核实后再下结论）
1. 显式标识：2026-10-23 起成片、导出文件和预览都不加，和办法第四条（视频起始画面和播放周边加显著提示，导出的文件也要带）、GB 45438-2025 第 5.4 条不一致。办法第九条允许按用户要求提供不带显式标识的内容，前提是用户协议写明对方的标识义务和使用责任，并留存提供对象等日志不少于六个月（见 [references/regulations.md](references/regulations.md) 第 1 节、[references/terms-clause.md](references/terms-clause.md)）。要恢复时可以从第三代的规格出发：按第 5.4 条（多源转载）起始画面靠右边缘 3 秒，每个字符（包括"AI"）都不低于最短边的 5%（测试 `tests/test_v2_code_doc_agreement.py` 的 `ThirdGenerationLabelTests` 从字体文件核对字高，`tests/test_editing_enhancements.py` 渲染第三代的 graphics.ass 核对位置）。TODO(待确认)：拿到官方全文后核对 f) 的口径。
2. 合成人声（TTS）是否需要显式标识（语音提示或节奏提示）。
3. 隐式元数据：AIGC JSON 和 C2PA 清单分别怎么写入 MP4、MP3、PNG、GIF；每个文件只保留一份 AIGC 元数据；服务提供者编码从哪里来。
4. 如果成片在网上分发（deploy/README.md 记有线上实例），还要评估第六条规定的平台义务。
5. 播放界面周边（办法第四条（四））：结果页播放器周边没有"含 AI 生成内容"的提示，只有逐句的"AI 生成示意画面"徽标。
6. 封面和缩略图（办法第四条（三））：来自 AI 画面的封面（poster）和镜头缩略图没有标识。
7. 用户协议（办法第八条）：要写明标识的方法和样式。条款草案见 [references/terms-clause.md](references/terms-clause.md)，未经法务审核，没有生效。

## 步骤
1. 读 [references/regulations.md](references/regulations.md)。如有需要，联网核对官方原文（只读，不下载可执行文件）。
2. 读上表列出的代码，确认现状没有变化。
3. 写差距表：要求、来源及其级别、现状、差距、风险等级。
4. 提出方案，至少包括：
   - 显式标识规格（文字、位置、字高、时长、起始画面、TTS 标识）；
   - 隐式标识（AIGC 字段的取值和来源、C2PA 断言及 digitalSourceType 的选择、签名证书方案）；
   - 导出限制；
   - 界面展示；
   - 测试方案（只用合成素材）。
5. 设计"AI 参与清单"字段，见下方模板。
6. 拆解任务：每个任务写明改哪些文件（参考 golden-mic-backend-map 的改动清单）、验收方法和依赖的批准项。
7. 交给用户批准。批准前不写产品代码。

## AI 参与清单字段（草案）
| 字段 | 含义 | 取值来源（待设计） |
|---|---|---|
| 环节 | 转写、画面理解、匹配、精排、读音、配音、AI 画面、效果推荐、写稿辅助 | 流水线各阶段 |
| 服务 | Seed ASR、Kimi、doubao-embedding-vision、Seed TTS 2.0、Seedream、Seedance | `backend/providers/` |
| 作用对象 | 第几句、哪个镜头、哪段时间 | EDL 和报告 |
| 是否生成内容 | 生成（画面、人声）或只做辅助（转写、匹配） | 设计判定 |
| 用户可见标识 | 烧录文字、界面徽标、无 | 渲染与界面 |
| 人工核对状态 | 记者是否已确认 | 发布检查 |

## 示例：评估要不要给 TTS 旁白加语音提示（假设）
1. 读 [references/regulations.md](references/regulations.md) 里关于合成语音的条款，注意每条的来源级别；标为 [转载] 的先写成 TODO(待确认)。
2. 读 `backend/mode_pipeline.py` 的 TTS 调用位置，确认“现有行为”表里“没有任何标识”仍然成立。
3. 写差距表一行：要求（来源、级别）、现状、差距、风险；再列方案选项（语音提示 / 只用画面标识 / 两者），标出需要用户或法务确认的项。
4. 不写代码，等用户批准方案。

## 验证
- 方案里每条法规引用都能在 `references/regulations.md` 找到，并带着来源级别。
- “现有行为”表里的位置用 grep 重新核对过。
- 设计阶段没有改动产品代码：`git status` 里只有文档。

## 输出格式
一份 Markdown 设计稿：差距表、方案、参与清单字段、任务拆解、待批准事项、TODO(待确认) 列表。放在用户指定的位置；未指定时只在对话中给出，不写进仓库。

## 来源
- [references/regulations.md](references/regulations.md)：法规、标准、同类产品协议、C2PA 要点，每条带来源级别
- [references/terms-clause.md](references/terms-clause.md)：用户协议条款草案（标识的方法和样式、转载与传播），未经法务审核
- [docs/USER_GUIDE.md](../../../docs/USER_GUIDE.md)（AI 示意画面和导出说明）
- [backend/graphics.py](../../../backend/graphics.py)、[backend/v2_editing.py](../../../backend/v2_editing.py)、[backend/publication.py](../../../backend/publication.py)、[backend/provenance.py](../../../backend/provenance.py)、[backend/providers/generative.py](../../../backend/providers/generative.py)
