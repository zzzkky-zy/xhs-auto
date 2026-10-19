//go:build fixture

package xiaohongshu

import (
	"context"
	"fmt"
	"github.com/xpzouying/xiaohongshu-mcp/internal/fixturebrowser"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/go-rod/rod/lib/proto"
	"github.com/stretchr/testify/require"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

// 临时无登录浏览器，只使用本机 HTML；不会访问或写入平台。
func commentFixtureBrowser(t *testing.T) *rod.Browser {
	t.Helper()
	bin := fixturebrowser.Require(t)
	l := launcher.New().Bin(bin).Headless(true).UserDataDir(t.TempDir()).Leakless(false)
	control, err := l.Launch()
	require.NoError(t, err)
	b := rod.New().ControlURL(control)
	require.NoError(t, b.Connect())
	t.Cleanup(func() { _ = b.Close(); l.Kill() })
	return b
}

type fixtureCommentTiming struct{}

func (fixtureCommentTiming) Timing() humanize.TimingProfile {
	profile := humanize.DefaultProvider{}.Timing()
	for key := range profile {
		profile[key] = humanize.LogNormal{Mu: -12, Min: time.Millisecond, Max: time.Millisecond}
	}
	return profile
}

func TestFixtureCommentAction(t *testing.T) {
	b := commentFixtureBrowser(t)
	humanize.SetProvider(fixtureCommentTiming{})
	defer humanize.SetProvider(humanize.DefaultProvider{})
	// 实际鼠标与输入事件；仅将阅读/输入间隔缩短以测试故障路径。
	cases := []struct {
		name, mode string
		reply      bool
		budget     time.Duration
		ok         bool
		submitted  bool
		captcha    bool
		clicks     int
	}{
		{"primary confirmed", "ack", false, 8 * time.Second, true, true, false, 1},
		{"collapsed editor has clipped wide wrapper", "collapsed", false, 4 * time.Second, true, true, false, 1},
		{"collapsed placeholder covers editable node", "collapsed-overlay", false, 4 * time.Second, true, true, false, 1},
		{"covered placeholder is never bypassed", "collapsed-control-mask", false, 1500 * time.Millisecond, false, false, false, 0},
		{"composer mounts after comment container", "mounting-composer", false, 4 * time.Second, true, true, false, 1},
		{"unrelated cover never receives a click", "collapsed-external-mask", false, 4 * time.Second, false, false, false, 0},
		{"unfinished image load does not block ready comment", "resource-stall", false, 8 * time.Second, true, true, false, 1},
		{"reply confirmed in target thread", "ack", true, 8 * time.Second, true, true, false, 1},
		{"confirmation later than old four seconds", "delayed", false, 9 * time.Second, true, true, false, 1},
		{"draft is not posted evidence", "noack", false, 2500 * time.Millisecond, false, true, false, 1},
		{"another author same words is not evidence", "other", false, 2500 * time.Millisecond, false, true, false, 1},
		{"optimistic node rejected by platform is not success", "rejected", false, 4 * time.Second, false, true, false, 1},
		{"account changed on task page never sends", "wrong-account", false, 3 * time.Second, false, false, false, 0},
		{"existing own comment is not new success", "duplicate", false, 5 * time.Second, false, false, false, 0},
		{"missing input cancels promptly", "missing", false, 600 * time.Millisecond, false, false, false, 0},
		{"unfinished page has bounded pre submit wait", "empty-page", false, 600 * time.Millisecond, false, false, false, 0},
		{"typing cancellation never submits partial text", "typing", false, 600 * time.Millisecond, false, false, false, 0},
		{"disabled send never dispatches", "disabled", false, 2500 * time.Millisecond, false, false, false, 0},
		{"captcha preserves session and stops", "captcha", false, 2 * time.Second, false, false, true, 0},
		{"missing exact reply target never falls back", "missing-target", true, 3 * time.Second, false, false, false, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			html := commentActionHTML(c.mode, c.reply)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/slow-resource" {
					<-r.Context().Done()
					return
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				fmt.Fprint(w, html)
			}))
			defer server.Close()
			t.Setenv("XHS_WEB_BASE_URL", server.URL)
			ctx, cancel := context.WithTimeout(context.Background(), c.budget)
			defer cancel()
			p, err := b.Context(ctx).Page(proto.TargetCreateTarget{})
			require.NoError(t, err)
			defer p.Context(context.Background()).Close()
			start := time.Now()
			action := NewCommentFeedAction(p, "mine")
			content := "具体建议"
			if c.mode == "typing" {
				content = strings.Repeat("字", 1000)
			}
			if c.reply {
				err = action.ReplyToComment(ctx, "fixture", "dummy", "target", "other", content)
			} else {
				err = action.PostComment(ctx, "fixture", "dummy", content)
			}
			elapsed := time.Since(start)
			if c.ok {
				require.NoError(t, err)
			} else {
				var failure *CommentActionError
				require.ErrorAs(t, err, &failure)
				require.Equal(t, c.submitted, failure.Submitted)
				if c.captcha {
					require.ErrorIs(t, err, xhserrors.ErrCaptcha)
				}
				if c.mode == "collapsed-external-mask" || c.mode == "collapsed-control-mask" {
					draft, readErr := p.Context(context.Background()).Timeout(time.Second).Eval(`() => document.querySelector('.content-input').innerText`)
					require.NoError(t, readErr)
					require.Empty(t, draft.Value.Str())
				}
			}
			require.Less(t, elapsed, c.budget+time.Second)
			value, readErr := p.Context(context.Background()).Timeout(time.Second).Eval(`() => window.submitCount || 0`)
			require.NoError(t, readErr)
			require.Equal(t, c.clicks, value.Value.Int())
			t.Logf("wall=%s submit_clicks=%d success=%v", elapsed.Round(time.Millisecond), c.clicks, c.ok)
		})
	}
}

