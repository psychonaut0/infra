package repo

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLocateFromEnv(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("INFRA_REPO", dir)
	got, err := Locate()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != dir {
		t.Errorf("want %q, got %q", dir, got)
	}
}

func TestLocateFromGit(t *testing.T) {
	dir := t.TempDir()
	// Simulate a git repo marker
	if err := os.MkdirAll(filepath.Join(dir, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	sub := filepath.Join(dir, "subdir")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}

	t.Setenv("INFRA_REPO", "")
	t.Chdir(sub)

	got, err := Locate()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != dir {
		t.Errorf("want %q, got %q", dir, got)
	}
}

func TestLocateFailsWithoutRepo(t *testing.T) {
	dir := t.TempDir() // not a git repo
	t.Setenv("INFRA_REPO", "")
	t.Chdir(dir)

	_, err := Locate()
	if err == nil {
		t.Fatal("expected error, got nil")
	}
}
