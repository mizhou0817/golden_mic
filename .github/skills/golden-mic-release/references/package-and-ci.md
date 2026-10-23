# 发布包内容、SBOM 与 CI

2026-10-22 按代码核对。打包范围只以 `deploy/build_release.py` 的白名单为准，文档不能扩大它。

## 发布包里有什么

| 类别 | 内容 |
|---|---|
| 目录（递归） | `backend`、`frontend/dist`（选用的前端构建，包里统一映射成标准 dist）、`vendor`、`deploy` |
| 根目录文件 | `README.md`、`requirements.txt`、`requirements-production.lock`、`sbom.cdx.json`、`pyproject.toml`、`uv.lock` |
| 文档 | `INCLUDED_FILES` 里逐个列出的 `docs/*.md`，加上 `docs/v2-model-selection-20261002.json` |
| 不打包的文档 | `docs/AGENT_SKILLS.md`、`docs/UI_SIMPLIFICATION_20261014.md`、`docs/agents/`、`docs/reports/`、根目录的设计交接文档、`.github/` |
| 排除 | `__pycache__`、`.pytest_cache`、`.ruff_cache`，以及 `.pyc`、`.pyo` |

- **新增文档要随包发布时**，必须把文件名加进 `INCLUDED_FILES`。这要改 `deploy/`，先经批准。
- **链接规则**：随包发布的文档只能链接包里有的文件。`docs/reports/` 下的文件要用 GitHub 地址链接。
- **谁来检查**：
  - `scripts/verify-publication.mjs`：检查 10 篇现行文档的链接，只读；
  - `tests/test_v2_release_integrity.py`：检查打包后的导航。

## 归档验证器 `deploy/verify_release_archive.py`

- **拒绝的东西**：
  - 越界或不规范的路径、重复成员；
  - 链接和设备文件；
  - setuid、setgid；
  - 路径里含 `.env`、`.venv`、`data`、`eval_sample`、`node_modules`、`private-models` 的成员；
  - 范例包 `backend/assets/samples`（范例在主机上单独安装，见 deploy/README.md"范例成片"）。
- **上限**：
  - 归档 1 GiB；
  - 单个成员 1 GiB；
  - 解压后合计 2 GiB；
  - 最多 5000 个成员。
- **必需成员**：`REQUIRED_MEMBERS` 列表里的文件，包括字体（含 `OFL.txt`）、关键文档和前端的 `ASSET_MANIFEST.sha256`。
- **验证通过的含义**：只说明包的结构合格，不代表模型、语音或目标机已经验收，也不等于批准部署。

## SBOM

- **生成**：`deploy/generate_sbom.py` 用 `uv export` 从 `uv.lock` 生成 CycloneDX 1.5，结果可复现：
  - `serialNumber` 用 uuid5 生成；
  - 时间戳取 `SOURCE_DATE_EPOCH`，默认 1785196800，即 2026-07-28。
- **默认输出**：不带 `--output` 时，覆盖仓库根的 `sbom.cdx.json`。
- **只读核对**：照 CI 的写法，输出到临时目录再比较。这会运行 uv，先征得用户同意：

  ```bash
  uv run --frozen --no-sync python -B deploy/generate_sbom.py --output /tmp/sbom.cdx.json
  diff -u sbom.cdx.json /tmp/sbom.cdx.json
  ```

- **逐字节一致的前提**：要用 uv 0.11.11 和相同的 `SOURCE_DATE_EPOCH`（deploy/README.md 第 2 节）。版本范围 `>=0.11.11,<0.12` 并不保证输出相同。
- **审计**：仓库里没有 pip-audit、npm audit 或 SPDX。做依赖漏洞审计要先经用户批准。

## Linux CI（`.github/workflows/linux-ci.yml`）

触发：push 到 `main`、pull request、手动运行。主要步骤：

1. 安装 FFmpeg；对部署脚本跑 `bash -n` 和 shellcheck；用 `systemd-analyze verify` 检查服务单元。
2. 渲染 nginx 配置并运行 `nginx -t`。
3. 跑 `uv sync --frozen --no-dev`。
4. 比较冻结依赖导出和 `requirements-production.lock`，比较 SBOM 和 `sbom.cdx.json`，两项都输出到 `/tmp` 再 `diff`。
5. 确认只装了一个 headless OpenCV。
6. 跑 `npm ci`，构建绑定源码的前端，并做 E2E 类型检查，再核对构建产物。
7. 前端回归和发布检查：`npm --prefix frontend test`、`scripts/test-v2-result.mjs`、`scripts/test-publication.mjs`、`scripts/verify-publication.mjs`。
8. 受保护的后端回归：`python -I -B tests/run_v2_validation.py --compile-ast --legacy`。
9. 在阻断网络的情况下，用 `APP_ENV=production` 检查生产前提，不产生服务商流量。
10. 构建最小发布包并运行 `verify_release_archive.py`，再用**临时生成的 CI 密钥**签名和验签。这只是测试流程，不是生产签名。
11. 用 `git diff --exit-code` 确认 CI 没有改动已提交的文件。

仓库里没有发布用的 workflow，生产签名和部署都是手工完成的。
