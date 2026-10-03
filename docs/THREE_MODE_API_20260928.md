# 三模式 API 契约（2026-09-28）

> **FINAL：本地合成验收通过，限于已验证范围，不是整套生产能力签收。** L browser 8/8、262.979523 秒、0 retry；最新后端 1118=1116 pass+2 skip，前端 459 pass、3 项 typecheck 通过。三模式尚未部署日常 `http://127.0.0.1:8000` 或生产。准确证据见 [THREE_MODE_VALIDATION_20260928.md](THREE_MODE_VALIDATION_20260928.md)，实现与缺口见 [THREE_MODE_IMPLEMENTATION_20260928.md](THREE_MODE_IMPLEMENTATION_20260928.md)，使用入口见 [README](../README.md)。

## 1. 模式、权限与兼容

| 模式 | 接受的句子 | 声音语义 |
| --- | --- | --- |
| A：`voiceover`（AI 配音） | 全部 `narration` | 默认全部由 AI 读；素材转写只作提示/检索，**不自动变成同期声**。显式选择 `voice:mine` 是自主录音分支，不是自动原声替换。 |
| B：`mixed`（旁白 + 原声） | `narration`、显式 `quote` | 只为旁白配音；原声句必须来自真实上传区间，不因匹配失败偷偷改用 TTS。 |
| C：`original`（只用原声） | 全部 `quote` | **无 TTS、无原声调速、无生成式补拍**；不接受配音文件或转旁白编辑。存储中的 `voice:ai` 兼容默认值不表示调用 TTS。 |

- 无登录、无教师/课堂流程。生产写请求仍须签名匿名会话、`X-CSRF-Token` 和严格同源 Origin；匿名 cookie 不是作品权限。`GET /api/session` 取得匿名会话。
- 任务 capability：创建返回 `access_token`，任务接口用 `X-Task-Token`，媒体可用 `token` 查询参数。仅非生产可信直连 loopback 可按现有规则免任务 token；错误/冲突 token 不会回退为本机权限。生产没有全局任务索引。
- **上传 capability 独立于任务 capability**：所有已有上传接口均须 `X-Upload-Token`，本机也不豁免；只有上传媒体 GET 允许 `?token=`。保存 token 的浏览器缓存是敏感数据，不打印到日志或复制进文档。
- 新接口与 legacy multipart、历史工作台及 Studio API 共存。没有 `mode` 的旧文件提交仍走兼容分支；有 `mode` 却无 `upload_ids` 的 multipart 被拒绝，不能混用两套语义。旧存档不自动改造成三模式任务。

依据：[main.py](../backend/main.py)、[uploads.py](../backend/uploads.py)、[models.py](../backend/models.py)。历史安全背景见 [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md)；与本文冲突的旧模式/入口描述不代表当前实现。

## 2. 分块上传与预转写

| 方法 / 路径 | 请求与回执 |
| --- | --- |
| `POST /api/uploads` | JSON：`name`、整数 `bytes`、SHA-256 `sha256`、可选 `content_type`；201 返回 `upload_id`、`chunk_size`、含 `{index}` 的 `put_url`、`access_token`。 |
| `PUT /api/uploads/{id}/chunks/{index}` | 原始字节，零起始索引，精确 `Content-Length`；普通块 **8 MiB = 8,388,608 字节**，最后一块按剩余长度。不是“整个文件只能 8 MiB”。 |
| `GET /api/uploads/{id}` | 已确认 `chunks`、`received_bytes`、进度、状态、有效上限、`expires_at`、媒体信息、转写与失败证据；不返回 token。 |
| `POST /api/uploads/{id}/complete` | 202；验证完整大小/散列后安排本地预处理及需要的 ASR。重复 complete 幂等，不因此再次发起付费 ASR。 |
| `DELETE /api/uploads/{id}` | 显式删除上传，成功 204。 |
| `GET /api/uploads/{id}/thumb`、`/wave` | 私有缩略图/真实采样波形；回执 URL 不自带凭据。 |

公开状态为 `uploading/probing/transcribing/ready/failed`；`failed` 不一定是坏媒体，应结合 `probe_ok`、`error_code`、`can_materialize`、`asr_retry_in_task` 判断。媒体已通过但 ASR 失败的上传可作画面；正式任务可显式补跑一次 ASR。GET、刷新或重启不自动重试 ASR；ready 预转写在正式任务复用。跨上传缓存复用限定同 owner、同 Provider/请求作用域。

