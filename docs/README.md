# 文档导航

[项目首页](../README.md)

按下面顺序阅读即可上手，不必从审计报告读起。**无日期的指南描述当前使用方式；带日期的报告是当时的实现/验收快照。** 历史文件里写的“当前”“最终”只适用于其日期，不代表你的服务已经更新或已上线。

## 新使用者

| 目标 | 文档 |
|---|---|
| 安装系统工具、创建环境、构建/启动、无凭证看界面 | [QUICKSTART.md](QUICKSTART.md) |
| A/B/C 制作、按句录音/修改、检查、导出、历史 | [USER_GUIDE.md](USER_GUIDE.md) |
| 凭证、环境读取、可选语音、容量与保留时间 | [CONFIGURATION.md](CONFIGURATION.md) |
| not_ready、FFmpeg、上传、播放、下载、录音或权限问题 | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |
| 停机/排空、备份恢复、费用与访问凭证保护 | [RUNBOOK.md](RUNBOOK.md) |

## 开发者和部署操作者

| 目标 | 文档 |
|---|---|
| 了解目录、前后端调试、当前安全测试与构建 | [DEVELOPMENT.md](DEVELOPMENT.md) |
| Linux 冻结依赖、签名包、TLS/服务、排空回滚及阻塞项 | [部署指南](../deploy/README.md) |
| 了解本地声纹/CTC 模型选择与供应链约束 | [模型决策（2026-10-02）](V2_MODEL_SELECTION_20261002.md)，实际后续状态以[交付记录](V2_PRODUCT_DELIVERY_20261003.md)为准 |

安装指南针对**完整源码副本**。最小发布包只有显式白名单文档、后端和已构建前端，不是完整源码：没有完整测试/前端开发配置，也不保证包含所有新指南。不要在包内执行源码构建步骤；不要把未签名 TEMP 包当可发布制品。

## API 与架构参考

### 当前 V2 集成先看这些源码

这些是实际路由/序列化的入口。调试 API 时保留任务 capability、生产匿名会话/CSRF/Origin 与版本条件；不要复制真实 token 到公开命令中。

| 功能 | 权威入口 |
|---|---|
| 会话、配置/健康、草稿创建、访问鉴权、路由挂载 | [main.py](../backend/main.py) |
| 任务草稿、task-scoped 文件分块/状态、align、start、恢复 | [drafts.py](../backend/drafts.py) |
| 前端实际请求/响应解析 | [appApi.ts](../frontend/src/lib/appApi.ts)、[workbenchApi.ts](../frontend/src/lib/workbenchApi.ts) |
| 批量应用、版本与导出 | [v2_editing.py](../backend/v2_editing.py)、[workbench.py](../backend/workbench.py)、[revisions.py](../backend/revisions.py) |
| V2 准入与生命周期 | [admission.py](../backend/admission.py)、[task_manager.py](../backend/task_manager.py) |
| 名称、模式、时序与质量规则 | [mode_rules.json](../backend/mode_rules.json)、[production_modes.py](../backend/production_modes.py) |

**不要直接把旧 JSON `POST /api/tasks` 示例当成 V2 创建流程。** 当前浏览器先获得草稿回执，向该任务添加文件/分块，明确 start 后才制作。开发环境可在自己启动的服务上查看 `/docs`；生产关闭 API 文档。

### 仍可查阅的专题契约

| 文档 | 适用范围 |
|---|---|
| [WORKBENCH_API.md](WORKBENCH_API.md) | 逐句编辑/录音/修订背景；V2 统一应用入口仍需对照上面的实际源码 |
| [MEDIA_INPUT_API.md](MEDIA_INPUT_API.md) | 媒体探测、图片/裁剪等基础边界；新 task-scoped 上传以 drafts 源码为准 |
| [THREE_MODE_API_20260928.md](THREE_MODE_API_20260928.md) | 三模式规则及兼容 API 的日期说明；不是当前前端请求序列的唯一权威 |
| [STUDIO_API.md](STUDIO_API.md) | 保留的旧有界 Studio API；**不代表 V2 主界面提供专业时间线** |
| [DESIGN-MAP.md](DESIGN-MAP.md) | 设计映射与盘点；不是全部视图/像素/无障碍已验收的声明 |

## 验收与历史资料

### 最近一次本地交付记录

- [V2_PRODUCT_DELIVERY_20261003.md](V2_PRODUCT_DELIVERY_20261003.md)：合成回归/浏览器、独立声纹技术验证、未签名发布包和外部阻塞。
- [脱敏机器摘要](../canary_test/artifacts/v2-product-delivery-20261003/summary.json)与[核验回执](../canary_test/artifacts/v2-product-delivery-20261003/verification-receipt.json)：已生成证据，不因本次文档重写而重新签署。

这次记录不等于当前机器已配置服务、不等于真实语音质量或生产上线。CTC、授权范例、真实语音评估和 Linux/TLS/负载/回滚仍需各自完成；部署指南还列出本轮文档核对发现的参数解析阻塞，不应盲目执行生产步骤。

### 按日期保留，不作为快速启动

| 文档 | 用途 |
|---|---|
| [V2_PRODUCT_PROGRESS_20261002.md](V2_PRODUCT_PROGRESS_20261002.md) | 前一天的实现/失败与模型进展，部分状态已被后续覆盖 |
| [V2_CURRENT_CLOSURE_20260930.md](V2_CURRENT_CLOSURE_20260930.md)、[V2_VALIDATION_20260930.md](V2_VALIDATION_20260930.md) | 分阶段验收与保留的失败归因 |
| [V2_IMPLEMENTATION_20260929.md](V2_IMPLEMENTATION_20260929.md)、[V2_DESIGN_REVIEW_20260930.md](V2_DESIGN_REVIEW_20260930.md) | 实现、设计差异与阶段性范围 |
| [THREE_MODE_IMPLEMENTATION_20260928.md](THREE_MODE_IMPLEMENTATION_20260928.md)、[THREE_MODE_VALIDATION_20260928.md](THREE_MODE_VALIDATION_20260928.md) | 更早三模式阶段，不拿其计数当当前回归 |
| [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)、[CORE_WORKSPACE_VALIDATION_20260927.md](CORE_WORKSPACE_VALIDATION_20260927.md) | 旧核心工作区与当时本地部署背景 |
| [历史 Canary 索引](../canary_test/README.md) | 保留 C24–C26、旧课堂/云阶段和失败证据；旧主机/命令不自动适用于 V2 |

课堂、队列、云作业和旧原型审计没有删除，但已不是当前新用户操作入口。不要据它们恢复登录流程、旧公共服务、付费调用或退役字段。