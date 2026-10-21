---
name: gm-ffmpeg-render
description: 金话筒 FFmpeg 渲染与音频约定（render / loudness / ASS subtitles / export / subprocess）。涵盖响度 −20 LUFS、原声降噪、ASS 字幕（Noto Sans SC）、配乐自动压低、推近、颜色统一、淡入淡出、H.264 30fps 编码、导出规格、AI 披露烧录，以及子进程调用、超时、取消和 Windows 路径转义。修改或排查 rendering.py、graphics.py、tts_pipeline.py、audio_filters.py、music.py、media.py、studio_render.py，或用户说"渲染""响度""字幕样式""配乐""导出参数""FFmpeg 命令""子进程超时"时使用。不用于安装或升级 FFmpeg，也不用于用真实素材制作成片。
---

# 金话筒 FFmpeg 渲染约定

## 目的
修改渲染、音频和导出代码时守住现有约定：参数、披露、子进程安全，并且只用合成素材验证。下表是代码现状；以代码为准，和文档或旧报告冲突时信代码。

## 适用 / 不适用
- 适用：调整滤镜、编码、响度、字幕样式、导出格式；排查渲染失败、超时、音画不同步；审查 FFmpeg 相关改动。
- 不适用：
  - 安装或升级 FFmpeg、下载模型或工具。
  - 用真实素材出片或调用付费服务。
  - 检查码和导出门禁的规则，见 gm-news-integrity；AI 标识的合规设计，见 gm-ai-labeling。

## 硬性规则
- FFmpeg 和 ffprobe 只用**参数列表**调用：异步用 `asyncio.create_subprocess_exec`，同步用 `subprocess.run([...])`。不拼 shell 字符串，不用 `shell=True`、`os.system`、`os.popen`。
- 不新增没有超时的子进程。媒体命令用 `MEDIA_COMMAND_TIMEOUT_SECONDS`（默认 7200 秒；生产环境不得超过 `SHUTDOWN_GRACE_SECONDS`）；其他固定超时见下文"子进程"一行。
- 保留 `media.py` 的协议和格式白名单（`-protocol_whitelist file` 等），不扩大输入来源。
- 滤镜图里的路径必须转义：渲染用 `rendering._escape_filter_path`，导出用 `studio_render.filter_path`。Windows 盘符冒号、反斜杠和引号都要处理。
- **AI 披露"AI生成示意画面"单独放在 `graphics.ass` 里烧录，不受字幕开关影响；导出时总是烧录 graphics**。任何改动都不能让披露被关掉、裁掉或丢失。会丢失披露的格式必须拒绝。
- FFmpeg 必需的能力有 libx264、aac、ass（libass）、loudnorm、ebur128、silencedetect、perspective，`readiness.py` 启动时会检查，不能删掉检查或用假工具绕过。
- 只用合成素材和受保护测试验证，运行方式见 gm-verify。

## 现行约定

