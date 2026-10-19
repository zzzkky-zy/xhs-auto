package main

import (
	"context"
	"encoding/json"
	"testing"
)

// Pure data/logic checks only. No fake websites, failures, network or browser.
func TestEngineeringProjectionPreservesTargets(t *testing.T) {
	value := map[string]any{"feeds": []any{map[string]any{"id": "one", "xsecToken": "access", "noteCard": map[string]any{"displayTitle": "title", "cover": map[string]any{"url": "image"}, "interactInfo": map[string]any{"likedCount": "201"}}}, map[string]any{"id": "two"}}, "count": 2}
	projected := projectJSON(value, true, 1).(map[string]any)
	if projected["feeds_truncated"] != true || projected["feeds_available_count"] != 2 {
		t.Fatal(projected)
	}
	item := projected["feeds"].([]any)[0].(map[string]any)
	if item["id"] != "one" || item["xsecToken"] != "access" {
		t.Fatal("target fields were lost")
	}
	card := item["noteCard"].(map[string]any)
	if _, exists := card["cover"]; exists {
		t.Fatal("compact media retained")
	}
	if card["interactInfo"] == nil {
		t.Fatal("metrics lost")
	}
	if len(value["feeds"].([]any)) != 2 {
		t.Fatal("projection mutated cached source")
	}
}
func TestEngineeringCacheUsesIndependentCopies(t *testing.T) {
	t.Setenv("XHS_READ_CACHE_TTL", "30s")
	c := &resultCache{}
	source := map[string]any{"id": "one"}
	c.Put("key", source)
	var a map[string]any
	if !c.Get(context.Background(), "key", &a) {
		t.Fatal("cache miss")
	}
	a["id"] = "changed"
	var b map[string]any
	if !c.Get(context.Background(), "key", &b) || b["id"] != "one" {
		t.Fatal("cached result was mutated")
	}
	raw, _ := json.Marshal(b)
	if string(raw) != "{\"id\":\"one\"}" {
		t.Fatal(string(raw))
	}
}
func TestEngineeringFreshReadBypassesCache(t *testing.T) {
	t.Setenv("XHS_READ_CACHE_TTL", "30s")
	c := &resultCache{}
	c.Put("key", map[string]int{"count": 1})
	var value map[string]int
	if c.Get(context.WithValue(context.Background(), freshReadKey{}, true), "key", &value) {
		t.Fatal("fresh read used cache")
	}
	c.Clear()
	if c.Get(context.Background(), "key", &value) {
		t.Fatal("cleared cache used")
	}
}
