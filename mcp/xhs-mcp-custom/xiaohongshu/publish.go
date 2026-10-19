package xiaohongshu

import (
	"context"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"log/slog"
	"math/rand"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/input"
	"github.com/go-rod/rod/lib/proto"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

// PublishImageContent 发布图文内容
type PublishImageContent struct {
	Title               string
	Content             string
	Tags                []string
	ImagePaths          []string
	ScheduleTime        *time.Time // 定时发布时间，nil 表示立即发布
	IsOriginal          bool       // 是否声明原创
	Visibility          string     // 可见范围: "公开可见"(默认), "仅自己可见", "仅互关好友可见"
	Products            []string   // 商品关键词列表，用于绑定带货商品
	Activity            string     // 关联活动名称（匹配 .activity-name）；空则跳过
	LongArticleTemplate string     // 长文一键排版模版名（写长文→一键排版→.template-card）；空则走普通图文。找不到卡片会报错
	TopicTemplate       string     // 话题模版名（发布页 #话题旁下拉→使用/管理话题模版→.template-card 点应用）；与 LongArticleTemplate 无关。找不到则报错
}

type PublishAction struct {
	page *rod.Page
}

const (
	urlOfPublic = `https://creator.xiaohongshu.com/publish/publish?source=official`

	// contentElemTimeout 查找正文输入框的轮询窗口
	contentElemTimeout = 10 * time.Second
)

func NewPublishImageAction(page *rod.Page) (*PublishAction, error) {

	pp := page.Timeout(300 * time.Second)

	if err := ensureCreatorSession(pp); err != nil {
		return nil, err
	}

	if err := pp.Navigate(urlOfPublic); err != nil {
		return nil, errors.Wrap(err, "导航到发布页面失败")
	}

	execution.Stage(pp.GetContext(), "publish_page_ready", 20*time.Second)
	if err := waitKeyState(pp, `() => !!document.querySelector('.upload-content, .upload-input, input[type="file"], .creator-tab')`, 15*time.Second); err != nil {
		return nil, err
	}
	if err := mustClickPublishTab(pp, "上传图文"); err != nil {
		logrus.Errorf("点击上传图文 TAB 失败: %v", err)
		return nil, err
	}

	return &PublishAction{
		page: pp,
	}, nil
}

func (p *PublishAction) Publish(ctx context.Context, content PublishImageContent) error {
	// 长文模版路径可不先传图；普通图文仍要求至少 1 张图
	if content.LongArticleTemplate == "" && len(content.ImagePaths) == 0 {
		return errors.New("图片不能为空")
	}

	// 重设超时：.Context(ctx) 会替换掉 NewPublishImageAction 里 Timeout(300s) 的 deadline
	page := p.page.Context(ctx).Timeout(300 * time.Second)

	// 长文模版：写长文 → 一键排版 → template-card（尽力；失败明确报错）
	if content.LongArticleTemplate != "" {
		if err := trySelectLongArticleTemplate(ctx, page, content.LongArticleTemplate, content.Title, content.Content); err != nil {
			return errors.Wrap(err, "长文模版选择失败")
		}
		// 模版「下一步」后进入发布表单；若仍有图则尽量上传（尽力）
		if len(content.ImagePaths) > 0 {
			if err := uploadImages(page, content.ImagePaths); err != nil {
				logrus.Warnf("长文模版后上传图片失败（继续发布表单）: %v", err)
			}
		}
	} else {
		if err := uploadImages(page, content.ImagePaths); err != nil {
			return errors.Wrap(err, "小红书上传图片失败")
		}
	}

	tags := content.Tags
	if len(tags) >= 10 {
		logrus.Warnf("标签数量超过10，截取前10个标签")
		tags = tags[:10]
	}

	logrus.Infof("发布内容: title=%s, images=%v, tags=%v, schedule=%v, original=%v, visibility=%s, products=%v, activity=%s, long_tpl=%s, topic_tpl=%s", content.Title, len(content.ImagePaths), tags, content.ScheduleTime, content.IsOriginal, content.Visibility, content.Products, content.Activity, content.LongArticleTemplate, content.TopicTemplate)

	skipBody := content.LongArticleTemplate != "" // 长文路径已填过标题正文
	if err := submitPublish(ctx, page, content.Title, content.Content, tags, content.ScheduleTime, content.IsOriginal, content.Visibility, content.Products, content.Activity, content.TopicTemplate, skipBody); err != nil {
		return errors.Wrap(err, "小红书发布失败")
	}

	return nil
}

// hasPopCover 当前页面是否还有挡人的浮层。
func hasPopCover(page *rod.Page) bool {
	has, _, err := page.Has("div.d-popover")
	return err == nil && has
}

// dismissPopCover 关掉挡住发布 TAB 的浮层，按 Esc → 点空白 → 摘节点逐级降级。
// 保留摘节点这一步是因为它只要节点还在就必定生效，前两步是否奏效取决于页面
// 自己有没有写对应的处理，无从预判。
func dismissPopCover(page *rod.Page) {
	if err := page.Keyboard.Press(input.Escape); err != nil {
		logrus.Debugf("按 Esc 关闭浮层失败: %v", err)
	}
	pause(page, 200*time.Millisecond) // 技术等待：等浮层收起动画
	if !hasPopCover(page) {
		return
	}

	clickEmptyPosition(page)
	pause(page, 200*time.Millisecond)
	if !hasPopCover(page) {
		return
	}

	// 前两步都无效，退回摘节点，保证发布能继续。
	has, elem, err := page.Has("div.d-popover")
	if err != nil || !has {
		return
	}
	logrus.Warn("Esc 与点击空白都未能关闭浮层，改为移除该节点")
	if err := elem.Remove(); err != nil {
		logrus.Warnf("移除浮层失败: %v", err)
	}
}

func clickEmptyPosition(page *rod.Page) {
	pt := proto.Point{
		X: float64(380 + rand.Intn(100)),
		Y: float64(20 + rand.Intn(60)),
	}
	// 兜底操作，点不动就算了，不该 panic
	if err := humanize.ClickAt(page, pt); err != nil {
		logrus.Debugf("点击空位置失败: %v", err)
	}
}

func mustClickPublishTab(page *rod.Page, tabname string) error {
	page.MustElement(`div.upload-content`).MustWaitVisible()

	deadline := time.Now().Add(15 * time.Second)
	blockedAtLeastOnce := false

	for time.Now().Before(deadline) {
		tab, blocked, err := getTabElement(page, tabname)
		if err != nil {
			logrus.Warnf("获取发布 TAB 元素失败: %v", err)
			pause(page, 200*time.Millisecond)
			continue
		}

		if tab == nil {
			pause(page, 200*time.Millisecond)
			continue
		}

		if blocked {
			blockedAtLeastOnce = true
			logrus.Info("发布 TAB 被遮挡，尝试关闭浮层")
			dismissPopCover(page)
			pause(page, 200*time.Millisecond)
			continue
		}

		if err := humanize.Click(tab); err != nil {
			logrus.Warnf("点击发布 TAB 失败: %v", err)
			pause(page, 200*time.Millisecond)
			continue
		}

		return nil
	}

	// 区分两种失败：找不到 TAB，和找到了但浮层一直关不掉
	if blockedAtLeastOnce {
		return errors.Errorf("发布 TAB %s 一直被浮层遮挡，Esc 与点击空白都未能关闭", tabname)
	}
	return errors.Errorf("没有找到发布 TAB - %s", tabname)
}

func getTabElement(page *rod.Page, tabname string) (*rod.Element, bool, error) {
	elems, err := page.Elements("div.creator-tab")
	if err != nil {
		return nil, false, err
	}

	for _, elem := range elems {
		if !isElementVisible(elem) {
			continue
		}

		text, err := elem.Text()
		if err != nil {
			logrus.Debugf("获取发布 TAB 文本失败: %v", err)
			continue
		}

		if strings.TrimSpace(text) != tabname {
			continue
		}

		blocked, err := isElementBlocked(elem)
		if err != nil {
			return nil, false, err
		}

		return elem, blocked, nil
	}

	return nil, false, nil
}

func isElementBlocked(elem *rod.Element) (bool, error) {
	result, err := elem.Eval(`() => {
		const rect = this.getBoundingClientRect();
		if (rect.width === 0 || rect.height === 0) {
			return true;
		}
		const x = rect.left + rect.width / 2;
		const y = rect.top + rect.height / 2;
		const target = document.elementFromPoint(x, y);
		return !(target === this || this.contains(target));
	}`)
	if err != nil {
		return false, err
	}

	return result.Value.Bool(), nil
}

func uploadImages(page *rod.Page, imagesPaths []string) error {
	execution.Stage(page.GetContext(), "upload_images", 90*time.Second)
	validPaths := make([]string, 0, len(imagesPaths))
	for _, path := range imagesPaths {
		if _, err := os.Stat(path); os.IsNotExist(err) {
			logrus.Warnf("图片文件不存在: %s", path)
			continue
		}
		validPaths = append(validPaths, path)
		logrus.Infof("获取有效图片：%s", path)
	}

	// 逐张上传：每张上传后等待预览出现，再上传下一张
	for i, path := range validPaths {
		uploadInput, err := findImageUploadInput(page, i == 0)
		if err != nil {
			return errors.Wrapf(err, "查找上传输入框失败(第%d张)", i+1)
		}
		if err := uploadInput.SetFiles([]string{path}); err != nil {
			return errors.Wrapf(err, "上传第%d张图片失败", i+1)
		}

		slog.Info("图片已提交上传", "index", i+1, "path", path)

		// 等待当前图片上传完成（预览元素数量达到 i+1），最多等 60 秒
		if err := waitForUploadComplete(page, i+1); err != nil {
			return errors.Wrapf(err, "第%d张图片上传超时", i+1)
		}
		pause(page, 1*time.Second)
	}

	return nil
}

// findImageUploadInput 查找图片上传的输入框
func findImageUploadInput(page *rod.Page, first bool) (*rod.Element, error) {
	if first {
		return page.Element(".upload-input")
	}

	inputs, err := page.Elements(`input[type="file"]`)
	if err != nil {
		return nil, err
	}
	if len(inputs) == 0 {
		return nil, errors.New("页面没有文件上传输入框")
	}

	for _, input := range inputs {
		accept, err := input.Attribute("accept")
		if err != nil || accept == nil {
			continue
		}
		if acceptsImage(*accept) {
			return input, nil
		}
	}

	return inputs[0], nil
}

// acceptsImage 判断 accept 属性是否接受图片
func acceptsImage(accept string) bool {
	accept = strings.ToLower(accept)
	if strings.Contains(accept, "image/") {
		return true
	}

	for _, ext := range []string{".jpg", ".jpeg", ".png", ".webp", ".heic"} {
		if strings.Contains(accept, ext) {
			return true
		}
	}
	return false
}

// waitForUploadComplete 等待第 expectedCount 张图片上传完成，最多等 60 秒
func waitForUploadComplete(page *rod.Page, expectedCount int) error {
	maxWaitTime := 60 * time.Second
	checkInterval := 500 * time.Millisecond
	start := time.Now()
	lastLogCount := expectedCount - 1

	for time.Since(start) < maxWaitTime {
		uploadedImages, err := page.Elements(".img-preview-area .pr")
		if err != nil {
			pause(page, checkInterval)
			continue
		}

		currentCount := len(uploadedImages)
		if currentCount != lastLogCount {
			slog.Info("等待图片上传", "current", currentCount, "expected", expectedCount)
			lastLogCount = currentCount
		}
		if currentCount >= expectedCount {
			slog.Info("图片上传完成", "count", currentCount)
			return nil
		}

		pause(page, checkInterval)
	}

	return errors.Errorf("第%d张图片上传超时(60s)，请检查网络连接和图片大小", expectedCount)
}

func submitPublish(ctx context.Context, page *rod.Page, title, content string, tags []string, scheduleTime *time.Time, isOriginal bool, visibility string, products []string, activity string, topicTemplate string, skipBody bool) error {
	execution.Stage(ctx, "compose_post", 120*time.Second)
	if !skipBody {
		titleElem, err := page.Element("div.d-input input")
		if err != nil {
			return errors.Wrap(err, "查找标题输入框失败")
		}
		if err := humanize.FillDraft(ctx, titleElem, title); err != nil {
			return errors.Wrap(err, "输入标题失败")
		}

		if err := checkTitleMaxLength(page); err != nil {
			return err
		}
		slog.Info("检查标题长度：通过")

		contentElem, err := getContentElement(page, contentElemTimeout)
		if err != nil {
			return err
		}
		if err := humanize.FillPostDraft(ctx, contentElem, content); err != nil {
			return errors.Wrap(err, "输入正文失败")
		}
		closeFeatureGuide(page)
		if err := waitAndClickTitleInput(titleElem); err != nil {
			return err
		}
		if err := inputTags(ctx, contentElem, tags); err != nil {
			return err
		}

		if err := checkContentMaxLength(page); err != nil {
			return err
		}
		slog.Info("检查正文长度：通过")
	} else if len(tags) > 0 {
		// 用户要求的标签不能在长文路径静默丢弃。
		contentElem, err := getContentElement(page, contentElemTimeout)
		if err != nil {
			return errors.Wrap(err, "长文标签输入框不可用，中止发布")
		}
		if err := inputTags(ctx, contentElem, tags); err != nil {
			return errors.Wrap(err, "长文标签未完整设置，中止发布")
		}
	}

	execution.Stage(ctx, "configure_post", 60*time.Second)
	// 话题模版：#话题旁下拉 → 使用/管理话题模版 → 按名应用（与长文一键排版无关）
	if err := applyTopicTemplate(page, topicTemplate); err != nil {
		return errors.Wrap(err, "应用话题模版失败")
	}

	if scheduleTime != nil {
		if err := setSchedulePublish(ctx, page, *scheduleTime); err != nil {
			return errors.Wrap(err, "设置定时发布失败")
		}
		slog.Info("定时发布设置完成", "schedule_time", scheduleTime.Format("2006-01-02 15:04"))
	}

	if err := setVisibility(page, visibility); err != nil {
		return errors.Wrap(err, "设置可见范围失败")
	}

	// 处理原创声明：显式请求了原创但设置失败 → 报错中止，不静默发成非原创（避免"以为原创其实不是"）
	if isOriginal {
		if err := setOriginal(page); err != nil {
			return errors.Wrap(err, "设置原创声明失败（已请求原创，中止发布）")
		}
		slog.Info("已声明原创")
	}

	if err := bindProducts(ctx, page, products); err != nil {
		return errors.Wrap(err, "绑定商品失败")
	}

	// 关联活动：有参数则必须成功，禁止静默忽略
	if err := bindActivity(page, activity); err != nil {
		return errors.Wrap(err, "关联活动失败")
	}

	// 长文「下一步」后封面图可能仍在生成；生成中点发布会被吞掉。
	execution.Stage(ctx, "assets_ready", 65*time.Second)
	if err := waitPublishAssetsReady(page, 60*time.Second); err != nil {
		return errors.Wrap(err, "发布资源未确认就绪，中止提交")
	}

	// 长文路径 skipBody 时，发布表单标题有时未带入；空标题会导致点了无跳转。
	if skipBody {
		if err := ensurePublishFormTitle(ctx, page, title); err != nil {
			return err
		}
	}
	if err := snapshotPublishSuccessToasts(page); err != nil {
		return errors.Wrap(err, "无法保存发布前页面状态")
	}

	execution.Stage(ctx, "submit_post", 20*time.Second)
	if err := clickPublishButton(page); err != nil {
		return err
	}

	// 定时发布按钮文案为「定时发布」；若有二次确认弹窗则再点一次。
	if scheduleTime != nil {
		if err := confirmScheduledPublishDialog(page); err != nil {
			return errors.Wrap(err, "定时发布确认结果未知")
		}
	}

	// 校验发布真的成功：成功后创作平台会跳转离开发布页或出成功 toast。
	execution.Stage(ctx, "confirm_post", 30*time.Second)
	return waitPublishSuccess(page, 25*time.Second)
}

// waitPublishAssetsReady 等待「笔记图片生成中」类 toast 消失。
func waitPublishAssetsReady(page *rod.Page, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(page.GetContext(), timeout)
	defer cancel()
	page = page.Context(ctx)
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		res, err := page.Eval(`() => {
			const body = (document.body && document.body.innerText) || '';
			if (body.includes('笔记图片生成中') || body.includes('图片生成中')) return true;
			const nodes = document.querySelectorAll('.d-new-toast, .d-toast-notice, .d-toast-wrapper');
			for (const n of nodes) {
				const t = (n.innerText || '');
				if (t.includes('生成中')) return true;
			}
			return false;
		}`)
		if err != nil {
			return err
		}
		if !res.Value.Bool() {
			return nil
		}
		if err := waitCommentPoll(ctx); err != nil {
			return errors.Wrap(err, "ERR_PUBLISH_ASSETS_NOT_READY: 等待笔记图片生成超时")
		}
	}
}