| 项目 | 现行做法 | 代码位置 |
|---|---|---|
| 响度 | 每个音频单元做两遍 `loudnorm`，目标 I=−20、LRA=5、TP=−3.5（dual_mono、linear）。旁白轨同样的目标。有配乐时混音后再做两遍、归一到 −20；没有配乐时最终音频直接复制。QC 用 `ebur128`：容差 ±1 LU，真峰值不超过 −3.0 dBTP。`TTS_TARGET_*` 只影响旧流程 | `tts_pipeline.py`、`mode_pipeline.py`、`rendering.py`、`quality.py` |
| 原声增强 | `highpass=f=70:p=2` 加时钟对齐的 `afftdn`（nr=12、nf=−50 等）。只处理真实录音（同期、本人录音），不处理 TTS。**默认关闭**，对应偏好项 `enhance_speech` | `tts_pipeline.SPEECH_ENHANCEMENT_FILTER`、`audio_filters.AFFTDN_CLOCK_FILTER` |
| 字幕 | ASS，字体 Noto Sans SC。字体随包放在 `backend/assets/fonts/`，启动时校验散列和 OFL 许可。画布 1920×1080；样式 ModeNarration、ModeQuote、ModeTitle。成片用 `ass=filename=…:fontsdir=…` 烧录，导出用 `subtitles=…` | `graphics.py`、`subtitles.py`、`rendering.py`、`readiness.py` |
| AI 披露 | 样式 GfxDisclosure：右上角，字号 30，带底框。只在 AI 生成镜头的时间段内显示 | `graphics.py`、`mode_pipeline.py` |
| 配乐压低 | V2 按语音时间算出音量包络（压低 −18 dB，0.3 秒渐变，`volume=…:eval=frame`），再用 `amix normalize=0` 混音。`sidechaincompress` 只在旧流程里用，`MUSIC_DUCK_RATIO` 对 V2 无效。配乐本身：开头 2 秒淡入、结尾 3 秒淡出，`loudnorm I=MUSIC_BED_TARGET_LUFS(−32):LRA=6:TP=−6`，循环播放。**默认关闭**，对应 `background_music` | `audio_filters.py`、`rendering.py`、`music.py` |
| 推近 | 用 `perspective` 滤镜，比例写死为 0.08（不是 `MOTION_ZOOM_RATIO`）。作用于旁白镜头，不限于照片。**默认关闭**，对应 `motion_effects`。跳切处有 6 帧、比例 0.06 的推近。照片会先转成 3 秒静态片段 | `rendering.py`、`mode_pipeline.py`、`media_input.py` |
| 颜色统一 | 逐片段应用 `normalize=smoothing=50:independence=0:strength=0.5,eq=contrast=1.04:saturation=1.05`。**默认关闭**，对应 `color_consistency` | `rendering.py` |
| 淡入淡出 | 只作用于画面：开头 `fade=t=in` 0.5 秒，结尾 `fade=t=out` 0.6 秒，音频不做淡化。只在开启 `transitions` 时生效；导出时在导出滤镜链里按设置应用 | `rendering.py`、`studio_render.py` |
| 成片编码 | 素材先统一成 1920×1080、`fps=30`、SAR 1。编码用 libx264 veryfast、CRF 20、yuv420p。成片要校验：H.264、1920×1080、30fps、AAC 48 kHz，与旁白时长相差不超过 0.3 秒 | `media.py`、`rendering.py` |
| 导出 | MP4：先 `scale=…:force_original_aspect_ratio=increase,crop` 填满目标画幅，再烧录 captions 和 graphics，`fps=30`；libx264 veryfast，码率 4000k（maxrate 4000k、bufsize 8000k），AAC 192k。分辨率 720p/1080p（后端另外接受 360p），画幅 16:9、9:16、1:1。GIF：宽 480、12 fps、取前 6 秒、无声。MP3：libmp3lame 192k，只含人声。另有 SRT 字幕和 PNG 封面。含 AI 披露的作品拒绝导出 MP3 和 SRT | `studio_render.py`、`v2_editing.py` |
| 子进程 | 异步执行，用 `asyncio.wait_for` 控制超时。取消或超时时：Linux/macOS 用独立会话加 `killpg`（先 SIGTERM，10 秒后 SIGKILL）；**Windows 只结束直接子进程，不清理进程树**。其他固定超时：QC 600 秒，启动探测 15 秒，上传按素材时长计算，V2 导出取 min(设置值, max(120, 4×时长)) | `media.py`、`quality.py`、`readiness.py`、`uploads.py`、`studio_render.py` |

## 已知不一致（只记录，这里不修）
- `narration_profile.json` 记录的真峰值是 −3.0，实际处理用的是 −3.5。
- `TTS_TARGET_*`、`MUSIC_DUCK_RATIO`、`MOTION_ZOOM_RATIO`、`COLOR_CONSISTENCY_TARGET_LUFS` 对 V2 不生效。
- Windows 下取消任务不会清理进程树。
- 披露的字高、位置和出现时段是否满足 GB 45438-2025 第 5.4 条：TODO(待确认)，见 gm-ai-labeling。

## 步骤（改渲染相关代码时）
1. 先按上表找到对应代码，确认现状。不清楚就读代码，不要凭文档猜。
2. 改动时保持四件事不变：参数列表调用、超时、路径转义、披露烧录。
3. 用合成素材跑受保护测试：gm-verify 的命令 B，重点关注 test_m5_rendering、test_motion_rendering、test_canary_rendering、test_v2_media、test_v2_exports。
4. 按"输出格式"报告。

## 输出格式
- 改了哪些滤镜或编码参数（改前 → 改后）。
- 对响度、时长、分辨率、帧率、披露显示的影响。
- 跑了哪些测试、结果如何；没跑的写"未运行"和原因。

## 来源
- [README.md](../../../README.md)（FFmpeg 必需组件）
- [backend/rendering.py](../../../backend/rendering.py)、[backend/graphics.py](../../../backend/graphics.py)、[backend/subtitles.py](../../../backend/subtitles.py)、[backend/tts_pipeline.py](../../../backend/tts_pipeline.py)、[backend/audio_filters.py](../../../backend/audio_filters.py)、[backend/music.py](../../../backend/music.py)、[backend/media.py](../../../backend/media.py)、[backend/studio_render.py](../../../backend/studio_render.py)、[backend/readiness.py](../../../backend/readiness.py)、[backend/config.py](../../../backend/config.py)、[backend/models.py](../../../backend/models.py)（偏好项默认值）
- 2026-10-21 按提交 af1e4e7 读代码核对。
