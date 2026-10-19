//go:build fixture

package humanize

import (
	"context"
	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/xpzouying/xiaohongshu-mcp/internal/fixturebrowser"
	"strings"
	"testing"
	"time"
)

// A locked/occluded host can activate a tab without producing animation frames.
// No mouse press, send callback or typing may happen in that state.
func TestInactivePageStopsBeforeInput(t *testing.T) {
	bin := fixturebrowser.Require(t)
	l := launcher.New().Bin(bin).HeadlessNew(true).Set("disable-gpu").UserDataDir(t.TempDir()).Leakless(false)
	u, err := l.Launch()
	if err != nil {
		t.Fatal(err)
	}
	b := rod.New().ControlURL(u).MustConnect()
	defer func() { _ = b.Close(); l.Kill() }()
	p := b.MustPage("about:blank")
	p.MustSetDocumentContent(`<button id="btn">send</button><input id="edit"><script>window.inputEvents=0;for(const type of ['mousedown','mouseup','input'])document.addEventListener(type,()=>window.inputEvents++);window.requestAnimationFrame=()=>0;</script>`)
	for _, mode := range []string{"click", "type"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			beforePress := false
			start := time.Now()
			if mode == "click" {
				err = ClickWithBeforePress(p.Context(ctx).MustElement("#btn"), func() error { beforePress = true; return nil })
			} else {
				err = Type(ctx, p.MustElement("#edit"), "must not type")
			}
			if err == nil || !strings.Contains(err.Error(), "ERR_BROWSER_INACTIVE") {
				t.Fatalf("expected actionable inactive-page failure, got %v", err)
			}
			if time.Since(start) > 4*time.Second {
				t.Fatalf("inactive page waited too long: %s", time.Since(start))
			}
			if beforePress || p.MustEval(`() => window.inputEvents`).Int() != 0 || p.MustElement("#edit").MustProperty("value").Str() != "" {
				t.Fatal("inactive page received input")
			}
		})
	}
}
