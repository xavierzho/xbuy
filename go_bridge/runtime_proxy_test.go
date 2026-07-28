package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func TestCleanRuntimeProxyPoolKeepsDuplicates(t *testing.T) {
	got := cleanRuntimeProxyPool([]string{
		" http://ip1:1 ",
		"http://ip1:1",
		"",
		"   ",
		"http://ip2:2",
		"http://ip1:1",
	})
	want := []string{
		"http://ip1:1",
		"http://ip1:1",
		"http://ip2:2",
		"http://ip1:1",
	}
	if len(got) != len(want) {
		t.Fatalf("len=%d want %d, got %#v", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got[%d]=%q want %q, all=%#v", i, got[i], want[i], got)
		}
	}
}

func TestRuntimeProxyPickerKeepsLaneUntil429(t *testing.T) {
	tlsClientRotationCounter.Store(0)
	p := newRuntimeProxyPicker(true, []string{
		"http://ip1:1",
		"http://ip2:2",
		"http://ip3:3",
	})
	first := p.pick(0)
	if first.ProxyURL != "http://ip1:1" {
		t.Fatalf("first pick = %q", first.ProxyURL)
	}
	if first.TLSProfile == "" {
		t.Fatalf("first pick missing TLS profile")
	}
	again := p.pick(0)
	if again != first {
		t.Fatalf("same lane should keep lease before 429, got %#v want %#v", again, first)
	}
	lane1 := p.pick(1)
	if lane1.ProxyURL != "http://ip2:2" {
		t.Fatalf("lane 1 pick = %q", lane1.ProxyURL)
	}
	p.markLaneHTTP429(0, "http://ip1:1")
	after429 := p.pick(0)
	if after429.ProxyURL == "http://ip1:1" {
		t.Fatalf("lane 0 reused banned proxy %q", after429.ProxyURL)
	}
	if after429.TLSProfile == first.TLSProfile {
		t.Fatalf("lane 0 reused TLS profile %q after 429", after429.TLSProfile)
	}
}

func TestRuntimeProxyPickerUpdateKeepsLaneLeaseUntil429(t *testing.T) {
	tlsClientRotationCounter.Store(0)
	p := newRuntimeProxyPicker(true, []string{
		"http://ip1:1",
	})
	first := p.pick(2)
	if first.ProxyURL != "http://ip1:1" {
		t.Fatalf("first pick = %q", first.ProxyURL)
	}
	p.updatePool([]string{
		"http://ip1:1",
		"http://ip2:2",
	})
	again := p.pick(2)
	if again != first {
		t.Fatalf("pool update should not replace active lane lease, got %#v want %#v", again, first)
	}
	p.markLaneHTTP429(2, "http://ip1:1")
	after429 := p.pick(2)
	if after429.ProxyURL != "http://ip2:2" {
		t.Fatalf("after 429 pick = %q want http://ip2:2", after429.ProxyURL)
	}
	if after429.TLSProfile == first.TLSProfile {
		t.Fatalf("after 429 reused TLS profile %q", after429.TLSProfile)
	}
}

func TestRushLoopLaneParallelWaitsWithinLane(t *testing.T) {
	var mu sync.Mutex
	active := 0
	maxActive := 0
	seenByLane := map[int]int{}
	firstStart := map[int]time.Time{}
	secondStart := map[int]time.Time{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		laneRaw := r.URL.Query().Get("lane")
		var lane int
		_, _ = fmt.Sscanf(laneRaw, "%d", &lane)

		mu.Lock()
		active++
		if active > maxActive {
			maxActive = active
		}
		seenByLane[lane]++
		n := seenByLane[lane]
		if n == 1 {
			firstStart[lane] = time.Now()
		} else if n == 2 {
			secondStart[lane] = time.Now()
		}
		mu.Unlock()

		time.Sleep(60 * time.Millisecond)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"code":1001,"message":"库存不足"}`))

		mu.Lock()
		active--
		mu.Unlock()
	}))
	defer srv.Close()

	var attempts []Attempt
	for shot := 0; shot < 10; shot++ {
		lane := shot % 5
		attempts = append(attempts, Attempt{
			Method:    "GET",
			URL:       fmt.Sprintf("%s/?lane=%d&shot=%d", srv.URL, lane, shot),
			LaneID:    lane,
			TimeoutMs: 3000,
		})
	}
	res := execRushLoop(RushLoopRequest{
		Attempts:        attempts,
		LaneParallel:    true,
		RushLoopDelayMs: 20,
	})
	if !res.OK {
		t.Fatalf("lane parallel rush loop failed: %s", res.Error)
	}
	if len(res.Trace) != 10 {
		t.Fatalf("trace len=%d want 10", len(res.Trace))
	}
	if maxActive < 5 {
		t.Fatalf("maxActive=%d want at least 5 lanes in parallel", maxActive)
	}
	for lane := 0; lane < 5; lane++ {
		if seenByLane[lane] != 2 {
			t.Fatalf("lane %d seen %d times, want 2", lane, seenByLane[lane])
		}
		if secondStart[lane].Sub(firstStart[lane]) < 55*time.Millisecond {
			t.Fatalf("lane %d did not wait for first response before second shot: delta=%s", lane, secondStart[lane].Sub(firstStart[lane]))
		}
	}
}
