# V2 完整设计契约评审 — 2026-09-30

> **2026-10-02 后续实现覆盖：** 12 秒供给估算、已知语音一镜与未知状态提示已实现并回归，见[产品进展及未完成验收](V2_PRODUCT_PROGRESS_20261002.md)。下文决策阶段“仍是 6 秒”仅保留历史含义；旧设计浏览器结果没有在当前源码重跑。

## D06 决策更新 — 2026-10-02

用户已授权自主产品决策，见[模型与产品决策记录](V2_MODEL_SELECTION_20261002.md)。D06 不再等待用户选择：**整句连续性警示和服务端门禁保留；图片 50 MiB 上限保留；镜头供给估算采用 12 秒/镜，已知采访一镜。** 后者当前仍是 6 秒实现，须修改和回归，不能标为已验收。下文原始差异/开放决定及历史证据保留；公开模型元数据选型不等于真实语音验收。

## 当前状态覆盖 — V/W 闭环（保留下文原始发现）

**下文 F01–F08 的“开放/尚未执行/父任务待决定”已被后续实现与有界证据覆盖，不再是当前未修复缺陷。** 当前依据为 [V/W 闭环报告](V2_CURRENT_CLOSURE_20260930.md)及[脱敏机器报告](../canary_test/artifacts/v2-closure-vw-20260930/final-report.json)：V 核心 **6/6**、W 独立设计 **4/4**，零重试/全局错误；不代表所有发现均由这十个浏览器用例逐项验收。

- **F01** 人名/身份 8/12 Unicode 码点验证；**F02** 结构化 401/403/404/410 只读错误，仅 410+`task_gone` 表示到期，保留迟到响应/写回执隔离；**F03** 取消态新建作品，不提供无动作重试/恢复；**F04** 空闲旁白上传入口及真实取消生命周期；**F05** 仅用提供时间的严格只读范例浏览，无假素材；**F06** 任务范围 probe-only 后显式 complete、全部原始时长预算和 `before_asr` 门禁、ASR 失败同伴不阻断其他待完成文件；**F07** 已保存版本目标窗口和准确分域指标、历史不回算；**F08** ≤640px 模式卡单列。
- 已有切片包括选中作品范围的权威 history checks（无逐行 GET 扇出）、候选前三再展开余项、准确 `badRows` 计数、A/B 只读转写。release 显式要求两个新后端模块；模板覆盖 **137/137**，旧缺口不再当前有效。
- 复用冻结证据 **后端629/629（仅V2+选定legacy）、前端1191/1191、独立root43/43、四类类型检查exit0**。原生录音 **17/17单列**，非物理麦克风/ASR；246字节浏览器诊断文件保留未读。U零测试配置失败及V首次关闭缺Origin→404均保留，详见闭环。
- **D06仍须明确决定/披露**：整句连续性警示不等于新增词下划线，6秒/12秒估算及图片50MiB上限不是用户已批准的等价设计。1114条目归属16组是完整静态评审，不是像素/语义全部通过。真实模型/许可/质量、物理麦克风、实际样包503、普通Vite/OneDrive、全历史后端安全runner、Linux/load/TLS/rollback仍有边界。

**历史边界：** 下文§1–8是本次修复之前的静态发现与当时行动请求，原文保留，不作为当前开放清单。当前V/W绑定在文档修改前核对无漂移；受绑定README的后续文档漂移明确披露，绝不重签旧证据。本轮仅文档/新闭环制品补丁，无产品或harness修改、测试、构建、服务或真实数据读取。

## 1. 结论与边界

**完整静态评审已完成；完整产品验收未完成。** 本次把全部设计清单分配到 16 个可追溯功能组，并逐组核对规范、当前实现、现有测试源码和历史证据。没有把字符串命中、源码存在或清单归组写成“功能通过”。发现的问题见 §6；产品修改须由父任务单独授权。

- 唯一新增文档为本文，另新增[静态分组证据](../canary_test/artifacts/v2-design-review-20260930-static-a5191ade/coverage.json)。原规范、原型、旧文档、产品、测试、配置和历史证据未修改。
- 未运行测试、类型检查、构建、浏览器、服务、模型或供应商调用；未读取实际环境配置、数据目录、保留任务或能力凭据清单。只读 Node 用于文本、JSON、计数、哈希与链接校验，未执行产品模块。
- 完整读取[规范正文与附录](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L1-L2068)，包括超长行的完整内容；C-3/C-4 自身的不完整索引不能替代附录实际返回对象/模板。
- **S/T 现在只作历史证据。** 本次有限哈希比较已发现 Workspace、CreateWizard、Processing、ResultWorkbench 与 S 绑定不同，不能继续沿用旧报告“current”作为当前验收。父任务尚未冻结源码；本文哈希只是静态观察点。
- 本文 `S`＝源码契约可定位；`P`＝部分实现/明确差异；`H`＝仅有注明范围的历史运行证据；`D`＝M0 决策或原型偏差；`O`＝模型/运营前置条件；`E`＝外部/真实质量验收。**没有“本次运行通过”状态。**

## 2. 基线、历史证据与计数含义

基线是[既有 design-map](../canary_test/artifacts/v2-validation-20260929-171045-6304bb8b1aec49a780d4b6b365f0dfe5/design-map.json)，不是不存在的另一份 docs 清单。原清单仍保留 `pending_integration` 和 `literal_candidate_not_semantic_proof`，本次没有批量改成 passed。

