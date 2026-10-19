# 独立封面

完整设计步骤与示例见 [完整制作方法](method.md)，再读取本页所需的预设及参考；使用当前宿主能力和私人配置。

由 sweety-cover-image 1.57.0 的实际 SKILL 和完整内容资源适配；[来源](../../UPSTREAM.md)。封面让读者理解主题并愿意读内页/正文，不承担全文。六维独立选择；默认值是候选，不覆盖任务和同帖视觉约定。每张先用 [Naturalizer](../naturalizer/guide.md) 形成方案，再生成和检查。

批量制作先按[每帖不同预设](../../batch-styles.md)结合爆款和内容选择，不逐帖套同一个默认组合。用户已允许真人封面且已提供模特照片后，需要真人出镜时可自主选择新增的 [gbro-cover-design](../gbro-cover-design/guide.md)，完整读取其原方法与模板；人脸必须用用户首次配置上传的真实照片，实际传入工具并核对长相一致。普通参考处理中的“不能传图就描述特征”不能用来生成人脸；内页仍无真人。

## 六个维度

| 维度 | 全部可用值 | 完整制作定义 |
| --- | --- | --- |
| type 构图 | hero、conceptual、typography、metaphor、scene、minimal | [比例、空间和主体安排](references/types.md) |
| palette 配色 | warm、elegant、cool、dark、earth、vivid、pastel、mono、retro、duotone | [warm](references/palettes/warm.md)、[elegant](references/palettes/elegant.md)、[cool](references/palettes/cool.md)、[dark](references/palettes/dark.md)、[earth](references/palettes/earth.md)、[vivid](references/palettes/vivid.md)、[pastel](references/palettes/pastel.md)、[mono](references/palettes/mono.md)、[retro](references/palettes/retro.md)、[duotone](references/palettes/duotone.md) |
| rendering 渲染 | flat-vector、hand-drawn、painterly、digital、pixel、chalk、screen-print | [flat-vector](references/renderings/flat-vector.md)、[hand-drawn](references/renderings/hand-drawn.md)、[painterly](references/renderings/painterly.md)、[digital](references/renderings/digital.md)、[pixel](references/renderings/pixel.md)、[chalk](references/renderings/chalk.md)、[screen-print](references/renderings/screen-print.md) |
| text 文字 | none、title-only、title-subtitle、text-rich | [分区、密度与层级](references/dimensions/text.md) |
| mood 氛围 | subtle、balanced、bold | [对比、饱和、线条、兼容性](references/dimensions/mood.md) |
| font 字体 | clean、handwritten、serif、display | [字形与全部交互矩阵](references/dimensions/font.md) |

常规默认 title-only / balanced / clean，其余按内容推断。clean 与手绘/粉笔不佳时读兼容表选择适合字形，不机械套默认。小红书沿用 1080×1440、3:4；2.35:1、16:9、4:3、3:2、1:1 等通用比例只有任务指定时使用。

minimal 构图、minimal 封面组合、bold 氛围、retro 配色/封面组合均保留，和系列图画风不是同一个维度。全部 24 个有效组合及展开值见 [风格组合](references/style-presets.md)，显式 palette/rendering 优先于组合中的对应值；没有被接受的旧名不自动映射回被排除选项。

## 匹配与制作

1. 从最终正文提取主题、读者、承诺、准确标题与一个主视觉隐喻。标题在文字制作阶段经 Humanizer 核对，封面阶段使用这份冻结稿；不自行换标题或从他人文章照搬标题。
2. 按 [自动选择规则](references/auto-selection.md) 逐维判断：动作/公告可 hero，方法/系统可 conceptual，观点可 typography，抽象概念可 metaphor，故事可 scene，单一聚焦才 minimal。不能每篇默认纯文字卡；typography 仅在标题本身承担表达时使用。
3. 读对应 palette/rendering 和文字/氛围/字体定义，核对 [兼容性矩阵](references/compatibility.md)。组合名只展开配色+渲染，不能代替构图等其他维度。内容需要时用 [视觉元素库](references/visual-elements.md) 选有意义的物件，不把火箭、齿轮、贴纸堆满。
4. 有参考图时读 [参考处理](references/workflow/reference-images.md)，提取实际构图、材质、密度、字形与颜色。品牌部分只标记留位与指定原素材，不写进重画提示词。参考只取风格/颜色时不冒充实际传图。
5. [选项收敛](references/workflow/confirm-options.md)：已有明确选择就沿用；可直接判断的制作选择写明理由继续，不为每个维度都重复问。只有影响内容承诺、事实或无法判定的取舍才向用户说明。
6. Naturalizer 明确主体、场景、层级、光线/材质/尺度及姿态/功能；平面渲染保留其无渐变/无投影规则。将这些填入 [封面提示词](references/workflow/prompt-template.md)，并按 [基础规则](references/base-prompt.md) 和 [共用模板](../../prompt-template.md) 展开所有选定维度与准确文字。
7. 用宿主已有出图能力逐张生成、实看和保存。封面沿用无品牌；若同帖后续页采用其他模块，传递颜色、字形、材质和主物件约定，不强行复刻封面布局。

## 交付与修改

记录六维、画布、准确标题、参考实际使用方式、提示词和图稿路径。改图先保留上一稿并更新提示词，只修本轮目标；按 [统一审查](../../review.md) 检查文字、价值承诺和主视觉。上游供应商优先级、API Key、EXTEND.md 首次设置和生成水印不接入；没有出图能力则保存方案待完成，不增加外部服务。
