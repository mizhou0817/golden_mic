# 金话筒开发规则

- 产品：面向零剪辑经验记者的中文新闻视频单机 Web 应用（V2）。前端 React 18 + TypeScript + Vite 8 + Tailwind 3，图标是 `frontend/src/components/ui/Icon.tsx` 里的手写 SVG（lucide-react 已声明但没有使用）；后端 Python 3.11/3.12 + FastAPI + FFmpeg。
- Windows 优先：命令用 `npm.cmd` 和 `& .\.venv\Scripts\python.exe`。
- 当前行为以 README.md、docs/ 下的现行指南、`backend/mode_rules.json` 和代码为准；带日期的报告只作背景参考。
- 不恢复已下线的 Studio 时间线、课堂、教师审批、独立云作业入口；legacy 代码只按兼容范围维护。

## 新闻真实性（细节见 gm-news-integrity）
- 不替记者写稿、改稿。分句时 LLM 只能插入断句符，不能改原文的任何一个字。
- 原声只能按真实词边界连续剪短，保留原始绝对时间和 ASR 证据；原话文字不能改。
- AI 示意画面默认关闭，事实性内容禁止生成。必须烧录"AI生成示意画面"，不能用关字幕或改配置绕过；会丢失披露的格式（MP3/SRT）必须拒绝导出。
- 发布前检查：错误不可豁免（`generated_media` 除外）；警告逐项确认，并绑定当前版本；阈值只写在 `mode_rules.json`。
- 和上面红线冲突的现状，待维护者决定，TODO(待确认)：A/B 模式的"AI 写一稿 / 润色"；"仍然导出（带未通过的检查）"能跳过错误级检查。不要扩大这两处的范围，也不要新增绕过路径。

## 费用、隐私与运行安全
- 选好文件后，上传和预转写就可能调用收费 ASR；稿件、音频、关键帧可能发给第三方。没有用户明确同意，不上传素材、不触发收费调用。
- 不读取、打印、修改 `.env` 和任何凭证；不填假 Key 伪造就绪；不靠关校验、改额度、删账本来绕过限制。
- 服务单进程、单 worker、只监听 127.0.0.1，前后端同源；收费作业期间不用 `--reload`，也不重启。

## 开发与测试
- 验证只走受保护入口（见 gm-verify）：后端用 `tests/run_v2_validation.py`，不直接跑 pytest 或 `unittest discover`；`npm run test:e2e` 不是 V2 验收。没运行的项写"未运行"和原因。
- 用户没批准时，不加新依赖，不改锁文件和 CI；只在用户要求时 commit，不 push、不 merge。
- 界面设计已定型：审查和打磨时不引入新的视觉风格、组件库、动画库或图标库。文案沿用现有术语（见 gm-ux-copy）。

## 第三方 skill 的项目覆盖规则（和 skill 原文冲突时以本节为准）
- 测试命令一律换成 gm-verify 里的受保护入口。不删除已有测试，包括 codebase-design 建议的"删掉旧单测"。
- skill 建议的全局安装或下载（`npm -g`、`pip install`、`npx lighthouse`、`uvx` 等）都要先经用户批准。
- 浏览器自检：在 frontend 目录用 `npx --no-install playwright cli` 加 `--browser=msedge`。只打开本地开发页、截图；不选文件、不上传，也不用 `gh pr` 上传附件。
- 复现和评测循环只用合成素材，不反复触发收费调用；break-ui 的测试开关只能留在开发环境。
- 写入 GLOSSARY.md、`docs/adr/` 前，先征得用户同意。
- 规则优先级：用户的明确指示 > 本文件 > 项目 skill（gm-*）> 第三方 skill。
