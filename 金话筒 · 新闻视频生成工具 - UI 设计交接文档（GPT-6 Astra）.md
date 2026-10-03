# 金话筒 · 新闻视频生成工具 —— UI 设计交接文档
> 供 GPT-6 Astra（或任何实现模型 / 工程师）分析、理解并据此实现真实产品。
> 生成日期：2026-09-28 · 基线：`金话筒 · 新闻视频生成工具.dc.html`（可运行的高保真原型，235 KB）· 同包附 `金话筒 · 新闻视频生成工具（独立版）.html` 可离线打开体验。

## 0 · 如何读这份文件

- **§1–§13 是结构化说明**：产品定义、三种制作模式、设计系统、状态机、逐屏功能、数据模型、流水线、检查规则、待应用机制、限额、响应式、后端对照与差距、Tweaks。
- **附录 A/B 是完整源码**：A = 模板（`<x-dc>` 内 HTML：内联样式 + `{{ }}` 绑定 + `<sc-if>`/`<sc-for>` 控制流），B = 逻辑（`class Component`：state → 动作方法 → 检查规则 → `renderVals()` 视图模型）。附录 C 是自动提取的索引。源码头部的 `/* 实现说明 */` 是给工程师的浓缩版，与本文一致。
- **原型 ≠ 生产代码**：它是设计参考，展示意图中的外观与行为。实现时应在目标代码库中按其既有模式重建；所有后端行为在原型里是前端模拟（`SIM`、`setTimeout`），真实接口对照见 §12。
- **标注约定**：`[需新增后端]` = 当前 API 不支持；`[纯前端可做]` = 现有 API 已支持、只需前端实现；`[演示态]` = 仅为演示原型存在（「填入示例」按钮、Tweaks），生产不保留。
- **命名约定**：界面词 ↔ 代码词见 §2 与 §7 表；阶段对象 `STAGES[n]` 有 `devName`（开发名，只在 devNotes 下显示）与 `label`（界面名）两套名字；「句」= `row`，旁白句 `kind:"narration"`，原声句 `kind:"quote"`。
- 模板语法速查：`{{ path }}` 只做点路径取值；`<sc-if value="{{ bool }}">` 条件渲染；`<sc-for list="{{ arr }}" as="x">` 循环；事件属性用 React 驼峰名（`onClick="{{ handler }}"`）；`style` 是内联样式；`data-r` 是响应式钩子（§11）；`data-screen-label`/`data-doc` 标出屏幕与其职责。

## 1 · 产品定义

金话筒是给零剪辑经验的**记者**用的工具：交上「文稿 + 采访素材」，AI 自动做出可直接播放的新闻视频（1080p 16:9 MP4，带配音或原声、字幕、台标、片尾板）；记者在结果页按「句」检查与修改，检查通过后导出。

一条闭环（三种模式共用）：**写稿 → 传素材 → 选效果 → 开始制作（10 阶段）→ 结果页逐句检查/修改（先记下、再一次性应用）→ 检查通过 → 导出/分享**。

设计原则：
1. 不让记者学剪辑：一切以「句」为单位，不出现轨道、关键帧、时码输入。
2. 新闻真实性优先：原话不改字、AI 示意画面必须标注、凑数画面阻断发布、字幕与声音一致。
3. 先记下、再一次性应用：结果页任何修改先进待应用列表，点「应用修改」才重做；每次重做产生一个新版本。
4. 每一步都告诉记者「现在该做什么」：结果页顶部黑条 `nextAction` 是主线，所有新状态都接入它。
5. 失败可恢复：稿子和素材永远保留；失败页给出 1–2 个明确动作；能预检的错误前置到向导里。
6. 第一屏只做一个决定：首次进入有三步引导卡，模式默认折叠为最简单的「AI 配音」，高级项默认收起。
7. 界面不出现校园/学段特指词，也不出现英文技术词（ASR/TTS/diarization → 转写/配音/分说话人）。

## 2 · 三种制作模式（`task.mode` / `S.mode`）

| 代码值 | 界面名 | 一句说明 | 文稿是什么 | 素材人声 | AI 配音 | 画面来源 |
|---|---|---|---|---|---|---|
| `voiceover` | AI 配音 | 稿子全由 AI 读，素材只出画面 | 记者写的旁白稿 | 压低为环境声 | 全部句子 | 每句按语义匹配镜头 |
| `mixed` | 旁白 + 原声 | 旁白 AI 读，人说话的地方直接用现场原声 | 旁白 + 受访者原话（标「同期」） | 原话句从素材剪出 | 只读旁白句 | 旁白句配空镜；原声句用说话人镜头 |
| `original` | 只用原声 | 稿子就是采访原话，直接剪出来 | 从采访转写中挑出的原话 | 全部使用 | 无 | 按稿子顺序剪出片段并拼接 |

关键规则：
- 「句」分两种：旁白句 `narration`（AI 读或自录）与原声句 `quote`（从素材剪出）。A 全旁白，C 全原声，B 混合。
- **原声句不能改字**。只能：剪短（按词选段，只缩小区间）、换一段（同一句话的其他说法/机位）、改成旁白（仅 B）、删掉。原声句字幕按实际说的话显示。
- 原声句的「匹配」= 稿句对齐到素材转写的时间段（语音识别 + 强制对齐）。相似度阈值：≥ 0.85 对上了 · 0.6–0.85 不太确定（需人工勾选）· < 0.6 没找到（阻断）。第 1 步、第 2 步、结果页三处一致。
- 句子类型判定优先级：用户手动 chip（`typeMarks[text]`）> 行首「同期：」「【同期】」「姓名（身份）：」> 默认旁白。原声行只按句末标点（。！？）拆段；旁白按「，。；！？」拆句。
- C 模式无配音 → 无播音速度、无语速检查；**跳切遮盖**：相邻原声句来自不同文件或同文件间隔 > 0.5 s → 1 处；`jump_cut_cover` = `broll` 自动插空镜（默认，无空镜自动降级 `zoom`）/ `zoom` 轻微推近 / `hard` 硬切。
- B/C 有**说话人**：`speakers{spkId:{name,role}}`，名字来自稿子「姓名（身份）」或用户填写；未命名人名条显示「受访者」；人名条首次出现 2.5 s，同一人隔 ≥ 60 s 再出现时重显。
- A 模式素材人声不自动变同期声，只提示「想用原声？切到旁白 + 原声」。
- 智能建议（每种一次，`suggestOff`）：A 下有 ≥ 1 句稿子与原话相似 ≥ 0.85 → 建议切 B；C 下有句没找到 → 建议切 B。切换模式不清空内容，`setMode()` 只重设模式默认偏好（C：音乐与推近默认关）。

## 3 · 设计系统（原型内联样式提取；新增界面只能用这些值）

风格：「纸质工作台」——米色纸面 + 28px 淡格线、深棕墨色 2px 实线描边（主卡片）/ 1.5px 浅线（次级卡片与输入框）、硬阴影（4px 4px 0，只用于主卡片与主 CTA）、微旋转「印章」标签与便签、大圆角、粗字重。不用渐变主色、不用模糊阴影（浮层除外）、不引入新字体、不引入图标库——图标一律是**手绘风内联 SVG**（§3.5），极小的开合/关闭控件仍可用字符 × ▾ ▴。色板在原「牛皮纸」色相上提亮一档以贴近年轻用户审美；所有文字级用色对比度 ≥ 4.5:1（TEAL 白底 5.4:1，RED 白底 5.0:1，INK 纸面 10.6:1）；GOLD 只做底色/描边/光环，其上文字一律 INK。

### 3.1 色板
| 名称 | 值 | 用途 |
|---|---|---|
| INK | `#4a3626` | 主文字、主描边、主按钮底、深色面板（页头、黑条、句面板头）、选中 chip 底 |
| MUTED | `#75645a` | 次级文字、说明、未选中文字 |
| LINE | `#dccfbb` | 浅描边、虚线分隔、未选中 chip 边、禁用按钮底与边 |
| TEAL | `#237f4a` | 成功/通过/原声/我的配音、上传区、开关开启、进度条填充、绿色链接 |
| RED | `#c94a2c` | 阻断/错误/危险动作/凑数画面/未匹配 |
| GOLD | `#f2b632` | 强调：Logo 圆、选中光环 `0 0 0 3px`、「应用修改」按钮底、小贴士边、标记点、播放进度、跳切竖线、toast 动作按钮 |
| 纸面 | `#f9f3e6` | body 背景；格线 `rgba(74,54,38,.03)` 每 28px 横竖各一条 |
| 白卡 | `#fff` | 卡片、输入框、未选中 chip |
| 米底 | `#f4ecdf` | 悬停、运行中行、候选选中、说明块、模式卡选中底 |
| 绿底 | `#e7f3ea` | 通过/原声/已改 浅底、上传区底 |
| 红底 | `#fbeee6` | 阻断/错误/危险开关 浅底 |
| 黄底 | `#fdf2d8` | 小贴士、维护横幅、不确定、过期文件 |
| 轨底 | `#f0e7d8` | 细进度条底、波形条底 |
| 占位/分隔 | `#e4d9c6` / `#ebe2d1` | 缩略图占位条纹、行分隔线 1px |
| 暗面浅字 | `#cfc1ad` | 深色面板里的次级文字 |
| 琥珀 | `#8a5a0b` | 「上次选的文件」过期提示 |
| 状态绿点 | `#6aa84f` | 服务正常 |
| 遮罩 | `rgba(74,54,38,.45)` | 弹窗/抽屉遮罩 |
| 人名条底 | `rgba(74,54,38,.9)` | 播放器左下人名条，左 4px GOLD 边 |

### 3.2 字体与字号
- 字体栈 `"Noto Sans SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif`；数字/时码/编号/百分比用 `ui-monospace,Menlo,monospace`；全局 `font-variant-numeric: tabular-nums`。
- 基准 16px（≤600px 为 17px）；「大字」开关把 `html` font-size 设为 20px，所有 rem 随之放大（新增尺寸一律用 rem）；默认关，仅无障碍用途。
- 字号阶梯（rem）：3.25 制作中大百分比 · 2.5 空态图标 · 1.75 步骤标题 · 1.5 页面标题 · 1.375 结果页标题/引导卡标题 · 1.25 弹窗标题/Logo · 1.125 失败标题 · 1.0625 开始制作/上传区标题/句文本 · 1 正文输入 · 0.9375 主按钮/模式卡名 · 0.875 次按钮/正文说明 · 0.8125 次级说明 · 0.75 元信息/标签 · 0.6875 图例/徽章 · 0.625 dev 标签。
- 字重：900 标题/主 CTA/印章/编号；800 按钮/标签/强调；700 次按钮/文件名；600 句文本；400 说明。行高：正文 1.5，句文本 1.65，输入框 1.75，按字选段 1.9。

### 3.3 描边、圆角、阴影、动效
- 描边：`2px solid INK` 主卡片/主按钮/模式卡选中；`1.5px solid LINE` 次级卡片/输入框/次按钮/文件行/作品卡；TEAL/RED 2px 状态描边。虚线：`3px dashed TEAL` 上传区；`2px dashed GOLD` 小贴士；`2px dashed LINE` 分隔；`1px dashed RED/TEAL` dev 标签。
- 圆角 px：18 主卡片/弹窗/播放器/引导卡 · 16 次级卡片/黑条/浮条 · 14 大按钮/输入框/作品卡/小贴士 · 12 按钮/开关卡/说明块/文件行/模式卡 · 10 小按钮/故事板格/列表行 · 8 印章/迷你按钮 · 6 缩略图/徽章 · 4 dev 标签/角标/主题条 · 999 chip/pill · 50% 圆。
- 阴影：主卡片 `4px 4px 0 #4a3626`；弹窗 `6px 6px 0 #4a3626`；当前作品卡 `3px 3px 0 #4a3626`；选中光环 `0 0 0 3px #f2b632`；浮层 `0 10px 30px rgba(74,54,38,.3)`；播放键 `0 6px 24px rgba(0,0,0,.35)`。其余无阴影。
- 动效（helmet `@keyframes`）：`gm-pop`（透明度 0→1 + 上移 8px，.2–.25s）弹窗/浮条/toast/引导卡；`gm-spin 1s linear infinite` 运行中图标；`gm-blink` 转写「正在听…」；`gm-check`（缩放 .4→1.25→1，.4s）步骤完成与勾选打勾；`gm-rise`（上移 6px 淡入，.25s）文件行入场；`gm-wave`（scaleY .35↔1，1.1s 循环）制作中话筒声波；开关 .2s；进度条 width .2–.3s。无其他动画。

### 3.5 手绘内联 SVG 图标集
统一规格：`viewBox 0 0 24 24`（Logo/制作中话筒为 40），`fill:none`，`stroke-width 2.4–3.2`，`stroke-linecap/linejoin: round`，颜色随语境（INK 在金/白底上，白在 INK 底上，TEAL 在上传区）；一律 `aria-hidden="true"`，语义靠旁边文字。建议在目标代码库做成 `Icon name size color` 组件，路径直接从附录 A 复制。

| 图标 | 用在哪 | 形状 |
|---|---|---|
| 话筒 | Logo（36px GOLD 圆内，rotate −8°）、模式卡「AI 配音」、制作中大百分比左侧（64px GOLD 圆，两侧 4 条声波 `gm-wave`） | 实心圆角话筒头 + 弧形托架 + 立杆 + 底座 |
| 话筒 + 对话框 | 模式卡「旁白 + 原声」 | 小话筒 + 右侧带尾巴的圆角对话框 |
| 对话框 | 模式卡「只用原声」 | 圆角对话框 + 两条文字线 |
| 云上传 | 上传区（40px TEAL） | 云轮廓 + 向上箭头 |
| 铅笔 | 小贴士标题 | 斜置铅笔 + 笔尖分割线 |
| 箭头 | 黑条「现在该做什么」左圆 | 粗右箭头 |
| 打勾 | 步骤条完成态、发布前检查已勾项（`gm-check`） | 单笔勾 |
| 场记板 + 话筒 | 首屏引导卡插画（132×110） | 倾斜场记板（金色条纹顶）+ 倾斜话筒 + 三个彩色点 |

### 3.6 组件规格
| 组件 | 规格 |
|---|---|
| 页头 | 高 60px，INK 底白字；左 ☰（44px，仅 ≤900px）+ Logo（36px GOLD 圆内话筒 SVG）+「金话筒」900/1.25rem；「大字」pill；「怎么用」pill（重开引导卡，≤900px 收进抽屉）；右 服务状态 pill + 「✓ 已自动保存 HH:MM」 |
| 侧栏 | 宽 260px，右边框 2px INK，底 rgba(255,255,255,.55)；「＋ 新作品」48px INK 按钮；「我的作品」+ 「按时间 ↕」；作品卡 14px 圆角白底 1.5px 边（当前 INK + 硬阴影），标题 0.875rem/800 单行省略，第二行 状态标签 + 模式小字 + 「改了 N 次」+ 日期 + 「还剩 N 天」；底部「看一条范例作品」虚线按钮 + 保留期说明 |
| 引导卡 | 首次进入第 1 步显示（`introDismissed` 持久化）：白底 18px 圆角 2px INK 硬阴影 padding 22 24，`gm-pop`；左 132×110 插画；标题「三步，把你的稿子变成一条新闻视频」1.375rem/900；「1 写稿 · 2 传素材 · 3 选效果 · → AI 剪好 · 通常几分钟」；按钮「开始写稿 →」INK 44px + 「先看一条范例成片」白底绿字 |
| 步骤条 | grid `1fr auto 1fr auto 1fr`；圆 34px：完成 TEAL 底白打勾 SVG（`gm-check`）/ 当前 INK 底白数字 / 未到 白底 LINE 边；连线 40×2px |
| 步骤卡 | 白底 18px 圆角 2px INK 硬阴影 padding 26 24 20；左上「第 N 步」印章（INK 底白字 0.75rem/800，rotate −2°/1.5°/−1°）；标题 1.75rem/900 + MUTED 副标 + 右侧「填入示例（演示）」`[演示态]` |
| 模式选择卡 | grid 3 列 gap 8（≤640px 1 列）；12px 圆角 2px 边 padding 12 14；34px INK 圆内模式 SVG + 界面名 0.9375rem/800；说明 0.8125rem MUTED；「适合：…」0.75rem；「AI 配音」卡 GOLD 印章「最简单，先用这个」；选中 INK 边 + `#f4ecdf` 底 + 右上 18px GOLD 圆 ✓；`aria-pressed`。默认折叠：只显示「AI 配音」+ pill「更多制作方式：旁白 + 原声 / 只用原声 ▾」（`modeMore`）；选了 B/C 后三张常显 |
| 小贴士 | 黄底 2px dashed GOLD 14px 圆角 padding 12 14，rotate ±.5°；铅笔 SVG +「小贴士」900 + 一句话 + 「更多/收起」绿字 |
| Chip | 999px；min-height 44（选项）/ 40 / 36 / 34（句子类型）；选中 INK 底白字，未选 白底 LINE 边；句子类型 chip「原声」TEAL 底白字 |
| 开关卡 | 12px 圆角 padding 10 12；开 TEAL 边 + 绿底，关 LINE 边白底；开关 40×22 圆钮 18；危险项（AI 示意图）RED 虚线边 + 红底 |
| 上传区 | `label` 3px dashed TEAL 绿底 14px 圆角 padding 26 16；云上传 SVG 40px + 「把视频或照片拖进来，或点击选择」TEAL 800 + 格式说明 |
| 文件行 | grid `64px minmax(0,1fr) auto`，入场 `gm-rise`；缩略图 64×36；名称 700 单行省略 + 元信息（时长/大小/有人说话 · 转写 n 句）；坏文件 RED 边红底，过期 GOLD 边黄底；默认只露名称/元信息/×，「备注 / 只用片段 ▾」30px 迷你按钮展开备注 + 「只用 a–b 秒」（`fileAdv`）；× 44px → toast「已移除「文件名」」+ GOLD「撤销」5 s |
| 转写展开列表 | 文件行内米底 10px 圆角；每行 时码等宽 0.75rem MUTED · 说话人徽章 · 文本 0.8125rem；C 模式每行左侧复选框（accent TEAL）；> 8 行折叠 |
| 匹配卡 | 与充足度卡同样式；标题「原话都拍到了吗？」(B) / 「原话对上了吗？」(C) + 摘要；行 grid `24px minmax(0,1fr) minmax(0,1fr) auto`（≤1000px 两列）；状态标签 ✓ 对上了 TEAL/绿底 · ○ 不太确定 INK/黄底 · ✕ 没找到 RED/红底 + 等宽百分比；未匹配行展开「改成旁白」「删掉」 |
| 说话人行 | grid `34px 1fr 1fr auto`；34px INK 圆序号 · 姓名输入 · 身份输入（32px 高 8px 圆角 LINE 边）· 「出现 n 次 · s 秒」；未命名 GOLD 边黄底 |
| 主/次按钮 | 主：高 48（向导）/52（开始制作）/46（弹窗）/42（结果页），INK 底白字 800；禁用底与边都变 LINE。次：白底 1.5px LINE 边 700；强调次按钮 2px INK 边 800；危险次按钮 RED 字 + RED 边 |
| 黑条（现在该做什么） | INK 底白字 14px 圆角 padding 12 18；左 34px GOLD 圆箭头 SVG；小标 0.75rem .7 + 主句 1rem/800；右 白底 INK 字 42px 按钮 |
| 播放器 | 16:9 18px 圆角 2px INK 硬阴影；左上主题条；样片水印（斜条纹 + 旋转白字）；人名条 left 16 bottom 100；字幕居中 bottom 60；中央 76px 播放键；底部 52px 渐黑控制条 |
| 故事板 | 概览条高 6 每句一段宽∝时长；格条高 104（大字 124）min 104px 10px 圆角；左上编号，右上状态点（TEAL 原声/我的配音 · 空心 不太确定 · INK AI 生成 · RED 凑数）；跳切处两格间 4px GOLD 竖线；选中 INK 边 + GOLD 光环；删除态透明度 .55；≤600px 横向滚动 + scroll-snap |
| 发布前检查 | 卡片 2px INK 硬阴影；「x / n 完成」+ 细进度条；项目 grid `repeat(auto-fit,minmax(280px,1fr))`；每项 32px 方框（阻断 RED「!」/ 通过 TEAL 打勾 SVG `gm-check` / 待勾 白底 INK 边）+ 文本 + 右侧「去看 / 去改字 / 去听 / 去剪短 / 去换 / 去填」 |
| 句面板 | sticky top 12（宽屏）；头部 INK 底「第 N 句 / 总」+ ← → + 徽章组（原声 · 姓名 TEAL 底 / 旁白 · AI 配音 / 旁白 · 我的配音 …）；旁白句 ①改这句话/我来读 ②换个画面（候选默认露 3 个 + 「还有 N 个镜头 ▾」）③不要这句；原声句 ①剪短这句 ②换一段（+ 改成旁白，仅 B）③不要这句；说话人姓名/身份输入 |
| 按字选段控件 | 词块 inline padding 2 4 radius 4 行高 1.9；保留范围 TEAL 底白字，未保留 MUTED + 删除线；8px 波形条（`#f0e7d8` 底 TEAL 选区）；起止时码；「◁ 0.2s」「0.2s ▷」；「记下剪短」「算了」；说明「只能剪短，不能改字」 |
| 待应用浮条 | sticky bottom 12，INK 底 16px 圆角；「记下了 n 处修改：…」+ 说明；「全部撤销」透明白边 + 「应用修改 → 第 N 次修改」GOLD 底 INK 字 900 |
| 弹窗 / Toast | 遮罩 + 白卡 min(440/540px,100%) 18px 圆角硬阴影 6px；按钮右对齐「先不要」+ 确定（危险 RED / INK）。Toast 底部居中 pill INK 底白字 2.8s；带动作的 toast（`toast(msg,{label,fn})`）右侧 GOLD 底 INK 字 34px 按钮，停留 5 s |
| dev 标签 | 等宽 0.625rem/700，`1px dashed` RED「需新增后端」/ TEAL「纯前端可做」；仅 `devNotes` 开启时渲染 |

## 4 · 信息架构与状态机

### 4.1 屏幕（`S.screen`，模板 `data-screen-label`）
| screen | 标签 | 内容 |
|---|---|---|
| `create` | 新建作品 | 引导卡（首次）+ 三步向导（`S.step` 1/2/3）：写稿 → 传素材 → 选效果 → 开始制作 |
| `processing` | 制作中 | 上传进度 → 排队 → 10 阶段（按模式改文案，始终 10 个）；失败卡 |
| `result` | 结果 | 播放器 + 故事板 + 发布前检查 + 句面板 + 待应用浮条 + 导出弹窗 |
| `gone` | 作品不存在 | 72 小时清理后的空态 |
| 覆盖层 | — | 我的作品抽屉（≤900px）、导出/分享弹窗、确认弹窗（含输入/选项）、toast |

### 4.2 任务状态 `task.status`
`uploading`（上传中 N%）→ `queued`（排队中）→ `running`（制作中 N/10）→ `done`（结果页；门禁 检查通过 / 待修改 N 处 / 待确认 N 项）或 `failed`（失败卡，`errorKind`: network / transient / shortage / qc / quote_missing）。删除 = 取消并永久删除。72 小时后 → `gone`。

### 4.3 向导门槛
- 第 1 步 → 第 2 步：稿子非空且标题合法（首行 ≤ 40 字、无句末标点、非占位符「（写一个标题）」）；C 模式允许空稿进第 2 步，但开始制作前必须 ≥ 1 句且全部「对上了」。
- 第 2 步 → 第 3 步：≥ 1 个可用文件、无坏文件、无过期文件；C 模式另要求没有「没找到」的原声句；充足度不够时二次确认弹窗。
- 开始制作：每小时 2 次提交配额；点击后进入 `processing`。

## 5 · 逐屏功能说明

### 5.0 首次进入引导
- 第 1 步顶部引导卡：三步说明 + 「开始写稿」+「先看一条范例成片」（`openSample` 打开内置范例作品的结果页）。关闭后持久化 `introDismissed`；页头「怎么用」随时重开（`showHelp` 同时回到第 1 步）。

### 5.1 页头与侧栏（全局）
- 页头：Logo/产品名；「大字」（`toggleFont`）；「怎么用」；服务状态 pill（正常 / 维护中）；「✓ 已自动保存 HH:MM」`[纯前端可做]`。
- 侧栏「我的作品」：按时间/状态排序；作品卡 = 标题（≤32 字）+ 状态标签（已清理 / 待修改 N 处 / 待确认 N 项 / 检查通过 / 没做成 / 上传中 N% / 排队中 / 制作中 N/10）+ 模式小字 + 「改了 N 次」+ 日期 + 「还剩 N 天」；点卡 `openTask`。历史只在本机 localStorage（≤100 条）`[需新增后端：服务端历史/账号]`。≤900px 变抽屉。

### 5.2 第 1 步 · 写稿
- 模式选择卡（默认 `voiceover`，默认折叠；`modeCards[].pick` / `toggleModeMore`）。
- 编辑框（`script`，首行 = 标题）+ 字数 `n / 3000 字`（软上限 3000，硬上限 8000）；标题提示 `titleLabel`，可一键修正 `fixTitle`。
- 五要素自检（何时/何地/何人/何事/为何：正则 + 手动点选 `elemMarks`）；长句提示（> 30 字）。
- **A**：拍摄清单（每句一条拍摄建议，可复制）。
- **B**：副标「第一行是标题；人说的话写成「同期 姓名（身份）：…」」；句子清单每句「旁白/原声」chip（`typeMarks`）、原声句左 3px TEAL 边；统计行「旁白 n 句 · 原声 m 句 · 预计成片 mm:ss」；单句原声 > 60 字提示断句；全旁白/全原声 → 小贴士建议切模式。
- **C**：placeholder「把从采访里挑出来的原话贴到这里，一句一行。还没有稿？先去第 2 步传素材，转写完回来挑句子」；「从转写挑句子」面板（`appendToScript`/`removeFromScript`）；每句匹配状态 ✓/○/✕；素材里没说过的新增词 RED 下划线（`hasNewWords`）。
- 智能建议小贴士（§2）。

### 5.3 第 2 步 · 传素材
- 拖放/选择上传（mp4/mov/avi/mkv/jpg/png/gif，图片定格 3 秒）；逐文件校验；浏览器读时长与首帧（`probe`）；容量条。文件行默认精简，「备注 / 只用片段 ▾」展开；删除可撤销。
- 预转写（`asr: pending → done`）`[需新增后端 /pretranscribe]`；元信息「有人说话 · 转写 n 句 · 说话人 k 位 / 正在听… / 这段听不太清」，可展开转写列表。
- 拍摄充足度卡（B 只按旁白句算并注明「原声 m 段 ≈ s 秒」；C 隐藏）+「每句话都拍到了吗？」。
- **A**：含人声文件提示条「这个模式不用人声…想用原声？切到「旁白 + 原声」」。
- **B/C**：匹配卡（`matchRows`）；说话人卡（`speakerNames`）。**C**：跳切预估行（`jumpLine`）；「从转写挑句子」入口。
- ≤900px 显示「手机传大视频很慢」提示。

### 5.4 第 3 步 · 选效果（`prefs`）
| 选项 | A | B | C |
|---|---|---|---|
| 谁来配音 `voice` ai/mine | 显示 | 「旁白谁来读」+「原声句不配音」 | 隐藏 |
| 播音速度 `pacing` | 显示 | 显示 | 隐藏 |
| 整体感觉 `tone` | 显示 | 显示 | 显示 |
| 字幕样式 `caption_style` | 显示 | + 「原声句字幕按实际说的话显示」 | 同 B |
| 背景音乐 `background_music` + `music_mood` | 默认开 | 默认开 + 「原声段落音乐自动压低」 | 默认关 |
| 画面慢慢推近 `motion_effects` | 默认开 | 默认开（只作用旁白句） | 默认关 |
| 台标片尾板 / 首尾淡化 / 颜色统一 | 开 | 开 | 开 |
| 现场原声降噪 `enhance_speech` | 开 | 开 | 开 |
| AI 示意图 `generative_fill`（危险项，默认关） | 显示 | 显示（只对旁白句） | 隐藏 |
| 人名条 `lower_third` | 隐藏 | 开关默认开 + 说话人列表 | 同 B |
| 跳切怎么处理 `jump_cut_cover` | 隐藏 | 可选 | chip 组；无空镜时「自动插空镜」灰掉 |
| 原声段的字幕 `quote_caption` | 隐藏 | 显示 | 同 B |
| 给 AI 剪辑师的话 `custom_instructions` ≤500 字 | 显示 | 显示 | 显示 |
- 卡底部汇总行 `summaryLine`；「开始制作」52px；说明「稿子和素材会上传到 AI 服务（含第三方云）处理」；`devNotes` 开时显示 `preferences` JSON。

### 5.5 制作中
- 标题 + 版本印章；大百分比卡（64px GOLD 圆话筒 + `gm-wave` 声波；上传 % / 排队位 / 进度 %）+ 粗进度条；排队/制作中时安抚「通常需要几分钟。可以先去做别的——做好后会出现在左边「我的作品」里。」（`pBusy`）；上传行；10 阶段列表（`stageDef(n,mode)`；C 第 7 阶段「整理原声」）；进度按 `STAGE_W` 加权。
- 失败卡：原因 + 建议 + 动作（`pFailActions`）；`quote_missing`：「有 k 句原话在素材里没找到」。「取消并删除」（确认弹窗）。无 ETA、无队列位置 `[需新增后端]`。

### 5.6 结果页
- 标题行：标题 + 门禁标签 + 版本 chip（可回看/「恢复这个版本」）+ 模式小字；操作：重命名 / 复制一份（可「保持 / 改为 …」模式）/ 删除 / 导出分享。
- 黑条 `nextAction`；播放器（未通过时样片水印「样片 · 还有 N 处要处理 / N 项要确认」；原声句人名条预览；字幕预览；空格播放/暂停）。
- 故事板（`cells`）：点选、← →、Delete 标记删句、跳切金线、角标（待换/改字/录音/删/凑数/AI 画的/剪短/换段/转旁白）。≤1000px 选中后 `scrollPanelNarrow()`。
- 发布前检查（§8）：阻断项不可勾只能处理；待勾项点方框勾选；提示项只读。
- 句面板：旁白句 ① 改这句话（`pendEdit`）/ 我来读（`record` → `pendVoice`）② 换个画面（指令 + 候选镜头 → `pendReplace`；失败分级 none/used/abstract）③ 不要这句（含事实提醒）。原声句 ① 剪短（`trimWords` → `pendTrim`）② 换一段（`altTakes` → `pendTake`；改成旁白 → `pendToNarration` 仅 B）③ 不要这句；说话人 → `pendSpeakers`。
- 待应用浮条：`pendingTitle`；「全部撤销」/「应用修改 → 第 N 次修改」（`applyPending`）。换画面与删句互斥。
- 质量数据（`metrics`）：句数/画面数/不同镜头/不确定/凑数/语速目标（C 不显示语速）。
- 导出弹窗：格式 mp4/mp3/gif · 画幅 · 清晰度 · 字幕；未通过门禁只能导出带水印样片（`gateMode=block` 时禁止）。

### 5.7 作品不存在 / 弹窗 / toast
- `gone`：「这条作品已清理」+「从历史移除」+「新作品」。确认弹窗统一走 `S.confirm={title,body,ok,okBg,input?,choices?,onOk}`。

## 6 · 数据模型

```jsonc
// 组件 state（节选，完整见附录 B constructor）
{
  "screen": "create|processing|result|gone", "step": 1,
  "mode": "voiceover|mixed|original", "script": "", "files": [],
  "typeMarks": {"句文本": "narration|quote"}, "speakerNames": {"spkId": {"name": "", "role": ""}},
  "prefs": { /* §5.4 全部键 */ },
  "tasks": {"t-xxx": Task}, "history": ["t-xxx"], "currentId": "t-xxx",
  // 结果页本地待应用状态（applyPending 翻译成 plan.steps）
  "deleted": [], "pendReplace": {"rowId": "指令或镜头描述"}, "pendEdit": {"rowId": "新文本"},
  "pendVoice": {"rowId": {"cpm": 0}}, "pendPacing": null,
  "pendTrim": {"rowId": {"start": 0, "end": 0}}, "pendTake": {"rowId": "takeId"},
  "pendToNarration": [], "pendSpeakers": {"spkId": {"name": "", "role": ""}},
  "checked": {"checkKey": true}, "selectedId": 0, "viewRev": null,
  "exportOpen": false, "exp": {"fmt": "mp4", "aspect": "16:9", "res": "1080p", "sub": "std"},
  // 上手/减负（纯前端）
  "introDismissed": false, "modeMore": false, "fileAdv": {"文件名": true}, "candMore": false,
  "toastAct": {"label": "撤销", "fn": "…"},
  "quotaUsed": 0, "quotaResetAt": 0, "bigFont": false, "histSort": "time|status"
}
```

```jsonc
// Task（S.tasks[id]）
{
  "id": "t-xxx", "title": "首行标题 ≤ 32 字", "mode": "voiceover|mixed|original",
  "createdAt": 0, "startedAt": 0, "doneAt": 0,
  "status": "uploading|queued|running|done|failed", "progress": 0, "current": 1, "queue": 0, "revision": 0,
  "stages": [{"n": 1, "status": "pending|running|done|failed", "msg": "", "elapsed": null}],
  "plan": {"steps": [{"op": "初版", "run": [1,2,3,4,5,6,7,8,9,10]}], "idx": 0},
  "script": "", "prefs": {},
  "files": [{"name": "", "sec": 48, "mb": 212, "img": false, "inSec": 0, "outSec": 48, "note": "",
             "speech": false, "asr": "pending|done|failed", "transcript": [{"t0": 3, "t1": 9, "spk": "spk1", "text": ""}]}],
  "speakers": {"spk1": {"name": "王红", "role": "市集主办方"}},
  "rows": [{
    "id": 0, "kind": "narration|quote", "s": "句子文本", "d": 3.2, "c": 0.73,
    "beats": [{"shot": 5, "t": "叠字(可选)"}],
    "fallback": false, "gen": false, "spoken": "实际说的话", "mine": false, "myCpm": 0,
    "overlay": "日期|文字|机构名|null", "miss": ["稿中提到但没拍到的实体"], "edited": false, "replaced": null,
    // 原声句专有
    "file": "主办方采访.mp4", "t0": 12.0, "t1": 21.0, "spk": "spk1", "snr": 24, "missing": false,
    "jump": "file|gap|null", "cover": "broll|zoom|hard|null", "coverShot": 3, "trimmed": false, "take": null
  }],
  "versions": [{"rev": 0, "op": "初版", "rows": []}], "replaceFails": {"rowId": {"kind": "none|used|abstract", "text": ""}},
  "errorKind": "network|transient|shortage|qc|quote_missing|null", "error": "", "errorPro": "", "badRows": [], "shortBy": 0,
  "upTotal": 0, "upDone": 0, "gone": false
}
```

本地持久化：`localStorage[SAVE_KEY]` 保存 tasks/history/checked/pend/draft/introDismissed（`persist()`/`restore()`）；刷新后草稿（含模式、句子类型、说话人）自动放回。

## 7 · 十阶段流水线与重做规则

阶段对象 `STAGES[n] = {n, devName, label, doing}`：`devName` 是开发名（只在 devNotes 下显示），`label` 是界面名，`doing` 是「正在做什么」。B/C 的覆盖在 `STAGE_MODE`，`stageDef(n,mode)` 取最终值。

| n | devName | A label / doing | B | C |
|---|---|---|---|---|
| 1 | 上传校验 | 检查素材 / 看看每个视频能不能打开、格式对不对。 | 同 A | 同 A |
| 2 | 同期声识别 | 听素材里的声音 / 找出有人说话的片段。 | 听采访 / 把每段话转成文字，分清谁在说。 | 同 B |
| 3 | 镜头切分 | 切分镜头 / 把每段视频切成一个个镜头。 | 切分空镜 / 把没人说话的画面切成一个个镜头。 | 找空镜 / 把没人说话的画面留下来，等会儿遮跳切。 |
| 4 | 画面理解 | 看懂每个画面 / 给每个镜头写一句「画面里有什么」。 | 同 A | 同 A |
| 5 | 文稿分句 | 给稿子分句 | 分旁白和原声 / 标出哪些是人说的话。 | 对稿子 / 把稿子每句和转写对上。 |
| 6 | 语义匹配 | 给每句话找画面 | 配画面、对原话 / 旁白找空镜；原话在采访里找到它说的那几秒。 | 找原话 / 定好剪切点。 |
| 7 | 配音与同期声 | 配音 | 配音 / 朗读旁白；原声段降噪、调音量。 | 整理原声 / 不用配音；把每段原声剪出来，降噪、调音量。 |
| 8 | 字幕生成 | 做字幕 | 旁白按读的字，原声按实际说的话；加人名条。 | 按实际说的话打字幕，加人名条。 |
| 9 | 视频渲染 | 合成视频 | 把空镜、原声、配音、字幕拼成一条片子。 | 按稿子顺序拼接原声，遮住跳切。 |
| 10 | 完成 | 质量检查 | 检查画面、原声和字幕对不对得上。 | 检查每段原声剪得干不干净。 |

- 阶段权重 `STAGE_W`：A 全 1；B `[1,2,1,1,1,2,1,1,1.5,1]`；C `[1,3,1,0.5,1,2.5,1,1,2.5,1]`。进度 = 已完成权重 / 总权重（无 ETA）。
- 重做规则（每个 step = 一次后端调用，`revision +1`，`versions[]` 存快照）：首版 1–10；删句/改字/自录/调语速/剪短/换段/改成旁白（remix）7–10；只改说话人 8–10；仅重新合成 9–10；换画面（replace-shot，每句一次）6、9、10。
- 失败类型 `errorKind`：`network` · `transient` · `shortage`（`shortBy`）· `qc`（`badRows`）· `quote_missing`。

## 8 · 发布前检查规则（`checksFor(t,rows)`，与后端 quality 规则同源）

级别：0 阻断（必须处理，不能勾）· 1 待勾 · 2 提示。门禁：级别 0 全部处理且级别 1 全部勾选 → 「检查通过」才能正常导出；未通过时播放器叠样片水印。`gateMode=block`（Tweak）时后端把阻断项直接判为制作失败——这是现有后端行为，本设计**提议**改为 warn 让记者在结果页自己处理 `[需后端调整]`。

| 代码 | 级别 | 模式 | 触发 | 界面文案 | 按钮 |
|---|---|---|---|---|---|
| MATCH_FALLBACK | 0 | 全部 | `row.fallback` | 第 i 句没找到合适画面，用了凑数画面——换个画面或删掉 | 去看 |
| EXPLICIT_ENTITY_NOT_COVERED | 0 | 全部 | `row.miss` 中的词仍在句中 | 第 i 句提到“X”，素材里没拍到它——去掉这个词，或换成拍到它的画面 | 去改字 |
| FREEZE_PAD_EXCESSIVE | 0 | 旁白句 | 单镜头时长 < 句时长 − 0.3s | 第 i 句的画面只有 a 秒，句子 b 秒——画面会定格 c 秒。换个更长的画面，或拆成两句 | 去看 |
| generated_media | 0（知情勾选） | A/B 旁白句 | `row.gen` | 第 i 句是 AI 画的示意画面，我知道它不是现场 | 去看 |
| QUOTE_NOT_FOUND | 0 | B/C | `row.missing` 或 c < 0.6 | 第 i 句是原话，但素材里没找到——改成旁白（B）、删掉，或回去传含这句话的素材 | 去看 |
| QUOTE_TOO_LONG | 0 / 1 | B/C | > 30s 阻断；> 20s 待勾 | 第 i 句原声 x 秒，太长了——剪短或断成两句 | 去剪短 |
| LOW_MATCH_CONFIDENCE | 1 | 旁白句 | c < 0.5 | 第 i 句的画面我看过了，是对的 | 去看 |
| VISUAL_CLIP_TOO_LONG | 1 | 旁白句 | d > 6.5s 且单镜头 | 第 i 句太长了（x 秒），一个画面撑不住——拆成两句 | 去改字 |
| NARRATION_SPEAKING_RATE | 1 | A/B 旁白句 | 语速超出所选播音速度 ±8% | 有 n 句读得偏快/偏慢（目标 a–b 字/分）——拆句或换个速度重新配音 | 去改字 |
| QUOTE_MATCH_LOW | 1 | B/C | 0.6 ≤ c < 0.85 | 第 i 句对上的原话不太像，听一下是不是这句 | 去听 |
| QUOTE_AUDIO_NOISY | 1 | B/C | SNR < 18 dB | 第 i 句原声有杂音，建议换一段 | 去换 |
| QUOTE_TEXT_DIFFERS | 1 | B/C | 稿句与转写差异 > 10% | 第 i 句字幕按实际说的话显示，和稿子有出入——确认没问题 | 去看 |
| SPEAKER_UNNAMED | 1 | B/C | 说话人无名字 | 说话人 k 还没填名字，人名条会显示“受访者” | 去填 |
| JUMP_CUT_UNCOVERED | 1 | B/C | 跳切且 cover ≠ broll | 有 k 处跳切没用空镜遮盖，画面会跳一下——可以回去多传空镜 | 去看 |
| FACT_CHECK | 1 | 全部 | 句中有数字/主办方/称谓 | 稿子里的数字、人名、日期我核对过（问过当事人 / 看过公告）——第 … 句 | — |
| CONTEXTUAL_BROLL_OVERLAY | 2 | 全部 | `row.overlay` | n 句的日期或机构名会做成字幕条，我确认拼写没错 | — |
| MIXED_NO_NARRATION | 2 | B | 全是原声句 | 这条全是原声，其实可以用「只用原声」模式 | — |

检查项 `key` 含句签名（镜头 + 文本），句子被改/换后自动变成新项（旧勾选失效）。

## 9 · 待应用 → 应用修改（`applyPending()`）

| 待应用键 | 来源操作 | 翻译成 | 重跑阶段 | 浮条文案 |
|---|---|---|---|---|
| `deleted[]` | 不要这句 | remix `keep_sentence_ids`（至少留一句） | 7–10 | 删 n 句 |
| `pendEdit{}` | 改这句话（旁白） | remix 文本替换 | 7–10 | 改 n 句字 |
| `pendVoice{}` | 我来读 | remix 自录音轨 | 7–10 | 录 n 句音 |
| `pendPacing` | 快一点/慢一点 | remix pacing | 7–10 | 调语速 |
| `pendReplace{}` | 换个画面 | replace-shot（每句一次；排除已占用镜头；失败分级写入 `replaceFails`） | 6, 9, 10 | 换 n 句画面 |
| `pendTrim{}` | 剪短这句（原声） | remix 缩小 `[t0,t1]`，`spoken` 同步裁切 | 7–10 | 剪短 n 句 |
| `pendTake{}` | 换一段（原声） | remix 换 take | 7–10 | 换 n 段原声 |
| `pendToNarration[]` | 改成旁白（仅 B） | remix quote → narration | 7–10 | 转旁白 n 句 |
| `pendSpeakers{}` | 改说话人名字/身份 | remix speakers | 8–10 | 改说话人名字 |

规则：先记下再一次性提交，不自动应用；换画面与删句互斥；每次应用 `revision +1` 并压入 `versions[]`，可回看/恢复（恢复 = 再一次重新合成）。

## 10 · 限额与常量（与 `GET /api/config/limits` 一致）

单文件 ≤ 500 MB · 总量 ≤ 5 GB · ≤ 20 个文件 · 单文件 ≤ 30 分钟 · 总时长 ≤ 60 分钟 · 镜头上限 120 · 单句画面 ≤ 6.5 秒 · 原声句最短 1.0 s，> 20 s 警告，> 30 s 阻断 · 语速目标 slow 230 / normal 265 / fast 290 字/分，窗口 ±8% · 稿子软上限 3000 字（硬 8000）· 标题 ≤ 40 字 · 每小时 2 次提交 · 样片保留 72 小时 · 支持格式 mp4/mov/avi/mkv/jpg/png/gif（图片定格 3 秒）。

原声剪切：切点吸附到最近词边界与静音（≤ 300 ms）；头留 120 ms、尾留 200 ms；相邻原声句之间 150 ms 间隙；同一段连续讲话拆成多句时合并为连续剪辑；同一句话多次出现取相似度最高、其次信噪比最高。音量：旁白与原声归一 −20 LUFS；音乐在人声段落压低 18 dB。

## 11 · 响应式、可访问性与键盘

- 布局：页头 60px + （侧栏 260px | 主区 `padding 24px 28px 40px`）；向导 max-width 960，制作中 760，结果页 1440（两栏 `minmax(0,1.6fr) minmax(320px,1fr)` gap 18），空态 560。
- 断点（`data-r` 属性 + helmet `@media`，不写新 class）：
  - ≤1180：`cols3` 三栏 → 两栏；文件行重排。
  - ≤1000：`rgrid` 结果页单栏，order 为 播放器 + 故事板 → 发布前检查 → 句面板；`scrollPanelNarrow()`；匹配卡 `mrow` 两列；`cols` 单栏。
  - ≤900：`side` 侧栏变抽屉（☰ `menu`）；`main` padding 14 且底部留 110px；`wrap`/`stack` 竖排；`h1` 1.5rem；`narrow-only`/`narrow-hide`；`startbar`/`pendbtns` 堆叠。
  - ≤640：模式卡/开关卡单列。
  - ≤600：`html{font-size:17px}`；`phone-hide` 隐藏次要说明；故事板 `strip` 横向滚动 + scroll-snap；`spk` 说话人行两行。
- 点击区 ≥ 44×44（图标按钮）或高 ≥ 34（chip）；开关/复选/模式卡有 `aria-pressed`/`aria-checked`/`aria-label`；弹窗 `role="dialog"`；SVG 图标 `aria-hidden`。
- 键盘：空格 播放/暂停；← → 上一句/下一句；Delete/Backspace 标记删句；Esc 关闭弹窗/抽屉/导出。

## 12 · 前端模拟 → 真实后端 对照与差距清单

| 原型方法 | 真实接口 | 说明 |
|---|---|---|
| `start()` | `POST /api/tasks`（multipart: script, files[], preferences JSON, mode）→ task_id | 上传进度用 XHR progress；`preferences` 必须发送完整对象（§5.4 全部键 + `target_cpm`） |
| `addFiles()` 预转写 | `POST /api/tasks/{id}/pretranscribe` `[需新增后端]` | ASR + 分说话人 + 词级时间戳，≤ 素材时长 × 0.3 内返回；正式制作复用 |
| `matchQuote()` | `POST /api/tasks/{id}/align` `[需新增后端]` | 稿句 ↔ 转写强制对齐，返回 score/t0/t1/spk/alts |
| `uploadOnly()/tick()/runTask()` | 轮询 `GET /api/tasks/{id}` | 原型用 `SIM` 时长伪造推进 |
| `complete()` | `GET /api/tasks/{id}/report` → rows/quality | block 模式失败 `qcFail` |
| `applyPending()` | `POST /api/tasks/{id}/remix`；`POST /api/tasks/{id}/replace-shot`（每句一次） | 剪短/换段/转旁白/说话人 `[需新增后端]`；批量修改一次提交 `[需新增后端]` |
| `restoreVersion()` | `GET /workbench/versions`, `POST /workbench/restore` `[需新增后端]` | 版本历史与回滚 |
| `record()` | `POST /workbench/recordings` `[需新增后端]` | MediaRecorder 3 秒/句，需服务端对齐 |
| 播放器/缩略图 | `GET /api/tasks/{id}/video`, `/poster`, `/thumbs/{shot_id}.jpg` | 原型用 `assets/shots/shot_N.jpg` |
| `deleteTask()` | `DELETE /api/tasks/{id}` | 取消并永久删除 → 404 |
| `LIMITS` | `GET /api/config/limits` | 保持一致 |

HTTP 错误映射：400 detail · 401 认证 · 403 CSRF 重试一次 · 404 → gone · 409 刷新状态 · 413 超限 · 422 校验 · 429 Retry-After（配额）· 503 维护/队列满 · 507 磁盘。无幂等键：超时不要自动重试。

其他 `[需新增后端]`：逐句 AI 生成来源 `media_origin` · 队列位置/ETA · 到期倒计时 · 服务端历史/账号 · 人名条烧录 · 跳切遮盖 · 样片水印烧录 · 五要素/镜头数预估。`[纯前端可做]`：music_mood 控件 · 质量数据面板 · 服务状态横幅 · 逐文件错误与容量进度 · 可访问确认框 · 上传字节进度 · 阶段时间戳 · 草稿自动保存 · 引导卡/撤销/折叠等上手优化。

## 13 · Tweaks（`data-props`，均为演示/开发用途）

```json
{
  "$preview": {
    "width": 1280,
    "height": 900
  },
  "mode": {
    "editor": "enum",
    "default": "voiceover",
    "options": [
      "voiceover",
      "mixed",
      "original"
    ],
    "tsType": "'voiceover'|'mixed'|'original'",
    "section": "制作模式"
  },
  "demoScenario": {
    "editor": "enum",
    "default": "normal",
    "options": [
      "normal",
      "quoteMissing",
      "noBroll"
    ],
    "tsType": "'normal'|'quoteMissing'|'noBroll'",
    "section": "制作模式"
  },
  "demoHelpers": {
    "editor": "boolean",
    "default": true,
    "tsType": "boolean",
    "section": "演示"
  },
  "devNotes": {
    "editor": "boolean",
    "default": false,
    "tsType": "boolean",
    "section": "演示"
  },
  "scenario": {
    "editor": "enum",
    "default": "normal",
    "options": [
      "normal",
      "netfail",
      "failed",
      "shortage",
      "maintenance"
    ],
    "tsType": "'normal'|'netfail'|'failed'|'shortage'|'maintenance'",
    "section": "演示"
  },
  "simSpeed": {
    "editor": "range",
    "default": 1,
    "min": 0.5,
    "max": 8,
    "step": 0.5,
    "unit": "x",
    "tsType": "number",
    "section": "演示"
  },
  "gateMode": {
    "editor": "enum",
    "default": "warn",
    "options": [
      "warn",
      "block"
    ],
    "tsType": "'warn'|'block'",
    "section": "质量门禁"
  },
  "startScreen": {
    "editor": "enum",
    "default": "create",
    "options": [
      "create",
      "processing",
      "result"
    ],
    "tsType": "'create'|'processing'|'result'",
    "section": "演示"
  }
}
```

- `mode` 直达三种模式；`demoScenario`：`quoteMissing`（C 模式稿中有素材里没人说过的话 → 第 1 步 ✕、第 2 步红行、阻断进第 3 步；强行提交则阶段 6 `quote_missing` 失败卡）、`noBroll`（只有采访文件 → 「自动插空镜」灰掉、默认轻微推近，结果页 JUMP_CUT_UNCOVERED）。
- `scenario`：`netfail` / `failed` / `shortage` / `maintenance`。`gateMode`：`warn` 默认 / `block`。`simSpeed` 模拟倍速。`demoHelpers` 显示「填入示例（演示）」。`devNotes` 显示 dev 标签、后端说明与 `preferences` JSON。

## 14 · 示例数据（演示用，源码常量 `SAMPLE_*` / `ROWS` / `SHOT` / `TRANSCRIPTS`）

题材：南宁信息港「金马贺岁，高新同驰」迎春市集。素材 8 个（4 空镜 + 3 采访：王红·市集主办方 / 李大姐·腊味摊摊主（SNR 14 dB）/ 张先生·市民 + 1 个不支持的 .wmv 用于演示坏文件）。B 示例稿 = 旁白 11 句 + 原声 5 段（含 1 处同文件跳切）；C 示例稿 = 8 句原话（7 处跳切、3 处杂音警告、3 处数字事实核对）。完整文本见附录 B。

## 15 · 用户视角评估（零经验用户 · 5 分制）

| 维度 | 上一版 | 当前 | 主要对策 |
|---|---|---|---|
| 易上手 | 3 | 4.5 | 引导卡 + 范例成片入口 + 「怎么用」；模式默认折叠为最简单的一种 |
| 易使用 | 3.5 | 4.5 | 文件行高级项收起；候选镜头收起；删文件可撤销；窄屏检查清单前移；文案口语化 |
| 功能全 | 4 | 4.5 | 补范例成片预览、撤销、制作中安抚；仍缺写稿脚手架（标题/导语/正文空模板）——建议下一版 |
| 长时间不疲劳 | 3.5 | 4.5 | 次级描边 1.5px、格线减半、硬阴影只留主卡片与 CTA；手机基准 17px |
| 年轻审美 | 2.5 | 4 | 同色相提亮一档；手绘 SVG 图标与插画；三个微动效 |

所有上手/视觉改动均为纯前端，不改接口与数据字段。若需更强品牌感，可在同一图标规格下扩充图标集，不要引入外部图标库或第二套字体。

---

# 附录 A · 模板源码（`<x-dc>` 内容）

> 结构：`<helmet>`（全局 reset、格线、@keyframes、`data-r` 响应式规则）→ 页头 → 侧栏 → 主区（4 个屏幕）→ 抽屉/弹窗/toast。所有样式内联；`{{ }}` 为视图键（附录 C-3）；`<svg>` 均为手绘内联图标（§3.5）。

## A-1 helmet（全局样式与响应式规则）

```html
<helmet data-dc-atomics>
<style>
*{box-sizing:border-box;}
body{margin:0;background:#f9f3e6;color:#4a3626;font-family:"Noto Sans SC","Source Han Sans SC","PingFang SC","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
a{color:#237f4a}a:hover{color:#4a3626}
button{font:inherit;cursor:pointer}
textarea,input,select{font:inherit;color:inherit}
@keyframes gm-spin{to{transform:rotate(360deg)}}
@keyframes gm-pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes gm-blink{50%{opacity:.3}}
@keyframes gm-check{0%{transform:scale(.4);opacity:0}70%{transform:scale(1.25)}100%{transform:scale(1);opacity:1}}
@keyframes gm-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes gm-wave{0%,100%{transform:scaleY(.35)}50%{transform:scaleY(1)}}
@media (max-width:600px){html{font-size:17px}}
[data-r="strip"]{overflow-x:auto;padding-bottom:6px}
[data-r="narrow-only"]{display:none}
@media (max-width:1180px){[data-r="cols3"]{grid-template-columns:1fr 1fr!important}[data-r="cols3"]>*:last-child{grid-column:1/-1;border-left:0!important;padding-left:0!important;border-top:1.5px solid #dccfbb;padding-top:12px}[data-r="narrow-hide"]{display:none!important}}
@media (max-width:1000px){[data-r="cols"]{grid-template-columns:1fr!important}[data-r="cols3"]{grid-template-columns:1fr!important}}
@media (max-width:1000px){[data-r="rgrid"]{grid-template-columns:1fr!important}[data-r="rgrid"]>*{grid-column:auto!important;grid-row:auto!important}[data-r="checks"]{order:2}[data-r="panel"]{order:3;position:static!important}[data-r="mrow"]{grid-template-columns:24px minmax(0,1fr)!important}[data-r="mrow"]>*:nth-child(n+3){grid-column:2}}
@media (max-width:640px){[data-r="modes"]{grid-template-columns:1fr!important}[data-r="toggles"]{grid-template-columns:1fr!important}[data-r="tline"]{grid-template-columns:20px 64px auto!important}[data-r="tline"]>*:last-child{grid-column:2/-1}}
[data-r="strip"]{scroll-snap-type:x proximity;scrollbar-width:thin}
@media (hover:none){[data-r="strip"]>button:hover{transform:none!important}}
@media (max-width:900px){[data-r="side"]{display:none!important}[data-r="phone-hide"]{display:none!important}[data-r="menu"]{display:inline-flex!important}[data-r="main"]{padding:14px 14px 110px!important}[data-r="stack"]{flex-direction:column!important;align-items:stretch!important}[data-r="stack"]>*{margin-left:0!important}[data-r="wrap"]{flex-wrap:wrap!important}[data-r="h1"]{font-size:1.5rem!important}[data-r="narrow-only"]{display:block}[data-r="steps"]{grid-template-columns:auto minmax(10px,1fr) auto minmax(10px,1fr) auto!important;gap:6px!important}[data-r="line"]{width:auto!important}[data-r="rtitle"]{flex-wrap:wrap}[data-r="rtitle"]>h1{flex:1 1 100%;white-space:normal!important;font-size:1.25rem!important}[data-r="ractions"]{width:100%;margin-left:0!important}[data-r="ractions"]>button{flex:1 1 auto}[data-r="ractions"]>button:last-child{flex:1 1 100%}[data-r="frow"]>*:nth-child(4){grid-column:1/-1!important}[data-r="spk"]{grid-template-columns:34px 1fr 1fr!important}[data-r="spk"]>*:nth-child(4){grid-column:2/-1}[data-r="startbar"]{flex-direction:column!important;align-items:stretch!important}[data-r="startbar"]>*{margin-left:0!important}[data-r="startbar"]>button:last-child{order:-1}[data-r="startbar"]>span{text-align:left!important;max-width:none!important}[data-r="pendbtns"]{width:100%;margin-left:0!important}[data-r="pendbtns"]>button{flex:1}}
@media (min-width:901px){[data-r="menu"]{display:none!important}[data-r="drawer"]{display:none!important}}
</style>
</helmet>
```


## A-2 应用外壳：页头 + 侧栏（我的作品）+ 主区开始

```html
<div data-r="app" style="min-height:100vh;display:flex;flex-direction:column;background-image:linear-gradient(rgba(74,54,38,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(74,54,38,.03) 1px,transparent 1px);background-size:28px 28px;line-height:1.5">
  <header style="height:60px;display:flex;align-items:center;gap:14px;padding:0 18px;background:#4a3626;color:#fff;flex:none">
    <button data-r="menu" onClick="{{ openDrawer }}" aria-label="我的作品" style="display:none;align-items:center;justify-content:center;width:44px;height:44px;border-radius:12px;border:2px solid rgba(255,255,255,.35);background:transparent;color:#fff;font-size:1.125rem">☰</button>
    <button onClick="{{ goHome }}" style="display:flex;align-items:center;gap:10px;background:none;border:0;color:#fff;padding:0"><span style="width:36px;height:36px;border-radius:50%;background:#f2b632;display:grid;place-items:center;transform:rotate(-8deg)"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4a3626" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8.5" y="3" width="7" height="12" rx="3.5" fill="#4a3626"></rect><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"></path><path d="M12 18v3M9 21h6"></path></svg></span><span style="font-weight:900;font-size:1.25rem;letter-spacing:.04em">金话筒</span></button>
    <button data-r="side" onClick="{{ toggleFont }}" aria-pressed="{{ bigFont }}" style="display:flex;align-items:center;gap:6px;height:36px;margin-left:10px;padding:0 12px;border-radius:999px;border:2px solid rgba(255,255,255,.35);background:{{ fontBtnBg }};color:{{ fontBtnFg }};font-weight:800;font-size:0.8125rem">{{ fontBtnLabel }}</button><button data-r="side" onClick="{{ showHelp }}" style="display:flex;align-items:center;height:36px;padding:0 12px;border-radius:999px;border:1.5px solid rgba(255,255,255,.35);background:transparent;color:#fff;font-size:0.8125rem;font-weight:700">怎么用</button>
    <div style="margin-left:auto;display:flex;align-items:center;gap:10px">
      
      <span data-r="narrow-hide" style="display:flex;align-items:center;gap:6px;font-size:0.8125rem;height:36px;padding:0 12px;border-radius:999px;background:rgba(255,255,255,.12)"><span style="width:8px;height:8px;border-radius:50%;background:{{ statusColor }};display:block"></span>{{ statusText }}</span>
      <sc-if value="{{ hasSaved }}" hint-placeholder-val="{{ false }}"><span data-r="narrow-hide" style="display:flex;align-items:center;gap:6px;font-size:0.75rem;height:36px;padding:0 12px;border-radius:999px;border:1.5px solid rgba(255,255,255,.25);color:rgba(255,255,255,.8)">✓ 已自动保存 {{ savedLabel }}</span></sc-if>
    </div>
  </header>
  <sc-if value="{{ maintenance }}" hint-placeholder-val="{{ false }}">
    <div style="background:#fdf2d8;border-bottom:2px solid #f2b632;padding:10px 18px;font-size:0.875rem;font-weight:700;text-align:center">服务正在维护，暂时不能新建作品。已做好的作品还能看。</div>
  </sc-if>
  
  <div style="display:flex;flex:1;min-height:0">
    <aside data-r="side" style="width:260px;flex:none;border-right:2px solid #4a3626;background:rgba(255,255,255,.55);padding:16px 14px;display:flex;flex-direction:column;gap:10px">
        
        <button onClick="{{ newTask }}" style="height:48px;border-radius:14px;background:#4a3626;color:#fff;border:2px solid #4a3626;font-weight:800;font-size:0.9375rem">＋ 新作品</button>
        
        <div style="display:flex;align-items:center;padding:8px 4px 0"><span style="font-weight:800;font-size:0.75rem;letter-spacing:.1em;color:#75645a">我的作品</span><button onClick="{{ toggleHistSort }}" style="margin-left:auto;background:none;border:0;padding:0;font-size:0.75rem;font-weight:700;color:#237f4a">{{ histSortLabel }}</button></div>
        <div style="display:flex;flex-direction:column;gap:8px">
          <sc-for list="{{ histItems }}" as="h" hint-placeholder-count="3">
            <button onClick="{{ h.open }}" style="text-align:left;width:100%;border-radius:14px;padding:12px 14px;background:#fff;border:2px solid {{ h.border }};box-shadow:{{ h.shadow }};color:#4a3626" style-hover="background:#f4ecdf">
              <div style="font-weight:800;font-size:0.875rem;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ h.title }}</div>
              <div style="margin-top:6px;display:flex;gap:6px;align-items:center;font-size:0.75rem;color:#75645a;flex-wrap:wrap"><span style="font-weight:800;color:{{ h.statusFg }};background:{{ h.statusBg }};border:1.5px solid {{ h.statusBorder }};border-radius:6px;padding:1px 7px">{{ h.status }}</span><span>{{ h.mode }}</span><span>{{ h.rev }}</span><span>{{ h.date }}</span><span style="color:{{ h.leftFg }};font-weight:{{ h.leftW }}">{{ h.left }}</span></div>
            </button>
          </sc-for>
        </div>
        <button onClick="{{ openSample }}" style="margin-top:4px;height:40px;border-radius:12px;border:2px dashed #dccfbb;background:transparent;font-size:0.8125rem;font-weight:700;color:#237f4a">看一条范例作品</button>
      
      
      <div style="margin-top:auto;font-size:0.75rem;color:#75645a;padding:10px 4px 0;border-top:2px dashed #dccfbb;display:flex;flex-direction:column;gap:6px"><span>{{ retentionLine }}</span><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span>现为固定 72h TTL，发布/保留都不会延长；retain_until 与到期倒计时字段 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></span></sc-if></div>
    </aside>
    <main data-r="main" style="flex:1;min-width:0;padding:24px 28px 40px">
```

## A-3 屏幕：新建作品

```html
<sc-if value="{{ isCreate }}" hint-placeholder-val="{{ true }}">
      <div data-screen-label="新建作品" data-doc="三步向导：1 写稿（模式卡、标题/正文、五要素、句子清单）→ 2 传素材（逐文件校验、预转写、匹配卡、说话人卡、充足度）→ 3 选效果（按模式显隐）→ 开始制作。首次进入先显示引导卡" style="max-width:960px;margin:0 auto;display:flex;flex-direction:column;gap:20px">
        
        
        <div data-r="steps" style="display:grid;grid-template-columns:1fr auto 1fr auto 1fr;align-items:center;gap:8px">
          <button onClick="{{ goStep1 }}" style="display:flex;align-items:center;gap:10px;background:none;border:0;padding:0;text-align:left;min-height:44px"><span style="width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font-weight:800;background:{{ s1.bg }};color:{{ s1.fg }};border:2px solid {{ s1.border }};flex:none"><sc-if value="{{ s1.done }}" hint-placeholder-val="{{ false }}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="display:block;animation:gm-check .4s ease-out"><path d="M5 12.5l4.5 4.5L19 7"></path></svg></sc-if><sc-if value="{{ s1.notDone }}" hint-placeholder-val="{{ true }}">{{ s1.mark }}</sc-if></span><span style="font-weight:800;color:{{ s1.label }};white-space:nowrap">写稿</span></button>
          <span data-r="line" style="width:40px;height:2px;background:{{ s1.line }};display:block"></span>
          <button onClick="{{ goStep2 }}" style="display:flex;align-items:center;gap:10px;background:none;border:0;padding:0;text-align:left;min-height:44px"><span style="width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font-weight:800;background:{{ s2.bg }};color:{{ s2.fg }};border:2px solid {{ s2.border }};flex:none"><sc-if value="{{ s2.done }}" hint-placeholder-val="{{ false }}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="display:block;animation:gm-check .4s ease-out"><path d="M5 12.5l4.5 4.5L19 7"></path></svg></sc-if><sc-if value="{{ s2.notDone }}" hint-placeholder-val="{{ true }}">{{ s2.mark }}</sc-if></span><span style="font-weight:800;color:{{ s2.label }};white-space:nowrap">传素材</span></button>
          <span data-r="line" style="width:40px;height:2px;background:{{ s2.line }};display:block"></span>
          <button onClick="{{ goStep3 }}" style="display:flex;align-items:center;gap:10px;background:none;border:0;padding:0;text-align:left;min-height:44px"><span style="width:34px;height:34px;border-radius:50%;display:grid;place-items:center;font-weight:800;background:{{ s3.bg }};color:{{ s3.fg }};border:2px solid {{ s3.border }};flex:none"><sc-if value="{{ s3.done }}" hint-placeholder-val="{{ false }}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="display:block;animation:gm-check .4s ease-out"><path d="M5 12.5l4.5 4.5L19 7"></path></svg></sc-if><sc-if value="{{ s3.notDone }}" hint-placeholder-val="{{ true }}">{{ s3.mark }}</sc-if></span><span style="font-weight:800;color:{{ s3.label }};white-space:nowrap">选效果</span></button>
        </div>

        <sc-if value="{{ isStep1 }}" hint-placeholder-val="{{ true }}">
<sc-if value="{{ showIntro }}" hint-placeholder-val="{{ false }}">
        <div data-r="stack" style="display:flex;align-items:center;gap:22px;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:4px 4px 0 #4a3626;padding:22px 24px;margin-bottom:22px;animation:gm-pop .25s ease-out">
          <svg aria-hidden="true" width="132" height="110" viewBox="0 0 132 110" fill="none" stroke="#4a3626" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" style="flex:none"><g transform="rotate(-8 40 60)"><rect x="14" y="40" width="52" height="40" rx="8" fill="#fff"></rect><path d="M14 52h52"></path><rect x="14" y="34" width="52" height="14" rx="6" fill="#f2b632"></rect><path d="M22 34l6 14M34 34l6 14M46 34l6 14"></path><path d="M26 62h28M26 70h18"></path></g><g transform="rotate(10 96 52)"><rect x="86" y="14" width="22" height="38" rx="11" fill="#4a3626"></rect><path d="M78 40a19 19 0 0 0 38 0M97 60v12M88 72h18"></path><path d="M70 30v18M124 30v18" style="transform-origin:center;"></path></g><circle cx="24" cy="18" r="4" fill="#237f4a" stroke="none"></circle><circle cx="118" cy="92" r="5" fill="#f2b632" stroke="none"></circle><path d="M6 96l6-6 6 6" stroke="#c94a2c"></path></svg>
          <div style="min-width:0;flex:1">
            <div style="font-weight:900;font-size:1.375rem;line-height:1.25">三步，把你的稿子变成一条新闻视频</div>
            <div data-r="wrap" style="display:flex;gap:6px 14px;flex-wrap:wrap;margin-top:10px;font-size:0.875rem;color:#75645a"><span><b style="color:#4a3626">1 写稿</b> · 粘贴或现写</span><span><b style="color:#4a3626">2 传素材</b> · 手机拍的就行</span><span><b style="color:#4a3626">3 选效果</b> · 都有推荐</span><span><b style="color:#237f4a">→ AI 剪好</b> · 通常几分钟</span></div>
            <div data-r="wrap" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px"><button onClick="{{ dismissIntro }}" style="height:44px;padding:0 18px;border-radius:12px;border:2px solid #4a3626;background:#4a3626;color:#fff;font-weight:800;font-size:0.9375rem">开始写稿 →</button><button onClick="{{ openSample }}" style="height:44px;padding:0 16px;border-radius:12px;border:2px solid #dccfbb;background:#fff;color:#237f4a;font-weight:800;font-size:0.9375rem">先看一条范例成片</button></div>
          </div>
        </div>
        </sc-if>
        <div style="position:relative;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:4px 4px 0 #4a3626;padding:26px 24px 20px">
          <span style="position:absolute;top:-13px;left:20px;background:#4a3626;color:#fff;font-weight:800;font-size:0.75rem;padding:6px 12px;border-radius:8px;transform:rotate(-2deg)">第 1 步</span>
          <div data-r="stack" style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
            <h1 data-r="h1" style="margin:0;font-size:1.75rem;font-weight:900;line-height:1.2">写稿</h1>
            <span style="color:#75645a">{{ step1Sub }}</span>
            <span style="margin-left:auto"><sc-if value="{{ demo }}" hint-placeholder-val="{{ true }}"><button onClick="{{ useSample }}" style="height:36px;padding:0 12px;border-radius:10px;border:1.5px solid #dccfbb;background:#fff;font-size:0.8125rem;font-weight:700;color:#75645a">填入示例（演示）</button></sc-if></span>
          </div>
          <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#75645a;margin-top:4px">script ≤ 8000 字（/api/config/limits；软上限 {{ charSoft }} 字）· 首行 ≤ 40 字且无句末标点 → 标题 · mode={{ modeCode }} · 原声句对齐阈值 0.85 / 0.6（预转写 + 强制对齐 需新增后端）· 五要素 / 事实句检测为前端正则，NLP 版 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
          <div data-r="modes" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px">
            <sc-for list="{{ modeCards }}" as="mc" hint-placeholder-count="3">
              <button onClick="{{ mc.pick }}" aria-pressed="{{ mc.on }}" style="position:relative;text-align:left;border-radius:12px;border:2px solid {{ mc.border }};background:{{ mc.bg }};padding:12px 14px;color:#4a3626;min-height:44px" style-hover="border-color:#4a3626">
                <sc-if value="{{ mc.on }}" hint-placeholder-val="{{ false }}"><span style="position:absolute;top:8px;right:8px;width:18px;height:18px;border-radius:50%;background:#f2b632;color:#4a3626;display:grid;place-items:center;font-size:0.6875rem;font-weight:900">✓</span></sc-if>
                <span style="display:flex;align-items:center;gap:8px"><span style="min-width:34px;height:34px;padding:0 8px;border-radius:999px;background:#4a3626;color:#fff;display:grid;place-items:center;flex:none"><sc-if value="{{ mc.isA }}" hint-placeholder-val="{{ true }}"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8.5" y="3" width="7" height="12" rx="3.5" fill="#fff"></rect><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"></path></svg></sc-if><sc-if value="{{ mc.isB }}" hint-placeholder-val="{{ false }}"><svg width="26" height="18" viewBox="0 0 34 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4.5" y="3" width="6" height="11" rx="3" fill="#fff"></rect><path d="M2 11a5.5 5.5 0 0 0 11 0M7.5 17v3"></path><path d="M19 4h11a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-6l-4 3v-3h-1a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"></path></svg></sc-if><sc-if value="{{ mc.isC }}" hint-placeholder-val="{{ false }}"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 4h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"></path><path d="M8 9h8M8 12.5h5"></path></svg></sc-if></span><span style="font-weight:800;font-size:0.9375rem;white-space:nowrap">{{ mc.name }}</span></span><sc-if value="{{ mc.tag }}" hint-placeholder-val="{{ false }}"><span style="display:inline-block;margin-top:8px;font-size:0.6875rem;font-weight:800;color:#4a3626;background:#f2b632;border-radius:6px;padding:2px 8px;transform:rotate(-1.5deg)">{{ mc.tag }}</span></sc-if>
                <span style="display:block;font-size:0.8125rem;color:#75645a;margin-top:6px">{{ mc.desc }}</span>
                <span style="display:block;font-size:0.75rem;margin-top:4px">适合：{{ mc.fit }}</span>
              </button>
            </sc-for>
          </div>
          <button onClick="{{ toggleModeMore }}" style="margin-top:8px;height:34px;padding:0 12px;border-radius:999px;border:1.5px solid #dccfbb;background:#fff;font-size:0.75rem;font-weight:800;color:#75645a">{{ modeMoreLabel }}</button>
          <sc-if value="{{ hasPick }}" hint-placeholder-val="{{ false }}">
          <div style="margin-top:12px;border:2px solid #237f4a;border-radius:12px;padding:10px 12px;background:#e7f3ea">
            <div data-r="wrap" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><span style="font-weight:900;font-size:0.875rem">从转写挑句子</span><span style="font-size:0.75rem;color:#75645a">勾一句，就按顺序加到稿子末尾 · {{ pickSummary }}</span><button onClick="{{ togglePick }}" style="margin-left:auto;height:32px;padding:0 10px;border-radius:8px;border:2px solid #237f4a;background:#fff;font-size:0.75rem;font-weight:800;color:#237f4a">{{ pickLabel }}</button></div>
            <sc-if value="{{ pickOpen }}" hint-placeholder-val="{{ true }}"><div style="display:flex;flex-direction:column;gap:8px;margin-top:8px">
              <sc-for list="{{ pickGroups }}" as="pg" hint-placeholder-count="2"><div style="background:#fff;border-radius:10px;padding:8px 10px"><div style="font-size:0.75rem;font-weight:800;color:#75645a;margin-bottom:4px">{{ pg.file }} · {{ pg.count }} 句</div><div style="display:flex;flex-direction:column;gap:2px"><sc-for list="{{ pg.lines }}" as="ln" hint-placeholder-count="3"><label data-r="tline" style="display:grid;grid-template-columns:20px 64px auto minmax(0,1fr);gap:4px 8px;align-items:center;min-height:34px;font-size:0.8125rem;cursor:pointer"><input type="checkbox" checked="{{ ln.on }}" onChange="{{ ln.flip }}" style="width:20px;height:20px;accent-color:#237f4a"><span style="font-family:ui-monospace,Menlo,monospace;font-size:0.75rem;color:#75645a">{{ ln.tc }}</span><span style="font-size:0.6875rem;font-weight:800;padding:2px 8px;border-radius:6px;background:#237f4a;color:#fff;white-space:nowrap;justify-self:start">{{ ln.spk }}</span><span style="min-width:0">{{ ln.text }}</span></label></sc-for></div></div></sc-for>
            </div></sc-if>
          </div>
          </sc-if>
          <textarea ref="{{ scriptRef }}" value="{{ script }}" onChange="{{ onScript }}" placeholder="{{ scriptPlaceholder }}" style="display:block;width:100%;min-height:240px;margin-top:14px;border:1.5px solid #dccfbb;border-radius:14px;padding:14px 16px;font-size:1rem;line-height:1.75;resize:vertical;background:#fff;outline:none" style-focus="border-color:#4a3626"></textarea>
          <div data-r="wrap" style="display:flex;align-items:center;gap:10px;margin-top:10px;font-size:0.8125rem;color:#75645a;flex-wrap:wrap">
            <span style="font-weight:700;color:{{ titleColor }}">{{ titleLabel }}</span>
            <sc-if value="{{ titleFixable }}" hint-placeholder-val="{{ false }}"><button onClick="{{ fixTitle }}" style="height:30px;padding:0 10px;border-radius:8px;border:2px solid #4a3626;background:#fff;font-size:0.75rem;font-weight:800">去掉标点，当标题</button></sc-if>
            <sc-if value="{{ hasLongSent }}" hint-placeholder-val="{{ false }}"><span style="color:#c94a2c;font-weight:700">{{ longSentText }}</span></sc-if><sc-if value="{{ hasQuoteLong }}" hint-placeholder-val="{{ false }}"><span style="color:#c94a2c;font-weight:700">{{ quoteLongText }}</span></sc-if><sc-if value="{{ hasNewWords }}" hint-placeholder-val="{{ false }}"><span style="color:#c94a2c;font-weight:700">{{ newWordsText }}</span></sc-if>
            <span style="margin-left:auto">{{ charCount }} / {{ charSoft }} 字</span>
          </div>
          <sc-if value="{{ showElements }}" hint-placeholder-val="{{ false }}">
          <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px;align-items:center"><span style="font-size:0.75rem;font-weight:800;color:#75645a;margin-right:4px">五要素</span><sc-for list="{{ elements }}" as="el" hint-placeholder-count="5"><button onClick="{{ el.flip }}" aria-pressed="{{ el.on }}" style="display:inline-flex;align-items:center;gap:5px;min-height:34px;padding:0 12px;border-radius:999px;border:2px solid {{ el.border }};background:{{ el.bg }};color:{{ el.fg }};font-size:0.75rem;font-weight:800">{{ el.mark }} {{ el.label }}</button></sc-for><span style="font-size:0.75rem;color:#75645a">{{ elemHint }}</span></div>
          </sc-if>
          <sc-if value="{{ hasShotList }}" hint-placeholder-val="{{ false }}">
          <div style="margin-top:12px;border:2px solid #237f4a;border-radius:12px;padding:10px 12px;background:#e7f3ea">
            <div data-r="wrap" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><span style="font-weight:900;font-size:0.875rem">{{ sentListTitle }}</span><span style="font-size:0.75rem;color:#75645a">{{ sentListSub }}</span><button onClick="{{ toggleShotList }}" style="margin-left:auto;height:32px;padding:0 10px;border-radius:8px;border:2px solid #237f4a;background:#fff;font-size:0.75rem;font-weight:800;color:#237f4a">{{ shotListLabel }}</button><button onClick="{{ copyShotList }}" style="height:32px;padding:0 10px;border-radius:8px;border:1.5px solid #dccfbb;background:#fff;font-size:0.75rem;font-weight:800;color:#4a3626">复制到手机</button></div>
            <sc-if value="{{ shotListOpen }}" hint-placeholder-val="{{ false }}"><div style="display:flex;flex-direction:column;gap:4px;margin-top:8px"><sc-for list="{{ sentRows }}" as="sh" hint-placeholder-count="4"><div style="display:grid;grid-template-columns:24px minmax(0,1fr) auto;gap:8px;align-items:center;padding:6px 8px;border-radius:10px;background:#fff;font-size:0.8125rem;border-left:3px solid {{ sh.leftBorder }}"><span style="font-weight:800;color:#75645a;font-family:ui-monospace,Menlo,monospace;font-size:0.75rem">{{ sh.idx }}</span><div style="min-width:0"><div style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis"><sc-for list="{{ sh.chunks }}" as="ch" hint-placeholder-count="1"><span style="color:{{ ch.fg }};text-decoration:{{ ch.deco }};text-decoration-color:#c94a2c;text-decoration-thickness:2px">{{ ch.text }}</span></sc-for></div><sc-if value="{{ sh.hasAdvice }}" hint-placeholder-val="{{ true }}"><div style="font-weight:700;color:{{ sh.fg }}">{{ sh.advice }}</div></sc-if></div><span style="display:flex;gap:6px;align-items:center"><sc-if value="{{ sh.hasStatus }}" hint-placeholder-val="{{ false }}"><span style="font-size:0.75rem;font-weight:800;padding:1px 7px;border-radius:6px;background:{{ sh.stBg }};color:{{ sh.stFg }};white-space:nowrap">{{ sh.status }}</span><span style="font-family:ui-monospace,Menlo,monospace;font-size:0.75rem;color:#75645a">{{ sh.score }}</span></sc-if><sc-if value="{{ sh.hasChip }}" hint-placeholder-val="{{ false }}"><button onClick="{{ sh.flip }}" style="height:34px;padding:0 12px;border-radius:999px;border:2px solid {{ sh.chipBorder }};background:{{ sh.chipBg }};color:{{ sh.chipFg }};font-weight:800;font-size:0.75rem" style-hover="border-color:#4a3626">{{ sh.chip }}</button></sc-if></span></div></sc-for></div><div style="font-size:0.75rem;color:#75645a;margin-top:8px">{{ sentStats }}</div></sc-if>
          </div>
          </sc-if>
          <sc-if value="{{ hasSuggest }}" hint-placeholder-val="{{ false }}"><div style="margin-top:12px;background:#fdf2d8;border:2px dashed #f2b632;border-radius:14px;padding:12px 14px;font-size:0.875rem;line-height:1.55"><div data-r="wrap" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><span style="font-weight:900;white-space:nowrap">✎ 建议</span><span style="flex:1;min-width:160px">{{ suggestText }}</span><button onClick="{{ suggestYes }}" style="height:36px;padding:0 12px;border-radius:10px;background:#4a3626;color:#fff;border:2px solid #4a3626;font-weight:800;font-size:0.8125rem">{{ suggestYesLabel }}</button><button onClick="{{ suggestNo }}" style="height:36px;padding:0 12px;border-radius:10px;background:#fff;border:1.5px solid #dccfbb;font-weight:700;font-size:0.8125rem;color:#4a3626">{{ suggestNoLabel }}</button></div></div></sc-if>
          <div style="margin-top:14px;background:#fdf2d8;border:2px dashed #f2b632;border-radius:14px;padding:12px 14px;font-size:0.875rem;line-height:1.55;transform:rotate(-.5deg)">
            <div style="display:flex;gap:10px;align-items:baseline"><span style="font-weight:900;white-space:nowrap;display:inline-flex;align-items:center;gap:5px"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#4a3626" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20l4.5-1 10-10-3.5-3.5-10 10z"></path><path d="M13.5 7l3.5 3.5"></path></svg>小贴士</span><span>{{ tip1Text }}</span><button onClick="{{ toggleTip1 }}" style="margin-left:auto;background:none;border:0;padding:0;font-weight:800;color:#237f4a;font-size:0.8125rem;white-space:nowrap">{{ tip1Label }}</button></div>
            <sc-if value="{{ tip1Open }}" hint-placeholder-val="{{ false }}"><ul style="margin:8px 0 0;padding-left:20px"><li>导语一句说清：何时、何地、何人、何事、为何。</li><li>一句话别超过 30 个字——太长会读得赶，画面也撑不住。</li><li>写了几样东西，就要拍到几样：“腊肉、糕点、礼盒”要三个镜头。</li><li>数字写阿拉伯数字，AI 会读对。</li></ul></sc-if>
          </div>
        </div>
        <div data-r="stack" style="display:flex;align-items:center;gap:12px">
          <span style="font-size:0.8125rem;color:#75645a">{{ step1Hint }}</span>
          <button onClick="{{ next }}" style="margin-left:auto;height:48px;padding:0 24px;border-radius:12px;background:{{ nextBg }};color:#fff;border:2px solid {{ nextBg }};font-weight:800;font-size:0.9375rem">下一步：传素材 →</button>
        </div>
        </sc-if>

        <sc-if value="{{ isStep2 }}" hint-placeholder-val="{{ false }}">
        <div style="position:relative;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:4px 4px 0 #4a3626;padding:26px 24px 20px">
          <span style="position:absolute;top:-13px;left:20px;background:#4a3626;color:#fff;font-weight:800;font-size:0.75rem;padding:6px 12px;border-radius:8px;transform:rotate(1.5deg)">第 2 步</span>
          <div data-r="stack" style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
            <h1 data-r="h1" style="margin:0;font-size:1.75rem;font-weight:900;line-height:1.2">传素材</h1>
            <span style="color:#75645a">{{ step2Sub }}</span>
            <span style="margin-left:auto"><sc-if value="{{ demo }}" hint-placeholder-val="{{ true }}"><button onClick="{{ addSampleFiles }}" style="height:36px;padding:0 12px;border-radius:10px;border:1.5px solid #dccfbb;background:#fff;font-size:0.8125rem;font-weight:700;color:#75645a">填入示例（演示）</button></sc-if></span>
          </div>
          <div data-r="narrow-only" style="margin-top:10px;background:#f4ecdf;border:1.5px solid #dccfbb;border-radius:12px;padding:10px 12px;font-size:0.8125rem">手机传大视频很慢。建议用电脑传素材，手机用来看片和提意见。</div>
          <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#75645a;margin-top:4px">来自 /api/config/limits：单文件 ≤ 500 MiB · 总量 ≤ 5 GB · ≤ 20 个 · 单文件 ≤ 30 分 · 总时长 ≤ 60 分 · 时长与首帧由浏览器读取 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #237f4a;color:#237f4a;border-radius:4px">纯前端可做</span> · 断点续传 / 幂等键 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
          <label onDrop="{{ onDrop }}" onDragOver="{{ onDragOver }}" style="display:block;margin-top:14px;border:3px dashed #237f4a;border-radius:14px;padding:26px 16px;text-align:center;background:#e7f3ea;cursor:pointer">
            <input type="file" multiple accept="video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,image/jpeg,image/png,image/gif,.mp4,.mov,.avi,.mkv,.jpg,.jpeg,.png,.gif" onChange="{{ onFiles }}" style="display:none">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#237f4a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="display:block;margin:0 auto 6px"><path d="M7 18a4.5 4.5 0 0 1-.8-8.9A6 6 0 0 1 17.7 8 4 4 0 0 1 17 18"></path><path d="M12 21v-8M8.5 16.5 12 13l3.5 3.5"></path></svg><div style="font-weight:800;font-size:1.0625rem;color:#237f4a">把视频或照片拖进来，或点击选择</div>
            <div style="font-size:0.75rem;color:#75645a;margin-top:4px">{{ uploadNote }}</div>
          </label>
          <sc-if value="{{ hasFiles }}" hint-placeholder-val="{{ false }}">
          <div style="display:flex;flex-direction:column;gap:8px;margin-top:12px">
            <sc-for list="{{ files }}" as="f" hint-placeholder-count="3">
              <div data-r="frow" style="animation:gm-rise .25s ease-out;display:grid;grid-template-columns:64px minmax(0,1fr) auto;gap:8px 12px;align-items:center;padding:8px;border-radius:12px;border:2px solid {{ f.border }};background:{{ f.bg }}">
                <span style="height:36px;border-radius:6px;background-color:#e4d9c6;background-image:{{ f.thumbImg }};background-size:cover;background-position:center;display:block"></span>
                <div style="min-width:0"><div style="font-weight:700;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ f.name }}</div><div style="font-size:0.75rem;color:{{ f.metaColor }}">{{ f.meta }}</div><sc-if value="{{ f.hasLines }}" hint-placeholder-val="{{ false }}"><button onClick="{{ f.toggleOpen }}" style="margin-top:4px;height:30px;padding:0 10px;border-radius:8px;border:1.5px solid #dccfbb;background:#fff;font-size:0.75rem;font-weight:800;color:#237f4a">{{ f.openLabel }}</button></sc-if><button onClick="{{ f.toggleAdv }}" style="margin-top:4px;margin-left:6px;height:30px;padding:0 10px;border-radius:8px;border:1.5px solid #dccfbb;background:#fff;font-size:0.75rem;font-weight:800;color:#75645a">{{ f.advLabel }}</button></div>
                <button onClick="{{ f.remove }}" aria-label="移除" style="width:44px;height:44px;border-radius:10px;border:0;background:none;color:#75645a;font-size:1.25rem">×</button>
                <sc-if value="{{ f.adv }}" hint-placeholder-val="{{ false }}"><div style="grid-column:2/-1;display:flex;gap:6px;align-items:center;flex-wrap:wrap"><input type="text" value="{{ f.note }}" onChange="{{ f.onNote }}" maxlength="20" placeholder="备注 / 标签：采访、门口远景…" style="flex:1;min-width:140px;height:36px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 8px;font-size:0.75rem;outline:none;background:#fff"><sc-if value="{{ f.hasTrim }}" hint-placeholder-val="{{ false }}"><span style="display:flex;gap:6px;align-items:center"><span style="font-size:0.75rem;color:#75645a;white-space:nowrap">只用</span><input type="number" min="0" value="{{ f.inSec }}" onChange="{{ f.onIn }}" style="width:58px;height:36px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 6px;font-size:0.75rem;outline:none;background:#fff"><span style="font-size:0.75rem;color:#75645a">–</span><input type="number" min="1" value="{{ f.outSec }}" onChange="{{ f.onOut }}" style="width:58px;height:36px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 6px;font-size:0.75rem;outline:none;background:#fff"><span style="font-size:0.75rem;color:#75645a;white-space:nowrap">秒</span></span></sc-if></div></sc-if>
                <sc-if value="{{ f.open }}" hint-placeholder-val="{{ false }}"><div style="grid-column:1/-1;background:#f4ecdf;border-radius:10px;padding:8px 10px;display:flex;flex-direction:column;gap:2px"><sc-for list="{{ f.lines }}" as="ln" hint-placeholder-count="3"><label data-r="tline" style="display:grid;grid-template-columns:20px 64px auto minmax(0,1fr);gap:4px 8px;align-items:center;min-height:34px;font-size:0.8125rem"><sc-if value="{{ ln.pickable }}" hint-placeholder-val="{{ false }}"><input type="checkbox" checked="{{ ln.on }}" onChange="{{ ln.flip }}" style="width:20px;height:20px;accent-color:#237f4a"></sc-if><sc-if value="{{ ln.plain }}" hint-placeholder-val="{{ true }}"><span></span></sc-if><span style="font-family:ui-monospace,Menlo,monospace;font-size:0.75rem;color:#75645a">{{ ln.tc }}</span><span style="font-size:0.6875rem;font-weight:800;padding:2px 8px;border-radius:6px;background:#237f4a;color:#fff;white-space:nowrap;justify-self:start">{{ ln.spk }}</span><span style="min-width:0">{{ ln.text }}</span></label></sc-for><sc-if value="{{ f.more }}" hint-placeholder-val="{{ false }}"><button onClick="{{ f.toggleAll }}" style="height:34px;border-radius:8px;border:2px dashed #dccfbb;background:#fff;font-size:0.75rem;font-weight:700;color:#237f4a">{{ f.moreLabel }}</button></sc-if></div></sc-if>
              </div>
            </sc-for>
          </div>
          <div style="display:flex;align-items:center;gap:12px;margin-top:12px;font-size:0.8125rem"><b>{{ fileCount }} / {{ maxFiles }} 个</b><span style="flex:1;height:10px;border-radius:5px;border:2px solid #4a3626;background:#fff;overflow:hidden;display:block"><span style="display:block;width:{{ capPct }}%;height:100%;background:#237f4a"></span></span><span style="color:#75645a">{{ totalGb }} / {{ maxTotalGb }} GB · {{ totalDur }} / 60 分</span></div>
          <sc-if value="{{ aHasSpeech }}" hint-placeholder-val="{{ false }}"><div data-r="wrap" style="margin-top:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:#f4ecdf;border:1.5px solid #dccfbb;border-radius:12px;padding:10px 12px;font-size:0.8125rem"><span style="flex:1;min-width:200px">这个模式不用人声，说话会被压低、只用画面。想用原声？</span><button onClick="{{ switchMixed }}" style="height:36px;padding:0 12px;border-radius:10px;border:2px solid #4a3626;background:#fff;font-weight:800;font-size:0.8125rem;color:#4a3626">切到「旁白 + 原声」</button></div></sc-if>
          <sc-if value="{{ hasMatchCard }}" hint-placeholder-val="{{ false }}">
          <div style="margin-top:12px;border:2px solid {{ matchColor }};border-radius:12px;padding:10px 12px">
            <div data-r="wrap" style="display:flex;align-items:baseline;gap:8px;font-size:0.875rem;flex-wrap:wrap"><b>{{ matchTitle }}</b><span style="color:#75645a;font-size:0.75rem">{{ matchSummary }}</span><sc-if value="{{ isOriginal }}" hint-placeholder-val="{{ false }}"><button onClick="{{ goPick }}" style="margin-left:auto;height:32px;padding:0 10px;border-radius:8px;border:2px solid #237f4a;background:#fff;font-size:0.75rem;font-weight:800;color:#237f4a">从转写挑句子</button></sc-if></div>
            <div style="display:flex;flex-direction:column;gap:4px;margin-top:8px">
              <sc-for list="{{ matchRows }}" as="mr" hint-placeholder-count="3"><div style="border-radius:10px;border:2px solid {{ mr.border }};background:{{ mr.bg }};padding:6px 10px"><div data-r="mrow" style="display:grid;grid-template-columns:24px minmax(0,1fr) minmax(0,1fr) auto;gap:4px 8px;align-items:center;font-size:0.8125rem"><span style="font-weight:800;color:#75645a;font-family:ui-monospace,Menlo,monospace;font-size:0.75rem">{{ mr.idx }}</span><span style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ mr.text }}</span><span style="font-size:0.75rem;color:#75645a;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ mr.info }}</span><span style="display:flex;gap:6px;align-items:center"><span style="font-size:0.75rem;font-weight:800;padding:1px 7px;border-radius:6px;background:{{ mr.stBg }};color:{{ mr.stFg }};white-space:nowrap">{{ mr.status }}</span><span style="font-family:ui-monospace,Menlo,monospace;font-size:0.75rem;color:#75645a">{{ mr.score }}</span></span></div><sc-if value="{{ mr.hasFix }}" hint-placeholder-val="{{ false }}"><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px"><sc-for list="{{ mr.fixes }}" as="fx" hint-placeholder-count="2"><button onClick="{{ fx.go }}" style="height:30px;padding:0 10px;border-radius:8px;border:2px solid #4a3626;background:#fff;font-size:0.75rem;font-weight:800;color:#4a3626">{{ fx.label }}</button></sc-for></div></sc-if></div></sc-for>
            </div>
            <sc-if value="{{ hasJumpLine }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#4a3626;margin-top:8px;border-top:2px dashed #dccfbb;padding-top:8px">{{ jumpLine }}</div></sc-if>
            <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#75645a;margin-top:6px">预转写 = 上传后立即转写 + 分说话人 + 词级时间戳；稿句对齐为前端 bigram 相似度模拟，阈值 0.85 / 0.6 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
          </div>
          </sc-if>
          <sc-if value="{{ hasSpeakers }}" hint-placeholder-val="{{ false }}">
          <div style="margin-top:12px;border:1.5px solid #dccfbb;border-radius:12px;padding:10px 12px">
            <div style="display:flex;align-items:baseline;gap:8px;font-size:0.875rem"><b>说话人</b><span style="color:#75645a;font-size:0.75rem">· 填好名字和身份，会做成人名条</span></div>
            <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px">
              <sc-for list="{{ speakerRows }}" as="sp" hint-placeholder-count="2"><div data-r="spk" style="display:grid;grid-template-columns:34px 1fr 1fr auto;gap:6px 8px;align-items:center;padding:6px 8px;border-radius:10px;border:2px solid {{ sp.border }};background:{{ sp.bg }}"><span style="width:34px;height:34px;border-radius:50%;background:#4a3626;color:#fff;display:grid;place-items:center;font-weight:900;font-size:0.8125rem">{{ sp.idx }}</span><input type="text" value="{{ sp.name }}" onChange="{{ sp.onName }}" placeholder="姓名" maxlength="8" style="height:32px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 8px;font-size:0.8125rem;outline:none;background:#fff;min-width:0"><input type="text" value="{{ sp.role }}" onChange="{{ sp.onRole }}" placeholder="身份，如：市集主办方" maxlength="12" style="height:32px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 8px;font-size:0.8125rem;outline:none;background:#fff;min-width:0"><span style="font-size:0.75rem;color:{{ sp.metaFg }};white-space:nowrap;font-weight:{{ sp.metaW }}">{{ sp.meta }}</span></div></sc-for>
            </div>
          </div>
          </sc-if>
          <div style="display:{{ suffDisplay }};margin-top:12px;border:2px solid {{ suffColor }};border-radius:12px;padding:10px 12px"><div data-r="wrap" style="display:flex;align-items:baseline;gap:8px;font-size:0.875rem;flex-wrap:wrap"><b>拍摄充足度：<span style="color:{{ suffColor }}">{{ suffLabel }}</span></b><span style="color:#75645a;margin-left:auto;font-size:0.75rem">{{ suffDetail }}</span></div><div style="margin-top:6px;height:8px;border-radius:4px;background:#f0e7d8;overflow:hidden"><div style="height:100%;width:{{ suffPct }}%;background:{{ suffColor }};transition:width .3s"></div></div><div style="font-size:0.75rem;color:#4a3626;margin-top:6px">{{ suffAdvice }}</div><sc-if value="{{ hasSentList }}" hint-placeholder-val="{{ false }}"><div style="margin-top:10px;border-top:2px dashed #dccfbb;padding-top:10px"><div style="font-size:0.8125rem;font-weight:800;margin-bottom:6px">每句话都拍到了吗？点一下告诉我 <span style="font-weight:400;color:#75645a">· {{ sentCheckSummary }}</span></div><div style="display:flex;flex-direction:column;gap:4px"><sc-for list="{{ sentList }}" as="sl" hint-placeholder-count="4"><button onClick="{{ sl.flip }}" style="display:grid;grid-template-columns:24px minmax(0,1fr) auto;gap:8px;align-items:center;min-height:36px;padding:4px 10px;border-radius:10px;border:2px solid {{ sl.border }};background:{{ sl.bg }};text-align:left;color:#4a3626;font-size:0.8125rem"><span style="font-weight:800;color:#75645a;font-family:ui-monospace,Menlo,monospace;font-size:0.75rem">{{ sl.idx }}</span><span style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ sl.text }}</span><span style="font-weight:800;color:{{ sl.fg }};white-space:nowrap">{{ sl.mark }}</span></button></sc-for><sc-if value="{{ sentMore }}" hint-placeholder-val="{{ false }}"><button onClick="{{ toggleSentList }}" style="height:36px;border-radius:10px;border:2px dashed #dccfbb;background:#fff;font-size:0.8125rem;font-weight:700;color:#237f4a">{{ sentMoreLabel }}</button></sc-if></div></div></sc-if><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#75645a;margin-top:6px">保守估算：每个视频 max(1, 时长÷12s) 个镜头，有人说话的采访算 1 个；成片时长 = 字数 ÷ 所选播音速度（目标字/分）。真实镜头数在阶段 3 才知道；镜头 &lt; 句数会在阶段 6 失败（ISSUE-01）且已产生费用 → 在此前置校验 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #237f4a;color:#237f4a;border-radius:4px">纯前端可做</span></div></sc-if></div>
          </sc-if>
          
          <div style="margin-top:14px;background:#fdf2d8;border:2px dashed #f2b632;border-radius:14px;padding:12px 14px;font-size:0.875rem;line-height:1.55;transform:rotate(.5deg)">
            <div style="display:flex;gap:10px;align-items:baseline"><span style="font-weight:900;white-space:nowrap">🎬 小贴士</span><span>{{ tip2Text }}</span><button onClick="{{ toggleTip2 }}" style="margin-left:auto;background:none;border:0;padding:0;font-weight:800;color:#237f4a;font-size:0.8125rem;white-space:nowrap">{{ tip2Label }}</button></div>
            <sc-if value="{{ tip2Open }}" hint-placeholder-val="{{ false }}"><ul style="margin:8px 0 0;padding-left:20px"><li>远景、中景、特写都拍一点；每个场景 10–20 秒就够。</li><li>有人说话的镜头会变成现场原声——让对方一句话说完，15 秒以内最好。</li><li>没同意出镜的人不要拍正脸；采访前先问一句。</li><li>iPhone 拍的视频读不出时长？相机设置里选“兼容性最好”。</li><li>同一个视频不要传两次。</li></ul></sc-if>
          </div>
        </div>
        <div data-r="stack" style="display:flex;align-items:center;gap:12px">
          <button onClick="{{ back }}" style="height:48px;padding:0 18px;border-radius:12px;background:#fff;color:#4a3626;border:1.5px solid #dccfbb;font-weight:700;font-size:0.9375rem">← 上一步</button>
          <span style="font-size:0.8125rem;color:{{ step2HintColor }};margin-left:auto">{{ step2Hint }}</span>
          <button onClick="{{ next }}" style="height:48px;padding:0 24px;border-radius:12px;background:{{ nextBg }};color:#fff;border:2px solid {{ nextBg }};font-weight:800;font-size:0.9375rem">下一步：选效果 →</button>
        </div>
        </sc-if>

        <sc-if value="{{ isStep3 }}" hint-placeholder-val="{{ false }}">
        <div style="position:relative;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:4px 4px 0 #4a3626;padding:26px 24px 20px">
          <span style="position:absolute;top:-13px;left:20px;background:#4a3626;color:#fff;font-weight:800;font-size:0.75rem;padding:6px 12px;border-radius:8px;transform:rotate(-1deg)">第 3 步</span>
          <div data-r="stack" style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap"><h1 data-r="h1" style="margin:0;font-size:1.75rem;font-weight:900;line-height:1.2">选效果</h1><span style="color:#75645a">{{ step3Sub }}</span></div>
          <div data-r="wrap" style="display:flex;gap:28px;flex-wrap:wrap;margin-top:18px">
            <sc-if value="{{ showVoice }}" hint-placeholder-val="{{ true }}"><div><div style="font-weight:800;margin-bottom:8px">{{ voiceTitle }} <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-weight:400;font-size:0.6875rem;color:#75645a">voice · 记者录音上传与对齐 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></span></sc-if></div><div style="display:flex;gap:6px;flex-wrap:wrap"><sc-for list="{{ voiceChips }}" as="c" hint-placeholder-count="2"><button onClick="{{ c.pick }}" style="min-height:44px;padding:0 16px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:700;font-size:0.875rem">{{ c.label }}</button></sc-for></div><div style="font-size:0.75rem;color:#75645a;margin-top:6px">{{ voiceNote }}</div></div></sc-if>
            
            <sc-if value="{{ showPacing }}" hint-placeholder-val="{{ true }}"><div><div style="font-weight:800;margin-bottom:8px">播音速度 <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-weight:400;font-size:0.6875rem;color:#75645a">pacing · 230 / 265 / 290 字/分 · 目标 {{ cpmRange }}（±8%）</span></sc-if></div><div style="display:flex;gap:6px;flex-wrap:wrap"><sc-for list="{{ pacingChips }}" as="c" hint-placeholder-count="3"><button onClick="{{ c.pick }}" style="min-height:44px;padding:0 16px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:700;font-size:0.875rem">{{ c.label }}</button></sc-for></div></div>
            </sc-if>
            <div><div style="font-weight:800;margin-bottom:8px">整体感觉 <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-weight:400;font-size:0.6875rem;color:#75645a">tone</span></sc-if></div><div style="display:flex;gap:6px;flex-wrap:wrap"><sc-for list="{{ toneChips }}" as="c" hint-placeholder-count="3"><button onClick="{{ c.pick }}" style="min-height:44px;padding:0 16px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:700;font-size:0.875rem">{{ c.label }}</button></sc-for></div></div>
            <div><div style="font-weight:800;margin-bottom:8px">字幕样式 <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-weight:400;font-size:0.6875rem;color:#75645a">caption_style · 需新增后端</span></sc-if></div><div style="display:flex;gap:6px;flex-wrap:wrap"><sc-for list="{{ captionChips }}" as="c" hint-placeholder-count="3"><button onClick="{{ c.pick }}" style="min-height:44px;padding:0 16px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:700;font-size:0.875rem">{{ c.label }}</button></sc-for></div><div style="font-size:0.75rem;color:#75645a;margin-top:6px">{{ captionNote }}</div></div>
          </div>
          <div data-r="toggles" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:18px">
            <sc-for list="{{ toggles }}" as="tg" hint-placeholder-count="6">
              <div style="border:2px {{ tg.borderStyle }} {{ tg.border }};background:{{ tg.bg }};border-radius:12px;padding:10px 12px">
                <button onClick="{{ tg.flip }}" role="switch" aria-checked="{{ tg.on }}" style="display:flex;width:100%;align-items:center;justify-content:space-between;gap:10px;background:none;border:0;padding:0;min-height:32px;text-align:left;color:#4a3626">
                  <span style="font-weight:800;font-size:0.875rem">{{ tg.label }}</span>
                  <span style="width:40px;height:22px;border-radius:11px;background:{{ tg.track }};position:relative;flex:none;display:block;transition:background .2s"><span style="position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;background:#fff;transform:translateX({{ tg.knob }});transition:transform .2s;display:block"></span></span>
                </button>
                <sc-if value="{{ tg.showNote }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:{{ tg.noteColor }};margin-top:4px">{{ tg.note }}</div></sc-if>
                <sc-if value="{{ tg.showMood }}" hint-placeholder-val="{{ false }}"><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"><sc-for list="{{ moodChips }}" as="m" hint-placeholder-count="5"><button onClick="{{ m.pick }}" style="min-height:44px;padding:0 14px;border-radius:999px;border:2px solid {{ m.border }};background:{{ m.bg }};color:{{ m.fg }};font-weight:700;font-size:0.8125rem">{{ m.label }}</button></sc-for></div></sc-if>
                <sc-if value="{{ tg.showSpeakers }}" hint-placeholder-val="{{ false }}"><div style="display:flex;flex-direction:column;gap:6px;margin-top:8px"><sc-for list="{{ speakerRows }}" as="sp" hint-placeholder-count="2"><div style="display:grid;grid-template-columns:28px 1fr 1fr;gap:6px;align-items:center;padding:4px 6px;border-radius:8px;border:2px solid {{ sp.border }};background:{{ sp.bg }}"><span style="width:28px;height:28px;border-radius:50%;background:#4a3626;color:#fff;display:grid;place-items:center;font-weight:900;font-size:0.75rem">{{ sp.idx }}</span><input type="text" value="{{ sp.name }}" onChange="{{ sp.onName }}" placeholder="姓名" maxlength="8" style="height:32px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 8px;font-size:0.75rem;outline:none;background:#fff;min-width:0"><input type="text" value="{{ sp.role }}" onChange="{{ sp.onRole }}" placeholder="身份" maxlength="12" style="height:32px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 8px;font-size:0.75rem;outline:none;background:#fff;min-width:0"></div></sc-for></div></sc-if>
                <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#75645a;margin-top:4px">{{ tg.devName }}</div></sc-if>
              </div>
            </sc-for>
          </div>
          <sc-if value="{{ showJump }}" hint-placeholder-val="{{ false }}"><div style="margin-top:12px;border:1.5px solid #dccfbb;border-radius:12px;padding:10px 12px"><div style="font-weight:800;margin-bottom:8px">跳切怎么处理 <span style="font-weight:400;font-size:0.75rem;color:#75645a">· {{ jumpNote }}</span><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-weight:400;font-size:0.6875rem;color:#75645a"> jump_cut_cover broll|zoom|hard · 空镜不足自动降级 zoom <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></span></sc-if></div><div style="display:flex;gap:6px;flex-wrap:wrap"><sc-for list="{{ jumpChips }}" as="c" hint-placeholder-count="3"><button onClick="{{ c.pick }}" aria-disabled="{{ c.disabled }}" style="min-height:44px;padding:0 16px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:700;font-size:0.875rem;cursor:{{ c.cursor }}">{{ c.label }}</button></sc-for></div></div></sc-if>
          <sc-if value="{{ showQuoteCap }}" hint-placeholder-val="{{ false }}"><div style="margin-top:8px;border:1.5px solid #dccfbb;border-radius:12px;padding:10px 12px"><div style="font-weight:800;margin-bottom:8px">原声段的字幕 <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-weight:400;font-size:0.6875rem;color:#75645a">quote_caption spoken|none <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></span></sc-if></div><div style="display:flex;gap:6px;flex-wrap:wrap"><sc-for list="{{ quoteCapChips }}" as="c" hint-placeholder-count="2"><button onClick="{{ c.pick }}" style="min-height:44px;padding:0 16px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:700;font-size:0.875rem">{{ c.label }}</button></sc-for></div></div></sc-if>
          <textarea value="{{ custom }}" onChange="{{ onCustom }}" placeholder="想对 AI 剪辑师说的话（选填），例如：多用有人笑的画面" style="display:block;width:100%;min-height:64px;margin-top:12px;border:1.5px solid #dccfbb;border-radius:12px;padding:10px 14px;font-size:0.875rem;line-height:1.6;resize:vertical;outline:none" style-focus="border-color:#4a3626"></textarea>
          <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><pre style="margin:10px 0 0;padding:10px 12px;background:#f4ecdf;border-radius:10px;font-size:0.6875rem;line-height:1.5;white-space:pre-wrap;color:#75645a;font-family:ui-monospace,Menlo,monospace">{{ prefsJson }}</pre></sc-if>
          <div style="margin-top:12px;background:#f4ecdf;border-radius:12px;padding:10px 12px;font-size:0.8125rem;font-weight:700">{{ summaryLine }}</div>
        </div>
        <div data-r="startbar" style="display:flex;align-items:center;gap:12px">
          <button onClick="{{ back }}" style="height:48px;padding:0 18px;border-radius:12px;background:#fff;color:#4a3626;border:1.5px solid #dccfbb;font-weight:700;font-size:0.9375rem">← 上一步</button>
          <span style="font-size:0.75rem;color:#75645a;margin-left:auto;text-align:right;max-width:440px">稿子和素材会上传到 AI 服务（含第三方云）处理。<br><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><br><span>匿名限额 2 次/时/浏览器 · 5 次/时/出口 IP · 全站 10 次/时 · 并发 1 · 待处理 ≤5 → 提交即先上传、服务端排队（已定：先收后做）；服务端队列位置查询 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></span></sc-if></span>
          <button onClick="{{ start }}" style="height:52px;padding:0 28px;border-radius:14px;background:{{ startBg }};color:#fff;border:2px solid {{ startBg }};font-weight:900;font-size:1.0625rem">开始制作</button>
        </div>
        </sc-if>
      </div>
      </sc-if>
```

## A-4 屏幕：制作中

```html
<sc-if value="{{ isProcessing }}" hint-placeholder-val="{{ false }}">
      <div data-screen-label="制作中" data-doc="上传进度 → 排队 → 10 阶段（B/C 模式文案不同，始终 10 个）；失败时显示原因与可选动作 pFailActions" style="max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:18px">
        <div data-r="wrap" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><h1 data-r="h1" style="margin:0;font-size:1.5rem;font-weight:900;line-height:1.2;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;flex:1">{{ pTitle }}</h1><span style="font-weight:800;font-size:0.75rem;color:#fff;background:#4a3626;border-radius:8px;padding:4px 10px;transform:rotate(-2deg)">{{ pVersion }}</span></div>
        <div style="position:relative;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:4px 4px 0 #4a3626;padding:22px 24px">
          <div data-r="stack" style="display:flex;align-items:center;gap:20px">
            <div style="display:flex;align-items:center;gap:16px;flex:none"><span aria-hidden="true" style="width:64px;height:64px;border-radius:50%;background:#f2b632;display:grid;place-items:center;flex:none"><svg width="40" height="40" viewBox="0 0 40 40" fill="none" stroke="#4a3626" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><rect x="14.5" y="6" width="11" height="17" rx="5.5" fill="#4a3626"></rect><path d="M10 18a10 10 0 0 0 20 0M20 28v5M15 33h10"></path><g style="transform-origin:6px 17px;animation:gm-wave 1.1s ease-in-out infinite"><path d="M6 12v10"></path></g><g style="transform-origin:3px 17px;animation:gm-wave 1.1s ease-in-out .3s infinite"><path d="M3 14v6"></path></g><g style="transform-origin:34px 17px;animation:gm-wave 1.1s ease-in-out .15s infinite"><path d="M34 12v10"></path></g><g style="transform-origin:37px 17px;animation:gm-wave 1.1s ease-in-out .45s infinite"><path d="M37 14v6"></path></g></svg></span><div style="font-size:3.25rem;font-weight:900;line-height:1;min-width:120px">{{ pBig }}</div></div>
            <div style="flex:1;min-width:0">
              <div style="font-weight:800;font-size:1.125rem">{{ pHeadline }}</div>
              <div style="color:#75645a;margin-top:2px">{{ pDoing }}</div><sc-if value="{{ hasStepLine }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.8125rem;color:#237f4a;font-weight:700;margin-top:4px">{{ pStepLine }}</div></sc-if>
              <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#75645a;margin-top:4px;font-family:ui-monospace,Menlo,monospace">{{ pPro }}</div></sc-if>
            </div>
          </div>
          <div style="margin-top:16px;height:14px;border-radius:7px;border:2px solid #4a3626;background:#fff;overflow:hidden"><div style="height:100%;width:{{ pProgress }}%;background:{{ pBarColor }};transition:width .2s"></div></div>
          <sc-if value="{{ pBusy }}" hint-placeholder-val="{{ true }}"><div style="margin-top:10px;font-size:0.8125rem;color:#75645a">通常需要几分钟。可以先去做别的——做好后会出现在左边「我的作品」里。</div></sc-if>
        </div>
        <sc-if value="{{ pFailed }}" hint-placeholder-val="{{ false }}">
        <div style="background:#fff;border:2px solid #c94a2c;border-radius:16px;padding:16px 18px">
          <div style="font-weight:900;font-size:1.125rem;color:#c94a2c">这次没做成</div>
          <div style="margin-top:4px">{{ pError }}</div>
          <div style="margin-top:6px;font-size:0.875rem;color:#75645a">{{ pErrorAdvice }}</div>
          <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#75645a;margin-top:4px;font-family:ui-monospace,Menlo,monospace">{{ pErrorPro }}</div></sc-if>
          <div data-r="wrap" style="display:flex;gap:10px;margin-top:14px;flex-wrap:wrap"><sc-for list="{{ pFailActions }}" as="a" hint-placeholder-count="2"><button onClick="{{ a.go }}" style="height:48px;padding:0 20px;border-radius:12px;background:{{ a.bg }};color:{{ a.fg }};border:2px solid #4a3626;font-weight:800;font-size:0.9375rem">{{ a.label }}</button></sc-for><button onClick="{{ askDelete }}" style="height:48px;padding:0 18px;border-radius:12px;background:#fff;color:#c94a2c;border:2px solid #c94a2c;font-weight:700;font-size:0.9375rem;margin-left:auto">删除这个作品</button></div>
        </div>
        </sc-if>
        <sc-if value="{{ hasUploadRow }}" hint-placeholder-val="{{ false }}"><div style="background:#fff;border:2px solid {{ upBorder }};border-radius:16px;padding:12px 14px;display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:12px;align-items:center"><span style="width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-weight:900;font-size:0.8125rem;background:{{ upIconBg }};color:{{ upIconFg }};border:2px solid {{ upBorder }}">{{ upIcon }}</span><div style="min-width:0"><div style="font-weight:800">上传素材 · {{ upFiles }}</div><div style="font-size:0.8125rem;color:#75645a">{{ upDetail }}</div><div style="margin-top:6px;height:8px;border-radius:4px;background:#f0e7d8;overflow:hidden"><div style="height:100%;width:{{ upPct }}%;background:#237f4a;transition:width .2s"></div></div><sc-if value="{{ hasUpList }}" hint-placeholder-val="{{ false }}"><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:4px 12px;margin-top:8px"><sc-for list="{{ upList }}" as="uf" hint-placeholder-count="3"><div style="display:flex;align-items:center;gap:6px;font-size:0.75rem;color:{{ uf.fg }};min-width:0"><span style="width:14px;height:14px;border-radius:50%;border:2px solid {{ uf.ring }};background:{{ uf.dot }};flex:none;display:block"></span><span style="overflow:hidden;white-space:nowrap;text-overflow:ellipsis;min-width:0;flex:1">{{ uf.name }}</span><span style="font-family:ui-monospace,Menlo,monospace;flex:none">{{ uf.pct }}</span></div></sc-for></div></sc-if></div><span style="font-size:0.75rem;color:#75645a;font-family:ui-monospace,Menlo,monospace">{{ upElapsed }}</span></div></sc-if>
        <div style="background:#fff;border:1.5px solid #dccfbb;border-radius:16px;padding:8px 10px">
          <sc-for list="{{ pStages }}" as="st" hint-placeholder-count="10">
            <div style="display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:12px;align-items:center;padding:9px 8px;border-radius:10px;background:{{ st.rowBg }}">
              <span style="width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font-weight:900;font-size:0.8125rem;background:{{ st.iconBg }};color:{{ st.iconFg }};border:2px solid {{ st.iconBorder }}"><span style="display:block;animation:{{ st.anim }}">{{ st.icon }}</span></span>
              <div style="min-width:0"><div style="font-weight:{{ st.weight }};color:{{ st.color }}">{{ st.label }}</div><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#75645a;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ st.devName }}{{ st.msg }}</div></sc-if></div>
              <span style="font-size:0.75rem;color:#75645a;font-family:ui-monospace,Menlo,monospace">{{ st.elapsed }}</span>
            </div>
          </sc-for>
        </div>
        <sc-if value="{{ pRunning }}" hint-placeholder-val="{{ true }}">
        <div data-r="stack" style="display:flex;align-items:center;gap:12px">
          <span style="font-size:0.8125rem;color:#75645a">{{ pNote }}<sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span> 上传进度为前端 XHR（纯前端可做）；上传完成后才占名额（已定：先收后做）· 提交顺序在服务端队列，队列位置/ETA 接口 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></span></sc-if></span>
          <button onClick="{{ askCancel }}" style="margin-left:auto;height:44px;padding:0 18px;border-radius:12px;background:#fff;color:#c94a2c;border:2px solid #c94a2c;font-weight:700;font-size:0.875rem">取消并删除</button>
        </div>
        </sc-if>
      </div>
      </sc-if>
```

## A-5 屏幕：结果

```html
<sc-if value="{{ isResult }}" hint-placeholder-val="{{ false }}">
      <div data-screen-label="结果" data-doc="结果页：播放器（样片水印/人名条预览）+ 故事板（每格一句，跳切金线）+ 发布前检查 + 「第 N 句」面板（旁白句：改字/录音/换画面/删；原声句：剪短/换一段/改成旁白/删）+ 待应用浮条；修改先记下再一次性应用" style="max-width:1440px;margin:0 auto;display:flex;flex-direction:column;gap:14px">
        <div data-r="wrap" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
          <div style="min-width:0;flex:1 1 300px">
            <div data-r="rtitle" style="display:flex;align-items:center;gap:10px;min-width:0"><h1 data-r="h1" style="margin:0;font-size:1.375rem;font-weight:900;line-height:1.2;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ rTitle }}</h1><span style="font-weight:800;font-size:0.75rem;color:{{ gateFg }};background:{{ gateBg }};border:2px solid {{ gateBorder }};border-radius:8px;padding:3px 10px;white-space:nowrap;flex:none">{{ gateLabel }}</span><span style="font-size:0.6875rem;font-weight:800;padding:2px 8px;border-radius:6px;border:1.5px solid #dccfbb;color:#75645a;white-space:nowrap;flex:none">{{ rMode }}</span></div>
            <div data-r="wrap" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:5px;font-size:0.75rem;color:#75645a">
              <span>{{ rOwnerLine }}</span><span style="font-weight:700;color:{{ rLeftFg }}">{{ rLeft }}</span>
              <sc-if value="{{ hasVersions }}" hint-placeholder-val="{{ false }}"><span style="margin-left:6px">版本</span><sc-for list="{{ versionChips }}" as="v" hint-placeholder-count="2"><button onClick="{{ v.pick }}" style="height:28px;padding:0 10px;border-radius:999px;border:2px solid {{ v.border }};background:{{ v.bg }};color:{{ v.fg }};font-size:0.75rem;font-weight:800">{{ v.label }}</button></sc-for><sc-if value="{{ canRestore }}" hint-placeholder-val="{{ false }}"><button onClick="{{ restoreVersion }}" style="height:28px;padding:0 12px;border-radius:999px;border:2px solid #4a3626;background:#4a3626;color:#fff;font-size:0.75rem;font-weight:800">恢复这个版本</button></sc-if><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">旧版视频 GET …/video?revision=n 已支持 · 恢复为当前版 需新增后端</span></sc-if></sc-if>
              <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span>{{ rMeta }}</span></sc-if>
            </div>
          </div>
          <div data-r="ractions" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:auto">
            <sc-if value="{{ canManage }}" hint-placeholder-val="{{ false }}"><button onClick="{{ renameTask }}" style="height:42px;padding:0 14px;border-radius:12px;background:#fff;color:#4a3626;border:1.5px solid #dccfbb;font-weight:700;font-size:0.8125rem">重命名</button><button onClick="{{ duplicateTask }}" style="height:42px;padding:0 14px;border-radius:12px;background:#fff;color:#4a3626;border:1.5px solid #dccfbb;font-weight:700;font-size:0.8125rem">复制一份</button></sc-if>
            <sc-if value="{{ canDelete }}" hint-placeholder-val="{{ true }}"><button onClick="{{ askDelete }}" style="height:42px;padding:0 14px;border-radius:12px;background:#fff;color:#c94a2c;border:1.5px solid #dccfbb;font-weight:700;font-size:0.8125rem">删除</button></sc-if>
            <sc-if value="{{ canDownload }}" hint-placeholder-val="{{ true }}"><button onClick="{{ download }}" style="height:46px;padding:0 20px;border-radius:12px;background:#4a3626;color:#fff;border:2px solid #4a3626;font-weight:800;font-size:0.9375rem;box-shadow:3px 3px 0 #f2b632">导出 / 分享</button></sc-if>
          </div>
        </div>

        <div data-r="stack" style="display:flex;align-items:center;gap:14px;background:#4a3626;color:#fff;border-radius:14px;padding:12px 18px">
          <span style="width:34px;height:34px;border-radius:50%;background:#f2b632;color:#4a3626;display:grid;place-items:center;flex:none"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#4a3626" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h15M13 6l6 6-6 6"></path></svg></span>
          <div style="min-width:0;flex:1"><div style="font-size:0.75rem;opacity:.7">现在该做什么</div><div style="font-weight:800;font-size:1rem;line-height:1.4">{{ nextAction }}</div></div>
          <sc-if value="{{ canEdit }}" hint-placeholder-val="{{ false }}"><div data-r="narrow-hide" style="font-size:0.6875rem;opacity:.6;line-height:1.6;text-align:right;flex:none">空格 播放/暂停 · ← → 上一句/下一句<br>Delete 标记删掉这句 · 点进度条跳转</div></sc-if>
          <sc-if value="{{ nextHasBtn }}" hint-placeholder-val="{{ false }}"><button onClick="{{ nextGo }}" style="height:42px;padding:0 16px;border-radius:10px;background:#fff;color:#4a3626;border:0;font-weight:800;font-size:0.875rem;white-space:nowrap;flex:none">{{ nextBtn }}</button></sc-if>
        </div>

        <div data-r="rgrid" style="display:grid;grid-template-columns:minmax(0,1.6fr) minmax(320px,1fr);gap:18px;align-items:start">
          <div style="grid-column:1;grid-row:1;display:flex;flex-direction:column;gap:14px;min-width:0">
            <div style="position:relative;aspect-ratio:16/9;border-radius:18px;border:2px solid #4a3626;box-shadow:4px 4px 0 #4a3626;overflow:hidden;background:#4a3626">
              <div style="position:absolute;inset:0;background-image:url({{ posterSrc }});background-size:cover;background-position:center"></div>
              <sc-if value="{{ showGraphics }}" hint-placeholder-val="{{ true }}"><span style="position:absolute;top:16px;left:16px;background:#4a3626;color:#fff;font-weight:800;font-size:0.8125rem;padding:6px 12px;border-radius:4px;border-left:4px solid #f2b632">{{ topicText }}</span></sc-if>
              <sc-if value="{{ posterGen }}" hint-placeholder-val="{{ false }}"><span style="position:absolute;top:16px;right:16px;background:rgba(255,255,255,.92);color:#4a3626;font-weight:800;font-size:0.75rem;padding:5px 10px;border-radius:4px">AI生成示意画面</span></sc-if>
              <sc-if value="{{ showLT }}" hint-placeholder-val="{{ false }}"><span style="position:absolute;left:16px;bottom:100px;background:rgba(74,54,38,.9);color:#fff;border-left:4px solid #f2b632;border-radius:4px;padding:6px 12px;display:block;max-width:60%"><span style="display:block;font-weight:800;font-size:0.9375rem;line-height:1.3">{{ ltName }}</span><span style="display:block;font-size:0.75rem;opacity:.8">{{ ltRole }}</span></span></sc-if>
              <sc-if value="{{ showWatermark }}" hint-placeholder-val="{{ false }}"><span style="position:absolute;inset:0;display:block;pointer-events:none;background:repeating-linear-gradient(-30deg,transparent 0 120px,rgba(255,255,255,.08) 120px 124px)"></span><span style="position:absolute;left:0;right:0;top:20%;text-align:center;pointer-events:none;transform:rotate(-18deg);color:rgba(255,255,255,.42);font-weight:900;font-size:clamp(1.125rem,4.5vw,2.5rem);letter-spacing:.25em;text-shadow:0 1px 2px rgba(0,0,0,.35);display:block">{{ wmText }}</span><span style="position:absolute;left:0;right:0;top:60%;text-align:center;pointer-events:none;transform:rotate(-18deg);color:rgba(255,255,255,.42);font-weight:900;font-size:clamp(1.125rem,4.5vw,2.5rem);letter-spacing:.25em;text-shadow:0 1px 2px rgba(0,0,0,.35);display:block">{{ wmOwner }}</span></sc-if>
              <span style="position:absolute;left:0;right:0;bottom:60px;text-align:center;color:#fff;font-weight:700;font-size:clamp(.8125rem,2vw,1.125rem);line-height:1.35;padding:0 28px;text-shadow:0 1px 3px rgba(0,0,0,.8);overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ posterText }}</span>
              <button onClick="{{ togglePlay }}" aria-label="{{ playAria }}" style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:76px;height:76px;border-radius:50%;background:rgba(255,255,255,.94);border:0;font-size:1.75rem;color:#4a3626;display:grid;place-items:center;box-shadow:0 6px 24px rgba(0,0,0,.35)">{{ playIcon }}</button>
              <div style="position:absolute;bottom:0;left:0;right:0;height:52px;background:linear-gradient(transparent,rgba(0,0,0,.78));display:flex;align-items:center;gap:12px;padding:0 16px;color:#fff;font-size:0.8125rem;font-family:ui-monospace,Menlo,monospace"><span>{{ timeLabel }}</span><span onClick="{{ seekTo }}" role="slider" aria-label="进度" style="flex:1;height:36px;display:flex;align-items:center;cursor:pointer"><span style="flex:1;height:5px;border-radius:3px;background:rgba(255,255,255,.35);display:block;overflow:hidden"><span style="display:block;height:100%;width:{{ progressPct }}%;background:#f2b632"></span></span></span><span>1080p</span></div>
            </div>

            <div style="background:#fff;border:1.5px solid #dccfbb;border-radius:16px;padding:12px 16px 10px">
              <div data-r="wrap" style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px;flex-wrap:wrap"><span style="font-weight:900;font-size:0.9375rem">故事板</span><span style="font-size:0.75rem;color:#75645a">一格一句话，宽=时长 · 点一格就能改它 · <span style="white-space:nowrap">第 <b style="color:#4a3626">{{ selIdx }}</b> / {{ rowCount }} 句</span></span><div data-r="narrow-hide" style="margin-left:auto;display:flex;gap:12px;font-size:0.6875rem;color:#75645a;flex-wrap:wrap"><span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#237f4a;vertical-align:middle"></span> 现场原声 / 我的配音</span><span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #4a3626;vertical-align:middle"></span> 不太确定</span><span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#4a3626;vertical-align:middle"></span> AI 生成</span><span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#c94a2c;vertical-align:middle"></span> 凑数画面</span><sc-if value="{{ isVoice }}" hint-placeholder-val="{{ true }}"></sc-if><sc-if value="{{ selIsQuoteMode }}" hint-placeholder-val="{{ false }}"><span><span style="display:inline-block;width:3px;height:10px;background:#f2b632;vertical-align:middle"></span> 跳切</span></sc-if></div></div>
              <div aria-hidden="true" style="display:flex;gap:2px;height:6px;margin-bottom:8px"><sc-for list="{{ overview }}" as="o" hint-placeholder-count="8"><span style="flex:{{ o.flex }} 1 0;min-width:3px;border-radius:2px;background:{{ o.bg }};box-shadow:{{ o.ring }};display:block"></span></sc-for></div>
              <div data-r="strip" style="display:flex;gap:5px;height:{{ stripH }}px">
                <sc-for list="{{ cells }}" as="c" hint-placeholder-count="8">
                  <button data-cell="{{ c.id }}" onClick="{{ c.select }}" style="position:relative;flex:{{ c.flex }} 1 0;min-width:{{ cellMin }}px;scroll-snap-align:start;border-radius:10px;overflow:hidden;border:2px solid {{ c.border }};box-shadow:{{ c.shadow }};opacity:{{ c.opacity }};background:#4a3626;padding:0;color:#fff;text-align:left" style-hover="transform:translateY(-2px)">
                    <span style="position:absolute;inset:0;background-image:url({{ c.thumb }});background-size:cover;background-position:center;display:block"></span>
                    <span style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.45),transparent 40%,transparent 50%,rgba(0,0,0,.82));display:block"></span>
                    <span style="position:absolute;top:5px;left:7px;font-weight:900;font-size:0.75rem;font-family:ui-monospace,Menlo,monospace">{{ c.idx }}</span>
                    <sc-if value="{{ c.hasDot }}" hint-placeholder-val="{{ false }}"><span style="position:absolute;top:6px;right:6px;width:10px;height:10px;border-radius:50%;background:{{ c.dotBg }};border:2px solid {{ c.dotBorder }};display:block"></span></sc-if>
                    <span style="position:absolute;bottom:5px;left:7px;right:7px;font-size:0.8125rem;font-weight:700;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;display:block">{{ c.short }}</span>
                    <sc-if value="{{ c.hasTag }}" hint-placeholder-val="{{ false }}"><span style="position:absolute;top:24px;left:7px;background:{{ c.tagBg }};color:#fff;font-size:0.6875rem;font-weight:900;padding:1px 6px;border-radius:4px">{{ c.tag }}</span></sc-if>
                  </button>
                </sc-for>
              </div>
            </div>
          </div>

            <div data-r="checks" style="grid-column:1;grid-row:2;background:#fff;border:2px solid #4a3626;border-radius:16px;box-shadow:4px 4px 0 #4a3626;padding:14px 16px;display:flex;flex-direction:column;gap:10px;min-width:0">
              <div data-r="wrap" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap"><span style="font-weight:900;font-size:0.9375rem">发布前检查</span><span style="font-size:0.75rem;color:#75645a">{{ checkSummary }}</span><div style="flex:1 1 160px;height:8px;border-radius:4px;border:2px solid #4a3626;overflow:hidden;background:#fff;min-width:120px"><div style="height:100%;width:{{ checkPct }}%;background:#237f4a;transition:width .3s"></div></div></div>
              <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:8px">
                <sc-for list="{{ checks }}" as="ck" hint-placeholder-count="3">
                  <div style="display:flex;gap:10px;align-items:flex-start;padding:9px 10px;border-radius:10px;border:2px solid {{ ck.border }};background:{{ ck.bg }};font-size:0.8125rem;line-height:1.45">
                    <button onClick="{{ ck.toggle }}" role="checkbox" aria-checked="{{ ck.done }}" aria-label="勾选" style="width:32px;height:32px;border-radius:9px;flex:none;display:grid;place-items:center;font-weight:900;font-size:0.9375rem;background:{{ ck.boxBg }};color:{{ ck.boxFg }};border:2px solid {{ ck.boxBorder }};padding:0;cursor:{{ ck.cursor }}"><sc-if value="{{ ck.done }}" hint-placeholder-val="{{ false }}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="display:block;animation:gm-check .4s ease-out"><path d="M5 12.5l4.5 4.5L19 7"></path></svg></sc-if><sc-if value="{{ ck.notDone }}" hint-placeholder-val="{{ true }}">{{ ck.mark }}</sc-if></button>
                    <span style="min-width:0;flex:1;text-decoration:{{ ck.deco }};color:{{ ck.color }}"><span>{{ ck.text }}</span><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="display:block;font-size:0.6875rem;color:#75645a;font-family:ui-monospace,Menlo,monospace;margin-top:2px;text-decoration:none">{{ ck.code }}</span></sc-if></span>
                    <sc-if value="{{ ck.hasGo }}" hint-placeholder-val="{{ false }}"><button onClick="{{ ck.go }}" style="height:30px;padding:0 10px;border-radius:8px;border:2px solid #4a3626;background:#fff;font-size:0.75rem;font-weight:800;color:#4a3626;white-space:nowrap">{{ ck.goLabel }}</button></sc-if>
                  </div>
                </sc-for>
              </div>
              <sc-if value="{{ showMetrics }}" hint-placeholder-val="{{ false }}">
                <div style="border-top:2px dashed #dccfbb;padding-top:10px"><div style="font-weight:800;font-size:0.75rem;margin-bottom:6px">{{ metricsTitle }}</div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:4px 10px;font-size:0.75rem;color:#75645a"><sc-for list="{{ metrics }}" as="m" hint-placeholder-count="6"><span>{{ m }}</span></sc-for></div><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#c94a2c;margin-top:8px;font-family:ui-monospace,Menlo,monospace;line-height:1.5">{{ gateNote }}</div></sc-if></div>
              </sc-if>
            </div>

          <div data-r="panel" style="grid-column:2;grid-row:1/3;position:sticky;top:12px;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:4px 4px 0 #4a3626;overflow:hidden;min-width:0">
            <div style="background:#4a3626;color:#fff;padding:10px 12px 10px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <span style="font-weight:900;font-size:1rem">第 {{ selIdx }} 句 <span style="font-weight:400;opacity:.65;font-size:0.8125rem">/ {{ rowCount }}</span></span>
              <span style="display:flex;gap:4px"><button onClick="{{ selPrev }}" aria-label="上一句" style="width:36px;height:36px;border-radius:10px;border:2px solid rgba(255,255,255,.35);background:transparent;color:#fff;font-weight:800;font-size:0.9375rem;padding:0">←</button><button onClick="{{ selNext }}" aria-label="下一句" style="width:36px;height:36px;border-radius:10px;border:2px solid rgba(255,255,255,.35);background:transparent;color:#fff;font-weight:800;font-size:0.9375rem;padding:0">→</button></span>
              <div style="display:flex;gap:6px;flex-wrap:wrap;margin-left:auto"><sc-for list="{{ selBadges }}" as="bd" hint-placeholder-count="1"><span style="font-size:0.6875rem;font-weight:800;padding:2px 8px;border-radius:6px;background:{{ bd.bg }};color:{{ bd.fg }};border:2px solid {{ bd.border }}">{{ bd.label }}</span></sc-for><sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-size:0.625rem;padding:2px 8px;border-radius:6px;border:2px solid rgba(255,255,255,.35);color:#fff;font-family:ui-monospace,Menlo,monospace">{{ selPro }}</span></sc-if></div>
            </div>
            <div style="padding:16px 16px 18px;display:flex;flex-direction:column;gap:14px">
              <div>
                <sc-if value="{{ notEditing }}" hint-placeholder-val="{{ true }}"><div style="font-size:1.0625rem;line-height:1.65;font-weight:600">{{ selText }}</div></sc-if>
                <sc-if value="{{ editing }}" hint-placeholder-val="{{ false }}"><textarea value="{{ editText }}" onChange="{{ onEditText }}" style="display:block;width:100%;min-height:80px;border:2px solid #4a3626;border-radius:12px;padding:10px 12px;font-size:1rem;line-height:1.6;resize:vertical;outline:none"></textarea><div style="display:flex;gap:8px;margin-top:8px"><button onClick="{{ saveEdit }}" style="height:40px;padding:0 14px;border-radius:10px;background:#4a3626;color:#fff;border:2px solid #4a3626;font-weight:800;font-size:0.8125rem">记下修改</button><button onClick="{{ cancelEdit }}" style="height:40px;padding:0 12px;border-radius:10px;background:#fff;border:1.5px solid #dccfbb;font-weight:700;font-size:0.8125rem">算了</button></div></sc-if>
                <div style="font-size:0.8125rem;color:#75645a;margin-top:6px;line-height:1.5">{{ selNote }}</div>
                <sc-if value="{{ hasSelFail }}" hint-placeholder-val="{{ false }}"><div style="margin-top:8px;background:#fbeee6;border:2px solid #c94a2c;border-radius:10px;padding:8px 12px;font-size:0.8125rem"><b style="color:#c94a2c">上次换画面没成功：</b>{{ selFailText }}<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px"><sc-for list="{{ selFailFixes }}" as="fx" hint-placeholder-count="2"><button onClick="{{ fx.go }}" style="min-height:34px;padding:0 12px;border-radius:999px;border:2px solid #4a3626;background:#fff;font-size:0.75rem;font-weight:800;color:#4a3626">{{ fx.label }}</button></sc-for></div></div></sc-if>
              </div>

              <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px">
                <sc-for list="{{ selBeats }}" as="b" hint-placeholder-count="1">
                  <div><div style="position:relative;aspect-ratio:16/9;border-radius:10px;overflow:hidden;border:1.5px solid #dccfbb;background:#4a3626"><span style="position:absolute;inset:0;background-image:url({{ b.thumb }});background-size:cover;background-position:center;display:block"></span><sc-if value="{{ b.showText }}" hint-placeholder-val="{{ false }}"><span style="position:absolute;bottom:6px;left:8px;background:rgba(74,54,38,.9);color:#fff;font-size:0.6875rem;font-weight:800;padding:2px 7px;border-radius:4px">{{ b.text }}</span></sc-if></div><div style="font-size:0.6875rem;color:#75645a;margin-top:4px;line-height:1.4">{{ b.desc }}<sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><span style="font-family:ui-monospace,Menlo,monospace"> · shot {{ b.shot }} · {{ b.conf }}</span></sc-if></div></div>
                </sc-for>
              </div>

              <sc-if value="{{ canEdit }}" hint-placeholder-val="{{ true }}">
              <sc-if value="{{ selIsNarr }}" hint-placeholder-val="{{ true }}"><sc-if value="{{ proposed }}" hint-placeholder-val="{{ true }}"><div>
                <div style="font-weight:900;font-size:0.8125rem;color:#75645a;margin-bottom:6px">① 改这句话</div>
                <div data-r="wrap" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
                  <button onClick="{{ startEdit }}" style="height:42px;padding:0 16px;border-radius:10px;background:#fff;border:2px solid #4a3626;font-weight:800;font-size:0.8125rem;color:#4a3626">✎ 改字</button>
                  <button onClick="{{ recordVoice }}" style="height:42px;padding:0 16px;border-radius:10px;background:#fff;border:2px solid {{ recBorder }};color:{{ recFg }};font-weight:800;font-size:0.8125rem">{{ recLabel }}</button>
                </div>
                <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#75645a;margin-top:6px">改字 / 录音 / 调语速：remix 仅 keep_sentence_ids、preferences 建任务时固定、Permissions-Policy 禁用麦克风 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
              </div></sc-if>
              <sc-if value="{{ current }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.8125rem;color:#75645a">要改这句的字，回到第 1 步改稿后重新提交（会用掉 1 次提交机会）。改字、录音、调语速即将推出。</div></sc-if>

              <div style="border-top:2px dashed #dccfbb;padding-top:12px">
                <div style="font-weight:900;font-size:0.8125rem;color:#75645a;margin-bottom:6px">② 换个画面 <span style="font-weight:400">· 只改画面，旁白字幕不变 · 还有 <b style="color:#4a3626">{{ poolLeft }}</b> 个没用过的镜头</span></div>
                <div style="display:flex;gap:8px;align-items:stretch">
                  <input type="text" value="{{ instruction }}" onChange="{{ onInstruction }}" placeholder="想要什么画面？例如：人多热闹 / 汽车" maxlength="500" style="flex:1;min-width:0;height:44px;border:1.5px solid #dccfbb;border-radius:12px;padding:0 12px;font-size:0.875rem;outline:none" style-focus="border-color:#4a3626">
                  <button onClick="{{ queueReplace }}" style="height:44px;padding:0 14px;border-radius:12px;background:{{ replaceBg }};color:#fff;border:2px solid {{ replaceBg }};font-weight:800;font-size:0.8125rem;white-space:nowrap">{{ replaceLabel }}</button>
                </div>
                <div style="margin-top:8px;font-size:0.75rem;color:#75645a">{{ candTitle }}</div>
                <div style="display:flex;flex-direction:column;gap:6px;margin-top:6px">
                  <sc-for list="{{ candShots }}" as="cs" hint-placeholder-count="3"><button onClick="{{ cs.use }}" style="display:flex;align-items:center;gap:10px;min-height:48px;padding:4px 10px 4px 4px;border-radius:10px;border:2px solid {{ cs.border }};background:{{ cs.bg }};text-align:left;color:#4a3626;width:100%" style-hover="border-color:#4a3626"><span style="width:72px;height:40px;border-radius:6px;background-image:url({{ cs.thumb }});background-size:cover;background-position:center;flex:none;display:block"></span><span style="font-size:0.75rem;font-weight:700;line-height:1.35;min-width:0">{{ cs.desc }}</span></button></sc-for>
                  <sc-if value="{{ candMore }}" hint-placeholder-val="{{ false }}"><button onClick="{{ toggleCandMore }}" style="height:36px;border-radius:10px;border:1.5px dashed #dccfbb;background:transparent;font-size:0.75rem;font-weight:800;color:#237f4a">{{ candMoreLabel }}</button></sc-if>
                  <sc-if value="{{ noCand }}" hint-placeholder-val="{{ false }}"><span style="font-size:0.75rem;color:#c94a2c;font-weight:700">没用过的镜头都用完了——删掉这句，或回去多传素材。</span></sc-if>
                </div>
                <div style="margin-top:8px;font-size:0.75rem;color:#75645a">{{ replaceHint }}</div>
                <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#75645a;margin-top:6px">全镜头浏览与直接选镜 · 当前 API 重剪跑 7–10，换镜跑 6/9/10，两者需分两次提交；合并提交与批量换镜 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
              </div>

              <div style="border-top:2px dashed #dccfbb;padding-top:12px">
                <div style="font-weight:900;font-size:0.8125rem;color:#75645a;margin-bottom:6px">③ 不要这句 <span style="font-weight:400">· 其他句子照原样，至少留一句</span></div>
                <label style="display:flex;align-items:center;gap:10px;min-height:46px;padding:0 12px;border-radius:12px;border:2px solid {{ delBorder }};background:{{ delBg }};font-weight:800;color:{{ delFg }};cursor:pointer"><input type="checkbox" checked="{{ selDeleted }}" onChange="{{ toggleDeleteSel }}" style="width:20px;height:20px;accent-color:#c94a2c">删掉第 {{ selIdx }} 句</label>
                <sc-if value="{{ selFactWarn }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#c94a2c;font-weight:700;margin-top:6px">这句有{{ selFacts }}，删掉后新闻会缺信息。</div></sc-if>
              </div>
              </sc-if>
              <sc-if value="{{ selIsQuote }}" hint-placeholder-val="{{ false }}">
              <div>
                <div style="font-weight:900;font-size:0.8125rem;color:#75645a;margin-bottom:6px">① 剪短这句 <span style="font-weight:400">· 点要保留的词 · 只能剪短，不能改字</span></div>
                <div style="line-height:1.9;font-size:0.9375rem"><sc-for list="{{ trimWords }}" as="w" hint-placeholder-count="6"><button onClick="{{ w.pick }}" style="display:inline-block;min-width:28px;min-height:32px;padding:2px 5px;border-radius:4px;border:0;background:{{ w.bg }};color:{{ w.fg }};text-decoration:{{ w.deco }};font-weight:600;font-size:0.9375rem;line-height:1.5;margin:0 1px 4px 0">{{ w.text }}</button></sc-for></div>
                <div style="position:relative;height:8px;border-radius:4px;background:#f0e7d8;margin-top:6px;background-image:repeating-linear-gradient(90deg,rgba(74,54,38,.18) 0 2px,transparent 2px 5px)"><span style="position:absolute;top:0;bottom:0;left:{{ trimL }}%;width:{{ trimW }}%;background:#237f4a;border-radius:4px;display:block"></span></div>
                <div data-r="wrap" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px;font-size:0.75rem;color:#75645a"><span style="font-family:ui-monospace,Menlo,monospace">{{ trimRange }}</span><button onClick="{{ trimStartBack }}" style="height:30px;padding:0 8px;border-radius:8px;border:1.5px solid #dccfbb;background:#fff;font-size:0.75rem;font-weight:800;color:#4a3626">◁ 0.2s</button><button onClick="{{ trimEndFwd }}" style="height:30px;padding:0 8px;border-radius:8px;border:1.5px solid #dccfbb;background:#fff;font-size:0.75rem;font-weight:800;color:#4a3626">0.2s ▷</button><span style="margin-left:auto;display:flex;gap:6px"><button onClick="{{ saveTrim }}" style="height:36px;padding:0 12px;border-radius:10px;background:{{ trimSaveBg }};color:#fff;border:2px solid {{ trimSaveBg }};font-weight:800;font-size:0.8125rem">记下剪短</button><button onClick="{{ cancelTrim }}" style="height:36px;padding:0 10px;border-radius:10px;background:#fff;border:1.5px solid #dccfbb;font-weight:700;font-size:0.8125rem;color:#4a3626">算了</button></span></div>
                <sc-if value="{{ trimPending }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#237f4a;font-weight:700;margin-top:6px">{{ trimPendingText }}</div></sc-if>
                <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#75645a;margin-top:6px">词级时间戳 · 切点吸附词边界与静音（≤ 300ms）· 头留 120ms 尾留 200ms · 相邻原声间隙 150ms · remix 7–10 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
              </div>
              <div style="border-top:2px dashed #dccfbb;padding-top:12px">
                <div style="font-weight:900;font-size:0.8125rem;color:#75645a;margin-bottom:6px">② 换一段 <span style="font-weight:400">· {{ altTitle }}</span></div>
                <div style="display:flex;flex-direction:column;gap:6px">
                  <sc-for list="{{ altTakes }}" as="ak" hint-placeholder-count="1"><button onClick="{{ ak.pick }}" style="display:flex;align-items:center;gap:10px;min-height:48px;padding:4px 10px 4px 4px;border-radius:10px;border:2px solid {{ ak.border }};background:{{ ak.bg }};text-align:left;color:#4a3626;width:100%" style-hover="border-color:#4a3626"><span style="width:72px;height:40px;border-radius:6px;background-color:#f0e7d8;background-image:repeating-linear-gradient(90deg,#237f4a 0 2px,transparent 2px 5px);background-size:100% 55%;background-position:center;background-repeat:repeat-x;flex:none;position:relative;display:block;border:1px solid #dccfbb"><span style="position:absolute;right:3px;bottom:2px;font-size:0.625rem;font-family:ui-monospace,Menlo,monospace;background:#fff;padding:0 3px;border-radius:3px">{{ ak.dur }}</span></span><span style="min-width:0"><span style="display:block;font-size:0.75rem;font-weight:700;line-height:1.35">{{ ak.text }}</span><span style="display:block;font-size:0.6875rem;color:#75645a">{{ ak.meta }}</span></span></button></sc-for>
                  <sc-if value="{{ canToNarr }}" hint-placeholder-val="{{ false }}"><button onClick="{{ toggleToNarr }}" style="height:40px;border-radius:10px;border:2px dashed {{ toNarrBorder }};background:{{ toNarrBg }};font-weight:800;font-size:0.8125rem;color:{{ toNarrFg }}">{{ toNarrLabel }}</button></sc-if>
                </div>
                <div data-r="wrap" style="margin-top:10px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;font-size:0.75rem;color:#75645a"><span>说话人</span><input type="text" value="{{ selSpkName }}" onChange="{{ onSelSpkName }}" placeholder="姓名" maxlength="8" style="width:90px;height:32px;border:2px solid {{ selSpkBorder }};border-radius:8px;padding:0 8px;font-size:0.75rem;outline:none;background:#fff"><input type="text" value="{{ selSpkRole }}" onChange="{{ onSelSpkRole }}" placeholder="身份" maxlength="12" style="width:140px;height:32px;border:1.5px solid #dccfbb;border-radius:8px;padding:0 8px;font-size:0.75rem;outline:none;background:#fff"><span style="color:#8a5a0b;font-weight:700">{{ selSpkHint }}</span></div>
                <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#75645a;margin-top:6px">alt_takes / take_id · 改成旁白 = 删原声句 + 新旁白句（remix 6–10）· 说话人名字只重跑 8–10 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
              </div>
              <div style="border-top:2px dashed #dccfbb;padding-top:12px">
                <div style="font-weight:900;font-size:0.8125rem;color:#75645a;margin-bottom:6px">③ 不要这句 <span style="font-weight:400">· 其他句子照原样，至少留一句</span></div>
                <label style="display:flex;align-items:center;gap:10px;min-height:46px;padding:0 12px;border-radius:12px;border:2px solid {{ delBorder }};background:{{ delBg }};font-weight:800;color:{{ delFg }};cursor:pointer"><input type="checkbox" checked="{{ selDeleted }}" onChange="{{ toggleDeleteSel }}" style="width:20px;height:20px;accent-color:#c94a2c">删掉第 {{ selIdx }} 句</label>
              </div>
              </sc-if>

              
            </div>
          </div>
        </div>

        <sc-if value="{{ hasPending }}" hint-placeholder-val="{{ false }}">
        <div data-r="stack" style="position:sticky;bottom:12px;display:flex;align-items:center;gap:12px;background:#4a3626;color:#fff;border-radius:16px;padding:12px 18px;box-shadow:0 10px 30px rgba(74,54,38,.3);animation:gm-pop .25s ease-out;z-index:5">
          <div style="min-width:0"><div style="font-weight:800">{{ pendingTitle }}</div><div style="font-size:0.75rem;opacity:.8">{{ pendingDetail }}</div></div>
          <div data-r="pendbtns" style="display:flex;gap:8px;margin-left:auto;flex:none">
            <button onClick="{{ clearPending }}" style="height:44px;padding:0 14px;border-radius:12px;background:transparent;color:#fff;border:2px solid rgba(255,255,255,.4);font-weight:700;font-size:0.875rem;white-space:nowrap">全部撤销</button>
            <button onClick="{{ applyPending }}" style="height:44px;padding:0 18px;border-radius:12px;background:#f2b632;color:#4a3626;border:2px solid #f2b632;font-weight:900;font-size:0.875rem;white-space:nowrap">{{ applyLabel }}</button>
          </div>
        </div>
        </sc-if>
      </div>
      </sc-if>
```

## A-6 屏幕：作品不存在

```html
<sc-if value="{{ isGone }}" hint-placeholder-val="{{ false }}">
      <div data-screen-label="作品不存在" data-doc="超过 72 小时未发布/未保留的作品已被清理" style="max-width:560px;margin:40px auto;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:4px 4px 0 #4a3626;padding:28px 26px;text-align:center">
        <div style="font-size:2.5rem;line-height:1">∅</div>
        <h1 style="margin:10px 0 0;font-size:1.5rem;font-weight:900">这个作品已经不在了</h1>
        <div style="color:#75645a;margin-top:6px">可能已被删除，或者过了保留期被清理了。</div>
        <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.75rem;color:#75645a;margin-top:6px;font-family:ui-monospace,Menlo,monospace">GET /api/tasks/{id} → 404 · Token 失效或任务不存在</div></sc-if>
        <div style="display:flex;gap:10px;justify-content:center;margin-top:18px;flex-wrap:wrap"><button onClick="{{ removeGone }}" style="height:48px;padding:0 20px;border-radius:12px;background:#4a3626;color:#fff;border:2px solid #4a3626;font-weight:800;font-size:0.9375rem">从列表移除</button><button onClick="{{ newTask }}" style="height:48px;padding:0 18px;border-radius:12px;background:#fff;color:#4a3626;border:2px solid #4a3626;font-weight:700;font-size:0.9375rem">新作品</button></div>
      </div>
      </sc-if>
    </main>
  </div>

  <sc-if value="{{ drawerOpen }}" hint-placeholder-val="{{ false }}">
  <div data-r="drawer" style="position:fixed;inset:0;z-index:40;display:flex">
    <div style="width:min(300px,84vw);background:#f9f3e6;border-right:2px solid #4a3626;padding:16px 14px;display:flex;flex-direction:column;gap:10px;overflow:auto">
      <div style="display:flex;align-items:center;gap:10px"><span style="font-weight:900;font-size:1rem">{{ drawerTitle }}</span><button onClick="{{ closeDrawer }}" aria-label="关闭" style="margin-left:auto;width:44px;height:44px;border-radius:12px;border:1.5px solid #dccfbb;background:#fff;font-size:1.125rem">×</button></div>
      <button onClick="{{ toggleFont }}" aria-pressed="{{ bigFont }}" style="height:44px;border-radius:12px;border:1.5px solid #dccfbb;background:#fff;font-weight:800;font-size:0.8125rem;color:#4a3626">字号：{{ fontBtnLabel }}</button>
        
        <button onClick="{{ newTask }}" style="height:48px;border-radius:14px;background:#4a3626;color:#fff;border:2px solid #4a3626;font-weight:800;font-size:0.9375rem">＋ 新作品</button>
        
        <sc-for list="{{ histItems }}" as="h" hint-placeholder-count="3">
          <button onClick="{{ h.open }}" style="text-align:left;width:100%;border-radius:14px;padding:12px 14px;background:#fff;border:2px solid {{ h.border }};box-shadow:{{ h.shadow }};color:#4a3626">
            <div style="font-weight:800;font-size:0.875rem;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">{{ h.title }}</div>
            <div style="margin-top:6px;display:flex;gap:6px;align-items:center;font-size:0.75rem;color:#75645a;flex-wrap:wrap"><span style="font-weight:800;color:{{ h.statusFg }};background:{{ h.statusBg }};border:1.5px solid {{ h.statusBorder }};border-radius:6px;padding:1px 7px">{{ h.status }}</span><span>{{ h.mode }}</span><span>{{ h.rev }}</span><span>{{ h.date }}</span><span style="color:{{ h.leftFg }};font-weight:{{ h.leftW }}">{{ h.left }}</span></div>
          </button>
        </sc-for>
    </div>
    <button onClick="{{ closeDrawer }}" aria-label="关闭" style="flex:1;background:rgba(74,54,38,.45);border:0"></button>
  </div>
  </sc-if>

  

  

  <sc-if value="{{ exportOpen }}" hint-placeholder-val="{{ false }}">
  <div role="dialog" aria-modal="true" style="position:fixed;inset:0;z-index:50;display:grid;place-items:center;background:rgba(74,54,38,.45);padding:20px">
    <div style="width:min(540px,100%);max-height:90vh;overflow:auto;background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:6px 6px 0 #4a3626;padding:22px 22px 18px;animation:gm-pop .2s ease-out;display:grid;gap:12px">
      <div style="font-weight:900;font-size:1.25rem">导出 / 分享</div>
      <div style="display:grid;gap:6px"><div style="font-size:0.75rem;font-weight:800;color:#75645a">导出什么</div><div style="display:flex;gap:8px;flex-wrap:wrap"><sc-for list="{{ expFmt }}" as="c" hint-placeholder-count="5"><button onClick="{{ c.pick }}" style="min-height:40px;padding:0 12px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:800;font-size:0.8125rem">{{ c.label }}</button></sc-for></div></div>
      <sc-if value="{{ expIsVideo }}" hint-placeholder-val="{{ true }}">
      <div style="display:grid;gap:6px"><div style="font-size:0.75rem;font-weight:800;color:#75645a">画幅</div><div style="display:flex;gap:8px;flex-wrap:wrap"><sc-for list="{{ expAspect }}" as="c" hint-placeholder-count="3"><button onClick="{{ c.pick }}" style="min-height:40px;padding:0 12px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:800;font-size:0.8125rem">{{ c.label }}</button></sc-for></div></div>
      <div style="display:grid;gap:6px"><div style="font-size:0.75rem;font-weight:800;color:#75645a">清晰度</div><div style="display:flex;gap:8px;flex-wrap:wrap"><sc-for list="{{ expRes }}" as="c" hint-placeholder-count="2"><button onClick="{{ c.pick }}" style="min-height:40px;padding:0 12px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:800;font-size:0.8125rem">{{ c.label }}</button></sc-for></div></div>
      <div style="display:grid;gap:6px"><div style="font-size:0.75rem;font-weight:800;color:#75645a">字幕</div><div style="display:flex;gap:8px;flex-wrap:wrap"><sc-for list="{{ expSub }}" as="c" hint-placeholder-count="3"><button onClick="{{ c.pick }}" style="min-height:40px;padding:0 12px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:800;font-size:0.8125rem">{{ c.label }}</button></sc-for></div></div>
      </sc-if>
      <div style="font-size:0.8125rem;color:#75645a">{{ expNote }}</div>
      <sc-if value="{{ dev }}" hint-placeholder-val="{{ false }}"><div style="font-size:0.6875rem;color:#75645a">后端现只输出 1080p 16:9 MP4；GIF / MP3 / SRT / 封面图 / 竖屏正方形裁切 / 字幕样式 / 720p 转码 <span style="font-family:ui-monospace,Menlo,monospace;font-size:0.625rem;font-weight:700;padding:2px 6px;border:1px dashed #c94a2c;color:#c94a2c;border-radius:4px">需新增后端</span></div></sc-if>
      
      <div style="display:flex;gap:10px;justify-content:flex-end"><button onClick="{{ closeExport }}" style="height:46px;padding:0 18px;border-radius:12px;background:#fff;color:#4a3626;border:2px solid #4a3626;font-weight:700;font-size:0.9375rem">关闭</button><button onClick="{{ doExport }}" style="height:46px;padding:0 18px;border-radius:12px;background:{{ expOkBg }};color:#fff;border:2px solid {{ expOkBg }};font-weight:800;font-size:0.9375rem">{{ expOkLabel }}</button></div>
    </div>
  </div>
  </sc-if>

  <sc-if value="{{ confirmOpen }}" hint-placeholder-val="{{ false }}">
  <div role="dialog" aria-modal="true" style="position:fixed;inset:0;z-index:50;display:grid;place-items:center;background:rgba(74,54,38,.45);padding:20px">
    <div style="width:min(440px,100%);background:#fff;border:2px solid #4a3626;border-radius:18px;box-shadow:6px 6px 0 #4a3626;padding:22px 22px 18px;animation:gm-pop .2s ease-out">
      <div style="font-weight:900;font-size:1.25rem">{{ cTitle }}</div>
      <div style="margin-top:6px;color:#75645a">{{ cBody }}</div>
      <sc-if value="{{ cHasInput }}" hint-placeholder-val="{{ false }}"><input type="text" value="{{ cText }}" onChange="{{ onCText }}" placeholder="{{ cPlaceholder }}" style="width:100%;height:44px;margin-top:12px;border:1.5px solid #dccfbb;border-radius:12px;padding:0 14px;font-size:1rem;outline:none"></sc-if>
      <sc-if value="{{ cHasChoices }}" hint-placeholder-val="{{ false }}"><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px"><sc-for list="{{ cChoices }}" as="c" hint-placeholder-count="3"><button onClick="{{ c.pick }}" style="min-height:40px;padding:0 12px;border-radius:999px;border:2px solid {{ c.border }};background:{{ c.bg }};color:{{ c.fg }};font-weight:800;font-size:0.8125rem">{{ c.label }}</button></sc-for></div></sc-if>
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px"><button onClick="{{ confirmCancel }}" style="height:46px;padding:0 18px;border-radius:12px;background:#fff;color:#4a3626;border:2px solid #4a3626;font-weight:700;font-size:0.9375rem">先不要</button><button onClick="{{ confirmOk }}" style="height:46px;padding:0 18px;border-radius:12px;background:{{ cOkBg }};color:#fff;border:2px solid {{ cOkBg }};font-weight:800;font-size:0.9375rem">{{ cOk }}</button></div>
    </div>
  </div>
  </sc-if>

  <sc-if value="{{ toastOpen }}" hint-placeholder-val="{{ false }}">
  <div role="status" style="position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:60;background:#4a3626;color:#fff;font-weight:700;padding:10px 12px 10px 18px;border-radius:999px;box-shadow:0 10px 30px rgba(74,54,38,.35);animation:gm-pop .2s ease-out;max-width:90vw;display:flex;align-items:center;gap:12px">{{ toastText }}<sc-if value="{{ toastHasAct }}" hint-placeholder-val="{{ false }}"><button onClick="{{ toastActFn }}" style="height:34px;padding:0 14px;border-radius:999px;border:0;background:#f2b632;color:#4a3626;font-weight:900;font-size:0.8125rem">{{ toastActLabel }}</button></sc-if></div>
  </sc-if>
</div>
```


# 附录 B · 逻辑源码（`class Component extends DCLogic`）

> 结构：B-1 文件头实现说明 → B-2 §A–§D 常量与纯函数 → B-3 §E 组件类（state、生命周期、动作方法、`checksFor`、`applyPending`、`renderVals`）。

## B-1 文件头实现说明

```js
/* =====================================================================================
 * 金话筒 · 新闻视频生成工具 —— 前端原型 v2 实现说明（供 GPT-6 Astra / 工程师据此实现真实产品）
 * =====================================================================================
 * 这是一个可运行的高保真原型：UI、状态机、数据模型、文案与检查规则都是最终规格；所有 AI/后端行为是前端模拟（§6）。
 * 目标用户：记者（零剪辑经验）。产品内没有账号/角色，也没有按人群变化的默认值。
 * 产品闭环：写稿 + 传素材 + 选效果 → AI 自动成片（10 个阶段）→ 结果页按「句」检查与修改（先记下、再一次性应用）→ 检查通过后导出。
 *
 * 文件结构（从上到下）
 *   §A 视觉常量（INK/TEAL/RED/LINE/MUTED/GOLD）· 十阶段 STAGES{n,devName,label,doing} · 范例成片数据 SHOT/ROWS/SAMPLE_*
 *   §B 三种制作模式 MODES/MODE_NAME · 按模式改写的阶段文案 STAGE_MODE · 阶段权重 STAGE_W · 模式默认偏好 defaultPrefs
 *   §C 稿件分析：五要素识别 RX · 字数/语速 · 事实项提取 · 句子拆分 scriptUnits/parseLine/splitQuote
 *   §D 原声句：转写 transcriptOf · 对齐 matchQuote/sim · 说话人 speakersOf · 成片行 buildRows/markJumps · 按词分块 wordsOf
 *   §E class Component：state → 动作方法 → checksFor/gateOf（发布前检查）→ applyPending（记下→应用）→ renderVals（模板视图模型）
 *
 * §1 屏幕 S.screen（模板用 data-screen-label 标出）
 *   create      新建作品：三步向导 S.step  1 写稿（模式卡、标题/正文、五要素、句子清单）→ 2 传素材（逐文件校验、预转写、匹配卡、说话人卡、充足度）→ 3 选效果（prefs）→ start()
 *   processing  制作中：上传进度 → 排队 → 10 个阶段（文案按模式取 stageDef(n,mode)，进度按 STAGE_W 加权）→ 成功进 result；失败显示 pFailActions
 *   result      结果页：播放器 + 故事板 cells + 发布前检查 checks + 「第 N 句」面板 + 待应用浮条；所有修改先记下（pendEdit/pendReplace/pendTrim… 与 deleted），applyPending() 一次性提交 → 回到 processing → 新版本
 *   gone        作品已被清理（超过 LIMITS.ttlH=72 小时）
 *   覆盖层：我的作品抽屉 drawerOpen（≤900px）· 导出弹窗 exportOpen · 确认弹窗 confirm · toast（可带一个动作按钮，如「撤销」）· 首次引导卡 showIntro
 *
 * §2 三种制作模式 S.mode / task.mode（界面只用 MODE_NAME 的中文名）
 *   voiceover「AI 配音」全部旁白句，素材人声压低；mixed「旁白 + 原声」旁白句 + 原声句混合；original「只用原声」全部原声句，无配音、无语速检查，有跳切遮盖
 *   句子类型 scriptUnits(script,mode,typeMarks)：行首「同期：」「【同期】」「姓名（身份）：」= 原声句 quote（只按句末标点拆）；其余 = 旁白句 narration（按逗号句号拆）；typeMarks[text] 为用户 chip 覆盖
 *   原声句不能改字：只能剪短 pendTrim / 换一段 pendTake / 改成旁白 pendToNarration（仅 mixed）/ 删掉
 *   对齐阈值（第 1 步、第 2 步、结果页一致）：相似度 ≥0.85 对上了 · 0.6–0.85 不太确定（需勾选）· <0.6 没找到（阻断）
 *   跳切 markJumps()：相邻原声句来自不同文件或同文件间隔 >0.5s → 1 处；遮盖 prefs.jump_cut_cover broll（默认，空镜不足自动降级 zoom）| zoom | hard
 *   说话人 speakersOf()：分说话人 id → 名字来自稿子「姓名（身份）」或 speakerNames；未命名人名条显示「受访者」；人名条首次出现 2.5s，同一人隔 ≥60s 重显
 *   智能建议（每种一次，suggestOff）：voiceover 下有稿句与原话相似 ≥0.85 → 建议切 mixed；original 下有句没找到 → 建议切 mixed
 *
 * §3 任务对象 task（S.tasks[id]；S.history 为 id 列表，最新在前）
 *   id, title(≤32 字), mode, createdAt/startedAt/doneAt, script, prefs(§4), speakers{spk:{name,role}}
 *   files[{name,sec,mb,img,inSec,outSec,note,speech,asr:'pending'|'done'|'failed',transcript[{t0,t1,spk,text}]}]
 *   status 'uploading'|'queued'|'running'|'done'|'failed' · progress 0-100 · current 1-10 · queue · stages[{n,status,msg,elapsed}]
 *   plan{steps:[{op,run:[阶段号]}],idx}：每个 step = 一次真实 API 调用。首版 1–10；remix（删/改字/录音/语速/剪短/换段/转旁白）7–10；只改说话人 8–10；换画面 replace-shot 6,9,10
 *   rows[] 成片的「句」：{id, kind:'narration'|'quote', s:文本, d:秒, c:置信度/相似度, beats:[{shot,t?}], fallback, gen, mine, myCpm, overlay, miss[], edited, replaced}
 *     原声句另有 spoken(实际说的话) spk file t0 t1 snr alts[] missing jump:'file'|'gap' cover coverShot trimmed took
 *   revision(修改次数) · versions[{rev,op,rows}] · replaceFails{rowId:{kind:'none'|'used'|'abstract',text}}
 *   errorKind 'network'|'transient'|'shortage'|'qc'|'quote_missing' · error(给记者) / errorPro(给开发者) · badRows · shortBy · upTotal/upDone · gone
 *
 * §4 制作偏好 prefs（随 POST /api/tasks 的 preferences 完整发送；target_cpm 由 pacing 推出）
 *   pacing slow|normal|fast(PACING_CPM 230/265/290) · tone · voice ai|mine · caption_style news|big|none · background_music + music_mood · motion_effects · transitions · news_graphics · color_consistency · enhance_speech · generative_fill(默认关，original 不可用) · custom_instructions(≤500)
 *   mixed/original 另有 lower_third(人名条) · jump_cut_cover · quote_caption spoken|none。各模式的显隐与默认值见 defaultPrefs() 与 renderVals 的 show* 键
 *
 * §5 发布前检查 checksFor(t,rows)（与后端 quality 规则同源；语速窗口 = 所选播音速度 ±RATE_TOL，只对旁白句）
 *   sev 0 阻断：MATCH_FALLBACK / EXPLICIT_ENTITY_NOT_COVERED / FREEZE_PAD_EXCESSIVE / generated_media(知情勾选) / QUOTE_NOT_FOUND / QUOTE_TOO_LONG(>30s)
 *   sev 1 待勾：LOW_MATCH_CONFIDENCE / VISUAL_CLIP_TOO_LONG / NARRATION_SPEAKING_RATE / FACT_CHECK / QUOTE_MATCH_LOW / QUOTE_TOO_LONG(>20s) / QUOTE_AUDIO_NOISY / QUOTE_TEXT_DIFFERS / SPEAKER_UNNAMED / JUMP_CUT_UNCOVERED
 *   sev 2 提示：CONTEXTUAL_BROLL_OVERLAY / MIXED_NO_NARRATION
 *   gateOf(t) → {blocking,open,passed}；passed 才能正常导出；未通过时播放器叠样片水印。props.gateMode 'block' 时阻断项直接让制作失败（qcFail）
 *
 * §6 前端模拟 → 真实后端 对照
 *   start()                 → POST /api/tasks（multipart: script, files[], preferences, mode）→ task_id；上传进度用 XHR progress
 *   addFiles() 预转写         → POST /api/tasks/{id}/pretranscribe（ASR + 分说话人 + 词级时间戳）【需新增后端】
 *   matchQuote()            → POST /api/tasks/{id}/align（稿句 ↔ 转写强制对齐）【需新增后端】
 *   uploadOnly()/tick()/runTask() 用 SIM 时长伪造推进 → 改为轮询 GET /api/tasks/{id}
 *   complete()              → GET /api/tasks/{id}/report → rows/quality
 *   applyPending()          → POST /api/tasks/{id}/remix（删/改字/录音/语速/剪短/换段/转旁白/说话人）· POST /api/tasks/{id}/replace-shot（换画面，每句一次）【剪短/换段/转旁白/说话人 需新增后端】
 *   restoreVersion()        → GET /workbench/versions · POST /workbench/restore【需新增后端】
 *   record()                → POST /workbench/recordings（浏览器 MediaRecorder）【需新增后端】
 *   播放器/缩略图             → GET /api/tasks/{id}/video · /poster · /thumbs/{shot_id}.jpg（原型用 assets/shots/shot_N.jpg）
 *   deleteTask()            → DELETE /api/tasks/{id}（取消并永久删除）
 *   LIMITS 与 GET /api/config/limits 一致；HTTP 错误映射见 errorKind；无幂等键，超时不要自动重试
 *
 * §7 本地持久化 localStorage[SAVE_KEY]：tasks/history/checked/pend/draft/introDismissed 等（persist()/restore()），草稿随写随存，刷新后自动放回
 *
 * §8 Tweaks（data-props，均为演示/开发用途，生产不保留）
 *   mode 直达三种模式 · demoScenario normal|quoteMissing|noBroll · scenario netfail|failed|shortage|maintenance · gateMode warn|block · simSpeed · startScreen · demoHelpers（「填入示例」按钮）· devNotes（dev 标签、后端说明、preferences JSON）
 *
 * §9 v2 上手与视觉优化（纯前端，不改接口与数据字段）
 *   首次引导卡 showIntro/introDismissed（页头「怎么用」重开）· 模式卡默认只露 AI 配音 modeMore · 文件行高级项收起 fileAdv · 换画面候选露 3 个 candMore · 删文件可撤销 toast(msg,{label,fn})
 *   窄屏结果页顺序 播放器 → 故事板 → 发布前检查 → 句面板 · 色板同色相提亮一档（TEAL #237f4a · GOLD #f2b632 · RED #c94a2c）· 次级描边 1.5px · 格线 .03 · ≤600px 基准字号 17px
 *   手绘内联 SVG 图标（Logo/模式/上传/小贴士/箭头/打勾/制作中话筒），不引入图标库 · 微动效 gm-check / gm-rise / gm-wave
 *
 * §10 响应式（helmet 内按 data-r 属性写 @media，不写新 class）
 *   ≤1180 三栏→两栏 · ≤1000 结果页 rgrid 单栏并重排、匹配卡 mrow 两列 · ≤900 侧栏变抽屉、竖排、标题 1.5rem · ≤640 模式卡/开关卡单列 · ≤600 故事板横向滚动、基准 17px
 *   键盘：空格 播放/暂停 · ← → 上一句/下一句 · Delete 标记删句 · Esc 关闭弹窗/抽屉
 * ===================================================================================== */
```

## B-2 / B-3 完整逻辑

```js
// ---------- §A 视觉常量 · 十阶段 STAGES · 范例成片数据（SHOT/ROWS/SAMPLE_*，真实数据来自后端 report）
const INK='#4a3626',TEAL='#237f4a',RED='#c94a2c',LINE='#dccfbb',MUTED='#75645a',GOLD='#f2b632';
const STAGES=[
 {n:1,devName:'上传校验',label:'检查素材',doing:'看看每个视频能不能打开、格式对不对。'},
 {n:2,devName:'同期声识别与本地格式适配',label:'听素材里的声音',doing:'找出有人说话的片段，以后可以当同期声。'},
 {n:3,devName:'镜头切分',label:'切分镜头',doing:'把每段视频切成一个个镜头。'},
 {n:4,devName:'画面理解',label:'看懂每个画面',doing:'给每个镜头写一句“画面里有什么”。'},
 {n:5,devName:'文稿分句',label:'给稿子分句',doing:'把稿子切成一句一句，方便配画面。'},
 {n:6,devName:'语义匹配',label:'给每句话找画面',doing:'比较句子和画面的意思，挑最合适、不重复的镜头。'},
 {n:7,devName:'配音与同期声',label:'配音',doing:'朗读稿子；有现场原声或你录的音的地方用原声。'},
 {n:8,devName:'字幕生成',label:'做字幕',doing:'按读到的字，一句句打上字幕。'},
 {n:9,devName:'视频渲染',label:'合成视频',doing:'把画面、配音、字幕和音乐拼成一条片子。'},
 {n:10,devName:'完成',label:'质量检查',doing:'检查有没有画面不合适、读得太快的地方。'}];
// ---------- §B 三种制作模式（voiceover / mixed / original）：模式卡文案 MODES、阶段文案 STAGE_MODE、进度权重 STAGE_W、默认偏好 defaultPrefs
const MODES=[['voiceover','AI 配音','稿子全由 AI 读，素材只出画面','消息、活动报道、无人说话的素材','🎙'],['mixed','旁白 + 原声','旁白 AI 读，人说话的地方直接用现场原声','带采访的新闻、有受访者发言','🎙+🗣'],['original','只用原声','稿子就是采访原话，直接剪出来','人物专访、演讲、街访集锦','🗣']];
const MODE_NAME=m=>(MODES.find(x=>x[0]===m)||MODES[0])[1];
const STAGE_MODE={mixed:{2:['听采访','把每段话转成文字，分清谁在说。'],3:['切分空镜','把没人说话的画面切成一个个镜头。'],5:['分旁白和原声','把稿子切成一句句，标出哪些是人说的话。'],6:['配画面、对原话','旁白找空镜；原话在采访里找到它说的那几秒。'],7:['配音','朗读旁白；原声段降噪、调音量。'],8:['做字幕','旁白按读的字，原声按实际说的话；加人名条。'],9:['合成视频','把空镜、原声、配音、字幕拼成一条片子。'],10:['质量检查','检查画面、原声和字幕对不对得上。']},original:{2:['听采访','把每段话转成文字，分清谁在说。'],3:['找空镜','把没人说话的画面留下来，等会儿遮跳切。'],5:['对稿子','把稿子每句和转写对上。'],6:['找原话','在采访里找到每句话说的那几秒，定好剪切点。'],7:['整理原声','不用配音；把每段原声剪出来，降噪、调音量。'],8:['做字幕','按实际说的话打字幕，加人名条。'],9:['合成视频','按稿子顺序拼接原声，遮住跳切。'],10:['质量检查','检查每段原声剪得干不干净。']}};
const stageDef=(n,mode)=>{const b=STAGES[n-1],o=STAGE_MODE[mode]&&STAGE_MODE[mode][n];return o?{...b,label:o[0],doing:o[1]}:b;};
const STAGE_W={voiceover:[1,1,1,1,1,1,1,1,1,1],mixed:[1,2,1,1,1,2,1,1,1.5,1],original:[1,3,1,0.5,1,2.5,1,1,2.5,1]};
const defaultPrefs=mode=>({pacing:'normal',tone:'neutral',voice:'ai',caption_style:'news',enhance_speech:true,background_music:mode!=='original',music_mood:'auto',motion_effects:mode!=='original',transitions:true,news_graphics:true,color_consistency:true,generative_fill:false,lower_third:true,jump_cut_cover:'broll',quote_caption:'spoken',custom_instructions:''});
const SIM=[0.6,1.6,0.8,2.2,1.4,2.6,1.0,0.4,1.4,0.5];
const CHAR_SOFT=3000;
// 与后端 /api/config/limits 一致：单文件 500MiB / 总 5GiB / 20 个 / 单文件 30 分 / 总 60 分 / 每小时 2 次
const LIMITS={maxFiles:20,fileMb:500,totalGb:5,fileMin:30,totalMin:60,perHour:2,ttlH:72,maxShots:120,syncMax:15,clipMax:6.5,uploadMbps:60};
const PACING_CPM={slow:230,normal:265,fast:290},RATE_TOL=0.08;
const SAVE_KEY='gm-modes-v1',H=3600e3;
const stageMsg=(n,t)=>{const rows=t.rows.length,fb=t.rows.filter(r=>r.fallback).length,sy=t.rows.filter(r=>r.sync||r.mine).length,f=t.fileCount||5,q=t.rows.filter(r=>r.kind==='quote'),nr=rows-q.length,jc=t.rows.filter(r=>r.jump).length,m=t.mode||'voiceover';if(m!=='voiceover'){const spk=new Set(q.map(r=>r.spk).filter(Boolean)).size;const byN={2:' · 转写 '+q.length+' 段，分出 '+spk+' 位说话人',5:m==='mixed'?' · 旁白 '+nr+' 句，原声 '+q.length+' 句':' · '+q.length+' 句原话',6:m==='mixed'?' · 旁白 '+nr+' 句配上画面，原声 '+q.length+' 句对上原话':' · '+q.length+' 句全部对上，跳切 '+jc+' 处',7:m==='mixed'?' · 旁白 '+nr+' 句已配音，原声 '+q.length+' 段已整理':' · 原声 '+q.length+' 段已整理',8:' · 共 '+rows+' 条，人名条 '+spk+' 个'};if(byN[n])return byN[n];}return [' · 共 '+f+' 个文件',' · 识别 '+f+'/'+f+'，格式适配 '+f+' 个',' · 切出 20 个镜头',' · 可用 20/20 个镜头',' · 共 '+rows+' 个播音单元',' · '+rows+' 句，兜底 '+fb,' · '+rows+' 句，原声/录音 '+sy,' · 共 '+(rows+1)+' 条',' · 共 '+rows+' 句',' · 匹配报告 '+rows+' 行'][n-1];};
const SHOT={0:'南宁信息港楼前，市集场地布置完毕',1:'广场上的市集摊位，市民在摊位边咨询',2:'广场外的年货活动摊位，有市民路过',3:'蓝色台面上摆着白酒、红酒和礼盒',4:'摊位上摆满辣椒酱和瓶装饮料',5:'红白桌布上的广西烟熏腊肠、腊肉',6:'摊主在红白条纹棚下给顾客装菜',7:'摊位上一袋袋螺蛳粉、粉丝',8:'展位上的南宁老友粉礼盒',9:'集市摊位前，几位市民在咨询选购',11:'女孩在锅前往锅里加食材',13:'红白条纹摊位前，市民在选购',15:'广场上的展车和优惠立牌，一人靠着黑色 SUV',21:'高楼下的红白条纹摊位，市民在逛',22:'穿红衣的女生站在木牌旁，周围很热闹',28:'路边展台上摆着大米等货品',29:'市民在摊位前停下来看商品',32:'寿司摊前，摊主和小孩，人来人往',44:'桌上摆着白酒、红酒和新年礼盒',52:'一锅冒着热气的糖炒栗子'};
const thumb=id=>'assets/shots/shot_'+id+'.jpg';
const ROWS=[
 {id:0,s:'马年新春将至，',d:1.47,c:.68,beats:[{shot:2}]},
 {id:1,s:'位于南宁市西乡塘区鲁班路95号的南宁信息港广场1月30日举办了“金马贺岁，高新同驰”迎春市集，',d:9.63,c:.44,beats:[{shot:0}],overlay:'日期'},
 {id:2,s:'吸引众多市民前来赶集购年货、品年味。',d:3.85,c:.73,beats:[{shot:52}],gen:true},
 {id:3,s:'活动现场汇聚几十家参展企业，',d:2.82,c:.62,beats:[{shot:1}]},
 {id:4,s:'展品丰富多样。',d:1.7,c:.35,beats:[{shot:3}],overlay:'文字'},
 {id:5,s:'近20款新能源汽车科技感十足，',d:3.35,c:.81,beats:[{shot:15}]},
 {id:6,s:'各类传统年货如腊肉、糕点、酒类、礼盒等一应俱全，',d:5.98,c:.82,beats:[{shot:5,t:'腊肉、糕点'},{shot:44,t:'酒类、礼盒'}],miss:['糕点']},
 {id:7,s:'灌阳油茶、现做寿司等特色小吃引来众多品尝者。',d:5.08,c:.75,beats:[{shot:11,t:'灌阳油茶'},{shot:32,t:'现做寿司'}]},
 {id:8,s:'市集现场人流涌动，欢声不断，',d:3.34,c:.73,beats:[{shot:29}]},
 {id:9,s:'洋溢着一派喜庆祥和的新春气氛。',d:3.2,c:.62,beats:[{shot:13}]},
 {id:10,s:'本次活动由南宁信息港主办、悦和物业公司协办，',d:4.78,c:.35,beats:[{shot:28}],overlay:'机构名'},
 {id:11,s:'活动以市集为纽带，',d:2.14,c:.35,beats:[{shot:22}],overlay:'文字'},
 {id:12,s:'融合高新科技与传统节庆，',d:2.98,c:.31,beats:[{shot:8}],fallback:true},
 {id:13,s:'为市民打造了一个集购物、体验、娱乐于一体的迎春平台。',d:5.89,c:.65,beats:[{shot:9}]},
 {id:14,s:'本次活动将持续到1月31日。',d:2.68,c:.9,beats:[{shot:21}],sync:true,spoken:'我们这个市集会一直持续到1月31号'}];
const POOL=[4,6,7];const SHOT_SEC={0:10.4,7:3.8};const shotSec=id=>SHOT_SEC[id]!=null?SHOT_SEC[id]:8;
const SAMPLE_SCRIPT='数十家企业汇聚南宁信息港 迎春市集开张备年货\n马年新春将至，位于南宁市西乡塘区鲁班路95号的南宁信息港广场1月30日举办了“金马贺岁，高新同驰”迎春市集，吸引众多市民前来赶集购年货、品年味。\n活动现场汇聚几十家参展企业，展品丰富多样。近20款新能源汽车科技感十足，各类传统年货如腊肉、糕点、酒类、礼盒等一应俱全，灌阳油茶、现做寿司等特色小吃引来众多品尝者。市集现场人流涌动，欢声不断，洋溢着一派喜庆祥和的新春气氛。\n本次活动由南宁信息港主办、悦和物业公司协办，活动以市集为纽带，融合高新科技与传统节庆，为市民打造了一个集购物、体验、娱乐于一体的迎春平台。本次活动将持续到1月31日。';
const SAMPLE_FILES=[{name:'市集门口.mp4',sec:48,mb:212,shot:0},{name:'新能源车展区.mov',sec:72,mb:420,shot:15},{name:'年货摊位.mp4',sec:66,mb:410,shot:5},{name:'小吃摊.mp4',sec:41,mb:198,shot:32},{name:'主办方采访.mp4',sec:35,mb:180,speech:true,shot:21,spk:'S1'},{name:'摊主采访.mp4',sec:58,mb:290,speech:true,shot:6,spk:'S2',extra:true},{name:'市民采访.mp4',sec:40,mb:200,speech:true,shot:29,spk:'S3',extra:true},{name:'舞狮.wmv',sec:20,mb:95,bad:'这种格式不支持，转成 mp4 再传'}];
const sampleFilesFor=(mode,scn)=>{const base=SAMPLE_FILES.filter(f=>mode==='voiceover'?!f.extra:true);return (scn==='noBroll'&&mode!=='voiceover')?base.filter(f=>f.speech):base;};
const SAMPLE_SCRIPT_MIXED='数十家企业汇聚南宁信息港 迎春市集开张备年货\n马年新春将至，位于南宁市西乡塘区鲁班路95号的南宁信息港广场1月30日举办了“金马贺岁，高新同驰”迎春市集，吸引众多市民前来赶集购年货、品年味。\n同期 王红（市集主办方）：今年我们特意把新能源汽车和传统年货放在一起，就是想让大家感受高新和年味的结合。\n活动现场汇聚几十家参展企业，近20款新能源汽车科技感十足，各类传统年货如腊肉、酒类、礼盒等一应俱全。\n同期 李大姐（腊味摊摊主）：我们家的腊肠是自己熏的，用的是广西本地的猪肉。这两天人特别多，昨天一天卖了两百多斤。\n灌阳油茶、现做寿司等特色小吃引来众多品尝者，市集现场人流涌动，欢声不断。\n同期 张先生（市民）：挺热闹的，感觉年味一下就来了。\n本次活动由南宁信息港主办、悦和物业公司协办，将持续到1月31日。\n同期 王红（市集主办方）：我们这个市集会一直持续到1月31号，欢迎大家来。';
const SAMPLE_SCRIPT_ORIGINAL='年味从一口腊肠开始：市集上的三个人\n买了点腊肉，还有礼盒，准备给老人送去\n我们家的腊肠是自己熏的，用的是广西本地的猪肉\n这两天人特别多，昨天一天卖了两百多斤\n今年我们特意把新能源汽车和传统年货放在一起，就是想让大家感受高新和年味的结合\n三十多家企业，基本上都是园区里的\n就是希望大家过年都能吃上一口家乡味\n挺热闹的，感觉年味一下就来了\n我们这个市集会一直持续到1月31号，欢迎大家来';
const sampleScriptFor=(mode,scn)=>mode==='mixed'?SAMPLE_SCRIPT_MIXED:mode==='original'?(SAMPLE_SCRIPT_ORIGINAL+(scn==='quoteMissing'?'\n今年我们还请了舞狮队':'')):SAMPLE_SCRIPT;
// 预转写结果（真实实现：上传后 ASR + 分说话人 + 词级时间戳）
const SPEAKERS={S1:{name:'王红',role:'市集主办方'},S2:{name:'李大姐',role:'腊味摊摊主'},S3:{name:'张先生',role:'市民'}};
const SAMPLE_TRANSCRIPTS={'主办方采访.mp4':[{t0:3,t1:9,spk:'S1',text:'我们这个市集会一直持续到一月三十一号，欢迎大家来'},{t0:12,t1:21,spk:'S1',text:'今年我们特意把新能源汽车和传统年货放在一起，就是想让大家感受高新和年味的结合'},{t0:24,t1:31,spk:'S1',text:'三十多家企业，基本上都是园区里的'}],'摊主采访.mp4':[{t0:2,t1:10,spk:'S2',text:'我们家的腊肠是自己熏的，用的是广西本地的猪肉'},{t0:15,t1:22,spk:'S2',text:'这两天人特别多，昨天一天卖了两百多斤'},{t0:30,t1:38,spk:'S2',text:'嗯…就是希望大家过年都能吃上一口家乡味'}],'市民采访.mp4':[{t0:1,t1:7,spk:'S3',text:'买了点腊肉，还有礼盒，准备给老人送去'},{t0:10,t1:16,spk:'S3',text:'挺热闹的，感觉年味一下就来了'}]};
const SNR={'摊主采访.mp4':14};const SPK_SHOT={S1:21,S2:6,S3:29};
// ---------- §D 原声句（mixed/original）：预转写 transcriptOf · 稿句对齐 matchQuote/sim · 说话人 speakersOf · 成片行 buildRows/markJumps · 按词分块 wordsOf（真实实现见头部 §6）
const transcriptOf=f=>(!f||f.bad||f.stale||f.asr!=='done')?[]:(SAMPLE_TRANSCRIPTS[f.name]||[]);
const fmtT=s=>String(Math.floor((s||0)/60)).padStart(2,'0')+':'+String(Math.floor((s||0)%60)).padStart(2,'0');
// 稿句 ↔ 转写 对齐（前端模拟：中文数字归一 + 字 bigram Dice；真实实现为强制对齐）
const CN_NUM={零:0,一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9};
const cnToNum=s=>s.replace(/[零一二两三四五六七八九十百千]+/g,w=>{let total=0,cur=0;for(const ch of w){if(ch==='十'){total+=(cur||1)*10;cur=0;}else if(ch==='百'){total+=(cur||1)*100;cur=0;}else if(ch==='千'){total+=(cur||1)*1000;cur=0;}else cur=CN_NUM[ch]||0;}return String(total+cur);});
const norm=s=>cnToNum(String(s||'')).replace(/[，。、“”‘’；：！？,.!?;:\s…（）()\-—]/g,'').toLowerCase();
const grams=s=>{const g=[];for(let i=0;i<s.length-1;i++)g.push(s.slice(i,i+2));return g;};
const sim=(a,b)=>{const A=norm(a),B=norm(b);if(!A||!B)return 0;if(A===B)return 1;const ga=grams(A),gb=grams(B);let dice=0;if(ga.length&&gb.length){const m=new Map();ga.forEach(g=>m.set(g,(m.get(g)||0)+1));let hit=0;gb.forEach(g=>{const c=m.get(g)||0;if(c>0){hit++;m.set(g,c-1);}});dice=2*hit/(ga.length+gb.length);}const contain=(A.length>=4&&B.includes(A))?0.85+0.15*A.length/B.length:0;return +Math.max(dice,contain).toFixed(2);};
const matchQuote=(text,files)=>{let best=null;const all=[];files.forEach(f=>transcriptOf(f).forEach(l=>{const sc=sim(text,l.text);const cand={file:f.name,t0:l.t0,t1:l.t1,spk:l.spk,spoken:l.text,score:sc,snr:SNR[f.name]!=null?SNR[f.name]:24};all.push(cand);if(!best||sc>best.score||(sc===best.score&&cand.snr>best.snr))best=cand;}));const alts=best?all.filter(c=>c!==best&&c.spk===best.spk&&c.score>=0.5).sort((a,b)=>b.score-a.score):[];return {best,alts,status:!best||best.score<0.6?'missing':best.score>=0.85?'ok':'unsure'};};
// 句子类型：「同期：」「【同期】」「姓名（身份）：」开头 = 原声句（quote），其余 = 旁白句（narration）；用户 chip 可覆盖
const parseLine=line=>{let s=line.trim(),m;if((m=s.match(/^(?:【同期】|同期)\s*[：:]?\s*(.*)$/))){s=m[1];let name=null,role=null;const n=s.match(/^([^\s（(：:，。]{1,8})(?:[（(]([^）)]{1,12})[）)])?\s*[：:]\s*(.*)$/);if(n){name=n[1];role=n[2]||null;s=n[3];}return {kind:'quote',name,role,text:s};}if((m=s.match(/^([^\s（(：:，。]{1,8})(?:[（(]([^）)]{1,12})[）)])?\s*[：:]\s*(.+)$/))&&!/^(记者|旁白|标题|同期)$/.test(m[1]))return {kind:'quote',name:m[1],role:m[2]||null,text:m[3]};return {kind:'narration',name:null,role:null,text:s};};
const scriptUnits=(script,mode,marks)=>{const out=[];script.split('\n').slice(1).forEach(line=>{if(!line.trim())return;if(mode==='voiceover'){splitUnits(line).forEach(x=>out.push({kind:'narration',auto:'narration',text:x}));return;}const p=mode==='original'?{kind:'quote',text:line.trim(),name:null,role:null}:parseLine(line);if(p.kind==='quote')splitQuote(p.text).forEach(x=>out.push({kind:(mode==='mixed'&&marks[x])||'quote',auto:'quote',text:x,name:p.name,role:p.role}));else splitUnits(p.text).forEach(x=>out.push({kind:marks[x]||'narration',auto:'narration',text:x}));});return out;};
const speakersOf=(files,qm,names)=>{const map={};files.forEach(f=>transcriptOf(f).forEach(l=>{if(!map[l.spk])map[l.spk]={spk:l.spk,file:f.name,count:0,sec:0,name:'',role:''};}));qm.forEach(m=>{if(!m.best||m.status==='missing')return;const s=map[m.best.spk];if(!s)return;s.count++;s.sec+=m.best.t1-m.best.t0;if(m.u.name&&!s.name){s.name=m.u.name;s.role=m.u.role||'';}});Object.entries(names||{}).forEach(([k,v])=>{if(map[k]){if(v.name!=null)map[k].name=v.name;if(v.role!=null)map[k].role=v.role;}});return Object.values(map).sort((a,b)=>a.spk<b.spk?-1:1);};
const brollOf=files=>files.filter(f=>!f.bad&&!f.stale&&!f.speech&&!f.img&&f.shot!=null).map(f=>f.shot);
// 跳切：相邻两句原声来自不同文件，或同文件不连续（间隔 > 0.5s）；旁白夹在中间则不计
const markJumps=(rows,cover,broll)=>{let bi=0;rows.forEach((r,i)=>{r.jump=null;r.cover=null;r.coverShot=null;if(r.kind!=='quote'||r.missing)return;const p=rows[i-1];if(!p||p.kind!=='quote'||p.missing)return;const j=p.file!==r.file?'file':(Math.abs(r.t0-p.t1)>0.5?'gap':null);if(!j)return;r.jump=j;const cv=(cover||'broll')==='broll'&&!broll.length?'zoom':(cover||'broll');r.cover=cv;if(cv==='broll'){r.coverShot=broll[bi%broll.length];bi++;}});return rows;};
// 由稿句生成成片行：原声句 → 对齐到转写时间段；旁白句 → 复用范例 ROWS 的镜头（前端模拟语义匹配）
const buildRows=(units,files,prefs,mode)=>{const target=PACING_CPM[prefs.pacing||'normal'];const used=new Set();const pool=Object.keys(SHOT).map(Number).filter(p=>!Object.values(SPK_SHOT).includes(p));let nid=0;const rows=[];
 units.forEach(u=>{if(u.kind==='quote'){const m=matchQuote(u.text,files),b=m.best;if(b&&m.status!=='missing'){rows.push({id:nid++,kind:'quote',s:u.text,spoken:b.spoken,d:+(b.t1-b.t0).toFixed(2),c:b.score,spk:b.spk,file:b.file,t0:b.t0,t1:b.t1,snr:b.snr,sync:true,beats:[{shot:SPK_SHOT[b.spk]!=null?SPK_SHOT[b.spk]:9}],alts:m.alts});}else rows.push({id:nid++,kind:'quote',s:u.text,spoken:'',d:+Math.max(1,chars(u.text)/4).toFixed(2),c:b?b.score:0,spk:b?b.spk:null,file:b?b.file:null,t0:0,t1:0,snr:24,sync:true,missing:true,beats:[{shot:b&&SPK_SHOT[b.spk]!=null?SPK_SHOT[b.spk]:2}],alts:[]});return;}
  let best=null,bs=0;ROWS.forEach(r=>{const sc=sim(u.text,r.s);if(sc>bs){bs=sc;best=r;}});
  if(best&&bs>=0.6&&!best.beats.some(b=>used.has(b.shot))){const {sync,spoken,gen,fallback,...rest}=best;best.beats.forEach(b=>used.add(b.shot));const jit=1+((nid*7)%5-2)*0.02;rows.push({...rest,id:nid++,kind:'narration',s:u.text,gen:false,fallback:false,beats:best.beats.map(b=>({...b})),miss:(best.miss||[]).filter(mm=>u.text.includes(mm)),d:+Math.max(1.2,chars(u.text)/target*60*jit).toFixed(2)});}
  else{const shot=pool.find(p=>!used.has(p));if(shot!=null)used.add(shot);rows.push({id:nid++,kind:'narration',s:u.text,d:+Math.max(1.2,chars(u.text)/target*60).toFixed(2),c:0.7,beats:[{shot:shot!=null?shot:1}]});}});
 return markJumps(rows,prefs.jump_cut_cover,brollOf(files));};
// 按词分块（真实实现用 ASR 词级时间戳；原型用 Intl.Segmenter 分词，不支持时退化为两字一块）
const SEG=(typeof Intl!=='undefined'&&Intl.Segmenter)?new Intl.Segmenter('zh',{granularity:'word'}):null;
const wordsOf=s=>{const out=[];const push=w=>{if(!w)return;if(/^[，。、！？…,!?\s]+$/.test(w)){if(out.length)out[out.length-1]+=w.trim();return;}out.push(w);};if(SEG){for(const seg of SEG.segment(String(s||'')))push(seg.segment);}else{String(s||'').split(/([，。、！？…,!?])/).filter(Boolean).forEach(p=>{if(/^[，。、！？…,!?]$/.test(p)){push(p);return;}for(let i=0;i<p.length;i+=2)out.push(p.slice(i,i+2));});}return out;};
const TOGGLE_NOTE={mixed:{background_music:'原声段落音乐自动压低',motion_effects:'只作用于旁白句画面',enhance_speech:'只作用于原声句和自录配音',generative_fill:'只对旁白句'},original:{background_music:'原声段落音乐自动压低',enhance_speech:'只作用于原声句'}};
const JUMP_OPTS=[['broll','自动插空镜'],['zoom','轻微推近'],['hard','直接硬切']];const QCAP_OPTS=[['spoken','按实际说的话'],['none','不显示字幕，只显示人名条']];
const TOGGLES=[
 {k:'background_music',label:'背景音乐',devName:'background_music · music_mood'},
 {k:'motion_effects',label:'画面慢慢推近',devName:'motion_effects · Ken Burns ≈ 8%'},
 {k:'news_graphics',label:'新闻台标和片尾板',devName:'news_graphics · 左上主题条 + 片尾 2.5s'},
 {k:'transitions',label:'开头结尾淡入淡出',devName:'transitions · 仅首尾 0.5s / 0.6s'},
 {k:'color_consistency',label:'颜色统一',devName:'color_consistency · 轻量'},
 {k:'enhance_speech',label:'现场原声降噪（去掉风声、嗡嗡声）',devName:'enhance_speech · 仅作用于同期声与自录配音 · 需新增后端'},
 {k:'generative_fill',label:'找不到画面时让 AI 画示意图',devName:'generative_fill · 仅非事实性兜底 · ≤ 2 段 · 烧录“AI生成示意画面”',danger:true,note:'新闻里不能用假画面冒充现场；只在拍不到时当示意用，成片会标注“AI 生成”，可能收费。'}];
const MOODS=[['auto','自动'],['solemn','庄重'],['neutral','中性'],['uplifting','明快'],['tense','紧张']];
const CAPTIONS=[['news','新闻标准'],['big','大字清晰'],['none','不要字幕']];
const PACING=[['slow','慢一点'],['normal','正常'],['fast','快一点']];
const TONE=[['solemn','庄重'],['neutral','中性'],['energetic','明快']];
const VOICES=[['ai','AI 播音员'],['mine','我自己读']];
// ---------- §C 稿件分析：五要素识别 RX、字数、语速(字/分)、事实项提取、句子拆分 scriptUnits
const RX={time:/(\d+月\d+日|\d{4}年|今天|昨天|上午|下午|近日|日前|将至)/,place:/(\d+号|区|广场|信息港|街道|商场|医院|车站|公园|市场|社区|夜市)/,who:/(市民|居民|游客|商户|工作人员|负责人|企业|公司|记者|志愿者|商家|受访者|摊主)/,what:/(举办|开展|举行|开幕|开张|进行|参加|启动|发布|比赛)/,why:/(为了|旨在|目的|以便|为.{1,14}打造|因为|由于|以.{1,6}为纽带|让.{1,10}(感受|了解|体验))/};
const ELEMENTS=[['time','何时'],['place','何地'],['who','何人'],['what','何事'],['why','为何']];
const factsOf=s=>{const f=[];if(/\d+月\d+日|\d{4}年/.test(s))f.push('时间');if(/(\d+号|区|广场|信息港)/.test(s))f.push('地点');if(/(主办|协办|承办)/.test(s))f.push('主办方');return f;};
// 按标点分句，但引号“ ”/《 》/（ ）内的标点不切（与后端分句器一致）
const splitBy=(text,rx)=>{const out=[];let buf='',depth=0;for(const ch of text){if('“《（'.includes(ch))depth++;else if('”》）'.includes(ch))depth=Math.max(0,depth-1);if(depth===0&&rx.test(ch)){if(buf.trim())out.push(buf.trim());buf='';continue;}buf+=ch;}if(buf.trim())out.push(buf.trim());return out;};
const splitUnits=text=>splitBy(text,/[，。；！？,;!?\n]/);const splitQuote=text=>splitBy(text,/[。！？!?\n]/);
const chars=s=>s.replace(/[，。、“”；：！？,.!?\s]/g,'').length;
const cpmOf=r=>Math.round(chars(r.s)/r.d*60);
const fmtTime=s=>Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0');
const fmtSec=s=>s>=60?Math.floor(s/60)+' 分 '+(s%60)+' 秒':s+' 秒';const effSec=f=>f.img?3:(f.inSec!=null&&f.outSec!=null?Math.max(0,f.outSec-f.inSec):(f.sec||0));
const fmtEl=s=>s==null?'':(s>=60?Math.floor(s/60)+'m '+Math.round(s%60)+'s':s.toFixed(1)+'s');
const dateOf=t=>{if(!t.createdAt)return t.date||'';const d=new Date(t.createdAt),n=new Date();if(d.toDateString()===n.toDateString())return '今天';const y=new Date(n);y.setDate(n.getDate()-1);if(d.toDateString()===y.toDateString())return '昨天';return (d.getMonth()+1)+'月'+d.getDate()+'日';};
const clock=ms=>{const d=new Date(ms);return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');};const fmtMin=ms=>ms<60e3?Math.max(1,Math.round(ms/1000))+' 秒':Math.max(1,Math.round(ms/60e3))+' 分钟';
const cloneRows=rows=>(rows||ROWS).map(r=>({...r,beats:r.beats.map(b=>({...b}))}));
const isSample=t=>!!t&&t.id==='t-sample';
const expiryOf=t=>isSample(t)?null:(t.startedAt||t.createdAt||Date.now())+LIMITS.ttlH*H;
const notSubmitted=t=>t.status==='uploading'||(t.status==='queued'&&!(t.upDone>0));
const leftOf=t=>{if(notSubmitted(t)||t.status==='failed'||isSample(t))return {text:'',fg:MUTED,urgent:false};const e=expiryOf(t);if(e==null)return {text:'',fg:MUTED,urgent:false};const ms=e-Date.now();if(ms<=0)return {text:'已清理',fg:RED,urgent:true};const h=Math.ceil(ms/H);if(h<24)return {text:'不到 '+h+' 小时就会清理',fg:RED,urgent:true};return {text:'还剩 '+Math.floor(h/24)+' 天',fg:MUTED,urgent:false};};
function makeTask(id,title,o={}){return {id,title,reflection:'',upTotal:0,upDone:0,createdAt:Date.now(),status:'queued',queue:0,revision:0,progress:0,current:1,error:null,errorKind:null,fileCount:5,mode:'voiceover',speakers:{},replaceFails:{},rows:cloneRows(),versions:[],stages:STAGES.map(s=>({n:s.n,status:'pending',msg:'',elapsed:null})),date:'今天',prefs:{news_graphics:true,pacing:'normal',voice:'ai'},script:'',files:[],plan:null,...o};}
function qcFail(t,bad){t.status='failed';t.current=10;t.progress=95;t.stages.forEach(s=>{if(s.n<10){s.status='done';if(s.elapsed==null)s.elapsed=SIM[s.n-1]*11;if(!s.msg)s.msg=stageMsg(s.n,t);}else{s.status='failed';s.msg='';}});t.errorKind='qc';t.badRows=[...new Set(bad.map(b=>b.i))];t.error=bad[0].text+(bad.length>1?'（还有 '+(bad.length-1)+' 处）':'')+'。检查没通过，这次没有出片。';t.errorPro='stage 10 质检 · '+[...new Set(bad.map(b=>b.code.split(' ·')[0]))].join(' + ')+' · quality_gate_mode=block → QualityGateError，无成片；阶段 1–9 的云端费用已产生';return t;}
function finish(t,op){t.status='done';t.progress=100;t.current=10;t.doneAt=Date.now();t.stages.forEach(s=>{s.status='done';if(s.elapsed==null)s.elapsed=SIM[s.n-1]*11;if(!s.msg)s.msg=stageMsg(s.n,t);});t.versions.push({rev:t.revision,op:op||(t.revision?'修改':'初版'),rows:cloneRows(t.rows)});return t;}

// ---------- §E 组件：state → 动作方法 → checksFor/gateOf → applyPending → renderVals（模板唯一的数据来源）
class Component extends DCLogic {
  constructor(props){
    super(props);
    const now=Date.now();
    const done=makeTask('t-done','老街夜市开市首日',{date:'9月3日',createdAt:now-58*H});done.rows=done.rows.map(r=>r.id===1?{...r,beats:[{shot:7}],c:0.41}:r);{const bad=this.blockingRows(done);if(props.gateMode==='block'&&bad.length)qcFail(done,bad);else{done.revision=2;finish(done);done.versions=[{rev:0,op:'初版',rows:cloneRows(done.rows)},{rev:1,op:'换第 13 句画面',rows:cloneRows(done.rows)},{rev:2,op:'重做：删 1 句',rows:cloneRows(done.rows)}];}}
    const run=makeTask('t-run','城东公园改造完成',{date:'9月2日',createdAt:now-0.2*H,status:'running',progress:55,current:6,upTotal:980,upDone:980,upStart:now-0.2*H,upEnd:now-0.19*H});run.stages.forEach(s=>{if(s.n<6){s.status='done';s.elapsed=SIM[s.n-1]*11;s.msg=stageMsg(s.n,run);}else if(s.n===6)s.status='running';});
    const mine=makeTask('t-mine','早餐摊：一条街的清晨',{date:'9月1日',createdAt:now-30*H,revision:1,reflection:''});
    mine.rows=cloneRows().filter(r=>!r.gen).map(r=>r.fallback?{...r,fallback:false,c:0.72,replaced:'摊主在红白条纹棚下给顾客装菜',beats:[{shot:6}]}:r).map(r=>r.miss?{...r,miss:[],s:r.s.replace('糕点、',''),edited:true}:r);
    finish(mine);mine.versions=[{rev:0,op:'初版',rows:cloneRows(ROWS)},{rev:1,op:'换第 13 句画面',rows:cloneRows(mine.rows)}];
    const gone=makeTask('t-gone','社区垃圾分类一年后',{date:'8月4日',createdAt:now-120*H,status:'done',gone:true});
    const sample=makeTask('t-sample','范例 · 南宁信息港迎春市集',{date:'去年',createdAt:now-4000*H,revision:2});
    sample.rows=cloneRows().filter(r=>!r.gen).map(r=>r.fallback?{...r,fallback:false,c:0.74,replaced:'摊位上摆满辣椒酱和瓶装饮料',beats:[{shot:4}]}:r).map(r=>r.miss?{...r,miss:[],s:r.s.replace('糕点、',''),edited:true}:r);sample.reflection='“融合高新科技与传统节庆”是总结句，拍不到，所以换成了摊位实拍；“糕点”没拍到，就从稿子里去掉了。';finish(sample);sample.versions=[{rev:0,op:'初版',rows:cloneRows(ROWS)},{rev:1,op:'换第 13 句画面',rows:cloneRows(sample.rows)},{rev:2,op:'重做：改 1 句字',rows:cloneRows(sample.rows)}];
    const mode0=MODES.some(m=>m[0]===props.mode)?props.mode:'voiceover';this._propMode=props.mode;
    this.state={screen:'create',step:1,script:'',files:[],mode:mode0,typeMarks:{},speakerNames:{},pickOpen:true,fileOpen:{},fileAdv:{},candMore:false,modeMore:false,introDismissed:false,toastAct:null,suggestOff:{},trim:null,pendTrim:{},pendTake:{},pendToNarration:[],pendSpeakers:{},bigFont:false,
      prefs:defaultPrefs(mode0),
      tasks:{'t-run':run,'t-done':done,'t-mine':mine,'t-gone':gone,'t-sample':sample},history:['t-run','t-done','t-mine','t-gone','t-sample'],currentId:null,
      tips:{},elemMarks:{},sentMarks:{},pend:{},shotListOpen:false,sentListOpen:false,exportOpen:false,exp:{fmt:'mp4',aspect:'16:9',res:'1080p',sub:'std'},histSort:'time',
      selectedId:0,deleted:[],pendReplace:{},pendEdit:{},pendVoice:{},pendPacing:null,viewRev:null,checked:{},editing:false,editText:'',instruction:'',recording:null,
      time:0,playing:false,drawerOpen:false,confirm:null,toast:null,quotaUsed:0,quotaResetAt:now+H};
    this.queue=[];this.stageStart=0;this.scriptRef=React.createRef();this.reflectRef=React.createRef();
    this.onKey=e=>{if(e.key==='Escape'){if(this.state.exportOpen){this.setState({exportOpen:false});return;}this.setState({confirm:null,drawerOpen:false});return;}
      if(this.state.screen==='result'&&!/INPUT|TEXTAREA/.test((document.activeElement&&document.activeElement.tagName)||'')){const t0=this.cur();if(t0&&t0.status==='done'){if(e.key===' '){e.preventDefault();this.togglePlay();return;}if((e.key==='Delete'||e.key==='Backspace')&&!isSample(t0)){const id=this.state.selectedId;this.setState(p=>({deleted:p.deleted.includes(id)?p.deleted.filter(x=>x!==id):[...p.deleted,id]}));return;}}}
      if(this.state.screen==='result'&&(e.key==='ArrowLeft'||e.key==='ArrowRight')&&!/INPUT|TEXTAREA/.test((document.activeElement&&document.activeElement.tagName)||'')){this.stepRow(e.key==='ArrowRight'?1:-1);}};
    this.onUnload=e=>{if(this.state.screen==='result'&&this.pendingCount()){e.preventDefault();e.returnValue='';}};
  }
  applyFont(){const f=(this.state.bigFont?20:16)+'px';if(document.documentElement.style.fontSize!==f)document.documentElement.style.fontSize=f;}
  /** 本地持久化（debounce）；restore() 在挂载时读回并清理超过 72h 的作品 */
  persist(){clearTimeout(this.saveTimer);this.saveTimer=setTimeout(()=>{try{if(typeof localStorage==='undefined')return;const S=this.state;const tasks={};Object.values(S.tasks).forEach(t=>{tasks[t.id]={...t,files:(t.files||[]).map(f=>({...f,thumb:f.thumb&&f.thumb.startsWith('data:')?null:f.thumb}))};});const pend={...(S.pend||{})};if(S.screen==='result'&&S.currentId){if(this.pendingCount())pend[S.currentId]={deleted:S.deleted,pendReplace:S.pendReplace,pendEdit:S.pendEdit,pendVoice:S.pendVoice,pendPacing:S.pendPacing,pendTrim:S.pendTrim,pendTake:S.pendTake,pendToNarration:S.pendToNarration,pendSpeakers:S.pendSpeakers};else delete pend[S.currentId];}localStorage.setItem(SAVE_KEY,JSON.stringify({tasks,history:S.history,checked:S.checked,pend,draft:this.draftOf(),quotaUsed:S.quotaUsed,quotaResetAt:S.quotaResetAt,bigFont:S.bigFont,introDismissed:S.introDismissed}));this.setState({savedAt:Date.now()});}catch(e){}},400);}
  restore(){try{if(typeof localStorage==='undefined')return null;const raw=localStorage.getItem(SAVE_KEY);if(!raw)return null;const d=JSON.parse(raw);if(!d||!d.tasks||!Array.isArray(d.history))return null;Object.values(d.tasks).forEach(t=>{if(t.status==='held'){t.status='queued';t.released=true;t.upDone=t.upTotal||0;t.upEnd=t.upEnd||t.upStart||Date.now();}if(t.status==='uploading'){t.status='failed';t.errorKind='network';t.error='页面刷新时上传中断，任务没有建成。';t.errorPro='multipart POST /api/tasks 中断 · 配额在请求开始时已计数 · 需重新提交';t.upDone=0;}else if(t.status==='waiting')finish(t,t.op);else if(t.status==='queued')t.released=true;if(t.upTotal&&t.upDone>=t.upTotal&&!t.upEnd)t.upEnd=t.upStart||Date.now();const ex=expiryOf(t);if(ex!=null&&ex<Date.now()&&t.status==='done')t.gone=true;});delete d.tasks['t-sample'];return d;}catch(e){return null;}}
  scrollToBoard(){setTimeout(()=>{const s=document.querySelector('[data-r="strip"]');if(!s)return;const top=s.getBoundingClientRect().top+window.scrollY-120;if(Math.abs(window.scrollY-top)>40)window.scrollTo({top:Math.max(0,top),behavior:'smooth'});},60);}
  /** 上一句 / 下一句（键盘 ← → 与句面板头部按钮共用） */
  stepRow(dir){const t=this.cur();if(!t)return;const v=this.state.viewRev!=null?t.versions.find(x=>x.rev===this.state.viewRev):null;const rows=v?v.rows:t.rows;if(!rows.length)return;const i=rows.findIndex(r=>r.id===this.state.selectedId);const j=Math.max(0,Math.min(rows.length-1,i+dir));let acc=0;for(let k=0;k<j;k++)acc+=rows[k].d;this.setState({selectedId:rows[j].id,time:acc,editing:false,trim:null});}
  /** 单栏布局（≤1000px：平板竖屏/手机）下选中一句后，把故事板滚到顶部，句面板紧随其下可见 */
  scrollPanelNarrow(){setTimeout(()=>{const app=document.querySelector('[data-r="app"]');if(!app||app.clientWidth>1000)return;const s=document.querySelector('[data-r="strip"]');if(!s)return;const top=s.getBoundingClientRect().top+window.scrollY-72;if(Math.abs(window.scrollY-top)>24)window.scrollTo({top:Math.max(0,top),behavior:'smooth'});},80);}
  scrollStrip(){setTimeout(()=>{const s=document.querySelector('[data-r="strip"]');if(!s)return;const el=s.querySelector('[data-cell="'+this.state.selectedId+'"]');if(!el)return;const sr=s.getBoundingClientRect(),er=el.getBoundingClientRect();const left=Math.max(0,s.scrollLeft+(er.left-sr.left)-s.clientWidth/2+er.width/2);if(Math.abs(s.scrollLeft-left)>8)s.scrollLeft=left;},60);}
  componentDidUpdate(){const S=this.state,prev=this._prev||{};this._prev=S;this.applyFont();if(this.props.mode!==this._propMode){this._propMode=this.props.mode;if(MODES.some(m=>m[0]===this.props.mode))this.setMode(this.props.mode);}if(prev.selectedId!==S.selectedId||prev.screen!==S.screen)this.scrollStrip();if(prev.selectedId!==S.selectedId&&prev.screen==='result'&&S.screen==='result'&&prev.viewRev===S.viewRev)this.scrollPanelNarrow();if(['tasks','history','checked','pend','quotaUsed','bigFont','mode','typeMarks','speakerNames','deleted','pendReplace','pendEdit','pendVoice','pendPacing','pendTrim','pendTake','pendToNarration','pendSpeakers','script','step','prefs','files','elemMarks','sentMarks'].some(k=>prev[k]!==S[k]))this.persist();}
  /** 切换制作模式：不清空已填内容；只重设随模式变化的默认值（C 关音乐与推近、关 AI 示意图） */
  setMode(m){if(!MODES.some(x=>x[0]===m))return;this.setState(p=>({mode:m,prefs:{...p.prefs,background_music:m!=='original',motion_effects:m!=='original',generative_fill:m==='original'?false:p.prefs.generative_fill,jump_cut_cover:p.prefs.jump_cut_cover||'broll',quote_caption:p.prefs.quote_caption||'spoken',lower_third:p.prefs.lower_third!==false}}));}
  /** C 模式「从转写挑句子」：勾选追加到稿子末尾；取消勾选则删掉对应行 */
  appendToScript(text){const clean=text.replace(/^[嗯啊呃哦呃嘛]+[…,，、]*/,'').replace(/…/g,'');this.setState(p=>({script:(p.script.trim()?p.script.replace(/\s+$/,''):'（写一个标题）')+'\n'+clean}));}
  removeFromScript(text){const n=norm(text);this.setState(p=>{const ls=p.script.split('\n');const i=ls.findIndex((l,k)=>{if(k===0)return false;const ln=norm(parseLine(l).text||l);return ln.length>=4&&(ln.includes(n)||n.includes(ln));});if(i<0)return null;const l=ls[i];const pl=parseLine(l);const rest=pl.text.replace(new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')),'').replace(/^[。！？；，,;!?\s]+|[。！？；，,;!?\s]+$/g,'');if(!rest.trim()||norm(rest).length<2)ls.splice(i,1);else ls[i]=l.slice(0,l.length-pl.text.length)+rest;return {script:ls.join('\n')};});}
  componentDidMount(){
    this.applyFont();this._prev=this.state;window.addEventListener('keydown',this.onKey);window.addEventListener('beforeunload',this.onUnload);
    const s=this.props.startScreen||'create';
    if(s==='create'){const d=this.restore();if(d){const dr=d.draft&&typeof d.draft==='object'?d.draft:null;this.setState(p=>({tasks:{...d.tasks,'t-sample':p.tasks['t-sample']},history:d.history.filter(id=>id!=='t-sample'&&d.tasks[id]&&!d.tasks[id].gone),checked:d.checked||{},pend:d.pend||{},quotaUsed:d.quotaUsed||0,quotaResetAt:d.quotaResetAt||Date.now()+H,bigFont:d.bigFont!=null?!!d.bigFont:p.bigFont,introDismissed:!!d.introDismissed,...(dr?{script:dr.script||'',step:dr.step||1,prefs:{...p.prefs,...(dr.prefs||{})},files:(dr.files||[]).map(f=>({...f,stale:f.shot==null&&!f.bad,asr:f.speech&&f.shot!=null?'done':null})),elemMarks:dr.elemMarks||{},sentMarks:dr.sentMarks||{},mode:MODES.some(m=>m[0]===dr.mode)?dr.mode:p.mode,typeMarks:dr.typeMarks||{},speakerNames:dr.speakerNames||{}}:{})}),()=>{this.resumeAll();if(dr)this.toast('上次的草稿还在，接着写'+((dr.files||[]).some(f=>f.shot==null&&!f.bad)?'；素材要重新选一次':''));});}else this.resumeTask('t-run');}
    else if(s==='result'){const id='t-demo',mode=this.state.mode,scn=this.props.demoScenario||'normal';const script=sampleScriptFor(mode,scn),files=sampleFilesFor(mode,scn).filter(f=>!f.bad).map(f=>({...f,asr:f.speech?'done':null}));const t=makeTask(id,script.split('\n')[0],{mode,script,files,prefs:defaultPrefs(mode),upTotal:files.reduce((a,f)=>a+f.mb,0),upDone:files.reduce((a,f)=>a+f.mb,0),upStart:Date.now()-150000,upEnd:Date.now()-126000});if(mode!=='voiceover'){const units=scriptUnits(script,mode,{});t.rows=buildRows(units,files,t.prefs,mode);t.speakers={...SPEAKERS};t.speakerNames={...SPEAKERS};t.rows.forEach(r=>{if(r.missing)r.missing=false;});}const ok=this.complete(t);this.setState(p=>({tasks:{...p.tasks,[id]:t},history:[id,...p.history],currentId:id,screen:ok?'result':'processing',selectedId:t.rows[0].id}));}
    else if(s==='processing'){const mode=this.state.mode,scn=this.props.demoScenario||'normal';this.setState({script:sampleScriptFor(mode,scn),files:sampleFilesFor(mode,scn).filter(f=>!f.bad).map(f=>({...f,asr:f.speech?'done':null})),speakerNames:mode==='voiceover'?{}:{...SPEAKERS}},()=>this.start());}
  }
  componentWillUnmount(){window.removeEventListener('keydown',this.onKey);window.removeEventListener('beforeunload',this.onUnload);clearInterval(this.timer);clearInterval(this.playTimer);clearInterval(this.recTimer);(this.upTimers||[]).forEach(clearInterval);clearTimeout(this.toastTimer);clearTimeout(this.saveTimer);}
  /** 成片完成：block 模式先跑 blockingRows()，有阻断项则 qcFail()；否则 finish() 记录版本 */
  complete(t,op){if(this.props.gateMode==='block'){const bad=this.blockingRows(t);if(bad.length){qcFail(t,bad);return false;}}finish(t,op);return true;}
  blockingRows(t){return this.checksFor(t,t.rows).checks.filter(ck=>ck.sev===0&&ck.auto).map(ck=>({i:t.rows.findIndex(r=>r.id===ck.id)+1,text:ck.text,code:ck.code}));}
  /** 生成发布前检查表（见文件头 §4）；返回 {checks:[{k,sev,auto,done,label,rowId}],win:[minCpm,maxCpm]}；语速窗口 = 所选播音速度 ±8% */
  checksFor(t,rows){
    const target=PACING_CPM[(t&&t.prefs&&t.prefs.pacing)||'normal'],win=[Math.round(target*(1-RATE_TOL)),Math.round(target*(1+RATE_TOL))];
    const checks=[];if(!t)return {checks,win};
    const sig=r=>r.beats.map(b=>b.shot).join('/')+'|'+r.s,key=(k,id,sg)=>t.id+':'+k+':'+id+':'+sg;
    const mode=t.mode||'voiceover';
    // 原声句（B/C）：对齐、时长、杂音、字幕出入、说话人、跳切
    rows.forEach((r,i)=>{if(r.kind!=='quote')return;const n=i+1;
      if(r.missing||r.c<0.6)checks.push({k:key('qnf',r.id,sig(r)),sev:0,auto:true,done:false,text:'第 '+n+' 句是原话，但素材里没找到——'+(mode==='mixed'?'改成旁白、':'')+'删掉，或回去传含这句话的素材',code:'QUOTE_NOT_FOUND · score '+(r.c||0).toFixed(2)+' < 0.6 · error · 阻断发布',id:r.id,goLabel:'去看'});
      else if(r.c<0.85)checks.push({k:key('qlow',r.id,sig(r)+r.c),sev:1,auto:false,text:'第 '+n+' 句对上的原话不太像，听一下是不是这句',code:'QUOTE_MATCH_LOW '+r.c.toFixed(2)+' · 0.6–0.85 · warning',id:r.id,goLabel:'去听'});
      if(!r.missing&&r.d>30)checks.push({k:key('qlen',r.id,sig(r)+r.d),sev:0,auto:true,done:false,text:'第 '+n+' 句原声 '+r.d.toFixed(0)+' 秒，太长了——剪短或断成两句',code:'QUOTE_TOO_LONG > 30s · error · 阻断发布',id:r.id,goLabel:'去剪短'});
      else if(!r.missing&&r.d>20)checks.push({k:key('qlen',r.id,sig(r)+r.d),sev:1,auto:false,text:'第 '+n+' 句原声 '+r.d.toFixed(0)+' 秒，太长了——剪短或断成两句',code:'QUOTE_TOO_LONG > 20s · warning',id:r.id,goLabel:'去剪短'});
      if(!r.missing&&r.snr!=null&&r.snr<18)checks.push({k:key('qsnr',r.id,sig(r)+r.file+r.t0),sev:1,auto:false,text:'第 '+n+' 句原声有杂音，建议换一段',code:'QUOTE_AUDIO_NOISY · SNR '+r.snr+' dB < 18',id:r.id,goLabel:'去换'});
      if(!r.missing&&r.spoken&&1-sim(r.s,r.spoken)>0.1)checks.push({k:key('qdiff',r.id,sig(r)+r.spoken),sev:1,auto:false,text:'第 '+n+' 句字幕按实际说的话显示，和稿子有出入——确认没问题',code:'QUOTE_TEXT_DIFFERS · '+Math.round((1-sim(r.s,r.spoken))*100)+'% > 10%',id:r.id,goLabel:'去看'});});
    if(mode!=='voiceover'){const spks=[...new Set(rows.filter(r=>r.kind==='quote'&&!r.missing&&r.spk).map(r=>r.spk))];spks.forEach((k,si)=>{const s=(t.speakers||{})[k]||{},pend=(this.state.pendSpeakers||{})[k]||{};if(!(pend.name||s.name)){const row=rows.find(r=>r.spk===k);checks.push({k:key('spk',k,''),sev:1,auto:false,text:'说话人 '+(si+1)+' 还没填名字，人名条会显示“受访者”',code:'SPEAKER_UNNAMED · '+k,id:row?row.id:null,goLabel:'去填',speaker:k});}});
      const unc=rows.filter(r=>r.jump&&r.cover!=='broll').length;if(unc)checks.push({k:key('jump','all',rows.filter(r=>r.jump).map(r=>r.id+r.cover).join(',')),sev:1,auto:false,text:'有 '+unc+' 处跳切没用空镜遮盖，画面会跳一下——可以回去多传空镜',code:'JUMP_CUT_UNCOVERED × '+unc+' · cover='+((t.prefs&&t.prefs.jump_cut_cover)||'broll'),id:rows.find(r=>r.jump&&r.cover!=='broll').id,goLabel:'去看'});
      if(mode==='mixed'&&rows.length&&rows.every(r=>r.kind==='quote'))checks.push({k:key('allq','all',''),sev:2,auto:false,text:'这条全是原声，其实可以用「只用原声」模式',code:'MIXED_NO_NARRATION · info',id:null});}
    rows.forEach((r,i)=>{if(r.fallback)checks.push({k:key('fb',r.id,sig(r)),sev:0,auto:true,done:false,text:'第 '+(i+1)+' 句没找到合适画面，用了凑数画面——换个画面或删掉',code:'MATCH_FALLBACK · error · 阻断发布',id:r.id,goLabel:'去看'});});
    rows.forEach((r,i)=>{(r.miss||[]).forEach(m=>{if(r.s.includes(m))checks.push({k:key('ent',r.id,sig(r)+m),sev:0,auto:true,done:false,edit:true,text:'第 '+(i+1)+' 句提到“'+m+'”，素材里没拍到它——去掉这个词，或换成拍到它的画面',code:'EXPLICIT_ENTITY_NOT_COVERED · error · 阻断发布',id:r.id,goLabel:'去改字'});});});
    rows.forEach((r,i)=>{if(r.gen)checks.push({k:key('gen',r.id,sig(r)),sev:0,auto:false,text:'第 '+(i+1)+' 句是 AI 画的示意画面，我知道它不是现场',code:'generated_media · 逐句 media_origin 需新增后端',id:r.id,goLabel:'去看'});});
    rows.forEach((r,i)=>{if(r.c<0.5&&!r.fallback&&r.kind!=='quote')checks.push({k:key('low',r.id,sig(r)+r.c),sev:1,auto:false,text:'第 '+(i+1)+' 句的画面我看过了，是对的',code:'LOW_MATCH_CONFIDENCE '+r.c.toFixed(2)+' · warning',id:r.id,goLabel:'去看'});});
    rows.forEach((r,i)=>{if(r.beats.length===1&&!r.sync&&!r.gen&&!r.fallback){const ss=shotSec(r.beats[0].shot),pad=r.d-ss;if(pad>0.3)checks.push({k:key('frz',r.id,sig(r)+r.d),sev:0,auto:true,done:false,edit:true,text:'第 '+(i+1)+' 句的画面只有 '+ss.toFixed(1)+' 秒，句子 '+r.d.toFixed(1)+' 秒——画面会定格 '+pad.toFixed(1)+' 秒。换个更长的画面，或拆成两句',code:'FREEZE_PAD_EXCESSIVE > 0.3s · error · 阻断发布',id:r.id,goLabel:'去看'});}});
    rows.forEach((r,i)=>{if(r.d>LIMITS.clipMax&&r.beats.length<2&&!r.sync&&!(r.beats[0]&&shotSec(r.beats[0].shot)<r.d-0.3))checks.push({k:key('long',r.id,sig(r)+r.d),sev:1,auto:false,edit:true,text:'第 '+(i+1)+' 句太长了（'+r.d.toFixed(1)+' 秒），一个画面撑不住——拆成两句',code:'VISUAL_CLIP_TOO_LONG > 6.5s · warning；镜头比句子短时 FREEZE_PAD_EXCESSIVE · error',id:r.id,goLabel:'去改字'});});
    const speedRows=rows.filter(r=>!r.sync).map(r=>({r,c:r.mine?r.myCpm:cpmOf(r)})).filter(x=>x.c<win[0]||x.c>win[1]);
    if(mode!=='original'&&speedRows.length){const speedSig=rows.filter(r=>!r.sync).map(r=>r.mine?'m'+r.myCpm:cpmOf(r)).join(','),slow=speedRows.filter(x=>x.c<win[0]),fast=speedRows.filter(x=>x.c>win[1]),mineAny=speedRows.some(x=>x.r.mine),longFast=fast.filter(x=>!x.r.mine&&chars(x.r.s)>=22),tgt=win.join('–')+' 字/分';
      if(fast.length>=slow.length&&longFast.length&&longFast.length===fast.length){const L=longFast[0],li=rows.indexOf(L.r);checks.push({k:key('speed','all',speedSig),sev:1,auto:false,edit:true,text:'有 '+fast.length+' 句读得偏快，是句子太长（第 '+(li+1)+' 句 '+chars(L.r.s)+' 字）——拆成两句最有效',code:'NARRATION_SPEAKING_RATE · 提议：目标 '+tgt+'、warning · 现为 pacing ±8% error 需后端调整',id:L.r.id,goLabel:'去改字'});}
      else checks.push({k:key('speed','all',speedSig),sev:1,auto:false,text:'有 '+speedRows.length+' 句读得'+(slow.length>=fast.length?'偏慢':'偏快')+'（目标 '+tgt+'）——'+(mineAny?'拆句或重录':'拆句或换个速度重新配音'),code:'NARRATION_SPEAKING_RATE · 提议：目标 '+tgt+'、warning · 现为 pacing ±8% error 需后端调整',id:speedRows[0].r.id,goLabel:mineAny?'去看':(slow.length>=fast.length?'快一点':'慢一点'),pacing:mineAny?null:(slow.length>=fast.length?'fast':'slow')});}
    const ovRows=rows.filter(r=>r.overlay);if(ovRows.length)checks.push({k:key('ov','all',ovRows.map(r=>r.overlay+':'+r.s).join('|')),sev:2,auto:false,text:ovRows.length+' 句的日期或机构名会做成字幕条，我确认拼写没错',code:'CONTEXTUAL_BROLL_OVERLAY × '+ovRows.length,id:ovRows[0].id,goLabel:'去看'});
    const factRows=rows.filter(r=>/\d/.test(r.s)||/(主办|协办|承办|先生|女士|主任|经理|负责人|师傅)/.test(r.s));if(factRows.length)checks.push({k:key('fact','all',factRows.map(r=>r.s).join('|')),sev:1,auto:false,text:'稿子里的数字、人名、日期我核对过（问过当事人 / 看过公告）——第 '+factRows.map(r=>rows.indexOf(r)+1).join('、')+' 句',code:'FACT_CHECK · 自确认 · 新闻素养，非后端规则',id:factRows[0].id,goLabel:'去看'});
    checks.sort((a,b)=>a.sev-b.sev);return {checks,win};
  }
  /** 发布门禁：blocking(sev0 未处理数)、open(sev1 未勾选数)、passed */
  gateOf(t){if(!t||t.status!=='done')return {blocking:0,open:0,passed:false,total:0,done:0};const {checks}=this.checksFor(t,t.rows);const isDone=ck=>ck.auto?!!ck.done:!!this.state.checked[ck.k];const blocking=checks.filter(ck=>ck.sev===0&&!isDone(ck)).length,open=checks.filter(ck=>ck.sev===1&&!isDone(ck)).length;return {blocking,open,total:checks.length,done:checks.filter(isDone).length,passed:checks.every(ck=>ck.sev===2||isDone(ck))};}
  cur(){return this.state.currentId?this.state.tasks[this.state.currentId]:null;}
  toast(msg,act){clearTimeout(this.toastTimer);this.setState({toast:msg,toastAct:act||null});this.toastTimer=setTimeout(()=>this.setState({toast:null,toastAct:null}),act?5000:2800);}
  bump(){this.setState(p=>({tasks:{...p.tasks}}));}
  pendingCount(o){const S=o||this.state;return (S.deleted||[]).length+Object.keys(S.pendReplace||{}).length+Object.keys(S.pendEdit||{}).length+Object.keys(S.pendVoice||{}).length+(S.pendPacing?1:0)+Object.keys(S.pendTrim||{}).length+Object.keys(S.pendTake||{}).length+(S.pendToNarration||[]).length+(Object.keys(S.pendSpeakers||{}).length?1:0);}
  guard(fn){if(this.state.screen==='result'&&this.pendingCount()){this.setState({confirm:{title:'还有没应用的修改',body:'现在离开，记下的修改会丢掉。',ok:'丢掉并离开',fn}});return true;}return false;}
  newTask(force){if(!force&&this.guard(()=>this.newTask(true)))return;clearInterval(this.playTimer);this.setState({screen:'create',step:1,script:'',files:[],currentId:null,drawerOpen:false,playing:false,sentMarks:{},elemMarks:{},typeMarks:{},speakerNames:{},pickOpen:true,suggestOff:{},...this.resetResult()});}
  resetResult(extra){const pend={...(this.state.pend||{})};if(this.state.currentId)delete pend[this.state.currentId];return {pend,deleted:[],pendReplace:{},pendEdit:{},pendVoice:{},pendPacing:null,pendTrim:{},pendTake:{},pendToNarration:[],pendSpeakers:{},trim:null,viewRev:null,editing:false,instruction:'',recording:null,time:0,playing:false,exportOpen:false,...extra};}
  /** 写稿向导草稿（随 persist 保存，刷新后在 componentDidMount 放回） */
  draftOf(){const S=this.state;const has=(S.script||'').trim().length>0||S.files.length>0;return has?{script:S.script,step:S.step,prefs:S.prefs,mode:S.mode,typeMarks:S.typeMarks,speakerNames:S.speakerNames,files:S.files.map(f=>({name:f.name,sec:f.sec,mb:f.mb,speech:!!f.speech,spk:f.spk||null,note:f.note||'',inSec:f.inSec,outSec:f.outSec,img:!!f.img,bad:f.bad||null,shot:f.shot!=null?f.shot:null,thumb:f.thumb&&!f.thumb.startsWith('data:')?f.thumb:null})),elemMarks:S.elemMarks,sentMarks:S.sentMarks,at:Date.now()}:null;}
  // ---- files
  addFiles(list){const files=[...this.state.files];const probes=[];let skipped=0;let totalMb=files.filter(f=>!f.bad&&!f.stale).reduce((a,f)=>a+f.mb,0),totalSec=files.filter(f=>!f.bad&&!f.stale).reduce((a,f)=>a+(f.sec||0),0);for(const f of list){const si=files.findIndex(x=>x.stale&&x.name===f.name);if(si>=0)files.splice(si,1);if(!(f instanceof File)&&files.some(x=>x.name===f.name)){skipped++;continue;}if(files.length>=LIMITS.maxFiles){this.toast('最多 '+LIMITS.maxFiles+' 个文件');break;}const ext=(f.name.split('.').pop()||'').toLowerCase();const rawMb=f.size!=null?f.size/1048576:0;const mb=f.mb!=null?f.mb:(rawMb<10?+rawMb.toFixed(1):Math.round(rawMb));const isImg=['jpg','jpeg','png','gif'].includes(ext);let bad=f.bad||null;if(!bad&&!isImg&&!['mp4','mov','avi','mkv'].includes(ext))bad='这种格式不支持：视频转成 mp4，图片用 jpg / png';if(!bad&&mb>LIMITS.fileMb)bad='超过 '+LIMITS.fileMb+' MB，太大了，压缩或剪短再传';if(!bad&&f.sec&&f.sec>LIMITS.fileMin*60)bad='超过 '+LIMITS.fileMin+' 分钟，剪短再传';if(!bad&&totalMb+mb>LIMITS.totalGb*1024)bad='加上它，总量就超过 '+LIMITS.totalGb+' GB 了';if(!bad&&f.sec&&totalSec+f.sec>LIMITS.totalMin*60)bad='加上它，总时长就超过 '+LIMITS.totalMin+' 分钟了';if(!bad&&files.some(x=>x.name===f.name&&x.mb===mb))bad='这个文件已经传过了';const entry={name:f.name,sec:isImg?3:(f.sec||null),img:isImg,mb,speech:!!f.speech,spk:f.spk||null,shot:f.shot!=null?f.shot:null,asr:(!bad&&f.speech)?'pending':null,bad,thumb:f.shot!=null?thumb(f.shot):null};files.push(entry);if(!bad){totalMb+=mb;totalSec+=f.sec||0;if(f instanceof File&&!isImg)probes.push([entry.name,f]);}}if(skipped&&skipped===list.length)this.toast('示例素材已经在列表里了');this.setState({files});probes.forEach(([name,file])=>this.probe(name,file));
    // 预转写：上传即后台转写（真实实现 ≤ 素材时长 × 0.3 内返回）；原型 1.5s 后标记完成
    files.filter(x=>x.asr==='pending').forEach(x=>setTimeout(()=>this.setState(p=>({files:p.files.map(y=>y.name===x.name&&y.asr==='pending'?{...y,asr:'done'}:y)})),1500/(this.props.simSpeed||1)));}
  probe(name,file){try{const url=URL.createObjectURL(file);const v=document.createElement('video');v.preload='metadata';v.muted=true;v.src=url;const fail=()=>{clearTimeout(tm);URL.revokeObjectURL(url);this.setState(p=>({files:p.files.map(x=>x.name===name&&!x.sec?{...x,probeFailed:true}:x)}));};const tm=setTimeout(fail,5000);v.onloadedmetadata=()=>{clearTimeout(tm);const sec=Math.round(v.duration||0);v.currentTime=Math.min(1,(v.duration||1)/2);v.onseeked=()=>{let th=null;try{const c=document.createElement('canvas');c.width=160;c.height=90;c.getContext('2d').drawImage(v,0,0,160,90);th=c.toDataURL('image/jpeg',.7);}catch(e){}URL.revokeObjectURL(url);this.setState(p=>{const others=p.files.filter(x=>x.name!==name&&!x.bad).reduce((a,x)=>a+(x.sec||0),0);return {files:p.files.map(x=>x.name===name?{...x,sec:sec||x.sec,thumb:th||x.thumb,bad:x.bad||(sec>LIMITS.fileMin*60?'超过 '+LIMITS.fileMin+' 分钟，剪短再传':others+sec>LIMITS.totalMin*60?'加上它，总时长就超过 '+LIMITS.totalMin+' 分钟了':null)}:x)};});};};v.onerror=fail;}catch(e){}}
  // ---- pipeline（每个 step = 一次真实 API 调用：remix 或 replace-shot）
  /** 按 plan.steps 依次跑阶段；每个 step 对应一次后端调用。真实实现：轮询 GET /api/tasks/{id} */
  runTask(id,steps,opts){
    clearInterval(this.timer);if(this.runningId&&this.runningId!==id){const prev=this.state.tasks[this.runningId];if(prev&&['running','queued','uploading'].includes(prev.status))finish(prev,prev.op);}
    this.runningId=id;const t=this.state.tasks[id];const plan=steps||[{op:'初版',run:STAGES.map(s=>s.n)}];const first=plan[0];
    t.plan={steps:plan,idx:0};t.op=first.op;t.progress=0;t.error=null;t.errorKind=null;t.queue=0;if(!steps)t.startedAt=Date.now();
    if(!steps&&t.upTotal&&(t.upDone||0)<t.upTotal){t.status='uploading';t.upStart=Date.now();t.upEnd=null;}else t.status='queued';
    t.stages.forEach(s=>{if(first.run.includes(s.n)){s.status='pending';s.msg='';s.elapsed=null;}else{s.status='done';s.msg=' · 沿用上次结果';}});
    this.queue=first.run.slice();this.stageStart=Date.now();
    if(opts&&opts.background)this.setState(p=>({tasks:{...p.tasks}}));else this.setState(p=>({tasks:{...p.tasks},screen:'processing',currentId:id,playing:false,drawerOpen:false}));
    this.timer=setInterval(()=>this.tick(),100);
  }
  resumeAll(){const run=Object.values(this.state.tasks).find(x=>x.status==='running');if(run)this.resumeTask(run.id);else this.startNext();}
  resumeTask(id){const t=this.state.tasks[id];if(!t||t.status!=='running')return;clearInterval(this.timer);this.runningId=id;if(!t.plan)t.plan={steps:[{op:t.op||'初版',run:STAGES.map(s=>s.n)}],idx:0};t.op=t.op||'初版';this.queue=t.stages.filter(s=>s.status!=='done').map(s=>s.n);this.stageStart=Date.now();this.timer=setInterval(()=>this.tick(),100);}
  startNext(){const list=Object.values(this.state.tasks).filter(x=>x.released&&x.status==='queued').sort((a,b)=>(a.queue||0)-(b.queue||0));if(!list.length)return;list.forEach((x,i)=>{x.queue=i;});const first=list[0];const watching=this.state.currentId===first.id&&this.state.screen==='processing';this.runTask(first.id,null,{background:!watching});}
  /** 模拟上传（LIMITS.uploadMbps）；完成后进入队列并 startNext()。真实实现：XHR upload progress */
  uploadOnly(id){const t0=this.state.tasks[id];if(!t0)return;t0.status='uploading';t0.upStart=Date.now();t0.upEnd=null;t0.upDone=0;this.bump();const iv=setInterval(()=>{const x=this.state.tasks[id];if(!x||x.status!=='uploading'){clearInterval(iv);return;}x.upDone=Math.min(x.upTotal,(x.upDone||0)+LIMITS.uploadMbps*(this.props.simSpeed||1)*0.1);if(this.props.scenario==='netfail'&&x.userMade&&!this.failedOnce&&x.upDone>=x.upTotal*0.4){this.failedOnce=true;x.status='failed';x.errorKind='network';x.error='网络断了，上传没完成（传到 '+Math.round(x.upDone/x.upTotal*100)+'%）。';x.errorPro='multipart POST /api/tasks 中断 · 服务端在收到完整请求前不会计入配额';clearInterval(iv);this.bump();return;}if(x.upDone>=x.upTotal){x.upEnd=Date.now();clearInterval(iv);x.released=true;x.status='queued';const running=this.runningId&&this.state.tasks[this.runningId];const busy=!!running&&running.id!==id&&['running','queued','uploading'].includes(running.status);if(busy){x.queue=Object.values(this.state.tasks).filter(y=>y.released&&y.status==='queued'&&y.id!==id).length+1;this.toast('素材已上传，前面还有 '+x.queue+' 个；可以关页面了');}else{this.bump();this.startNext();return;}}this.bump();},100);this.upTimers=this.upTimers||[];this.upTimers.push(iv);}
  /** 100ms 心跳：推进 uploading→queued→running→各阶段→complete()；scenario 在此注入演示失败 */
  tick(){
    const t=this.state.tasks[this.runningId];if(!t||!['running','queued','uploading'].includes(t.status)){clearInterval(this.timer);return;}
    const speed=this.props.simSpeed||1,now=Date.now();
    if(t.status==='uploading'){t.upDone=Math.min(t.upTotal,(t.upDone||0)+LIMITS.uploadMbps*speed*0.1);if(this.props.scenario==='netfail'&&!this.failedOnce&&t.upDone>=t.upTotal*0.4){this.failedOnce=true;t.status='failed';t.errorKind='network';t.error='网络断了，上传没完成（传到 '+Math.round(t.upDone/t.upTotal*100)+'%）。';t.errorPro='multipart POST /api/tasks 中断 · 配额在请求开始时已计数（_enforce_task_rate_limits 先于读取 body）· 服务端 429 则不计数';clearInterval(this.timer);this.runningId=null;this.bump();this.startNext();return;}if(t.upDone>=t.upTotal){t.upEnd=now;t.status='queued';this.stageStart=now;}this.bump();return;}
    if(t.status==='queued'){if(now-this.stageStart>600/speed){t.status='running';this.stageStart=now;}this.bump();return;}
    if(!this.queue.length){
      const plan=t.plan;
      if(plan&&plan.idx<plan.steps.length-1){plan.idx++;const step=plan.steps[plan.idx];t.op=step.op;t.stages.forEach(s=>{if(step.run.includes(s.n)){s.status='pending';s.msg='';s.elapsed=null;}});this.queue=step.run.slice();this.stageStart=now;this.bump();return;}
      const ok=this.complete(t,plan?plan.steps.map(s=>s.op).join('＋'):t.op);clearInterval(this.timer);this.runningId=null;const lf=this.lastFails;this.lastFails=null;if(!ok){this.bump();this.toast('「'+t.title.slice(0,12)+'」检查没通过，看看原因');this.startNext();return;}const watching=this.state.currentId===t.id&&this.state.screen==='processing';if(watching){const fails=t.replaceFails||{};const sid=this.state.selectedId;const failRow=t.rows.find(r=>fails[r.id]&&r.id===sid)||t.rows.find(r=>fails[r.id]&&lf&&lf.includes(r.id));const keep=failRow||t.rows.find(r=>r.id===sid&&(r.replaced||r.edited||r.mine));const selRow=keep||t.rows[0];this.setState(p=>({tasks:{...p.tasks},screen:'result',selectedId:selRow.id,...this.resetResult({time:keep?this.rowStarts(t)[t.rows.indexOf(selRow)]:0})}));}else this.bump();this.toast((t.versions.length>1?'第 '+(t.versions.length-1)+' 次修改做好了':'做好了！')+(watching?'':'——「'+t.title.slice(0,12)+'」在我的作品里'));this.startNext();return;}
    const n=this.queue[0],st=t.stages[n-1];
    if(st.status!=='running'){st.status='running';this.stageStart=now;t.current=n;}
    const frac=Math.min(1,(now-this.stageStart)/(SIM[n-1]/speed*1000));
    {const W=STAGE_W[t.mode||'voiceover']||STAGE_W.voiceover,tot=W.reduce((a,b)=>a+b,0),done=t.stages.filter(s=>s.status==='done').reduce((a,s)=>a+W[s.n-1],0);t.progress=Math.round((done+frac*W[n-1])/tot*100);}
    if(frac>=1){
      const sc=this.props.scenario;
      if(t.quoteMissing&&n===6&&!t.qmFailed){const bad=t.rows.filter(r=>r.missing).map(r=>t.rows.indexOf(r)+1);st.status='failed';t.status='failed';t.qmFailed=true;t.errorKind='quote_missing';t.badRows=bad;t.error='有 '+bad.length+' 句原话在素材里没找到（第 '+bad.join('、')+' 句）。';t.errorPro='stage 6 强制对齐失败 · QUOTE_NOT_FOUND × '+bad.length+' · 正式转写与预转写不一致 · 阶段 1–5 产物可复用';clearInterval(this.timer);this.runningId=null;this.bump();this.startNext();return;}
      if(sc==='failed'&&n===4&&t.userMade&&!this.failedOnce){st.status='failed';t.status='failed';this.failedOnce=true;t.errorKind='transient';t.error='AI 看画面的时候卡住了，可能是服务太忙。';t.errorPro='stage 4 画面理解失败 · vision provider 503 · 无续跑接口，只能重新 POST /api/tasks（重新上传、消耗配额）';clearInterval(this.timer);this.runningId=null;this.bump();this.startNext();return;}
      if(sc==='shortage'&&n===6&&t.userMade&&!this.failedOnce&&t.revision===0){const rows=t.rows.length,shots=Math.max(1,rows-4);st.status='failed';t.status='failed';this.failedOnce=true;t.errorKind='shortage';t.shortBy=rows-shots;t.error='素材里只切出 '+shots+' 个能用的镜头，稿子有 '+rows+' 句——每句要一个不同的镜头，还差 '+(rows-shots)+' 个。';t.errorPro='stage 6 语义匹配失败 · ISSUE-01 usable_shots '+shots+' < sentences '+rows+' · 阶段 1–5 的云端费用已产生 → 应在第 2 步前置校验';clearInterval(this.timer);this.runningId=null;this.bump();this.startNext();return;}
      st.status='done';st.elapsed=SIM[n-1]*11;st.msg=stageMsg(n,t);this.queue.shift();
    }
    this.bump();
  }
  /** 提交作品：校验稿件/素材 → 建 task → uploadOnly()。真实实现：POST /api/tasks(multipart) */
  start(){
    const now=Date.now();
    if(this.props.scenario==='maintenance'){this.toast('服务维护中，稍后再试');return;}
    const {script,files}=this.state;const valid=files.filter(f=>!f.bad&&!f.stale);
    if((this.state.mode==='original'?!script.trim():script.trim().length<20)||!valid.length||files.length!==valid.length)return;
    const qUsed=now>this.state.quotaResetAt?0:this.state.quotaUsed,qReset=now>this.state.quotaResetAt?now+H:this.state.quotaResetAt;
    const id='t-'+Date.now().toString(36);const title=(script.split('\n')[0]||'').trim().replace(/[。！？!?.]+$/,'').slice(0,32)||'未命名作品';
    const prefs={...this.state.prefs};
    const mode=this.state.mode||'voiceover';
    const t=makeTask(id,title,{fileCount:valid.length,prefs,mode,typeMarks:{...this.state.typeMarks},speakerNames:{...this.state.speakerNames},script,files:valid.map(f=>({...f})),upTotal:valid.reduce((a,f)=>a+f.mb,0)});
    if(mode!=='voiceover'){const units=scriptUnits(script,mode,this.state.typeMarks);const asr=valid.filter(f=>f.asr==='done');const qm=units.filter(u=>u.kind==='quote').map(u=>({u,...matchQuote(u.text,asr)}));const speakers={};speakersOf(asr,qm,this.state.speakerNames).forEach(s=>{speakers[s.spk]={name:s.name||'',role:s.role||''};});t.speakers=speakers;t.rows=buildRows(units,asr.concat(valid.filter(f=>f.asr!=='done')),prefs,mode);t.quoteMissing=t.rows.some(r=>r.missing);}
    else{if(!/融合高新科技/.test(script))t.rows=t.rows.filter(r=>!r.fallback);
    if(!/糕点/.test(script))t.rows.forEach(r=>{r.miss=[];});}
    if(t.prefs.voice==='mine'){t.rows.forEach(r=>{if(!r.sync){r.mine=true;r.myCpm=cpmOf(r)+Math.round(Math.random()*40-20);}});}
    const clean={script:'',files:[],step:1,sentMarks:{},elemMarks:{},typeMarks:{},speakerNames:{},suggestOff:{}};
    t.userMade=true;this.setState(p=>({tasks:{...p.tasks,[id]:t},history:[id,...p.history],quotaUsed:qUsed+1,quotaResetAt:qReset,screen:'processing',currentId:id,playing:false,...clean}),()=>this.uploadOnly(id));
  }
  restoreDraft(step){const t=this.cur();if(!t)return;this.setState({screen:'create',step:step||1,script:t.script||'',files:(t.files||[]).map(f=>({...f})),mode:t.mode||'voiceover',typeMarks:{...(t.typeMarks||{})},speakerNames:{...(t.speakerNames||{})},pickOpen:true,currentId:null});this.toast(step===2?(t.errorKind==='quote_missing'?'素材已放回，传含那几句话的采访视频':'素材已放回，再传几个不同场景的视频'):(t.errorKind==='qc'?'稿子已放回，把第 '+(t.badRows||[]).join('、')+' 句改具体或删掉再试':t.errorKind==='quote_missing'?'稿子已放回，把第 '+(t.badRows||[]).join('、')+' 句改成真说过的话，或删掉':'稿子已放回，删掉或改具体几句再试'));}
  retrySame(){const t=this.cur();if(!t)return;this.setState({script:t.script||'',files:(t.files||[]).map(f=>({...f})),mode:t.mode||'voiceover',typeMarks:{...(t.typeMarks||{})},speakerNames:{...(t.speakerNames||{})}},()=>this.start());}
  deleteTask(id){if(this.runningId===id){clearInterval(this.timer);this.runningId=null;}clearInterval(this.playTimer);this.setState(p=>{const tasks={...p.tasks};delete tasks[id];const pend={...(p.pend||{})};delete pend[id];return {tasks,pend,history:p.history.filter(h=>h!==id),currentId:null,screen:'create',step:1,confirm:null,playing:false,exportOpen:false};},()=>this.startNext());this.toast('已删除');}
  /** 打开作品：按 status 进入 processing / result / gone，并恢复该作品未提交的修改(pend) */
  openTask(id,force){
    if(!force&&id!==this.state.currentId&&this.guard(()=>this.openTask(id,true)))return;
    const t=this.state.tasks[id];if(!t)return;clearInterval(this.playTimer);
    if(t.gone){this.setState({screen:'gone',currentId:id,drawerOpen:false,playing:false});return;}
    if(t.status==='done'){const saved=(this.state.pend||{})[id];this.setState({screen:'result',currentId:id,selectedId:t.rows[0].id,drawerOpen:false,...this.resetResult(),...(saved||{})});if(saved){const n=this.pendingCount(saved);if(n)this.toast('上次记下的 '+n+' 处修改还在，点右下角应用');}}
    else this.setState({screen:'processing',currentId:id,drawerOpen:false,playing:false});
  }
  rowStarts(t){let acc=0;return t.rows.map(r=>{const s=acc;acc+=r.d;return s;});}
  togglePlay(){
    const t=this.cur();if(!t)return;
    if(this.state.playing){clearInterval(this.playTimer);this.setState({playing:false});return;}
    const total=t.rows.reduce((a,r)=>a+r.d,0);
    this.playTimer=setInterval(()=>{this.setState(p=>{const cur=p.currentId?p.tasks[p.currentId]:null;if(!cur||cur.id!==t.id||!p.playing){clearInterval(this.playTimer);return {playing:false};}const nt=p.time+0.2;if(nt>=total){clearInterval(this.playTimer);return {time:0,playing:false};}const starts=this.rowStarts(t);const i=starts.findIndex((s,k)=>nt>=s&&nt<s+t.rows[k].d);return {time:nt,selectedId:i>=0?t.rows[i].id:p.selectedId};});},200);
    this.setState({playing:true});
  }
  record(){
    const S=this.state,t=this.cur();if(!t||S.recording)return;const id=S.selectedId;
    this.setState({recording:{id,left:3}});
    this.recTimer=setInterval(()=>{this.setState(p=>{if(!p.recording)return null;const left=+(p.recording.left-0.1).toFixed(1);if(left<=0){clearInterval(this.recTimer);const r=t.rows.find(x=>x.id===id);const cpm=cpmOf(r)+Math.round(Math.random()*60-30);return {recording:null,pendVoice:{...p.pendVoice,[id]:{cpm}}};}return {recording:{id,left}};});},100);
  }
  /** 把记下的修改一次性提交：删句/改字/自录/语速 → remix；换画面 → replace-shot（逐句）；随后 runTask 重做 [9,10] 或 [6,9,10] */
  applyPending(){
    const S=this.state,t=this.cur();if(!t)return;
    const dels=S.deleted,reps=S.pendReplace,edits=S.pendEdit,voices=S.pendVoice;
    let rows=t.rows.filter(r=>!dels.includes(r.id));if(!rows.length){this.toast('至少要留一句');return;}
    const remixOps=[];const remixRun=new Set([9,10]);
    if(dels.length){remixOps.push('删 '+dels.length+' 句');remixRun.add(7);remixRun.add(8);}
    const used=new Set(rows.flatMap(x=>x.beats.map(b=>b.shot)));const fails={};
    Object.entries(reps).forEach(([id,ins])=>{const r=rows.find(x=>x.id===+id);if(!r)return;const direct=POOL.find(p=>!used.has(p)&&SHOT[p]===ins);const abstract=/(融合|氛围|意义|精神|平台|理念|纽带|感受|未来|发展|文化)/.test(ins)||ins.replace(/\s/g,'').length<4;if(!POOL.length){fails[r.id]={kind:'none',text:'素材里没有其他镜头了。'};return;}const cand=direct!=null?direct:POOL.find(p=>!used.has(p));if(cand==null){fails[r.id]={kind:'used',text:'没用过的镜头都被别的句子占了。'};return;}if(direct==null&&abstract){fails[r.id]={kind:'abstract',text:'“'+ins+'”太抽象，AI 找不到能直接表现它的画面。'};return;}used.add(cand);r.beats=[{shot:cand}];r.fallback=false;r.gen=false;r.overlay=null;r.c=direct!=null?0.83:0.71;r.replaced=ins;r.miss=(r.miss||[]).filter(m=>!(SHOT[cand]||'').includes(m));});
    t.replaceFails={...(t.replaceFails||{}),...fails};Object.keys(reps).forEach(id=>{if(!fails[id])delete t.replaceFails[id];});const failed=Object.keys(fails).map(id=>rows.findIndex(x=>x.id===+id)+1);this.lastFails=Object.keys(fails).map(Number);
    Object.entries(edits).forEach(([id,txt])=>{const r=rows.find(x=>x.id===+id);if(!r)return;const oldC=chars(r.s)||1;r.s=txt;r.miss=(r.miss||[]).filter(m=>txt.includes(m));r.d=+(r.d*chars(txt)/oldC).toFixed(2)||1;r.edited=true;r.sync=false;remixRun.add(7);remixRun.add(8);});
    if(Object.keys(edits).length)remixOps.push('改 '+Object.keys(edits).length+' 句字');
    Object.entries(voices).forEach(([id,v])=>{const r=rows.find(x=>x.id===+id);if(!r)return;r.mine=true;r.myCpm=v.cpm;r.sync=false;remixRun.add(7);remixRun.add(8);});
    if(Object.keys(voices).length)remixOps.push('录 '+Object.keys(voices).length+' 句音');
    if(S.pendPacing){t.prefs={...t.prefs,pacing:S.pendPacing};const k=S.pendPacing==='slow'?1.12:S.pendPacing==='fast'?0.9:1;rows.forEach(r=>{if(!r.mine&&!r.sync)r.d=+(r.d*k).toFixed(2);});remixOps.push('调语速');remixRun.add(7);remixRun.add(8);}
    // 原声句：剪短（只缩小区间）/ 换一段 / 改成旁白 / 改说话人名字
    Object.entries(S.pendTrim||{}).forEach(([id,v])=>{const r=rows.find(x=>x.id===+id);if(!r||r.kind!=='quote')return;const span=Math.max(0.1,r.t1-r.t0),cps=(r.spoken||'').length/span;const a=Math.max(0,Math.round((v.start-r.t0)*cps)),b=Math.min((r.spoken||'').length,Math.round((v.end-r.t0)*cps));r.spoken=(r.spoken||'').slice(a,Math.max(a+1,b));r.t0=+v.start.toFixed(2);r.t1=+v.end.toFixed(2);r.d=+(r.t1-r.t0).toFixed(2);r.trimmed=true;remixRun.add(7);remixRun.add(8);});
    if(Object.keys(S.pendTrim||{}).length)remixOps.push('剪短 '+Object.keys(S.pendTrim).length+' 句');
    Object.entries(S.pendTake||{}).forEach(([id,a])=>{const r=rows.find(x=>x.id===+id);if(!r||r.kind!=='quote')return;Object.assign(r,{file:a.file,t0:a.t0,t1:a.t1,spoken:a.spoken,c:a.score,snr:a.snr,spk:a.spk,d:+(a.t1-a.t0).toFixed(2),took:true,trimmed:false});remixRun.add(7);remixRun.add(8);});
    if(Object.keys(S.pendTake||{}).length)remixOps.push('换 '+Object.keys(S.pendTake).length+' 段原声');
    (S.pendToNarration||[]).forEach(id=>{const r=rows.find(x=>x.id===id);if(!r||r.kind!=='quote')return;const target=PACING_CPM[(t.prefs&&t.prefs.pacing)||'normal'];const usedNow=new Set(rows.flatMap(x=>x.beats.map(b=>b.shot)));const shot=Object.keys(SHOT).map(Number).find(p=>!usedNow.has(p)&&!Object.values(SPK_SHOT).includes(p));Object.assign(r,{kind:'narration',sync:false,spoken:null,missing:false,jump:null,cover:null,coverShot:null,d:+Math.max(1.2,chars(r.s)/target*60).toFixed(2),c:0.7,beats:[{shot:shot!=null?shot:1}],toNarr:true});remixRun.add(6);remixRun.add(7);remixRun.add(8);});
    if((S.pendToNarration||[]).length)remixOps.push((S.pendToNarration||[]).length+' 句改成旁白');
    if(Object.keys(S.pendSpeakers||{}).length){t.speakers={...(t.speakers||{})};Object.entries(S.pendSpeakers).forEach(([k,v])=>{t.speakers[k]={...(t.speakers[k]||{}),...v};});t.speakerNames={...(t.speakerNames||{}),...t.speakers};remixOps.push('改说话人名字');remixRun.add(8);}
    if((t.mode||'voiceover')!=='voiceover')markJumps(rows,(t.prefs&&t.prefs.jump_cut_cover)||'broll',brollOf(t.files||[]));
    const steps=[];if(remixOps.length)steps.push({op:'重做：'+remixOps.join('、'),run:[...remixRun].sort((a,b)=>a-b)});
    Object.keys(reps).forEach(id=>{const i=t.rows.findIndex(x=>x.id===+id);if(i>=0&&rows.some(x=>x.id===+id))steps.push({op:'换第 '+(i+1)+' 句画面',run:[6,9,10]});});
    if(!steps.length)steps.push({op:'重新合成',run:[9,10]});
    t.rows=rows;t.revision+=steps.length;
    if(failed.length)this.toast('第 '+failed.join('、')+' 句没换成，打开那句看原因和建议');
    this.setState(this.resetResult());
    this.runTask(t.id,steps);
  }
  /** 回到某个历史版本：复制 versions[rev].rows 后重新合成 */
  restoreVersion(){const S=this.state,t=this.cur();if(!t||S.viewRev==null)return;const v=t.versions.find(x=>x.rev===S.viewRev);if(!v)return;t.rows=cloneRows(v.rows);t.revision+=1;this.setState(this.resetResult());this.runTask(t.id,[{op:'恢复'+(t.versions.indexOf(v)?'第 '+t.versions.indexOf(v)+' 次修改':'初版')+'的样子',run:[9,10]}]);}
  /** 视图模型：模板 {{ }} 只读取这里返回的键；按屏幕分块：向导 → 制作中 → 结果页(故事板/检查/句面板) → 专业台 → 抽屉/弹窗 */
  renderVals(){
    const S=this.state,P=this.props,dev=!!P.devNotes,demo=P.demoHelpers!==false,t=this.cur(),maintenance=P.scenario==='maintenance',now=Date.now();
    const mode=(S.screen==='create'||!t||!t.mode)?S.mode:t.mode,isMixed=mode==='mixed',isOriginal=mode==='original',isVoice=mode==='voiceover',scn=P.demoScenario||'normal',qmAllowed=scn==='quoteMissing';
    const target=PACING_CPM[(S.screen==='create'?S.prefs.pacing:(t&&t.prefs&&t.prefs.pacing))||'normal'];
    const set=o=>()=>this.setState(o);
    const stepObj=(n,ok)=>{const done=S.step>n&&ok,curr=S.step===n;return {done,notDone:!done,mark:String(n),bg:done?TEAL:curr?INK:'#fff',fg:done||curr?'#fff':MUTED,border:done?TEAL:curr?INK:LINE,label:curr||done?INK:MUTED,line:done?TEAL:LINE};};
    const script=S.script,first=(script.split('\n')[0]||'').trim();
    const titlePlaceholder=/^[（(]写一个标题[)）]$/.test(first);
    const titlePunct=/[。！？.!?]$/.test(first),titleLong=first.length>40,hasTitle=first.length>0&&!titleLong&&!titlePunct&&!titlePlaceholder;
    const charCount=script.length,step1Ok=isOriginal?(!script.trim()||hasTitle):(script.trim().length>=20&&hasTitle);
    const elements=ELEMENTS.map(([k,label])=>{const found=RX[k].test(script),mk=S.elemMarks[k],manual=!!mk,ok=found||manual,ev=typeof mk==='string'?mk:'';return {label,on:ok,mark:ev?'✓ '+ev:found?'✓':manual?'✓ 我写了':'○ 没找到',border:ok?TEAL:LINE,bg:ok?'#e7f3ea':'#fff',fg:ok?TEAL:MUTED,flip:()=>{const ta=this.scriptRef&&this.scriptRef.current;const st=ta&&ta.selectionStart!==ta.selectionEnd?ta.value.slice(ta.selectionStart,ta.selectionEnd).trim().slice(0,12):'';this.setState(p=>({elemMarks:{...p.elemMarks,[k]:st?st:(p.elemMarks[k]?false:true)}}));}};});
    const missing=ELEMENTS.filter(([k])=>!RX[k].test(script)&&!S.elemMarks[k]).map(([,l])=>l);
    const allUnits=scriptUnits(script,mode,S.typeMarks),quoteUnits=allUnits.filter(u=>u.kind==='quote'),units=allUnits.filter(u=>u.kind!=='quote').map(u=>u.text),sentenceEst=Math.max(1,units.length),longUnits=units.filter(u=>chars(u)>30),listUnits=units.filter(u=>/、|等/.test(u)).length,needShots=sentenceEst+listUnits;
    const sentRaw=units.slice(0,60),sentShown=S.sentListOpen?sentRaw:sentRaw.slice(0,12);
    const ABSTRACT_RX=/(融合|氛围|意义|精神|平台|理念|纽带|感受|未来|发展|文化|一体|气氛|活力|风采|凝聚)/;const shotList=units.map((u,i)=>{let advice,fg=INK;if(/(说|表示|介绍|告诉|称|认为)/.test(u)){advice='采访：让对方一句话说完，15 秒以内；拍正脸要先问同意';fg=TEAL;}else if(/、|等/.test(u)){const items=u.replace(/等.*$/,'').split(/、|如|和|与/).map(s=>s.replace(/^[^\u4e00-\u9fa5\d]+|[^\u4e00-\u9fa5\d]+$/g,'')).filter(s=>s&&s.length<=6);advice='写了 '+(items.length||2)+' 样：'+(items.length?items.join('、')+'，':'')+'每样拍一个特写';}else if(ABSTRACT_RX.test(u)){advice='总结句，拍不到——拍人们正在做什么，或考虑删掉';fg=RED;}else if(/\d+月\d+日|\d{4}年|(主办|协办|承办)/.test(u)){advice='日期 / 机构名会做成字幕条；配一个环境镜头（门口、招牌）';fg=MUTED;}else advice='拍一个能看出这句话的画面：远景 + 一个特写';return {idx:String(i+1).padStart(2,'0'),text:u,advice,fg};});
    // ---- 三模式：预转写、稿句对齐、说话人、跳切预估、句子清单、转写挑句、智能建议
    const validFiles0=S.files.filter(f=>!f.bad&&!f.stale),asrFiles=validFiles0.filter(f=>f.asr==='done');
    const qMatches=quoteUnits.map(u=>({u,...matchQuote(u.text,asrFiles)}));const unmatched=qMatches.filter(m=>m.status==='missing'),unsureQ=qMatches.filter(m=>m.status==='unsure');
    const spkList=speakersOf(asrFiles,qMatches,S.speakerNames);const spkLabel=k=>{const s=spkList.find(x=>x.spk===k);return s&&s.name?s.name:('说话人 '+String(k||'').replace(/\D/g,''));};
    const speakerRows=spkList.map((s,i)=>({idx:String(i+1),name:s.name||'',role:s.role||'',meta:s.count?'出现 '+s.count+' 次 · '+Math.round(s.sec)+' 秒':(s.name?'还没用到':'还没填名字'),metaFg:s.name?MUTED:'#8a5a0b',metaW:s.name?400:800,border:s.name?LINE:GOLD,bg:s.name?'#fff':'#fdf2d8',onName:e=>{const v=e.target.value.slice(0,8);this.setState(p=>({speakerNames:{...p.speakerNames,[s.spk]:{...(p.speakerNames[s.spk]||{}),name:v}}}));},onRole:e=>{const v=e.target.value.slice(0,12);this.setState(p=>({speakerNames:{...p.speakerNames,[s.spk]:{...(p.speakerNames[s.spk]||{}),role:v}}}));}}));
    const quoteSec=qMatches.reduce((a,m)=>a+(m.best&&m.status!=='missing'?m.best.t1-m.best.t0:Math.max(1,chars(m.u.text)/4)),0),narrSecAll=Math.round(units.reduce((a,u)=>a+chars(u),0)/target*60),estSec=narrSecAll+Math.round(quoteSec);
    let estJumps=0;{let prev=null;allUnits.forEach(u=>{if(u.kind!=='quote'){prev=null;return;}const m=qMatches.find(x=>x.u===u);const b=m&&m.status!=='missing'?m.best:null;if(prev&&b&&(prev.file!==b.file||Math.abs(b.t0-prev.t1)>0.5))estJumps++;prev=b;});}
    const spokenSet=new Set(asrFiles.flatMap(f=>transcriptOf(f)).flatMap(l=>(l.text+norm(l.text)).split('')));const isCJK=c=>/[\u4e00-\u9fa5\d]/.test(c);const unsaid=c=>isCJK(c)&&!spokenSet.has(c)&&!spokenSet.has(norm(c));
    const newWordChars=isOriginal&&asrFiles.length?[...new Set(quoteUnits.flatMap(u=>[...u.text].filter(unsaid)))]:[];
    const quoteLong=quoteUnits.filter(u=>chars(u.text)>60);
    const sentRows=(isVoice?units.map(u=>({kind:'narration',text:u})):allUnits).slice(0,60).map((u,i)=>{const row={idx:String(i+1).padStart(2,'0'),text:u.text,leftBorder:u.kind==='quote'?TEAL:'transparent',chunks:[{text:u.text,fg:INK,deco:'none'}],hasAdvice:false,advice:'',fg:INK,hasStatus:false,status:'',stBg:'#fff',stFg:INK,score:'',hasChip:false,chip:'',chipBg:'#fff',chipFg:INK,chipBorder:LINE,flip:()=>{}};
      if(u.kind!=='quote'){const sh=shotList.find(x=>x.text===u.text);if(sh){row.hasAdvice=true;row.advice=sh.advice;row.fg=sh.fg;}}
      if(isMixed){const q=u.kind==='quote';row.hasChip=true;row.chip=q?'原声':'旁白';row.chipBg=q?TEAL:'#fff';row.chipFg=q?'#fff':INK;row.chipBorder=q?TEAL:LINE;row.flip=()=>this.setState(p=>{const m={...p.typeMarks};const want=q?'narration':'quote';if(u.auto===want)delete m[u.text];else m[u.text]=want;return {typeMarks:m};});}
      if(u.kind==='quote'){const m=qMatches.find(x=>x.u===u);if(m){row.hasStatus=true;row.status=m.status==='ok'?'✓ 对上了':m.status==='unsure'?'○ 不太确定':'✕ 没找到';row.stBg=m.status==='ok'?'#e7f3ea':m.status==='unsure'?'#fdf2d8':'#fbeee6';row.stFg=m.status==='ok'?TEAL:m.status==='unsure'?INK:RED;row.score=m.best?Math.round(m.best.score*100)+'%':(asrFiles.length?'—':'等转写');}
        if(isOriginal&&asrFiles.length){const ch=[];let cur=null;for(const c of u.text){const bad=unsaid(c);if(cur&&cur.bad===bad)cur.text+=c;else{cur={text:c,bad};ch.push(cur);}}row.chunks=ch.map(x=>({text:x.text,fg:x.bad?RED:INK,deco:x.bad?'underline':'none'}));}}
      return row;});
    const scriptNorms=scriptUnits(script,'original',{}).map(u=>norm(u.text)).filter(x=>x.length>=4);const lineOn=text=>{const n=norm(text);return scriptNorms.some(x=>x===n||(n.includes(x)&&x.length>=6));};
    const lineRows=(f,pickable)=>transcriptOf(f).map(l=>{const on=lineOn(l.text);return {tc:fmtT(l.t0)+'–'+fmtT(l.t1),spk:spkLabel(l.spk),text:l.text,pickable,plain:!pickable,on,flip:()=>on?this.removeFromScript(l.text):this.appendToScript(l.text)};});
    const pickGroups=isOriginal?asrFiles.filter(f=>transcriptOf(f).length).map(f=>({file:f.name,count:transcriptOf(f).length,lines:lineRows(f,true)})):[];
    const pickedN=pickGroups.reduce((a,g)=>a+g.lines.filter(l=>l.on).length,0);
    const speechMeta=f=>{if(!f.speech)return null;const ls=transcriptOf(f);return f.asr==='pending'?'有人说话 · 正在听…':f.asr==='done'?(ls.length?'有人说话 · 转写 '+ls.length+' 句 · 说话人 '+new Set(ls.map(l=>l.spk)).size+' 位':'有人说话 · 这段听不太清'):'有人说话';};
    const a2bHits=isVoice&&asrFiles.length?units.filter(u=>asrFiles.some(f=>transcriptOf(f).some(l=>sim(u,l.text)>=0.85))):[];
    const suggest=isVoice&&a2bHits.length&&!S.suggestOff.a2b?{text:'有 '+a2bHits.length+' 句话和素材里的原话对上了，想直接用原声？',yes:'切到「旁白 + 原声」',no:'不用',go:()=>{this.setMode('mixed');this.setState(p=>{const m={...p.typeMarks};a2bHits.forEach(u=>{m[u]='quote';});return {typeMarks:m,suggestOff:{...p.suggestOff,a2b:true}};});},off:()=>this.setState(p=>({suggestOff:{...p.suggestOff,a2b:true}}))}:(isOriginal&&unmatched.length&&asrFiles.length&&!S.suggestOff.c2b?{text:'有 '+unmatched.length+' 句在素材里没找到，这几句让 AI 读？',yes:'切到「旁白 + 原声」',no:'我自己改',go:()=>{this.setMode('mixed');this.setState(p=>{const m={...p.typeMarks};unmatched.forEach(x=>{m[x.u.text]='narration';});return {typeMarks:m,suggestOff:{...p.suggestOff,c2b:true}};});},off:()=>this.setState(p=>({suggestOff:{...p.suggestOff,c2b:true}}))}:null);
    const files=S.files.map(f=>({name:f.name,meta:f.bad?f.bad:f.stale?'上次选的文件——请重新选择它才能上传':f.img?'图片 · 定格 3 秒 · '+f.mb+' MB · 可以用':[(f.sec?fmtSec(effSec(f))+(effSec(f)!==f.sec?'（原 '+fmtSec(f.sec)+'）':''):f.probeFailed?'读不出时长，仍可上传（服务端会转码）':'读取时长中…'),f.mb+' MB',speechMeta(f)].filter(Boolean).join(' · ')+' · 可以用',metaColor:f.bad?RED:f.stale?'#8a5a0b':MUTED,border:f.bad?RED:f.stale?GOLD:'#ebe2d1',bg:f.bad?'#fbeee6':f.stale?'#fdf2d8':'#fff',thumbImg:f.thumb?'url('+f.thumb+')':'repeating-linear-gradient(135deg,#e4d9c6 0 5px,#f0e7d8 5px 10px)',note:f.note||'',onNote:e=>{const v=e.target.value.slice(0,20);this.setState(p=>({files:p.files.map(x=>x===f?{...x,note:v}:x)}));},hasTrim:!f.bad&&!f.stale&&!f.img&&!!f.sec,inSec:f.inSec!=null?f.inSec:0,outSec:f.outSec!=null?f.outSec:(f.sec||0),onIn:e=>{const hi=(f.outSec!=null?f.outSec:(f.sec||0));const v=Math.max(0,Math.min(Math.round(+e.target.value)||0,hi-1));this.setState(p=>({files:p.files.map(x=>x===f?{...x,inSec:v,outSec:hi}:x)}));},onOut:e=>{const lo=f.inSec||0;const v=Math.max(lo+1,Math.min(Math.round(+e.target.value)||0,f.sec||0));this.setState(p=>({files:p.files.map(x=>x===f?{...x,inSec:lo,outSec:v}:x)}));},hasLines:transcriptOf(f).length>0,open:!!S.fileOpen[f.name],openLabel:S.fileOpen[f.name]?'收起转写 ▴':'看转写 ▾',toggleOpen:()=>this.setState(p=>({fileOpen:{...p.fileOpen,[f.name]:!p.fileOpen[f.name]}})),lines:(S.fileOpen[f.name+'#all']?lineRows(f,isOriginal):lineRows(f,isOriginal).slice(0,8)),more:transcriptOf(f).length>8,moreLabel:S.fileOpen[f.name+'#all']?'收起':'展开全部 '+transcriptOf(f).length+' 句',toggleAll:()=>this.setState(p=>({fileOpen:{...p.fileOpen,[f.name+'#all']:!p.fileOpen[f.name+'#all']}})),remove:()=>{const idx=S.files.indexOf(f);this.setState(p=>({files:p.files.filter(x=>x!==f)}));this.toast('已移除「'+f.name+'」',{label:'撤销',fn:()=>{this.setState(p=>{const fs=p.files.slice();fs.splice(Math.min(idx,fs.length),0,f);return {files:fs,toast:null,toastAct:null};});}});},adv:!!S.fileAdv[f.name],advLabel:S.fileAdv[f.name]?'收起':'备注 / 只用片段 ▾',toggleAdv:()=>this.setState(p=>({fileAdv:{...p.fileAdv,[f.name]:!p.fileAdv[f.name]}}))}));
    const badCount=S.files.filter(f=>f.bad).length,staleCount=S.files.filter(f=>f.stale&&!f.bad).length,validCount=S.files.length-badCount-staleCount,step2Ok=validCount>=1&&badCount===0&&staleCount===0&&(!isOriginal||(quoteUnits.length>=1&&(unmatched.length===0||qmAllowed)));
    const validFiles=S.files.filter(f=>!f.bad&&!f.stale),totalMb=validFiles.reduce((a,f)=>a+f.mb,0),totalSec=validFiles.reduce((a,f)=>a+effSec(f),0);
    const totalGb=(totalMb/1024).toFixed(1);
    const capPct=Math.round(Math.min(1,Math.max(totalMb/(LIMITS.totalGb*1024),totalSec/(LIMITS.totalMin*60)))*100);
    const canNext=S.step===1?step1Ok:step2Ok;
    const chip=(items,key)=>items.map(([v,label])=>{const on=S.prefs[key]===v;return {label,border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState(p=>({prefs:{...p.prefs,[key]:v}}))};});
    const toggleDefs=TOGGLES.filter(tg=>!(tg.danger&&isOriginal)).concat(isVoice?[]:[{k:'lower_third',label:'人名条',devName:'lower_third · 首次出现 2.5s · 同一人隔 ≥ 60s 重显 · 未命名显示“受访者” · 需新增后端',note:'说话人首次出现时显示姓名和身份',speakers:true}]);
    const toggles=toggleDefs.map(tg=>{const on=!!S.prefs[tg.k];const mn=(TOGGLE_NOTE[mode]||{})[tg.k];const note=[tg.note,mn].filter(Boolean).join(' · ');return {label:tg.label,devName:tg.devName,on,track:on?TEAL:LINE,knob:on?'18px':'0px',border:tg.danger?RED:(on?TEAL:LINE),borderStyle:tg.danger?'dashed':'solid',bg:tg.danger?'#fbeee6':(on?'#e7f3ea':'#fff'),showNote:!!note,note,noteColor:tg.danger?RED:MUTED,showMood:tg.k==='background_music'&&on,showSpeakers:!!tg.speakers&&on,flip:()=>{if(tg.danger&&!S.prefs[tg.k])this.toast('已开启：拍不到的句子可能用 AI 示意画面，成片会标注');this.setState(p=>({prefs:{...p.prefs,[tg.k]:!p.prefs[tg.k]}}));}};});
    const brollNow=brollOf(validFiles0);const jumpChips=JUMP_OPTS.map(([v,label])=>{const disabled=v==='broll'&&!brollNow.length;const cur=disabled&&S.prefs.jump_cut_cover==='broll'?'zoom':(S.prefs.jump_cut_cover||'broll');const on=cur===v;return {label:disabled?label+'（没有空镜可用）':label,disabled,cursor:disabled?'not-allowed':'pointer',border:disabled?LINE:on?INK:LINE,bg:on?INK:disabled?'#f4ecdf':'#fff',fg:on?'#fff':disabled?MUTED:INK,pick:()=>{if(disabled){this.toast('没有空镜可用，回去多传几段没人说话的画面');return;}this.setState(p=>({prefs:{...p.prefs,jump_cut_cover:v}}));}};});
    const quoteCapChips=QCAP_OPTS.map(([v,label])=>{const on=(S.prefs.quote_caption||'spoken')===v;return {label,border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState(p=>({prefs:{...p.prefs,quote_caption:v}}))};});
    const canStart=step1Ok&&step2Ok&&!maintenance&&(!isOriginal||quoteUnits.length>=1);
    // processing
    const stView=s=>{const run=s.status==='running',done=s.status==='done',fail=s.status==='failed';return {icon:done?'✓':fail?'✕':run?'◠':'',iconBg:done?TEAL:fail?RED:run?INK:'#fff',iconFg:done||fail||run?'#fff':MUTED,iconBorder:done?TEAL:fail?RED:run?INK:LINE,anim:run?'gm-spin 1s linear infinite':'none',rowBg:run?'#f4ecdf':'transparent',weight:run||fail?800:600,color:fail?RED:(s.status==='pending'?MUTED:INK)};};
    const pStages=t?t.stages.map(s=>({...stView(s),icon:stView(s).icon||String(s.n),label:stageDef(s.n,mode).label,devName:STAGES[s.n-1].devName,msg:s.msg||'',elapsed:s.status==='done'?fmtEl(s.elapsed):''})):[];
    const curStage=stageDef(t?(t.current||1):1,mode);
    const pFailed=!!t&&t.status==='failed',pBusy=!!t&&(t.status==='queued'||t.status==='running'),pDone=!!t&&t.status==='done',pQueued=!!t&&t.status==='queued',pUploading=!!t&&t.status==='uploading',upPct=t&&t.upTotal?Math.round((t.upDone||0)/t.upTotal*100):0;
    const plan=t&&t.plan,stepN=plan?plan.steps.length:1,stepI=plan?plan.idx:0;
    // result rows (viewing version?)
    const version=t&&S.viewRev!=null?t.versions.find(v=>v.rev===S.viewRev):null;
    const rows=version?version.rows:(t?t.rows:[]);const total=rows.reduce((a,r)=>a+r.d,0);
    let acc=0;const starts=rows.map(r=>{const s=acc;acc+=r.d;return s;});
    const sel=rows.find(r=>r.id===S.selectedId)||rows[0]||null;const selI=sel?rows.indexOf(sel):0;
    const isOwner=!!t&&!isSample(t);
    const canEdit=!!t&&pDone&&!version&&isOwner;
    const isQ=r=>!!r&&r.kind==='quote';const spkOf=k=>({...(((t&&t.speakers)||{})[k]||{}),...((S.pendSpeakers||{})[k]||{})});const spkName=k=>spkOf(k).name||'受访者';
    const cells=rows.map((r,i)=>{const low=r.c<0.5&&!r.fallback&&!isQ(r),isSel=sel&&r.id===sel.id,del=S.deleted.includes(r.id);const dot=(r.fallback||r.missing)?[RED,RED]:r.gen?[INK,INK]:(r.sync||r.mine)?[TEAL,TEAL]:low?['transparent','#fff']:null;const tag=del?'删':S.pendReplace[r.id]?'待换':S.pendEdit[r.id]?'改字':S.pendVoice[r.id]?'录音':S.pendTrim[r.id]?'剪短':S.pendTake[r.id]?'换段':S.pendToNarration.includes(r.id)?'转旁白':r.missing?'没找到':r.fallback?'凑数':r.gen?'AI 画的':'';const jumpShadow=r.jump?'-5px 0 0 0 '+GOLD:'';return {id:r.id,idx:String(i+1).padStart(2,'0'),short:S.pendEdit[r.id]||r.s,flex:Math.max(1.4,r.d),thumb:thumb(r.beats[0].shot),border:isSel?INK:((r.fallback||r.missing)?RED:'#fff'),shadow:[jumpShadow,isSel?'0 0 0 3px '+GOLD:''].filter(Boolean).join(', ')||'none',opacity:del?'.55':'1',hasDot:!!dot,dotBg:dot?dot[0]:'',dotBorder:dot?dot[1]:'',hasTag:!!tag,tag,tagBg:del||r.missing||(r.fallback&&!S.pendReplace[r.id]&&!S.pendEdit[r.id])?RED:INK,select:()=>this.setState({selectedId:r.id,time:starts[i],editing:false,trim:null})};});
    const overview=rows.map(r=>{const isSel=sel&&r.id===sel.id;return {flex:Math.max(1,r.d),bg:r.fallback?RED:r.gen?INK:(r.sync||r.mine)?TEAL:(r.c<0.5?'#fdf2d8':LINE),ring:isSel?'0 0 0 2px '+GOLD:'none'};});
    const selLow=!!sel&&sel.c<0.5&&!sel.fallback;
    const badges=[];if(sel){if(isQ(sel))badges.push({label:'原声 · '+spkName(sel.spk),bg:TEAL,fg:'#fff',border:TEAL});else if(sel.mine)badges.push({label:(isVoice?'':'旁白 · ')+'我的配音',bg:TEAL,fg:'#fff',border:TEAL});else if(sel.sync)badges.push({label:'现场原声',bg:TEAL,fg:'#fff',border:TEAL});else badges.push({label:(isVoice?'':'旁白 · ')+'AI 配音',bg:'#fff',fg:MUTED,border:LINE});if(sel.missing)badges.push({label:'没找到原话',bg:RED,fg:'#fff',border:RED});if(sel.trimmed)badges.push({label:'已剪短',bg:'#e7f3ea',fg:TEAL,border:TEAL});if(sel.took)badges.push({label:'已换段',bg:'#e7f3ea',fg:TEAL,border:TEAL});if(sel.toNarr)badges.push({label:'改成了旁白',bg:'#e7f3ea',fg:TEAL,border:TEAL});if(isQ(sel)&&sel.jump)badges.push({label:'跳切 · '+(sel.cover==='broll'?'空镜遮盖':sel.cover==='zoom'?'轻微推近':'硬切'),bg:'#fff',fg:INK,border:GOLD});if(sel.fallback)badges.push({label:'凑数画面',bg:RED,fg:'#fff',border:RED});if(sel.gen)badges.push({label:'AI 生成示意画面',bg:INK,fg:'#fff',border:INK});if(selLow)badges.push({label:'不太确定',bg:'#fff',fg:INK,border:INK});if(sel.overlay)badges.push({label:sel.overlay+'用文字显示',bg:'#fff',fg:MUTED,border:LINE});if(sel.replaced)badges.push({label:'已换画面',bg:'#e7f3ea',fg:TEAL,border:TEAL});if(sel.edited)badges.push({label:'已改字',bg:'#e7f3ea',fg:TEAL,border:TEAL});}
    const {checks,win:cpmWin}=this.checksFor(t,rows);const overlays=rows.filter(r=>r.overlay).length;
    const cpmSel=sel?(sel.mine?sel.myCpm:cpmOf(sel)):0;const cpmWord=c=>c<cpmWin[0]?'偏慢':c>cpmWin[1]?'偏快':'正合适';
    const quoteNote=!isQ(sel)?'':sel.missing?'这句是原话，但素材里没找到。'+(isMixed?'改成旁白、':'')+'删掉，或回去传含这句话的素材。':'用的是'+spkName(sel.spk)+'的原声，来自「'+sel.file+'」'+fmtT(sel.t0)+'–'+fmtT(sel.t1)+'。字幕按实际说的话显示。'+(sel.c<0.85?' 这句对上的原话不太像，听一下。':'')+(sel.jump?' 与上一句之间是跳切 · '+(sel.cover==='broll'?'已用空镜「'+(SHOT[sel.coverShot]||'').slice(0,12)+'」遮盖':sel.cover==='zoom'?'用轻微推近遮盖':'直接硬切，画面会跳一下'):'');
    const selNote=!sel?'':isQ(sel)?quoteNote:sel.fallback?'没找到合适的画面，先用了别的镜头。换个画面，或者删掉这句。':sel.gen?'这句没拍到，AI 画了一张示意画面，片子里已经标注。新闻里它只能当示意，不能当现场。':sel.mine?'这是你自己读的：'+cpmSel+' 字/分，'+cpmWord(cpmSel)+'。':sel.sync?'用的是现场原声：“'+sel.spoken+'”':sel.replaced?'已按你的话换过画面：“'+sel.replaced+'”':selLow?'AI 不太确定这个画面对不对，看一眼。':sel.overlay?sel.overlay+'会用文字叠在画面上，这是电视新闻的常规做法。':'画面和这句话对得上。';
    const selBeats=sel?sel.beats.map(b=>({thumb:thumb(b.shot),desc:isQ(sel)?(sel.missing?'没找到这句原话，暂用环境画面':spkName(sel.spk)+' · 说话人镜头 · '+sel.d.toFixed(1)+' 秒'):(SHOT[b.shot]||'')+' · '+shotSec(b.shot).toFixed(1)+' 秒',showText:!!b.t,text:b.t||'',shot:b.shot,conf:Math.round(sel.c*100)+'%'})).concat(isQ(sel)&&sel.coverShot!=null?[{thumb:thumb(sel.coverShot),desc:'跳切遮盖 · 空镜「'+(SHOT[sel.coverShot]||'')+'」约 2 秒',showText:true,text:'空镜',shot:sel.coverShot,conf:''}]:[]):[];
    // 原声句面板：剪短（按词选段）/ 换一段 / 改成旁白 / 说话人
    const tr=S.trim&&sel&&S.trim.rowId===sel.id?S.trim:null;const q0=sel?sel.t0||0:0,q1=sel?sel.t1||0:0,ts=tr?tr.start:q0,te=tr?tr.end:q1,qspan=Math.max(0.1,q1-q0);
    const words=isQ(sel)&&!sel.missing?wordsOf(sel.spoken):[];const totalChars=words.join('').length||1;let accC=0;
    const trimWords=words.map(w=>{const a=q0+(accC/totalChars)*qspan;accC+=w.length;const b=q0+(accC/totalChars)*qspan;const on=b>ts+0.05&&a<te-0.05;return {text:w,bg:on?TEAL:'transparent',fg:on?'#fff':MUTED,deco:on?'none':'line-through',pick:()=>{if(!canEdit)return;this.setState(p=>{const cur=p.trim&&p.trim.rowId===sel.id?p.trim:null;if(!cur)return {trim:{rowId:sel.id,start:+a.toFixed(2),end:q1}};if(a<cur.start-0.01)return {trim:{...cur,start:+a.toFixed(2)}};return {trim:{...cur,end:+b.toFixed(2)}};});}};});
    const trimOk=!!tr&&(te-ts)>=1&&(ts>q0+0.05||te<q1-0.05);
    const altTakes=isQ(sel)?(sel.alts||[]).map(a=>{const on=!!S.pendTake[sel.id]&&S.pendTake[sel.id].file===a.file&&S.pendTake[sel.id].t0===a.t0;return {text:(a.spoken||'').slice(0,30)+((a.spoken||'').length>30?'…':''),meta:a.file+' · '+fmtT(a.t0)+'–'+fmtT(a.t1)+' · 相似度 '+Math.round(a.score*100)+'%'+(a.snr<18?' · 有杂音':''),dur:(a.t1-a.t0).toFixed(0)+'s',border:on?INK:LINE,bg:on?'#f4ecdf':'#fff',pick:()=>{if(!canEdit)return;this.setState(p=>{const pt={...p.pendTake};if(on)delete pt[sel.id];else pt[sel.id]=a;return {pendTake:pt};});}};}):[];
    const selSpk=isQ(sel)?spkOf(sel.spk):{};
    const usedShots=new Set(rows.flatMap(x=>x.beats.map(b=>b.shot)));
    const takenIns=new Set(Object.entries(S.pendReplace).filter(([id])=>!sel||+id!==sel.id).map(([,ins])=>ins));
    const candPool=POOL.filter(p=>!usedShots.has(p)&&!takenIns.has(SHOT[p]));const poolLeft=candPool.length;
    const libAll=Object.keys(SHOT).map(Number).filter(p=>!usedShots.has(p)&&!takenIns.has(SHOT[p]));const libQ=S.instruction.trim().toLowerCase();const qTerms=libQ?libQ.split(/[\s，,、]+/).filter(Boolean):[];const libHits=qTerms.length?libAll.filter(p=>qTerms.some(w=>SHOT[p].toLowerCase().includes(w))):[];const candList=(libHits.length?libHits:libAll.filter(p=>POOL.includes(p)).concat(libAll.filter(p=>!POOL.includes(p)))).slice(0,8);
    const candAll=candList.map(p=>{const on=S.instruction===SHOT[p]||(!!sel&&S.pendReplace[sel.id]===SHOT[p]);return {thumb:thumb(p),desc:SHOT[p]+' · '+shotSec(p).toFixed(0)+' 秒',border:on?INK:LINE,bg:on?'#f4ecdf':'#fff',use:()=>this.setState({instruction:SHOT[p]})};}),candShots=S.candMore||candAll.length<=4?candAll:candAll.slice(0,3),candHidden=candAll.length-candShots.length;
    const candTitle=qTerms.length?(libHits.length?'素材库里找到 '+libHits.length+' 个包含“'+libQ+'”的镜头，点它就填好：':'素材库里没有包含“'+libQ+'”的镜头，可以直接让 AI 按这句话找，或从没用过的镜头里挑：'):'从没用过的 '+libAll.length+' 个镜头里挑一个（在上面输入关键词可以搜）：';
    const pendCount=this.pendingCount();
    // checklist：见 checksFor，与后端 quality.py 规则同源
    const isDone=ck=>ck.auto?ck.done:!!S.checked[ck.k];
    const goRow=(id,edit)=>{const k=rows.findIndex(r=>r.id===id);const row=rows[k];this.setState({selectedId:id,time:starts[k]||0,editing:!!edit&&canEdit,editText:edit&&row?(S.pendEdit[id]||row.s):S.editText});this.scrollToBoard();};
    const checkItems=checks.map(ck=>{const done=isDone(ck);const blocked=ck.auto;return {done,notDone:!done,text:ck.text,code:ck.code,mark:done?'✓':(blocked?'!':''),boxBg:done?TEAL:blocked?RED:'#fff',boxFg:'#fff',boxBorder:done?TEAL:blocked?RED:INK,cursor:(blocked||!isOwner)?'default':'pointer',deco:done?'line-through':'none',color:done?MUTED:INK,bg:ck.sev===0&&!done?'#fbeee6':'#fff',border:ck.sev===0&&!done?RED:LINE,hasGo:!done&&ck.id!=null,goLabel:ck.goLabel,go:()=>{if(ck.pacing&&canEdit){this.setState({pendPacing:ck.pacing});this.toast('已记下：用“'+(ck.pacing==='slow'?'慢一点':'快一点')+'”重新配音，点右下角应用');return;}goRow(ck.id,ck.edit);if(ck.speaker)this.toast('在右侧“说话人”处填上姓名和身份');},toggle:()=>{if(blocked||!isOwner)return;this.setState(p=>({checked:{...p.checked,[ck.k]:!p.checked[ck.k]}}));}};});
    const doneCount=checks.filter(isDone).length;
    const blocking=checks.filter(ck=>ck.sev===0&&!isDone(ck)).length;
    const openCount=checks.filter(ck=>ck.sev<2&&!isDone(ck)).length;
    const passed=!!t&&pDone&&checks.every(ck=>ck.sev===2||isDone(ck));
    const gate=passed?['检查通过 ✓',TEAL,'#e7f3ea',TEAL]:blocking?['待修改 · '+blocking+' 处要处理',RED,'#fbeee6',RED]:['待确认 · 还有 '+openCount+' 项',INK,'#fff',INK];
    const firstBlock=checks.find(ck=>ck.sev===0&&!isDone(ck));const firstOpen=checks.find(ck=>!isDone(ck)&&ck.sev<2);
    let nextAction='',nextBtn='',nextGo=null;
    if(t){if(version){const vi=t.versions.indexOf(version);nextAction='正在看'+(vi?'第 '+vi+' 次修改':'初版')+'的结果。想回到这版就点“恢复这个版本”。';}
      else if(t.id==='t-sample'){nextAction='这是一条范例：点故事板看每句配了什么画面，右侧可以看每句的检查结果。';}
      else if(pendCount){nextAction='你记下了 '+pendCount+' 处修改，点右下角一起应用。';nextBtn='应用修改';nextGo=()=>this.applyPending();}
      else if(firstBlock){const k=rows.findIndex(r=>r.id===firstBlock.id);nextAction=firstBlock.auto?'先处理第 '+(k+1)+' 句：换个画面，或者删掉它。':firstBlock.text;nextBtn='去第 '+(k+1)+' 句';nextGo=()=>goRow(firstBlock.id);}
      else if(firstOpen){const k=rows.findIndex(r=>r.id===firstOpen.id);const go=()=>goRow(firstOpen.id,firstOpen.edit);if(firstOpen.pacing!==undefined||firstOpen.edit){nextAction=firstOpen.text+'；觉得现在这样也行，就在清单里打勾。';nextBtn=firstOpen.edit?'去改第 '+(k+1)+' 句':firstOpen.pacing?'用“'+(firstOpen.pacing==='slow'?'慢一点':'快一点')+'”重配':'去第 '+(k+1)+' 句';nextGo=firstOpen.pacing&&canEdit?()=>{this.setState({pendPacing:firstOpen.pacing});this.toast('已记下，点右下角一起应用');}:go;}else{nextAction='看一眼第 '+(k+1)+' 句，没问题就在清单里打勾。';nextBtn='去第 '+(k+1)+' 句';nextGo=go;}}
      else if(passed&&isOwner){nextAction='检查都通过了！可以下载成片，也可以继续修改。';nextBtn='下载';nextGo=()=>this.toast('开始下载 MP4（原型演示）');}
      else nextAction='看看故事板，每一格是一句话和它的画面。';}
    const reflection=t?(()=>{const fb=rows.filter(r=>r.fallback).length,low=rows.filter(r=>r.c<0.5&&!r.fallback&&!isQ(r)).length,gen=rows.filter(r=>r.gen).length,mine=rows.filter(r=>r.mine).length,qn=rows.filter(isQ).length,noisy=rows.filter(r=>isQ(r)&&r.snr<18).length,jumps=rows.filter(r=>r.jump).length;const parts=[];if(qn)parts.push('用了 '+qn+' 段原声'+(noisy?'，其中 '+noisy+' 段有杂音——采访时离对方近一点、避开风口':'，采访收音都很干净'));if(jumps)parts.push(jumps+' 处跳切靠空镜遮盖——多拍没人说话的画面，剪的时候更从容');if(fb)parts.push('有 '+fb+' 句太抽象，没拍到能表现它的画面——下次写总结句时，想想“这句配什么画面”');if(low)parts.push(low+' 句画面不太确定，多半是素材里缺这个东西的特写——采访时多拍几个近景');if(gen)parts.push('用了 '+gen+' 张 AI 示意画面，记住新闻里它只能当示意');if(mine)parts.push('你自己配了 '+mine+' 句音，语速'+cpmWord(Math.round(rows.filter(r=>r.mine).reduce((a,r)=>a+r.myCpm,0)/mine)));if(!parts.length)parts.push('每句都配到了画面，下次试试自己配音，让作品更像你的');return parts.join('；')+'。';})():'';
    const narrRows=rows.filter(r=>!isQ(r)),narrTotal=narrRows.reduce((a,r)=>a+r.d,0);
    const metrics=t&&pDone?[rows.length+' 句 · '+rows.reduce((a,r)=>a+r.beats.length,0)+' 个画面',usedShots.size+' 个不同镜头','不确定 '+rows.filter(r=>r.c<0.5&&!r.fallback&&!isQ(r)).length+' · 凑数画面 '+rows.filter(r=>r.fallback).length,'文字叠层 '+overlays+' · AI 生成 '+rows.filter(r=>r.gen).length,'时长 '+fmtTime(total)+(isOriginal?'':' · 语速 '+Math.round(narrRows.reduce((a,r)=>a+chars(r.s),0)/Math.max(1,narrTotal)*60)+' 字/分'),'响度 −20.1 LUFS · 峰值 −3.7 dB'].concat(isVoice?[]:['原声 '+rows.filter(isQ).length+' 段 · 跳切 '+rows.filter(r=>r.jump).length+' 处 · 说话人 '+new Set(rows.filter(isQ).map(r=>r.spk)).size+' 位']):[];
    const posterI=starts.findIndex((s,k)=>S.time>=s&&S.time<s+rows[k].d);const poster=rows[posterI>=0?posterI:selI]||sel;
    // history
    const stMeta=x=>{if(x.gone)return ['已清理','#fff',MUTED,LINE];if(x.status==='done'){const g=this.gateOf(x);return g.blocking?['待修改 '+g.blocking+' 处','#fff',RED,RED]:g.open?['待确认 '+g.open+' 项','#fff',INK,INK]:['检查通过','#fff',TEAL,TEAL];}if(x.status==='failed')return ['没做成','#fff',RED,RED];if(x.status==='uploading')return ['上传中 '+Math.round((x.upDone||0)/(x.upTotal||1)*100)+'%',INK,'#fff',INK];if(x.status==='queued')return [x.queue?'排队 · 前面 '+x.queue+' 个':'排队中','#fff',MUTED,LINE];return ['制作中 '+(x.current||1)+'/10',INK,'#fff',INK];};
    const myIds=S.history.filter(id=>S.tasks[id]&&!isSample(S.tasks[id])).sort((a,b)=>S.histSort==='title'?(S.tasks[a].title||'').localeCompare(S.tasks[b].title||'','zh'):0);
    const histItems=myIds.map(id=>{const x=S.tasks[id];const [status,bg,fg,border]=stMeta(x);const active=id===S.currentId;const lf=x.gone?null:leftOf(x);return {title:x.title,mode:MODE_NAME(x.mode),status,statusBg:bg,statusFg:fg,statusBorder:border,rev:x.versions&&x.versions.length>1?'改了 '+(x.versions.length-1)+' 次':'',date:dateOf(x),left:lf?lf.text:'',leftFg:lf&&lf.urgent?RED:MUTED,leftW:lf&&lf.urgent?800:400,border:active?INK:LINE,shadow:active?'3px 3px 0 '+INK:'none',open:()=>this.openTask(id)};});
    const verLabel=x=>{if(!x)return '初版';const n=x.versions?x.versions.length:0;if(x.status==='done')return n>1?'改了 '+(n-1)+' 次':'初版';return n>=1?'第 '+n+' 次修改中':'初版';};
    const ask=(title,body,ok,fn,input,okBg)=>()=>this.setState({confirm:{title,body,ok,fn,input:!!input,text:'',okBg}});
    const pendParts=[];if(S.deleted.length)pendParts.push('删 '+S.deleted.length+' 句');if(Object.keys(S.pendReplace).length)pendParts.push('换 '+Object.keys(S.pendReplace).length+' 句画面');if(Object.keys(S.pendEdit).length)pendParts.push('改 '+Object.keys(S.pendEdit).length+' 句字');if(Object.keys(S.pendVoice).length)pendParts.push('录 '+Object.keys(S.pendVoice).length+' 句音');if(S.pendPacing)pendParts.push('语速'+(S.pendPacing==='slow'?'慢一点':'快一点'));if(Object.keys(S.pendTrim).length)pendParts.push('剪短 '+Object.keys(S.pendTrim).length+' 句');if(Object.keys(S.pendTake).length)pendParts.push('换 '+Object.keys(S.pendTake).length+' 段原声');if(S.pendToNarration.length)pendParts.push(S.pendToNarration.length+' 句改成旁白');if(Object.keys(S.pendSpeakers).length)pendParts.push('改说话人名字');
    const stepNames=[];if(S.deleted.length||Object.keys(S.pendEdit).length||Object.keys(S.pendVoice).length||S.pendPacing||Object.keys(S.pendTrim).length||Object.keys(S.pendTake).length||S.pendToNarration.length||Object.keys(S.pendSpeakers).length)stepNames.push('重做');Object.keys(S.pendReplace).forEach(id=>{const i=rows.findIndex(r=>r.id===+id);if(i>=0)stepNames.push('换第 '+(i+1)+' 句');});
    const rec=S.recording;
    const narrSec=Math.max(1,isVoice?Math.round(chars(script)/target*60):narrSecAll),estShots=validFiles.reduce((a,f)=>a+((f.img||f.speech)?1:Math.max(1,Math.round(effSec(f)/12))),0);
    const noCount=sentRaw.filter(s=>S.sentMarks[s]==='no').length,yesCount=sentRaw.filter(s=>S.sentMarks[s]==='yes').length,tooMuch=totalSec>15*60||estShots>LIMITS.maxShots;
    const suffRatio=Math.min(1,Math.min(estShots/Math.max(1,needShots),totalSec/(narrSec*1.5)));const suffLevel=!validFiles.length?0:tooMuch?3:suffRatio>=1?2:suffRatio>=.6?1:0;
    const suffLabel=['不够','勉强够','充足','太多了'][suffLevel],suffColor=[RED,INK,TEAL,'#8a5a0b'][suffLevel];
    const suffDetail='保守估计约 '+estShots+' 个镜头 vs 需要约 '+needShots+' 个（'+(isMixed?'旁白 ':'')+sentenceEst+' 句'+(listUnits?'，其中 '+listUnits+' 句列举了几样东西':'')+'）· 素材 '+fmtSec(totalSec)+' vs 成片约 '+fmtSec(narrSec)+(isMixed?' · 原声 '+quoteUnits.length+' 段 ≈ '+Math.round(quoteSec)+' 秒，不需要空镜':'');
    const suffAdvice=!validFiles.length?'':(tooMuch?'素材太多了：'+fmtSec(totalSec)+'。超过 15 分钟会切出上百个镜头，AI 看画面要很久，还可能超过 120 个镜头的上限——每个场景只留最好的 10–20 秒。':estShots>=needShots?'稿子约 '+sentenceEst+' 句，素材保守估计有 '+estShots+' 个镜头，够每句配一个不同的画面。':'每句话要一个不同的镜头：需要约 '+needShots+' 个，素材保守估计只有 '+estShots+' 个——再拍 '+(needShots-estShots)+' 个不同场景，或删 '+(needShots-estShots)+' 句。不然做到一半会失败，还会用掉一个名额。')+(noCount?' 你说有 '+noCount+' 句没拍到——回去改稿或补拍，不然会变成凑数画面。':'');
    const showWatermark=!!t&&pDone&&!passed;
    const selFail=t&&sel&&t.replaceFails?t.replaceFails[sel.id]||null:null;
    const delSel=()=>{if(sel)this.setState(p=>({deleted:p.deleted.includes(sel.id)?p.deleted:[...p.deleted,sel.id]}));};const editSel=()=>{if(sel)this.setState({editing:true,editText:S.pendEdit[sel.id]||sel.s});};
    const failFixes=!selFail?[]:selFail.kind==='abstract'?[{label:'从下面的候选镜头里挑一个',go:()=>this.setState({instruction:''})},{label:'✎ 改具体：写人们在做什么',go:editSel},{label:'删掉这句',go:delSel}]:selFail.kind==='used'?[{label:'删掉这句',go:delSel},{label:'✎ 并进上一句',go:editSel}]:[{label:'删掉这句',go:delSel},{label:'✎ 改成现有画面能表现的说法',go:editSel}];
    const doneDur=Object.values(S.tasks).filter(x=>x.doneAt&&x.startedAt&&x.doneAt>x.startedAt).map(x=>x.doneAt-x.startedAt);const avgMs=doneDur.length?doneDur.reduce((a,b)=>a+b,0)/doneDur.length:5*60e3;
    const left=t&&!t.gone?leftOf(t):null;
    const pFailActions=!pFailed?[]:t.errorKind==='quote_missing'?[{label:'回去改第 '+(t.badRows||[]).join('、')+' 句',bg:INK,fg:'#fff',go:()=>this.restoreDraft(1)},{label:'回去多传素材',bg:'#fff',fg:INK,go:()=>this.restoreDraft(2)}]:t.errorKind==='qc'?[{label:'回去改第 '+(t.badRows||[]).join('、')+' 句',bg:INK,fg:'#fff',go:()=>this.restoreDraft(1)},{label:'回去多传素材',bg:'#fff',fg:INK,go:()=>this.restoreDraft(2)}]:t.errorKind==='shortage'?[{label:'回去删掉 '+t.shortBy+' 句',bg:INK,fg:'#fff',go:()=>this.restoreDraft(1)},{label:'回去多传素材',bg:'#fff',fg:INK,go:()=>this.restoreDraft(2)}]:[{label:'再试一次（重新提交）',bg:INK,fg:'#fff',go:()=>this.retrySame()},{label:'回去改稿子和素材',bg:'#fff',fg:INK,go:()=>this.restoreDraft(1)}];
    const gateNote='quality_gate_mode：后端枚举 warn|block。本原型提议 warn（阻断项在结果页处理），Tweaks gateMode=block 可看现有后端行为（有阻断项即制作失败、无成片）。';
    const sampleScript=sampleScriptFor(mode,scn);
    return {
      dev,
      demo,
      maxFiles:LIMITS.maxFiles,
      maxTotalGb:LIMITS.totalGb,
      totalDur:fmtSec(totalSec),
      bigFont:S.bigFont,
      toggleFont:()=>this.setState(p=>({bigFont:!p.bigFont})),
      fontBtnLabel:S.bigFont?'大字 ✓':'大字',
      fontBtnBg:S.bigFont?GOLD:'transparent',
      fontBtnFg:S.bigFont?INK:'#fff',
      cpmRange:cpmWin.join('–')+' 字/分',
      charSoft:CHAR_SOFT,
      modeCode:mode,
      isMixed,isOriginal,isVoice,
      modeCards:MODES.filter(([v])=>S.modeMore||S.mode!=='voiceover'||v==='voiceover').map(([v,name,desc,fit,icon])=>{const on=S.mode===v;return {name,desc,fit,icon,on,isA:v==='voiceover',isB:v==='mixed',isC:v==='original',tag:v==='voiceover'?'最简单，先用这个':'',border:on?INK:LINE,bg:on?'#f4ecdf':'#fff',pick:()=>this.setMode(v)};}),
      modeMoreLabel:(!S.modeMore&&S.mode==='voiceover')?'更多制作方式：旁白 + 原声 / 只用原声 ▾':(S.mode==='voiceover'?'收起 ▴':'三种方式'),toggleModeMore:()=>this.setState(p=>({modeMore:!p.modeMore})),
      candMore:candHidden>0,candMoreLabel:'还有 '+candHidden+' 个镜头 ▾',toggleCandMore:()=>this.setState(p=>({candMore:!p.candMore})),
      toastHasAct:!!S.toastAct,toastActLabel:S.toastAct?S.toastAct.label:'',toastActFn:()=>{if(S.toastAct)S.toastAct.fn();},
      showIntro:!S.introDismissed&&S.screen==='create'&&S.step===1,dismissIntro:()=>{this.setState({introDismissed:true});},showHelp:()=>this.setState({introDismissed:false,screen:'create',step:1,drawer:false}),
      step1Sub:isMixed?'第一行是标题；人说的话写成「同期 姓名（身份）：…」。':isOriginal?'第一行是标题；下面每行一句原话。':'第一行是标题。',
      scriptPlaceholder:isMixed?'把稿子粘贴到这里；受访者的话写成「同期 王红（市集主办方）：…」':isOriginal?'把从采访里挑出来的原话贴到这里，一句一行。还没有稿？先去第 2 步传素材，转写完回来挑句子':'把新闻稿粘贴到这里……',
      hasQuoteLong:quoteLong.length>0,
      quoteLongText:'有 '+quoteLong.length+' 句原声超过 60 字，观众会走神——在稿子里断成两句',
      hasNewWords:newWordChars.length>0,
      newWordsText:'这几个字素材里没说过，剪不出来：'+newWordChars.slice(0,8).join('、')+(newWordChars.length>8?'…':''),
      sentRows,
      sentListTitle:isMixed?'句子清单 · 旁白 '+units.length+' 句 · 原声 '+quoteUnits.length+' 句':isOriginal?'原话清单 · 对上 '+(qMatches.length-unmatched.length)+' / '+quoteUnits.length+' 句':'拍摄清单 · '+shotList.length+' 条',
      sentListSub:isMixed?'点右侧标签可以改类型；原声句会从素材里剪出原话':isOriginal?'每句都要是素材里真说过的话；红线标出的字素材里没有':'写完稿先看这个再去拍，拍到了就不会有凑数画面',
      sentStats:isVoice?'':'预计成片 '+fmtTime(estSec)+(isMixed?' · 旁白约 '+fmtSec(narrSecAll)+' + 原声约 '+Math.round(quoteSec)+' 秒':' · 跳切约 '+estJumps+' 处'),
      hasPick:isOriginal&&pickGroups.length>0,
      pickGroups,
      pickOpen:!!S.pickOpen,
      pickLabel:S.pickOpen?'收起':'展开',
      pickSummary:'已挑 '+pickedN+' 句',
      togglePick:()=>this.setState(p=>({pickOpen:!p.pickOpen})),
      goPick:()=>this.setState({step:1,pickOpen:true}),
      hasSuggest:!!suggest&&S.step<3,
      suggestText:suggest?suggest.text:'',
      suggestYesLabel:suggest?suggest.yes:'',
      suggestNoLabel:suggest?suggest.no:'',
      suggestYes:()=>{if(suggest)suggest.go();},
      suggestNo:()=>{if(suggest)suggest.off();},
      step2Sub:isMixed?'旁白配空镜，原声从说话的片段里剪。':isOriginal?'传含采访的视频；AI 会先把话转成文字。':'AI 会给每句话挑一个不同的镜头。',
      uploadNote:isVoice?'mp4 · mov · avi · mkv · 照片 jpg / png（定格 3 秒）':'mp4 · mov · avi · mkv · 照片 jpg / png · 采访视频会自动转文字',
      aHasSpeech:isVoice&&validFiles0.some(f=>f.speech),
      switchMixed:()=>this.setMode('mixed'),
      suffDisplay:isOriginal?'none':'block',
      hasMatchCard:!isVoice&&(quoteUnits.length>0||isOriginal)&&validFiles0.length>0,
      matchColor:unmatched.length?RED:unsureQ.length?INK:TEAL,
      matchTitle:isMixed?'原话都拍到了吗？':'原话对上了吗？',
      matchSummary:!quoteUnits.length?'还没有原话——从转写里挑几句，或在稿子里写「同期 …：」':'· '+(isMixed?'对上 '+(qMatches.length-unmatched.length)+' / 原声句 '+quoteUnits.length:'原声覆盖率 '+(qMatches.length-unmatched.length)+' / '+quoteUnits.length+' 句')+(!asrFiles.length?' · 正在听素材…':''),
      matchRows:qMatches.map((m,i)=>{const st=m.status,b=m.best;return {idx:String(i+1).padStart(2,'0'),text:m.u.text,info:b?b.file+' · '+fmtT(b.t0)+'–'+fmtT(b.t1)+' · '+spkLabel(b.spk):(asrFiles.length?'素材里没有相近的话':'等转写…'),status:st==='ok'?'✓ 对上了':st==='unsure'?'○ 不太确定':'✕ 没找到',score:b?Math.round(b.score*100)+'%':'—',stBg:st==='ok'?'#e7f3ea':st==='unsure'?'#fdf2d8':'#fbeee6',stFg:st==='ok'?TEAL:st==='unsure'?INK:RED,border:st==='missing'?RED:LINE,bg:st==='missing'?'#fbeee6':'#fff',hasFix:st==='missing'&&asrFiles.length>0,fixes:isMixed?[{label:'改成旁白',go:()=>this.setState(p=>({typeMarks:{...p.typeMarks,[m.u.text]:'narration'}}))},{label:'删掉',go:()=>this.removeFromScript(m.u.text)}]:[{label:'删掉',go:()=>this.removeFromScript(m.u.text)},{label:'从转写里挑',go:()=>this.setState({step:1,pickOpen:true})}]};}),
      hasJumpLine:isOriginal&&quoteUnits.length>1,
      jumpLine:'预计 '+estJumps+' 处跳切，第 3 步可选遮盖方式'+(brollNow.length?'':'——现在没有空镜可用，多传几段没人说话的画面'),
      hasSpeakers:!isVoice&&speakerRows.length>0,
      speakerRows,
      step3Sub:isOriginal?'这个模式不配音，全部用现场原声。':'已经推荐好了，直接开始也行。',
      showVoice:!isOriginal,
      voiceTitle:isMixed?'旁白谁来读':'谁来配音',
      showPacing:!isOriginal,
      showJump:!isVoice,
      jumpNote:isOriginal?'相邻两段原声不连续时，画面会跳一下':'两段原声连着出现时才用得上',
      jumpChips,
      showQuoteCap:!isVoice,
      quoteCapChips,
      summaryLine:isVoice?'AI 配音 · '+units.length+' 句 · 预计 '+fmtTime(estSec):isMixed?'旁白 + 原声 · 旁白 '+units.length+' 句 / 原声 '+quoteUnits.length+' 段 · 预计 '+fmtTime(estSec)+' · 说话人 '+speakerRows.length+' 位':'只用原声 · '+quoteUnits.length+' 句 · 预计 '+fmtTime(estSec)+' · 跳切 '+estJumps+' 处',
      rMode:MODE_NAME(t?t.mode:mode),
      showLT:!!poster&&isQ(poster)&&!poster.missing&&!!t&&(!t.prefs||t.prefs.lower_third!==false),
      ltName:poster&&isQ(poster)?spkName(poster.spk):'',
      ltRole:poster&&isQ(poster)?(spkOf(poster.spk).role||''):'',
      metricsTitle:'质量数据'+(isOriginal?'':' · 语速目标 '+cpmWin.join('–')+' 字/分'),
      selIsQuote:isQ(sel),
      selIsQuoteMode:!isVoice,
      selPrev:()=>this.stepRow(-1),
      selNext:()=>this.stepRow(1),
      selIsNarr:!isQ(sel),
      trimWords,
      trimL:Math.round((ts-q0)/qspan*100),
      trimW:Math.max(2,Math.round((te-ts)/qspan*100)),
      trimRange:fmtT(ts)+' – '+fmtT(te)+' · '+(te-ts).toFixed(1)+' 秒'+(tr?'（原 '+qspan.toFixed(1)+' 秒）':''),
      trimSaveBg:trimOk?INK:LINE,
      trimStartBack:()=>{if(!tr){this.toast('先点一个词，从它开始保留');return;}this.setState({trim:{...tr,start:+Math.max(q0,tr.start-0.2).toFixed(2)}});},
      trimEndFwd:()=>{if(!tr){this.toast('先点一个词，从它开始保留');return;}this.setState({trim:{...tr,end:+Math.min(q1,tr.end+0.2).toFixed(2)}});},
      saveTrim:()=>{if(!tr||!sel)return;if(te-ts<1){this.toast('至少留 1 秒');return;}if(!trimOk){this.toast('还没剪掉任何内容');return;}this.setState(p=>({pendTrim:{...p.pendTrim,[sel.id]:{start:ts,end:te}},trim:null}));},
      cancelTrim:()=>this.setState(p=>{const pt={...p.pendTrim};if(sel)delete pt[sel.id];return {trim:null,pendTrim:pt};}),
      trimPending:!!(sel&&S.pendTrim[sel.id]),
      trimPendingText:sel&&S.pendTrim[sel.id]?'已记下：保留 '+fmtT(S.pendTrim[sel.id].start)+' – '+fmtT(S.pendTrim[sel.id].end)+'（'+(S.pendTrim[sel.id].end-S.pendTrim[sel.id].start).toFixed(1)+' 秒）':'',
      altTitle:altTakes.length?'同一句话还有 '+altTakes.length+' 种说法，点一个换过去':'这句话只说过一次',
      altTakes,
      canToNarr:isMixed&&isQ(sel)&&canEdit,
      toNarrLabel:sel&&S.pendToNarration.includes(sel.id)?'✓ 已记下：改成旁白（AI 读）':'改成旁白（AI 读）',
      toNarrBorder:sel&&S.pendToNarration.includes(sel.id)?TEAL:LINE,
      toNarrBg:sel&&S.pendToNarration.includes(sel.id)?'#e7f3ea':'#fff',
      toNarrFg:sel&&S.pendToNarration.includes(sel.id)?TEAL:INK,
      toggleToNarr:()=>{if(!sel)return;this.setState(p=>({pendToNarration:p.pendToNarration.includes(sel.id)?p.pendToNarration.filter(x=>x!==sel.id):[...p.pendToNarration,sel.id]}));},
      selSpkName:selSpk.name||'',
      selSpkRole:selSpk.role||'',
      selSpkBorder:selSpk.name?LINE:GOLD,
      selSpkHint:selSpk.name?'':'还没填名字，人名条会显示“受访者”',
      onSelSpkName:e=>{if(!sel)return;const v=e.target.value.slice(0,8);this.setState(p=>({pendSpeakers:{...p.pendSpeakers,[sel.spk]:{...(p.pendSpeakers[sel.spk]||{}),name:v}}}));},
      onSelSpkRole:e=>{if(!sel)return;const v=e.target.value.slice(0,12);this.setState(p=>({pendSpeakers:{...p.pendSpeakers,[sel.spk]:{...(p.pendSpeakers[sel.spk]||{}),role:v}}}));},
      retentionLine:'样片保留 3 天；做好后请及时下载留档。',
      statusText:maintenance?'维护中':'服务正常',
      statusColor:maintenance?GOLD:'#6aa84f',
      maintenance,
      hasSaved:!!S.savedAt,
      savedLabel:S.savedAt?clock(S.savedAt):'',
      histSortLabel:S.histSort==='title'?'按标题 ↕':'按时间 ↕',
      toggleHistSort:()=>this.setState(p=>({histSort:p.histSort==='title'?'time':'title'})),
      drawerTitle:'我的作品',
      openDrawer:set({drawerOpen:true}),
      closeDrawer:set({drawerOpen:false}),
      drawerOpen:S.drawerOpen,
      histItems,
      openSample:()=>this.openTask('t-sample'),
      newTask:()=>this.newTask(),
      goHome:()=>{const go=()=>{clearInterval(this.playTimer);this.setState({screen:'create',drawerOpen:false,playing:false,...this.resetResult()});};if(this.guard(go))return;go();},
      isCreate:S.screen==='create',
      isProcessing:S.screen==='processing',
      isResult:S.screen==='result'&&!!t,
      isGone:S.screen==='gone',
      scriptRef:this.scriptRef,
      elemHint:'在稿子里选中一个词再点要素，就标出它；没找到但你写了，直接点一下',
      hasLongSent:longUnits.length>0,
      longSentText:'有 '+longUnits.length+' 句超过 30 字——读起来会赶，画面也撑不住，拆开它',
      isStep1:S.step===1,
      isStep2:S.step===2,
      isStep3:S.step===3,
      s1:stepObj(1,step1Ok),
      s2:stepObj(2,step2Ok),
      s3:stepObj(3,true),
      goStep1:set({step:1}),
      goStep2:()=>{if(step1Ok)this.setState({step:2});else this.toast(script.trim().length<20?'先写至少 20 个字':'先把第一行改成标题');},
      goStep3:()=>{if(step1Ok&&step2Ok)this.setState({step:3});else this.toast(!step1Ok?'先写完稿子':badCount?'先删掉不能用的文件':'先传至少 1 个视频');},
      back:()=>this.setState(p=>({step:Math.max(1,p.step-1)})),
      next:()=>{if(!canNext){this.toast(S.step===1?(script.trim().length<20?'先写至少 20 个字':'先把第一行改成标题'):badCount?'先删掉不能用的文件':!validCount?'至少传 1 个视频':isOriginal&&!quoteUnits.length?'先在稿子里放至少 1 句原话（可以从转写里挑）':isOriginal&&unmatched.length?'有 '+unmatched.length+' 句在素材里没找到，改成真说过的话或删掉':'还不能继续');return;}if(S.step===2&&isMixed&&unmatched.length){this.setState({confirm:{title:'有 '+unmatched.length+' 句原话没找到',body:'第 '+unmatched.map(m=>qMatches.indexOf(m)+1).join('、')+' 句原声在素材里没对上，做到一半会失败。先改成旁白或删掉更稳妥。仍要继续吗？',ok:'仍要继续',okBg:INK,fn:()=>this.setState({step:3})}});return;}if(S.step===2&&!isOriginal&&(suffLevel===0||noCount>0)){this.setState({confirm:{title:'素材可能不够',body:(suffLevel===0?'保守估计镜头数不够每句配一个。':'')+(noCount?'你标了 '+noCount+' 句没拍到。':'')+'这样提交可能做到一半失败，还会用掉 1 次提交机会。仍要继续吗？',ok:'仍要继续',okBg:INK,fn:()=>this.setState({step:3})}});return;}this.setState(p=>({step:Math.min(3,p.step+1)}));},
      nextBg:canNext?INK:LINE,
      script,
      onScript:e=>this.setState({script:e.target.value}),
      useSample:()=>this.setState(p=>({script:sampleScript,speakerNames:isVoice?p.speakerNames:{...SPEAKERS,...p.speakerNames},pickOpen:false})),
      titleLabel:!script.trim()?'第一行会当作标题':titlePlaceholder?'第一行还是占位符——把「（写一个标题）」换成真正的标题':titleLong?'标题太长了（超过 40 字），请精简':titlePunct?'标题末尾不要标点，否则会被当正文读出来':'标题：'+first,
      titleColor:script.trim()&&!hasTitle?RED:(hasTitle?TEAL:MUTED),
      titleFixable:!!script.trim()&&titlePunct&&!titleLong,
      fixTitle:()=>this.setState(p=>{const ls=p.script.split('\n');ls[0]=ls[0].trim().replace(/[。！？.!?]+$/,'');return {script:ls.join('\n')};}),
      charCount,
      elements,
      hasShotList:sentRows.length>0,
      showElements:!isOriginal&&script.trim().length>0,
      shotList,
      shotListOpen:S.shotListOpen,
      shotListLabel:S.shotListOpen?'收起':'展开',
      toggleShotList:()=>this.setState(p=>({shotListOpen:!p.shotListOpen})),
      copyShotList:()=>{const txt=shotList.map(s=>s.idx+' '+s.text+'\n   → '+s.advice).join('\n');const done=()=>this.toast('已复制，发到手机上看'),fail=()=>this.toast('复制失败，展开后手动选择文字');if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(txt).then(done,fail);else fail();},
      step1Hint:step1Ok?'':(script.trim().length<20?'至少写 20 个字':'第一行要是标题'),
      tip1Text:isMixed?(script.trim()&&allUnits.length&&!quoteUnits.length?'全是旁白，其实「AI 配音」模式就够了。':script.trim()&&allUnits.length&&!units.length?'全是原话，可以试试「只用原声」模式。':'旁白由 AI 读，「同期」开头的句子会从素材里剪出原声——说过的话不能改，不确定原话就先去传素材。'):isOriginal?'这个模式不配音：稿子里的每句都要是素材里真说过的话。可以删词、断句，不能加字。':(script.trim()?(missing.length?'没找到：'+missing.join('、')+'。写了就点一下那个要素；没写就在导语里补一句。':'五要素都有了，很好。'):'导语一句说清：何时、何地、何人、何事、为何。'),
      tip1Open:!!S.tips[1],
      tip1Label:S.tips[1]?'收起':'更多',
      toggleTip1:()=>this.setState(p=>({tips:{...p.tips,1:!p.tips[1]}})),
      tip2Open:!!S.tips[2],
      tip2Label:S.tips[2]?'收起':'更多',
      toggleTip2:()=>this.setState(p=>({tips:{...p.tips,2:!p.tips[2]}})),
      tip2Text:isMixed?'原声句不需要空镜；旁白约 '+sentenceEst+' 句，至少要 '+needShots+' 个不同镜头。':isOriginal?'多传几段无人说话的空镜，可以遮住跳切。':'你的稿子约 '+sentenceEst+' 句，至少要 '+needShots+' 个不同镜头（列举了几样东西的句子要更多）；一段采访只算 1 个。',
      files,
      hasFiles:S.files.length>0,
      fileCount:S.files.length,
      totalGb,
      capPct,
      onFiles:e=>{this.addFiles([...e.target.files]);e.target.value='';},
      onDrop:e=>{e.preventDefault();this.addFiles([...e.dataTransfer.files]);},
      onDragOver:e=>e.preventDefault(),
      addSampleFiles:()=>{this.addFiles(sampleFilesFor(mode,scn));if(!isVoice)this.setState(p=>({speakerNames:{...SPEAKERS,...p.speakerNames}}));},
      step2Hint:badCount?badCount+' 个文件不能用，删掉它才能继续':staleCount?'有 '+staleCount+' 个上次选的文件要重新选择（点上面的框再选一次）':!validCount?'至少传 1 个视频':'',
      step2HintColor:badCount?RED:MUTED,
      voiceChips:chip(VOICES,'voice'),
      voiceNote:(isMixed?'原声句不配音。':'')+(S.prefs.voice==='mine'?'做好后在结果页逐句录音，也可以先让 AI 读一遍再替换。':'AI 播音员先读，做好后你也可以逐句换成自己的声音。'),
      pacingChips:chip(PACING,'pacing'),
      toneChips:chip(TONE,'tone'),
      toggles,
      moodChips:chip(MOODS,'music_mood'),
      captionChips:chip(CAPTIONS,'caption_style'),
      captionNote:(S.prefs.caption_style==='none'?'只保留日期、机构名等字幕条，不显示旁白字幕。':S.prefs.caption_style==='big'?'字更大、描边更粗，适合投影和手机。':'白字黑边，居中下方，和电视新闻一致。')+(isVoice?'':' 原声句的字幕按实际说的话显示。'),
      custom:S.prefs.custom_instructions,
      onCustom:e=>this.setState(p=>({prefs:{...p.prefs,custom_instructions:e.target.value.slice(0,500)}})),
      prefsJson:'preferences = '+JSON.stringify({...S.prefs,mode:S.mode,target_cpm:target,quality_gate_mode:'warn'})+'\n// mode / target_cpm(由 pacing 推出) / lower_third / jump_cut_cover / quote_caption / 逐任务 quality_gate_mode 为提议字段；后端枚举 warn|block，production 强制 block（config.py L190）；QC 语速窗口 = pacing 目标 ±8%',
      startBg:canStart?INK:LINE,
      start:()=>{if(canStart)this.start();else if(maintenance)this.toast('服务维护中，稍后再试');},
      pTitle:t?t.title:'',
      pVersion:verLabel(t),
      pProgress:pUploading?upPct:(t?t.progress:0),
      pBarColor:pFailed?RED:pUploading?INK:TEAL,
      pBig:pUploading?upPct+'%':pQueued?(t.queue?'第 '+t.queue+' 位':'排队'):(t?t.progress:0)+'%',
      pHeadline:pUploading?'正在上传素材':pQueued?(t.queue?'前面还有 '+t.queue+' 个作品 · 预计 '+clock(now+avgMs*t.queue)+' 开始':'排到了，马上开始'):pFailed?(t.errorKind==='quote_missing'?'有 '+(t.badRows||[]).length+' 句原话在素材里没找到':'停在了“'+curStage.label+'”'):pDone?'做好了':(stepN>1?'第 '+(stepI+1)+'/'+stepN+' 步 · '+t.op+'：'+curStage.label:(t&&t.revision?'正在'+(t.op||'重新制作')+'：'+curStage.label:'AI 剪辑师正在：'+curStage.label)),
      pDoing:pUploading?'上传完成前请别关这个页面；中断了要重新传。':pQueued?'素材已上传到服务器，可以关页面。一次只做一个作品，每个约 '+fmtMin(avgMs)+'。可以先去写下一篇，做好了会在“我的作品”里。':pFailed?'':curStage.doing,
      hasStepLine:!!t&&(t.status==='running'||t.status==='queued')&&stepN>1&&stepI<stepN-1,
      pStepLine:stepN>1&&stepI<stepN-1?'接下来：'+plan.steps.slice(stepI+1).map(s=>s.op).join(' → '):'',
      pPro:t?(pUploading?'multipart POST /api/tasks 上传中 · XHR progress 纯前端可做 · 配额在请求开始即计数':pQueued?'status=queued · 素材已上传，顺序在服务端队列（已定：先收后做）；队列位置 / ETA 接口 需新增后端':'阶段 '+(t.current||1)+'/10 · '+curStage.devName+' · progress '+t.progress+' · revision '+t.revision+(stepN>1?' · step '+(stepI+1)+'/'+stepN+'（每步一次 API 调用、各 +1 revision，remix 与 replace-shot 互斥）':'')):'',
      pNote:pUploading?'上传完成前请别关这个页面；中断了要重新传，这次名额也会用掉。':'可以先去做别的，回来在“我的作品”里找它。'+(pQueued?'还没开始制作，取消不占名额。':'取消并删除也会用掉 1 次提交机会。'),
      hasUploadRow:!!t&&!!t.upTotal&&(!pFailed||t.errorKind==='network'),
      upBorder:pFailed?RED:pUploading?INK:TEAL,
      upIconBg:pFailed?RED:pUploading?INK:TEAL,
      upIconFg:'#fff',
      upIcon:pFailed?'✕':pUploading?'↑':'✓',
      upPct,
      hasUpList:!!t&&(t.files||[]).length>0&&(pUploading||pFailed),
      upList:t?(()=>{let cum=0;return (t.files||[]).map(f=>{const s0=cum;cum+=f.mb;const d=Math.max(0,Math.min(f.mb,(t.upDone||0)-s0));const ok=d>=f.mb;return {name:f.name,pct:Math.round(d/Math.max(1,f.mb)*100)+'%',fg:ok?TEAL:d>0?INK:MUTED,dot:ok?TEAL:'transparent',ring:ok?TEAL:d>0?INK:LINE};});})():[],
      upFiles:t?t.fileCount+' 个文件 · '+((t.upTotal||0)/1024).toFixed(1)+' GB':'',
      upDetail:pFailed?'上传中断在 '+Math.round(t.upDone||0)+' / '+t.upTotal+' MB':pUploading?'已传 '+Math.round(t.upDone)+' / '+t.upTotal+' MB · 约剩 '+Math.max(1,Math.ceil((t.upTotal-t.upDone)/(LIMITS.uploadMbps*(P.simSpeed||1))))+' 秒':'上传完成',
      upElapsed:t&&t.upStart?fmtEl(((t.upEnd||now)-t.upStart)/1000):'',
      pStages,
      pFailed,pBusy,
      pRunning:!!t&&['running','queued','uploading'].includes(t.status),
      pError:t&&t.error||'',
      pErrorPro:t&&t.errorPro||'',
      pErrorAdvice:!pFailed?'':t.errorKind==='quote_missing'?'稿子和素材都还在。把那几句改成真说过的话，或者传含这几句话的视频，再重新提交（算 1 次提交机会）。':t.errorKind==='network'?'稿子和素材都还在。按现在的后端规则，这次名额已经用掉；换个网络好一点的地方，再试一次会重新上传。':t.errorKind==='qc'?'新闻里不能用不相关的画面充数。把那句改成拍到的东西（人们在做什么），或者删掉它，再重新提交（算 1 次提交机会）。稿子和素材都还在。':t.errorKind==='shortage'?'删掉 '+t.shortBy+' 句最省事；或者回去再传 '+t.shortBy+' 个不同场景的视频。稿子和素材都还在。':'稿子和素材都还在。再试一次会重新提交（算 1 次提交机会）；还不行就等几分钟再试。',
      pFailActions,
      askCancel:ask('取消并删除这个作品？',pQueued?((t.upDone>0)?'已上传但还没开始制作，不占名额；会连素材一起从服务器删除。':'还没提交到服务端，不占名额；稿子和素材会一起删掉。'):pUploading?'上传会中断；稿子和素材会一起删掉。':'素材、进度和片子都会被永久删除，不能恢复；这次的提交机会也不会退回。','取消并删除',()=>t&&this.deleteTask(t.id)),
      askDelete:()=>this.setState({confirm:{title:'删除这个作品？',body:'素材、片子和报告都会被永久删除，不能恢复。',ok:'永久删除',fn:()=>t&&this.deleteTask(t.id)}}),
      rTitle:t?t.title:'',
      rOwnerLine:t?(verLabel(t)+(version?'（正在看'+(t.versions.indexOf(version)?'第 '+t.versions.indexOf(version)+' 次修改':'初版')+'）':'')+' · '+fmtTime(total)):'',
      rLeft:left&&left.text?'· '+left.text:'',
      rLeftFg:left&&left.urgent?RED:MUTED,
      rMeta:t?'1080p · 30fps · MP4 · 制作用时 2:17（不含上传排队）· revision '+t.revision+' · 每次 remix / replace-shot 各 +1 revision · 样片水印为前端叠加，导出烧录 需新增后端':'',
      gateLabel:gate[0],
      gateBorder:gate[1],
      gateBg:gate[2],
      gateFg:gate[3],
      canDownload:!!t&&pDone,
      download:()=>this.setState({exportOpen:true}),
      closeExport:()=>this.setState({exportOpen:false}),
      exportOpen:!!S.exportOpen&&!!t,
      expFmt:[['mp4','MP4 视频'],['gif','GIF 动图'],['mp3','MP3 音频'],['srt','SRT 字幕'],['png','封面图片']].map(([v,l])=>{const on=S.exp.fmt===v;return {label:l,border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState(p=>({exp:{...p.exp,fmt:v}}))};}),
      expIsVideo:S.exp.fmt==='mp4'||S.exp.fmt==='gif',
      expAspect:[['16:9','横屏 16:9'],['9:16','竖屏 9:16'],['1:1','正方形']].map(([v,l])=>{const on=S.exp.aspect===v;return {label:l,border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState(p=>({exp:{...p.exp,aspect:v}}))};}),
      expRes:[['720p','720p 省流量'],['1080p','1080p 高清']].map(([v,l])=>{const on=S.exp.res===v;return {label:l,border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState(p=>({exp:{...p.exp,res:v}}))};}),
      expSub:[['std','标准字幕'],['big','大字幕'],['none','不要字幕']].map(([v,l])=>{const on=S.exp.sub===v;return {label:l,border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState(p=>({exp:{...p.exp,sub:v}}))};}),
      expNote:S.exp.fmt==='gif'?'GIF 只取前 6 秒，没有声音，适合发群里做预告。':S.exp.fmt==='mp3'?(isOriginal?'只导出现场原声，可以当广播稿。':'只导出配音和现场原声，可以当广播稿。'):S.exp.fmt==='srt'?'字幕文件可以导入其他剪辑软件。':S.exp.fmt==='png'?'导出当前画面当封面。':(S.exp.aspect!=='16:9'?'竖屏 / 正方形会居中裁切，字幕条重新排版；两边的画面会被裁掉。':''),
      expOkLabel:passed?'开始导出':'检查通过后才能导出',
      expOkBg:passed?INK:LINE,
      doExport:()=>{if(!passed){this.toast('检查都通过后才能导出');return;}this.setState({exportOpen:false});this.toast('开始导出 '+S.exp.fmt.toUpperCase()+(S.exp.fmt==='mp4'?' · '+S.exp.aspect+' · '+S.exp.res:'')+'（原型演示）');},
      canManage:!!t&&pDone&&isOwner&&t.id!=='t-sample',
      renameTask:()=>{if(!t)return;this.setState({confirm:{title:'重命名作品',body:'改的是作品名，不改视频里的标题字幕。',ok:'保存',okBg:INK,input:true,text:t.title,placeholder:'作品名',fn:c=>{const v=((c&&c.text)||'').trim().slice(0,32);if(!v)return;t.title=v;this.bump();this.toast('已重命名');}}});},
      duplicateTask:()=>{if(!t)return;const curMode=t.mode||'voiceover';this.setState({confirm:{title:'复制一份',body:'复制时保持「'+MODE_NAME(curMode)+'」，还是改为…',ok:'复制',okBg:INK,choice:'keep',choices:[{v:'keep',label:'保持'}].concat(MODES.filter(m=>m[0]!==curMode).map(m=>({v:m[0],label:'改为 '+m[1]}))),fn:c=>{const ch=(c&&c.choice)||'keep';if(ch==='keep'){const id='t-'+Date.now().toString(36);const copy={...t,id,title:(t.title+'（副本）').slice(0,34),createdAt:Date.now(),startedAt:Date.now(),doneAt:Date.now(),reflection:'',rows:cloneRows(t.rows),versions:t.versions.map(v=>({...v,rows:cloneRows(v.rows)})),stages:t.stages.map(s=>({...s})),replaceFails:{},plan:null};this.setState(p=>({tasks:{...p.tasks,[id]:copy},history:[id,...p.history]}),()=>this.openTask(id,true));this.toast('已复制一份，可以另做一版');return;}clearInterval(this.playTimer);this.setState({screen:'create',step:1,script:t.script||'',files:(t.files||[]).map(f=>({...f})),mode:ch,typeMarks:{},speakerNames:{...(t.speakerNames||{})},pickOpen:true,currentId:null,...this.resetResult()},()=>this.setMode(ch));this.toast('已按「'+MODE_NAME(ch)+'」放回向导，改好稿子就能开始');}}});},
      wmText:blocking?'样片 · 还有 '+blocking+' 处要处理':!passed?'样片 · 还有 '+openCount+' 项要确认':'样片',
      canDelete:!!t&&isOwner,
      hasVersions:!!t&&t.versions.length>1,
      versionChips:t?t.versions.map((v,i)=>{const on=version?version.rev===v.rev:v.rev===t.revision;return {label:i?'第 '+i+' 次 · '+v.op:'初版',border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState({viewRev:v.rev===t.revision?null:v.rev,selectedId:v.rows[0].id,time:0,editing:false})};}):[],
      canRestore:!!version&&isOwner,
      restoreVersion:()=>this.restoreVersion(),
      nextAction,
      nextHasBtn:!!nextGo,
      nextBtn,
      nextGo:()=>{if(nextGo)nextGo();},
      posterSrc:poster?thumb(poster.beats[0].shot):'',
      posterText:poster?poster.s:'',
      posterGen:!!(poster&&poster.gen),
      showGraphics:!t||!t.prefs||t.prefs.news_graphics!==false,
      topicText:t?t.title.slice(0,10):'',
      playIcon:S.playing?'❚❚':'▶',
      playAria:S.playing?'暂停':'播放',
      togglePlay:()=>this.togglePlay(),
      timeLabel:fmtTime(S.time)+' / '+fmtTime(total),
      progressPct:total?Math.min(100,S.time/total*100):0,
      checks:checkItems,
      checkSummary:doneCount+' / '+checks.length+' 完成'+(blocking?' · '+blocking+' 项要处理':''),
      checkPct:checks.length?Math.round(doneCount/checks.length*100):100,
      metrics,
      showMetrics:dev,
      gateNote,
      cells,
      overview,
      stripH:S.bigFont?124:104,
      cellMin:S.bigFont?124:104,
      rowCount:rows.length,
      selIdx:selI+1,
      selBeats,
      selBadges:badges,
      selText:S.pendEdit[sel&&sel.id]||(sel?sel.s:''),
      selNote,
      selPro:sel?(isQ(sel)?'quote · '+(sel.file||'—')+' '+fmtT(sel.t0)+'–'+fmtT(sel.t1)+' · score '+sel.c.toFixed(2)+' · snr '+sel.snr+' dB · spk '+sel.spk:'sentence_id '+sel.id+' · '+sel.d.toFixed(2)+'s · confidence '+sel.c.toFixed(2)+' · '+cpmSel+' cpm'):'',
      poolLeft,
      canEdit,
      notEditing:!S.editing,
      editing:S.editing,
      editText:S.editText,
      onEditText:e=>this.setState({editText:e.target.value}),
      startEdit:()=>this.setState({editing:true,editText:S.pendEdit[sel.id]||sel.s}),
      cancelEdit:set({editing:false}),
      saveEdit:()=>{const txt=S.editText.trim();if(!txt||!sel)return;this.setState(p=>{const pe={...p.pendEdit};if(txt===sel.s)delete pe[sel.id];else pe[sel.id]=txt;return {pendEdit:pe,editing:false};});},
      recLabel:rec?'● 录音中 '+rec.left.toFixed(1)+'s':S.pendVoice[sel&&sel.id]?'✓ 已录，点击重录':'🎙 录我的声音',
      recBorder:rec?RED:(S.pendVoice[sel&&sel.id]?TEAL:LINE),
      recFg:rec?RED:(S.pendVoice[sel&&sel.id]?TEAL:INK),
      recordVoice:()=>this.record(),
      instruction:S.instruction,
      onInstruction:e=>this.setState({instruction:e.target.value.slice(0,500)}),
      queueReplace:()=>{const ins=S.instruction.trim();if(!sel||!t)return;if(!ins){this.toast('先写一句想要的画面，或点下面的候选镜头');return;}if(poolLeft<=0&&!S.pendReplace[sel.id]){this.toast('没有更多可换的镜头了，试试删掉这句');return;}const direct=candPool.find(p=>SHOT[p]===ins);const abstract=/(融合|氛围|意义|精神|平台|理念|纽带|感受|未来|发展|文化)/.test(ins)||ins.replace(/\s/g,'').length<4;if(direct==null&&abstract){t.replaceFails={...(t.replaceFails||{}),[sel.id]:{kind:'abstract',text:'“'+ins+'”太抽象，AI 找不到能直接表现它的画面。'}};this.bump();this.toast('这句太抽象，换不了——看看下面的建议');return;}if(t.replaceFails&&t.replaceFails[sel.id])delete t.replaceFails[sel.id];this.setState(p=>({pendReplace:{...p.pendReplace,[sel.id]:ins},instruction:''}));},
      replaceBg:S.instruction.trim()?INK:LINE,
      replaceLabel:S.pendReplace[sel&&sel.id]?'改成这个':'记下换画面',
      replaceHint:S.pendReplace[sel&&sel.id]?'已记下：“'+S.pendReplace[sel.id]+'”':(S.instruction.trim()?S.instruction.length+' / 500 字':'一次只能换一句的画面；从上面挑一个镜头最稳'),
      candShots,
      candTitle,
      noCand:candShots.length===0,
      seekTo:e=>{if(!t||!pDone)return;const r=e.currentTarget.getBoundingClientRect();const frac=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));const nt=frac*total;const i=starts.findIndex((s,k)=>nt>=s&&nt<s+rows[k].d);this.setState({time:nt,selectedId:i>=0?rows[i].id:S.selectedId});},
      selDeleted:!!sel&&S.deleted.includes(sel.id),
      toggleDeleteSel:()=>{if(!sel)return;this.setState(p=>({deleted:p.deleted.includes(sel.id)?p.deleted.filter(x=>x!==sel.id):[...p.deleted,sel.id]}));},
      delBorder:sel&&S.deleted.includes(sel.id)?RED:LINE,
      delBg:sel&&S.deleted.includes(sel.id)?'#fbeee6':'#fff',
      delFg:sel&&S.deleted.includes(sel.id)?RED:INK,
      selFactWarn:!!sel&&factsOf(sel.s).length>0,
      selFacts:sel?factsOf(sel.s).join('、'):'',
      hasPending:pendCount>0&&canEdit,
      pendingTitle:'记下了 '+pendCount+' 处修改：'+pendParts.join('、'),
      pendingDetail:stepNames.length>1?'会按顺序做 '+stepNames.length+' 步：'+stepNames.join(' → ')+'，每步约 1 分钟':'重新合成一次，通常不到 1 分钟',
      clearPending:()=>this.setState(this.resetResult()),
      applyPending:()=>this.applyPending(),
      applyLabel:'应用修改 → 第 '+(t?Math.max(1,t.versions.length):1)+' 次修改',
      removeGone:()=>{if(t)this.setState(p=>{const tasks={...p.tasks};delete tasks[t.id];return {tasks,history:p.history.filter(h=>h!==t.id),currentId:null,screen:'create',step:1};});},
      confirmOpen:!!S.confirm,
      cTitle:S.confirm?S.confirm.title:'',
      cBody:S.confirm?S.confirm.body:'',
      cOk:S.confirm?S.confirm.ok:'',
      cOkBg:S.confirm&&S.confirm.mustMatch&&(S.confirm.text||'').trim()!==S.confirm.mustMatch?LINE:(S.confirm&&S.confirm.okBg?S.confirm.okBg:RED),
      cHasInput:!!(S.confirm&&(S.confirm.mustMatch||S.confirm.input)),
      cHasChoices:!!(S.confirm&&S.confirm.choices),
      cChoices:S.confirm&&S.confirm.choices?S.confirm.choices.map(c=>{const on=(S.confirm.choice||'keep')===c.v;return {label:c.label,border:on?INK:LINE,bg:on?INK:'#fff',fg:on?'#fff':INK,pick:()=>this.setState(p=>({confirm:p.confirm?{...p.confirm,choice:c.v}:null}))};}):[],
      cText:S.confirm?(S.confirm.text||''):'',
      cPlaceholder:S.confirm?(S.confirm.placeholder||''):'',
      onCText:e=>{const v=e.target.value;this.setState(p=>({confirm:p.confirm?{...p.confirm,text:v}:null}));},
      confirmOk:()=>{const c=S.confirm;if(c&&c.mustMatch&&(c.text||'').trim()!==c.mustMatch){this.toast('请输入 '+c.mustMatch+' 确认');return;}this.setState({confirm:null},()=>{if(c&&c.fn)c.fn(c);});},
      confirmCancel:set({confirm:null}),
      toastOpen:!!S.toast,
      toastText:S.toast||'',
      suffLabel,
      suffColor,
      suffDetail,
      suffAdvice,
      suffPct:Math.round(suffRatio*100),
      hasSentList:validFiles.length>0&&sentRaw.length>0,
      sentMore:sentRaw.length>12,
      sentMoreLabel:S.sentListOpen?'收起':'展开全部 '+sentRaw.length+' 句',
      toggleSentList:()=>this.setState(p=>({sentListOpen:!p.sentListOpen})),
      sentList:sentShown.map((s,i)=>{const m=S.sentMarks[s];return {idx:String(i+1).padStart(2,'0'),text:s,mark:m==='yes'?'拍到了 ✓':m==='no'?'没拍到 ✕':'点一下确认',fg:m==='yes'?TEAL:m==='no'?RED:MUTED,border:m==='yes'?TEAL:m==='no'?RED:LINE,bg:m==='yes'?'#e7f3ea':m==='no'?'#fbeee6':'#fff',flip:()=>this.setState(p=>{const nm={...p.sentMarks};if(m==='yes')nm[s]='no';else if(m==='no')delete nm[s];else nm[s]='yes';return {sentMarks:nm};})};}),
      sentCheckSummary:'确认了 '+(yesCount+noCount)+' / '+sentRaw.length+(noCount?' · 没拍到 '+noCount+' 句':''),
      showWatermark,
      wmOwner:t?verLabel(t):'',
      hasSelFail:!!selFail,
      selFailText:selFail?selFail.text:'',
      selFailFixes:failFixes
    };
  }
}
```


# 附录 C · 自动提取索引

## C-1 组件方法（45）

`constructor(props)` · `applyFont()` · `persist()` · `restore()` · `scrollToBoard()` · `stepRow(dir)` · `scrollPanelNarrow()` · `scrollStrip()` · `componentDidUpdate()` · `setMode(m)` · `appendToScript(text)` · `removeFromScript(text)` · `componentDidMount()` · `componentWillUnmount()` · `complete(t,op)` · `blockingRows(t)` · `checksFor(t,rows)` · `gateOf(t)` · `cur()` · `toast(msg,act)` · `bump()` · `pendingCount(o)` · `guard(fn)` · `newTask(force)` · `resetResult(extra)` · `draftOf()` · `addFiles(list)` · `probe(name,file)` · `runTask(id,steps,opts)` · `resumeAll()` · `resumeTask(id)` · `startNext()` · `uploadOnly(id)` · `tick()` · `start()` · `restoreDraft(step)` · `retrySame()` · `deleteTask(id)` · `openTask(id,force)` · `rowStarts(t)` · `togglePlay()` · `record()` · `applyPending()` · `restoreVersion()` · `renderVals()`

## C-2 `data-r` 响应式标记（26）

`app` · `menu` · `side` · `narrow-hide` · `main` · `steps` · `line` · `stack` · `wrap` · `h1` · `modes` · `tline` · `narrow-only` · `frow` · `mrow` · `spk` · `toggles` · `startbar` · `rtitle` · `ractions` · `rgrid` · `strip` · `checks` · `panel` · `pendbtns` · `drawer`

## C-3 `renderVals()` 视图键（311）

`prefs` · `tasks` · `tips` · `selectedId` · `time` · `maxFiles` · `maxTotalGb` · `totalDur` · `bigFont` · `toggleFont` · `fontBtnLabel` · `fontBtnBg` · `fontBtnFg` · `cpmRange` · `charSoft` · `modeCode` · `modeCards` · `modeMoreLabel` · `candMore` · `toastHasAct` · `showIntro` · `step1Sub` · `scriptPlaceholder` · `hasQuoteLong` · `quoteLongText` · `hasNewWords` · `newWordsText` · `sentListTitle` · `sentListSub` · `sentStats` · `hasPick` · `pickOpen` · `pickLabel` · `pickSummary` · `togglePick` · `goPick` · `hasSuggest` · `suggestText` · `suggestYesLabel` · `suggestNoLabel` · `suggestYes` · `suggestNo` · `step2Sub` · `uploadNote` · `aHasSpeech` · `switchMixed` · `suffDisplay` · `hasMatchCard` · `matchColor` · `matchTitle` · `matchSummary` · `matchRows` · `hasJumpLine` · `jumpLine` · `hasSpeakers` · `step3Sub` · `showVoice` · `voiceTitle` · `showPacing` · `showJump` · `jumpNote` · `showQuoteCap` · `summaryLine` · `rMode` · `showLT` · `ltName` · `ltRole` · `metricsTitle` · `selIsQuote` · `selIsQuoteMode` · `selPrev` · `selNext` · `selIsNarr` · `trimL` · `trimW` · `trimRange` · `trimSaveBg` · `trimStartBack` · `trimEndFwd` · `saveTrim` · `cancelTrim` · `trimPending` · `trimPendingText` · `altTitle` · `canToNarr` · `toNarrLabel` · `toNarrBorder` · `toNarrBg` · `toNarrFg` · `toggleToNarr` · `selSpkName` · `selSpkRole` · `selSpkBorder` · `selSpkHint` · `onSelSpkName` · `onSelSpkRole` · `retentionLine` · `statusText` · `statusColor` · `hasSaved` · `savedLabel` · `histSortLabel` · `toggleHistSort` · `drawerTitle` · `openDrawer` · `closeDrawer` · `drawerOpen` · `openSample` · `newTask` · `goHome` · `isCreate` · `isProcessing` · `isResult` · `isGone` · `scriptRef` · `elemHint` · `hasLongSent` · `longSentText` · `isStep1` · `isStep2` · `isStep3` · `s1` · `s2` · `s3` · `goStep1` · `goStep2` · `goStep3` · `back` · `next` · `nextBg` · `onScript` · `useSample` · `titleLabel` · `titleColor` · `titleFixable` · `fixTitle` · `hasShotList` · `showElements` · `shotListOpen` · `shotListLabel` · `toggleShotList` · `copyShotList` · `step1Hint` · `tip1Text` · `tip1Open` · `tip1Label` · `toggleTip1` · `tip2Open` · `tip2Label` · `toggleTip2` · `tip2Text` · `hasFiles` · `fileCount` · `onFiles` · `onDrop` · `onDragOver` · `addSampleFiles` · `step2Hint` · `step2HintColor` · `voiceChips` · `voiceNote` · `pacingChips` · `toneChips` · `moodChips` · `captionChips` · `captionNote` · `custom` · `onCustom` · `prefsJson` · `startBg` · `start` · `pTitle` · `pVersion` · `pProgress` · `pBarColor` · `pBig` · `pHeadline` · `pDoing` · `hasStepLine` · `pStepLine` · `pPro` · `pNote` · `hasUploadRow` · `upBorder` · `upIconBg` · `upIconFg` · `upIcon` · `hasUpList` · `upList` · `upFiles` · `upDetail` · `upElapsed` · `pRunning` · `pError` · `pErrorPro` · `pErrorAdvice` · `askCancel` · `askDelete` · `rTitle` · `rOwnerLine` · `rLeft` · `rLeftFg` · `rMeta` · `gateLabel` · `gateBorder` · `gateBg` · `gateFg` · `canDownload` · `download` · `closeExport` · `exportOpen` · `expFmt` · `expIsVideo` · `expAspect` · `expRes` · `expSub` · `expNote` · `expOkLabel` · `expOkBg` · `doExport` · `canManage` · `renameTask` · `duplicateTask` · `wmText` · `canDelete` · `hasVersions` · `versionChips` · `canRestore` · `restoreVersion` · `nextHasBtn` · `nextGo` · `posterSrc` · `posterText` · `posterGen` · `showGraphics` · `topicText` · `playIcon` · `playAria` · `togglePlay` · `timeLabel` · `progressPct` · `checks` · `checkSummary` · `checkPct` · `showMetrics` · `stripH` · `cellMin` · `rowCount` · `selIdx` · `selBadges` · `selText` · `selPro` · `notEditing` · `editing` · `editText` · `onEditText` · `startEdit` · `cancelEdit` · `saveEdit` · `recLabel` · `recBorder` · `recFg` · `recordVoice` · `instruction` · `onInstruction` · `queueReplace` · `replaceBg` · `replaceLabel` · `replaceHint` · `noCand` · `seekTo` · `selDeleted` · `toggleDeleteSel` · `delBorder` · `delBg` · `delFg` · `selFactWarn` · `selFacts` · `hasPending` · `pendingTitle` · `pendingDetail` · `clearPending` · `applyPending` · `applyLabel` · `removeGone` · `confirmOpen` · `cTitle` · `cBody` · `cOk` · `cOkBg` · `cHasInput` · `cHasChoices` · `cChoices` · `cText` · `cPlaceholder` · `onCText` · `confirmOk` · `confirmCancel` · `toastOpen` · `toastText` · `suffPct` · `hasSentList` · `sentMore` · `sentMoreLabel` · `toggleSentList` · `sentList` · `sentCheckSummary` · `wmOwner` · `hasSelFail` · `selFailText` · `selFailFixes`

## C-4 模板中的绑定路径（609）

`openDrawer` · `goHome` · `toggleFont` · `bigFont` · `fontBtnBg` · `fontBtnFg` · `fontBtnLabel` · `showHelp` · `statusColor` · `statusText` · `hasSaved` · `false` · `savedLabel` · `maintenance` · `newTask` · `toggleHistSort` · `histSortLabel` · `histItems` · `h.open` · `h.border` · `h.shadow` · `h.title` · `h.statusFg` · `h.statusBg` · `h.statusBorder` · `h.status` · `h.mode` · `h.rev` · `h.date` · `h.leftFg` · `h.leftW` · `h.left` · `openSample` · `retentionLine` · `dev` · `isCreate` · `true` · `goStep1` · `s1.bg` · `s1.fg` · `s1.border` · `s1.done` · `s1.notDone` · `s1.mark` · `s1.label` · `s1.line` · `goStep2` · `s2.bg` · `s2.fg` · `s2.border` · `s2.done` · `s2.notDone` · `s2.mark` · `s2.label` · `s2.line` · `goStep3` · `s3.bg` · `s3.fg` · `s3.border` · `s3.done` · `s3.notDone` · `s3.mark` · `s3.label` · `isStep1` · `showIntro` · `dismissIntro` · `step1Sub` · `demo` · `useSample` · `charSoft` · `modeCode` · `modeCards` · `mc.pick` · `mc.on` · `mc.border` · `mc.bg` · `mc.isA` · `mc.isB` · `mc.isC` · `mc.name` · `mc.tag` · `mc.desc` · `mc.fit` · `toggleModeMore` · `modeMoreLabel` · `hasPick` · `pickSummary` · `togglePick` · `pickLabel` · `pickOpen` · `pickGroups` · `pg.file` · `pg.count` · `pg.lines` · `ln.on` · `ln.flip` · `ln.tc` · `ln.spk` · `ln.text` · `scriptRef` · `script` · `onScript` · `scriptPlaceholder` · `titleColor` · `titleLabel` · `titleFixable` · `fixTitle` · `hasLongSent` · `longSentText` · `hasQuoteLong` · `quoteLongText` · `hasNewWords` · `newWordsText` · `charCount` · `showElements` · `elements` · `el.flip` · `el.on` · `el.border` · `el.bg` · `el.fg` · `el.mark` · `el.label` · `elemHint` · `hasShotList` · `sentListTitle` · `sentListSub` · `toggleShotList` · `shotListLabel` · `copyShotList` · `shotListOpen` · `sentRows` · `sh.leftBorder` · `sh.idx` · `sh.chunks` · `ch.fg` · `ch.deco` · `ch.text` · `sh.hasAdvice` · `sh.fg` · `sh.advice` · `sh.hasStatus` · `sh.stBg` · `sh.stFg` · `sh.status` · `sh.score` · `sh.hasChip` · `sh.flip` · `sh.chipBorder` · `sh.chipBg` · `sh.chipFg` · `sh.chip` · `sentStats` · `hasSuggest` · `suggestText` · `suggestYes` · `suggestYesLabel` · `suggestNo` · `suggestNoLabel` · `tip1Text` · `toggleTip1` · `tip1Label` · `tip1Open` · `step1Hint` · `next` · `nextBg` · `isStep2` · `step2Sub` · `addSampleFiles` · `onDrop` · `onDragOver` · `onFiles` · `uploadNote` · `hasFiles` · `files` · `f.border` · `f.bg` · `f.thumbImg` · `f.name` · `f.metaColor` · `f.meta` · `f.hasLines` · `f.toggleOpen` · `f.openLabel` · `f.toggleAdv` · `f.advLabel` · `f.remove` · `f.adv` · `f.note` · `f.onNote` · `f.hasTrim` · `f.inSec` · `f.onIn` · `f.outSec` · `f.onOut` · `f.open` · `f.lines` · `ln.pickable` · `ln.plain` · `f.more` · `f.toggleAll` · `f.moreLabel` · `fileCount` · `maxFiles` · `capPct` · `totalGb` · `maxTotalGb` · `totalDur` · `aHasSpeech` · `switchMixed` · `hasMatchCard` · `matchColor` · `matchTitle` · `matchSummary` · `isOriginal` · `goPick` · `matchRows` · `mr.border` · `mr.bg` · `mr.idx` · `mr.text` · `mr.info` · `mr.stBg` · `mr.stFg` · `mr.status` · `mr.score` · `mr.hasFix` · `mr.fixes` · `fx.go` · `fx.label` · `hasJumpLine` · `jumpLine` · `hasSpeakers` · `speakerRows` · `sp.border` · `sp.bg` · `sp.idx` · `sp.name` · `sp.onName` · `sp.role` · `sp.onRole` · `sp.metaFg` · `sp.metaW` · `sp.meta` · `suffDisplay` · `suffColor` · `suffLabel` · `suffDetail` · `suffPct` · `suffAdvice` · `hasSentList` · `sentCheckSummary` · `sentList` · `sl.flip` · `sl.border` · `sl.bg` · `sl.idx` · `sl.text` · `sl.fg` · `sl.mark` · `sentMore` · `toggleSentList` · `sentMoreLabel` · `tip2Text` · `toggleTip2` · `tip2Label` · `tip2Open` · `back` · `step2HintColor` · `step2Hint` · `isStep3` · `step3Sub` · `showVoice` · `voiceTitle` · `voiceChips` · `c.pick` · `c.border` · `c.bg` · `c.fg` · `c.label` · `voiceNote` · `showPacing` · `cpmRange` · `pacingChips` · `toneChips` · `captionChips` · `captionNote` · `toggles` · `tg.borderStyle` · `tg.border` · `tg.bg` · `tg.flip` · `tg.on` · `tg.label` · `tg.track` · `tg.knob` · `tg.showNote` · `tg.noteColor` · `tg.note` · `tg.showMood` · `moodChips` · `m.pick` · `m.border` · `m.bg` · `m.fg` · `m.label` · `tg.showSpeakers` · `tg.devName` · `showJump` · `jumpNote` · `jumpChips` · `c.disabled` · `c.cursor` · `showQuoteCap` · `quoteCapChips` · `custom` · `onCustom` · `prefsJson` · `summaryLine` · `start` · `startBg` · `isProcessing` · `pTitle` · `pVersion` · `pBig` · `pHeadline` · `pDoing` · `hasStepLine` · `pStepLine` · `pPro` · `pProgress` · `pBarColor` · `pBusy` · `pFailed` · `pError` · `pErrorAdvice` · `pErrorPro` · `pFailActions` · `a.go` · `a.bg` · `a.fg` · `a.label` · `askDelete` · `hasUploadRow` · `upBorder` · `upIconBg` · `upIconFg` · `upIcon` · `upFiles` · `upDetail` · `upPct` · `hasUpList` · `upList` · `uf.fg` · `uf.ring` · `uf.dot` · `uf.name` · `uf.pct` · `upElapsed` · `pStages` · `st.rowBg` · `st.iconBg` · `st.iconFg` · `st.iconBorder` · `st.anim` · `st.icon` · `st.weight` · `st.color` · `st.label` · `st.devName` · `st.msg` · `st.elapsed` · `pRunning` · `pNote` · `askCancel` · `isResult` · `rTitle` · `gateFg` · `gateBg` · `gateBorder` · `gateLabel` · `rMode` · `rOwnerLine` · `rLeftFg` · `rLeft` · `hasVersions` · `versionChips` · `v.pick` · `v.border` · `v.bg` · `v.fg` · `v.label` · `canRestore` · `restoreVersion` · `rMeta` · `canManage` · `renameTask` · `duplicateTask` · `canDelete` · `canDownload` · `download` · `nextAction` · `canEdit` · `nextHasBtn` · `nextGo` · `nextBtn` · `posterSrc` · `showGraphics` · `topicText` · `posterGen` · `showLT` · `ltName` · `ltRole` · `showWatermark` · `wmText` · `wmOwner` · `posterText` · `togglePlay` · `playAria` · `playIcon` · `timeLabel` · `seekTo` · `progressPct` · `selIdx` · `rowCount` · `isVoice` · `selIsQuoteMode` · `overview` · `o.flex` · `o.bg` · `o.ring` · `stripH` · `cells` · `c.id` · `c.select` · `c.flex` · `cellMin` · `c.shadow` · `c.opacity` · `c.thumb` · `c.idx` · `c.hasDot` · `c.dotBg` · `c.dotBorder` · `c.short` · `c.hasTag` · `c.tagBg` · `c.tag` · `checkSummary` · `checkPct` · `checks` · `ck.border` · `ck.bg` · `ck.toggle` · `ck.done` · `ck.boxBg` · `ck.boxFg` · `ck.boxBorder` · `ck.cursor` · `ck.notDone` · `ck.mark` · `ck.deco` · `ck.color` · `ck.text` · `ck.code` · `ck.hasGo` · `ck.go` · `ck.goLabel` · `showMetrics` · `metricsTitle` · `metrics` · `m` · `gateNote` · `selPrev` · `selNext` · `selBadges` · `bd.bg` · `bd.fg` · `bd.border` · `bd.label` · `selPro` · `notEditing` · `selText` · `editing` · `editText` · `onEditText` · `saveEdit` · `cancelEdit` · `selNote` · `hasSelFail` · `selFailText` · `selFailFixes` · `selBeats` · `b.thumb` · `b.showText` · `b.text` · `b.desc` · `b.shot` · `b.conf` · `selIsNarr` · `proposed` · `startEdit` · `recordVoice` · `recBorder` · `recFg` · `recLabel` · `current` · `poolLeft` · `instruction` · `onInstruction` · `queueReplace` · `replaceBg` · `replaceLabel` · `candTitle` · `candShots` · `cs.use` · `cs.border` · `cs.bg` · `cs.thumb` · `cs.desc` · `candMore` · `toggleCandMore` · `candMoreLabel` · `noCand` · `replaceHint` · `delBorder` · `delBg` · `delFg` · `selDeleted` · `toggleDeleteSel` · `selFactWarn` · `selFacts` · `selIsQuote` · `trimWords` · `w.pick` · `w.bg` · `w.fg` · `w.deco` · `w.text` · `trimL` · `trimW` · `trimRange` · `trimStartBack` · `trimEndFwd` · `saveTrim` · `trimSaveBg` · `cancelTrim` · `trimPending` · `trimPendingText` · `altTitle` · `altTakes` · `ak.pick` · `ak.border` · `ak.bg` · `ak.dur` · `ak.text` · `ak.meta` · `canToNarr` · `toggleToNarr` · `toNarrBorder` · `toNarrBg` · `toNarrFg` · `toNarrLabel` · `selSpkName` · `onSelSpkName` · `selSpkBorder` · `selSpkRole` · `onSelSpkRole` · `selSpkHint` · `hasPending` · `pendingTitle` · `pendingDetail` · `clearPending` · `applyPending` · `applyLabel` · `isGone` · `removeGone` · `drawerOpen` · `drawerTitle` · `closeDrawer` · `exportOpen` · `expFmt` · `expIsVideo` · `expAspect` · `expRes` · `expSub` · `expNote` · `closeExport` · `doExport` · `expOkBg` · `expOkLabel` · `confirmOpen` · `cTitle` · `cBody` · `cHasInput` · `cText` · `onCText` · `cPlaceholder` · `cHasChoices` · `cChoices` · `confirmCancel` · `confirmOk` · `cOkBg` · `cOk` · `toastOpen` · `toastText` · `toastHasAct` · `toastActFn` · `toastActLabel`

## C-5 检查代码（17）

`QUOTE_NOT_FOUND` · `QUOTE_MATCH_LOW` · `QUOTE_TOO_LONG` · `QUOTE_AUDIO_NOISY` · `QUOTE_TEXT_DIFFERS` · `SPEAKER_UNNAMED` · `JUMP_CUT_UNCOVERED` · `MIXED_NO_NARRATION` · `MATCH_FALLBACK` · `EXPLICIT_ENTITY_NOT_COVERED` · `generated_media` · `LOW_MATCH_CONFIDENCE` · `FREEZE_PAD_EXCESSIVE` · `VISUAL_CLIP_TOO_LONG` · `NARRATION_SPEAKING_RATE` · `CONTEXTUAL_BROLL_OVERLAY` · `FACT_CHECK`

## C-6 模板静态文案索引（105 条）

- 服务正在维护，暂时不能新建作品。已做好的作品还能看。
- ＋ 新作品
- 我的作品
- 看一条范例作品
- 现为固定 72h TTL，发布/保留都不会延长；retain_until 与到期倒计时字段
- 需新增后端
- 三步，把你的稿子变成一条新闻视频
- 1 写稿
- · 粘贴或现写
- 2 传素材
- · 手机拍的就行
- 3 选效果
- · 都有推荐
- → AI 剪好
- · 通常几分钟
- 开始写稿 →
- 先看一条范例成片
- 第 1 步
- 填入示例（演示）
- 从转写挑句子
- 去掉标点，当标题
- 复制到手机
- ✎ 建议
- 导语一句说清：何时、何地、何人、何事、为何。
- 一句话别超过 30 个字——太长会读得赶，画面也撑不住。
- 写了几样东西，就要拍到几样：“腊肉、糕点、礼盒”要三个镜头。
- 数字写阿拉伯数字，AI 会读对。
- 下一步：传素材 →
- 第 2 步
- 手机传大视频很慢。建议用电脑传素材，手机用来看片和提意见。
- 来自 /api/config/limits：单文件 ≤ 500 MiB · 总量 ≤ 5 GB · ≤ 20 个 · 单文件 ≤ 30 分 · 总时长 ≤ 60 分 · 时长与首帧由浏览器读取
- 纯前端可做
- · 断点续传 / 幂等键
- 把视频或照片拖进来，或点击选择
- 这个模式不用人声，说话会被压低、只用画面。想用原声？
- 切到「旁白 + 原声」
- 预转写 = 上传后立即转写 + 分说话人 + 词级时间戳；稿句对齐为前端 bigram 相似度模拟，阈值 0.85 / 0.6
- · 填好名字和身份，会做成人名条
- 拍摄充足度：
- 每句话都拍到了吗？点一下告诉我
- 保守估算：每个视频 max(1, 时长÷12s) 个镜头，有人说话的采访算 1 个；成片时长 = 字数 ÷ 所选播音速度（目标字/分）。真实镜头数在阶段 3 才知道；镜头 &lt; 句数会在阶段 6 失败（ISSUE-01）且已产生费用 → 在此前置校验
- 🎬 小贴士
- 远景、中景、特写都拍一点；每个场景 10–20 秒就够。
- 有人说话的镜头会变成现场原声——让对方一句话说完，15 秒以内最好。
- 没同意出镜的人不要拍正脸；采访前先问一句。
- iPhone 拍的视频读不出时长？相机设置里选“兼容性最好”。
- 同一个视频不要传两次。
- ← 上一步
- 下一步：选效果 →
- 第 3 步
- voice · 记者录音上传与对齐
- 播音速度
- 整体感觉
- 字幕样式
- caption_style · 需新增后端
- 跳切怎么处理
- jump_cut_cover broll|zoom|hard · 空镜不足自动降级 zoom
- 原声段的字幕
- 稿子和素材会上传到 AI 服务（含第三方云）处理。
- 匿名限额 2 次/时/浏览器 · 5 次/时/出口 IP · 全站 10 次/时 · 并发 1 · 待处理 ≤5 → 提交即先上传、服务端排队（已定：先收后做）；服务端队列位置查询
- 开始制作
- 通常需要几分钟。可以先去做别的——做好后会出现在左边「我的作品」里。
- 这次没做成
- 删除这个作品
- 上传进度为前端 XHR（纯前端可做）；上传完成后才占名额（已定：先收后做）· 提交顺序在服务端队列，队列位置/ETA 接口
- 取消并删除
- 恢复这个版本
- 旧版视频 GET …/video?revision=n 已支持 · 恢复为当前版 需新增后端
- 复制一份
- 导出 / 分享
- 现在该做什么
- 空格 播放/暂停 · ← → 上一句/下一句
- Delete 标记删掉这句 · 点进度条跳转
- AI生成示意画面
- 一格一句话，宽=时长 · 点一格就能改它 ·
- 现场原声 / 我的配音
- 不太确定
- AI 生成
- 凑数画面
- 发布前检查
- 记下修改
- 上次换画面没成功：
- ① 改这句话
- ✎ 改字
- 改字 / 录音 / 调语速：remix 仅 keep_sentence_ids、preferences 建任务时固定、Permissions-Policy 禁用麦克风
- 要改这句的字，回到第 1 步改稿后重新提交（会用掉 1 次提交机会）。改字、录音、调语速即将推出。
- ② 换个画面
- · 只改画面，旁白字幕不变 · 还有
- 个没用过的镜头
- 没用过的镜头都用完了——删掉这句，或回去多传素材。
- 全镜头浏览与直接选镜 · 当前 API 重剪跑 7–10，换镜跑 6/9/10，两者需分两次提交；合并提交与批量换镜
- ③ 不要这句
- · 其他句子照原样，至少留一句
- ① 剪短这句
- · 点要保留的词 · 只能剪短，不能改字
- 记下剪短
- 词级时间戳 · 切点吸附词边界与静音（≤ 300ms）· 头留 120ms 尾留 200ms · 相邻原声间隙 150ms · remix 7–10
- ② 换一段
- alt_takes / take_id · 改成旁白 = 删原声句 + 新旁白句（remix 6–10）· 说话人名字只重跑 8–10
- 全部撤销
- 这个作品已经不在了
- 可能已被删除，或者过了保留期被清理了。
- 从列表移除
- 导出什么
- 后端现只输出 1080p 16:9 MP4；GIF / MP3 / SRT / 封面图 / 竖屏正方形裁切 / 字幕样式 / 720p 转码
