package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/proto"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

// Pages belong to one action, as in the original browser flow. There is no
// reusable page, input lease, freeze/thaw or idle-page lifecycle. Only possibly
// submitted writes need a live review target; proven pre-submit failures save
// bounded diagnostics and drafts before their owned target is released.
type taskPages struct {
	preserved   []proto.TargetTargetID
	unsubmitted []unsubmittedTaskPage
}

type unsubmittedTaskPage struct {
	page *rod.Page
	ctx  context.Context
}

func (s *XiaohongshuService) releaseExpiredResources(_ context.Context) {
	s.readCache.PruneExpired(time.Now())
	s.detailCache.PruneExpired(time.Now())
	s.searchCache.PruneExpired(time.Now())
}

// The caller holds browserMu. Only targets created by this service are tracked;
// user tabs and preserved targets from earlier processes are never enumerated.
func withTaskPage[T any](s *XiaohongshuService, ctx context.Context, write bool, fn func(*rod.Page) (*T, error)) (result *T, err error) {
	b := newBrowser()
	defer b.Close()
	if write && !s.identityGenerationCurrent() {
		return nil, &runtimeError{Code: "ERR_LOGIN_STATE_UNKNOWN"}
	}
	if write && b.Attached() {
		// An evidence/storage failure is separate from uncertain submission.
		// Retry saving/closing that proven pre-submit target before opening more.
		remainingUnsubmitted := s.pages.unsubmitted[:0]
		for _, held := range s.pages.unsubmitted {
			_, lookupErr := (proto.TargetGetTargetInfo{TargetID: held.page.TargetID}).Call(b.Rod().Context(ctx).Timeout(2 * time.Second))
			if lookupErr != nil && strings.Contains(strings.ToLower(lookupErr.Error()), "no target") {
				continue // Explicit target absence is proof; a disconnected browser is not.
			}
			if saveUnsubmittedPageDiagnostic(held.page, held.ctx) == nil {
				cleanup, cancel := context.WithTimeout(context.Background(), 3*time.Second)
				closeErr := held.page.Context(cleanup).Close()
				cancel()
				if closeErr == nil {
					continue
				}
			}
			remainingUnsubmitted = append(remainingUnsubmitted, held)
		}
		s.pages.unsubmitted = remainingUnsubmitted
		if len(remainingUnsubmitted) > 0 {
			return nil, fmt.Errorf("ERR_PAGE_DIAGNOSTIC_SAVE: 明确未提交的自有页尚未完成草稿诊断保存或关闭；保留现场，恢复存储或浏览器连接后重试")
		}
		remaining := s.pages.preserved[:0]
		for _, id := range s.pages.preserved {
			_, lookupErr := (proto.TargetGetTargetInfo{TargetID: id}).Call(b.Rod().Context(ctx).Timeout(2 * time.Second))
			if lookupErr == nil || !strings.Contains(strings.ToLower(lookupErr.Error()), "no target") {
				remaining = append(remaining, id)
			}
		}
		s.pages.preserved = remaining
		if len(remaining) >= 2 {
			return nil, fmt.Errorf("ERR_WRITE_PAGE_REVIEW_REQUIRED (alias=ERR_RESEARCH_PAGE_REVIEW_REQUIRED): 两个可能已提交的自有页面仍需核对；未创建新写入页")
		}
	}
	page := b.NewPageContext(ctx)
	execution.Count("task_pages_created")
	defer func() {
		failure := recover()
		if write && (failure != nil || err != nil || ctx.Err() != nil) {
			var commentErr *xiaohongshu.CommentActionError
			notSubmitted := execution.NotDispatched(ctx) || (errors.As(err, &commentErr) && commentErr.SubmissionNotAttempted())
			if notSubmitted && saveUnsubmittedPageDiagnostic(page, ctx) == nil {
				cleanup, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				closeErr := page.Context(cleanup).Close()
				cancel()
				if closeErr == nil {
					execution.Count("write_pages_closed_before_submit")
					if failure != nil {
						panic(failure)
					}
					return
				}
			}
			if notSubmitted {
				s.pages.unsubmitted = append(s.pages.unsubmitted, unsubmittedTaskPage{page, ctx})
				execution.Count("write_pages_waiting_diagnostic")
				if failure != nil {
					panic(failure)
				}
				return
			}
			// Browser loss is never proof that a target closed. Failed evidence
			// capture/close also retains the target rather than losing its draft.
			s.pages.preserved = append(s.pages.preserved, page.TargetID)
			execution.Count("write_pages_preserved")
		} else {
			closeTaskPage(page)
			execution.Count("task_pages_closed")
		}
		if failure != nil {
			panic(failure)
		}
	}()
	return fn(page)
}

type unsubmittedPageDiagnostic struct {
	TargetID    proto.TargetTargetID `json:"target_id"`
	OperationID string               `json:"operation_id,omitempty"`
	CapturedAt  time.Time            `json:"captured_at"`
	Stage       string               `json:"stage"`
	Page        json.RawMessage      `json:"page"`
}

func saveUnsubmittedPageDiagnostic(page *rod.Page, ctx context.Context) error {
	capture, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	value, err := page.Context(capture).Eval(`() => ({path:location.origin+location.pathname,title:document.title.slice(0,256),drafts:Array.from(document.querySelectorAll('textarea,[contenteditable="true"],.content-input,input[placeholder*="标题"]')).slice(0,16).map(e=>({kind:e.tagName,text:(e.value??e.innerText??'').slice(0,8192)})),alerts:Array.from(document.querySelectorAll('.toast,[role="alert"]')).slice(0,4).map(e=>(e.innerText||'').slice(0,512))})`)
	if err != nil {
		return err
	}
	raw, err := value.Value.MarshalJSON()
	if err != nil {
		return err
	}
	path := filepath.Join(runtimeDir(), "unsubmitted-page-diagnostics.json")
	entries := []unsubmittedPageDiagnostic{}
	if previous, readErr := os.ReadFile(path); readErr == nil {
		if err := json.Unmarshal(previous, &entries); err != nil {
			return err
		}
	} else if !os.IsNotExist(readErr) {
		return readErr
	}
	id, _ := ctx.Value(operationIDKey{}).(string)
	entries = append(entries, unsubmittedPageDiagnostic{TargetID: page.TargetID, OperationID: id, CapturedAt: time.Now(), Stage: execution.CurrentStage(ctx), Page: raw})
	if len(entries) > 20 {
		entries = entries[len(entries)-20:]
	}
	return atomicJSON(path, entries)
}
