package main

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
)

func TestUnavailableBrowserIsExpectedToolError(t *testing.T) {
	handler := withPanicRecovery("check_login_status", func(context.Context, *mcp.CallToolRequest, any) (*mcp.CallToolResult, any, error) {
		panic(&browser.UnavailableError{Cause: fmt.Errorf("private connection detail")})
	})
	result, response, err := handler(context.Background(), &mcp.CallToolRequest{Params: &mcp.CallToolParamsRaw{}}, nil)
	if err != nil || response != nil || !result.IsError || len(result.Content) != 1 {
		t.Fatalf("expected a normal tool error, got %#v %v", result, err)
	}
	text := result.Content[0].(*mcp.TextContent).Text
	if !strings.HasPrefix(text, "ERR_BROWSER_UNAVAILABLE:") || strings.Contains(text, "private connection detail") || strings.Contains(text, "box-chrome") || strings.Contains(text, "XHS_LAUNCH_BROWSER") {
		t.Fatalf("unexpected browser recovery guidance: %s", text)
	}
}
