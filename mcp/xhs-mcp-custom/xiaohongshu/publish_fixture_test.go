//go:build fixture

package xiaohongshu

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

func TestFixturePublishConfirmationRegression(t *testing.T) {
	b := commentFixtureBrowser(t)
	humanize.SetProvider(fixtureCommentTiming{})
	defer humanize.SetProvider(humanize.DefaultProvider{})
	t.Run("article words never count as success toast", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div contenteditable="true">这篇正文介绍如何发布成功</div>`)
		ok, _ := peekPublishSuccessToast(p)
		require.False(t, ok)
	})
	t.Run("hidden old success toast never counts", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="d-new-toast" style="display:none">发布成功</div>`)
		ok, _ := peekPublishSuccessToast(p)
		require.False(t, ok)
	})
	t.Run("unrelated page never confirms publication", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div>登录页面</div>`)
		require.Error(t, waitPublishSuccess(p, 200*time.Millisecond))
	})
	t.Run("scheduled primary button is never clicked again", func(t *testing.T) {
		p := commentFixturePage(t, b, `<button onclick="window.clicked=(window.clicked||0)+1">定时发布</button>`)
		require.NoError(t, confirmScheduledPublishDialog(p))
		value, err := p.Eval(`() => window.clicked || 0`)
		require.NoError(t, err)
		require.Zero(t, value.Value.Int())
	})
	t.Run("canceled resource check cannot pretend ready", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div>笔记图片生成中</div>`)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		require.ErrorIs(t, waitPublishAssetsReady(p.Context(ctx), time.Second), context.Canceled)
	})
	t.Run("fresh visible success toast confirms", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="d-new-toast">发布成功</div>`)
		require.NoError(t, waitPublishSuccess(p, time.Second))
	})
	t.Run("visible toast from before submit is not a new receipt", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="d-new-toast">发布成功</div>`)
		require.NoError(t, snapshotPublishSuccessToasts(p))
		require.Error(t, waitPublishSuccess(p, 200*time.Millisecond))
	})
	t.Run("new toast after snapshot confirms", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="d-new-toast">发布成功</div>`)
		require.NoError(t, snapshotPublishSuccessToasts(p))
		_, err := p.Eval(`() => { const e=document.createElement('div');e.className='d-new-toast';e.innerText='定时发布成功';document.body.append(e) }`)
		require.NoError(t, err)
		require.NoError(t, waitPublishSuccess(p, time.Second))
	})
	t.Run("confirmation clicks only in scheduling dialog once", func(t *testing.T) {
		p := commentFixturePage(t, b, `<button onclick="window.primary=(window.primary||0)+1">定时发布</button><div role="dialog" style="padding:30px;border:1px solid">确认定时发布<button onclick="window.confirmed=(window.confirmed||0)+1;this.closest('[role=dialog]').remove()">确认发布</button></div>`)
		require.NoError(t, confirmScheduledPublishDialog(p))
		value, err := p.Eval(`() => [window.primary || 0,window.confirmed || 0]`)
		require.NoError(t, err)
		require.Zero(t, value.Value.Arr()[0].Int())
		require.Equal(t, 1, value.Value.Arr()[1].Int())
	})
	t.Run("missing title stops before clicking", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="d-input"><input value=""></div>`)
		require.Error(t, ensurePublishFormTitle(context.Background(), p, ""))
	})
	t.Run("changed title is not silently accepted", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="d-input"><input value="另一标题"></div>`)
		require.Error(t, ensurePublishFormTitle(context.Background(), p, "已审标题"))
	})
	t.Run("real submit helper sees one new receipt", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="d-input"><input value="已审标题"></div><div class="publish-page-publish-btn"><button class="bg-red" onclick="window.clicked=(window.clicked||0)+1;const e=document.createElement('div');e.className='d-new-toast';e.innerText='发布成功';document.body.append(e)">发布</button></div>`)
		require.NoError(t, submitPublish(context.Background(), p, "已审标题", "", nil, nil, false, "", nil, "", "", true))
		value, err := p.Eval(`() => window.clicked || 0`)
		require.NoError(t, err)
		require.Equal(t, 1, value.Value.Int())
	})
	t.Run("widget exception after dispatch never triggers a fallback click", func(t *testing.T) {
		p := commentFixturePage(t, b, `<xhs-publish-btn is-publish="true" style="display:block;width:600px;height:60px" onclick="window.clicks=(window.clicks||0)+1"></xhs-publish-btn><script>document.querySelector('xhs-publish-btn')._onPublish=()=>{window.calls=(window.calls||0)+1;throw new Error('dispatched before response failed')}</script>`)
		ctx := execution.TrackSubmission(context.Background())
		require.Error(t, clickPublishButton(p.Context(ctx)))
		require.False(t, execution.NotDispatched(ctx))
		value, err := p.Eval(`() => [window.calls || 0,window.clicks || 0]`)
		require.NoError(t, err)
		require.Equal(t, 1, value.Value.Get("0").Int())
		require.Zero(t, value.Value.Get("1").Int())
	})
	t.Run("waiting for disabled publish button respects caller cancellation", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="publish-page-publish-btn"><button class="bg-red" disabled>发布</button></div>`)
		ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
		defer cancel()
		start := time.Now()
		_, err := waitForPublishButtonClickable(p.Context(ctx), 15*time.Second)
		require.ErrorIs(t, err, context.DeadlineExceeded)
		require.Less(t, time.Since(start), time.Second)
	})
}
