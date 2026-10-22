# 历史报告索引

[文档导航](../README.md) · [项目首页](../../README.md)

这里存放**按日期保留的审计、分析与调研快照**。每份报告只描述其日期当时的源码、数据和结论；正文里的“当前”“最新”“最终”只适用于该日期，不代表现在的状态。当前使用方式以[项目首页](../../README.md)和[文档导航](../README.md)为准，与源码冲突时以源码为准。

这些报告原先散放在仓库根目录，2026-10-21 起按主题归入本目录，报告内的相对链接已同步更新。本目录不在最小发布包的文件清单中，不随包发布。

## audits/ · 审计与复测

| 报告 | 日期 | 内容 |
|---|---|---|
| [AUDIT_REPORT.md](audits/AUDIT_REPORT.md) | 2026-07-23 | 项目全面审计（含已移除的 MediaKit 云端画质增强的历史记录） |
| [P0_REVALIDATION_REPORT_20260728.md](audits/P0_REVALIDATION_REPORT_20260728.md) | 2026-07-28 | 腾讯云部署前 P0 二次复核 |
| [TENCENT_CLOUD_LINUX_DEPLOYMENT_AUDIT_20260728.md](audits/TENCENT_CLOUD_LINUX_DEPLOYMENT_AUDIT_20260728.md) | 2026-07-28 | 腾讯云 Linux 单机部署前全面审计 |
| [LATEST_FRAME_BY_FRAME_PIPELINE_AUDIT_20260804.md](audits/LATEST_FRAME_BY_FRAME_PIPELINE_AUDIT_20260804.md) | 2026-08-04 | 成片与参考片逐帧对比，反推智能剪辑流水线 |
| [LOCAL_PRODUCT_FUNCTIONAL_AUDIT_20260905.md](audits/LOCAL_PRODUCT_FUNCTIONAL_AUDIT_20260905.md) | 2026-09-05 | 本机服务与产品功能测试 |
| [LOCAL_PRODUCT_LIVE_RETEST_20260905.md](audits/LOCAL_PRODUCT_LIVE_RETEST_20260905.md) | 2026-09-05 | 本机产品第二轮实时全功能复测 |
| [PRODUCT_FUNCTION_UI_AUDIT_20261015.md](audits/PRODUCT_FUNCTION_UI_AUDIT_20261015.md) | 2026-10-15 | V2 功能效果与多端界面检查 |

## pipeline/ · 流水线性能与语义匹配

| 报告 | 日期 | 内容 |
|---|---|---|
| [PIPELINE_PERFORMANCE_ANALYSIS.md](pipeline/PIPELINE_PERFORMANCE_ANALYSIS.md) | 2026-07-24 | AI 智能剪辑阶段耗时与瓶颈 |
| [SEMANTIC_MATCHING_PERFORMANCE_ANALYSIS.md](pipeline/SEMANTIC_MATCHING_PERFORMANCE_ANALYSIS.md) | 2026-07-24 | 语义匹配阶段耗时根因 |
| [SEMANTIC_MATCHING_OPTIMIZATION_PLAN.md](pipeline/SEMANTIC_MATCHING_OPTIMIZATION_PLAN.md) | 2026-07-24 | 候选级视频 Embedding 优化方案与执行结果 |
| [VIDEO_EMBEDDING_CACHE_CONCURRENCY_SOLUTION.md](pipeline/VIDEO_EMBEDDING_CACHE_CONCURRENCY_SOLUTION.md) | 2026-07-25 | 视频 Embedding 跨任务缓存与并发治理方案 |
| [SEMANTIC_MATCHING_CURRENT_ANALYSIS_20260725.md](pipeline/SEMANTIC_MATCHING_CURRENT_ANALYSIS_20260725.md) | 2026-07-25 | 语义匹配耗时根因的后续分析 |
| [SEMANTIC_MATCHING_REPETITION_SOLUTION_20260726.md](pipeline/SEMANTIC_MATCHING_REPETITION_SOLUTION_20260726.md) | 2026-07-26 | 文稿—镜头重复失配解决方案 |

## product/ · 需求、规格与市场调研

| 报告 | 日期 | 内容 |
|---|---|---|
| [REQUIREMENTS_GAP_ANALYSIS.md](product/REQUIREMENTS_GAP_ANALYSIS.md) | 2026-07-24 | PC/移动端剪辑功能需求差距分析 |
| [CLAUDE_FABLE_5_UI_FUNCTIONAL_SPEC.md](product/CLAUDE_FABLE_5_UI_FUNCTIONAL_SPEC.md) | 2026-09-07 | 面向前端重设计的全功能与 UI/交互构建规格 |
| [PROJECT_FUNCTION_INVENTORY_20261019.md](product/PROJECT_FUNCTION_INVENTORY_20261019.md) | 2026-10-19 | 当前 V2 功能盘点（源码只读核查） |
| [AI_VIDEO_EDITOR_MARKET_RESEARCH_20261019.md](product/AI_VIDEO_EDITOR_MARKET_RESEARCH_20261019.md) | 2026-10-19 | 22 个 AI 视频剪辑产品的市场调研 |
| [AI_VIDEO_EDITOR_COMPARISON_20261019.md](product/AI_VIDEO_EDITOR_COMPARISON_20261019.md) | 2026-10-19 | 与金话筒的功能对比和产品建议 |

## tooling/ · 开发工具与 Agent Skills

| 报告 | 日期 | 内容 |
|---|---|---|
| [AGENT_SKILLS_SELECTION_20261021.md](tooling/AGENT_SKILLS_SELECTION_20261021.md) | 2026-10-21 | Agent Skills 选型与安全审查：候选来源、审查结论、安装方式。现行清单见 [AGENT_SKILLS.md](../AGENT_SKILLS.md) |