原始响应的 `transcript` 是对象，含 `text/segments/speakers/precision/segment_precision/word_trim_allowed` 等；前端适配后的数组不是原始 wire schema。每段含 `id/start/end/speaker_id/text/words`，词为 `{w,s,e}`；可有 `confidence/snr_db`。**所有转写与 take 时间均为原始上传的绝对秒数**，不是截取副本的零起点。

### 有界容量（不是性能承诺）

| 项目 | 当前三模式实现 |
| --- | --- |
| 视频/总量 | 单文件最多 500 MiB、owner 上传最多 5 GiB；配置可进一步收紧。 |
| 文件数 | 新上传 owner 容量与新任务选择最多 20 个；开发模板已同步为 20。 |
| 源时长 | 单文件最多 1800 秒、选择合计最多 3600 秒；仍受配置/解码校验。 |
| 图片 | JPEG/PNG/GIF，最多 **50 MiB、20,000,000 像素（20 MP）**，并受配置宽高限制。GIF 只取首帧，作为 3 秒静态图片；仍检查动画结构，最多 300 帧、累计 1 亿画布像素、每帧不得越界。 |
| 上传期限 | 从创建起 72 小时；已持久化的有效过期时间不因读取延长。 |
| 新任务保留 | `mode_contract=true` 的普通终态按保留参考时间计 72 小时；不是创建后精确到点删除，也不会清理正在执行/受保护任务。旧 `local_only` 跳过 TTL。 |
| 制作容量 | 一个服务 worker，任务执行并发上限 1；pending 上限 5，**包含 queued + running**，不是 1 个运行再加 5 个等待。 |

支持视频 MP4/MOV/AVI/MKV；不接受 WebM/M4V/WMV。预处理本身有独立有界并发，不能把“1 个制作任务”理解为只有一个媒体子进程。`GET /api/config/limits` 提供客户端限额；上传状态还提供图片等细分上限。[开发模板](../.env.example)现为 **20 文件 / 1 执行 / 5 pending**，并刻意保留 **`TASK_TTL_HOURS=0`**：它保护 legacy 普通任务免于自动 TTL 清理，不关闭新三模式固定 72 小时保留。**不要为开启三模式保留期而把开发 TTL 改成 72，这会同时给 legacy 普通任务启用清理。** 旧 `local_only` 另有保护。[生产模板](../deploy/golden-mic.env.production.example)为 20/1/5/72；这是独立部署策略，不是本地迁移指令。模板不证明真实环境已更改或日常 8000 已运行新代码。

## 3. 原话预检与 JSON 创建

### `POST /api/match/preview`

请求：`sentences`（最多 200）、`upload_ids`（1–20）、`upload_tokens`（ID → 上传 token）、可选 `speakers`（最多 100）。稿句总字数最多 8000。返回 `matches`、`match_ok:0.85`、`match_low:0.6`。

每项保留输入 `idx/kind`，包含匹配分数、`source`、`alt_takes` 及源区间信息。`source` 为 `QuoteTake`：`take_id/upload_id/start/end/speaker_id/asr_text/score/snr_db/words/precision`，可含 `matched_text/segment_ids`。无匹配时不得把空值伪装成时间 0 的真实出处。预检可给旁白提出原声建议，但**不修改句子类型、不创建任务**；单个预检槽忙时返回 429。

分数是文本规范化后的编辑距离相似度，不是声学准确率、人物身份置信度或新闻事实核验。段级降级时 `matched_text` 可只是局部命中文本，`asr_text` 必须保留实际选中整段，不能按字数分配出假词时。

### `POST /api/tasks`（JSON，202）

| 字段 | 约束 |
| --- | --- |
| `mode` | `voiceover/mixed/original`，默认 A。 |
| `script` | 标题 + 正文，最多 8000 字；标题按首行契约校验，最长 40 字，不朗读。 |
| `sentences` | `{idx,text,kind,speaker_hint?,source_hint?}`；`idx` 从 0 连续，最多 200 句、单句最多 2000 字；`source_hint` 为 `{upload_id,seg_id}`。 |
| `upload_ids/upload_tokens` | 已完成预处理或明确可 materialize 的素材及有效 capability；最多 20，无重复。 |
| `speakers` | `{id,name,title,auto_label,appearances,seconds}` 的列表，最多 100，ID 唯一。标签来自 ASR 聚类/用户填写，不是实名识别。 |
| `preferences` | 剪辑偏好；模式新增 `voice`、`lower_third`、`jump_cut_cover`、`quote_caption`。 |
| `asset_options` | 可选素材裁剪/备注列表，沿用[媒体输入契约](MEDIA_INPUT_API.md)。 |
| `quality_gate_mode` | 开发可选 `warn/block`；生产始终 `block`。真实性错误不能借 warn 获得正式发布资格。 |