// ensurePublishFormTitle 若发布表单标题为空则填入。
func ensurePublishFormTitle(ctx context.Context, page *rod.Page, title string) error {
	title = strings.TrimSpace(title)
	if title == "" {
		return errors.New("发布标题为空，中止提交")
	}
	elem, err := page.Element("div.d-input input, .title-container input, input[placeholder*='标题']")
	if err != nil {
		return errors.Wrap(err, "发布标题输入框不可用")
	}
	if elem == nil {
		return errors.New("发布标题输入框不可用")
	}
	val, err := elem.Eval(`() => (this.value || '').trim()`)
	if err != nil {
		return err
	}
	if val.Value.Str() != "" {
		if val.Value.Str() != title {
			return errors.New("发布页标题与已审标题不一致，中止提交")
		}
		return nil
	}
	if err := humanize.Click(elem); err != nil {
		return err
	}
	if err := humanize.FillDraft(ctx, elem, title); err != nil {
		return err
	}
	val, err = elem.Eval(`() => (this.value || '').trim()`)
	if err != nil {
		return err
	}
	if val.Value.Str() != title {
		return errors.New("发布标题未完整匹配，中止提交")
	}
	return nil
}

// confirmScheduledPublishDialog 处理定时发布后可能出现的确认弹窗。
func confirmScheduledPublishDialog(page *rod.Page) error {
	parent := page.GetContext()
	ctx, cancel := context.WithTimeout(parent, 3*time.Second)
	defer cancel()
	page = page.Context(ctx)
	for {
		if err := parent.Err(); err != nil {
			return err
		}
		if ctx.Err() != nil {
			return nil
		} // 确认弹窗是可选的；仍由后续回执确认结果。
		btns, err := page.ElementsByJS(rod.Eval(`() => [...document.querySelectorAll('[role="dialog"], .d-modal, .d-dialog, .dialog')].filter(d => {
			const s=getComputedStyle(d);return d.getClientRects().length > 0 && s.visibility !== 'hidden' && s.display !== 'none' && /定时发布|确认发布/.test(d.innerText);
		}).flatMap(d=>[...d.querySelectorAll('button, .d-button, .ce-btn')])`))
		if err != nil {
			if ctx.Err() != nil && parent.Err() == nil {
				return nil
			}
			return err
		}
		for _, b := range btns {
			txt, _ := b.Text()
			txt = strings.TrimSpace(strings.ReplaceAll(txt, "\n", ""))
			if txt == "定时发布" || txt == "确认发布" || txt == "确认定时发布" || txt == "确定" {
				if vis, err := b.Visible(); err == nil && vis {
					if err := ctx.Err(); err != nil {
						return err
					}
					if err := humanize.Click(b); err != nil {
						return err
					}
					slog.Info("已点击定时发布弹窗内的确认按钮")
					return nil
				}
			}
		}
		if err := waitCommentPoll(ctx); err != nil {
			if parent.Err() != nil {
				return parent.Err()
			}
			return nil
		}
	}
}

