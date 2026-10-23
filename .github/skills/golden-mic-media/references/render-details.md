# 渲染与导出的其他约定

这些约定原先写在 gm-ffmpeg-render 里（2026-10-21 按提交 af1e4e7 核对），改名合并时移到这里。以代码为准，行号会变。

| 项目 | 现行做法 | 代码位置 |
|---|---|---|
| 原声增强 | `highpass=f=70:p=2`，加上按时钟对齐的 `afftdn`（nr=12、nf=−50 等）。只处理真实录音（同期声、本人录音），不处理 TTS。**默认关闭**，对应偏好项 `enhance_speech` | `tts_pipeline.SPEECH_ENHANCEMENT_FILTER`、`audio_filters.AFFTDN_CLOCK_FILTER` |
| 推近 | 用 `perspective` 滤镜，比例写死为 0.08，不读 `MOTION_ZOOM_RATIO`。作用于旁白镜头，不只是照片。**默认关闭**，对应 `motion_effects`。跳切处的推近见 SKILL.md 的跳切遮盖 | `rendering.py`、`mode_pipeline.py` |
| 颜色统一 | 逐片段应用 `normalize=smoothing=50:independence=0:strength=0.5,eq=contrast=1.04:saturation=1.05`。**默认关闭**，对应 `color_consistency` | `rendering.py` |
| 淡入淡出 | 只作用于画面：开头 `fade=t=in` 0.5 秒，结尾 `fade=t=out` 0.6 秒，音频不淡化。只在开启 `transitions` 时生效；导出时在导出滤镜链里按设置应用 | `rendering.py`、`studio_render.py` |
| 成片编码 | 素材先统一成 1920×1080、`fps=30`、SAR 1；用 libx264 veryfast 编码，CRF 20，yuv420p。成片要校验：H.264、1920×1080、30fps、AAC 48 kHz，和旁白的时长差不超过 0.3 秒 | `media.py`、`rendering.py` |
| 导出 | 格式、画幅、分辨率见下方"导出格式" | `studio_render.py`（`V2ExportOptions`，第 474–483 行）、`v2_editing.py` |
| 子进程 | 异步执行，用 `asyncio.wait_for` 控制超时。取消或超时时：Linux 和 macOS 用独立会话加 `killpg`（先 SIGTERM，10 秒后 SIGKILL）；**Windows 先结束整棵子进程树（`media.kill_windows_descendants`），再结束直接子进程**。固定超时：质检 600 秒，启动探测 15 秒，上传按素材时长计算，V2 导出取 min(设置值, max(120, 4 × 时长)) | `media.py`、`quality.py`、`readiness.py`、`uploads.py`、`studio_render.py` |

## 导出格式

- **MP4**：
  - 先用 `scale=…:force_original_aspect_ratio=increase,crop` 填满目标画幅，再烧录字幕和 graphics（旧版本的 AI 标识也在里面），`fps=30`；
  - 视频用 libx264 veryfast，码率 4000k（maxrate 4000k、bufsize 8000k）；音频 AAC 192k。
- **分辨率**：720p、1080p，后端另外接受 360p。**画幅**：16:9、9:16、1:1。
- **GIF**：宽 480，12 fps，只取前 6 秒，无声。
- **MP3**：libmp3lame 192k，只含人声。
- **其他**：SRT 字幕、PNG 封面。
- 含 AI 画面的作品也可以导出 MP3 和 SRT（2026-10-23 起；之前因为会丢失标识而拒绝）。
- **并发限制**：每个任务最多 20 个导出作业，全站同时最多 2 个（`v2_editing.py:578-582`）。

## 能力预检

- `readiness._check_media_capabilities` 运行 `ffmpeg -hide_banner -encoders`、`ffmpeg -hide_banner -filters`、`ffprobe -hide_banner -version`，每条超时 15 秒（`readiness.py:291-323`）。
- 必需的能力：编码器 libx264、aac；滤镜 ass、loudnorm、ebur128、silencedetect、perspective。
- `media.require_media_tools` 只检查 PATH 上有没有 ffmpeg 和 ffprobe，在流水线开头调用。
- 代码用到、但预检没有检查的：afftdn、sidechaincompress、`volume` 的 eval、lut3d。
