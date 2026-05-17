package cmd

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestRunFromMirror_DownloadsAndInstalls(t *testing.T) {
	binBytes := []byte("#!/bin/sh\necho fake infra v0.5.0\n")
	sum := sha256.Sum256(binBytes)
	binSHA := hex.EncodeToString(sum[:])

	mux := http.NewServeMux()
	srv := httptest.NewServer(mux)
	defer srv.Close()

	binPath := fmt.Sprintf("/linux/%s/infra", runtime.GOARCH)
	mux.HandleFunc(binPath, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(binBytes)
	})
	mux.HandleFunc("/manifest.json", func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, `{
			"version": "v0.5.0",
			"commit": "deadbeef",
			"published_at": "2026-04-29T00:00:00Z",
			"binaries": {
				"linux/%s": {"url": %q, "sha256": %q}
			}
		}`, runtime.GOARCH, srv.URL+binPath, binSHA)
	})

	tmp := t.TempDir()
	dest := filepath.Join(tmp, "infra")
	if err := os.WriteFile(dest, []byte("old"), 0755); err != nil {
		t.Fatal(err)
	}

	var out bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	err := runFromMirror(ctx, runFromMirrorOpts{
		MirrorURL:   srv.URL + "/manifest.json",
		CurrentVer:  "v0.4.0",
		InstallPath: dest,
		Yes:         true,
		Check:       false,
		Out:         &out,
	})
	if err != nil {
		t.Fatalf("runFromMirror: %v\nout=%s", err, out.String())
	}

	got, err := os.ReadFile(dest)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, binBytes) {
		t.Errorf("dest content mismatch")
	}
}

func TestRunFromMirror_ChecksumMismatch(t *testing.T) {
	binBytes := []byte("real bytes")
	mux := http.NewServeMux()
	srv := httptest.NewServer(mux)
	defer srv.Close()
	binPath := fmt.Sprintf("/linux/%s/infra", runtime.GOARCH)
	mux.HandleFunc(binPath, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(binBytes)
	})
	mux.HandleFunc("/manifest.json", func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, `{
			"version": "v0.5.0",
			"commit": "x",
			"published_at": "2026-04-29T00:00:00Z",
			"binaries": {
				"linux/%s": {"url": %q, "sha256": "0000000000000000000000000000000000000000000000000000000000000000"}
			}
		}`, runtime.GOARCH, srv.URL+binPath)
	})

	tmp := t.TempDir()
	dest := filepath.Join(tmp, "infra")
	_ = os.WriteFile(dest, []byte("old"), 0755)

	var out bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	err := runFromMirror(ctx, runFromMirrorOpts{
		MirrorURL:   srv.URL + "/manifest.json",
		CurrentVer:  "v0.4.0",
		InstallPath: dest,
		Yes:         true,
		Out:         &out,
	})
	if err == nil {
		t.Fatal("expected sha mismatch error")
	}
	got, _ := os.ReadFile(dest)
	if string(got) != "old" {
		t.Errorf("dest should be unchanged on sha mismatch, got %q", got)
	}
}

func TestRunFromMirror_AlreadyOnLatest(t *testing.T) {
	mux := http.NewServeMux()
	srv := httptest.NewServer(mux)
	defer srv.Close()
	mux.HandleFunc("/manifest.json", func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, `{
			"version": "v0.4.0",
			"commit": "x",
			"published_at": "2026-04-29T00:00:00Z",
			"binaries": {"linux/%s": {"url": "u", "sha256": "s"}}
		}`, runtime.GOARCH)
	})

	tmp := t.TempDir()
	dest := filepath.Join(tmp, "infra")
	_ = os.WriteFile(dest, []byte("old"), 0755)

	var out bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := runFromMirror(ctx, runFromMirrorOpts{
		MirrorURL:   srv.URL + "/manifest.json",
		CurrentVer:  "v0.4.0",
		InstallPath: dest,
		Yes:         true,
		Out:         &out,
	})
	if err != nil {
		t.Fatalf("runFromMirror: %v", err)
	}
	if got, _ := os.ReadFile(dest); string(got) != "old" {
		t.Errorf("dest should be unchanged when already on latest")
	}
	if !bytes.Contains(out.Bytes(), []byte("Already on latest")) {
		t.Errorf("expected 'Already on latest' in output, got %q", out.String())
	}
}

func TestRunFromMirror_CheckMode(t *testing.T) {
	binBytes := []byte("never downloaded")
	mux := http.NewServeMux()
	srv := httptest.NewServer(mux)
	defer srv.Close()
	binPath := fmt.Sprintf("/linux/%s/infra", runtime.GOARCH)
	mux.HandleFunc(binPath, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(binBytes)
		t.Errorf("binary should not be downloaded in --check mode")
	})
	mux.HandleFunc("/manifest.json", func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, `{
			"version": "v0.5.0",
			"commit": "x",
			"published_at": "2026-04-29T00:00:00Z",
			"binaries": {"linux/%s": {"url": %q, "sha256": "deadbeef"}}
		}`, runtime.GOARCH, srv.URL+binPath)
	})

	tmp := t.TempDir()
	dest := filepath.Join(tmp, "infra")
	_ = os.WriteFile(dest, []byte("old"), 0755)

	var out bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := runFromMirror(ctx, runFromMirrorOpts{
		MirrorURL:   srv.URL + "/manifest.json",
		CurrentVer:  "v0.4.0",
		InstallPath: dest,
		Check:       true,
		Yes:         false, // intentionally no confirmation; --check should bypass prompt
		Out:         &out,
	})
	if err != nil {
		t.Fatalf("runFromMirror: %v", err)
	}
	if got, _ := os.ReadFile(dest); string(got) != "old" {
		t.Errorf("dest should be unchanged in --check mode")
	}
}
