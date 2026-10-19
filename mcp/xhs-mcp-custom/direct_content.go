package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
)

// Administrative references travel in context, never in mutationSignature.
type directContentKey struct{}
type directContentRequest struct {
	RunID       string         `json:"run_id,omitempty"`
	ApprovalRef string         `json:"approval_ref,omitempty"`
	Payload     map[string]any `json:"-"`
	Invalid     bool           `json:"-"`
}

func withDirectContentRequest(ctx context.Context, raw []byte) context.Context {
	var request directContentRequest
	if json.Unmarshal(raw, &request) != nil {
		return context.WithValue(ctx, directContentKey{}, directContentRequest{Invalid: true})
	}
	if json.Unmarshal(raw, &request.Payload) != nil {
		return ctx
	}
	delete(request.Payload, "operation_id")
	delete(request.Payload, "run_id")
	delete(request.Payload, "approval_ref")
	return context.WithValue(ctx, directContentKey{}, request)
}

// Keep the original wire fields (including explicit false/empty lists) for the
// existing review fingerprint. The typed browser requests remain unchanged.
func bindDirectContentJSON(c *gin.Context, request any) error {
	if err := c.ShouldBindBodyWithJSON(request); err != nil {
		return err
	}
	if value, ok := c.Get(gin.BodyBytesKey); ok {
		if raw, ok := value.([]byte); ok {
			c.Request = c.Request.WithContext(withDirectContentRequest(c.Request.Context(), raw))
		}
	}
	return nil
}

func directExecutorConfigured() bool {
	return os.Getenv("XHS_OPS_PYTHON") != "" || os.Getenv("XHS_OPS_SCRIPT") != "" || os.Getenv("XHS_OPS_CONFIG") != ""
}

var directErrorCode = regexp.MustCompile(`ERR_[A-Z0-9_]+`)

