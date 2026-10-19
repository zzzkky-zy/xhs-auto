// Package execution records bounded, content-free task progress. It never stores
// cookies, tool arguments, post text or signed URLs.
package execution

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

type spanKey struct{}
type Record struct {
	ID                string    `json:"id"`
	Tool              string    `json:"tool"`
	TaskID            string    `json:"task_id,omitempty"`
	Stage             string    `json:"stage"`
	Started           time.Time `json:"started_at"`
	Updated           time.Time `json:"updated_at"`
	StageLimitSeconds float64   `json:"stage_expected_seconds"`
	Seconds           float64   `json:"seconds"`
	Status            string    `json:"status"`
}
type Span struct {
	mu     sync.Mutex
	rec    Record
	notify func(string, float64)
	step   float64
}

var registry = struct {
	sync.Mutex
	active     map[string]*Span
	recent     []Record
	totals     map[string]int
	counters   map[string]int
	path       string
	persistErr string
}{active: map[string]*Span{}, totals: map[string]int{}, counters: map[string]int{}}

func Configure(dir string) {
	registry.Lock()
	defer registry.Unlock()
	if dir != "" {
		registry.path = filepath.Join(dir, "activity.json")
	}
}
func Count(name string) { registry.Lock(); defer registry.Unlock(); registry.counters[name]++ }
func Begin(parent context.Context, tool, taskID string, notify func(string, float64)) (context.Context, func(bool)) {
	ctx, cancel := context.WithCancel(parent)
	now := time.Now()
	span := &Span{rec: Record{ID: now.Format("20060102T150405.000000000"), Tool: tool, TaskID: taskID, Stage: "execute", Started: now, Updated: now, Status: "running", StageLimitSeconds: 180}, notify: notify}
	registry.Lock()
	registry.active[span.rec.ID] = span
	registry.Unlock()
	ctx = context.WithValue(ctx, spanKey{}, span)
	var once sync.Once
	return ctx, func(ok bool) {
		once.Do(func() {
			cancel()
			span.mu.Lock()
			r := span.rec
			r.Seconds = time.Since(r.Started).Seconds()
			r.Status = "failed"
			if ok {
				r.Status = "succeeded"
			}
			span.mu.Unlock()
			registry.Lock()
			delete(registry.active, r.ID)
			registry.recent = append(registry.recent, r)
			if len(registry.recent) > 100 {
				registry.recent = registry.recent[len(registry.recent)-100:]
			}
			registry.totals[r.Status]++
			registry.Unlock()
			persist()
		})
	}
}

// Stage observes progress only. The outer tool/action deadline and explicit
// cancellation bound execution; an estimated stage duration never cancels input.
func Stage(ctx context.Context, stage string, limit time.Duration) {
	span, _ := ctx.Value(spanKey{}).(*Span)
	if span == nil {
		return
	}
	span.mu.Lock()
	span.rec.Stage = stage
	span.rec.Updated = time.Now()
	if limit <= 0 {
		limit = 30 * time.Second
	}
	span.rec.StageLimitSeconds = limit.Seconds()
	span.step++
	step := span.step
	notify := span.notify
	span.mu.Unlock()
	persist()
	if notify != nil {
		notify(stage, step)
	}
}
func Snapshot() map[string]any {
	registry.Lock()
	defer registry.Unlock()
	active := []Record{}
	for _, s := range registry.active {
		s.mu.Lock()
		r := s.rec
		r.Seconds = time.Since(r.Started).Seconds()
		s.mu.Unlock()
		active = append(active, r)
	}
	recent := append([]Record{}, registry.recent...)
	totals := map[string]int{}
	for k, v := range registry.totals {
		totals[k] = v
	}
	counts := map[string]int{}
	for k, v := range registry.counters {
		counts[k] = v
	}
	durations := []float64{}
	for _, r := range recent {
		durations = append(durations, r.Seconds)
	}
	sort.Float64s(durations)
	p95 := 0.0
	if len(durations) > 0 {
		p95 = durations[(len(durations)-1)*95/100]
	}
	return map[string]any{"active": active, "recent": recent, "totals": totals, "counters": counts, "recent_p95_seconds": p95, "sample_limit": 100, "sample_count": len(recent), "persistence_error": registry.persistErr, "metrics_scope": "current_process; persisted activity is diagnostic, not submission proof"}
}

var persistMu sync.Mutex

func persist() {
	persistMu.Lock()
	defer persistMu.Unlock()
	snapshot := Snapshot()
	registry.Lock()
	path := registry.path
	registry.Unlock()
	if path == "" {
		return
	}
	var failure error
	defer func() {
		registry.Lock()
		defer registry.Unlock()
		if failure != nil {
			if registry.persistErr == "" {
				log.Print("ERR_ACTIVITY_WRITE: diagnostic progress unavailable; inspect private storage")
			}
			registry.persistErr = "ERR_ACTIVITY_WRITE"
			registry.counters["progress_persist_errors"]++
		} else {
			registry.persistErr = ""
		}
	}()
	data, err := json.Marshal(snapshot)
	if err != nil {
		failure = err
		return
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".activity-")
	if err != nil {
		failure = err
		return
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
	if err == nil {
		err = os.Rename(name, path)
	}
	failure = err
}

// Sleep is cancellable even for old page-settling waits.
func Sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
