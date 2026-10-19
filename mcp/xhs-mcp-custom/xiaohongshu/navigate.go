package xiaohongshu

import (
	"context"
	"time"

	"github.com/go-rod/rod"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

type NavigateAction struct {
	page *rod.Page
}

func NewNavigate(page *rod.Page) *NavigateAction {
	return &NavigateAction{page: page}
}

func (n *NavigateAction) ToExplorePage(ctx context.Context) error {
	page := n.page.Context(ctx).Timeout(60 * time.Second) // 加超时保护，避免 MustNavigate/MustWaitStable 无限挂

	if err := page.Navigate(webBaseURL() + "/explore"); err != nil {
		return err
	}
	return waitKeyState(page, `() => !!document.querySelector('div#app')`, 8*time.Second)
}

func (n *NavigateAction) ToProfilePage(ctx context.Context) error {
	page := n.page.Context(ctx).Timeout(60 * time.Second) // 加超时保护，避免 MustNavigate/MustWaitStable 无限挂

	// First navigate to explore page
	if err := n.ToExplorePage(ctx); err != nil {
		return err
	}

	// Find and click the "我" channel link in sidebar
	profileLink, err := page.Timeout(8 * time.Second).Element(`div.main-container li.user.side-bar-component a.link-wrapper span.channel`)
	if err != nil {
		return err
	}
	humanize.Delay(ctx, humanize.BeforeClick)
	if err := humanize.Click(profileLink); err != nil {
		return err
	}

	return waitKeyState(page, `() => location.pathname.startsWith('/user/profile/')`, 8*time.Second)
}