// waitPublishSuccess 轮询等待发布成功信号：
// 只接受明确成功页或可见的新成功提示，跳到登录/验证/无关页不能算成功。
// 超时则附带当前 toast 文案便于排查。
func waitPublishSuccess(page *rod.Page, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(page.GetContext(), timeout)
	defer cancel()
	page = page.Context(ctx)
	for {
		if err := ctx.Err(); err != nil {
			return errors.Wrap(err, "ERR_PUBLISH_CONFIRMATION_UNKNOWN")
		}
		if wall, reason := detectLoginWall(page); wall {
			return errFromLoginWall(reason)
		} else if reason == "eval_failed" {
			return errors.New("ERR_PUBLISH_CONFIRMATION_UNKNOWN: 无法读取当前页面")
		}
		if info, err := page.Info(); err == nil {
			if isExplicitPublishSuccessURL(info.URL) {
				slog.Info("检测到创作平台明确的发布成功页")
				return nil
			}
		}
		if ok, msg := peekPublishSuccessToast(page); ok {
			slog.Info("发布成功，检测到成功 toast", "toast", msg)
			return nil
		}
		if err := waitCommentPoll(ctx); err != nil {
			return errors.Wrap(err, "ERR_PUBLISH_CONFIRMATION_UNKNOWN: 未取得明确发布回执，先查创作中心状态")
		}
	}
}

func isExplicitPublishSuccessURL(raw string) bool {
	u, err := url.Parse(raw)
	return err == nil && u.Scheme == "https" && u.Hostname() == "creator.xiaohongshu.com" && strings.TrimRight(u.Path, "/") == "/publish/success"
}

const publishToastSelector = `.d-new-toast, .d-toast-notice, .d-toast-wrapper, [class*="toast"]`

func snapshotPublishSuccessToasts(page *rod.Page) error {
	_, err := page.Eval(`(selector) => { window.__xhsBeforePublishToasts = new WeakSet([...document.querySelectorAll(selector)].filter(n=>{
		const s=getComputedStyle(n);return n.getClientRects().length>0 && s.display!=='none' && s.visibility!=='hidden' && Number(s.opacity)>0;
	})); }`, publishToastSelector)
	return err
}

func peekPublishSuccessToast(page *rod.Page) (bool, string) {
	res, err := page.Eval(`(selector) => {
		const nodes = document.querySelectorAll(selector);
		const texts = [];
		nodes.forEach(n => {
			const s=getComputedStyle(n);
			if (!n.getClientRects().length || s.display==='none' || s.visibility==='hidden' || Number(s.opacity)<=0 || n.closest('[contenteditable="true"], input, textarea') || window.__xhsBeforePublishToasts?.has(n)) return;
			const t = (n.innerText || '').trim().replace(/\s+/g, ' ');
			if (t) texts.push(t);
		});
		return texts;
	}`, publishToastSelector)
	if err != nil || res == nil {
		return false, ""
	}
	arr := res.Value.Arr()
	for _, v := range arr {
		t := strings.TrimSpace(v.Str())
		if t == "" {
			continue
		}
		if strings.HasPrefix(t, "发布成功") || strings.HasPrefix(t, "定时发布成功") ||
			strings.HasPrefix(t, "已设置定时发布") || strings.HasPrefix(t, "定时发布已设置") {
			return true, t
		}
	}
	return false, ""
}

func collectVisibleToasts(page *rod.Page) []string {
	res, err := page.Eval(`() => {
		const nodes = document.querySelectorAll('.d-new-toast, .d-toast-notice, .d-toast-wrapper, [class*="toast"]');
		const out = [];
		nodes.forEach(n => {
			const s=getComputedStyle(n);
			if (!n.getClientRects().length || s.display==='none' || s.visibility==='hidden' || Number(s.opacity)<=0 || n.closest('[contenteditable="true"], input, textarea')) return;
			const t = (n.innerText || '').trim().replace(/\s+/g, ' ');
			if (t && t.length < 120) out.push(t);
		});
		return out.slice(0, 5);
	}`)
	if err != nil || res == nil {
		return nil
	}
	var out []string
	for _, v := range res.Value.Arr() {
		if s := strings.TrimSpace(v.Str()); s != "" {
			out = append(out, s)
		}
	}
	return out
}

type publishButton struct {
	elem     *rod.Element
	isWidget bool
}

func clickPublishButton(page *rod.Page) error {
	btn, err := waitForPublishButtonClickable(page, 15*time.Second)
	if err != nil {
		return err
	}

	if btn.isWidget {
		return clickPublishWidget(page, btn.elem)
	}

	if err := humanize.ClickWithBeforePress(btn.elem, func() error {
		execution.MarkDispatch(page.GetContext())
		return nil
	}); err != nil {
		return errors.Wrap(err, "点击发布按钮失败")
	}
	return nil
}

// waitForPublishButtonClickable 等待新版 xhs-publish-btn 或旧版 button.bg-red 可点击。
func waitForPublishButtonClickable(page *rod.Page, maxWait time.Duration) (*publishButton, error) {
	root := page
	ctx, cancel := context.WithTimeout(page.GetContext(), maxWait)
	defer cancel()
	page = page.Context(ctx)
	var lastDisabledReason string

	slog.Info("开始等待发布按钮可点击")

	for {
		if ctx.Err() != nil {
			break
		}
		btn, disabledReason, err := findPublishButton(page)
		if err != nil {
			return nil, err
		}
		if btn != nil && disabledReason == "" {
			live, err := root.ElementFromObject(btn.elem.Object)
			if err != nil {
				return nil, err
			}
			btn.elem = live
			return btn, nil
		}
		if disabledReason != "" {
			lastDisabledReason = disabledReason
		}
		if err := waitCommentPoll(ctx); err != nil {
			break
		}
	}
	if err := root.GetContext().Err(); err != nil {
		return nil, err
	}

	if lastDisabledReason != "" {
		return nil, errors.Errorf("等待发布按钮可点击超时: %s", lastDisabledReason)
	}
	return nil, errors.New("等待发布按钮可点击超时")
}

