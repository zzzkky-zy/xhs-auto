# 专业信息图

完整设计步骤与示例见 [完整制作方法](method.md)，再读取本页所需的预设及参考；使用当前宿主能力和私人配置。

由 sweety-infographic 1.56.1 的实际 SKILL 和资源适配；固定来源见 [上游记录](../../UPSTREAM.md)。先确定要让读者看懂的关系，再选布局 × 画风。通用要求见 [统一流程](../../../visual-production.md)，每张出图前后都应用 [Naturalizer](../naturalizer/guide.md)。这里是运营入口内的制作模式，不新增命令或生图供应商。

## 完整布局

| 布局 | 适用关系 | 定义 |
| --- | --- | --- |
| `linear-progression` | 有起止的时间线、步骤 | [结构/标注/搭配](references/layouts/linear-progression.md) |
| `binary-comparison` | 两对象同维度对比 | [定义](references/layouts/binary-comparison.md) |
| `comparison-matrix` | 多对象、多因素矩阵 | [定义](references/layouts/comparison-matrix.md) |
| `hierarchical-layers` | 重要性、优先级或内外层次 | [定义](references/layouts/hierarchical-layers.md) |
| `tree-branching` | 父子分类和分支 | [定义](references/layouts/tree-branching.md) |
| `hub-spoke` | 中心与关联项 | [定义](references/layouts/hub-spoke.md) |
| `structural-breakdown` | 组件、剖面、拆解 | [定义](references/layouts/structural-breakdown.md) |
| `bento-grid` | 多主题概览，主格与辅助格 | [定义](references/layouts/bento-grid.md) |
| `iceberg` | 表面与隐藏部分 | [定义](references/layouts/iceberg.md) |
| `bridge` | 现状、差距、达到目标的方法 | [定义](references/layouts/bridge.md) |
| `funnel` | 逐层筛选或转化 | [定义](references/layouts/funnel.md) |
| `isometric-map` | 地点、空间及其路径 | [定义](references/layouts/isometric-map.md) |
| `dashboard` | 同口径指标与状态 | [定义](references/layouts/dashboard.md) |
| `periodic-table` | 规则一致的分类集合 | [定义](references/layouts/periodic-table.md) |
| `comic-strip` | 单图短序列解释 | [定义](references/layouts/comic-strip.md) |
| `story-mountain` | 起承、张力、高潮、回落 | [定义](references/layouts/story-mountain.md) |
| `jigsaw` | 部分之间的互补与缺口 | [定义](references/layouts/jigsaw.md) |
| `venn-diagram` | 集合的差异与交集 | [定义](references/layouts/venn-diagram.md) |
| `winding-roadmap` | 旅程与里程碑 | [定义](references/layouts/winding-roadmap.md) |
| `circular-flow` | 周期与反馈循环 | [定义](references/layouts/circular-flow.md) |
| `dense-modules` | 有充分数据的多功能模块指南 | [定义](references/layouts/dense-modules.md) |

## 完整画风

| 画风 | 识别特征 | 配色、笔触、元素、文字与适用内容 |
| --- | --- | --- |
| `craft-handmade` | 手绘 / 纸艺两分支，暖纸色与卡通 | [完整定义](references/styles/craft-handmade.md) |
| `claymation` | 黏土、指纹、微缩场景与柔影 | [定义](references/styles/claymation.md) |
| `kawaii` | 粉彩、圆润大眼、可爱图解 | [定义](references/styles/kawaii.md) |
| `storybook-watercolor` | 水彩渗色、纸纹、轻柔线条 | [定义](references/styles/storybook-watercolor.md) |
| `chalkboard` | 深色黑板和彩色粉笔纹理 | [定义](references/styles/chalkboard.md) |
| `aged-academia` | 古典科学笔记 / 标本图两分支 | [定义](references/styles/aged-academia.md) |
| `technical-schematic` | 蓝图 / 等距两分支，精确结构 | [定义](references/styles/technical-schematic.md) |
| `origami` | 折纸、几何折痕与纸层深度 | [定义](references/styles/origami.md) |
| `pixel-art` | 限色、格点、像素字形 | [定义](references/styles/pixel-art.md) |
| `ikea-manual` | 装配说明线条、编号、方向箭头 | [定义](references/styles/ikea-manual.md) |
| `lego-brick` | 可见凸点、积木组合与模块结构 | [定义](references/styles/lego-brick.md) |
| `pop-laboratory` | 技术网格、坐标、荧光强调与参数 | [定义](references/styles/pop-laboratory.md) |
| `morandi-journal` | 暖纸、莫兰迪色、手账线条 | [定义](references/styles/morandi-journal.md) |
| `retro-pop-grid` | 严格格子、粗轮廓、平面复古色 | [定义](references/styles/retro-pop-grid.md) |

