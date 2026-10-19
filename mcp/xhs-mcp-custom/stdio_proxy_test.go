package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/stretchr/testify/require"
)

func TestProxyDoesNotReplayLostToolResult(t *testing.T) {
	upstream := mcp.NewServer(&mcp.Implementation{Name: "fixture", Version: "1"}, nil)
	handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return upstream }, &mcp.StreamableHTTPOptions{Stateless: true})
	var dispatched atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			raw, _ := io.ReadAll(r.Body)
			var message struct {
				Method string `json:"method"`
			}
			_ = json.Unmarshal(raw, &message)
			r.Body = io.NopCloser(strings.NewReader(string(raw)))
			if message.Method == "tools/call" {
				dispatched.Add(1) // 模拟上游已收到提交，但回包丢失。
				connection, _, err := w.(http.Hijacker).Hijack()
				require.NoError(t, err)
				connection.Close()
				return
			}
		}
		handler.ServeHTTP(w, r)
	}))
	defer server.Close()
	result, err := forwardProxyTool(context.Background(), server.URL, "fixture-write", &mcp.CallToolParamsRaw{Arguments: json.RawMessage(`{}`)}, func(context.Context) error { return nil })
	require.NoError(t, err)
	require.True(t, result.IsError)
	require.Contains(t, result.Content[0].(*mcp.TextContent).Text, "ERR_OPERATION_UNKNOWN")
	require.EqualValues(t, 1, dispatched.Load())
}

func TestProxyPreparationFailureDoesNotDispatch(t *testing.T) {
	result, err := forwardProxyTool(context.Background(), "http://127.0.0.1:1", "fixture-write", &mcp.CallToolParamsRaw{}, func(context.Context) error { return io.EOF })
	require.NoError(t, err)
	require.True(t, result.IsError)
	require.Contains(t, result.Content[0].(*mcp.TextContent).Text, "ERR_MCP_NOT_DISPATCHED")
}
