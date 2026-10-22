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
- 现有披露不能被削弱：AI 生成镜头必须烧录"AI生成示意画面"，不受字幕开关影响；含 AI 画面的作品拒绝导出 MP3/SRT（`backend/v2_editing.py`）。新方案只能加强，不能绕过。
- 法规条款只能引用 [references/regulations.md](references/regulations.md) 里标了来源的内容。标为 [转载] 的条款，必须先对照官方全文核实才能写进方案结论；查不到的写 TODO(待确认)。
- 引入 `c2pa-python` 等新依赖、申请签名证书、改动导出格式，都要先经用户批准。
- 不读取或使用真实凭证，不上传素材，不调用付费服务。

## 现有行为（2026-10-21 读代码核对，提交 af1e4e7）
| 项目 | 现状 | 位置 |
|---|---|---|
| AI 画面开关 | 默认关闭（`GENERATIVE_FILL_ENABLED=False`），默认生成**视频**（Seedance）。需要全局开关、Key、本任务勾选三者同时满足，C 模式不可用。涉及事实的句子（实体、日期、机构）不会生成 | `backend/config.py`、`backend/providers/generative.py`、`backend/mode_pipeline.py` |
| 显式标识 | ASS 样式 GfxDisclosure：右上角，字号 30（1920×1080 画布），带底框，**只在 AI 镜头期间显示**。Studio 路径另有"AI生成示意画面（本片含AI生成内容）" | `backend/graphics.py`、`backend/studio_render.py` |
| 导出 | 先裁切填满画幅，再烧录 graphics，所以竖屏、方形导出不会裁掉披露。MP3/SRT 由服务端以 422 拒绝，但界面仍显示这两个选项 | `backend/studio_render.py`、`backend/v2_editing.py`、`frontend/src/components/ResultWorkbench.tsx` |
| 发布检查 | `generated_media` 是级别 0 但**可确认**的唯一例外；AI 句子同时会触发不可确认的 MATCH_FALLBACK | `backend/publication.py`、`backend/quality.py` |
| 隐式标识 | **没有**。`provenance.py` 只写流水线清单和散列，不写 AIGC 元数据或 C2PA | `backend/provenance.py` |
| 合成人声 | Seed TTS 旁白目前**没有任何标识** | `backend/mode_pipeline.py` |

## 待评估的差距（需核实后再下结论）
1. 显式标识的位置、字高、时长：按 GB 45438-2025 第 5.4 条的转载文本，要求起始画面显示不少于 2 秒，字高不低于最短边的 5%（1080p 约 54 像素）。现行字号 30，大约是最短边的 2.8%，并且只在 AI 镜头期间出现。TODO(待确认)：先对照官方全文。
2. 合成人声（TTS）是否需要显式标识（语音提示或节奏提示）。
3. 隐式元数据：AIGC JSON 和 C2PA 清单分别怎么写入 MP4、MP3、PNG、GIF；每个文件只保留一份 AIGC 元数据；服务提供者编码从哪里来。
4. "仍然导出（带未通过的检查）"路径下，标识是否完整保留：现有代码注释说披露门禁仍然生效，需要有测试证明。
5. 如果成片在网上分发（deploy/README.md 记有线上实例），还要评估第六条规定的平台义务。

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

## 输出格式
一份 Markdown 设计稿：差距表、方案、参与清单字段、任务拆解、待批准事项、TODO(待确认) 列表。放在用户指定的位置；未指定时只在对话中给出，不写进仓库。

## 来源
- [references/regulations.md](references/regulations.md)：法规、标准、C2PA 要点，每条带来源级别
- [docs/USER_GUIDE.md](../../../docs/USER_GUIDE.md)（AI 示意画面和导出说明）
- [backend/graphics.py](../../../backend/graphics.py)、[backend/v2_editing.py](../../../backend/v2_editing.py)、[backend/publication.py](../../../backend/publication.py)、[backend/provenance.py](../../../backend/provenance.py)、[backend/providers/generative.py](../../../backend/providers/generative.py)
