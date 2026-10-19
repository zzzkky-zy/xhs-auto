//go:build fixture

package browser

import (
	"context"
	"github.com/xpzouying/xiaohongshu-mcp/internal/fixturebrowser"
	"testing"
	"time"

	"github.com/go-rod/rod/lib/launcher"
	"github.com/go-rod/rod/lib/proto"
	"github.com/stretchr/testify/require"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

func TestFixtureAttachedInputLifetimeAndCancellation(t *testing.T) {
	bin := fixturebrowser.Require(t)
	l := launcher.New().Bin(bin).Headless(true).UserDataDir(t.TempDir()).Leakless(false)
	control, err := l.Launch()
	require.NoError(t, err)
	defer l.Kill()
	t.Setenv("XHS_CDP_URL", control)
	b := NewBrowser(true)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	p := b.NewPageContext(ctx)
	defer p.Context(context.Background()).Close()
	require.NoError(t, p.SetDocumentContent(`<html><body><button onclick="window.clicked=(window.clicked||0)+1">模拟按钮</button></body></html>`))
	el, err := p.Element("button")
	require.NoError(t, err)
	require.NoError(t, humanize.Click(el))
	value, err := p.Eval(`() => window.clicked`)
	require.NoError(t, err)
	require.Equal(t, 1, value.Value.Int())
	cancel()
	require.ErrorIs(t, p.Mouse.MoveTo(proto.Point{X: 100, Y: 100}), context.Canceled)
	// 任务取消不取消全局连接；下一项任务仍可新建并执行页面事件。
	q := b.NewPageContext(context.Background())
	defer q.Close()
	require.NoError(t, q.SetDocumentContent(`<html><body>next</body></html>`))
	require.NoError(t, q.Timeout(time.Second).WaitLoad())
}

// An operator may use another tab while the task is running. Hidden tabs stop
// animation frames and input acknowledgements; activate the owned task page
// before interacting with it, leaving unrelated pages open.
func TestFixtureAttachedInputOnBackgroundPage(t *testing.T) {
	bin := fixturebrowser.Require(t)
	l := launcher.New().Bin(bin).Headless(true).UserDataDir(t.TempDir()).Leakless(false)
	control, err := l.Launch()
	require.NoError(t, err)
	defer l.Kill()
	t.Setenv("XHS_CDP_URL", control)
	b := NewBrowser(true)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	p := b.NewPageContext(ctx)
	defer p.Context(context.Background()).Close()
	require.NoError(t, p.SetDocumentContent(`<html><body><button onclick="window.clicked=(window.clicked||0)+1">展开评论</button><input></body></html>`))
	other := b.NewPageContext(context.Background())
	defer other.Close()
	require.NoError(t, other.SetDocumentContent(`<html><body>另一个页面</body></html>`))
	_, err = other.Activate()
	require.NoError(t, err)
	visibility, err := p.Eval(`() => document.visibilityState`)
	require.NoError(t, err)
	require.Equal(t, "hidden", visibility.Value.Str())
	button, err := p.Element("button")
	require.NoError(t, err)
	require.NoError(t, humanize.Click(button))
	clicked, err := p.Eval(`() => window.clicked`)
	require.NoError(t, err)
	require.Equal(t, 1, clicked.Value.Int())
	_, err = other.Activate()
	require.NoError(t, err)
	input, err := p.Element("input")
	require.NoError(t, err)
	require.NoError(t, humanize.Type(ctx, input, "准备好了"))
	value, err := input.Property("value")
	require.NoError(t, err)
	require.Equal(t, "准备好了", value.Str())
	visibility, err = other.Eval(`() => document.visibilityState`)
	require.NoError(t, err)
	require.Equal(t, "hidden", visibility.Value.Str())
	require.NoError(t, other.SetDocumentContent(`<html><body>仍保留的页面</body></html>`))
}
