# 三模式最终验收记录（2026-09-28）

> **FINAL：本轮 coding todos 已完成，本地 synthetic 有界验收通过。最终 L browser 8/8、0 retry，主机已完成正常 lifespan 退出；不是整个产品/生产能力无条件签收。三模式未部署日常 `http://127.0.0.1:8000`，未部署生产。** 本次仅更新四份授权现有文档，只读核对已有证据；不重跑测试、构建、服务或 Provider，不触碰实际环境/用户 data。API 见 [THREE_MODE_API_20260928.md](THREE_MODE_API_20260928.md)，源码映射见 [THREE_MODE_IMPLEMENTATION_20260928.md](THREE_MODE_IMPLEMENTATION_20260928.md)。

## 1. 当前结果表（各 run 独立，不累计成通过总数）

| 范围 / run | 已知结果 | 来源与限制 |
| --- | --- | --- |
| Backend safe full：`workspace-validation-20260928-132643-628340` | **1118 项，1116 pass、2 skip、0 failure、0 error**；expected failures / unexpected successes 均 0；测试 887.556619 秒，总计 890.898581 秒 | 从用户指定 launcher stdout 定位精确 run，核对该 run 的 [summary](../canary_test/artifacts/workspace-validation-20260928-132643-628340/summary.json) 与 [backend log](../canary_test/artifacts/workspace-validation-20260928-132643-628340/backend.log)。不是从目录时间猜测或选另一份绿色历史。 |
| Frontend 最新主集成反馈 | **459 pass；3 项 typecheck 通过** | 三项为产品、workspace E2E、modes E2E。该数字由本次任务/主集成提供，本次不重跑，尚未补原始前端 run 路径/散列。不将额外 standalone Node tsconfig 的结果包含在“三项”中或声称所有配置通过。 |
| Browser J：`modes-cache-20260928-j` | **failed：7 pass、1 fail / 8 计划项**，retry 均 0；350094.72500000003 ms；globalErrors=1 | MODES-01…07 passed，MODES-08 failed；失败原因按主集成诊断为旧 heading 断言。8 份 safety 均 pageErrors=0、unexpectedDialogs=0。 |
| Browser K：`modes-integrated-20260928-k` | **failed：6 pass、1 fail、1 未运行 / 8 计划项**，retry 均 0；334793.63399999996 ms；globalErrors=1 | MODES-01…06 passed，MODES-07 failed，MODES-08 未运行。离开非空草稿的 confirm 未在 harness 登记，MODES-07 unexpectedDialogs=1；7 份 safety 的 pageErrors 均 0。不能把 globalErrors 当 pageerror，也不能把 6/7 写成套件通过。 |
| Browser L：`modes-verified-20260928-l` | **passed：8/8，262.979523 秒，0 retry，globalErrors=0** | 最终独立 [L 构建清单](../frontend/dist-canary-modes-20260928-l/ASSET_MANIFEST.sha256)；`bindingUnchanged=true`、`frontendSourcesBoundToBuild=true`、`noPaidCalls=true`。23 layout 零 overflow，8 safety reports 零 pageerror/blocked/unexpectedDialogs/cleanupError；精确范围及停机见下文。 |

L 的 MODES-01…08 在同一次 run 全部通过，不是拼接 J/K 的绿色用例。J/K 及更早失败证据不删除、不改写为通过。MODES-03 覆盖八句 C 的完整十阶段制作、合法 trim 修订与真实 1080p FFmpeg 导出；MODES-05/06 覆盖短 A/B。MODES-04 是完整 B 样例预检/匹配，**没有渲染全部 16 句**。

## 2. 精确证据定位与绑定

以下 TEMP 定位符只用于本机证据定位，不是公开下载链接，不含 token/请求体/稿件。TEMP 根按本机环境展开；不读取原始敏感错误转储。

| 项目 | 精确定位 |
| --- | --- |
| 唯一指定后端 launcher stdout | %TEMP%/gm-modes-final-core-20260928-212643-3986420.out.log |
| 从该 log 解析出的后端证据 | [canary_test/artifacts/workspace-validation-20260928-132643-628340/summary.json](../canary_test/artifacts/workspace-validation-20260928-132643-628340/summary.json)；[backend.log](../canary_test/artifacts/workspace-validation-20260928-132643-628340/backend.log) |
| 该次后端独占 TEMP | %TEMP%/golden-mic-core-validation-svbevq5n |
| J host manifest | %TEMP%/gm-modes-acceptance-20260928-j.json |
| J summary | %TEMP%/golden-mic-modes-8ygufw0o/browser-runs/modes-cache-20260928-j/summary.json |
| K host manifest | %TEMP%/gm-modes-acceptance-20260928-k.json |
| K summary | %TEMP%/golden-mic-modes-cxu1z52a/browser-runs/modes-integrated-20260928-k/summary.json |
| J/K safety | 各自 run 下 test-results 各用例目录中的 safety.json；只提取计数，不转录请求内容。 |
| L 独占根 | %TEMP%/golden-mic-modes-42od7mnp |
| L host manifest | %TEMP%/gm-modes-acceptance-20260928-l.json |
| L browser run | %TEMP%/golden-mic-modes-42od7mnp/browser-runs/modes-verified-20260928-l；其 summary.json 与 binding.json 为结果/绑定，test-results 为脱敏用例 JSON。 |
| L 构建 | [frontend/dist-canary-modes-20260928-l/ASSET_MANIFEST.sha256](../frontend/dist-canary-modes-20260928-l/ASSET_MANIFEST.sha256)；清单 SHA-256：`27bb22d96b0eab93b1a6fab91a6cbff5ed1f3df10d4cf4e4f2b57191ce23a7fd`。未更新共享日常 build。 |
| Backend source 基线 | [source-before.json](../canary_test/artifacts/workspace-validation-20260928-132643-628340/source-before.json)，106 个 source。 |

