package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	bolt "go.etcd.io/bbolt"
)

func TestJournalMigratesHistoryAndKeepsRuntimeStateBounded(t *testing.T) {
	s := testRuntime(t)
	s.state.data.Version = 1
	for i := 0; i < 5000; i++ {
		id := fmt.Sprintf("history-%05d", i)
		s.state.data.Operations[id] = &operationRecord{ID: id, Tool: "comment", Fingerprint: id, Status: "succeeded", Result: json.RawMessage(`{"success":true}`)}
	}
	require.NoError(t, atomicJSON(s.state.path, s.state.data))
	st, err := openStateStore(s.state.path, "fixture")
	require.NoError(t, err)
	require.Empty(t, st.data.Operations)
	raw, err := os.ReadFile(st.path)
	require.NoError(t, err)
	require.Less(t, len(raw), 4096)
	op, err := st.receipt("history-02500", "")
	require.NoError(t, err)
	require.Equal(t, "succeeded", op.Status)
	op, err = st.receipt("", "history-04999")
	require.NoError(t, err)
	require.Equal(t, "history-04999", op.ID)
	counts, err := st.operationCounts()
	require.NoError(t, err)
	require.Equal(t, 5000, counts["succeeded"])
	// One new result does not load or serialize the old 5000 receipts.
	require.NoError(t, st.saveOperation(&operationRecord{ID: "new", Fingerprint: "new", Status: "succeeded"}))
	require.Empty(t, st.data.Operations)
	info, err := os.Stat(st.path)
	require.NoError(t, err)
	require.Less(t, info.Size(), int64(4096))
}

func TestJournalMissingAfterMigrationNeverCreatesAnEmptyReplacement(t *testing.T) {
	s := testRuntime(t)
	require.NoError(t, os.Remove(s.state.journalPath()))
	_, err := openStateStore(s.state.path, "fixture")
	require.ErrorContains(t, err, "ERR_STATE_LOST")
	_, err = os.Stat(s.state.journalPath())
	require.True(t, os.IsNotExist(err))
}

func TestJournalRollbackExportsNewReceiptsAndDeadlines(t *testing.T) {
	s := testRuntime(t)
	ctx := withOperationID(context.Background(), "after-upgrade")
	calls := 0
	fn := func() (*PostCommentResponse, error) { calls++; return &PostCommentResponse{Success: true}, nil }
	_, err := protectedWrite(s, ctx, "comment", "fixture", fn)
	require.NoError(t, err)
	deadline := s.state.data.NextWriteAt
	require.NoError(t, s.state.exportLegacy())
	raw, err := os.ReadFile(s.state.path)
	require.NoError(t, err)
	var legacy runtimeState
	require.NoError(t, json.Unmarshal(raw, &legacy))
	require.Equal(t, 1, legacy.Version)
	require.Equal(t, "succeeded", legacy.Operations["after-upgrade"].Status)
	require.True(t, deadline.Equal(legacy.NextWriteAt))
	// An old version may add receipts after rollback. Re-upgrade imports them
	// without dropping receipts or multiplying counts.
	legacy.Operations["while-rolled-back"] = &operationRecord{ID: "while-rolled-back", Fingerprint: "other", Status: "confirmed", UpdatedAt: time.Now()}
	require.NoError(t, atomicJSON(s.state.path, legacy))
	restarted := NewXiaohongshuService()
	require.NoError(t, restarted.InitRuntime())
	_, err = protectedWrite(restarted, ctx, "comment", "fixture", fn)
	require.NoError(t, err)
	require.Equal(t, 1, calls)
	op, err := restarted.OperationStatus("while-rolled-back")
	require.NoError(t, err)
	require.Equal(t, "confirmed", op.Status)
	counts, err := restarted.state.operationCounts()
	require.NoError(t, err)
	require.Equal(t, 1, counts["succeeded"])
	require.Equal(t, 1, counts["confirmed"])
}

func TestJournalTerminalReceiptIsAuthoritativeAfterJSONWriteLoss(t *testing.T) {
	s := testRuntime(t)
	op := &operationRecord{ID: "result-saved", Fingerprint: "same", Status: "in_flight"}
	require.NoError(t, s.state.saveOperation(op))
	op.Status = "succeeded"
	// Simulate the last small JSON replacement not yet having happened.
	require.NoError(t, s.state.journal(false, func(tx *bolt.Tx) error { return saveReceipt(tx, op) }))
	st, err := openStateStore(s.state.path, "fixture")
	require.NoError(t, err)
	require.Empty(t, st.data.Operations)
	receipt, err := st.receipt("result-saved", "")
	require.NoError(t, err)
	require.Equal(t, "succeeded", receipt.Status)
}