func findPublishButton(page *rod.Page) (*publishButton, string, error) {
	widgets, err := page.Elements("xhs-publish-btn")
	if err != nil {
		return nil, "", errors.Wrap(err, "查找新版发布按钮失败")
	}

	for _, widget := range widgets {
		if !isElementVisible(widget) {
			continue
		}

		isPublish, err := widget.Attribute("is-publish")
		if err != nil {
			return nil, "", errors.Wrap(err, "读取新版发布按钮 is-publish 属性失败")
		}
		if isPublish != nil && *isPublish == "false" {
			continue
		}

		submitDisabled, err := widget.Attribute("submit-disabled")
		if err != nil {
			return nil, "", errors.Wrap(err, "读取新版发布按钮 submit-disabled 属性失败")
		}
		if submitDisabled != nil && *submitDisabled == "true" {
			return &publishButton{elem: widget, isWidget: true}, "新版发布按钮不可点击", nil
		}

		return &publishButton{elem: widget, isWidget: true}, "", nil
	}

	oldButtons, err := page.Elements(".publish-page-publish-btn button.bg-red")
	if err != nil {
		return nil, "", errors.Wrap(err, "查找旧版发布按钮失败")
	}

	for _, oldButton := range oldButtons {
		if !isElementVisible(oldButton) {
			continue
		}

		if disabled, err := oldButton.Attribute("disabled"); err != nil {
			return nil, "", errors.Wrap(err, "读取旧版发布按钮 disabled 属性失败")
		} else if disabled != nil {
			return &publishButton{elem: oldButton}, "旧版发布按钮 disabled", nil
		}

		if ariaDisabled, err := oldButton.Attribute("aria-disabled"); err != nil {
			return nil, "", errors.Wrap(err, "读取旧版发布按钮 aria-disabled 属性失败")
		} else if ariaDisabled != nil && *ariaDisabled == "true" {
			return &publishButton{elem: oldButton}, "旧版发布按钮 aria-disabled=true", nil
		}

		if cls, err := oldButton.Attribute("class"); err != nil {
			return nil, "", errors.Wrap(err, "读取旧版发布按钮 class 属性失败")
		} else if cls != nil && hasExactClass(*cls, "disabled") {
			return &publishButton{elem: oldButton}, "旧版发布按钮包含 disabled class", nil
		}

		return &publishButton{elem: oldButton}, "", nil
	}

	return nil, "", nil
}

func clickPublishWidget(page *rod.Page, widget *rod.Element) error {
	if err := widget.ScrollIntoView(); err != nil {
		return errors.Wrap(err, "滚动新版发布按钮到可视区域失败")
	}
	pause(page, 200*time.Millisecond)

	// xhs-publish-btn 使用 closed shadow：内含居中的「暂存离开」(白) + 「发布/定时发布」(红 .ce-btn.bg-red)。
	// 旧实现按 host 宽度 65% 点击，红按钮仅约 120px 宽且居中，65% 常落在按钮右缘外侧 → 空点。
	// Preserve the original component entry. Inspect before invoking it; an
	// invocation error may occur after dispatch, so it must never fall back to
	// a second click. Geometry is used only when the method is absent.
	hasMethod, err := widget.Eval(`() => typeof this._onPublish === 'function'`)
	if err != nil {
		return err
	}
	if hasMethod.Value.Bool() {
		execution.MarkDispatch(page.GetContext())
		if _, err := widget.Eval(`() => this._onPublish()`); err != nil {
			return errors.Wrap(err, "新版发布组件已调用，结果待核对")
		}
		slog.Info("已通过 xhs-publish-btn._onPublish 触发发布")
		return nil
	}

	shape, err := widget.Shape()
	if err != nil {
		return errors.Wrap(err, "获取新版发布按钮位置失败")
	}
	if len(shape.Quads) == 0 {
		return errors.New("获取新版发布按钮位置失败: 无可点击区域")
	}

	quad := shape.Quads[0]
	minX, maxX := quad[0], quad[0]
	minY, maxY := quad[1], quad[1]
	for i := 0; i < quad.Len(); i++ {
		x := quad[i*2]
		y := quad[i*2+1]
		if x < minX {
			minX = x
		}
		if x > maxX {
			maxX = x
		}
		if y < minY {
			minY = y
		}
		if y > maxY {
			maxY = y
		}
	}

	width := maxX - minX
	height := maxY - minY
	// 双按钮居中：白120 + gap24 + 红120；红按钮中心 ≈ host中心 + 72px
	x := minX + width/2 + 72
	if x > maxX-10 {
		x = minX + width*0.58
	}
	y := minY + height/2
	submitText := ""
	if attr, _ := widget.Attribute("submit-text"); attr != nil {
		submitText = *attr
	}
	slog.Info("点击发布按钮几何落点", "x", x, "y", y, "submit_text", submitText, "host_width", width)
	execution.MarkDispatch(page.GetContext())
	if err := humanize.ClickAt(page, proto.Point{X: x, Y: y}); err != nil {
		return errors.Wrap(err, "点击新版发布按钮失败")
	}
	return nil
}

// closeFeatureGuide 页面有功能引导卡时点「我知道了」关闭
func closeFeatureGuide(page *rod.Page) {
	has, btn, err := page.Has(".feature-guide__btn")
	if err != nil || !has {
		return
	}
	if visible, err := btn.Visible(); err != nil || !visible {
		return
	}

	btn = btn.Timeout(5 * time.Second)
	defer btn.CancelTimeout()
	if err := humanize.Click(btn); err != nil {
		slog.Warn("关闭功能引导卡失败", "error", err)
		return
	}
	slog.Info("已关闭功能引导卡")
}

// waitAndClickTitleInput 在正文核对后等待标题可交互并回点，提交编辑状态。
func waitAndClickTitleInput(titleElem *rod.Element) error {
	slog.Info("正文已核对，回点可交互的标题输入框")
	if err := humanize.Click(titleElem); err != nil {
		return errors.Wrap(err, "回点标题输入框失败")
	}
	slog.Info("已回点标题输入框，继续后续发布流程")
	return nil
}

// 检查标题是否超过最大长度
func checkTitleMaxLength(page *rod.Page) error {
	has, elem, err := page.Has(`div.title-container div.max_suffix`)
	if err != nil {
		return errors.Wrap(err, "检查标题长度元素失败")
	}

	if !has {
		return nil
	}

	titleLength, err := elem.Text()
	if err != nil {
		return errors.Wrap(err, "获取标题长度文本失败")
	}

	return makeMaxLengthError(titleLength)
}

func checkContentMaxLength(page *rod.Page) error {
	has, elem, err := page.Has(`div.edit-container div.length-error`)
	if err != nil {
		return errors.Wrap(err, "检查正文长度元素失败")
	}

	if !has {
		return nil
	}

	contentLength, err := elem.Text()
	if err != nil {
		return errors.Wrap(err, "获取正文长度文本失败")
	}

	return makeMaxLengthError(contentLength)
}

func makeMaxLengthError(elemText string) error {
	parts := strings.Split(elemText, "/")
	if len(parts) != 2 {
		return errors.Errorf("长度超过限制: %s", elemText)
	}

	currLen, maxLen := parts[0], parts[1]

	return errors.Errorf("当前输入长度为%s，最大长度为%s", currLen, maxLen)
}

// contentElemSelectors 正文输入框的候选选择器，按先后顺序尝试。
var contentElemSelectors = []string{
	`div[role="textbox"][contenteditable="true"]`,
	`div.tiptap[contenteditable="true"]`,
	`div.ql-editor`,
}

// getContentElement 在 timeout 内轮询查找正文输入框，全部落空返回错误。
func getContentElement(page *rod.Page, timeout time.Duration) (*rod.Element, error) {
	deadline := time.Now().Add(timeout)

	for {
		elem, err := findContentElement(page)
		if err == nil {
			return elem, nil
		}

		if time.Now().After(deadline) {
			return nil, errors.Wrap(err, "查找正文输入框失败")
		}
		pause(page, 300*time.Millisecond)
	}
}

func findContentElement(page *rod.Page) (*rod.Element, error) {
	for _, selector := range contentElemSelectors {
		elems, err := page.Elements(selector)
		if err != nil {
			return nil, errors.Wrapf(err, "查找正文输入框失败: %s", selector)
		}

		for _, elem := range elems {
			if isElementVisible(elem) {
				return elem, nil
			}
		}
	}

	return findTextboxByPlaceholder(page)
}

