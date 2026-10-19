package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"sync"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
)

type freshReadKey struct{}
type cacheEntry struct {
	data  []byte
	until time.Time
}
type resultCache struct {
	mu      sync.Mutex
	entries map[string]cacheEntry
}

func prepareRead[T any](s *XiaohongshuService, ctx context.Context, tool string, payload any, cache *resultCache, out *T, ttl time.Duration) (string, bool, error) {
	key := cacheKey(s, tool, payload)
	hit := cache.GetFor(ctx, key, out, ttl)
	if hit {
		// This is a historical local snapshot, explicitly labelled cached/read_at.
		// Reading it does not assert current login or justify a write. Only a new
		// site read needs another identity check; never navigate just for a cache hit.
		return key, true, nil
	}
	if err := s.readSlot(false); err != nil {
		return key, false, err
	}
	if err := s.ensureLoggedIn(ctx); err != nil {
		return key, false, err
	}
	current := cacheKey(s, tool, payload)
	return current, cache.GetFor(ctx, current, out, ttl), nil
}

func reserveReadSlot(s *XiaohongshuService, ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	return s.readSlot(true)
}

func readTTL() time.Duration {
	if d, e := time.ParseDuration(os.Getenv("XHS_READ_CACHE_TTL")); e == nil && d >= 0 && d <= 2*time.Minute {
		return d
	}
	return 30 * time.Second
}
func cacheKey(s *XiaohongshuService, tool string, payload any) string {
	s.login.mu.Lock()
	id := s.login.userID
	s.login.mu.Unlock()
	raw, _ := json.Marshal([]any{tool, id, browser.Generation(), payload})
	hash := sha256.Sum256(raw)
	return hex.EncodeToString(hash[:])
}
func (c *resultCache) Get(ctx context.Context, key string, out any) bool {
	return c.GetFor(ctx, key, out, readTTL())
}
func (c *resultCache) GetFor(ctx context.Context, key string, out any, ttl time.Duration) bool {
	if fresh, _ := ctx.Value(freshReadKey{}).(bool); fresh || ttl == 0 {
		return false
	}
	c.mu.Lock()
	e, ok := c.entries[key]
	c.mu.Unlock()
	if !ok || time.Now().After(e.until) {
		return false
	}
	if json.Unmarshal(e.data, out) != nil {
		return false
	}
	execution.Count("read_cache_hits")
	return true
}
func (c *resultCache) Put(key string, value any) {
	c.PutFor(key, value, readTTL())
}
func (c *resultCache) PutFor(key string, value any, ttl time.Duration) {
	if ttl == 0 {
		return
	}
	raw, err := json.Marshal(value)
	if err != nil {
		return
	}
	if len(raw) > 2*1024*1024 {
		execution.Count("read_cache_oversize_skips")
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.entries == nil {
		c.entries = map[string]cacheEntry{}
	}
	delete(c.entries, key)
	bytes := 0
	for k, v := range c.entries {
		if time.Now().After(v.until) {
			delete(c.entries, k)
		} else {
			bytes += len(v.data)
		}
	}
	for len(c.entries) >= 32 || bytes+len(raw) > 8*1024*1024 {
		oldest := ""
		var at time.Time
		for k, v := range c.entries {
			if oldest == "" || v.until.Before(at) {
				oldest = k
				at = v.until
			}
		}
		bytes -= len(c.entries[oldest].data)
		delete(c.entries, oldest)
	}
	c.entries[key] = cacheEntry{raw, time.Now().Add(ttl)}
}
func (c *resultCache) Clear() { c.mu.Lock(); c.entries = nil; c.mu.Unlock() }
func (c *resultCache) PruneExpired(now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for key, value := range c.entries {
		if !now.Before(value.until) {
			delete(c.entries, key)
		}
	}
}
func projectReadResult(result *mcp.CallToolResult, compact bool, limit int) {
	if result == nil || result.IsError || (!compact && limit == 0) {
		return
	}
	for _, content := range result.Content {
		t, ok := content.(*mcp.TextContent)
		if !ok {
			continue
		}
		var value any
		if json.Unmarshal([]byte(t.Text), &value) != nil {
			continue
		}
		value = projectJSON(value, compact, limit)
		raw, err := json.Marshal(value)
		if err == nil {
			t.Text = string(raw)
		}
	}
}
func projectJSON(v any, compact bool, limit int) any {
	switch x := v.(type) {
	case []any:
		if limit > 0 && len(x) > limit {
			x = x[:limit]
		}
		out := make([]any, len(x))
		for i, v := range x {
			out[i] = projectJSON(v, compact, 0)
		}
		return out
	case map[string]any:
		out := map[string]any{}
		for k, v := range x {
			if compact && (k == "cover" || k == "avatar" || k == "imageList" || k == "video" || k == "images" || k == "media") {
				continue
			}
			n := 0
			if k == "feeds" || k == "comments" || k == "list" {
				n = limit
				if list, ok := v.([]any); ok {
					out[k+"_available_count"] = len(list)
					out[k+"_truncated"] = limit > 0 && len(list) > limit
				}
			}
			out[k] = projectJSON(v, compact, n)
		}
		return out
	default:
		return v
	}
}
