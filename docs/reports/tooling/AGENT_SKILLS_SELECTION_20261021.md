# 金话筒后端 Agent Skills 选型报告（阶段一）

- 日期：2026-10-21，分支 `chore/agent-skills`。外部来源的查看日期都是这一天。
- 状态：用户于 2026-10-21 确认了安装清单，按 2.1 装了 15 个，可选的 2 个没有装。安装结果和之后的维护见 [AGENT_SKILLS.md](../../AGENT_SKILLS.md)。下文保留确认前的原貌。
- 范围：服务于后端开发（FastAPI、FFmpeg、语音与 AI 服务、测试、安全）的 Agent Skills，用在 VS Code + GitHub Copilot。

## 1. 结论

1. **建议新装 15 个第三方 skill**（见 2.1），另有 2 个可选（见 2.2）。
2. **工作流框架选 Matt Pocock**，用 `gh skill install --pin` 逐个安装，不用插件。Superpowers 只取 `verification-before-completion` 这一个。
3. **已装的 playwright-cli 违反红线 6**：它的 frontmatter 里预授权了 Bash（见 2.3）。
4. **上个会话的待装清单有 3 个改判**：上个会话"已批准、待安装"的 11 个里，python-performance-optimization 改为仅参考，improve-codebase-architecture 改为暂不安装，insecure-defaults 改为拒绝（见 2.4）。
5. **任务书 §1.4 的部分内容已经过时**：其中标为"需新增"的 `/pretranscribe`、`/align`、版本、应用修改、录音接口，代码里已经有了（见第 6 节）。阶段三写 skill 时以代码为准。
6. **5 个必建 skill 里有 3 个和现有的 gm-* 重叠**，建议合并（见第 5 节）。

## 2. 候选清单

列说明：

- **最近提交**：写成"skill 目录的最后一次提交 / 整个仓库的最后一次推送"。
- **采用度**：GitHub 星标 / skills.sh 安装量 · skills.sh 上的三方审计（Gen Agent Trust Hub、Socket、Snyk）。安装量和审计结果只作参考，不是审核结论。
- **"gh --pin"**：指 `gh skill install <仓库> <路径> --pin <提交>`，装到 `.agents/skills/`（见第 4 节）。

### 2.1 建议安装（15 个）

