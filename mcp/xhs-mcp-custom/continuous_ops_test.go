package main

import (
	"context"
	"github.com/stretchr/testify/require"
	"strings"
	"testing"
	"time"
)

func TestRandomWriteDeadlineDrawnOnceAndRetainedAfterRestart(t *testing.T) {
	t.Setenv("XHS_WRITE_INTERVAL", "")
	t.Setenv("XHS_WRITE_INTERVAL_MIN", "60s")
	t.Setenv("XHS_WRITE_INTERVAL_MAX", "180s")
	s := testRuntime(t)
	now := time.Now()
	s.requestNowFn = func() time.Time { return now }
	_, err := protectedWrite(s, withOperationID(context.Background(), "random-fixture"), "post_comment_to_feed", "fixture", func() (*PostCommentResponse, error) { return &PostCommentResponse{Success: true}, nil })
	require.NoError(t, err)
	deadline := s.state.data.NextWriteAt
	require.True(t, !deadline.Before(now.Add(time.Minute)) && !deadline.After(now.Add(3*time.Minute)))
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	require.True(t, deadline.Equal(restarted.state.data.NextWriteAt))
}

func TestResearchCacheHasByteAndEntryBounds(t *testing.T) {
	c := resultCache{}
	for i := 0; i < 30; i++ {
		c.PutFor(time.Duration(i).String(), strings.Repeat("x", 1024*1024), time.Hour)
	}
	total := 0
	for _, e := range c.entries {
		total += len(e.data)
	}
	require.LessOrEqual(t, total, 8*1024*1024)
	require.LessOrEqual(t, len(c.entries), 32)
	c.PutFor("oversize", strings.Repeat("x", 3*1024*1024), time.Hour)
	_, ok := c.entries["oversize"]
	require.False(t, ok)
	c.Clear()
	require.Empty(t, c.entries)
}
