package configs

import (
	"os"
	"strconv"
	"strings"

	"github.com/sirupsen/logrus"
)

var (
	useHeadless = true

	fingerprintSeed = 0

	proxy = ""
)

func InitHeadless(h bool) {
	useHeadless = h
}

// IsHeadless 是否无头模式。
func IsHeadless() bool {
	return useHeadless
}

func SetFingerprintSeed(s int) {
	fingerprintSeed = s
}

func FingerprintSeed() int {
	return fingerprintSeed
}

// FingerprintSeedFromEnv 从 XHS_FP_SEED 环境变量解析固定 seed。
// 未设或非法返回 0（回退随机）。env 读取集中在配置层，浏览器工厂只收 Option。
func FingerprintSeedFromEnv() int {
	s := os.Getenv("XHS_FP_SEED")
	if s == "" {
		return 0
	}
	seed, err := strconv.Atoi(s)
	if err != nil || seed <= 0 {
		logrus.Warnf("invalid XHS_FP_SEED=%q, ignored (fallback to random seed)", s)
		return 0
	}
	return seed
}

func SetProxy(p string) {
	proxy = p
}

func Proxy() string {
	return proxy
}

// ProxyFromEnv 从 XHS_PROXY 环境变量读取代理地址。未设则直连。
// 若 XHS_PROXY_FALLBACK_DIRECT=1 且代理非空，仍返回代理（由调用方决定失败回退）；
// 本函数本身只负责「设了才走代理」。
func ProxyFromEnv() string {
	p := strings.TrimSpace(os.Getenv("XHS_PROXY"))
	if p == "" {
		return ""
	}
	return p
}

// ProxyFallbackDirect 失败时是否允许回退直连（配置开关，供上层策略读取）。
func ProxyFallbackDirect() bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv("XHS_PROXY_FALLBACK_DIRECT")))
	return v == "1" || v == "true" || v == "yes"
}

// CDPURLFromEnv 解析要 attach 的 Chrome DevTools 地址。
// 优先 XHS_CDP_URL（如 http://127.0.0.1:9224）；否则 XHS_CDP_PORT（如 9224 → http://127.0.0.1:9224）。
// 都未设则返回空（应走 LaunchBrowser 兜底或直接拒绝自启）。
func CDPURLFromEnv() string {
	if u := strings.TrimSpace(os.Getenv("XHS_CDP_URL")); u != "" {
		return u
	}
	port := strings.TrimSpace(os.Getenv("XHS_CDP_PORT"))
	if port == "" {
		return ""
	}
	// 允许纯端口或已带 host
	if strings.Contains(port, "://") {
		return port
	}
	if strings.Contains(port, ".") || strings.Contains(port, ":") {
		// host:port without scheme
		if !strings.HasPrefix(port, "http://") && !strings.HasPrefix(port, "ws://") {
			return "http://" + port
		}
		return port
	}
	return "http://127.0.0.1:" + port
}

// LaunchBrowserAllowed 是否允许 rod 自启独立 Chromium。
// 默认 false；仅 XHS_LAUNCH_BROWSER=1/true/yes 时开启兜底。
func LaunchBrowserAllowed() bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv("XHS_LAUNCH_BROWSER")))
	return v == "1" || v == "true" || v == "yes"
}
