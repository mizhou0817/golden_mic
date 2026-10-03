# 核心工作区本地验收汇总 · 2026-09-27

**结论：移除应用账号/课堂及独立云作业、保留 AI 新闻生成和编辑的范围调整已完成本地集成验证，并已部署到日常 `http://127.0.0.1:8000`。不是公网生产、真实 Provider 或硬件验收。** 核心新闻生成算法没有因去账号而替换或简化；逐句修订、源时序、字幕/音频处理、真实 QC 和强制生成内容披露继续保留。

当前契约：[CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)、[WORKBENCH_API.md](WORKBENCH_API.md)、[STUDIO_API.md](STUDIO_API.md)、[MEDIA_INPUT_API.md](MEDIA_INPUT_API.md)；生产要求见 [deploy/README.md](../deploy/README.md)。本文汇总已有结果，文档收尾只做只读证据/链接核对，**不运行产品代码、测试、构建、服务或付费调用，不修改机器证据或旧审计**。

## 1. 最终结果与证据口径

| 范围 | 最终已验证结果 | 证据 / 边界 |
|---|---|---|
| 后端完整回归 | **766 项：764 通过、2 跳过、0 failure、0 error；435.199335 秒** | [最终机器汇总](../canary_test/artifacts/workspace-validation-20260927-131111-860100/summary.json)、[原始日志](../canary_test/artifacts/workspace-validation-20260927-131111-860100/backend.log)；不是历史 C26 的计数 |
| 前端契约 | **286/286，0 失败；8.5351768 秒** | [最终 TAP](../canary_test/artifacts/workspace-closure-20260927/frontend-final.tap.log)、[空 stderr](../canary_test/artifacts/workspace-closure-20260927/frontend-final.stderr.log)；7 个脚本组，明细见下文 |
| Edge 浏览器 | **14/14，全部 retry=0，globalErrors=0；69.495374 秒** | [最终浏览器汇总](../canary_test/artifacts/workspace-core-final-20260927-212523/summary.json)；合成媒体、真实本地流水线/编码、假模型响应，不是 live-model 质量验收 |
| 布局 | **8 个宽度/字号组合 × 7 个页面状态 = 56 个样本，0 溢出** | 最终浏览器证据中的 8 份 responsive 记录；320/390/768/1440px × 16/20px，不宣称原生 Windows 滚动条或完整 WCAG 覆盖 |
| 类型与构建 | 应用 `typecheck`、当前 `typecheck:e2e`、生产前端构建成功；**1788 modules、4 assets** | 主集成终端已记录成功；当前 [前端清单](../frontend/dist/ASSET_MANIFEST.sha256)与最终浏览器主机绑定一致，文档阶段独立只读复核了四个文件 SHA-256 |
| 部署契约 | **9 项 deployment 测试通过，已计入 766 项** | [部署测试原始结果](../canary_test/artifacts/workspace-validation-20260927-131111-860100/backend.log#L60-L68)，包括精确环境 schema、实例锁、归档拒绝及匿名生产 HTTP 契约；不等于实际发布归档或 Linux 主机验收 |
| 日常本机服务 | 8000 已受控更新，live/ready 均 200，`local_history:true`；恢复 **46 项，41 done / 5 failed** | 主集成只读服务/浏览器观察与[启动日志](../data/service-logs/local-20260927-212245.stderr.log)、[恢复日志](../data/service-logs/local-20260927-212245.stdout.log)；不是生产部署 |

已落盘的后端/前端/浏览器机器记录是计数依据。[最终交付核对](../canary_test/artifacts/workspace-closure-20260927/delivery-check.json)另外记录了 94 个后端/测试/部署文件无漂移、日常 HTTP 实际返回的四个构建文件散列、73 个隔离快照文件独立复核、47 个本机任务状态/数据库文件无变化及临时归档验证。后端汇总的 435.199335 秒是 runner 测量，原始 unittest 文本为 435.198 秒，含准备/收尾总时间为 437.804319 秒，不混用这三种口径。修改文件编辑器诊断清洁，不外推为全仓所有历史文件零诊断，也不声称执行了额外 lint 工具。

## 2. 后端隔离、跳过与保留失败

最终运行使用 **Python 3.11.9、FFmpeg 9.0.2**，独立 TEMP、禁用真实 dotenv、真实数据/历史证据访问防护，以及受控的本地 HTTP/原生媒体进程。汇总记录 **94 个源文件检查、`source_drift:[]`、0 次套件违规操作、`remaining_owned_http_listeners:[]`**。防护自检中故意触发的 `self_check:denied:*` 是拒绝机制证据，不是套件越界或真实外连；本地 socketpair 不等于外网访问。

两项跳过逐项保留，不计为通过：

1. `test_live_canary_replay_corrects_rates_and_loudness_without_mutating_sources`：需显式 opt-in 的历史 Canary 本地音频回放，本轮没有启用。
2. `test_lexical_traversal_and_dangling_component_links_rejected`：Windows 缺少创建 symlink 的权限。其他路径/链接检查的通过不能代替这一跳过项。

当前完整回归包含真实主应用的本机/生产权限负向、非空 capability 回执、旧 `local_only` 数据、显式重试/独立副本、重启不启动付费任务，以及工作台和 Studio 的修订、媒体、QC、并发、取消和清理失败契约。生产 HTTP 契约是在隔离测试环境中执行，不是在公网/TLS 目标机执行。

[早期完整后端运行](../canary_test/artifacts/workspace-validation-20260927-125242-243502/summary.json)仍为失败，不覆盖或改标为绿色。原因是旧测试仍接受缺失的创建 token、仍 mock 已移除的 Studio 别名，以及工作台夹具嵌套路径超过 Windows 约 260 字符限制；这是测试契约/夹具问题，不是通过放宽产品校验修复的产品回归。后续对齐当前契约与夹具后才有本页的最终完整通过记录；中间定向运行不累加到最终数。

## 3. 前端契约与最终构建

最终 TAP 的 **286 项**由以下 7 个脚本组组成（TAP 的 `# suites` 为 0，并非另有 7 个嵌套 suite）：

| 脚本组 | 通过数 |
|---|---:|
| [app API](../frontend/scripts/test-app-api.mjs) | 71 |
| [timeline](../frontend/scripts/test-timeline-editing.mjs) | 17 |
| [preferences](../frontend/scripts/test-workbench-preferences.mjs) | 12 |
| [compositions](../frontend/scripts/test-studio-compositions.mjs) | 25 |
| [assets](../frontend/scripts/test-studio-assets.mjs) | 40 |
| [proxy](../frontend/scripts/test-studio-proxy.mjs) | 70 |
| [sequences](../frontend/scripts/test-studio-sequences.mjs) | 51 |
| **总计** | **286** |

早期全量曾发生代理脚本的瞬时加载失败：**217 项、216 通过、1 个文件级错误**，会话工具 TEMP 原日志保留。随后代理组独立 **70/70**，再完整执行得到当前 **286/286**；没有降低断言或把定向重试相加冒充完整通过。

[appApi.ts](../frontend/src/lib/appApi.ts)统一任务请求和匿名 CSRF 会话，不再是账户会话；[studioApi.ts](../frontend/src/lib/studioApi.ts)移除了 `sessionOnly` 分支。任务凭据来自当前所选回执，拒绝跨任务/任意凭据注入，取消与会话代次有隔离，未知提交结果不自动重放。Studio 的源、图片、代理和**已完成输出**全部由已知同源路径构造，不把 capability 拼到目录返回的任意 URL。最终浏览器运行发生在这次媒体 URL 清理之后。

当前四文件清单（SHA-256 均与磁盘及最终 TEMP 主机的前端绑定相符）：

| 构建文件 | SHA-256 |
|---|---|
| [Studio chunk](../frontend/dist/assets/Studio-BdzWh9ZU.js) | `2b235091e9c4ad82fa85605412b7f468934ef386653a742f277495e6e9e5bcd2` |
| [主 JS](../frontend/dist/assets/index-Bg0iI9-E.js) | `83d7a7519505049a5e8bdb8dce9de12efa640ccca3c7726511e73173a53c4063` |
| [样式](../frontend/dist/assets/index-puTQXyyb.css) | `adea204a306d58cb1505a89f237381707e1849f49682ac9caaf393c8960d5be4` |
| [入口 HTML](../frontend/dist/index.html) | `3e2d04743fe699016b32540cf132b59b106bb01454c84ce28dfd90d7ebce0b05` |

这里的构建/类型成功是已完成集成结果；文档收尾不重新构建，也不改写清单或 C25/C26 的独立构建。

## 4. 浏览器：真实本地执行与合成边界

[当前浏览器用例](../frontend/e2e/workspace/acceptance.workspace.spec.ts)在新建 TEMP 的真实应用上执行。四段输入为实际 FFmpeg 生成的动态图案 MP4；Vision、文本 Embedding、LLM/断句和 TTS 适配器只连接**精确匹配的 loopback 假 Provider**。TTS 是音调，**不是讲话**。十阶段真实执行、真实生成文件、真实 QC/FFmpeg 和浏览器原生解码均保留；未用假进度或伪造通过替代。

| 用例 | 本次实际覆盖 |
|---|---|
| WS-01 | 首次访问直接进入三步创建向导，无登录/课堂 UI，无自行发起的写请求 |
| WS-02 | 文件选择器上传四段真实合成媒体，非空 token 回执、十阶段中间进度与完成、历史刷新、实际 MP4/Range。新片 1920×1080、3.8 秒、114 帧，原生播放至 ended，真实 QC 阻断 0；见[生成证据](../canary_test/artifacts/workspace-core-final-20260927-212523/test-results/acceptance.workspace-WS-02-e6214-ry-reload-and-native-decode-workspace-msedge/generation.json) |
| WS-03 | TEMP 独立副本：无 token 同源改句 → `/remix` 删句 → UI 读取/恢复版本 0 → UI 再删句。依次产生 r1–r4 四次修订，连同 r0 共五个可读版本；不操作用户作品 |
| WS-04 | 真实 Studio 保存 2 秒素材范围，普通修剪至源入点 0.2 秒/输出 1.5 秒，按保存的工程修订渲染 360p，原生解码 640×360、45 帧并下载；QC 门禁未 patch，输出不冒充流水线 QC；见[媒体证据](../canary_test/artifacts/workspace-core-final-20260927-212523/test-results/acceptance.workspace-WS-04-23f6f-d-downloads-validated-media-workspace-msedge/studio-media.json) |
| WS-05 | 本机列表不泄露 token；跨站/转发头、显式坏或空 capability、缺失/外源 Origin 写入被拒绝。模拟远端身份条件，不作真实远端连接；见[本机权限证据](../canary_test/artifacts/workspace-core-final-20260927-212523/test-results/acceptance.workspace-WS-05-ebbb0-ite-and-forwarded-authority-workspace-msedge/local-authority.json) |
| WS-06 | 草稿刷新仅恢复文件元数据，要求重新选择文件；held 冷链接跨越 debounce 前后，原草稿字节不变，0 浏览器存储写入、0 非预期 HTTP 写入 |
| 8 个 layout 用例 | 每组检查向导首步证据展开/折叠、真实文件清单、效果页、历史、结果、Studio，共 56 个样本；可见元素与文档宽度无溢出 |

WS-04 下载 **828293 字节**，SHA-256 `d0b3652fd503ddc3df74a0719c80e3ddbb101dba98b65e89ea2697415b93bb6a`，下载字节与受保护输出响应相符。该精确短片下载验证**不是所有用户媒体的散列验证**。14 份 safety 记录均为 0 页面错误、0 非预期 Provider 调用、0 清理错误。

合成主机明确关闭 **ASR、整篇自主录音、直接视频 Embedding**。这些能力有各自后端单元/契约覆盖，但没有在这次合成浏览器流程中执行，更没有新 live Provider、麦克风、真实发音/音质或新闻匹配质量验收。**新增真实 Provider 请求 0、付费请求 0**；页面/适配器网络约束不是 OS 全局防火墙证明。

### 运行顺序、退出与未解决风险

- [首轮相同 14 项](../canary_test/artifacts/workspace-core-first-20260927-211646/summary.json)也通过，耗时 **78.373824 秒**，但早于最终媒体 URL 清理；当前采用后续完整 **69.495374 秒**结果，不能相加为 28 项。
- 更早合成夹具的 `stored_name` 不符合 UUID 命名约束，修正夹具后才启动成功；失败 TEMP 实例 **golden-mic-workspace-w6vndqpl**保留。这不是放宽产品路径验证，也不是生产故障。
- 最终 TEMP 实例 **golden-mic-workspace-wy57yw_u**通过匹配实例的专用 shutdown 端点结束；主机清单为 `serverState:"stopped"`、`shutdownComplete:true`，并记录 `originalSnapshotUnchanged:true`。退出后又独立重新散列 **73 个原始合成快照文件，全部一致**；原始快照/TEMP 保留，验收进程及 8782–8799 监听均已退出。
- Windows Proactor **10054** 断连回调仍曾出现，但这次用例通过；没有宣称修复该回调。后端日志的 Starlette/httpx 弃用告警也不隐去。
- [C26 原生崩溃](../canary_test/CANARY_C26_20260926.md)的 Python 3.11.9 `c0000005` **HIGH、根因未解决**仍保留；这次绿色结果/正常 TEMP 退出既不证明它已修复，也不补写历史 C26 深层主机正常退出证据。

## 5. 日常 8000 部署与旧数据保护

这是主集成完成的**本机开发工作区部署**，不是 Linux/公网部署。排空并确认 `active/studio/cloud/held` 均为 **0** 后，从旧 worker **41344**更新为 worker **16200**（launcher **18516**）。[本次服务 stderr](../data/service-logs/local-20260927-212245.stderr.log)记录实际 worker 和 startup complete；[stdout](../data/service-logs/local-20260927-212245.stdout.log)记录恢复 46 项。live/ready 返回 200，工作区 `local_history:true`。旧进程内存只知道 45 项，重启重新读取了磁盘 46 项，并非新增一项生成。

重启前后已逐文件比较 **47 个文件：46 个任务状态 + 1 个旧课堂 DB，均无变化**。文档阶段只读复核时，46 个状态全部可读，仍为 **41 done / 5 failed**；[旧数据库](../data/classroom.sqlite3)为 **188416 字节**，SHA-256 **FCF6088544D5DB882BBD5590BB1222F51E017F327F901329D159A89517110AF1**。本页不重新打开 SQLite 连接，不初始化/迁移/清空数据库，不卸载用户环境依赖，不删除视频目录或历史证据。

主集成浏览器只读打开既有任务 **064939d545964a2393c71ff40bab553d**：无需登录，显示 **15 句**、视频元数据 **61.8 秒**，编辑入口可用。没有对用户任务编辑、生成、重剪或重做 QC。额外历史报告基线读取曾受 OneDrive 云提供程序错误阻断，保留原状，未强制 hydrate 或覆盖；任务状态全部可读，**但不能据此声称所有媒体/报告完整散列验证通过**。

旧任务保留 `local_only`，独立副本也继承这个标志，不会因新 ID/token 而公开。启动、刷新和读历史不自动释放旧 held 上传或调用付费模型；旧课堂 DB、视频/任务目录、失败证据和[既有一次性付费回执](../canary_test/artifacts/cloud-live-20260923/report.json)保留。历史费用预约不因删除云入口而被清零或重新授权。

## 6. 依赖、配置与制品边界

- 已移除 **3 个 Tencent 直接依赖和 7 个仅由其引入的传递依赖**；[依赖声明](../pyproject.toml)、[开发依赖](../requirements.txt)、[uv 锁](../uv.lock)、[生产哈希导出](../requirements-production.lock)、[SBOM](../sbom.cdx.json)同步。当前为 **44 个不同外部包名、45 个 SBOM 组件**，没有 Tencent SDK 依赖；核心 Kimi/火山生成适配器保留。
- 保留依赖的版本、来源和制品 SHA 不变；原生 uv 为缓存条目补充 size/upload-time 元数据，不是升级保留包。已通过离线锁检查、`pip check` 和[安装环境验证器](../deploy/verify_python_environment.py)；生产导出/SBOM 曾与独立 TEMP 生成物核对。用户环境 **76 个已安装 distribution 不变，没有卸载操作**。这些是主集成/依赖清理结果，文档阶段不安装、不运行验证器，也不声称新增漏洞扫描。
- [开发模板](../.env.example)与[生产模板](../deploy/golden-mic.env.production.example)均已删除 **32 个退役 `CLOUD_*` 键和 `PUBLIC_ACCESS_MODE`**，当前没有这些赋值；精确 schema 测试通过，不能恢复旧键或放宽校验。实际生产秘密文件未由本次文档工作读取/改写。
- [发布脚本](../deploy/build_release.py)现在显式包括四份当前契约文档：核心工作区、Workbench、Studio、Media Input。**不是递归打包整个 docs**，本验证汇总、历史审计、canary 证据/实际任务数据不在这四份白名单中。临时构建归档已通过[安全归档验证](../deploy/verify_release_archive.py)：**92 文件、20578235 字节**，SHA-256 `72c8f29dcc77d48363985d9787175ab862e9442d1e24398e6f8656e79e91c3f5`，构建同时校验冻结导出/SBOM、字体和前端清单。这是本次 TEMP 打包检查，**未签名、未部署生产**；随后仅更新本验证汇总，不改已验证源码或归档。

## 7. 为什么仍保留 token / CSRF

删除的是**用户账号、课堂身份与审批**，不是作品私密性或防跨站/费用保护：

- 自有本机开发必须直连 loopback、loopback Host、无转发头；免 token 写入仍要求**精确同源 Origin**。显式错误/空/冲突 token 不降级为本机授权。不能将这个工作区暴露到 LAN/隧道/公网。
- 生产始终使用 HTTPS、签名匿名 cookie、`X-CSRF-Token`、严格 Origin/Host 和独立单任务 capability。cookie/CSRF 不是账户身份，也不授予作品权限；生产没有全局任务索引，即使从生产主机 loopback 访问也没有。
- 单任务 token 是持有者 capability，服务器只存哈希；生产浏览器丢失回执后，不能通过“重新登录”找回。Studio 同源媒体路径和不自动重放 POST 防止泄露/重复花费，不构成登录流程。
- 创建、符合条件的失败初版重试、涉及模型的编辑须由用户**主动点击并明确确认费用**，保持 Provider 预算和配额。可选生成式补拍仍默认关闭，需运营配置和用户显式选择；删除 Tencent 独立作业不意味着 AI 新闻生成离线或免费。

## 8. 未执行或仍需独立验收

| 项目 | 当前结论 |
|---|---|
| 临时发布归档、生产签名 | 本次 TEMP 归档独立验证通过；未签名、未发布或部署生产，不把打包检查当作生产批准 |
| Linux CI、ShellCheck、实际 Nginx/systemd、TLS/代理链、公网 CSRF/Origin 与日志 | **未作本轮目标环境验收**；本机和隔离生产 HTTP 契约不能代替 |
| CVM/CBS、安全组、WAF、预算告警、备份恢复、排空/回滚/负载与故障演练 | **未验收**；日常 8000 的空闲重启不等于这些演练 |
| 新 live ASR/整篇自主录音/候选视频 Embedding、模型匹配与新闻质量 | **本轮未实测**；既有单元契约与历史付费结果分开，不能自动补跑 |
| 物理麦克风、音频设备、打印等硬件 | **未验收**；音调/合成素材不代表人声或硬件效果 |
| 完整 WCAG、人工对比度/字幕/键盘审查、原生滚动条 | **未完整验收**；56 个布局样本只证明相应状态不溢出，历史人工待查项保留 |
| Python 原生崩溃、历史深层退出缺口、断电跨文件事务 | **未解决 / 不作保证**；当前绿色和新 TEMP 正常退出不重写 C26 |
| 全部 113 项原型工具、完整 NLE/HDR/4K/自动多机位 | **未实现或不在当前范围**；能力上限仍由当前 API/Studio 契约约束 |

旧 [C26](../canary_test/CANARY_C26_20260926.md)、[C25](../canary_test/CANARY_20260926.md)及[证据索引](../canary_test/README.md)保持其日期、失败与已用费用回执。本次本地完成结论不替换历史证据、不授予新的付费测试权限，也不等于生产批准。