//go:build fixture

package xiaohongshu

import (
	"context"
	"fmt"
	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/stretchr/testify/require"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
	"github.com/xpzouying/xiaohongshu-mcp/internal/fixturebrowser"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// 只访问本机 HTML 夹具，不访问小红书，不登录、不发送。
func TestFixtureLoginProbeIdentityAndCaptcha(t *testing.T) {
	bin := fixturebrowser.Require(t)
	l := launcher.New().Bin(bin).Headless(true).UserDataDir(t.TempDir()).Leakless(false)
	control, err := l.Launch()
	require.NoError(t, err)
	defer l.Kill()
	b := rod.New().ControlURL(control)
	require.NoError(t, b.Connect())
	defer b.Close()
	cases := []struct {
		name, html string
		ok         bool
		captcha    bool
		rate       bool
	}{
		{"actual user", `<script>window.__INITIAL_STATE__={user:{userInfo:{value:{userId:'fixture',nickname:'Test',guest:false}}}}</script>`, true, false, false},
		{"captcha", `<h1>Security Verification</h1>`, false, true, false},
		{"guest search results not identity", `<script>window.__INITIAL_STATE__={search:{feeds:{value:[1]}},user:{userInfo:{guest:true}}}</script>`, false, false, false},
	}
	cases = append(cases, struct {
		name, html        string
		ok, captcha, rate bool
	}{"rate limit page", `<title>安全限制</title><h1>300013</h1><p>Too many requests. Try again later.</p>`, false, false, true})
	cases = append(cases,
		struct {
			name, html        string
			ok, captcha, rate bool
		}{"rate limit URL", `<p>Too many requests</p>`, false, false, true},
		struct {
			name, html        string
			ok, captcha, rate bool
		}{"post mentions rate limit is not a wall", `<script>window.__INITIAL_STATE__={user:{userInfo:{userId:'fixture',guest:false}}}</script><p>笔记讨论300013 / Too many requests</p>`, true, false, false},
	)
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			searchCount := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if c.name == "rate limit URL" && r.URL.Path == "/explore" {
					http.Redirect(w, r, "/website-login/error?error_code=300013&error_msg=Too%20many%20requests", http.StatusFound)
					return
				}
				if r.URL.Path == "/search_result" {
					searchCount++
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				fmt.Fprint(w, "<html><body>"+c.html+"</body></html>")
			}))
			defer server.Close()
			t.Setenv("XHS_WEB_BASE_URL", server.URL)
			page := b.MustPage()
			defer page.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			ok, err := NewLogin(page).CheckLoginStatus(ctx)
			if c.ok {
				require.NoError(t, err)
				require.True(t, ok)
			} else if c.rate {
				require.ErrorIs(t, err, xhserrors.ErrRateLimited)
				require.NotErrorIs(t, err, xhserrors.ErrNotLoggedIn)
			} else if c.captcha {
				require.ErrorIs(t, err, xhserrors.ErrCaptcha)
			} else {
				require.Error(t, err)
				require.False(t, ok)
			}
			require.Zero(t, searchCount)
		})
	}
}
