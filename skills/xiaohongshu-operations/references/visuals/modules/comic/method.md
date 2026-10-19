
# Knowledge Comic Creator

Create original knowledge comics with flexible art style × tone combinations.

## Visual dimensions

### Art Styles (画风)

| Style | 中文 | Description |
|-------|------|-------------|
| `ligne-claire` | 清线 | Uniform lines, flat colors, European comic tradition (Tintin, Logicomix) |
| `manga` | 日漫 | Large eyes, manga conventions, expressive emotions |
| `realistic` | 写实 | Digital painting, realistic proportions, sophisticated |
| `ink-brush` | 水墨 | Chinese brush strokes, ink wash effects |
| `chalk` | 粉笔 | Chalkboard aesthetic, hand-drawn warmth |

### Tones (基调)

| Tone | 中文 | Description |
|------|------|-------------|
| `neutral` | 中性 | Balanced, rational, educational |
| `warm` | 温馨 | Nostalgic, personal, comforting |
| `dramatic` | 戏剧 | High contrast, intense, powerful |
| `romantic` | 浪漫 | Soft, beautiful, decorative elements |
| `energetic` | 活力 | Bright, dynamic, exciting |
| `action` | 动作 | Speed lines, impact effects, combat |

### Preset Shortcuts

Presets with special rules beyond art+tone:

| Preset | Equivalent | Special Rules |
|--------|-----------|---------------|
| `--style ohmsha` | `--art manga --tone neutral` | Visual metaphors, NO talking heads, gadget reveals |
| `--style wuxia` | `--art ink-brush --tone action` | Qi effects, combat visuals, atmospheric elements |
| `--style shoujo` | `--art manga --tone romantic` | Decorative elements, eye details, romantic beats |

### Compatibility Matrix

| Art Style | ✓✓ Best | ✓ Works | ✗ Avoid |
|-----------|---------|---------|---------|
| ligne-claire | neutral, warm | dramatic, energetic | romantic, action |
| manga | neutral, romantic, energetic, action | warm, dramatic | — |
| realistic | neutral, warm, dramatic | action | romantic, energetic |
| ink-brush | neutral, dramatic, action | warm | romantic, energetic |
| chalk | neutral, warm, energetic | — | dramatic, action, romantic |

Details: [references/auto-selection.md](references/auto-selection.md)

## Auto Selection

Content signals determine default art + tone + layout (or preset):

| Content Signals | Recommended |
|-----------------|-------------|
| Tutorial, how-to, programming, educational | **ohmsha** preset |
| Pre-1950, classical, ancient | realistic + neutral |
| Personal story, mentor | ligne-claire + warm |
| Martial arts, wuxia | **wuxia** preset |
| Romance, school life | **shoujo** preset |
| Biography, balanced | ligne-claire + neutral |

**When preset is recommended**: Load `references/presets/{preset}.md` and apply all special rules.

Details: [references/auto-selection.md](references/auto-selection.md)

## File Structure

Output directory: `comic/{topic-slug}/`
- Slug: 2-4 words kebab-case from topic (e.g., `alan-turing-bio`)
- Conflict: append timestamp (e.g., `turing-story-20260118-143052`)

**Contents**:
| File | Description |
|------|-------------|
| `source-{slug}.{ext}` | Source files |
| `analysis.md` | Content analysis |
| `storyboard.md` | Storyboard with panel breakdown |
| `characters/characters.md` | Character definitions |
| `characters/characters.png` | Character reference sheet |
| `prompts/NN-{cover\|page}-[slug].md` | Generation prompts |
| `NN-{cover\|page}-[slug].png` | Generated images |

## Language Handling

**Detection Priority**:
1. `--lang` flag (explicit)
2. current private language preference
3. User's conversation language
4. Source content language

**Rule**: Use user's input language or saved language preference for ALL interactions:
- Storyboard outlines and scene descriptions
- Image generation prompts
- User selection options and confirmations
- Progress updates, questions, errors, summaries

Technical terms remain in English.

## Workflow

Read current private preferences, analyze content, choose style/tone/layout, make storyboard and character sheet, assemble prompts, generate and visually review each page. Use the current task authorization; no additional preference setup or mandatory confirmation.

### Step 7: Image Generation ⚠️ CRITICAL

**Character reference is MANDATORY for visual consistency.**

**7.1 Generate character sheet first**:
- **Backup rule**: If `characters/characters.png` exists, rename to `characters/characters-backup-YYYYMMDD-HHMMSS.png`
- Invoke an installed image generation skill available in the current host
- Read that skill's `SKILL.md` and follow its documented interface rather than calling its scripts directly
- Use `characters/characters.md` as the prompt-file input
- Save output to `characters/characters.png`
- Use aspect ratio `4:3`

**Compress character sheet** (recommended):
Compress to reduce token usage when used as reference image:
- Use available image compression skill (if any)
- Or system tools: `pngquant`, `optipng`, `sips` (macOS)
- **Keep PNG format**, lossless compression preferred

**7.2 Generate each page WITH character reference**:

| Skill Capability | Strategy |
|------------------|----------|
| Supports `--ref` | Pass `characters/characters.png` with EVERY page |
| No `--ref` support | Prepend character descriptions to EVERY prompt file |

**Backup rules for page generation**:
- If prompt file exists: rename to `prompts/NN-{cover|page}-[slug]-backup-YYYYMMDD-HHMMSS.md`
- If image file exists: rename to `NN-{cover|page}-[slug]-backup-YYYYMMDD-HHMMSS.png`
- Invoke the installed image generation skill for each page
- Use `prompts/01-page-xxx.md` as the prompt-file input
- Save output to `01-page-xxx.png`
- Use aspect ratio `3:4`
- If the chosen skill supports reference images, pass `characters/characters.png` as `--ref`

**Full workflow details**: [references/workflow.md](references/workflow.md)

## References

**Core Templates**:
- [analysis-framework.md](references/analysis-framework.md) - Deep content analysis
- [character-template.md](references/character-template.md) - Character definition format
- [storyboard-template.md](references/storyboard-template.md) - Storyboard structure
- [ohmsha-guide.md](references/ohmsha-guide.md) - Ohmsha manga specifics

**Style Definitions**:
- `references/art-styles/` - Art styles (ligne-claire, manga, realistic, ink-brush, chalk)
- `references/tones/` - Tones (neutral, warm, dramatic, romantic, energetic, action)
- `references/presets/` - Presets with special rules (ohmsha, wuxia, shoujo)
- `references/layouts/` - Layouts (standard, cinematic, dense, splash, mixed, webtoon)

**Workflow**:
- [workflow.md](references/workflow.md) - Full workflow details
- [auto-selection.md](references/auto-selection.md) - Content signal analysis
- [partial-workflows.md](references/partial-workflows.md) - Partial workflow options

## Page Modification

| Action | Steps |
|--------|-------|
| **Edit** | **Update prompt file FIRST** → `--regenerate N` |
| **Add** | Create prompt at position → Generate with character ref → Renumber subsequent → Update storyboard |
| **Delete** | Remove files → Renumber subsequent → Update storyboard |

**IMPORTANT**: When updating pages, ALWAYS update the prompt file (`prompts/NN-{cover|page}-[slug].md`) FIRST before regenerating. This ensures changes are documented and reproducible.

## Notes

- Auto-retry once on generation failure
- Use stylized alternatives for sensitive public figures
- Maintain style consistency via session ID
- **Step 7.1 character sheet MUST be generated before pages** - ensures consistency
- **Step 7.2 EVERY page MUST reference characters** - use `--ref` or embed descriptions
