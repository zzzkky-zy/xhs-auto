package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"sync"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/sirupsen/logrus"
)

// The MCP service owns this finite child. Shutdown cancels it; the queue and
// journal remain authoritative after a restart. No cron, browser or credentials
// are created by these tools.
type queueRunner struct {
	once   sync.Once
	wake   chan struct{}
	cancel context.CancelFunc
	done   chan struct{}
	mu     sync.Mutex
	errors int
	halted bool
}

func (r *queueRunner) failure() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.errors++
	r.halted = r.errors >= 3
}
func (r *queueRunner) healthy() { r.mu.Lock(); r.errors = 0; r.halted = false; r.mu.Unlock() }
func (r *queueRunner) Snapshot() map[string]any {
	r.mu.Lock()
	defer r.mu.Unlock()
	return map[string]any{"halted_after_executor_errors": r.halted, "consecutive_executor_errors": r.errors, "error_limit": 3}
}

func queueBridge(ctx context.Context, request any) (map[string]any, error) {
	python, script, config := os.Getenv("XHS_OPS_PYTHON"), os.Getenv("XHS_OPS_SCRIPT"), os.Getenv("XHS_OPS_CONFIG")
	if python == "" || script == "" || config == "" {
		return nil, fmt.Errorf("ERR_EXECUTOR_UNAVAILABLE: task tools require the configured same-host runtime")
	}
	data, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	cmd := exec.CommandContext(ctx, python, script, "--config", config, "bridge")
	cmd.Stdin = bytes.NewReader(data)
	var out, stderr bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		// Cancellation/deadline describes the bridge, not the platform outcome.
		// The executor may already have dispatched an action. Preserve its
		// durable receipt/unknown state; classification never authorizes retry.
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, errors.New("ERR_TASK_EXECUTOR_DEADLINE: executor deadline exceeded; outcome unconfirmed; inspect private task state")
		}
		if errors.Is(ctx.Err(), context.Canceled) {
			return nil, errors.New("ERR_TASK_EXECUTOR_CANCELLED: executor cancelled; outcome unconfirmed; inspect private task state")
		}
		var launchError *exec.Error
		var pathError *os.PathError
		if errors.As(err, &launchError) || errors.As(err, &pathError) {
			return nil, errors.New("ERR_TASK_EXECUTOR_UNAVAILABLE: executor could not be launched; inspect private configuration")
		}
		message := safeBridgeError(stderr.Bytes())
		if bridgeDomainError.MatchString(message) {
			// Keep the executor's safe domain code first: MCP callers use it
			// to distinguish authorization/quality refusal from process failure.
			return nil, errors.New(message)
		}
		return nil, errors.New("ERR_TASK_EXECUTOR_FAILED: executor failed; outcome unconfirmed; inspect private task state")
	}
	var value map[string]any
	if err := json.Unmarshal(out.Bytes(), &value); err != nil {
		return nil, fmt.Errorf("ERR_TASK_PROTOCOL: invalid executor response")
	}
	return value, nil
}

var bridgeDomainError = regexp.MustCompile(`^(ERR_[A-Z0-9_]+)(?:$|:|\s|；)`)

func safeBridgeError(data []byte) string {
	var value struct {
		Error string `json:"error"`
	}
	if json.Unmarshal(data, &value) == nil && value.Error != "" {
		if len(value.Error) > 350 {
			return "executor rejected request; inspect private task state"
		}
		if match := bridgeDomainError.FindStringSubmatch(value.Error); match != nil {
			// Only the existing machine-code grammar is public. Even a short
			// structured error can contain a token, command or private path.
			return match[1] + ": executor rejected request; inspect private task state"
		}
	}
	return "executor unavailable; inspect private state and configuration"
}
func (r *queueRunner) Start() {
	r.StartAfter(time.Second)
}

func (r *queueRunner) StartAfter(initialDelay time.Duration) {
	r.once.Do(func() {
		ctx, cancel := context.WithCancel(context.Background())
		r.cancel = cancel
		r.wake = make(chan struct{}, 1)
		r.done = make(chan struct{})
		go func() {
			defer close(r.done)
			delay := initialDelay
			for {
				r.mu.Lock()
				halted := r.halted
				r.mu.Unlock()
				if halted {
					select {
					case <-ctx.Done():
						return
					case <-r.wake:
						r.healthy()
						delay = time.Millisecond
					}
				}
				timer := time.NewTimer(delay)
				select {
				case <-ctx.Done():
					timer.Stop()
					return
				case <-r.wake:
					timer.Stop()
				case <-timer.C:
				}
				if ctx.Err() != nil {
					return
				}
				work, cancelWork := context.WithTimeout(ctx, 5*time.Minute)
				status, err := queueBridge(work, map[string]any{"action": "advance"})
				cancelWork()
				delay = 30 * time.Second
				if err != nil {
					r.failure()
					logrus.Warn("ERR_QUEUE_ADVANCE: bounded executor stopped; queue/lease retained")
					continue
				}
				r.healthy()
				delay = queueNextDelay(status, time.Now())
			}
		}()
	})
}