func inputTags(ctx context.Context, contentElem *rod.Element, tags []string) error {
	page := contentElem.Page()
	if len(tags) == 0 {
		return nil
	}

	pause(page, 1*time.Second)

	for i := 0; i < 20; i++ {
		ka, err := contentElem.KeyActions()
		if err != nil {
			return errors.Wrap(err, "创建键盘操作失败")
		}
		if err := ka.Type(input.ArrowDown).Do(); err != nil {
			return errors.Wrap(err, "按下方向键失败")
		}
		pause(page, 10*time.Millisecond)
	}

	ka, err := contentElem.KeyActions()
	if err != nil {
		return errors.Wrap(err, "创建键盘操作失败")
	}
	if err := ka.Press(input.Enter).Press(input.Enter).Do(); err != nil {
		return errors.Wrap(err, "按下回车键失败")
	}

	pause(page, 1*time.Second)

	for _, tag := range tags {
		tag = strings.TrimLeft(tag, "#")
		if err := inputTag(ctx, contentElem, tag); err != nil {
			return errors.Wrapf(err, "输入标签[%s]失败", tag)
		}
	}
	return nil
}

func inputTag(ctx context.Context, contentElem *rod.Element, tag string) error {
	page := contentElem.Page()
	// 输入 # 触发话题联想
	if err := humanize.Type(ctx, contentElem, "#"); err != nil {
		return errors.Wrap(err, "输入#失败")
	}
	pause(page, 200*time.Millisecond) // 技术等待：等联想下拉框弹出

	if err := humanize.Type(ctx, contentElem, tag); err != nil {
		return errors.Wrap(err, "输入标签内容失败")
	}

	pause(page, 1*time.Second) // 技术等待：等联想结果刷新

	topicContainer, err := page.Element("#creator-editor-topic-container")
	if err != nil || topicContainer == nil {
		slog.Warn("未找到标签联想下拉框，直接输入空格", "tag", tag)
		return humanize.Type(ctx, contentElem, " ")
	}

	firstItem, err := topicContainer.Element(".item")
	if err != nil || firstItem == nil {
		slog.Warn("未找到标签联想选项，直接输入空格", "tag", tag)
		return humanize.Type(ctx, contentElem, " ")
	}

	if err := humanize.Click(firstItem); err != nil {
		return errors.Wrap(err, "点击标签联想选项失败")
	}
	slog.Info("成功点击标签联想选项", "tag", tag)
	pause(page, 500*time.Millisecond) // 技术等待：等标签处理完成
	return nil
}

func findTextboxByPlaceholder(page *rod.Page) (*rod.Element, error) {
	elements, err := page.Elements("p")
	if err != nil {
		return nil, errors.Wrap(err, "查找正文候选元素失败")
	}
	if len(elements) == 0 {
		return nil, errors.New("no p elements found")
	}

	placeholderElem := findPlaceholderElement(elements, "输入正文描述")
	if placeholderElem == nil {
		return nil, errors.New("no placeholder element found")
	}

	textboxElem := findTextboxParent(placeholderElem)
	if textboxElem == nil {
		return nil, errors.New("no textbox parent found")
	}

	return textboxElem, nil
}

func findPlaceholderElement(elements []*rod.Element, searchText string) *rod.Element {
	for _, elem := range elements {
		placeholder, err := elem.Attribute("data-placeholder")
		if err != nil || placeholder == nil {
			continue
		}

		if strings.Contains(*placeholder, searchText) && isElementVisible(elem) {
			return elem
		}
	}
	return nil
}

func findTextboxParent(elem *rod.Element) *rod.Element {
	currentElem := elem
	for i := 0; i < 5; i++ {
		parent, err := currentElem.Parent()
		if err != nil {
			break
		}

		role, err := parent.Attribute("role")
		if err != nil || role == nil {
			currentElem = parent
			continue
		}

		if *role == "textbox" {
			return parent
		}

		currentElem = parent
	}
	return nil
}

// isElementVisible 检查元素是否可见
func isElementVisible(elem *rod.Element) bool {

	style, err := elem.Attribute("style")
	if err == nil && style != nil {
		styleStr := *style

		if strings.Contains(styleStr, "left: -9999px") ||
			strings.Contains(styleStr, "top: -9999px") ||
			strings.Contains(styleStr, "position: absolute; left: -9999px") ||
			strings.Contains(styleStr, "display: none") ||
			strings.Contains(styleStr, "visibility: hidden") ||
			strings.Contains(styleStr, "opacity: 1e-05") {
			return false
		}

		// 精确匹配 opacity: 0（不匹配 0.5、0.1 等）
		if strings.Contains(styleStr, "opacity: 0") {
			// 确认是 opacity: 0 而非 opacity: 0.x
			if matched, _ := regexp.MatchString(`opacity:\s*0(\s|;|$)`, styleStr); matched {
				return false
			}
		}
	}

	ariaHidden, err := elem.Attribute("aria-hidden")
	if err == nil && ariaHidden != nil && *ariaHidden == "true" {
		return false
	}

	// 检查 tabindex 属性（-1 表示不可聚焦，通常也意味着不可见）
	tabindex, err := elem.Attribute("tabindex")
	if err == nil && tabindex != nil && *tabindex == "-1" {
		// 结合检查是否有 active class 来判断是否是真正的隐藏
		class, _ := elem.Attribute("class")
		// 使用单词边界检查，避免匹配 "inactive" 等
		if class == nil || !hasExactClass(*class, "active") {
			// 不是激活状态的 -1 tabindex 元素，可能是隐藏的叠加层
			return false
		}
	}

	visible, err := elem.Visible()
	if err != nil {
		slog.Warn("无法获取元素可见性", "error", err)
		return true
	}

	return visible
}

// hasExactClass 检查 class 字符串是否包含指定的完整类名（单词边界匹配）
func hasExactClass(classStr, className string) bool {
	pattern := `\b` + regexp.QuoteMeta(className) + `\b`
	matched, _ := regexp.MatchString(pattern, classStr)
	return matched
}

// setVisibility 设置可见范围
// 支持: "公开可见"(默认), "仅自己可见", "仅互关好友可见"
func setVisibility(page *rod.Page, visibility string) error {
	if visibility == "" || visibility == "公开可见" {
		slog.Info("可见范围使用默认：公开可见")
		return nil
	}

	supported := map[string]bool{"仅自己可见": true, "仅互关好友可见": true}
	if !supported[visibility] {
		return errors.Errorf("不支持的可见范围: %s，支持: 公开可见、仅自己可见、仅互关好友可见", visibility)
	}

	dropdown, err := page.Element("div.permission-card-wrapper div.d-select-content")
	if err != nil {
		return errors.Wrap(err, "查找可见范围下拉框失败")
	}
	if err := humanize.Click(dropdown); err != nil {
		return errors.Wrap(err, "点击可见范围下拉框失败")
	}
	pause(page, 500*time.Millisecond)

	// 在弹窗中查找并点击目标选项
	opts, err := page.Elements("div.d-options-wrapper div.d-grid-item div.custom-option")
	if err != nil {
		return errors.Wrap(err, "查找可见范围选项失败")
	}
	for _, opt := range opts {
		text, err := opt.Text()
		if err != nil {
			continue
		}
		if strings.Contains(text, visibility) {
			if err := humanize.Click(opt); err != nil {
				return errors.Wrap(err, "选择可见范围失败")
			}
			slog.Info("已设置可见范围", "visibility", visibility)
			pause(page, 200*time.Millisecond)
			return nil
		}
	}
	return errors.Errorf("未找到可见范围选项: %s", visibility)
}

// setSchedulePublish 设置定时发布时间
func setSchedulePublish(ctx context.Context, page *rod.Page, t time.Time) error {
	// 1. 点击定时发布开关
	if err := clickScheduleSwitch(page); err != nil {
		return err
	}
	pause(page, 800*time.Millisecond)

	// 2. 设置日期时间
	if err := setDateTime(ctx, page, t); err != nil {
		return err
	}
	pause(page, 500*time.Millisecond)

	return nil
}

// clickScheduleSwitch 点击定时发布开关
func clickScheduleSwitch(page *rod.Page) error {
	switchElem, err := page.Element(".post-time-wrapper .d-switch")
	if err != nil {
		return errors.Wrap(err, "查找定时发布开关失败")
	}

	if err := humanize.Click(switchElem); err != nil {
		return errors.Wrap(err, "点击定时发布开关失败")
	}
	slog.Info("已点击定时发布开关")
	return nil
}

