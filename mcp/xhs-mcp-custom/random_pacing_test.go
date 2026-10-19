package main

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestRandomReadAndSearchIntervalsRespectBoundsAndLegacyOverrides(t *testing.T) {
	for _, prefix := range []string{"XHS_READ_INTERVAL", "XHS_SEARCH_INTERVAL"} {
		t.Setenv(prefix, "")
		t.Setenv(prefix+"_MIN", "")
		t.Setenv(prefix+"_MAX", "")
	}
	readLow, readHigh := readIntervalBounds()
	searchLow, searchHigh := searchIntervalBounds()
	require.Equal(t, 10*time.Second, readLow)
	require.Equal(t, 20*time.Second, readHigh)
	require.Equal(t, time.Minute, searchLow)
	require.Equal(t, 90*time.Second, searchHigh)
	readSamples, searchSamples := map[time.Duration]bool{}, map[time.Duration]bool{}
	for range 100 {
		read, search := readInterval(), searchInterval()
		require.GreaterOrEqual(t, read, readLow)
		require.LessOrEqual(t, read, readHigh)
		require.GreaterOrEqual(t, search, searchLow)
		require.LessOrEqual(t, search, searchHigh)
		readSamples[read], searchSamples[search] = true, true
	}
	require.Greater(t, len(readSamples), 1)
	require.Greater(t, len(searchSamples), 1)
	t.Setenv("XHS_READ_INTERVAL_MIN", "13s")
	t.Setenv("XHS_READ_INTERVAL_MAX", "17s")
	t.Setenv("XHS_SEARCH_INTERVAL_MIN", "65s")
	t.Setenv("XHS_SEARCH_INTERVAL_MAX", "85s")
	readLow, readHigh = readIntervalBounds()
	searchLow, searchHigh = searchIntervalBounds()
	require.Equal(t, 13*time.Second, readLow)
	require.Equal(t, 17*time.Second, readHigh)
	require.Equal(t, 65*time.Second, searchLow)
	require.Equal(t, 85*time.Second, searchHigh)
	t.Setenv("XHS_READ_INTERVAL", "9s")
	t.Setenv("XHS_SEARCH_INTERVAL", "45s")
	require.Equal(t, 9*time.Second, readInterval())
	require.Equal(t, 45*time.Second, searchInterval())
}

func TestRandomReadAndSearchDeadlinesPersistWithoutRedrawingOnRefusal(t *testing.T) {
	for _, kind := range []string{"read", "search"} {
		t.Run(kind, func(t *testing.T) {
			t.Setenv("XHS_READ_INTERVAL", "")
			t.Setenv("XHS_READ_INTERVAL_MIN", "10s")
			t.Setenv("XHS_READ_INTERVAL_MAX", "20s")
			t.Setenv("XHS_SEARCH_INTERVAL", "")
			t.Setenv("XHS_SEARCH_INTERVAL_MIN", "60s")
			t.Setenv("XHS_SEARCH_INTERVAL_MAX", "90s")
			s := testRuntime(t)
			now := time.Now()
			s.requestNowFn = func() time.Time { return now }
			reserve := s.readSlot
			if kind == "search" {
				reserve = s.searchSlot
			}
			require.NoError(t, reserve(true))
			actionAt := s.state.data.NextActionAt
			require.False(t, actionAt.Before(now.Add(10*time.Second)))
			require.False(t, actionAt.After(now.Add(20*time.Second)))
			if kind == "search" {
				require.False(t, s.state.data.NextSearchAt.Before(now.Add(time.Minute)))
				require.False(t, s.state.data.NextSearchAt.After(now.Add(90*time.Second)))
			}
			before, err := os.ReadFile(s.state.path)
			require.NoError(t, err)
			for range 3 {
				require.ErrorContains(t, reserve(true), "ERR_RATE_LIMIT:")
			}
			after, err := os.ReadFile(s.state.path)
			require.NoError(t, err)
			require.Equal(t, before, after)
			restarted := NewXiaohongshuService()
			require.NoError(t, restarted.InitRuntime())
			require.True(t, actionAt.Equal(restarted.state.data.NextActionAt))
			require.True(t, s.state.data.NextReadAt.Equal(restarted.state.data.NextReadAt))
			require.True(t, s.state.data.NextSearchAt.Equal(restarted.state.data.NextSearchAt))
		})
	}
}

