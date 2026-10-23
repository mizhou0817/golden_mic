---
name: golden-mic-verify
description: 用于离线验证金话筒的改动：依赖校验（pip check）、预检（backend.preflight，必须 ready true）、静态检查（typecheck、AST）、受保护的后端回归、前端回归和构建，外部服务全部模拟，并按固定格式如实汇报。改完 backend/、frontend/、tests/、scripts/ 或 mode_rules.json 后，以及说"完成""修好了""测试通过"、提交之前必须使用；用户说"验证""跑测试""回归""类型检查""能不能提交"时使用。不要用于部署、真实素材制作、真实 AI 或语音质量验收、完整 V2 浏览器验收。关键词：验证 verify、预检 preflight、pip check、回归 regression、typecheck、build、run_v2_validation。
argument-hint: "[静态|后端|前端|构建|全部]"
---

# 金话筒离线验证

用仓库规定的受保护入口验证改动。每一项都写清命令、退出码和通过数。没跑的项写"未运行"和原因，不能写成通过。拿不出本次运行的输出，就不能说"完成"。

## 何时使用 / 不要使用

- 使用：
  - 改了 `backend/`、`frontend/`、`tests/`、`scripts/`、`backend/mode_rules.json` 之后；
  - 声称完成、修复或测试通过之前，提交之前；
  - 用户要求验证、跑测试、回归。
- 不要使用：
  - 部署和生产预检：见 [docs/RUNBOOK.md](../../../docs/RUNBOOK.md)、[deploy/README.md](../../../deploy/README.md)。
  - 真实服务调用、真实素材制作、语音或 AI 质量验收。
  - 完整 V2 浏览器验收：由维护者按 [frontend/e2e/v2/README.md](../../../frontend/e2e/v2/README.md) 执行。

## 项目约束（附出处）

- 后端回归只用受保护入口 `tests/run_v2_validation.py`。它在导入应用前屏蔽 dotenv，重定向 TEMP、缓存和任务目录，限制外部连接、子进程和 SQLite 路径。不直接跑 `unittest discover`、`pytest` 或 `run_core_validation` 全量发现，也不先导入真实 `backend.main` 再改环境变量。出处：[docs/DEVELOPMENT.md](../../../docs/DEVELOPMENT.md) 第 5 节。
- 入口只有三个参数：`--static-only`、`--compile-ast`、`--legacy`。每次运行全部 `tests/test_v2_*.py`，没有"只跑某个模块"的开关。出处：`tests/run_v2_validation.py::main`。
- `APP_ENV=test` 本身不禁用 dotenv，也不阻断网络。出处：DEVELOPMENT.md 第 1 节。
- 预检顺序是先 `pip check`，再 `backend.preflight`，退出码为 0 且报告 `ready: true` 才算就绪。出处：[README.md](../../../README.md)"先预检，再启动单进程服务"。
- 预检**不调用外部服务**，只在本机检查：
  - 前端构建 `frontend/dist`、字体散列、磁盘余量；
  - FFmpeg 能力：`ffmpeg -encoders`、`ffmpeg -filters`、`ffprobe -version`，各 15 秒超时；
  - 服务商配置：只检查必需字段是否存在、URL 前缀是否正确。

  它会加载本机配置，在数据目录写入并立即删除一个 `.readiness-<uuid>.tmp` 探针文件，目录不存在时会创建。输出里的错误已脱敏，缺配置时只列键名。出处：`backend/preflight.py`、`backend/readiness.py::run_full_preflight`、`backend/pipeline.py::_validate_pipeline_configuration`、`backend/providers/*.py::validate_configuration`。
- 后端回归运行期间要冻结源码：不同时修改产品代码、测试、依赖或被绑定的文档，否则会报 `source_drift_requires_rerun`。出处：DEVELOPMENT.md 第 5 节，`tests/run_v2_validation.py`。
- 前端默认测试就会启动真实浏览器（Edge、Chrome 或 WebKit）并用 FFmpeg 合成媒体。`npm run test:e2e` 指向旧配置，不是 V2 验收。出处：DEVELOPMENT.md 第 6 节。
- `npm.cmd --prefix frontend run build` 会替换 `frontend/dist/`。别人的服务正在用这个目录时不要构建。出处：DEVELOPMENT.md 第 7 节。

