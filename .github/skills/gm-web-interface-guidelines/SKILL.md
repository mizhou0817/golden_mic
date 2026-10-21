---
name: gm-web-interface-guidelines
description: 按固定版本的 Vercel Web Interface Guidelines（MIT）审查金话筒前端代码（UI review / 界面规范审查），覆盖可访问性、焦点、表单、动画、排版、内容处理、图片、性能、导航与状态、触控、布局、国际化、交互状态、文案等规则，输出"文件:行"问题清单。用户说"界面规范审查""review my UI""检查一下这个组件""前端规范"时使用。只审查、不改代码；不用于重做视觉风格（界面已定型），也不联网拉取最新规则。
argument-hint: "<文件或 glob，例如 frontend/src/components/ResultWorkbench.tsx>"
---

# 界面规范审查（固定版 Web Interface Guidelines）

## 来源与许可
- 规则原文：[references/vercel-web-interface-guidelines/command.md](references/vercel-web-interface-guidelines/command.md)。
  - 逐字节复制自 https://github.com/vercel-labs/web-interface-guidelines 的提交 `434b7f91364665f2f733b310ec54809bf8f37937`。该文件最后一次修改是提交 `4ecfb9fb8d1d3b7009674869b3aaee2f904042e1`（2026-10-05）。
  - SHA-256：`d246b026f4f29b5823a9cc857f9edf3d2507002e055e32040cadeaf3b38e0234`。
- 许可证：MIT，Copyright (c) 2025 Vercel Labs。全文见 [LICENSE](references/vercel-web-interface-guidelines/LICENSE)。
- 本 skill 用来替代 vercel-labs/agent-skills 里的 `web-design-guidelines`，原因有两条：那个 skill 每次运行都从 `main` 分支现拉规则（版本不固定，相当于执行远程指令）；它所在的仓库没有 LICENSE 文件。
- 升级方法：审查新版 `command.md` 后整个替换，并更新上面的提交号和 SHA-256。不要在运行时联网获取规则。

## 适用 / 不适用
- 适用：审查组件或页面的可访问性、焦点、表单、交互状态、长内容处理、性能写法，以及界面改动的评审。
- 不适用：
  - 视觉重设计或换风格。
  - 中文文案用词：见 gm-ux-copy。
  - 新闻真实性和发布门禁：见 gm-news-integrity。
  - 截图类检查：用 playwright-cli；构造极端数据：用 break-ui；WCAG 2.2 审计：用 accessibility。

## 项目覆盖规则（与原文冲突时以本节为准）
- 只读审查，先出问题清单，用户批准后才改代码。
- 不引入新的视觉风格、组件库、动画库或图标库。图标是 `frontend/src/components/ui/Icon.tsx` 里的手写 SVG；`package.json` 虽然声明了 lucide-react，但代码和测试都不使用它。
- 项目是 Vite 单页应用，没有服务端渲染。原文的"Hydration Safety"和 Next.js 相关条目不适用；`<Link>` 按普通导航链接理解。
- 中文排版和用词按 gm-ux-copy：全角标点；中英文、中文与数字之间加空格；"进行中"用单个"…"。原文里的英文弯引号条目，对中文文案不适用。
- 动画：工具型界面的动效要克制，必须尊重 `prefers-reduced-motion`；不新增装饰性动画。
- 只在已有主题能力的范围内检查"Dark Mode & Theming"，不借此新增主题。
- 原文中的 `$ARGUMENTS` 指用户给出的文件或范围。
- 必须一直可见的信息（上传错误、原话门槛、隐私与费用提醒、发布前检查里的错误项），不能为了"简洁"被收起或隐藏。

## 步骤
1. 确认审查范围（文件或 glob）。没有指定就先问用户。
2. 读规则原文，再读本节覆盖规则。
3. 逐个文件对照规则检查，记录行号。
4. 剔除不适用的条目，按严重程度排序输出。

## 输出格式
- 每条一行：`路径:行 — 规则类别 — 问题 — 建议`，按"影响使用 > 可访问性 > 一致性 > 细节"排序。
- 末尾附"不适用的条目"及原因，以及建议配合使用的 skill（accessibility、break-ui、playwright-cli）。

## 来源
- 规则原文和许可证见上文。
- 项目约束：[docs/UI_SIMPLIFICATION_20261014.md](../../../docs/UI_SIMPLIFICATION_20261014.md)、[frontend/package.json](../../../frontend/package.json)。
