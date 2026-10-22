# Agent Skills 清单

更新于 2026-10-21（分支 `chore/agent-skills`）。本文记录本仓库给 VS Code + GitHub Copilot 用的 Agent Skills，包括：

- 装了哪些，来自哪里，固定在哪个提交；
- 审查结论，以及本地做过的改动；
- 怎样更新和卸载；
- 怎样试用。

选型的完整过程和被拒绝的候选，见 [AGENT_SKILLS_SELECTION_20261021.md](reports/tooling/AGENT_SKILLS_SELECTION_20261021.md)。

## 1. 总览

| 类别 | 位置 | 数量 | 维护方式 |
|---|---|---|---|
| 第三方 skill | `.agents/skills/<名称>/` | 18 个，2026-10-21 全部用 gh 安装 | 用 `gh skill install --pin <提交>` 安装。除第 3 节列出的改动外，不改上游原文 |
| 项目自建 skill | `.github/skills/` | 10 个：5 个 `golden-mic-*`，5 个 `gm-*` | 本仓库维护，事实来自仓库文件，并注明出处 |
| 常驻规则 | `.github/copilot-instructions.md` | 1 个文件，约 50 行 | 每次对话自动加载 |
| Matt Pocock skill 的项目配置 | `docs/agents/issue-tracker.md`、`docs/agents/domain.md` | 2 个文件 | 本地 Markdown tracker（`.scratch/`，已被 git 忽略）和领域文档的约定 |
| 校验脚本 | `scripts/validate_agent_skills.py` | — | 只用标准库，不联网 |

- **规则优先级**：用户的明确指示 > `.github/copilot-instructions.md` > 项目 skill（`golden-mic-*`、`gm-*`）> 第三方 skill。第三方 skill 和项目规则冲突时，以常驻规则里"第三方 skill 的项目覆盖规则"一节为准。
- **校验**：`& .\.venv\Scripts\python.exe -I -B scripts/validate_agent_skills.py`，加 `--list` 可以列出每个 skill。
  - 检查 frontmatter、name 是否和目录一致、description 长度、相对链接、隐藏字符。
  - **`allowed-tools` 里预授权 shell、bash 或终端时报错**，对应红线 6。
