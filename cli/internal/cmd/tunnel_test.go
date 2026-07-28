package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

// Only writeFileAtomic is covered here. loadTunnel hardcodes
// tunnel.DefaultConfigPath() (the operator's real ~/.config/infra/cloudflare.yml)
// and tunnel.NewClient always points at the live Cloudflare API base URL —
// neither has a seam for injecting a test config or a test server today, so
// the network-touching paths in this file (loadTunnel, fetch, renderLive, and
// the RunE closures built on top of them) are not reachable from a unit test
// without refactoring them first. That refactor is out of scope here;
// writeFileAtomic is pure and needs no such seam.

// assertNoTempFiles fails t if dir contains anything matching the
// "*.tmp-*" glob writeFileAtomic uses for its scratch file, confirming
// cleanup happened regardless of which return path was taken.
func assertNoTempFiles(t *testing.T, dir string) {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("ReadDir(%s): %v", dir, err)
	}
	for _, e := range entries {
		if matched, _ := filepath.Match("*.tmp-*", e.Name()); matched {
			t.Errorf("temp file left behind: %s", e.Name())
		}
	}
}

func TestWriteFileAtomic_NewFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ingress.yml")

	if err := writeFileAtomic(path, []byte("hello"), 0o644); err != nil {
		t.Fatalf("writeFileAtomic: %v", err)
	}

	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if string(got) != "hello" {
		t.Errorf("content = %q, want %q", got, "hello")
	}
	assertNoTempFiles(t, dir)
}

func TestWriteFileAtomic_OverwritesExistingContent(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ingress.yml")
	if err := os.WriteFile(path, []byte("old content, longer than the new content"), 0o644); err != nil {
		t.Fatalf("seed WriteFile: %v", err)
	}

	if err := writeFileAtomic(path, []byte("new"), 0o644); err != nil {
		t.Fatalf("writeFileAtomic: %v", err)
	}

	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if string(got) != "new" {
		t.Errorf("content = %q, want %q (overwrite, not append)", got, "new")
	}
	assertNoTempFiles(t, dir)
}

func TestWriteFileAtomic_LeavesNoTempFileBehindOnSuccess(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ingress.yml")

	if err := writeFileAtomic(path, []byte("hello"), 0o644); err != nil {
		t.Fatalf("writeFileAtomic: %v", err)
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("ReadDir: %v", err)
	}
	if len(entries) != 1 {
		t.Fatalf("dir has %d entries after a successful write, want exactly 1 (the target file): %v", len(entries), entries)
	}
	if entries[0].Name() != "ingress.yml" {
		t.Errorf("unexpected entry left behind: %s", entries[0].Name())
	}
}

func TestWriteFileAtomic_UnwritableDirReturnsErrorAndLeavesNoTempFile(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("running as root: directory permissions do not block writes")
	}

	dir := t.TempDir()
	roDir := filepath.Join(dir, "readonly")
	if err := os.Mkdir(roDir, 0o500); err != nil { // r-x------: no write permission
		t.Fatalf("Mkdir: %v", err)
	}
	// Restore write permission so t.TempDir's own cleanup can remove roDir.
	t.Cleanup(func() { os.Chmod(roDir, 0o700) })

	path := filepath.Join(roDir, "ingress.yml")
	err := writeFileAtomic(path, []byte("hello"), 0o644)
	if err == nil {
		t.Fatal("expected an error writing into a directory without write permission")
	}
	assertNoTempFiles(t, roDir)
}
