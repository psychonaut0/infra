package updatecheck

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestRun_WritesCache(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, `{
			"version": "v0.5.0",
			"commit": "x",
			"published_at": "2026-04-29T00:00:00Z",
			"binaries": {"linux/%s": {"url": "u", "sha256": "s"}}
		}`, runtime.GOARCH)
	}))
	defer srv.Close()

	dir := t.TempDir()
	path := filepath.Join(dir, "update-check.json")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	checker := &Checker{
		MirrorURL: srv.URL,
		CachePath: path,
		TTL:       time.Hour,
		Enabled:   true,
		Now:       func() time.Time { return time.Date(2026, 4, 29, 12, 0, 0, 0, time.UTC) },
	}
	checker.Refresh(ctx)

	got, err := readCache(path)
	if err != nil {
		t.Fatalf("readCache: %v", err)
	}
	if got.LatestVersion != "v0.5.0" {
		t.Errorf("cached version = %q", got.LatestVersion)
	}
}

func TestFooter_PrintsWhenNewer(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "update-check.json")
	_ = writeCache(path, cacheEntry{
		LastCheckAt:   time.Date(2026, 4, 29, 11, 0, 0, 0, time.UTC),
		LatestVersion: "v0.5.0",
	})
	var buf bytes.Buffer
	c := &Checker{CachePath: path, CurrentVersion: "v0.4.0", Out: &buf, Enabled: true}
	c.Footer()
	if !bytes.Contains(buf.Bytes(), []byte("update available")) {
		t.Errorf("expected footer, got %q", buf.String())
	}
}

func TestFooter_SuppressedWhenEqual(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "update-check.json")
	_ = writeCache(path, cacheEntry{
		LastCheckAt:   time.Date(2026, 4, 29, 11, 0, 0, 0, time.UTC),
		LatestVersion: "v0.4.0",
	})
	var buf bytes.Buffer
	c := &Checker{CachePath: path, CurrentVersion: "v0.4.0", Out: &buf, Enabled: true}
	c.Footer()
	if buf.Len() != 0 {
		t.Errorf("expected no output, got %q", buf.String())
	}
}

func TestFooter_SuppressedWhenDisabled(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "update-check.json")
	_ = writeCache(path, cacheEntry{
		LastCheckAt:   time.Date(2026, 4, 29, 11, 0, 0, 0, time.UTC),
		LatestVersion: "v0.5.0",
	})
	var buf bytes.Buffer
	c := &Checker{CachePath: path, CurrentVersion: "v0.4.0", Out: &buf, Enabled: false}
	c.Footer()
	if buf.Len() != 0 {
		t.Errorf("expected no output when disabled")
	}
}
