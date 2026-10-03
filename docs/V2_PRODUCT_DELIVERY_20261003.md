# 金话筒 V2：本地开发交付 — 2026-10-03

**结论：本轮可在当前环境完成的代码修复、构建、回归、合成浏览器验收及离线发布包校验已完成；尚不能声明真实语音质量和生产上线验收完成。** 不恢复课堂/账户功能，不扩展旧 Studio。此前选型不重开；Seed ASR/TTS 保留，C 模式不合成配音。

权威证据：[脱敏摘要](../canary_test/artifacts/v2-product-delivery-20261003/summary.json)、[核验回执](../canary_test/artifacts/v2-product-delivery-20261003/verification-receipt.json)。[10 月 2 日记录](V2_PRODUCT_PROGRESS_20261002.md)保留为历史，不覆盖旧失败或重新签署旧绑定。

## 1. 本轮实现

- **真实声纹输出保护：** [应用适配器](../backend/providers/local_speech.py)验证维度稳定性、数值类型、有限值和非零范数；拒绝无效 embedding，不用归一化、补零或任意最短时长掩盖问题。实际 CAMPPlus 对部分短输入产生非有限向量的问题已复现并修复。
- **必需语音模式完整性门禁：** [模型包桥接](../backend/local_speech_bundle.py)和[启动检查](../backend/readiness.py)复用完整模型包验证器。`local_speech_required=true` 且生产环境时必须提供 `LOCAL_SPEECH_BUNDLE_MANIFEST_PATH`；必需开发模式显式提供清单时也检查。Settings 的 `local_speech_required` 默认仍为 false；非必需模式不运行此模型包检查，不代表模型已激活。
- **配置契约：** [开发模板](../.env.example)与[生产模板](../deploy/golden-mic.env.production.example)覆盖 138/138 Settings 字段；仅模型清单路径允许精确空字符串。生产模板 required=true，实际用户环境未修改。摘要/路径完整性不代表许可证、来源真实性、推理兼容性或质量。
- **发布能力：** [构建器](../deploy/build_release.py)允许显式选择新建的独立前端构建，归档内映射为标准静态目录，不覆盖共享构建；拒绝覆盖已有包。发布校验新增两个必需模型包模块锚点。
- **范例链路验证：** [六项集成测试](../tests/test_v2_sample_runtime.py)覆盖真实 FFmpeg、准备工具、实际 API、解码和 Range。前两轮失败是测试误用了内部报告 schema、未考虑中间件强化缓存头；未为通过测试修改产品契约。仍没有安装已审核公开范例，缺包 503 保持真实。
- **原生下载测试修复：** Edge 原生下载绕过页面路由，之前的纯内存假域名传输不能证明下载成功。新的[单次本机拒绝代理](../frontend/scripts/final-download-fixture.mjs)不转发上游，严格绑定导出请求/版本/令牌/URL；[客户端测试](../frontend/scripts/test-v2-final-browser.mjs)校验实际下载文件字节、SHA 和清理。产品下载属性未删除，失败断言未放宽。

## 2. 当前源码实际验证

| 范围 | 本次结果 | 边界 |
|---|---|---|
| [后端](../canary_test/artifacts/v2-validation-20261002-182630-933889911e8640298799a02de4ee7cb4/summary.json) | **812/812**，377.030871 秒 | V2 + 选定 legacy，非全部历史测试；300 源文件运行期无漂移，零失败/错误/跳过/剩余自有监听器。目录日期为 UTC。 |
| 前端串行全 glob | **1268/1268**，113930.7142 ms | dialog/final/recording 原生 opt-in 全开，114 文件运行期无漂移；零失败/跳过/取消，进程 exit 0。TAP 来自父执行终端，未声称完整原始日志归档。 |
| 独立结果工作台 | **43/43**，1216.5483 ms | 单独入口，不能再计入前端全 glob；合成 VM/SSR/HTTP。 |
| TypeScript | **4/4 exit 0** | frontend、workspace E2E、modes E2E、V2 E2E；不是全仓 Python 严格类型清零。 |
| [核心浏览器](../canary_test/artifacts/v2-product-delivery-20261003/core-browser-summary.json) | **6/6**，309279.03 ms | 当前 A/B/C 创建、编辑应用、七次导出；36 布局样本零溢出；零重试、全局错误和安全计数。 |
| [独立设计浏览器](../canary_test/artifacts/v2-product-delivery-20261003/design-browser-summary.json) | **4/4**，28250.636 ms | 原生折叠、元数据 CAS/模态框焦点、复制/恢复、404；两种模态框布局。`baseSixPassed=false` 正确保留。 |
| 正常 Vite/PostCSS | **成功，42 模块、3 资产** | [当前构建绑定](../frontend/dist-canary-modes-v2-20261003-final-a/MODE_BUILD_BINDING.json)，48 输入；没有 Lightning 替代或覆盖共享静态目录。 |

