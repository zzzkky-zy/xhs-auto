package main

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// These are configurable operating defaults, not published platform thresholds.
func intervalBounds(legacy, minKey, maxKey string, fallbackLow, fallbackHigh, floor time.Duration) (time.Duration, time.Duration) {
	if d, err := time.ParseDuration(os.Getenv(legacy)); err == nil && d >= floor && d <= time.Hour {
		return d, d
	}
	low, high := fallbackLow, fallbackHigh
	if d, err := time.ParseDuration(os.Getenv(minKey)); err == nil && d >= floor && d <= time.Hour {
		low = d
	}
	if d, err := time.ParseDuration(os.Getenv(maxKey)); err == nil && d >= low && d <= time.Hour {
		high = d
	}
	if high < low {
		high = low
	}
	return low, high
}

func searchIntervalBounds() (time.Duration, time.Duration) {
	return intervalBounds("XHS_SEARCH_INTERVAL", "XHS_SEARCH_INTERVAL_MIN", "XHS_SEARCH_INTERVAL_MAX", time.Minute, 90*time.Second, 30*time.Second)
}

func searchInterval() time.Duration {
	low, high := searchIntervalBounds()
	return randomInterval(low, high)
}

func searchHourlyBudget() int {
	if n, err := strconv.Atoi(os.Getenv("XHS_SEARCH_MAX_PER_HOUR")); err == nil && n >= 1 && n <= 60 {
		return n
	}
	return 20
}

func searchCacheTTL() time.Duration {
	if d, err := time.ParseDuration(os.Getenv("XHS_SEARCH_CACHE_TTL")); err == nil && d >= 0 && d <= time.Hour {
		return d
	}
	return 15 * time.Minute
}

func detailCacheTTL() time.Duration {
	if d, err := time.ParseDuration(os.Getenv("XHS_DETAIL_CACHE_TTL")); err == nil && d >= 0 && d <= time.Hour {
		return d
	}
	return 15 * time.Minute
}

func readIntervalBounds() (time.Duration, time.Duration) {
	return intervalBounds("XHS_READ_INTERVAL", "XHS_READ_INTERVAL_MIN", "XHS_READ_INTERVAL_MAX", 10*time.Second, 20*time.Second, 3*time.Second)
}

func readInterval() time.Duration {
	low, high := readIntervalBounds()
	return randomInterval(low, high)
}

func readHourlyBudget() int {
	if n, err := strconv.Atoi(os.Getenv("XHS_READ_MAX_PER_HOUR")); err == nil && n >= 1 && n <= 360 {
		return n
	}
	return 120
}

func readWindow(data *runtimeState, now time.Time) ([]time.Time, time.Time) {
	active := make([]time.Time, 0, len(data.ReadRequests)+1)
	for _, at := range data.ReadRequests {
		if at.Add(time.Hour).After(now) {
			active = append(active, at)
		}
	}
	next := data.NextReadAt
	if data.NextActionAt.After(next) {
		next = data.NextActionAt
	}
	if len(active) >= readHourlyBudget() {
		at := active[len(active)-readHourlyBudget()].Add(time.Hour)
		if at.After(next) {
			next = at
		}
	}
	return active, next
}

func reserveRead(data *runtimeState, active []time.Time, now time.Time) {
	data.ReadRequests = append(active, now)
	data.NextActionAt = now.Add(readInterval())
	data.NextReadAt = data.NextActionAt
	if len(data.ReadRequests) >= readHourlyBudget() {
		at := data.ReadRequests[len(data.ReadRequests)-readHourlyBudget()].Add(time.Hour)
		if at.After(data.NextReadAt) {
			data.NextReadAt = at
		}
	}
}

// Cache hits do not call this. All uncached research reads share this budget;
// serial execution alone does not prevent a burst of fast page reads.
func (s *XiaohongshuService) readSlot(reserve bool) error {
	if s.state == nil {
		return &runtimeError{Code: "ERR_STATE_NOT_INITIALIZED"}
	}
	st := s.state
	st.mu.Lock()
	defer st.mu.Unlock()
	if s.stateErr != nil {
		return s.stateErr
	}
	now := s.requestNow()
	active, next := readWindow(&st.data, now)
	if now.Before(next) {
		return &runtimeError{Code: "ERR_RATE_LIMIT", Scope: "read", RetryAt: next}
	}
	if reserve {
		reserveRead(&st.data, active, now)
		if err := atomicJSON(st.path, st.data); err != nil {
			s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", err)
			return s.stateErr
		}
	}
	return nil
}