## 步骤

1. **确认范围**：运行 `git status --short` 和 `git diff --stat`，判断改动涉及前端、后端还是两者。
2. **依赖校验**：运行命令 1。退出码不为 0 就停下并报告，不要自己装包或改依赖。
3. **预检**：运行命令 2。退出码不为 0、或报告不是 `ready: true` 时：
   - 停下，原样报告 `checks` 里为 false 的项和 `errors`；
   - 不填假 Key，不关校验，不改配置；
   - 问用户是否继续。静态检查和受保护回归会屏蔽 dotenv、不需要凭证，用户同意后可以照常运行，但汇报里必须写明"预检未就绪"，也不能得出"可以制作"之类的结论。
4. **静态检查**：总是先做，不启动服务。命令 A 的五条分别记录。
5. **后端受保护回归**：改了 `backend/`、`tests/` 或 `mode_rules.json` 时运行命令 B。耗时数分钟，运行前告诉用户。
6. **前端回归**：改了 `frontend/`、`scripts/` 或 `mode_rules.json` 时运行命令 C。它会启动真实浏览器，**先征得用户同意**。
7. **构建**：需要交付时运行命令 D。先确认 8000 端口的服务没有在用 `frontend/dist/`。
8. **汇报**：按"验证结果的汇报格式"输出。

想单独跑某个测试模块快速看结果时，先征得用户同意，再按下面的顺序选：

- **文件自带受保护运行器的，直接运行那个文件。** 目前有 `tests/test_mode_pipeline.py`（文件头注释写明"显式运行，不要 discovery"）：`& .\.venv\Scripts\python.exe -B tests/test_mode_pipeline.py [类名或 类名.用例名 …]`。它只保留系统运行必需的环境变量（服务商凭证被清掉）、屏蔽 dotenv，禁止访问 `.env`、`data/`、`eval_sample/`、`canary_test/artifacts` 和网络，只放行本机 FFmpeg、ffprobe 子进程，并且要求 WinGet 安装的 Gyan.FFmpeg。`tests/test_mode_exports.py` 的 `main()` 用 `run_core_validation` 的同一套防护，也直接运行它。
- **其余文件没有受保护运行器**（例如 `test_m4_tts`、`test_m5_rendering`、`test_motion_rendering`、`test_asr`；`tests/run_legacy_validation.py` 只盘点，不执行）。这条路**没有受保护入口的闸门**：不屏蔽 dotenv，不拦外部连接，也不限制子进程；`-E` 只忽略 `PYTHON*` 变量，进程环境里的服务商 Key 和网络都还在。所以只跑已经确认全部外部调用都被模拟的模块（先读测试里的 `patch`、`MockTransport`），在新开的 PowerShell 里先清掉名字含 `KEY`、`TOKEN`、`SECRET`、`APP_ID` 的环境变量，并且必须在**不含 `.env` 和 `data/` 的副本**里运行。做法：用 `git ls-files -co --exclude-standard` 列出的文件复制到 `$env:TEMP` 下的新目录，在那里运行 `& <仓库>\.venv\Scripts\python.exe -E -s -B -m unittest tests.<模块名>`（不能用 `-I`：它不把当前目录放进 `sys.path`，会报 "No module named 'tests'"）。每个模块单独开一个进程跑：同一进程里先导入了 `backend.main` 的模块，会让 `test_v2_text_rules` 的导入检查失败。需要真实 FFmpeg 的用例，先把 FFmpeg 所在目录加进 PATH（受保护入口会自己找 WinGet 安装的 Gyan.FFmpeg）。这只是辅助，结论以命令 B 为准。

## 命令（Windows PowerShell，仓库根目录）

### 1 依赖校验
```powershell
& .\.venv\Scripts\python.exe -m pip check
```

### 2 预检（必须 ready: true）
```powershell
& .\.venv\Scripts\python.exe -m backend.preflight
```

### A 静态检查
```powershell
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run typecheck:e2e
node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/e2e/modes/tsconfig.json
node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/e2e/v2/tsconfig.json
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --static-only --compile-ast
```
最后一条会在 `canary_test/artifacts/` 下新建证据目录（被 git 忽略），并且依赖已安装的前端 TypeScript。

