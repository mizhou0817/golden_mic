# 三模式隔离浏览器验收（仅新文件）

## 边界

- 只连接 **http://127.0.0.1:8786**。端口已占用直接失败，不杀其他进程，不访问日常 8000 服务。
- Python 新进程清空继承配置、禁 dotenv、显式 `Settings(_env_file=None)`；任务、上传、缓存、原始合成素材、manifest、浏览器证据均在全新系统 TEMP。不读取、复制或 seed 工作区真实数据、评测素材、历史证据、账户库。
- 仅注入上传的 `_transcribe(record)`，按原始文件名 **及真实上传 SHA-256** 选 fixture。真实 `_editorial_transcript`、PCM 解码、缩略图、波形、chunk 校验、阶段 6 匹配、模式管线、字幕、QC、修订、发布门禁、导出均不打桩。其他提供商使用已有受控 loopback HTTP 适配器。所有外部 HTTP、WebSocket 被拒绝。
- 3 段采访视频：35 / 58 / 40 秒，320×180、30fps、测试图案＋不同频率正弦波；4 段无音轨空镜各 3 秒。**音频不是人声；转写、说话人、平均分布词时间是明确标注的合成测试数据。** 不宣称识别准确率、词对齐声学准确率、人物身份、14dB 杂音证据或新闻事实核验。
- 完整 C 为原始示例 8 句；给定转写总区间为 57 秒，实际成片由真实词区间、头尾余量与间隔决定，**不强行裁成 55 秒**。完整 B 严格为 **11 旁白＋5 原声**，只跑真实上传／匹配预览；渲染 B 使用短稿，避免重复重渲染 16 行。
- 一 worker、5 pending、20 文件、TTL 72 小时、最小空闲磁盘 0、warn QC、生成式补画面禁用。warn 不代表发布放行；真实硬错误不可勾掉。
- 每例新浏览器上下文、仅新建任务；不修改其他例或任何旧任务。新任务及其修订留在 owned TEMP 供独立核对；例末删除本例上传会话，不删除原始输入文件。C 修改前后独立哈希 `r0` 全部文件。
- 0 自动测试重试，首失败停止套件。关闭 trace/video/screenshot/自动 DOM 错误上下文，仅写非秘密投影。[配置](../../playwright.modes.config.ts)要求 `modesNoRawArtifacts=true`；[支持层](support.ts)覆盖 Playwright 1.63 内部 `_setupArtifacts` 自动产物 fixture（保留其 all-hooks-included 注册），阻止错误上下文文件的生成，而不是生成后删除。`PLAYWRIGHT_NO_COPY_PROMPT` 单独只阻止页面 snapshot，不能阻止错误消息/源码上下文文件。升级 Playwright 必须重新核对该内部 fixture。主配置在清输出目录**之前**独占 claim；replacement worker 可读取本轮 claim，不可复用旧轮 label。独立请求 guard、安全 teardown、修订哈希和脱敏 reporter 不变。
- 这是 Python／提供商／页面请求边界，不是原生 FFmpeg 或 Edge 的 OS 沙箱。未运行前不能称浏览器验收通过；通过也不等于真实 ASR、可懂语音、生产或完整 WCAG 验收。

## 8 个用例

| ID | 覆盖 |
|---|---|
| 01 | 三卡、空稿 C 进上传、B 11＋5、切模式不改稿、类型 chip 不重分句 |
| 02 | 真文件上传／预处理／词转换，从转写选句、保存刷新，0 重复 create/chunk/complete POST 或 ASR |
| 03 | 完整 C 7 文件→8 原声→10 阶段→真实 1080p；原话不可改字／配音；实际连续词 trim→新修订、r0 不变；未确认直接 export 409→UI 逐项核对→真实 MP4 下载＋独立 ffprobe |
| 04 | 3 采访真实上传，完整 B 数字与中文日期相等匹配、保留源词时间，不重渲染 |
| 05 | 短 A 浏览器全链路，真实管线＋tone TTS，确认词句不被模型重新拆分 |
| 06 | 短 B 浏览器全链路，旁白／原声音源分离及不同编辑面板 |
| 07 | 真 Wizard 上传＋真 API 强制提交缺失原话→阶段 6 `quote_missing`，不进 TTS；真实 reload 后显式“返回修改原话与素材”，确认替换另一份未提交草稿，恢复原稿／原模式／原 upload IDs 与 bindings；真实上传 GET 和匹配 preview POST 使用原 capability，0 新 task/upload/chunk/complete 写入、0 重复 ASR；快照准备边界见下文 |
| 08 | 同例新短 C，320／900 宽、20px 大字：三卡、稿件、展开/折叠 details、上传转写／说话人、效果、真实处理中、结果／波形、待修改、导出弹窗、历史、抽屉；不模拟 processing HTTP |

