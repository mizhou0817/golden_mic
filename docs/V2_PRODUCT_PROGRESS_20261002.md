# 金话筒：产品实现与验证进展 — 2026-10-02

> **2026-10-03 后续交付已完成：** 见[最新开发交付](V2_PRODUCT_DELIVERY_20261003.md)与[证据摘要](../canary_test/artifacts/v2-product-delivery-20261003/summary.json)。后端 812/812、前端 1268/1268、当前核心 6/6 和设计 4/4、真实适配器技术验证及未签名发布包均已有新结果。下文下载失败/适配器未验证/未打包等内容保留为当时事实，不是最新状态；真实语音与生产阻塞仍未解除。

**状态：本轮实现及有界回归完成，最终产品/生产验收未完成。** 用户后续授权“你自行决定完成最终的产品”；本轮不再要求重选模型或 D06。本文覆盖此前决策阶段的“12 秒尚未实现、未下载/安装”现状描述，但不改写其当时事实或旧验收证据。

机器摘要：[本轮验证记录](../canary_test/artifacts/v2-product-progress-20261002/summary.json)。[选型记录](V2_MODEL_SELECTION_20261002.md)和[选型 JSON](v2-model-selection-20261002.json)仍是决策材料，不是已安装模型锁；不得将其 deployable/license 字段改成 true 以绕过门禁。

## 1. 已落地的产品与工具

| 范围 | 实现及边界 |
|---|---|
| D06 素材供给估算 | [mediaInput](../frontend/src/lib/mediaInput.ts) 普通视频采用裁剪后时长的 `max(1, floor(seconds / 12))`；图片一镜，已知含语音视频一镜。未知语音状态单独计数，不伪装成无语音；这是供给估算，不是输出剪辑节奏或新的准入门禁。 |
| 估算证据绑定 | [CreateWizard](../frontend/src/components/CreateWizard.tsx) 仅使用当前任务/能力凭据/文件身份绑定且 ready、probe_ok 的已有回执；不新增请求、ASR、上传或重试。每次渲染重新读取，防止凭据/回执变化后沿用旧 memo。整句连续性警示、服务端阻断、图片 50 MiB 与解码保护不变。 |
| 本地模型包只读校验 | [verify_local_speech_bundle](../deploy/verify_local_speech_bundle.py) 检查显式安装清单、规范路径、链接/硬链接、大小、SHA、有限 JSON、safetensors 布局及部分 CTC 结构；拒绝把决策清单当安装包。不导入/加载模型、不下载、不转换、不激活。正例使用假 ONNX 和微型张量，不能证明真实模型完整性、许可、推理或质量。 |
| 已审核范例准备 | [prepare_sample_bundle](../deploy/prepare_sample_bundle.py) 只接受显式来源、全新输出与权利审核声明；检查规范路径（含 Windows 8.3 别名）、文件身份/摘要、显式 audio_kind 与原声/tts 矛盾。写 registry 在最后，状态为 prepared_not_installed。权利声明不是自动验证；不证明隐私、ffprobe、播放/渲染或内容质量。未提供真实已审核范例包。 |
| 发布文档 | [build_release](../deploy/build_release.py) 将模型决策、机器决策和此前闭环文档加入发布文件集合；历史 required-member 规则未放宽，没有生成或部署真实发布包。本文属于后续工作记录，并非新增运行时必需成员。 |
| 历史测试安全规划 | [run_legacy_validation](../tests/run_legacy_validation.py) 仅 AST 盘点/规划，不导入应用、不执行被盘点测试；拒绝硬链接等路径，`--execute` 明确拒绝。它不是全历史安全执行器，tests_executed=0。 |

新增回归见 [估算](../frontend/scripts/test-v2-sufficiency-policy.mjs)、[模型包](../tests/test_v2_model_bundle.py)、[范例](../tests/test_v2_sample_bundle.py)、[历史盘点](../tests/test_v2_legacy_profiles.py)、[发布完整性](../tests/test_v2_release_integrity.py)。估算新增 41 项；模型 49、范例 29、盘点 27、发布新增 1 项包含在下面后端 735 内，不重复累加。