// setDateTime 设置日期时间
func setDateTime(ctx context.Context, page *rod.Page, t time.Time) error {
	// 创作者中心时间选择常按 30 分钟对齐；非整点/半点可能校验失败。
	rounded := t.Truncate(time.Minute)
	min := rounded.Minute()
	switch {
	case min == 0 || min == 30:
		// ok
	case min < 30:
		rounded = rounded.Add(time.Duration(30-min) * time.Minute)
	default:
		rounded = rounded.Add(time.Duration(60-min) * time.Minute)
	}
	dateTimeStr := rounded.Format("2006-01-02 15:04")

	elem, err := page.Element(".date-picker-container input")
	if err != nil {
		return errors.Wrap(err, "查找日期时间输入框失败")
	}

	// SelectAllText 走 Eval(this.select())，只改选区、不额外派发事件，暂时保留。
	// 换成键盘全选要区分 Ctrl/Cmd，换成三击又可能触发日期控件的其他行为。
	if err := elem.SelectAllText(); err != nil {
		return errors.Wrap(err, "选择日期时间文本失败")
	}
	if err := humanize.Type(ctx, elem, dateTimeStr); err != nil {
		return errors.Wrap(err, "输入日期时间失败")
	}
	// 提交输入并收起选择器，避免遮挡后续发布按钮
	_ = page.Keyboard.Press(input.Enter)
	pause(page, 200*time.Millisecond)
	_, _ = page.Eval(`() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); }`)
	// 若仍有「确定」类按钮则点掉
	if btns, _ := page.Elements("button, .d-button"); btns != nil {
		for _, b := range btns {
			txt, _ := b.Text()
			txt = strings.TrimSpace(txt)
			if txt == "确定" || txt == "确认" {
				if vis, err := b.Visible(); err == nil && vis {
					_ = humanize.Click(b)
					break
				}
			}
		}
	}
	slog.Info("已设置日期时间", "datetime", dateTimeStr, "raw", t.Format("2006-01-02 15:04"))

	return nil
}

// setOriginal 设置原创声明
func setOriginal(page *rod.Page) error {
	// 根据小红书创作者页面的实际结构：
	// div.custom-switch-card 包含 span.has-tips 文本为"原创声明"
	// 开关是 div.d-switch 组件

	// 查找包含"原创声明"文本的 custom-switch-card
	switchCards, err := page.Elements("div.custom-switch-card")
	if err != nil {
		return errors.Wrap(err, "查找原创声明卡片失败")
	}

	for _, card := range switchCards {
		text, err := card.Text()
		if err != nil {
			continue
		}

		// 检查是否是原创声明卡片
		if !strings.Contains(text, "原创声明") {
			continue
		}

		// 找到原创声明卡片，查找其中的 d-switch
		switchElem, err := card.Element("div.d-switch")
		if err != nil {
			continue
		}

		// 检查开关是否已打开
		checked, err := switchElem.Eval(`() => {
			const input = this.querySelector('input[type="checkbox"]');
			return input ? input.checked : false;
		}`)
		if err != nil {
			continue
		}

		if checked.Value.Bool() {
			slog.Info("原创声明已开启")
			return nil
		}

		// 点击开关
		if err := humanize.Click(switchElem); err != nil {
			return errors.Wrap(err, "点击原创声明开关失败")
		}

		pause(page, 500*time.Millisecond)

		// 处理原创声明确认弹窗
		if err := confirmOriginalDeclaration(page); err != nil {
			return errors.Wrap(err, "确认原创声明失败")
		}

		slog.Info("已开启原创声明")
		return nil
	}

	return errors.New("未找到原创声明选项")
}

// confirmOriginalDeclaration 交互（勾选须知、点声明按钮）走 go-rod 点击；
// 仅用只读 Eval 读取 checkbox 勾选态（不产生交互，无法用属性判断的自定义组件才用）。
func confirmOriginalDeclaration(page *rod.Page) error {
	pause(page, 800*time.Millisecond) // 技术等待：等确认弹窗渲染

	if footer, err := findFooterByText(page, "原创声明须知"); err != nil {
		slog.Warn("未找到原创声明确认弹窗的 footer", "error", err)
	} else if err := checkFooterCheckbox(footer); err != nil {
		slog.Warn("勾选原创声明须知失败", "error", err)
	}

	pause(page, 500*time.Millisecond) // 技术等待：勾选后等"声明原创"按钮变可用

	footer, err := findFooterByText(page, "声明原创")
	if err != nil {
		return errors.Wrap(err, "未找到声明原创弹窗")
	}

	btn, err := footer.Element("button.custom-button")
	if err != nil {
		return errors.Wrap(err, "未找到声明原创按钮")
	}

	if isButtonDisabled(btn) {
		// 兜底：按钮仍禁用，可能须知未勾上，再勾一次
		if err := checkFooterCheckbox(footer); err != nil {
			slog.Warn("二次勾选须知失败", "error", err)
		}
		pause(page, 300*time.Millisecond)
		if isButtonDisabled(btn) {
			return errors.New("声明原创按钮仍处于禁用状态")
		}
	}

	if err := humanize.Click(btn); err != nil {
		return errors.Wrap(err, "点击声明原创按钮失败")
	}
	slog.Info("已成功点击声明原创按钮")
	pause(page, 300*time.Millisecond)
	return nil
}

func findFooterByText(page *rod.Page, keyword string) (*rod.Element, error) {
	footers, err := page.Elements("div.footer")
	if err != nil {
		return nil, errors.Wrap(err, "查找弹窗 footer 失败")
	}
	for _, footer := range footers {
		text, err := footer.Text()
		if err != nil {
			continue
		}
		if strings.Contains(text, keyword) {
			return footer, nil
		}
	}
	return nil, errors.Errorf("未找到包含%q的弹窗 footer", keyword)
}

// checkFooterCheckbox 勾选 footer 内的自定义 checkbox（未勾选时才点）。
func checkFooterCheckbox(footer *rod.Element) error {
	cb, err := footer.Element("div.d-checkbox")
	if err != nil {
		return errors.Wrap(err, "未找到须知 checkbox")
	}

	// 只读判断当前是否已勾选（隐藏 input.checked 或 simulator 上的 checked 态）
	checked, err := cb.Eval(`() => {
		const input = this.querySelector('input[type="checkbox"]');
		return (input && input.checked) || this.querySelector('.checked') !== null;
	}`)
	if err != nil {
		return errors.Wrap(err, "读取 checkbox 状态失败")
	}
	if checked.Value.Bool() {
		return nil
	}

	return humanize.Click(cb)
}

func isButtonDisabled(btn *rod.Element) bool {
	if disabled, _ := btn.Attribute("disabled"); disabled != nil {
		return true
	}
	if cls, _ := btn.Attribute("class"); cls != nil && hasExactClass(*cls, "disabled") {
		return true
	}
	return false
}

// bindProducts 绑定商品到发布内容
func bindProducts(ctx context.Context, page *rod.Page, products []string) error {
	if len(products) == 0 {
		return nil
	}

	slog.Info("开始绑定商品", "products", products)

	// 点击"添加商品"按钮
	if err := clickAddProductButton(page); err != nil {
		return errors.Wrap(err, "点击添加商品按钮失败")
	}
	pause(page, 1*time.Second)

	// 等待商品选择弹窗出现
	modal, err := waitForProductModal(page)
	if err != nil {
		return errors.Wrap(err, "等待商品弹窗失败")
	}
	slog.Info("商品选择弹窗已打开")

	// 遍历搜索并选择商品
	var failedProducts []string
	for _, keyword := range products {
		if err := searchAndSelectProduct(ctx, page, modal, keyword); err != nil {
			slog.Warn("搜索选择商品失败", "keyword", keyword, "error", err)
			failedProducts = append(failedProducts, keyword)
		}
		pause(page, 500*time.Millisecond)
	}

	// 点击保存按钮
	slog.Info("准备点击保存按钮")
	if err := clickModalSaveButton(modal); err != nil {
		return errors.Wrap(err, "点击保存按钮失败")
	}
	slog.Info("保存按钮点击完成，开始等待弹窗关闭")

	// 等待弹窗关闭
	if err := waitForModalClose(page); err != nil {
		slog.Warn("等待弹窗关闭超时", "error", err)
	} else {
		slog.Info("弹窗已关闭")
	}

	if len(failedProducts) > 0 {
		return errors.Errorf("部分商品未找到: %v", failedProducts)
	}

	slog.Info("商品绑定完成", "total", len(products))
	pause(page, 1000*time.Millisecond)
	return nil
}

// clickAddProductButton 点击"添加商品"按钮
func clickAddProductButton(page *rod.Page) error {
	slog.Info("开始查找添加商品按钮")

	// 查找包含"添加商品"文本的元素
	spans, err := page.Elements("span.d-text")
	if err != nil {
		return errors.Wrap(err, "查找商品按钮文本失败")
	}

	for _, span := range spans {
		text, err := span.Text()
		if err != nil {
			continue
		}
		if strings.TrimSpace(text) == "添加商品" {
			slog.Info("找到添加商品文本，向上查找可点击父元素")
			// 向上查找可点击的父元素
			parent := span
			for i := 0; i < 5; i++ {
				p, err := parent.Parent()
				if err != nil {
					break
				}
				parent = p

				tagName, err := parent.Eval(`() => this.tagName.toLowerCase()`)
				if err != nil {
					continue
				}
				tag := tagName.Value.Str()

				// 检查是否为 button 或含 d-button class
				if tag == "button" {
					if err := humanize.Click(parent); err != nil {
						return errors.Wrap(err, "点击添加商品按钮失败")
					}
					slog.Info("已点击添加商品按钮")
					pause(page, 300*time.Millisecond) // 确保弹窗动画开始
					return nil
				}

				cls, _ := parent.Attribute("class")
				if cls != nil && strings.Contains(*cls, "d-button") {
					if err := humanize.Click(parent); err != nil {
						return errors.Wrap(err, "点击添加商品按钮失败")
					}
					slog.Info("已点击添加商品按钮")
					pause(page, 300*time.Millisecond) // 确保弹窗动画开始
					return nil
				}
			}
		}
	}

	return errors.New("未找到添加商品按钮，账号可能未开通商品功能")
}

