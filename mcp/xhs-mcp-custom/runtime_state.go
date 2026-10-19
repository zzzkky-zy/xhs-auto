package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

// runtimeState 保存冷却和提交账本；不保存 cookies、登录令牌或完整文案。
type runtimeState struct {
	Version                int                         `json:"version"`
	StateID                string                      `json:"state_id"`
	Account                string                      `json:"account"`
	CaptchaUntil           time.Time                   `json:"captcha_until,omitempty"`
	SearchRateLimitUntil   time.Time                   `json:"search_rate_limit_until,omitempty"`
	IdentityRateLimitUntil time.Time                   `json:"identity_rate_limit_until,omitempty"`
	NextWriteAt            time.Time                   `json:"next_write_at,omitempty"`
	NextPublishAt          time.Time                   `json:"next_publish_at,omitempty"`
	NextSearchAt           time.Time                   `json:"next_search_at,omitempty"`
	SearchRequests         []time.Time                 `json:"search_requests,omitempty"`
	NextActionAt           time.Time                   `json:"next_action_at,omitempty"`
	NextReadAt             time.Time                   `json:"next_read_at,omitempty"`
	ReadRequests           []time.Time                 `json:"read_requests,omitempty"`
	Operations             map[string]*operationRecord `json:"operations"`
}

type operationRecord struct {
	ID          string          `json:"id"`
	Tool        string          `json:"tool"`
	Fingerprint string          `json:"fingerprint"`
	Status      string          `json:"status"`
	UpdatedAt   time.Time       `json:"updated_at"`
	Result      json.RawMessage `json:"result,omitempty"`
	Evidence    string          `json:"evidence,omitempty"`
}

type stateStore struct {
	mu   sync.Mutex
	path string
	data runtimeState
}

type runtimeError struct {
	Code    string
	ID      string
	RetryAt time.Time
	Scope   string
}

func (e *runtimeError) Error() string {
	return fmt.Sprintf("%s: operation_id=%s scope=%s retry_at=%s；保存进度并按运行契约处理", e.Code, e.ID, e.Scope, e.RetryAt.Format(time.RFC3339Nano))
}

func runtimeDir() string {
	if p := strings.TrimSpace(os.Getenv("XHS_STATE_DIR")); p != "" {
		return p
	}
	base := os.Getenv("XDG_STATE_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".local", "state")
	}
	return filepath.Join(base, "xiaohongshu-operations")
}

func atomicJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".state-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if err = f.Chmod(0600); err == nil {
		_, err = f.Write(data)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	dir, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}

