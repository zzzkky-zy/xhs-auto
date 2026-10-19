package xiaohongshu

import (
	"context"
	"errors"
	"fmt"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"strings"
	"time"

	"github.com/go-rod/rod"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

const (
	commentActionTimeout = 120 * time.Second
	commentSearchTimeout = 45 * time.Second
	commentStepTimeout   = 8 * time.Second
	commentConfirmTime   = 12 * time.Second
)

// CommentActionError 区分未点发送与发送结果未知；后者禁止直接重试。
type CommentActionError struct {
	Phase     string
	Submitted bool
	Cause     error
}

func (e *CommentActionError) Error() string {
	code := "ERR_COMMENT_NOT_SUBMITTED"
	if e.Submitted {
		code = "ERR_COMMENT_CONFIRMATION_UNKNOWN"
	}
	return fmt.Sprintf("%s: phase=%s: %v", code, e.Phase, e.Cause)
}
func (e *CommentActionError) Unwrap() error                { return e.Cause }
func (e *CommentActionError) SubmissionNotAttempted() bool { return !e.Submitted }

type CommentFeedAction struct {
	page     *rod.Page
	authorID string
}

func NewCommentFeedAction(page *rod.Page, authorID ...string) *CommentFeedAction {
	f := &CommentFeedAction{page: page}
	if len(authorID) > 0 {
		f.authorID = authorID[0]
	}
	return f
}

func commentFailure(phase string, submitted bool, cause error) error {
	if !submitted && (phase == "navigate" || phase == "page_ready") && errors.Is(cause, context.DeadlineExceeded) {
		cause = fmt.Errorf("ERR_COMMENT_TEMPORARY_PAGE_FAILURE: %w", cause)
	}
	return &CommentActionError{Phase: phase, Submitted: submitted, Cause: cause}
}

func (f *CommentFeedAction) PostComment(ctx context.Context, feedID, xsecToken, content string) error {
	return f.run(ctx, feedID, xsecToken, "", "", content, false)
}

func (f *CommentFeedAction) ReplyToComment(ctx context.Context, feedID, xsecToken, commentID, userID, content string) error {
	return f.run(ctx, feedID, xsecToken, commentID, userID, content, true)
}

func (f *CommentFeedAction) run(ctx context.Context, feedID, token, commentID, userID, content string, reply bool) error {
	ctx, cancel := context.WithTimeout(ctx, commentActionTimeout)
	defer cancel()
	page := f.page.Context(ctx)
	if err := ctx.Err(); err != nil {
		return commentFailure("start", false, err)
	}
	if strings.TrimSpace(feedID) == "" || strings.TrimSpace(content) == "" || (reply && commentID == "" && userID == "") {
		return commentFailure("validate", false, fmt.Errorf("帖子、内容或回复目标缺失"))
	}
	// 不打印含目标访问令牌的 URL。
	logrus.WithField("feed_id", feedID).Info("打开评论任务页面")
	execution.Stage(ctx, "navigate_feed", 25*time.Second)
	if !alreadyAtFeed(page, makeFeedDetailURL(feedID, token)) {
		if err := page.Navigate(makeFeedDetailURL(feedID, token)); err != nil {
			return commentFailure("navigate", false, err)
		}
	}
	execution.Stage(ctx, "page_ready", 12*time.Second)
	if err := waitCommentPageReady(ctx, page); err != nil {
		return commentFailure("page_ready", false, err)
	}
	humanize.Delay(ctx, humanize.AfterNavigate)
	if err := commentPageError(page); err != nil {
		return commentFailure("page_check", false, err)
	}
	// Replies scroll to their target before clicking. Bring the owned page back
	// first so Rod's layout wait does not depend on a hidden tab's paused frames.
	if err := humanize.PrepareInput(page, ctx); err != nil {
		return commentFailure("activate_page", false, err)
	}
	if reply {
		execution.Stage(ctx, "find_comment", 45*time.Second)
		target, err := findCommentElement(ctx, page, commentID, userID)
		if err != nil {
			return commentFailure("find_target", false, err)
		}
		if err := target.ScrollIntoView(); err != nil {
			return commentFailure("scroll_target", false, err)
		}
		button, err := target.Element(".right .interactions .reply")
		if err != nil {
			return commentFailure("reply_button", false, err)
		}
		if err := humanize.Click(button); err != nil {
			return commentFailure("open_reply", false, err)
		}
	} else {
		// In the collapsed composer an absolute placeholder covers the editable
		// node. Click the visible placeholder text, which opens the real editor;
		// waiting for the covered p itself to be interactable cannot succeed.
		execution.Stage(ctx, "open_comment_input", commentStepTimeout)
		if err := openCommentComposer(page); err != nil {
			return commentFailure("open_input", false, err)
		}
	}
	humanize.Delay(ctx, humanize.AfterClick)
	return f.composeAndSubmit(ctx, page, content, commentID, userID, reply)
}

// Follow the original UI flow: click the visible placeholder span, then use
// the real editable paragraph. There is no coordinate/ancestor hit override.
// An already expanded composer can be focused directly. Only one click is made.
func openCommentComposer(page *rod.Page) error {
	for {
		if err := commentPageError(page); err != nil {
			return err
		}
		placeholders, err := page.Elements("div.input-box div.content-edit span")
		if err != nil {
			return err
		}
		for _, placeholder := range placeholders {
			if visible, err := placeholder.Visible(); err != nil {
				return err
			} else if visible {
				return humanize.Click(placeholder)
			}
		}
		inputs, err := page.Elements("div.input-box div.content-edit p.content-input")
		if err != nil {
			return err
		}
		for _, input := range inputs {
			if visible, err := input.Visible(); err != nil {
				return err
			} else if visible {
				return humanize.Click(input)
			}
		}
		if err := waitCommentPoll(page.GetContext()); err != nil {
			return err
		}
	}
}

// 输入、提交与确认共用调用上下文；只发送一次，不在未知状态补点。
func (f *CommentFeedAction) composeAndSubmit(ctx context.Context, page *rod.Page, content, commentID, userID string, reply bool) error {
	user, identityErr := NewLogin(page).CurrentUser(ctx)
	if identityErr != nil || f.authorID == "" || user.UserID != f.authorID {
		return commentFailure("identity", false, fmt.Errorf("ERR_ACCOUNT_UNCONFIRMED: 当前页面未核实为预期发送账号"))
	}
	input, err := page.Element("div.input-box div.content-edit p.content-input")
	if err != nil {
		return commentFailure("input", false, err)
	}
	// 元素等待的子期限不应用于整段输入。
	input, err = page.ElementFromObject(input.Object)
	if err != nil {
		return commentFailure("input", false, err)
	}
	text, err := input.Text()
	if err != nil || strings.TrimSpace(text) != "" {
		return commentFailure("input_not_empty", false, fmt.Errorf("输入框已有内容或无法读取，保留现场"))
	}
	execution.Stage(ctx, "compose_comment", 60*time.Second)
	if err := humanize.FillDraft(ctx, input, content); err != nil {
		return commentFailure("type", false, err)
	}
	text, err = input.Text()
	if err != nil || normalizeCommentText(text) != normalizeCommentText(content) {
		return commentFailure("verify_input", false, fmt.Errorf("输入内容未完整匹配"))
	}
	before, err := matchingComments(page, content, f.authorID, commentID, userID, reply)
	if err != nil {
		return commentFailure("snapshot", false, err)
	}
	if len(before) > 0 {
		return commentFailure("duplicate_on_page", false, fmt.Errorf("页面已有此账号的同内容评论，先核对历史记录"))
	}
	button, err := page.Element("div.bottom button.submit")
	if err != nil {
		return commentFailure("submit_button", false, err)
	}
	if err := button.WaitEnabled(); err != nil {
		return commentFailure("submit_disabled", false, err)
	}
	if err := commentPageError(page); err != nil {
		return commentFailure("before_submit", false, err)
	}
	user, identityErr = NewLogin(page).CurrentUser(ctx)
	if identityErr != nil || user.UserID != f.authorID {
		return commentFailure("before_submit", false, fmt.Errorf("ERR_ACCOUNT_UNCONFIRMED: 页面账号已改变或无法核实"))
	}
	humanize.Delay(ctx, humanize.BeforeSubmit)
	if err := ctx.Err(); err != nil {
		return commentFailure("before_submit", false, err)
	}
	// The element lookup's deadline must not expire during identity checks and
	// the pre-submit pause. Preparation is bounded afresh, without losing the
	// caller's deadline. Mark dispatch only before the irreversible mouse press.
	button, err = page.ElementFromObject(button.Object)
	if err != nil {
		return commentFailure("submit_preparation", false, err)
	}
	execution.Stage(ctx, "prepare_comment_click", 12*time.Second)
	submitted := false
	if err := humanize.ClickWithBeforePress(button, func() error {
		if err := ctx.Err(); err != nil {
			return err
		}
		execution.Stage(ctx, "submit_comment", 12*time.Second)
		execution.MarkDispatch(ctx)
		submitted = true
		return nil
	}); err != nil {
		return commentFailure("submit", submitted, err)
	}
	execution.Stage(ctx, "confirm_comment", 15*time.Second)
	if err := waitNewComment(ctx, page, content, f.authorID, commentID, userID, reply, before); err != nil {
		return commentFailure("confirm", true, err)
	}
	logrus.Info("已确认当前账号的新评论，任务结束")
	return nil
}

func normalizeCommentText(text string) string { return strings.Join(strings.Fields(text), " ") }

// 不等整页 load 事件；第三方资源一直加载不能拖住已可用的评论区。
func waitCommentPageReady(ctx context.Context, page *rod.Page) error {
	ctx, cancel := context.WithTimeout(ctx, commentStepTimeout)
	defer cancel()
	p := page.Context(ctx)
	for {
		if err := commentPageError(p); err != nil {
			return err
		}
		value, err := p.Eval(`() => !!document.querySelector('.comments-container, div.input-box, .interact-container, .note-scroller')`)
		if err != nil {
			return err
		}
		if value.Value.Bool() {
			return nil
		}
		if err := waitCommentPoll(ctx); err != nil {
			return err
		}
	}
}

func waitCommentPoll(ctx context.Context) error {
	timer := time.NewTimer(200 * time.Millisecond)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func commentPageError(page *rod.Page) error {
	if err := page.GetContext().Err(); err != nil {
		return err
	}
	wall, reason := detectLoginWall(page)
	if reason == "eval_failed" {
		return fmt.Errorf("ERR_COMMENT_PAGE_UNAVAILABLE: 无法读取页面")
	}
	if wall {
		return errFromLoginWall(reason)
	}
	res, err := page.Eval(`() => {
		const e = document.querySelector('.access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper');
		return e ? e.innerText.trim() : '';
	}`)
	if err != nil {
		return err
	}
	if res.Value.Str() != "" {
		return fmt.Errorf("ERR_COMMENT_PAGE_UNAVAILABLE: 笔记不可访问")
	}
	res, err = page.Eval(`() => [...document.querySelectorAll('[role="alert"], .toast, .message')].filter(e => e.getClientRects().length > 0).map(e => e.innerText).join('\n')`)
	if err != nil {
		return err
	}
	message := res.Value.Str()
	for _, term := range []string{"发送失败", "评论失败", "操作频繁", "操作太频繁", "评论功能受限", "禁言", "无权限"} {
		if strings.Contains(message, term) {
			return fmt.Errorf("ERR_PERMISSION: 平台提示评论失败或受限，保存进度后处理")
		}
	}
	return nil
}

// 一次查询所有已渲染评论，不逐条等待不存在的作者节点。
const locateCommentJS = `(id, user) => {
	const root = document.querySelector('.comments-container');
	if (!root) return [];
	const ownUser = e => [...e.querySelectorAll('[data-user-id], a[href*="/user/profile/"]')].some(a => {
		if (a.closest('.comment-item') !== e) return false;
		if (a.getAttribute('data-user-id') === user) return true;
		try { return new URL(a.href, location.href).pathname.split('/user/profile/')[1]?.split('/')[0] === user; } catch { return false; }
	});
	const items = [...root.querySelectorAll('.comment-item')];
	if (id) return items.filter(e => e.id === 'comment-' + id && (!user || ownUser(e)));
	return items.filter(ownUser);
}`

func locateComment(page *rod.Page, commentID, userID string) (*rod.Element, error) {
	items, err := page.ElementsByJS(rod.Eval(locateCommentJS, commentID, userID))
	if err != nil {
		return nil, err
	}
	if len(items) > 1 {
		return nil, fmt.Errorf("ERR_COMMENT_TARGET_AMBIGUOUS: 作者有多条评论，请提供准确 comment_id")
	}
	if len(items) == 1 {
		return items[0], nil
	}
	return nil, nil
}

func lookupComment(page *rod.Page, commentID, userID string) *rod.Element {
	el, _ := locateComment(page, commentID, userID)
	return el
}

func findCommentElement(ctx context.Context, page *rod.Page, commentID, userID string) (*rod.Element, error) {
	ctx, cancel := context.WithTimeout(ctx, commentSearchTimeout)
	defer cancel()
	p := page.Context(ctx)
	lastCount, stagnant := -1, 0
	// 使用 DOM 节点标记，使同一无效展开按钮不会被反复点击。
	_, err := p.Eval(`() => { window.__xhsExpandedForTask = new WeakSet(); }`)
	if err != nil {
		return nil, err
	}
	for round := 0; round < maxSearchScrolls; round++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if err := commentPageError(p); err != nil {
			return nil, err
		}
		el, err := locateComment(p, commentID, userID)
		if err != nil {
			return nil, err
		}
		if el != nil {
			return page.ElementFromObject(el.Object)
		}
		// 一轮至多展开一个，排除不在视口附近的按钮。
		button, err := p.Sleeper(rod.NotFoundSleeper).ElementByJS(rod.Eval(`() => [...document.querySelectorAll('.comments-container .show-more')].find(e => {
			const r = e.getBoundingClientRect();
			return !window.__xhsExpandedForTask.has(e) && r.width >= 24 && r.height >= 10 && r.top < innerHeight && r.bottom > 0 && /^展开\s*(\d+\s*条|更多)回复$/.test(e.innerText.trim());
		}) || null`))
		if err == nil && round < maxExpandRounds*maxClickPerRound {
			_, _ = button.Eval(`() => { window.__xhsExpandedForTask.add(this); }`)
			if err := humanize.Click(button.Timeout(2 * time.Second)); err == nil {
				humanize.Delay(ctx, humanize.Reading)
				continue
			}
		}
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		stats, err := p.Eval(`() => ({count:document.querySelectorAll('.comments-container .comment-item').length, end: /THE\s*END/i.test(document.querySelector('.end-container')?.innerText || '')})`)
		if err != nil {
			return nil, err
		}
		m := stats.Value.Map()
		if m["end"].Bool() {
			break
		}
		count := m["count"].Int()
		if count == lastCount {
			stagnant++
		} else {
			lastCount, stagnant = count, 0
		}
		if stagnant >= 3 {
			break
		}
		scroller, err := p.Sleeper(rod.NotFoundSleeper).Element(".note-scroller, .comments-container")
		if err != nil {
			return nil, err
		}
		if err := scroller.ScrollIntoView(); err != nil {
			return nil, err
		}
		if err := humanize.Hover(scroller); err != nil {
			return nil, err
		}
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if err := p.Mouse.Scroll(0, 480, 4); err != nil {
			return nil, err
		}
		humanize.Delay(ctx, humanize.BetweenScroll)
	}
	return nil, fmt.Errorf("ERR_COMMENT_TARGET_NOT_FOUND: 有限查找未找到准确目标，保留 comment_id")
}

// 只读每条评论自己的正文/作者；排除编辑框、旧节点与其他人的同文评论。
const matchingCommentsJS = `(text, author, targetID, targetUser, reply) => {
	const root = document.querySelector('.comments-container');
	if (!root) return [];
	const norm = s => s.replace(/\s+/g, ' ').trim();
	const ownUser = (e, id) => [...e.querySelectorAll('[data-user-id], a[href*="/user/profile/"]')].some(a => {
		if (a.closest('.comment-item') !== e) return false;
		if (a.getAttribute('data-user-id') === id) return true;
		try { return new URL(a.href, location.href).pathname.split('/user/profile/')[1]?.split('/')[0] === id; } catch { return false; }
	});
	const items = [...root.querySelectorAll('.comment-item')];
	const target = reply ? (targetID ? items.find(t => t.id === 'comment-' + targetID) : items.find(t => ownUser(t, targetUser))) : null;
	const isReply = e => {
		const thread = e.closest('.parent-comment');
		const main = thread && (thread.matches('.comment-item') ? thread : thread.querySelector('.comment-item'));
		return !!e.parentElement.closest('.comment-item') || !!(main && main !== e);
	};
	return items.filter(e => {
		if (!e.id || e.closest('.input-box, [contenteditable="true"]')) return false;
		const body = [...e.querySelectorAll('.content')].find(c => c.closest('.comment-item') === e && !c.closest('.input-box, [contenteditable="true"]'));
		if (!body || norm(body.innerText) !== norm(text) || !ownUser(e, author)) return false;
		if (!reply) return !isReply(e);
		if (!target) return false;
		const thread = target.closest('.parent-comment') || target;
		return e !== target && thread.contains(e) && isReply(e);
	}).map(e => e.id);
}`

func matchingComments(page *rod.Page, content, author, targetID, targetUser string, reply bool) ([]string, error) {
	value, err := page.Eval(matchingCommentsJS, content, author, targetID, targetUser, reply)
	if err != nil {
		return nil, err
	}
	ids := []string{}
	for _, id := range value.Value.Arr() {
		ids = append(ids, id.Str())
	}
	return ids, nil
}

func waitNewComment(ctx context.Context, page *rod.Page, content, author, targetID, targetUser string, reply bool, before []string) error {
	ctx, cancel := context.WithTimeout(ctx, commentConfirmTime)
	defer cancel()
	p := page.Context(ctx)
	old := map[string]bool{}
	firstSeen := map[string]time.Time{}
	for _, id := range before {
		old[id] = true
	}
	for {
		if err := commentPageError(p); err != nil {
			return err
		}
		ids, err := matchingComments(p, content, author, targetID, targetUser, reply)
		if err != nil {
			return err
		}
		for _, id := range ids {
			if !old[id] {
				if firstSeen[id].IsZero() {
					firstSeen[id] = time.Now()
				}
				cleared, readErr := p.Eval(`() => !document.querySelector('div.input-box p.content-input')?.innerText.trim()`)
				if readErr != nil {
					return readErr
				}
				if cleared.Value.Bool() && time.Since(firstSeen[id]) >= time.Second {
					return nil
				}
			}
		}
		for id := range firstSeen {
			found := false
			for _, current := range ids {
				if current == id {
					found = true
				}
			}
			if !found {
				delete(firstSeen, id)
			}
		}
		if err := waitCommentPoll(ctx); err != nil {
			return fmt.Errorf("提交后未确认新评论，请查平台结果: %w", err)
		}
	}
}