// New tasks/control changes wake the worker immediately. While idle, only the
// hourly local maintenance deadline wakes Python; future tasks keep their exact
// persisted deadline instead of causing two process launches every 30 seconds.
func queueNextDelay(status map[string]any, now time.Time) time.Duration {
	delay := time.Hour
	if next, ok := status["background_next_at"].(float64); ok {
		delay = time.UnixMilli(int64(next * 1000)).Sub(now)
	}
	if delay < time.Second {
		return time.Second
	}
	if delay > time.Hour {
		return time.Hour
	}
	return delay
}
func (r *queueRunner) Wake() {
	r.Start()
	r.healthy()
	select {
	case r.wake <- struct{}{}:
	default:
	}
}
func (r *queueRunner) Stop() {
	if r.cancel == nil {
		return
	}
	r.cancel()
	select {
	case <-r.done:
	case <-time.After(3 * time.Second):
	}
}

type submitTaskArgs struct {
	Tool        string         `json:"tool" jsonschema:"现有队列支持的读取/评论/发布工具名"`
	Payload     map[string]any `json:"payload" jsonschema:"该工具的原始参数对象；不要包含密码或登录凭据"`
	ApprovalRef string         `json:"approval_ref,omitempty" jsonschema:"真实发送必须提供本轮已有明确授权的记录引用；填字符串本身不代表用户授权"`
	NotBefore   float64        `json:"not_before,omitempty" jsonschema:"可选 Unix 秒；未到时间不会执行"`
	Dependency  string         `json:"dependency,omitempty"`
	RunID       string         `json:"run_id,omitempty" jsonschema:"可选持续运营批次编号；验收后沿用该批次的持续授权与内部审查"`
	AutoRun     *bool          `json:"auto_run,omitempty" jsonschema:"默认将本任务纳入后台队列；false只保存。旧版/手工入队任务不因升级自动执行"`
}
type taskStatusArgs struct {
	TaskID        string `json:"task_id"`
	IncludeResult bool   `json:"include_result,omitempty"`
}
type queueControlArgs struct {
	Enabled bool `json:"enabled" jsonschema:"启用或暂停领取新任务；已运行任务按其期限结束"`
}
type resumeTaskArgs struct {
	TaskID      string `json:"task_id"`
	Evidence    string `json:"evidence" jsonschema:"实际处理完成的证据；不能凭猜测恢复未知发送"`
	ApprovalRef string `json:"approval_ref,omitempty"`
}

type reconcileTaskArgs struct {
	TaskID string `json:"task_id" jsonschema:"原任务编号；仅查询同编号的持久提交回执并归账，不重新发送"`
}

type operationRunArgs struct {
	Operation string         `json:"operation" jsonschema:"configure/begin/update/cancel/schedule/review/finish/accept/maintain/status/repair；accept仅记录真实用户对完整首批的验收"`
	Data      map[string]any `json:"data,omitempty" jsonschema:"私有批次参数，字段见随包持续运营说明"`
}