生成最多等 900 秒；上传、修订、导出与响应等待分别 180 秒。没有超时后自动重新提交。

### 本轮验收改动与未验证范围（2026-09-28）

- MODES-08 的历史 heading 已按 [Workspace 实际标题](../../src/components/Workspace.tsx#L1009)改为精确匹配“作品历史”，未放宽 320/900px、20px、document overflow 或可见元素边界断言；抽屉补充同样的 20px 检查。
- MODES-07 仍只创建原有的一个 stage-6 失败任务，不新增整片渲染。由于缺失原话本来不能经过 Wizard 的提交门禁，API 强制提交后，用真实 Wizard 保存的草稿及真实上传凭据，在浏览器内准备与 [submissionRecovery](../../src/lib/submissionRecovery.ts)同 schema/TTL、绑定该任务的**显式快照 fixture**。不假造上传回执，不打桩 HTTP，不放开门禁。随后通过 UI 改成另一份旁白草稿，真实刷新失败任务，明确点击返回并确认替换；验证实际产品读取、校验、恢复路径。**这不是 Workspace 接收提交回执后自动保存快照路径的端到端证明**；证据明确记录 `productionSaveHookCovered=false`。凭据比较只输出布尔值，不写 token、原始 draft 或 DOM。
- MODES-07/08 每次测得 layout 后、执行断言前，立即排他保存独立样本 JSON；后续失败不会丢失前面几何。越界项仅含元素序号与数值矩形，不含文本、属性、URL 或 token。样本的 `assertionsPending=true` 表示“已测量，不代表通过”；仅全部成功后才有最终聚合证据。原失败轮证据不覆盖、不删除。
- 本轮只运行 acceptance TypeScript 静态检查；未启动浏览器、host、build，也未重跑用例。父代理报告的 hostJ 旧构建 01–07 passed 不覆盖新增恢复断言，08 旧标题失败不能记为通过。须由父代理将最终产品源码构建到新独立输出并启动新 host 后再验收，不使用 hostJ 旧 build。

## 主代理运行（Windows PowerShell 5.1）

先等主代理 API / Wizard / Workspace / QuoteEditor 的最终变更落盘。不要运行现有默认 build，它会覆盖共享构建。下列独立 build 不加载既有 Vite 配置或 dotenv，使用相同 React plugin；只从前端源码构建，全新自定义输出，`publicDir=false`，不复制原型/演示媒体。若产品依赖 public 资源需由主代理单独复核，不能偷偷借用旧构建。

### 1. 静态检查及全新独立 build（本代理未执行 build）

```powershell
Set-Location 'C:\Users\zhoumi\OneDrive - Microsoft\Documents\golden-mic'
$ErrorActionPreference = 'Stop'
$gmModesPython = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
$gmModesNode = 'C:\Program Files\nodejs\node.exe'
& $gmModesPython -B -X faulthandler -m tests.mode_acceptance_server --check-only
if ($LASTEXITCODE -ne 0) { throw 'Isolation/fixture check failed; do not start' }
& $gmModesNode frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/e2e/modes/tsconfig.json
if ($LASTEXITCODE -ne 0) { throw 'New modes suite does not typecheck' }
$gmModesLabel = 'local-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff')
& $gmModesNode frontend/e2e/modes/build.mjs --label $gmModesLabel
if ($LASTEXITCODE -ne 0) { throw 'Custom build failed; retain it, use a new label after investigating' }
$gmModesDist = Join-Path (Get-Location).Path ('frontend\dist-canary-modes-' + $gmModesLabel)
$gmModesManifest = Join-Path $env:LOCALAPPDATA ('Temp\golden-mic-modes-' + $gmModesLabel + '.json')
if (Test-Path -LiteralPath $gmModesManifest) { throw 'Manifest already exists' }
Write-Output ('FRONTEND=' + $gmModesDist)
Write-Output ('MANIFEST=' + $gmModesManifest)
```

`--check-only` 无服务监听、无渲染、无浏览器／提供商请求；只在隔离 TEMP 验证结构、真实解析/转写转换/文本匹配函数和 FFmpeg/ffprobe 版本查询。它不认证最终业务路由、UI 门禁或媒体结果。

### 2. 父代理显式启动 host（单独终端；长期进程由父代理持有）

在保留上述变量的终端执行；若新终端则填入上一步打印的两个**绝对路径**。

```powershell
& $gmModesPython -B -X faulthandler -m tests.mode_acceptance_server --manifest $gmModesManifest --frontend-dir $gmModesDist
```

代理应将此命令作为异步 host 运行；手工用户可留此终端运行。**不要使用日常启动任务，不要另开 8786 Uvicorn，不要 force kill 占用者。** host 输出 `MODES_MANIFEST`；只有 manifest `serverState=ready` 且 `/health/ready` 200 后才能开始浏览器。启动失败的 manifest 与 TEMP 必须保留，不要复用；启动 API 合约未完成会写 `missingProductRoutes` 并失败，不会增加假业务路由。

### 3. 另一终端运行浏览器

```powershell
Set-Location 'C:\Users\zhoumi\OneDrive - Microsoft\Documents\golden-mic'
$ErrorActionPreference = 'Stop'
$env:PATH = 'C:\Program Files\nodejs;' + $env:PATH
$env:MODES_MANIFEST = '填入上面打印的完整绝对 manifest 路径'
$env:MODES_RUN_LABEL = 'run-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff')
$env:MODES_BASE_URL = 'http://127.0.0.1:8786'
Push-Location frontend
try {
    & '.\node_modules\.bin\playwright.cmd' test --config playwright.modes.config.ts
    if ($LASTEXITCODE -ne 0) { throw 'Modes acceptance failed; no automatic rerun' }
} finally { Pop-Location }
```

调用已安装的 Playwright，不允许 npx 自动下载缺失依赖。等价的人工命令为 `npx.cmd --no-install playwright test --config playwright.modes.config.ts`（工作目录为 frontend）。独立 ffprobe 使用 host manifest 的绝对可执行文件路径，不依赖测试终端 FFmpeg PATH。

证据路径由 manifest `root` 定位：`browser-runs/<label>/binding.json`、`summary.json`、逐例 `test-results/**/safety.json` 及媒体／布局投影。这些是运行时 TEMP 文件，不是仓库旧验收证据。`--list` 也会消费 label，必须另用新 label。首次失败即停止；不是“重复跑到绿”。

### 4. 仅关闭自己持有的 host

只在父代理确认不再需要 host 且当前实例匹配后，使用相同 manifest：

```powershell
$gmModesOwned = Get-Content -LiteralPath $env:MODES_MANIFEST -Raw -Encoding UTF8 | ConvertFrom-Json
if ($gmModesOwned.kind -ne 'golden-mic-mode-acceptance' -or $gmModesOwned.baseURL -ne 'http://127.0.0.1:8786' -or -not $gmModesOwned.synthetic) { throw 'Wrong host manifest' }
$gmModesLive = Invoke-RestMethod -Uri 'http://127.0.0.1:8786/api/test/modes' -Method Get -MaximumRedirection 0
if ($gmModesLive.instanceId -ne $gmModesOwned.instanceId) { throw 'Port no longer belongs to this run' }
Invoke-RestMethod -Uri 'http://127.0.0.1:8786/api/test/modes/shutdown' -Method Post -MaximumRedirection 0 -ContentType 'application/json' -Headers @{ Origin = 'http://127.0.0.1:8786' } -Body (@{ instanceId = $gmModesOwned.instanceId } | ConvertTo-Json -Compress)
```

响应 `stopping` **不是**关闭完成。等父代理持有的 host 进程实际退出后，再单次读 manifest 验证 `serverState=stopped`、`shutdownComplete=true`、`lifespanShutdownComplete=true`、`fakeProviderStopped=true`、`inputHashesUnchanged=true`。原生 crash / 强制停止不会自动被改写成 orderly shutdown。失败和成功 TEMP 均保留，不递归清理。

## 已发现的对接风险（2026-09-28，不是浏览器运行结论）

- 初次检查时真实 main 尚未挂载 upload/checks/quotes/waves/export 等最终适配路由，Workspace 的草稿恢复也正在由父代理重构。不要把路由缺失伪装为测试通过。
- 合成 fixture dry alignment 复现：C 第 6 句“就是希望大家过年都能吃上一口家乡味”，返回 `score=1` 但 `asr_text` 为“希望大家过年都能吃上一口家乡味”。当前前端连续原话校验不会忽略“就是”，因此可能阻断第 3 步。完整 C 用例保留原稿和门禁；不得删词、手改 source 或放宽断言让它过去。父代理需协调真实匹配/证据合约后复验。
- 源码/构建在 host 导入之后变化，global setup 会失败；请待并行重构落盘后重建新独立输出并启动新 host。不要热换当前轮证据或 manifest。
- 本实现阶段只执行了语法、独立 TS、隔离 fixture dry check；**未启动 host、未执行浏览器、未生成或导出成片**。