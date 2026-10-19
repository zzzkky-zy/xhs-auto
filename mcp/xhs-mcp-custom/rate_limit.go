package main

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
)

// This is a local quiet period, not a claim about the platform's unban time.
const defaultPlatformRateLimitCooldown = time.Hour

type platformRateLimits struct {
	mu                         sync.Mutex
	searchUntil, identityUntil time.Time
	nowFn                      func() time.Time
}

func (r *platformRateLimits) now() time.Time {
	if r.nowFn != nil {
		return r.nowFn()
	}
	return time.Now()
}

func platformRateLimitCooldown() time.Duration {
	if d, err := time.ParseDuration(strings.TrimSpace(os.Getenv("XHS_PLATFORM_RATE_LIMIT_COOLDOWN"))); err == nil && d >= time.Minute && d <= 24*time.Hour {
		return d
	}
	return defaultPlatformRateLimitCooldown
}

func (s *XiaohongshuService) tripRateLimit(scope string, err error) {
	if !errors.Is(err, xhserrors.ErrRateLimited) {
		return
	}
	s.limits.mu.Lock()
	defer s.limits.mu.Unlock()
	now := s.limits.now()
	until := &s.limits.searchUntil
	if scope == "identity" {
		until = &s.limits.identityUntil
	}
	// A blocked local call never extends the existing quiet period.
	if now.Before(*until) {
		return
	}
	*until = now.Add(platformRateLimitCooldown())
	search, identity := s.limits.searchUntil, s.limits.identityUntil
	if s.state == nil {
		return
	}
	s.state.mu.Lock()
	defer s.state.mu.Unlock()
	s.state.data.SearchRateLimitUntil = search
	s.state.data.IdentityRateLimitUntil = identity
	if err := atomicJSON(s.state.path, s.state.data); err != nil {
		s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", err)
	}
}

func (s *XiaohongshuService) errIfRateLimited(scope string) error {
	if s.state != nil {
		s.state.mu.Lock()
		err := s.stateErr
		s.state.mu.Unlock()
		if err != nil {
			return err
		}
	}
	s.limits.mu.Lock()
	defer s.limits.mu.Unlock()
	until := s.limits.identityUntil
	if scope == "search" && s.limits.searchUntil.After(until) {
		until = s.limits.searchUntil
	}
	if !s.limits.now().Before(until) {
		return nil
	}
	return fmt.Errorf("%w: scope=%s retry_at=%s；仅本地静默期，不代表平台已解除限制", xhserrors.ErrRateLimited, scope, until.Format(time.RFC3339))
}
