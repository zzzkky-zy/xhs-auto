package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
)

func TestRateLimitSearchQuietPeriodPreservesVerifiedIdentity(t *testing.T) {
	s := testRuntime(t)
	s.tripRateLimit("search", xhserrors.ErrRateLimited)
	_, err := s.SearchFeeds(context.Background(), "fixture")
	require.ErrorIs(t, err, xhserrors.ErrRateLimited) // no browser is available or needed
	identity, err := s.CheckLoginStatus(context.Background())
	require.NoError(t, err)
	require.True(t, identity.IsLoggedIn)
	require.Equal(t, "fixture-user", identity.UserID)
	require.NoError(t, s.errIfRateLimited("identity"))
}

func TestRateLimitPersistsAndLocalChecksNeverRenewDeadline(t *testing.T) {
	s := testRuntime(t)
	s.tripRateLimit("search", xhserrors.ErrRateLimited)
	deadline := s.limits.searchUntil
	before, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	for range 4 {
		require.ErrorIs(t, s.errIfRateLimited("search"), xhserrors.ErrRateLimited)
	}
	s.tripRateLimit("search", xhserrors.ErrRateLimited)
	require.Equal(t, deadline, s.limits.searchUntil)
	after, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	require.Equal(t, before, after)
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	require.ErrorIs(t, restarted.errIfRateLimited("search"), xhserrors.ErrRateLimited)
	require.NoError(t, restarted.errIfRateLimited("identity"))
	// The first later requested search may try again; the clock itself does nothing.
	restarted.limits.nowFn = func() time.Time { return deadline.Add(time.Second) }
	require.NoError(t, restarted.errIfRateLimited("search"))
}

func TestRateLimitHomepageCannotBeSoftAllowedOrMarkLoggedIn(t *testing.T) {
	s := testRuntime(t)
	s.rememberLogin(false)
	s.tripRateLimit("identity", xhserrors.ErrRateLimited)
	_, err := s.CheckLoginStatus(context.Background())
	require.ErrorIs(t, err, xhserrors.ErrRateLimited)
	require.ErrorIs(t, s.ensureLoggedIn(context.Background()), xhserrors.ErrRateLimited)
	_, ok := s.cachedLoginOK()
	require.False(t, ok)
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	require.ErrorIs(t, restarted.errIfRateLimited("identity"), xhserrors.ErrRateLimited)
}

func TestRateLimitAfterDispatchStillRequiresReconciliation(t *testing.T) {
	s := testRuntime(t)
	_, err := protectedWrite(s, withOperationID(context.Background(), "platform-after-send"), "publish_content", "fixture", func() (*PublishResponse, error) {
		return nil, xhserrors.ErrRateLimited
	})
	require.ErrorContains(t, err, "ERR_OPERATION_UNKNOWN")
	require.ErrorIs(t, s.errIfRateLimited("identity"), xhserrors.ErrRateLimited)
	op, err := s.OperationStatus("platform-after-send")
	require.NoError(t, err)
	require.Equal(t, "unknown", op.Status)
}

func TestLocalWriteRefusalKeepsCachedReads(t *testing.T) {
	s := testRuntime(t)
	s.readCache.Put("fixture-read", map[string]int{"value": 1})
	s.state.data.NextWriteAt = time.Now().Add(time.Minute)
	_, err := protectedWrite(s, withOperationID(context.Background(), "local-refusal"), "publish_content", "fixture", func() (*PublishResponse, error) {
		t.Fatal("a locally rate-limited operation must never reach the browser")
		return nil, nil
	})
	require.ErrorContains(t, err, "ERR_RATE_LIMIT:")
	var cached map[string]int
	require.True(t, s.readCache.Get(context.Background(), "fixture-read", &cached))
	require.Equal(t, 1, cached["value"])
}
