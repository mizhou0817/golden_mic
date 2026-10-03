> **历史验收 · 2026-09-27 退役标识：独立云作业、课堂授权及旧浏览器入口已退出当前产品。** 下文“完成/最新”与测试数只描述 2026-09-23 的当次验证，不是现行套件或新验收。当前范围见 [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)。保留原始回执、日志、账本与媒体，不修改费用状态、不复跑一次性调用；核心生成的 Kimi/火山 Provider 仍保留。

# 云接入第一阶段：实现、回归与受控实测

**结论：第一阶段代码与本地验收完成；腾讯真实媒体验收未完成，原型全量高级能力仍未完成。** 本记录接续[核心原型重构](PROTOTYPE_REFACTOR_20260923.md)，不覆盖其历史结果。记录日期采用本轮开始日；最后的浏览器复验结束于 2026-09-23 16:12 UTC（北京时间次日）。

## 1. 本次交付

- 六种独立云作业：Kimi 参考解说/字幕文字翻译；腾讯 MPS/COS 超分、补帧、音频降噪、智能字幕。真实账号未配置的能力保持不可用；没有模拟成功或自动创建云资源。
- 独立 SQLite 账本保存具名一次性、UTC 日、单作品额度；原子预约和容量准入，固定幂等键，未知受理不重提，已尝试费用保留待对账。删除作品不能清账；活动作业阻止源素材编辑、复制、删除及 TTL。
- 私有课堂鉴权、教师费用授权、当前审核版本/源哈希/费率配置绑定、限量输入输出和受保护下载；衍生结果均 `unreviewed`，不覆盖原片或自动发布。
- 课堂剪辑台新增真实能力/报价/任务面板，按需加载剪辑台。最终首屏脚本 **376.65 kB**、剪辑台分块 **138.77 kB**（构建报告的未压缩大小），不再触发单块超过 500 kB 的警告；不是实测加载延迟结论。
- [配置示例](../.env.example)、[生产配置示例](../deploy/golden-mic.env.production.example)、公开依赖锁与 SBOM 已同步；实际秘密环境文件未启用或修改。接口/上限见 [CLOUD_API.md](CLOUD_API.md)，腾讯安全配置与费用/隐私边界见[实施方案](CLOUD_CAPABILITY_PLAN_20260923.md)。

## 2. 最新完整验证

| 项目 | 本次实际结果 | 证据 |
| --- | --- | --- |
| 后端全量 | **795 项：793 通过、2 跳过，389.889 秒** | [最终日志](../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-final.log) |
| 页面回归 | **32/32，242.143 秒**；单 Edge worker、0 retries | [最终摘要](../canary_test/artifacts/cloud-pages-verified-20260923/summary.json) |
| 深层与云交互 | **42/42，239.027 秒**；单 Edge worker、0 retries | [最终摘要](../canary_test/artifacts/round2/cloud-verified-20260923/summary.json) |
| 云/单次调用保护/部署定向 | **287/287，129.719 秒**；包含全量中的用例，不能重复相加 | [定向日志](../canary_test/artifacts/prototype-refactor-20260923/cloud-enabled-contract.log) |
| 类型/构建/清单 | 应用及两套 E2E TypeScript、Vite 构建通过；4 个资源 SHA-256 全匹配 | [构建清单](../frontend/dist/ASSET_MANIFEST.sha256) |
| Python/部署一致性 | `compileall`、`pip check`、环境验证、离线 `uv lock --check` 通过 | [机器可读汇总](../canary_test/artifacts/prototype-refactor-20260923/cloud-validation.json) |
| 依赖审计 | 当前 Python **76 包、0 已知漏洞、0 跳过**；npm 全部依赖 **0 已知漏洞** | [Python](../canary_test/artifacts/prototype-refactor-20260923/cloud-pip-audit.json)、[npm](../canary_test/artifacts/prototype-refactor-20260923/cloud-npm-audit.json) |
| 锁文件/SBOM | 对冻结锁独立导出到 TEMP，与工作区两份产物逐字节哈希相等 | [生产锁](../requirements-production.lock)、[SBOM](../sbom.cdx.json) |

后端两项跳过仍为 Windows 符号链接权限及需显式开启的历史真实 TTS 离线回放；不是因缺 FFmpeg 跳过媒体验证。Python 3.11.9、Node 24.18.0、FFmpeg/ffprobe 9.0.2、Playwright 1.63.0，使用安装的 Edge。

**42 项的范围须分开理解：**31 项真实本地 API/媒体/课堂用例（含默认关闭云面板）＋11 项启用态云面板契约。后 11 项的所有云 HTTP 响应在浏览器中拦截，云 POST 不进入后台，明确标为合成契约测试；真实作品、报告、工程、成片字节保持不变。它们不证明腾讯连通、实际处理效果、收费或真实云流程验收。十种短片导出仍为本地 FFmpeg 验证，不是云输出。

## 3. 唯一一次实际 Kimi 请求

[实测报告](../canary_test/artifacts/cloud-live-20260923/report.json)及[不可重用回执](../canary_test/artifacts/cloud-live-20260923/receipt.json)已保留：

