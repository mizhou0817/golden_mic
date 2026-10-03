> **已退役 · 2026-09-27：以下为历史课堂/账号 Canary 与原型验收记录，不是当前测试套件或运行指南。** 当前范围见[核心工作区契约](../docs/CORE_WORKSPACE_20260927.md)，当前浏览器入口见[无登录工作区说明](../frontend/e2e/README.md#current-no-login-workspace-suite)。旧页面/Round 2 登录场景、种子/主机、隔离测试及 C25/C26 一次性构建任务已移除；下文命令、端口、源码链接及通过数仅对当时版本有效，不应复跑或据此恢复已删除账号功能。
>
> 原始 artifacts、付费回执、测试日志、隔离构建、源媒体与原型保留不动。历史 C26 的 **1148 后端 / 37 页面 / 69 深层 / 260 Node** 是当日记录，不能当成当前套件数量或本次回归结果；原生崩溃与停机证据缺口也没有因退役而解决。本次只清理测试入口和文档，没有运行套件、服务或付费请求。

# Canary与原型重构历史验收

2026-09-27 清理的完整文件/任务清单、静态验证范围与交接项见[退役清单](retired-harnesses-20260927.json)：移除 **67 个旧入口/支持文件**和 **5 个一次性构建任务**，保留新工作区、产品测试及通用素材/媒体夹具。这是清理记录，不是新的产品验收证据。

## 历史 C26 全面审查：2026-09-26

**最新完整本地复验已结束；原生 Python 崩溃仍为 HIGH／高风险未解决，不构成生产就绪结论。** [C26 主报告](CANARY_C26_20260926.md)、[机器汇总](artifacts/c26-audit-20260926/validation.json)与下列已有证据记录本轮；C25/C24 的通过数、失败与停机记录保留在后面的历史章节，不累计、不改写。

| 验证 | C26 最终完整结果与已有证据 |
| --- | --- |
| 完整后端 | **1148 tests：1145 通过、3 跳过，623.067s，exit 0**；[日志](artifacts/c26-audit-20260926/backend-full.log)。新增 **24 个 Studio 失败/授权契约 + 3 个真实主应用授权场景已包含在内**，不得再加一次。 |
| 页面 | **37/37，309646.872ms**；[最终摘要](artifacts/c26-pages-final-20260926/summary.json)。 |
| 深层 | **69/69，843674.39ms，19 个 spec 文件**；[最新完整摘要](artifacts/round2/c26-deep-verified-20260926/summary.json)。**58 项真实本地后端（含空课堂）+ 11 项明确模拟云 UI**，不是腾讯实测；页面/深层均为 **1 Edge worker、0 自动重试**。 |
| 7 套 Node 契约 | [会话 45](artifacts/c26-audit-20260926/test-classroom-session-final.log)、[时间线 17](artifacts/c26-audit-20260926/test-timeline-editing-final.log)、[偏好 12](artifacts/c26-audit-20260926/test-workbench-preferences-final.log)、[组合 25](artifacts/c26-audit-20260926/test-studio-compositions-final.log)、[素材 40](artifacts/c26-audit-20260926/test-studio-assets-final.log)、[代理/作业 70](artifacts/c26-audit-20260926/test-studio-proxy-final.log)、[序列 51](artifacts/c26-audit-20260926/test-studio-sequences-final.log)，合计 **260 通过**，不代替浏览器/真实主机认证证明。 |
| 类型与构建 | `typecheck`、`typecheck:canary`、`typecheck:round2` 全通过；[最终独立构建](../frontend/dist-canary-c26-final-20260926/ASSET_MANIFEST.sha256) **1793 模块、7.20s、4 个资源**。[页面 8768 HTTP](artifacts/c26-audit-20260926/http-final.json)及[最新深层 8769/8770 HTTP](artifacts/c26-audit-20260926/http-verified.json)均匹配该构建；前者的旧深层主机记录不能代替后者。 |
| 环境与审计 | [环境记录](artifacts/c26-audit-20260926/environment-final.log)：编译、`pip check`、环境校验、离线 uv 检查通过。[Python 审计](artifacts/c26-audit-20260926/python-audit.json) **76 包、0 已知漏洞、0 跳过**；[npm 审计](artifacts/c26-audit-20260926/npm-audit.json) **0 已知漏洞**。不等于原生崩溃已解决。 |

### 六项新增浏览器覆盖与修复范围

原 18 个深层 spec 保留；[新增审查 spec](../frontend/e2e/round2/audit.round2.spec.ts)贡献 **6 项真实本地用例**，不是页面套件新增六项：

- **C26-NAV-01–04**：原生 Back 取消等待中的私有 GET；Tab→Enter 跳到主要内容只移动焦点、保留作品 hash 并可刷新重开；重选当前“我的作品”取消等待后可再次打开；“新作品”在等待清空草稿前取消旧 GET，真实空白保存不被迟到响应覆盖。最后一项允许且核验明确的清空/空白保存 POST，不冒称零写入。
- **C26-MC-01**：机位追加避开未激活子序列的全工程 ID，真实保存、撤销、重开均保持唯一；原生组合契约还覆盖 replace 分支。不是自动同步或完整多机位实现。
- **C26-LAYOUT-01**：匿名登录、结果、Studio ×16/20px，**6 次 305px 可用内容宽度采样**；模型为 320−15px 滚动条空间，**不是 headless 原生 Windows 滚动条模拟**。另一次真实集成浏览器观察是窗口 320、client 305、body 320、横溢出 15px；改为 `min-width:0` 后 client/scroll 均为 305，没有隐藏溢出或缩字。详细方法见[深层覆盖](../frontend/e2e/round2/README.md)。

Studio 在读取 body 后及异步探测后/作业准入前重查授权；真实教师撤销、取消核对、注销均不得留下新作业或工程写入。失败/取消终态与 `cleanup_pending`/清理错误独立持久化，残留仍占额度；授权 GET 每次只尝试一次本地清理，UI 警告及 GET 按钮不编码、不重提作业、不豁免配额。成功清单先写入再发布成功结果，失败清空成功引用；不承诺断电原子性。BT.709 文案更正为实际 SDR 有限范围 YUV 转换，不扩大 HDR 能力。

相对 C25 的产品源码修复共六处：[backend/studio.py](../backend/studio.py)、[frontend/src/App.tsx](../frontend/src/App.tsx)、[frontend/src/components/Studio.tsx](../frontend/src/components/Studio.tsx)、[frontend/src/lib/studioApi.ts](../frontend/src/lib/studioApi.ts)、[frontend/src/lib/studioCompositions.ts](../frontend/src/lib/studioCompositions.ts)、[frontend/src/styles.css](../frontend/src/styles.css)。[来源记录](artifacts/c26-audit-20260926/final-source.json)分列最初五处及后端整轮后唯一 CSS 变更；没有把测试辅助修正当成额外产品功能。

### 保留失败与 HIGH 未解决运行时风险

- [第一次完整深层](artifacts/round2/c26-deep-final-20260926/summary.json) **68/69，806547.705ms**，仅 C25P-01 切回原源的监看 `currentTime` 差一微秒。[真实字节原生实验](artifacts/c26-audit-20260926/native-seek-precision.json)复现 Edge **2.007592→2.007591s**，二进制浮点乘 10⁶ 得 **2007591.9999999998**。仅该切回监看断言容许 **1e-6 秒 + 浮点噪声**；精确源标记、媒体字节、缓存及权限断言未变。[定向复验](artifacts/round2/c26-proxy-clock-retest-20260926/summary.json) **1/1** 后又跑完整套件，不能相加冒充整轮。
- [后续深层 closure](artifacts/round2/c26-deep-closure-20260926/summary.json) **61/69，744784.912ms**：C25S-02 功能断言结束后清理连接失败，随后七项连接失败；**不是八个独立业务缺陷**。[Windows 原生事件](artifacts/c26-audit-20260926/native-host-crash.json)：**2026-09-26T14:23:26Z，Python 3.11.9，故障模块 python311.dll，c0000005，offset 0x205cbe，PID 22888**。根因未定位；最新 69/69 仅增加 `-X faulthandler` 诊断，**没有环境升级，不能宣称原生崩溃已修复**。未读取/复制 dump，未收集认证局部变量。
- 崩溃绕过原主机 finally；[独立崩溃后源核验](artifacts/c26-audit-20260926/crash-source-recheck.json)确认 **259 个 SHA 一致**，未回写旧清单假装正常停机。崩溃时未删完的 TEMP 副本及所有失败记录继续保留。

### 最终完整性、停机与边界

[已核验浏览器/完整性记录](artifacts/c26-audit-20260926/browser-integrity-verified.json)：**163 份 axe、0 violations**；incomplete 为 **1764 个对比度 + 45 个字幕节点出现次数**，不是独立缺陷数或 WCAG 通过。**31 份矩阵布局报告、150 次采样、0 横向溢出**；上述新增 305px 的六次采样另列，不混成 31 份矩阵报告。**106 份用例观察，0 problems/errors**，不撤销先前原生崩溃。页面 **564 原文件 SHA 一致**；84 个源码/依赖绑定相对最终绑定未变，共享构建、旧 C25 构建、最终构建与唯一付费回执未变。初次 Node 散列读取遇 `EBUSY`，空的[初次完整性输出](artifacts/c26-audit-20260926/browser-integrity-final.json)保留；这不是篡改证据，后续独立核验全匹配。

[最终停机核验](artifacts/c26-audit-20260926/shutdown-verified.json)：本轮 **8768/8769/8770 无监听、无验收 Python 进程**，未终止用户进程。**只有页面主机清单记录 `stopped`**；最新深层两个重定向诊断终端由 Ctrl-C 退出，未更新 finally 清单，仍为 `startup_complete`。其 `errors=[]` **不是正常 lifespan/清理成功的证明**。[最新深层独立源核验](artifacts/c26-audit-20260926/final-deep-source-recheck.json)确认 **259 个 SHA 一致**，没有回写清单；不能与页面 564 或旧崩溃的 259 相加。所有 TEMP/失败证据保留。

**新增付费请求 0**；历史唯一 Kimi **100 分（1 元）预约仍待对账、实扣未知**，不重跑；腾讯实测仍缺批准配置。113 项分类保持 **58 partial / 8 metadata / 11 renderer / 36 unsupported**，不是 77 项完整实现或全 113 完成。原 live 四项 QC 阻断、实物麦克风/打印、生产部署/负载与原生崩溃风险未消除；**共享构建未更新、日常 8000 未启动**。

### 历史 C26 复跑前置条件（入口已退役，不执行）

遵循[页面说明](../frontend/e2e/README.md)与[深层说明](../frontend/e2e/round2/README.md)：**主机及 Playwright 两端终端 PATH 都要有 FFmpeg/ffprobe**，动态定位当前 WinGet 包；新建构建目标、TEMP 主机、种子/结果标签，保留旧失败和一次性任务目标。先确认真实 `startup_complete` 与监听，不能只看旧清单；完整深层需 `ROUND2_EMPTY_SETUP=1`，不用时取消变量而非设 `0`。保持 **1 Edge worker、0 自动重试**、脱敏证据、默认关闭云端；不接管既有服务或重跑付费请求。诊断开关不是崩溃修复，也不授权环境升级或生产部署。

## 历史 C25 闭环：2026-09-26

以下数字、构建及停机状态仅对应 C25，不替代上方 C26 结果。

本地闭环验收完成；[本轮报告](CANARY_20260926.md)、[机器汇总](artifacts/p25-closure-20260926/validation.json)、[实现说明](../docs/PROTOTYPE_IMPLEMENTATION_20260925.md)及[113 项能力矩阵](../docs/PROTOTYPE_CAPABILITY_MATRIX_20260925.md)记录范围与限制。以下均为本轮独立结果，不累加历史/筛选复验。

| 验证 | 最终结果与现有证据 |
| --- | --- |
| 完整后端 | **1121 tests：1118 通过、3 跳过，937.139s，exit 0**；[日志](artifacts/p25-closure-20260926/backend.log)。 |
| 页面 | **37/37，322225.95ms**；[最终摘要](artifacts/p25-pages-final-20260926/summary.json)。 |
| 深层 | **63/63，855637.336ms**：52 项真实本地后端 + 11 项明确模拟的启用态云 UI，**不是腾讯实测**；[最终摘要](artifacts/round2/p25-deep-final-20260926/summary.json)。两套浏览器均为 1 Edge worker、0 自动重试。 |
| 7 套 Node 契约 | 会话 **45**、时间线 **17**、偏好 **12**、组合 **23**、素材 **40**、代理 **50**、序列 **51**，合计 **238 项通过**；不等于浏览器/服务器认证证明。各日志位于本轮闭环证据目录。 |
| 类型、构建与环境 | 应用及两套 E2E 类型检查通过；[独立构建](../frontend/dist-canary-p25-closure-20260926/ASSET_MANIFEST.sha256) 4 个资源分别在 [8768](artifacts/p25-closure-20260926/http-pages-final.json)、[8769/8770](artifacts/p25-closure-20260926/http-deep-final.json) 经 HTTP 哈希验证。`pip check`、编译、环境校验及离线 uv 检查通过；[环境日志](artifacts/p25-closure-20260926/environment-checks.log)、[Python 审计](artifacts/p25-closure-20260926/python-audit.json) 76 包/0 已知漏洞、[npm 审计](artifacts/p25-closure-20260926/npm-audit.json) 0 已知漏洞。 |

深层在原 42 项之上增加 **21 项 C25**：原型 9、素材 4、代理 3、序列 4、冷深链 1。冷 work/show 深链现在先进入只读 history，打开作品时不挂载创作向导或自动保存，正常离开创作页仍保存草稿；[C25D-01](../frontend/e2e/round2/deep-link.round2.spec.ts)挂起真实 work GET、打开前后各推进 1500ms（越过实际 800ms 自动保存防抖），验证规范化的非空 step-3 完整草稿不变且浏览器写请求为 0。P25-08 的唯一 workbench POST 断言通过，页面 23 已按“删除组并取消全部序列归属”定位。

### 保留失败，不改写为全绿

- 中断前[页面 complete](artifacts/p25-pages-complete-20260926/summary.json) **36/37**（旧分组按钮定位），[深层 complete](artifacts/round2/p25-deep-complete-20260926/summary.json) **60/62**（页面崩溃与意外草稿 POST）仍是失败记录。
- [页面 closure 首次尝试](artifacts/p25-pages-closure-20260926/summary.json)前置条件未就绪，**0 用例执行、118433.971ms**，不是 37 项业务失败；标签与证据保留。
- [深层 closure](artifacts/round2/p25-deep-closure-20260926/summary.json) **61/63**：Playwright 终端 PATH 缺 ffprobe，两个 `spawn ffprobe ENOENT`。补齐该终端 PATH 后[针对性复验](artifacts/round2/p25-media-path-retest-20260926/summary.json) **2/2**，随后全新整轮 **63/63**；不把两轮相加。

### 完整性、费用与未覆盖边界

[浏览器及完整性汇总](artifacts/p25-closure-20260926/browser-and-integrity-verified.json)：163 份 axe 报告 **0 violations**，但对比度 **1764**、字幕 **45** 个 incomplete **节点出现次数**仍需人工复核；31 份布局报告、150 次采样无溢出，不是像素级一致或 WCAG 认证。页面 **564** 原文件与深层 **259** 允许读取源分别哈希一致，**不能相加**；共享前端 4 个资源未变，本轮未部署共享构建。

[停机记录](artifacts/p25-closure-20260926/shutdown.json)确认仅本轮拥有的 **8768/8769/8770** 主机已停止，`errors=[]`。恢复时旧 **8000/8766/8767/8771 已无监听**，没有停止它们，也不声称保留其旧 PID；所有 TEMP 与失败证据保留。

新增付费请求 **0**；历史唯一 Kimi 的 **1 元预约待对账、实扣未知**，不得重跑；腾讯缺经批准的配置，未实测。当前 113 项为 **58 partial / 8 metadata / 11 renderer / 36 unsupported**，不是 77 项完整实现，partial 仍有缺失子功能。本地高级剪辑、HDR、完整多机位/跟踪等并非仅缺凭据；原 live 四项 QC 阻断、硬件及生产验收不由本轮消除。

### 历史 C25 复跑前置条件（入口已退役，不执行）

详见[页面说明](../frontend/e2e/README.md)与[深层说明](../frontend/e2e/round2/README.md)。**主机和 Playwright 两端的终端 PATH 均须包含 FFmpeg 与 ffprobe**；新终端不继承另一终端的临时 PATH，动态定位当前 WinGet `Gyan.FFmpeg`，不要固定旧 9.0.1 路径。先等待清单 `startup_complete` 及真实监听；页面种子会散列 48+8 个视频（样例 2 约 919 MB）并复制历史档案，OneDrive 可慢于 runner 就绪检查窗口，检查不会准备种子。

每次选新构建目标、页面种子及结果标签，**不得重跑已使用的一次性构建任务或复用证据标签**；Round 2 另用新 TEMP 主机和自己的归档清单槽。完整深层设置 `ROUND2_EMPTY_SETUP=1`，不用时取消变量，**不要设 `0`**。Windows 筛选正则含 `|` 时直接使用 Node Playwright CLI，不经 npm/cmd 管道。零自动重试、无原始认证转储；不接管既有服务、不部署、不重跑付费调用。

## 历史 C24 Canary：2026-09-24

以下数字与端口状态仅对应 C24 当日，不代表 C25 或当前仍有监听。

本轮[问题与修复日志](CANARY_20260924.md)已收口：**845项后端（843通过/2跳过）、37/37页面、42/42深层、45/45会话传输**。本轮使用全部48+8评测视频及DOCX的隔离副本，新增会话和可访问性修复；历史通过数未借用。

- [最终机器汇总](artifacts/c24-validation-20260924/validation.json)、[后端日志](artifacts/c24-validation-20260924/backend-final.log)、[页面摘要](artifacts/c24-pages-verified/summary.json)、[深层摘要](artifacts/round2/c24-deep-verified/summary.json)。
- [基线32/35失败](artifacts/c24-pages-baseline/summary.json)、[匿名恢复失败](artifacts/c24-expired-session-baseline/summary.json)、[放映测试竞态41/42](artifacts/round2/c24-deep-final/summary.json)保留；没有跳过或改成预期失败。
- 本轮只关闭8768/8769/8770；既有8766/8767/8771及共享构建不变。页面564原文件和深层259允许读取源分别哈希一致，不能相加。
- 新增付费请求0。云启用态11项为明确拦截的合成契约，腾讯真实处理/58项高级缺口及硬件/生产验收不冒充完成。修复在源码和[独立构建](../frontend/dist-canary-c24-verified/ASSET_MANIFEST.sha256)，未热替换使用中的实例。

## 历史云接入验收：2026-09-23

该历史阶段以[云接入验收报告](../docs/CLOUD_ACCEPTANCE_20260923.md)为准：后端 **795项，793通过/2跳过，389.889秒**；页面 **32/32，242.143秒**；深层 **42/42，239.027秒**。深层包含11项合成云响应的浏览器契约，不是腾讯实测。Python76包与npm全部依赖审计均0已知漏洞。证据：

- [后端日志](artifacts/prototype-refactor-20260923/cloud-backend-final.log)、[页面摘要](artifacts/cloud-pages-verified-20260923/summary.json)、[深层摘要](artifacts/round2/cloud-verified-20260923/summary.json)。
- [机器可读汇总](artifacts/prototype-refactor-20260923/cloud-validation.json)、[Python审计](artifacts/prototype-refactor-20260923/cloud-pip-audit.json)、[npm审计](artifacts/prototype-refactor-20260923/cloud-npm-audit.json)。
- [唯一Kimi实测](artifacts/cloud-live-20260923/report.json)：自编非人物合成文字，9.266秒、377输入/175输出token，1元预约待对账、实扣未知；不可重跑。腾讯缺配置仍未实测，真实环境开关未改。

最终8766/8769/8770已停止，8765–8770无监听；深层主机复核259源文件哈希未变。保留[测试辅助逻辑失败](artifacts/round2/cloud-enabled-20260923/summary.json)与[Edge建页意外退出](artifacts/round2/cloud-final-20260923/summary.json)，均不冒充最终全绿。原完整live四项QC阻断不变。

## 接云之前的核心重构基线

以下是[原型重构历史报告](../docs/PROTOTYPE_REFACTOR_20260923.md)，不用于替代上述当前代码测试。

- 后端：**517项，515通过/2跳过，398.389秒**；[日志](artifacts/prototype-refactor-20260923/backend-verified.log)。
- 页面与原型交互：**32/32**；[摘要](artifacts/prototype-refactor-final/summary.json)。
- 深层功能／十种真实导出／空课堂／删除无残留：**30/30**；[摘要](artifacts/round2/prototype-final/summary.json)。
- [验证汇总](artifacts/prototype-refactor-20260923/validation.json)、[实际清理清单](../docs/REFACTOR_CLEANUP_20260923.md)。

复现入口：[页面套件说明](../frontend/e2e/README.md)、[深层套件说明](../frontend/e2e/round2/README.md)。需要先构建前端、启用正确Python/FFmpeg/Node/Edge，再启动指定隔离主机；不能拿日常8000服务替代TEMP课堂。每轮使用新标签，零重试、独立登录上下文、遮罩截图，保留失败，不保存原始认证响应、Cookie/PIN、trace/HAR/storageState。

该核心重构轮8766/8769/8770均已停止，源文件复核未改变259个允许读取文件；数据和失败档案保留。旧完整live原片仍有4项QC阻断，**当时没有新增付费模型调用，不得自动再跑完整live**。后续唯一Kimi文字请求已在上文单独记录。短片导出成功不等于新闻事实、人物授权或原完整影片QC已经通过。

历史轮次目录保持原样，包括本次中间失败：公开限制字段断言、错误的测试入口名称、专业能力中文化后的旧英文断言。它们不覆盖最终517/32/30结果，也不抹除曾出现的问题。