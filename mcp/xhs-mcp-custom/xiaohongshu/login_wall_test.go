package xiaohongshu

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
)

func TestClassifyHrefForWall_CaptchaBeforeLoginPath(t *testing.T) {
	cases := []struct {
		name string
		href string
		want string
	}{
		{
			name: "website-login/captcha with redirectPath",
			href: "https://www.xiaohongshu.com/website-login/captcha?redirectPath=/search_result/?keyword=%E9%9D%A2%E8%AF%95",
			want: captchaWallReason,
		},
		{
			name: "captcha substring anywhere",
			href: "https://www.xiaohongshu.com/foo/captcha/bar",
			want: captchaWallReason,
		},
		{
			name: "real /login path",
			href: "https://www.xiaohongshu.com/login",
			want: "login_path",
		},
		{
			name: "passport",
			href: "https://www.xiaohongshu.com/passport/xxx",
			want: "login_path",
		},
		{
			name: "normal search_result",
			href: "https://www.xiaohongshu.com/search_result?keyword=%E9%9D%A2%E8%AF%95",
			want: "",
		},
		{
			name: "explore",
			href: "https://www.xiaohongshu.com/explore",
			want: "",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := classifyHrefForWall(tc.href)
			require.Equal(t, tc.want, got)
			// regression: captcha URL must NOT be login_path
			if strings.Contains(strings.ToLower(tc.href), "captcha") {
				require.Equal(t, captchaWallReason, got)
				require.NotEqual(t, "login_path", got)
			}
		})
	}
}

func TestClassifyBodyForCaptcha(t *testing.T) {
	require.True(t, classifyBodyForCaptcha("Please complete Security Verification"))
	require.True(t, classifyBodyForCaptcha("请完成安全验证后继续"))
	require.True(t, classifyBodyForCaptcha("滑动验证"))
	require.False(t, classifyBodyForCaptcha("登录后搜索更多内容"))
	require.False(t, classifyBodyForCaptcha(""))
}

func TestErrFromLoginWall(t *testing.T) {
	require.Equal(t, rateLimitWallReason, classifyHrefForWall("https://www.xiaohongshu.com/website-login/error?error_code=300013&error_msg=Too%20many%20requests"))
	require.Equal(t, rateLimitWallReason, classifyHrefForWall("https://www.xiaohongshu.com/website-login/error?error_msg=Too%20many%20requests"))
	require.ErrorIs(t, errFromLoginWall(rateLimitWallReason), xhserrors.ErrRateLimited)
	require.NotErrorIs(t, errFromLoginWall(rateLimitWallReason), xhserrors.ErrNotLoggedIn)
	require.NotErrorIs(t, errFromLoginWall(rateLimitWallReason), xhserrors.ErrCaptcha)
	require.ErrorIs(t, errFromLoginWall("captcha"), xhserrors.ErrCaptcha)
	require.ErrorIs(t, errFromLoginWall(captchaWallReason), xhserrors.ErrCaptcha)
	require.ErrorIs(t, errFromLoginWall("login_path"), xhserrors.ErrNotLoggedIn)
	require.ErrorIs(t, errFromLoginWall("login_modal"), xhserrors.ErrNotLoggedIn)
	require.ErrorIs(t, errFromLoginWall("search_cta_wall"), xhserrors.ErrNotLoggedIn)
	require.NotErrorIs(t, errFromLoginWall("captcha"), xhserrors.ErrNotLoggedIn)
	require.Contains(t, errFromLoginWall("captcha").Error(), "ERR_CAPTCHA")
	require.Contains(t, errFromLoginWall("login_path").Error(), "ERR_NOT_LOGGED_IN")
}

func TestIsCaptchaReason(t *testing.T) {
	require.True(t, isCaptchaReason("captcha"))
	require.False(t, isCaptchaReason("login_path"))
	require.False(t, isCaptchaReason(""))
}
