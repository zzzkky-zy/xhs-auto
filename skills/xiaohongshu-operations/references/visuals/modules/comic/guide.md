# 漫画

完整设计步骤与示例见 [完整制作方法](method.md)，再读取本页所需的预设及参考；使用当前宿主能力和私人配置。

由 sweety-comic 1.56.1 的实际 SKILL 和资源适配；[来源](../../UPSTREAM.md)。用人物行动与分镜解释问题。先判断故事动作能否承担内容，再选画风 × 基调 × 布局；普通 manga + neutral 不自动拥有 ohmsha 的特殊规则。统一流程与 [Naturalizer](../naturalizer/guide.md) 适用于角色参考表、封面和每页漫画。

## 完整预设体系

| 维度 | 全部可用值与完整定义 |
| --- | --- |
| 画风 | [ligne-claire 清线](references/art-styles/ligne-claire.md)、[manga 日漫](references/art-styles/manga.md)、[realistic 数字绘画](references/art-styles/realistic.md)、[ink-brush 水墨](references/art-styles/ink-brush.md)、[chalk 粉笔](references/art-styles/chalk.md) |
| 基调 | [neutral 中性](references/tones/neutral.md)、[warm 温馨](references/tones/warm.md)、[dramatic 戏剧](references/tones/dramatic.md)、[romantic 浪漫](references/tones/romantic.md)、[energetic 活力](references/tones/energetic.md)、[action 动作](references/tones/action.md) |
| 分镜布局 | [standard 4–6 格](references/layouts/standard.md)、[cinematic 2–4 宽格](references/layouts/cinematic.md)、[dense 6–9 格](references/layouts/dense.md)、[splash 主格+辅助格](references/layouts/splash.md)、[mixed 不规则节奏](references/layouts/mixed.md)、[webtoon 单列竖读](references/layouts/webtoon.md) |
| 特殊预设 | [ohmsha](references/presets/ohmsha.md) = manga + neutral + 道具/动作隐喻、师生角色与揭示过程；[wuxia](references/presets/wuxia.md) = ink-brush + action + 气流/打斗/环境；[shoujo](references/presets/shoujo.md) = manga + romantic + 眼部、装饰和情绪节拍 |

定义文件保留完整线条、角色、色值、材质、光线、对白、适配与避免项。特殊预设必须读全文，不能仅展开 art/tone 就结束；ohmsha 还读 [教学隐喻指导](references/ohmsha-guide.md)。允许表现力，仍减少重复动作和无信息装饰；wuxia 的动势、shoujo 的情绪装饰不能全删掉变成讲话头像。

## 匹配

完整信号与兼容矩阵见 [自动选择](references/auto-selection.md)。普通默认 ligne-claire + neutral + standard；教程/概念可 ohmsha，个人故事可 ligne-claire + warm，武侠可 wuxia，浪漫校园可 shoujo。历史内容需要准确时代服饰与工具，可 realistic + neutral，不启用被移除的基调或其旧别名。不要为普通求职条目硬加打斗或恋爱剧情。

小红书沿用 1080×1440、3:4、整帖最多 5 页含封面；角色参考表不计作发帖页。角色表和旧稿放过程资料区，最终发帖 PNG 另存交付图片目录，格式检查只指向该最终目录。下文 characters/、prompts/ 等过程路径相对于本帖过程资料区。布局格数是阅读结构参考，文字或动作放不下时拆页、删重复或重排，不硬缩字体。4:3、16:9 是任务可选通用比例，不自动切换。

## 制作与续做

1. 读 [分析框架](references/analysis-framework.md)，确定读者、核心问题、原始事实、冲突、转折与学会后的动作。真实记录依据来源；教学/虚构性质只在内部资料注明，不冒充亲历。成品用场景和人物行动叙事，不在标题、旁白、对白或页脚添加示例身份声明，执行[禁用规则](../../../audience-copy.md)。
2. 按 [角色模板](references/character-template.md) 固定外观、衣着、配件、色值、比例、表情及必要年龄变化；按 [分镜模板](references/storyboard-template.md) 写每页每格的位置、景别、人物动作、道具、环境与准确对白。Humanizer 从起草参与，冻结文字后出图。
3. Naturalizer 检查每格的姿态支撑、视线、手与工具接触、道具功能、尺度/遮挡及场景连续性；夸张动作、气流、抽象隐喻按预设表达，不一律改成照片真实感。
4. 用 [完整制作流程](references/workflow.md) 先做并实看角色参考表，再逐页调用宿主出图能力。支持参考图时每页实际传同一角色表；不支持时嵌入角色完整描述并实查一致性，不能假称传了图。
5. 每页将 [漫画基础规则](references/base-prompt.md)、[单页提示词](references/page-prompt-template.md)、所选定义和 Naturalizer 约束合并。生成一页、实看分镜/文字/角色、保存后才做下一页。复杂一页先排格与对白区，再处理动作，最后局部修字，单轮一个目标。
6. 内页按统一流程最后贴原品牌；保留无品牌稿和最终稿。封面可用本漫画方案，也可走独立封面模块，但共享角色、色值、材质和字形；不把角色表误当发帖页。
7. 中断或部分重做按 [局部流程](references/partial-workflows.md)，先更新对应提示词，只接未完成/不合格页。页序与文件引用一致，已通过页保留。不自动合并 PDF、安装 Bun 或调用不存在的上游脚本。

完成记录分清分镜完成、角色表已查、各页生成/检查/最终稿及待修目标。静态资源完整不等于角色一致性或漫画效果已经验收。
