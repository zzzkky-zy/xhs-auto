package xiaohongshu

import (
	"fmt"
	"github.com/go-rod/rod"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"time"
)

func waitKeyState(page *rod.Page, expression string, timeout time.Duration) error {
	p := page.Timeout(timeout)
	for {
		if err := p.GetContext().Err(); err != nil {
			return fmt.Errorf("ERR_PAGE_READY_TIMEOUT: %w", err)
		}
		v, err := p.Eval(expression)
		if err == nil && v.Value.Bool() {
			return nil
		}
		if err := execution.Sleep(p.GetContext(), 150*time.Millisecond); err != nil {
			return fmt.Errorf("ERR_PAGE_READY_TIMEOUT: %w", err)
		}
	}
}
func pause(page *rod.Page, d time.Duration) {
	if err := execution.Sleep(page.GetContext(), d); err != nil {
		panic(err)
	}
}
func alreadyAtFeed(page *rod.Page, target string) bool {
	info, err := page.Info()
	return err == nil && info.URL == target && feedPageLooksReady(page)
}
