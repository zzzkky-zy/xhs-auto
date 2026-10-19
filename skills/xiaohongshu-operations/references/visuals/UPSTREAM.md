# 上游来源、范围与适配

固定来源：[threerocks/sweety-skills](https://github.com/threerocks/sweety-skills/tree/3ddcaeb80310daf991797ad2772bba82709d6f3e)，提交 `3ddcaeb80310daf991797ad2772bba82709d6f3e`，核对日期 2026-10-15。按实际 SKILL 和资源读取，不采用上游 README 的旧数量。

| 来源 | 实际版本 | 本包内容 |
| --- | --- | --- |
| sweety-infographic | 1.56.1 | 21 布局、14 画风，含变体、数据结构、匹配、内容和提示词模板 |
| sweety-cover-image | 1.57.0 | 6 构图、10 配色、7 渲染、4 文字级别、3 氛围、4 字体、24 组合及兼容矩阵 |
| sweety-comic | 1.56.1 | 5 画风、6 基调、6 分镜、3 特殊预设，含角色、分镜、提示词、逐页/局部流程 |
| sweety-image-naturalizer | 1.79.2 | 完整 Pinterest/VHTC 体系、七类检查与来源，加上所有图片的生成前后接入 |

[资源目录](resource-catalog.json) 是本地内容索引，不是新增 MCP 参数；[来源与哈希清单](upstream-sources.json) 记录读取过的实际源文件、源 SHA-256、去留理由、随包文件与当前 SHA-256。预设定义和必要参考已随包保存，正常制作无需联网补齐。各模块guide连接当前完整method与全部有效预设。来源哈希和适配记录保留，停用历史快照和旧命令不随包。

## 精确排除

- 小红书系列图画风移除 bold、minimal、pop、retro 的文件、组合、推荐及风格专属元素段。
- 信息图画风移除 cyberpunk-neon、bold-graphic、corporate-memphis、ui-wireframe、subway-map、knolling；布局保留全部 21 个。依赖这些风格的旧推荐重写为有效组合，旧 graphic-novel/pop-art 等指向已排除画风的别名不保留为选项。
- 封面移除 vintage 组合名；漫画移除 vintage 基调及其 sepia 旧别名推荐。未创建同义的新选择器恢复它们。
- 保留 cover 的 minimal 构图/组合、bold 氛围、retro 配色/组合，保留信息图 pop-laboratory、retro-pop-grid、aged-academia、pixel-art；一般描述词（如 bold outlines、vintage paper）不被当成删除指令。

## 内容适配边界

入口按页选择模块，所有图片先用 Humanizer 固定文字、Naturalizer 编写具体视觉方案，再逐张生成检查。保留风格表现力，减少无用堆叠。上游“缩小字塞满角落”、强制绘制品牌及与现行运营画幅/页数冲突的通用例子已作局部适配。

上游的供应商回退、密钥配置、安装、首次 EXTEND.md 设置、生成水印和合并 PDF 不属于本轮内容能力，不接入、不让 agent 联网补装。参考图按宿主真实能力处理；现有任务配置、状态和授权不变。未复制这些工程资源的原因逐文件记在来源清单；上游信息图 SKILL 引用但未实际提供的 config 文件也不伪造。

现有系列图的 7 个完整画风与 8 个布局沿用接手时源码并清理引用；这是保留的本地内容，不冒称逐字等同于本次固定上游。其进入本轮前的文件哈希在工程接入清单所指的差异基线中记录。

## 许可

上游 README 和插件清单声明 MIT；可得的原文声明保存在 [许可证据](UPSTREAM-LICENSE-NOTICE.txt)。此提交没有根目录或四模块独立的完整 LICENSE 文件；没有替作者补造版权人、年份或许可文件，也没有拿无关依赖许可证代替。上游 README 的 ClawHub MIT-0 说明不被套用到这次 GitHub 源码。

工程侧公开分发前应核对这个许可材料缺口；本轮仅完成用户要求的本地内容适配，没有上传、公开分发或改写现有 Humanizer/MCP 的许可。品牌原素材不因上游声明而获得额外分发授权。

## gbro-cover-design

完整来源是 [pyang5166/gbro-cover-design](https://github.com/pyang5166/gbro-cover-design)，固定提交与逐文件 Git blob / SHA256 见[独立来源清单](modules/gbro-cover-design/manifest.json)。[原目录](modules/gbro-cover-design/upstream/README.md)保留10种完整构图模板、8组示例与原MIT许可，制作入口清除停用的首次配置和强制三轮提问；实际制作从[薄连接](modules/gbro-cover-design/guide.md)进入，再完整读制作方法 和选中模板、示例。

用户确认真人只用于封面，必须使用首次配置上传的真实人脸并保持长相一致，不能采用原文的“无图就描述人物”分支；内页无真人。原 `config.md` / `assets/my-face.png` 连接到现有包外私有 content_profile，不把人脸放进本目录。用户已授权模型按原方法结合爆款和内容自主选择，不逐封面重问原三轮制作选项；表情、背景、字体和字色的完整选项仍保留。生成提示词后沿当前宿主实际能力制作和实看，不另装供应商、要求用户手动跑图或扩大外发授权。

用户同时确认[每批每帖不同预设风格](batch-styles.md)，数量按实际约定，原“5 帖至少 3 种”不能降低新增要求。该要求接在选择前和整批成图审查，原选择方法及其他质量要求继续执行。
