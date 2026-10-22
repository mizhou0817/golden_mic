---
name: golden-mic-media
description: 金话筒媒体处理约定（FFmpeg / ffprobe）。涵盖依赖与预检；先用 ffprobe 探测再处理；响度统一到 −20 LUFS；人声段音乐压低 18 dB；原声切点吸附到词边界和不超过 300 ms 的静音，片头留 120 ms、片尾留 200 ms，相邻原声间隔 150 ms；跳切遮盖 broll/zoom/hard；图片定格 3 秒；字幕、人名条和 AI 标注烧录；导出规格；子进程超时和取消；Windows 与 Linux 的路径转义差异。修改或排查 rendering.py、graphics.py、tts_pipeline.py、audio_filters.py、music.py、media.py、media_input.py、studio_render.py、production_modes.py 的切点和跳切代码，或用户说"渲染""响度""切点""跳切""字幕样式""人名条""配乐""导出参数""FFmpeg 命令""子进程超时"时使用。不要用于安装或升级 FFmpeg，也不要用真实素材制作成片。关键词：渲染 FFmpeg、ffprobe、响度 loudnorm LUFS、闪避 ducking、切点 cut、跳切 jump cut、字幕 ASS、人名条、导出 export。
---

# 金话筒媒体处理约定

修改渲染、音频、切点和导出代码时，守住现有约定：参数、新闻真实性、AI 标注、子进程安全，并且只用合成素材验证。下面写的是**代码现状**，和文档或旧报告冲突时以代码为准。

## 何时使用 / 不要使用

- 使用：
  - 调整滤镜、编码、响度、切点、跳切、字幕样式、导出格式；
  - 排查渲染失败、超时、音画不同步；
  - 审查 FFmpeg 相关改动。
- 不要使用：
  - 安装或升级 FFmpeg，下载模型或工具；
  - 用真实素材出片，或调用付费服务；
  - 查检查码和导出门禁的规则（见 gm-news-integrity），或 AI 标识的合规设计（见 gm-ai-labeling）。

## 硬性规则

- **只用参数列表调用 FFmpeg 和 ffprobe。** 异步用 `asyncio.create_subprocess_exec`，同步用 `subprocess.run([...])`。不拼 shell 字符串，不用 `shell=True`、`os.system`、`os.popen`。
- **每个子进程都要有超时。**
  - 媒体命令用 `MEDIA_COMMAND_TIMEOUT_SECONDS`，默认 7200 秒；生产环境不能超过 `SHUTDOWN_GRACE_SECONDS`；
  - 质检 600 秒；
  - 能力探测和上传探测各 15 秒。
- **先探测再处理。** 上传后先用 `uploads._probe` 探测，之后才做波形分析和 ASR。探测有协议和格式白名单，`probesize` 8 MiB，`max_alloc` 128 MiB，超时 15 秒（`uploads.py:889-947`）。开始制作时、阶段 1、录音、成片也都会先探测。**不要扩大 `media.py` 和 `uploads.py` 的协议、格式白名单。**
- **滤镜图里的路径必须转义**，并且用对函数（规则见下文"Windows 与 Linux"）：
  - 成片的字幕和图文：`rendering._escape_filter_path`；
  - concat 列表：`rendering._escape_concat_path`；
  - 导出：`studio_render.filter_path`。
- **"AI生成示意画面"标注**单独写在 `graphics.ass` 里烧录，不受字幕开关影响，导出时也总会烧录。任何改动都不能让标注被关掉、裁掉或丢失；会丢失标注的格式（MP3、SRT）必须拒绝导出。
- **原声不改字。** 只能按真实的词边界剪短；segment 精度的原声不能做词级裁剪（`production_modes.py:770-771`）。
- **不能删掉能力预检，也不能用假工具绕过。** 预检检查的能力是：编码器 libx264、aac，滤镜 ass、loudnorm、ebur128、silencedetect、perspective（`readiness._check_media_capabilities`）。代码还用到 afftdn、sidechaincompress、`volume` 的 eval、lut3d，这几个预检没有覆盖：新增依赖它们的功能时，要考虑是否加进预检（改预检会影响启动，先问用户）。
- **只用合成素材和受保护测试验证**，见 golden-mic-verify。

## 核心数字（2026-10-21 核实）

