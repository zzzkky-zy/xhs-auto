package main

import (
	"errors"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/sirupsen/logrus"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
)

// defaultCaptchaCooldown：撞 ERR_CAPTCHA 后的进程级冷却，避免定时探针 / check_login /
// search_feeds 反复导航到 search_result 再触发扫码墙。可用 XHS_CAPTCHA_COOLDOWN 覆盖。
const defaultCaptchaCooldown = 15 * time.Minute

// captchaSkipLogMinGap：冷却跳过日志的最小间隔（once-ish）。
const captchaSkipLogMinGap = 1 * time.Minute

// captchaCooldown 进程级验证码冷却闸（挂在 XiaohongshuService 上）。
type captchaCooldown struct {
	mu      sync.Mutex
	until   time.Time
	skipLog time.Time // 上次「跳过」日志时间
	nowFn   func() time.Time
}

func (c *captchaCooldown) now() time.Time {
	if c != nil && c.nowFn != nil {
		return c.nowFn()
	}
	return time.Now()
}

// captchaCooldownFromEnv 解析 XHS_CAPTCHA_COOLDOWN（Go duration，如 15m / 0）；未设用默认。
// 纯数字按分钟；0 表示关闭冷却（不推荐，仅调试）。
func captchaCooldownFromEnv() time.Duration {
	raw := strings.TrimSpace(os.Getenv("XHS_CAPTCHA_COOLDOWN"))
	if raw == "" {
		return defaultCaptchaCooldown
	}
	if raw == "0" {
		return 0
	}
	if d, err := time.ParseDuration(raw); err == nil {
		if d < 0 {
			return 0
		}
		return d
	}
	if n, err := strconv.Atoi(raw); err == nil {
		if n <= 0 {
			return 0
		}
		return time.Duration(n) * time.Minute
	}
	logrus.Warnf("invalid XHS_CAPTCHA_COOLDOWN=%q, use default %s", raw, defaultCaptchaCooldown)
	return defaultCaptchaCooldown
}

// tripCaptchaCooldown 在任意路径映射到 ErrCaptcha 时调用：延长冷却至 now+duration。
// 非 ErrCaptcha / nil 忽略。duration=0（env 关冷却）时不设置。
func (s *XiaohongshuService) tripCaptchaCooldown(err error) {
	if err == nil || !errors.Is(err, xhserrors.ErrCaptcha) {
		return
	}
	d := captchaCooldownFromEnv()
	if d <= 0 {
		return
	}
	now := s.captcha.now()
	until := now.Add(d)
	s.captcha.mu.Lock()
	if until.After(s.captcha.until) {
		s.captcha.until = until
	}
	persisted := s.captcha.until
	s.captcha.mu.Unlock()
	s.persistCaptcha(persisted)
	logrus.Warnf("captcha cooldown: tripped until %s (duration %s)", until.Format(time.RFC3339), d)
}

// clearCaptchaCooldown 成功非 captcha 的 check_login / search 后提前解除冷却。
func (s *XiaohongshuService) clearCaptchaCooldown() {
	s.captcha.mu.Lock()
	if s.captcha.until.IsZero() {
		s.captcha.mu.Unlock()
		return
	}
	if s.captcha.now().Before(s.captcha.until) {
		logrus.Infof("captcha cooldown: cleared early (was until %s)", s.captcha.until.Format(time.RFC3339))
	}
	s.captcha.until = time.Time{}
	s.captcha.mu.Unlock()
	s.persistCaptcha(time.Time{})
}

// captchaCooldownActive 是否仍在冷却；remaining 为剩余时间。
func (s *XiaohongshuService) captchaCooldownActive() (active bool, remaining time.Duration) {
	s.captcha.mu.Lock()
	defer s.captcha.mu.Unlock()
	if s.captcha.until.IsZero() {
		return false, 0
	}
	now := s.captcha.now()
	if !now.Before(s.captcha.until) {
		s.captcha.until = time.Time{}
		return false, 0
	}
	return true, s.captcha.until.Sub(now)
}

// errIfCaptchaCooldown 冷却中返回 ErrCaptcha（不导航）；op 用于日志。
// 跳过日志限频（captchaSkipLogMinGap），避免 periodic 每轮刷屏。
func (s *XiaohongshuService) errIfCaptchaCooldown(op string) error {
	if s.state != nil {
		s.state.mu.Lock()
		err := s.stateErr
		s.state.mu.Unlock()
		if err != nil {
			return err
		}
	}
	active, rem := s.captchaCooldownActive()
	if !active {
		return nil
	}
	s.captcha.mu.Lock()
	shouldLog := s.captcha.skipLog.IsZero() || s.captcha.now().Sub(s.captcha.skipLog) >= captchaSkipLogMinGap
	if shouldLog {
		s.captcha.skipLog = s.captcha.now()
	}
	s.captcha.mu.Unlock()
	if shouldLog {
		logrus.Warnf("captcha cooldown: skip %s (remaining %s); 请在 box-chrome 完成扫码验证或等待冷却", op, rem.Round(time.Second))
	}
	return xhserrors.ErrCaptcha
}