// waitForProductModal 等待商品选择弹窗出现
func waitForProductModal(page *rod.Page) (*rod.Element, error) {
	deadline := time.Now().Add(10 * time.Second)

	for time.Now().Before(deadline) {
		modal, err := page.Element(".multi-goods-selector-modal")
		if err == nil && modal != nil {
			visible, _ := modal.Visible()
			if visible {
				slog.Info("商品选择弹窗已出现")
				return modal, nil
			}
		}
		pause(page, 100*time.Millisecond) // 缩短轮询间隔，更快响应
	}

	return nil, errors.New("等待商品选择弹窗超时")
}

// searchAndSelectProduct 搜索并选择商品
func searchAndSelectProduct(ctx context.Context, page *rod.Page, modal *rod.Element, keyword string) error {
	slog.Info("搜索商品", "keyword", keyword)

	// 1. 获取搜索框
	searchInput, err := modal.Element(`input[placeholder="搜索商品ID 或 商品名称"]`)
	if err != nil {
		return errors.Wrap(err, "未找到商品搜索框")
	}

	// 2. 清空并输入关键词。SelectAllText 走 Eval(this.select())，只改选区、不额外
	// 派发事件，暂时保留（换键盘全选要区分 Ctrl/Cmd）。
	if err := searchInput.SelectAllText(); err != nil {
		slog.Warn("选择搜索框文本失败", "error", err)
	}
	pause(page, 100*time.Millisecond)

	if err := humanize.Type(ctx, searchInput, keyword); err != nil {
		return errors.Wrap(err, "输入搜索关键词失败")
	}
	pause(page, 300*time.Millisecond)

	// 3. 触发搜索（模拟键盘 Enter）
	if err := page.Keyboard.Press(input.Enter); err != nil {
		return errors.Wrap(err, "触发搜索失败")
	}

	// 4. 等待搜索结果加载
	pause(page, 1*time.Second)

	// 等待 loading 消失（使用与工作代码相同的选择器）
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		loading, err := modal.Element(".goods-list-loading")
		if err != nil || loading == nil {
			break
		}
		visible, _ := loading.Visible()
		if !visible {
			break
		}
		pause(page, 100*time.Millisecond)
	}

	// 等待商品列表渲染完成（使用与工作代码相同的选择器）
	for time.Now().Before(deadline) {
		productList, err := modal.Element(".goods-list-normal .good-card-container")
		if err == nil && productList != nil {
			break
		}
		pause(page, 100*time.Millisecond)
	}
	pause(page, 500*time.Millisecond) // 额外等待确保渲染完成

	// 5. 点击第一个商品的 checkbox（使用与工作代码相同的选择器）
	checkbox, err := modal.Element(".goods-list-normal .good-card-container .d-checkbox")
	if err != nil {
		return errors.Wrap(err, "未找到商品选择框")
	}

	// 检查是否已经选中
	isChecked, err := checkbox.Eval(`(el) => {
		return el.querySelector('.d-checkbox-simulator.checked') !== null ||
			   el.querySelector('input[type="checkbox"]:checked') !== null;
	}`)
	if err == nil && isChecked.Value.Bool() {
		slog.Info("商品已选中，跳过", "keyword", keyword)
		return nil
	}

	if err := humanize.Click(checkbox); err != nil {
		return errors.Wrap(err, "点击商品选择框失败")
	}

	randomDelay := 800 + rand.Intn(700)
	pause(page, time.Duration(randomDelay)*time.Millisecond)

	slog.Info("已选择商品", "keyword", keyword)
	return nil
}

// clickModalSaveButton 点击保存按钮
func clickModalSaveButton(modal *rod.Element) error {
	// 查找保存按钮（参考工作代码：直接查找并点击，不强制要求找到）
	btn, err := modal.Element(".goods-selected-footer button")
	if err == nil && btn != nil {
		if err := humanize.Click(btn); err != nil {
			slog.Warn("点击保存按钮失败", "error", err)
		} else {
			slog.Info("已点击保存按钮")
			return nil
		}
	}

	// 尝试点击主按钮
	primaryBtn, err := modal.Element(".goods-selected-footer .d-button--primary")
	if err == nil && primaryBtn != nil {
		if err := humanize.Click(primaryBtn); err != nil {
			slog.Warn("点击主按钮失败", "error", err)
		} else {
			slog.Info("已点击主按钮")
			return nil
		}
	}

	slog.Warn("未找到保存按钮，继续执行")
	return nil
}

// waitForModalClose 等待弹窗关闭
func waitForModalClose(page *rod.Page) error {
	deadline := time.Now().Add(5 * time.Second)
	slog.Info("开始等待弹窗关闭")

	for time.Now().Before(deadline) {
		// 使用 Has 代替 Element，避免等待元素出现的阻塞
		has, _, err := page.Has(".multi-goods-selector-modal")
		if err != nil || !has {
			slog.Info("弹窗已关闭")
			return nil
		}
		pause(page, 200*time.Millisecond)
	}

	return errors.New("等待弹窗关闭超时")
}

// bindActivity 在发布页「关联活动」区按名称点击「关联」。
// activity 为空则跳过；页面无入口或无匹配时返回明确错误（禁止静默忽略）。
func bindActivity(page *rod.Page, activity string) error {
	activity = strings.TrimSpace(activity)
	if activity == "" {
		return nil
	}

	plugin, err := page.Element(".activity-plugin, .publish-page-content-activity")
	if err != nil || plugin == nil {
		return errors.New("页面无关联活动入口（未找到 .activity-plugin）；当前账号/页态可能不支持活动，或需先上传媒体后再出现")
	}

	// 先在可见卡片里找
	if ok, err := clickActivityCard(page, activity); err != nil {
		return err
	} else if ok {
		return nil
	}

	// 点「更多」再试一次（展开完整活动列表）
	if more, err := page.Element(".activity-plugin-label button.more, .activity-plugin-label .more"); err == nil && more != nil {
		_ = humanize.Click(more)
		pause(page, 800*time.Millisecond)
		if ok, err := clickActivityCard(page, activity); err != nil {
			return err
		} else if ok {
			return nil
		}
	}

	return errors.Errorf("未找到匹配活动 %q（已扫 .activity-card /.activity-name）；请核对名称或页面是否展示该活动", activity)
}

// clickActivityCard 在当前页活动卡片中按名称（包含匹配）点击「关联」。
func clickActivityCard(page *rod.Page, activity string) (bool, error) {
	cards, err := page.Elements(".activity-card")
	if err != nil {
		return false, errors.Wrap(err, "查找活动卡片失败")
	}
	for _, card := range cards {
		nameEl, err := card.Element(".activity-name")
		if err != nil || nameEl == nil {
			continue
		}
		name, _ := nameEl.Text()
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		if name != activity && !strings.Contains(name, activity) && !strings.Contains(activity, name) {
			continue
		}
		btn, err := card.Element("button.activity-action, .activity-action")
		if err != nil || btn == nil {
			return false, errors.Errorf("找到活动 %q 但无「关联」按钮", name)
		}
		if err := humanize.Click(btn); err != nil {
			return false, errors.Wrapf(err, "点击关联活动 %q 失败", name)
		}
		slog.Info("已关联活动", "activity", name)
		pause(page, 500*time.Millisecond)
		return true, nil
	}
	return false, nil
}

