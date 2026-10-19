package xiaohongshu

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/input"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

const urlOfChat = "https://www.xiaohongshu.com/chat"

// DirectMessageConversation 私信会话列表项（侧栏）。
type DirectMessageConversation struct {
	ConversationID string `json:"conversation_id,omitempty"` // 打开会话后 URL /chat/{id}；列表态可能为空
	Name           string `json:"name"`
	Time           string `json:"time,omitempty"`
	Summary        string `json:"summary,omitempty"`
	Pinned         bool   `json:"pinned"`
}

// DirectMessageList 私信会话列表。
type DirectMessageList struct {
	Conversations []DirectMessageConversation `json:"conversations"`
	Count         int                         `json:"count"`
	URL           string                      `json:"url"`
	Note          string                      `json:"note,omitempty"`
}

// DirectMessageReplyResult 私信回复/打开会话结果。
type DirectMessageReplyResult struct {
	ConversationID string `json:"conversation_id,omitempty"`
	Name           string `json:"name,omitempty"`
	Opened         bool   `json:"opened"`
	Sent           bool   `json:"sent"`
	OpenOnly       bool   `json:"open_only"`
	Message        string `json:"message"`
	URL            string `json:"url,omitempty"`
}

// DMAction 私信页自动化（www /chat）。
type DMAction struct {
	page *rod.Page
}

func NewDMAction(page *rod.Page) *DMAction {
	return &DMAction{page: page}
}

func (a *DMAction) navigateChat(ctx context.Context) error {
	page := a.page.Context(ctx).Timeout(60 * time.Second)
	if err := page.Navigate(urlOfChat); err != nil {
		return errors.Wrap(err, "导航私信页失败")
	}
	_ = page.WaitLoad()
	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		has, _, err := page.Has(".xhs-im-conv-list, .xhs-im-page, .xhs-im-view")
		if err == nil && has {
			time.Sleep(500 * time.Millisecond)
			return nil
		}
		time.Sleep(300 * time.Millisecond)
	}
	logrus.Warn("私信页关键容器未及时出现，继续尝试刮列表")
	return nil
}

// ListConversations 只读：打开 /chat，刮侧栏会话。
func (a *DMAction) ListConversations(ctx context.Context, limit int) (*DirectMessageList, error) {
	if limit <= 0 {
		limit = 30
	}
	if err := a.navigateChat(ctx); err != nil {
		return nil, err
	}
	page := a.page.Context(ctx).Timeout(45 * time.Second)

	items, err := page.Elements(".xhs-im-conv-item")
	if err != nil {
		return nil, errors.Wrap(err, "查找私信会话列表失败（.xhs-im-conv-item）")
	}

	out := make([]DirectMessageConversation, 0, len(items))
	for _, item := range items {
		if len(out) >= limit {
			break
		}
		name := textOf(item, ".xhs-im-conv-item__name")
		if name == "" {
			continue
		}
		cls, _ := item.Attribute("class")
		pinned := cls != nil && strings.Contains(*cls, "xhs-im-conv-item--pinned")
		out = append(out, DirectMessageConversation{
			Name:    name,
			Time:    textOf(item, ".xhs-im-conv-item__time"),
			Summary: textOf(item, ".xhs-im-conv-item__summary-text"),
			Pinned:  pinned,
		})
	}

	info, _ := page.Info()
	url := ""
	if info != nil {
		url = info.URL
	}
	note := "只读列表；conversation_id 需打开会话后从 URL /chat/{id} 获得。回复用 reply_direct_message（写操作需 XHS_ALLOW_WRITE=1；open_only=true 仅打开会话不发送）。"
	return &DirectMessageList{
		Conversations: out,
		Count:         len(out),
		URL:           url,
		Note:          note,
	}, nil
}