## 2. 本轮实际执行结果

| 运行 | 结果 | 范围/限制 |
|---|---|---|
| [后端冻结回归](../canary_test/artifacts/v2-validation-20261002-061134-b5000e3abd6243d88dc61da0dcec9e8d/summary.json) | **735/735；505.075336 s；零失败/错误/跳过** | V2 + 八个选定 legacy 模块，非完整历史发现；backend_passed_not_acceptance。292 个源文件零漂移，剩余自有 HTTP listener 0；1005 次原生媒体进程、863 次自有 admission 连接。预期 guard 自检拒绝保留，套件无额外拒绝。 |
| 前端完整 glob 首次 | **1255/1256；1 失败；124736.3148 ms** | 原 SampleView 测试在 synthetic-native-media 阶段编码器 status=null；终端无 ffmpeg 且未设 GM_SAMPLE_FFMPEG。不是已证实的播放器缺陷。失败保留，不能重标通过。 |
| 原 SampleView 聚焦复核 | **16/16；10648.1944 ms** | 使用测试已有 GM_SAMPLE_FFMPEG 指向已安装编码器，源码/断言未改。Edge 154.0.4258.37 实际播放四秒合成 MP4、键盘选句，8 请求，unexpected/pageerror/download 均 0；不是用户素材或真实样片。 |
| 前端完整 glob 最终 | **1256/1256；107778.1817 ms；零失败/跳过/取消** | 串行 Node TAP；GM_DIALOG_BROWSER=1，显式编码器；GM_FINAL_BROWSER/GM_RECORDING_BROWSER 清除。111 个 frontend src/scripts/e2e 源文件零漂移，stderr 0，拥有的进程等待 exit 0。独立下载测试只有三个默认单元被纳入，并未运行其失败的 opt-in 原生用例。 |
| 根目录结果工作台 | **43/43；1915.6119 ms；零失败/跳过/取消** | [独立入口](../scripts/test-v2-result.mjs)，真实 TSX 的 VM/SSR/合成 HTTP，不是原生浏览器或后端链路。 |
| 四项类型检查 | **全部 exit 0** | frontend、workspace E2E、modes E2E、V2 E2E 的 noEmit；不包含 standalone tsconfig.node 或所有 Python 严格类型诊断。 |

最终前端启动器 PID 44972、后端启动器 PID 48648 均通过持有的进程对象等待退出；不以 PID 重查代替原进程完成证据。计数来自实际 TAP/后端摘要，不将不同作用域相加。前端 TAP 仅在本次终端内存中保留，本仓库机器摘要为允许字段的投影，不声称存在完整 TAP 归档。

### 正常 Vite 构建恢复

- A：从仓库根调用构建，exit 0，但 Tailwind content 配置缺失警告；产物保留，不作为首选。
- B：以 frontend 为 cwd 正常 Vite/PostCSS 构建成功，无该警告，42 模块、3 个资产；不是 Lightning 替代构建。没有重新安装 npm 依赖，也不能据此宣称 OneDrive 永久修复。
- [B 构建绑定](../frontend/dist-canary-modes-v2-20261002-product-b/MODE_BUILD_BINDING.json) SHA-256 `6063ae0c6aa3a15675b2bec232939e4946e2764a0ef52dffd6f326fc40db9491`；[资产清单](../frontend/dist-canary-modes-v2-20261002-product-b/ASSET_MANIFEST.sha256) SHA-256 `44a906a6a4337a8b937e8607a67fe9d7c29b1004879773bbeedce540d61b69ab`。
- 最终使用 manifest-only 路径**验证原绑定**成功，不是重新签署。共享 frontend/dist 未替换；本轮未部署日常服务。
- 9 月 V 核心 6/6、W 设计 4/4 是保留的历史结果，**未在本轮 12 秒产品源码上重新执行**。

