
# 封面图生成器

为文章生成优雅的封面图，支持五维度定制。

## 五维度

| 维度 | 可选值 | 默认 |
|------|--------|------|
| **类型** | hero, conceptual, typography, metaphor, scene, minimal | 自动 |
| **配色** | warm, elegant, cool, dark, earth, vivid, pastel, mono, retro, duotone | 自动 |
| **渲染风格** | flat-vector, hand-drawn, painterly, digital, pixel, chalk, screen-print | 自动 |
| **文字** | none, title-only, title-subtitle, text-rich | title-only |
| **氛围** | subtle, balanced, bold | balanced |
| **字体** | clean, handwritten, serif, display | clean |

自动选择规则：[references/auto-selection.md](references/auto-selection.md)

## 画廊

**类型**：hero, conceptual, typography, metaphor, scene, minimal
→ 详情：[references/types.md](references/types.md)

**配色方案**：warm, elegant, cool, dark, earth, vivid, pastel, mono, retro, duotone
→ 详情：[references/palettes/](references/palettes/)

**渲染风格**：flat-vector, hand-drawn, painterly, digital, pixel, chalk, screen-print
→ 详情：[references/renderings/](references/renderings/)

**文字密度**：none（纯视觉）| title-only（默认）| title-subtitle | text-rich（含标签）
→ 详情：[references/dimensions/text.md](references/dimensions/text.md)

**氛围强度**：subtle（低对比度）| balanced（默认）| bold（高对比度）
→ 详情：[references/dimensions/mood.md](references/dimensions/mood.md)

**字体**：clean（无衬线）| handwritten | serif | display（粗体装饰）
→ 详情：[references/dimensions/font.md](references/dimensions/font.md)

## 文件结构

输出目录取决于 `default_output_dir` 偏好设置：
- `same-dir`：`{article-dir}/`
- `imgs-subdir`：`{article-dir}/imgs/`
- `independent`（默认）：`cover-image/{topic-slug}/`

```
<output-dir>/
├── source-{slug}.{ext}    # 原始文件
├── refs/                  # 参考图片（如有提供）
│   ├── ref-01-{slug}.{ext}
│   └── ref-01-{slug}.md   # 描述文件
├── prompts/cover.md       # 生成提示词
└── cover.png              # 输出图片
```

**Slug 规则**：2-4 个单词，kebab-case。冲突时追加 `-YYYYMMDD-HHMMSS`

## 工作流程

读取当前私人配置，使用宿主现有生图能力；非必要偏好缺失跳过。输出1080×1440，已授权的画风选择由agent完成。

### 步骤 1：分析内容

1. **保存参考图**（如有提供）→ [references/workflow/reference-images.md](references/workflow/reference-images.md)
2. **保存原始内容**（如粘贴内容，保存为 `source.md`）
3. **分析内容**：主题、语调、关键词、视觉隐喻
4. **深度分析参考图** ⚠️：提取具体、明确的元素（见 reference-images.md）
5. **检测语言**：对比源内容、用户输入、私人配置偏好
6. **确定输出目录**：按文件结构规则

**⚠️ 参考图中有人物时：**

如果参考图包含需要出现在封面中的**人物**：

- **模型支持 `--ref`**（默认情况）：将图片复制到 `refs/`，生成时通过 `--ref` 传递。无需描述文件——模型可以直接看到面部。
- **不支持参考图时**：不能以描述替代真人参考照；改用支持参考图的已可用工具，缺能力则报告该项未完成。

完整决策表见 [reference-images.md](references/workflow/reference-images.md)。

### 步骤 2：确定选项

根据内容和主参考自主确定类型、配色、渲染、文字密度、氛围与字体，保持本批预设不同。

### 步骤 3：创建提示词

保存到 `prompts/cover.md`。模板：[references/workflow/prompt-template.md](references/workflow/prompt-template.md)

**关键 - 前置元数据中的参考图**：
- 文件已保存到 `refs/` → 添加到 frontmatter 的 `references` 列表
- 仅口头提取风格（无文件）→ 省略 `references`，在正文中描述
- 写入前 → 验证：`test -f refs/ref-NN-{slug}.{ext}`

**正文中的参考元素**必须详细描述，带 "MUST"/"REQUIRED" 前缀，附集成方案。

### 步骤 4：生成图片

1. **备份已有** `cover.png`（如重新生成）
2. **检查图像生成技能**；使用宿主当前可用能力
3. **处理参考图**（来自提示词 frontmatter）：
   - `direct` 用法 → 通过 `--ref` 传递（使用支持参考图的后端）
   - `style`/`palette` → 提取特征，追加到提示词
4. **生成**：调用技能，传入提示词文件、输出路径、宽高比
6. 单次失败：自动重试一次

### 步骤 5：完成报告

```
封面图已生成！

主题：[topic]
类型：[type] | 配色：[palette] | 渲染：[rendering]
文字：[text] | 氛围：[mood] | 字体：[font] | 比例：[ratio]
标题：[title 或 "纯视觉"]
语言：[lang] | 水印：[启用/禁用]
参考图：[N 张 或 "提取风格" 或 "无"]
服务商：[provider] / [model]
位置：[directory path]

文件：
✓ source-{slug}.{ext}
✓ prompts/cover.md
✓ cover.png
```

## 图片修改

| 操作 | 步骤 |
|------|------|
| **重新生成** | 备份 → 先更新提示词文件 → 重新生成 |
| **更改维度** | 备份 → 确认新值 → 更新提示词 → 重新生成 |

## 构图原则

- **留白**：40-60% 呼吸空间
- **视觉锚点**：主体元素居中或偏左
- **人物**：需要真人封面时使用已确认参考照并实际传图，保持同一人
- **标题**：使用已审的本帖准确封面文字

## 参考文档

**维度**：[text.md](references/dimensions/text.md) | [mood.md](references/dimensions/mood.md) | [font.md](references/dimensions/font.md)
**配色方案**：[references/palettes/](references/palettes/)
**渲染风格**：[references/renderings/](references/renderings/)
**类型**：[references/types.md](references/types.md)
**自动选择**：[references/auto-selection.md](references/auto-selection.md)
**风格预设**：[references/style-presets.md](references/style-presets.md)
**兼容性**：[references/compatibility.md](references/compatibility.md)
**视觉元素**：[references/visual-elements.md](references/visual-elements.md)