func openStateStore(path, account string) (*stateStore, error) {
	identity := make([]byte, 16)
	if _, err := rand.Read(identity); err != nil {
		return nil, err
	}
	s := &stateStore{path: path, data: runtimeState{Version: 1, StateID: hex.EncodeToString(identity), Account: account, Operations: map[string]*operationRecord{}}}
	data, err := os.ReadFile(path)
	if err == nil {
		s.data = runtimeState{}
		if err = json.Unmarshal(data, &s.data); err != nil {
			return nil, fmt.Errorf("ERR_STATE_CORRUPT: %w", err)
		}
		if (s.data.Version != 1 && s.data.Version != 2) || s.data.StateID == "" || s.data.Account != account || s.data.Operations == nil {
			return nil, fmt.Errorf("ERR_STATE_INCOMPATIBLE: 版本或账号不匹配，禁止覆盖")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	for _, op := range s.data.Operations {
		if op.Status == "in_flight" {
			op.Status = "unknown"
			op.UpdatedAt = time.Now()
		}
	}
	if s.data.NextPublishAt.IsZero() {
		// Upgrading must not grant a fresh burst after a recent older-version
		// submission. Older journals only have the last update timestamp.
		for _, op := range s.data.Operations {
			if isPublication(op.Tool) && op.Status != "not_applied" {
				at := op.UpdatedAt.Add(publishInterval())
				if at.After(s.data.NextPublishAt) {
					s.data.NextPublishAt = at
				}
			}
		}
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	if err := s.initJournal(s.data.Version == 1); err != nil {
		return nil, err
	}
	s.data.Version = 2
	if err := atomicJSON(path, s.data); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *XiaohongshuService) InitRuntime() error {
	account := strings.TrimSpace(os.Getenv("XHS_ACCOUNT_ID"))
	if account == "" {
		account = "default"
	}
	path := filepath.Join(runtimeDir(), "runtime-state.json")
	expected := os.Getenv("XHS_EXPECTED_STATE_ID")
	if expected != "" {
		if _, err := os.Stat(path); err != nil {
			return fmt.Errorf("ERR_STATE_LOST: 已绑定运行状态不存在；禁止重建")
		}
	}
	st, err := openStateStore(path, account)
	if err != nil {
		return err
	}
	s.state = st
	if expected != "" && expected != st.data.StateID {
		return fmt.Errorf("ERR_STATE_INCOMPATIBLE: 状态身份不匹配")
	}
	s.captcha.until = st.data.CaptchaUntil
	s.limits.searchUntil = st.data.SearchRateLimitUntil
	s.limits.identityUntil = st.data.IdentityRateLimitUntil
	return nil
}

// 冷却落盘失败后停止浏览器工作，不通过重启绕开保护。
func (s *XiaohongshuService) persistCaptcha(until time.Time) {
	if s.state == nil {
		return
	}
	s.state.mu.Lock()
	defer s.state.mu.Unlock()
	s.state.data.CaptchaUntil = until
	if err := atomicJSON(s.state.path, s.state.data); err != nil {
		s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", err)
	}
}

type operationIDKey struct{}

func withOperationID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, operationIDKey{}, id)
}

func writeIntervalBounds() (time.Duration, time.Duration) {
	if d, err := time.ParseDuration(os.Getenv("XHS_WRITE_INTERVAL")); err == nil && d >= time.Minute {
		return d, d
	}
	low, high := time.Minute, 3*time.Minute
	if d, err := time.ParseDuration(os.Getenv("XHS_WRITE_INTERVAL_MIN")); err == nil && d >= time.Minute && d <= time.Hour {
		low = d
	}
	if d, err := time.ParseDuration(os.Getenv("XHS_WRITE_INTERVAL_MAX")); err == nil && d >= low && d <= time.Hour {
		high = d
	}
	if high < low {
		high = low
	}
	return low, high
}

func writeInterval() time.Duration {
	low, high := writeIntervalBounds()
	return randomInterval(low, high)
}

func randomInterval(low, high time.Duration) time.Duration {
	n, err := rand.Int(rand.Reader, big.NewInt(int64((high-low)/time.Second)+1))
	if err != nil {
		return high
	}
	return low + time.Duration(n.Int64())*time.Second
}

func protectedWrite[T any](s *XiaohongshuService, ctx context.Context, tool string, payload any, fn func() (*T, error)) (result *T, err error) {
	if err := requireWriteAllowed(); err != nil {
		return nil, err
	}
	if s.state == nil {
		return nil, &runtimeError{Code: "ERR_STATE_NOT_INITIALIZED"}
	}
	if err := s.writeMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.writeMu.Unlock()
	encoded, err := mutationSignature(payload)
	if err != nil {
		return nil, err
	}
	hash := sha256.Sum256(append([]byte(tool+"\n"), encoded...))
	fingerprint := hex.EncodeToString(hash[:])
	id, _ := ctx.Value(operationIDKey{}).(string)
	if id == "" {
		id = fingerprint
	}
	if len(id) > 128 || strings.TrimSpace(id) != id {
		return nil, &runtimeError{Code: "ERR_INVALID_OPERATION_ID"}
	}
	st := s.state
	st.mu.Lock()
	if os.Getenv("XHS_EXPECTED_STATE_ID") == "" || os.Getenv("XHS_EXPECTED_STATE_ID") != st.data.StateID {
		st.mu.Unlock()
		return nil, &runtimeError{Code: "ERR_STATE_UNCONFIRMED", ID: id}
	}
	if s.stateErr != nil {
		err := s.stateErr
		st.mu.Unlock()
		return nil, err
	}
	old, lookupErr := st.receipt(id, "")
	if lookupErr != nil {
		s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", lookupErr)
		st.mu.Unlock()
		return nil, s.stateErr
	}
	if old != nil {
		if old.Fingerprint != fingerprint {
			st.mu.Unlock()
			return nil, &runtimeError{Code: "ERR_IDEMPOTENCY_CONFLICT", ID: id}
		}
		if old.Status == "succeeded" && len(old.Result) > 0 {
			var value T
			err := json.Unmarshal(old.Result, &value)
			st.mu.Unlock()
			return &value, err
		}
		if old.Status != "not_applied" {
			status := old.Status
			st.mu.Unlock()
			return nil, &runtimeError{Code: "ERR_OPERATION_" + strings.ToUpper(status), ID: id}
		}
	}
	// Indexed lookup does not load or scan the accumulated history.
	duplicate, lookupErr := st.receipt("", fingerprint)
	if lookupErr != nil {
		s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", lookupErr)
		st.mu.Unlock()
		return nil, s.stateErr
	}
	if duplicate != nil && duplicate.ID != id && duplicate.Status != "not_applied" {
		st.mu.Unlock()
		return nil, &runtimeError{Code: "ERR_DUPLICATE_OPERATION", ID: duplicate.ID}
	}
	// The outer queue stops automatic writes while a task is unknown. An
	// independently authorized direct request keeps its own ID and fingerprint;
	// it must neither resolve nor replay another operation's unknown receipt.
	now := s.requestNow()
	at := st.data.NextWriteAt
	scope := "write"
	if st.data.NextActionAt.After(at) {
		at, scope = st.data.NextActionAt, "action"
	}
	if isPublication(tool) && st.data.NextPublishAt.After(at) {
		at, scope = st.data.NextPublishAt, "publish"
	}
	if now.Before(at) {
		st.mu.Unlock()
		return nil, &runtimeError{Code: "ERR_RATE_LIMIT", ID: id, RetryAt: at, Scope: scope}
	}
	st.mu.Unlock()
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	// 绑定已核验的实际账号，避免浏览器换号后把任务发到另一账号。
	expected := strings.TrimSpace(os.Getenv("XHS_EXPECTED_USER_ID"))
	s.login.mu.Lock()
	actual := s.login.userID
	s.login.mu.Unlock()
	if expected == "" || actual == "" || expected != actual {
		return nil, &runtimeError{Code: "ERR_ACCOUNT_UNCONFIRMED", ID: id}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	st.mu.Lock()
	// Identity navigation (or a concurrent read) may have reserved the shared
	// interval since preflight. Defer before creating any submission record.
	now = s.requestNow()
	if now.Before(st.data.NextActionAt) {
		at := st.data.NextActionAt
		st.mu.Unlock()
		return nil, &runtimeError{Code: "ERR_RATE_LIMIT", ID: id, RetryAt: at, Scope: "action"}
	}
	op := &operationRecord{ID: id, Tool: tool, Fingerprint: fingerprint, Status: "in_flight", UpdatedAt: time.Now()}
	now = s.requestNow()
	st.data.NextWriteAt = now.Add(writeInterval())
	st.data.NextActionAt = now.Add(readInterval())
	previousPublishAt := st.data.NextPublishAt
	if isPublication(tool) {
		// Scheduled posts still submit to the creator platform now, so they use
		// the same submission interval as immediate posts.
		st.data.NextPublishAt = now.Add(publishInterval())
	}
	err = st.saveOperation(op)
	if err != nil {
		s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", err)
	}
	st.mu.Unlock()
	if err != nil {
		return nil, fmt.Errorf("ERR_STATE_WRITE: %w", err)
	}
	defer func() {
		if recovered := recover(); recovered != nil {
			result = nil
			err = fmt.Errorf("ERR_EXECUTION_PANIC: 浏览器操作中断")
			if failure, ok := recovered.(error); ok {
				err = failure
			}
		}
		if errors.Is(err, xhserrors.ErrRateLimited) {
			s.tripRateLimit("identity", err)
		}
		var commentErr *xiaohongshu.CommentActionError
		notSubmitted := errors.As(err, &commentErr) && commentErr.SubmissionNotAttempted()
		if err != nil && execution.NotDispatched(ctx) {
			notSubmitted = true
			if commentErr == nil {
				err = fmt.Errorf("ERR_ACTION_NOT_SUBMITTED: phase=%s: %w", execution.CurrentStage(ctx), err)
			}
		}
		st.mu.Lock()
		op.UpdatedAt = time.Now()
		if err == nil {
			op.Result, err = json.Marshal(result)
		}
		if err == nil {
			op.Status = "succeeded"
		} else if notSubmitted {
			op.Status = "not_applied"
			if isPublication(tool) {
				// A proven pre-submit failure does not consume a submission
				// slot. The already-drawn shared write interval still applies.
				st.data.NextPublishAt = previousPublishAt
			}
			op.Evidence = "no submit dispatch; phase=" + execution.CurrentStage(ctx)
			if commentErr != nil {
				op.Evidence = "no submit dispatch; phase=" + commentErr.Phase
			}
		} else {
			op.Status = "unknown"
		}
		if saveErr := st.saveOperation(op); saveErr != nil {
			notSubmitted = false
			err = fmt.Errorf("ERR_STATE_WRITE: operation_id=%s；结果需核对", id)
			s.stateErr = err
		}
		st.mu.Unlock()
		if err != nil {
			s.tripCaptchaCooldown(err)
			if notSubmitted {
				err = fmt.Errorf("%w; operation_id=%s；未点发送，保存进度后处理原因", err, id)
			} else {
				err = fmt.Errorf("ERR_OPERATION_UNKNOWN: operation_id=%s；先查询记录及平台结果，禁止重发: %w", id, err)
			}
		}
	}()
	defer s.readCache.Clear()
	return fn()
}

func (s *XiaohongshuService) OperationStatus(id string) (*operationRecord, error) {
	if s.state == nil {
		return nil, &runtimeError{Code: "ERR_STATE_NOT_INITIALIZED"}
	}
	s.state.mu.Lock()
	defer s.state.mu.Unlock()
	if s.stateErr != nil {
		return nil, s.stateErr
	}
	op, lookupErr := s.state.receipt(id, "")
	if lookupErr != nil {
		return nil, lookupErr
	}
	if op == nil {
		return nil, &runtimeError{Code: "ERR_OPERATION_NOT_FOUND", ID: id}
	}
	copy := *op
	copy.Result = append(json.RawMessage(nil), op.Result...)
	return &copy, nil
}

func (s *XiaohongshuService) ResolveOperation(id, status, evidence string) (*operationRecord, error) {
	if os.Getenv("XHS_ALLOW_RECONCILE") != "1" {
		return nil, &runtimeError{Code: "ERR_RECONCILE_NOT_ALLOWED", ID: id}
	}
	if s.state == nil {
		return nil, &runtimeError{Code: "ERR_STATE_NOT_INITIALIZED"}
	}
	if (status != "confirmed" && status != "not_applied") || strings.TrimSpace(evidence) == "" {
		return nil, fmt.Errorf("ERR_INVALID_RESOLUTION: 必须提供平台核对依据及 confirmed/not_applied")
	}
	s.state.mu.Lock()
	defer s.state.mu.Unlock()
	op, lookupErr := s.state.receipt(id, "")
	if lookupErr != nil {
		return nil, lookupErr
	}
	if op == nil || op.Status != "unknown" {
		return nil, fmt.Errorf("ERR_INVALID_RESOLUTION: 只允许处理 unknown")
	}
	op.Status = status
	op.Evidence = evidence
	op.UpdatedAt = time.Now()
	if err := s.state.saveOperation(op); err != nil {
		s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", err)
		return nil, err
	}
	copy := *op
	return &copy, nil
}

// 本地素材发生改变必须改变提交指纹，防止路径相同但内容不同被当成重放。
func mutationSignature(payload any) ([]byte, error) {
	assets := []string{}
	switch value := payload.(type) {
	case *PublishRequest:
		assets = value.Images
	case *PublishVideoRequest:
		assets = []string{value.Video}
	}
	hashes := []string{}
	for _, path := range assets {
		if strings.HasPrefix(path, "https://") || strings.HasPrefix(path, "http://") {
			hashes = append(hashes, path)
			continue
		}
		f, err := os.Open(path)
		if err != nil {
			return nil, fmt.Errorf("ERR_ASSET_UNAVAILABLE: %w", err)
		}
		h := sha256.New()
		_, err = io.Copy(h, f)
		_ = f.Close()
		if err != nil {
			return nil, err
		}
		hashes = append(hashes, hex.EncodeToString(h.Sum(nil)))
	}
	return json.Marshal(struct {
		Payload any
		Assets  []string
	}{payload, hashes})
}

// contextMutex 让等待浏览器或写操作的请求也能超时退出。
type contextMutex struct {
	once sync.Once
	slot chan struct{}
}

func (m *contextMutex) init() { m.once.Do(func() { m.slot = make(chan struct{}, 1) }) }
func (m *contextMutex) LockContext(ctx context.Context) error {
	m.init()
	select {
	case m.slot <- struct{}{}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (m *contextMutex) Lock() { _ = m.LockContext(context.Background()) }
func (m *contextMutex) TryLock() bool {
	m.init()
	select {
	case m.slot <- struct{}{}:
		return true
	default:
		return false
	}
}
func (m *contextMutex) Unlock() { <-m.slot }
