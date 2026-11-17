package cmd

import (
	"archive/tar"
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/psychonaut0/infra/cli/internal/ssh"
)

func TestSyncHAStackPreservesManagedAndPrivateConfiguration(t *testing.T) {
	root := t.TempDir()
	files := map[string]string{
		"docker-compose.yml":              "services: {}\n",
		"mosquitto/config/mosquitto.conf": "listener 1883\n",
		"config/configuration.yaml":       "default_config:\n",
		"config/secrets.yaml":             "private: do-not-deploy\n",
		"config/.storage/auth":            "private runtime",
		".env":                            "PRIVATE=secret\n",
		".env.local":                      "PRIVATE=other\n",
	}
	for name, contents := range files {
		path := filepath.Join(root, "stacks", "ct-ha", name)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	// Capture the actual archive handed to SSH, without touching a real host.
	bin := t.TempDir()
	capture := filepath.Join(t.TempDir(), "stack.tar")
	if err := os.WriteFile(filepath.Join(bin, "ssh"), []byte("#!/bin/sh\ncat > \"$CAPTURE_TAR\"\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("CAPTURE_TAR", capture)
	if err := syncStack(context.Background(), ssh.New(), "unused", "ct-ha", root); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(capture)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	reader := tar.NewReader(f)
	found := map[string]bool{}
	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if header.Typeflag == tar.TypeReg {
			found[header.Name] = true
		}
	}
	for _, name := range []string{"ct-ha/docker-compose.yml", "ct-ha/mosquitto/config/mosquitto.conf"} {
		if !found[name] {
			t.Errorf("missing stack file %s", name)
		}
	}
	if len(found) != 2 {
		t.Errorf("generic deployment included HA configuration or secrets: %v", found)
	}
}