后端 started_at 为 **2026-09-28T13:26:43.630341+00:00**，finished_at 为 **2026-09-28T13:41:34.530081+00:00**。launcher 的本地时间标签与 UTC run 名不同，不能因此选错 run。启动行携带初始化 `status=setup_failed`，**最终摘要为 passed**；后端原始终结行为 `Ran 1118 tests in 887.557s`、`OK (skipped=2)`，与精确摘要一致。

后端摘要记录 Python 3.11.9、FFmpeg 9.0.2，106 个 source 文件检查、`source_drift=[]`、`remaining_owned_http_listeners=[]`。这是**该次运行结束时**的记录，不是全机清理声明；最终独立复核见下表。两个 skip 为：显式 opt-in 的本地 Canary 音频重放、Windows 无 symlink 创建权限；不能把 skip 计为 pass。

J/K 摘要均记录 `bindingUnchanged=true`、`noPaidCalls=true`。J 的 `frontendSourcesBoundToBuild=false`，使用 [A 构建清单](../frontend/dist-canary-modes-20260928-a/ASSET_MANIFEST.sha256)；K 为 `frontendSourcesBoundToBuild=true`，使用 [K 构建清单](../frontend/dist-canary-modes-20260928-k/ASSET_MANIFEST.sha256)。这些仅是各自历史绑定，不能将 J 视为最终源码对应构建，也不拿 K 替代 L。

### 最终独立只读散列复核

主集成提供的独立复核完成于 **2026-09-28T13:53:56.623Z**，以下集合 **ALL matched**；这是散列检查，不是重跑套件。各集合可能重叠，不累计为唯一源码文件总数。

| 集合 | 文件数 | 结果 |
| --- | --- | --- |
| L 前端 source → 独立 build 绑定 | 31 | 全部匹配 |
| L build assets | 4 | 全部匹配，清单 SHA 如上 |
| 最新 backend source 基线 | 106 | 全部匹配，source unchanged |
| L harness 绑定 source | 56 | 全部匹配；不表示 56 个文件全是测试 harness 实现 |
| L test 绑定 | 11 | 全部匹配 |

### L C 成片、发布门禁与不可变 R0

合成任务 ID：`9153dc2ec2fb4cb5b623dbabfbc475f8`；证据为 L 的 MODES-03 脱敏 complete-original JSON 与任务保留记录，不含 capability。

| 检查 | 最终结果 |
| --- | --- |
| 输入与初版 | 8 quotes，十阶段完成；初版实测 **60.066667 秒**。人工注入文本/词时，不是真人语音证据。 |
| 合法 trim | 提交真实源连续区间，生成 **R1**；当前稿句/报告同步实际保留 ASR 文本。 |
| 发布门禁 | 确认前直接 export **409**；确认 **10 项 checks** 后正式输出可下载。 |
| 正式导出 | **59.566667 秒，31,579,719 字节，1920×1080 / 30fps / H.264 / AAC / 48 kHz**。 |
| 输出 SHA-256 | `c171c0260e2412b0f5767519e0d4cd8a2476913d57cff86b47d484b6cb1dfdde` |
| R0 完整性 | **62/62 文件 SHA 不变**；不把新 revision、导出或 preview cache 写进旧修订。 |
| Provider 边界 | C **无 TTS、无重复 ASR**；整轮无真实付费 Provider 调用。 |

### L 布局、安全、恢复与视觉覆盖

- **23 份 layout = 21 regular + 2 missing-quote**，覆盖 320/900px、20px 字体；实测零 overflow。两份 missing-quote 布局与常规 21 份分列，不重复计数。
- **8 份 safety reports**：`pageErrors=0`、`blockedRequests=0`、`unexpectedDialogs=0`、`cleanupErrors` 合计 0；套件 `globalErrors=0`。这不表示服务端停机日志无 callback。
- L browser run 内共 **41 份 JSON**，无 raw artifacts（原始 DOM/错误上下文、trace、视频或自动截图）。这是 browser run 目录范围，不把 TEMP 根中的原生诊断日志或父代理单独目视检查混算其中。
- **恢复 fixture 显式预置快照，验证主动读取/只读恢复路径；不是产品 auto-save hook E2E。** 真实保存 hook 已有单元测试覆盖，不能拼成完整浏览器自动保存证据。
- **父代理已目视检查实际结果页纸感 UI 的截图，仅当前 viewport**；不是完整页面像素比较、逐帧像素验收或完整 WCAG 签收。