// Both MCP and HTTP content sends use this wrapper before protectedWrite. The
// local prepare/complete bridge never calls HTTP or starts the queue worker.
func directContentWrite[T any](s *XiaohongshuService, ctx context.Context, tool string, payload any, run func(context.Context, json.RawMessage) (*T, error)) (result *T, err error) {
	request, _ := ctx.Value(directContentKey{}).(directContentRequest)
	if request.Invalid {
		return nil, fmt.Errorf("ERR_DIRECT_METADATA: run_id/approval_ref must be strings")
	}
	if !directExecutorConfigured() {
		if request.RunID != "" || request.ApprovalRef != "" {
			return nil, fmt.Errorf("ERR_EXECUTOR_UNAVAILABLE: run_id/approval_ref require the configured same-host runtime")
		}
		raw, encodeErr := json.Marshal(payload)
		if encodeErr != nil {
			return nil, encodeErr
		}
		return run(ctx, raw)
	}
	if request.Payload == nil {
		raw, encodeErr := json.Marshal(payload)
		if encodeErr != nil {
			return nil, encodeErr
		}
		if decodeErr := json.Unmarshal(raw, &request.Payload); decodeErr != nil {
			return nil, decodeErr
		}
	}
	id, _ := ctx.Value(operationIDKey{}).(string)
	prepareContext, cancelPrepare := context.WithTimeout(ctx, 8*time.Second)
	prepared, prepareErr := queueBridge(prepareContext, map[string]any{"action": "prepare_direct", "tool_name": tool, "payload": request.Payload, "operation_id": id, "run_id": request.RunID, "approval_ref": request.ApprovalRef})
	cancelPrepare()
	if prepareErr != nil {
		return nil, prepareErr
	}
	taskID, _ := prepared["task_id"].(string)
	operationID, _ := prepared["operation_id"].(string)
	if taskID == "" || operationID != taskID {
		return nil, fmt.Errorf("ERR_TASK_PROTOCOL: direct preparation returned no stable operation ID")
	}
	status, _ := prepared["status"].(string)
	receiptOnly, _ := prepared["receipt_only"].(bool)
	managedByQueue, _ := prepared["managed_by_queue"].(bool)
	if receiptOnly || status == "succeeded" || status == "confirmed" || status == "unknown" {
		// A terminal task is never a fresh send authorization. In particular,
		// retention may have removed its sealed files, or the Go journal may be
		// missing. Neither condition permits rebuilding the mutation and sending.
		result, receipt, replayErr := replayDirectReceipt[T](s, operationID, tool)
		if replayErr == nil && status == "unknown" && !managedByQueue {
			// The send succeeded before earlier bookkeeping was interrupted.
			// Import only that original durable receipt; never execute a mutation.
			completeContext, cancelComplete := context.WithTimeout(context.WithoutCancel(ctx), 8*time.Second)
			_, completeErr := queueBridge(completeContext, map[string]any{"action": "complete_direct", "task_id": taskID, "operation_status": receipt})
			cancelComplete()
			if completeErr != nil {
				return nil, fmt.Errorf("ERR_DIRECT_ACCOUNTING: operation_id=%s；原成功回执补账失败，禁止重新发送: %w", operationID, completeErr)
			}
		}
		return result, replayErr
	}
	if deferred, _ := prepared["deferred"].(bool); deferred {
		code, _ := prepared["error_code"].(string)
		if code == "ERR_RATE_LIMIT" {
			retryAt, _ := prepared["retry_at"].(float64)
			if retryAt <= 0 {
				return nil, fmt.Errorf("ERR_TASK_PROTOCOL: deferred task has no original retry deadline")
			}
			return nil, &runtimeError{Code: code, ID: operationID, RetryAt: time.UnixMilli(int64(retryAt * 1000)), Scope: "write"}
		}
		if code == "" {
			code = "ERR_TASK_DEFERRED"
		}
		return nil, fmt.Errorf("%s: operation_id=%s dependency_id=%v dependency_status=%v；沿原任务等待或处理依赖，尚未执行", code, operationID, prepared["dependency_id"], prepared["dependency_status"])
	}
	raw, encodeErr := json.Marshal(prepared["payload"])
	if encodeErr != nil || string(raw) == "null" {
		return nil, fmt.Errorf("ERR_TASK_PROTOCOL: direct preparation returned no reviewed payload")
	}
	ctx = withOperationID(ctx, operationID)
	defer func() {
		if managedByQueue {
			return // The original Python tick owns its running task and lease.
		}
		completion := map[string]any{"action": "complete_direct", "task_id": taskID}
		receipt, receiptErr := s.OperationStatus(operationID)
		if receiptErr == nil {
			completion["operation_status"] = receipt
		} else {
			var notFound *runtimeError
			if !errors.As(receiptErr, &notFound) || notFound.Code != "ERR_OPERATION_NOT_FOUND" {
				completion["error_code"] = "ERR_STATE_READ"
			}
		}
		if err != nil {
			if errors.Is(err, xhserrors.ErrWriteNotAllowed) {
				completion["error_code"] = "ERR_WRITE_NOT_ALLOWED"
			} else if code := directErrorCode.FindString(err.Error()); code != "" {
				completion["error_code"] = code
			} else {
				completion["error_code"] = "ERR_DIRECT_EXECUTION"
			}
			var runtimeErr *runtimeError
			if errors.As(err, &runtimeErr) && !runtimeErr.RetryAt.IsZero() {
				completion["retry_at"] = float64(runtimeErr.RetryAt.UnixMilli()) / 1000
			}
		}
		// Caller cancellation cannot erase the durable receipt or skip bookkeeping.
		completeContext, cancelComplete := context.WithTimeout(context.WithoutCancel(ctx), 8*time.Second)
		_, completeErr := queueBridge(completeContext, completion)
		cancelComplete()
		if completeErr != nil {
			err = fmt.Errorf("ERR_DIRECT_ACCOUNTING: operation_id=%s；仅归账失败，先查询原回执，禁止换编号重发: %w", operationID, completeErr)
		}
	}()
	return run(ctx, raw)
}

func replayDirectReceipt[T any](s *XiaohongshuService, operationID, tool string) (*T, *operationRecord, error) {
	receipt, err := s.OperationStatus(operationID)
	if err != nil {
		return nil, nil, fmt.Errorf("ERR_OPERATION_RECEIPT_REQUIRED: operation_id=%s；原任务仅可查询回执，回执不可用时禁止重新发送: %w", operationID, err)
	}
	if receipt.ID != operationID || receipt.Tool != tool {
		return nil, receipt, fmt.Errorf("ERR_RECEIPT_MISMATCH: operation_id=%s；原回执编号或工具不匹配，禁止重新发送", operationID)
	}
	if receipt.Status != "succeeded" && receipt.Status != "confirmed" {
		return nil, receipt, &runtimeError{Code: "ERR_OPERATION_" + strings.ToUpper(receipt.Status), ID: operationID}
	}
	if len(receipt.Result) == 0 || string(receipt.Result) == "null" {
		return nil, receipt, fmt.Errorf("ERR_OPERATION_RECEIPT_REQUIRED: operation_id=%s；已提交回执没有可重放结果，禁止重新发送", operationID)
	}
	var result T
	if err := json.Unmarshal(receipt.Result, &result); err != nil {
		return nil, receipt, fmt.Errorf("ERR_OPERATION_RECEIPT_REQUIRED: operation_id=%s；原结果不可读取，禁止重新发送: %w", operationID, err)
	}
	return &result, receipt, nil
}
