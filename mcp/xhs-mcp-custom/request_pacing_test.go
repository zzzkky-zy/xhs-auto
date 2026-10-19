package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

func TestSearchPacingPrecedesIdentityAndDoesNotExtendOnRefusal(t *testing.T) {
	s := testRuntime(t)
	now := time.Now()
	s.requestNowFn = func() time.Time { return now }
	require.NoError(t, s.searchSlot(true))
	deadline := s.state.data.NextSearchAt
	s.rememberLogin(false) // A deferred search must not try to recheck this identity.
	before, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	for range 5 {
		_, err := s.SearchFeeds(context.Background(), "another keyword")
		var deferred *runtimeError
		require.ErrorAs(t, err, &deferred)
		require.Equal(t, "ERR_RATE_LIMIT", deferred.Code)
		require.Equal(t, deadline, deferred.RetryAt)
	}
	after, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	require.Equal(t, before, after)
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	restarted.requestNowFn = func() time.Time { return now }
	require.ErrorContains(t, restarted.searchSlot(true), "ERR_RATE_LIMIT:")
	now = deadline
	require.NoError(t, restarted.searchSlot(true))
	require.Len(t, restarted.state.data.SearchRequests, 2)
}

func TestSearchRollingHourBudgetCannotBurstOrResetOnRestart(t *testing.T) {
	t.Setenv("XHS_SEARCH_INTERVAL", "60s")
	s := testRuntime(t)
	base := time.Now()
	now := base
	s.requestNowFn = func() time.Time { return now }
	for i := range 20 {
		now = base.Add(time.Duration(i) * time.Minute)
		require.NoError(t, s.searchSlot(true))
	}
	require.Equal(t, base.Add(time.Hour), s.state.data.NextSearchAt)
	now = base.Add(30 * time.Minute)
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	restarted.requestNowFn = func() time.Time { return now }
	require.ErrorContains(t, restarted.searchSlot(true), "ERR_RATE_LIMIT:")
	require.Len(t, restarted.state.data.SearchRequests, 20)
	now = base.Add(time.Hour)
	require.NoError(t, restarted.searchSlot(true))
	require.Len(t, restarted.state.data.SearchRequests, 20)
}

func TestSearchCacheReusesResultsAcrossWritesAndFreshStillObeysBudget(t *testing.T) {
	s := testRuntime(t)
	now := time.Now()
	s.requestNowFn = func() time.Time { return now }
	readAt := time.Now().Add(-time.Minute)
	key := cacheKey(s, "SearchFeeds", []any{"fixture", []xiaohongshu.FilterOption(nil)})
	s.searchCache.PutFor(key, &FeedsListResponse{Count: 2, ReadAt: readAt}, searchCacheTTL())
	require.NoError(t, s.searchSlot(true))
	deadline := s.state.data.NextSearchAt
	for range 2 {
		result, err := s.SearchFeeds(context.Background(), " fixture ")
		require.NoError(t, err)
		require.True(t, result.Cached)
		require.True(t, readAt.Equal(result.ReadAt))
	}
	now = s.state.data.NextActionAt
	_, err := protectedWrite(s, withOperationID(context.Background(), "comment-cache"), "post_comment_to_feed", "fixture", func() (*PostCommentResponse, error) {
		return &PostCommentResponse{Success: true}, nil
	})
	require.NoError(t, err)
	result, err := s.SearchFeeds(context.Background(), "fixture")
	require.NoError(t, err)
	require.True(t, result.Cached)
	require.Equal(t, deadline, s.state.data.NextSearchAt)
	require.Len(t, s.state.data.SearchRequests, 1)
	_, err = s.SearchFeeds(context.WithValue(context.Background(), freshReadKey{}, true), "fixture")
	require.ErrorContains(t, err, "ERR_RATE_LIMIT:")
}

