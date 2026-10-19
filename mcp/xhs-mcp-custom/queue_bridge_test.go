package main

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestQueueBridgePreservesSafeExecutorDomainCode(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 unavailable")
	}
	for _, tt := range []struct {
		name, stderr, prefix string
		absent               string
	}{
		{"domain refusal", `{"error":"ERR_WRITE_NOT_ALLOWED: writes disabled"}`, "ERR_WRITE_NOT_ALLOWED:", "ERR_TASK_EXECUTOR"},
		{"unstructured process failure", "Traceback: private-process-detail", "ERR_TASK_EXECUTOR:", "private-process-detail"},
		{"non-domain JSON", `{"error":"executor rejected request"}`, "ERR_TASK_EXECUTOR:", ""},
		{"long JSON error", `{"error":"ERR_STATE_LOST: ` + strings.Repeat("private-detail", 40) + `"}`, "ERR_TASK_EXECUTOR:", "private-detail"},
		{"embedded domain is not a domain refusal", `{"error":"process failed with ERR_WRITE_NOT_ALLOWED"}`, "ERR_TASK_EXECUTOR:", ""},
	} {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			script := filepath.Join(dir, "executor.py")
			require.NoError(t, os.WriteFile(script, []byte("import os,sys\nsys.stderr.write(os.environ['XHS_TEST_EXECUTOR_STDERR'])\nsys.exit(2)\n"), 0600))
			t.Setenv("XHS_OPS_PYTHON", python)
			t.Setenv("XHS_OPS_SCRIPT", script)
			t.Setenv("XHS_OPS_CONFIG", filepath.Join(dir, "unused-config"))
			t.Setenv("XHS_TEST_EXECUTOR_STDERR", tt.stderr)
			_, err := queueBridge(context.Background(), map[string]any{"action": "prepare_direct"})
			require.Error(t, err)
			require.True(t, strings.HasPrefix(err.Error(), tt.prefix), err.Error())
			if tt.absent != "" {
				require.NotContains(t, err.Error(), tt.absent)
			}
		})
	}
}