| 项目 | 现行做法 | 出处 |
|---|---|---|
| 响度 | 旁白、原声、录音的每个音频单元都做两遍 `loudnorm`：I=−20、LRA=5、TP=−3.5；太短或太轻、无法测量时保持原音量。整条旁白轨拼接后再统一到 −20。有配乐时，混音后再做一次 −20。A 模式的环境声先压到 −24 / 5 / −6，合轨后整体 −20。质检目标是 −20，真峰值上限 −3.0 | `tts_pipeline.py:1543-1581`、`mode_pipeline.py:1380`、`mode_pipeline.py:1657`、`mode_pipeline.py:1747-1771`、`rendering.py:1311-1312` |
| 人声段压低音乐 | **仅 V2**：`audio_filters.speech_duck_filter`，在所有语音单元的时间区间内把音乐压低 18 dB，前后各有 0.3 秒线性过渡。配乐本身 −32 LUFS（LRA 6、TP −6），开头 2 秒淡入、结尾 3 秒淡出。旧流程用 `sidechaincompress`（ratio = `MUSIC_DUCK_RATIO` 8），不是固定的 18 dB | `audio_filters.py:34-56`、`mode_pipeline.py:1585-1586`、`music.py:103-108`、`rendering.py:1296` |
| 切点吸附 | `production_modes.refine_cut`：先扩展到所有相交的词；头尾留白不越过相邻的词；再吸附到调用方给出的、不和词重叠的静音，距离不超过 `cut_snap` 0.3 秒。只对词级精度的候选生效。静音区间来自上传时的波形分析 | `production_modes.py:748-793`、`production_modes.py:962-976`、`uploads.py:682`、`uploads.py:1089` |
| 头尾留白 | 片头 `cut_head` 0.12 秒，片尾 `cut_tail` 0.2 秒；碰到相邻的词或素材边界时缩短 | `mode_rules.json` 的 `limits`、`production_modes.py:779-780` |
| 相邻原声间隔 | `quote_gap` 0.15 秒；同一个连续组内部为 0。V2 中原声转旁白也是 0.15 秒；旁白之间句末 0.4 秒、分句 0.25 秒 | `mode_pipeline.py:1359-1363`、`tts_pipeline.py:1102-1112` |
| 跳切判定 | `production_modes.calculate_jumpcuts` 只比较**相邻的两条原声**，中间隔着旁白就不算相邻。同一个上传文件、间隔在 0–0.5 秒（`jump_continuity`）内算连续；其余情况各算一处跳切：不同文件、间隔超过 0.5 秒、重叠或倒退 | `production_modes.py:1276-1304` |
| 跳切遮盖 | `jump_cut_cover` 默认 `broll`。`broll` 需要后一行明确给出 `cover_shot`，否则降级为 `zoom` 并标记 downgraded。渲染时（`mode_pipeline.build_mode_edl`）：<br>• `broll`：取一个没用过的源空镜，时长 min(2.0 秒, 0.4 × 句长)（V2），旧流程为 1.5 秒；<br>• `zoom`：6 帧推近到 1.06（V2），旧流程 1.08；<br>• 句子短于 6 帧时降级为 `hard`。<br>遮盖方式不是 `broll` 时发 `JUMP_CUT_UNCOVERED` | `production_modes.py:1302-1303`、`production_modes.py:1389-1393`、`mode_pipeline.py:1416-1558`、`models.py:183` |
| 图片定格 | 照片转成 3 秒的静态片段（`PHOTO_DURATION` 3.0） | `uploads.py:76`、`uploads.py:899-907`、`media_input.py:41` |
| 字幕 | ASS，画布 1920×1080，字体 Noto Sans SC（随包放在 `backend/assets/fonts/`，启动时校验散列和 OFL 许可）。V2 字号：标准 42、大字 54；每行不超过 18 字（大字不超过 14 字）。原声字幕用实际的 ASR 文本。用 `ass=filename=…:fontsdir=…` 烧录；缺 Noto Sans SC 直接报错 | `graphics.py:173-283`、`rendering.py:1627-1695`、`subtitles.py:26` |
| 人名条 | `graphics.generate_mode_graphics` 写入 `graphics.ass`：同一人首次出现显示 2.5 秒，间隔 60 秒以上再次显示；没有名字时显示"受访者" | `graphics.py:334-397`、`rendering.py:1395-1398`、`mode_rules.json` 的 `lower_third_*` |
| AI 标注 | 样式 GfxDisclosure：右上角，字号 30，带底框；只在 AI 生成镜头的时间段内显示 | `graphics.py:125-140`、`graphics.py:394` |
| 样片水印 | **后端没有实现**。只有前端用 CSS 叠加（`ResultWorkbench.tsx:890`） | 见"待确认" |

