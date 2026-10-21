# Agent Skills 清单

更新于 2026-10-21（分支 `chore/agent-skills`）。本文记录本仓库给 VS Code + GitHub Copilot 使用的 Agent Skills：装了哪些、为什么装、固定在哪个版本、怎样更新和卸载，以及怎样手动试用自建 skill。

## 1. 总览

| 类别 | 目录 | 数量 | 维护方式 |
|---|---|---|---|
| 第三方 skill | `.agents/skills/<名称>/` | 已安装 3 个；已批准、待安装 11 个 | 用 `gh skill install --pin <提交>` 安装，不改原文 |
| 项目自建 skill | `.github/skills/gm-*/` | 8 个 | 本仓库维护，事实来自仓库文件 |
| 常驻规则 | `.github/copilot-instructions.md` | 1 个文件 | 每次对话自动加载 |

- 上限：第三方加自建合计不超过 22 个。待安装的 11 个装完后正好 22 个，以后要新增，先删掉一个。
- 规则优先级：用户的明确指示 > `.github/copilot-instructions.md` > 项目 skill（gm-*）> 第三方 skill。第三方 skill 和项目规则冲突时，以常驻规则里"第三方 skill 的项目覆盖规则"一节为准。
- 校验：`& .\.venv\Scripts\python.exe scripts/validate_agent_skills.py`，加 `--list` 可列出每个 skill。只用标准库，不联网。
- 规范：[Agent Skills 规范](https://agentskills.io/specification)；[VS Code Agent Skills 文档](https://code.visualstudio.com/docs/agent-customization/agent-skills)。

## 2. 已安装的第三方 skills

安装目录都是 `.agents/skills/<名称>/`（gh 的 `--agent github-copilot --scope project`）。

| 名称 | 仓库 · 路径 | 固定提交 | 许可证 | 用途 | 审查结论（分/18） |
|---|---|---|---|---|---|
| accessibility | [addyosmani/web-quality-skills](https://github.com/addyosmani/web-quality-skills) · `skills/accessibility` | `afa8da942115f2961fdbfa80807ea0b232ff6c00` | MIT | 按 WCAG 2.2 审查和改进可访问性 | 采纳（15）。覆盖 WCAG 2.2 新增的 9 条。它建议的 `npm i -g @axe-core/cli`、`npx lighthouse` 要先经用户批准。正文链接到没装的 web-quality-audit，校验记为警告 |
| break-ui | [emilkowalski/skills](https://github.com/emilkowalski/skills) · `skills/break-ui` | `2d228630ba1446365f614a108cb8252f1feda202` | MIT | 用最坏情况数据（超长标题和人名、缺字段、极端数字、非拉丁文字等）压测组件，列出问题和修法 | 采纳（16）。会加"演示数据 / 最坏情况"开关和测试数据，只能留在开发环境。`CATALOG.md` 里的零宽连接符 U+200D 是有意的测试数据，校验记为警告 |
| playwright-cli | [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) · `skills/playwright-cli` | `b1a5903c5b633b8c9f843ad7b629c2a78437040c` | Apache-2.0 | 浏览器自检：打开本地开发页、截图、读页面快照 | 采纳（14）。在 frontend 目录用项目自带 Playwright 1.63 的 `npx --no-install playwright cli`，加 `--browser=msedge`；不全局安装、不选文件、不上传，不走"上传到 PR"的流程。输出目录 `.playwright-cli/` 已加入 `.gitignore` |

这三个 skill 都不需要在聊天里运行初始化命令。

安装命令（PowerShell，在仓库根目录逐条运行）：

```powershell
$env:GH_TELEMETRY = "false"   # 关闭 gh 遥测，只对当前终端有效
gh skill install addyosmani/web-quality-skills skills/accessibility --agent github-copilot --scope project --pin afa8da942115f2961fdbfa80807ea0b232ff6c00
gh skill install emilkowalski/skills skills/break-ui --agent github-copilot --scope project --pin 2d228630ba1446365f614a108cb8252f1feda202
gh skill install microsoft/playwright-cli skills/playwright-cli --agent github-copilot --scope project --pin b1a5903c5b633b8c9f843ad7b629c2a78437040c
```

卸载命令见第 5 节。

## 3. 已批准、待安装的第三方 skills

2026-10-21 安装时，公司出口 IP 的未登录 GitHub API 额度（每小时 60 次，同一出口 IP 的所有人共用）已被用完，gh 返回 403，下面 11 个没装上。运行 `gh auth login` 之后，按本节命令逐条安装，装完把表格移到第 2 节。

| 名称 | 仓库 · 路径 | 固定提交 | 许可证 | 用途 | 审查结论（分/18）与使用限制 |
|---|---|---|---|---|---|
| async-python-patterns | [wshobson/agents](https://github.com/wshobson/agents) · `plugins/python-development/skills/async-python-patterns` | `0874a27b17a22e2b7c2e2bcd725768e0803cad06` | MIT | asyncio 并发、超时、取消的写法 | 采纳（14）。忽略其中的 pytest、httpbin 示例；HTTP 客户端沿用 httpx |
| python-performance-optimization | 同上 · `plugins/python-development/skills/python-performance-optimization` | 同上 | MIT | 用 cProfile 等工具定位 Python 性能瓶颈 | 采纳（15）。它建议用 `pip install` 装分析工具，要先经用户批准 |
| tdd | [mattpocock/skills](https://github.com/mattpocock/skills) · `skills/engineering/tdd` | `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | MIT | 测试先行的红-绿-重构流程 | 采纳（17）。不绑定测试运行器；运行测试一律走 gm-verify 的受保护入口 |
| diagnosing-bugs | 同上 · `skills/engineering/diagnosing-bugs` | 同上 | MIT | 难查的 bug 和性能回退的诊断循环 | 采纳（16）。复现循环只用合成素材，不反复触发收费调用；附带的 `scripts/hitl-loop.template.sh` 只是模板 |
| grill-with-docs | 同上 · `skills/engineering/grill-with-docs` | 同上 | MIT | 追问式打磨方案，同时记录术语表和 ADR | 采纳（16）。上游设为只能手动调用（`/grill-with-docs`），依赖 grilling 和 domain-modeling |
| grilling | 同上 · `skills/productivity/grilling` | 同上 | MIT | 追问、压力测试一个方案或决定 | 采纳（16，与 grill-with-docs 一起评估） |
| domain-modeling | 同上 · `skills/engineering/domain-modeling` | 同上 | MIT | 整理领域术语，写 `GLOSSARY.md` 和 ADR | 采纳（16，同上）。写 `GLOSSARY.md`、`docs/adr/` 前先征得用户同意 |
| improve-codebase-architecture | 同上 · `skills/engineering/improve-codebase-architecture` | 同上 | MIT | 找可以"加深"的模块，出 HTML 报告后逐项讨论 | 采纳（13）。上游设为只能手动调用；只出方案，不直接重构 |
| codebase-design | 同上 · `skills/engineering/codebase-design` | 同上 | MIT | 深模块、接口、接缝的设计词汇 | 采纳（16）。原文"删掉旧单测"的建议被项目规则否决：不删除已有测试 |
| fastapi | [fastapi/fastapi](https://github.com/fastapi/fastapi) · `fastapi/.agents/skills/fastapi` | `866b7a3d0ce1025a3811f23aea4846d01a2b16a8`（标签 0.139.2，和 `uv.lock` 一致） | MIT | FastAPI 官方最佳实践（依赖、Pydantic、SSE、托管前端） | 采纳（15）。在隐藏目录里，安装要加 `--allow-hidden-dirs` |
| insecure-defaults | [trailofbits/skills](https://github.com/trailofbits/skills) · `plugins/insecure-defaults/skills/insecure-defaults` | `9ea55c598763f7cb87ab56933d773d7dc34344a0`（旧版） | CC BY-SA 4.0 | 找"出错时放行"的不安全默认值：硬编码密钥、弱认证、过宽权限 | 采纳（16）。上游最新版已改成只能在 Claude Code 里运行的多代理流程，所以固定在这个旧版，不随上游更新。要署名，见第 7 节 |

这些 skill 也不需要初始化命令。mattpocock 的 `setup-matt-pocock-skills` 没有安装，上面几个 skill 不读取它的输出。

```powershell
$env:GH_TELEMETRY = "false"
gh skill install wshobson/agents plugins/python-development/skills/async-python-patterns --agent github-copilot --scope project --pin 0874a27b17a22e2b7c2e2bcd725768e0803cad06
gh skill install wshobson/agents plugins/python-development/skills/python-performance-optimization --agent github-copilot --scope project --pin 0874a27b17a22e2b7c2e2bcd725768e0803cad06
gh skill install mattpocock/skills skills/engineering/tdd --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
gh skill install mattpocock/skills skills/engineering/diagnosing-bugs --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
gh skill install mattpocock/skills skills/engineering/grill-with-docs --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
gh skill install mattpocock/skills skills/productivity/grilling --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
gh skill install mattpocock/skills skills/engineering/domain-modeling --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
gh skill install mattpocock/skills skills/engineering/improve-codebase-architecture --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
gh skill install mattpocock/skills skills/engineering/codebase-design --agent github-copilot --scope project --pin 5beb35b040e3de97e54fa9e386d7e42d15c295c2
gh skill install fastapi/fastapi fastapi/.agents/skills/fastapi --allow-hidden-dirs --agent github-copilot --scope project --pin 866b7a3d0ce1025a3811f23aea4846d01a2b16a8
gh skill install trailofbits/skills plugins/insecure-defaults/skills/insecure-defaults --agent github-copilot --scope project --pin 9ea55c598763f7cb87ab56933d773d7dc34344a0
```

## 4. 待装清单（未批准）

下列候选审查过，暂不安装。要装哪一个，都要先按当时的版本重新审查、经用户批准，并先腾出名额。

| 候选 | 仓库 · 路径 | 审查时的提交 | 许可证 | 分/18 | 暂不安装的原因 | 什么时候再考虑 |
|---|---|---|---|---|---|---|
| impeccable | [pbakaus/impeccable](https://github.com/pbakaus/impeccable) · `.github/skills/impeccable` | `374a0e179bb36725283a612462131705ba4780ae` | Apache-2.0 | 10 | 每个会话都会运行；首次运行下载并执行约 20 MB 的引擎，只有同一来源提供的 sha256 校验；会访问 impeccable.style；遥测默认开启；自带审美规则，和"界面已定型"冲突 | 界面要大改版，并且能接受下载执行引擎时。装后要在聊天里运行 `/impeccable init`（会生成 PRODUCT.md）和 `/impeccable document` |
| web-design-reviewer | [github/awesome-copilot](https://github.com/github/awesome-copilot) · `skills/web-design-reviewer` | `613efc8c21d9b3ce642dff361d17006cd9967e28` | MIT | 14 | 和 playwright-cli、break-ui 重叠，而且会直接改代码 | 需要"截图审查加自动修复"的一体流程时 |
| security-review | 同上 · `skills/security-review` | 同上 | MIT | 15 | 还没做逐文件的全量审查 | 做安全专项前，先完成审查 |
| hybrid-search-implementation | [wshobson/agents](https://github.com/wshobson/agents) · `plugins/llm-application-dev/skills/hybrid-search-implementation` | `0874a27b17a22e2b7c2e2bcd725768e0803cad06` | MIT | 14 | 优先级低 | P1 做素材检索时 |
| vercel-react-best-practices | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) · `skills/react-best-practices` | `063bee94c3f4df8453406c830b0a7df0f2860278` | 仓库没有 LICENSE | 12 | 没有许可证；规则以 Next.js 为主；name 和文件夹名不一致 | 上游补上许可证之后 |

```powershell
# 每条都要先重新审查、经批准并腾出名额
gh skill install pbakaus/impeccable .github/skills/impeccable --allow-hidden-dirs --agent github-copilot --scope project --pin 374a0e179bb36725283a612462131705ba4780ae
gh skill install github/awesome-copilot skills/web-design-reviewer --agent github-copilot --scope project --pin 613efc8c21d9b3ce642dff361d17006cd9967e28
gh skill install github/awesome-copilot skills/security-review --agent github-copilot --scope project --pin 613efc8c21d9b3ce642dff361d17006cd9967e28
gh skill install wshobson/agents plugins/llm-application-dev/skills/hybrid-search-implementation --agent github-copilot --scope project --pin 0874a27b17a22e2b7c2e2bcd725768e0803cad06
# 上游补上 LICENSE 前不要安装；装后如果 name 和文件夹不一致，只能改文件夹名
gh skill install vercel-labs/agent-skills skills/react-best-practices --agent github-copilot --scope project --pin 063bee94c3f4df8453406c830b0a7df0f2860278
```

评估过但不采纳（不进待装清单）：
- python-resilience（12 分）：对付费 POST 请求自动重试 3 次，没有幂等键，会重复计费；还要引入 tenacity、structlog。
- llm-evaluation、prompt-engineering-patterns（13、12 分）：基于 LangChain/LangSmith，会上传数据、调用付费 API，指标只适用于英文。
- setup-matt-pocock-skills（13 分）：已选的 skill 不读它的输出，还会写 CLAUDE.md、AGENTS.md。superpowers：和 mattpocock 的 skill 冲突，还带 SessionStart hook。
- supply-chain-risk-auditor（11 分）：会读取 `gh auth token`，并把依赖清单上传到 OSV 等服务。
- insecure-defaults 最新版（12 分）：只能在 Claude Code 里运行，所以改装旧版。
- astral-sh 的 uv、ruff（14 分）：近 8 个月没更新，并引导用户运行 `uvx`、`uv tool install`。
- web-design-guidelines（11 分）：仓库没有许可证，而且每次运行都从 main 分支拉取规则；已用自建的 gm-web-interface-guidelines 代替。
- anthropics 的 frontend-design、webapp-testing：前者默认不装，后者和 playwright-cli 重叠。openai/skills 已于 2026-06-22 弃用。
- FFmpeg 社区 skill、国内内容审核 MCP：找不到可信机构发布的版本。FFmpeg 相关约定已写进自建的 gm-ffmpeg-render。

## 5. 更新与卸载

- 第三方 skill 都用 `--pin` 固定。`gh skill update --all` 会**跳过**固定的 skill，只打印提示，所以它不会自动更新这里的任何一个。不要用 `--unpin`，它会解除固定，改为跟随上游最新版。
- 升级一个 skill：
  1. 在上游看从旧提交到新提交改了什么（SKILL.md、scripts/、references/），按第 6 节重新审查。
  2. 用新提交重装：`gh skill install <仓库> <路径> --agent github-copilot --scope project --pin <新提交> --force`。
  3. 运行校验脚本，用 `git diff` 确认改动都来自上游。提交说明里写清新旧提交号。
  4. 更新本文的表格。
- 卸载：gh 2.102.0 没有卸载子命令，直接删除目录，例如 `Remove-Item -Recurse -Force .agents\skills\break-ui`。然后运行校验脚本、更新本文并提交。

## 6. 安全说明

- 来源：只从发布者的官方 GitHub 仓库安装。目录站、博客、排行榜只用来发现候选。拒绝 `curl | bash` 式安装、来历不明的直链下载、没有许可证或长期不维护的仓库。
- 审查：每个候选都逐文件读过 SKILL.md、scripts/、references/。检查项包括：
  - 网络请求和上传；
  - 读取环境变量、凭证或用户目录，写入项目以外的位置；
  - 全局安装、下载二进制、遥测；
  - shell 注入、混淆或 base64 内容、隐藏的 Unicode 字符；
  - 提示注入话术，以及要求关闭审批的内容；
  - name 和文件夹是否一致、frontmatter 是否合法、许可证。

  打分按 9 个维度，每项 0–2 分，满分 18：来源可信度、维护活跃度、采用度、许可证、与项目需求的契合度、能否在 VS Code / Copilot 中使用、安全审查、上下文成本、与项目规则的冲突程度。
- 安装后核对：gh 只改了 frontmatter，加入来源元数据（`github-repo`、`github-ref`、`github-tree-sha`、`github-path`、`github-pinned`），并重新排列键的顺序、把缩进改成 4 个空格。正文和其他文件与审查时的上游文件逐字节一致。
- 不改第三方原文。要定制时，新建 `gm-` 开头的项目 skill，注明来源和许可证，并移除原版，避免两份同时生效。
- 项目覆盖规则写在 `.github/copilot-instructions.md`：
  - 测试命令换成 gm-verify 的受保护入口；
  - 全局安装和下载要先经批准；
  - 浏览器自检只看本地开发页，不选文件、不上传；
  - 复现和评测只用合成素材；
  - 写 GLOSSARY.md、`docs/adr/` 前先问用户。
- gh 版本要求不低于 2.102.0。更早的版本有两个安全通告：[GHSA-qcwj-mr2r-2cx7](https://github.com/cli/cli/security/advisories/GHSA-qcwj-mr2r-2cx7)（2.102.0 修复）、[GHSA-3m3g-3wcr-px46](https://github.com/cli/cli/security/advisories/GHSA-3m3g-3wcr-px46)（2.97.0 修复）。见 [gh 2.102.0 发布说明](https://github.com/cli/cli/releases/tag/v2.102.0)。
- 遥测：gh 用 `GH_TELEMETRY=false` 或 `DO_NOT_TRACK=1` 关闭。没有安装任何 hook、MCP 服务器、插件或自定义 agent，也没有使用 Vercel 的 `skills` 命令行。
- 已知校验警告，来自上游原文，不修改：
  - accessibility 链接到没装的 `../web-quality-audit/SKILL.md`；
  - break-ui 的 `CATALOG.md` 含零宽连接符 U+200D（emoji 测试数据）。
- 为什么要逐个审查：Snyk 的 [ToxicSkills 研究](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/)扫描了 3,984 个公开 skill。36.82%（1,467 个）至少有一个安全问题，13.4% 有严重问题，并确认了 76 个恶意载荷。

## 7. 许可与署名

本仓库提交了下列第三方 skill 的副本，各自沿用原许可证。许可证原文链接都固定到安装的提交：
- accessibility：MIT，Copyright (c) 2026 Addy Osmani。[LICENSE](https://github.com/addyosmani/web-quality-skills/blob/afa8da942115f2961fdbfa80807ea0b232ff6c00/LICENSE)
- break-ui：MIT，Copyright (c) 2026 Emil Kowalski。[LICENSE](https://github.com/emilkowalski/skills/blob/2d228630ba1446365f614a108cb8252f1feda202/LICENSE)
- playwright-cli：Apache-2.0，Microsoft。[LICENSE](https://github.com/microsoft/playwright-cli/blob/b1a5903c5b633b8c9f843ad7b629c2a78437040c/LICENSE)
- wshobson/agents 的 skill：MIT，Copyright (c) 2024 Seth Hobson。[LICENSE](https://github.com/wshobson/agents/blob/0874a27b17a22e2b7c2e2bcd725768e0803cad06/LICENSE)
- mattpocock/skills 的 skill：MIT，Copyright (c) 2026 Matt Pocock。[LICENSE](https://github.com/mattpocock/skills/blob/5beb35b040e3de97e54fa9e386d7e42d15c295c2/LICENSE)
- fastapi：MIT，Copyright (c) 2018 Sebastián Ramírez。[LICENSE](https://github.com/fastapi/fastapi/blob/866b7a3d0ce1025a3811f23aea4846d01a2b16a8/LICENSE)
- insecure-defaults（安装后）：作者 Trail of Bits，来自 [trailofbits/skills](https://github.com/trailofbits/skills) 的提交 `9ea55c598763f7cb87ab56933d773d7dc34344a0`，按 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) 授权。改动：gh 安装时在 frontmatter 里加入来源元数据并重新排列键，正文未改。这份改动后的副本同样按 CC BY-SA 4.0 授权。
- gm-web-interface-guidelines 里的规则原文 `command.md`：MIT，Copyright (c) 2025 Vercel Labs，许可证原文随文件放在 `references/vercel-web-interface-guidelines/LICENSE`。

## 8. 维护建议（未实施）

- CI：可以在 `.github/workflows/linux-ci.yml` 里加一步 `python scripts/validate_agent_skills.py`（只用标准库，不用装依赖），防止 skill 的 frontmatter 和链接被改坏。本次按约束没有改 CI。
- 新增 skill 前先数一下总数，不超过 22 个；同一能力只保留一个。
