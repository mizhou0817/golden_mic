# Issue tracker：本地 Markdown

给 Matt Pocock 的工程类 skill（to-spec、to-tickets、implement、code-review 等）用的 tracker 配置。本仓库的规格和工单都写成本地 Markdown 文件：不在 GitHub 上建 issue，也不运行 `gh issue`、`gh label`。

## 约定

- 每个功能一个目录：`.scratch/<feature-slug>/`。slug 用英文 kebab-case，例如 `align-endpoint`。
- 规格写在 `.scratch/<feature-slug>/spec.md`。
- 工单每个一个文件：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`。从 `01` 开始按依赖顺序编号，先写被依赖的工单；不要把多个工单合进一个文件。
- 文件开头写一行 `Status:`，取值用 `ready-for-agent`、`ready-for-human`、`needs-info`、`needs-triage`、`wontfix` 之一。
- 讨论记录追加在文件末尾的 `## Comments` 标题下。
- `.scratch/` 被 git 忽略，只留在本机。需要长期保留的结论，另写进 `docs/` 或 ADR（见 [domain.md](domain.md)）。

## skill 要求 "publish to the issue tracker" 时

在 `.scratch/<feature-slug>/` 下新建文件，目录不存在就先创建。不要发到 GitHub。

## skill 要求 "fetch the relevant ticket" 时

读取用户给出的路径或编号对应的文件。

## 来源

改写自 [mattpocock/skills](https://github.com/mattpocock/skills/blob/5beb35b040e3de97e54fa9e386d7e42d15c295c2/skills/engineering/setup-matt-pocock-skills/issue-tracker-local.md) 的 `issue-tracker-local.md` 和 `triage-labels.md`（提交 `5beb35b`）。MIT 许可，Copyright (c) 2026 Matt Pocock。没有安装 wayfinder 和 triage，所以删去了相关的段落。
