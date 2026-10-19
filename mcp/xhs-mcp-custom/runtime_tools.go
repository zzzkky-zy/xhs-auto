package main

import (
	"context"
	"encoding/json"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type operationStatusArgs struct {
	OperationID string `json:"operation_id" jsonschema:"稳定提交编号"`
}
type resolveOperationArgs struct {
	OperationID string `json:"operation_id"`
	Status      string `json:"status" jsonschema:"confirmed 或 not_applied；需人工实际核对平台结果"`
	Evidence    string `json:"evidence" jsonschema:"核对的链接或创作中心状态、核对时间与说明，不得推测"`
}

func runtimeToolResult(value any, err error) (*mcp.CallToolResult, any, error) {
	if err != nil {
		return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: err.Error()}}}, nil, nil
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, nil, err
	}
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(raw)}}}, nil, nil
}

func registerRuntimeTools(server *mcp.Server, app *AppServer) {
	registerQueueTools(server, app)
	mcp.AddTool(server, &mcp.Tool{Name: "get_operation_status", Description: "只读查询持久提交记录，不导航、不重发；找不到不等于平台未提交", Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true}}, func(ctx context.Context, req *mcp.CallToolRequest, args operationStatusArgs) (*mcp.CallToolResult, any, error) {
		value, err := app.xiaohongshuService.OperationStatus(args.OperationID)
		return runtimeToolResult(value, err)
	})
	mcp.AddTool(server, &mcp.Tool{Name: "resolve_operation", Description: "人工核对 unknown 结果后记录结论；默认禁用，需 XHS_ALLOW_RECONCILE=1。没有平台证据不得把未知标为未提交", Annotations: &mcp.ToolAnnotations{DestructiveHint: boolPtr(true)}}, func(ctx context.Context, req *mcp.CallToolRequest, args resolveOperationArgs) (*mcp.CallToolResult, any, error) {
		value, err := app.xiaohongshuService.ResolveOperation(args.OperationID, args.Status, args.Evidence)
		return runtimeToolResult(value, err)
	})
}
