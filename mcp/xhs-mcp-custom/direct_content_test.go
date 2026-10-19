package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func testDirectBridge(t *testing.T, managed bool) string {
	t.Helper()
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 unavailable")
	}
	dir := t.TempDir()
	log := filepath.Join(dir, "requests.jsonl")
	script := filepath.Join(dir, "bridge.py")
	source := `import json,os,sys
r=json.load(sys.stdin)
with open(os.environ['XHS_TEST_BRIDGE_LOG'],'a') as f: f.write(json.dumps(r)+'\n')
if r['action']=='prepare_direct':
 v={'task_id':'stable-reviewed-task','operation_id':'stable-reviewed-task','payload':r['payload'] if os.environ.get('XHS_TEST_OMIT_REPLAY_PAYLOAD')!='1' else None,'managed_by_queue':os.environ.get('XHS_TEST_QUEUE_MANAGED')=='1','status':os.environ.get('XHS_TEST_DIRECT_STATUS','running'),'receipt_only':os.environ.get('XHS_TEST_RECEIPT_ONLY')=='1'}
 if os.environ.get('XHS_TEST_DIRECT_DEFERRED'): v.update(deferred=True,error_code=os.environ['XHS_TEST_DIRECT_DEFERRED'],retry_at=1900000000.125,dependency_id='original-dependency',dependency_status='queued')
 print(json.dumps(v))
else: print(json.dumps({'task_id':r['task_id'],'status':'recorded'}))
`
	require.NoError(t, os.WriteFile(script, []byte(source), 0600))
	t.Setenv("XHS_OPS_PYTHON", python)
	t.Setenv("XHS_OPS_SCRIPT", script)
	t.Setenv("XHS_OPS_CONFIG", filepath.Join(dir, "not-loaded-config"))
	t.Setenv("XHS_TEST_BRIDGE_LOG", log)
	if managed {
		t.Setenv("XHS_TEST_QUEUE_MANAGED", "1")
	} else {
		t.Setenv("XHS_TEST_QUEUE_MANAGED", "0")
	}
	return log
}

func readDirectBridgeRequests(t *testing.T, path string) []map[string]any {
	t.Helper()
	f, err := os.Open(path)
	require.NoError(t, err)
	defer f.Close()
	decoder := json.NewDecoder(f)
	requests := []map[string]any{}
	for decoder.More() {
		var request map[string]any
		require.NoError(t, decoder.Decode(&request))
		requests = append(requests, request)
	}
	return requests
}

func TestDirectContentPreservesWireReviewAndOriginalReceipt(t *testing.T) {
	s := testRuntime(t)
	log := testDirectBridge(t, false)
	ctx := withDirectContentRequest(context.Background(), []byte(`{"feed_id":"feed","xsec_token":"token","content":"exact reviewed text","is_original":false,"tags":[],"run_id":"reviewed-run","approval_ref":"approved-message"}`))
	calls := 0
	for range 2 {
		result, err := directContentWrite(s, ctx, "post_comment_to_feed", map[string]any{}, func(ctx context.Context, raw json.RawMessage) (*PostCommentResponse, error) {
			var payload map[string]any
			require.NoError(t, json.Unmarshal(raw, &payload))
			require.NotContains(t, payload, "run_id")
			return protectedWrite(s, ctx, "post_comment_to_feed", []string{payload["feed_id"].(string), payload["content"].(string)}, func() (*PostCommentResponse, error) { calls++; return &PostCommentResponse{Success: true}, nil })
		})
		require.NoError(t, err)
		require.True(t, result.Success)
	}
	require.Equal(t, 1, calls, "stable reviewed task must replay its durable receipt")
	requests := readDirectBridgeRequests(t, log)
	require.Len(t, requests, 4)
	prepared := requests[0]
	require.Equal(t, "reviewed-run", prepared["run_id"])
	require.Equal(t, "approved-message", prepared["approval_ref"])
	wire := prepared["payload"].(map[string]any)
	require.Equal(t, false, wire["is_original"])
	require.Equal(t, []any{}, wire["tags"])
	require.NotContains(t, wire, "approval_ref")
	completed := requests[1]["operation_status"].(map[string]any)
	require.Equal(t, "stable-reviewed-task", completed["id"])
	require.Equal(t, "succeeded", completed["status"])
}

