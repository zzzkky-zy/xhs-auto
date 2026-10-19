package main

import (
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"os"
	"time"
)

func (s *XiaohongshuService) identitySnapshot() map[string]any {
	s.login.mu.Lock()
	defer s.login.mu.Unlock()
	fresh := s.login.ok && !s.login.checked.IsZero() && time.Since(s.login.checked) < defaultLoginCacheTTL && s.login.generation == browser.Generation()
	return map[string]any{"user_id": s.login.userID, "fresh": fresh, "checked_at_unix": float64(s.login.checked.UnixMilli()) / 1000, "source": "verified browser identity; freshness alone is not a platform write receipt"}
}
func (s *XiaohongshuService) executionSnapshot() map[string]any { return execution.Snapshot() }
func toolTimeout() time.Duration {
	if d, e := time.ParseDuration(os.Getenv("XHS_TOOL_TIMEOUT")); e == nil && d >= time.Second && d <= 3*time.Minute {
		return d
	}
	return 3 * time.Minute
}

func (s *XiaohongshuService) identityGenerationCurrent() bool {
	s.login.mu.Lock()
	defer s.login.mu.Unlock()
	return s.login.generation == browser.Generation()
}
