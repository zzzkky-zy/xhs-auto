package browser

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/cdp"
	"github.com/go-rod/rod/lib/proto"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/headless_browser"
	"github.com/xpzouying/xiaohongshu-mcp/configs"
	"github.com/xpzouying/xiaohongshu-mcp/cookies"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
)

type browserConfig struct {
	// fingerprintSeed 固定指纹 seed；>0 时钉死，同账号每次同一套指纹。0 = 每次随机。
	// 仅自启浏览器（XHS_LAUNCH_BROWSER=1）时生效；attach CDP 时忽略。
	fingerprintSeed int
	// proxy 代理地址；非空时启用。仅自启时生效。
	proxy string
}

type Option func(*browserConfig)

// WithProxy 设置代理（http/https/socks5）。空字符串视为不启用。
func WithProxy(proxy string) Option {
	return func(c *browserConfig) {
		c.proxy = proxy
	}
}

// WithFingerprintSeed 设置 seed，seed<=0 视为未设，回退每次随机。
func WithFingerprintSeed(seed int) Option {
	return func(c *browserConfig) {
		c.fingerprintSeed = seed
	}
}

// maskProxyCredentials masks username and password in proxy URL for safe logging.
func maskProxyCredentials(proxyURL string) string {
	u, err := url.Parse(proxyURL)
	if err != nil || u.User == nil {
		return proxyURL
	}
	cred := "***"
	if _, hasPassword := u.User.Password(); hasPassword {
		cred = "***:***"
	}
	// 直接在原串替换 userinfo，避免 url.String() 把 * 编码成 %2A（日志变乱码）。
	return strings.Replace(proxyURL, u.User.String()+"@", cred+"@", 1)
}

// Browser 附着宿主已有会话；Close 不退出宿主的 Chrome。
type Browser struct {
	rod      *rod.Browser
	hb       *headless_browser.Browser // 自启路径
	attached bool
}

// UnavailableError 表示宿主浏览器不可连接，不是账号掉线或提交成功。
type UnavailableError struct{ Cause error }

func (e *UnavailableError) Error() string {
	return "ERR_BROWSER_UNAVAILABLE: 无法连接配置的宿主浏览器；保存进度，检查 CDP 服务与地址后恢复。保留原 profile，不自动另开浏览器或重新登录"
}

func (e *UnavailableError) Unwrap() error { return e.Cause }

var (
	attachMu     sync.Mutex
	sharedAttach *Browser
	sharedCDP    string
)
var connectionGeneration atomic.Uint64

func Generation() uint64 { return connectionGeneration.Load() }

// NewBrowser 创建浏览器会话。
// 优先级：XHS_CDP_URL / XHS_CDP_PORT → attach；否则仅当 XHS_LAUNCH_BROWSER=1 才 MustLaunch 内置 Chromium。
func NewBrowser(headless bool, options ...Option) *Browser {
	cfg := &browserConfig{}
	for _, opt := range options {
		opt(cfg)
	}

	if cdp := configs.CDPURLFromEnv(); cdp != "" {
		b, err := attachShared(cdp)
		if err != nil {
			panic(&UnavailableError{Cause: err})
		}
		return b
	}

	if !configs.LaunchBrowserAllowed() {
		panic(&UnavailableError{Cause: fmt.Errorf("CDP address not configured")})
	}

	return launchOwn(headless, cfg)
}

func attachShared(cdpURL string) (*Browser, error) {
	attachMu.Lock()
	defer attachMu.Unlock()

	if sharedAttach != nil && sharedCDP == cdpURL {
		if err := pingBrowser(sharedAttach.rod); err == nil {
			return sharedAttach, nil
		} else {
			logrus.Warnf("既有 CDP 连接失效，重新 attach: %v", err)
			sharedAttach = nil
		}
	}

	wsURL, err := resolveCDPURL(cdpURL)
	if err != nil {
		return nil, fmt.Errorf("ResolveURL(%s): %w", cdpURL, err)
	}

	logrus.Infof("attaching to existing Chrome CDP: %s → %s", cdpURL, wsURL)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	client, err := cdp.StartWithURL(ctx, wsURL, nil)
	if err != nil {
		return nil, fmt.Errorf("Connect: %w", err)
	}
	// 握手可超时；长期事件连接不能继承一个随后取消的握手上下文。
	bounded := &boundedCDPClient{Client: client}
	rodBrowser := rod.New().NoDefaultDevice().Client(bounded)
	if err := rodBrowser.Connect(); err != nil {
		return nil, fmt.Errorf("Connect: %w", err)
	}

	sharedAttach = &Browser{rod: rodBrowser, attached: true}
	connectionGeneration.Add(1)
	sharedCDP = cdpURL
	logrus.Infof("CDP attach OK（会话以浏览器为准；Close 不会退出 Chrome）")
	return sharedAttach, nil
}

func pingBrowser(b *rod.Browser) error {
	if b == nil {
		return fmt.Errorf("nil browser")
	}
	_, err := (proto.BrowserGetVersion{}).Call(b.Timeout(2 * time.Second))
	return err
}