### L 最终主机退出

最终 manifest 与 8786 监听复核确认：`serverState=stopped`，`shutdownComplete=true`、`lifespanShutdownComplete=true`、`fakeProviderStopped=true`、`inputHashesUnchanged=true`，**8786 无监听**。只声明所拥有 L 主机的退出，不扩大为全机无进程/无残留；TEMP、合成任务与失败证据保留。

**退出仍有 3 次 WinError 10054 / Proactor callback。** 正常 lifespan 收尾不等于日志 clean，更不证明稳定性根治。manifest 中 **`errors` 与 `sourceHashesUnchanged` 字段实际缺失**；投影得到的 `null` 不是 `errors=[]`，也不是源码不变证明。源码完整性使用上述独立散列复核，不从缺失字段推断。此次正常退出不补写 C26 历史退出证据缺口。

## 3. 本次文档同步的代码事实

- **长片 simple export**：三模式 MP4/MP3 用服务端生成的固定 `SimpleModeProject`，预算 `min(600, settings.max_total_source_duration_seconds)` 秒；不是把公共 `Project/Clip` 上限改大。legacy Studio 任意轨道/嵌套/旧 simple 的 120 秒不变。128 MiB、85% 预估余量拒绝高码率及实际大小/完整时长校验保留，MP3 无损中间文件另受预算限制。GIF 只前 6 秒，完整源仍须在预算内。不是任意长片/码率或性能签收。
- **失败恢复**：`submissionRecovery` 在接受 task 回执后保存同浏览器/同源 30 天快照，task 绑定、明确点击才读；之后只读核对上传。快照 TTL 不延长媒体 72 小时，媒体缺失/过期不能恢复可用素材，清 storage 后无持久快照不能找回；未完成文件/整篇录音须重选。无跨设备或服务端 creation context 恢复，无自动重传/ASR/重提。存储失败的同生命周期内存回退不等于可靠持久恢复。
- **开发模板**：已为 20 files / 1 running / 5 pending，legacy `TASK_TTL_HOURS=0` 刻意保留；新模式固定 72 小时。不能为三模式把开发 TTL 改为 72 而给 legacy 普通任务误启清理；旧 `local_only` 仍保护。本次未改模板/真实环境。
- **修复与兼容**：合法 trim/take 更新当前 ASR 原话、句子清单与报告，旧稿在旧 revision；主 export adapter 内部 path/raw_path 修复 self-gate，但外部 API、授权/QC/修订门禁保留。preview JPG/日志写任务级 public_previews，不写不可变 revision，缓存键绑定源位置。legacy 清理复核无 active classroom；旧任务兼容、后台 Studio 及 `replace-shot` 的 `sentence_id + instruction` 两字段旧契约保留，不列为缺陷。本次不执行删除/迁移。

## 4. 最终结论与必须保留的缺口

1. **Synthetic only**：合成视频/音调、注入文本与人工词时、假 Provider 加真实 FFmpeg。L 的通过与 `noPaidCalls` 不代表真实 ASR、词边界、说话人身份、发声自然度、新闻事实、主观试听已验；未发真实付费 Provider 请求，没有新增真人质量/perf 结论。
2. SNR **15 dB**、`quote_caption=asr/none` 不变；真实 ASR/SNR/人工事实核查缺口保留。source matching 200 ms SLA 未达，旧性能数字不作当前基准，预转写倍率/长片负载/生产 SLA 未验。
3. raw preview **无强制烧录水印**；UI 样片提示、正式下载/export 门禁不是 DRM。preview cache 修复不补水印缺口。
4. L 正常停机仍有 **3 次 Windows 10054/Proactor callback**，历史 native Python crash **未证明根治**；后端绿色或 pageErrors=0 不能证明稳定性风险消失。Linux/生产、长时负载、备份恢复、断电原子性、完整 WCAG、真实麦克风仍未验。
5. J/K 失败记录与 TEMP 保留；L 正常退出/8786 无监听已有证据，但不声明其他服务、全机进程或 C26 历史退出正常。`errors/sourceHashesUnchanged` 缺失不写成 clean；独立 hash 结果与前端反馈的原始日志定位限制分开披露。
6. **本轮 coding todos 和有界本地验收已完成，不再等待 L。** 当前状态为“本地合成验收通过，有明确缺口”，不是整个生产系统已完成/已上线。日常 8000、共享 build、实际环境、真实用户 data 与付费 Provider 均未因本轮文档收尾而修改或调用。