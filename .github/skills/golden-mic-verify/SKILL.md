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
8. **汇报**：按"汇报格式"输出。

想单独跑某个测试模块快速看结果时，先征得用户同意，并且必须在**不含 `.env` 和 `data/` 的副本**里运行。做法：用 `git ls-files -co --exclude-standard` 列出的文件复制到 `$env:TEMP` 下的新目录，在那里运行 `& <仓库>\.venv\Scripts\python.exe -I -B -m unittest tests.<模块名>`。这只是辅助，结论以命令 B 为准。

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

## 汇报格式

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

- 2026-10-21，在本机（Windows 11 10.0.26300）上，`tests.test_v2_release_integrity` 有 2 项失败，结尾都是 `AssertionError: unexpected native tool`：
  - `test_generated_checks_select_dist_without_changing_readiness_globals`
  - `test_selected_checks_still_reject_dependency_and_sbom_drift`

  原因：这个系统版本下 `platform.win32_ver()` 会派生 `ver` 子进程，碰上了测试的 mock。在干净的 `git archive HEAD` 导出里也能复现，和改动无关。汇报时标为"已知基线失败"，不要当成新回归，也不要为此改测试。

## 来源

- [docs/DEVELOPMENT.md](../../../docs/DEVELOPMENT.md) 第 1、4–9 节
- [README.md](../../../README.md)"先预检，再启动单进程服务"
- [backend/preflight.py](../../../backend/preflight.py)、[backend/readiness.py](../../../backend/readiness.py)
- [tests/run_v2_validation.py](../../../tests/run_v2_validation.py)、[frontend/package.json](../../../frontend/package.json)、[.github/workflows/linux-ci.yml](../../../.github/workflows/linux-ci.yml)
- 2026-10-21 按提交 af1e4e7 的代码核对。
