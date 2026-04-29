// Package updatecheck implements a passive, daily check that prints a
// non-blocking footer when a newer release is published to the mirror.
package updatecheck

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

type cacheEntry struct {
	LastCheckAt   time.Time `json:"last_check_at"`
	LatestVersion string    `json:"latest_version"`
}

func cachePath() (string, error) {
	dir := os.Getenv("XDG_CACHE_HOME")
	if dir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		dir = filepath.Join(home, ".cache")
	}
	return filepath.Join(dir, "infra", "update-check.json"), nil
}

func readCache(path string) (cacheEntry, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return cacheEntry{}, err
	}
	var e cacheEntry
	if err := json.Unmarshal(b, &e); err != nil {
		return cacheEntry{}, fmt.Errorf("parse cache: %w", err)
	}
	return e, nil
}

func writeCache(path string, e cacheEntry) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	b, err := json.Marshal(e)
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func stale(last time.Time, ttl time.Duration, now time.Time) bool {
	if last.IsZero() {
		return true
	}
	return now.Sub(last) > ttl
}