**客户端已确认的 `sentences` 是正式制作输入**，尤其 C 的转写选句不得由服务端模型再分句或重写。服务端检查清单与稿件文字/顺序一致、模式允许的类型与源提示归属；只有未提供/空清单时才使用确定性解析器补齐。该一致性校验不是逐字节签名，也不是事实核验。

202 回执为 `{task_id,access_token}`，不保证带完整状态/版本；随后 GET 查询。无创建幂等键，响应不明时先查状态，不能自动重复 POST。显式自主录音使用含相同 JSON 字段的 multipart 加 `own_voice`；C 拒绝该文件，选择 `mine` 却缺录音不会静默改用 AI。

`quote_caption`（界面原声字幕开关，亦称 quoteCaption）的正式值为 **`asr/none`**，不是原型的 `spoken`；`spoken` 仅在前端旧偏好读取时兼容映射。`caption_style` 为 `news/big/none`；制作阶段 `none` 只关闭旁白字幕，原声字幕由 `quote_caption` 独立控制，注意与导出 `sub:none` 区别。

## 4. 状态、报告、原声修改

| 方法 / 路径 | 行为 |
| --- | --- |
| `GET /api/tasks/{id}` | 真实状态、十阶段、权重、阶段/总耗时、错误；权重不是 ETA。**不提供失败任务创建上下文恢复接口**。 |
| `GET /api/tasks/{id}/report` | 当前已提交版本的逐句报告，含模式、句型、实际发声文字/来源、说话人、检查/指标；不能从报告反推原始提交稿。 |
| `GET /api/tasks/{id}/video` | capability 保护的 raw preview；`download=true` 另需发布门禁。 |
| `GET /api/tasks/{id}/quotes/{row_id}/takes` | `{takes:[...]}`，仅返回当前计划中的候选，不是任意源时间输入。 |
| `GET /api/tasks/{id}/waves/{row_id}.json` | `{interval_ms:20,samples,start,end}`；从已生成原声 PCM 测 RMS，时间取实际源区间，不伪造波形。 |
| `POST /api/tasks/{id}/remix` | 三模式转入有界工作台批量编辑；202 只代表接受操作。 |
| `POST /api/tasks/{id}/workbench/edit` | 直接使用同一修订事务，严格 `EditRequest`。 |
| `GET /api/tasks/{id}/workbench/versions`、`POST .../restore` | 历史快照/显式恢复；恢复请求为 `revision + expected_revision`，不是覆盖历史。 |

批量编辑建议总是发送 `expected_revision` 与按原序的 `keep_sentence_ids`（至少一句）。`remix` 兼容缺省当前版本/保留列表，但不能依赖这种默认值实现并发安全；直接工作台接口要求显式字段。

- `edits:[{sentence_id,text?,shot_id?,instruction?,recording_id?}]`：旁白改文/换镜/自录，`shot_id` 与 `instruction` 互斥；`remix` 也接受 `id` 别名，不能与 `sentence_id` 同时发。
- `quote_trims:[{id,start,end}]`：只缩小当前连续原声范围，至少 1 秒，不切词内；仅整段精度时只允许原区间不变。不能提交替换文字/伪造词数组、扩张范围或掐掉中间词再拼接。
- `quote_takes:[{id,take_id}]`：只选择当前 `alt_takes` 中已有候选；不得任填文件/时间。
- `to_narration:[id]`：仅 B 的显式转旁白。必须先单独转换，不能同批改这句文字；C 拒绝。
- `speakers:[...]`：同一批提交名字/身份变更；不会建立跨录音的人物身份认证。
- 可附 `pacing/caption_style/enhance_speech`。当前编辑 schema **没有** `quote_caption/lower_third/jump_cut_cover` 的任意修改入口，不能把创建偏好全量当编辑请求发送。
- 同一句只允许一项 edit/trim/take/conversion，目标必须仍被保留。原声不接受改字、自录替换或换画面；删句保留原序。失败保留上次成功版，不宣称断电跨文件原子性。