### B 后端受保护回归
```powershell
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --compile-ast
```
- 改动涉及八个复用的旧模块时，改用下面这条（替代上一条，不要两条都跑）：
  ```powershell
  & .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --compile-ast --legacy
  ```
  八个模块是：`test_production_modes`、`test_upload_sessions`、`test_mode_workbench`、`test_quality`、`test_publication`、`test_public_media`、`test_anonymous_access`、`test_headline_contract`。
- Windows 原生事件循环用例要求 CPython 3.11.9。CI（Linux）跑的是 `python -I -B tests/run_v2_validation.py --compile-ast --legacy`（见 `.github/workflows/linux-ci.yml`）。

### C 前端回归（先征得同意）
```powershell
npm.cmd --prefix frontend test
node --test --test-concurrency=1 scripts/test-v2-result.mjs
node --test scripts/test-publication.mjs
node scripts/verify-publication.mjs
```
- 后三条在仓库根目录运行，结果和第一条分开报告。
- PATH 里找不到 ffmpeg 时，只设置 `$env:GM_SAMPLE_FFMPEG` 指向已安装的 ffmpeg.exe。
- 三个 opt-in 开关（`GM_DIALOG_BROWSER`、`GM_RECORDING_BROWSER`、`GM_FINAL_BROWSER`）只在专用终端里临时设置，用完关闭该终端。

### D 构建
```powershell
npm.cmd --prefix frontend run build
```

## 红线：验证时不能做的事

- 不调用收费服务，不提交真实生成任务；测试里的 Kimi、火山 Embedding、Seed ASR/TTS 一律用模拟（写法见 golden-mic-providers）。
- 不读取、打印 `.env` 的值，不注入真实凭证。
- 不启动或重启正在服务的实例，不用 `--reload`，不占用 8000 端口。
- 不为了通过而关门禁、放宽阈值、删断言、改额度、删失败证据。失败时保留证据目录。
- 不改依赖和锁文件，不装新包。
- 平台排除项（例如 Linux 不跑的 Windows 原生用例）逐项单独列出，不算进通过数。

## 验证结果的汇报格式

| 检查项 | 命令 | 退出码 | 结果（通过/总数） | 备注或未运行原因 |
|---|---|---|---|---|

表后附上：

- 证据目录路径；
- 预检里为 false 的项；
- 平台排除项；
- 源码有无漂移；
- 仍未验证的范围，例如真实语音、费用、生产环境。

`run_v2_validation.py` 的退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 所选流程完成或回归通过 |
| 1 | 测试、跳过、清理、防护或源码漂移需要处理 |
| 2 | 启动条件、运行器或证据写入失败 |

## 示例

改了 `backend/drafts.py` 的对齐接口后：

| 检查项 | 命令 | 退出码 | 结果 | 备注 |
|---|---|---|---|---|
| 依赖 | `python -m pip check` | 0 | 无冲突 | |
| 预检 | `python -m backend.preflight` | 1 | ready: false | `provider_configuration` 为 false：本机没有配置 TTS 字段。已停下报告，用户同意后继续离线检查 |
| 静态 | `run_v2_validation.py --static-only --compile-ast` | 0 | 完成 | |
| 后端回归 | `run_v2_validation.py --compile-ast` | 0 | 通过数见运行摘要 | 平台排除：无 |
| 前端回归 | — | — | 未运行 | 只改了后端 |

## 已知情况

- 2026-10-21，在本机（Windows 11 10.0.26300）上直接运行 `tests.test_v2_release_integrity` 有 2 项失败，结尾都是 `AssertionError: unexpected native tool`（2026-10-22 已修，见下文）：
  - `test_generated_checks_select_dist_without_changing_readiness_globals`
  - `test_selected_checks_still_reject_dependency_and_sbom_drift`

  原因：这个系统版本下第一次调用 `platform.uname()`（经 `win32_ver()`）会派生 `ver` 子进程，碰上了测试替换的 `subprocess.run`。2026-10-22 起测试模块在导入时先调用一次 `platform.uname()` 填好缓存，在副本里直接运行 66 项全过；受保护入口本来就替换了 `platform._syscmd_ver`，不受影响。