func TestDirectContentRunningQueueOwnsCompletion(t *testing.T) {
	s := testRuntime(t)
	log := testDirectBridge(t, true)
	_, err := directContentWrite(s, context.Background(), "post_comment_to_feed", map[string]any{"feed_id": "f", "content": "c"}, func(context.Context, json.RawMessage) (*PostCommentResponse, error) {
		return nil, errors.New("fixture not sent")
	})
	require.Error(t, err)
	require.Len(t, readDirectBridgeRequests(t, log), 1, "queue tick retains its original lease/completion path")
}

func TestDirectTerminalWithoutDurableReceiptNeverDispatches(t *testing.T) {
	for _, status := range []string{"unknown", "succeeded", "confirmed"} {
		t.Run(status, func(t *testing.T) {
			s := testRuntime(t)
			log := testDirectBridge(t, false)
			t.Setenv("XHS_TEST_DIRECT_STATUS", status)
			t.Setenv("XHS_TEST_RECEIPT_ONLY", "1")
			t.Setenv("XHS_TEST_OMIT_REPLAY_PAYLOAD", "1")
			_, err := directContentWrite(s, context.Background(), "publish_content", map[string]any{"images": []string{"/missing/retired-asset.png"}}, func(context.Context, json.RawMessage) (*PublishResponse, error) {
				t.Fatal("terminal task must not enter protectedWrite/browser action")
				return nil, nil
			})
			require.ErrorContains(t, err, "ERR_OPERATION_RECEIPT_REQUIRED")
			require.Len(t, readDirectBridgeRequests(t, log), 1)
		})
	}
}

func TestDirectCompactedPublicationReplaysOriginalStoredResult(t *testing.T) {
	for _, tool := range []string{"publish_content", "publish_with_video"} {
		t.Run(tool, func(t *testing.T) {
			s := testRuntime(t)
			log := testDirectBridge(t, true)
			t.Setenv("XHS_TEST_DIRECT_STATUS", "succeeded")
			t.Setenv("XHS_TEST_RECEIPT_ONLY", "1")
			t.Setenv("XHS_TEST_OMIT_REPLAY_PAYLOAD", "1")
			result := json.RawMessage(`{"title":"原作品","content":"原正文","images":2,"video":"/retired/sealed-original.mp4","status":"发布完成"}`)
			s.state.mu.Lock()
			err := s.state.saveOperation(&operationRecord{ID: "stable-reviewed-task", Tool: tool, Fingerprint: "original-fingerprint-retained", Status: "succeeded", UpdatedAt: time.Now(), Result: result})
			s.state.mu.Unlock()
			require.NoError(t, err)
			value, err := directContentWrite(s, context.Background(), tool, map[string]any{"title": "新请求字段不代替原结果", "images": []string{"/missing/cleaned.png"}, "video": "/missing/cleaned.mp4"}, func(context.Context, json.RawMessage) (*map[string]any, error) {
				t.Fatal("compacted publication must not recalculate mutation signature or execute")
				return nil, nil
			})
			require.NoError(t, err)
			require.Equal(t, "原作品", (*value)["title"])
			require.Equal(t, "/retired/sealed-original.mp4", (*value)["video"])
			require.Len(t, readDirectBridgeRequests(t, log), 1, "already accounted result only reads the original Go journal")
		})
	}
}

func TestDirectUnknownReceiptCannotDispatchAndCanImportOriginalSuccess(t *testing.T) {
	for _, receiptStatus := range []string{"unknown", "succeeded"} {
		t.Run(receiptStatus, func(t *testing.T) {
			s := testRuntime(t)
			log := testDirectBridge(t, false)
			t.Setenv("XHS_TEST_DIRECT_STATUS", "unknown")
			t.Setenv("XHS_TEST_RECEIPT_ONLY", "1")
			s.state.mu.Lock()
			err := s.state.saveOperation(&operationRecord{ID: "stable-reviewed-task", Tool: "post_comment_to_feed", Fingerprint: "original", Status: receiptStatus, UpdatedAt: time.Now(), Result: json.RawMessage(`{"feed_id":"original-feed","success":true}`)})
			s.state.mu.Unlock()
			require.NoError(t, err)
			value, err := directContentWrite(s, context.Background(), "post_comment_to_feed", map[string]any{"feed_id": "original-feed"}, func(context.Context, json.RawMessage) (*PostCommentResponse, error) {
				t.Fatal("unknown task must never execute again")
				return nil, nil
			})
			if receiptStatus == "unknown" {
				require.ErrorContains(t, err, "ERR_OPERATION_UNKNOWN")
				require.Len(t, readDirectBridgeRequests(t, log), 1)
			} else {
				require.NoError(t, err)
				require.True(t, value.Success)
				requests := readDirectBridgeRequests(t, log)
				require.Len(t, requests, 2)
				require.Equal(t, "complete_direct", requests[1]["action"])
			}
		})
	}
}

