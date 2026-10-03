# 文稿—镜头重复失配解决方案（2026-07-26）

## 1. 问题证据

真实任务 `9a2d0bf2af744bda841d08483c98ed48` 中，18 个播音单元只使用 12 个不同镜头，镜头 20（“南宁信息港迎春市集入口处，有人员从建筑内走出”）被实际播放 8 次；质量建议上限为 2。阶段 6 已选择该镜头 6 次，阶段 9 独立 overlay 重选又将另外 2 句改为该镜头。

## 2. 研究依据

### 2.1 Maximal Marginal Relevance（MMR）

Goldstein 与 Carbonell，*Summarization: Using MMR for Diversity-Based Reranking*，TIPSTER/ACL 1998，DOI 10.3115/1119089.1119120：

- 官方页面：https://aclanthology.org/X98-1025/
- 核心思想：候选选择不应只最大化查询相关性，还应同时最大化相对于已选结果的新颖性。

对应本项目：候选镜头效用由语义相关性、LLM 选择、实体证据和候选排名组成。MMR 的多样性思想用于候选效用设计；当前产品约束进一步收紧为容量 1，不再允许第二次使用。

### 2.2 子模视频摘要

Gygli et al.，*Video Summarization by Learning Submodular Mixtures of Objectives*，CVPR 2015：

- 官方论文：https://www.cv-foundation.org/openaccess/content_cvpr_2015/papers/Gygli_Video_Summarization_by_2015_CVPR_paper.pdf
- 核心思想：视频摘要需要联合优化重要性、代表性、覆盖和多样性，而不是逐片段独立贪心。

对应本项目：阶段 6 和 overlay 都必须对整组播音单元联合分配镜头，不能逐句独立取最高分。

### 2.3 容量约束分配 / 最小费用流

Google OR-Tools 官方文档：

- https://developers.google.com/optimization/flow/mincostflow
- https://developers.google.com/optimization/flow/assignment_min_cost_flow

核心思想：任务—资源分配可建模为二部网络；资源到汇点的容量直接限制最多分配次数，单位费用表达使用代价。容量约束由优化器保证，而不是事后告警。

对应本项目：每个视觉节拍供应 1 单位流；每个镜头容量固定为 1。实体证据也不能触发扩容；候选图无法形成一对一匹配时明确失败，要求增加素材、减少播音单元或改善候选覆盖。

### 2.4 查询相关时刻定位

Lei et al.，*QVHighlights: Detecting Moments and Highlights in Videos via Natural Language Queries*，NeurIPS 2021：

- 官方页面：https://arxiv.org/abs/2107.09609
- 论文将自然语言查询、相关时刻和查询相关片段显著性联合建模；ASR 弱监督也能显著改善时刻检索。

对应本项目：检索向量只描述当前播音单元/视觉节拍；完整自然句仅用于候选共享和 LLM 背景。命中 ASR 或四秒语义窗口时，保存查询相关入点，而不是默认从镜头 0 秒开始。

### 2.5 DPP 多样化子集（设计参考，不直接引入）

Kulesza & Taskar，*Determinantal Point Processes for Machine Learning*，Foundations and Trends in ML 2012：

- https://arxiv.org/abs/1207.6083
- DPP 将高质量和相互排斥统一到子集概率中，适合高质量多样集合选择。

本项目没有直接引入 DPP：当前规模小、存在明确硬容量，最小费用流更容易审计、复现和施加强约束；DPP 的“质量 + 排斥”思想通过候选效用和一对一资源约束实现。

## 3. 已实施算法

### 3.1 局部查询、分组召回

- Embedding 查询只包含当前播音单元、当前视觉节拍、意图和显式实体。
- 完整自然句不再直接进入查询向量，避免“南宁信息港/迎春市集”支配所有抽象短句。
- 同一自然句仍共享粗召回候选并集，保留短句召回能力。
- 完整自然句只作为 LLM 精排背景。

### 3.2 阶段 6 全局容量分配

每个节拍—候选镜头效用：

`utility = hybrid_relevance + evidence_bonus + llm_selected_bonus + rank_bonus`

约束：

- 每个节拍必须分配一个候选镜头；
- 每个镜头实际容量固定为 1；
- 显式实体候选必须有词法或 Vision 复核证据；
- 实体镜头不享有扩容例外；
- 严格候选图不可行时，只扩展到同一召回池中仍满足实体证据要求的候选，不放宽容量；
- 扩展后仍不可行则阻止任务继续，绝不静默复用。

意图允许的最大语义损失：

- entity：0.08
- general：0.12
- organization/date：0.16
- abstract：0.20

