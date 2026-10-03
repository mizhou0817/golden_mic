# 金话筒 V2：模型选型与产品决策 — 2026-10-02

## 选型待办完成确认 — 2026-10-02

用户要求完成当前四项选型待办。本节是该待办的最终状态；**完成的是需求核对、候选核查、决策和验收条件记录，不是权重全部部署或最终产品验收**。

| 待办 | 完成结论 |
|---|---|
| 核对产品需求与模型接口 | 已完成。保留现有云 ASR/TTS；本地接口要求 16 kHz 单声道 PCM、sherpa SpeakerEmbeddingExtractor，以及带中文字符词表和卷积时钟的标准 CTC。C/原声句不允许补念。 |
| 核验候选模型来源与兼容性 | 已完成选型层面的核查并明确证据等级。再次只读计算已取得 CAMPPlus 文件：28,281,138 bytes，SHA-256 与 §2.1 相同；此前原始库合成输入 smoke 为 192 维有限非零向量。CTC 只有固定 revision 镜像元数据兼容证据，源站、转换和实际运行仍 blocked。未把条件兼容标为推理验收。 |
| 确定模型与产品决策 | 已完成，采用 §1 最终选择；保留整句警示/服务端门禁、采用 12 秒普通视频供给估算（已知语音视频/图片各一镜）、保留图片 50 MiB 及解码保护。12 秒规则已实现，下面决策阶段的“待实现”不再是当前状态。 |
| 记录选型及后续验收条件 | 已完成。§4/§6 保留来源、许可证、安全转换、离线加载及真实质量验收要求；最新实现、测试结果及失败见[产品进展](V2_PRODUCT_PROGRESS_20261002.md)。 |

**后续门槛继续有效：** CTC 可信源站/revision/摘要与许可证核验；受限离线转换、逐张量和 logits 等价；冻结 Windows/Linux 运行库矩阵及应用 adapter 离线加载；经授权独立标注集上的质量/边界/拒绝率和 CPU/RSS 性能验证（测试前固定验收阈值）；真实范例/录音和当前浏览器链路验收；最后才是配置启用与回退。没有相应证据即不放行，不自动降低阈值或启用许可声明。

机器选型清单明确标为**历史决策阶段快照**，通过后续记录指针查阅当前实施状态；其 false/null 安全字段不会因待办完成而改成 true。此次收尾不执行新的模型推理、安装、付费调用或生产变更，也不重跑之前已暂停的原生下载测试。

> **后续实施记录：** 用户进一步授权完成产品后，12 秒估算已落地，CAMPPlus 官方 GitHub 二进制已校验并在独立环境完成合成输入 smoke。详见[产品进展](V2_PRODUCT_PROGRESS_20261002.md)。下文“本次只读/未下载”指本决策阶段；CTC、安全转换、真实质量与产品激活仍未完成，选型 JSON 不改为安装锁。

**状态：选型与三项产品决策已确定；权重交付、安装、适配及真实质量验收未完成。**

用户已授权“自行根据产品需求决策并选定模型”。本记录不再把选择模型或 D06 交回用户决定；也不把决策当作运行授权、模型可用、许可证最终放行或测试通过。本次只读取源码和公开模型说明/小型元数据，修改文档；没有下载模型权重、安装依赖、执行模型、修改配置/产品代码、调用付费服务或访问真实任务。

- 需求基线：[新闻视频 UI 交接稿](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md)、[M0 集成记录](V2_IMPLEMENTATION_20260929.md)。不是旧三模式原型。
- 机器可读选型：[docs/v2-model-selection-20261002.json](v2-model-selection-20261002.json)。这是**决策清单，不是已验证的安装锁或自动下载配置**。
- [V/W 验收记录](V2_CURRENT_CLOSURE_20260930.md)和所有旧证据保持原样，不将本决策计入旧通过数。

## 1. 最终选择

