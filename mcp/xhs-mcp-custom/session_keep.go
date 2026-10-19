package main

import (
	"context"
	"errors"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/sirupsen/logrus"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

// 登录闸短缓存：避免每个业务都重新打开首页检查身份。
// 后台定时探针与 check_login_status 成功时会刷新。
// 已登录用较长 TTL；未登录用短负缓存，避免 periodic 假阴性堵发帖数分钟。
const defaultLoginCacheTTL = 3 * time.Minute
const negativeLoginCacheTTL = 20 * time.Second

// defaultLoginProbeInterval 启动后定时轻量探针间隔；0 或负 = 关闭。
// 可用环境变量 XHS_LOGIN_PROBE_INTERVAL 覆盖（Go duration，如 5m / 0）。
const defaultLoginProbeInterval = 0 * time.Minute

type loginGate struct {
	mu         sync.Mutex
	checked    time.Time
	ok         bool
	userID     string
	nickname   string
	generation uint64
}

func (s *XiaohongshuService) rememberLogin(ok bool) {
	s.login.mu.Lock()
	defer s.login.mu.Unlock()
	s.login.checked = time.Now()
	s.login.ok = ok
	s.login.generation = browser.Generation()
	if !ok {
		s.login.userID = ""
		s.login.nickname = ""
	}
}

// cachedLoginOK 读登录闸缓存。ok=true 用 defaultLoginCacheTTL；
// ok=false 用 negativeLoginCacheTTL（短负缓存）。
func (s *XiaohongshuService) cachedLoginOK() (hit bool, ok bool) {
	s.login.mu.Lock()
	defer s.login.mu.Unlock()
	if s.login.checked.IsZero() {
		return false, false
	}
	if s.login.generation != browser.Generation() {
		return false, false
	}
	ttl := defaultLoginCacheTTL
	if !s.login.ok {
		ttl = negativeLoginCacheTTL
	}
	if time.Since(s.login.checked) > ttl {
		return false, false
	}
	return true, s.login.ok
}

// ensureLoggedIn 业务入口登录闸。失效返回 ErrNotLoggedIn；验证码返回 ErrCaptcha（非掉线）。
// 验证码冷却中：直接返回 ErrCaptcha（不 soft-allow、不探针），让 Soft CTA / 写操作停风暴。
// 成功时把 CDP 浏览器 cookies 落盘，与 box-chrome 会话对齐（attach 从不反灌磁盘 cookies）。
func (s *XiaohongshuService) ensureLoggedIn(ctx context.Context) error {
	if err := s.errIfCaptchaCooldown("ensureLoggedIn"); err != nil {
		return err
	}
	// A fresh content read must not force another identity navigation on every
	// continuation. Explicit CheckLoginStatus(fresh=true) still refreshes it.
	status, err := s.CheckLoginStatus(context.WithValue(ctx, freshReadKey{}, false))
	if err != nil {
		// 验证码 ≠ 掉线：保留正缓存，但返回 ErrCaptcha（触发冷却，停止写/搜风暴）。
		if errors.Is(err, xhserrors.ErrCaptcha) {
			s.tripCaptchaCooldown(err)
			logrus.Warnf("ensureLoggedIn: 验证码墙，返回 ErrCaptcha（保留登录正缓存，进入冷却）")
			return xhserrors.ErrCaptcha
		}
		return err
	}
	if !status.IsLoggedIn {
		return xhserrors.ErrNotLoggedIn
	}
	s.clearCaptchaCooldown()
	return nil
}

// probeAndSyncLogin 只读首页身份；成功后更新缓存。
// 持 browserMu，避免与业务/并发探针抢同一 CDP 页态。
// 业务 / check_login 路径：失败会 rememberLogin(false)（短负缓存）。
func (s *XiaohongshuService) probeAndSyncLogin(ctx context.Context) (bool, error) {
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return false, err
	}
	defer s.browserMu.Unlock()
	if hit, ok := s.cachedLoginOK(); hit && ctx.Value(freshReadKey{}) != true {
		return ok, nil
	}
	return s.probeAndSyncLoginLocked(ctx, true)
}

