package execution

import (
	"context"
	"sync/atomic"
)

type submissionKey struct{}
type Submission struct{ dispatched atomic.Bool }

func TrackSubmission(ctx context.Context) context.Context {
	return context.WithValue(ctx, submissionKey{}, &Submission{})
}

// MarkDispatch must be called BEFORE invoking the irreversible click, never after.
func MarkDispatch(ctx context.Context) {
	if s, _ := ctx.Value(submissionKey{}).(*Submission); s != nil {
		s.dispatched.Store(true)
	}
}
func NotDispatched(ctx context.Context) bool {
	s, _ := ctx.Value(submissionKey{}).(*Submission)
	return s != nil && !s.dispatched.Load()
}
func CurrentStage(ctx context.Context) string {
	s, _ := ctx.Value(spanKey{}).(*Span)
	if s == nil {
		return "execute"
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.rec.Stage
}