// ReplyOrOpen 打开会话；openOnly 时不发送。发送走 contenteditable + Enter。
// conversationID 与 name 至少一个；优先 conversationID（URL 直达）。
func (a *DMAction) ReplyOrOpen(ctx context.Context, conversationID, name, content string, openOnly bool) (*DirectMessageReplyResult, error) {
	conversationID = strings.TrimSpace(conversationID)
	name = strings.TrimSpace(name)
	content = strings.TrimSpace(content)
	if conversationID == "" && name == "" {
		return nil, errors.New("需要 conversation_id 或 name 至少一个")
	}
	if !openOnly && content == "" {
		return nil, errors.New("发送私信需要 content；若仅打开会话请设 open_only=true")
	}

	page := a.page.Context(ctx).Timeout(90 * time.Second)

	if conversationID != "" {
		url := fmt.Sprintf("%s/%s", urlOfChat, conversationID)
		if err := page.Navigate(url); err != nil {
			return nil, errors.Wrap(err, "按 conversation_id 打开私信失败")
		}
		_ = page.WaitLoad()
		time.Sleep(2 * time.Second)
	} else {
		if err := a.navigateChat(ctx); err != nil {
			return nil, err
		}
		page = a.page.Context(ctx).Timeout(90 * time.Second)
		if err := clickConvByName(page, name); err != nil {
			return nil, err
		}
		time.Sleep(2 * time.Second)
	}

	// 解析打开后的 conversation_id / 对方昵称
	info, _ := page.Info()
	openedURL := ""
	resolvedID := conversationID
	if info != nil {
		openedURL = info.URL
		if resolvedID == "" {
			resolvedID = convIDFromURL(openedURL)
		}
	}
	headerName := textOfPage(page, ".xhs-im-chat-window__header-name")
	if headerName == "" {
		headerName = name
	}

	// 确认输入框出现
	editor, err := waitDMEditor(page, 10*time.Second)
	if err != nil {
		return &DirectMessageReplyResult{
			ConversationID: resolvedID,
			Name:           headerName,
			Opened:         strings.Contains(openedURL, "/chat/"),
			Sent:           false,
			OpenOnly:       openOnly,
			Message:        "会话可能已打开但未找到输入框（.xhs-im-input-bar-editor）：" + err.Error(),
			URL:            openedURL,
		}, nil
	}

	result := &DirectMessageReplyResult{
		ConversationID: resolvedID,
		Name:           headerName,
		Opened:         true,
		OpenOnly:       openOnly,
		URL:            openedURL,
	}

	if openOnly {
		result.Sent = false
		result.Message = "已打开私信会话（open_only，未发送）"
		return result, nil
	}

	if err := humanize.Click(editor); err != nil {
		return nil, errors.Wrap(err, "聚焦私信输入框失败")
	}
	humanize.Delay(ctx, humanize.BeforeClick)
	if err := humanize.Type(ctx, editor, content); err != nil {
		return nil, errors.Wrap(err, "输入私信内容失败")
	}
	humanize.Delay(ctx, humanize.AfterType)

	// 页面无显式「发送」按钮文案；用 Enter 提交（与常见 IM 一致）
	if err := page.Keyboard.Press(input.Enter); err != nil {
		return nil, errors.Wrap(err, "按 Enter 发送私信失败")
	}
	time.Sleep(1200 * time.Millisecond)

	result.Sent = true
	result.Message = "私信已尝试发送（Enter）；请以会话气泡为准核实"
	if info2, err := page.Info(); err == nil && info2 != nil {
		result.URL = info2.URL
		if result.ConversationID == "" {
			result.ConversationID = convIDFromURL(info2.URL)
		}
	}
	return result, nil
}

func clickConvByName(page *rod.Page, name string) error {
	items, err := page.Elements(".xhs-im-conv-item")
	if err != nil {
		return errors.Wrap(err, "查找会话列表失败")
	}
	for _, item := range items {
		n := textOf(item, ".xhs-im-conv-item__name")
		if n == name || strings.Contains(n, name) || strings.Contains(name, n) {
			if err := humanize.Click(item); err != nil {
				return errors.Wrapf(err, "点击会话 %q 失败", n)
			}
			logrus.Infof("已打开私信会话: %s", n)
			return nil
		}
	}
	return errors.Errorf("未找到名称为 %q 的私信会话", name)
}

func waitDMEditor(page *rod.Page, timeout time.Duration) (*rod.Element, error) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		el, err := page.Element(".xhs-im-input-bar-editor")
		if err == nil && el != nil {
			return el, nil
		}
		time.Sleep(300 * time.Millisecond)
	}
	return nil, errors.New("超时未出现 .xhs-im-input-bar-editor")
}

func convIDFromURL(u string) string {
	// https://www.xiaohongshu.com/chat/<id>
	const marker = "/chat/"
	i := strings.Index(u, marker)
	if i < 0 {
		return ""
	}
	rest := u[i+len(marker):]
	if j := strings.IndexAny(rest, "?#/"); j >= 0 {
		rest = rest[:j]
	}
	return strings.TrimSpace(rest)
}

func textOf(el *rod.Element, sel string) string {
	child, err := el.Element(sel)
	if err != nil || child == nil {
		return ""
	}
	t, _ := child.Text()
	return strings.TrimSpace(t)
}

func textOfPage(page *rod.Page, sel string) string {
	el, err := page.Element(sel)
	if err != nil || el == nil {
		return ""
	}
	t, _ := el.Text()
	return strings.TrimSpace(t)
}