func TestPublicationPacingIncludesScheduledPostsButKeepsCommentsAvailable(t *testing.T) {
	t.Setenv("XHS_PUBLISH_INTERVAL", "90m") // Explicit older operating policy remains valid.
	s := testRuntime(t)
	base := time.Now()
	now := base
	s.requestNowFn = func() time.Time { return now }
	calls := 0
	publish := func() (*PublishResponse, error) { calls++; return &PublishResponse{Status: "success"}, nil }
	_, err := protectedWrite(s, withOperationID(context.Background(), "scheduled-one"), "publish_content", PublishRequest{Title: "one", ScheduleAt: "2030-10-20T10:00:00+08:00"}, publish)
	require.NoError(t, err)
	now = base.Add(4 * time.Minute)
	_, err = protectedWrite(s, withOperationID(context.Background(), "scheduled-two"), "publish_with_video", PublishVideoRequest{Title: "two", ScheduleAt: "2030-10-20T12:00:00+08:00"}, publish)
	var deferred *runtimeError
	require.ErrorAs(t, err, &deferred)
	require.Equal(t, "publish", deferred.Scope)
	require.Equal(t, base.Add(90*time.Minute), deferred.RetryAt)
	require.Equal(t, 1, calls)
	_, err = protectedWrite(s, withOperationID(context.Background(), "comment-between"), "post_comment_to_feed", "comment", func() (*PostCommentResponse, error) { return &PostCommentResponse{Success: true}, nil })
	require.NoError(t, err)
	now = base.Add(90 * time.Minute)
	_, err = protectedWrite(s, withOperationID(context.Background(), "scheduled-two"), "publish_with_video", PublishVideoRequest{Title: "two", ScheduleAt: "2030-10-20T12:00:00+08:00"}, publish)
	require.NoError(t, err)
	require.Equal(t, 2, calls)
}

func TestPreSubmitPublicationFailureDoesNotWastePublicationSlot(t *testing.T) {
	s := testRuntime(t)
	ctx := execution.TrackSubmission(withOperationID(context.Background(), "not-submitted"))
	_, err := protectedWrite(s, ctx, "publish_content", "fixture", func() (*PublishResponse, error) { return nil, context.DeadlineExceeded })
	require.ErrorContains(t, err, "ERR_ACTION_NOT_SUBMITTED")
	require.True(t, s.state.data.NextPublishAt.IsZero())
	require.True(t, s.state.data.NextWriteAt.After(time.Now()))
	op, err := s.OperationStatus("not-submitted")
	require.NoError(t, err)
	require.Equal(t, "not_applied", op.Status)
}

func TestUpgradeRetainsPreviousPublicationPacing(t *testing.T) {
	t.Setenv("XHS_PUBLISH_INTERVAL", "90m")
	s := testRuntime(t)
	at := time.Now()
	s.state.data.Version = 1 // This fixture represents the old JSON journal.
	s.state.data.Operations["old-post"] = &operationRecord{ID: "old-post", Tool: "publish_content", Status: "succeeded", UpdatedAt: at}
	require.NoError(t, atomicJSON(s.state.path, s.state.data))
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	require.True(t, at.Add(90*time.Minute).Equal(restarted.state.data.NextPublishAt))
}

func TestTwentyScheduledSubmissionsAtOldCadenceOnlyDispatchOne(t *testing.T) {
	t.Setenv("XHS_PUBLISH_INTERVAL", "90m")
	s := testRuntime(t)
	base := time.Now()
	now := base
	s.requestNowFn = func() time.Time { return now }
	dispatched := 0
	for i := range 20 {
		now = base.Add(time.Duration(i) * 150 * time.Second)
		id := time.Duration(i).String()
		_, err := protectedWrite(s, withOperationID(context.Background(), id), "publish_content", id, func() (*PublishResponse, error) {
			dispatched++
			return &PublishResponse{Status: "success"}, nil
		})
		if i == 0 {
			require.NoError(t, err)
		} else {
			var deferred *runtimeError
			require.ErrorAs(t, err, &deferred)
			require.Equal(t, base.Add(90*time.Minute), deferred.RetryAt)
		}
	}
	require.Equal(t, 1, dispatched)
	require.Empty(t, s.state.data.Operations)
	counts, err := s.state.operationCounts()
	require.NoError(t, err)
	require.Equal(t, 1, counts["succeeded"])
}
