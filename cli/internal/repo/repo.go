// Package repo locates the infra repository root.
package repo

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// ErrNotFound is returned when the infra repo cannot be located.
var ErrNotFound = errors.New("infra repo not found")

// Locate returns the absolute path to the infra repo root.
// Precedence: $INFRA_REPO → walk up from cwd looking for a `.git` directory.
func Locate() (string, error) {
	if env := os.Getenv("INFRA_REPO"); env != "" {
		abs, err := filepath.Abs(env)
		if err != nil {
			return "", fmt.Errorf("resolve INFRA_REPO: %w", err)
		}
		return abs, nil
	}

	cwd, err := os.Getwd()
	if err != nil {
		return "", fmt.Errorf("get cwd: %w", err)
	}

	dir := cwd
	for {
		if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("%w: not inside an infra git repo and $INFRA_REPO is unset", ErrNotFound)
		}
		dir = parent
	}
}
