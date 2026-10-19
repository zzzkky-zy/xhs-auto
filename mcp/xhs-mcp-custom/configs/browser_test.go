package configs

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

// TestFingerprintSeedFromEnv 校验 XHS_FP_SEED 解析：非法/非正数一律回退 0（随机）。
func TestFingerprintSeedFromEnv(t *testing.T) {
	tests := []struct {
		name string
		env  string
		want int
	}{
		{name: "未设返回0", env: "", want: 0},
		{name: "合法seed", env: "98759", want: 98759},
		{name: "非数字回退0", env: "abc", want: 0},
		{name: "零回退0", env: "0", want: 0},
		{name: "负数回退0", env: "-5", want: 0},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("XHS_FP_SEED", tt.env)
			assert.Equal(t, tt.want, FingerprintSeedFromEnv())
		})
	}
}

func TestProxyFromEnv(t *testing.T) {
	t.Run("未设为空", func(t *testing.T) {
		t.Setenv("XHS_PROXY", "")
		assert.Equal(t, "", ProxyFromEnv())
	})
	t.Run("读取代理地址", func(t *testing.T) {
		t.Setenv("XHS_PROXY", "socks5://127.0.0.1:1080")
		assert.Equal(t, "socks5://127.0.0.1:1080", ProxyFromEnv())
	})
}

func TestCDPURLFromEnv(t *testing.T) {
	t.Run("URL优先", func(t *testing.T) {
		t.Setenv("XHS_CDP_URL", "http://127.0.0.1:9224")
		t.Setenv("XHS_CDP_PORT", "9999")
		assert.Equal(t, "http://127.0.0.1:9224", CDPURLFromEnv())
	})
	t.Run("仅端口", func(t *testing.T) {
		t.Setenv("XHS_CDP_URL", "")
		t.Setenv("XHS_CDP_PORT", "9224")
		assert.Equal(t, "http://127.0.0.1:9224", CDPURLFromEnv())
	})
	t.Run("未设为空", func(t *testing.T) {
		t.Setenv("XHS_CDP_URL", "")
		t.Setenv("XHS_CDP_PORT", "")
		assert.Equal(t, "", CDPURLFromEnv())
	})
}

func TestLaunchBrowserAllowed(t *testing.T) {
	t.Setenv("XHS_LAUNCH_BROWSER", "")
	assert.False(t, LaunchBrowserAllowed())
	t.Setenv("XHS_LAUNCH_BROWSER", "1")
	assert.True(t, LaunchBrowserAllowed())
	t.Setenv("XHS_LAUNCH_BROWSER", "true")
	assert.True(t, LaunchBrowserAllowed())
}