核心与设计分别使用全新临时任务/七个合成输入、模拟 ASR/音调 TTS 和真实 FFmpeg，不是实际语音、内容真实性或人类听审。核心七次下载使用有界鉴权 Range 校验；独立原生点击下载由另一测试证明，不能混为同一端到端用例。

两台临时主机均以匹配实例和精确 Origin 关闭：stopped、lifespan/shutdown/provider 完成及输入/源码/测试稳定标志均 true，denied egress=0，8787 已空闲。核心浏览器 exit 0；其启动器句柄随终端清理丢失，**不声明启动器 exit 0 或 stderr 0**。设计启动器精确等待 exit 0、stderr 0。

## 3. 实际 CAMPPlus 技术验证

[显式 test-only 探针](../tests/local_speaker_runtime_smoke.py)在独立 Python 3.11.9、sherpa/core 1.13.8、NumPy 2.4.2 环境中运行真实应用适配器。28,281,138-byte 权重 SHA 为 `f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11`。

合成 PCM 得到 192 维有限非零向量；重复、长片中间窗口与实际裁剪结果差值均 0。修复后 1/16/80/160/320 样本输入拒绝，400/800 样本有限；这是观测点，**不是推导出的时长阈值**。探针把所有 `SpeechUnavailable` 标为 `rejected_too_short`，其中包含无效输出拒绝，不能据标签误述根因。

构造器仅在 test-only 作用域临时替换 `_require`，嵌入计算前恢复；前后 `license_reviewed=false` 均拒绝。未激活产品模型或伪造许可审批。实际回执 SHA：`9887fe11d7b1012a972c400ff64f5a6812555b1f91d659ecda3d3097b92468fb`。此 Windows 独立 wheel 锁不等于应用基础环境或 Linux 兼容证明。

## 4. 已生成的未签名发布包

位置见[发布投影](../canary_test/artifacts/v2-product-delivery-20261003/release-projection.json)。位于系统 TEMP 下的独立 gm-release-20261003-dc657964d11d4524a8756e833fcc006a 目录，**20,811,857 bytes、118 个成员**，SHA-256：

`807fc0243115d481e1ed997f4cfd83f9e65de9302a26f6d295691f51864e8530`

真实字体、前端输入绑定、离线 UV 锁/导出、生产依赖与 SBOM 字节比较、归档/校验和验证通过。118 个打包文件与冻结输入一致，310 个输入运行期稳定。启动器/构建器/UV 检查退出 0；无输入 FFmpeg 导入探测退出 1 属于该探测行为，不是渲染失败。独立核验只复查允许的 304/310 发布输入，不重读受排除的环境/媒体条目。

首次发布启动器因过宽 `socket.*` 测试保护拦截本机 hostname 查询而失败，尚未运行构建器；失败保留。后续仅在内存使用固定合成 hostname，未放开外连/DNS，不跳过产品校验。Python 防护和 `UV_OFFLINE` **不是原生 OS 沙箱**。

包内是写本文前的冻结源码和历史文档；本文及 README/进展页的后续说明未重新打入该包。不将后写文档冒充包内内容，也不为此改写旧绑定。包未签名、未部署；TEMP 不是永久发布存储。

## 5. 剩余外部验收条件

1. **CTC 未交付：** 固定 revision 的官方源站仍超时；未下载/安全转换/加载模型，未安装 torch/transformers，无派生 safetensors 摘要。不能用镜像元数据冒充源站验证，不能启用不安全 pickle 或随机 CTC head。选择仍见[模型决策](V2_MODEL_SELECTION_20261002.md)。
2. **真实语音质量未验收：** 需要授权且标注的语料、录音设备、说话人/对齐准确率、真实听审和性能验证。合成结果不能解除这一条件。
3. **公开范例未安装：** 需要批准的内容、隐私和使用权材料；准备/测试能力不等于已批准样片。
4. **生产未验收：** Linux 依赖/模型兼容、签名发布、TLS、负载、排空/回滚和实际运维资源尚需执行；历史原生崩溃不因本次绿色结果而被宣布根治。
5. **完整历史测试执行器仍未完成：** 当前可信执行范围明确为 V2 + 选定 legacy，旧测试盘点不是全部运行；全设计/像素/WCAG 也未宣称穷尽。

本轮没有修改实际环境/任务数据、评估媒体或数据库，没有付费调用、日常 8000 重启或生产部署。旧原生下载聚焦回执的 255 项保护清单有 5 项被本轮后续开发更新，已在[投影](../canary_test/artifacts/v2-product-delivery-20261003/focused-native-projection.json)列出；自有三个下载测试文件仍匹配。不得把旧清单改成当前无漂移，当前整轮前端和浏览器结果独立报告。