func publishInterval() time.Duration {
	if d, err := time.ParseDuration(os.Getenv("XHS_PUBLISH_INTERVAL")); err == nil && d >= time.Minute && d <= 24*time.Hour {
		return d
	}
	return time.Minute
}

func (s *XiaohongshuService) requestNow() time.Time {
	if s.requestNowFn != nil {
		return s.requestNowFn()
	}
	return time.Now()
}

func isPublication(tool string) bool {
	return tool == "publish_content" || tool == "publish_with_video"
}

// searchSlot runs before identity/browser navigation. Only reserve=true consumes
// a slot; the caller holds browserMu then, so concurrent identical reads can hit
// the cache before reserving another slot. A restart retains the same budget.
func (s *XiaohongshuService) searchSlot(reserve bool) error {
	if s.state == nil {
		return &runtimeError{Code: "ERR_STATE_NOT_INITIALIZED"}
	}
	st := s.state
	st.mu.Lock()
	defer st.mu.Unlock()
	if s.stateErr != nil {
		return s.stateErr
	}
	now := s.requestNow()
	active := make([]time.Time, 0, len(st.data.SearchRequests)+1)
	for _, at := range st.data.SearchRequests {
		if at.Add(time.Hour).After(now) {
			active = append(active, at)
		}
	}
	next := st.data.NextSearchAt
	readActive, readNext := readWindow(&st.data, now)
	if readNext.After(next) {
		next = readNext
	}
	if len(active) >= searchHourlyBudget() {
		// Also correct when an operator lowered the budget since the last call.
		at := active[len(active)-searchHourlyBudget()].Add(time.Hour)
		if at.After(next) {
			next = at
		}
	}
	if now.Before(next) {
		return &runtimeError{Code: "ERR_RATE_LIMIT", RetryAt: next, Scope: "search"}
	}
	if !reserve {
		return nil
	}
	active = append(active, now)
	next = now.Add(searchInterval())
	if len(active) >= searchHourlyBudget() {
		at := active[len(active)-searchHourlyBudget()].Add(time.Hour)
		if at.After(next) {
			next = at
		}
	}
	st.data.SearchRequests, st.data.NextSearchAt = active, next
	reserveRead(&st.data, readActive, now)
	if err := atomicJSON(st.path, st.data); err != nil {
		s.stateErr = fmt.Errorf("ERR_STATE_WRITE: %w", err)
		return s.stateErr
	}
	return nil
}

func requestPolicy() map[string]any {
	low, high := writeIntervalBounds()
	readLow, readHigh := readIntervalBounds()
	searchLow, searchHigh := searchIntervalBounds()
	return map[string]any{
		"search_interval_seconds":         searchLow.Seconds(),
		"search_interval_min_seconds":     searchLow.Seconds(),
		"search_interval_max_seconds":     searchHigh.Seconds(),
		"read_interval_min_seconds":       readLow.Seconds(),
		"read_interval_max_seconds":       readHigh.Seconds(),
		"search_max_per_hour":             searchHourlyBudget(),
		"search_cache_seconds":            searchCacheTTL().Seconds(),
		"detail_cache_seconds":            detailCacheTTL().Seconds(),
		"read_interval_seconds":           readLow.Seconds(),
		"read_max_per_hour":               readHourlyBudget(),
		"browser_page_policy":             "fresh_page_per_action",
		"publish_submit_interval_seconds": publishInterval().Seconds(),
		"write_interval_min_seconds":      low.Seconds(),
		"write_interval_max_seconds":      high.Seconds(),
		"scheduled_post_gap_min_minutes":  60,
		"scheduled_post_gap_max_minutes":  90,
	}
}

func (s *XiaohongshuService) requestPacingSnapshot() map[string]any {
	value := map[string]any{"policy": requestPolicy()}
	if s.state != nil {
		s.state.mu.Lock()
		defer s.state.mu.Unlock()
		value["next_action_at"] = s.state.data.NextActionAt
		value["next_search_at"] = s.state.data.NextSearchAt
		value["next_publish_at"] = s.state.data.NextPublishAt
		value["next_write_at"] = s.state.data.NextWriteAt
		value["next_read_at"] = s.state.data.NextReadAt
	}
	return value
}
