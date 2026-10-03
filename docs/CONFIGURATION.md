# 配置说明

[项目首页](../README.md) · [文档目录](README.md) · [快速开始](QUICKSTART.md) · [使用指南](USER_GUIDE.md) · [运行维护](RUNBOOK.md) · [故障排查](TROUBLESHOOTING.md)

面向首次配置的使用者。**模板不是可直接上线的配置，填入凭证也不等于已获调用授权。** 本文依据当前源码说明规则，不读取你的实际配置、不验证真实密钥。GitHub `blob/main` 链接指向完整源码，不是包内文件或部署版本证明；开发模板不随最小包发布，生产模板使用包内相对链接。

## 1. 先选环境，再填配置

| 场景 | 从哪里开始 | 注意事项 |
|---|---|---|
| 单机开发、界面体验 | [开发模板](https://github.com/mizhou0817/golden_mic/blob/main/.env.example)和[快速开始](QUICKSTART.md) | 从项目根目录启动；默认 `APP_ENV=development`，只监听本机回环地址 |
| 正式服务器 | [生产模板](../deploy/golden-mic.env.production.example)和[部署说明](../deploy/README.md) | 使用受权限保护的服务环境文件、HTTPS、独立数据盘；不能原样复制模板后宣布就绪 |

[Settings](../backend/config.py)及两份模板目前覆盖 **138 个配置字段**。无需逐项调参：先保留模板的非凭证值，再填写实际使用的服务配置。生产环境校验要求字段齐全；“不用”通常指保留字段并留空其值，而不是删掉字段。

**读取顺序与重启：**

- 普通 `get_settings()` 在非生产分支调用 `Settings()`：读取**当前工作目录**的本地 dotenv 文件，进程环境变量优先于该文件，再使用代码默认值。工作目录不是自动固定的，所以应从项目根目录启动。
- 只有进程环境变量 `APP_ENV` 已为 `production`，`get_settings()` 才调用 `Settings(_env_file=None)`，不从 dotenv 加载配置值。生产由服务管理器注入环境；不能只在本地 dotenv 中写 production 来实现这项隔离。
- `APP_ENV=test` **不是**屏蔽 dotenv 的办法，仍走普通读取分支。隔离开发/验证流程见[开发指南](DEVELOPMENT.md)。
- 配置有进程内缓存，启动就绪检查也是快照。修改配置、模型路径或依赖后，须按[运行维护](RUNBOOK.md)先排空再重启；刷新网页不会重新加载这些配置。
- “不加载 dotenv 配置值”不等于绝不访问该文件：生产预检还会检查当前目录的 dotenv 是否含已移除的 MediaKit 变量。详见[预检实现](../backend/readiness.py)。

## 2. 默认链路要准备哪些凭证

以下针对模板默认的 Kimi + 火山引擎组合；只写字段名，不提供真实密钥或可直接运行的付费示例。

| 用途 | 必需配置 | 容易混淆的地方 |
|---|---|---|
| 画面理解、文本处理、稿件分屏 | `KIMI_API_KEY`；保留对应 URL、`KIMI_MODEL=kimi-k3` | 分屏仍由 Kimi 配置创建；即使改了 `LLM_PROVIDER`，也不能据此删除 Kimi 配置 |
| 检索向量 Embedding | `VOLCENGINE_VISION_API_KEYS` | 默认 Embedding **共用此字段**，不存在 `VOLCENGINE_EMBEDDING_API_KEYS`；Kimi 不能直接替代此向量接口 |
| 语音识别 ASR | `VOLCENGINE_APP_ID` + `VOLCENGINE_ACCESS_TOKEN` | 默认开启原声处理；还需对应 ASR 资源授权，只有 Ark 密钥不够 |
| 配音 TTS | `VOLCENGINE_TTS_API_KEYS`，或可用的 `VOLCENGINE_APP_ID` + `VOLCENGINE_ACCESS_TOKEN` 组合 | 后者是 TTS 支持的备用鉴权，不代表有 ASR 权限就自动有 TTS 权限 |

来源：[分屏与 LLM](../backend/providers/llm.py)、[ASR](../backend/providers/asr.py)、[TTS](../backend/providers/tts.py)、[Embedding](../backend/providers/embedding.py)。服务端预检只检查配置存在、格式和本地前提，**不验证密钥真伪、额度、实际网络或模型权限**。

### 未使用的配置怎么处理

- 默认 Kimi 模式不使用备用的 `VISION_*`、`LLM_*` 兼容接口密钥；默认火山 Embedding 不使用备用 `EMBED_*` 密钥。保留模板字段，未使用的字符串值可留空。
- `VOLCENGINE_LLM_API_KEYS` 只在相应 LLM 选择下使用；`VOLCENGINE_APP_KEY` 不是当前上述 ASR/TTS 鉴权的必填替代品；`VOLCENGINE_ASR_CLUSTER_ID` 在当前 ASR 配置校验中不是必填。
- 生产模板中的 `CHANGE_ME` 必须逐项处理：使用中的字段填真实且获准的值；确实未使用的可选凭证留空。生产校验会拒绝残留的模板占位凭证，即使对应服务没启用。
- `notused` 只是普通字符串，**不是禁用开关，也不是有效凭证**。不要在必需字段填它来骗过存在性检查；未使用的可选凭证优先留空。
- 不要把“字符串可留空”推广到布尔、数字或路径字段。尤其两项模型路径不应填空字符串；下面只有可选清单路径有空值归一化规则。

## 3. 本地语音：保留真实的未就绪状态

本地声纹和 CTC 对齐是独立于云端 ASR/TTS 的能力。**模型选型完成不等于模型已安装、应用已接入或许可证已批准。**

| 字段 | 代码默认 / 开发模板 | 生产模板 |
|---|---|---|
| `LOCAL_SPEECH_REQUIRED` | `false` / `false` | `true` |
| `LOCAL_SPEECH_LICENSE_REVIEWED` | `false` / `false` | `false`，必须完成真实审查后才能确认 |
| `LOCAL_SPEAKER_MODEL_PATH` | 代码为 `None`；模板为示例文件路径 | 示例声纹文件路径，不表示文件已存在 |
| `LOCAL_ALIGNMENT_MODEL_PATH` | 代码为 `None`；模板为示例目录 | 示例 CTC 目录，不表示权重/词表已齐备 |
| `LOCAL_SPEECH_BUNDLE_MANIFEST_PATH` | 空白字符串归一化为 `None` | 留空时，必需生产模式会报告缺清单 |

**仅 `LOCAL_SPEECH_BUNDLE_MANIFEST_PATH` 这个可选路径字段专门把空白字符串转成 `None`**；不能依赖其他路径空值表示关闭。开发模板的声纹/CTC 示例路径与生产模型包布局不同，不应直接拼成生产清单。

生产 `LOCAL_SPEECH_REQUIRED=true` 时，必须同时满足：

1. 操作者提供并审核本地模型、语言适配、许可证、来源和摘要；应用不会自动下载或安装。
2. 应用实际运行环境能找到 `sherpa_onnx`，以及 CTC 所需 `torch`、`transformers`；独立环境安装不等于基础环境安装。
3. 使用同一个模型包根目录下的**精确规范绝对路径**：声纹为根目录下 speaker/model.onnx，CTC 为根目录下 ctc；清单也必须为绝对路径，可在包内或包外。不得用链接、别名或改写摘要绕过绑定。
4. [模型包校验](../backend/local_speech_bundle.py)通过清单、目录和文件摘要核对。该检查只是完整性前提，仍不证明来源真实性、推理兼容性或真实语音质量；预检不会运行推理。

非必需模式不运行这项必需模型包门禁，不代表语音功能可用；必需开发模式显式配置清单时也会校验。不要为了让状态变绿把生产 required 改成 false、伪造 license 确认、禁用校验或运行测试专用绕过逻辑。

截至[2026-10-03 交付记录](V2_PRODUCT_DELIVERY_20261003.md)：CAMPPlus 只有独立环境中的合成输入技术验证，**不是应用基础环境集成验收**；CTC 尚未下载安装并完成安全转换/加载。生产模板因此保持阻塞，待操作者供给并批准。选型详情见[2026-10-02 模型决策](V2_MODEL_SELECTION_20261002.md)。

## 4. 限额、保留时间和质量门禁

| 项目 | 当前规则 | 不应采用的“修复” |
|---|---|---|
| V2 等待队列 | `v2_max_waiting_tasks` 固定为 **50 个等待任务** | 不是 50 个并发，也不是 50 次免费调用 |
| 旧链路 pending | `MAX_PENDING_TASKS=5`，旧接收检查计入 queued/running | 不要混同 V2 的 50 或仅改此值扩容 V2 |
| 处理并发 | 模板 `MAX_CONCURRENT_TASKS=1`；部署使用一个 worker | 不要多开 worker 解决排队 |
| V2 草稿 | **24 小时空闲**到期 | 浏览器缓存不是永久备份 |
| V2 终态作品 | **72 小时**，包括失败；以服务端保留时间为准 | 不因查看、确认检查而无限续期 |
| 暂存上传 | 自创建起最长 **72 小时** | 草稿续期不会让旧上传无限续期 |
| 旧本地任务 | 开发模板 `TASK_TTL_HOURS=0`：关闭旧链路自动 TTL 清理 | **不能修改它来修复 V2 到期问题**；也不保证所有临时数据永久保留 |
| 质量 | 开发模板 `warn`，生产模板 `block` | 当前 Settings 允许生产 `warn`，但发布/导出仍有独立硬门禁，不能靠它强行导出 |

默认上传限制为单文件 500 MiB、总计 5120 MiB、20 个文件；视频单个 1800 秒、总计 3600 秒。V2 图片另有 50 MiB 限制。界面以服务端返回的实际限制为准。开发磁盘安全余量 5 GiB，生产至少 50 GiB；预留倍数默认 8，不等于只需容纳上传文件大小。

模板还保留独立的会话/IP/全站小时预算。取消、删除或失败不会退回已接收任务的预算；队列有空位也可能因预算或磁盘不足拒绝任务。完整保留/备份方法见[运行维护](RUNBOOK.md)。

## 5. 可选能力和费用边界

- 开发模板 `ASR_SPARSE_RETRY_ENABLED=false`、`ENTITY_VERIFICATION_ENABLED=false`；代码默认及生产模板为 true。前者涉及稀疏识别额外重试，后者涉及实体核验，开启前先评估费用。
- `VIDEO_EMBEDDING_ENABLED=true`，开发模板也会使用云服务；“本地运行”不等于“完全离线”。稿件、抽取音频、画面或向量检索输入可能发送给配置的提供方。
- `GENERATIVE_FILL_ENABLED=false` 是默认值。生成补画面还需要已批准的密钥、资源与对应功能选择；不要为了排查缺素材随手开启付费生成。
- `SYNC_SOUND_*`、语速、响度、检索和 Embedding 并发先保留模板值。不要通过关闭语音、放宽质量或提高并发假装解决缺凭证/缺模型问题。

下一步：[快速开始](QUICKSTART.md)负责安装与首次启动；[故障排查](TROUBLESHOOTING.md)按错误定位。生产审批、签名和回滚只按[部署说明](../deploy/README.md)执行。