| 名称 | 来源 | 发布者 | 许可证 | 最近提交 | 采用度 | 安全审查 | 与金话筒的关联 | 安装方式 | 建议与理由 |
|---|---|---|---|---|---|---|---|---|---|
| fastapi | [fastapi/fastapi](https://github.com/fastapi/fastapi/tree/866b7a3d0ce1025a3811f23aea4846d01a2b16a8/fastapi/.agents/skills/fastapi) `fastapi/.agents/skills/fastapi`，标签 `0.139.2`（`866b7a3`） | 官方组织 | MIT（skill 目录里没有 LICENSE，要随附仓库根的 LICENSE） | 2026-06-25 / 2026-10-20 | ★103.3K / 13.0K · 三项通过 | 通过。只有 SKILL.md 和 6 个参考文件，没有脚本，不联网，不读环境变量。本地 `.venv` 里 0.139.2 自带的副本和这个标签逐字节一致 | 新增或修改接口：`Annotated` 依赖、返回类型、SSE、托管前端 | gh --pin，要加 `--allow-hidden-dirs`；不登录 gh 时从 `.venv` 复制 | **安装**。版本和 `uv.lock` 锁定的一致。和项目冲突的 4 处由常驻规则覆盖：不运行 `fastapi dev` / `fastapi run`（前者带 reload，后者监听 0.0.0.0）；不在 `pyproject.toml` 加 `[tool.fastapi]`；不 `uv add asyncer`；不改现有的前端托管方式 |
| grill-with-docs | [mattpocock/skills](https://github.com/mattpocock/skills/tree/5beb35b040e3de97e54fa9e386d7e42d15c295c2/skills/engineering/grill-with-docs) `skills/engineering/grill-with-docs`，`5beb35b` | 知名作者（个人） | MIT | 2026-08-15 / 2026-10-19 | ★298.9K / 1.2M · 三项通过 | 通过。上游已设为只能手动调用。正文"Call the Skill tool"是 Claude Code 的说法，Copilot 会按名称读取对应的 skill | 接到 `/align` 这类需求后的第 1 步：追问细节，同时记录术语和决策 | gh --pin | **安装**。它要调用 grilling 和 domain-modeling，三个一起装 |
| grilling | 同上 `skills/productivity/grilling` | 同上 | MIT | 2026-10-07 / 同上 | 956.5K · 三项通过 | 通过 | grill-with-docs 里的追问环节 | gh --pin | **安装** |
| domain-modeling | 同上 `skills/engineering/domain-modeling` | 同上 | MIT | 2026-09-17 / 同上 | 857.3K · 三项通过 | 通过。会写 `GLOSSARY.md` 和 `docs/adr/` | 统一"稿句、原声句、发布前检查"等术语 | gh --pin | **安装**。常驻规则：写这两处之前先问你 |
| to-spec | 同上 `skills/engineering/to-spec` | 同上 | MIT | 2026-08-19 / 同上 | 697.5K · 三项通过 | 有注意事项：会把规格"发布到 issue tracker"。配置成本地 `.scratch/` 目录后不会建 GitHub issue | 第 2 步：写规格 | gh --pin | **安装**（只能手动调用） |
| to-tickets | 同上 `skills/engineering/to-tickets` | 同上 | MIT | 2026-10-06 / 同上 | 693.0K · Snyk 警告 W011 | 同 to-spec，本地每个工单一个文件 | 第 3 步：拆成可独立验收的工单 | gh --pin | **安装**（只能手动调用） |
| implement | 同上 `skills/engineering/implement` | 同上 | MIT | 2026-10-07 / 同上 | 756.1K · 三项通过 | 有注意事项：最后一行要求"Commit your work to the current branch."；最后跑全量测试 | 第 4 步：按工单实现，过程中调用 tdd，结束时调用 code-review | gh --pin | **安装**（只能手动调用）。常驻规则：不自动提交，改为列出改动后问你；测试一律走 golden-mic-verify 的入口 |
| tdd | 同上 `skills/engineering/tdd` | 同上 | MIT | 2026-10-07 / 同上 | 1.1M · 三项通过 | 通过。示例是 TS/jest；`mocking.md` 主张外部服务一律用模拟，和红线 1 一致 | 先写测试再实现 | gh --pin | **安装**。它引用 codebase-design |
| codebase-design | 同上 `skills/engineering/codebase-design` | 同上 | MIT | 2026-09-17 / 同上 | 831.8K · 三项通过 | 通过。`DEEPENING.md:34` 建议删掉旧单测 | tdd 用到的设计概念（深模块、接缝） | gh --pin | **安装**。常驻规则：不删除已有测试 |
| code-review | 同上 `skills/engineering/code-review` | 同上 | MIT | 2026-10-07 / 同上 | 770.5K · Snyk 警告 W011 | 通过。用两个并行子代理审查 `git diff <对比点>...HEAD`，只读 | implement 收尾时按代码规范和规格各审一遍 | gh --pin | **安装** |
| diagnosing-bugs | 同上 `skills/engineering/diagnosing-bugs` | 同上 | MIT | 2026-10-07 / 同上 | 816.5K · 三项通过 | 有注意事项：默认会自动触发；反馈循环要反复运行（例如上千次随机输入），可能反复调用收费服务；带一个 bash 模板脚本 | 排查失败任务和性能回退 | gh --pin | **安装**，按任务书 4.4 设 `disable-model-invocation: true`。复现只用合成素材和模拟服务 |
| verification-before-completion | [obra/superpowers](https://github.com/obra/superpowers/tree/bb92a77741419a4ab5f06e711a283343f1ada0c3/skills/verification-before-completion) `skills/verification-before-completion`，标签 `v7.0.0`（`bb92a77`；这个目录和当前 HEAD 一致） | 知名作者（个人） | MIT | 2026-07-23 / 2026-10-21 | ★302.7K / 247.5K · 三项通过 | 通过。只有一个文件，没有脚本 | 拿不出验证结果就不许说"完成"；具体跑哪些命令由 golden-mic-verify 规定 | gh --pin | **安装** |
| find-bugs | [getsentry/skills](https://github.com/getsentry/skills/tree/f57ada2d0b640406ea7e25b11d636c530e77c58b/skills/find-bugs) `skills/find-bugs`，`f57ada2` | 官方组织（Sentry） | Apache-2.0（在仓库根，要随附） | 2026-04-19 / 2026-10-19 | ★1.1K / 6.9K · 三项通过 | 通过。只报告，不改代码。第 1 步用 `gh repo view` 查默认分支，要联网和登录 | 第 5 步：审查分支改动里的 bug 和安全问题 | gh --pin | **安装**，设 `disable-model-invocation: true`。常驻规则：对比分支直接用 `main` |
| security-and-hardening | [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills/tree/aeac41d4d005999432bb7312f7ff06383a91d59d/skills/security-and-hardening) `skills/security-and-hardening`，`aeac41d` | 知名作者（个人） | MIT | 2026-10-03 / 2026-10-13 | ★109.1K / 65.6K · 三项通过 | 通过。正文链接仓库根的 `references/security-checklist.md`，这个文件在 skill 目录外，要按同一提交单独复制，否则链接失效。建议发布前跑依赖漏洞审计，要联网，需先经你批准 | 上传、CSRF、匿名访问与会话；把 LLM 和 ASR 的输出当作不可信输入；隐私 | gh --pin，另外手动复制 1 个参考文件 | **安装**。同类里只有它可用：openai 的已弃用，Sentry 的 security-review 预授权了 Bash |
| async-python-patterns | [wshobson/agents](https://github.com/wshobson/agents/tree/0874a27b17a22e2b7c2e2bcd725768e0803cad06/plugins/python-development/skills/async-python-patterns) `plugins/python-development/skills/async-python-patterns`，`0874a27` | 知名作者（个人） | MIT | 2026-10-15 / 2026-10-19 | ★41.3K / 17.9K · 三项通过 | 通过。`references/details.md:158` 把用 `asyncio.Lock` 的计数器说成"线程安全"，这是错的；测试示例用 pytest-asyncio | 任务管理、并发、超时与取消、用 httpx 调外部服务 | gh --pin | **安装**（上个会话已批准）。常驻规则：测试用 unittest，走受保护入口 |

### 2.2 可选（2 个，默认不装）

| 名称 | 来源 | 发布者 | 许可证 | 最近提交 | 采用度 | 安全审查 | 与金话筒的关联 | 安装方式 | 建议与理由 |
|---|---|---|---|---|---|---|---|---|---|
| test-gap-audit | [github/awesome-copilot](https://github.com/github/awesome-copilot/tree/613efc8c21d9b3ce642dff361d17006cd9967e28/skills/test-gap-audit) `skills/test-gap-audit`，`613efc8` | 官方组织（GitHub） | MIT | 2026-09-08 / 2026-10-21 | ★40.2K / 71 · 三项通过 | 通过。自带 `scripts/coverage_map.py`：只用标准库，只读，按 `git ls-files` 列文件 | 实现 §1.4 的功能前，找出没有测试覆盖的路径 | gh --pin，设只能手动调用 | **可选**。和 tdd 有部分重叠，采用量很低 |
| docs-sync-audit | 同上 `skills/docs-sync-audit` | 同上 | MIT | 2026-09-08 / 同上 | 63 · 三项通过 | 有条件通过。`scripts/docs_drift.py:220-242` 会读取被 git 跟踪的 `.env*` 文件，本仓库只有 `.env.example`，而且只取键名。`.env`、`.env.*` 已被忽略 | 改接口后，核对 README 和 `docs/` 有没有过时 | gh --pin，设只能手动调用 | **可选**。golden-mic-api-change 里会列出要同步的文档清单，可以先不用它 |

### 2.3 已安装（上个会话装的，本次复核）

| 名称 | 来源 | 发布者 | 许可证 | 最近提交 | 采用度 | 安全审查 | 与金话筒的关联 | 安装方式 | 建议与理由 |
|---|---|---|---|---|---|---|---|---|---|
| accessibility | [addyosmani/web-quality-skills](https://github.com/addyosmani/web-quality-skills) `skills/accessibility`，`afa8da9` | 知名作者（个人） | MIT | — / 2026-08-24 | ★3.0K | 通过（上个会话审查） | 前端可访问性，不属于这次的后端范围 | 已用 gh 装在 `.agents/skills/` | **保留**。目录里缺许可证文件，推送前要补 |
| break-ui | [emilkowalski/skills](https://github.com/emilkowalski/skills) `skills/break-ui`，`2d22863` | 知名作者（个人） | MIT | — / 2026-10-11 | ★48.1K | 通过 | 用最坏情况的数据压测前端组件 | 同上 | **保留**。要补许可证文件 |
| playwright-cli | [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) `skills/playwright-cli`，`b1a5903` | 官方组织 | Apache-2.0 | — / 2026-10-19 | ★14.4K | **违反红线 6**：frontmatter 写了 `allowed-tools: Bash(playwright-cli:*) Bash(npx playwright:*) Bash(npx --no-install playwright:*)`。GitHub 文档说明 Copilot 对列出的工具不再要求确认 | 浏览器自检 | 同上 | **建议删掉 `allowed-tools` 这一行**，作为唯一的本地改动记入清单。要补许可证文件 |

### 2.4 上个会话已批准、本次改判（3 个）

| 名称 | 来源 | 发布者 | 许可证 | 最近提交 | 采用度 | 安全审查 | 与金话筒的关联 | 安装方式 | 建议与理由 |
|---|---|---|---|---|---|---|---|---|---|
| insecure-defaults | [trailofbits/skills](https://github.com/trailofbits/skills) `plugins/insecure-defaults/skills/insecure-defaults`，`9ea55c5` | 官方组织 | CC BY-SA 4.0 | 2026-08-05 起上游已删掉这个 skill / 2026-10-20 | ★7.7K · Snyk W007 高危 | **不通过**：`allowed-tools: Read Grep Glob Bash`（红线 6） | 上线前查不安全的默认配置 | — | **拒绝**。上游最新版已改成只能在 Claude Code 里运行的插件命令 |
| improve-codebase-architecture | [mattpocock/skills](https://github.com/mattpocock/skills) `skills/engineering/improve-codebase-architecture`，`5beb35b` | 知名作者（个人） | MIT | — / 2026-10-19 | skills.sh ATH 警告、Snyk 警告 W012 | **不通过**：生成的报告页从 CDN 加载 Tailwind 和 Mermaid（`securityLevel: "loose"`），并用 `start` 自动打开（`HTML-REPORT.md:13-16`，`SKILL.md:39`） | 找可以"加深"的模块，不是这一轮后端工作的必需品 | — | **暂不安装**。需要时可以特批，同时加"不用 CDN、不自动打开"的覆盖规则 |
| python-performance-optimization | [wshobson/agents](https://github.com/wshobson/agents) `plugins/python-development/skills/python-performance-optimization`，`0874a27` | 知名作者（个人） | MIT | — / 2026-10-19 | — | 有注意事项：多处建议 `pip install` 分析工具；`references/details.md:180-183` 说 `map` 加 `lambda` 比列表推导更快，结论错误 | 本项目的耗时主要在 FFmpeg 和外部服务，关联低 | — | **仅参考** |

### 2.5 仅参考（不装）

| 名称 | 来源 | 发布者 | 许可证 | 最近提交 | 采用度 | 安全审查 | 与金话筒的关联 | 安装方式 | 建议与理由 |
|---|---|---|---|---|---|---|---|---|---|
| setup-matt-pocock-skills | [mattpocock/skills](https://github.com/mattpocock/skills) `skills/engineering/setup-matt-pocock-skills`，`5beb35b` | 知名作者（个人） | MIT | 2026-10-07 / 2026-10-19 | 1.0M · 三项通过 | 有注意事项：默认用 GitHub issue（`gh issue create`、`gh label create`）；会改写 `CLAUDE.md` 或 `AGENTS.md` | 其他 Matt Pocock skill 需要的 tracker 配置 | — | **不装**。用它附带的模板（MIT）手写同样的配置：本地 `.scratch/` tracker，写进常驻规则和 `docs/agents/` |
| python-resource-management | [wshobson/agents](https://github.com/wshobson/agents)，`0874a27` | 知名作者（个人） | MIT | — / 2026-10-19 | — | 有错误：`open()` 没指定 encoding | 一般 Python 写法 | — | 仅参考 |
| python-error-handling | [wshobson/agents](https://github.com/wshobson/agents)，`0874a27` | 知名作者（个人） | MIT | — / 2026-10-19 | — | 有错误：用了 3.12 才有的语法 | 一般 Python 写法 | — | 仅参考 |
| python-configuration | [wshobson/agents](https://github.com/wshobson/agents)，`0874a27` | 知名作者（个人） | MIT | — / 2026-10-19 | — | 有错误：用 `str` 存密钥，校验失败时可能回显值 | 一般 Python 写法 | — | 仅参考 |
| uv | [astral-sh/claude-code-plugins](https://github.com/astral-sh/claude-code-plugins) `plugins/astral/skills/uv`，`f3ce88a` | 官方组织 | Apache-2.0 | 2026-02-27 / 2026-02-27 | ★313 | 通过，内容准确 | 依赖管理 | — | 仅参考。近 8 个月没有提交，issue 没人回复，不满足维护要求 |
| pydantic | [pydantic/skills](https://github.com/pydantic/skills) `skills/pydantic`，`98f591c` | 官方组织 | MIT | — / 2026-10-16 | ★144 | 通过。第 303 行用了 3.12 才有的语法 | Pydantic 写法 | — | 仅参考，和 fastapi 的 Pydantic 部分重叠 |
| security-threat-model | [openai/skills](https://github.com/openai/skills) `skills/.curated/security-threat-model`，`49f948f` | 官方组织 | 每个 skill 自带 LICENSE.txt | 2026-06-23 / 2026-06-23 | ★28.2K | 通过 | 上线前做威胁建模 | — | 仅参考。仓库 2026-06-22 宣布弃用 |
| acquire-codebase-knowledge | [github/awesome-copilot](https://github.com/github/awesome-copilot) `skills/acquire-codebase-knowledge`，`613efc8` | 官方组织 | MIT | — / 2026-10-21 | ★40.2K | 有注意事项：`scripts/scan.py:607-613` 把 `.env.example` 的内容原样写进输出（任务书要求只看键名）；遍历目录时不排除 `data/` | 生成代码库说明文档 | — | 仅参考。golden-mic-backend-map 会按仓库事实手写同类内容 |
| bug-reproduction-brief | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | — | 通过 | 写复现说明 | — | 仅参考，和 diagnosing-bugs 重叠 |
| ffmpeg-ops | [0xDarkMatter/claude-mods](https://github.com/0xDarkMatter/claude-mods) `skills/ffmpeg-ops`，`fa93328` | 个人 | MIT | 2026-10-07 / 2026-10-14 | ★69 / 23 · 三项通过 | 通过，但有多处错误：Windows 路径转义的两处写法互相矛盾；视频旋转的建议不对；用 sidechaincompress "压低 12 dB"的说法不成立 | FFmpeg 常见陷阱 | — | 仅参考。挑出正确的条目写进 golden-mic-media，注明出处和 MIT |
| ffmpeg-skill | [kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) | 个人 | MIT | — / 2026-10-21 | ★2.0K | 没有逐文件审查。它带 42 个脚本和一个 MCP 服务器 | 让代理直接剪视频，不是写后端代码的指导 | — | 只作线索 |
| volcengine-documentation | [bytedance/agentkit-samples](https://github.com/bytedance/agentkit-samples)，`d07cdc7` | 官方组织 | Apache-2.0 + NOTICE | 2026-04-03 / 2026-10-20 | ★474 | 有注意事项：要向 `docs-api.cn-beijing.volces.com` 发查询，需要 requests 库 | 查火山引擎文档 | — | 仅参考。golden-mic-providers 直接给出官方文档的地址 |
| cyclonedx-spec | [CycloneDX/skills](https://github.com/CycloneDX/skills)，`a6962e9` | 官方组织 | Apache-2.0 | 2026-04-16 | — | 通过 | SBOM（`sbom.cdx.json`） | — | 仅参考。半年没有提交 |
| secure-agent-playbook | [OWASP/secure-agent-playbook](https://github.com/OWASP/secure-agent-playbook)，`65d4efd` | 官方组织 | CC BY 4.0 | — | — | 有注意事项：里面的密钥扫描用 trufflehog，默认会拿找到的密钥去真实服务验证 | 安全检查清单 | — | 仅参考 |

### 2.6 拒绝

| 名称 | 来源 | 发布者 | 许可证 | 最近提交 | 采用度 | 安全审查 | 与金话筒的关联 | 安装方式 | 建议与理由 |
|---|---|---|---|---|---|---|---|---|---|
| supply-chain-risk-auditor | [trailofbits/skills](https://github.com/trailofbits/skills)，`a313174` | 官方组织 | CC BY-SA 4.0 | — / 2026-10-20 | ★7.7K | **不通过**：`scripts/sources.py:525` 运行 `gh auth token`；把整份依赖清单发给 OSV、deps.dev 等服务（`:304`、`:461`） | 依赖风险审查 | — | 拒绝（红线 2） |
| differential-review | 同上 | 官方组织 | CC BY-SA 4.0 | — / 同上 | 同上 | **不通过**：`allowed-tools` 含 Bash；会 `git checkout <baseline>`；依赖 Claude Code 专用的 agent | 审查改动 | — | 拒绝（红线 6） |
| static-analysis（一个插件，含 semgrep、sarif-parsing、codeql 三个 skill，作为整体审查） | 同上 | 官方组织 | CC BY-SA 4.0 | — / 同上 | 同上 | **不通过**：`allowed-tools` 含 Bash；联网下载规则，`npm install -g`；会给目标项目 `pip install` 依赖 | 静态分析 | — | 拒绝 |
| security-review | [getsentry/skills](https://github.com/getsentry/skills)，`f57ada2` | 官方组织 | Apache-2.0；参考资料为 CC BY-SA 4.0 | — / 2026-10-19 | ★1.1K · skills.sh 上 ATH 和 Socket 不通过 | **不通过**：`allowed-tools: Read Grep Glob Bash Task`；正文引用的 7 个文件不存在 | 安全审查 | — | 拒绝。由 find-bugs 加 security-and-hardening 代替 |
| security-best-practices | [openai/skills](https://github.com/openai/skills)，`49f948f` | 官方组织 | LICENSE.txt | 2026-06-23 | ★28.2K | **不通过**：仓库已弃用；多份参考文件让代理去 grep `.env` 和密钥内容 | 按框架做安全评审 | — | 拒绝（红线 2） |
| security-review | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | ★40.2K | **不通过**：默认扫描包括 `.env` 在内的所有文件 | 安全审查 | — | 拒绝 |
| codeql | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | ★40.2K | **不通过**：引导新建 CI 工作流并上传 SARIF。CodeQL CLI 只对开源代码免费，本仓库公开但没有开源许可证 | 静态扫描 | — | 拒绝。改 CI 也超出本任务范围 |
| webapp-testing | [anthropics/skills](https://github.com/anthropics/skills) `skills/webapp-testing`，`dbd4588` | 官方组织 | 目录内 LICENSE.txt | 2025-10 之后没改过 / 2026-10-09 | ★182.5K | **不通过**：`scripts/with_server.py` 用 `shell=True` 启动服务；端口已被占用时直接当作"已就绪"，测试会打到正在运行的实例；Windows 上 `terminate()` 只结束外层 shell，会留下孤儿进程；SKILL.md 要求"先运行脚本、别读源码" | 浏览器端到端测试 | — | 拒绝（红线 3、5）。和已装的 playwright-cli 重叠 |
| python-testing-patterns | [wshobson/agents](https://github.com/wshobson/agents)，`0874a27` | 知名作者（个人） | MIT | — / 2026-10-19 | — | 通过 | — | — | 拒绝。只讲 pytest，本仓库的测试全是 unittest |
| uv-package-manager | 同上 | 知名作者（个人） | MIT | — / 同上 | — | **不通过**：引用不存在的参数，如 `uv lock --no-install`、`uv pip freeze --require-hashes`（已用本机 uv 0.11.11 核对）；示例里有 `uv lock --upgrade` 和 git commit | 依赖管理 | — | 拒绝 |
| python-resilience | 同上 | 知名作者（个人） | MIT | — / 同上 | — | **不通过**：对收费的 POST 请求自动重试 3 次 | 重试与容错 | — | 拒绝。现有接口没有幂等键，超时后不能自动重试 |
| copilot-instructions-blueprint-generator | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | 2026-02-24 / 2026-10-21 | — | 有问题：输出到 `.github/copilot/`，路径不对 | 生成常驻指令初稿 | — | 拒绝。常驻指令按仓库事实手写 |
| ai-ready | [github/awesome-copilot](https://github.com/github/awesome-copilot)（包装 johnpapa/ai-ready） | 官方组织 + 个人 | — | — | — | **不通过**：包装的上游没有固定版本；上游会建 CI、commit、push、开 PR | 同上 | — | 拒绝（红线 8） |
| Superpowers 整套（插件） | [obra/superpowers](https://github.com/obra/superpowers) | 知名作者（个人） | MIT | — / 2026-10-21 | ★302.7K | **不通过**：SessionStart hook 每次会话注入 `<EXTREMELY_IMPORTANT>` 指令；writing-plans、subagent-driven-development 会自动提交；brainstorming 的本地服务器有遥测；建 worktree 时自动装依赖 | 工作流框架 | 插件 | 拒绝。只取 verification-before-completion |
| Matt Pocock 的插件装法 | [mattpocock/skills](https://github.com/mattpocock/skills) | 知名作者（个人） | MIT | — | — | **不通过**：装在用户目录，每天自动更新，一次带进 27 个 skill，其中有读写 `.env` 的 wizard | — | 插件 | 拒绝这种装法，改为逐个 `gh skill install --pin` |
| ffmpeg | [TerminalSkills/skills](https://github.com/TerminalSkills/skills)，`6edcf94` | 组织（社区） | Apache-2.0 | — / 2026-10-20 | ★174 | 有错误：要求 `sudo apt install`；缺 `yuv420p`；concat 的结论不对；`afade` 参数单位写错 | FFmpeg | — | 拒绝 |
| byted-las-*（火山引擎 LAS） | [bytedance/agentkit-samples](https://github.com/bytedance/agentkit-samples)，`d07cdc7` | 官方组织 | Apache-2.0 | — / 2026-10-20 | ★474 | **不通过**：`npm i -g @volcengine/las-cli`；上传到 TOS，按次计费；audio-convert 的 `scripts/env_init.sh:32-41` 不校验哈希就 `pip install` 远程 wheel | 云端数据处理 | — | 拒绝（会计费，也不是写代码的指导） |
| byted-voice-to-text、byted-text-to-speech | 同上 | 官方组织 | Apache-2.0 | — / 同上 | 同上 | **不通过**：`scripts/api_key.py:172-217` 会在你的账号上调用 `CreateAPIKey`，并把密钥明文写入 `.env`；把转写结果当作用户消息（提示注入入口）；`ensure_ffmpeg.py` 自己用 winget、choco、sudo 安装 FFmpeg | 转写、配音 | — | 拒绝（红线 1、2）。其中的接口信息只作线索，以官方文档为准 |
| byted-mediakit-voiceover-editing | 同上 | 官方组织 | Apache-2.0 | — / 同上 | 同上 | **不通过**：`serve_review_page.py:402-412` 以 `Access-Control-Allow-Origin: *` 暴露任意绝对路径的文件 | 配音剪辑 | — | 拒绝。其中"删词时从前一个词的结尾切到后一个词的开头"的规则可以参考 |
| volcengine-knowledge-search | [volcengine/volcengine-agent-toolkit](https://github.com/volcengine/volcengine-agent-toolkit)，`04a90fe` | 官方组织 | MIT | — | — | **不通过**：要装 `ve` 命令行；所在插件的 hooks 会上报遥测 | 检索火山文档 | — | 拒绝 |
| 方舟文档类 skill（ark-docs-assistant 等） | 火山方舟官方文档站（没有固定的仓库地址，未查到） | 官方 | — | — | — | 无法核实：用 `npx skills@latest add` 从 TOS 存储桶安装，没有版本号 | 查方舟文档 | — | 拒绝 |
| 社区豆包语音 skill（如 vahnxu/doubao-asr） | [vahnxu/doubao-asr](https://github.com/vahnxu/doubao-asr)（个人仓库） | 个人 | Apache-2.0 | — / 2026-09-27 | ★2 | 没有审查 | 转写 | — | 拒绝。个人仓库，几乎没人用，而且会调用收费接口 |
| eval-driven-dev | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | — | **不通过**：强制调用真实 LLM | — | — | 拒绝 |
| webapp-testing（awesome-copilot 版） | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | — | **不通过**：自动安装 Playwright | — | — | 拒绝 |
| pytest-coverage | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | — | **不通过**：只支持 pytest | — | — | 拒绝 |
| transloadit-media-processing | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | — | **不通过**：把文件上传到第三方云 | — | — | 拒绝 |
| screen-recording | [github/awesome-copilot](https://github.com/github/awesome-copilot) | 官方组织 | MIT | — / 2026-10-21 | — | **不通过**：要求 `pip install` | — | — | 拒绝 |

### 2.7 暂不安装（与当前技术栈不符）

| 名称 | 来源 | 发布者 | 许可证 | 最近提交 | 采用度 | 安全审查 | 与金话筒的关联 | 安装方式 | 建议与理由 |
|---|---|---|---|---|---|---|---|---|---|
| Supabase、Prisma、Redis、Stripe、Better Auth、Cloudflare、Azure 等 | 各厂商官方仓库（任务书 3.3 最后一行，没有逐个检索） | 官方组织 | — | — | — | 未审查 | 与当前技术栈不符（本项目没有数据库、支付或云平台依赖） | — | 暂不安装。以后做账号或数据库时再评估 |

另外：**Kimi 没有官方的 API 开发 skill**。MoonshotAI 的 kimi-cli、kimi-code 里的 skill 和本项目无关。golden-mic-providers 会直接引用 Kimi 和火山方舟的官方文档。

## 3. 工作流框架：选 Matt Pocock

任务书要求"工作流框架只能有一个"。选 Matt Pocock 的理由：

1. **粒度小，可以只装要用的。** `grill-with-docs`、`to-spec`、`to-tickets`、`implement` 在上游就设为只能手动调用，不会自己跳出来接管对话。
2. **审查面小。** 没有 hooks，没有 MCP 服务器。只有 diagnosing-bugs 带一个 bash 模板，而且只是模板。
3. **和任务书第 7 节的用法一一对应**：`/grill-with-docs` → `/to-spec` → `/to-tickets` → `/implement`（内含 tdd、code-review）。
4. **tracker 可以配成本地 Markdown。** 规格和工单写进 `.scratch/<功能名>/`，不往 GitHub 发 issue，也不需要登录 gh。
5. **维护活跃。** 2026-10-19 有推送，最近 30 天关闭了 8 个 issue。

不选 Superpowers 的原因：它的 SessionStart hook 每次会话都会注入强制指令；有几个 skill 会自动提交；本地服务器带遥测。这些和红线 3、5、8 以及"终端命令逐条人工批准"冲突。两个框架同时装，还会互相争夺流程。Superpowers 里只有 `verification-before-completion` 能独立使用，而且和红线不冲突，所以单独安装它。

Matt Pocock 的 skill 需要一份项目配置，阶段二按它的 MIT 模板手写，不运行 setup：

- **tracker 用本地 Markdown。** 规格放在 `.scratch/<功能名>/spec.md`，工单放在 `.scratch/<功能名>/issues/NN-<名称>.md`。默认把 `.scratch/` 加进 `.gitignore`。如果你希望规格和工单入库，告诉我。
- **领域文档。** `GLOSSARY.md` 和 `docs/adr/` 放在仓库根，用到时才创建，写之前先问你。
- **写在哪里。** 以上配置写进 `.github/copilot-instructions.md` 的"Agent skills"一节，以及 `docs/agents/issue-tracker.md`、`docs/agents/domain.md`。

## 4. 安装方式和位置

- **目录分工。**
  - 第三方 skill 装到 `.agents/skills/`。这是 gh 给 Copilot 装项目级 skill 的默认目录（`gh skill install --help` 原文），已装的 3 个也在这里，VS Code 会读取它。
  - 项目自建的 skill 放在 `.github/skills/`。
  - 这样外来的和自己维护的分开，更新和许可证核对都更简单。
  - 任务书写的是"装进 `.github/skills/`"。如果你希望全部放在一起，可以加 `--dir .github/skills`；以后 `gh skill update`、`gh skill list` 也都要加 `--dir`。
- **安装途径**（二选一，见第 8 节问题 2）：
  - **用 gh（推荐）。** 公司出口 IP 的匿名 GitHub API 额度总是被别人用完，gh 会返回 403，所以需要你先在终端运行一次 `gh auth login`。装好后 frontmatter 里带来源元数据，以后可以用 gh 检查更新。运行时设 `GH_TELEMETRY=false`。
  - **手动复制。** 从审查过的那个提交复制目录。内容完全相同，只是没有 gh 的来源元数据，来源记在 `docs/AGENT_SKILLS.md`。
- **不用 `uvx library-skills`。** 它要从 PyPI 下载一个工具并执行，`--copy` 得到的结果和从 `.venv` 手动复制一样。
- **不用插件。** 不装任何 hook、MCP 服务器或插件。
- **装后对上游原文只做两类改动，都记入清单：**
  1. 给 find-bugs、diagnosing-bugs 设 `disable-model-invocation: true`；如果装可选的两个，也一并设上。
  2. 删掉 playwright-cli 的 `allowed-tools`（需要你同意）。

  其余文件和上游逐字节一致。
- **许可证。** 本仓库是公开的。每个第三方 skill 目录都要放上它的 LICENSE 文件，包括已装的 3 个。

## 5. 和分支现有内容的关系

- **已提交、未推送。** 提交 `6f4f3be` 和 `ecf2312` 包括：3 个第三方 skill、8 个 gm-* 项目 skill、常驻指令（31 行）、`docs/AGENT_SKILLS.md`、`scripts/validate_agent_skills.py`。当前校验结果：0 个错误，2 个已知的上游警告。
- **工作区里另一个会话暂存的目录整理**（24 个文件，把根目录的报告移进了 `docs/reports/`）：不归本任务管，我不会去动。提交时只提交本任务的文件。其中 `docs/AGENT_SKILLS.md`、`gm-quality-benchmark`、`gm-ux-copy` 也有它暂存的链接修改。
- **必建 skill 与现有 gm-* 的对应关系（建议合并）：**

  | 任务书必建 | 现有 | 建议 |
  |---|---|---|
  | golden-mic-verify | gm-verify | 改名，并按 5.2 扩写：`pip check`、`backend.preflight`、隔离测试 |
  | golden-mic-backend-map | gm-pipeline-map | 改名，并扩写：任务生命周期、重做规则、限额与常量、保留时长、"需求 → 代码位置"索引 |
  | golden-mic-media | gm-ffmpeg-render | 改名，并扩写：响度、切点吸附、跳切遮盖、定格、人名条、Windows 转义 |
  | golden-mic-api-change | — | 新建 |
  | golden-mic-providers | — | 新建 |

  另外 5 个 gm-*（gm-ai-labeling、gm-ux-copy、gm-news-integrity、gm-quality-benchmark、gm-web-interface-guidelines）保持原名。其中两个有另一个会话暂存的改动，改名会和它冲突。
- **常驻指令。** 按 5.4 重写，不超过 60 行。保留现有的"第三方 skill 的项目覆盖规则"，并补上本报告新增的覆盖项。
- **校验脚本。** 加两条检查：`golden-mic-` 前缀也算项目 skill；`allowed-tools` 里出现 shell、bash 或终端时报错。
- **数量。** 上个会话在 `docs/AGENT_SKILLS.md` 里定了"第三方加自建不超过 22 个"，这次的任务书没有这条。按本报告建议装完一共 28 个（第三方 18 个、项目 10 个），装可选的 2 个就是 30 个。

## 6. 和任务书、官方文档或仓库不一致的地方

1. **gh 的默认安装目录。** `gh skill install` 给 Copilot 装项目级 skill，默认目录是 `.agents/skills/`，不是 `.github/skills/`（gh 2.102.0 的帮助原文）。`gh skills` 是 `gh skill` 的别名，两种写法都对。
2. **VS Code 文档。** [Agent Skills 文档](https://code.visualstudio.com/docs/copilot/customization/agent-skills)页面显示 2026-10-14 更新：
   - `/skills` 和 `/create-skill` 只在会话目标为 Local 时可用；其他目标要从"Open Customizations"进入。
   - `chat.agentSkillsLocations` 已弃用，而且 Copilot 目标根本不读它。
   - 文档没有提到 `allowed-tools`、`license`、`compatibility`、`metadata` 这几个字段。
3. **GitHub 文档对 `allowed-tools` 的说明。** [GitHub 文档](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-skills)明确说，Copilot 对 `allowed-tools` 里的工具不再要求确认。所以第三方 skill 里预授权 Bash 是实际风险，已装的 playwright-cli 就有。
4. **openai/skills 已弃用。** 2026-06-22 起弃用，README 引导用户改用 openai/plugins。候选表里的两个 openai skill 已不再维护。
5. **Trail of Bits 的变化。**
   - insecure-defaults 自 2026-08-05 起不再是 skill。
   - static-analysis 是包含 codeql、semgrep、sarif-parsing 的插件。
   - 许可证 CC BY-SA 4.0 只放在仓库根。
6. **FastAPI 的版本。** FastAPI 0.139.2 随包自带这个 skill，已在本地 `.venv` 核实。master 上的新版本内容不同，所以固定在 0.139.2。
7. **Matt Pocock 的版本与改名。** 不加 `--pin` 时，gh 会装最新标签 v1.3.1，它比 HEAD 落后 47 个提交。另外 `to-prd` 已改名为 `to-spec`，`CONTEXT.md` 改为 `GLOSSARY.md`。
8. **仓库事实。**
   - **接口已存在。** §1.4 里标为"需新增"的接口代码里已经有了：`POST /api/tasks/{id}/pretranscribe` 和 `POST /api/tasks/{id}/align`（`backend/drafts.py:1505-1528`）；版本、应用修改、录音（`backend/workbench.py:1318-1458`）。后续是在现有基础上补全。自建 skill 以代码为准，和交接文档 §12 不一致的地方列为待确认。
   - **测试框架。** 测试全部基于 unittest：`tests/` 下 95 个文件用 unittest，0 个用 pytest。受保护入口是 `tests/run_v2_validation.py`。
   - **provider 不止 4 个。** 证据包里还记录了火山 LLM、Seedream 和 Seedance 生成、`providers/local_speech.py`。阶段三写 golden-mic-providers 时逐个核实。
   - **仓库公开且没有 LICENSE。** `mizhou0817/golden_mic` 是公开仓库，根目录没有 LICENSE。把第三方 skill 提交进来就是再分发：MIT 要附版权声明和许可文本，Apache-2.0 要附 LICENSE。
9. **Snyk ToxicSkills 的数字。** "36.82%（1,467/3,984）至少有一个安全问题"属实；"约九分之一含密钥"对应的是 ClawHub 上的 10.9%。
10. **officialskills.sh。** 它是社区（VoltAgent）维护的目录，不做安全审计。

## 7. 检索范围和方法

- **检索范围。**
  - A 类：VS Code 和 GitHub 官方文档、agentskills.io 规范、github/awesome-copilot、officialskills.sh。
  - B 类：anthropics、openai、fastapi（含 tiangolo/library-skills）、getsentry、trailofbits、microsoft、vercel-labs。
  - C 类：obra、mattpocock、wshobson、addyosmani。
  - D 类：skills.sh 排行榜和社区仓库。
  - 另外用中英文关键词检索了 FFmpeg、火山引擎 / 豆包语音、Kimi、SBOM、OWASP、pytest、uv、Pydantic。
- **审查方法。**
  - 候选仓库全部 `git clone` 到系统临时目录，逐个文件阅读 SKILL.md、`scripts/`、`references/`。
  - 没有执行任何第三方脚本。
- **分工。**
  - 前一个会话：2026-10-21 上午完成逐文件审查。
  - 本会话（同一天下午）：
    1. 确认 19 个仓库当前的 HEAD 和审查时的提交一致，审查之后没有新提交；
    2. 对照原文，逐条复核决定"装 / 拒"的关键结论；
    3. 用 GitHub API 补齐星标、许可证、归档状态、最近推送和 issue 回应情况；
    4. 抽查 skills.sh 上的安装量和审计结果；
    5. 补充检索 FFmpeg、火山引擎、Kimi 的官方 skill。
- **受到的限制。**
  - gh 没有登录，`gh skill search` 和 `gh skill preview` 被匿名额度挡住（403）。
  - 网页抓取工具打不开 github.com。
  - 所以改用 git clone、匿名的 GitHub API（经另一条出口）和 skills.sh 网页。
- **证据。** 逐文件审查的笔记（含行号）原放在系统临时目录 `%TEMP%\gm-skills-review-20261021\selection-evidence.md`，该目录后来被清空，笔记没有保留（2026-10-22 核对）。结论已并入 `docs/AGENT_SKILLS.md`；表中带 `文件:行` 的审查结论，需要时用固定提交重新克隆复核。

## 8. 需要你确认

1. **安装清单。** 按 2.1 装 15 个吗？可选的 2 个装不装？22 个的上限还要不要？
2. **安装途径。** 你先运行 `gh auth login` 再用 gh 安装，还是不登录、手动复制？
3. **重叠的 skill。** 按第 5 节合并 3 个重叠的 gm-* 吗？
4. **playwright-cli。** 删掉 `allowed-tools` 这一行、卸载它，还是保持原样？

以下默认做法，不同意请告诉我：

- 第三方 skill 装在 `.agents/skills/`。
- `.scratch/` 加进 `.gitignore`。
- 每个第三方 skill 目录都补上 LICENSE。
- 提交和推送之前，会再单独问你。

### 用户答复（2026-10-21 起，在对话中选择）

1. 安装清单：选“按建议装 15 个（推荐）”。可选的 2 个没有装。
2. 安装途径：选“我登录 gh，用 gh 安装（推荐）”。
3. 重叠的 skill：选“合并重叠的 3 个（推荐）”。
4. playwright-cli：选“删掉 allowed-tools 这一行（推荐）”。2026-10-22 全面核对后，它又被设为只能手动调用，见 [AGENT_SKILLS.md](../../AGENT_SKILLS.md) 第 3 节。

任务书标为“安装”、最后没有装的候选（Trail of Bits 的 4 个、webapp-testing、acquire-codebase-knowledge、security-best-practices、security-threat-model 等），理由见 2.5 和 2.6。用户按“装 15 个”的清单确认，没有就这些候选单独作答。