func TestDirectDeferredTaskKeepsOriginalDeadlineAndDoesNotDispatch(t *testing.T) {
	for _, code := range []string{"ERR_RATE_LIMIT", "ERR_DEPENDENCY_PENDING", "ERR_DEPENDENCY_FAILED"} {
		t.Run(code, func(t *testing.T) {
			s := testRuntime(t)
			log := testDirectBridge(t, false)
			t.Setenv("XHS_TEST_DIRECT_DEFERRED", code)
			_, err := directContentWrite(s, context.Background(), "post_comment_to_feed", map[string]any{"feed_id": "f", "content": "c"}, func(context.Context, json.RawMessage) (*PostCommentResponse, error) {
				t.Fatal("deferred task must not execute or complete")
				return nil, nil
			})
			require.ErrorContains(t, err, code)
			if code == "ERR_RATE_LIMIT" {
				var runtimeErr *runtimeError
				require.ErrorAs(t, err, &runtimeErr)
				require.Equal(t, time.UnixMilli(1900000000125), runtimeErr.RetryAt)
				require.Equal(t, "stable-reviewed-task", runtimeErr.ID)
			}
			require.Len(t, readDirectBridgeRequests(t, log), 1)
		})
	}
}

func TestDirectContentStandaloneRejectsAdministrativeReferences(t *testing.T) {
	t.Setenv("XHS_OPS_PYTHON", "")
	t.Setenv("XHS_OPS_SCRIPT", "")
	t.Setenv("XHS_OPS_CONFIG", "")
	ctx := withDirectContentRequest(context.Background(), []byte(`{"run_id":"must-not-be-ignored","content":"c"}`))
	_, err := directContentWrite(&XiaohongshuService{}, ctx, "post_comment_to_feed", map[string]any{}, func(context.Context, json.RawMessage) (*PostCommentResponse, error) {
		t.Fatal("must not dispatch")
		return nil, nil
	})
	require.ErrorContains(t, err, "ERR_EXECUTOR_UNAVAILABLE")
	value, err := directContentWrite(&XiaohongshuService{}, context.Background(), "post_comment_to_feed", map[string]any{"content": "original"}, func(context.Context, json.RawMessage) (*PostCommentResponse, error) {
		return &PostCommentResponse{Success: true}, nil
	})
	require.NoError(t, err)
	require.True(t, value.Success)
	invalid := withDirectContentRequest(context.Background(), []byte(`{"run_id":123,"content":"c"}`))
	_, err = directContentWrite(&XiaohongshuService{}, invalid, "post_comment_to_feed", map[string]any{}, func(context.Context, json.RawMessage) (*PostCommentResponse, error) {
		t.Fatal("invalid metadata must not silently dispatch")
		return nil, nil
	})
	require.ErrorContains(t, err, "ERR_DIRECT_METADATA")
}