// trySelectLongArticleTemplate 尽力走「写长文 → 新的创作 → 一键排版 → .template-card」。
// 找不到稳定模版卡片时返回明确错误（供上层标 gap，禁止静默当成功）。
func trySelectLongArticleTemplate(ctx context.Context, page *rod.Page, templateName, title, content string) error {
	templateName = strings.TrimSpace(templateName)
	if templateName == "" {
		return nil
	}

	if err := mustClickPublishTab(page, "写长文"); err != nil {
		return errors.Wrap(err, "切换「写长文」失败（长文模版未实现/页态无此 tab）")
	}
	pause(page, 1500*time.Millisecond)

	// 新的创作
	clicked := false
	btns, _ := page.Elements("button, .d-button")
	for _, b := range btns {
		t, _ := b.Text()
		if strings.TrimSpace(t) == "新的创作" {
			if err := humanize.Click(b); err != nil {
				return errors.Wrap(err, "点击「新的创作」失败")
			}
			clicked = true
			break
		}
	}
	if !clicked {
		return errors.New("长文模版：未找到「新的创作」按钮")
	}
	pause(page, 2*time.Second)

	// Both fields must be present and verified before applying formatting.
	if title != "" {
		titleEl, err := page.Element(`textarea.d-text, textarea[placeholder*="标题"], .rich-editor-title textarea, .rich-editor-title [contenteditable]`)
		if err != nil {
			return errors.Wrap(err, "长文标题输入框不可用")
		}
		if err := humanize.FillDraft(ctx, titleEl, title); err != nil {
			return errors.Wrap(err, "长文标题未完整填写")
		}
	}
	if content != "" {
		ed, err := page.Element(`.rich-editor-content [contenteditable], .rich-editor-content .ProseMirror, div.tiptap.ProseMirror, div.ProseMirror[contenteditable="true"]`)
		if err != nil {
			return errors.Wrap(err, "长文正文输入框不可用")
		}
		if err := humanize.FillPostDraft(ctx, ed, content); err != nil {
			return errors.Wrap(err, "长文正文未完整填写")
		}
	}

	// 一键排版
	formatClicked := false
	all, _ := page.Elements("button, .d-button, span, div")
	for _, el := range all {
		t, _ := el.Text()
		if strings.TrimSpace(t) == "一键排版" {
			if err := humanize.Click(el); err != nil {
				return errors.Wrap(err, "点击「一键排版」失败")
			}
			formatClicked = true
			break
		}
	}
	if !formatClicked {
		return errors.New("长文模版：未找到「一键排版」按钮")
	}

	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		cards, _ := page.Elements(".template-card")
		if len(cards) > 0 {
			break
		}
		pause(page, 400*time.Millisecond)
	}
	cards, _ := page.Elements(".template-card")
	if len(cards) == 0 {
		return errors.New("长文模版：一键排版后未出现 .template-card（UI 可能变更或正文未生效）；见 README gap，禁止静默忽略")
	}

	matched := false
	for _, card := range cards {
		titleEl, err := card.Element(".template-title")
		var name string
		if err == nil && titleEl != nil {
			name, _ = titleEl.Text()
		} else {
			name, _ = card.Text()
		}
		name = strings.TrimSpace(name)
		if name == templateName || strings.Contains(name, templateName) || strings.Contains(templateName, name) {
			if err := humanize.Click(card); err != nil {
				return errors.Wrapf(err, "点击模版 %q 失败", name)
			}
			matched = true
			slog.Info("已选择长文模版", "template", name)
			break
		}
	}
	if !matched {
		names := make([]string, 0, len(cards))
		for _, card := range cards {
			if t, err := card.Element(".template-title"); err == nil && t != nil {
				n, _ := t.Text()
				names = append(names, strings.TrimSpace(n))
			}
		}
		return errors.Errorf("长文模版：未找到名称匹配 %q 的 .template-card；可用: %v", templateName, names)
	}
	pause(page, 800*time.Millisecond)

	// 下一步
	nextClicked := false
	alls, _ := page.Elements("button, .d-button")
	for _, el := range alls {
		t, _ := el.Text()
		if strings.TrimSpace(t) == "下一步" {
			if err := humanize.Click(el); err != nil {
				return errors.Wrap(err, "点击「下一步」失败")
			}
			nextClicked = true
			break
		}
	}
	if !nextClicked {
		return errors.New("长文模版：已选模版但未找到「下一步」")
	}
	pause(page, 2*time.Second)
	return nil
}

// applyTopicTemplate 在发布页应用「话题模版」（非长文一键排版）。
// 路径：button.topicTemplate →「使用/管理话题模版」→ .topic-template-list-modal 内 .template-card
// 按 .template-card__title 匹配名称 → 点 button.template-card__apply。
// 空名称跳过；有值但找不到入口/卡片/应用按钮则明确报错。
func applyTopicTemplate(page *rod.Page, templateName string) error {
	templateName = strings.TrimSpace(templateName)
	if templateName == "" {
		return nil
	}

	// 优先 JS 点击下拉箭头（rod Click 在该按钮上偶发长时间卡住）
	clickedCaret, evalErr := page.Eval(`() => {
		const btn = document.querySelector('button.topicTemplate');
		if (!btn) return false;
		btn.click();
		return true;
	}`)
	if evalErr != nil || clickedCaret == nil || !clickedCaret.Value.Bool() {
		has, caret, err := page.Has("button.topicTemplate")
		if err != nil || !has || caret == nil {
			return errors.New("话题模版：未找到 #话题旁下拉按钮 button.topicTemplate")
		}
		if err := humanize.Click(caret); err != nil {
			return errors.Wrap(err, "话题模版：点击 button.topicTemplate 失败")
		}
	}
	pause(page, 800*time.Millisecond)

	opened, err := page.Eval(`() => {
		const items = [...document.querySelectorAll('.topicTemplatePopover, .template-topic-popover .title, .template-topic-popover div')];
		for (const el of items) {
			const t = (el.innerText||'').trim().replace(/\s+/g,' ');
			if (t === '使用/管理话题模版') {
				el.click();
				return {ok:true, cls: String(el.className||'').slice(0,120)};
			}
		}
		// 宽松：包含文案且较短
		for (const el of document.querySelectorAll('div,span,li,button,a')) {
			const t = (el.innerText||'').trim().replace(/\s+/g,' ');
			if (t === '使用/管理话题模版' || (t.includes('使用/管理话题模版') && t.length < 20)) {
				el.click();
				return {ok:true, soft:true};
			}
		}
		return {ok:false, hasPopover: !!(document.querySelector('.template-topic-popover, .topicTemplatePopover'))};
	}`)
	if err != nil {
		return errors.Wrap(err, "话题模版：打开「使用/管理话题模版」失败")
	}
	if opened == nil || !opened.Value.Get("ok").Bool() {
		return errors.New("话题模版：下拉后未找到「使用/管理话题模版」")
	}
	pause(page, 1500*time.Millisecond)

	deadline := time.Now().Add(12 * time.Second)
	var cards []*rod.Element
	for time.Now().Before(deadline) {
		cards, _ = page.Elements(".topic-template-list-modal .template-card, .d-modal.topic-template-list-modal .template-card")
		if len(cards) == 0 {
			cards, _ = page.Elements(".topic-template-list-modal .template-card")
		}
		if len(cards) == 0 {
			// 弹层已开但选择器变体
			cards, _ = page.Elements(".template-card")
			// 过滤：须带 template-card__title 与 template-card__apply（话题模版卡片）
			filtered := make([]*rod.Element, 0, len(cards))
			for _, c := range cards {
				hasTitle, _, _ := c.Has(".template-card__title")
				hasApply, _, _ := c.Has("button.template-card__apply, .template-card__apply")
				if hasTitle && hasApply {
					filtered = append(filtered, c)
				}
			}
			cards = filtered
		}
		if len(cards) > 0 {
			break
		}
		pause(page, 300*time.Millisecond)
	}
	if len(cards) == 0 {
		return errors.New("话题模版：未出现话题模版列表（.topic-template-list-modal .template-card）")
	}

	var matchedName string
	applied := false
	names := make([]string, 0, len(cards))
	for _, card := range cards {
		titleEl, err := card.Element(".template-card__title")
		var name string
		if err == nil && titleEl != nil {
			name, _ = titleEl.Text()
		} else {
			name, _ = card.Text()
		}
		name = strings.TrimSpace(name)
		if name != "" {
			names = append(names, name)
		}
		if name != templateName && !strings.EqualFold(name, templateName) &&
			!strings.Contains(strings.ToLower(name), strings.ToLower(templateName)) &&
			!strings.Contains(strings.ToLower(templateName), strings.ToLower(name)) {
			continue
		}
		matchedName = name
		// 在卡片内点「应用」（跳过 disabled）
		res, evalErr := card.Eval(`() => {
			const btns = [...this.querySelectorAll('button.template-card__apply')];
			for (const b of btns) {
				if (b.classList.contains('disabled') || b.classList.contains('notAllowed') || b.disabled) continue;
				b.click();
				return true;
			}
			return false;
		}`)
		if evalErr != nil || res == nil || !res.Value.Bool() {
			return errors.Errorf("话题模版：找到 %q 但无可用「应用」按钮", name)
		}
		applied = true
		break
	}
	if !applied {
		return errors.Errorf("话题模版：未找到名称匹配 %q 的卡片；可用: %v", templateName, names)
	}
	slog.Info("已应用话题模版", "template", matchedName)
	pause(page, 1200*time.Millisecond)
	return nil
}
