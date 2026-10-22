# 通用 FFmpeg 陷阱

写新的 FFmpeg 命令或审查改动时逐条对照。这些是 FFmpeg 的通用行为，不是本项目特有的规定；本项目的具体参数以 SKILL.md 和代码为准。

1. **`-ss` 和 `-to` 放在同一侧。**
   - `-ss` 放在 `-i` 前面是输入选项：直接跳转到该位置，速度快。放在后面是输出选项：先解码再丢弃前面的部分。
   - 如果 `-ss` 是输入选项、`-to` 是输出选项，`-to` 会按输出的时间轴（从 0 开始）计算，截出来的长度就不对了。
   - 不确定时，用 `-t` 指定时长。
2. **H.264 成片要显式写 `-pix_fmt yuv420p`。** 要在网页里边下边播，加 `-movflags +faststart`，把 moov 移到文件开头。
3. **用 `scale=W:-2` 而不是 `-1`。** `-2` 会保证高度是偶数，yuv420p 要求宽高都是偶数。
4. **多路输入时显式写 `-map`。** 否则 FFmpeg 会按默认规则挑流，可能拿错音轨，或者漏掉字幕。
5. **`amix` 默认会把各路音量平均掉。** 要保持原来的电平，用 `amix=…:normalize=0`（本项目的混音就是这么做的）。
6. **`loudnorm` 会把采样率升到 192 kHz。** 输出时要显式写 `-ar 48000`（或接 `aresample=48000`）。
7. **两遍 `loudnorm` 要确认第二遍真的是线性模式。**
   - 第二遍要传入第一遍测得的 `measured_I`、`measured_LRA`、`measured_TP`、`measured_thresh`、`offset`，并设 `linear=true`。
   - 再检查输出 JSON 里的 `normalization_type` 是不是 `linear`。目标值无法满足时，滤镜会退回 dynamic 模式。
8. **concat demuxer 只能拼参数完全一致的片段。** 编码器、分辨率、帧率、采样率、声道都要相同；否则改用 concat 滤镜重新编码。用 `-c copy` 拼接 AAC 分段时，编码器延迟可能在接缝处留下很短的空隙。
9. **非交互运行要写 `-nostdin`，以及 `-y` 或 `-n` 之一。** 否则 FFmpeg 可能等待键盘输入或覆盖确认，导致子进程挂起，直到超时。
10. **帧率模式用 `-fps_mode`。** 旧的 `-vsync` 已被弃用。要恒定帧率时，写 `-fps_mode cfr`，或者在滤镜里用 `fps=30`（本项目用后者）。
11. **`-shortest` 不一定精确。** 音视频长度不一致时，因为缓冲，输出可能比最短的流略长。需要精确时长时，用 `-t` 或在滤镜里裁剪，再用 ffprobe 核对。
12. **图片转视频。** 用 `-loop 1 -t <秒> -i 图片`，或用 `tpad` 延长最后一帧。本项目照片定格 3 秒的做法见 `media_input.py`。
13. **滤镜参数里的路径要按两级解析来转义。** 一级是滤镜图解析，一级是滤镜选项解析。本项目已有的转义函数见 SKILL.md 的"Windows 与 Linux"，不要另写一套。
14. **处理不可信的上传前，先限制探测资源。** 本项目的做法：协议和格式白名单、`probesize`、`max_alloc`、超时（`uploads._probe`）。

## 来源

- FFmpeg 官方文档：[ffmpeg 命令行选项](https://ffmpeg.org/ffmpeg.html)、[滤镜手册](https://ffmpeg.org/ffmpeg-filters.html)（loudnorm、amix、concat、tpad），以及 [Concatenate wiki](https://trac.ffmpeg.org/wiki/Concatenate)。
- 条目的选取参考了 [0xDarkMatter/claude-mods](https://github.com/0xDarkMatter/claude-mods) 的 ffmpeg-ops skill（提交 `fa93328`，MIT 许可）里的陷阱表，已经逐条改写。它有错误的条目没有采用：Windows 转义的两种写法互相矛盾、视频旋转的建议、"sidechaincompress 压低 12 dB"的说法等。
