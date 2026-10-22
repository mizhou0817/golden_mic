# 领域文档（Domain docs）

给 Matt Pocock 的工程类 skill 读的配置：探索代码时，按下面的方式使用本仓库的领域文档。布局是 single-context，只有一份术语表。

## 探索代码前先读

- 仓库根的 `GLOSSARY.md`：领域术语表。
- `docs/adr/`：和这次改动相关的架构决策记录（ADR）。

这两处文件还不存在时，**照常继续，不要提醒，也不要主动创建**。由 `/domain-modeling` 在确有术语或决策需要记录时创建。本项目要求：**写入或修改 `GLOSSARY.md`、`docs/adr/` 之前，先征得用户同意。**

术语表建立之前，可以先参考现有资料里的说法：

- 界面术语，如"稿句""原声句""发布前检查""应用修改"：见 [gm-ux-copy](../../.github/skills/gm-ux-copy/SKILL.md)。
- 模式、阶段、检查码的代号：见 [`backend/mode_rules.json`](../../backend/mode_rules.json) 和 [golden-mic-backend-map](../../.github/skills/golden-mic-backend-map/SKILL.md)。

## 文件结构

```
/
├── GLOSSARY.md
├── docs/adr/
│   └── 0001-<决策名>.md
└── backend/ …
```

## 用术语表里的说法

输出里提到领域概念时（工单标题、重构建议、假设、测试名），用 `GLOSSARY.md` 里定义的词，不要换成术语表明确不用的同义词。需要的概念术语表里还没有，说明要么是在发明项目不用的说法（重新考虑），要么是真缺口（记下来，交给 `/domain-modeling`）。

## 和 ADR 冲突时要指出

输出和已有 ADR 矛盾时，明确写出来，不要悄悄推翻。例如：

> _与 ADR-0007 冲突，但值得重新讨论，因为……_

## 来源

改写自 [mattpocock/skills](https://github.com/mattpocock/skills/blob/5beb35b040e3de97e54fa9e386d7e42d15c295c2/skills/engineering/setup-matt-pocock-skills/domain.md) 的 `domain.md`（提交 `5beb35b`）。MIT 许可，Copyright (c) 2026 Matt Pocock。