合法 trim/take 后，当前文字、句子清单与报告同步为实际保留的 ASR 原话，旧稿留在不可变旧修订；后续 QC 使用当前原话，不再拿剪短前的旧稿误判。最终 L 的 MODES-03 已通过八句 C 十阶段、合法 trim 至 R1 与真实 FFmpeg 导出：初版 60.066667 秒，正式输出 59.566667 秒 / 31,579,719 字节 / 1080p30 H.264 + AAC 48 kHz；确认前 export 为 409，确认 10 项后可下载，R0 的 62/62 文件 SHA 不变，无 TTS/重复 ASR。L 全套 8/8 通过；J/K 原失败证据仍保留，详见[最终验收记录](THREE_MODE_VALIDATION_20260928.md)。这是合成音调/人工词时覆盖，不是真实 ASR 或人声质量签收。

旧 `POST /api/tasks/{id}/replace-shot` 按设计保留 `sentence_id + instruction` 两字段契约，不新增 `expected_revision`，也不把这一兼容设计列为缺陷。需要显式版本控制的批量修改使用上述工作台接口。

## 5. 质量与发布确认

依据：[mode_rules.json](../backend/mode_rules.json)、[production_modes.py](../backend/production_modes.py)、[publication.py](../backend/publication.py)。

- 原声匹配 `<0.6` 阻断，`0.6≤score<0.85` 待人工确认，`≥0.85` 仅表示文本匹配较高。C 仍要求稿句为实际 ASR 的规范化连续子串，不能用高分容许同音替换、新增词或中间删词。
- 原声 `<1 秒` 阻断，`>20 秒` 警告，`>30 秒` 阻断；SNR **低于 15 dB** 警告，不采用旧规格的 18 dB。SNR 是 PCM/ASR 范围估计，缺测不填假数。
- 稿件与实际转写差异 `>10%` 等触发 `QUOTE_TEXT_DIFFERS`；字幕仍使用实际 ASR。启用人名条且未命名时提醒补填。C 的跳切检查针对自动空镜降级或硬切超过 3 处，不是原型里“所有非空镜都警告”。
- `FACT_CHECK` 要记者人工核对数字、人名、日期、称谓；勾选只记录人已核查，ASR/LLM 不替代事实来源。常规真实 QC、缺源/缺披露、fallback 等错误继续阻断。

`GET /api/tasks/{id}/checks` 返回 `{revision,blocking_count,pending_count,passed,checks}`。每项含 `key/code/level/message/sentence_id/action/checked`。`POST` 同路径提交 `{expected_revision,checked_keys}`，**替换完整勾选集合**，空数组撤销；旧版本 409，未知/旧 key、普通 error 或纯提示 key 为 422。

级别 0 普通错误不可勾免，级别 1 须确认，级别 2 为提示；只有服务端从当前生成媒体来源派生的 `generated_media` 知情项可在级别 0 勾选，缺披露等真实错误仍不能放行。key 绑定版本与报告/来源证据，改版或证据变化会使旧确认失效。缺少/损坏 QC 时 fail closed，前端计算出的“通过”不是权限。

## 6. 简单导出与预览缺口

`POST /api/tasks/{id}/export`：JSON `fmt:mp4|mp3|gif`、`aspect:16:9|9:16|1:1`、`res:360p|720p|1080p`、`sub:std|big|none`，默认 MP4/16:9/1080p/std。需当前任务完成且发布门禁通过；202 返回 Studio 作业回执及 `export_id`，轮询 `GET /api/tasks/{id}/exports/{export_id}`；完成后用受保护的 Studio output 地址获取文件。既有 Studio 作业取消/下载接口保留，详见 [STUDIO_API.md](STUDIO_API.md)。

| 选项 | 当前三模式导出实现 |
| --- | --- |
| `std` / `big` | 1080p 模板字号分别 **54 / 72**，不是旧版 1.5 倍；使用真实字幕事件，在目标画幅/分辨率渲染。 |
| `none` | 关闭旁白与原声正文字幕；**保留标题（最多前 2.5 秒）、已启用的人名条/包装及强制生成内容披露**。不是清除画面上一切文字。 |
| 原声字幕已关闭 | `quote_caption:none` 在 std/big 导出时仍关闭，不擅自恢复；旁白可从真实时序重新开启。 |
| MP3 | 从散列绑定的逐句人声音频及真实间隙重组，A/B 为配音/自主录音与原声，C 为原声；**无 BGM**，不是从混有音乐的 final 抽音。 |
| GIF | 只取 **前 6 秒**（不足则全段），无声音，不支持任意预告区间。 |
| MP4 / MP3 长片 | 三模式 simple export 已使用独立的**服务端固定计划** `SimpleModeProject`，时长预算为 **`min(600, settings.max_total_source_duration_seconds)` 秒**，不再套用 legacy Studio 的 120 秒。只接受服务端生成的固定声画轨，不接受客户端任意 tracks/clips/effects/nesting 或可编辑工程 JSON。 |
| 字节/高码率限制 | **128 MiB** 上限仍有效；预估 `duration × bitrate × 125 > 128 MiB × 0.85` 时拒绝高码率组合，实际输出必须小于 128 MiB，仍检查完整时长，不截短冒充成功。600 秒是时长预算，不承诺任意高码率长片都可导出。 |
| legacy Studio | 公共 `Project/Clip`、任意轨道/嵌套及 legacy simple export 的 **120 秒** 限制不变；新计划不能用来绕过旧 API 的校验。 |