## 3. 模型实际进展

### CAMPPlus：真实二进制与最小运行已验证，未接入产品验收

官方 sherpa-onnx GitHub asset API，asset id **198893103**，成功下载 **28,281,138 bytes**，SHA-256 **f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11**。这证明下载的官方 GitHub 资产与记录摘要相同；不能反向宣称 Hugging Face 固定 revision 已从源站验证，GitHub 可覆盖资产/无元数据 digest 的限制仍在。

独立 LOCALAPPDATA/GoldenMic/local-speech-20261002 下存放权重、轮子和 Python 3.11.9 runtime，未覆盖应用 venv。安装 sherpa-onnx **1.13.8**、sherpa-onnx-core **1.13.8**、numpy **2.4.2**，pip check 通过。PyPI 官方 JSON 提供 wheel SHA；官方 wheel 传输 TLS 失败，镜像传输字节与官方 SHA 一致后才离线安装。最初环境安装工具走错解释器且失败，不计为成功安装。

实际 sherpa CPU 提取器加载 ONNX，对 **48,000 帧 16 kHz 合成正弦输入**产出 **192 维有限非零 embedding**，exit 0。**只是原始库最小 smoke，不是应用 adapter、真实说话人准确率、性能或许可证最终验收。** 首次直链失败和第二次 19,330,165-byte 超时部分文件均保留，完整下载另用新文件。

### 中文 CTC：明确阻塞

选定 jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn，revision **99ccb2737be22b8bb50dcfcc39ad4d567fb90cfd**。官方 HF/别名访问超时；源 bin **1,276,296,151 bytes** 和 SHA `de031fd4b29e0c0667e5346450fadfe1326c89936b888b59c4ede608db763ee4` 仍只是先前镜像元数据，尚未独立验证源站。

**未下载 CTC、未安装 torch/transformers、未转换/推理、无派生 safetensors 摘要。** 需要可信来源/revision/许可证材料，再做隔离的安全补丁版本 weights_only 转换、逐张量及 logits 等价、离线 AutoProcessor 和真实质量验证。禁止 unrestricted pickle、remote code、随机 CTC head、自动激活或捏造转换 SHA 来解除阻塞。

现有 Seed ASR/TTS 选择不变；C 模式/原声句不补念。实际配置、required/license ack 未改。

## 4. 保留的失败与最终阻塞

1. [独立原生客户端测试](../frontend/scripts/test-v2-final-browser.mjs) opt-in 三次均失败：held-stale-read → stale-draft → native-download-file。最后记录为 **3 单元通过/1 原生失败**，虽出现 download 事件，但 downloadGets=0，文件字节/摘要未验证，后续 DELETE 未执行；cleanupErrors=1、ownedBrowserClosed=true。三个 TEMP receipt 前缀分别 gm-final-browser-zkxdaa、gm-final-browser-R1vths、gm-final-browser-Jw3lzU。已达到三次修复尝试限制，**停止第四次修改/重跑，须明确下一步诊断授权**。不能删断言、用事件代替下载完成，或声称后端 TTL/删除验证。离线原生下载链路可能绕开路由只是待验证假设。
2. CTC 可信来源、安全转换和离线推理未完成；CAMPPlus 尚无真实质量/产品 adapter 验收。
3. 无已审核真实公开范例包；准备工具不等于安装样片，不使用占位片掩盖 unavailable。
4. 当前源码尚无新的核心六项/设计四项完整浏览器验收；完整历史后端安全执行器仍未实现。
5. 真实录音/听审/ASR、性能、Linux/TLS/负载/回滚及公共部署仍需相应资源和验证。本轮不访问实际环境文件、真实任务/数据库/评估媒体，不调用付费提供方，不重启日常 8000，不部署生产。

**因此交付的是已实现且有界回归通过的当前源码与正常构建，不是“最终产品已完成”。** 文档与本摘要写在测试之后；不改旧 receipt/manifest/source maps，也不把新文档字节声称为已测试源码。