更多约定（人声增强、推近、颜色统一、淡入淡出、成片编码、导出格式、子进程取消）见 [references/render-details.md](references/render-details.md)。

## Windows 与 Linux

- **两个转义函数规则不同，不能混用：**
  - `rendering._escape_filter_path`：先 resolve，再 `as_posix()`，然后对 `\ : ' , [ ] ;` 加反斜杠（`rendering.py:1698-1702`）。
  - `studio_render.filter_path`：把 `\` 换成 `/`，`:` 转成 `\:`，`'` 转成 `\'\''`，对应两级解析（`studio_render.py:955-959`）。
- **Windows 盘符的冒号必须转义**，例如 `C\:/…`。也可以把子进程的工作目录设为作业目录，用相对文件名，避开转义。
- **长路径**：Windows 用 `\\?\` 前缀（`revisions.py:50-61`）。
- **取消子进程**：Linux 和 macOS 用独立会话加 `killpg`，先 SIGTERM，10 秒后 SIGKILL；**Windows 只结束直接子进程，不清理进程树**（`media.py:396`、`media.py:468-490`）。
- **空输出**：Windows 用 `NUL`，其他平台用 `/dev/null`（`quality.py:738`、`tts_pipeline.py:1459`）。
- **请求路径**：含 Windows 非法字符时直接返回 404（`main.py:1286-1290`）。

## 步骤（改媒体相关代码时）

1. 按上表找到对应代码，读代码确认现状，不凭文档猜。
2. 改动时保持这些不变：
   - 参数列表调用、超时、白名单；
   - 路径转义；
   - AI 标注的烧录；
   - 原声不改字。
3. 动了数值，就同步 `mode_rules.json`。前端也读这个文件，改完要重新构建前端。
4. 用合成素材跑受保护测试（golden-mic-verify 的命令 B），重点看这些模块：test_m5_rendering、test_motion_rendering、test_v2_media、test_v2_exports、test_production_modes。
5. 按"验证"一节汇报。

## 示例：把相邻原声的间隔从 150 ms 改成 200 ms（假设）

1. 先确认需求来自用户或交接文档的修订，不要自己决定。
2. 修改 `mode_rules.json` 的 `quote_gap`。再查 `mode_pipeline.py:1359-1363` 是否写死了这个值，以及前端 `productionModes.ts` 是否用到它。
3. 测试：在 `test_production_modes` 或 `test_mode_pipeline` 里断言新间隔；用合成音频确认总时长的变化。
4. 汇报：改前 → 改后；对总时长和跳切判定的影响；测试结果。

## 验证

- 汇报改了哪些滤镜或编码参数（改前 → 改后），以及对响度、时长、分辨率、帧率、AI 标注显示的影响。
- 跑了哪些测试、结果如何；没跑的写"未运行"和原因。

## 待确认（只记录，这里不修）

1. **样片水印。** 交接文档要求烧录样片水印；后端没有实现，而且 `acknowledge_unresolved` 可以导出不带水印的成片（`v2_editing.py:521-534`）。
2. **真峰值有三个值。** 处理时用 −3.5，质检上限 −3.0，旧设置默认 −2.0；`narration_profile.json` 记录的是 −3.0。
3. **18 dB 闪避只在 V2 生效。** `TTS_TARGET_*`、`MUSIC_DUCK_RATIO`、`MOTION_ZOOM_RATIO`、`COLOR_CONSISTENCY_TARGET_LUFS` 对 V2 都不生效。
4. **没被使用的规则字段。** `mode_rules.json` 的 `original_hard_cuts_warn_above` 在代码里没有使用。
5. **Windows 下取消任务不清理进程树。**
6. **AI 标注是否合规。** 标注的字高、位置和显示时段是否满足 GB 45438-2025 第 5.4 条，见 gm-ai-labeling。

## 参考

- [references/render-details.md](references/render-details.md)：人声增强、推近、颜色统一、淡入淡出、成片编码、导出格式、子进程
- [references/ffmpeg-pitfalls.md](references/ffmpeg-pitfalls.md)：通用的 FFmpeg 陷阱清单
- 来源：README.md（FFmpeg 必需组件）、`backend/rendering.py`、`graphics.py`、`subtitles.py`、`tts_pipeline.py`、`audio_filters.py`、`music.py`、`media.py`、`media_input.py`、`uploads.py`、`studio_render.py`、`production_modes.py`、`mode_pipeline.py`、`readiness.py`、`mode_rules.json`、交接文档 §2、§10。2026-10-21 按工作区代码核对，行号会变，以函数名为准。
