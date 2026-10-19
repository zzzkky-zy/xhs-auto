//go:build fixture

package main

import (
	"context"
	"fmt"
	"github.com/xpzouying/xiaohongshu-mcp/internal/fixturebrowser"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/go-rod/rod/lib/launcher"
	"github.com/stretchr/testify/require"
)

// 真服务方法、CDP浏览器、落盘和重启重放；网站全是本机夹具，无真实发送。
func TestFixtureCommentServiceRestartDoesNotSubmitTwice(t *testing.T) {
	bin := fixturebrowser.Require(t)
	l := launcher.New().Bin(bin).Headless(true).UserDataDir(t.TempDir()).Leakless(false)
	control, err := l.Launch()
	require.NoError(t, err)
	defer l.Kill()
	var submitted, visits atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/submitted" {
			submitted.Add(1)
			w.WriteHeader(204)
			return
		}
		if r.URL.Path != "/explore/fixture" {
			w.WriteHeader(204)
			return
		}
		visits.Add(1)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, `<!DOCTYPE html><html><body><style>p{width:600px;min-height:40px;border:1px solid}button{padding:15px}</style>
<div class="comments-container"></div><div class="input-box"><div class="content-edit"><p contenteditable="true" class="content-input"></p></div></div><div class="bottom"><button class="submit">发送模拟评论</button></div>
<script>window.__INITIAL_STATE__={user:{userInfo:{value:{userId:'fixture-user',guest:false}}}};document.querySelector('.submit').onclick=()=>{fetch('/submitted');const text=document.querySelector('.content-input').innerText;document.querySelector('.comments-container').innerHTML='<div class="comment-item" id="comment-fixture"><a data-user-id="fixture-user">模拟账号</a><div class="content"></div></div>';document.querySelector('.content').textContent=text;document.querySelector('.content-input').innerText='';};</script></body></html>`)
	}))
	defer server.Close()
	t.Setenv("XHS_CDP_URL", control)
	t.Setenv("XHS_WEB_BASE_URL", server.URL)
	// The fixture's verified identity belongs to this attached connection, just
	// as a real identity probe records the current browser generation.
	b := newBrowser()
	defer b.Close()
	s := testRuntime(t)
	ctx, cancel := context.WithTimeout(withOperationID(context.Background(), "fixture-comment"), 30*time.Second)
	defer cancel()
	result, err := s.PostCommentToFeed(ctx, "fixture", "not-a-login-token", "具体建议")
	require.NoError(t, err)
	require.True(t, result.Success)
	require.Equal(t, int32(1), submitted.Load())
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	result, err = restarted.PostCommentToFeed(ctx, "fixture", "rotated-not-a-login-token", "具体建议")
	require.NoError(t, err)
	require.True(t, result.Success)
	require.Equal(t, int32(1), visits.Load())
	require.Equal(t, int32(1), submitted.Load())
}
