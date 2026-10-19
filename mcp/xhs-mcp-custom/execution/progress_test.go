package execution

import (
	"context"
	"testing"
	"time"
)

func TestEngineeringSubmissionTracking(t *testing.T) {
	ctx := TrackSubmission(context.Background())
	if !NotDispatched(ctx) {
		t.Fatal("initial dispatch flag")
	}
	MarkDispatch(ctx)
	if NotDispatched(ctx) {
		t.Fatal("dispatch flag did not advance")
	}
	if NotDispatched(context.Background()) {
		t.Fatal("untracked code must never prove no submission")
	}
}
func TestEngineeringProgressRecordsNormalCompletion(t *testing.T) {
	ctx, finish := Begin(context.Background(), "pure-read", "task", nil)
	Stage(ctx, "read", time.Second)
	if CurrentStage(ctx) != "read" {
		t.Fatal("missing stage")
	}
	finish(true)
	data := Snapshot()
	found := false
	for _, r := range data["recent"].([]Record) {
		if r.TaskID == "task" && r.Stage == "read" && r.Status == "succeeded" {
			found = true
		}
	}
	if !found {
		t.Fatal("completion missing")
	}
}

func TestEngineeringStageObservationDoesNotCancelBrowserAction(t *testing.T) {
	parent, cancel := context.WithCancel(context.Background())
	defer cancel()
	ctx, finish := Begin(parent, "comment", "slow-input", nil)
	defer finish(false)
	Stage(ctx, "open_comment_input", time.Millisecond)
	// The former 250ms watchdog would cancel this context. Stage estimates are
	// now diagnostics; cancellation still comes from the enclosing operation.
	if err := Sleep(ctx, 350*time.Millisecond); err != nil {
		t.Fatalf("progress observer cancelled input: %v", err)
	}
	cancel()
	if err := ctx.Err(); err != context.Canceled {
		t.Fatalf("parent cancellation lost: %v", err)
	}
}