func registerQueueTools(server *mcp.Server, app *AppServer) {
	call := func(ctx context.Context, request any) (*mcp.CallToolResult, any, error) {
		ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
		defer cancel()
		v, e := queueBridge(ctx, request)
		return runtimeToolResult(v, e)
	}
	mcp.AddTool(server, &mcp.Tool{Name: "manage_operation_run", Description: "保存一次配置与首次人工验收，后续批次由agent自审；持久随机预约时间，完成后清理自有临时文件并保留防重发记录。不生成内容、不创建宿主日程、不登录。"}, func(ctx context.Context, req *mcp.CallToolRequest, a operationRunArgs) (*mcp.CallToolResult, any, error) {
		r, x, e := call(ctx, map[string]any{"action": "operation_run", "operation": a.Operation, "data": a.Data})
		if e == nil && r != nil && !r.IsError && (a.Operation == "finish" || a.Operation == "maintain" || a.Operation == "accept") {
			app.xiaohongshuService.readCache.Clear()
			app.xiaohongshuService.detailCache.Clear()
			app.xiaohongshuService.searchCache.Clear()
		}
		return r, x, e
	})
	mcp.AddTool(server, &mcp.Tool{Name: "submit_task", Description: "持久保存任务并快速返回编号，默认交由同机后台执行；不会在本次工具调用里等待网页操作。重复内容返回原任务；真实发送仍需已有授权及写开关。"}, func(ctx context.Context, req *mcp.CallToolRequest, a submitTaskArgs) (*mcp.CallToolResult, any, error) {
		auto := a.AutoRun == nil || *a.AutoRun
		result, extra, err := call(ctx, map[string]any{"action": "submit", "tool": a.Tool, "payload": a.Payload, "approval_ref": a.ApprovalRef, "not_before": a.NotBefore, "dependency": a.Dependency, "run_id": a.RunID, "auto_run": auto})
		if err == nil && result != nil && !result.IsError && auto {
			app.queue.Wake()
		}
		return result, extra, err
	})
	mcp.AddTool(server, &mcp.Tool{Name: "get_task_status", Description: "只读获取任务状态、执行阶段、耗时和可选结果，不打开网页、不重发。", Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true}}, func(ctx context.Context, req *mcp.CallToolRequest, a taskStatusArgs) (*mcp.CallToolResult, any, error) {
		return call(ctx, map[string]any{"action": "task", "task_id": a.TaskID, "include_result": a.IncludeResult})
	})
	mcp.AddTool(server, &mcp.Tool{Name: "reconcile_task", Description: "仅查询原编号的持久Go提交回执并归账任务；不导航、不发送、不启用队列。新手动一级评论须编号、工具、准确内容指纹及目标一致；无记录或未确认成功不能记成功，未知结果继续保留。"}, func(ctx context.Context, req *mcp.CallToolRequest, a reconcileTaskArgs) (*mcp.CallToolResult, any, error) {
		return call(ctx, map[string]any{"action": "reconcile", "task_id": a.TaskID})
	})
	mcp.AddTool(server, &mcp.Tool{Name: "control_task_queue", Description: "启用或暂停已有账号队列；启用只执行已明确纳入后台且获准的任务，暂停不取消正在运行的网页动作。"}, func(ctx context.Context, req *mcp.CallToolRequest, a queueControlArgs) (*mcp.CallToolResult, any, error) {
		r, x, e := call(ctx, map[string]any{"action": "control", "enabled": a.Enabled})
		if e == nil && r != nil && !r.IsError {
			app.queue.Wake()
		}
		return r, x, e
	})
	mcp.AddTool(server, &mcp.Tool{Name: "resume_task", Description: "用户处理验证/权限等问题后，凭实际证据恢复同一任务；未知发送只可先查记录，不允许用此入口强行重发。"}, func(ctx context.Context, req *mcp.CallToolRequest, a resumeTaskArgs) (*mcp.CallToolResult, any, error) {
		r, x, e := call(ctx, map[string]any{"action": "resume", "task_id": a.TaskID, "evidence": a.Evidence, "approval_ref": a.ApprovalRef})
		if e == nil && r != nil && !r.IsError {
			app.queue.Wake()
		}
		return r, x, e
	})
	mcp.AddTool(server, &mcp.Tool{Name: "get_runtime_status", Description: "只读诊断：队列、阶段进度、最近耗时及计数、登录检查是否新鲜。不会导航、登录或发送；服务可用不等于账号已登录。", Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true}}, withPanicRecovery("get_runtime_status", func(ctx context.Context, req *mcp.CallToolRequest, a struct{}) (*mcp.CallToolResult, any, error) {
		ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
		defer cancel()
		v, e := queueBridge(ctx, map[string]any{"action": "status"})
		if e != nil {
			// Queue availability must not hide the browser/journal diagnostics.
			v = map[string]any{"queue_available": false, "queue": map[string]any{"available": false, "error": e.Error()}}
		} else if _, present := v["queue_available"]; !present {
			v["queue_available"] = true
		}
		v["execution"] = app.xiaohongshuService.executionSnapshot()
		v["identity"] = app.xiaohongshuService.identitySnapshot()
		v["request_pacing"] = app.xiaohongshuService.requestPacingSnapshot()
		app.xiaohongshuService.limits.mu.Lock()
		v["platform_rate_limits"] = map[string]any{"search_until": app.xiaohongshuService.limits.searchUntil, "identity_until": app.xiaohongshuService.limits.identityUntil}
		app.xiaohongshuService.limits.mu.Unlock()
		v["queue_worker"] = app.queue.Snapshot()
		return runtimeToolResult(v, nil)
	}))
}