- **规范**：[Agent Skills 规范](https://agentskills.io/specification)、[VS Code Agent Skills 文档](https://code.visualstudio.com/docs/copilot/customization/agent-skills)、[GitHub Copilot 文档](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-skills)，查看日期 2026-10-21。

## 2. 第三方 skills

都装在 `.agents/skills/`。这是 gh 用 `--agent github-copilot --scope project` 时的默认目录，VS Code 会读取。审查方法见第 5 节。

### 2.1 第一批（3 个）

| 名称 | 来源（仓库 · 路径） | 固定提交 | 许可证 | 安装方式 | 安装日期 | 审查结论 | 调用方式 |
|---|---|---|---|---|---|---|---|
| accessibility | [addyosmani/web-quality-skills](https://github.com/addyosmani/web-quality-skills) · `skills/accessibility` | `afa8da942115f2961fdbfa80807ea0b232ff6c00` | MIT | gh skill install --pin | 2026-10-21 | 通过。它建议的 `npm i -g @axe-core/cli`、`npx lighthouse` 要先经用户批准 | 自动加载 |
| break-ui | [emilkowalski/skills](https://github.com/emilkowalski/skills) · `skills/break-ui` | `2d228630ba1446365f614a108cb8252f1feda202` | MIT | gh skill install --pin | 2026-10-21 | 通过。测试开关只能留在开发环境 | 自动加载 |
| playwright-cli | [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) · `skills/playwright-cli` | `b1a5903c5b633b8c9f843ad7b629c2a78437040c` | Apache-2.0 | gh skill install --pin，之后有本地改动（第 3 节） | 2026-10-21 | 原版 `allowed-tools` 预授权了 Bash，违反红线 6，已删除这一行。浏览器自检只开本地开发页 | 自动加载 |

### 2.2 第二批（15 个，2026-10-21 用户确认后安装）

都用 `gh skill install --pin` 安装，安装日期 2026-10-21。装好后逐个文件对照固定提交核对过：除第 3 节列出的改动外，完全一致。

| 名称 | 来源（仓库 · 路径） | 固定提交 | 许可证 | 审查结论（2026-10-21） | 调用方式 |
|---|---|---|---|---|---|
| fastapi | [fastapi/fastapi](https://github.com/fastapi/fastapi) · `fastapi/.agents/skills/fastapi` | `866b7a3d0ce1025a3811f23aea4846d01a2b16a8`（标签 0.139.2，和 `uv.lock` 一致） | MIT | 通过。和本地 `.venv` 自带的副本逐字节一致。4 处冲突由常驻规则覆盖：`fastapi dev/run`、`[tool.fastapi]`、`uv add asyncer`、前端托管方式 | 自动加载 |
| grill-with-docs | [mattpocock/skills](https://github.com/mattpocock/skills) · `skills/engineering/grill-with-docs` | `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | MIT | 通过 | 只能手动：`/grill-with-docs` |
| grilling | 同上 · `skills/productivity/grilling` | 同上 | MIT | 通过 | 自动加载，或被 grill-with-docs 调用 |
| domain-modeling | 同上 · `skills/engineering/domain-modeling` | 同上 | MIT | 通过。写 `GLOSSARY.md`、`docs/adr/` 前先问用户 | 自动加载 |
| to-spec | 同上 · `skills/engineering/to-spec` | 同上 | MIT | 有注意事项：会"发布到 tracker"，已配置成本地 `.scratch/` | 只能手动：`/to-spec` |
| to-tickets | 同上 · `skills/engineering/to-tickets` | 同上 | MIT | 同 to-spec | 只能手动：`/to-tickets` |
| implement | 同上 · `skills/engineering/implement` | 同上 | MIT | 有注意事项：原文最后一步是自动提交，常驻规则改为先问用户 | 只能手动：`/implement` |
| tdd | 同上 · `skills/engineering/tdd` | 同上 | MIT | 通过。测试改用 unittest，走受保护入口 | 自动加载 |
| codebase-design | 同上 · `skills/engineering/codebase-design` | 同上 | MIT | 通过。"删掉旧单测"的建议被常驻规则否决 | 自动加载 |
| code-review | 同上 · `skills/engineering/code-review` | 同上 | MIT | 通过。只读，用两个并行子代理 | 自动加载 |
| diagnosing-bugs | 同上 · `skills/engineering/diagnosing-bugs` | 同上 | MIT | 有注意事项：反馈循环可能反复调用收费服务；带一个 bash 模板。已设 `disable-model-invocation: true` | 只能手动：`/diagnosing-bugs` |
| verification-before-completion | [obra/superpowers](https://github.com/obra/superpowers) · `skills/verification-before-completion` | `bb92a77741419a4ab5f06e711a283343f1ada0c3`（标签 v7.0.0） | MIT | 通过。只有一个文件 | 自动加载 |
| find-bugs | [getsentry/skills](https://github.com/getsentry/skills) · `skills/find-bugs` | `f57ada2d0b640406ea7e25b11d636c530e77c58b` | Apache-2.0 | 通过。第 1 步用 `gh repo view`（联网），已设 `disable-model-invocation: true`；常驻规则：直接对比 `main` | 只能手动：`/find-bugs` |
| security-and-hardening | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) · `skills/security-and-hardening` | `aeac41d4d005999432bb7312f7ff06383a91d59d` | MIT | 通过。正文链接的 `../../references/security-checklist.md` 在 skill 目录外，已按同一提交复制到 `.agents/references/` | 自动加载 |
| async-python-patterns | [wshobson/agents](https://github.com/wshobson/agents) · `plugins/python-development/skills/async-python-patterns` | `0874a27b17a22e2b7c2e2bcd725768e0803cad06` | MIT | 通过。`references/details.md:158` 说"线程安全"是错的；pytest 示例改用 unittest | 自动加载 |

## 3. 本地改动（和上游原文的差异）

- **gh 自动做的改动（所有用 gh 安装的 skill）**：
  - 在 frontmatter 的 `metadata` 里加入来源信息（`github-repo`、`github-ref`、`github-tree-sha`、`github-path`、`github-pinned`）；
  - 把 frontmatter 的键按字母顺序重排，`metadata` 下面缩进 4 个空格；
  - 删掉 frontmatter 和正文之间的空行。

  除此之外，正文和其他文件和上游逐字节一致（2026-10-21 对照固定提交核对了第二批 15 个和 playwright-cli）。
- **playwright-cli**：删除了 `allowed-tools: Bash(playwright-cli:*) Bash(npx playwright:*) Bash(npx --no-install playwright:*)`。`metadata` 里加了 `golden-mic-local-change`，注明这项改动（Apache-2.0 要求标明改动）。
- **find-bugs、diagnosing-bugs**：加了 `disable-model-invocation: true`，原因写在 `metadata` 的 `golden-mic-local-change` 里：
  - find-bugs 第 1 步会联网运行 `gh repo view`；
  - diagnosing-bugs 的反馈循环可能反复调用收费服务，还带一个 bash 模板。

  find-bugs 是 Apache-2.0，这条说明同时满足标明改动的要求。
- **许可证文件**：每个第三方 skill 目录里都放了一份它上游仓库的 `LICENSE`，从固定提交原样取出，和上游逐字节一致。上游的 skill 目录本身都没有许可证文件，而本仓库是公开的。playwright-cli 的 LICENSE 在上游就是 CRLF 换行，其余都是 LF。
- **security-and-hardening**：按同一提交，把上游仓库根的 `references/security-checklist.md` 复制到了 `.agents/references/security-checklist.md`，让正文里的链接能打开。

升级或重装后，要重新做以上改动。重装会恢复 `allowed-tools`，校验脚本会报错提醒。

## 4. 安装、更新与卸载

安装结果已经提交在仓库里，平时不需要运行下面的命令；它们用来重装，或者核对来源。先在本机终端运行 `gh auth login`（不要把令牌贴到聊天里），然后在仓库根目录用 PowerShell 逐条运行。装完后重新做第 3 节的本地改动：

```powershell
$env:GH_TELEMETRY = "false"   # 关闭 gh 遥测，只对当前终端有效
$gh = "C:\Program Files\GitHub CLI\gh.exe"
& $gh skill install fastapi/fastapi fastapi/.agents/skills/fastapi --allow-hidden-dirs --agent github-copilot --scope project --pin 866b7a3d0ce1025a3811f23aea4846d01a2b16a8
& $gh skill install mattpocock/skills skills/engineering/grill-with-docs --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/productivity/grilling --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/domain-modeling --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/to-spec --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/to-tickets --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/implement --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/tdd --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/codebase-design --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/code-review --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install mattpocock/skills skills/engineering/diagnosing-bugs --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
& $gh skill install obra/superpowers skills/verification-before-completion --agent github-copilot --scope project --pin bb92a77741419a4ab5f06e711a283343f1ada0c3
& $gh skill install getsentry/skills skills/find-bugs --agent github-copilot --scope project --pin f57ada2d0b640406ea7e25b11d636c530e77c58b
& $gh skill install addyosmani/agent-skills skills/security-and-hardening --agent github-copilot --scope project --pin aeac41d4d005999432bb7312f7ff06383a91d59d
& $gh skill install wshobson/agents plugins/python-development/skills/async-python-patterns --agent github-copilot --scope project --pin 0874a27b17a22e2b7c2e2bcd725768e0803cad06
# 第一批 3 个
& $gh skill install addyosmani/web-quality-skills skills/accessibility --agent github-copilot --scope project --pin afa8da942115f2961fdbfa80807ea0b232ff6c00
& $gh skill install emilkowalski/skills skills/break-ui --agent github-copilot --scope project --pin 2d228630ba1446365f614a108cb8252f1feda202
& $gh skill install microsoft/playwright-cli skills/playwright-cli --agent github-copilot --scope project --pin b1a5903c5b633b8c9f843ad7b629c2a78437040c
```

- **更新**：所有第三方 skill 都用 `--pin` 固定了。`gh skill update --all` 会跳过固定的 skill，只打印提示。不要用 `--unpin`，它会让 skill 跟随上游最新版。
- **升级一个 skill**：
  1. 在上游看从旧提交到新提交改了什么（SKILL.md、scripts/、references/），按第 5 节重新审查；
  2. 用新提交重装：加 `--pin <新提交> --force`；
  3. 重新做第 3 节的本地改动，运行校验脚本，用 `git diff` 确认改动都来自上游；
  4. 更新本文表格，提交说明里写清新旧提交号。
- **卸载**：gh 2.102.0 没有卸载命令，直接删除目录，例如 `Remove-Item -Recurse -Force .agents\skills\break-ui`。然后运行校验脚本，更新本文。
- **不用的安装方式**：
  - 插件（会带 hooks 和 MCP 服务器，装在仓库外面，还会自动更新）；
  - `uvx library-skills`（要下载并执行一个 PyPI 工具；`--copy` 的结果和从 `.venv` 手动复制一样）。

## 5. 安全说明

- **来源**：只从发布者的官方 GitHub 仓库安装。目录站、博客、排行榜只用来发现候选。
- **审查方式**：逐个文件读 SKILL.md、scripts/、references/，不执行任何第三方脚本。检查这些项：
  - 网络请求和上传；
  - 读取环境变量、凭证、用户目录，或写到项目以外的位置；
  - 全局安装、下载二进制、遥测；
  - shell 注入、混淆、隐藏的 Unicode 字符；
  - 提示注入，以及要求关闭审批的内容；
  - `allowed-tools`；
  - name 是否和目录一致、frontmatter 是否合法、许可证。

  2026-10-21 已确认：所有已批准的 skill，上游 HEAD 和审查时的提交一致。
- **红线落在哪里**：
  - `.github/copilot-instructions.md` 的红线和"第三方 skill 的项目覆盖规则"；
  - 校验脚本拒绝预授权 shell 的 `allowed-tools`。
- **gh 版本**：要求不低于 2.102.0（本机就是 2.102.0）。更早的版本有两个安全通告（2026-10-21 核对）：
  - [GHSA-qcwj-mr2r-2cx7](https://github.com/cli/cli/security/advisories/GHSA-qcwj-mr2r-2cx7)：`gh skill search` 的参数注入，可以把安装重定向到别的来源。影响 2.90.0–2.101.0，2.102.0 修复。
  - [GHSA-3m3g-3wcr-px46](https://github.com/cli/cli/security/advisories/GHSA-3m3g-3wcr-px46)：多个命令会把外部内容里的终端转义序列原样输出。影响 2.96.0 及更早版本，2.97.0 修复。
- **遥测**：gh 用 `GH_TELEMETRY=false` 或 `DO_NOT_TRACK=1` 关闭。
- **没有安装的东西**：任何 hook、MCP 服务器、插件、自定义 agent。
- **随附的非 Markdown 文件**（安装后逐个看过）：
  - Matt Pocock 的 10 个 skill 各带一个 `agents/openai.yaml`，是 OpenAI Codex 用的显示名称和调用策略，Copilot 不读取；
  - diagnosing-bugs 带 `scripts/hitl-loop.template.sh`，是人工配合复现问题用的 bash 模板。要用时先复制、改写，并经用户同意再运行。

  除此之外没有脚本或可执行文件。
- **已知的校验警告**（来自上游原文，不修改）：
  - accessibility 链接到没有安装的 `../web-quality-audit/SKILL.md`；
  - break-ui 的 `CATALOG.md` 含零宽连接符 U+200D，这是 emoji 测试数据。
- **为什么要逐个审查**：Snyk 的 [ToxicSkills 研究](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/)扫描了 3,984 个公开 skill：
  - 36.82%（1,467 个）至少有一个安全问题；
  - ClawHub 上约 10.9% 含有硬编码或暴露的密钥。

## 6. 许可与署名

本仓库是公开的，提交第三方 skill 就是再分发。各自沿用原许可证，许可证原文随目录一起放：

| 来源 | 许可证 | 版权 |
|---|---|---|
| accessibility | MIT | Copyright (c) 2026 Addy Osmani（[LICENSE](https://github.com/addyosmani/web-quality-skills/blob/afa8da942115f2961fdbfa80807ea0b232ff6c00/LICENSE)） |
| break-ui | MIT | Copyright (c) 2026 Emil Kowalski（[LICENSE](https://github.com/emilkowalski/skills/blob/2d228630ba1446365f614a108cb8252f1feda202/LICENSE)） |
| playwright-cli | Apache-2.0 | Copyright (c) Microsoft Corporation（[LICENSE](https://github.com/microsoft/playwright-cli/blob/b1a5903c5b633b8c9f843ad7b629c2a78437040c/LICENSE)）；本地改动见第 3 节 |
| fastapi | MIT | Copyright (c) 2018 Sebastián Ramírez（[LICENSE](https://github.com/fastapi/fastapi/blob/866b7a3d0ce1025a3811f23aea4846d01a2b16a8/LICENSE)） |
| mattpocock/skills 的各个 skill | MIT | Copyright (c) 2026 Matt Pocock（[LICENSE](https://github.com/mattpocock/skills/blob/5beb35b040e3de97e54fa9e386d7e42d15c295c2/LICENSE)） |
| verification-before-completion | MIT | Copyright (c) 2025 Jesse Vincent（[LICENSE](https://github.com/obra/superpowers/blob/bb92a77741419a4ab5f06e711a283343f1ada0c3/LICENSE)） |
| find-bugs | Apache-2.0 | Copyright 2025 Functional Software, Inc. dba Sentry（[LICENSE](https://github.com/getsentry/skills/blob/f57ada2d0b640406ea7e25b11d636c530e77c58b/LICENSE)）；本地改动见第 3 节 |
| security-and-hardening | MIT | Copyright (c) 2025 Addy Osmani（[LICENSE](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/LICENSE)） |
| async-python-patterns | MIT | Copyright (c) 2024 Seth Hobson（[LICENSE](https://github.com/wshobson/agents/blob/0874a27b17a22e2b7c2e2bcd725768e0803cad06/LICENSE)） |

项目文件里改写或引用了第三方内容的地方：

- `.agents/references/security-checklist.md`：addyosmani/agent-skills 的原文（MIT，提交 `aeac41d`），许可证见 `.agents/skills/security-and-hardening/LICENSE`。
- `docs/agents/issue-tracker.md`、`docs/agents/domain.md`：改写自 mattpocock/skills 的 setup-matt-pocock-skills 模板（MIT）。
- `.github/skills/gm-web-interface-guidelines/references/vercel-web-interface-guidelines/command.md`：Vercel Labs 的原文（MIT），许可证放在同一目录。
- `.github/skills/golden-mic-media/references/ffmpeg-pitfalls.md`：条目选取参考了 0xDarkMatter/claude-mods 的 ffmpeg-ops（MIT，提交 `fa93328`），已经改写。

两个 Apache-2.0 的上游仓库（microsoft/playwright-cli、getsentry/skills）在固定提交下都没有 NOTICE 文件，不需要随附（2026-10-21 核对）。

## 7. 项目自建 skills

| 名称 | 用途 | 加载方式 | 主要来源 |
|---|---|---|---|
| [golden-mic-backend-map](../.github/skills/golden-mic-backend-map/SKILL.md) | 后端地图：模块分工、生命周期、十个阶段与重做规则、三种模式、限额、保留时长、需求 → 代码位置、改动清单 | 只由模型自动加载，不出现在 `/` 菜单 | 交接文档 §1–12、`mode_rules.json`、`drafts.py`、`task_manager.py`、`v2_editing.py`、`workbench.py`、`mode_pipeline.py` |
| [golden-mic-api-change](../.github/skills/golden-mic-api-change/SKILL.md) | 新增或修改接口：状态码、鉴权与 CSRF、请求体、防重复提交、限额同步、测试写法、同步清单 | 自动加载；也可以手动 `/golden-mic-api-change` | `main.py`、`drafts.py`、`workbench.py`、`v2_editing.py`、`admission.py`、`appApi.ts`、`docs/README.md` |
| [golden-mic-media](../.github/skills/golden-mic-media/SKILL.md) | FFmpeg 与音频约定：响度、闪避、切点、跳切、字幕、人名条、导出、子进程、Windows 与 Linux 差异 | 自动加载；也可以手动 | `rendering.py`、`graphics.py`、`tts_pipeline.py`、`audio_filters.py`、`production_modes.py`、`mode_pipeline.py`、`studio_render.py` |
| [golden-mic-providers](../.github/skills/golden-mic-providers/SKILL.md) | 外部 AI 服务：调用位置、键名、鉴权差异、超时与重试、计费、缓存、隐私、模拟写法、官方文档入口 | 自动加载；也可以手动 | `backend/providers/`、`config.py`、`uploads.py`、`mode_pipeline.py`、README、`docs/CONFIGURATION.md` |
| [golden-mic-verify](../.github/skills/golden-mic-verify/SKILL.md) | 离线验证：`pip check`、预检（必须 `ready: true`）、静态检查、受保护回归、前端回归、构建，以及汇报格式 | 自动加载；也可以手动 `/golden-mic-verify`，参数：静态、后端、前端、构建、全部 | `docs/DEVELOPMENT.md`、README、`backend/preflight.py`、`tests/run_v2_validation.py` |
| [gm-news-integrity](../.github/skills/gm-news-integrity/SKILL.md) | 新闻真实性红线、检查码、发布门禁 | 只由模型自动加载 | `mode_rules.json`、`quality.py`、`publication.py` |
| [gm-ux-copy](../.github/skills/gm-ux-copy/SKILL.md) | 中文界面文案与术语 | 自动加载 | `docs/UI_SIMPLIFICATION_20261014.md`、`frontend/src` |
| [gm-web-interface-guidelines](../.github/skills/gm-web-interface-guidelines/SKILL.md) | 按固定版本的 Vercel 界面规范审查前端 | 自动加载；也可以手动 | `references/vercel-web-interface-guidelines/command.md` |
| [gm-ai-labeling](../.github/skills/gm-ai-labeling/SKILL.md) | AI 内容标识的设计草稿 | 只能手动 `/gm-ai-labeling` | `references/regulations.md` |
| [gm-quality-benchmark](../.github/skills/gm-quality-benchmark/SKILL.md) | 用本地授权样本做质量基准的设计草稿 | 只能手动 `/gm-quality-benchmark` | `eval_sample/`、`docs/reports/pipeline/PIPELINE_PERFORMANCE_ANALYSIS.md` |

- 2026-10-21 把三个和必建 skill 重叠的 gm-* 改名并扩写：`gm-verify` → `golden-mic-verify`，`gm-pipeline-map` → `golden-mic-backend-map`，`gm-ffmpeg-render` → `golden-mic-media`。
- 自建 skill 里的数字和行号都标了核对日期。改了 `mode_rules.json`、检查码、阈值、接口或界面术语后，要同步更新对应的 skill。

## 8. 试用和验证

1. **确认能被发现。**
   - 在 VS Code 的 Copilot 对话里：会话目标为 Local 时，输入 `/skills` 打开 Configure Skills；其他目标从命令面板运行"Chat: Open Customizations"，进入 Skills。
   - 应该能看到第 2 节和第 7 节列出的全部 skill。golden-mic-backend-map 和 gm-news-integrity 不出现在 `/` 菜单里，这是设计如此。
2. **逐条试用。** 新开一个 Agent 模式的对话，逐条输入下面的提问。回复引用的文件里出现了对应的 SKILL.md，就算触发成功。具体显示方式以 VS Code 实际界面为准。

| 提问 | 预期触发 |
|---|---|
| 为 `/api/tasks/{id}/align` 设计一个返回说话人置信度的字段 | golden-mic-api-change、fastapi、golden-mic-backend-map |
| 我改完了 `backend/drafts.py`，帮我验证一下能不能提交 | golden-mic-verify、verification-before-completion |
| 转成旁白之后，会从哪个阶段开始重跑？ | golden-mic-backend-map |
| TTS 偶尔返回 429，重试会不会重复收费？ | golden-mic-providers |
| 原声切点为什么会吸附到静音？300 ms 是在哪里定义的？ | golden-mic-media |
| 上传接口还有哪些安全风险需要加固？ | security-and-hardening |
| `task_manager` 里这段 asyncio 的超时和取消该怎么写？ | async-python-patterns |
| 这个路由的依赖注入怎么改成 `Annotated` 写法？ | fastapi |
| 先写测试，再实现"改说话人"的后端校验 | tdd（会引用 codebase-design） |
| 帮我审查一下这个分支相对 main 的改动 | code-review（自动），或手动 `/find-bugs` |
| `/grill-with-docs` 我想给对齐接口加批量模式 | grill-with-docs、grilling、domain-modeling |
| `/diagnosing-bugs` 阶段 9 合成偶尔超时 | diagnosing-bugs |
| QUOTE_MATCH_LOW 这个检查能确认后导出吗？ | gm-news-integrity |
| 把结果页"开始导出"按钮的文案改得更清楚 | gm-ux-copy |

**不应触发的例子**（用来检查 description 写得够不够准）：

| 提问 | 不应触发 |
|---|---|
| 帮我把最新版本部署到生产服务器 | golden-mic-verify |
| 怎么在 Windows 上安装最新版 FFmpeg？ | golden-mic-media |
| 把整个界面换成深色玻璃拟态风格 | gm-web-interface-guidelines |
| 帮我润色一下这篇新闻稿 | gm-ux-copy |

## 9. 用法速查：实现一个后端功能（以 `/align` 为例）

1. `/grill-with-docs`：把需求问清楚，记下术语。写 `GLOSSARY.md` 或 ADR 前会先问你。
2. `/to-spec`：写规格，保存到 `.scratch/<功能>/spec.md`。
3. `/to-tickets`：拆成工单，保存到 `.scratch/<功能>/issues/`。
4. `/implement`：按工单实现。它会调用 tdd，结束时调用 code-review。fastapi、golden-mic-api-change、golden-mic-providers、golden-mic-backend-map 按需自动加载。原文最后一步是提交到当前分支；按常驻规则，提交前会先问你。
5. `/find-bugs`：审查分支改动里的 bug 和安全问题。
6. golden-mic-verify：跑验证并按格式汇报。verification-before-completion 保证拿不出结果就不说"完成"。

## 10. 维护建议（未实施）

- **CI**：可以在 `.github/workflows/linux-ci.yml` 里加一步 `python -I -B scripts/validate_agent_skills.py`（只用标准库）。本次按约束没有改 CI。
- **新增 skill 前**：先查同类有没有已经装的，同一种能力只保留一个，并按第 5 节审查。