- 2026-10-22 最后一次全面核对的结果：受保护入口 `run_v2_validation.py --compile-ast --legacy` 共 994 项，993 项通过，1 项出错，0 项跳过，342 个受保护文件无漂移（约 18 分钟；上一次是 984 项、983 项通过，约 26 分钟）。出错的是 `tests.test_v2_validation_environment` 的 `test_darwin_homebrew_style_links_resolve_to_paired_real_tools`：`WinError 1314`，本机账号没有创建符号链接的权限；在干净的 `git archive HEAD` 导出里同样失败，所以退出码是 1。同一次运行的设计清单还报告 `view_keys` 声明 311、实际 357（交接文档附录 C 的标题数字，附录不能改）。汇报时把它们标为"已知基线失败"，并写明数字。
- 前端全部测试（`node --test --test-concurrency=1 scripts/test-*.mjs`，约 7 分钟）：1335 项，1332 通过，3 项失败，都出在两个文件里，和改动无关，在 `git archive HEAD` 导出里一模一样：`test-v2-responsive-audit.mjs` 的 create 场景及其父用例（305 像素宽、20 像素字号时页面横向溢出），和 `test-v2-sample-review.mjs` 的 Edge 运行时用例。`tsc --noEmit` 无错误。在副本里跑之前先确认副本的 `frontend/scripts` 不是空的：目录是空的时候 `node --test` 会以 0 项、退出码 0 “通过”，要看测试数量，不能只看退出码。
- `tests/test_mode_pipeline.py`（37 项）和 `tests/test_mode_exports.py`（29 项）不在受保护入口的选择里，各有自己的受保护直跑入口：`./.venv/Scripts/python.exe -B tests/test_mode_pipeline.py [类名或 类.方法名]`、`./.venv/Scripts/python.exe -B tests/test_mode_exports.py [--focused|--main-only|--long-only]`。它们需要真的 FFmpeg，找不到会报错，不会跳过；一次要 6–20 分钟，取决于机器负载。2026-10-22 最后一次：前者 37 项全过（约 7 分钟），后者 29 项全过（约 9 分钟）。其间改正的是过期的测试，不是本机问题：V2 的遮挡时长是 min(2 秒, 被遮盖句子的 40%)，按整帧取整；大字幕每行 14 个字符，连续字母会被换行拆开；传给 `ass=filename=` 的 Windows 盘符冒号要转义。
- `tests/` 里不在受保护入口选择、也没有自己直跑入口的旧测试文件，末尾是裸 `unittest.main()`，没有防护。不要在仓库里直接运行；要跑就在没有 `.env` 和 `data/` 的副本里，并先征得用户同意。2026-10-22 把其余 30 个旧模块在这样的副本里逐个跑过（`test_workspace_access` 和 `test_m6_operations` 单独跑，也过了）：25 个全过；`test_editing_enhancements` 和 `test_prototype_preferences` 里按旧位置（右上角）数亮像素的断言，在 AI 标识挪到右侧边缘（上边距 130）后失败，已按新位置更正，两个模块都全过；`test_deployment`（匿名生产契约期望 429，实得 413）和 `test_mode_api`（20 个文件上限期望 429，实得 201）在原始提交上就失败，是过期的断言；`test_task_operations` 的 MAX_PATH 用例取决于副本的路径长度（257 不大于 260）；`test_deployment` 的另一项在副本里缺 `frontend/dist` 时失败，把 `dist` 拷进副本就过。

## 参考

- [docs/DEVELOPMENT.md](../../../docs/DEVELOPMENT.md) 第 1、4–9 节
- [README.md](../../../README.md)"先预检，再启动单进程服务"
- [backend/preflight.py](../../../backend/preflight.py)、[backend/readiness.py](../../../backend/readiness.py)
- [tests/run_v2_validation.py](../../../tests/run_v2_validation.py)、[frontend/package.json](../../../frontend/package.json)、[.github/workflows/linux-ci.yml](../../../.github/workflows/linux-ci.yml)
- 2026-10-21 按提交 af1e4e7 的代码核对。
