# 开发指南：结构、调试与安全验证

[项目首页](../README.md) · [文档目录](README.md) · [快速开始](QUICKSTART.md) · [配置说明](CONFIGURATION.md) · [运行维护](RUNBOOK.md)

适合已经能启动本地工作区、准备修改代码的贡献者。本文提供当前源码对应的命令，不宣布这些命令已在读者环境执行；历史验证数字仅见[日期交付记录](V2_PRODUCT_DELIVERY_20261003.md)。

## 1. 开发边界

- 使用自己拥有的独立源码/环境，不将真实 dotenv、任务、模型、评估媒体或生产挂载带入测试工作区。
- 不为测试修改实际凭证、清空浏览器存储、删旧数据库、改不可变版本、关闭发布门禁或放宽模型许可。
- 安装依赖可能联网；应用上传/生成可能调用付费服务；受保护测试与实际制作要分开。`APP_ENV=test` 本身**不禁用 dotenv**，也不阻断网络。
- 当前运行架构是单机单 worker；没有 Redis/Celery、账号服务或分布式队列。不要通过多 worker 修补并发问题。
- V2 是当前入口，legacy/Studio 代码用于兼容，不据旧组件重新增加课堂或专业时间线导航。

## 2. 代码地图

| 范围 | 入口与职责 |
|---|---|
| 页面编排 | [App](../frontend/src/App.tsx)挂载[Workspace](../frontend/src/components/Workspace.tsx)，处理创建、深链接、历史、不可用状态 |
| 创建与上传 | [CreateWizard](../frontend/src/components/CreateWizard.tsx)、[appApi](../frontend/src/lib/appApi.ts)、[uploadSessions](../frontend/src/lib/uploadSessions.ts) |
| 处理与结果 | [Processing](../frontend/src/components/Processing.tsx)、[ResultWorkbench](../frontend/src/components/ResultWorkbench.tsx)及[工作台 API 客户端](../frontend/src/lib/workbenchApi.ts) |
| 持久草稿 | [v2Persistence](../frontend/src/lib/v2Persistence.ts)、[pendingEdits](../frontend/src/lib/pendingEdits.ts)；浏览器回执不是服务器作品备份 |
| 共享规则 | [mode_rules.json](../backend/mode_rules.json)、[productionModes](../frontend/src/lib/productionModes.ts)、[production_modes](../backend/production_modes.py)；不应在文档/前端另造阈值 |
| HTTP 与启动 | [main](../backend/main.py)、[config](../backend/config.py)、[preflight](../backend/preflight.py)、[readiness](../backend/readiness.py) |
| V2 生命周期 | [drafts](../backend/drafts.py)、[admission](../backend/admission.py)、[uploads](../backend/uploads.py)、[task_manager](../backend/task_manager.py) |
| 制作流水线 | [mode_pipeline](../backend/mode_pipeline.py)、[pipeline](../backend/pipeline.py)、[providers/](../backend/providers/)；真实媒体、语音和提供方边界应分开测试 |
| 编辑/发布 | [v2_editing](../backend/v2_editing.py)、[workbench](../backend/workbench.py)、[revisions](../backend/revisions.py)、[publication](../backend/publication.py) |
| 媒体与质量 | [rendering](../backend/rendering.py)、[subtitles](../backend/subtitles.py)、[quality](../backend/quality.py)、[public_media](../backend/public_media.py) |
| 发布供应链 | [build_release](../deploy/build_release.py)、[frontend_binding](../deploy/frontend_binding.py)、[verify_release_archive](../deploy/verify_release_archive.py) |

V2 创建链路为：先建草稿获得 capability → 在该任务下分块传文件 → 完成/探测/预转写 → 匹配与确认 → 显式 start → 异步制作 → 统一 apply/版本 → 检查/导出。旧直接创建 JSON/multipart 仍可能存在，不能把旧接口表当成当前浏览器实际调用顺序。接口索引见[文档目录](README.md)。