- 操作：`reference_narration`；仅自编的蓝色圆纸片/黄色方纸片文字，不含可识别人物、学生数据或真实新闻事件。
- 固定 Kimi 中国端点、`kimi-k3`、最多 256 输出 tokens、60 秒、**恰好一次**请求；未再次调用。
- 成功耗时 **9.266 秒**；服务商实际 usage：**377 输入＋175 输出＝552 tokens**。返回非空正文且保留未核实警示，源文本严格匹配。
- **预留 100 分（1 元）仍待对账，真实账单未知**。不按 usage 估算金额冒充实扣，也不把成功当作免费或自动退预约。总授权仍是一次性最多 5000 分，不是生产循环预算。
- 驱动永久消费唯一证据槽，不允许删回执、改标签或提高输出上限后重试。只证明参考解说适配器一次成功；字幕翻译、真实课堂云前端和腾讯媒体均不能借用此结论。

## 4. 发现并修复的问题

| 问题 | 修复与验证 |
| --- | --- |
| 预算正确但活动云任务缺少统一准入 | 账本同事务约束单作品 1 个、全局 1–8 个；未知/有远端风险的中断占槽，幂等重放不重复预约 |
| 媒体成功仅验证“文件可读” | 增加输入 120 秒/1080p/60fps、输出 4K、2× 超分、目标补帧 FPS、时长/音轨校验；明确仍只验证流属性，不代表增强效果 |
| 任意小报价被称为上界 | 标记为管理员预约额度；Kimi 保守本地许可下限；报价及确认明确“实际账单可能超过预留额” |
| 已撤审/停用付费后页面不能取消已有任务 | 独立 `can_cancel`，确认后刷新权限/任务；仅教师可取消，不放宽私有鉴权。11 项启用态用例覆盖撤审、停用及权限变化 |
| 页面切换丢失未知提交的幂等保护 | 发送前同步写入并回读最小同标签页回执，按真实 `role:id`/作品隔离；返回、刷新、同标签页重新登录不抹掉未知记录。损坏/读写失败禁止新消费；不声称跨标签页或清存储后仍可恢复 |
| 中文界面只能请求英文参考稿 | 参考解说也使用明确目标语言，默认简体中文；报价/确认披露语言 |
| 固定智能字幕模板与所选语言不匹配，上传后才失败 | 新审核语种配置、能力允许列表；本地已知不匹配在读取媒体/预约/上传前拒绝；远端模板漂移仍需供应商校验 |
| 报价不返回获准地域 | 返回配置绑定的 `cn`/腾讯地域，前端缺失则拒绝报价；配置不是独立数据驻留证明 |
| 统计区 `aria-label` 用于不可命名的普通 div | 增加 `role=group`；最新 R2-23 检查实际可访问组，axe 中该待审项消失 |

云作用域 320px/20px 最新 axe：0 violations、0 incomplete；老师小屏仍有 11 个渐变背景对比度节点待人工测量。全页其它渐变、部分遮挡时间刻度、视频字幕存在性待审项未被静默记为通过。遮罩截图只是当前视口/显式滚动位置，不是全页像素一致性、读屏或硬件认证。

## 5. 失败与中间证据不覆盖

- [249 项契约旧失败](../canary_test/artifacts/prototype-refactor-20260923/cloud-contract-verified.log)：容量 HTTP 429、新费用下限及 TTL 并发夹具三项旧预期；修正夹具，没有削弱准入/预算。
- [旧全量 785 项](../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-full.log)：783 通过/2 跳过、532.420 秒；早于最新 10 项及启用态修复，不是最终结果。
- [首轮启用态 41/42](../canary_test/artifacts/round2/cloud-enabled-20260923/summary.json)：丢失回执测试漏处理初始未保存工程的丢弃确认；测试修正后[11/11 定向复测](../canary_test/artifacts/round2/cloud-enabled-receipt-retest/summary.json)，未改产品确认逻辑。
- [第二轮 41/42](../canary_test/artifacts/round2/cloud-final-20260923/summary.json)：R2-13 在 `browserContext.newPage` 时 Edge 意外关闭，尚未到业务断言；未抑制失败或调整断言。第三轮新进程/新种子完整 42/42，不能以跨轮拼接代替。
- PowerShell 将 Python 正常 stderr 显示为 `NativeCommandError`；实际退出码及 unittest 最终摘要是判断依据。一次只读 Node 汇总多余括号导致语法失败，修正命令重查，不涉及产品或测试变更。

## 6. 数据、服务与剩余条件

- 最终 8766/8769/8770 临时服务已正常关闭，8765–8770 检查无监听；没有启动/修改日常 8000 服务。TEMP、副本、失败档案及唯一付费回执保留。
- [深层主机清单](../canary_test/artifacts/round2/current/manifest.json)在关闭时复核 **259 个允许读取的原始文件哈希全部一致**；[空主机清单](../canary_test/artifacts/round2/empty/manifest.json)无种子媒体。历史完整 live 原片的 4 项 QC 阻断不变。
- 腾讯尚缺实际 MPS/COS 凭据、私有桶、处理地域、审核模板/语种、费率/预算配置；不擅自购买、创建资源或上传人物素材。密钥由操作者在本机安全配置，**不要贴入聊天**。
- 还缺腾讯真实效果/取消/重启/清理/费用验收、人工对账/恢复产品与自动清理队列；当前 COS 清理仅当前对象，不涵盖历史版本/分片/内部副本。
- 113 项本地原型目录仍为 11 渲染、38 有限、6 元数据、58 未接入。六种云作业不是补齐全部高级工具；多机位、跟踪、完整专业时间线、HDR/8K、协作/社交发布等仍待逐项实现和验收。
- 不承诺主机断电事务原子性、生产 Linux/负载、实物打印/麦克风质量。Windows Proactor 10054 媒体断连回调及 Starlette/httpx 弃用提醒仍保留，未通过吞警告制造无错误印象。