### 3.3 overlay 联合分配

- 先统计所有非 overlay 节拍的实际镜头占用；
- 每个源镜头的 overlay 剩余容量为 `max(0, 1 - existing_usage)`；
- 所有 date/organization/abstract overlay 一次性进行最小费用流分配；
- 使用当前句 + 同视觉组文本做局部语境评分，不再使用整篇文稿；
- 对阶段 6 原镜头给予保留奖励，但不突破容量；
- 不再加入虚假的“事实文字叠加” matched term；
- 不再把 fallback 强制改为成功，也不再把置信度强制抬到 0.5；
- 保留原候选列表供审计。

### 3.4 查询相关时间窗口

- 优先使用 ASR 命中位置前 0.4 秒作为入点；
- 没有 ASR 命中时，对四秒 semantic windows 做查询词加权覆盖评分；
- evidence-aligned 片段不再被后续“错开重复入点”逻辑覆盖。

### 3.5 同期声、EDL、换镜和重剪

- 同期声候选不能占用其他播音单元已经保留的镜头；成功替换时原子释放本句旧镜头并保留同期声镜头；
- 最终 EDL 只消费匹配计划明确分配的镜头，不再从 alternates、候选池或全局画质列表补片；
- 单镜头时长不足时冻结末帧补足旁白，不额外消耗镜头；
- EDL 写盘和渲染前再次检查所有 `shot_id` 全局唯一；
- 交互换镜排除其他句子匹配计划及实际 EDL 中的全部镜头，并在计划、EDL 两个提交点复检；
- 删减重剪也拒绝继承历史重复镜头。

### 3.6 质量门禁

- `maximum_visual_shot_use_count`、过度复用和快速复用均按实际播放节拍计算；
- `visual_group_shot_usage` 仅保留为分析指标，不再用于放宽容量；
- 硬上限固定为 1；
- 任一重复使用产生 `VISUAL_SHOT_OVERUSED` error，而不是 warning；
- 不存在实体证据、同视觉组或 overlay 例外。

## 4. 自动化验收

- 8 个节拍都偏好同一事件身份镜头时，8 个结果必须使用 8 个不同镜头；
- 7 个 overlay 都偏好同一入口镜头时，7 个结果必须使用 7 个不同镜头；
- 同一 visual group 不获得复用豁免；
- 多个实体节拍只有同一个证据镜头时明确报错；
- 同期声不能与其他句子的视觉镜头碰撞；
- 最终 EDL、交互换镜和删减重剪均拒绝重复镜头；
- ASR/semantic-window 入点继续通过 EDL 持久化；
- fallback overlay 保持 fallback 状态和真实置信度。

## 5. 回滚边界

算法均为本地确定性后处理：关闭 overlay 或回退 `matching.py`/`rendering.py` 即可恢复旧行为；不改变上传、ASR、Vision、Embedding、TTS 和最终渲染的外部 API。全局求解器只使用标准库，不增加生产依赖。

## 6. 真实问题任务回放

任务：`9a2d0bf2af744bda841d08483c98ed48`。严格唯一性回放复用已保存的完整候选池和当时 LLM 决策，只重新运行容量 1 全局分配、overlay 联合分配和 EDL 构建；云端请求为 0，未覆盖原成片。

| 指标 | 原成片 | 容量 2 回放 | 严格容量 1 回放 |
|---|---:|---:|---:|
| 镜头 20 使用次数 | 8 | 2 | **1** |
| 任一镜头最大使用次数 | 8 | 2 | **1** |
| 不同镜头数 | 12 | 19 | **20** |
| 实际视觉片段数 | 20 | 20 | **20** |
| Overlay 数 | 7 | 7 | 7 |

回放审计产物：

- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/matching_replay_20260726.json`
- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/match_plan.replay_20260726.json`
- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/match_plan.replay_after_overlay_20260726.json`
- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/overlay_shot_assignment.json`
- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/strict_uniqueness_replay_20260726/matching_replay_unique_20260726.json`
- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/strict_uniqueness_replay_20260726/match_plan.stage6.json`
- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/strict_uniqueness_replay_20260726/match_plan.after_overlay.json`
- `data/tasks/9a2d0bf2af744bda841d08483c98ed48/strict_uniqueness_replay_20260726/edl.json`

阶段 6、overlay 后计划和最终 EDL 的最大使用次数均为 1，且 20 个视觉片段使用 20 个不同镜头。关键语义镜头仍保留：新能源汽车 31、腊味 1、油茶 35、寿司 13；因此严格唯一性没有以丢失显式实体为代价。
