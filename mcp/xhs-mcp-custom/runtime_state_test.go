package main

import (
	"context"
	"errors"
	"github.com/stretchr/testify/require"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func testRuntime(t *testing.T) *XiaohongshuService {
	t.Helper()
	t.Setenv("XHS_STATE_DIR", t.TempDir())
	t.Setenv("XHS_ACCOUNT_ID", "fixture")
	t.Setenv("XHS_ALLOW_WRITE", "1")
	t.Setenv("XHS_EXPECTED_USER_ID", "fixture-user")
	s := NewXiaohongshuService()
	require.NoError(t, s.InitRuntime())
	t.Setenv("XHS_EXPECTED_STATE_ID", s.state.data.StateID)
	s.rememberLogin(true)
	s.login.userID = "fixture-user"
	return s
}

func TestRuntimeCaptchaSurvivesRestart(t *testing.T) {
	s := testRuntime(t)
	s.tripCaptchaCooldown(xhserrors.ErrCaptcha)
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	require.ErrorIs(t, restarted.errIfCaptchaCooldown("test"), xhserrors.ErrCaptcha)
}

func TestRuntimeSuccessfulReplayAndPayloadConflict(t *testing.T) {
	s := testRuntime(t)
	ctx := withOperationID(context.Background(), "stable-id")
	n := 0
	fn := func() (*PostCommentResponse, error) {
		n++
		return &PostCommentResponse{Success: true, Message: "fixture"}, nil
	}
	_, err := protectedWrite(s, ctx, "post_comment_to_feed", []string{"f", "c"}, fn)
	require.NoError(t, err)
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	value, err := protectedWrite(restarted, ctx, "post_comment_to_feed", []string{"f", "c"}, fn)
	require.NoError(t, err)
	require.True(t, value.Success)
	require.Equal(t, 1, n)
	_, err = protectedWrite(restarted, ctx, "post_comment_to_feed", []string{"f", "other"}, fn)
	require.ErrorContains(t, err, "ERR_IDEMPOTENCY_CONFLICT")
	require.Equal(t, 1, n)
}

func TestRuntimeUnknownIsProtectedAcrossIndependentRequestAndRestart(t *testing.T) {
	s := testRuntime(t)
	ctx := withOperationID(context.Background(), "unknown-id")
	_, err := protectedWrite(s, ctx, "publish_content", "fixture", func() (*PublishResponse, error) { return nil, errors.New("network interrupted after click") })
	require.ErrorContains(t, err, "ERR_OPERATION_UNKNOWN")
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	called := false
	fn := func() (*PublishResponse, error) { called = true; return &PublishResponse{}, nil }
	_, err = protectedWrite(restarted, ctx, "publish_content", "fixture", fn)
	require.ErrorContains(t, err, "ERR_OPERATION_UNKNOWN")
	_, err = protectedWrite(restarted, withOperationID(context.Background(), "changed-id"), "publish_content", "fixture", fn)
	require.ErrorContains(t, err, "ERR_DUPLICATE_OPERATION")
	require.False(t, called)
	before, err := restarted.OperationStatus("unknown-id")
	require.NoError(t, err)
	restarted.rememberLogin(true)
	restarted.login.userID = "fixture-user"
	restarted.requestNowFn = func() time.Time { return time.Now().Add(10 * time.Minute) }
	_, err = protectedWrite(restarted, withOperationID(context.Background(), "another"), "post_comment_to_feed", "new-target-and-content", func() (*PostCommentResponse, error) {
		called = true
		return &PostCommentResponse{Success: true}, nil
	})
	require.NoError(t, err)
	require.True(t, called)
	op, err := restarted.OperationStatus("unknown-id")
	require.NoError(t, err)
	require.Equal(t, before, op)
	restartedAgain := NewXiaohongshuService()
	require.NoError(t, restartedAgain.InitRuntime())
	op, err = restartedAgain.OperationStatus("unknown-id")
	require.NoError(t, err)
	require.Equal(t, before, op)
}

func TestRuntimeCommentBeforeSubmitPersistsNotApplied(t *testing.T) {
	s := testRuntime(t)
	ctx := withOperationID(context.Background(), "pre-submit")
	_, err := protectedWrite(s, ctx, "post_comment_to_feed", "fixture", func() (*PostCommentResponse, error) {
		return nil, &xiaohongshu.CommentActionError{Phase: "input", Cause: context.DeadlineExceeded}
	})
	require.ErrorContains(t, err, "ERR_COMMENT_NOT_SUBMITTED")
	require.NotContains(t, err.Error(), "ERR_OPERATION_UNKNOWN")
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	op, err := restarted.OperationStatus("pre-submit")
	require.NoError(t, err)
	require.Equal(t, "not_applied", op.Status)
	require.Contains(t, op.Evidence, "phase=input")
}

func TestRuntimeCommentAfterSubmitPersistsUnknown(t *testing.T) {
	s := testRuntime(t)
	_, err := protectedWrite(s, withOperationID(context.Background(), "after-submit"), "reply_comment_in_feed", "fixture", func() (*ReplyCommentResponse, error) {
		return nil, &xiaohongshu.CommentActionError{Phase: "confirm", Submitted: true, Cause: context.DeadlineExceeded}
	})
	require.ErrorContains(t, err, "ERR_OPERATION_UNKNOWN")
	op, err := s.OperationStatus("after-submit")
	require.NoError(t, err)
	require.Equal(t, "unknown", op.Status)
}

func TestRuntimeInflightRecoveredAsUnknown(t *testing.T) {
	s := testRuntime(t)
	require.NoError(t, s.state.saveOperation(&operationRecord{ID: "interrupted", Status: "in_flight"}))
	next, err := openStateStore(s.state.path, "fixture")
	require.NoError(t, err)
	require.Equal(t, "unknown", next.data.Operations["interrupted"].Status)
}

func TestRuntimeRateLimitPersistsAndDoesNotSleep(t *testing.T) {
	s := testRuntime(t)
	_, err := protectedWrite(s, withOperationID(context.Background(), "first"), "comment", "one", func() (*PostCommentResponse, error) { return &PostCommentResponse{Success: true}, nil })
	require.NoError(t, err)
	start := time.Now()
	_, err = protectedWrite(s, withOperationID(context.Background(), "second"), "comment", "two", func() (*PostCommentResponse, error) { t.Fatal("must not submit"); return nil, nil })
	require.ErrorContains(t, err, "ERR_RATE_LIMIT")
	require.Less(t, time.Since(start), time.Second)
}

func TestRuntimeDuplicateWithDifferentIDRejected(t *testing.T) {
	s := testRuntime(t)
	fn := func() (*PostCommentResponse, error) { return &PostCommentResponse{Success: true}, nil }
	_, err := protectedWrite(s, withOperationID(context.Background(), "one"), "comment", "same", fn)
	require.NoError(t, err)
	_, err = protectedWrite(s, withOperationID(context.Background(), "two"), "comment", "same", fn)
	require.ErrorContains(t, err, "ERR_DUPLICATE_OPERATION")
}

func TestRuntimePanicRecordsUnknown(t *testing.T) {
	s := testRuntime(t)
	_, err := protectedWrite(s, withOperationID(context.Background(), "panic"), "comment", "c", func() (*PostCommentResponse, error) { panic("fixture") })
	require.ErrorContains(t, err, "ERR_OPERATION_UNKNOWN")
	op, err := s.OperationStatus("panic")
	require.NoError(t, err)
	require.Equal(t, "unknown", op.Status)
}

func TestRuntimeResolutionRequiresGateAndEvidence(t *testing.T) {
	s := testRuntime(t)
	require.NoError(t, s.state.saveOperation(&operationRecord{ID: "id", Status: "unknown"}))
	_, err := s.ResolveOperation("id", "not_applied", "verified platform evidence")
	require.ErrorContains(t, err, "ERR_RECONCILE_NOT_ALLOWED")
	t.Setenv("XHS_ALLOW_RECONCILE", "1")
	_, err = s.ResolveOperation("id", "not_applied", "")
	require.Error(t, err)
	_, err = s.ResolveOperation("id", "not_applied", "fixture record and timestamp")
	require.NoError(t, err)
}

func TestRuntimeStateCorruptionNeverReset(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")
	require.NoError(t, os.WriteFile(path, []byte("broken"), 0600))
	_, err := openStateStore(path, "fixture")
	require.ErrorContains(t, err, "ERR_STATE_CORRUPT")
	raw, _ := os.ReadFile(path)
	require.Equal(t, "broken", string(raw))
}

func TestRuntimeAccountMismatchAndPrivateMode(t *testing.T) {
	s := testRuntime(t)
	_, err := openStateStore(s.state.path, "another-account")
	require.ErrorContains(t, err, "ERR_STATE_INCOMPATIBLE")
	info, err := os.Stat(s.state.path)
	require.NoError(t, err)
	require.Equal(t, os.FileMode(0600), info.Mode().Perm())
}

func TestRuntimeSerialConcurrentTrigger(t *testing.T) {
	s := testRuntime(t)
	ctx := withOperationID(context.Background(), "same")
	var calls atomic.Int32
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := protectedWrite(s, ctx, "comment", "same", func() (*PostCommentResponse, error) { calls.Add(1); return &PostCommentResponse{Success: true}, nil })
			if err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	require.Equal(t, int32(1), calls.Load())
}

func TestRuntimeBrowserLockCanTimeout(t *testing.T) {
	var m contextMutex
	m.Lock()
	defer m.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	require.ErrorIs(t, m.LockContext(ctx), context.DeadlineExceeded)
}

func TestRuntimeWriteGateCoversAllMutationServices(t *testing.T) {
	t.Setenv("XHS_ALLOW_WRITE", "0")
	s := NewXiaohongshuService()
	ctx := context.Background()
	calls := []func() error{
		func() error { _, e := s.PublishContent(ctx, &PublishRequest{}); return e }, func() error { _, e := s.PublishVideo(ctx, &PublishVideoRequest{}); return e },
		func() error { _, e := s.PostCommentToFeed(ctx, "", "", ""); return e }, func() error { _, e := s.ReplyCommentToFeed(ctx, "", "", "", "", ""); return e },
		func() error { _, e := s.LikeFeed(ctx, "", ""); return e }, func() error { _, e := s.FavoriteFeed(ctx, "", ""); return e },
		func() error { _, e := s.ReplyNotification(ctx, "", ""); return e }, func() error { _, e := s.LikeNotification(ctx, "", false); return e }, func() error { _, e := s.ReplyDirectMessage(ctx, "", "", "", false); return e }}
	for _, call := range calls {
		require.ErrorIs(t, call(), xhserrors.ErrWriteNotAllowed)
	}
}

func TestRuntimeListenAuthRules(t *testing.T) {
	require.NoError(t, validateListenAddress("127.0.0.1:18061", ""))
	require.NoError(t, validateListenAddress("0.0.0.0:18061", "fixture-token"))
	require.True(t, strings.Contains(validateListenAddress(":18061", "").Error(), "ERR_AUTH_REQUIRED"))
}