func TestDirectReceiptFingerprintVectors(t *testing.T) {
	cases := []struct {
		tool    string
		payload any
		want    string
	}{
		{"post_comment_to_feed", []string{"feed-1", "准确稿 <&>\u2028"}, "17f60c29a79d725a9db0b6d0ccdee4f76fa47f1611a2c786f640526facb0eac6"},
		{"reply_comment_in_feed", []string{"feed-1", "comment-2", "user-3", "回复&文本"}, "73b1fb8bda2ef666e6b9b2f2176c457e60c23450eb0fbb7c3f543c1f0ac789ec"},
		{"publish_content", &PublishRequest{Title: "标题", Content: "正文<&>", Images: []string{"https://fixture.invalid/one.png?x=1&y=2"}, Tags: []string{"求职", "面试"}, ScheduleAt: "2030-10-20T10:00:00+08:00", IsOriginal: true, Visibility: "仅自己可见", Products: []string{"fixture-product"}, Activity: "fixture-activity", LongArticleTemplate: "fixture-long", TopicTemplate: "fixture-topic"}, "b84fa30e8dc8a3afbdc4733932686ae26c9f8c9fd31e35f60f0541bfcd405f96"},
		{"publish_with_video", &PublishVideoRequest{Title: "视频标题", Content: "正文", Video: "https://fixture.invalid/clip.mp4", Tags: []string{"面试"}, ScheduleAt: "2030-10-20T12:00:00+08:00", IsOriginal: true, Visibility: "公开可见", Products: []string{"fixture-product"}, Activity: "fixture-activity"}, "b50bf01a3fcdab278277ad1155b7bf7cc2a63c2e440d86792864d0734f4a33ad"},
	}
	for _, tt := range cases {
		t.Run(tt.tool, func(t *testing.T) {
			encoded, err := mutationSignature(tt.payload)
			require.NoError(t, err)
			hash := sha256.Sum256(append([]byte(tt.tool+"\n"), encoded...))
			got := hex.EncodeToString(hash[:])
			require.Equal(t, tt.want, got)
		})
	}
}

func TestDirectContentHTTPAndMCPSharePreparation(t *testing.T) {
	s := testRuntime(t)
	log := testDirectBridge(t, false)
	t.Setenv("XHS_ALLOW_WRITE", "0") // Exercise admission/accounting without any browser action.
	router := setupRoutes(NewAppServer(s, ""))
	body := `{"feed_id":"f","xsec_token":"token","content":"reviewed","run_id":"run-http-mcp","approval_ref":"approved"}`
	for _, path := range []string{"/api/v1/feeds/comment", "/mcp"} {
		requestBody := body
		if path == "/mcp" {
			requestBody = `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"post_comment_to_feed","arguments":` + body + `}}`
		}
		request := httptest.NewRequest(http.MethodPost, path, strings.NewReader(requestBody))
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Accept", "application/json, text/event-stream")
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		require.NotEqual(t, http.StatusNotFound, response.Code)
	}
	requests := readDirectBridgeRequests(t, log)
	require.Len(t, requests, 4)
	for _, i := range []int{0, 2} {
		require.Equal(t, "prepare_direct", requests[i]["action"])
		require.Equal(t, "run-http-mcp", requests[i]["run_id"])
		require.Equal(t, "approved", requests[i]["approval_ref"])
		require.NotContains(t, requests[i]["payload"], "approval_ref")
		require.Equal(t, "ERR_WRITE_NOT_ALLOWED", requests[i+1]["error_code"], "disabled writes are known not submitted, not unknown")
	}
}

func TestRuntimeDiagnosticsRemainAvailableWithoutQueueExecutor(t *testing.T) {
	t.Setenv("XHS_OPS_PYTHON", "")
	t.Setenv("XHS_OPS_SCRIPT", "")
	t.Setenv("XHS_OPS_CONFIG", "")
	router := setupRoutes(NewAppServer(NewXiaohongshuService(), ""))
	request := httptest.NewRequest(http.MethodPost, "/mcp", strings.NewReader(`{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_runtime_status","arguments":{}}}`))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json, text/event-stream")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	require.Equal(t, http.StatusOK, response.Code)
	var result struct {
		Result struct {
			IsError bool `json:"isError"`
			Content []struct {
				Text string `json:"text"`
			} `json:"content"`
		} `json:"result"`
	}
	require.NoError(t, json.Unmarshal(response.Body.Bytes(), &result))
	require.False(t, result.Result.IsError)
	require.NotEmpty(t, result.Result.Content)
	var snapshot map[string]any
	require.NoError(t, json.Unmarshal([]byte(result.Result.Content[0].Text), &snapshot))
	require.Equal(t, false, snapshot["queue_available"])
	require.Contains(t, snapshot, "execution")
	require.Contains(t, snapshot, "identity")
	require.Contains(t, snapshot, "request_pacing")
}
