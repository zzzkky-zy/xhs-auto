package main

import (
	"errors"
	"sync/atomic"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
)

func TestCaptchaCooldownFromEnv(t *testing.T) {
	t.Setenv("XHS_CAPTCHA_COOLDOWN", "")
	assert.Equal(t, defaultCaptchaCooldown, captchaCooldownFromEnv())

	t.Setenv("XHS_CAPTCHA_COOLDOWN", "0")
	assert.Equal(t, time.Duration(0), captchaCooldownFromEnv())

	t.Setenv("XHS_CAPTCHA_COOLDOWN", "10m")
	assert.Equal(t, 10*time.Minute, captchaCooldownFromEnv())

	t.Setenv("XHS_CAPTCHA_COOLDOWN", "20")
	assert.Equal(t, 20*time.Minute, captchaCooldownFromEnv())
}

func TestTripAndSkipWithoutBrowser(t *testing.T) {
	t.Setenv("XHS_CAPTCHA_COOLDOWN", "15m")

	var nowMs atomic.Int64
	base := time.Date(2026, 10, 14, 19, 0, 0, 0, time.Local)
	nowMs.Store(base.UnixMilli())

	s := &XiaohongshuService{}
	s.captcha.nowFn = func() time.Time {
		return time.UnixMilli(nowMs.Load())
	}

	// 未冷却
	require.NoError(t, s.errIfCaptchaCooldown("probe"))
	active, _ := s.captchaCooldownActive()
	assert.False(t, active)

	// 非 captcha 不 trip
	s.tripCaptchaCooldown(errors.New("other"))
	active, _ = s.captchaCooldownActive()
	assert.False(t, active)

	// trip ErrCaptcha
	s.tripCaptchaCooldown(xhserrors.ErrCaptcha)
	active, rem := s.captchaCooldownActive()
	require.True(t, active)
	assert.InDelta(t, float64(15*time.Minute), float64(rem), float64(time.Second))

	err := s.errIfCaptchaCooldown("login_probe_periodic")
	require.ErrorIs(t, err, xhserrors.ErrCaptcha)

	err = s.errIfCaptchaCooldown("search_feeds")
	require.ErrorIs(t, err, xhserrors.ErrCaptcha)

	err = s.errIfCaptchaCooldown("ensureLoggedIn")
	require.ErrorIs(t, err, xhserrors.ErrCaptcha)

	// 冷却未到期：ensureLoggedIn 不导航，直接 ErrCaptcha
	err = s.ensureLoggedIn(nil)
	require.ErrorIs(t, err, xhserrors.ErrCaptcha)

	// 时间推进到冷却结束后应放行
	nowMs.Store(base.Add(15*time.Minute + time.Second).UnixMilli())
	active, _ = s.captchaCooldownActive()
	assert.False(t, active)
	require.NoError(t, s.errIfCaptchaCooldown("probe"))
}

func TestClearCaptchaCooldownEarly(t *testing.T) {
	t.Setenv("XHS_CAPTCHA_COOLDOWN", "15m")

	var nowMs atomic.Int64
	base := time.Date(2026, 10, 14, 19, 0, 0, 0, time.Local)
	nowMs.Store(base.UnixMilli())

	s := &XiaohongshuService{}
	s.captcha.nowFn = func() time.Time {
		return time.UnixMilli(nowMs.Load())
	}

	s.tripCaptchaCooldown(xhserrors.ErrCaptcha)
	require.ErrorIs(t, s.errIfCaptchaCooldown("search_feeds"), xhserrors.ErrCaptcha)

	s.clearCaptchaCooldown()
	require.NoError(t, s.errIfCaptchaCooldown("search_feeds"))
	active, _ := s.captchaCooldownActive()
	assert.False(t, active)
}

func TestTripCaptchaCooldownDisabled(t *testing.T) {
	t.Setenv("XHS_CAPTCHA_COOLDOWN", "0")
	s := &XiaohongshuService{}
	s.tripCaptchaCooldown(xhserrors.ErrCaptcha)
	active, _ := s.captchaCooldownActive()
	assert.False(t, active, "cooldown duration 0 should not arm")
}

func TestTripCaptchaCooldownExtends(t *testing.T) {
	t.Setenv("XHS_CAPTCHA_COOLDOWN", "5m")

	var nowMs atomic.Int64
	base := time.Date(2026, 10, 14, 19, 0, 0, 0, time.Local)
	nowMs.Store(base.UnixMilli())

	s := &XiaohongshuService{}
	s.captcha.nowFn = func() time.Time {
		return time.UnixMilli(nowMs.Load())
	}

	s.tripCaptchaCooldown(xhserrors.ErrCaptcha)
	_, rem1 := s.captchaCooldownActive()

	// 2 分钟后再次 trip：应延长到 now+5m
	nowMs.Store(base.Add(2 * time.Minute).UnixMilli())
	s.tripCaptchaCooldown(xhserrors.ErrCaptcha)
	_, rem2 := s.captchaCooldownActive()
	assert.Greater(t, rem2, rem1-2*time.Minute-time.Second)
	assert.InDelta(t, float64(5*time.Minute), float64(rem2), float64(time.Second))
}

func TestRunLoginProbeOnceSkipsDuringCooldown(t *testing.T) {
	t.Setenv("XHS_CAPTCHA_COOLDOWN", "15m")

	s := &XiaohongshuService{}
	s.captcha.nowFn = func() time.Time {
		return time.Date(2026, 10, 14, 19, 0, 0, 0, time.Local)
	}
	s.tripCaptchaCooldown(xhserrors.ErrCaptcha)

	// 不应拿 browserMu / 开浏览器；冷却闸直接 return
	s.runLoginProbeOnce("periodic")
	active, _ := s.captchaCooldownActive()
	assert.True(t, active)
}

func TestDefaultCooldownConstant(t *testing.T) {
	assert.Equal(t, 15*time.Minute, defaultCaptchaCooldown)
}
