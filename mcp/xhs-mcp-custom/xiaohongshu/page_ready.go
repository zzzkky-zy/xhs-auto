package xiaohongshu

import (
	"time"

	"github.com/go-rod/rod"
	"github.com/sirupsen/logrus"
)

// feedReadyTimeout 等待详情页关键容器的上限，超时不算失败。
const feedReadyTimeout = 8 * time.Second

// feedReadySelectors 详情页已出结果的判据：正常笔记容器，或 checkPageAccessible 认的错误容器。
const feedReadySelectors = ".interact-container, .note-scroller, " +
	".access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper"

// waitFeedPageReady 等待 feed 详情页可用。
// attach 到真实 Chrome/SPA 时勿 MustWaitLoad / MustWaitStable：持续导航会报
// "Inspected target navigated or closed" 并 panic。改为忽略 WaitLoad 错误，
// 轮询关键容器或 __INITIAL_STATE__ note 注水。
func waitFeedPageReady(page *rod.Page) {

	deadline := time.Now().Add(feedReadyTimeout)
	for time.Now().Before(deadline) {
		ready, err := page.Eval(`() => {
			if (document.querySelector('.interact-container, .note-scroller, .access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper')) {
				return true;
			}
			const st = window.__INITIAL_STATE__;
			if (!st) return false;
			if (st.note || st.noteDetailMap) return true;
			return false;
		}`)
		if err == nil && ready.Value.Bool() {
			return
		}
		pause(page, 300*time.Millisecond)
	}

	logrus.Warnf("详情页关键容器未在 %s 内出现，继续尝试", feedReadyTimeout)
}

// feedPageLooksReady 供导航重试用：关键容器或 note 状态是否已出现。
func feedPageLooksReady(page *rod.Page) bool {
	ready, err := page.Eval(`() => {
		if (document.querySelector('.interact-container, .note-scroller, .access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper')) {
			return true;
		}
		const st = window.__INITIAL_STATE__;
		return !!(st && (st.note || st.noteDetailMap));
	}`)
	return err == nil && ready.Value.Bool()
}
