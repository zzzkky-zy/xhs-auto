package main

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
)

func validateListenAddress(addr, token string) error {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return fmt.Errorf("监听地址必须是 host:port: %w", err)
	}
	ip := net.ParseIP(host)
	if token == "" && host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return fmt.Errorf("ERR_AUTH_REQUIRED: 非本机监听必须配置 AUTH_TOKEN 或宿主认证代理")
	}
	return nil
}

func requestRuntimeMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Minute)
		defer cancel()
		if id := c.GetHeader("Idempotency-Key"); id != "" {
			ctx = withOperationID(ctx, id)
		}
		c.Request = c.Request.WithContext(ctx)
		c.Next()
	}
}

// Admission closes atomically before a controlled idle reload.
func (s *AppServer) admissionMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.URL.Path == "/runtime" || c.Request.URL.Path == "/runtime/drain" {
			c.Next()
			return
		}
		s.maintenanceMu.Lock()
		if s.draining {
			s.maintenanceMu.Unlock()
			respondError(c, 503, "ERR_MCP_NOT_DISPATCHED", "service reloading; no tool dispatched", nil)
			c.Abort()
			return
		}
		s.activeRequests++
		s.maintenanceMu.Unlock()
		defer func() { s.maintenanceMu.Lock(); s.activeRequests--; s.maintenanceMu.Unlock() }()
		c.Next()
	}
}

func (s *AppServer) drainHandler(c *gin.Context) {
	s.maintenanceMu.Lock()
	defer s.maintenanceMu.Unlock()
	if s.activeRequests != 0 {
		respondError(c, 409, "ERR_CONFIG_PENDING_RELOAD", "request executing; retry when idle", nil)
		return
	}
	s.draining = true
	respondSuccess(c, gin.H{"draining": true}, "idle service reserved for reload")
}

func (s *AppServer) runtimeStatusHandler(c *gin.Context) {
	state := s.xiaohongshuService.state
	if state == nil {
		respondError(c, 503, "ERR_STATE_NOT_INITIALIZED", "运行记录未初始化", nil)
		return
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	statuses, err := state.operationCounts()
	if err != nil {
		respondError(c, 503, "ERR_STATE_WRITE", "operation journal unavailable", nil)
		return
	}
	respondSuccess(c, gin.H{"version": version, "config_fingerprint": os.Getenv("XHS_CONFIG_FINGERPRINT"), "account": state.data.Account, "state_id": state.data.StateID, "write_enabled": requireWriteAllowed() == nil, "captcha_until": state.data.CaptchaUntil, "next_write_at": state.data.NextWriteAt, "next_publish_at": state.data.NextPublishAt, "next_search_at": state.data.NextSearchAt, "next_read_at": state.data.NextReadAt, "request_policy": requestPolicy(), "operations": statuses, "identity": s.xiaohongshuService.identitySnapshot(), "captcha_active": time.Now().Before(state.data.CaptchaUntil), "search_rate_limit_until": state.data.SearchRateLimitUntil, "identity_rate_limit_until": state.data.IdentityRateLimitUntil, "persistence_error": s.xiaohongshuService.stateErr != nil}, "进程记录，不代表平台已登录")
}

func (s *AppServer) operationStatusHandler(c *gin.Context) {
	op, err := s.xiaohongshuService.OperationStatus(c.Param("id"))
	if err != nil {
		respondError(c, http.StatusNotFound, "ERR_OPERATION_STATUS", err.Error(), nil)
		return
	}
	respondSuccess(c, op, "提交记录")
}