| 职责 | 决定 | 原因与边界 |
|---|---|---|
| 主 ASR | 保留现有火山引擎 Seed ASR，资源 `volc.seedasr.sauc.duration` | 保持现有中文转写、真实词时间和回执链；不增加第二次云 ASR 或新付费提供方。资源名不是不可变模型权重版本。 |
| AI 旁白 | 保留现有 Seed TTS 2.0，资源 `seed-tts-2.0` | 仅 A 全文/B 旁白使用；C 不使用 TTS，原声句不允许补念。保留现有音色，不做声纹克隆。 |
| 任务内说话人聚类 | **3D-Speaker 中文 CAMPPlus，16 kHz，sherpa-onnx 导出的 FP32 ONNX 单文件** | 中文采访方向、约 28.3 MB、适配现有 CPU 提取器。只聚类同任务音段，不判断实名，不跨任务建声纹库，不分离重叠人声。 |
| 本地中文强制对齐 | **jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn**，冻结下述 revision；经过受控转换后仅部署 safetensors | 已微调的中文 CTC 头、字符词表及可定位卷积时钟；优先可信来源/可审计结构，不为追求 base 尺寸选预训练底座或来源不清的转换件。large 的 CPU 成本须实测，不能声称已达标。 |
| 其他生成/匹配模型 | 本次不改 | 保留既有 Kimi/火山适配边界；不启用新生成式补画面、额外付费模型、第三方托管声纹服务。 |

