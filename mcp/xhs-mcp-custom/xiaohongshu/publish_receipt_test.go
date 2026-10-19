package xiaohongshu

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExplicitPublishSuccessURL(t *testing.T) {
	for _, c := range []struct {
		url string
		ok  bool
	}{
		{"https://creator.xiaohongshu.com/publish/success?note=fixture", true},
		{"https://creator.xiaohongshu.com/new/home", false},
		{"https://creator.xiaohongshu.com/login", false},
		{"https://creator.xiaohongshu.com/website-login/captcha", false},
		{"https://unrelated.example/publish/success", false},
		{"https://creator.xiaohongshu.com/publish/successful", false},
		{"about:blank", false},
	} {
		t.Run(c.url, func(t *testing.T) { require.Equal(t, c.ok, isExplicitPublishSuccessURL(c.url)) })
	}
}