func commentActionHTML(mode string, reply bool) string {
	if mode == "captcha" {
		return `<html><body>Security Verification</body></html>`
	}
	if mode == "missing" {
		return `<html><body><script>window.__INITIAL_STATE__={user:{userInfo:{userId:'mine'}}}</script><div class="comments-container"></div></body></html>`
	}
	if mode == "empty-page" {
		return `<html><body><script>window.__INITIAL_STATE__={user:{userInfo:{userId:'mine'}}}</script></body></html>`
	}
	resource := ""
	overlay := ""
	if mode == "collapsed" {
		resource = `<style>.input-box{width:160px;overflow:hidden}.content-edit{width:650px}p.content-input{width:120px}.bottom{display:none}body.expanded .input-box{width:650px}body.expanded p.content-input{width:550px}body.expanded .bottom{display:block}</style><script>document.addEventListener("click",e=>{if(e.target.closest("p.content-input"))document.body.classList.add("expanded")})</script>`
	}
	if mode == "collapsed-overlay" || mode == "collapsed-control-mask" || mode == "collapsed-external-mask" || mode == "mounting-composer" {
		resource = `<style>.content-edit{position:relative}p.content-input{margin:0}.inner-when-not-active{position:absolute;inset:0;display:flex;align-items:center;background:#ddd}.inner{display:flex;padding:8px}body.expanded .inner-when-not-active{display:none}.bottom{display:none}body.expanded .bottom{display:block}</style><script>document.addEventListener("click",e=>{if(e.target.closest(".inner-when-not-active"))document.body.classList.add("expanded")})</script>`
		overlay = `<div class="not-active inner-when-not-active"><div class="inner"><span>说点什么...</span></div></div>`
		if mode == "collapsed-control-mask" {
			resource += `<style>.input-box{width:160px;overflow:hidden}.content-edit{width:650px}.content-edit::after{content:"";position:absolute;inset:0;z-index:2}body.expanded .input-box{width:650px}body.expanded .content-edit::after{display:none}</style><script>document.addEventListener("click",e=>{if(e.target.closest(".content-edit"))document.body.classList.add("expanded")})</script>`
		}
		if mode == "collapsed-external-mask" {
			resource += `<style>body::after{content:"";position:fixed;inset:0;z-index:999}</style>`
		}
		if mode == "mounting-composer" {
			resource += `<script>document.addEventListener('DOMContentLoaded',()=>{const c=document.querySelector('.content-edit');const markup=c.innerHTML;c.innerHTML='';setTimeout(()=>{c.innerHTML=markup},250)})</script>`
		}
	}
	if mode == "resource-stall" {
		resource = `<img src="/slow-resource">`
	}
	id := "target"
	if mode == "missing-target" {
		id = "another"
	}
	disabled := ""
	if mode == "disabled" {
		disabled = "disabled"
	}
	old := ""
	if mode == "duplicate" {
		old = `<div id="comment-old" class="comment-item"><a data-user-id="mine">我</a><div class="content">具体建议</div></div>`
	}
	return fmt.Sprintf(`<!DOCTYPE html><html><body><style>
body{margin:30px}.comments-container{height:220px;width:650px;overflow:auto}.comment-item{padding:10px;border:1px solid gray}.reply,button{padding:10px;cursor:pointer}p.content-input{min-height:40px;width:550px;border:1px solid gray}
</style>%s<div class="comments-container"><div class="parent-comment"><div class="comment-item" id="comment-%s"><a data-user-id="other">原作者</a><div class="content">原评论</div><div class="right"><div class="interactions"><button class="reply" onclick="window.replying=true">回复</button></div></div></div><div class="replies"></div></div>%s<div class="end-container">THE END</div></div>
<div class="input-box"><div class="content-edit"><p class="content-input" contenteditable="true"></p>%s</div></div><div class="bottom"><button class="submit" %s>发送</button></div>
<script>window.__INITIAL_STATE__={user:{userInfo:{value:{userId:%q,guest:false}}}};window.submitCount=0;document.querySelector('.submit').onclick=()=>{
window.submitCount++;const mode=%q;const reply=%t;const content=document.querySelector('.content-input').innerText;
if(mode==='noack')return;const add=()=>{const e=document.createElement('div');e.className='comment-item';e.id='comment-new';const a=document.createElement('a');a.setAttribute('data-user-id',mode==='other'?'someone-else':'mine');a.textContent='作者';e.append(a);const body=document.createElement('div');body.className='content';body.textContent=content;e.append(body);document.querySelector(reply?'.replies':'.comments-container').append(e);document.querySelector('.content-input').innerText='';};
if(mode==='delayed')setTimeout(add,5000);else add();if(mode==='rejected')setTimeout(()=>{document.querySelector('#comment-new')?.remove();const e=document.createElement('div');e.className='toast';e.innerText='评论失败';document.body.append(e)},300);};</script></body></html>`, resource, id, old, overlay, disabled, fixtureAuthor(mode), mode, reply)
}