| 维度 | 实际清单 | 本次处理 |
|---|---:|---|
| 响应式 hooks | 26 | 每个 hook 指派一个功能组，映射到实际 CSS/组件而非要求同名属性 |
| view keys | 357 | 全部指派；原 C-3 标题 311 少 46，原文不改 |
| unique bindings / occurrences | 609 / 892 | 唯一键归属与全部出现位置分别保留；不把 892 当独立功能数 |
| C-6 static copies | 105 | 全部指派；这是作者索引，不是所有动态/条件文案 |
| check codes | 17 | 全部追到规则生产、发布门禁或安全偏差；非 17 条运行通过 |
| 独立清单条目 | 1,114 | 26+357+609+105+17；无未归属、无最终重复归属 |

原清单 SHA-256 `9bdbf0f8cf426c323a3f4dab2f5b9364c777aed20c27df97bdc7d03cd0db6356`；规范 SHA-256 `9dba934b1d64499cdaa990a0eb598c23781f2311866638347b171982610bac26`。当前主文件哈希、精确数组区间和分组摘要见[新静态证据](../canary_test/artifacts/v2-design-review-20260930-static-a5191ade/coverage.json)。未重新签发历史构建绑定。

历史证据的允许结论：

| 证据 | 可以引用 | 不能推出 |
|---|---|---|
| [S/T 历史最终报告](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/final-report.json) | S 6/6、322.094054s、36 个 20px 布局样本无溢出；3 次初版、3 次 apply、7 次成功导出；T 4/4、35.454555s、320/900 两个模态布局样本，元数据 CAS/复制/恢复/无效链接 404 | 当前源码通过；全设计覆盖；真实 410、删除、麦克风、同浏览器缓存续作；像素一致、新闻事实或语音质量 |
| 同报告的 export 范围 | 通过 UI 链接取得的鉴权分块 Range 下载，哈希/探测验证 | 原生浏览器下载按钮/下载完成体验已验收 |
| [冻结回归汇总](../canary_test/artifacts/frozen-auth-validation-20260930-010918-b5f499ffa67f4ea38b58275f6d716911/final-summary.json) | 当时后端 589、前端 902、独立 root result 40、四项类型检查 | 本次重跑、全部历史测试、当前 green；不可与其他运行相加成一个新总数 |
| [S 源码绑定](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/S/current-bindings.json) | 当时的源/测试/构建一致性 | 后续变动自动获得验收；旧停止记录证明当前端口状态 |

历史 S 的四次 Windows 10054 回调是当时事实；后续专门修复/验证由父任务另行归档，本文不根据 T 的零次回调宣布修复，也不把旧风险描述当成当前诊断。模拟 ASR/词时钟/tone TTS + 真实 FFmpeg 不能证明真实说话人辨识、可懂度、无截字、新闻语义或生产稳定性。

## 3. 完整清单归属账本

以下是**审阅责任分组**而非通过率。列依次为 hooks / view / unique bindings / binding occurrences / C-6 copy / checks。

| 组 | 功能契约 | Hk | V | B | Occ | Copy | Q |
|---|---|---:|---:|---:|---:|---:|---:|
| G00 | 共用显示别名、开发/演示排除项 | 0 | 8 | 32 | 264 | 20 | 0 |
| G01 | 页头/侧栏/引导/历史/范例/草稿 | 9 | 29 | 38 | 60 | 15 | 0 |
| G02 | 模式、标题、写稿、句型、五要素 | 5 | 60 | 121 | 128 | 10 | 0 |
| G03 | 上传、转写挑句、容量、充足度 | 1 | 40 | 75 | 78 | 15 | 0 |
| G04 | 原声匹配、说话人、跳切预估 | 2 | 9 | 19 | 20 | 1 | 0 |
| G05 | 效果偏好、隐私、开始制作 | 2 | 22 | 44 | 45 | 8 | 0 |
| G06 | 进度、失败、取消、恢复、续作 | 0 | 30 | 51 | 53 | 4 | 0 |
| G07 | 结果元数据、复制、版本、删除 | 2 | 15 | 26 | 26 | 2 | 0 |
| G08 | 播放器、故事板、结果布局 | 2 | 31 | 42 | 46 | 8 | 0 |
| G09 | 发布检查、下一步、水印状态、指标 | 1 | 15 | 30 | 32 | 2 | 17 |
| G10 | 旁白改字、自录、换镜、删句 | 1 | 35 | 57 | 63 | 10 | 0 |
| G11 | 原声剪短、换段、转旁白、人名 | 0 | 25 | 37 | 38 | 4 | 0 |
| G12 | 九种待应用、提交、恢复 | 1 | 6 | 6 | 6 | 1 | 0 |
| G13 | 导出选项、任务与下载 | 0 | 13 | 11 | 12 | 2 | 0 |
| G14 | 不可访问/到期/清理空态 | 0 | 1 | 2 | 2 | 3 | 0 |
| G15 | 确认、toast、撤销与模态生命周期 | 0 | 18 | 18 | 19 | 0 | 0 |
| **合计** | **无漏项；不是全部通过** | **26** | **357** | **609** | **892** | **105** | **17** |

