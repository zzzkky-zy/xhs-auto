package main

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

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
		{"domain detail redacted", `{"error":"ERR_WRITE_NOT_ALLOWED: /private/account/config.json token=NEVER_EXPORT"}`, "ERR_WRITE_NOT_ALLOWED:", "NEVER_EXPORT"},
		{"domain refusal", `{"error":"ERR_WRITE_NOT_ALLOWED: writes disabled"}`, "ERR_WRITE_NOT_ALLOWED:", "ERR_TASK_EXECUTOR"},
		{"unstructured process failure", "Traceback: private-process-detail", "ERR_TASK_EXECUTOR_FAILED:", "private-process-detail"},
		{"non-domain JSON", `{"error":"executor rejected request"}`, "ERR_TASK_EXECUTOR_FAILED:", ""},
		{"long JSON error", `{"error":"ERR_STATE_LOST: ` + strings.Repeat("private-detail", 40) + `"}`, "ERR_TASK_EXECUTOR_FAILED:", "private-detail"},
		{"embedded domain is not a domain refusal", `{"error":"process failed with ERR_WRITE_NOT_ALLOWED"}`, "ERR_TASK_EXECUTOR_FAILED:", ""},
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

// controlledBridgeContext separates executor startup from termination so a
// slow CI host cannot consume the deadline before the fixture has started.
type controlledBridgeContext struct {
	context.Context
	done    chan struct{}
	failure error
}

func (c controlledBridgeContext) Done() <-chan struct{} { return c.done }
func (c controlledBridgeContext) Err() error {
	select {
	case <-c.done:
		return c.failure
	default:
		return nil
	}
}

// Fake executors only: no account, browser or real service is contacted.
func TestQueueBridgeClassifiesProcessFailuresWithoutLeakingDetails(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 unavailable")
	}
	for _, kind := range []string{"deadline", "cancelled", "unavailable", "failed"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			script := filepath.Join(dir, "private-executor.py")
			attempts := filepath.Join(dir, "attempts")
			// A valid domain error must not override context termination. Both
			// the domain detail and unstructured failure contain private data.
			body := "import os,sys,time\n"
			if kind == "deadline" || kind == "cancelled" {
				body += "sys.stderr.write('{\"error\":\"ERR_WRITE_NOT_ALLOWED: token=NEVER_EXPORT /private/secret --credential\"}')\n"
			} else {
				body += "sys.stderr.write('private-token=NEVER_EXPORT /private/secret command --credential')\n"
			}
			body += "sys.stderr.flush()\nwith open(os.environ['XHS_TEST_ATTEMPTS'],'a') as f: f.write('x')\n"
			if kind == "deadline" || kind == "cancelled" {
				body += "time.sleep(30)\n"
			} else {
				body += "sys.exit(7)\n"
			}
			require.NoError(t, os.WriteFile(script, []byte(body), 0600))
			t.Setenv("XHS_OPS_PYTHON", python)
			t.Setenv("XHS_OPS_SCRIPT", script)
			t.Setenv("XHS_OPS_CONFIG", filepath.Join(dir, "private-config"))
			t.Setenv("XHS_TEST_ATTEMPTS", attempts)
			ctx := context.Background()
			want := "ERR_TASK_EXECUTOR_FAILED:"
			var finish func()
			switch kind {
			case "deadline", "cancelled":
				failure := context.DeadlineExceeded
				want = "ERR_TASK_EXECUTOR_DEADLINE:"
				if kind == "cancelled" {
					failure = context.Canceled
					want = "ERR_TASK_EXECUTOR_CANCELLED:"
				}
				done := make(chan struct{})
				var once sync.Once
				finish = func() { once.Do(func() { close(done) }) }
				defer finish()
				ctx = controlledBridgeContext{Context: context.Background(), done: done, failure: failure}
			case "unavailable":
				t.Setenv("XHS_OPS_PYTHON", filepath.Join(dir, "missing-private-python"))
				want = "ERR_TASK_EXECUTOR_UNAVAILABLE:"
			}
			results := make(chan error, 1)
			go func() {
				_, failure := queueBridge(ctx, map[string]any{"action": "prepare_direct"})
				results <- failure
			}()
			if finish != nil {
				// Only this startup diagnostic has a wall clock budget. Actual
				// cancellation happens after stderr flush and the started marker.
				until := time.Now().Add(10 * time.Second)
				for {
					if data, readErr := os.ReadFile(attempts); readErr == nil && string(data) == "x" {
						break
					}
					require.True(t, time.Now().Before(until), "synthetic executor did not signal startup")
					time.Sleep(10 * time.Millisecond)
				}
				finish()
			}
			var result error
			select {
			case result = <-results:
			case <-time.After(10 * time.Second):
				t.Fatal("synthetic executor termination not observed")
			}
			require.Error(t, result)
			require.True(t, strings.HasPrefix(result.Error(), want), result.Error())
			for _, secret := range []string{dir, "NEVER_EXPORT", "--credential", "/private/secret"} {
				require.NotContains(t, result.Error(), secret)
			}
			if kind != "unavailable" {
				data, readErr := os.ReadFile(attempts)
				require.NoError(t, readErr)
				require.Equal(t, "x", string(data), "bridge must not retry a failed or timed-out executor")
				require.Contains(t, result.Error(), "outcome unconfirmed")
			}
		})
	}
}