仅以上 14 种画风。相近词不是同一选项；最后两种含 pop/retro 的名称仍保留。画风分支写在定义内，不另造别名选择器。

## 内容到布局的匹配

先读 [分析框架](references/analysis-framework.md)，回答读者要比较什么、按什么顺序、哪些是分组/层级/因果。独立条目才考虑默认 bento-grid；已经有明确关系时不因“信息图”三个字跳过关系判断。默认画风 craft-handmade；技术精度、纸艺、黏土或像素需求按完整定义选择。

| 内容 | 可行组合 | 取舍 |
| --- | --- | --- |
| 时间线 / 分步动作 | linear-progression + craft-handmade / ikea-manual | 时间和动作先后明确 |
| 两对象差别 | binary-comparison + craft-handmade / technical-schematic | 左右同口径，不把观点画成统计 |
| 多项选择比较 | comparison-matrix + technical-schematic / morandi-journal | 行列和单位一致 |
| 层级 / 分类 / 交集 | hierarchical-layers / tree-branching / venn-diagram + craft-handmade | 按真实关系选结构 |
| 组件与系统 | structural-breakdown + technical-schematic | 对应部件、比例和连线准确 |
| 指标 / 漏斗 | dashboard / funnel + technical-schematic | 没有真实数据就不用数值图形 |
| 周期 | circular-flow + craft-handmade | 闭环必须有真实反馈 |
| 学习旅程 | winding-roadmap + storybook-watercolor | 里程碑可循，不强加精确时长 |
| 多维实用指南 | dense-modules + morandi-journal / pop-laboratory / retro-pop-grid | 信息够且手机可读，减少无用装饰 |

“高密度信息大图 / high-density-info”可优先考察 dense-modules 及其三种搭配，但仍需检验数据和可读性，不硬塞 6–7 块或添加伪参数。每个布局文件也提供已清理的有效搭配。

## 制作步骤

1. 明确 1–3 个读者学习目标，提取原始数据、专名、引用、条件和来源；原创场景的材料性质只在内部资料注明，用条件/场景表达组织内容，不冒充研究事实。正文和图中不得添加“教学/练习示例”等声明，执行[禁用规则](../../../audience-copy.md)。
2. 用 [结构化内容模板](references/structured-content-template.md) 组织分区、概念、文字标签与视觉对象。Humanizer 可编辑解释文字，数据/引用原样保留；冻结最终清单后不让图像模型再改。
3. 读所选布局和画风全文，列清节点/分组/连线/图例，决定文字区和主视觉；一张图信息过多则重排、拆页或改正文分工。
4. Naturalizer 将光线、材质、尺度、功能与阅读路径落实到本图；平面图不加错误摄影深度，技术图不为“自然”扭曲精确线条。
5. 用 [模块基础提示词](references/base-prompt.md) 加 [共用单图模板](../../prompt-template.md) 形成完整提示词，实际展开布局与画风规则、准确文字及避免项。小红书默认 3:4、1080×1440；通用 landscape=16:9、portrait=9:16、square=1:1 或任意 W:H 仅在任务指定时采用。
6. 单张生成、对照结构与文字实看、必要时分轮修正，再按统一流程叠加品牌和保存。图表数值、箭头和标注一旦错位不能带错交付。