func launchOwn(headless bool, cfg *browserConfig) *Browser {
	logrus.Warn("XHS_LAUNCH_BROWSER=1：显式配置为自启浏览器；不继承宿主既有会话")

	binPath, err := EnsureBrowser()
	if err != nil {
		panic(fmt.Sprintf("内置浏览器不可用，拒绝启动: %v", err))
	}

	opts := []headless_browser.Option{
		headless_browser.WithHeadless(headless),
		headless_browser.WithFingerprint(""),
		headless_browser.WithStealthJS(false),
		headless_browser.WithLanguage("zh-CN"),
		headless_browser.WithExtraFlags(map[string]string{"fingerprint-brand": "Chrome"}),
		headless_browser.WithChromeBinPath(binPath),
	}

	if cfg.proxy != "" {
		opts = append(opts, headless_browser.WithProxy(cfg.proxy))
		logrus.Infof("Using proxy: %s", maskProxyCredentials(cfg.proxy))
	}

	if cfg.fingerprintSeed > 0 {
		opts = append(opts, headless_browser.WithFingerprintSeed(cfg.fingerprintSeed))
		logrus.Infof("fingerprint seed pinned: %d", cfg.fingerprintSeed)
	}

	cookiePath := cookies.GetCookiesFilePath()
	cookieLoader := cookies.NewLoadCookie(cookiePath)
	if data, err := cookieLoader.LoadCookies(); err == nil {
		opts = append(opts, headless_browser.WithCookies(string(data)))
		logrus.Debugf("loaded cookies from file successfully")
	} else {
		logrus.Warnf("failed to load cookies: %v", err)
	}

	hb := headless_browser.New(opts...)
	return &Browser{hb: hb, attached: false}
}

// NewPage 在所连接的宿主浏览器打开新标签。
func (b *Browser) NewPage() *rod.Page {
	return b.NewPageContext(context.Background())
}

// NewPageContext 将输入设备与页面都绑定到任务；不能创建后取消再只替换页面 ctx。
func (b *Browser) NewPageContext(ctx context.Context) *rod.Page {
	if b.hb != nil {
		return b.hb.NewPage().Context(ctx)
	}
	page, err := b.rod.Context(ctx).Page(proto.TargetCreateTarget{})
	if err != nil {
		panic(fmt.Errorf("ERR_CDP_PAGE: %w", err))
	}
	return page
}

// 每个 CDP 请求有限时，事件订阅和浏览器会话保持独立生命周期。
type boundedCDPClient struct {
	*cdp.Client
}

func (c *boundedCDPClient) Call(ctx context.Context, sessionID, method string, params interface{}) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if method == "Page.navigate" {
		raw, _ := json.Marshal(params)
		var target struct {
			URL string `json:"url"`
		}
		if json.Unmarshal(raw, &target) == nil {
			if category := navigationCategory(target.URL); category != "" {
				// Counts attempted page navigations, never URLs, tokens or HTTP
				// requests. This cannot be mistaken for a platform request limit.
				execution.Count("site_navigation_attempts")
				execution.Count("site_navigation_" + category)
			}
		}
	}
	return c.Client.Call(ctx, sessionID, method, params)
}

func navigationCategory(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() != "www.xiaohongshu.com" && u.Hostname() != "creator.xiaohongshu.com" {
		return ""
	}
	switch {
	case strings.HasPrefix(u.Path, "/search_result"):
		return "search"
	case strings.HasPrefix(u.Path, "/user/profile/"):
		return "profile"
	case strings.HasPrefix(u.Path, "/explore/") || strings.HasPrefix(u.Path, "/discovery/item/"):
		return "detail"
	case u.Hostname() == "creator.xiaohongshu.com":
		return "creator"
	default:
		return "main"
	}
}

// Close attach 模式：不断开也不退出 Chrome（进程级单例连接保留）。
// 自启模式：关闭自启的 Chromium。
func (b *Browser) Close() {
	if b.attached {
		return
	}
	if b.hb != nil {
		b.hb.Close()
	}
}

// Attached 是否连接宿主已有 CDP。
func (b *Browser) Attached() bool {
	return b.attached
}

// Rod 返回底层 rod.Browser（attach 路径）；自启路径可能为 nil。
func (b *Browser) Rod() *rod.Browser {
	return b.rod
}

// resolveCDPURL 设置连接超时，不允许失败时另起浏览器。
func resolveCDPURL(raw string) (string, error) {
	u, err := url.Parse(raw)
	if err != nil || u.User != nil || u.Host == "" {
		return "", fmt.Errorf("invalid CDP URL")
	}
	if u.Scheme == "ws" || u.Scheme == "wss" {
		return raw, nil
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return "", fmt.Errorf("unsupported CDP scheme")
	}
	client := &http.Client{Timeout: 5 * time.Second}
	response, err := client.Get(strings.TrimRight(raw, "/") + "/json/version")
	if err != nil {
		return "", err
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return "", fmt.Errorf("CDP status %d", response.StatusCode)
	}
	var value struct {
		URL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.NewDecoder(response.Body).Decode(&value); err != nil {
		return "", err
	}
	if value.URL == "" {
		return "", fmt.Errorf("missing websocket URL")
	}
	return value.URL, nil
}