// probeAndSyncLoginLocked 调用方须已持 browserMu。
// rememberNegative=false 时（定时探针）：ok=false / err 只打日志，不写负缓存，避免发帖并发假阴性。
func (s *XiaohongshuService) probeAndSyncLoginLocked(ctx context.Context, rememberNegative bool) (bool, error) {
	execution.Stage(ctx, "check_identity", 30*time.Second)
	if err := s.errIfRateLimited("identity"); err != nil {
		return false, err
	}
	// 冷却中禁止重新打开身份检查页面。
	if err := s.errIfCaptchaCooldown("login_probe"); err != nil {
		return false, err
	}

	if err := reserveReadSlot(s, ctx); err != nil {
		return false, err
	}

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	loginAction := xiaohongshu.NewLogin(page)
	ok, err := loginAction.CheckLoginStatus(ctx)
	if err != nil {
		if errors.Is(err, xhserrors.ErrRateLimited) {
			s.tripRateLimit("identity", err)
			return false, err
		}
		// captcha ≠ 掉线：不写负缓存，保留已有正缓存；触发冷却
		if errors.Is(err, xhserrors.ErrCaptcha) {
			s.tripCaptchaCooldown(err)
			logrus.Warnf("登录检查: 验证码墙，保留原登录缓存并进入冷却（不判定掉线）: %v", err)
			return false, err
		}
		if rememberNegative {
			if errors.Is(err, xhserrors.ErrNotLoggedIn) {
				s.rememberLogin(false)
			}
		}
		return false, err
	}

	if ok {
		s.clearCaptchaCooldown()
		user, err := loginAction.CurrentUser(ctx)
		if err != nil {
			return false, err
		}
		s.login.mu.Lock()
		s.login.userID = user.UserID
		s.login.nickname = user.Nickname
		s.login.mu.Unlock()
		s.rememberLogin(true)
		if err := saveCookies(page); err != nil {
			logrus.Warnf("登录有效但 cookies 落盘失败（会话仍以 box-chrome 为准）: %v", err)
		} else {
			logrus.Debugf("cookies 已从 CDP 浏览器同步到磁盘")
		}
		return true, nil
	}

	if rememberNegative {
		s.rememberLogin(false)
		logrus.Warn("登录探针: 未登录或已失效 — 请 get_login_qrcode / login_with_sms；勿关 box-chrome / 勿清 profile")
	} else {
		logrus.Warn("登录探针(periodic): 判定未登录，但不写入负缓存（避免假阴性堵发帖）；请用 check_login_status 确认")
	}
	return false, nil
}

// loginProbeIntervalFromEnv 解析 XHS_LOGIN_PROBE_INTERVAL；未设用默认；0 关闭。
func loginProbeIntervalFromEnv() time.Duration {
	raw := strings.TrimSpace(os.Getenv("XHS_LOGIN_PROBE_INTERVAL"))
	if raw == "" {
		return defaultLoginProbeInterval
	}
	if raw == "0" {
		return 0
	}
	if d, err := time.ParseDuration(raw); err == nil {
		return d
	}
	// 纯数字按分钟
	if n, err := strconv.Atoi(raw); err == nil {
		if n <= 0 {
			return 0
		}
		return time.Duration(n) * time.Minute
	}
	logrus.Warnf("invalid XHS_LOGIN_PROBE_INTERVAL=%q, use default %s", raw, defaultLoginProbeInterval)
	return defaultLoginProbeInterval
}

// StartLoginProbe 启动或定时轻量登录探针；掉线写 Warn 日志。interval<=0 则跳过。
func (s *XiaohongshuService) StartLoginProbe(interval time.Duration) {
	if interval <= 0 {
		logrus.Info("登录定时探针已关闭（XHS_LOGIN_PROBE_INTERVAL=0）")
		return
	}
	logrus.Infof("登录定时探针已启动，间隔 %s（会话源: box-chrome CDP）", interval)

	go func() {
		// 启动后错开首批 MCP 请求；若缓存已暖则跳过 startup 探针
		time.Sleep(45 * time.Second)
		if hit, ok := s.cachedLoginOK(); hit && ok {
			logrus.Info("登录探针(startup): 跳过（缓存已显示已登录）")
		} else {
			s.runLoginProbeOnce("startup")
		}

		t := time.NewTicker(interval)
		defer t.Stop()
		for range t.C {
			s.runLoginProbeOnce("periodic")
		}
	}()
}

// runLoginProbeOnce 定时/启动探针。拿不到 browserMu（发帖/业务占用）则跳过本轮，不记未登录。
// periodic 失败不写负缓存；startup 与业务路径一致可写（短 TTL）。
func (s *XiaohongshuService) runLoginProbeOnce(reason string) {
	if s.errIfRateLimited("identity") != nil {
		return
	}
	// 冷却中：跳过定时/启动探针（errIfCaptchaCooldown 已 once-ish 打日志）
	if err := s.errIfCaptchaCooldown("login_probe_" + reason); err != nil {
		return
	}
	if !s.browserMu.TryLock() {
		logrus.Infof("登录探针(%s): 发帖/业务占用，跳过探针", reason)
		return
	}
	defer s.browserMu.Unlock()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Second)
	defer cancel()

	rememberNeg := reason != "periodic"
	ok, err := s.probeAndSyncLoginLocked(ctx, rememberNeg)
	if err != nil {
		if errors.Is(err, xhserrors.ErrCaptcha) {
			s.tripCaptchaCooldown(err) // 幂等延长；冷却闸已在 locked 内 trip
			logrus.Warnf("登录探针(%s): 验证码墙 ERR_CAPTCHA（不记掉线，冷却中）: %v", reason, err)
			return
		}
		logrus.Warnf("登录探针(%s)出错: %v", reason, err)
		return
	}
	if ok {
		logrus.Infof("登录探针(%s): 仍已登录；cookies 已尝试同步", reason)
	} else {
		logrus.Warnf("登录探针(%s): 掉线/未登录 — ERR_NOT_LOGGED_IN，请重登；勿关 box-chrome", reason)
	}
}