func TestReadWriteAlternationUsesSharedGapWithoutChargingWritesToReadBudget(t *testing.T) {
	s := testRuntime(t)
	now := time.Now()
	s.requestNowFn = func() time.Time { return now }
	require.NoError(t, s.readSlot(true))
	actionAt := s.state.data.NextActionAt
	called := 0
	write := func() (*PostCommentResponse, error) {
		called++
		return &PostCommentResponse{Success: true}, nil
	}
	ctx := withOperationID(context.Background(), "alternating-fixture")
	_, err := protectedWrite(s, ctx, "post_comment_to_feed", "fixture", write)
	var deferred *runtimeError
	require.ErrorAs(t, err, &deferred)
	require.Equal(t, "action", deferred.Scope)
	require.True(t, actionAt.Equal(deferred.RetryAt))
	require.Zero(t, called)
	op, err := s.OperationStatus("alternating-fixture")
	require.ErrorContains(t, err, "ERR_OPERATION_NOT_FOUND")
	require.Nil(t, op)
	require.True(t, s.state.data.NextWriteAt.IsZero())
	now = actionAt
	_, err = protectedWrite(s, ctx, "post_comment_to_feed", "fixture", write)
	require.NoError(t, err)
	require.Equal(t, 1, called)
	require.Len(t, s.state.data.ReadRequests, 1)
	require.ErrorContains(t, s.readSlot(true), "ERR_RATE_LIMIT:")
	require.ErrorContains(t, s.searchSlot(true), "ERR_RATE_LIMIT:")
	require.Len(t, s.state.data.ReadRequests, 1)
	require.Empty(t, s.state.data.SearchRequests)
	now = s.state.data.NextActionAt
	require.NoError(t, s.readSlot(true))
	require.Len(t, s.state.data.ReadRequests, 2)
}

func TestSharedGapStopsNotificationDMAndIdentityNavigationBeforeBrowser(t *testing.T) {
	s := testRuntime(t)
	now := time.Now()
	s.requestNowFn = func() time.Time { return now }
	s.state.data.NextActionAt = now.Add(15 * time.Second)
	require.NoError(t, atomicJSON(s.state.path, s.state.data))
	s.rememberLogin(false)
	calls := []struct {
		name string
		call func() error
	}{
		{"unread", func() error { _, err := s.GetUnreadCount(context.Background()); return err }},
		{"notifications", func() error { _, err := s.ListNotifications(context.Background(), "comments", 10); return err }},
		{"direct_messages", func() error { _, err := s.ListDirectMessages(context.Background(), 10); return err }},
		{"open_direct_message", func() error {
			_, err := s.ReplyDirectMessage(context.Background(), "fixture", "", "", true)
			return err
		}},
		{"identity", func() error {
			_, err := s.CheckLoginStatus(context.WithValue(context.Background(), freshReadKey{}, true))
			return err
		}},
		{"probe", func() error { _, err := s.probeAndSyncLoginLocked(context.Background(), false); return err }},
	}
	before, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	for _, call := range calls {
		t.Run(call.name, func(t *testing.T) {
			var deferred *runtimeError
			require.ErrorAs(t, call.call(), &deferred)
			require.Equal(t, "ERR_RATE_LIMIT", deferred.Code)
			require.True(t, s.state.data.NextActionAt.Equal(deferred.RetryAt))
		})
	}
	after, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	require.Equal(t, before, after)
	require.Empty(t, s.state.data.ReadRequests)
}

func TestFreshContentReadReusesVerifiedIdentityWithoutNavigatingAgain(t *testing.T) {
	for _, afterIdentitySlot := range []bool{false, true} {
		name := "valid_identity"
		if afterIdentitySlot {
			name = "continue_after_identity_slot"
		}
		t.Run(name, func(t *testing.T) {
			s := testRuntime(t)
			now := time.Now()
			s.requestNowFn = func() time.Time { return now }
			if afterIdentitySlot {
				// Simulate the persisted slot occupied by a cold identity read.
				// The actual identity page is unnecessary for this local regression.
				s.rememberLogin(false)
				require.NoError(t, s.readSlot(true))
				var pending FeedsListResponse
				_, _, err := prepareRead(s, context.WithValue(context.Background(), freshReadKey{}, true), "fixture-fresh", nil, &s.readCache, &pending, readTTL())
				require.ErrorContains(t, err, "ERR_RATE_LIMIT:")
				s.rememberLogin(true)
				now = s.state.data.NextActionAt
			}
			key := cacheKey(s, "fixture-fresh", nil)
			s.readCache.Put(key, &FeedsListResponse{Count: 99})
			before, err := os.ReadFile(s.state.path)
			require.NoError(t, err)
			// Hold the browser lock so a regression cannot accidentally connect
			// to CDP: cached identity must let prepareRead complete without it.
			s.browserMu.Lock()
			defer s.browserMu.Unlock()
			ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
			defer cancel()
			ctx = context.WithValue(ctx, freshReadKey{}, true)
			var result FeedsListResponse
			_, hit, err := prepareRead(s, ctx, "fixture-fresh", nil, &s.readCache, &result, readTTL())
			require.NoError(t, err)
			require.False(t, hit)
			require.Zero(t, result.Count)
			after, err := os.ReadFile(s.state.path)
			require.NoError(t, err)
			require.Equal(t, before, after)
			if afterIdentitySlot {
				require.Len(t, s.state.data.ReadRequests, 1)
			} else {
				require.Empty(t, s.state.data.ReadRequests)
			}
		})
	}
}
