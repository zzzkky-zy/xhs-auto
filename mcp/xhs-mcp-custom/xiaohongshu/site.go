package xiaohongshu

import (
	"os"
	"strings"
)

// 身份探针与详情页可指向本地验收夹具；默认使用平台网页。
func webBaseURL() string {
	if s := strings.TrimSpace(os.Getenv("XHS_WEB_BASE_URL")); s != "" {
		return strings.TrimRight(s, "/")
	}
	return "https://www.xiaohongshu.com"
}
