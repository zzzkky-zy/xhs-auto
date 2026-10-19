package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

func TestReadPacingStopsAllColdResearchReadsBeforeIdentity(t *testing.T) {
	s := testRuntime(t)
	require.NoError(t, s.readSlot(true))
	s.rememberLogin(false)
	calls := []func() error{
		func() error { _, e := s.ListFeeds(context.Background()); return e },
		func() error { _, e := s.GetFeedDetail(context.Background(), "fixture", "token", false); return e },
		func() error { _, e := s.UserProfile(context.Background(), "fixture", "token", "note"); return e },
		func() error { _, e := s.GetMyProfile(context.Background(), "note"); return e },
		func() error { _, e := s.SearchFeeds(context.Background(), "fixture"); return e },
	}
	for _, call := range calls {
		err := call()
		var deferred *runtimeError
		require.ErrorAs(t, err, &deferred)
		require.Equal(t, "ERR_RATE_LIMIT", deferred.Code)
	}
	require.Len(t, s.state.data.ReadRequests, 1)
}

func TestDetailCacheDoesNotSpendReadsAndIgnoresRotatedToken(t *testing.T) {
	s := testRuntime(t)
	config := xiaohongshu.DefaultCommentLoadConfig()
	key := cacheKey(s, "GetFeedDetailWithConfig", []any{"fixture", false, config})
	readAt := time.Now().Add(-time.Minute)
	s.detailCache.PutFor(key, &FeedDetailResponse{FeedID: "fixture", ReadAt: readAt}, detailCacheTTL())
	require.NoError(t, s.readSlot(true))
	for _, token := range []string{"first", "rotated"} {
		result, err := s.GetFeedDetail(context.Background(), "fixture", token, false)
		require.NoError(t, err)
		require.True(t, result.Cached)
		require.True(t, readAt.Equal(result.ReadAt))
	}
	s.login.mu.Lock()
	s.login.checked = time.Now().Add(-4 * time.Minute)
	s.login.mu.Unlock()
	// Reading a labelled local snapshot does not open a login page merely
	// because its identity evidence is old. A new site read still must check.
	result, err := s.GetFeedDetail(context.Background(), "fixture", "third", false)
	require.NoError(t, err)
	require.True(t, result.Cached)
	require.Len(t, s.state.data.ReadRequests, 1)
	_, err = s.GetFeedDetail(context.WithValue(context.Background(), freshReadKey{}, true), "fixture", "fresh-token", false)
	require.ErrorContains(t, err, "ERR_RATE_LIMIT:")
}

func TestSearchAndDetailsShareDurableReadBudgetWithoutSpendingRefusedSlots(t *testing.T) {
	t.Setenv("XHS_SEARCH_INTERVAL", "60s")
	t.Setenv("XHS_READ_MAX_PER_HOUR", "3")
	s := testRuntime(t)
	now := time.Now()
	base := now
	s.requestNowFn = func() time.Time { return now }
	for i := range 3 {
		now = base.Add(time.Duration(i) * time.Minute)
		require.NoError(t, s.searchSlot(true))
	}
	before, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	now = base.Add(3 * time.Minute)
	require.ErrorContains(t, s.searchSlot(true), "ERR_RATE_LIMIT:")
	require.ErrorContains(t, s.readSlot(true), "ERR_RATE_LIMIT:")
	after, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	require.Equal(t, before, after)
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	restarted.requestNowFn = func() time.Time { return now }
	require.ErrorContains(t, restarted.readSlot(true), "ERR_RATE_LIMIT:")
	require.Len(t, restarted.state.data.SearchRequests, 3)
	now = base.Add(time.Hour)
	require.NoError(t, restarted.readSlot(true))
}

func TestActionPagePolicyDoesNotReuseInputSessions(t *testing.T) {
	require.Equal(t, "fresh_page_per_action", requestPolicy()["browser_page_policy"])
}