## 3. 日常编辑与热更新

普通使用只需要 [快速开始](QUICKSTART.md)中的 8000 单服务；开发前端时可以另开 Vite。先启动自己开发副本的后端（一个 worker、无访问日志），再在第二个终端执行：

```powershell
npm.cmd --prefix frontend run dev
```

Linux 对应 `npm --prefix frontend run dev`。访问 <http://127.0.0.1:5173>；[Vite 配置](../frontend/vite.config.ts)将 `/api`、`/health` 代理到 8000。保持相同 Origin 使用，8000 与 5173 的 localStorage 不共享；代理头也可能影响本地历史授权。不要修改 API base 或关闭安全校验解决权限差异。

- `dev` 不替代后端、不生成正式源码绑定。
- `preview` 的 4173 预览已构建前端。锁定的 Vite 8 会默认继承 `server.proxy`，因此这里的 `/api`、`/health` 仍转发到 8000，但它不会启动后端；也不是生产服务器。4173 未列在开发模板默认 Origin 中，且存储与 8000/5173 分离，不把它当正常制作入口或用关闭 Origin 校验解决写入失败。
- 后端模块/配置变化要重启；启动缓存和就绪快照不会随网页刷新更新。
- 不默认使用后端 `--reload`。上传、收费生成、应用修改或导出期间自动重启会中断工作；排空后再手动重启。
- 如果只是 CSS/UI 工作，无凭证时按[只看界面分支](QUICKSTART.md#view-only)操作，不能上传用户媒体探测界面。

## 4. 先做静态检查

以下命令均从完整源码根目录运行，使用已安装的 Node/TypeScript；**不启动服务或提交制作**：

```powershell
npm.cmd --prefix frontend run typecheck
if ($LASTEXITCODE -ne 0) { throw 'Frontend typecheck failed.' }
npm.cmd --prefix frontend run typecheck:e2e
if ($LASTEXITCODE -ne 0) { throw 'Workspace E2E typecheck failed.' }
node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/e2e/modes/tsconfig.json
if ($LASTEXITCODE -ne 0) { throw 'Modes E2E typecheck failed.' }
node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/e2e/v2/tsconfig.json
if ($LASTEXITCODE -ne 0) { throw 'V2 E2E typecheck failed.' }
```

四套配置的检查分别记录，不能泛化为 standalone Node 配置或全部 Python 类型清零。

[V2 验证入口](../tests/run_v2_validation.py)有静态分支：

```powershell
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --help
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --static-only --compile-ast
```

第二条会创建新的脱敏证据目录，读取源码/模板、进行 Python AST 和设计 JS AST 盘点；不执行产品测试。它还依赖已安装的前端 TypeScript 和完整设计交接输入，因此不是仅有生产包也能运行的命令。静态盘点完成不等于设计完全一致或产品验收。

## 5. 当前后端回归：受保护的 Windows 入口

当前运行分支依赖 Windows `LOCALAPPDATA` TEMP 和 WinGet 安装的 Gyan FFmpeg/ffprobe；并非可直接复制到 Linux/macOS 的 CI。缺依赖时拒绝，不应该删媒体断言或跳过测试来让计数变绿。

从新的 Python 进程运行，先冻结源码，不在过程中同时编辑产品、测试、被绑定文档或依赖：

```powershell
# V2 默认选择
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --compile-ast
```

需要连同八个明确复用的历史模块验证时，用下面的**替代命令**，不必为了累计计数把两条都跑一遍：

```powershell
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --compile-ast --legacy
```

`--legacy` 只加入 `test_production_modes`、`test_upload_sessions`、`test_mode_workbench`、`test_quality`、`test_publication`、`test_public_media`、`test_anonymous_access`、`test_headline_contract`。不等于全历史 unittest 发现。

运行器在导入应用之前清理继承配置、屏蔽 dotenv、重定向 TEMP/缓存/任务目录，限制外部连接、日常服务、原生媒体子进程以及 SQLite 路径。真实 FFmpeg 会消耗 CPU/磁盘，测试会生成自己的合成媒体与受控 HTTP/ASGI 请求；这是 Python 层保护，**不是原生 OS 沙箱**。

输出会在 [canary_test/artifacts/](../canary_test/artifacts/)下创建全新带 UTC/UUID 的目录；保留摘要、源码前后散列与失败代码位置，TEMP 通常保留。不要覆盖旧 label，不公开原始异常/凭证，不把原生崩溃后的不完整 claim 当作通过。

| 退出 | 解读 |
|---|---|
| 0 | 所选静态流程完成，或所选回归通过；仍不是 UI/真实语音/生产验收 |
| 1 | 测试、跳过/xfail、清理、防护或源码漂移需处理 |
| 2 | 启动条件、运行器或证据写入失败，需要诊断 |

**不推荐在此项目直接运行无防护 `unittest discover`、导入真实 `backend.main` 后再改环境，或拿 `run_core_validation` 全发现当当前验证入口。** 广泛历史测试的额外进程、SQLite 与生命周期权限仍需要独立审核。

仅盘点历史测试可用：

```powershell
& .\.venv\Scripts\python.exe -I -B tests/run_legacy_validation.py --inventory-only --summary
```

这是 AST 资源盘点，测试执行数为 0；`--execute` 明确拒绝。Linux 锁支持不代表 Linux 安全测试执行器、生产兼容或目标机验收已经完成。

## 6. 前端回归：默认也不全是纯单元

[package.json](../frontend/package.json)的 `test` 串行执行全部前端测试 glob。**即使未打开三个 opt-in，默认测试已包含真实 Edge 和合成媒体用例**，例如样片查看和响应式布局；需要本机 Edge、FFmpeg 与安装好的前端依赖，不是任意纯 Node 环境都能跑。

在隔离的开发/测试终端中，不注入实际提供方秘密。Windows 若 PATH 找不到编码器，只将 `GM_SAMPLE_FFMPEG` 指向已安装的真实 ffmpeg 可执行文件；可先采用[动态 PATH 方法](QUICKSTART.md)再运行：

```powershell
npm.cmd --prefix frontend test
if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed; preserve failure evidence.' }
node --test --test-concurrency=1 scripts/test-v2-result.mjs
if ($LASTEXITCODE -ne 0) { throw 'Independent result tests failed.' }
```

根目录结果工作台测试不在前端 glob 内，分别报告，不把旧数字硬编码为成功标准。`test:e2e` npm 别名指向**旧 workspace 浏览器配置**，不是当前 V2 的核心六项，不作为快捷新手测试。

### 显式增加原生交互检查

`GM_DIALOG_BROWSER=1`、`GM_RECORDING_BROWSER=1`、`GM_FINAL_BROWSER=1` 分别增加原生模态框、合成录音生命周期和实际下载检查。它们不是物理麦克风或真实声音质量验收；下载测试会创建自有 loopback 拒绝代理和临时文件。

确认权限和本地依赖后，在**专用于本次测试**的 PowerShell 终端执行一次完整 opt-in 组合；不用全局持久化环境变量：

```powershell
$env:GM_DIALOG_BROWSER = '1'
$env:GM_RECORDING_BROWSER = '1'
$env:GM_FINAL_BROWSER = '1'
$env:GM_SAMPLE_FFMPEG = (Get-Command ffmpeg.exe -ErrorAction Stop).Source
npm.cmd --prefix frontend test
if ($LASTEXITCODE -ne 0) { throw 'Opt-in frontend tests failed; preserve evidence.' }
```

完成后关闭这个专用终端，避免后续默认测试不知情继承 opt-in。不要通过自动重试原生崩溃、删除下载文件断言或扩大内存/媒体限制来追求全绿。

## 7. 构建：日常输出与隔离输出分开

### 自己工作区的日常构建

```powershell
npm.cmd --prefix frontend run build
```

这个命令会替换标准 [frontend/dist/](../frontend/dist/)（目录存在时），自动做类型检查、调用 Vite 并写入资产清单和源码绑定。不要在别人服务正在使用该目录时重建，也不要先运行裸 Vite 再“补签”摘要。

### 隔离验收用新目录

下面是示例 label，**每次都必须换成未用过的新值**。它保留 `dist-canary-modes-v2-` 前缀以满足 V2 主机约束；使用 PowerShell 且从项目根开始：

```powershell
$gmBuildLeaf = 'dist-canary-modes-v2-dev-unique01'
if (Test-Path -LiteralPath (Join-Path 'frontend' $gmBuildLeaf)) { throw 'Build label already used.' }
Push-Location frontend
try {
    node node_modules/typescript/bin/tsc --noEmit
    if ($LASTEXITCODE -ne 0) { throw 'Typecheck failed.' }
    node scripts/write-manifest.mjs --build --out-dir $gmBuildLeaf
    if ($LASTEXITCODE -ne 0) { throw 'Build failed; retain output and choose a new label next time.' }
} finally { Pop-Location }
```

必须在 frontend 工作目录构建，避免 Tailwind 内容扫描受工作目录影响。构建器不接受任意输出路径、链接或陈旧绑定；不要用历史成功目录覆盖失败结果。

## 8. 完整 V2 浏览器验收由维护者执行

精确程序见[隔离 V2 验收说明](../frontend/e2e/v2/README.md)的 **Parent commands and ownership** 与 **Graceful stop**，不是首页快速启动。该说明顶部有历史日期状态，当前记录由[10 月 3 日交付](V2_PRODUCT_DELIVERY_20261003.md)覆盖；操作协议仍要逐项遵守。

- 准备当前源码绑定的新构建、未用过的绝对 TEMP manifest、独立 run label。
- 主机仅绑定 127.0.0.1:8787，端口占用就拒绝，不能复用/杀掉其持有者，也不访问日常 8000。
- 确认 manifest 真正 `ready` 后才运行 [playwright.v2.config.ts](../frontend/playwright.v2.config.ts)。默认核心六项；`V2_DESIGN_ONLY=1` 的设计四项用新主机/label，分别报告。
- 不覆盖 worker/retry/reporter/trace/video/screenshot 安全设置，不自动读取原始 DOM 或带能力令牌的请求日志。
- 停止前核对实例响应的 header/body；shutdown POST 必须有精确 Origin 与 instanceId。等待确切主机退出和 stopped/稳定性标志，不仅看浏览器测试退出。
- 主机使用模拟 ASR/音调 TTS 与真实媒体处理，不能声称模型质量、真实音频、配额、Linux 或生产全链路验收。

若无法满足隔离条件，停在静态/合约验证并明确记录未运行，不把测试连接到实际用户数据或已启动的日常服务。

## 9. 修改后的提交自检

1. 明确改动属于当前 V2、兼容 API、测试还是文档，先核对实际生产者与调用者，不凭历史说明猜语义。
2. 为行为变化补最小复现与回归，不弱化原鉴权/时序/有限值/版本/资源断言。新增配置必须同步两模板与验证用例。
3. 按影响范围执行上述静态检查和受保护回归，记录精确命令、作用域、退出、跳过/失败与源码漂移。
4. 需要浏览器时使用新绑定、新 label；真实 Provider 调用另需素材、预算和停止条件授权。
5. 同步面向新人的文档和接口参考，校验链接/命令；**不要改写旧日期报告、已生成回执或旧发布包来伪装重新通过**。
6. 不提交实际配置、能力令牌、私有模型/媒体、原始日志或本机绝对用户路径。最小包的内容由[发布白名单](../deploy/build_release.py)决定，新文档并不自动进入旧包。