可复核规则：原数组一基索引为身份，绝不以键名子串猜测实现。view/copy 先分配 G00 明确排除项，再分配唯一功能区间；bindings 除显式共用索引外，以首次模板位置确定主责，全部出现次数随主责记账。37 个绑定键跨模板功能区出现，证据保留每个原始 `source_lines`，可重新展开全部使用组。**这不是把其他使用位置视作已通过**：共享外观由设计系统核对，动作/状态由下表各功能契约核对。

G00 不是未审查兜底桶：`true/false` 是条件占位字面量；`c.*` 等是不同循环的局部别名，`ln.*` 同时用于挑句/文件转写，`sp.*` 用于上传/效果人名输入，`fx.*` 用于匹配/结果修复动作。它们不能按一个同名函数验收。其余 `dev/demo/useSample/addSampleFiles/prefsJson/pPro/pErrorPro/selPro/proposed` 与开发阶段名是显式演示/诊断边界；不能要求生产保留模拟录音、随机 CPM、假进度或“即将支持”的旧文案。

26 个 hook 的实际实现归宿：G01 的 `app/menu/side/narrow-hide/main/wrap/h1/narrow-only/drawer` → [全局样式](../frontend/src/styles.css)与 [Workspace](../frontend/src/components/Workspace.tsx)；G02 的 `steps/line/stack/modes/tline`、G03 `frow`、G04 `mrow/spk`、G05 `toggles/startbar` → [向导样式](../frontend/src/styles/modes.css)；G07 `rtitle/ractions`、G08 `rgrid/strip`、G09 `checks`、G10 `panel`、G12 `pendbtns` → [结果样式](../frontend/src/components/ResultWorkbench.css)。原型要求 data-r 的实现方式已转为 React/CSS class，不等于布局像素相同。

## 4. 规范契约矩阵

每行 ID 是组合需求，不是人为拆出的数百个 pass 标签。源码链接指向契约拥有者；测试链接表示已有可复用验证源码，**本次均未运行**。规范 §0–15、A/B/C 全部有归宿：§0/1→R01；§2→R04/R07/R13；§3/11→R02/R17；§4/6/12→R03/R09/R14/R15/R16；§5→R03–R16；§7→R09/R14；§8→R11；§9→R14；§10→R06/R08/R13；§13–15→R18；A→上述 UI；B→状态/处理程序契约；C→§3 账本。附录 B 的 45 方法索引只作导航，不冒充 45 个运行用例。