现有提供方默认来自 [Settings](../backend/config.py#L46-L89)，不是对实际私密配置的读取。新本地模型是声学证据补充，**不是把主识别/配音迁移到离线**。

## 2. 固定模型标识和获取信任边界

### 2.1 说话人：中文 CAMPPlus

- 上游：[iic/speech_campplus_sv_zh-cn_16k-common](https://modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common)。页面声明模型采用 Apache License 2.0；这是模型页面声明，与 sherpa-onnx 运行库许可证分别记录。
- 选定导出仓库：`csukuangfj/speaker-embedding-models`，revision **`0743f301363dec56491a490f6d6cbc9d67f9a3bf`**。
- [固定 ONNX 制品](https://huggingface.co/csukuangfj/speaker-embedding-models/blob/0743f301363dec56491a490f6d6cbc9d67f9a3bf/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx)：**28,281,138 bytes**。
- 公布的 LFS SHA-256：`f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11`。
- 官方 [sherpa 导出说明](https://github.com/k2-fsa/sherpa-onnx/blob/040afe360a38e25daaa325ce8889abf93ea02609/scripts/3dspeaker/README.md)和[发布工作流](https://github.com/k2-fsa/sherpa-onnx/blob/040afe360a38e25daaa325ce8889abf93ea02609/.github/workflows/export-3dspeaker-to-onnx.yaml)确认该导出路线；不是任意 ONNX 都能交给 `SpeakerEmbeddingExtractor`。
- [GitHub release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models)另有同名、同字节数制品，但其 API `digest` 为空且工作流允许覆盖。**文件名/大小/标签 commit 相同不证明二进制相同**。

### 2.2 对齐：中文 XLSR-53 CTC

- [选定上游](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn)，revision **`99ccb2737be22b8bb50dcfcc39ad4d567fb90cfd`**，模型卡声明 Apache-2.0。
- 原始 PyTorch 权重：**1,276,296,151 bytes**；公布的 LFS SHA-256：`de031fd4b29e0c0667e5346450fadfe1326c89936b888b59c4ede608db763ee4`。
- 配置为 `Wav2Vec2ForCTC`；16 kHz；3,503 词表项，其中 3,441 个单 CJK 字符；`pad_token_id=0`。**有限词表不保证覆盖新闻人名、地名、数字、英文或方言**。
- 卷积核 `[10,3,3,3,3,2,2]`、步幅 `[5,2,2,2,2,2,2]`：320 样本/20 ms 步进、400 样本/25 ms 感受野。它们是架构时钟，不是“20 ms 对齐准确率”的证据。
- 该 revision **没有 safetensors**，也没有独立 tokenizer-config 文件；有词表、特殊 token 映射和特征预处理配置。必须验证锁定 Transformers 版本下的 `AutoProcessor` 离线加载；若须补标准 processor 元数据，记录为派生包修改并验证 token ID 完全不变。
- 部署目标为该上游的**审计派生 safetensors 包**，不是原始 pickle 权重。派生包尚不存在，输出哈希必须保持未知，不能填造或声称可直接上线。

### 元数据证据等级

本轮 Hugging Face 源站请求未成功取得有效元数据；revision、LFS 摘要和文件清单来自 **hf-mirror.com 的公开镜像**。ModelScope 说明、sherpa 官方文档/GitHub 导出路线直接读取成功。机器清单保存来源和小型文本文件的响应字节 SHA-256。

上述权重 SHA-256 是**镜像公布值，不是本地计算值，也不是源站已独立认证值**。获取权重前必须从源站/维护者可信渠道交叉核对 revision、LFS 指针和许可证；下载后再计算完整二进制摘要。镜像只用于研究，不成为自动下载或许可放行依据。固定路径禁止浮动 `main/latest`。

## 3. 不选的方案

| 候选 | 排除理由 |
|---|---|
| `TencentGameMate/chinese-wav2vec2-base` | 检查到 `Wav2Vec2ForPreTraining`，模型卡明确音频预训练、无 tokenizer；不能以 `AutoModelForCTC` 随机新建的头冒充已训练中文对齐器。 |
| `kehanlu/mandarin-wav2vec2-aishell1` | 卡片有非商业/学术使用限制且采用自定义 `Wav2Vec2ForEspnetCTC`；不符合本产品直接部署边界。 |
| Meta MMS_FA | [官方模型许可证](https://docs.pytorch.org/audio/stable/generated/torchaudio.pipelines.MMS_FA.html)为 CC-BY-NC 4.0；没有单独许可，不作为商业部署默认。 |
| `wbbbbb/wav2vec2-large-chinese-zh-cn` | 虽有 safetensors，但模型卡与预训练架构/来源元数据不一致；不把格式安全误当作模型正确。 |
| `zainulhakim/240615-wav2vec2-ASR-Chinese` | 标准 base CTC/safetensors，但检查到的词表是拉丁字符，没有当前逐汉字 lookup 所需的中文字符。 |
| Whisper、Paraformer 或任意“有时间戳”模型直接替换 | 不符合现有逐字符 CTC/卷积帧时钟接口；不是此次最低风险集成路径。 |

这些是有界候选比较，不是对所有模型的普遍排名。没有据此宣称 large 在本机最快或最准确。

## 4. 安全打包和启用决定

### 一次性 CTC 转换，不在应用启动中转换

1. 先独立验证来源、原始大小/哈希及模型/训练数据相关许可；固定所有输入小文件。模型卡标注 Apache-2.0 不替代完整权利审核。
2. 仅在另行授权的离线、无凭据、无项目写权限、限内存/CPU 的一次性环境转换。使用经当前安全公告审核并锁定的 PyTorch；[CVE-2025-32434](https://github.com/pytorch/pytorch/security/advisories/GHSA-53q9-r3pm-6pq6)表明 ≤2.5.1 即使 `weights_only=True` 也有漏洞，2.6.0 是该漏洞的修复下限，**不是本次推荐安装版本或“此后永远安全”声明**。
3. 显式受限 `weights_only=True`、CPU 加载，仅接受预期 tensor state-dict；禁止 `weights_only=False`、自动允许自定义 globals、远程代码、隐式联网或异常时放宽加载。受限 unpickler 不是 OS 沙箱。
4. 记录每个 tensor 的名称/shape/dtype/原始字节哈希，safetensors 回读须逐 tensor 位级相等。任何旧权重归一化键迁移都须独立解释；不得静默丢头、随机初始化或忽略 missing/unexpected keys。
5. 验证转换前后确定性 CPU logits、CTC head、token ID、processor 和真实音频时钟；重复转换验证可复现性。记录工具版本、来源及输出 SHA-256；保留模型卡/许可证/派生说明。
6. 运行应用仍维持 [现有本地加载限制](../backend/providers/local_speech.py#L157-L190)：`local_files_only=True`、`trust_remote_code=False`、`use_safetensors=True`。生产服务不接收原始 pickle 权重。

### 运行策略

- 选 **CPU、单 worker、受控串行本地声学作业**；不要求 GPU，不增加 Celery/Redis，不提前启用量化。转换和加载峰值内存可能远高于约 1.28 GB 权重大小，必须测量。
- speaker 保持现有单线程提取、最多取中间 20 秒；CTC 窗口最多 30 秒、4M trellis 上限、真实 16 kHz mono PCM，不能靠补静音/均分字时通过。
- 保留 `TaskSpeakers` 的 **0.75 下限**和现有 CTC 置信度门槛，本次不借更换模型降低门槛。任务内首次锚点和窗口策略仍需真实域内标注集校准；阈值是当前规则，不是 CAMPPlus 已校准的质量保证。
- [当前 CTC 接入](../backend/mode_pipeline.py#L850-L892)只在自录旁白 ASR 段缺词时间时补齐；已有真实词时间不覆盖。**不能宣称所有上传采访/所有原声句已经接入本地 CTC**；扩展路径须单独实现与回归。
- 对齐不是事实核验。缺字、低置信度、混说、否定词/同音替换或无法确定的身份保持明确不可用/人工复核；不得删除未知字、近音冒充原话或悄悄改稿放行。
- 开发/隔离合成环境保留 `LOCAL_SPEECH_REQUIRED=false`；正式 V2 生产发布策略确定为 **`true`**，且两种能力都完成许可证、来源、真实加载和质量门槛前不发布。本次不改变实际配置；旧环境兼容默认不被偷偷收紧。
- `LOCAL_SPEECH_LICENSE_REVIEWED` 仍为 false，直至两个实际交付包及其来源/语言/许可证完成审核。[预检](../backend/readiness.py#L111-L151)依旧只是前置条件检查，`inference_verified=false` 不能改成推理通过。
- 后续把本地运行库作为独立可选依赖组锁定并验证 Windows/Python 3.11 与目标 Linux/Python 3.12；本次不捏造兼容版本、不更新现有锁文件或全局环境。

## 5. D06 产品决策，已作出而非等待用户选择

| 项目 | 决定 | 实现状态 |
|---|---|---|
| C 原话变化提示 | **保留整句连续性警示和服务端阻断**。精确 diff 将来只可辅助解释，不取代整句安全判断；不仅新增字，删词、重排和拼接也可能改变原话。 | 当前行为接受为明确的安全设计偏差，不再要求逐新增词下划线作为首版关闭条件；不声称与原稿像素等价。 |
| 拍摄充足度 | **按每 12 秒估一镜**，已知有人说话的采访按一镜、图片按一镜；保留“仅作估算”和真实服务端分析。它是建议，不是新的计费/开始授权门禁。 | **待实现和回归**。当前 [estimateSufficiency](../frontend/src/lib/mediaInput.ts#L122-L130)及[文案](../frontend/src/components/CreateWizard.tsx#L1201-L1205)仍为 6 秒；说话信息需使用已有权威上传回执，不猜测未知状态。 |
| 图片上传 | **保留独立 50 MiB 上限**和现有像素/解码/GIF结构护栏，普通视频上限不能套到图片。 | 接受当前安全收紧；不修改既有文件/历史记录或放宽到 500 MiB。 |

估算方向必须正确：这里计算的是**供给镜头数**。同一段 60 秒普通视频，6 秒除数估 10 镜，12 秒估 5 镜；原算法 `possibleShots / sentenceCount` 下，6 秒更容易报“够用”，并非更保守。也不能把成片期望切镜速度与原素材可提供的不同镜头数混为一谈。[交接稿附录](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L1570)已有 12 秒/采访一镜方向。

本次仅决策，不趁文档更新修改前端；因此不能把 D06 整体写成“实现和验收均完成”。

## 6. 后续执行顺序和完成定义

1. **内部可实施**：实现 12 秒/已知采访一镜估算及文案，增加边界回归；准备小文件/权重验收清单、可选依赖锁定方案和只读模型包验证器。
2. **需资源操作授权后**：源站交叉校验、下载固定权重、隔离转换、安装冻结运行库；不读取真实任务或触发付费推理。若源站、许可或转换不能确认，保持 blocked，不能自动换陌生模型。
3. **真实语音验收**：使用经授权、有人工转写/说话人/字边界标注的中文采访集，覆盖噪声、短句、首尾字、数字、专名、同音和否定词、换麦克风、跨文件同人及重叠说话。测试集与阈值调参集分离；无足够样本不能声称统计达标。
4. **预注册量化验收门槛**：在查看测试结果前固定误合并率/误拆分率、字符覆盖及边界误差、峰值 RSS、冷/热 RTF 和端到端预算；现有规则阈值不替代质量指标。报告错误与拒绝率，不能只统计成功片段或拿上游 ASR WER 当本产品字时精度。
5. **启用门槛**：离线加载、转换一致性、真实说话人/CTC/取消资源回收、实际设备录音和目标 Linux 性能均有证据，再修改受限部署配置并做回退演练。未通过不降精度/不换词补时/不宣称正式可用。

本次已消除“模型选哪家/三项设计怎么定”的决策等待；仍保留合理的安装下载、真实素材/听审、最终许可证和生产变更边界。既有样包、标准 Vite、完整历史安全测试和生产验收工作不因选型自动完成。