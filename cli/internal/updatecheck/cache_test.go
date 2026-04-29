package updatecheck

import (
	"path/filepath"
	"testing"
	"time"
)

func TestCache_RoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "update-check.json")

	if _, err := readCache(path); err == nil {
		t.Fatal("expected error reading missing cache")
	}

	now := time.Date(2026, 4, 29, 12, 0, 0, 0, time.UTC)
	if err := writeCache(path, cacheEntry{LastCheckAt: now, LatestVersion: "v0.4.0"}); err != nil {
		t.Fatalf("writeCache: %v", err)
	}

	got, err := readCache(path)
	if err != nil {
		t.Fatalf("readCache: %v", err)
	}
	if got.LatestVersion != "v0.4.0" {
		t.Errorf("LatestVersion = %q", got.LatestVersion)
	}
	if !got.LastCheckAt.Equal(now) {
		t.Errorf("LastCheckAt = %v", got.LastCheckAt)
	}
}

func TestStale(t *testing.T) {
	now := time.Date(2026, 4, 29, 12, 0, 0, 0, time.UTC)
	cases := []struct {
		last time.Time
		ttl  time.Duration
		now  time.Time
		want bool
	}{
		{now.Add(-25 * time.Hour), 24 * time.Hour, now, true},
		{now.Add(-1 * time.Hour), 24 * time.Hour, now, false},
		{time.Time{}, 24 * time.Hour, now, true},
	}
	for _, c := range cases {
		got := stale(c.last, c.ttl, c.now)
		if got != c.want {
			t.Errorf("stale(%v, %v, %v) = %v, want %v", c.last, c.ttl, c.now, got, c.want)
		}
	}
}