| ID / 组 | 规范定位与应满足的契约 | 实际源码 / 现有测试映射 | 静态判定与未闭合范围 |
|---|---|---|---|
| R01 / 全组 | [§0–2](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L5-L45)：无剪辑经验记者；新闻真实；原话不可冒充配音；非现场生成画面明确标识；不引入课堂/账号/Studio | [Workspace](../frontend/src/components/Workspace.tsx)、[QuoteEditor](../frontend/src/components/QuoteEditor.tsx)、[publication](../backend/publication.py)；[shell tests](../frontend/scripts/test-v2-shell.mjs)、[publication tests](../tests/test_publication.py) | S/D/E：四屏产品和安全边界可定位；事实正确、来源授权、辨识/匹配质量必须人工/真实模型验收 |
| R02 / G00+全部 hooks | [§3](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L47-L126)、[A 样式](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L478-L505)：暖色 token、系统字体、边框/阴影、手绘图标、主次按钮、折叠/三类状态、微动效 | [tokens](../frontend/src/components/ui/tokens.css)、[Icon](../frontend/src/components/ui/Icon.tsx)、[modes CSS](../frontend/src/styles/modes.css)、[result CSS](../frontend/src/components/ResultWorkbench.css)；[UI parity](../frontend/scripts/test-v2-ui-parity.mjs)、[progressive design](../frontend/scripts/test-v2-progressive-design.mjs) | S/D/H：token 与可访问图标源码可核对；不是像素比对/全页面截图/字体渲染或主观审美评分 |
| R03 / G01+G14 | [§4–5.1](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L128-L155)、[§6](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L208-L259)：四屏、帮助回第1步、首次引导、字体、服务/真实保存时间、历史100、持久化/刷新 | [Workspace](../frontend/src/components/Workspace.tsx)、[model](../frontend/src/model.ts)、[historyChecks](../frontend/src/lib/historyChecks.ts)、[SampleView](../frontend/src/components/ui/SampleView.tsx)；[shell](../frontend/scripts/test-v2-shell.mjs)、[history checks](../frontend/scripts/test-v2-history-checks.mjs)、[task metadata](../frontend/scripts/test-v2-task-metadata.mjs) | S/P/D：300ms 保存及凭据绑定；历史仅中继当前已验证 checks，未访问行 unknown，无逐行 GET/持久化放行；范例交互缺口 F05，非仅样包安装问题 |
| R04 / G02 | [§2](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L29-L45)、[§5.2](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L156-L163)：A旁白/B混合/C原声；手动>前缀>默认；不同分句规则；切模式不丢稿；C禁 TTS/语速/生成 | [productionModes](../frontend/src/lib/productionModes.ts)、[CreateWizard](../frontend/src/components/CreateWizard.tsx)、[mode rules](../backend/mode_rules.json)；[text rules](../frontend/scripts/test-v2-text-rules.mjs)、[backend text rules](../tests/test_v2_text_rules.py)、[recovered script](../frontend/scripts/test-v2-recovered-mode-script.mjs) | S/D：解析/保存/提交链可定位；不同模式的禁止项不靠隐藏控件代替服务端校验；不能据词面相似度证明新闻语义 |
| R05 / G02 | [§5.2](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L156-L163)、[§10](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L324-L328)：标题40/无末尾句号/非占位；A/B正文20；软3000硬8000；C空稿可去上传但不能开始；五要素、拍摄清单、长句/建议 | [CreateWizard](../frontend/src/components/CreateWizard.tsx)、[mediaInput](../frontend/src/lib/mediaInput.ts)、[drafts](../backend/drafts.py)；[wizard](../frontend/scripts/test-v2-wizard.mjs)、[UI parity](../frontend/scripts/test-v2-ui-parity.mjs) | S/P：手动自检仅证据标记非事实认证，编辑后失效；C不连续文本当前整句波浪线而非精确新增词标记（D06） |
| R06 / G03 | [§5.3](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L165-L171)、[§10](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L324-L328)、[A 上传](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L629-L687)：七类格式、图3秒、500MiB/5GiB/20文件/30min单个/60min总计、备注20、裁剪、重选、5秒撤销；ASR与8行折叠，C选择/取消原始来源且不重排；B仅旁白充足度/C隐藏 | [CreateWizard 上传链](../frontend/src/components/CreateWizard.tsx#L680-L750)、[mediaInput](../frontend/src/lib/mediaInput.ts)、[drafts](../backend/drafts.py)；[design details](../frontend/scripts/test-v2-design-details.mjs)、[v2 drafts](../tests/test_v2_drafts.py) | S/P/D：A/B转写 div、C checkbox label 的新修复已在源码；解码前置缩窄格式支持 F06；照片50MiB额外约束和6秒估算需明确偏差；预估不是实际镜头或免失败承诺 |
| R07 / G04 | [§2](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L29-L45)、[§5.3](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L165-L171)：0.85好/0.6待确认/以下阻断；连续原话独立校验；文件/说话人来源；跨文件或间隔>.5s跳切；空镜/推近/硬切 | [productionModes](../frontend/src/lib/productionModes.ts)、[后端匹配与 QC](../backend/production_modes.py)、[drafts admission](../backend/drafts.py)；[quote ranking](../tests/test_v2_quote_ranking.py)、[quote admission](../tests/test_v2_quote_admission.py)、[production modes tests](../tests/test_production_modes.py) | S/H/O/E：C非连续证据 admission 阻断；相似度不是连续性/身份/事实保证；真实说话人、词时钟、SNR还需模型和听审 |
| R08 / G05 | [§5.4](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L173-L189)：完整 voice/pacing/tone/caption/music+mood/motion/graphics/transitions/color/enhance/generated/lowerthird/jump/quote_caption/custom500；C默认音乐/运动关；云隐私提示；230/265/290±8% | [CreateWizard](../frontend/src/components/CreateWizard.tsx)、[productionModes](../frontend/src/lib/productionModes.ts)、[mode rules](../backend/mode_rules.json)；[preferences](../frontend/scripts/test-workbench-preferences.mjs)、[recovery preferences](../frontend/scripts/test-v2-recovery-preferences.mjs)、[source voice roundtrip](../frontend/scripts/test-v2-source-voice-roundtrip.mjs)、[media tests](../tests/test_v2_media.py) | S/D/O/E：完整偏好与禁项可定位；mine=先AI成片再逐句录制；控制生效的实际像素/音频/真实模型质量不能由表单证明 |
| R09 / G06 | [§5.5](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L191-L193)、[§7](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L261-L280)：十阶段、三模式权重、上传≠接收、实际 elapsed、无 ETA、五类失败、取消、回稿/补素材、明确同任务续作 | [Processing](../frontend/src/components/Processing.tsx)、[Workspace](../frontend/src/components/Workspace.tsx)、[verified retry](../backend/drafts.py#L291-L350)；[processing tests](../frontend/scripts/test-v2-processing.mjs)、[cache retry tests](../tests/test_v2_drafts.py#L987-L1041) | S/P：服务端进度而非本地时钟伪造；缺原话去重计数修复可见；取消态动作 F03；retry已实现且有合成测试，不是全未实现，但同浏览器真实续作仍无验收 |
| R10 / G07 | [§5.6](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L195-L203)、[§9](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L308-L322)：重命名、保持/换模式复制、版本、恢复、删除及保留期 | [Workspace](../frontend/src/components/Workspace.tsx)、[task metadata](../backend/task_metadata.py)、[v2 editing](../backend/v2_editing.py)；[metadata tests](../frontend/scripts/test-v2-task-metadata.mjs)、[copy tests](../tests/test_v2_copy.py)、[editing tests](../tests/test_v2_editing.py) | S/H/D：显示标题CAS不烧录标题/不改TTL；复制能力独立、模式复制经服务器恢复稿；版本号≠修改次数；真实删除/断网歧义/到期边界仍需验收 |
| R11 / G09 | [§8](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L282-L306)、[结果指标](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L195-L203)：17类检查、0不可勾/1确认/2只读、签名变更失效、水印/nextAction、质量指标 | [publication](../backend/publication.py)、[quality](../backend/quality.py)、[parseChecks](../frontend/src/lib/workbenchApi.ts#L226-L254)、[结果检查和指标](../frontend/src/components/ResultWorkbench.tsx#L857-L880)；[publication tests](../tests/test_publication.py)、[quote-editor tests](../frontend/scripts/test-quote-editor.mjs) | S/P/D：服务端 authority 与 revision 绑定，generated知情同意不能抹掉来源/披露/兜底 blocker；指标不是完整设计集合 F07；17码逐组见 §5 |
| R12 / G08+G10 | [§5.6](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L195-L203)、[A 结果](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L783-L907)：原生播放器、时钟、高亮/选中分离、故事板/键盘、下一步、旁白改字、3候选+更多、none/used/abstract、删句与事实提醒 | [ResultWorkbench](../frontend/src/components/ResultWorkbench.tsx)、[result CSS](../frontend/src/components/ResultWorkbench.css)；[独立 result tests](../scripts/test-v2-result.mjs)、[design details](../frontend/scripts/test-v2-design-details.mjs) | S/H：实际媒体 timeupdate，不以句长累加捏造切点；候选新修复可见且 root 旧正则测试已改为行为断言（非本次运行）；候选描述提交并非保证锁定某 shot_id |
| R13 / G10+G11 | [原声/录音面板](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L855-L933)、[声学常量](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L324-L328)：原话不可改字；连续词剪短/真实波形/不扩范围/≥1s；换段/仅B转旁白；人名8/12、2.5s重显间隔60s；录音与字幕核验 | [QuoteEditor](../frontend/src/components/QuoteEditor.tsx)、[native recording](../frontend/src/components/ResultWorkbench.tsx#L610-L697)、[Person](../backend/v2_editing.py#L98-L100)；[quote tests](../frontend/scripts/test-quote-editor.mjs)、[own voice](../tests/test_v2_own_voice.py)、[speech](../tests/test_v2_speech.py) | S/P/O/E：缺完整词证据时禁精剪，保留真实段文本；姓名长度 F01、录音取消 F04；头.12/尾.2/吸附.3/间隙.15/-20LUFS/duck18 的配置≠真实听感/截字验收 |
| R14 / G12 | [§6](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L208-L259)、[§9](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L308-L322)：delete/edit/voice/pacing/replace/trim/take/to_narration/speakers 九种；先记后提交；至少留一句；冲突互斥、离开保护、草稿恢复、版本提交 | [ResultWorkbench](../frontend/src/components/ResultWorkbench.tsx)、[pendingEdits](../frontend/src/lib/pendingEdits.ts)、[API batch validation](../frontend/src/lib/workbenchApi.ts#L260-L285)、[compile_plan](../backend/v2_editing.py#L155-L207)；[pending timers](../frontend/scripts/test-v2-pending-timers.mjs)、[result tests](../scripts/test-v2-result.mjs)、[editing tests](../tests/test_v2_editing.py) | S/H/D：receipt/plan/stage/revision核对，未知写结果不重放；每个内部step增revision非一次点击只增1；转旁白重跑6–10保守重匹配，而非机械照抄7–10 |
| R15 / G13 | [导出模板](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L988-L1001)、[§12 API](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L342-L360)：MP4/GIF/MP3/SRT/PNG、画幅/分辨率/字幕、job进度/取消、当前revision与发布gate、下载 | [ResultWorkbench](../frontend/src/components/ResultWorkbench.tsx)、[workbenchApi](../frontend/src/lib/workbenchApi.ts)、[v2 editing](../backend/v2_editing.py)；[export tests](../tests/test_v2_exports.py)、[download auth tests](../tests/test_v2_export_download_auth.py) | S/H/D：GIF限定片段无声、MP3人声无BGM、PNG当前帧；不允许未通过gate的水印样片导出（M0安全偏差）；原生下载仍待验收 |
| R16 / G14+G15 | [§5.7](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L205-L207)、[A 空态/弹窗](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L953-L1021)、[§12 错误](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L342-L360)：确认/取消/toast撤销，401/403/404/409/413/422/429/503/507；不自动重放 | [Workspace](../frontend/src/components/Workspace.tsx)、[appApi](../frontend/src/lib/appApi.ts)、[服务端 authority](../backend/main.py#L702-L738)；[metadata tests](../frontend/scripts/test-v2-task-metadata.mjs)、[dialog focus tests](../frontend/scripts/test-v2-dialog-focus.mjs) | S/P/D：只有410+task_gone证明到期；404不谎称已清理；结果挂载期间转换缺 F02；原生dialog≠已证明所有焦点/读屏分支 |
| R17 / 全部 hooks | [§11](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L330-L340)：1180/1000/900/640/600断点；结果顺序播放器+故事板→检查→面板；16/17/20px；图标44/chip34；语义状态/空格/方向/删句/Escape/减弱动画 | [全局 CSS](../frontend/src/styles.css#L189-L211)、[向导 CSS](../frontend/src/styles/modes.css)、[结果响应式](../frontend/src/components/ResultWorkbench.css#L265-L314)、[Icon](../frontend/src/components/ui/Icon.tsx)；[UI parity](../frontend/scripts/test-v2-ui-parity.mjs)、[design browser spec](../frontend/e2e/v2/design.v2.spec.ts) | S/P/H：单栏顺序、减弱动画有源码；模式卡600而非640 F08；历史无溢出不证明全控件44px/完整WCAG/窄屏打开details/原生滚动条；大字按钮窄屏仍在页头，不是缺失 |
| R18 / G00 | [§13–15](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L362-L468)、[B 逻辑](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L1025-L1938)、[C 索引](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L1939-L2068)：演示Tweaks、示例常量、主观评分与真实能力分离 | [Processing dev boundary](../frontend/src/components/Processing.tsx#L94-L100)、[SampleView](../frontend/src/components/ui/SampleView.tsx)、[M0 记录](V2_IMPLEMENTATION_20260929.md#L25-L41)；[shell sample tests](../frontend/scripts/test-v2-shell.mjs#L253-L270) | D/O/E：不把SIM/随机质量/演示稿/模拟ASR带入生产；评分4.5不是验收；下一版写稿空模板建议不冒充本次必须实现；样包需运营提供 |

## 5. 全部 17 检查码：从规则到发布权限

共同链路是生产 QC → committed report/quality → [服务端规范化与内容绑定](../backend/publication.py#L72-L152) → [严格 checks 解析/确认资格](../frontend/src/lib/workbenchApi.ts#L226-L254) → [只读/可确认界面](../frontend/src/components/ResultWorkbench.tsx#L857-L874)。字典出现不等于真实媒体已触发测试。

| 检查码（共17） | 实际生产与判定要点 | 状态 / 相关测试源码 |
|---|---|---|
| `QUOTE_NOT_FOUND`, `QUOTE_MATCH_LOW` | [mode checks](../backend/production_modes.py#L1335-L1356)：源证据/score<.6阻断，.6–.85确认；C另有连续性admission，不以高score放行新词 | S；[quote admission](../tests/test_v2_quote_admission.py)、[ranking](../tests/test_v2_quote_ranking.py) |
| `QUOTE_TOO_LONG`, `QUOTE_AUDIO_NOISY`, `QUOTE_TEXT_DIFFERS` | [时长/SNR/文字检查](../backend/production_modes.py#L1357-L1380)：>20警告/>30阻断，SNR<18；差异比例/否定词/连续性；未知SNR不是静音或通过证明 | S/O/E；[speech](../tests/test_v2_speech.py)、[production modes](../tests/test_production_modes.py) |
| `SPEAKER_UNNAMED`, `JUMP_CUT_UNCOVERED`, `MIXED_NO_NARRATION` | [mode checks](../backend/production_modes.py#L1381-L1394)：人名条启用才要求姓名；非空镜跳切提醒；B全原声为提示 | S/D；[production modes](../tests/test_production_modes.py)、[quote editor](../frontend/scripts/test-quote-editor.mjs)；result人名输入边界仍F01 |
| `MATCH_FALLBACK`, `EXPLICIT_ENTITY_NOT_COVERED`, `LOW_MATCH_CONFIDENCE` | [真实匹配计划QC](../backend/quality.py#L153-L226)：fallback/实体缺失error；低置信度按传入阈值warning；词面/模型证据不等于记者事实核查 | S/E；[publication tests](../tests/test_publication.py)；没有重新运行语义质量评测 |
| `FREEZE_PAD_EXCESSIVE`, `VISUAL_CLIP_TOO_LONG`, `NARRATION_SPEAKING_RATE` | [freeze/clip](../backend/quality.py#L364-L403)、[实测语速](../backend/quality.py#L241-L293)：.3s/6.5s及实际TTS rate；旧内部rate码有[显式别名记录](../backend/quality.py#L20-L28)，不能要求所有旧报告迁移 | S/D/E；[media tests](../tests/test_v2_media.py)、[publication tests](../tests/test_publication.py)；实际音视频帧/语速分布待新绑定验收 |
| `generated_media` | [publication](../backend/publication.py)：从来源/披露证据生成显式知情确认；缺披露、兜底error不能被同意勾选消除 | S/D；[publication tests](../tests/test_publication.py)、[confirmability tests](../frontend/scripts/test-quote-editor.mjs#L210-L250) |
| `CONTEXTUAL_BROLL_OVERLAY`, `FACT_CHECK` | [overlay](../backend/quality.py#L144-L152)、[人工事实核对](../backend/quality.py#L463-L479)：实际后端overlay warning 不必等于原型level2；数字/人名/日期提示不是自动事实鉴定 | S/D/E；[publication tests](../tests/test_publication.py)；不降级服务端warning只为匹配原型 |

额外的 `QUOTE_INVALID_RANGE/QUOTE_TOO_SHORT/QUOTE_NONCONTIGUOUS`、不可测响度/真实峰值、缺披露等后端安全检查不能因原型只有17码而删除。`warn` 是允许制作可审看结果，不是允许发布；[原视频下载也调用 publication gate](../backend/main.py#L915-L929)，故页脚恢复下载链接不是已证实的门禁绕过。

## 6. 新发现的产品缺口与父任务决定

以下均是**源码路径证明/静态设计差异，不是本次浏览器复现**。P1＝影响主要编辑/恢复路径，应先决定；P2＝交互/设计覆盖不足。未发现可据此报告的 P0 或鉴权绕过。

| 编号 / 优先级 | 证据、影响 | 建议父任务决策与关闭条件（尚未执行） |
|---|---|---|
| **F01 / P1 — 结果人名长度跨层不一致** | [QuoteEditor](../frontend/src/components/QuoteEditor.tsx#L288-L295)允许姓名80/身份120；[batch parser](../frontend/src/lib/workbenchApi.ts#L210-L214)及[提交验证](../frontend/src/lib/workbenchApi.ts#L282-L285)也接受；[后端 Person](../backend/v2_editing.py#L98-L100)仅8/12。9字姓名或13字身份能留在pending，随后整个apply请求422，牵连其他修改。向导8/12测试不覆盖结果页 | 同意统一结果输入和提交边界为8/12；保留较宽历史读取兼容，不截短服务器身份ID。加入8/9、12/13及混合pending测试，保留输入并在提交前准确提示 |
| **F02 / P1 — 已打开结果到期没有转统一空态** | [reload catch](../frontend/src/components/ResultWorkbench.tsx#L233-L249)仅清context/report+panel error；[宿主 refreshSelected](../frontend/src/components/Workspace.tsx#L862-L878)仅通用错误。对照[后端410 task_gone](../backend/main.py#L724-L733)，mounted result 无终态回调路径；重新从历史打开才走正确 gone 判定。服务端仍拒绝访问，非安全绕过 | 增加带当前selection/generation绑定的结构化不可访问通知；只410+task_gone显示到期，404不谎称清理；pending/凭据不能自动删。覆盖初开、可见性reload、checks、操作后刷新、迟到旧请求和真实TTL跨界 |
| **F03 / P2 — 取消态回稿按钮无动作** | [Processing](../frontend/src/components/Processing.tsx#L43-L44)把cancelled归失败，[动作](../frontend/src/components/Processing.tsx#L111-L116)仍显示onEdit；[Workspace guard](../frontend/src/components/Workspace.tsx#L883-L889)只接受failed。且[后端恢复](../backend/drafts.py#L960-L974)也只接受done/failed。不是简单删前端guard就能修好 | 明确取消后是否支持恢复：若不支持，隐藏/说明无效动作并提供新建；若支持须单独审查服务端接受记录/原输入/配额安全。用实际cancelled持久状态关闭，不能只测failed |
| **F04 / P2 — 录音取消承诺了不存在的上传入口** | [开始录音](../frontend/src/components/ResultWorkbench.tsx#L653-L657)清error；[上传选择器](../frontend/src/components/ResultWorkbench.tsx#L918-L928)仅error时显示；[取消](../frontend/src/components/ResultWorkbench.tsx#L943-L946)只setNotice并说“可点击或上传音频”。正常许可后取消不产生error，所以提示与可操作控件不一致 | 常驻合适的音频上传入口，或取消提示不承诺隐藏动作；验证权限等待/倒计时/录制三阶段取消、迟到grant轨道关闭、无空POST、已有pending保留。不能只模拟MediaRecorder成功回调 |
| **F05 / P2 — 范例不是设计中的可探索结果页** | [规范§5.0](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L149-L150)要求范例结果页；[SampleView](../frontend/src/components/ui/SampleView.tsx#L42-L52)即使有效样包也只有video+有序句子列表，无故事板点选/句面板。样包缺失是O，但装包不能补齐这部分UI | 决定采用只读结果组件/等价教学浏览，或明确缩减设计；不能导入cap/写历史/确认/导出/付费。验证有效样包下视频联动与只读约束，而不只验证503 |
| **F06 / P2 — 宣称格式支持被浏览器解码前置缩窄** | [规范原型文件说明](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L1490)允许浏览器probe失败后服务端转码；实际[probeMedia](../frontend/src/lib/mediaInput.ts#L151-L218)解码失败/超时拒绝，[上传链](../frontend/src/components/CreateWizard.tsx#L694-L738)在uploadFile前停止。可被服务端解码而浏览器不可解码的AVI/MKV等无法进入服务器校验 | 决定安全的服务器probe回退，仍保留字节/时长/配额/真实解码门禁，不能盲信扩展名；或明确缩减“支持”定义。用同一合法文件浏览器拒绝/服务器接受的独立fixture验证，本文不声称全部AVI/MKV都失败 |
| **F07 / P2 — 质量数据面板未覆盖全部设计指标** | [规范指标](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L202-L203)要求句/画面/不同镜头/不确定/凑数/目标语速；[实际面板](../frontend/src/components/ResultWorkbench.tsx#L876-L880)仅句、不同镜头、凑数及速度选项。dev实测CPM不能代替普通用户的目标窗口 | 明确哪些指标保留；有服务端事实才显示，未知必须unknown而非0；不可把visual beat数直接冒充不同镜头。补A/B/C、缺数据、生成来源测试 |
| **F08 / P2 — 模式卡断点偏离640px要求** | [§11](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md#L333-L337)要求≤640单列；[modes CSS](../frontend/src/styles/modes.css)模式卡仅≤600单列，601–640仍三列。效果卡≤1000已单列，为另一较早折叠偏差。不是已证明溢出 | 父任务决定严格640或批准现有分段；在601/620/640、16/20px、完整展开模式卡测可读性和触达，不把320/900历史布局当作这些边界覆盖 |

F01–F04 应先决定行为修复；F05–F08可修复或由产品负责人明确接受设计缩减。不得把“父任务待决定”改写为已豁免。

## 7. M0、原型排除与已修复切片

以[已记录 M0 决策](V2_IMPLEMENTATION_20260929.md#L25-L41)为依据，不自行批准新功能偏差：

| 编号 | 规范/原型与当前边界 | 处理 |
|---|---|---|
| D01 | 原型账号/服务器历史说明 vs 无账号四屏；历史≤100仅展示上限；cap不是URL里的公开身份 | 保留无账号/无课堂/无Studio；不迁移旧数据、不导入他人能力、不因展示100而删凭据 |
| D02 | 403自动重试一次、404→gone、localStorage checked放行等原型便捷逻辑 | 生产不自动重放mutation；401/403/404/bare410不伪称到期；服务端checks/revision决定权限，浏览器勾选不是authority |
| D03 | 原型mine/3秒模拟录音 vs新任务先AI、成片逐句录音 | M0明确非初版整轨录音、非克隆；真正MediaRecorder有3秒倒计时、真实长度/MIME、受控上传。停止即上传是当前行为，规范未明确要求本地试听确认，本文不伪造该缺口 |
| D04 | 72h终态保留、24h闲置draft、30d凭据墓碑、已接受配额不退、队列50而非演示5 | 固定draft TTL不是可随意配置的变量；真实边界/重启/并发/删除另验；不读取实际数据证明运营状态 |
| D05 | 标题模型32 vs规则40；时间/状态 vs附录时间/标题；revision+1 vs多step；原型水印样片可导出 | 采用40、当前时间/标题排序、服务器version_count而非revision猜修改次数；正式gate不通过不能导出；转旁白6–10重匹配、scoped drafts/files/start/apply取代原型端点表 |
| D06 | C“新增词”精确下划线 vs当前整句不连续警示；充足度每12s估镜 vs每6s；图片50MiB额外上限 | 明确未做精确词差异和原估算等价验收；估算标签不能当实际镜头检测。需父任务接受/调整，不隐藏成全部对齐 |
| D07 | §13 Tweaks、SAMPLE/ROWS/SHOT/TRANSCRIPTS、模拟计时/随机CPM、§15主观评分 | 非生产需求；移除/禁用演示模拟是正确边界；合法只读已核验样包与假样例不是同一能力 |

本轮不重复提交旧缺口：历史checks摘要中继、候选3+more、缺失原话去重计数、A/B与C转写语义已在当前源码/相应新测试中定位；release成员和环境模板由父任务独立切片负责，旧“26字段缺失/成员未列入”不能未经重核就当作当前阻断。本文未打开实际环境配置。root result 的旧 `candidates.length<=4` 正则假设已被当前[完整编辑器候选行为测试](../scripts/test-v2-result.mjs#L216-L251)取代；旧失败仍是历史事实，不等于当前运行失败或通过。

## 8. 冻结后的验收门槛（本次不执行）

| 门槛 | 最小有意义的关闭证据 |
|---|---|
| 原生录音 | 真浏览器权限允许/拒绝/无设备/占用/非安全上下文；等待、倒计时、录制取消及迟到许可；正常停止/超时/设备中断、MIME/20MiB/空文件/长度上限；真实录音上传→转写核验→pending→apply→字幕/音轨；首尾音节/时钟/音量听审，区分mock与物理麦克风 |
| 到期/无权/删除 | 当前cap下自然/受控时钟跨TTL的结构化410；裸410/错cap/未知id仍不假到期；打开结果/processing/check/export/后台恢复分别测；删除确认、失败/歧义不丢凭据和pending、重启后墓碑边界，不访问已有用户作品 |
| RetrySame | 同一浏览器从network/transient失败显式单POST续作；服务端核验输入/实现/cache，从首个未完成阶段继续，不重复TTS/ASR/收费/额度；未知provider receipt必须拒绝；断回应只GET调和，不第二次POST；切换作品/旧回包隔离 |
| 设计/无障碍 | 所有四屏、抽屉/确认/导出、展开details/长中文错误/empty/loading/disabled；1180/1000/900/640/600两侧及320/305可用宽、16/17/20字体；原生滚动条/焦点顺序/Tab与ShiftTab/Escape/还原焦点/读屏/减弱动画/触达尺寸；像素比对单独标注，不从0overflow推出 |
| 导出与媒体 | 5格式真实输出元数据/哈希/字幕/来源披露，原生download点击完成；历史revision/dirty/checks失效/权限撤销的拒绝路径。GIF/MP3/PNG不同语义分别验证，不只探测容器 |
| 真实语音、语义与运营 | 用户提供经许可审核的本地speaker/alignment模型和runtime；真实声学身份/词级时间/同音错词/否定词/非连续拼接/SNR边界；匹配实体/新闻事实/生成披露人工复核；ASR≤素材时长×0.3等性能目标独立测量，本文没有达标数据 |
| 生产集成 | 父任务freeze后的源/测试/构建完整绑定及已授权回归；Linux/TLS/预算/磁盘/队列/故障恢复/发布回滚/模型许可由独立外部证据关闭。样包路径与模型可用性不从历史503/缺包推断当前机器 |

**父任务行动请求：** 先确认 F01–F04 的行为修复范围，并决定 F05–F08/D06 是修复还是记录批准偏差；冻结后再安排有边界、具新哈希绑定的验收。本文完成的是全设计静态评审与缺口清单，不是放行上线，也没有擅自执行下一轮测试。