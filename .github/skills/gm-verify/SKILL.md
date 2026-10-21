---
name: gm-verify
description: 金话筒验证流程（verify / typecheck / regression / build）。按改动范围运行静态检查、受保护的后端回归、前端回归和构建，如实报告通过数和未运行项。改完 backend/、frontend/、tests/、scripts/ 或 mode_rules.json 后使用；用户说"验证""跑测试""回归""检查一下""类型检查""能不能提交"时使用。不用于生产部署、预检、真实素材制作、真实 AI 质量验收或完整 V2 浏览器验收。
argument-hint: "[静态|后端|前端|构建|全部]"
---

# 金话筒验证流程

## 目的
用仓库规定的受保护入口验证改动。每一项都写清命令、退出码和通过数；没跑的项写"未运行"和原因，不能写成通过。

## 适用 / 不适用
- 适用：改了 `backend/`、`frontend/`、`tests/`、`scripts/`、`backend/mode_rules.json` 之后；提交前自检；用户要求验证、跑测试、回归。
- 不适用：
  - 生产部署和预检，见 [RUNBOOK](../../../docs/RUNBOOK.md)、[deploy/README](../../../deploy/README.md)。
  - 真实提供方调用、真实素材制作、真实语音或 AI 质量验收。
  - 完整 V2 浏览器验收，由维护者按 [frontend/e2e/v2/README.md](../../../frontend/e2e/v2/README.md) 执行。

## 硬性规则
- 只在专用的开发或测试终端运行。不注入、读取或打印真实凭证，不调用付费服务。
- 后端只用 `tests/run_v2_validation.py`。不要直接跑 `unittest discover`、`pytest` 或 `run_core_validation` 全量发现，也不要先导入真实 `backend.main` 再改环境变量。
- `npm run test:e2e` 指向旧的 workspace 浏览器配置，不能当作 V2 验收。
- 运行期间冻结源码：不同时修改产品代码、测试、依赖或被绑定的文档。
- 不能为了通过而关门禁、放宽阈值、删断言、改额度或删失败证据。失败时保留证据目录。
- `python -m backend.preflight` 会加载本机真实配置并探测数据，不属于默认验证，必须用户明确同意才能运行。
- 平台排除项（如 Linux 不跑的 Windows 原生用例）要逐项单独列出，不能算进通过数。

## 步骤
1. 确认范围：`git status`、`git diff --stat`，判断改动涉及前端、后端还是两者。
2. 静态检查（总是先做，不启动服务）：运行命令 A 的五条，每条分别记录。
3. 后端回归（改了 `backend/`、`tests/` 或 `mode_rules.json`）：运行命令 B，耗时数分钟，运行前告诉用户。
4. 前端回归（改了 `frontend/`、`scripts/` 或 `mode_rules.json`）：运行命令 C。它会启动真实浏览器（默认 Edge）和 FFmpeg 合成媒体，耗时数分钟，**先征得用户同意**。
5. 构建（需要交付时）：运行命令 D。它会替换 `frontend/dist/`，先确认本机 8000 端口的服务没有在使用这个目录。
6. 完整 V2 浏览器验收不在本流程内。隔离条件不满足时停在第 2–4 步，并记录"未运行"。

## 命令（Windows PowerShell，仓库根目录）

### A 静态检查
```powershell
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run typecheck:e2e
node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/e2e/modes/tsconfig.json
node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/e2e/v2/tsconfig.json
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --static-only --compile-ast
```
最后一条会在 `canary_test/artifacts/` 下新建证据目录（已被 git 忽略），并且依赖已安装的前端 TypeScript。

### B 后端受保护回归
```powershell
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --compile-ast
```
- 涉及八个复用的旧模块（`test_production_modes`、`test_upload_sessions`、`test_mode_workbench`、`test_quality`、`test_publication`、`test_public_media`、`test_anonymous_access`、`test_headline_contract`）时，改用下面这条替代命令，不要两条都跑：
  ```powershell
  & .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --compile-ast --legacy
  ```
- Windows 原生事件循环用例要求 CPython 3.11.9。
- CI（Linux）跑的是 `python -I -B tests/run_v2_validation.py --compile-ast --legacy`。

### C 前端回归
```powershell
npm.cmd --prefix frontend test
node --test --test-concurrency=1 scripts/test-v2-result.mjs
node --test scripts/test-publication.mjs
node scripts/verify-publication.mjs
```
- 后三条在仓库根目录运行，结果和第一条分开报告。
- 如果 PATH 里找不到 ffmpeg，只设置 `$env:GM_SAMPLE_FFMPEG` 指向已安装的真实 ffmpeg.exe。
- 三个 opt-in 开关（`GM_DIALOG_BROWSER`、`GM_RECORDING_BROWSER`、`GM_FINAL_BROWSER`）只在专用终端里临时设置，用完关闭该终端。

### D 构建
```powershell
npm.cmd --prefix frontend run build
```

## 输出格式

| 检查项 | 命令 | 退出码 | 结果（通过/总数） | 备注或未运行原因 |
|---|---|---|---|---|

表后附上：证据目录路径、平台排除项、源码有无漂移，以及仍未验证的范围（例如真实语音、费用、生产环境）。

`run_v2_validation.py` 的退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 所选流程完成或回归通过 |
| 1 | 测试、跳过、清理、防护或源码漂移需要处理 |
| 2 | 启动条件、运行器或证据写入失败 |

## 来源
- [docs/DEVELOPMENT.md](../../../docs/DEVELOPMENT.md) 第 4–9 节（静态检查、后端回归、前端回归、构建、V2 验收、提交自检）
- [frontend/package.json](../../../frontend/package.json) 的 scripts
- [tests/run_v2_validation.py](../../../tests/run_v2_validation.py)：参数只有 `--static-only`、`--compile-ast`、`--legacy`
- [.github/workflows/linux-ci.yml](../../../.github/workflows/linux-ci.yml)：CI 实际运行的命令