GIF 仍只输出前 6 秒，但三模式入口会先核对完整源成片也在上述时长预算内。MP3 另检查无损人声中间文件预算，不能只看最终 MP3 大小。

主入口 `/api/tasks/{id}/export` 继续保留；转交 Studio 时内部请求的 `path/raw_path` 改为 `/api/tasks/{id}/studio/export`，避免准备后重授权把自己的 busy reservation 当成外部修改而 self-gate。外层任务授权、发布/QC、修订和资源检查没有绕过。

导出从干净画面和规范字幕/图文元数据重建，复核真实时序、来源散列、人名条与字幕绑定；缺证据拒绝，不从旧字幕/文本比例编造时间。人名条首次最多 2.5 秒、受实际连续出场时长约束，距上次显示结束至少 60 秒才重显；关闭正文字幕不关闭人名条开关。

**未批准的 preview 仍是样片，但 raw preview 没有强制水印。** UI 样片提示不是烧录水印或 DRM；`GET /video` 的 inline 路径仅做任务授权，不调用发布门禁，`download=true` 与正式 export 才检查发布。不能宣称未通过检查的字节无法取得，也没有“已实现带强制水印样片导出”的承诺。

海报/缩略图 preview cache 的修订写入问题已修：固定读取已捕获的 committed 源媒体，JPG 和运行日志写入任务级 `public_previews`，不写不可变 revision。缓存键含源相对位置、revision、大小、mtime 和取帧点，避免 legacy→revision 同 stat 误复用。此修复不等于补上 raw preview 水印，依据 [public_media.py](../backend/public_media.py)。

## 7. 错误与恢复边界

常见 404 为不存在/无权访问；409 为版本、占用或发布门禁；413 为字节上限；415 为不支持媒体；422 为模式/来源/剪切/导出限制；429 为限流/预检占用；503 为服务/容量不可用；507 为磁盘保护。以实际响应为准，网络不明不得自动重提可能计费的任务。

失败页“返回编辑”使用 [submissionRecovery.ts](../frontend/src/lib/submissionRecovery.ts) 的**同浏览器、同源、按 task ID 绑定的提交快照**：只在服务器接受创建回执后保存，固定保留 30 天；只有明确点击返回编辑才读取，不在挂载/刷新时自动恢复或重提。持久化成功后可跨刷新/同浏览器标签读取，**不是跨设备同步，也不是 server creation context API**。替换另一份未提交草稿前须确认；不会从报告或别的草稿拼原稿。

恢复稿件/模式/素材清单后，上传 capability 走既有**只读复核**；不自动重传、complete、ASR 或制作，原服务器任务不变。**30 天仅指快照，不延长上传/任务的 72 小时保留**：媒体缺失、上传过期、凭据失效时不能恢复可用媒体，必须明确重选；未传完文件和整篇录音也须重选。快照过期、损坏、清 storage 后无持久快照、浏览器禁止访问或旧任务从未保存时，不能据此找回原提交。写入失败会提示，并仅可能保留同生命周期、同 TTL 的内存回退；不能把它当作刷新/清 storage 后可靠恢复，更不能因缓存失败重复创建。

**恢复验收边界：** L 使用显式预置恢复快照的 fixture，验证用户主动返回编辑及只读恢复路径，**不是产品自动保存 hook 的端到端验收**；真实保存 hook 另有单元测试覆盖。不能把两层覆盖合写为浏览器自动保存闭环已验证。

当前主界面的专业剪辑入口为只读 `StudioDemo`，不保存工程、不调用专业剪辑服务、不渲染/导出；后台 Studio API 仍保留，不表示整套旧编辑 UI 是当前验收入口。legacy 清理复核确认没有 active classroom 流程；旧任务兼容、Studio 与 replace-shot 两字段契约保留。历史文档、旧存档及用户数据保留，不因本次文档更新删除或迁移。