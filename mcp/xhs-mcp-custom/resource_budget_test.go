package main

import (
	"context"
	"testing"
	"time"
)

func TestQueueSleepsUntilActualDeadline(t *testing.T) {
	now := time.Unix(1800000000, 0)
	for _, test := range []struct {
		at   float64
		want time.Duration
	}{
		{float64(now.Unix() + 123), 123 * time.Second}, {float64(now.Unix() - 10), time.Second},
		{float64(now.Unix() + 7200), time.Hour},
	} {
		if got := queueNextDelay(map[string]any{"background_next_at": test.at}, now); got != test.want {
			t.Fatalf("delay=%v want=%v", got, test.want)
		}
	}
	if got := queueNextDelay(map[string]any{}, now); got != time.Hour {
		t.Fatal(got)
	}
}

func TestIdleCachePruneRetainsFreshEvidence(t *testing.T) {
	cache := &resultCache{}
	cache.PutFor("fresh", map[string]string{"id": "keep"}, time.Minute)
	cache.PutFor("expired", map[string]string{"id": "old"}, time.Minute)
	cache.mu.Lock()
	item := cache.entries["expired"]
	item.until = time.Now().Add(-time.Second)
	cache.entries["expired"] = item
	cache.mu.Unlock()
	cache.PruneExpired(time.Now())
	var out map[string]string
	if !cache.Get(context.Background(), "fresh", &out) || out["id"] != "keep" {
		t.Fatal("fresh evidence lost")
	}
	cache.mu.Lock()
	_, exists := cache.entries["expired"]
	cache.mu.Unlock()
	if exists {
		t.Fatal("idle expired bytes retained")
	}
}