func TestFixtureOriginalComposerClickWaitsForMountedPlaceholder(t *testing.T) {
	b := commentFixtureBrowser(t)
	humanize.SetProvider(fixtureCommentTiming{})
	defer humanize.SetProvider(humanize.DefaultProvider{})
	p, err := b.Page(proto.TargetCreateTarget{})
	require.NoError(t, err)
	defer p.Close()
	require.NoError(t, p.SetDocumentContent(commentActionHTML("mounting-composer", false)))
	require.NoError(t, openCommentComposer(p.Timeout(2*time.Second)))
	state, err := p.Eval(`() => [document.body.classList.contains('expanded'), window.submitCount]`)
	require.NoError(t, err)
	require.True(t, state.Value.Get("0").Bool())
	require.Zero(t, state.Value.Get("1").Int())
}

func fixtureAuthor(mode string) string {
	if mode == "wrong-account" {
		return "other-account"
	}
	return "mine"
}

func TestFixtureBackgroundCommentAndReply(t *testing.T) {
	b := commentFixtureBrowser(t)
	humanize.SetProvider(fixtureCommentTiming{})
	defer humanize.SetProvider(humanize.DefaultProvider{})
	for _, reply := range []bool{false, true} {
		t.Run(fmt.Sprintf("reply=%t", reply), func(t *testing.T) {
			mode := "collapsed-overlay"
			if reply {
				mode = "ack"
			}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				fmt.Fprint(w, commentActionHTML(mode, reply))
			}))
			defer server.Close()
			t.Setenv("XHS_WEB_BASE_URL", server.URL)
			ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
			defer cancel()
			p, err := b.Context(ctx).Page(proto.TargetCreateTarget{})
			require.NoError(t, err)
			defer p.Context(context.Background()).Close()
			require.NoError(t, p.Navigate(makeFeedDetailURL("fixture", "dummy")))
			_, err = p.Element(".comments-container")
			require.NoError(t, err)
			other, err := b.Page(proto.TargetCreateTarget{})
			require.NoError(t, err)
			defer other.Close()
			_, err = other.Activate()
			require.NoError(t, err)
			visibility, err := p.Eval(`() => document.visibilityState`)
			require.NoError(t, err)
			require.Equal(t, "hidden", visibility.Value.Str())
			action := NewCommentFeedAction(p, "mine")
			if reply {
				err = action.ReplyToComment(ctx, "fixture", "dummy", "target", "other", "具体建议")
			} else {
				err = action.PostComment(ctx, "fixture", "dummy", "具体建议")
			}
			require.NoError(t, err)
			count, err := p.Eval(`() => window.submitCount`)
			require.NoError(t, err)
			require.Equal(t, 1, count.Value.Int())
		})
	}
}

func commentFixturePage(t *testing.T, b *rod.Browser, html string) *rod.Page {
	t.Helper()
	p := b.MustPage()
	require.NoError(t, p.SetDocumentContent("<html><body>"+html+"</body></html>"))
	t.Cleanup(func() { _ = p.Close() })
	return p
}

func TestFixtureCommentDOMRegression(t *testing.T) {
	b := commentFixtureBrowser(t)
	t.Run("missing ID must not select another comment by same user", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="comments-container"><div id="comment-other" class="comment-item"><a data-user-id="target"></a></div></div>`)
		require.Nil(t, lookupComment(p.Timeout(3*time.Second), "missing", "target"))
	})
	t.Run("user alone must identify exactly one comment", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="comments-container"><div class="comment-item" id="comment-a"><a data-user-id="target"></a></div><div class="comment-item" id="comment-b"><a data-user-id="target"></a></div></div>`)
		require.Nil(t, lookupComment(p.Timeout(3*time.Second), "", "target"))
	})
	t.Run("bulk DOM lookup does not wait per unrelated author", func(t *testing.T) {
		var html strings.Builder
		html.WriteString(`<div class="comments-container">`)
		for i := 0; i < 30; i++ {
			fmt.Fprintf(&html, `<div class="comment-item" id="comment-%d"><a data-user-id="unrelated-%d"></a></div>`, i, i)
		}
		html.WriteString(`<div class="comment-item" id="comment-target"><a data-user-id="target"></a></div></div>`)
		p := commentFixturePage(t, b, html.String())
		start := time.Now()
		require.NotNil(t, lookupComment(p.Timeout(6*time.Second), "", "target"))
		require.Less(t, time.Since(start), 2*time.Second)
	})
	t.Run("canceled search returns without scroll or panic", func(t *testing.T) {
		p := commentFixturePage(t, b, `<div class="comments-container"></div>`)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		start := time.Now()
		_, err := findCommentElement(ctx, p.Timeout(3*time.Second), "missing", "")
		require.ErrorIs(t, err, context.Canceled)
		require.Less(t, time.Since(start), 500*time.Millisecond)
	})
}

func TestFixturePausedPageNeverSubmitsCommentOrReply(t *testing.T) {
	b := commentFixtureBrowser(t)
	humanize.SetProvider(fixtureCommentTiming{})
	defer humanize.SetProvider(humanize.DefaultProvider{})
	for _, reply := range []bool{false, true} {
		t.Run(fmt.Sprintf("reply=%t", reply), func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				fmt.Fprint(w, commentActionHTML("ack", reply)+`<script>window.requestAnimationFrame=()=>0</script>`)
			}))
			defer server.Close()
			t.Setenv("XHS_WEB_BASE_URL", server.URL)
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			p, err := b.Context(ctx).Page(proto.TargetCreateTarget{})
			require.NoError(t, err)
			defer p.Context(context.Background()).Close()
			action := NewCommentFeedAction(p, "mine")
			start := time.Now()
			if reply {
				err = action.ReplyToComment(ctx, "fixture", "dummy", "target", "other", "not sent")
			} else {
				err = action.PostComment(ctx, "fixture", "dummy", "not sent")
			}
			var failure *CommentActionError
			require.ErrorAs(t, err, &failure)
			require.False(t, failure.Submitted)
			require.Contains(t, err.Error(), "ERR_BROWSER_INACTIVE")
			require.Less(t, time.Since(start), 4*time.Second)
			count, err := p.Eval(`() => window.submitCount`)
			require.NoError(t, err)
			require.Zero(t, count.Value.Int())
			draft, err := p.Eval(`() => document.querySelector('.content-input').innerText`)
			require.NoError(t, err)
			require.Empty(t, draft.Value.Str())
		})
	}
}
